"""A real airOS 6: its firmware's web (XW 6.3.26, from Ubiquiti), emulated with QEMU (see
emu6.py), against the program's client: the first boot and the factory password, the
session, the board (with its radio_caps), the channels of its regulatory table and test mode
(try, keep and discard) with what its web sends: POST with the session's token. No radio or
services: what needs them is not there. Skipped if something is missing (emu6.missing())."""
import os
import re
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "emu"))
import emu6  # noqa: E402

if os.environ.get("AIROS_EMU6") != "1":            # in namespaces of its own (no root)
    lack = emu6.missing()
    if lack:
        print("skipped: missing " + "; ".join(lack))
        print("FAILURES: none")
        sys.exit(0)
    os.environ["AIROS_EMU6"] = "1"
    os.execvp("unshare", ["unshare", "-rnmpf", "--kill-child", "--mount-proc",
                          sys.executable, os.path.abspath(__file__)])
from _common import check, fails, FakeUI, suite, WORK  # noqa: E402
T, S, XDG = suite("emu6")
tr = T.tr
URL = f"https://{emu6.HOST}"
root = emu6.prepare(os.path.join(WORK, "emu6-root"))
web = emu6.start(root)


def on_device(rel):
    """A configuration file of the emulated device (tmp/system.cfg, tmp/running.cfg or
    etc/persistent/flash.cfg, its "flash")."""
    return T.Config(open(os.path.join(root, rel), encoding="utf-8").read())


def name_in(rel):
    return on_device(rel).get("resolv.host.1.name")


def login_error(user, password):
    try:
        T.AirOS(URL, user, password).connect()
    except T.AuthError as e:
        return str(e)
    return ""


def until(cond, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.5)
    return cond()


try:
    print("=== the first boot: its web asks for the country, the terms and another password ===")
    said = login_error("ubnt", "ubnt")
    check("the program says so, with the factory password (6.3 does not let one go on with it)",
          said == tr("The device is on its first boot: log in once through its web to choose "
                     "the country, accept the terms and change the factory password, and "
                     "connect with the new one."), said)
    factory = {}
    for rel in ("tmp/system.cfg", "tmp/running.cfg"):        # (a country already chosen)
        path = os.path.join(root, rel)
        factory[path] = open(path, encoding="utf-8").read()
        with open(path, "a", encoding="utf-8") as f:
            f.write("radio.1.countrycode=840\n")
    said = login_error("ubnt", "ubnt")
    check("  with the country already chosen and the factory password: change it in its web",
          said == tr("The device still has its factory password, and its web asks to change "
                     "it before anything else: log in once through its web, choose another "
                     "one and connect with it."), said)
    for path, text in factory.items():                       # (factory settings again)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    print("=== the connection screen: its first login, as its web ===")
    kept = {}
    for rel in ("tmp/system.cfg", "tmp/running.cfg", "etc/persistent/flash.cfg",
                "etc/passwd"):                               # (to go back afterwards)
        kept[rel] = open(os.path.join(root, rel), encoding="utf-8").read()
    v6 = T.AirOS(URL, "", "")
    v6.probe()
    seen = v6.v6_first_login_info() or {}
    check("what its page asks for: the country (from its list), the language, its terms, and a "
          "new administrator and password", seen.get("first") and seen.get("new_password")
          and seen.get("new_username") and ("724", "Spain") in seen.get("countries", [])
          and ("sp_SP", "Español") in seen.get("languages", [])  # (the firmware's list)
          and "professionally installed" in seen.get("terms", ""),
          str({k: v for k, v in seen.items() if k not in ("countries", "terms")}))

    def page_with(keys):
        """What the program reads of its login page with these keys in the configuration its
        login.cgi reads (tmp/system.cfg), and the page itself; then that file as it was."""
        path, text = os.path.join(root, "tmp/system.cfg"), kept["tmp/system.cfg"]
        for key, value in keys.items():
            text, n = re.subn(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
            text += "" if n else f"{key}={value}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            other = T.AirOS(URL, "", "")
            other.probe()
            return other.v6_first_login_info() or {}, other._login_page()
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(kept["tmp/system.cfg"])
    # (its administrator's name can be changed while it is ubnt: "Administrator User Name",
    # in its language; once changed, with the factory password still, "User Name". The labels
    # are its page's own, in each language: the firmware's texts, not the program's)
    wrong = []
    for lang, named, label in (("sp_SP", False, "Nombre de usuario del administrador"),
                               ("sp_SP", True, "Nombre de usuario"),
                               ("fr_FR", True, "Nom d'utilisateur"),
                               ("zh_CN", False, "管理员用户名"),
                               ("en_US", True, "User Name")):
        info, page = page_with(dict({"gui.language": lang},
                                    **({"users.1.name": "Operator"} if named else {})))
        if not (info.get("new_password") and info.get("new_username") == (not named)
                and info.get("language") == lang and f">{label}:</label>" in page):
            wrong.append((lang, named, {k: info.get(k) for k in ("new_password", "new_username",
                                                                  "language")}))
    check("  the administrator's name, changeable only while it is ubnt: it knows it in its "
          "page's language (Spanish, French, Chinese, English)", not wrong, str(wrong))
    strong = "Safe-Passkey-2026x"
    args = types.SimpleNamespace(device=URL, user="Operator", http=False, https=False,
                                 target=None)
    ui = FakeUI([("menu", "724"), ("menu", "sp_SP"), ("yesno", 0), ("input", "Operator"),
                 ("password", "short"), ("password", strong), ("password", strong)])
    os.environ.pop("AIROS_PASSWORD", None)
    dev = T.connect_screen(ui, args)
    errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]
    check('it does it as its web, with its rules (pstrength.js: "Too short"), and logs in with '
          "the new administrator", dev is not None and dev.flavor == "v6"
          and dev.username == "Operator" and not ui.script and errors == [
              tr("Too short: its web asks for at least 12 characters.")], str((errors, ui.script)))
    check("  its configuration: the country, the administrator and its password, changed",
          until(lambda: on_device("tmp/running.cfg").get("radio.1.countrycode") == "724")
          and on_device("tmp/system.cfg").get("users.1.name") == "Operator"
          and login_error("ubnt", "ubnt") and login_error("Operator", strong) == "",
          str(on_device("tmp/system.cfg").get("users.1.name")))
    for rel, text in kept.items():                           # (factory settings again)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)
    emu6.first_login(root)
    check("its first login (as its web): the country and the new password, saved",
          on_device("etc/persistent/flash.cfg").get("radio.1.countrycode") == "840"
          and on_device("tmp/running.cfg").get("users.1.password", "").startswith("$6$"))

    print("=== the session, the board and its channels ===")
    dev = T.AirOS(URL, "ubnt", emu6.PASSWORD)
    dev.connect()
    check("logs in and recognizes it as airOS 6", dev.flavor == "v6", str(dev.flavor))
    board = dev.board_info()
    check('the board (getboardinfo.sh) and the "fast clock" its System page publishes '
          "(radio_caps)", board.get("board.name") == emu6.BOARD["name"]
          and board.get("radio.1.caps") == str(emu6.CAPS),
          str({k: board.get(k) for k in ("board.name", "radio.1.caps")}))
    chans = dev.regdomain(840, 20) or []
    _, _, raw = dev._req("GET", "/regdomain_by_country.cgi?country=840&chanbw=20")
    lines = re.findall(rb'^"\d{4} ', raw or b"", re.M)
    upper = [c for c in chans if c.band == "A" and c.control == 5745]
    check("its regulatory table (regdomain_by_country.cgi): a channel per line; 5745 MHz "
          "in 840, with the FCC's 53 dBm of EIRP", len(chans) == len(lines) > 0
          and upper and {c.eirp for c in upper} == {53.0},
          str((len(chans), len(lines), upper[:2])))
    st = dev.status()
    check("its status.cgi, with its shape (no radio: its data, empty)", isinstance(st, dict)
          and {"host", "wireless", "interfaces"} <= set(st), str(sorted(st)[:8]))

    print("=== test mode, as its web asks for it (POST with the session's token) ===")
    cfg = T.Config(dev.get_config())
    cfg.set("resolv.host.1.name", "EMU6-TEST")
    dev.write_config(cfg, test=True)                # (and what runs changes after restarting)
    running = until(lambda: name_in("tmp/running.cfg") == "EMU6-TEST")
    check("try: it runs with the change and enters test mode (the whole 180 s: its web does "
          "not count them down)", running and name_in("etc/persistent/flash.cfg") != "EMU6-TEST"
          and dev.test_mode_info() == {"active": True, "time_left": 180},
          str((name_in("tmp/running.cfg"), dev.test_mode_info())))
    dev.confirm_test()
    check("keep: it stays saved and leaves test mode",
          until(lambda: not os.path.exists(os.path.join(root, "var/run/testmode"))
                and name_in("etc/persistent/flash.cfg") == "EMU6-TEST")
          and not (dev.test_mode_info() or {}).get("active"))
    cfg = T.Config(dev.get_config())
    cfg.set("resolv.host.1.name", "EMU6-OTHER")
    dev.write_config(cfg, test=True)
    until(lambda: name_in("tmp/running.cfg") == "EMU6-OTHER")
    dev.discard_test()
    check("discard: it goes back to what is saved and leaves test mode",
          until(lambda: name_in("tmp/running.cfg") == "EMU6-TEST")
          and name_in("tmp/system.cfg") == "EMU6-TEST"
          and not (dev.test_mode_info() or {}).get("active"), name_in("tmp/running.cfg"))

    print("=== its apply.cgi does nothing with a GET (which is why the program sends a POST) ===")
    before = name_in("tmp/running.cfg")
    cfg = T.Config(dev.get_config())
    cfg.set("resolv.host.1.name", "EMU6-GET")
    dev._v6_post_with_token("/system.cgi", [("action", "cfgupload"), ("cfgupload", "Upload")],
                            [("cfgfile", "system.cfg", cfg.text().encode("utf-8"))])
    dev._req("GET", "/apply.cgi?testmode=on")
    time.sleep(3)
    check("it uploads the configuration, but neither runs it nor enters test mode",
          name_in("tmp/system.cfg") == "EMU6-GET" and name_in("tmp/running.cfg") == before
          and not os.path.exists(os.path.join(root, "var/run/testmode")),
          str((name_in("tmp/system.cfg"), name_in("tmp/running.cfg"), before)))
finally:
    web.terminate()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
