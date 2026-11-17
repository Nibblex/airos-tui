"""airView, the spectrum analyzer of the airOS 8 web (the first of its Tools): its model as its
web computes it (its airviewData, fed by /airviewdata.cgi as its dialog reads it when it has no
WebSocket: every 800 ms, or 400 ms when nothing was new) and its three views in text: the
waveform (how often each power is heard at each frequency, and the power of the moment), the
waterfall (its last 24 reads, the newest at the bottom) and the ambient noise level (a row per
minute, its peak, or per hour, its average; the newest at the bottom).

In text, a column is a group of its frequencies (the highest value of them) and its web's
colours are characters, denser the stronger: its colour scale's stops (blue, light blue, cyan,
green, yellow-green, yellow, orange and red) and, in its waveform, its ten bands two by two."""
from __future__ import annotations

import itertools
import math
import time
from bisect import bisect_right

from ..core.i18n import N_, tr
from ..core.errors import AirOSError
from ..core.config import js_round, js_str
from .system import management_radio_up, provmode_state

AIRVIEW_READ, AIRVIEW_IDLE = 0.8, 0.4   # its runInterval: 800 ms, 400 ms if nothing was new
AIRVIEW_STATUS_EVERY = 1.0              # its statusReloadInterval (whether it is enabled…)
AIRVIEW_RUNNING = 2                     # its services.airview: 0 off, 2 running (3: the
                                        # management radio has it, as a real 8.7.14 says)
WATERFALL_ROWS = 24                     # its getWaterfallTimeBins
CDF_LEVELS = 10
CDF_FLOOR = -125                        # (its levels start there, whatever its minimum is)
WAVEFORM_BOTTOM = -120                  # its waveform's scale: from -120 dBm
NOISE_SERIES = {60: "mediumAnze", 3600: "latestAnze"}
NOISE_NAMES = {60: N_("Minute Peak"), 3600: N_("Hour Average")}
# Its waveform's ten bands (the one under the median the last, its legend's 1) in five
# characters, two bands each, with its legend's probability for them.
PROB_GLYPHS = "·░▒▓█"
PROB_TICKS = ("0.5", "0.6", "0.7", "0.8", "0.9–1")
INSTANT_GLYPH = "•"
# Its waterfall's and its noise's colours (a value v is 2v-125 dBm in its legend): its colour
# scale's stops 4 (light blue), 10 (cyan), 14 (green), 18 (yellow-green), 21 (yellow) and 25
# (orange, red from 30); under 4 (blue), blank.
POWER_STEPS = (4, 10, 14, 18, 21, 25)
POWER_GLYPHS = " ·:░▒▓█"
_REQUIRED = {"stFreqGridNumPts": int, "stPowrMin": (int, float), "stPowrDel": (int, float),
             "stPowrNum": int, "stFreqGridLabels": list, "pwrHistogram": list,
             "totalSamples": list, "latestPower": list, "instantPower": list}


# -- its model ---------------------------------------------------------------------------

def _num(values, i: int) -> float:
    """values[i] as its web reads it: a missing one is undefined (NaN in its sums)."""
    if isinstance(values, list) and 0 <= i < len(values):
        v = values[i]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return math.nan


def _js_div(a: float, b: float) -> float:
    """JavaScript's a / b: by 0 it is Infinity (or NaN for 0 / 0)."""
    if math.isnan(a) or math.isnan(b):
        return math.nan
    if b == 0:
        return math.nan if a == 0 else math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _smooth(values: list[float]) -> list[float]:
    """Its smoothing, as it computes it: about two points to each side, but its window never
    lets the first point go (a slip of its web: from the fourth point on, it averages the first
    one with one point before and two after)."""
    n = len(values)
    out = [0.0] * n
    total, count = 0.0, 0
    for s in range(2):
        total += values[s] if s < n else math.nan
        count += 1
    for o in range(n):
        if o + 2 < n:
            total += values[o + 2]
            count += 1
        if o - 2 > 0:
            total -= values[o - 2]
            count -= 1
        out[o] = total / count
    return out


def airview_usable(data) -> bool:
    """Whether a read has what its model computes with (its web would stop on it)."""
    if not isinstance(data, dict):
        return False
    for key, kind in _REQUIRED.items():
        v = data.get(key)
        if not isinstance(v, kind) or isinstance(v, bool):
            return False
    if not all(isinstance(f, (int, float)) and not isinstance(f, bool)
               for f in data["stFreqGridLabels"]):      # (its axis and steps are worked out)
        return False
    return data["stPowrDel"] != 0


class AirviewData:
    """Its airviewData: the last read and what its views draw, as its web computes them."""

    def __init__(self) -> None:
        self.data: dict | None = None
        self._last: float | None = None        # its l (the first read does not set it)
        self.levels: list[list[float]] = []   # its CDF patches: the top one (93 %) first
        self.latest: list[float] = []         # latestPower, in dBm
        self.instant: list[list[float]] = []  # its frames of instantPower, in dBm
        self.waterfall: list[list[float]] = []  # its rows, the oldest first (24 at most)
        self.anze_interval = 60               # its ambient noise: 60 (Minute Peak) or 3600
        self._frame = 0                       # its instant power's frame (v)

    @staticmethod
    def timestamp(data: dict) -> float:
        """Its getTimestamp: the sum of totalSamples (it grows as the device reads)."""
        total = 0.0
        for v in data.get("totalSamples") or []:
            if isinstance(v, (int, float)):
                total += v
        return total

    def is_updated(self, data: dict) -> bool:
        return self._last is None or self.timestamp(data) != self._last

    def add_data(self, data: dict) -> None:
        if self.data is not None:
            self._last = self.timestamp(data)
        self.data = data

    def calculate(self) -> None:
        """Its calculate: the powers in dBm; each frequency's ten levels (where the share of
        its samples above falls under 0.52, 0.47… 0.07: from about its median up), smoothed,
        the top one first; and a row of its waterfall."""
        d = self.data
        npts, pnum = d["stFreqGridNumPts"], d["stPowrNum"]
        pmin, pdel = d["stPowrMin"], d["stPowrDel"]

        def dbm(v: float) -> float:
            return pmin + v * pdel
        self.latest = [dbm(_num(d["latestPower"], i)) for i in range(len(d["latestPower"]))]
        self.instant = [[dbm(_num(frame, i)) for i in range(len(frame))]
                        for frame in d["instantPower"] if isinstance(frame, list)]
        levels = [[float(CDF_FLOOR)] * npts for _ in range(CDF_LEVELS)]
        hist, totals = d["pwrHistogram"], d["totalSamples"]
        for r in range(npts):
            left, c, total = 1.0, 0, _num(totals, r)
            for u in range(pnum):
                left -= _js_div(_num(hist[u] if u < len(hist) else None, r), total)
                while c < CDF_LEVELS and left < .52 + -.05 * c:
                    levels[c][r] = dbm(u)
                    c += 1
        self.levels = [_smooth(lv) for lv in levels][::-1]
        row = []
        for s in range(npts):
            v = (_num(self.latest, s) - pmin) / pdel
            row.append(js_round(v) - 1 if math.isfinite(v) else math.nan)
        self.waterfall = (self.waterfall + [row])[-WATERFALL_ROWS:]

    def instant_frame(self) -> list[float]:
        """Its getInstantPowerFrame: each call, the next of its frames."""
        if not self.instant:
            return []
        frame = self.instant[self._frame % len(self.instant)]
        self._frame += 1
        return frame

    def noise_rows(self) -> list[list]:
        """Its getNoiseData: the rows of the ambient noise chosen (the newest first), without
        the empty ones (all of it 0: not gathered yet)."""
        rows = (self.data or {}).get(NOISE_SERIES[self.anze_interval])
        if not isinstance(rows, list):               # (not gathered, or not what it should be)
            return []
        return [r for r in rows if isinstance(r, list) and any(v != 0 for v in r)]

    def freq_delta(self) -> float | None:
        """Its getFreqDelta: the usual step between its frequencies (on a tie, the larger)."""
        labels = (self.data or {}).get("stFreqGridLabels") or []
        counts: dict = {}
        for a, b in itertools.pairwise(labels):
            counts[b - a] = counts.get(b - a, 0) + 1
        if not counts:
            return None
        top = max(counts.values())
        return max(k for k, v in counts.items() if v == top)

    def labels(self) -> list[tuple[int, float]]:
        """Its getLabels: (index, frequency) of the first, of each one after a jump and of
        the last (its frequency axis goes straight between them)."""
        freqs = (self.data or {}).get("stFreqGridLabels") or []
        if not freqs:
            return []
        step = self.freq_delta()
        out = [(0, freqs[0])]
        out += [(i, freqs[i]) for i in range(1, len(freqs)) if freqs[i] - freqs[i - 1] != step]
        return out + [(len(freqs) - 1, freqs[-1])]

    def waveform_top(self) -> int:
        """Its waveform's scale goes from -120 dBm up to 20 dB over the first of -120, -80,
        -40 and 0 that the bottoms of its bands (its y0) do not pass (else, 0)."""
        high = -200.0
        for k, band in enumerate(self.levels):
            below = self.levels[k + 1] if k + 1 < len(self.levels) else None
            for n in range(len(band)):
                y0 = below[n] if below is not None else -130
                if y0 > high:
                    high = y0
        r = 0
        for s in range(4):
            r = 40 * s - 120
            if high <= r:
                break
        return r + 20


# -- the views in text --------------------------------------------------------------------

def d3_ticks(lo: float, hi: float, count: int = 10) -> list[float]:
    """d3's (v3) linear ticks: about count round values from lo to hi, where its axes put
    their labels (its steps here are whole: of frequencies and of dBm)."""
    if hi < lo:
        lo, hi = hi, lo
    span = hi - lo
    if not span > 0 or count < 1:
        return []
    step = 10 ** math.floor(math.log(span / count) / math.log(10))
    err = count / span * step
    if err <= .15:
        step *= 10
    elif err <= .35:
        step *= 5
    elif err <= .75:
        step *= 2
    start = math.ceil(lo / step) * step
    stop = math.floor(hi / step) * step + step * .5
    out: list[float] = []
    while start + len(out) * step < stop:
        out.append(start + len(out) * step)
    return out


def _spans(n: int, width: int) -> list[tuple[int, int]]:
    """The points of each column: width columns (fewer if there are fewer points)."""
    w = max(1, min(width, n))
    return [(i * n // w, (i + 1) * n // w) for i in range(w)]


def _peak(values, lo: int, hi: int) -> float:
    """The highest of values[lo:hi] (its missing ones left out; NaN if none)."""
    best = math.nan
    for v in values[lo:hi] if isinstance(values, list) else ():
        if isinstance(v, (int, float)) and not math.isnan(v) and (math.isnan(best) or v > best):
            best = v
    return best


def _power_glyph(v: float) -> str:
    if math.isnan(v):
        return " "
    return POWER_GLYPHS[sum(1 for s in POWER_STEPS if v >= s)]


def _freq_index(labels: list[tuple[int, float]], freq: float) -> float:
    """Its polylinear scale: where a frequency falls between its labels, as an index."""
    xs, ys = [f for _, f in labels], [i for i, _ in labels]
    k = len(xs) - 1
    if k < 1:
        return float(ys[0]) if ys else 0.0
    j = max(0, min(bisect_right(xs, freq, 1, k) - 1, k - 1))
    x0, x1, y0, y1 = xs[j], xs[j + 1], ys[j], ys[j + 1]
    return y0 + (freq - x0) / (x1 - x0) * (y1 - y0) if x1 != x0 else float(y0)


def _margin(label: str = "", tick: bool = False) -> str:
    return f"{label:>5}" + ("┤" if tick else "│")


def freq_axis(model: AirviewData, width: int, points: int) -> list[str]:
    """Its frequency axis under a chart of width columns over points points: its line and its
    ticks' frequencies (MHz), where its scale puts them (the first one starts at its tick,
    as in its web; the ones that do not fit are left out)."""
    labels = model.labels()
    lines = [" " * 5 + "└" + "─" * width]
    if not labels or points < 2:
        return lines
    cells, end = [" "] * width, -1
    for k, f in enumerate(d3_ticks(labels[0][1], labels[-1][1],
                                   max(2, min(10, width // 7)))):
        col = min(width - 1, max(0, int(_freq_index(labels, f) / (points - 1) * width)))
        text = js_str(f / 1e4)
        start = max(0, min(col if k == 0 else col - len(text) // 2, width - len(text)))
        if start <= end:
            continue
        cells[start:start + len(text)] = text
        end = start + len(text)
    return lines + [" " * 6 + "".join(cells).rstrip() + " MHz"]


def waveform_lines(model: AirviewData, width: int, height: int) -> list[str]:
    """Its Waveform View in width columns and height rows (and its axis): its bands, from its
    median down (the densest) to the top one (the lightest, 93 %), and the power of the
    moment (the next of its frames each time, as its web animates them)."""
    n = len(model.levels[0]) if model.levels else 0
    spans = _spans(n, width)
    top = model.waveform_top()
    step = (top - WAVEFORM_BOTTOM) / height
    frame = model.instant_frame()

    def row_of(v: float) -> int:
        return min(height - 1, int((top - v) / step))
    cols = []
    for lo, hi in spans:
        lv = [_peak(model.levels[CDF_LEVELS - 1 - c], lo, hi) for c in range(CDF_LEVELS)]
        now = _peak(frame, lo, hi)
        cols.append((lv, row_of(now) if WAVEFORM_BOTTOM <= now <= top else None))
    ticks: dict[int, float] = {}
    for t in d3_ticks(WAVEFORM_BOTTOM, top, 5):
        ticks.setdefault(row_of(t), t)
    lines = []
    for y in range(height):
        p = top - (y + .5) * step
        cells = []
        for lv, now_row in cols:
            if now_row == y:
                cells.append(INSTANT_GLYPH)
            elif math.isnan(lv[-1]) or p > lv[-1]:
                cells.append(" ")
            else:                                  # the band: how many levels are under p
                cells.append(PROB_GLYPHS[(CDF_LEVELS - 1 - sum(1 for v in lv if v < p)) // 2])
        t = ticks.get(y)
        lines.append(_margin(js_str(t) if t is not None else "", t is not None)
                     + "".join(cells))
    return lines + freq_axis(model, len(spans), n)


def waterfall_lines(model: AirviewData, width: int, height: int) -> list[str]:
    """Its Waterfall View: its rows (the newest at the bottom), blank above until it has 24,
    as its web fills it; the ones that do not fit in height, the oldest, left out."""
    rows = model.waterfall[-height:]
    n = len(rows[-1]) if rows else len(model.latest)
    spans = _spans(n, width)
    lines = [_margin()] * (min(WATERFALL_ROWS, height) - len(rows))
    lines += [_margin() + "".join(_power_glyph(_peak(r, lo, hi)) for lo, hi in spans)
              for r in rows]
    return lines + freq_axis(model, len(spans), len(model.latest))


def _noise_tick(start: float, unit: int, every: int) -> float | None:
    """The time of its time axis' tick in [start, start + unit), if there is one: whole
    minutes (or hours) multiple of every, by the clock."""
    if unit == 60:
        return start if time.localtime(start).tm_min % every == 0 else None
    t = start + (60 - time.localtime(start).tm_min) % 60 * 60
    if t < start + unit and time.localtime(t).tm_hour % every == 0:
        return t
    return None


def noise_lines(model: AirviewData, width: int, height: int, now: float) -> list[str] | None:
    """Its Ambient Noise Level (its interval: Minute Peak or Hour Average): a row per minute
    or per hour, the newest at the bottom, with its time axis (every 1, 2 or 3 of them, as its
    web: by how many rows it has); None if it has no row yet."""
    rows = model.noise_rows()
    if not rows:
        return None
    unit = model.anze_interval
    every = 3 if len(rows) > 15 else 2 if len(rows) > 5 else 1
    shown = rows[:height]
    lines = [_margin()] * (min(WATERFALL_ROWS, height) - len(shown))
    end = now // 60 * 60                           # (its "now", in whole minutes)
    for r in range(len(shown) - 1, -1, -1):        # the oldest at the top
        start = end - (r + 1) * unit
        tick = _noise_tick(start, unit, every)
        label = time.strftime("%H:%M", time.localtime(tick)) if tick is not None else ""
        lines.append(_margin(label, tick is not None) + "".join(
            _power_glyph(_peak(shown[r], lo, hi)) for lo, hi in _spans(len(shown[r]), width)))
    width = len(_spans(len(rows[0]), width))
    return lines + freq_axis(model, width, len(model.latest))


def _legend(glyphs: str, labels) -> str:
    return "  ".join(f"{g} {t}" for g, t in zip(glyphs, labels))


def power_legend() -> str:
    """Its legend of power (dBm per MHz: a value v is 2v-125), at each character's start."""
    return _legend(POWER_GLYPHS[1:], [js_str(2 * s - 125) for s in POWER_STEPS])


# -- its state and its messages -------------------------------------------------------------

def airview_state(status: dict) -> float:
    """Its services.airview (0 off, 2 running), as a number (NaN if it is not one)."""
    services = status.get("services") if isinstance(status.get("services"), dict) else {}
    try:
        return float(services.get("airview", 0))
    except (TypeError, ValueError):
        return math.nan


def airview_enabled(status: dict) -> bool:
    """Its isEnabledAirview."""
    return airview_state(status) > 0


def airview_running(status: dict) -> bool:
    """Its isRunningAirview: only then its Tools menu opens it."""
    return airview_state(status) == AIRVIEW_RUNNING


def seconds_to_hms(seconds: float) -> str:
    """Its secondsToHms: "1 hour, 2 minutes, 3 seconds", without the parts at 0."""
    t = int(seconds)
    h, m, s = t // 3600, t % 3600 // 60, t % 3600 % 60
    parts = []
    if h > 0:
        parts.append(tr("{n} hour", n=h) if h == 1 else tr("{n} hours", n=h))
    if m > 0:
        parts.append(tr("{n} minute", n=m) if m == 1 else tr("{n} minutes", n=m))
    if s > 0:
        parts.append(tr("{n} second", n=s) if s == 1 else tr("{n} seconds", n=s))
    return ", ".join(parts)


def management_radio_text(status: dict) -> str:
    return tr("Management radio is enabled. airView functionality will be restored in {time}.",
              time=seconds_to_hms(provmode_state(status)[1] or 0))


def airview_off_text(status: dict) -> str:
    """Why its Tools menu does not open it (it shows it greyed out)."""
    if not airview_enabled(status):
        return tr("airView is disabled. It can be enabled in System tab.")
    if management_radio_up(status):
        return management_radio_text(status)
    return tr("airView is not running on the device now (its web shows it greyed out). Try "
              "again in a moment.")


class AirviewSession:
    """Its airView dialog's loop (its runAjax) over a device: each step, the status if a second
    went by (its statusReloadInterval), a read of /airviewdata.cgi and what its dialog does
    with it; interval, how long until the next one (its runInterval)."""

    def __init__(self, dev, clock=time.monotonic, status: dict | None = None) -> None:
        self.dev, self.clock = dev, clock
        self.model = AirviewData()
        self.status: dict = status or {}       # (the last one read: until it reads another)
        self._status_at: float | None = None
        self.blocked: str | None = None       # the message over its views
        self.error: str | None = None         # the last read, if it failed (over them)
        self.interval = AIRVIEW_READ

    def step(self) -> None:
        now = self.clock()
        if self._status_at is None or now - self._status_at >= AIRVIEW_STATUS_EVERY:
            try:
                self.status = self.dev.status()
            except AirOSError:
                pass                            # (the last one it had)
            self._status_at = now
        try:
            data = self.dev.airview_data()
        except AirOSError as e:                 # (with the management radio on: empty)
            if management_radio_up(self.status):
                self.blocked = management_radio_text(self.status)
            else:
                self.error = str(e)
            return
        if not airview_usable(data):
            self.error = tr("Its airView data is not what this program knows.")
            return
        self.error = None
        if self.status and not airview_enabled(self.status):    # (none read: not known)
            self.blocked = tr("airView is disabled. It can be enabled in System tab.")
            return
        self.blocked = None
        if self.model.is_updated(data):
            self.model.add_data(data)
            self.model.calculate()
            self.interval = AIRVIEW_READ
        else:
            self.interval = AIRVIEW_IDLE


AIRVIEW_VIEWS = ("waveform", "waterfall", "noise60", "noise3600")


def airview_text(session: AirviewSession, view: str, width: int, height: int,
                 now: float) -> str:
    """A view's text for width × height characters: the message over it when there is one,
    or the view with its legend and its frequency axis."""
    if session.blocked:
        return session.blocked
    model, head = session.model, []
    if session.error:
        head.append(tr("No answer: {error}", error=session.error))
    if model.data is None:
        return "\n".join(head + [tr("Waiting for its first data…")])
    w = max(10, width - 10)                     # (its margin and " MHz")
    if view == "waveform":
        legend = tr("Probability") + ": " + _legend(PROB_GLYPHS, PROB_TICKS)
        now_legend = INSTANT_GLYPH + " " + tr("the power right now")
        head.append(tr("Waveform View") + " · " + tr("Power (dBm) per MHz"))
        head += ([legend + "   " + now_legend] if len(legend) + 3 + len(now_legend) <= width
                 else [legend, now_legend])          # (narrow: its own line, not wrapped)
        body = waveform_lines(model, w, max(4, height - len(head) - 2))
    elif view == "waterfall":
        head += [tr("Waterfall View") + " · " + tr("a row per new read, the newest at the "
                                                    "bottom"),
                 tr("Power (dBm) per MHz") + ": " + power_legend()]
        body = waterfall_lines(model, w, max(4, height - len(head) - 2))
    else:
        model.anze_interval = 3600 if view == "noise3600" else 60
        head += [tr("Ambient Noise Level") + " · " + tr(NOISE_NAMES[model.anze_interval]),
                 tr("Power (dBm) per MHz") + ": " + power_legend()]
        body = noise_lines(model, w, max(4, height - len(head) - 2), now)
        if body is None:
            body = [] if management_radio_up(session.status) else [
                tr("Gathering data... This may take up to 1 hour to populate.")]
    return "\n".join(head + body)
