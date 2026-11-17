"""Tests of the Network section: the keys' logic, validations, following the IP and discovery."""
import sys
import threading
import time

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("net")
tr = T.tr


def cfg_of(mode="bridge", version=8, **kw):
    d = S.Device(version, "ubnt", "ubnt", 1, 10, mode=mode)
    c = T.Config(S.cfg_text(d.running))
    for k, v in kw.items():
        c.set(k.replace("__", "."), v)
    return c


def applied(cfg, updates):
    c = cfg.copy()
    for k, v in updates.items():
        if v is None:
            c.delete(k)
        else:
            c.set(k, v)
    return c


def unit():
    print("=== network logic ===")
    b = cfg_of()
    ifc = T.main_iface(b)
    check("bridge: management interface br0", ifc["devname"] == "br0" and ifc["role"] == "mlan")
    check("bridge: static", T.ip_mode(b, "br0") == "static")
    s = applied(b,
                T.plan_static(b, ifc, "10.0.0.5", "255.255.255.0", "10.0.0.1", "1.1.1.1",
                              "9.9.9.9"))
    check("static: IP and netmask", s.get("netconf.3.ip") == "10.0.0.5"
          and s.get("netconf.3.netmask") == "255.255.255.0")
    check("static: replaces the default route", T.default_route(s) == (1, "10.0.0.1")
          and len(T._indices(s, "route")) == 1)
    check("static: DNS", T.dns_servers(s) == ["1.1.1.1", "9.9.9.9"])
    d = applied(b, T.plan_dhcp(b, ifc, "192.168.1.30", "255.255.255.0"))
    check("DHCP: mode", T.ip_mode(d, "br0") == "dhcp")
    check("DHCP: IP 0.0.0.0 and the fallback", d.get("netconf.3.ip") == "0.0.0.0"
          and T.dhcp_fallback(d, "br0") == ("192.168.1.30", "255.255.255.0"))
    s2 = applied(d,
                 T.plan_static(d, T.main_iface(d), "10.0.0.6", "255.255.255.0", "10.0.0.1", "", ""))
    check("DHCP -> static turns dhcpc off", T.ip_mode(s2, "br0") == "static"
          and s2.get("dhcpc.1.status") == "disabled" and s2.get("dhcpc.status") == "disabled")
    nogw = applied(b, T.plan_static(b, ifc, "10.0.0.5", "255.255.255.0", "", "", ""))
    check("no gateway: the route disabled + a warning", T.default_route(nogw) == (1, "")
          and [p.level for p in T.net_problems(nogw)] == ["warning"])
    r = cfg_of("router")
    wan = T.main_iface(r)
    check("router: WAN eth0 over DHCP", wan["devname"] == "eth0" and T.ip_mode(r, "eth0") == "dhcp")
    rs = applied(r, T.plan_static(r, wan, "10.0.0.5", "255.255.255.0", "10.0.0.1", "8.8.8.8", ""))
    check("router: a static WAN creates a route", T.default_route(rs) == (1, "10.0.0.1")
          and rs.get("route.1.devname") == "eth0")
    lan = T.lan_ifaces(r)[0]
    rl = applied(r,
                 T.plan_lan(r, lan, "10.1.1.1", "255.255.255.0", True, "10.1.1.10", "10.1.1.99",
                            "3600"))
    check("LAN: IP and range", rl.get("netconf.3.ip") == "10.1.1.1"
          and T.dhcp_server(rl, "br0")["start"] == "10.1.1.10"
          and rl.get("dhcpd.1.lease_time") == "3600" and not T.net_problems(rl))
    off = applied(r, T.plan_lan(r, lan, "192.168.1.1", "255.255.255.0", False))
    check("LAN: DHCP off", not T.dhcp_server(off, "br0")["on"])
    bad = applied(r,
                  T.plan_lan(r, lan, "10.1.1.1", "255.255.255.0", True, "192.168.1.2",
                             "192.168.1.9"))
    check("LAN: a range outside the network -> error",
          [p.level for p in T.net_problems(bad)] == ["error"])
    inside = applied(r,
                     T.plan_lan(r, lan, "10.1.1.50", "255.255.255.0", True, "10.1.1.10",
                                "10.1.1.99"))
    check("LAN: the IP inside the range -> warning",
          [p.level for p in T.net_problems(inside)] == ["warning"])
    check("netmasks", T.parse_mask("24") == "255.255.255.0"
          and T.parse_mask("/30") == "255.255.255.252"
          and T.parse_mask("255.0.255.0") is None)
    check("shifting a range",
          T.shift_into("192.168.1.100", "192.168.1.1/24", "10.0.0.1/24") == "10.0.0.100")
    bc = applied(b, T.plan_static(b, ifc, "10.0.0.255", "255.255.255.0", "10.0.0.1", "", ""))
    check("broadcast -> error", [p.level for p in T.net_problems(bc)] == ["error"])
    same = applied(b, T.plan_static(b, ifc, "10.0.0.1", "255.255.255.0", "10.0.0.1", "", ""))
    check("gateway = IP -> error", "error" in [p.level for p in T.net_problems(same)])
    edges = [applied(r, T.plan_lan(r, lan, "192.168.1.255", "255.255.255.0", False)),
             applied(r, T.plan_lan(r, lan, "192.168.1.0", "255.255.255.0", False)),
             applied(b, T.plan_dhcp(b, ifc, "192.168.1.255", "255.255.255.0"))]
    check("the network or the broadcast address: an error on a LAN and as a fallback IP too "
          "(no device can have it)", all("error" in [p.level for p in T.net_problems(c)]
                                         for c in edges),
          str([[p.msg for p in T.net_problems(c)] for c in edges]))

    class Offline:                          # (the App without a network: its configuration)
        flavor, host, base, username, notes, backup_loaded = (
            "v8", "192.168.1.20", "https://192.168.1.20", "ubnt", [], False)

        def __init__(self, text):
            self.text = text

        def status(self):
            return {}

        def get_config(self):
            return self.text
    on_dhcp = applied(b, T.plan_dhcp(b, ifc, "192.168.1.20", "255.255.255.0"))
    app = T.App(FakeUI(), Offline(on_dhcp.text()))
    app.set_loaded(on_dhcp.text())
    app.status = {"interfaces": [{"ifname": "br0", "status": {"ipaddr": "192.168.1.20"}}]}
    app._accept(T.plan_dhcp(app.working, ifc, "192.168.1.30", "255.255.255.0"))
    moved = app._follow_plan()
    app._accept(T.plan_dhcp(app.working, ifc, "192.168.1.20", "255.255.0.0"))
    check("on DHCP, reached through its fallback IP (no server answers): another fallback moves "
          "it there, and the program follows it; the same one does not",
          (moved or {}).get("new") == "192.168.1.30" and app._follow_plan() is None, str(moved))
    v8default = T.Config("netmode=bridge\nnetconf.3.status=enabled\nnetconf.3.devname=br0\n"
                         "netconf.3.ip=192.168.1.20\nnetconf.3.role=mlan\ndhcpc.status=enabled\n"
                         "dhcpc.1.status=enabled\ndhcpc.1.devname=br0\nradio.1.mode=Managed\n")
    check("airOS 8 factory config = DHCP", T.ip_mode(v8default, "br0") == "dhcp"
          and not T.net_problems(v8default))
    norole = T.Config("netmode=bridge\nnetconf.1.status=enabled\nnetconf.1.devname=br0\n"
                      "netconf.1.ip=1.2.3.4\n")
    check("no role: uses br0 (as airOS 8)", T.main_iface(norole)["n"] == 1)

    print("=== discovery ===")
    dev = S.Device(8, "ubnt", "ubnt", 1, 10, mgmt_ip="127.0.0.20")
    info = T.parse_discovery(dev.discovery_reply(), "127.0.0.1")
    check("v1 reply", info and info["ip"] == "127.0.0.20" and "68D79A98FBFF" in info["macs"]
          and info["hostname"] == "AP-North-Tower", str(info))
    check("unrelated packets", T.parse_discovery(b"\x01\x00\x00\x00", "1.2.3.4") is None
          and T.parse_discovery(b"\x02\x06abc", "1.2.3.4") is None)
    srv = S.make_server(8, 18690, restart=0.5, mgmt_ip="127.0.0.40", discovery_port=18691)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    found = T.discover(1.5, target="127.0.0.1", port=18691)
    check("discover() against the simulator", len(found) == 1 and found[0]["ip"] == "127.0.0.40",
          str(found))
    srv.cluster.shutdown()


def follow_flows(version, base_port):
    print(f"=== following the IP · airOS {version} ===")
    port, dport = base_port, base_port + 1
    srv = S.make_server(version, port, restart=0.8, test_secs=40, mgmt_ip="127.0.0.20",
                        discovery_port=dport)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    dev = T.AirOS(f"http://127.0.0.20:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.discovery = {"target": "127.0.0.1", "port": dport}
    app.load()

    # 1) a new static IP + "Keep"
    ui.script = [("menu", "2"), ("menu", "1"), ("input", "127.0.0.21"), ("input", "24"),
                 ("input", "127.0.0.1"), ("input", ""), ("input", ""), ("menu", None)]
    app.network_menu()
    note = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Management IP")]
    check(f"v{version}: reconnection note", note and "127.0.0.21" in note[-1], str(note[-1:]))
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    confirm = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test changes")]
    check(f"v{version}: the confirmation mentions the IP change", confirm
          and tr("⚠ The IP you are connected through changes: {old} → {new}.",
                 old="127.0.0.20", new="127.0.0.21") in confirm[0])
    decide = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test mode")]
    check(f"v{version}: deciding from the new IP", decide
          and tr("The antenna now answers at {new} (before {old}).", new="127.0.0.21",
                 old="127.0.0.20") in decide[0],
          str(ui.titles("yesno")) + str(ui.titles()))
    check(f"v{version}: saved and connected to .21", d.saved.get("netconf.3.ip") == "127.0.0.21"
          and dev.host == "127.0.0.21" and tr("Done") in ui.titles(), f"{dev.host} {ui.titles()}")
    check(f"v{version}: the old IP no longer answers", not dev.ping("127.0.0.20"))

    # 2) another IP + "Revert": back to .21
    ui.log.clear()
    ui.script = [("menu", "2"), ("menu", "1"), ("input", "127.0.0.22"), ("input", "24"),
                 ("input", "127.0.0.1"), ("input", ""), ("input", ""), ("menu", None)]
    app.network_menu()
    ui.script = [("yesno", 0), ("yesno", 1)]
    app.apply(test=True)
    check(f"v{version}: reverting goes back to the previous IP", dev.host == "127.0.0.21"
          and d.running.get("netconf.3.ip") == "127.0.0.21" and tr("Reverted") in ui.titles(),
          f"{dev.host} {ui.titles()}")
    check(f"v{version}: the change is still pending",
          app.working.get("netconf.3.ip") == "127.0.0.22")
    app.working = app.original.copy()

    # 3) not deciding (Esc): it points at the old IP and the test expires by itself
    ui.script = [("menu", "2"), ("menu", "1"), ("input", "127.0.0.23"), ("input", "24"),
                 ("input", "127.0.0.1"), ("input", ""), ("input", ""), ("menu", None)]
    app.network_menu()
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 255)]
    app.apply(test=True)
    check(f"v{version}: not confirmed -> it points at the previous IP", dev.host == "127.0.0.21"
          and tr("Not confirmed") in ui.titles())
    d.test_until = time.time()          # speed it up: let it expire now
    time.sleep(0.2)
    dev.ping()                           # triggers tick (but .21 is "down" until it comes back)
    ok = app.wait_for_device("t", 0, max_wait=15)
    check(f"v{version}: the test expired and it went back to .21", ok
          and d.running.get("netconf.3.ip") == "127.0.0.21")
    app.load()

    # 4) static -> DHCP: it finds it through discovery
    if version == 8:
        ui.log.clear()
        ui.script = [("menu", "2"), ("menu", "2"), ("input", "192.168.1.20"), ("input", "24"),
                     ("menu", None)]
        app.network_menu()
        note = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Management IP")]
        check("v8: the DHCP note mentions the MAC", note and "MAC" in note[-1])
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 0)]
        app.apply(test=True)
        check("v8: DHCP, found through discovery", dev.host == "127.0.0.77"
              and d.saved.get("dhcpc.1.status") == "enabled" and tr("Done") in ui.titles(),
              f"{dev.host} {ui.titles()}")
    srv.cluster.shutdown()


def router_flows(base_port):
    print("=== router: LAN and WAN ===")
    port = base_port
    srv = S.make_server(8, port, restart=0.8, test_secs=40, mode="router", mgmt_ip="127.0.0.30")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    dev = T.AirOS(f"http://127.0.0.30:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    ui.script = [("menu", "1"), ("menu", None)]
    app.network_menu()
    summary = [t for k, ti, t in ui.log if k == "textbox"]
    check("router summary", summary and "WAN" in summary[0] and "LAN: br0" in summary[0]
          and tr("NAT enabled on {ifaces}", ifaces="eth0") in summary[0],
          summary[0][:400] if summary else "")
    ui.log.clear()
    ui.script = [("menu", "l1"), ("menu", "1"), ("input", "127.0.0.31"), ("input", "24"),
                 ("menu", "d"), ("menu", "r"), ("input", "127.0.0.100"),
                 ("input", "127.0.0.200"), ("menu", "t"), ("input", "7200"), ("menu", None),
                 ("menu", None), ("menu", None)]
    app.network_menu()
    check("LAN: the IP, the range and the lease (its range stays in the network: it does not "
          "ask about it)", not ui.script and not ui.titles("yesno"), str(ui.log[-6:]))
    check("LAN: prepared", app.working.get("dhcpd.1.start") == "127.0.0.100"
          and app.working.get("netconf.3.ip") == "127.0.0.31")
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    check("LAN: applied, following the IP", dev.host == "127.0.0.31"
          and d.saved.get("dhcpd.1.lease_time") == "7200" and tr("Done") in ui.titles(),
          f"{dev.host} {ui.titles()}")
    ui.log.clear()
    ui.script = [("menu", "2"), ("menu", "1"), ("input", "10.0.0.5"), ("input", "24"),
                 ("input", "10.9.9.9"), ("input", "1.1.1.1"), ("input", ""), ("yesno", 1),
                 ("menu", None)]
    app.network_menu()
    check("WAN: a gateway outside the network asks for confirmation",
          tr("Attention") in ui.titles("yesno") and T.ip_mode(app.working, "eth0") == "dhcp")
    ui.script = [("menu", "2"), ("menu", "1"), ("input", "10.0.0.5"), ("input", "24"),
                 ("input", "10.0.0.1"), ("input", "1.1.1.1"), ("input", ""), ("menu", None)]
    app.network_menu()
    check("WAN: static, prepared without following (we are connected through the LAN)",
          T.ip_mode(app.working, "eth0") == "static" and app._follow_plan() is None)
    srv.cluster.shutdown()


def mgmt_vlan_unit():
    print("=== management VLAN (airOS 8): what its web saves ===")
    base = cfg_of()
    T._merge(base, T.bridge_firewall(base, "v8"))      # (as its web leaves it: with EAPOL)
    check("no management VLAN", T.mgmt_vlan(base) is None)
    up = T.plan_mgmt_vlan(base, 10)
    want = {"vlan.status": "enabled"}
    for n, hw in ((1, "eth0"), (2, "ath0")):
        want.update({f"vlan.{n}.status": "enabled", f"vlan.{n}.id": "10",
                     f"vlan.{n}.devname": hw,
                     f"netconf.{n + 3}.status": "enabled", f"netconf.{n + 3}.devname": f"{hw}.10",
                     f"netconf.{n + 3}.autoip.status": "disabled", f"netconf.{n + 3}.mtu": "1500",
                     f"bridge.2.port.{n}.status": "enabled",
                     f"bridge.2.port.{n}.devname": f"{hw}.10",
                     f"ebtables.sys.vlan.{n}.status": "enabled", f"ebtables.sys.vlan.{n}.id": "10",
                     f"ebtables.sys.vlan.{n}.devname": hw})
    want.update({"bridge.2.status": "enabled", "bridge.2.stp.status": "disabled",
                 "bridge.2.devname": "br1",
                 "netconf.6.status": "enabled", "netconf.6.devname": "br1", "netconf.6.mtu": "1500",
                 "netconf.6.role": "mlan", "netconf.6.ip": "192.168.1.20",
                 "netconf.6.netmask": "255.255.255.0", "netconf.6.autoip.status": "enabled",
                 "netconf.3.role": None, "netconf.3.ip": None, "netconf.3.netmask": None,
                 "netconf.3.autoip.status": "disabled",
                 "dhcpc.1.devname": "br1", "route.1.devname": "br1",
                 "ebtables.sys.vlan.status": "enabled"})
    check("enabling it with ID 10: a VLAN on eth0 and another on ath0, the bridge br1 with them, "
          "the management (role, IP, netmask, automatic IP, route and DHCP) on br1 and each "
          "VLAN's ebtables rule", up == want,
          str(sorted(set(up.items()) ^ set(want.items())))[:700])
    on = applied(base, up)
    ifc = T.main_iface(on)
    check("  and what it reads of it: VLAN 10, the management on br1 with its IP",
          T.mgmt_vlan(on) == 10 and ifc["devname"] == "br1" and ifc["ip"] == "192.168.1.20")
    check("disabling it: the configuration is left exactly as it was",
          base.diff(applied(on, T.plan_mgmt_vlan(on, None))) == [])
    v20 = applied(on, T.plan_mgmt_vlan(on, 20))
    check("changing the ID (10 → 20): only the VLANs' IDs and names change",
          T.mgmt_vlan(v20) == 20 and sorted(k for k, _, _ in on.diff(v20)) == [
              "bridge.2.port.1.devname", "bridge.2.port.2.devname", "ebtables.sys.vlan.1.id",
              "ebtables.sys.vlan.2.id", "netconf.4.devname", "netconf.5.devname", "vlan.1.id",
              "vlan.2.id"], str(on.diff(v20)))
    check("  (and the same ID changes nothing)", T.plan_mgmt_vlan(on, 10) == {})
    extra = applied(base, {"netconf.3.alias.1.status": "enabled",
                           "netconf.3.alias.1.ip": "10.9.9.9",
                           "netconf.3.alias.1.netmask": "255.255.255.0",
                           "netconf.3.ip6.status": "enabled", "netconf.3.ip6.1.status": "enabled",
                           "netconf.3.ip6.1.addr": "fd00::5", "netconf.3.ip6.1.netmask": "64"})
    xon = applied(extra, T.plan_mgmt_vlan(extra, 10))
    check("what goes with the management is what it writes (its IPv6 too); its IP aliases stay "
          "on br0 (in the web they are separate and saved by the interface's name)",
          xon.get("netconf.6.ip6.1.addr") == "fd00::5" and xon.get("netconf.6.ip6.status")
          == "enabled" and xon.get("netconf.3.alias.1.ip") == "10.9.9.9"
          and not [k for k in xon.keys() if k.startswith(("netconf.3.ip6.", "netconf.6.alias."))]
          and extra.diff(applied(xon, T.plan_mgmt_vlan(xon, None))) == [])
    b1 = applied(on, {"netconf.6.alias.1.status": "enabled", "netconf.6.alias.1.ip": "10.8.8.8",
                      "netconf.6.alias.1.netmask": "255.255.255.0"})
    c20, boff = (applied(b1, T.plan_mgmt_vlan(b1, v)) for v in (20, None))
    check("  and br1's stay with br1 when the ID changes, and are lost when it is disabled (as "
          "in the web)", c20.get("netconf.6.devname") == "br1"
          and c20.get("netconf.6.alias.1.ip") == "10.8.8.8"
          and not [k for k in boff.keys() if ".alias." in k])
    off = applied(on, {"vlan.2.status": "disabled"})
    fw = T.bridge_firewall(off, "v8")
    check("the VLAN rules, as its _saveEbtablesVlan: a disabled VLAN gets none",
          fw.get("ebtables.sys.vlan.status") == "enabled"
          and fw.get("ebtables.sys.vlan.1.devname") == "eth0"
          and "ebtables.sys.vlan.2.devname" not in fw, str(fw))
    d = S.Device(8, "ubnt", "ubnt", 1, 10, station=True)
    sta = T.Config(S.cfg_text(d.running))
    sta.set("wireless.1.wds.status", "disabled")
    T._merge(sta, T.bridge_firewall(sta, "v8"))
    sta_on = applied(sta, T.plan_mgmt_vlan(sta, 10))
    check("a station without WDS: ARP-NAT on ath0.10 too and EAPOL only on ath0 (as the web: "
          "ARP-NAT on every wireless port of the bridges, EAPOL on those that are not VLANs)",
          [sta_on.get(f"ebtables.sys.arpnat.{j}.devname") for j in (1, 2, 3)] == [
              "ath0", "ath0.10", None]
          and [sta_on.get(f"ebtables.sys.eap.{j}.devname") for j in (1, 2)] == ["ath0", None]
          and sta.diff(applied(sta_on, T.plan_mgmt_vlan(sta_on, None))) == [])
    req, bad = tr("VLAN ID required."), tr("VLAN ID is invalid")
    rng = tr("VLAN ID must be between {min} and {max}", min=2, max=4094)
    got = [T.mgmt_vlan_problem(v) for v in ("", " ", "abc", "-5", "1.5", "0", "1", "4095", "2",
                                            "4094", " 10 ")]
    check("its rules: required, a whole number, from 2 to 4094", got == [
        req, req, bad, bad, bad, rng, rng, rng, None, None, None], str(got))
    check("where its web offers it: airOS 8 in bridge mode, except on Gen3 boards",
          T.mgmt_vlan_offered(base, "v8", {}) and not T.mgmt_vlan_offered(base, "v6", {})
          and not T.mgmt_vlan_offered(cfg_of("router"), "v8", {})
          and not T.mgmt_vlan_offered(base, "v8", {"feature.g3": "1"}))
    check("  (with the advanced network configuration it is not offered: there it is done by "
          "hand)", T.advanced_network(cfg_of(gui__network__advanced__status="enabled"))
          and not T.advanced_network(base))


def mgmt_vlan_flows(port):
    print("=== management VLAN (airOS 8): preparing it and applying it with test mode ===")
    ip = "127.0.0.70"
    srv = S.make_server(8, port, restart=0.5, test_secs=40, mgmt_ip=ip)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    dev = T.AirOS(f"http://{ip}:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    real_wait = app.wait_for_device

    def short_wait(title, initial, max_wait=T.MAX_WAIT, alt=None):    # 6 s instead of 180
        return real_wait(title, initial, max_wait=min(max_wait, 6), alt=alt)
    app.wait_for_device = short_wait
    title = tr("Management VLAN")

    def row(key):
        return dict([m for m in ui.menus if m[0] == tr("Network")][-1][1]).get(key)

    def state(vid=None, changed=False):
        return ("* " if changed else "") + tr("Management VLAN: {state}", state=(
            f"VLAN {vid}" if vid else tr("disabled")))

    def device(which):
        return T.Config(S.cfg_text(getattr(d, which)))
    ok = run(app, "network_menu", ui, [("menu", None)])
    check('in "Network": the row, disabled', ok and row("v") == state(), row("v"))
    ok = run(app, "network_menu", ui, [
        ("menu", "v"), ("input", ""), ("input", "abc"), ("input", "1"), ("input", "10"),
        ("yesno", 1), ("menu", None)])
    invalid = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
    check('it asks for the ID with the web\'s messages; "No" prepares nothing', ok
          and invalid == [
              tr("VLAN ID required."), tr("VLAN ID is invalid"),
              tr("VLAN ID must be between {min} and {max}", min=2, max=4094)]
          and not app.changes(), str(invalid))
    ok = run(app, "network_menu", ui, [("menu", "v"), ("input", "10"), ("yesno", 0),
                                       ("menu", None)])
    ask = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
    check("it warns of what will happen at its IP and prepares it", ok and ask and ip in ask[0]
          and "VLAN 10" in ask[0] and T.mgmt_vlan(app.working) == 10
          and row("v") == state(10, True), f"{row('v')} {ask[:1]}")
    check("  (the STP row is still br0's, as in the web)",
          row("6") == tr("STP on the bridge {dev}: disabled", dev="br0"), row("6"))

    # 1) this computer is not on that VLAN: it stops answering and the test reverts it
    d.on_vlan = False
    ui.log.clear()
    ui.script = [("yesno", 0)]
    app.apply(test=True)
    question = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test changes")]
    lost = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("No answer")]
    check("outside the VLAN: it stops answering and says so (it is in test mode)", lost
          and ip in lost[0] and tr("It was in TEST MODE: if it is not confirmed, it goes back to "
                                   "the previous configuration by itself in a few minutes.")
          in lost[0] and d.test_until, str(lost) + str(ui.titles()))
    check("  (when asking, it warned that the network changes)", question and tr(
        "If you stop reaching the device, the test reverts by itself.") in question[0])
    check("  and the change is still pending", T.mgmt_vlan(app.working) == 10
          and T.mgmt_vlan(app.original) is None)
    d.test_until = time.time()          # speed it up: let it expire now
    time.sleep(0.2)
    dev.ping()                           # (triggers going back)
    ok = app.wait_for_device("t", 0)
    check("the test expires: it comes back without the VLAN and answers again", ok
          and "test-expired" in d.events and T.mgmt_vlan(device("running")) is None)

    # 2) on that VLAN: it answers at the same IP and "Keep" saves it
    d.on_vlan = True
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    saved = device("saved")
    check('on the VLAN: it answers at the same IP and "Keep" saves it', T.mgmt_vlan(saved) == 10
          and saved.get("ebtables.sys.vlan.1.devname") == "eth0" and dev.host == ip
          and tr("Done") in ui.titles() and not app.changes(), f"{dev.host} {ui.titles()}")
    with d.lock:
        rules = {k: v for k, v in d.running.items() if k.startswith("ebtables.sys.vlan.")}
        for k in rules:
            del d.running[k]
        unreachable = ip not in d.reachable_ips()
        d.running.update(rules)
    check("  (the simulator: without its VLAN's ebtables rule the management would not be "
          "reached)", unreachable)
    ok = run(app, "network_menu", ui, [("menu", "1"), ("menu", None)])
    summary = ([t for k, ti, t in ui.log if k == "textbox"] or [""])[0]
    check("the network summary: the management on br1 with its VLAN, br0 with its STP, and it no "
          "longer says the management VLAN is not changed from here", ok and tr(
              "── Management interface: {dev} (role {role}) ──", dev="br1", role="mlan") in summary
          and tr("VLAN {id} · bridge br1: {ports}", id=10, ports="eth0.10, ath0.10") in summary
          and tr("Bridge {dev}:", dev="br0") in summary
          and tr("{ports} · STP no", ports="eth0, ath0") in summary
          and tr("Not changed from here: the interface MTU, IPv6 or the static routes.\n"
                 "(The advanced configuration's VLANs and bridges are in this menu.)\n"
                 'For the rest use the web or "Search and edit any key".') in summary,
          summary[:900])
    ok = run(app, "network_menu", ui, [("menu", "v"), ("menu", "c"), ("input", "20"),
                                       ("yesno", 0), ("menu", None)])
    check("changing the ID: it prepares it with the new one", ok
          and T.mgmt_vlan(app.working) == 20 and row("v") == state(20, True), row("v"))
    app.working = app.original.copy()
    ok = run(app, "network_menu", ui, [("menu", "v"), ("menu", "o"), ("yesno", 0),
                                       ("menu", None)])
    ask = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
    check("disabling it: it warns and prepares it", ok and ask and ip in ask[0]
          and T.mgmt_vlan(app.working) is None and T.main_iface(app.working)["devname"] == "br0"
          and row("v") == state(None, True), row("v"))
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    saved = device("saved")
    check("  and applied: the management goes back to br0, without the VLAN or its rules",
          T.mgmt_vlan(saved) is None and saved.get("netconf.3.ip") == ip and not [
              k for k in saved.keys() if k.startswith(("vlan.", "ebtables.sys.vlan."))]
          and tr("Done") in ui.titles(), str(ui.titles()))

    # 3) the advanced network configuration: by hand, from its web
    with d.lock:
        for cfg in (d.running, d.saved, d.system):
            cfg["gui.network.advanced.status"] = "enabled"
    app.load()
    ok = run(app, "network_menu", ui, [("menu", "v"), ("menu", None)])
    note = [t for k, ti, t in ui.log if k == "msgbox" and ti == title]
    check("the advanced network configuration: the row says so and it explains that it is done "
          "in the web", ok
          and row("v") == tr("Management VLAN: {state}",
                             state=tr("set by hand (advanced configuration)"))
          and note and not app.changes(), row("v"))
    srv.cluster.shutdown()


def run(app, method, ui, script):
    """Runs app.method with that script: False if it asked for something else or did not use
    all of it."""
    ui.log.clear()
    ui.script = list(script)
    try:
        getattr(app, method)()
    except AssertionError as e:
        print("     script:", e)
        return False
    return not ui.script


def review_unit():
    print("=== the review: odd digits, a mistyped gateway, what is lost ===")
    check('a netmask with digits int() does not read ("²", "٣"): invalid, without breaking',
          [T.parse_mask(x) for x in ("24", "²", "٣", "/24")] == [
              "255.255.255.0", None, None, "255.255.255.0"])
    bad = cfg_of(**{"netconf__3__ip": "192.168.1.20", "netconf__3__netmask": "255.255.255.0",
                    "dhcpc__status": "disabled", "route__1__status": "enabled",
                    "route__1__ip": "0.0.0.0", "route__1__netmask": "0",
                    "route__1__devname": "br0", "route__1__gateway": "192.168.1.1 "})
    found = [p.msg for p in T.check_config(bad, None) if p.level == "error"]
    check("a gateway that is not an IP: an error, without breaking",
          tr("The gateway {gateway} is not an IPv4 address.", gateway="192.168.1.1 ") in found,
          str(found))


if __name__ == "__main__":
    review_unit()
    unit()
    mgmt_vlan_unit()
    follow_flows(8, 18700)
    follow_flows(6, 18710)
    router_flows(18720)
    mgmt_vlan_flows(18730)
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
