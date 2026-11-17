"""End-to-end test: the real TUI (dialog) in tmux against the simulator.

    python3 tests/e2e/e2e.py                 all the scenarios, one after another
    python3 tests/e2e/e2e.py SCENARIO ...    those (for example: discovery firstsetup)
    python3 tests/e2e/e2e.py --list          the groups that can run at the same time

It runs in a tmux server of its own (tmux -L airos-tui-e2e, without the user's configuration;
or the one in AIROS_E2E_TMUX), to watch it: tmux -L airos-tui-e2e attach -t e2e8 (or whatever
session is running). run.py --e2e runs the groups at the same time, each with its own tmux
server (airos-tui-e2e-GROUP) and folder (AIROS_TEST_WORK/e2e/GROUP, with its screens).

The TUI runs in Spanish, as in the suites: what a scenario waits for comes from tr(), and
on_screen() finds a message even when dialog wraps it over several lines."""
from __future__ import annotations
import contextlib
import glob
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

# The scenarios, in the order of a whole run. Each one can run alone (from scratch) and at
# the same time as the others, but for these, which run one after another: their simulators
# answer Ubiquiti discovery at 127.0.0.1:10001, where the program looks.
ORDER = ["v8", "v6", "power", "network", "pppoe", "netmode", "discovery", "english",
         "services", "access", "system", "graphs", "advnet", "airview", "wireless", "tools",
         "maintenance", "firmware", "fwcheck", "ping", "date", "vlan", "router", "stations",
         "emergency", "firstsetup", "keys", "signals", "small"]
TOGETHER = [["discovery", "english", "services"]]


def groups() -> list[list[str]]:
    """What can run at the same time: each scenario alone, but those of TOGETHER."""
    joined = {name for group in TOGETHER for name in group}
    return [g for g in TOGETHER] + [[name] for name in ORDER if name not in joined]


if __name__ == "__main__" and sys.argv[1:] == ["--list"]:
    print("\n".join(" ".join(g) for g in groups()))
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _common import check, fails, SIM, suite, TUI, WORK  # noqa: E402
T, S, XDG = suite("e2e")
tr = T.tr                    # (Spanish, as the TUI of the scenarios: Session(lang="es"))

SHOTS = os.path.join(WORK, "screens")
os.makedirs(SHOTS, exist_ok=True)
# The TUI's XDG_RUNTIME_DIR, where it leaves what its screens show (what it hands to dialog,
# the text of a textbox): here, to see what stays.
RUN = os.path.join(WORK, "run")
os.makedirs(RUN, mode=0o700, exist_ok=True)


def screen_files():
    return glob.glob(os.path.join(RUN, "airos-tui-*"))


# dialog's frames as tmux captures them: horizontal runs (lqqqk, tqqqu, mqqqj; a corner only
# where no text touches it, so that a title drawn against the line keeps its letters), the
# vertical sides at both ends of a line, and the blank lines inside frames.
_FRAME_RUN = re.compile(r"(?:(?<!\S)[lmtwvn])?q{3,}(?:[kjuwvn](?!\S))?")
_FRAME_SIDES = re.compile(r"^(x\s+)+|(\s+x)+$")
_ONLY_SIDES = re.compile(r"(x\s*)*")


def flat(screen: str) -> str:
    """The text of a screen in one line: without dialog's frames (or box-drawing characters;
    a frame's title stays) and with every line break and run of spaces as one space."""
    out = []
    for raw in screen.splitlines():
        line = _FRAME_RUN.sub(" ", re.sub("[\u2500-\u257f]", " ", raw)).strip()
        if not _ONLY_SIDES.fullmatch(line):
            out.append(_FRAME_SIDES.sub("", line))
    return " ".join(" ".join(out).split())


def on_screen(text: str, screen: str) -> bool:
    """Whether the screen shows text: as it is, or wrapped by dialog over several lines."""
    return text in screen or " ".join(text.split()) in flat(screen)


def ready(sim, timeout=20):
    """Wait until the simulator just started takes connections (a fixed second was not always
    enough: under load it took longer, and the TUI found nobody there)."""
    args = list(sim.args)
    port = int(args[args.index("--port") + 1])
    host = args[args.index("--mgmt-ip") + 1] if "--mgmt-ip" in args else "127.0.0.1"
    end = time.time() + timeout
    while time.time() < end:
        if sim.poll() is not None:
            raise RuntimeError(f"the simulator ended before answering: {args[2:]}")
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"the simulator does not answer at {host}:{port}")


# Its own tmux server. On the user's one, what acts on "the active pane of the most recent
# session" lands on these tests' panes, which are the most recent ones while they run: a
# trackball helper's scroll put one in copy mode, which swallowed every key until an Escape,
# and pressing its ball opened a shell window in another. And nothing of these tests can reach
# the user's sessions.
TMUX_SOCKET = os.environ.get("AIROS_E2E_TMUX") or "airos-tui-e2e"
# The window the scenarios run in, COLUMNSxROWS (AIROS_E2E_SIZE=91x24, for example: a uConsole's
# console in tmux); the group small sets its own.
SIZE = tuple(int(n) for n in os.environ.get("AIROS_E2E_SIZE", "110x34").lower().split("x"))
TMUX_ENV = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}


def tmux(*args):
    try:
        r = subprocess.run(["tmux", "-L", TMUX_SOCKET, "-f", os.devnull, *args],
                           capture_output=True, text=True, timeout=30, env=TMUX_ENV)
    except subprocess.TimeoutExpired:
        fails.append(f"tmux did not answer in 30 s: {' '.join(args[:2])}")
        return ""
    return r.stdout


class Session:
    def __init__(self, name, port, host="127.0.0.1", tui_args=None, lang="es", program=TUI,
                 size=SIZE):
        self.name = name
        tui_args = tui_args if tui_args is not None else f"http://{host}:{port} -u ubnt --wait 3"
        # tmux does not pass on the environment: with run.py --coverage, the TUI is measured
        # too
        cov = "".join(f"{k}={shlex.quote(os.environ[k])} " for k in
                      ("PYTHONPATH", "AIROS_COV_DIR", "AIROS_COV_FILES") if os.environ.get(k))
        # HOME too: what the TUI offers to save in "~" (backups, support info) stays here even
        # if a scenario fails half way and Enter accepts it.
        home = os.path.join(WORK, "home")
        os.makedirs(home, exist_ok=True)
        cmd = (f"env HOME={shlex.quote(home)} "
               f"XDG_DATA_HOME={shlex.quote(os.path.join(WORK, 'xdg'))} {cov}AIROS_LANG={lang} "
               "AIROS_PASSWORD=ubnt ESCDELAY=50 TERM=xterm-256color "
               f"XDG_RUNTIME_DIR={shlex.quote(RUN)} "
               f"python3 {shlex.quote(program)} {tui_args}; "
               f"echo EXIT=$?; sleep 60")
        # Its session by its exact name ("=": not another one whose name starts the same) and
        # its keys and captures to its own pane, by its ID: never to another one, not even
        # to a window someone opens in this session (a shell there would run them).
        tmux("kill-session", "-t", f"={name}")
        self.pane = tmux("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", name,
                         "-x", str(size[0]), "-y", str(size[1]), cmd).strip()
        if not re.fullmatch(r"%[0-9]+", self.pane):
            raise RuntimeError(f"tmux did not create the session {name}: {self.pane!r}")
        self.n = 0
        self.started = False             # (whether its first screen settled: see wait)

    def keys(self, *k, literal=False):
        tmux("send-keys", "-t", self.pane, *(["-l"] if literal else []), *k)
        time.sleep(0.6)

    def screen(self):
        return tmux("capture-pane", "-p", "-t", self.pane)

    def wait(self, text, timeout=25, shot=None):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.screen()
            if on_screen(text, s):
                if not self.started:
                    # Keys wait until the TUI's first screen stops changing (a cheap guard).
                    s = self._settled(s)
                    self.started = True
                if shot:
                    self.shot(shot, s)
                return s
            time.sleep(0.4)
        s = self.screen()
        self.shot((shot or "timeout") + "-FAILED", s)
        fails.append(f"{self.name}: {text!r} did not show up")
        print(f"  FAIL expected {text!r}. Screen:\n{s}")
        return s

    def _settled(self, s, most=1.5):
        """The screen once it stops changing: the same twice in a row, or as it is after
        most seconds (a live screen keeps changing)."""
        end = time.time() + most
        while time.time() < end:
            time.sleep(0.25)
            again = self.screen()
            if again == s:
                break
            s = again
        return s

    def shot(self, name, s=None):
        self.n += 1
        with open(f"{SHOTS}/{self.name}-{self.n:02d}-{name}.txt", "w") as f:
            f.write(s if s is not None else self.screen())

    def clear_field(self, n=40):
        tmux("send-keys", "-t", self.pane, *(["BSpace"] * n))
        time.sleep(0.3)

    def close(self):
        tmux("kill-session", "-t", f"={self.name}")


def scenario_v8():
    print("=== e2e airOS 8: SSID from the quick settings, test mode and Keep ===")
    port = 18188
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "6", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2e8", port)
    try:
        s.wait(tr("Device and link status"), shot="menu")
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("Choose the setting to change (* = changed, not applied yet)."), shot="quick")
        s.keys("2")
        s.keys("Enter")
        s.wait(tr('New value for "{setting}":', setting="SSID"), shot="input-ssid")
        s.clear_field()
        s.keys("South-Tower", literal=True)
        s.keys("Enter")
        s.wait(tr("Change prepared (not sent to the device yet)."), shot="prepared")
        s.keys("Enter")
        s.wait("* SSID: South-Tower", shot="quick-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=3), shot="menu-pending")
        s.keys("5")
        s.keys("Enter")
        s.wait(tr("{count} pending change(s) (not sent yet):", count=3), shot="diff")
        s.keys("Enter")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"),
               shot="apply-menu")
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"), shot="confirm-test")
        s.keys("Enter")
        s.wait(tr("Waiting for the device to answer at {host}… ({seconds} s)").partition("{")[0],
               timeout=15, shot="waiting")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.keys("Enter")   # Keep
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("── Ethernet ──"), shot="status")
        s.keys("G")       # (it scrolls: to its end)
        s.wait(tr("── The other end: {name} ──", name="Client-North-01"), shot="status-end")
        s.keys("Enter")
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10, shot="exit")
    finally:
        s.close()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    cfg = T.Config(dev.get_config())
    check("v8 e2e: SSID saved", cfg.get("wireless.1.ssid") == "South-Tower",
          cfg.get("wireless.1.ssid"))
    check("v8 e2e: not in test mode", not dev.test_mode_info()["active"])
    sim.terminate()


def scenario_v6():
    print("=== e2e airOS 6: key editor, test mode and Revert ===")
    port = 18186
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "6", "--port", str(port),
                            "--restart", "2", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2e6", port)
    try:
        s.wait(tr("Device and link status"), shot="menu")
        s.keys("4")
        s.keys("Enter")
        s.wait(tr("Search keys"), shot="search")
        s.keys("txpower", literal=True)
        s.keys("Enter")
        s.wait("radio.1.txpower", shot="list")
        s.keys("Down")
        s.keys("Enter")
        s.wait(tr("Change the value"), shot="options")
        s.keys("Enter")
        s.wait(tr("New value for\n{key}", key="radio.1.txpower"), shot="input")
        s.clear_field()
        s.keys("10", literal=True)
        s.keys("Enter")
        s.wait("* 10", shot="list-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=1))
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"))
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=45, shot="decide")
        s.keys("Tab")
        s.keys("Enter")     # Revert
        s.wait(tr("The device went back to the previous configuration."), timeout=40,
               shot="reverted")
        s.keys("Enter")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=1),
               shot="menu-after-revert")
        s.keys("0")
        s.keys("Enter")
        s.wait(tr("There are {count} unapplied change(s) that will be lost.\nExit anyway?",
                  count=1), shot="quit")
        s.keys("Tab")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10, shot="exit")
    finally:
        s.close()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    cfg = T.Config(dev.get_config())
    check("v6 e2e: power reverted", cfg.get("radio.1.txpower") == "20",
          cfg.get("radio.1.txpower"))
    check("v6 e2e: not in test mode", not dev.test_mode_info()["active"])
    sim.terminate()


def scenario_power():
    print("=== e2e airOS 8: power limits on screen ===")
    port = 18190
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2epw", port)
    try:
        s.wait(tr("Device and link status"))
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("Choose the setting to change (* = changed, not applied yet)."))
        s.keys("4")
        s.keys("Enter")
        s.wait(tr("Legal limit (calculated as the web does): {legal}.").partition("{")[0],
               shot="power-hint")
        s.clear_field()
        s.keys("30", literal=True)
        s.keys("Enter")
        # (from after {max}: the range before it is the simulated model's)
        s.wait(tr("This device supports {min} to {max} dBm; {power} dBm is out of range.")
               .partition("{max}")[2].format(power=30), shot="hw-rejected")
        s.keys("Enter")
        s.wait(tr("Choose the setting to change (* = changed, not applied yet)."))
        s.keys("4")
        s.keys("Enter")
        s.wait(tr("Legal limit (calculated as the web does): {legal}.").partition("{")[0])
        s.clear_field()
        s.keys("22", literal=True)
        s.keys("Enter")
        s.wait(tr("{power} dBm exceeds the legal limit the web calculates: {legal}. The web "
                  "does not allow it: the power must be between {min} and {max} dBm.")
               .partition("{legal}")[0].format(power=22), shot="legal-rejected")
        s.keys("Enter")   # as the web does
        s.wait(f"{tr('Transmit power (dBm)')}: 20", shot="unchanged")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=0))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()


def scenario_network():
    print("=== e2e airOS 8: change the management IP and follow it ===")
    port = 18192
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--mgmt-ip", "127.0.0.20"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2enet", port, host="127.0.0.20")
    try:
        s.wait(tr("Device and link status"))
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("Network mode: {mode}", mode=tr("Bridge")), shot="network")
        s.keys("1")
        s.keys("Enter")
        s.wait("Interfaces", shot="summary")
        s.keys("Enter")
        s.wait(tr("Network mode: {mode}", mode=tr("Bridge")))
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("Now: {now}\nHow does it get its IP?").partition("\n")[2], shot="ip-mode")
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("IP address:"))
        s.clear_field()
        s.keys("127.0.0.21", literal=True)
        s.keys("Enter")
        s.wait(tr("Netmask (255.255.255.0 or the prefix, for example 24):"))
        s.clear_field()
        s.keys("24", literal=True)
        s.keys("Enter")
        s.wait(tr("Gateway (empty = no gateway):"))
        s.clear_field()
        s.keys("127.0.0.1", literal=True)
        s.keys("Enter")
        s.wait(tr("Primary DNS (empty = none):"))
        s.keys("Enter")
        s.wait(tr("Secondary DNS (empty = none):"))
        s.keys("Enter")
        s.wait(tr("Change prepared (not sent to the device yet)."), shot="prepared")
        s.keys("Enter")
        s.wait("* " + tr("Management IP"), shot="network-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
               .partition("\n")[2].partition("{")[0])
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait("127.0.0.20 → 127.0.0.21", shot="confirm")
        s.keys("Enter")
        s.wait(tr("The antenna now answers at {new} (before {old}).", new="127.0.0.21",
                  old="127.0.0.20"), timeout=45, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20)
        s.keys("Enter")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.21:{port}", user="ubnt", count=0),
               shot="menu-new-ip")
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()


def scenario_pppoe():
    print("=== e2e airOS 8 router: WAN over PPPoE, test, status and reconnection ===")
    port = 18194
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--mode", "router", "--mgmt-ip", "127.0.0.30"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eppp", port, host="127.0.0.30")
    try:
        s.wait(tr("Device and link status"))
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("Network mode: {mode}", mode=tr("Router")), shot="network")
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("PPPoE: user and password from your provider"), shot="modes")
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("PPPoE user (your provider gives it to you):"))
        s.clear_field()
        s.keys("customer", literal=True)
        s.keys("Enter")
        s.wait(tr("PPPoE password:"), shot="password")
        s.keys("secret", literal=True)
        check("e2e PPPoE: the password does not show while it is typed",
              "secret" not in s.screen())
        s.keys("Enter")
        s.wait(tr("PPPoE service name (leave it empty unless your provider gives you one):"))
        s.keys("Enter")
        s.wait(tr("PPPoE MTU (64–{max}; the usual is 1492):", max=1518))
        s.keys("Enter")
        s.wait(tr("PPPoE MRU (64–{max}; the usual is 1492):", max=1518))
        s.keys("Enter")
        s.wait(tr("Require MPPE encryption?\n\nEnable it only if your provider asks for it: if "
                  "its server does not support it, it will not connect.").partition("\n")[0],
               shot="mppe")
        s.keys("Enter")
        s.wait(tr("WAN fallback IP (the one it will have while PPPoE does not connect):"))
        s.keys("Enter")
        s.wait(tr("Netmask (255.255.255.0 or the prefix, for example 24):"))
        s.keys("Enter")
        s.wait(tr("Change prepared (not sent to the device yet)."), shot="prepared")
        s.keys("Enter")
        s.wait("* WAN (eth0): " + tr("PPPoE (user {name})", name="customer"),
               shot="network-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
               .partition("\n")[2].partition("{")[0])
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"), shot="confirm")
        s.keys("Enter")
        s.wait(tr("PPPoE: connected, IP {ip}.", ip="100.64.10.20"), timeout=60, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20)
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("PPPoE connection status (on the device)"), shot="network-pppoe")
        s.keys("p")
        s.keys("Enter")
        s.wait(tr("Connected for"), shot="status")
        s.keys("Enter")
        s.wait(tr("Reconnect PPPoE"))
        s.keys("c")
        s.keys("Enter")
        s.wait(tr("Cut the PPPoE session and connect again?\n\nWhat goes out to the Internet "
                  "through this antenna will be without connection for a few seconds.")
               .partition("\n")[0], shot="reconnect")
        s.keys("Left")
        s.keys("Enter")
        s.wait(tr("Connected for"), timeout=40, shot="reconnected")
        s.keys("Enter")
        s.wait(tr("Reconnect PPPoE"))
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()


def scenario_netmode():
    print("=== e2e airOS 8 station: bridge → router with PPPoE, test and keep ===")
    port = 18196
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--mgmt-ip", "127.0.0.24", "--station"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2enetmode", port, host="127.0.0.24")
    try:
        s.wait(tr("Device and link status"))
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("Network mode: {mode}", mode=tr("Bridge")), shot="network-bridge")
        s.keys("m")
        s.keys("Enter")
        s.wait(tr("Now: {mode}.\nWhich mode do I switch it to?", mode=tr("Bridge")),
               shot="modes")
        s.keys("r")
        s.keys("Enter")
        s.wait(tr("Switching from {old} to {new} rebuilds the antenna's whole network with the "
                  "new mode's defaults, just like the web.", old=tr("Bridge"), new=tr("Router")),
               shot="confirm-mode")
        s.keys("Left")
        s.keys("Enter")
        s.wait(tr("You are now connected through {old}. To keep access, that IP stays on the "
                  "side your PC comes from.\nWhere does your PC reach the antenna from?",
                  old="127.0.0.24"), shot="side")
        s.keys("Enter")
        s.wait(tr("Network mode: {mode} (not sent to the device yet).", mode=tr("Router")),
               shot="mode-summary")
        s.keys("Enter")
        s.wait("* " + tr("Network mode: {mode}", mode=tr("Router")), shot="network-router")
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("PPPoE: user and password from your provider"))
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("PPPoE user (your provider gives it to you):"))
        s.clear_field()
        s.keys("customer", literal=True)
        s.keys("Enter")
        s.wait(tr("PPPoE password:"))
        s.keys("secret", literal=True)
        s.keys("Enter")
        s.wait(tr("PPPoE service name (leave it empty unless your provider gives you one):"))
        s.keys("Enter")
        s.wait(tr("PPPoE MTU (64–{max}; the usual is 1492):", max=1518))
        s.keys("Enter")
        s.wait(tr("PPPoE MRU (64–{max}; the usual is 1492):", max=1518))
        s.keys("Enter")
        s.wait(tr("Require MPPE encryption?\n\nEnable it only if your provider asks for it: if "
                  "its server does not support it, it will not connect.").partition("\n")[0])
        s.keys("Enter")
        s.wait(tr("WAN fallback IP (the one it will have while PPPoE does not connect):"))
        s.keys("Enter")
        s.wait(tr("Netmask (255.255.255.0 or the prefix, for example 24):"))
        s.keys("Enter")
        s.wait(tr("Change prepared (not sent to the device yet)."))
        s.keys("Enter")
        s.wait(tr("PPPoE (user {name})", name="customer"), shot="network-pppoe")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
               .partition("\n")[2].partition("{")[0])
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("⚠ The network mode changes: {old} → {new}. The antenna's whole network is "
                  "rebuilt.", old=tr("Bridge"), new=tr("Router")), shot="confirm")
        s.keys("Enter")
        s.wait(tr("PPPoE: connected, IP {ip}.", ip="100.64.10.20"), timeout=60, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=25)
        s.keys("Enter")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
               .partition("{")[0] + "http://127.0.0.24")
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("PPPoE connection status (on the device)"), shot="network-end")
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()


def scenario_discovery():
    print("=== e2e: search for antennas without a user or password (--discover and on screen) "
          "===")
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", "18198",
                            "--mgmt-ip", "127.0.0.26", "--discovery", "10001"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    try:
        r = subprocess.run([sys.executable, TUI, "--discover", "--target", "127.0.0.1",
                            "--lang", "es"],
                           capture_output=True, text=True, timeout=30)
        with open(f"{SHOTS}/e2edisc-00-search.txt", "w") as f:
            f.write(r.stdout + "\n--- stderr\n" + r.stderr)
        check("e2e --discover: a table with the antenna", r.returncode == 0
              and "127.0.0.26" in r.stdout and "AP-North-Tower" in r.stdout
              and "airOS 8.7.19" in r.stdout, r.stdout + r.stderr)
        # (without the recent devices of the scenarios before: its address field first)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(os.path.join(WORK, "xdg", "airos-tui", "recent.json"))
        s = Session("e2edisc", 0, tui_args="--target 127.0.0.1")
        try:
            s.wait(tr("IP or name of the device (http://IP:port or https://IP work too).\nLeave "
                      "it empty to search for antennas on your network, with no user or "
                      "password:"), shot="connect")
            s.clear_field()
            s.keys("Enter")
            s.wait(tr("Search again"), shot="list")
            check("e2e on screen: it asks for no user or password",
                  not on_screen(tr("Password:").rstrip(":"), s.screen())
                  and not on_screen(tr("User"), s.screen()))
            s.keys("Enter")
            s.wait(tr("Up for"), shot="details")
            s.keys("Escape")
            s.wait(tr("Search again"))
            s.keys("Escape")
            s.wait(tr("IP or name of the device (http://IP:port or https://IP work too).\nLeave "
                      "it empty to search for antennas on your network, with no user or "
                      "password:"))
            s.keys("Escape")
            s.wait("EXIT=0", timeout=10)
        finally:
            s.close()
        # and in Tools, connected to it (--target applies there too)
        s = Session("e2edisc2", 18198, tui_args="http://127.0.0.26:18198 -u ubnt --wait 3 "
                                                "--target 127.0.0.1")
        try:
            s.wait(tr("Tools: alignment, site survey, discovery, ping, speed test"), shot="menu")
            s.keys("t")
            s.keys("Enter")
            s.wait(tr("Discovery from your PC: the Ubiquiti devices on your network"),
                   shot="tools")
            s.keys("p")
            s.keys("Enter")
            found = s.wait(tr("→ = the device you are connected to."), shot="list")
            check('e2e Tools → "Discovery from your PC": it marks the device it is connected to',
                  "→ 127.0.0.26" in found and "AP-North-Tower" in found, found)
            s.keys("Enter")
            s.wait(tr("You are connected to this device."), shot="details")
            s.keys("Enter")
            s.wait(tr("Search again"))
            s.keys("Escape")
            s.wait(tr("Discovery from your PC: the Ubiquiti devices on your network"))
            s.keys("d")
            s.keys("Enter")   # the antenna's
            antenna = s.wait("Client-North-01", shot="antenna")
            check("e2e Tools → the antenna's Discovery: what it finds, as its web shows it",
                  "127.0.0.21" in antenna and "STA" in antenna and "Gateway" in antenna, antenna)
            s.keys("Enter")
            s.wait(tr("MAC Address"), shot="antenna-details")
            s.keys("Enter")      # "Connect"
            s.wait(tr("This computer does not reach {ip} (its web answers neither HTTPS nor "
                      "HTTP): it may be behind a router. You are still connected to {current}.",
                      ip="127.0.0.21", current="127.0.0.26"), shot="unreachable")
            s.keys("Enter")
            s.wait(tr("Scan"))
            s.keys("Escape")
            s.wait(tr("Align the antenna: the signal of the link, live"))
            s.keys("Escape")
            s.wait(tr("Device and link status"))
            s.keys("0")
            s.keys("Enter")
            s.wait("EXIT=0", timeout=10)
        finally:
            s.close()
        # and next time, without an address: the device it was connected to, first
        s = Session("e2edisc3", 0, tui_args="--wait 3")
        try:
            menu = s.wait(tr("Devices you connected to lately (their address and user; never a "
                             "password):"), shot="recent")
            check("e2e recent devices: without an address, the connect screen starts with them "
                  "(their address and user)", "127.0.0.26:18198  (ubnt)" in menu
                  and on_screen(tr("Another address…"), menu)
                  and on_screen(tr("Forget these devices"), menu), menu)
            s.keys("Enter")                                  # (the first one)
            ask = s.wait(tr("HTTPS does not answer at {target}, but its web does over HTTP, "
                            "unencrypted: your user and password would travel in clear, and "
                            "anyone on the way could read them.\n\nConnect over HTTP anyway?"
                            "\n\n(With http:// before the address, or --http, this is not "
                            "asked.)").partition("{target}")[2].partition("\n")[0],
                         shot="recent-http")                  # (saved without http://)
            check("e2e recent devices: as it is saved without the protocol, it asks again before "
                  "sending the password over HTTP", "127.0.0.26:18198" in ask, ask)
            s.keys("Left")
            s.keys("Enter")                  # "Yes"
            menu = s.wait(tr("Device and link status"), timeout=30, shot="recent-in")
            check("e2e recent devices: it logs in with the user it remembers", "(ubnt)" in menu,
                  menu)
            s.keys("0")
            s.keys("Enter")
            s.wait("EXIT=0", timeout=10)
        finally:
            s.close()
    finally:
        sim.terminate()
        sim.wait(timeout=10)       # (frees port 10001 for the next scenario)


def scenario_english():
    print("=== e2e in English (AIROS_LANG=en): SSID, test mode and Keep; --discover ===")
    port = 18200
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--test-time", "60", "--lang", "en",
                            "--mgmt-ip", "127.0.0.28", "--discovery", "10001"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2een", port, host="127.0.0.28", lang="en")
    try:
        s.wait("Device and link status", shot="menu")
        s.keys("2")
        s.keys("Enter")
        s.wait("Choose the setting", shot="quick")
        s.keys("2")
        s.keys("Enter")
        s.wait('New value for "SSID"', shot="input-ssid")
        s.clear_field()
        s.keys("East-Tower", literal=True)
        s.keys("Enter")
        s.wait("Change prepared", shot="prepared")
        s.keys("Enter")
        s.wait("* SSID: East-Tower", shot="quick-marked")
        s.keys("Escape")
        s.wait("Unapplied changes: 3")
        s.keys("6")
        s.keys("Enter")
        s.wait("Test: it reverts", shot="apply-menu")
        s.keys("1")
        s.keys("Enter")
        s.wait("Continue?", shot="confirm")
        s.keys("Enter")
        scr = s.wait("ACTIVE IN TEST MODE", timeout=40, shot="decide")
        check("e2e en: Keep / Revert buttons", "Keep" in scr and "Revert" in scr, scr)
        s.keys("Enter")   # Keep
        s.wait("saved for good", timeout=20, shot="saved")
        s.keys("Enter")
        s.keys("3")
        s.keys("Enter")
        s.wait("Network mode: Bridge", shot="network")
        s.keys("Escape")
        s.wait("Device and link status")
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10, shot="exit")
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.28:{port}", "ubnt", "ubnt")
        dev.connect()
        check("e2e en: SSID saved", T.Config(dev.get_config()).get("wireless.1.ssid")
              == "East-Tower")
        env = dict(os.environ, AIROS_LANG="en")
        r = subprocess.run([sys.executable, TUI, "--discover", "--target", "127.0.0.1"],
                           capture_output=True, text=True, timeout=30, env=env)
        with open(f"{SHOTS}/e2een-99-search.txt", "w") as f:
            f.write(r.stdout + "\n--- stderr\n" + r.stderr)
        check("e2e en: --discover in English", r.returncode == 0
              and "127.0.0.28" in r.stdout and "AP-North-Tower" in r.stdout
              and r.stdout.startswith("IP ") and "Name" in r.stdout and "Uptime" in r.stdout
              and "Looking for Ubiquiti antennas on 127.0.0.1" in r.stderr
              and "1 device(s)." in r.stderr, r.stdout + r.stderr)
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_services():
    print("=== e2e services: discovery and LLDP off, CDP and the ping watchdog on, test and "
          "keep ===")
    port = 18202
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--test-time", "60",
                            "--mgmt-ip", "127.0.0.32", "--discovery", "10001"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eserv", port, host="127.0.0.32")
    try:
        s.wait(tr("Services: web, SSH, SNMP, discovery, ping watchdog, UISP and more"),
               shot="menu")
        s.keys("s")
        s.keys("Enter")
        s.wait(tr("Ubiquiti discovery: enabled"), shot="services")
        s.keys("Enter")
        s.wait(tr("Disable Ubiquiti discovery?\n\nThe antenna will stop answering the discovery "
                  "query (UDP 10001): neither airos-tui --discover nor the \"Discovery\" tool of "
                  "other Ubiquiti devices will find it. If a later change moves it to DHCP or "
                  "PPPoE, this program will only be able to look for it at its fallback IP.")
               .partition("\n")[0], shot="confirm-discovery")
        s.keys("Left")
        s.keys("Enter")                    # "Yes" (turning it off, "No" is the default)
        s.wait("* " + tr("Ubiquiti discovery: disabled"), shot="marked")
        s.keys("c")
        s.keys("Enter")
        s.wait(tr("Enable CDP?\n\nThe antenna will announce itself with CDP (Cisco Discovery "
                  "Protocol), so that the switches and routers that understand it list it "
                  "among their neighbors.").partition("\n")[0], shot="confirm-cdp")
        s.keys("Enter")
        s.wait("* " + tr("CDP: enabled"), shot="marked-cdp")
        s.keys("l")
        s.keys("Enter")
        s.wait(tr("Disable LLDP?\n\nThe antenna will stop using LLDP (Link Layer Discovery "
                  "Protocol, IEEE 802.1AB): switches and routers will no longer list it among "
                  "their neighbors. It does not affect access to the antenna or Ubiquiti "
                  "discovery.\n\nThe web does not show this setting: airos-tui changes the key "
                  "the firmware reads, discovery.lldp.status.").partition("\n")[0],
               shot="confirm-lldp")
        s.keys("Enter")
        s.wait("* " + tr("LLDP: disabled"), shot="marked-lldp")
        s.keys("w")
        s.keys("Enter")                        # the ping watchdog
        s.wait(tr("Ping Watchdog: {state}", state=tr("disabled")), shot="watchdog")
        s.keys("Enter")
        s.wait(tr("IP Address To Ping"), shot="watchdog-ip")
        s.clear_field()
        s.keys("192.168.1.1", literal=True)
        s.keys("Enter")
        s.wait("* " + tr("IP Address To Ping: {address}", address="192.168.1.1"),
               shot="watchdog-enabled")
        s.keys("f")
        s.keys("Enter")
        s.wait(tr("Pings in a row without an answer that make the device reboot (from {low} to "
                  "{high}):", low=3, high=15), shot="watchdog-failures")
        s.clear_field()
        s.keys("5", literal=True)
        s.keys("Enter")
        s.wait("* " + tr("Failure Count To Reboot: {count}", count=5), shot="watchdog-failures-5")
        s.keys("Escape")
        s.wait("* " + tr("Ping Watchdog: {state}", state=tr("enabled") + " · 192.168.1.1"),
               shot="marked-watchdog")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.32:{port}", user="ubnt", count=6), shot="menu-pending")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"), shot="confirm")
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        r = subprocess.run([sys.executable, TUI, "--discover", "--target", "127.0.0.1",
                            "--timeout", "2", "--lang", "es"],
                           capture_output=True, text=True, timeout=30)
        with open(f"{SHOTS}/e2eserv-99-search.txt", "w") as f:
            f.write(r.stdout + "\n--- stderr\n" + r.stderr)
        check("e2e services: it no longer answers discovery", r.returncode == 1
              and tr("No airOS antenna answered on {where}.", where="127.0.0.1") in r.stderr,
              r.stdout + r.stderr)
        dev = T.AirOS(f"http://127.0.0.32:{port}", "ubnt", "ubnt")
        dev.connect()
        cfg = T.Config(dev.get_config())
        check("e2e services: keys saved", cfg.get("discovery.status") == "disabled"
              and cfg.get("discovery.cdp.status") == "enabled"
              and cfg.get("discovery.lldp.status") == "disabled"
              and (cfg.get("pwdog.status"), cfg.get("pwdog.host"), cfg.get("pwdog.retry"))
              == ("enabled", "192.168.1.1", "5")
              and not dev.test_mode_info()["active"])
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_access():
    print("=== e2e access: the SSH port and its web's; test and keep, and the program follows "
          "its web ===")
    port, new = 18212, 18213
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--test-time", "60", "--web-follow"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eacc", port)
    try:
        s.wait(tr("Services: web, SSH, SNMP, discovery, ping watchdog, UISP and more"),
               shot="menu")
        s.keys("s")
        s.keys("Enter")
        s.wait(tr("SSH Server: enabled, port {port}, {count} key(s)", port=22, count=0),
               shot="services")
        s.keys("s")
        s.keys("Enter")
        s.wait(tr("Password Authentication: enabled"), shot="ssh")
        s.keys("p")
        s.keys("Enter")
        s.wait(tr("Server Port:"), shot="ssh-port")
        s.clear_field()
        s.keys(str(port), literal=True)
        s.keys("Enter")   # (its web's)
        s.wait(tr("Server Port is used by another service"), shot="ssh-taken")
        s.keys("Enter")
        s.wait(tr("Server Port:"))
        s.clear_field()
        s.keys("2222", literal=True)
        s.keys("Enter")
        s.wait("* " + tr("Server Port: {port}", port=2222), shot="ssh-2222")
        s.keys("Escape")
        s.wait("* " + tr("SSH Server: enabled, port {port}, {count} key(s)", port=2222, count=0),
               shot="marked-ssh")
        s.keys("h")
        s.keys("Enter")
        s.wait(tr("Server Port: {port}", port=port), shot="web")
        s.keys("h")
        s.keys("Enter")
        s.wait(tr("Server Port:"), shot="web-port")
        s.clear_field()
        s.keys(str(new), literal=True)
        s.keys("Enter")
        s.wait("* " + tr("Server Port: {port}", port=new), shot="web-new")
        s.keys("Escape")
        s.wait("* " + tr("Web Server: HTTP on port {http}, without HTTPS", http=new),
               shot="marked-web")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=2), shot="menu-pending")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("⚠ Its web moves: {old} → {new}. The program will reconnect there.",
                  old=f"http://127.0.0.1:{port}", new=f"http://127.0.0.1:{new}"), shot="confirm")
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.wait(tr("Its web now answers at {new} (before {old}).", new=f"http://127.0.0.1:{new}",
                  old=f"http://127.0.0.1:{port}"), timeout=5)
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{new}", "ubnt", "ubnt")
        dev.connect()
        cfg = T.Config(dev.get_config())
        check("e2e access: saved, and its web now answers only on the new port",
              (cfg.get("sshd.port"), cfg.get("httpd.port")) == ("2222", str(new))
              and not dev.test_mode_info()["active"]
              and not T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt").ping())
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_system():
    print("=== e2e system: the LEDs' night mode, the administrator's name and the latitude; test "
          "and keep, and the program logs in with the new name ===")
    port = 18228
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2esys", port)
    try:
        s.wait(tr("System: firmware, device, accounts, location, maintenance, backups"),
               shot="menu")
        s.keys("y")
        s.keys("Enter")
        s.wait(tr("Device: interface language {language}", language="English"), shot="system")
        s.keys("e")
        s.keys("Enter")
        s.wait(f"{tr('LEDs Night Mode')}: {tr('disabled')}", shot="device")
        s.keys("n")
        s.keys("Enter")
        s.wait(f"* {tr('LEDs Night Mode')}: {tr('enabled')}", shot="led")
        s.keys("Escape")
        s.wait(tr("System Accounts: {admin}; no read-only account", admin="ubnt"))
        s.keys("a")
        s.keys("Enter")
        s.wait(tr("Administrator Username: {name}", name="ubnt"), shot="accounts")
        s.keys("u")
        s.keys("Enter")
        s.wait(tr("Administrator Username:"), shot="name")
        s.clear_field()
        s.keys("admin-e2e", literal=True)
        s.keys("Enter")
        s.wait("* " + tr("Administrator Username: {name}", name="admin-e2e"), shot="name-new")
        s.keys("Escape")
        s.wait(tr("Location: not set"))
        s.keys("l")
        s.keys("Enter")
        s.wait(tr("Device Height: {value}", value="—"), shot="location")
        s.keys("a")
        s.keys("Enter")
        s.wait(tr("Latitude:"), shot="latitude")
        s.clear_field()
        s.keys("40.4168", literal=True)
        s.keys("Enter")   # (tmux takes
        s.wait("* " + tr("Latitude: {value}", value="40.4168"), shot="latitude-new")
        s.keys("Escape")  #  "-…" for an option)
        s.wait("* " + tr("Location: {latitude}, {longitude}", latitude="40.4168", longitude="—"))
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=3), shot="menu-pending")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("⚠ The administrator is renamed: {old} → {new}. From then on the program logs "
                  "in with the new name (the password does not change).", old="ubnt",
                  new="admin-e2e"), shot="confirm")
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "admin-e2e", "ubnt")
        dev.connect()
        cfg = T.Config(dev.get_config())
        check("e2e system: saved, and it logs in as admin-e2e",
              (cfg.get("users.1.name"), cfg.get("system.leds.nightmode"),
               cfg.get("system.latitude")) == ("admin-e2e", "enabled", "40.4168")
              and not dev.test_mode_info()["active"])
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_graphs():
    print("=== e2e graphs: the live screen of its main page ===")
    port = 18230
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2egraphs", port)
    try:
        s.wait(tr("Graphs: capacity, throughput and latency, live"), shot="menu")
        s.keys("g")
        s.keys("Enter")
        s.wait(tr("── This device ──"), shot="graphs")
        screen = s.wait(tr("Throughput RX"), timeout=10)
        time.sleep(4)                                  # (a few reads: their points)
        screen = s.screen()
        s.shot("graphs-points", screen)
        check("e2e graphs: this device and the other end, with values and their bars",
              on_screen(tr("── The other end: {name} ──", name="Client-North-01"), screen)
              and "2.00 Mbps" in screen and any(c in screen for c in "▁▂▃▄▅▆▇█"), screen)
        s.keys("q")
        s.wait(tr("Device and link status"), shot="back")
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_advnet():
    print("=== e2e advanced network: the advanced mode, a VLAN and a bridge with it; test and "
          "keep ===")
    port = 18232
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eadv", port)
    try:
        s.wait(tr("Device and link status"), shot="menu")
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("Configuration mode: simple"), shot="network")
        s.keys("a")
        s.keys("Enter")
        s.wait(tr("Switch to the advanced network configuration?\n\nAs in its web, the network "
                  "is then set as its interfaces, VLANs and bridges; it does not change by "
                  "switching.").partition("\n")[0], shot="confirm")
        s.keys("Enter")
        s.wait("* " + tr("Configuration mode: advanced"), shot="advanced")
        s.keys("a")
        s.keys("Down")
        s.keys("Enter")          # (vl, after the mode: "v" goes
                                                              #  to the management VLAN)
        s.wait(tr("Add a VLAN"), shot="vlans")
        s.keys("+")
        s.keys("Enter")
        s.wait(tr("Its interface:"), shot="interface")
        s.keys("e")
        s.keys("Enter")
        s.wait(tr("VLAN ID (2 to 4094):"), shot="id")
        s.clear_field()
        s.keys("100", literal=True)
        s.keys("Enter")
        s.wait(tr("Comment (optional):"))
        s.keys("Enter")
        s.wait("* LAN0  VLAN 100", shot="vlan")
        s.keys("Escape")
        s.wait(tr("Bridges: {list}", list="BRIDGE0"))
        s.keys("b")
        s.keys("Enter")
        s.wait(tr("Add a bridge"), shot="bridges")
        s.keys("+")
        s.keys("Enter")
        s.wait(tr("Its ports: choose one to put it in or take it out (the free interfaces are "
                  "offered)."), shot="ports")
        s.keys("e")
        s.keys("Enter")
        s.wait("[x] LAN0.100", shot="port")
        s.keys("o")
        s.keys("Enter")
        s.wait(tr("Comment (optional):"))
        s.keys("Enter")
        s.wait("* BRIDGE1: LAN0.100", shot="bridge")
        s.keys("Escape")
        s.wait("* " + tr("Bridges: {list}", list="BRIDGE0, BRIDGE1"), shot="network-changes")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
               .partition("\n")[2].partition("{")[0], shot="menu-pending")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"), shot="confirm-apply")
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        dev.connect()
        cfg = T.Config(dev.get_config())
        check("e2e advanced network: saved (VLAN, bridge, their netconf and the advanced mode)",
              [v["devname"] for v in T.vlan_list(cfg)] == ["eth0.100"]
              and [(b["devname"], b["ports"]) for b in T.bridge_list(cfg)] == [
                  ("br0", ["eth0", "ath0"]), ("br1", ["eth0.100"])]
              and cfg.get("gui.network.advanced.status") == "enabled"
              and not dev.test_mode_info()["active"], str(T.bridge_list(cfg)))
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_airview():
    print("=== e2e airView: its live views (waveform, waterfall and noise) ===")
    port = 18234
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--airview-period", "0.3"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eav", port)
    try:
        s.wait(tr("Tools: alignment, site survey, discovery, ping, speed test"), shot="menu")
        s.keys("t")
        s.keys("Enter")
        s.wait(tr("airView: the spectrum the device hears, live"), shot="tools")
        s.keys("e")
        s.keys("Enter")
        s.wait(tr("Waterfall View: its last 24 reads, the newest at the bottom"),
               shot="airview-menu")
        s.keys("Enter")                                      # 1: the waveform
        s.wait(tr("Probability") + ":", shot="waveform")
        time.sleep(2)                                        # (a few reads)
        wave = s.wait("5900", shot="waveform-more")
        check("e2e airView: the waveform, with its scale, its bands and its frequencies",
              " -120┤" in wave and "█" in wave and "5100" in wave and "MHz" in wave
              and on_screen(tr("Any key goes back."), wave), wave)
        s.keys("q")
        s.wait(tr("Waterfall View: its last 24 reads, the newest at the bottom"), shot="back")
        s.keys("2")
        s.keys("Enter")
        s.wait(tr("Waterfall View") + " · " + tr("a row per new read, the newest at the bottom"),
               shot="waterfall")
        time.sleep(3)
        fall = s.screen()
        s.shot("waterfall-more", fall)
        filled = [x for x in fall.split("\n") if "│" in x and any(c in x for c in "·:░▒▓█")]
        check("e2e airView: the waterfall fills up, a row per read (the same session)",
              len(filled) >= 6, fall)
        s.keys("q")
        s.wait(tr("Waterfall View: its last 24 reads, the newest at the bottom"))
        s.keys("3")
        s.keys("Enter")
        noise = s.wait(tr("Ambient Noise Level") + " · " + tr("Minute Peak"), shot="noise")
        check("e2e airView: the noise of each minute, with its time",
              re.search(r"\d\d:\d\d┤[ ·:░▒▓█]+", noise) is not None, noise)
        s.keys("q")
        s.wait(tr("Waterfall View: its last 24 reads, the newest at the bottom"))
        s.keys("Escape")
        s.wait(tr("airView: the spectrum the device hears, live"))
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_wireless():
    print("=== e2e wireless airOS 8: 40 MHz with its frequency, WPA key, test and keep ===")
    port = 18203
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2", "--test-time", "60", "--mgmt-ip", "127.0.0.33"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2ewifi", port, host="127.0.0.33")
    try:
        s.wait(tr("Wireless: mode, SSID, frequency, power, security, MAC ACL…"), shot="menu")
        s.keys("w")
        s.keys("Enter")
        s.wait(f"{tr('Wireless mode')}: {tr('Access point PtMP')}", shot="wireless")
        s.wait(f"{tr('Country')}: Mexico (484)")
        s.keys("b")
        s.keys("Enter")
        s.wait(tr("Channel width (the web offers these in country {cc}):", cc=484),
               shot="widths")
        s.keys("4")
        s.keys("Enter")                       # 40 MHz
        s.wait(tr("Center frequency:"), shot="centers")
        s.keys("Down")
        s.keys("Down")
        s.keys("Enter")    # 5190, 5230, [5755], 5795
        s.wait(tr("Control frequency (the primary 20 MHz channel) within {center} MHz:",
                  center=5755), shot="controls")
        s.keys("Enter")   # 5745
        s.wait(f"* {tr('Channel width')}: 40 MHz", shot="marked")
        s.wait("* " + tr("Frequency") + ": 5745 MHz " + tr("(center {center} MHz)", center=5755))
        s.keys("x")
        s.keys("Enter")
        s.wait(tr("Security: {security}", security="WPA2 " + tr("personal (PSK)")),
               shot="security")
        s.keys("k")
        s.keys("Enter")
        s.wait(tr("Shared key (PSK), from 8 to 63 printable characters:"), shot="key")
        s.clear_field(20)
        s.keys("key-e2e-123", literal=True)
        s.keys("Enter")
        s.wait(tr("WPA key: {key}", key="key-e2e-123"), shot="key-changed")
        s.keys("Escape")
        s.wait(f"* {tr('Security')}: WPA2 {tr('personal (PSK)')}", shot="back")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.33:{port}", user="ubnt", count=5), shot="menu-pending")
        s.keys("6")
        s.keys("Enter")
        s.wait(tr("Test: it reverts by itself if you do not confirm (recommended)"))
        s.keys("1")
        s.keys("Enter")
        s.wait(tr("Continue?"), shot="confirm")
        s.keys("Enter")
        s.wait(tr("The changes are ACTIVE IN TEST MODE."), timeout=40, shot="decide")
        s.keys("Enter")
        s.wait(tr("Changes saved for good."), timeout=20, shot="saved")
        s.keys("Enter")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.33:{port}", "ubnt", "ubnt")
        dev.connect()
        cfg = T.Config(dev.get_config())
        got = {k: cfg.get(k) for k in ("radio.1.chanbw", "radio.1.center.1.freq", "radio.1.freq",
                                       "radio.1.ieee_mode", "aaa.1.wpa.psk",
                                       "wpasupplicant.profile.1.network.1.psk")}
        check("e2e wireless: saved as the web saves it", got == {
            "radio.1.chanbw": "40", "radio.1.center.1.freq": "5755", "radio.1.freq": "5745",
            "radio.1.ieee_mode": "11acvht40", "aaa.1.wpa.psk": "key-e2e-123",
            "wpasupplicant.profile.1.network.1.psk": "key-e2e-123"}, str(got))
        check("e2e wireless: no test mode left pending", not dev.test_mode_info()["active"])
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_tools():
    print("=== e2e tools (airOS 8 station): live alignment, find an AP and lock to it; change "
          "the password ===")
    port = 18215
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60", "--station"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    backup = os.path.join(WORK, "tools-backup.cfg")
    if os.path.exists(backup):                              # (from an earlier run)
        os.remove(backup)
    s = Session("e2etools", port)
    try:
        s.wait(tr("Tools: alignment, site survey, discovery, ping, speed test"), shot="menu")
        s.keys("t")
        s.keys("Enter")
        s.wait(tr("Align the antenna: the signal of the link, live"), shot="tools")
        s.keys("Enter")
        s.wait(tr("Local signal"), shot="alignment")
        s.wait(tr("Any key goes back."))
        time.sleep(2.5)                                     # (it redraws itself)
        live = s.wait(tr("chains 0 / 1: {chains} (difference {diff} dB)").partition("{")[0],
                      shot="alignment-more")
        check("e2e tools: the alignment shows both ends and their chains",
              on_screen(tr("Remote signal"), live)
              and on_screen(tr("best {signal}", signal="-58 dBm"), live), live)
        s.keys("q")                                         # any key
        s.wait(tr("Site survey: the access points around"), shot="tools-back")
        # (the first word of its title, "Antenna alignment", as the check had it)
        check("e2e tools: a key goes back to the menu",
              not on_screen(tr("Antenna alignment").split()[0], s.screen()))
        s.keys("s")
        s.keys("Enter")
        s.wait("North-Tower", timeout=30, shot="list")
        s.keys("Enter")
        s.wait(tr("Lock to AP: this SSID and only this access point (its MAC)"), shot="ap")
        s.keys("l")
        s.keys("Enter")
        s.wait(tr("Shared key (PSK), from 8 to 63 printable characters:"), shot="key")
        s.clear_field(20)
        s.keys("north-key-123", literal=True)
        s.keys("Enter")
        s.wait(tr("Locked to the access point {mac}.", mac="68:D7:9A:11:22:30"), shot="prepared")
        s.keys("Enter")
        s.keys("Escape")
        menu = s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}")
                      .partition("\n")[2].partition("{")[0], shot="menu-changes")
        # (the backup items by their first words, as the check had them: "Save a backup"…)
        check("e2e tools: the backups and the reboot are no longer in the main menu",
              not on_screen(" ".join(tr("Save a backup of the configuration").split()[:3]), menu)
              and not on_screen(" ".join(tr("Load a backup of the configuration").split()[:3]),
                                menu)
              and not on_screen(tr("Reboot the device"), menu)
              and on_screen(tr("System: firmware, device, accounts, location, maintenance, "
                               "backups"), menu), menu)
        s.keys("y")
        s.keys("Enter")
        system = s.wait(tr("Change the administrator password"), shot="system")
        check("e2e tools: System has the reboot and the backups",
              on_screen(tr("Reboot the device"), system)
              and on_screen(tr("Load a backup of the configuration"), system), system)
        s.keys("n")
        s.keys("Enter")
        s.wait(tr('New value for "{setting}":', setting=tr("Device name")), shot="name")
        s.clear_field()
        s.keys("South-Tower", literal=True)
        s.keys("Enter")
        s.wait(tr("Change prepared (not sent to the device yet)."))
        s.keys("Enter")
        s.wait(f"* {tr('Device name')}: South-Tower", shot="system-name")
        s.keys("b")
        s.keys("Enter")                        # the device's backup, as the web
        s.wait(tr("The device's configuration backup, as its web downloads it.\n\nWhere to "
                  "save it (a folder or a file):").rpartition("\n")[2], shot="backup")
        s.clear_field(80)
        s.keys(backup, literal=True)
        s.keys("Enter")
        s.wait(tr("Saved in {path} ({size} KB).\n\nIt has the whole configuration, with the "
                  "hashes of its passwords: keep it safe.").rpartition("\n")[2])
        s.keys("Enter")
        s.wait(tr("Save a backup of the configuration"))
        s.keys("p")
        s.keys("Enter")
        s.wait(tr("Current password of {user}:", user="ubnt"), shot="current")
        s.keys("ubnt", literal=True)
        s.keys("Enter")
        s.wait(tr("New password (from 12 characters; with 20 or more, any):"))
        s.keys("north-tower-26", literal=True)
        s.keys("Enter")
        s.wait(tr("Verify New Password:"))
        s.keys("north-tower-26", literal=True)
        s.keys("Enter")
        s.wait(tr("As its web does, the device changes the password right away and saves it, "
                  "without test mode.\n\nKeep the new one safe: without it you would have to "
                  "reset the device to its defaults.\n\nChange the password of {user}?",
                  user="ubnt").rpartition("\n")[2], shot="confirm")
        s.keys("Left")
        s.keys("Enter")                     # "Yes" (the focus starts on "No")
        s.wait(tr("Administrator password successfully changed."), shot="changed")
        s.keys("Enter")
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait(tr("There are {count} unapplied change(s) that will be lost.\nExit anyway?")
               .partition("\n")[2], shot="quit")
        s.keys("Left")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        ok_new = bool(T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "north-tower-26").connect())
        try:
            T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt").connect()
            old_rejected = False
        except T.AuthError:
            old_rejected = True
        check("e2e tools: the device was left with the new password",
              ok_new and old_rejected)
    finally:
        sim.terminate()


def scenario_maintenance():
    print("=== e2e maintenance (airOS 8): support info and factory reset ===")
    port = 18216
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    cli = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    cli.connect()
    c = T.Config(cli.get_config())
    c.set("resolv.host.1.name", "Before-Reset")
    cli.write_config(c, test=False)
    time.sleep(1.5)                                         # the simulated restart
    sup = os.path.join(WORK, "e2e-support.sup")
    cfg = os.path.join(WORK, "e2e-backup.cfg")
    for f in (sup, cfg):
        if os.path.exists(f):
            os.remove(f)
    s = Session("e2emaint", port)
    try:
        s.wait("Before-Reset", shot="menu")
        s.keys("y")
        s.keys("Enter")
        system = s.wait(tr("Reset to factory defaults"), shot="system")
        order = [system.find(t) for t in (tr("Firmware: {version}", version="v8.7.19"),
                                          tr("Device name"), tr("Reboot the device"),
                                          tr("Reset to factory defaults"),
                                          tr("Download the support info"),
                                          tr("Save a backup of the configuration"),
                                          tr("Load a backup of the configuration"))]
        check("e2e maintenance: System in the web's order", -1 not in order
              and order == sorted(order), system)
        s.keys("s")
        s.keys("Enter")
        s.wait(tr("The device's support file: its configuration, logs and statistics, for "
                  "Ubiquiti support.\n\nWhere to save it (a folder or a file):")
               .rpartition("\n")[2], shot="support-where")
        s.clear_field(80)
        s.keys(sup, literal=True)
        s.keys("Enter")
        s.wait(tr("Saved in {path} ({size} KB).\n\nIt has the device's configuration and its "
                  "logs: share it only with whom you trust, such as Ubiquiti support.")
               .partition("{")[0], shot="support-saved")
        s.keys("Enter")
        s.wait(tr("Save a backup of the configuration"))   # (System again before a key)
        s.keys("b")
        s.keys("Enter")                        # the device's backup (cfg.cgi)
        s.wait(tr("The device's configuration backup, as its web downloads it.\n\nWhere to "
                  "save it (a folder or a file):").rpartition("\n")[2], shot="backup-where")
        s.clear_field(80)
        s.keys(cfg, literal=True)
        s.keys("Enter")
        s.wait(tr("Saved in {path} ({size} KB).\n\nIt has the whole configuration, with the "
                  "hashes of its passwords: keep it safe.").rpartition("\n")[2],
               shot="backup-saved")
        s.keys("Enter")
        s.wait(tr("Load a backup of the configuration"))
        s.keys("u")
        s.keys("Enter")                        # and back, as the web uploads it
        s.wait(tr("Backups in {path}").partition("{")[0], shot="load")
        s.keys("o")
        s.keys("Enter")
        s.wait(tr("Path of the .cfg file:"))
        s.clear_field(80)
        s.keys(cfg, literal=True)
        s.keys("Enter")
        s.wait(tr("Upload {name} to the device, as its web does?", name=os.path.basename(cfg)),
               shot="upload")
        s.keys("Left")
        s.keys("Enter")
        s.wait(tr("Configuration backup file uploaded."), shot="apply-or-discard")
        s.keys("Left")
        s.keys("Enter")                     # "Apply" (focus on "Discard")
        s.wait(tr("Backup applied."), timeout=40, shot="applied")
        s.keys("Enter")
        s.wait(tr("Reset to factory defaults"))
        s.keys("f")
        s.keys("Enter")
        s.wait(tr("Do you want to reset the device to defaults?"), shot="reset")
        s.keys("Left")
        s.keys("Enter")                     # "Reset" (focus on "Cancel")
        s.wait(tr("Device has been reset to defaults. Please wait while it reboots..."),
               shot="reset-done")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        check("e2e maintenance: the support file, for the user only",
              os.path.isfile(sup) and oct(os.stat(sup).st_mode & 0o777) == "0o600"
              and open(sup, "rb").read(2) == b"\x1f\x8b")
        check("e2e maintenance: the backup is the device's file, for the user only",
              os.path.isfile(cfg) and oct(os.stat(cfg).st_mode & 0o777) == "0o600"
              and open(cfg, encoding="utf-8").read().startswith("### PART ###"))
        time.sleep(2.5)                                     # the simulated reboot
        after = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        after.connect()
        name = (after.status().get("host") or {}).get("hostname")
        check("e2e maintenance: the device went back to its factory configuration",
              name == "AP-North-Tower", str(name))
    finally:
        sim.terminate()


def scenario_firmware():
    print("=== e2e firmware (airOS 8): upload a firmware and upgrade ===")
    port = 18217
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    fw = os.path.join(WORK, "WA.v8.7.20.bin")
    with open(fw, "wb") as f:
        f.write(b"UBNT" + b"\0" * 12 + b"WA.v8.7.20\0" + os.urandom(4096))
    s = Session("e2efw", port)
    try:
        s.wait(tr("System: firmware, device, accounts, location, maintenance, backups"),
               shot="menu")
        s.keys("y")
        s.keys("Enter")
        s.wait(tr("Firmware: {version}", version="v8.7.19"), shot="system")
        s.keys("v")
        s.keys("Enter")
        s.wait(tr("Firmware Version: {version}", version="v8.7.19 (WA)"), shot="firmware")
        s.keys("u")
        s.keys("Enter")
        s.wait(tr("The firmware file (.bin), or a folder to choose it from:"), shot="file")
        s.clear_field(80)
        s.keys(fw, literal=True)
        s.keys("Enter")
        s.wait(tr("Uploaded Firmware Version: {version}", version="WA.v8.7.20"), shot="uploaded")
        s.keys("Left")
        s.keys("Enter")                     # "Update" (focus on "Discard")
        s.wait(tr("Firmware updated: {old} → {new}.", old="v8.7.19", new="v8.7.20"), timeout=60,
               shot="updated")
        s.keys("Enter")
        s.wait(tr("Firmware Version: {version}", version="v8.7.20 (WA)"), shot="firmware-new")
        s.keys("Escape")
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        after = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        after.connect()
        now = (after.status().get("host") or {}).get("fwversion")
        check("e2e firmware: the device was left with the new version", now == "v8.7.20",
              str(now))
    finally:
        sim.terminate()


def scenario_fw_check():
    print("=== e2e check for updates (airOS 8, simulator with --fw-update) ===")
    port = 18220
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60", "--fw-update", "v8.7.20"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    # (when it last checked each device is kept in its data folder: from scratch, its daily
    # check runs as it starts)
    state = os.path.join(WORK, "xdg", "airos-tui", "auto-update.json")
    if os.path.exists(state):
        os.unlink(state)
    s = Session("e2efwc", port)
    try:
        # As its web as it starts: "Check for Updates" is on (the simulator has no key, as its
        # web shows it then), so it checks; dismissing what it offers does not stop Check Now.
        s.wait(tr("Firmware update {version} is available.", version="WA.v8.7.20"),
               shot="at-start")
        s.wait("- Fixes.")
        s.keys("Enter")                                     # (its notes: "Back")
        s.wait(tr("Download firmware {version} to the device?", version="WA.v8.7.20"),
               shot="at-start-download")
        s.keys("Right")
        s.keys("Enter")                    # "Dismiss"
        s.wait(tr("System: firmware, device, accounts, location, maintenance, backups"),
               shot="menu")
        s.keys("y")
        s.keys("Enter")
        s.wait(tr("Firmware: {version}", version="v8.7.19"), shot="system")
        s.keys("v")
        s.keys("Enter")
        s.wait(tr("Firmware Version: {version}", version="v8.7.19 (WA)"), shot="firmware")
        s.keys("n")
        s.keys("Enter")                        # "Check Now"
        s.wait("airOS v8.7.20 (simulated)", shot="notes")
        s.keys("Enter")
        s.wait(tr("Download firmware {version} to the device?", version="WA.v8.7.20"),
               shot="download")
        s.keys("Enter")
        s.wait("Simulated license", shot="license")
        s.keys("Enter")
        s.wait(tr("Do you agree with the license of firmware {version}?", version="WA.v8.7.20"),
               shot="agree")
        s.keys("Enter")
        s.wait(tr("Uploaded Firmware Version: {version}", version="WA.v8.7.20"), shot="uploaded")
        s.keys("Left")
        s.keys("Enter")                     # "Update" (focus on "Discard")
        s.wait(tr("Firmware updated: {old} → {new}.", old="v8.7.19", new="v8.7.20"), timeout=60,
               shot="updated")
        s.keys("Enter")
        s.wait(tr("Firmware Version: {version}", version="v8.7.20 (WA)"), shot="firmware-new")
        s.keys("n")
        s.keys("Enter")                        # and again: nothing newer
        s.wait(tr("You have latest available firmware version."), shot="latest")
        s.keys("Enter")
        s.wait(tr("Firmware Version: {version}", version="v8.7.20 (WA)"))
        s.keys("Escape")
        s.wait(tr("Firmware: {version}", version="v8.7.20"))
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_net_tools():
    print("=== e2e network tools (airOS 8): ping and traceroute from the device ===")
    port = 18221
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2eping", port)
    try:
        s.wait(tr("Tools: alignment, site survey, discovery, ping, speed test"), shot="menu")
        s.keys("t")
        s.keys("Enter")
        s.wait(tr("Ping: the device pings an address"), shot="tools")
        s.keys("n")
        s.keys("Enter")
        s.wait(tr("Packet Count: {count}", count=5), shot="ping")
        s.keys("Enter")     # "Start"
        s.wait(tr("Select Destination IP"), shot="target")
        s.keys("1")
        s.keys("Enter")
        ping = s.wait(tr("Results") + " · 192.168.1.1", timeout=30, shot="ping-results")
        check("e2e ping: 5 replies and their results",
              on_screen(tr("Packet Loss: {loss}% ({received} of {total} packets received)",
                           loss=0, received=5, total=5), ping)
              and ping.count("192.168.1.1  ") >= 5, ping)
        s.keys("Enter")
        s.wait(tr("Packet Count: {count}", count=5))
        s.keys("Escape")
        s.wait(tr("Traceroute: the way from the device to an address"))
        s.keys("r")
        s.keys("Enter")
        s.wait(tr("Resolve IP Addresses: {state}", state=tr("no")), shot="traceroute")
        s.keys("r")
        s.keys("Enter")
        s.wait(tr("Resolve IP Addresses: {state}", state=tr("yes")))
        s.keys("s")
        s.keys("Enter")
        s.wait(tr("Destination Host") + ":", shot="trace-target")
        s.clear_field()
        s.keys("8.8.8.8", literal=True)
        s.keys("Enter")
        trace = s.wait(tr("Results") + " · 8.8.8.8", timeout=30, shot="trace")
        check("e2e traceroute: the hops, the one that does not answer and the names",
              "* · * · *" in trace and "dns.google" in trace and "gateway.lan" in trace, trace)
        s.keys("Enter")
        s.wait(tr("Resolve IP Addresses: {state}", state=tr("yes")))
        s.keys("Escape")
        s.wait(tr("Align the antenna: the signal of the link, live"))
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_date():
    print("=== e2e System → Date Settings (airOS 8): time zone and NTP ===")
    port = 18222
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    art = next(i for i, (v, _) in enumerate(T.TIME_ZONES) if v == "ART3")
    utc = next(i for i, (v, _) in enumerate(T.TIME_ZONES) if v == "UTC")
    s = Session("e2edate", port)
    try:
        s.wait(tr("System: firmware, device, accounts, location, maintenance, backups"),
               shot="menu")
        s.keys("y")
        s.keys("Enter")
        s.wait(tr("Date settings: {zone}, NTP {state}", zone="UTC/GMT", state=tr("disabled")),
               shot="system")
        s.keys("d")
        s.keys("Enter")
        s.wait(tr("Time Zone: {zone}", zone="(UTC/GMT) Universal Time Coordinated"), shot="date")
        s.keys("Enter")                                     # "Time Zone"
        s.wait(tr("The device's time zone:"), shot="zones")
        s.keys(*(["Up"] * (utc - art)))                     # from UTC up to Buenos Aires
        s.keys("Enter")
        s.wait("* " + tr("Time Zone: {zone}", zone="(GMT-03:00) Greenland, Buenos Aires"),
               shot="zone")
        s.keys("n")
        s.keys("Enter")
        s.wait("* " + tr("NTP Client: {state}", state=tr("enabled")), shot="ntp")
        s.keys("Escape")
        s.wait("* " + tr("Date settings: {zone}, NTP {state}", zone="GMT-03:00",
                         state=tr("enabled")), shot="system-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=3), shot="pending")
        s.keys("0")
        s.keys("Enter")
        s.wait(tr("There are {count} unapplied change(s) that will be lost.\nExit anyway?")
               .partition("\n")[2])
        s.keys("Left")
        s.keys("Enter")   # "Yes" (focus on "No")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_mgmt_vlan():
    print("=== e2e Network → Management VLAN (airOS 8): prepare it ===")
    port = 18224
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    base = T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60).running))
    pending = len(T.plan_mgmt_vlan(base, 10))
    s = Session("e2evlan", port)
    try:
        s.wait(tr("Network: mode (bridge/router), management IP and VLAN, DNS"), shot="menu")
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("Management VLAN: {state}", state=tr("disabled")), shot="network")
        s.keys("v")
        s.keys("Enter")
        s.wait(tr("VLAN ID (from 2 to 4094):"), shot="id")
        s.keys("1", literal=True)
        s.keys("Enter")
        s.wait(tr("VLAN ID must be between {min} and {max}", min=2, max=4094), shot="rule")
        s.keys("Enter")
        s.wait(tr("VLAN ID (from 2 to 4094):"))
        s.clear_field()
        s.keys("10", literal=True)
        s.keys("Enter")
        s.wait(tr("Put the management on VLAN {id}?\n\nAfter applying, the device will answer "
                  "at {ip} only with VLAN {id} tagged: the computer or the switch port you "
                  "manage it from must use that VLAN, or you will lose access to it. The rest "
                  "of the traffic goes on as before, without a VLAN.\n\nApply it with \"Apply "
                  "changes\" → \"Test\": if you cannot reach it, it goes back by itself.")
               .partition("\n")[0].format(id=10), shot="warning")
        s.keys("Left")
        s.keys("Enter")                     # "Yes" (focus on "No")
        s.wait("* " + tr("Management VLAN: {state}", state="VLAN 10"), shot="network-marked")
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=pending),
               shot="pending")
        s.keys("0")
        s.keys("Enter")
        s.wait(tr("There are {count} unapplied change(s) that will be lost.\nExit anyway?")
               .partition("\n")[2])
        s.keys("Left")
        s.keys("Enter")   # "Yes" (focus on "No")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_router():
    print("=== e2e Network (airOS 8, router, advanced configuration): port forwarding and "
          "leases ===")
    port = 18226
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60", "--mode", "router",
                            "--advanced"], stdout=subprocess.DEVNULL)
    ready(sim)
    base = T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60, mode="router").running))
    pending = len(T.plan_port_forwarding(base, False, [dict(
        T.new_port_forward(base), host="192.168.1.10", dport="8080")]))
    s = Session("e2erouter", port)
    try:
        s.wait(tr("Network: mode, WAN (static, DHCP or PPPoE), LAN"), shot="menu")
        s.keys("3")
        s.keys("Enter")
        s.wait(tr("IPv6 firewall: disabled, {count} rule(s)", count=0), shot="network")
        s.keys("r")
        s.keys("Enter")
        s.wait(tr("Add a rule"), shot="forwarding")
        s.keys("a")
        s.keys("Enter")
        s.wait(f"{tr('Private IP')}: —", shot="rule")
        s.keys("h")
        s.keys("Enter")
        s.wait(tr("The IP of the LAN device it goes to:"))
        s.keys("192.168.1.10", literal=True)
        s.keys("Enter")
        s.wait(f"{tr('Private IP')}: 192.168.1.10")
        s.keys("q")
        s.keys("Enter")
        s.wait(tr("The public port (a range as 1000:2000):"))
        s.keys("8080", literal=True)
        s.keys("Enter")
        s.wait(f"{tr('Public Port')}: 8080", shot="rule-done")
        s.keys("o")
        s.keys("Enter")
        s.wait("* #1 LAN0  TCP  :8080 → 192.168.1.10", shot="list")
        s.keys("Escape")
        s.wait("* " + tr("Port forwarding: disabled, {count} rule(s)", count=1),
               shot="network-marked")
        s.keys("l")
        s.keys("Enter")
        s.wait(tr("Leases given (on the device)"), shot="lan")
        s.keys("c")
        s.keys("Enter")
        s.wait("laptop-north", shot="leases")
        s.keys("Enter")
        s.wait(tr("DHCP server: {state}", state=tr("enabled, {start}–{end}", start="192.168.1.2",
                                                   end="192.168.1.254")))
        s.keys("Escape")
        s.wait(tr("Network mode: {mode}", mode=tr("Router")))
        s.keys("Escape")
        s.wait(tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                  address=f"http://127.0.0.1:{port}", user="ubnt", count=pending),
               shot="pending")
        s.keys("0")
        s.keys("Enter")
        s.wait(tr("There are {count} unapplied change(s) that will be lost.\nExit anyway?")
               .partition("\n")[2])
        s.keys("Left")
        s.keys("Enter")   # "Yes" (focus on "No")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()
        sim.wait(timeout=10)


def scenario_stations():
    print("=== e2e stations (airOS 8 AP): view a station and kick it ===")
    port = 18218
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60"], stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2esta", port)
    try:
        s.wait(tr("Stations ({count}): their data and kicking them", count=1), shot="menu")
        s.keys("e")
        s.keys("Enter")
        listing = s.wait(tr("Kick All"), shot="list")
        check("e2e stations: the list with the station", "Client-North-01" in listing, listing)
        s.keys("1")
        s.keys("Enter")
        details = s.wait(tr("Link Potential"), shot="details")
        check("e2e stations: its data, with the throughput",
              on_screen(tr("Throughput RX"), details) and "LiteBeam 5AC" in details, details)
        s.keys("Enter")                                     # "Kick"
        s.wait(tr("Kick {name}?", name="Client-North-01"), shot="question")
        s.keys("Left")
        s.keys("Enter")                     # "Yes" (focus on "No")
        s.wait(tr("Sent: it is disconnected; it usually connects again in a few seconds."),
               shot="sent")
        s.keys("Enter")
        s.wait(tr("No station is connected."), shot="empty")
        s.keys("Escape")
        s.wait(tr("Device and link status"))
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
        sim.terminate()


def scenario_first_setup():
    print("=== e2e first setup (airOS 8 just reset): its web's wizard ===")
    port = 18242
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--first-setup"], stdout=subprocess.DEVNULL)
    ready(sim)
    good = "Secure-Key-2026x"
    cc = list(S.COUNTRIES)[1]                       # (the second of its list)
    s = Session("e2efirst", port, tui_args=f"http://127.0.0.1:{port} -u Operator --wait 3")
    try:
        first = s.wait(tr("The device waits for its first setup, as its web asks for it on the "
                          "first access after a reset.\n\nDevice Country:").rpartition("\n")[2],
                       shot="country")
        check("e2e first setup: instead of the user and password, its setup: the country",
              on_screen(tr("Please Set Up Your Device"), first)
              and not on_screen(tr("Password of {user} on {target}:").partition(" {")[0], first),
              first)
        s.keys("Down")
        s.keys("Enter")
        s.wait(tr("Language of its web:"), shot="language")
        s.keys("Enter")
        terms = s.wait(tr("TERMS OF USE"), shot="terms")
        check("e2e first setup: its terms, to accept them", "(simulated terms of use)" in terms
              and on_screen(tr("I agree"), terms), terms)
        s.keys("Enter")                                 # "I agree"
        ways = s.wait(tr("Upload Backup Configuration"), shot="way")
        check("e2e first setup: as its web, a new user and password, or a backup",
              on_screen(tr("Create Your Username and Password"), ways), ways)
        s.keys("Enter")                                 # (the new user: the first one)
        s.wait(tr("Choose your username:"), shot="user")
        s.keys("Enter")   # (Operator)
        s.wait(tr("Choose a strong password:"), shot="password")
        s.keys(good, literal=True)
        s.keys("Enter")
        s.wait(tr("Confirm New Password:"))
        s.keys(good, literal=True)
        s.keys("Enter")
        menu = s.wait(tr("Device and link status"), timeout=30, shot="menu")
        check("e2e first setup: set up, it logs in with the new user", "Operator" in menu, menu)
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "Operator", good)
        dev.connect()
        cfg = T.Config(dev.get_config())
        check("e2e first setup: the device was left with its country, its user and its password",
              cfg.get("radio.countrycode") == str(cc) and cfg.get("users.1.name") == "Operator",
              str((cfg.get("radio.countrycode"), cfg.get("users.1.name"))))
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_emergency():
    print("=== e2e warning at start (airOS 8): rebooted by the ping watchdog ===")
    port = 18219
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "1", "--test-time", "60", "--emergency", "0"],
                           stdout=subprocess.DEVNULL)
    ready(sim)
    s = Session("e2ewarning", port)
    try:
        warning = s.wait(tr("Device was rebooted by Ping Watchdog."), shot="warning")
        check("e2e warning: its web's, with its two buttons, before anything else",
              on_screen(tr("Warning"), warning) and on_screen(tr("Download Support Info"), warning)
              and on_screen(tr("Dismiss"), warning) and on_screen(tr("Later"), warning), warning)
        s.keys("d")
        s.keys("Enter")                        # "Dismiss"
        s.wait(tr("Device and link status"), shot="menu")
        s.keys("0")
        s.keys("Enter")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        dev.connect()
        check("e2e warning: dismissed on the device", dev.warnings().get("emergReasonCode") == -1)
    finally:
        sim.terminate()
        sim.wait(timeout=10)


def scenario_keys():
    print("=== e2e keys: a live screen goes back with the key even if it arrives while it reads "
          "===")
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_watch_keys.py")
    for mode, how in (("", "with curses"), ("dialog", "with a dialog per refresh, if curses "
                                                      "cannot start")):
        s = Session("e2ekeys" + mode, 0, tui_args=mode, program=helper)
        try:
            for n in (1, 2, 3):
                s.wait(f"Round {n}: read 2", timeout=15, shot=f"{mode or 'curses'}-{n}")
                time.sleep(0.15)                   # (almost surely in the middle of a read: 0.4 s)
                s.keys("x")
            end = s.wait("THREE ROUNDS", timeout=10)
            check(f"e2e keys, {how}: each live screen goes back with its key, even if it arrives "
                  "while it reads", "THREE ROUNDS" in end, end)
            s.wait("EXIT=0", timeout=10)
        finally:
            s.close()


# The option wider than an 80-column menu that e2e_screens.py long shows
LONG_OPTION = "Option " + "wide " * 13 + "END"


def scenario_small():
    print("=== e2e small: in small windows the screens fit: an option too wide cut with … (whole "
          "on the last line), a text too tall cut (whole behind ?), the list's ⚠ ===")
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_screens.py")
    backtitle = tr("airos-tui · Ubiquiti airMAX antennas over HTTP")
    marked = tr("⚠ = it waits for its first setup.")
    s = Session("e2esmall1", 0, tui_args="empty", program=helper, size=(60, 20))
    try:
        cut = tr("… (the rest: option {tag})", tag="?")
        screen = s.wait(cut, shot="60x20")
        rows = screen.splitlines()

        def row(text):
            return next((i for i, x in enumerate(rows) if text in x), -1)
        check("e2e small, 60×20: a text taller than the window, cut, with an option that shows it "
              "whole; the list keeps its rows, above the buttons (no frame over another); no "
              "backtitle (the box covers its rows)",
              on_screen(tr("Read the whole text above"), screen)
              and 0 <= row(tr("Search again")) < row(tr("Back")) - 2
              and backtitle not in screen, screen)
        s.keys("?")
        s.keys("Enter")
        whole = s.wait(tr(T.ACTIVE_HINT, active="a"), shot="60x20-whole")
        check("  that option: the whole text, to its end",
              on_screen(tr(T.ACTIVE_HINT, active="a"), whole), whole)
        s.keys("Enter")
        s.wait(cut)
        s.keys("Escape")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    s = Session("e2esmall2", 0, tui_args="one", program=helper, size=(80, 24))
    try:
        screen = s.wait(marked, shot="80x24")
        check("e2e small, 80×24: the antenna that waits for its first setup, with ⚠ after its IP "
              "(explained above), and its options whole",
              "10.20.0.5       ⚠ " in screen and "…" not in screen
              and on_screen(tr("Search at an address: an IP, a range (10.3.0.0/24) or a "
                               "broadcast"), screen)
              and on_screen(tr("Active search: each address's web (even with discovery off)"),
                            screen), screen)
        s.keys("Escape")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    s = Session("e2esmall3", 0, tui_args="long", program=helper, size=(80, 24))
    try:
        screen = s.wait("Short option", shot="80x24-long")
        option = next((x for x in screen.splitlines() if "2  Option" in x), "")
        check("e2e small, 80×24: an option wider than the menu, cut with …",
              "…" in option and "END" not in option, screen)
        s.keys("Down")
        screen = s.screen()
        whole = [x for x in screen.splitlines() if LONG_OPTION in x]
        check("  and whole on the window's last line while it is highlighted",
              whole and "│" not in whole[0], screen)
        s.keys("Escape")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()
    s = Session("e2esmall4", 0, tui_args="one", program=helper, size=(110, 34))
    try:
        screen = s.wait(marked, shot="110x34")
        check("e2e small, 110×34 (the other scenarios' size): the backtitle, above the box",
              backtitle in screen and screen.index(backtitle) < screen.index(tr("Discovery")),
              screen)
        s.keys("Escape")
        s.wait("EXIT=0", timeout=10)
    finally:
        s.close()


def gone(pid, timeout=8.0):
    """Whether process pid ended within timeout seconds."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    return False


def scenario_signals():
    print("=== e2e: its terminal closes (SIGHUP) or it is terminated (SIGTERM): it ends cleanly "
          "===")
    port = 18240
    sim = subprocess.Popen([sys.executable, SIM, "--airos", "8", "--port", str(port),
                            "--restart", "2"], stdout=subprocess.DEVNULL)
    ready(sim)
    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            name = sig.name
            s = Session("e2e" + name[3:].lower(), port)
            try:
                s.wait(tr("Device and link status"), shot="menu")
                shell = tmux("display-message", "-p", "-t", s.pane, "#{pane_pid}").strip()
                tui = subprocess.run(["pgrep", "-P", shell, "-f", "airos_tui"],
                                     capture_output=True, text=True).stdout.split()
                files = screen_files()
                check(f"{name}: with its menu open, what dialog shows is in a file of its own",
                      len(files) == 1 and len(tui) == 1, str((files, tui)))
                if len(tui) != 1:
                    continue
                pid = int(tui[0])
                if sig == signal.SIGTERM:
                    os.kill(pid, sig)
                    s.wait("EXIT=143", shot="terminated")
                    mouse = tmux("display-message", "-p", "-t", s.pane,
                                 "#{mouse_any_flag}").strip()
                    check("SIGTERM: it exits (143) and dialog leaves the terminal as it was "
                          "(without the mouse it had turned on)", mouse == "0", mouse)
                else:
                    s.close()                    # (its terminal closes: SIGHUP)
                check(f"{name}: the program ends and does not leave the file behind", gone(pid)
                      and not screen_files(), str(screen_files()))
            finally:
                s.close()
    finally:
        sim.terminate()


def main():
    for f in os.listdir(SHOTS):
        os.unlink(os.path.join(SHOTS, f))
    for f in screen_files():                       # (of a run cut half way)
        os.unlink(f)
    scenarios = {"v8": scenario_v8, "v6": scenario_v6, "power": scenario_power,
                 "network": scenario_network, "pppoe": scenario_pppoe,
                 "netmode": scenario_netmode, "discovery": scenario_discovery,
                 "english": scenario_english, "services": scenario_services,
                 "access": scenario_access, "system": scenario_system,
                 "graphs": scenario_graphs, "advnet": scenario_advnet,
                 "airview": scenario_airview, "wireless": scenario_wireless,
                 "tools": scenario_tools, "maintenance": scenario_maintenance,
                 "firmware": scenario_firmware, "fwcheck": scenario_fw_check,
                 "ping": scenario_net_tools, "date": scenario_date, "vlan": scenario_mgmt_vlan,
                 "router": scenario_router, "stations": scenario_stations,
                 "emergency": scenario_emergency, "firstsetup": scenario_first_setup,
                 "keys": scenario_keys, "signals": scenario_signals,
                 "small": scenario_small}
    assert sorted(scenarios) == sorted(ORDER), "ORDER and the scenarios differ"
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")] or ORDER
    unknown = [name for name in wanted if name not in scenarios]
    if unknown:
        sys.exit(f"e2e: no such scenario: {', '.join(unknown)}  (they are: {' '.join(ORDER)})")
    for name in wanted:
        scenarios[name]()


def socket_file() -> str:
    """Where this tmux server has its socket (which it leaves behind when it ends)."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}",
                        TMUX_SOCKET)


if __name__ == "__main__":
    tmux("kill-server")                  # (what a run cut half way left: only this server's)
    try:
        main()
    finally:
        tmux("kill-server")
        with contextlib.suppress(OSError):
            os.unlink(socket_file())
    end = time.time() + 5                        # (each screen still open ends on its SIGHUP)
    while screen_files() and time.time() < end:
        time.sleep(0.2)
    check("e2e: no screen left its dialog file behind", not screen_files(),
          str(screen_files()))
    print("FAILURES:", fails if fails else "none")
    sys.exit(1 if fails else 0)
