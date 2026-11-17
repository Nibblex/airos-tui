"""App: the stations connected (airOS 8, as the stations table of its web's main page): their
data, kicking one or all of them and, on a station, reconnecting to its access point (the
"Reconnect" of its main page); and the graphs of that page, live."""
from __future__ import annotations

import time

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import _int, gen3
from ..device.graphs import chart_lines, GRAPH_POINTS, LinkGraphs
from ..device.stations import BROADCAST_MAC, station_rows, station_text


class StationsScreens:
    """The stations table (airOS 8)."""

    def _is_ap(self) -> bool:
        mode = str((self.status.get("wireless") or {}).get("mode") or "")
        return mode.startswith("ap") or mode == "master"

    def _stations_label(self) -> str:
        """The main menu's entry: the stations of an access point, or the link of a station."""
        if not self._is_ap():
            return tr("Access point: the link and reconnecting")
        w = self.status.get("wireless") or {}
        count = (len([s for s in w.get("sta") or [] if isinstance(s, dict)])
                 if self.dev.flavor == "v8" else _int(w.get("count")) or 0)
        return tr("Stations ({count}): their data and kicking them", count=count)

    def _read_stations(self, title: str) -> list[dict]:
        """The stations now: status.cgi read twice, a second apart, as its web's (their
        throughput is the one between them, not since the list was last shown)."""
        self.ui.infobox(title, tr("Reading the stations…"))
        self.status = self.dev.status()
        before = {r["mac"]: r["bytes"] for r in station_rows(self.status, None, None)}
        since = time.monotonic()
        time.sleep(1)
        self.status = self.dev.status()
        return station_rows(self.status, before, time.monotonic() - since)

    def stations_menu(self) -> None:
        title = tr("Stations") if self._is_ap() else tr("Access point")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the stations are in the device's web (its main "
                                     "page)."))
            return
        rows = self._read_stations(title)
        while True:
            ap = self._is_ap()
            items = [(str(i), f"{r['name'] or r['mac']:<20.20}  {r['mac']}  "
                              f"{r['signal'] if r['signal'] is not None else '?':>4} dBm  "
                              f"{r['downlink']:>12} ↓  {r['ip'] or '-'}")
                     for i, r in enumerate(rows, 1)]
            if ap and rows:
                items.append(("k", tr("Kick All")))
            items.append(("r", tr("Read them again")))
            if rows:
                text = (tr("{count} station(s) connected. Choose one to see its data.",
                           count=len(rows)) if ap else
                        tr("The access point this station is connected to."))
            else:
                text = (tr("No station is connected.") if ap else
                        tr("Not connected to an access point."))
            choice = self.ui.menu(title, text, items)
            if choice is None:
                return
            if choice == "k":
                self._kick(BROADCAST_MAC, tr("Kick all the stations? They are disconnected and "
                                             "usually connect again at once."))
            elif choice != "r":
                row = rows[int(choice) - 1]
                if not self._station_detail(row, ap):
                    continue
            rows = self._read_stations(title)

    def _station_detail(self, row: dict, ap: bool) -> bool:
        """One station: its data and kicking it (on a station: reconnecting). True if
        something was sent."""
        name = row["name"] or row["mac"]
        items = [(("k", tr("Kick: disconnect it (it usually connects again at once)")) if ap
                  else ("c", tr("Reconnect: leave this access point and connect again")))]
        # (all its data: in a small window the menu shows what fits, and the rest behind "?")
        choice = self.ui.menu(name, station_text(row), items, ok=tr("Choose"),
                              cancel=tr("Back"))
        if choice is None:
            return False
        question = (tr("Kick {name}?", name=name) if ap else
                    tr("Reconnect to {name}? The link is cut for a moment.", name=name))
        return self._kick(row["mac"], question)

    def _kick(self, mac: str, question: str) -> bool:
        """As the web: POST /stakick.cgi with the wireless interface and the MAC."""
        title = tr("Stations") if self._is_ap() else tr("Access point")
        if not self.ui.ask(title, question, default_no=True):
            return False
        self.dev.kick_station("wlan0" if gen3(self._board()) else "ath0", mac)
        self.ui.msgbox(title, tr("Sent: it is disconnected; it usually connects again in a few "
                                 "seconds."))
        return True

    # -- the graphs of its main page (airOS 8) --

    def graphs_screen(self) -> None:
        """Its main page's graphs, live (a read of status.cgi every second, as its web): this
        device and the other end (on an access point with several stations, the one chosen),
        each with its capacity, throughput and latency."""
        title = tr("Graphs")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("The graphs are those of the airOS 8 web's main page; "
                                     "airOS 6 shows others, in its web."))
            return
        self.status = self.dev.status()           # (the stations now, not at startup)
        w = self.status.get("wireless") or {}
        mode = str(w.get("mode") or "")
        stations = [s for s in w.get("sta") or [] if isinstance(s, dict) and s.get("mac")]
        mac = stations[0]["mac"] if stations else None
        if len(stations) > 1:
            mac = self.ui.menu(title, tr("Which station, as the other end?"), [
                (s["mac"], f"{(s.get('remote') or {}).get('hostname') or '?'}  {s['mac']}")
                for s in stations[:40]])
            if mac is None:
                return
        names = {s["mac"]: (s.get("remote") or {}).get("hostname") or s["mac"]
                 for s in stations}
        graphs = LinkGraphs()
        ptmp_ap = mode == "ap-ptmp"

        def render() -> str:
            try:
                st = self.dev.status()
            except AirOSError as e:
                return tr("No answer: {error}", error=e)
            graphs.feed(st)
            lines = [tr("── This device ──")] + chart_lines(graphs.local, average=ptmp_ap)
            if mac and mac in graphs.stations:
                lines += ["", tr("── The other end: {name} ──", name=names.get(mac, mac))]
                lines += chart_lines(graphs.stations[mac])
            lines += ["", tr("The last {seconds} s, as its web: a point per read, from the "
                             "third one; capacity and throughput share the scale of each "
                             "direction.", seconds=GRAPH_POINTS)]
            return "\n".join(lines)
        due = [time.monotonic()]

        def every_second() -> float:
            """A read each second from the first, as its web's timer (not a second after each
            one ends): its points are its last seconds. Late, the next one at once."""
            now = time.monotonic()
            due[0] = max(due[0] + 1.0, now)
            return due[0] - now
        self.ui.watch(title, render, interval=every_second)
