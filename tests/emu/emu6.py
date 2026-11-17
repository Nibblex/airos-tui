"""airOS 6 emulated for the tests: the web of a real firmware (XW 6.3.26, from Ubiquiti), run
with QEMU's user mode in namespaces of its own (no root; no network but its own loopback): its
lighttpd and its CGIs, on a fake board (a NanoStation M5); what needs the hardware is stood in
for (see STUBS). Its radio, its JavaScript and its services are not there.

    python3 tests/emu/emu6.py fetch   the firmware, once (kept in ~/.cache/airos-tui/emu6)
    python3 tests/emu/emu6.py shell   the emulated device and a shell beside it, to try the
                              program: python3 -m airos_tui https://127.0.0.1 -u ubnt
                              (PASSWORD below)

It needs qemu-user and qemu-user-binfmt (MIPS programs through binfmt_misc, with its F flag),
unsquashfs (squashfs-tools) and unprivileged user namespaces."""
import hashlib
import http.client
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib

FIRMWARE = {
    "name": "XW.v6.3.26.bin",
    "url": "https://fw-download.ubnt.com/data/XW/"
           "3ade-airmax-6.3.26-4149bba9-847c-437a-96b6-33f08f2b10ff.bin",
    "sha256": "755f67c3cf8a5815ec8b2e9d7e9c5b6484c2f1c052f08a61f903c123816a3af7",
}
CACHE = os.environ.get("AIROS_EMU6_CACHE") or os.path.join(
    os.path.expanduser("~"), ".cache", "airos-tui", "emu6")
HOST = "127.0.0.1"
PASSWORD = "airos6emu"          # the one of its first login (6.3 does not keep the factory one)
MAC = "0418D6123456"
CAPS = 8192                     # radio_cap_fast_clock
BOARD = {                       # a NanoStation M5 (XW), as its ubnthal module would describe it
    "id": "0xe855", "cpu": "0x0019374c", "name": "NanoStation M5", "short": "N5N",
    "radio": "Atheros AR9342 (emulated)", "radio_short": "ar9342", "ccode": 840,
    "txmax": 27, "txmin": 1, "ant_name": "Built in - 16 dBi", "ant_gain": 16, "chains": 2,
    "chanbw": "5,10,20,30,40", "reboot": 40, "upgrade": 150,
}


# -- what is needed ---------------------------------------------------------------------

def firmware_path():
    return os.path.join(CACHE, FIRMWARE["name"])


def missing():
    """What this machine lacks to run it (empty: nothing)."""
    out = []
    binfmt = "/proc/sys/fs/binfmt_misc/qemu-mips"
    try:
        info = open(binfmt).read()
    except OSError:
        info = ""
    if "enabled" not in info or "F" not in info.split("flags:")[-1].split("\n")[0]:
        out.append("qemu-user and qemu-user-binfmt (MIPS programs with binfmt_misc)")
    if not shutil.which("unsquashfs"):
        out.append("unsquashfs (squashfs-tools)")
    if not shutil.which("unshare") or subprocess.run(
            ["unshare", "-rn", "true"], capture_output=True).returncode != 0:
        out.append("user namespaces without privileges (unshare -rn)")
    if not os.path.exists(firmware_path()):
        out.append(f"the firmware (python3 {os.path.abspath(__file__)} fetch, once)")
    return out


def fetch():
    """Download the firmware from Ubiquiti, check its sha256 and keep it in CACHE."""
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    req = urllib.request.Request(FIRMWARE["url"], headers={"User-Agent": "airos-tui-tests"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FIRMWARE["sha256"]:
        raise SystemExit(f"the firmware's sha256 is not the expected one: {digest}")
    tmp = firmware_path() + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, firmware_path())
    print(f"{FIRMWARE['name']}: {len(data)} bytes, sha256 ok, in {CACHE}")


def pristine():
    """Its root filesystem as it comes (extracted once into CACHE): only read."""
    root = os.path.join(CACHE, "rootfs")
    if os.path.isdir(root):
        return root
    data = open(firmware_path(), "rb").read()
    assert data[:4] == b"UBNT", "not a Ubiquiti firmware"
    off, rootfs = 4 + 256 + 8, None
    while data[off:off + 4] == b"PART":          # (its blocks: u-boot, kernel, rootfs…)
        name = data[off + 4:off + 20].split(b"\0")[0]
        size = struct.unpack(">I", data[off + 48:off + 52])[0]
        crc = struct.unpack(">I", data[off + 56 + size:off + 60 + size])[0]
        assert zlib.crc32(data[off:off + 56 + size]) & 0xffffffff == crc, name
        if name == b"rootfs":
            rootfs = data[off + 56:off + 56 + size]
        off += 56 + size + 8
    assert rootfs and rootfs[:4] == b"hsqs", "no squashfs rootfs"
    part = os.path.join(CACHE, "rootfs.squashfs")
    with open(part, "wb") as f:
        f.write(rootfs)
    tmp = root + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run(["unsquashfs", "-no-xattrs", "-q", "-d", tmp, part], check=True,
                   stdout=subprocess.DEVNULL)
    os.unlink(part)
    os.replace(tmp, root)
    return root


# -- its root, as it is after booting ------------------------------------------------------

STUBS = {
    # ubntconf needs the board in /proc/ubnthal (without it, it crashes): of what it writes,
    # /etc/passwd, which the web's login checks, from the users of the configuration
    "bin/ubntconf": r"""#!/bin/sh
# Emulation stand-in for ubntconf (the real one needs the board in /proc/ubnthal): of what it
# writes, only /etc/passwd, from the users of /tmp/system.cfg, as it does (name:hash:uid:gid:
# comment:homedir:shell). Its options (-p FILE, -i, -l, -f) are accepted and ignored.
CFG=/tmp/system.cfg
OUT=/etc/passwd.new
get() { sed -n "s/^users\.$i\.$1=//p" $CFG | tail -n 1; }
: > $OUT
i=1
while [ $i -le 9 ]; do
    name=$(get name)
    if [ -n "$name" ] && [ "$(get status)" != "disabled" ]; then
        uid=$(get uid); gid=$(get gid); comment=$(get comment)
        home=$(get homedir); shell=$(get shell)
        line="$name:$(get password):${uid:-0}:${gid:-0}:${comment:-Administrator}"
        echo "$line:${home:-/etc/persistent}:${shell:-/bin/sh}" >> $OUT
    fi
    i=$((i + 1))
done
[ -s $OUT ] || echo "ubnt:VvpvCwhccFv6Q:0:0:Administrator:/etc/persistent:/bin/sh" > $OUT
mv $OUT /etc/passwd
exit 0
""",
    "bin/cfgmtd": r"""#!/bin/sh
# Emulation stand-in for cfgmtd: the configuration "in flash" is /etc/persistent/flash.cfg.
#   -w [-f FILE]: keep FILE (by default /tmp/system.cfg) · -r [-f FILE]: put it in FILE
FLASH=/etc/persistent/flash.cfg
mode=
file=/tmp/system.cfg
while [ $# -gt 0 ]; do
    case "$1" in
        -w) mode=w ;;
        -r) mode=r ;;
        -f) shift; file=$1 ;;
        -p|-t|-e) shift ;;
    esac
    shift
done
case $mode in
    w) cp "$file" $FLASH ;;
    r) [ -s $FLASH ] && cp $FLASH "$file" ;;
    *) exit 1 ;;
esac
""",
    "bin/iwpriv": r"""#!/bin/sh
# Emulation stand-in for iwpriv: there is no radio. What the web and its scripts ask: the
# channel width and the country (the configuration's, or the board's).
case "$2" in
    get_chanbw) echo "$1     get_chanbw:20" ;;
    getCountryID)
        cc=$(sed -n 's/^radio\.1\.countrycode=//p' /tmp/system.cfg | tail -n 1)
        echo "$1     getCountryID:${cc:-840}" ;;
esac
exit 0
""",
    "usr/etc/rc.d/rc.softrestart": r"""#!/bin/sh
# Emulation stand-in for rc.softrestart: what it leaves in the configuration files and in the
# test-mode lock, as the real one; there are no services to restart here. While a test runs,
# it also stands in for what reverts it when its time (the number in the lock) is over.
CFG_SYSTEM=/tmp/system.cfg
CFG_RUNNING=/tmp/running.cfg
TEST_LOCK_FILE=/var/run/testmode
REVERT=/var/run/testmode.revert

restart() {
    /sbin/ubntconf
    [ -e $TEST_LOCK_FILE ] && chmod 644 $TEST_LOCK_FILE
}

case "$1" in
    save)
        sort $CFG_RUNNING | tr -d "\r" > /tmp/.r.$$
        sort $CFG_SYSTEM | tr -d "\r" > /tmp/.s.$$
        if diff /tmp/.r.$$ /tmp/.s.$$ > /dev/null; then
            rm -f /tmp/.r.$$ /tmp/.s.$$
            exit 0
        fi
        rm -f /tmp/.r.$$ /tmp/.s.$$
        restart
        sort $CFG_SYSTEM | tr -d "\r" > $CFG_RUNNING
        /sbin/cfgmtd -w -f $CFG_RUNNING -p /etc/
        ;;
    test)
        restart
        cp $CFG_SYSTEM $CFG_RUNNING
        [ -e $REVERT ] && kill $(cat $REVERT) 2> /dev/null
        secs=$(cat $TEST_LOCK_FILE 2> /dev/null)
        (
            sleep ${secs:-180}
            if [ -e $TEST_LOCK_FILE ]; then
                rm -f $TEST_LOCK_FILE
                /sbin/cfgmtd -r -p /etc/ -f $CFG_SYSTEM
                cp $CFG_SYSTEM $CFG_RUNNING
                /sbin/ubntconf
            fi
            rm -f $REVERT
        ) < /dev/null > /dev/null 2>&1 &
        echo $! > $REVERT
        ;;
    *)
        restart
        ;;
esac
exit 0
""",
}


def prepare(root):
    """A copy of its root in root (whatever was there goes), as it is after booting: the /etc
    its /init builds, its factory configuration in /tmp (and "in flash"), the board, the
    lighttpd.conf its ubntconf writes, and the stand-ins. Its first login is still to come."""
    shutil.rmtree(root, ignore_errors=True)
    subprocess.run(["cp", "-a", pristine(), root], check=True)

    def path(rel):
        return os.path.join(root, rel.lstrip("/"))

    def write(rel, text, mode=0o644):
        p = path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.lexists(p):
            os.unlink(p)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(p, mode)

    def symlink(target, rel):
        if os.path.lexists(path(rel)):
            os.unlink(path(rel))
        os.symlink(target, path(rel))

    # what /init does
    for d in ("var/run", "var/tmp", "var/log", "var/lock", "var/etc/persistent",
              "var/etc/rc.d", "var/etc/init.d", "var/etc/udhcpc", "var/etc/sysinit",
              "var/etc/httpd", "tmp", "proc", "sys", "dev/pts", "dev/shm"):
        os.makedirs(path(d), exist_ok=True)
    for f in ("inittab", "rc.d/rc.sysinit", "rc.d/rc", "rc.d/rc.stop"):
        shutil.copy2(path("usr/etc/" + f), path("etc/" + f))
    shutil.copytree(path("usr/etc/ppp"), path("etc/ppp"), symlinks=True, dirs_exist_ok=True)
    for f in ("group", "login.defs", "profile", "hosts", "host.conf", "fstab",
              "udhcpc/udhcpc", "startup.list", "udhcpc_services"):
        if os.path.lexists(path("usr/etc/" + f)) and not os.path.lexists(path("etc/" + f)):
            shutil.copy2(path("usr/etc/" + f), path("etc/" + f))
    for f in ("services", "protocols", "shells", "mime.types", "ethertypes", "modules.d",
              "motd"):
        if not os.path.lexists(path("etc/" + f)):
            symlink("/usr/etc/" + f, "etc/" + f)
    symlink("/usr/etc/system.cfg", "etc/default.cfg")
    for f in ("server.crt", "server.key"):
        symlink("/usr/etc/" + f, "etc/httpd/" + f)
    with open(path("etc/server.pem"), "w") as out:
        for f in ("server.crt", "server.key"):
            out.write(open(path("usr/etc/" + f)).read())
    for d in ("null", "zero", "random", "urandom", "tty"):   # (where /dev's are bound)
        write("dev/" + d, "")

    # its factory configuration, as /init leaves it (sorted, without \r), and its passwd
    lines = sorted(line.rstrip("\r\n") for line in open(path("usr/etc/system.cfg"))
                   if line.strip())
    text = "\n".join(lines) + "\n"
    for rel in ("tmp/system.cfg", "tmp/running.cfg", "etc/persistent/flash.cfg"):
        write(rel, text)
    cfg = dict(line.split("=", 1) for line in lines if "=" in line)
    write("etc/passwd", f"{cfg['users.1.name']}:{cfg['users.1.password']}:0:0:"
                        "Administrator:/etc/persistent:/bin/sh\n")

    # the board: what its ubnthal module would write
    b = BOARD
    write("etc/board.info", "".join(f"{k}={v}\n" for k, v in (
        ("board.sysid", b["id"]), ("board.cpurevision", b["cpu"]), ("board.name", b["name"]),
        ("board.shortname", b["short"]), ("board.subtype", ""), ("board.netmodes", 7),
        ("board.hwaddr", MAC), ("board.reboot", b["reboot"]),
        ("board.upgrade", b["upgrade"]), ("board.phycount", 1),
        ("board.phy.1.maxmtu", 1528), ("board.radio_count", 1),
        ("radio.1.name", b["radio"]), ("radio.1.shortname", b["radio_short"]),
        ("radio.1.bus", "ahb"), ("radio.1.devdomain", 0), ("radio.1.regdomain_flags", ""),
        ("radio.1.ccode", b["ccode"]), ("radio.1.ccode_fixed", 0),
        ("radio.1.ccode_locked", 0), ("radio.1.cap.station_only", 0),
        ("radio.1.cap.ap_only", 0), ("radio.1.subsystemid", b["id"]),
        ("radio.1.subvendorid", "0x0777"), ("radio.1.txpower.max", b["txmax"]),
        ("radio.1.txpower.min", b["txmin"]), ("radio.1.txpower.offset", 0),
        ("radio.1.ieee_modes", 5), ("radio.1.antennas", 1), ("radio.1.def_antenna", 1),
        ("radio.1.antenna.1.id", 1), ("radio.1.antenna.1.name", b["ant_name"]),
        ("radio.1.antenna.1.gain", b["ant_gain"]), ("radio.1.antenna.1.builtin", 1),
        ("radio.1.antenna.1.chains", b["chains"]), ("radio.1.distance_limit_km", 0),
        ("radio.1.chanbw", f'"{b["chanbw"]}"'))))
    write("etc/board.inc", "<?\n" + "".join(f"{k}={v};\n" for k, v in (
        ("$board_id", f'"{b["id"]}"'), ("$cpu_revision", f'"{b["cpu"]}"'),
        ("$board_name", f'"{b["name"]}"'), ("$board_raw_name", f'"{b["name"]}"'),
        ("$board_shortname", f'"{b["short"]}"'), ("$board_subtype", '""'),
        ("$board_hwaddr", f'"{MAC}"'), ("$board_netmodes", 7), ("$board_portnumber", 1),
        ("$board_fcc_unii_factory_enabled", 0), ("$board_fcc_only_unii", 0),
        ("$board_fcc_unii_lock_state", 0), ("$board_fcc_id", '""'),
        ("$reboot_time", b["reboot"]), ("$upgrade_time", b["upgrade"]),
        ("$session_id", f'"AIROS_{MAC}"'), ("$radio_count", 1),
        ("$radio1_name", f'"{b["radio"]}"'), ("$radio1_shortname", f'"{b["radio_short"]}"'),
        ("$radio1_bus", '"ahb"'), ("$radio1_devdomain", 0), ("$radio1_ccode", b["ccode"]),
        ("$radio1_ccode_fixed", 0), ("$radio1_ccode_locked", 0),
        ("$radio1_subsystemid", f'"{b["id"]}"'), ("$radio1_subvendorid", '"0x0777"'),
        ("$radio1_txpower_max", b["txmax"]), ("$radio1_txpower_min", b["txmin"]),
        ("$radio1_txpower_offset", 0), ("$radio1_ieee_modes", 5),
        ("$radio1_ieee_mode", '"11naht"'), ("$radio1_ant_count", 1),
        ("$radio1_def_antenna", 1),
        ("$radio1_ant_name[1]", f'dict_translate("{b["ant_name"]}")'),
        ("$radio1_ant_gain[1]", b["ant_gain"]), ("$radio1_ant_builtin[1]", 1),
        ("$radio1_ant_chains[1]", b["chains"]), ("$radio1_caps", CAPS),
        ("$radio1_distance_limit_km", 0), ("$radio1_regdomain_flags", '""'),
        ("$radio1_chains", b["chains"]), ("$radio1_ieee_mode_a", 1),
        ("$radio1_antennas", 1), ("$radio1_low_txpower_mode", 0), ("$radio1_legacy", 0),
        ("$radio1_chanbw", f'"{b["chanbw"]}"'), ("$radio1_ext_channel", 1))) + ">\n")

    # the lighttpd.conf its ubntconf writes: server.port is HTTP's (80), which sends to HTTPS,
    # a socket of its own on 443
    write("etc/lighttpd.conf", f"""server.document-root = "/usr/www"
include "/usr/etc/lighttpd/modules.conf"
include "/usr/etc/lighttpd/mimetypes.conf"
include "/usr/etc/lighttpd/lighttpd.conf"
airos.session-timeout = 900
airos.cookie-name = "AIROS_{MAC}"
server.port = 80
$SERVER["socket"] == ":443" {{
	ssl.engine = "enable"
	ssl.pemfile = "/etc/server.pem"
	ssl.use-sslv2 = "disable"
	ssl.cipher-list = "aRSA+HIGH:!3DES:!kEDH:+kRSA:!kSRP:!kPSK"
}}
$SERVER["socket"] == ":80" {{
	ssl.engine = "disable"
	airos.redirect-https-port = 443
}}
""")
    for rel, script in STUBS.items():
        write(rel, script, 0o755)          # (instead of ubntbox's links and the real script)
    return root


# -- running it (inside its namespaces: unshare -rnmpf --kill-child --mount-proc) -------------

def start(root, log=None):
    """Its lighttpd, chrooted in root; back when its HTTPS answers."""
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    subprocess.run(["mount", "-t", "proc", "proc", os.path.join(root, "proc")], check=True)
    for d in ("null", "zero", "random", "urandom", "tty"):
        subprocess.run(["mount", "--bind", "/dev/" + d, os.path.join(root, "dev", d)],
                       check=True)
    out = open(log or os.path.join(root, "tmp", "lighttpd.out"), "w")
    web = subprocess.Popen(["chroot", root, "/bin/lighttpd", "-D", "-f", "/etc/lighttpd.conf"],
                           stdout=out, stderr=subprocess.STDOUT)
    end = time.time() + 60
    while time.time() < end:
        try:
            socket.create_connection((HOST, 443), timeout=1).close()
            return web
        except OSError:
            time.sleep(0.3)
    web.kill()
    raise RuntimeError("its lighttpd does not answer")


def first_login(root, country="840", password=PASSWORD):
    """Its first login, as its web does it: a protected page gives the session cookie, and the
    form of login.cgi sends the user, the new password twice (6.3 does not keep the factory
    one) and the country. Then the configuration has them, saved."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from airos_tui.device.client import _tls_context
    conn = http.client.HTTPSConnection(HOST, 443, context=_tls_context(), timeout=60)
    conn.request("GET", "/")
    resp = conn.getresponse()
    resp.read()
    cookies = "; ".join(c.split(";", 1)[0] for c in resp.headers.get_all("Set-Cookie") or [])
    body = urllib.parse.urlencode({"uri": "/index.cgi", "username": "ubnt",
                                   "password": password, "password2": password,
                                   "country": country, "ui_language": "en_US",
                                   "lang_changed": "no"})
    conn.request("POST", "/login.cgi", body, {
        "Cookie": cookies, "Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    if resp.status != 302:
        raise RuntimeError(f"its first login did not go through (HTTP {resp.status})")
    running = os.path.join(root, "tmp", "running.cfg")
    end = time.time() + 30                     # (ccode.sh saves it in the background)
    while time.time() < end:
        if f"radio.1.countrycode={country}\n" in open(running).read():
            return
        time.sleep(0.5)
    raise RuntimeError("its first login did not save the country")


def main(argv):
    if argv[:1] == ["fetch"]:
        fetch()
        return 0
    if argv[:1] == ["shell"]:
        if os.environ.get("AIROS_EMU6") != "1":
            lack = missing()
            if lack:
                print("Missing:", "; ".join(lack))
                return 2
            os.environ["AIROS_EMU6"] = "1"
            os.execvp("unshare", ["unshare", "-rnmpf", "--kill-child", "--mount-proc",
                                  sys.executable, os.path.abspath(__file__), "shell"])
        root = prepare(os.path.join(CACHE, "shell-root"))
        web = start(root)
        first_login(root)
        print(f"airOS 6 emulated at https://{HOST} (ubnt / {PASSWORD}); its root: {root}.\n"
              "Leaving this shell turns it off.")
        subprocess.run([os.environ.get("SHELL", "/bin/sh")])
        web.terminate()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
