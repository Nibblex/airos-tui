"""Tests of the airOS 8 airView: its model (its web's airviewData: its levels, its waterfall, its
labels), with the numbers its web's own code gives on a real capture from a NanoStation 5AC loco
with 8.7.14; its text views; its messages; the simulator; its dialog's reading loop; and the
screen (Tools → airView). All at 127.0.0.1."""
import copy
import gzip
import json
import math
import os
import sys
import threading
import time

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("airview")
tr = T.tr
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = json.loads(gzip.open(os.path.join(HERE, "fixtures", "real",
                                         "airos8.7.14-nanostation5ac-airviewdata.json.gz")).read())

# What its web's airviewData calculates (its JavaScript, run in QuickJS on 2026-11-09) with
# REAL: the ten levels of some points (the top one first) and its waterfall row.
REF_LEVELS = {
    0: [-105, -106.33333333333333, -107, -107, -107.66666666666667, -109, -109, -109, -109,
        -109.66666666666667],
    1: [-105, -106.5, -107, -107, -107.5, -108.5, -109, -109, -109, -109.5],
    2: [-104.6, -105.8, -106.6, -106.6, -107, -108.2, -108.6, -108.6, -108.6, -109],
    300: [-108.2, -109.8, -110.2, -110.2, -111.8, -112.2, -112.2, -112.2, -112.2, -113],
    647: [-107.66666666666667, -107.66666666666667, -109.66666666666667, -109.66666666666667,
          -109.66666666666667, -110.33333333333333, -110.33333333333333, -111.66666666666667,
          -111.66666666666667, -111.66666666666667],
}
REF_WF = [5, 7, 6, 6, 9, 5, 8, 9, 5, 10, 7, 6]
# …with a totalSamples of 646 (as the first real read had): the last two without a total
REF_SHORT = {646: [-116, -116, -117, -117, -117, -117.5, -117.5, -118, -118, -118],
             647: [-118.33333333333333, -118.33333333333333, -119, -119, -119,
                   -119.66666666666667, -119.66666666666667, -119.66666666666667,
                   -119.66666666666667, -119.66666666666667]}
# …with the totals of 5, 6 and 7 at 0 (6 without samples: 0/0; 7 with 3: x/0)
REF_ZERO = {5: [-116.6, -116.6, -117.4, -117.4, -117.4, -118.2, -118.2, -118.2, -118.2, -118.2],
            6: [-117, -117, -117.8, -117.8, -117.8, -118.2, -118.2, -118.6, -118.6, -118.6],
            7: [-112.6, -113, -113.8, -113.8, -114.2, -114.6, -114.6, -115, -115, -115.4]}


def model(*datas, anze=60):
    m = T.AirviewData()
    for d in datas:
        m.add_data(copy.deepcopy(d))
        m.calculate()
    m.anze_interval = anze
    return m


def at(m, b):
    return [lv[b] for lv in m.levels]


print("=== its model, as its web's (the real capture) ===")
m = model(REAL)
check("its ten levels per frequency, as its web calculates them", len(m.levels) == 10
      and all(len(lv) == 648 for lv in m.levels)
      and all(at(m, b) == ref for b, ref in REF_LEVELS.items()), str(at(m, 300)))
check("  the top one first (93 %), never below the next one",
      all(m.levels[k][r] >= m.levels[k + 1][r] for k in range(9) for r in range(648)))
check("its waterfall row: each raw power minus 1",
      m.waterfall == [[v - 1 for v in REAL["latestPower"]]] and m.waterfall[0][:12] == REF_WF)
check("its powers in dBm (-125 + 2 per step)", m.latest[:3] == [
    -125 + 2 * v for v in REAL["latestPower"][:3]] and len(m.instant) == 8)
check("its labels: the first and the last (no gaps), a 1.25 MHz step",
      m.labels() == [(0, 50956250), (647, 59043750)] and m.freq_delta() == 12500)
check("its noise: 24 minutes and 11 hours (without the empty rows)",
      len(model(REAL, anze=60).noise_rows()) == 24 and len(model(REAL, anze=3600).noise_rows())
      == 11)
frames = [m.instant_frame() for _ in range(9)]
check("the power right now: its next frame each time (8, and back to the first)",
      frames[0] == m.instant[0] and frames[1] == m.instant[1] and frames[8] == m.instant[0])
check("its scale: from -120 to -60 dBm", m.waveform_top() == -60)
short = copy.deepcopy(REAL)
short["totalSamples"] = short["totalSamples"][:646]
ms = model(short)
check("a shorter totalSamples: those without a total, as its web (NaN: -125 and smoothed)",
      all(at(ms, b) == ref for b, ref in REF_SHORT.items()), str(at(ms, 647)))
zero = copy.deepcopy(REAL)
for r in (5, 6, 7):
    zero["totalSamples"][r] = 0
for u in range(64):
    zero["pwrHistogram"][u][6] = 0
zero["pwrHistogram"][0][7] = 3
mz = model(zero)
check("totals at 0: 0/0 (NaN) and x/0 (Infinity), as its web",
      all(at(mz, b) == ref for b, ref in REF_ZERO.items()), str(at(mz, 7)))
check("its smoothing, with its slip-up: the first point stays in every window",
      T._smooth([10.0, 0, 0, 0, 0, 0, 0]) == [10 / 3, 2.5, 2.0, 2.0, 2.0, 2.5, 10 / 3],
      str(T._smooth([10.0, 0, 0, 0, 0, 0, 0])))
seq, again = T.AirviewData(), copy.deepcopy(REAL)
again["totalSamples"][0] += 1
seen = []
for d in (REAL, REAL, REAL, again):
    seen.append(seq.is_updated(d))
    seq.add_data(d)
check("new data: the sum of totalSamples; the first two reads always (its l)",
      seen == [True, True, False, True], str(seen))
many = []
for k in range(27):
    d = copy.deepcopy(REAL)
    d["latestPower"] = [(v + k) % 40 for v in d["latestPower"]]
    many.append(d)
mm = model(*many)
check("the waterfall keeps its last 24 rows", len(mm.waterfall) == 24
      and mm.waterfall[-1] == [v - 1 for v in many[-1]["latestPower"]]
      and mm.waterfall[0] == [v - 1 for v in many[3]["latestPower"]])
gap = copy.deepcopy(REAL)
gap["stFreqGridLabels"] = [f + (2000000 if i >= 300 else 0)
                           for i, f in enumerate(gap["stFreqGridLabels"])]
check("with a gap in the frequencies, also where it jumps",
      model(gap).labels() == [(0, 50956250), (300, 56706250), (647, 61043750)])
tie = T.AirviewData()
tie.data = {"stFreqGridLabels": [0, 5, 10, 20, 30]}
check("its usual step: on a tie, the larger one", tie.freq_delta() == 10)
top = T.AirviewData()
found = []
for high in (-125.0, -100.0, -50.0, 5.0):
    top.levels = [[high] * 3 for _ in range(10)]
    found.append(top.waveform_top())
check("its scale: up to -100, -60, -20 or 20 dBm, depending on its bands", found ==
      [-100, -60, -20, 20], str(found))
bare = T.AirviewData()
check("no data: no frames, no step, no labels; and without labels, only the axis line",
      bare.instant_frame() == [] and bare.freq_delta() is None and bare.labels() == []
      and T.freq_axis(bare, 20, 648) == ["     └" + "─" * 20]
      and T._freq_index([], 5) == 0.0 and T._freq_index([(3, 100)], 5) == 3.0)
check("what a read needs (otherwise its web would stop)", T.airview_usable(REAL)
      and not T.airview_usable({k: v for k, v in REAL.items() if k != "stPowrNum"})
      and not T.airview_usable(dict(REAL, stPowrDel=0)) and not T.airview_usable([REAL])
      and not T.airview_usable(dict(REAL, stFreqGridNumPts=True)))
labels = list(REAL["stFreqGridLabels"])
labels[5] = None
check("  frequencies that are numbers (its axis and its step are worked out with them); and "
      "an ambient noise that is not a list of rows is none yet (not an error)",
      not T.airview_usable(dict(REAL, stFreqGridLabels=labels))
      and not T.airview_usable(dict(REAL, stFreqGridLabels=["5180"] * len(labels)))
      and model(dict(REAL, mediumAnze=7), anze=60).noise_rows() == []
      and model(dict(REAL, latestAnze={"x": 1}), anze=3600).noise_rows() == [])

print("=== its axes (d3's) ===")
check("dBm: every 10, every 5, every 20", [
    T.d3_ticks(-120, -60, 5), T.d3_ticks(-120, -100, 5), T.d3_ticks(-120, 20, 5)] == [
    [-120, -110, -100, -90, -80, -70, -60], [-120, -115, -110, -105, -100],
    [-120, -100, -80, -60, -40, -20, 0, 20]])
check("frequencies: every 100 MHz, from 5100 to 5900",
      [f / 1e4 for f in T.d3_ticks(50956250, 59043750)] == [5100.0 + 100 * k for k in range(9)])
check("  and with nothing to spread, none; reversed, the same", T.d3_ticks(5, 5) == []
      and T.d3_ticks(1, 2, 0) == [] and T.d3_ticks(-60, -120, 5) == T.d3_ticks(-120, -60, 5))


def hms(*parts):
    """A duration as its secondsToHms writes it: each (msgid, n), joined with ", "."""
    return ", ".join(tr(msgid, n=n) for msgid, n in parts)


print("=== its messages ===")
check("its secondsToHms", [T.seconds_to_hms(s) for s in (896, 3600, 3661, 7322, 0)] == [
    hms(("{n} minutes", 14), ("{n} seconds", 56)), hms(("{n} hour", 1)),
    hms(("{n} hour", 1), ("{n} minute", 1), ("{n} second", 1)),
    hms(("{n} hours", 2), ("{n} minutes", 2), ("{n} seconds", 2)), ""])
states = [{}, {"services": {"airview": 0}}, {"services": {"airview": 3}},
          {"services": {"airview": "2"}}, {"services": {"airview": "x"}}, {"services": [2]}]
check("enabled (>0) and running (2), as its web reads them",
      [(T.airview_enabled(s), T.airview_running(s)) for s in states] == [
          (False, False), (False, False), (True, False), (True, True), (False, False),
          (False, False)])
check("why it does not open: disabled, the management radio, or not yet",
      [T.airview_off_text(s) for s in (
          {"services": {"airview": 0}},
          {"services": {"airview": 3}, "provmode": {"status": 2, "timeout": 896}},
          {"services": {"airview": 1}})] == [
          tr("airView is disabled. It can be enabled in System tab."),
          tr("Management radio is enabled. airView functionality will be restored in {time}.",
             time=hms(("{n} minutes", 14), ("{n} seconds", 56))),
          tr("airView is not running on the device now (its web shows it greyed out). Try "
             "again in a moment.")])

print("=== its text views (the real capture) ===")
m = model(REAL)
wave = T.waveform_lines(m, 84, 20)
chart = wave[:20]
check("waveform: 20 rows of 84 columns (with its margin) and its axis", len(wave) == 22
      and all(len(line) == 90 for line in chart) and wave[20] == "     └" + "─" * 84
      and wave[21].endswith(" MHz"), str([len(x) for x in wave]))
check("  its scale on the left: -60 at the top, -120 at the bottom, every 10 dBm",
      [line[:6] for line in chart if "┤" in line[:6]] == [
          "  -60┤", "  -70┤", "  -80┤", "  -90┤", " -100┤", " -110┤", " -120┤"])
check("  at the bottom, the noise floor (its most likely band, or the power right now)",
      set(chart[-1][6:]) <= {"█", "•"} and "█" in chart[-1])
check("  and each frequency with the power right now on one row (at most)",
      all(sum(line[6 + c] == "•" for line in chart) <= 1 for c in range(84))
      and sum(line.count("•") for line in chart) > 40)
labels = wave[21][6:].split()
check("  the frequencies of its axis, in order, the first one from its tick",
      labels == ["5100", "5200", "5300", "5400", "5500", "5600", "5700", "5800", "5900",
                 "MHz"] and wave[21].startswith("      5100"), wave[21])
narrow = T.waveform_lines(model(REAL), 30, 6)
check("  narrow: fewer ticks (every 200 MHz), not overlapping", len(narrow) == 8 and all(
    len(x) == 36 for x in narrow[:6]) and narrow[7].split() == ["5200", "5400", "5600", "5800",
                                                               "MHz"], str(narrow[7]))
check("  if two ticks overlap, the second one is not written", T.freq_axis(m, 49, 648)[1]
      == "      5100      5300  5400  5500  5600  5700  5800 5900 MHz")
fall = T.waterfall_lines(m, 84, 20)
check("waterfall with one read: 19 empty rows and its own at the bottom", len(fall) == 22
      and fall[:19] == ["     │"] * 19 and len(fall[19]) == 90 and fall[19].strip("│ ")
      and fall[20] == wave[20], str(fall[18:20]))
check("  with 27 reads in 20 rows: the last 20", T.waterfall_lines(mm, 84, 20)[0]
      != "     │" and len(T.waterfall_lines(mm, 84, 20)) == 22)
check("its power characters, at the steps of its color scale", [
    T._power_glyph(v) for v in (math.nan, 3, 4, 9, 10, 14, 18, 21, 25, 40)] == [
    " ", " ", "·", "·", ":", "░", "▒", "▓", "█", "█"]
      and T.power_legend() == "· -117  : -105  ░ -97  ▒ -89  ▓ -83  █ -75")
now = time.mktime((2026, 11, 9, 14, 36, 20, 0, 0, -1))
noise = T.noise_lines(model(REAL, anze=60), 84, 20, now)
check("noise, peak per minute: 20 rows (the newest at the bottom) and its axis", len(noise) == 22
      and all(len(x) == 6 + 84 for x in noise[:20]) and noise[20] == wave[20],
      str([len(x) for x in noise[:3]]))
check("  its time every 3 rows (it has more than 15), at minutes that are multiples of 3",
      [(i, x[:5]) for i, x in enumerate(noise[:20]) if x[5] == "┤"] == [
          (2, "14:18"), (5, "14:21"), (8, "14:24"), (11, "14:27"), (14, "14:30"),
          (17, "14:33")], str([x[:6] for x in noise[:20]]))
hourly = T.noise_lines(model(REAL, anze=3600), 84, 20, now)
check("  average per hour: its 11 rows at the bottom, its time every 2 (at even hours)",
      hourly[:9] == ["     │"] * 9 and [(i, x[:5]) for i, x in enumerate(hourly[:20])
                                         if x[5] == "┤"] == [
          (9, "04:00"), (11, "06:00"), (13, "08:00"), (15, "10:00"), (17, "12:00"),
          (19, "14:00")], str([x[:6] for x in hourly[:20]]))
few = copy.deepcopy(REAL)
few["latestAnze"] = [r if i < 3 else [0] * 161 for i, r in enumerate(few["latestAnze"])]
check("  with few rows, its time on each one", [x[:5] for x in T.noise_lines(
    model(few, anze=3600), 84, 20, now)[17:20]] == ["12:00", "13:00", "14:00"])
empty = copy.deepcopy(REAL)
empty["latestAnze"] = [[0] * 161 for _ in range(24)]
check('  with none, nothing (its web: "Gathering data...")',
      T.noise_lines(model(empty, anze=3600), 84, 20, now) is None)


class Shown:
    """A session already read, for airview_text."""

    def __init__(self, mdl, status=None, blocked=None, error=None):
        self.model, self.status = mdl, status or {}
        self.blocked, self.error = blocked, error


text = T.airview_text(Shown(model(REAL)), "waveform", 94, 24, now)
check("the waveform screen: its title, its legend and its graph in 24 lines",
      text.split("\n")[:2] == [
          tr("Waveform View") + " · " + tr("Power (dBm) per MHz"),
          tr("Probability") + ": · 0.5  ░ 0.6  ▒ 0.7  ▓ 0.8  █ 0.9–1   • "
          + tr("the power right now")]
      and len(text.split("\n")) == 24 and max(len(x) for x in text.split("\n")) <= 94,
      text.split("\n")[1])
check("  the waterfall's and the noise's", [T.airview_text(Shown(model(REAL)), v, 94, 24, now)
                                         .split("\n")[0] for v in T.AIRVIEW_VIEWS[1:]] == [
    tr("Waterfall View") + " · " + tr("a row per new read, the newest at the bottom"),
    tr("Ambient Noise Level") + " · " + tr("Minute Peak"),
    tr("Ambient Noise Level") + " · " + tr("Hour Average")])
check("  without noise rows: its notice (not with the management radio on)",
      T.airview_text(Shown(model(empty)), "noise3600", 94, 24, now).split("\n")[2]
      == tr("Gathering data... This may take up to 1 hour to populate.")
      and len(T.airview_text(Shown(model(empty), {"provmode": {"status": 2}}), "noise3600",
                             94, 24, now).split("\n")) == 2)
check("  blocked: only its message; no data yet: that it waits; a failure: at the top", [
    T.airview_text(Shown(model(REAL), blocked="X"), "waveform", 94, 24, now),
    T.airview_text(Shown(T.AirviewData()), "waveform", 94, 24, now),
    T.airview_text(Shown(model(REAL), error="E"), "waterfall", 94, 24, now).split("\n")[0]] ==
      ["X", tr("Waiting for its first data…"), tr("No answer: {error}", error="E")])

print("=== the simulator ===")
fake = S.airview_payload(3, 5745, 20)
check("its /airviewdata.cgi: the keys of the real one, in its order and with its sizes",
      list(fake) == list(REAL) and all(
          (len(fake[k]), len(fake[k][0]) if isinstance(fake[k][0], list) else 0)
          == (len(REAL[k]), len(REAL[k][0]) if isinstance(REAL[k][0], list) else 0)
          if isinstance(REAL[k], list) else fake[k] == REAL[k] for k in REAL)
      and fake["stFreqGridLabels"] == REAL["stFreqGridLabels"]
      and fake["ltFreqGridLabels"] == REAL["ltFreqGridLabels"])
check("  each histogram adds up to its total; 11 hours of noise", all(
    sum(fake["pwrHistogram"][u][r] for u in range(64)) == fake["totalSamples"][r]
    for r in range(648)) and len(model(fake, anze=3600).noise_rows()) == 11)
loud = model(fake)
check("  its channel (5745 MHz, 20 MHz) is heard loud; the noise is not",
      loud.levels[9][int((5745 - 5095.625) / 1.25)] > -90
      and loud.levels[0][int((5300 - 5095.625) / 1.25)] < -100)


def start(port, version=8):
    srv = S.make_server(version, port, restart=0.3, test_secs=60)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    return srv, srv.dev, ui, app


def stop(srv):
    srv.shutdown()
    srv.server_close()


def session_flow(port):
    print("=== its dialog's loop (its runAjax) against the simulator ===")
    srv, d, ui, app = start(port)
    try:
        dev = app.dev
        check("the client reads /airviewdata.cgi; running (services.airview 2)",
              T.airview_usable(dev.airview_data()) and T.airview_running(dev.status()))
        d.airview_period = 0
        clock = [0.0]
        calls = []
        real_status = dev.status

        def status():
            calls.append(clock[0])
            return real_status()
        dev.status = status
        s = T.AirviewSession(dev, clock=lambda: clock[0])
        for t in (0.0, 0.5, 1.0, 1.4, 2.1):
            clock[0] = t
            s.step()
        check("each new read: 800 ms and one more row; its status, every second",
              s.interval == 0.8 and len(s.model.waterfall) == 5 and calls == [0.0, 1.0, 2.1]
              and s.blocked is None and s.error is None, str((s.interval, calls)))
        d.airview_period = 1000
        s.step()
        s.step()
        check("  with nothing new: 400 ms, and no row", s.interval == 0.4
              and len(s.model.waterfall) == 5, str((s.interval, len(s.model.waterfall))))
        d.running["airview.status"] = "disabled"
        clock[0] = 5.0
        first = dev.airview_data()
        s.step()
        check("disabled: services.airview 0, its data unchanged and its message",
              s.blocked == tr("airView is disabled. It can be enabled in System tab.")
              and dev.airview_data() == first and T.airview_state(s.status) == 0)
        d.running["airview.status"] = "enabled"
        clock[0] = 6.0
        s.step()
        check("  enabled again: no message", s.blocked is None)
        d.provmode_until = time.time() + 896.5
        try:
            dev.airview_data()
            empty_error = None
        except T.AirOSError as e:
            empty_error = str(e)
        clock[0] = 7.0
        s.step()
        restored = tr("Management radio is enabled. airView functionality will be restored in "
                      "{time}.").partition("{")[0]      # (then "14 minutes, 5x seconds.")
        check("the management radio: services.airview 3, its data empty and its message",
              T.airview_state(s.status) == 3 and empty_error is not None
              and tr("(empty)") in empty_error and s.blocked is not None
              and s.blocked.startswith(restored + tr("{n} minutes", n=14) + ", 5"),
              str(s.blocked))
        d.provmode_until = None
        clock[0] = 8.0
        s.step()
        check("  turned off: no message", s.blocked is None and s.error is None)
        rows = len(s.model.waterfall)
        stop(srv)
        clock[0] = 9.0
        s.step()
        shown = T.airview_text(s, "waterfall", 94, 24, time.time()).split("\n")
        check("no answer (and no management radio): it says so, and keeps what it had",
              s.error is not None and s.blocked is None and len(s.model.waterfall) == rows
              and shown[0].startswith(tr("No answer: {error}").partition("{")[0])
              and shown[1].startswith(tr("Waterfall View")), str(shown[:2]))

        class Odd:
            def status(self):
                return {"services": {"airview": 2}}

            def airview_data(self):
                return {"stFreqGridNumPts": 648}
        odd = T.AirviewSession(Odd())
        odd.step()
        check("data it does not know: it says so", odd.error == tr(
            "Its airView data is not what this program knows.") and odd.model.data is None)
    finally:
        stop(srv)


def screen_flow(port):
    print("=== the screen: Tools → airView, its four views live ===")
    srv, d, ui, app = start(port)
    try:
        d.airview_period = 0
        ui.script = [("menu", "e"), ("menu", "1"), ("watch", 3), ("menu", "2"), ("watch", 3),
                     ("menu", "3"), ("watch", 1), ("menu", "4"), ("watch", 1), ("menu", None),
                     ("menu", None)]
        app.tools_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        tools = [items for t, items, _ in ui.menus if t == tr("Tools")][0]
        check('in "Tools", the first one (as in its web)', tools[0] == (
            "e", tr("airView: the spectrum the device hears, live")) and tools[1][0] == "a")
        views = [items for t, items, _ in ui.menus if t == "airView"]
        check("its menu: its three views and the two noise averages", [k for k, _ in views[0]]
              == ["1", "2", "3", "4"]
              and views[0][2][1] == tr("Ambient Noise Level") + ": " + tr("Minute Peak")
              and [default for t, _, default in ui.menus if t == "airView"] == [
                  "1", "1", "2", "3", "4"])
        wave, fall, minute, hour = ui.watched
        check("the waveform, live (a read each time)", len(wave) == 3 and all(
            w.startswith(tr("Waveform View")) and "5100" in w for w in wave)
              and wave[0] != wave[1])
        data_rows = [sum(1 for x in f.split("\n")[2:-2] if x.strip("│ ")) for f in fall]
        check("the waterfall: one more row with each read (the same session)",
              data_rows == [4, 5, 6], str(data_rows))
        check("the noise: peak per minute and average per hour", minute[0].split("\n")[0]
              == tr("Ambient Noise Level") + " · " + tr("Minute Peak") and hour[0].split("\n")[0]
              == tr("Ambient Noise Level") + " · " + tr("Hour Average") and "┤" in hour[0])
        ui.log.clear()
        ui.menus.clear()
        d.running["airview.status"] = "disabled"
        ui.script = [("menu", "e"), ("menu", None)]
        app.tools_menu()
        check("disabled: its message, without opening it (its web shows it greyed out)",
              [t for k, ti, t in ui.log if k == "msgbox"] == [
                  tr("airView is disabled. It can be enabled in System tab.")]
              and not [t for t, _, _ in ui.menus if t == "airView"])
        d.running["airview.status"] = "enabled"
        ui.menus.clear()
        board = app.limits.board
        app.limits.board = {k: v for k, v in board.items() if "web_exclude" not in k}
        ui.script = [("menu", None)]
        app.tools_menu()
        check("a board without airView (no radio left out of its web): it is not there",
              [k for k, _ in rows(ui, tr("Tools"))] == ["a", "s", "d", "n", "r", "v", "p"])
        app.limits.board = board
    finally:
        stop(srv)
    print("=== no answer when opening it ===")
    srv, d, ui, app = start(port + 2)
    try:
        def down():
            raise T.AirOSError("down")
        app.dev.status = down
        ui.script = [("menu", "e"), ("menu", None)]
        app.tools_menu()
        check("it says so, without opening it", [(ti, t) for k, ti, t in ui.log
                                                 if k == "msgbox"] == [(tr("No answer"), "down")]
              and not [t for t, _, _ in ui.menus if t == "airView"])
    finally:
        stop(srv)
    print("=== airOS 6: its airView is something else ===")
    srv, d, ui, app = start(port + 1, version=6)
    try:
        ui.script = [("menu", "e"), ("menu", None)]
        app.tools_menu()
        check("it says so", [t for k, ti, t in ui.log if k == "msgbox"] == [
            tr("On airOS 6, airView is a Java application of its web (Tools menu); this "
               "program does not show it.")])
    finally:
        stop(srv)


def rows(ui, title):
    return [items for t, items, _ in ui.menus if t == title][-1]


session_flow(19961)
screen_flow(19965)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
