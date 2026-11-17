"""Validation of single values, as each web checks its fields, and the quick settings."""
from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple

from .i18n import N_, tr
from .config import Config, INT_RE, IPV4_KEY_RE, NETMASK_KEY_RE, ROUTE_MASK_RE, is_digits

# Services that clash if they share a port: port key -> status key.
SERVICE_PORTS = {"httpd.port": "httpd.status", "httpd.https.port": "httpd.https.status",
                 "sshd.port": "sshd.status", "telnetd.port": "telnetd.status"}


def _v_hostname(v: str) -> str | None:
    if not 1 <= len(v) <= 64 or not re.fullmatch(r"[ -~]+", v):
        return tr("Between 1 and 64 printable ASCII characters.")
    return None


def _v_ssid(v: str) -> str | None:
    if not 1 <= len(v) <= 32 or not re.fullmatch(r"[ -~]+", v):
        return tr("The SSID must have between 1 and 32 printable ASCII characters.")
    return None


def _v_psk(v: str) -> str | None:
    if re.fullmatch(r"[0-9A-Fa-f]{64}", v) or (8 <= len(v) <= 63
                                                and re.fullmatch(r"[ -~]+", v)):
        return None
    return tr("The WPA2 key must have between 8 and 63 characters (or 64 hexadecimal ones).")


def _v_txpower(v: str) -> str | None:
    # The range is checked later against the device's real limits (check_config).
    return None if INT_RE.fullmatch(v) else tr("The power must be a whole number in dBm.")


def _is_netmask(value: str) -> bool:
    """IPv4 netmask with contiguous ones (255.255.255.0 yes, 255.0.255.0 no)."""
    try:
        bits = int(ipaddress.IPv4Address(value))
    except ValueError:
        return False
    inverse = ~bits & 0xFFFFFFFF
    return inverse & (inverse + 1) == 0


def validate_value(key: str, value: str, orig: str | None = None) -> str | None:
    """Format of a value. orig is the previous value (used to infer the type)."""
    if "\n" in value or "\r" in value:
        return tr("The value cannot contain line breaks.")
    if NETMASK_KEY_RE.match(key):
        if not _is_netmask(value):
            return tr("{key} must be a valid netmask (for example 255.255.255.0).", key=key)
    elif ROUTE_MASK_RE.match(key):
        if key.startswith("route6."):
            if not (is_digits(value) and int(value) <= 128):
                return tr("{key} must be an IPv6 prefix from 0 to 128.", key=key)
        elif not ((is_digits(value) and int(value) <= 32) or _is_netmask(value)):
            return tr("{key} must be a netmask (255.255.255.0) or a prefix from 0 to 32.",
                      key=key)
    elif IPV4_KEY_RE.match(key):
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            return tr("{key} must be an IPv4 address (for example 192.168.1.20).", key=key)
    if key in SERVICE_PORTS and not (is_digits(value) and 1 <= int(value) <= 65535):
        return tr("A port must be a number between 1 and 65535.")
    if key.endswith(".ssid"):
        return _v_ssid(value)
    if key.endswith(".psk"):
        return _v_psk(value)
    if key == "radio.1.txpower":
        return _v_txpower(value)
    if key in ("radio.1.antenna.gain", "radio.1.cable.loss") and not (
            is_digits(value) and int(value) <= 40):
        return tr("It must be a whole number from 0 to 40 (the same limits as the web).")
    if orig in ("enabled", "disabled") and value not in ("enabled", "disabled"):
        return tr('This key only accepts "enabled" or "disabled".')
    if orig is not None and INT_RE.fullmatch(orig) and not INT_RE.fullmatch(value):
        return tr("This key is numeric: type a whole number.")
    return None


class Problem(NamedTuple):
    level: str      # "error": not accepted · "warning": asks for confirmation
    key: str
    msg: str


class QuickSetting:
    """A web setting that touches several system.cfg keys at once."""

    def __init__(self, label, keys, validate, create=True, also=None, ap_prefers=None):
        self.label, self.keys, self.validate = label, keys, validate   # label: N_() msgid
        self.create = create          # create the first key if it does not exist
        self.also = also or {}        # auxiliary keys to set
        self.ap_prefers = ap_prefers  # in AP mode, show this key first

    def current(self, cfg: Config) -> str | None:
        keys = list(self.keys)
        is_ap = (cfg.get("radio.1.mode") or "").lower() == "master"
        if self.ap_prefers and is_ap:
            keys.remove(self.ap_prefers)
            keys.insert(0, self.ap_prefers)
        elif self.ap_prefers:
            keys.remove(self.ap_prefers)
            keys.append(self.ap_prefers)
        for k in keys:
            if cfg.get(k):
                return cfg.get(k)
        return next((cfg.get(k) for k in keys if k in cfg), None)

    def updates(self, cfg: Config, value: str) -> dict[str, str]:
        """Keys that must change in cfg to set this setting."""
        out = {k: value for i, k in enumerate(self.keys)
               if k in cfg or (i == 0 and self.create)}
        if out:
            out.update(self.also)
        return out

    def apply(self, cfg: Config, value: str) -> list[str]:
        changes = self.updates(cfg, value)
        for k, v in changes.items():
            cfg.set(k, v)
        return list(changes)


# Keys checked against the airOS 8 web (changing the SSID also sets the one of the
# wpa_supplicant profile and the aaa one; the WPA2 key is saved on both sides).
QUICK_SETTINGS = [
    # (the app edits the device name as the System page does: system.device_name_*)
    QuickSetting(N_("Device name"), ["resolv.host.1.name"], _v_hostname,
                 also={"resolv.host.1.status": "enabled"}),
    QuickSetting("SSID", ["wireless.1.ssid", "wpasupplicant.profile.1.network.1.ssid",
                          "aaa.1.ssid", "wireless.3.ssid"], _v_ssid),
    QuickSetting(N_("WPA2 key (PSK)"), ["aaa.1.wpa.psk",
                                        "wpasupplicant.profile.1.network.1.psk"],
                 _v_psk, create=False, ap_prefers="aaa.1.wpa.psk"),
    QuickSetting(N_("Transmit power (dBm)"), ["radio.1.txpower"], _v_txpower),
]
# The quick settings that go through the Wireless page (all but on airOS 8 Gen3 boards).
QUICK_WIRELESS = ("wireless.1.ssid", "aaa.1.wpa.psk", "radio.1.txpower")
