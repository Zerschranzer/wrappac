import os
import re
import signal
import time
from typing import Iterable, Optional, Callable

import pexpect
from PySide6.QtCore import QObject, Signal, QSocketNotifier


class ExpectRunner(QObject):
    """Process runner based on pexpect with password prompt detection."""

    PASSWORD_MARKER = "[WRAPPAC_PASSWORD_PROMPT]"

    started = Signal()
    finished = Signal(int)
    password_requested = Signal(str)

    def __init__(self, append_fn: Callable[[str], None]):
        super().__init__()
        self._append = append_fn
        self._proc: Optional[pexpect.spawn] = None
        self._notifier: Optional[QSocketNotifier] = None
        self._buffer: str = ""
        self._waiting_for_password: bool = False
        self._password_context: str = "root"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def run(self, argv: Iterable[str], env: Optional[dict] = None):
        argv = list(argv)
        if not argv:
            return

        if self.is_running():
            return

        view_cmd = " ".join(self._quote(a) for a in argv)
        self._append(f"$ {view_cmd}\n\n")

        env = (env or os.environ.copy()).copy()
        env.setdefault("NO_COLOR", "1")
        env.setdefault("CLICOLOR", "0")
        # Use a minimally capable terminal so helpers like paru do not warn that
        # the terminal is "not fully functional", while still disabling color
        # escape sequences via the variables above.
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("PACMAN_COLOR", "never")

        argv = self._prepare_argv(argv)

        try:
            self._proc = pexpect.spawn(
                argv[0],
                argv[1:],
                env=env,
                encoding="utf-8",
                echo=False,
                timeout=None,
            )
        except Exception:
            self._append("[error] could not start process\n")
            self._cleanup(127)
            return

        self._proc.delaybeforesend = None

        self._notifier = QSocketNotifier(self._proc.child_fd, QSocketNotifier.Read)
        self._notifier.activated.connect(self._on_read_ready)

        self.started.emit()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    def is_waiting_for_password(self) -> bool:
        return self._waiting_for_password

    def write_bytes(self, data: bytes):
        if not self._proc or not self._proc.isalive():
            return
        if not data:
            return
        try:
            text = data.decode("utf-8", errors="ignore")
            if text:
                self._proc.send(text)
        except Exception:
            pass

    def send_password(self, password: str):
        if not self._proc or not self._proc.isalive():
            return
        try:
            self._proc.sendline(password)
        except Exception:
            pass
        finally:
            self._waiting_for_password = False

    def send_sigint(self):
        if not self._proc or not self._proc.isalive():
            return
        try:
            self._proc.sendintr()
        except Exception:
            try:
                os.kill(self._proc.pid, signal.SIGINT)
            except Exception:
                pass

    def terminate(self):
        if not self._proc or not self._proc.isalive():
            return
        try:
            self._proc.terminate(force=False)
        except Exception:
            pass

        deadline = time.monotonic() + 3.0
        while self._proc and self._proc.isalive() and time.monotonic() < deadline:
            time.sleep(0.1)

        if not self._proc or not self._proc.isalive():
            return

        try:
            self._proc.terminate(force=True)
        except Exception:
            try:
                self._proc.kill(signal.SIGKILL)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _on_read_ready(self):
        if not self._proc:
            return

        try:
            while True:
                chunk = self._proc.read_nonblocking(4096, timeout=0)
                if not chunk:
                    break
                self._process_text(chunk)
        except pexpect.exceptions.TIMEOUT:
            pass
        except pexpect.exceptions.EOF:
            self._finalize_process()
            return
        except OSError:
            self._finalize_process()
            return

        if self._proc and not self._proc.isalive():
            self._finalize_process()

    def _process_text(self, text: str):
        if not text:
            return

        text = self._strip_ansi(text)
        if not text:
            return

        self._buffer += text

        marker = self.PASSWORD_MARKER
        while True:
            idx = self._buffer.find(marker)
            if idx == -1:
                break

            before = self._buffer[:idx]
            if before:
                self._append(before)

            self._buffer = self._buffer[idx + len(marker):]
            self._emit_password_request()

        if not self._buffer:
            return

        keep = self._marker_prefix_length(self._buffer, marker)
        if keep and keep < len(self._buffer):
            output = self._buffer[:-keep]
            if output:
                self._append(output)
            self._buffer = self._buffer[-keep:]
        elif not keep:
            self._append(self._buffer)
            self._buffer = ""

    def _finalize_process(self):
        if not self._proc:
            return

        if self._buffer:
            self._append(self._buffer)
            self._buffer = ""

        exit_code = self._proc.exitstatus
        if exit_code is None:
            if self._proc.signalstatus is not None:
                exit_code = -self._proc.signalstatus
            else:
                exit_code = 0

        self._cleanup(exit_code)

    def _cleanup(self, code: int):
        if self._notifier:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None

        proc = self._proc
        self._proc = None
        self._waiting_for_password = False
        if proc is not None:
            try:
                if proc.isalive():
                    proc.close(force=True)
                else:
                    proc.close()
            except Exception:
                pass

        self._append(f"\n[exit {code}]\n")
        self.finished.emit(code)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Return *text* with ANSI escape sequences removed."""

        if "\x1b" not in text:
            return text

        # Match CSI, OSC, and other ANSI escape sequences.
        csi = r"\x1B[@-_][0-?]*[ -/]*[@-~]"
        osc = r"\x1B\][^\x07\x1B]*(\x07|\x1B\\)"
        pattern = f"({csi}|{osc})"
        return re.sub(pattern, "", text)

    def _prepare_argv(self, argv: list[str]) -> list[str]:
        if not argv:
            return argv

        cmd = os.path.basename(argv[0])
        if cmd == "sudo":
            self._password_context = "sudo"
            return self._prepare_sudo(argv)

        if cmd == "doas":
            self._password_context = "doas"
            return self._prepare_doas(argv)

        self._password_context = cmd
        return argv

    def _prepare_sudo(self, argv: list[str]) -> list[str]:
        args = [argv[0]]
        has_stdin = False
        options: list[str] = []

        short_with_arg = {"-A", "-C", "-g", "-p", "-r", "-t", "-u"}
        long_with_arg = {
            "--askpass",
            "--chdir",
            "--cd",
            "--close-from",
            "--prompt",
            "--group",
            "--host",
            "--type",
            "--role",
            "--chroot",
            "--user",
        }

        idx = 1
        while idx < len(argv):
            item = argv[idx]

            if item == "--":
                options.append(item)
                idx += 1
                break

            if item.startswith("--"):
                if item.startswith("--prompt="):
                    idx += 1
                    continue
                if item == "--prompt":
                    idx += 2 if idx + 1 < len(argv) else 1
                    continue
                if item == "--stdin":
                    has_stdin = True
                    idx += 1
                    continue
                options.append(item)
                if item in long_with_arg and "=" not in item:
                    idx += 1
                    if idx < len(argv):
                        options.append(argv[idx])
                        idx += 1
                    continue
                idx += 1
                continue

            if item.startswith("-") and item != "-":
                if item == "-S":
                    has_stdin = True
                    idx += 1
                    continue
                if item == "-p":
                    idx += 2 if idx + 1 < len(argv) else 1
                    continue
                options.append(item)
                if item in short_with_arg:
                    if idx + 1 < len(argv):
                        options.append(argv[idx + 1])
                        idx += 2
                    else:
                        idx += 1
                    continue
                idx += 1
                continue

            break

        command = argv[idx:]

        insert_at = options.index("--") if "--" in options else len(options)
        extra: list[str] = []
        if not has_stdin:
            extra.append("-S")
        extra.extend(["-p", self.PASSWORD_MARKER])
        options[insert_at:insert_at] = extra

        args.extend(options)
        args.extend(command)
        return args

    def _prepare_doas(self, argv: list[str]) -> list[str]:
        args = [argv[0]]
        remaining: list[str] = []
        skip_next = False
        for item in argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if item == "-p":
                skip_next = True
                continue
            remaining.append(item)

        args.extend(["-p", self.PASSWORD_MARKER])
        args.extend(remaining)
        return args

    def _emit_password_request(self):
        if self._waiting_for_password:
            return
        self._waiting_for_password = True
        self.password_requested.emit(self._password_context)

    @staticmethod
    def _marker_prefix_length(buffer: str, marker: str) -> int:
        max_len = min(len(buffer), len(marker) - 1)
        for length in range(max_len, 0, -1):
            if marker.startswith(buffer[-length:]):
                return length
        return 0

    @staticmethod
    def _quote(value: str) -> str:
        if any(ch.isspace() for ch in value):
            return "'" + value.replace("'", "'\\''") + "'"
        return value

