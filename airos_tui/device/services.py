"""Services: device discovery, CDP and LLDP, the ping watchdog and (airOS 8) the rest of its
web's Services page, SNMP, the web server, SSH with its authorized keys, Telnet and the system
log, as that page saves them; and UISP (airOS 8), as the switch in its web's header."""
from __future__ import annotations

import html
import re

from ..core.i18n import N_, tr
from ..core.config import (_cfg_def, Config, _find, gen3, _int, js_parse_int, _merge, _model_bool,
                           port_kind, _v8_on, WEB_IPV4_RE)
from .network import main_iface, net_mode
from .limits import SYSTEM_PORTS
from .netmode import nat_devname, restore_network


def services_offered(flavor: str | None, board: dict) -> bool:
    """airOS 8 does not offer discovery and CDP on airMAX Gen3 boards (feature.g3=1)."""
    return flavor == "v6" or not gen3(board)


# Both webs always save the two keys together (airOS 6: services.cgi; airOS 8: its
# DiscoveryModel). When saving, they also regenerate the management ports the DMZ keeps
# for the device, which include UDP 10001 only while discovery is on (airOS 6:
# cfg_update_dmz_mgmt(); airOS 8: the WAN model of the network page, which every save
# runs).

def discovery_settings(cfg: Config, flavor: str | None) -> tuple[bool, bool]:
    """(Ubiquiti discovery, CDP) as the Services page shows them. With the key missing,
    discovery counts as on in both webs, and CDP as off on airOS 8 and as
    wireless.1.addmtikie on airOS 6."""
    disc, cdp = cfg.get("discovery.status"), cfg.get("discovery.cdp.status")
    if flavor == "v6":
        return ((disc or "enabled") == "enabled",
                (cdp or cfg.get("wireless.1.addmtikie") or "disabled") == "enabled")

    def flag(value, default):     # DiscoveryModel: a missing key keeps the model's default
        return default if value is None else value in ("enabled", "true", "1")
    return flag(disc, True), flag(cdp, False)


def dmz_mgmt_exceptions(cfg: Config, flavor: str | None) -> dict[str, str | None]:
    """The device's management ports that the DMZ does not forward (UDP 10001 only with
    discovery on), regenerated the way each web does when saving. Nothing when there is
    no DMZ or it forwards those ports too."""
    return _dmz_mgmt_v6(cfg) if flavor == "v6" else _dmz_mgmt_v8(cfg)


def _dmz_mgmt_v6(cfg: Config) -> dict[str, str | None]:
    """cfg_update_dmz_mgmt() in lib/link.inc."""
    if net_mode(cfg) == "bridge" or any(
            _cfg_def(cfg, k, "disabled") != "enabled"
            for k in ("iptables.status", "iptables.sys.status", "iptables.sys.dmz.status",
                      "iptables.sys.dmz.1.except.status")):
        return {}
    ports = [(_cfg_def(cfg, "httpd.port", "80"), "TCP"),
             (_cfg_def(cfg, "airview.tcp_port", "18888"), "TCP"), ("8", "ICMP"), ("9", "UDP")]
    for status, default, port_key, port in (("sshd.status", "enabled", "sshd.port", "22"),
                                            ("httpd.https.status", "disabled",
                                             "httpd.https.port", "443"),
                                            ("telnetd.status", "disabled", "telnetd.port",
                                             "23")):
        if _cfg_def(cfg, status, default) == "enabled":
            ports.append((_cfg_def(cfg, port_key, port), "TCP"))
    if _cfg_def(cfg, "snmp.status", "disabled") == "enabled":
        ports.append(("161", "UDP"))
    if _cfg_def(cfg, "discovery.status", "enabled") == "enabled":
        ports.append(("10001", "UDP"))
    pre = "iptables.sys.dmz.1.except"
    up: dict[str, str | None] = {}
    for i, (port, proto) in enumerate(ports, 1):
        up.update({f"{pre}.{i}.port": port, f"{pre}.{i}.proto": proto,
                   f"{pre}.{i}.status": "enabled"})
    for i in range(len(ports) + 1, 9):            # the rest of the list, up to 8, off
        if _cfg_def(cfg, f"{pre}.{i}.status", "disabled") == "enabled":
            up[f"{pre}.{i}.status"] = "disabled"
    return up


def _dmz_mgmt_v8(cfg: Config) -> dict[str, str | None]:
    """The WAN's DMZ exception list as airOS 8 rebuilds it (iptSysLoad and the WAN model's
    save): the old entries go away and the new ones are numbered from 1."""
    wan = main_iface(cfg, "v8") if net_mode(cfg) != "bridge" else None
    if not wan or not _v8_on(cfg, "iptables.sys.dmz.status"):
        return {}
    k = _find(cfg, "iptables.sys.dmz", devname=nat_devname(cfg, wan))
    if k is None or not _v8_on(cfg, f"iptables.sys.dmz.{k}.status") \
            or not _v8_on(cfg, f"iptables.sys.dmz.{k}.except.status"):
        return {}
    ports = [("8", "ICMP"), ("9", "UDP")]
    for section, port_key, port in (("httpd", "httpd.port", "80"), ("sshd", "sshd.port", "22"),
                                    ("httpd.https", "httpd.https.port", "443"),
                                    ("telnetd", "telnetd.port", "23")):
        if _v8_on(cfg, f"{section}.status"):
            ports.append((cfg.get(port_key) or port, "TCP"))
    if _v8_on(cfg, "snmp.status"):
        ports.append(("161", "UDP"))
    if _v8_on(cfg, "discovery.status"):
        ports.append(("10001", "UDP"))
    pre = f"iptables.sys.dmz.{k}.except"
    entry = re.compile(rf"^{re.escape(pre)}\.\d+\.")
    up: dict[str, str | None] = {key: None for key in cfg.keys() if entry.match(key)}
    for i, (port, proto) in enumerate(ports, 1):
        up.update({f"{pre}.{i}.status": "enabled", f"{pre}.{i}.port": port,
                   f"{pre}.{i}.proto": proto})
    return up


# LLDP is not in either web: on airOS 8 (from 8.6; checked in 8.6.2, 8.7.0 and 8.7.19, not in
# 8.5.7 or airOS 6) udapi-bridge reads discovery.lldp.status from /tmp/system.cfg with
# kvfile_get_bool(), and its default, when the key is missing, is on.
LLDP_TRUE = frozenset(("on", "enabled", "1", "enable", "active", "true"))   # strcasecmp


def lldp_offered(flavor: str | None, fw: str, cfg: Config) -> bool:
    """Does this firmware handle LLDP? airOS 8.6 or later (by the key if the version is
    unknown)."""
    if flavor == "v6":
        return False
    m = re.match(r"v?(\d+)\.(\d+)", fw or "")
    if m:
        return (int(m.group(1)), int(m.group(2))) >= (8, 6)
    return "discovery.lldp.status" in cfg


def lldp_enabled(cfg: Config) -> bool:
    """LLDP as udapi-bridge reads it: on unless the key says otherwise."""
    value = cfg.get("discovery.lldp.status")
    return value is None or value.lower() in LLDP_TRUE


# The services whose ports (and whether they are on) are in the DMZ's management list.
_DMZ_SERVICES = ("httpd", "sshd", "telnetd", "snmp", "discovery", "airview")


def network_back(cfg: Config, original: Config, flavor: str | None) -> Config:
    """restore_network, with the DMZ's management ports following the services as they are
    prepared (when they changed: the device's list is the one of its own services)."""
    new = restore_network(cfg, original, flavor)
    if any(k.split(".", 1)[0] in _DMZ_SERVICES for k, _, _ in original.diff(cfg)):
        _merge(new, dmz_mgmt_exceptions(new, flavor))
    return new


def plan_discovery(cfg: Config, flavor: str | None, discovery: bool,
                   cdp: bool) -> dict[str, str | None]:
    """What saving the Services page writes for these settings: both keys, and the DMZ
    management ports regenerated with the new discovery state (None = delete)."""
    up: dict[str, str | None] = {"discovery.status": "enabled" if discovery else "disabled",
                                 "discovery.cdp.status": "enabled" if cdp else "disabled"}
    up.update(dmz_mgmt_exceptions(_merge(cfg.copy(), up), flavor))
    return up


# -- UISP (airOS 8: the UnmsModel of its web and the switch in its header) -----------------

UNMS_KEY_MIN = 55
# The web's "unmsKey" pattern (wss://controller…), whole.
_UNMS_KEY_RE = re.compile(r"(?:wss?://)[\w.-]+(?:\.[\w.-]+)+[\w\-._~:/?#\[\]@!$&'()*+,;=.]+",
                          re.ASCII)


def unms_key(cfg: Config) -> str:
    """The UISP key the device has (unms.uri), or "": "-" means none, as in its web."""
    key = cfg.get("unms.uri") or ""
    return "" if key == "-" else key


def unms_on(cfg: Config) -> bool:
    """UISP management as its web shows it: on only with a key."""
    return bool(unms_key(cfg)) and cfg.get("unms.status") == "enabled"


def unms_key_shown(key: str) -> str:
    """The key as its web shows it: the controller, without what follows "+" (its secret)."""
    return html.unescape(key.split("+", 1)[0])


def unms_key_problem(key: str) -> str | None:
    """What the web says of a UISP key, or None: required, at least 55 characters and the
    form wss://controller…"""
    if not key.strip():
        return tr("UISP Key is required")
    if len(key.encode("utf-16-le")) // 2 < UNMS_KEY_MIN:      # (as JavaScript counts)
        return tr("UISP Key must be at least {count} characters", count=UNMS_KEY_MIN)
    if not _UNMS_KEY_RE.fullmatch(key):
        return tr("UISP Key is invalid")
    return None


def unms_state(status: dict) -> str:
    """The connection to UISP, as its web's indicator says it (status.cgi: unms.status)."""
    unms = status.get("unms") if isinstance(status.get("unms"), dict) else {}
    code = _int(unms.get("status")) or 0
    if code == 2:
        text = tr("Connected")
        stamp = unms.get("timestamp")
        return f"{text} ({stamp})" if stamp not in (None, "", "-") else text
    return tr("Connecting...") if code == 1 else tr("UISP not configured")


# -- Ping Watchdog (airOS 8: the PingWatchdogModel of its Services page) -----------------

PWDOG_SUPPORT = "/bin/support /tmp/emerg /etc/persistent/emerg.supp emerg 0; reboot -f"
PWDOG_DELAY = (20, 65535)           # Startup Delay, seconds
PWDOG_FAILURES = (3, 15)            # Failure Count To Reboot


def watchdog_settings(cfg: Config) -> dict:
    """The watchdog as its web reads it (its model's load): a missing key keeps the model's
    default (off, no address, 300 s, 300 s and 3 failures, without the support info)."""
    def num(key, default):
        value = cfg.get(key)
        if value is None:
            return default
        n = js_parse_int(value)
        return value if n is None else n        # (not a number: as it is, to be seen)
    return {"on": cfg.get("pwdog.status") in ("enabled", "true", "1"),
            "host": cfg.get("pwdog.host") or "",
            "interval": num("pwdog.period", 300), "delay": num("pwdog.delay", 300),
            "failures": num("pwdog.retry", 3), "support": bool(cfg.get("pwdog.command"))}


def _whole(text: str, low: int, high: int) -> bool:
    return bool(re.fullmatch(r"-?[0-9]+", text)) and low <= int(text) <= high


def watchdog_problem(field: str, value) -> str | None:
    """What the web says of a value of the watchdog, or None. Its rules only apply while
    it is on."""
    text = str(value).strip()
    if field == "host":
        if not text:
            return tr("IP Address to Ping is required")
        return None if WEB_IPV4_RE.fullmatch(text) else tr("IP Address to Ping is invalid")
    if field == "interval":           # (its own message, for all its rules)
        if re.fullmatch(r"[0-9]+", text) and int(text) >= 1:
            return None
        return tr("Ping Interval must be an integer greater than 0")
    if field == "delay":
        if not text:
            return tr("Startup Delay is required")
        low, high = PWDOG_DELAY
        return None if _whole(text, low, high) else tr(
            "Startup Delay must be between {low} and {high}", low=low, high=high)
    if not text:
        return tr("Failure Count To Reboot is required")
    low, high = PWDOG_FAILURES
    return None if _whole(text, low, high) else tr(
        "Failure Count To Reboot must be between {low} and {high}", low=low, high=high)


def plan_watchdog(ws: dict) -> dict[str, str | None]:
    """The keys its web saves for the watchdog (None = delete): all of them, the command
    only with "Save Support Info" and, as its configuration leaves empty values out, the
    address only when there is one."""
    def kept(value):
        return None if value in (None, "") else str(value)
    return {"pwdog.status": "enabled" if ws["on"] else "disabled",
            "pwdog.host": kept(ws["host"]), "pwdog.period": kept(ws["interval"]),
            "pwdog.delay": kept(ws["delay"]), "pwdog.retry": kept(ws["failures"]),
            "pwdog.command": PWDOG_SUPPORT if ws["support"] else None}


# -- the rest of its Services page (airOS 8): SNMP, the web server, SSH, Telnet, the log ----
#
# As its models read them: a missing key keeps the model's default (SSH and the system log
# on, SNMP and Telnet off, HTTPS on port 443, HTTP on 80); on saving, a section writes all
# its fields (an empty value is left out). Gen3 boards have no SNMP or Telnet there.

MAX_SSH_KEYS = 10                         # its max_sshd_auth_keys
SSH_KEY_FILE_MAX = 1048576                # its key dialog's _keyMaxSize
_SSH_KEY_RE = re.compile(r"^(ssh-(?:rsa|dsa))\s+([A-Za-z0-9+/=]+)\s*(\S*)$")   # its _keyPattern
# Its portSys messages, by label: required, range, invalid range, the system's, another's.
_PORT_MSGS = {
    "port": (N_("Server Port is required"), N_("Server Port must be between {low} and {high}"),
             N_("Server Port is invalid"), N_("Server Port is used by system"),
             N_("Server Port is used by another service")),
    "https_port": (N_("Secure Server Port is required"),
                   N_("Secure Server Port must be between {low} and {high}"),
                   N_("Secure Server Port is invalid"), N_("Secure Server Port is used by system"),
                   N_("Secure Server Port is used by another service")),
}


def _model_port(cfg: Config, key: str, default: int) -> str:
    n = js_parse_int(cfg.get(key)) if cfg.get(key) is not None else None
    return str(default if n is None else n)


def access_offered(flavor: str | None, board: dict) -> dict[str, bool]:
    """Which of them its web's Services page has: all on airOS 8 but SNMP and Telnet on Gen3
    boards; none of them here for airOS 6 (another web)."""
    v8 = flavor == "v8"
    return {"snmp": v8 and not gen3(board), "web": v8, "ssh": v8,
            "telnet": v8 and not gen3(board), "syslog": v8}


def snmp_settings(cfg: Config) -> dict:
    community = cfg.get("snmp.community")
    return {"on": _model_bool(cfg, "snmp.status", False),
            "community": "public" if community is None else community,
            "contact": cfg.get("snmp.contact") or "", "location": cfg.get("snmp.location") or ""}


def snmp_problem(field: str, value) -> str | None:
    """Its rules while the agent is on: the three fields required."""
    if str(value).strip():
        return None
    return {"community": tr("SNMP Community is required"),
            "contact": tr("SNMP Contact is required"),
            "location": tr("SNMP Location is required")}.get(field)


def plan_snmp(s: dict) -> dict[str, str | None]:
    return {"snmp.status": "enabled" if s["on"] else "disabled",
            "snmp.community": s["community"] or None, "snmp.contact": s["contact"] or None,
            "snmp.location": s["location"] or None}


def web_settings(cfg: Config) -> dict:
    """Its web server: HTTPS on or off and both ports (its session timeout is kept: the
    page does not show it)."""
    return {"https": _model_bool(cfg, "httpd.https.status", True),
            "https_port": _model_port(cfg, "httpd.https.port", 443),
            "http_port": _model_port(cfg, "httpd.port", 80)}


def plan_web(ws: dict) -> dict[str, str | None]:
    return {"httpd.status": "enabled", "httpd.https.status": "enabled" if ws["https"]
            else "disabled", "httpd.https.port": ws["https_port"], "httpd.port": ws["http_port"]}


def web_endpoint(ws: dict) -> tuple[str, int]:
    """Where its web answers with these settings: over HTTPS on its port while it is on (the
    HTTP port sends there), over HTTP otherwise."""
    if ws["https"]:
        return "https", int(ws["https_port"])
    return "http", int(ws["http_port"])


def ssh_settings(cfg: Config) -> dict:
    return {"on": _model_bool(cfg, "sshd.status", True),
            "port": _model_port(cfg, "sshd.port", 22),
            "passwd": _model_bool(cfg, "sshd.auth.passwd", True)}


def plan_ssh(s: dict) -> dict[str, str | None]:
    return {"sshd.status": "enabled" if s["on"] else "disabled", "sshd.port": s["port"],
            "sshd.auth.passwd": "enabled" if s["passwd"] else "disabled"}


def ssh_keys(cfg: Config) -> list[dict]:
    """Its authorized keys, sshd.auth.key.1 to 10, the ones with a value (its model reads up
    to the 9th only, so its web would lose a 10th one when saving: here it is kept)."""
    out = []
    for n in range(1, MAX_SSH_KEYS + 1):
        p = f"sshd.auth.key.{n}."
        if cfg.get(p + "value"):
            out.append({"value": cfg.get(p + "value"), "type": cfg.get(p + "type") or "",
                        "comment": cfg.get(p + "comment") or "",
                        "on": (cfg.get(p + "status") or "enabled") == "enabled"})
    return out


def parse_ssh_keys(text: str) -> list[dict] | None:
    """The public keys of a file, as its dialog reads them: one per line, ssh-rsa (or
    ssh-dsa), the key and a comment without spaces; None if a line is not one."""
    keys = []
    for line in re.split(r"\s*\n\s*", str(text)):
        if not line:
            continue
        m = _SSH_KEY_RE.match(line)
        if not m:
            return None
        keys.append({"type": m.group(1), "value": m.group(2), "comment": m.group(3), "on": True})
    return keys


def add_ssh_keys(keys: list[dict], new: list[dict]) -> list[dict]:
    """Its dialog's addUploadedKeys: the new ones, but not those already there."""
    out = list(keys)
    for key in new:
        if all(k["value"] != key["value"] for k in out):
            out.append(key)
    return out


def ssh_key_shown(key: dict) -> str:
    """A key as its dialog lists it: RSA or DSA, its start and end, its comment."""
    v = key["value"]
    short = f"{v[:16]} … {v[-16:]}" if len(v) > 32 else v
    kind = "DSA" if key.get("type") == "ssh-dss" else "RSA"
    return f"{kind}  {short}" + (f"  {key['comment']}" if key.get("comment") else "")


def plan_ssh_keys(cfg: Config, keys: list[dict]) -> dict[str, str | None]:
    """What its model writes: the keys 1, 2, 3… (value, type, comment, status) and no other."""
    new = cfg.copy()
    for k in [k for k in new.keys() if k.startswith("sshd.auth.key.")]:
        new.delete(k)
    for n, key in enumerate(keys, 1):
        for f, v in (("value", key["value"]), ("type", key.get("type")),
                     ("comment", key.get("comment")),
                     ("status", "enabled" if key.get("on", True) else "disabled")):
            if v:
                new.set(f"sshd.auth.key.{n}.{f}", v)
    return {k: after for k, _, after in cfg.diff(new)}


def telnet_settings(cfg: Config) -> dict:
    return {"on": _model_bool(cfg, "telnetd.status", False),
            "port": _model_port(cfg, "telnetd.port", 23)}


def plan_telnet(s: dict) -> dict[str, str | None]:
    return {"telnetd.status": "enabled" if s["on"] else "disabled", "telnetd.port": s["port"]}


def syslog_settings(cfg: Config) -> dict:
    return {"on": _model_bool(cfg, "syslog.status", True),
            "remote": _model_bool(cfg, "syslog.remote.status", False),
            "ip": cfg.get("syslog.remote.ip") or "",
            "port": _model_port(cfg, "syslog.remote.port", 514)}


def syslog_problem(field: str, value) -> str | None:
    """Its rules while the remote log is on (its label for the IP ends in a colon, which its
    messages carry: here without it)."""
    text = str(value).strip()
    if field == "ip":
        if not text:
            return tr("Remote Log IP Address is required")
        return None if WEB_IPV4_RE.fullmatch(text) else tr("Remote Log IP Address is invalid")
    if not text:
        return tr("Remote Log Port is required")
    return None if _whole(text, 1, 65535) else tr(
        "Remote Log Port must be between {low} and {high}", low=1, high=65535)


def plan_syslog(s: dict) -> dict[str, str | None]:
    return {"syslog.status": "enabled" if s["on"] else "disabled",
            "syslog.remote.status": "enabled" if s["remote"] else "disabled",
            "syslog.remote.ip": s["ip"] or None, "syslog.remote.port": s["port"]}


def service_port(text: str) -> str:
    """A server's port as it is saved: the number its web reads (parseInt), so that "0x16"
    or "+22", which its check lets through, do not reach the device as such."""
    return str(js_parse_int(str(text).strip()))


def service_port_problem(service: str, field: str, value, cfg: Config) -> str | None:
    """Its required and portSys for a server's port (service: web, ssh or telnet; field:
    http_port, https_port or port; cfg with the other services as they will be): a port, not
    one the system keeps, nor one another server already uses (a range a:b, which its check
    takes, is not one here)."""
    required, out_of_range, invalid, system, other = _PORT_MSGS[
        "https_port" if field == "https_port" else "port"]
    text = str(value).strip()
    if not text:
        return tr(required)
    kind = "invalid" if ":" in text else port_kind(text)   # (its check takes a:b too, which
    if kind:                                                 #  would leave the server down)
        return tr(invalid) if kind == "invalid" else tr(out_of_range, low=1, high=65535)
    n = js_parse_int(text)
    if n in SYSTEM_PORTS:
        return tr(system)
    web, ssh, telnet = web_settings(cfg), ssh_settings(cfg), telnet_settings(cfg)
    used = []
    if field != "http_port":
        used.append(web["http_port"])
    if field != "https_port" and web["https"]:
        used.append(web["https_port"])
    if service != "ssh" and ssh["on"]:
        used.append(ssh["port"])
    if service != "telnet" and telnet["on"]:
        used.append(telnet["port"])
    return tr(other) if n in {js_parse_int(p) for p in used} else None
