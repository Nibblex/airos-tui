"""system.cfg: the Config class, the patterns of its keys and the small helpers every
part uses to read and change it."""
from __future__ import annotations

import ipaddress
import math
import os
import re

# The webs' "ipv4Address" pattern (Backbone.Validation), whole.
WEB_IPV4_RE = re.compile(r"((25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}"
                         r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])")


def web_ipv6_ok(text: str) -> bool:
    """An IPv6 address as the webs' "ipv6Address" pattern takes it: in one of its usual forms,
    without a zone (fe80::1%eth0, which Python's ipaddress takes, the webs do not)."""
    if "%" in text:
        return False
    try:
        ipaddress.IPv6Address(text)
    except ValueError:
        return False
    return True

# Where airos-tui keeps what it saves (backups, update checks), only for this user.
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "airos-tui")

# Keys that are not edited by hand: passwords (airOS 8 returns them masked as "-") and
# metadata the device manages itself.
PROTECTED_RE = re.compile(
    r"^(users\.\d+\.password|mac-telnetd\.users\.\d+\.password"
    r"|system\.cfg\.version|system\.cfg\.editor\.webui)$")
# What the airOS 8 web strips before saving: that way the device keeps the current
# passwords.
V8_STRIP_RE = re.compile(r"^(users\.\d+\.password|system\.cfg\.editor\.webui)$")
# Changes that can cut management access.
NET_RE = re.compile(
    r"^(netconf\.|dhcpc\.|dhcpd\.|route6?\.|bridge\.|vlan\.|ppp\.|netmode$|httpd\.)")
WIFI_RE = re.compile(r"^(radio\.|wireless\.|aaa\.|wpasupplicant\.)")
IPV4_KEY_RE = re.compile(
    r"^(netconf\.\d+\.ip|route\.\d+\.gateway|resolv\.nameserver\.\d+\.ip"
    r"|dhcpc\.\d+\.fallback|dhcpd\.\d+\.(start|end)|ppp\.\d+\.fallback)$")
NETMASK_KEY_RE = re.compile(
    r"^(netconf\.\d+\.netmask|dhcpc\.\d+\.fallback_netmask|dhcpd\.\d+\.netmask"
    r"|ppp\.\d+\.fallback_netmask)$")
ROUTE_MASK_RE = re.compile(r"^route6?\.\d+\.netmask$")
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
INT_RE = re.compile(r"-?[0-9]+")       # (not \d: int() takes "٢٠" too, the device does not)


class Config:
    """airOS system.cfg: key=value lines. A repeated key has the value its web reads: the
    last one (airOS 8: its fromPlainText, on what getcfg.cgi hands over, the file sorted),
    except on airOS 6 (flavor "v6") when the file's first entry is one of them: then that one
    (its cfg_load, on the file as it is: its list holds the first entry first and then the
    later lines before the earlier ones; a line "=x" is an entry too, while comments, blank
    lines and lines without "=" are not). The device may apply another one: the ubntconf of
    both parses as airOS 6's web, on the file as it is then, and rewrites the file from its
    list (airOS 8 every time; airOS 6 when its fixups change something), which swaps the
    repeated lines (checked on both, emulated). So repeated (the keys with different values,
    in the order of the text) is warned of, and text() writes each key once, where it first
    appeared, as airOS 8's web does when it saves: written once, it cannot change by itself."""

    def __init__(self, text: str = "", flavor: str | None = None):
        self._d: dict[str, str] = {}
        seen: dict[str, list[str]] = {}
        first = None                   # the key of the file's first entry
        for raw in text.replace("\r", "").split("\n"):
            if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            seen.setdefault(key, []).append(value)
            if first is None:
                first = key
            elif flavor == "v6" and key == first:
                continue               # (airOS 6's web reads its first entry's)
            if key:
                self._d[key] = value
        self.repeated = {k: v for k, v in seen.items() if k and len(set(v)) > 1}

    def copy(self) -> Config:
        c = Config()
        c._d = dict(self._d)
        return c

    def keys(self) -> list[str]:
        return list(self._d)

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._d[key] = value

    def delete(self, key: str) -> None:
        self._d.pop(key, None)

    def __contains__(self, key) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def text(self, skip: re.Pattern | None = None) -> str:
        return "".join(f"{k}={v}\n" for k, v in self._d.items()
                       if not (skip and skip.match(k)))

    def diff(self, new: Config) -> list[tuple[str, str | None, str | None]]:
        """[(key, value_before, value_after)]; None = does not exist."""
        out = []
        for k in sorted(set(self._d) | set(new._d)):
            a, b = self._d.get(k), new._d.get(k)
            if a != b:
                out.append((k, a, b))
        return out


def looks_like_cfg(text: str) -> bool:
    lines = [line for line in text.splitlines() if "=" in line]
    return len(lines) >= 10 and any(line.startswith(("radio.", "netconf.", "wireless."))
                                    for line in lines)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_countdown(seconds) -> str:
    """Seconds -> '2 d 03:04:05' or '03:04:05', as the web shows them."""
    s = _int(seconds)
    if s is None:
        return str(seconds or "")
    d, s = divmod(max(s, 0), 86400)
    text = f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{d} d {text}" if d else text


def _on(cfg: Config, key: str) -> bool:
    return cfg.get(key) == "enabled"


def _indices(cfg: Config, prefix: str) -> list[int]:
    pat = re.compile(rf"^{re.escape(prefix)}\.(\d+)\.")
    return sorted({int(m.group(1)) for m in map(pat.match, cfg.keys()) if m})


def _find(cfg: Config, prefix: str, **match) -> int | None:
    for n in _indices(cfg, prefix):
        if all(cfg.get(f"{prefix}.{n}.{k}") == v for k, v in match.items()):
            return n
    return None


def _next_index(cfg: Config, prefix: str) -> int:
    idx = _indices(cfg, prefix)
    return idx[-1] + 1 if idx else 1


def _merge(cfg: Config, updates: dict[str, str | None]) -> Config:
    for k, v in updates.items():
        if v is None:
            cfg.delete(k)
        else:
            cfg.set(k, v)
    return cfg


def _has(cfg: Config, prefix: str) -> bool:
    return any(k == prefix or k.startswith(prefix + ".") for k in cfg.keys())


# airOS 8: ".status" keys that count as disabled when they are missing (the web's own
# list); any other missing status counts as enabled.
V8_OFF_BY_DEFAULT = frozenset((
    "aaa.%d.radius.macacl.status", "aaa.status", "art.status", "bridge.%d.stp.status",
    "dhcp6d.status", "dhcp6d.%d.stateful.status", "dhcp6d.%d.stateless.status",
    "dnsmasq.status", "ebtables.sys.arpnat.status", "ebtables.sys.eap.status",
    "ebtables.sys.fw6.status", "ebtables.sys.fw.status", "ebtables.sys.status",
    "ebtables.sys.vlan.status", "ebtables.sys.dhcp_relay.status", "gpio.%d.status",
    "gui.network.advanced.status", "httpd.https.status", "ip6tables.sys.fw6.status",
    "iptables.status", "iptables.sys.dmz.status", "iptables.sys.dmz.%d.except.status",
    "iptables.sys.fw.status", "iptables.sys.masq.status", "iptables.sys.mgmt.status",
    "iptables.sys.portfw.status", "iptables.sys.status", "iptables.sys.tcpmss.status",
    "iptables.sys.upnpd.status", "ls_logo.status", "netconf.%d.alias.%d.status",
    "netconf.%d.autoip.status", "netconf.%d.hwaddr.status", "netconf.%d.ip6.status",
    "netconf.%d.phy.%d.status", "netconf.status", "ppp.1.ipv6.status",
    "radio.%d.airsync.status", "radio.%d.ani.status", "radio.%d.scan_list.status",
    "syslog.remote.status", "syslog.remote.tcp.status", "system.date.status",
    "system.modules.blacklist.status", "wireless.%d.mac_acl.status",
    "wireless.%d.wds.status", "wireless.hideindoor.status", "wpasupplicant.status"))


def _v8_on(cfg: Config, key: str) -> bool:
    """airOS 8's section.enabled() for a ".status" key."""
    value = cfg.get(key) or ""
    if value:
        try:
            one = float(value) == 1
        except ValueError:
            one = False
        return value.lower() == "enabled" or one
    return re.sub(r"\.[0-9]+\.", ".%d.", key) not in V8_OFF_BY_DEFAULT


def _cfg_def(cfg: Config, key: str, default: str) -> str:
    """airOS 6's cfg_get_def(): the value if the key exists, the default if it does not."""
    value = cfg.get(key)
    return default if value is None else value


def gen3(board: dict) -> bool:
    """An airMAX Gen3 board (60 GHz): airOS 8 shows other Wireless and Services pages."""
    return _int(board.get("feature.g3")) == 1


def _freqs(text: str | None) -> list[int]:
    """A frequency list as the webs split it: commas, spaces allowed."""
    return [n for n in (_int(x) for x in (text or "").split(",")) if n is not None]


# -- JavaScript's numbers, as the webs compute what they show ---------------------------

def _model_bool(cfg: Config, key: str, default: bool) -> bool:
    """A yes/no of the airOS 8 web's models (a "bool" field): the key's value if there is
    one ("enabled", "true" or "1"), its model's default if not."""
    value = cfg.get(key)
    return default if value is None else value in ("enabled", "true", "1")


def is_digits(text) -> bool:
    """Only digits 0 to 9 (str.isdigit also takes "²", which int() refuses, and "٣", which
    it takes: neither is what a device's field takes)."""
    return isinstance(text, str) and text.isascii() and text.isdigit()


def js_round(value: float) -> int:
    """JavaScript's Math.round: halves go up (Python's round goes to the even one)."""
    return math.floor(value + 0.5)


def js_to_fixed(value: float, digits: int) -> str:
    """The web's toFixed: Math.round(value * 10^digits), with the point put back."""
    n = js_round(abs(value) * 10 ** digits)
    text = f"{n / 10 ** digits:.{digits}f}"
    return ("-" + text) if value < 0 and n else text


def js_parse_int(value) -> int | None:
    """JavaScript's parseInt (without a radix): the whole number the text starts with, in hex
    after 0x (None if there is none)."""
    text = str(value)
    m = re.match(r"\s*([+-]?)0[xX]([0-9a-fA-F]*)", text)
    if m:
        return int(m.group(1) + m.group(2), 16) if m.group(2) else None
    m = re.match(r"\s*([+-]?[0-9]+)", text)
    return int(m.group(1)) if m else None


def js_number(text: str) -> float:
    """JavaScript's Number() of a string (NaN if it is not a number): trimmed, an empty one
    is 0, and only the digits 0 to 9 count."""
    text = text.strip()
    if not text:
        return 0.0
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", text):
        return float(int(text, 16))
    if re.fullmatch(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?", text):
        return float(text)
    if re.fullmatch(r"[+-]?Infinity", text):
        return math.inf if not text.startswith("-") else -math.inf
    return math.nan


def js_int_like(text: str) -> int | None:
    """The number if parseInt(text) == text in JavaScript (as the webs' validators check)."""
    n, v = js_parse_int(text), js_number(text)
    return n if n is not None and not math.isnan(v) and n == v else None


def port_kind(value) -> str | None:
    """The airOS 8 web's port validator: None if valid (empty too), "invalid" for a bad range
    a:b and "range" for a single port out of 1..65535. A space inside ("1000 : 2000"), which
    its parseInt takes, is "invalid" here: written as it is, it would break the rule."""
    text = str(value).strip()
    if any(c.isspace() for c in text):
        return "invalid"
    if not text:
        return None
    if ":" in text:
        a, b = text.split(":", 1)
        x, y = js_int_like(a.strip()), js_int_like(b.strip())
        if x is None or y is None or not 1 <= x <= 65535 or not 1 <= y <= 65535 or x > y:
            return "invalid"
        return None
    n = js_int_like(text)
    return None if n is not None and 1 <= n <= 65535 else "range"


def js_parse_float(value) -> float | None:
    """JavaScript's parseFloat: the number the text starts with (None if there is none)."""
    m = re.match(r"\s*([+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)", str(value))
    return float(m.group(1)) if m else None


def js_str(value) -> str:
    """A value as JavaScript writes it: 1, not 1.0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
