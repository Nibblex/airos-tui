"""App: the Tools menu (antenna alignment, site survey, the device discovery of the web
and the one from this computer; the network tools are in nettools)."""
from __future__ import annotations

import time
import urllib.parse

from ..core.i18n import tr
from ..core.errors import AirOSError, SwitchDevice
from ..core.config import _int
from ..device.client import reachable
from ..device.discovery import ACTIVE_PORTS
from ..device.wireless import v8_modes
from ..device.tools import (align_link, align_text, ANTENNA_DISCOVERY_SCANS, format_found,
                            found_addresses, found_line, found_list, merge_found, survey_aps,
                            survey_changes, survey_frequency, survey_selectable)
from .discovery import discovery_screen, mark_connected

SURVEY_MAX = 60            # seconds: a scan takes about 20; the web asks every half second


class ToolsScreens:
    """The web's tools: nothing they do changes the device until it is applied."""

    def tools_menu(self) -> None:
        last = "a"
        while True:
            items = [("a", tr("Align the antenna: the signal of the link, live")),
                     ("s", tr("Site survey: the access points around")),
                     ("d", tr("Discovery: the devices the antenna finds, as in the web")),
                     ("n", tr("Ping: the device pings an address")),
                     ("r", tr("Traceroute: the way from the device to an address")),
                     ("v", tr("Speed test: the throughput to another device")),
                     ("p", tr("Discovery from your PC: the Ubiquiti devices on your "
                              "network"))]
            if self.airview_offered_here():      # (the first of its Tools, as in its web)
                items.insert(0, ("e", tr("airView: the spectrum the device hears, live")))
            choice = self.ui.menu(tr("Tools"), tr("The tools of the web, and the discovery "
                                                  "from your PC."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "e":
                self.airview_tool()
            elif choice == "a":
                self.align_antenna()
            elif choice == "s":
                self.site_survey()
            elif choice == "d":
                self.antenna_discovery()
            elif choice == "n":
                self.ping_tool()
            elif choice == "r":
                self.traceroute_tool()
            elif choice == "v":
                self.speedtest_tool()
            elif choice == "p":
                self.pc_discovery()

    # -- antenna alignment --

    def align_antenna(self) -> None:
        title = tr("Antenna alignment")
        mac = None
        if self.dev.flavor == "v8":
            self.status = self.dev.status()
            stations = [s for s in (self.status.get("wireless") or {}).get("sta") or []
                        if isinstance(s, dict)]
            if len(stations) == 1 and self._is_ap():
                # (that one: if it goes and another one comes, not the other one's signal as
                # if it were its, nor its best)
                mac = stations[0].get("mac")
            if len(stations) > 1:                # an access point: which station
                items = [(str(i), f"{(s.get('remote') or {}).get('hostname') or s.get('mac')}"
                                  f"  ({s.get('mac')}, {s.get('signal')} dBm)")
                         for i, s in enumerate(stations, 1)]
                choice = self.ui.menu(title, tr("The link to align:"), items)
                if choice is None:
                    return
                mac = stations[int(choice) - 1].get("mac")
        best: dict = {}

        def render() -> str:
            try:
                status = self.dev.status()
            except AirOSError as e:
                return tr("The device does not answer: {error}", error=e)
            return align_text(align_link(status, self.dev.flavor, mac), best)
        self.ui.watch(title, render)

    # -- discovery --

    def antenna_discovery(self) -> None:
        """The web's Device Discovery (airOS 8): the antenna asks with Ubiquiti discovery on
        its networks as its web does when it opens (POST /discovery.cgi listening 0.5 s, 1 s
        and, a second later, 5 s), one row per device; "Scan" starts again, as its button.
        Connecting to one leaves this device, as exiting does (its web opens it in a tab)."""
        title = tr("Device Discovery")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the device discovery is in the device's web "
                                     "(Tools)."))
            return
        own = {self.dev.host} | {str(i.get("hwaddr") or "").upper() for i in
                                 self.status.get("interfaces") or [] if isinstance(i, dict)}

        def mine(d: dict) -> bool:          # (the antenna itself, if it answers too)
            return bool((set(found_addresses(d)) | {str(d.get("hwaddr") or "").upper()})
                        & (own - {""}))
        found = self._antenna_scan(title)
        while True:
            lines, legend = mark_connected([found_line(d) for d in found],
                                           [mine(d) for d in found])
            items = [(str(i), line) for i, line in enumerate(lines, 1)] + [("r", tr("Scan"))]
            text = (tr("{count} device(s) found by the antenna on its networks. Choose one to "
                       "see its data.", count=len(found)) if found
                    else tr("No devices found.")) + legend
            choice = self.ui.menu(title, text, items, default="1" if found else "r",
                                  ok=tr("View"), cancel=tr("Back"))
            if choice is None:
                return
            if choice == "r":
                found = self._antenna_scan(title)
                continue
            d = found[int(choice) - 1]
            name = str(d.get("hostname") or d.get("ipv4") or d.get("hwaddr") or "?")
            ip = str(d.get("ipv4") or "")
            if mine(d):
                self.ui.msgbox(name, format_found(d) + "\n\n"
                               + tr("You are connected to this device."))
            elif ip and ip != "0.0.0.0" and not ip.startswith("169.254."):
                if self.ui.yesno(name, format_found(d), yes=tr("Connect"), no=tr("Back")) == 0:
                    self._switch_to(ip)
            else:
                self.ui.msgbox(name, format_found(d))

    def _antenna_scan(self, title: str) -> list[dict]:
        """The scans its web makes as it opens, one row per device; if none of them works,
        it says why."""
        found: dict = {}
        answered, error = False, None
        self.ui.infobox(title, tr("Scanning, please wait..."))
        for pause, ms in ANTENNA_DISCOVERY_SCANS:
            time.sleep(pause)
            try:
                merge_found(found, self.dev.device_discovery(ms))
                answered = True
            except AirOSError as e:
                error = error or e
        if not answered and error is not None:
            self.ui.msgbox(title, str(error))
        return found_list(found)

    def pc_discovery(self) -> None:
        """The devices on the network, as the connection screen finds them (Ubiquiti discovery
        from this computer, without logging into them), with this one marked. Connecting to
        another one leaves this device, as exiting does."""
        # (its addresses: connected by a name, it is the one at them)
        current = {self.dev.host} | {
            str((i.get("status") or {}).get("ipaddr") or "")
            for i in self.status.get("interfaces") or [] if isinstance(i, dict)} - {"", "0.0.0.0"}
        ip = discovery_screen(self.ui, self.discovery_target or self.discovery["target"],
                              port=self.discovery["port"], current=current,
                              ports=self.discovery_ports or ACTIVE_PORTS)
        if ip is not None:
            self._switch_to(ip)

    def _switch_to(self, ip: str) -> None:
        """Leave this device for ip (an address, or the URL of a web on another port), as
        exiting does (what is not applied is lost: asked first), if this computer reaches its
        web; otherwise this session goes on."""
        try:
            url = urllib.parse.urlsplit(ip if "://" in ip else "//" + ip)
            host, port = url.hostname, url.port
        except ValueError:
            host, port = None, None
        if not host or not reachable(host, ports=(port,) if port else (443, 80)):
            self.ui.msgbox(tr("Connect"), tr(
                "This computer does not reach {ip} (its web answers neither HTTPS nor HTTP): "
                "it may be behind a router. You are still connected to {current}.", ip=ip,
                current=self.dev.host))
            return
        n = len(self.changes())
        if n and not self.ui.ask(tr("Connect"), tr(
                "There are {count} unapplied change(s) that will be lost.\nConnect to {ip} "
                "anyway?", count=n, ip=ip), default_no=True):
            return
        raise SwitchDevice(ip)

    # -- site survey --

    def site_survey(self) -> None:
        title = tr("Site survey")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the site survey is in the device's web "
                                     "(Tools)."))
            return
        aps = self._survey_scan(title)
        if aps is None:
            return
        station = not self._wstate()["ap"]
        while True:
            status_bw = _int((self.status.get("wireless") or {}).get("chanbw")) or 0
            modes = v8_modes(self._board())
            ok = [survey_selectable(ap, status_bw, modes) for ap in aps]
            items = [(str(i), f"{'*' if station and sel else ' '} "
                              f"{ap['essid'] or tr('(hidden)'):<20.20} "
                              f"{ap['signal'] if ap['signal'] is not None else '?':>4}/"
                              f"{ap['noise'] if ap['noise'] is not None else '?'} dBm  "
                              f"{survey_frequency(ap):>6} GHz  {ap['mode']:<14.14} "
                              f"{ap['encryption'] or '-':<5.5} {ap['mac']}")
                     for i, (ap, sel) in enumerate(zip(aps, ok), 1)]
            items.append(("r", tr("Scan again")))
            text = (tr("{count} access point(s). * = this station can connect to it (airMAX, "
                       "visible SSID, a mode and a width it can use).", count=len(aps))
                    if station else tr("{count} access point(s).", count=len(aps)))
            choice = self.ui.menu(title, text, items, ok=tr("View"), cancel=tr("Back"))
            if choice is None:
                return
            if choice == "r":
                found = self._survey_scan(title)
                aps = aps if found is None else found
                continue
            ap = aps[int(choice) - 1]
            if self._survey_ap(ap, station and ok[int(choice) - 1]):
                return

    def _survey_scan(self, title: str) -> list[dict] | None:
        """Scan, as the web: the first read starts it; then, every half second, the results
        so far until it ends. The access points by signal, strongest first."""
        self.ui.infobox(title, tr("Scanning for access points (it takes about half a "
                                  "minute)…"))
        try:
            self.status = self.dev.status()
            t0, failed = time.monotonic(), 0
            scanning, aps = survey_aps(self.dev.site_survey())
            while scanning and time.monotonic() - t0 < SURVEY_MAX:
                time.sleep(0.5)
                try:
                    scanning, aps = survey_aps(self.dev.site_survey(again=True))
                    failed = 0
                except AirOSError:              # (a read lost while its radio scans: the
                    failed += 1                 # next one; not three in a row)
                    if failed == 3:
                        raise
        except AirOSError as e:
            self.ui.msgbox(title, str(e))
            return None
        if not aps:
            self.ui.msgbox(title, tr("No scan results."))
            return None
        return sorted(aps, key=lambda ap: -(ap["signal"] if ap["signal"] is not None else -999))

    def _survey_ap(self, ap: dict, selectable: bool) -> bool:
        """An access point of the list: its data and, on a station, using it. True if it
        was chosen."""
        lines = [f"SSID: {ap['essid'] or tr('(hidden)')}", f"MAC: {ap['mac']}",
                 tr("Device name: {name}", name=ap["hostname"] or "-"),
                 tr("Radio mode: {mode}", mode=ap["mode"] or "-"),
                 tr("Encryption: {encryption}", encryption=ap["encryption"] or "-"),
                 tr("Signal/noise: {signal} / {noise} dBm",
                    signal="?" if ap["signal"] is None else ap["signal"],
                    noise="?" if ap["noise"] is None else ap["noise"]),
                 tr("Frequency: {freq} GHz", freq=survey_frequency(ap))]
        if not selectable:
            if not self._wstate()["ap"]:
                lines += ["", tr("Selectable SSIDs must be visible, have compatible channel "
                                 "bandwidth and security settings, and must be compatible "
                                 "with airMAX AC technology.")]
            self.ui.msgbox(ap["essid"] or ap["mac"], "\n".join(lines))
            return False
        choice = self.ui.menu(ap["essid"] or ap["mac"], "\n".join(lines),
                              [("s", tr("Select: connect to this SSID")),
                               ("l", tr("Lock to AP: this SSID and only this access point "
                                        "(its MAC)"))], ok=tr("Choose"), cancel=tr("Back"))
        if choice not in ("s", "l"):
            return False
        return self._survey_use(ap, choice == "l")

    def _survey_use(self, ap: dict, lock: bool) -> bool:
        """What the Wireless page prepares when an access point is chosen, and the key (or
        the user) its security needs."""
        title = tr("Site survey")
        st = self._wstate()
        status_bw = _int((self.status.get("wireless") or {}).get("chanbw")) or 0
        changes = survey_changes(ap, st, status_bw, v8_modes(self._board()), lock)
        new = dict(st, **changes)
        if new["security"] == "wpa2" and new["wpa_auth"] == "WPA-PSK":
            key = self._ask_psk(st["psk"])
            if key is None:
                return False
            changes["psk"] = key
        elif new["security"] == "wpa2":
            creds = self._ask_eap(st)
            if creds is None:
                return False
            changes.update(creds)
        if not self._wchange(**changes):
            return False
        lines = [tr("SSID: {ssid}", ssid=changes["ssid"])]
        if changes["ap_mac"]:
            lines.append(tr("Locked to the access point {mac}.", mac=changes["ap_mac"]))
        if "ptp" in changes:
            lines.append(tr("Mode: {mode}.", mode=self._w_mode_text(self._wstate())))
        lines += ["", tr('Prepared, not sent yet: apply it with "Apply changes" (with test '
                         'mode).')]
        self.ui.msgbox(title, "\n".join(lines))
        return True
