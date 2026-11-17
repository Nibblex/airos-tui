"""Tests of Tools and System: the airOS 8 web's password rules, the alignment (with real
python-airos statuses), the site survey and what choosing an access point prepares, the device
discovery from the antenna, the password change and the live screen (with a pseudo-terminal).
All against the simulator at 127.0.0.1."""
import glob
import io
import json
import os
import pty
import re
import sys
import tarfile
import termios
import threading
import time

from _common import check, FakeUI, fails, FIXTURES, suite  # noqa: E402
T, S, XDG = suite("tools")
tr = T.tr


def start(version, port, **kw):
    srv = S.make_server(version, port, restart=0.3, test_secs=60, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=1)
    app.load()
    return srv, srv.dev, dev, app.ui, app


def run(app, method, script, *args):
    app.ui.script = list(script)
    app.ui.log.clear()
    app.ui.watched.clear()
    try:
        getattr(app, method)(*args)
    except AssertionError as e:
        print("     script:", e)
        return False
    return not app.ui.script


def keys(app):
    return {k: b for k, _, b in app.changes()}


def msgs(ui, title=None):
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def password_rules():
    print("=== password: the airOS 8 web's rules (ChangePasswordView) ===")
    P = T.password_problem
    cases = [
        ("", "", tr("Type the new password.")),
        ("Abc1!", "", tr("Use at least 12 characters.")),
        ("Abcdefgh1!x", "", tr("Use at least 12 characters.")),           # 11
        ("north-tower-26", "", None),                                       # 14, nothing else
        ("aaab-cdef-ghij", "", tr("The password may not contain sequences of three or more "
                                  "repeated characters.")),
        ("my-ubnt-radio-1", "", tr("Please avoid using known facts like 'ubnt', 'password', "
                                   "your username or hostname.")),
        ("PassWord-tower-9", "", tr("Please avoid using known facts like 'ubnt', 'password', "
                                    "your username or hostname.")),
        ("aaaaaaaaaaaaaaaaaaaa", "", None),             # 20 or more: the web takes it as it is
        ("my-ubnt-radio-long-01", "", None),
        ("north-tower-26", "north-tower-26", tr("Entered password is the same as current. "
                                                "Please choose a different one.")),
        ("x" * 64, "", tr("At most {max} characters.", max=63)),
        ("😀" * 6, "", None),                          # 12 UTF-16 units, as JS counts them
        ("😀" * 5, "", tr("Use at least 12 characters.")),
    ]
    bad = [(new, P(new, cur), want) for new, cur, want in cases if P(new, cur) != want]
    check(f"the {len(cases)} cases give the same as the web", not bad, str(bad))
    check("  its first setup's password takes 64 (its field)",
          P("Secure-Key-" + "x" * 53, longest=64) is None
          and P("y" * 65, longest=64) == tr("At most {max} characters.", max=64))

    print("=== password: airOS 6.3's first login (its page's pstrength.js) ===")
    V = T.v6_password_verdict
    # (its calculateScore's own verdicts, by hand and with the original under node: with every
    # group, 12 characters or more and other than the user name, its score never falls under
    # 34, "Normal" at least; of 146032 such random ones under node, 38 the lowest)
    cases = [("short", "ubnt", "short"), ("Abcdefgh1!x", "ubnt", "short"),        # 11
             ("onlyLowercase12", "ubnt", "weak"),             # (no symbol)
             ("no-uppercase-12", "ubnt", "weak"), ("NO-LOWERCASE-12", "ubnt", "weak"),
             ("No-Numbers-Here", "ubnt", "weak"),
             ("Operator-26!", "operator-26!", "weak"),       # (the user name, in other case)
             ("Secure-Key-2026x", "ubnt", "strong"),
             ("Aaaaaaaaaaa1!", "ubnt", "strong"),            # (repetitive: 80 points)
             ("Aa1!Aa1!Aa1!", "ubnt", "strong"),
             ("AAAAAAAAA9z.", "ubnt", "normal"),             # (49 points)
             ("Abcdefgh1!\U0001F600", "ubnt", "strong"),    # (the emoji counts two: 12)
             ("Abcdefg1!\U0001F600", "ubnt", "short"),      # (11)
             ("Abcdefgh1!\u2028x", "ubnt", "weak"),         # (a line break, which its "."
             ("Abcdefgh1!\rxyz", "ubnt", "weak"),           #  does not take: it stops short)
             ("Abcdefgh1!x\n", "ubnt", "weak"),
             ("\U00010428Bcdefgh1!xyz", "\U00010400bcdefgh1!XYZ", "weak")]  # (the user name:
    # its toLowerCase also lowercases the letters beyond U+FFFF, Deseret ones here)
    bad = [(p, u, V(p, u), want) for p, u, want in cases if V(p, u) != want]
    check(f"its {len(cases)} verdicts: short, weak (a group missing, the user name or a line "
          "break), normal or strong, counting as JavaScript does", not bad, str(bad))
    check('  what the program says: "Too short", "Weak", or nothing',
          T.v6_password_problem("short", "ubnt") == tr("Too short: its web asks for at least "
                                                       "12 characters.")
          and T.v6_password_problem("onlyLowercase12", "ubnt") == tr(
              "Weak: its web asks for upper and lower case letters, numbers and symbols, not "
              "the user name, and not too repetitive.")
          and T.v6_password_problem("Secure-Key-2026x", "ubnt") is None
          and T.v6_username_problem("  ") == tr("Username is required")
          and T.v6_username_problem("x" * 32) == tr("At most {max} characters.", max=31)
          and T.v6_username_problem("Operator") is None)


def alignment_math():
    print("=== alignment: the signals as the web computes them (real statuses) ===")
    st = fixture("LiteBeam5AC_ap-ptp_30mhz.json")
    link = T.align_link(st, "v8")
    check("local: signal, chains (chainrssi + signal - rssi) and their difference",
          (link["local"]["signal"], link["local"]["chains"], link["local"]["imbalance"],
           link["local"]["percent"]) == (-65, [-67, -70], 3, 86), str(link["local"]))
    check("remote: the same with the other end's data",
          (link["remote"]["signal"], link["remote"]["chains"], link["remote"]["imbalance"])
          == (-66, [-68, -70], 2), str(link["remote"]))
    check("the other end's name and the CINR", link["name"] == "Harmonia_8a"
          and link["cinr"] == (24, 24))
    two = fixture("liteapgps_ap_ptmp_40mhz.json")
    other = T.align_link(two, "v8", "01:11:22:33:31:38")
    check("access point with two stations: the chosen one", other["name"] == "NanoBeam-shed1"
          and other["local"]["chains"] == [-46, -45], str(other["local"]))
    v6 = T.align_link(fixture("NanoStation_M5_sta_v6.3.16.json"), "v6")
    check("airOS 6: only the local side (status.cgi has no remote one)", v6["remote"] is None
          and (v6["local"]["signal"], v6["local"]["chains"]) == (-64, [-66, -68]), str(v6))
    check("no link: None", T.align_link({"wireless": {"sta": []}}, "v8") is None
          and T.align_link({"wireless": {"signal": 0}}, "v6") is None)
    best = {}
    text = T.align_text(link, best)
    check("the screen: signals, chains, the best one seen and the CINR", "-65 dBm" in text
          and tr("chains 0 / 1: {chains} (difference {diff} dB)", chains="-67 dBm / -70 dBm",
                 diff=3) in text and tr("best {signal}", signal="-65 dBm") in text
          and tr("CINR RX/TX {rx} / {tx} dB", rx=24, tx=24) in text, text)
    weaker = json.loads(json.dumps(st))
    weaker["wireless"]["sta"][0]["signal"] = -70
    text = T.align_text(T.align_link(weaker, "v8"), best)
    check("the best signal seen stays even if it drops", "-70 dBm" in text
          and tr("best {signal}", signal="-65 dBm") in text)
    skew = json.loads(json.dumps(st))
    skew["wireless"]["sta"][0].update(signal=-20, rssi=76, chainrssi=[76, 60, 0])
    text = T.align_text(T.align_link(skew, "v8"), {})
    check("warnings: chains more than 5 dB apart and a signal that saturates",
          tr("{side}: the two chains differ by more than {limit} dB (cable, connector or "
             "polarization).", side=tr("Local signal"), limit=5) in text
          and tr("{side}: above {limit} dBm the signal saturates the receiver (too close or "
                 "too much power).", side=tr("Local signal"), limit=-25) in text, text)
    check("no link: it says why", T.align_text(None, {}).startswith(tr(
        "No link: the device is not connected (to an access point, or no station is "
        "connected to it). The signal shows up as soon as there is one.")[:20]))


def survey_logic():
    print("=== site survey: reading it and what choosing an access point prepares (selectAp) ===")
    scanning, aps = T.survey_aps({"scan_status": "completed", "scan_data": S.SURVEY_APS})
    by = {ap["essid"] or ap["hostname"]: ap for ap in aps}
    check("reading: the radio mode as the web shows it", not scanning and (
        by["North-Tower"]["mode"], by["Cafe-WiFi"]["mode"]) == ("airMAX AC", "AC"))
    modes = ["ap-ptp", "ap-ptmp", "sta-ptp", "sta-ptmp"]
    sel = {name: T.survey_selectable(ap, 20, modes) for name, ap in by.items()}
    check("only the airMAX ones with a visible SSID can be chosen",
          sel == {"North-Tower": True, "South-Link": True, "Cafe-WiFi": False,
                  "Hidden-AP": False}, str(sel))
    check("and not with an automatic width if the AP announces htcap 2", not T.survey_selectable(
        dict(by["North-Tower"], htcap=2), 0, modes))
    check("nor if the device lacks that station mode", not T.survey_selectable(
        by["South-Link"], 20, ["ap-ptmp", "sta-ptmp"]))
    check("nor an Ad-Hoc network (the device's mode field, not the radio mode)",
          by["North-Tower"]["op_mode"] == "Master" and not T.survey_selectable(
              dict(by["North-Tower"], op_mode="Ad-Hoc"), 20, modes))
    st = T.wireless_state(T.Config(S.cfg_text(S.Device(8, "u", "p", 1, 60,
                                                           station=True).running)), "v8",
                          dict(S.BOARD[8]))
    ch = T.survey_changes(by["South-Link"], st, 20, modes, False)
    check("PtP with 802.1X: PtP mode, the SSID without locking the MAC, WPA2 enterprise, "
          "automatic width",
          st["chanbw"] == 20 and ch == {"ptp": True, "ssid": "South-Link", "ap_mac": "",
                                        "security": "wpa2", "wpa_auth": "WPA-EAP", "chanbw": 0},
          str(ch))
    ch = T.survey_changes(by["North-Tower"], st, 30, modes, True)
    check('"Lock to AP": its MAC; and with a 30 MHz width in use, that width',
          ch.get("ap_mac") == "68:D7:9A:11:22:30" and ch.get("chanbw", st["chanbw"]) == 30,
          str(ch))
    ch = T.survey_changes(dict(by["Hidden-AP"]), st, 20, modes, False)
    check('hidden SSID: "any" and always its MAC', (ch["ssid"], ch["ap_mac"])
          == ("any", "68:D7:9A:11:22:50"))
    check('open ("-"): no security', T.survey_changes(by["Cafe-WiFi"], st, 20, modes,
                                                      False)["security"] == "none")
    check("the frequency in GHz, as the web shows it", (T.survey_frequency(by["North-Tower"]),
                                                        T.survey_frequency({"frequency": "5.18"}))
          == ("5.745", "5.180"))


def live_screen():
    print("=== live screen (UI.watch) with a real terminal ===")
    ui = T.UI.__new__(T.UI)
    ui.prog, ui.is_dialog, ui.backtitle = "dialog", True, "x"
    shown = []
    ui._run = lambda args, labels=None, extra=None: (shown.append(args), (0, ""))[1]
    real_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO("")
        ui.watch("Test", lambda: "once")
        check("no terminal: shown once, then it returns", len(shown) == 1
              and "--msgbox" in shown[0])
        master, slave = pty.openpty()
        sys.stdin = os.fdopen(slave, "r")
        before = termios.tcgetattr(slave)
        count = [0]

        def render():
            count[0] += 1
            return f"round {count[0]}"
        shown.clear()
        threading.Timer(0.7, lambda: os.write(master, b"\x1b[A")).start()   # an arrow key
        t0 = time.monotonic()
        ui.watch("Test", render, interval=0.1)
        elapsed = time.monotonic() - t0
        check("redrawn until a key is pressed", count[0] >= 4 and 0.6 < elapsed < 3
              and all("--infobox" in a for a in shown), f"{count[0]} rounds, {elapsed:.1f} s")
        check("leaves the terminal as it was", termios.tcgetattr(slave) == before)
        check("the box does not shrink between rounds", len({(a[-2], a[-1]) for a in shown}) == 1)
        os.close(master)
        sys.stdin.close()
    finally:
        sys.stdin = real_stdin
    import curses
    order = []

    class Screen:
        def erase(self):
            order.append("erase")
    live = T.LiveScreen("x")
    live.scr = Screen()
    real_endwin = curses.endwin
    curses.endwin = lambda: order.append("endwin")
    try:
        live.__exit__(None, None, None)
    finally:
        curses.endwin = real_endwin
    check("leaving a live screen empties it first: curses repaints it as the next one starts, "
          "before its data (not this one's old data meanwhile)", order == ["erase", "endwin"],
          str(order))


def title_widths():
    print("=== long titles are shown whole (the widths asked of dialog) ===")
    ui = T.UI.__new__(T.UI)
    ui.prog, ui.is_dialog, ui.backtitle = "dialog", True, "x"
    shown = []
    ui._run = lambda args, labels=None, extra=None: (shown.append(args), (0, ""))[1]
    title = tr("Firmware update {version} is available.", version="WA.v8.7.20")
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "110"
    try:
        for t in (title, "Hello", "x" * 150):
            ui.msgbox(t, "text")
            ui.yesno(t, "text")
            ui.inputbox(t, "text")
            ui.infobox(t, "text")
            ui.menu(t, "text", [("a", "one")])
            ui.textbox(t, "# airOS v8.7.20\n\n- Fixes.")
    finally:
        if old is None:
            del os.environ["COLUMNS"]
        else:
            os.environ["COLUMNS"] = old
    widths = [(a[2], int(a[5])) for a in shown]           # (the widget, its width)
    long_ = widths[:6]
    check("with a long title, the window leaves room for it (and the textbox, 20 columns more: "
          "dialog covers the start of its border)", all(
              w >= len(title) + (20 if kind == "--textbox" else 6) for kind, w in long_),
          str(long_))
    check("with a short one, the usual widths (the menu, as wide as its items need)",
          [w for _, w in widths[6:12]] == [50, 50, 50, 50, 16, 60], str(widths[6:12]))
    check("and never wider than the terminal (but its shadow)",
          all(w <= 108 for _, w in widths[12:])
          and max(w for kind, w in widths[12:] if kind != "--textbox") == 100, str(widths[12:]))


def date_page():
    print("=== System → Date Settings (the DateModel of the airOS 8 web) ===")

    def cfg(**kv):
        return T.Config("".join(f"{k.replace('__', '.')}={v}\n" for k, v in kv.items()))
    check("no keys: its model's defaults", T.date_settings(cfg()) == {
        "zone": "UTC", "startup": False, "date": "", "ntp": False,
        "server": "0.ubnt.pool.ntp.org"})
    real = T.date_settings(cfg(ntpclient__1__server="0.ubnt.pool.ntp.org",
                               ntpclient__1__status="enabled", ntpclient__status="enabled",
                               system__date__status="disabled", system__timezone="ART3"))
    check("the keys of a real LiteBeam 5AC (8.7.25)", real == {
        "zone": "ART3", "startup": False, "date": "", "ntp": True,
        "server": "0.ubnt.pool.ntp.org"}, str(real))
    check("the startup date, as the web reads it (YYYYMMDD0000 → MM/DD/YYYY)", [
        T.date_settings(cfg(system__date__timestamp=v))["date"] for v in (
            "202611080000", "2026-11-08", "20261108")] == ["11/08/2026", "", ""])
    invalid = tr("Invalid Startup Date")
    check('its rules: a valid date (empty is "invalid" too, as on the web) and an NTP server',
          [T.date_problem("date", v) for v in (
              "", "13/45/2026", "11/08/2026", "2026-11-08")] + [
              T.date_problem("server", " "), T.date_problem("server", "pool.ntp.org")] == [
              invalid, invalid, None, None, tr("NTP Server is required"), None])
    check("what it saves: NTP in its two keys and the date as YYYYMMDD0000",
          T.plan_date({"zone": "ART3", "startup": True, "date": "11/08/2026", "ntp": True,
                       "server": "pool.ntp.org"}) == {
              "system.timezone": "ART3", "system.date.status": "enabled",
              "system.date.timestamp": "202611080000", "ntpclient.status": "enabled",
              "ntpclient.1.status": "enabled", "ntpclient.1.server": "pool.ntp.org"})
    check("  (and with no date or server, it removes them: its configuration leaves out what "
          "is empty)",
          T.plan_date({"zone": "UTC", "startup": False, "date": "", "ntp": False,
                       "server": ""}) == {
              "system.timezone": "UTC", "system.date.status": "disabled",
              "system.date.timestamp": None, "ntpclient.status": "disabled",
              "ntpclient.1.status": "disabled", "ntpclient.1.server": None})
    g3 = T.time_zones({"feature.g3": "1"})
    check("its 33 zones (Gen3 boards: two of them differ) and their names", len(T.TIME_ZONES)
          == 33 and len(g3) == 33 and [i for i in range(33) if g3[i] != T.TIME_ZONES[i]]
          == [14, 15] and T.zone_label("ART3", {}) == "(GMT-03:00) Greenland, Buenos Aires"
          and T.zone_label("XYZ1", {}) == "XYZ1" and T.zone_short("ART3", {}) == "GMT-03:00")

    srv, d, dev, ui, app = start(8, 19210)
    title = tr("Date Settings")
    ok = run(app, "system_menu", [("menu", None)])
    row = dict([m for m in ui.menus if m[0] == tr("System")][-1][1]).get("d")
    check("in System: the zone and NTP", ok and row == tr(
        "Date settings: {zone}, NTP {state}", zone="UTC/GMT", state=tr("disabled")), row)
    art = str(next(i for i, (v, _) in enumerate(T.TIME_ZONES, 1) if v == "ART3"))
    ok = run(app, "date_menu", [("menu", "z"), ("menu", art), ("menu", "n"), ("menu", "s"),
                                ("input", " "), ("input", "pool.ntp.org"), ("menu", "d"),
                                ("input", ""), ("input", "13/45/2026"), ("input", "11/08/2026"),
                                ("menu", None)])
    want = {"system.timezone": "ART3", "ntpclient.status": "enabled",
            "ntpclient.1.status": "enabled", "ntpclient.1.server": "pool.ntp.org",
            "system.date.status": "enabled", "system.date.timestamp": "202611080000"}
    check("zone, NTP with its server and the startup date, with the web's messages", ok
          and msgs(ui, tr("Invalid value")) == [tr("NTP Server is required"), invalid, invalid]
          and {k: app.working.get(k) for k in want} == want, str(msgs(ui)))
    last = dict([m for m in ui.menus if m[0] == title][-1][1])
    check("  (and each row marked as changed)", last == {
        "z": "* " + tr("Time Zone: {zone}", zone="(GMT-03:00) Greenland, Buenos Aires"),
        "d": "* " + tr("Startup Date: {date}", date="11/08/2026"),
        "n": "* " + tr("NTP Client: {state}", state=tr("enabled")),
        "s": "* " + tr("NTP Server: {server}", server="pool.ntp.org")}, str(last))
    app.ui.script = [("menu", "2"), ("yesno", 0)]               # apply and save
    app.apply_menu()
    check("saved on the device", {k: d.saved.get(k) for k in want} == want)
    ui.log.clear()
    app.show_status()
    shown = [t for k, ti, t in ui.log if k == "textbox"]
    check("the status: the synced date and when NTP set the clock", shown and (
        f"{tr('Date')} ({tr('Synced')})") in shown[0] and tr(
            "NTP Client synced at {time}", time="")[:12] in shown[0], str(shown[:1])[:300])
    ok = run(app, "date_menu", [("menu", "n"), ("yesno", 0), ("menu", "n"), ("menu", None)])
    check("turning NTP off and on again: nothing pending", ok and not app.changes(),
          str(app.changes()))
    ok = run(app, "date_menu", [("menu", "d"), ("menu", "o"), ("menu", None)])
    check("turning the startup date off (its date stays, as on the web)", ok
          and app.changes() == [("system.date.status", "enabled", "disabled")],
          str(app.changes()))
    srv.shutdown()
    srv, d, dev, ui, app = start(6, 19211)
    ok = run(app, "system_menu", [("menu", None)])
    check("airOS 6: not there (another web)", ok and "d" not in dict(
        [m for m in ui.menus if m[0] == tr("System")][-1][1]))
    srv.shutdown()


def flows():
    print("=== Tools → Antenna alignment (simulator) ===")
    srv, d, dev, ui, app = start(8, 19201)
    ok = run(app, "tools_menu", [("menu", "a"), ("watch", 2), ("menu", None)])
    text = ui.watched[0][-1] if ui.watched else ""
    check("airOS 8: both ends, their chains and the CINR", ok and "Client-North-01" in text
          and tr("chains 0 / 1: {chains} (difference {diff} dB)", chains="-59 dBm / -62 dBm",
                 diff=3) in text and tr("CINR RX/TX {rx} / {tx} dB", rx=28, tx=27) in text,
          text)
    calls = [0]
    real = dev.status

    def flaky():
        calls[0] += 1
        if calls[0] == 2:
            raise T.AirOSError("connection lost")
        return real()
    dev.status = flaky
    ok = run(app, "align_antenna", [("watch", 3)])
    del dev.status
    check("if the device stops answering, it says so and keeps trying", ok
          and tr("The device does not answer: {error}", error="connection lost") in ui.watched[0][0]
          and "dBm" in ui.watched[0][1])
    dev.status = lambda: fixture("liteapgps_ap_ptmp_40mhz.json")
    ok = run(app, "align_antenna", [("menu", "2"), ("watch", 1)])
    del dev.status
    check("with two stations it asks which one", ok and "NanoBeam-shed1" in ui.watched[0][0])

    print("=== Tools → Site survey (on an access point: only looking) ===")
    ok = run(app, "site_survey", [("menu", "1"), ("menu", None)])
    lst = [m for m in ui.menus if m[0] == tr("Site survey")]
    items = lst[-1][1] if lst else []
    check("the list, from the strongest signal to the weakest, no marks on an AP", ok
          and [t for t, _ in items] == ["1", "2", "3", "4", "r"]
          and "North-Tower" in items[0][1] and "Cafe-WiFi" in items[3][1]
          and not any(text.startswith("*") for _, text in items), str(items))
    check("viewing one: its data", any("North-Tower-AP" in t for t in msgs(ui)))
    aps = app._survey_scan(tr("Site survey"))
    ui.log.clear()
    app._survey_ap(dict(aps[0], signal=None, noise=None), False)
    check("  without its levels: ? (as the list), not None", msgs(ui) and tr(
        "Signal/noise: {signal} / {noise} dBm", signal="?", noise="?") in msgs(ui)[0],
          str(msgs(ui)))
    calls = [0]
    real_survey = dev.site_survey

    def flaky(again=False):
        calls[0] += 1
        if calls[0] == 2:
            raise T.AirOSError("survey.json.cgi answered HTTP 500: x")
        return real_survey(again=again)
    dev.site_survey = flaky
    ok = run(app, "site_survey", [("menu", None)])
    check("a read lost while it scans: the next one, and its list", ok and calls[0] > 2
          and [m for m in ui.menus if m[0] == tr("Site survey")], str(calls))

    def broken(again=False):
        if again:
            raise T.AirOSError("survey.json.cgi answered HTTP 500: x")
        return real_survey()
    dev.site_survey = broken
    ok = run(app, "site_survey", [])
    check("  three in a row: it says why and goes back (not to the main menu with an error)",
          ok and msgs(ui, tr("Site survey")) == ["survey.json.cgi answered HTTP 500: x"],
          str(msgs(ui)))
    del dev.site_survey
    real_status = dev.status
    first = real_status()
    other = json.loads(json.dumps(first))
    other["wireless"]["sta"][0]["mac"] = "02:00:00:00:00:99"
    other["wireless"]["sta"][0].setdefault("remote", {})["hostname"] = "Another-Station"
    reads = iter([first, first, other])
    dev.status = lambda: next(reads)
    ok = run(app, "align_antenna", [("watch", 2)])
    del dev.status
    shown = ui.watched[0] if ui.watched else ["", ""]
    check("alignment on an access point with one station: that one; if another one takes its "
          "place, not its signal as if it were the same", ok and "Client-North-01" in shown[0]
          and "Another-Station" not in shown[1] and tr(
              "No link: the device is not connected (to an access point, or no station is "
              "connected to it). The signal shows up as soon as there is one.") in shown[1],
          str(shown))
    srv.shutdown()

    print("=== site survey from a station: choosing an access point ===")
    srv, d, dev, ui, app = start(8, 19202, station=True)
    ok = run(app, "wireless_menu", [("menu", "z"), ("menu", "1"), ("menu", "l"),
                                    ("input", "north-key-1"), ("menu", None)])
    k = keys(app)
    check('"Lock to AP": its MAC, the WPA key and the automatic width, as the web does', ok
          and k.get("wpasupplicant.profile.1.network.1.bssid") == "68:D7:9A:11:22:30"
          and k.get("wpasupplicant.profile.1.network.1.psk") == "north-key-1", str(k))
    check("  (left prepared, not sent)", any(
        tr('Prepared, not sent yet: apply it with "Apply changes" (with test mode).') in t
        for t in msgs(ui)) and not d.events)
    app.set_loaded(app.original_text)
    ok = run(app, "site_survey", [("menu", "2"), ("menu", "s"), ("input", "anon"),
                                  ("input", "alice"), ("input", "pw")])
    k = keys(app)
    check("PtP with 802.1X: becomes a PtP station with WPA2 enterprise", ok
          and k.get("radio.1.ptpmode") == "1" and k.get("wireless.1.ssid") == "South-Link"
          and k.get("wpasupplicant.profile.1.network.1.key_mgmt.1.name") == "WPA-EAP"
          and k.get("wpasupplicant.profile.1.network.1.identity") == "alice", str(k))
    app.set_loaded(app.original_text)
    ok = run(app, "site_survey", [("menu", "1"), ("menu", "s"), ("input", None), ("menu", None)])
    check("cancelling the key: nothing prepared", ok and not keys(app))
    ok = run(app, "site_survey", [("menu", "4"), ("menu", None)])
    check("one that is not airMAX: only its data and why it cannot be chosen", ok and any(
        "Cafe-WiFi" in t and tr("Selectable SSIDs must be visible, have compatible channel "
                                "bandwidth and security settings, and must be compatible with "
                                "airMAX AC technology.") in t for t in msgs(ui)))
    marks = [m for m in ui.menus if m[0] == tr("Site survey")][-1][1]
    check('  (in the list, "*" on the ones that can be chosen)', [t[0] for _, t in marks[:4]]
          == ["*", "*", " ", " "], str(marks[:4]))
    ok = run(app, "site_survey", [("menu", "r"), ("menu", None)])
    check('"Scan again"', ok and len([m for m in ui.menus
                                      if m[0] == tr("Site survey")]) >= 2)
    srv.shutdown()

    print("=== System → change the password (airOS 8) ===")
    srv, d, dev, ui, app = start(8, 19203)
    ok = run(app, "system_menu", [("menu", "p"), ("password", "wrong"),
                                  ("password", "north-tower-26"), ("password", "north-tower-26"),
                                  ("yesno", 0), ("menu", None)])
    check("wrong current password: it says so and changes nothing", ok
          and msgs(ui, tr("Change Password")) == [tr("Wrong Current Password")]
          and d.password == "ubnt" and dev.password == "ubnt")
    ok = run(app, "change_password", [("password", "ubnt"), ("password", "short"),
                                      ("password", "north-tower-26"), ("password", "other"),
                                      ("password", "north-tower-26"),
                                      ("password", "north-tower-26"), ("yesno", 1)])
    check('rejects a short one and a repeat that differs; "No" sends nothing', ok
          and msgs(ui, tr("Invalid value")) == [tr("Use at least 12 characters."),
                                                 tr("Passwords should match.")]
          and d.password == "ubnt")
    ok = run(app, "change_password", [("password", "ubnt"), ("password", "north-tower-26"),
                                      ("password", "north-tower-26"), ("yesno", 0)])
    check("changes it: the device saves it and the program still logs in", ok
          and msgs(ui) == [tr("Administrator password successfully changed.")]
          and d.password == "north-tower-26" and "password" in d.events
          and d.saved["users.1.password"] != "-" and dev.password == "north-tower-26")
    dev.cookies.clear()
    check("  (a new session logs in with the new one)", dev.status().get("host") is not None)
    ok = run(app, "change_password", [("password", None)])
    check("cancel: nothing", ok)
    srv.shutdown()

    print("=== airOS 6: what it does not do yet ===")
    srv, d, dev, ui, app = start(6, 19204)
    ok = run(app, "change_password", [])
    check("password: on airOS 6, from its web", ok and msgs(ui) == [tr(
        "On airOS 6 the password is changed from the device's web (System page).")])
    ok = run(app, "site_survey", [])
    check("site survey: on airOS 6, from its web", ok and msgs(ui) == [tr(
        "On airOS 6 the site survey is in the device's web (Tools).")])
    ok = run(app, "align_antenna", [("watch", 1)])
    check("alignment on airOS 6: the local signal", ok
          and tr("chains 0 / 1: {chains} (difference {diff} dB)", chains="-63 dBm / -65 dBm",
                 diff=2) in ui.watched[0][0], ui.watched[0][0] if ui.watched else "")
    srv.shutdown()


def system_page():
    print("=== System → Device name, as on the System page of the airOS 8 web ===")
    srv, d, dev, ui, app = start(8, 19205)
    model = S.BOARD[8]["board.name"]
    label = tr("Device name")

    def name_row():
        return dict([m for m in ui.menus if m[0] == tr("System")][-1][1])["n"]
    ok = run(app, "system_menu", [("menu", None)])
    check("the menu, in the web's order: name, device, management radio, date and time, "
          "accounts, location, password, reboot and the backups",
          ok and [t for t, _ in [m for m in ui.menus if m[0] == tr("System")][-1][1]]
          == ["v", "n", "e", "m", "d", "a", "l", "p", "r", "f", "s", "b", "u"]
          and name_row() == f"{label}: AP-North-Tower",
          name_row())
    ok = run(app, "system_menu", [("menu", "n"), ("input", "   "), ("menu", "n"),
                                  ("input", "x" * 65), ("menu", "n"),
                                  ("input", "x" * 63 + "😀"), ("menu", None)])
    check("required, and 1 to 64 characters (counted as JavaScript does)", ok
          and msgs(ui, tr("Invalid value")) == [
              tr("The device name is required."),
              tr("The device name must be between 1 and {max} characters.", max=64),
              tr("The device name must be between 1 and {max} characters.", max=64)]
          and not app.changes())
    ok = run(app, "system_menu", [("menu", "n"), ("input", "Zürich Tower 😀"), ("menu", None)])
    check("any character, as on the web (without the ASCII rule of airOS 6)", ok
          and keys(app) == {"resolv.host.1.name": "Zürich Tower 😀"}
          and app.working.get("resolv.host.1.status") == "enabled"
          and name_row() == f"* {label}: Zürich Tower 😀", str(keys(app)))
    check("  (left prepared, not sent)", any(tr(
        'Change prepared (not sent to the device yet).\n\nChanged keys:\n  {keys}\n\n'
        'Use "Apply changes" in the main menu.',
        keys="resolv.host.1.name\n  resolv.host.1.status") == t for t in msgs(ui, label))
          and not d.events)
    ok = run(app, "system_menu", [("menu", "n"), ("input", model), ("menu", None)])
    check("the model's name is saved empty, and the model's name still shows", ok
          and keys(app) == {"resolv.host.1.name": ""} and name_row() == f"* {label}: {model}")
    blank = app.original.copy()
    blank.set("resolv.host.1.name", "")
    app.set_loaded(blank.text())
    ok = run(app, "system_menu", [("menu", "n"), ("input", model), ("menu", None)])
    check("no name in the configuration: it shows the model's, and choosing it is no change",
          ok and name_row() == f"{label}: {model}" and not app.changes())
    ok = run(app, "quick_settings", [("menu", "1"), ("input", "South-Tower"), ("menu", "1"),
                                     ("input", model), ("menu", None)])
    quick = [m for m in ui.menus if m[0] == tr("Quick settings")][-1][1]
    check("the quick settings use the same (showing and saving)", ok and not app.changes()
          and dict(quick)["1"] == f"{label}: {model}", str(quick[:1]))

    print("=== System → backups ===")
    ok = run(app, "system_menu", [("menu", "b"), ("input", None), ("menu", "u"), ("menu", None),
                                  ("menu", None)])
    check("saving and loading a backup are in System", ok
          and ui.inputs[-1] == os.path.join(os.path.expanduser("~"), f"WA-{S.MAC}.cfg")
          and any(m[0] == tr("Load backup") for m in ui.menus))

    print("=== System → reboot; the main menu without backups or reboot ===")
    ok = run(app, "system_menu", [("menu", "r"), ("yesno", 1), ("menu", None)])
    check('rebooting is in System ("No" sends nothing)', ok and "reboot" not in d.events
          and [ti for k, ti, _ in ui.log if k == "yesno"] == [tr("Reboot")])
    ui.script, ui.log[:] = [("menu", None)], []
    app.run()
    main = [m for m in ui.menus if m[0] == app.title()][-1][1]
    check('no "8", "9" or "r"; "y" is System, with the name, reboot and the backups',
          not {"8", "9", "r"} & {t for t, _ in main}
          and dict(main)["y"] == tr("System: firmware, device, accounts, location, maintenance, "
                                    "backups"),
          str(main))
    srv.shutdown()

    print("=== System → Device name on airOS 6 ===")
    srv, d, dev, ui, app = start(6, 19206)
    ok = run(app, "system_menu", [("menu", "n"), ("input", "Zürich"), ("menu", "n"),
                                  ("input", S.BOARD[6]["board.name"]), ("menu", None)])
    check("the old rule (1 to 64 ASCII), and the model's name is saved as it is", ok
          and msgs(ui, tr("Invalid value")) == [tr("Between 1 and 64 printable ASCII "
                                                   "characters.")]
          and keys(app) == {"resolv.host.1.name": S.BOARD[6]["board.name"]}, str(keys(app)))
    srv.shutdown()


def maintenance():
    print("=== System → Download Support Info (airOS 8), as the web does ===")
    check('the name: the firmware prefix and the MAC without ":" (WA-68D79A98FBFF.sup)',
          T.support_filename("WA.ar934x.v8.7.19.48279.250811.0636",
                             {"board.hwaddr": "68:D7:9A:98:FB:FF"}) == "WA-68D79A98FBFF.sup"
          and "/" not in T.support_filename("../x", {"board.hwaddr": "a/b"}))
    check("the wait after a reset: 25 s plus board.reboot", T.reset_wait(
        {"board.reboot": "40"}) == 65 and T.reset_wait({}) == 25)
    # (an airOS 8 older than its first setup: after its reset, ubnt/ubnt; 8.7.x's, below)
    srv, d, dev, ui, app = start(8, 19207, setup_wizard=False)
    title = tr("Download Support Info")
    folder = os.path.join(XDG, "support")
    os.makedirs(folder, exist_ok=True)
    name = f"WA-{S.MAC}.sup"
    path = os.path.join(folder, name)
    ok = run(app, "system_menu", [("menu", "s"), ("input", f" {folder} "), ("menu", None)])
    content = open(path, "rb").read() if os.path.isfile(path) else b""
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            names = tar.getnames()
    except tarfile.TarError:
        names = []
    check("given a folder: inside it, with the web's name, for the user only", ok
          and oct(os.stat(path).st_mode & 0o777) == "0o600"
          and f"support-WA-{S.MAC.lower()}/status.cgi.txt" in names
          and msgs(ui, title) == [tr(
              "Saved in {path} ({size} KB).\n\nIt has the device's configuration and its "
              "logs: share it only with whom you trust, such as Ubiquiti support.", path=path,
              size=max(1, round(len(content) / 1024)))], str(names))
    check("it offers the home folder with that name", ui.inputs[-1] == os.path.join(
        os.path.expanduser("~"), name), ui.inputs[-1:])
    check("  (and warns that it takes a while)", (
        title, tr("Please be patient while we are generating the support file "
                  "(about half a minute).")) in ui.infos)
    ok = run(app, "download_support_info", [("input", path), ("yesno", 1)])
    check('if it exists it asks; "No" leaves it alone', ok and open(path, "rb").read() == content
          and [t for k, t, _ in ui.log if k == "yesno"] == [title])
    with open(path, "wb") as f:
        f.write(b"old")
    ok = run(app, "download_support_info", [("input", path), ("yesno", 0)])
    check('"Yes" replaces it', ok and open(path, "rb").read()[:2] == b"\x1f\x8b")
    ok = run(app, "download_support_info", [("input", None)]) and run(
        app, "download_support_info", [("input", "   ")])
    check("cancelling or leaving it empty: nothing", ok and not msgs(ui))

    def broken():
        raise T.AirOSError("support.cgi answered HTTP 500: no")
    dev.support_info = broken
    other = os.path.join(folder, "other.sup")
    ok = run(app, "download_support_info", [("input", other)])
    del dev.support_info
    check("if the device fails: the web's notice and the detail", ok and msgs(ui) == [
        tr("Unexpected error occurred while getting Support Info")
        + "\n\nsupport.cgi answered HTTP 500: no"] and not os.path.exists(other))
    bad = os.path.join(folder, "missing", "x.sup")
    ok = run(app, "download_support_info", [("input", bad)])
    check("if it cannot be saved: it says so", ok and len(msgs(ui, "Error")) == 1 and msgs(
        ui, "Error")[0].startswith(tr("Could not save {path}: {error}", path=bad, error="")))

    print("=== System → Reset to Factory Defaults (airOS 8), as the web does ===")
    title = tr("Reset to Factory Defaults")
    d.set_password("pre-reset-secret-26")
    dev.password = "pre-reset-secret-26"
    for c in (d.saved, d.system, d.running):
        c["resolv.host.1.name"] = "Before-The-Reset"
    app.load()
    app.working.set("radio.1.txpower", "12")
    ok = run(app, "system_menu", [("menu", "f"), ("yesno", 1), ("menu", None)])
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
    check('it asks as the web does and warns of what is lost; "Cancel" sends nothing', ok
          and "reset" not in d.events and len(asked) == 1
          and asked[0].startswith(tr("Do you want to reset the device to defaults?"))
          and tr("The {count} unapplied change(s) are lost too.", count=1) in asked[0]
          and "http://192.168.1.20/" in asked[0], asked[:1])
    before = set(glob.glob(os.path.join(T.BACKUP_DIR, "*.cfg")))
    ui.script, ui.log[:] = [("menu", "y"), ("menu", "f"), ("yesno", 0)], []
    app.run()                                     # it ends by itself
    ended = msgs(ui, title)
    new = set(glob.glob(os.path.join(T.BACKUP_DIR, "*.cfg"))) - before
    check('"Reset": sent, the session ends and it says where the device will be', not ui.script
          and "reset" in d.events and len(ended) == 1 and ended[0].startswith(tr(
              "Device has been reset to defaults. Please wait while it reboots..."))
          and tr("In about {seconds} s it answers at {url} (user {user}, password "
                 "{password}). airos-tui ends this session: connect again then.", seconds=65,
                 url="http://192.168.1.20/", user="ubnt", password="ubnt") in ended[0],
          ended[:1])
    check("  (first it saved the device's backup, as its web gives it)", len(new) == 1
          and tr("Local backup: {path}", path=next(iter(new))) in ended[0]
          and open(next(iter(new))).read().startswith("### PART ###")
          and "resolv.host.1.name=Before-The-Reset" in open(next(iter(new))).read())
    check("  (the device is back to its factory defaults: user and password ubnt)",
          d.password == "ubnt" and d.saved["resolv.host.1.name"] == "AP-North-Tower")
    srv.shutdown()

    # a firmware with its first setup (8.7.x, as the device says before its login): after the
    # reset its web asks for the country, its terms and a new administrator, not ubnt/ubnt
    srv, d, dev, ui, app = start(8, 18977, setup_wizard=True)
    ok = run(app, "system_menu", [("menu", "f"), ("yesno", 1), ("menu", None)])
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
    ui.script, ui.log[:] = [("menu", "y"), ("menu", "f"), ("yesno", 0)], []
    app.run()
    ended = msgs(ui, title)
    check("with its first setup (8.7.x): it warns of it, before and after, instead of "
          "ubnt/ubnt; and the device waits for it", ok and asked and ended
          and "http://192.168.1.20/" in asked[0] and "ubnt" not in asked[0]
          and tr("In about {seconds} s it answers at {url} and waits for its first setup (the "
                 "country, its terms and the administrator's new user and password), which "
                 "airos-tui asks for when you connect again, as its web does. airos-tui ends "
                 "this session.", seconds=65, url="http://192.168.1.20/") in ended[0]
          and "ubnt" not in ended[0].split(tr("Local backup: {path}", path=""))[0]
          and d.setup_pending, str((asked[:1], ended[:1])))
    srv.shutdown()

    print("=== airOS 6: the reset and the support info, from its web ===")
    srv, d, dev, ui, app = start(6, 19208)
    ok = run(app, "system_menu", [("menu", "f"), ("menu", "s"), ("menu", None)])
    check("it says so and sends nothing", ok and msgs(ui) == [
        tr("On airOS 6 the reset to factory defaults is in the device's web (System page)."),
        tr("On airOS 6 the support info is downloaded from the device's web (System page).")]
          and "reset" not in d.events)
    srv.shutdown()


def web_backups():
    print("=== backups: the device's (cfg.cgi) and airos-tui's ===")
    lines = "\n".join(f"radio.{i}.x=1" for i in range(12))
    web = f"### PART ###\n## 1\nx\n### PART ###\n{lines}\nusers.1.password=$1$a$b\n"
    check('it tells the web one, the airos-tui one (passwords "-") and what is not a backup',
          (T.backup_kind(web), T.backup_kind(f"{lines}\nusers.1.password=-\n"),
           T.backup_kind("hello\n"), T.backup_kind("### PART ###\nnothing\n"))
          == ("web", "airos-tui", None, None))
    check("its configuration is the last part", T.backup_config_part(web).startswith(
        "radio.0.x=1") and "## 1" not in T.backup_config_part(web))
    sha512 = (f"### PART ###\n## 1\n##WA.v8.7.25\n#board.name=x\n### PART SHA512 ###\n"
              f"{lines}\nusers.1.password=$6$a$b\n")
    check('  also after "### PART SHA512 ###" (8.7.x, with the password in SHA-512): its '
          "backupcfg splits on /^### PART.*###$/", T.backup_kind(sha512) == "web"
          and T.backup_config_part(sha512).startswith("radio.0.x=1")
          and "PART" not in T.backup_config_part(sha512) and "#board" not in
          T.backup_config_part(sha512))
    check("the name, as the device gives it", T.backup_filename(
        "WA.ar934x.v8.7.19", {"board.hwaddr": "68:D7:9A:98:FB:FF"}) == "WA-68D79A98FBFF.cfg")

    print("=== System → save a backup (airOS 8), as the web does ===")
    srv, d, dev, ui, app = start(8, 19209)
    folder = os.path.join(XDG, "web-backups")
    os.makedirs(folder, exist_ok=True)
    name = f"WA-{S.MAC}.cfg"
    path = os.path.join(folder, name)
    ok = run(app, "system_menu", [("menu", "b"), ("input", folder), ("menu", None)])
    content = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    check("given a folder: the device's file with its name, for the user only", ok
          and oct(os.stat(path).st_mode & 0o777) == "0o600" and content.startswith("### PART ###")
          and f"users.1.password={d.pw_hash}" in content
          and ui.inputs[-1] == os.path.join(os.path.expanduser("~"), name)
          and msgs(ui, tr("Back Up Configuration")) == [tr(
              "Saved in {path} ({size} KB).\n\nIt has the whole configuration, with the hashes "
              "of its passwords: keep it safe.", path=path,
              size=max(1, round(len(content.encode()) / 1024)))], content[:40])

    print("=== System → load a web backup (airOS 8): upload, discard, apply ===")

    def load(file, *rest):
        return run(app, "system_menu", [("menu", "u"), ("menu", "o"), ("input", file), *rest,
                                        ("menu", None)])
    ok = load(path, ("yesno", 1))
    check('it asks before uploading it; "No" sends nothing', ok and not d.backup_loaded
          and "backup-uploaded" not in d.events
          and ui.inputs[-1] == os.path.join(os.path.expanduser("~"), name))
    ok = load(path, ("yesno", 0), ("yesno", 255), ("yesno", 1))
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Configuration")]
    check('it uploads it and asks what the web asks (no choice made: it asks again); "Discard"', ok
          and "backup-uploaded" in d.events and "backup-discarded" in d.events
          and not d.backup_loaded and len(asked) == 2
          and asked[0].startswith(tr("Configuration backup file uploaded."))
          and msgs(ui, tr("Configuration")) == [tr("Backup discarded: the device keeps its "
                                                   "configuration.")], str(asked))
    changed = os.path.join(folder, "changed.cfg")
    with open(changed, "w", encoding="utf-8") as f:
        f.write(content.replace("resolv.host.1.name=AP-North-Tower",
                                "resolv.host.1.name=From-Backup"))
    app.working.set("radio.1.txpower", "12")
    ok = load(changed, ("yesno", 0), ("yesno", 0))
    question = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Upload Configuration")]
    done = msgs(ui, tr("Applying the backup"))
    check('"Apply": the device uses it and saves it, and it is read again', ok
          and "backup-applied" in d.events and d.saved["resolv.host.1.name"] == "From-Backup"
          and app.original.get("resolv.host.1.name") == "From-Backup" and not app.changes()
          and len(done) == 1 and done[0].startswith(tr("Backup applied.")), str(done))
    check("  (first it warned that the unapplied changes are lost)", len(question) == 1
          and tr("The {count} unapplied change(s) are lost.", count=1) in question[0])
    bad = os.path.join(folder, "rejected.cfg")
    with open(bad, "w", encoding="utf-8") as f:
        f.write(content.replace("radio.status=enabled", "radio.status=disabled"))
    ok = run(app, "_do", [("menu", "o"), ("input", bad), ("yesno", 0)], app.restore_backup)
    check("if the device rejects it: what it answers", ok and not d.backup_loaded
          and msgs(ui, "Error") == ["Invalid configuration file"], str(msgs(ui)))
    masked = os.path.join(folder, "airos-tui.cfg")
    c = app.original.copy()
    c.set("wireless.1.ssid", "From-TUI")
    with open(masked, "w", encoding="utf-8") as f:
        f.write(c.text())
    uploads = d.events.count("backup-uploaded")
    ok = run(app, "restore_backup", [("menu", "o"), ("input", masked)])
    loaded = msgs(ui, tr("Backup loaded"))
    check('one from airos-tui (passwords "-"): not uploaded; it stays as pending changes', ok
          and d.events.count("backup-uploaded") == uploads and len(loaded) == 1
          and tr("It is a backup airos-tui saved without the passwords, so it is not uploaded "
                 "as the web does: it is loaded as pending changes.") in loaded[0]
          and [k for k, _, _ in app.changes()] == ["wireless.1.ssid"], str(app.changes()))
    app.set_loaded(app.original_text)

    print("=== a backup the web left uploaded without applying it ===")
    dev.upload_config(name, content.encode())
    discarded = d.events.count("backup-discarded")
    ui.script, ui.log[:] = [("yesno", 1), ("menu", None)], []
    app.run()
    check("on starting it asks the same as the web", not ui.script
          and d.events.count("backup-discarded") == discarded + 1 and not d.backup_loaded)
    dev.upload_config(name, content.encode())
    ok = run(app, "reload", [("yesno", 1)])
    check("  (and on reading the device again)", ok and not d.backup_loaded
          and d.events.count("backup-discarded") == discarded + 2)

    print("=== the automatic backup (before applying or resetting) ===")

    def broken():
        raise T.AirOSError("cfg.cgi answered HTTP 500")
    dev.backup_config = broken
    fallback = app._safety_backup()
    del dev.backup_config
    check("if the device does not give its backup: the configuration read",
          open(fallback, encoding="utf-8").read() == app.original_text)
    check("  (if it does, its file)", open(app._safety_backup(), encoding="utf-8").read()
          .startswith("### PART ###"))

    print("=== applying a backup with another password ===")
    other = os.path.join(folder, "other-password.cfg")
    with open(other, "w", encoding="utf-8") as f:
        f.write(content.replace(f"users.1.password={d.pw_hash}", "users.1.password=$1$x$other"))
    real_apply = dev.apply_config

    def apply_and_drop():
        res = real_apply()
        d.sessions.clear()                    # (as if the device dropped the session)
        return res
    dev.apply_config = apply_and_drop
    ok = run(app, "_do", [("menu", "o"), ("input", other), ("yesno", 0), ("yesno", 0),
                          ("input", None)], app.restore_backup)
    del dev.apply_config
    relogin = [t for k, ti, t in ui.log if k == "input" and ti == tr("Applying the backup")]
    check("it applies it, asks for the backup's user and password (as the login page of its "
          "web does) and, without them, ends the session saying why", ok and d.password is None
          and len(relogin) == 1 and ui.inputs[-1] == dev.username
          and msgs(ui, tr("Applying the backup")) == [tr(
              "The backup is applied and the device answers at {address}, but airos-tui cannot "
              "log in again: the backup has another user or password. Connect again with them.",
              address=dev.base)], str(msgs(ui)))
    srv.shutdown()

    print("=== airOS 6: the backups, as before ===")
    srv, d, dev, ui, app = start(6, 19210)
    ok = run(app, "save_backup", [])
    saved = msgs(ui, tr("Backup saved"))
    check("saving: the configuration read, in the backups folder", ok and len(saved) == 1
          and open(saved[0], encoding="utf-8").read() == app.original_text)
    srv.shutdown()


def antenna_discovery():
    print("=== Device discovery from the antenna (the web's DiscoveryView) ===")
    check("the version as its getFirmwareVersion gives it", [T.web_fw_version(v) for v in (
        "WA.ar934x.v8.7.25.48568.260730.1010", "XW.ar934x.v6.3.16.33429.241004.1620",
        "e50.v2.0.9-hotfix.7.5622731.230615.0857", "", None, "garbage")] == [
        "v8.7.25", "v6.3.16", "v2.0.9-hotfix.7", "", "", ""])
    check('its "Mode" column: STA, AP or -', [T.found_mode(v) for v in (2, 3, "3", 0, None)]
          == ["STA", "AP", "AP", "-", "-"])
    found = {}
    T.merge_found(found, [{"hwaddr": "68:D7:9A:00:00:01", "hostname": "old"},
                          {"hwaddr": "68:D7:9A:00:00:02", "hostname": "other"}])
    T.merge_found(found, [{"hwaddr": "68:d7:9a:00:00:01", "hostname": "new"}])
    check("one row per MAC, with the newest data (as its table)", sorted(
        d["hostname"] for d in found.values()) == ["new", "other"], str(found))
    srv, d, dev, ui, app = start(8, 18960)
    real_req = dev._req
    dev._req = lambda *a, **k: (200, {}, b'{ "devices": [\n] }\n')    # (the real 8.7.25)
    empty = dev.device_discovery(500)
    dev._req = lambda *a, **k: (200, {}, b'{"devices": [{"hostname": "A\x01B"}]}')
    stripped = dev.device_discovery(500)
    dev._req = lambda *a, **k: (500, {}, b"<html>Internal Server Error</html>")
    try:
        dev.device_discovery(500)
        broken = None
    except T.AirOSError as e:
        broken = str(e)
    dev._req = real_req
    check("it reads the real answer (no devices), without the unprintable as its web, and an "
          "error",
          empty == [] and stripped == [{"hostname": "AB"}] and broken
          and "discovery.cgi" in broken, str((empty, stripped, broken)))
    d.events.clear()
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", "1"), ("yesno", 1), ("menu", "r"),
                                 ("menu", None), ("menu", None)])
    title = tr("Device Discovery")
    asks = [x for x in d.events if x.startswith("discovery:")]
    check('it asks as its web does on opening: 0.5 s, 1 s and 5 s (and again with "Scan")', ok
          and asks == ["discovery:500", "discovery:1000", "discovery:5000"] * 2, str(asks))
    lists = [dict(items) for t, items, _ in ui.menus if t == title]
    check("the list: IP, name, product, mode and version, by IP", lists and lists[0] == {
        "1": "127.0.0.21      Client-North-01 · LiteBeam 5AC · STA · v8.7.19",
        "2": "127.0.0.254     Gateway · EdgeRouter X · v2.0.9-hotfix.7",
        "r": tr("Scan")}, str(lists[:1]))
    shown = [t for k, ti, t in ui.log if k == "yesno" and ti == "Client-North-01"]
    check("its data, with the columns of the web's table", shown and shown[0].splitlines()[:7]
          == [f"{tr('MAC Address'):<17}  68:D7:9A:11:22:33",
              f"{tr('Device Name'):<17}  Client-North-01", f"{tr('Mode'):<17}  STA",
              f"{'SSID':<17}  North-Tower", f"{tr('Product'):<17}  LiteBeam 5AC",
              f"{tr('Firmware'):<17}  v8.7.19", f"{tr('IP Address'):<17}  127.0.0.21"],
          str(shown[:1]))
    check("  (and a notice while it scans)", (title, tr("Scanning, please wait...")) in ui.infos)
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", "2"), ("yesno", 1), ("menu", None),
                                 ("menu", None)])
    shown = [t for k, ti, t in ui.log if k == "yesno" and ti == "Gateway"]
    check('a device that is not airMAX: mode "-" and its other addresses', ok and shown
          and f"{tr('Mode'):<17}  -" in shown[0]
          and f"{tr('Addresses'):<17}  127.0.0.253" in shown[0], str(shown[:1]))
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", "1"), ("yesno", 0), ("menu", None),
                                 ("menu", None)])
    told = msgs(ui, tr("Connect"))
    check('"Connect" to a device this PC cannot reach: it says so and stays on this one', ok
          and told and "127.0.0.21" in told[0] and app.next_target is None, str(told))
    real_reachable = T.reachable
    T.reachable = lambda host, timeout=2.0, ports=(443, 80): True
    try:
        app.working.set("radio.1.txpower", "13")
        app.ui.script = [("menu", "d"), ("menu", "1"), ("yesno", 0), ("yesno", 0)]
        app.ui.log.clear()
        ended = not app._do(app.tools_menu)
    finally:
        T.reachable = real_reachable
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Connect")]
    check("if it can reach it: it asks about the unapplied changes and leaves this session to go "
          "to that device",
          ended and asked and app.next_target == "127.0.0.21", str(asked))
    app.next_target = None
    app.working = app.original.copy()
    seen = []
    T.reachable = lambda host, timeout=2.0, ports=(443, 80): not seen.append((host, ports))
    try:
        app.ui.script, app.ui.log[:] = [], []
        ended = not app._do(lambda: app._switch_to("https://127.0.0.21:8443"))
    finally:
        T.reachable = real_reachable
    check("a web on another port (the active search gives its URL): asked there, and connecting "
          "goes to it", ended and seen == [("127.0.0.21", (8443,))]
          and app.next_target == "https://127.0.0.21:8443", str(seen))
    app.next_target = None
    got = {}
    real_screen = T.discovery_screen
    T.discovery_screen = lambda *a, current=(), **k: got.update(current=set(current))
    try:
        app.pc_discovery()
    finally:
        T.discovery_screen = real_screen
    own = {str((i.get("status") or {}).get("ipaddr") or "")
           for i in app.status.get("interfaces") or []} - {"", "0.0.0.0"}
    check("discovery from this computer: this device is the one at its own addresses too (if "
          "connected by a name, still marked)", own and got.get("current") == own | {
              app.dev.host}, str((own, got)))
    real_found = d.discovered
    d.discovered = lambda: [{"hwaddr": "68:D7:9A:98:FB:FF", "hostname": "AP-North-Tower",
                             "wmode": 3, "ipv4": "127.0.0.1", "product": "LiteBeam 5AC"}]
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", "1"), ("menu", None), ("menu", None)])
    lists = [(t, dict(items)) for (ti, items, _), (k, _t, t) in zip(
        ui.menus[-2:], [x for x in ui.log if x[0] == "menu"][-2:]) if ti == title]
    check('if the antenna finds itself: it marks it and does not offer "Connect"', ok
          and lists and lists[0][1]["1"].startswith("→ 127.0.0.1 ")
          and lists[0][0].endswith(tr("→ = the device you are connected to."))
          and msgs(ui, "AP-North-Tower")[0].endswith(tr("You are connected to this device.")),
          str(lists))
    d.discovered = lambda: []
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", None), ("menu", None)])
    last = [(t, dict(items)) for (ti, items, _), (k, _t, t) in zip(
        ui.menus[-2:], [x for x in ui.log if x[0] == "menu"][-2:]) if ti == title]
    check('no devices (as the real antenna): "No devices found."', ok and last
          and last[0][0] == tr("No devices found.") and list(last[0][1]) == ["r"], str(last))
    d.discovered = real_found

    def no_answer(ms):
        raise T.AirOSError("discovery.cgi answered HTTP 500: x")
    real_disc, app.dev.device_discovery = app.dev.device_discovery, no_answer
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", None), ("menu", None)])
    app.dev.device_discovery = real_disc
    check("if the antenna does not answer: it says so", ok and msgs(ui, title) == [
        "discovery.cgi answered HTTP 500: x"], str(msgs(ui)))
    srv.shutdown()
    srv, d, dev, ui, app = start(6, 18961)
    ok = run(app, "tools_menu", [("menu", "d"), ("menu", None)])
    check("airOS 6: it is in the device's web", ok and msgs(ui, title) == [
        tr("On airOS 6 the device discovery is in the device's web (Tools).")])
    srv.shutdown()


def network_tools():
    print("=== ping, traceroute and speed test (the web's network tools) ===")
    check("destination: an IPv4 or IPv6 address, as the web's patterns (no zone: fe80::1%eth0, "
          "which the web does not take)", [T.valid_ip(v) for v in (
              "192.168.1.1", "fe80::1", "300.1.1.1", "router", "", "fe80::1%eth0")]
          == [True, True, False, False, False, False])
    found = [T.ping_problem("ip", v) for v in ("", "router", "10.0.0.1")]
    found += [T.ping_problem("count", v) for v in ("", "0", "two", "3")]
    found += [T.ping_problem("size", v) for v in ("", "27", "28", "65507", "65508")]
    between = tr("Packet size must be between {low} and {high}", low=28, high=65507)
    at_least = tr("Packet count must be greater than or equal to {min}", min=1)
    check("the rules and messages of its ping", found == [
        tr("Destination IP is required"), tr("Destination IP is invalid"), None,
        tr("Packet count is required"), at_least, at_least, None,
        tr("Packet size is required"), between, None, None, between], str(found))
    s = T.ping_stats([{"time": 1.0}, {"timeout": True}, {"time": 3.0}])
    check("its results: loss, minimum, average and maximum", s == {
        "sent": 3, "received": 2, "loss": 33, "min": 1.0, "max": 3.0, "avg": 2.0}, str(s))
    check("  (no answers: all zero)", T.ping_stats([{"timeout": True}]) == {
        "sent": 1, "received": 0, "loss": 100, "min": 0, "max": 0.0, "avg": 0.0})
    check("a hop with no answer, and the times as JavaScript writes them", (
        T.trace_row({"hop": 2, "timeout": True})["responses"],
        T.trace_row({"hop": 1, "ip": "1.1.1.1", "rtts": [1.0, 2.5]})["responses"]) == (
        "* · * · *", "1 · 2.5"))
    found = [T.speedtest_problem("port", v) for v in ("", "0", "65536", "53762", "8080")]
    found += [T.speedtest_problem("duration", v) for v in ("", "9", "100", "30")]
    found += [T.speedtest_problem("user", " "), T.speedtest_problem("password", "  ")]
    port = tr("Port must be between {low} and {high}", low=1, high=65535)
    duration = tr("Duration must be between {low} and {high}", low=10, high=99)
    check("the rules and messages of its speed test (system port included)", found == [
        tr("Port is required"), port, port, tr("Port is used by system"), None,
        tr("Duration is required"), duration, duration, None, tr("User is required"),
        tr("Password is required")], str(found))
    res = {"avg_rx": 150, "avg_tx": 120, "max_rx": 160, "max_tx": 130, "rx_speed": [140, 155],
           "tx_speed": [119, 121]}
    check("only the direction asked for, and the current value is the last of each series", (
        T.speedtest_values(res, "tx")["avg_rx"], T.speedtest_values(res, "tx")["curr_tx"],
        T.speedtest_values(res, "rx")["max_tx"], T.speedtest_values(res, "dx")["curr_rx"]) == (
        0, 121, 0, 155))
    with open(os.path.join(os.path.dirname(FIXTURES), "real",
                           "airos8.7.25-speedtest-status.json"), encoding="utf-8") as f:
        real = json.load(f)
    v = T.speedtest_values(real, "dx")
    check("the real answer of a LiteBeam 5AC when it finishes (state 9 at 10 s)",
          T.speedtest_done(real, 10) and not T.speedtest_done(real, 11)
          and (v["avg_rx"], v["avg_tx"], v["max_tx"], v["curr_rx"], v["curr_tx"]) == (
              49.49, 59.7, 108.7, real["rx_speed"][-1], real["tx_speed"][-1]), str(v))
    check("it ends with state 10 or when the time is up; its stopwatch", (
        T.speedtest_done({"state": 10}, 30), T.speedtest_done({"duration": 30.0}, 30),
        T.speedtest_done({"state": 2, "duration": 12}, 30), T.ms_to_time(65123)) == (
        True, True, False, "01:05:123"))

    srv, d, dev, ui, app = start(8, 18962)
    real_req = dev._req
    answers = []
    for raw in (b"0|192.168.1.1|0.715|64", b"-4|", b"-2|", b""):
        dev._req = lambda *a, **k: (200, {}, raw)
        try:
            answers.append(dev.ping_once("192.168.1.1", 56))
        except T.AirOSError:
            answers.append("error")
    dev._req = real_req
    check("it reads each ping as the web does: answer, no answer (-4), network error and empty",
          answers == [{"host": "192.168.1.1", "time": 0.715, "ttl": 64},
                      {"host": "192.168.1.1", "timeout": True, "error": False},
                      {"host": "192.168.1.1", "timeout": True, "error": True}, "error"],
          str(answers))

    d.events.clear()
    ok = run(app, "tools_menu", [("menu", "n"), ("menu", "s"), ("menu", "1"), ("watch", 9),
                                 ("menu", None), ("menu", None)])
    lists = [items for t, items, _ in ui.menus if t == tr("Network Ping")]
    results = [t for k, ti, t in ui.log if k == "textbox"]
    check("ping: the destination from the antenna's list (ipscan.cgi), 5 pings and their results",
          ok and any(("2", "192.168.1.21") in items for items in lists)
          and d.events.count("ping:192.168.1.1") == 5
          and len(ui.watched[-1]) == 5 and results and tr(
              "Packet Loss: {loss}% ({received} of {total} packets received)", loss=0,
              received=5, total=5) in results[0], str(results[:1]))
    ok = run(app, "tools_menu", [
        ("menu", "n"), ("menu", "c"), ("input", "0"), ("input", "2"), ("menu", "z"),
        ("input", "27"), ("input", "1000"), ("menu", "d"), ("menu", "m"), ("input", "router"),
        ("input", "10.9.9.9"), ("menu", "s"), ("watch", 9), ("menu", None), ("menu", None)])
    errors = msgs(ui, tr("Invalid value"))
    results = [t for k, ti, t in ui.log if k == "textbox"]
    check("  (with its rules; typed in and with no answer: 2 of 2 lost)", ok and errors == [
        at_least, between, tr("Destination IP is invalid")] and results and tr(
            "Packet Loss: {loss}% ({received} of {total} packets received)", loss=100,
            received=0, total=2) in results[0] and results[0].count(tr("Request timeout")) == 2
          and app.tools["ping"] == {"ip": "10.9.9.9", "count": 2, "size": 1000}, str(errors))

    d.events.clear()
    ok = run(app, "tools_menu", [("menu", "r"), ("menu", "s"), ("input", "dns.google"),
                                 ("watch", 40), ("menu", "r"), ("menu", "s"), ("watch", 40),
                                 ("menu", None), ("menu", None)])
    results = [t for k, ti, t in ui.log if k == "textbox"]
    traced = [e for e in d.events if e.startswith("trace:")]
    check("traceroute: hop by hop, with the hop that does not answer; then by the IP it gives",
          ok and len(results) == 2 and "* · * · *" in results[0]
          and "8.8.8.8          14.3 · 14.41 · 14.35" in results[0]
          and "100.64.0.1       6.4 · * · 6.45" in results[0]
          and "dns.google" not in results[0].split("\n", 1)[1]
          and traced[:2] == ["trace:dns.google", "trace:8.8.8.8"], str(results[:1]))
    check("  (and resolving the addresses, their names)", len(results) == 2
          and "dns.google" in results[1] and "core.isp.example.net" in results[1])
    app.tools["traceroute"]["host"] = "nohost.invalid"
    ok = run(app, "tools_menu", [("menu", "r"), ("menu", "s"), ("watch", 5), ("menu", None),
                                 ("menu", None)])
    results = [t for k, ti, t in ui.log if k == "textbox"]
    check("  (a name it does not know: the error it gives)", ok and results
          and results[0].endswith(tr("Error") + ": Unknown host"), str(results))
    app.tools["traceroute"]["host"] = "10.9.9.9"
    ok = run(app, "tools_menu", [("menu", "r"), ("menu", "s"), ("watch", 40), ("menu", None),
                                 ("menu", None)])
    results = [t for k, ti, t in ui.log if k == "textbox"]
    check("  (up to 30 hops)", ok and results and len(results[0].splitlines()) == 31,
          str(results[:1]))

    try:
        dev.speed_test({"ticket": 4242, "action": "status"})
        refused = ""
    except T.AirOSError as e:
        refused = str(e)
    check("a ticket outside 0-999: the antenna rejects it (HTTP 400), as the real one does",
          'Invalid \\"ticket\\" parameter' in refused or 'Invalid "ticket" parameter' in refused,
          refused)
    d.speedtest_speedup = 1e6                       # (over at the first question)
    d.events.clear()
    ok = run(app, "tools_menu", [("menu", "v"), ("menu", "s"), ("menu", "2"), ("input", "ubnt"),
                                 ("password", "ubnt"), ("watch", 40), ("menu", None),
                                 ("menu", None)])
    done = msgs(ui, tr("Speed Test"))
    asks = [e for e in d.events if e.startswith("sptest:")]
    check("speed test: it asks for what is missing, contacts the other device, starts and "
          "follows it to the end", ok
          and asks[:2] == ["sptest:remote", "sptest:start"] and "sptest:stop" not in asks
          and done and done[-1].startswith(tr("Finished")) and "TX: " in done[-1]
          and "RX: " in done[-1], str(done))
    check("  (and on leaving it forgets the user and password, as the web does unless asked)",
          (app.tools["speedtest"]["user"], app.tools["speedtest"]["password"]) == ("", ""))
    ok = run(app, "tools_menu", [("menu", "v"), ("menu", "m"), ("menu", "w"), ("input", "53762"),
                                 ("input", "80"), ("menu", "p"), ("password", "wrong"),
                                 ("menu", "s"), ("input", "ubnt"), ("menu", None),
                                 ("menu", None)])
    check("  (wrong password: the device's error; system port: its rule)", ok
          and msgs(ui, tr("Invalid value")) == [tr("Port is used by system")]
          and msgs(ui, tr("Speed Test")) == [tr("Error") + ": Invalid credentials"],
          str(msgs(ui)))
    check('  (and with "Remember credentials", they stay for this session)',
          (app.tools["speedtest"]["user"], app.tools["speedtest"]["password"]) == (
              "ubnt", "wrong"))
    app.tools["speedtest"]["password"] = "ubnt"
    d.speedtest_speedup = 1.0
    d.events.clear()
    ok = run(app, "tools_menu", [("menu", "v"), ("menu", "s"), ("watch", 2), ("menu", None),
                                 ("menu", None)])
    check("  (a key stops it: it tells the device)", ok and "sptest:stop" in d.events
          and d.speedtest is None and msgs(ui, tr("Speed Test"))[-1].startswith(tr("Finished")))
    d.speedtest_speedup = 1e6
    ok = run(app, "tools_menu", [("menu", "v"), ("menu", "r"), ("menu", "rx"), ("menu", "t"),
                                 ("input", "5"), ("input", "10"), ("menu", "s"), ("watch", 40),
                                 ("menu", None), ("menu", None)])
    started = d.speedtest or {}
    check("  (direction and duration, 10 to 99 s as on its web: the device starts with them)",
          ok and msgs(ui, tr("Invalid value")) == [tr("Duration must be between {low} and {high}",
                                                      low=10, high=99)]
          and (started.get("direction"), started.get("duration")) == ("rx", 10),
          str((msgs(ui), started)))
    srv.shutdown()
    srv, d, dev, ui, app = start(6, 18963)
    ok = run(app, "tools_menu", [("menu", "n"), ("menu", "r"), ("menu", "v"), ("menu", None)])
    check("airOS 6: they are in the device's web", ok and len(set(msgs(ui))) == 1 and msgs(ui)[0]
          == tr("On airOS 6 this tool is in the device's web (Tools)."), str(msgs(ui)))
    srv.shutdown()


def args_file():
    print("=== dialog reads its arguments from a file: the key is not left on its command line "
          "===")
    import subprocess
    ui = T.UI.__new__(T.UI)
    ui.prog, ui.is_dialog, ui.backtitle = "dialog", True, "x"
    seen = []

    class FakePopen:
        def __init__(self, argv, **kw):
            path = argv[2] if argv[1:2] == ["--file"] else None
            info = None
            if path:
                with open(path, encoding="utf-8") as f:
                    info = (oct(os.stat(path).st_mode & 0o777), f.read(), os.path.dirname(path))
            box = re.search(r'"--textbox" "([^"]+)"', info[1]) if info else None
            if box:                              # (what a textbox shows, while it is open)
                box = box.group(1)
                with open(box, encoding="utf-8") as f:
                    box = (box, oct(os.stat(box).st_mode & 0o777), f.read())
            seen.append((list(argv), path, info, box))
            self.returncode = 0

        def communicate(self):
            return None, b"other"
    real = subprocess.Popen
    subprocess.Popen = FakePopen
    try:
        got = ui.inputbox("WPA2 key", "Key:", "secret-wpa-123")
        ui.textbox("Changes", "wireless.1.security.psk=secret-wpa-456")
        ui.is_dialog, ui.prog = False, "whiptail"
        ui.inputbox("WPA2 key", "Key:", "secret-wpa-123")
    finally:
        subprocess.Popen = real
    (argv, path, info, _), (_, box_args, _, box), (w_argv, _, _, _) = seen
    run_dir = os.environ.get("XDG_RUNTIME_DIR")
    check("dialog: only the file on its command line; the file, for the user only, in its "
          "private folder, with everything", got == "other" and argv == ["dialog", "--file", path]
          and "secret-wpa-123" not in " ".join(argv) and info[0] == "0o600"
          and '"secret-wpa-123"' in info[1] and '"--inputbox"' in info[1]
          and (not run_dir or info[2] == run_dir), str((argv, info and info[0], info and info[2])))
    check("  and it is removed on closing", path and not os.path.exists(path))
    check("whiptail does not support it: it stays in its arguments", "--file" not in w_argv
          and "secret-wpa-123" in w_argv, str(w_argv[:4]))
    check("a viewer's text (the configuration, with its keys): in another file of that "
          "folder, for the user only, and removed on closing", box and box[1] == "0o600"
          and "psk=secret-wpa-456" in box[2] and not os.path.exists(box[0])
          and (not run_dir or os.path.dirname(box[0]) == run_dir) and box_args
          and not os.path.exists(box_args), str((box and box[:2], box_args)))
    os.environ["XDG_RUNTIME_DIR"] = "/"              # (another's, which su without - keeps)
    seen.clear()
    subprocess.Popen = FakePopen
    ui.is_dialog, ui.prog = True, "dialog"
    try:
        private = T._private_dir()
        ui.inputbox("WPA2 key", "Key:", "secret-wpa-123")
        ui.textbox("Changes", "wireless.1.security.psk=secret-wpa-456")
    finally:
        subprocess.Popen = real
        if run_dir is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = run_dir
    (argv, path, info, _), (_, _, _, box) = seen
    check("a runtime folder that is not this user's to write in: the system's temporary one, "
          "and still nothing on its command line", os.geteuid() == 0 or (
              private is None and argv == ["dialog", "--file", path]
              and "secret-wpa-123" not in " ".join(argv) and info[0] == "0o600"
              and info[2] != "/" and box and box[1] == "0o600" and os.path.dirname(box[0]) != "/"),
          str((private, argv, info and info[2], box and box[:2])))


def ending_signals():
    print("=== if its terminal is closed (SIGHUP) or it is terminated (SIGTERM): it exits "
          "cleanly, without leaving dialog's file ===")
    import signal
    folder = os.path.join(XDG, "fake-dialog")
    os.makedirs(folder, exist_ok=True)
    fake, mark = os.path.join(folder, "dialog"), os.path.join(folder, "mark")
    with open(fake, "w", encoding="utf-8") as f:   # (it notes its file and how it ended)
        f.write('#!/bin/sh\n'
                'echo "$2" > "$MARK.file"\n'
                'if grep -q -- \'"--gauge"\' "$2"; then cat > /dev/null; echo eof >> "$MARK"; '
                'exit 0; fi\n'
                'sleep 30 &\n'
                'trap \'echo term >> "$MARK"; kill $! 2>/dev/null; exit 1\' TERM\n'
                'wait\n')
    os.chmod(fake, 0o700)
    os.environ["MARK"] = mark
    ui = T.UI.__new__(T.UI)
    ui.prog, ui.is_dialog, ui.backtitle = fake, True, "x"
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGHUP, signal.SIGTERM)}

    def read(path):
        return open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""

    def cut(sig, show):
        """show() open, and sig to this process after 0.8 s: (exit code, dialog's file, how
        dialog ended, the signal's handler inside, after the first one)."""
        for f in (mark, mark + ".file"):
            if os.path.exists(f):
                os.unlink(f)
        for other in (signal.SIGHUP, signal.SIGTERM):  # (as in a terminal, whoever runs
            signal.signal(other, signal.SIG_DFL)      # these tests)
        code = inside = None
        with T.ending_signals():
            threading.Timer(0.8, os.kill, (os.getpid(), sig)).start()
            try:
                show()
            except SystemExit as e:
                code, inside = e.code, signal.getsignal(sig)
        return code, read(mark + ".file"), read(mark), inside

    code, path, ended, inside = cut(signal.SIGTERM, lambda: ui.msgbox("Key", "secret-wpa-123"))
    check("SIGTERM with a dialog open: it exits (143), its file is removed and dialog ends with "
          "SIGTERM, which lets it give the terminal back (not killed)", code == 143 and path
          and not os.path.exists(path) and ended == "term", str((code, path, ended)))
    check("  a second signal now ends it at once, and on exit they are as they were",
          inside == signal.SIG_DFL and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL)
    code, path, ended, _ = cut(signal.SIGHUP, lambda: ui.msgbox("Key", "secret-wpa-123"))
    check("SIGHUP (its terminal was closed): the same (129)", code == 129 and path
          and not os.path.exists(path) and ended == "term", str((code, path, ended)))

    def waiting():                                 # (as when waiting for the device after applying)
        gauge = ui.gauge("Applying", "Wait…")
        try:
            time.sleep(10)
        finally:
            gauge.close()
    code, path, ended, _ = cut(signal.SIGTERM, waiting)
    check("with the progress bar open: it closes and its file is removed too",
          code == 143 and path and not os.path.exists(path) and ended == "eof",
          str((code, path, ended)))
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with T.ending_signals():
        kept = signal.getsignal(signal.SIGHUP)
        mine = signal.getsignal(signal.SIGTERM)
    check("run with nohup: the hangup stays ignored (and SIGTERM does exit cleanly)",
          kept == signal.SIG_IGN and mine == T._end_on_signal
          and signal.getsignal(signal.SIGHUP) == signal.SIG_IGN)
    for sig, handler in before.items():
        signal.signal(sig, handler)


if __name__ == "__main__":
    args_file()
    ending_signals()
    password_rules()
    alignment_math()
    survey_logic()
    live_screen()
    title_widths()
    flows()
    antenna_discovery()
    network_tools()
    system_page()
    date_page()
    maintenance()
    web_backups()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
