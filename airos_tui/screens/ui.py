"""Interface: dialog, or whiptail if dialog is missing."""
from __future__ import annotations

import curses
import locale
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import textwrap
import threading
import time
import tty

from ..core.i18n import tr

# Room a title needs besides its own width so that dialog shows it whole: the corners of the
# box in the usual widgets; the textbox also covers the first columns of its top border.
TITLE_MARGIN = 6
TEXTBOX_TITLE_MARGIN = 20
# The smallest window its screens fit in: below it, it asks for a bigger one first (no box
# fits there: dialog would draw its frames over each other).
MIN_COLS, MIN_ROWS = 60, 18
# The widgets UI shows (--widget TEXT HEIGHT WIDTH…: see UI._run).
_WIDGETS = {"--msgbox", "--yesno", "--infobox", "--inputbox", "--passwordbox", "--menu",
            "--textbox"}
# Rows a menu's list keeps at least (all of them if it has fewer): when the text above it does
# not fit as well, the text is cut, with an option that shows it whole (MORE_TAG).
MENU_LIST_ROWS = 4
MORE_TAG = "?"
# A line that starts an item of a list ("· ", "- ", "1. "…), and a "label   value" row: their
# next lines go under their text, not under their start (see wrap_lines).
_BULLET = re.compile(r" *(?:[·•→⚠*-]|[0-9]{1,2}[.)]) +")
_COLUMNS = re.compile(r" *\S.*? {2,}(?=\S)")


CHANGED = "* "                  # a row whose value is not the device's (not applied yet)


def changed_mark(now, before) -> str:
    """The "* " of a row whose value is not the device's."""
    return CHANGED if now != before else ""


def field_marks(now: dict, before: dict):
    """mark(*fields) for two sets of settings: "* " if any of those fields differs."""
    return lambda *fields: CHANGED if any(now.get(f) != before.get(f) for f in fields) else ""


def _dialog_word(arg: str) -> str:
    """An argument as dialog reads it from --file: in double quotes, with \\ and " escaped
    (empty ones, line breaks and leading dashes stay as they are)."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _private_dir() -> str | None:
    """Where the files with what a screen shows go: the user's own runtime folder
    (XDG_RUNTIME_DIR: only theirs, and emptied when they log out), if it is theirs to write
    in (not another user's, which su without "-" keeps); or None, the system's temporary
    one."""
    run_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not run_dir:
        return None
    try:
        mine = os.stat(run_dir).st_uid == os.getuid()
    except OSError:
        return None
    return run_dir if mine and os.path.isdir(run_dir) and os.access(
        run_dir, os.W_OK | os.X_OK) else None


def _private_file(suffix: str) -> tuple[int, str]:
    """A new file only this user can read (mkstemp makes it 0600): in _private_dir() or, if
    it cannot be made there, in the system's temporary folder. OSError if neither."""
    folder = _private_dir()
    if folder is not None:
        try:
            return tempfile.mkstemp(prefix="airos-tui-", suffix=suffix, dir=folder)
        except OSError:
            pass
    return tempfile.mkstemp(prefix="airos-tui-", suffix=suffix)


def _remove_file(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _stop_dialog(proc: subprocess.Popen) -> None:
    """dialog, when this program has to end half way (a signal, Ctrl+C): with SIGTERM it gives
    the terminal back as it was; killed, the terminal would stay without echo."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# What Linux's text console (fbcon) cannot draw with its usual fonts (Terminus, of
# console-setup: 791 characters, without ⚠, the blocks of eighths or ▓), and what it is shown
# with there (see linux_console): a sparkline in four steps, _░▒█.
CONSOLE_GLYPHS = str.maketrans({"⚠": "!", "▁": "_", "▂": "_", "▃": "░", "▄": "░", "▅": "▒",
                                "▆": "▒", "▇": "█", "▓": "#"})
_CONSOLE: list[bool] = []                    # (linux_console's answer, once asked)


def linux_console(env=None, run=subprocess.run) -> bool:
    """Whether the screen is Linux's text console: directly (TERM=linux) or through tmux on it
    (its client's terminal). Its fonts draw a few hundred characters (see CONSOLE_GLYPHS)."""
    env = os.environ if env is None else env
    if env.get("TERM", "").startswith("linux"):
        return True
    if not env.get("TMUX"):
        return False
    try:
        out = run(["tmux", "display-message", "-p", "#{client_termname}"],
                  capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return str(out).strip().startswith("linux")


def _drawable(text: str) -> str:
    """text as the screen can draw it (see CONSOLE_GLYPHS)."""
    if not _CONSOLE:
        _CONSOLE.append(linux_console())
    return text.translate(CONSOLE_GLYPHS) if _CONSOLE[0] else text


def _clean(text) -> str:
    """An argument as dialog can take it: without NULs (a process argument cannot have
    them) and with what UTF-8 cannot carry (lone surrogates) as "?"; device data can bring
    both. And what the screen cannot draw, as it can (see CONSOLE_GLYPHS)."""
    return _drawable(str(text).replace("\0", "").encode("utf-8", "replace").decode("utf-8"))


def wrap_lines(text: str, width: int) -> list[str]:
    """text in lines of width characters at most, as a box shows it: split between words (a
    word longer than a line, wherever it must), its own line breaks kept, and the next lines of
    an item of a list ("· ", "- ", "1. "…) or of a "label   value" row under its text."""
    width, out = max(10, width), []
    for line in str(text).split("\n"):
        if len(line) <= width:
            out.append(line)
            continue
        lead = len(line) - len(line.lstrip(" "))
        m = _BULLET.match(line) or _COLUMNS.match(line)
        indent = m.end() if m else lead
        if indent > width // 2:                  # (a label too long: under its start)
            indent = lead if lead <= width // 2 else 0
        out += textwrap.wrap(line, width, subsequent_indent=" " * indent,
                             break_long_words=True, break_on_hyphens=False) or [""]
    return out


class LiveScreen:
    """A live screen (UI.watch) drawn with curses as dialog draws its infobox: its default
    colors (dialog --create-rc), its backtitle and the line under it, its box (lit on top and
    left, shaded on bottom and right) with its title and its shadow, centered as dialog centers
    it. Unlike a dialog per refresh, which clears the whole screen and draws it again (its
    flicker, and 4 KiB to the terminal each time), it is redrawn in place: only what changes
    goes out. And the keys are read as they come: a key pressed while it reads the device
    stays waiting for it."""

    COLORS = {"screen": (curses.COLOR_CYAN, curses.COLOR_BLUE, True),
              "shadow": (curses.COLOR_BLACK, curses.COLOR_BLACK, True),
              "dialog": (curses.COLOR_BLACK, curses.COLOR_WHITE, False),
              "title": (curses.COLOR_BLUE, curses.COLOR_WHITE, True),
              "border": (curses.COLOR_WHITE, curses.COLOR_WHITE, True)}
    MONO = {"screen": curses.A_NORMAL, "shadow": curses.A_NORMAL, "dialog": curses.A_REVERSE,
            "title": curses.A_REVERSE | curses.A_BOLD, "border": curses.A_REVERSE}

    def __init__(self, backtitle: str):
        self.backtitle = backtitle
        self.scr = None
        self.attr: dict = {}

    def __enter__(self) -> LiveScreen:
        try:
            locale.setlocale(locale.LC_CTYPE, "")   # (its characters: ▁▂▃, ─…)
        except locale.Error:
            pass
        self.scr = curses.initscr()             # (again: it only resumes the screen)
        try:
            curses.noecho()
            curses.cbreak()
            self.scr.keypad(True)
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.attr = dict(self.MONO)
            if curses.has_colors():
                curses.start_color()
                for i, (name, (fg, bg, bold)) in enumerate(self.COLORS.items(), 1):
                    curses.init_pair(i, fg, bg)
                    self.attr[name] = curses.color_pair(i) | (curses.A_BOLD if bold else 0)
            self._resized()
            self.scr.clear()                        # (dialog drew there meanwhile)
            curses.flushinp()                       # (what was typed before it is not for it)
        except BaseException:
            curses.endwin()
            raise
        return self

    def __exit__(self, *exc) -> None:
        # (empty: curses repaints this screen as the next live one starts, before its first
        # data, which may take a while: not this one's old data meanwhile)
        try:
            self.scr.erase()
        except curses.error:
            pass
        curses.endwin()

    def _resized(self) -> None:
        cols, rows = shutil.get_terminal_size((100, 32))
        if curses.is_term_resized(rows, cols):
            curses.resizeterm(rows, cols)

    def rows(self) -> int:
        return self.scr.getmaxyx()[0]

    def _put(self, y: int, x: int, text: str, attr: int) -> None:
        rows, cols = self.scr.getmaxyx()
        if 0 <= y < rows and 0 <= x < cols and text:
            try:
                self.scr.addstr(y, x, _drawable(text[:cols - x]), attr)
            except curses.error:                     # (the last cell of the screen)
                pass

    def draw(self, title: str, text: str, h: int, w: int) -> None:
        scr, a = self.scr, self.attr
        rows, cols = scr.getmaxyx()
        h, w = max(3, min(h, rows - 2)), max(10, min(w, cols - 2))
        top = 2 if h + 5 <= rows else 0              # (its backtitle: as UI._backtitle)
        scr.bkgdset(" ", a["screen"])
        scr.erase()
        if top:
            self._put(0, 1, self.backtitle, a["screen"])
            if cols > 2:
                scr.hline(1, 1, curses.ACS_HLINE, cols - 2, a["screen"])
        y, x = max(top, (rows - 1 - h) // 2), max(0, (cols - (w + 2)) // 2)
        for r in range(y + 1, y + h + 1):           # its shadow
            self._put(r, x + w, "  ", a["shadow"])
        self._put(y + h, x + 2, " " * w, a["shadow"])
        for r in range(y, y + h):
            self._put(r, x, " " * w, a["dialog"])
        try:
            scr.addch(y, x, curses.ACS_ULCORNER, a["border"])
            scr.hline(y, x + 1, curses.ACS_HLINE, w - 2, a["border"])
            scr.addch(y, x + w - 1, curses.ACS_URCORNER, a["dialog"])
            scr.vline(y + 1, x, curses.ACS_VLINE, h - 2, a["border"])
            scr.vline(y + 1, x + w - 1, curses.ACS_VLINE, h - 2, a["dialog"])
            scr.addch(y + h - 1, x, curses.ACS_LLCORNER, a["border"])
            scr.hline(y + h - 1, x + 1, curses.ACS_HLINE, w - 2, a["dialog"])
            scr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER, a["dialog"])
        except curses.error:
            pass
        label = title[:w - 2]                         # (as dialog: no spaces around it)
        self._put(y, x + max(1, (w - len(label) - 2) // 2), label, a["title"])
        for i, line in enumerate(wrap_lines(text, w - 4)[:h - 2]):
            self._put(y + 1 + i, x + 2, line, a["dialog"])
        scr.noutrefresh()
        curses.doupdate()

    def key(self, seconds: float) -> bool:
        """Whether a key comes within seconds (a resize is not one: it is redrawn sooner)."""
        self.scr.timeout(max(0, int(seconds * 1000)))
        ch = self.scr.getch()
        if ch == curses.KEY_RESIZE:
            self._resized()
            return False
        return ch != -1


class UI:
    LABELS = {  # dialog option, whiptail option
        "ok": ("--ok-label", "--ok-button"),
        "cancel": ("--cancel-label", "--cancel-button"),
        "yes": ("--yes-label", "--yes-button"),
        "no": ("--no-label", "--no-button"),
        "exit": ("--exit-label", "--ok-button"),
    }

    def __init__(self):
        self.prog = shutil.which("dialog") or shutil.which("whiptail")
        if not self.prog:
            raise SystemExit(tr("Missing 'dialog' (sudo apt install dialog) or 'whiptail'."))
        self.is_dialog = os.path.basename(self.prog) == "dialog"
        self.backtitle = tr("airos-tui · Ubiquiti airMAX antennas over HTTP")

    # -- base -----------------------------------------------------------------

    def _run(self, args: list[str], labels: dict | None = None,
             extra: list[str] | None = None) -> tuple[int, str]:
        self._ensure_room()
        h = int(args[4]) if len(args) > 4 and args[2] in _WIDGETS else 0
        cmd = [self.prog, *self._backtitle(h)]
        if self.is_dialog:
            cmd += ["--cr-wrap", "--no-collapse"]
        for name, text in (labels or {}).items():
            cmd += [self.LABELS[name][0 if self.is_dialog else 1], text]
        if not self.is_dialog and len(args) > 3:
            # (whiptail takes any argument that starts with "-" for an option: "-5 dBm" too)
            args = args[:3] + ["--"] + args[3:]
        cmd = [_clean(a) for a in cmd + (extra or []) + args]
        path = None
        try:
            argv, path = self._command(cmd)
            proc = subprocess.Popen(argv, stderr=subprocess.PIPE)
            try:
                _, err = proc.communicate()
            except BaseException:                # (this program ends: dialog too, cleanly)
                _stop_dialog(proc)
                raise
        finally:
            _remove_file(path)
        out = err.decode("utf-8", "replace")
        if proc.returncode not in (0, 1, 3) and out.lstrip().startswith(("Error", "Usage")):
            raise SystemExit(tr("{prog} failed: {output}", prog=os.path.basename(self.prog),
                                output=out.strip()[:300]))
        return proc.returncode, out

    def _command(self, cmd: list[str]) -> tuple[list[str], str | None]:
        """What to run: dialog reads its arguments from a file only this user can read
        (--file, removed afterwards), so that what they carry (a WPA key, a password…) is not
        on its command line, which any user of this computer can see while it is open;
        whiptail cannot, and they stay there. (The command, and the file to remove.)"""
        if not self.is_dialog:
            return cmd, None
        try:
            fd, path = _private_file(".args")
        except OSError:
            return cmd, None                     # (as before rather than no screen at all)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(" ".join(_dialog_word(a) for a in cmd[1:]) + "\n")
        except OSError:
            _remove_file(path)
            return cmd, None
        except BaseException:                    # (this program ends just now)
            _remove_file(path)
            raise
        return [cmd[0], "--file", path], path

    def _backtitle(self, h: int) -> list[str]:
        """The backtitle's arguments, when a box h rows tall leaves its two rows free (dialog
        centers it over the window but its shadow): none otherwise, as a taller one would
        cover it, and its line would show at the box's sides."""
        rows = shutil.get_terminal_size((100, 32)).lines
        return ["--backtitle", self.backtitle] if h + 5 <= rows else []

    @staticmethod
    def _ensure_room() -> None:
        """A window smaller than its screens need (MIN_COLS × MIN_ROWS): it says so, as plain
        text (no box fits there), and waits for Enter; again until it is big enough."""
        while sys.stdin.isatty() and sys.stdout.isatty():
            cols, rows = shutil.get_terminal_size((100, 32))
            if cols >= MIN_COLS and rows >= MIN_ROWS:
                return
            sys.stdout.write("\033[H\033[2J" + "\n".join(wrap_lines(tr(
                "The window is too small ({cols}×{rows}) for this program's screens: make it at "
                "least {min_cols}×{min_rows} and press Enter.", cols=cols, rows=rows,
                min_cols=MIN_COLS, min_rows=MIN_ROWS), cols - 1)) + "\n")
            sys.stdout.flush()
            if not sys.stdin.readline():             # (no more input: as it is)
                return

    @staticmethod
    def _max_w(cols: int) -> int:
        """The widest box: the whole window but its shadow, 100 columns at most."""
        return max(30, min(cols - 2, 100))

    @staticmethod
    def _max_h(rows: int, bottom: int = 0) -> int:
        """The tallest box: the whole window but its shadow (without the backtitle, then: see
        _backtitle); bottom: rows kept free under it (a menu's help line)."""
        return max(7, rows - 2 - bottom)

    def _layout(self, text: str, extra: int = 0, min_w: int = 50,
                title: str = "", live: bool = False) -> tuple[int, int, list[str]]:
        """A box for text: (its height, its width, text in lines that fit in it: see
        wrap_lines). extra: its rows besides its text and frame (an input line). A window too
        small is made bigger first (_ensure_room), but in a live screen (live)."""
        if not live:
            self._ensure_room()
        cols, rows = shutil.get_terminal_size((100, 32))
        max_w = self._max_w(cols)
        min_w = max(min_w, len(title) + TITLE_MARGIN)       # (the title, whole)
        longest = max((len(x) for x in text.split("\n")), default=0)
        w = max(min(min_w, max_w), min(longest + 6, max_w))
        lines = wrap_lines(text, w - 4)
        return max(7, min(len(lines) + extra + 5, self._max_h(rows))), w, lines

    def clear(self) -> None:
        if shutil.which("clear"):
            subprocess.run(["clear"], check=False)       # (if it cannot, nothing is lost)

    # -- widgets ----------------------------------------------------------------

    def _scroll(self, lines: list[str], h: int) -> list[str]:
        """A text taller than its box: dialog scrolls it by itself; whiptail, only with
        --scrolltext (otherwise what does not fit is not seen)."""
        return ["--scrolltext"] if not self.is_dialog and len(lines) + 5 > h else []

    def msgbox(self, title: str, text: str) -> None:
        h, w, lines = self._layout(text, title=title)
        self._run(["--title", title, "--msgbox", "\n".join(lines), str(h), str(w)],
                  {"ok": tr("OK")}, self._scroll(lines, h))

    def infobox(self, title: str, text: str) -> None:
        h, w, lines = self._layout(text, title=title)
        self._run(["--title", title, "--infobox", "\n".join(lines), str(h), str(w)])

    def yesno(self, title: str, text: str, yes: str | None = None, no: str | None = None,
              default_no: bool = False, timeout: int | None = None) -> int:
        """0 = yes, 1 = no, 255 = Esc or the time ran out."""
        h, w, lines = self._layout(text, title=title)
        extra = (["--defaultno"] if default_no else []) + self._scroll(lines, h)
        if timeout and self.is_dialog:
            extra += ["--timeout", str(max(5, int(timeout)))]
        rc, _ = self._run(["--title", title, "--yesno", "\n".join(lines), str(h), str(w)],
                          {"yes": yes or tr("Yes"), "no": no or tr("No")}, extra)
        return rc if rc in (0, 1) else 255

    def ask(self, title: str, text: str, **kw) -> bool:
        return self.yesno(title, text, **kw) == 0

    def inputbox(self, title: str, text: str, init: str = "") -> str | None:
        h, w, lines = self._layout(text, 3, title=title)
        rc, out = self._run(["--title", title, "--inputbox", "\n".join(lines), str(h), str(w),
                             init],
                            {"ok": tr("OK"), "cancel": tr("Cancel")})
        return out.rstrip("\n") if rc == 0 else None

    def passwordbox(self, title: str, text: str) -> str | None:
        h, w, lines = self._layout(text, 3, title=title)
        extra = ["--insecure"] if self.is_dialog else []
        rc, out = self._run(["--title", title, "--passwordbox", "\n".join(lines), str(h),
                             str(w)],
                            {"ok": tr("OK"), "cancel": tr("Cancel")}, extra)
        return out.rstrip("\n") if rc == 0 else None

    def menu(self, title: str, text: str, items: list[tuple[str, str]],
             default: str | None = None, ok: str | None = None,
             cancel: str | None = None) -> str | None:
        """The tag of the option chosen, or None. An option wider than the box is cut with
        "…", and shown whole on the window's last line while it is the highlighted one
        (dialog's item help); a text above the list taller than the window leaves the list
        its rows (MENU_LIST_ROWS): it is cut, with an option (MORE_TAG) that shows it whole."""
        labels = {"ok": ok or tr("Choose"), "cancel": cancel or tr("Back")}
        while True:
            self._ensure_room()
            cols, rows = shutil.get_terminal_size((100, 32))
            widest = self._widest(items)
            _, w, prompt = self._layout(text, min_w=min(widest, self._max_w(cols)),
                                        title=title)
            options = list(items)
            room = self._max_h(rows, 1 if self.is_dialog else 0) - 6   # (its text and list)
            cut = len(prompt) > room - min(len(options), MENU_LIST_ROWS)
            if cut:                              # (that option, whole too: maybe wider)
                options.append((MORE_TAG, tr("Read the whole text above")))
                widest = self._widest(options)
                _, w, prompt = self._layout(text, min_w=min(widest, self._max_w(cols)),
                                            title=title)
                keep = max(0, room - min(len(options), MENU_LIST_ROWS) - 1)
                prompt = prompt[:keep] + [tr("… (the rest: option {tag})", tag=MORE_TAG)]
            list_h = max(1, min(len(options), room - len(prompt)))
            fit = w - 8 - max((len(t) for t, _ in options), default=1)
            short = [d if len(d) <= fit else d[:max(1, fit - 1)].rstrip() + "…"
                     for _, d in options]
            helps = self.is_dialog and any(s != d for s, (_, d) in zip(short, options))
            args = ["--title", title, "--menu", "\n".join(prompt),
                    str(len(prompt) + list_h + 6), str(w), str(list_h)]
            for s, (tag, desc) in zip(short, options):
                args += [tag, s] + ([desc if s != desc else ""] if helps else [])
            extra = ((["--default-item", default] if default else [])
                     + (["--item-help"] if helps else []))
            rc, out = self._run(args, labels, extra)
            choice = out.strip() if rc == 0 else None
            if cut and choice == MORE_TAG:
                self.msgbox(title, text)
                continue
            return choice

    @staticmethod
    def _widest(options) -> int:
        """The width of a menu with its options whole: dialog lines them up after its longest
        tag."""
        return (max((len(t) for t, _ in options), default=0)
                + max((len(d) for _, d in options), default=0) + 12)

    def textbox(self, title: str, text: str) -> None:
        self._ensure_room()
        cols, rows = shutil.get_terminal_size((100, 32))
        width = min(cols - 2, max(60, max((len(x) for x in text.split("\n")), default=0) + 6,
                                  len(title) + TEXTBOX_TITLE_MARGIN))
        height = min(self._max_h(rows), max(10, text.count("\n") + 6))
        try:                                     # (what it shows: only the user can read it)
            fd, path = _private_file(".txt")
        except OSError:                          # (nowhere to write it: as a message)
            self.msgbox(title, text)
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:
                f.write(_clean(text) + "\n")
            extra = [] if self.is_dialog else ["--scrolltext"]
            self._run(["--title", title, "--textbox", path, str(height), str(width)],
                      {"exit": tr("Back")}, extra)
        finally:
            _remove_file(path)

    def gauge(self, title: str, text: str) -> Gauge:
        return Gauge(self, title, text)

    def _live(self, body: str, footer: str, title: str, rows: int) -> tuple[str, int, int]:
        """A live screen's text in lines that fit its box, and that box: (text, height,
        width). Taller than the window, its first lines and its newest ones (see _fit)."""
        _, w, _ = self._layout(body + footer, title=title, live=True)
        text = self._fit("\n".join(wrap_lines(body, w - 4)), max(5, self._max_h(rows) - 8))
        text += footer
        return text, max(7, min(len(wrap_lines(text, w - 4)) + 5, self._max_h(rows))), w

    @staticmethod
    def _fit(text: str, lines: int, keep: int = 3) -> str:
        """text in lines lines at most: its first keep ones (its title, its table's header)
        and the last ones (the newest, its results), with "…" between."""
        rows = text.split("\n")
        if len(rows) <= lines:
            return text
        tail = lines - keep - 1
        return "\n".join(rows[:keep] + ["…"] + rows[-tail:] if tail > 0 else rows[-lines:])

    def watch(self, title: str, render, interval=1.0, until=None) -> None:
        """A screen that renders itself again every interval seconds (a number, or what
        interval() says each time; render() gives its text) until a key is pressed or, with
        until, until it says it is over. Drawn with curses, as dialog would (LiveScreen); with
        dialog itself, one per refresh, where curses cannot start (its terminal unknown)."""
        footer = "\n\n" + tr("Any key goes back.")
        if not sys.stdin.isatty():              # no keyboard to stop it: show it once
            self.msgbox(title, render())
            return
        self._ensure_room()
        try:
            live = LiveScreen(self.backtitle).__enter__()
        except curses.error:
            self._watch_dialog(title, render, interval, until, footer)
            return
        try:
            h = w = 0
            while True:
                text, th, tw = self._live(render(), footer, title, live.rows())
                h, w = max(h, th), max(w, tw)    # (a box that shrinks would leave traces)
                live.draw(title, text, h, w)
                if until is not None and until():
                    return
                started, wait = time.monotonic(), interval() if callable(interval) else interval
                while True:
                    if live.key(wait - (time.monotonic() - started)):
                        return
                    if time.monotonic() - started >= wait:
                        break
                    live.draw(title, text, h, w)        # (resized: drawn again at once)
        finally:
            live.__exit__()

    def _watch_dialog(self, title: str, render, interval, until, footer: str) -> None:
        """watch with a dialog per refresh (where curses cannot start)."""
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        h = w = 0
        pressed, stop = threading.Event(), threading.Event()

        def take_key():
            # The key is taken as it comes: each refresh is a new dialog, and dialog empties
            # the input of the terminal it draws on as it starts (init_dialog: flushinp), so a
            # key left waiting meanwhile (while reading the device, or as dialog starts) was
            # lost, one in three with a busy screen.
            while not stop.is_set():
                if select.select([fd], [], [], 0.05)[0]:
                    os.read(fd, 64)              # the key, and the rest of its sequence
                    pressed.set()
                    return
        reader = threading.Thread(target=take_key, daemon=True)
        try:
            tty.setcbreak(fd)
            termios.tcflush(fd, termios.TCIFLUSH)
            reader.start()
            while True:
                rows = shutil.get_terminal_size((100, 32)).lines
                text, th, tw = self._live(render(), footer, title, rows)
                h, w = max(h, th), max(w, tw)    # (a box that shrinks would leave traces)
                if pressed.is_set():
                    return
                self._run(["--title", title, "--infobox", text, str(h), str(w)])
                if until is not None and until():
                    return
                if pressed.wait(interval() if callable(interval) else interval):
                    return
        finally:
            stop.set()
            if reader.is_alive():
                reader.join()
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except termios.error:                # (its terminal is gone: nothing to give back)
                pass


class Gauge:
    def __init__(self, ui: UI, title: str, text: str):
        ui._ensure_room()
        width = min(max(70, len(title) + TITLE_MARGIN),
                    ui._max_w(shutil.get_terminal_size((100, 32)).columns))
        cmd = [_clean(a) for a in (ui.prog, *ui._backtitle(9), "--title", title, "--gauge",
                                   text, "9", str(width), "0")]
        self._args = None
        try:
            argv, self._args = ui._command(cmd)
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, text=True)
        except BaseException:
            _remove_file(self._args)
            raise

    def update(self, pct: int, text: str) -> None:
        try:
            self.proc.stdin.write(f"XXX\n{int(pct)}\n{_clean(text)}\nXXX\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self) -> None:
        """Without more input it ends; its file goes away even if this is cut half way."""
        try:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait()
        except BaseException:
            _stop_dialog(self.proc)
            raise
        finally:
            _remove_file(self._args)
