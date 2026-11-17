"""Tests of the rest of "System" (airOS 8): the Device section (its web's language, external
reset, airView, PoE passthrough, crash reports, LED night mode and the TDD capacity report),
the management radio (on startup and its button), the accounts (the administrator's name, which
the program follows when applying, and the read-only account with its password) and the
location, as its web reads and saves them (with the real configuration and board.info of a
NanoStation 5AC loco with 8.7.14). All on 127.0.0.1."""
import os
import shutil
import sys
import threading
import time

from _common import check, fails, FakeUI, settle, suite  # noqa: E402
T, S, XDG = suite("system")
tr = T.tr
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "fixtures", "real", "airos8.7.14-nanostation5ac-sta")


def cfg(**kv):
    return T.Config("".join(f"{k.replace('__', '.')}={v}\n" for k, v in kv.items()))


real = T.Config(open(REAL + ".cfg", encoding="utf-8").read())
board = dict(line.split("=", 1) for line in open(REAL + ".board.info", encoding="utf-8")
             .read().splitlines() if "=" in line)

print("=== what its web shows on the real NanoStation (8.7.14) ===")
check("Device: airView (its 2.4 GHz radio, left out of the web), crash reports and LED night "
      "mode; no external reset or PoE passthrough (its board.info does not have them)",
      [k for k, _, _ in T.device_switches(board)] == [
          "airview.status", "system.autosend_crash_reports", "system.leds.nightmode"]
      and [T.switch_on(real, k) for k, _, _ in T.device_switches(board)]
      == [True, False, False])
check("its web in Spanish; TDD report, the default",
      T.ui_language(real) == "sp_SP" and T.tdd_reporting(real) == 0)
check("Gen2: management radio, off on startup; no GPS: location, empty",
      T.management_radio_offered(board) and not T.provmode_startup(real)
      and T.location_offered(board) and T.location(real) == {"lat": "", "lon": "", "height": ""})
check("no read-only account and no password for it", {k: v for k, v in T.accounts(real).items()
                                                      if k != "admin"} == {
    "ro": False, "ro_name": "", "ro_password": False})

print("=== what it shows depending on the board, and the defaults ===")
check("external reset and PoE passthrough only if its board.info has them", [
    k for k, _, _ in T.device_switches({"feature.ext_reset": "1", "feature.poe_passthrough": "1"})]
      == ["system.external.reset", "system.poe_passthrough", "system.autosend_crash_reports",
          "system.leds.nightmode"])
check("airView: with a radio left out of the web (even \"0\", as in its web), not on Gen3",
      T.airview_offered({"radio.2.web_exclude": "0"}) and T.airview_offered(board)
      and not T.airview_offered({"radio.1.chains": "2"})
      and not T.airview_offered(dict(board, **{"feature.g3": "1"})))
check("management radio on Gen2 and Gen3; the location, not with GPS",
      T.management_radio_offered({"feature.g3": "1"})
      and not T.management_radio_offered({"feature.g2": "0"})
      and not T.location_offered({"feature.gps": "1"}))
empty = cfg()
check("without keys: external reset and airView enabled, the rest not; English; radio on "
      "startup enabled; administrator ubnt",
      [T.switch_on(empty, k) for k, _, _, _ in T.DEVICE_SWITCHES]
      == [True, True, False, False, False] and T.ui_language(empty) == "en_US"
      and T.provmode_startup(empty) and T.accounts(empty)["admin"] == "ubnt")
check("the radio's state (status.cgi): without data, off; any other state counts as on, as "
      "for its button",
      T.provmode_state({"provmode": {}}) == (-1, None)
      and not T.management_radio_up({"provmode": {}}) and not T.management_radio_up({})
      and T.management_radio_up({"provmode": {"status": 0}})
      and T.provmode_state({"provmode": {"status": 2, "timeout": 894}}) == (2, 894))
ap_ptmp = {"ap": True, "ptp": False, "tdd_duration": 5000}
check('TDD report: on an access point (not Gen3); editable in PtMP without "Flexible (legacy)"',
      T.tdd_reporting_offered(ap_ptmp, {}) and not T.tdd_reporting_offered(
          dict(ap_ptmp, ap=False), {}) and not T.tdd_reporting_offered(ap_ptmp, {"feature.g3": "1"})
      and T.tdd_reporting_editable(ap_ptmp)
      and not T.tdd_reporting_editable(dict(ap_ptmp, ptp=True))
      and not T.tdd_reporting_editable(dict(ap_ptmp, tdd_duration=0)))

print("=== the accounts: their rules and what it saves ===")
acc = {"admin": "admin", "ro": True, "ro_name": "reader", "ro_password": True}
check("administrator: required, 1 to 64, its pattern", [T.admin_name_problem(n, acc) for n in (
    "", " ", "x" * 65, "-x", " admin", "a b", "naïve", "ok_1.a-b")] == [
    tr("Administrator Username is required"), tr("Administrator Username is required"),
    tr("Administrator Username must be between {low} and {high} characters", low=1, high=64),
    tr("Invalid username."), tr("Invalid username."), tr("Invalid username."),
    tr("Invalid username."), None])
check("  and different from the read-only one's if that account is enabled",
      T.admin_name_problem("reader", acc)
      == tr("Read-Only and Administrator Account usernames should differ.")
      and T.admin_name_problem("reader", dict(acc, ro=False)) is None)
check("read-only: in its order (required, length, different, password, pattern)",
      [T.ro_name_problem(n, dict(acc, ro_password=p)) for n, p in (
          ("", True), ("y" * 65, True), ("admin", False), ("reader", False), ("-x", True),
          ("reader", True))] == [
          tr("Read only user name is required"),
          tr("Read only user name must be between {low} and {high} characters", low=1,
             high=64),
          tr("Read-Only and Administrator Account usernames should differ."),
          tr("Read-Only Account Password required."), tr("Invalid username."), None])
check("it saves both names and, always, the read-only account's shell and ids",
      T.plan_accounts(dict(acc, ro=False, ro_name="")) == {
          "users.1.name": "admin", "users.2.name": None, "users.2.shell": "/bin/false",
          "users.2.gid": "100", "users.2.uid": "100", "users.2.status": "disabled"})
check("its password: any one that is not empty (its web does not check it)",
      T.ro_password_problem("") == tr("Type the new password.")
      and T.ro_password_problem("a") is None
      and T.ro_password_problem("x" * 64) == tr("At most {max} characters.", max=63))

print("=== the location ===")
check("optional and in its range (the height in meters)", [T.location_problem(f, v) for f, v in (
    ("lat", ""), ("lat", "-34.6037"), ("lat", "90.1"), ("lon", "180"), ("lon", "-180.5"),
    ("lon", "1,000"), ("height", "+5"), ("height", "10000"), ("height", "abc"))] == [
    None, None, tr("Latitude must be between {low} and {high}", low=-90, high=90), None,
    tr("Longitude must be between {low} and {high}", low=-180, high=180),
    tr("Longitude must be between {low} and {high}", low=-180, high=180),
    tr("Device Height must be between {low} and {high}", low=0, high=10000), None,
    tr("Device Height must be between {low} and {high}", low=0, high=10000)])
check("empty deletes it", T.plan_location({"lat": " -34.6 ", "lon": "", "height": "12"}) == {
    "system.latitude": "-34.6", "system.longitude": None, "system.height": "12"})
check("in the US and the UK, in feet as in its web (saved in meters, with one decimal)",
      T.imperial(840) and T.imperial(826) and not T.imperial(32)
      and T.height_shown("30.5", True) == "100" and T.height_saved("100", True) == "30.5"
      and T.height_shown("30.5", False) == "30.5" and T.height_saved(" 12 ", False) == "12"
      and T.height_saved("", True) == "")


def start(port, version=8):
    srv = S.make_server(version, port, restart=0.3, test_secs=60)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    return srv, srv.dev, ui, app


def rows(ui, title):
    return dict([items for t, items, _ in ui.menus if t == title][-1])


def msgs(ui, title=None):
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def screens(port):
    print("=== v8: the System menu and the Device section ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [("menu", None)]
        app.system_menu()
        menu = rows(ui, tr("System"))
        check("the rows, in its page's order", list(menu) == list("vnemdalprfsbu") and {
            k: menu[k] for k in "emal"} == {
            "e": tr("Device: interface language {language}", language="English"),
            "m": tr("Management Radio on startup: disabled") + " · " + tr("off now"),
            "a": tr("System Accounts: {admin}; no read-only account", admin="ubnt"),
            "l": tr("Location: not set")}, str(menu))
        app.working.set("radio.1.polling_ff_flex", "0")      # (with "Flexible (legacy)")
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "l"), ("menu", "sp_SP"), ("menu", "x"), ("menu", "v"),
                     ("menu", "c"), ("menu", "n"), ("menu", "c"), ("menu", "t"),
                     ("menu", None)]
        app.device_menu()
        langs = [items for t, items, _ in ui.menus if t == tr("Interface Language")][0]
        check("the language, from its web's list", len(langs) == 11
              and ("sp_SP", "Español") in langs,       # (its web's own name for it: device data)
              str(langs[:3]))
        check('TDD: with "Flexible (legacy)" it cannot be changed (as in its web)',
              msgs(ui) == [tr("Its web only lets you change it on an access point in PtMP, "
                              "with a frame other than \"Flexible (legacy)\".")], str(msgs(ui)))
        dev_rows = rows(ui, tr("Device"))
        check("it marks what changed (the reports, twice: as they were)", dev_rows == {
            "l": "* " + tr("Interface Language: {language}", language="Español"),  # (data)
            "x": "* " + tr("External Reset") + ": " + tr("disabled"),
            "v": "* " + tr("Airview") + ": " + tr("disabled"),
            "c": tr("Auto-send Crash Reports") + ": " + tr("disabled"),
            "n": "* " + tr("LEDs Night Mode") + ": " + tr("enabled"),
            "t": tr("Fixed TDD Framing Capacity Reporting: {mode}", mode=tr("Default"))},
              str(dev_rows))
        app.working.set("radio.1.polling_ff_flex", "1")      # (with "Flexible")
        ui.script = [("menu", "t"), ("menu", "1"), ("menu", None)]
        app.device_menu()
        check('  with "Flexible", it can', app.working.get("radio.1.ff_cap_rep") == "1")
        check("what is prepared", app.changes() == [
            ("airview.status", "enabled", "disabled"), ("gui.language", "en_US", "sp_SP"),
            ("radio.1.ff_cap_rep", "0", "1"), ("system.external.reset", "enabled", "disabled"),
            ("system.leds.nightmode", "disabled", "enabled")], str(app.changes()))
        app.working = app.original.copy()
        print("=== the management radio ===")
        ui.log.clear()
        ui.script = [("menu", "r"), ("yesno", 1), ("menu", "r"), ("yesno", 0), ("menu", "r"),
                     ("yesno", 0), ("menu", "s"), ("menu", None)]
        app.management_radio_menu()
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("right away (it warns first): on for 15 minutes, and off; its web restarts",
              len(asked) == 3 and asked[0].endswith(tr("Turn the management radio on for 15 "
                                                       "minutes?"))
              and asked[2].endswith(tr("Turn the management radio off?"))
              and msgs(ui) == [tr("The management radio is on."),
                               tr("The management radio is off.")]
              and d.events[-2:] == ["provmode-start", "provmode-stop"], str(msgs(ui)))
        on = [items for t, items, _ in ui.menus
              if t == tr("Management Radio Settings")][2]
        check('  on, its button says "Disable"', dict(on)["r"] == tr(
            "Management Radio: {action}", action=tr("Disable")), str(on))
        check("on startup: it is prepared", app.changes() == [
            ("system.airosx.prov.status", "disabled", "enabled")], str(app.changes()))
        real_status, calls = app.dev.status, []

        def slow():                         # (as the real NanoStation after restarting its web)
            calls.append(1)
            if len(calls) == 1:
                raise T.AirOSError("status.cgi did not return valid data (HTTP 200).")
            return real_status()
        app.dev.status = slow
        ui.log.clear()
        ui.script = [("menu", "r"), ("yesno", 0), ("menu", "r"), ("yesno", 0), ("menu", None)]
        app.management_radio_menu()
        app.dev.status = real_status
        check("if status.cgi is slow to give its data, it reads it again", msgs(ui) == [
            tr("The management radio is on."), tr("The management radio is off.")]
              and len(calls) == 3, str((msgs(ui), len(calls))))
        app.working = app.original.copy()
        print("=== the location ===")
        ui.log.clear()
        ui.script = [("menu", "a"), ("input", "91"), ("input", " -34.6037 "), ("menu", "o"),
                     ("input", "-58.3816"), ("menu", "h"), ("input", "-1"), ("input", "25"),
                     ("menu", None)]
        app.location_menu()
        check("its ranges and what is prepared", msgs(ui) == [
            tr("Latitude must be between {low} and {high}", low=-90, high=90),
            tr("Device Height must be between {low} and {high}", low=0, high=10000)]
              and app.changes() == [("system.height", None, "25"),
                                    ("system.latitude", None, "-34.6037"),
                                    ("system.longitude", None, "-58.3816")]
              and rows(ui, tr("Location"))["h"] == "* " + tr("Device Height: {value}",
                                                             value="25 m"),
              str((msgs(ui), app.changes())))
        real_ws = app._wstate
        app._wstate = lambda c=None: dict(real_ws(c), country=840)     # (in the US)
        ui.script = [("menu", "h"), ("input", "100"), ("menu", None)]
        app.location_menu()
        app._wstate = real_ws
        check("in the US, in feet: 100 ft are saved as 30.5 m",
              app.working.get("system.height") == "30.5" and ui.inputs[-1] == "82"
              and rows(ui, tr("Location"))["h"] == "* " + tr("Device Height: {value}",
                                                             value="100 ft"),
              str((ui.inputs[-1:], app.working.get("system.height"))))
        app.working = app.original.copy()
    finally:
        srv.shutdown()
        srv.server_close()


def accounts_flow(port):
    print("=== v8: the accounts; the read-only password goes right away ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [
            ("menu", "o"), ("input", "ubnt"), ("input", "reader"),     # enable it: its name
            ("password", ""), ("password", "abc"), ("password", "abd"),  # and its password
            ("password", "abc"), ("password", "abc"), ("yesno", 0),
            ("menu", "u"), ("input", "reader"), ("input", "-admin"), ("input", "admin2"),
            ("menu", None)]
        app.accounts_menu()
        check("script used up", not ui.script, str(ui.script[:2]))
        check("its web's messages, in order", [t for k, ti, t in ui.log if k == "msgbox"
                                               and ti == tr("Invalid value")] == [
            tr("Read-Only and Administrator Account usernames should differ."),
            tr("Type the new password."), tr("Passwords should match."),
            tr("Read-Only and Administrator Account usernames should differ."),
            tr("Invalid username.")], str(msgs(ui)))
        check("it asks for the missing password; the device saves it right away and, as a real "
              "8.7.14 does, enables the account (it says so)",
              msgs(ui, tr("System Accounts"))[0].startswith(
                  tr("Read-Only Account Password required.")) and msgs(
                  ui, tr("Change Read-Only User Password")) == [
                  tr("Read-Only Account Password successfully changed.") + "\n\n" + tr(
                      "The device has turned the read-only account on already (it does so "
                      "when its password is set); its name goes with the other changes.")]
              and d.saved.get("users.2.password", "").startswith("$1$")
              and d.saved.get("users.2.status") == "enabled"
              and app.original.get("users.2.status") == "enabled"
              and d.events.count("ro-password") == 1
              and T.accounts(app.original)["ro_password"]   # (as getcfg.cgi gives it: "-")
              and app.original.get("users.2.password") == "-",
              str((msgs(ui, tr("System Accounts"))[:1], d.saved.get("users.2.password"),
                   app.original.get("users.2.password"), d.events[-3:])))
        check("the rest, prepared (as it marks it)", app.changes() == [
            ("users.1.name", "ubnt", "admin2"), ("users.2.name", None, "reader")]
              and rows(ui, tr("System Accounts")) == {
                  "u": "* " + tr("Administrator Username: {name}", name="admin2"),
                  "p": tr("Change Password"),
                  "o": tr("Read-Only Account: enabled"),
                  "n": "* " + tr("Read-Only Account Name: {name}", name="reader"),
                  "w": tr("Change Read-Only User Password")},
              str(app.changes()))
        print("=== the program follows the administrator's new name ===")
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 1)]                   # test and revert
        app.apply(test=True)
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("it warns before applying", tr(
            "⚠ The administrator is renamed: {old} → {new}. From then on the program logs in "
            "with the new name (the password does not change).", old="ubnt", new="admin2")
              in asked[0], asked[0][-300:])
        check("revert: it logs in as ubnt again", ui.titles()[-1] == tr("Reverted")
              and app.dev.username == "ubnt" and d.running.get("users.1.name") == "ubnt")
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 255)]                 # not confirmed
        app.apply(test=True)
        check("not confirmed: the same (the device will go back to ubnt)",
              ui.titles()[-1] == tr("Not confirmed") and app.dev.username == "ubnt")
        other = T.AirOS(f"http://127.0.0.1:{port}", "admin2", "ubnt")   # (as the end of
        other.connect()                                                 #  the test would)
        other.discard_test()
        time.sleep(0.2)
        settle(srv)
        app.load()
        for k, v in (("users.1.name", "admin2"), ("users.2.name", "reader"),
                     ("users.2.status", "enabled")):
            app.working.set(k, v)
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 0)]                   # test and keep
        app.apply(test=True)
        app.dev.cookies.clear()
        app.dev.login()                                            # (logs in again)
        check("keep: saved, and the program logs in as admin2", ui.titles()[-1] == tr(
            "Done") and app.dev.username == "admin2" and d.saved.get("users.1.name") == "admin2"
              and app.status, str(ui.titles()))
        ui.script = [("menu", "o"), ("yesno", 0), ("menu", None)]
        app.accounts_menu()
        check("disabling it keeps its name", app.changes() == [
            ("users.2.status", "enabled", "disabled")], str(app.changes()))
    finally:
        srv.shutdown()
        srv.server_close()


def airos6(port):
    print("=== airOS 6: another web, without these rows ===")
    srv, d, ui, app = start(port, version=6)
    try:
        ui.script = [("menu", None)]
        app.system_menu()
        check("its System menu does not have them",
              list(rows(ui, tr("System"))) == list("vnprfsbu"), str(rows(ui, tr("System"))))
    finally:
        srv.shutdown()
        srv.server_close()


def review(port):
    print("=== the review: nothing pending after going there and back, and what is saved right "
          "away ===")
    srv, d, ui, app = start(port)
    try:
        for c in (d.running, d.saved, d.system):
            c.pop("system.leds.nightmode", None)
        app.load()
        ui.script = [("menu", "n"), ("menu", "n"), ("menu", None)]
        app.device_menu()
        check("a switch that was not in its configuration, there and back: nothing pending",
              not ui.script and not app.changes(), str(app.changes()))
        for k, v in (("radio.countrycode", "840"), ("radio.1.countrycode", "840"),
                     ("system.height", "10")):
            app.working.set(k, v)
        app.original = app.working.copy()
        ui.script = [("menu", "h"), ("input", T.height_shown("10", True)), ("menu", None)]
        app.location_menu()
        check("the height in feet just as it is shown: nothing pending (not 10.1)",
              not ui.script and not app.changes(), str(app.changes()))
        app.load()
        ui.script = [("menu", "o")] + [("input", v) for v in ("me@x", "here")] + [
            ("menu", "o"), ("yesno", 0), ("menu", None)]          # (its community is there)
        try:
            app.snmp_menu()
        except AssertionError as e:
            print("     script:", e)
        check("SNMP enabled (with what it asks for) and disabled again: nothing pending",
              not ui.script and not app.changes(), str(app.changes()))
        other = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        other.connect()
        cfg = T.Config(other.get_config())
        cfg.set("wireless.1.ssid", "Someone-elses-test")
        other.write_config(cfg, test=True)
        settle(srv)
        ui.log.clear()
        ui.script = []
        app.change_password()
        check("with a test in progress, not the password (it is saved right away)",
              msgs(ui, tr("Change Password")) == [tr(
                  "A test is in progress on the device: confirm it or revert it first.")])
        other.discard_test()
        settle(srv)
        app.load()
        blocker = T.BACKUP_DIR
        shutil.rmtree(blocker, ignore_errors=True)
        os.makedirs(os.path.dirname(blocker), exist_ok=True)
        open(blocker, "w").close()                  # (a file where its folder goes)
        app._accept({"resolv.host.1.name": "No-backup"})
        ui.log.clear()
        ui.script = [("yesno", 0), ("yesno", 1)]
        try:
            app.apply(test=False)
        finally:
            os.remove(blocker)
        asked = [ti for k, ti, t in ui.log if k == "yesno"]
        check('unable to write the local backup: it asks and, with "No", sends nothing',
              not ui.script and asked == [tr("Apply and save"), tr("Local backup")]
              and d.system.get("resolv.host.1.name") != "No-backup", str(asked))
        app.working = app.original.copy()
    finally:
        srv.shutdown()
        srv.server_close()


screens(19901)
review(19931)
accounts_flow(19911)
airos6(19921)
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
