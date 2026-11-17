"""Tests of PPPoE on the WAN: keys, validations, status and reconnection, following the IP and
complete flows with the simulated UI against the simulator (airOS 8 and 6)."""
import sys
import threading
import time
import types

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("ppp")
tr = T.tr

T.PPPOE_WAIT = 5               # so that waiting for pppd does not make the tests longer
T.PPPOE_POLL = 0.5


def cfg_of(mode="router", version=8, **kw):
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


def levels(cfg, flavor="v8"):
    return sorted((p.level, p.key) for p in T.net_problems(cfg, flavor))


GOOD = {"name": "customer", "password": "secret", "service": "", "fallback": "192.168.10.1",
        "fallback_netmask": "255.255.255.0", "mtu": 1492, "mru": 1492, "mppe": False}


def unit():
    print("=== PPPoE keys (those of the web's setIpMode/setPppoe) ===")
    r = cfg_of()
    wan = T.main_iface(r)
    p = applied(r, T.plan_pppoe(r, wan, GOOD))
    want = {"ppp.status": "enabled", "ppp.1.status": "enabled", "ppp.1.devname": "eth0",
            "ppp.1.name": "customer", "ppp.1.password": "secret", "ppp.1.pppoe_service": "",
            "ppp.1.fallback": "192.168.10.1", "ppp.1.fallback_netmask": "255.255.255.0",
            "ppp.1.mtu": "1492", "ppp.1.mru": "1492", "ppp.1.require.mppe128": "disabled"}
    check("PPPoE mode", T.ip_mode(p, "eth0") == "pppoe")
    check("the whole ppp.1 entry", all(p.get(k) == v for k, v in want.items()),
          str({k: p.get(k) for k in want}))
    check("the WAN's DHCP off", p.get("dhcpc.1.status") == "disabled"
          and p.get("dhcpc.status") == "disabled")
    check("the WAN without an IP of its own (0.0.0.0/32)", p.get("netconf.1.ip") == "0.0.0.0"
          and p.get("netconf.1.netmask") == "255.255.255.255")
    check("firewall and MSS clamping on", p.get("iptables.status") == "enabled"
          and p.get("iptables.sys.status") == "enabled"
          and p.get("iptables.sys.tcpmss.status") == "enabled")
    check("NAT moves to ppp+", p.get("iptables.sys.masq.1.devname") == "ppp+")
    check("description",
          T.describe_ip(p, T.main_iface(p)) == tr("PPPoE (user {name})", name="customer"))
    check("no problems", levels(p) == [], str(levels(p)))
    check("without IPv6 on the WAN, ppp.1.ipv6 is not touched", "ppp.1.ipv6.status" not in p)

    s = applied(r, T.plan_static(r, wan, "10.0.0.5", "255.255.255.0", "10.0.0.1", "1.1.1.1",
                                 "8.8.4.4"))
    sp = applied(s, T.plan_pppoe(s, T.main_iface(s), GOOD))
    check("static -> PPPoE turns off the route and the fixed DNS",
          T.default_route(sp)[1] == "" and T.dns_servers(sp) == ["", ""])
    sd = applied(s, T.plan_dhcp(s, T.main_iface(s), "192.168.10.1", "255.255.255.0"))
    check("static -> DHCP turns them off too (a fix)",
          T.default_route(sd)[1] == "" and T.dns_servers(sd) == ["", ""])

    ps = applied(p, T.plan_static(p, T.main_iface(p), "10.0.0.5", "255.255.255.0", "10.0.0.1",
                                  "1.1.1.1", ""))
    check("PPPoE -> static turns off ppp and the MSS", ps.get("ppp.1.status") == "disabled"
          and ps.get("ppp.status") == "disabled"
          and ps.get("iptables.sys.tcpmss.status") == "disabled")
    check("PPPoE -> static: NAT goes back to eth0",
          ps.get("iptables.sys.masq.1.devname") == "eth0")
    check("PPPoE -> static: mode, IP and netmask", T.ip_mode(ps, "eth0") == "static"
          and ps.get("netconf.1.ip") == "10.0.0.5"
          and ps.get("netconf.1.netmask") == "255.255.255.0")
    check("the PPPoE data is kept (as the web does)", ps.get("ppp.1.name") == "customer")
    pd = applied(p, T.plan_dhcp(p, T.main_iface(p), "192.168.10.1", "255.255.255.0"))
    check("PPPoE -> DHCP", T.ip_mode(pd, "eth0") == "dhcp" and pd.get("ppp.status") == "disabled"
          and pd.get("iptables.sys.masq.1.devname") == "eth0"
          and pd.get("iptables.sys.tcpmss.status") == "disabled")

    edit = applied(p, T.plan_pppoe(p, T.main_iface(p), dict(GOOD, password="other", mtu=1480)))
    check("editing PPPoE only changes its data",
          {k for k, _, _ in p.diff(edit)} == {"ppp.1.password", "ppp.1.mtu"},
          str(p.diff(edit)))
    old = cfg_of(ppp__1__devname="ath0", ppp__1__name="old", ppp__1__status="disabled")
    po = applied(old, T.plan_pppoe(old, T.main_iface(old), GOOD))
    check("reuses the first ppp entry (as the web does)",
          T._indices(po, "ppp") == [1] and po.get("ppp.1.devname") == "eth0")
    six = cfg_of(netconf__1__ip6__status="enabled")
    p6 = applied(six, T.plan_pppoe(six, T.main_iface(six), GOOD))
    check("WAN with IPv6 -> ppp.1.ipv6 enabled", p6.get("ppp.1.ipv6.status") == "enabled")

    print("=== validations ===")
    check("no user -> warning",
          levels(applied(p, {"ppp.1.name": ""})) == [("warning", "ppp.1.name")])
    check("MTU 1518 is valid on airOS 8", levels(applied(p, {"ppp.1.mtu": "1518"})) == [])
    check("MTU 1519 -> error on airOS 8",
          levels(applied(p, {"ppp.1.mtu": "1519"})) == [("error", "ppp.1.mtu")])
    check("MTU 63 -> error", levels(applied(p, {"ppp.1.mtu": "63"})) == [("error", "ppp.1.mtu")])
    check("a non-numeric MRU -> error",
          levels(applied(p, {"ppp.1.mru": "abc"})) == [("error", "ppp.1.mru")])
    check("airOS 6: the limit = the WAN's MTU − 8",
          levels(applied(p, {"ppp.1.mtu": "1493"}), "v6") == [("error", "ppp.1.mtu")]
          and levels(applied(p, {"ppp.1.mtu": "1492"}), "v6") == [])
    check("airOS 6: a WAN with MTU 1600 takes 1592",
          levels(applied(p, {"netconf.1.mtu": "1600", "ppp.1.mtu": "1592"}), "v6") == [])
    noneg = applied(p, {"ppp.1.mru_negotiation": "disabled", "ppp.1.mru": "1500"})
    check("airOS 6: MRU without negotiation, up to 1500", levels(noneg, "v6") == []
          and levels(applied(noneg, {"ppp.1.mru": "1501"}), "v6") == [("error", "ppp.1.mru")])
    w = T.main_iface(p)
    check("limits per version", T.pppoe_limits(p, w, "v8") == (1518, 1518)
          and T.pppoe_limits(p, w, "v6") == (1492, 1492)
          and T.pppoe_limits(p, w, None) == (1518, 1518))
    check("a PPPoE fallback IP in the LAN's network -> warning",
          levels(applied(p, {"ppp.1.fallback": "192.168.1.50"})) == [("warning", "ppp.1.fallback")])
    check("a DHCP fallback IP in the LAN's network -> warning",
          levels(applied(r, {"dhcpc.1.fallback": "192.168.1.50"})) == [("warning",
                                                                        "dhcpc.1.fallback")])
    check("in bridge mode there is no LAN to clash with",
          levels(cfg_of("bridge", dhcpc__status="enabled", dhcpc__1__status="enabled")) == [])
    check("the fallback IP's format", T.validate_value("ppp.1.fallback", "1.2.3") is not None
          and T.validate_value("ppp.1.fallback_netmask", "255.0.255.0") is not None
          and T.validate_value("ppp.1.fallback", "10.0.0.1") is None)

    print("=== the session's status ===")
    ok8 = T.parse_pppoe_v8({"interfaces": [dict(S.PPPOE_SESSION, duration=93784)]})
    check("airOS 8: connected", ok8["iplocal"] == "100.64.10.20" and ok8["dns2"] == "100.64.0.54"
          and ok8["duration"] == "1 d 02:03:04", str(ok8))
    check("airOS 8: errors", T.parse_pppoe_v8({"error": "Not connected"}) == {"error": "Not "
                                                                                       "connected"}
          and T.parse_pppoe_v8({"interfaces": []}) == {"error": "Not connected"}
          and T.parse_pppoe_v8([]) == {"error": tr("Invalid response.")})
    d = S.Device(6, "ubnt", "ubnt", 1, 10, mode="router")
    h = S.Handler.__new__(S.Handler)
    h.server = types.SimpleNamespace(dev=d)

    def page():
        return h._pppinfo_page({"token": "t"})
    check("airOS 6: not enabled", T.parse_pppoe_v6(page()) == {"error": "PPPoE is not enabled."})
    c = T.Config(S.cfg_text(d.running))
    d.running = S.parse_cfg(applied(c, T.plan_pppoe(c, T.main_iface(c), GOOD)).text())
    d.ppp_up_at = time.time() - 3700
    english = T.parse_pppoe_v6(page())
    check("airOS 6: connected",
          {k: english.get(k) for k in ("name", "iplocal", "ipremote", "dns1", "dns2")}
          == {k: S.PPPOE_SESSION[k] for k in ("name", "iplocal", "ipremote", "dns1", "dns2")}
          and english["duration"].startswith("01:01:"), str(english))
    d.ui_lang = "es"
    spanish = T.parse_pppoe_v6(page())
    check("airOS 6: labels in Spanish (it reads by position)", spanish == english, str(spanish))
    saved = dict(S.PPPOE_SESSION)
    S.PPPOE_SESSION["dns2"] = ""
    try:
        no2 = T.parse_pppoe_v6(page())
    finally:
        S.PPPOE_SESSION.update(saved)
    check("airOS 6: no secondary DNS", no2["dns2"] == "" and no2["duration"].startswith("01:01:"),
          str(no2))
    d.running["ppp.1.password"] = "wrong"
    check("airOS 6: Not Connected", T.parse_pppoe_v6(page()) == {"error": "Not Connected"})
    check("texts", tr("No connection: the device could not open the PPPoE session.\n\nCheck the "
                      "user, the password and the service name, and that the WAN is connected to "
                      "the modem or to the provider's network.")
          in T.format_pppoe({"error": "Not Connected"})
          and tr("PPPoE is not enabled on the device. If you have just set it up, apply the "
                 "changes first.") in T.format_pppoe({"error": "PPPoE is not enabled."})
          and tr("⚠ PPPoE: NOT CONNECTED. Check the user, password and service before keeping "
                 "the changes.") in T.pppoe_line({"error": "Not connected"})
          and T.pppoe_line(ok8) == tr("PPPoE: connected, IP {ip}.", ip="100.64.10.20")
          and tr("Could not query the PPPoE status:\n{error}", error="x")
          in T.format_pppoe({"error": "x", "local": True})
          and "secret" not in T.format_pppoe(ok8))


def roles_unit():
    print("=== a WAN without a role in the configuration (old firmwares) ===")
    r = cfg_of()
    r.delete("netconf.1.role")
    check("without the version: eth0, as airOS 6", (T.main_iface(r) or {}).get("devname") == "eth0")
    ap = T.main_iface(r, "v8")
    check("airOS 8 access point: eth0", (ap or {}).get("devname") == "eth0"
          and ap.get("guessed"))
    sta = applied(r, {"radio.1.mode": "managed"})
    sta.delete("netconf.3.role")        # in the simulator br0 is the LAN; here, without a role
    check("airOS 8 station: br0 (_checkNetconfRoles)",
          (T.main_iface(sta, "v8") or {}).get("devname") == "br0")
    check("airOS 6 station: eth0", (T.main_iface(sta, "v6") or {}).get("devname") == "eth0")
    check("saving sets the role", T._role_fix(ap, "wan") == {"netconf.1.role": "wan"}
          and T._role_fix(T.main_iface(cfg_of(), "v8"), "wan") == {})
    taken = applied(r, {"netconf.1.role": "lan"})
    check("it does not take an interface with another role as the WAN",
          T.main_iface(taken, "v8") is None)


def bridge_texts(port):
    print("=== bridge mode: it offers to change the mode, without the old explanation ===")
    srv = S.make_server(8, port, restart=0.5, mgmt_ip="127.0.0.64")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.64:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    ui.script = [("menu", "0")]
    app.run()
    main = [items for t, items, _ in ui.menus if t != tr("Network")][0]
    label = dict(main).get("3", "")
    # (here and below, "there is no WAN" in Spanish, on purpose: a piece of the explanation it
    # no longer shows; no msgid has it now, so tr() could not give it)
    check("main menu: it names the mode, without the explanation",
          tr("Network: mode (bridge/router), management IP and VLAN, DNS") in label
          and "no hay WAN" not in label, label)
    ui.log.clear()
    ui.menus.clear()
    ui.script = [("menu", "2"), ("menu", None), ("menu", None)]
    app.network_menu()
    texts = {ti: t for k, ti, t in ui.log if k == "menu"}
    network = dict(ui.menus[0][1])
    check('"Network" menu: an option to change the mode, and without the explanation',
          network.get("m", "").startswith(tr("Network mode: {mode}", mode=tr("Bridge")))
          and "no hay WAN" not in texts[tr("Network")],        # (the Spanish, as above)
          str(network))
    check('"Management IP": without the PPPoE explanation',
          "PPPoE" not in texts.get(tr("Management IP"), ""),
          str(texts))
    srv.cluster.shutdown()
    srv = S.make_server(8, port + 2, restart=0.5, mode="router", mgmt_ip="127.0.0.66")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.66:{port + 2}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI([("menu", "0")])
    T.App(ui, dev, wait=1).run()
    label = dict(ui.menus[0][1]).get("3", "")
    check("main menu in router mode: it names the WAN and PPPoE",
          "WAN" in label and "PPPoE" in label, label)
    srv.cluster.shutdown()


def roleless_flow(port):
    print("=== airOS 8 in router mode without role=wan: the WAN shows up and is saved with its "
          "role ===")
    srv = S.make_server(8, port, restart=0.5, test_secs=60, mode="router", mgmt_ip="127.0.0.68")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    for c in (d.saved, d.system, d.running):
        c.pop("netconf.1.role", None)
    dev = T.AirOS(f"http://127.0.0.68:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    ui.script = wizard("customer", "secret") + [("menu", None)]
    app.network_menu()
    network = [items for t, items, _ in ui.menus if t == tr("Network")][0]
    check("the WAN shows up even without the role",
          any(k == "2" and label.startswith("WAN (eth0)") for k, label in network), str(network))
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    check("saved with role=wan and PPPoE", d.saved.get("netconf.1.role") == "wan"
          and d.saved.get("ppp.1.name") == "customer" and tr("Done") in ui.titles(),
          str(ui.titles()))
    srv.cluster.shutdown()


def follow_unit():
    print("=== if you are connected through the WAN ===")
    r = cfg_of()
    st = applied(r, T.plan_static(r, T.main_iface(r), "10.0.0.5", "255.255.255.0", "10.0.0.1",
                                  "1.1.1.1", ""))
    app = T.App(FakeUI(), T.AirOS("http://10.0.0.5", "u", "p"))
    app.original = st
    app.working = applied(st, T.plan_pppoe(st, T.main_iface(st), GOOD))
    plan = app._follow_plan()
    check("static WAN -> PPPoE: look for it by MAC and at the fallback IP", bool(plan)
          and plan["new"] is None and plan["mode"] == "pppoe"
          and plan["candidates"] == ["192.168.10.1"], str(plan))
    note = app._ip_change_note()
    check("an honest note", tr(
        "You are now connected through {old}, the WAN IP. With PPPoE the WAN will no longer have "
        "that IP: it will have the one your provider gives it, and you will most likely lose "
        "access from here. The program will look for the antenna by its MAC (Ubiquiti "
        "discovery) and at the fallback IP {fallback}, the one it takes if PPPoE does not "
        "connect. If it does not find it, test mode takes it back to the previous configuration "
        "by itself.", old="10.0.0.5", fallback="192.168.10.1") in note
          and "192.168.10.1" in note, note)
    target = app._follow_target(plan)
    check("the confirmation's text", "PPPoE" in target and "192.168.10.1" in target, target)
    app.working = applied(st, T.plan_dhcp(st, T.main_iface(st), "192.168.10.9", "255.255.255.0"))
    plan = app._follow_plan()
    check("static WAN -> DHCP tries the fallback IP too",
          bool(plan) and plan["candidates"] == ["192.168.10.9"], str(plan))
    pp = applied(r, T.plan_pppoe(r, T.main_iface(r), GOOD))
    app = T.App(FakeUI(), T.AirOS("http://100.64.10.20", "u", "p"))
    app.status = {"interfaces": [{"ifname": "ppp0", "status": {"ipaddr": "100.64.10.20"}}]}
    app.original = pp
    app.working = applied(pp, T.plan_static(pp, T.main_iface(pp), "10.0.0.5", "255.255.255.0",
                                            "10.0.0.1", "", ""))
    check("through the PPPoE session's IP -> static: it follows the new one",
          (app._follow_plan() or {}).get("new") == "10.0.0.5", str(app._follow_plan()))
    app.working = applied(pp, T.plan_pppoe(pp, T.main_iface(pp), dict(GOOD, password="other")))
    check("only the password changes: no new IP to follow", app._follow_plan() is None)


def identity(port):
    print("=== looking for the antenna: it has to be the same one (MAC) ===")
    srv = S.make_server(8, port, restart=0.5, mgmt_ip="127.0.0.60")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.60:{port}", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=1)
    app.discovery = {"target": "127.0.0.1", "port": port + 1}     # nobody answers there
    app.load()
    plan = {"old": "127.0.0.62", "new": None, "mode": "pppoe", "macs": app._device_macs(),
            "candidates": ["127.0.0.60"]}
    dev.retarget("127.0.0.61")
    check("it finds it at the fallback IP", app._wait_dhcp("t", 0, plan, max_wait=10)
          and dev.host == "127.0.0.60", dev.host)
    dev.retarget("127.0.0.61")
    other = dict(plan, macs={"001122334455"})
    check("another antenna with that IP: it does not follow it",
          not app._wait_dhcp("t", 0, other, max_wait=4) and dev.host == "127.0.0.61", dev.host)
    srv.cluster.shutdown()


def wizard(user, pw, service="", mtu="1492", mru="1492", mppe=1, fb="192.168.10.1", mask="24"):
    return [("menu", "2"), ("menu", "3"), ("input", user), ("password", pw), ("input", service),
            ("input", mtu), ("input", mru), ("yesno", mppe), ("input", fb), ("input", mask)]


def flows(version, port, mgmt):
    v = f"v{version}"
    print(f"=== complete flow · airOS {version} (router, connected through the LAN) ===")
    srv = S.make_server(version, port, restart=0.6, test_secs=60, mode="router", mgmt_ip=mgmt)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    dev = T.AirOS(f"http://{mgmt}:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()

    # 1) the WAN offers PPPoE; without PPPoE on the device there is no status or reconnection
    ui.script = [("menu", "2"), ("menu", None), ("menu", None)]
    app.network_menu()
    wan = [(items, default) for t, items, default in ui.menus if t.startswith("WAN")]
    check(f"{v}: the WAN offers static, DHCP and PPPoE",
          wan and [k for k, _ in wan[0][0]] == ["1", "2", "3"] and wan[0][1] == "2", str(wan))
    network = [items for t, items, _ in ui.menus if t == tr("Network")]
    check(f"{v}: without PPPoE on, there is no status or reconnection",
          network and not {"p", "c"} & {k for k, _ in network[0]})

    # 2) the wizard with the right data
    ui.log.clear()
    ui.script = wizard("customer", "secret") + [("menu", None)]
    app.network_menu()
    prepared = [t for k, ti, t in ui.log if k == "msgbox" and ti.startswith("WAN")]
    check(f"{v}: prepared without moving the IP you are connected through",
          prepared and tr("The IP you are connected through does not change.") in prepared[-1],
          str(prepared))
    w = app.working
    check(f"{v}: keys prepared", T.ip_mode(w, "eth0") == "pppoe"
          and w.get("ppp.1.password") == "secret"
          and w.get("iptables.sys.masq.1.devname") == "ppp+")
    asked = [t for k, ti, t in ui.log if k == "password"]
    check(f"{v}: the password is asked for hidden", asked
          and tr("PPPoE password (empty = keep the current one):") not in asked[0])

    # 3) test mode: before deciding it says whether the session connected
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    decide = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test mode")]
    check(f"{v}: the decision shows PPPoE connected",
          decide and tr("PPPoE: connected, IP {ip}.", ip="100.64.10.20") in decide[0],
          (decide or [""])[0][:700])
    s = d.saved
    check(f"{v}: saved with the password", s.get("ppp.1.name") == "customer"
          and s.get("ppp.1.password") == "secret" and s.get("iptables.sys.masq.1.devname") == "ppp+"
          and s.get("iptables.sys.tcpmss.status") == "enabled" and tr("Done") in ui.titles(),
          str(ui.titles()))

    # 4) status, summary and reconnection
    ui.log.clear()
    ui.menus.clear()
    ui.script = [("menu", "p"), ("menu", "1"), ("menu", "c"), ("yesno", 0), ("menu", None)]
    app.network_menu()
    network = [items for t, items, _ in ui.menus if t == tr("Network")]
    check(f"{v}: with PPPoE on, the status and the reconnection show up",
          network and {"p", "c"} <= {k for k, _ in network[0]})
    st = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("PPPoE connection")]
    check(f"{v}: the session's status", st and tr("connected") in st[0]
          and "100.64.10.20" in st[0] and tr("Connected for") in st[0], (st or [""])[0])
    summary = [t for k, ti, t in ui.log if k == "textbox"]
    check(f"{v}: summary with PPPoE and without the password", summary
          and (f"{tr('PPPoE user:'):<16} " + tr("{user} · password saved", user="customer"))
          in summary[0]
          and "secret" not in summary[0], (summary or [""])[0][:600])
    if version == 8:
        check("v8: the summary shows the session's IP",
              f"{tr('IP in use now:'):<16} 100.64.10.20" in summary[0])
    check(f"{v}: it reconnects and connects again", "pppoe-restart" in d.events and len(st) == 2
          and tr("connected") in st[1], str(st))
    if version == 6:
        d.ui_lang = "es"
        info = dev.pppoe_info()
        d.ui_lang = "en"
        check("v6: the status with the web in Spanish", info.get("iplocal") == "100.64.10.20",
              str(info))

    # 5) an empty password = the current one; an MTU out of range is asked for again
    ui.log.clear()
    top = 1518 if version == 8 else 1492
    ui.script = [("menu", "2"), ("menu", "3"), ("input", "customer"), ("password", ""),
                 ("input", "isp"), ("input", str(top + 1)), ("input", "1480"), ("input", "1480"),
                 ("yesno", 1), ("input", "192.168.10.1"), ("input", "24"), ("menu", None)]
    app.network_menu()
    bad = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
    check(f"{v}: MTU {top + 1} rejected (limit {top})", bad
          and tr('"{value}" is not valid: type a whole number from {lo} to {hi}.', value=top + 1,
                 lo=64, hi=top) in bad[0], str(bad))
    asked = [t for k, ti, t in ui.log if k == "password"]
    check(f"{v}: it offers to keep the current password", asked
          and tr("PPPoE password (empty = keep the current one):") in asked[0])
    check(f"{v}: only the service, MTU and MRU change",
          {k for k, _, _ in app.changes()} == {"ppp.1.pppoe_service", "ppp.1.mtu", "ppp.1.mru"}
          and app.working.get("ppp.1.password") == "secret", str(app.changes()))
    app.working = app.original.copy()
    ui.menus.clear()
    ui.script = wizard("customer", "other") + [("menu", None)]
    app.network_menu()
    wan_item = [label for k, label in ui.menus[-1][1] if k == "2"]
    check(f"{v}: changing only the password marks the WAN as changed",
          {k for k, _, _ in app.changes()} == {"ppp.1.password"} and wan_item
          and wan_item[0].startswith("* "), f"{app.changes()} {wan_item}")
    app.working = app.original.copy()

    # 6) a wrong password: the decision warns NOT CONNECTED and it is reverted
    ui.log.clear()
    ui.script = wizard("customer", "wrong") + [("menu", None)]
    app.network_menu()
    ui.script = [("yesno", 0), ("yesno", 1)]
    app.apply(test=True)
    decide = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test mode")]
    check(f"{v}: the decision warns that it did not connect", decide
          and tr("⚠ PPPoE: NOT CONNECTED. Check the user, password and service before keeping "
                 "the changes.") in decide[0],
          (decide or [""])[0][:700])
    check(f"{v}: reverted to the right password", d.running.get("ppp.1.password") == "secret"
          and tr("Reverted") in ui.titles(), str(ui.titles()))
    app.working = app.original.copy()

    # 7) back to DHCP from the menu: it undoes PPPoE as the web does
    ui.log.clear()
    ui.script = [("menu", "2"), ("menu", "2"), ("input", "192.168.10.1"), ("input", "24"),
                 ("menu", None)]
    app.network_menu()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    s = d.saved
    keys = ("dhcpc.1.status", "ppp.status", "ppp.1.status", "iptables.sys.masq.1.devname",
            "iptables.sys.tcpmss.status")
    check(f"{v}: back to DHCP", [s.get(k) for k in keys]
          == ["enabled", "disabled", "disabled", "eth0", "disabled"] and tr("Done") in ui.titles(),
          str({k: s.get(k) for k in keys}) + str(ui.titles()))
    ui.menus.clear()
    ui.script = [("menu", None)]
    app.network_menu()
    check(f"{v}: without PPPoE, no status or reconnection any more",
          not {"p", "c"} & {k for k, _ in ui.menus[0][1]})
    check(f"{v}: the status with PPPoE off", tr(
        "PPPoE is not enabled on the device. If you have just set it up, apply the changes "
        "first.") in T.format_pppoe(dev.pppoe_info()))
    srv.cluster.shutdown()


if __name__ == "__main__":
    unit()
    roles_unit()
    bridge_texts(18950)
    roleless_flow(18960)
    follow_unit()
    identity(18900)
    flows(8, 18910, "127.0.0.30")
    flows(6, 18920, "127.0.0.32")
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
