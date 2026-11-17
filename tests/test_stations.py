"""Tests of the stations list (airOS 8, like its web's table): the web's formats, each
station's data (with real python-airos states), the throughput, kicking one or all of them,
reconnecting from a station, and airOS 6. All against the simulator on 127.0.0.1."""
import json
import os
import sys
import threading
import time

from _common import check, FakeUI, fails, FIXTURES, suite  # noqa: E402
T, S, XDG = suite("stations")
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
    app.ui.menus.clear()
    try:
        getattr(app, method)(*args)
    except AssertionError as e:
        print("     script:", e)
        return False
    return not app.ui.script


def msgs(ui, title=None):
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def formats():
    print("=== the web's formats (formatBPS, getElapsedTime, Link Potential) ===")
    check("throughput: bps, kbps with 1 decimal, Mbps with 2", (
        T.fmt_bps(None), T.fmt_bps(0), T.fmt_bps(999), T.fmt_bps(1000), T.fmt_bps(1_500_000))
          == ("-", "0 bps", "999 bps", "1.0 kbps", "1.50 Mbps"))
    check("connection time: HH:MM:SS, with the days before", (
        T.fmt_elapsed(59), T.fmt_elapsed(3661), T.fmt_elapsed(90061),
        T.fmt_elapsed(2 * 86400 + 5)) == ("00:00:59", "01:01:01", f"1 {tr('day')} 01:01:01",
                                          f"2 {tr('days')} 00:00:05"))
    check("link potential: the mean of the link scores; 0 is n/a", (
        T.link_potential({}), T.link_potential({"dl_avg_linkscore": 88, "ul_avg_linkscore": 91}),
        T.link_potential({"dl_avg_linkscore": 150, "ul_avg_linkscore": 150}))
          == ("n/a", "89.5%", "100%"))


def real_rows():
    print("=== each station's data (real python-airos states) ===")
    st = fixture("NanoBeam_5AC_ap-ptmp_v8.7.18.json")
    r = T.station_rows(st)[0]
    check("those of the web's table", (
        r["name"], r["model"], r["signal"], r["remote_signal"], r["noise"], r["latency"],
        r["distance"], r["downlink"], r["uplink"], r["airtime_tx"], r["airtime_rx"],
        r["potential"], r["ip"], r["throughput_rx"]) == (
        "NanoStation M5", "NanoStation M5", -52, -64, -92, 1, 750, "168.48 Mbps",
        "273.00 Mbps", "0.4 %", "2.7 %", "n/a", "192.168.0.125", None), str(r))
    rx, tx = r["bytes"]
    r2 = T.station_rows(st, {r["mac"]: (rx - 8000, tx - 16000)}, 2.0)[0]
    check("the throughput, with an earlier read", (T.fmt_bps(r2["throughput_rx"]),
                                                   T.fmt_bps(r2["throughput_tx"]))
          == ("32.0 kbps", "64.0 kbps"))
    text = T.station_text(r)
    check("its text: the web's columns", all(part in text for part in (
        tr("Device Name"), "NanoStation M5", tr("Connection Time"), f"1 {tr('day')} 17:36:46",
        tr("Link Potential"), "n/a", tr("Last IP"), "192.168.0.125")), text)
    two = T.station_rows(fixture("liteapgps_ap_ptmp_40mhz.json"))
    check("an AP with two stations", [x["name"] for x in two]
          == ["NanoBeam-shed2", "NanoBeam-shed1"])


def section(text, header):
    """The lines of a section of the status screen: {label: value}."""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("──"):
            inside = header in line
            continue
        if inside and len(line) > 22:
            out.setdefault(line[:22].strip(), line[22:].strip())
        elif inside and not line.strip() and out:
            break
    return out


def dashboard():
    print("=== what the web's main page shows (a real LiteBeam 5AC PtP) ===")
    with open(os.path.join(os.path.dirname(FIXTURES), "real",
                           "airos8.7.25-litebeam5ac-ap-ptp.status.json"), encoding="utf-8") as f:
        st = json.load(f)
    text = T.format_status(st, "v8", "WA.ar934x.v8.7.25.48568.260730.1010")
    first = {line[:22].strip(): line[22:].strip() for line in text.split("\n\n")[0].splitlines()}
    check("this device: the version with its series, the date and UISP", (
        first.get("Firmware"), first.get(tr("Date")), first.get("UISP")) == (
        "v8.7.25 (WA)", "2026-11-08 15:46:07", tr("UISP not configured")), str(first))
    eth = section(text, "Ethernet")
    check("Ethernet: 1000 Mbps-Full, < 20 m and the mean of the 4 pairs (28.5 → +29, as "
          "Math.round)", eth == {tr("{port} Speed", port="LAN"): "1000 Mbps-Full",
                                 tr("Cable Length"): "< 20 m", tr("Cable SNR"): "+29 dB"},
          str(eth))
    wl = section(text, tr("── Wireless ──").strip("─ "))
    check("wireless: distance, bytes, TDD framing, CINR and service uptime (PtP: that of the "
          "end in service longer)", (
              wl.get(tr("Distance")), wl.get(tr("TX/RX Bytes")), wl.get(tr("TDD Framing")),
              wl.get("CINR"), wl.get(tr("Service uptime"))) == (
              tr("{miles} miles ({km} km)", miles="0.8", km="1.4"), "107 M / 18.5 M",
              tr("Flexible (legacy)"), "+27 dB", "99.884%"), str(wl))
    bare = json.loads(json.dumps(st))
    bare["wireless"]["sta"][0].pop("remote", None)
    mac = bare["wireless"]["sta"][0]["mac"]
    bare_text = T.format_status(bare, "v8", "WA.ar934x.v8.7.25.48568.260730.1010")
    check("the other end without its own data (no remote): still which one it is (its MAC)",
          tr("── The other end: {name} ──", name=mac) in bare_text.split("\n"),
          bare_text[-600:])
    other = section(text, tr("── The other end: {name} ──", name="Station-Example")[3:14])
    o_wl = section(text, tr("── Wireless (the other end) ──").strip("─ "))
    o_eth = section(text, tr("── Ethernet (the other end) ──").strip("─ "))
    check("the other end, like the web's remote panel, in its three blocks", all(
        other.get(k) == v for k, v in (
            (tr("Device Model"), "LiteBeam 5AC"), (tr("Version"), "v8.7.21 (WA)"),
            ("CPU", "5 %"), (tr("Memory"), "64 %"), (tr("Date"), "2026-11-08 15:46:06")))
          and all(o_wl.get(k) == v for k, v in (
              (tr("Wireless Mode"), tr("Station PtP")), (tr("Last IP"), "192.0.2.1"),
              (tr("TX/RX Bytes"), "14.0 G / 19.9 G"), ("CINR", "+25 dB"),
              (tr("Noise Floor"), "-89 dBm")))
          and o_eth == {tr("{port} Speed", port="LAN"): "100 Mbps-Full",
                        tr("Cable Length"): "20 m", tr("Cable SNR"): "+30 dB"}
          and tr("Wireless Mode") not in other and tr("{port} Speed", port="LAN") not in o_wl,
          f"{other}\n{o_wl}\n{o_eth}")
    check("the web's cases: a 300 m minimum, measuring, 100 %, fixed framing", (
        T.fmt_distance(120), T.fmt_distance(100000), T.service_uptime({"time": 9, "link": 9}),
        T.service_uptime({}), T.tdd_framing({"fixed_frame": True, "ff_frame_dur": 5,
                                             "gps_sync": True}),
        T.tdd_framing({"flex_mode": 1})) == (
        tr("{miles} miles ({km} km)", miles="0.2", km="0.3"), tr("measuring"), "100%",
        "0.000%", tr("Fixed") + " (5ms) & " + tr("GPS Synced"), tr("Flexible")))
    two = T.ethernet_rows(fixture("NanoBeam_5AC_ap-ptmp_v8.7.18.json")["interfaces"])
    check("two ports: LAN0 / LAN1, as the web", two[0] == (
        tr("{port} Speed", port="LAN0 / LAN1"), "1000 Mbps-Full / -"), str(two))
    v6 = T.format_status(fixture("NanoStation_M5_sta_v6.3.16.json"), "v6")
    check("airOS 6: its LAN speed (no cable length or SNR: it does not give them)",
          section(v6, "Ethernet") == {tr("{port} Speed", port="LAN0 / LAN1"):
                                      "100 Mbps-Full / -"}, str(section(v6, "Ethernet")))
    detail = T.station_text(T.station_rows(st)[0])
    check("Stations: the detail also has the other end's data", all(x in detail for x in (
        tr("Wireless Mode"), tr("Station PtP"), "100 Mbps-Full", "+30 dB", "v8.7.21 (WA)"))
          and detail.count("v8.7.21 (WA)") == 1, detail)
    parts = detail.split("\n\n")
    check("  (in blocks: the device, Wireless and Ethernet)", len(parts) == 3
          and parts[1].startswith(tr("── Wireless ──")) and parts[2].startswith(
              tr("── Ethernet ──")) and "CPU" in parts[0] and tr("Signal") in parts[1]
          and "100 Mbps-Full" in parts[2] and "Mbps-Full" not in parts[1], detail)


def ap_flows():
    print("=== Stations (an AP of the simulator): the list, their data, kicking ===")
    srv, d, dev, ui, app = start(8, 19401)
    label = tr("Stations ({count}): their data and kicking them", count=1)
    ui.script, ui.log[:] = [("menu", None)], []
    app.run()
    main = [items for t, items, _ in ui.menus if t == app.title()][-1]
    check("in the main menu, after the status", main[1] == ("e", label), str(main[:3]))
    os.environ["LINES"] = "60"                  # (a tall terminal: all of it in the menu)
    ok = run(app, "stations_menu", [("menu", "1"), ("menu", None), ("menu", None)])
    title = tr("Stations")
    lists = [m for m in ui.menus if m[0] == title]
    detail = [t for k, ti, t in ui.log if k == "menu" and ti == "Client-North-01"]
    check('the list: the station, "Kick All" and reading again', ok
          and [t for t, _ in lists[0][1]] == ["1", "k", "r"]
          and "Client-North-01" in lists[0][1][0][1], str(lists[:1]))
    line = next((x for x in (detail[0] if detail else "").splitlines()
                 if x.startswith(tr("Throughput RX"))), "")
    value = line.split()[-2] if line else "0"
    check("its data, with the throughput of two reads (about 2 Mbit/s)", detail
          and "LiteBeam 5AC" in detail[0] and line.endswith("Mbps")
          and 1.5 < float(value) < 2.6, line)
    calls = [0]
    real_status = dev.status

    def counted():
        calls[0] += 1
        return real_status()
    dev.status = counted
    ok = run(app, "stations_menu", [("menu", "r"), ("menu", None)])
    del dev.status
    check('"Read them again": two reads a second apart, as when it opens (its throughput is the '
          "one between them, not since the list was shown)", ok and calls[0] == 4, str(calls))
    os.environ["LINES"] = "24"                  # (a small one: the same; the menu cuts it)
    ui.menus[:], ui.log[:] = [], []
    ok = run(app, "stations_menu", [("menu", "1"), ("menu", None), ("menu", None)])
    small = [t for k, ti, t in ui.log if k == "menu" and ti == "Client-North-01"]
    check("in a small terminal, all its data to the menu too, without an option of its own: what "
          "does not fit, the menu shows behind ? (test_ui)", ok and small
          and tr("Throughput RX") in small[0] and not small[0].endswith("…")
          and [t for t, _ in [m for m in ui.menus if m[0] == "Client-North-01"][0][1]]
          == ["k"], str(small[:1]))
    os.environ["LINES"] = "60"
    ok = run(app, "stations_menu", [("menu", "1"), ("menu", "k"), ("yesno", 1), ("menu", None)])
    check('"Kick" asks; "No" sends nothing', ok
          and not [e for e in d.events if e.startswith("kick:")])
    ok = run(app, "stations_menu", [("menu", "1"), ("menu", "k"), ("yesno", 0), ("menu", None)])
    after = [text for k, ti, text in ui.log if k == "menu" and ti == title]
    check('"Yes": kicks it (POST stakick.cgi) and reads again: it is gone', ok
          and "kick:68:D7:9A:11:22:33" in d.events and after[-1] == tr("No station is "
                                                                      "connected.")
          and msgs(ui, title) == [tr("Sent: it is disconnected; it usually connects again in a "
                                     "few seconds.")], str(after))
    time.sleep(3.2)                              # (the simulator lets it back after 3 s)
    ok = run(app, "stations_menu", [("menu", "k"), ("yesno", 0), ("menu", None)])
    check('"Kick All": the broadcast MAC, as the web', ok
          and "kick:FF:FF:FF:FF:FF:FF" in d.events)
    time.sleep(3.2)

    def broken(iface, mac):
        raise T.AirOSError("stakick.cgi answered HTTP 500: x")
    dev.kick_station = broken
    ui.script, ui.log[:] = [("menu", "1"), ("menu", "k"), ("yesno", 0)], []
    app._do(app.stations_menu)
    del dev.kick_station
    check("if the device fails: it says so", not ui.script
          and ("msgbox", "Error", "stakick.cgi answered HTTP 500: x") in ui.log)
    real_status = dev.status
    dev.status = lambda: fixture("liteapgps_ap_ptmp_40mhz.json")
    ok = run(app, "stations_menu", [("menu", None)])
    dev.status = real_status
    items = [m for m in ui.menus if m[0] == title][0][1]
    check("an AP with two stations (a real status): both", ok
          and [t for t, _ in items] == ["1", "2", "k", "r"]
          and "NanoBeam-shed1" in items[1][1], str(items))
    srv.shutdown()


def station_flows():
    print("=== from a station: the link with its AP, and reconnecting ===")
    srv, d, dev, ui, app = start(8, 19402, station=True)
    title = tr("Access point")
    check("in the main menu", app._stations_label()
          == tr("Access point: the link and reconnecting"))
    ok = run(app, "stations_menu", [("menu", "1"), ("menu", "c"), ("yesno", 0), ("menu", None)])
    lists = [m for m in ui.menus if m[0] == title]
    asked = [t for k, ti, t in ui.log if k == "yesno" and ti == title]
    check('its AP, without "Kick All"; "Reconnect" kicks it, like the web\'s button', ok
          and [t for t, _ in lists[0][1]] == ["1", "r"] and "kick:68:D7:9A:11:22:33" in d.events
          and asked == [tr("Reconnect to {name}? The link is cut for a moment.",
                           name="Client-North-01")], str(asked))
    srv.shutdown()


def airos6():
    print("=== airOS 6 ===")
    srv, d, dev, ui, app = start(6, 19403)
    ok = run(app, "stations_menu", [])
    check("from its web", ok and msgs(ui) == [tr(
        "On airOS 6 the stations are in the device's web (its main page).")])
    srv.shutdown()


if __name__ == "__main__":
    formats()
    real_rows()
    dashboard()
    ap_flows()
    station_flows()
    airos6()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
