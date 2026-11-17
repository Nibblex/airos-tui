"""Tests of the router functions and the firewall of the Network page (airOS 8): port
forwarding, the firewall (IPv4 and IPv6, in bridge mode and in router mode) and the LANs' DHCP
(server, relay, DNS proxy, reservations and leases), as its web reads and saves them; and their
screens against the simulator. All on 127.0.0.1."""
import os
import re
import sys
import threading

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("router")
tr = T.tr
REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "real")
# The firewall rule of a real LiteBeam 5AC (8.7.25, bridge mode, advanced configuration), made
# with its web: exactly as it is in its configuration.
REAL_RULE = "-A FIREWALL -i ath0 -p 0x0800 --ip-protocol 17 --ip-sport 67 --ip-dport 68 -j DROP"
REAL_KEYS = {"ebtables.1.cmd": REAL_RULE, "ebtables.1.comment": "BLOCK DHCP FROM WLAN",
             "ebtables.1.status": "enabled", "ebtables.status": "enabled",
             "ebtables.sys.fw.status": "enabled", "ebtables.sys.fw6.status": "disabled",
             "ebtables.sys.status": "enabled", "iptables.status": "disabled",
             "iptables.sys.portfw.status": "disabled", "iptables.sys.status": "enabled",
             "gui.network.advanced.status": "enabled"}


def cfg_of(mode="bridge", station=False, **kw):
    d = S.Device(8, "ubnt", "ubnt", 1, 10, mode=mode, station=station)
    c = T.Config(S.cfg_text(d.running))
    for k, v in kw.items():
        c.set(k.replace("__", "."), v)
    return c


def applied(c, up):
    return T._merge(c.copy(), up)


def rule(v6=False, **kw):
    r = T.new_fw_rule(v6)
    r.update(kw)
    return r


# -- the firewall -----------------------------------------------------------------------

def firewall_unit():
    print("=== firewall: the command lines as its web reads and writes them ===")
    parsed = T.parse_fw_cmd(REAL_RULE)
    check("the real rule: its fields (parseCmd)", parsed == {
        "chain": "FIREWALL", "devname": "ath0", "etherType": "0x0800", "ipProto": "17",
        "sport": "67", "dport": "68", "target": "DROP"}, str(parsed))
    check("  and, written again (buildCmd for ebtables), it stays the same",
          T.build_fw_cmd(rule(**parsed), "ebtables") == REAL_RULE)
    same = []
    for cmd, table in (("-A FIREWALL -i eth0 --protocol 6 ! --src 10.0.0.0/8 --dport 22 -j DROP",
                        "iptables"),
                       ("-A FIREWALL -m ipp2p --ipp2p -j DROP", "iptables"),
                       ("-A FIREWALL -p 0x0800 --ip-src ! 192.168.1.0/24 -j ACCEPT", "ebtables"),
                       ("-A FIREWALL6 -i eth0 --protocol 58 -j ACCEPT", "ip6tables"),
                       ("-A FIREWALL6 -p 0x86dd --ip6-protocol 6 --ip6-dport 443 -j DROP",
                        "ebtables"),
                       ("-A FIREWALL -p 0x8100 --vlan-id 20 -j DROP", "ebtables")):
        r = rule("FIREWALL6" in cmd, **T.parse_fw_cmd(cmd))
        same.append(T.build_fw_cmd(r, table) == cmd)
    check("iptables, ip6tables and ebtables (negations before and after, P2P, VLAN), round "
          "trip", all(same), str(same))
    new = rule(target="DROP", devname="eth0", ipProto="6", src="10.0.0.0/8", src_inv=True,
               dport="22")
    check('a new rule: ebtables puts the "!" after the option, iptables before it', [
        T.build_fw_cmd(new, "ebtables"), T.build_fw_cmd(new, "iptables")] == [
        "-A FIREWALL -i eth0 -p 0x0800 --ip-protocol 6 --ip-src ! 10.0.0.0/8 --ip-dport 22 "
        "-j DROP",
        "-A FIREWALL -i eth0 --protocol 6 ! --src 10.0.0.0/8 --dport 22 -j DROP"])
    check('  (and with "IP" as its IP Type, the web writes --protocol ip in iptables, nothing in '
          "ebtables)", [T.build_fw_cmd(rule(), t) for t in ("iptables", "ebtables")] == [
              "-A FIREWALL --protocol ip -j ACCEPT", "-A FIREWALL -p 0x0800 -j ACCEPT"])
    check("the interface names, as its web shows them (devname2uidevname)", [
        T.ui_devname(d) for d in ("eth0", "ath0", "br0", "eth0.10", "ppp+", "eth1")] == [
        "LAN0", "WLAN0", "BRIDGE0", "LAN0.10", "TUNNEL0", "LAN1"])

    real = applied(cfg_of(), REAL_KEYS)
    rules = T.firewall_rules(real, False)
    check("the real configuration: the firewall enabled with its rule, no IPv6 rules",
          T.firewall_on(real, False) and not T.firewall_on(real, True) and len(rules) == 1
          and rules[0]["comment"] == "BLOCK DHCP FROM WLAN" and rules[0]["cmd"] == REAL_RULE
          and rules[0]["sport"] == "67" and T.firewall_rules(real, True) == [], str(rules))
    check("the interfaces it offers: in bridge mode, without the bridges",
          T.rule_interfaces(real) == ["eth0", "ath0"], str(T.rule_interfaces(real)))
    router = cfg_of("router")
    ppp = applied(router, {"dhcpc.1.status": "disabled", "ppp.status": "enabled",
                           "ppp.1.status": "enabled", "ppp.1.devname": "eth0"})
    check("  in router mode with ath0 in a bridge, not the wireless one; with PPPoE, also ppp+",
          T.rule_interfaces(router) == ["eth0", "br0"]
          and T.rule_interfaces(ppp)[-1] == "ppp+", f"{T.rule_interfaces(router)} "
                                                   f"{T.rule_interfaces(ppp)}")
    port = next(k for k in router.keys() if k.startswith("bridge.") and k.endswith(".devname")
                and ".port." in k and router.get(k) == "ath0")
    nowl = applied(router, {port: None})
    check("  (and if ath0 is not in a bridge, ath0 too)",
          T.rule_interfaces(nowl) == ["eth0", "ath0", "br0"], str(T.rule_interfaces(nowl)))

    print("=== firewall: its validation rules ===")
    got = [T.fw_rule_problem("src", v, False) for v in (
        "", "0.0.0.0/0", "10.0.0.0/8", "10.0.0.1", "10.0.0.0/33", "10.0.0", "10.0.0.0/8.5")]
    bad = tr("Source IP/Mask is invalid")
    check("IP/mask (IPv4): empty, an address or a network with 0 to 32 bits", got == [
        None, None, None, None, bad, bad, bad], str(got))
    got = [T.fw_rule_problem("dst", v, True) for v in ("fd00::/64", "::ffff:1.2.3.4", "fd00::/129",
                                                       "fe80::1%eth0", "1::2::3")]
    bad = tr("Destination IP/Mask is invalid")
    check("  (IPv6: from 0 to 128 bits)", got == [None, None, bad, bad, bad], str(got))
    rng = tr("Source Port must be between {low} and {high}", low=1, high=65535)
    inv = tr("Source Port is invalid")
    got = [T.fw_rule_problem("sport", v, False) for v in (
        "", "80", "0", "65536", "1000:2000", "2000:1000", "a:b", "x", "08")]
    check('ports: one from 1 to 65535 or a range a:b (as its validator, "08" is valid)', got == [
        None, None, rng, rng, None, inv, inv, rng, None], str(got))
    check('as JavaScript (parseInt and Number): "0x50" is valid as a port; "Infinity" and ":80" '
          "are not",
          [T.port_kind(v) for v in ("0x50", "Infinity", "1:Infinity", ":80")] == [
              None, "range", "invalid", "invalid"]
          and [T.js_parse_int(v) for v in ("0x50", " -0x1F", "0x", "12px", "x")] == [
              80, -31, None, 12, None])
    check("  (and the destination port's messages)", [
        T.fw_rule_problem("dport", v, False) for v in ("1:x", "0")] == [
        tr("Destination Port is invalid"),
        tr("Destination Port must be between {low} and {high}", low=1, high=65535)])
    check("buildCmd with its table (-t) and the output interface (-o)", T.build_fw_cmd(rule(
        table="nat", devnameOutput="eth0"), "iptables")
        == "-t nat -A FIREWALL -o eth0 --protocol ip -j ACCEPT")
    icmp = T.fw_rule_saved(rule(ipProto="1", sport="5", dport="6", src="0.0.0.0/0"), False)
    tcp6 = T.fw_rule_saved(rule(True, ipProto="58", sport="5"), True)
    check("accepting it: an IPv4 rule that is neither TCP nor UDP loses its ports, 0.0.0.0/0 is "
          '"any" (in IPv6 the web does not do it)', (icmp["sport"], icmp["dport"], icmp["src"])
          == ("", "", "") and tcp6["sport"] == "5")

    print("=== firewall: what it saves ===")
    v6rule = "-A FIREWALL6 -p 0x86dd --ip6-protocol 6 --ip6-dport 443 -j DROP"
    both = applied(real, {"ebtables.2.cmd": v6rule, "ebtables.2.status": "enabled"})
    add = T.plan_firewall(both, False, True, T.firewall_rules(both, False) + [
        rule(target="DROP", devname="eth0", ipProto="6", dport="22", comment="ssh")])
    after = applied(both, add)
    check("in bridge mode, a new IPv4 rule goes before the IPv6 ones (which move down)", [
        after.get(f"ebtables.{n}.cmd") for n in (1, 2, 3)] == [
        REAL_RULE, "-A FIREWALL -i eth0 -p 0x0800 --ip-protocol 6 --ip-dport 22 -j DROP",
        v6rule] and after.get("ebtables.2.comment") == "ssh"
        and after.get("ebtables.1.comment") == "BLOCK DHCP FROM WLAN", str(add))
    gone = applied(after, T.plan_firewall(after, False, True, T.firewall_rules(after, False)[1:]))
    check("deleting the first one: the others are numbered from 1", [
        gone.get(f"ebtables.{n}.cmd") for n in (1, 2, 3)] == [
        "-A FIREWALL -i eth0 -p 0x0800 --ip-protocol 6 --ip-dport 22 -j DROP", v6rule, None])
    off = T.plan_firewall(real, False, False, T.firewall_rules(real, False))
    check("turning it off: only its switch", off == {"ebtables.sys.fw.status": "disabled"},
          str(off))
    odd = applied(real, {"ebtables.1.cmd": "-A FIREWALL   -i ath0 -j DROP"})
    keep = T.plan_firewall(odd, False, True, [dict(r, comment="x") for r in
                                              T.firewall_rules(odd, False)])
    check("a rule whose comment alone changes keeps its line as it is",
          keep == {"ebtables.1.comment": "x"}, str(keep))
    rtr = applied(cfg_of("router"), {"gui.network.advanced.status": "enabled"})
    up4 = T.plan_firewall(rtr, False, True, [rule(target="DROP", ipProto="6", dport="23")])
    up6 = T.plan_firewall(applied(rtr, up4), True, True, [rule(True, target="DROP")])
    check("in router mode: iptables for IPv4 and ip6tables for IPv6, each with its own "
          "switch", up4.get("iptables.1.cmd") == "-A FIREWALL --protocol 6 --dport 23 -j DROP"
          and up4.get("iptables.sys.fw.status") == "enabled"
          and up6.get("ip6tables.1.cmd") == "-A FIREWALL6 --protocol 41 -j DROP"
          and up6.get("ip6tables.sys.fw6.status") == "enabled"
          and up6.get("ip6tables.status") == "enabled", f"{up4} {up6}")


# -- port forwarding ----------------------------------------------------------------------

def portfw_unit():
    print("=== port forwarding (router mode) ===")
    c = cfg_of("router")
    on, rules = T.port_forwarding(c)
    new = T.new_port_forward(c)
    check("no rules and off; a new one, as its dialog: enabled, on the WAN, TCP, from and "
          "to anywhere", not on and rules == [] and new == {
              "on": True, "devname": "eth0", "host": "", "port": "", "proto": "tcp",
              "src": "0.0.0.0/0", "dst": "0.0.0.0/0", "dport": "", "comment": ""}, str(new))
    got = {f: T.port_forward_problem(f, v) for f, v in (
        ("host", ""), ("port", "0"), ("src", "1.2.3.4/40"), ("dst", "x"), ("dport", ""))}
    check("its messages", got == {
        "host": tr("Private IP is required"),
        "port": tr("Private Port must be between {low} and {high}", low=1, high=65535),
        "src": tr("Source IP/Mask is invalid"), "dst": tr("Public IP/Mask is invalid"),
        "dport": tr("Public Port is required")}, str(got))
    check("  (a reversed range is not valid; an empty private port is)", [
        T.port_forward_problem("dport", "90:80"), T.port_forward_problem("port", ""),
        T.port_forward_problem("dport", "70000"), T.port_forward_problem("other", "x")] == [
        tr("Public Port is invalid"), None,
        tr("Public Port must be between {low} and {high}", low=1, high=65535), None])
    r1 = dict(new, host="192.168.1.10", port="80", dport="8080", comment="web")
    r2 = dict(new, host="192.168.1.11", dport="2222", proto="udp", on=False)
    up = T.plan_port_forwarding(c, True, [r1, r2])
    after = applied(c, up)
    check("what it saves: the switch and each rule at its position (empty values left out)",
          up.get("iptables.sys.portfw.status") == "enabled"
          and up.get("iptables.sys.portfw.1.comment") == "web"
          and up.get("iptables.sys.portfw.2.status") == "disabled"
          and "iptables.sys.portfw.2.port" not in up
          and T.port_forwarding(after) == (True, [r1, r2]), str(up))
    sim = S.Device(8, "ubnt", "ubnt", 1, 10, mode="router")
    sim.ui_lang = "es"
    check("the simulator, with it off and in Spanish, gives the notice of a real 8.7.25",
          sim.portfw_listing() == open(os.path.join(
              REAL, "airos8.7.25-port-forward-off-es.txt"), encoding="utf-8").read())
    gone = applied(after, T.plan_port_forwarding(after, True, [r2]))
    check("  and deleting the first one, the other moves to position 1",
          T.port_forwarding(gone)[1] == [r2] and not [
              k for k in gone.keys() if k.startswith("iptables.sys.portfw.2.")])


# -- the LAN's DHCP -----------------------------------------------------------------------

def dhcp_unit():
    print("=== LAN DHCP: what its web reads and saves ===")
    check("the range it proposes when enabling it (calculateIpRange: at most a /24, without the "
          "device's IP)", [T.web_dhcp_range(*a) for a in (
              ("192.168.1.1", "255.255.255.0"), ("10.0.0.1", "255.255.0.0"),
              ("192.168.1.254", "255.255.255.0"), ("192.168.1.50", "255.255.255.128"))] == [
        ("192.168.1.2", "192.168.1.254", "255.255.255.0"),
        ("10.0.0.2", "10.0.0.254", "255.255.255.0"),
        ("192.168.1.1", "192.168.1.253", "255.255.255.0"),
        ("192.168.1.51", "192.168.1.126", "255.255.255.128")])
    c = cfg_of("router")
    ds = T.lan_dhcp(c, "br0")
    check("the router template: server enabled with the DNS proxy (dnsproxy)", ds == {
        "mode": "enabled", "start": "192.168.1.2", "end": "192.168.1.254",
        "netmask": "255.255.255.0", "lease": "600", "dns1": "", "dns2": "", "proxy": True,
        "relay_ip": "", "agent_id": ""}, str(ds))
    check("  and saving it unchanged writes the proxy's dnsmasq entry, as its web does",
          T.plan_lan_dhcp(c, "br0", ds, "eth0") == {
              "dnsmasq.status": "enabled", "dnsmasq.1.status": "enabled",
              "dnsmasq.1.devname": "br0"})
    off = applied(c, T.plan_lan_dhcp(c, "br0", dict(ds, mode="disabled", proxy=False), "eth0"))
    check('disabling it: neither its entry nor the section (no "disabled" left)', not [
        k for k in off.keys() if k.startswith("dhcpd")] and T.lan_dhcp(off, "br0")["mode"]
        == "disabled")
    relay = dict(ds, mode="relay", relay_ip="10.0.0.9", agent_id="ag1", proxy=False)
    rel = applied(c, T.plan_lan_dhcp(c, "br0", relay, "eth0"))
    check("relay: the server (towards the WAN) and the client with its Agent ID", {
        k: rel.get(k) for k in ("dhcprelay.status", "dhcprelay.server.1.ip",
                                "dhcprelay.server.1.devname", "dhcprelay.client.1.devname",
                                "dhcprelay.client.1.agentid")} == {
        "dhcprelay.status": "enabled", "dhcprelay.server.1.ip": "10.0.0.9",
        "dhcprelay.server.1.devname": "eth0", "dhcprelay.client.1.devname": "br0",
        "dhcprelay.client.1.agentid": "ag1"} and T.lan_dhcp(rel, "br0") == dict(
        relay, start="192.168.1.2", end="192.168.1.254", netmask="255.255.255.0", lease="600"))
    back = applied(rel, T.plan_lan_dhcp(rel, "br0", dict(ds, proxy=False, dns1="1.1.1.1"),
                                        "eth0"))
    check("  and back to enabled: no relay, with the DNS it hands out and no proxy", not [
        k for k in back.keys() if k.startswith("dhcprelay")]
        and back.get("dhcpd.1.dns.1.server") == "1.1.1.1"
        and back.get("dhcpd.1.dnsproxy") == "disabled" and "dnsmasq.status" not in back)
    two = applied(c, {"netconf.4.devname": "eth1", "netconf.4.status": "enabled",
                      "netconf.4.role": "lan", "netconf.4.ip": "192.168.2.1",
                      "netconf.4.netmask": "255.255.255.0", "dhcpd.2.status": "enabled",
                      "dhcpd.2.devname": "eth1", "dhcpd.2.start": "192.168.2.2",
                      "dhcpd.2.end": "192.168.2.9"})
    renum = applied(two, T.plan_lan_dhcp(two, "br0", dict(ds, mode="disabled"), "eth0"))
    check("with two LANs, removing the first one numbers the other as dhcpd.1",
          renum.get("dhcpd.1.devname") == "eth1" and "dhcpd.2.devname" not in renum
          and renum.get("dhcpd.status") == "enabled")

    print("=== DHCP: its validation rules ===")
    base = dict(ds, start="192.168.1.50", proxy=False)
    got = {f: T.dhcp_problem(f, v, base) for f, v in (
        ("start", ""), ("start", "1.2.3"), ("end", "192.168.1.10"), ("end", "192.168.2.60"),
        ("netmask", "255.0.255.0"), ("lease", "100"), ("lease", "172800"), ("dns1", ""),
        ("dns2", ""), ("dns2", "1.2.3"), ("relay_ip", "0.0.0.0"), ("relay_ip", ""),
        ("agent_id", " "))}
    want = {"start": tr("Range Start is invalid"), "end": tr("Inconsistent DHCP Range"),
            "netmask": tr("Netmask is invalid"), "lease": None, "dns1": tr(
                "Primary DNS is required"), "dns2": tr("Secondary DNS is invalid"),
            "relay_ip": None, "agent_id": tr("Agent ID is required")}
    check("its messages (the range end, before the start or in another network: inconsistent)",
          got == want, str(got))
    got = [T.dhcp_problem(f, v, base) for f, v in (
        ("end", ""), ("end", "x"), ("netmask", ""), ("netmask", "1.2.3"), ("lease", ""),
        ("other", "x"))]
    check("  (and those of empty or mistyped fields)", got == [
        tr("Range End is required"), tr("Range End is invalid"), tr("Netmask is required"),
        tr("Netmask is invalid"), tr("Lease Time is required"), None], str(got))
    check("  (no range to propose: an IP or a netmask that is not one, or a /32)", [
        T.web_dhcp_range(*a) for a in (("x", "255.255.255.0"),
                                       ("192.168.1.1", "255.255.255.255"))] == [None, None])
    dns2 = applied(c, T.plan_lan_dhcp(c, "br0", dict(ds, dns2="9.9.9.9"), "eth0"))
    check("emptying the secondary DNS deletes it (its configuration leaves out empty values)",
          T.plan_lan_dhcp(dns2, "br0", dict(ds, dns2=""), "eth0")
          == {"dhcpd.1.dns.2.server": None})
    check("  (the lease time, from 120 to 172800; with the DNS proxy it does not ask for DNS)", [
        T.dhcp_problem("lease", "100", base), T.dhcp_problem("dns1", "", ds)] == [
        tr("Lease Time must be between {low} and {high}", low=120, high=172800), None])

    print("=== DHCP reservations and leases ===")
    res = [{"devname": "br0", "on": True, "mac": "00:11:22:33:44:55", "ip": "192.168.1.70",
            "comment": "camera"},
           {"devname": "br0", "on": False, "mac": "00:11:22:33:44:66", "ip": "192.168.1.71",
            "comment": ""}]
    with_res = applied(c, T.plan_reservations(c, res))
    check("the reservations go inside their LAN's server, numbered",
          with_res.get("dhcpd.1.dhcpreserv.1.mac") == "00:11:22:33:44:55"
          and with_res.get("dhcpd.1.dhcpreserv.2.status") == "disabled"
          and T.dhcp_reservations(with_res) == res and T.dhcp_lans(with_res) == ["br0"])
    lost = applied(with_res, T.plan_lan_dhcp(with_res, "br0", dict(ds, mode="disabled"), "eth0"))
    check("  and they are lost if their server is disabled (as in the web)",
          T.dhcp_reservations(lost) == [] and T.dhcp_lans(lost) == [])
    check("  and those of a disabled server, or with the section off, do not count",
          T.dhcp_reservations(applied(with_res, {"dhcpd.1.status": "disabled"})) == []
          and T.dhcp_reservations(applied(with_res, {"dhcpd.status": "disabled"})) == [])
    again = applied(with_res, T.plan_reservations(with_res, [res[1], dict(res[0],
                                                                          devname="eth9")]))
    check("saving them again: numbered from 1, and the one of a LAN without a server is lost",
          T.dhcp_reservations(again) == [res[1]] and "dhcpd.1.dhcpreserv.2.mac" not in again)
    check("its rules: MAC and IP required and valid", [
        T.reservation_problem("mac", ""), T.reservation_problem("mac", "00:11:22:33:44"),
        T.reservation_problem("ip", "1.2.3.400"), T.reservation_problem("mac", "aa:BB:cc:00:11:22"),
        T.reservation_problem("ip", ""), T.reservation_problem("comment", "x")]
        == [tr("MAC Address is required"), tr("MAC Address is invalid"),
            tr("IP Address is invalid"), None, tr("IP Address is required"), None])
    check("the time left, as its web shows it (getElapsedTime)", [
        T.elapsed_time(v) for v in (59, 3600, 86400, 90061, 200000)] == [
        "00:00:59", "01:00:00", f"1 {tr('day')} 00:00:00", f"1 {tr('day')} 01:01:01",
        f"2 {tr('days')} 07:33:20"])
    v4, v6 = T.parse_leases({"aaData": [["00:11:22:33:44:55", "192.168.1.100", 3550, "pc"],
                                        ["aa:bb:cc:dd:ee:ff", "192.168.1.101", "x", None]],
                             "aaData6": [["0001", "fd00::5", 100]]})
    real = T.parse_leases(__import__("json").load(open(os.path.join(
        REAL, "airos8.7.25-dhcp-leases-off.json"))))
    check("the leases: IPv4 and IPv6 (and the real answer without a server: none)",
          v4[0] == {"mac": "00:11:22:33:44:55", "ip": "192.168.1.100", "left": "00:59:10",
                    "hostname": "pc"} and v4[1]["left"] == "x" and v4[1]["hostname"] == ""
          and v6 == [{"id": "0001", "ip": "fd00::5", "left": "100"}] and real == ([], [])
          and T.parse_leases("x") == ([], []))


# -- the screens against the simulator ------------------------------------------------------

def start(port, mode="router", advanced=True, extra=None, version=8):
    srv = S.make_server(version, port, restart=0.3, test_secs=60, mode=mode)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    for c in (d.running, d.saved, d.system):
        if advanced:
            c["gui.network.advanced.status"] = "enabled"
        c.update(extra or {})
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=1)
    app.load()
    return srv, d, dev, app.ui, app


def run(app, method, script, *args):
    app.ui.script = list(script)
    app.ui.log.clear()
    app.ui.menus.clear()
    try:
        getattr(app, method)(*args)
    except AssertionError as e:
        print("     script:", e)
        return False
    return not app.ui.script


def rows(ui, title):
    return dict([m for m in ui.menus if m[0] == title][-1][1])


def msgs(ui, title=None):
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def keep(app):
    """Apply with test mode, then "Keep"."""
    app.ui.log.clear()
    app.ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    return tr("Done") in app.ui.titles()


def router_flows(port):
    print("=== screens: router mode with the advanced configuration ===")
    srv, d, dev, ui, app = start(port)
    ok = run(app, "network_menu", [("menu", None)])
    net = rows(ui, tr("Network"))
    check('in "Network": port forwarding, DHCP reservations and both firewalls', ok and {
        k: net.get(k) for k in ("r", "d", "f", "f6")} == {
        "r": tr("Port forwarding: disabled, {count} rule(s)", count=0),
        "d": tr("DHCP reservations: {count}", count=0),
        "f": tr("Firewall: disabled, {count} rule(s)", count=0),
        "f6": tr("IPv6 firewall: disabled, {count} rule(s)", count=0)}, str(net))

    ok = run(app, "port_forward_menu", [
        ("menu", "a"), ("menu", "h"), ("input", "192.168.1.300"), ("input", "192.168.1.10"),
        ("menu", "p"), ("input", "80"), ("menu", "q"), ("input", ""), ("input", "8080"),
        ("menu", "c"), ("input", "web"), ("menu", "ok"), ("menu", "o"), ("menu", None)])
    check("port forwarding: adding a rule (with the web's messages) and enabling it", ok
          and msgs(ui, tr("Invalid value")) == [tr("Private IP is invalid"),
                                                tr("Public Port is required")]
          and T.port_forwarding(app.working) == (True, [dict(
              T.new_port_forward(app.working), host="192.168.1.10", port="80",
              dport="8080", comment="web")]), str(msgs(ui)))
    ok = run(app, "firewall_menu", [
        ("menu", "a"), ("menu", "t"), ("menu", "2"), ("menu", "p"), ("menu", "3"),
        ("menu", "2"), ("input", "!22"), ("menu", "s"), ("input", "10.0.0.0/33"),
        ("input", "! 10.0.0.0/8"), ("menu", "ok"), ("menu", "a"), ("menu", "c"),
        ("input", "second"), ("menu", "ok"), ("menu", "2"), ("menu", "u"), ("menu", "o"),
        ("menu", None)], False)
    fw = T.firewall_rules(app.working, False)
    check("firewall: two rules, the second one moved up to first place, and enabled", ok
          and [r["comment"] for r in fw] == ["second", ""] and T.firewall_on(app.working, False)
          and app.working.get("iptables.2.cmd")
          == "-A FIREWALL --protocol 6 ! --src 10.0.0.0/8 ! --dport 22 -j DROP"
          and msgs(ui, tr("Invalid value")) == [tr("Source IP/Mask is invalid")],
          str([r.get("cmd") for r in fw]) + str(msgs(ui)))
    menu = rows(ui, tr("Firewall"))
    check("  (and its list, as the web's columns)", menu.get("2") == "* " + (
        "#2 DROP  " + tr("ANY") + " → " + tr("ANY") + "  TCP  ! 10.0.0.0/8 → 0.0.0.0/0 :! 22"),
        str(menu))
    check('apply and "Keep": saved on the device', keep(app)
          and d.saved.get("iptables.sys.portfw.1.dport") == "8080"
          and d.saved.get("iptables.1.comment") == "second"
          and d.saved.get("iptables.sys.fw.status") == "enabled", str(app.ui.titles()))
    ok = run(app, "port_forward_menu", [("menu", "v"), ("menu", None)])
    shown = [t for k, ti, t in ui.log if k == "textbox"]
    check('now the device uses them: "Rules in use (on the device)" shows them', ok and shown
          and "dpt:8080 to:192.168.1.10:80" in shown[0], str(shown))
    ok = run(app, "firewall_menu", [("menu", "1"), ("menu", "x"), ("yesno", 0), ("menu", None)],
             False)
    check("deleting a firewall rule", ok and [r["comment"] for r in T.firewall_rules(
        app.working, False)] == [""])
    status = app.status
    app.status = dict(status, firewall={"iptables": True, "ebtables": False, "ip6tables": True,
                                        "eb6tables": False})
    tags = []
    for v6 in (False, True):
        run(app, "firewall_menu", [("menu", None)], v6)
        tags.append([t for t, _ in ui.menus[-1][1]])
    app.status = status
    check('"Rules in use (on the device)" in the IPv4 firewall only (the device\'s listing is its '
          "IPv4 chain's)", "v" in tags[0] and "v" not in tags[1], str(tags))
    check("an address or a port with a space inside: invalid, as a wrong one (written as it is, "
          "the rule's command would break)",
          T.port_forward_problem("src", "10.0.0.0/ 8") == tr("Source IP/Mask is invalid")
          and T.fw_rule_problem("dport", "1000 : 2000", False) == tr("Destination Port is "
                                                                       "invalid")
          and T.port_forward_problem("src", " 10.0.0.0/8 ") is None
          and T.fw_rule_problem("dport", "1000:2000", False) is None)
    app.working = app.original.copy()

    print("=== screens: the LAN and its DHCP ===")
    lan = T.lan_ifaces(app.working)[0]
    ok = run(app, "lan_menu", [("menu", "c"), ("menu", None)], lan)
    shown = [t for k, ti, t in ui.log if k == "textbox"]
    check("the leases it gave (the device's, with their time left)", ok and shown
          and "laptop-north" in shown[0] and f"1 {tr('day')} 01:01:01" in shown[0], str(shown))
    ok = run(app, "lan_menu", [
        ("menu", "d"), ("menu", "r"), ("input", "192.168.1.50"), ("input", "192.168.1.20"),
        ("input", "192.168.1.60"), ("menu", "t"), ("input", "60"), ("input", "3600"),
        ("menu", None), ("menu", "p"), ("yesno", 0), ("input", ""), ("input", "1.1.1.1"),
        ("input", ""), ("menu", None)], lan)
    ds = T.lan_dhcp(app.working, "br0")
    check("range, lease and removing the DNS proxy (it asks for the DNS to hand out)", ok and (
        ds["start"], ds["end"], ds["lease"], ds["proxy"], ds["dns1"]) == (
        "192.168.1.50", "192.168.1.60", "3600", False, "1.1.1.1")
        and msgs(ui, tr("Invalid value")) == [tr("Inconsistent DHCP Range"), tr(
            "Lease Time must be between {low} and {high}", low=120, high=172800),
            tr("Primary DNS is required")], str(msgs(ui)))
    ok = run(app, "reservations_menu", [
        ("menu", "a"), ("menu", "m"), ("input", "00:11:22:33:44:55"), ("menu", "p"),
        ("input", "192.168.1.70"), ("menu", "ok"), ("menu", None)])
    check("a reservation on the LAN with DHCP", ok and T.dhcp_reservations(app.working) == [{
        "devname": "br0", "on": True, "mac": "00:11:22:33:44:55", "ip": "192.168.1.70",
        "comment": ""}])
    kept = app.working.copy()
    ok = run(app, "dhcp_menu", [("menu", "m"), ("menu", "3"), ("yesno", 1), ("menu", None)], lan)
    asked = [t for k, _, t in ui.log if k == "yesno"]
    check("relay with reservations: asked first, as when it is disabled (they are lost); "
          "\"No\" leaves it as it was", ok and app.working.diff(kept) == [] and asked
          and tr("Its {count} reservation(s) will be lost too, as in its web.", count=1)
          in asked[0], str(asked))
    ok = run(app, "dhcp_menu", [("menu", "m"), ("menu", "3"), ("yesno", 0), ("input", "0.0.0.0"),
                                ("input", "10.0.0.9"), ("input", ""), ("input", "opt82"),
                                ("menu", None)], lan)
    check("relay: asks for its server and Agent ID; the reservation is lost", ok and T.lan_dhcp(
        app.working, "br0")["mode"] == "relay" and T.dhcp_reservations(app.working) == []
        and msgs(ui, tr("Invalid value")) == [tr("DHCP Server IP is invalid"),
                                              tr("Agent ID is required")], str(msgs(ui)))
    ok = run(app, "dhcp_menu", [("menu", "m"), ("menu", "2"), ("menu", None)], lan)
    ds = T.lan_dhcp(app.working, "br0")
    check("and enabled again: the range the web proposes, with the lease and DNS it had in this "
          "session (as its page does, which keeps the model)", ok
          and (ds["mode"], ds["start"], ds["end"], ds["lease"], ds["dns1"]) == (
              "enabled", "192.168.1.2", "192.168.1.254", "3600", "1.1.1.1"), str(ds))
    app._accept(T.plan_lan(app.working, T.lan_ifaces(app.working)[0], "10.0.0.1",
                           "255.255.255.252", False))
    ok = run(app, "dhcp_menu", [("menu", "m"), ("menu", "2"), ("menu", None)],
             T.lan_ifaces(app.working)[0])
    ds = T.lan_dhcp(app.working, "br0")
    check("  on a /30 LAN, where its web proposes no range: its one free address, valid", ok
          and (ds["mode"], ds["start"], ds["end"]) == ("enabled", "10.0.0.2", "10.0.0.2")
          and not [p for p in T.net_problems(app.working) if p.level == "error"], str(ds))
    app.working = app.original.copy()
    ok = run(app, "lan_menu", [("menu", "1"), ("input", "10.20.30.1"), ("input", "24"),
                               ("yesno", 0), ("menu", None)], lan)
    ds = T.lan_dhcp(app.working, "br0")
    check("another IP for the LAN: it offers to move the range to the new network", ok
          and app.working.get("netconf.3.ip") == "10.20.30.1"
          and (ds["start"], ds["end"]) == ("10.20.30.2", "10.20.30.254"), str(ds))
    srv.shutdown()

    print("=== screens: what each mode shows ===")
    srv, d, dev, ui, app = start(port + 1, advanced=False)
    ok = run(app, "network_menu", [("menu", None)])
    net = rows(ui, tr("Network"))
    check("router with the simple configuration: only port forwarding", ok
          and "r" in net and not {"d", "f", "f6"} & set(net), str(sorted(net)))
    srv.shutdown()
    srv, d, dev, ui, app = start(port + 2, mode="bridge", extra=REAL_KEYS)
    d.ui_lang = "es"
    ok = run(app, "network_menu", [("menu", None)])
    net = rows(ui, tr("Network"))
    check("advanced bridge mode (as the real antenna): the firewalls, no port forwarding", ok
          and net.get("f") == tr("Firewall: enabled, {count} rule(s)", count=1)
          and not {"r", "d"} & set(net), str(net))
    ok = run(app, "firewall_menu", [("menu", "1"), ("menu", "e"), ("menu", "c"),
                                    ("input", "no DHCP from the WLAN"), ("menu", "ok"),
                                    ("menu", "v"), ("menu", None)], False)
    shown = [t for k, ti, t in ui.log if k == "textbox"]
    real = open(os.path.join(REAL, "airos8.7.25-firewall-user-rules.txt")).read()
    check("its real rule: the list, changing the comment keeps the line, and what it has in use "
          "matches the real listing", ok and rows(ui, tr("Firewall")).get("1", "").endswith(
              "#1 DROP  WLAN0 → " + tr("ANY") + "  UDP  0.0.0.0/0 :67 → 0.0.0.0/0 :68  "
              "(no DHCP from the WLAN)") and app.changes() == [
              ("ebtables.1.comment", "BLOCK DHCP FROM WLAN", "no DHCP from the WLAN")]
          and shown == [real.strip()], str(app.changes()) + str(shown))
    srv.shutdown()
    srv, d, dev, ui, app = start(port + 3, version=6, advanced=False)
    ok = run(app, "network_menu", [("menu", None)])
    check("airOS 6: none of this (it is another web)", ok and not {"r", "d", "f", "f6"} & set(
        rows(ui, tr("Network"))))
    srv.shutdown()


def real_listings():
    print("=== what a real NanoStation 5AC (8.7.14) lists in use, and the simulator alike ===")
    base = T.Config(open(os.path.join(REAL, "airos8.7.14-nanostation5ac-sta.cfg")).read())
    board = {"board.netmodes": "3", "board.phycount": "1", "feature.g3": "0"}

    def real(name):          # (its counters, as the simulator's: 0)
        text = open(os.path.join(REAL, name), encoding="utf-8").read()
        text = re.sub(r"pcnt = \d+ -- bcnt = \d+", "pcnt = 0 -- bcnt = 0", text)
        return re.sub(r"(?m)^ *\d+ +\d+ ", "    0     0 ", text)

    def device(cfg):
        d = S.Device(8, "ubnt", "ubnt", 1, 10)
        d.running = {k: cfg.get(k) for k in cfg.keys()}
        return d

    def router():
        c = T.switch_netmode(base, "v8", "router", board)
        c.set("gui.network.advanced.status", "enabled")
        return c, T.main_iface(c, "v8")["devname"], T.new_port_forward(c)
    r1, wan, pf = router()
    T._merge(r1, T.plan_port_forwarding(r1, True, [
        dict(pf, host="192.168.1.100", port="22", dport="2222"),
        dict(pf, host="192.168.1.100", proto="udp", dport="5000:5010", src="10.0.0.0/8")]))
    T._merge(r1, T.plan_firewall(r1, False, True, [
        rule(target="DROP", devname=wan, ipProto="6", dport="23"),
        rule(target="ACCEPT", ipProto="17", src="192.168.1.0/24", src_inv=True, dport="53",
             dport_inv=True),
        rule(target="DROP", ipProto="p2p")]))
    d = device(r1)
    check("router mode: the firewall in use, with the iptables columns and without the P2P rule "
          "(the device keeps it but does not apply it)",
          d.firewall_listing() == real("airos8.7.14-router-user-rules-1.txt"))
    check("  and port forwarding in use: a DNAT per rule and an SNAT for the one open to any "
          "source", d.portfw_listing() == real("airos8.7.14-router-port-forward-1.txt"))
    r2, wan, pf = router()
    T._merge(r2, T.plan_port_forwarding(r2, True, [
        dict(pf, host="192.168.1.100", dport="8080"),
        dict(pf, host="192.168.1.100", port="53", proto="udp", dport="5353"),
        dict(pf, host="192.168.1.100", port="80", dport="8081", src="10.0.0.0/8"),
        dict(pf, host="192.168.1.100", port="443", dport="8443", dst="192.168.10.1")]))
    T._merge(r2, T.plan_firewall(r2, False, True, [
        rule(target="DROP", ipProto="1", src="10.1.2.3"), rule(target="ACCEPT"),
        rule(target="DROP", devnameOutput=wan, ipProto="6", sport="1024:65535", sport_inv=True,
             dst="8.8.8.8/32", dport="25")]))
    d = device(r2)
    check('  (ICMP, "IP" as all, an IP without /32, negated source port; and the SNAT, with or '
          "without a private port, only on the ones open to any source)",
          d.firewall_listing() == real("airos8.7.14-router-user-rules-2.txt")
          and d.portfw_listing() == real("airos8.7.14-router-port-forward-2.txt"))
    b = base.copy()
    b.set("gui.network.advanced.status", "enabled")
    T._merge(b, T.plan_firewall(b, False, True, [
        rule(target="DROP", devname="ath0", ipProto="17", sport="67", dport="68"),
        rule(target="ACCEPT", ipProto="6", src="192.168.1.0/24", src_inv=True,
             dport="1000:2000"),
        rule(target="DROP", devnameOutput="eth0", ipProto="1", dst="10.0.0.0/8"),
        rule(target="ACCEPT")]))
    T._merge(b, T.plan_firewall(b, True, True, [rule(True, target="DROP", devname="eth0",
                                                     ipProto="58", dst="fd00::/64")]))
    check("bridge mode: ebtables (the jump to FIREWALL6 first, source and destination before the "
          "protocol; the IPv6 rules are not listed)",
          device(b).firewall_listing() == real("airos8.7.14-bridge-user-rules.txt"))


def more_flows(port):
    print('=== screens: from the "Network" menu, editing and deleting, and the other cases ===')
    srv, d, dev, ui, app = start(port)
    pf = dict(T.new_port_forward(app.working), host="192.168.1.10", dport="8080")
    res = {"devname": "br0", "on": True, "mac": "00:11:22:33:44:55", "ip": "192.168.1.70",
           "comment": "cam"}
    app._accept(T.plan_port_forwarding(app.working, True, [pf]))
    app._accept(T.plan_reservations(app.working, [res]))
    ok = run(app, "network_menu", [
        ("menu", "f6"), ("menu", "a"), ("menu", "d"), ("input", "fd00::/129"),
        ("input", "fd00::/64"), ("menu", "ok"), ("menu", None),
        ("menu", "f"), ("menu", None),
        ("menu", "d"), ("menu", "1"), ("menu", "e"), ("menu", "p"), ("input", "192.168.1.71"),
        ("menu", "ok"), ("menu", "1"), ("menu", "x"), ("yesno", 0), ("menu", None),
        ("menu", None)])
    listed = [dict(items).get("1", "") for t, items, _ in ui.menus if t == tr("DHCP reservations")]
    check('from "Network": the IPv6 firewall (a rule, with its validation), the IPv4 one and the '
          "reservations (editing one and deleting it)", ok
          and app.working.get("ip6tables.1.cmd") == "-A FIREWALL6 --protocol 41 --dst fd00::/64 "
                                                   "-j ACCEPT"
          and msgs(ui, tr("Invalid value")) == [tr("Destination IP/Mask is invalid")]
          and any("192.168.1.71" in x for x in listed) and T.dhcp_reservations(app.working) == [],
          str(msgs(ui)) + str(listed))
    ok = run(app, "network_menu", [
        ("menu", "r"), ("menu", "1"), ("menu", "e"), ("menu", "e"), ("menu", "s"),
        ("input", "10.0.0.0/8"), ("menu", None), ("yesno", 1), ("menu", "ok"),
        ("menu", "a"), ("menu", "ok"), ("menu", None),
        ("menu", "1"), ("menu", "x"), ("yesno", 0), ("menu", None), ("menu", None)])
    lines = [dict(items).get("1", "") for t, items, _ in ui.menus if t == tr("Port forwarding")]
    check("port forwarding: editing a rule (leaving with changes asks), accepting a new one "
          "without what is required (the web's messages) and deleting", ok
          and any(tr("from {source}", source="10.0.0.0/8") in x and x.endswith(tr("(disabled)"))
                  for x in lines)
          and msgs(ui, tr("Invalid value")) == [tr("Private IP is required") + "\n"
                                                + tr("Public Port is required")]
          and T.port_forwarding(app.working)[1] == [], str(lines) + str(msgs(ui)))
    app.working = app.original.copy()

    lan = T.lan_ifaces(app.working)[0]
    ok = run(app, "lan_menu", [
        ("menu", "d"), ("menu", "n"), ("input", "255.0.255.0"), ("input", "25"), ("input", "24"),
        ("menu", "m"), ("menu", None), ("menu", None),
        ("menu", "p"), ("yesno", 0), ("input", "8.8.8.8"), ("input", ""),
        ("menu", "d"), ("menu", "1"), ("input", "9.9.9.9"), ("menu", "2"), ("input", "1.0.0.1"),
        ("menu", "m"), ("menu", "3"), ("input", "10.0.0.9"), ("input", "ag"),
        ("menu", "i"), ("input", "10.0.0.10"), ("menu", "a"), ("input", "ag2"), ("menu", None),
        ("menu", "d"), ("menu", "m"), ("menu", "1"), ("yesno", 0), ("menu", None),
        ("menu", "p"), ("yesno", 0), ("menu", "p"), ("yesno", 1), ("menu", None)], lan)
    lan_rows = [dict(items).get("d") for t, items, _ in ui.menus if t == "LAN (br0)"]
    ds = T.lan_dhcp(app.working, "br0")
    check("DHCP: the netmask (with its messages), the DNS servers, the relay and disabling it; "
          "and enabling the DNS proxy again", ok and msgs(ui, tr("Invalid value")) == [
              tr("Netmask is invalid"), tr("Inconsistent DHCP Range")]
          and "* " + tr("DHCP server: {state}", state=tr("relay to {ip}", ip="10.0.0.10"))
          in lan_rows and lan_rows[-1] == "* " + tr("DHCP server: {state}", state=tr("disabled"))
          and ds["mode"] == "disabled" and ds["proxy"], str(lan_rows) + str(msgs(ui)))

    dev.dhcp_leases = lambda: {"aaData6": [["00:01:00:01:aa", "fd00::5", 3600]]}
    ok = run(app, "show_leases", [])
    shown = [t for k, ti, t in ui.log if k == "textbox"]
    check("the IPv6 leases, as its table shows them", ok and shown and "fd00::5" in shown[0]
          and tr("Unique Identifier") in shown[0], str(shown))

    def fails_now():
        raise T.AirOSError("no answer")
    dev.dhcp_leases = fails_now
    ok = run(app, "show_leases", []) and run(app, "_device_text", [], "Firewall", fails_now)
    check("  and if the device does not answer, it says so", ok and msgs(ui) == ["no answer"])

    def loading():
        raise T.AirOSError("/api/firewall/user-rules answered HTTP 500: error running ebtables")
    ok = run(app, "_device_text", [], "Firewall", loading)
    check("  (right after applying, the device is still loading its rules: it explains so)", ok
          and msgs(ui)[0].endswith(tr("Right after applying, the device takes about 15 s to "
                                      "load its rules: try again in a moment.")))
    ok = run(app, "firewall_menu", [("menu", "a"), ("menu", "p"), ("menu", "5"), ("menu", "ok"),
                                    ("menu", None)], False)
    check("a P2P rule: it warns that a real 8.7.14 does not apply it", ok and msgs(ui) == [
        tr("Careful: a NanoStation 5AC with airOS 8.7.14 keeps the P2P rules but does not "
           'apply them (they are not among its rules in use). Check "Rules in use (on the '
           'device)" after applying.')], str(msgs(ui)))
    srv.shutdown()


def review_rules(port):
    print("=== the review: rules whose command is not read whole ===")

    def read(cmd, v6=False):
        r = rule(v6)
        r.update(T.parse_fw_cmd(cmd))
        r["cmd"] = cmd
        return r
    web = {"ebtables": ["-A FIREWALL -i ath0 -p 0x0800 --ip-protocol 17 --ip-sport 67 "
                        "--ip-dport 68 -j DROP", "-A FIREWALL -i eth0 -p 0x0800 --ip-protocol 6 "
                        "--ip-src ! 10.0.0.0/8 --ip-dport 22 -j ACCEPT"],
           "iptables": ["-A FIREWALL -m ipp2p --ipp2p -j DROP",
                        "-A FIREWALL -i eth0 --protocol 6 --dport 22 -j DROP",
                        "-A FIREWALL ! --src 10.0.0.0/8 -j DROP",
                        "-A FIREWALL -i eth0 --src 10.0.0.0/8 -j DROP"]}
    check("the commands as its web writes them are read whole, in each table (in iptables "
          "without a protocol too: its --protocol ip is any)",
          all(T.fw_cmd_lossless(read(c), t, False) for t, cmds in web.items() for c in cmds))
    odd = ["-A FIREWALL -i eth0 -p tcp --dport 23 -j DROP",
           "-A FIREWALL -i eth0 -p 0x0800 -m limit --limit 5/s -j DROP"]
    check("  others are not (iptables -p tcp, or a limit its web does not know): rewritten, they "
          "would be another, broader rule", not any(T.fw_cmd_lossless(read(c), t, False)
                                                    for c in odd
                                                    for t in ("ebtables", "iptables")))
    negated = [("iptables", False, "-A FIREWALL ! -i br0 --protocol 6 --dport 22 -j DROP"),
               ("ebtables", False, "-A FIREWALL -i ! ath0 -p 0x0800 -j DROP"),
               ("ebtables", False, "-A FIREWALL -p ! 0x0800 -j ACCEPT")]
    check("  nor those with a ! its parseCmd drops (on -i, -p…): rewritten, the opposite rule "
          "(SSH blocked but from the LAN: blocked from the LAN), or a broken one",
          not any(T.fw_cmd_lossless(read(c, v6), t, v6) for t, v6, c in negated))
    filled = [("ebtables", False, "-A FIREWALL -i ath0 -j DROP"),
              ("ip6tables", True, "-A FIREWALL6 -i eth0 -j DROP")]
    check("  nor those its model would fill in: ebtables without -p (IPv4 only, after), "
          "ip6tables without --protocol (41 only, after)",
          not any(T.fw_cmd_lossless(read(c, v6), t, v6) for t, v6, c in filled))
    srv, d, dev, ui, app = start(port, mode="bridge", extra={
        "ebtables.status": "enabled", "ebtables.sys.status": "enabled",
        "ebtables.sys.fw.status": "enabled", "ebtables.1.status": "enabled",
        "ebtables.1.cmd": odd[0]})
    try:
        r = T.firewall_rules(app.working, False)[0]
        ok = run(app, "_edit_fw_rule", [("menu", "e"), ("menu", "ok")], r, False)
        got = ok and app.ui.menus and [k for k, _ in app.ui.menus[-1][1]]
        check("editing it: it warns, only its switch and comment can change; its command stays",
              got == ["e", "c", "ok"] and any("-p tcp --dport 23" in t
                                               for k, ti, t in app.ui.log if k == "msgbox"),
              str(got))
        app.ui.script = [("menu", "e"), ("menu", "ok")]
        out = app._edit_fw_rule(dict(r), False)
        check("  disabled, with the same command", out is not None and out["on"] is False
              and out["cmd"] == odd[0], str(out))
    finally:
        srv.shutdown()


if __name__ == "__main__":
    review_rules(19509)
    firewall_unit()
    portfw_unit()
    dhcp_unit()
    real_listings()
    router_flows(19501)
    more_flows(19505)
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
