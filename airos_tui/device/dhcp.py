"""The DHCP of the LANs of the web's Network page (airOS 8, router and SOHO modes), as its web
reads and saves it: per LAN, its DHCP server disabled, enabled (the range, its netmask, the
lease time and, without the DNS proxy, the DNS servers it hands out) or relaying to another
server (with an agent ID); the LAN's DNS proxy; the address reservations (advanced network
configuration, router mode); and the leases the device has given (its main page's "DHCP
Leases").

Its web writes a LAN's server in dhcpd.N (its reservations inside it, dhcpreserv.M), the relay
in dhcprelay.server.N and dhcprelay.client.N, and the DNS proxy as a dnsmasq.N entry and the
server's dnsproxy. A disabled server is no entry at all (it writes the network again from its
model, which then has none)."""
from __future__ import annotations

import ipaddress
import math
import re

from ..core.i18n import tr
from ..core.config import Config, WEB_IPV4_RE, _find, _indices, js_str, _merge, _next_index, _v8_on
from .network import lan_ifaces
from .netmode import _renumber

DHCP_LEASE_RANGE = (120, 172800)
_DHCPD_DEFAULTS = {"start": "192.168.1.2", "end": "192.168.1.254", "netmask": "255.255.255.0",
                   "lease": "600"}
_MAC_RE = re.compile(r"([0-9a-fA-F][0-9a-fA-F]:){5}[0-9a-fA-F][0-9a-fA-F]")


def _ip_int(text: str) -> int | None:
    try:
        return int(ipaddress.IPv4Address(str(text).strip()))
    except ValueError:
        return None


def web_dhcp_range(ip: str, netmask: str) -> tuple[str, str, str] | None:
    """The range its web proposes when the server is enabled (calculateIpRange): the LAN's
    network (at most a /24: a wider netmask is taken as 255.255.255.0) but the device's IP,
    from the one after it to the last if two or more are left there (if not, from the first
    to the one before it); and that netmask. None if neither has room (a /30)."""
    a, m = _ip_int(ip), _ip_int(netmask)
    if a is None or m is None:
        return None
    m = max(m, 0xFFFFFF00)
    bits, ones = 0, 0
    while bits < 32:
        nxt = ones + (1 << (31 - bits))
        if m & nxt != nxt:
            break
        ones, bits = nxt, bits + 1
    net = a & ((0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF)
    first, last = net + 1, net + (1 << (32 - bits)) - 2
    if a + 1 < last:
        first = a + 1
    else:
        last = a - 1
    if first > last or first < 0:
        return None
    return (str(ipaddress.IPv4Address(first)), str(ipaddress.IPv4Address(last)),
            str(ipaddress.IPv4Address(m)))


def lan_dhcp(cfg: Config, devname: str) -> dict:
    """The LAN's DHCP as its web reads it (_getDhcpd, _getDnsProxy): its mode, the server's
    settings (its model's defaults for what is missing), the relay's and the DNS proxy."""
    ds = dict(_DHCPD_DEFAULTS, mode="disabled", dns1="", dns2="", proxy=False, relay_ip="",
              agent_id="")
    k = _find(cfg, "dhcpd", devname=devname)
    c = _find(cfg, "dhcprelay.client", devname=devname)
    if _v8_on(cfg, "dhcpd.status") and k is not None and _v8_on(cfg, f"dhcpd.{k}.status"):
        p = f"dhcpd.{k}."
        ds.update(mode="enabled", start=cfg.get(p + "start") or ds["start"],
                  end=cfg.get(p + "end") or ds["end"],
                  netmask=cfg.get(p + "netmask") or ds["netmask"],
                  lease=cfg.get(p + "lease_time") or ds["lease"],
                  dns1=cfg.get(p + "dns.1.server") or "", dns2=cfg.get(p + "dns.2.server") or "",
                  proxy=cfg.get(p + "dnsproxy") != "disabled")
    elif c is not None and _v8_on(cfg, f"dhcprelay.client.{c}.status"):
        ds.update(mode="relay", relay_ip=cfg.get(f"dhcprelay.server.{c}.ip") or "",
                  agent_id=cfg.get(f"dhcprelay.client.{c}.agentid") or "")
    m = _find(cfg, "dnsmasq", devname=devname)
    if _v8_on(cfg, "dnsmasq.status") and m is not None and _v8_on(cfg, f"dnsmasq.{m}.status"):
        ds["proxy"] = True
    return ds


def _netmask_ok(text: str) -> bool:
    """Its ipv4Netmask: an address whose ones come first (0.0.0.0 too)."""
    n = _ip_int(text) if WEB_IPV4_RE.fullmatch(text) else None
    if n is None:
        return False
    inv = 0xFFFFFFFF - n
    return inv & (inv + 1) == 0


def dhcp_problem(field: str, value, ds: dict) -> str | None:
    """What its web says of a field of the LAN's DHCP (ds: the rest of its settings), or
    None. The DNS servers are only checked without the DNS proxy, and the second one only if
    there is one."""
    text = str(value).strip()
    ip_ok = bool(WEB_IPV4_RE.fullmatch(text))
    if field == "start":
        return tr("Range Start is required") if not text else None if ip_ok else tr(
            "Range Start is invalid")
    if field == "end":
        if not text:
            return tr("Range End is required")
        if not ip_ok:
            return tr("Range End is invalid")
        a, b = _ip_int(ds.get("start") or ""), _ip_int(text)
        if a is not None:
            m = _ip_int(ds.get("netmask") or "")
            if a > b or (m is not None and a & m != b & m):
                return tr("Inconsistent DHCP Range")
        return None
    if field == "netmask":
        if not text:
            return tr("Netmask is required")
        return None if _netmask_ok(text) else tr("Netmask is invalid")
    if field == "lease":
        if not text:
            return tr("Lease Time is required")
        low, high = DHCP_LEASE_RANGE
        return None if re.fullmatch(r"[0-9]+", text) and low <= int(text) <= high else tr(
            "Lease Time must be between {low} and {high}", low=low, high=high)
    if field in ("dns1", "dns2"):
        if ds.get("proxy") or (field == "dns2" and not text):
            return None
        if not text:
            return tr("Primary DNS is required")
        if ip_ok:
            return None
        return tr("Primary DNS is invalid") if field == "dns1" else tr("Secondary DNS is invalid")
    if field == "relay_ip":
        if not text or (ip_ok and text not in ("0.0.0.0", "255.255.255.255")):
            return None
        return tr("DHCP Server IP is invalid")
    if field == "agent_id":
        return None if text else tr("Agent ID is required")
    return None


def _drop(cfg: Config, prefix: str) -> None:
    for k in [k for k in cfg.keys() if k.startswith(prefix)]:
        cfg.delete(k)


def plan_lan_dhcp(cfg: Config, devname: str, ds: dict, wan: str) -> dict[str, str | None]:
    """What its web saves for the LAN's DHCP (None = delete): its server's entry (kept with
    its reservations while enabled, none otherwise), the relay's (to the WAN wan) and the DNS
    proxy's; the sections left without entries out, and the entries numbered 1, 2, 3…"""
    new = cfg.copy()
    k = _find(new, "dhcpd", devname=devname)
    if ds["mode"] == "enabled":
        if k is None:
            k = _next_index(new, "dhcpd")
        values = {"status": "enabled", "devname": devname,
                  "dnsproxy": "enabled" if ds["proxy"] else "disabled", "start": ds["start"],
                  "end": ds["end"], "netmask": ds["netmask"], "lease_time": str(ds["lease"]),
                  "dns.1.server": ds["dns1"], "dns.2.server": ds["dns2"]}
        for key, value in values.items():
            if value:
                new.set(f"dhcpd.{k}.{key}", value)
            else:
                new.delete(f"dhcpd.{k}.{key}")
        new.set("dhcpd.status", "enabled")
    elif k is not None:
        _drop(new, f"dhcpd.{k}.")
    c = _find(new, "dhcprelay.client", devname=devname)
    if ds["mode"] == "relay":
        if c is None:
            c = max(_next_index(new, "dhcprelay.client"), _next_index(new, "dhcprelay.server"))
        new.set("dhcprelay.status", "enabled")
        for key, value in ((f"dhcprelay.server.{c}.ip", ds["relay_ip"]),
                           (f"dhcprelay.server.{c}.devname", wan),
                           (f"dhcprelay.client.{c}.devname", devname),
                           (f"dhcprelay.client.{c}.agentid", ds["agent_id"])):
            if value:
                new.set(key, value)
            else:
                new.delete(key)
    elif c is not None:
        _drop(new, f"dhcprelay.client.{c}.")
        _drop(new, f"dhcprelay.server.{c}.")
    m = _find(new, "dnsmasq", devname=devname)
    if ds["proxy"]:
        if m is None:
            m = _next_index(new, "dnsmasq")
        _merge(new, {"dnsmasq.status": "enabled", f"dnsmasq.{m}.status": "enabled",
                     f"dnsmasq.{m}.devname": devname})
    elif m is not None:
        _drop(new, f"dnsmasq.{m}.")
    if not _indices(new, "dhcpd"):
        new.delete("dhcpd.status")
    if not _indices(new, "dnsmasq"):
        new.delete("dnsmasq.status")
    if not _indices(new, "dhcprelay.client") and not _indices(new, "dhcprelay.server"):
        new.delete("dhcprelay.status")
    for prefix in ("dhcpd", "dnsmasq", "dhcprelay.client", "dhcprelay.server"):
        _renumber(new, prefix)
    return {key: after for key, _, after in cfg.diff(new)}


# -- address reservations (advanced network configuration, router mode) ---------------

def dhcp_lans(cfg: Config) -> list[str]:
    """The LANs with their DHCP server enabled (its getDhcpdIfcs): where a reservation can
    go."""
    return [lan["devname"] for lan in lan_ifaces(cfg)
            if lan_dhcp(cfg, lan["devname"])["mode"] == "enabled"]


def dhcp_reservations(cfg: Config) -> list[dict]:
    """Its reservations (_getDhcpReservation), those of every enabled server, in order."""
    out: list[dict] = []
    if not _v8_on(cfg, "dhcpd.status"):
        return out
    for k in _indices(cfg, "dhcpd"):
        if not _v8_on(cfg, f"dhcpd.{k}.status"):
            continue
        dev = cfg.get(f"dhcpd.{k}.devname") or ""
        for n in _indices(cfg, f"dhcpd.{k}.dhcpreserv"):
            p = f"dhcpd.{k}.dhcpreserv.{n}."
            out.append({"devname": dev, "on": _v8_on(cfg, p + "status"),
                        "mac": cfg.get(p + "mac") or "", "ip": cfg.get(p + "ip") or "",
                        "comment": cfg.get(p + "comment") or ""})
    return out


def reservation_problem(field: str, value) -> str | None:
    """What its web says of the MAC or the IP of a reservation, or None."""
    text = str(value).strip()
    if field == "mac":
        if not text:
            return tr("MAC Address is required")
        return None if _MAC_RE.fullmatch(text) else tr("MAC Address is invalid")
    if field == "ip":
        if not text:
            return tr("IP Address is required")
        return None if WEB_IPV4_RE.fullmatch(text) else tr("IP Address is invalid")
    return None


def plan_reservations(cfg: Config, items: list[dict]) -> dict[str, str | None]:
    """What its web saves for the reservations (None = delete): each in the server of its
    LAN, numbered 1, 2, 3… in their order (one whose LAN has no server any more is lost, as
    there)."""
    new = cfg.copy()
    for k in [k for k in new.keys() if re.match(r"dhcpd\.\d+\.dhcpreserv\.", k)]:
        new.delete(k)
    count: dict[int, int] = {}
    for item in items:
        n = _find(new, "dhcpd", devname=item["devname"])
        if n is None:
            continue
        count[n] = count.get(n, 0) + 1
        p = f"dhcpd.{n}.dhcpreserv.{count[n]}."
        new.set(p + "status", "enabled" if item["on"] else "disabled")
        for f in ("mac", "ip", "comment"):
            if item.get(f):
                new.set(p + f, str(item[f]))
    return {key: after for key, _, after in cfg.diff(new)}


# -- the leases (GET /api/dhcp-server/leases) -------------------------------------------

def elapsed_time(seconds) -> str:
    """A time left as its web writes it (getElapsedTime): HH:MM:SS, after the days."""
    t = float(seconds)

    def two(x) -> str:
        text = js_str(x)
        return ("0" if len(text) < 2 else "") + text
    days = int(t / 86400)
    text = (f"{two(int(math.fmod(int(t / 3600), 24)))}:{two(int(math.fmod(int(t / 60), 60)))}"
            f":{two(math.fmod(t, 60))}")
    if days > 0:
        text = f"{days} {tr('day') if days == 1 else tr('days')} {text}"
    return text


def parse_leases(data) -> tuple[list[dict], list[dict]]:
    """The leases of its reply, as its main page shows them: IPv4 (MAC, IP, time left and
    the client's name) and IPv6 (its identifier, IP and time left)."""
    if not isinstance(data, dict):
        return [], []
    v4, v6 = [], []
    for row in data.get("aaData") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            left = row[2]
            number = isinstance(left, (int, float)) and not isinstance(left, bool)
            v4.append({"mac": str(row[0]), "ip": str(row[1]),
                       "left": elapsed_time(left) if number else str(left),
                       "hostname": str(row[3]) if len(row) > 3 and row[3] is not None else ""})
    for row in data.get("aaData6") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            v6.append({"id": str(row[0]), "ip": str(row[1]), "left": str(row[2])})
    return v4, v6
