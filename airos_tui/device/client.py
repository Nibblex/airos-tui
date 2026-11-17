"""The device's HTTP client: the same addresses its own web uses (login, configuration,
test mode, PPPoE, country, reboot)."""
from __future__ import annotations

import datetime as _dt
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import urllib.parse
import uuid
import warnings

from .. import __version__
from ..core.i18n import tr
from ..core.errors import AirOSError, AuthError, SetupRefused
from ..core.config import (Config, _fmt_countdown, _int, js_parse_float, js_parse_int,
                           looks_like_cfg, PROTECTED_RE, V8_STRIP_RE)
from ..core.debuglog import debug_request, debugging
from .limits import (Channel, DeviceLimits, parse_kv, parse_regdomain_v6, parse_regdomain_v8,
                     V6_CAPS_KEY)


USER_AGENT = f"airos-tui/{__version__}"
FORM = "application/x-www-form-urlencoded"
SUPPORT_TIMEOUT = 120     # seconds: the device puts the support file together first
FW_UPLOAD_TIMEOUT = 300   # seconds: a firmware file is some megabytes, and it is checked


def _json(data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig", "replace"))
    except ValueError:
        return None


def _bracket_ipv6(target: str) -> str:
    """An IPv6 address typed without its brackets ("fe80::1", "https://2001:db8::20"), with
    them: its colons are not a port."""
    scheme, sep, rest = target.rpartition("://")
    bare = rest.rstrip("/")
    try:
        ipaddress.IPv6Address(bare.split("%", 1)[0])
    except ValueError:
        return target
    return f"{scheme}{sep}[{bare}]"


def _elsewhere(location, scheme: str, port: int) -> bool:
    """Whether a redirect's Location sends to another web than scheme://…:port (another scheme
    or port); a path alone is the same one."""
    url = urllib.parse.urlsplit(str(location or ""))
    if not url.scheme:
        return False
    try:
        to_port = url.port or (443 if url.scheme == "https" else 80)
    except ValueError:                            # (not a port: not this web)
        return True
    return url.scheme != scheme or to_port != port


def _short(data: bytes, n: int = 160) -> str:
    text = re.sub(r"<[^>]+>", " ", data.decode("utf-8", "replace"))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n] + ("…" if len(text) > n else "")


def _v6_username_changeable(page: str) -> bool:
    """Whether airOS 6.3's login page lets the administrator's name be changed (while it is
    ubnt, with the factory password). Its label then says «Administrator User Name»,
    otherwise «User Name», as its field's realname always does: in its page's language, so
    the two are compared (they differ in each of its languages). In English, without them."""
    label = re.search(r'<label for="username">(.*?)</label>', page, re.S)
    real = re.search(r'<input[^>]*\bid="username"[^>]*\brealname="([^"]*)"', page)
    if not (label and real):
        return "Administrator User Name" in page

    def plain(s):
        return html.unescape(re.sub(r"<[^>]*>", "", s)).strip()
    shown = plain(label.group(1)).removesuffix(":")          # (the page adds it)
    return shown.strip() != plain(real.group(1))


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE        # the devices use self-signed certificates
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")   # airOS 6 only offers old ciphers
    except ssl.SSLError:
        pass
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (ValueError, AttributeError):
            pass
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    return ctx


def _multipart(fields, files) -> tuple[bytes, str]:
    boundary = "----airostui" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                f"\r\n\r\n").encode() + value.encode("utf-8") + b"\r\n"
    for name, filename, content in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream'
                f"\r\n\r\n").encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def parse_pppoe_v8(info) -> dict:
    """GET /api/pppoe/info on airOS 8: {"interfaces": [{name, iplocal, ipremote, dns1, dns2,
    duration (s)…}]} or {"error": "PPPoE is not enabled." | "Not connected"}."""
    if not isinstance(info, dict):
        return {"error": tr("Invalid response.")}
    if info.get("error"):
        return {"error": str(info["error"])}
    ifs = info.get("interfaces")
    first = ifs[0] if isinstance(ifs, list) and ifs and isinstance(ifs[0], dict) else info
    out = {k: str(first.get(k) or "") for k in ("name", "iplocal", "ipremote", "dns1", "dns2")}
    out["duration"] = _fmt_countdown(first.get("duration"))
    return out if out["iplocal"] else {"error": "Not connected"}


def parse_pppoe_v6(page: str) -> dict:
    """pppinfo.cgi on airOS 6. Its labels are translated to the web's language, so it is
    read by position: name, local IP, remote IP, DNS 1, [DNS 2], connection time."""
    m = re.search(r'<td valign="top" colspan="2">(.*?)</td>', page, re.S)
    if m:
        return {"error": html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
                or tr("No PPPoE data.")}
    vals = [html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
            for v in re.findall(r'<td class="f">.*?</td>\s*<td>(.*?)</td>', page, re.S)]
    if len(vals) < 5:
        return {"error": tr("Could not read the PPPoE status.")}
    out = dict(zip(("name", "iplocal", "ipremote", "dns1"), vals[:4]), dns2="")
    rest = vals[4:]
    try:
        ipaddress.IPv4Address(rest[0])
        out["dns2"], rest = rest[0], rest[1:]
    except ValueError:
        pass
    out["duration"] = rest[0] if rest else ""
    return out


def setup_steps(info: dict | None) -> tuple[bool, bool]:
    """The steps of its web's first access (LoginViewMain.collectSteps), from what
    /api/info/public says (see public_info): (the country, the language and its terms, while its
    setup is not done: setup_data without setup_complete; the administrator's user and
    password, while it has the factory ones: default_password). Its API lets the factory user
    and password in meanwhile, but its web never logs in with them alone."""
    if not isinstance(info, dict):
        return False, False
    first = isinstance(info.get("setup_data"), dict) and not info.get("setup_complete")
    return first, bool(info.get("default_password"))


class AirOS:
    """HTTP session with an airOS 8 or airOS 6 antenna."""

    def __init__(self, target: str, username: str, password: str,
                 scheme: str | None = None, timeout: float = 12.0):
        self.username, self.password, self.timeout = username, password, timeout
        t = _bracket_ipv6(target.strip())
        try:
            if "://" in t:
                u = urllib.parse.urlsplit(t)
                scheme = u.scheme.lower()
            else:
                u = urllib.parse.urlsplit("//" + t)
        except ValueError:                    # ("[::1" or "http://[x")
            raise AirOSError(tr("Invalid address: {target!r}", target=target)) from None
        if scheme not in (None, "http", "https"):
            raise AirOSError(tr("Unsupported scheme: {scheme} (use http or https)",
                                scheme=scheme))
        if not u.hostname:
            raise AirOSError(tr("Invalid address: {target!r}", target=target))
        try:
            self._port_arg = u.port
        except ValueError:
            raise AirOSError(tr("Invalid port in {target!r}", target=target)) from None
        self.host = u.hostname
        self.scheme = scheme                  # None = detect
        self.port = self._port_arg or (443 if scheme == "https" else 80)
        self.cookies: dict[str, str] = {}
        self.csrf: str | None = None
        self.auth_kind: str | None = None     # "api" (airOS 8) or "cgi" (airOS 6)
        self.flavor: str | None = None        # "v8" or "v6": which config paths to use
        self.fw = ""
        self.fw_major = 0
        self.notes: list[str] = []
        self.login_info: dict = {}            # airOS 8 login JSON (boardinfo, rd…)
        self.backup_loaded = False             # airOS 8: a backup uploaded, not applied yet
        # HTTPS did not answer and its web does over HTTP (without sending to HTTPS): the user
        # and password would go unencrypted. Known after probe(), before anything is sent.
        self.http_fallback = False
        self._probed = False
        self._ctx = _tls_context()
        # (one request at a time: a speed test's pings go from another thread, and a login
        # there must not change the session under a request here)
        self._lock = threading.RLock()

    # -- HTTP helpers -------------------------------------------------------

    @property
    def base(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = 443 if self.scheme == "https" else 80
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{host}{port}"

    def _url(self, path: str) -> str:
        """Where a request goes (for --debug: also before its protocol is known)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        scheme = "https" if self.scheme == "https" else "http"
        port = "" if self.port == (443 if scheme == "https" else 80) else f":{self.port}"
        return f"{scheme}://{host}{port}{path}"

    def _raw(self, method: str, path: str, body=None, headers=None, timeout=None,
             auth: bool = True):
        """One request. auth=False: without the session (its cookie and CSRF token), for a
        probe of an address that may be another device."""
        tmo = timeout or self.timeout
        try:
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(self.host, self.port, timeout=tmo,
                                                   context=self._ctx)
            else:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=tmo)
        except (http.client.HTTPException, ValueError) as e:   # ("192.168.1 .20")
            raise AirOSError(tr("Invalid address: {target!r}", target=self.host)) from e
        hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", "Connection": "close"}
        if method == "POST":
            hdrs["Origin"] = self.base
            hdrs["Referer"] = self.base + "/"
        if self.cookies and auth:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.csrf and auth:
            hdrs["X-CSRF-ID"] = self.csrf
        if headers:
            hdrs.update(headers)
        if isinstance(body, str):
            body = body.encode("utf-8")
        started = time.monotonic()
        try:
            try:
                conn.request(method, path, body=body, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read()
            except ssl.SSLError as e:
                raise AirOSError(tr("TLS error with {host}: {reason}", host=self.host,
                                    reason=e.reason or e)) from e
            except TimeoutError as e:
                raise AirOSError(tr("{host} did not answer in time.", host=self.host)) from e
            except http.client.HTTPException as e:
                raise AirOSError(tr("{host} cut the connection or answered badly ({error}).",
                                    host=self.host, error=repr(e))) from e
            except UnicodeError as e:            # ("192.168..20": not a valid name)
                raise AirOSError(tr("Invalid address: {target!r}", target=self.host)) from e
            except OSError as e:
                raise AirOSError(tr("Could not connect to {host}:{port} ({reason}).",
                                    host=self.host, port=self.port,
                                    reason=e.strerror or e)) from e
            finally:
                conn.close()
        except AirOSError as e:
            if debugging():                      # (--debug)
                debug_request(method, self._url(path), hdrs, body,
                              time.monotonic() - started, error=str(e))
            raise
        if debugging():
            debug_request(method, self._url(path), hdrs, body, time.monotonic() - started,
                          resp.status, resp.headers, data)
        # (a probe without the session takes none either: what answers may be another device)
        for sc in (resp.headers.get_all("Set-Cookie") or []) if auth else []:
            pair = sc.split(";", 1)[0]
            if "=" in pair:
                k, v = (x.strip() for x in pair.split("=", 1))
                if v and v.lower() != "deleted":
                    self.cookies[k] = v
                else:
                    self.cookies.pop(k, None)
        return resp.status, resp.headers, data

    @staticmethod
    def _session_lost(status: int, headers) -> bool:
        if status in (401, 403):
            return True
        if status in (301, 302, 303, 307):
            loc = (headers.get("Location") or "").lower()
            return any(x in loc for x in ("login.cgi", "cookiechecker", "nocookies"))
        return False

    def _req(self, method, path, body=None, headers=None, timeout=None):
        """Authenticated request; if the session expired, log in again once."""
        with self._lock:
            for attempt in (1, 2):
                status, hdrs, data = self._raw(method, path, body, headers, timeout)
                if not self._session_lost(status, hdrs):
                    return status, hdrs, data
                if attempt == 1:
                    self.login()
        raise AuthError(tr("The device rejected the request (read-only user?)."))

    def _ms(self) -> str:
        return str(int(time.time() * 1000))

    # -- connection -----------------------------------------------------------

    def _detect_scheme(self) -> None:
        errors = []
        for sch in ([self.scheme] if self.scheme else ["https", "http"]):
            self.scheme = sch
            self.port = self._port_arg or (443 if sch == "https" else 80)
            try:
                _, hdrs, _ = self._raw("GET", "/", timeout=6)
            except AirOSError as e:
                errors.append(f"{sch.upper()}: {e}")
                continue
            loc = hdrs.get("Location") or ""
            if sch == "http" and loc.lower().startswith("https://"):
                try:
                    port = urllib.parse.urlsplit(loc).port or 443
                except ValueError:                # (not a URL there: its usual port)
                    port = 443
                self.scheme, self.port = "https", port
                self.notes.append(tr("The device requires HTTPS; HTTPS is used."))
            elif sch == "http" and errors:
                self.http_fallback = True
            return
        raise AirOSError(tr("Could not connect to the device:") + "\n  " + "\n  ".join(errors))

    def probe(self) -> None:
        """Which protocol and port its web answers on, without logging in (only GET /): after
        it, http_fallback says whether the user and password would go unencrypted."""
        if not self._probed:
            self._detect_scheme()
            self._probed = True

    def connect(self) -> dict:
        self.probe()
        self.login()
        status = self.status()
        self.set_firmware(status)
        if self.fw_major >= 7 or (self.fw_major == 0 and self.auth_kind == "api"):
            self.flavor = "v8"
        else:
            self.flavor = "v6"
        if self.fw_major == 7:
            self.notes.append(tr("airOS 7 is not tested: the airOS 8 paths are used."))
        elif 0 < self.fw_major < 6:
            self.notes.append(tr("airOS 5 or older is not tested: the airOS 6 paths are "
                                 "used."))
        elif self.fw_major == 0:
            self.notes.append(tr("The firmware version was not recognized ({fw!r}).",
                                 fw=self.fw))
        return status

    def set_firmware(self, status: dict) -> None:
        """Its firmware, as status.cgi says it (fw: "v8.7.19"; fw_major: 8): as it connects,
        and again after an update."""
        host = status.get("host") if isinstance(status.get("host"), dict) else {}
        self.fw = str(host.get("fwversion") or "")
        m = re.match(r"v?(\d+)", self.fw)
        self.fw_major = int(m.group(1)) if m else 0

    def login(self) -> None:
        if self._login_api():
            self.auth_kind = "api"
        else:
            self._login_cgi()
            self.auth_kind = "cgi"

    def _login_api(self, extra: dict | None = None) -> bool:
        """airOS 8 login (with extra, its first setup's fields). Returns False if the device
        has no /api/auth."""
        self.cookies.clear()
        self.csrf = None
        body = urllib.parse.urlencode(dict({"username": self.username, "password": self.password},
                                           **(extra or {})))
        status, hdrs, data = self._raw("POST", "/api/auth", body, {"Content-Type": FORM})
        info = _json(data)
        if status == 200 and isinstance(info, dict):
            if info.get("error"):
                raise AuthError(tr("The device refused access: {error}", error=info["error"]),
                                str(info["error"]))
            csrf = hdrs.get("X-CSRF-ID")
            if csrf:
                self.csrf = csrf
                self.login_info = info
                return True
            return False
        if status in (401, 403) and isinstance(info, dict):
            detail = info.get("error") or info.get("message")
            if detail:
                raise AuthError(tr("Wrong user or password ({detail}).", detail=detail),
                                str(detail))
            raise AuthError(tr("Wrong user or password."))
        return False

    def public_info(self) -> dict | None:
        """airOS 8: what its web reads before its login (/api/info/public, without a session),
        with its languages: whether the device waits for its first setup (see setup_steps).
        None where there is no such page (airOS 6)."""
        try:
            status, _, data = self._raw("GET", "/api/info/public?include_langs=true&lang=")
        except AirOSError:
            return None
        info = _json(data)
        return info if status == 200 and isinstance(info, dict) else None

    def first_setup(self, fields: dict) -> None:
        """Its web's first access (airOS 8): its login, POST /api/auth, with the setup's fields
        (device_setup; new_ccode and new_lang; new_username and new_password, or new_config:
        a backup, as text), sent with the user and password set (while it has the factory ones,
        those, as its web sends them). The device sets itself up and opens the session; the
        program then logs in with the new user and password. From a backup, the device keeps
        it waiting to be applied (as the web's "Upload Configuration"), and until then it is
        logged in with the factory ones. SetupRefused: what the device answered instead."""
        try:
            done = self._login_api(fields)
        except AuthError as e:
            if not e.detail:
                raise
            raise SetupRefused(tr("The device did not take its first setup: {error}",
                                  error=e.detail)) from None
        if not done:
            raise AirOSError(tr("The device did not take its first setup (/api/auth)."))
        self.auth_kind = "api"
        if fields.get("new_username"):
            self.username, self.password = fields["new_username"], fields["new_password"]

    def _login_page(self) -> str:
        """airOS 6's login page, with the session cookie it gives (as _login_cgi starts)."""
        self.cookies.clear()
        self.csrf = None
        _, _, page = self._raw("GET", "/login.cgi")
        if not self._has_session_cookie():
            self._raw("GET", "/")
            _, _, page = self._raw("GET", "/login.cgi")
        return (page or b"").decode("utf-8", "replace")

    def v6_first_login_info(self) -> dict | None:
        """airOS 6.3's login page on its first boot (first_login: the country, the language of
        its web and its terms) or while it has the factory password (password2: the new one,
        and the administrator's name while it is ubnt), which its web asks before anything
        else: what it asks, with its lists. None otherwise (or not airOS 6)."""
        try:
            text = self._login_page()
        except AirOSError:
            return None
        if not self._has_session_cookie():
            return None
        first = re.search(r"first_login\s*:\s*true", text) is not None
        new_password = re.search(r'name="password2"', text) is not None
        if not (first or new_password):
            return None

        def options(select_id):
            m = re.search(rf'<select[^>]*id="{select_id}"[^>]*>(.*?)</select>', text, re.S)
            return [(html.unescape(v), html.unescape(re.sub(r"<[^>]*>", "", n)).strip(),
                     "selected" in attrs)
                    for v, attrs, n in re.findall(r'<option value="([^"]*)"([^>]*)>(.*?)</option>',
                                                  m.group(1), re.S)] if m else []
        countries = [(v, n) for v, n, _ in options("country") if v != "0"]
        langs = options("ui_language")
        terms = re.search(r"<strong>[^<]*</strong><p>(.*?)<br>", text, re.S)
        preset = re.search(r'country\s*:\s*"(\d*)"', text)
        return {"first": first, "new_password": new_password,
                "new_username": _v6_username_changeable(text),
                "countries": countries, "country": preset.group(1) if preset else "",
                "languages": [(v, n) for v, n, _ in langs],
                "language": next((v for v, _, sel in langs if sel), "en_US"),
                "terms": html.unescape(re.sub(r"<[^>]*>", "", terms.group(1))).strip()
                if terms else ""}

    def v6_first_login(self, fields: dict) -> None:
        """Its first login, as its page's form sends it (POST login.cgi: username, password and
        password2, country, ui_language): logged in with the factory password while it has it,
        it sets the new one (and the administrator's name) and the country. From then on the
        program logs in with them."""
        self._login_page()
        body = urllib.parse.urlencode(dict({"uri": "/index.cgi", "lang_changed": "no"},
                                           **fields))
        status, hdrs, page = self._raw("POST", "/login.cgi", body,
                                       {"Content-Type": FORM, "Referer": self.base + "/login.cgi"})
        loc = (hdrs.get("Location") or "").lower()
        if status not in (301, 302, 303) or "login.cgi" in loc:
            m = re.search(rb'<div id="errmsg" class="error">(.*?)</div>', page or b"", re.S)
            reason = _short(m.group(1)) if m else ""
            raise AuthError(tr("The device did not take its first login{reason}.",
                               reason=f" ({reason})" if reason else ""))
        self.username, self.password = fields["username"], fields["password"]

    def _login_cgi(self) -> None:
        """airOS 6 login (/login.cgi form + AIROS_<MAC> cookie)."""
        self.cookies.clear()
        self.csrf = None
        _, _, page = self._raw("GET", "/login.cgi")
        if not self._has_session_cookie():
            self._raw("GET", "/")
            _, _, page = self._raw("GET", "/login.cgi")
        if not self._has_session_cookie():
            raise AirOSError(tr("The device did not answer like an airOS (it gave no session "
                                "cookie)."))
        first_setup = re.search(rb"first_login\s*:\s*true", page or b"") is not None
        # 6.3 does not let the factory password stay: its login page asks for a new one
        # (twice: password2) and lets nobody in until it has it. Changing it is not done here.
        new_password = re.search(rb'name="password2"', page or b"") is not None
        body = urllib.parse.urlencode({"uri": "/index.cgi", "username": self.username,
                                       "password": self.password})
        status, hdrs, _ = self._raw("POST", "/login.cgi", body,
                                    {"Content-Type": FORM, "Referer": self.base + "/login.cgi"})
        loc = (hdrs.get("Location") or "").lower()
        if status not in (301, 302, 303) or "login.cgi" in loc:
            if first_setup and new_password:
                raise AuthError(tr("The device is on its first boot: log in once through its "
                                   "web to choose the country, accept the terms and change "
                                   "the factory password, and connect with the new one."))
            if first_setup:
                raise AuthError(tr("The device is on its first boot. Log in once through the "
                                   "web to choose the country and accept the terms, and try "
                                   "again."))
            if new_password:
                raise AuthError(tr("The device still has its factory password, and its web "
                                   "asks to change it before anything else: log in once "
                                   "through its web, choose another one and connect with "
                                   "it."))
            raise AuthError(tr("Wrong user or password."))
        status, hdrs, _ = self._raw("GET", "/index.cgi")
        if self._session_lost(status, hdrs):
            raise AuthError(tr("The device did not accept the session after logging in."))

    def _has_session_cookie(self) -> bool:
        return any(k.startswith("AIROS_") for k in self.cookies)

    # -- reading ----------------------------------------------------------------

    def retarget(self, host: str) -> None:
        """The antenna changed its IP: keep talking to it at the new address."""
        self.host = host

    def retarget_web(self, scheme: str, port: int) -> None:
        """Its web server moved (another port, HTTPS on or off): keep talking to it there."""
        self.scheme, self.port, self._port_arg = scheme, port, port

    def ping(self, host: str | None = None, web: tuple[str, int] | None = None,
             moved_ok: bool = True) -> bool:
        """Does the device's web server answer (at host, or at web: (scheme, port))? It needs
        no session. moved_ok=False: not if it only sends to another web (another scheme or
        port: its HTTP once its HTTPS is on), only to a page of its own (its login)."""
        with self._lock:
            saved = self.host, self.scheme, self.port
            self.host = host or self.host
            if web:
                self.scheme, self.port = web
            try:
                status, headers, _ = self._raw("GET", "/poll.cgi", timeout=4, auth=False)
                moved = 300 <= status < 400 and _elsewhere(headers.get("Location"), self.scheme,
                                                           self.port)
            except AirOSError:
                return False
            finally:
                self.host, self.scheme, self.port = saved
        return status < 500 and (moved_ok or not moved)

    def status_at(self, host: str) -> dict | None:
        """status.cgi at host with this session, as it is: without logging in there (it may be
        another device, which must not get the user and password) and without taking
        anything from its answer (its cookies would replace this session's). None if it does
        not give it: another device does not know this session."""
        with self._lock:
            saved = self.host, dict(self.cookies), self.csrf
            self.host = host
            try:
                status, hdrs, data = self._raw("GET", "/status.cgi")
            except AirOSError:
                return None
            finally:
                self.host, self.cookies, self.csrf = saved
        info = _json(data)
        if status != 200 or self._session_lost(status, hdrs) or not isinstance(info, dict):
            return None
        return info

    def status(self) -> dict:
        status, _, data = self._req("GET", "/status.cgi")
        info = _json(data)
        if status != 200 or not isinstance(info, dict):
            raise AirOSError(tr("status.cgi did not return valid data (HTTP {status}).",
                                status=status))
        return info

    def get_config(self) -> str:
        path = "/getcfg.cgi" if self.flavor == "v8" else "/cfg.cgi"
        status, hdrs, data = self._req("GET", path)
        text = data.decode("utf-8", "replace")
        if status != 200 or not looks_like_cfg(text):
            raise AirOSError(tr("Could not read the configuration ({path} → HTTP {status}).",
                                path=path, status=status))
        # airOS 8, as its web reads it: "1" while a configuration backup uploaded with
        # cfgupl.cgi waits to be applied or discarded (what it hands over then is that one).
        self.backup_loaded = self.flavor == "v8" and (hdrs or {}).get("X-Cfg-Modified") == "1"
        return text

    def board_info(self) -> dict[str, str]:
        """The device's board.info: hardware limits, antennas, channel widths…"""
        info = self.login_info.get("boardinfo")      # airOS 8 hands it over at login
        if isinstance(info, dict):
            return {str(k): str(v) for k, v in info.items()}
        if isinstance(info, str) and "=" in info:
            return parse_kv(info)
        status, _, data = self._req("GET", "/getboardinfo.sh")   # airOS 6
        text = data.decode("utf-8", "replace") if status == 200 else ""
        if "radio." not in text:
            return {}
        info = parse_kv(text)
        # Without the radio's capabilities, which the distance limits depend on (its fast
        # clock): the System page has them.
        status, _, page = self._req("GET", "/system.cgi")
        m = re.search(rb"var radio_caps = (\d+);", page) if status == 200 else None
        if m:
            info[V6_CAPS_KEY] = m.group(1).decode()
        return info

    def regdomain(self, cc: int, bw: int) -> list[Channel] | None:
        """Channels the country allows, with their maximum EIRP (what the web uses)."""
        if self.flavor == "v8":
            rd = self.login_info.get("rd")
            if rd and _int(self.login_info.get("ccode")) == cc:
                channels = parse_regdomain_v8(rd)
                if channels:
                    return channels
            status, _, data = self._req("GET", f"/api/regdomain?cc={cc}")
            info = _json(data)
            if status == 200 and isinstance(info, dict):
                return parse_regdomain_v8(info.get("data")) or None
            return None
        status, _, data = self._req(
            "GET", f"/regdomain_by_country.cgi?country={cc}&chanbw={bw}")
        info = _json(data)
        if status == 200 and isinstance(info, list):
            chans = parse_regdomain_v6(info)
            # (it names the country but has no channels: none at that width; nothing at
            # all: not known)
            named = any(isinstance(x, str) and x.strip() for x in info)
            return chans if chans or named else None
        return None

    def set_country(self, cc: int) -> None:
        """airOS 8, as the web's "Change country" dialog: POST /api/regdomain/country. The
        device writes the country in its configuration, applies it and saves it at once
        (rc.softrestart save), without test mode."""
        status, _, data = self._req("POST", "/api/regdomain/country", f"ccode={int(cc)}",
                                    {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or not (isinstance(res, dict) and res.get("ok")):
            error = res.get("error") if isinstance(res, dict) else None
            raise AirOSError(tr("The device did not change the country: {error}",
                                error=error or f"HTTP {status}: {_short(data)}"))

    def change_password(self, new: str, current: str, read_only: bool = False) -> bool:
        """airOS 8, as the web's "Change Password" dialog: POST /pwd.cgi. The device checks
        the current password and saves the new one at once. False if the current one is
        wrong. read_only: the read-only account's password (its dialog asks for no current
        one)."""
        if self.flavor != "v8":
            raise AirOSError(tr("On airOS 6 the password is changed from the device's web "
                                "(System page)."))
        fields = {"change": "yes", "ro": "1" if read_only else "0", "pwd": new,
                  "oldPwd": "" if read_only else current, "newUsername": ""}
        status, _, data = self._req("POST", "/pwd.cgi", urllib.parse.urlencode(fields),
                                    {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="pwd.cgi",
                                status=status, body=_short(data)))
        if not res.get("success"):
            return False
        if not read_only:
            self.password = new                  # what logging in again needs from now on
        return True

    def ui_languages(self) -> tuple[str, list[dict]]:
        """airOS 8: the languages its web offers, as it reads them (/api/info/public with
        include_langs, without a session): (the one it uses, [{"lang", "code"}, …])."""
        status, _, data = self._raw("GET", "/api/info/public?include_langs=true&lang=")
        res = _json(data)
        if status != 200 or not isinstance(res, dict) or not isinstance(res.get("ui_langs"),
                                                                         list):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="api/info/public",
                                status=status, body=_short(data)))
        return str(res.get("ui_lang") or ""), [x for x in res["ui_langs"]
                                                if isinstance(x, dict) and x.get("code")]

    def management_radio(self, start: bool) -> None:
        """airOS 8 (Gen2 and Gen3 boards), as the web's Management Radio button: POST
        /api/provmode start (for 15 minutes) or stop. Its web server restarts right after."""
        status, _, data = self._req("POST", "/api/provmode", urllib.parse.urlencode(
            {"action": "start" if start else "stop"}), {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or not (isinstance(res, dict) and res.get("ok")):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="api/provmode",
                                status=status, body=_short(data)))

    def airview_data(self) -> dict:
        """airOS 8, as its airView dialog when it has no WebSocket: one read of
        /airviewdata.cgi (its spectrum: the last powers, their histogram and the ambient
        noise). With the management radio on, the device answers it empty."""
        status, _, data = self._req("GET", "/airviewdata.cgi", timeout=10)
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="airviewdata.cgi",
                                status=status, body=_short(data) or tr("(empty)")))
        return res

    def site_survey(self, again: bool = False) -> dict:
        """airOS 8, as the web's Site Survey: one read of /survey.json.cgi (again=True keeps
        the results of the scan in progress). Its scan_status says whether it goes on."""
        if self.flavor != "v8":
            raise AirOSError(tr("On airOS 6 the site survey is in the device's web (Tools)."))
        query = urllib.parse.urlencode({"iface": "ath0", "update": "last" if again else ""})
        status, _, data = self._req("GET", f"/survey.json.cgi?{query}", timeout=15)
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="survey.json.cgi", status=status, body=_short(data)))
        return res

    def countries(self) -> list[tuple[int, str]]:
        """The countries the web offers when changing it, [(code, name)] ([] if it cannot
        say): airOS 8 /api/info/public (setup_data.rd_countries), airOS 6 cc_change.cgi."""
        try:
            if self.flavor == "v8":
                status, _, data = self._req(
                    "GET", "/api/info/public?include_langs=false&force_setup_data=true")
                info = _json(data) if status == 200 else None
                setup = info.get("setup_data") if isinstance(info, dict) else None
                rows = setup.get("rd_countries") if isinstance(setup, dict) else None
                out = [(_int(r.get("code")), str(r.get("name") or ""))
                       for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
            else:
                status, _, data = self._req("GET", "/cc_change.cgi")
                text = data.decode("utf-8", "replace") if status == 200 else ""
                out = [(_int(code), html.unescape(name).strip())
                       for code, name in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)', text)]
        except AirOSError:
            return []
        return sorted({(cc, name) for cc, name in out if cc}, key=lambda x: x[1].lower())

    def device_limits(self) -> DeviceLimits:
        return DeviceLimits(self.flavor or "v8", self.board_info(), self.regdomain)

    def test_mode_info(self) -> dict | None:
        """{'active': bool, 'time_left': int|None}, or None if it cannot be told."""
        if self.flavor == "v8":
            status, _, data = self._req("GET", "/test_mode.cgi")
            info = _json(data)
            if status != 200 or not isinstance(info, dict):
                return None
            return {"active": str(info.get("active")).lower() in ("1", "true"),
                    "time_left": _int(info.get("time_left"))}
        # airOS 6 has no JSON for this: the system page carries it embedded.
        status, _, page = self._req("GET", "/system.cgi")
        if status != 200:
            return None
        m = re.search(rb"var testmode = '([^']*)'", page)
        if not m:
            return {"active": False, "time_left": None}
        t = re.search(rb"var testtime_left = (\d+)", page)
        return {"active": m.group(1) == b"on", "time_left": int(t.group(1)) if t else None}

    def pppoe_info(self) -> dict:
        """PPPoE session status (the same as the web's "PPPoE" window): {'error': text}
        or name, iplocal, ipremote, dns1, dns2 and duration."""
        if self.flavor == "v8":
            status, _, data = self._req("GET", "/api/pppoe/info")
            info = _json(data)
            if isinstance(info, dict) and info.get("error"):
                return {"error": str(info["error"])}
            if status != 200 or not isinstance(info, dict):
                raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                    path="/api/pppoe/info", status=status, body=_short(data)))
            return parse_pppoe_v8(info)
        status, _, page = self._req("GET", "/pppinfo.cgi")
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}.", path="pppinfo.cgi",
                                status=status))
        return parse_pppoe_v6(page.decode("utf-8", "replace"))

    # -- what its main page's device details show (airOS 8) --

    def _get_text(self, path: str) -> str:
        status, _, data = self._req("GET", path)
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path=path,
                                status=status, body=_short(data)))
        return data.decode("utf-8", "replace")

    def firewall_text(self) -> str:
        """GET /api/firewall/user-rules: the firewall rules the device has in use, as its
        listing gives them (its web's "Firewall" tab)."""
        return self._get_text("/api/firewall/user-rules")

    def port_forward_text(self) -> str:
        """GET /api/firewall/port-forward: the port forwarding rules in use, or the device's
        notice that it is off (in its own language)."""
        return self._get_text("/api/firewall/port-forward")

    def dhcp_leases(self) -> dict:
        """GET /api/dhcp-server/leases: {"aaData": [[MAC, IP, seconds left, name]…],
        "aaData6": [[identifier, IP, time left]…]} (just {"ok": true} with no server)."""
        status, _, data = self._req("GET", "/api/dhcp-server/leases")
        info = _json(data)
        if status != 200 or not isinstance(info, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="/api/dhcp-server/leases", status=status,
                                body=_short(data)))
        return info

    # -- writing ----------------------------------------------------------------

    def write_config(self, cfg: Config, test: bool) -> dict:
        """Send the whole configuration and apply it (in test mode if test=True)."""
        if self.flavor == "v8":
            return self._write_v8(cfg, test)
        return self._write_v6(cfg, test)

    def _write_v8(self, cfg: Config, test: bool) -> dict:
        # Like the web: without the masked passwords and with the edit stamp.
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        fields = {"cfgData": cfg.text(skip=V8_STRIP_RE)
                  + f"system.cfg.editor.webui={stamp}\n"}
        if test:
            fields["testmode"] = "yes"
        status, _, data = self._req("POST", "/writecfg.cgi", urllib.parse.urlencode(fields),
                                    {"Content-Type": FORM}, timeout=40)
        res = _json(data)
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="writecfg.cgi",
                                status=status, body=_short(data)))
        if isinstance(res, dict):
            if res.get("error") or res.get("code") not in (None, 0, "0"):
                raise AirOSError(tr("The device rejected the configuration: {reason}",
                                    reason=res.get("error") or res.get("message") or res))
            return res
        return {}

    def _v6_token(self, path: str) -> str | None:
        """airOS 6 requires a session token, hidden in the page, in every POST."""
        status, _, page = self._req("GET", path)
        m = re.search(rb'name="token"\s+value="([^"]*)"', page or b"")
        return m.group(1).decode() if (status == 200 and m) else None

    def _v6_post_with_token(self, path, fields, files=()):
        for attempt in (1, 2):
            token = self._v6_token(path)
            all_fields = ([("token", token)] if token is not None else []) + list(fields)
            body, ctype = _multipart(all_fields, list(files))
            status, hdrs, data = self._raw("POST", path, body, {"Content-Type": ctype},
                                           timeout=40)
            if not self._session_lost(status, hdrs):
                return status, data
            if attempt == 1:                      # the session expired: log in again once
                self.login()
        raise AuthError(tr("The device rejected the request (read-only user?)."))

    def _v6_post_form(self, path: str, fields: dict) -> tuple[int, bytes]:
        """airOS 6, as its web's buttons (Apply, Test, Discard, Restart Service): a POST with
        the fields and the session's token, form-encoded. Its CGIs act on nothing else: a GET,
        or a POST without the token, does nothing. The token comes from the System page (the
        one of the session: these pages hand none over)."""
        for attempt in (1, 2):
            token = self._v6_token("/system.cgi")
            if token is None:                     # (without it, it would do nothing)
                raise AirOSError(tr("{path} did not hand over its session token: nothing was "
                                    "sent.", path="system.cgi"))
            body = urllib.parse.urlencode(dict(fields, token=token))
            status, hdrs, data = self._raw("POST", path, body, {"Content-Type": FORM})
            if not self._session_lost(status, hdrs):
                return status, data
            if attempt == 1:                      # the session expired: log in again once
                self.login()
        raise AuthError(tr("The device rejected the request (read-only user?)."))

    def _write_v6(self, cfg: Config, test: bool) -> dict:
        before = Config(self.get_config(), self.flavor)
        intended = [(k, new) for k, _, new in before.diff(cfg) if not PROTECTED_RE.match(k)]
        status, page = self._v6_post_with_token(
            "/system.cgi", [("action", "cfgupload"), ("cfgupload", "Upload")],
            [("cfgfile", "system.cfg", cfg.text().encode("utf-8"))])
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}.", path="system.cgi",
                                status=status))
        m = re.search(rb'<div id="error">(.*?)</div>', page or b"", re.S)
        if m and _short(m.group(1)):
            raise AirOSError(tr("The device rejected the configuration: {reason}",
                                reason=_short(m.group(1))))
        # If the token does not match, the device silently ignores the upload: check it.
        after = Config(self.get_config(), self.flavor)
        wrong = [k for k, new in intended if after.get(k) != new]
        if wrong:
            raise AirOSError(tr("The device did not accept the uploaded configuration "
                                "(unchanged: {keys}).", keys=", ".join(wrong[:5])))
        status, _ = self._v6_post_form("/apply.cgi", {"testmode": "on" if test else ""})
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}.", path="apply.cgi",
                                status=status))
        return {}

    def confirm_test(self) -> None:
        """Save for good the changes that are in test mode."""
        if self.flavor == "v8":
            status, _, data = self._req("POST", "/apply.cgi", "apply=yes",
                                        {"Content-Type": FORM})
            res = _json(data)
            if status != 200 or (isinstance(res, dict)
                                 and res.get("code") not in (None, 0, "0")):
                raise AirOSError(tr("Could not confirm the changes: {body}",
                                    body=_short(data)))
        else:                     # (with the test lock there, it keeps what is running)
            status, _ = self._v6_post_form("/apply.cgi", {"testmode": ""})
            if status != 200:
                raise AirOSError(tr("{path} answered HTTP {status}.", path="apply.cgi",
                                    status=status))

    def discard_test(self) -> None:
        """Leave test mode and go back right away to the saved configuration."""
        if self.flavor == "v8":
            status, _, data = self._req("POST", "/discard.cgi", "d=0&testmode=yes",
                                        {"Content-Type": FORM})
            res = _json(data)
            if status != 200 or (isinstance(res, dict)
                                 and res.get("code") not in (None, 0, "0")):
                raise AirOSError(tr("Could not revert the changes: {body}",
                                    body=_short(data)))
        else:
            status, _ = self._v6_post_form("/discard.cgi", {"testmode": "on"})
            if status != 200:
                raise AirOSError(tr("{path} answered HTTP {status}.", path="discard.cgi",
                                    status=status))

    def pppoe_restart(self) -> None:
        """Cut the PPPoE session so that it reconnects (the web runs killall pppd)."""
        if self.flavor == "v8":
            status, _, data = self._req("POST", "/api/pppoe/restart", "",
                                        {"Content-Type": FORM})
            res = _json(data)
            if isinstance(res, dict) and res.get("error"):
                raise AirOSError(tr("The device did not restart PPPoE: {error}",
                                    error=res["error"]))
        else:
            status, data = self._v6_post_form("/pppinfo.cgi", {"action": "restartppp"})
        if status != 200:
            raise AirOSError(tr("Could not restart PPPoE (HTTP {status}): {body}",
                                status=status, body=_short(data)))

    def backup_config(self) -> bytes:
        """airOS 8, as the web's "Back Up Configuration": GET /cfg.cgi, the device's backup
        file (the configuration with the password hashes, and another part before it)."""
        status, _, data = self._req("GET", f"/cfg.cgi?timestamp={self._ms()}")
        if status != 200 or not data:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="cfg.cgi",
                                status=status, body=_short(data)))
        return data

    def upload_config(self, name: str, data: bytes) -> None:
        """airOS 8, as the web's "Upload Configuration": POST /cfgupl.cgi with the file
        (cfgfile). The device keeps it until it is applied (apply_config) or discarded
        (discard_config)."""
        name = re.sub(r'[\r\n"]', "_", name) or "system.cfg"
        body, ctype = _multipart([], [("cfgfile", name, data)])
        status, _, resp = self._req("POST", "/cfgupl.cgi", body, {"Content-Type": ctype},
                                    timeout=40)
        res = _json(resp)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("Configuration upload failed") + f" (HTTP {status}: "
                             f"{_short(resp)})")
        if str(res.get("code")) != "0":
            raise AirOSError(str(res.get("message") or tr("Configuration upload failed")))

    def apply_config(self) -> dict:
        """airOS 8: POST /apply.cgi (apply=yes), as its web applies an uploaded backup: the
        device uses it and saves it, without test mode."""
        status, _, data = self._req("POST", "/apply.cgi", "apply=yes", {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or not isinstance(res, dict) or str(res.get("code")) != "0":
            raise AirOSError(tr("Failed to apply changes") + f": {_short(data)}")
        return res

    def discard_config(self) -> dict:
        """airOS 8: POST /discard.cgi (d=0), as its web discards an uploaded backup. Its
        answer (with fast_restart: how it restarts)."""
        status, _, data = self._req("POST", "/discard.cgi", "d=0", {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or not isinstance(res, dict) or str(res.get("code")) != "0":
            raise AirOSError(tr("Failed to discard changes.") + f" {_short(data)}")
        return res

    # -- firmware (airOS 8, as the web's FwView and AutoUpdateController) --

    def warnings(self) -> dict:
        """GET /api/warnings, what its web reads as it starts (an uploaded firmware waiting,
        a reboot by the ping watchdog…). {} if the device does not say."""
        try:
            status, _, data = self._req("GET", "/api/warnings")
        except AirOSError:
            return {}
        res = _json(data)
        return res if status == 200 and isinstance(res, dict) else {}

    def device_discovery(self, duration_ms: int) -> list[dict]:
        """POST /discovery.cgi (airOS 8), as its web's Device Discovery: the antenna asks with
        Ubiquiti discovery on its networks for duration_ms and answers what it found. As its
        web, an answer that is not JSON is read again without what is not printable ASCII."""
        body = urllib.parse.urlencode({"discover": "y", "duration": duration_ms,
                                       "filter_aircube": ""})
        status, _, data = self._req("POST", "/discovery.cgi", body, {"Content-Type": FORM},
                                    timeout=duration_ms / 1000 + 5)
        res = _json(data)
        if res is None:
            res = _json(re.sub(rb"[^ -~]+", b"", data))
        if not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="discovery.cgi",
                                status=status, body=_short(data)))
        devices = res.get("devices")
        return [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []

    # -- the web's network tools (airOS 8) --

    def ip_scan(self) -> list[str]:
        """GET /ipscan.cgi: the addresses its web offers as destinations, one per line."""
        status, _, data = self._req("GET", "/ipscan.cgi")
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="ipscan.cgi",
                                status=status, body=_short(data)))
        return [line.strip() for line in data.decode("utf-8", "replace").strip().split("\n")
                if line.strip()]

    def ping_once(self, ip: str, size: int) -> dict:
        """POST /pingtest_action.cgi: one ping from the device, as its web asks for each one
        (code|host|time|ttl): {host, time (ms), ttl}, or timeout (-4), or error (the rest)."""
        body = urllib.parse.urlencode({"ip_addr": ip, "ping_size": size})
        status, _, data = self._req("POST", "/pingtest_action.cgi", body,
                                    {"Content-Type": FORM}, timeout=20)
        text = data.decode("utf-8", "replace").strip()
        if status != 200 or not text:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="pingtest_action.cgi", status=status, body=_short(data)))
        parts = text.split("|")
        code = js_parse_int(parts[0])
        if code == 0 and len(parts) > 3:
            return {"host": parts[1], "time": js_parse_float(parts[2]) or 0.0,
                    "ttl": js_parse_int(parts[3])}
        return {"host": ip, "timeout": True, "error": code != -4}

    def traceroute_hop(self, target: str, hop: int, resolve: bool) -> dict:
        """POST /api/traceroute: one hop, as its web asks for each one (up to 30)."""
        body = urllib.parse.urlencode({"target": target, "hop": hop,
                                       "resolve": "yes" if resolve else ""})
        status, _, data = self._req("POST", "/api/traceroute", body, {"Content-Type": FORM},
                                    timeout=30)
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="api/traceroute",
                                status=status, body=_short(data)))
        return res

    def speed_test(self, fields: dict) -> dict:
        """GET /sptest_action.cgi (its web's Speed Test, without the WebSocket: as its
        fallback): action remote, start, status or stop. The fields carry the other device's
        password, so no message repeats them."""
        status, _, data = self._req("GET", "/sptest_action.cgi?" + urllib.parse.urlencode(fields),
                                    timeout=15)
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="sptest_action.cgi", status=status, body=_short(data)))
        return res

    def dismiss_warnings(self) -> None:
        """POST /api/warnings/dismiss: its web's "Dismiss" of the emergency notice."""
        status, _, data = self._req("POST", "/api/warnings/dismiss", "", {"Content-Type": FORM})
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="api/warnings/dismiss", status=status, body=_short(data)))

    def _fw_post(self, path: str, fields: dict | None = None, timeout=None,
                 failed: str = "") -> dict:
        status, _, data = self._req("POST", path, urllib.parse.urlencode(fields or {}),
                                    {"Content-Type": FORM}, timeout=timeout)
        res = _json(data)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(f"{failed} (HTTP {status}: {_short(data)})")
        return res

    def fw_upload(self, name: str, data: bytes) -> dict:
        """POST /fwupl.cgi with the firmware (fwfile): the device checks it and keeps it. Its
        answer: code 0 with version and thirdParty, or the error code (err)."""
        name = re.sub(r'[\r\n"]', "_", name) or "firmware.bin"
        body, ctype = _multipart([], [("fwfile", name, data)])
        status, _, resp = self._req("POST", "/fwupl.cgi", body, {"Content-Type": ctype},
                                    timeout=FW_UPLOAD_TIMEOUT)
        res = _json(resp)
        if status != 200 or not isinstance(res, dict):
            raise AirOSError(tr("Firmware upload failed") + f" (HTTP {status}: {_short(resp)})")
        return res

    def fw_flash(self) -> dict:
        """POST /fwflash.cgi do_update=1: the device writes the firmware it keeps and
        reboots with it."""
        return self._fw_post("/fwflash.cgi", {"do_update": 1},
                             failed=tr("Firmware update request failed"))

    def fw_discard(self) -> None:
        """POST /fwflash.cgi discard=1: the device forgets the firmware it keeps."""
        self._fw_post("/fwflash.cgi", {"discard": 1}, failed=tr("Discarding failed"))

    def fw_update_check(self, force: bool) -> dict:
        """POST /api/fw/update-check: the device asks Ubiquiti whether there is a newer
        firmware (force: even if it asked a short while ago)."""
        return self._fw_post("/api/fw/update-check", {"force": "yes"} if force else {},
                             failed=tr("Error occurred while checking for update"))

    def fw_download(self) -> dict:
        """POST /api/fw/download: the device downloads the new firmware (ok, or error)."""
        return self._fw_post("/api/fw/download", timeout=60, failed=tr("Error"))

    def fw_download_progress(self) -> int | None:
        """POST /api/fw/download-progress: how much of it is downloaded (0-100), or None. It is
        asked while fw_download, in another thread, waits for its answer (as its web asks it
        meanwhile): so without the session's lock, which that request holds, nor logging in
        again (None then)."""
        try:
            status, headers, data = self._raw("POST", "/api/fw/download-progress", "",
                                              {"Content-Type": FORM}, timeout=5)
        except AirOSError:
            return None
        res = _json(data)
        if status != 200 or self._session_lost(status, headers) or not isinstance(res, dict):
            return None
        progress = _int(res.get("progress"))
        return progress if progress is not None and 0 <= progress <= 100 else None

    def fw_download_eula(self) -> str:
        """POST /api/fw/download-eula: the license of the downloaded firmware (HTML)."""
        status, _, data = self._req("POST", "/api/fw/download-eula", "",
                                    {"Content-Type": FORM}, timeout=5)
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}",
                                path="api/fw/download-eula", status=status, body=_short(data)))
        return data.decode("utf-8", "replace")

    # -- stations (airOS 8, as the stations table of its web) --

    def kick_station(self, iface: str, mac: str) -> None:
        """POST /stakick.cgi (staif, staid): the device disconnects that station, which
        usually connects again at once; ff:ff:ff:ff:ff:ff is all of them ("Kick All")."""
        body = urllib.parse.urlencode({"staif": iface, "staid": mac})
        status, _, data = self._req("POST", "/stakick.cgi", body, {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or (isinstance(res, dict) and (res.get("ok") is False
                                                        or res.get("error"))):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="stakick.cgi",
                                status=status, body=_short(data)))

    # -- UISP (airOS 8, as the switch in its web's header) --

    def unms_enable(self, key: str) -> None:
        """POST /api/unms/enable (unms_key): the device saves the key, turns UISP management
        on and connects, at once."""
        self._unms_post("/api/unms/enable", urllib.parse.urlencode({"unms_key": key}))

    def unms_disable(self) -> None:
        """POST /api/unms/disable: the device turns UISP management off (it keeps the key)."""
        self._unms_post("/api/unms/disable", "")

    def _unms_post(self, path: str, body: str) -> None:
        status, _, data = self._req("POST", path, body, {"Content-Type": FORM})
        res = _json(data)
        if status != 200 or (isinstance(res, dict) and (res.get("ok") is False
                                                        or res.get("error"))):
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path=path.lstrip("/"),
                                status=status, body=_short(data)))

    def reset_defaults(self) -> None:
        """airOS 8, as the web's "Reset to Factory Defaults": POST /reset.cgi. The device erases
        its configuration and reboots with the factory one."""
        if self.flavor != "v8":
            raise AirOSError(tr("On airOS 6 the reset to factory defaults is in the device's "
                                "web (System page)."))
        status, _, data = self._req("POST", "/reset.cgi", "reset=yes", {"Content-Type": FORM})
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="reset.cgi",
                                status=status, body=_short(data)))

    def support_info(self) -> bytes:
        """airOS 8, as the web's "Download Support Info": GET /support.cgi, a .tar.gz with the
        device's logs, statistics and configuration (it takes about half a minute)."""
        if self.flavor != "v8":
            raise AirOSError(tr("On airOS 6 the support info is downloaded from the device's "
                                "web (System page)."))
        status, _, data = self._req("GET", "/support.cgi", timeout=SUPPORT_TIMEOUT)
        if status != 200 or not data:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="support.cgi",
                                status=status, body=_short(data)))
        return data

    @property
    def full_version(self) -> str:
        """airOS 8: the firmware's full version, from the login (WA.ar934x.v8.7.19…)."""
        return str(self.login_info.get("fullVersion") or "")

    def reboot(self) -> None:
        if self.flavor == "v8":
            status, _, data = self._req("POST", "/reboot.cgi", "reboot=yes",
                                        {"Content-Type": FORM})
        else:
            status, data = self._v6_post_with_token("/reboot.cgi", [])
        if status != 200:
            raise AirOSError(tr("{path} answered HTTP {status}: {body}", path="reboot.cgi",
                                status=status, body=_short(data)))


def reachable(host: str, timeout: float = 2.0, ports=(443, 80)) -> bool:
    """Does this computer reach the web of host (by default HTTPS or HTTP, at their usual
    ports)?"""
    for port in ports:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except (OSError, ValueError):          # (UnicodeError: not a valid name)
            pass
    return False
