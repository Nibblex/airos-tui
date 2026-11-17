"""The simulated device: its configuration (flash, /tmp/system.cfg and the one in
use), its restarts and test mode, its sessions, its stability and its pages' data (status,
stations, tools, airView…). No HTTP: that is in web8 and web6."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tarfile
import threading
import time
from .data import (
    BACKUP_PART_RE, BOARD, COMMON_CFG, COUNTRIES, FIRMWARE, LEASES, MAC, MODEL, NEIGHBORS,
    NET_BRIDGE, NET_ROUTER, PASSWORD_RE, PORTFW_OFF, PPPOE_PASSWORD, PPPOE_SESSION, PPPOE_USER,
    STATION, V6_EXTRA, V8_EXTRA, _EB_PROTOS, _IPT_HEADER, _IPT_PROTOS, _IP_PROTOS, _cc,
    airview_payload, channels_of,)
from .frames import LLDP_TRUE, cdp_frame, lldp_frame
from .cfgfile import SIM_PASSWORDS, cfg_text, parse_cfg, sim_hash


def _netconf_by_role(c: dict, role: str) -> int | None:
    for k, v in sorted(c.items()):
        m = re.fullmatch(r"netconf\.(\d+)\.role", k)
        if m and v == role and c.get(f"netconf.{m.group(1)}.status") == "enabled":
            return int(m.group(1))
    return None

class Device:
    def __init__(self, version: int, user: str, password: str, restart: float,
                 test_secs: int, mode: str = "bridge", mgmt_ip: str | None = None,
                 dhcp_ip: str = "127.0.0.77", station: bool = False):
        self.version, self.user, self.password = version, user, password
        self.restart, self.test_secs = restart, test_secs
        net = NET_ROUTER if mode == "router" else NET_BRIDGE
        cfg = parse_cfg(COMMON_CFG + net + (V8_EXTRA if version == 8 else V6_EXTRA))
        self.pw_hash = sim_hash(password)
        cfg["users.1.name"] = user
        cfg["users.1.password"] = self.pw_hash
        if station:                   # client (CPE) instead of access point
            cfg.update(STATION[version])
        if "mac-telnetd.users.1.password" in cfg:
            cfg["mac-telnetd.users.1.password"] = self.pw_hash
        # With a 127.x management IP the simulator "moves" to another IP like the real antenna;
        # with web_follow (make_server), its web to another port (httpd.port).
        self.ip_follow = self.follow = bool(mgmt_ip)
        self.web_follow = False
        self.at = (mgmt_ip or "127.0.0.1", 0)     # (where its first server listens)
        self.dhcp_ip = dhcp_ip
        if mgmt_ip:
            n = _netconf_by_role(cfg, "lan" if mode == "router" else "mlan")
            cfg[f"netconf.{n}.ip"] = mgmt_ip
            if mode == "router":       # the LAN's DHCP range, inside its network
                base = mgmt_ip.rsplit(".", 1)[0]
                cfg["dhcpd.1.start"], cfg["dhcpd.1.end"] = f"{base}.100", f"{base}.200"
        self.saved = dict(cfg)      # flash
        self.system = dict(cfg)     # /tmp/system.cfg
        self.running = dict(cfg)    # configuration in use
        self.modified = False       # airOS 6: uploaded changes not applied
        self.sessions: dict[str, dict] = {}
        self.down_until = 0.0
        # airOS 8 with its first setup (8.7.x): after a reset it waits for it (the country, its
        # terms and a new administrator) and says so in /api/info/public.
        self.wizard = self.setup_pending = False
        # (set up, but with the factory password still: its web asks for a new one, or a backup)
        self.factory_password = False
        self.setup_before_backup = None   # (its first setup from a backup: as it was before)
        self.survey_until: float | None = None   # the site survey in progress ends then
        self.backup_loaded = False   # airOS 8: a backup uploaded (cfgupl.cgi), not applied yet
        self.fw_version = "v8.7.19" if version == 8 else "v6.3.16"
        self.fw_pending: str | None = None     # airOS 8: an uploaded firmware (WA.v8.7.20)
        self.fw_third_party = False
        # airOS 8: the notice it has as it starts (emergReasonCode): -1 none, 0 a reboot by
        # the ping watchdog, 1 a flash memory failure, 2 or 3 a crash
        self.emerg_reason = -1
        self.provmode_until = None             # the management radio, on until then
        self.pings = 0                          # (each ping a slightly different time)
        self.speedtest: dict | None = None      # the speed test in progress
        self.speedtest_speedup = 1.0            # (the tests make its seconds shorter)
        # airView: its reads so far (a new one every airview_period seconds while it runs; 0,
        # at each request) and the last one given
        self.airview_period = 1.0
        self.airview_frame, self.airview_at, self.airview_cache = 0, time.time(), None
        # whether this computer is on the management VLAN (then it still reaches the device)
        self.on_vlan = False
        self.fw_available: str | None = None   # what /api/fw/update-check finds, if anything
        # seconds /api/fw/download takes (its answer comes when it is over, and
        # /api/fw/download-progress tells how far it is meanwhile, as a real one)
        self.fw_download_seconds = 0.0
        self.fw_download_started: float | None = None
        self.flash_delay = 0.0       # seconds its web still answers after fwflash.cgi
        # airOS 6: its radio's capabilities ($radio1_caps), which its System page publishes;
        # 8192 is the fast clock (radio_cap_fast_clock)
        self.radio_caps = 8192
        self.fw_check_error = False            # the update server cannot be reached
        self.unms_state = 0     # UISP in status.cgi: 0 not configured, 1 connecting, 2 connected
        self.kicked_until: dict[str, float] = {}   # stakick.cgi: they come back after a while
        self.test_until: float | None = None
        self.boot = time.time()
        self.events: list[str] = []
        self.lock = threading.RLock()
        self.on_address = None       # tells the servers where it is reached (IP and port)
        self.ppp_up_at = time.time()  # when the PPPoE session comes up (if it can)
        self.ui_lang = "en"           # language of the airOS 6 pages and of the airOS 8 texts
        # airOS 6 copies the new configuration to /tmp/running.cfg (what pppinfo.cgi reads)
        # after restarting the services: for a while the web answers with the old one.
        self.prev_running: dict | None = None
        self.running_cfg_at = 0.0

    def apply_running(self, new: dict) -> None:
        """airOS 6: start using new (rc.softrestart test/save)."""
        self.prev_running = dict(self.running)
        self.running = dict(new)
        self.soft_restart()
        self.running_cfg_at = self.down_until + 2.0

    def pppoe_state(self) -> dict:
        """What pppd would tell: {'error': …} or the session data."""
        def entry(c: dict) -> int | None:
            k = next((m for m in range(1, 9) if c.get(f"ppp.{m}.status") == "enabled"), None)
            return k if c.get("ppp.status") == "enabled" else None
        seen = self.running         # what pppinfo.cgi reads to tell whether it is enabled
        if (self.version == 6 and self.prev_running is not None
                and time.time() < self.running_cfg_at):
            seen = self.prev_running
        if entry(seen) is None:
            return {"error": "PPPoE is not enabled."}
        c = self.running            # the real session depends on what pppd uses
        k = entry(c)
        if (k is None or c.get(f"ppp.{k}.name") != PPPOE_USER
                or c.get(f"ppp.{k}.password") != PPPOE_PASSWORD
                or c.get(f"ppp.{k}.require.mppe128") == "enabled"
                or time.time() < self.ppp_up_at):
            return {"error": "Not connected"}
        return dict(PPPOE_SESSION, duration=int(time.time() - self.ppp_up_at))

    def factory_reset(self) -> None:
        """/reset.cgi: the configuration it starts with (bridge, access point, user and
        password ubnt) and a reboot. The address stays: the simulator cannot move to
        192.168.1.20 as the real antenna does."""
        with self.lock:
            cfg = parse_cfg(COMMON_CFG + NET_BRIDGE + (V8_EXTRA if self.version == 8
                                                      else V6_EXTRA))
            self.user = self.password = "ubnt"
            self.pw_hash = sim_hash("ubnt")
            cfg["users.1.name"], cfg["users.1.password"] = "ubnt", self.pw_hash
            if "mac-telnetd.users.1.password" in cfg:
                cfg["mac-telnetd.users.1.password"] = self.pw_hash
            self.saved, self.system, self.running = dict(cfg), dict(cfg), dict(cfg)
            self.test_until, self.modified, self.backup_loaded = None, False, False
            self.sessions.clear()
            self.setup_pending = self.wizard         # (8.7.x: it waits for its first setup)
            self.factory_password, self.setup_before_backup = False, None
            self.boot = time.time()
            self.soft_restart(2)
            self.events.append("reset")

    def backup_file(self) -> str:
        """cfg.cgi (airOS 8): the backup its web downloads, as its backupcfg makes it: a part
        with the time, its firmware (/etc/version: ##WA.v8.7.19), its board and their md5,
        each line behind a #, and /tmp/system.cfg with the password hashes (its separator
        says SHA512 when that password is one)."""
        head = [f"# {int(time.time())}", f"#WA.{self.fw_version}"]
        head += [f"{k}={v}" for k, v in BOARD[8].items()]
        head.append(hashlib.md5(("\n".join(head) + "\n").encode()).hexdigest())
        sha512 = (self.system.get("users.1.password") or "").startswith("$6$")
        return ("### PART ###\n" + "".join(f"#{line}\n" for line in head)
                + f"### PART {'SHA512 ' if sha512 else ''}###\n" + cfg_text(self.system))

    @staticmethod
    def backup_config(text: str) -> dict[str, str]:
        """The configuration of a backup: its last part."""
        return parse_cfg(BACKUP_PART_RE.split(text)[-1])

    @staticmethod
    def setup_backup_problem(text: str) -> str | None:
        """Its first setup's check of a backup (cfg_backup_validate): the structure of a
        configuration (radio.status and radio.1.status, on or off), its firmware's line
        (##WA.v8.7.25) and that firmware of this kind (WA, or XC). None, or its error."""
        new = Device.backup_config(text)
        if any(new.get(k) not in ("enabled", "disabled") for k in ("radio.status",
                                                                   "radio.1.status")):
            return "Invalid configuration file structure"
        fw = next((m.group(1) for m in map(re.compile(r"#+\s*([^.]+\.v[^.]+\..*)").match,
                                            text.splitlines()) if m), None)
        if fw is None:
            return "Error reading firmware version from uploaded config."
        if fw.split(".", 1)[0] not in ("WA", "XC"):
            return "Uploaded configuration file is not compatible with current firmware version"
        return None

    def upload_backup(self, data: bytes) -> bool:
        """cfgupl.cgi: the configuration of the file (its last part) waits to be applied or
        discarded. As writecfg.cgi, a password missing from it is kept."""
        text = data.decode("utf-8", "replace")
        new = self.backup_config(text)
        if new.get("radio.status") != "enabled":
            return False
        with self.lock:
            for k, v in self.system.items():
                if PASSWORD_RE.match(k) and k not in new:
                    new[k] = v
            self.system, self.backup_loaded = new, True
            self.events.append("backup-uploaded")
        return True

    def apply_backup(self) -> None:
        """apply.cgi with a backup waiting: it is used and saved. If its user or password is
        another one, the ones of before no longer work."""
        with self.lock:
            self.running, self.saved = dict(self.system), dict(self.system)
            self.backup_loaded, self.setup_before_backup = False, None
            self.user = self.running.get("users.1.name") or self.user
            if self.running.get("users.1.password") != self.pw_hash:
                # (its password: the one of that hash if a simulated device of this process
                # made it, as the backup of another one; otherwise nobody knows it)
                self.pw_hash = self.running.get("users.1.password") or ""
                self.password = SIM_PASSWORDS.get(self.pw_hash)
            self.soft_restart()
            self.events.append("backup-applied")

    def check_firmware(self, data: bytes) -> dict:
        """/fwupl.cgi: a simulated firmware starts with UBNT and says its version (WA.v8.7.20)
        near the start; "third-party" in it makes it one of those. The answer of the web's
        FwView: code 0 with version and thirdParty, or the error code (err)."""
        m = re.search(rb"\b([A-Z0-9]+)\.(v\d+\.\d+\.\d+[0-9A-Za-z.-]*)", data[:512])
        if not data.startswith(b"UBNT") or not m:
            return {"code": 1, "err": 101, "message": "Invalid firmware file"}
        if m.group(1) != b"WA":
            return {"code": 1, "err": 68, "message": "Wrong firmware architecture"}
        with self.lock:
            self.fw_pending = f"WA.{m.group(2).decode()}"
            self.fw_third_party = b"third-party" in data[:512]
            self.events.append("fw-uploaded")
        return {"code": 0, "version": self.fw_pending, "thirdParty": self.fw_third_party}

    def flash_firmware(self) -> None:
        """/fwflash.cgi do_update=1: the device writes it and reboots with it (a while)."""
        with self.lock:
            self.fw_version = self.fw_pending.split(".", 1)[1]
            self.fw_pending, self.fw_third_party = None, False
            self.system, self.running = dict(self.saved), dict(self.saved)
            self.test_until, self.backup_loaded = None, False
            self.sessions.clear()
            self.boot = time.time()
            self.soft_restart(3)
            self.events.append("fw-flashed")

    def v8_stations(self) -> list[dict]:
        """wireless.sta of status.cgi: one station (or, on a station, its access point), as
        a real one, with byte counters that grow 2 Mbit/s down and 4 up; none while kicked."""
        if time.time() < self.kicked_until.get("68:D7:9A:11:22:33", 0):
            return []
        up = max(0.0, time.time() - self.boot)
        return [{"mac": "68:D7:9A:11:22:33", "signal": -58, "rssi": 38, "noisefloor": -96,
                 "chainrssi": [37, 34, 0], "distance": 1200, "lastip": "192.168.1.21",
                 "uptime": 5400, "tx_latency": 2, "dl_avg_linkscore": 88,
                 "ul_avg_linkscore": 92,
                 "stats": {"rx_bytes": int(up * 250_000), "tx_bytes": int(up * 500_000),
                           "rx_pps": 180, "tx_pps": 320},
                 "airmax": {"dl_capacity": 210000, "ul_capacity": 190000,
                            "rx": {"cinr": 28, "usage": 31}, "tx": {"cinr": 27, "usage": 52}},
                 "remote": {"hostname": "Client-North-01", "signal": -60, "rssi": 36,
                            "noisefloor": -96, "rx_chainmask": 3, "chainrssi": [35, 33, 0],
                            "platform": "LiteBeam 5AC",
                            "version": "WA.ar934x.v8.7.19.48279.250811.0636"}}]

    def discovered(self) -> list[dict]:
        """POST /discovery.cgi: what the antenna finds with Ubiquiti discovery, in the fields of
        the web's DiscoveryResultModel (a real 8.7.25 that finds nobody answers
        {"devices": []}): the other end of the link and a router. Their IPs are 127.0.0.x, so
        that connecting to one never leaves this computer."""
        sta = str(self.running.get("wireless.1.mode", "")).startswith("sta")
        out = []
        for i, s in enumerate(self.v8_stations()):
            r = s.get("remote") or {}
            ip = f"127.0.0.{21 + i}"
            out.append({"hwaddr": s["mac"], "hostname": r.get("hostname", ""),
                        "wmode": 3 if sta else 2,
                        "essid": self.running.get("wireless.1.ssid", ""),
                        "product": r.get("platform", ""), "fwversion": r.get("version", ""),
                        "ipv4": ip, "uptime": int(time.time() - self.boot) + 7200,
                        "addresses": [{"hwaddr": s["mac"], "ipv4": ip}]})
        out.append({"hwaddr": "68:D7:9A:00:00:FE", "hostname": "Gateway", "wmode": 0,
                    "essid": "", "product": "EdgeRouter X",
                    "fwversion": "e50.v2.0.9-hotfix.7.5622731.230615.0857",
                    "ipv4": "127.0.0.254", "uptime": 864000,
                    "addresses": [{"hwaddr": "68:D7:9A:00:00:FE", "ipv4": "127.0.0.254"},
                                  {"hwaddr": "68:D7:9A:00:00:FF", "ipv4": "127.0.0.253"}]})
        return out

    def own_ips(self) -> list[str]:
        return [v for k, v in self.running.items() if re.fullmatch(r"netconf\.\d+\.ip", k)]

    def ping_answer(self, ip: str) -> str:
        """/pingtest_action.cgi: code|host|time|ttl, as the web reads it (-4: no answer)."""
        if ip in NEIGHBORS or ip in self.own_ips():
            _, ms, ttl = NEIGHBORS.get(ip, ("", 0.05, 64))
            self.pings += 1
            return f"0|{ip}|{round(ms + self.pings % 5 * 0.07, 3)}|{ttl}"
        return "-4|" if re.fullmatch(r"[0-9.]+|[0-9A-Fa-f:]+", ip) else "-1|"

    def trace_hop(self, target: str, hop: int, resolve: bool) -> dict:
        """/api/traceroute: one hop toward the target (by name too, if it knows it)."""
        ip = {name: a for a, (name, _, _) in NEIGHBORS.items()}.get(target, target)
        if not re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", ip):
            return {"error": "Unknown host"}
        if ip in ("192.168.1.1", "192.168.1.21") or ip in self.own_ips():
            route = [ip]                          # (on its own network)
        elif ip in NEIGHBORS:
            route = ["192.168.1.1", None, "100.64.0.1", ip]
        else:                                     # (nobody answers past the provider)
            route = ["192.168.1.1", None, "100.64.0.1"] + [None] * 27
        here = route[hop - 1] if 1 <= hop <= len(route) else None
        if here is None:
            return {"hop": hop, "timeout": True, "target_ip": ip}
        name, ms, _ = NEIGHBORS.get(here, (here, 0.05, 64))
        rtts = [round(ms + d, 3) for d in (0.0, 0.11, 0.05)]
        if here == "100.64.0.1":                  # (as a real one: some probes unanswered)
            rtts[1] = "*"
        return {"hop": hop, "ip": here, "hostname": name if resolve else here, "timeout": False,
                "rtts": rtts, "last_hop": here == ip, "target_ip": ip}

    def speedtest_status(self, ticket: int) -> dict:
        """The speed test in progress, as a real 8.7.25 answers it: state 5 while it runs and 9
        at the end, about three figures a second of each direction (Mbps), the first one 0."""
        t = self.speedtest
        if not t or t["ticket"] != ticket:
            return {"ticket": ticket, "state": 0}
        elapsed = min((time.time() - t["start"]) * self.speedtest_speedup, t["duration"])
        n = int(elapsed * 3.3) + 1
        rx = ([0.0] + [round(152.4 + 6.1 * math.sin(i), 2) for i in range(1, n)]
              if t["direction"] != "tx" else [])
        tx = ([0.0] + [round(121.7 + 4.3 * math.cos(i), 2) for i in range(1, n)]
              if t["direction"] != "rx" else [])
        out = {"ticket": ticket, "state": 9 if elapsed >= t["duration"] else 5,
               "duration": round(elapsed, 1), "rx_speed": rx, "tx_speed": tx}
        for side, xs in (("rx", rx), ("tx", tx)):
            out.update({f"avg_{side}": round(sum(xs) / len(xs), 2) if xs else 0,
                        f"max_{side}": max(xs, default=0), f"min_{side}": min(xs, default=0)})
        return out

    def warnings_info(self) -> dict:
        """GET /api/warnings, as a NanoStation 5AC with airOS 8.7.14 answers it (8.7.25 gives
        firmware.requirePasswd too)."""
        return {"isDefaultPasswd": self.password == "ubnt", "customScripts": False,
                "isWatchdogReset": 0, "label": 0, "chAvailable": False,
                "emergReasonCode": self.emerg_reason,
                "firmware": {"isThirdParty": self.fw_third_party, "requirePasswd": False,
                             "version": self.fw_pending or "", "uploaded": bool(self.fw_pending)}}

    def support_file(self) -> bytes:
        """/support.cgi: a .tar.gz with a folder support-<prefix>-<mac> of text files, as the
        real one (which has about a hundred: logs, statistics, the configuration…)."""
        folder = f"support-WA-{MAC.lower()}"
        files = {"uptime": f"{int(time.time() - self.boot)}\n",
                 "status.cgi.txt": json.dumps(self.status(), indent=1),
                 "system.cfg": cfg_text({k: ("-" if PASSWORD_RE.match(k) else v)
                                         for k, v in self.running.items()})}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(folder)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
            tar.addfile(info)
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f"{folder}/{name}")
                info.size, info.mtime = len(data), int(time.time())
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def login_name(self) -> str:
        """The administrator's name it takes now: the one of the configuration in use."""
        return self.running.get("users.1.name") or self.user

    def set_ro_password(self, password: str) -> None:
        """/pwd.cgi with ro=1: the read-only account's password, saved at once; and, as a
        real 8.7.14 does, the account turned on."""
        with self.lock:
            digest = sim_hash(password)
            for c in (self.saved, self.system, self.running):
                c["users.2.password"], c["users.2.status"] = digest, "enabled"
            self.events.append("ro-password")

    def management_radio(self, start: bool) -> None:
        """POST /api/provmode: on for 15 minutes, or off; its web server restarts."""
        self.provmode_until = time.time() + 900 if start else None
        self.soft_restart(0.5)
        self.events.append("provmode-start" if start else "provmode-stop")

    def set_password(self, password: str) -> None:
        """/pwd.cgi: the new password of the administrator, saved at once."""
        with self.lock:
            self.password = password
            self.pw_hash = sim_hash(password)
            for c in (self.saved, self.system, self.running):
                c["users.1.password"] = self.pw_hash
                if "mac-telnetd.users.1.password" in c:
                    c["mac-telnetd.users.1.password"] = self.pw_hash
            self.events.append("password")

    def pppoe_restart(self) -> None:
        self.ppp_up_at = time.time() + 1.0
        self.events.append("pppoe-restart")

    def first_setup(self, form: dict) -> str | None:
        """airOS 8 POST /api/auth with device_setup (its web's first access): the country and
        the language of its web ("first"), and the new administrator, sent with the factory
        user and password; then its quick restart. Or a backup (new_config: with "first", or
        "cfg" once set up with the factory password), which waits to be applied as one of
        cfgupl.cgi, with that country and language. None, or the error it answers."""
        kind = form.get("device_setup")
        backup = form.get("new_config")
        if kind == "first" and not form.get("new_ccode"):
            return "Missing device setup params."
        if kind == "first" and not self.setup_pending:
            return "Device setup is already complete."
        if kind != "first" and not (self.setup_pending or self.factory_password):
            return ("Cannot upload backup config because device is already setup." if kind == "cfg"
                    else "Cannot create new username & password because they are already setup.")
        if form.get("username") != self.login_name() or form.get("password") != self.password:
            return "Invalid credentials"
        if kind == "cfg" and not backup:
            return "Please provide backup config for device setup."
        if kind == "first" and _cc(form["new_ccode"]) not in COUNTRIES:
            return "Invalid country code"
        if backup and kind in ("first", "cfg"):
            error = self.setup_backup_problem(backup)
            if error:
                return error
            new = self.backup_config(backup)
            if kind == "first":
                cc = str(_cc(form["new_ccode"]))
                new["radio.countrycode"] = new["radio.1.countrycode"] = cc
                new["gui.language"] = form.get("new_lang") or "en_US"
            with self.lock:
                self.setup_before_backup = (self.setup_pending, self.factory_password)
                self.system, self.backup_loaded = new, True
                self.setup_pending = self.factory_password = False
                self.events.append("first-setup-backup")
            return None
        if not (form.get("new_username") and form.get("new_password")):
            return "Please provide username and password for user setup."
        if kind == "first":
            self.change_country(_cc(form["new_ccode"]))
        self.user, self.password = form["new_username"], form["new_password"]
        self.pw_hash = sim_hash(self.password)
        for c in (self.saved, self.system, self.running):
            c["users.1.name"], c["users.1.password"] = self.user, self.pw_hash
            if "mac-telnetd.users.1.password" in c:
                c["mac-telnetd.users.1.password"] = self.pw_hash
            if kind == "first":
                c["gui.language"] = form.get("new_lang") or "en_US"
        self.setup_pending = self.factory_password = False
        self.soft_restart(0.5)
        self.events.append("first-setup")
        return None

    def change_country(self, cc: int) -> None:
        """airOS 8 POST /api/regdomain/country: change_country_in_cfg() (both country keys,
        and the mode of dual-band boards in "auto") and rc.softrestart save."""
        with self.lock:
            c = dict(self.system)
            c["radio.countrycode"] = c["radio.1.countrycode"] = str(cc)
            modes = int(BOARD[self.version].get("radio.1.ieee_modes") or 0)
            if modes & 5 and modes & 10 and (c.get("radio.1.ieee_mode") or "auto") == "auto":
                c["radio.1.ieee_mode"] = "11acvht80" if len(channels_of(cc)) >= 3 else "11nght40"
            self.system, self.running, self.saved = dict(c), dict(c), dict(c)
            self.test_until = None
            self.soft_restart()
            self.events.append("country")

    def tick(self) -> None:
        if self.test_until is not None and time.time() >= self.test_until:
            self.system = dict(self.saved)
            self.running = dict(self.saved)
            self.test_until = None
            self.modified = False
            self.soft_restart()
            self.events.append("test-expired")

    def soft_restart(self, factor: float = 1.0) -> None:
        self.down_until = time.time() + self.restart * factor
        self.ppp_up_at = self.down_until + 1.0
        if self.on_address:
            for ip, port in self.endpoints():
                self.on_address(ip, port)

    def _dhcp_on(self, dev: str) -> bool:
        c = self.running
        return c.get("dhcpc.status") == "enabled" and any(
            c.get(f"dhcpc.{m}.devname") == dev and c.get(f"dhcpc.{m}.status") == "enabled"
            for m in range(1, 9))

    def _mgmt_blocked(self, dev: str) -> bool:
        c = self.running
        return c.get("iptables.sys.mgmt.status") == "enabled" and any(
            c.get(f"iptables.sys.mgmt.{m}.devname") in (dev, "ppp+")
            and c.get(f"iptables.sys.mgmt.{m}.status") == "enabled" for m in range(1, 9))

    def iface_ip(self, n: str) -> str:
        """IP netconf.n has: the fixed one or, with DHCP, the one it "gets" (--dhcp-ip)."""
        c = self.running
        if self._dhcp_on(c.get(f"netconf.{n}.devname") or ""):
            return self.dhcp_ip
        return c.get(f"netconf.{n}.ip") or "0.0.0.0"

    def reachable_ips(self) -> list[str]:
        """IPs the device is reached at: the management one (bridge) or the LAN ones and,
        unless management is blocked, the WAN one (router and SOHO)."""
        c = self.running
        router = c.get("netmode") in ("router", "soho")
        first, then = [], []
        for k, role in sorted(c.items()):
            m = re.fullmatch(r"netconf\.(\d+)\.role", k)
            if not m or c.get(f"netconf.{m.group(1)}.status") != "enabled":
                continue
            n, dev = m.group(1), c.get(f"netconf.{m.group(1)}.devname") or ""
            if (router and role == "lan") or (not router and role == "mlan"):
                if not router and self._vlan_ports(dev) and not self._on_its_vlan(dev):
                    continue                      # (this computer does not reach that VLAN)
                first.append(self.iface_ip(n))
            elif router and role == "wan" and not self._mgmt_blocked(dev):
                then.append(self.iface_ip(n))
        return [ip for ip in first + then if ip and ip != "0.0.0.0"]

    def endpoints(self) -> list[tuple[str, int]]:
        """Where its web answers: the IPs it is reached at (or its fixed one) and its port
        (with web_follow, the one of its configuration: httpd.port)."""
        ip, port = self.at
        if self.web_follow:
            port = int(re.match(r"\d*", self.running.get("httpd.port") or "").group() or port)
        return [(addr, port) for addr in (self.reachable_ips() if self.ip_follow else [ip])]

    def _vlan_ports(self, dev: str) -> list[str]:
        """The ports of the bridge dev if it is made only of VLANs (the management VLAN of the
        airOS 8 web: eth0.10, ath0.10…), or []."""
        c = self.running
        b = next((m.group(1) for k, v in c.items()
                  if v == dev and (m := re.fullmatch(r"bridge\.(\d+)\.devname", k))), None)
        ports = [v for k, v in c.items()
                 if b and re.fullmatch(rf"bridge\.{b}\.port\.\d+\.devname", k)]
        return ports if ports and all("." in p for p in ports) else []

    def _on_its_vlan(self, dev: str) -> bool:
        """Does this computer reach the management on its VLAN? It has to be on that VLAN
        (on_vlan) and the cable's port has to leave its tagged traffic to it, not to br0: the
        ebtables.sys.vlan entry the web writes for it."""
        c = self.running
        if not self.on_vlan or any(c.get(k) != "enabled" for k in (
                "ebtables.status", "ebtables.sys.status", "ebtables.sys.vlan.status")):
            return False
        rules = {(c.get(f"ebtables.sys.vlan.{m.group(1)}.devname"),
                  c.get(f"ebtables.sys.vlan.{m.group(1)}.id"))
                 for k in c if (m := re.fullmatch(r"ebtables\.sys\.vlan\.(\d+)\.status", k))
                 and c.get(k) == "enabled"}
        return any(tuple(p.split(".", 1)) in rules for p in self._vlan_ports(dev)
                   if p.startswith("eth"))

    # -- what its main page's device details show (airOS 8) --

    def _rules(self, table: str) -> list[str]:
        c = self.running
        idx = sorted({int(m.group(1)) for k in c
                      if (m := re.fullmatch(rf"{table}\.(\d+)\.cmd", k))})
        return [c[f"{table}.{n}.cmd"] for n in idx if c.get(f"{table}.{n}.status") != "disabled"]

    @staticmethod
    def _rule_fields(cmd: str) -> dict:
        """The options of a rule's command line (iptables or ebtables): {option: (value,
        negated)}, with "!" before the option or after it."""
        out, neg, opt = {}, False, None
        for word in cmd.split():
            if word == "!":
                neg = True
            elif word.startswith("-") and opt is None:
                opt = word
            elif opt is not None:
                out[opt], neg, opt = (word, neg), False, None
        return out

    @staticmethod
    def _ipt_row(target: str, proto: str, iface_in: str, iface_out: str, src: str, dst: str,
                 match: str = "") -> str:
        """A rule as iptables -L -v -n lists it, with the columns of a real 8.7.14's (an
        address with "!" in front if negated, without /32)."""
        def addr(a: str) -> str:
            neg, a = a.startswith("!"), a.lstrip("!") or "0.0.0.0/0"
            return ("!" if neg else " ") + f"{a[:-3] if a.endswith('/32') else a:<20}"
        row = (f"{0:>5} {0:>5} {target:<11}{proto:<5}--  {iface_in or '*':<7}"
               f"{iface_out or '*':<7}{addr(src)}{addr(dst)}")
        return row + (f" {match}" if match else "")

    @staticmethod
    def _ipt_ports(proto: str, sport, dport) -> str:
        """Its ports as iptables lists them: tcp spt:67 dpt:68, dpts:1000:2000, dpt:!53."""
        parts = [f"{name}{'s' if ':' in value else ''}:{'!' if neg else ''}{value}"
                 for name, (value, neg) in (("spt", sport), ("dpt", dport)) if value]
        return f"{proto} {' '.join(parts)}" if parts else ""

    def firewall_listing(self) -> str:
        """/api/firewall/user-rules, as a real 8.7.14 and 8.7.25 list it: in bridge mode the
        ebtables chain FIREWALL (the jump to FIREWALL6 first if the IPv6 firewall is on, its
        rules in the order ebtables writes their options); in router mode the iptables chain
        FIREWALL. P2P rules are missing: the device keeps them but does not apply them."""
        c = self.running
        fw = c.get("ebtables.sys.fw.status" if (c.get("netmode") or "bridge") == "bridge"
                   else "iptables.sys.fw.status") == "enabled"
        if (c.get("netmode") or "bridge") == "bridge":
            rules = [r for r in self._rules("ebtables") if "FIREWALL6" not in r
                     and "p2p" not in r.split()] if fw else []
            entries = (["-p IPv6 -j FIREWALL6, pcnt = 0 -- bcnt = 0"]
                       if c.get("ebtables.sys.fw6.status") == "enabled" else [])
            entries += [self._ebtables_listed(r) + " , pcnt = 0 -- bcnt = 0" for r in rules]
            return "\n".join(["Bridge table: filter", "",
                              f"Bridge chain: FIREWALL, entries: {len(entries)}, policy: RETURN"]
                             + entries) + "\n"
        rows = []
        for cmd in self._rules("iptables") if fw else []:
            if "FIREWALL6" in cmd or "ipp2p" in cmd:
                continue
            f = self._rule_fields(cmd)
            proto = _IPT_PROTOS.get(f.get("--protocol", ("ip",))[0], f.get("--protocol")[0])
            src, dst = f.get("--src", ("", False)), f.get("--dst", ("", False))
            rows.append(self._ipt_row(
                f.get("-j", ("DROP",))[0], proto, f.get("-i", ("",))[0], f.get("-o", ("",))[0],
                ("!" if src[1] else "") + src[0], ("!" if dst[1] else "") + dst[0],
                self._ipt_ports(proto, f.get("--sport", ("", False)), f.get("--dport", ("", False)))
                if proto in ("tcp", "udp") else ""))
        return "\n".join(["Chain FIREWALL (2 references)", _IPT_HEADER] + rows) + "\n"

    @classmethod
    def _ebtables_listed(cls, cmd: str) -> str:
        """A rule's command line as ebtables -L lists it (as a real 8.7.14 does): -p by name,
        the interfaces, the addresses, the protocol by name and then the ports."""
        f = cls._rule_fields(cmd)
        ether = f.get("-p", ("",))[0]
        pre = "--ip6-" if ether == "0x86dd" else "--ip-"
        out = [f"-p {_EB_PROTOS.get(ether, ether)}"]
        out += [f"{flag} {f[flag][0]}" for flag in ("-i", "-o") if flag in f]
        out += [f"{pre}{n} {'! ' if f[pre + n][1] else ''}{f[pre + n][0]}"
                for n in ("src", "dst") if pre + n in f]
        if pre + "protocol" in f:
            value = f[pre + "protocol"][0]
            out.append(f"{pre}proto {_IP_PROTOS.get(value, value)}")
        out += [f"{pre}{n} {'! ' if f[pre + n][1] else ''}{f[pre + n][0]}"
                for n in ("sport", "dport") if pre + n in f]
        return " ".join(out + [f"-j {f.get('-j', ('DROP',))[0]}"])

    def portfw_listing(self) -> str:
        """/api/firewall/port-forward: its notice while it is off (as a real 8.7.25's, in its
        language); on, its NAT rules as a real 8.7.14 lists them: a DNAT for each rule and,
        for those open to any source, a SNAT to the LAN's address."""
        c = self.running
        if c.get("iptables.sys.portfw.status") != "enabled":
            return PORTFW_OFF.get(self.ui_lang, PORTFW_OFF["en"])
        lan_ip = next((c.get(f"netconf.{m.group(1)}.ip") for k, v in sorted(c.items())
                       if v == "lan" and (m := re.fullmatch(r"netconf\.(\d+)\.role", k))), "")
        dnat, snat = [], []
        for n in sorted({int(m.group(1)) for k in c
                         if (m := re.fullmatch(r"iptables\.sys\.portfw\.(\d+)\.host", k))}):
            p = f"iptables.sys.portfw.{n}."
            if c.get(p + "status") == "disabled":
                continue
            proto, host = c.get(p + "proto") or "tcp", c.get(p + "host", "")
            port = c.get(p + "port")
            src, dev = c.get(p + "src") or "0.0.0.0/0", c.get(p + "devname", "")
            dnat.append(self._ipt_row(
                "DNAT", proto, dev, "", src, c.get(p + "dst") or "0.0.0.0/0",
                f"{self._ipt_ports(proto, ('', False), (c.get(p + 'dport', ''), False))} "
                f"to:{host}" + (f":{port}" if port else "")))
            if src == "0.0.0.0/0":
                ports = self._ipt_ports(proto, ("", False), (port, False)) if port else ""
                snat.append(self._ipt_row("SNAT", proto, "", dev, "", host,
                                          (f"{ports} " if ports else "") + f"to:{lan_ip}"))
        return ("\n".join(["Chain PREROUTING (1 references)", _IPT_HEADER] + dnat) + "\n\n\n"
                + "\n".join(["Chain POSTROUTING (1 references)", _IPT_HEADER] + snat) + "\n")

    def _dhcpd_entries(self) -> list[str]:
        c = self.running
        if c.get("dhcpd.status") == "disabled":
            return []
        return [m.group(1) for k in sorted(c) if (m := re.fullmatch(r"dhcpd\.(\d+)\.devname", k))
                and c.get(f"dhcpd.{m.group(1)}.status") != "disabled"]

    def leases(self) -> dict:
        """/api/dhcp-server/leases: {"ok": true} with no DHCP server (as a real 8.7.25 in
        bridge mode); with one, its leases (LEASES, in its range)."""
        entries = self._dhcpd_entries()
        if not entries or (self.running.get("netmode") or "bridge") == "bridge":
            return {"ok": True}
        start = self.running.get(f"dhcpd.{entries[0]}.start") or "192.168.1.2"
        net = start.rsplit(".", 1)[0]
        return {"ok": True, "aaData": [[mac, f"{net}.{host}", left, name]
                                       for mac, host, left, name in LEASES]}

    def airview_state(self) -> int:
        """Its services.airview, as a real 8.7.14: 0 disabled, 3 while the management radio
        has it (then its data comes empty), 2 running."""
        if self.running.get("airview.status") == "disabled":
            return 0
        if self.provmode_until and time.time() < self.provmode_until:
            return 3
        return 2

    def airview_data(self) -> bytes:
        """/airviewdata.cgi: while airView runs, a new read every airview_period seconds;
        disabled, the last one again, as a real one gives it."""
        now = time.time()
        if self.airview_state() == 2 and now - self.airview_at >= self.airview_period:
            self.airview_frame, self.airview_at, self.airview_cache = (
                self.airview_frame + 1, now, None)
        if self.airview_cache is None:
            c = self.running
            self.airview_cache = json.dumps(airview_payload(
                self.airview_frame, int(c.get("radio.1.freq") or 0) or 5745,
                int(c.get("radio.1.chanbw") or 20))).encode()
        return self.airview_cache

    def _services(self) -> dict:
        """status.cgi's services: which daemons run (dhcpc, dhcpd, pppoe…)."""
        c = self.running
        router = (c.get("netmode") or "bridge") != "bridge"
        dhcpc = c.get("dhcpc.status") == "enabled" and any(
            c.get(f"dhcpc.{m}.status") == "enabled" for m in range(1, 9))
        return {"dhcpc": dhcpc, "dhcpd": router and bool(self._dhcpd_entries()),
                "dhcp6d_stateful": False, "pppoe": c.get("ppp.status") == "enabled",
                "airview": self.airview_state()}

    def _firewall(self) -> dict:
        """status.cgi's firewall: which of its tables have the firewall on."""
        c = self.running
        bridge = (c.get("netmode") or "bridge") == "bridge"
        return {"iptables": not bridge and c.get("iptables.sys.fw.status") == "enabled",
                "ebtables": bridge and c.get("ebtables.sys.fw.status") == "enabled",
                "ip6tables": not bridge and c.get("ip6tables.sys.fw6.status") == "enabled",
                "eb6tables": bridge and c.get("ebtables.sys.fw6.status") == "enabled"}

    def _polling(self) -> dict:
        """wireless.polling of status.cgi (airOS 8): its capacity and its TDD framing, as its
        web reads them (fixed_frame; flex_mode only in PtMP, as a real PtP has none)."""
        c = self.running
        flex = int(c.get("radio.1.polling_ff_flex") or 0)
        dur = int(c.get("radio.1.polling_ff_dur") or 0)
        out = {"dl_capacity": 210000, "ul_capacity": 190000, "fixed_frame": bool(dur and not flex),
               "gps_sync": False, "ff_cap_rep": c.get("radio.1.ff_cap_rep") == "1"}
        if c.get("radio.1.ptpmode") == "0":
            out["flex_mode"] = flex
        if dur and not flex:
            out["ff_frame_dur"] = dur // 1000
        return out

    def interfaces(self) -> list[dict]:
        c, out = self.running, []
        for k, dev in sorted(c.items()):
            m = re.fullmatch(r"netconf\.(\d+)\.devname", k)
            if not m:
                continue
            ip = self.iface_ip(m.group(1))
            status = {"plugged": 1}
            if self.version == 8:            # airOS 6 does not report the IP in status.cgi
                status["ipaddr"] = ip
                if dev.startswith("ath"):    # (what its stations send and get, as they grow)
                    up = max(0.0, time.time() - self.boot)
                    status.update(rx_bytes=int(up * 250_000), tx_bytes=int(up * 500_000))
            out.append({"ifname": dev, "hwaddr": "68:D7:9A:98:FB:FF", "enabled": True,
                        "status": status})
        ppp = self.pppoe_state()
        if "error" not in ppp:
            status = {"plugged": 1, "tx_bytes": 15628, "rx_bytes": 10677}
            if self.version == 8:
                status["ipaddr"] = ppp["iplocal"]
            out.append({"ifname": "ppp0", "hwaddr": "00:00:00:00:00:00", "enabled": True,
                        "status": status})
        return out

    def discovery_reply(self) -> bytes:
        mac = bytes.fromhex(MAC)
        ips = self.reachable_ips() or ["0.0.0.0"]
        c = self.running
        name = c.get("resolv.host.1.name") or "airOS"
        v8 = self.version == 8
        model, fw = MODEL[self.version], FIRMWARE[self.version]
        uptime = int(time.time() - self.boot) + 86400 * 3
        ap = (c.get("radio.1.mode") or "").lower() == "master"
        tlvs = [(0x02, mac + bytes(int(x) for x in ip.split("."))) for ip in ips]
        tlvs += [(0x01, mac), (0x0A, uptime.to_bytes(4, "big")), (0x0B, name.encode()),
                 (0x0C, b"LBE-5AC-Gen2" if v8 else b"NSM5"),
                 (0x0D, (c.get("wireless.1.ssid") or "").encode()),
                 (0x0E, bytes([3 if ap else 2])), (0x14, model.encode()), (0x03, fw.encode())]
        body = b"".join(bytes([t]) + len(v).to_bytes(2, "big") + v for t, v in tlvs)
        return b"\x01\x00" + len(body).to_bytes(2, "big") + body

    def announcements(self) -> list[bytes]:
        """The CDP and LLDP frames the device would send now, according to its configuration:
        CDP if discovery.cdp.status is enabled; LLDP only on airOS 8, on unless
        discovery.lldp.status says otherwise. (Their content is plausible, not copied from
        the firmware.)"""
        c, mac = self.running, bytes.fromhex(MAC)
        name = c.get("resolv.host.1.name") or "airOS"
        ips, fw, model = self.reachable_ips(), FIRMWARE[self.version], MODEL[self.version]
        out = []
        if c.get("discovery.cdp.status") == "enabled":
            out.append(cdp_frame(mac, name, ips, "eth0", fw, model))
        lldp = c.get("discovery.lldp.status")
        if self.version == 8 and (lldp is None or lldp.lower() in LLDP_TRUE):
            out.append(lldp_frame(mac, mac, "eth0", name, f"{model} {fw}", ips))
        return out

    def effective_power(self) -> int:
        """Power the radio "transmits" with: capped by the hardware and the country."""
        c, b = self.running, BOARD[self.version]
        lo, hi = int(b["radio.1.txpower.min"]), int(b["radio.1.txpower.max"])
        power = min(int(c.get("radio.1.txpower") or 0), hi)
        obey = c.get("radio.1.reg_obey") == "enabled" and (
            self.version == 8 or c.get("radio.1.obey", "enabled") == "enabled")
        if obey:
            bw = int(c.get("radio.1.chanbw") or 20)
            freq = int(c.get("radio.1.freq") or 0)
            chans = channels_of(c.get("radio.countrycode") or 484)
            eirps = ([e for f, _, w, e in chans if w == bw and f == freq]
                     or [e for _, _, w, e in chans if w == bw])
            if eirps:
                power = min(power, max(eirps) - int(c.get("radio.1.antenna.gain") or 0)
                            + int(c.get("radio.1.cable.loss") or 0))
        return max(lo, power)

    # -- status.cgi -----------------------------------------------------------

    def status(self) -> dict:
        c = self.running
        ap = c.get("radio.1.mode", "").lower() == "master"
        up = int(time.time() - self.boot) + 86400 * 3
        name = c.get("resolv.host.1.name") or "airOS"
        if self.version == 8:
            return {
                "host": {"hostname": name, "devmodel": "LiteBeam 5AC (simulated)",
                         "fwversion": self.fw_version, "netrole": c.get("netmode") or "bridge",
                         "uptime": up, "cpuload": 12.5, "totalram": 63447040,
                         "freeram": 19419136, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                         # (as a real 8.7.25: a millisecond counter that wraps around, and
                         # the height of its System page's Location)
                         "timestamp": int(time.time() * 1000) & 0xFFFFFFFF,
                         "height": float(c["system.height"]) if c.get("system.height")
                         else None},
                # (as a real one: when NTP last set its clock, if it is on)
                "ntpclient": ({"last_sync": time.strftime("%Y-%m-%d %H:%M:%S",
                                                          time.localtime(self.boot + 5))}
                              if c.get("ntpclient.status") == "enabled" else {}),
                # (as a real 8.7.25: what its main page's device details offer)
                "firewall": self._firewall(),
                "portfw": ((c.get("netmode") or "bridge") != "bridge"
                           and c.get("iptables.sys.portfw.status") == "enabled"),
                "services": self._services(),
                "unms": ({"status": 2, "timestamp": "2026-11-08 10:00:00"}
                         if self.unms_state == 2 else {"status": self.unms_state}),
                # (as a real 8.7.14: nothing while the management radio is off)
                "provmode": ({"status": 2, "timeout": int(self.provmode_until - time.time())}
                             if self.provmode_until and time.time() < self.provmode_until
                             else {}),
                "wireless": {
                    "essid": c.get("wireless.1.ssid"),
                    "mode": ("ap-" if ap else "sta-")
                    + ("ptmp" if c.get("radio.1.ptpmode") == "0" else "ptp"),
                    "hide_essid": int(c.get("wireless.1.hide_ssid") == "enabled"),
                    "frequency": int(c.get("radio.1.freq") or 0),
                    "center1_freq": int(c.get("radio.1.center.1.freq")
                                        or c.get("radio.1.freq") or 0),
                    "chanbw": int(c.get("radio.1.chanbw") or 20),
                    "ieeemode": (c.get("radio.1.ieee_mode") or "11acvht20").upper(),
                    "txpower": self.effective_power(), "noisef": -92,
                    "security": "WPA2", "distance": 1200, "rx_chainmask": 3,
                    "count": len(self.v8_stations()),
                    "polling": self._polling(),
                    "sta": self.v8_stations()},
                "interfaces": self.interfaces(),
            }
        return {
            "host": {"hostname": name, "devmodel": "NanoStation M5 (simulated) ",
                     "fwversion": "v6.3.16", "fwprefix": "XW",
                     "netrole": c.get("netmode") or "bridge", "uptime": up, "cpuload": 100.0,
                     "cputotal": 195292633, "cpubusy": 11092563, "totalram": 63627264,
                     "freeram": 42516480},
            "wireless": {"mode": "ap" if ap else "sta", "essid": c.get("wireless.1.ssid"),
                         "channel": 149, "frequency": f"{c.get('radio.1.freq')} MHz",
                         "opmode": (c.get("radio.1.ieee_mode") or "11naht20").upper(),
                         "signal": -61, "rssi": 35, "noisef": -96, "rx_chainmask": 3,
                         "chainrssi": [33, 31, 0],
                         "txpower": self.effective_power(), "distance": 750,
                         "ccq": 987, "txrate": "130", "rxrate": "117", "security": "WPA2",
                         "count": 1, "chanbw": int(c.get("radio.1.chanbw") or 20),
                         "polling": {"quality": 86, "capacity": 62}},
            "interfaces": self.interfaces(),
        }
