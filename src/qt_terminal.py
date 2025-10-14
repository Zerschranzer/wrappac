#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QtTerminalWidget – Fixed scroll behavior and output ordering
"""

from __future__ import annotations

import os
import pty
import fcntl
import termios
import struct
import signal
import sys
import re
from dataclasses import dataclass
from typing import List, Tuple, Optional, Deque, Union, Dict
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets


# --------------------------- Configuration ---------------------------------
DEFAULT_FONT_FAMILY = "Monospace"
DEFAULT_FONT_POINT_SIZE = 11
DEFAULT_SCROLLBACK = 5000
READ_CHUNK = 8192
CURSOR_BLINK_MS = 600


# --------------------------- Helper Structures --------------------------------
@dataclass
class Cell:
    ch: str = " "
    fg: Optional[QtGui.QColor] = None
    bg: Optional[QtGui.QColor] = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class ScreenBuffer:
    """Screen buffer with scrollback."""
    def __init__(self, rows: int, cols: int, scrollback: int = DEFAULT_SCROLLBACK):
        self.rows = rows
        self.cols = cols
        self.scrollback_limit = scrollback
        self.reset()

    def reset(self):
        self.cursor_row = 0
        self.cursor_col = 0
        self.origin_mode = False
        self.saved_cursor: Tuple[int, int] = (0, 0)
        self.primary: List[List[Cell]] = [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]
        self.scrollback: Deque[List[Cell]] = deque()

    def resize(self, rows: int, cols: int):
        """Resize without losing lines: top lines go to scrollback when shrinking."""
        if rows < self.rows:
            n = self.rows - rows
            for _ in range(n):
                if self.primary:
                    top = self.primary.pop(0)
                    if self.scrollback_limit > 0:
                        self.scrollback.append(top)
                        if len(self.scrollback) > self.scrollback_limit:
                            self.scrollback.popleft()
        elif rows > self.rows:
            for _ in range(rows - self.rows):
                self.primary.append([Cell() for _ in range(self.cols)])

        if cols != self.cols:
            for r in range(len(self.primary)):
                row = self.primary[r]
                if cols > self.cols:
                    row.extend([Cell() for _ in range(cols - self.cols)])
                else:
                    self.primary[r] = row[:cols]

        self.rows = rows
        self.cols = cols
        self.cursor_row = clamp(self.cursor_row, 0, self.rows - 1)
        self.cursor_col = clamp(self.cursor_col, 0, self.cols - 1)

    def put_char(self, cell: Cell):
        if cell.ch == "\n":
            self.newline()
            return
        if cell.ch == "\r":
            self.cursor_col = 0
            return
        if cell.ch == "\b":
            self.cursor_col = max(0, self.cursor_col - 1)
            return
        if cell.ch == "\t":
            spaces = 8 - (self.cursor_col % 8)
            for _ in range(spaces):
                self._write_char(Cell(" ", cell.fg, cell.bg, cell.bold, cell.italic, cell.underline, cell.inverse))
            return
        self._write_char(cell)

    def _write_char(self, cell: Cell):
        if self.cursor_col >= self.cols:
            self.newline()
        if 0 <= self.cursor_row < self.rows and 0 <= self.cursor_col < self.cols:
            self.primary[self.cursor_row][self.cursor_col] = cell
            self.cursor_col += 1

    def newline(self):
        self.cursor_col = 0
        self.cursor_row += 1
        if self.cursor_row >= self.rows:
            self.scroll_up(1)
            self.cursor_row = self.rows - 1

    def scroll_up(self, n: int):
        for _ in range(n):
            if self.scrollback_limit > 0:
                self.scrollback.append(self.primary[0])
                if len(self.scrollback) > self.scrollback_limit:
                    self.scrollback.popleft()
            self.primary.pop(0)
            self.primary.append([Cell() for _ in range(self.cols)])

    def erase_in_display(self, mode: int):
        if mode == 2:
            self.primary = [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]
            self.cursor_row = clamp(self.cursor_row, 0, self.rows - 1)
            self.cursor_col = clamp(self.cursor_col, 0, self.cols - 1)
        elif mode == 0:
            row = self.primary[self.cursor_row]
            for c in range(self.cursor_col, self.cols):
                row[c] = Cell()
            for r in range(self.cursor_row + 1, self.rows):
                self.primary[r] = [Cell() for _ in range(self.cols)]
        elif mode == 1:
            for r in range(0, self.cursor_row):
                self.primary[r] = [Cell() for _ in range(self.cols)]
            row = self.primary[self.cursor_row]
            for c in range(0, self.cursor_col + 1):
                row[c] = Cell()

    def erase_in_line(self, mode: int):
        row = self.primary[self.cursor_row]
        if mode == 2:
            for c in range(self.cols):
                row[c] = Cell()
        elif mode == 0:
            for c in range(self.cursor_col, self.cols):
                row[c] = Cell()
        elif mode == 1:
            for c in range(0, self.cursor_col + 1):
                row[c] = Cell()

    def move_cursor(self, r: int, c: int):
        self.cursor_row = clamp(r, 0, self.rows - 1)
        self.cursor_col = clamp(c, 0, self.cols - 1)

    def save_cursor(self):
        self.saved_cursor = (self.cursor_row, self.cursor_col)

    def restore_cursor(self):
        self.cursor_row, self.cursor_col = self.saved_cursor


# --------------------------- ANSI/VT Parser ---------------------------------
class AnsiParser:
    """Simple ANSI/VT100 parser."""
    CSI_RE = re.compile(r"\x1b\[([?]?[0-9;]*)([A-Za-z])")
    OSC_TITLE_RE = re.compile(r"\x1b\]0;.*?\x07")

    def __init__(self, screen: ScreenBuffer):
        self.screen = screen
        self.fg: Optional[QtGui.QColor] = None
        self.bg: Optional[QtGui.QColor] = None
        self.bold = False
        self.italic = False
        self.underline = False
        self.inverse = False
        self.alt_screen: Optional[ScreenBuffer] = None

    def _xterm_256_to_qcolor(self, idx: int) -> QtGui.QColor:
        if idx < 16:
            base = [
                (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
                (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
                (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
                (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)
            ]
            r, g, b = base[idx]
            return QtGui.QColor(r, g, b)
        elif 16 <= idx <= 231:
            idx -= 16
            r = (idx // 36) % 6
            g = (idx // 6) % 6
            b = idx % 6
            to = [0, 95, 135, 175, 215, 255]
            return QtGui.QColor(to[r], to[g], to[b])
        else:
            shade = 8 + (idx - 232) * 10
            shade = clamp(shade, 0, 255)
            return QtGui.QColor(shade, shade, shade)

    def _apply_sgr(self, params: List[int]):
        if not params:
            params = [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self.fg = self.bg = None
                self.bold = self.italic = self.underline = self.inverse = False
            elif p == 1:
                self.bold = True
            elif p == 3:
                self.italic = True
            elif p == 4:
                self.underline = True
            elif p == 7:
                self.inverse = True
            elif p == 22:
                self.bold = False
            elif p == 23:
                self.italic = False
            elif p == 24:
                self.underline = False
            elif p == 27:
                self.inverse = False
            elif 30 <= p <= 37:
                self.fg = self._xterm_256_to_qcolor(p - 30 + 0)
            elif p == 39:
                self.fg = None
            elif 40 <= p <= 47:
                self.bg = self._xterm_256_to_qcolor(p - 40 + 0)
            elif p == 49:
                self.bg = None
            elif 90 <= p <= 97:
                self.fg = self._xterm_256_to_qcolor(p - 90 + 8)
            elif 100 <= p <= 107:
                self.bg = self._xterm_256_to_qcolor(p - 100 + 8)
            elif p in (38, 48):
                is_fg = (p == 38)
                if i + 1 < len(params):
                    mode = params[i + 1]
                    if mode == 5 and i + 2 < len(params):
                        color = self._xterm_256_to_qcolor(params[i + 2])
                        if is_fg:
                            self.fg = color
                        else:
                            self.bg = color
                        i += 2
                    elif mode == 2 and i + 4 < len(params):
                        r, g, b = params[i + 2:i + 5]
                        color = QtGui.QColor(clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255))
                        if is_fg:
                            self.fg = color
                        else:
                            self.bg = color
                        i += 4
            i += 1

    def _current_cell_style(self) -> Tuple[Optional[QtGui.QColor], Optional[QtGui.QColor], bool, bool, bool, bool]:
        return (self.fg, self.bg, self.bold, self.italic, self.underline, self.inverse)

    def feed(self, data: bytes):
        txt = data.decode('utf-8', errors='replace')

        # Remove OSC (Operating System Command) sequences
        txt = self.OSC_TITLE_RE.sub('', txt)

        # Remove other problematic sequences:
        # - G0/G1 character set designation: ESC ( B, ESC ) 0, etc.
        txt = re.sub(r'\x1b[\(\)][0AB]', '', txt)

        # - Save cursor position (DECSC): ESC 7
        # - Restore cursor position (DECRC): ESC 8
        txt = re.sub(r'\x1b[78]', '', txt)

        pos = 0
        for m in self.CSI_RE.finditer(txt):
            before = txt[pos:m.start()]
            self._emit_plain(before)
            params_raw, cmd = m.groups()
            self._handle_csi(params_raw, cmd)
            pos = m.end()
        self._emit_plain(txt[pos:])

    def _emit_plain(self, s: str):
        fg, bg, bold, italic, underline, inverse = self._current_cell_style()
        for ch in s:
            self.screen.put_char(Cell(ch, fg, bg, bold, italic, underline, inverse))

    def _handle_csi(self, params_raw: str, cmd: str):
        private = params_raw.startswith('?')
        if private:
            params_raw = params_raw[1:]
        params = [int(p) if p else 0 for p in params_raw.split(';') if p is not None]

        if cmd == 'm':
            self._apply_sgr(params)
            return

        if cmd in ('H', 'f'):
            row = (params[0] - 1) if len(params) >= 1 and params[0] else 0
            col = (params[1] - 1) if len(params) >= 2 and params[1] else 0
            self.screen.move_cursor(row, col)
            return
        if cmd == 'A':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row - n, self.screen.cursor_col)
            return
        if cmd == 'B':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row + n, self.screen.cursor_col)
            return
        if cmd == 'C':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row, self.screen.cursor_col + n)
            return
        if cmd == 'D':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row, self.screen.cursor_col - n)
            return
        if cmd == 'E':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row + n, 0)
            return
        if cmd == 'F':
            n = params[0] if params else 1
            self.screen.move_cursor(self.screen.cursor_row - n, 0)
            return
        if cmd == 'G':
            col = (params[0] - 1) if params else 0
            self.screen.move_cursor(self.screen.cursor_row, col)
            return
        if cmd == 'J':
            mode = params[0] if params else 0
            self.screen.erase_in_display(mode)
            return
        if cmd == 'K':
            mode = params[0] if params else 0
            self.screen.erase_in_line(mode)
            return
        if cmd == 'S':
            n = params[0] if params else 1
            self.screen.scroll_up(n)
            return
        if cmd == 's':
            self.screen.save_cursor()
            return
        if cmd == 'u':
            self.screen.restore_cursor()
            return

        if private and cmd in ('h', 'l'):
            enable = (cmd == 'h')
            for p in params:
                if p in (47, 1049):
                    if enable:
                        if self.alt_screen is None:
                            self.alt_screen = ScreenBuffer(self.screen.rows, self.screen.cols, 0)
                            self.alt_screen.cursor_row = self.screen.cursor_row
                            self.alt_screen.cursor_col = self.screen.cursor_col
                        self.screen, self.alt_screen = self.alt_screen, self.screen
                    else:
                        if self.alt_screen is not None:
                            self.screen, self.alt_screen = self.alt_screen, self.screen
            return


# --------------------------- Terminal Widget -------------------------------
class TerminalWidget(QtWidgets.QAbstractScrollArea):
    titleChanged = QtCore.Signal(str)

    def __init__(self,
                 shell: Optional[Union[List[str], str]] = None,
                 parent=None,
                 scrollback: int = DEFAULT_SCROLLBACK,
                 start_pty: bool = True):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.setContextMenuPolicy(QtCore.Qt.DefaultContextMenu)

        self.font = QtGui.QFont(DEFAULT_FONT_FAMILY, DEFAULT_FONT_POINT_SIZE)
        self.font.setStyleHint(QtGui.QFont.Monospace)
        self.font.setFixedPitch(True)
        self.fm = QtGui.QFontMetrics(self.font)
        self.char_w = self.fm.horizontalAdvance("M")
        self.char_h = self.fm.height()
        self.ascent = self.fm.ascent()

        self.rows = max(24, self.viewport().height() // self.char_h)
        self.cols = max(80, self.viewport().width() // self.char_w)

        self.screen = ScreenBuffer(self.rows, self.cols, scrollback)
        self.parser = AnsiParser(self.screen)

        self.cursor_visible = True
        self.cursor_blink = QtCore.QTimer(self)
        self.cursor_blink.timeout.connect(self._toggle_cursor)
        self.cursor_blink.start(CURSOR_BLINK_MS)

        self.selection_active = False
        self.sel_start: Optional[Tuple[int, int]] = None
        self.sel_end: Optional[Tuple[int, int]] = None

        self.master_fd: Optional[int] = None
        self.child_pid: Optional[int] = None
        self.notifier: Optional[QtCore.QSocketNotifier] = None

        self.scrollbar = self.verticalScrollBar()
        self.scrollbar.setSingleStep(1)
        self.scrollbar.valueChanged.connect(self.viewport().update)

        self._manage_pty = start_pty
        self._autostart = shell
        self._was_at_bottom = True  # Track if user was scrolled to bottom

        if self._manage_pty and self._autostart is not None:
            self.start_process(self._autostart)

    def feed_text(self, text: str):
        """External feed for text rendering (when start_pty=False)."""
        self.parser.feed(text.encode("utf-8", errors="replace"))
        self._update_scrollbar_and_view()

    def start_process(self,
                      command: Union[str, List[str]],
                      cwd: Optional[str] = None,
                      env: Optional[Dict[str, str]] = None):
        """Start a new child process in its own PTY."""
        if not self._manage_pty:
            raise RuntimeError("This terminal doesn't manage its own PTY (start_pty=False).")

        try:
            if self.child_pid is not None:
                os.kill(self.child_pid, signal.SIGHUP)
        except Exception:
            pass
        self.child_pid = None
        if self.notifier:
            self.notifier.setEnabled(False)
            self.notifier = None
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None

        pid, master = pty.fork()
        if pid == 0:
            # Child
            try:
                if cwd:
                    os.chdir(cwd)
                if env:
                    os.environ.update(env)
                os.environ['TERM'] = 'xterm-256color'
                if isinstance(command, str):
                    sh = os.environ.get('SHELL') or '/bin/bash'
                    os.execvp(sh, [sh, "-lc", command])
                else:
                    os.execvp(command[0], command)
            except Exception as e:
                print("exec failed:", e, file=sys.stderr)
                os._exit(1)
        else:
            # Parent
            self.child_pid = pid
            self.master_fd = master
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.notifier = QtCore.QSocketNotifier(self.master_fd, QtCore.QSocketNotifier.Read, self)
            self.notifier.activated.connect(self._read_pty)
            self._set_winsize()
            self._update_scrollbar_and_view()

    def run_line(self, line: str):
        """Send a complete line (with CR) to the child process."""
        self.write_pty((line + "\r").encode("utf-8", errors="ignore"))

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        try:
            if self.child_pid is not None:
                os.kill(self.child_pid, signal.SIGHUP)
        except Exception:
            pass
        return super().closeEvent(e)

    def _set_winsize(self):
        if self.master_fd is None:
            return
        rows = self.rows
        cols = self.cols
        ws = struct.pack('HHHH', rows, cols, self.viewport().height(), self.viewport().width())
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, ws)
        except Exception:
            pass

    @QtCore.Slot()
    def _read_pty(self):
        if self.master_fd is None:
            return

        # Remember if we were at bottom before new data arrives
        self._was_at_bottom = self._is_scrolled_to_bottom()

        try:
            while True:
                data = os.read(self.master_fd, READ_CHUNK)
                if not data:
                    break
                self.parser.feed(data)
        except BlockingIOError:
            pass
        except OSError:
            pass

        self._update_scrollbar_and_view()

    def write_pty(self, data: bytes):
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data)
        except OSError:
            pass

    def _is_scrolled_to_bottom(self) -> bool:
        """Check if scrollbar is at the bottom."""
        return self.scrollbar.value() == self.scrollbar.maximum()

    def _update_scrollbar_and_view(self):
        """Update scrollbar range and auto-scroll if needed."""
        full_lines = list(self.screen.scrollback) + self.screen.primary
        total_lines = len(full_lines)
        max_scroll = max(0, total_lines - self.rows)

        # Update range
        self.scrollbar.setRange(0, max_scroll)

        # Auto-scroll to bottom if user was there
        if self._was_at_bottom:
            self.scrollbar.setValue(max_scroll)

        self.viewport().update()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self.viewport())
        painter.setFont(self.font)

        # Full timeline: scrollback + primary
        full_lines: List[List[Cell]] = list(self.screen.scrollback) + self.screen.primary
        total_lines = len(full_lines)

        # Scroll range
        max_scroll = max(0, total_lines - self.rows)
        self.scrollbar.setRange(0, max_scroll)

        # FIXED: Correct scroll calculation
        # scrollbar value 0 = top (oldest lines)
        # scrollbar value max_scroll = bottom (newest lines)
        scroll_pos = clamp(self.scrollbar.value(), 0, max_scroll)

        # Show lines from scroll_pos onwards
        start = scroll_pos
        visible = full_lines[start:start + self.rows]

        # Pad if needed
        if len(visible) < self.rows:
            visible.extend([[Cell() for _ in range(self.cols)] for _ in range(self.rows - len(visible))])

        # Background
        painter.fillRect(self.viewport().rect(), QtGui.QColor(20, 20, 20))

        # Draw visible lines
        for r, line in enumerate(visible[:self.rows]):
            y = r * self.char_h
            run_fg = run_bg = None
            run_bold = run_underline = False
            x_run_start = 0
            run_text: List[str] = []

            def flush_run(x_start: int):
                if not run_text:
                    return
                text = ''.join(run_text)
                if run_bg is not None:
                    rect = QtCore.QRect(x_start, y, self.fm.horizontalAdvance(text), self.char_h)
                    painter.fillRect(rect, run_bg)
                font = self.font
                font.setBold(run_bold)
                painter.setFont(font)
                painter.setPen(QtGui.QPen(run_fg or QtGui.QColor(235, 235, 235)))
                painter.drawText(x_start, y + self.ascent, text)
                if run_underline:
                    underline_y = y + self.ascent + 1
                    painter.drawLine(x_start, underline_y, x_start + self.fm.horizontalAdvance(text), underline_y)

            for c, cell in enumerate(line):
                ch = cell.ch if len(cell.ch) == 1 else ' '
                fg, bg = cell.fg, cell.bg
                if cell.inverse:
                    fg, bg = (bg or QtGui.QColor(20, 20, 20)), (fg or QtGui.QColor(235, 235, 235))
                same = (fg == run_fg and bg == run_bg and cell.bold == run_bold and cell.underline == run_underline)
                if not same:
                    flush_run(x_run_start)
                    x_run_start = c * self.char_w
                    run_fg, run_bg = fg, bg
                    run_bold, run_underline = cell.bold, cell.underline
                    run_text = []
                run_text.append(ch)
            flush_run(x_run_start)

        # Show cursor only if scrolled to bottom
        if self._is_scrolled_to_bottom() and self.cursor_visible:
            cr = clamp(self.screen.cursor_row, 0, self.rows - 1)
            cc = clamp(self.screen.cursor_col, 0, self.cols - 1)
            x = cc * self.char_w
            y = cr * self.char_h
            painter.fillRect(QtCore.QRect(x, y, max(2, self.char_w // 8), self.char_h),
                             QtGui.QColor(220, 220, 220, 180))

        # Selection
        if self.selection_active and self.sel_start and self.sel_end:
            a = self._norm_sel(self.sel_start, self.sel_end)
            if a:
                (r0, c0), (r1, c1) = a
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Difference)
                for r in range(r0, r1 + 1):
                    x0 = (c0 if r == r0 else 0) * self.char_w
                    x1 = (c1 if r == r1 else self.cols - 1) * self.char_w + self.char_w
                    y = r * self.char_h
                    painter.fillRect(QtCore.QRect(x0, y, x1 - x0, self.char_h), QtGui.QColor(255, 255, 255, 120))

    def _toggle_cursor(self):
        self.cursor_visible = not self.cursor_visible
        if self._is_scrolled_to_bottom():
            self.viewport().update()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        old_rows, old_cols = self.rows, self.cols
        self.rows = max(1, self.viewport().height() // self.char_h)
        self.cols = max(1, self.viewport().width() // self.char_w)
        if self.rows != old_rows or self.cols != old_cols:
            self.screen.resize(self.rows, self.cols)
            self.parser.alt_screen = None
            self._set_winsize()
            self._update_scrollbar_and_view()
        super().resizeEvent(e)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        # Copy/Paste shortcuts
        if e.modifiers() == (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier) and e.key() == QtCore.Qt.Key_C:
            self.copy_selection()
            return
        if (e.modifiers() == (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier) and e.key() == QtCore.Qt.Key_V) or \
           (e.modifiers() == (QtCore.Qt.ShiftModifier) and e.key() == QtCore.Qt.Key_Insert):
            self.paste_from_clipboard()
            return

        seq = self._translate_key(e)
        if seq is not None:
            self.write_pty(seq)
            return
        text = e.text()
        if text:
            self.write_pty(text.encode('utf-8', errors='ignore'))
        else:
            super().keyPressEvent(e)

    def _translate_key(self, e: QtGui.QKeyEvent) -> Optional[bytes]:
        key = e.key()
        mods = e.modifiers()
        alt = bool(mods & QtCore.Qt.AltModifier)
        ctrl = bool(mods & QtCore.Qt.ControlModifier)

        if key == QtCore.Qt.Key_Backspace:
            s = b"\x7f"
            if alt:
                s = b"\x1b" + s
            return s

        # Control characters
        if ctrl and QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
            c = key - QtCore.Qt.Key_A + 1
            s = bytes([c])
            if alt:
                s = b"\x1b" + s
            return s

        mapping = {
            QtCore.Qt.Key_Return: b"\r\n",
            QtCore.Qt.Key_Enter: b"\r\n",
            QtCore.Qt.Key_Tab: b"\t",
            QtCore.Qt.Key_Escape: b"\x1b",
            QtCore.Qt.Key_Left: b"\x1b[D",
            QtCore.Qt.Key_Right: b"\x1b[C",
            QtCore.Qt.Key_Up: b"\x1b[A",
            QtCore.Qt.Key_Down: b"\x1b[B",
            QtCore.Qt.Key_Home: b"\x1b[H",
            QtCore.Qt.Key_End: b"\x1b[F",
            QtCore.Qt.Key_PageUp: b"\x1b[5~",
            QtCore.Qt.Key_PageDown: b"\x1b[6~",
            QtCore.Qt.Key_Insert: b"\x1b[2~",
            QtCore.Qt.Key_Delete: b"\x1b[3~",
            QtCore.Qt.Key_F1: b"\x1bOP",
            QtCore.Qt.Key_F2: b"\x1bOQ",
            QtCore.Qt.Key_F3: b"\x1bOR",
            QtCore.Qt.Key_F4: b"\x1bOS",
            QtCore.Qt.Key_F5: b"\x1b[15~",
            QtCore.Qt.Key_F6: b"\x1b[17~",
            QtCore.Qt.Key_F7: b"\x1b[18~",
            QtCore.Qt.Key_F8: b"\x1b[19~",
            QtCore.Qt.Key_F9: b"\x1b[20~",
            QtCore.Qt.Key_F10: b"\x1b[21~",
            QtCore.Qt.Key_F11: b"\x1b[23~",
            QtCore.Qt.Key_F12: b"\x1b[24~",
        }
        if key in mapping:
            s = mapping[key]
            if alt:
                s = b"\x1b" + s
            return s
        return None

    # --------------------------- Selection/Clipboard ------------------------
    def _view_to_cell(self, pos: QtCore.QPoint) -> Tuple[int, int]:
        r = clamp(pos.y() // self.char_h, 0, self.rows - 1)
        c = clamp(pos.x() // self.char_w, 0, self.cols - 1)
        return (r, c)

    def _norm_sel(self, a: Tuple[int, int], b: Tuple[int, int]) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        (r0, c0), (r1, c1) = a, b
        if (r0, c0) > (r1, c1):
            r0, c0, r1, c1 = r1, c1, r0, c0
        return ((r0, c0), (r1, c1))

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            self.selection_active = True
            self.sel_start = self._view_to_cell(e.position().toPoint())
            self.sel_end = self.sel_start
            self.viewport().update()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self.selection_active and self.sel_start is not None:
            self.sel_end = self._view_to_cell(e.position().toPoint())
            self.viewport().update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            if self.selection_active:
                self.selection_active = False
                self.viewport().update()
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        menu = QtWidgets.QMenu(self)
        act_copy = menu.addAction("Copy")
        act_paste = menu.addAction("Paste")
        act_reset = menu.addAction("Reset Terminal")
        chosen = menu.exec_(e.globalPos())
        if chosen == act_copy:
            self.copy_selection()
        elif chosen == act_paste:
            self.paste_from_clipboard()
        elif chosen == act_reset:
            self.reset_terminal()

    def copy_selection(self):
        if not (self.sel_start and self.sel_end):
            return
        a = self._norm_sel(self.sel_start, self.sel_end)
        if not a:
            return
        (r0, c0), (r1, c1) = a
        lines: List[str] = []
        for r in range(r0, r1 + 1):
            row = self.screen.primary[r]
            start = c0 if r == r0 else 0
            end = c1 if r == r1 else self.cols - 1
            text = ''.join(cell.ch for cell in row[start:end + 1]).rstrip()
            lines.append(text)
        QtWidgets.QApplication.clipboard().setText('\n'.join(lines))

    def paste_from_clipboard(self):
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return
        self.write_pty(text.encode('utf-8'))

    def reset_terminal(self):
        self.screen.reset()
        self.parser = AnsiParser(self.screen)
        self._update_scrollbar_and_view()


# Backwards compatibility
class QtTerminalWidget(TerminalWidget):
    """Compat alias for legacy imports."""


# --------------------------- Example Window -------------------------------
class TerminalWindow(QtWidgets.QMainWindow):
    def __init__(self, start_cmd: Optional[Union[str, List[str]]] = None):
        super().__init__()
        self.setWindowTitle("QtTerminalWidget – Fixed")
        self.resize(1000, 600)
        self.term = TerminalWidget(start_pty=True)
        self.setCentralWidget(self.term)

        tb = self.addToolBar("Actions")
        act_copy = QtGui.QAction("Copy", self)
        act_copy.triggered.connect(self.term.copy_selection)
        act_paste = QtGui.QAction("Paste", self)
        act_paste.triggered.connect(self.term.paste_from_clipboard)
        act_reset = QtGui.QAction("Reset", self)
        act_reset.triggered.connect(self.term.reset_terminal)
        tb.addAction(act_copy)
        tb.addAction(act_paste)
        tb.addAction(act_reset)

        if start_cmd:
            self.term.start_process(start_cmd)


def main():
    import argparse
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)

    parser = argparse.ArgumentParser(description="QtTerminalWidget – Standalone")
    parser.add_argument("--cmd", help="Start command as string (executed via shell -lc)", default=None)
    parser.add_argument("--cwd", help="Working directory for start command", default=None)
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    win = TerminalWindow(start_cmd=args.cmd)
    if args.cmd and args.cwd:
        win.term.start_process(args.cmd, cwd=args.cwd)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
