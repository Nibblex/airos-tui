"""Showing the status: the device and its link (with what the airOS 8 web's main page shows
of both ends, in its formats: its distance warnings and, with GPS, its GPS group), PPPoE and
the power report."""
from __future__ import annotations

import math

from ..core.i18n import N_, tr
from ..core.config import _int, js_parse_float, js_round, js_to_fixed
from .network import netmode_label
from .services import unms_state
from .wireless import NO_ACK_DISTANCE
from .firmware import fmt_version

ATPC_LABELS = {1: N_("adjusting"), 2: N_("automatic"), 3: N_("automatic, failed"),
               4: N_("automatic, at the limit")}

WIRELESS_MODES = {
    "ap-ptp": N_("Access point PtP"), "ap-ptmp": N_("Access point PtMP"),
    "sta-ptp": N_("Station PtP"), "sta-ptmp": N_("Station PtMP"),
    "ap": N_("Access point"), "sta": N_("Station"),
    "aprepeater": N_("Repeater"), "apwds": N_("Access point WDS"), "stawds": N_("Station WDS"),
}


PPPOE_ERRORS = {
    "pppoe is not enabled.": N_("PPPoE is not enabled on the device. If you have just set it "
                                "up, apply the changes first."),
    "not connected": N_("No connection: the device could not open the PPPoE session.\n\n"
                        "Check the user, the password and the service name, and that the WAN "
                        "is connected to the modem or to the provider's network."),
}


def format_pppoe(info: dict) -> str:
    if info.get("local"):
        return tr("Could not query the PPPoE status:\n{error}", error=info["error"])
    if info.get("error"):
        err = info["error"].strip()
        known = PPPOE_ERRORS.get(err.lower())
        return tr(known) if known else tr("The device answers: {error}", error=err)
    rows = [(N_("Status"), tr("connected")), (N_("User"), info.get("name")),
            (N_("Assigned IP"), info.get("iplocal")), (N_("Server (remote)"), info.get("ipremote")),
            (N_("Primary DNS"), info.get("dns1")), (N_("Secondary DNS"), info.get("dns2")),
            (N_("Connected for"), info.get("duration"))]
    return "\n".join(f"{tr(label):<20}{value}" for label, value in rows if value)


def pppoe_line(info: dict) -> str:
    """One line for the test mode decision."""
    if not info.get("error"):
        return tr("PPPoE: connected, IP {ip}.", ip=info.get("iplocal"))
    if info.get("local"):
        return tr("PPPoE: I could not query the session status.")
    err = info["error"].strip()
    if err.lower() == "not connected":
        return tr("⚠ PPPoE: NOT CONNECTED. Check the user, password and service before "
                  "keeping the changes.")
    if err.lower() == "pppoe is not enabled.":
        return tr("⚠ PPPoE: the device says it is not enabled.")
    return tr('⚠ PPPoE: the device answers "{error}".', error=err)


def _fmt_uptime(seconds) -> str | None:
    s = _int(seconds)
    if s is None:
        return None
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return (f"{d} d " if d else "") + f"{h} h {m} min"


def _freq(w: dict) -> str | None:
    f = w.get("frequency")
    if f in (None, "", 0):
        return None
    text = f"{f} MHz" if isinstance(f, (int, float)) else str(f)
    if w.get("channel"):
        text = tr("{freq} (channel {channel})", freq=text, channel=w["channel"])
    c = w.get("center1_freq")
    if c and str(c) not in text:
        text = tr("{freq}, center {center} MHz", freq=text, center=c)
    return text


def wireless_mode_label(mode) -> str:
    return tr(WIRELESS_MODES[mode]) if mode in WIRELESS_MODES else mode


# -- the formats of the airOS 8 web's main page ----------------------------------------

MIN_DISTANCE = 300          # m: the web never shows less


def fmt_bytes_if(n) -> str:
    """The web's formatBytesIf: 107 M, 18.5 M, 14.0 G…"""
    t = float(_int(n) or 0)
    if js_round(t) < 1e3:
        unit = ""
    elif js_round(t / 1e3) < 1e3:
        t, unit = t / 1e3, "K"
    elif js_round(t / 1e6) < 1e3:
        t, unit = t / 1e6, "M"
    else:
        t, unit = t / 1e9, "G"
    value = "0" if not t else js_to_fixed(t, 0 if t > 100 else 1 if t > 10 else 2)
    return f"{value} {unit}".rstrip()


def fmt_distance(meters, minimum: bool = True) -> str | None:
    """The web's _formatDistance: miles and km, never less than 300 m (minimum)."""
    m = _int(meters) if minimum else meters
    if m is None:
        return None
    if minimum and m == 100000:
        return tr("measuring")
    m = max(m, MIN_DISTANCE) if minimum else m
    return tr("{miles} miles ({km} km)", miles=js_to_fixed(m / 1609.344, 1),
              km=js_to_fixed(m / 1000, 1))


# Its margin for the distance it warns about in PtMP with "Flexible (legacy)" framing (the
# limit of each channel width, its getNoAckDistance, is wireless.NO_ACK_DISTANCE).
DISTANCE_TOLERANCE = 0.17
EARTH_RADIUS = 6378137                     # m, as its calculateDistanceFromCoordinates


def no_ack_distance(chanbw) -> int:
    return NO_ACK_DISTANCE.get(_int(chanbw), 22500)


def distance_limit_reached(meters: float, st: dict) -> bool:
    """Its _isDistanceLimitReached: in PtMP, with a frame neither fixed nor flexible (that is,
    "Flexible (legacy)"), when the distance and its 17 % reach that channel width's limit."""
    w = st.get("wireless") or {}
    if not str(w.get("mode") or "").endswith("-ptmp"):
        return False
    poll = w.get("polling") or {}
    if poll.get("fixed_frame") or (_int(poll.get("flex_mode")) or 0) > 0:
        return False
    return meters * (1 + DISTANCE_TOLERANCE) >= no_ack_distance(w.get("chanbw"))


def _degrees(value) -> float:
    """parseFloat(value).toFixed(6), as a number (NaN: 0)."""
    n = js_parse_float(value)
    return 0.0 if n is None or math.isnan(n) else float(js_to_fixed(n, 6))


def coordinates(st: dict, cfg=None, sta: dict | None = None) -> tuple[float, float] | None:
    """Its getGpsInfo: where this device is (its GPS, with a fix; without one, the Location of
    its System page: cfg) or, with sta, the other end (its remote.gps). None without both."""
    if sta is not None:
        gps = (sta.get("remote") or {}).get("gps") or {}
        lat, lon = _degrees(gps.get("lat", 0)), _degrees(gps.get("lon", 0))
    elif (st.get("gps") or {}).get("fix"):
        lat, lon = _degrees(st["gps"].get("lat", 0)), _degrees(st["gps"].get("lon", 0))
    elif cfg is not None:
        lat, lon = _degrees(cfg.get("system.latitude")), _degrees(cfg.get("system.longitude"))
    else:
        return None
    return (lat, lon) if lat != 0 and lon != 0 else None


def distance_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Its calculateDistanceFromCoordinates (meters)."""
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2)
    return EARTH_RADIUS * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


# Its _getGpsSignal: the GPS signal from its dilution of precision (with a fix).
_GPS_DOP = ((20, 10), (15, 20), (10, 30), (7, 40), (5, 50), (3.5, 60), (2, 70), (1.5, 80),
            (1, 90), (0, 100))


def gps_rows(gps: dict) -> list[tuple]:
    """Its GPS group: satellites, signal, location and altitude."""
    fix = _int(gps.get("fix")) == 1
    dop = js_parse_float(gps.get("dop")) or 0.0
    signal = next((pct for limit, pct in _GPS_DOP if dop > limit), 0) if fix else 0
    lat, lon = _degrees(gps.get("lat", 0)), _degrees(gps.get("lon", 0))
    alt = js_parse_float(gps.get("alt"))
    return [(N_("Satellites tracked"), str(_int(gps.get("sats")) or 0)),
            (N_("GPS Signal"), f"{signal} %" if signal else "-"),
            (N_("Location"), f"{js_to_fixed(lat, 6)} / {js_to_fixed(lon, 6)}"
             if lat and lon else "-"),
            (N_("Altitude"), f"{js_round(alt)} m ({js_round(3.2808 * alt)} ft)"
             if fix and alt is not None else "-")]


def service_uptime(service) -> str | None:
    """The web's "Service uptime": the time with link over the time in service."""
    if not isinstance(service, dict):
        return None
    total = _int(service.get("time"))
    if not total:
        return "0.000%"
    value = (_int(service.get("link")) or 0) / total * 100
    return ("100" if value == 100 else js_to_fixed(value, 3)) + "%"


def tdd_framing(polling: dict) -> str:
    """The web's "TDD Framing"."""
    if polling.get("fixed_frame"):
        text = tr("Fixed") + f" ({_int(polling.get('ff_frame_dur')) or 0}ms)"
        return text + (" & " + tr("GPS Synced") if polling.get("gps_sync") else "")
    return tr("Flexible") if (_int(polling.get("flex_mode")) or 0) > 0 else tr(
        "Flexible (legacy)")


def _cinr(value) -> str:
    v = _int(value)
    return "-" if v in (None, 3) else f"+{v} dB"


def _eth_name(ifname: str) -> str:
    """The web's devname2uidevname: eth0 is LAN0."""
    return (str(ifname).replace("ath", "WLAN").replace("eth", "LAN").replace("br", "BRIDGE")
            .replace("ppp+", "TUNNEL0").replace("ppp", "TUNNEL"))


def _eth_speed(st: dict) -> str:
    """The web's get_eth_str: 1000 Mbps-Full."""
    if not st or not st.get("plugged"):
        return "-"
    speed = _int(st.get("speed")) or 0
    if speed <= 0:
        return tr("Plugged")
    duplex = st.get("duplex")
    if duplex is True or duplex == 1:
        return f"{speed} Mbps-" + tr("Full")
    if duplex is False or duplex == 0:
        return f"{speed} Mbps-" + tr("Half")
    return f"{speed} Mbps"


def ethernet_rows(ifaces: list) -> list[tuple[str, str]]:
    """The web's Ethernet group: LAN speed, cable length and cable SNR of the LAN ports
    (status.cgi interfaces eth*, or remote.ethlist)."""
    ports = [i for i in ifaces or [] if isinstance(i, dict)
             and str(i.get("ifname") or "").startswith("eth") and str(i["ifname"])[3:].isdigit()]
    if not ports:
        return []
    status = [i.get("status") if isinstance(i.get("status"), dict) else i for i in ports]
    names = [_eth_name(i["ifname"]) for i in ports]
    label = (names[0].replace("0", "") if len(names) == 1 else " / ".join(names))
    rows = [(tr("{port} Speed", port=label), " / ".join(_eth_speed(x) for x in status))]
    if any("cable_len" in x or "snr" in x for x in status):
        lengths, snrs = [], []
        for x in status:
            length = _int(x.get("cable_len")) if x.get("plugged") else None
            lengths.append("-" if length is None or length < 0 else
                           "< 20 m" if length < 20 else f"{length} m")
            pairs = [p for p in (_int(v) for v in (x.get("snr") or [])[:4]) if p and p > 0]
            avg = (js_round(sum(pairs) / len(pairs)) if len(x.get("snr") or []) == 4 and pairs
                   else 0)
            snrs.append(f"+{avg} " + tr("dB") if avg else "-")
        rows += [(tr("Cable Length"), " / ".join(lengths)), (tr("Cable SNR"), " / ".join(snrs))]
    return rows


def _memory(total, free) -> str | None:
    t, f = _int(total), _int(free)
    if not t:
        return None
    return f"{max(0, min(100, 100 * (t - (f or 0)) // t))} %"


def remote_rows(sta: dict, ptmp_ap: bool) -> dict[str, list[tuple[str, str]]]:
    """The web's panel of the other end (a station, or the access point of a station), in
    its groups: "main" (the device), "wireless" and "ethernet"."""
    r = sta.get("remote") or {}
    airmax = sta.get("airmax") or {}
    main = [(tr("Device Model"), r.get("platform")), (tr("Version"), fmt_version(r.get("version"))),
            (tr("Network role"), netmode_label(r.get("netrole")) if r.get("netrole") else None),
            ("UISP", unms_state({"unms": r.get("unms")}) if isinstance(r.get("unms"), dict)
             else None),
            ("CPU", f"{js_round(float(r['cpuload']))} %"
             if isinstance(r.get("cpuload"), (int, float)) else None),
            (tr("Memory"), _memory(r.get("totalram"), r.get("freeram"))),
            (tr("Date"), r.get("time")), (tr("Uptime"), _fmt_uptime(r.get("uptime")))]
    wireless = [
            (tr("Wireless Mode"), wireless_mode_label(r.get("mode")) if r.get("mode") else None),
            (tr("Last IP"), sta.get("lastip")), (tr("Distance"), fmt_distance(r.get("distance"))),
            (tr("TX/RX Bytes"), f"{fmt_bytes_if(r.get('tx_bytes'))} / "
                                f"{fmt_bytes_if(r.get('rx_bytes'))}"
             if r.get("tx_bytes") is not None else None),
            (tr("Connection Time"), _fmt_uptime(sta.get("uptime"))),
            ("CINR", _cinr((airmax.get("tx") or {}).get("cinr")) if airmax else None),
            (tr("Noise Floor"), f"{r['noisefloor']} dBm" if r.get("noisefloor") else None)]
    if ptmp_ap:
        wireless.append((tr("Service uptime"), service_uptime(r.get("service"))))
    groups = {"main": main, "wireless": wireless, "ethernet": ethernet_rows(r.get("ethlist") or [])}
    return {k: [(label, value) for label, value in rows if value not in (None, "")]
            for k, rows in groups.items()}


def _shown(rows) -> list[str]:
    """Lines of label (to translate) and value, without the empty values."""
    return [f"{tr(label):<21} {value}" for label, value in rows
            if value not in (None, "", [], {})]


def _date_rows(st: dict, ntp: bool | None) -> list[tuple]:
    """The airOS 8 web's Date: synchronized or not, and its NTP (whether the client is on,
    from the configuration: ntp)."""
    time = (st.get("host") or {}).get("time")
    if ntp is None:
        return [(N_("Date"), time)]
    synced = (st.get("ntpclient") or {}).get("last_sync") if ntp else None
    return [(f"{tr('Date')} ({tr('Synced') if synced else tr('Not Synced')})", time),
            ("NTP", tr("NTP Client synced at {time}", time=synced) if synced
             else tr("NTP Client not synced") if ntp else tr("NTP Client disabled"))]


def _device_rows(st: dict, flavor: str | None, full_version: str,
                 ntp: bool | None = None) -> list[tuple]:
    """The device: name, model, version, role, date, uptime, CPU, memory and UISP."""
    h = st.get("host") or {}
    rows = [(N_("Name"), h.get("hostname")), (N_("Model"), (h.get("devmodel") or "").strip())]
    if flavor == "v8" and full_version:
        rows.append(("Firmware", fmt_version(full_version)))
    else:
        rows.append(("Firmware", " ".join(str(x) for x in (h.get("fwprefix"),
                                                           h.get("fwversion")) if x)))
    rows.append((N_("Network role"), netmode_label(h.get("netrole"))))
    if flavor == "v8":
        rows += _date_rows(st, ntp)
    rows.append((N_("Running for"), _fmt_uptime(h.get("uptime"))))
    cpu = h.get("cpuload")
    if h.get("cputotal") and h.get("cpubusy") is not None:   # airOS 6
        cpu = 100.0 * h["cpubusy"] / h["cputotal"]
    if isinstance(cpu, (int, float)):
        rows.append(("CPU", f"{cpu:.0f} %"))
    tot, free = h.get("totalram"), h.get("freeram")
    if tot and free is not None:
        rows.append((N_("Memory used"), tr("{used} % of {total} MB",
                                           used=f"{100 - 100 * free / tot:.0f}",
                                           total=f"{tot / 1048576:.0f}")))
    if flavor == "v8" and isinstance(st.get("unms"), dict):
        rows.append(("UISP", unms_state(st)))
    return rows


def _wireless_rows(st: dict, flavor: str | None, remote: dict | None,
                   cfg=None) -> list[tuple]:
    """The wireless link, as each web's main page shows it."""
    w = st.get("wireless") or {}
    rows = [(N_("Mode"), wireless_mode_label(w.get("mode"))), ("SSID", w.get("essid")),
            (N_("Frequency"), _freq(w)),
            (N_("Channel width"), f"{w['chanbw']} MHz" if w.get("chanbw") else None),
            (N_("IEEE mode"), w.get("ieeemode") or w.get("opmode")),
            (N_("Security"), w.get("security")), (N_("TX power"), _txpower_text(w)),
            (N_("Noise floor"), f"{w['noisef']} dBm" if w.get("noisef") is not None else None)]
    if flavor == "v8":
        rows += _v8_link_rows(st, remote, cfg)
    else:
        rows.append((N_("Distance"), f"{w['distance']} m" if w.get("distance") else None))
    if flavor == "v6":
        rows.append((N_("Signal"), f"{w['signal']} dBm" if w.get("signal") else None))
        if w.get("ccq") is not None:
            rows.append(("CCQ", f"{_int(w['ccq']) / 10:.1f} %" if _int(w["ccq"]) is not None
                         else None))
        if w.get("txrate") or w.get("rxrate"):
            rows.append((N_("TX / RX rate"),
                         f"{w.get('txrate', '?')} / {w.get('rxrate', '?')} Mbps"))
    poll = w.get("polling") or {}
    if poll.get("dl_capacity") or poll.get("ul_capacity"):
        rows.append((N_("Capacity ↓/↑"), f"{(poll.get('dl_capacity') or 0) / 1000:.0f} / "
                                         f"{(poll.get('ul_capacity') or 0) / 1000:.0f} Mbps"))
    elif poll.get("quality") is not None:
        rows.append((N_("airMAX quality/cap."),
                     f"{poll.get('quality')} % / {poll.get('capacity')} %"))
    rows.append((N_("Connected links"), w.get("count")))
    return rows


def _distance_rows(st: dict, remote: dict | None, cfg=None) -> list[tuple]:
    """Its distance: the one it measures (marked when it warns about it) and, if both ends
    have coordinates (GPS, or the Location of its System page), the one between them."""
    w = st.get("wireless") or {}
    measured = _int((remote or {}).get("distance") if remote else w.get("distance"))
    if not w.get("distance") or measured is None:
        return [(N_("Distance"), None)]
    shown = max(measured, MIN_DISTANCE)
    warn = measured != 100000 and distance_limit_reached(shown, st)
    rows = [(N_("Distance"), fmt_distance(measured) + ("  ⚠" if warn else ""))]
    here, there = coordinates(st, cfg), coordinates(st, sta=remote) if remote else None
    if here and there:
        between = distance_between(here, there)
        rows.append((N_("Distance (coord.)"), fmt_distance(between, minimum=False)
                     + ("  ⚠" if distance_limit_reached(between, st) else "")))
        warn = warn or distance_limit_reached(between, st)
        margin = MIN_DISTANCE if shown <= MIN_DISTANCE else max(between, shown) * DISTANCE_TOLERANCE
        if abs(between - shown) > margin and shown <= between:
            rows.append(("", tr("⚠ Coordinate-based and propagation-based distances do not "
                                "match.")))
    if warn:
        rows.append(("", tr("⚠ In PtMP with \"Flexible (legacy)\" framing, its web warns from "
                            "{km} km at {width} MHz (with a 17 % margin); not with a fixed or "
                            "\"Flexible\" frame.", width=_int(w.get("chanbw")),
                            km=js_to_fixed(no_ack_distance(w.get("chanbw")) / 1000, 1))))
    return rows


def _v8_link_rows(st: dict, remote: dict | None, cfg=None) -> list[tuple]:
    """What the airOS 8 web shows of the link: distance, bytes, TDD framing, CINR and the
    service uptime."""
    w = st.get("wireless") or {}
    rows = _distance_rows(st, remote, cfg)
    wlan = next((i.get("status") or {} for i in st.get("interfaces") or []
                 if str(i.get("ifname") or "").startswith(("ath", "wlan"))), {})
    if wlan.get("tx_bytes") is not None:
        rows.append((N_("TX/RX Bytes"), f"{fmt_bytes_if(wlan.get('tx_bytes'))} / "
                                        f"{fmt_bytes_if(wlan.get('rx_bytes'))}"))
    rows.append((N_("TDD Framing"),
                 tdd_framing(w.get("polling") or {}) if w.get("polling") else None))
    if remote:
        rows.append(("CINR", _cinr(((remote.get("airmax") or {}).get("rx") or {}).get("cinr"))))
    mine, theirs = w.get("service"), ((remote or {}).get("remote") or {}).get("service")
    if "-ptp" in str(w.get("mode") or "") and isinstance(theirs, dict) and (
            _int(theirs.get("time")) or 0) > (_int((mine or {}).get("time")) or 0):
        mine = theirs                         # (PtP: the one in service longer)
    rows.append((N_("Service uptime"), service_uptime(mine)))
    return rows


def _other_end_lines(remote: dict, mode: str, gps: bool = False) -> list[str]:
    """The airOS 8 web's panel of the other end, in its three groups (and its GPS one, when
    this device has GPS: gps)."""
    name = (remote.get("remote") or {}).get("hostname") or remote.get("mac") or "?"
    panel = remote_rows(remote, mode.startswith("ap") and "-ptp" not in mode)
    if gps:
        panel["gps"] = [(tr(label), value) for label, value in
                        gps_rows((remote.get("remote") or {}).get("gps") or {})]
    out: list[str] = []
    head = tr("── The other end: {name} ──", name=name)
    for key, title in (("main", head), ("wireless", tr("── Wireless (the other end) ──")),
                       ("ethernet", tr("── Ethernet (the other end) ──")),
                       ("gps", tr("── GPS (the other end) ──"))):
        if panel.get(key):
            if not out and key != "main":       # (without its own data: which one it is)
                out += ["", head]
            out += ["", title] + [f"{label:<22}{value}" for label, value in panel[key]]
    return out


def _station_lines(stations: list[dict]) -> list[str]:
    """The connected devices, a line and their data each."""
    out = []
    for s in stations[:40]:
        r = s.get("remote") or {}
        out.append(f"{s.get('mac', '?')}  {r.get('hostname') or ''}".rstrip())
        parts = []
        if s.get("signal") is not None:
            parts.append(tr("signal {signal} dBm", signal=s["signal"]))
        if r.get("signal") is not None:
            parts.append(tr("remote {signal} dBm", signal=r["signal"]))
        am = s.get("airmax") or {}
        if am.get("dl_capacity") or am.get("ul_capacity"):
            parts.append(tr("cap. {down}/{up} Mbps",
                            down=f"{(am.get('dl_capacity') or 0) / 1000:.0f}",
                            up=f"{(am.get('ul_capacity') or 0) / 1000:.0f}"))
        if s.get("distance"):
            parts.append(f"{s['distance']} m")
        if s.get("lastip"):
            parts.append(f"IP {s['lastip']}")
        if s.get("uptime"):
            parts.append(tr("connected {time}", time=_fmt_uptime(s["uptime"])))
        if parts:
            out.append("    " + ", ".join(parts))
    return out


def _ip_lines(st: dict) -> list[str]:
    out = []
    for i in st.get("interfaces") or []:
        ip = (i.get("status") or {}).get("ipaddr")
        if ip and ip != "0.0.0.0":
            out.append(f"{i.get('ifname') or '?'!s:<8}{ip}")
    return out


def format_status(st: dict, flavor: str | None, full_version: str = "",
                  ntp: bool | None = None, cfg=None, board: dict | None = None) -> str:
    """The status screen: the device, its link (on airOS 8, with what its web's main page
    shows of both ends), its Ethernet, the connected devices and its addresses. ntp: whether
    the NTP client is on (airOS 8: then the date says whether it is synchronized); cfg, the
    configuration (its Location), and board, its board.info (whether it has GPS)."""
    w = st.get("wireless") or {}
    stations = [x for x in w.get("sta") or [] if isinstance(x, dict)]
    remote = stations[0] if len(stations) == 1 else None      # (the web's other end)
    gps = flavor == "v8" and _int((board or {}).get("feature.gps")) == 1
    out = _shown(_device_rows(st, flavor, full_version, ntp))
    out += ["", tr("── Wireless ──")] + _shown(_wireless_rows(st, flavor, remote, cfg))
    eth = ethernet_rows(st.get("interfaces") or [])
    if eth:
        out += ["", tr("── Ethernet ──")] + [f"{label:<22}{value}" for label, value in eth]
    if gps:
        out += ["", tr("── GPS ──")] + _shown(gps_rows(st.get("gps") or {}))
    if remote and flavor == "v8":
        out += _other_end_lines(remote, str(w.get("mode") or ""), gps)
    elif stations:
        out += ["", tr("── Connected devices ──")] + _station_lines(stations)
    ips = _ip_lines(st)
    if ips:
        out += ["", tr("── IP addresses ──")] + ips
    return "\n".join(out)


def _txpower_text(w: dict) -> str | None:
    """Power the radio reports (not the configured one), with the ATPC state."""
    if w.get("txpower") in (None, ""):
        return None
    atpc = _int((w.get("polling") or {}).get("atpc_status")) or 0
    return f"{w['txpower']} dBm" + (f" (ATPC: {tr(ATPC_LABELS[atpc])})" if atpc in ATPC_LABELS
                                    else "")


def short_status(st: dict) -> str:
    w = st.get("wireless") or {}
    parts = [f"SSID: {w.get('essid', '?')}"]
    if _freq(w):
        parts.append(tr("frequency: {freq}", freq=_freq(w)))
    if _txpower_text(w):
        parts.append(tr("TX power: {power}", power=_txpower_text(w)))
    if w.get("signal"):
        parts.append(tr("signal: {signal} dBm", signal=w["signal"]))
    if w.get("count") is not None:
        parts.append(tr("connected links: {count}", count=w["count"]))
    return "\n".join(parts)


def power_report(changes, st: dict) -> str:
    """Compare the power asked for with the one the radio reports after applying."""
    asked = next((_int(b) for k, _, b in changes if k == "radio.1.txpower"), None)
    w = st.get("wireless") or {}
    real = _int(w.get("txpower"))
    if asked is None or real is None:
        return ""
    if real == asked:
        return tr("Power: the radio reports {real} dBm, the one you asked for.", real=real)
    msg = [tr("⚠ You asked for {asked} dBm and the radio reports {real} dBm.", asked=asked,
              real=real)]
    atpc = _int((w.get("polling") or {}).get("atpc_status")) or 0
    if atpc in ATPC_LABELS:
        msg.append(tr("The automatic power control (ATPC: {state}) adjusts it by itself.",
                      state=tr(ATPC_LABELS[atpc])))
    elif real < asked:
        msg.append(tr("The device limited it."))
    return " ".join(msg)
