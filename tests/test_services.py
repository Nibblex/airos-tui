"""Tests of "Services": Ubiquiti discovery and CDP with the web's keys, the DMZ exceptions,
Gen3 boards, the ping watchdog and the emergency notice at startup (airOS 8), and complete
flows against the simulator (airOS 8 and 6).
All on 127.0.0.x: discovery is queried only on 127.0.0.1."""
import os
import sys
import threading

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("services")
tr = T.tr


def cfg(**kv):
    return T.Config("".join(f"{k.replace('__', '.')}={v}\n" for k, v in kv.items()))


def except_list(c, k=1):
    """[(port, protocol, status)] of iptables.sys.dmz.k.except, in order."""
    out, i = [], 1
    while f"iptables.sys.dmz.{k}.except.{i}.port" in c:
        p = f"iptables.sys.dmz.{k}.except.{i}"
        out.append((c.get(f"{p}.port"), c.get(f"{p}.proto"), c.get(f"{p}.status")))
        i += 1
    return out


def applied(c, up):
    return T._merge(c.copy(), up)


print("=== the state as the web shows it ===")
check("v8 without keys: discovery yes, CDP no",
      T.discovery_settings(cfg(), "v8") == (True, False))
check("v6 without keys: discovery yes, CDP no",
      T.discovery_settings(cfg(), "v6") == (True, False))
check("v6 without the CDP key: uses wireless.1.addmtikie",
      T.discovery_settings(cfg(wireless__1__addmtikie="enabled"), "v6") == (True, True))
check("v6 empty discovery = enabled",
      T.discovery_settings(cfg(discovery__status=""), "v6")[0] is True)
check("v8 empty discovery = disabled (the web's model)",
      T.discovery_settings(cfg(discovery__status=""), "v8")[0] is False)
check("v8 accepts enabled/true/1", T.discovery_settings(
    cfg(discovery__status="1", discovery__cdp__status="true"), "v8") == (True, True))
check("normal values", T.discovery_settings(
    cfg(discovery__status="disabled", discovery__cdp__status="enabled"), "v6") == (False, True))
check("Gen3 on airOS 8: not offered", not T.services_offered("v8", {"feature.g3": "1"})
      and T.services_offered("v8", {"feature.g3": "0"}) and T.services_offered("v8", {})
      and T.services_offered("v6", {"feature.g3": "1"}))

print("=== section state on airOS 8 (the web's section.enabled()) ===")
c = cfg(httpd__status="enabled", sshd__status="disabled")
check("with a value", T._v8_on(c, "httpd.status") and not T._v8_on(c, "sshd.status"))
check("without a value: enabled except for the web's list",
      T._v8_on(c, "telnetd.status") and T._v8_on(c, "snmp.status")
      and not T._v8_on(c, "httpd.https.status") and not T._v8_on(c, "iptables.sys.dmz.status")
      and not T._v8_on(c, "iptables.sys.dmz.4.except.status")
      and not T._v8_on(c, "netconf.1.phy.2.status") and T._v8_on(c, "iptables.sys.dmz.4.status"))
check("uppercase and 1", T._v8_on(cfg(x__status="Enabled"), "x.status")
      and T._v8_on(cfg(x__status="1"), "x.status") and not T._v8_on(cfg(x__status="0"), "x.status"))

print("=== the two keys, always together ===")
bridge = T.Config(S.cfg_text(S.Device(6, "u", "p", 1, 60).running))
up = T.plan_discovery(bridge, "v6", False, False)
check("bridge: only the two keys", up == {"discovery.status": "disabled",
                                          "discovery.cdp.status": "disabled"}, str(up))
check("v6 without the CDP key: writes it anyway (as services.cgi does)",
      "discovery.cdp.status" not in bridge
      and T.plan_discovery(bridge, "v6", True, False)["discovery.cdp.status"] == "disabled")

print("=== LLDP (udapi-bridge from airOS 8.6 on; the web does not show it) ===")
check("offered on airOS 8.6 and later", T.lldp_offered("v8", "v8.7.19", cfg())
      and T.lldp_offered("v8", "v8.6.2", cfg()) and T.lldp_offered("v8", "8.6", cfg()))
check("not on 8.5 nor on airOS 6", not T.lldp_offered("v8", "v8.5.7", cfg())
      and not T.lldp_offered("v8", "v8.5.7", cfg(discovery__lldp__status="enabled"))
      and not T.lldp_offered("v6", "v6.3.16", cfg(discovery__lldp__status="enabled")))
check("unknown version: according to the key", T.lldp_offered("v8", "", cfg(
    discovery__lldp__status="enabled")) and not T.lldp_offered("v8", "", cfg()))
check("without the key: enabled (the firmware's default)", T.lldp_enabled(cfg()))
check("values the firmware takes as enabled",
      all(T.lldp_enabled(cfg(discovery__lldp__status=v))
          for v in ("enabled", "Enabled", "on", "ON", "1", "enable", "active", "TRUE")))
check("and the rest, disabled", not any(T.lldp_enabled(cfg(discovery__lldp__status=v))
                                        for v in ("disabled", "off", "0", "", "no")))

print("=== DMZ on airOS 6 (cfg_update_dmz_mgmt) ===")
OLD6 = [("80", "TCP"), ("18888", "TCP"), ("8", "ICMP"), ("9", "UDP"), ("22", "TCP"),
        ("10001", "UDP")]
r6 = cfg(netmode="router", iptables__status="enabled", iptables__sys__status="enabled",
         iptables__sys__dmz__status="enabled", iptables__sys__dmz__1__status="enabled",
         iptables__sys__dmz__1__devname="eth0", iptables__sys__dmz__1__host="192.168.1.100",
         iptables__sys__dmz__1__except__status="enabled", httpd__port="80",
         sshd__status="enabled", sshd__port="22", discovery__status="enabled",
         **{f"iptables__sys__dmz__1__except__{i}__{f}": v for i, (port, proto) in
            enumerate(OLD6, 1) for f, v in (("port", port), ("proto", proto),
                                             ("status", "enabled"))})
off6 = applied(r6, T.plan_discovery(r6, "v6", False, False))
check("without discovery: 10001 out (entry 6 disabled)",
      except_list(off6) == [(p, q, "enabled") for p,
                            q in OLD6[:5]] + [("10001", "UDP", "disabled")],
      str(except_list(off6)))
on6 = applied(off6, T.plan_discovery(off6, "v6", True, False))
check("with discovery: 10001 comes back", except_list(on6) == [(p, q, "enabled") for p, q in OLD6],
      str(except_list(on6)))
https6 = applied(r6, {"httpd.https.status": "enabled", "httpd.https.port": "8443",
                      "snmp.status": "enabled"})
check("the web's order: http, airview, icmp, 9, ssh, https, telnet, snmp, discovery",
      [(p, q) for p, q, _ in except_list(applied(https6, T.plan_discovery(https6, "v6", True,
                                                                          True)))]
      == OLD6[:5] + [("8443", "TCP"), ("161", "UDP"), ("10001", "UDP")])
check("without exceptions in the DMZ: leaves it alone", set(T.plan_discovery(
    applied(r6, {"iptables.sys.dmz.1.except.status": "disabled"}), "v6", False, False))
    == {"discovery.status", "discovery.cdp.status"})
check("bridge: leaves it alone", set(T.plan_discovery(applied(r6, {"netmode": "bridge"}), "v6",
                                                      False, False))
      == {"discovery.status", "discovery.cdp.status"})

print("=== DMZ on airOS 8 (the WAN model rebuilds it on saving) ===")
r8 = T.Config(S.cfg_text(S.Device(8, "u", "p", 1, 60, mode="router").running))
OLD8 = [("8", "ICMP"), ("9", "UDP"), ("80", "TCP"), ("22", "TCP"), ("10001", "UDP"),
        ("7777", "TCP")]
r8 = applied(r8, dict({"iptables.sys.dmz.status": "enabled",
                       "iptables.sys.dmz.1.status": "enabled",
                       "iptables.sys.dmz.1.devname": "eth0",
                       "iptables.sys.dmz.1.host": "192.168.1.100",
                       "iptables.sys.dmz.1.except.status": "enabled",
                       "telnetd.status": "disabled", "snmp.status": "disabled"},
                      **{f"iptables.sys.dmz.1.except.{i}.{f}": v for i, (port, proto) in
                         enumerate(OLD8, 1) for f, v in (("port", port), ("proto", proto),
                                                          ("status", "enabled"))}))
off8 = applied(r8, T.plan_discovery(r8, "v8", False, False))
check("without discovery: the list rebuilt without 10001 or leftovers",
      except_list(off8) == [("8", "ICMP", "enabled"), ("9", "UDP", "enabled"),
                            ("80", "TCP", "enabled"), ("22", "TCP", "enabled")]
      and not any(k.startswith("iptables.sys.dmz.1.except.5.") for k in off8.keys()),
      str(except_list(off8)))
check("the exceptions section stays enabled", off8.get("iptables.sys.dmz.1.except.status")
      == "enabled" and off8.get("iptables.sys.dmz.1.host") == "192.168.1.100")
on8 = applied(off8, T.plan_discovery(off8, "v8", True, True))
check("with discovery: 10001 at the end", except_list(on8)[-1] == ("10001", "UDP", "enabled")
      and len(except_list(on8)) == 5)
check("telnet without a status counts as enabled (as in the web)",
      ("23", "TCP", "enabled") in except_list(applied(r8, T.plan_discovery(
          T._merge(r8.copy(), {"telnetd.status": None}), "v8", True, False))))
ppp8 = applied(r8, T.plan_pppoe(r8, T.main_iface(r8, "v8"), {
    "name": "customer", "password": "secret", "service": "", "fallback": "192.168.10.1",
    "fallback_netmask": "255.255.255.0", "mtu": 1492, "mru": 1492, "mppe": False}))
check("with PPPoE the DMZ is on ppp+", ppp8.get("iptables.sys.dmz.1.devname") == "ppp+"
      and except_list(applied(ppp8, T.plan_discovery(ppp8, "v8", False, False)))[-1][0] == "22")
check("a DMZ that forwards management too: leaves it alone", set(T.plan_discovery(
    applied(r8, {"iptables.sys.dmz.1.except.status": "disabled"}), "v8", False, False))
    == {"discovery.status", "discovery.cdp.status"})


print("=== Ping Watchdog (airOS 8): its web's rules and keys ===")
DEFAULTS = {"on": False, "host": "", "interval": 300, "delay": 300, "failures": 3,
            "support": False}
check("without keys: its model's defaults",
      T.watchdog_settings(cfg()) == DEFAULTS, str(T.watchdog_settings(cfg())))
check("the keys of a real LiteBeam 5AC (8.7.25): the same", T.watchdog_settings(cfg(
    pwdog__status="disabled", pwdog__period="300", pwdog__delay="300", pwdog__retry="3"))
      == DEFAULTS)
ON = T.watchdog_settings(cfg(pwdog__status="enabled", pwdog__host="192.168.1.1",
                             pwdog__period="60", pwdog__delay="120", pwdog__retry="5",
                             pwdog__command=T.PWDOG_SUPPORT))
check("enabled, with everything", ON == {"on": True, "host": "192.168.1.1", "interval": 60,
                                         "delay": 120, "failures": 5, "support": True}, str(ON))
check("enabled as its web reads it: enabled, true or 1, nothing else", [T.watchdog_settings(cfg(
    pwdog__status=v))["on"] for v in ("enabled", "true", "1", "Enabled", "on", "")]
      == [True, True, True, False, False, False])
odd = T.watchdog_settings(cfg(pwdog__period="60abc", pwdog__retry="x", pwdog__command=""))
check("numbers as parseInt reads them (60abc → 60); what is not one, as it is",
      (odd["interval"], odd["failures"], odd["support"]) == (60, "x", False), str(odd))
found = [T.watchdog_problem("host", v) for v in ("", "  ", "192.168.1.256", "01.2.3.4",
                                                 "192.168.1.1", " 8.8.8.8 ")]
check("the IP: required and an IPv4 address (the web's ipv4Address pattern)", found == [
    tr("IP Address to Ping is required")] * 2 + [tr("IP Address to Ping is invalid")] * 2
      + [None, None], str(found))
found = [T.watchdog_problem("interval", v) for v in ("", "0", "1.5", "-3", "٣", "1", "86400")]
check("the interval: an integer greater than 0, with its own message", found == [
    tr("Ping Interval must be an integer greater than 0")] * 5 + [None, None], str(found))
between = tr("Startup Delay must be between {low} and {high}", low=20, high=65535)
found = [T.watchdog_problem("delay", v) for v in ("", "19", "20", "65535", "65536", "abc")]
check("the startup delay: required, from 20 to 65535", found == [
    tr("Startup Delay is required"), between, None, None, between, between], str(found))
between = tr("Failure Count To Reboot must be between {low} and {high}", low=3, high=15)
found = [T.watchdog_problem("failures", v) for v in ("", "2", "3", "15", "16")]
check("the failure count to reboot: required, from 3 to 15", found == [
    tr("Failure Count To Reboot is required"), between, None, None, between], str(found))
check('what it saves: all its keys and the command of "Save Support Info"',
      T.plan_watchdog(ON) == {"pwdog.status": "enabled", "pwdog.host": "192.168.1.1",
                              "pwdog.period": "60", "pwdog.delay": "120", "pwdog.retry": "5",
                              "pwdog.command": T.PWDOG_SUPPORT})
check("without IP or support info it deletes them (its configuration leaves out empty values)",
      T.plan_watchdog(DEFAULTS) == {"pwdog.status": "disabled", "pwdog.host": None,
                                    "pwdog.period": "300", "pwdog.delay": "300",
                                    "pwdog.retry": "3", "pwdog.command": None})


def start(version, port, ip, dport):
    srv = S.make_server(version, port, restart=0.6, test_secs=60, mgmt_ip=ip,
                        discovery_port=dport)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://{ip}:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.discovery = {"target": "127.0.0.1", "port": dport}
    app.load()
    return srv, srv.dev, ui, app


def answers(dport):
    return [d["ip"] for d in T.discover(1.0, target="127.0.0.1", port=dport)]


def flow(version, port, ip, dport):
    v = f"v{version}"
    print(f"=== {v}: turning discovery off and CDP on, test and keep ===")
    srv, d, ui, app = start(version, port, ip, dport)
    try:
        check(f"{v}: at first it answers discovery", answers(dport) == [ip])
        ui.script = [("menu", "s"), ("menu", None), ("menu", "0")]
        app.run()
        main = [dict(items) for t, items, _ in ui.menus if t != tr("Services")][0]
        label = (tr("Services: web, SSH, SNMP, discovery, ping watchdog, UISP and more")
                 if version == 8 else tr("Services: device discovery and CDP"))
        check(f'{v}: entry "s" in the main menu', main.get("s") == label, str(main))
        first = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][0]
        lldp = ({"l": tr("LLDP: enabled"),
                 "w": tr("Ping Watchdog: {state}", state=tr("disabled")),
                 "a": tr("SNMP Agent: disabled"),
                 "h": tr("Web Server: HTTP on port {http}, without HTTPS", http=80),
                 "s": tr("SSH Server: enabled, port {port}, {count} key(s)", port=22, count=0),
                 "t": tr("Telnet Server: disabled"), "g": tr("System Log: enabled"),
                 "u": tr("UISP: {state}",
                         state=tr("disabled") + " · " + tr("UISP not configured"))}
                if version == 8 else {})          # (the watchdog, the rest and UISP: airOS 8)
        check(f"{v}: initial state", first == dict({"d": tr("Ubiquiti discovery: enabled"),
                                                    "c": tr("CDP: disabled")}, **lldp),
              str(first))
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "d"), ("yesno", 0), ("menu", "c"), ("yesno", 0), ("menu", None)]
        app.services_menu()
        asked = [(ti, t) for k, ti, t in ui.log if k == "yesno"]
        check(f"{v}: explains what is lost", asked and asked[0][0] == tr("Ubiquiti discovery")
              and "airos-tui --discover" in asked[0][1] and tr(
                  "Disable Ubiquiti discovery?\n\nThe antenna will stop answering the discovery "
                  'query (UDP 10001): neither airos-tui --discover nor the "Discovery" tool of '
                  "other Ubiquiti devices will find it. If a later change moves it to DHCP or "
                  "PPPoE, this program will only be able to look for it at its fallback IP.")
              in asked[0][1], str(asked[:1]))
        check(f"{v}: explains CDP", len(asked) > 1 and "Cisco Discovery Protocol" in asked[1][1])
        last = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][-1]
        check(f"{v}: marks the changes", last == dict({
            "d": "* " + tr("Ubiquiti discovery: disabled"), "c": "* " + tr("CDP: enabled")},
            **lldp), str(last))
        check(f"{v}: keys prepared", app.working.get("discovery.status") == "disabled"
              and app.working.get("discovery.cdp.status") == "enabled"
              and [k for k, _, _ in app.changes()] == ["discovery.cdp.status",
                                                        "discovery.status"],
              str(app.changes()))
        ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 0)]       # test and keep
        app.apply_menu()
        check(f"{v}: saved on the device", d.saved.get("discovery.status") == "disabled"
              and d.saved.get("discovery.cdp.status") == "enabled", str(ui.titles()))
        check(f"{v}: no longer answers discovery", answers(dport) == [])
        ui.script = [("menu", "d"), ("yesno", 0), ("menu", None), ("menu", "2"), ("yesno", 0)]
        app.services_menu()
        app.apply_menu()                                              # apply and save
        check(f"{v}: enabled again", d.saved.get("discovery.status") == "enabled"
              and d.saved.get("discovery.cdp.status") == "enabled")
        check(f"{v}: answers again", answers(dport) == [ip])
        ui.script = [("menu", "c"), ("yesno", 1), ("menu", None)]      # "No": nothing changes
        app.services_menu()
        check(f'{v}: answering "No" changes nothing', not app.changes())
        if version == 8:
            lldp_flow(v, d, ui, app)
    finally:
        srv.cluster.shutdown()


def lldp_flow(v, d, ui, app):
    print(f"=== {v}: LLDP ===")
    ui.menus.clear()
    ui.log.clear()
    ui.script = [("menu", "l"), ("yesno", 0), ("menu", None)]
    app.services_menu()
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == "LLDP"]
    check(f"{v}: explains LLDP and that the web does not show it", asked
          and "IEEE 802.1AB" in asked[0] and tr(
              "Disable LLDP?\n\nThe antenna will stop using LLDP (Link Layer Discovery "
              "Protocol, IEEE 802.1AB): switches and routers will no longer list it among their "
              "neighbors. It does not affect access to the antenna or Ubiquiti discovery.\n\nThe "
              "web does not show this setting: airos-tui changes the key the firmware reads, "
              "discovery.lldp.status.") in asked[0]
          and "discovery.lldp.status" in asked[0], str(asked))
    last = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][-1]
    check(f"{v}: marks LLDP", last.get("l") == "* " + tr("LLDP: disabled"), str(last))
    check(f"{v}: only discovery.lldp.status changes", app.changes()
          == [("discovery.lldp.status", "enabled", "disabled")], str(app.changes()))
    ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 0)]           # test and keep
    app.apply_menu()
    check(f"{v}: LLDP saved", d.saved.get("discovery.lldp.status") == "disabled"
          and d.saved.get("discovery.status") == "enabled"
          and d.saved.get("discovery.cdp.status") == "enabled")
    ui.script = [("menu", "l"), ("yesno", 0), ("menu", None), ("menu", "2"), ("yesno", 0)]
    app.services_menu()
    app.apply_menu()
    check(f"{v}: LLDP enabled again", d.saved.get("discovery.lldp.status") == "enabled")
    print(f"=== {v}: LLDP by version and key ===")
    app.original.delete("discovery.lldp.status")
    app.working.delete("discovery.lldp.status")
    ui.menus.clear()
    ui.script = [("menu", "l"), ("yesno", 0), ("menu", None)]
    app.services_menu()
    first = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][0]
    check(f"{v}: without the key it shows as enabled, and turning it off writes it",
          first.get("l") == tr("LLDP: enabled")
          and app.working.get("discovery.lldp.status") == "disabled", str(first))
    app.working = app.original.copy()
    real_fw, app.dev.fw = app.dev.fw, "v8.5.7"
    ui.menus.clear()
    ui.log.clear()
    ui.script = [("menu", None)]
    try:
        app.services_menu()
    finally:
        app.dev.fw = real_fw
    menu = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][0]
    check(f"{v}: not offered on 8.5", set(menu) == set("dcwahstgu")
          and tr("LLDP is not in the web.") not in str(ui.log), str(menu))


def follow_warning(port, ip, dport):
    print("=== v8: IP over DHCP with discovery off ===")
    srv, d, ui, app = start(8, port, ip, dport)
    try:
        ui.script = [("menu", "d"), ("yesno", 0), ("menu", None),
                     ("menu", "2"), ("menu", "2"), ("input", "192.168.1.20"), ("input", "24"),
                     ("menu", None),
                     ("menu", "1"), ("yesno", 1)]                      # test: "No"
        app.services_menu()
        app.network_menu()
        app.apply_menu()
        notes = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Management IP")]
        check("the note warns about it", notes and tr(
            "You are now connected through {old}. The antenna will ask for an IP over DHCP: the "
            "program will look for it on your network by its MAC (Ubiquiti discovery) and at the "
            "fallback IP {fallback}, the one it takes if no DHCP server answers.",
            old=ip, fallback="192.168.1.20") + " " + tr(
            "Careful: Ubiquiti discovery is disabled, so the search by MAC will not find it.")
            + "\n\n" in notes[-1], str(notes[-1:]))
        confirm = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test changes")]
        check("the confirmation warns about it", confirm and tr(
            "⚠ Ubiquiti discovery stays disabled: I will not be able to find the antenna by its "
            "MAC, only at {ips}.", ips=f"192.168.1.20, {ip}") in confirm[0], (confirm or [""])[0])
        check("nothing was sent", d.saved.get("discovery.status") == "enabled")
    finally:
        srv.cluster.shutdown()


def gen3(port, ip, dport):
    print("=== v8 Gen3: what its page has (no discovery, CDP, SNMP or Telnet) ===")
    srv, d, ui, app = start(8, port, ip, dport)
    try:
        app.limits.board["feature.g3"] = "1"
        ui.menus.clear()
        ui.script = [("menu", None)]
        app.services_menu()
        title, items, default = ui.menus[0]
        text = [t for k, ti, t in ui.log if k == "menu"][0]
        check("the watchdog, the web server, SSH, the log and UISP", [k for k, _ in items]
              == ["w", "h", "s", "g", "u"] and default == "w" and not app.changes(), str(items))
        check("  and it says so", text.endswith(" " + tr(
            "On this board (Gen3) its web has no discovery, CDP, SNMP or Telnet.")), text)
        check("the main menu sums it up like this", app._services_label() == tr(
            "Services: web, SSH, system log, ping watchdog and UISP"))
    finally:
        srv.cluster.shutdown()


def uisp(port, ip, dport):
    print("=== v8: UISP, as the switch in its web's header ===")
    srv, d, ui, app = start(8, port, ip, dport)
    tr = T.tr
    key = "wss://uisp.example.com:443+" + "a" * 40 + "+allowUntrustedCertificate"
    try:
        ui.menus.clear()
        ui.script = [("menu", None)]
        app.services_menu()
        first = [dict(items) for t, items, _ in ui.menus if t == tr("Services")][0]
        check('in "Services": disabled and not configured', first.get("u") == tr(
            "UISP: {state}", state=tr("disabled") + " · " + tr("UISP not configured")),
              str(first))
        text = [t for k, ti, t in ui.log if k == "menu" and ti == tr("Services")]
        check("  (and the text says it is the switch in its web's header)", text and tr(
            "The Services page of the web: device discovery, the ping watchdog, SNMP, its web "
            "server, SSH, Telnet and the system log; UISP is the switch in its header. * = "
            "changed, not applied yet.") + " " + tr("LLDP is not in the web.") == text[0],
              str(text[:1]))
        real_fw, app.dev.fw = app.dev.fw, "v8.5.7"
        before_86 = app._services_label()
        app.dev.fw = real_fw
        check("the main menu sums it up (the same before airOS 8.6, without LLDP)",
              app._services_label() == tr("Services: web, SSH, SNMP, discovery, ping watchdog, "
                                          "UISP and more") == before_86, before_86)
        p0 = app.original.get("radio.1.txpower")
        app.working.set("radio.1.txpower", "12")
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "k"), ("input", "   "), ("input", "wss://short"), ("input", "x" * 60),
                     ("input", key), ("menu", None)]
        app.uisp_menu()
        screens = [m for m in ui.menus if m[0] == "UISP"]
        check("without a key it cannot be turned on, only added", [t for t, _ in screens[0][1]]
              == ["k", "r"] and dict(screens[0][1])["k"] == tr("Add Key"))
        errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
        check("the web's rules: required, 55 characters and wss://…", errors == [
            tr("UISP Key is required"), tr("UISP Key must be at least {count} characters",
                                           count=55), tr("UISP Key is invalid")], str(errors))
        asked = [t for k, ti, t in ui.log if k == "input" and ti == tr("UISP Controller")]
        check("  (and it warns that it needs NTP, which is not enabled here)", asked and tr(
            "Warning: correct time/date settings are required by UISP. Please make sure NTP "
            "time synchronization is enabled.") in asked[0])
        done = [t for k, ti, t in ui.log if k == "msgbox" and ti == "UISP"]
        check("sends it at once: on and connected; the key, without its secret",
              d.saved["unms.status"] == "enabled" and d.saved["unms.uri"] == key
              and done == [tr("UISP management is on: {controller}.",
                              controller="wss://uisp.example.com:443") + "\n"
                           + tr("Status: {state}",
                                state=tr("Connected") + " (2026-11-08 10:00:00)")],
              str(done))
        check("what is pending stays pending and does not undo the key", app.changes()
              == [("radio.1.txpower", p0, "12")] and app.working.get("unms.uri") == key)
        ui.script = [("menu", "s"), ("menu", None)]
        app.uisp_menu()
        check("turning it off: at once, and it keeps the key", d.saved["unms.status"]
              == "disabled" and d.saved["unms.uri"] == key and "unms-disabled" in d.events)
        ui.menus.clear()
        ui.script = [("menu", "s"), ("menu", None)]
        app.uisp_menu()
        items = dict([m for m in ui.menus if m[0] == "UISP"][0][1])
        check('with a key and off: "Turn UISP management on" and "Add Key", as in the web',
              items == {"s": tr("Turn UISP management on"), "k": tr("Add Key"),
                        "r": tr("Read the status again")} and d.saved["unms.status"] == "enabled"
              and d.events.count("unms-enabled") == 2, str(items))
        app.original.set("ntpclient.status", "enabled")
        ui.log.clear()
        ui.script = [("menu", "k"), ("input", None), ("menu", None)]
        app.uisp_menu()
        asked = [t for k, ti, t in ui.log if k == "input" and ti == tr("UISP Controller")]
        check("with NTP enabled it does not warn", asked and "NTP" not in asked[0])
        real = app.dev.unms_disable

        def broken():
            raise T.AirOSError("api/unms/disable answered HTTP 500: x")
        app.dev.unms_disable = broken
        ui.log.clear()
        ui.script = [("menu", "s")]
        app._do(app.uisp_menu)
        app.dev.unms_disable = real
        check("if the device fails: it says so and nothing changes", not ui.script
              and ("msgbox", "Error", "api/unms/disable answered HTTP 500: x") in ui.log
              and d.saved["unms.status"] == "enabled")
    finally:
        srv.cluster.shutdown()


def pwdog(c):
    return {k: c.get(k) for k in ("pwdog.status", "pwdog.host", "pwdog.period",
                                  "pwdog.delay", "pwdog.retry", "pwdog.command")}


def watchdog(port, ip, dport):
    print('=== v8: Ping Watchdog, as in the "Services" page of its web ===')
    srv, d, ui, app = start(8, port, ip, dport)
    title = tr("Ping Watchdog")
    try:
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "w"), ("menu", "s"), ("input", "  "), ("input", "192.168.1.300"),
                     ("input", "192.168.1.1"), ("menu", None), ("menu", None)]
        app.services_menu()
        rows = [dict(items).get("w") for t, items, _ in ui.menus if t == tr("Services")]
        check('in "Services": its row, disabled and then enabled, with its IP', rows == [
            tr("Ping Watchdog: {state}", state=tr("disabled")),
            "* " + tr("Ping Watchdog: {state}", state=tr("enabled") + " · 192.168.1.1")],
              str(rows))
        screens = [items for t, items, _ in ui.menus if t == title]
        check("disabled: only the switch (the web hides its fields)",
              [k for k, _ in screens[0]] == ["s"], str(screens[0]))
        errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
        check("enabling it asks for the IP, with the web's rules and messages", errors == [
            tr("IP Address to Ping is required"), tr("IP Address to Ping is invalid")],
              str(errors))
        asked = [t for k, ti, t in ui.log if k == "input" and ti == tr("IP Address To Ping")]
        check("  (and it explains that the device reboots if it does not answer)", asked
              and "300 s" in asked[0] and "3 pings" in asked[0], str(asked[:1]))
        check("enabled: its fields, with the default values", dict(screens[-1]) == {
            "s": "* " + tr("Ping Watchdog: {state}", state=tr("enabled")),
            "h": "* " + tr("IP Address To Ping: {address}", address="192.168.1.1"),
            "i": tr("Ping Interval: {seconds} sec", seconds=300),
            "d": tr("Startup Delay: {seconds} sec", seconds=300),
            "f": tr("Failure Count To Reboot: {count}", count=3),
            "p": tr("Save Support Info: {state}", state=tr("disabled"))}, str(screens[-1]))
        check("  (pending, only what changes)", [k for k, _, _ in app.changes()] == [
            "pwdog.host", "pwdog.status"], str(app.changes()))
        ui.log.clear()
        ui.script = [("menu", "i"), ("input", "0"), ("input", "60"),
                     ("menu", "d"), ("input", "19"), ("input", "30"),
                     ("menu", "f"), ("input", "16"), ("input", "5"),
                     ("menu", "p"), ("yesno", 0), ("menu", None)]
        app.watchdog_menu()
        errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
        check("interval, delay and failures, with the web's messages", errors == [
            tr("Ping Interval must be an integer greater than 0"),
            tr("Startup Delay must be between {low} and {high}", low=20, high=65535),
            tr("Failure Count To Reboot must be between {low} and {high}", low=3, high=15)],
              str(errors))
        want = {"pwdog.status": "enabled", "pwdog.host": "192.168.1.1", "pwdog.period": "60",
                "pwdog.delay": "30", "pwdog.retry": "5", "pwdog.command": T.PWDOG_SUPPORT}
        check("the keys prepared, as its web saves them", pwdog(app.working) == want,
              str(pwdog(app.working)))
        ui.script = [("menu", "2"), ("yesno", 0)]                     # apply and save
        app.apply_menu()
        check("saved on the device", pwdog(d.saved) == want,
              str(ui.titles()))
        ui.log.clear()
        ui.script = [("menu", "s"), ("yesno", 0), ("menu", None)]
        app.watchdog_menu()
        asked = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
        check("disabling it: it asks, and only the switch changes (the rest stays, as in the "
              "web)", asked and "192.168.1.1" in asked[0] and app.changes() == [
                  ("pwdog.status", "enabled", "disabled")], str(app.changes()))
        ui.script = [("menu", "s"), ("input", "192.168.1.1"), ("menu", None)]
        app.watchdog_menu()
        check("back to what the device has: nothing pending", not app.changes()
              and ui.inputs[-1] == "192.168.1.1", str(app.changes()))
        ui.script = [("menu", "p"), ("yesno", 0), ("menu", None)]
        app.watchdog_menu()
        check('turning off "Save Support Info" deletes its command', app.changes() == [
            ("pwdog.command", T.PWDOG_SUPPORT, None)], str(app.changes()))
        app.working = app.original.copy()
        ui.script = [("menu", "s"), ("yesno", 0), ("menu", "s"), ("input", None),
                     ("menu", None)]
        app.watchdog_menu()
        check("cancelling when asked for the IP does not enable it", app.changes() == [
            ("pwdog.status", "enabled", "disabled")], str(app.changes()))
        for c in (app.original, app.working):        # (an out of range value on the device)
            c.set("pwdog.status", "disabled")
            c.set("pwdog.retry", "20")
        ui.log.clear()
        ui.script = [("menu", "s"), ("input", "10.0.0.1"), ("input", "4"), ("menu", None)]
        app.watchdog_menu()
        errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
        check("enabling it corrects whatever the device has outside the rules", errors == [
            tr("Failure Count To Reboot must be between {low} and {high}", low=3, high=15)]
              and app.working.get("pwdog.retry") == "4"
              and app.working.get("pwdog.host") == "10.0.0.1", str(errors))
    finally:
        srv.cluster.shutdown()


def emergency(port, ip, dport):
    print("=== v8: the emergency notice at startup (its web's checkForEmergency) ===")
    srv, d, ui, app = start(8, port, ip, dport)
    try:
        d.emerg_reason = 0
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", None), ("menu", "0")]             # "Later", then quit
        app.run()
        notice = [(ti, t) for k, ti, t in ui.log if k == "menu"][0]
        check("rebooted by the watchdog: the web's notice, first of all", notice[0] == tr(
            "Warning") and notice[1].startswith(tr("Device was rebooted by Ping Watchdog.")),
              str(notice))
        check('  (with "Download Support Info" and "Dismiss")', ui.menus[0][1] == [
            ("s", tr("Download Support Info")), ("d", tr("Dismiss"))], str(ui.menus[0][1]))
        check('"Later" does not dismiss it', d.emerg_reason == 0
              and "warnings-dismissed" not in d.events)
        os.makedirs(XDG, exist_ok=True)
        path = os.path.join(XDG, "emergency.sup")
        ui.log.clear()
        ui.script = [("menu", "s"), ("input", path), ("menu", "d"), ("menu", "0")]
        app.run()
        with open(path, "rb") as f:
            head = f.read(2)
        os.unlink(path)
        titles = [ti for k, ti, _ in ui.log if k == "menu"]
        check("it shows up again; downloading the support info and dismissing it", head
              == b"\x1f\x8b" and titles[:2] == [tr("Warning")] * 2 and d.emerg_reason == -1
              and d.events.count("warnings-dismissed") == 1, str(titles))
        ui.log.clear()
        ui.script = [("menu", "0")]
        app.run()
        check("dismissed, it no longer shows up", tr("Warning") not in [ti for k, ti, _ in ui.log])
        for code, title, first in ((1, tr("Warning"), tr("Flash memory failure detected.")),
                                   (2, tr("Crash Log Report"), tr("Unexpected reboot detected.")),
                                   (3, tr("Crash Log Report"), tr("Unexpected reboot detected."))):
            d.emerg_reason = code
            ui.log.clear()
            ui.script = [("menu", "d"), ("menu", "0")]
            app.run()
            notice = [(ti, t) for k, ti, t in ui.log if k == "menu"][0]
            check(f'code {code}: "{first}"', notice[0] == title
                  and notice[1].startswith(first) and d.emerg_reason == -1, str(notice))
        check("  (the crash report is not sent to Ubiquiti, and it says so)", tr(
            "Its web also offers to send a report to Ubiquiti (from the browser); airos-tui "
            "does not send it.") in notice[1])
        d.emerg_reason, d.fw_pending = 0, "WA.v8.7.20"
        ui.log.clear()
        ui.script = [("menu", "d"), ("yesno", 1), ("menu", "0")]
        app.run()
        order = [(k, ti) for k, ti, _ in ui.log if k in ("menu", "yesno")][:2]
        check("before the notice of the uploaded firmware, as its web does", order == [
            ("menu", tr("Warning")), ("yesno", tr("Firmware Update"))]
              and d.fw_pending is None, str(order))
        app.dev.flavor = "v6"
        ui.log.clear()
        app.emergency_notice(0)
        app.dev.flavor = "v8"
        check("airOS 6: it does not have it", not ui.log)
    finally:
        srv.cluster.shutdown()


def review_dmz():
    print("=== the review: the DMZ's management ports follow the services ===")

    class Offline:
        flavor, host, base, username, notes, backup_loaded = (
            "v8", "192.0.2.1", "https://192.0.2.1", "ubnt", [], False)
    c = T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60, mode="router").running))
    wan = T.main_iface(c, "v8")
    for k, v in (("iptables.status", "enabled"), ("iptables.sys.status", "enabled"),
                 ("iptables.sys.dmz.status", "enabled"), ("iptables.sys.dmz.1.status", "enabled"),
                 ("iptables.sys.dmz.1.devname", T.nat_devname(c, wan)),
                 ("iptables.sys.dmz.1.host", "192.168.1.50"),
                 ("iptables.sys.dmz.1.except.status", "enabled"),
                 ("httpd.https.status", "enabled")):
        c.set(k, v)
    T._merge(c, T.dmz_mgmt_exceptions(c, "v8"))
    app = T.App(FakeUI(), Offline())
    app.set_loaded(c.text())
    app._accept_service(T.plan_web(dict(T.web_settings(c), https_port="8443")))
    app._accept_service(T.plan_ssh(dict(T.ssh_settings(c), port="2222")))
    ports = [p for p, _, _ in except_list(app.working)]
    check("changing the HTTPS or SSH port rebuilds its list (as its web does on saving)",
          "8443" in ports and "2222" in ports and "443" not in ports and "22" not in ports,
          str(except_list(app.working)))
    moved = T.switch_netmode(app.working, "v8", "bridge", {})
    back = T.network_back(moved, app.original, "v8")
    check("  and back to the device's network mode, it matches the prepared services",
          except_list(back) == except_list(app.working), str(except_list(back)))
    same = T.network_back(T.switch_netmode(c, "v8", "bridge", {}), c, "v8")
    check("  with no service changed, the device's list, as it is", not c.diff(same))


review_dmz()
flow(8, 18401, "127.0.0.150", 18451)
flow(6, 18402, "127.0.0.156", 18452)
follow_warning(18403, "127.0.0.151", 18453)
gen3(18404, "127.0.0.152", 18454)
uisp(18405, "127.0.0.153", 18455)
watchdog(18406, "127.0.0.154", 18456)
emergency(18407, "127.0.0.155", 18457)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
