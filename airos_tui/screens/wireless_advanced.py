"""App: the advanced wireless settings."""
from __future__ import annotations

from ..core.i18n import tr
from ..core.config import _int
from ..device.wireless import ATPC_TARGET, ETSI_COUNTRIES, STA_PRIORITIES, V8_RATES


class WirelessAdvanced:
    """The advanced settings of the Wireless menu."""

    # -- advanced --

    def advanced_menu(self) -> None:
        last = "1"
        while True:
            st = self._wstate()
            orig = self._wstate(self.original)
            rows = self._advanced_rows(st)
            items = []
            for tag, label, text, _ in rows:
                cur = text(st)
                items.append((tag, f"{'' if text(orig) == cur else '* '}{label}: {cur}"))
            choice = self.ui.menu(tr("Advanced settings"), tr("Advanced wireless settings, as "
                                                              "in the web. * = changed, not "
                                                              "applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            action = next((a for tag, _, _, a in rows if tag == choice), None)
            if action:
                action(st)

    def _advanced_rows(self, st: dict) -> list[tuple]:
        """(tag, label, text, action) of the advanced settings the web shows for this mode."""
        on = self._w_onoff
        if st["flavor"] == "v6":
            rows = [("1", tr("Aggregation"), lambda s: tr("{frames} frames, {bytes} bytes",
                                                          frames=s["aggr_frames"],
                                                          bytes=s["aggr_bytes"])
                     if s["aggr_on"] else tr("disabled"), self._adv_aggregation),
                    ("2", tr("Multicast data"), lambda s: tr("allowed") if s["mcast_data"]
                     else tr("blocked"), self._toggle_action("mcast_data",
                                                             tr("Multicast data")))]
            if st["ap"]:
                rows += [("3", tr("Multicast enhancement"), lambda s: on(s["mcast_enh"]),
                          self._toggle_action("mcast_enh", tr("Multicast enhancement"))),
                         ("4", tr("Client isolation"), lambda s: on(s["isolation"]),
                          self._toggle_action("isolation", tr("Client isolation")))]
            rows += [("5", tr("Sensitivity threshold"),
                      lambda s: f"{s['rx_sens']} dBm" if s["rx_sens"] is not None
                      else tr("disabled"), self._adv_sensitivity),
                     ("6", tr("RTS threshold"), lambda s: str(s["rts"]) if s["rts"] is not None
                      else tr("disabled"), self._adv_rts)]
            return rows
        ap, ptmp = st["ap"], not st["ptp"]
        rows = [("1", "AMPDU", lambda s: tr("{frames} frames", frames=s["ampdu"])
                 if s["ampdu"] > 1 else tr("disabled"), self._adv_ampdu),
                ("2", "AMSDU", lambda s: on(s["amsdu"] == 3), self._adv_amsdu)]
        if not ap and ptmp:
            rows.append(("3", tr("airMAX station priority"),
                         lambda s: self._priority_text(s["sta_priority"]), self._adv_priority))
        if ap and ptmp:
            rows += [("4", tr("Client isolation"), lambda s: on(s["isolation"]),
                      self._toggle_action("isolation", tr("Client isolation"))),
                     ("5", tr("Multicast enhancement"), lambda s: on(s["mcast_enh"]),
                      self._adv_mcast_v8)]
        rows += [("6", tr("Maximum transmit rate"), lambda s: tr(dict(V8_RATES).get(
                    s["rate"], s["rate"])), self._adv_rate),
                 ("7", tr("Rate algorithm"), lambda s: tr("alternative") if s["rate_module"]
                  else tr("default"), self._adv_rate_module),
                 ("8", tr("Sensitivity threshold"), lambda s: f"{s['rx_sens']} dBm"
                  if s["rx_sens_on"] else tr("disabled"), self._adv_sensitivity)]
        if ap:
            rows.append(("9", tr("Automatic power control"),
                         lambda s: tr("enabled, target {target}", target=s["atpc_target"])
                         if s["atpc"] else tr("disabled"), self._adv_atpc))
        elif ptmp:
            rows.append(("9", tr("Automatic power control"), lambda s: on(s["atpc_sta"]),
                         self._toggle_action("atpc_sta", tr("Automatic power control"))))
        if ap and ptmp and st["tdd_duration"] < 1:
            rows.append(("t", tr("TDMA filter"), lambda s: str(s["cmsbias"]), self._adv_tdma))
        if ap and ptmp and st["country"] in ETSI_COUNTRIES:
            rows.append(("c", tr("Change channel on a station DFS event"),
                         lambda s: on(s["radar_change"]),
                         self._toggle_action("radar_change",
                                             tr("Change channel on a station DFS event"))))
        return rows

    def _toggle_action(self, key: str, label: str):
        def action(st: dict) -> None:
            question = (tr("Disable «{setting}»?", setting=label) if st[key]
                        else tr("Enable «{setting}»?", setting=label))
            if self.ui.ask(label, question):
                self._wchange(**{key: not st[key]})
        return action

    def _adv_aggregation(self, st: dict) -> None:
        title = tr("Aggregation")
        if st["aggr_on"] and not self._change_or_disable(title, tr("Aggregation is enabled."),
                                                         aggr_on=False):
            return
        frames = self._ask_int(title, tr("Frames (1-32):"), st["aggr_frames"], 1, 32)
        if frames is None:
            return
        size = self._ask_int(title, tr("Bytes (2304-65535):"), st["aggr_bytes"], 2304, 65535)
        if size is not None:
            self._wchange(aggr_on=True, aggr_frames=frames, aggr_bytes=size)

    def _adv_ampdu(self, st: dict) -> None:
        value = self._ask_int("AMPDU", tr("Frames aggregated per transmission (1-64; 1 = "
                                          "disabled):"), st["ampdu"], 1, 64)
        if value is not None and value != st["ampdu"]:
            self._wchange(ampdu=value)

    def _adv_amsdu(self, st: dict) -> None:
        on = st["amsdu"] == 3
        if self.ui.ask("AMSDU", tr("Disable AMSDU?") if on else tr("Enable AMSDU?")):
            self._wchange(amsdu=1 if on else 3)

    def _adv_priority(self, st: dict) -> None:
        choice = self.ui.menu(tr("airMAX station priority"),
                              tr("Share of the access point's time this station asks for:"),
                              [(str(v), tr(label)) for v, label in STA_PRIORITIES],
                              default=str(st["sta_priority"]))
        if choice is not None and int(choice) != st["sta_priority"]:
            self._wchange(sta_priority=int(choice))

    def _adv_mcast_v8(self, st: dict) -> None:
        on = bool(st["mcast_enh"])
        label = tr("Multicast enhancement")
        if self.ui.ask(label, tr("Disable «{setting}»?", setting=label) if on
                       else tr("Enable «{setting}»?", setting=label)):
            self._wchange(mcast_enh=0 if on else 1)

    def _adv_rate(self, st: dict) -> None:
        choice = self.ui.menu(tr("Maximum transmit rate"), tr("Highest modulation the radio "
                                                              "may use:"),
                              [(v, tr(label)) for v, label in V8_RATES], default=st["rate"])
        if choice is not None and choice != st["rate"]:
            self._wchange(rate=choice)

    def _adv_rate_module(self, st: dict) -> None:
        choice = self.ui.menu(tr("Rate algorithm"), tr("How the radio chooses the rate:"),
                              [("0", tr("default")), ("1", tr("alternative"))],
                              default=str(st["rate_module"]))
        if choice is not None and int(choice) != st["rate_module"]:
            self._wchange(rate_module=int(choice))

    def _adv_sensitivity(self, st: dict) -> None:
        title = tr("Sensitivity threshold")
        v8 = st["flavor"] == "v8"
        on = st["rx_sens_on"] if v8 else st["rx_sens"] is not None
        if on and not self._change_or_disable(title, tr("The sensitivity threshold is enabled."),
                                              **({"rx_sens_on": False} if v8
                                                 else {"rx_sens": None})):
            return
        lo, hi = (-90, -40) if v8 else (-96, -1)
        cur = st["rx_sens"] if on else (-80 if v8 else -90)
        value = self.ui.inputbox(title, tr("Ignore signals weaker than (dBm, from {lo} to "
                                           "{hi}):", lo=lo, hi=hi), str(cur))
        if value is None:
            return
        n = _int(value.strip())
        if n is None or not lo <= n <= hi:
            self.ui.msgbox(tr("Invalid value"), tr("It must be a whole number from {lo} to "
                                                   "{hi}.", lo=lo, hi=hi))
            return
        self._wchange(**({"rx_sens_on": True, "rx_sens": n} if v8 else {"rx_sens": n}))

    def _adv_rts(self, st: dict) -> None:
        title = tr("RTS threshold")
        if st["rts"] is not None and not self._change_or_disable(
                title, tr("The RTS threshold is enabled."), rts=None):
            return
        value = self._ask_int(title, tr("Bytes (0-2346):"),
                              2346 if st["rts"] is None else st["rts"], 0, 2346)
        if value is not None:
            self._wchange(rts=value)

    def _adv_atpc(self, st: dict) -> None:
        title = tr("Automatic power control")
        if st["atpc"] and not self._change_or_disable(
                title, tr("Automatic power control is enabled, with target {target}.",
                          target=st["atpc_target"]), atpc=False):
            return
        lo, default, hi = ATPC_TARGET
        target = self._ask_int(title, tr("Signal level the stations should keep, from {lo} "
                                         "(minimal) through {default} (reliable) to {hi} "
                                         "(maximum):", lo=lo, default=default, hi=hi),
                               st["atpc_target"] if st["atpc"] else default, lo, hi)
        if target is not None:
            self._wchange(atpc=True, atpc_target=target)

    def _adv_tdma(self, st: dict) -> None:
        value = self._ask_int(tr("TDMA filter"), tr("TDMA filter (0-9):"), st["cmsbias"], 0, 9)
        if value is not None and value != st["cmsbias"]:
            self._wchange(cmsbias=value)
