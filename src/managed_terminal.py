from __future__ import annotations

import os
import signal
from typing import Optional

from PySide6.QtCore import QTimer, Signal, Slot

from qt_terminal import QtTerminalWidget


class ManagedTerminalWidget(QtTerminalWidget):
    """Extend QtTerminalWidget with process lifecycle management for WrapPac."""

    started = Signal()
    finished = Signal(int)

    def __init__(self, parent: Optional[object] = None):
        # CRITICAL FIX: Don't pass shell=None, use start_pty=True without autostart
        super().__init__(shell=None, parent=parent, start_pty=True)
        self._process_exit_code: Optional[int] = None
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

        # Use the base class start_process method
        self.start_process(command, env=merged_env)
        self._process_exit_code = None
        self._monitor_timer.start()
        self.started.emit()

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
                self.finished.emit(exit_code)
        except ChildProcessError:
            # Process doesn't exist anymore
            pass
        except OSError:
            pass

    def send_sigint(self) -> None:
        """Send SIGINT (Ctrl+C) to the child process."""
        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGINT)
            except OSError:
                pass

    def terminate(self) -> None:
        """Terminate the child process (SIGTERM then SIGKILL)."""
        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGTERM)
                import time
                time.sleep(0.5)
                if self.child_pid is not None:
                    os.kill(self.child_pid, signal.SIGKILL)
            except OSError:
                pass

    def is_running(self) -> bool:
        """Check if a child process is currently running."""
        return self.child_pid is not None

    def write_bytes(self, data: bytes) -> None:
        """Write raw bytes to the PTY (compatibility method)."""
        self.write_pty(data)
