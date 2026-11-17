"""Tests of changing the network mode (bridge / router / SOHO): the firmware's templates, what
each web adjusts when saving, following the IP, and complete flows with the simulated UI
against the simulator (airOS 8 and 6)."""
import os
import sys
import threading
import types

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("mode")
tr = T.tr

T.PPPOE_WAIT = 5
T.PPPOE_POLL = 0.5
B1 = {"board.netmodes": "7", "board.phycount": "1"}
B2 = {"board.netmodes": "7", "board.phycount": "2"}


def losses():
    print("=== what a new mode loses: what each web rebuilds ===")
    c = cfg_of(6, "router")
    for k, v in (("vlan.status", "enabled"), ("vlan.1.status", "enabled"),
                 ("vlan.1.devname", "eth0"), ("vlan.1.id", "7"), ("upnpd.status", "enabled"),
                 ("dhcprelay.status", "enabled")):
        c.set(k, v)
    v6, v8 = T.network_losses(c, "v6"), T.network_losses(c, "v8")
    check("airOS 6 keeps its VLANs and UPnP (its web does not delete them); both lose the DHCP "
          "relay", tr("DHCP relay") in v6 and "VLAN" not in v6 and "UPnP" not in v6
          and {"VLAN", "UPnP", tr("DHCP relay")} <= set(v8), str((v6, v8)))


def cfg_of(version=8, mode="bridge", **kw):
    d = S.Device(version, "ubnt", "ubnt", 1, 10, mode=mode)
    c = T.Config(S.cfg_text(d.running))
    for k, v in kw.items():
        c.set(k.replace("__", "."), v)
    return c


def unit():
    print("=== templates: the WAN and the LAN as each web sets them ===")
    want = {("v8", True, False): ("eth0", "wired", "br0", "wireless"),
            ("v8", True, True): ("br1", "wired", "br0", "wireless"),
            ("v8", False, False): ("br0", "wireless", "eth0", "wired"),
            ("v8", False, True): ("br0", "wireless", "br1", "wired"),
            ("v6", True, False): ("eth0", "wired", "ath0", "wireless"),
            ("v6", True, True): ("br0", "wired", "ath0", "wireless"),
            ("v6", False, False): ("ath0", "wireless", "eth0", "wired"),
            ("v6", False, True): ("ath0", "wireless", "br0", "wired")}
    for (fl, ap, two), (wdev, wside, ldev, lside) in want.items():
        c = cfg_of(int(fl[1]), radio__1__mode="master" if ap else "managed")
        n = T.switch_netmode(c, fl, "router", B2 if two else B1)
        w, lans = T.main_iface(n, fl), T.lan_ifaces(n)
        got = (w and w["devname"], w and T.iface_side(n, w["devname"]),
               [x["devname"] for x in lans], lans and T.iface_side(n, lans[0]["devname"]))
        check(f"{fl} {'AP' if ap else 'station'}, {2 if two else 1} port(s): WAN {wdev} "
              f"({wside}), LAN {ldev}", got == (wdev, wside, [ldev], lside), str(got))

    for fl in ("v8", "v6"):
        v = int(fl[1])
        r = T.switch_netmode(cfg_of(v), fl, "router", B1)
        w = T.main_iface(r, fl)
        check(f"{fl}: router with the WAN over DHCP and the web's LAN", T.net_mode(r) == "router"
              and T.ip_mode(r, w["devname"]) == "dhcp" and T.lan_ifaces(r)[0]["ip"] == "192.168.1.1"
              and r.get("dhcpc.1.fallback_netmask") == "255.255.255.0")
        check(f"{fl}: NAT as the web sets it ({'on' if fl == 'v8' else 'off'})",
              T.nat_on(r, w) == (fl == "v8"))
        check(f"{fl}: management from the WAN ({'blocked' if fl == 'v6' else 'allowed'})",
              T.mgmt_blocked(r, w) == (fl == "v6")
              and (r.get("ip6tables.sys.mgmt.1.devname") == w["devname"]) == (fl == "v6"))
        check(f"{fl}: no DHCP server on the LAN (as in the template)",
              not (T.dhcp_server(r, T.lan_ifaces(r)[0]["devname"]) or {}).get("on"))
        b = T.switch_netmode(T.switch_netmode(cfg_of(v), fl, "router", B1), fl, "bridge", B1)
        m = T.main_iface(b, fl)
        check(f"{fl}: bridge with the web's IP", m["devname"] == "br0"
              and m["ip"] == "192.168.1.20"
              and T.default_route(b)[1] == "192.168.1.1" and not T.lan_ifaces(b))
        check(f"{fl}: one port: no eth1", not any(i["devname"] == "eth1" for i in T.net_ifaces(b))
              and "eth1" not in [b.get(k) for k in b.keys() if ".port." in k])
        s = T.switch_netmode(cfg_of(v), fl, "soho", B1)
        sw = T.main_iface(s, fl)
        srv = T.dhcp_server(s, "br0")
        check(f"{fl}: SOHO with WAN eth0, LAN br0, DHCP and NAT", sw["devname"] == "eth0"
              and srv and srv["on"] and srv["start"] == "192.168.1.2" and T.nat_on(s, sw))

    print("=== what is deleted and what is kept ===")
    base = cfg_of(8, "router", vlan__1__devname="eth0", vlan__1__id="100",
                  vlan__1__status="enabled",
                  upnpd__status="enabled", system__modules__blacklist__1__expr="ip_.*_sip",
                  netconf__1__mtu="1400", iptables__sys__portfw__status="enabled",
                  iptables__sys__portfw__1__status="enabled",
                  iptables__sys__portfw__1__host="1.2.3.4")
    keep = ("resolv.host.1.name", "sshd.port", "wireless.1.ssid", "users.1.name",
            "radio.1.freq", "system.modules.blacklist.1.expr")
    for fl in ("v8", "v6"):
        n = T.switch_netmode(base, fl, "bridge", B1)
        check(f"{fl}: keeps what is not about the network",
              all(n.get(k) == base.get(k) for k in keep))
        check(f"{fl}: deletes port forwarding, NAT and DHCP", not T._has(n, "iptables.sys.portfw")
              and not T._has(n, "iptables.sys.masq") and not T._has(n, "dhcpd"))
        check(f"{fl}: VLAN and UPnP {'deleted' if fl == 'v8' else 'kept'} (as the web does)",
              (T._has(n, "vlan") and T._has(n, "upnpd")) == (fl == "v6"))
        check(f"{fl}: eth0's MTU {'kept' if fl == 'v6' else 'back to the default'}",
              (n.get("netconf.1.mtu") == "1400") == (fl == "v6"), n.get("netconf.1.mtu"))
    check("the losses it warns about", T.network_losses(base) == [
        "NAT", tr("port forwarding"), "VLAN", tr("DHCP server"), "UPnP"],
        str(T.network_losses(base)))

    print("=== ARP-NAT and EAPOL on the bridge ===")
    sta = cfg_of(8, radio__1__mode="managed", wireless__1__wds__status="disabled")
    n8 = T.switch_netmode(sta, "v8", "bridge", B1)
    check("v8 station without WDS: ARP-NAT on ath0",
          n8.get("ebtables.sys.arpnat.status") == "enabled"
          and n8.get("ebtables.sys.arpnat.1.devname") == "ath0")
    n8w = T.switch_netmode(cfg_of(8, radio__1__mode="managed", wireless__1__wds__status="enabled"),
                           "v8", "bridge", B1)
    check("v8 station with WDS: no ARP-NAT", not T._has(n8w, "ebtables.sys.arpnat"))
    n8r = T.switch_netmode(sta, "v8", "router", B1)
    check("v8 station in router mode: ARP-NAT on ath0 (br0 is the WAN)",
          n8r.get("ebtables.sys.arpnat.1.devname") == "ath0")
    ap8 = cfg_of(8, aaa__1__wpa__mode="2")
    e8 = T.switch_netmode(ap8, "v8", "bridge", B1)
    check("v8 AP with WPA: EAPOL on ath0, no ARP-NAT",
          e8.get("ebtables.sys.eap.1.devname") == "ath0"
          and not T._has(e8, "ebtables.sys.arpnat"))
    check("v8 AP without WPA: nothing", not T._has(T.switch_netmode(
        cfg_of(8, aaa__status="disabled", aaa__1__status="disabled"), "v8", "bridge", B1),
        "ebtables"))
    n6 = T.switch_netmode(cfg_of(6, radio__1__mode="managed"), "v6", "bridge", B1)
    check("v6 station: ARP-NAT enabled", n6.get("ebtables.sys.arpnat.status") == "enabled"
          and n6.get("ebtables.status") == "enabled")
    n6c = T.switch_netmode(cfg_of(6, radio__1__mode="managed", wireless__1__macclone="enabled"),
                           "v6", "bridge", B1)
    check("v6 station with a cloned MAC: no ARP-NAT",
          n6c.get("ebtables.sys.arpnat.status") == "disabled")
    n6a = T.switch_netmode(cfg_of(6), "v6", "bridge", B1)
    check("v6 AP with WPA: EAPOL enabled, ARP-NAT not",
          n6a.get("ebtables.sys.eap.status") == "enabled"
          and n6a.get("ebtables.sys.arpnat.status") == "disabled")
    vl = T.switch_netmode(cfg_of(6, "router", vlan__1__devname="eth0", vlan__1__id="100"), "v6",
                          "bridge", B1)
    check("v6: the VLAN of a bridge port goes to ebtables.sys.vlan",
          vl.get("ebtables.sys.vlan.1.devname") == "eth0"
          and vl.get("ebtables.sys.vlan.1.id") == "100"
          and vl.get("ebtables.sys.vlan.status") == "enabled")
    check("v6 router: route.status enabled", T.switch_netmode(cfg_of(6), "v6", "router", B1)
          .get("route.status") == "enabled")

    print("=== helpers ===")
    orig = cfg_of(8)
    back = T.restore_network(T.switch_netmode(orig, "v8", "router", B1), orig, "v8")
    check("going back to the device's mode leaves its network as it was", orig.diff(back) == [],
          str(orig.diff(back)))
    check("the board's modes",
          T.board_netmodes({"board.netmodes": "7"}) == ["bridge", "router", "soho"]
          and T.board_netmodes({"board.netmodes": "3"}) == ["bridge", "router"]
          and T.board_netmodes({}) == ["bridge"])
    check("the LAN's DHCP range",
          T.lan_range("192.168.1.1", "255.255.255.0") == ("192.168.1.2", "192.168.1.254")
          and T.lan_range("10.0.0.20", "255.255.255.0") == ("10.0.0.21", "10.0.0.254")
          and T.lan_range("10.0.0.254", "255.255.255.0") == ("10.0.0.1", "10.0.0.253"))
    r = T.switch_netmode(cfg_of(8), "v8", "router", B1)
    w = T.main_iface(r, "v8")
    clash = T._merge(r.copy(), T.plan_static(r, w, "192.168.1.5", "255.255.255.0", "192.168.1.254",
                                             "", ""))
    check("WAN and LAN on the same network: an error",
          "error" in [p.level for p in T.net_problems(clash, "v8")])
    off = T._merge(r.copy(), T.plan_nat(r, w, False))
    check("NAT off", not T.nat_on(off, w) and off.get("iptables.sys.masq.status") == "disabled")
    ppp = T._merge(r.copy(), T.plan_pppoe(r, w, {"name": "a", "password": "b", "service": "",
                                                 "fallback": "192.168.10.1",
                                                 "fallback_netmask": "255.255.255.0",
                                                 "mtu": 1492, "mru": 1492, "mppe": False}))
    blk = T._merge(ppp.copy(), T.plan_block_mgmt(ppp, w, True))
    check("with PPPoE, NAT and the blocking go to ppp+", T.nat_on(ppp, w)
          and T.nat_devname(ppp, w) == "ppp+"
          and blk.get("iptables.sys.mgmt.1.devname") == "ppp+"
          and blk.get("ip6tables.sys.mgmt.1.devname") == "ppp+")


def access_unit():
    print("=== where your IP ends up when going from bridge to router or SOHO (no device; the "
          "factory network, 192.168.1.20/24) ===")

    def run(version, mode, script, host=None, mask=None):
        """_netmode_access on the mode's template: (what it leaves, its notes, the UI)."""
        fl = f"v{version}"
        old = cfg_of(version)
        mg = T.main_iface(old, fl)
        if mask:
            old.set(f"netconf.{mg['n']}.netmask", mask)
        app = T.App.__new__(T.App)
        app.ui = FakeUI(script)
        app.dev = types.SimpleNamespace(flavor=fl, host=host or mg["ip"])
        notes = []
        out = app._netmode_access(old, T.switch_netmode(old, fl, mode, B1), mode, notes)
        check(f"  ({fl} {mode}: the script answered all it asked, and it asked nothing more)",
              not app.ui.script, str(app.ui.script))
        return out, notes

    for v in (8, 6):
        fl = f"v{v}"
        for mode in ("router", "soho"):
            out, notes = run(v, mode, [("menu", "wired")])
            wan, lan = T.main_iface(out, fl), T.lan_ifaces(out)[0]
            srv = T.dhcp_server(out, lan["devname"]) or {}
            check(f"{fl} {mode}, over the cable: 192.168.1.20 to the WAN, and its template's LAN "
                  "(192.168.1.1/24, the same network) moves to 192.168.2.1/24, with a note",
                  (wan["devname"], T.ip_mode(out, "eth0"), wan["ip"], wan["netmask"])
                  == ("eth0", "static", "192.168.1.20", "255.255.255.0")
                  and lan["ip"] == "192.168.2.1"
                  and any("192.168.2.1/24" in n for n in notes), str((wan, lan, notes)))
            if v == 6:
                check("  (airOS 6: management from the WAN stays allowed, with a note)",
                      not T.mgmt_blocked(out, wan)
                      and any(tr("Management from the WAN stays allowed (the web blocks it when "
                                 "changing mode), because you are connected through it.") in n
                              for n in notes), str(notes))
            if mode == "soho":
                check("  with SOHO's DHCP server, handing out addresses on the new network",
                      srv.get("on") and (srv["start"], srv["end"])
                      == ("192.168.2.2", "192.168.2.254"), str(srv))
            out, notes = run(v, mode, [("menu", "wireless")])
            lan = T.lan_ifaces(out)[0]
            srv = T.dhcp_server(out, lan["devname"]) or {}
            check(f"{fl} {mode}, over the link: 192.168.1.20 to the LAN, and the WAN over DHCP, "
                  "as in its template", lan["ip"] == "192.168.1.20"
                  and T.ip_mode(out, T.main_iface(out, fl)["devname"]) == "dhcp" and not notes,
                  str((lan, notes)))
            if mode == "soho":
                check("  and its DHCP hands out from the next IP on (not its own)", srv.get("on")
                      and (srv["start"], srv["end"]) == ("192.168.1.21", "192.168.1.254"),
                      str(srv))
    out, notes = run(8, "router", [("menu", "wired")], mask="255.255.0.0")
    lan = T.lan_ifaces(out)[0]
    check("with a /16 netmask the LAN moves to the first free network outside it "
          "(10.10.10.1/24)",
          T.main_iface(out, "v8")["netmask"] == "255.255.0.0" and lan["ip"] == "10.10.10.1",
          str((lan, notes)))
    template = T.switch_netmode(cfg_of(8), "v8", "router", B1)
    out, notes = run(8, "router", [("menu", "default")])
    check('"Use the web\'s defaults": its template as it is', out is not None
          and out.diff(template) == [] and not notes)
    out, notes = run(8, "router", [], host="10.9.9.9")
    check("if you are not connected through a fixed IP of the antenna: its template, with a "
          "note, without asking",
          out is not None and out.diff(template) == [] and len(notes) == 1
          and tr("You are not connected through a fixed IP of the antenna: the web's defaults "
                 "stay.") in notes[0], str(notes))
    out, notes = run(8, "router", [("menu", None)])
    check("cancelling the question: the mode does not change", out is None)


def start(version, port, ip, dport, station=False, extra=None):
    srv = S.make_server(version, port, restart=0.6, test_secs=60, mgmt_ip=ip,
                        discovery_port=dport, station=station)
    for k, v in (extra or {}).items():       # the configuration the device starts with
        for c in (srv.dev.saved, srv.dev.system, srv.dev.running):
            c[k] = v
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://{ip}:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.discovery = {"target": "127.0.0.1", "port": dport}
    app.load()
    return srv, srv.dev, dev, ui, app


def pppoe_wizard(user="customer", pw="secret"):
    return [("menu", "2"), ("menu", "3"), ("input", user), ("password", pw), ("input", ""),
            ("input", "1492"), ("input", "1492"), ("yesno", 1), ("input", "192.168.10.1"),
            ("input", "24")]


def station_flow(version, port):
    v = f"v{version}"
    ip = "127.0.0.40" if version == 8 else "127.0.0.44"
    print(f"=== {v} station (CPE): bridge → router with PPPoE, revert, keep, go back ===")
    # a station without WDS (a bridge with ARP-NAT)
    srv, d, dev, ui, app = start(version, port, ip, port + 1, station=True,
                                 extra={"wireless.1.wds.status": "disabled"})
    ui.script = [("menu", "m"), ("menu", "router"), ("yesno", 0), ("menu", "wired"),
                 ("menu", None)]
    app.network_menu()
    menus = {t: dict(items) for t, items, _ in ui.menus}
    sides = menus.get(tr("Network mode"), {})
    check(f"{v}: offers to leave the IP on the cable's side (the LAN)",
          sides.get("wired", "").endswith(tr("{ip} moves to the LAN ({dev})", ip=ip, dev="eth0"))
          if version == 8 else tr("{ip} moves to the LAN ({dev})", ip=ip, dev="br0")
          in sides.get("wired", ""), str(sides))
    summary = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Network mode")]
    # (the start of its WAN and LAN lines: each with the side it is reached through)
    check(f"{v}: summary with a wireless WAN and a wired LAN", summary
          and tr("{name}: {dev}, through {side}: {address}", name="WAN",
                 dev="br0" if version == 8 else "ath0", side=tr("the wireless link"),
                 address="") in summary[0]
          and tr("LAN: {dev}, through {side}: {address}", dev="eth0" if version == 8 else "br0",
                 side=tr("the cable (Ethernet)"), address=f"{ip}/24") in summary[0],
          (summary or [""])[0])
    w = app.working
    wan = T.main_iface(w, app.dev.flavor)
    check(f"{v}: prepared", T.net_mode(w) == "router" and T.lan_ifaces(w)[0]["ip"] == ip
          and T.iface_side(w, wan["devname"]) == "wireless")
    network = [dict(items) for t, items, _ in ui.menus if t == tr("Network")][-1]
    check(f"{v}: the menu marks the mode and offers NAT and management",
          network["m"] == "* " + tr("Network mode: {mode}", mode=tr("Router"))
          and "n" in network and "b" in network, str(network))
    ui.log.clear()
    nat = [] if version == 8 else [("menu", "n"), ("yesno", 0)]   # airOS 6: NAT off
    ui.script = nat + pppoe_wizard() + [("menu", None)]
    app.network_menu()
    check(f"{v}: WAN over PPPoE", T.ip_mode(app.working, wan["devname"]) == "pppoe")

    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 1)]           # test and revert
    app.apply(test=True)
    confirm = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test changes")]
    check(f"{v}: the confirmation warns of the mode change",
          confirm and tr("⚠ The network mode changes: {old} → {new}. The antenna's whole network "
                         "is rebuilt.", old=tr("Bridge"), new=tr("Router")) in confirm[0],
          (confirm or [""])[0][:500])
    check(f"{v}: reverted: back to bridge at {ip}", d.running.get("netmode") == "bridge"
          and dev.host == ip and tr("Reverted") in ui.titles(), f"{dev.host} {ui.titles()}")

    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]           # test and keep
    app.apply(test=True)
    decide = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test mode")]
    s = d.saved
    check(f"{v}: PPPoE connected before deciding", decide
          and tr("PPPoE: connected, IP {ip}.").partition("{")[0] in decide[0],
          (decide or [""])[0][:600])
    check(f"{v}: saved in router mode with PPPoE and NAT", s.get("netmode") == "router"
          and s.get("ppp.1.name") == "customer" and T.nat_on(T.Config(S.cfg_text(s)), wan)
          and tr("Done") in ui.titles(), str(ui.titles()))
    check(f"{v}: still connected through the LAN", dev.host == ip)
    check(f"{v}: ARP-NAT {'on ath0' if version == 8 else 'does not apply'} (a station without "
          "WDS)", (s.get("ebtables.sys.arpnat.1.devname") == "ath0") == (version == 8))

    print(f"--- {v}: router → bridge")
    new_ip = "127.0.0.41" if version == 8 else "127.0.0.45"
    ui.log.clear()
    gw = [("input", "")] if version == 8 else [("input", "127.0.0.1")]
    ui.script = ([("menu", "m"), ("menu", "bridge"), ("yesno", 0), ("menu", "1"),
                  ("input", new_ip), ("input", "24")] + gw + [("input", ""), ("input", "")])
    if version == 8:
        ui.script.append(("yesno", 0))                # warning: no gateway
    ui.script.append(("menu", None))
    app.network_menu()
    mg = T.main_iface(app.working, app.dev.flavor)
    check(f"{v}: bridge prepared with the chosen IP", T.net_mode(app.working) == "bridge"
          and mg["ip"] == new_ip and not T._has(app.working, "ppp"), str(ui.titles()))
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    s = d.saved
    check(f"{v}: saved in bridge mode and connected to {new_ip}", s.get("netmode") == "bridge"
          and dev.host == new_ip and tr("Done") in ui.titles(), f"{dev.host} {ui.titles()}")
    check(f"{v}: a station's ARP-NAT on the bridge",
          (s.get("ebtables.sys.arpnat.1.devname") == "ath0") if version == 8
          else s.get("ebtables.sys.arpnat.status") == "enabled")
    srv.cluster.shutdown()


def ap_flow(port):
    print("=== v6 two-port AP: the IP stays on the WAN (cable) ===")
    srv, d, dev, ui, app = start(6, port, "127.0.0.50", port + 1)
    # the gateway 192.168.1.1 was already outside the management network: not a new warning
    ui.script = [("menu", "m"), ("menu", "router"), ("yesno", 0), ("menu", "wired"),
                 ("menu", None)]
    app.network_menu()
    w = app.working
    wan = T.main_iface(w, "v6")
    summary = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Network mode")]
    check("WAN br0 (eth0+eth1) with the same IP as before", wan["devname"] == "br0"
          and wan["ip"] == "127.0.0.50"
          and T.ip_mode(w, "br0") == "static", str(wan))
    check("management from the WAN allowed (you are connected through it), with a note",
          not T.mgmt_blocked(w, wan) and summary
          and tr("Management from the WAN stays allowed (the web blocks it when changing mode), "
                 "because you are connected through it.") in summary[0])
    check("NAT off, as in airOS 6's template", not T.nat_on(w, wan))
    ui.log.clear()
    ui.script = [("menu", "n"), ("yesno", 0), ("menu", "l1"), ("input", "127.0.1.51"),
                 ("input", "24"), ("yesno", 1), ("menu", "b"), ("yesno", 1), ("menu", None)]
    app.network_menu()
    blk = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Management from the WAN")]
    check("blocking the management warns that you would lose access", blk
          and tr("⚠ You are connected through the WAN now ({host}): you would lose access.",
                 host="127.0.0.50") in blk[0])
    check("NAT enabled and the LAN moved", T.nat_on(app.working, wan)
          and T.lan_ifaces(app.working)[0]["ip"] == "127.0.1.51")
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    s = d.saved
    check("saved in router mode and connected", s.get("netmode") == "router"
          and tr("Done") in ui.titles()
          and dev.host in ("127.0.0.50", "127.0.1.51"), f"{dev.host} {ui.titles()}")
    check("on the device: a static WAN, NAT on br0, the management not blocked",
          s.get("iptables.sys.masq.1.devname") == "br0"
          and s.get("iptables.sys.mgmt.1.status") == "disabled", str({k: s.get(k) for k in s
                                                                      if "iptables" in k}))
    ui.log.clear()
    ui.script = [("menu", "m"), ("menu", "bridge"), ("yesno", 0), ("menu", None), ("menu", None)]
    app.network_menu()
    ask = [t for k, ti, t in ui.log if k == "menu" and ti == tr("Management IP (bridge mode)")]
    # (how it would describe the WAN's static IP, whatever its prefix and gateway)
    wan_ip = tr("static {ip}/{prefix}, gateway {gateway}").partition("/")[0].format(
        ip="127.0.0.50")
    check("to bridge with a static WAN: it proposes its template's static IP (192.168.1.20), as "
          "its web's form does, not the WAN's", ask
          and tr("static {ip}/{prefix}, gateway {gateway}", ip="192.168.1.20", prefix=24,
                 gateway="192.168.1.1") in ask[0]
          and wan_ip not in ask[0], (ask or [""])[0])
    check("cancelling there leaves no changes", app.changes() == [], str(app.changes()[:3]))
    srv.cluster.shutdown()


def misc_flows(port):
    print("=== going back, a board with a single mode, no board information ===")
    srv, d, dev, ui, app = start(8, port, "127.0.0.60", port + 1)
    ui.script = [("menu", "m"), ("menu", "router"), ("yesno", 0), ("menu", "default"),
                 ("menu", "m"), ("menu", "bridge"), ("menu", None)]
    app.network_menu()
    check("router and then bridge again: no pending changes", app.changes() == []
          and tr("The network the device has comes back ({mode}), just as it was.",
                 mode=tr("Bridge")) in " ".join(t for k, ti, t in ui.log if k == "msgbox"),
          str(app.changes()[:3]))
    ui.log.clear()
    ui.script = [("menu", "m"), ("menu", "router"), ("yesno", 1), ("menu", None)]
    app.network_menu()
    check("cancelling the confirmation changes nothing", app.changes() == [])
    app.limits.board["board.netmodes"] = "1"
    ui.log.clear()
    ui.script = [("menu", "m"), ("menu", None)]
    app.network_menu()
    check("a bridge-only board: it says so", tr(
        "This device only supports the {mode} mode (according to the board's board.netmodes).",
        mode=tr("Bridge")) in " ".join(t for k, ti, t in ui.log if k == "msgbox"))
    app.limits = None
    ui.log.clear()
    ui.script = [("menu", "m"), ("menu", None)]
    app.network_menu()
    check("no board information: it does not change the mode", tr(
        "I could not read the board information (which modes it supports and how many ports it "
        "has) and without it I do not change the network mode. Read the configuration again or "
        "use the web.") in " ".join(t for k, ti, t in ui.log if k == "msgbox"))
    plan = T.App(FakeUI(), T.AirOS("http://127.0.0.60", "u", "p"))
    plan.original = T.Config(S.cfg_text(d.running))
    plan.working = T.switch_netmode(plan.original, "v8", "router", B1)
    p = plan._follow_plan()
    check("following: it looks at the new mode's IPs", p and p["mode"] == "netmode"
          and p["candidates"] == ["192.168.10.1", "192.168.1.1"], str(p))
    srv.cluster.shutdown()


def review_mgmt_vlan():
    print("=== the review: the management VLAN when its interface had no role ===")
    real = T.Config(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                                      "real", "airos8.7.14-nanostation5ac-sta.cfg"),
                         encoding="utf-8").read())
    real.delete("netconf.3.role")
    after = T._merge(real.copy(), T.plan_mgmt_vlan(real, 10))
    ifc = T.main_iface(after, "v8")
    check("br1 takes its address and its role (as its web saves it): no errors",
          ifc and ifc["devname"] == "br1" and not ifc.get("guessed") and not [
              p for p in T.check_config(after, None) if p.level == "error"], str(ifc))

    class Offline:
        flavor, host, base, username, notes, backup_loaded = (
            "v8", "192.0.2.1", "https://192.0.2.1", "ubnt", [], False)
    router = cfg_of(8, "router")
    router.set("iptables.sys.portfw.status", "enabled")
    router.set("iptables.sys.portfw.1.status", "enabled")
    ui = FakeUI()
    app = T.App(ui, Offline())
    app.set_loaded(router.text())
    app.limits = T.DeviceLimits("v8", dict(S.BOARD[8]), None)
    ui.script = [("menu", "m"), ("menu", "bridge"), ("yesno", 1), ("menu", None)]
    app.network_menu()
    asked = [t for k, ti, t in ui.log if k == "yesno"]
    check("when changing the mode, what is lost, translated", asked
          and tr("Lost: {items}.").partition("{")[0] in asked[0]
          and tr("port forwarding") in asked[0] and "port forwarding" not in asked[0],
          str(asked[:1]))
    print("=== to bridge with the WAN over DHCP: as its web does ===")
    ui = FakeUI()
    app = T.App(ui, Offline())
    app.set_loaded(cfg_of(8, "router").text())
    app.limits = T.DeviceLimits("v8", dict(S.BOARD[8]), lambda cc, bw: None)   # (no channels)
    wan = T.main_iface(app.working, "v8")
    ui.script = [("menu", "m"), ("menu", "bridge"), ("yesno", 0), ("menu", "2"),
                 ("input", "192.168.1.20"), ("input", "255.255.255.0"), ("menu", None)]
    try:
        app.network_menu()
    except AssertionError as e:
        print("     script:", e)
    form = [t for k, ti, t in ui.log if k == "menu" and ti == tr("Management IP (bridge mode)")]
    mg = T.main_iface(app.working, "v8")
    check("it proposes its template's static IP (192.168.1.20), as its web's form does, and "
          "lets you choose DHCP", T.ip_mode(app.original, wan["devname"]) == "dhcp" and form
          and tr("static {ip}/{prefix}, gateway {gateway}", ip="192.168.1.20", prefix=24,
                 gateway="192.168.1.1") in form[0] and not ui.script and mg
          and T.net_mode(app.working) == "bridge" and T.ip_mode(app.working, mg["devname"])
          == "dhcp", str((form[:1], mg)))


if __name__ == "__main__":
    review_mgmt_vlan()
    unit()
    losses()
    access_unit()
    station_flow(8, 18970)
    station_flow(6, 18974)
    ap_flow(18978)
    misc_flows(18982)
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
