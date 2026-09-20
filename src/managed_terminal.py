from __future__ import annotations

import os
import shlex
import shutil
import signal
import tempfile
from typing import List, Optional

from PySide6.QtCore import QTimer, Signal, Slot

from qt_terminal import QtTerminalWidget


class ManagedTerminalWidget(QtTerminalWidget):
    """Extend QtTerminalWidget with process lifecycle management for WrapPac."""

    started = Signal()
    finished = Signal(int)
    sequence_finished = Signal(list)

    def __init__(self, parent: Optional[object] = None):
        # CRITICAL FIX: Don't pass shell=None, use start_pty=True without autostart
        super().__init__(shell=None, parent=parent, start_pty=True)
        self._process_exit_code: Optional[int] = None
        self._seq_rc_dir: Optional[str] = None
        self._seq_count = 0
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._check_process_status)
        self._monitor_timer.setInterval(100)

    def run(self, argv: list[str], env: Optional[dict] = None) -> None:
        """Start a process (API-compatible with the old ExpectRunner.run)."""
        if not argv:
            return

        # Convert list to proper command format
        if len(argv) == 1 and isinstance(argv[0], str):
            command = argv[0]
        else:
            command = argv

        # Ensure proper terminal environment
        merged_env = os.environ.copy()
        merged_env.update({
            'TERM': 'xterm-256color',
            'COLORTERM': 'truecolor',
            'LANG': merged_env.get('LANG', 'C.UTF-8'),
            'LC_ALL': merged_env.get('LC_ALL', 'C.UTF-8'),
        })

        if env:
            merged_env.update(env)

        # A single command must never be reported through sequence_finished:
        # clear any leftover sequence state (e.g. a sequence that was
        # interrupted before its process was reaped) so this run emits the
        # regular finished signal.
        if self._seq_rc_dir is not None:
            shutil.rmtree(self._seq_rc_dir, ignore_errors=True)
            self._seq_rc_dir = None
        self._seq_count = 0

        # Use the base class start_process method
        self.start_process(command, env=merged_env)
        self._process_exit_code = None
        self._monitor_timer.start()
        self.started.emit()

    def run_sequence(self, argv_list: List[List[str]]) -> None:
        """Run several commands sequentially in ONE PTY (one bash).

        Every step runs on the same tty, so sudo's per-tty credential
        ticket (default `Defaults tty_tickets`) is shared by all sudo
        invocations of the run - including the one AUR helpers (yay/paru)
        spawn internally for `pacman -U`. One password prompt covers the
        whole sequence. Per-step exit codes are written to private rc
        files and emitted via `sequence_finished` (missing file = -1,
        i.e. the step was interrupted before it could finish).
        """
        steps = [list(argv) for argv in argv_list if argv]
        if not steps:
            return

        rc_dir = tempfile.mkdtemp(prefix="wrappac_seq_")
        os.chmod(rc_dir, 0o700)

        parts: List[str] = []
        for i, argv in enumerate(steps):
            rc_file = os.path.join(rc_dir, f"rc_{i:03d}")
            parts.append(
                f"{shlex.join(argv)}; printf '%s\\n' \"$?\" > {shlex.quote(rc_file)}"
            )
        script = "\n".join(parts)

        self._seq_rc_dir = rc_dir
        self._seq_count = len(steps)

        # Ensure proper terminal environment (same as run())
        merged_env = os.environ.copy()
        merged_env.update({
            'TERM': 'xterm-256color',
            'COLORTERM': 'truecolor',
            'LANG': merged_env.get('LANG', 'C.UTF-8'),
            'LC_ALL': merged_env.get('LC_ALL', 'C.UTF-8'),
        })

        self.start_process(["bash", "-c", script], env=merged_env)
        self._process_exit_code = None
        self._monitor_timer.start()
        self.started.emit()

    def _collect_sequence_codes(self) -> List[int]:
        """Read per-step exit codes, then remove the private rc dir."""
        codes: List[int] = []
        rc_dir = self._seq_rc_dir
        for i in range(self._seq_count):
            path = os.path.join(rc_dir, f"rc_{i:03d}")
            try:
                with open(path, "r", encoding="ascii") as fh:
                    codes.append(int(fh.read().strip()))
            except Exception:
                codes.append(-1)  # step never completed
        shutil.rmtree(rc_dir, ignore_errors=True)
        return codes

    @Slot()
    def _check_process_status(self) -> None:
        """Monitor child process for completion."""
        if self.child_pid is None:
            return

        try:
            pid, status = os.waitpid(self.child_pid, os.WNOHANG)
            if pid != 0:
                # Process has exited
                self._monitor_timer.stop()
                if os.WIFEXITED(status):
                    exit_code = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    exit_code = -os.WTERMSIG(status)
                else:
                    exit_code = -1

                self._process_exit_code = exit_code
                self.child_pid = None

                if self._seq_rc_dir is not None:
                    codes = self._collect_sequence_codes()
                    self._seq_rc_dir = None
                    self._seq_count = 0
                    self.sequence_finished.emit(codes)
                else:
                    self.finished.emit(exit_code)
        except ChildProcessError:
            # Process doesn't exist anymore
            pass
        except OSError:
            pass

    def _signal_group(self, sig: int) -> None:
        """Signal the child's whole process group.

        The child of pty.fork() is a session/process-group leader, so
        killpg reaches the command actually running (e.g. pacman under
        sudo), not only the top-level process.
        """
        if self.child_pid is None:
            return
        try:
            os.killpg(self.child_pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(self.child_pid, sig)
            except OSError:
                pass

    def send_sigint(self) -> None:
        """Send SIGINT (Ctrl+C) to the child process group."""
        self._signal_group(signal.SIGINT)

    def terminate(self) -> None:
        """Terminate the child process group (SIGTERM then SIGKILL)."""
        self._signal_group(signal.SIGTERM)
        import time
        time.sleep(0.5)
        if self.child_pid is not None:
            self._signal_group(signal.SIGKILL)

    def is_running(self) -> bool:
        """Check if a child process is currently running."""
        return self.child_pid is not None

    def write_bytes(self, data: bytes) -> None:
        """Write raw bytes to the PTY (compatibility method)."""
        self.write_pty(data)
