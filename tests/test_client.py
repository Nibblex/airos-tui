"""Tests of airos-tui's HTTP client against the simulator (airOS 8 and 6)."""
import glob
import json
import os
import sys
import threading
import time
import traceback

from _common import check, fails, FIXTURES, suite  # noqa: E402
T, S, XDG = suite("client")
tr = T.tr

RESTART = 1.5
TEST_SECS = 6


def start(version, port):
    srv = S.make_server(version, port, restart=RESTART, test_secs=TEST_SECS)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_back(dev, extra=0.0):
    time.sleep(RESTART + 0.3 + extra)
    for _ in range(40):
        if dev.ping():
            return True
        time.sleep(0.25)
    return False


def run_common(version, port):
    srv = start(version, port)
    d = srv.dev
    flavor = "v8" if version == 8 else "v6"
    print(f"\n=== airOS {version} ===")
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    st = dev.connect()
    check(f"{flavor}: connect/flavor", dev.flavor == flavor and dev.fw_major == version,
          f"flavor={dev.flavor} fw={dev.fw}")
    check(f"{flavor}: auth kind", dev.auth_kind == ("api" if version == 8 else "cgi"),
          dev.auth_kind)
    check(f"{flavor}: status hostname", st["host"]["hostname"] == "AP-North-Tower")
    text = dev.get_config()
    cfg = T.Config(text)
    if version == 8:
        check("v8: password masked in getcfg", cfg.get("users.1.password") == "-")
    else:
        check("v6: password hash in cfg.cgi", cfg.get("users.1.password") == d.pw_hash)
    pw_before = d.saved["users.1.password"]

    # 1) test mode + confirm
    new = cfg.copy()
    T.QUICK_SETTINGS[1].apply(new, "South-Tower")      # SSID
    dev.write_config(new, test=True)
    check(f"{flavor}: device down during restart", not dev.ping())
    check(f"{flavor}: device back", wait_back(dev))
    info = dev.test_mode_info()
    check(f"{flavor}: test mode active", info and info["active"], str(info))
    check(f"{flavor}: running has new SSID", d.running["wireless.1.ssid"] == "South-Tower")
    check(f"{flavor}: saved still old SSID", d.saved["wireless.1.ssid"] == "North-Tower")
    check(f"{flavor}: wpasupplicant/aaa SSID synced",
          d.running["wpasupplicant.profile.1.network.1.ssid"] == "South-Tower"
          and d.running["aaa.1.ssid"] == "South-Tower")
    dev.confirm_test()
    info = dev.test_mode_info()
    check(f"{flavor}: test mode off after confirm", info and not info["active"], str(info))
    check(f"{flavor}: saved has new SSID", d.saved["wireless.1.ssid"] == "South-Tower")
    check(f"{flavor}: password unchanged after write",
          d.saved["users.1.password"] == pw_before, d.saved["users.1.password"])
    if version == 8:
        check("v8: mac-telnetd password unchanged",
              d.saved.get("mac-telnetd.users.1.password") in (pw_before, "-"),
              d.saved.get("mac-telnetd.users.1.password"))

    # 2) test mode + revert
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    new.set("radio.1.txpower", "10")
    dev.write_config(new, test=True)
    wait_back(dev)
    check(f"{flavor}: test2 running txpower 10", d.running["radio.1.txpower"] == "10")
    dev.discard_test()
    wait_back(dev)
    check(f"{flavor}: discard reverted running", d.running["radio.1.txpower"] == "20")
    check(f"{flavor}: discard reverted saved", d.saved["radio.1.txpower"] == "20")
    check(f"{flavor}: event test-discarded", "test-discarded" in d.events)

    # 3) test mode that expires by itself
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    new.set("radio.1.txpower", "12")
    dev.write_config(new, test=True)
    wait_back(dev)
    time.sleep(TEST_SECS + 0.5)
    dev.ping()   # triggers the tick
    wait_back(dev)
    check(f"{flavor}: expired test reverted", d.running["radio.1.txpower"] == "20"
          and "test-expired" in d.events)
    info = dev.test_mode_info()
    check(f"{flavor}: test mode off after expiry", info and not info["active"], str(info))

    # 4) apply directly
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    T.QUICK_SETTINGS[0].apply(new, "AP-South-Tower")
    dev.write_config(new, test=False)
    wait_back(dev)
    check(f"{flavor}: direct apply saved hostname",
          d.saved["resolv.host.1.name"] == "AP-South-Tower")
    check(f"{flavor}: password unchanged after direct apply",
          d.saved["users.1.password"] == pw_before)

    # 5) delete a key
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    new.delete("resolv.nameserver.1.ip")
    dev.write_config(new, test=False)
    wait_back(dev)
    check(f"{flavor}: key deleted", "resolv.nameserver.1.ip" not in d.saved)

    # 6) expired session -> transparent relogin
    d.sessions.clear()
    try:
        st = dev.status()
        check(f"{flavor}: relogin after session loss", st["host"]["hostname"] == "AP-South-Tower")
    except Exception as e:
        check(f"{flavor}: relogin after session loss", False, repr(e))

    # 7) wrong password
    bad = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "wrong")
    try:
        bad.connect()
        check(f"{flavor}: wrong password rejected", False, "connect succeeded")
    except T.AuthError as e:
        check(f"{flavor}: wrong password rejected", True)
        print("       message:", e)

    # 8) invalid configuration
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    new.set("radio.status", "disabled")
    try:
        dev.write_config(new, test=True)
        check(f"{flavor}: invalid cfg rejected", False, "no error")
    except T.AirOSError as e:
        rejected = tr("The device rejected the configuration: {reason}").partition("{")[0]
        check(f"{flavor}: invalid cfg rejected", rejected in str(e), str(e))
        print("       message:", e)
    time.sleep(0.2)

    # 9) reboot
    dev.reboot()
    check(f"{flavor}: reboot event", "reboot" in d.events)
    wait_back(dev, extra=RESTART)
    st = dev.status()
    check(f"{flavor}: status after reboot (relogin)", st["host"]["hostname"] == "AP-South-Tower")
    return srv, dev


def v6_specific(srv, dev):
    d = srv.dev
    print("\n=== airOS 6: token ===")
    # stale token: the device ignores the upload without a word -> it must be detected
    orig = dev._v6_token
    dev._v6_token = lambda path: "stale-token"
    cfg = T.Config(dev.get_config())
    new = cfg.copy()
    new.set("radio.1.txpower", "15")
    try:
        dev.write_config(new, test=True)
        check("v6: stale token detected", False, "no error")
    except T.AirOSError as e:
        check("v6: stale token detected", tr("The device did not accept the uploaded "
                                             "configuration (unchanged: {keys}).",
                                             keys="radio.1.txpower") in str(e), str(e))
        print("       message:", e)
    dev._v6_token = orig
    check("v6: nothing applied with stale token", d.running["radio.1.txpower"] == "20"
          and not d.test_until)
    dev._v6_token = lambda path: None                  # (a page without it)
    try:
        dev.discard_test()
        raised = None
    except T.AirOSError as e:
        raised = str(e)
    dev._v6_token = orig
    check("v6: without its token, a button's POST is not sent (it would do nothing, and look "
          "done)", raised == tr("{path} did not hand over its session token: nothing was "
                                "sent.", path="system.cgi"), str(raised))
    # session lost right before the upload: relogin + a new token
    d.sessions.clear()
    dev.write_config(new, test=False)
    wait_back(dev)
    check("v6: upload after session loss", d.saved["radio.1.txpower"] == "15")
    print("\n=== airOS 6: the radio's capabilities, which board.info lacks ===")
    board = dev.board_info()
    d.radio_caps = 1024
    again = dev.board_info()
    check('v6: board.info + radio_caps from the System page (its "fast clock")',
          board.get("radio.1.caps") == "8192" and again.get("radio.1.caps") == "1024"
          and board.get("radio.1.txpower.max") == "27", str(board.get("radio.1.caps")))


def offline_tests():
    print("\n=== Config / validation / status ===")
    c = T.Config("a=1\r\nb=x=y\n\n#c=3\nwireless.1.ssid= with spaces \n")
    check("parse value with '='", c.get("b") == "x=y")
    check("parse keeps spaces", c.get("wireless.1.ssid") == " with spaces ")
    check("comments ignored", "#c" not in c and len(c) == 3)
    c2 = c.copy()
    check("an IPv6 address typed without its brackets: its colons are not a port",
          [(d.host, d.port, d.scheme) for d in (
              T.AirOS("fe80::1", "u", "p"), T.AirOS("https://2001:db8::20", "u", "p"),
              T.AirOS("[2001:db8::20]:8443", "u", "p"))]
          == [("fe80::1", 80, None), ("2001:db8::20", 443, "https"),
              ("2001:db8::20", 8443, None)])
    bad_loc = T.AirOS("192.0.2.1", "u", "p")
    bad_loc._raw = lambda *a, **k: (302, {"Location": "https://192.0.2.1:99999/"}, b"")
    try:
        bad_loc.probe()
        probed = (bad_loc.scheme, bad_loc.port)
    except Exception as e:                      # (it used to escape: a traceback)
        probed = repr(e)
    check("a web that sends to HTTPS with a broken Location: HTTPS on its usual port, not a "
          "crash", probed == ("https", 443), str(probed))
    c2.set("a", "2")
    c2.delete("b")
    c2.set("n", "z")
    check("diff", c.diff(c2) == [("a", "1", "2"), ("b", "x=y", None), ("n", None, "z")],
          str(c.diff(c2)))
    # A repeated key: what its web reads (airOS 6's, emulated, reads all of these so; what the
    # device applies may be another: see Config)
    k = "wireless.1.ssid"
    first = f"{k}=A\nradio.1.freq=5745\n{k}=B\nx=1\n{k}=C\n"
    check("repeated key: the last one (v8; v6 if it is not the first entry); written once, "
          "where it first appeared",
          T.Config(first).get(k) == "C" and T.Config(f"x=1\n{k}=A\n{k}=B\n", "v6").get(k) == "B"
          and T.Config(first).text() == f"{k}=C\nradio.1.freq=5745\nx=1\n")
    check("v6: if one is the first entry, that one (as its web does); written once",
          T.Config(first, "v6").get(k) == "A"
          and T.Config(first, "v6").text() == f"{k}=A\nradio.1.freq=5745\nx=1\n")
    check("v6: what is not an entry does not count (empty, a comment even with '=', no '='); "
          "neither the \\r nor spaces before the key change which one it is",
          all(T.Config(f"{lead}\n {k}=A\n{k}=B\n", "v6").get(k) == "A"
              for lead in ("", "# a=b", "junk", f"{k}=A\r")))
    check("repeated keys with different values, in the text's order (equal ones do not count; "
          "a copy, the working configuration, has none)",
          T.Config(first).repeated == T.Config(first, "v6").repeated == {k: ["A", "B", "C"]}
          and T.Config(f"{k}=A\n{k}=A\nx=1\n").repeated == {}
          and T.Config(first).copy().repeated == {})
    check("v6: a line '=x' is indeed the first entry (and the rule no longer applies)",
          T.Config(f"=x\n{k}=A\n{k}=B\n", "v6").get(k) == "B"
          and "" not in T.Config(f"=x\n{k}=A\n", "v6"))

    class V6:
        flavor = "v6"
    app = T.App(None, V6())
    app.set_loaded(first)
    check("the app reads an airOS 6 configuration with its rule",
          app.original.get(k) == app.working.get(k) == "A" and not app.changes())
    check("strip v8",
          "users.1.password" not in T.Config("users.1.password=-\nx=1\n").text(T.V8_STRIP_RE))
    ap = T.Config("radio.1.mode=Master\naaa.1.wpa.psk=AAA\n"
                  "wpasupplicant.profile.1.network.1.psk=WWW\n")
    sta = T.Config("radio.1.mode=Managed\naaa.1.wpa.psk=AAA\n"
                   "wpasupplicant.profile.1.network.1.psk=WWW\n")
    q = T.QUICK_SETTINGS[2]
    check("psk current AP", q.current(ap) == "AAA")
    check("psk current STA", q.current(sta) == "WWW")
    t = q.apply(sta, "new-passkey-1")
    check("psk sets both",
          sta.get("aaa.1.wpa.psk") == sta.get("wpasupplicant.profile.1.network.1.psk")
          == "new-passkey-1" and len(t) == 2)
    check("psk not created", T.QUICK_SETTINGS[2].apply(T.Config("x=1\n"), "abcdefgh1") == [])
    host = T.Config("x=1\n")
    check("hostname creates + status",
          T.QUICK_SETTINGS[0].apply(host, "X") == ["resolv.host.1.name", "resolv.host.1.status"])
    check("validate ssid long", T._v_ssid("x" * 33) is not None)
    check("validate psk short", T._v_psk("1234567") is not None)
    check("validate psk hex64", T._v_psk("a" * 64) is None)
    check("validate ip", T.validate_value("netconf.3.ip", "192.168.1.300") is not None
          and T.validate_value("netconf.3.ip", "10.0.0.2") is None)
    check("validate txpower", T._v_txpower("27") is None and T._v_txpower("abc") is not None)
    check("protected", T.PROTECTED_RE.match("users.2.password")
          and not T.PROTECTED_RE.match("users.1.name"))
    # the status, from python-airos's real JSON files
    ok = True
    for p in sorted(glob.glob(os.path.join(FIXTURES, "*.json"))):
        try:
            data = json.load(open(p))
            flavor = "v6" if "v6" in os.path.basename(p) else "v8"
            out = T.format_status(data, flavor)
            T.short_status(data)
            assert tr("── Wireless ──") in out
        except Exception:
            ok = False
            print("     ", p)
            traceback.print_exc()
    check("format_status on python-airos fixtures", ok)
    sample = json.load(open(os.path.join(FIXTURES, "NanoBeam_5AC_ap-ptmp_v8.7.18.json")))
    print("\n--- sample status screen (fixture NanoBeam 5AC, airOS 8.7.18) ---")
    print("\n".join(T.format_status(sample, "v8").split("\n")[:32]))
    sample6 = json.load(open(os.path.join(FIXTURES, "NanoStation_M5_sta_v6.3.16.json")))
    print("--- sample (fixture NanoStation M5, airOS 6.3.16) ---")
    print(T.format_status(sample6, "v6"))


def probe_without_session(port, probe_port):
    """What answers at another address may be another device: the probe neither sends it the
    session nor keeps the one it hands out."""
    import http.server
    seen, moved = [], [None]

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Cookie"), self.headers.get("X-CSRF-ID")))
            if moved[0]:
                self.send_response(302)
                self.send_header("Location", moved[0])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Set-Cookie", "AIROS_SESSIONID=other-device; Path=/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    print("\n=== probing another address, without the session ===")
    other = http.server.ThreadingHTTPServer(("127.0.0.1", probe_port), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    srv = start(8, port)
    try:
        dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
        dev.connect()
        before = (dict(dev.cookies), dev.csrf)
        answered = dev.ping("127.0.0.1", web=("http", probe_port))
        check("it answers without being sent the session's cookie or token, and the session "
              "stays the same", answered and seen == [("/poll.cgi", None, None)] and before[0]
              and (dict(dev.cookies), dev.csrf) == before, str((seen, dev.cookies)))
        check("  and the session still works", dev.status().get("host", {}).get("hostname"))
        web = ("http", probe_port)
        moved[0] = "https://127.0.0.1/poll.cgi"            # (its HTTP once its HTTPS is on)
        to_https = (dev.ping("127.0.0.1", web=web), dev.ping("127.0.0.1", web=web,
                                                              moved_ok=False))
        moved[0] = "/login.cgi?uri=/poll.cgi"               # (its own login: airOS 6)
        to_login = dev.ping("127.0.0.1", web=web, moved_ok=False)
        moved[0] = None
        check("ping: an answer that only sends to another web counts as an answer, but not with "
              "moved_ok=False; one to its own login does", to_https == (True, False) and to_login,
              str((to_https, to_login)))
        twin = http.server.ThreadingHTTPServer(("127.0.0.2", port), Other)   # (the same port)
        threading.Thread(target=twin.serve_forever, daemon=True).start()
        try:
            seen.clear()
            got = dev.status_at("127.0.0.2")
            check("status_at: with this session (on purpose) and without logging in there; its "
                  "cookie does not change the session, and it still points at the device",
                  got == {}
                  and [(p, c is not None) for p, c, _ in seen] == [("/status.cgi", True)]
                  and (dict(dev.cookies), dev.csrf) == before and dev.host == "127.0.0.1",
                  str((seen, dev.cookies, dev.host)))
        finally:
            twin.shutdown()
            twin.server_close()
    finally:
        other.shutdown()
        other.server_close()
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    offline_tests()
    probe_without_session(18090, 18091)
    run_common(8, 18088)
    srv6, dev6 = run_common(6, 18086)
    v6_specific(srv6, dev6)
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
