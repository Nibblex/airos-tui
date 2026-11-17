"""Tests of discovery without logging in: the packet's format (with a real one), the search per
interface, the TUI's screen and --discover. Nothing leaves this machine: 127.0.0.1 is used and,
for the broadcast per interface, only the loopback interface."""
import contextlib
import http.client
import http.server
import io
import ipaddress
import itertools
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import types

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("disc")
tr = T.tr

# A real announcement of an airOS 8 station (fixtures/airos_sta_discovery_packet.bin of
# python-airos, with the MAC and the name anonymized).
REAL = bytes.fromhex(
    "01060000000006012345 6789cd02000a 0123456789cdc0a80103 0300 0a57412e56382e372e3137"
    "0a000400040c9f 0b0004 6e616d65 0c0014 4e616e6f53746174696f6e20354143206c6f636f"
    "0d0008 44656d6f53534944 140014 4e616e6f53746174696f6e20354143206c6f636f".replace(" ", ""))


def tlv(kind, value):
    return bytes([kind]) + len(value).to_bytes(2, "big") + value


def reply(mac_hex, ip, name, extra=b""):
    mac = bytes.fromhex(mac_hex)
    body = tlv(0x02, mac + socket.inet_aton(ip)) + tlv(0x01, mac) + tlv(0x0B, name.encode()) + extra
    return b"\x01\x00" + len(body).to_bytes(2, "big") + body


# The firmware an airOS 6 announces (its TLV): what makes a reply an airOS antenna's.
AIROS6 = tlv(0x03, b"XW.ar934x.v6.3.6.33330.210818.1930")


def responder(port, packets, bind="127.0.0.1"):
    """Answers each query with the given packets (for cases the simulator does not cover)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind((bind, port))

    def loop():
        while True:
            try:
                data, addr = s.recvfrom(100)
            except OSError:
                return
            if data[:2] == b"\x01\x00":
                for p in packets:
                    s.sendto(p, addr)
    threading.Thread(target=loop, daemon=True).start()
    return s


def too_big_active(rng, count):
    """What it says of a range too big for the active search."""
    return tr("{range} has {count} addresses: the active search asks them one by one, {most} at "
              "most (a /22). Search a smaller range.", range=rng, count=count, most=1024)


class OtherWeb(http.server.BaseHTTPRequestHandler):
    """A web that is not airOS (a printer's)."""
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"<html><head><title>  Office  Printer </title></head></html>"
        self.send_response(200 if self.path == "/" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SlowFirst(http.server.BaseHTTPRequestHandler):
    """An airOS 8's /api/info/public whose first answer takes 1 s (its TLS, on a small CPU)."""
    calls = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.calls.append(self.path)
        if len(self.calls) == 1:
            time.sleep(1)
        body = b'{"setup_complete":true,"ui_lang":"en_US","product_name":"LiteBeam 5AC"}'
        try:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:                          # (who asked did not wait)
            pass


def redirect_to(port):
    """An HTTP that only sends to HTTPS, as airOS 8's does (paths: what it was asked)."""
    class Redirect(http.server.BaseHTTPRequestHandler):
        paths: list[str] = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.paths.append(self.path)
            self.send_response(301)
            self.send_header("Location", f"https://127.0.0.101:{port}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
    return Redirect


# What it adds when nothing answered discovery and the active search was not done (a msgid:
# tr(ACTIVE_HINT, active=...), --active-scan or the screen's option a)
ACTIVE_HINT = ("The active search ({active}) also finds antennas with discovery off: it asks "
               "each address for its web, without a user or password.")


def too_big(rng, count):
    """What it says of a range too big to ask address by address."""
    return tr("{range} has {count} addresses: outside your networks discovery asks them one by "
              "one, {most} at most (a /22). Search a smaller range.", range=rng, count=count,
              most=1024)


def unit():
    print("=== the packet ===")
    d = T.parse_discovery(REAL, "192.168.1.3")
    want = {"ip": "192.168.1.3", "mac": "01:23:45:67:89:CD", "hostname": "name",
            "model": "NanoStation 5AC loco", "fw": "WA.V8.7.17", "version": "8.7.17",
            "uptime": 265375, "essid": "DemoSSID"}
    check("a real airOS 8 announcement (header 01 06)",
          d and all(d[k] == v for k, v in want.items()), str(d and {k: d[k] for k in want}))
    sim = S.Device(6, "ubnt", "ubnt", 1, 10, mgmt_ip="127.0.0.81", station=True)
    s = T.parse_discovery(sim.discovery_reply(), "127.0.0.1")
    check("the reply of the simulated airOS 6 station", s["ip"] == "127.0.0.81"
          and s["wmode"] == 2
          and s["platform"] == "NSM5" and s["version"] == "6.3.16" and s["uptime"] >= 3 * 86400
          and s["pairs"] == [("68:D7:9A:98:FB:FF", "127.0.0.81")], str(s))
    raw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "real",
                            "airos8.7.14-nanostation5ac-discovery.bin"), "rb").read()
    ns = T.parse_discovery(raw, "192.168.1.20")
    check("the real reply of a NanoStation 5AC loco (8.7.14) to the v1 query: its two addresses "
          "(the fallback one and the automatic one), name, model, firmware and uptime",
          ns and (ns["mac"], ns["addrs"], ns["hostname"], ns["model"], ns["platform"],
                  ns["version"], ns["essid"], ns["wmode"], ns["uptime"]) == (
              "FC:EC:DA:00:00:01", ["192.168.1.20", "169.254.0.1"], "Test Tower1",
              "NanoStation 5AC loco", "Loco5AC", "8.7.14", "", 2, 237), str(ns))
    eth1 = (3, "eth1", "192.168.1.100", "255.255.255.0")
    wlan = (2, "wlan0", "172.18.1.251", "255.255.255.0")
    check("where the query goes: the general broadcast through all of them; one of their "
          "networks' broadcast, the general one only through that one (airOS does not answer "
          "the network's); an IP, as it is", [
              T.probe_plan(t, [wlan, eth1]) for t in (
                  "255.255.255.255", "192.168.1.255", "10.0.0.255", "192.168.1.20")] == [
              ("255.255.255.255", [wlan, eth1]), ("255.255.255.255", [eth1]),
              ("10.0.0.255", []), ("192.168.1.20", [])])
    body = tlv(0x02, bytes.fromhex("0026A1B2C3D4") + socket.inet_aton("10.0.0.3")) + AIROS6
    only = T.parse_discovery(b"\x01\x00" + len(body).to_bytes(2, "big") + body, "10.0.0.3")
    check("a reply with only its MAC and IP: its MAC as the others' (with colons)",
          only and only["mac"] == "00:26:A1:B2:C3:D4", str(only and only["mac"]))
    heads = [http.client.parse_headers(io.BytesIO(h)) for h in (
        b"Location: /cgi-bin/login.cgi\r\n\r\n", b"Location: /cookiechecker?uri=/\r\n\r\n",
        b"Set-Cookie: AIROS_0026A1B2C3D4=x; Path=/\r\nLocation: /login.cgi?uri=/\r\n\r\n")]
    check("airOS 6: by its cookie, or its cookie checker; not any web that sends to a login.cgi",
          [T._airos6_mac(h) for h in heads] == [None, "", "00:26:A1:B2:C3:D4"],
          str([T._airos6_mac(h) for h in heads]))
    first = T.parse_discovery(reply("0026A1000001", "192.168.1.20", "Factory-1"), "192.168.1.20")
    second = T.parse_discovery(reply("0026A1000002", "192.168.1.20", "Factory-2"),
                               "192.168.1.20")
    merged = T.merge_neighbors([first, second], [dict(second, protocols=["lldp"], hostname="")])
    check("two at the same address (two antennas out of the box): what CDP or LLDP announced goes "
          "to the one with its MAC, not to the first", [(d["hostname"], d["protocols"])
                                                       for d in merged]
          == [("Factory-1", ["ubnt"]), ("Factory-2", ["ubnt", "lldp"])],
          str([(d["hostname"], d["protocols"]) for d in merged]))
    check("packets that are not discovery replies",
          T.parse_discovery(b"\x02\x00\x00\x00", "x") is None
          and T.parse_discovery(b"\x01\x00", "x") is None
          and T.parse_discovery(b"\x01\x00\x00\x00" + tlv(0x0B, b"no-mac"), "x") is None)
    cut = T.parse_discovery(reply("0026A1B2C3D4", "10.0.0.2", "cut") + b"\x0d\x00\x09ab",
                            "10.0.0.2")
    check("a cut TLV: it keeps what it read", cut and cut["hostname"] == "cut"
          and cut["essid"] == "")
    check("airOS version", T.airos_version("XW.ar934x.v6.3.6.33330.210818.1930") == "6.3.6"
          and T.airos_version("2XC.qca956x.v8.7.11.46972.220614.0420") == "8.7.11"
          and T.airos_version("BZ.mt7621.v6.6.55.15189.230830.1123") == ""
          and T.airos_version("") == "")
    check("the IP suggested for the PC", T._suggest_ip("192.168.1.20") == "192.168.1.100"
          and T._suggest_ip("10.0.0.100") == "10.0.0.101")

    print("=== ranges in CIDR notation ===")
    check("a range as it is typed: normalized (any IP of it; the bits or the netmask); one that "
          "is not a range, none; IP:port, its IP, as before", [T.discovery_host(t) for t in (
              "10.3.0.0/24", " 10.3.0.77/24 ", "10.3.0.0/255.255.255.0", "10.3.0.0/33",
              "300.1.1.0/24", "10.3.0.2:8443")] == [
              "10.3.0.0/24", "10.3.0.0/24", "10.3.0.0/24", None, None, "10.3.0.2"])
    big = (4, "eth2", "10.20.0.5", "255.255.0.0")
    plan = T.query_plan("10.3.0.0/30", [wlan, eth1])
    check("a range of no network of yours: address by address (without its network and "
          "broadcast addresses), the answers only from it",
          (plan[0], plan[1], str(plan[2])) == (["10.3.0.1", "10.3.0.2"], [], "10.3.0.0/30"),
          str(plan))
    check("  one address (/32): that one", T.query_plan("10.3.0.7/32", [])[0] == ["10.3.0.7"])
    check("one of your networks: its broadcast, only through that interface (as 192.168.1.255)",
          T.query_plan("192.168.1.0/24", [wlan, eth1]) == (["255.255.255.255"], [eth1], None))
    part = T.query_plan("192.168.1.0/26", [wlan, eth1])
    check("  a part of one: the same, keeping only the answers from that part",
          part[:2] == (["255.255.255.255"], [eth1]) and str(part[2]) == "192.168.1.0/26",
          str(part))
    check("what it cannot ask: a range that is not one; outside your networks, more than a /22 "
          "(address by address); one of yours, however big, it can (one broadcast)",
          [T.target_problem(t, [wlan, eth1, big]) for t in (
              "10.3.0.0/33", "10.0.0.0/21", "10.0.0.0/22", "10.20.0.0/16")] == [
              tr('"{target}" is not a valid range (for example 10.3.0.0/24).',
                 target="10.3.0.0/33"),
              too_big("10.0.0.0/21", 2048), None, None])
    try:
        T.query_plan("10.0.0.0/21", [])
        refused = False
    except ValueError:
        refused = True
    check("  (and the search itself refuses it too)", refused)

    print("=== the device's data ===")
    base = T.parse_discovery(reply("0026A1B2C3D4", "192.168.1.20", "factory",
                                   tlv(0x03, b"XW.ar934x.v6.3.6.33330.210818.1930")
                                   + tlv(0x0E, b"\x03") + tlv(0x0D, b"Network")), "192.168.1.20")
    text = T.format_discovered(base)
    check("mode, SSID and firmware", f"{tr('access point')}, SSID Network" in text
          and "(airOS 6.3.6)" in text)
    check("on another network: how to get in", tr(
        "None of your interfaces is on the network of {ip}. If it is on your same cable, add an "
        "IP of its network to that interface (for example {suggest}/24) to be able to get in.",
        ip="192.168.1.20", suggest="192.168.1.100") in text, text)
    base["local"] = "eth1"
    check("on your network: you can connect",
          tr("It is on the network of your interface {iface}: you can connect.", iface="eth1")
          in T.format_discovered(base))
    other = T.parse_discovery(reply("0026A1B2C3D5", "10.1.1.1", "unifi",
                                    tlv(0x03, b"BZ.mt7621.v6.6.55.15189")), "10.1.1.1")
    check("only airOS antennas are listed: not another Ubiquiti device (UniFi)",
          not T.is_airos(other) and T.is_airos(base) and T.airos_only([other, base]) == [base])
    zero = T.parse_discovery(reply("0026A1B2C3D6", "0.0.0.0", "no-ip"), "0.0.0.0")
    check("no usable IP: it says so", tr("It has no usable IP: get in through the automatic IP "
                                         "169.254.x.x or set one up from the web.")
          in T.format_discovered(zero))
    check("the data in JSON", T.discovery_json(base)["mode"] == "ap"
          and T.discovery_json(base)["airos"] == "6.3.6"
          and T.discovery_json(base)["addresses"] == [{"mac": "00:26:A1:B2:C3:D4",
                                                       "ip": "192.168.1.20"}])


def search():
    print("=== the search ===")
    srv = S.make_server(8, 18830, restart=0.5, mgmt_ip="127.0.0.82", discovery_port=18831)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    found = T.discover(1.2, target="127.0.0.1", port=18831)
    check("at a given IP", len(found) == 1 and found[0]["ip"] == "127.0.0.82"
          and found[0]["local"] == "lo", str(found))
    srv.dev.running["discovery.status"] = "disabled"
    check("with discovery off it does not answer",
          T.discover(1.0, target="127.0.0.1", port=18831) == [])
    srv.dev.running["discovery.status"] = "enabled"
    srv.cluster.shutdown()

    loopback = socket.if_nametoindex("lo")
    two = [reply("0026A1000001", "192.168.1.20", "factory-1"),
           reply("0026A1000002", "192.168.1.20", "factory-2")]
    r = responder(18833, two, bind="")
    found = T.discover(1.2, port=18833, interfaces=[(loopback, "lo", "127.0.0.1", "255.0.0.0")])
    check("broadcast per interface (IP_PKTINFO, here only loopback)", len(found) == 2, str(found))
    check("two factory-default antennas with the same IP: both, by MAC",
          [d["hostname"] for d in found] == ["factory-1", "factory-2"])
    r.close()
    check("nobody to answer", T.discover(0.8, target="127.0.0.1", port=18835) == [])

    print("=== a range ===")
    rs = [responder(18836, [reply(f"0026A10000{n}", f"127.0.0.{n}", f"range-{n}")],
                    bind=f"127.0.0.{n}") for n in (90, 93, 97)]
    found = T.discover(1.2, target="127.0.0.88/29", port=18836)
    check("a range that is no network of yours (127.0.0.88/29): it asks each address, and only "
          "those (not 127.0.0.97)", [d["hostname"] for d in found] == ["range-90", "range-93"],
          str(found))
    for r in rs:
        r.close()
    lo = [(loopback, "lo", "127.0.0.1", "255.0.0.0")]
    r = responder(18837, two, bind="")
    found = T.discover(1.2, target="127.0.0.0/8", port=18837, interfaces=lo)
    check("one of your networks (here loopback's, 127.0.0.0/8: 16 million addresses): one "
          "broadcast through that interface", len(found) == 2, str(found))
    check("  a part of it: only what answers from that part (they answer from 127.0.0.1)",
          len(T.discover(1.0, target="127.0.0.0/24", port=18837, interfaces=lo)) == 2
          and T.discover(1.0, target="127.0.1.0/24", port=18837, interfaces=lo) == [])
    r.close()


def screens():
    print("=== the TUI's screen (without logging in) ===")
    srv = S.make_server(8, 18840, restart=0.5, mgmt_ip="127.0.0.83", discovery_port=18841)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    T.DISCOVERY_PORT, T.DISCOVERY_SECONDS = 18841, 1.0
    ui = FakeUI([("menu", "1"), ("yesno", 1), ("menu", "r"), ("menu", "d"), ("input", "127.0.0.9"),
                 ("menu", "d"), ("input", "127.0.0.1"), ("menu", "1"), ("yesno", 0)])
    ip = T.discovery_screen(ui, "127.0.0.1")
    lists = [(text, dict(items)) for (t, items, _), (k, _t, text) in
             zip(ui.menus, [x for x in ui.log if x[0] == "menu"])]
    # (what the list says above it: one device on 127.0.0.1)
    on = tr("{count} device(s) on {where}. Choose one to see its data; no user or password "
            "needed.", count=1, where="127.0.0.1")
    check("a numbered list with the device", lists
          and lists[0][1].get("1", "").startswith("127.0.0.83")
          and "AP-North-Tower" in lists[0][1]["1"] and "airOS 8.7.19" in lists[0][1]["1"],
          str(lists[:1]))
    check("it says no user or password is needed", on in lists[0][0])
    details = [t for k, ti, t in ui.log if k == "yesno"]
    check("the device's data", details and "LBE-5AC-Gen2" in details[0]
          and tr("Up for") in details[0]
          and f"{tr('access point')}, SSID North-Tower" in details[0])
    nobody = [text for text, _ in lists
              if tr("No airOS antenna answered on {where}.", where="127.0.0.9") in text]
    check("at another address with no answer: it says so, with advice", nobody
          and "Discovery" in nobody[0])
    check('"Connect" returns the IP', ip == "127.0.0.83", str(ip))
    ui = FakeUI([("menu", "d"), ("input", "[::1"), ("menu", "r"), ("menu", "d"),
                 ("input", "127.0.0.1:8443"), ("menu", None)])
    T.discovery_screen(ui, "127.0.0.1")
    texts = [text for k, _t, text in ui.log if k == "menu"]
    bad = [text for k, _t, text in ui.log if k == "msgbox"]
    check('"Search at a specific address" with one that is not an address: it says so, and the '
          'list and "Search again" stay on the previous one', not ui.script and bad == [tr(
              '"{target}" is not an IP or a name that can be resolved.', target="[::1")]
          and len(texts) == 4 and all(t.startswith(on) for t in texts[:3]), str((bad, texts)))
    check("  and IP:port, as for connecting, searches at that IP", len(texts) == 4
          and texts[3].startswith(on), str(texts[3:]))

    real = T.socket.getaddrinfo

    def fail(*a, **k):
        raise socket.gaierror("no")
    T.socket.getaddrinfo = fail
    try:
        ui = FakeUI([("menu", None)])
        T.discovery_screen(ui, "no-such-name")
    finally:
        T.socket.getaddrinfo = real
    check("an address that cannot be resolved: it says so", tr("Discovery") in ui.titles())

    rs = [responder(18841, [reply(f"0026A10000{n}", f"127.0.0.{n}", f"range-{n}", AIROS6)],
                    bind=f"127.0.0.{n}") for n in (90, 93)]
    ui = FakeUI([("menu", "d"), ("input", "127.1.0.0/16"), ("menu", None)])
    T.discovery_screen(ui, "127.0.0.95/29")          # (as --target comes: any IP of the range)
    texts = [text for k, _t, text in ui.log if k == "menu"]
    check("a range as the target: the devices of the range",
          texts and texts[0].startswith(tr(
              "{count} device(s) on {where}. Choose one to see its data; no user or password "
              "needed.", count=2, where="127.0.0.88/29")), str(texts[:1]))
    check("  one too big to ask address by address: it says so, and asks nothing",
          ui.titles() == [tr("Discovery")] and [t for k, _t, t in ui.log if k == "msgbox"] == [
              too_big("127.1.0.0/16", 65536)], str(ui.log[-1:]))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = T.main(["--discover", "--target", "127.0.0.88/29", "--timeout", "1"])
    check("--discover with a range", rc == 0 and "range-90" in out.getvalue()
          and "range-93" in out.getvalue(), out.getvalue() + err.getvalue())
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        bad = [T.main(["--discover", "--target", t, "--timeout", "1"])
               for t in ("127.0.0.0/16", "127.0.0.0/33")]
    check("  too big, or not a range: exit code 2 and why, before asking anything",
          bad == [2, 2] and too_big("127.0.0.0/16", 65536) in err.getvalue()
          and tr('"{target}" is not a valid range (for example 10.3.0.0/24).',
                 target="127.0.0.0/33") in err.getvalue(), err.getvalue())
    for r in rs:
        r.close()

    print("=== from the connection screen ===")
    os.environ["AIROS_PASSWORD"] = "ubnt"
    args = types.SimpleNamespace(device=None, user="ubnt", http=True, https=False,
                                 target="127.0.0.1")
    ui = FakeUI([("input", ""), ("menu", "1"), ("yesno", 0), ("input", None)])
    T.connect_screen(ui, args)
    errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Connection error")]
    asked = [t for k, ti, t in ui.log if k == "input" and ti == tr("Connect")]
    check("empty = search, without asking for a user or password first",
          asked and tr("IP or name of the device (http://IP:port or https://IP work too).\nLeave "
                       "it empty to search for antennas on your network, with no user or "
                       "password:") in asked[0])
    check("when you choose one, it tries to log into that IP", errors
          and "127.0.0.83" in errors[0], str(errors))

    print("=== --discover ===")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = T.main(["--discover", "--target", "127.0.0.1", "--timeout", "1"])
    check("a table in the terminal", rc == 0 and "AP-North-Tower" in out.getvalue()
          and "127.0.0.83" in out.getvalue()
          and tr("{count} device(s).", count=1) in err.getvalue(), out.getvalue())
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = T.main(["--discover", "127.0.0.1", "--timeout", "1", "--json"])
    data = json.loads(out.getvalue())
    check("JSON (the device as the target)", rc == 0 and data[0]["ip"] == "127.0.0.83"
          and data[0]["mode"] == "ap" and data[0]["airos"] == "8.7.19"
          and data[0]["platform"] == "LBE-5AC-Gen2" and isinstance(data[0]["uptime"], int),
          str(data))
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = T.main(["--discover", "--target", "127.0.0.9", "--timeout", "0.8"])
    check("no answer: exit code 1 and advice, with the active search that finds them anyway",
          rc == 1
          and tr("No airOS antenna answered on {where}.", where="127.0.0.9") in err.getvalue()
          and tr(ACTIVE_HINT, active="--active-scan") in err.getvalue(), err.getvalue())
    srv.cluster.shutdown()


def tools():
    print("=== Tools → Discovery (connected to a device) ===")
    srv = S.make_server(8, 18850, restart=0.5, mgmt_ip="127.0.0.84")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # (the device this session is connected to, and another one: the simulator answers
    # discovery only for itself)
    r = responder(18851, [reply("0026A1000084", "127.0.0.84", "This-Device", AIROS6),
                          reply("0026A1000085", "127.0.0.85", "Other-Device", AIROS6)])
    real_reachable = T.reachable
    try:
        dev = T.AirOS("http://127.0.0.84:18850", "ubnt", "ubnt")
        dev.connect()
        ui = FakeUI()
        app = T.App(ui, dev, wait=1)
        app.discovery = {"target": "127.0.0.1", "port": 18851}
        app.load()
        T.DISCOVERY_SECONDS = 1.0
        ui.script = [("menu", "p"), ("menu", "1"), ("menu", None), ("menu", None)]
        ok = app._do(app.tools_menu)
        tools_items = dict(ui.menus[0][1])
        check('in "Tools", next to the antenna\'s', tools_items.get("p") == tr(
            "Discovery from your PC: the Ubiquiti devices on your network")
              and tools_items.get("d") == tr(
                  "Discovery: the devices the antenna finds, as in the web"), str(tools_items))
        listed = dict(ui.menus[1][1])
        legend = [t for k, ti, t in ui.log if k == "menu" and ti == tr("Discovery")]
        check("the list marks the device it is connected to (at the start: it is not cut off)",
              listed.get("1", "").startswith("→ 127.0.0.84 ") and "This-Device" in listed["1"]
              and listed.get("2", "").startswith("  127.0.0.85 ") and legend
              and legend[0].endswith("\n" + tr("→ = the device you are connected to.")),
              str(listed))
        shown = [t for k, ti, t in ui.log if k == "msgbox" and ti == "This-Device"]
        check('  (its data, without offering "Connect")', ok and shown
              and shown[0].endswith(tr("You are connected to this device."))
              and not [x for x in ui.log if x[0] == "yesno"], str(ui.log[-3:]))
        ui.log.clear()
        ui.script = [("menu", "p"), ("menu", "2"), ("yesno", 0), ("menu", None)]
        ok = app._do(app.tools_menu)
        told = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Connect")]
        check('"Connect" to one this PC does not reach (nobody on its ports 80 and 443): it says '
              "so and stays connected to this one", ok and app.next_target is None and told
              and "127.0.0.85" in told[0] and "127.0.0.84" in told[0], str(told))
        T.reachable = lambda host, timeout=2.0, ports=(443, 80): True   # (as if it got there)
        app.working.set("radio.1.txpower", "12")
        ui.log.clear()
        ui.script = [("menu", "p"), ("menu", "2"), ("yesno", 0), ("yesno", 1), ("menu", None)]
        ok = app._do(app.tools_menu)
        asked = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Connect")]
        check('another device with unapplied changes: it asks; "No" stays', ok
              and app.next_target is None and asked
              and tr("There are {count} unapplied change(s) that will be lost.\nConnect to {ip} "
                     "anyway?", count=1, ip="127.0.0.85") in asked[0]
              and "127.0.0.85" in asked[0], str(asked))
        ui.script = [("menu", "p"), ("menu", "2"), ("yesno", 0), ("yesno", 0)]
        ok = app._do(app.tools_menu)
        check('"Yes": it ends this session to go to that device', not ok and not ui.script
              and app.next_target == "127.0.0.85", str(app.next_target))

        print("=== and main logs into the other device ===")
        os.environ["AIROS_PASSWORD"] = "ubnt"
        T.DISCOVERY_PORT = 18851
        ui = FakeUI([("menu", "t"), ("menu", "p"), ("menu", "2"), ("yesno", 0),
                     ("menu", None)])
        real_ui = T.UI
        T.UI = lambda: ui
        try:
            rc = T.main(["http://127.0.0.84:18850", "-u", "ubnt", "--target", "127.0.0.1",
                         "--lang", "es"])
        finally:
            T.UI = real_ui
        errors = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Connection error")]
        recent = [items for title, items, _ in ui.menus if title == tr("Connect")]
        check('--target works in "Tools" too; choosing another device, it tries to log into that '
              "IP (nobody is on its port 80 here) and goes back to the connection screen: to the "
              "recent devices, with the previous one", rc == 0 and not ui.script and errors
              and "127.0.0.85" in errors[0] and len(recent) == 1
              and ("1", "127.0.0.84:18850  (ubnt)") in recent[0], str(ui.log[-4:]))
    finally:
        T.reachable = real_reachable
        r.close()
        srv.shutdown()


def active():
    print("=== the active search: the web of each address, and ARP ===")
    check("the ports as they are typed", T.parse_ports(" 443, 80,8443 80") == [443, 80, 8443])
    bad = []
    for text in ("0", "70000", "http", "", "80;443"):
        try:
            T.parse_ports(text)
        except ValueError:
            bad.append(text)
    check("  and what are not ports", bad == ["0", "70000", "http", "", "80;443"], str(bad))
    eth1 = (3, "eth1", "192.168.1.100", "255.255.255.0")
    big = (4, "eth2", "10.20.0.5", "255.255.0.0")
    nets, notes = T.scan_ranges(T.BROADCAST, [eth1, big])
    check("with no target, your networks; one beyond a /22 left out, saying what part to search",
          [str(n) for n in nets] == ["192.168.1.0/24"] and notes == [tr(
              "Your network {range} ({count} addresses) is left out: the active search asks a "
              "/22 at most. Search a part of it, for example {part}.", range="10.20.0.0/16",
              count=65536, part="10.20.0.0/24")], str((nets, notes)))
    check("the broadcast of one of them, that network; a range; an IP",
          [[str(n) for n in T.scan_ranges(t, [eth1, big])[0]]
           for t in ("192.168.1.255", "10.3.0.0/24", "10.3.0.7")]
          == [["192.168.1.0/24"], ["10.3.0.0/24"], ["10.3.0.7/32"]])
    said = []
    for t in ("10.0.0.0/21", "10.20.255.255", "10.3.0.0/33"):
        try:
            T.scan_ranges(t, [eth1, big])
            said.append(None)
        except ValueError as e:
            said.append(str(e))
    check("what it cannot ask: beyond a /22 (one of your networks too: it goes address by "
          "address), or what is not a range", said == [
              too_big_active("10.0.0.0/21", 2048), too_big_active("10.20.0.0/16", 65536),
              tr('"{target}" is not a valid range (for example 10.3.0.0/24).',
                 target="10.3.0.0/33")], str(said))
    check("the addresses: the hosts of each range, without this computer's", T.scan_addresses(
        [ipaddress.IPv4Network("192.168.1.96/30"), ipaddress.IPv4Network("10.3.0.7/32")],
        [eth1, (5, "eth3", "192.168.1.97", "255.255.255.0")]) == ["192.168.1.98", "10.3.0.7"])
    os.makedirs(XDG, exist_ok=True)
    table = os.path.join(XDG, "arp")
    with open(table, "w", encoding="ascii") as f:
        f.write("IP address       HW type     Flags       HW address            Mask     Device\n"
                "192.168.1.20     0x1         0x2         fc:ec:da:00:00:01     *        eth1\n"
                "192.168.1.30     0x1         0x0         00:00:00:00:00:00     *        eth1\n"
                "192.168.1.40     0x1         0x6         68:d7:9a:00:00:02     *        eth1\n")
    check("the kernel's ARP table: what answered (complete entries), with its MAC",
          T.read_arp(table) == {"192.168.1.20": ("FC:EC:DA:00:00:01", "eth1"),
                                "192.168.1.40": ("68:D7:9A:00:00:02", "eth1")}
          and T.read_arp(os.path.join(XDG, "no-such-table")) == {})

    P, PS = 18864, 18865                       # (their web, on 127.0.0.96/28; and an HTTPS)
    CLOSED = 18867                              # (nothing there: refused at once)
    tls = os.path.join(XDG, "tls")
    os.makedirs(tls, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                    f"{tls}/k.pem", "-out", f"{tls}/c.pem", "-days", "1", "-subj", "/CN=airos"],
                   check=True, capture_output=True)
    sims = [S.make_server(8, P, restart=0.5, mgmt_ip="127.0.0.97"),
            S.make_server(6, P, restart=0.5, mgmt_ip="127.0.0.98"),
            S.make_server(8, PS, restart=0.5, mgmt_ip="127.0.0.101", first_setup=True),
            S.make_server(8, P, restart=0.5, mgmt_ip="127.0.0.102", factory_password=True)]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{tls}/c.pem", f"{tls}/k.pem")
    sims[2].socket = ctx.wrap_socket(sims[2].socket, server_side=True)
    redirect = redirect_to(PS)
    webs = [http.server.ThreadingHTTPServer(("127.0.0.99", P), OtherWeb),
            http.server.ThreadingHTTPServer(("127.0.0.101", P), redirect)]
    for srv in sims + webs:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    raw = socket.socket()                       # (it takes connections and speaks no HTTP)
    raw.bind(("127.0.0.100", P))
    raw.listen(8)

    def hang_up():
        while True:
            try:
                raw.accept()[0].close()
            except OSError:
                return
    threading.Thread(target=hang_up, daemon=True).start()
    model = S.BOARD[8]["board.name"]
    try:
        steps = []
        found, notes = T.active_scan("127.0.0.96/28", [P], interfaces=[],
                                     progress=lambda done, total, again: steps.append(
                                         (done, total, again)))
        by_ip = {d["ip"]: d for d in found}
        check("a range out of your networks: the airOS antennas, by each address's web, "
              "without a user or password", sorted(by_ip) == ["127.0.0.101", "127.0.0.102",
                                                             "127.0.0.97", "127.0.0.98"]
              and not notes
              and steps[0] == (0, 14, False) and steps[-1] == (14, 14, False),
              str((sorted(by_ip), steps[-1:])))
        check("  as the list shows them: airOS 8 with its model (and what it still lacks), airOS "
              "6 with its MAC", [
                  T.discovery_line(by_ip[ip]) for ip in (
                      "127.0.0.97", "127.0.0.98", "127.0.0.101", "127.0.0.102")] == [
                  f"{ip:<15} " + " · ".join(parts) for ip, parts in (
                      ("127.0.0.97", [tr("(no name)"), model, "airOS 8"]),
                      ("127.0.0.98", [T._colon_mac(S.MAC), "airOS 6"]),
                      ("127.0.0.101", [tr("(no name)"), model, "airOS 8",
                                       tr("waiting for its first setup")]),
                      ("127.0.0.102", [tr("(no name)"), model, "airOS 8",
                                       tr("factory password")]))],
              str([T.discovery_line(d) for d in found]))
        check("  not another web (a printer's, 127.0.0.99), nor what speaks no HTTP (127.0.0.100)",
              "127.0.0.99" not in by_ip and "127.0.0.100" not in by_ip)
        first = by_ip["127.0.0.101"]
        check("an HTTP that only sends to HTTPS (as airOS 8's): followed at once (its HTTP "
              "asked once), and connecting goes there",
              first["web"]["url"] == f"https://127.0.0.101:{PS}"
              and redirect.paths == ["/api/info/public"]
              and T._connect_to(first) == f"https://127.0.0.101:{PS}"
              and T.protocols_label(by_ip["127.0.0.97"]) == f"web http:{P}",
              str((first["web"], redirect.paths)))
        check("  which redirects are that: to HTTPS, on this same address (its port, or 443)", [
            T._https_port("10.1.0.3", status, location) for status, location in (
                (302, "https://10.1.0.3/api/info/public"), (301, "https://10.1.0.3:8443/"),
                (302, "https:/x"), (302, "https://10.1.0.4/"), (302, "http://10.1.0.3/"),
                (200, "https://10.1.0.3/"), (302, "/cookiechecker?uri=/"),
                (302, "https://10.1.0.3:99999/"), (302, "http://[fe80::1"),
                (302, "https://[x]/"))] == [443, 8443, 443] + [None] * 7)
        check("  its data: what it waits for, and that its web answers from here",
              tr("It waits for its first setup (country, language and its terms, and the "
                 "administrator): connecting to it does it, as its web does.")
              in T.format_discovered(first) and tr("Its web answers from this computer: you can "
                                                   "connect.") in T.format_discovered(first))
        check("  in JSON, its web", T.discovery_json(by_ip["127.0.0.98"])["web"] == {
            "url": f"http://127.0.0.98:{P}", "airos": "6", "model": "", "first_setup": False,
            "factory_password": False})

        here = [(socket.if_nametoindex("lo"), "lo", "127.0.0.110", "255.255.255.240")]
        arp = {"127.0.0.97": ("02:00:00:00:00:97", "lo"),
               "127.0.0.100": ("02:00:00:00:01:00", "lo")}
        found, notes = T.active_scan(T.BROADCAST, [P], interfaces=here, arp=lambda: arp)
        by_ip = {d["ip"]: d for d in found}
        check("on your network (here 127.0.0.96/28, on loopback) ARP gives the MAC of the "
              "antennas; what has no airOS web is not listed, ARP or not (nor this computer)",
              by_ip["127.0.0.97"]["protocols"] == ["web", "arp"]
              and by_ip["127.0.0.97"]["mac"] == "02:00:00:00:00:97"
              and not {"127.0.0.99", "127.0.0.100", "127.0.0.110"} & set(by_ip) and not notes,
              str(sorted(by_ip)))
        check("  that MAC, in its data", "02:00:00:00:00:97"
              in T.format_discovered(by_ip["127.0.0.97"]))
        far = T.active_scan(T.BROADCAST, [P], interfaces=[(here[0][0], "lo", "127.0.0.1",
                                                           "255.0.0.0")])
        check("  your network beyond a /22: left out (saying so), and nothing asked",
              far[0] == [] and len(far[1]) == 1 and "127.0.0.0/8" in far[1][0], str(far))
        told = T.parse_discovery(reply("020000000097", "127.0.0.97", "Tower-97"), "127.0.0.97")
        merged = T.merge_active([told], found)
        same = [d for d in merged if d["ip"] == "127.0.0.97"]
        check("with discovery's answers: each device once, with all that found it, and its web",
              len(same) == 1 and same[0]["protocols"] == ["ubnt", "web", "arp"]
              and same[0]["hostname"] == "Tower-97" and same[0]["web"]["airos"] == "8"
              and len(merged) == len(found), str(same))
        twins = [T.parse_discovery(reply(mac, "127.0.0.97", name), "127.0.0.97")
                 for mac, name in (("020000000001", "Factory-1"), ("020000000097", "Factory-2"))]
        merged = T.merge_active(twins, [by_ip["127.0.0.97"]])
        check("  two at the same address: its web goes to the one with its MAC (ARP's), not to "
              "the first", [d["hostname"] for d in merged if d.get("web")] == ["Factory-2"],
              str([(d["hostname"], bool(d.get("web"))) for d in merged]))

        ui = FakeUI([("menu", "a"), ("input", "127.0.0.96/28"), ("input", str(P)), ("menu", "1"),
                     ("yesno", 0)])
        url = T.discovery_screen(ui, "127.0.0.9")    # (where nobody answers discovery)
        texts = [text for k, _t, text in ui.log if k == "menu"]
        check("the screen's active search: its devices in the list, with its progress; "
              "connecting to one on another port, its URL (before it, nobody: it was proposed)",
              url == f"http://127.0.0.97:{P}" and len(texts) == 2
              and tr(ACTIVE_HINT, active="a") in texts[0] and texts[1].startswith(tr(
                  "{count} device(s) on {where}. Choose one to see its data; no user or password "
                  "needed.", count=4, where=tr("{where} and, with the active search, {scanned}",
                                               where="127.0.0.9", scanned="127.0.0.96/28")))
              and ui.gauges and ui.gauges[0].texts[-1] == tr(
                  "Asking {total} addresses for their web: {done} done…", done=14, total=14),
              str((url, texts[1:])))
        ui = FakeUI([("menu", "a"), ("input", "127.0.0.96/28"), ("input", "80, http"),
                     ("menu", "a"), ("input", "127.1.0.0/16"), ("input", "80"), ("menu", None)])
        T.discovery_screen(ui, "127.0.0.9")
        check("  bad ports, or a range too big: it says so, and asks nothing",
              [t for k, _t, t in ui.log if k == "msgbox"] == [
                  tr('"{ports}" are not ports: numbers from 1 to 65535, separated by commas.',
                     ports="80, http"), too_big_active("127.1.0.0/16", 65536)] and not ui.gauges)
        ui = FakeUI([("menu", "a"), ("input", "127.0.0.112/28"), ("input", str(CLOSED)),
                     ("menu", None)])
        T.discovery_screen(ui, "127.0.0.9")
        texts = [text for k, _t, text in ui.log if k == "menu"]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = T.main(["--active-scan", "--target", "127.0.0.112/28", "--ports", str(CLOSED),
                         "--timeout", "0.3"])
        check("  nothing either with the active search: it is not proposed again (screen and "
              "--active-scan)", len(texts) == 2 and tr(
                  "No airOS antenna answered on {where}.", where=tr(
                      "{where} and, with the active search, {scanned}", where="127.0.0.9",
                      scanned="127.0.0.112/28")) in texts[1]
              and tr(ACTIVE_HINT, active="a") not in texts[1] and rc == 1
              and tr("No airOS antenna answered on {where}.", where="127.0.0.112/28")
              in err.getvalue() and ACTIVE_HINT.partition("(")[0] not in err.getvalue(),
              str((texts[1:], err.getvalue())))

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = T.main(["--active-scan", "--target", "127.0.0.96/28", "--ports", str(P),
                         "--timeout", "0.5", "--json"])
        data = {d["ip"]: d for d in json.loads(out.getvalue() or "[]")}
        check("--active-scan (it implies --discover): the list, its web in the JSON",
              rc == 0 and {ip: d["web"]["airos"] for ip, d in data.items()} == {
                  "127.0.0.97": "8", "127.0.0.98": "6", "127.0.0.101": "8",
                  "127.0.0.102": "8"}, out.getvalue()[:300] + err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = T.main(["--active-scan", "--target", "127.0.0.96/28", "--ports", str(P),
                         "--timeout", "0.5"])
        check("  in the terminal: what it asks first, then the table", rc == 0 and tr(
            "Active search: asking {count} addresses for their web (ports {ports}); Ctrl+C to "
            "stop earlier…", count=14, ports=str(P)) in err.getvalue()
              and "127.0.0.97" in out.getvalue() and "127.0.0.99" not in out.getvalue()
              and f"web http:{P}" in out.getvalue(),
              out.getvalue() + err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = T.main(["--active-scan", "--target", "127.0.0.0/16", "--timeout", "0.5"])
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                T.main(["--active-scan", "--ports", "0"])
            code = None
        except SystemExit as e:
            code = e.code
        check("  a range too big: exit code 2 and why; --ports with what is not one, argparse's "
              "error", rc == 2 and too_big_active("127.0.0.0/16", 65536) in err.getvalue()
              and code == 2, err.getvalue())

        real_probe, lost = T._probe_port, []

        def lost_first(ip, port, ctx, pace=None):  # (its first answer lost on the way)
            if ip == "127.0.0.97" and not lost:
                lost.append(ip)
                return "", None
            return real_probe(ip, port, ctx, pace)
        T._probe_port = lost_first
        try:
            steps = []
            found, _ = T.active_scan("127.0.0.96/28", [P], interfaces=[],
                                     progress=lambda done, total, again: steps.append(
                                         (done, total, again)))
        finally:
            T._probe_port = real_probe
        check("an address that did not answer is asked again at the end, and found then",
              "127.0.0.97" in {d["ip"] for d in found} and lost == ["127.0.0.97"]
              and (0, 1, True) in steps and steps[-1] == (1, 1, True), str(steps[-3:]))
        slow = http.server.ThreadingHTTPServer(("127.0.0.104", P), SlowFirst)
        threading.Thread(target=slow.serve_forever, daemon=True).start()
        read = T.READ_SECONDS
        T.READ_SECONDS = 0.5
        try:
            found, _ = T.active_scan("127.0.0.104", [P], interfaces=[])
        finally:
            T.READ_SECONDS = read
            slow.shutdown()
            slow.server_close()
        check("  one that answers too late the first time, too",
              [d["model"] for d in found] == ["LiteBeam 5AC"]
              and SlowFirst.calls == ["/api/info/public"] * 2, str((found, SlowFirst.calls)))

        starts, real_connect = [], socket.create_connection

        def timed(address, *args, **kw):          # (when each new connection starts)
            starts.append(time.monotonic())
            return real_connect(address, *args, **kw)
        socket.create_connection = timed
        try:
            began = time.monotonic()
            T.active_scan("127.0.0.112/28", [CLOSED], interfaces=[], rate=20)
            paced = time.monotonic() - began
            gaps = [b - a for a, b in itertools.pairwise(starts)]
            del starts[:]
            T.active_scan("127.0.0.112/28", [CLOSED], interfaces=[], rate=0)
            burst = [b - a for a, b in itertools.pairwise(starts)]
        finally:
            socket.create_connection = real_connect
        check("new connections one after another, never several at once: rate per second at "
              "most (a router may drop a burst of them)", len(gaps) == 13
              and min(gaps) >= 0.035 and paced >= 0.6 and len(burst) == 13
              and min(burst) < 0.035, str((round(paced, 2), [round(g, 3) for g in gaps])))
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        began = time.monotonic()
        T.active_scan("127.0.0.112/28", [CLOSED], interfaces=[], rate=2, stop=stop)
        took = time.monotonic() - began
        check("  stopped meanwhile, it does not wait for the turns left (at 2 per second, 14 "
              "addresses would take 6.5 s)", took < 1.5, f"{took:.2f} s")
        release, real_probe = threading.Event(), T._probe_port

        def stuck(ip, port, ctx, pace=None):        # (an address whose web takes its time)
            if ip == "127.0.0.110":
                release.wait(20)
                return "", None
            return real_probe(ip, port, ctx, pace)

        def interrupt(done, total, again):           # (Ctrl+C on its screen)
            if done:
                raise KeyboardInterrupt
        T._probe_port = stuck
        try:
            stop = threading.Event()
            threading.Timer(1.5, stop.set).start()
            began = time.monotonic()
            found, _ = T.active_scan("127.0.0.96/28", [P], interfaces=[], stop=stop)
            took = time.monotonic() - began
            began = time.monotonic()
            try:
                T.active_scan("127.0.0.96/28", [P], interfaces=[], progress=interrupt)
                interrupted = None
            except KeyboardInterrupt:
                interrupted = time.monotonic() - began
        finally:
            T._probe_port = real_probe
            release.set()
        check("stopped (Ctrl+C in --active-scan) while an address is still being asked: back at "
              "once, with what it found", took < 3 and "127.0.0.97" in {d["ip"] for d in found},
              f"{took:.2f} s")
        check("  interrupted where it is asked (Ctrl+C on its screen): at once too",
              interrupted is not None and interrupted < 2, str(interrupted))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = T.main(["--active-scan", "--target", "127.0.0.97", "--ports", str(P),
                         "--timeout", "0.5", "--debug"])
        log = open(T.DEBUG_FILE, encoding="utf-8").read()
        check("--debug with --active-scan: what each web answered (and how long it took)",
              rc == 0 and f"GET http://127.0.0.97:{P}/api/info/public → 200 (" in log
              and S.BOARD[8]["board.name"] in log, log[-600:])
    finally:
        for srv in sims:
            srv.cluster.shutdown()
        for srv in webs:
            srv.shutdown()
            srv.server_close()
        raw.close()


if __name__ == "__main__":
    unit()
    search()
    screens()
    active()
    tools()
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
