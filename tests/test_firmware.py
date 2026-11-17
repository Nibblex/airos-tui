"""Tests of the firmware update (airOS 8, as its web): the rules (versions, channel width
compatibility, error messages), uploading and updating or discarding, checking and downloading
with its license, what it asks at startup, and airOS 6. All against the simulator at
127.0.0.1."""
import contextlib
import glob
import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _common import check, FakeUI, fails, suite  # noqa: E402
T, S, XDG = suite("firmware")
tr = T.tr
T.UPGRADE_POLL_DELAY = 0      # (it asks at once: what answers then may be the old firmware)
os.makedirs(XDG, exist_ok=True)


def start(version, port, **kw):
    srv = S.make_server(version, port, restart=0.3, test_secs=60, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    app = T.App(FakeUI(), dev, wait=1)
    app.load()
    return srv, srv.dev, dev, app.ui, app


def run(app, method, script, *args):
    app.ui.script = list(script)
    app.ui.log.clear()
    try:
        getattr(app, method)(*args)
    except AssertionError as e:
        print("     script:", e)
        return False
    return not app.ui.script


def msgs(ui, title=None):
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def asked(ui, title):
    return [t for k, ti, t in ui.log if k == "yesno" and ti == title]


def firmware(name, version="WA.v8.7.20", extra=b""):
    """A simulated firmware file: UBNT, its version near the start, and some bytes."""
    path = os.path.join(XDG, name)
    with open(path, "wb") as f:
        f.write(b"UBNT" + b"\0" * 12 + version.encode() + b"\0" + extra + os.urandom(4096))
    return path


def rules():
    print("=== the web's rules: version, build, compatibility and errors ===")
    full = "WA.ar934x.v8.7.14.47873.241003.1314"
    check("build, prefix and the version the web compares", (
        T.fw_build(full), T.fw_build("x"), T.fw_prefix(full), T.fw_version(full, "v8.7.14"))
          == ("47873", None, "WA", "WA.v8.7.14"))
    check("the board's minimum version (board.required_fw_version)",
          T.fw_required({"board.required_fw_version": "524800"}) == "v8.2.0")
    check('"Check for Updates": on when the key is missing, as the web shows it', (
        T.update_check_on(T.Config("")), T.update_check_on(T.Config("update.check.status="
                                                                    "disabled\n")))
          == (True, False))
    check("isCompatibleFw: the versions from before 30/60 MHz and those after", (
        T.fw_compatible("WA.v8.7.14", "WA.v8.7.20"),
        T.fw_compatible("WA.v8.7.14", "WA.v8.0.0-beta3"),
        T.fw_compatible("WA.v7.1.4", "WA.v8.0.0-beta3"),
        T.fw_compatible("v8.7.14", "WA.v8.0.0-beta3")) == (True, False, True, True))
    text = T.fw_uploaded_text
    check("the notice: third-party, incompatible width, the same version", (
        tr("WARNING: Third-party firmware detected! Ensure you are familiar with recovery "
           "procedure.") in text("WA.v8.7.20", True, "WA.v8.7.19", 20),
        tr("WARNING: {width} MHz channel width is incompatible with uploaded firmware. Access "
           "to device may be lost.", width=30) in text("WA.v8.0.0-beta3", False, "WA.v8.7.19",
                                                       30),
        tr("WARNING: Device already has the same firmware version.") in text(
            "WA.v8.7.19", False, "WA.v8.7.19", 20),
        text("WA.v8.7.19", False, "WA.v8.7.19", 30)) == (True, True, True, tr(
            "Uploaded Firmware Version: {version}", version="WA.v8.7.19")))
    board = {"board.required_fw_version": "524800"}
    check("the device's errors, with the web's messages", (
        T.fw_error(68, "WA", board), T.fw_error(98, "WA", board), T.fw_error(99, "WA", board),
        T.fw_error(7, "WA", board), T.fw_error(None, "WA", board)) == (
        tr("Invalid firmware file. Please check that you have selected the correct firmware "
           "architecture ({series}) for your device.", series="WA"),
        tr("Incorrect firmware version. Please upload {version} or newer and try again.",
           version="v8.2.0"),
        tr("Corrupted firmware file uploaded. Please upload correct file and try again.")
        + " (99)", tr("Firmware image check failed. Error code") + ": 7",
        tr("Firmware image check failed. Error code") + ": ?"))


def upload_and_update():
    print('=== System → Firmware: what it shows, and "Check for Updates" ===')
    srv, d, dev, ui, app = start(8, 19301)
    title = tr("Firmware Update")
    ok = run(app, "system_menu", [("menu", "v"), ("menu", "c"), ("menu", None), ("menu", None)])
    system = [m for m in ui.menus if m[0] == tr("System")][0][1]
    head = [t for k, ti, t in ui.log if k == "menu" and ti == title]
    check('first in "System", with its version; at the top, the version and the build', ok
          and system[0] == ("v", tr("Firmware: {version}", version="v8.7.19"))
          and head[0].startswith(tr("Firmware Version: {version}", version="v8.7.19 (WA)"))
          and tr("Build Number: {build}", build="48279") in head[0], str(head[:1]))
    check('"Check for Updates": it stays as a pending change', {
        k: b for k, _, b in app.changes()} == {"update.check.status": "disabled"})
    ok = run(app, "firmware_menu", [("menu", "c"), ("menu", None)])
    check("  (and changing it back leaves it as it was)", ok and not app.changes())

    print("=== uploading a firmware: what the device refuses ===")
    up = tr("Upload Firmware")
    bad = os.path.join(XDG, "not-firmware.bin")
    with open(bad, "wb") as f:
        f.write(b"hello" * 100)
    ok = run(app, "upload_firmware", [("input", bad)])
    check("a file that is not a firmware: the web's message", ok and msgs(ui, up) == [tr(
        "Invalid firmware file uploaded. Please upload correct file and try again.")])
    ok = run(app, "upload_firmware", [("input", firmware("xc.bin", "XC.v8.7.20"))])
    check("another architecture: it says so, with this device's (WA)", ok and msgs(ui, up) == [tr(
        "Invalid firmware file. Please check that you have selected the correct firmware "
        "architecture ({series}) for your device.", series="WA")])
    gone = os.path.join(XDG, "missing.bin")
    ok = run(app, "upload_firmware", [("input", gone)])
    check("one that does not exist: it cannot read it", ok and msgs(ui, "Error")[0].startswith(
        tr("Could not read {path}: {error}", path=gone, error="")))
    check("  (it uploaded nothing)", "fw-uploaded" not in d.events)

    print('=== the notice of the uploaded firmware: warnings and "Discard" ===')
    ok = run(app, "upload_firmware", [("input", firmware("equal.bin", "WA.v8.7.19")),
                                      ("yesno", 255), ("yesno", 1)])
    notice = asked(ui, title)
    check('the same version: it warns; with no choice it asks again; "Discard"', ok
          and len(notice) == 2 and tr("WARNING: Device already has the same firmware "
                                      "version.") in notice[0]
          and d.fw_pending is None and "fw-discarded" in d.events
          and msgs(ui, title) == [tr("The uploaded firmware was discarded.")], str(notice))
    ok = run(app, "upload_firmware", [("input", firmware("third-party.bin",
                                                         extra=b"third-party")), ("yesno", 1)])
    check("a third-party one: it warns", ok and tr(
        "WARNING: Third-party firmware detected! Ensure you are familiar with recovery "
        "procedure.") in asked(ui, title)[0])
    for c in (d.saved, d.system, d.running):
        c["radio.1.chanbw"] = "30"
    app.status = dev.status()
    ok = run(app, "upload_firmware", [("input", firmware("old.bin", "WA.v8.0.0-beta3")),
                                      ("yesno", 1)])
    check("with 30 MHz and a firmware from before that width: it warns", ok and tr(
        "WARNING: {width} MHz channel width is incompatible with uploaded firmware. Access to "
        "device may be lost.", width=30) in asked(ui, title)[0])
    for c in (d.saved, d.system, d.running):
        c["radio.1.chanbw"] = "20"
    app.status = dev.status()

    print("=== choosing it from a folder ===")
    folder = os.path.join(XDG, "firmwares")
    os.makedirs(folder, exist_ok=True)
    empty = os.path.join(XDG, "empty")
    os.makedirs(empty, exist_ok=True)
    ok = run(app, "upload_firmware", [("input", empty)])
    check("a folder without a .bin: it says so", ok and msgs(ui, up) == [tr(
        "There is no .bin file in {path}.", path=empty)])
    new = firmware(os.path.join("firmwares", "WA.v8.7.20.bin"))

    print('=== "Update": it writes it, waits for the device and reads it again ===')
    ok = run(app, "upload_firmware", [("input", folder), ("menu", "1"), ("yesno", 0)])
    done = msgs(ui, title)
    check("it updates to the uploaded version and says so", ok and "fw-flashed" in d.events
          and d.fw_version == "v8.7.20" and (app.status.get("host") or {}).get("fwversion")
          == "v8.7.20" and len(done) == 1 and done[0].startswith(tr(
              "Firmware updated: {old} → {new}.", old="v8.7.19", new="v8.7.20")), str(done))
    check("  (it chose it from the folder)", any(m[0] == up and m[1][0][1] == os.path.basename(new)
                                                 for m in ui.menus))
    real_flash = dev.fw_flash
    dev.fw_flash = lambda: {"code": 1, "message": "Flash busy"}
    ok = run(app, "upload_firmware", [("input", firmware("other.bin", "WA.v8.7.21")),
                                      ("yesno", 0)])
    dev.fw_flash = real_flash
    check("if the device does not start updating: its message", ok
          and msgs(ui, title) == ["Flash busy"] and d.fw_version == "v8.7.20")
    real_wait = app._wait
    app._wait = lambda *a, **k: False
    ok = run(app, "firmware_uploaded", [("yesno", 0)], "WA.v8.7.21", False)
    app._wait = real_wait
    check("if it is not back in time: it says not to turn it off and to wait", ok and msgs(ui, tr(
        "No answer")) == [tr("The device does not answer at {address} yet. The update may "
                             "still be running: do not turn it off, wait a few minutes and "
                             "connect again.", address=dev.base)])
    srv.shutdown()


def check_and_download():
    print('=== "Check Now" ===')
    srv, d, dev, ui, app = start(8, 19302)
    title = tr("Firmware Update")
    ok = run(app, "firmware_menu", [("menu", "n"), ("menu", None)])
    check("no new version: it says so", ok and "fw-check-forced" in d.events
          and msgs(ui, title) == [tr("You have latest available firmware version.")])
    d.fw_check_error = True
    ok = run(app, "check_for_update", [("yesno", 1)], True)
    said = asked(ui, title)
    check("if the device does not reach the server: it says so, with the downloads page, and "
          'offers to check from this PC; "Close", nothing else', ok and not msgs(ui) and said == [
              tr("Could not connect to update server.") + " " + tr(
                  "Go to Ubiquiti downloads page: {url}", url=T.DOWNLOADS_URL) + "\n\n"
              + tr(T.FROM_HERE_OFFER)], str(said))
    d.fw_check_error = False

    def broken(force):
        raise T.AirOSError(tr("Error occurred while checking for update") + " (HTTP 500: x)")
    dev.fw_update_check = broken
    ok = run(app, "check_for_update", [("yesno", 1)], True)
    del dev.fw_update_check
    check("if the request fails: the web's message (and the same offer)", ok
          and asked(ui, title) == [tr("Error occurred while checking for update")
                                   + " (HTTP 500: x).\n\n" + tr(T.FROM_HERE_OFFER)])

    print("=== there is a new version: its notes, downloading it, the license ===")
    d.fw_available = "WA.v8.7.20"
    offer = tr("Firmware update {version} is available.", version="WA.v8.7.20")
    ok = run(app, "check_for_update", [("yesno", 1)], True)
    notes = [t for k, ti, t in ui.log if k == "textbox" and ti == offer]
    check('it shows its notes (from the address the device gives); "Dismiss" discards it', ok
          and len(notes) == 1 and "airOS v8.7.20 (simulated)" in notes[0]
          and "fw-discarded" in d.events and d.fw_pending is None, str(notes))
    d.fw_download_seconds = 2.5                    # (as a real one: its answer at the end)
    ui.gauges.clear()
    ok = run(app, "check_for_update", [("yesno", 0), ("yesno", 1)], True)
    d.fw_download_seconds = 0.0
    licence = [t for k, ti, t in ui.log if k == "textbox" and ti == tr("License")]
    check('"Download": the device downloads it and shows the license; "Don\'t Agree" discards it',
          ok and "fw-downloaded" in d.events and licence == ["License\nSimulated license."]
          and d.fw_pending is None, str(licence))
    pcts = ui.gauges[0].pcts if ui.gauges else []
    check("  its progress, while the device downloads it (asked meanwhile, as its web does)",
          any(0 < p < 100 for p in pcts) and "fw-download-progress" in d.events, str(pcts))
    app._accept({"resolv.host.1.name": "Updated-Name"})
    ok = run(app, "check_for_update", [("yesno", 0), ("yesno", 0), ("yesno", 0)], True)
    check('"Agree" and "Update": it ends up with the new version', ok
          and d.fw_version == "v8.7.20" and msgs(ui, title)[-1].startswith(tr(
              "Firmware updated: {old} → {new}.", old="v8.7.19", new="v8.7.20")))
    check("  the change not applied: said that it is lost, before updating; and the program "
          "knows the new version", tr("If you update, the {count} unapplied change(s) are "
                                       "lost.", count=1) in asked(ui, title)[-1]
          and not app.changes() and app.dev.fw == "v8.7.20", str((asked(ui, title)[-1:],
                                                                  app.dev.fw)))
    ok = run(app, "check_for_update", [], True)
    check("  (and it is not offered again)", ok and msgs(ui, title) == [
        tr("You have latest available firmware version.")], str(msgs(ui)))
    srv.shutdown()


def simulator_option():
    print("=== the simulator: --fw-update ===")
    check("the version, as its update server gives it", [S.fw_update_arg(v) for v in (
        "v8.7.20", "8.7.20", "WA.v8.7.20", " wa.V8.7.21 ", "offline", "OFFLINE")]
          == ["WA.v8.7.20"] * 3 + ["WA.v8.7.21", "offline", "offline"])
    bad = []
    for value in ("8.7", "latest", "v8.7.20.1", ""):
        try:
            S.fw_update_arg(value)
        except ValueError:
            bad.append(value)
    check("  (and not what is not a version)", bad == ["8.7", "latest", "v8.7.20.1", ""])
    said = []
    for args in (["--airos", "6", "--fw-update", "v8.7.20"], ["--fw-update", "latest"]):
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                S.main(args)
        except SystemExit as e:
            said.append((e.code, err.getvalue()))
    check("on airOS 6, or with something that is not a version: it says so and does not start",
          len(said) == 2 and said[0][0] == said[1][0] == 2
          and tr("--fw-update only applies to airOS 8") in said[0][1]
          and tr('--fw-update: "{value}" is not a version such as v8.7.20, nor "offline"',
                 value="latest") in said[1][1], str(said))
    offer = S.make_server(8, 19305, fw_update="WA.v8.7.21")
    offline = S.make_server(8, 19306, fw_update="offline")
    check('what "Check Now" will find', (offer.dev.fw_available,
                                         offer.dev.fw_check_error) == ("WA.v8.7.21", False)
          and (offline.dev.fw_available, offline.dev.fw_check_error) == (None, True))
    offer.server_close()
    offline.server_close()


def startup():
    print("=== at startup, as the web ===")
    srv, d, dev, ui, app = start(8, 19303)
    title = tr("Firmware Update")
    with open(firmware("pending.bin"), "rb") as f:
        dev.fw_upload("pending.bin", f.read())
    ui.script, ui.log[:] = [("yesno", 1), ("menu", None)], []
    app.run()
    check("a firmware uploaded and not installed: it asks", not ui.script
          and len(asked(ui, title)) == 1 and d.fw_pending is None and "fw-discarded" in d.events)
    check('  without its key, "Check for Updates" is on (as its web shows it, and its menu): it '
          "checks at startup", "update.check.status" not in d.system
          and d.events.count("fw-check") == 1)
    for c in (d.saved, d.system, d.running):
        c["update.check.status"] = "enabled"
    d.fw_available = "WA.v8.7.20"
    app._last_check(None)
    ui.script, ui.log[:] = [("yesno", 1), ("menu", None)], []
    app.run()
    checks = d.events.count("fw-check")
    check('with "Check for Updates" on: it checks and offers the new version',
          not ui.script and checks == 2 and asked(ui, tr(
              "Firmware update {version} is available.", version="WA.v8.7.20")))
    ui.script, ui.log[:] = [("menu", None)], []
    app.run()
    check("  (once a day)", not ui.script and d.events.count("fw-check") == checks)
    for c in (d.saved, d.system, d.running):
        c["update.check.status"] = "disabled"
    app._last_check(None)
    ui.script, ui.log[:] = [("menu", None)], []
    app.run()
    check("  (and off, it does not check)", not ui.script and d.events.count("fw-check")
          == checks)
    srv.shutdown()


def airos6():
    print("=== airOS 6 ===")
    srv, d, dev, ui, app = start(6, 19304)
    ok = run(app, "system_menu", [("menu", "v"), ("menu", None)])
    check("the firmware, from its web", ok and msgs(ui) == [tr(
        "On airOS 6 the firmware is updated from the device's web (System page).")])
    srv.shutdown()


def flash_wait():
    print("=== after updating, only the device already restarted counts ===")
    title = tr("Firmware Update")
    writing = tr("Upgrading the firmware… it still answers with {version} and has not restarted: "
                 "it is writing the new one. Do not turn it off. ({seconds} s)",
                 version="v8.7.19", seconds=0).split("(")[0]

    def told(ui, d):
        """What it said at the end, and whether the device had written it by then."""
        out = []
        real = ui.msgbox
        ui.msgbox = lambda ti, text: (out.append((ti, text, "fw-flashed" in d.events)),
                                      real(ti, text))
        return out
    srv, d, dev, ui, app = start(8, 19307, flash_delay=2.5)
    try:
        with open(firmware("new.bin"), "rb") as f:
            dev.fw_upload("new.bin", f.read())
        said = told(ui, d)
        app._update_firmware()
        shown = [t for g in ui.gauges for t in g.texts]
        check("while its web still answers with the old firmware (it is writing the new one), "
              "that does not count: it says so and waits for it to come back with the new one",
              said and said[-1][1].startswith(tr("Firmware updated: {old} → {new}.",
                                                 old="v8.7.19", new="v8.7.20"))
              and said[-1][2] and any(t.startswith(writing) for t in shown),
              str((said[-1:], shown[-3:])))
    finally:
        srv.shutdown()
        srv.server_close()
    srv, d, dev, ui, app = start(8, 19308, flash_delay=999)
    margin = T.UPGRADE_MARGIN
    try:
        with open(firmware("new.bin"), "rb") as f:
            dev.fw_upload("new.bin", f.read())
        app.limits.board["board.upgrade"] = "1"
        T.UPGRADE_MARGIN = 3
        said = told(ui, d)
        app._update_firmware()
        check("the wait runs out and the old one still answers without restarting: it says just "
              'that (not "No answer", nor that it finished)',
              [(ti, x) for ti, x, _ in said] == [(title, tr(
                  "The device still answers with firmware {version} and has not restarted: the "
                  "update may not have started yet, or still be running. Do not turn it off; "
                  "check its firmware in a few minutes (System → Firmware).",
                  version="v8.7.19"))], str(said))
    finally:
        T.UPGRADE_MARGIN = margin
        srv.shutdown()
        srv.server_close()
    srv, d, dev, ui, app = start(8, 19309, flash_delay=1.5)
    try:
        with open(firmware("same.bin", version="WA.v8.7.19"), "rb") as f:
            dev.fw_upload("same.bin", f.read())
        time.sleep(max(0.0, 6 - (time.time() - d.boot)))    # (up for a while: its uptime)
        said = told(ui, d)
        app._update_firmware()
        check("with the same version: it counts once it has restarted (its uptime went back), "
              "not while it writes", said and said[-1][1] == tr(
                  "The device answers, but its firmware is still {version}.", version="v8.7.19")
              and said[-1][2], str(said))
    finally:
        srv.shutdown()
        srv.server_close()


def from_here():
    print("=== the antenna without internet: checking and downloading from this PC ===")
    fw = b"UBNT" + b"\0" * 12 + b"WA.v8.7.20\0" + os.urandom(300_000)     # (as firmware())
    name = "WA.v8.7.20.48000.260101.0101.bin"
    seen = {"queries": [], "downloads": 0, "mode": "ok"}

    class Ubiquiti(BaseHTTPRequestHandler):
        """Its update server (callhome.svc.ui.com) and its downloads (dl.ubnt.com), as their
        real answers are, at 127.0.0.1."""

        def log_message(self, *args):
            pass

        def _answer(self, head):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/update-fw/check.php":
                seen["queries"].append(dict(urllib.parse.parse_qsl(url.query)))
                info = {"update": "true", "date": "260101", "version": "v8.7.20",
                        "url": f"http://127.0.0.1:19311/firmwares/XC-fw/v8.7.20/{name}",
                        "checksum": hashlib.md5(fw).hexdigest(),
                        "changelog": "http://127.0.0.1:19311/changelog.txt", "security": ""}
                if seen["mode"] == "bad":
                    info["checksum"] = "0" * 32
                elif seen["mode"] == "incomplete":
                    del info["checksum"]
                elif seen["mode"] == "no-url":
                    info["url"] = "ftp://127.0.0.1/firmware.bin"
                body, ctype = json.dumps(info).encode(), "application/json; charset=UTF-8"
                if seen["mode"] == "none":                  # (its answer, byte for byte)
                    body = b'{"update":"false"}'
                elif seen["mode"] == "html":
                    body, ctype = b"<html><body>502 Bad Gateway</body></html>", "text/html"
            elif url.path.endswith(".bin"):
                seen["downloads"] += not head
                body, ctype = fw, "application/octet-stream"
            elif url.path == "/changelog.txt":
                body, ctype = b"airOS v8.7.20 (from the fake server)\n- Fixes.\n", "text/plain"
            elif url.path == "/eula.html":
                body = b"<html><body><h1>EULA</h1><p>Fake license.</p></body></html>"
                ctype = "text/html"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self._answer(False)

        def do_HEAD(self):
            self._answer(True)

    ubnt = ThreadingHTTPServer(("127.0.0.1", 19311), Ubiquiti)
    threading.Thread(target=ubnt.serve_forever, daemon=True).start()
    real = (T.FW_CHECK_URL, T.FW_EULA_URL)
    T.FW_CHECK_URL = "http://127.0.0.1:19311/update-fw/check.php"     # (nothing goes out)
    T.FW_EULA_URL = "http://127.0.0.1:19311/eula.html"
    title = tr("Firmware Update")
    kept = os.path.join(T.FW_DIR, name)
    try:
        board = {"board.sysid": "0xe7fa", "board.hwaddr": "68D79A000001"}
        check("what it asks, as the antenna does (its update_check): its sysid, its version, the "
              "md5 of its MAC and the language of its web", T.update_query(
                  board, "WA.ar934x.v8.7.11.46972.220614.0420", T.Config("gui.language=sp_SP\n"))
              == {"sysid": "0xe7fa", "fwver": "WA.ar934x.v8.7.11.46972.220614.0420",
                  "hash": hashlib.md5(b"68D79A000001").hexdigest(), "lang": "sp_SP"}
              and [T.has_update({"update": x}) for x in ("true", True, "false", False, "")]
              == [True, True, False, False, False])
        try:
            T.update_answer(b'{"update":"true"}')
            short = ""
        except T.AirOSError as e:
            short = str(e)
        check("its answer: with nothing new, {\"update\":\"false\"} alone (as it really "
              "answers); with a new one, all its keys", T.update_answer(b'{"update":"false"}')
              == {"update": "false"} and short == tr(
                  "The update server's answer is not valid (received incomplete update info: "
                  "missing key: {key}): {answer}", key="date", answer='{"update":"true"}'),
              short)
        srv, d, dev, ui, app = start(8, 19312, fw_update="offline")
        before = dev.full_version                   # (afterwards, the new one's)
        ok = run(app, "check_for_update", [("yesno", 0), ("yesno", 0), ("yesno", 0),
                                           ("yesno", 0)], True)
        query = seen["queries"][-1] if seen["queries"] else {}
        b = app._board()
        notes = [t for k, ti, t in ui.log if k == "textbox"
                 and ti == tr("Firmware update {version} is available.", version="v8.7.20")]
        offer = [t for k, ti, t in ui.log if k == "yesno"
                 and ti == tr("Firmware update {version} is available.", version="v8.7.20")]
        licence = [t for k, ti, t in ui.log if k == "textbox" and ti == tr("License")]
        check('no internet on the antenna, "Check from here": it asks Ubiquiti what the antenna '
              "would ask (and nothing without asking first)", ok and query == {
                  "sysid": b.get("board.sysid"), "fwver": before,
                  "hash": hashlib.md5(b["board.hwaddr"].encode()).hexdigest(),
                  "lang": app.original.get("gui.language") or "en_US"}, str(query))
        check("  its notes and, before downloading it, its size", len(notes) == 1
              and "from the fake server" in notes[0] and offer and tr(
                  "Download firmware {version} to this computer ({size}) and upload it to the "
                  "device?", version="v8.7.20", size=f"{len(fw) / 1048576:.1f} MB") == offer[0],
              str((notes, offer)))
        check("  it downloads it with its progress bar, checks it (md5) and keeps it for other "
              "antennas; the license, as when the antenna downloads it", seen["downloads"] == 1
              and T.file_md5(kept) == hashlib.md5(fw).hexdigest() and ui.gauges
              and licence == ["EULA\nFake license."], str((seen["downloads"], licence)))
        check('  and it uploads it as "Upload Firmware" does: the antenna checks it, and "Update" '
              "leaves it with it", "fw-uploaded" in d.events and d.fw_version == "v8.7.20"
              and msgs(ui, title)[-1].startswith(tr("Firmware updated: {old} → {new}.",
                                                    old="v8.7.19", new="v8.7.20")),
              str((d.events[-4:], msgs(ui)[-1:])))
        srv.shutdown()

        srv, d, dev, ui, app = start(8, 19313, fw_update="offline")
        ok = run(app, "check_from_here", [("yesno", 0), ("yesno", 0), ("yesno", 1)])
        again = asked(ui, tr("Firmware update {version} is available.", version="v8.7.20"))
        check('another such antenna: the one already downloaded, without downloading it again; '
              '"Discard" discards it', ok
              and seen["downloads"] == 1 and again == [tr(
                  "Firmware {version} was downloaded to this computer already: {path}\n\nUpload "
                  "it to the device?", version="v8.7.20", path=kept)]
              and "fw-discarded" in d.events and d.fw_version == "v8.7.19", str(again))
        ok = run(app, "upload_firmware", [("input", None)])
        check('  "Upload Firmware" starts at the folder of the downloaded ones',
              ok and ui.inputs[-1] == T.FW_DIR + os.sep, str(ui.inputs[-1:]))
        T.FW_EULA_URL = "http://127.0.0.1:19311/no-license.html"     # (its server: 404)
        uploads = d.events.count("fw-uploaded")
        ok = run(app, "check_from_here", [("yesno", 0)])
        T.FW_EULA_URL = "http://127.0.0.1:19311/eula.html"
        check("if it cannot get its license: it says so, with why, and does not upload it (it "
              "stays downloaded)", ok
              and msgs(ui, title) == [tr("Could not get the license of the firmware: {error}",
                                         error="http://127.0.0.1:19311/no-license.html: Not "
                                               "Found") + "\n\n" + tr(
                  "The firmware stays in {path}: Upload Firmware takes it.", path=kept)]
              and d.events.count("fw-uploaded") == uploads and seen["downloads"] == 1,
              str(msgs(ui)))
        seen["mode"] = "bad"
        uploads = d.events.count("fw-uploaded")
        debug = os.path.join(XDG, "debug-fw.log")
        T.start_debug(debug)
        ok = run(app, "check_from_here", [("yesno", 0)])
        T.stop_debug()
        log = open(debug, encoding="utf-8").read()
        check("if what it downloaded is not what was announced (another md5): it neither keeps "
              "nor uploads it", ok
              and msgs(ui, title) == [tr("The downloaded file is not the firmware the update "
                                         "server announced (its checksum does not match): it "
                                         "is not kept.")]
              and not glob.glob(os.path.join(T.FW_DIR, "*.part"))
              and T.file_md5(kept) == hashlib.md5(fw).hexdigest()
              and d.events.count("fw-uploaded") == uploads, str(msgs(ui)))
        check("  with --debug, this PC's requests are logged too: the query and its answer, "
              "and the download (its size and what happened, not the file)",
              "GET http://127.0.0.1:19311/update-fw/check.php?sysid=0xe7f9&fwver=" in log
              and '"checksum": "00000000000000000000000000000000"' in log
              and f"HEAD http://127.0.0.1:19311/firmwares/XC-fw/v8.7.20/{name} → 200" in log
              and f"GET http://127.0.0.1:19311/firmwares/XC-fw/v8.7.20/{name} → " + tr(
                  "The downloaded file is not the firmware the update server announced (its "
                  "checksum does not match): it is not kept.") in log
              and "UBNT" not in log, log[-600:])
        seen["mode"] = "none"
        ok = run(app, "firmware_menu", [("menu", "h"), ("menu", None)])
        check('if there is no new one (from the menu: "Check Now from this computer"): it says '
              'so, as the antenna does (Ubiquiti answers {"update":"false"} alone)', ok
              and msgs(ui, title) == [tr("You have latest available firmware version.")],
              str(msgs(ui)))
        seen["mode"] = "html"
        ok = run(app, "check_from_here", [])
        check("if it answers something else (an error page): it says so, with what it answered",
              ok
              and msgs(ui) and msgs(ui)[0].startswith(tr(
                  "The update server's answer is not valid (received invalid json): {answer}",
                  answer="<html><body>502 Bad Gateway</body></html>")), str(msgs(ui)))
        seen["mode"] = "incomplete"
        ok = run(app, "check_from_here", [])
        check("an answer with something missing: what the antenna would say", ok and msgs(ui)
              and msgs(ui)[0].startswith(tr(
                  "The update server's answer is not valid (received incomplete update info: "
                  "missing key: {key}): {answer}", key="checksum", answer="")), str(msgs(ui)))
        board = app._board
        app._board = lambda: {}
        ok = run(app, "check_from_here", [])
        app._board = board
        check("without its board's data (its model, its MAC): not asked (the server would answer "
              "for no device, maybe \"up to date\")", ok and msgs(ui, title) == [tr(
                  "The device's model or firmware version is not known: its update cannot be "
                  "checked from here.")], str(msgs(ui)))
        seen["mode"] = "no-url"
        ok = run(app, "check_from_here", [])
        check("an update whose url is not http(s): not valid, said so (not offered)", ok
              and msgs(ui) and msgs(ui)[0].startswith(tr(
                  "The update server's answer is not valid (its url is not an http(s) "
                  "address): {answer}", answer="")[:60]), str(msgs(ui)))
        T.FW_CHECK_URL = "http://127.0.0.1:1/update-fw/check.php"      # (nobody answers)
        ok = run(app, "check_from_here", [])
        check("if this PC does not reach it either: it says so, with the downloads page", ok
              and msgs(ui)
              and msgs(ui)[0].startswith(tr("Could not reach the update server from this "
                                            "computer either: {error}", error="")) and tr(
                  "Go to Ubiquiti downloads page: {url}", url=T.DOWNLOADS_URL) in msgs(ui)[0],
              str(msgs(ui)))
        srv.shutdown()
    finally:
        T.FW_CHECK_URL, T.FW_EULA_URL = real
        ubnt.shutdown()
        ubnt.server_close()


if __name__ == "__main__":
    flash_wait()
    rules()
    upload_and_update()
    check_and_download()
    simulator_option()
    startup()
    airos6()
    from_here()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
