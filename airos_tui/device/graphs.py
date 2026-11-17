"""The graphs of the airOS 8 web's main page: capacity, throughput and latency over the last
120 points, of this device and of the other end, kept as its chart data is kept (its data
model and how its chart controller feeds it from each status.cgi) and drawn with text."""
from __future__ import annotations

import math

from ..core.i18n import N_, tr
from ..core.config import _int, js_round, js_to_fixed

GRAPH_POINTS = 120              # its graphsTickCount
SERIES = ("rx", "tx", "capacity_rx", "capacity_tx", "average_capacity_rx",
          "average_capacity_tx", "latency")
_BLOCKS = "▁▂▃▄▅▆▇█"


def _overwrap(now: float, last: float) -> float:
    """Its handle_overwrap: what a byte counter went up, also when it wrapped around (not
    below 0: a 64-bit counter that starts again, a station that came back, is not a wrap)."""
    if now < last:
        if last > 2147483647:
            return max(0.0, 4294967295 - last + now)
        return now if now + last < 2415919103 else 2147483647 - last + now
    return now - last


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


class ChartData:
    """One of its charts' data: the samples of each status read (rates from the byte
    counters, in bits/s; capacity, in bits/s; latency, in ms) are averaged into a point
    every interval, from the third one on; the last GRAPH_POINTS points, zeros at first."""

    def __init__(self, points: int = GRAPH_POINTS):
        self.points = {key: [0.0] * points for key in SERIES}
        self._samples: dict[str, list[float]] = {key: [] for key in SERIES}
        self._last: dict[str, float] = {}
        self._ticks = 0

    def add_bytes(self, rx, tx, ms: float) -> None:
        """Its addValues: the counters and the milliseconds since the last read."""
        if ms <= 0:
            return
        for key, value in (("rx", rx), ("tx", tx)):
            if value:
                now = float(value)
                rate = (1000 * _overwrap(now, self._last[key]) / ms * 8
                        if key in self._last else math.nan)   # (its first one: NaN, so 0)
                self._last[key] = now
            else:
                rate = 0.0
            self._samples[key].append(rate)

    def add_capacity(self, rx_kbps, tx_kbps) -> None:
        for key, value in (("capacity_rx", rx_kbps), ("capacity_tx", tx_kbps)):
            self._samples[key].append(1000 * (value or 0))
            self._samples["average_" + key].append(1000 * (value or 0))

    def add_latency(self, ms) -> None:
        self._samples["latency"].append(float(ms or 0))

    def tick(self) -> None:
        """Its updateAvgValues: a new point of each series."""
        self._ticks += 1
        avg = {key: _mean(values) for key, values in self._samples.items()}
        for key in self._samples:
            self._samples[key] = []
        for side in ("rx", "tx"):            # (the capacity, never under the throughput)
            if avg["capacity_" + side] < avg[side]:
                avg["capacity_" + side] = avg[side]
        latency = avg["latency"]
        avg["latency"] = (self.points["latency"][-1] if math.isnan(latency)
                          else math.floor(10 * latency + 0.5) / 10)
        if self._ticks <= 2:
            return
        for key in SERIES:
            value = avg[key]
            self.points[key] = self.points[key][1:] + [0.0 if math.isnan(value) else value]


class LinkGraphs:
    """The two charts of its main page, fed as its chart controller feeds them: this device
    (its wireless interface, its isolated capacity and the mean latency of its stations)
    and each station (what the station sends and receives, its capacity and latency)."""

    def __init__(self):
        self.local = ChartData()
        self.stations: dict[str, ChartData] = {}
        self._uptime = self._timestamp = None

    def feed(self, st: dict) -> None:
        """One status.cgi (airOS 8), and a point of each chart."""
        host, w = st.get("host") or {}, st.get("wireless") or {}
        uptime, stamp = _int(host.get("uptime")) or 0, _int(host.get("timestamp")) or 0
        if self._timestamp is None:
            ms = 0.0
        else:
            ms = (4294967295 - self._timestamp + 1 + stamp if stamp < self._timestamp
                  else stamp - self._timestamp)
            if ms > 1000 * (uptime - self._uptime) + 1000:
                ms = 1000 * (uptime - self._uptime)
        ap = str(w.get("mode") or "").startswith("ap")
        wlan = next((i.get("status") or {} for i in st.get("interfaces") or []
                     if str(i.get("ifname") or "").startswith(("ath", "wlan"))), {})
        poll = w.get("polling") or {}
        stations = [s for s in w.get("sta") or [] if isinstance(s, dict) and s.get("mac")]
        self._add(self.local, wlan.get("rx_bytes"), wlan.get("tx_bytes"), ms, ap,
                  poll.get("ul_capacity"), poll.get("dl_capacity"),
                  math.floor(10 * _mean([s.get("tx_latency") or 0 for s in stations]) + 0.5)
                  / 10 if stations else 0)
        for s in stations:
            chart = self.stations.setdefault(s["mac"], ChartData())
            stats, airmax = s.get("stats") or {}, s.get("airmax") or {}
            self._add(chart, stats.get("tx_bytes"), stats.get("rx_bytes"), ms, ap,
                      airmax.get("dl_capacity"), airmax.get("ul_capacity"),
                      s.get("tx_latency") or 0)
        self._uptime, self._timestamp = uptime, stamp
        self.local.tick()
        for chart in self.stations.values():
            chart.tick()

    @staticmethod
    def _add(chart: ChartData, rx, tx, ms, ap: bool, rx_cap, tx_cap, latency) -> None:
        chart.add_bytes(rx, tx, ms)
        chart.add_capacity(*((rx_cap, tx_cap) if ap else (tx_cap, rx_cap)))
        chart.add_latency(latency)


def fmt_chart_bps(value: float) -> str:
    """Its formatBPS as its graphs show it: bps, kbps or Mbps, with its normalize_float (the
    stations table writes them with fixed decimals: stations.fmt_bps)."""
    if js_round(value) < 1000:
        unit = "bps"
    elif js_round(value / 1000) < 1000:
        value, unit = value / 1000, "kbps"
    else:
        value, unit = value / 1e6, "Mbps"
    digits = 0 if value > 100 else 1 if value > 10 else 2
    return f"{js_to_fixed(value, digits) if value else '0'} {unit}"


def sparkline(values: list[float], width: int, top: float) -> str:
    """The points in width columns (the highest of each group), from ▁ to █ up to top."""
    group = max(1, math.ceil(len(values) / width))
    cols = [max(values[i:i + group]) for i in range(0, len(values), group)][-width:]
    if top <= 0:
        return " " * len(cols)
    return "".join(" " if v <= 0 else _BLOCKS[min(7, int(v / top * 8))] for v in cols)


def fmt_ms(value: float) -> str:
    return f"{js_to_fixed(value, 1).rstrip('0').rstrip('.')} ms"


# What each block shows: its label, the series and how the value is written.
CHART_ROWS = ((N_("Capacity RX"), "capacity_rx", fmt_chart_bps),
              (N_("Capacity TX"), "capacity_tx", fmt_chart_bps),
              (N_("Throughput RX"), "rx", fmt_chart_bps),
              (N_("Throughput TX"), "tx", fmt_chart_bps),
              (N_("Latency"), "latency", fmt_ms))


def chart_lines(chart: ChartData, width: int = 40, average: bool = False) -> list[str]:
    """A block of the screen: a line per series (capacity and throughput share the scale
    of their direction, as its chart's axis); average: the capacity as its "average" chart
    (an access point in PtMP), which is not raised to the throughput."""
    out = []
    for label, key, fmt in CHART_ROWS:
        series = chart.points["average_" + key if average and key.startswith("capacity")
                              else key]
        if key == "latency":
            top = max(series)
        else:
            side = key[-2:]
            top = max([*chart.points["capacity_" + side], *chart.points[side]])
        out.append(f"{tr(label):<15}{sparkline(series, width, top):<{width}}  "
                   f"{fmt(series[-1])}")
    return out
