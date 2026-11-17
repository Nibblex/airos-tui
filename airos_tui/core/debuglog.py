"""--debug: what the program sends to the device and what it answers, in a file only you can
read, without what is secret: passwords and their hashes, WPA keys, RADIUS secrets, SNMP
communities, the session's cookies and token, an uploaded file (a backup, a firmware)."""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse

from .config import DATA_DIR

DEBUG_FILE = os.path.join(DATA_DIR, "debug.log")
DEBUG_BODY = 600                      # characters of each body it shows
DEBUG_MAX = 20 * 1024 * 1024          # bytes: past this, it stops writing (and says so)
HIDDEN = "***"

# A name that holds a secret: a form field, a JSON key, a key of the configuration.
_SECRET = r"[\w.-]*(?:pass|pwd|psk|secret|key|token|community|new_config|cfgfile)[\w.-]*"
_LINE_RE = re.compile(rf"(?mi)^(\s*{_SECRET}\s*=).*$")                 # key=value lines
_JSON_RE = re.compile(rf'(?i)("{_SECRET}"\s*:\s*)("(?:[^"\\]|\\.)*"|[^,}}\]\s]+)')
_FORM_RE = re.compile(rf"(?i)((?:^|[?&]){_SECRET}=)[^&\s]*")
_HASH_RE = re.compile(r"\$(?:1|5|6|2[aby]?)\$[^\s\"'&:;,]+")              # crypt hashes
_SECRET_NAME = re.compile(_SECRET, re.I)

_lock = threading.Lock()
_out = None
_written = 0


def hide_secrets(text: str) -> str:
    """text without its secrets: those of the configuration's lines, of JSON, of a form or a
    query string, and any password hash."""
    text = _LINE_RE.sub(lambda m: m.group(1) + HIDDEN, text)
    text = _JSON_RE.sub(lambda m: m.group(1) + f'"{HIDDEN}"', text)
    text = _FORM_RE.sub(lambda m: m.group(1) + HIDDEN, text)
    return _HASH_RE.sub(HIDDEN, text)


def _cookies(value: str) -> str:
    """A Cookie or Set-Cookie header with its values hidden (their names and attributes
    stay: which session, its path…)."""
    return re.sub(r"(^|;\s*)([^=;\s]+)=([^;]*)", lambda m: m.group(1) + m.group(2) + "=" + (
        m.group(3) if m.group(2).lower() in ("path", "expires", "max-age", "domain", "samesite")
        else HIDDEN), value)


def _headers(items) -> list[str]:
    out = []
    for k, value in items:
        low = k.lower()
        shown = (_cookies(value) if low in ("cookie", "set-cookie")
                 else HIDDEN if low in ("x-csrf-id", "authorization") else value)
        out.append(f"{k}: {shown}")
    return out


def _is_text(ctype: str, text: str) -> bool:
    """Whether a body is text to show: of a text type (or none said) and without the control
    characters of binary data."""
    texty = not ctype or ctype.startswith("text/") or any(
        t in ctype for t in ("json", "javascript", "xml"))
    return texty and not re.search(r"[\x00-\x08\x0e-\x1f]", text[:2048])


def _body(data, content_type: str) -> str:
    """A body as it is shown: text with its secrets hidden, cut at DEBUG_BODY; a file or
    binary data, only its size."""
    if not data:
        return ""
    if isinstance(data, str):
        data = data.encode("utf-8")
    ctype = (content_type or "").lower()
    if "multipart" in ctype:
        return f"<{len(data)} bytes, a form with a file: not shown>"
    text = data.decode("utf-8", "replace")
    if "x-www-form-urlencoded" in ctype:
        pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
        text = "&".join(f"{k}={HIDDEN if _SECRET_NAME.fullmatch(k) else v}" for k, v in pairs)
    elif not _is_text(ctype, text):
        return f"<{len(data)} bytes ({content_type or 'binary'}): not shown>"
    text = hide_secrets(text)
    cut = len(text) > DEBUG_BODY
    return text[:DEBUG_BODY] + (f"… ({len(text)} characters)" if cut else "")


def start_debug(path: str = DEBUG_FILE) -> str:
    """Write from now on what goes to the device and what it answers in path (only for you:
    0600), anew. Returns path."""
    global _out, _written
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with _lock:
        _out, _written = os.fdopen(fd, "w", encoding="utf-8"), 0
    _write(f"airos-tui --debug, {time.strftime('%Y-%m-%d %H:%M:%S')}: passwords, keys, hashes "
           f"and session cookies shown as {HIDDEN}\n\n")
    return path


def stop_debug() -> None:
    global _out
    with _lock:
        if _out is not None:
            _out.close()
            _out = None


def debugging() -> bool:
    return _out is not None


def _write(text: str) -> None:
    """Into the file, while it can be written (a full disk: no more of it, and the program
    goes on as without --debug)."""
    global _written, _out
    with _lock:
        if _out is None:
            return
        try:
            if _written + len(text) > DEBUG_MAX:
                _out.write(f"(the file reached {DEBUG_MAX // (1024 * 1024)} MB: the rest is "
                           "not saved)\n")
                _out.close()
                _out = None
                return
            _out.write(text)
            _out.flush()
        except OSError:
            try:
                _out.close()
            except OSError:
                pass
            _out = None
            return
        _written += len(text)


def debug_request(method: str, url: str, headers: dict, body, seconds: float,
                  status: int | None = None, resp_headers=None, data=None,
                  error: str | None = None, size: int | None = None) -> None:
    """One request and its answer (or what went wrong), as the file shows it. size: the
    bytes of an answer not kept here (a firmware downloaded to a file)."""
    if _out is None:
        return
    when = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    head = f"{when} {method} {hide_secrets(url)}"
    got = len(data or b"") if size is None else size
    lines = [head + (f" → {status} ({seconds:.2f} s, {got} bytes)" if error is None
                     else f" → {error} ({seconds:.2f} s)")]
    lines += ["  > " + h for h in _headers(headers.items())]
    shown = _body(body, headers.get("Content-Type", ""))
    if shown:
        lines += ["  > " + x for x in shown.split("\n")]
    if error is None:
        rh = list(resp_headers.items()) if resp_headers is not None else []
        lines += ["  < " + h for h in _headers(rh)]
        shown = _body(data, resp_headers.get("Content-Type", "") if resp_headers else "")
        if shown:
            lines += ["  < " + x for x in shown.split("\n")]
    _write("\n".join(lines) + "\n\n")
