"""i18n sanity checks.

These tests ensure the German and English translation tables stay in sync
and that every translation key used by the UI actually exists. This guards
against the silent fallback in ``tr()`` (returning the raw key) when a key
is missing.
"""

import os
import re

import i18n


def _src_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _literal_tr_keys():
    """Collect every first argument passed as a string literal to ``tr(...)``."""
    keys = set()
    tr_call = re.compile(r"\btr\(")
    string_literal = re.compile(r'(["\'])((?:\\.|(?!\1).)*)\1')

    for root, _dirs, files in os.walk(_src_dir()):
        for name in files:
            if not name.endswith(".py") or name == "i18n.py":
                continue
            src = open(os.path.join(root, name), encoding="utf-8").read()
            for m in tr_call.finditer(src):
                tail = src[m.end():].lstrip()
                lit = string_literal.match(tail)
                if lit:
                    keys.add(lit.group(2))
    return keys


def test_de_en_have_identical_key_sets():
    de = set(i18n.TRANSLATIONS["de"])
    en = set(i18n.TRANSLATIONS["en"])
    assert de == en, f"only in de: {sorted(de - en)!r}, only in en: {sorted(en - de)!r}"


def test_all_used_keys_are_translated():
    de = i18n.TRANSLATIONS["de"]
    en = i18n.TRANSLATIONS["en"]
    missing = sorted({k for k in _literal_tr_keys() if k not in de or k not in en})
    assert not missing, f"keys missing from at least one language: {missing}"


def test_tr_falls_back_to_key():
    assert i18n.tr("__definitely_missing_key__") == "__definitely_missing_key__"


def test_tr_formats_arguments():
    # Both languages use a "{}" placeholder for this key.
    assert "X" in i18n.tr("settings_lang_detected", "X")
