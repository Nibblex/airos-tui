"""The advanced network configuration of the airOS 8 web (its Network page's "Configuration
Mode"): switching between simple and advanced as its web does (going back to simple, when the
configuration does not fit it, its web builds the network again from its mode's template) and,
in advanced mode, its VLANs and its bridges, written as its web saves them: their sections, the
netconf entry of each enabled one, the DHCP relay of option 82 (bridge mode), the rules of an
interface that goes away (deleted) or is disabled (disabled) and the bridges' ebtables."""
from __future__ import annotations

import re

from ..core.i18n import tr
from ..core.config import Config, _find, _indices, _int, _merge, js_int_like, _v8_on
from .network import (default_route, dhcp_fallback, dns_servers, HW_DEVNAME, ip_mode, lan_ifaces,
                      main_iface, net_mode, plan_dhcp, plan_pppoe, plan_static, pppoe_settings)
from .templates import NETMODE_SECTIONS
from .netmode import (bridge_firewall, _BRIDGE_EBTABLES, DEFAULT_MTU, _in_sections, is_ap,
                      mgmt_blocked, nat_devname, nat_on, netmode_template, plan_block_mgmt,
                      plan_nat, _renumber, two_ports)
from .services import dmz_mgmt_exceptions
from .firewall import firewall_on, firewall_rules, plan_firewall, ui_devname
from .dhcp import lan_dhcp, plan_lan_dhcp

VLAN_IDS = (2, 4094)
CFG_MODE_KEY = "gui.network.advanced.status"


def hw_interfaces(cfg: Config) -> list[str]:
    """Its hardware interfaces (in its order: by name): the ones with a netconf entry and the
    ones a bridge or a VLAN uses (its router templates, as this program saves them, have no
    netconf entry for the wireless one: without it, rewriting the bridges would leave it out
    of its bridge)."""
    names = {cfg.get(f"netconf.{n}.devname") or "" for n in _indices(cfg, "netconf")}
    names |= {cfg.get(f"bridge.{b}.port.{p}.devname") or "" for b in _indices(cfg, "bridge")
              for p in _indices(cfg, f"bridge.{b}.port")}
    names |= {cfg.get(f"vlan.{n}.devname") or "" for n in _indices(cfg, "vlan")}
    return sorted(d for d in names if HW_DEVNAME.fullmatch(d))


def vlan_list(cfg: Config) -> list[dict]:
    """Its VLANs as its models load them: parent interface, ID, its name (parent.ID), enabled
    and comment."""
    out = []
    for n in _indices(cfg, "vlan"):
        parent, vid = cfg.get(f"vlan.{n}.devname") or "", cfg.get(f"vlan.{n}.id") or ""
        if parent and vid:
            out.append({"parent": parent, "id": vid, "devname": f"{parent}.{vid}",
                        "on": _v8_on(cfg, f"vlan.{n}.status"),
                        "comment": cfg.get(f"vlan.{n}.comment") or ""})
    return out


def _option82(cfg: Config) -> set[str]:
    """The bridges with DHCP option 82 (its ebtables.sys.dhcp_relay entries, bridge mode)."""
    if net_mode(cfg) != "bridge":
        return set()
    return {cfg.get(f"ebtables.sys.dhcp_relay.{n}.devname") or ""
            for n in _indices(cfg, "ebtables.sys.dhcp_relay")
            if _v8_on(cfg, f"ebtables.sys.dhcp_relay.{n}.status")}


def bridge_list(cfg: Config) -> list[dict]:
    """Its bridges as its models load them: name, enabled, STP, its ports (the enabled ones
    that are interfaces it offers: hardware, enabled VLANs and bridges), comment and, in
    bridge mode, DHCP option 82."""
    names = [(n, cfg.get(f"bridge.{n}.devname") or "") for n in _indices(cfg, "bridge")]
    offered = (set(hw_interfaces(cfg)) | {v["devname"] for v in vlan_list(cfg) if v["on"]}
               | {d for n, d in names if d and _v8_on(cfg, f"bridge.{n}.status")})
    relay, out = _option82(cfg), []
    for n, dev in names:
        if not dev:
            continue
        ports = [cfg.get(f"bridge.{n}.port.{p}.devname") or ""
                 for p in _indices(cfg, f"bridge.{n}.port")
                 if _v8_on(cfg, f"bridge.{n}.port.{p}.status")]
        out.append({"devname": dev, "on": _v8_on(cfg, f"bridge.{n}.status"),
                    "stp": _v8_on(cfg, f"bridge.{n}.stp.status"),
                    "ports": [p for p in ports if p in offered],
                    "comment": cfg.get(f"bridge.{n}.comment") or "", "option82": dev in relay})
    return out


def _roles(cfg: Config) -> dict[str, str]:
    return {cfg.get(f"netconf.{n}.devname") or "": cfg.get(f"netconf.{n}.role") or ""
            for n in _indices(cfg, "netconf") if cfg.get(f"netconf.{n}.role")}


def in_use(cfg: Config, devname: str) -> str | None:
    """Its getUsedIfcs, said: what uses the interface (a bridge's port, the management, the
    WAN or a LAN), or None."""
    for b in bridge_list(cfg):
        if devname in b["ports"]:
            return tr("it is a port of {bridge}", bridge=ui_devname(b["devname"]))
    role = _roles(cfg).get(devname)
    return {"mlan": tr("the management is on it"), "wan": tr("it is the WAN"),
            "lan": tr("it is a LAN")}.get(role) if role else None


def free_ports(cfg: Config) -> list[str]:
    """What its bridge dialog offers to add: the interfaces it offers that nothing uses, but
    bridges."""
    vl = [v["devname"] for v in vlan_list(cfg) if v["on"]]
    return [d for d in hw_interfaces(cfg) + vl if not in_use(cfg, d) and "br" not in d]


def vlan_id_problem(cfg: Config, parent: str, value) -> str | None:
    """Its rules for a new VLAN's ID: required, whole, 2 to 4094, not already on that
    interface."""
    text = str(value).strip()
    if not text:
        return tr("VLAN ID required.")
    n = js_int_like(text)
    if n is None:
        return tr("VLAN ID value invalid.")
    if not VLAN_IDS[0] <= n <= VLAN_IDS[1]:
        return tr("VLAN ID must be between 2 and 4094.")
    if any(v["parent"] == parent and _int(v["id"]) == n for v in vlan_list(cfg)):
        return tr("VLAN already exists.")
    return None


def bridge_ports_problem(cfg: Config, ports: list[str]) -> str | None:
    """Its rules for a bridge's ports: not empty, a VLAN's MTU not under 1500, and not a VLAN
    with its own interface."""
    if not ports:
        return tr("Bridge cannot be empty.")
    vlans = [p for p in ports if "." in p]
    for p in vlans:
        n = _find(cfg, "netconf", devname=p)
        mtu = _int(cfg.get(f"netconf.{n}.mtu")) if n is not None else None
        if mtu is not None and mtu < int(DEFAULT_MTU):
            return tr("Wrong MTU value ({mtu}) for port {port}. Cannot be less than {min}.",
                      mtu=mtu, port=ui_devname(p), min=DEFAULT_MTU)
    if any(o in v for v in vlans for o in ports if "." not in o):
        return tr("Selected ports cannot contain combination of VLAN and its parent "
                  "interface.")
    return None


def next_bridge_name(bridges: list[dict]) -> str:
    """Its new bridge's name: br and one more than the highest number (br_100 counts 100)."""
    nums = [_int(b["devname"][2:].replace("_", "")) for b in bridges]
    nums = [n for n in nums if n is not None]
    return f"br{max(nums) + 1}" if nums else "br0"


def new_vlan(cfg: Config, parent: str, vid: str, comment: str = "") -> list[dict]:
    """What its web adds with a VLAN: the VLAN and, on an access point, for a wireless one, a
    bridge of its own (br_ID, "Automatically created for VLAN ID ID")."""
    n = js_int_like(str(vid).strip())              # (as its parseInt reads it: 0x10 is 16)
    vlan = {"parent": parent, "id": str(n), "devname": f"{parent}.{n}", "on": True,
            "comment": comment}
    extra = []
    if is_ap(cfg) and parent.startswith("ath"):
        extra.append({"devname": f"br_{vlan['id']}", "on": True, "stp": False,
                      "ports": [vlan["devname"]], "option82": False,
                      "comment": f"Automatically created for VLAN ID {vlan['id']}"})
    return [vlan] + extra


def _mtu_of(cfg: Config, devname: str) -> str:
    n = _find(cfg, "netconf", devname=devname)
    return (cfg.get(f"netconf.{n}.mtu") if n is not None else None) or DEFAULT_MTU


def plan_vlans_bridges(cfg: Config, vlans: list[dict], bridges: list[dict]) -> dict:
    """What its web saves with these VLANs and bridges (as vlan_list and bridge_list give
    them, in their order; None = delete)."""
    new = cfg.copy()
    old_v = {v["devname"] for v in vlan_list(cfg)}
    old_b = {b["devname"] for b in bridge_list(cfg)}
    now = {x["devname"]: x for x in vlans + bridges}
    gone = (old_v | old_b) - set(now)
    off = {d for d, x in now.items() if not x["on"]}
    for k in [k for k in new.keys() if k.split(".", 1)[0] in ("vlan", "bridge")]:
        new.delete(k)
    if vlans:
        new.set("vlan.status", "enabled")
    for i, v in enumerate(vlans, 1):
        _merge(new, {f"vlan.{i}.status": "enabled" if v["on"] else "disabled",
                     f"vlan.{i}.id": v["id"], f"vlan.{i}.devname": v["parent"],
                     f"vlan.{i}.comment": v["comment"] or None})
    if bridges:
        new.set("bridge.status", "enabled")
    for i, b in enumerate(bridges, 1):
        _merge(new, {f"bridge.{i}.status": "enabled" if b["on"] else "disabled",
                     f"bridge.{i}.devname": b["devname"],
                     f"bridge.{i}.stp.status": "enabled" if b["stp"] else "disabled",
                     f"bridge.{i}.comment": b["comment"] or None})
        for j, port in enumerate(b["ports"], 1):
            _merge(new, {f"bridge.{i}.port.{j}.status": "enabled",
                         f"bridge.{i}.port.{j}.devname": port})
    # netconf: one for each enabled VLAN or bridge (the ones that stay keep theirs)
    for n in _indices(new, "netconf"):
        if new.get(f"netconf.{n}.devname") in gone | off:
            for k in [k for k in new.keys() if k.startswith(f"netconf.{n}.")]:
                new.delete(k)
    for dev, x in now.items():
        if x["on"] and _find(new, "netconf", devname=dev) is None:
            n = max(_indices(new, "netconf"), default=0) + 1
            mtu = _mtu_of(cfg, x["parent"]) if "parent" in x else DEFAULT_MTU
            _merge(new, {f"netconf.{n}.status": "enabled", f"netconf.{n}.devname": dev,
                         f"netconf.{n}.autoip.status": "disabled", f"netconf.{n}.mtu": mtu})
    _renumber(new, "netconf")
    if net_mode(cfg) == "bridge":                  # its DHCP relay of option 82
        for k in [k for k in new.keys() if k.startswith(("dhcprelay.",
                                                         "ebtables.sys.dhcp_relay."))]:
            new.delete(k)
        for i, b in enumerate([b for b in bridges if b.get("option82")], 1):
            dev = b["devname"]
            _merge(new, {"dhcprelay.status": "enabled",
                         f"dhcprelay.server.{i}.status": "enabled",
                         f"dhcprelay.server.{i}.devname": dev,
                         f"dhcprelay.client.{i}.status": "enabled",
                         f"dhcprelay.client.{i}.devname": dev,
                         f"dhcprelay.client.{i}.agentid": "_mac_",
                         "ebtables.status": "enabled", "ebtables.sys.status": "enabled",
                         "ebtables.sys.dhcp_relay.status": "enabled",
                         f"ebtables.sys.dhcp_relay.{i}.status": "enabled",
                         f"ebtables.sys.dhcp_relay.{i}.devname": dev})
    def uses(rule: dict, devs) -> bool:             # (as its input or its output)
        return bool({rule.get("devname"), rule.get("devnameOutput")} & set(devs))
    for v6 in (False, True):                       # the rules of what went away or is off
        rules = firewall_rules(new, v6)
        kept = [dict(r, on=False) if uses(r, off) else r for r in rules if not uses(r, gone)]
        if kept != rules:
            _merge(new, plan_firewall(new, v6, firewall_on(new, v6), kept))
    for n in _indices(new, "tshaper"):
        dev = new.get(f"tshaper.{n}.devname")
        if dev in gone:
            for k in [k for k in new.keys() if k.startswith(f"tshaper.{n}.")]:
                new.delete(k)
        elif dev in off:
            new.set(f"tshaper.{n}.status", "disabled")
    _renumber(new, "tshaper")
    for k in [k for k in new.keys() if k.startswith(_BRIDGE_EBTABLES)]:
        new.delete(k)
    _merge(new, bridge_firewall(new, "v8"))
    return {k: after for k, _, after in cfg.diff(new)}


# -- the configuration mode ---------------------------------------------------------------

def cfg_mode_advanced(cfg: Config) -> bool:
    return cfg.get(CFG_MODE_KEY) == "enabled"


def simple_blockers(cfg: Config) -> list[str]:
    """Its isSimpleModeApplicable, said: what the simple mode cannot show (then its web
    builds the network again to go back to it); none: it can."""
    out = []
    if any(re.match(r"netconf\.\d+\.alias\.\d+\.ip$", k) and cfg.get(k) for k in cfg.keys()):
        out.append(tr("IP aliases"))
    if firewall_rules(cfg, False):
        out.append(tr("firewall rules"))
    if any(cfg.get(f"tshaper.{n}.devname") for n in _indices(cfg, "tshaper")):
        out.append(tr("traffic shaping rules"))
    if out:
        return out
    mode, vlans, bridges, hw = net_mode(cfg), vlan_list(cfg), bridge_list(cfg), hw_interfaces(cfg)
    if mode == "bridge":
        if len(bridges) == 1 and not vlans:
            ok = set(bridges[0]["ports"]) <= set(hw)
        elif len(bridges) == 2 and len(vlans) == len(hw):
            tagged = {f"{h}.{vlans[0]['id']}" for h in hw}
            ok = all(set(b["ports"]) <= (set(hw) if b["devname"] == "br0" else tagged)
                     for b in bridges)
        else:
            ok = False
    else:
        lans = [d for d, role in _roles(cfg).items() if role == "lan"]
        ok = len(bridges) <= 1 and not vlans and len(lans) == 1
    return [] if ok else [tr("VLANs and bridges")]


def plan_cfg_mode(advanced: bool) -> dict[str, str]:
    return {CFG_MODE_KEY: "enabled" if advanced else "disabled"}


# Its WAN's DMZ exceptions as confirmSwitchToSimple writes them (its WAN's save then
# writes them as dmz_mgmt_exceptions does).
_SIMPLE_DMZ_EXCEPT = ((1, "ICMP", "8"), (2, "UDP", "9"), (3, "TCP", "80"), (4, "TCP", "22"),
                      (5, "TCP", "443"), (6, "UDP", "10001"))


def _main_plan(cfg: Config, ifc: dict, new: Config, main: dict) -> dict:
    """The device's main address on the template's main interface, as this program's forms
    set it: static (with its gateway and DNS), DHCP (with its fallback) or PPPoE (its
    account)."""
    how = ip_mode(cfg, ifc["devname"])
    if how == "dhcp":
        return plan_dhcp(new, main, *dhcp_fallback(cfg, ifc["devname"]))
    if how == "pppoe":
        return plan_pppoe(new, main, pppoe_settings(cfg, ifc["devname"]))
    dns = dns_servers(cfg)
    return plan_static(new, main, ifc["ip"], ifc["netmask"], default_route(cfg)[1], dns[0],
                       dns[1])


def _router_extras(cfg: Config, ifc: dict, new: Config, main: dict) -> None:
    """In router and SOHO modes: its WAN's NAT, management blocking, DMZ and cloned MAC, and
    its LAN (address and DHCP server) on the template's (new, changed here)."""
    if nat_on(cfg, ifc):
        _merge(new, plan_nat(new, main, True))
    if mgmt_blocked(cfg, ifc):
        _merge(new, plan_block_mgmt(new, main, True))
    up: dict[str, str | None] = {}
    d = _find(cfg, "iptables.sys.dmz", devname=nat_devname(cfg, ifc))
    if d is not None and _v8_on(cfg, f"iptables.sys.dmz.{d}.status") \
            and _v8_on(cfg, "iptables.sys.dmz.status"):
        up.update({"iptables.status": "enabled", "iptables.sys.status": "enabled",
                   "iptables.sys.dmz.status": "enabled", "iptables.sys.dmz.1.status": "enabled",
                   "iptables.sys.dmz.1.host": cfg.get(f"iptables.sys.dmz.{d}.host") or "",
                   "iptables.sys.dmz.1.devname": nat_devname(new, main)})
    for n, proto, port in _SIMPLE_DMZ_EXCEPT:     # (as its web writes them)
        up.update({f"iptables.sys.dmz.1.except.{n}.proto": proto,
                   f"iptables.sys.dmz.1.except.{n}.port": port,
                   f"iptables.sys.dmz.1.except.{n}.status": "enabled"})
    up["iptables.sys.dmz.1.except.status"] = "enabled"
    n = ifc["n"]
    if _v8_on(cfg, f"netconf.{n}.hwaddr.status") and cfg.get(f"netconf.{n}.hwaddr.mac"):
        up.update({f"netconf.{main['n']}.hwaddr.status": "enabled",
                   f"netconf.{main['n']}.hwaddr.mac": cfg.get(f"netconf.{n}.hwaddr.mac")})
    _merge(new, up)
    olds, news = lan_ifaces(cfg), lan_ifaces(new)
    if olds and news:
        lan = news[0]
        old = next((x for x in olds if x["devname"] == lan["devname"]), olds[0])
        _merge(new, {f"netconf.{lan['n']}.ip": old["ip"],
                     f"netconf.{lan['n']}.netmask": old["netmask"]})
        _merge(new, plan_lan_dhcp(new, lan["devname"], lan_dhcp(cfg, old["devname"]),
                                  main["devname"]))
    _merge(new, dmz_mgmt_exceptions(new, "v8"))


def plan_simple(cfg: Config, board: dict) -> dict:
    """What going back to the simple mode saves when the configuration does not fit it (its
    web asks first): its mode's template again, which leaves out what the simple mode cannot
    show (its VLANs, other bridges, IP aliases, firewall and traffic shaping rules, DHCP
    reservations), with the device's own settings on the template's interfaces, set as this
    program's forms set them: its main address (static with its gateway and DNS, DHCP with
    its fallback, or PPPoE) and its automatic IP and, in router and SOHO modes, its NAT, its
    management blocking, its DMZ, its cloned MAC and its LAN (address and DHCP server). Its
    NAT helpers stay as they are (outside the network). None = delete.

    Its web keeps those only for a static address (and writes them on br0, which is the LAN
    of an access point); with DHCP it leaves the template's (in bridge mode the static
    192.168.1.20, which loses a device that gets its address over DHCP) and it resets the
    LAN to 192.168.1.1 without its DHCP server: this program does not. A second port: the
    board's, or the one its configuration has (eth1)."""
    mode = net_mode(cfg)
    new = cfg.copy()
    for k in cfg.keys():
        if _in_sections(k, NETMODE_SECTIONS["v8"]):
            new.delete(k)
    ports2 = two_ports(board) or "eth1" in hw_interfaces(cfg)
    tpl = netmode_template("v8", mode, is_ap(cfg), ports2)
    _merge(new, {k: tpl.get(k) for k in tpl.keys()})
    ifc, main = main_iface(cfg, "v8"), main_iface(new, "v8")
    if ifc and main:
        _merge(new, _main_plan(cfg, ifc, new, main))
        if ip_mode(new, main["devname"]) == "static" and not any(
                _v8_on(new, f"dhcpc.{n}.status") for n in _indices(new, "dhcpc")):
            new.set("dhcpc.status", "disabled")            # (as its web writes it)
        new.set(f"netconf.{main['n']}.autoip.status",
                "enabled" if ifc["autoip"] else "disabled")
        if mode != "bridge":
            _router_extras(cfg, ifc, new, main)
    new.set(CFG_MODE_KEY, "disabled")
    _merge(new, bridge_firewall(new, "v8"))
    return {k: after for k, _, after in cfg.diff(new)}
