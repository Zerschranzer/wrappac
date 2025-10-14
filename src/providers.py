import os
import re
import shutil
import subprocess
import shlex
from typing import Iterable, List, Optional

from models import PackageItem
from settings import settings

_run_errors: list[dict[str, str]] = []


def _format_cmd(cmd: list[str]) -> str:
    try:
        return shlex.join(cmd)
    except AttributeError:
        return " ".join(cmd)


def _record_error(cmd: list[str], message: str, stderr: str = "") -> None:
    _run_errors.append({
        "command": _format_cmd(cmd),
        "message": message,
        "stderr": stderr.strip(),
    })


def consume_errors() -> list[dict[str, str]]:
    global _run_errors
    errors = list(_run_errors)
    _run_errors = []
    return errors


def _run_with_code(cmd: list[str], ignore_exit_codes: Iterable[int] = ()) -> tuple[str, int]:
    """Run a command and return stdout together with the exit code."""
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        _record_error(cmd, "not-found")
        return "", -1
    except Exception as exc:
        _record_error(cmd, f"exception: {exc}")
        return "", -1

    if proc.returncode != 0 and proc.returncode not in ignore_exit_codes:
        details = proc.stderr.strip() or proc.stdout.strip()
        _record_error(cmd, f"exit-code {proc.returncode}", details)
    return proc.stdout, proc.returncode


def _run(cmd: list[str]) -> str:
    """Run a command and return stdout while recording errors."""
    out, _ = _run_with_code(cmd)
    return out


def _which_or_hint(cmd: str) -> bool:
    """Return True if an executable command is available."""
    return shutil.which(cmd) is not None
def _parse_pacman_query_output(out: str) -> list[tuple[str, str, Optional[str]]]:
    """Parses formatted pacman -Q output lines into name/version/repo tuples."""

    entries: list[tuple[str, str, Optional[str]]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            parts = line.split()  # Fallback if the tab separator is missing

        if len(parts) >= 3:
            name, version, repo = parts[:3]
        elif len(parts) == 2:
            name, version = parts
            repo = None
        else:
            continue

        name = name.strip()
        version = version.strip()
        cleaned_repo = repo.strip() if isinstance(repo, str) else repo

        entries.append((name, version, cleaned_repo))

    return entries


def _pacman_query(args: list[str], include_repo: bool = False) -> list[tuple[str, str, Optional[str]]]:
    """Runs pacman -Q queries with optional --format support."""

    if not _which_or_hint("pacman"):
        return []

    if include_repo:
        formatted_cmd = ["pacman", *args, "--format", "%n\t%v\t%r"]
        out, code = _run_with_code(formatted_cmd, ignore_exit_codes=(1,))
        if code == 0:
            return _parse_pacman_query_output(out)

    out = _run(["pacman", *args])
    return _parse_pacman_query_output(out)


def list_pacman_native() -> List[PackageItem]:
    """Packages from the official repositories via pacman -Qn --format."""

    items: List[PackageItem] = []
    for name, version, repo in _pacman_query(["-Qn"], include_repo=True):
        items.append(
            PackageItem(
                pid=name,
                name=name,
                version=version,
                source="Repo",
                origin=(repo or "unknown"),
            )
        )
    return items


def list_pacman_foreign() -> List[PackageItem]:
    """Foreign or AUR packages via pacman -Qm --format (removable with pacman -Rns)."""

    items: List[PackageItem] = []
    for name, version, repo in _pacman_query(["-Qm"], include_repo=True):
        origin = repo or "local"
        items.append(
            PackageItem(
                pid=name,
                name=name,
                version=version,
                source="AUR",
                origin=origin,
            )
        )
    return items


def list_flatpak() -> List[PackageItem]:
    """Flatpak apps via flatpak list --app --columns=application,name,branch,origin."""
    if not _which_or_hint("flatpak"):
        return []
    out = _run(["flatpak", "list", "--app", "--columns=application,name,branch,origin"])
    items: List[PackageItem] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            parts = line.split()  # Fallback if the tab separator is missing
        if len(parts) >= 4:
            appid, dispname, branch, origin = [p.strip() for p in parts[:4]]
            items.append(PackageItem(pid=appid, name=dispname or appid, version=branch, source="Flatpak", origin=origin))
    return items


def list_all() -> List[PackageItem]:
    """Return the combined list of Repo, AUR, and Flatpak packages."""
    return list_pacman_native() + list_pacman_foreign() + list_flatpak()


def updates_pacman_count() -> int:
    """Return the number of updates available in the official repositories."""
    out, code = _run_with_code(["checkupdates"], ignore_exit_codes=(2,))
    if code in (0, 2):
        return sum(1 for ln in out.splitlines() if ln.strip())
    return 0


def updates_aur_count() -> int:
    """Return the number of available AUR updates (yay -Qua)."""
    tool = settings.get_aur_helper()
    if not tool:
        return 0

    out, code = _run_with_code([tool, "-Qua"], ignore_exit_codes=(1,))
    # Exit code 0 = updates available
    # Exit code 1 = no updates (normal for yay!)
    if code in (0, 1):
        return sum(1 for ln in out.splitlines() if ln.strip())

    return 0


def updates_flatpak_count() -> int:
    """Return the number of Flatpak updates (apps and runtimes).

    Both user and system installations are queried explicitly to avoid relying
    on Flatpak's auto-detection heuristics, which can miss newly added remotes
    in mixed setups. Modern Flatpak releases refresh remotes automatically
    whenever --updates is supplied, so passing --refresh is no longer required
    (and would fail on newer versions).
    """
    if not _which_or_hint("flatpak"):
        return 0

    def _count_scope(scope: str) -> int:
        cmd = [
            "flatpak",
            "remote-ls",
            "--updates",
            scope,
            "--columns=ref",
        ]
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            _record_error(cmd, "timeout")
            return 0
        except Exception as exc:
            _record_error(cmd, f"exception: {exc}")
            return 0

        if proc.returncode != 0:
            _record_error(cmd, f"exit-code {proc.returncode}", proc.stderr)
            return 0

        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if lines and lines[0].lower() == "ref":
            lines = lines[1:]

        return len(lines)

    user_count = _count_scope("--user")
    system_count = _count_scope("--system")
    return user_count + system_count


def is_reflector_available() -> bool:
    return _which_or_hint("reflector")


def build_reflector_command(args: Optional[str] = None) -> Optional[dict[str, object]]:
    if not is_reflector_available():
        _record_error(["reflector"], "not-found")
        return None

    arg_str = args if args is not None else settings.get("reflector_args", "").strip()

    try:
        extra = shlex.split(arg_str) if arg_str else []
    except ValueError as exc:
        _record_error(["reflector"], f"args-error: {exc}")
        return None

    save_path = settings.get("reflector_save_path", "").strip()
    if save_path and "--save" not in extra:
        extra.extend(["--save", save_path])

    # Reflector attempts to use the "rsync" tool for mirrors using the rsync
    # protocol. If rsync is missing and no protocol preference is specified,
    # limit the selection to HTTPS to avoid noisy warnings.
    protocols_specified = False
    for arg in extra:
        if arg in {"--protocol", "-p"} or arg.startswith("--protocol="):
            protocols_specified = True
            break

    if not protocols_specified and shutil.which("rsync") is None:
        extra.extend(["--protocol", "https"])

    cmd = ["reflector", *extra]

    return {"argv": cmd, "needs_root": True}
