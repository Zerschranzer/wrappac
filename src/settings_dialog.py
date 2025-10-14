"""
Settings dialog for WrapPac focusing on the five main categories.
"""

import locale
import shlex
import shutil
import subprocess
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QPushButton, QLabel, QComboBox, QSpinBox, QCheckBox,
    QRadioButton, QButtonGroup, QGroupBox, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialogButtonBox, QFormLayout, QTimeEdit
)
from PySide6.QtCore import Qt, QTime

from settings import settings
import update_service
from i18n import tr


class FlatpakRemoteDialog(QDialog):
    """Dialog to capture parameters for a new Flatpak remote."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_flatpak_remote_add_title"))
        self.resize(420, 0)

        self._result = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.url_edit = QLineEdit()
        self.scope_combo = QComboBox()
        self.scope_combo.addItems([
            tr("settings_flatpak_remote_scope_user"),
            tr("settings_flatpak_remote_scope_system"),
        ])

        form.addRow(tr("settings_flatpak_remote_name"), self.name_edit)
        form.addRow(tr("settings_flatpak_remote_url"), self.url_edit)
        form.addRow(tr("settings_flatpak_remote_scope"), self.scope_combo)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #cc6600; margin-left: 4px;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url:
            self.error_label.setText(tr("settings_flatpak_remote_required"))
            return

        scope = "user" if self.scope_combo.currentIndex() == 0 else "system"
        self._result = (scope, name, url)
        self.accept()

    @property
    def result(self):
        return self._result


class SettingsDialog(QDialog):
    """Settings dialog with five categories."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_dialog_title"))
        self.resize(620, 480)

        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()
        tabs.addTab(self._build_aur_tab(), tr("settings_tab_aur"))
        tabs.addTab(self._build_root_tab(), tr("settings_tab_root"))
        tabs.addTab(self._build_pacman_tab(), tr("settings_tab_pacman"))
        tabs.addTab(self._build_reflector_tab(), tr("settings_tab_reflector"))
        tabs.addTab(self._build_flatpak_tab(), tr("settings_tab_flatpak"))
        tabs.addTab(self._build_cleanup_tab(), tr("settings_tab_cleanup"))
        tabs.addTab(self._build_update_service_tab(), tr("settings_tab_update_service"))
        tabs.addTab(self._build_language_tab(), tr("settings_tab_language"))

        layout.addWidget(tabs)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_reset = QPushButton(tr("settings_btn_reset"))
        self.btn_cancel = QPushButton(tr("btn_cancel"))
        self.btn_save = QPushButton(tr("settings_btn_save"))

        self.btn_reset.clicked.connect(self._reset_defaults)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save_and_close)

        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

    # ===== TAB 1: AUR helper =====
    def _build_aur_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_aur_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        # Radio buttons
        self.aur_auto = QRadioButton(tr("settings_aur_auto"))
        self.aur_yay = QRadioButton(tr("settings_aur_yay"))
        self.aur_paru = QRadioButton(tr("settings_aur_paru"))
        self.aur_pikaur = QRadioButton(tr("settings_aur_pikaur"))
        self.aur_custom = QRadioButton(tr("settings_aur_custom"))

        self.aur_group = QButtonGroup()
        for btn in (self.aur_auto, self.aur_yay, self.aur_paru, self.aur_pikaur, self.aur_custom):
            self.aur_group.addButton(btn)
            layout.addWidget(btn)

        # Custom path
        custom_row = QHBoxLayout()
        custom_row.addSpacing(30)
        self.aur_custom_path = QLineEdit()
        self.aur_custom_path.setPlaceholderText("/usr/bin/pikaur")
        custom_row.addWidget(self.aur_custom_path)
        layout.addLayout(custom_row)

        # Status info
        layout.addSpacing(20)
        status_label = QLabel(f"<b>{tr('settings_aur_detected')}:</b>")
        layout.addWidget(status_label)

        detected = []
        for cmd in ["yay", "paru", "pikaur"]:
            if shutil.which(cmd):
                detected.append(f"✓ {cmd} ({shutil.which(cmd)})")

        if detected:
            for d in detected:
                lbl = QLabel(d)
                lbl.setStyleSheet("color: green; margin-left: 20px;")
                layout.addWidget(lbl)
        else:
            lbl = QLabel(tr("settings_aur_none_found"))
            lbl.setStyleSheet("color: #cc6600; margin-left: 20px;")
            layout.addWidget(lbl)

            hint = QLabel(tr("settings_aur_install_tip"))
            hint.setWordWrap(True)
            hint.setStyleSheet("color: gray; font-size: 9pt; margin: 10px 20px;")
            layout.addWidget(hint)

        layout.addStretch()
        return widget

    # ===== TAB 2: Root method =====
    def _build_root_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_root_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        # Radio buttons
        self.root_auto = QRadioButton(tr("settings_root_auto"))
        self.root_sudo = QRadioButton(tr("settings_root_sudo"))
        self.root_doas = QRadioButton(tr("settings_root_doas"))

        self.root_group = QButtonGroup()
        for btn in (self.root_auto, self.root_sudo, self.root_doas):
            self.root_group.addButton(btn)
            layout.addWidget(btn)

        # Status info
        layout.addSpacing(20)
        status_label = QLabel(f"<b>{tr('settings_root_available')}:</b>")
        layout.addWidget(status_label)

        for cmd, desc in [("sudo", "sudo"), ("doas", "doas")]:
            if shutil.which(cmd):
                lbl = QLabel(f"✓ {desc} ({shutil.which(cmd)})")
                lbl.setStyleSheet("color: green; margin-left: 20px;")
            else:
                lbl = QLabel(tr("settings_root_not_installed", desc))
                lbl.setStyleSheet("color: gray; margin-left: 20px;")
            layout.addWidget(lbl)

        # Security notice – now styled as warning
        layout.addSpacing(20)
        security_note = QLabel(tr("settings_root_security_note"))
        security_note.setWordWrap(True)
        security_note.setStyleSheet(
            "background-color: #fff3cd; padding: 10px; border-radius: 5px; "
            "color: #856404; font-weight: bold;"
        )
        layout.addWidget(security_note)

        layout.addStretch()
        return widget

    # ===== TAB 3: Pacman options =====
    def _build_pacman_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_pacman_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        # Uninstall flags
        grp_uninstall = QGroupBox(tr("settings_pacman_uninstall_group"))
        grp_layout = QVBoxLayout()

        flags_label = QLabel(f"<b>{tr('settings_pacman_flags_label')}:</b>")
        grp_layout.addWidget(flags_label)

        self.pacman_flags = QComboBox()
        self.pacman_flags.addItems([
            tr("settings_pacman_flags_rns"),
            tr("settings_pacman_flags_rn"),
            tr("settings_pacman_flags_rs"),
            tr("settings_pacman_flags_r")
        ])
        grp_layout.addWidget(self.pacman_flags)

        grp_layout.addSpacing(10)

        self.pacman_show_deps = QCheckBox(tr("settings_pacman_show_deps"))
        self.pacman_show_deps.setChecked(True)
        grp_layout.addWidget(self.pacman_show_deps)

        grp_uninstall.setLayout(grp_layout)
        layout.addWidget(grp_uninstall)

        # Update options
        grp_update = QGroupBox(tr("settings_pacman_update_group"))
        grp_update_layout = QVBoxLayout()

        self.pacman_noconfirm = QCheckBox(tr("settings_pacman_noconfirm"))
        self.pacman_noconfirm.setStyleSheet("font-weight: bold;")
        grp_update_layout.addWidget(self.pacman_noconfirm)

        hint = QLabel(tr("settings_pacman_noconfirm_hint"))
        hint.setStyleSheet("color: gray; font-size: 9pt; margin-left: 25px;")
        grp_update_layout.addWidget(hint)

        grp_update.setLayout(grp_update_layout)
        layout.addWidget(grp_update)

        # Refresh after installation
        layout.addSpacing(10)
        self.auto_refresh = QCheckBox(tr("settings_pacman_auto_refresh"))
        self.auto_refresh.setChecked(True)
        layout.addWidget(self.auto_refresh)

        delay_row = QHBoxLayout()
        delay_row.addSpacing(25)
        delay_row.addWidget(QLabel(tr("settings_pacman_refresh_delay")))
        self.refresh_delay = QSpinBox()
        self.refresh_delay.setRange(0, 2000)
        self.refresh_delay.setValue(400)
        self.refresh_delay.setSuffix(" ms")
        delay_row.addWidget(self.refresh_delay)
        delay_row.addStretch()
        layout.addLayout(delay_row)

        # Info box – now blue/info (passive, no action required)
        layout.addSpacing(20)
        pacman_note = QLabel(tr("settings_pacman_note"))
        pacman_note.setWordWrap(True)
        pacman_note.setStyleSheet(
            "background-color: #e8f4f8; padding: 10px; border-radius: 5px; "
            "color: #0c5460;"
        )
        layout.addWidget(pacman_note)

        layout.addStretch()
        return widget

    # ===== TAB 5: Reflector =====
    def _build_reflector_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_reflector_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        available = shutil.which("reflector") is not None
        status = QLabel(
            tr("settings_reflector_available") if available else tr("settings_reflector_missing")
        )
        status.setStyleSheet("color: green;" if available else "color: #cc6600;")
        layout.addWidget(status)

        self.reflector_config_group = QGroupBox(tr("settings_reflector_command_group"))
        config_layout = QFormLayout()

        self.reflector_args = QLineEdit()
        self.reflector_args.setPlaceholderText("--latest 10 --sort rate")
        config_layout.addRow(tr("settings_reflector_args"), self.reflector_args)

        self.reflector_save_path = QLineEdit()
        self.reflector_save_path.setPlaceholderText("/etc/pacman.d/mirrorlist")
        config_layout.addRow(tr("settings_reflector_save_path"), self.reflector_save_path)

        self.reflector_backup_enable = QCheckBox(tr("settings_reflector_backup_enable"))
        config_layout.addRow(self.reflector_backup_enable)

        self.reflector_backup_path = QLineEdit()
        self.reflector_backup_path.setPlaceholderText("/etc/pacman.d/mirrorlist.bak")
        config_layout.addRow(tr("settings_reflector_backup_path"), self.reflector_backup_path)

        self.reflector_backup_enable.toggled.connect(self.reflector_backup_path.setEnabled)

        self.reflector_config_group.setLayout(config_layout)
        layout.addWidget(self.reflector_config_group)

        hint = QLabel(tr("settings_reflector_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 9pt;")
        layout.addWidget(hint)

        layout.addStretch()
        return widget

    # ===== TAB 6: Flatpak =====
    def _build_flatpak_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_flatpak_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        # Scope selection
        grp_scope = QGroupBox(tr("settings_flatpak_scope_group"))
        grp_scope_layout = QVBoxLayout()

        scope_label = QLabel(f"<b>{tr('settings_flatpak_scope_label')}:</b>")
        grp_scope_layout.addWidget(scope_label)

        self.flatpak_scope = QComboBox()
        self.flatpak_scope.addItems([
            tr("settings_flatpak_scope_user"),
            tr("settings_flatpak_scope_system")
        ])
        grp_scope_layout.addWidget(self.flatpak_scope)

        grp_scope.setLayout(grp_scope_layout)
        layout.addWidget(grp_scope)

        # Remote management
        grp_remote = QGroupBox(tr("settings_flatpak_remotes_group"))
        grp_remote_layout = QVBoxLayout()

        self.flatpak_auto_remote = QCheckBox(tr("settings_flatpak_auto_add"))
        self.flatpak_auto_remote.setChecked(True)
        grp_remote_layout.addWidget(self.flatpak_auto_remote)

        hint = QLabel(tr("settings_flatpak_auto_add_hint"))
        hint.setStyleSheet("color: gray; font-size: 9pt; margin-left: 25px;")
        grp_remote_layout.addWidget(hint)

        grp_remote.setLayout(grp_remote_layout)
        layout.addWidget(grp_remote)

        layout.addSpacing(20)

        remotes_group = QGroupBox(tr("settings_flatpak_configured"))
        remotes_layout = QVBoxLayout()

        self.flatpak_remote_table = QTableWidget(0, 3)
        self.flatpak_remote_table.setHorizontalHeaderLabels([
            tr("settings_flatpak_remotes_table_scope"),
            tr("settings_flatpak_remotes_table_name"),
            tr("settings_flatpak_remotes_table_url"),
        ])
        self.flatpak_remote_table.verticalHeader().setVisible(False)
        self.flatpak_remote_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.flatpak_remote_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.flatpak_remote_table.setSelectionMode(QTableWidget.SingleSelection)
        self.flatpak_remote_table.horizontalHeader().setStretchLastSection(True)
        self.flatpak_remote_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.flatpak_remote_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        remotes_layout.addWidget(self.flatpak_remote_table)

        self.flatpak_remote_status = QLabel()
        self.flatpak_remote_status.setStyleSheet("color: gray; margin-left: 4px;")
        remotes_layout.addWidget(self.flatpak_remote_status)

        btn_remote_row = QHBoxLayout()
        self.btn_flatpak_remote_add = QPushButton(tr("settings_flatpak_remotes_add"))
        self.btn_flatpak_remote_remove = QPushButton(tr("settings_flatpak_remotes_remove"))
        self.btn_flatpak_remote_refresh = QPushButton(tr("settings_flatpak_remotes_refresh"))

        btn_remote_row.addWidget(self.btn_flatpak_remote_add)
        btn_remote_row.addWidget(self.btn_flatpak_remote_remove)
        btn_remote_row.addStretch()
        btn_remote_row.addWidget(self.btn_flatpak_remote_refresh)
        remotes_layout.addLayout(btn_remote_row)

        remotes_group.setLayout(remotes_layout)
        layout.addWidget(remotes_group)

        self.btn_flatpak_remote_add.clicked.connect(self._on_add_flatpak_remote)
        self.btn_flatpak_remote_remove.clicked.connect(self._on_remove_flatpak_remote)
        self.btn_flatpak_remote_refresh.clicked.connect(self._refresh_flatpak_remotes)

        self.flatpak_remote_table.selectionModel().selectionChanged.connect(self._update_flatpak_remote_buttons)

        self._refresh_flatpak_remotes()

        layout.addStretch()
        return widget

    # ===== TAB 7: System maintenance =====
    def _build_cleanup_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_cleanup_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        form = QFormLayout()

        self.cleanup_keep_versions = QSpinBox()
        self.cleanup_keep_versions.setRange(0, 10)
        self.cleanup_keep_versions.setSuffix(tr("settings_cleanup_keep_versions_suffix"))
        form.addRow(tr("settings_cleanup_keep_versions"), self.cleanup_keep_versions)

        self.cleanup_log_age = QSpinBox()
        self.cleanup_log_age.setRange(1, 365)
        self.cleanup_log_age.setSuffix(tr("settings_cleanup_log_age_suffix"))
        form.addRow(tr("settings_cleanup_log_age"), self.cleanup_log_age)

        layout.addLayout(form)

        hint = QLabel(tr("settings_cleanup_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "background-color: #e8f4f8; padding: 10px; border-radius: 5px; "
            "color: #0c5460;"
        )
        layout.addWidget(hint)

        layout.addStretch()
        return widget

    # ===== TAB 8: Update Service =====
    def _build_update_service_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_update_service_description"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        self.update_service_enable = QCheckBox(tr("settings_update_service_enable"))
        layout.addWidget(self.update_service_enable)

        boot_row = QHBoxLayout()
        boot_row.addSpacing(24)
        self.update_service_check_on_boot = QCheckBox(
            tr("settings_update_service_check_on_boot")
        )
        boot_row.addWidget(self.update_service_check_on_boot)
        boot_row.addStretch(1)
        layout.addLayout(boot_row)

        group = QGroupBox(tr("settings_update_service_interval_group"))
        group_layout = QVBoxLayout(group)

        manual_row = QHBoxLayout()
        self.update_service_manual = QRadioButton(tr("settings_update_service_manual"))
        manual_row.addWidget(self.update_service_manual)

        self.update_service_manual_hours = QSpinBox()
        self.update_service_manual_hours.setRange(1, 168)
        self.update_service_manual_hours.setSingleStep(1)
        self.update_service_manual_hours.setSuffix(
            f" {tr('settings_update_service_hours_suffix')}"
        )
        manual_row.addWidget(self.update_service_manual_hours)
        manual_row.addStretch(1)
        group_layout.addLayout(manual_row)

        daily_row = QHBoxLayout()
        self.update_service_daily = QRadioButton(tr("settings_update_service_daily"))
        daily_row.addWidget(self.update_service_daily)

        self.update_service_daily_time_label = QLabel(tr("settings_update_service_time_label"))
        daily_row.addWidget(self.update_service_daily_time_label)

        self.update_service_daily_time = QTimeEdit()
        self.update_service_daily_time.setDisplayFormat("HH:mm")
        self.update_service_daily_time.setTime(QTime(9, 0))
        daily_row.addWidget(self.update_service_daily_time)
        daily_row.addStretch(1)
        group_layout.addLayout(daily_row)

        weekly_row = QHBoxLayout()
        self.update_service_weekly = QRadioButton(tr("settings_update_service_weekly"))
        weekly_row.addWidget(self.update_service_weekly)

        self.update_service_weekly_day_label = QLabel(tr("settings_update_service_day_label"))
        weekly_row.addWidget(self.update_service_weekly_day_label)

        self.update_service_weekly_day = QComboBox()
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_monday"), "Mon"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_tuesday"), "Tue"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_wednesday"), "Wed"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_thursday"), "Thu"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_friday"), "Fri"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_saturday"), "Sat"
        )
        self.update_service_weekly_day.addItem(
            tr("settings_update_service_weekday_sunday"), "Sun"
        )
        weekly_row.addWidget(self.update_service_weekly_day)

        self.update_service_weekly_time_label = QLabel(tr("settings_update_service_time_label"))
        weekly_row.addWidget(self.update_service_weekly_time_label)

        self.update_service_weekly_time = QTimeEdit()
        self.update_service_weekly_time.setDisplayFormat("HH:mm")
        self.update_service_weekly_time.setTime(QTime(9, 0))
        weekly_row.addWidget(self.update_service_weekly_time)
        weekly_row.addStretch(1)

        group_layout.addLayout(weekly_row)

        group_layout.addStretch(1)
        layout.addWidget(group)

        self.update_service_status = QLabel()
        self.update_service_status.setWordWrap(True)
        self.update_service_status.setStyleSheet("color: gray; margin-top: 12px;")
        layout.addWidget(self.update_service_status)

        layout.addStretch(1)

        self.update_service_manual.toggled.connect(self._on_update_service_mode_changed)
        self.update_service_daily.toggled.connect(self._on_update_service_mode_changed)
        self.update_service_weekly.toggled.connect(self._on_update_service_mode_changed)

        return widget

    # ===== TAB 9: Language =====
    def _build_language_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(tr("settings_lang_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; margin-bottom: 10px;")
        layout.addWidget(info)

        # Radio buttons
        self.lang_auto = QRadioButton(tr("settings_lang_auto"))
        self.lang_de = QRadioButton(tr("settings_lang_de"))
        self.lang_en = QRadioButton(tr("settings_lang_en"))

        self.lang_group = QButtonGroup()
        for btn in (self.lang_auto, self.lang_de, self.lang_en):
            self.lang_group.addButton(btn)
            layout.addWidget(btn)

        layout.addSpacing(20)

        # Status info: System-Sprache
        status_label = QLabel(f"<b>{tr('settings_lang_system')}:</b>")
        layout.addWidget(status_label)

        try:
            sys_lang, _ = locale.getdefaultlocale()
            if not sys_lang:
                sys_lang = "unknown"
        except Exception:
            sys_lang = "unknown"

        sys_lang_lbl = QLabel(tr("settings_lang_detected", sys_lang))
        sys_lang_lbl.setStyleSheet("margin-left: 20px; color: #555;")
        layout.addWidget(sys_lang_lbl)

        detected_lang = tr("settings_lang_de") if sys_lang.startswith("de") else tr("settings_lang_en")
        detected_lbl = QLabel(tr("settings_lang_auto_selection", detected_lang))
        detected_lbl.setStyleSheet("color: green; margin-left: 20px; font-weight: bold;")
        layout.addWidget(detected_lbl)

        layout.addSpacing(20)

        # Current setting
        current_label = QLabel(f"<b>{tr('settings_lang_current')}:</b>")
        layout.addWidget(current_label)

        current_lang = settings.get_language()
        current_lang_name = tr("settings_lang_de") if current_lang == "de" else tr("settings_lang_en")
        current_lbl = QLabel(tr("settings_lang_active", current_lang_name))
        current_lbl.setStyleSheet("color: green; margin-left: 20px; font-weight: bold;")
        layout.addWidget(current_lbl)

        layout.addSpacing(20)

        # Notice – remains warning yellow (restart required = action needed)
        note = QLabel(tr("settings_lang_restart_note"))
        note.setWordWrap(True)
        note.setStyleSheet(
            "background-color: #fff3cd; padding: 10px; border-radius: 5px; "
            "color: #856404;"
        )
        layout.addWidget(note)

        layout.addStretch()
        return widget

    # ===== Logic =====
    def _load_values(self):
        """""Load the current settings into the UI."""

        # AUR
        aur = settings.get("aur_helper")
        if aur == "auto":
            self.aur_auto.setChecked(True)
        elif aur == "yay":
            self.aur_yay.setChecked(True)
        elif aur == "paru":
            self.aur_paru.setChecked(True)
        elif aur == "pikaur":
            self.aur_pikaur.setChecked(True)
        else:
            self.aur_custom.setChecked(True)
            self.aur_custom_path.setText(aur)

        # Root
        root = settings.get("root_method")
        if root == "auto":
            self.root_auto.setChecked(True)
        elif root == "sudo":
            self.root_sudo.setChecked(True)
        elif root == "doas":
            self.root_doas.setChecked(True)
        else:
            self.root_sudo.setChecked(True)


        # Pacman
        self.pacman_noconfirm.setChecked(settings.get("pacman_noconfirm", False))
        self.pacman_show_deps.setChecked(settings.get("pacman_show_deps_before_remove", True))

        flags = settings.get("pacman_uninstall_flags", "-Rns")
        idx = {"-Rns": 0, "-Rn": 1, "-Rs": 2, "-R": 3}.get(flags, 0)
        self.pacman_flags.setCurrentIndex(idx)

        self.auto_refresh.setChecked(settings.get("auto_refresh_after_install", True))
        self.refresh_delay.setValue(settings.get("refresh_delay_ms", 400))

        self.reflector_args.setText(settings.get("reflector_args", "--latest 10 --sort rate"))
        self.reflector_save_path.setText(settings.get("reflector_save_path", "/etc/pacman.d/mirrorlist"))
        backup_enabled = settings.get("reflector_backup_enabled", True)
        self.reflector_backup_enable.setChecked(backup_enabled)
        self.reflector_backup_path.setText(settings.get("reflector_backup_path", "/etc/pacman.d/mirrorlist.bak"))
        self.reflector_backup_path.setEnabled(backup_enabled)

        # Flatpak
        scope = settings.get("flatpak_default_scope", "user")
        self.flatpak_scope.setCurrentIndex(0 if scope == "user" else 1)
        self.flatpak_auto_remote.setChecked(settings.get("flatpak_auto_add_remotes", True))

        self._update_flatpak_remote_buttons()

        # Cleanup
        self.cleanup_keep_versions.setValue(settings.get("cleanup_keep_pkg_versions", 2))
        self.cleanup_log_age.setValue(settings.get("cleanup_log_max_age_days", 14))

        # Sprache
        lang = settings.get("language", "auto")
        if lang == "auto":
            self.lang_auto.setChecked(True)
        elif lang == "de":
            self.lang_de.setChecked(True)
        elif lang == "en":
            self.lang_en.setChecked(True)
        else:
            self.lang_auto.setChecked(True)

        # Update service
        enabled = settings.get("update_service_enabled", False)
        mode = settings.get("update_service_mode", "daily")
        hours = settings.get("update_service_manual_hours", 24)
        daily_time = settings.get("update_service_daily_time", "09:00")
        weekly_day = settings.get("update_service_weekly_day", "Mon")
        weekly_time = settings.get("update_service_weekly_time", "09:00")
        check_on_boot = settings.get("update_service_check_on_boot", True)

        self.update_service_enable.setChecked(bool(enabled))
        self.update_service_check_on_boot.setChecked(bool(check_on_boot))
        self.update_service_manual_hours.setValue(max(1, int(hours)))

        daily_qtime = QTime.fromString(str(daily_time), "HH:mm")
        if not daily_qtime.isValid():
            daily_qtime = QTime(9, 0)
        self.update_service_daily_time.setTime(daily_qtime)

        weekly_qtime = QTime.fromString(str(weekly_time), "HH:mm")
        if not weekly_qtime.isValid():
            weekly_qtime = QTime(9, 0)
        self.update_service_weekly_time.setTime(weekly_qtime)

        idx = self.update_service_weekly_day.findData(str(weekly_day))
        if idx != -1:
            self.update_service_weekly_day.setCurrentIndex(idx)
        else:
            self.update_service_weekly_day.setCurrentIndex(0)

        if mode == "manual":
            self.update_service_manual.setChecked(True)
        elif mode == "weekly":
            self.update_service_weekly.setChecked(True)
        else:
            self.update_service_daily.setChecked(True)

        self._on_update_service_mode_changed()
        self._refresh_update_service_status()

    def _refresh_flatpak_remotes(self):
        available = shutil.which("flatpak") is not None
        self.flatpak_remote_table.setRowCount(0)

        if not available:
            self.flatpak_remote_table.setEnabled(False)
            self.btn_flatpak_remote_add.setEnabled(False)
            self.btn_flatpak_remote_remove.setEnabled(False)
            self.btn_flatpak_remote_refresh.setEnabled(False)
            self.flatpak_remote_status.setText(tr("settings_flatpak_not_installed"))
            self.flatpak_remote_status.setStyleSheet("color: #cc6600; margin-left: 4px;")
            return

        self.flatpak_remote_table.setEnabled(True)
        self.btn_flatpak_remote_add.setEnabled(True)
        self.btn_flatpak_remote_refresh.setEnabled(True)

        try:
            remotes = self._query_flatpak_remotes("user") + self._query_flatpak_remotes("system")
        except subprocess.CalledProcessError:
            self.flatpak_remote_status.setText(tr("settings_flatpak_query_failed"))
            self.flatpak_remote_status.setStyleSheet("color: #cc6600; margin-left: 4px;")
            self.btn_flatpak_remote_remove.setEnabled(False)
            return
        except FileNotFoundError:
            self.flatpak_remote_status.setText(tr("settings_flatpak_not_installed"))
            self.flatpak_remote_status.setStyleSheet("color: #cc6600; margin-left: 4px;")
            self.btn_flatpak_remote_add.setEnabled(False)
            self.btn_flatpak_remote_remove.setEnabled(False)
            self.btn_flatpak_remote_refresh.setEnabled(False)
            return

        if not remotes:
            self.flatpak_remote_status.setText(tr("settings_flatpak_remotes_empty"))
            self.flatpak_remote_status.setStyleSheet("color: gray; margin-left: 4px;")
        else:
            self.flatpak_remote_status.setText("")
            self.flatpak_remote_status.setStyleSheet("color: gray; margin-left: 4px;")

        for scope, name, url in remotes:
            row = self.flatpak_remote_table.rowCount()
            self.flatpak_remote_table.insertRow(row)
            scope_item = QTableWidgetItem(
                tr("settings_flatpak_remote_scope_user") if scope == "user" else tr("settings_flatpak_remote_scope_system")
            )
            scope_item.setData(Qt.UserRole, scope)
            name_item = QTableWidgetItem(name)
            url_item = QTableWidgetItem(url)
            self.flatpak_remote_table.setItem(row, 0, scope_item)
            self.flatpak_remote_table.setItem(row, 1, name_item)
            self.flatpak_remote_table.setItem(row, 2, url_item)

        self._update_flatpak_remote_buttons()

    def _query_flatpak_remotes(self, scope: str):
        args = ["flatpak", "remotes", f"--{scope}", "--columns=name,url"]
        output = subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
        remotes = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("name"):
                continue
            parts = line.split(None, 1)
            name = parts[0]
            url = parts[1].strip() if len(parts) > 1 else ""
            remotes.append((scope, name, url))
        return remotes

    def _on_add_flatpak_remote(self):
        dlg = FlatpakRemoteDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return

        scope, name, url = dlg.result
        cmd = ["flatpak", "remote-add", f"--{scope}", name, url]
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError as exc:
            output = (exc.output or str(exc)).strip()
            QMessageBox.warning(self, tr("dialog_hint"), tr("settings_flatpak_remote_add_error", output))
            return
        except FileNotFoundError:
            QMessageBox.warning(self, tr("dialog_hint"), tr("settings_flatpak_not_installed"))
            self._refresh_flatpak_remotes()
            return

        self._refresh_flatpak_remotes()

    def _on_remove_flatpak_remote(self):
        current_row = self.flatpak_remote_table.currentRow()
        if current_row < 0:
            return

        name_item = self.flatpak_remote_table.item(current_row, 1)
        scope_item = self.flatpak_remote_table.item(current_row, 0)
        if not name_item or not scope_item:
            return

        name = name_item.text()
        scope = scope_item.data(Qt.UserRole) or "system"

        if QMessageBox.question(
            self,
            tr("dialog_confirm"),
            tr("settings_flatpak_remote_delete_confirm", name)
        ) != QMessageBox.Yes:
            return

        cmd = ["flatpak", "remote-delete", f"--{scope}", name]
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError as exc:
            output = (exc.output or str(exc)).strip()
            QMessageBox.warning(self, tr("dialog_hint"), tr("settings_flatpak_remote_delete_error", output))
            return
        except FileNotFoundError:
            QMessageBox.warning(self, tr("dialog_hint"), tr("settings_flatpak_not_installed"))
            self._refresh_flatpak_remotes()
            return

        self._refresh_flatpak_remotes()

    def _update_flatpak_remote_buttons(self, *args):
        available = shutil.which("flatpak") is not None
        has_selection = False
        if self.flatpak_remote_table.selectionModel():
            has_selection = bool(self.flatpak_remote_table.selectionModel().selectedRows())

        self.btn_flatpak_remote_remove.setEnabled(available and has_selection)

    def _save_and_close(self):
        """Speichert UI-Werte in Settings."""

        # Remember previous language
        old_lang = settings.get_language()

        # AUR
        if self.aur_auto.isChecked():
            settings.set("aur_helper", "auto")
        elif self.aur_yay.isChecked():
            settings.set("aur_helper", "yay")
        elif self.aur_paru.isChecked():
            settings.set("aur_helper", "paru")
        elif self.aur_pikaur.isChecked():
            settings.set("aur_helper", "pikaur")
        else:
            settings.set("aur_helper", self.aur_custom_path.text().strip() or "auto")

        # Root
        if self.root_auto.isChecked():
            settings.set("root_method", "auto")
        elif self.root_sudo.isChecked():
            settings.set("root_method", "sudo")
        elif self.root_doas.isChecked():
            settings.set("root_method", "doas")
        

        # Pacman
        settings.set("pacman_noconfirm", self.pacman_noconfirm.isChecked())
        settings.set("pacman_show_deps_before_remove", self.pacman_show_deps.isChecked())

        flags_map = ["-Rns", "-Rn", "-Rs", "-R"]
        settings.set("pacman_uninstall_flags", flags_map[self.pacman_flags.currentIndex()])

        settings.set("auto_refresh_after_install", self.auto_refresh.isChecked())
        settings.set("refresh_delay_ms", self.refresh_delay.value())

        # Reflector
        reflector_args = self.reflector_args.text().strip()
        try:
            shlex.split(reflector_args) if reflector_args else []
        except ValueError as exc:
            QMessageBox.warning(
                self,
                tr("dialog_hint"),
                tr("settings_reflector_args_invalid", str(exc)),
            )
            self.reflector_args.setFocus()
            return

        settings.set("reflector_args", reflector_args)
        settings.set("reflector_save_path", self.reflector_save_path.text().strip())
        settings.set("reflector_backup_enabled", self.reflector_backup_enable.isChecked())
        settings.set("reflector_backup_path", self.reflector_backup_path.text().strip())

        # Flatpak
        scope = "user" if self.flatpak_scope.currentIndex() == 0 else "system"
        settings.set("flatpak_default_scope", scope)
        settings.set("flatpak_auto_add_remotes", self.flatpak_auto_remote.isChecked())

        # Cleanup
        settings.set("cleanup_keep_pkg_versions", self.cleanup_keep_versions.value())
        settings.set("cleanup_log_max_age_days", self.cleanup_log_age.value())

        # Sprache
        if self.lang_auto.isChecked():
            settings.set("language", "auto")
        elif self.lang_de.isChecked():
            settings.set("language", "de")
        elif self.lang_en.isChecked():
            settings.set("language", "en")

        # Update service
        update_enabled = self.update_service_enable.isChecked()
        if self.update_service_manual.isChecked():
            update_mode = "manual"
        elif self.update_service_weekly.isChecked():
            update_mode = "weekly"
        else:
            update_mode = "daily"

        manual_hours = self.update_service_manual_hours.value()

        daily_time = self.update_service_daily_time.time().toString("HH:mm")
        weekly_day = self.update_service_weekly_day.currentData(Qt.UserRole) or "Mon"
        weekly_time = self.update_service_weekly_time.time().toString("HH:mm")

        ok, error = update_service.apply_settings(
            update_enabled,
            update_mode,
            manual_hours,
            daily_time,
            str(weekly_day),
            weekly_time,
            check_on_boot=self.update_service_check_on_boot.isChecked(),
        )
        if not ok:
            QMessageBox.warning(
                self,
                tr("dialog_hint"),
                tr("settings_update_service_apply_failed", error or ""),
            )
            self._refresh_update_service_status()
            return

        settings.set("update_service_enabled", update_enabled)
        settings.set("update_service_mode", update_mode)
        settings.set("update_service_manual_hours", manual_hours)
        settings.set("update_service_daily_time", daily_time)
        settings.set("update_service_weekly_day", str(weekly_day))
        settings.set("update_service_weekly_time", weekly_time)
        settings.set(
            "update_service_check_on_boot",
            self.update_service_check_on_boot.isChecked(),
        )

        # Speichern
        settings.save()

        # Determine new language
        settings.load()
        new_lang = settings.get_language()

        # Check whether the language changed
        if old_lang != new_lang:
            QMessageBox.information(
                self,
                "Neustart erforderlich / Restart Required",
                "Die Sprache wurde geändert.\n"
                "Bitte starte WrapPac neu, damit die Änderung wirksam wird.\n\n"
                "Language has been changed.\n"
                "Please restart WrapPac for changes to take effect."
            )

        self.accept()

    def _reset_defaults(self):
        """Reset all values to their defaults."""
        reply = QMessageBox.question(
            self, tr("settings_reset_title"),
            tr("settings_reset_message"),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            settings.reset_to_defaults()
            self._load_values()
            QMessageBox.information(
                self, tr("settings_reset_done_title"),
                tr("settings_reset_done_message")
            )

    def _on_update_service_mode_changed(self):
        if hasattr(self, "update_service_manual_hours"):
            self.update_service_manual_hours.setEnabled(self.update_service_manual.isChecked())
        if hasattr(self, "update_service_daily_time"):
            is_daily = self.update_service_daily.isChecked()
            self.update_service_daily_time.setEnabled(is_daily)
            self.update_service_daily_time_label.setEnabled(is_daily)
        if hasattr(self, "update_service_weekly_day"):
            is_weekly = self.update_service_weekly.isChecked()
            self.update_service_weekly_day.setEnabled(is_weekly)
            self.update_service_weekly_day_label.setEnabled(is_weekly)
            self.update_service_weekly_time.setEnabled(is_weekly)
            self.update_service_weekly_time_label.setEnabled(is_weekly)

    def _refresh_update_service_status(self):
        if not hasattr(self, "update_service_status"):
            return

        status = update_service.get_status()
        if not status.get("available", False):
            text = tr("settings_update_service_systemctl_missing")
        else:
            enabled = status.get("enabled")
            active = status.get("active")
            if enabled is None or active is None:
                text = tr("settings_update_service_status_unknown")
            elif enabled:
                if active:
                    text = tr("settings_update_service_status_active")
                else:
                    text = tr("settings_update_service_status_enabled")
            else:
                text = tr("settings_update_service_status_disabled")

        self.update_service_status.setText(text)
