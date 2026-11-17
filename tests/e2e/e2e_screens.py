"""The program's real screens without a device, for the e2e tests in small windows (e2e.py,
group small): python3 tests/e2e/e2e_screens.py empty|one|long.

    empty   the discovery screen when nobody answered (its long text above the list)
    one     the same with an antenna that waits for its first setup
    long    a menu with an option wider than any 80-column window"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _paths import ROOT

sys.path.insert(0, ROOT)
from airos_tui.core import i18n  # noqa: E402
from airos_tui.device import discovery as DD  # noqa: E402
from airos_tui.screens import discovery as D  # noqa: E402
from airos_tui.screens.ui import UI  # noqa: E402

# An option wider than the menu in an 80-column window, whole on its last line (79 columns)
LONG = "Option " + "wide " * 13 + "END"

i18n.set_language(os.environ.get("AIROS_LANG", "es"))
which, ui = sys.argv[1], UI()
if which in ("empty", "one"):
    web = {"url": "https://10.20.0.5", "scheme": "https", "port": 443, "airos": "8",
           "model": "NanoStation 5AC loco", "mac": "", "first_setup": True,
           "factory_password": False, "status": 0, "location": ""}
    found = [] if which == "empty" else [DD._active_entry("10.20.0.5", "", web, "")]
    D._scan = lambda ui, target, port=None: [dict(d) for d in found]   # (no network)
    D.discovery_screen(ui, D.BROADCAST)
elif which == "long":
    ui.menu("Long", "Short text.", [("1", "Short option"), ("2", LONG)])
