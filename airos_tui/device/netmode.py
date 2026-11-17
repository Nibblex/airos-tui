"""Network mode (bridge, router or SOHO), as the web changes it."""
from __future__ import annotations

import ipaddress
import re

from ..core.i18n import N_, tr
from ..core.config import Config, _find, _has, _indices, _int, _merge, _next_index, _on, gen3
from .network import (bridge_of, default_route, dhcp_fallback, HW_DEVNAME, ip_mode, main_iface,
                      net_ifaces, net_mode, _network, plan_dhcp)
from .templates import NETMODE_SECTIONS, NETMODE_TEMPLATES


def _in_sections(key: str, sections) -> bool:
    return any(key == s or key.startswith(s + ".") for s in sections)


def board_netmodes(board: dict) -> list[str]:
    """Modes the board supports (board.netmodes: 1 bridge, 2 router, 4 SOHO). Without that
    value, only bridge, as airOS 8 does."""
    mask = _int(board.get("board.netmodes"))
    mask = 1 if mask is None else mask
    return [m for m, bit in (("bridge", 1), ("router", 2), ("soho", 4)) if mask & bit]


def two_ports(board: dict) -> bool:
    return (_int(board.get("board.phycount")) or 1) > 1


def is_ap(cfg: Config) -> bool:
    return (cfg.get("radio.1.mode") or "managed").lower() == "master"


def iface_side(cfg: Config, devname: str) -> str:
    """'wired', 'wireless' or 'both' (a bridge with ports of both kinds)."""
    if devname.startswith(("ath", "wlan")):
        return "wireless"
    b = bridge_of(cfg, devname)
    ports = [cfg.get(f"bridge.{b}.port.{p}.devname") or ""
             for p in _indices(cfg, f"bridge.{b}.port")] if b is not None else []
    kinds = {iface_side(cfg, p) for p in ports if p}
    if not kinds:
        return "wired"
    return kinds.pop() if len(kinds) == 1 else "both"


SIDE_TEXT = {"wired": N_("the cable (Ethernet)"), "wireless": N_("the wireless link"),
             "both": N_("the cable and the wireless link")}


def _drop_iface(cfg: Config, dev: str) -> None:
    for n in _indices(cfg, "netconf"):
        if cfg.get(f"netconf.{n}.devname") == dev:
            for k in [k for k in cfg.keys() if k.startswith(f"netconf.{n}.")]:
                cfg.delete(k)
    for b in _indices(cfg, "bridge"):
        for p in _indices(cfg, f"bridge.{b}.port"):
            if cfg.get(f"bridge.{b}.port.{p}.devname") == dev:
                for k in [k for k in cfg.keys() if k.startswith(f"bridge.{b}.port.{p}.")]:
                    cfg.delete(k)


def netmode_template(flavor: str | None, target: str, ap: bool, eth1: bool) -> Config:
    """The default configuration the web loads when the mode is chosen."""
    tpl = NETMODE_TEMPLATES["v6" if flavor == "v6" else "v8"]
    if target == "router":
        if flavor == "v6":     # getNetworkConfig() in ubnt.net.js
            lan, wan = (("ath0", "br0" if eth1 else "eth0") if ap
                        else ("br0" if eth1 else "eth0", "ath0"))
        else:                  # i.a.get() in the airOS 8 web
            lan, wan = (("br0", "br1" if eth1 else "eth0") if ap
                        else ("br1" if eth1 else "eth0", "br0"))
        text = tpl["router_with_bridge" if eth1 else "router_no_bridge"]
        text = text.replace("LAN", lan).replace("WAN", wan)
    else:
        text = tpl[target]
    cfg = Config(text)
    cfg.set("netmode", target)
    if not eth1:               # single-port boards: the web removes eth1
        _drop_iface(cfg, "eth1")
    return cfg


def wpa_in_use(cfg: Config, flavor: str | None) -> bool:
    """Is WPA in use? It decides whether the bridge lets EAPOL through (ebtables.sys.eap),
    like the web."""
    if is_ap(cfg):
        if flavor == "v6":
            return (_on(cfg, "aaa.status") and _on(cfg, "aaa.1.status") and _has(cfg, "aaa.1.wpa")
                    and cfg.get("aaa.1.wpa.mode") != "0")
        return (_on(cfg, "aaa.status") and (cfg.get("aaa.1.status") or "enabled") == "enabled"
                and (_int(cfg.get("aaa.1.wpa.mode")) or 0) > 0)
    if flavor == "v6":
        p = "wpasupplicant.profile.1.network.1.proto.1"
        return (_on(cfg, "wpasupplicant.status") and _on(cfg, "wpasupplicant.device.1.status")
                and _has(cfg, p) and cfg.get(f"{p}.mode") != "0")
    return (_on(cfg, "wpasupplicant.status")
            and (cfg.get("wpasupplicant.device.1.status") or "enabled") == "enabled"
            and any((cfg.get(f"wpasupplicant.profile.1.network.{i}.key_mgmt.1.name") or "NONE")
                    != "NONE" for i in (1, 2)))


def bridge_firewall(cfg: Config, flavor: str | None) -> dict[str, str]:
    """ARP-NAT (station without WDS), EAPOL pass-through (with WPA) and the VLANs of their
    ports on the bridges, as when saving.

    airOS 6 (updateStatuses) enables or disables the sections the template brings; airOS 8
    (_saveEbtablesArpnat/_saveEbtablesEap/_saveEbtablesVlan) writes one entry per wireless
    port and one per VLAN of a bridge's port (with it the bridge leaves that VLAN's tagged
    traffic to it)."""
    up: dict[str, str] = {}
    bridges = [b for b in _indices(cfg, "bridge") if cfg.get(f"bridge.{b}.devname")]
    ports = [cfg.get(f"bridge.{b}.port.{p}.devname") or ""
             for b in bridges for p in _indices(cfg, f"bridge.{b}.port")]
    sta, wds, wpa = not is_ap(cfg), _on(cfg, "wireless.1.wds.status"), wpa_in_use(cfg, flavor)
    if flavor == "v6":
        state = "enabled" if bridges else "disabled"
        for sec in ("bridge", "ebtables", "ebtables.sys"):
            if _has(cfg, sec):
                up[f"{sec}.status"] = state
        if bridges and _has(cfg, "ebtables.sys"):
            arpnat = sta and not wds and cfg.get("wireless.1.macclone") != "enabled"
            if _has(cfg, "ebtables.sys.arpnat"):
                up["ebtables.sys.arpnat.status"] = "enabled" if arpnat else "disabled"
            if _has(cfg, "ebtables.sys.eap"):
                up["ebtables.sys.eap.status"] = "enabled" if wpa else "disabled"
        vlans = [n for n in _indices(cfg, "vlan") if cfg.get(f"vlan.{n}.devname") in ports]
        for j, n in enumerate(vlans, 1):          # recreateEbtableSysVlan()
            pre = f"vlan.{n}."
            for k in cfg.keys():
                if k.startswith(pre):
                    up[f"ebtables.sys.vlan.{j}.{k[len(pre):]}"] = cfg.get(k)
        if vlans:
            up["ebtables.sys.vlan.status"] = "enabled"
        return up
    wireless = [p for p in ports if p.startswith(("ath", "wlan"))]
    for name, cond, devs in (("arpnat", sta and not wds, wireless),
                             ("eap", wpa, [p for p in wireless if "." not in p])):
        if cond and devs:
            up.update({"ebtables.status": "enabled", "ebtables.sys.status": "enabled",
                       f"ebtables.sys.{name}.status": "enabled"})
            for j, dev in enumerate(devs, 1):
                up[f"ebtables.sys.{name}.{j}.status"] = "enabled"
                up[f"ebtables.sys.{name}.{j}.devname"] = dev
    vlans = [n for n in _indices(cfg, "vlan") if cfg.get(f"vlan.{n}.devname") in ports]
    if vlans:                                     # (an entry for each enabled one)
        up.update({"ebtables.status": "enabled", "ebtables.sys.status": "enabled",
                   "ebtables.sys.vlan.status": "enabled"})
        on = [n for n in vlans if (cfg.get(f"vlan.{n}.status") or "enabled") == "enabled"]
        for j, n in enumerate(on, 1):
            up.update({f"ebtables.sys.vlan.{j}.status": "enabled",
                       f"ebtables.sys.vlan.{j}.id": cfg.get(f"vlan.{n}.id") or "",
                       f"ebtables.sys.vlan.{j}.devname": cfg.get(f"vlan.{n}.devname") or ""})
    return up


def nat_devname(cfg: Config, ifc: dict) -> str:
    """NAT, DMZ and management blocking go to "ppp+" if the WAN uses PPPoE."""
    return "ppp+" if ip_mode(cfg, ifc["devname"]) == "pppoe" else ifc["devname"]


def _ipt_toggle(cfg: Config, section: str, table: str, dev: str, on: bool) -> dict[str, str]:
    """The web's natEnabled()/blockMgmtIpv(): the WAN entry in <table>.sys.<something>."""
    up: dict[str, str] = {}
    k = _find(cfg, section, devname=dev)
    if k is None:
        if not on:
            return up
        k = _next_index(cfg, section)
        up[f"{section}.{k}.devname"] = dev
    up[f"{section}.{k}.status"] = "enabled" if on else "disabled"
    if on:
        up.update({f"{table}.status": "enabled", f"{table}.sys.status": "enabled",
                   f"{section}.status": "enabled"})
    elif not any(_on(cfg, f"{section}.{j}.status") for j in _indices(cfg, section) if j != k):
        up[f"{section}.status"] = "disabled"
    return up


def _ipt_on(cfg: Config, section: str, dev: str) -> bool:
    k = _find(cfg, section, devname=dev)
    return k is not None and _on(cfg, f"{section}.status") and _on(cfg, f"{section}.{k}.status")


def nat_on(cfg: Config, ifc: dict) -> bool:
    return _ipt_on(cfg, "iptables.sys.masq", nat_devname(cfg, ifc))


def mgmt_blocked(cfg: Config, ifc: dict) -> bool:
    return _ipt_on(cfg, "iptables.sys.mgmt", nat_devname(cfg, ifc))


def plan_nat(cfg: Config, ifc: dict, on: bool) -> dict[str, str]:
    return _ipt_toggle(cfg, "iptables.sys.masq", "iptables", nat_devname(cfg, ifc), on)


def plan_block_mgmt(cfg: Config, ifc: dict, on: bool) -> dict[str, str]:
    dev = nat_devname(cfg, ifc)
    up = _ipt_toggle(cfg, "iptables.sys.mgmt", "iptables", dev, on)
    up.update(_ipt_toggle(cfg, "ip6tables.sys.mgmt", "ip6tables", dev, on))
    return up


def switch_netmode(cfg: Config, flavor: str | None, target: str, board: dict) -> Config:
    """The configuration the web saves when the mode is changed and what it proposes is
    accepted."""
    flavor = "v6" if flavor == "v6" else "v8"
    new = cfg.copy()
    for k in cfg.keys():
        if _in_sections(k, NETMODE_SECTIONS[flavor]):
            new.delete(k)
    tpl = netmode_template(flavor, target, is_ap(cfg), two_ports(board))
    _merge(new, {k: tpl.get(k) for k in tpl.keys()})
    if flavor == "v6":         # airOS 6 keeps the MTU of the interfaces that remain
        mtus = {i["devname"]: i["mtu"] for i in net_ifaces(cfg) if i["mtu"]}
        for i in net_ifaces(new):
            if i["devname"] in mtus:
                new.set(f"netconf.{i['n']}.mtu", mtus[i["devname"]])
    wan = main_iface(new, flavor) if target != "bridge" else None
    if wan:
        fb, mask = dhcp_fallback(new, wan["devname"])
        _merge(new, plan_dhcp(new, wan, fb, mask))       # what the WAN form saves
        if flavor == "v8" and target == "router":        # net:netmode:changed: NAT enabled
            _merge(new, plan_nat(new, wan, True))
        if flavor == "v6":
            if net_mode(cfg) != target:                  # oldNetMode: management blocked
                _merge(new, plan_block_mgmt(new, wan, True))
            new.set("route.status", "enabled")
    return _merge(new, bridge_firewall(new, flavor))


def restore_network(cfg: Config, original: Config, flavor: str | None) -> Config:
    """Go back to the mode the device has: its network as it is (the web keeps a copy)."""
    secs = NETMODE_SECTIONS["v6" if flavor == "v6" else "v8"]
    new = cfg.copy()
    for k in cfg.keys():
        if _in_sections(k, secs):
            new.delete(k)
    for k in original.keys():
        if _in_sections(k, secs):
            new.set(k, original.get(k))
    if "netmode" in original:
        new.set("netmode", original.get("netmode"))
    else:
        new.delete("netmode")
    return new


def network_losses(cfg: Config, flavor: str = "v8") -> list[str]:
    """What the current network has configured and is lost when it is rebuilt (the sections
    that flavor's web deletes: NETMODE_SECTIONS; airOS 6 keeps its VLANs and UPnP)."""
    checks = [("iptables.sys.masq", "NAT"), ("iptables.sys.portfw", N_("port forwarding")),
              ("iptables.sys.fw", N_("firewall rules")),
              ("ebtables.sys.fw", N_("firewall rules")), ("iptables.sys.dmz", "DMZ"),
              ("vlan", "VLAN"), ("tshaper", N_("traffic shaping")), ("ppp", "PPPoE"),
              ("dhcpd", N_("DHCP server")), ("dhcprelay", N_("DHCP relay")),
              ("dhcp6d", N_("DHCPv6 server")), ("route6", N_("IPv6 routes")),
              ("igmpproxy", N_("multicast (IGMP)")), ("upnpd", "UPnP")]
    sections = NETMODE_SECTIONS["v6" if flavor == "v6" else "v8"]
    out: list[str] = []
    for prefix, label in checks:
        if not _in_sections(prefix, sections):
            continue
        section, entries = cfg.get(f"{prefix}.status"), _indices(cfg, prefix)
        if section == "disabled" or label in out:
            continue
        if (any(_on(cfg, f"{prefix}.{n}.status") for n in entries) if entries
                else section == "enabled"):
            out.append(label)
    r, _ = default_route(cfg)
    extra = [n for n in _indices(cfg, "route") if n != r and _on(cfg, f"route.{n}.status")]
    if extra:
        out.append(N_("static routes"))
    if any(cfg.get(f"netconf.{n}.alias.{a}.ip") for n in _indices(cfg, "netconf")
           for a in _indices(cfg, f"netconf.{n}.alias")):
        out.append(N_("additional IPs (aliases)"))
    return [tr(label) for label in out]


def lan_range(ip: str, netmask: str) -> tuple[str, str]:
    """DHCP range for a LAN: the part of its network after the device's IP (before it, if it
    is the last one); ("", "") for a network too small."""
    net = _network(ip, netmask)
    if net is None or net.num_addresses < 4:
        return "", ""
    first, last = int(net.network_address) + 1, int(net.broadcast_address) - 1
    me = int(ipaddress.IPv4Address(ip))
    lo, hi = (me + 1, last) if me < last else (first, me - 1)
    return str(ipaddress.IPv4Address(lo)), str(ipaddress.IPv4Address(hi))


# -- management VLAN (airOS 8: bridge mode with the simple network configuration) ----------
#
# Its web (the network helper's setMgmtVlanId and disableMgmtVlan) puts the management on a
# VLAN this way: a VLAN with that ID on each hardware interface, a bridge br1 with them as
# its ports and the management interface (its role, its address and what goes with it) on
# br1; br0 goes on bridging the untagged traffic, without an address. Off, the management
# goes back to br0 and there are no VLANs, nor br1. When saving it writes the network again
# from its model, so the entries are numbered 1, 2, 3… without gaps, and the bridges'
# ebtables entries too (ARP-NAT, EAPOL and the VLANs of their ports: with these br0 leaves
# the management's tagged traffic to the VLANs).

MGMT_VLAN_RANGE = (2, 4094)
_BRIDGE_EBTABLES = ("ebtables.sys.arpnat.", "ebtables.sys.eap.", "ebtables.sys.vlan.")
# What its management writes in its interface's netconf (its model and its static IP's)
# moves with it. The rest is the interface's own, or its IP aliases' (saved by the
# interface's name): it stays.
_MGMT_KEYS = ("role", "ip", "netmask", "autoip.status")
DEFAULT_MTU = "1500"


def _mgmt_key(rest: str) -> bool:
    return rest in _MGMT_KEYS or rest.startswith("ip6.")


def mgmt_vlan_offered(cfg: Config, flavor: str | None, board: dict) -> bool:
    """Where its web has the switch: airOS 8, bridge mode, not a Gen3 board (60 GHz: there it
    is something else)."""
    return flavor == "v8" and net_mode(cfg) == "bridge" and not gen3(board)


def advanced_network(cfg: Config) -> bool:
    """The web's advanced network configuration (VLANs and bridges by hand): there it does
    not offer the switch."""
    return cfg.get("gui.network.advanced.status") == "enabled"


def _ports(cfg: Config, devname: str) -> list[str]:
    b = bridge_of(cfg, devname)
    if b is None:
        return []
    return [cfg.get(f"bridge.{b}.port.{p}.devname") or ""
            for p in _indices(cfg, f"bridge.{b}.port")
            if (cfg.get(f"bridge.{b}.port.{p}.status") or "enabled") == "enabled"]


def _hw_ports(cfg: Config) -> list[str]:
    """The hardware interfaces, in the order of br0's ports."""
    return [p for p in _ports(cfg, "br0") if HW_DEVNAME.fullmatch(p)]


def mgmt_vlan(cfg: Config) -> int | None:
    """Its getMgmtVlanId: br1 with a port for each hardware interface; the ID of its first."""
    ports = _ports(cfg, "br1")
    if not ports or len(ports) != len(_hw_ports(cfg)) or "." not in ports[0]:
        return None
    return _int(ports[0].split(".", 1)[1])


def mgmt_vlan_problem(value) -> str | None:
    """Its rules for the VLAN ID: required, a whole number, from 2 to 4094."""
    text = str(value).strip()
    if not text:
        return tr("VLAN ID required.")
    if not re.fullmatch(r"[0-9]+", text):
        return tr("VLAN ID is invalid")
    low, high = MGMT_VLAN_RANGE
    return None if low <= int(text) <= high else tr(
        "VLAN ID must be between {min} and {max}", min=low, max=high)


def _renumber(cfg: Config, prefix: str) -> None:
    """prefix.N.* numbered 1, 2, 3… again, in their order."""
    moves = {old: new for new, old in enumerate(_indices(cfg, prefix), 1) if old != new}
    if not moves:
        return
    pat = re.compile(rf"^{re.escape(prefix)}\.(\d+)\.(.*)$")
    entries = []
    for k in list(cfg.keys()):
        m = pat.match(k)
        if m and int(m.group(1)) in moves:
            entries.append((moves[int(m.group(1))], m.group(2), cfg.get(k)))
            cfg.delete(k)
    for n, rest, value in entries:
        cfg.set(f"{prefix}.{n}.{rest}", value)


def _add_netconf(cfg: Config, devname: str) -> int:
    """Its createNetconf and saveMtu for a VLAN or a bridge."""
    n = _next_index(cfg, "netconf")
    _merge(cfg, {f"netconf.{n}.status": "enabled", f"netconf.{n}.devname": devname,
                 f"netconf.{n}.autoip.status": "disabled", f"netconf.{n}.mtu": DEFAULT_MTU})
    return n


def plan_mgmt_vlan(cfg: Config, vlan_id: int | None) -> dict[str, str | None]:
    """What its web saves when the management VLAN is turned on with vlan_id, or off (None):
    the VLANs, br1 and their netconf, the management settings moved, the routes and DHCP
    clients of the management with it, and the bridges' ebtables entries again (None =
    delete)."""
    new = cfg.copy()
    ifc = main_iface(new, "v8")
    if ifc is None:
        return {}
    old, pre = ifc["devname"], f"netconf.{ifc['n']}."
    moved = {k[len(pre):]: new.get(k) for k in new.keys()
             if k.startswith(pre) and _mgmt_key(k[len(pre):])}
    if ifc.get("guessed"):                 # (as its web saves it: the role, which it lacked)
        moved.setdefault("role", "mlan")
    for rest in moved:
        new.delete(pre + rest)
    # disableMgmtVlan: no VLANs (with their netconf) and no br1. IP aliases are saved by
    # their interface's name: br1's stay if there is a br1 again.
    gone = {f"{new.get(f'vlan.{n}.devname')}.{new.get(f'vlan.{n}.id')}"
            for n in _indices(new, "vlan")} | {"br1"}
    b1 = bridge_of(new, "br1")
    drop = [k for k in new.keys() if k == "vlan.status" or k.startswith("vlan.")
            or (b1 is not None and k.startswith(f"bridge.{b1}."))]
    aliases = {}
    for n in _indices(new, "netconf"):
        name, pn = new.get(f"netconf.{n}.devname"), f"netconf.{n}."
        if name in gone:
            for k in [k for k in new.keys() if k.startswith(pn)]:
                drop.append(k)
                if k.startswith(pn + "alias."):
                    aliases[(name, k[len(pn):])] = new.get(k)
    for k in drop:
        new.delete(k)
    target = "br0"
    if vlan_id:                                   # setMgmtVlanId
        ports = []
        for hw in _hw_ports(new):
            n = _next_index(new, "vlan")
            _merge(new, {"vlan.status": "enabled", f"vlan.{n}.status": "enabled",
                         f"vlan.{n}.id": str(vlan_id), f"vlan.{n}.devname": hw})
            ports.append(f"{hw}.{vlan_id}")
            _add_netconf(new, ports[-1])
        b = _next_index(new, "bridge")
        _merge(new, {f"bridge.{b}.status": "enabled", f"bridge.{b}.stp.status": "disabled",
                     f"bridge.{b}.devname": "br1"})
        for p, port in enumerate(ports, 1):
            _merge(new, {f"bridge.{b}.port.{p}.status": "enabled",
                         f"bridge.{b}.port.{p}.devname": port})
        _add_netconf(new, "br1")
        target = "br1"
        n0 = _find(new, "netconf", devname="br0")
        if n0 is not None:                        # (a bridge's own: no automatic IP)
            new.set(f"netconf.{n0}.autoip.status", "disabled")
    t = _find(new, "netconf", devname=target)
    for rest, value in moved.items():
        new.set(f"netconf.{t}.{rest}", value)
    for (name, rest), value in aliases.items():
        n = _find(new, "netconf", devname=name)
        if n is not None:
            new.set(f"netconf.{n}.{rest}", value)
    for sec in ("route", "route6", "dhcpc", "dhcp6c"):
        for n in _indices(new, sec):
            if new.get(f"{sec}.{n}.devname") == old:
                new.set(f"{sec}.{n}.devname", target)
    for prefix in ("netconf", "bridge", "vlan"):
        _renumber(new, prefix)
    for k in [k for k in new.keys() if k.startswith(_BRIDGE_EBTABLES)]:
        new.delete(k)
    _merge(new, bridge_firewall(new, "v8"))
    return {k: after for k, _, after in cfg.diff(new)}
