"""What the suites share: the program as a single namespace (T), the simulator (S), check(),
the simulated interface (FakeUI) and the paths.

At the start of each suite:

    from _common import FakeUI, check, fails, suite
    T, S, XDG = suite("network")    # its own XDG_DATA_HOME (the backups); all in Spanish
    tr = T.tr                       # what the program shows: tr("Invalid value"), in Spanish
"""
import ast
import glob
import importlib
import os
import pkgutil
import shutil
import sys
import time

from _paths import FIXTURES, HERE, ROOT, WORK  # noqa: F401  (the suites take them from here)

PACKAGE = os.path.join(ROOT, "airos_tui")
TUI = PACKAGE                                   # python3 TUI …: the program, as it is used
SIM = os.path.join(ROOT, "airos_simulator", "__main__.py")
SOURCES = sorted(glob.glob(os.path.join(PACKAGE, "**", "*.py"), recursive=True))
SIM_SOURCES = sorted(glob.glob(os.path.join(ROOT, "airos_simulator", "**", "*.py"),
                                recursive=True))

fails = []


def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f" [{detail}]" if detail and not cond
                                                       else ""))
    if not cond:
        fails.append(name)


def _modules(name="airos_tui"):
    """Every module of the package, its subpackages (core, device, screens) included."""
    pkg = importlib.import_module(name)
    return [pkg] + [importlib.import_module(info.name)
                    for info in pkgutil.walk_packages(pkg.__path__, prefix=name + ".")
                    if not info.name.endswith(".__main__")]


def _defined(path):
    """The names a module defines at its top level (assignments, functions, classes)."""
    out = set()
    for node in ast.parse(open(path, encoding="utf-8").read()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                out |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return out


class Package:
    """airos_tui as one namespace, like the single file it used to be. Reading a name finds it
    in the module that defines it; assigning one (what the suites patch: discover, UI,
    PPPOE_POLL…) changes it in every module that uses it, so the program sees the change
    wherever it looks the name up."""

    def __init__(self, name="airos_tui"):
        mods = _modules(name)
        owner = {}
        for mod in mods:
            for n in _defined(mod.__file__):
                assert n not in owner, f"{n} is defined in {owner[n].__name__} and {mod.__name__}"
                owner[n] = mod
        object.__setattr__(self, "_modules", mods)
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name):
        mod = self._owner.get(name)
        if mod is not None:
            return getattr(mod, name)
        for mod in self._modules:                # a module the program imports (socket…)
            if name in vars(mod):
                return vars(mod)[name]
        raise AttributeError(f"airos_tui has no {name!r}")

    def __setattr__(self, name, value):
        holders = [m for m in self._modules if name in vars(m)]
        if not holders:
            raise AttributeError(f"airos_tui has no {name!r}")
        for mod in holders:
            setattr(mod, name, value)

    def __dir__(self):
        return sorted(self._owner)


def suite(name, lang="es"):
    """Prepare a suite: its own XDG_DATA_HOME (where the program keeps its backups), the
    program and the simulator speaking lang. Returns (T, S, the XDG folder)."""
    xdg = os.path.join(WORK, f"xdg-{name}")
    shutil.rmtree(xdg, ignore_errors=True)
    os.environ["XDG_DATA_HOME"] = xdg          # before importing: BACKUP_DIR depends on it
    os.environ["AIROS_TUI_STRICT"] = "1"       # (an unexpected error fails the suite)
    # (whether the screen is Linux's console: not from the tmux these tests may run in)
    os.environ.pop("TMUX", None)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    T = Package()
    T.set_language(lang)
    os.environ["AIROS_LANG"] = lang            # main() picks the language again
    import airos_simulator as S
    return T, S, xdg


def settle(srv):
    """Wait until the simulator has finished restarting its services."""
    while time.time() < srv.dev.down_until + 0.2:
        time.sleep(0.1)


class FakeGauge:
    def __init__(self):
        self.texts = []        # what it showed, in order
        self.pcts = []         # and its percentages

    def update(self, pct, text):
        self.texts.append(text)
        self.pcts.append(pct)

    def close(self):
        pass


class FakeUI:
    """Answers from a script [(kind, value)]; the value can be a function."""

    def __init__(self, script=()):
        self.script = list(script)
        self.log = []
        self.menus = []        # (title, items, default) of each menu shown
        self.watched = []      # the texts of each live screen (watch)
        self.inputs = []       # the initial value of each text field (inputbox)
        self.infos = []        # (title, text) of each wait notice (infobox)
        self.gauges = []       # each progress bar (its texts, in .texts)

    def _next(self, kind, title, text):
        self.log.append((kind, title, text))
        assert self.script, f"no answer for {kind} {title!r}"
        k, v = self.script.pop(0)
        assert k == kind, f"expected {k}, got {kind} {title!r}"
        return v() if callable(v) else v

    def msgbox(self, title, text):
        self.log.append(("msgbox", title, text))

    def infobox(self, title, text):
        self.infos.append((title, text))

    def yesno(self, title, text, yes="Yes", no="No", default_no=False, timeout=None):
        return self._next("yesno", title, text)

    def ask(self, title, text, **kw):
        return self.yesno(title, text, **kw) == 0

    def inputbox(self, title, text, init=""):
        self.inputs.append(init)
        return self._next("input", title, text)

    def menu(self, title, text, items, default=None, ok="", cancel=""):
        self.menus.append((title, list(items), default))
        return self._next("menu", title, text)

    def passwordbox(self, title, text):
        return self._next("password", title, text)

    def textbox(self, title, text):
        self.log.append(("textbox", title, text))

    def gauge(self, title, text):
        g = FakeGauge()
        self.gauges.append(g)
        return g

    def watch(self, title, render, interval=1.0, until=None):
        """A live screen: the script says how many times it is drawn before the key (or until
        until() says it is over)."""
        times = self._next("watch", title, "")
        shown = []
        for _ in range(times):
            shown.append(render())
            if until is not None and until():
                break
        self.watched.append(shown)

    def titles(self, kind="msgbox"):
        return [t for k, t, _ in self.log if k == kind]

    def clear(self):
        pass
