"""Discovery: finding the antennas without logging in, with Ubiquiti discovery (UDP 10001),
the CDP and LLDP announcements of the devices of the link and the active search (their web,
address by address, and ARP). Its screen: screens.discovery."""
from __future__ import annotations

import concurrent.futures
import errno
import http.client
import ipaddress
import re
import select
import socket
import struct
import threading
import time
import urllib.parse

from ..core.i18n import N_, tr
from ..core.config import _fmt_countdown
from ..core.debuglog import debug_request, debugging
from .network import _network
from .client import _json, setup_steps, _tls_context, USER_AGENT


DISCOVERY_PORT = 10001


def _mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


def norm_mac(mac: str) -> str:
    """'68:d7:9a:98:fb:ff' or '68D79A98FBFF' -> '68D79A98FBFF' (empty if it is no MAC)."""
    m = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    return m if len(m) == 12 else ""


def _ip_key(ip: str) -> tuple:
    try:
        return (0, int(ipaddress.IPv4Address(ip)))
    except ValueError:
        return (1, ip)


BROADCAST = "255.255.255.255"
DISCOVERY_QUERY = b"\x01\x00\x00\x00"      # v1 query, the one of the official tool
# A range that is not one of this computer's networks is asked address by address: at most
# this many addresses (a /22).
MAX_SWEEP = 1024
_RANGE = re.compile(r"[0-9.]+\s*/\s*[0-9.]+")        # what looks like a range: IP/bits, IP/mask
# Wireless mode (TLV 0x0E): the discovery tool of both webs only tells 2 = station and
# 3 = access point apart (decodeWmode).
DISCOVERY_WMODES = {2: ("STA", N_("station")), 3: ("AP", N_("access point"))}
# airMAX firmware prefixes with airOS 6 (XM, XW, TI) and airOS 8 (WA, XC, 2XC, 2WA).
AIROS_FW_PREFIXES = {"XM", "XW", "TI", "WA", "XC", "2XC", "2WA"}


def airos_version(fw: str) -> str:
    """'WA.ar934x.v8.7.19.48279…' or 'WA.V8.7.17' -> '8.7.19' (empty if it is not airOS)."""
    m = re.match(r"([0-9A-Za-z]+)\..*?[vV](\d+\.\d+(?:\.\d+)?)", fw or "")
    return m.group(2) if m and m.group(1).upper() in AIROS_FW_PREFIXES else ""


def parse_discovery(data: bytes, src: str) -> dict | None:
    """Ubiquiti discovery v1 packet: header 01 CC LL LL (CC = 00 reply to the query,
    06 periodic announcement) and TLVs with a 1-byte type and a 2-byte length."""
    if len(data) < 4 or data[0] != 1 or data[1] not in (0x00, 0x06):
        return None
    info = {"ip": src, "src": src, "mac": "", "macs": set(), "addrs": [], "pairs": [],
            "hostname": "", "model": "", "platform": "", "fw": "", "version": "", "essid": "",
            "wmode": None, "uptime": None, "local": ""}
    off = 4
    while off + 3 <= len(data):
        kind, length = data[off], int.from_bytes(data[off + 1:off + 3], "big")
        value = data[off + 3:off + 3 + length]
        off += 3 + length
        if len(value) < length:
            break
        text = value.decode("utf-8", "replace").strip("\x00").strip()
        if kind in (0x00, 0x01) and length == 6:          # MAC (0x00 in announcements)
            info["mac"] = _mac(value)
            info["macs"].add(norm_mac(info["mac"]))
        elif kind == 0x02 and length == 10:               # MAC + IPv4 of each interface
            mac, ip = _mac(value[:6]), ".".join(map(str, value[6:]))
            info["macs"].add(norm_mac(mac))
            info["addrs"].append(ip)
            info["pairs"].append((mac, ip))
        elif kind == 0x03:
            info["fw"] = text
        elif kind == 0x0A and length == 4:                # uptime (s)
            info["uptime"] = int.from_bytes(value, "big")
        elif kind == 0x0B:
            info["hostname"] = text
        elif kind == 0x0C:                                # platform, e.g. LBE-5AC-Gen2
            info["platform"] = text
        elif kind == 0x0D:
            info["essid"] = text
        elif kind == 0x0E and length == 1:
            info["wmode"] = value[0]
        elif kind == 0x14:                                # full model name
            info["model"] = text
    if not info["macs"]:
        return None
    info["mac"] = info["mac"] or _colon_mac(sorted(info["macs"])[0])
    info["model"] = info["model"] or info["platform"]
    info["version"] = airos_version(info["fw"])
    usable = [ip for ip in info["addrs"] if ip != "0.0.0.0" and not ip.startswith("169.254.")]
    if src not in info["addrs"] and usable:
        info["ip"] = usable[0]
    return info


def local_ipv4() -> list[tuple[int, str, str, str]]:
    """(index, name, IP, netmask) of this computer's interfaces with IPv4, without loopback.
    Linux only (ioctl SIOCGIFADDR/SIOCGIFNETMASK); on other systems, an empty list."""
    try:
        import fcntl
        names = socket.if_nameindex()
    except (ImportError, OSError, AttributeError):
        return []
    out = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for idx, name in names:
            req = struct.pack("256s", name.encode()[:15])
            try:
                ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, req)[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x891B, req)[20:24])
            except OSError:
                continue                                   # no IPv4
            if not ip.startswith("127."):
                out.append((idx, name, ip, mask))
    finally:
        s.close()
    return out


def _local_iface_for(ip: str, ifaces) -> str:
    """Name of the local interface whose network contains ip ('' if none)."""
    if ip.startswith("127."):
        return "lo"
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return ""
    for _idx, name, my_ip, mask in ifaces:
        net = _network(my_ip, mask)
        if net is not None and addr in net:
            return name
    return ""


def target_range(target: str) -> ipaddress.IPv4Network | None:
    """The range of a target in CIDR notation (10.3.0.0/24; the IP may be any of the range's,
    and the bits a netmask: 10.3.0.5/255.255.255.0), or None if it is not one."""
    if not _RANGE.fullmatch(str(target or "").strip()):
        return None
    try:
        return ipaddress.IPv4Network(re.sub(r"\s+", "", target), strict=False)
    except ValueError:
        return None


def _local_for(net: ipaddress.IPv4Network, ifaces) -> list:
    """The interfaces of this computer whose network holds the whole range."""
    return [i for i in ifaces if (own := _network(i[2], i[3])) is not None
            and own.prefixlen < 31 and net.subnet_of(own)]


def probe_plan(target: str, ifaces) -> tuple[str, list]:
    """Where the query goes and through which interfaces: the general broadcast through all
    of them; the broadcast of one of this computer's networks, as the general broadcast but
    only through that interface (an airOS 8.7.14 does not answer its network's broadcast,
    only the general one or its own IP); anything else, as it is."""
    if target == BROADCAST:
        return BROADCAST, list(ifaces)
    mine = [i for i in ifaces if (net := _network(i[2], i[3])) is not None
            and net.prefixlen < 31 and str(net.broadcast_address) == target]
    return (BROADCAST, mine) if mine else (target, [])


def query_plan(target: str, ifaces) -> tuple[list[str], list, ipaddress.IPv4Network | None]:
    """probe_plan for any target, ranges in CIDR notation too: (where the query goes, through
    which interfaces, the range its answers must come from or None). A range of one of this
    computer's networks goes as that network's broadcast (only through its interface; a part
    of the network, keeping the answers from that part); any other, address by address (it
    gets there even with routers in between; ValueError beyond MAX_SWEEP addresses)."""
    net = target_range(target)
    if net is None:
        dest, per_iface = probe_plan(target, ifaces)
        return [dest], per_iface, None
    mine = _local_for(net, ifaces)
    if mine:
        whole = any(_network(i[2], i[3]) == net for i in mine)
        return [BROADCAST], mine, None if whole else net
    if net.num_addresses > MAX_SWEEP:
        raise ValueError(f"{net}: {net.num_addresses} addresses, more than {MAX_SWEEP}")
    return [str(h) for h in net.hosts()] or [str(net.network_address)], [], net


def target_problem(target: str, ifaces=None) -> str | None:
    """Why discovery cannot ask there (None if it can): a range that is not one, or one too
    big to ask address by address; an address that is not an IP or a name that resolves."""
    if _RANGE.fullmatch(target.strip()):                 # (not any "/": http://[x is no range)
        net = target_range(target)
        if net is None:
            return tr('"{target}" is not a valid range (for example 10.3.0.0/24).',
                      target=target)
        if net.num_addresses > MAX_SWEEP and not _local_for(
                net, local_ipv4() if ifaces is None else ifaces):
            return tr("{range} has {count} addresses: outside your networks discovery asks "
                      "them one by one, {most} at most (a /22). Search a smaller range.",
                      range=str(net), count=net.num_addresses, most=MAX_SWEEP)
        return None
    try:
        socket.getaddrinfo(target, DISCOVERY_PORT, socket.AF_INET)
    except (socket.gaierror, UnicodeError):
        return tr('"{target}" is not an IP or a name that can be resolved.', target=target)
    return None


def _in_range(info: dict, net: ipaddress.IPv4Network) -> bool:
    """Whether a device that answered has an address in the range (the one it answered
    from, or one of those it announces)."""
    for ip in (info["src"], info["ip"], *info["addrs"]):
        try:
            if ipaddress.IPv4Address(ip) in net:
                return True
        except ValueError:
            continue
    return False


def discovery_host(text: str) -> str | None:
    """Where to ask, from what is typed: an IP, a name, a broadcast or a range in CIDR
    notation (as 10.3.0.0/24), also written as the device is given to connect (IP:port,
    http(s)://IP:port: discovery has its own port). None if it cannot be one."""
    t = str(text or "").strip()
    if _RANGE.fullmatch(t):
        net = target_range(t)
        return str(net) if net is not None else None
    try:
        host = urllib.parse.urlsplit(t if "://" in t else "//" + t).hostname
    except ValueError:                          # ("[::1" or "http://[x")
        return None
    return host or None


def discover(timeout: float = 3.0, target: str = BROADCAST, port: int | None = None,
             interfaces=None, first: bool = False,
             stop: threading.Event | None = None) -> list[dict]:
    """Look for Ubiquiti devices without logging into them: the v1 query of the official
    tool (UDP 10001). With the general broadcast it asks on every interface with IPv4, like
    that tool (otherwise it would only leave through the default route's interface); with
    the broadcast of one of this computer's networks, only through that one; with an IP,
    only that device (it works across routers); with a range in CIDR notation, as query_plan
    says: address by address, each one twice (a query can get lost; not again once it
    answered), spread over the first 80 % of the time. first: back with the first answer
    (asking one IP, the only one), instead of listening the whole time. stop: once it is set,
    back with what answered until then (Ctrl+C)."""
    port = port or DISCOVERY_PORT
    ifaces = local_ipv4() if interfaces is None else list(interfaces)
    dests, per_iface, within = query_plan(target, ifaces)
    sweep = dests * 2 if len(dests) > 1 else []
    found: dict[str, dict] = {}
    answered: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send() -> None:
        sent = False
        for idx, _name, ip, _mask in per_iface:      # IP_PKTINFO: leave through that interface
            try:
                info = struct.pack("=I4s4s", idx, socket.inet_aton(ip), b"\0" * 4)
                sock.sendmsg([DISCOVERY_QUERY], [(socket.IPPROTO_IP, socket.IP_PKTINFO, info)],
                               0, (dests[0], port))
                sent = True
            except (OSError, AttributeError):
                pass
        if not sent:
            try:
                sock.sendto(DISCOVERY_QUERY, (dests[0], port))
            except OSError:
                pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        start = time.monotonic()
        end, next_send, swept = start + timeout, 0.0, 0
        while time.monotonic() < end and not (stop is not None and stop.is_set()):
            now = time.monotonic()
            if sweep:
                due = min(len(sweep), int(len(sweep) * (now - start) / (timeout * 0.8)) + 1)
                for dest in sweep[swept:due]:
                    if dest not in answered:
                        try:
                            sock.sendto(DISCOVERY_QUERY, (dest, port))
                        except OSError:             # (no route there, a full buffer…)
                            pass
                swept = max(swept, due)
            elif now >= next_send:
                send()
                next_send = now + 0.8
            sock.settimeout(0.02 if swept < len(sweep) else 0.25)
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except (ConnectionRefusedError, ConnectionResetError):
                continue                 # (an ICMP "unreachable" some systems hand over)
            except OSError:
                break
            info = parse_discovery(data, addr[0])
            if info and (within is None or _in_range(info, within)):
                answered.add(addr[0])
                info["local"] = _local_iface_for(info["ip"], ifaces)
                found[info["mac"]] = info
                if first:
                    break
    finally:
        sock.close()
    return sorted(found.values(), key=lambda d: (_ip_key(d["ip"]), d["mac"]))


# CDP and LLDP cannot be asked for: every device announces itself every minute or so (airOS:
# LLDP every 60 s) with an Ethernet frame that only reaches its own link. Hearing them takes
# a raw socket (root or CAP_NET_RAW) and some patience.
NEIGHBOR_LISTEN = 65             # seconds: a whole announcement period, with some margin
PROTOCOL_NAMES = {"ubnt": "Ubiquiti", "cdp": "CDP", "lldp": "LLDP", "web": "web",
                  "arp": "ARP"}
CDP_MAC = bytes.fromhex("01000ccccccc")
CDP_SNAP = b"\xaa\xaa\x03\x00\x00\x0c\x20\x00"          # LLC + SNAP (Cisco OUI, type 0x2000)
LLDP_MACS = (bytes.fromhex("0180c200000e"), bytes.fromhex("0180c2000003"),
             bytes.fromhex("0180c2000000"))
ETH_P_802_2, ETH_P_LLDP = 0x0004, 0x88CC
SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_MULTICAST = 263, 1, 0


def _neighbor(protocol: str, src: bytes) -> dict:
    """A device heard through CDP or LLDP, with the same fields as parse_discovery()."""
    mac = _mac(src)
    return {"ip": "", "src": "", "mac": mac, "macs": {norm_mac(mac)}, "addrs": [], "pairs": [],
            "hostname": "", "model": "", "platform": "", "fw": "", "version": "", "essid": "",
            "wmode": None, "uptime": None, "local": "", "port": "", "protocols": [protocol]}


def _text(value: bytes) -> str:
    return value.decode("utf-8", "replace").strip("\x00").strip()


def _finish_neighbor(info: dict) -> dict:
    usable = [ip for ip in info["addrs"] if ip != "0.0.0.0" and not ip.startswith("169.254.")]
    info["ip"] = usable[0] if usable else (info["addrs"][0] if info["addrs"] else "")
    info["pairs"] = [(info["mac"], ip) for ip in info["addrs"]]
    info["version"] = next((v for word in info["fw"].split() if (v := airos_version(word))), "")
    return info


def parse_lldp(data: bytes, src: bytes) -> dict | None:
    """LLDPDU (IEEE 802.1AB): TLVs with a 7-bit type and a 9-bit length. None without the
    mandatory chassis, port and TTL."""
    info, seen, off = _neighbor("lldp", src), set(), 0
    while off + 2 <= len(data):
        head = int.from_bytes(data[off:off + 2], "big")
        kind, length = head >> 9, head & 0x1FF
        value = data[off + 2:off + 2 + length]
        off += 2 + length
        if kind == 0 or len(value) < length:
            break
        seen.add(kind)
        if kind == 1 and len(value) == 7 and value[0] == 4:             # chassis: MAC
            info["mac"] = _mac(value[1:])
            info["macs"].add(norm_mac(info["mac"]))
        elif kind == 1 and len(value) == 6 and value[0] == 5 and value[1] == 1:   # chassis: IPv4
            info["addrs"].append(".".join(map(str, value[2:6])))
        elif kind == 2 and value:                                         # port
            info["port"] = _mac(value[1:]) if value[0] == 3 and len(value) == 7 else _text(
                value[1:])
        elif kind == 5:
            info["hostname"] = _text(value)
        elif kind == 6:
            info["fw"] = _text(value)                                     # system description
        elif kind == 8 and len(value) >= 6 and value[0] == 5 and value[1] == 1:   # mgmt IPv4
            ip = ".".join(map(str, value[2:6]))
            if ip not in info["addrs"]:
                info["addrs"].append(ip)
    return _finish_neighbor(info) if {1, 2, 3} <= seen else None


def _cdp_addresses(value: bytes) -> list[str]:
    """CDP address list: count, then protocol type and protocol, length and address."""
    out, off = [], 4
    for _ in range(int.from_bytes(value[:4], "big") if len(value) >= 4 else 0):
        if off + 2 > len(value):
            break
        ptype, plen = value[off], value[off + 1]
        proto = value[off + 2:off + 2 + plen]
        off += 2 + plen
        if off + 2 > len(value):
            break
        alen = int.from_bytes(value[off:off + 2], "big")
        addr = value[off + 2:off + 2 + alen]
        off += 2 + alen
        if ptype == 1 and proto == b"\xcc" and len(addr) == 4:          # NLPID IPv4
            out.append(".".join(map(str, addr)))
    return out


def parse_cdp(data: bytes, src: bytes) -> dict | None:
    """CDP after LLC/SNAP: version, TTL, checksum and TLVs (type, length with its header)."""
    if len(data) < 4 or data[0] not in (1, 2):
        return None
    info, off = _neighbor("cdp", src), 4
    while off + 4 <= len(data):
        kind = int.from_bytes(data[off:off + 2], "big")
        length = int.from_bytes(data[off + 2:off + 4], "big")
        if length < 4 or off + length > len(data):
            break
        value = data[off + 4:off + length]
        off += length
        if kind == 0x0001:
            info["hostname"] = _text(value)                               # device ID
        elif kind in (0x0002, 0x0016):                                    # addresses, mgmt
            info["addrs"] += [ip for ip in _cdp_addresses(value) if ip not in info["addrs"]]
        elif kind == 0x0003:
            info["port"] = _text(value)
        elif kind == 0x0005:
            info["fw"] = _text(value)                                     # software version
        elif kind == 0x0006:
            info["model"] = _text(value)                                  # platform
    return _finish_neighbor(info)


def parse_neighbor_frame(frame: bytes) -> dict | None:
    """A CDP or LLDP Ethernet frame (with or without an 802.1Q tag); None for anything else."""
    if len(frame) < 14:
        return None
    dst, src = frame[:6], frame[6:12]
    etype, off = int.from_bytes(frame[12:14], "big"), 14
    if etype == 0x8100 and len(frame) >= 18:
        etype, off = int.from_bytes(frame[16:18], "big"), 18
    if etype == ETH_P_LLDP:
        return parse_lldp(frame[off:], src)
    if etype <= 1500 and dst == CDP_MAC and frame[off:off + 8] == CDP_SNAP:
        return parse_cdp(frame[off + 8:off + etype], src)
    return None


def neighbor_sockets() -> tuple[list, list[str]]:
    """Raw sockets for LLDP and CDP that receive on every interface but loopback (they join
    those multicast groups), and the names of the interfaces. PermissionError without root
    or CAP_NET_RAW; OSError or AttributeError where there are no packet sockets."""
    socks: list = []
    try:
        for proto in (ETH_P_LLDP, ETH_P_802_2):
            socks.append(socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(proto)))
        names = []
        for idx, name in socket.if_nameindex():
            if name == "lo":
                continue
            names.append(name)
            for mac in LLDP_MACS + (CDP_MAC,):
                mreq = struct.pack("iHH8s", idx, PACKET_MR_MULTICAST, 6, mac)
                for s in socks:
                    try:
                        s.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)
                    except OSError:
                        pass
    except BaseException:
        for s in socks:
            s.close()
        raise
    return socks, names


def listen_neighbors(seconds: float, socks: list, stop: threading.Event | None = None
                     ) -> list[dict]:
    """The CDP and LLDP announcements heard for some seconds (or until stop): the last one of
    each device and protocol. It closes the sockets."""
    heard: dict[tuple[str, str], dict] = {}
    end = time.monotonic() + seconds
    outgoing = getattr(socket, "PACKET_OUTGOING", 4)
    try:
        while not (stop and stop.is_set()):
            left = end - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select(socks, [], [], min(left, 0.5))
            for s in ready:
                try:
                    frame, addr = s.recvfrom(65535)
                except OSError:
                    continue
                if addr[2] == outgoing:                    # what this computer sends
                    continue
                info = parse_neighbor_frame(frame)
                if info:
                    info["local"] = addr[0]
                    heard[(info["protocols"][0], info["mac"])] = info
    finally:
        for s in socks:
            s.close()
    return list(heard.values())


def merge_neighbors(found: list[dict], heard: list[dict]) -> list[dict]:
    """The Ubiquiti discovery answers plus what CDP and LLDP announced: each device once (the
    same MAC or IP), with every protocol that found it."""
    out = [dict(d, protocols=list(d.get("protocols") or ["ubnt"])) for d in found]
    for h in heard:
        # (by its MAC first: two at the same address, as two antennas just out of the box,
        # are two)
        same = (next((d for d in out if d["macs"] & h["macs"]), None)
                or next((d for d in out if set(h["addrs"]) & (set(d["addrs"]) | {d["ip"]})),
                        None))
        if same is None:
            out.append(dict(h, protocols=list(h["protocols"]), addrs=list(h["addrs"])))
            continue
        same["protocols"] += [p for p in h["protocols"] if p not in same["protocols"]]
        same["macs"] = same["macs"] | h["macs"]
        same["addrs"] = same["addrs"] + [ip for ip in h["addrs"] if ip not in same["addrs"]]
        for key in ("ip", "hostname", "model", "fw", "version", "port", "local"):
            if not same.get(key) and h.get(key):
                same[key] = h[key]
        if not same["pairs"]:
            same["pairs"] = [(same["mac"], ip) for ip in same["addrs"]]
    for d in out:
        d["protocols"].sort(key=list(PROTOCOL_NAMES).index)
    return sorted(out, key=lambda d: (_ip_key(d["ip"]) if d["ip"] else (2, ""), d["mac"]))


def protocols_label(d: dict) -> str:
    web = d.get("web")
    return ", ".join(f"web {web['scheme']}:{web['port']}" if p == "web" and web
                     else PROTOCOL_NAMES[p] for p in d.get("protocols") or ["ubnt"])


def _announced(d: dict) -> bool:
    """Whether it announced itself (Ubiquiti discovery, CDP or LLDP), rather than only being
    found by the active search."""
    return bool(set(d.get("protocols") or ["ubnt"]) & {"ubnt", "cdp", "lldp"})


def _fw_label(d: dict) -> str:
    """Its airOS: the version it announces, or 6 or 8 as its web answers (see is_airos)."""
    return f"airOS {d['version'] or (d.get('web') or {}).get('airos') or '?'}"


def _setup_warning(d: dict) -> str:
    """airOS 8's state before its login, as its web reads it: what it still lacks."""
    web = d.get("web") or {}
    if web.get("first_setup"):
        return tr("waiting for its first setup")
    return tr("factory password") if web.get("factory_password") else ""


# What marks a device that waits for its first setup or keeps its factory password, in a list
# of them (see discovery_line and setup_legend).
SETUP_MARK = "⚠"


def discovery_line(d: dict, marked: bool = False) -> str:
    """One line per device for the list: what it is, and at its end what airOS 8 still lacks
    before its login (its first setup, or a password other than the factory one). marked: that
    as SETUP_MARK after its IP instead, where a narrow window does not cut it off (a column
    for every line of the list: see setup_legend)."""
    announced = _announced(d)
    parts = [d["hostname"] or ("" if announced else d["mac"]) or tr("(no name)")]
    if announced or d["model"]:
        parts.append(d["model"] or "?")
    if d["wmode"] in DISCOVERY_WMODES:
        parts.append(DISCOVERY_WMODES[d["wmode"]][0])
    warning = _setup_warning(d)
    if marked:
        return f"{d['ip']:<15} {SETUP_MARK if warning else ' '} " + " · ".join(
            parts + [_fw_label(d)])
    parts += [x for x in (_fw_label(d), warning) if x]
    return f"{d['ip']:<15} " + " · ".join(parts)


def setup_legend(found: list[dict]) -> str:
    """What SETUP_MARK means in a list of these devices, to add to its text ("" if none has
    it): which of the two it is, when they all have the same one."""
    kinds = {bool((d.get("web") or {}).get("first_setup")) for d in found if _setup_warning(d)}
    if not kinds:
        return ""
    if kinds == {True}:
        return "\n" + tr("⚠ = it waits for its first setup.")
    if kinds == {False}:
        return "\n" + tr("⚠ = it keeps the factory password.")
    return "\n" + tr("⚠ = it waits for its first setup or keeps the factory password (its "
                     "data say which).")


def _suggest_ip(ip: str) -> str:
    """An IP of the same /24 as ip to put on your PC (.100, or .101)."""
    a, b, c, last = ip.split(".")
    return f"{a}.{b}.{c}.{101 if last == '100' else 100}"


def format_discovered(d: dict) -> str:
    """Everything the device announces (or its web answers), without logging into it."""
    web = d.get("web")
    mode = tr(DISCOVERY_WMODES[d["wmode"]][1]) if d["wmode"] in DISCOVERY_WMODES else ""
    rows = [(N_("Name"), d["hostname"]),
            (N_("Model"), d["model"] + (f" ({d['platform']})" if d["platform"]
                                        and d["platform"] != d["model"] else "")),
            ("Firmware", d["fw"] + (f" (airOS {d['version']})" if d["version"] else "")),
            (N_("Mode"), mode + (f", SSID {d['essid']}" if d["essid"] else "")),
            (N_("Up for"), _fmt_countdown(d["uptime"]) if d["uptime"] is not None else ""),
            ("MAC", d["mac"])]
    if web:
        rows.append(("Web", web["url"]))
    out = [f"{tr(label):<17}{value}" for label, value in rows if value]
    pairs = d["pairs"] or [(d["mac"], d["ip"])]
    for i, (mac, ip) in enumerate(pairs):
        out.append(f"{tr('Addresses') if i == 0 else '':<17}{ip}" + (f" ({mac})" if mac else ""))
    if d["src"] not in d["addrs"] and d["src"] != d["ip"]:
        out.append(f"{tr('Replied from'):<17}{d['src']}")
    notes = []
    if web and web["first_setup"]:
        notes.append(tr("It waits for its first setup (country, language and its terms, and the "
                        "administrator): connecting to it does it, as its web does."))
    elif web and web["factory_password"]:
        notes.append(tr("It still has the factory user and password: connecting to it asks for "
                        "new ones, as its web does."))
    usable = d["ip"] not in ("", "0.0.0.0") and not d["ip"].startswith("169.254.")
    if not usable:
        notes.append(tr("It has no usable IP: get in through the automatic IP 169.254.x.x "
                        "or set one up from the web."))
    elif d["local"]:
        notes.append(tr("It is on the network of your interface {iface}: you can connect.",
                        iface=d["local"]))
    else:
        notes.append(tr("None of your interfaces is on the network of {ip}. If it is on your "
                        "same cable, add an IP of its network to that interface (for example "
                        "{suggest}/24) to be able to get in.",
                        ip=d["ip"], suggest=_suggest_ip(d["ip"])) if not web else
                     tr("Its web answers from this computer: you can connect."))
    return "\n".join(out + [""] + notes)


def discovery_json(d: dict) -> dict:
    mode = DISCOVERY_WMODES.get(d["wmode"])
    return {"ip": d["ip"], "mac": d["mac"], "hostname": d["hostname"], "model": d["model"],
            "platform": d["platform"], "firmware": d["fw"], "airos": d["version"],
            "mode": mode[0].lower() if mode else None, "wmode": d["wmode"],
            "essid": d["essid"], "uptime": d["uptime"], "interface": d["local"],
            "addresses": [{"mac": m, "ip": ip} for m, ip in d["pairs"]],
            "port": d.get("port", ""), "protocols": list(d.get("protocols") or ["ubnt"]),
            "web": {k: d["web"][k] for k in ("url", "airos", "model", "first_setup",
                                             "factory_password")} if d.get("web") else None}


def discovery_table(found: list[dict]) -> str:
    head = ("IP", "MAC", tr("Name"), tr("Model"), tr("Mode"), "SSID", "Firmware", tr("Uptime"),
            tr("Found by"))
    rows = [(d["ip"], d["mac"], d["hostname"], d["model"],
             DISCOVERY_WMODES.get(d["wmode"], ("-",))[0], d["essid"], _fw_label(d),
             _fmt_countdown(d["uptime"]) if d["uptime"] is not None else "",
             protocols_label(d)) for d in found]
    widths = [min(32, max(len(str(r[i])) for r in [head] + rows)) for i in range(len(head))]
    return "\n".join("  ".join(str(c)[:w].ljust(w) for c, w in zip(r, widths)).rstrip()
                     for r in [head] + rows)


# -- the active search: the antennas' webs, address by address ---------------------------
#
# For when the antennas do not announce themselves (Ubiquiti discovery, CDP and LLDP turned
# off): it looks for their web, which is always on (it is how they are managed), and tells
# airOS by what it answers before any login, without sending a user or a password. airOS 8:
# /api/info/public, what its web reads before its login (its model; whether it waits for its
# first setup or keeps the factory password). airOS 6: the session cookie it hands out at
# once, AIROS_<its MAC>. On this computer's networks, ARP adds the MAC of each one: what the
# kernel learned asking for it (/proc/net/arp). Any other web is left out.

ACTIVE_PORTS = (443, 80)                 # its web: HTTPS, and HTTP (airOS 6, or a redirect)
ACTIVE_WORKERS = 64                      # addresses asked at the same time, at most
# New connections per second, one after another: never several at once. A router on the way
# may drop what goes beyond a few new connections at once, and then the same in the second
# round's burst (seen: 9 antennas behind one; 2 to 6 found asking 16 to 64 addresses at a
# time, all 9 with the connections spaced at 10, 20 or 40 per second). 64 at a time was
# already about 40 per second on average, but in bursts: spaced, it takes the same.
ACTIVE_RATE = 40.0
# Seconds to connect (a first packet lost on the way is sent again at 1 s: that one arrives) and
# to answer (an antenna's first HTTPS answer can take a few seconds: TLS on a small CPU). An
# address that did not answer either way is asked again at the end: a second round.
CONNECT_SECONDS, READ_SECONDS = 1.5, 8.0
ARP_TABLE = "/proc/net/arp"
_AIROS6_COOKIE = re.compile(r"AIROS_([0-9A-Fa-f]{12})=")
_ABSENT = {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}
_REDIRECTS = (301, 302, 303, 307, 308)


def is_airos(d: dict) -> bool:
    """Whether it is an airOS antenna (6 or 8), by the firmware it announces (Ubiquiti
    discovery, CDP or LLDP) or by what its web answers: the only devices the lists show."""
    return bool(d.get("version") or (d.get("web") or {}).get("airos"))


def airos_only(found: list[dict]) -> list[dict]:
    return [d for d in found if is_airos(d)]


def parse_ports(text: str) -> list[int]:
    """'443, 80,8443' -> [443, 80, 8443] (in that order, each once). ValueError if one is not a
    port, or there is none."""
    ports: list[int] = []
    for word in re.split(r"[\s,]+", str(text or "").strip()):
        if not word:
            continue
        if not re.fullmatch(r"[0-9]{1,5}", word) or not 1 <= int(word) <= 65535:
            raise ValueError(word)
        if int(word) not in ports:
            ports.append(int(word))
    if not ports:
        raise ValueError(text)
    return ports


def _colon_mac(mac: str) -> str:
    """Any form of a MAC -> 68:D7:9A:98:FB:FF (as discovery shows them; empty if it is none)."""
    m = norm_mac(mac)
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)) if m else ""


def _too_big_to_scan(net: ipaddress.IPv4Network) -> str:
    return tr("{range} has {count} addresses: the active search asks them one by one, {most} at "
              "most (a /22). Search a smaller range.", range=str(net),
              count=net.num_addresses, most=MAX_SWEEP)


def scan_ranges(target: str, ifaces) -> tuple[list[ipaddress.IPv4Network], list[str]]:
    """What the active search asks, address by address: with no target (the general
    broadcast), each network of this computer (beyond a /22, left out, with a note saying
    so); the broadcast of one of them, that network; a range in CIDR notation, that range; an
    IP or a name, that address. ValueError, with what to say, if it cannot."""
    own: list[tuple[ipaddress.IPv4Network, str]] = []
    for _idx, _name, ip, mask in ifaces:
        net = _network(ip, mask)
        if net is not None and net.prefixlen < 31 and net not in [n for n, _ in own]:
            own.append((net, ip))
    if target == BROADCAST:
        nets, notes = [], []
        for net, ip in own:
            if net.num_addresses <= MAX_SWEEP:
                nets.append(net)
            else:
                notes.append(tr("Your network {range} ({count} addresses) is left out: the "
                                "active search asks a /22 at most. Search a part of it, for "
                                "example {part}.", range=str(net), count=net.num_addresses,
                                part=str(ipaddress.IPv4Network(f"{ip}/24", strict=False))))
        return nets, notes
    net = next((n for n, _ in own if str(n.broadcast_address) == target), None)
    if net is None and _RANGE.fullmatch(target.strip()):
        net = target_range(target)
        if net is None:
            raise ValueError(tr('"{target}" is not a valid range (for example 10.3.0.0/24).',
                                target=target))
    if net is not None:
        if net.num_addresses > MAX_SWEEP:
            raise ValueError(_too_big_to_scan(net))
        return [net], []
    try:
        ip = socket.getaddrinfo(target, None, socket.AF_INET)[0][4][0]
    except (socket.gaierror, UnicodeError, IndexError, ValueError):
        raise ValueError(tr('"{target}" is not an IP or a name that can be resolved.',
                            target=target)) from None
    return [ipaddress.IPv4Network(f"{ip}/32")], []


def read_arp(path: str = ARP_TABLE) -> dict[str, tuple[str, str]]:
    """The kernel's ARP table (Linux): IP -> (MAC, interface) of what answered (complete
    entries only). Empty where there is none."""
    out = {}
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            next(f, None)                                     # (its heading)
            for line in f:
                cols = line.split()
                if len(cols) >= 6 and int(cols[2], 16) & 0x2 and norm_mac(cols[3]).strip("0"):
                    out[cols[0]] = (_colon_mac(cols[3]), cols[5])
    except (OSError, ValueError):
        return {}
    return out


class _Pacer:
    """New connections one by one, 1/rate seconds apart at least (see ACTIVE_RATE), from any
    thread: wait() before each one, False if stop was set meanwhile."""

    def __init__(self, rate: float, stop: threading.Event | None = None):
        self.gap, self.stop = 1.0 / rate, stop
        self.lock, self.next = threading.Lock(), 0.0

    def wait(self) -> bool:
        with self.lock:                          # (one at a time: each a gap after the last)
            delay = self.next - time.monotonic()
            if delay > 0:
                if self.stop is None:
                    time.sleep(delay)
                elif self.stop.wait(delay):
                    return False
            self.next = time.monotonic() + self.gap
        return not (self.stop is not None and self.stop.is_set())


def _get(ip: str, port: int, scheme: str, path: str, ctx, sock=None,
         pace: _Pacer | None = None):
    """One GET without credentials: (status, headers, at most 64 KiB of its body). On the
    connection given, or a new one, at its turn (TimeoutError, as if it did not answer, if the
    search was stopped while it waited). With --debug, in its file (what answered, or not,
    and how long it took)."""
    if sock is None and pace is not None and not pace.wait():
        raise TimeoutError("stopped")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Connection": "close"}
    url, started = f"{scheme}://{ip}:{port}{path}", time.monotonic()
    try:
        if sock is None:
            sock = socket.create_connection((ip, port), timeout=CONNECT_SECONDS)
        try:
            sock.settimeout(READ_SECONDS)
            if scheme == "https":
                sock = ctx.wrap_socket(sock)
            conn = http.client.HTTPConnection(ip, port, timeout=READ_SECONDS)
            conn.sock = sock
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status, answer, body = resp.status, resp.headers, resp.read(65536)
        finally:
            sock.close()
    except (OSError, http.client.HTTPException, ValueError) as e:
        if debugging():
            debug_request("GET", url, headers, None, time.monotonic() - started,
                          error=f"{type(e).__name__}: {e}")
        raise
    if debugging():
        debug_request("GET", url, headers, None, time.monotonic() - started, status, answer,
                      body)
    return status, answer, body


def _airos6_mac(headers) -> str | None:
    """airOS 6's answer to a request without a session: its session cookie, AIROS_<its MAC>,
    and the page that checks for cookies. Its MAC ("" if it did not say), or None if it is
    not that."""
    cookies = " ".join(headers.get_all("Set-Cookie") or [])
    m = _AIROS6_COOKIE.search(cookies)
    location = headers.get("Location") or ""
    if m or "cookiechecker" in location:
        return _colon_mac(m.group(1)) if m else ""
    return None


def _https_port(ip: str, status: int, location: str) -> int | None:
    """The port of this same address's HTTPS a redirect sends to (an HTTP that only sends
    there: airOS 8's, or airOS 6's with HTTPS on), or None if it is not that."""
    try:
        url = urllib.parse.urlsplit(location)
        port = url.port
    except ValueError:                            # (not a URL, or not a port)
        return None
    if status in _REDIRECTS and url.scheme == "https" and url.hostname in (ip, None):
        return port or 443
    return None


def _identify(ip: str, port: int, scheme: str, ctx, sock=None,
              pace: _Pacer | None = None) -> dict:
    """What the web at ip:port is, from what it answers before any login (OSError, SSLError or
    HTTPException if it does not speak that protocol): airOS 8 (its model and first setup),
    airOS 6 (its MAC), or another web (airos empty; where it sends, if it redirects)."""
    default = 443 if scheme == "https" else 80
    web = {"url": f"{scheme}://{ip}" + ("" if port == default else f":{port}"),
           "scheme": scheme, "port": port, "airos": "", "model": "", "mac": "",
           "first_setup": False, "factory_password": False, "status": 0, "location": ""}
    status, headers, body = _get(ip, port, scheme, "/api/info/public", ctx, sock, pace)
    info = _json(body) if status == 200 else None
    if isinstance(info, dict) and "product_name" in info:
        first, factory = setup_steps(info)
        web.update(airos="8", model=str(info.get("product_name") or ""), first_setup=first,
                   factory_password=factory)
        return web
    mac, location = _airos6_mac(headers), headers.get("Location") or ""
    if mac is None and _https_port(ip, status, location) is None:
        # (that page is airOS 8's; airOS 6 tells on its main one. Not when it only sends to
        # its HTTPS: that is asked instead)
        status, headers, _ = _get(ip, port, scheme, "/", ctx, pace=pace)
        mac, location = _airos6_mac(headers), headers.get("Location") or ""
    if mac is not None:
        web.update(airos="6", mac=mac)
    else:
        web.update(status=status, location=location)
    return web


def _probe_port(ip: str, port: int, ctx, pace: _Pacer | None = None
                ) -> tuple[str, dict | None]:
    """One port of one address: ("web", what it is), "closed" (it refused: no web there),
    "open" (it speaks no HTTP), "absent" (no route to it) or "" (no answer in time). Each new
    connection at its turn, with pace."""
    if pace is not None and not pace.wait():
        return "", None                           # (the search was stopped meanwhile)
    try:
        sock = socket.create_connection((ip, port), timeout=CONNECT_SECONDS)
    except ConnectionRefusedError:
        return "closed", None
    except OSError as e:
        return ("absent" if e.errno in _ABSENT else ""), None
    https = str(port).endswith("443")             # (443, 8443…: HTTPS first; else HTTP)
    for n, scheme in enumerate(("https", "http") if https else ("http", "https")):
        try:
            web = _identify(ip, port, scheme, ctx, sock if n == 0 else None, pace)
        except TimeoutError:                      # (slow, or lost on the way: the second
            return "", None                       # round asks again, not the other protocol)
        except (OSError, http.client.HTTPException, ValueError):  # (SSLError is an OSError)
            continue                              # (it may speak the other one)
        to = None if web["airos"] else _https_port(ip, web["status"], web["location"])
        if to is not None:
            try:                                  # (its HTTP only sends to HTTPS: airOS 8)
                return "web", _identify(ip, to, "https", ctx, pace=pace)
            except TimeoutError:                  # (slow there: worth asking again)
                return "", None
            except (OSError, http.client.HTTPException, ValueError):
                pass
        return "web", web
    return "open", None


def _probe_host(ip: str, ports: list[int], ctx, stop: threading.Event | None,
                pace: _Pacer | None = None) -> tuple[dict | None, bool]:
    """One address, port by port: (the web of the airOS antenna there, or None; whether that
    is final: not when a port did not answer, which is worth asking again)."""
    final = True
    for port in ports:
        if stop is not None and stop.is_set():
            break
        state, web = _probe_port(ip, port, ctx, pace)
        if web is not None and web["airos"]:
            return web, True
        if state in ("", "absent"):
            final = False
            if state == "absent":
                break
    return None, final


def _active_entry(ip: str, iface: str, web: dict, arp_mac: str) -> dict:
    """An antenna the active search found, as discovery's answers are (see parse_discovery),
    with protocols "web" and, if ARP gave its MAC, "arp"."""
    mac = web["mac"] or arp_mac
    return {"ip": ip, "src": ip, "mac": mac,
            "macs": {m for m in (norm_mac(mac), norm_mac(arp_mac)) if m}, "addrs": [ip],
            "pairs": [(arp_mac or mac, ip)] if mac else [], "hostname": "",
            "model": web["model"], "platform": "", "fw": "", "version": "", "essid": "",
            "wmode": None, "uptime": None, "local": iface, "port": "",
            "protocols": ["web"] + (["arp"] if arp_mac else []), "web": web}


def scan_addresses(ranges, ifaces) -> list[str]:
    """The addresses of those ranges the active search asks, each once and in order: their
    hosts (without their network and broadcast addresses), but this computer's own."""
    own = {i[2] for i in ifaces}
    out: list[str] = []
    for net in ranges:
        for host in list(net.hosts()) or [net.network_address]:
            ip = str(host)
            if ip not in own and ip not in out:
                out.append(ip)
    return out


def active_scan(target: str = BROADCAST, ports=ACTIVE_PORTS, interfaces=None, progress=None,
                stop: threading.Event | None = None, arp=read_arp,
                workers: int = ACTIVE_WORKERS, rate: float = ACTIVE_RATE
                ) -> tuple[list[dict], list[str]]:
    """The active search at target (what scan_ranges says), workers addresses at a time and
    rate new connections per second at most, one after another (0: no limit), and once more
    those that did not answer: (the airOS antennas it found, as discovery's answers, sorted
    by IP; the notes: networks left out). It never asks this computer's own addresses.
    progress(done, total, again) as addresses are done, from the calling thread (again: in
    the second round); stop: once it is set, back with what was found until then.
    ValueError, with what to say, for a target it cannot ask."""
    ifaces = local_ipv4() if interfaces is None else list(interfaces)
    ranges, notes = scan_ranges(target, ifaces)
    ports = list(ports)
    addresses = scan_addresses(ranges, ifaces)
    nets = [(n, i[1]) for i in ifaces if (n := _network(i[2], i[3])) is not None
            and n.prefixlen < 31]

    def iface_of(ip: str) -> str:
        return next((name for n, name in nets if ipaddress.IPv4Address(ip) in n), "")
    ctx, webs = _tls_context(), {}
    stop = stop or threading.Event()              # (also to stop its own on Ctrl+C here)
    pace = _Pacer(rate, stop) if rate else None

    def round_of(todo: list[str], again: bool) -> list[str]:
        """Ask todo; what did not answer, to ask again. Stopped (or interrupted here: then
        its addresses stop too), back at once with what was found: the ones being asked end
        by themselves, at their next step."""
        retry, done = [], 0
        if progress:
            progress(0, len(todo), again)
        if not todo:
            return retry
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(todo)))
        futures = {pool.submit(_probe_host, ip, ports, ctx, stop, pace): ip for ip in todo}
        pending = set(futures)
        try:
            while pending and not stop.is_set():
                finished, pending = concurrent.futures.wait(
                    pending, timeout=0.2, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in finished:
                    web, final = future.result()
                    if web is not None:
                        webs[futures[future]] = web
                    elif not final:
                        retry.append(futures[future])
                    done += 1
                    if progress:
                        progress(done, len(todo), again)
        except BaseException:
            stop.set()
            raise
        finally:
            pool.shutdown(wait=not stop.is_set(), cancel_futures=True)
        return retry
    retry = round_of(addresses, False)
    if retry and not stop.is_set():
        round_of(sorted(retry, key=_ip_key), True)
    table = arp() if any(iface_of(ip) for ip in webs) else {}
    found = [_active_entry(ip, iface_of(ip), web,
                           table.get(ip, ("", ""))[0] if iface_of(ip) else "")
             for ip, web in webs.items()]
    return sorted(found, key=lambda d: _ip_key(d["ip"])), notes


def merge_active(found: list[dict], active: list[dict]) -> list[dict]:
    """The devices discovery found plus the antennas the active search did: each device once
    (the same MAC or IP), with every protocol that found it and its web."""
    out = [dict(d, protocols=list(d.get("protocols") or ["ubnt"])) for d in found]
    for a in active:
        same = (next((d for d in out if d["macs"] & a["macs"]), None)      # (as above)
                or next((d for d in out if a["ip"] in {*d["addrs"], d["ip"], d["src"]}),
                        None))
        if same is None:
            out.append(dict(a, protocols=list(a["protocols"]), addrs=list(a["addrs"])))
            continue
        same["protocols"] += [p for p in a["protocols"] if p not in same["protocols"]]
        same["macs"] = same["macs"] | a["macs"]
        same["web"] = a["web"]
        for key in ("mac", "model", "local"):
            if not same.get(key) and a.get(key):
                same[key] = a[key]
        if not same["pairs"] and same["mac"]:
            same["pairs"] = [(same["mac"], ip) for ip in same["addrs"]]
    for d in out:
        d["protocols"].sort(key=list(PROTOCOL_NAMES).index)
    return sorted(out, key=lambda d: (_ip_key(d["ip"]) if d["ip"] else (2, ""), d["mac"]))
