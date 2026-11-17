"""App: the Wireless menu and its settings (mode, SSID, country, width, frequency,
antenna, power, distance…)."""
from __future__ import annotations

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import Config, _freqs, _int, INT_RE, js_round, _merge, is_digits
from ..device.limits import board_antennas, Channel, _sample
from ..device.wireless import (CHANBW_AUTO, CHANBW_AUTO_AP, EU_LOCKED_CC, LIGHT_HALF,
                               NO_ACK_DISTANCE, plan_wireless, ssid_problem, STA_PRIORITIES,
                               TDD_DURATIONS, _v6_country, V6_FIXED_COUNTRIES, v6_max_distance,
                               _v6_page, V6_SECURITY, v8_antenna_choices, V8_LOCKED_COUNTRIES,
                               v8_modes, v8_station_widths, v8_tdd_ratios, V8_UNLOCKED_COUNTRIES,
                               v8_widths, width_number, wireless_errors, wireless_state,
                               wireless_supported)
from ..device.status import WIRELESS_MODES


class WirelessScreens:
    """The Wireless menu and its settings."""

    # -- wireless ----------------------------------------------------------------------

    def _board(self) -> dict:
        return self.limits.board if self.limits else {}

    def _wstate(self, cfg: Config | None = None) -> dict:
        return wireless_state(self.working if cfg is None else cfg, self.dev.flavor,
                              self._board())

    def _wplan(self, **changes) -> dict[str, str | None] | None:
        """The keys the web writes for a wireless change, or None if the web would not save
        it (only the errors the change brings count)."""
        board, cur = self._board(), self._wstate()
        new = dict(cur, **changes)
        before = set(wireless_errors(cur, board) + self._scan_errors(cur))
        errors = [e for e in wireless_errors(new, board) + self._scan_errors(new)
                  if e not in before]
        if errors:
            self.ui.msgbox(tr("Invalid value"), "\n\n".join(errors))
            return None
        return plan_wireless(self.working, self.dev.flavor, board,
                             self._power_follows(cur, changes))

    def _power_follows(self, cur: dict, changes: dict) -> dict:
        """changes, with the power lowered to the legal limit where the web lowers it by
        itself: when its page opens (a power the device already has above it; both webs)
        and, on airOS 6, each time the page works the limit out again (another antenna,
        width, channel…). airOS 8 does not lower it then: it does not save until you lower
        it (check_config). A power the user sets is not touched: above the limit, neither
        web saves it. Nor one with another country: its page opens again with that one, and
        _after_country() already gives the power its limit there. Nor, on airOS 6, a change of
        another of its pages (Advanced, airMAX, MAC ACL): they do not have the power."""
        if "power" in changes or "country" in changes or not self.limits:
            return changes
        if self.dev.flavor == "v6" and "link" not in {_v6_page(k) for k in changes}:
            return changes
        power, legal = cur["power"], None
        steps = [self.working]
        if self.dev.flavor == "v6":
            steps.append(_merge(self.working.copy(), plan_wireless(
                self.working, "v6", self._board(), changes)))
        for cfg in steps:
            pl = self.limits.power_limit(cfg)
            if pl and pl["legal"] and power > pl["max"]:
                power, legal = pl["max"], pl["legal"]
        if legal is None:
            return changes
        self.ui.msgbox(tr("Output power"), tr("The power goes down from {old} to {new} dBm, "
                                              "the legal limit: {legal}. The web lowers it "
                                              "too.", old=cur["power"], new=power,
                                              legal=legal))
        return dict(changes, power=power)

    def _change_or_disable(self, title: str, text: str, **off) -> bool:
        """A setting that is on: True to change it; "Disable" prepares off (what turns it
        off) and cancelling does nothing."""
        answer = self.ui.yesno(title, text, yes=tr("Change"), no=tr("Disable"))
        if answer == 1:
            self._wchange(**off)
        return answer == 0

    def _wchange(self, **changes) -> bool:
        """Prepare a wireless change (it goes through the device limits too)."""
        updates = self._wplan(**changes)
        return updates is not None and (not updates or self._accept(updates))

    def _band(self, st: dict) -> str:
        if st["flavor"] == "v6":
            return "A" if st["ieee"] == "11naht" else "G"
        return "G" if st["ieee"] == "11nght" else "A"

    def _chans(self, st: dict, bw: int | None = None,
               cc: int | None = None) -> list[Channel] | None:
        """The country's channels of this band and width (on airOS 6 with "obey", only the
        ones its web offers then); None if they cannot be read."""
        if not self.limits:
            return None
        bw = st["chanbw"] if bw is None else bw
        every = self.limits.channels(st["country"] if cc is None else cc, bw)
        if every is None:
            return None
        band = self._band(st)            # width 0 (20/40/80 automatic): any of them
        return [c for c in every if c.band == band and (bw == 0 or c.bw == bw)
                and (st["flavor"] == "v8" or c.obey_ok or not st["obey"])]

    def _available_widths(self, st: dict, cc: int | None = None) -> set[int] | None:
        """The channel widths with channels in the country, in this band."""
        if not self.limits:
            return None
        cc = st["country"] if cc is None else cc
        band = self._band(st)
        if st["flavor"] == "v8":
            every = self.limits.channels(cc, 0)
            return None if every is None else {c.bw for c in every if c.band == band}
        out = set()
        for bw in self.limits.supported_bw():
            chans = self.limits.channels(cc, bw)
            if chans is None:
                return None
            if any(c.band == band and c.bw == bw for c in chans):
                out.add(bw)
        return out

    def _width_choices(self, st: dict, cc: int | None = None) -> list[int]:
        """The widths the web offers, in its order."""
        board, avail = self._board(), self._available_widths(st, cc)
        if st["flavor"] == "v8":
            return (v8_widths if st["ap"] else v8_station_widths)(st, board, avail)
        supported = self.limits.supported_bw() if self.limits else [5, 10, 20, 40]
        return sorted((w for w in supported if avail is None or w in avail), reverse=True)

    def _width_label(self, st: dict, width: int) -> str:
        if width == CHANBW_AUTO_AP:
            return tr("Automatic")
        if width == CHANBW_AUTO and st["flavor"] == "v8":
            auto = [w for w in (20, 40, 80) if w in v8_widths(st, self._board(),
                                                              self._available_widths(st))]
            return "/".join(map(str, auto or [20, 40, 80])) + " MHz"
        if st["flavor"] == "v6" and not st["ap"] and width == 40:
            return tr("Automatic 20/40 MHz")
        return f"{width} MHz"

    def _current_width(self, st: dict) -> int:
        return CHANBW_AUTO_AP if st["flavor"] == "v8" and not st["ap"] and st["auto_chanbw"] \
            else st["chanbw"]

    def _scan_allowed(self, st: dict) -> list[int] | None:
        """The frequencies a frequency list may have (None if unknown)."""
        chans = self._chans(st)
        if chans is None:
            return None
        allowed = {c.control for c in chans}
        if st["flavor"] == "v6" and st["chanbw"] == 40 and not st["ap"]:
            allowed |= {c.control for c in self._chans(st, 20) or []}
        return sorted(allowed)

    def _scan_used(self, st: dict) -> bool:
        """Is the frequency list shown (and saved enabled)?"""
        return st["scan_on"] and (st["flavor"] == "v6" or st["ap"] or not st["auto_chanbw"])

    def _scan_errors(self, st: dict) -> list[str]:
        if not self._scan_used(st):
            return []
        if st["flavor"] == "v6" and st["ap"] and st["freq"] and st["freq"] not in st["scan"]:
            return [tr('The frequency of the access point must be one of the list, or '
                       '"Automatic".')]
        allowed = self._scan_allowed(st)
        if not st["scan"]:
            return [tr("The frequency list is empty.")]
        bad = [f for f in st["scan"] if allowed is not None and f not in allowed]
        if bad:
            return [tr("Some frequencies of the list are not allowed by country {cc} with "
                       "{bw} MHz: {sample}.", cc=st["country"], bw=width_number(st["chanbw"]),
                       sample=_sample(bad))]
        return []

    def _centers(self, st: dict) -> list[int] | None:
        """airOS 8: the center frequencies of the width (only those of the list, if on)."""
        chans = self._chans(st)
        if chans is None:
            return None
        if self._scan_used(st) and st["scan"]:
            chans = [c for c in chans if c.control in st["scan"]]
        return sorted({c.center for c in chans})

    def _freq_ok(self, st: dict) -> bool:
        """Is the access point's frequency among the ones the web offers (or unknown)?"""
        if st["flavor"] == "v8":
            centers = self._centers(st)
            if st["center"] in (0, -1) or centers is None:
                return True
            chans = self._chans(st) or []
            return st["center"] in centers and any(
                c.center == st["center"] and c.control == st["freq"] for c in chans)
        chans = self._chans(st)
        if st["freq"] and self._scan_used(st) and st["freq"] not in st["scan"]:
            return False            # validateFrequency(): one of the list, or automatic
        return not st["freq"] or chans is None or st["freq"] in {c.control for c in chans}

    # -- texts of the menu --

    def _w_mode_text(self, st: dict) -> str:
        if st["flavor"] == "v8":
            return tr(WIRELESS_MODES[("ap-" if st["ap"] else "sta-")
                                     + ("ptp" if st["ptp"] else "ptmp")])
        return tr(WIRELESS_MODES[st["wmode6"]])

    def _w_country_text(self, st: dict) -> str:
        names = dict(self._country_cache or [])
        name = names.get(st["country"])
        return f"{name} ({st['country']})" if name else str(st["country"])

    def _w_freq_text(self, st: dict) -> str:
        if st["flavor"] == "v8":
            if st["center"] == 0 or (st["center"] == -1 and not st["freq"]):
                return tr("Automatic")
            text = f"{st['freq']} MHz"
            if st["center"] not in (-1, st["freq"]):
                text += " " + tr("(center {center} MHz)", center=st["center"])
            return text
        if not st["freq"]:
            return tr("Automatic")
        ext = {"plus": tr("upper extension channel"),
               "minus": tr("lower extension channel")}.get(st["ext"]) \
            if st["chanbw"] == 40 else None
        return f"{st['freq']} MHz" + (f" ({ext})" if ext else "")

    def _w_scan_text(self, st: dict) -> str:
        if st["flavor"] == "v8" and not st["ap"] and st["auto_chanbw"]:
            return tr("not used with automatic width")
        if not st["scan_on"]:
            return tr("disabled")
        return (", ".join(map(str, st["scan"])) or tr("(empty)")) + " MHz"

    def _w_antenna_text(self, st: dict) -> str:
        name = ""
        if st["flavor"] == "v8":
            ant = next((a for a in v8_antenna_choices(self._board(), st["ieee"])
                        if a["id"] == st["antenna"]), None)
            name = tr("custom") if st["antenna"] == 0 else (ant["name"] if ant else "")
        gain = f"{st['gain']} dBi"
        parts = [name] if name and gain in name else [p for p in (name, gain) if p]
        if st["loss"]:
            parts.append(tr("cable loss {loss} dB", loss=st["loss"]))
        return ", ".join(parts)

    def _w_distance_text(self, st: dict) -> str:
        text = f"{st['distance'] / 1000:g} km"
        return text + " " + tr("(adjusted automatically)") if st["ack_auto"] else text

    def _w_security_text(self, st: dict) -> str:
        sec = st["security"]
        if st["flavor"] == "v8":
            if sec == "none":
                return tr("none")
            name = "WPA2"
        else:
            if not sec.startswith("wpa"):
                return tr("none") if sec == "none" else V6_SECURITY.get(sec, sec)
            name = V6_SECURITY.get(sec, sec)
        return name + " " + (tr("personal (PSK)") if st["wpa_auth"] == "WPA-PSK"
                             else tr("enterprise (EAP, RADIUS)"))

    def _w_acl_text(self, st: dict) -> str:
        if not st["acl_on"]:
            return tr("disabled ({count} MAC)", count=len(st["acl_rules"]))
        if st["acl_policy"] == "deny":
            return tr("deny the listed MACs ({count})", count=len(st["acl_rules"]))
        return tr("allow only the listed MACs ({count})", count=len(st["acl_rules"]))

    def _w_onoff(self, on) -> str:
        return tr("enabled") if on else tr("disabled")

    def _wireless_rows(self, st: dict) -> list[tuple]:
        """(tag, label, text function or None for a submenu) of the Wireless menu."""
        v8, ap = st["flavor"] == "v8", st["ap"]
        rows: list[tuple] = [("m", tr("Wireless mode"), self._w_mode_text)]
        if not v8:
            rows.append(("w", "WDS", lambda s: self._w_onoff(s["wds"]
                                                              or s["wmode6"] == "aprepeater")))
        rows.append(("s", "SSID", lambda s: s["ssid"]))
        if ap:
            rows.append(("h", tr("Hide SSID"), lambda s: tr("yes") if s["hide_ssid"]
                         else tr("no")))
        else:
            rows.append(("k", tr("Lock to AP MAC"), lambda s: s["ap_mac"] or tr("no")))
            if v8:
                rows.append(("z", tr("Site survey: choose the access point"), None))
        rows += [("c", tr("Country"), self._w_country_text),
                 ("b", tr("Channel width"),
                  lambda s: self._width_label(s, self._current_width(s)))]
        if ap:
            rows.append(("f", tr("Frequency"), self._w_freq_text))
        rows += [("l", tr("Frequency list") if ap else tr("Frequency scan list"),
                  self._w_scan_text),
                 ("a", tr("Antenna"), self._w_antenna_text),
                 ("e", tr("Calculate EIRP limit"), lambda s: self._w_onoff(s["obey"])),
                 ("p", tr("Output power"), lambda s: f"{s['power']} dBm"),
                 ("d", tr("Distance"), self._w_distance_text)]
        if v8 and ap and not st["ptp"]:
            rows.append(("t", tr("Frame duration"), lambda s: tr(dict(TDD_DURATIONS).get(
                s["tdd_duration"], str(s["tdd_duration"])))))
        rows.append(("x", tr("Security"), self._w_security_text))
        if ap:
            rows.append(("y", tr("MAC access control"), self._w_acl_text))
        if v8 and not ap:
            rows.append(("u", tr("Backup SSID"), lambda s: s["bk_ssid"] if s["bk_on"]
                         else tr("disabled")))
        if not v8:
            rows.append(("r", "airMAX", lambda s: self._w_onoff(s["polling"]) if s["ap"]
                         else tr("priority {priority}", priority=self._priority_text(
                             s["sta_priority"]))))
        rows.append(("v", tr("Advanced settings"), None))
        return rows

    # The values behind each row of the menu: the row is marked as changed if any of them
    # changes, even if its text does not show it (the WPA key, a RADIUS secret…).
    ROW_KEYS = {"x": ("security", "wpa_auth", "psk", "radius_ip", "radius_port",
                      "radius_secret", "acct_on", "acct_ip", "acct_port", "acct_secret",
                      "eap_method", "eap_anon", "eap_user", "eap_pass", "deauth"),
                "y": ("acl_on", "acl_policy", "acl_rules"),
                "u": ("bk_on", "bk_ssid", "bk_mac", "bk_security", "bk_wpa_auth", "bk_psk",
                      "bk_anon", "bk_user", "bk_pass"),
                "a": ("antenna", "gain", "loss"), "f": ("center", "freq", "ext"),
                "l": ("scan_on", "scan"), "d": ("distance", "ack_auto"),
                "t": ("tdd_duration", "tdd_ratio"), "r": ("polling", "sta_priority"),
                "v": ("ampdu", "amsdu", "sta_priority", "isolation", "mcast_enh", "rate",
                      "rate_module", "rx_sens_on", "rx_sens", "atpc", "atpc_target",
                      "atpc_sta", "cmsbias", "radar_change", "aggr_on", "aggr_frames",
                      "aggr_bytes", "mcast_data", "rts")}

    def _row_changed(self, tag: str, st: dict, orig: dict) -> bool:
        def rules(s):
            return [(r.get("mac"), r.get("comment") or "", r["on"]) for r in s["acl_rules"]]
        for key in self.ROW_KEYS.get(tag, ()):
            if key == "sta_priority" and tag == "v" and st["flavor"] == "v6":
                continue                    # (on airOS 6 it is airMAX's, not an advanced one)
            if key in st and (rules(st) != rules(orig) if key == "acl_rules"
                              else st[key] != orig.get(key)):
                return True
        return False

    def _priority_text(self, value) -> str:
        label = dict(STA_PRIORITIES).get(_int(value))
        return tr(label) if label else tr("(not set)")

    def wireless_menu(self) -> None:
        if not wireless_supported(self.dev.flavor, self._board()):
            self.ui.msgbox(tr("Wireless"), tr("This device is an airMAX Gen3 board: its web has "
                                              "another wireless page (60 GHz), which airos-tui "
                                              "does not handle. Use the key editor or the "
                                              "web."))
            return
        actions = {"m": self._w_mode, "w": self._w_wds, "s": self._w_ssid,
                   "h": self._w_hide, "k": self._w_apmac, "c": self._w_country,
                   "b": self._w_width, "f": self._w_freq, "l": self._w_scan,
                   "a": self._w_antenna, "e": self._w_obey, "p": self._w_power,
                   "d": self._w_distance, "t": self._w_tdd, "x": self.security_menu,
                   "y": self.acl_menu, "u": self.backup_menu, "r": self._w_airmax,
                   "v": self.advanced_menu, "z": self.site_survey}
        self._country_list()                 # the names of the countries, once a session
        last = "m"
        while True:
            st, orig = self._wstate(), self._wstate(self.original)
            items = []
            for tag, label, text in self._wireless_rows(st):
                changed = self._row_changed(tag, st, orig)
                if text is None:
                    items.append((tag, ("* " if changed else "") + label + "…"))
                    continue
                cur = text(st)
                mark = "* " if changed or text(orig) != cur else ""
                items.append((tag, f"{mark}{label}: {cur}"))
            choice = self.ui.menu(tr("Wireless"), tr("The settings of the web's Wireless page, "
                                                     "saved with the keys it writes. * = "
                                                     "changed, not applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            if choice in actions:
                actions[choice]()

    # -- the settings --

    def _w_mode(self) -> None:
        st, board = self._wstate(), self._board()
        title = tr("Wireless mode")
        if st["flavor"] == "v8":
            modes = v8_modes(board)
            cur = ("ap-" if st["ap"] else "sta-") + ("ptp" if st["ptp"] else "ptmp")
            choice = self.ui.menu(title, tr("PtP: a link between two devices. PtMP: an access "
                                            "point with several stations."),
                                  [(m, tr(WIRELESS_MODES[m])) for m in modes], default=cur)
            if choice is None or choice == cur:
                return
            ap, ptp = choice.startswith("ap-"), choice.endswith("-ptp")
            changes: dict = {"ap": ap, "ptp": ptp}
            if ap and ptp and st["chanbw"] == 80:          # what the web does on the change
                changes["chanbw"] = 40
            if ap and not ptp:
                changes.update(tdd_duration=1, tdd_flex=1)
            new = dict(st, **changes)
            widths = self._width_choices(new)
            shown = self._current_width(new)
            if widths and shown not in widths:              # the select falls on its first
                pick = next((w for w in widths if w != CHANBW_AUTO_AP), widths[0])
                changes.update(chanbw=pick, auto_chanbw=False)
            new = dict(st, **changes)
            if ap and not self._freq_ok(new):
                picked = self._pick_frequency(dict(new), tr("Choose the frequency of the "
                                                            "access point:"))
                if picked is None:
                    return
                changes.update(picked)
        else:
            if st["wmode6"] == "aprepeater":
                note = tr("The device is an AP-Repeater (WDS with peers): airos-tui does not "
                          "configure that mode, but you can change it to access point or "
                          "station.") + "\n\n"
            else:
                note = ""
            cur = st["wmode6"]
            choice = self.ui.menu(title, note + tr("Access point: the stations connect to it. "
                                                   "Station: it connects to an access point."),
                                  [("ap", tr("Access point")), ("sta", tr("Station"))],
                                  default=cur)
            if choice is None or choice == cur:
                return
            changes = {"wmode6": choice, "ap": choice != "sta"}
            if cur == "aprepeater" and st["security"] == "wep":
                changes["security"] = "none"
            if choice != "sta" and cur == "sta":       # the station page sends no frequency
                changes.update(freq=0, ext="")
        if not self._wchange(**changes):
            return
        new = self._wstate()
        lines = [tr("Mode: {mode}.", mode=self._w_mode_text(new))]
        if changes.get("security") == "none":        # (WEP is only for the repeater)
            lines.append(tr("Security: none, as its web leaves it (WEP is only for the "
                            "repeater): choose one in Security, or the network is open."))
        if new["ap"] and new["flavor"] == "v6" and "freq" in changes:
            lines.append(tr("Frequency: automatic, as the web leaves it; choose one in "
                            "Frequency if you want."))
        if not new["ap"]:
            lines.append(tr("Check the SSID (and the AP MAC, if you lock it) of the access "
                            "point this station must connect to, and its security."))
        if new["flavor"] == "v6" and new["ap"] and not new["polling"]:
            lines.append(tr("airMAX is disabled: enable it in airMAX if the stations use it."))
        self.ui.msgbox(title, "\n\n".join(lines))

    def _w_wds(self) -> None:
        st = self._wstate()
        if st["wmode6"] == "aprepeater":
            self.ui.msgbox("WDS", tr("An AP-Repeater always uses WDS."))
            return
        question = (tr("Disable WDS (transparent bridge)?\n\nWithout WDS a station bridges "
                       "with ARP-NAT: the devices behind it are not seen with their own MAC "
                       "on the other side.") if st["wds"] else
                    tr("Enable WDS (transparent bridge)?\n\nBoth ends of the link must use "
                       "it: the access point and its stations."))
        if self.ui.ask("WDS", question, default_no=st["wds"]):
            self._wchange(wds=not st["wds"])

    def _w_ssid(self) -> None:
        st = self._wstate()
        text = (tr("Name of the wireless network this access point announces:") if st["ap"]
                else tr("SSID of the access point this station connects to:"))
        value = self.ui.inputbox("SSID", text, st["ssid"])
        if value is not None and value != st["ssid"]:
            problem = ssid_problem(value, self.dev.flavor)
            if problem:
                self.ui.msgbox(tr("Invalid value"), problem)
                return
            self._wchange(ssid=value)

    def _w_hide(self) -> None:
        st = self._wstate()
        note = ("\n\n" + tr("The airOS 8 web does not have this option; airos-tui changes the "
                            "key the firmware applies, wireless.1.hide_ssid.")
                if st["flavor"] == "v8" else "")
        question = (tr("Show the SSID again in the beacons?") if st["hide_ssid"] else
                    tr("Hide the SSID?\n\nThe access point will not announce its name: the "
                       "stations must have it typed in.")) + note
        if self.ui.ask(tr("Hide SSID"), question):
            self._wchange(hide_ssid=not st["hide_ssid"])

    def _w_apmac(self) -> None:
        st = self._wstate()
        value = self.ui.inputbox(tr("Lock to AP MAC"), tr("MAC of the only access point this "
                                                          "station may connect to (like "
                                                          "00:11:22:33:44:55). Empty = any "
                                                          "with the SSID:"), st["ap_mac"] or "")
        if value is not None and value.strip() != (st["ap_mac"] or ""):
            self._wchange(ap_mac=value.strip())

    def _country_list(self) -> list[tuple[int, str]]:
        if self._country_cache is None:
            self.ui.infobox(tr("Country"), tr("Reading the countries the device offers…"))
            self._country_cache = self.dev.countries()
        return self._country_cache

    def _w_country(self) -> None:
        st, board = self._wstate(), self._board()
        title = tr("Country")
        cc = st["country"]
        if st["flavor"] == "v8":
            locked = (_int(board.get("radio.1.ccode_locked")) or 0) > 0 \
                and _int(board.get("radio.1.ccode")) != EU_LOCKED_CC
            can = cc in V8_UNLOCKED_COUNTRIES or (cc not in V8_LOCKED_COUNTRIES and not locked)
        else:
            fixed = (_int(board.get("radio.1.ccode_locked")) == 1
                     or _int(board.get("radio.1.ccode_fixed")) == 1)
            original = _v6_country(self.original, board)
            can = not fixed and original not in V6_FIXED_COUNTRIES
        if not can:
            self.ui.msgbox(title, tr("The web of this device does not let you change the "
                                     "country (it is locked to {country}).",
                                     country=self._w_country_text(st)))
            return
        if st["flavor"] == "v8" and not self._v8_country_ready():
            return
        countries = self._country_list()
        if countries:
            choice = self.ui.menu(title, tr("Country where the device is installed:"),
                                  [(str(code), name) for code, name in countries],
                                  default=str(cc))
            new_cc = _int(choice) if choice is not None else None
        else:
            value = self.ui.inputbox(title, tr("I could not read the list of countries. "
                                               "Numeric code of the country (ISO 3166, for "
                                               "example 484 for Mexico or 840 for the United "
                                               "States):"), str(cc))
            new_cc = _int(value) if value else None
        if new_cc is None or new_cc == cc:
            return
        name = dict(countries).get(new_cc, str(new_cc))
        if st["flavor"] == "v8":
            self._v8_change_country(st, new_cc, name)
            return
        text = tr("The country decides which frequencies, channel widths and powers the "
                  "device may use. Choose the one where it is installed: using another one can "
                  "be illegal and interfere with other services.\n\nChange to {country}?",
                  country=name)
        if new_cc in V6_FIXED_COUNTRIES:              # (as on airOS 8: it says so first)
            text += "\n\n" + tr("After this change the web will not let you change the "
                                 "country again.")
        if not self.ui.ask(title, text, default_no=True):
            return
        changes = self._after_country(st, new_cc)
        if changes is not None:
            self._wchange(**changes)

    def _country_issues(self, st: dict, cc: int) -> list[str]:
        """What the current settings will not be allowed to keep in another country."""
        new = dict(st, country=cc)
        out = []
        avail = self._available_widths(new)
        if avail is not None and not avail:            # (before the change, which is saved)
            return [tr("the device has no channels there in its band")]
        widths, width = self._width_choices(new), self._current_width(new)
        if widths and width not in widths:
            out.append(tr("the channel width ({width}) is not available there",
                          width=self._width_label(st, width)))
        elif new["ap"] and not self._freq_ok(new):
            out.append(tr("the frequency ({freq}) is not allowed there",
                          freq=self._w_freq_text(st)))
        allowed = self._scan_allowed(new)
        if self._scan_used(new) and allowed is not None and any(
                f not in allowed for f in new["scan"]):
            out.append(tr("some frequencies of the list are not allowed there"))
        cand = _merge(self.working.copy(), {"radio.countrycode": str(cc),
                                            "radio.1.countrycode": str(cc)})
        pl = self.limits.power_limit(cand) if self.limits else None
        if pl and pl["legal"] and st["power"] > pl["max"]:
            out.append(tr("the power ({power} dBm) is above its limit there ({max} dBm)",
                          power=st["power"], max=pl["max"]))
        return out

    def _v8_country_ready(self) -> bool:
        """airOS 8 changes the country at once and reads the configuration again: nothing
        may be pending, neither here nor in test mode on the device."""
        title = tr("Country")
        n = len(self.changes())
        if n:
            self.ui.msgbox(title, tr("There are {count} unapplied change(s). Like its web, "
                                     "airOS 8 changes the country right away and then reads "
                                     "the configuration again: apply or discard them first.",
                                     count=n))
            return False
        return not self._test_running(title)

    def _v8_change_country(self, st: dict, cc: int, name: str) -> None:
        """airOS 8 changes the country like its web: right away and saved, and then the page
        shows what the new country allows (that part is prepared as usual changes)."""
        title = tr("Country")
        issues = self._country_issues(st, cc)
        text = tr("The country decides which frequencies, channel widths and powers the device "
                  "may use. Choose the one where it is installed: using another one can be "
                  "illegal and interfere with other services.\n\nChange to {country}?",
                  country=name)
        text += "\n\n" + tr("As its web does, airOS 8 applies and SAVES the new country right "
                            "away, without test mode: the wireless link drops for a few seconds "
                            "while the radio restarts.")
        if issues:
            text += "\n\n" + tr("In {country}, {issues}: you will have to change it after "
                                "the restart, and until then the link may not work.",
                                country=name, issues="; ".join(issues))
        if cc in V8_LOCKED_COUNTRIES:
            text += "\n\n" + tr("After this change the web will not let you change the country "
                                "again.")
        if not self.ui.ask(title, text, default_no=True):
            return
        self.ui.infobox(title, tr("Changing the country to {country}…", country=name))
        try:
            self.dev.set_country(cc)
        except AirOSError as e:
            self.ui.msgbox(title, str(e))
            return
        if not self.wait_for_device(title, self._initial_wait({})):
            self.ui.msgbox(tr("No answer"), tr("The device does not answer yet. When it does, "
                                               "read its configuration again (main menu)."))
            return
        try:
            self.dev.login()                 # fresh login data (country and its channels)
        except AirOSError:
            pass
        self.load()
        now = self._wstate()
        lines = [tr("The country is now {country}, already saved on the device.",
                    country=self._w_country_text(now))]
        changes = self._after_country(now, cc) if now["country"] == cc else None
        changes = {k: v for k, v in (changes or {}).items() if now.get(k) != v}
        if changes and self._wchange(**changes):
            lines.append(tr("The settings the new country needs are prepared, not applied "
                            "yet: apply them (with test mode) from the main menu."))
        elif issues:
            lines.append(tr("Some settings are still not allowed in the new country: check "
                            "the width, the frequency and the power."))
        self.ui.msgbox(title, "\n\n".join(lines))

    def _after_country(self, st: dict, cc: int) -> dict | None:
        """What the page reloads with in the new country: a width and frequency it offers
        there, and only the allowed frequencies of the list."""
        changes: dict = {"country": cc}
        if st["flavor"] == "v6" and cc == 511:
            changes["obey"] = False
        elif st["flavor"] == "v6" and cc == 764:       # (force_obey_regulatory(): always on)
            changes["obey"] = True
        new = dict(st, **changes)
        avail = self._available_widths(new)
        if avail is not None and not avail:
            self.ui.msgbox(tr("Country"), tr("The device has no channels in that country in "
                                             "its band."))
            return None
        widths = self._width_choices(new)
        shown = self._current_width(new)
        if widths and shown not in widths:
            if new["flavor"] == "v6":                 # init_chanbw(): the first one up to 20
                pick = next((w for w in widths if w <= 20), widths[0])
            else:
                pick = next((w for w in widths if w != CHANBW_AUTO_AP), widths[0])
            changes.update(chanbw=pick, auto_chanbw=False)
        new = dict(st, **changes)
        allowed = self._scan_allowed(new)
        if self._scan_used(new) and allowed is not None:
            kept = [f for f in new["scan"] if f in allowed]
            if kept != new["scan"]:
                changes.update(scan=kept, scan_on=bool(kept))
        new = dict(st, **changes)
        if new["ap"] and not self._freq_ok(new):
            if new["flavor"] == "v6":
                changes.update(freq=0, ext="")
            else:
                picked = self._pick_frequency(new, tr("The frequency is not allowed in the new "
                                                      "country. Choose another one:"))
                if picked is None:
                    return None
                changes.update(picked)
        changes = self._ext_follows(st, changes)
        # the power, within what the new country allows: both pages lower it by themselves
        # when they open again with the new country (airOS 8 changes it at once)
        cand = _merge(self.working.copy(), plan_wireless(self.working, self.dev.flavor,
                                                         self._board(), changes))
        pl = self.limits.power_limit(cand) if self.limits else None
        if pl and pl["legal"] and st["power"] > pl["max"]:
            changes["power"] = pl["max"]
            self.ui.msgbox(tr("Country"), tr("The power goes down from {old} to {new} dBm, the "
                                             "limit in the new country: {legal}.",
                                             old=st["power"], new=pl["max"], legal=pl["legal"]))
        return changes

    def _w_width(self) -> None:
        st = self._wstate()
        widths = self._width_choices(st)
        if not widths:
            self.ui.msgbox(tr("Channel width"), tr("I could not read which widths the country "
                                                   "allows."))
            return
        cur = self._current_width(st)
        text = tr("Channel width (the web offers these in country {cc}):", cc=st["country"])
        if st["flavor"] == "v8" and not st["ap"]:
            text += "\n" + tr("Automatic: the station uses the access point's width.")
        choice = self.ui.menu(tr("Channel width"), text,
                              [(str(w), self._width_label(st, w)) for w in widths],
                              default=str(cur))
        width = _int(choice) if choice is not None else None
        if width is None or width == cur or width not in widths:
            return
        if st["flavor"] == "v8" and not st["ap"]:
            changes = ({"auto_chanbw": True} if width == CHANBW_AUTO_AP
                       else {"auto_chanbw": False, "chanbw": width})
        else:
            changes = {"chanbw": width}
        new = dict(st, **changes)
        if st["ap"] and not self._freq_ok(new):
            if st["flavor"] == "v6":                  # the page reloads with "automatic"
                changes.update(freq=0, ext="")
            else:
                picked = self._pick_frequency(new, tr("The frequency is not available with "
                                                      "{width} MHz. Choose another one:",
                                                      width=width))
                if picked is None:
                    return
                changes.update(picked)
        elif st["ap"] and st["flavor"] == "v6" and width == 40 and st["freq"]:
            changes["ext"] = self._ext_for(new, st["freq"]) or ""
        self._wchange(**changes)

    def _ext_follows(self, st: dict, changes: dict) -> dict:
        """changes, on an airOS 6 access point at 40 MHz, with the extension channel its
        frequency allows after them (its page offers only those: the current one if it can,
        if not the first); as they are where the channels are not known."""
        new = dict(st, **changes)
        if new["flavor"] != "v6" or not new["ap"] or new["chanbw"] != 40 or not new["freq"] \
                or self._chans(new) is None:
            return changes
        ext = self._ext_for(new, new["freq"]) or ""
        return changes if ext == new["ext"] else dict(changes, ext=ext)

    def _ext_for(self, st: dict, freq: int, ask: bool = False) -> str | None:
        """airOS 6 at 40 MHz: the extension channels the frequency allows (upper: plus,
        lower: minus); the current one if it can, if not the first."""
        chan = next((c for c in self._chans(st) or [] if c.control == freq), None)
        options = []
        if chan and chan.ext & 3 == 3:
            options.append(("plus", tr("Upper")))
        if chan and chan.ext & 5 == 5:
            options.append(("minus", tr("Lower")))
        if not options:
            return ""
        if ask and len(options) > 1:
            return self.ui.menu(tr("Extension channel"), tr("Extension channel of {freq} MHz "
                                                            "at 40 MHz:", freq=freq),
                                options, default=st["ext"] or options[0][0])
        return st["ext"] if st["ext"] in dict(options) else options[0][0]

    def _pick_frequency(self, st: dict, text: str) -> dict | None:
        """Choose the access point's frequency the way the web offers it: the changes, or
        None if cancelled."""
        title = tr("Frequency")
        if st["flavor"] == "v8":
            centers = self._centers(st)
            if centers is None:
                self.ui.msgbox(title, tr("I could not read the channels of country {cc}.",
                                         cc=st["country"]))
                return None
            chans = self._chans(st) or []
            items = [("0", tr("Automatic"))] if st["center"] == 0 else []
            for center in centers:
                dfs = any(c.dfs for c in chans if c.center == center)
                items.append((str(center), f"{center} MHz" + (" (DFS)" if dfs else "")))
            if not items:
                self.ui.msgbox(title, tr("There are no frequencies for this width in the "
                                         "country (or in the frequency list)."))
                return None
            choice = self.ui.menu(title, text + "\n" + tr("Center frequency:"), items,
                                  default=str(st["center"]))
            if choice is None:
                return None
            center = _int(choice)
            if center == 0:
                return {"center": 0}
            controls = sorted({c.control for c in chans if c.center == center})
            if not controls:
                return None
            control = st["freq"] if st["freq"] in controls else controls[0]
            if len(controls) > 1:
                choice = self.ui.menu(title, tr("Control frequency (the primary 20 MHz "
                                                "channel) within {center} MHz:",
                                                center=center),
                                      [(str(f), f"{f} MHz") for f in controls],
                                      default=str(control))
                if choice is None or _int(choice) not in controls:
                    return None
                control = int(choice)
            return {"center": center, "freq": control}
        chans = self._chans(st)
        if chans is None:
            self.ui.msgbox(title, tr("I could not read the channels of country {cc}.",
                                     cc=st["country"]))
            return None
        if self._scan_used(st):            # the web only accepts one of the list
            chans = [c for c in chans if c.control in st["scan"]]
        items = [("0", tr("Automatic"))] + [
            (str(c.control), f"{c.control} MHz" + (" (DFS)" if c.dfs else ""))
            for c in sorted(chans, key=lambda c: c.control)]
        choice = self.ui.menu(title, text, items, default=str(st["freq"]))
        if choice is None or choice not in dict(items):
            return None
        freq = int(choice)
        changes = {"freq": freq, "ext": ""}
        if freq and st["chanbw"] == 40:
            ext = self._ext_for(dict(st, freq=freq), freq, ask=True)
            if ext is None:
                return None
            changes["ext"] = ext
        return changes

    def _w_freq(self) -> None:
        st = self._wstate()
        text = tr("Frequency of the access point with {width} in country {cc}:",
                  width=self._width_label(st, st["chanbw"]), cc=st["country"])
        if st["flavor"] == "v8" and st["center"] != 0:
            text += "\n" + tr("(the web only offers \"Automatic\" when it is already in use)")
        picked = self._pick_frequency(st, text)
        if picked:
            self._wchange(**picked)

    def _w_scan(self) -> None:
        st = self._wstate()
        title = tr("Frequency list") if st["ap"] else tr("Frequency scan list")
        if st["flavor"] == "v8" and not st["ap"] and st["auto_chanbw"]:
            self.ui.msgbox(title, tr("With automatic channel width the station scans every "
                                     "frequency: the web does not use the list then. Choose a "
                                     "width first."))
            return
        allowed = self._scan_allowed(st)
        hint = (tr("Frequencies allowed with {width}: {sample}.", width=self._width_label(
            st, st["chanbw"]), sample=_sample(allowed, 12)) if allowed else "")
        text = (tr("Frequencies (MHz) the access point may choose when its frequency is "
                   "automatic, separated by commas.") if st["ap"] else
                tr("Frequencies (MHz) where the station looks for its access point, separated "
                   "by commas."))
        value = self.ui.inputbox(title, text + " " + tr("Empty = disable the list.")
                                 + (f"\n\n{hint}" if hint else ""),
                                 ",".join(map(str, st["scan"])) if st["scan_on"] else "")
        if value is None:
            return
        freqs = _freqs(value.replace(" ", ","))
        if value.strip() and (not freqs or any(not is_digits(x.strip())
                                               for x in value.replace(" ", "").split(","))):
            self.ui.msgbox(tr("Invalid value"), tr("Type frequencies in MHz separated by "
                                                   "commas, for example 5745,5765."))
            return
        changes = {"scan_on": bool(freqs), "scan": freqs if freqs else st["scan"]}
        if not freqs and not st["scan_on"]:
            return
        new = dict(st, **changes)
        if new["ap"] and not self._freq_ok(new):
            picked = self._pick_frequency(new, tr("The frequency of the access point is not "
                                                  "in the list. Choose another one:"))
            if picked is None:
                return
            changes.update(picked)
        self._wchange(**changes)

    def _w_antenna(self) -> None:
        st, board = self._wstate(), self._board()
        title = tr("Antenna")
        if st["flavor"] == "v8":
            choices = v8_antenna_choices(board, st["ieee"])
            changes: dict = {}
            # (a choice, if there is one: a single external antenna has its "Custom…" too)
            if len([a for a in choices if a["id"] != -1]) > 1:
                items = [(str(a["id"]), tr("Custom…") if a["id"] == 0 else
                          f"{a['name']} ({a['gain']} dBi)") for a in choices if a["id"] != -1]
                choice = self.ui.menu(title, tr("Antenna in use:"), items,
                                      default=str(st["antenna"]))
                if choice is None:
                    return
                ant = next(a for a in choices if str(a["id"]) == choice)
                changes["antenna"] = ant["id"]
                if ant["id"] != 0:                    # the web copies its gain
                    changes["gain"] = ant["gain"]
                    if ant["builtin"]:
                        changes["loss"] = 0
            new = dict(st, **changes)
            current = next((a for a in choices if a["id"] == new["antenna"]), None)
            if new["antenna"] == 0:
                gain = self._ask_int(title, tr("Antenna gain (dBi):"), new["gain"], 0, 40)
                if gain is None:
                    return
                changes["gain"] = gain
            builtin = bool(current and current["builtin"])
            if not builtin:
                top = changes.get("gain", new["gain"]) if new["obey"] or new["antenna"] == 0 \
                    else 40
                loss = self._ask_int(title, tr("Cable loss between the radio and the antenna "
                                               "(dB):"), new["loss"], 0, max(top, 0))
                if loss is None:
                    return
                changes["loss"] = loss
            if not changes:
                self.ui.msgbox(title, tr("This device has a single built-in antenna of "
                                         "{gain} dBi: there is nothing to choose.",
                                         gain=st["gain"]))
                return
            self._wchange(**changes)
            return
        ants = board_antennas(board)
        changes = {}
        if len(ants) > 1:
            choice = self.ui.menu(title, tr("Antenna in use:"),
                                  [(str(a["id"]), f"{a['name'] or a['id']} ({a['gain']} dBi)")
                                   for a in ants], default=str(st["antenna"]))
            if choice is None:
                return
            ant = next(a for a in ants if str(a["id"]) == choice)
            changes.update(antenna=ant["id"], gain=ant["gain"])
        elif not any(a["builtin"] for a in ants):    # (none, or a single external one)
            gain = self._ask_int(title, tr("Antenna gain (dBi):"), st["gain"], 0, 40)
            if gain is None:
                return
            changes["gain"] = gain
        new = dict(st, **changes)
        builtin = any(a["id"] == new["antenna"] and a["builtin"] for a in ants)
        if builtin:
            changes["loss"] = 0
        else:
            loss = self._ask_int(title, tr("Cable loss between the radio and the antenna "
                                           "(dB):"), new["loss"], 0, 40)
            if loss is None:
                return
            changes["loss"] = loss
        if all(new.get(k) == v for k, v in changes.items()) and builtin and len(ants) <= 1:
            self.ui.msgbox(title, tr("This device has a single built-in antenna of {gain} "
                                     "dBi: there is nothing to choose.", gain=st["gain"]))
            return
        self._wchange(**changes)

    def _w_obey(self) -> None:
        st = self._wstate()
        title = tr("Calculate EIRP limit")
        if st["flavor"] == "v8" and _int(self._board().get("radio.1.ccode")) == EU_LOCKED_CC:
            self.ui.msgbox(title, tr("On this device (locked to the European Union) the web "
                                     "always calculates it."))
            return
        if st["flavor"] == "v6" and st["country"] in (511, 902, 764):
            self.ui.msgbox(title, tr("With country {cc} the web does not let you change it.",
                                     cc=st["country"]))
            return
        question = (tr("Stop calculating the EIRP limit?\n\nThe web will then let you use the "
                       "whole power of the radio, whatever the country allows: you are "
                       "responsible for staying within the law.") if st["obey"] else
                    tr("Calculate the EIRP limit?\n\nThe power will be limited to what the "
                       "country allows with this antenna and cable."))
        if not self.ui.ask(title, question, default_no=st["obey"]):
            return
        changes: dict = {"obey": not st["obey"]}
        new = dict(st, **changes)
        if st["flavor"] == "v6" and new["ap"] and not self._freq_ok(new):
            controls = sorted(c.control for c in self._chans(new) or [])
            lower = [f for f in controls if f <= st["freq"]]   # init_frequencies(): the closest
            changes.update(freq=lower[-1] if lower else 0)   # one below, if not automatic
            changes = self._ext_follows(st, changes)
        self._wchange(**changes)

    def _w_power(self) -> None:
        st = self._wstate()
        value = self.ui.inputbox(tr("Output power"), tr("Transmit power in dBm:") + "\n\n"
                                 + self.power_hint(), str(st["power"]))
        if value is None or value.strip() == str(st["power"]):
            return
        if not INT_RE.fullmatch(value.strip()):
            self.ui.msgbox(tr("Invalid value"), tr("The power must be a whole number in dBm."))
            return
        self._wchange(power=int(value))

    def _w_distance(self) -> None:
        st = self._wstate()
        title = tr("Distance")
        v8 = st["flavor"] == "v8"
        if v8 and st["ap"] and not st["ptp"] and st["tdd_duration"] != 0:
            self.ui.msgbox(title, tr("With this frame duration the web hides the distance: "
                                     "the device adjusts the timing by itself."))
            return
        if not v8 and st["autoack_disabled"]:
            self.ui.msgbox(title, tr("With airMAX without ACK or airSync the web does not let "
                                     "you change the distance."))
            return
        auto = self.ui.yesno(title, tr("Adjust the distance (the ACK timeout) automatically?"),
                             yes=tr("Automatic"), no=tr("Fixed"), default_no=not st["ack_auto"])
        if auto not in (0, 1):
            return
        if auto == 0:
            if not st["ack_auto"]:
                self._wchange(ack_auto=True)
            return
        top = (NO_ACK_DISTANCE.get(st["chanbw"], 22500) if v8
               else v6_max_distance(st, self._board()))
        value = self.ui.inputbox(title, tr("Distance to the other end, in km (up to {km} km "
                                           "with {width} MHz):", km=f"{top / 1000:g}",
                                           width=width_number(st["chanbw"]))
                                 + ("" if v8 else "\n" + tr("airOS 6 counts it in steps of "
                                                            "150 m.")),
                                 f"{st['distance'] / 1000:g}")
        if value is None:
            return
        try:
            km = float(value.replace(",", ".").strip())
        except ValueError:
            km = -1
        if not 0 <= km * 1000 <= top:
            self.ui.msgbox(tr("Invalid value"), tr("The distance must be between 0 and {km} "
                                                   "km.", km=f"{top / 1000:g}"))
            return
        metres = js_round(km * 1000)
        if not v8:
            metres = min(js_round(metres / LIGHT_HALF) * LIGHT_HALF, top)
        self._wchange(ack_auto=False, distance=metres)

    def _w_tdd(self) -> None:
        st = self._wstate()
        title = tr("Frame duration")
        options = [(d, label) for d, label in TDD_DURATIONS
                   if not (d == 0 and st["ax_compat"]) and not (d == 5000 and st["chanbw"] == 10)]
        choice = self.ui.menu(title, tr("airMAX frame of the access point (TDD). Flexible: the "
                                        "split adapts to the traffic. Fixed: a fixed frame "
                                        "and split, to synchronize several access points."),
                              [(str(d), tr(label)) for d, label in options],
                              default=str(st["tdd_duration"]))
        if choice is None:
            return
        duration = int(choice)
        changes = {"tdd_duration": duration, "tdd_flex": 1 if duration == 1 else 0}
        ratios = v8_tdd_ratios(duration, st["chanbw"])
        if duration > 1 and ratios:
            ratio = st["tdd_ratio"] if st["tdd_ratio"] in ratios else ratios[0]
            choice = self.ui.menu(title, tr("Download/upload split of the frame:"),
                                  [(str(r), f"{r}/{100 - r}") for r in ratios],
                                  default=str(ratio))
            if choice is None:
                return
            changes["tdd_ratio"] = int(choice)
        if all(st[k] == v for k, v in changes.items()):
            return
        self._wchange(**changes)

    def _w_airmax(self) -> None:
        st = self._wstate()
        if st["ap"]:
            if st["airselect"]:
                self.ui.msgbox("airMAX", tr("airSelect is on: change airMAX from the web, "
                                            "which also rebuilds the airSelect frequencies."))
                return
            question = (tr("Disable airMAX?\n\nThe stations will use plain 802.11 (CSMA), "
                           "with less capacity when there are several.") if st["polling"] else
                        tr("Enable airMAX?\n\nThe access point will schedule the stations "
                           "(TDMA). The stations must be Ubiquiti airMAX ones."))
            if self.ui.ask("airMAX", question, default_no=st["polling"]):
                self._wchange(polling=not st["polling"])
            return
        cur = str(st["sta_priority"])
        choice = self.ui.menu(tr("airMAX priority"), tr("Share of the access point's time this "
                                                        "station asks for:"),
                              [(str(v), tr(label)) for v, label in STA_PRIORITIES],
                              default=cur or "2")
        if choice is not None and choice != cur:
            self._wchange(sta_priority=choice)
