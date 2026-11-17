"""The port forwarding and the firewall of the web's Network page (airOS 8), as its web reads
and saves them.

Port forwarding (router and SOHO modes): its rules are iptables.sys.portfw.N (N, their
position), each with the interface, the private IP and port, TCP or UDP, the source and the
public IP (with their masks) and the public port; iptables.sys.portfw.status turns them on.

The firewall (only with the advanced network configuration, in any mode): its rules are
ebtables (bridge mode) or iptables and ip6tables (router) command lines, (eb|ip|ip6)tables.N
.cmd, that its web reads and writes with its parseCmd and buildCmd: the input and output
interfaces, the protocol, the source and the destination (each with its mask and port, and
"!" for all but it) and ACCEPT or DROP. Their order counts: the first one that matches
decides. In bridge mode the IPv6 rules (chain FIREWALL6) go after the IPv4 ones in ebtables;
.sys.fw.status and .sys.fw6.status turn each family on."""
from __future__ import annotations

import re

from ..core.i18n import tr
from ..core.config import (Config, WEB_IPV4_RE, web_ipv6_ok, _indices, js_int_like, _merge,
                           port_kind, _v8_on)
from .network import HW_DEVNAME, ip_mode, main_iface, net_mode

_PORTS = (1, 65535)


# -- what its web's validators do (main.js: port, ipv4IPMask, ipv6IPMask) ----------------

def _ip_mask_ok(value, v6: bool = False) -> bool:
    """Its ipv4IPMask / ipv6IPMask: empty (any), an address, or an address/bits. Not with a
    space inside (10.0.0.0/ 8, which its parseInt takes): written as it is, it would break
    the rule's command line."""
    text = str(value).strip()
    if any(c.isspace() for c in text):
        return False
    if not text or (not v6 and text == "0.0.0.0/0"):
        return True
    if "/" in text:
        text, bits = text.split("/", 1)
        n = js_int_like(bits)
        if n is None or not 0 <= n <= (128 if v6 else 32):
            return False
    return web_ipv6_ok(text) if v6 else bool(WEB_IPV4_RE.fullmatch(text))


# -- interfaces, as its web names and offers them --------------------------------------

def ui_devname(devname: str) -> str:
    """How its web names an interface (devname2uidevname): eth0 → LAN0, ath0 → WLAN0, br0 →
    BRIDGE0, ppp+ → TUNNEL0."""
    name = devname.replace("ath", "WLAN", 1).replace("eth", "LAN", 1).replace("br", "BRIDGE", 1)
    return name.replace("ppp+", "TUNNEL0", 1).replace("ppp", "TUNNEL", 1)


def rule_interfaces(cfg: Config) -> list[str]:
    """The interfaces its web offers for a rule (getInterfacesForFirewallRules): the hardware
    ones, the enabled VLANs and bridges, but not the bridges in bridge mode nor, in router
    mode with ath0 in a bridge, the wireless one; and ppp+ (PPPoE) with a PPPoE WAN."""
    def on(key):
        return _v8_on(cfg, key)
    hw = [d for d in (cfg.get(f"netconf.{n}.devname") or "" for n in _indices(cfg, "netconf"))
          if HW_DEVNAME.fullmatch(d)]
    vlans = [f"{cfg.get(f'vlan.{n}.devname')}.{cfg.get(f'vlan.{n}.id')}"
             for n in _indices(cfg, "vlan") if on(f"vlan.{n}.status")
             and cfg.get(f"vlan.{n}.devname") and cfg.get(f"vlan.{n}.id")]
    bridges = [cfg.get(f"bridge.{n}.devname") or "" for n in _indices(cfg, "bridge")
               if cfg.get(f"bridge.{n}.devname") and on(f"bridge.{n}.status")]
    ports = [cfg.get(f"bridge.{b}.port.{p}.devname") for b in _indices(cfg, "bridge")
             for p in _indices(cfg, f"bridge.{b}.port")]
    mode = net_mode(cfg)
    skip = r"WLAN[0-9]*" if mode != "bridge" and "ath0" in ports else None
    out = [d for d in dict.fromkeys(hw + vlans + ([] if mode == "bridge" else bridges))
           if not (skip and re.fullmatch(skip, ui_devname(d)))]
    wan = main_iface(cfg, "v8") if mode != "bridge" else None
    if wan and ip_mode(cfg, wan["devname"]) == "pppoe":
        out.append("ppp+")
    return out


# -- port forwarding (router and SOHO modes) --------------------------------------------

PORTFW_PROTOS = ("tcp", "udp")
_PORTFW = "iptables.sys.portfw"
_PORTFW_KEYS = ("devname", "host", "port", "proto", "src", "dst", "dport", "comment")


def port_forwarding(cfg: Config) -> tuple[bool, list[dict]]:
    """Whether it is on and its rules, in their order (a missing source or public IP is its
    model's 0.0.0.0/0)."""
    rules = []
    for n in _indices(cfg, _PORTFW):
        rule = {k: cfg.get(f"{_PORTFW}.{n}.{k}") or "" for k in _PORTFW_KEYS}
        rule["src"] = rule["src"] or "0.0.0.0/0"
        rule["dst"] = rule["dst"] or "0.0.0.0/0"
        rule["on"] = _v8_on(cfg, f"{_PORTFW}.{n}.status")
        rules.append(rule)
    return _v8_on(cfg, f"{_PORTFW}.status"), rules


def new_port_forward(cfg: Config) -> dict:
    """A new rule as its dialog starts it: enabled, on the WAN, TCP, from and to anywhere."""
    wan = main_iface(cfg, "v8")
    return {"on": True, "devname": wan["devname"] if wan else "", "host": "", "port": "",
            "proto": "tcp", "src": "0.0.0.0/0", "dst": "0.0.0.0/0", "dport": "",
            "comment": ""}


def port_forward_problem(field: str, value) -> str | None:
    """What its web says of a field of a port forwarding rule, or None."""
    text = str(value).strip()
    if field == "host":
        if not text:
            return tr("Private IP is required")
        return None if WEB_IPV4_RE.fullmatch(text) else tr("Private IP is invalid")
    if field in ("src", "dst"):
        if _ip_mask_ok(text):
            return None
        return tr("Source IP/Mask is invalid") if field == "src" else tr(
            "Public IP/Mask is invalid")
    if field == "port":
        kind = port_kind(text)
        return None if kind is None else tr("Private Port is invalid") if kind == "invalid" \
            else tr("Private Port must be between {low} and {high}", low=_PORTS[0],
                    high=_PORTS[1])
    if field == "dport":
        if not text:
            return tr("Public Port is required")
        kind = port_kind(text)
        return None if kind is None else tr("Public Port is invalid") if kind == "invalid" \
            else tr("Public Port must be between {low} and {high}", low=_PORTS[0],
                    high=_PORTS[1])
    return None


def plan_port_forwarding(cfg: Config, on: bool, rules: list[dict]) -> dict[str, str | None]:
    """What its web saves (None = delete): the section on or off and the rules at positions
    1, 2, 3… in their order (its configuration leaves empty values out)."""
    new = cfg.copy()
    for k in [k for k in new.keys() if re.match(rf"{re.escape(_PORTFW)}\.\d+\.", k)]:
        new.delete(k)
    _merge(new, {"iptables.status": "enabled", "iptables.sys.status": "enabled",
                 f"{_PORTFW}.status": "enabled" if on else "disabled"})
    for i, rule in enumerate(rules, 1):
        new.set(f"{_PORTFW}.{i}.status", "enabled" if rule["on"] else "disabled")
        for k in _PORTFW_KEYS:
            if rule.get(k):
                new.set(f"{_PORTFW}.{i}.{k}", str(rule[k]))
    return {k: after for k, _, after in cfg.diff(new)}


# -- the firewall (advanced network configuration) -------------------------------------

FW_TARGETS = ("ACCEPT", "DROP")
# The "IP Type" of its dialog: its label and the value saved (IPv4, and IPv6).
FW_PROTOS = {False: (("IP", "ip"), ("ICMP", "1"), ("TCP", "6"), ("UDP", "17"), ("P2P", "p2p")),
             True: (("IP", "41"), ("ICMP", "58"), ("TCP", "6"), ("UDP", "17"))}
_FW_FIELDS = ("src", "sport", "dst", "dport")


def fw_table(cfg: Config, v6: bool) -> str:
    """Where its rules go: ebtables in bridge mode, iptables or ip6tables in router mode."""
    if net_mode(cfg) == "bridge":
        return "ebtables"
    return "ip6tables" if v6 else "iptables"


def firewall_on(cfg: Config, v6: bool) -> bool:
    return _v8_on(cfg, f"{fw_table(cfg, v6)}.sys.{'fw6' if v6 else 'fw'}.status")


def new_fw_rule(v6: bool) -> dict:
    """A rule as its model starts it (its dialog's selects take their first option: any
    interface, IP)."""
    return {"on": True, "target": "ACCEPT", "chain": "FIREWALL6" if v6 else "FIREWALL",
            "etherType": "0x86dd" if v6 else "0x0800", "devname": "", "devnameOutput": "",
            "ipProto": FW_PROTOS[v6][0][1], "src": "", "sport": "", "dst": "", "dport": "",
            "src_inv": False, "sport_inv": False, "dst_inv": False, "dport_inv": False,
            "comment": "", "cmd": ""}


# The options its parseCmd reads: the ones with an address or a port (which may be negated)
# and the ones with a plain value.
_FW_RANGE_OPTS = {"--src": "src", "--ip-src": "src", "--ip6-src": "src",
                  "--sport": "sport", "--ip-sport": "sport", "--ip6-sport": "sport",
                  "--dst": "dst", "--ip-dst": "dst", "--ip6-dst": "dst",
                  "--dport": "dport", "--ip-dport": "dport", "--ip6-dport": "dport"}
_FW_VALUE_OPTS = {"-t": "table", "-A": "chain", "-p": "etherType", "-i": "devname",
                  "-o": "devnameOutput", "--protocol": "ipProto", "--ip-protocol": "ipProto",
                  "--ip6-protocol": "ipProto", "--vlan-id": "vlan", "-j": "target"}


def parse_fw_cmd(cmd: str) -> dict:
    """Its parseCmd: the fields of a rule's command line (an ebtables or iptables one)."""
    out: dict = {}
    words = [w for w in str(cmd or "").split(" ") if w]
    bang, i = -2, 0
    names, simple = _FW_RANGE_OPTS, _FW_VALUE_OPTS
    while i < len(words) - 1:
        w = words[i]
        if w == "!":
            bang = i
        elif w in simple:
            i += 1
            out[simple[w]] = words[i]
        elif w == "-m":
            i += 1
            if words[i] == "ipp2p":
                out["ipProto"] = "p2p"
        elif w in names:
            if i - 1 == bang or (i + 1 < len(words) and words[i + 1] == "!"):
                if i - 1 != bang:
                    i += 1
                out[f"{names[w]}_inv"] = True
            i += 1
            out[names[w]] = words[i] if i < len(words) else ""
        if not out.get("etherType") and out.get("chain") == "FIREWALL6":
            out["etherType"] = "0x86dd"
        i += 1
    return out


def _fw_unknown_words(cmd: str) -> list[str]:
    """The words of a command line its parseCmd does not read (options it does not know and
    their values), or reads without their meaning: a "!" anywhere but before or after an
    address or a port (! -i br0, -i ! ath0, -p ! 0x0800), which it drops."""
    words = [w for w in str(cmd or "").split(" ") if w]
    unknown, i = [], 0
    while i < len(words):
        w, nxt = words[i], words[i + 1] if i + 1 < len(words) else None
        if w == "!" and nxt in _FW_RANGE_OPTS:           # (! --src …)
            i += 1
        elif w in _FW_RANGE_OPTS:                          # (--ip-src [!] …)
            i += 3 if nxt == "!" else 2
        elif w in _FW_VALUE_OPTS and nxt != "!":
            i += 2
        elif w == "-m" and nxt == "ipp2p":
            i += 3 if i + 2 < len(words) and words[i + 2] == "--ipp2p" else 2
        else:
            unknown.append(w)
            i += 1
    return unknown


def fw_cmd_lossless(rule: dict, table: str, v6: bool) -> bool:
    """Whether a rule's command line reads whole into its fields, so that writing it again
    from them, as its web does after an edit, keeps what it does: every word read (not a "!"
    its parseCmd drops), and the same read from the command written again, without what its
    model fills in (ebtables' -p 0x0800 where it had none: IPv4 only; ip6tables' --protocol
    41). A command written another way (iptables' own -p tcp, options its web does not know,
    such as a rate limit) would become another rule: a wider one, or even its opposite."""
    cmd = rule.get("cmd") or ""
    if not cmd:
        return True

    def read(line: str) -> dict:
        out = parse_fw_cmd(line)
        if table == "iptables" and out.get("ipProto") == "ip":
            del out["ipProto"]                 # (iptables' --protocol ip: any, as none)
        return out
    model = new_fw_rule(v6)
    model.update(parse_fw_cmd(cmd))
    return not _fw_unknown_words(cmd) and read(cmd) == read(build_fw_cmd(model, table))


def build_fw_cmd(rule: dict, table: str) -> str:
    """Its buildCmd: the command line of a rule for that table."""
    def neg(key):
        return "! " if rule.get(key) else ""
    out = []
    if rule.get("table"):
        out.append(f"-t {rule['table']}")
    out.append(f"-A {rule.get('chain') or 'FIREWALL'}")
    if rule.get("devname"):
        out.append(f"-i {rule['devname']}")
    if rule.get("devnameOutput"):
        out.append(f"-o {rule['devnameOutput']}")
    proto, ether = rule.get("ipProto") or "", rule.get("etherType") or ""
    if table == "ebtables":
        out.append(f"-p {ether}")
        pre = {"0x0800": "--ip-", "0x86dd": "--ip6-"}.get(ether)
        if ether == "0x8100" and rule.get("vlan"):
            out.append(f"--vlan-id {rule['vlan']}")
        elif pre:
            if proto and (ether == "0x86dd" or proto != "ip"):
                out.append(f"{pre}protocol {proto}")
            for f in _FW_FIELDS:
                if rule.get(f) and (proto or f in ("src", "dst")):
                    out.append(f"{pre}{f} {neg(f + '_inv')}{rule[f]}")
    else:
        if proto:
            out.append("-m ipp2p --ipp2p" if proto == "p2p" else f"--protocol {proto}")
        for f in _FW_FIELDS:
            if rule.get(f) and (proto or f in ("src", "dst")):
                out.append(f"{neg(f + '_inv')}--{f} {rule[f]}")
    out.append(f"-j {rule.get('target') or 'DROP'}")
    return " ".join(out)


def firewall_rules(cfg: Config, v6: bool) -> list[dict]:
    """Its rules of that family, in their order: each with the fields of its command line,
    whether it is enabled, its comment and the command line as it is."""
    table, out = fw_table(cfg, v6), []
    for n in _indices(cfg, table):
        cmd = cfg.get(f"{table}.{n}.cmd")
        if not cmd or ("FIREWALL6" in cmd) != v6:
            continue
        rule = new_fw_rule(v6)
        rule.update(parse_fw_cmd(cmd))
        rule.update({"on": _v8_on(cfg, f"{table}.{n}.status"),
                     "comment": cfg.get(f"{table}.{n}.comment") or "", "cmd": cmd})
        out.append(rule)
    return out


def fw_ports_apply(rule: dict) -> bool:
    """Ports only go with TCP and UDP (its dialog disables them for the rest)."""
    return str(rule.get("ipProto")) in ("6", "17")


def fw_rule_problem(field: str, value, v6: bool) -> str | None:
    """What its web says of a field of a firewall rule (the value without its "!"), or
    None."""
    text = str(value).strip()
    if field in ("src", "dst"):
        if _ip_mask_ok(text, v6):
            return None
        return tr("Source IP/Mask is invalid") if field == "src" else tr(
            "Destination IP/Mask is invalid")
    if field in ("sport", "dport"):
        kind = port_kind(text)
        if kind is None:
            return None
        if field == "sport":
            return tr("Source Port is invalid") if kind == "invalid" else tr(
                "Source Port must be between {low} and {high}", low=_PORTS[0], high=_PORTS[1])
        return tr("Destination Port is invalid") if kind == "invalid" else tr(
            "Destination Port must be between {low} and {high}", low=_PORTS[0], high=_PORTS[1])
    return None


def fw_rule_saved(rule: dict, v6: bool) -> dict:
    """The rule as its model leaves it when its dialog is accepted: an IPv4 rule that is not
    TCP or UDP loses its ports, and a source or destination 0.0.0.0/0 is "any"."""
    rule = dict(rule)
    if not v6:
        if not fw_ports_apply(rule):
            rule.update({"sport": "", "sport_inv": False, "dport": "", "dport_inv": False})
        for f in ("src", "dst"):
            if rule.get(f) == "0.0.0.0/0":
                rule[f] = ""
    return rule


def plan_firewall(cfg: Config, v6: bool, on: bool, rules: list[dict]) -> dict[str, str | None]:
    """What its web saves for that family (None = delete): the section on or off and its
    rules at positions 1, 2, 3… in their order; in bridge mode, in ebtables, the IPv4 rules
    first and the IPv6 ones after them. A rule keeps its command line unless it was edited
    (cmd emptied): then it is written again as its web builds it."""
    new, table = cfg.copy(), fw_table(cfg, v6)
    bridge = table == "ebtables"
    others = firewall_rules(cfg, not v6) if bridge else []
    for n in _indices(cfg, table):
        cmd = cfg.get(f"{table}.{n}.cmd")
        if cmd and (bridge or ("FIREWALL6" in cmd) == v6):
            for k in [k for k in new.keys() if k.startswith(f"{table}.{n}.")]:
                new.delete(k)
    _merge(new, {f"{table}.status": "enabled", f"{table}.sys.status": "enabled",
                 f"{table}.sys.{'fw6' if v6 else 'fw'}.status": "enabled" if on else "disabled"})
    ordered = (others + rules if v6 else rules + others) if bridge else rules
    for i, rule in enumerate(ordered, 1):
        new.set(f"{table}.{i}.status", "enabled" if rule["on"] else "disabled")
        new.set(f"{table}.{i}.cmd", rule.get("cmd") or build_fw_cmd(rule, table))
        if rule.get("comment"):
            new.set(f"{table}.{i}.comment", str(rule["comment"]))
    return {k: after for k, _, after in cfg.diff(new)}


def proto_name(value: str, v6: bool = False) -> str:
    """The protocol as its list shows it (getProtocolString: IP for the rest)."""
    return next((label for label, v in FW_PROTOS[v6] if v == str(value)), "IP")
