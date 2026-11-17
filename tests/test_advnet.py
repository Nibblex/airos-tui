"""Tests of the advanced network configuration of airOS 8: the configuration mode (simple or
advanced, and going back to simple as its web does), its VLANs and its bridges, as its web loads
and saves them (with the real configuration of a NanoStation 5AC loco with 8.7.14); and the
menus against the simulator, with test mode. All on 127.0.0.1."""
import os
import sys
import threading

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("advnet")
tr = T.tr
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = T.Config(open(os.path.join(HERE, "fixtures", "real",
                                  "airos8.7.14-nanostation5ac-sta.cfg"), encoding="utf-8").read())


def applied(c, plan):
    return T._merge(c.copy(), plan)


def sim_cfg(mode="bridge"):
    return T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60, mode=mode).running))


print("=== what its web loads (the real NanoStation) ===")
check("hardware interfaces, by name; br0 with eth0 and ath0; no VLANs",
      T.hw_interfaces(REAL) == ["ath0", "eth0"] and T.vlan_list(REAL) == []
      and T.bridge_list(REAL) == [{"devname": "br0", "on": True, "stp": False,
                                   "ports": ["eth0", "ath0"], "comment": "",
                                   "option82": False}])
check("in use: eth0 (a port of BRIDGE0) and br0 (the management); none free",
      T.in_use(REAL, "eth0") == tr("it is a port of {bridge}", bridge="BRIDGE0")
      and T.in_use(REAL, "br0") == tr("the management is on it") and T.free_ports(REAL) == []
      and T.in_use(REAL, "eth1") is None)
check("its simple mode fits; the configuration mode, simple",
      T.simple_blockers(REAL) == [] and not T.cfg_mode_advanced(REAL))

print("=== its rules ===")
check("VLAN ID: required, an integer, 2 to 4094, not repeated", [
    T.vlan_id_problem(REAL, "eth0", v) for v in ("", "abc", "10.5", "1", "4095", "0x10", "100")]
      == [tr("VLAN ID required."), tr("VLAN ID value invalid."), tr("VLAN ID value invalid."),
          tr("VLAN ID must be between 2 and 4094."), tr("VLAN ID must be between 2 and 4094."),
          None, None])
with_vlan = applied(REAL, T.plan_vlans_bridges(REAL, T.new_vlan(REAL, "eth0", "100"),
                                                T.bridge_list(REAL)))
check("  (already there on that interface; not on another one)",
      T.vlan_id_problem(with_vlan, "eth0", "100") == tr("VLAN already exists.")
      and T.vlan_id_problem(with_vlan, "ath0", "100") is None)
check("ports: not empty, nor a VLAN with its parent interface", [
    T.bridge_ports_problem(with_vlan, p) for p in ([], ["eth0", "eth0.100"], ["eth0.100"])] == [
    tr("Bridge cannot be empty."),
    tr("Selected ports cannot contain combination of VLAN and its parent interface."), None])
small = with_vlan.copy()
small.set("netconf.4.mtu", "1400")
check("  nor a VLAN with an MTU under 1500", T.bridge_ports_problem(small, ["eth0.100"])
      == tr("Wrong MTU value ({mtu}) for port {port}. Cannot be less than {min}.", mtu=1400,
            port="LAN0.100", min="1500"))
check("the new bridge's name: one more than the highest (br_100 counts as 100)",
      T.next_bridge_name(T.bridge_list(REAL)) == "br1"
      and T.next_bridge_name([{"devname": "br0"}, {"devname": "br_100"}]) == "br101")
ap = sim_cfg()
check("on an access point, a wireless VLAN brings its own bridge (as its web does)",
      T.new_vlan(ap, "ath0", "20") == [
          {"parent": "ath0", "id": "20", "devname": "ath0.20", "on": True, "comment": ""},
          {"devname": "br_20", "on": True, "stp": False, "ports": ["ath0.20"],
           "option82": False, "comment": "Automatically created for VLAN ID 20"}]
      and len(T.new_vlan(ap, "eth0", "20")) == 1 and len(T.new_vlan(REAL, "ath0", "20")) == 1)

print("=== what its web saves ===")
bridges = T.bridge_list(REAL) + [{"devname": "br1", "on": True, "stp": False,
                                  "ports": ["eth0.100"], "comment": "", "option82": False}]
plan = T.plan_vlans_bridges(REAL, T.new_vlan(REAL, "eth0", "100", "test"), bridges)
check("a VLAN and a bridge with it: their sections, their netconf and the VLAN's ebtables "
      "entry (its parent is a port of br0)", plan == {
          "vlan.status": "enabled", "vlan.1.status": "enabled", "vlan.1.id": "100",
          "vlan.1.devname": "eth0", "vlan.1.comment": "test",
          "bridge.2.status": "enabled", "bridge.2.devname": "br1",
          "bridge.2.stp.status": "disabled", "bridge.2.port.1.status": "enabled",
          "bridge.2.port.1.devname": "eth0.100",
          "netconf.4.status": "enabled", "netconf.4.devname": "eth0.100",
          "netconf.4.autoip.status": "disabled", "netconf.4.mtu": "1500",
          "netconf.5.status": "enabled", "netconf.5.devname": "br1",
          "netconf.5.autoip.status": "disabled", "netconf.5.mtu": "1500",
          "ebtables.sys.vlan.status": "enabled", "ebtables.sys.vlan.1.status": "enabled",
          "ebtables.sys.vlan.1.id": "100", "ebtables.sys.vlan.1.devname": "eth0"}, str(plan))
two = applied(REAL, plan)
back = applied(two, T.plan_vlans_bridges(two, [], T.bridge_list(REAL)))
check("and deleting them leaves the configuration as it was", not back.diff(REAL),
      str(back.diff(REAL)))
off = applied(two, T.plan_vlans_bridges(two, [dict(T.vlan_list(two)[0], on=False)],
                                         T.bridge_list(two)))
check("disabled: without its netconf or its ebtables entry", T.vlan_list(off)[0]["on"] is False
      and "eth0.100" not in [off.get(f"netconf.{n}.devname") for n in range(1, 7)]
      and off.get("ebtables.sys.vlan.1.id") is None, str(T.vlan_list(off)))
rules = two.copy()
for k, v in (("ebtables.status", "enabled"), ("ebtables.sys.status", "enabled"),
             ("ebtables.sys.fw.status", "enabled"),
             ("ebtables.1.cmd", "-t filter -A FIREWALL -p 0x0800 -i eth0.100 -j DROP"),
             ("ebtables.1.status", "enabled"),
             ("ebtables.2.cmd", "-t filter -A FIREWALL -p 0x0800 -i eth0 -j ACCEPT"),
             ("ebtables.2.status", "enabled"),
             ("tshaper.1.devname", "eth0.100"), ("tshaper.1.status", "enabled"),
             ("tshaper.2.devname", "eth0"), ("tshaper.2.status", "enabled")):
    rules.set(k, v)
gone = applied(rules, T.plan_vlans_bridges(rules, [], T.bridge_list(REAL)))
check("deleting it removes its firewall and traffic shaping rules (the rest are renumbered)",
      [r["devname"] for r in T.firewall_rules(gone, False)] == ["eth0"]
      and gone.get("ebtables.1.cmd") == "-t filter -A FIREWALL -p 0x0800 -i eth0 -j ACCEPT"
      and gone.get("tshaper.1.devname") == "eth0" and gone.get("tshaper.2.devname") is None,
      str([(k, gone.get(k)) for k in gone.keys() if k.startswith(("ebtables.1", "tshaper"))]))
dis = applied(rules, T.plan_vlans_bridges(rules, [dict(T.vlan_list(rules)[0], on=False)],
                                           T.bridge_list(rules)))
check("disabling it disables them", [r["on"] for r in T.firewall_rules(dis, False)]
      == [False, True] and dis.get("tshaper.1.status") == "disabled"
      and dis.get("tshaper.2.status") == "enabled")
out = rules.copy()
for k, v in (("ebtables.3.cmd", "-t filter -A FIREWALL -p 0x0800 -o eth0.100 -j DROP"),
             ("ebtables.3.status", "enabled")):
    out.set(k, v)
out_gone = applied(out, T.plan_vlans_bridges(out, [], T.bridge_list(REAL)))
out_off = applied(out, T.plan_vlans_bridges(out, [dict(T.vlan_list(out)[0], on=False)],
                                              T.bridge_list(out)))
check("  the same with those that send through it (its output, -o): gone with it, off with it",
      [(r["devname"], r["devnameOutput"]) for r in T.firewall_rules(out_gone, False)]
      == [("eth0", "")] and [r["on"] for r in T.firewall_rules(out_off, False)]
      == [False, True, False], str(T.firewall_rules(out_gone, False)))
br = two.copy()
for k, v in (("bridge.9.devname", "br_20"), ("bridge.9.status", "enabled"),
             ("vlan.9.devname", "eth0"), ("vlan.9.status", "enabled")):
    br.set(k, v)
check("a rule's interfaces in bridge mode: not its bridges (an access point's br_20 either), "
      "nor a VLAN without its ID", T.net_mode(br) == "bridge"
      and not [d for d in T.rule_interfaces(br) if d.startswith("br") or "None" in d],
      str(T.rule_interfaces(br)))
o82 = applied(REAL, T.plan_vlans_bridges(REAL, [], [dict(T.bridge_list(REAL)[0],
                                                         option82=True)]))
check("option 82 (bridge mode): its DHCP relay and its ebtables entry", {
    k: o82.get(k) for k in o82.keys() if k.startswith(("dhcprelay", "ebtables.sys.dhcp"))} == {
    "dhcprelay.status": "enabled", "dhcprelay.server.1.status": "enabled",
    "dhcprelay.server.1.devname": "br0", "dhcprelay.client.1.status": "enabled",
    "dhcprelay.client.1.devname": "br0", "dhcprelay.client.1.agentid": "_mac_",
    "ebtables.sys.dhcp_relay.status": "enabled", "ebtables.sys.dhcp_relay.1.status": "enabled",
    "ebtables.sys.dhcp_relay.1.devname": "br0"}
      and T.bridge_list(o82)[0]["option82"] is True)
check("  and turning it off deletes them", not applied(o82, T.plan_vlans_bridges(
    o82, [], T.bridge_list(REAL))).diff(REAL))
check("an ID such as 0x10 (its parseInt: 16) is saved as 16",
      T.new_vlan(REAL, "eth0", "0x10")[0]["id"] == "16")

print("=== the configuration mode ===")
check("to advanced, only its key", T.plan_cfg_mode(True) == {
    "gui.network.advanced.status": "enabled"})
mgmt = applied(REAL, T.plan_mgmt_vlan(REAL, 50))
check("what fits the simple mode: a br0 with hardware interfaces only, or the management VLAN",
      T.simple_blockers(mgmt) == [] and T.simple_blockers(two) == [tr("VLANs and bridges")])
alias = REAL.copy()
alias.set("netconf.3.alias.1.ip", "10.0.0.1")
check("no aliases, no firewall rules, no traffic shaping rules", T.simple_blockers(alias) == [
    tr("IP aliases")] and T.simple_blockers(rules) == [tr("firewall rules"),
                                                       tr("traffic shaping rules")])
router = sim_cfg("router")
extra = applied(router, T.plan_vlans_bridges(router, [], T.bridge_list(router) + [
    {"devname": "br1", "on": True, "stp": False, "ports": ["ath0"], "comment": "",
     "option82": False}]))
check("router: one bridge at most, no VLANs, one LAN", T.simple_blockers(router) == []
      and T.simple_blockers(extra) == [tr("VLANs and bridges")])
simple = applied(two, T.plan_simple(two, {}))
check("back to simple when it does not fit: its template again, without VLANs or br1, and the "
      "simple mode",
      T.vlan_list(simple) == [] and [b["devname"] for b in T.bridge_list(simple)] == ["br0"]
      and simple.get("gui.network.advanced.status") == "disabled"
      and simple.get("ebtables.sys.vlan.status") is None, str(T.bridge_list(simple)))
check("  its management on DHCP stays on DHCP, with its fallback and its automatic IP (its web "
      "would leave the template's static IP, 192.168.1.20: a device with its IP over DHCP "
      "would be lost)",
      T.ip_mode(simple, "br0") == "dhcp" and T.dhcp_fallback(simple, "br0") == (
          "192.168.1.20", "255.255.255.0") and T.default_route(simple) == (1, "")
      and simple.get("netconf.3.ip") == "0.0.0.0"
      and T.main_iface(simple, "v8")["autoip"] == T.main_iface(REAL, "v8")["autoip"],
      str([(k, simple.get(k)) for k in simple.keys() if k.startswith(("dhcpc", "route"))]))
static = two.copy()
for k, v in (("netconf.3.ip", "10.1.2.3"), ("netconf.3.netmask", "255.255.0.0"),
             ("dhcpc.status", "disabled"), ("route.1.devname", "br0"),
             ("route.1.gateway", "10.1.0.1"), ("route.1.ip", "0.0.0.0"),
             ("route.1.netmask", "0"), ("route.1.status", "enabled"),
             ("resolv.nameserver.1.ip", "1.1.1.1"), ("resolv.nameserver.1.status", "enabled"),
             ("resolv.nameserver.2.ip", "8.8.8.8"), ("resolv.nameserver.2.status", "enabled")):
    static.set(k, v)
kept = applied(static, T.plan_simple(static, {}))
check("  with a static IP, it keeps it (with its gateway, its DNS and its automatic IP)",
      (kept.get("netconf.3.ip"), kept.get("netconf.3.netmask"), kept.get("route.1.gateway"),
       kept.get("resolv.nameserver.1.ip"), kept.get("resolv.nameserver.2.ip"),
       kept.get("netconf.3.autoip.status"), kept.get("dhcpc.status")) == (
          "10.1.2.3", "255.255.0.0", "10.1.0.1", "1.1.1.1", "8.8.8.8", "enabled", "disabled"))



def routed(station):
    """A router with a static WAN, NAT, blocked management, a DMZ and a cloned MAC."""
    c = T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60, mode="router",
                                     station=station).running))
    wan = T.main_iface(c, "v8")
    c.set("dhcpc.status", "disabled")
    for k, v in ((f"netconf.{wan['n']}.ip", "203.0.113.10"),
                 (f"netconf.{wan['n']}.netmask", "255.255.255.0"),
                 (f"netconf.{wan['n']}.hwaddr.status", "enabled"),
                 (f"netconf.{wan['n']}.hwaddr.mac", "00:11:22:33:44:55"),
                 ("iptables.sys.dmz.status", "enabled"), ("iptables.sys.dmz.1.status", "enabled"),
                 ("iptables.sys.dmz.1.devname", wan["devname"]),
                 ("iptables.sys.dmz.1.host", "192.168.1.50")):
        c.set(k, v)
    wan = T.main_iface(c, "v8")
    T._merge(c, T.plan_nat(c, wan, True))
    T._merge(c, T.plan_block_mgmt(c, wan, True))
    return c, wan


for station, wan_dev in ((True, "br0"), (False, "eth0")):
    c, wan = routed(station)
    after = applied(c, T.plan_simple(c, {}))
    who = ("station (its WAN, br0)" if station
           else "access point (its WAN, eth0: its web would put br0, its LAN)")
    check(f"back to simple, router, {who}: its IP, NAT, blocked management, DMZ and MAC",
          (after.get("netconf.1.devname"), after.get("netconf.1.ip"),
           after.get("netconf.1.netmask"), after.get("netconf.1.hwaddr.mac"))
          == (wan_dev, "203.0.113.10", "255.255.255.0", "00:11:22:33:44:55")
          and T.nat_on(after, T.main_iface(after, "v8"))
          and T.mgmt_blocked(after, T.main_iface(after, "v8"))
          and (after.get("iptables.sys.dmz.1.devname"), after.get("iptables.sys.dmz.1.host"))
          == (wan_dev, "192.168.1.50") and after.get("dhcpc.status") == "disabled"
          and after.get("gui.network.advanced.status") == "disabled",
          str({k: after.get(k) for k in after.keys() if k.startswith(
              ("netconf.1", "iptables.sys.masq", "iptables.sys.dmz.1.devname"))}))


dhcp_router = sim_cfg("router")
dwan = T.main_iface(dhcp_router, "v8")
T._merge(dhcp_router, T.plan_dhcp(dhcp_router, dwan, "10.9.8.7", "255.255.0.0"))
after = applied(dhcp_router, T.plan_simple(dhcp_router, {}))
check("router with its WAN on DHCP: it stays on DHCP, with its fallback (not the template's)",
      T.ip_mode(after, T.main_iface(after, "v8")["devname"]) == "dhcp"
      and T.dhcp_fallback(after, T.main_iface(after, "v8")["devname"]) == (
          "10.9.8.7", "255.255.0.0"))


class Offline:
    """A device that only gives its configuration (the App, without a network)."""
    flavor, host, base, username, notes, backup_loaded = (
        "v8", "192.0.2.1", "https://192.0.2.1", "ubnt", [], False)

    def __init__(self, text):
        self.text = text

    def status(self):
        return {}

    def get_config(self):
        return self.text


def offline(cfg):
    ui = FakeUI()
    app = T.App(ui, Offline(cfg.text()))
    app.set_loaded(cfg.text())
    return ui, app


def mode_menu(ui, app, answer):
    ui.log.clear()
    ui.script = [("menu", "a"), ("yesno", answer), ("menu", None)]
    app.network_menu()
    return [t for k, ti, t in ui.log if k in ("yesno", "msgbox")]


print("=== back to the mode the device has: its network too (as on a real LiteBeam) ===")
# As on the LiteBeam 5AC (8.7.25) where it happened: advanced configuration, management over
# DHCP and its firewall rule (on the real NanoStation, also with its management over DHCP).
lite = REAL.copy()
for k, v in (("gui.network.advanced.status", "enabled"), ("ebtables.status", "enabled"),
             ("ebtables.sys.status", "enabled"), ("ebtables.sys.fw.status", "enabled"),
             ("ebtables.1.cmd", "-A FIREWALL -i ath0 -p 0x0800 --ip-protocol 17 --ip-sport 67 "
                                "--ip-dport 68 -j DROP"),
             ("ebtables.1.comment", "BLOCK DHCP FROM WLAN"), ("ebtables.1.status", "enabled")):
    lite.set(k, v)
for answer, left in ((0, "none"), (1, "web"), (255, "esc")):
    ui, app = offline(lite)
    asked = mode_menu(ui, app, 0)
    rebuilt = len(app.changes())
    if answer == 0:
        check("to simple: it warns, with its management on DHCP and how to undo it", rebuilt > 30
              and tr("with {main} still on DHCP (fallback IP {ip}).", main=tr("the management"),
                     ip="192.168.1.20") in asked[0]
              and asked[0].endswith(tr("Going back to the advanced mode brings its network "
                                       "back, as the device has it.")), asked[0])
    back = mode_menu(ui, app, answer)
    keys = [k for k, _, _ in app.changes()]
    if left == "none":
        check('  and back to advanced, "Bring it back": its network as the device has it, no '
              "changes",
              keys == [] and back[0].startswith(tr("Back to the advanced network configuration, "
                                                   "the one the device has."))
              and tr("Its network can come back too, as the device has it: the {count} network "
                     "change(s) prepared since are undone (what the change of mode rebuilt, and "
                     "any other). With «Only the mode», as its web does, the network stays as "
                     "it is prepared.", count=rebuilt - 1) in back[0]
              and back[1] == tr("Its configuration mode and its network are back as the device "
                                "has them."), str((keys, back)))
    elif left == "web":
        check('  "Only the mode", as its web does: the rebuilt network stays (what was seen: 49 '
              "on the LiteBeam)",
              len(keys) == rebuilt - 1 and "gui.network.advanced.status" not in keys
              and "ebtables.1.cmd" in keys and len(back) == 1, str(len(keys)))
    else:
        check("  Esc: nothing", len(keys) == rebuilt and len(back) == 1)
ui, app = offline(REAL)
app._accept(T.plan_cfg_mode(True))
app._accept(T.plan_vlans_bridges(app.working, T.new_vlan(app.working, "eth0", "100"),
                                 T.bridge_list(app.working)))
back = mode_menu(ui, app, 0)
check('the other way round: advanced with a VLAN and back to simple, "Bring it back": no changes',
      not app.changes() and back[0].startswith(tr("Back to the simple network configuration, "
                                                  "the one the device has.")),
      str(app.changes()))
fixed = static.copy()
fixed.set("gui.network.advanced.status", "enabled")
ui, app = offline(fixed)
asked = mode_menu(ui, app, 1)
check('with a static IP, the warning says it keeps it; "No": nothing', not app.changes()
      and tr("keeping the static IP of {main} (with its gateway and DNS).",
             main=tr("the management")) in asked[0], asked[0])
ppp = sim_cfg("router")
pwan = T.main_iface(ppp, "v8")
T._merge(ppp, T.plan_pppoe(ppp, pwan, {"name": "u", "password": "p", "service": "",
                                       "fallback": "10.0.0.1", "fallback_netmask": "255.0.0.0",
                                       "mtu": 1492, "mru": 1492, "mppe": False}))
ppp.set("gui.network.advanced.status", "enabled")
ppp.set("vlan.status", "enabled")
for k, v in (("vlan.1.status", "enabled"), ("vlan.1.id", "7"), ("vlan.1.devname", "eth0")):
    ppp.set(k, v)
ui, app = offline(ppp)
asked = mode_menu(ui, app, 1)
check("with PPPoE: that the WAN stays on PPPoE and that, in router mode, its NAT, its DMZ, its "
      "MAC and its LAN are kept", tr("with {main} still on PPPoE (its account).",
                                     main=tr("the WAN")) + " " + tr(
          "Its NAT, its management blocking, its DMZ, its cloned MAC and its LAN (address and "
          "DHCP server) are kept too.") in asked[0], asked[0])
for k, v in (("iptables.sys.portfw.status", "enabled"), ("iptables.sys.portfw.1.status", "enabled"),
             ("iptables.sys.portfw.1.devname", pwan["devname"]),
             ("iptables.sys.portfw.1.host", "192.168.1.30"), ("iptables.sys.portfw.1.port", "80"),
             ("iptables.sys.portfw.1.proto", "tcp"), ("iptables.sys.portfw.1.dport", "8080")):
    ppp.set(k, v)
ui, app = offline(ppp)
asked = mode_menu(ui, app, 1)
said = next((x for x in asked[0].split("\n") if x.startswith(tr("Lost: {items}.").split("{")[0])),
            "")
check("  and what it loses, which the template does not bring back (its port forwarding, its "
      "VLAN), not what it keeps", tr("port forwarding") in said and "VLAN" in said
      and "NAT" not in said and "PPPoE" not in said, asked[0])


print("=== the review: ath0 in its bridge, plan_simple in router and SOHO, and the mode ===")
made = T.switch_netmode(REAL, "v8", "router", {})          # (its template: ath0 without netconf)
kept = applied(made, T.plan_vlans_bridges(made, T.new_vlan(made, "eth0", "100"),
                                          T.bridge_list(made)))
check("a router made with its template: ath0 stays in br0 when a VLAN is added (and its EAP)",
      T.bridge_list(made)[0]["ports"] == ["ath0"] and T.bridge_list(kept)[0]["ports"] == ["ath0"]
      and kept.get("ebtables.sys.eap.1.devname") == "ath0", str(T.bridge_list(kept)))


def router_case(how, station=False):
    c = sim_cfg("router") if not station else T.Config(S.cfg_text(S.Device(
        8, "ubnt", "ubnt", 1, 60, mode="router", station=True).running))
    w = T.main_iface(c, "v8")
    if how == "static":
        T._merge(c, T.plan_static(c, w, "203.0.113.10", "255.255.255.0", "203.0.113.1",
                                  "8.8.8.8", ""))
    elif how == "pppoe":
        T._merge(c, T.plan_pppoe(c, w, {"name": "u", "password": "p", "service": "",
                                        "fallback": "10.0.0.1", "fallback_netmask": "255.0.0.0",
                                        "mtu": "1492", "mru": "1492", "mppe": False}))
    w = T.main_iface(c, "v8")
    T._merge(c, T.plan_block_mgmt(c, w, True))
    c.set(f"netconf.{w['n']}.hwaddr.status", "enabled")
    c.set(f"netconf.{w['n']}.hwaddr.mac", "00:11:22:33:44:55")
    lan = T.lan_ifaces(c)[0]
    T._merge(c, T.plan_lan(c, lan, "10.0.0.1", "255.255.255.0", True, "10.0.0.50", "10.0.0.99"))
    return c


def summary(c):
    w, lan = T.main_iface(c, "v8"), T.lan_ifaces(c)[0]
    return (T.ip_mode(c, w["devname"]), T.nat_on(c, w), T.mgmt_blocked(c, w),
            c.get(f"netconf.{w['n']}.hwaddr.mac"), lan["ip"],
            T.lan_dhcp(c, lan["devname"])["mode"], T.lan_dhcp(c, lan["devname"])["start"])


for how in ("dhcp", "static", "pppoe"):
    for station in (False, True):
        c = router_case(how, station)
        after = applied(c, T.plan_simple(c, {}))
        check(f"back to simple, router {'station' if station else 'AP'} with its WAN on {how}: "
              "its mode, its NAT, the blocking, its MAC and its LAN with its DHCP (its web would "
              "lose them)",
              summary(after) == summary(c) and not [
                  p for p in T.check_config(after, None) if p.level == "error"],
              str((summary(c), summary(after))))
c = router_case("static")
after = applied(c, T.plan_simple(c, {}))
w = T.main_iface(after, "v8")
check("  and its default route, through its WAN (its web leaves it on br0, an AP's LAN)",
      after.get(f"route.{T.default_route(after)[0]}.devname") == w["devname"] == "eth0")
soho = T.switch_netmode(REAL, "v8", "soho", {})
T._merge(soho, T.plan_static(soho, T.main_iface(soho, "v8"), "203.0.113.10", "255.255.255.0",
                             "203.0.113.1", "", ""))
after = applied(soho, T.plan_simple(soho, {}))
check("SOHO with a static WAN: on its WAN (eth0), not on its LAN",
      (T.main_iface(after, "v8")["devname"], T.main_iface(after, "v8")["ip"],
       T.lan_ifaces(after)[0]["ip"]) == ("eth0", "203.0.113.10", "192.168.1.1")
      and not [p for p in T.check_config(after, None) if p.level == "error"])
nogw = REAL.copy()
T._merge(nogw, T.plan_static(nogw, T.main_iface(nogw, "v8"), "10.1.2.3", "255.255.0.0", "", "",
                             ""))
after = applied(nogw, T.plan_simple(nogw, {}))
check("static without a gateway: its route, disabled (not empty and enabled)",
      T.default_route(after) == (1, "") and after.get("route.1.status") == "disabled")
eth1 = applied(REAL, {"netconf.9.devname": "eth1", "netconf.9.status": "enabled",
                     "bridge.1.port.3.devname": "eth1", "bridge.1.port.3.status": "enabled"})
eth1.set("gui.network.advanced.status", "enabled")
check("without its board's data, its second port comes from its configuration (eth1 stays in "
      "br0)", "eth1" in T.bridge_list(applied(eth1, T.plan_simple(eth1, {})))[0]["ports"])
two_lan = T.switch_netmode(REAL, "v8", "router", {"board.phycount": "2"})
ui, app = offline(two_lan)
app._accept(T.plan_cfg_mode(True))
back = mode_menu(ui, app, 0)
check("back to its simple mode with nothing to undo: only the mode, even if its network does "
      'not "fit" according to its web (as a router with two ports)', not app.changes()
      and back[0].startswith(tr("Switch to the simple network configuration?\n\nThe network "
                                "as it is fits it: only the mode changes.")), str(back[:1]))
lost = lite.copy()
lost.delete("netconf.3.role")
lost.delete("netconf.3.status")
ui, app = offline(lost)
ui.script = [("menu", "a"), ("menu", None)]
app.network_menu()
asked = [t for k, ti, t in ui.log if k in ("yesno", "msgbox")]
check("without its management interface: not offered (its IP would be the template's)",
      not app.changes() and asked == [tr("The simple mode is not offered: the interface of "
                                         "{main} is not found in its configuration, so its "
                                         "address could not be kept.", main=tr(
                                             "the management"))], str(asked))


def start(port, mode="bridge"):
    srv = S.make_server(8, port, restart=0.3, test_secs=60, mode=mode)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    return srv, srv.dev, ui, app


def rows(ui, title):
    return dict([items for t, items, _ in ui.menus if t == title][-1])


def flow(port):
    print("=== v8 (AP, bridge mode): advanced, VLANs and bridges; test and keep ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [("menu", None)]
        app.network_menu()
        check("in the Network menu: the configuration mode, simple",
              rows(ui, tr("Network")).get("a") == tr("Configuration mode: simple")
              and "vl" not in rows(ui, tr("Network")))
        ui.menus.clear()
        ui.script = [("menu", "a"), ("yesno", 0), ("menu", None)]
        app.network_menu()
        net = rows(ui, tr("Network"))
        check("to advanced: its VLANs and its bridges", net.get("a")
              == "* " + tr("Configuration mode: advanced")
              and net.get("vl") == tr("VLANs: {list}", list="—")
              and net.get("br") == tr("Bridges: {list}", list="BRIDGE0"), str(net))
        ui.menus.clear()
        ui.log.clear()
        ui.script = [
            ("menu", "+"), ("menu", "eth0"), ("input", "1"), ("input", "100"),
            ("input", " office "),
            ("menu", "+"), ("menu", "ath0"), ("input", "100"), ("input", ""),   # (with its bridge)
            ("menu", "2"), ("menu", "x"),                         # in use: it cannot be deleted
            ("menu", None)]
        app.vlans_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        notes = [t for k, ti, t in ui.log if k == "msgbox"]
        check("its message, the wireless VLAN's bridge and that it is in use", notes == [
            tr("VLAN ID must be between 2 and 4094."),
            tr("As its web does on an access point, a wireless VLAN gets a bridge of its own: "
               "{bridge}.", bridge="BRIDGE_100"),
            tr("{name} cannot be deleted: {reason}.", name="WLAN0.100",
               reason=tr("it is a port of {bridge}", bridge="BRIDGE_100"))], str(notes))
        check("both, marked", rows(ui, tr("VLANs")) == {
            "1": "* LAN0  VLAN 100  · office", "2": "* WLAN0  VLAN 100",
            "+": tr("Add a VLAN")}, str(rows(ui, tr("VLANs"))))
        ui.menus.clear()
        ui.log.clear()
        ui.script = [
            ("menu", "+"), ("menu", "ok"), ("menu", "eth0.100"), ("menu", "ath0"),
            ("menu", "ok"), ("input", "trunk"),                  # a new bridge with LAN0.100
            ("menu", "3"), ("menu", "s"),                        # its STP
            ("menu", "1"), ("menu", "o"),                        # option 82 on br0
            ("menu", "1"), ("menu", "x"),                        # br0 with WLAN0: no
            ("menu", "1"), ("menu", "e"),                        # nor disabling it (management)
            ("menu", None)]
        app.bridges_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        notes = [t for k, ti, t in ui.log if k == "msgbox"]
        check("its messages (empty, WLAN0 fixed, br0 is not touched)", notes == [
            tr("Bridge cannot be empty."),
            tr("As in its web, the wireless interface (WLAN0) cannot be moved."),
            tr("{name} cannot be deleted: {reason}.", name="BRIDGE0",
               reason=tr("it has the wireless interface")),
            tr("{name} cannot be disabled: {reason}.", name="BRIDGE0",
               reason=tr("the management is on it"))], str(notes))
        brs = rows(ui, tr("Bridges"))
        check("the bridges: br0 with option 82, the wireless VLAN's one and the new one with STP",
              brs == {"1": "* BRIDGE0: LAN0, WLAN0  " + tr("option 82"),
                      "2": "* BRIDGE_100: WLAN0.100  · Automatically created for VLAN ID 100",
                      "3": "* BRIDGE101: LAN0.100  STP  · trunk", "+": tr("Add a bridge")},
              str(brs))
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 0)]
        app.apply(test=True)
        saved = T.Config(S.cfg_text(d.saved))
        check("saved: its VLANs and bridges, its netconf, its relay and its ebtables",
              [v["devname"] for v in T.vlan_list(saved)] == ["eth0.100", "ath0.100"]
              and [(b["devname"], b["ports"], b["stp"], b["option82"])
                   for b in T.bridge_list(saved)] == [
                  ("br0", ["eth0", "ath0"], False, True), ("br_100", ["ath0.100"], False, False),
                  ("br101", ["eth0.100"], True, False)]
              and saved.get("ebtables.sys.vlan.1.devname") == "eth0"
              and saved.get("gui.network.advanced.status") == "enabled"
              and ui.titles()[-1] == tr("Done"), str(ui.titles()))
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "a"), ("yesno", 0), ("menu", None)]
        app.network_menu()
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("back to simple: it warns as its web does, and says why",
              asked and asked[0].startswith(tr("Switch to simple mode?"))
              and tr("Warning: Changing to simple mode will erase all current advanced "
                     "networking settings (bridge/vlan/etc).") in asked[0]
              and tr("VLANs and bridges") in asked[0], str(asked[:1]))
        check("  and prepares its network again", T.vlan_list(app.working) == []
              and [b["devname"] for b in T.bridge_list(app.working)] == ["br0"]
              and app.working.get("gui.network.advanced.status") == "disabled")
        app.working = app.original.copy()
    finally:
        srv.shutdown()
        srv.server_close()


def more(port):
    print("=== v8: simple when it fits; enable, comment and delete; the ports; none free ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [("menu", "+"), ("menu", "ok")]           # (none free yet: it says so)
        app._add_bridge(T.bridge_list(app.working))
        ui.script = []
        check("with no free interfaces there is no new bridge", ui.titles()[-1:] == [tr("Bridges")]
              and not app.changes())
        app._accept(T.plan_cfg_mode(True))
        ui.log.clear()
        ui.script = [("menu", "a"), ("yesno", 0), ("menu", None)]
        app.network_menu()
        check("back to simple, when it fits: only the mode (its key, as its web saves it)",
              app.changes() == [("gui.network.advanced.status", None, "disabled")]
              and [t for k, ti, t in ui.log if k == "yesno"][0].startswith(tr(
                  "Switch to the simple network configuration?\n\nThe network as it is fits "
                  "it: only the mode changes.")), str(app.changes()))
        app._accept(T.plan_cfg_mode(True))
        ui.log.clear()
        ui.script = [
            ("menu", "+"), ("menu", "eth0"), ("input", "200"), ("input", None),  # cancel
            ("menu", "+"), ("menu", None),                                       # cancel
            ("menu", "+"), ("menu", "eth0"), ("input", "200"), ("input", "a"),
            ("menu", "1"), ("menu", "e"),                         # disable it (it is free)
            ("menu", "1"), ("menu", "e"),                         # and enable it
            ("menu", "1"), ("menu", "c"), ("input", None),        # comment: cancel
            ("menu", "1"), ("menu", "c"), ("input", " b "),
            ("menu", "1"), ("menu", None),                        # action: cancel
            ("menu", None)]
        app.vlans_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        check("the VLAN, with its comment", T.vlan_list(app.working) == [
            {"parent": "eth0", "id": "200", "devname": "eth0.200", "on": True,
             "comment": "b"}], str(T.vlan_list(app.working)))
        ui.script = [
            ("menu", "+"), ("menu", None),                        # ports: cancel
            ("menu", "+"), ("menu", "eth0.200"), ("menu", "ok"), ("input", None),
            ("menu", "+"), ("menu", "eth0.200"), ("menu", "ok"), ("input", ""),
            ("menu", "2"), ("menu", "e"),                         # disable it (it is free)
            ("menu", "2"), ("menu", "c"), ("input", "x"),
            ("menu", "2"), ("menu", "p"), ("menu", "eth0.200"), ("menu", "ok"),  # empty: no
            ("menu", None),
            ("menu", "2"), ("menu", "x"), ("yesno", 1),           # delete it: "No"
            ("menu", "2"), ("menu", "x"), ("yesno", 0),           # and "Yes"
            ("menu", None)]
        app.bridges_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        check("the bridge, deleted: only br0", [b["devname"] for b in T.bridge_list(app.working)]
              == ["br0"], str(T.bridge_list(app.working)))
        ui.script = [("menu", "1"), ("menu", "e"), ("menu", "1"), ("menu", "x"), ("yesno", 0),
                     ("menu", None)]
        app.vlans_menu()
        check("and the VLAN, free now, too (br0 stays as its web saves it)",
              T.vlan_list(app.working) == [] and T.bridge_list(app.working)
              == T.bridge_list(app.original) and not [
                  k for k, _, _ in app.changes() if k.startswith(("vlan", "netconf"))],
              str(app.changes()))
        app.working = app.original.copy()
    finally:
        srv.shutdown()
        srv.server_close()


flow(19941)
more(19951)
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
