"""Wireless: the web's Wireless page and its advanced settings, read and saved as each
web does (airOS 8: its models; airOS 6: link.cgi, advanced.cgi, ubnt.cgi and
macacl.cgi), and their validation."""
from __future__ import annotations

import ipaddress
import re

from ..core.i18n import N_, tr
from ..core.config import (_cfg_def, Config, _freqs, gen3, _indices, _int, js_round, _model_bool,
                           _v8_on)
from ..core.validation import _v_ssid
from .network import net_mode
from .limits import board_antennas, v6_active_antenna, V6_CAPS_KEY


#
# Each web is reproduced whole. wireless_state() reads the configuration the way the web
# fills in its page (airOS 8: the cfgMap of its WirelessBasic, WirelessSecurity,
# Wireless_Advanced, WirelessBackupSsid and MacAclRule models; airOS 6: what link.cgi,
# advanced.cgi, ubnt.cgi and macacl.cgi start from), and wireless_save() writes what saving
# the page with that state writes. A change (plan_wireless) is the difference between saving
# the page as it is and saving it with the change: only the keys that depend on the change,
# with the values the web gives them, and none of the defaults the web would also rewrite on
# its own. airOS 8 Gen3 boards (60 GHz) are not handled.

CHANBW_AUTO_AP, CHANBW_AUTO = -1, 0          # airOS 8 settings.CHANBW
V8_MIN_ACK = 31                              # airOS 8 ack._minAck
LIGHT_HALF = 150                             # metres per microsecond, there and back
ATPC_TARGET = (26, 36, 46)                   # airOS 8 atpc_rssi_min, _default and _max
V8_LOCKED_COUNTRIES = frozenset((300, 360, 392, 724, 764, 792))
V8_UNLOCKED_COUNTRIES = frozenset((1125,))
EU_LOCKED_CC = 46
# regdomain.requiresETSI() on airOS 8 and requiresCE() on airOS 6: the same list.
ETSI_COUNTRIES = frozenset((40, 56, 100, 191, 196, 203, 208, 233, 246, 250, 276, 300, 348,
                            352, 372, 380, 428, 438, 440, 442, 470, 528, 530, 578, 616, 620,
                            642, 703, 705, 724, 752, 826))
# airOS 6: countries whose page has no "Change" button (is_changeable_country()).
V6_FIXED_COUNTRIES = ETSI_COUNTRIES | {840, 630, 360, 804, 756, 764, 643}
# airOS 8 getNoAckDistance(): the farthest distance each channel width allows, in metres.
NO_ACK_DISTANCE = {10: 37500, 20: 22500, 40: 22500, 80: 22500, 30: 15000, 60: 15000,
                   50: 18000}
# airOS 6 radio capabilities (radio_cap_11n_no_ht40 and radio_cap_fast_clock).
V6_CAP_NO_HT40, V6_CAP_FAST_CLOCK = 1024, 8192
V8_RATES = (("-1", N_("Automatic")), ("2", "2x (QPSK)"), ("4", "4x (16QAM)"),
            ("7", "6x (64QAM)"), ("9", "8x (256QAM)"))
STA_PRIORITIES = ((0, N_("High")), (1, N_("Medium")), (2, N_("Base")), (3, N_("Low")))
TDD_DURATIONS = ((0, N_("Flexible (legacy)")), (1, N_("Flexible")), (5000, N_("Fixed 5 ms")),
                 (8000, N_("Fixed 8 ms")), (10000, N_("Fixed 10 ms")))
V6_SECURITY = {"none": N_("None"), "wep": "WEP", "wpa": "WPA", "wpatkip": "WPA-TKIP",
               "wpaaes": "WPA-AES", "wpa2": "WPA2", "wpa2tkip": "WPA2-TKIP",
               "wpa2aes": "WPA2-AES"}
V6_PAIRWISE = {"wpa": "TKIP CCMP", "wpa2": "TKIP CCMP", "wpatkip": "TKIP",
               "wpa2tkip": "TKIP", "wpaaes": "CCMP", "wpa2aes": "CCMP"}
# airOS 6 keeps each part on its own page, and a change saves only the page it is on.
V6_PAGES = {"advanced": frozenset(("rts", "distance", "ack_auto", "aggr_on", "aggr_frames",
                                   "aggr_bytes", "mcast_data", "mcast_enh", "isolation",
                                   "rx_sens")),
            "airmax": frozenset(("polling", "sta_priority")),
            "acl": frozenset(("acl_rules",))}
V6_PAGE_ORDER = ("link", "advanced", "airmax", "acl")
# The generic antennas the airOS 8 web adds for the dual-band Bullet AC.
GENERIC_ANTENNAS = ({"id": 100, "name": "Omni", "gain": 13, "builtin": False, "band": 1},
                    {"id": 101, "name": "Omni", "gain": 13, "builtin": False, "band": 2},
                    {"id": 102, "name": "Dish", "gain": 24, "builtin": False, "band": 1},
                    {"id": 103, "name": "Dish", "gain": 34, "builtin": False, "band": 2},
                    {"id": 104, "name": "Sector", "gain": 17, "builtin": False, "band": 1},
                    {"id": 105, "name": "Sector", "gain": 22, "builtin": False, "band": 2})
BULLET_AC_SYSIDS = ("0xe2c5", "0xe2c7")
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def _uesc(text):
    """The web's _uesc(): undoes the HTML escaping of its forms."""
    if not isinstance(text, str):
        return text
    for a, b in (("&amp;", "&"), ("&#x27;", "'"), ("&#x2F;", "/"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(a, b)
    return text


def _b(on) -> str:
    return "enabled" if on else "disabled"


def wireless_supported(flavor: str | None, board: dict) -> bool:
    """airOS 6, and airOS 8 except the Gen3 boards (their 60 GHz radio is another page)."""
    return flavor == "v6" or not gen3(board)


# -- airOS 8 --------------------------------------------------------------------------

def _v8_put(c: Config, key: str, value) -> None:
    """setValue() of the airOS 8 web: an empty, false or undefined value is not saved."""
    if value is None or value is False or value == "":
        c.delete(key)
    else:
        c.set(key, str(value))


def _v8_num(cfg: Config, key: str, default: int, empty_default: bool = False) -> int:
    """An "int" field; empty_default for the ones read as getValue(key) || default."""
    value = cfg.get(key)
    n = None if value is None or (empty_default and value == "") else _int(value)
    return default if n is None else n


def v8_bands(board: dict) -> tuple[bool, bool]:
    """(5 GHz, 2.4 GHz) according to radio.1.ieee_modes (hasIeeeModeA/G)."""
    modes = _int(board.get("radio.1.ieee_modes")) or 0
    return bool(modes & 5), bool(modes & 10)


def v8_modes(board: dict) -> list[str]:
    """The wireless modes the web offers (getAvailableModes); all four if board.info does
    not say."""
    keys = {"ap-ptp": "radio.1.ptp_only", "ap-ptmp": "radio.1.ptmp_only",
            "sta-ptp": "radio.1.ptp_sta", "sta-ptmp": "radio.1.ptmp_sta"}
    if not any(k in board for k in keys.values()):
        return list(keys)
    return [m for m, k in keys.items() if board.get(k) not in (None, "", "0")]


def v8_antenna_choices(board: dict, ieee: str) -> list[dict]:
    """getAntennas(): the antennas of the band, "Select.." on dual-band boards and
    "Custom.." if there is an external one."""
    ants = board_antennas(board)
    out: list[dict] = []
    has_a, has_g = v8_bands(board)
    if has_a and has_g:
        if (board.get("board.sysid") or "").lower() in BULLET_AC_SYSIDS:
            ants = ants + [dict(a) for a in GENERIC_ANTENNAS]
        band = 1 if "G" in ieee.upper() else 2
        ants = [a for a in ants if a["band"] & band]
        out.append({"id": -1, "name": "Select..", "gain": 0, "builtin": False, "band": 0})
    out += ants
    if any(not a["builtin"] for a in board_antennas(board)):
        out.append({"id": 0, "name": "Custom..", "gain": 0, "builtin": False, "band": 0})
    return out


def _v8_antenna_by_id(board: dict, ant_id) -> dict | None:
    return next((a for a in board_antennas(board) + list(GENERIC_ANTENNAS)
                 if a["id"] == ant_id), None)


def _v8_antenna_id(cfg: Config, board: dict, ieee: str) -> int:
    """The antenna cfgMap's getValue(): the configured one, or the one whose gain matches."""
    def_id = _int(board.get("radio.1.def_antenna")) or -1
    default = _v8_antenna_by_id(board, def_id)
    raw = cfg.get("radio.1.antenna.id")
    ant_id = _int(raw) if raw else def_id
    if ant_id is None or ant_id == 0:
        return 0 if ant_id == 0 else def_id
    cur = _v8_antenna_by_id(board, ant_id)
    graw = cfg.get("radio.1.antenna.gain")
    gain = _int(graw) if graw else (default["gain"] if default else 0)
    ants = sorted(v8_antenna_choices(board, ieee), key=lambda a: a["gain"])
    if ants and gain is not None and (not cur or cur["gain"] != gain):
        last = ants[0]
        for a in ants:
            if a["gain"] >= gain:
                return a["id"]
            last = a
        return last["id"]
    return ant_id


def _v8_wireless_state(cfg: Config, board: dict) -> dict:
    g = cfg.get
    ap = (g("radio.1.mode") or "managed").lower() == "master"
    has_a, has_g = v8_bands(board)
    raw_ieee = g("radio.1.ieee_mode")
    if not raw_ieee or raw_ieee == "auto":
        ieee = "11nght" if has_g and not has_a else "11acvht"
    else:
        ieee = raw_ieee[:-2]
    country = g("radio.countrycode")
    st = {
        # WirelessBasic
        "ap": ap, "ptp": _model_bool(cfg, "radio.1.ptpmode", True),
        "wds": _model_bool(cfg, "wireless.1.wds.status", True),
        "ssid": "www.uisp.com" if g("wireless.1.ssid") is None else g("wireless.1.ssid"),
        "ap_mac": g("wpasupplicant.profile.1.network.1.bssid"),
        "chanbw": _v8_num(cfg, "radio.1.chanbw", 0, True),
        "auto_chanbw": _model_bool(cfg, "radio.1.scanbw.status", False),
        "gain": _v8_num(cfg, "radio.1.antenna.gain", 0),
        "loss": _v8_num(cfg, "radio.1.cable.loss", 2),
        "antenna": _v8_antenna_id(cfg, board, ieee),
        "power": _v8_num(cfg, "radio.1.txpower", 0),
        "freq": _v8_num(cfg, "radio.1.freq", 0, True),
        "center": _v8_num(cfg, "radio.1.center.1.freq", -1, True),
        "ieee": ieee,
        "scan_on": _model_bool(cfg, "radio.1.scan_list.status", False),
        "scan": _freqs(g("radio.1.scan_list.channels")),
        "obey": _model_bool(cfg, "radio.1.reg_obey", True),
        "ack_auto": _model_bool(cfg, "radio.1.ack.auto", False),
        "distance": _v8_num(cfg, "radio.1.ackdistance", 0),
        "country": (_int(country) if country else None)
        or (_int(board.get("radio.1.ccode")) or 840),
        "tdd_flex": _v8_num(cfg, "radio.1.polling_ff_flex", 0),
        "tdd_ratio": _v8_num(cfg, "radio.1.polling_ff_dl_ratio", 50),
        "ax_compat": _model_bool(cfg, "radio.1.polling_11ax_compat", False),
        # not in the web: the firmware reads it (iwpriv hide_ssid)
        "hide_ssid": (g("wireless.1.hide_ssid") or "disabled") == "enabled",
    }
    st["tdd_duration"] = 1 if st["tdd_flex"] else _v8_num(cfg, "radio.1.polling_ff_dur", 0)
    # WirelessSecurity
    if ap:
        on = ((g("aaa.status") or "disabled") == "enabled"
              and (g("aaa.1.status") or "enabled") == "enabled"
              and (_int(g("aaa.1.wpa.mode")) or 0) > 0)
        auth = g("aaa.1.wpa.key.1.mgmt") or "WPA-PSK"
        psk = g("aaa.1.wpa.psk") or g("wpasupplicant.profile.1.network.1.psk") or ""
    else:
        mgmt = g("wpasupplicant.profile.1.network.1.key_mgmt.1.name") or "NONE"
        on = ((g("wpasupplicant.status") or "disabled") == "enabled"
              and (g("wpasupplicant.device.1.status") or "enabled") == "enabled"
              and mgmt != "NONE")
        auth = "WPA-PSK" if mgmt == "NONE" else mgmt
        psk = g("wpasupplicant.profile.1.network.1.psk") or g("aaa.1.wpa.psk") or ""
    p1 = "wpasupplicant.profile.1.network.1."
    st.update({
        "security": "wpa2" if on else "none", "wpa_auth": auth, "psk": psk,
        "radius_ip": g("aaa.1.radius.auth.1.ip"),
        "radius_port": _v8_num(cfg, "aaa.1.radius.auth.1.port", 1812),
        "radius_secret": g("aaa.1.radius.auth.1.secret"),
        "acct_on": _model_bool(cfg, "aaa.1.radius.acct.1.status", False),
        "acct_ip": g("aaa.1.radius.acct.1.ip"),
        "acct_port": _v8_num(cfg, "aaa.1.radius.acct.1.port", 1813),
        "acct_secret": g("aaa.1.radius.acct.1.secret"),
        "eap_anon": g(p1 + "anonymous_identity"), "eap_user": g(p1 + "identity"),
        "eap_pass": g(p1 + "password"),
        "acl_on": _model_bool(cfg, "wireless.1.mac_acl.status", False),
        "acl_policy": "allow" if g("wireless.1.mac_acl.policy") is None
        else g("wireless.1.mac_acl.policy"),
        "acl_rules": [{"idx": n, "on": _v8_on(cfg, f"wireless.1.mac_acl.{n}.status"),
                       "mac": g(f"wireless.1.mac_acl.{n}.mac"),
                       "comment": g(f"wireless.1.mac_acl.{n}.comment")}
                      for n in _indices(cfg, "wireless.1.mac_acl")],
        "acl_rebuild": False,
        "deauth": _int(g("radio.1.polling_daprot")) == 1,
    })
    # Wireless_Advanced
    sens = _v8_num(cfg, "radio.1.rx_sensitivity", -96, True)
    rate = g("wireless.1.rate.mcs")
    st.update({
        "ampdu": _v8_num(cfg, "wireless.1.ampdu.frames", 32),
        "sta_priority": _v8_num(cfg, "radio.1.pollingpri", 2, True),
        "isolation": _model_bool(cfg, "wireless.1.l2_isolation", False),
        "mcast_enh": _v8_num(cfg, "wireless.1.mcast.enhance", 1, True),
        "cmsbias": _v8_num(cfg, "radio.1.cmsbias", 0),
        "rx_sens_on": sens > -96, "rx_sens": sens,
        "atpc": _model_bool(cfg, "radio.1.atpc.status", False),
        "atpc_sta": _model_bool(cfg, "radio.1.atpc.sta.status", True),
        "atpc_target": _v8_num(cfg, "radio.1.atpc.threshold", ATPC_TARGET[1]),
        "amsdu": _v8_num(cfg, "wireless.1.amsdu", 3),
        "rate": "2" if rate == "0" else (rate or "-1"),
        "rate_module": _v8_num(cfg, "radio.1.rc_mode", 0),
        "tdd_rese": bool(_int(g("radio.1.polling_ff_sta_rx_rssi_th"))),
        "radar_change": _int(g("radio.1.radarmode")) == 1,
    })
    # WirelessBackupSsid (the station's second profile)
    p2 = "wpasupplicant.profile.1.network.2."
    mgmt2 = g(p2 + "key_mgmt.1.name") or "NONE"
    on2 = ((g("wpasupplicant.status") or "disabled") == "enabled"
           and (g("wpasupplicant.device.1.status") or "enabled") == "enabled"
           and mgmt2 != "NONE")
    st.update({
        "bk_security": "wpa2" if on2 else "none",
        "bk_wpa_auth": g(p2 + "key_mgmt.1.name") or "WPA-PSK",
        "bk_psk": g(p2 + "psk") or "",
        "bk_anon": g(p2 + "anonymous_identity"), "bk_user": g(p2 + "identity"),
        "bk_pass": g(p2 + "password"),
        "bk_on": _model_bool(cfg, p2 + "status", False),
        "bk_ssid": "" if g(p2 + "ssid") is None else g(p2 + "ssid"),
        "bk_mac": "" if g(p2 + "bssid") is None else g(p2 + "bssid"),
    })
    return st


def v8_ieee_mode(st: dict, board: dict) -> str:
    """What the ieeeMode cfgMap writes: "auto" for the automatic widths of single-band
    boards, if not the band and the width (20, 40 or 80)."""
    width = st["chanbw"]
    has_a, has_g = v8_bands(board)
    dual = has_a and has_g
    if width == 0 and not dual:
        return "auto"
    base = st["ieee"] if dual else ("11nght" if has_g else "11acvht")
    if width == 0:          # dual band: the widest width the list starts with
        return base + ("80" if st["ptp"] and base == "11acvht" else "40")
    return base + (str(width) if width in (20, 80) else "20" if width < 40 else "40")


def _v8_ebtables(c: Config, st: dict) -> None:
    """The network helper on every save: the bridge's ARP-NAT (station without WDS) and
    EAPOL (WPA, or WPA in the backup profile) entries, rebuilt."""
    for k in [k for k in c.keys() if k.startswith(("ebtables.sys.arpnat.",
                                                     "ebtables.sys.eap."))]:
        c.delete(k)
    bridges = [b for b in _indices(c, "bridge") if c.get(f"bridge.{b}.devname")]
    ports = [c.get(f"bridge.{b}.port.{p}.devname") or ""
             for b in bridges for p in _indices(c, f"bridge.{b}.port")]
    wireless = [p for p in ports if p.startswith(("ath", "wlan"))]
    wpa = st["security"] == "wpa2" or st["bk_security"] == "wpa2"
    for name, cond, devs in (("arpnat", not st["ap"] and not st["wds"], wireless),
                             ("eap", wpa, [p for p in wireless if "." not in p])):
        if cond and devs:
            c.set("ebtables.status", "enabled")
            c.set("ebtables.sys.status", "enabled")
            c.set(f"ebtables.sys.{name}.status", "enabled")
            for j, dev in enumerate(devs, 1):
                c.set(f"ebtables.sys.{name}.{j}.status", "enabled")
                c.set(f"ebtables.sys.{name}.{j}.devname", dev)


def _v8_wireless_save(c: Config, st: dict, board: dict) -> None:
    def put(key, value):
        _v8_put(c, key, value)
    ap, ptp = st["ap"], st["ptp"]
    sta, tdd = not ap, ap and not ptp
    ssid = _uesc(st["ssid"])
    if st.get("acl_rebuild"):           # the MAC ACL dialog: removeSections("wireless.1.mac_acl")
        for k in [k for k in c.keys() if k.startswith("wireless.1.mac_acl.")]:
            c.delete(k)
    # WirelessBasic
    put("wireless.1.ssid", ssid)
    put("wpasupplicant.profile.1.network.1.ssid", ssid)
    put("wireless.1.wds.status", "enabled" if ptp or st["wds"] else "disabled")
    put("wpasupplicant.profile.1.network.1.bssid", st["ap_mac"])
    if st["chanbw"] != CHANBW_AUTO_AP:
        put("radio.1.chanbw", st["chanbw"])
    put("radio.1.scanbw.status", _b(sta and st["auto_chanbw"]))
    put("radio.1.antenna.gain", st["gain"])
    put("radio.1.cable.loss", st["loss"])
    if (_int(board.get("radio.1.antennas")) or 0) > 0:
        put("radio.1.antenna.id", st["antenna"])
    put("radio.1.txpower", st["power"])
    put("radio.1.freq", 0 if st["center"] == 0 else st["freq"])
    put("radio.1.center.1.freq", 0 if st["center"] == -1 else st["center"])
    put("radio.1.mode", "master" if ap else "managed")
    put("radio.1.ptpmode", "1" if ptp else "0")
    if ap:
        put("radio.1.polling_11ac_11n_compat", "0" if ptp or st["chanbw"] == 30 else "1")
    put("radio.1.ieee_mode", v8_ieee_mode(st, board))
    put("radio.1.scan_list.status", _b(st["scan_on"] and not (sta and st["auto_chanbw"])))
    put("radio.1.scan_list.channels", ",".join(map(str, st["scan"])))
    put("radio.1.reg_obey", _b(st["obey"]))
    put("radio.1.ack.auto", _b(st["ack_auto"]))
    put("radio.1.ackdistance", st["distance"])
    put("radio.1.acktimeout", js_round(V8_MIN_ACK + int(st["distance"]) / LIGHT_HALF))
    put("radio.countrycode", st["country"])
    put("radio.1.countrycode", st["country"])
    put("radio.1.polling_ff_flex", st["tdd_flex"] if tdd else 0)
    duration, ratio = (st["tdd_duration"], st["tdd_ratio"]) if tdd else (0, 50)
    put("radio.1.polling_ff_dur", 0 if duration == 1 else duration)
    put("radio.1.polling_ff_dl_ratio", ratio)
    if "radio.1.polling_11ax_compat" in c:      # only AX-capable devices have it
        can = tdd and st["chanbw"] in (20, 40) and st["tdd_duration"] != 0
        put("radio.1.polling_11ax_compat", _b(st["ax_compat"]) if can else None)
    put("wireless.1.hide_ssid", _b(st["hide_ssid"]))
    # WirelessSecurity
    auth, psk, p1 = st["wpa_auth"], st["psk"], "wpasupplicant.profile.1.network.1."
    # (accounting goes with a RADIUS server: an access point with a shared key turns it off)
    psk_ap = ap and st["security"] != "none" and auth == "WPA-PSK"
    if ap and st["security"] != "none":
        for key, value in (("aaa.status", "enabled"), ("aaa.1.status", "enabled"),
                           ("aaa.1.wpa.mode", 2), ("aaa.1.wpa.1.pairwise", "CCMP"),
                           ("aaa.1.devname", "ath0"), ("aaa.1.ssid", ssid),
                           ("aaa.1.wpa.key.1.mgmt", auth)):
            put(key, value)
        if auth == "WPA-PSK":
            put("aaa.1.radius.auth.1.status", "disabled")
            put("aaa.1.radius.acct.1.status", "disabled")
            put("aaa.1.wpa.psk", psk)
            put(p1 + "psk", psk)
        else:                             # (its servers: below, as their fields keep them)
            put("aaa.1.radius.auth.1.status", "enabled")
    else:
        put("aaa.status", "disabled")
        put("aaa.1.status", "disabled")
    if sta:
        for key, value in (("wpasupplicant.status", "enabled"),
                           ("wpasupplicant.device.1.status", "enabled"),
                           ("wpasupplicant.device.1.profile", "AUTO"),
                           ("wpasupplicant.device.1.devname", "ath0"),
                           ("wpasupplicant.profile.1.name", "AUTO"), (p1 + "ssid", ssid),
                           (p1 + "priority", "100"), (p1 + "bssid", st["ap_mac"])):
            put(key, value)
        if st["security"] == "none":
            put(p1 + "key_mgmt.1.name", "NONE")
        else:
            put(p1 + "pairwise.1.name", "CCMP")
            put(p1 + "proto.1.name", "RSN")
            put(p1 + "key_mgmt.1.name", auth)
            if auth == "WPA-PSK":
                put(p1 + "psk", psk)
                put("aaa.1.wpa.psk", psk)
            if auth == "WPA-EAP":
                put(p1 + "eap.1.status", "enabled")
                put(p1 + "eap.1.name", "TTLS PEAP")
                put(p1 + "phase2", "auth=MSCHAPV2")
                put(p1 + "anonymous_identity", st["eap_anon"])
                put(p1 + "identity", st["eap_user"])
                put(p1 + "password", st["eap_pass"])
            else:
                put(p1 + "eap.1.status", "disabled")
    else:
        put("wpasupplicant.device.1.status", "disabled")
        put("wpasupplicant.status", "disabled")
    put("aaa.1.radius.auth.1.ip", st["radius_ip"])
    put("aaa.1.radius.auth.1.port", st["radius_port"])
    put("aaa.1.radius.auth.1.secret", st["radius_secret"])
    put("aaa.1.radius.acct.1.status", _b(st["acct_on"] and not psk_ap))
    put("aaa.1.radius.acct.1.ip", st["acct_ip"])
    put("aaa.1.radius.acct.1.port", st["acct_port"])
    put("aaa.1.radius.acct.1.secret", st["acct_secret"])
    put(p1 + "anonymous_identity", st["eap_anon"])
    put(p1 + "identity", st["eap_user"])
    put(p1 + "password", st["eap_pass"])
    put("wireless.1.mac_acl.status", _b(st["acl_on"]) if ap else "disabled")
    put("wireless.1.mac_acl.policy", st["acl_policy"])
    put("radio.1.polling_daprot", "1" if ap and st["deauth"] else None)
    for rule in st["acl_rules"]:                    # MacAclRule models
        p = f"wireless.1.mac_acl.{rule['idx']}."
        put(p + "status", _b(rule["on"]))
        put(p + "mac", rule["mac"])
        put(p + "comment", rule["comment"])
    # Wireless_Advanced
    put("radio.1.cmsbias", st["cmsbias"] if tdd and not st["tdd_duration"] > 0 else 0)
    put("wireless.1.ampdu.frames", st["ampdu"])
    put("wireless.1.ampdu.status", "enabled")
    put("radio.1.pollingpri", st["sta_priority"])
    put("wireless.1.l2_isolation", _b(st["isolation"]))
    put("wireless.1.mcast.enhance", st["mcast_enh"] if tdd else 0)
    put("radio.1.rx_sensitivity", st["rx_sens"] if st["rx_sens_on"] else -96)
    put("radio.1.atpc.status", _b(st["atpc"]) if ap else "disabled")
    put("radio.1.atpc.sta.status", _b(st["atpc_sta"]))
    put("radio.1.atpc.threshold", st["atpc_target"])
    put("wireless.1.amsdu", st["amsdu"])
    put("wireless.1.rate.mcs", st["rate"])
    put("wireless.1.rate.auto", "enabled")
    put("radio.1.rc_mode", st["rate_module"])
    put("radio.1.polling_ff_sta_rx_rssi_th", ("1" if st["tdd_rese"] else "0") if tdd else "0")
    if tdd and st["country"] in ETSI_COUNTRIES:
        put("radio.1.radarmode", "1" if st["radar_change"] else "0")
    # WirelessBackupSsid
    p2 = "wpasupplicant.profile.1.network.2."
    put(p2 + "priority", 2)
    if st["bk_security"] == "wpa2":
        put(p2 + "proto.1.name", "RSN")
        put(p2 + "pairwise.1.name", "CCMP")
        put(p2 + "key_mgmt.1.name", st["bk_wpa_auth"])
        if st["bk_wpa_auth"] == "WPA-PSK":
            put(p2 + "psk", st["bk_psk"])
        if st["bk_wpa_auth"] == "WPA-EAP":
            put(p2 + "eap.1.status", "enabled")
            put(p2 + "eap.1.name", "TTLS PEAP")
            put(p2 + "phase2", "auth=MSCHAPV2")
        else:
            put(p2 + "eap.1.status", "disabled")
    else:
        put(p2 + "key_mgmt.1.name", "NONE")
        for k in ("psk", "pairwise.1.name", "proto.1.name"):
            c.delete(p2 + k)
    put(p2 + "anonymous_identity", st["bk_anon"])
    put(p2 + "identity", st["bk_user"])
    put(p2 + "password", st["bk_pass"])
    put(p2 + "status", _b(st["bk_on"]))
    if st["bk_on"] and sta:
        put("wpasupplicant.status", "enabled")
        put("wpasupplicant.device.1.devname", "ath0")
        put("wpasupplicant.device.1.status", "enabled")
    put(p2 + "ssid", _uesc(st["bk_ssid"]))
    put(p2 + "bssid", _uesc(st["bk_mac"]))
    _v8_ebtables(c, st)


# -- airOS 6 --------------------------------------------------------------------------

def _v6_wmode(cfg: Config) -> str:
    """cfg_get_wmode(): ap, aprepeater (WDS with peers or automatic WDS) or sta."""
    master = _cfg_def(cfg, "radio.1.mode", "managed").lower() == "master"
    if master and _cfg_def(cfg, "wireless.1.wds.status", "disabled") == "enabled":
        peers = [cfg.get(f"wireless.1.wds.{i}.peer") or "" for i in range(1, 7)]
        return "aprepeater" if cfg.get("wireless.1.autowds") == "enabled" or any(peers) else "ap"
    return "ap" if master else "sta"


def v6_band_a(cfg: Config, board: dict) -> bool:
    """A 5 GHz radio ($radio["ieee_mode_a"]): from board.info if it says, if not from the
    mode or the frequency in use."""
    flag = board.get("radio.1.ieee_mode_a")
    if flag is not None:
        return flag == "1"
    ieee = (cfg.get("radio.1.ieee_mode") or "").lower()
    if ieee.startswith("11na") or ieee == "a":
        return True
    if ieee.startswith("11ng") or ieee in ("b", "g"):
        return False
    return (_int(cfg.get("radio.1.freq")) or 5000) > 4000


def _v6_country(cfg: Config, board: dict) -> int:
    """cfg_get_country(): the board's if it is fixed; 2000 + code for "licensed" ones."""
    board_cc = _int(board.get("radio.1.ccode"))
    if _int(board.get("radio.1.ccode_locked")) == 1 or _int(board.get("radio.1.ccode_fixed")) == 1:
        cc = board_cc or 840
    else:
        cc = _int(_cfg_def(cfg, "radio.countrycode", str(board_cc or 840))) or 0
    if (_int(_cfg_def(cfg, "radio.countrylicense", "0")) or 0) == cc:
        cc += 2000
    return cc


def _v6_wireless_state(cfg: Config, board: dict) -> dict:
    def d(key, default=""):
        return _cfg_def(cfg, key, default)
    wmode = _v6_wmode(cfg)
    ap = wmode != "sta"
    band_a = v6_band_a(cfg, board)
    if wmode == "aprepeater" and _v6_wpa_in_cfg(cfg):
        wmode = "ap"                 # link.cgi shows it as an access point (WPA has no WDS peers)
    full = d("radio.1.ieee_mode", "11nght").lower()
    ieee = full[:6] if len(full) > 6 else full
    if ieee not in ("11naht", "11nght"):
        ieee = "11naht" if band_a else "11nght"
    # Without one (as its factory configuration), link.cgi asks the radio (iwpriv
    # get_chanbw): an access point's width is its mode's; a station's 20 it shows as 40
    # (Automatic 20/40).
    chanbw = _int(d("radio.1.chanbw")) or (40 if full[6:8] == "40" or not ap else 20)
    cc = _v6_country(cfg, board)
    antenna, gain = v6_active_antenna(cfg, board)
    builtin = any(a["id"] == antenna and a["builtin"] for a in board_antennas(board))
    has_builtin = any(a["builtin"] for a in board_antennas(board))
    fixed = _int(board.get("radio.1.ccode_fixed")) == 1
    obey_default = "disabled" if fixed or not has_builtin else "enabled"
    eirp = d("system.eirp.status")
    first_run = eirp == ""
    if first_run:
        eirp = "enabled" if not has_builtin else "disabled"

    def obey_of(default):
        return d("radio.1.obey", default) if d("radio.1.reg_obey", "disabled") == "enabled" \
            else "disabled"
    obey = obey_of(obey_default)
    if first_run and not has_builtin:
        obey = "disabled"
    if cc == 764:                                  # force_obey_regulatory()
        obey = "enabled"
    dfs = d("radio.1.dfs.status", "dummy")
    if dfs in ("dummy", ""):
        dfs = obey_of(obey_default)
    distance = d("radio.1.ackdistance")
    if distance == "":                   # cfg_get_ackdistance(), from its ACK timeout
        base = v6_ack_timings(full, chanbw, _v6_fast_clock(board))[0]
        ack = (_int(d("radio.1.acktimeout", "0")) or 0) - base
        distance = ack * LIGHT_HALF if ack > 0 else 0
    power = _int(d("radio.1.txpower")) or 0
    hw_max = _int(board.get("radio.1.txpower.max"))
    if hw_max is not None and power > hw_max:
        power = hw_max
    sec = d("wireless.1.security.type", "none")
    if sec[:3] == "wep":
        sec = "wep"
    else:
        if ap:
            on = (d("aaa.status", "disabled") == "enabled"
                  and d("aaa.1.status", "enabled") == "enabled")
            proto, pairwise = d("aaa.1.wpa.mode", "1"), d("aaa.1.wpa.1.pairwise", "TKIP CCMP")
        else:
            on = (d("wpasupplicant.status", "disabled") == "enabled"
                  and d("wpasupplicant.device.1.status", "enabled") == "enabled")
            proto = d("wpasupplicant.profile.1.network.1.proto.1.name", "WPA")
            pairwise = d("wpasupplicant.profile.1.network.1.pairwise.1.name", "TKIP CCMP")
        if on and proto not in ("0", ""):
            sec = "wpa2" if proto in ("RSN", "2") else "wpa"
            sec += {"TKIP": "tkip", "CCMP": "aes"}.get(pairwise, "")
    p1 = "wpasupplicant.profile.1.network.1."
    if ap:
        auth = d("aaa.1.wpa.key.1.mgmt", "WPA-PSK")
        psk = d("aaa.1.wpa.psk", d(p1 + "psk", ""))
    else:
        auth = d(p1 + "key_mgmt.1.name", "WPA-PSK")
        psk = d(p1 + "psk", d("aaa.1.wpa.psk", ""))
    if auth not in ("WPA-PSK", "WPA-EAP"):     # what the select can send
        auth = "WPA-PSK"
    rules = []
    for n in _indices(cfg, "wireless.1.mac_acl"):       # ubnt.cfg.js: invalid ones go away
        mac = cfg.get(f"wireless.1.mac_acl.{n}.mac") or ""
        if MAC_RE.match(mac):
            rules.append({"idx": n, "mac": mac,
                          "on": d(f"wireless.1.mac_acl.{n}.status", "enabled") == "enabled",
                          "comment": d(f"wireless.1.mac_acl.{n}.comment")})
    sens = d("wireless.1.sens", "0")
    rx = -96 if sens == "" else -96 + (_int(sens) or 0)
    polling = d("radio.1.polling") == "enabled"
    return {
        "wmode6": wmode, "ap": ap, "band_a": band_a,
        "wds": d("wireless.1.wds.status", "disabled") == "enabled",
        "macclone": not ap and d("wireless.1.macclone") == "enabled"
        and net_mode(cfg) == "bridge",
        "autowds": cfg.get("wireless.1.autowds") == "enabled",
        "peers": [cfg.get(f"wireless.1.wds.{i}.peer") or "" for i in range(1, 7)],
        "ssid": d("wireless.1.ssid", "UBNT"),
        "hide_ssid": d("wireless.1.hide_ssid") == "enabled",
        "hideindoor": band_a and (cc in ETSI_COUNTRIES or cc == 756)
        and d("wireless.hideindoor.status") == "enabled",
        "ap_mac": d("wireless.1.ap"),
        "country": cc, "ieee": ieee, "chanbw": chanbw,
        "ext": full[8:] if full[6:8] == "40" and full[8:] in ("plus", "minus") else "",
        "freq": _int(d("radio.1.freq")) or 0,
        "scan_on": d("wireless.1.scan_list.status", "disabled") == "enabled",
        "scan": _freqs(d("wireless.1.scan_list.channels")),
        "antenna": antenna, "gain": gain,
        "loss": 0 if builtin else (_int(d("radio.1.cable.loss", "0")) or 0),
        "power": power, "obey": obey == "enabled", "eirp_status": eirp,
        "dfs_on": dfs == "enabled",
        "distance": _int(distance) or 0,
        "ack_auto": d("radio.1.ack.auto", "disabled") == "enabled",
        "rate": d("radio.1.rate.mcs", "15"), "rate_auto": d("radio.1.rate.auto", "enabled"),
        "mcast_rate": _int(d("radio.1.mcastrate")) or 0,
        "authtype": d("wireless.1.authmode", "1"),
        "security": sec, "wpa_auth": auth, "psk": psk,
        "wep_key_length": "wep64" if d("wireless.1.security.type", "wep64") == "none"
        else d("wireless.1.security.type", "wep64"),
        "radius_ip": d("aaa.1.radius.auth.1.ip"),
        "radius_port": d("aaa.1.radius.auth.1.port", "1812"),
        "radius_secret": d("aaa.1.radius.auth.1.secret", "secret"),
        "acct_on": d("aaa.1.radius.acct.1.status") == "enabled",
        "acct_ip": d("aaa.1.radius.acct.1.ip"),
        "acct_port": d("aaa.1.radius.acct.1.port", "1813"),
        "acct_secret": d("aaa.1.radius.acct.1.secret", "secret"),
        "radius_mac_on": d("aaa.1.radius.macacl.status") == "enabled",
        "radius_mac_format": d("aaa.1.radius.macacl.format"),
        "radius_mac_pass": d("aaa.1.radius.macacl.password.status") or "enabled",
        "eap_method": d(p1 + "eap.1.name", "TTLS") if not ap else "TTLS",
        "eap_inner": d(p1 + "phase2", "auth=MSCHAPV2"),
        "eap_user": d(p1 + "identity"),
        "eap_anon": d(p1 + "anonymous_identity", "anonymous@myisp.com"),
        "eap_pass": d(p1 + "password"),
        "acl_on": d("wireless.1.mac_acl.status", "disabled") == "enabled",
        "acl_policy": d("wireless.1.mac_acl.policy", "allow"), "acl_rules": rules,
        # advanced.cgi
        "rts": None if d("radio.1.rts", "off") in ("off", "") else _int(d("radio.1.rts")),
        "aggr_on": d("radio.1.ampdu.status", "enabled") == "enabled",
        "aggr_frames": _int(d("radio.1.ampdu.frames", "32")) or 32,
        "aggr_bytes": _int(d("radio.1.ampdu.bytes", "50000")) or 50000,
        "mcast_data": d("netconf.2.allmulti", "enabled") == "enabled",
        "isolation": d("wireless.1.l2_isolation", "disabled") == "enabled",
        "mcast_enh": d("wireless.1.mcast.enhance", "2") != "0",
        "rx_sens": None if rx <= -96 else rx,
        "autoack_disabled": polling and ap and (d("radio.1.pollingnoack", "0") == "1"
                                                or d("radio.1.airsync.status",
                                                     "disabled") == "enabled"),
        # ubnt.cgi
        "polling": polling, "airselect": (_int(d("radio.1.polling_fh", "0")) or 0) != 0,
        "sta_priority": d("radio.1.pollingpri"),
    }


def _v6_wpa_in_cfg(cfg: Config) -> bool:
    """An access point with WPA (cfg_get_security() of an AP)."""
    if _cfg_def(cfg, "wireless.1.security.type", "none")[:3] == "wep":
        return False
    return (_cfg_def(cfg, "aaa.status", "disabled") == "enabled"
            and _cfg_def(cfg, "aaa.1.status", "enabled") == "enabled"
            and _cfg_def(cfg, "aaa.1.wpa.mode", "1") not in ("0", ""))


def _v6_any_on(c: Config, pattern: str) -> str:
    """get_wpasupplicant_status() / get_aaa_status(): enabled if radio 1 or 2 is."""
    return _b(any(_cfg_def(c, pattern % i, "disabled") == "enabled" for i in (1, 2)))


def v6_ack_timings(mode: str, chanbw: int, fast_clock: bool) -> tuple[int, int]:
    """get_timings() of airOS 6: (the ACK timeout of distance 0, the largest one), in µs, for
    an ieee_mode and a channel width. fast_clock: a 5 GHz radio with the clock of 2.4 GHz
    (fast_clock_mode() gives it those timings)."""
    if fast_clock:
        mode = ("11nght40" if mode in ("11naht40minus", "11naht40plus", "11naht40")
                else "11nght20" if mode in ("1", "a", "11naht20") else mode)
    narrow = 5 if 0 < chanbw <= 5 else 10 if 0 < chanbw <= 10 else 0
    slot, top = 9, 204                   # (204 also for HT40 at 5 GHz and the turbo modes)
    if mode in ("1", "a", "11naht20"):
        top = {5: 1638, 10: 819}.get(narrow, 409)
    elif mode in ("2", "b"):
        slot, top = 20, 744              # (long slot)
    elif mode in ("11nght40minus", "11nght40plus", "11nght40"):
        top = 186
    elif mode in ("3", "gt", "g", "pureg", "11nght20"):
        top = {5: 1489, 10: 744}.get(narrow, 372)
    return {5: -5 + 80, 10: -5 + 40}.get(narrow, slot + 4) + 10, top


def _v6_fast_clock(board: dict) -> bool:
    """Its radio has the fast clock; without its capabilities, as if it had it (the shorter
    timings: no distance the web might not allow)."""
    caps = _int(board.get(V6_CAPS_KEY))
    return caps is None or bool(caps & V6_CAP_FAST_CLOCK)


def _v6_full_mode(st: dict) -> str:
    """The radio.1.ieee_mode that saving the page writes (11naht20, 11naht40plus…)."""
    ieee = st["ieee"]
    if len(ieee) == 6:
        ieee += (("40" + (st["ext"] if st["wmode6"] != "sta" else "")) if st["chanbw"] == 40
                 else "20")
    return ieee


def v6_max_distance(st: dict, board: dict) -> int:
    """The farthest distance airOS 6's Advanced page lets you set, in metres. Its slider goes
    from the ACK timeout of distance 0 to the largest one (get_timings(), with the mode
    cfg_get_ieee_mode() reads), 150 m per microsecond, or up to the board's own distance
    limit; it saves multiples of 150 m."""
    caps = _int(board.get(V6_CAPS_KEY))
    mode = _v6_full_mode(st)
    if _int(board.get("radio.1.ccode_fixed")) == 1 and caps is not None \
            and caps & V6_CAP_NO_HT40:
        mode = st["ieee"][:6]            # (11naht or 11nght: get_timings() gives them 204)
    base, top = v6_ack_timings(mode, st["chanbw"], _v6_fast_clock(board))
    km = _int(board.get("radio.1.distance_limit_km"))
    if km:
        top = base + 2 * km * 1000 / 300 + 0.5
    return js_round(max(0.0, top - base)) * LIGHT_HALF


def _v6_put_ack(c: Config, distance: int, ieee: str) -> None:
    """set_ack_distance(): the timeout grows a microsecond every 150 m, from 21 µs (43 in
    802.11b: its long slot)."""
    c.set("radio.1.ackdistance", str(distance))
    c.set("radio.1.acktimeout", str((43 if ieee in ("b", "2") else 21)
                                    + int(distance) // LIGHT_HALF))


def _v6_save_link(c: Config, st: dict, board: dict) -> None:
    """The POST of link.cgi (the Wireless page) with the whole form."""
    put = c.set
    wmode = st["wmode6"]
    ap = wmode != "sta"
    ieee = _v6_full_mode(st)
    wds = wmode == "aprepeater" or st["wds"]
    macclone = wmode == "sta" and not st["wds"] and st["macclone"]
    if wds or macclone or ap:                      # set_wmode()
        put("ebtables.sys.arpnat.status", "disabled")
    else:
        put("ebtables.sys.status", "enabled")
        put("ebtables.sys.arpnat.status", "enabled")
    put("wireless.1.wds.status", _b(wds))
    put("radio.1.mode", "master" if ap else "managed")
    if ap:
        put("radio.1.freq", str(st["freq"]))
    put("wireless.1.ssid", st["ssid"])
    cc = st["country"]                             # set_country()
    if cc == 2124:
        cc = 124
        put("radio.countrylicense", "124")
    else:
        put("radio.countrylicense", "0")
    put("radio.countrycode", str(cc))
    put("radio.1.countrycode", str(cc))
    put("radio.1.subsystemid", board.get("radio.1.subsystemid") or "")
    obey = _b(st["obey"])                          # set_obey()
    c.delete("radio.1.regulatory")
    put("radio.1.reg_obey", obey)
    put("radio.1.obey", obey)
    put("system.eirp.status", st["eirp_status"])
    power, lo = int(st["power"]), _int(board.get("radio.1.txpower.min"))
    put("radio.1.txpower", str(lo if lo is not None and power < lo else power))
    put("radio.1.cwm.mode", ("1" if wmode == "sta" else "2") if "ht40" in ieee else "0")
    put("radio.1.forbiasauto", "1" if "11ng" in ieee else "0")
    put("radio.1.cwm.enable", "0")
    put("radio.1.ieee_mode", ieee)
    if st["band_a"]:
        forced = st["country"] in ETSI_COUNTRIES or st["country"] == 756   # (and Switzerland)
        put("radio.1.dfs.status", _b(forced or st["dfs_on"]))
    _v6_put_ack(c, st["distance"], ieee)
    put("radio.1.rate.auto", st["rate_auto"] or "disabled")
    put("radio.1.rate.mcs", str(st["rate"]))
    put("radio.1.mcastrate", str(st["mcast_rate"]))
    put("radio.1.chanbw", str(st["chanbw"]))
    put("wireless.1.authmode", str(st["authtype"]) or "1")
    put("radio.1.antenna.id", str(st["antenna"]))
    put("radio.1.antenna.gain", str(st["gain"]))
    put("radio.1.cable.loss", str(st["loss"]))
    put("wireless.1.scan_list.status", _b(st["scan_on"]))
    put("wireless.1.scan_list.channels", ",".join(map(str, st["scan"])))
    put("wireless.hideindoor.status", _b(st["hideindoor"]))
    if wmode == "sta":
        put("wireless.1.ap", st["ap_mac"])
        put("wireless.1.hide_ssid", "disabled")
    else:
        put("wireless.1.ap", "")
        put("wireless.1.hide_ssid", _b(st["hide_ssid"]))
        repeater = wmode == "aprepeater"
        put("wireless.1.autowds", _b(repeater and st["autowds"]))
        for i in range(1, 7):
            put(f"wireless.1.wds.{i}.peer", st["peers"][i - 1] if repeater else "")
        put("wireless.1.mac_acl.status", _b(st["acl_on"]))
        put("wireless.1.mac_acl.policy", "deny" if st["acl_policy"] == "deny" else "allow")
    _v6_put_security(c, st)


def _v6_put_security(c: Config, st: dict) -> None:
    """set_security() and then set_wpa_ap(), set_wpa_sta() or set_radius_macacl()."""
    put, sec, ap = c.set, st["security"], st["wmode6"] != "sta"
    p1 = "wpasupplicant.profile.1.network.1."
    if sec in ("wep", "wep64", "wep128"):
        put("wireless.1.security.type", st["wep_key_length"] if sec == "wep" else sec)
        put("wpasupplicant.device.1.status", "disabled")
        put("wpasupplicant.status", _v6_any_on(c, "wpasupplicant.device.%d.status"))
        put("aaa.1.status", "disabled")
        put("aaa.status", _v6_any_on(c, "aaa.%d.status"))
        return               # the WEP keys go as they are (airos-tui does not offer WEP)
    if not sec.startswith("wpa"):
        put("wireless.1.security.type", "none")
        put("aaa.1.status", "disabled")
        put("aaa.status", _v6_any_on(c, "aaa.%d.status"))
        put("wpasupplicant.device.1.status", "disabled")
        put("wpasupplicant.status", _v6_any_on(c, "wpasupplicant.device.%d.status"))
        put("ebtables.sys.eap.status", "disabled")
        if ap:
            _v6_radius_macacl(c, st)
        return
    pairwise, wpa2 = V6_PAIRWISE.get(sec, ""), sec.startswith("wpa2")
    put("ebtables.sys.status", "enabled")
    put("ebtables.sys.eap.status", "enabled")
    put("ebtables.sys.eap.1.status", "enabled")
    put("ebtables.sys.eap.1.devname", "ath0")
    ssid = _cfg_def(c, "wireless.1.ssid", "UBNT")
    auth = st["wpa_auth"] or "WPA-PSK"
    if ap:
        put("wpasupplicant.device.1.status", "disabled")
        put("wpasupplicant.status", _v6_any_on(c, "wpasupplicant.device.%d.status"))
        put("wireless.1.security.type", "none")
        put("aaa.status", "enabled")
        put("aaa.1.status", "enabled")
        put("aaa.1.wpa.mode", "2" if wpa2 else "1")
        put("aaa.1.wpa.1.pairwise", pairwise)
        put("aaa.1.driver", "madwifi")
        put("aaa.1.devname", "ath0")
        put("aaa.1.br.devname", "br0")
        put("aaa.1.ssid", ssid)
        put("aaa.1.wpa.key.1.mgmt", auth)              # set_wpa_ap()
        if auth == "WPA-PSK":
            put("aaa.1.radius.auth.1.status", "disabled")
            put("aaa.1.radius.acct.1.status", "disabled")
            put("aaa.1.wpa.psk", st["psk"])
            put(p1 + "psk", st["psk"])
        else:
            put("aaa.1.radius.auth.1.status", "enabled")
            put("aaa.1.radius.auth.1.ip", st["radius_ip"])
            put("aaa.1.radius.auth.1.port", str(st["radius_port"]))
            put("aaa.1.radius.auth.1.secret", st["radius_secret"])
            if st["acct_on"]:
                put("aaa.1.radius.acct.1.status", "enabled")
                put("aaa.1.radius.acct.1.ip", st["acct_ip"])
                put("aaa.1.radius.acct.1.port", str(st["acct_port"]))
                put("aaa.1.radius.acct.1.secret", st["acct_secret"])
            else:
                put("aaa.1.radius.acct.1.status", "disabled")
        put("aaa.1.radius.macacl.status", "disabled")
        return
    put("aaa.1.status", "disabled")
    put("aaa.status", _v6_any_on(c, "aaa.%d.status"))
    put("wireless.1.security.type", "none")
    put("wpasupplicant.status", "enabled")
    put("wpasupplicant.device.1.status", "enabled")
    put("wpasupplicant.device.1.devname", "ath0")
    put("wpasupplicant.device.1.driver", "madwifi")
    put(p1 + "proto.1.name", "RSN" if wpa2 else "WPA")
    put(p1 + "pairwise.1.name", pairwise)
    put(p1 + "ssid", ssid)
    eap = auth != "WPA-PSK"           # set_wpa_sta(), with what the form sends: the fields of
    psk = "" if eap else st["psk"]    # the other kind are disabled and do not go
    put("wpasupplicant.device.1.profile", auth)
    put("wpasupplicant.profile.1.name", auth)
    put(p1 + "key_mgmt.1.name", auth)
    put(p1 + "bssid", st["ap_mac"])
    if psk:
        put(p1 + "psk", psk)
        put("aaa.1.wpa.psk", psk)
    if eap and st["eap_method"]:
        put(p1 + "eap.1.status", "enabled")
        put(p1 + "eap.1.name", st["eap_method"])
    else:
        put(p1 + "eap.1.status", "disabled")
    if eap and st["eap_user"]:
        put(p1 + "identity", st["eap_user"])
    if eap:
        put(p1 + "anonymous_identity", st["eap_anon"])
    if eap and st["eap_pass"]:
        put(p1 + "password", st["eap_pass"])
    if eap and st["eap_inner"]:
        put(p1 + "phase2", st["eap_inner"])


def _v6_radius_macacl(c: Config, st: dict) -> None:
    """set_radius_macacl(): the RADIUS MAC authentication of an access point without
    security (airos-tui keeps it as it is)."""
    put, on = c.set, st["radius_mac_on"]
    acct = on and st["acct_on"]
    put("aaa.1.radius.macacl.status", _b(on))
    put("aaa.1.radius.auth.1.status", _b(on))
    put("aaa.1.radius.acct.1.status", _b(acct))
    if not on:
        put("aaa.status", "disabled")
        put("aaa.1.status", "disabled")
        return
    put("aaa.status", "enabled")
    put("aaa.1.devname", "ath0")
    put("aaa.1.radius.macacl.format", st["radius_mac_format"])
    put("aaa.1.radius.macacl.password.status", st["radius_mac_pass"] or "enabled")
    put("aaa.1.status", "enabled")
    put("aaa.1.wpa.mode", "0")
    put("aaa.1.radius.auth.1.ip", st["radius_ip"])
    put("aaa.1.radius.auth.1.port", str(st["radius_port"]))
    put("aaa.1.radius.auth.1.secret", st["radius_secret"])
    if acct:
        put("aaa.1.radius.acct.1.ip", st["acct_ip"])
        put("aaa.1.radius.acct.1.port", str(st["acct_port"]))
        put("aaa.1.radius.acct.1.secret", st["acct_secret"])


def _v6_save_advanced(c: Config, st: dict, board: dict) -> None:
    """The POST of advanced.cgi, the parts airos-tui offers."""
    put = c.set
    put("radio.1.rts", "off" if st["rts"] is None else str(st["rts"]))
    if not st["autoack_disabled"]:
        _v6_put_ack(c, st["distance"], (c.get("radio.1.ieee_mode") or "").lower())
        put("radio.1.ack.auto", _b(st["ack_auto"]))
    if st["aggr_on"]:
        put("radio.1.ampdu.frames", str(st["aggr_frames"]))
        put("radio.1.ampdu.bytes", str(st["aggr_bytes"]))
    put("radio.1.ampdu.status", _b(st["aggr_on"]))
    put("netconf.2.allmulti", _b(st["mcast_data"]))
    put("wireless.1.l2_isolation", _b(st["isolation"]))
    if st["wmode6"] != "sta":
        put("wireless.1.mcast.enhance", "2" if st["mcast_enh"] else "0")
    put("wireless.1.sens", "0" if st["rx_sens"] is None else str(96 + st["rx_sens"]))


def _v6_save_airmax(c: Config, st: dict, board: dict) -> None:
    """The POST of ubnt.cgi (airMAX): the checkbox exists only for access points, and the
    priority only for stations."""
    c.set("radio.1.polling", _b(st["wmode6"] != "sta" and st["polling"]))
    if st["wmode6"] == "sta" and str(st["sta_priority"]) != "":
        c.set("radio.1.pollingpri", str(st["sta_priority"]))


def _v6_save_acl(c: Config, st: dict, board: dict) -> None:
    """macacl.cgi: the whole configuration goes back with the rules numbered from 1."""
    for k in [k for k in c.keys() if re.match(r"^wireless\.1\.mac_acl\.\d+\.", k)]:
        c.delete(k)
    rules = [r for r in st["acl_rules"] if MAC_RE.match(r.get("mac") or "")]
    for j, rule in enumerate(rules, 1):
        c.set(f"wireless.1.mac_acl.{j}.status", _b(rule["on"]))
        c.set(f"wireless.1.mac_acl.{j}.mac", rule["mac"])
        c.set(f"wireless.1.mac_acl.{j}.comment", rule.get("comment") or "")


V6_SAVERS = {"link": _v6_save_link, "advanced": _v6_save_advanced,
             "airmax": _v6_save_airmax, "acl": _v6_save_acl}


# -- both -------------------------------------------------------------------------------

def wireless_state(cfg: Config, flavor: str | None, board: dict) -> dict:
    """The Wireless page as the web shows it."""
    st = _v6_wireless_state(cfg, board) if flavor == "v6" else _v8_wireless_state(cfg, board)
    st["flavor"] = "v6" if flavor == "v6" else "v8"
    return st


def wireless_save(cfg: Config, flavor: str | None, board: dict, st: dict,
                  page: str = "link") -> Config:
    """cfg after saving the page with this state (airOS 8 saves everything at once)."""
    c = cfg.copy()
    if flavor == "v6":
        V6_SAVERS[page](c, st, board)
    else:
        _v8_wireless_save(c, st, board)
    return c


def _v6_page(key: str) -> str:
    return next((page for page, keys in V6_PAGES.items() if key in keys), "link")


_ACL_KEY_RE = re.compile(r"^wireless\.1\.mac_acl\.\d+\.")


def ssid_problem(value: str, flavor: str | None) -> str | None:
    """An SSID typed, as each web checks it (only its errors filter the rest of the page's:
    an SSID that was already wrong must not let an empty one through)."""
    return _v_ssid(_uesc(value) if flavor == "v8" else value)


def plan_wireless(cfg: Config, flavor: str | None, board: dict,
                  changes: dict) -> dict[str, str | None]:
    """The keys that change when saving the page with these changes (None = delete)."""
    base = wireless_state(cfg, flavor, board)
    new = dict(base, **changes)
    if "acl_rules" in changes:        # the MAC ACL dialog renumbers the rules from 1
        base = dict(base, acl_rebuild=True,
                    acl_rules=[dict(r, idx=i) for i, r in enumerate(base["acl_rules"], 1)])
        new.update(acl_rebuild=True,
                   acl_rules=[dict(r, idx=i) for i, r in enumerate(new["acl_rules"], 1)])
    pages = ["link"]
    if flavor == "v6":
        pages = sorted({_v6_page(k) for k in changes} or {"link"}, key=V6_PAGE_ORDER.index)
    out: dict[str, str | None] = {}
    acl = "acl_rules" in changes
    for page in pages:
        a = wireless_save(cfg, flavor, board, base, page)
        b = wireless_save(cfg, flavor, board, new, page)
        keys = set(a.keys()) | set(b.keys())
        if acl:
            keys |= {k for k in cfg.keys() if _ACL_KEY_RE.match(k)}
        for k in sorted(keys):
            if acl and _ACL_KEY_RE.match(k):
                # (its rules, numbered again from 1, against the device's own numbering:
                # with gaps, the same rule moves to another number)
                if b.get(k) != cfg.get(k):
                    out[k] = b.get(k)
            elif a.get(k) != b.get(k) and b.get(k) != cfg.get(k):
                out[k] = b.get(k)
    return out


# -- validation, as each web's (the power and the frequencies: check_config) ---------

def _text_ok(value, lo: int, hi: int) -> bool:
    return (isinstance(value, str) and lo <= len(value) <= hi
            and bool(re.fullmatch(r"[ -~]*", value)))


def width_number(chanbw: int) -> str:
    """A channel width as a text says it before "MHz": airOS 8's automatic one (a station's
    20/40/80) as the widths it picks from."""
    return "20/40/80" if chanbw == CHANBW_AUTO else str(chanbw)


def _v_eap(user, password, anon) -> list[str]:
    """Its checks of an enterprise (EAP) profile, the main one or the backup SSID's: the user,
    the password and, if there is one, the anonymous identity."""
    out = []
    if not _text_ok(user or "", 1, 63):
        out.append(tr("The user name must have between 1 and 63 printable ASCII characters."))
    if not _text_ok(password or "", 1, 63):
        out.append(tr("The password must have between 1 and 63 printable ASCII characters."))
    if anon and not _text_ok(anon, 1, 63):
        out.append(tr("The anonymous identity must have between 1 and 63 printable ASCII "
                      "characters."))
    return out


def _v_wpa_psk(value: str) -> str | None:
    """Both webs: from 8 to 63 printable ASCII characters (a 64-digit hexadecimal key is not
    accepted)."""
    if _text_ok(value, 8, 63):
        return None
    return tr("The WPA key must have between 8 and 63 printable ASCII characters.")


def _v_radius(st: dict, prefix: str, label: str) -> list[str]:
    out = []
    try:
        ipaddress.IPv4Address(str(st[f"{prefix}_ip"] or ""))
    except ValueError:
        out.append(tr("{server}: the IP is not a valid IPv4 address.", server=label))
    port = _int(st[f"{prefix}_port"])
    if port is None or not 1 <= port <= 65535:
        out.append(tr("{server}: the port must be between 1 and 65535.", server=label))
    if not _text_ok(st[f"{prefix}_secret"] or "", 1, 32):
        out.append(tr("{server}: the secret must have between 1 and 32 printable ASCII "
                      "characters.", server=label))
    return out


def wireless_errors(st: dict, board: dict) -> list[str]:
    """What the web would not let you save, besides the power and the frequencies."""
    out = []
    v8, ap = st["flavor"] == "v8", st["ap"]
    err = _v_ssid(_uesc(st["ssid"]) if v8 else st["ssid"])
    if err:
        out.append(err)
    wpa = st["security"] == "wpa2" if v8 else st["security"].startswith("wpa")
    if wpa and st["wpa_auth"] == "WPA-PSK":
        err = _v_wpa_psk(st["psk"])
        if err:
            out.append(err)
    elif wpa and ap:
        out += _v_radius(st, "radius", tr("Authentication server"))
        if st["acct_on"]:
            out += _v_radius(st, "acct", tr("Accounting server"))
    elif wpa:
        out += _v_eap(st["eap_user"], st["eap_pass"], st["eap_anon"])
    if not ap and st["ap_mac"] and not MAC_RE.match(st["ap_mac"]):
        out.append(tr('The AP MAC must be like 00:11:22:33:44:55.'))
    macs = [(r.get("mac") or "").upper() for r in st["acl_rules"]]
    if any(not MAC_RE.match(m) for m in macs):
        out.append(tr("Every rule of the MAC ACL needs a MAC like 00:11:22:33:44:55."))
    if len(set(macs)) != len(macs):
        out.append(tr("The MAC ACL has the same MAC twice."))
    if v8:
        top = NO_ACK_DISTANCE.get(st["chanbw"], 22500)
        # (with the automatic distance it is not used: its web's slider, which clamps it to
        # the new width's limit, does not stop a change of width there either)
        if not st["ack_auto"] and not 0 <= st["distance"] <= top:
            out.append(tr("With {width} MHz the distance must be between 0 and {km} km.",
                          width=width_number(st["chanbw"]), km=f"{top / 1000:g}"))
        if st["antenna"] == -1:
            out.append(tr("Choose the antenna."))
        if st["obey"] or st["antenna"] == 0:
            if not 0 <= st["gain"] <= 40:
                out.append(tr("The antenna gain must be a whole number from 0 to 40 dBi."))
            elif not 0 <= st["loss"] <= st["gain"]:
                out.append(tr("The cable loss must be from 0 dB to the antenna gain."))
        if not ap and st["bk_on"]:
            err = _v_ssid(_uesc(st["bk_ssid"]))
            if err:
                out.append(tr("Backup SSID: {error}", error=err))
            if st["bk_security"] == "wpa2" and st["bk_wpa_auth"] == "WPA-PSK":
                err = _v_wpa_psk(st["bk_psk"])
                if err:
                    out.append(tr("Backup SSID: {error}", error=err))
            elif st["bk_security"] == "wpa2":         # (enterprise: as its main profile)
                out += [tr("Backup SSID: {error}", error=e)
                        for e in _v_eap(st["bk_user"], st["bk_pass"], st["bk_anon"])]
            if st["bk_mac"] and not MAC_RE.match(st["bk_mac"]):
                out.append(tr("Backup SSID: the AP MAC must be like 00:11:22:33:44:55."))
    elif not st["ack_auto"] and not st["autoack_disabled"]:
        # (as on airOS 8: with the automatic distance, or airMAX without ACK, it is not used)
        top = v6_max_distance(st, board)
        if not 0 <= st["distance"] <= top:
            out.append(tr("With {width} MHz the distance must be between 0 and {km} km.",
                          width=st["chanbw"], km=f"{top / 1000:g}"))
    return out


def v8_widths(st: dict, board: dict, available: set[int] | None) -> list[int]:
    """getAvailableChanbw() of an access point: the widths of the board that have channels
    in the country (available, if known), at most 40 MHz in PtMP, widest first."""
    raw = (board.get("radio.1.chanbw") or "").replace('"', "")
    if st["ieee"] == "11nght" and board.get("radio.1.chanbw_g"):
        raw = board["radio.1.chanbw_g"]
    widths = [w for w in (_int(x) for x in raw.split(",")) if w and w % 10 == 0]
    widths = [w for w in widths if available is None or w in available]
    if not st["ptp"]:
        widths = [w for w in widths if w <= 40]
    return sorted(set(widths), reverse=True)


def v8_station_widths(st: dict, board: dict, available: set[int] | None) -> list[int]:
    """The station's list: Auto (-1), the automatic widths together (0, or the only one)
    and the others."""
    widths = v8_widths(st, board, available)
    auto = [w for w in (20, 40, 80) if w in widths]
    out = [CHANBW_AUTO_AP]
    if len(auto) > 1:
        out.append(CHANBW_AUTO)
    elif auto:
        out.append(auto[0])
    return out + [w for w in widths if w not in auto]


def v8_tdd_ratios(duration: int, width: int) -> list[int]:
    """getAvailableFFDlRatios(): the download/upload splits of a fixed frame."""
    if width != 10:
        return [50, 67, 75] if duration in (8000, 10000) else [50, 67] if duration == 5000 else []
    return [67] if duration in (8000, 10000) else []
