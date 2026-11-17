"""The simulator's messages: airOS 6 and airOS 8 speak English, or airos-tui's own
Spanish catalog (airos_tui/locales), and so does its command line."""
from __future__ import annotations

import argparse
import json
import os


# Messages are written in English and translated with airos-tui's own catalog
# (airos_tui/locales/<language>.json, in the project's folder).
LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "airos_tui", "locales")
LANGUAGES = ("en", "es")
_catalog: dict[str, str] = {}


def tr(message: str, **values) -> str:
    """The message in the current language; with values, fills in its {placeholders}."""
    text = _catalog.get(message, message)
    return text.format(**values) if values else text


def detect_language(explicit: str | None = None) -> str:
    """--lang, then AIROS_LANG, then the system locale (LC_ALL, LC_MESSAGES, LANG)."""
    for value in (explicit, os.environ.get("AIROS_LANG"), os.environ.get("LC_ALL"),
                  os.environ.get("LC_MESSAGES"), os.environ.get("LANG")):
        if value:
            code = value.split(".")[0].split("_")[0].split("-")[0].lower()
            return code if code in LANGUAGES else "en"
    return "en"


def set_language(lang: str) -> None:
    global _catalog
    _catalog = {}
    if lang in LANGUAGES and lang != "en":
        try:
            with open(os.path.join(LOCALE_DIR, f"{lang}.json"), encoding="utf-8") as f:
                _catalog = json.load(f)
        except (OSError, ValueError):
            pass
    argparse._ = lambda text: _catalog.get(text, text)    # argparse looks _ up when used
