"""Network: reading and changes with the same keys the web writes."""
from __future__ import annotations

import ipaddress
import re

from ..core.i18n import N_, tr
from ..core.config import Config, _find, _indices, _int, _next_index, _on, is_digits
from ..core.validation import _is_netmask, Problem

# A hardware interface's name (eth0, ath0, wlan0…), not a VLAN's, a bridge's or a tunnel's.
HW_DEVNAME = re.compile(r"(eth|ath|wlan)[0-9]+")


#
# Both webs (airOS 6: ubnt.net.js · airOS 8: Mlan/Wan/StaticIp/Dhcpc models) work on
# the interface with the "mlan" role (bridge) or the "wan" role (router/SOHO):
#   · static: netconf.N.ip/netmask, the default route (netmask 0) and
#     resolv.nameserver.1/2; the DHCP client of that interface is disabled.
#   · DHCP: dhcpc.status and a dhcpc.M entry with devname, fallback and
#     fallback_netmask; the interface IP becomes 0.0.0.0.
# Those keys are edited in place here, without regenerating the rest of the network.

NET_MODES = {"bridge": N_("Bridge"), "router": N_("Router"), "soho": N_("SOHO router")}


def netmode_label(mode: str | None) -> str | None:
    """Name of a network mode in the current language (the raw value if it is unknown)."""
    return tr(NET_MODES[mode]) if mode in NET_MODES else mode


def net_mode(cfg: Config) -> str:
    return (cfg.get("netmode") or "bridge").lower()


def net_iface(cfg: Config, n: int) -> dict:
    p = f"netconf.{n}"
    return {"n": n, "devname": cfg.get(f"{p}.devname") or "", "role": cfg.get(f"{p}.role") or "",
            "ip": cfg.get(f"{p}.ip") or "0.0.0.0", "netmask": cfg.get(f"{p}.netmask") or "",
            "enabled": _on(cfg, f"{p}.status"), "autoip": _on(cfg, f"{p}.autoip.status"),
            "mtu": cfg.get(f"{p}.mtu") or ""}


def net_ifaces(cfg: Config) -> list[dict]:
    return [net_iface(cfg, n) for n in _indices(cfg, "netconf")]


def main_role(cfg: Config) -> str:
    return "mlan" if net_mode(cfg) == "bridge" else "wan"


def main_iface(cfg: Config, flavor: str | None = None) -> dict | None:
    """Interface with the main IP: management (bridge) or WAN (router and SOHO).

    If none has the role (configurations from old firmwares), it is worked out like the
    web does: in bridge, br0; in router or SOHO, airOS 8 takes eth0 if the antenna is an
    access point and br0 if it is a station (_checkNetconfRoles), and airOS 6 takes eth0.
    That interface comes with "guessed": when saving, it is given the role, as the web
    does."""
    role = main_role(cfg)
    ifs = [i for i in net_ifaces(cfg) if i["enabled"]]
    found = next((i for i in ifs if i["role"] == role), None)
    if found is not None:
        return found
    if role == "mlan":
        guess = "br0"
    elif flavor == "v8":
        guess = "eth0" if (cfg.get("radio.1.mode") or "").lower() == "master" else "br0"
    else:
        guess = "eth0"
    found = next((i for i in ifs if i["devname"] == guess and not i["role"]), None)
    return dict(found, guessed=True) if found else None


def _role_fix(ifc: dict, role: str) -> dict[str, str]:
    """The web saves the role of the main interface if it was missing."""
    return {f"netconf.{ifc['n']}.role": role} if ifc.get("guessed") else {}


def lan_ifaces(cfg: Config) -> list[dict]:
    if net_mode(cfg) == "bridge":
        return []
    return [i for i in net_ifaces(cfg) if i["enabled"] and i["role"] == "lan"]


def ip_mode(cfg: Config, devname: str) -> str:
    """'dhcp', 'pppoe' or 'static', just like the web's getIpMode()."""
    if _on(cfg, "dhcpc.status"):
        n = _find(cfg, "dhcpc", devname=devname)
        if n is not None and _on(cfg, f"dhcpc.{n}.status"):
            return "dhcp"
    if _on(cfg, "ppp.status"):
        n = _find(cfg, "ppp", devname=devname)
        if n is not None and _on(cfg, f"ppp.{n}.status"):
            return "pppoe"
    return "static"


def default_route(cfg: Config) -> tuple[int | None, str]:
    """(index, gateway) of the default route: the one with netmask 0, as in the web."""
    for n in _indices(cfg, "route"):
        if (cfg.get(f"route.{n}.netmask") or "") in ("0", "0.0.0.0") and \
                (cfg.get(f"route.{n}.ip") or "0.0.0.0") == "0.0.0.0":
            return n, (cfg.get(f"route.{n}.gateway") or "") if _on(cfg, f"route.{n}.status") else ""
    return None, ""


def dns_servers(cfg: Config) -> list[str]:
    return [(cfg.get(f"resolv.nameserver.{i}.ip") or "")
            if _on(cfg, f"resolv.nameserver.{i}.status") else "" for i in (1, 2)]


def dhcp_fallback(cfg: Config, devname: str) -> tuple[str, str]:
    default = "192.168.1.20" if net_mode(cfg) == "bridge" else "192.168.10.1"
    n = _find(cfg, "dhcpc", devname=devname)
    if n is None:
        return default, "255.255.255.0"
    return (cfg.get(f"dhcpc.{n}.fallback") or default,
            cfg.get(f"dhcpc.{n}.fallback_netmask") or "255.255.255.0")


def dhcp_server(cfg: Config, devname: str) -> dict | None:
    k = _find(cfg, "dhcpd", devname=devname)
    if k is None:
        return None
    return {"k": k, "on": _on(cfg, "dhcpd.status") and _on(cfg, f"dhcpd.{k}.status"),
            "start": cfg.get(f"dhcpd.{k}.start") or "", "end": cfg.get(f"dhcpd.{k}.end") or "",
            "lease": cfg.get(f"dhcpd.{k}.lease_time") or "600"}


def bridge_of(cfg: Config, devname: str) -> int | None:
    return _find(cfg, "bridge", devname=devname)


def prefix_len(netmask: str) -> int | None:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen
    except ValueError:
        return None


def parse_mask(text: str) -> str | None:
    """'24', '/24' or '255.255.255.0' -> '255.255.255.0'."""
    t = text.strip().lstrip("/")
    if is_digits(t) and int(t) <= 32:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{t}").netmask)
    return t if _is_netmask(t) else None


PPPOE_DEFAULTS = {"fallback": "192.168.10.1", "fallback_netmask": "255.255.255.0",
                  "mtu": "1492", "mru": "1492"}


def ppp_index(cfg: Config, devname: str) -> int | None:
    """The WAN's ppp.N entry; like the web, it reuses the first one if it belongs to another
    interface."""
    k = _find(cfg, "ppp", devname=devname)
    if k is None:
        idx = _indices(cfg, "ppp")
        k = idx[0] if idx else None
    return k


def pppoe_settings(cfg: Config, devname: str) -> dict:
    k = ppp_index(cfg, devname)
    p = f"ppp.{k}" if k is not None else "ppp.0"
    get = lambda f: cfg.get(f"{p}.{f}") or PPPOE_DEFAULTS.get(f, "")  # noqa: E731
    return {"k": k, "name": cfg.get(f"{p}.name") or "", "password": cfg.get(f"{p}.password") or "",
            "service": cfg.get(f"{p}.pppoe_service") or "", "fallback": get("fallback"),
            "fallback_netmask": get("fallback_netmask"), "mtu": get("mtu"), "mru": get("mru"),
            "mppe": _on(cfg, f"{p}.require.mppe128")}


def pppoe_limits(cfg: Config, ifc: dict, flavor: str | None) -> tuple[int, int]:
    """Highest PPPoE (MTU, MRU) the web accepts (the minimum is 64 on both).

    airOS 8: 1518. airOS 6: the WAN MTU − 8, and 1500 for the MRU if MRU negotiation is
    off. Without knowing the version, the most permissive cap."""
    if flavor != "v6":
        return 1518, 1518
    top = (_int(ifc.get("mtu")) or 1500) - 8
    k = ppp_index(cfg, ifc["devname"])
    return top, 1500 if cfg.get(f"ppp.{k}.mru_negotiation") == "disabled" else top


def describe_ip(cfg: Config, ifc: dict) -> str:
    mode = ip_mode(cfg, ifc["devname"])
    if mode == "dhcp":
        ip, mask = dhcp_fallback(cfg, ifc["devname"])
        return tr("DHCP (fallback {ip}/{prefix})", ip=ip, prefix=prefix_len(mask) or mask)
    if mode == "pppoe":
        return tr("PPPoE (user {name})",
                  name=pppoe_settings(cfg, ifc["devname"])["name"] or "—")
    _, gw = default_route(cfg)
    prefix = prefix_len(ifc["netmask"]) or ifc["netmask"]
    if gw:
        return tr("static {ip}/{prefix}, gateway {gateway}", ip=ifc["ip"], prefix=prefix,
                  gateway=gw)
    return tr("static {ip}/{prefix}, no gateway", ip=ifc["ip"], prefix=prefix)


def _rename_nat(cfg: Config, old: set[str], new: str) -> dict[str, str]:
    """NAT, DMZ, management blocking and DHCPv6 follow the WAN ("ppp+" with PPPoE), like
    the web's updateNatDevname()."""
    up = {}
    for prefix in ("iptables.sys.masq", "iptables.sys.dmz", "iptables.sys.mgmt",
                   "ip6tables.sys.mgmt", "dhcp6c"):
        for n in _indices(cfg, prefix):
            if cfg.get(f"{prefix}.{n}.devname") in old:
                up[f"{prefix}.{n}.devname"] = new
    return up


def _disable_dhcpc(cfg: Config, dev: str) -> dict[str, str]:
    up = {}
    k = _find(cfg, "dhcpc", devname=dev)
    if k is not None and _on(cfg, f"dhcpc.{k}.status"):
        up[f"dhcpc.{k}.status"] = "disabled"
        if _on(cfg, "dhcpc.status") and not any(
                _on(cfg, f"dhcpc.{j}.status") for j in _indices(cfg, "dhcpc") if j != k):
            up["dhcpc.status"] = "disabled"
    return up


def _leave_pppoe(cfg: Config, dev: str) -> dict[str, str]:
    """When leaving PPPoE the web turns off the ppp section and the MSS clamping (tcpmss)."""
    k = _find(cfg, "ppp", devname=dev)
    if k is None or not _on(cfg, f"ppp.{k}.status"):
        return {}
    up = {f"ppp.{k}.status": "disabled", "ppp.status": "disabled"}
    if _on(cfg, "iptables.sys.tcpmss.status"):
        up["iptables.sys.tcpmss.status"] = "disabled"
    up.update(_rename_nat(cfg, {"ppp+"}, dev))
    return up


def _drop_static_route_dns(cfg: Config) -> dict[str, str]:
    """With DHCP or PPPoE the web disables the fixed default route and the fixed DNS."""
    up = {}
    r, _ = default_route(cfg)
    if r is not None and _on(cfg, f"route.{r}.status"):
        up[f"route.{r}.status"] = "disabled"
    for i in (1, 2):
        if _on(cfg, f"resolv.nameserver.{i}.status"):
            up[f"resolv.nameserver.{i}.status"] = "disabled"
    return up


def plan_dns(cfg: Config, dns1: str, dns2: str) -> dict[str, str]:
    up: dict[str, str] = {}
    if dns1 or dns2:
        if cfg.get("resolv.nameserver.status") == "disabled":
            up["resolv.nameserver.status"] = "enabled"
    for i, v in ((1, dns1), (2, dns2)):
        key = f"resolv.nameserver.{i}"
        if v:
            up[f"{key}.ip"], up[f"{key}.status"] = v, "enabled"
        elif f"{key}.ip" in cfg or f"{key}.status" in cfg:   # emptied: it is disabled
            up[f"{key}.ip"], up[f"{key}.status"] = "", "disabled"
    return up


def plan_static(cfg: Config, ifc: dict, ip: str, netmask: str, gateway: str,
                dns1: str, dns2: str) -> dict[str, str]:
    dev, n = ifc["devname"], ifc["n"]
    up: dict[str, str] = {}
    up.update(_disable_dhcpc(cfg, dev))
    up.update(_leave_pppoe(cfg, dev))
    up[f"netconf.{n}.ip"] = ip
    up[f"netconf.{n}.netmask"] = netmask
    r, _ = default_route(cfg)
    if gateway:
        r = _next_index(cfg, "route") if r is None else r
        up.update({"route.status": "enabled", f"route.{r}.status": "enabled",
                   f"route.{r}.devname": dev, f"route.{r}.ip": "0.0.0.0",
                   f"route.{r}.netmask": "0", f"route.{r}.gateway": gateway})
    elif r is not None:
        up[f"route.{r}.status"] = "disabled"
    up.update(plan_dns(cfg, dns1, dns2))
    return up


def plan_dhcp(cfg: Config, ifc: dict, fallback_ip: str, fallback_mask: str) -> dict[str, str]:
    dev, n = ifc["devname"], ifc["n"]
    k = _find(cfg, "dhcpc", devname=dev)
    k = _next_index(cfg, "dhcpc") if k is None else k
    up = _leave_pppoe(cfg, dev)
    up.update(_drop_static_route_dns(cfg))
    up.update({"dhcpc.status": "enabled", f"dhcpc.{k}.status": "enabled",
               f"dhcpc.{k}.devname": dev, f"dhcpc.{k}.fallback": fallback_ip,
               f"dhcpc.{k}.fallback_netmask": fallback_mask, f"netconf.{n}.ip": "0.0.0.0"})
    return up


def plan_pppoe(cfg: Config, ifc: dict, s: dict) -> dict[str, str]:
    """WAN over PPPoE with the same keys the web writes (setIpMode + setPppoe)."""
    dev, n = ifc["devname"], ifc["n"]
    k = ppp_index(cfg, dev)
    k = 1 if k is None else k
    p = f"ppp.{k}"
    up = _disable_dhcpc(cfg, dev)
    up.update(_drop_static_route_dns(cfg))
    up.update({
        "ppp.status": "enabled", f"{p}.status": "enabled", f"{p}.devname": dev,
        f"{p}.name": s["name"], f"{p}.password": s["password"],
        f"{p}.pppoe_service": s["service"], f"{p}.fallback": s["fallback"],
        f"{p}.fallback_netmask": s["fallback_netmask"], f"{p}.mtu": str(s["mtu"]),
        f"{p}.mru": str(s["mru"]), f"{p}.require.mppe128": "enabled" if s["mppe"] else "disabled",
        "iptables.status": "enabled", "iptables.sys.status": "enabled",
        "iptables.sys.tcpmss.status": "enabled",
        f"netconf.{n}.ip": "0.0.0.0", f"netconf.{n}.netmask": "255.255.255.255",
    })
    # Both webs enable IPv6 on the PPPoE session if the WAN has IPv6.
    ip6 = _on(cfg, f"netconf.{n}.ip6.status")
    if ip6 or _on(cfg, f"{p}.ipv6.status"):
        up[f"{p}.ipv6.status"] = "enabled" if ip6 else "disabled"
    up.update(_rename_nat(cfg, {dev}, "ppp+"))
    return up


def plan_lan(cfg: Config, lan: dict, ip: str, netmask: str, dhcp_on: bool,
             start: str = "", end: str = "", lease: str = "600") -> dict[str, str]:
    dev, n = lan["devname"], lan["n"]
    up = {f"netconf.{n}.ip": ip, f"netconf.{n}.netmask": netmask}
    k = _find(cfg, "dhcpd", devname=dev)
    if dhcp_on:
        if k is None:
            k = _next_index(cfg, "dhcpd")
            up.update({f"dhcpd.{k}.devname": dev, f"dhcpd.{k}.dnsproxy": "enabled"})
        up.update({"dhcpd.status": "enabled", f"dhcpd.{k}.status": "enabled",
                   f"dhcpd.{k}.start": start, f"dhcpd.{k}.end": end,
                   f"dhcpd.{k}.netmask": netmask, f"dhcpd.{k}.lease_time": lease})
    elif k is not None:
        up[f"dhcpd.{k}.status"] = "disabled"
    return up


def shift_into(addr: str, old_net: str, new_net: str) -> str:
    """Move an IP from one network to another keeping its host part (for DHCP ranges)."""
    try:
        old = ipaddress.IPv4Network(old_net, strict=False)
        new = ipaddress.IPv4Network(new_net, strict=False)
        host = int(ipaddress.IPv4Address(addr)) - int(old.network_address)
        if 0 <= host < new.num_addresses:
            return str(ipaddress.IPv4Address(int(new.network_address) + host))
    except ValueError:
        pass
    return addr


def _network(ip: str, mask: str) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except ValueError:
        return None


def _fallback(cfg: Config, ifc: dict, mode: str) -> tuple[str, str, str]:
    """The fallback IP of an interface on DHCP or PPPoE (the one it uses while no server
    answers, or its session is down): (its IP, its netmask, its key)."""
    if mode == "dhcp":
        k = _find(cfg, "dhcpc", devname=ifc["devname"])
        ip, mask = dhcp_fallback(cfg, ifc["devname"])
        return ip, mask, f"dhcpc.{k}.fallback"
    s = pppoe_settings(cfg, ifc["devname"])
    return s["fallback"], s["fallback_netmask"], f"ppp.{s['k']}.fallback"


def _not_an_ip(ip: str, netmask: str, key: str) -> list[Problem]:
    """An address that is its own network's or its broadcast (up to a /30): no device can
    have it, and the interface would be left without access."""
    net = _network(ip, netmask)
    if net is not None and net.prefixlen <= 30 and ip in (str(net.network_address),
                                                          str(net.broadcast_address)):
        return [Problem("error", key, tr("{ip} is the network or broadcast address of {net}; "
                                         "it cannot be used as an IP.", ip=ip, net=net))]
    return []


def _fallback_problems(cfg: Config, ifc: dict, mode: str) -> list[Problem]:
    """The WAN fallback IP must not fall in the LAN network: while it is in use, the device
    would have two interfaces on the same network and the LAN could be left without
    access."""
    ip, mask, key = _fallback(cfg, ifc, mode)
    fb = _network(ip, mask)
    out = []
    for lan in lan_ifaces(cfg):
        net = _network(lan["ip"], lan["netmask"])
        if fb is not None and net is not None and fb.overlaps(net):
            out.append(Problem("warning", key,
                               tr("The WAN fallback IP ({ip}) is on the same network as the LAN "
                                  "{lan} ({net}): while it uses it, the LAN may be left without "
                                  "access.", ip=ip, lan=lan["devname"], net=net)))
    return out


def net_problems(cfg: Config, flavor: str | None = None) -> list[Problem]:
    """Network errors that would leave the device unreachable or out of service."""
    out: list[Problem] = []
    ifc = main_iface(cfg, flavor)
    mode = ip_mode(cfg, ifc["devname"]) if ifc else ""
    if ifc and mode == "pppoe":
        s = pppoe_settings(cfg, ifc["devname"])
        p = f"ppp.{s['k']}"
        if not s["name"].strip():
            out.append(Problem("warning", f"{p}.name",
                               tr("PPPoE without a user: the provider will reject the "
                                  "connection.")))
        for field, hi in zip(("mtu", "mru"), pppoe_limits(cfg, ifc, flavor)):
            v = _int(s[field])
            if v is None or not 64 <= v <= hi:
                out.append(Problem("error", f"{p}.{field}",
                                   tr("The PPPoE {field} must be a number from 64 to {max} "
                                      "(now: {value}).", field=field.upper(), max=hi,
                                      value=s[field] or tr("empty"))))
    if ifc and mode in ("dhcp", "pppoe"):
        out += _not_an_ip(*_fallback(cfg, ifc, mode))
        if net_mode(cfg) != "bridge":
            out += _fallback_problems(cfg, ifc, mode)
    for lan in lan_ifaces(cfg):
        out += _not_an_ip(lan["ip"], lan["netmask"], f"netconf.{lan['n']}.ip")
    if ifc and mode == "static":
        key, ip = f"netconf.{ifc['n']}.ip", ifc["ip"]
        net = _network(ip, ifc["netmask"])
        if ip in ("", "0.0.0.0"):
            out.append(Problem("error", key,
                               tr("{iface} is on a static IP but has no address: you could not "
                                  "get back in.", iface=ifc["devname"])))
        elif net is not None:
            out += _not_an_ip(ip, ifc["netmask"], key)
            r, gw = default_route(cfg)
            try:
                gw_addr = ipaddress.IPv4Address(gw) if gw else None
            except ValueError:
                out.append(Problem("error", f"route.{r}.gateway",
                                   tr("The gateway {gateway} is not an IPv4 address.",
                                      gateway=gw)))
                gw_addr = None
            if gw_addr is not None:
                if gw == ip:
                    out.append(Problem("error", f"route.{r}.gateway",
                                       tr("The gateway cannot be the device's own IP.")))
                elif gw_addr not in net:
                    out.append(Problem("warning", f"route.{r}.gateway",
                                       tr("The gateway {gateway} is not on the network {net}: "
                                          "the device will not be able to reach it.",
                                          gateway=gw, net=net)))
            else:
                out.append(Problem("warning", key,
                                   tr("Without a gateway, the device will only be reachable "
                                      "from its own network.")))
            for lan in lan_ifaces(cfg):
                lnet = _network(lan["ip"], lan["netmask"])
                if lnet is not None and net.overlaps(lnet):
                    out.append(Problem("error", f"netconf.{lan['n']}.ip",
                                       tr("The WAN ({wan}) and the LAN {lan} ({lan_net}) are on "
                                          "the same network: the router would not know where to "
                                          "send the traffic.", wan=net, lan=lan["devname"],
                                          lan_net=lnet)))
    for lan in lan_ifaces(cfg):
        srv = dhcp_server(cfg, lan["devname"])
        net = _network(lan["ip"], lan["netmask"])
        if not srv or not srv["on"] or net is None:
            continue
        key = f"dhcpd.{srv['k']}.start"
        try:
            start, end = ipaddress.IPv4Address(srv["start"]), ipaddress.IPv4Address(srv["end"])
        except ValueError:
            out.append(Problem("error", key, tr("The DHCP server range is not valid.")))
            continue
        if start not in net or end not in net:
            out.append(Problem("error", key,
                               tr("The DHCP range {start}–{end} is not inside the LAN network "
                                  "({net}).", start=start, end=end, net=net)))
        elif start > end:
            out.append(Problem("error", key,
                               tr("The DHCP range starts ({start}) after it ends ({end}).",
                                  start=start, end=end)))
        elif start <= ipaddress.IPv4Address(lan["ip"]) <= end:
            out.append(Problem("warning", key,
                               tr("The LAN IP ({ip}) is inside the DHCP range: it could be "
                                  "handed out to another device.", ip=lan["ip"])))
    return out
