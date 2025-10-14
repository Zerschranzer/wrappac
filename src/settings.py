import json
import locale
import shutil
from pathlib import Path
from typing import Any, Dict


class Settings:
    """Central settings management with sensible defaults."""

    DEFAULTS = {
        # Category 1: AUR helper
        "aur_helper": "auto",  # "auto", "yay", "paru", "pikaur", or a custom path
        "aur_helper_fallback": ["yay", "paru", "pikaur"],

        # Category 2: Root method
        "root_method": "sudo",  # "auto", "sudo", "doas"

        # Category 3: Pacman options
        "pacman_noconfirm": False,
        "pacman_uninstall_flags": "-Rns",
        "pacman_show_deps_before_remove": True,

        # Category 4: Flatpak
        "flatpak_default_scope": "user",  # "user" or "system"
        "flatpak_default_remote": "flathub",
        "flatpak_auto_add_remotes": True,

        # Category 5: Language
        "language": "auto",  # "auto", "de", "en"

        # UI & behavior (kept optional)
        "terminal_font_family": "Monospace",
        "terminal_font_size": 10,
        "terminal_theme": "dark",
        "notify_updates_available": True,
        "notify_install_complete": True,
        "notify_errors": True,
        "auto_refresh_after_install": True,
        "refresh_delay_ms": 400,

        # Mirror optimisation
        "reflector_args": "--latest 10 --sort rate",
        "reflector_save_path": "/etc/pacman.d/mirrorlist",
        "reflector_backup_enabled": True,
        "reflector_backup_path": "/etc/pacman.d/mirrorlist.bak",

        # System maintenance
        "cleanup_keep_pkg_versions": 2,
        "cleanup_log_max_age_days": 14,

        # Update service
        "update_service_enabled": False,
        "update_service_mode": "daily",  # "manual", "daily", "weekly"
        "update_service_manual_hours": 24,
        "update_service_daily_time": "09:00",
        "update_service_weekly_day": "Mon",
        "update_service_weekly_time": "09:00",
        "update_service_check_on_boot": True,
    }

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "wrappac"
        self.config_file = self.config_dir / "settings.json"
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self):
        """Load settings from file and fall back to defaults."""
        self._data = dict(self.DEFAULTS)

        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                    self._data.update(user_data)
            except Exception as e:
                print(f"Warning: Could not load settings: {e}")

    def save(self):
        """Persist the current settings."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error while saving settings: {e}")

    def get(self, key: str, default=None) -> Any:
        """Retrieve a setting value."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        """Store a setting value."""
        self._data[key] = value

    def reset_to_defaults(self):
        """Reset all settings."""
        self._data = dict(self.DEFAULTS)
        self.save()

    # ---- Convenience methods ----

    def get_aur_helper(self) -> str | None:
        """Determine which AUR helper to use."""
        helper = self.get("aur_helper")

        if helper == "auto":
            # Auto-detect
            for candidate in self.get("aur_helper_fallback", []):
                if shutil.which(candidate):
                    return candidate
            return None

        # Explicit helper value
        if shutil.which(helper):
            return helper

        return None

    def get_root_command(self) -> list[str]:
        """Return the prefix for root commands."""
        method = self.get("root_method")

        if method == "auto":
            if shutil.which("sudo"):
                return ["sudo"]
            if shutil.which("doas"):
                return ["doas"]
            return []

        if method == "sudo" and shutil.which("sudo"):
            return ["sudo"]
        elif method == "doas" and shutil.which("doas"):
            return ["doas"]

        return []

    def get_pacman_remove_flags(self) -> list[str]:
        """Return flags for pacman -R."""
        flags = self.get("pacman_uninstall_flags", "-Rns")
        # Split "-Rns" → ["-Rns"] or "-R -n -s" → ["-R", "-n", "-s"]
        return [flags] if not flags.startswith("-R ") else flags.split()

    def get_language(self) -> str:
        """Return the language code to use ("de" or "en")."""
        lang = self.get("language", "auto")

        if lang == "auto":
            # Detect system locale
            try:
                sys_lang, _ = locale.getdefaultlocale()
                if sys_lang and sys_lang.startswith("de"):
                    return "de"
            except Exception:
                pass
            return "en"  # Fallback to English

        # Validate explicit language choice
        return lang if lang in ("de", "en") else "en"


# Global instance
settings = Settings()
