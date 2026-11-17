"""A real airOS 8: its firmware's web (WA 8.7.25, from Ubiquiti: its lighttpd and its Lua
API), emulated with QEMU (see emu8.py), against the program: its first setup after a reset
(with the connection screen, as its web), the session, the configuration as its getcfg.cgi
gives it (sorted, with the repeated keys), test mode (try, keep and discard), what is saved
without it, its channel tables, changing the country and the password, and the reset. No
radio or services: what needs them is not there (its status.cgi is a real one of an 8.7.25,
with this one's name). Skipped if something is missing (emu8.missing())."""
import os
import re
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "emu"))
import emu8  # noqa: E402

if os.environ.get("AIROS_EMU8") != "1":            # in namespaces of its own (no root)
    lack = emu8.missing()
    if lack:
        print("skipped: missing " + "; ".join(lack))
        print("FAILURES: none")
        sys.exit(0)
    os.environ["AIROS_EMU8"] = "1"
    os.execvp("unshare", ["unshare", "-rnmpf", "--kill-child", "--mount-proc",
                          sys.executable, os.path.abspath(__file__)])
from _common import check, fails, FakeUI, settle, suite, WORK  # noqa: E402
T, S, XDG = suite("emu8")
tr = T.tr
URL = f"https://{emu8.HOST}"
USER, PASSWORD = "Operator", "Safe-Passkey-2026x"
os.environ.pop("AIROS_PASSWORD", None)
root = emu8.prepare(os.path.join(WORK, "emu8-root"))
web = emu8.start(root)


def on_device(rel):
    """A configuration file of the emulated device (tmp/system.cfg, tmp/running.cfg or
    etc/persistent/flash.cfg, its "flash")."""
    return T.Config(open(os.path.join(root, rel), encoding="utf-8").read(), "v8")


def everywhere(key):
    """Its value in use (system.cfg, running.cfg) and "in flash"."""
    emu8.settle(root)
    return tuple(on_device(rel).get(key) for rel in ("tmp/system.cfg", "tmp/running.cfg",
                                                     "etc/persistent/flash.cfg"))


def shape(x, depth=0):
    """What an answer is like: its keys and the types of its values (two levels)."""
    if isinstance(x, dict):
        return {k: shape(v, depth + 1) for k, v in sorted(x.items())} if depth < 2 else "dict"
    if isinstance(x, list):
        return [shape(x[0], depth + 1)] if x else []
    return type(x).__name__


def columns(line):
    """A line of its channel tables: the kind of each column (integer, decimal, letters)."""
    return ["int" if p.isdigit() else "dec" if p.replace(".", "", 1).isdigit() else "txt"
            for p in line.split()]


def login_error(user, password):
    try:
        T.AirOS(URL, user, password).connect()
    except T.AuthError as e:
        return str(e)
    return ""


try:
    print("=== before its login, as its web: it waits for its first setup ===")
    probe = T.AirOS(URL, "", "")
    probe.probe()
    info = probe.public_info() or {}
    setup = info.get("setup_data") or {}
    check("its public info: it waits for its setup (country, terms and a new user)",
          T.setup_steps(info) == (True, True) and info.get("product_name")
          == "NanoStation 5AC loco" and len(setup.get("rd_countries") or []) > 50
          and "professionally installed" in (setup.get("license_agreement") or ""),
          str({k: v for k, v in info.items() if k != "setup_data"})[:300])
    check("  and its API lets the factory user in meanwhile (its web never logs in like that)",
          login_error("ubnt", "ubnt") == "")

    print("=== the connection screen: its first setup, as its web ===")
    args = types.SimpleNamespace(device=URL, user=USER, http=False, https=False, target=None)
    ui = FakeUI([("menu", "724"), ("menu", "sp_SP"), ("yesno", 0), ("menu", "new"),
                 ("input", USER), ("password", PASSWORD), ("password", PASSWORD)])
    dev = T.connect_screen(ui, args)
    asked = [ti for k, ti, t in ui.log if k in ("menu", "yesno", "input", "password")]
    check("it asks for what its web asks for (not the usual user and password) and logs in "
          "with the new one", dev is not None and dev.username == USER and dev.flavor == "v8"
          and not ui.script and tr("Connect") not in asked, str((asked, ui.script)))
    check("  the device is set up: its country, its language and its administrator, in use and "
          "saved", everywhere("radio.countrycode") == ("724",) * 3
          and everywhere("gui.language") == ("sp_SP",) * 3
          and everywhere("users.1.name") == (USER,) * 3, str(everywhere("users.1.name")))
    check("  it no longer waits for it; the factory user no longer logs in, the new one does",
          T.setup_steps(probe.public_info()) == (False, False)
          and login_error("ubnt", "ubnt") and login_error(USER, PASSWORD) == "")

    print("=== the configuration, as its getcfg.cgi gives it ===")
    text = dev.get_config()
    lines = [x for x in text.split("\n") if x]
    check("sorted, and with the passwords masked (\"-\")", lines == sorted(lines)
          and T.Config(text).get("users.1.password") == "-", lines[:3])
    k = "resolv.host.1.name"
    saved = {rel: open(os.path.join(root, rel), encoding="utf-8").read()
             for rel in ("tmp/system.cfg", "tmp/running.cfg")}
    for rel in saved:                                   # (repeated, edited by hand)
        with open(os.path.join(root, rel), "a", encoding="utf-8") as f:
            f.write(f"{k}=Zeta\n{k}=Alpha\n")
    twice = T.Config(dev.get_config(), "v8")
    check("a repeated key: it gives both, sorted; the program reads the last one, as its web, "
          "and counts it among the repeated ones", twice.get(k) == "Zeta"
          and twice.repeated == {k: ["Alpha", "Zeta"]}, str((twice.get(k), twice.repeated)))
    for rel, body in saved.items():
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(body)

    print("=== test mode, as its web ===")
    cfg = T.Config(dev.get_config(), "v8")
    cfg.set(k, "EMU8-TEST")
    dev.write_config(cfg, test=True)
    info = dev.test_mode_info() or {}
    check("try: in use, not saved, with its countdown", info.get("active")
          and info.get("time_left", 0) > 100 and everywhere(k) == ("EMU8-TEST",) * 2 + (None,),
          str((info, everywhere(k))))
    dev.confirm_test()
    check("keep: saved, and out of test mode", everywhere(k) == ("EMU8-TEST",) * 3
          and not (dev.test_mode_info() or {}).get("active"), str(everywhere(k)))
    cfg = T.Config(dev.get_config(), "v8")
    cfg.set(k, "EMU8-OTHER")
    dev.write_config(cfg, test=True)
    dev.discard_test()
    check("discard: it goes back to what is saved and leaves test mode",
          everywhere(k) == ("EMU8-TEST",) * 3
          and not (dev.test_mode_info() or {}).get("active"), str(everywhere(k)))
    cfg = T.Config(dev.get_config(), "v8")
    cfg.set(k, "EMU8-DIRECT")
    dev.write_config(cfg, test=False)
    check("without test mode: in use and saved", everywhere(k) == ("EMU8-DIRECT",) * 3,
          str(everywhere(k)))

    print("=== its channel tables and the country ===")
    channels = dev.regdomain(724, 20) or []
    first = next((c for c in channels if c.bw == 20 and c.control == 5180), None)
    check("Spain (724): its channels, with its table's EIRP (23 dBm at 5180 MHz)",
          first is not None and first.eirp == 23.0, str(first))
    dev.set_country(32)
    check('changing the country (as its "Change" dialog does): in use and saved at once',
          everywhere("radio.countrycode") == ("32",) * 3, str(everywhere("radio.countrycode")))

    print("=== the password ===")
    new = "Other-Safe-Passkey-7"
    check("changing it: the new one logs in, not the old one", dev.change_password(new, PASSWORD)
          and login_error(USER, new) == "" and login_error(USER, PASSWORD), "")

    print("=== the simulator (the other tests' one) answers with the shape of its API ===")
    real = T.AirOS(URL, USER, new)
    real.connect()
    srv = S.make_server(8, 19556, restart=0.3, test_secs=60)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sim = T.AirOS("http://127.0.0.1:19556", "ubnt", "ubnt")
    sim.connect()

    def answer(d, path):
        status, _, data = d._req("GET", path)
        return status, shape(T._json(data))
    for path in ("/api/info/public", "/api/info/public?include_langs=false&force_setup_data=true",
                 "/api/warnings", "/test_mode.cgi", "/api/regdomain?cc=840",
                 "/api/dhcp-server/leases", "/api/pppoe/info"):
        a, b = answer(real, path), answer(sim, path)
        check(f"GET {path}", a == b, f"its API {a} · simulator {b}")
    check("its login (/api/auth): the same keys", sorted(real.login_info)
          == sorted(sim.login_info), str((sorted(real.login_info), sorted(sim.login_info))))
    lines = [T._json(d._req("GET", "/api/regdomain?cc=840")[2])["data"] for d in (real, sim)]
    check("  and the lines of their channel tables, the same columns", [columns(x[1])
          for x in lines] == [["int", "int", "int", "txt", "int", "dec", "int", "int", "int"]] * 2,
          str([x[1] for x in lines]))

    def backup_head(d):
        """Its backup (cfg.cgi) before its configuration, each line as its kind (the board's,
        one), and whether its separator says SHA512 exactly when the password is one."""
        text = d.backup_config().decode("utf-8", "replace")
        lines = text.split("\n")
        end = next(i for i in range(1, len(lines)) if lines[i].startswith("### PART"))
        kinds = []
        for x in lines[:end]:
            kind = ("part" if x == "### PART ###" else "time" if re.fullmatch(r"## \d+", x)
                    else "fw" if re.fullmatch(r"##[A-Z0-9]+\.v\d+\.\d+\.\d+", x)
                    else "md5" if re.fullmatch(r"#[0-9a-f]{32}", x)
                    else "board" if re.fullmatch(r"#[a-z][\w.]*=.*", x) else x)
            if not (kinds and kind == kinds[-1] == "board"):
                kinds.append(kind)
        sha = (T.Config(T.backup_config_part(text) or "", "v8").get("users.1.password")
               or "").startswith("$6$")
        return kinds, lines[end] == ("### PART SHA512 ###" if sha else "### PART ###")
    a, b = backup_head(real), backup_head(sim)
    check("its backup (cfg.cgi): the same header (the time, its firmware, its board and its md5), "
          "and its separator, with SHA512 if the password is one", a == b and a[1],
          f"its API {a} · simulator {b}")
    seen = []
    for d, back in ((real, lambda: emu8.settle(root)), (sim, lambda: settle(srv))):
        cfg = T.Config(d.get_config(), "v8")
        cfg.set(k, "EMU8-SHAPE")
        written = shape(d.write_config(cfg, test=True))
        back()                                       # (it restarts: until it answers again)
        seen.append((written, answer(d, "/test_mode.cgi")))
        d.confirm_test()
        back()
    check("saving in test mode (writecfg.cgi) and the test under way (test_mode.cgi)",
          seen[0] == seen[1], str(seen))
    srv.shutdown()

    print("=== the reset, as its web: afterwards, its first setup again ===")
    dev = T.AirOS(URL, USER, new)
    dev.connect()
    app = T.App(FakeUI([("yesno", 0)]), dev, wait=1)
    app.load()
    try:
        app.reset_defaults()
        ended = ""
    except T.SessionEnded as e:
        ended = e.args[1] if len(e.args) > 1 else str(e)
    emu8.settle(root)
    check("the program warns of it (its firmware has it: it says so before its login), not of "
          "ubnt/ubnt", tr("Device has been reset to defaults. Please wait while it reboots...")
          in ended and "ubnt" not in ended.split(tr("Local backup: {path}", path=""))[0],
          ended[:300])
    check("  and the device is back to its factory settings, waiting for it", T.setup_steps(
        probe.public_info()) == (True, True) and everywhere("users.1.name") == ("ubnt",) * 3,
        str(everywhere("users.1.name")))

    print("=== its first setup from a backup: the copy the program made ===")
    args = types.SimpleNamespace(device=URL, user=None, http=False, https=False, target=None)
    cc = next(str(c["code"]) for c in (probe.public_info() or {})["setup_data"]["rd_countries"]
              if str(c["code"]) != "724")            # (not the backup's)
    ui = FakeUI([("menu", cc), ("menu", "en_US"), ("yesno", 0), ("menu", "backup"),
                 ("menu", "1")])                     # (the newest: the one from before the reset)
    dev = T.connect_screen(ui, args)
    emu8.settle(root)
    waiting = on_device("tmp/system.cfg")
    check("its API takes it as from its web (/api/auth: device_setup=first, the country, the "
          "language and the backup, its cfg.cgi): it waits to be applied, with that country and "
          "that language, and the factory user logs in meanwhile", dev is not None and not ui.script
          and dev.username == "ubnt" and waiting.get("users.1.name") == USER
          and waiting.get("radio.countrycode") == waiting.get("radio.1.countrycode") == cc
          and waiting.get("gui.language") == "en_US"
          and on_device("tmp/running.cfg").get("users.1.name") == "ubnt",
          str((ui.script, waiting.get("users.1.name"), waiting.get("radio.countrycode"))))
    app = T.App(FakeUI([("yesno", 0), ("input", USER), ("password", new), ("menu", None)]), dev,
                wait=1)
    app.discovery = {"target": "127.0.0.1", "port": 19943}   # (in its own network: nothing leaves)
    app.run()
    check('  on starting, "Apply", as its web: it is put in use and saved; as it has another '
          "password, it asks for it, and logs in with the backup's (no longer the factory one)",
          not app.ui.script
          and dev.backup_loaded is False and everywhere("users.1.name") == (USER,) * 3
          and everywhere("radio.countrycode") == (cc,) * 3
          and login_error(USER, new) == "" and login_error("ubnt", "ubnt") != "",
          str((app.ui.script, everywhere("users.1.name"))))
finally:
    web.terminate()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
