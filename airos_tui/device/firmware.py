"""The firmware update of the web's System page (airOS 8: FwView and AutoUpdateController):
the version it shows, its checks of an uploaded firmware and the messages it gives. And its
check for updates made from this computer, through its internet (for a device without it):
what the device asks Ubiquiti, and the firmware it would download, here."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
import urllib.parse
import urllib.request

from .. import __version__
from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import Config, DATA_DIR, _int
from ..core.debuglog import debug_request, debugging

DOWNLOADS_URL = "https://www.ui.com/download/airmax/"
FW_CHECK_URL = "https://callhome.svc.ui.com/update-fw/check.php"   # its fw_update_url
FW_EULA_URL = "https://www.ubnt.com/eula/eula.html"                # its fw_eula_url
FW_DIR = os.path.join(DATA_DIR, "firmware")   # what is downloaded here: for other devices too
FW_MAX = 64 * 1024 * 1024                   # bytes: the largest firmware file it reads
UPGRADE_POLL_DELAY = 10           # seconds before the web starts asking whether it is back
UPGRADE_TIME = 150                # board.upgrade when board.info does not say
UPGRADE_MARGIN = 60               # seconds beyond board.upgrade before giving up waiting
AUTO_CHECK_EVERY = 86400          # seconds between automatic checks (the web: once a day)


def fw_prefix(full_version: str) -> str:
    """The firmware series (WA, XC, XW…): the start of the version the login hands over."""
    return (full_version or "").split(".")[0]


def fw_build(full_version: str) -> str | None:
    """The build number the web shows (47873 in WA.ar934x.v8.7.14.47873.241003.1314)."""
    m = re.search(r"[.]([0-9]+)[.][0-9]{6}[.][0-9]{4}", full_version or "")
    return m.group(1) if m else None


def fw_version(full_version: str, fwversion: str) -> str:
    """The version the web compares an uploaded firmware with: WA.v8.7.14."""
    return f"{fw_prefix(full_version)}.{fwversion or ''}"


def update_check_on(cfg: Config) -> bool:
    """"Check for Updates" (update.check.status): on when it is missing, as its web shows it."""
    value = cfg.get("update.check.status")
    return value is None or value == "enabled"


def fw_required(board: dict) -> str:
    """The oldest firmware the board takes (board.required_fw_version): v8.2.0."""
    n = _int(board.get("board.required_fw_version")) or 0
    return f"v{n >> 16 & 255}.{n >> 8 & 255}.{n & 255}"


# isCompatibleFw: the firmwares before the 30 and 60 MHz channel widths and those after.
_FW_RE = re.compile(r"^(?:\w+\.)v(\d+).(\d+)(?:\.(\d{1,4})[\.|-])?(?:[\.|-]?([a-zA-Z]+)(\d+)?)?",
                    re.ASCII)
_STAGES = ("devel", "test", "alpha", "beta", "rc")
_OLD = (((7, 0, 0, 0, 0), (7, 1, 6, 0, 0)), ((7, 2, 0, 0, 0), (7, 2, 0, 3, 4)),
        ((8, 0, 0, 0, 0), (8, 0, 0, 3, 5)))


def _fw_parts(version: str) -> tuple | None:
    m = _FW_RE.match(version or "")
    if not m:
        return None
    stage = (m.group(4) or "").lower()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0),
            _STAGES.index(stage) if stage in _STAGES else len(_STAGES), int(m.group(5) or 0))


def fw_compatible(current: str, uploaded: str) -> bool:
    """The web's isCompatibleFw: both on the same side of the 30/60 MHz support (or a version
    it cannot read)."""
    a, b = _fw_parts(current), _fw_parts(uploaded)
    if not a or not b:
        return True

    def old(v):
        return any(lo <= v <= hi for lo, hi in _OLD)
    return old(a) == old(b)


def fw_uploaded_text(version: str, third_party: bool, current: str, chanbw: int | None) -> str:
    """The web's notice of an uploaded firmware, with its warnings."""
    lines = [tr("Uploaded Firmware Version: {version}", version=version)]
    if third_party:
        lines.append(tr("WARNING: Third-party firmware detected! Ensure you are familiar with "
                        "recovery procedure."))
    elif chanbw in (30, 60):
        if not fw_compatible(current, version):
            lines.append(tr("WARNING: {width} MHz channel width is incompatible with uploaded "
                            "firmware. Access to device may be lost.", width=chanbw))
    elif current == version:
        lines.append(tr("WARNING: Device already has the same firmware version."))
    return "\n".join(lines)


_CORRUPTED = (99, 100, 102, 103, 104, 106, 107, 108, 110, 113)


def fw_error(err, prefix: str, board: dict) -> str:
    """What the web says when the device rejects an uploaded firmware (its error code)."""
    code = _int(err)
    if code == 45:
        return tr("Incompatible firmware file uploaded. Please upload correct file and try again.")
    if code == 68:
        return tr("Invalid firmware file. Please check that you have selected the correct "
                  "firmware architecture ({series}) for your device.", series=prefix)
    if code in (101, 112):
        return tr("Invalid firmware file uploaded. Please upload correct file and try again.")
    if code == 114:
        return tr("Invalid or corrupted firmware file uploaded. Please upload correct file and "
                  "try again.")
    if code == 98:
        return tr("Incorrect firmware version. Please upload {version} or newer and try again.",
                  version=fw_required(board))
    if code in _CORRUPTED:
        return tr("Corrupted firmware file uploaded. Please upload correct file and try "
                  "again.") + f" ({code})"
    return tr("Firmware image check failed. Error code") + f": {'?' if err is None else err}"


# -- the version as the webs write it ----------------------------------------------------

_WEB_FW_RE = re.compile(r"^\w+\.(?:[^.]+\.)?(v.*?)(?:\.[^.]+\.[^.]+\.[^.]+)?$", re.I)


def fmt_version(version: str) -> str | None:
    """The web's version: v8.7.25 (WA) from WA.ar934x.v8.7.25.48568.260730.1010."""
    if not version:
        return None
    parts = str(version).split(".")
    if len(parts) > 3:
        start = next((i for i, p in enumerate(parts) if p.startswith("v")), 0)
        short = ".".join(parts[start:max(start + 1, len(parts) - 3)])
        prefix = parts[0]
        return f"{short} ({prefix})" if prefix and not prefix.startswith("v") else short
    return str(version)


def web_fw_version(text) -> str:
    """The web's getFirmwareVersion: v8.7.25 from WA.ar934x.v8.7.25.48568.260730.1010."""
    m = _WEB_FW_RE.match(str(text or ""))
    return m.group(1) if m else ""


# -- its check for updates, from this computer --------------------------------------------

_UPDATE_KEYS = ("update", "date", "version", "security", "changelog", "url", "checksum")


def update_query(board: dict, full_version: str, cfg: Config) -> dict:
    """What the device sends when it checks for updates (its update_check): its board's sysid,
    its firmware's full version, the md5 of its MAC (board.hwaddr) and its web's language."""
    return {"sysid": board.get("board.sysid") or "", "fwver": full_version or "",
            "hash": hashlib.md5((board.get("board.hwaddr") or "").encode()).hexdigest(),
            "lang": cfg.get("gui.language") or "en_US"}


def update_answer(data: bytes) -> dict:
    """The update server's answer as the device takes it: JSON with "update" and, when it
    offers a firmware ("true"), the rest of its keys (date, version, security, changelog, url,
    checksum; with nothing newer, it answers {"update":"false"} alone). AirOSError if not, with
    its words and the answer itself."""
    text = data.decode("utf-8", "replace")
    try:
        info = json.loads(text)
    except ValueError:
        info = None
    answer = " ".join(text.split())[:160] or '""'
    if not isinstance(info, dict):
        raise AirOSError(tr("The update server's answer is not valid (received invalid json): "
                            "{answer}", answer=answer))
    missing = [k for k in (_UPDATE_KEYS if has_update(info) else ("update",)) if k not in info]
    if missing:
        raise AirOSError(tr("The update server's answer is not valid (received incomplete "
                            "update info: missing key: {key}): {answer}", key=missing[0],
                            answer=answer))
    if has_update(info) and urllib.parse.urlsplit(str(info["url"])).scheme not in ("http",
                                                                                   "https"):
        raise AirOSError(tr("The update server's answer is not valid (its url is not an "
                            "http(s) address): {answer}", answer=answer))
    return info


def has_update(info: dict) -> bool:
    """Whether it offers a newer firmware ("update": "true", as its server writes it)."""
    return str(info.get("update")).strip().lower() in ("true", "1", "yes")


_HEADERS = {"User-Agent": f"airos-tui/{__version__}"}
# What a request of this computer to Ubiquiti can fail with: the network, the address, and an
# answer cut short or broken (urllib hands http.client's over as they are).
_NET_ERRORS = (OSError, ValueError, http.client.HTTPException)


def _http(url: str, timeout: float, method: str = "GET"):
    """A request of this computer to Ubiquiti (http or https only; its certificate checked)."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(tr("It is not an http(s) address: {url}", url=url))
    req = urllib.request.Request(url, method=method, headers=_HEADERS)
    return urllib.request.urlopen(req, timeout=timeout)       # noqa: S310 (http/s only)


def _reason(e: Exception) -> str:
    return str(getattr(e, "reason", None) or e)


def _logged(method: str, url: str, started: float, resp=None, data=None, size=None,
            error=None) -> None:
    """With --debug, a request of this computer to Ubiquiti, as those to the device."""
    if debugging():
        debug_request(method, url, _HEADERS, None, time.monotonic() - started,
                      resp.status if resp is not None else None,
                      resp.headers if resp is not None else None, data,
                      error=None if error is None else _reason(error), size=size)


def _fetch(url: str, timeout: float, limit: int) -> bytes:
    """GET url from this computer: its body, at most limit bytes."""
    started = time.monotonic()
    try:
        with _http(url, timeout) as resp:
            data = resp.read(limit)
    except _NET_ERRORS as e:
        _logged("GET", url, started, error=e)
        raise
    _logged("GET", url, started, resp, data)
    return data


def check_update_here(query: dict, timeout: float = 20) -> dict:
    """The device's check for updates, made from this computer: Ubiquiti's update server
    asked what the device would ask (update_query). Its answer (update_answer), or
    AirOSError if it cannot be reached from here either."""
    try:
        data = _fetch(FW_CHECK_URL + "?" + urllib.parse.urlencode(query), timeout, 100_000)
    except _NET_ERRORS as e:
        raise AirOSError(tr("Could not reach the update server from this computer either: "
                            "{error}", error=_reason(e))) from None
    return update_answer(data)


def fetch_text(url, timeout: float = 15, limit: int = 500_000) -> str | None:
    """A text of Ubiquiti's (its release notes), from this computer; None if it cannot be
    had."""
    if not isinstance(url, str):
        return None
    try:
        return _fetch(url, timeout, limit).decode("utf-8", "replace").strip() or None
    except _NET_ERRORS:
        return None


def fetch_license(timeout: float = 15) -> str:
    """The license the device's own download shows (its fw_eula_url), from this computer;
    AirOSError with why it could not be had."""
    try:
        text = _fetch(FW_EULA_URL, timeout, 500_000).decode("utf-8", "replace").strip()
    except _NET_ERRORS as e:
        raise AirOSError(f"{FW_EULA_URL}: {_reason(e)}") from None
    if not text:
        raise AirOSError(f"{FW_EULA_URL}: {tr('(empty)')}")
    return text


def firmware_file(info: dict) -> str:
    """Where the firmware of an answer is kept here: FW_DIR, with the name of its file."""
    name = os.path.basename(urllib.parse.urlsplit(str(info.get("url") or "")).path)
    return os.path.join(FW_DIR, re.sub(r"[^A-Za-z0-9._-]", "_", name) or "firmware.bin")


def file_md5(path: str) -> str | None:
    """The md5 of a file of this computer, or None if it cannot be read."""
    digest = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def firmware_size(url: str, timeout: float = 10) -> int | None:
    """The size of a firmware file, as its server says (HEAD), or None."""
    started = time.monotonic()
    try:
        with _http(url, timeout, "HEAD") as resp:
            _logged("HEAD", url, started, resp)
            return _int(resp.headers.get("Content-Length"))
    except _NET_ERRORS as e:
        _logged("HEAD", url, started, error=e)
        return None


def download_firmware(info: dict, progress=None, timeout: float = 30) -> str:
    """The firmware of an update server's answer, here: in FW_DIR (if a file with its checksum
    is there already, that one), downloaded from its url with progress(bytes, total or None)
    as it comes, and checked against its checksum (the md5 of the file, as the device checks
    it). If it is not that one, or something fails, nothing of it stays: AirOSError says why.
    Returns its path."""
    url, want = str(info.get("url") or ""), str(info.get("checksum") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", want):
        raise AirOSError(tr("The update server gave no checksum for the file: it is not "
                            "downloaded."))
    path = firmware_file(info)
    if file_md5(path) == want:
        return path
    tmp, done, digest, kept = path + ".part", 0, hashlib.md5(), False
    started, resp, total = time.monotonic(), None, None
    try:
        os.makedirs(FW_DIR, exist_ok=True)
        with _http(url, timeout) as resp, open(tmp, "wb") as out:
            total = _int(resp.headers.get("Content-Length"))
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                done += len(chunk)
                if done > FW_MAX or (total or 0) > FW_MAX:
                    raise AirOSError(tr("The firmware file is too large ({size} MB).",
                                        size=round(max(done, total or 0) / 1048576)))
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:              # (the server said it sends more)
            raise AirOSError(tr("The download was cut short ({done} of {total} bytes): it is "
                                "not kept.", done=done, total=total))
        if digest.hexdigest() != want:
            raise AirOSError(tr("The downloaded file is not the firmware the update server "
                                "announced (its checksum does not match): it is not kept."))
        os.replace(tmp, path)
        kept = True
    except _NET_ERRORS as e:
        _logged("GET", url, started, error=e)
        raise AirOSError(tr("Could not download {url}: {error}", url=url,
                            error=_reason(e))) from None
    except AirOSError as e:                     # (not the one announced, or too large)
        _logged("GET", url, started, error=e)
        raise
    finally:
        if not kept and os.path.exists(tmp):
            os.unlink(tmp)
    _logged("GET", url, started, resp, size=done)          # (its bytes went to the file)
    return path
