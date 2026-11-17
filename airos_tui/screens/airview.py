"""App: airView, the spectrum analyzer of the airOS 8 web (Tools): its three views, live, read
as its dialog reads them."""
from __future__ import annotations

import shutil
import time

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..device.system import airview_offered
from ..device.airview import (airview_off_text, airview_running, airview_text, AirviewSession,
                              AIRVIEW_VIEWS)


class AirviewScreens:
    """Its airView (the first of its Tools, where the board has it)."""

    def airview_offered_here(self) -> bool:
        """Its Tools menu has it on airOS 8 where the board has it (hasFeatureAirView); on
        airOS 6 its web has its own (a Java application)."""
        return self.dev.flavor != "v8" or airview_offered(self._board())

    def airview_tool(self) -> None:
        """Its airView: its waveform, its waterfall and its ambient noise (each minute's
        peak or each hour's average), live; one reading for all of them, as its dialog."""
        title = "airView"
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6, airView is a Java application of its web "
                                     "(Tools menu); this program does not show it."))
            return
        try:
            self.status = self.dev.status()
        except AirOSError as e:
            self.ui.msgbox(tr("No answer"), str(e))
            return
        if not airview_running(self.status):     # (its web shows it greyed out)
            self.ui.msgbox(title, airview_off_text(self.status))
            return
        session, last = AirviewSession(self.dev, status=self.status), "1"
        items = [("1", tr("Waveform View: how often each power is heard, per frequency")),
                 ("2", tr("Waterfall View: its last 24 reads, the newest at the bottom")),
                 ("3", tr("Ambient Noise Level") + ": " + tr("Minute Peak")),
                 ("4", tr("Ambient Noise Level") + ": " + tr("Hour Average"))]
        while True:
            choice = self.ui.menu(title, tr("The spectrum the device hears, as its web's "
                                            "airView shows it: choose a view (each one, "
                                            "live; any key comes back here)."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            view = AIRVIEW_VIEWS[int(choice) - 1]

            def render(view=view) -> str:
                session.step()
                cols, rows = shutil.get_terminal_size((100, 32))
                return airview_text(session, view, max(30, min(cols - 4, 100)) - 6,
                                    rows - 10, time.time())
            self.ui.watch(title, render, interval=lambda: session.interval)
