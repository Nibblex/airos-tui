"""For e2e (the "keys" scenario): three live screens of the TUI (UI.watch) that take a while
to read (0.4 s, as a slow device) and refresh at once; each one, until a key. So the key
almost always arrives while it reads. With curses (LiveScreen) or, with "dialog", as when
curses cannot start: a dialog per refresh, which empties the terminal's input as it starts
(what used to lose the key)."""
import curses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from airos_tui.screens import ui as ui_module  # noqa: E402
from airos_tui.screens.ui import UI  # noqa: E402

if sys.argv[1:] == ["dialog"]:                   # (curses that cannot start)
    def broken(self):
        raise curses.error("no curses here")
    ui_module.LiveScreen.__enter__ = broken
ui = UI()
for n in (1, 2, 3):
    reads = [0]

    def render(n=n, reads=reads):
        time.sleep(0.4)
        reads[0] += 1
        return f"Round {n}: read {reads[0]}"
    ui.watch(f"Round {n}", render, interval=0.05)
print("THREE ROUNDS")
