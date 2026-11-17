"""Tests of the rest of the airOS 8 main page: its graphs (capacity, throughput and latency: how
it keeps their data and feeds them with each status.cgi; with the real status of a LiteBeam 5AC
with 8.7.25), the distance warning in PtMP with "Flexible (legacy)", the distance from the
coordinates (the GPS or the Location of its System page) and its GPS group. All at
127.0.0.1."""
import copy
import json
import os
import sys
import threading
import time

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("graphs")
tr = T.tr
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = json.load(open(os.path.join(HERE, "fixtures", "real",
                                   "airos8.7.25-litebeam5ac-ap-ptp.status.json")))

print("=== its data: counters, points and averages ===")
check("the counter that wraps around (its handle_overwrap)", [T._overwrap(*a) for a in (
    (500, 100), (100, 4294967000), (100, 2000000000), (2400000000, 100000000))] == [
    400, 4294967295 - 4294967000 + 100, 100, 2400000000 - 100000000])
c = T.ChartData(points=6)
c.add_bytes(1000, 2000, 1000)                       # (the first: with no previous one, 0)
c.add_capacity(100, 50)
c.add_latency(2)
c.tick()
c.add_bytes(126000, 2000 + 250000, 1000)            # 125 000 B and 250 000 B in 1 s
c.add_capacity(100, 50)                             # (in kbps)
c.add_latency(2.04)
c.tick()
check("the first two points are not added (as its web)", c.points["rx"] == [0.0] * 6)
c.add_bytes(126000 + 250000, 252000 + 125000, 500)  # 250 000 B in half a second
c.add_capacity(3000, 50)
c.add_latency(2.04)
c.tick()
check("throughput in bit/s; capacity in bit/s, never below the throughput",
      c.points["rx"][-1] == 4e6 and c.points["tx"][-1] == 2e6
      and c.points["capacity_rx"][-1] == 4e6 and c.points["capacity_tx"][-1] == 2e6
      and c.points["average_capacity_rx"][-1] == 3e6
      and c.points["average_capacity_tx"][-1] == 50000, str(
          {k: v[-1] for k, v in c.points.items()}))
check("latency rounded to a tenth", c.points["latency"][-1] == 2.0)
c.add_bytes(0, 0, 1000)
c.add_capacity(0, 0)
c.tick()
check("no latency at that point: it repeats the last one; no counters, 0",
      c.points["latency"][-1] == 2.0 and c.points["rx"][-1] == 0.0
      and len(c.points["rx"]) == 6)
check("the format of its graphs (formatBPS with normalize_float)", [T.fmt_chart_bps(v) for v in (
    0, 999, 1500, 15000, 150000, 2e6, 153.9e6)] == [
    "0 bps", "999 bps", "1.50 kbps", "15.0 kbps", "150 kbps", "2.00 Mbps", "154 Mbps"]
      and T.fmt_ms(2.0) == "2 ms" and T.fmt_ms(2.5) == "2.5 ms")
check("sparkline: the highest of each group, from ▁ to █; empty, nothing",
      T.sparkline([0, 1, 2, 3, 4, 8], 3, 8) == "▂▄█" and T.sparkline([0] * 6, 3, 0) == "   "
      and T.sparkline([0, 0, 8, 0], 4, 8) == "  █ ")


def advanced(st, seconds, rx, tx, sta_rx=0, sta_tx=0):
    """The status, seconds later, with those extra bytes (ath0 and its station)."""
    s = copy.deepcopy(st)
    s["host"]["timestamp"] = (s["host"]["timestamp"] + int(seconds * 1000)) & 0xFFFFFFFF
    s["host"]["uptime"] += int(seconds)
    for i in s["interfaces"]:
        if i["ifname"] == "ath0":
            i["status"]["rx_bytes"] += rx
            i["status"]["tx_bytes"] += tx
    sta = s["wireless"]["sta"][0]
    sta["stats"]["rx_bytes"] += sta_rx
    sta["stats"]["tx_bytes"] += sta_tx
    return s


print("=== how its page feeds them, with the LiteBeam's real status (AP PtP) ===")
g = T.LinkGraphs()
st = REAL
for _ in range(4):
    g.feed(st)
    st = advanced(st, 1, 250000, 1000000, 1000000, 250000)
mac = REAL["wireless"]["sta"][0]["mac"]
local, remote = g.local.points, g.stations[mac].points
check("this device (AP): the RX of ath0 and its upload capacity (ul_capacity)",
      local["rx"][-1] == 2e6 and local["tx"][-1] == 8e6
      and local["capacity_rx"][-1] == 140400e3 and local["capacity_tx"][-1] == 153900e3,
      str({k: v[-1] for k, v in local.items()}))
check("the other end: what its station receives (what the AP sends it) and its download",
      remote["rx"][-1] == 2e6 and remote["tx"][-1] == 8e6
      and remote["capacity_rx"][-1] == 153900e3 and remote["capacity_tx"][-1] == 140400e3,
      str({k: v[-1] for k, v in remote.items()}))
sta_view = copy.deepcopy(REAL)
sta_view["wireless"]["mode"] = "sta-ptp"
g2 = T.LinkGraphs()
for _ in range(4):
    g2.feed(sta_view)
    sta_view = advanced(sta_view, 1, 0, 0)
check("a station: its RX is the download (dl_capacity)", g2.local.points["capacity_rx"][-1]
      == 153900e3 and g2.local.points["capacity_tx"][-1] == 140400e3)
g4, st4 = T.LinkGraphs(), REAL
for _ in range(4):
    gen3 = copy.deepcopy(st4)
    for i in gen3["interfaces"]:
        if i["ifname"] == "ath0":
            i["ifname"] = "wlan0"
    g4.feed(gen3)
    st4 = advanced(st4, 1, 250000, 1000000, 1000000, 250000)
check("a Gen3 board (its wireless interface is wlan0): its throughput too",
      g4.local.points["rx"][-1] == 2e6 and g4.local.points["tx"][-1] == 8e6,
      str(g4.local.points["rx"][-2:]))
wrap = copy.deepcopy(REAL)
wrap["host"]["timestamp"] = 4294967000
g3 = T.LinkGraphs()
g3.feed(wrap)
for _ in range(3):
    wrap = advanced(wrap, 1, 125000, 0)
    g3.feed(wrap)
check("its milliseconds counter wraps around, and it follows it",
      g3.local.points["rx"][-1] == 1e6, str(g3.local.points["rx"][-3:]))
lines = T.chart_lines(g.local, width=40)
check("each series, its sparkline and its last value", len(lines) == 5
      and lines[0].startswith(tr("Capacity RX")) and lines[0].endswith("140 Mbps")
      and lines[2].startswith(tr("Throughput RX")) and lines[2].endswith("2.00 Mbps")
      and lines[4].endswith("0 ms") and "█" in lines[0], "\n".join(lines))

print('=== the distance warning of its page (PtMP with "Flexible (legacy)") ===')


def ptmp(distance, chanbw=20, **polling):
    s = copy.deepcopy(REAL)
    s["wireless"].update(mode="ap-ptmp", chanbw=chanbw)
    s["wireless"]["polling"] = dict(s["wireless"]["polling"], **polling)
    s["wireless"]["sta"][0]["distance"] = distance
    return s


def row(label, value):
    """A row of the status screen: its label (the msgid) and its value."""
    return f"{tr(label):<21} {value}"


check("with its 17 %: it warns from the limit of its channel width (20 MHz: 22.5 km)",
      T.distance_limit_reached(19300, ptmp(0)) and not T.distance_limit_reached(19200, ptmp(0))
      and T.distance_limit_reached(32100, ptmp(0, 10))
      and not T.distance_limit_reached(32000, ptmp(0, 10)))
check('not with a fixed or "Flexible" frame, nor in PtP',
      not T.distance_limit_reached(30000, ptmp(0, fixed_frame=True))
      and not T.distance_limit_reached(30000, ptmp(0, flex_mode=1))
      and not T.distance_limit_reached(30000, REAL))
text = T.format_status(ptmp(20000), "v8", "")
check('the "Distance" row marks it and explains it', row(
    "Distance", tr("{miles} miles ({km} km)", miles="12.4", km="20.0") + "  ⚠") in text
      and tr("⚠ In PtMP with \"Flexible (legacy)\" framing, its web warns from {km} "
             "km at {width} MHz (with a 17 % margin); not with a fixed or \"Flexible\" "
             "frame.", km="22.5", width=20) in text, text[:1500])
check("  no warning at 1.4 km", "⚠" not in T.format_status(ptmp(1350), "v8", ""))

print("=== the distance from the coordinates ===")
cfg = T.Config("system.latitude=-34.6037\nsystem.longitude=-58.3816\n")
there = copy.deepcopy(REAL)
there["wireless"]["sta"][0]["remote"]["gps"] = {"lat": -34.62, "lon": -58.40}
check("this device: its GPS with a fix or, if not, the Location of its System page",
      T.coordinates({"gps": {"fix": 1, "lat": 1.5, "lon": 2.25}}, cfg) == (1.5, 2.25)
      and T.coordinates({"gps": {"fix": 0, "lat": 1.5, "lon": 2.25}}, cfg)
      == (-34.6037, -58.3816) and T.coordinates({}, T.Config("")) is None
      and T.coordinates({}, T.Config("system.latitude=abc\nsystem.longitude=1\n")) is None)
check("the other end: its remote.gps", T.coordinates(there, sta=there["wireless"]["sta"][0])
      == (-34.62, -58.4) and T.coordinates(REAL, sta=REAL["wireless"]["sta"][0]) is None)
d = T.distance_between((-34.6037, -58.3816), (-34.62, -58.40))
check("its formula (radius 6378137 m)", abs(d - 2476.74) < 0.01, str(d))
text = T.format_status(there, "v8", "", None, cfg)
check("with both: its row and, as it does not match the measured one (1.4 km), its warning",
      row("Distance (coord.)", tr("{miles} miles ({km} km)", miles="1.5", km="2.5")) in text
      and tr("⚠ Coordinate-based and propagation-based distances do not match.") in text,
      text[:1200])
near = copy.deepcopy(there)
near["wireless"]["sta"][0]["remote"]["gps"] = {"lat": -34.6125, "lon": -58.3894}
check("  if they match (with its margin), no warning",
      "⚠" not in T.format_status(near, "v8", "", None, cfg))

print("=== its GPS group (only with GPS on its board) ===")
gps = {"fix": 1, "lat": -34.6037, "lon": -58.3816, "alt": 25.4, "sats": 9, "dop": 1.2}
rows = dict(T.gps_rows(gps))
check("satellites, signal (from its dop), location and altitude", rows == {
    "Satellites tracked": "9", "GPS Signal": "90 %", "Location": "-34.603700 / -58.381600",
    "Altitude": "25 m (83 ft)"}, str(rows))
check("without a fix: no signal nor altitude", dict(T.gps_rows({"fix": 0, "sats": 2, "dop": 30}))
      == {"Satellites tracked": "2", "GPS Signal": "-", "Location": "-", "Altitude": "-"})
with_gps = copy.deepcopy(there)
with_gps["gps"] = gps
text = T.format_status(with_gps, "v8", "", None, cfg, {"feature.gps": "1"})
check("on the status screen: its own and the other end's", tr("── GPS ──") in text
      and row("Satellites tracked", "9") in text and tr("── GPS (the other end) ──") in text
      and "-34.620000 / -58.400000" in text, text[-900:])
check("  without GPS on its board, nothing",
      "GPS" not in T.format_status(with_gps, "v8", "", None, cfg))


def screen(port):
    print("=== v8: the live screen, against the simulator (AP PtMP, one station) ===")
    srv = S.make_server(8, port, restart=0.3, test_secs=60)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        dev.connect()
        ui = FakeUI()
        app = T.App(ui, dev, wait=1)
        app.load()
        real_status = app.dev.status

        def paced():
            time.sleep(0.25)
            return real_status()
        app.dev.status = paced
        ui.script = [("watch", 6)]
        app.graphs_screen()
        shown = ui.watched[-1]
        last = shown[-1].split("\n")
        check("this device and the other end, with their series",
              last[0] == tr("── This device ──")
              and [ln[:15].strip() for ln in last[1:6]] == [tr(x) for x in (
                  "Capacity RX", "Capacity TX", "Throughput RX", "Throughput TX", "Latency")]
              and last[7].startswith(tr("── The other end: {name} ──").partition("{")[0]),
              shown[-1])
        captured = {}
        ui.watch = lambda title, render, interval=1.0, until=None: captured.update(
            interval=interval)
        app.graphs_screen()
        del ui.watch
        every = captured.get("interval")
        waits = [every() for _ in range(3)] if callable(every) else []
        check("a read each second from the first, as its web's timer (not a second after each "
              "read ends): its points are its last seconds",
              [round(w) for w in waits] == [1, 2, 3], str(waits))
        rx = last[3].rsplit("  ", 1)[-1]
        value = float(rx.split()[0]) if rx.endswith("Mbps") else 0
        check("after a few reads, its throughput (the simulator: 2 Mbps upload)",
              1.4 < value < 2.6 and "█" in shown[-1] and shown[0].count("█") == 0,
              rx + "\n" + shown[0])
        check('the average capacity on a PtMP AP (as its "average" graph)',
              last[1].endswith("190 Mbps") and last[2].endswith("210 Mbps"), last[1])
        app.dev.status = real_status
        two = copy.deepcopy(app.dev.status())
        other = dict(two["wireless"]["sta"][0], mac="68:D7:9A:11:22:44")
        two["wireless"]["sta"].append(other)
        app.dev.status = lambda: two                # (read as it opens: the current status)
        ui.menus.clear()
        ui.script = [("menu", None)]
        app.graphs_screen()
        app.dev.status = real_status
        check("with several stations, it asks which one (and Esc goes back)", ui.menus[-1][0]
              == tr("Graphs") and [t for t, _ in ui.menus[-1][1]] == [
                  "68:D7:9A:11:22:33", "68:D7:9A:11:22:44"] and not ui.script)
        app.dev.flavor = "v6"
        ui.log.clear()
        app.graphs_screen()
        app.dev.flavor = "v8"
        check("airOS 6: it says so", ui.titles() == [tr("Graphs")])
    finally:
        srv.shutdown()
        srv.server_close()


screen(19935)                    # (19931 is test_system's: run.py runs suites at once)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
