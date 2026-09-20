"""Pure, GUI-independent domain logic for WrapPac packages.

This module intentionally has no Qt/PySide6 dependency so it can be unit
tested without a display or the Qt runtime installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PackageItem:
    pid: str           # pacman: package name, flatpak: app ID
    name: str          # Display name
    version: str
    source: str        # "Repo" | "AUR" | "Flatpak"
    origin: str        # Repository or remote (e.g. extra, community, local, flathub)
    size: str = ""


_SIZE_RE = re.compile(r"([0-9.,]+)\s*([KMGTPE]?i?B)?", re.IGNORECASE)

_SIZE_FACTORS = {
    "B": 1,
    "KIB": 1024,
    "MIB": 1024 ** 2,
    "GIB": 1024 ** 3,
    "TIB": 1024 ** 4,
    "PIB": 1024 ** 5,
    "EIB": 1024 ** 6,
    "KB": 1000,
    "MB": 1000 ** 2,
    "GB": 1000 ** 3,
    "TB": 1000 ** 4,
    "PB": 1000 ** 5,
    "EB": 1000 ** 6,
}


def size_to_bytes(size: str) -> float:
    """Parse a human-readable size (e.g. ``"12.3 MiB"``) into bytes.

    Returns ``0.0`` for empty or unparseable input.
    """
    if not size:
        return 0.0
    match = _SIZE_RE.match(size.strip())
    if not match:
        return 0.0

    number_part = match.group(1).replace(",", ".")
    try:
        value = float(number_part)
    except ValueError:
        return 0.0

    unit = (match.group(2) or "B").upper()
    return value * _SIZE_FACTORS.get(unit, 1)


_VERSION_TOKEN_RE = re.compile(r"\d+|[^\d]+")


def version_key(version: str) -> tuple:
    """Turn a version string into a sortable key.

    Splits the version into alternating numeric and non-numeric tokens so
    that digit runs sort numerically (``"1.10" > "1.2"``) while text parts
    sort case-insensitively. Empty input sorts before everything else.
    """
    if not version:
        return ()

    key = []
    for token in _VERSION_TOKEN_RE.findall(version):
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token.lower()))
    return tuple(key)
