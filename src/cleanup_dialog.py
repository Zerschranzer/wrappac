from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QCheckBox,
    QDialogButtonBox,
)

from i18n import tr


class CleanupDialog(QDialog):
    """Dialog zur Auswahl der Systempflege-Aktionen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_cleanup_title"))
        self.resize(420, 0)

        layout = QVBoxLayout(self)

        info = QLabel(tr("cleanup_dialog_intro"))
        info.setWordWrap(True)
        layout.addWidget(info)

        self.chk_orphans = QCheckBox(tr("cleanup_option_remove_orphans"))
        self.chk_cache = QCheckBox(tr("cleanup_option_clean_cache"))
        self.chk_flatpak = QCheckBox(tr("cleanup_option_remove_flatpak_runtimes"))
        self.chk_aur = QCheckBox(tr("cleanup_option_clear_aur_cache"))
        self.chk_logs = QCheckBox(tr("cleanup_option_clean_logs"))

        for chk in (
            self.chk_orphans,
            self.chk_cache,
            self.chk_flatpak,
            self.chk_aur,
            self.chk_logs,
        ):
            chk.setChecked(True)
            layout.addWidget(chk)

        hint = QLabel(tr("cleanup_dialog_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selections(self) -> dict:
        return {
            "orphans": self.chk_orphans.isChecked(),
            "cache": self.chk_cache.isChecked(),
            "flatpak": self.chk_flatpak.isChecked(),
            "aur": self.chk_aur.isChecked(),
            "logs": self.chk_logs.isChecked(),
        }
