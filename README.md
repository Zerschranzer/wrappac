# WrapPac

<p align="center">
  <img src="src/assets/wrappac_logo.svg" alt="WrapPac Logo" width="160">
</p>

> ⚠️ **Disclaimer:**  
> This software is **experimental** and provided **as is**.  
> Use at your own risk — I take **no responsibility** for any damage, data loss, or system issues resulting from its use.

**WrapPac** is a lightweight desktop companion for **Arch Linux** users who mix packages from the official repositories, the AUR, and Flatpak.
It unifies all installed packages into a single overview and provides convenient tools to inspect, update, or remove them — without relying on internal pacman libraries or background root daemons.

---

## 💡 Motivation

**WrapPac** was built for users who want a graphical interface that behaves *exactly like the terminal* — using the same commands they run daily: `pacman`, `yay`, `paru`, and `flatpak`.

Most graphical package managers access pacman's functionality through the `libalpm` library, which provides programmatic control over transactions. While this approach works well for many users, some prefer the transparency and predictability of direct CLI invocation.

WrapPac takes a different approach: it's a pure wrapper around existing command-line tools, with no abstraction layers. Every action you perform in WrapPac is identical to typing the command yourself — you see the same output, the same prompts, and the same behavior.

---

## 🧠 Philosophy

WrapPac is **not** meant to replace your terminal workflow — it complements it.
It's for users who value Arch's simplicity and control, but still want a clear overview of installed packages and updates.

> **No root daemons. No hidden abstractions. No "magic".**  
> Just your real system commands — visualized.

---

## ⚙️ Design Philosophy

| Aspect            | Traditional GUI Package Managers       | **WrapPac**                                                       |
| ----------------- | -------------------------------------- | ----------------------------------------------------------------- |
| Backend           | `libalpm` library (programmatic API)   | Direct CLI invocation (`pacman`, `yay`, `paru`, `flatpak`)        |
| Transaction logic | Library-level transaction handling     | Identical to manual terminal commands                             |
| Privilege model   | Varies (some use persistent daemons)   | No persistent root access — uses `sudo`/`doas` only when needed   |
| Output visibility | GUI-formatted, abstracted messages     | Raw PTY terminal showing live, unfiltered command output          |
| Update checks     | Integrated background services         | Optional systemd timer with tray notifications |
| Configuration     | Managed through application settings   | Uses system `pacman.conf` and all configured hooks                |

---

## ✨ Features

### Core Functionality
* **Unified package view** — combines results from `pacman -Qn`, `pacman -Qm`, and `flatpak list`
* **Context-aware actions** — uninstall packages using the correct backend (`pacman`, `yay`, `paru`, or `flatpak`)
* **Integrated PTY terminal** — live output with keyboard control.
* **Non-blocking UI** — background threads keep the interface responsive during long operations

### Update Management
* **Optional systemd timer** — automatic update checks with configurable intervals:
  - Custom intervals (every N hours)
  - Daily at a specific time
  - Weekly on a specific day and time
  - Optional check on system boot
* **System tray notifications** — discrete notifications when updates are available
* **Single-instance enforcement** — prevents multiple windows, allows `--show-updates` flag for direct access

### System Maintenance
* **Mirror optimization** — integrated Reflector support with:
  - Customizable arguments
  - Automatic backup of current mirrorlist
  - Protocol filtering (prefers HTTPS when rsync unavailable)
* **Cleanup helpers** — remove:
  - Orphaned packages (`pacman -Qtdq`)
  - Package cache (configurable retention via `paccache`)
  - Unused Flatpak runtimes
  - AUR build caches (yay/paru/pikaur/etc.)
  - Old system logs (`journalctl --vacuum-time`)

### Flatpak Integration
* **Remote management** — add/remove Flatpak remotes directly from settings
* **Automatic remote setup** — optional auto-add for missing remotes (e.g., Flathub)
* **Scope control** — choose between user (`--user`) and system (`--system`) installations
* **Intelligent remote detection** — automatically determines installation scope

### User Experience
* **Multilingual** — full support for English and German with auto-detection
* **Configurable behavior** — extensive settings for:
  - AUR helper selection (auto-detect or manual)
  - Root method (`sudo` vs `doas`)
  - Pacman flags for removal (`-Rns`, `-Rn`, `-Rs`, `-R`)
  - Terminal appearance (font, theme)
  - Auto-refresh after operations
* **Rich package insights** — dual-tab details dialog with formatted metadata and raw command output
* **Persistent search history** — autocomplete remembers recent queries for faster lookups
* **Backup tools** — export installed packages to JSON snapshots and re-import with per-source previews
* **Status-aware overview** — live package counters, size column, and advanced filters for explicit, dependency, or orphaned installs
* **Install queue** — batch multiple package installations
* **Comprehensive error reporting** — shows failed commands with exit codes and stderr

---

## ⚙️ Installation (Arch & derivatives)

WrapPac is designed **exclusively for Arch-based distributions**.

### Install via PKGBUILD

```bash
git clone https://github.com/Zerschranzer/wrappac.git
cd wrappac
makepkg -si
```

This will:

* Build the package using your local environment
* Install it system-wide under `/usr/share/wrappac`
* Create a launcher script in `/usr/bin/wrappac`
* Add a `.desktop` entry for your menu

Uninstall it as usual:

```bash
sudo pacman -Rns wrappac-git
```

---

## 🧬 Requirements

**Runtime dependencies:**

* Python ≥ 3.10
* [PySide6](https://doc.qt.io/qtforpython/)
* `pacman` and `pacman-contrib` (for `checkupdates`)

**Optional integrations:**

* `yay` or `paru` — for AUR support
* `flatpak` — for Flatpak management
* `sudo` or `opendoas` — for privilege escalation
* `reflector` — to refresh mirrors
* `systemd` — for the optional update service timer

All dependencies are handled through the PKGBUILD.

---

## 🚀 Usage

### Basic Workflow

1. **Browse installed packages** — filter by source (All/Official/AUR/Flatpak)
2. **Search for new packages** — select a source, search, add to queue or install directly
3. **System updates** — click "System Update" to check and apply updates across all sources
4. **System maintenance** — use "System Cleanup" to remove orphans, clean caches, etc.

### Command-Line Options

```bash
wrappac                    # Normal start
wrappac --show-updates     # Open directly to update dialog
wrappac --tray-mode        # Start minimized (for systemd timer)
wrappac --run-update-service  # Internal: check updates and show tray notification
```

### Update Service Setup

Enable automatic update checks in **Settings → Update Service**:

1. Check "Enable update service"
2. Choose interval (manual hours, daily, or weekly)
3. Optionally enable "Check on system start"
4. Click Save

This creates a systemd user timer (`~/.config/systemd/user/wrappac-update.timer`) that runs in the background and shows tray notifications when updates are available.

**Checking service status:**

```bash
systemctl --user status wrappac-update.timer
systemctl --user list-timers  # See next scheduled run
```

---

## 📸 Screenshots

### Main Window

Unified view for Pacman, AUR, and Flatpak packages — all in one place.
![Main Window](screenshots/main_window.png)

### Package Installation

Search and install AUR, Flatpak, or official repository packages directly.
![Install Dialog](screenshots/main_window_install.png)

### Package Uninstallation

Context-aware uninstall — automatically uses the correct backend.
![Uninstall Dialog](screenshots/main_window_uninstall.png)

### Settings: Pacman

Configure removal flags, auto-refresh behavior, and confirmation options.
![Pacman Settings](screenshots/settings_dialog_pacman.png)

### Settings: Flatpak

Manage Flatpak remotes and user/system installation scopes.
![Flatpak Settings](screenshots/settings_dialog_flatpak.png)

### Settings: Language

Switch between English and German — auto-detects system locale.
![Language Settings](screenshots/settings_dialog_language.png)

### System Maintenance

Clean caches, remove orphans, and keep your system tidy.
![System Maintenance](screenshots/system_maintenance.png)

---

## ⚙️ Configuration

Settings are stored in `~/.config/wrappac/settings.json`. The settings dialog provides a GUI for all options:

### Categories

- **AUR Helper** — auto-detect or manually select yay/paru/pikaur
- **Root Method** — choose between sudo and doas
- **Pacman** — configure removal flags, --noconfirm usage, auto-refresh
- **Reflector** — mirror update arguments and backup settings
- **Flatpak** — default scope, remote management, auto-add behavior
- **System Maintenance** — cache retention, log age limits
- **Update Service** — timer configuration and boot-time checks
- **Language** — UI language (English/German)

---

## 🛡️ Security

WrapPac follows these security principles:

* **No persistent root daemons** — root access is only requested for individual operations
* **Transparent command execution** — every command is shown in the PTY terminal before execution
* **Password masking** — passwords are entered in secure dialogs, never echoed in the terminal
* **Respect system configuration** — uses your existing `sudoers`/`doas.conf` rules
* **No phone-home** — completely offline except for package operations you initiate

---

## 🗺️ Roadmap

Potential future features (not promised):

- [ ] Downgrade packages to previous versions
- [ ] AUR package build inspection before installation
- [ ] Package pinning (exclude from updates)
- [ ] Import/export installed package lists

---

<p align="center">
  <sub>WrapPac © 2025 — developed by Zerschranzer</sub>
</p>
