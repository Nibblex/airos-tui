"""Tests of App's logic (with a simulated UI), HTTPS and redirects."""
import os
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _common import check, fails, FakeUI, suite, WORK  # noqa: E402
T, S, XDG = suite("app")
tr = T.tr


def start(version, port, restart=1.0, test_secs=4):
    srv = S.make_server(version, port, restart=restart, test_secs=test_secs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def app_for(port, script):
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI(script)
    app = T.App(ui, dev, wait=1)
    app.status = dev.status()
    app.set_loaded(dev.get_config())
    return app, ui, dev


def flows(version, port):
    fl = f"v{version}"
    srv = start(version, port)
    d = srv.dev
    print(f"=== App airOS {version} ===")

    # quick settings: SSID
    app, ui, dev = app_for(port, [("menu", "2"), ("input", "South-Link"), ("menu", None)])
    app.quick_settings()
    # the keys each web's Wireless page writes on an AP with WPA2-PSK
    want = (["aaa.1.ssid", "wireless.1.ssid", "wpasupplicant.profile.1.network.1.ssid"]
            if version == 8 else ["aaa.1.ssid", "wireless.1.ssid"])
    check(f"{fl}: quick SSID", app.working.get("wireless.1.ssid") == "South-Link"
          and [k for k, _, _ in app.changes()] == want, str(app.changes()))

    # editor: change and delete
    app, ui, dev = app_for(port, [("input", "txpower"), ("menu", "radio.1.txpower"),
                                  ("menu", "1"), ("input", "15"), ("menu", None)])
    app.edit_keys()
    check(f"{fl}: edit key", app.working.get("radio.1.txpower") == "15")
    ui.script = [("input", "nameserver"), ("menu", "resolv.nameserver.1.ip"), ("menu", "2"),
                 ("yesno", 0), ("menu", None)]
    app.edit_keys()
    check(f"{fl}: delete key", "resolv.nameserver.1.ip" not in app.working)
    ui.script = [("input", "password"), ("menu", "users.1.password"), ("menu", None)]
    app.edit_keys()
    check(f"{fl}: protected key not editable", tr("Protected key") in ui.titles())
    ui.script = [("input", "x"), ("menu", "+"), ("input", "resolv.nameserver.2.ip"),
                 ("input", "1.1.1.1"), ("menu", None)]
    app.edit_keys()
    check(f"{fl}: add key", app.working.get("resolv.nameserver.2.ip") == "1.1.1.1")
    ui.script = [("input", "x"), ("menu", "+"), ("input", "netconf.3.ip"), ("menu", "1"),
                 ("input", "192.168.1.999"), ("menu", None)]
    app.edit_keys()
    check(f"{fl}: invalid IP rejected", tr("Invalid value") in ui.titles()
          and app.working.get("netconf.3.ip") == "192.168.1.20")

    # apply directly (no test)
    ui.script = [("yesno", 0)]
    app.apply(test=False)
    check(f"{fl}: apply direct saved", d.saved.get("radio.1.txpower") == "15"
          and d.saved.get("resolv.nameserver.2.ip") == "1.1.1.1"
          and "resolv.nameserver.1.ip" not in d.saved)
    check(f"{fl}: apply direct -> no pending", not app.changes() and tr("Done") in ui.titles(),
          str(ui.titles()))
    backups = os.listdir(T.BACKUP_DIR)
    check(f"{fl}: backup written 0600", backups and oct(os.stat(
        os.path.join(T.BACKUP_DIR, backups[0])).st_mode & 0o777) == "0o600")

    # test mode: confirming too late (the device already went back)
    app.working.set("radio.1.txpower", "11")
    ui.script = [("yesno", 0), ("yesno", lambda: (time.sleep(d.test_secs + 1.5), 0)[1])]
    app.apply(test=True)
    check(f"{fl}: late confirm detected",
          tr("Too late") in ui.titles() or tr("No answer") in ui.titles(), str(ui.titles()))
    time.sleep(1.5)
    check(f"{fl}: late confirm keeps pending", app.working.get("radio.1.txpower") == "11"
          and app.original.get("radio.1.txpower") == "15")
    check(f"{fl}: device reverted", d.saved.get("radio.1.txpower") == "15"
          and d.running.get("radio.1.txpower") == "15")

    # test mode started by another session -> pending_test -> Revert
    other = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    other.connect()
    c = T.Config(other.get_config())
    c.set("wireless.1.ssid", "From-Other-Session")
    other.write_config(c, test=True)
    time.sleep(d.restart + 0.3)
    app2, ui2, _ = app_for(port, [("yesno", 1)])
    check(f"{fl}: loaded during test",
          app2.original.get("wireless.1.ssid") == "From-Other-Session")
    app2.pending_test()
    check(f"{fl}: other-session test reverted", d.running.get("wireless.1.ssid") == "North-Tower")
    check(f"{fl}: no fake pending after revert", not app2.changes(), str(app2.changes()))

    # own test mode -> Keep in time
    app2.working.set("radio.1.txpower", "13")
    ui2.script = [("yesno", 0), ("yesno", 0)]
    app2.apply(test=True)
    check(f"{fl}: confirm in time", d.saved.get("radio.1.txpower") == "13"
          and tr("Done") in ui2.titles() and not app2.changes(), str(ui2.titles()))

    # backup + restore
    app2._write_backup(app2.original_text)
    c = T.Config(other.get_config())
    c.set("wireless.1.ssid", "Changed-Outside")
    other.write_config(c, test=False)
    time.sleep(d.restart + 0.3)
    ui2.script = [("menu", "1")]
    app2.set_loaded(app2.dev.get_config())
    app2.restore_backup()
    ch = app2.changes()
    check(f"{fl}: restore backup diff", [k for k, _, _ in ch] == ["wireless.1.ssid"]
          and app2.working.get("wireless.1.ssid") == "North-Tower", str(ch))
    check(f"{fl}: restore keeps device passwords",
          app2.working.get("users.1.password") == app2.original.get("users.1.password"))

    # keys repeated with different values (from a hand edit, a script…): the one its web reads,
    # and a warning, once for the same keys
    dev3 = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev3.connect()
    ui3 = FakeUI()
    app3 = T.App(ui3, dev3, wait=1)
    app3.load()
    quiet = not [x for x in ui3.log if x[1] == tr("Repeated keys")]
    real = dev3.get_config
    dev3.get_config = lambda: real() + "wireless.1.ssid=Other-Net\nusers.1.password=$1$x$y\n"
    app3.load()
    app3.load()
    warned = [x[2] for x in ui3.log if x[1] == tr("Repeated keys")]
    check(f"{fl}: repeated keys: the one its web reads, and a warning once (with their values; "
          "of the hashes, only how many); without repeated keys, nothing",
          quiet and app3.original.get("wireless.1.ssid") == "Other-Net" and len(warned) == 1
          and "Other-Net" in warned[0] and tr("(shown: {value})", value="Other-Net") in warned[0]
          and "$1$x$y" not in warned[0]
          and "users.1.password: " + tr("{count} values", count=2) in warned[0],
          str(warned)[:300])
    srv.shutdown()


def tls_tests():
    print("=== HTTPS and redirects ===")
    tmp = os.path.join(WORK, "tls")
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                    f"{tmp}/k.pem", "-out", f"{tmp}/c.pem", "-days", "1", "-subj", "/CN=airos"],
                   check=True, capture_output=True)
    srv = S.make_server(8, 18443, restart=1, test_secs=5)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{tmp}/c.pem", f"{tmp}/k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS("127.0.0.1:18443", "ubnt", "ubnt")
    dev.connect()
    check("https autodetect", dev.scheme == "https" and dev.flavor == "v8", dev.base)
    check("https get_config", "wireless.1.ssid" in dev.get_config())

    class Redir(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://127.0.0.1:18443/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    r = ThreadingHTTPServer(("127.0.0.1", 18080), Redir)
    threading.Thread(target=r.serve_forever, daemon=True).start()
    dev = T.AirOS("127.0.0.1:18080", "ubnt", "ubnt")
    dev.connect()
    check("http->https redirect followed", dev.base == "https://127.0.0.1:18443"
          and any("HTTPS" in n for n in dev.notes), f"{dev.base} {dev.notes}")
    dev = T.AirOS("127.0.0.1:18099", "ubnt", "ubnt", timeout=3)
    try:
        dev.connect()
        check("unreachable -> error", False)
    except T.AirOSError as e:
        check("unreachable -> error", tr("Could not connect to the device:") in str(e), str(e))
        print("       message:", str(e).replace("\n", " | "))
    try:
        T.AirOS("ftp://1.2.3.4", "a", "b")
        check("bad scheme rejected", False)
    except T.AirOSError:
        check("bad scheme rejected", True)


if __name__ == "__main__":
    flows(8, 18288)
    flows(6, 18286)
    tls_tests()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
