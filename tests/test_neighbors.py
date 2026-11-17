"""Tests of CDP and LLDP in --discover: parsing the frames, merging them with Ubiquiti discovery,
and the output (table and JSON). No privileges or traffic: the frames are built here with the
simulator, and the network is replaced by fixed answers."""
import contextlib
import io
import json
import os
import random
import shutil
import sys

from _common import check, fails, suite  # noqa: E402
T, S, XDG = suite("neighbors")
tr = T.tr


MAC = bytes.fromhex("68D79A98FBFF")
SWITCH = bytes.fromhex("00163E112233")
FW = S.FIRMWARE[8]
# What --discover says while it listens for CDP and LLDP (a msgid: tr(LISTENING, ...))
LISTENING = ("Listening for CDP and LLDP announcements on {ifaces} for {seconds} s (they are "
             "sent about once a minute; Ctrl+C to stop earlier)…")

print("=== LLDP frames ===")
lldp = S.lldp_frame(MAC, MAC, "eth0", "AP-North-Tower", f"{S.MODEL[8]} {FW}", ["192.168.1.20"])
d = T.parse_neighbor_frame(lldp)
check("protocol, MAC and name", d and d["protocols"] == ["lldp"]
      and d["mac"] == "68:D7:9A:98:FB:FF" and d["hostname"] == "AP-North-Tower", str(d))
check("management IP and port", d["ip"] == "192.168.1.20" and d["addrs"] == ["192.168.1.20"]
      and d["port"] == "eth0" and d["pairs"] == [("68:D7:9A:98:FB:FF", "192.168.1.20")])
check("airOS version taken from the description", d["version"] == "8.7.19"
      and d["fw"].endswith(FW))
vlan = lldp[:12] + b"\x81\x00\x00\x0a" + lldp[12:]
check("with an 802.1Q tag",
      (T.parse_neighbor_frame(vlan) or {}).get("hostname") == "AP-North-Tower")
padded = lldp + bytes(30)
check("with padding at the end", (T.parse_neighbor_frame(padded) or {}).get("ip") == "192.168.1.20")
no_ttl = lldp[:14] + lldp[14:14 + 9] + lldp[14 + 9:14 + 9 + 7]   # chassis and port, no TTL
check("without the mandatory TLVs: nothing", T.parse_neighbor_frame(no_ttl + b"\x00\x00") is None)

print("=== CDP frames ===")
cdp = S.cdp_frame(SWITCH, "switch-1", ["10.0.0.2"], "GigabitEthernet0/1",
                  "Cisco IOS Software, C2960 Version 15.0(2)SE", "cisco WS-C2960-24TT-L")
d = T.parse_neighbor_frame(cdp)
check("protocol, name and model", d and d["protocols"] == ["cdp"] and d["hostname"] == "switch-1"
      and d["model"] == "cisco WS-C2960-24TT-L", str(d))
check("IP, port and software", d["ip"] == "10.0.0.2" and d["port"] == "GigabitEthernet0/1"
      and d["fw"].startswith("Cisco IOS") and d["version"] == "")
check("the frame's checksum is valid", S._checksum(cdp[22:]) == 0)
check("another destination MAC: nothing", T.parse_neighbor_frame(bytes(6) + cdp[6:]) is None)
other_llc = cdp[:14] + b"\x42\x42\x03" + cdp[17:]
check("another 802.3 frame (STP): nothing", T.parse_neighbor_frame(other_llc) is None)
check("a short frame: nothing", T.parse_neighbor_frame(b"\x01" * 10) is None)
check("any IPv4 frame: nothing", T.parse_neighbor_frame(
    bytes(12) + b"\x08\x00" + bytes(40)) is None)

rng, broken = random.Random(7), 0
for frame in (lldp, cdp) * 400:
    f = bytearray(frame[:rng.randrange(len(frame) + 1)])
    for _ in range(rng.randrange(4)):
        if f:
            f[rng.randrange(len(f))] = rng.randrange(256)
    try:
        T.parse_neighbor_frame(bytes(f))
    except Exception as e:                      # noqa: BLE001
        broken += 1
        print("    ", type(e).__name__, e, bytes(f).hex())
check("800 truncated or altered frames without a single exception", broken == 0)

print("=== merging with Ubiquiti discovery ===")
dev = S.Device(8, "u", "p", 1, 60, mgmt_ip="127.0.0.40")
ubnt = T.parse_discovery(dev.discovery_reply(), "127.0.0.40")
heard_lldp = T.parse_neighbor_frame(S.lldp_frame(MAC, MAC, "eth0", "AP-North-Tower", FW,
                                                 ["127.0.0.40"]))
heard_cdp = T.parse_neighbor_frame(S.cdp_frame(bytes.fromhex("68D79A98FBFE"), "AP-North-Tower",
                                               ["127.0.0.40"], "eth0", FW, "LBE-5AC-Gen2"))
switch = T.parse_neighbor_frame(cdp)
macs_before = set(ubnt["macs"])
merged = T.merge_neighbors([ubnt], [heard_lldp, heard_cdp, switch])
check("the same device only once", len(merged) == 2, str([m["hostname"] for m in merged]))
check("by MAC (LLDP) and by IP (CDP with another MAC)",
      merged[1]["protocols"] == ["ubnt", "cdp", "lldp"]
      and merged[1]["ip"] == "127.0.0.40", str(merged[1]["protocols"]))
check("keeps the discovery data", merged[1]["essid"] == "North-Tower"
      and merged[1]["uptime"] is not None and merged[1]["model"] == S.MODEL[8])
check("another device, only by CDP", merged[0]["hostname"] == "switch-1"
      and merged[0]["protocols"] == ["cdp"])
check("does not change what it is given", ubnt["macs"] == macs_before and "protocols" not in ubnt)
lone = T.parse_neighbor_frame(S.lldp_frame(bytes.fromhex("AABBCCDDEEFF"),
                                           bytes.fromhex("AABBCCDDEEFF"), "1", "no-ip", "x", []))
both = T.merge_neighbors([], [switch, T.parse_neighbor_frame(S.lldp_frame(
    SWITCH, SWITCH, "Gi0/1", "switch-1", "Cisco IOS", ["10.0.0.2"])), lone])
check("CDP and LLDP of the same device together", [m["protocols"] for m in both]
      == [["cdp", "lldp"], ["lldp"]], str([m["protocols"] for m in both]))
check("without an IP, at the end", both[-1]["hostname"] == "no-ip" and both[-1]["ip"] == "")

print("=== --discover with announcements (network replaced) ===")
real = {name: getattr(T, name) for name in ("discover", "neighbor_sockets", "listen_neighbors")}
calls = []


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = T.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def fake(found, heard):
    T.discover = lambda timeout, target=T.BROADCAST, **kw: [dict(d) for d in found]
    T.neighbor_sockets = lambda: (calls.append("sockets") or ["socket"], ["eth9"])

    def listen(seconds, socks, stop=None):
        calls.append(seconds)
        return [dict(h) for h in heard]
    T.listen_neighbors = listen


try:
    fake([ubnt], [heard_lldp, heard_cdp, switch])
    rc, out, err = run(["--discover", "--listen", "2"])
    lines = out.splitlines()
    check('table with the "Found by" column', rc == 0 and lines[0].split()[-2:]
          == tr("Found by").split(), out)
    check("the antenna with its protocols; the switch (only CDP, not airOS) is not listed",
          len(lines) == 2 and lines[1].rstrip().endswith("Ubiquiti, CDP, LLDP")
          and "switch-1" not in out, out)
    check("says what it listens on and for how long",
          tr(LISTENING, ifaces="eth9", seconds=2) in err
          and tr("{count} device(s).", count=1) in err, err)
    calls.clear()
    run(["--discover"])
    check("by default it listens 65 s on your networks", calls == ["sockets", 65], str(calls))
    calls.clear()
    run(["--discover", "--target", "127.0.0.1"])
    check("with --target it does not listen", calls == [], str(calls))
    calls.clear()
    run(["--discover", "--target", "127.0.0.1", "--listen", "5"])
    check("unless asked to with --listen", calls == ["sockets", 5], str(calls))
    rc, out, err = run(["--discover", "--listen", "1", "--json"])
    data = json.loads(out)
    check("JSON with protocols and port (its own, as LLDP says it)", rc == 0
          and [x["protocols"] for x in data] == [["ubnt", "cdp", "lldp"]]
          and data[0]["port"] == "eth0" and data[0]["airos"] == "8.7.19", out[:300])
    check("no progress messages with JSON", tr(LISTENING).partition("{")[0] not in err, err)
    fake([], [])
    rc, out, err = run(["--discover", "--listen", "1"])
    check("nothing: code 1, and it says so", rc == 1
          and tr("No airOS antenna answered on {where}.").partition("{")[0] in err
          and tr("No CDP or LLDP announcement was heard either.") in err, err)
    fake([ubnt], [heard_lldp])
    T.set_language("en")
    check('in English: "Found by"', T.discovery_table(T.merge_neighbors([ubnt], [heard_lldp]))
          .splitlines()[0].rstrip().endswith("Found by"))
    T.set_language("es")
finally:
    for name, func in real.items():
        setattr(T, name, func)

print("=== without permissions (the real system: it only tries to listen) ===")
if os.geteuid() != 0:
    T.discover = lambda timeout, target=T.BROADCAST, **kw: [dict(ubnt)]
    try:
        rc, out, err = run(["--discover", "--listen", "1"])
    finally:
        T.discover = real["discover"]
    check("says root is needed and goes on with Ubiquiti", rc == 0
          and tr("CDP and LLDP were not listened for: that needs root (sudo) or the CAP_NET_RAW "
                 "capability. Only Ubiquiti discovery was used.") in err
          and tr(LISTENING).partition("{")[0] not in err
          and out.splitlines()[1].rstrip().endswith("Ubiquiti"), err + out)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            S.main(["--announce", "lo", "--port", "18599", "--lang", "es"])
            code = 0
        except SystemExit as e:
            code = e.code
    check("the simulator explains it too", code == 2
          and tr("--announce needs root or CAP_NET_RAW ({error})").partition("{")[0]
          in err.getvalue(), err.getvalue())
else:
    print("  (running as root: the lack of permissions is not tested)")

shutil.rmtree(XDG, ignore_errors=True)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
