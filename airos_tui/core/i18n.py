"""Translations: messages are written in English and looked up in a catalog
(locales/<language>.json in this package); English needs none."""
from __future__ import annotations

import argparse
import json
import os


# (airos_tui/locales: beside this module's folder)
LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "locales")
LANGUAGES = ("en", "es")
_catalog: dict[str, str] = {}
_language = "en"


def N_(message: str) -> str:
    """Mark a message for translation without translating it (labels kept in constants)."""
    return message


def tr(message: str, **values) -> str:
    """The message in the current language; with values, fills in its {placeholders}."""
    text = _catalog.get(message, message)
    return text.format(**values) if values else text


def current_language() -> str:
    return _language


def detect_language(explicit: str | None = None) -> str:
    """--lang, then AIROS_LANG, then the system locale (LC_ALL, LC_MESSAGES, LANG)."""
    for value in (explicit, os.environ.get("AIROS_LANG"), os.environ.get("LC_ALL"),
                  os.environ.get("LC_MESSAGES"), os.environ.get("LANG")):
        if value:
            code = value.split(".")[0].split("_")[0].split("-")[0].lower()
            return code if code in LANGUAGES else "en"
    return "en"


def set_language(lang: str) -> str:
    """Load the catalog of lang (English needs none). Returns the language in use."""
    global _catalog, _language
    _catalog, _language = {}, "en"
    if lang in LANGUAGES and lang != "en":
        try:
            with open(os.path.join(LOCALE_DIR, f"{lang}.json"), encoding="utf-8") as f:
                _catalog = json.load(f)
            _language = lang
        except (OSError, ValueError):
            pass
    argparse._ = lambda text: _catalog.get(text, text)    # argparse looks _ up when used
    return _language


# argparse's own messages, so that --help and its errors are translated too.
ARGPARSE_MESSAGES = (
    N_("usage: "), N_("options"), N_("positional arguments"),
    N_("show this help message and exit"), N_("show program's version number and exit"),
    N_("the following arguments are required: %s"), N_("unrecognized arguments: %s"),
    N_("expected one argument"), N_("argument %(argument_name)s: %(message)s"),
    N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("invalid %(type)s value: %(value)r"), N_("not allowed with argument %s"),
)
