"""airOS 8 emulated for the tests: the web of a real firmware (WA 8.7.25, from Ubiquiti), run
with QEMU's user mode in namespaces of its own (no root; no network but its own loopback): its
lighttpd and its Lua API, on a fake board (a NanoStation 5AC loco); what needs the hardware
is stood in for (see STUBS). Its radio, its JavaScript and its services are not there.

    python3 tests/emu/emu8.py fetch   the firmware, once (kept in ~/.cache/airos-tui/emu8)
    python3 tests/emu/emu8.py shell   the emulated device and a shell beside it, to try the
                              program

It needs what emu6.py needs (qemu-user and qemu-user-binfmt, unsquashfs, unprivileged user
namespaces), and a C compiler that links statically (gcc -static: see qcpu). Its binaries are
for the 74Kc of its board (MIPS with DSP): QEMU runs them as a 74Kf (QEMU_CPU)."""
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu6  # noqa: E402

FIRMWARE = {
    "name": "WA.v8.7.25.bin",
    "url": "https://dl.ui.com/firmwares/XC-fw/v8.7.25/WA.v8.7.25.48568.260730.1010.bin",
    # (Ubiquiti publishes none where it can be read without JavaScript: the one of the file
    # downloaded twice from its address, with every block's CRC32 right)
    "sha256": "e87ffba1f0ed9e245b42541a86044f7688f3a7ba1ca783a30263ad7d9ad3dbc3",
}
CACHE = os.environ.get("AIROS_EMU8_CACHE") or os.path.join(
    os.path.expanduser("~"), ".cache", "airos-tui", "emu8")
CPU = "74Kf"
HOST = emu6.HOST


# -- what is needed ---------------------------------------------------------------------

def firmware_path():
    return os.path.join(CACHE, FIRMWARE["name"])


def missing():
    """What this machine lacks to run it (empty: nothing)."""
    out = [x for x in emu6.missing() if "firmware" not in x and "emu6.py" not in x]
    if not shutil.which("gcc"):
        out.append("gcc (for its qcpu wrapper, linked statically)")
    if not os.path.exists(firmware_path()):
        out.append(f"the firmware (python3 {os.path.abspath(__file__)} fetch, once)")
    return out


def fetch():
    """Download the firmware from Ubiquiti, check its sha256 and keep it in CACHE."""
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    req = urllib.request.Request(FIRMWARE["url"], headers={"User-Agent": "airos-tui-tests"})
    with urllib.request.urlopen(req, timeout=300) as resp:
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
    while data[off:off + 4] == b"PART":          # (its blocks: u-boot, kernel, rootfs)
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
    # (as a user it cannot make its /dev/console: the only thing it leaves out)
    subprocess.run(["unsquashfs", "-no-xattrs", "-q", "-d", tmp, part],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.unlink(part)
    assert os.path.isfile(os.path.join(tmp, "bin", "ubntbox")), "the rootfs did not extract"
    os.replace(tmp, root)
    return root


# -- its root, as it is after booting ------------------------------------------------------

# lighttpd runs its CGIs (its API, airos-api.lua with /bin/lua; status.cgi, ubntbox) with an
# environment of their own, without QEMU_CPU, and as a 24Kf they die (an illegal
# instruction): they are run through qcpu, a native program (static: the root has no libraries
# of this machine) that sets it. As qcpu-lua it is the interpreter of the .lua ones.
QCPU_C = r"""#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    const char *self = strrchr(argv[0], '/');
    char *args[argc + 1];
    int i;
    self = self ? self + 1 : argv[0];
    if (argc < 2) return 127;
    setenv("QEMU_CPU", "74Kf", 1);
    if (strcmp(self, "qcpu-lua") == 0) {
        args[0] = "/bin/lua";
        for (i = 1; i <= argc; i++) args[i] = argv[i];
        execv("/bin/lua", args);
    } else {
        execv(argv[1], argv + 1);
    }
    return 127;
}
"""
CGIS = ("status.cgi", "signal.cgi", "glogo.cgi", "hist-stats.cgi")


def qcpu():
    """The wrapper, built once into CACHE."""
    exe = os.path.join(CACHE, "qcpu-" + hashlib.sha256(QCPU_C.encode()).hexdigest()[:12])
    if not os.path.exists(exe):
        src = exe + ".c"
        with open(src, "w") as f:
            f.write(QCPU_C)
        subprocess.run(["gcc", "-static", "-Os", "-s", "-o", exe + ".tmp", src], check=True)
        os.replace(exe + ".tmp", exe)
        os.unlink(src)
    return exe

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures", "real")
# a real NanoStation 5AC loco's board.info (its MAC, a made-up one), as its ubnthal module
# writes it; and a real 8.7.25's status.cgi (see status.cgi in STUBS)
BOARD_INFO = os.path.join(FIXTURES, "airos8.7.14-nanostation5ac-sta.board.info")
STATUS = os.path.join(FIXTURES, "airos8.7.25-litebeam5ac-ap-ptp.status.json")

CCODE = r"""#!/bin/sh
# Emulation stand-in for ccode.sh: its save_cfg (the configuration sorted, in use and "in
# flash"), at once; there is no radio to set the country on. Its calls go to
# /var/log/ccode.log.
echo "$*" >> /var/log/ccode.log
[ $# -lt 1 ] && exit 254
sort /tmp/system.cfg > /tmp/.system.cfg.$$
cp /tmp/.system.cfg.$$ /tmp/running.cfg
cp /tmp/.system.cfg.$$ /tmp/system.cfg
/sbin/cfgmtd -w -f /tmp/.system.cfg.$$ -p /etc/
rm -f /tmp/.system.cfg.$$
exit 0
"""

STUBS = {
    "bin/cfgmtd": r"""#!/bin/sh
# Emulation stand-in for cfgmtd: the configuration "in flash" is /etc/persistent/flash.cfg,
# and its backup copy (-t 2), flash-backup.cfg. Its calls go to /var/log/cfgmtd.log.
#   -w [-f FILE]: keep FILE (by default /tmp/system.cfg) · -r [-f FILE]: put it in FILE
echo "$*" >> /var/log/cfgmtd.log
FLASH=/etc/persistent/flash.cfg
mode=
file=/tmp/system.cfg
while [ $# -gt 0 ]; do
    case "$1" in
        -w) mode=w ;;
        -r) mode=r ;;
        -f) shift; file=$1 ;;
        -t) shift; [ "$1" = 2 ] && FLASH=/etc/persistent/flash-backup.cfg ;;
        -p|-e|-o) shift ;;
    esac
    shift
done
case $mode in
    w) cp "$file" $FLASH ;;
    r) [ -s $FLASH ] && cp $FLASH "$file" ;;
    *) exit 1 ;;
esac
""",
    # status.cgi reads what a daemon of its radio leaves in shared memory (/device_status):
    # here a real 8.7.25's, with this device's name and model
    "usr/www/status.cgi": r"""#!/bin/sh
# Emulation stand-in for status.cgi (see emu8.py).
name=$(sed -n 's/^resolv\.host\.1\.name=//p' /tmp/running.cfg | tail -n 1 | tr -d '"\\/&')
echo "Content-Type: application/json"
echo ""
sed "s/__HOSTNAME__/${name:-ubnt}/" /usr/lib/emu-status.json
""",
    # ubntconf needs the board in /proc/ubnthal (without it, it crashes): of what it writes,
    # /etc/passwd, where its web's login looks the user up (ulib: getpwnam and crypt), from
    # the users of the configuration (in one awk: each program costs under QEMU)
    "bin/ubntconf": r"""#!/bin/sh
# Emulation stand-in for ubntconf: of what it writes, only /etc/passwd, from the users of
# /tmp/system.cfg (name:hash:uid:gid:comment:homedir:shell). Its options are accepted and
# ignored; its calls go to /var/log/ubntconf.log.
echo "$*" >> /var/log/ubntconf.log
awk '
/^users\.[0-9]+\.[a-z]+=/ {
    split($0, kv, "="); split(kv[1], k, ".")
    v = substr($0, length(kv[1]) + 2); u[k[2] "," k[3]] = v
    if (k[2] + 0 > n) n = k[2] + 0
}
function d(i, f, def) { return (u[i "," f] == "") ? def : u[i "," f] }
END {
    for (i = 1; i <= n; i++)
        if (u[i ",name"] != "" && u[i ",status"] != "disabled")
            print u[i ",name"] ":" u[i ",password"] ":" d(i, "uid", 0) ":" d(i, "gid", 0) ":" \
                d(i, "comment", "Administrator") ":" d(i, "homedir", "/etc/persistent") ":" \
                d(i, "shell", "/bin/sh")
}' /tmp/system.cfg > /etc/passwd.new
[ -s /etc/passwd.new ] && mv /etc/passwd.new /etc/passwd
exit 0
""",
    # ccode.sh (its API's country change, also in its first setup) sets the radio's country
    # (no radio here: slow under QEMU and for nothing) and then saves the configuration with
    # a cfgmtd in the background, which, that slow, saved whatever the next request had left
    # by then: here what it leaves in the files, at once
    "bin/ccode.sh": CCODE,
    "usr/bin/ccode.sh": CCODE,
    "usr/sbin/ccode.sh": CCODE,
    # a reboot: here nothing restarts, but what its rc.stop and its /init do with the
    # configuration (with factory_reset's mark, the factory one) happens, and the sessions go
    "bin/reboot": r"""#!/bin/sh
# Emulation stand-in for reboot: rc.stop (with /etc/reset-to-defaults, factory_reset's mark,
# the factory configuration "in flash") and then /init (the one "in flash", sorted, in use;
# its users); the sessions and a test in progress go. Its calls go to /var/log/reboot.log.
echo "$*" >> /var/log/reboot.log
if [ -e /etc/reset-to-defaults ]; then
    /sbin/cfgmtd -w -f /etc/default.cfg -p /etc/
    rm -f /etc/reset-to-defaults
fi
/sbin/cfgmtd -r -p /etc/ -f /tmp/.boot.cfg
sort /tmp/.boot.cfg | tr -d "\r" > /tmp/system.cfg
rm -f /tmp/.boot.cfg /var/run/testmode
cp /tmp/system.cfg /tmp/running.cfg
/sbin/ubntconf
rm -rf /tmp/.sessions/*
exit 0
""",
    "bin/testmode-revert": r"""#!/bin/sh
# Emulation stand-in for what reverts a test when its time is over (inside ubntbox on the
# real one): if it is still undecided then, the configuration "in flash" goes in use again.
sleep $1
if [ -e /var/run/testmode ]; then
    rm -f /var/run/testmode
    /sbin/cfgmtd -r -p /etc/ -f /tmp/system.cfg
    cp /tmp/system.cfg /tmp/running.cfg
    /sbin/ubntconf
fi
rm -f /var/run/testmode.revert
""",
    "usr/etc/rc.d/rc.softrestart": r"""#!/bin/sh
# Emulation stand-in for rc.softrestart (and rc.do.softrestart): what it leaves in the
# configuration files and in the test-mode lock, as the real one; there are no services to
# restart here. While a test runs, it also stands in for what reverts it when its time (the
# number in the lock) is over.
CFG_SYSTEM=/tmp/system.cfg
CFG_RUNNING=/tmp/running.cfg
TEST_LOCK_FILE=/var/run/testmode
REVERT=/var/run/testmode.revert
echo "$*" >> /var/log/softrestart.log
echo 1 > /tmp/.rc_is_running
case "$1" in
    save)
        sort $CFG_RUNNING | tr -d "\r" > /tmp/.r.$$
        sort $CFG_SYSTEM | tr -d "\r" > /tmp/.s.$$
        if ! diff /tmp/.r.$$ /tmp/.s.$$ > /dev/null; then
            /sbin/ubntconf
            sort $CFG_SYSTEM | tr -d "\r" > $CFG_RUNNING
            /sbin/cfgmtd -w -f $CFG_RUNNING -p /etc/
        fi
        rm -f /tmp/.r.$$ /tmp/.s.$$ /tmp/.rc_is_running
        ;;
    test)
        /sbin/ubntconf
        cp $CFG_SYSTEM $CFG_RUNNING
        [ -e $TEST_LOCK_FILE ] && chmod 644 $TEST_LOCK_FILE
        [ -e $REVERT ] && kill $(cat $REVERT) 2> /dev/null
        secs=$(cat $TEST_LOCK_FILE 2> /dev/null)
        /bin/testmode-revert ${secs:-180} < /dev/null > /dev/null 2>&1 &
        echo $! > $REVERT
        rm -f /tmp/.rc_is_running
        ;;
    force)
        /sbin/ubntconf
        rm -f /tmp/.rc_is_running
        ;;
    *)
        echo "Usage: /usr/etc/rc.d/rc.softrestart (force|test|save)"
        exit 1
        ;;
esac
exit 0
""",
}


def prepare(root):
    """A copy of its root in root (whatever was there goes), as it is after booting: the /etc
    its /init builds, its factory configuration in /tmp (and "in flash"), the board, a
    certificate of its own, the lighttpd.conf its ubntconf writes and the stand-ins. As the
    factory configuration is, it waits for its first setup."""
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

    # what /init does (/etc is var/etc)
    for d in ("var/run", "var/tmp", "var/log", "var/lock", "var/etc/persistent",
              "var/etc/rc.d", "var/etc/init.d", "var/etc/udhcpc", "var/etc/sysinit",
              "var/etc/httpd", "tmp", "proc", "sys", "dev/pts", "dev/shm"):
        os.makedirs(path(d), exist_ok=True)
    for f in ("inittab", "rc.d/rc.sysinit", "rc.d/rc", "rc.d/rc.stop"):
        if os.path.exists(path("usr/etc/" + f)):
            shutil.copy2(path("usr/etc/" + f), path("etc/" + f))
    for f in ("passwd", "group", "login.defs", "profile", "hosts", "host.conf", "fstab",
              "udhcpc/udhcpc", "startup.list"):
        if os.path.exists(path("usr/etc/" + f)) and not os.path.lexists(path("etc/" + f)):
            shutil.copy2(path("usr/etc/" + f), path("etc/" + f))
    for f in ("services", "protocols", "shells", "mime.types", "ethertypes", "modules.d"):
        if not os.path.lexists(path("etc/" + f)):
            os.symlink("/usr/etc/" + f, path("etc/" + f))
    if os.path.lexists(path("etc/default.cfg")):
        os.unlink(path("etc/default.cfg"))
    os.symlink("/usr/etc/system.cfg", path("etc/default.cfg"))
    for d in ("null", "zero", "random", "urandom", "tty"):   # (where /dev's are bound)
        write("dev/" + d, "")

    # its factory configuration, as /init leaves it (sorted, without \r)
    lines = sorted(line.rstrip("\r\n") for line in open(path("usr/etc/system.cfg"))
                   if line.strip())
    text = "\n".join(lines) + "\n"
    for rel in ("tmp/system.cfg", "tmp/running.cfg", "etc/persistent/flash.cfg"):
        write(rel, text)
    cfg = dict(line.split("=", 1) for line in lines if "=" in line)
    write("etc/passwd", f"{cfg['users.1.name']}:{cfg['users.1.password']}:0:0:"
                        "Administrator:/etc/persistent:/bin/sh\n")

    # its version as its boot leaves it in /etc/version, without the chip and the build
    # (WA.v8.7.25: so its backups have it, cfg.cgi's "##WA.v8.7.25", and its API checks it)
    m = re.match(r"([A-Za-z0-9]+)\.[^.]+\.(v\d+\.\d+\.\d+)",
                 open(path("usr/lib/version"), encoding="utf-8").read())
    write("etc/version", f"{m.group(1)}.{m.group(2)}\n")

    # the board, what its ubnthal module would write; and what its status.cgi gives
    shutil.copy(BOARD_INFO, path("etc/board.info"))
    status = json.load(open(STATUS, encoding="utf-8"))
    status["host"].update(hostname="__HOSTNAME__", devmodel="NanoStation 5AC loco")
    write("usr/lib/emu-status.json", json.dumps(status))

    # a certificate of its own (the real one makes it with certgen, once)
    key, crt = path("etc/httpd/server.key"), path("etc/httpd/server.crt")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                    "-subj", "/CN=UBNT", "-keyout", key, "-out", crt], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(path("etc/server.pem"), "w") as out:
        for f in (crt, key):
            out.write(open(f).read())

    # the lighttpd.conf its ubntconf writes: HTTP on 80, HTTPS on 443; and two changes of
    # the emulation: its CGIs run through qcpu, and its errors go to a file (no syslog)
    shutil.copy(qcpu(), path("bin/qcpu"))
    os.symlink("qcpu", path("bin/qcpu-lua"))
    conf = open(path("usr/etc/lighttpd/lighttpd.conf")).read()
    conf = conf.replace('server.errorlog-use-syslog = "enable"',
                        'server.errorlog = "/var/log/lighttpd.err"')
    for cgi in CGIS:
        assert f'"{cgi}" => ""' in conf, cgi
        conf = conf.replace(f'"{cgi}" => ""', f'"{cgi}" => "/bin/qcpu"')
    assert '".lua"       => "/bin/lua"' in conf
    conf = conf.replace('".lua"       => "/bin/lua"', '".lua"       => "/bin/qcpu-lua"')
    write("usr/etc/lighttpd/lighttpd.conf", conf)
    write("etc/lighttpd.conf", """server.document-root = "/usr/www"
include "/usr/etc/lighttpd/modules.conf"
include "/usr/etc/lighttpd/mimetypes.conf"
include "/usr/etc/lighttpd/lighttpd.conf"
server.modules += ( "mod_openssl" )
server.port = 80
$SERVER["socket"] == ":443" {
	ssl.engine = "enable"
	ssl.pemfile = "/etc/server.pem"
}
""")
    for rel, script in STUBS.items():
        write(rel, script, 0o755)          # (instead of ubntbox's links and the real script)
    return root


# -- running it (inside its namespaces: unshare -rnmpf --kill-child --mount-proc) -------------

def start(root, log=None):
    """Its lighttpd, chrooted in root, its MIPS programs run as a 74Kf; back when its HTTPS
    answers."""
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    subprocess.run(["mount", "-t", "proc", "proc", os.path.join(root, "proc")], check=True)
    for d in ("null", "zero", "random", "urandom", "tty"):
        subprocess.run(["mount", "--bind", "/dev/" + d, os.path.join(root, "dev", d)],
                       check=True)
    out = open(log or os.path.join(root, "tmp", "lighttpd.out"), "w")
    web = subprocess.Popen(["chroot", root, "/bin/lighttpd", "-D", "-f", "/etc/lighttpd.conf"],
                           stdout=out, stderr=subprocess.STDOUT, env=dict(os.environ, QEMU_CPU=CPU))
    end = time.time() + 90
    while time.time() < end:
        if web.poll() is not None:
            raise RuntimeError(f"its lighttpd ended ({web.returncode}): "
                               + open(out.name).read()[-500:])
        try:
            socket.create_connection((HOST, 443), timeout=1).close()
            return web
        except OSError:
            time.sleep(0.3)
    web.kill()
    raise RuntimeError("its lighttpd does not answer")


BACKGROUND = ("ccode.sh", "cfgmtd", "ubntconf", "factory_reset", "reboot", "rc.softrestart")


def settle(root, wait=30.0):
    """Wait for what its API leaves running in the background (ccode.sh, a cfgmtd, a reboot…):
    in these namespaces, /proc has only the emulation's processes. (Its API may answer before
    it starts them: a moment first.)"""
    time.sleep(0.3)
    end = time.time() + wait
    while time.time() < end:
        busy = False
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmd = open(f"/proc/{pid}/cmdline", "rb").read()
            except OSError:
                continue
            if any(name.encode() in cmd for name in BACKGROUND):
                busy = True
                break
        if not busy and not os.path.exists(os.path.join(root, "tmp", ".rc_is_running")):
            return
        time.sleep(0.2)


def main(argv):
    if argv[:1] == ["fetch"]:
        fetch()
        return 0
    if argv[:1] == ["shell"]:
        if os.environ.get("AIROS_EMU8") != "1":
            lack = missing()
            if lack:
                print("Missing:", "; ".join(lack))
                return 2
            os.environ["AIROS_EMU8"] = "1"
            os.execvp("unshare", ["unshare", "-rnmpf", "--kill-child", "--mount-proc",
                                  sys.executable, os.path.abspath(__file__), "shell"])
        root = prepare(os.path.join(CACHE, "shell-root"))
        web = start(root)
        print(f"airOS 8 emulated at https://{HOST}: it waits for its first setup (the one the "
              f"TUI asks for when it connects); its root: {root}.\nLeaving this shell turns it "
              "off.")
        subprocess.run([os.environ.get("SHELL", "/bin/sh")])
        web.terminate()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
