import argparse
import sys
import shutil
import subprocess
import shlex
import re
import itertools
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple, Sequence, Iterable, Callable

from PySide6.QtGui import QTextCursor, QAction, QKeySequence, QShortcut, QIcon
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QPlainTextEdit, QTableView, QMenu,
    QMessageBox, QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QSplitter, QStyle, QCheckBox, QProgressBar,
    QDialogButtonBox, QSystemTrayIcon
)

from models import PackageModel, PackageItem
import providers
from runner import ExpectRunner
from settings import settings
from settings_dialog import SettingsDialog
from cleanup_dialog import CleanupDialog
from i18n import tr
import update_service


APP_DIR = Path(__file__).resolve().parent
ICON_PATH = APP_DIR / "assets" / "wrappac_logo.svg"
SINGLE_INSTANCE_SERVER_NAME = "wrappac-single-instance"


def _load_app_icon() -> Optional[QIcon]:
    if ICON_PATH.exists():
        return QIcon(str(ICON_PATH))
    return None


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _check_output(args: List[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def _notify_running_instance(server_name: str, message: str, timeout_ms: int = 1000) -> bool:
    """Send a message to a running instance if possible."""

    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(timeout_ms):
        return False

    if message:
        socket.write(message.encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)

    socket.disconnectFromServer()
    socket.waitForDisconnected(timeout_ms)
    return True


def _create_single_instance_server(server_name: str) -> Optional[QLocalServer]:
    """Create a QLocalServer for enforcing a single running instance."""

    server = QLocalServer()
    if server.listen(server_name):
        return server

    if server.serverError() == QAbstractSocket.AddressInUseError:
        QLocalServer.removeServer(server_name)
        if server.listen(server_name):
            return server

    return None


def _run_update_service(qt_args: Sequence[str]) -> int:
    try:
        pac = providers.updates_pacman_count()
        aur = providers.updates_aur_count()
        flp = providers.updates_flatpak_count()
    except Exception:
        pac = aur = flp = 0

    total = pac + aur + flp
    if total <= 0:
        return 0

    if _notify_running_instance(SINGLE_INSTANCE_SERVER_NAME, "show-updates"):
        return 0

    qt_argv = [sys.argv[0], *qt_args]
    app = QApplication(qt_argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        cmd = update_service.build_launch_command(show_updates=True)
        subprocess.Popen(cmd)
        app.quit()
        return 0

    icon = _load_app_icon()
    if icon is None:
        icon = app.style().standardIcon(QStyle.SP_MessageBoxInformation)

    tray = QSystemTrayIcon(icon)
    tray.setToolTip(tr("update_service_tray_tooltip", total))

    menu = QMenu()

    def _open_wrappac():
        cmd = update_service.build_launch_command(show_updates=True)
        subprocess.Popen(cmd)
        tray.hide()
        app.quit()

    action_open = QAction(tr("update_service_tray_open"), tray)
    action_open.triggered.connect(_open_wrappac)
    menu.addAction(action_open)

    def _quit_tray():
        tray.hide()
        app.quit()

    action_quit = QAction(tr("update_service_tray_quit"), tray)
    action_quit.triggered.connect(_quit_tray)
    menu.addAction(action_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: _open_wrappac() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
    tray.messageClicked.connect(_open_wrappac)

    tray.show()

    title = tr("update_service_tray_title")
    summary = tr("update_service_tray_message", total)
    details = tr("update_service_tray_details", pac, aur, flp)
    tray.showMessage(title, f"{summary}\n{details}", QSystemTrayIcon.Information, 15000)

    return app.exec()


class RefreshThread(QThread):
    """Load package lists in the background to keep the UI responsive."""
    finished_with = Signal(list)   # List[PackageItem]

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            pkgs = providers.list_all()
        except Exception:
            pkgs = []
        self.finished_with.emit(pkgs)


class UpdateCheckThread(QThread):
    """Collect update counters in the background."""

    finished_with = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            pac = providers.updates_pacman_count()
            aur = providers.updates_aur_count()
            flp = providers.updates_flatpak_count()
        except Exception:
            pac = aur = flp = 0
        self.finished_with.emit(pac, aur, flp)


class PasswordDialog(QDialog):
    """Dialog for entering a password without echo."""

    def __init__(self, prompt: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_password_title"))
        self.setModal(True)

        layout = QVBoxLayout(self)
        label = QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def password(self) -> str:
        return self.edit.text()


class TerminalEdit(QPlainTextEdit):
    """Line-oriented terminal widget that buffers input before sending it."""

    def __init__(self, write_bytes_cb, send_sigint_cb, is_running_cb, parent=None):
        super().__init__(parent)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._write_bytes = write_bytes_cb
        self._send_sigint = send_sigint_cb
        self._is_running = is_running_cb
        self._input_enabled = True

        self._read_only_end = 0  # Text before this marker is read-only

        self.setPlaceholderText(
            "Terminal-Konsole\n"
            "Tippe hier und drücke Enter zum Senden\n"
            "Ctrl+C = Prozess abbrechen (SIGINT)\n"
            "Shift+Ctrl+C = Kopieren | Shift+Ctrl+V = Einfügen"
        )

        sc_copy = QShortcut(QKeySequence("Shift+Ctrl+C"), self)
        sc_copy.activated.connect(self.copy)
        sc_paste = QShortcut(QKeySequence("Shift+Ctrl+V"), self)
        sc_paste.activated.connect(self._paste_text)

        font = self.font()
        font.setFamily("Monospace")
        font.setPointSize(10)
        self.setFont(font)

    def append_output(self, text: str):
        """Append process output to the read-only section."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

        self.insertPlainText(text)

        self._read_only_end = self.textCursor().position()

        self.ensureCursorVisible()

    def set_input_enabled(self, enabled: bool):
        self._input_enabled = enabled

    def keyPressEvent(self, ev):
        if not self._input_enabled:
            if ev.modifiers() == Qt.ControlModifier and ev.key() == Qt.Key_C:
                self.copy()
            return

        if ev.modifiers() == Qt.ControlModifier and ev.key() == Qt.Key_C:
            if self._is_running():
                self._send_sigint()
            else:
                self.copy()
            return

        if not self._is_running():
            if ev.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
                super().keyPressEvent(ev)
            return

        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._send_current_line()
            return

        if ev.key() == Qt.Key_Backspace:
            if self.textCursor().position() > self._read_only_end:
                super().keyPressEvent(ev)
            return

        if ev.key() == Qt.Key_Delete:
            super().keyPressEvent(ev)
            return

        # Allow navigation keys to behave normally
        if ev.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                       Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown):
            super().keyPressEvent(ev)
            return

        # Tab key inserts spaces to keep indentation predictable
        if ev.key() == Qt.Key_Tab:
            self.insertPlainText("    ")  # 4 Spaces
            return

        # Default text input
        text = ev.text()
        if text and text.isprintable():
            # Ensure the cursor stays inside the editable area
            cursor = self.textCursor()
            if cursor.position() < self._read_only_end:
                # Jump to the end if the cursor is inside the read-only part
                cursor.movePosition(QTextCursor.End)
                self.setTextCursor(cursor)

            super().keyPressEvent(ev)
            return

        ev.ignore()

    def mousePressEvent(self, ev):
        """Allow clicks without moving into the read-only section."""
        super().mousePressEvent(ev)

        # After the click: move back to the end when the cursor enters the read-only region
        cursor = self.textCursor()
        if cursor.position() < self._read_only_end and not cursor.hasSelection():
            cursor.setPosition(self._read_only_end)
            self.setTextCursor(cursor)

    def _send_current_line(self):
        """Collect the current input line and send it to the PTY."""
        # Retrieve text starting from the read-only marker
        full_text = self.toPlainText()
        input_line = full_text[self._read_only_end:]

        # Visually append a newline
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)
        self.insertPlainText("\n")

        # Update the read-only marker after appending the newline
        self._read_only_end = self.textCursor().position()

        # Send the input line including the newline to the PTY
        self._write_bytes((input_line + "\n").encode('utf-8'))

    def _paste_text(self):
        """Handle the Shift+Ctrl+V paste shortcut."""
        if not self._is_running():
            return

        md = QApplication.clipboard().mimeData()
        if md and md.hasText():
            text = md.text()
            if text:
                # Move to the end if necessary
                cursor = self.textCursor()
                if cursor.position() < self._read_only_end:
                    cursor.movePosition(QTextCursor.End)
                    self.setTextCursor(cursor)

                self.insertPlainText(text)

    def contextMenuEvent(self, event):
        """Build the custom context menu."""
        menu = QMenu(self)

        # Copy
        action_copy = menu.addAction("Kopieren (Shift+Ctrl+C)")
        action_copy.triggered.connect(self.copy)
        action_copy.setEnabled(self.textCursor().hasSelection())

        # Paste
        if self._is_running():
            action_paste = menu.addAction("Einfügen (Shift+Ctrl+V)")
            action_paste.triggered.connect(self._paste_text)

        menu.addSeparator()

        # SIGINT
        if self._is_running():
            action_sigint = menu.addAction("Prozess abbrechen (Ctrl+C)")
            action_sigint.triggered.connect(self._send_sigint)

        # Clear
        action_clear = menu.addAction("Terminal leeren")
        action_clear.triggered.connect(self._clear_terminal)

        menu.exec(event.globalPos())

    def _clear_terminal(self):
        """Clear the terminal and reset the read-only boundary."""
        self.clear()
        self._read_only_end = 0


class MainWindow(QMainWindow):
    def __init__(self, show_updates: bool = False, tray_mode: bool = False):
        super().__init__()
        self._tray_mode = tray_mode
        icon = _load_app_icon()
        if icon:
            self.setWindowIcon(icon)
        self.setWindowTitle(tr("app_title"))
        self.resize(1300, 820)

        self.current_source: str = "Alle"
        self.install_queue: List[Tuple[str, str, Dict[str, str]]] = []
        self._refresh_thread: Optional[RefreshThread] = None
        self._update_thread: Optional[UpdateCheckThread] = None
        self._is_loading: bool = False
        self._update_indicator_state: Optional[Tuple[bool, str]] = None
        self._single_instance_server: Optional[QLocalServer] = None

        self.model = PackageModel()
        self.table_installed = QTableView()
        self.table_installed.setModel(self.model)
        self.table_installed.setSelectionBehavior(QTableView.SelectRows)
        self.table_installed.setSortingEnabled(True)
        self.table_installed.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_installed.customContextMenuRequested.connect(self._ctx_menu_installed)
        self.table_installed.verticalHeader().setDefaultSectionSize(24)

        self.installed_search_edit = QLineEdit()
        self.installed_search_edit.setPlaceholderText(tr("installed_filter_placeholder"))
        self.installed_search_edit.setClearButtonEnabled(True)
        self.installed_search_edit.textChanged.connect(self._on_installed_filter_changed)

        self.btn_all = QPushButton(tr("btn_all"))
        self.btn_repo = QPushButton(tr("btn_official"))
        self.btn_aur = QPushButton(tr("btn_aur"))
        self.btn_flatpak = QPushButton(tr("btn_flatpak"))
        for b in (self.btn_all, self.btn_repo, self.btn_aur, self.btn_flatpak):
            b.setCheckable(True)
        self.btn_all.setChecked(True)

        self.btn_all.clicked.connect(lambda: self._set_src("Alle"))
        self.btn_repo.clicked.connect(lambda: self._set_src("Repo"))
        self.btn_aur.clicked.connect(lambda: self._set_src("AUR"))
        self.btn_flatpak.clicked.connect(lambda: self._set_src("Flatpak"))

        self.btn_refresh = QPushButton(tr("btn_refresh"))
        self.btn_refresh.clicked.connect(self.refresh)

        self.btn_system_update = QPushButton(tr("btn_system_update"))
        self.btn_system_update.clicked.connect(self._system_update_dialog)

        self.btn_reflector = QPushButton(tr("btn_run_reflector"))
        self.btn_reflector.clicked.connect(self._run_reflector)

        self.loading_indicator = QProgressBar()
        self.loading_indicator.setRange(0, 0)
        self.loading_indicator.setVisible(False)
        self.loading_indicator.setFixedWidth(200)
        self.loading_indicator.setTextVisible(True)
        self.loading_indicator.setFormat(tr("status_loading_packages"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder"))
        self.btn_search = QPushButton(tr("btn_search"))
        self.btn_search.clicked.connect(self._on_search_clicked)
        self.search_info = QLabel(tr("search_info_select_source"))
        self.search_info.setStyleSheet("color: gray;")
        self.results = QTableWidget(0, 6)
        self._setup_results_table()
        self.results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._ctx_menu_results)

        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.btn_queue_install = QPushButton(tr("btn_install_queue"))
        self.btn_queue_clear = QPushButton(tr("btn_clear_queue"))
        self.btn_queue_remove = QPushButton(tr("btn_remove_from_queue"))

        self.btn_queue_install.clicked.connect(self._queue_install_all)
        self.btn_queue_clear.clicked.connect(self._queue_clear)
        self.btn_queue_remove.clicked.connect(self._queue_remove_selected)

        queue_box = QVBoxLayout()
        queue_box.addWidget(QLabel(tr("install_queue")))
        queue_box.addWidget(self.queue_list, 1)
        row_q = QHBoxLayout()
        row_q.addWidget(self.btn_queue_install)
        row_q.addWidget(self.btn_queue_remove)
        row_q.addWidget(self.btn_queue_clear)
        queue_box.addLayout(row_q)

        queue_widget = QWidget()
        queue_widget.setLayout(queue_box)
        queue_widget.setMinimumWidth(360)

        self.console = TerminalEdit(
            write_bytes_cb=self._write_terminal_bytes,
            send_sigint_cb=self._send_sigint,
            is_running_cb=lambda: self.runner.is_running()
        )

        self.runner = ExpectRunner(self._handle_runner_output)
        self.runner.started.connect(lambda: None)
        self.runner.finished.connect(lambda _code: self._schedule_refresh())
        self.runner.password_requested.connect(self._prompt_for_password)

        topbar = QHBoxLayout()
        topbar.addWidget(self.btn_refresh)
        topbar.addWidget(self.btn_system_update)
        self.btn_system_cleanup = QPushButton(tr("btn_system_cleanup"))
        self.btn_system_cleanup.clicked.connect(self._system_cleanup_dialog)

        topbar.addWidget(self.btn_reflector)
        topbar.addWidget(self.btn_system_cleanup)
        topbar.addWidget(self.loading_indicator)
        topbar.addSpacing(12)
        for b in (self.btn_all, self.btn_repo, self.btn_aur, self.btn_flatpak):
            topbar.addWidget(b)
        topbar.addStretch(1)

        installed_panel = QWidget()
        installed_layout = QVBoxLayout(installed_panel)
        installed_layout.setContentsMargins(0, 0, 0, 0)
        installed_layout.setSpacing(6)

        installed_search_row = QHBoxLayout()
        installed_search_row.addWidget(self.installed_search_edit)
        installed_layout.addLayout(installed_search_row)
        installed_layout.addWidget(self.table_installed)

        mid_split = QSplitter()
        mid_split.setOrientation(Qt.Vertical)
        mid_split.addWidget(installed_panel)
        mid_split.addWidget(self.console)
        mid_split.setSizes([500, 320])

        left_bottom_box = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.btn_search)
        left_bottom_box.addLayout(row)
        left_bottom_box.addWidget(self.search_info)
        left_bottom_box.addWidget(self.results, 1)

        left_bottom_widget = QWidget()
        left_bottom_widget.setLayout(left_bottom_box)

        bottom_split = QSplitter()
        bottom_split.setOrientation(Qt.Horizontal)
        bottom_split.addWidget(left_bottom_widget)
        bottom_split.addWidget(queue_widget)
        bottom_split.setSizes([900, 400])

        root = QWidget()
        root_v = QVBoxLayout(root)
        root_v.addLayout(topbar)
        root_v.addWidget(mid_split, 3)
        root_v.addWidget(bottom_split, 2)
        self.setCentralWidget(root)

        self._build_menu()
        self._apply_settings()
        self.refresh()

        if tray_mode:
            self.hide()
        else:
            if show_updates:
                QTimer.singleShot(300, self._system_update_dialog)
            self.show()

        self.search_edit.returnPressed.connect(self.btn_search.click)

        sc_clear = QShortcut(QKeySequence("Ctrl+K"), self.console)
        sc_clear.activated.connect(lambda: self.console.clear())

    def _build_menu(self):
        m = self.menuBar().addMenu(tr("menu_actions"))
        act_refresh = QAction(tr("action_refresh"), self)
        act_refresh.triggered.connect(self.refresh)
        m.addAction(act_refresh)

        m_settings = self.menuBar().addMenu(tr("menu_settings"))
        act_settings = QAction(tr("action_settings"), self)
        act_settings.triggered.connect(self._show_settings)
        act_settings.setShortcut("Ctrl+,")
        m_settings.addAction(act_settings)

        m_h = self.menuBar().addMenu(tr("menu_help"))
        h1 = QAction(tr("action_tips"), self)
        h1.triggered.connect(lambda: QMessageBox.information(
            self, tr("action_tips"), tr("tips_text")
        ))
        m_h.addAction(h1)

    def setup_single_instance_server(self, server: QLocalServer) -> None:
        self._single_instance_server = server
        server.newConnection.connect(self._on_single_instance_connection)

    @Slot()
    def _on_single_instance_connection(self):
        if not self._single_instance_server:
            return

        while self._single_instance_server.hasPendingConnections():
            socket = self._single_instance_server.nextPendingConnection()
            if not socket:
                continue
            socket.setProperty("wrappac_handled", False)
            socket.readyRead.connect(lambda s=socket: self._process_single_instance_socket(s))
            socket.disconnected.connect(lambda s=socket: self._on_single_instance_socket_disconnected(s))
            if socket.bytesAvailable():
                self._process_single_instance_socket(socket)

    def _process_single_instance_socket(self, socket: QLocalSocket) -> None:
        if not socket or not socket.bytesAvailable():
            return

        data = bytes(socket.readAll()).decode("utf-8", errors="ignore").strip()
        if not data:
            data = "show"

        self._handle_single_instance_command(data)
        socket.setProperty("wrappac_handled", True)
        socket.disconnectFromServer()

    def _on_single_instance_socket_disconnected(self, socket: QLocalSocket) -> None:
        if not socket:
            return

        handled = bool(socket.property("wrappac_handled"))
        if not handled:
            self._handle_single_instance_command("show")

        socket.deleteLater()

    def _handle_single_instance_command(self, command: str) -> None:
        self._focus_main_window()
        normalized = command.strip().lower()
        if normalized in {"--show-updates", "show-updates"}:
            QTimer.singleShot(150, self._system_update_dialog)

    def _focus_main_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        handle = self.windowHandle()
        if handle is not None:
            handle.requestActivate()

    def closeEvent(self, event):
        """Handle window close event."""
        if self._tray_mode:
            event.ignore()
            self.hide()
            # Keep the application running quietly in tray mode.
        else:
            event.accept()
            super().closeEvent(event)

    def _show_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._apply_settings()
            self.console.append_output(tr("msg_settings_saved") + "\n")

    def _apply_settings(self):
        font_size = settings.get("terminal_font_size", 10)
        font = self.console.font()
        font.setPointSize(font_size)
        self.console.setFont(font)

        if settings.get("terminal_theme") == "dark":
            self.console.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        else:
            self.console.setStyleSheet("background-color: #ffffff; color: #000000;")

        self.btn_refresh.setText(tr("btn_refresh"))
        self.btn_system_update.setText(tr("btn_system_update"))
        self.btn_reflector.setText(tr("btn_run_reflector"))
        self.loading_indicator.setFormat(tr("status_loading_packages"))
        self._update_reflector_button_state()
        self.installed_search_edit.setPlaceholderText(tr("installed_filter_placeholder"))

    def _system_update_dialog(self):
        if self.runner.is_running():
            QMessageBox.information(
                self, tr("dialog_update_title"),
                tr("dialog_update_process_running")
            )
            return

        if self._update_thread and self._update_thread.isRunning():
            return

        self.console.append_output(tr("msg_update_check_start") + "\n")
        self.btn_system_update.setEnabled(False)

        self._update_indicator_state = (
            self.loading_indicator.isVisible(),
            self.loading_indicator.format(),
        )
        self.loading_indicator.setFormat(tr("status_checking_updates"))
        self.loading_indicator.setVisible(True)

        self._update_thread = UpdateCheckThread(self)
        self._update_thread.finished_with.connect(self._on_update_counts_ready)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    @Slot(int, int, int)
    def _on_update_counts_ready(self, pac: int, aur: int, flp: int):
        self.btn_system_update.setEnabled(True)
        self._restore_update_indicator()
        self._report_provider_errors()
        self._show_update_dialog_counts(pac, aur, flp)

    @Slot()
    def _on_update_thread_finished(self):
        self.btn_system_update.setEnabled(True)
        if self._update_thread:
            self._update_thread.deleteLater()
            self._update_thread = None
        self._restore_update_indicator()

    def _restore_update_indicator(self):
        if not self._update_indicator_state:
            return
        was_visible, fmt = self._update_indicator_state
        self.loading_indicator.setFormat(fmt)
        self.loading_indicator.setVisible(was_visible)
        self._update_indicator_state = None

    def _show_update_dialog_counts(self, pac: int, aur: int, flp: int):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog_update_title"))
        lay = QVBoxLayout(dlg)

        lbl = QLabel(
            f"{tr('dialog_update_available')}\n"
            f"• {tr('dialog_update_official')}: {pac}\n"
            f"• {tr('dialog_update_aur')}: {aur}\n"
            f"• {tr('dialog_update_flatpak')}: {flp}"
        )
        lay.addWidget(lbl)

        cb_pac = QCheckBox(f"{tr('dialog_update_official')} (pacman -Syu) – {pac} {tr('updates')}")
        cb_aur = QCheckBox(f"{tr('dialog_update_aur')} (yay -Syu) – {aur} {tr('updates')}")
        cb_flp = QCheckBox(f"{tr('dialog_update_flatpak')} (flatpak update) – {flp} {tr('updates')}")
        cb_preview = QCheckBox(tr("dialog_update_preview"))

        cb_pac.setChecked(pac > 0)
        cb_aur.setChecked(aur > 0)
        cb_flp.setChecked(flp > 0)

        tool = settings.get_aur_helper()
        if not tool:
            cb_aur.setEnabled(False)
            cb_aur.setText(f"{tr('dialog_update_aur')} {tr('dialog_update_no_helper')}")

        lay.addWidget(cb_pac)
        lay.addWidget(cb_aur)
        lay.addWidget(cb_flp)

        lay.addSpacing(8)
        lay.addWidget(cb_preview)

        row = QHBoxLayout()
        ok = QPushButton(tr("btn_start"))
        cancel = QPushButton(tr("btn_cancel"))
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

        if dlg.exec() != QDialog.Accepted:
            return

        self._system_update_run(
            do_pac=cb_pac.isChecked(),
            do_aur=cb_aur.isChecked(),
            do_flp=cb_flp.isChecked(),
            preview=cb_preview.isChecked(),
        )

    def _system_update_run(self, do_pac: bool, do_aur: bool, do_flp: bool, preview: bool):
        if self.runner.is_running():
            QMessageBox.information(
                self, tr("dialog_update_title"),
                tr("dialog_update_process_running")
            )
            return

        cmds: list[Sequence[str] | dict | tuple] = []

        if do_pac and shutil.which("pacman"):
            if preview:
                cmds.append(["pacman", "-Qu"])
            else:
                base_cmd = ["pacman", "-Syu"]
                if not settings.get("pacman_noconfirm", False):
                    cmds.append(base_cmd)
                else:
                    cmds.append(base_cmd + ["--noconfirm"])

        if do_aur:
            tool = settings.get_aur_helper()
            if tool:
                if preview:
                    cmds.append([tool, "-Qua"])
                else:
                    cmds.append([tool, "-Syu"])

        if do_flp and shutil.which("flatpak"):
            if preview:
                cmds.append(["flatpak", "remote-ls", "--updates"])
            else:
                scope = settings.get("flatpak_default_scope", "user")
                if scope == "user":
                    cmds.append(["flatpak", "update", "--user", "-y"])
                else:
                    cmds.append(["flatpak", "update", "-y"])

        if not cmds:
            QMessageBox.information(
                self, tr("dialog_update_title"),
                tr("dialog_update_no_source")
            )
            return

        self._run_cmds_sequential(cmds)

    def _system_cleanup_dialog(self):
        if self.runner.is_running():
            QMessageBox.information(
                self, tr("dialog_cleanup_title"),
                tr("dialog_update_process_running")
            )
            return

        dlg = CleanupDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        selections = dlg.selections()
        summary_lines: List[str] = []
        if selections.get("orphans"):
            summary_lines.append("• " + tr("cleanup_summary_orphans"))
        if selections.get("cache"):
            summary_lines.append("• " + tr("cleanup_summary_cache"))
        if selections.get("flatpak"):
            summary_lines.append("• " + tr("cleanup_summary_flatpak"))
        if selections.get("aur"):
            summary_lines.append("• " + tr("cleanup_summary_aur"))
        if selections.get("logs"):
            summary_lines.append("• " + tr("cleanup_summary_logs"))

        if not summary_lines:
            QMessageBox.information(
                self, tr("dialog_cleanup_title"),
                tr("cleanup_no_action_selected")
            )
            return

        summary_text = "\n".join(summary_lines)
        confirm_text = tr("cleanup_confirm_question", summary_text)
        if QMessageBox.question(self, tr("dialog_confirm"), confirm_text) != QMessageBox.Yes:
            return

        self._execute_cleanup_actions(selections)

    def _execute_cleanup_actions(self, selections: Dict[str, bool]):
        cmds: List[Dict[str, object]] = []
        self.console.append_output(tr("msg_cleanup_start") + "\n")

        if selections.get("orphans"):
            if shutil.which("pacman"):
                message_no_orphans = tr("msg_cleanup_orphans_none")
                script = (
                    "orphans=$(pacman -Qtdq); "
                    "if [ -z \"$orphans\" ]; then "
                    f"echo {shlex.quote(message_no_orphans)}; "
                    "else pacman -Rns $orphans; fi"
                )
                cmds.append({
                    "argv": ["bash", "-lc", script],
                    "needs_root": True,
                })
            else:
                self.console.append_output(tr("cleanup_skip_orphans_missing") + "\n")

        if selections.get("cache"):
            if shutil.which("pacman"):
                keep = max(0, int(settings.get("cleanup_keep_pkg_versions", 2)))
                fallback_note = tr("msg_cleanup_cache_fallback")
                script = (
                    "if command -v paccache >/dev/null 2>&1; then "
                    f"paccache -rk {keep}; "
                    "else "
                    f"echo {shlex.quote(fallback_note)}; "
                    "pacman -Sc --noconfirm; fi"
                )
                cmds.append({
                    "argv": ["bash", "-lc", script],
                    "needs_root": True,
                })
            else:
                self.console.append_output(tr("cleanup_skip_cache_missing") + "\n")

        if selections.get("flatpak"):
            if shutil.which("flatpak"):
                cmds.append({
                    "argv": ["flatpak", "uninstall", "--user", "--unused", "-y"],
                    "needs_root": False,
                })
                if settings.get("flatpak_default_scope", "user") == "system":
                    cmds.append({
                        "argv": ["flatpak", "uninstall", "--system", "--unused", "-y"],
                        "needs_root": True,
                    })
            else:
                self.console.append_output(tr("cleanup_skip_flatpak_missing") + "\n")

        if selections.get("aur"):
            cleanup_msg = tr("msg_cleanup_aur_cleaning")
            done_msg = tr("msg_cleanup_aur_done")
            script = (
                "dirs=(~/.cache/yay ~/.cache/paru ~/.cache/pikaur ~/.cache/trizen ~/.cache/aurman); "
                "for dir in \"${dirs[@]}\"; do "
                "if [ -d \"$dir\" ]; then "
                f"echo {shlex.quote(cleanup_msg)} \"$dir\"; "
                "find \"$dir\" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; fi; done; "
                f"echo {shlex.quote(done_msg)}"
            )
            cmds.append({
                "argv": ["bash", "-lc", script],
                "needs_root": False,
            })

        if selections.get("logs"):
            if shutil.which("journalctl"):
                days = max(1, int(settings.get("cleanup_log_max_age_days", 14)))
                cmds.append({
                    "argv": ["journalctl", f"--vacuum-time={days}d"],
                    "needs_root": True,
                })
            else:
                self.console.append_output(tr("cleanup_skip_logs_missing") + "\n")

        if not cmds:
            self.console.append_output(tr("cleanup_no_action_possible") + "\n")
            return

        self._run_cmds_sequential(
            cmds,
            final_message=tr("msg_cleanup_complete"),
            schedule_refresh=False,
        )

    def _run_cmds_sequential(
        self,
        cmds: List[Sequence[str] | Dict[str, object] | Tuple[Iterable[str], bool]],
        *,
        final_message: Optional[str] = None,
        schedule_refresh: bool = True,
        on_done: Optional[Callable[[bool], None]] = None,
    ):
        normalized: List[Tuple[List[str], bool]] = []
        for cmd in cmds:
            argv: List[str]
            needs_root = False
            if isinstance(cmd, dict):
                raw = cmd.get("argv", [])
                argv = list(raw) if isinstance(raw, Iterable) else []
                needs_root = bool(cmd.get("needs_root", False))
            elif isinstance(cmd, tuple):
                seq, needs_root = cmd
                argv = list(seq)
                needs_root = bool(needs_root)
            else:
                argv = list(cmd)
                needs_root = self._command_requires_root(argv)

            if not argv:
                continue
            normalized.append((argv, needs_root))

        self._cmd_queue = normalized

        message = final_message if final_message is not None else tr("msg_updates_complete")
        completed_codes: List[int] = []

        def _finish_sequence():
            success = bool(completed_codes) and all(code == 0 for code in completed_codes)
            if message:
                self.console.append_output("\n" + message + "\n")
            if on_done:
                try:
                    on_done(success)
                except Exception:
                    pass
            if schedule_refresh:
                self._schedule_refresh()

        def _run_next():
            if not self._cmd_queue:
                _finish_sequence()
                return

            argv, needs_root = self._cmd_queue.pop(0)
            if needs_root:
                root_cmd = settings.get_root_command()
                if root_cmd:
                    self.runner.run(root_cmd + argv)
                else:
                    self.console.append_output(tr("msg_no_root_method") + "\n")
                    _run_next()
            else:
                self.runner.run(argv)

        try:
            self.runner.finished.disconnect()
        except Exception:
            pass
        if not self._cmd_queue:
            _finish_sequence()
            return

        def _on_command_finished(exit_code: int) -> None:
            completed_codes.append(exit_code)
            _run_next()

        self.runner.finished.connect(_on_command_finished)
        _run_next()

    def _command_requires_root(self, argv: List[str]) -> bool:
        if not argv:
            return False
        if argv[0] in {"pacman", "reflector", "paccache", "journalctl"}:
            if argv[0] == "pacman" and len(argv) >= 2 and argv[1] == "-Qu":
                return False
            return True
        return False

    def _run_reflector(self):
        if self.runner.is_running():
            QMessageBox.information(
                self, tr("dialog_update_title"),
                tr("dialog_update_process_running")
            )
            return

        if not providers.is_reflector_available():
            QMessageBox.warning(
                self, tr("dialog_update_title"),
                tr("dialog_update_reflector_missing")
            )
            self._update_reflector_button_state()
            return

        cmd = providers.build_reflector_command()
        if not cmd:
            self.console.append_output(tr("msg_reflector_unavailable") + "\n")
            self._report_provider_errors()
            self._update_reflector_button_state()
            return

        self.console.append_output(tr("msg_reflector_start") + "\n")
        self._run_cmds_sequential(
            [cmd],
            final_message=tr("msg_reflector_complete"),
            schedule_refresh=False,
        )

    def _update_reflector_button_state(self):
        available = providers.is_reflector_available()

        if not available:
            self.btn_reflector.setEnabled(False)
            self.btn_reflector.setToolTip(tr("tooltip_reflector_missing"))
        else:
            self.btn_reflector.setEnabled(True)
            self.btn_reflector.setToolTip(tr("tooltip_reflector_ready"))

    def _setup_results_table(self):
        self.results.setRowCount(0)
        self.results.setColumnCount(6)
        self.results.setHorizontalHeaderLabels([
            tr("table_package"),
            tr("table_version"),
            tr("table_branch_repo"),
            tr("table_remote_source"),
            tr("table_source"),
            tr("table_description")
        ])
        self.results.verticalHeader().setVisible(False)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results.setSelectionBehavior(QTableWidget.SelectRows)
        self.results.setSelectionMode(QTableWidget.ExtendedSelection)
        self.results.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setSortingEnabled(True)
        for i, w in enumerate([300, 100, 130, 140, 120, 460]):
            self.results.setColumnWidth(i, w)

    def _schedule_refresh(self):
        if settings.get("auto_refresh_after_install", True):
            delay = settings.get("refresh_delay_ms", 400)
            QTimer.singleShot(delay, self.refresh)

    def _write_terminal_bytes(self, data: bytes):
        self.runner.write_bytes(data)

    def _send_sigint(self):
        self.runner.send_sigint()

    def _handle_runner_output(self, text: str):
        self.console.append_output(text)
        if self.runner.is_waiting_for_password():
            self.console.set_input_enabled(False)

    def _prompt_for_password(self, context: str):
        self.console.set_input_enabled(False)
        tool_name = context or tr("unknown")
        prompt_text = tr("dialog_password_prompt", tool=tool_name)
        dlg = PasswordDialog(prompt_text, self)
        result = dlg.exec()
        password = dlg.password() if result == QDialog.Accepted else ""
        if password:
            self.runner.send_password(password)
            self.console.append_output(tr("msg_password_submitted") + "\n")
        else:
            self.runner.send_password("")
            self.console.append_output(tr("msg_password_cancelled") + "\n")

        self.console.set_input_enabled(True)

    def _set_src(self, src: str):
        self.current_source = src
        for b, name in [(self.btn_all, "Alle"), (self.btn_repo, "Repo"), (self.btn_aur, "AUR"), (self.btn_flatpak, "Flatpak")]:
            b.setChecked(name == src)
        self.model.set_source_filter(src)
        self._update_search_placeholder()

    def _on_installed_filter_changed(self, text: str):
        self.model.set_text_filter(text.strip())

    def refresh(self):
        if self._is_loading:
            self.console.append_output(tr("msg_loading") + "\n")
            return

        self._is_loading = True
        self.btn_refresh.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.loading_indicator.setFormat(tr("status_loading_packages"))
        self.loading_indicator.setVisible(True)

        self.console.append_output(tr("msg_package_list_loading") + "\n")
        self._refresh_thread = RefreshThread(self)
        self._refresh_thread.finished_with.connect(self._on_refresh_finished)
        self._refresh_thread.finished.connect(self._on_refresh_thread_end)
        self._refresh_thread.start()

    @Slot(list)
    def _on_refresh_finished(self, pkgs: List[PackageItem]):
        self.model.set_items(pkgs)
        self.console.append_output(tr("msg_loaded", len(pkgs)) + "\n\n")

    @Slot()
    def _on_refresh_thread_end(self):
        self._is_loading = False
        self.btn_refresh.setEnabled(True)
        QApplication.restoreOverrideCursor()
        self.loading_indicator.setVisible(False)
        if self._refresh_thread:
            self._refresh_thread.deleteLater()
            self._refresh_thread = None
        self._report_provider_errors()

    def _report_provider_errors(self):
        errors = providers.consume_errors()
        if not errors:
            return

        self.console.append_output(tr("msg_provider_errors_header") + "\n")
        for err in errors:
            cmd = err.get("command", "")
            message = err.get("message", "")
            details = err.get("stderr", "")

            if message.startswith("exit-code"):
                code = message.split(" ", 1)[1] if " " in message else message
                line = tr("msg_command_failed_exit", cmd, code)
            elif message == "not-found":
                line = tr("msg_command_failed_not_found", cmd)
            elif message.startswith("exception:"):
                line = tr("msg_command_failed_exception", cmd, message.split(":", 1)[1].strip())
            elif message.startswith("args-error"):
                info = message.split(":", 1)[1].strip() if ":" in message else ""
                line = tr("msg_command_failed_args", cmd, info)
            else:
                line = tr("msg_command_failed_generic", cmd, message)

            self.console.append_output(line + "\n")
            if details:
                self.console.append_output(details + "\n")

        self.console.append_output("\n")

    def _update_search_placeholder(self):
        if self.current_source == "Repo":
            self.search_edit.setPlaceholderText(tr("search_placeholder_repo"))
            self.search_info.setText(tr("search_info_repo"))
        elif self.current_source == "AUR":
            tool = settings.get_aur_helper()
            if tool:
                self.search_edit.setPlaceholderText(tr("search_placeholder_aur", tool))
                self.search_info.setText(tr("search_info_aur", tool))
            else:
                self.search_edit.setPlaceholderText(tr("search_placeholder_aur_no_helper"))
                self.search_info.setText(tr("search_info_aur_no_helper"))
        elif self.current_source == "Flatpak":
            self.search_edit.setPlaceholderText(tr("search_placeholder_flatpak"))
            self.search_info.setText(tr("search_info_flatpak"))
        else:
            self.search_edit.setPlaceholderText(tr("search_placeholder"))
            self.search_info.setText(tr("search_info_all"))

    def _ctx_menu_installed(self, pos):
        idx = self.table_installed.indexAt(pos)
        if not idx.isValid():
            return
        item = self.model.item_at(idx.row())

        menu = QMenu(self)
        act_details = menu.addAction(tr("ctx_show_details"))
        act_un = menu.addAction(tr("ctx_uninstall_item", item.name))

        act_details.triggered.connect(lambda: self._show_details_installed(item))
        act_un.triggered.connect(lambda: self._confirm_uninstall(item))

        menu.exec(self.table_installed.viewport().mapToGlobal(pos))

    def _ctx_menu_results(self, pos):
        idxs = self.results.selectionModel().selectedRows()
        if not idxs:
            return
        menu = QMenu(self)
        act_install = menu.addAction(tr("ctx_install"))
        act_queue = menu.addAction(tr("ctx_add_to_queue"))
        act_details = menu.addAction(tr("ctx_show_details"))

        act_install.triggered.connect(self._results_install_now)
        act_queue.triggered.connect(self._results_add_to_queue)
        act_details.triggered.connect(self._results_show_details)

        menu.exec(self.results.viewport().mapToGlobal(pos))

    def _confirm_uninstall(self, it: PackageItem):
        msg = tr("msg_uninstall_confirm", it.name)
        if it.source in ("Repo", "AUR"):
            flags = settings.get("pacman_uninstall_flags", "-Rns")
            msg += f"\n(pacman {flags})"
        elif it.source == "Flatpak":
            msg += "\n(flatpak uninstall --delete-data)"
        else:
            QMessageBox.warning(
                self, tr("dialog_unknown_source"),
                tr("msg_cannot_uninstall_source", it.source)
            )
            return

        if QMessageBox.question(self, tr("dialog_confirm"), msg) != QMessageBox.Yes:
            return
        self._do_uninstall(it)

    def _do_uninstall(self, it: PackageItem):
        if it.source in ("Repo", "AUR"):
            flags = settings.get_pacman_remove_flags()
            argv = ["pacman"] + flags + [it.pid]
            self.console.append_output(tr("msg_uninstalling_pacman", ' '.join(flags), it.pid) + "\n")

            root_cmd = settings.get_root_command()
            if root_cmd:
                self.runner.run(root_cmd + argv)
            else:
                self.console.append_output(tr("msg_no_root_method") + "\n")
        elif it.source == "Flatpak":
            scope = self._detect_flatpak_scope(it.pid)

            if scope == "user":
                argv = ["flatpak", "uninstall", "--user", "--delete-data", it.pid]
            elif scope == "system":
                argv = ["flatpak", "uninstall", "--system", "--delete-data", it.pid]
            else:
                argv = ["flatpak", "uninstall", "--delete-data", it.pid]

            self.console.append_output(tr("msg_uninstalling_flatpak", it.pid) + "\n")
            self.runner.run(argv)

    def _detect_flatpak_scope(self, app_id: str) -> str:
        """Ermittle, ob ein Flatpak als --user oder --system installiert ist."""

        try:
            result = subprocess.run(
                ["flatpak", "list", "--user", "--app", "--columns=application"],
                text=True,
                capture_output=True,
                check=False
            )
            if result.returncode == 0 and app_id in result.stdout:
                return "user"
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["flatpak", "list", "--system", "--app", "--columns=application"],
                text=True,
                capture_output=True,
                check=False
            )
            if result.returncode == 0 and app_id in result.stdout:
                return "system"
        except Exception:
            pass

        return settings.get("flatpak_default_scope", "user")

    def _on_search_clicked(self):
        term = self.search_edit.text().strip()
        if not term:
            return

        self.console.append_output(tr("msg_searching", self.current_source, term) + "\n")
        self.results.setRowCount(0)

        if self.current_source == "Repo":
            rows = self._search_pacman(term)
            self._fill_results(rows)
        elif self.current_source == "AUR":
            rows = self._search_aur(term)
            self._fill_results(rows)
        elif self.current_source == "Flatpak":
            rows = self._flatpak_search(term)
            self._fill_results(rows)
        else:
            combined: List[Dict[str, str]] = []
            combined.extend(self._search_pacman(term))
            combined.extend(self._search_aur(term))
            combined.extend(self._flatpak_search(term))
            self._fill_results(combined)

    def _fill_results(self, rows: List[Dict[str, str]]):
        self.results.setSortingEnabled(False)

        for r in rows:
            row = self.results.rowCount()
            self.results.insertRow(row)

            source = (r.get("source") or "").strip() or self.current_source
            if source in ("Repo", "AUR"):
                data_id = r.get("name", "")
                display = r.get("name", "")
                version = r.get("version", "")
                branch = r.get("repo", "")
                remote = r.get("repo", "")
                desc = r.get("description", "")
            elif source == "Flatpak":
                data_id = r.get("application", "")
                name = r.get("name", "") or data_id
                if name and name != data_id:
                    display = f"{name} ({data_id})"
                else:
                    display = data_id
                version = r.get("version", "")
                branch = r.get("branch", "")
                remote = r.get("remotes", "")
                desc = r.get("description", "")
            else:
                data_id = r.get("name", "") or r.get("application", "")
                display = data_id
                version = r.get("version", "")
                branch = r.get("branch", "") or r.get("repo", "")
                remote = r.get("remotes", "") or r.get("repo", "")
                desc = r.get("description", "")

            cells = [
                QTableWidgetItem(display),
                QTableWidgetItem(version),
                QTableWidgetItem(branch),
                QTableWidgetItem(remote),
                QTableWidgetItem(source),
                QTableWidgetItem(desc),
            ]
            cells[0].setData(Qt.UserRole, r)
            for col, cell in enumerate(cells):
                self.results.setItem(row, col, cell)

        self.results.setSortingEnabled(True)

    def _search_pacman(self, term: str) -> List[Dict[str, str]]:
        if not _which("pacman"):
            return []
        out = _check_output(["pacman", "-Ss", term])
        rows: List[Dict[str, str]] = []
        name = repo = version = desc = ""
        for ln in out.splitlines():
            if not ln.strip():
                continue
            m = re.match(r"^([a-z0-9\-+_.]+)/([^\s]+)\s+([^\s]+)\s*(.*)$", ln)
            if m:
                if name:
                    rows.append({
                        "name": name,
                        "repo": repo,
                        "version": version,
                        "description": desc.strip(),
                        "source": "Repo",
                    })
                repo, name, version, tail = m.groups()
                desc = tail.strip()
            else:
                if ln.startswith(" "):
                    desc += " " + ln.strip()
        if name:
            rows.append({
                "name": name,
                "repo": repo,
                "version": version,
                "description": desc.strip(),
                "source": "Repo",
            })
        return rows

    def _search_aur(self, term: str) -> List[Dict[str, str]]:
        import os, subprocess, re

        tool = settings.get_aur_helper()
        if not tool:
            self.console.append_output(tr("msg_no_aur_helper") + "\n")
            self.console.append_output(tr("msg_aur_helper_tip") + "\n")
            return []

        env = os.environ.copy()
        env["YAY_PAGER"] = "cat"
        env["PAGER"] = "cat"
        env["NO_COLOR"] = "1"
        env["LC_ALL"] = env.get("LC_ALL", "C")
        env["LANG"] = env.get("LANG", "C")

        try:
            out_names = subprocess.check_output(
                [tool, "-Ssq", "--aur", term],
                text=True, stderr=subprocess.DEVNULL, env=env
            )
        except Exception:
            out_names = ""

        names = [ln.strip() for ln in out_names.splitlines() if ln.strip()]
        if not names:
            try:
                out_raw = subprocess.check_output([tool, "-Ss", term], text=True, stderr=subprocess.DEVNULL, env=env)
            except Exception:
                out_raw = ""
            out_raw = re.sub(r"\x1b\[[0-9;]*m", "", out_raw)
            for ln in out_raw.splitlines():
                m = re.match(r"^aur/([^\s]+)\s", ln)
                if m:
                    names.append(m.group(1))
            names = list(dict.fromkeys(names))

        if not names:
            return []

        MAX_NAMES = 100
        names = names[:MAX_NAMES]

        rows: List[Dict[str, str]] = []
        for chunk in self._split_chunks(names, 25):
            try:
                out_info = subprocess.check_output(
                    [tool, "-Si", *chunk],
                    text=True, stderr=subprocess.DEVNULL, env=env
                )
            except Exception:
                continue
            rows.extend(self._parse_yay_si(out_info))

        for r in rows:
            r.setdefault("repo", "aur")
            r["repo"] = "aur"
            r.setdefault("source", "AUR")
        return rows

    def _split_chunks(self, seq, n):
        it = iter(seq)
        while True:
            chunk = list(itertools.islice(it, n))
            if not chunk:
                break
            yield chunk

    def _parse_yay_si(self, text: str) -> List[Dict[str, str]]:
        import re
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        blocks = re.split(r"\n{2,}", text.strip(), flags=re.M)

        results: List[Dict[str, str]] = []
        for blk in blocks:
            name = version = desc = ""
            for ln in blk.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if re.match(r"^(Name|Package\s*name)\s*:", ln, re.I):
                    name = ln.split(":", 1)[1].strip()
                elif re.match(r"^(Version)\s*:", ln, re.I):
                    version = ln.split(":", 1)[1].strip()
                elif re.match(r"^(Beschreibung|Description)\s*:", ln, re.I):
                    desc = ln.split(":", 1)[1].strip()
            if name:
                results.append({
                    "name": name,
                    "version": version,
                    "description": desc,
                    "repo": "aur",
                    "source": "AUR",
                })
        return results

    def _flatpak_search(self, term: str) -> List[Dict[str, str]]:
        if not _which("flatpak"):
            return []
        try:
            out = subprocess.check_output(
                ["flatpak", "search", "--columns=application,name,description,branch,remotes,version", term],
                text=True, stderr=subprocess.DEVNULL
            )
        except Exception:
            out = ""
        rows: List[Dict[str, str]] = []
        for ln in out.splitlines():
            parts = [p.strip() for p in ln.split("\t")]
            if len(parts) < 6 or parts[0].lower() == "application":
                continue
            application, name, description, branch, remotes, version = parts[:6]
            rows.append({
                "application": application,
                "name": name,
                "description": description,
                "branch": branch,
                "remotes": remotes,
                "version": version,
                "source": "Flatpak",
            })
        return rows

    def _show_details_installed(self, it: PackageItem):
        if it.source in ("Repo", "AUR"):
            info = _check_output(["pacman", "-Qi", it.pid])
            if not info:
                tool = settings.get_aur_helper()
                if tool:
                    info = _check_output([tool, "-Qi", it.pid])
        elif it.source == "Flatpak":
            info = _check_output(["flatpak", "info", it.pid])
        else:
            info = ""
        if not info:
            info = tr("msg_no_details", it.pid)
        self._show_text_dialog(tr("dialog_details_title", it.name), info)

    def _results_show_details(self):
        rows = [idx.row() for idx in self.results.selectionModel().selectedRows()]
        if not rows:
            return
        rdict = self.results.item(rows[0], 0).data(Qt.UserRole) or {}
        source = (rdict.get("source") or self.current_source).strip()
        if source == "Flatpak":
            appid = (rdict.get("application") or "").strip()
            info = _check_output(["flatpak", "info", appid]) if appid else ""
            title = tr("dialog_details_flatpak", appid or tr("unknown"))
        elif source == "Repo":
            name = (rdict.get("name") or "").strip()
            info = _check_output(["pacman", "-Si", name]) if name else ""
            title = tr("dialog_details_repo", name or tr("unknown"))
        elif source == "AUR":
            name = (rdict.get("name") or "").strip()
            tool = settings.get_aur_helper()
            if tool and name:
                info = _check_output([tool, "-Si", name])
            else:
                info = tr("msg_aur_details_need_helper")
            title = tr("dialog_details_aur", name or tr("unknown"))
        else:
            name = (rdict.get("name") or "").strip()
            info = _check_output(["pacman", "-Si", name]) if name else ""
            title = tr("dialog_details_repo", name or tr("unknown"))

        if not info:
            info = tr("msg_no_details_available")
        self._show_text_dialog(title, info)

    def _show_text_dialog(self, title: str, text: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(800, 600)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        dlg.exec()

    def _results_install_now(self):
        rows = [idx.row() for idx in self.results.selectionModel().selectedRows()]
        if not rows:
            return
        repo_names: List[str] = []
        aur_names: List[str] = []
        flatpak_rows: List[Dict[str, str]] = []

        for r in rows:
            data = self.results.item(r, 0).data(Qt.UserRole) or {}
            source = (data.get("source") or self.current_source).strip()
            if source == "Repo":
                nm = (data.get("name") or "").strip()
                if nm:
                    repo_names.append(nm)
            elif source == "AUR":
                nm = (data.get("name") or "").strip()
                if nm:
                    aur_names.append(nm)
            elif source == "Flatpak":
                flatpak_rows.append(data)

        commands: List[Sequence[str] | Dict[str, object]] = []

        if flatpak_rows:
            flatpak_cmds = self._prepare_flatpak_install_commands(flatpak_rows)
            if flatpak_cmds is None:
                return
            for message, argv, needs_root in flatpak_cmds:
                if message:
                    self.console.append_output(message + "\n")
                commands.append({"argv": argv, "needs_root": needs_root})

        if repo_names:
            self.console.append_output(tr("msg_installing_repo", ', '.join(repo_names)) + "\n")
            commands.append(["pacman", "-S", *repo_names])

        if aur_names:
            tool = settings.get_aur_helper()
            if not tool:
                QMessageBox.information(
                    self, tr("dialog_aur"),
                    tr("msg_no_aur_helper_configured")
                )
            else:
                self.console.append_output(tr("msg_installing_aur", tool, ', '.join(aur_names)) + "\n")
                commands.append([tool, "-S", *aur_names])

        if not commands:
            return

        self._run_cmds_sequential(commands, final_message="")

    def _results_add_to_queue(self):
        rows = [idx.row() for idx in self.results.selectionModel().selectedRows()]
        if not rows:
            return
        added = 0
        for r in rows:
            d = self.results.item(r, 0).data(Qt.UserRole) or {}
            source = (d.get("source") or self.current_source).strip()
            if source == "Flatpak":
                appid = (d.get("application") or "").strip()
                remotes = (d.get("remotes") or "").strip()
                preferred_remote = remotes.split(",")[0].strip() if remotes else ""
                if appid:
                    self._queue_add(("Flatpak", appid, {"remote": preferred_remote}))
                    added += 1
            elif source == "Repo":
                name = (d.get("name") or "").strip()
                repo = (d.get("repo") or "").strip()
                if name:
                    self._queue_add(("Repo", name, {"repo": repo}))
                    added += 1
            elif source == "AUR":
                name = (d.get("name") or "").strip()
                if name:
                    self._queue_add(("AUR", name, {}))
                    added += 1
        if added:
            self.console.append_output(tr("msg_added_to_queue", added) + "\n")

    def _queue_add(self, entry: Tuple[str, str, Dict[str, str]]):
        self.install_queue.append(entry)
        item = QListWidgetItem(self._queue_entry_label(entry))
        item.setData(Qt.UserRole, entry)
        icon = self.style().standardIcon(QStyle.SP_ArrowRight)
        item.setIcon(icon)
        self.queue_list.addItem(item)

    def _queue_entry_label(self, entry: Tuple[str, str, Dict[str, str]]) -> str:
        src, ident, meta = entry
        if src == "Flatpak":
            r = meta.get("remote") or ""
            return f"[Flatpak] {ident}  ({r or 'auto'})"
        elif src == "Repo":
            return f"[Repo] {ident}"
        else:
            return f"[AUR] {ident}"

    def _queue_install_all(self):
        if not self.install_queue:
            QMessageBox.information(self, tr("menu_queue"), tr("msg_queue_empty"))
            return
        flatpak_by_remote: Dict[str, List[str]] = {}
        repo_pkgs: List[str] = []
        aur_pkgs: List[str] = []
        for src, ident, meta in self.install_queue:
            if src == "Flatpak":
                remote = meta.get("remote") or ""
                flatpak_by_remote.setdefault(remote, []).append(ident)
            elif src == "Repo":
                repo_pkgs.append(ident)
            elif src == "AUR":
                aur_pkgs.append(ident)

        if flatpak_by_remote:
            self._flatpak_install_grouped(flatpak_by_remote)

        if repo_pkgs:
            self.console.append_output(tr("msg_installing_repo", ', '.join(repo_pkgs)) + "\n")
            argv = ["pacman", "-S"] + repo_pkgs
            root_cmd = settings.get_root_command()
            if root_cmd:
                self.runner.run(root_cmd + argv)
            else:
                self.console.append_output(tr("msg_no_root_method") + "\n")

        if aur_pkgs:
            tool = settings.get_aur_helper()
            if not tool:
                self.console.append_output(tr("msg_aur_no_helper_skip") + "\n")
            else:
                self.console.append_output(tr("msg_installing_aur", tool, ', '.join(aur_pkgs)) + "\n")
                self.runner.run([tool, "-S"] + aur_pkgs)

        self._queue_clear()

    def _queue_clear(self):
        self.install_queue.clear()
        self.queue_list.clear()

    def _queue_remove_selected(self):
        items = self.queue_list.selectedItems()
        if not items:
            return
        for it in items:
            entry = it.data(Qt.UserRole)
            if entry in self.install_queue:
                self.install_queue.remove(entry)
            idx = self.queue_list.row(it)
            self.queue_list.takeItem(idx)

    def _prepare_flatpak_install_commands(self, selected_rows: List[Dict[str, str]]) -> Optional[List[Tuple[str, List[str], bool]]]:
        scopes = self._flatpak_list_remotes()
        user_remotes = set(scopes["user"])
        system_remotes = set(scopes["system"])
        default_scope = settings.get("flatpak_default_scope", "user")

        to_install_by_remote: Dict[Optional[str], List[str]] = {}
        need_user_add: Set[str] = set()
        missing_remotes: Set[str] = set()

        for row in selected_rows:
            appid = (row.get("application") or "").strip()
            if not appid:
                continue
            remotes_field = (row.get("remotes") or "").strip()
            preferred_remote = None
            if remotes_field:
                preferred_remote = remotes_field.split(",")[0].strip()
                if preferred_remote in user_remotes:
                    if default_scope == "system" and preferred_remote not in system_remotes:
                        missing_remotes.add(preferred_remote)
                elif preferred_remote in system_remotes:
                    if default_scope == "user" and preferred_remote not in user_remotes:
                        need_user_add.add(preferred_remote)
                else:
                    missing_remotes.add(preferred_remote)
            to_install_by_remote.setdefault(preferred_remote, []).append(appid)

        if not to_install_by_remote:
            return []

        if not self._handle_flatpak_missing_remotes(missing_remotes, user_remotes, default_scope):
            return None

        if need_user_add:
            if QMessageBox.question(
                self, tr("dialog_add_remote_as_user"),
                tr("msg_remotes_system_only", ', '.join(sorted(need_user_add)))
            ) == QMessageBox.Yes:
                for r in sorted(need_user_add):
                    if r == "flathub":
                        self.console.append_output("$ flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo\n")
                        ok_add = self._exec_quiet(["flatpak", "remote-add", "--if-not-exists", "--user",
                                                   "flathub", "https://flathub.org/repo/flathub.flatpakrepo"])
                        if ok_add:
                            user_remotes.add("flathub")
                        else:
                            self.console.append_output(tr("msg_could_not_add_flathub") + "\n")
                            return None
                    else:
                        QMessageBox.information(
                            self, tr("dialog_remote_url_needed"),
                            tr("msg_remote_url_unknown", r)
                        )
            else:
                return None

        commands: List[Tuple[str, List[str], bool]] = []
        for remote, appids in to_install_by_remote.items():
            appids = [a for a in appids if a]
            if not appids:
                continue
            scope_flag = f"--{default_scope}"
            needs_root = default_scope == "system"
            if remote:
                if remote not in user_remotes and remote not in system_remotes:
                    self.console.append_output(tr("msg_remote_unknown_skip", remote, ', '.join(appids)) + "\n")
                    continue
                if default_scope == "user":
                    message = tr("msg_installing_flatpak_user", remote, ', '.join(appids))
                else:
                    message = tr("msg_installing_flatpak_system", remote, ', '.join(appids))
                argv = ["flatpak", "install", scope_flag, "-y", remote, *appids]
            else:
                if default_scope == "user":
                    message = tr("msg_installing_flatpak_user_auto", ', '.join(appids))
                else:
                    message = tr("msg_installing_flatpak_system_auto", ', '.join(appids))
                argv = ["flatpak", "install", scope_flag, "-y", *appids]
            commands.append((message, argv, needs_root))

        return commands

    def _flatpak_install_selection(self, selected_rows: List[Dict[str, str]]):
        commands = self._prepare_flatpak_install_commands(selected_rows)
        if commands is None:
            return
        if not commands:
            return

        seq: List[Dict[str, object]] = []
        for message, argv, needs_root in commands:
            if message:
                self.console.append_output(message + "\n")
            seq.append({"argv": argv, "needs_root": needs_root})

        self._run_cmds_sequential(seq, final_message="")

    def _flatpak_install_grouped(self, grouped: Dict[str, List[str]]):
        scopes = self._flatpak_list_remotes()
        user_remotes = scopes["user"]
        system_remotes = scopes["system"]
        default_scope = settings.get("flatpak_default_scope", "user")

        commands: List[Dict[str, object]] = []

        for remote, appids in grouped.items():
            appids = [a for a in appids if a]
            if not appids:
                continue

            scope_flag = f"--{default_scope}"
            needs_root = default_scope == "system"
            if remote:
                if remote not in user_remotes and remote not in system_remotes:
                    self.console.append_output(tr("msg_remote_unknown_skip", remote, ', '.join(appids)) + "\n")
                    continue
                if default_scope == "user":
                    self.console.append_output(tr("msg_installing_flatpak_user", remote, ', '.join(appids)) + "\n")
                else:
                    self.console.append_output(tr("msg_installing_flatpak_system", remote, ', '.join(appids)) + "\n")
                argv = ["flatpak", "install", scope_flag, "-y", remote] + appids
                commands.append({"argv": argv, "needs_root": needs_root})
            else:
                if default_scope == "user":
                    self.console.append_output(tr("msg_installing_flatpak_user_auto", ', '.join(appids)) + "\n")
                else:
                    self.console.append_output(tr("msg_installing_flatpak_system_auto", ', '.join(appids)) + "\n")
                argv = ["flatpak", "install", scope_flag, "-y"] + appids
                commands.append({"argv": argv, "needs_root": needs_root})

        if commands:
            self._run_cmds_sequential(commands, final_message="")

    def _handle_flatpak_missing_remotes(self, missing_remotes: Set[str], user_remotes: Set[str],
                                        default_scope: str) -> bool:
        if not missing_remotes:
            return True

        if default_scope == "system":
            QMessageBox.warning(
                self, tr("dialog_remotes_missing"),
                tr("msg_missing_remotes_setup", ", ".join(sorted(missing_remotes)))
            )
            return False

        if not settings.get("flatpak_auto_add_remotes", True):
            QMessageBox.warning(
                self, tr("dialog_remotes_missing"),
                tr("msg_missing_remotes_manual", ", ".join(sorted(missing_remotes)))
            )
            return False

        if missing_remotes == {"flathub"}:
            if QMessageBox.question(
                self, tr("dialog_remote_missing"),
                tr("msg_flathub_not_configured")
            ) == QMessageBox.Yes:
                self.console.append_output("$ flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo\n")
                ok_add = self._exec_quiet(["flatpak", "remote-add", "--if-not-exists", "--user",
                                           "flathub", "https://flathub.org/repo/flathub.flatpakrepo"])
                if ok_add:
                    try:
                        verify = subprocess.run(
                            ["flatpak", "remotes", "--user", "--columns=name"],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                    except FileNotFoundError:
                        verify = None
                    except Exception:
                        verify = None

                    if verify:
                        names = [line.strip() for line in verify.stdout.splitlines() if line.strip()]
                        if verify.returncode == 0 and "flathub" in names:
                            self.console.append_output(tr("msg_flathub_added") + "\n")
                            user_remotes.add("flathub")
                            missing_remotes.clear()
                            return True

                    self.console.append_output(tr("msg_flathub_verify_failed") + "\n")
                    return False
                else:
                    self.console.append_output(tr("msg_could_not_add_flathub") + "\n")
                    return False
            else:
                self.console.append_output(tr("msg_aborted_flathub_missing") + "\n")
                return False
        else:
            QMessageBox.warning(
                self, tr("dialog_remotes_missing"),
                tr("msg_missing_remotes_setup", ", ".join(sorted(missing_remotes)))
            )
            return False

    def _flatpak_list_remotes(self) -> dict:
        return {
            "user": self._flatpak_remotes_scope("--user"),
            "system": self._flatpak_remotes_scope("--system"),
        }

    def _flatpak_remotes_scope(self, scope_flag: str) -> Set[str]:
        try:
            out = subprocess.check_output(["flatpak", "remotes", scope_flag, "--columns=name"],
                                          text=True, stderr=subprocess.DEVNULL)
        except Exception:
            return set()
        names = {ln.strip() for ln in out.splitlines()
                 if ln.strip() and not ln.lower().startswith("name")}
        return names

    def _exec_quiet(self, argv: List[str]) -> bool:
        try:
            subprocess.check_call(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="WrapPac")
    parser.add_argument(
        "--show-updates",
        action="store_true",
        help="Open the update dialog after startup.",
    )
    parser.add_argument(
        "--tray-mode",
        action="store_true",
        help="Start minimized with only tray icon visible.",
    )
    parser.add_argument(
        "--run-update-service",
        action="store_true",
        help="Run the background update service and show a tray notification when updates are available.",
    )
    args, qt_args = parser.parse_known_args()

    if args.run_update_service:
        sys.exit(_run_update_service(qt_args))

    qt_argv = [sys.argv[0], *qt_args]

    app = QApplication(qt_argv)

    message = "show-updates" if args.show_updates else "show"
    if _notify_running_instance(SINGLE_INSTANCE_SERVER_NAME, message):
        return

    server = _create_single_instance_server(SINGLE_INSTANCE_SERVER_NAME)
    if server is None:
        QMessageBox.warning(None, tr("dialog_hint"), tr("single_instance_error"))
        return

    app.aboutToQuit.connect(server.close)
    app.aboutToQuit.connect(lambda: QLocalServer.removeServer(SINGLE_INSTANCE_SERVER_NAME))

    icon = _load_app_icon()
    if icon:
        app.setWindowIcon(icon)
    w = MainWindow(show_updates=args.show_updates, tray_mode=args.tray_mode)
    w.setup_single_instance_server(server)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
