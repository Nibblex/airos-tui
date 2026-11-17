"""Tests of the main menu's actions and of the error paths the other suites do not take:
reboot, read again, backups, quick settings, the key editor, the pending changes, DNS, the
fallback IP, LAN and PPPoE, the endings of test mode, the connection screen, the command line
and the device's errors (simulated by changing what it answers on some paths). All against the
simulator on 127.0.0.1; the antenna search only asks 127.0.0.1."""
import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import sys
import threading
import time
import types

from _common import check, fails, FakeUI, Package, settle, suite, WORK  # noqa: E402
os.environ.pop("AIROS_PASSWORD", None)        # the connection screen would take it
T, S, XDG = suite("actions")
TMP = os.path.join(WORK, "actions")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
tr = T.tr


def start(version, port, **kw):
    kw.setdefault("restart", 0.5)
    kw.setdefault("test_secs", 60)
    srv = S.make_server(version, port, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def discard(srv, dev):
    """Leave the test mode the program left running (once the simulator is up again)."""
    settle(srv)
    dev.discard_test()
    settle(srv)


def app_for(port, wait=1):
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=wait)
    app.load()
    return app, app.ui, dev


def act(app_or_ui, script, fn, *args):
    """fn(*args) with the scripted answers. Returns (it used them all and asked nothing more,
    what it showed [(kind, title, text)], what fn returned)."""
    ui = getattr(app_or_ui, "ui", app_or_ui)
    ui.script = list(script)
    mark = len(ui.log)
    result = None
    try:
        result = fn(*args)
        done = not ui.script
    except Exception as e:                  # AssertionError: a question with no answer
        print(f"       ({type(e).__name__}) {e}")
        done = False
    return done, ui.log[mark:], result


def shown(log, title, kind="msgbox"):
    """The text of the first window with that title and kind ("" if there was none)."""
    return next((text for k, t, text in log if k == kind and t == title), "")


def titles(log, kind="msgbox"):
    return [t for k, t, _ in log if k == kind]


def inject(dev, rules):
    """From now on the device answers differently on some paths (the rest, as usual).
    rules = {"[METHOD ]/path prefix": (status, body)}, or a function that gets the real
    answer (status, headers, body) and returns it changed. Returns what undoes it."""
    real = T.AirOS._raw.__get__(dev)

    def raw(method, path, body=None, headers=None, timeout=None, auth=True):
        for key, rule in rules.items():
            want, _, prefix = key.rpartition(" ")
            if path.startswith(prefix) and want in ("", method):
                if callable(rule):
                    return rule(*real(method, path, body, headers, timeout, auth))
                return rule[0], {}, rule[1]
        return real(method, path, body, headers, timeout, auth)
    dev._raw = raw
    return lambda: dev.__dict__.pop("_raw", None)


@contextlib.contextmanager
def faults(dev, rules):
    undo = inject(dev, rules)
    try:
        yield
    finally:
        undo()


_MISSING = object()


@contextlib.contextmanager
def patched(obj, **attrs):
    """Replace attributes for a while (to put the program in a situation that the simulator
    cannot produce on its own): of the program (T) or of an object (the app, the device)."""
    old = {k: getattr(obj, k) if isinstance(obj, Package) else vars(obj).get(k, _MISSING)
           for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is _MISSING:
                delattr(obj, k)
            else:
                setattr(obj, k, v)


def raises(fn, *args):
    """The AirOSError that fn raises (None if it raises nothing)."""
    try:
        fn(*args)
    except T.AirOSError as e:
        return e
    return None


E500 = (500, b"boom")


# ------------------------------------------------------------------------------------------
def menu_actions():
    print("=== Reboot (System), read again and backups (airOS 8) ===")
    srv = start(8, 19101)
    d = srv.dev
    app, ui, dev = app_for(19101)
    p0 = app.original.get("radio.1.txpower")

    ok, log, _ = act(app, [("yesno", 1)], app.reboot)
    check('reboot: "No" sends nothing', ok and "reboot" not in d.events)
    real_wait = app.wait_for_device

    def short_wait(title, initial, max_wait=T.MAX_WAIT, alt=None):    # 1 s instead of 40
        return real_wait(title, 1, max_wait=30, alt=alt)
    app.working.set("radio.1.txpower", "12")
    with patched(app, wait_for_device=short_wait):
        ok, log, _ = act(app, [("yesno", 0)], app.reboot)
    check('reboot: "Yes" reboots, waits, logs in again and reads', ok and "reboot" in d.events
          and shown(log, tr("Done")) == tr("The device answers again."), str(log[-1:]))
    check("reboot: the unapplied changes are still pending",
          app.changes() == [("radio.1.txpower", p0, "12")], str(app.changes()))
    settle(srv)
    with patched(app, wait_for_device=lambda *a, **k: False):
        ok, log, _ = act(app, [("yesno", 0)], app.reboot)
    check('reboot: if it does not come back, "No answer"', ok
          and shown(log, tr("No answer")) == tr("The device does not answer yet."))
    settle(srv)

    print("=== Read again ===")
    ok, log, _ = act(app, [("yesno", 1)], app.reload)
    check('with changes it asks; "No" keeps them', ok
          and titles(log, "yesno") == [tr("Read again")]
          and app.working.get("radio.1.txpower") == "12")
    ok, log, _ = act(app, [("yesno", 0)], app.reload)
    check('"Yes" discards them and reads the device', ok and not app.changes())
    ok, log, _ = act(app, [], app.reload)
    check("without changes it does not ask", ok and not titles(log, "yesno"))

    print("=== Backups (airOS 8: the device's file, as the web) ===")
    dest = os.path.join(TMP, "web-backup.cfg")
    ok, log, _ = act(app, [("input", dest)], app.save_backup)
    content = open(dest, encoding="utf-8").read() if os.path.isfile(dest) else ""
    text = shown(log, tr("Back Up Configuration"))
    # (the message up to its size: "Saved in {path} (")
    saved_in = tr("Saved in {path} ({size} KB).\n\nIt has the whole configuration, with the "
                  "hashes of its passwords: keep it safe.").partition("{size}")[0]
    check("save: the device's file (cfg.cgi), 0600, with the password hashes",
          ok and oct(os.stat(dest).st_mode & 0o777) == "0o600"
          and content.startswith("### PART ###") and "users.1.password=$1$" in content
          and text.startswith(saved_in.format(path=dest)), text[:120])
    ok, log, _ = act(app, [("menu", None)], app.restore_backup)
    check("load: cancel", ok and not app.changes())
    ok, log, _ = act(app, [("menu", "o"), ("input", "")], app.restore_backup)
    check("load another file: an empty path does nothing", ok and not titles(log))
    gone = os.path.join(TMP, "missing.cfg")
    ok, log, _ = act(app, [("menu", "o"), ("input", gone)], app.restore_backup)
    check("load: a file that does not exist", ok
          and shown(log, "Error").startswith(tr("Could not read {path}: {error}", path=gone,
                                                error="")), str(log[-1:]))   # (any error)
    big = os.path.join(TMP, "big.cfg")
    with open(big, "w") as f:
        f.write("radio.1.txpower=10\n" * 30000)          # 570 kB
    ok, log, _ = act(app, [("menu", "o"), ("input", big)], app.restore_backup)
    check("load: more than 512 kB is refused", ok
          and shown(log, "Error") == tr("Could not read {path}: {error}", path=big,
                                        error=tr("too large")))
    junk = os.path.join(TMP, "notes.txt")
    with open(junk, "w") as f:
        f.write("shopping list\nbread\nmilk\n")
    ok, log, _ = act(app, [("menu", "o"), ("input", junk)], app.restore_backup)
    check("load: a file that is not a system.cfg", ok
          and shown(log, "Error") == tr("That file does not look like an airOS system.cfg."))
    c = app.original.copy()          # (as airos-tui saves it on airOS 8: users.1.password "-")
    c.set("wireless.1.ssid", "From-Backup")
    c.set("mac-telnetd.users.1.password", "$1$other$device")
    c.set("system.cfg.version", "1")
    good = os.path.join(TMP, "other-device.cfg")
    with open(good, "w", encoding="utf-8") as f:
        f.write(c.text())
    ok, log, _ = act(app, [("menu", "o"), ("input", f"  {good}  ")], app.restore_backup)
    check("load another file (path with spaces): its differences become pending changes",
          ok and tr("Backup loaded") in titles(log)
          and [k for k, _, _ in app.changes()] == ["wireless.1.ssid"], str(app.changes()))
    check("load: the passwords and the version stay as the device has them",
          app.working.get("mac-telnetd.users.1.password")
          == app.original.get("mac-telnetd.users.1.password")
          and app.working.get("system.cfg.version") == app.original.get("system.cfg.version"))
    app.set_loaded(app.original_text)

    print("=== Quick settings ===")
    ok, log, _ = act(app, [("menu", "1"), ("input", "   "), ("menu", "1"),
                           ("input", "North Tower"), ("menu", "1"), ("input", "North Tower"),
                           ("menu", None)], app.quick_settings)
    check("name (as in System): only spaces is not valid; the same value is not a change", ok
          and titles(log) == [tr("Invalid value"), tr("Device name")]
          and app.working.get("resolv.host.1.name") == "North Tower", str(titles(log)))
    check("name (airOS 6): 1 to 64 printable ASCII characters", T._v_hostname("")
          and T._v_hostname("x" * 65) and T._v_hostname("Tower-1 (south)") is None)
    app.set_loaded(app.original_text)
    for k in ("aaa.1.wpa.psk", "wpasupplicant.profile.1.network.1.psk"):
        app.working.delete(k)
    ok, log, _ = act(app, [("menu", "3"), ("menu", None)], app.quick_settings)
    check("WPA2 key with no key in the configuration: it explains and does not create it", ok
          and shown(log, tr("WPA2 key (PSK)")) == tr(
              "This device does not have that key in its configuration (is wireless security "
              "disabled?).\nSet it up in Wireless → Security."))
    app.set_loaded(app.original_text)
    careful = tr("Careful: its wireless security is disabled, so this key is not used until it "
                 "is enabled (Wireless → Security).")
    prompts = []
    for security in ("wpa2", "none"):
        app.set_loaded(app.original_text)
        if security == "none":
            app._accept(T.plan_wireless(app.working, app.dev.flavor, app._board(),
                                        {"security": "none"}))
        ok, log, _ = act(app, [("menu", "3"), ("input", None), ("menu", None)],
                         app.quick_settings)
        prompts.append(shown(log, tr("WPA2 key (PSK)"), "input"))
    check("WPA2 key: the warning of a disabled security, only when it is (by its state, as "
          "Wireless → Security reads it)", careful not in prompts[0] and careful in prompts[1],
          str(prompts))
    app.set_loaded(app.original_text)

    print("=== Key editor and pending changes ===")
    ok, log, _ = act(app, [("input", None)], app.edit_keys)
    check("editor: cancel the search", ok)
    ok, log, _ = act(app, [("input", ""), ("menu", None)], app.edit_keys)
    head = shown(log, tr("Configuration (system.cfg)"), "menu")
    check("editor: no filter, all the keys", ok and head == tr(
        "{count} key(s). * = modified, not applied yet.", count=len(list(app.working.keys()))),
        head)
    ok, log, _ = act(app, [("input", "")], app.add_key)
    check("add key: an empty name does nothing", ok and not titles(log))
    ok, log, _ = act(app, [("input", "key with spaces")], app.add_key)
    check("add key: invalid name", ok and shown(log, tr("Invalid name"))
          == tr("Use letters, numbers, dots, hyphens and _."))
    ok, log, _ = act(app, [("input", "users.1.password")], app.add_key)
    check("add key: a password is not added from here", ok
          and titles(log) == [tr("Invalid name")])
    ok, log, _ = act(app, [("input", " radio.1.txpower "), ("menu", None)], app.add_key)
    check("add key: if it already exists, it is edited", ok
          and titles(log, "menu") == ["radio.1.txpower"])
    ok, log, _ = act(app, [("input", "resolv.nameserver.2.ip"), ("input", None)], app.add_key)
    check("add key: cancel the value", ok and not app.changes())
    ok, log, _ = act(app, [("input", "resolv.nameserver.2.ip"), ("input", "1.2.3.400")],
                     app.add_key)
    check("add key: invalid value", ok and titles(log) == [tr("Invalid value")]
          and not app.changes())
    ok, log, _ = act(app, [("menu", "2"), ("yesno", 0)], app.edit_one, "resolv.nameserver.1.ip")
    check("edit: delete a key", ok and "resolv.nameserver.1.ip" not in app.working)
    ok, log, _ = act(app, [("yesno", 0)], app.edit_one, "resolv.nameserver.1.ip")
    check("edit: a deleted key is restored", ok and titles(log, "yesno") == [tr("Restore key")]
          and not app.changes())
    ok, log, _ = act(app, [("menu", "1"), ("input", p0)], app.edit_one, "radio.1.txpower")
    check("edit: the same value is not a change", ok and not app.changes())
    app.working.set("radio.1.txpower", "12")
    ok, log, _ = act(app, [("menu", "3")], app.edit_one, "radio.1.txpower")
    check("edit: back to the original value", ok
          and [t for t, _ in ui.menus[-1][1]] == ["1", "2", "3"]
          and not app.changes())
    ok, log, _ = act(app, [], app.show_changes)
    check("view changes: no changes", ok
          and shown(log, tr("Changes")) == tr("There are no pending changes."))
    app.working.set("resolv.nameserver.2.ip", "1.1.1.1")
    app.working.delete("resolv.nameserver.1.status")
    app.working.set("radio.1.txpower", "12")
    ok, log, _ = act(app, [], app.show_changes)
    text = shown(log, tr("Pending changes"), "textbox")
    check("view changes: added (+), deleted (-) and changed (~)", ok
          and "+ resolv.nameserver.2.ip = 1.1.1.1" in text
          and "- resolv.nameserver.1.status" in text and "~ radio.1.txpower" in text, text)
    ok, log, _ = act(app, [("menu", "3")], app.apply_menu)
    check('apply → "View the changes"', ok
          and titles(log, "textbox") == [tr("Pending changes")])
    ok, log, _ = act(app, [("menu", "4"), ("yesno", 1)], app.apply_menu)
    check('apply → "Discard", and "No" keeps them', ok and len(app.changes()) == 3)
    ok, log, _ = act(app, [("menu", "4"), ("yesno", 0)], app.apply_menu)
    check('apply → "Discard" drops them without sending anything', ok and not app.changes()
          and d.saved.get("resolv.nameserver.2.ip") is None)
    ok, log, _ = act(app, [], app.apply_menu)
    check("apply without changes: it says so", ok
          and shown(log, tr("Apply")) == tr("There are no pending changes."))
    srv.shutdown()


# ------------------------------------------------------------------------------------------
def test_mode_outcomes():
    print("=== Apply: what is not sent, waits and odd answers (airOS 8) ===")
    srv = start(8, 19102)
    d = srv.dev
    app, ui, dev = app_for(19102)

    app.working.set("radio.1.txpower", "abc")
    ok, log, _ = act(app, [], app.apply, True)
    check("with a value the web does not let you save, it sends nothing", ok
          and tr("The power must be a whole number in dBm.") in shown(log, tr("Cannot apply"))
          and d.test_until is None)
    app.set_loaded(app.original_text)
    app.wait = None
    check("initial wait (airOS 8): 4 s if it announces a fast restart; otherwise 15 s",
          app._initial_wait({"fast_restart": True}) == 4 and app._initial_wait({}) == 15)
    app.wait = 1
    check("wait for the device: it gives up past the maximum",
          app.wait_for_device("x", 0, max_wait=0) is False)
    dev.retarget("127.0.0.2")                       # nothing answers there
    check("wait for the device: it finds it at the previous IP and stays there",
          app.wait_for_device("x", 0, max_wait=10, alt="127.0.0.1") and dev.host == "127.0.0.1")

    def power(value):
        """The device reports this power, whatever was sent ("it did not keep it")."""
        def change(status, hdrs, body):
            return status, hdrs, re.sub(rb"(?m)^radio\.1\.txpower=.*$",
                                        f"radio.1.txpower={value}".encode(), body)
        return change
    p = int(app.original.get("radio.1.txpower"))
    app.working.set("radio.1.txpower", str(p - 1))
    with faults(dev, {"/getcfg.cgi": power(p)}):
        ok, log, _ = act(app, [("yesno", 0)], app.apply, False)
    check("apply and save: warns about the keys that did not keep their value", ok and tr(
        "The device answered, but these keys did not keep the value sent:\n  {keys}",
        keys="radio.1.txpower") in shown(log, tr("Attention")), str(log[-1:]))
    settle(srv)
    app.load()
    p = int(app.original.get("radio.1.txpower"))
    app.working.set("radio.1.txpower", str(p - 1))
    with faults(dev, {"/getcfg.cgi": power(p)}):
        ok, log, _ = act(app, [("yesno", 0), ("yesno", 1)], app.apply, True)
    q = shown(log, tr("Test mode"), "yesno")
    check("test mode: the decision warns about the keys that did not keep their value", ok
          and tr("⚠ These keys did not keep the value sent:") + "\n  radio.1.txpower" in q, q)
    check('test mode: "Revert", and the change is still pending', tr("Reverted") in titles(log)
          and app.changes() == [("radio.1.txpower", str(p), str(p - 1))], str(app.changes()))
    settle(srv)

    print("=== The endings of test mode ===")
    ok, log, _ = act(app, [], app._decide_test, app.changes(), [], time.monotonic())
    check("the test had already ended when deciding: it says so; the change is still pending", ok
          and shown(log, tr("Test mode")).startswith(tr(
              "The device is no longer in test mode: the time probably ran out and it went "
              "back to the previous configuration.")) and len(app.changes()) == 1)
    other = T.AirOS("http://127.0.0.1:19102", "ubnt", "ubnt")
    other.connect()
    ok, log, _ = act(app, [("yesno", 0), ("yesno", lambda: (other.discard_test(), 1)[1])],
                     app.apply, True)
    check('"Revert" when another session already reverted it: it says so', ok
          and shown(log, tr("Reverted")).startswith(tr("The device had already gone back to "
                                                       "the previous configuration."))
          and len(app.changes()) == 1, str(titles(log)))
    settle(srv)
    with patched(dev, confirm_test=lambda: None):          # the device ignores it
        ok, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
    check('"Keep" with no effect: it warns that it is still in test mode', ok
          and shown(log, tr("Attention"))
          == tr("The device is still in test mode. Check the web."))
    discard(srv, dev)
    with patched(dev, confirm_test=dev.discard_test):      # it went back just before
        ok, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
    check('"Keep" just too late: it warns about what did not stay, and it is still pending', ok
          and "radio.1.txpower" in shown(log, tr("Attention")) and len(app.changes()) == 1
          and d.test_until is None, str(log[-1:]))
    settle(srv)
    calls = []
    real_wait = app.wait_for_device

    def first_only(*a, **k):
        calls.append(a[0])
        return real_wait(*a, **k) if len(calls) == 1 else False
    with patched(app, wait_for_device=first_only):
        ok, log, _ = act(app, [("yesno", 0), ("yesno", 1)], app.apply, True)
    check('"Revert" and it does not answer again: it says so', ok
          and shown(log, tr("No answer")) == tr(
              "The device does not answer after reverting (I looked for it at {host}).",
              host="127.0.0.1"))
    settle(srv)

    def refuse():
        raise T.AirOSError("discard: HTTP 500")
    with patched(dev, discard_test=refuse):               # the device refuses it
        ok, log, _ = act(app, [("yesno", 0), ("yesno", 1)], app.apply, True)
    check('"Revert" refused by the device: not "Reverted" but that it is still in test mode, and '
          "why; the change is still pending", ok and tr("Reverted") not in titles(log)
          and shown(log, tr("Attention")) == tr(
              "The device did not go back to the previous configuration: it is still in test "
              "mode ({error}).", error="discard: HTTP 500")
          and len(app.changes()) == 1 and d.test_until is not None, str(log[-2:]))
    discard(srv, dev)
    app._follow_ctx = {"old": "10.9.9.9", "new": "127.0.0.1"}     # (an earlier test's)
    ok, log, _ = act(app, [], app.pending_test)
    check("no test any more: where an earlier one moved it is forgotten (a later test does not "
          "send the program back there)", ok and app._follow_ctx is None)
    macs = app._device_macs()
    answers = {}
    for name, found in (("other", [{"macs": {"AABBCCDDEEFF"}}]), ("this", [{"macs": set(macs)}]),
                        ("silent", [])):
        with patched(T, discover=lambda *a, f=found, **k: f):
            answers[name] = app._another_device("10.9.9.9", macs)
    check("another device at its previous address: only if discovery says so (another MAC); not "
          "this one, nor when discovery says nothing (turned off)", bool(macs)
          and answers == {"other": True, "this": False, "silent": False}, str(answers))
    d.running["users.1.name"] = "boss"                    # (a test that only renamed it, kept)
    try:
        which = (app._which_admin("ubnt", "ubnt"), app._which_admin("ubnt", "boss"),
                 dev.username)
    finally:
        d.running["users.1.name"], dev.username = "ubnt", "ubnt"
        dev.login()
    check("a test that only renamed its administrator, ended as it was decided: the name it "
          "takes says what it kept, and the program logs in with it",
          which == ("same", "new", "boss"), str(which))

    def gone(*a, **k):
        raise T.AirOSError("no connection (simulated)")
    with patched(dev, test_mode_info=gone), patched(app, wait_for_device=lambda *a, **k: False):
        ok, log, _ = act(app, [("yesno", 0)], app.resolve_test, "Keep?", None,
                         app.changes())
    check('deciding without being able to query the device: "No answer" (and its changes are '
          "still pending)",
          ok and shown(log, tr("No answer")) == tr("The device does not answer at {address}.",
                                                   address=dev.base) + "\n" + tr(
              "Your changes are still pending in case you want to try again."))
    with patched(app, wait_for_device=lambda *a, **k: False):
        ok, log, _ = act(app, [("yesno", 0)], app.apply, True)
    text = shown(log, tr("No answer"))
    check("apply in test mode, no answer: it goes back by itself; it says where the backup is", ok
          and tr("It was in TEST MODE: if it is not confirmed, it goes back to the previous "
                 "configuration by itself in a few minutes.") in text
          and T.BACKUP_DIR in text and len(app.changes()) == 1, text)
    discard(srv, dev)

    print("=== If the IP you connect through changes ===")
    moved = {"old": "127.0.0.1", "new": "127.0.0.9", "mode": "static", "macs": set(),
             "candidates": []}
    for test in (True, False):
        with patched(app, _follow_plan=lambda *a, **k: dict(moved),
                     _follow_device=lambda *a, **k: False):
            ok, log, _ = act(app, [("yesno", 0)], app.apply, test)
        text = shown(log, tr("No answer"))
        want = (tr("It was in TEST MODE: if it is not confirmed, it goes back to {old} by itself "
                   "in a few minutes. The program already points there.", old="127.0.0.1")
                + "\n" + tr('To keep it, connect airos-tui to its new address from a network '
                            'that reaches it, before that time runs out, and use "Test mode in '
                            'progress" there.')
                if test else tr("The configuration is already saved: connect to the new "
                                "address from a network that reaches it."))
        check(f"not found at its new IP ({'test' if test else 'saved'}): it explains",
              ok and tr("I could not reach the antenna at its new address: {target}.",
                        target="127.0.0.9") in text and want in text
              and dev.host == "127.0.0.1", text)
        if test:
            discard(srv, dev)
        settle(srv)
    app.load()
    app.working.set("radio.1.txpower", "10")
    for cands, want in (
            (["127.0.0.1"], tr("⚠ If your PC stays on the same side, the antenna will stay at "
                               "{old}; if it does not answer there, I will look for it by its "
                               "MAC.", old="127.0.0.1")),
            ([], tr("⚠ The antenna leaves {old}: I will look for it by its MAC.",
                    old="127.0.0.1"))):
        plan = {"old": "127.0.0.1", "new": None, "mode": "netmode", "macs": set(),
                "candidates": cands}
        with patched(app, _follow_plan=lambda *a, p=plan, **k: dict(p)):
            ok, log, _ = act(app, [("yesno", 1)], app.apply, True)
        check(f"network mode change ({'stays' if cands else 'leaves the IP'}): it warns first",
              ok and want in shown(log, tr("Test changes"), "yesno") and d.test_until is None)
    F = T.App._follow_target
    cases = [
        ("netmode", ["10.0.0.2"], False, tr("the IP it has in the new mode (I did not find it)")),
        ("netmode", ["10.0.0.2"], True, tr("the IP it has in the new mode (I will look for it "
                                           "by its MAC and at {where})", where="10.0.0.2")),
        ("netmode", [], True, tr("the IP it has in the new mode (I will look for it by its MAC)")),
        ("dhcp", ["10.0.0.3"], False, tr("the IP DHCP gives it (I did not find it)")),
        ("dhcp", [], True, tr("the IP DHCP gives it (I will look for it by its MAC)")),
        ("pppoe", [], False, tr("the IP it gets with PPPoE (I did not find it)")),
        ("pppoe", [], True, tr("the IP it gets with PPPoE (I will look for it by its MAC)"))]
    bad = [(m, c, f) for m, c, f, want in cases
           if F({"new": None, "mode": m, "candidates": c}, found=f) != want]
    check("where it looks for it, in each case (network mode, DHCP, PPPoE)", not bad, str(bad))

    print('=== "Test mode in progress" ===')
    app.set_loaded(app.original_text)
    ok, log, _ = act(app, [], app.pending_test)
    check("no test in progress: it says so", ok
          and shown(log, tr("Test mode")) == tr("This device has no changes in test mode."))
    c = T.Config(other.get_config())
    c.set("wireless.1.ssid", "Other-Session")
    other.write_config(c, test=True)
    settle(srv)
    app.load()
    ok, log, _ = act(app, [("yesno", 0)], app.pending_test)
    check('a test by another session: "Keep" saves it', ok and tr("Done") in titles(log)
          and d.saved.get("wireless.1.ssid") == "Other-Session" and not app.changes(),
          str(titles(log)))
    srv.shutdown()


# ------------------------------------------------------------------------------------------
def network():
    print("=== Network in bridge mode: DNS, automatic IP and STP ===")
    srv = start(8, 19103)
    app, ui, dev = app_for(19103)
    ok, log, _ = act(app, [("menu", "3"), ("input", "1.2.3"), ("input", "9.9.9.9"),
                           ("input", ""), ("menu", None)], app.network_menu)
    dns = T.dns_servers(app.working)
    check("DNS: it refuses an invalid IP; primary without secondary", ok
          and titles(log) == [tr("Invalid value")] and dns[0] == "9.9.9.9" and not dns[1],
          str(dns))
    ok, log, _ = act(app, [("menu", "3"), ("input", None), ("menu", None)], app.network_menu)
    check("DNS: cancel", ok and T.dns_servers(app.working) == dns)
    autoip = T.main_iface(app.working, "v8")["autoip"]
    ok, log, _ = act(app, [("menu", "5"), ("yesno", 0), ("menu", "6"), ("yesno", 0),
                           ("menu", None)], app.network_menu)
    check("automatic IP 169.254.x.x: it changes", ok
          and titles(log, "yesno")[0] == tr("Automatic IP")
          and T.main_iface(app.working, "v8")["autoip"] != autoip)
    check("STP on the bridge: it is enabled", app.working.get("bridge.1.stp.status") == "enabled")
    srv.shutdown()

    print("=== Network in router mode: fallback IP, DNS, LAN and PPPoE ===")
    srv = start(8, 19104, mode="router")
    d = srv.dev
    app, ui, dev = app_for(19104)
    ok, log, _ = act(app, [("menu", "4"), ("input", "192.168.10.2"), ("input", "255.0.255.0"),
                           ("input", "23"), ("menu", None)], app.network_menu)
    check("fallback IP: it refuses an invalid netmask; it saves IP and netmask, and says whether "
          "the address it is reached at moves (as the other IPs do)", ok
          and titles(log) == [tr("Invalid value"), tr("Fallback IP")]
          and tuple(T.dhcp_fallback(app.working, "eth0")) == ("192.168.10.2", "255.255.254.0"),
          str(titles(log)))
    base = app.working.text()
    ok, log, _ = act(app, [("menu", "4"), ("input", None), ("menu", "4"),
                           ("input", "192.168.10.3"), ("input", None), ("menu", None)],
                     app.network_menu)
    check("fallback IP: cancel the IP or the netmask", ok and app.working.text() == base)
    ok, log, _ = act(app, [("input", ""), ("input", "1.1.1.1"), ("input", "")], app.edit_dns, True)
    check("required DNS (WAN with a static IP): the primary cannot be left empty", ok
          and titles(log) == [tr("Invalid value")]
          and T.dns_servers(app.working)[0] == "1.1.1.1")
    ok, log, r = act(app, [("input", None)], app._ask_ip, "IP", "IP:")
    check("ask for an IP: cancel", ok and r is None)
    ok, log, r = act(app, [("input", "0.0.0.0"), ("input", "10.1.2.300"), ("input", " 10.1.2.3 ")],
                     app._ask_ip, "IP", "IP:")
    check("ask for an IP: it refuses 0.0.0.0 and invalid ones, and asks again", ok
          and r == "10.1.2.3" and titles(log) == [tr("Invalid value")] * 2)
    ok, log, r = act(app, [("input", None)], app._ask_mask, "Netmask", "255.255.255.0")
    check("ask for a netmask: cancel", ok and r is None)
    app.set_loaded(app.original_text)

    wan = T.main_iface(app.working, "v8")
    base = app.working.text()
    stops = []
    for full in ([("menu", "1"), ("input", "192.168.20.2"), ("input", "24"),
                  ("input", "192.168.20.1"), ("input", "1.1.1.1"), ("input", "")],
                 [("menu", "2"), ("input", "192.168.10.3"), ("input", "24")]):
        for i, (kind, _) in enumerate(full):
            ok, log, _ = act(app, full[:i] + [(kind, None)], app.edit_main_ip, wan)
            stops.append(ok and app.working.text() == base)
    check("WAN with a static IP or DHCP: cancelling at any step changes nothing", all(stops),
          str(stops))

    lan = T.lan_ifaces(app.working)[0]
    full = [("input", "192.168.5.1"), ("input", "24"), ("yesno", 0), ("input", "192.168.5.100"),
            ("input", "192.168.5.200"), ("input", "600")]
    base = app.working.text()
    stops = []
    for i, (kind, _) in enumerate(full):
        ok, log, _ = act(app, full[:i] + [(kind, 255 if kind == "yesno" else None)],
                         app.edit_lan, lan)
        stops.append(ok and app.working.text() == base)
    check("LAN: cancelling at any step changes nothing", all(stops), str(stops))
    ok, log, _ = act(app, full[:5] + [("input", "ten"), ("input", "119"), ("input", "172801"),
                                      ("input", "7200")], app.edit_lan, lan)
    dhcp = T.dhcp_server(app.working, lan["devname"]) or {}
    check("LAN: the lease time, from 120 to 172800 s as its web: it asks again, without losing "
          "what was typed", ok and titles(log)[:3] == [tr("Invalid value")] * 3
          and shown(log, tr("Invalid value")) == tr(
              '"{value}" is not valid: type a whole number from {lo} to {hi}.', value="ten",
              lo=120, hi=172800)
          and T.lan_ifaces(app.working)[0]["ip"] == "192.168.5.1"
          and (dhcp.get("start"), dhcp.get("end"), dhcp.get("lease"))
          == ("192.168.5.100", "192.168.5.200", "7200"), str((titles(log), dhcp)))
    app.set_loaded(app.original_text)
    ok, log, _ = act(app, full, app.edit_lan, lan)
    new = T.lan_ifaces(app.working)[0]
    dhcp = T.dhcp_server(app.working, new["devname"]) or {}
    check("LAN: IP, netmask and the DHCP range", ok and new["ip"] == "192.168.5.1"
          and (dhcp.get("start"), dhcp.get("end"), dhcp.get("lease"))
          == ("192.168.5.100", "192.168.5.200", "600"), str(dhcp))
    app.set_loaded(app.original_text)

    wan = T.main_iface(app.working, "v8")
    full = [("input", "customer@isp"), ("password", "secret"), ("input", ""), ("input", "1492"),
            ("input", "1492"), ("yesno", 1), ("input", "192.168.10.1"), ("input", "24")]
    stops = []
    for i, (kind, _) in enumerate(full):
        ok, log, _ = act(app, full[:i] + [(kind, 255 if kind == "yesno" else None)],
                         app.edit_pppoe, wan)
        stops.append(ok and app.working.text() == base)
    check("PPPoE: cancelling at any step changes nothing", all(stops), str(stops))
    ok, log, _ = act(app, full, app.edit_pppoe, wan)
    check("PPPoE: the WAN goes from DHCP to PPPoE", ok
          and T.ip_mode(app.working, wan["devname"]) == "pppoe")
    device_text = app.original_text
    app.set_loaded(app.working.text())                  # as if it were already applied
    again = [("input", "customer@isp"), ("password", "")] + full[2:]    # "" = keep it
    with patched(app, _live_ip=lambda name: app.dev.host if name == "ppp0" else ""):
        ok, log, _ = act(app, again, app.edit_pppoe, T.main_iface(app.working, "v8"))
    text = shown(log, tr("WAN ({dev}) over PPPoE", dev=wan["devname"]))
    check("PPPoE: if you connect through the session IP, it warns that it may change", ok and tr(
        "You are connected through the PPPoE session IP ({ip}): when applying, the session "
        "reconnects and your provider could give it another IP.", ip="127.0.0.1") in text
        and not app.changes(), text)
    app.set_loaded(device_text)
    with faults(dev, {"/api/pppoe/info": E500}):
        ok, log, _ = act(app, [], app.pppoe_status)
    text = shown(log, tr("PPPoE connection"))
    check("PPPoE status: if the device fails, it shows the error", ok and "500" in text
          and "boom" in text, text)
    with patched(app, _live_ip=lambda name: app.dev.host):
        ok, log, _ = act(app, [("yesno", 1)], app.pppoe_reconnect)
    check('reconnect PPPoE: it warns if you connect through that IP; "No" cuts nothing', ok
          and "(127.0.0.1)" in shown(log, tr("Reconnect PPPoE"), "yesno")
          and "pppoe-restart" not in d.events)
    with faults(dev, {"/api/pppoe/restart": (200, b'{"error": "busy"}')}):
        ok, log, _ = act(app, [("yesno", 0)], app.pppoe_reconnect)
    check("reconnect PPPoE: if the device cannot, it says so", ok
          and "busy" in shown(log, tr("Reconnect PPPoE")) and "pppoe-restart" not in d.events)
    srv.shutdown()


# ------------------------------------------------------------------------------------------
def client_errors():
    print("=== Client: device errors (airOS 8) ===")
    srv = start(8, 19105)
    dev = T.AirOS("http://127.0.0.1:19105", "ubnt", "ubnt")
    dev.connect()
    cfg = T.Config(dev.get_config())
    for name, rules, call in (
            ("status", {"/status.cgi": E500}, dev.status),
            ("read the configuration", {"/getcfg.cgi": E500}, dev.get_config),
            ("write", {"/writecfg.cgi": E500}, lambda: dev.write_config(cfg, test=True)),
            ("confirm the test", {"/apply.cgi": E500}, dev.confirm_test),
            ("revert the test", {"/discard.cgi": E500}, dev.discard_test),
            ("PPPoE status", {"/api/pppoe/info": E500}, dev.pppoe_info),
            ("reconnect PPPoE", {"/api/pppoe/restart": E500}, dev.pppoe_restart),
            ("reboot", {"/reboot.cgi": E500}, dev.reboot),
            ("change the country", {"/api/regdomain/country": E500}, lambda: dev.set_country(32))):
        with faults(dev, rules):
            e = raises(call)
        check(f"{name}: HTTP 500 -> an error with what it answered", e is not None
              and ("500" in str(e) or "boom" in str(e)), repr(e))
    e = raises(dev.set_country, 999)
    check("change to a country the device does not know: its error", e is not None
          and str(e) == tr("The device did not change the country: {error}",
                           error="Invalid country code") and srv.dev.saved.get(
              "radio.1.countrycode") == cfg.get("radio.1.countrycode"))
    with faults(dev, {"/test_mode.cgi": E500}):
        check('test mode with HTTP 500: "unknown" (None)', dev.test_mode_info() is None)
    with faults(dev, {"/api/regdomain": E500}):
        check("channels of another country with HTTP 500: None", dev.regdomain(840, 20) is None)
    with faults(dev, {"/api/info/public": (403, b"")}):
        check("list of countries without permission: empty", dev.countries() == [])
    with faults(dev, {"/writecfg.cgi": (200, b"ok")}):
        check("write: an answer that is not JSON counts as done",
              dev.write_config(cfg, test=False) == {})
    with faults(dev, {"/status.cgi": (403, b"")}):
        e = raises(dev.status)
    check("read-only user: it logs in again once, then AuthError",
          isinstance(e, T.AuthError)
          and str(e) == tr("The device rejected the request (read-only user?)."), repr(e))
    with faults(dev, {"/api/auth": (200, b'{"error": "blocked"}')}):
        e = raises(dev.login)
    check("log in: the device refuses it with a reason", isinstance(e, T.AuthError)
          and str(e) == tr("The device refused access: {error}", error="blocked"))
    with faults(dev, {"/api/auth": (401, b"{}")}):
        e = raises(dev.login)
    check("log in: wrong user or password, with no detail", isinstance(e, T.AuthError)
          and str(e) == tr("Wrong user or password."))
    with faults(dev, {"/api/auth": (200, b'{"ok": true}')}):      # no X-CSRF-ID
        e = raises(dev.login)
    check('log in: neither /api/auth nor a session cookie -> "does not answer like an airOS"',
          e is not None and str(e) == tr("The device did not answer like an airOS (it gave no "
                                         "session cookie)."), repr(e))
    dev.login()

    def fw(version):
        def change(status, hdrs, body):
            info = json.loads(body)
            info["host"]["fwversion"] = version
            return status, hdrs, json.dumps(info).encode()
        return change
    for version, flavor, note in (
            ("v7.2.4", "v8", tr("airOS 7 is not tested: the airOS 8 paths are used.")),
            ("v5.6.15", "v6", tr("airOS 5 or older is not tested: the airOS 6 paths are used.")),
            ("XC.ar", "v8", tr("The firmware version was not recognized ({fw!r}).", fw="XC.ar"))):
        other = T.AirOS("http://127.0.0.1:19105", "ubnt", "ubnt")
        with faults(other, {"/status.cgi": fw(version)}):
            other.connect()
        check(f"firmware {version}: the {flavor} paths and a notice", other.flavor == flavor
              and other.notes == [note], str(other.notes))
    for target, want in (("http://", tr("Invalid address: {target!r}", target="http://")),
                         ("127.0.0.1:99999", tr("Invalid port in {target!r}",
                                                target="127.0.0.1:99999")),
                         ("127.0.0.1:abc", tr("Invalid port in {target!r}",
                                              target="127.0.0.1:abc"))):
        e = raises(T.AirOS, target, "ubnt", "ubnt")
        check(f"address {target!r}: refused", e is not None and str(e) == want, repr(e))
    srv.shutdown()

    print("=== Client: device errors (airOS 6) ===")
    srv = start(6, 19106)
    dev = T.AirOS("http://127.0.0.1:19106", "ubnt", "ubnt")
    dev.connect()
    cfg = T.Config(dev.get_config())
    for name, rules, call in (
            ("confirm the test", {"/apply.cgi": E500}, dev.confirm_test),
            ("revert the test", {"/discard.cgi": E500}, dev.discard_test),
            ("PPPoE status", {"/pppinfo.cgi": E500}, dev.pppoe_info),
            ("upload the configuration", {"POST /system.cgi": E500},
             lambda: dev.write_config(cfg, test=True))):
        with faults(dev, rules):
            e = raises(call)
        check(f"{name}: HTTP 500 -> error", e is not None and "500" in str(e), repr(e))
    with faults(dev, {"/system.cgi": E500}):
        check('test mode with HTTP 500: "unknown" (None)', dev.test_mode_info() is None)
    with faults(dev, {"POST /reboot.cgi": (403, b"")}):
        e = raises(dev.reboot)
    check("reboot without permission: it logs in again once, then AuthError",
          isinstance(e, T.AuthError) and "reboot" not in srv.dev.events, repr(e))
    app = T.App(FakeUI(), dev, wait=None)
    app.load()
    ok, log, _ = act(app, [], app.save_backup)
    check("save a backup (airOS 6): without the note about the passwords", ok
          and "\n" not in shown(log, tr("Backup saved")))
    check("initial wait (airOS 6): 20 s", app._initial_wait({}) == 20)
    fresh = T.AirOS("http://127.0.0.1:19106", "ubnt", "ubnt")
    fresh.connect()
    with faults(fresh, {"GET /login.cgi": lambda s, h, b: (fresh.cookies.clear(), (s, h, b))[1]}):
        e = raises(fresh.login)
    check('log in without a session cookie: "does not answer like an airOS"', e is not None
          and str(e) == tr("The device did not answer like an airOS (it gave no session "
                           "cookie)."), repr(e))
    with faults(fresh, {"GET /login.cgi": lambda s, h, b: (s, h, b + b"\nfirst_login : true\n"),
                        "POST /login.cgi": (200, b"")}):
        e = raises(fresh.login)
    check("log in on its first boot: it explains what to do", isinstance(e, T.AuthError)
          and str(e) == tr("The device is on its first boot. Log in once through the web to "
                           "choose the country and accept the terms, and try again."), repr(e))
    cfg.set("radio.1.txpower", "11")
    with faults(dev, {"/apply.cgi": E500}):
        e = raises(dev.write_config, cfg, True)
    check("uploaded fine but it cannot apply: an error", e is not None
          and str(e) == tr("{path} answered HTTP {status}.", path="apply.cgi", status=500),
          repr(e))
    srv.shutdown()


# ------------------------------------------------------------------------------------------
def main_loop():
    print("=== Main menu: notices, errors of the actions, and exit ===")
    srv = start(8, 19107)
    d = srv.dev
    app, ui, dev = app_for(19107)
    dev.notes = ["Test note"]
    undo = []

    def pick(tag, rules=None, then=None):
        """The answer to the main menu; it first changes how the device answers."""
        def answer():
            while undo:
                undo.pop()()
            if rules:
                undo.append(inject(dev, rules))
            if then:
                then()
            return tag
        return answer
    script = [("menu", pick("1", {"/status.cgi": (403, b"")})),
              ("menu", pick("y", {"/reboot.cgi": (500, b"no")},
                            then=lambda: app.working.set("radio.1.txpower", "12"))),
              ("menu", "r"), ("yesno", 0),
              ("menu", pick("0")), ("yesno", 1),
              ("menu", pick(None)), ("yesno", 0)]
    ok, log, _ = act(app, script, app.run)
    while undo:
        undo.pop()()
    check("at start it shows the connection's notices", shown(log, tr("Notice")) == "Test note")
    check('an action without permission: "Access denied", and it stays in the menu',
          shown(log, tr("Access denied")) == tr("The device rejected the request (read-only "
                                                "user?)."))
    check('an action that fails (reboot, from System): "Error" with what the device answered',
          "reboot.cgi" in shown(log, "Error") and "reboot" not in d.events, shown(log, "Error"))
    check('exit with unapplied changes: it asks; "No" goes back to the menu', ok
          and titles(log, "yesno").count(tr("Exit")) == 2)
    srv.shutdown()


# ------------------------------------------------------------------------------------------
def first_setup():
    print("=== First setup (airOS 8 after a reset, as its web) ===")
    steps = T.setup_steps
    check("its steps, as LoginViewMain.collectSteps: waiting for it, both; done, none; done but "
          "with the factory password, the user; without that information, none",
          steps({"default_password": True, "setup_data": {}}) == (True, True)
          and steps({"setup_complete": True}) == (False, False)
          and steps({"setup_complete": True, "default_password": True}) == (False, True)
          and steps({"setup_data": {}}) == (True, False)
          and steps({"ui_lang": "en_US"}) == steps(None) == (False, False))
    srv = start(8, 19109, first_setup=True)
    target = "127.0.0.1:19109"
    args = types.SimpleNamespace(device=None, user="Operator", http=True, https=False,
                                 target=None)
    cc = next(c for c in S.COUNTRIES if c != 484)
    ui = FakeUI()
    ok, log, dev = act(ui, [("input", target), ("menu", str(cc)), ("menu", "sp_SP"),
                            ("yesno", 1), ("input", None)], T.connect_screen, ui, args)
    check('"Cancel" on its terms: it sends nothing and asks for the address again', ok
          and dev is None and srv.dev.setup_pending and "first-setup" not in srv.dev.events)
    weak, good = "short", "Secure-Phrase-2026"
    ok, log, dev = act(ui, [("input", target), ("menu", str(cc)), ("menu", "sp_SP"),
                            ("yesno", 0), ("menu", "new"), ("input", "Operator"),
                            ("password", weak),
                            ("password", good), ("password", good + "!"), ("password", good),
                            ("password", good)], T.connect_screen, ui, args)
    terms = shown(log, tr("Please Set Up Your Device"), "yesno")
    errors = [t for k, ti, t in log if k == "msgbox" and ti == tr("Invalid value")]
    check("waiting for it: country, language and terms; then user and password (twice), with "
          "the rules of its web; it never asks for the usual user and password", ok
          and dev is not None and "(simulated terms of use)" in terms
          and tr("TERMS OF USE") in terms and tr(T.LFUA_NOTICE) not in terms
          and errors == [tr("Use at least 12 characters."), tr("Passwords should match.")]
          and tr("User:") not in [t for k, ti, t in log if k == "input"]
          and not shown(log, tr("Connect"), "password"),
          str((ok, titles(log), errors)))
    check("  and it sets it up as its web does (a POST to /api/auth): its country, its language "
          "and the new user; the program goes on with it, and the factory password no longer works",
          dev is not None and not srv.dev.setup_pending and "first-setup" in srv.dev.events
          and srv.dev.running["radio.countrycode"] == str(cc)
          and srv.dev.running["gui.language"] == "sp_SP"
          and srv.dev.running["users.1.name"] == "Operator" and srv.dev.password == good
          and dev.username == "Operator" and dev.flavor == "v8",
          str((srv.dev.running.get("radio.countrycode"), srv.dev.running.get("users.1.name"))))
    again = T.AirOS(f"http://{target}", "ubnt", "ubnt")
    try:
        again.connect()
        refused = False
    except T.AuthError:
        refused = True
    check("  (ubnt/ubnt no longer logs in)", refused)
    srv.shutdown()
    info = {"setup_data": {"rd_countries": [{"code": 511, "name": "Licensed"},
                                            {"code": 32, "name": "Argentina"}],
                           "license_agreement": "Terms."}, "default_password": True}
    ui = FakeUI([("menu", "511"), ("yesno", 1)])
    T.first_setup_screens(ui, info, True, True, None)
    licensed = [t for k, ti, t in ui.log if k == "yesno"]
    ui = FakeUI([("menu", "32"), ("yesno", 1)])
    T.first_setup_screens(ui, info, True, True, None)
    plain = [t for k, ti, t in ui.log if k == "yesno"]
    check('a "licensed" country: with the LFUA notice of its web; another one, without it; with '
          "no list of languages, the language of its web, without asking",
          licensed and tr(T.LFUA_NOTICE) in licensed[0]
          and plain and tr(T.LFUA_NOTICE) not in plain[0])
    first_setup_backup()


def first_setup_backup():
    print('=== First setup from a backup (airOS 8: "Upload Backup Configuration") ===')
    owner, key = "Operator", "Secure-Phrase-2026"
    # (with web_follow its web answers on the port of its configuration, as a real one does: the
    # backup's is this one's, 19141, and the other one moves there when applying it)
    src = start(8, 19141, user=owner, password=key, mgmt_ip="127.0.0.141", web_follow=True)
    backup = src.dev.backup_file()                  # (the one of its web: cfg.cgi)
    src.shutdown()
    src.server_close()
    folder = os.path.join(TMP, "first-setup-backups")
    os.makedirs(folder, exist_ok=True)
    files = {"good": backup, "nothing": "hello\n",
             "airos-tui": re.sub(r"(?m)^(users\.\d+\.password)=.*$", r"\1=-", backup),
             "airos6": backup.replace(f"##WA.{src.dev.fw_version}", "##XW.v6.3.16")}
    for name, text in files.items():
        with open(os.path.join(folder, name + ".cfg"), "w", encoding="utf-8") as f:
            f.write(text)
    path = {name: os.path.join(folder, name + ".cfg") for name in files}

    dst = start(8, 19142, mgmt_ip="127.0.0.142", first_setup=True, web_follow=True)
    args = types.SimpleNamespace(device=None, user=None, http=True, https=False, target=None)
    cc = next(c for c in S.COUNTRIES if str(c) != src.dev.running.get("radio.countrycode"))
    ui = FakeUI()
    ok, log, dev = act(ui, [("input", "127.0.0.142:19142"), ("menu", str(cc)), ("menu", "sp_SP"),
                            ("yesno", 0), ("menu", "backup"),
                            ("menu", "o"), ("input", path["airos-tui"]),
                            ("menu", "o"), ("input", path["nothing"]),
                            ("menu", "o"), ("input", path["good"])], T.connect_screen, ui, args)
    ways = [items for title, items, _ in ui.menus if title == tr("Please Set Up Your Device")
            and [k for k, _ in items] == ["new", "backup"]]
    errors = [t for k, ti, t in log if k == "msgbox" and ti == "Error"]
    check("after the country and its terms, as its web: a new user or a backup; one of "
          'airos-tui without passwords (they would be "-") or another file is not sent',
          ok and ways
          and errors == [tr(T.SETUP_BACKUP_MASKED),
                         tr("That file does not look like an airOS system.cfg.")]
          and tr("Create Your Username and Password") not in titles(log, "input"),
          str((ok, errors, titles(log, "input"))))
    system = dst.dev.system
    check("  it sends it as its web does (/api/auth: device_setup=first, the country, the "
          "language and the backup): it waits to be applied, with that country and language, "
          "and meanwhile the program logs in with the factory user", dev is not None
          and dst.dev.backup_loaded
          and "first-setup-backup" in dst.dev.events and system["users.1.name"] == owner
          and system["radio.countrycode"] == system["radio.1.countrycode"] == str(cc)
          and system["gui.language"] == "sp_SP" and dst.dev.running["users.1.name"] == "ubnt"
          and dev.username == "ubnt" and not dst.dev.setup_pending,
          str((dev and dev.username, system.get("users.1.name"), system.get("radio.countrycode"))))

    app = T.App(FakeUI(), dev, wait=1)
    app.discovery = {"target": "127.0.0.1", "port": 19143}      # (nobody answers)
    app.load()
    plan = app._backup_plan(app.original)
    local = plan is not None and all(ip.startswith("127.") for ip in plan["candidates"])
    check("  on starting, the notice of its web (a backup waiting); where it will end up: at the "
          "IPs of the backup (only 127.x here)", dev.backup_loaded and local
          and plan["candidates"] == ["127.0.0.141"], str(plan))
    if local:                                       # (nothing outside this computer)
        app.ui.script = [("yesno", 0), ("input", owner), ("password", key), ("menu", None)]
        app.ui.log.clear()
        app.run()
        done = [t for k, ti, t in app.ui.log if k == "msgbox"
                and ti == tr("Applying the backup")]
        asked = [t for k, ti, t in app.ui.log if k == "input" and ti == tr("Applying the backup")]
        check('  "Apply": it uses it and saves it, the program follows it to its IP and, as the '
              "backup has another password, it asks for it (as its login page does) and goes "
              "on with it",
              not app.ui.script and "backup-applied" in dst.dev.events
              and dst.dev.running["users.1.name"] == owner
              and (app.dev.host, app.dev.port) == ("127.0.0.141", 19141)
              and (app.dev.username, app.dev.password) == (owner, key) and len(asked) == 1
              and app.ui.inputs[-1] == owner and len(done) == 1
              and done[0].startswith(tr("Backup applied.")), str((app.ui.script, done)))
    dst.shutdown()

    dst = start(8, 19144, factory_password=True)
    ui = FakeUI()
    ok, log, dev = act(ui, [("input", "127.0.0.1:19144"), ("menu", "backup"), ("menu", "o"),
                            ("input", path["airos6"]), ("menu", "backup"), ("menu", "o"),
                            ("input", path["good"])], T.connect_screen, ui, args)
    refused = [t for k, ti, t in log if k == "msgbox" and ti == tr("Please Set Up Your Device")]
    check("set up already but with the factory password: its web offers it where it asks for the "
          "new user (device_setup=cfg); what the device refuses is said, and it starts over", ok
          and dev is not None and refused == [tr(
              "The device did not take its first setup: {error}", error="Uploaded configuration "
              "file is not compatible with current firmware version")]
          and [title for title, items, _ in ui.menus if [k for k, _ in items] == ["new", "backup"]]
          == [tr("Create Your Username and Password")] * 2
          and dst.dev.system["users.1.name"] == owner and dst.dev.backup_loaded
          and dst.dev.system["radio.countrycode"] == src.dev.running["radio.countrycode"],
          str((ok, refused, ui.menus[-1][0])))
    dst.shutdown()


def startup():
    print("=== Connection screen ===")
    srv = start(8, 19108)
    target = "127.0.0.1:19108"
    args = types.SimpleNamespace(device=None, user=None, http=True, https=False, target=None)
    ui = FakeUI()
    searched = []
    with patched(T, discovery_screen=lambda ui, where, **kw: searched.append(where)):
        ok, log, dev = act(ui, [("input", ""), ("input", None)], T.connect_screen, ui, args)
    check("empty address: it searches your networks; with nothing chosen, it asks again", ok
          and dev is None and searched == [T.BROADCAST])
    ok, log, dev = act(ui, [("input", target), ("input", "")], T.connect_screen, ui, args)
    check("empty user: it exits", ok and dev is None)
    ok, log, dev = act(ui, [("input", target), ("input", "ubnt"), ("password", None)],
                       T.connect_screen, ui, args)
    check("cancel the password: it exits", ok and dev is None)
    ok, log, dev = act(ui, [("input", target), ("input", "ubnt"), ("password", "wrong"),
                            ("input", "ubnt"), ("password", "ubnt")], T.connect_screen, ui, args)
    check('wrong password: "Could not log in", and it asks again for the user (the previous one, '
          "in case it was right) and the password", ok and dev is not None
          and dev.flavor == "v8"
          and titles(log) == [tr("Could not log in")] and ui.inputs[-1] == "ubnt",
          str((titles(log), ui.inputs[-2:])))
    ok, log, dev = act(ui, [("input", "127.0.0.1:1"), ("input", None)], T.connect_screen, ui,
                       args)
    check('nobody at that address: "Connection error", without asking for user or password '
          "first, and it asks for the address again", ok and dev is None
          and titles(log) == [tr("Connection error")])
    auto = types.SimpleNamespace(device=None, user=None, http=False, https=False, target=None)
    logins = len(srv.dev.sessions)                       # (each login, one session)
    ok, log, dev = act(ui, [("input", target), ("yesno", 1), ("input", None)],
                       T.connect_screen, ui, auto)
    asked = shown(log, tr("Unencrypted connection"), "yesno")
    check("without saying the protocol, HTTPS does not answer and HTTP does: it asks before the "
          'password; with "No" it does not send it and asks for the address again',
          ok and dev is None
          and target in asked and len(srv.dev.sessions) == logins, str((asked[:80], logins)))
    ok, log, dev = act(ui, [("input", target), ("yesno", 0), ("input", "ubnt"),
                            ("password", "ubnt")], T.connect_screen, ui, auto)
    check('  with "Yes": it logs in over HTTP, and does not warn about it again later', ok
          and dev is not None and dev.scheme == "http" and dev.flavor == "v8" and not dev.notes
          and len(srv.dev.sessions) == logins + 1, str(dev and dev.notes))
    ok, log, dev = act(ui, [("input", "http://" + target), ("input", "ubnt"),
                            ("password", "ubnt")], T.connect_screen, ui, auto)
    check("  asking for HTTP (http:// in front, or --http): no question", ok and dev is not None
          and not titles(log, "yesno"), str(titles(log, "yesno")))
    srv.shutdown()
    first_setup()

    print("=== main and the command line ===")
    def fail(ui, args):
        raise T.AirOSError("the device went away")

    def interrupt(*a, **k):
        raise KeyboardInterrupt
    err = io.StringIO()
    with patched(T, UI=FakeUI, connect_screen=fail), contextlib.redirect_stderr(err):
        rc = T.main(["127.0.0.1"])
    check("a device error: to stderr, and exit code 1", rc == 1
          and tr("Error: {error}", error="the device went away") in err.getvalue())
    with patched(T, UI=FakeUI, connect_screen=interrupt):
        rc = T.main([])
    check("Ctrl+C: it exits with no error", rc == 0)
    err = io.StringIO()
    with patched(T, LOCALE_DIR=os.path.join(TMP, "no-catalogs")), \
            contextlib.redirect_stderr(err):
        rc = T.main(["--discover", "--target", "bad..name"])
    T.set_language("es")
    check("without the catalog of the language: it warns and goes on in English", rc == 2
          and "no 'es' translation" in err.getvalue()
          and '"bad..name" is not an IP or a name that can be resolved.' in err.getvalue(),
          err.getvalue())
    os.environ["AIROS_LANG"] = "en"
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = T.main(["--la", "es", "--discover", "--target", "bad..name"])
    os.environ["AIROS_LANG"] = "es"
    T.set_language("es")
    check("--la (abbreviated) chooses the language too", rc == 2 and tr(
        '"{target}" is not an IP or a name that can be resolved.', target="bad..name")
        in err.getvalue(), err.getvalue())

    def ns(**kw):
        return types.SimpleNamespace(**{"target": None, "device": None, "listen": None,
                                        "json": False, "timeout": 0.3, **kw})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = T.cli_discover(ns(device="http://bad..name:8080/"))
    check("--discover with a URL whose name does not resolve: exit code 2", rc == 2 and tr(
        '"{target}" is not an IP or a name that can be resolved.', target="bad..name")
        in err.getvalue(), err.getvalue())

    def no_sockets():
        raise OSError("no sockets in this test")
    err = io.StringIO()
    with patched(T, neighbor_sockets=no_sockets), contextlib.redirect_stderr(err):
        rc = T.cli_discover(ns(target="127.0.0.1", listen=1))
    check("--discover: if it cannot listen for CDP/LLDP, it says so and searches anyway", rc == 1
          and "no sockets in this test" in err.getvalue()
          and tr("No airOS antenna answered on {where}.", where="127.0.0.1") in err.getvalue(),
          err.getvalue())

    def listening(listen, socks, stop):
        stop.wait(10)
        return []
    one = T.parse_discovery(S.Device(8, "ubnt", "ubnt", 1, 60).discovery_reply(), "127.0.0.1")

    def answered(timeout, target=None, port=None, interfaces=None, first=False, stop=None):
        stop.wait(10)                      # (one already answered; Ctrl+C before the end)
        return [one]
    err, out = io.StringIO(), io.StringIO()
    # Ctrl+C as in a terminal, even if these tests were started with SIGINT ignored (in the
    # background from a script, Python then does not turn it into KeyboardInterrupt).
    before = signal.signal(signal.SIGINT, signal.default_int_handler)
    t0 = time.monotonic()
    threading.Timer(0.6, lambda: os.kill(os.getpid(), signal.SIGINT)).start()     # (Ctrl+C)
    try:
        with patched(T, neighbor_sockets=lambda: ([object()], ["eth-test"]),
                     listen_neighbors=listening, discover=answered), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = T.cli_discover(ns(target="127.0.0.1", listen=10))
    finally:
        signal.signal(signal.SIGINT, before)
    check("--discover: Ctrl+C stops the search and the listening, and shows what already "
          "answered", rc == 0
          and "eth-test" in err.getvalue()
          and tr("Interrupted: showing what was found so far.") in err.getvalue()
          and "AP-North-Tower" in out.getvalue() and time.monotonic() - t0 < 3,
          err.getvalue() + out.getvalue())
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = T.cli_discover(ns(device="127.0.0.1:8443"))
    check("--discover with IP:port, as to connect: it searches that IP", rc == 1 and tr(
        "No airOS antenna answered on {where}.", where="127.0.0.1") in err.getvalue(),
          err.getvalue())
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = T.cli_discover(ns(device="http://[x"))
    check("  and one that is not an address: it says so (exit code 2), no crash", rc == 2 and tr(
        '"{target}" is not an IP or a name that can be resolved.', target="http://[x")
        in err.getvalue(), err.getvalue())
    ui = FakeUI()
    with patched(T, DISCOVERY_SECONDS=0.3):
        ok, log, r = act(ui, [("menu", "d"), ("input", None), ("menu", None)],
                         T.discovery_screen, ui, "127.0.0.1")
    check("discovery screen: cancelling the address goes back to the list", ok
          and r is None and titles(log, "menu") == [tr("Discovery")] * 2)


def review_apply():
    print("=== the review: before writing, the device as it is now ===")
    srv = start(8, 19111)
    settle(srv)
    try:
        app, ui, dev = app_for(19111)
        d = srv.dev
        ssid0 = d.system["wireless.1.ssid"]
        for c in (d.running, d.saved, d.system):          # (changed on its web, saved)
            c["wireless.1.ssid"] = "Changed-on-the-web"
        app._accept({"resolv.host.1.name": "TUI-Name"})
        done, log, _ = act(app, [("yesno", 1)], app.apply, False)
        asked = shown(log, tr("Changed meanwhile"), "yesno")
        check('changed meanwhile: it says so, with its keys; with "No", it sends nothing',
              done and "wireless.1.ssid" in asked
              and d.system.get("resolv.host.1.name") != "TUI-Name", asked[:200])
        done, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, False)
        settle(srv)
        check('  with "Yes": its change on top of the current one (what its web did stays)',
              done and (d.system["wireless.1.ssid"], d.system["resolv.host.1.name"])
              == ("Changed-on-the-web", "TUI-Name") and not app.changes(),
              str((d.system["wireless.1.ssid"], titles(log))))
        other = T.AirOS("http://127.0.0.1:19111", "ubnt", "ubnt")
        other.connect()
        cfg = T.Config(other.get_config())
        cfg.set("wireless.1.ssid", "Foreign-Test")
        other.write_config(cfg, test=True)
        settle(srv)
        app._accept({"resolv.host.1.name": "Other"})
        done, log, _ = act(app, [], app.apply, True)
        check("with a test in progress that is not its own, it does not write: decide on it first",
              done and shown(log, tr("Test mode")).startswith(tr(
                  'The device has changes in test mode right now: decide on them first ("Test '
                  'mode in progress": keep or revert). What is sent now would go on top of '
                  'them.'))
              and d.system.get("resolv.host.1.name") == "TUI-Name")
        other.discard_test()
        settle(srv)
        app.working = app.original.copy()
    finally:
        srv.shutdown()
    print("=== the review: starting in the middle of someone else's test, and menu 7 ===")
    srv = start(8, 19112, test_secs=2)
    settle(srv)
    try:
        d = srv.dev
        ssid0 = d.system["wireless.1.ssid"]
        other = T.AirOS("http://127.0.0.1:19112", "ubnt", "ubnt")
        other.connect()
        cfg = T.Config(other.get_config())
        cfg.set("wireless.1.ssid", "Foreign-Test")
        other.write_config(cfg, test=True)
        settle(srv)
        app, ui, dev = app_for(19112)
        done, log, _ = act(app, [("yesno", 255)], app.pending_test, True)
        check("at start: someone else's test, undecided (Esc)", done and app.original.get(
            "wireless.1.ssid") == "Foreign-Test", str(titles(log)))
        deadline = time.time() + 15
        while d.test_until is not None and time.time() < deadline:
            time.sleep(0.3)
            d.tick()
        settle(srv)
        app._accept({"resolv.host.1.name": "After-the-Test"})
        done, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, False)
        settle(srv)
        check("  it reverted by itself: applying says so and sends only its change, not the test",
              done and "wireless.1.ssid" in shown(log, tr("Changed meanwhile"), "yesno")
              and (d.system["wireless.1.ssid"], d.system["resolv.host.1.name"])
              == (ssid0, "After-the-Test"), str((d.system["wireless.1.ssid"], titles(log))))
        app._accept({"resolv.host.1.name": "Own"})
        done, log, _ = act(app, [("yesno", 0), ("yesno", 255)], app.apply, True)
        app._accept({"wireless.1.ssid": "Prepared-Later"})
        done2, log2, _ = act(app, [("yesno", 1)], app.pending_test)
        settle(srv)
        keys = [k for k, _, _ in app.changes()]
        check("menu 7, revert its own forgotten test: what was prepared is still pending",
              done and done2 and sorted(keys) == ["resolv.host.1.name", "wireless.1.ssid"],
              str((keys, titles(log), titles(log2))))
    finally:
        srv.shutdown()
    print("=== the review: follow the device only if it is the same one (its MAC) ===")
    srv = start(8, 19113)
    settle(srv)
    try:
        app, ui, dev = app_for(19113)
        with patched(dev, ping=lambda *a, **k: True), patched(
                app, _is_same_device=lambda ip, macs: False):
            found = app.wait_for_device("x", 0, max_wait=2, alt="127.0.0.9", macs={"aa"})
        with patched(dev, ping=lambda *a, **k: True), patched(
                app, _is_same_device=lambda ip, macs: True):
            same = app.wait_for_device("x", 0, max_wait=2, macs={"aa"})
        check("what answers at its new IP or at the old one with another MAC is not the device",
              found is False and same is True)
        saved = os.environ.pop("AIROS_TUI_STRICT")
        try:
            ok = app._do(lambda: 1 / 0)
        finally:
            os.environ["AIROS_TUI_STRICT"] = saved
        path = os.path.join(XDG, "airos-tui", "last-error.txt")
        check("an unexpected error does not close the program: it says so, leaves its details "
              "(only for the user)",
              ok is True and titles(ui.log)[-1] == tr("Unexpected error")
              and os.path.exists(path) and oct(os.stat(path).st_mode & 0o777) == "0o600"
              and "ZeroDivisionError" in open(path).read())
    finally:
        srv.shutdown()
    identity()


def identity():
    print("=== checking it is the device, without giving the user and password to another ===")
    srv = start(8, 19114)                               # this device, at 127.0.0.1
    other = start(8, 19114, bind="127.0.0.92")          # another one, another IP, same port
    settle(srv)
    S.serve_discovery(srv.dev, "127.0.0.1", 19115)      # this one answers its discovery
    reply = srv.dev.discovery_reply().replace(bytes.fromhex(S.MAC), bytes.fromhex("0418D6000001"))
    stranger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stranger.bind(("127.0.0.92", 19116))                # the other one, with another MAC

    def answer():
        while True:
            try:
                _, addr = stranger.recvfrom(2048)
                stranger.sendto(reply, addr)
            except OSError:
                return
    threading.Thread(target=answer, daemon=True).start()
    try:
        app, ui, dev = app_for(19114)
        macs = app._device_macs()
        asked, logins = [], []
        real_status_at = dev.status_at

        def status_at(host):
            asked.append(host)
            return real_status_at(host)

        def login():
            logins.append(dev.host)
            raise T.AuthError("no")
        with patched(dev, status_at=status_at, login=login):
            app.discovery = {"target": "127.0.0.1", "port": 19115}
            t0 = time.monotonic()
            mine = app._is_same_device("127.0.0.1", macs)
            took = time.monotonic() - t0
            check("its discovery answers with its MAC: same device, without asking it anything "
                  "else (nor waiting for the search time to pass)", mine is True and not asked
                  and not logins and took < 0.9, f"{asked} {logins} {took:.2f} s")
            app.discovery = {"target": "127.0.0.1", "port": 19116}
            before = (dict(dev.cookies), dev.csrf)
            stranger_said = app._is_same_device("127.0.0.92", macs)
            check("  another one answers with another MAC: not the device, and nothing is sent to "
                  "it (not even the session)",
                  stranger_said is False and not asked and not logins
                  and dev.host == "127.0.0.1", f"{asked} {logins} {dev.host}")
            app.discovery = {"target": "127.0.0.1", "port": 19117}      # (nobody answers)
            mine = app._is_same_device("127.0.0.1", macs)
            check("without discovery: with this session, which the device knows (no new login)",
                  mine is True and asked == ["127.0.0.1"] and not logins, f"{asked} {logins}")
            asked.clear()
            not_mine = app._is_same_device("127.0.0.92", macs)
            check("  the other one does not know it: not the device, and it receives neither the "
                  "user nor the password",
                  not_mine is False and asked == ["127.0.0.92"] and not logins
                  and not other.dev.sessions and dev.host == "127.0.0.1"
                  and (dict(dev.cookies), dev.csrf) == before,
                  f"{asked} {logins} {other.dev.sessions} {dev.host}")
        check("  and the session still works with it", dev.status().get("host", {}).get(
            "hostname"))
    finally:
        stranger.close()
        other.shutdown()
        other.server_close()
        srv.shutdown()
        srv.server_close()


def endings_moved():
    print("=== the odd endings of a test that moves the IP: the program follows the device ===")
    old, new = "127.0.0.94", "127.0.0.95"
    srv = start(8, 19118, mgmt_ip=old, restart=0.4)
    d = srv.dev
    settle(srv)
    dev = T.AirOS(f"http://{old}:19118", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=1)

    def move():
        """The device at its old IP, out of test mode; the IP change prepared."""
        settle(srv)
        app.load()
        ifc = T.main_iface(app.working, "v8")
        app._accept(T.plan_static(app.working, ifc, new, "255.255.255.0", "127.0.0.1", "",
                                  ""))

    def expire():
        """Its test time ends now: it goes back to its saved configuration (the old IP)."""
        with d.lock:
            d.test_until = time.time() - 1
        d.tick()
        settle(srv)

    def pending_ip():
        return [b for k, _, b in app.changes() if k.endswith(".ip")]

    def late_keep():
        expire()
        raise T.AirOSError("cut off (simulated)")
    try:
        move()
        with patched(dev, confirm_test=late_keep):
            ok, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
        check('"Keep" just as its time runs out (the connection drops): it looks for it, it is '
              'at the previous IP; "Too late", and the change is still pending', ok
              and dev.host == old
              and tr("Too late") in titles(log) and new in pending_ip() and d.test_until is None,
              str((dev.host, titles(log), pending_ip())))

        def gone(*a, **k):
            raise T.AirOSError("no connection (simulated)")

        def nowhere():
            dev.test_mode_info = gone
            app._wait = lambda *a, **k: False
            return 1
        move()
        try:
            ok, log, _ = act(app, [("yesno", 0), ("yesno", nowhere)], app.apply, True)
        finally:
            del dev.test_mode_info, app._wait
        check("when deciding, it answers neither at the new IP nor at the previous one: it says "
              "so and points to the previous one (where it ends up), not to a mix",
              ok and dev.host == old
              and shown(log, tr("No answer")).startswith(tr(
                  "The device does not answer at {address}.", address=f"http://{old}:19118")),
              str((dev.host, titles(log))))
        expire()
        move()
        with patched(dev, confirm_test=lambda: None):        # (it ignores it)
            ok, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
        still = ok and dev.host == new and shown(log, tr("Attention")) == tr(
            "The device is still in test mode. Check the web.")
        ok, log, _ = act(app, [("yesno", 255)], app.pending_test)
        check('"Keep" with no effect, then "Test mode in progress" left undecided: it knows '
              "where it goes back to (the previous IP)", still and ok and dev.host == old and tr(
                  "When it reverts it will go back to {old}; the program already points there.",
                  old=old) in shown(log, tr("Not confirmed")), str((dev.host, titles(log))))
        expire()
        move()
        with patched(dev, confirm_test=lambda: None):
            act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
        expire()                                            # (and it ends by itself)
        ok, log, _ = act(app, [], app.pending_test)
        check('  and if it ran out meanwhile: "Test mode in progress" finds it at the previous IP',
              ok and dev.host == old and shown(log, tr("Test mode")) == tr(
                  "This device has no changes in test mode."), str((dev.host, titles(log))))
    finally:
        if d.test_until is not None:
            expire()
        srv.cluster.shutdown()


def keep_messages():
    print("=== what the endings say: what had not taken its value, and the test time on airOS 6 "
          "===")
    srv = start(8, 19119)
    settle(srv)
    try:
        app, ui, dev = app_for(19119)
        d = srv.dev
        p = int(app.original.get("radio.1.txpower"))
        app._accept({"radio.1.txpower": str(p - 1), "resolv.host.1.name": "Saved-Anyway"})

        def not_taken(status, hdrs, body):          # (its power, always the previous one)
            return status, hdrs, re.sub(rb"(?m)^radio\.1\.txpower=.*$",
                                        f"radio.1.txpower={p}".encode(), body)
        with faults(dev, {"/getcfg.cgi": not_taken}):
            ok, log, _ = act(app, [("yesno", 0), ("yesno", 0)], app.apply, True)
        said = shown(log, tr("Attention"))
        check('"Keep" with a key that had not taken its value (as it said before deciding): the '
              "rest is saved, and it does not say the time ran out", ok
              and said.startswith(tr("Saved for good, except these keys, which the device had "
                                     "not taken (as it said before deciding):\n  {keys}",
                                     keys="radio.1.txpower"))
              and d.saved.get("resolv.host.1.name") == "Saved-Anyway"
              and [k for k, _, _ in app.changes()] == ["radio.1.txpower"],
              str((said[:90], app.changes())))
        settle(srv)
        app.load()
        app._accept({"resolv.host.1.name": "Other"})
        took = []
        slow = app.wait_for_device

        def slower(*a, **k):
            time.sleep(1.5)                          # (it takes a while to answer again)
            return slow(*a, **k)
        with patched(app, wait_for_device=slower,
                     _decide_test=lambda changes, missing, t: took.append(time.monotonic() - t)):
            ok, log, _ = act(app, [("yesno", 0)], app.apply, True)
        check("the test time counts from when it was sent, not from when it answers again "
              "(airOS 6 does not count it down: the program does)", ok and took
              and took[0] >= 1.5, str(took))
        discard(srv, dev)
    finally:
        srv.shutdown()
        srv.server_close()


def recent_and_debug():
    print("=== recent devices and --debug ===")
    key = "Secure-Phrase-2026"
    srv = start(8, 19146, user="Operator", password=key)
    T.forget_devices()
    try:
        dev = T.AirOS("http://127.0.0.1:19146", "Operator", key)
        dev.connect()
        T.remember_device(dev)
        # (another one, at a loopback address too: nothing here goes outside this computer)
        T.remember_device(types.SimpleNamespace(host="127.0.0.147", port=443, scheme="https",
                                                username="admin"))
        T.remember_device(dev)                       # (again: at the top, not repeated)
        path = T.RECENT_FILE
        saved = json.load(open(path, encoding="utf-8"))
        check("it saves the address (without the protocol: found out again) and the user, never "
              "the password; the last one at the top, not repeated; only for you (0600)",
              saved == [{"target": "127.0.0.1:19146", "user": "Operator"},
                        {"target": "127.0.0.147", "user": "admin"}]
              and key not in open(path, encoding="utf-8").read()
              and oct(os.stat(path).st_mode & 0o777) == "0o600", str(saved))
        args = types.SimpleNamespace(device=None, user=None, http=True, https=False, target=None)
        ui = FakeUI()
        ok, log, got = act(ui, [("menu", "1"), ("password", key)], T.connect_screen, ui, args)
        items = ui.menus[-1][1]
        check("the connection screen starts with them; once one is chosen, it only asks for its "
              "password (its user, the remembered one)", ok and got is not None
              and got.username == "Operator"
              and [label for _, label in items] == [
                  "127.0.0.1:19146  (Operator)", "127.0.0.147  (admin)", tr("Another address…"),
                  tr("Search for antennas on your network (no user or password)"),
                  tr("Forget these devices")]
              and shown(log, tr("Connect"), "password") == tr(
                  "Password of {user} on {target}:", user="Operator", target="127.0.0.1:19146")
              and not shown(log, tr("Connect"), "input"), str((ok, items)))
        ok, log, got = act(ui, [("menu", "1"), ("password", None)], T.connect_screen, ui,
                           types.SimpleNamespace(device=None, user="other", http=True,
                                                 https=False, target=None))
        check("  with -u, that one and not the remembered one", ok and got is None
              and shown(log, tr("Connect"), "password") == tr(
                  "Password of {user} on {target}:", user="other", target="127.0.0.1:19146"),
              str(log))
        ok, log, got = act(ui, [("menu", "n"), ("input", None), ("menu", None)],
                           T.connect_screen, ui, args)
        check('  "Another address…": the usual field (with the last one); cancelling it goes '
              "back to them, and cancelling them exits", ok and got is None
              and ui.inputs[-1] == "127.0.0.1:19146", str(ui.inputs[-1:]))
        searched = []
        with patched(T, discovery_screen=lambda ui, where, **kw: searched.append(where)):
            ok, log, got = act(ui, [("menu", "s"), ("menu", None)], T.connect_screen, ui, args)
        check('  "Search for antennas…": no user or password; with none chosen, back to them',
              ok and got is None and searched == [T.BROADCAST])
        ok, log, got = act(ui, [("menu", "f"), ("yesno", 1), ("menu", "f"), ("yesno", 0),
                                ("input", None)], T.connect_screen, ui, args)
        check("  forget them (asked first): the file is deleted, and it asks for the address as "
              "usual", ok and got is None and not os.path.exists(path))

        print("=== main: it remembers them, and --debug ===")
        seen = {}

        def connect(ui, args):
            d = T.AirOS("http://127.0.0.1:19146", "Operator", key)
            d.connect()
            d.get_config()
            seen["dev"] = d
            return d

        class NoApp:
            def __init__(self, ui, dev, wait=None):
                self.dev, self.next_target, self.discovery_target = dev, None, None

            def run(self):
                self.dev.username = "Renamed"        # (its administrator changed meanwhile)
        err = io.StringIO()
        with patched(T, UI=FakeUI, App=NoApp, connect_screen=connect), \
                contextlib.redirect_stderr(err):
            rc = T.main(["--debug"])
        log = open(T.DEBUG_FILE, encoding="utf-8").read()
        psk = srv.dev.running.get("aaa.1.wpa.psk") or ""
        secrets = [key, srv.dev.pw_hash, psk, seen["dev"].csrf] + list(seen["dev"].cookies.values())
        check("--debug: what was sent and answered, in a file only for you, with no passwords, "
              "hashes, WPA keys, cookies or its token; on exit it says where", rc == 0
              and "POST http://127.0.0.1:19146/api/auth → 200" in log
              and "GET http://127.0.0.1:19146/getcfg.cgi → 200" in log and "password=***" in log
              and len(psk) > 3 and not [x for x in secrets if x and len(x) > 3 and x in log]
              and oct(os.stat(T.DEBUG_FILE).st_mode & 0o777) == "0o600"
              and tr("What was sent to the device and what it answered: {path}",
                     path=T.DEBUG_FILE) in err.getvalue(), log[:300] + err.getvalue())
        check("  and main remembers the device on connecting and on finishing (with the "
              "administrator it had then)", T.recent_devices() == [{"target": "127.0.0.1:19146",
                                                                  "user": "Renamed"}],
              str(T.recent_devices()))
        hide = T.hide_secrets
        check("  what it hides: configuration lines, JSON, forms and URLs, and any hash",
              hide("users.1.password=$1$a$b\nwireless.1.ssid=Net\n")
              == "users.1.password=***\nwireless.1.ssid=Net\n"
              and hide('{"password": "x", "ssid": "y", "psk": 1}')
              == '{"password": "***", "ssid": "y", "psk": "***"}'
              and hide("/x.cgi?login=a&passwd=b&n=1") == "/x.cgi?login=a&passwd=***&n=1"
              and hide("hash $6$salt$value end") == "hash *** end")

        class Full(io.StringIO):                     # (a full disk)
            def write(self, text):
                raise OSError(28, "No space left on device")
        debuglog = sys.modules["airos_tui.core.debuglog"]
        debuglog._out = Full()
        try:
            debuglog._write("GET /status.cgi\n")
            went_on = not T.debugging()
        except OSError as e:
            went_on = repr(e)
        finally:
            T.stop_debug()
        check("  a log that cannot be written (a full disk): no more of it, and the program goes "
              "on (not an error in the middle of a request)", went_on is True, str(went_on))
    finally:
        T.forget_devices()
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    keep_messages()
    endings_moved()
    review_apply()
    menu_actions()
    test_mode_outcomes()
    network()
    client_errors()
    main_loop()
    startup()
    recent_and_debug()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
