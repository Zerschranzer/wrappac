import argparse
import sys
import shutil
import subprocess
import shlex
import re
import itertools
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple, Sequence, Iterable, Callable

from PySide6 import QtGui
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QIcon, QFontDatabase
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTableView, QMenu,
    QMessageBox, QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QSplitter, QStyle, QCheckBox, QProgressBar,
    QDialogButtonBox, QSystemTrayIcon, QPlainTextEdit, QTabWidget, QTextBrowser,
    QFileDialog, QCompleter
)

from models import PackageModel, PackageItem
import providers
from managed_terminal import ManagedTerminalWidget
from settings import settings
from settings_dialog import SettingsDialog
from cleanup_dialog import CleanupDialog
from i18n import tr
import update_service
from search_history import SearchHistory


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
        self._notification_tray: Optional[QSystemTrayIcon] = None
        self._explicit_packages: Optional[Set[str]] = None
        self._dependency_packages: Optional[Set[str]] = None
        self._orphan_packages: Optional[Set[str]] = None

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
        self.search_history = SearchHistory()
        self.search_completer = QCompleter(self.search_history.get_all())
        self.search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.search_completer.setFilterMode(Qt.MatchContains)
        self.search_edit.setCompleter(self.search_completer)
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

        self.console = ManagedTerminalWidget(self)
        self.console.contextMenuEvent = self._console_context_menu  # type: ignore[attr-defined]
        self.runner = self.console
        self._default_terminal_font_size = int(settings.DEFAULTS.get("terminal_font_size", 10))
        font_size = settings.get("terminal_font_size", 10)
        font = self.console.font
        font.setPointSize(font_size)
        self.console.font = font

        if settings.get("terminal_theme") == "dark":
            pass

        self._runner_finished_handler = lambda _code: self._schedule_refresh()
        self.runner.started.connect(lambda: None)
        self.runner.finished.connect(self._runner_finished_handler)

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

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(tr("filters") + ":"))

        self.filter_explicit = QCheckBox(tr("filter_explicit_only"))
        self.filter_deps = QCheckBox(tr("filter_show_deps"))
        self.filter_orphans = QCheckBox(tr("filter_orphans_only"))

        filter_row.addWidget(self.filter_explicit)
        filter_row.addWidget(self.filter_deps)
        filter_row.addWidget(self.filter_orphans)
        filter_row.addStretch()

        installed_layout.addLayout(filter_row)
        self.filter_explicit.toggled.connect(self._apply_advanced_filters)
        self.filter_deps.toggled.connect(self._apply_advanced_filters)
        self.filter_orphans.toggled.connect(self._apply_advanced_filters)
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

        self.statusbar = self.statusBar()
        self.status_label = QLabel()
        self.statusbar.addPermanentWidget(self.status_label)

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
        sc_clear.activated.connect(self.console.reset_terminal)

        shortcuts = [
            ("Ctrl+F", lambda: self.search_edit.setFocus()),
            ("Ctrl+U", self._system_update_dialog),
            ("Ctrl+R", self.refresh),
            ("F5", self.refresh),
            ("Escape", lambda: self.search_edit.clear() if self.search_edit.hasFocus() else None),
        ]

        for key, handler in shortcuts:
            QShortcut(QKeySequence(key), self).activated.connect(handler)

        font_shortcuts = [
            ("Ctrl++", lambda: self._adjust_terminal_font(1)),
            ("Ctrl+=", lambda: self._adjust_terminal_font(1)),
            ("Ctrl+-", lambda: self._adjust_terminal_font(-1)),
            ("Ctrl+0", self._reset_terminal_font),
        ]

        for key, handler in font_shortcuts:
            QShortcut(QKeySequence(key), self.console).activated.connect(handler)

    def _build_menu(self):
        m = self.menuBar().addMenu(tr("menu_actions"))
        act_refresh = QAction(tr("action_refresh"), self)
        act_refresh.triggered.connect(self.refresh)
        m.addAction(act_refresh)

        m_backup = self.menuBar().addMenu(tr("menu_backup"))
        act_export = QAction(tr("export_packages"), self)
        act_export.triggered.connect(self._export_package_list)
        m_backup.addAction(act_export)
        act_import = QAction(tr("import_packages"), self)
        act_import.triggered.connect(self._import_package_list)
        m_backup.addAction(act_import)

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

        act_stats = QAction(tr("menu_statistics"), self)
        act_stats.triggered.connect(self._show_statistics)
        m_h.addAction(act_stats)

        act_shortcuts = QAction(tr("menu_shortcuts"), self)
        act_shortcuts.triggered.connect(self._show_shortcuts_help)
        m_h.addAction(act_shortcuts)

    def setup_single_instance_server(self, server: QLocalServer) -> None:
        self._single_instance_server = server
        server.newConnection.connect(self._on_single_instance_connection)

    def _show_shortcuts_help(self) -> None:
        rows = [
            ("Ctrl+F", tr("shortcut_focus_search")),
            ("Ctrl+U", tr("shortcut_open_update")),
            ("Ctrl+R", tr("shortcut_refresh")),
            ("F5", tr("shortcut_refresh")),
            ("Escape", tr("shortcut_clear_search")),
            ("Ctrl+K", tr("shortcut_reset_terminal")),
            ("Ctrl+,", tr("shortcut_open_settings")),
        ]

        table_rows = [
            "<table style='width:100%; border-collapse:collapse;'>",
            f"<tr><th align='left'>{tr('shortcut_column_key')}</th><th align='left'>{tr('shortcut_column_action')}</th></tr>",
        ]
        for key, description in rows:
            table_rows.append(
                f"<tr><td style='padding:4px 8px;'><b>{key}</b></td><td style='padding:4px 8px;'>{description}</td></tr>"
            )
        table_rows.append("</table>")

        box = QMessageBox(self)
        box.setWindowTitle(tr("menu_shortcuts"))
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText("".join(table_rows))
        box.exec()

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
            if self._notification_tray:
                self._notification_tray.hide()
            event.accept()
            super().closeEvent(event)

    def _show_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._apply_settings()
            self.console.feed_text(tr("msg_settings_saved") + "\n")

    def _apply_settings(self):
        font_size = settings.get("terminal_font_size", 10)
        font = self.console.font
        font.setPointSize(font_size)
        self.console.font = font
        self.console.fm = QtGui.QFontMetrics(font)
        self.console.char_w = self.console.fm.horizontalAdvance("M")
        self.console.char_h = self.console.fm.height()

        self.btn_refresh.setText(tr("btn_refresh"))
        self.btn_system_update.setText(tr("btn_system_update"))
        self.btn_reflector.setText(tr("btn_run_reflector"))
        self.loading_indicator.setFormat(tr("status_loading_packages"))
        self._update_reflector_button_state()
        self.installed_search_edit.setPlaceholderText(tr("installed_filter_placeholder"))
        self._update_status_info()

    def _system_update_dialog(self):
        if self.runner.is_running():
            QMessageBox.information(
                self, tr("dialog_update_title"),
                tr("dialog_update_process_running")
            )
            return

        if self._update_thread and self._update_thread.isRunning():
            return

        self.console.feed_text(tr("msg_update_check_start") + "\n")
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
        total = pac + aur + flp
        if total > 0 and settings.get("notify_updates_available", True):
            summary = tr("update_service_tray_message", total)
            details = tr("update_service_tray_details", pac, aur, flp)
            self._show_notification(
                tr("update_service_tray_title"),
                f"{summary}\n{details}",
                QSystemTrayIcon.Information,
            )

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
                cmds.append(["flatpak", "remote-ls", "--updates", "--user"])
                cmds.append(["flatpak", "remote-ls", "--updates", "--system"])
            else:
                cmds.append(["flatpak", "update", "--user", "-y"])
                cmds.append({
                    "argv": ["flatpak", "update", "--system", "-y"],
                    "needs_root": True,
                })

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
        """Execute cleanup with single root authentication."""
        root_cmds: List[str] = []
        user_cmds: List[Dict[str, object]] = []

        self.console.feed_text(tr("msg_cleanup_start") + "\n")

        if selections.get("orphans"):
            if shutil.which("pacman"):
                message_no_orphans = tr("msg_cleanup_orphans_none")
                script = (
                    "orphans=$(pacman -Qtdq); "
                    "if [ -z \"$orphans\" ]; then "
                    f"echo {shlex.quote(message_no_orphans)}; "
                    "else pacman -Rns $orphans; fi"
                )
                root_cmds.append(f"bash -lc {shlex.quote(script)}")
            else:
                self.console.feed_text(tr("cleanup_skip_orphans_missing") + "\n")

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
                root_cmds.append(f"bash -lc {shlex.quote(script)}")
            else:
                self.console.feed_text(tr("cleanup_skip_cache_missing") + "\n")

        if selections.get("flatpak"):
            if shutil.which("flatpak"):
                user_cmds.append({
                    "argv": ["flatpak", "uninstall", "--user", "--unused", "-y"],
                    "needs_root": False,
                })
                if settings.get("flatpak_default_scope", "user") == "system":
                    root_cmds.append("flatpak uninstall --system --unused -y")
            else:
                self.console.feed_text(tr("cleanup_skip_flatpak_missing") + "\n")

        if selections.get("logs"):
            if shutil.which("journalctl"):
                days = max(1, int(settings.get("cleanup_log_max_age_days", 14)))
                root_cmds.append(f"journalctl --vacuum-time={days}d")
            else:
                self.console.feed_text(tr("cleanup_skip_logs_missing") + "\n")

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
            user_cmds.append({
                "argv": ["bash", "-lc", script],
                "needs_root": False,
            })

        cmds: List[Dict[str, object]] = []

        if root_cmds:
            root_method = settings.get_root_command()
            if not root_method:
                self.console.feed_text(tr("msg_no_root_method") + "\n")
            else:
                combined = " && ".join(root_cmds)
                cmds.append({
                    "argv": root_method + ["bash", "-lc", combined],
                    "needs_root": False,
                })

        cmds.extend(user_cmds)

        if not cmds:
            self.console.feed_text(tr("cleanup_no_action_possible") + "\n")
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

        message = final_message if final_message is not None else tr("msg_updates_complete")

        if not normalized:
            if message:
                self.console.feed_text("\n" + message + "\n")
            if on_done:
                try:
                    on_done(False)
                except Exception:
                    pass
            if schedule_refresh:
                self._schedule_refresh()
            return

        self._cmd_queue = list(normalized)
        completed_codes: List[int] = []

        try:
            self.runner.finished.disconnect(self._runner_finished_handler)
        except (RuntimeError, TypeError):
            # Signal was already disconnected or never connected - this is fine
            pass

        def _on_command_finished(exit_code: int) -> None:
            completed_codes.append(exit_code)
            _run_next()

        def _restore_default_handler():
            try:
                self.runner.finished.disconnect(_on_command_finished)
            except Exception:
                pass
            try:
                self.runner.finished.disconnect(self._runner_finished_handler)
            except Exception:
                pass
            self.runner.finished.connect(self._runner_finished_handler)

        def _finish_sequence():
            success = bool(completed_codes) and all(code == 0 for code in completed_codes)
            if message:
                self.console.feed_text("\n" + message + "\n")
            if on_done:
                try:
                    on_done(success)
                except Exception:
                    pass
            if success and settings.get("notify_install_complete", True):
                self._show_notification(
                    tr("notification_install_complete_title"),
                    tr("notification_install_complete_body"),
                    QSystemTrayIcon.Information,
                )
            elif not success and settings.get("notify_errors", True):
                code = completed_codes[-1] if completed_codes else -1
                body = f"{tr('notification_error_title')} (code {code})"
                self._show_notification(
                    tr("notification_error_title"),
                    body,
                    QSystemTrayIcon.Critical,
                )
            if schedule_refresh:
                self._schedule_refresh()
            _restore_default_handler()

        def _run_next():
            if not self._cmd_queue:
                _finish_sequence()
                return

            argv, needs_root = self._cmd_queue.pop(0)
            if needs_root:
                root_cmd = settings.get_root_command()
                if root_cmd:
                    argv = root_cmd + argv
                else:
                    self.console.feed_text(tr("msg_no_root_method") + "\n")
                    _run_next()
                    return

            self.runner.run(argv)

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
            self.console.feed_text(tr("msg_reflector_unavailable") + "\n")
            self._report_provider_errors()
            self._update_reflector_button_state()
            return

        self.console.feed_text(tr("msg_reflector_start") + "\n")
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

    def _send_sigint(self):
        self.runner.send_sigint()

    def _set_src(self, src: str):
        self.current_source = src
        for b, name in [(self.btn_all, "Alle"), (self.btn_repo, "Repo"), (self.btn_aur, "AUR"), (self.btn_flatpak, "Flatpak")]:
            b.setChecked(name == src)
        self.model.set_source_filter(src)
        self._update_search_placeholder()
        self._apply_advanced_filters()

    def _on_installed_filter_changed(self, text: str):
        self.model.set_text_filter(text.strip())
        self._apply_advanced_filters()

    def refresh(self):
        if self._is_loading:
            self.console.feed_text(tr("msg_loading") + "\n")
            return

        self._is_loading = True
        self.btn_refresh.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.loading_indicator.setFormat(tr("status_loading_packages"))
        self.loading_indicator.setVisible(True)

        self._refresh_thread = RefreshThread(self)
        self._refresh_thread.finished_with.connect(self._on_refresh_finished)
        self._refresh_thread.finished.connect(self._on_refresh_thread_end)
        self._refresh_thread.start()

    @Slot(list)
    def _on_refresh_finished(self, pkgs: List[PackageItem]):
        self.model.set_items(pkgs)
        self.console.feed_text(tr("msg_package_list_loading") + "\n")
        self.console.feed_text(tr("msg_loaded", len(pkgs)) + "\n")
        self._explicit_packages = None
        self._dependency_packages = None
        self._orphan_packages = None
        self._update_status_info()
        self._apply_advanced_filters()

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

        self.console.feed_text(tr("msg_provider_errors_header") + "\n")
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

            self.console.feed_text(line + "\n")
            if details:
                self.console.feed_text(details + "\n")

        self.console.feed_text("\n")
        if settings.get("notify_errors", True) and errors:
            first = errors[0]
            body = f"{first.get('command', '')}: {first.get('message', '')}".strip()
            if not body:
                body = tr("notification_error_title")
            self._show_notification(
                tr("notification_error_title"),
                body,
                QSystemTrayIcon.Critical,
            )

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

    def _update_status_info(self):
        if not hasattr(self, "status_label"):
            return

        total = self.model.total_count()
        filtered = self.model.filtered_count()
        if hasattr(self, "table_installed"):
            visible = 0
            for row in range(self.model.rowCount()):
                if not self.table_installed.isRowHidden(row):
                    visible += 1
            filtered = visible
        helper = settings.get_aur_helper() or "-"
        root_cmd = settings.get_root_command()
        if root_cmd:
            root_display = " ".join(root_cmd)
        else:
            configured = settings.get("root_method", "")
            root_display = configured if configured else "-"

        status_text = (
            f"{total} {tr('packages')} ({filtered} {tr('filtered')}) "
            f"| AUR: {helper} | Root: {root_display}"
        )
        self.status_label.setText(status_text)

    def _apply_advanced_filters(self):
        if not hasattr(self, "table_installed"):
            return

        active_filters = [
            self.filter_explicit.isChecked(),
            self.filter_deps.isChecked(),
            self.filter_orphans.isChecked(),
        ]

        if not any(active_filters):
            for row in range(self.model.rowCount()):
                self.table_installed.setRowHidden(row, False)
            self._update_status_info()
            return

        if self.filter_explicit.isChecked() and self._explicit_packages is None:
            self._explicit_packages = providers.get_explicit_packages()
        if self.filter_deps.isChecked() and self._dependency_packages is None:
            self._dependency_packages = providers.get_dependency_packages()
        if self.filter_orphans.isChecked() and self._orphan_packages is None:
            self._orphan_packages = providers.get_orphaned_packages()

        explicit = self._explicit_packages or set()
        deps = self._dependency_packages or set()
        orphans = self._orphan_packages or set()

        for row in range(self.model.rowCount()):
            item = self.model.item_at(row)
            show = True
            if self.filter_explicit.isChecked():
                show = item.pid in explicit
            if show and self.filter_deps.isChecked():
                show = item.pid in deps
            if show and self.filter_orphans.isChecked():
                show = item.pid in orphans
            self.table_installed.setRowHidden(row, not show)

        self._update_status_info()

    def _calculate_statistics(self) -> Dict[str, object]:
        items = self.model.all_items()
        repo = sum(1 for it in items if it.source == "Repo")
        aur = sum(1 for it in items if it.source == "AUR")
        flatpak = sum(1 for it in items if it.source == "Flatpak")
        size_bytes = sum(
            PackageModel._size_to_bytes(it.size)
            for it in items
            if it.size and it.size != "?"
        )
        size_str = self._format_human_size(size_bytes) if size_bytes > 0 else "N/A"
        return {
            "total": len(items),
            "repo": repo,
            "aur": aur,
            "flatpak": flatpak,
            "size": size_str,
        }

    @staticmethod
    def _format_human_size(num: float) -> str:
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        value = num
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{num:.1f} B"

    def _show_statistics(self) -> None:
        stats = self._calculate_statistics()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("package_statistics"))
        dlg.resize(420, 320)

        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        rows = [
            (tr("total_packages"), stats["total"]),
            (tr("from_repos"), stats["repo"]),
            (tr("from_aur"), stats["aur"]),
            (tr("from_flatpak"), stats["flatpak"]),
            (tr("total_size"), stats["size"]),
        ]
        html = [f"<h2>{tr('package_statistics')}</h2>", "<table style='width:100%; border-collapse:collapse;'>"]
        for label, value in rows:
            html.append(
                f"<tr><td style='padding:4px 8px;'><b>{label}</b></td><td style='padding:4px 8px;'>{value}</td></tr>"
            )
        html.append("</table>")
        browser.setHtml("".join(html))
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    def _reset_terminal_font(self) -> None:
        current = self.console.font.pointSize()
        delta = self._default_terminal_font_size - current
        if delta:
            self._adjust_terminal_font(delta)

    def _adjust_terminal_font(self, delta: int):
        """Adjust the terminal font size by delta and persist the change."""

        current = self.console.font.pointSize()
        new_size = max(6, min(24, current + delta))
        if new_size == current:
            return

        font = self.console.font
        font.setPointSize(new_size)
        self.console.font = font
        self.console.fm = QtGui.QFontMetrics(font)
        self.console.char_w = self.console.fm.horizontalAdvance("M")
        self.console.char_h = self.console.fm.height()
        self.console.viewport().update()
        settings.set("terminal_font_size", new_size)
        settings.save()

    def _console_context_menu(self, event):
        menu = QMenu(self.console)
        act_copy = menu.addAction(tr("terminal_copy"))
        act_paste = menu.addAction(tr("terminal_paste"))
        act_reset = menu.addAction(tr("terminal_reset"))
        menu.addSeparator()
        act_inc = menu.addAction(tr("increase_font"))
        act_dec = menu.addAction(tr("decrease_font"))
        act_reset_font = menu.addAction(tr("reset_font"))

        chosen = menu.exec(event.globalPos()) if event else None

        if chosen == act_copy:
            self.console.copy_selection()
        elif chosen == act_paste:
            self.console.paste_from_clipboard()
        elif chosen == act_reset:
            self.console.reset_terminal()
        elif chosen == act_inc:
            self._adjust_terminal_font(1)
        elif chosen == act_dec:
            self._adjust_terminal_font(-1)
        elif chosen == act_reset_font:
            self._reset_terminal_font()

    def _ensure_notification_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        if self._notification_tray is None:
            icon = self.windowIcon()
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.SP_MessageBoxInformation)
            tray = QSystemTrayIcon(icon, self)
            tray.setVisible(True)
            self._notification_tray = tray
        return self._notification_tray

    def _show_notification(
        self,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information,
        timeout_ms: int = 8000,
    ) -> None:
        tray = self._ensure_notification_tray()
        if not tray:
            return
        tray.show()
        tray.showMessage(title, message, icon, timeout_ms)
        QTimer.singleShot(timeout_ms + 500, tray.hide)

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
            self.console.feed_text(tr("msg_uninstalling_pacman", ' '.join(flags), it.pid) + "\n")

            root_cmd = settings.get_root_command()
            if root_cmd:
                self.runner.run(root_cmd + argv)
            else:
                self.console.feed_text(tr("msg_no_root_method") + "\n")
        elif it.source == "Flatpak":
            scope = self._detect_flatpak_scope(it.pid)

            if scope == "user":
                argv = ["flatpak", "uninstall", "--user", "--delete-data", it.pid]
            elif scope == "system":
                argv = ["flatpak", "uninstall", "--system", "--delete-data", it.pid]
            else:
                argv = ["flatpak", "uninstall", "--delete-data", it.pid]

            self.console.feed_text(tr("msg_uninstalling_flatpak", it.pid) + "\n")
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

        self.console.feed_text(tr("msg_searching", self.current_source, term) + "\n")
        self.search_history.add(term)
        model = self.search_completer.model()
        if hasattr(model, "setStringList"):
            model.setStringList(self.search_history.get_all())
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
            self.console.feed_text(tr("msg_no_aur_helper") + "\n")
            self.console.feed_text(tr("msg_aur_helper_tip") + "\n")
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

    def _format_package_info(self, text: str) -> str:
        """Convert plain package information text into styled HTML."""

        url_pattern = re.compile(r'(https?://[^\s<>"]+)')

        def linkify(segment: str) -> str:
            result: List[str] = []
            last = 0
            for match in url_pattern.finditer(segment):
                result.append(escape(segment[last:match.start()]))
                url = match.group(0)
                safe_url = escape(url)
                result.append(f'<a href="{safe_url}">{safe_url}</a>')
                last = match.end()
            result.append(escape(segment[last:]))
            return "".join(result)

        tokens: List[Tuple[str, Optional[Tuple[str, str]]]] = []
        for raw in text.splitlines():
            if not raw.strip():
                tokens.append(("break", None))
                continue

            if ":" in raw:
                key, value = raw.split(":", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    tokens.append(("kv", (key, value)))
                    continue

            tokens.append(("text", (raw.strip(), "")))

        style = (
            "<style>"
            "body{font-family:'Noto Sans',sans-serif;font-size:13px;color:#222;}"
            "dl{margin:0;}"
            "dt{color:#1793D1;font-weight:bold;margin-top:6px;}"
            "dd{margin:0 0 6px 14px;}"
            "p{margin:6px 0;}"
            "</style>"
        )

        html_parts: List[str] = ["<html><head>", style, "</head><body>"]
        in_dl = False

        for kind, payload in tokens:
            if kind == "kv" and payload:
                if not in_dl:
                    html_parts.append("<dl>")
                    in_dl = True
                key, value = payload
                key_html = escape(key)
                value_html = linkify(value)
                if not value_html:
                    value_html = "&nbsp;"
                html_parts.append(f"<dt>{key_html}</dt><dd>{value_html}</dd>")
            elif kind == "break":
                if in_dl:
                    html_parts.append("</dl>")
                    in_dl = False
                html_parts.append("<p>&nbsp;</p>")
            else:
                if in_dl:
                    html_parts.append("</dl>")
                    in_dl = False
                if payload:
                    text_segment = payload[0]
                else:
                    text_segment = ""
                html_parts.append(f"<p>{linkify(text_segment)}</p>")

        if in_dl:
            html_parts.append("</dl>")

        html_parts.append("</body></html>")
        return "".join(html_parts)

    def _show_text_dialog(self, title: str, text: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(900, 620)

        tabs = QTabWidget(dlg)

        formatted = QTextBrowser()
        formatted.setOpenExternalLinks(True)
        formatted.setHtml(self._format_package_info(text))
        tabs.addTab(formatted, tr("tab_formatted"))

        raw_view = QPlainTextEdit()
        raw_view.setReadOnly(True)
        raw_view.setPlainText(text)
        mono_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        raw_view.setFont(mono_font)
        tabs.addTab(raw_view, tr("tab_raw"))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        copy_btn = buttons.addButton(tr("btn_copy_all"), QDialogButtonBox.ActionRole)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text))

        layout = QVBoxLayout(dlg)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        dlg.exec()

    def _export_package_list(self):
        """Export the installed packages into a JSON snapshot."""

        default_name = f"wrappac_backup_{datetime.now():%Y%m%d}.json"
        default_path = str(Path.home() / default_name)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            tr("export_packages"),
            default_path,
            "JSON Files (*.json);;All Files (*)",
        )
        if not filename:
            return

        items = self.model.all_items()
        pacman_pkgs = sorted({it.pid for it in items if it.source == "Repo"})
        aur_pkgs = sorted({it.pid for it in items if it.source == "AUR"})
        flatpak_entries = [
            {
                "id": it.pid,
                "remote": getattr(it, "origin", ""),
            }
            for it in items
            if it.source == "Flatpak"
        ]

        payload = {
            "export_date": datetime.now().isoformat(timespec="seconds"),
            "pacman": pacman_pkgs,
            "aur": aur_pkgs,
            "flatpak": flatpak_entries,
        }

        try:
            with open(filename, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.warning(self, tr("dialog_hint"), str(exc))
            return

        self.console.feed_text(tr("export_packages") + f": {filename}\n")

    def _import_package_list(self):
        """Load a previously exported package list and offer installations."""

        filename, _ = QFileDialog.getOpenFileName(
            self,
            tr("import_packages"),
            str(Path.home()),
            "JSON Files (*.json);;All Files (*)",
        )
        if not filename:
            return

        try:
            with open(filename, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            QMessageBox.warning(self, tr("dialog_hint"), str(exc))
            return

        pacman_list = [
            str(entry).strip()
            for entry in data.get("pacman", [])
            if isinstance(entry, str) and str(entry).strip()
        ]
        aur_list = [
            str(entry).strip()
            for entry in data.get("aur", [])
            if isinstance(entry, str) and str(entry).strip()
        ]
        flatpak_list: List[Tuple[str, str]] = []
        for entry in data.get("flatpak", []):
            if isinstance(entry, str):
                ident = entry.strip()
                if ident:
                    flatpak_list.append((ident, ""))
            elif isinstance(entry, dict):
                ident = str(entry.get("id") or entry.get("application") or entry.get("name") or "").strip()
                remote = str(entry.get("remote") or entry.get("origin") or "").strip()
                if ident:
                    flatpak_list.append((ident, remote))

        items = self.model.all_items()
        installed_pacman = {it.pid for it in items if it.source == "Repo"}
        installed_aur = {it.pid for it in items if it.source == "AUR"}
        installed_flatpak = {it.pid for it in items if it.source == "Flatpak"}

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("import_packages"))
        dlg.resize(540, 420)

        layout = QVBoxLayout(dlg)
        export_date = data.get("export_date", "")
        label_text = tr("will_be_installed")
        if export_date:
            label_text = f"{label_text} – {export_date}"
        info = QLabel(label_text)
        info.setWordWrap(True)
        layout.addWidget(info)

        tabs = QTabWidget(dlg)
        layout.addWidget(tabs, 1)

        installed_brush = QtGui.QBrush(QtGui.QColor("#C8E6C9"))
        lists: Dict[str, QListWidget] = {}

        def _create_list(entries: List, installed_set: Set[str], formatter) -> QListWidget:
            widget = QListWidget()
            widget.setSelectionMode(QListWidget.ExtendedSelection)
            for entry in entries:
                display_text, value = formatter(entry)
                if not value:
                    continue
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, value)
                is_installed = value in installed_set if isinstance(value, str) else value[0] in installed_set
                item.setData(Qt.UserRole + 1, is_installed)
                if is_installed:
                    item.setBackground(installed_brush)
                    item.setToolTip(tr("already_installed"))
                else:
                    item.setToolTip(tr("will_be_installed"))
                widget.addItem(item)
            return widget

        lists["pacman"] = _create_list(
            pacman_list,
            installed_pacman,
            lambda name: (name, name),
        )
        tabs.addTab(lists["pacman"], tr("btn_official"))

        lists["aur"] = _create_list(
            aur_list,
            installed_aur,
            lambda name: (name, name),
        )
        tabs.addTab(lists["aur"], tr("btn_aur"))

        def _format_flatpak(entry: Tuple[str, str]) -> Tuple[str, Tuple[str, str]]:
            ident, remote = entry
            label = ident if not remote else f"{ident} ({remote})"
            return label, (ident, remote)

        lists["flatpak"] = _create_list(
            flatpak_list,
            installed_flatpak,
            _format_flatpak,
        )
        tabs.addTab(lists["flatpak"], tr("btn_flatpak"))

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        btn_install_all = buttons.addButton(tr("install_all"), QDialogButtonBox.ActionRole)
        btn_install_sel = buttons.addButton(tr("install_selected"), QDialogButtonBox.ActionRole)
        layout.addWidget(buttons)

        buttons.rejected.connect(dlg.reject)

        def _collect_selection(all_items: bool) -> Dict[str, List]:
            collected = {"pacman": [], "aur": [], "flatpak": []}
            for key, widget in lists.items():
                if all_items:
                    items_iter = [widget.item(i) for i in range(widget.count())]
                else:
                    items_iter = widget.selectedItems()
                for list_item in items_iter:
                    if not list_item:
                        continue
                    if list_item.data(Qt.UserRole + 1):
                        continue
                    value = list_item.data(Qt.UserRole)
                    if not value:
                        continue
                    collected[key].append(value)
            return collected

        def _perform_install(all_items: bool):
            selection = _collect_selection(all_items)
            if not any(selection.values()):
                QMessageBox.information(self, tr("dialog_hint"), tr("already_installed"))
                return

            summary_lines = []
            if selection["pacman"]:
                summary_lines.append("pacman: " + ", ".join(selection["pacman"]))
            if selection["aur"]:
                summary_lines.append("AUR: " + ", ".join(selection["aur"]))
            if selection["flatpak"]:
                ids = [pkg if isinstance(pkg, str) else pkg[0] for pkg in selection["flatpak"]]
                summary_lines.append("Flatpak: " + ", ".join(ids))

            confirm_text = tr("will_be_installed") + ":\n" + "\n".join(summary_lines)
            if QMessageBox.question(self, tr("dialog_confirm"), confirm_text) != QMessageBox.Yes:
                return

            cmds: List[Sequence[str] | Dict[str, object]] = []

            if selection["pacman"]:
                cmds.append(["pacman", "-S", *selection["pacman"]])

            if selection["aur"]:
                tool = settings.get_aur_helper()
                if not tool:
                    QMessageBox.warning(self, tr("dialog_hint"), tr("msg_no_aur_helper_configured"))
                else:
                    cmds.append([tool, "-S", *selection["aur"]])

            if selection["flatpak"]:
                rows = []
                for value in selection["flatpak"]:
                    if isinstance(value, tuple):
                        ident, remote = value
                    else:
                        ident, remote = value, ""
                    rows.append({"application": ident, "remotes": remote, "source": "Flatpak"})
                flatpak_cmds = self._prepare_flatpak_install_commands(rows)
                if flatpak_cmds is None:
                    return
                for message, argv, needs_root in flatpak_cmds:
                    if message:
                        self.console.feed_text(message + "\n")
                    cmds.append({"argv": argv, "needs_root": needs_root})

            if not cmds:
                QMessageBox.information(self, tr("dialog_hint"), tr("already_installed"))
                return

            dlg.accept()
            self._run_cmds_sequential(cmds)

        btn_install_all.clicked.connect(lambda: _perform_install(True))
        btn_install_sel.clicked.connect(lambda: _perform_install(False))

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
                    self.console.feed_text(message + "\n")
                commands.append({"argv": argv, "needs_root": needs_root})

        if repo_names:
            self.console.feed_text(tr("msg_installing_repo", ', '.join(repo_names)) + "\n")
            commands.append(["pacman", "-S", *repo_names])

        if aur_names:
            tool = settings.get_aur_helper()
            if not tool:
                QMessageBox.information(
                    self, tr("dialog_aur"),
                    tr("msg_no_aur_helper_configured")
                )
            else:
                self.console.feed_text(tr("msg_installing_aur", tool, ', '.join(aur_names)) + "\n")
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
            self.console.feed_text(tr("msg_added_to_queue", added) + "\n")

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
            self.console.feed_text(tr("msg_installing_repo", ', '.join(repo_pkgs)) + "\n")
            argv = ["pacman", "-S"] + repo_pkgs
            root_cmd = settings.get_root_command()
            if root_cmd:
                self.runner.run(root_cmd + argv)
            else:
                self.console.feed_text(tr("msg_no_root_method") + "\n")

        if aur_pkgs:
            tool = settings.get_aur_helper()
            if not tool:
                self.console.feed_text(tr("msg_aur_no_helper_skip") + "\n")
            else:
                self.console.feed_text(tr("msg_installing_aur", tool, ', '.join(aur_pkgs)) + "\n")
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
                        self.console.feed_text("$ flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo\n")
                        ok_add = self._exec_quiet(["flatpak", "remote-add", "--if-not-exists", "--user",
                                                   "flathub", "https://flathub.org/repo/flathub.flatpakrepo"])
                        if ok_add:
                            user_remotes.add("flathub")
                        else:
                            self.console.feed_text(tr("msg_could_not_add_flathub") + "\n")
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
                    self.console.feed_text(tr("msg_remote_unknown_skip", remote, ', '.join(appids)) + "\n")
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
                self.console.feed_text(message + "\n")
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
                    self.console.feed_text(tr("msg_remote_unknown_skip", remote, ', '.join(appids)) + "\n")
                    continue
                if default_scope == "user":
                    self.console.feed_text(tr("msg_installing_flatpak_user", remote, ', '.join(appids)) + "\n")
                else:
                    self.console.feed_text(tr("msg_installing_flatpak_system", remote, ', '.join(appids)) + "\n")
                argv = ["flatpak", "install", scope_flag, "-y", remote] + appids
                commands.append({"argv": argv, "needs_root": needs_root})
            else:
                if default_scope == "user":
                    self.console.feed_text(tr("msg_installing_flatpak_user_auto", ', '.join(appids)) + "\n")
                else:
                    self.console.feed_text(tr("msg_installing_flatpak_system_auto", ', '.join(appids)) + "\n")
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
                self.console.feed_text("$ flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo\n")
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
                            self.console.feed_text(tr("msg_flathub_added") + "\n")
                            user_remotes.add("flathub")
                            missing_remotes.clear()
                            return True

                    self.console.feed_text(tr("msg_flathub_verify_failed") + "\n")
                    return False
                else:
                    self.console.feed_text(tr("msg_could_not_add_flathub") + "\n")
                    return False
            else:
                self.console.feed_text(tr("msg_aborted_flathub_missing") + "\n")
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
