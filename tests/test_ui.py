"""How the screens lay out their texts (ui.py), without drawing any: what UI hands dialog (its
texts, sizes and options) for a given window size, with dialog replaced by a function that keeps
what it got; and that the menus' fixed options fit in an 80-column window, in both languages.
How it all looks drawn, in small windows: e2e.py (group small)."""
import ast
import glob
import io
import json
import os
import sys
import types

from _common import check, fails, suite  # noqa: E402
from _paths import ROOT  # noqa: E402
T, S, XDG = suite("ui")
tr = T.tr
W = T.wrap_lines

print("=== wrap_lines: between words, its line breaks kept, the next lines under their text ===")
check("a line that fits, as it is; its line breaks kept",
      W("one two three", 40) == ["one two three"] and W("a\n\nb", 10) == ["a", "", "b"])
check("an item of a list: its next lines under its text",
      W("  · alpha beta gamma delta", 16) == ["  · alpha beta", "    gamma delta"],
      str(W("  · alpha beta gamma delta", 16)))
check("  numbered too", W("1. alpha beta gamma", 12) == ["1. alpha", "   beta", "   gamma"],
      str(W("1. alpha beta gamma", 12)))
check('a "label   value" row: under its value',
      W("Name     alpha beta gamma", 19) == ["Name     alpha beta", "         gamma"],
      str(W("Name     alpha beta gamma", 19)))
check("  a label too long for that: under its start",
      W("A-very-long-label   value one two three four", 20)
      == ["A-very-long-label", "value one two three", "four"],
      str(W("A-very-long-label   value one two three four", 20)))
check("a word longer than a line: split", W("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5])
sample = tr(T.NOBODY_HINT) + "\n\n" + tr(T.ACTIVE_HINT, active="a")
check("no line wider than asked", all(len(x) <= 37 for x in W(sample, 37))
      and " ".join(" ".join(W(sample, 37)).split()) == " ".join(sample.split()))

ui = T.UI()
ui.prog, ui.is_dialog = "dialog", True        # (what it hands dialog: none is run)
calls, answers = [], []


def fake_run(args, labels=None, extra=None):
    calls.append((list(args), list(extra or [])))
    return answers.pop(0) if answers else (0, "")


ui._run = fake_run


def window(cols, rows):
    """The window's size, as shutil.get_terminal_size (and so UI) reads it."""
    os.environ["COLUMNS"], os.environ["LINES"] = str(cols), str(rows)


def menus():
    """Each menu handed to dialog: (its text, height, width, list height, options, extra)."""
    out = []
    for args, extra in calls:
        if "--menu" in args:
            i = args.index("--menu")
            rest, step = args[i + 5:], 3 if "--item-help" in extra else 2
            out.append((args[i + 1], int(args[i + 2]), int(args[i + 3]), int(args[i + 4]),
                        [tuple(rest[j:j + step]) for j in range(0, len(rest), step)], extra))
    return out


print("=== the boxes in an 80×24 window ===")
window(80, 24)
check("the backtitle, when the box leaves it its two rows (dialog centers it over the window "
      "but its shadow): not over a taller one", ui._backtitle(19) == ["--backtitle", ui.backtitle]
      and ui._backtitle(20) == [])
long_item = "D" * 80
ui.menu("Title", "Short text.", [("r", tr("Search again")), ("d", long_item)])
text, h, w, list_h, items, extra = menus()[-1]
fit = w - 8 - 1
check("a menu: the whole window but its shadow (78 columns)", w == 78, str(w))
check("  an option wider than it: cut with … to its room exactly, and whole as its help (on the "
      "window's last line while it is highlighted)", items[1][1] == "D" * (fit - 1) + "…"
      and items[1][2] == long_item and items[0] == ("r", tr("Search again"), "")
      and "--item-help" in extra, str(items))
check("  its height, exact: its text, its list and its frame (6)", (h, list_h) == (1 + 2 + 6, 2),
      str((h, list_h)))
calls.clear()
ui.menu("Title", "Short text.", [("r", tr("Search again"))])
check("  when they all fit: as they are, without a help line",
      menus()[-1][4] == [("r", tr("Search again"))] and "--item-help" not in menus()[-1][5])

print("=== a menu whose text does not fit: 60×20 ===")
window(60, 20)
calls.clear()
answers[:] = [(0, "?"), (0, ""), (0, "r")]     # (?, then OK to the whole text, then r)
words = "\n".join(f"line {n}" for n in range(30))
choice = ui.menu("Title", words, [("r", "R"), ("d", "D"), ("a", "A")])
shown = menus()
text, h, w, list_h, items, extra = shown[0]
check("its text cut, with an option that shows it whole: the list keeps its rows, and the box "
      "fits in the window (but its last line)",
      text.split("\n")[-1] == tr("… (the rest: option {tag})", tag="?")
      and items[-1] == ("?", tr("Read the whole text above")) and list_h == 4
      and w >= len(tr("Read the whole text above")) + 1 + 8
      and h == len(text.split("\n")) + list_h + 6 and h <= 20 - 2 - 1, str((text, h, items)))
whole = [args[args.index("--msgbox") + 1] for args, _ in calls if "--msgbox" in args]
check("  that option: the whole text, then the menu again, and what is chosen there",
      choice == "r" and len(shown) == 2 and whole and "line 0" in whole[0]
      and "line 29" in whole[0], str((choice, len(shown), whole[:1])))

print("=== a message: in lines that fit its box ===")
calls.clear()
ui.msgbox("Title", sample + "\n\nModel            " + "value " * 15)
args, _ = calls[-1]
text, h, w = args[3], int(args[4]), int(args[5])
lines = text.split("\n")
check("each line within its box (its width less 4), the next ones of an item or a row under "
      "their text; its height, exact (its lines and 5)", w == 58
      and all(len(x) <= w - 4 for x in lines) and any(x.startswith("    ") for x in lines)
      and any(x.startswith(" " * 17 + "value") for x in lines)
      and h == min(len(lines) + 5, 20 - 2), text)

print("=== a live screen taller than the window ===")
window(80, 24)
body = "\n".join(f"row {n}" for n in range(60))
text, h, w = ui._live(body, "\n\n" + tr("Any key goes back."), "Title", 24)
check("its first rows and its newest ones, in lines that fit, within the window",
      "row 0\n" in text and "row 59" in text and "row 30\n" not in text and h <= 24 - 2
      and all(len(x) <= w - 4 for x in text.split("\n")), str((h, text[:80])))

print("=== with whiptail: cut with …, without a help line (it has none) ===")
ui.is_dialog = False
calls.clear()
ui.menu("Title", "Short text.", [("d", long_item)])
check("an option wider than the menu", menus()[-1][4] == [("d", "D" * (78 - 8 - 1 - 1) + "…")]
      and "--item-help" not in menus()[-1][5], str(menus()[-1][4]))
ui.is_dialog = True

print("=== the menus' fixed options fit in an 80-column window, in both languages ===")
es = json.load(open(os.path.join(ROOT, "airos_tui", "locales", "es.json"), encoding="utf-8"))
room = T.UI._max_w(80) - 8                   # (a menu's width less its frame and margins)
wide, seen = [], 0
for path in sorted(glob.glob(os.path.join(ROOT, "airos_tui", "**", "*.py"),
                               recursive=True)):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if (isinstance(node, ast.Tuple) and len(node.elts) == 2
                and isinstance(node.elts[0], ast.Constant)
                and isinstance(node.elts[0].value, str) and 1 <= len(node.elts[0].value) <= 4
                and isinstance(node.elts[1], ast.Call)
                and getattr(node.elts[1].func, "id", "") in ("tr", "N_") and node.elts[1].args
                and isinstance(node.elts[1].args[0], ast.Constant)):
            tag, msgid = node.elts[0].value, node.elts[1].args[0].value
            seen += 1
            for text in (msgid, es.get(msgid, msgid)):
                if len(text) > room - len(tag):
                    wide.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} [{tag}] {text}")
check(f"each one, {room} characters at most with its tag ({seen} options)", seen > 100
      and not wide, str(wide))

print("=== Linux's text console: what its fonts cannot draw, as they can ===")
asked = []


def tmux_says(out):
    """subprocess.run as tmux would answer (what it prints, or what it raises)."""
    def run(cmd, **kw):
        asked.append(cmd)
        if isinstance(out, BaseException):
            raise out
        return types.SimpleNamespace(stdout=out)
    return run


inside = {"TERM": "tmux-256color", "TMUX": "/tmp/tmux-1000/default,1322,0"}
check("the console: TERM=linux, or tmux on it (its client's terminal, as tmux says it); not "
      "another terminal, nor when tmux does not answer",
      T.linux_console({"TERM": "linux"}) and T.linux_console(inside, tmux_says("linux\n"))
      and not T.linux_console(inside, tmux_says("xterm-256color\n"))
      and not T.linux_console({"TERM": "xterm-256color"}, tmux_says("linux\n"))
      and not T.linux_console(inside, tmux_says(OSError("no tmux")))
      and asked == [["tmux", "display-message", "-p", "#{client_termname}"]] * 3, str(asked))
T._CONSOLE[:] = [True]
there = T._clean("⚠ ▁▂▃▄▅▆▇█ ·:░▒▓█ → …")
T._CONSOLE[:] = [False]
check("there, ⚠ as !, a sparkline in four steps (_░▒█) and ▓ as #; the rest as it is (its fonts "
      "have · → …); elsewhere, all as it is", there == "! __░░▒▒██ ·:░▒#█ → …"
      and T._clean("⚠ ▁▂▃▄▅▆▇█") == "⚠ ▁▂▃▄▅▆▇█", there)

print("=== a window too small: it says so, and waits until it is bigger ===")


class Terminal(io.StringIO):
    """stdin or stdout as a terminal; reading it, the user makes the window grow (or not) and
    presses Enter (or the input ends)."""

    def __init__(self, grow=None):
        super().__init__()
        self.grow, self.read = grow, 0

    def isatty(self):
        return True

    def readline(self, *args):
        self.read += 1
        if self.grow is None:
            return ""
        window(*self.grow)
        return "\n"


screens = []
for grow in ((80, 24), None):
    window(50, 14)
    keyboard, out = Terminal(grow), Terminal()
    real = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = keyboard, out
    try:
        T.UI._ensure_room()
    finally:
        sys.stdin, sys.stdout = real
    screens.append((keyboard.read, out.getvalue()))
said = tr("The window is too small ({cols}×{rows}) for this program's screens: make it at least "
          "{min_cols}×{min_rows} and press Enter.", cols=50, rows=14, min_cols=60, min_rows=18)
check("on a clear screen, its size and the least it needs, until it grows (Enter)",
      screens[0][0] == 1 and screens[0][1].startswith("\033[H\033[2J")
      and " ".join(said.split()) in " ".join(screens[0][1].split())
      and all(len(x) <= 49 for x in screens[0][1].removeprefix("\033[H\033[2J").split("\n")),
      repr(screens[0][1][:120]))
check("  without more input, it goes on as it is (it does not wait for ever)",
      screens[1][0] == 1, str(screens[1][0]))

window(50, 14)
keyboard, out = Terminal((100, 30)), Terminal()
real = sys.stdin, sys.stdout
sys.stdin, sys.stdout = keyboard, out
calls.clear()
try:
    ui.menu("Title", "Text.", [("1", "x" * 70)])
finally:
    sys.stdin, sys.stdout = real
check("  the box measured once it grew, not for the small window (its option whole)",
      keyboard.read == 1 and menus() and menus()[-1][4] == [("1", "x" * 70)], str(menus()[-1:]))

print("=== whiptail: a text taller than its box scrolls (--scrolltext) ===")
window(80, 24)
ui.is_dialog = False
calls.clear()
tall = "\n".join(f"line {n}" for n in range(60))
ui.msgbox("Title", tall)
ui.yesno("Title", tall)
ui.msgbox("Title", "Short.")
ui.is_dialog = True
ui.msgbox("Title", tall)
check("only with whiptail, and only when it does not fit (a message, a question): dialog "
      "scrolls it by itself", ["--scrolltext" in extra for _, extra in calls]
      == [True, True, False, False], str([extra for _, extra in calls]))

print("=== a menu with tags of different lengths: as wide as its longest tag and option ===")
window(140, 40)
calls.clear()
ui.menu("Keys", "Choose.", [("+", "v" * 60), ("radio.1.chanshift.status", "enabled")])
check("its options whole when the window has room (dialog lines them up after the longest "
      "tag)", menus()[-1][4][0] == ("+", "v" * 60), str(menus()[-1][4]))

for name in ("COLUMNS", "LINES"):
    os.environ.pop(name, None)
print("FAILURES:", fails if fails else "none")
raise SystemExit(1 if fails else 0)
