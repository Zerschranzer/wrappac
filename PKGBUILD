# Maintainer: Zerschranzer
pkgname=wrappac-git
_pkgname=wrappac
pkgver=0.0.0.r11.gfccdd05
pkgrel=1
pkgdesc="Minimalist GUI wrapper for pacman, AUR und Flatpak"
arch=('any')
url="https://github.com/Zerschranzer/wrappac"
license=('MIT')
depends=(
  'python'
  'pyside6'
  'python-pexpect'
  'pacman-contrib'
  'hicolor-icon-theme'
)
optdepends=(
  'yay: AUR-Support'
  'flatpak: Flatpak-Support'
)
makedepends=(
  'git'
)

source=("git+${url}.git#branch=testing")
sha256sums=('SKIP')

pkgver() {
  cd "${srcdir}/${_pkgname}"

  local ver
  ver="$(git describe --tags --long 2>/dev/null || true)"
  if [[ -n "$ver" ]]; then
    # "v" vorne entfernen und "-" in "." umwandeln
    ver="${ver#v}"
    ver="${ver//-/.}"
    printf '%s\n' "$ver"
  else
    printf '0.0.0.r%s.g%s\n' \
      "$(git rev-list --count HEAD)" \
      "$(git rev-parse --short HEAD)"
  fi
}

package() {
  cd "${srcdir}/${_pkgname}"

  # Install Python source files
  install -d "${pkgdir}/usr/share/${_pkgname}"
  cp -r src/* "${pkgdir}/usr/share/${_pkgname}/"

  # Create launcher script
  install -d "${pkgdir}/usr/bin"
  cat > "${pkgdir}/usr/bin/wrappac" << 'EOF'
#!/usr/bin/env bash
cd /usr/share/wrappac
exec python main.py "$@"
EOF
  chmod +x "${pkgdir}/usr/bin/wrappac"

  # Install desktop file
  install -d "${pkgdir}/usr/share/applications"
  cat > "${pkgdir}/usr/share/applications/wrappac.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=WrapPac
Comment=GUI-Wrapper for pacman, AUR and Flatpak
Exec=wrappac
Icon=wrappac
Terminal=false
Categories=System;PackageManager;
Keywords=package;pacman;aur;flatpak;
StartupNotify=true
EOF

  # Install icon
  install -Dm644 "src/assets/wrappac_logo.svg" \
    "${pkgdir}/usr/share/icons/hicolor/scalable/apps/wrappac.svg"

  # Install license
  if [[ -f LICENSE || -f LICENSE.txt ]]; then
    install -Dm644 LICENSE* "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
  fi
}
