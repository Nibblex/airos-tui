"""Startup: the command line, the connection screen and --discover."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading
import time

from . import __version__
from .core.i18n import (current_language, detect_language, LANGUAGES, LOCALE_DIR, N_, set_language,
                        tr)
from .core.errors import AirOSError, AuthError, SetupRefused
from .core.config import DATA_DIR
from .core.debuglog import DEBUG_FILE, start_debug, stop_debug
from .device.client import AirOS, setup_steps
from .device.discovery import (ACTIVE_PORTS, active_scan, airos_only, BROADCAST, discover,
                               discovery_host, discovery_json, discovery_table, listen_neighbors,
                               local_ipv4, merge_active, merge_neighbors, NEIGHBOR_LISTEN,
                               neighbor_sockets, parse_ports, scan_addresses, scan_ranges,
                               target_problem)
from .device.system import (backup_kind, DEFAULT_PASSWORD, DEFAULT_USER, password_problem,
                            SETUP_FIELD_MAX, setup_username_problem, v6_password_problem,
                            v6_username_problem)
from .screens.ui import UI
from .screens.discovery import ACTIVE_HINT, discovery_screen, DISCOVERY_SECONDS, NOBODY_HINT, _where
from .screens.system import BACKUP_MAX, pick_backup, read_local_file
from .screens.app import App


def cli_discover(args) -> int:
    """--discover: the list of antennas in the terminal (or as JSON), without logging into
    any; with --active-scan, also what the active search finds."""
    given = args.target or args.device or BROADCAST
    target = discovery_host(given) or given.strip()        # (IP:port or a URL: its host)
    where = _where(target)
    active = bool(getattr(args, "active_scan", False))
    ports = getattr(args, "ports", None) or list(ACTIVE_PORTS)
    if active:                         # (first what the active search says: it asks more)
        try:
            ifaces = local_ipv4()
            addresses = scan_addresses(scan_ranges(target, ifaces)[0], ifaces)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
    problem = target_problem(target)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    listen = getattr(args, "listen", None)
    listen = max(0.0, listen if listen is not None else
                 NEIGHBOR_LISTEN if target == BROADCAST else 0.0)
    socks, ifaces = [], []
    if listen:
        try:
            socks, ifaces = neighbor_sockets()
        except PermissionError:
            print(tr("CDP and LLDP were not listened for: that needs root (sudo) or the "
                     "CAP_NET_RAW capability. Only Ubiquiti discovery was used."),
                  file=sys.stderr)
        except (OSError, AttributeError) as e:
            print(tr("CDP and LLDP were not listened for ({error}). Only Ubiquiti discovery "
                     "was used.", error=e), file=sys.stderr)
    if not args.json:
        print(tr("Looking for Ubiquiti antennas on {where} ({seconds} s)…", where=where,
                 seconds=f"{args.timeout:g}"), file=sys.stderr)
        if active:
            print(tr("Active search: asking {count} addresses for their web (ports {ports}); "
                     "Ctrl+C to stop earlier…", count=len(addresses),
                     ports=", ".join(str(p) for p in ports)), file=sys.stderr)
        if socks:
            print(tr("Listening for CDP and LLDP announcements on {ifaces} for {seconds} s "
                     "(they are sent about once a minute; Ctrl+C to stop earlier)…",
                     ifaces=", ".join(ifaces) or "-", seconds=f"{listen:g}"), file=sys.stderr)
    # Both searches in threads, and this one waiting: Ctrl+C reaches it, stops them, and what
    # they found until then is shown.
    stop, heard, asked = threading.Event(), [], {}

    def ask():
        try:
            asked["found"] = discover(args.timeout, target=target, stop=stop)
        except Exception as e:                 # (shown here, as before: not in its thread)
            asked["error"] = e
    def scan():
        try:
            asked["active"] = active_scan(target, ports, stop=stop)
        except Exception as e:                 # (shown here, as before: not in its thread)
            asked["error"] = e
    workers = [threading.Thread(target=ask, daemon=True)]
    if active:
        workers.append(threading.Thread(target=scan, daemon=True))
    if socks:
        workers.append(threading.Thread(
            target=lambda: heard.extend(listen_neighbors(listen, socks, stop)), daemon=True))
    for w in workers:
        w.start()
    try:
        while any(w.is_alive() for w in workers):
            for w in workers:
                w.join(0.2)
    except KeyboardInterrupt:
        stop.set()
        for w in workers:
            w.join(2)
        print(tr("Interrupted: showing what was found so far."), file=sys.stderr)
    if "error" in asked:
        raise asked["error"]
    found = merge_neighbors(asked.get("found", []), heard)
    if active:
        scanned, notes = asked.get("active", ([], []))
        found = merge_active(found, scanned)
        for note in notes:
            print(note, file=sys.stderr)
    found = airos_only(found)                  # (only airOS antennas: not what else answered)
    if args.json:
        print(json.dumps([discovery_json(d) for d in found], ensure_ascii=False, indent=2))
    elif found:
        print(discovery_table(found))
        print("\n" + tr("{count} device(s).", count=len(found)), file=sys.stderr)
    else:
        print(tr("No airOS antenna answered on {where}.", where=where) + "\n" + tr(NOBODY_HINT),
              file=sys.stderr)
        if not active:
            print(tr(ACTIVE_HINT, active="--active-scan"), file=sys.stderr)
        if socks and not heard:
            print(tr("No CDP or LLDP announcement was heard either."), file=sys.stderr)
    return 0 if found else 1


LFUA_NOTICE = N_(
    'You have selected a "licensed" country option in the airOS firmware. Please note that your '
    "use of the licensed firmware is subject to your compliance at all times with the terms and "
    "restrictions of the Licensed Firmware User Agreement (LFUA), including having received "
    "valid authorization from applicable governmental authorities to configure and use Ubiquiti "
    "products beyond their standard radio frequency settings. Ubiquiti reserves the right to "
    "request such information and records as may be reasonably necessary to confirm your "
    "compliance with the LFUA.")


SETUP_BACKUP_MASKED = N_(
    'That backup was saved by airos-tui without the passwords (as "-"): set up from it, the '
    'device would take "-" as its password and nobody could log in. Use one made by its web '
    "(Back Up Configuration), as System → backups saves them.")


def setup_backup_screen(ui: UI) -> str | None:
    """Its web's "Upload Backup Configuration" on its first access (airOS 8): a backup of this
    computer, chosen as in System → backups, read as its web reads it (as text). One made by
    its web (Back Up Configuration, with the password hashes); not one airos-tui saved without
    them, where its web would send "-" as the password. None if cancelled."""
    title = tr("Upload Backup Configuration")
    while True:
        path = pick_backup(ui, title)
        if path is None:
            return None
        data = read_local_file(ui, path, BACKUP_MAX)
        if data is None:
            continue
        text = data.decode("utf-8-sig", "replace")     # (its FileReader: without a BOM)
        kind = backup_kind(text)
        if kind == "web":
            return text
        ui.msgbox(tr("Error"), tr("That file does not look like an airOS system.cfg.")
                  if kind is None else tr(SETUP_BACKUP_MASKED))


def _setup_way(ui: UI, title: str) -> str | None:
    """Its web's two ways while it has the factory password (once the country and its terms
    are chosen, or as it asks for the new user when its setup is done): a new username and
    password ("" here) or "Upload Backup Configuration" (the backup's text). None if
    cancelled."""
    items = [("new", tr("Create Your Username and Password")),
             ("backup", tr("Upload Backup Configuration"))]
    while True:
        way = ui.menu(title, tr("Set it up with a new username and password, or from a backup "
                                "of its configuration: then its user and password are the "
                                "backup's, once applied."), items, ok=tr("Choose"),
                      cancel=tr("Cancel"))
        if way is None:
            return None
        if way == "new":
            return ""
        backup = setup_backup_screen(ui)
        if backup is not None:
            return backup


def first_setup_screens(ui: UI, info: dict, first: bool, new_admin: bool,
                        user: str | None) -> dict | None:
    """Its web's first access (airOS 8, after a reset): "Please Set Up Your Device" (the
    country, the language of its web and its terms of use, with its notice for a "licensed"
    country) while its setup is not done, and "Create Your Username and Password" while it
    has the factory ones, with its web's rules. While it has them, its web also offers to set
    it up from a backup instead ("Upload Backup Configuration"): the device keeps it waiting
    to be applied, and once applied its user and password are the backup's. The fields of its
    login (AirOS.first_setup), or None if cancelled."""
    fields: dict[str, str] = {}
    if first:
        title = tr("Please Set Up Your Device")
        setup = info.get("setup_data") or {}
        countries = [(str(c["code"]), str(c.get("name") or c["code"]))
                     for c in setup.get("rd_countries") or []
                     if isinstance(c, dict) and c.get("code") is not None]
        if not countries:
            ui.msgbox(title, tr("The device did not give its list of countries."))
            return None
        cc = ui.menu(title, tr("The device waits for its first setup, as its web asks for it "
                               "on the first access after a reset.\n\nDevice Country:"),
                     countries, ok=tr("Choose"), cancel=tr("Cancel"))
        if cc is None:
            return None
        lang = str(info.get("ui_lang") or "en_US")
        langs = [(str(x["code"]), str(x.get("lang") or x["code"]))
                 for x in info.get("ui_langs") or [] if isinstance(x, dict) and x.get("code")]
        if langs:
            lang = ui.menu(title, tr("Language of its web:"), langs, default=lang,
                           ok=tr("Choose"), cancel=tr("Cancel"))
            if lang is None:
                return None
        terms = [tr("TERMS OF USE"), "", str(setup.get("license_agreement") or "")]
        if "licensed" in dict(countries)[cc].lower():
            terms += ["", tr(LFUA_NOTICE)]
        terms += ["", tr("By setting it up, you agree to be bound by the terms and conditions "
                         "of Ubiquiti's End User License Agreement.")]
        if not ui.ask(title, "\n".join(terms), yes=tr("I agree"), no=tr("Cancel")):
            return None
        fields.update(device_setup="first", new_ccode=cc, new_lang=lang)
    if new_admin:
        # (its web offers the backup on its first screen, or on this one when it is the first)
        backup = _setup_way(ui, tr("Please Set Up Your Device") if first
                            else tr("Create Your Username and Password"))
        if backup is None:
            return None
        if backup:
            fields["new_config"] = backup
            fields.setdefault("device_setup", "cfg")
            return fields
        title = tr("Create Your Username and Password")
        name = user or ""
        while True:
            name = ui.inputbox(title, tr("Choose your username:"), name)
            if name is None:
                return None
            err = setup_username_problem(name)
            if err is None:
                break
            ui.msgbox(tr("Invalid value"), err)
        while True:
            pwd = ui.passwordbox(title, tr("Choose a strong password:"))
            if pwd is None:
                return None
            err = password_problem(pwd, longest=SETUP_FIELD_MAX)
            if err:
                ui.msgbox(tr("Invalid value"), err)
                continue
            again = ui.passwordbox(title, tr("Confirm New Password:"))
            if again is None:
                return None
            if again == pwd:
                break
            ui.msgbox(tr("Invalid value"), tr("Passwords should match."))
        fields.update(new_username=name, new_password=pwd)
        fields.setdefault("device_setup", "user")
    return fields


V6_LICENSED = ("511", "2124")      # its handle_licensed(): the countries with the LFUA


def v6_first_login_screens(ui: UI, setup: dict, user: str | None) -> dict | None:
    """Its web's first login (airOS 6.3, login.cgi): on its first boot, the country (unless
    its board fixes it), the language of its web and its terms; while it has the factory
    password, a new one, twice, with its page's rules (pstrength.js; not ubnt), and the
    administrator's name (a new one while it is ubnt). The fields of its form, or None if
    cancelled. (Without a new password, its user and password are asked as always.)"""
    fields: dict[str, str] = {}
    if setup["first"]:
        title = tr("Please Set Up Your Device")
        if setup["countries"]:
            cc = ui.menu(title, tr("The device waits for its first setup, as its web asks for it "
                                   "on the first access after a reset.\n\nDevice Country:"),
                         setup["countries"], default=setup["country"] or None, ok=tr("Choose"),
                         cancel=tr("Cancel"))
            if cc is None:
                return None
            fields["country"] = cc
        lang = setup["language"]
        if setup["languages"]:
            lang = ui.menu(title, tr("Language of its web:"), setup["languages"], default=lang,
                           ok=tr("Choose"), cancel=tr("Cancel"))
            if lang is None:
                return None
        fields["ui_language"] = lang
        terms = [tr("TERMS OF USE"), "", setup["terms"]]
        if fields.get("country") in V6_LICENSED:
            terms += ["", tr(LFUA_NOTICE)]
        terms += ["", tr("I have read and agree to the TERMS OF USE, EULA and PRIVACY POLICY.")]
        if not ui.ask(title, "\n".join(terms), yes=tr("I agree"), no=tr("Cancel")):
            return None
    if setup["new_password"]:
        title = tr("Create Your Username and Password")
        label = tr("Administrator User Name:") if setup["new_username"] else tr("User:")
        name = user or DEFAULT_USER
        while True:
            name = ui.inputbox(title, label, name)
            if name is None:
                return None
            err = v6_username_problem(name)
            if err is None:
                break
            ui.msgbox(tr("Invalid value"), err)
        while True:
            pwd = ui.passwordbox(title, tr("New Password:"))
            if pwd is None:
                return None
            err = v6_password_problem(pwd, name)
            if err:
                ui.msgbox(tr("Invalid value"), err)
                continue
            again = ui.passwordbox(title, tr("Verify New Password:"))
            if again is None:
                return None
            if again == pwd:
                break
            ui.msgbox(tr("Invalid value"), tr("Passwords should match."))
        fields.update(username=name, password=pwd, password2=again)
    return fields


RECENT_FILE = os.path.join(DATA_DIR, "recent.json")
RECENT_MAX = 10


def recent_devices() -> list[dict]:
    """The devices connected to lately, the newest first: {"target": its address, "user"}
    (never a password)."""
    try:
        with open(RECENT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [{"target": d["target"].strip(), "user": str(d.get("user") or "")}
            for d in (data if isinstance(data, list) else [])
            if isinstance(d, dict) and isinstance(d.get("target"), str)
            and d["target"].strip()][:RECENT_MAX]


def device_address(dev: AirOS) -> str:
    """How a device is written to come back to it: its host, and its port if it is not the
    usual one; without its protocol, so that it is found out again (and, if only its HTTP
    answers, asked again before sending the password unencrypted)."""
    host = f"[{dev.host}]" if ":" in dev.host else dev.host
    return host if dev.port == (443 if dev.scheme == "https" else 80) else f"{host}:{dev.port}"


def remember_device(dev: AirOS) -> None:
    """First among the recent devices: its address and its user, in a file only for you
    (0600). If it cannot be written, nothing is said: the address is asked as before."""
    address = device_address(dev)
    items = [d for d in recent_devices() if d["target"] != address]
    items.insert(0, {"target": address, "user": dev.username or ""})
    tmp = RECENT_FILE + ".new"
    try:
        os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items[:RECENT_MAX], f, ensure_ascii=False, indent=1)
        os.replace(tmp, RECENT_FILE)
    except OSError:
        pass


def forget_devices() -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(RECENT_FILE)


def ask_target(ui: UI, args, recent: list[dict], last_target: str) -> tuple | None:
    """The address to connect to: one of the recent devices (with its user), another one
    typed, or one found on the network with no user or password. (address, the recent one's
    user or None), or None to exit. recent: what is offered (emptied if forgotten)."""
    while True:
        way = "n"
        if recent:
            items = [(str(i), r["target"] + (f"  ({r['user']})" if r["user"] else ""))
                     for i, r in enumerate(recent, 1)]
            items += [("n", tr("Another address…")),
                      ("s", tr("Search for antennas on your network (no user or password)")),
                      ("f", tr("Forget these devices"))]
            way = ui.menu(tr("Connect"), tr("Devices you connected to lately (their address and "
                                            "user; never a password):"), items,
                          ok=tr("Connect"), cancel=tr("Exit"))
            if way is None:
                return None
            if way == "f":
                if ui.ask(tr("Connect"), tr("Forget the {count} recent device(s)? ({path} is "
                                            "removed.)", count=len(recent), path=RECENT_FILE),
                          default_no=True):
                    forget_devices()
                    recent.clear()
                continue
            if way not in ("n", "s"):
                picked = recent[int(way) - 1]
                return picked["target"], picked["user"] or None
        if way == "n":
            typed = ui.inputbox(tr("Connect"), tr("IP or name of the device (http://IP:port or "
                                                  "https://IP work too).\nLeave it empty to "
                                                  "search for antennas on your network, with no "
                                                  "user or password:"), last_target)
            if typed is None:
                if recent:
                    continue                     # (back to the recent ones)
                return None
            if typed.strip():
                return typed.strip(), None
        found = discovery_screen(ui, getattr(args, "target", None) or BROADCAST,
                                 ports=getattr(args, "ports", None) or ACTIVE_PORTS)
        if found:
            return found, None


def _connect_after_setup(ui: UI, dev: AirOS, wait: float = 90) -> None:
    """Once set up, the device applies it (its country: a quick restart of its services), and
    its web may not answer for a moment: the program waits for it, as it does elsewhere."""
    end = time.time() + wait
    while True:
        try:
            dev.connect()
            return
        except AuthError:
            raise
        except AirOSError:
            if time.time() > end:
                raise
            ui.infobox(tr("Please Set Up Your Device"),
                       tr("The device applies its first setup; waiting for it…"))
            time.sleep(3)


def connect_screen(ui: UI, args, user_default: str = "ubnt") -> AirOS | None:
    target, user = args.device, args.user
    password = os.environ.get("AIROS_PASSWORD")
    recent = recent_devices()
    last_target = target or (recent[0]["target"] if recent else "192.168.1.20")
    scheme = "http" if args.http else "https" if args.https else None
    while True:
        if not target:
            picked = ask_target(ui, args, recent, last_target)
            if picked is None:
                return None
            target, recent_user = picked
            if recent_user and not args.user:    # (a recent device: its user, unless -u)
                user = recent_user
            last_target = target
        # As its web, before any user or password: where it answers and (airOS 8) whether
        # it waits for its first setup, after a reset.
        try:
            dev = AirOS(target, user or "", password or "", scheme=scheme)
            ui.infobox(tr("Connecting"), tr("Connecting to {target}…", target=target))
            dev.probe()
            if dev.http_fallback and not ui.ask(tr("Unencrypted connection"), tr(
                    "HTTPS does not answer at {target}, but its web does over HTTP, "
                    "unencrypted: your user and password would travel in clear, and anyone on "
                    "the way could read them.\n\nConnect over HTTP anyway?\n\n(With http:// "
                    "before the address, or --http, this is not asked.)", target=target),
                    default_no=True):
                target = None                    # (another address, or https:// with its port)
                continue
            info = dev.public_info()
            # (without airOS 8's: airOS 6's login page, on its first boot or with the factory
            # password, asks for its own)
            v6 = dev.v6_first_login_info() if info is None else None
        except AirOSError as e:
            ui.msgbox(tr("Connection error"), str(e))
            target = None
            continue
        first, new_admin = setup_steps(info)
        if v6:
            new_admin = v6["new_password"]
            setup = v6_first_login_screens(ui, v6, user)
        else:
            setup = first_setup_screens(ui, info, first, new_admin, user) \
                if first or new_admin else None
        if (v6 or first or new_admin) and setup is None:
            target = None                        # (cancelled: another address, or none)
            continue
        if new_admin and not v6:                 # (sent with the factory ones, as its web)
            dev.username, dev.password = DEFAULT_USER, DEFAULT_PASSWORD
        elif not new_admin:
            if not user:
                user = ui.inputbox(tr("Connect"), tr("User:"), user_default)
                if not user:
                    return None
            if password is None:
                password = ui.passwordbox(tr("Connect"), tr("Password of {user} on {target}:",
                                                            user=user, target=target))
                if password is None:
                    return None
            dev.username, dev.password = user, password
        try:
            if v6:
                if not new_admin:                # (its user and password, as always)
                    setup.update(username=user, password=password)
                ui.infobox(tr("Please Set Up Your Device"), tr("Setting up the device…"))
                dev.v6_first_login(setup)
                _connect_after_setup(ui, dev)
            elif setup:
                ui.infobox(tr("Please Set Up Your Device"), tr("Setting up the device…"))
                dev.first_setup(setup)
                _connect_after_setup(ui, dev)
            else:
                dev.connect()
            return dev
        except SetupRefused as e:                # (its web shows it, and its setup again)
            ui.msgbox(tr("Please Set Up Your Device"), str(e))
        except AuthError as e:
            ui.msgbox(tr("Could not log in"), str(e))
            # (the user may be what was wrong: asked again too, with the one typed)
            password, user_default, user = None, user, None
        except AirOSError as e:
            ui.msgbox(tr("Connection error"), str(e))
            target = None


# Its terminal closed (SIGHUP) or asked to end (SIGTERM).
ENDING_SIGNALS = (signal.SIGHUP, signal.SIGTERM)


def _end_on_signal(signum, frame):
    """It ends as with Ctrl+C, through its cleanups (the files with what dialog shows, which
    could be a WPA key; dialog itself, which gives the terminal back). A second one ends it at
    once."""
    for s in ENDING_SIGNALS:
        signal.signal(s, signal.SIG_DFL)
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def ending_signals():
    """While its screens are open, SIGHUP and SIGTERM end it through _end_on_signal; only
    where they are at their default (launched with nohup, a hang-up stays ignored)."""
    caught = {s: signal.signal(s, _end_on_signal) for s in ENDING_SIGNALS
              if signal.getsignal(s) == signal.SIG_DFL}
    try:
        yield
    finally:
        for s, handler in caught.items():
            signal.signal(s, handler)


def _wait_seconds(text: str) -> int:
    """--wait: from 0 to 120 s (with more, the wait for the device, of 180 s, would give up
    before looking for it)."""
    try:
        n = int(text)
    except ValueError:
        n = -1
    if not 0 <= n <= 120:
        raise argparse.ArgumentTypeError(tr("from 0 to 120 seconds"))
    return n


def _ports_arg(text: str) -> list[int]:
    """--ports: the web's ports the active search asks."""
    try:
        return parse_ports(text)
    except ValueError:
        raise argparse.ArgumentTypeError(tr("ports from 1 to 65535, separated by commas")
                                         ) from None


def _lang_arg(argv) -> str | None:
    """--lang LANG or --lang=LANG, read before parsing so that --help and the errors of the
    parser already come out in that language."""
    args = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(args):
        if a == "--":
            break
        if a == "--lang" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def main(argv=None) -> int:
    wanted = detect_language(_lang_arg(argv))
    if set_language(wanted) != wanted:
        print(f"airos-tui: no {wanted!r} translation in {LOCALE_DIR}; using English.",
              file=sys.stderr)
    ap = argparse.ArgumentParser(
        prog="airos-tui",                      # (the command it installs)
        description=tr("TUI to configure Ubiquiti airMAX antennas (airOS 8 and 6) over "
                       "HTTP/HTTPS."),
        epilog=tr("The password is asked on screen or taken from AIROS_PASSWORD."))
    ap.add_argument("device", nargs="?", metavar=tr("DEVICE"),
                    help=tr("IP or name of the device (also http://IP:port or https://IP)"))
    ap.add_argument("-u", "--user", metavar=tr("USER"),
                    help=tr("user (asked by default; it is usually ubnt)"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--http", action="store_true", help=tr("use HTTP without trying HTTPS"))
    g.add_argument("--https", action="store_true", help=tr("use HTTPS only"))
    ap.add_argument("--wait", type=_wait_seconds, metavar=tr("SEC"),
                    help=tr("seconds before checking whether the device came back after "
                            "applying (default 15 on airOS 8 and 20 on airOS 6)"))
    d = ap.add_argument_group(tr("discovery: the devices on the network (no user or password "
                                 "needed)"))
    d.add_argument("--discover", action="store_true",
                   help=tr("list the airOS antennas on the network (Ubiquiti discovery, CDP and "
                           "LLDP) and exit; the device, if you give it, is where to look"))
    d.add_argument("--target", metavar="IP",
                   help=tr("where to look: a specific IP (it gets there even with routers in "
                           "between), a range such as 10.3.0.0/24 (one of your networks, with "
                           "its broadcast; any other, address by address, up to a /22) or the "
                           "broadcast of one of your networks (only there); by default, all "
                           "your networks. It also applies to the discovery of the connection "
                           "screen and of Tools"))
    d.add_argument("--timeout", type=float, default=DISCOVERY_SECONDS, metavar=tr("SEC"),
                   help=tr("seconds asking with Ubiquiti discovery (default {seconds})",
                           seconds=f"{DISCOVERY_SECONDS:g}"))
    d.add_argument("--listen", type=float, metavar=tr("SEC"),
                   help=tr("seconds listening for the CDP and LLDP announcements, which cannot "
                           "be asked for: they arrive about once a minute and only from your "
                           "own link, and it needs root. Default: {seconds} when searching your "
                           "networks, 0 with a target", seconds=NEIGHBOR_LISTEN))
    d.add_argument("--active-scan", action="store_true",
                   help=tr("also search actively: each address of your networks (or of "
                           "--target, up to a /22) is asked for its web, without a user or "
                           "password; on your networks, ARP gives their MAC. It finds the "
                           "antennas even with discovery, CDP and LLDP off; it implies "
                           "--discover"))
    d.add_argument("--ports", type=_ports_arg, metavar=tr("PORTS"),
                   help=tr("the web's ports the active search asks (default {ports}); also "
                           "those the discovery screen proposes",
                           ports=",".join(str(p) for p in ACTIVE_PORTS)))
    d.add_argument("--json", action="store_true", help=tr("with --discover, the list as JSON"))
    ap.add_argument("--lang", choices=LANGUAGES,
                    help=tr("interface language (default: AIROS_LANG or the system locale)"))
    ap.add_argument("--debug", action="store_true",
                    help=tr("save what is sent to the device and what it answers in {path}, "
                            "only for you, with passwords, keys and session cookies hidden",
                            path=DEBUG_FILE))
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)
    if args.lang and args.lang != current_language():     # e.g. an abbreviated --la es
        set_language(args.lang)
    debug = None
    if args.debug:
        try:
            debug = start_debug()
        except OSError as e:
            print(tr("Could not save {path}: {error}", path=DEBUG_FILE, error=e.strerror or e),
                  file=sys.stderr)
            return 1
    try:
        if args.discover or args.active_scan:
            return cli_discover(args)
        return _run(args)
    finally:
        if debug:
            stop_debug()
            print(tr("What was sent to the device and what it answered: {path}", path=debug),
                  file=sys.stderr)


def _run(args) -> int:
    """The screens: the connection one and, connected, the device's (App)."""
    ui = UI()
    with ending_signals():
        try:
            dev = connect_screen(ui, args)
            while dev is not None:
                remember_device(dev)
                app = App(ui, dev, wait=args.wait)
                app.discovery_target, app.discovery_ports = args.target, args.ports
                app.run()
                remember_device(dev)             # (where it ended: it may have moved)
                if not app.next_target:
                    break
                # Tools → Discovery → Connect: that device, as if typed on the connection screen
                args.device = app.next_target
                dev = connect_screen(ui, args, user_default=dev.username)
        except KeyboardInterrupt:
            pass
        except AirOSError as e:
            ui.clear()
            print(tr("Error: {error}", error=e), file=sys.stderr)
            return 1
        ui.clear()
    return 0
