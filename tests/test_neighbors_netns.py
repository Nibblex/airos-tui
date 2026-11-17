"""Real CDP and LLDP in an isolated network namespace (unshare -rn, no sudo): the simulator
sends its announcements through one veth interface and airos_tui --discover listens on the
other, together with Ubiquiti discovery on 127.0.0.1. And the active search through real ARP:
another veth pair, with its other end and a simulator in a namespace of their own. Nothing goes
out to your networks: in these namespaces there are only lo and the veth pairs."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

if os.environ.get("AIROS_NETNS") != "1":          # start again inside the isolated namespace
    os.environ["AIROS_NETNS"] = "1"
    os.execvp("unshare", ["unshare", "-rn", sys.executable, os.path.abspath(__file__)])

from _common import check, fails, ROOT, SIM, suite, TUI  # noqa: E402
T, S, XDG = suite("netns")
tr = T.tr

# What --discover says while it listens for CDP and LLDP (a msgid: tr(LISTENING, ...))
LISTENING = ("Listening for CDP and LLDP announcements on {ifaces} for {seconds} s (they are "
             "sent about once a minute; Ctrl+C to stop earlier)…")

for cmd in (["ip", "link", "set", "lo", "up"], ["ip", "link", "add", "v0", "type", "veth", "peer",
                                                "name", "v1"],
            ["ip", "link", "set", "v0", "up"], ["ip", "link", "set", "v1", "up"]):
    subprocess.run(cmd, check=True)
links = subprocess.run(["ip", "-br", "link"], capture_output=True, text=True).stdout
check("isolated namespace: only lo and the veth pair",
      sorted(line.split()[0].split("@")[0] for line in links.splitlines()) == ["lo", "v0", "v1"],
      links)

srv = S.make_server(8, 18501, restart=0.3, mgmt_ip="127.0.0.60", discovery_port=10001,
                    announce="v1", announce_every=0.5)
threading.Thread(target=srv.serve_forever, daemon=True).start()
dev = srv.dev
with dev.lock:
    dev.running["discovery.cdp.status"] = "enabled"       # airOS 8 comes with CDP off

SWITCH = bytes.fromhex("00163E112233")                    # another device on the link: CDP only
sw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
sw.bind(("v1", 0))


def switch_loop():
    while True:
        sw.send(S.cdp_frame(SWITCH, "switch-1", ["10.0.0.2"], "Gi0/1",
                            "Cisco IOS Software", "cisco WS-C2960"))
        time.sleep(0.5)


threading.Thread(target=switch_loop, daemon=True).start()


def search(*extra):
    return subprocess.run([sys.executable, TUI, "--discover", "--target", "127.0.0.1",
                           "--timeout", "1", *extra], capture_output=True, text=True, timeout=60)


def devices(r):
    try:
        return {d["ip"]: d for d in json.loads(r.stdout)}
    except ValueError:
        return {}


print("=== the three protocols ===")
r = search("--listen", "3", "--json")
found = devices(r)
ap = found.get("127.0.0.60", {})
check("the antenna, by all three", ap.get("protocols") == ["ubnt", "cdp", "lldp"],
      r.stdout + r.stderr)
check("with the discovery data", ap.get("airos") == "8.7.19"
      and ap.get("essid") == "North-Tower" and ap.get("hostname") == "AP-North-Tower", str(ap))
check("the switch (only CDP, not airOS) is not listed", "10.0.0.2" not in found,
      str(list(found)))
check("one device (what is sent through v1 is not counted twice)", len(found) == 1,
      str(list(found)))
r = search("--listen", "3")
first = r.stdout.splitlines()[0] if r.stdout else ""
listening = [line for line in r.stderr.splitlines()
             if line.startswith(tr(LISTENING).partition("{")[0])]
check('table with "Found by"', first.rstrip().endswith(tr("Found by"))
      and "Ubiquiti, CDP, LLDP" in r.stdout, r.stdout)
# (the part of that message after {ifaces}: " for 3 s (they are sent about once a minute...)…")
check("says where and for how long it listens", listening and "v0" in listening[0]
      and "v1" in listening[0]
      and tr(LISTENING).partition("{ifaces}")[2].format(seconds=3) in listening[0], r.stderr)

print("=== without Ubiquiti discovery, CDP and LLDP find it all the same ===")
with dev.lock:
    dev.running["discovery.status"] = "disabled"
r = search("--listen", "3", "--json")
ap = devices(r).get("127.0.0.60", {})
check("only by CDP and LLDP, with its IP and its version", ap.get("protocols") == ["cdp", "lldp"]
      and ap.get("airos") == "8.7.19" and ap.get("hostname") == "AP-North-Tower", str(ap))

print("=== with CDP and LLDP off, only Ubiquiti ===")
with dev.lock:
    dev.running.update({"discovery.status": "enabled", "discovery.cdp.status": "disabled",
                        "discovery.lldp.status": "disabled"})
r = search("--listen", "2", "--json")
ap = devices(r).get("127.0.0.60", {})
check("only Ubiquiti", ap.get("protocols") == ["ubnt"], str(ap))

print("=== --listen 0: it searches without listening ===")
r = search("--listen", "0")
check("it does not listen", tr(LISTENING).partition("{")[0] not in r.stderr
      and r.returncode == 0 and tr("Found by") in r.stdout, r.stderr)

print("=== the simulator from the command line (--announce) ===")
with dev.lock:          # both simulators have the same MAC: only the second one must be heard
    dev.running["discovery.status"] = "disabled"
p = subprocess.Popen([sys.executable, SIM, "--port", "18502", "--mgmt-ip", "127.0.0.61",
                      "--announce", "v1", "--announce-every", "0.5", "--lang", "es"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(1.5)
try:
    r = search("--listen", "2", "--json")
    other = devices(r).get("127.0.0.61", {})
    check("its LLDP announcements arrive (CDP is off by default)",
          other.get("protocols") == ["lldp"] and other.get("airos") == "8.7.19",
          r.stdout + r.stderr)
finally:
    p.terminate()
    p.wait(timeout=10)

print("=== the active search through a link of its own: its web, and its MAC by real ARP ===")
# Another veth pair: a1 goes to a namespace of its own, with 10.77.0.5/29 and an airOS 8
# simulator there (started as a library: its command only takes 127.0.0.0/8 addresses, so as
# not to be on a real network); from this one (a0, 10.77.0.1/29) it is reached as on a real
# network, through ARP, and nothing else answers on that link.
subprocess.run(["ip", "link", "add", "a0", "type", "veth", "peer", "name", "a1"], check=True)
a1_mac = json.loads(subprocess.run(["ip", "-j", "link", "show", "a1"], capture_output=True,
                                  text=True, check=True).stdout)[0]["address"].upper()
peer = subprocess.Popen(["unshare", "-n", "sh", "-c",
                         "while ! ip link show a1 >/dev/null 2>&1; do sleep 0.1; done; "
                         "ip link set lo up && ip addr add 10.77.0.5/29 dev a1 && "
                         f"ip link set a1 up && exec {sys.executable} -c '"
                         f"import sys; sys.path.insert(0, {json.dumps(ROOT)}); "
                         "import airos_simulator as S; "
                         "S.make_server(8, 18503, mgmt_ip=\"10.77.0.5\").serve_forever()'"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    end_wait = time.time() + 10      # (moved before it has its namespace, a1 would stay here)
    while (os.readlink(f"/proc/{peer.pid}/ns/net") == os.readlink("/proc/self/ns/net")
           and time.time() < end_wait):
        time.sleep(0.05)
    subprocess.run(["ip", "link", "set", "a1", "netns", str(peer.pid)], check=True)
    for cmd in (["ip", "addr", "add", "10.77.0.1/29", "dev", "a0"], ["ip", "link", "set", "a0",
                                                                      "up"]):
        subprocess.run(cmd, check=True)
    end_wait = time.time() + 15
    while time.time() < end_wait:
        try:
            socket.create_connection(("10.77.0.5", 18503), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    r = subprocess.run([sys.executable, TUI, "--active-scan", "--target", "10.77.0.0/29",
                        "--ports", "18503", "--timeout", "1", "--json"],
                       capture_output=True, text=True, timeout=60)
    seen = devices(r)
    ant = seen.get("10.77.0.5", {})
    check("the antenna through that link, by its web (airOS 8), with the MAC ARP gave (a1's), "
          "on a0; nothing else on the link", list(seen) == ["10.77.0.5"]
          and (ant.get("web") or {}).get("airos") == "8"
          and {"web", "arp"} <= set(ant.get("protocols") or ()) and ant.get("mac") == a1_mac
          and ant.get("interface") == "a0", r.stdout + r.stderr)
finally:
    peer.terminate()
    peer.wait(timeout=10)

srv.cluster.shutdown()
shutil.rmtree(XDG, ignore_errors=True)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
