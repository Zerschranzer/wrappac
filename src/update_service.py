"""Helpers for managing the optional systemd based update service."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Sequence


SERVICE_NAME = "wrappac-update.service"
TIMER_NAME = "wrappac-update.timer"

APP_DIR = Path(__file__).resolve().parent
MAIN_SCRIPT = APP_DIR / "main.py"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class UpdateServiceError(RuntimeError):
    """Raised when the update service could not be configured."""


def _systemctl_available() -> bool:
    return shutil.which("systemctl") is not None


def _run_systemctl(args: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    if not _systemctl_available():
        return None

    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise UpdateServiceError(str(exc)) from exc


def _quote_cmd(cmd: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _wrap_command(extra_args: Sequence[str]) -> list[str]:
    exe = shutil.which("wrappac")
    if exe:
        return [exe, *extra_args]

    python = sys.executable or shutil.which("python3") or "python3"
    return [python, str(MAIN_SCRIPT), *extra_args]


def _sanitize_time(value: str, default: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except (ValueError, AttributeError):
        pass

    try:
        default_hour, default_minute = default.split(":", 1)
        return int(default_hour), int(default_minute)
    except (ValueError, AttributeError):  # pragma: no cover - defensive
        return 9, 0


def _write_unit_files(
    mode: str,
    manual_hours: int,
    daily_time: str,
    weekly_day: str,
    weekly_time: str,
    *,
    check_on_boot: bool,
) -> None:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    cmd = _wrap_command(["--run-update-service"])
    exec_line = _quote_cmd(cmd)

    service_content = textwrap.dedent(
        f"""
        [Unit]
        Description=WrapPac automatic update check

        [Service]
        Type=simple
        ExecStart={exec_line}
        KillMode=process
        Restart=no
        """
    ).strip() + "\n"

    if manual_hours < 1:
        manual_hours = 1

    timer_lines: list[str] = []

    if check_on_boot:
        timer_lines.append("OnBootSec=5min")

    if mode == "daily":
        hour, minute = _sanitize_time(daily_time, "09:00")
        timer_lines.append(f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00")
    elif mode == "weekly":
        hour, minute = _sanitize_time(weekly_time, "09:00")
        day = weekly_day if weekly_day in WEEKDAYS else "Mon"
        timer_lines.append(f"OnCalendar={day} *-*-* {hour:02d}:{minute:02d}:00")
    else:
        timer_lines.append(f"OnUnitActiveSec={manual_hours}h")
        timer_lines.append("AccuracySec=1min")

    timer_block = "".join(f"{line}\n" for line in timer_lines)

    timer_content = (
        "[Unit]\n"
        "Description=Schedule WrapPac update checks\n\n"
        "[Timer]\n"
        f"{timer_block}"
        "Persistent=true\n"
        f"Unit={SERVICE_NAME}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    (SYSTEMD_USER_DIR / SERVICE_NAME).write_text(service_content, encoding="utf-8")
    (SYSTEMD_USER_DIR / TIMER_NAME).write_text(timer_content, encoding="utf-8")


def apply_settings(
    enabled: bool,
    mode: str,
    manual_hours: int,
    daily_time: str,
    weekly_day: str,
    weekly_time: str,
    *,
    check_on_boot: bool,
) -> tuple[bool, str | None]:
    """Create/update the unit files and toggle the timer."""

    if not _systemctl_available():
        if enabled:
            return False, "systemctl not available"
        return True, None

    try:
        _write_unit_files(
            mode,
            manual_hours,
            daily_time,
            weekly_day,
            weekly_time,
            check_on_boot=check_on_boot,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)

    try:
        proc = _run_systemctl(["daemon-reload"])
    except UpdateServiceError as exc:
        return False, str(exc)
    if proc and proc.returncode != 0:
        return False, proc.stderr or proc.stdout

    if enabled:
        try:
            proc = _run_systemctl(["enable", "--now", TIMER_NAME])
        except UpdateServiceError as exc:
            return False, str(exc)
        if proc and proc.returncode != 0:
            return False, proc.stderr or proc.stdout
    else:
        try:
            proc = _run_systemctl(["disable", "--now", TIMER_NAME])
        except UpdateServiceError as exc:
            return False, str(exc)
        if proc and proc.returncode not in (0, 1, 5):
            # 1: not enabled, 5: not loaded
            return False, proc.stderr or proc.stdout

    return True, None


def get_status() -> dict[str, bool | None]:
    """Return availability, enabled state and active state for the timer."""

    if not _systemctl_available():
        return {"available": False, "enabled": None, "active": None}

    enabled: bool | None
    active: bool | None

    try:
        proc_enabled = _run_systemctl(["is-enabled", TIMER_NAME])
    except UpdateServiceError:
        proc_enabled = None
    if not proc_enabled or proc_enabled.returncode not in (0, 1, 4):
        enabled = None
    else:
        enabled = proc_enabled.returncode == 0

    try:
        proc_active = _run_systemctl(["is-active", TIMER_NAME])
    except UpdateServiceError:
        proc_active = None
    if not proc_active or proc_active.returncode not in (0, 3):
        active = None
    else:
        active = proc_active.returncode == 0

    return {"available": True, "enabled": enabled, "active": active}


def build_launch_command(*, show_updates: bool = False, tray_mode: bool = False) -> list[str]:
    args: list[str] = []
    if show_updates:
        args.append("--show-updates")
    if tray_mode:
        args.append("--tray-mode")
    return _wrap_command(args)

