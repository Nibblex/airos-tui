"""The sample data: the configurations of an airOS 8 and an airOS 6 (bridge, router,
station), the board, the channels and countries of each, the site survey, the neighbours of
its network tools, airView's spectrum, its DHCP leases and its PPPoE provider, and the little
helpers that build them."""
from __future__ import annotations

import math
import random
import re


MAC = "68D79A98FBFF"
# Between the parts of airOS 8's backup (cfg.cgi): "### PART ###", or "### PART SHA512 ###"
# before the configuration when its password is SHA-512 (its backupcfg splits on both).
BACKUP_PART_RE = re.compile(r"^### PART.*###$", re.M)
PASSWORD_RE = re.compile(r"^(users\.\d+\.password|mac-telnetd\.users\.\d+\.password)$")

COMMON_CFG = """\
users.status=enabled
users.1.status=enabled
users.1.name=ubnt
users.1.password=x
httpd.status=enabled
httpd.port=80
httpd.https.status=disabled
httpd.https.port=443
httpd.session.timeout=900
radio.status=enabled
radio.countrycode=484
radio.1.countrycode=484
radio.1.status=enabled
radio.1.devname=ath0
radio.1.mode=master
radio.1.txpower=20
radio.1.freq=5745
radio.1.chanbw=20
radio.1.dfs.status=enabled
radio.1.reg_obey=enabled
radio.1.obey=enabled
radio.1.antenna.id=1
radio.1.antenna.gain=16
radio.1.cable.loss=0
radio.1.ack.auto=disabled
radio.1.ackdistance=1500
wireless.status=enabled
wireless.1.status=enabled
wireless.1.devname=ath0
wireless.1.ssid=North-Tower
wireless.1.hide_ssid=disabled
wireless.1.security.type=none
wireless.1.mac_acl.status=disabled
wireless.1.mac_acl.policy=allow
wireless.1.l2_isolation=disabled
aaa.status=enabled
aaa.1.status=enabled
aaa.1.devname=ath0
aaa.1.ssid=North-Tower
aaa.1.wpa.mode=2
aaa.1.wpa.1.pairwise=CCMP
aaa.1.wpa.key.1.mgmt=WPA-PSK
aaa.1.wpa.psk=test-key-123
aaa.1.radius.auth.1.status=disabled
aaa.1.radius.acct.1.status=disabled
wpasupplicant.status=disabled
wpasupplicant.device.1.status=disabled
wpasupplicant.device.1.devname=ath0
wpasupplicant.profile.1.network.1.ssid=North-Tower
wpasupplicant.profile.1.network.1.psk=test-key-123
resolv.host.1.status=enabled
resolv.host.1.name=AP-North-Tower
resolv.nameserver.1.status=enabled
resolv.nameserver.1.ip=8.8.8.8
sshd.status=enabled
sshd.port=22
discovery.status=enabled
"""
# Network in bridge mode: the same structure as the web's template (ubnt.net.js).
NET_BRIDGE = """\
netmode=bridge
bridge.status=enabled
bridge.1.devname=br0
bridge.1.fd=1
bridge.1.port.1.devname=eth0
bridge.1.port.2.devname=ath0
bridge.1.stp.status=disabled
netconf.status=enabled
netconf.1.status=enabled
netconf.1.devname=eth0
netconf.1.ip=0.0.0.0
netconf.1.netmask=255.255.255.0
netconf.1.up=enabled
netconf.2.status=enabled
netconf.2.devname=ath0
netconf.2.ip=0.0.0.0
netconf.2.netmask=255.255.255.0
netconf.2.up=enabled
netconf.3.status=enabled
netconf.3.devname=br0
netconf.3.ip=192.168.1.20
netconf.3.netmask=255.255.255.0
netconf.3.up=enabled
netconf.3.role=mlan
netconf.3.autoip.status=enabled
dhcpc.status=disabled
dhcpc.1.status=disabled
dhcpc.1.devname=br0
dhcpc.1.fallback=192.168.1.20
dhcpc.1.fallback_netmask=255.255.255.0
route.status=enabled
route.1.status=enabled
route.1.devname=br0
route.1.gateway=192.168.1.1
route.1.ip=0.0.0.0
route.1.netmask=0
"""
# Network in router mode: WAN on eth0 with DHCP, LAN on br0 with a DHCP server and NAT.
NET_ROUTER = """\
netmode=router
bridge.status=enabled
bridge.1.devname=br0
bridge.1.port.1.devname=ath0
netconf.status=enabled
netconf.1.status=enabled
netconf.1.devname=eth0
netconf.1.ip=0.0.0.0
netconf.1.netmask=255.255.255.0
netconf.1.up=enabled
netconf.1.role=wan
netconf.2.status=enabled
netconf.2.devname=ath0
netconf.2.ip=0.0.0.0
netconf.2.netmask=255.255.255.0
netconf.2.up=enabled
netconf.3.status=enabled
netconf.3.devname=br0
netconf.3.ip=192.168.1.1
netconf.3.netmask=255.255.255.0
netconf.3.up=enabled
netconf.3.role=lan
dhcpc.status=enabled
dhcpc.1.status=enabled
dhcpc.1.devname=eth0
dhcpc.1.fallback=192.168.10.1
dhcpc.1.fallback_netmask=255.255.255.0
dhcpd.status=enabled
dhcpd.1.status=enabled
dhcpd.1.devname=br0
dhcpd.1.dnsproxy=enabled
dhcpd.1.lease_time=600
dhcpd.1.netmask=255.255.255.0
dhcpd.1.start=192.168.1.2
dhcpd.1.end=192.168.1.254
iptables.status=enabled
iptables.sys.status=enabled
iptables.sys.masq.status=enabled
iptables.sys.masq.1.status=enabled
iptables.sys.masq.1.devname=eth0
route.status=enabled
"""
V8_EXTRA = ("system.cfg.version=65547\nmac-telnetd.users.1.password=x\n"
            # discovery as in the factory /etc/system.cfg of airOS 8.7.19 (airOS 6 brings
            # no discovery.cdp.status: its web then shows wireless.1.addmtikie)
            "discovery.beacon.status=enabled\ndiscovery.cdp.status=disabled\n"
            "discovery.lldp.status=enabled\n"
            # UISP as a NanoStation 5AC with airOS 8.7.14 has it before any key ("-": none)
            "unms.status=disabled\nunms.uri=-\n"
            # the date settings with NTP off (a real 8.7.25 with it on: ntpclient.status and
            # ntpclient.1.status enabled, the same server, system.timezone=ART3)
            "ntpclient.1.server=0.ubnt.pool.ntp.org\nntpclient.1.status=disabled\n"
            "ntpclient.status=disabled\nsystem.date.status=disabled\nsystem.timezone=UTC\n"
            # the rest of the Services page as a NanoStation 5AC loco with 8.7.14 has it
            "snmp.community=public\nsnmp.status=disabled\nsshd.auth.passwd=enabled\n"
            "syslog.remote.port=514\nsyslog.remote.status=disabled\nsyslog.status=enabled\n"
            "telnetd.port=23\ntelnetd.status=disabled\n"
            # the rest of the System page as a NanoStation 5AC loco with 8.7.14 has it (its
            # web in Spanish there)
            "airview.status=enabled\ngui.language=en_US\nradio.1.ff_cap_rep=0\n"
            "system.airosx.prov.status=disabled\nsystem.autosend_crash_reports=disabled\n"
            "system.external.reset=enabled\nsystem.leds.nightmode=disabled\n"
            "users.2.gid=100\nusers.2.shell=/bin/false\nusers.2.status=disabled\n"
            "users.2.uid=100\n"
            # the ping watchdog as a LiteBeam 5AC with airOS 8.7.25 has it (never turned on)
            "pwdog.delay=300\npwdog.period=300\npwdog.retry=3\npwdog.status=disabled\n"
            # the rest of what the airOS 8 web saves for an access point PtMP
            "radio.1.ieee_mode=11acvht20\nradio.1.center.1.freq=5745\nradio.1.ptpmode=0\n"
            "radio.1.scanbw.status=disabled\nradio.1.scan_list.status=disabled\n"
            "radio.1.acktimeout=41\nradio.1.polling_11ac_11n_compat=1\n"
            "radio.1.polling_ff_flex=1\nradio.1.polling_ff_dur=0\n"
            "radio.1.polling_ff_dl_ratio=50\nradio.1.polling_ff_sta_rx_rssi_th=0\n"
            "radio.1.cmsbias=0\nradio.1.pollingpri=2\nradio.1.rx_sensitivity=-96\n"
            "radio.1.atpc.status=disabled\nradio.1.atpc.sta.status=enabled\n"
            "radio.1.atpc.threshold=36\nradio.1.rc_mode=0\nwireless.1.wds.status=enabled\n"
            "wireless.1.mcast.enhance=1\nwireless.1.ampdu.frames=32\n"
            "wireless.1.ampdu.status=enabled\nwireless.1.amsdu=3\nwireless.1.rate.mcs=-1\n"
            "wireless.1.rate.auto=enabled\naaa.1.radius.auth.1.port=1812\n"
            "aaa.1.radius.acct.1.port=1813\nwpasupplicant.device.1.profile=AUTO\n"
            "wpasupplicant.profile.1.name=AUTO\n"
            "wpasupplicant.profile.1.network.1.key_mgmt.1.name=WPA-PSK\n"
            "wpasupplicant.profile.1.network.1.pairwise.1.name=CCMP\n"
            "wpasupplicant.profile.1.network.1.proto.1.name=RSN\n"
            "wpasupplicant.profile.1.network.1.priority=100\n"
            "wpasupplicant.profile.1.network.2.priority=2\n"
            "wpasupplicant.profile.1.network.2.key_mgmt.1.name=NONE\n"
            "wpasupplicant.profile.1.network.2.status=disabled\n")
# What link.cgi, advanced.cgi and ubnt.cgi of airOS 6 save for an access point.
V6_EXTRA = ("system.cfg.version=65546\nradio.1.ieee_mode=11naht20\nradio.1.cwm.mode=0\n"
            "radio.1.cwm.enable=0\nradio.1.forbiasauto=0\nradio.1.acktimeout=31\n"
            "radio.1.rate.auto=enabled\nradio.1.rate.mcs=15\nradio.1.mcastrate=0\n"
            "radio.countrylicense=0\nradio.1.subsystemid=\nradio.1.polling=enabled\n"
            "radio.1.pollingnoack=0\nradio.1.pollingpri=\nradio.1.ampdu.status=enabled\n"
            "radio.1.ampdu.frames=32\nradio.1.ampdu.bytes=50000\nradio.1.rts=off\n"
            "system.eirp.status=disabled\nwireless.1.ap=\nwireless.1.wds.status=disabled\n"
            "wireless.1.autowds=disabled\nwireless.1.authmode=1\n"
            "wireless.1.scan_list.status=disabled\nwireless.1.scan_list.channels=\n"
            "wireless.1.mcast.enhance=2\nwireless.1.sens=0\n"
            "wireless.hideindoor.status=disabled\naaa.1.driver=madwifi\n"
            "aaa.1.br.devname=br0\naaa.1.radius.macacl.status=disabled\n"
            "ebtables.sys.status=enabled\nebtables.sys.arpnat.status=disabled\n"
            "ebtables.sys.eap.status=enabled\nebtables.sys.eap.1.status=enabled\n"
            "ebtables.sys.eap.1.devname=ath0\n")
# --station: what each web saves when the same device becomes a station (WPA2-PSK).
STATION = {
    8: {"radio.1.mode": "managed", "aaa.status": "disabled", "aaa.1.status": "disabled",
        "wpasupplicant.status": "enabled", "wpasupplicant.device.1.status": "enabled",
        "wireless.1.mac_acl.status": "disabled", "radio.1.atpc.status": "disabled",
        "wireless.1.mcast.enhance": "0", "radio.1.polling_ff_flex": "0"},
    6: {"radio.1.mode": "managed", "aaa.status": "disabled", "aaa.1.status": "disabled",
        "wpasupplicant.status": "enabled", "wpasupplicant.device.1.status": "enabled",
        "wpasupplicant.device.1.driver": "madwifi", "wpasupplicant.device.1.profile": "WPA-PSK",
        "wpasupplicant.profile.1.name": "WPA-PSK",
        "wpasupplicant.profile.1.network.1.key_mgmt.1.name": "WPA-PSK",
        "wpasupplicant.profile.1.network.1.proto.1.name": "RSN",
        "wpasupplicant.profile.1.network.1.pairwise.1.name": "CCMP",
        "wpasupplicant.profile.1.network.1.bssid": "",
        "wpasupplicant.profile.1.network.1.eap.1.status": "disabled",
        "ebtables.sys.arpnat.status": "enabled", "radio.1.polling": "disabled"},
}
FIRMWARE = {8: "WA.ar934x.v8.7.19.48279.250811.0636", 6: "XW.ar934x.v6.3.16.33429.240408.1736"}
MODEL = {8: "LiteBeam 5AC (simulated)", 6: "NanoStation M5 (simulated)"}

# CDP and LLDP announcements (the device sends them by itself; nobody asks for them).

UI_LANGS = [{"lang": "Deutsch", "code": "de_DE"}, {"lang": "English", "code": "en_US"},
            {"lang": "Español", "code": "sp_SP"}, {"lang": "Français", "code": "fr_FR"},
            {"lang": "Italiano", "code": "it_IT"}, {"lang": "Lietuviškai", "code": "lt_LT"},
            {"lang": "Polski", "code": "pl_PL"}, {"lang": "Português", "code": "pt_PT"},
            {"lang": "Türk", "code": "tr_TR"}, {"lang": "Česky", "code": "cz_CZ"},
            {"lang": "中文（简体）", "code": "zh_CN"}]
# board.info (the same keys ubnthal generates on the real device).
BOARD = {
    8: {"board.name": "LiteBeam 5AC (simulated)", "board.sysid": "0xe7f9",   # (a Gen2's)
        "radio.1.txpower.max": "25",
        "radio.1.txpower.min": "-4", "radio.1.chanbw": "10,20,30,40,50,60,80",
        "radio.1.ccode": "484", "radio.1.def_antenna": "1", "radio.1.antenna.1.id": "1",
        "radio.1.antenna.1.gain": "16", "radio.1.antenna.1.builtin": "1",
        "radio.1.antenna.1.name": "Built in - 16 dBi", "radio.1.antennas": "1",
        "radio.1.ieee_modes": "5", "radio.1.ptp_only": "1", "radio.1.ptmp_only": "1",
        "radio.1.ptp_sta": "1", "radio.1.ptmp_sta": "1",
        "board.netmodes": "7", "board.phycount": "1", "board.hwaddr": MAC,
        "board.reboot": "40", "board.upgrade": "150", "board.required_fw_version": "524800",
        # a Gen2 board, as a NanoStation 5AC loco: its 2.4 GHz management radio is left out
        # of the web (web_exclude); and, unlike it, the "External Reset" of the PoE adapter
        "radio.2.ieee_modes": "10", "radio.2.web_exclude": "1", "feature.g2": "1",
        "feature.g3": "0", "feature.ext_reset": "1", "feature.poe_passthrough": "0"},
    6: {"board.name": "NanoStation M5 (simulated)", "radio.1.txpower.max": "27",
        "radio.1.txpower.min": "1", "radio.1.chanbw": "5,10,20,30,40",
        "radio.1.ccode": "484", "radio.1.def_antenna": "1", "radio.1.antenna.1.id": "1",
        "radio.1.antenna.1.gain": "16", "radio.1.antenna.1.builtin": "1",
        "radio.1.antenna.1.name": "Built in - 16 dBi",
        "board.netmodes": "7", "board.phycount": "2"},
}
# Sample channels for country 484: (control, center, width, maximum EIRP).
# Illustrative values for testing, not the real regulations.
_LOW = [5180, 5200, 5220, 5240]
_HIGH = [5745, 5765, 5785, 5805]
CHANNELS = ([(f, f, 20, 30) for f in _LOW] + [(f, f, 20, 36) for f in _HIGH + [5825]]
            + [(f, 5190 if f < 5210 else 5230, 40, 30) for f in _LOW]
            + [(f, 5755 if f < 5775 else 5795, 40, 36) for f in _HIGH]
            + [(f, 5210, 80, 30) for f in _LOW] + [(f, 5775, 80, 36) for f in _HIGH])


_DFS = [5500, 5520, 5540, 5560]
# More countries, to change it: also illustrative. Spain (ETSI) only has the low band and
# the DFS channels; it is one of the countries the airOS 8 web does not let you leave.
COUNTRIES = {
    484: ("Mexico", "MX", CHANNELS),
    840: ("United States", "US", [(f, c, w, e if f > 5700 else e + 6) for f, c, w, e in CHANNELS]),
    724: ("Spain", "ES", [(f, c, w, 23) for f, c, w, e in CHANNELS if f < 5300]
          + [(f, f, 20, 30) for f in _DFS]
          + [(f, 5510 if f < 5530 else 5550, 40, 30) for f in _DFS]),
}


def channels_of(cc) -> list[tuple]:
    return COUNTRIES.get(_cc(cc), ("", "", []))[2]


def _cc(cc) -> int:
    try:
        return int(cc)
    except (TypeError, ValueError):
        return 0


def regdomain_v8(cc=484) -> list[str]:
    """The login's rd and /api/regdomain's data, as a real 8.7.25 gives them (checked against
    its API, emulated): a line with the country, then one per channel: control and center
    frequencies, channel, band, width, maximum EIRP, a code (1 from 10 to 30 MHz; from 40 to
    60, 2 or 4, its extension above or below; 8 at 80; plus 128 with DFS) and two 1s. A
    country it does not know: nothing."""
    name, iso, chans = COUNTRIES.get(_cc(cc), ("", "", []))
    if not chans:
        return []
    lines = [f"{name} ({iso}, 0x0, {_cc(cc)}) RD_SIM (0x0, 0) X (gain=0)"]
    for f, c, bw, e in channels_of(cc):
        code = 1 if bw <= 30 else 8 if bw >= 80 else (2 if c >= f else 4)
        code |= 128 if f in _DFS else 0
        band = "A" if c > 4000 else "G"
        lines.append(f"{f} {c} {(f - 5000) // 5:3} {band} {bw} {float(e):.1f} {code:3}  1  1")
    return lines


def regdomain_v6(bw: int, cc=484) -> list[str]:
    name, iso, chans = COUNTRIES.get(_cc(cc), ("", "", []))
    if not chans:
        return ["", ""]
    lines = [f"{name} ({iso}), 0x0, {_cc(cc)}"]
    # columns: frequency, channel, mode, width, EIRP, flags (1 + 2 above + 4 below; 8 DFS),
    # ubnt, obey
    for f, c, b, e in chans:
        if b == bw:
            flags = 1 | (8 if f in _DFS else 0) | ((2 if c > f else 4) if b == 40 else 0)
            lines.append(f"{f} {(f - 5000) // 5} A {b} {e}.0 {flags} 1 1")
    return lines + [""]


def country_options() -> str:
    """The <option>s cc_change.cgi lists (airOS 6)."""
    return "".join(f'<option value="{cc}">{name}</option>'
                   for cc, (name, _, _) in sorted(COUNTRIES.items(), key=lambda x: x[1][0]))

SURVEY_TIME = 1.0
# The network around the device, for the web's network tools: made-up answers, the device is
# the one that asks (nothing leaves this computer). Address: (name, ms of a ping, TTL).
NEIGHBORS = {"192.168.1.1": ("gateway.lan", 0.62, 64),
             "192.168.1.21": ("client-north-01.lan", 1.84, 64),
             "100.64.0.1": ("core.isp.example.net", 6.4, 254),
             "8.8.8.8": ("dns.google", 14.3, 117), "1.1.1.1": ("one.one.one.one", 12.7, 57)}
SPEEDTEST_PEER = "192.168.1.21"           # its station, the other end of a speed test
# The leases its DHCP server gave (MAC, the host part of the IP from the range's start,
# seconds left, name): what /api/dhcp-server/leases lists in router mode.
LEASES = (("00:15:6D:11:22:33", 98, 3561, "laptop-north"),
          ("F0:9F:C2:44:55:66", 99, 90061, "camera-gate"))
# airView (/airviewdata.cgi), as a real 5 GHz Gen2 board gives it (8.7.14): its short-term grid
# every 1.25 MHz from 5095.625 MHz, its ambient noise's every 5 MHz from 5097.5, its powers in
# steps of 2 dB from -125 dBm. What it hears is made up: the noise, its own channel and, half
# of the time, a neighbour around 5500 MHz.
AV_ST = [50956250 + 12500 * i for i in range(648)]
AV_LT = [50975000 + 50000 * i for i in range(161)]
AV_FRAMES, AV_HOURS = 8, 11      # its instant frames; the hours gathered (as the real one had)


def _av_mean(mhz: float, own: int, bw: int, busy: bool) -> float:
    """The power heard at a frequency, in its steps (v is 2v-125 dBm)."""
    level = 9.0                                      # -107 dBm: the noise
    if abs(mhz - own) <= bw / 2:
        level = 22.0                                 # -81 dBm: its own channel
    if busy and abs(mhz - 5500) <= 20:
        level = max(level, 17.0)                     # -91 dBm: the neighbour
    return level


def _av_pmf(mean: float, sd: float = 1.5) -> list[float]:
    """The share of its readings in each power step (0 to 63) around mean."""
    edges = [0.5 * (1 + math.erf((k - 0.5 - mean) / (sd * math.sqrt(2)))) for k in range(65)]
    return [edges[u + 1] - edges[u] for u in range(64)]


def airview_payload(frame: int, own: int, bw: int) -> dict:
    """Its /airviewdata.cgi after frame reads: its histogram grows a little at each one, its
    latest and instant powers change and its ambient noise (24 minutes, AV_HOURS hours) stays
    as it is."""
    rng, still = random.Random(frame), random.Random(7)
    busy, total, cache = frame % 4 < 2, 800 + 4 * frame, {}
    columns = []
    for f in AV_ST:
        key = (_av_mean(f / 1e4, own, bw, False), _av_mean(f / 1e4, own, bw, True))
        if key not in cache:
            quiet, loud = (_av_pmf(m) for m in key)
            cache[key] = [round(total * (a + b) / 2) for a, b in zip(quiet, loud)]
        columns.append(cache[key])

    def heard(f: int, extra: float = 0.0, sd: float = 1.5, now: bool = busy) -> int:
        return max(0, min(63, round(rng.gauss(_av_mean(f / 1e4, own, bw, now) + extra, sd))))
    medium = [[max(1, round(_av_mean(f / 1e4, own, bw, row % 3 == 0) + 4 + 3 * still.random()))
               for f in AV_LT] for row in range(24)]
    hourly = [[max(1, round(_av_mean(f / 1e4, own, bw, True) - 4 + still.random()))
               for f in AV_LT] if row < AV_HOURS else [0] * len(AV_LT) for row in range(24)]
    return {"stFreqGridNumPts": len(AV_ST), "stFreqGridLabels": AV_ST, "stPowrMin": -125,
            "stPowrNum": 64, "stPowrDel": 2, "mtUpdateInterval": 60,
            "mtFreqGridNumPts": len(AV_LT), "mtFreqGridLabels": AV_LT,
            "ltUpdateInterval": 3600, "ltFreqGridNumPts": len(AV_LT), "ltFreqGridLabels": AV_LT,
            "pwrHistogram": [[col[u] for col in columns] for u in range(64)],
            "totalSamples": [sum(col) for col in columns],
            "latestPower": [heard(f) for f in AV_ST], "instantPowerNumFrames": AV_FRAMES,
            "instantPower": [[heard(f) for f in AV_ST] for _ in range(AV_FRAMES)],
            "mtTimeNum": 24, "mediumAnze": medium, "ltTimeNum": 24, "latestAnze": hourly}


# What a real 8.7.25 with its web in Spanish answers to /api/firewall/port-forward while port
# forwarding is off (the English one is a guess: not seen on a device).
PORTFW_OFF = {"es": "El redireccionamiento de puertos está desactivado, no es posible mostrar "
                    "las reglas del redireccionamiento de puertos.",
              "en": "Port forwarding is disabled, unable to show the port forwarding rules."}
_EB_PROTOS = {"0x0800": "IPv4", "0x86dd": "IPv6", "0x8100": "802_1Q", "0x0806": "ARP"}
_IP_PROTOS = {"1": "icmp", "6": "tcp", "17": "udp", "58": "ipv6-icmp"}
_IPT_PROTOS = {"ip": "all", "1": "icmp", "6": "tcp", "17": "udp", "tcp": "tcp", "udp": "udp"}
_IPT_HEADER = (" pkts bytes target     prot opt in     out     source               "
               "destination         ")


def _ap(mac, essid, hostname, airmax, mode, signal, enc, auth, freq, center, chanbw, ch):
    return {"mac": mac, "essid": essid, "hostname": hostname, "ieee_mode": "802.11ac",
            "airmax_ie": "enabled" if airmax else "", "airmax_mode": mode, "htcap": 0,
            "signal_level": str(signal), "noise_level": "-95", "quality": f"{signal + 96}/94",
            "encryption": enc, "auth_suites": auth, "pairwise_ciphers": "CCMP" if auth else "",
            "group_cipher": "CCMP" if auth else "", "frequency": freq, "center1": center,
            "chanbw": chanbw, "channel": ch, "mode": "Master", "cell": "", "connected_clients": "",
            "max_clients": ""}


SURVEY_APS = [
    _ap("68:D7:9A:11:22:30", "North-Tower", "North-Tower-AP", True, "ptmp", -62, "wpa2", "PSK",
        "5.745", "5755", "40", "149"),
    _ap("68:D7:9A:11:22:40", "South-Link", "South-PtP", True, "ptp", -70, "wpa2", "802.1X",
        "5.18", "5210", "80", "36"),
    _ap("00:11:22:33:44:55", "Cafe-WiFi", "", False, "", -80, "-", "", "5.2", "5200", "20",
        "40"),
    _ap("68:D7:9A:11:22:50", "", "Hidden-AP", True, "ptmp", -75, "wpa2", "PSK", "5.765",
        "5765", "20", "153"),
]

# Simulated PPPoE "provider": it accepts this user and this password, and no MPPE.
PPPOE_USER, PPPOE_PASSWORD = "customer", "secret"
PPPOE_SESSION = {"device": "ppp0", "name": PPPOE_USER, "iplocal": "100.64.10.20",
                 "ipremote": "100.64.0.1", "dns1": "100.64.0.53", "dns2": "100.64.0.54"}
# pppinfo.cgi labels (airOS 6 translates them to the web's language).
PPPINFO_LABELS = {
    "en": ["Name", "Local IP Address", "Remote IP Address", "Primary DNS IP",
           "Secondary DNS IP", "Connection Time", "Bytes Transmitted", "Bytes Received"],
    "es": ["Nombre", "Dirección IP local", "Dirección IP remota", "DNS primario",
           "DNS secundario", "Tiempo de conexión", "Bytes transmitidos", "Bytes recibidos"],
}

def _countdown(secs: int) -> str:
    """airOS 6's secsToCountdown()."""
    d, s = divmod(secs, 86400)
    text = f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{d} {'day' if d == 1 else 'days'} {text}" if d else text
