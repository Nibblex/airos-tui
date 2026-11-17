"""The web's tools: the antenna alignment (the signals of status.cgi, computed as its
alignment tool does), the site survey (the access points around and what choosing one
prepares, as its Wireless page does), the device discovery (what the antenna finds, as its
table shows it) and the network ping, traceroute and speed test (their rules, figures and
tables)."""
from __future__ import annotations

import re

from ..core.i18n import N_, tr
from ..core.config import (_fmt_countdown, _int, js_round, js_str, js_to_fixed, WEB_IPV4_RE,
                           web_ipv6_ok)
from .limits import SYSTEM_PORTS
from .discovery import _ip_key
from .firmware import web_fw_version

# -- antenna alignment (airOS 8: the status model and AntennaAlignView) -----------------

SIGNAL_OVERSATURATION_LIMIT = -25   # dBm: above it the signal saturates the receiver
CHAIN_DIFF_LIMIT = 5                # dB between the two chains: above it, too high
RSSI_MAX = 36                       # the RSSI that fills a signal bar


def _bits(value) -> int:
    return bin(_int(value) or 0).count("1")


def _chains(entry: dict, mask) -> list[int | None]:
    """The signal of chains 0 and 1: chainrssi + (signal - rssi), None for a chain that is
    off (the web's rssi_to_signal)."""
    signal, rssi, chains = _int(entry.get("signal")), _int(entry.get("rssi")), \
        entry.get("chainrssi")
    if signal is None or rssi is None or not isinstance(chains, list):
        return [None, None]
    out = []
    for i in (0, 1):
        value = _int(chains[i]) if i < len(chains) else None
        out.append(value + signal - rssi if (_int(mask) or 0) & (1 << i) and value is not None
                   else None)
    return out


def _side(entry: dict, mask) -> dict:
    signal, rssi = _int(entry.get("signal")), _int(entry.get("rssi"))
    chains = _chains(entry, mask)
    both = [c for c in chains if c is not None]
    imbalance = abs(both[0] - both[1]) if len(both) == 2 and _bits(mask) >= 2 else 0
    return {"signal": signal if signal and signal < 0 else None,
            "percent": max(0, min(100, (100 * rssi) // RSSI_MAX)) if rssi is not None else 0,
            "chains": chains, "imbalance": imbalance,
            "imbalance_high": imbalance > CHAIN_DIFF_LIMIT,
            "saturated": bool(signal) and signal > SIGNAL_OVERSATURATION_LIMIT,
            "noise": _int(entry.get("noisefloor", entry.get("noisef")))}


def align_link(status: dict, flavor: str, mac: str | None = None) -> dict | None:
    """The signals of the link to align: local and (airOS 8) remote, of the station with
    that MAC (the first one if None). None if there is no link."""
    w = status.get("wireless") or {}
    if flavor != "v8":                          # airOS 6: status.cgi has only the local side
        if not _int(w.get("signal")):
            return None
        return {"name": w.get("essid") or "", "local": _side(w, w.get("rx_chainmask")),
                "remote": None, "cinr": None}
    stations = [s for s in w.get("sta") or [] if isinstance(s, dict)]
    sta = next((s for s in stations if mac is None or s.get("mac") == mac), None)
    if sta is None:
        return None
    remote = sta.get("remote") or {}
    airmax = sta.get("airmax") or {}
    return {"name": remote.get("hostname") or sta.get("mac") or "",
            "local": _side(sta, w.get("rx_chainmask")),
            "remote": _side(remote, remote.get("rx_chainmask")) if remote else None,
            "cinr": ((airmax.get("rx") or {}).get("cinr"), (airmax.get("tx") or {}).get("cinr"))}


def _bar(percent: int, width: int = 20) -> str:
    full = max(0, min(width, round(percent * width / 100)))
    return "█" * full + "░" * (width - full)


def _dbm(value) -> str:
    return f"{value} dBm" if isinstance(value, int) and value < 0 else "-"


def align_text(link: dict | None, best: dict) -> str:
    """The alignment screen: the signal of each side with its bar, the best one seen (best
    keeps it between refreshes), each chain and the warnings of the web."""
    if link is None:
        return tr("No link: the device is not connected (to an access point, or no station "
                  "is connected to it). The signal shows up as soon as there is one.")
    lines = [tr("Link with {name}", name=link["name"]) if link["name"] else "", ""]
    sides = [("local", tr("Local signal"), link["local"])]
    if link["remote"]:
        sides.append(("remote", tr("Remote signal"), link["remote"]))
    warns = []
    for key, label, side in sides:
        signal = side["signal"]
        if signal is not None and (best.get(key) is None or signal > best[key]):
            best[key] = signal
        lines.append(f"{label:<16}{_dbm(signal):>9}  {_bar(side['percent'] if signal else 0)}  "
                     + tr("best {signal}", signal=_dbm(best.get(key))))
        if any(c is not None for c in side["chains"]):
            chains = " / ".join(_dbm(c) for c in side["chains"])
            lines.append("  " + tr("chains 0 / 1: {chains} (difference {diff} dB)",
                                   chains=chains, diff=side["imbalance"]))
        if side["imbalance_high"]:
            warns.append(tr("{side}: the two chains differ by more than {limit} dB (cable, "
                            "connector or polarization).", side=label, limit=CHAIN_DIFF_LIMIT))
        if side["saturated"]:
            warns.append(tr("{side}: above {limit} dBm the signal saturates the receiver "
                            "(too close or too much power).", side=label,
                            limit=SIGNAL_OVERSATURATION_LIMIT))
    noise = link["local"]["noise"]
    extra = [tr("noise floor {noise}", noise=_dbm(noise))] if noise else []
    cinr = [c for c in link["cinr"] or () if isinstance(c, int) and c != 3]
    if link["cinr"] and len(cinr) == 2:
        extra.append(tr("CINR RX/TX {rx} / {tx} dB", rx=cinr[0], tx=cinr[1]))
    if extra:
        lines += ["", " · ".join(extra)]
    if warns:
        lines += [""] + ["⚠ " + w for w in warns]
    return "\n".join(lines).strip("\n")


# -- site survey (airOS 8: SiteSurveyCollection and the Wireless models' selectAp) ------

def survey_aps(data) -> tuple[bool, list[dict]]:
    """/survey.json.cgi: (still scanning, the access points found)."""
    if not isinstance(data, dict):
        return False, []
    aps = []
    for e in data.get("scan_data") or []:
        if not isinstance(e, dict):
            continue
        ieee = str(e.get("ieee_mode") or "")
        mode = "AC" if "ac" in ieee else ieee
        if e.get("airmax_ie") == "enabled":
            mode = "airMAX AC" if mode == "AC" else mode + " airMAX"
        aps.append({"mac": str(e.get("mac") or ""), "essid": str(e.get("essid") or ""),
                    "hostname": str(e.get("hostname") or ""), "mode": mode,
                    "op_mode": str(e.get("mode") or ""),        # Master, Ad-Hoc…
                    "airmax": e.get("airmax_ie") == "enabled",
                    "airmax_mode": str(e.get("airmax_mode") or ""),
                    "htcap": _int(e.get("htcap")),
                    "signal": _int(e.get("signal_level")), "noise": _int(e.get("noise_level")),
                    "encryption": str(e.get("encryption") or "").upper(),
                    "auth": str(e.get("auth_suites") or ""),
                    "frequency": e.get("frequency"), "center": e.get("center1")})
    return data.get("scan_status") == "scanning", aps


def survey_selectable(ap: dict, status_chanbw: int, modes: list[str]) -> bool:
    """canConnect: an airMAX access point with a visible SSID, a station mode the device
    has and a width it can follow."""
    return (f"sta-{ap['airmax_mode']}" in modes and not (status_chanbw == 0 and ap["htcap"] == 2)
            and ap["airmax"] and bool(ap["essid"]) and ap["op_mode"] != "Ad-Hoc")


def survey_changes(ap: dict, st: dict, status_chanbw: int, modes: list[str],
                   lock: bool) -> dict:
    """What choosing this access point prepares on a station (selectAp): its PtP or PtMP
    mode, the SSID (and its MAC with "Lock to AP"), the security it announces and, as the
    web, the channel width it is using now (automatic for 20, 40 and 80 MHz)."""
    changes: dict = {}
    want = f"sta-{ap['airmax_mode']}"
    current = "sta-" + ("ptp" if st["ptp"] else "ptmp")
    if want in modes and want != current:
        changes["ptp"] = want == "sta-ptp"
    if ap["essid"]:
        changes.update(ssid=ap["essid"], ap_mac=ap["mac"] if lock else "")
    else:
        changes.update(ssid="any", ap_mac=ap["mac"])
    security = ap["encryption"].lower()
    security = "none" if security in ("-", "", "none") else security
    if security in ("wpa", "wpa2"):
        security = "wpa2"
    if security in ("none", "wpa2"):           # (the airOS 8 web offers no other)
        changes["security"] = security
    changes["wpa_auth"] = "WPA-EAP" if "802.1x" in ap["auth"].lower() else "WPA-PSK"
    width = 0 if status_chanbw in (0, 20, 40, 80) else status_chanbw
    if width != st["chanbw"]:
        changes["chanbw"] = width
    return changes


def survey_frequency(ap: dict) -> str:
    """The frequency as the web shows it (GHz)."""
    f = ap.get("frequency")
    try:
        value = float(f)
    except (TypeError, ValueError):
        return str(f or "")
    return f"{value / 1000:.3f}" if value > 100 else f"{value:.3f}"


# -- device discovery (airOS 8: the web's DiscoveryView, from the antenna) ----------------

# The scans its web makes as it opens: (seconds before it, how long the antenna listens in
# ms). While it stays open it repeats the last one every 20 s.
ANTENNA_DISCOVERY_SCANS = ((0, 500), (0, 1000), (1, 5000))
_IPV4_TEXT = re.compile(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}")


def found_mode(wmode) -> str:
    """The web's Mode column: STA, AP or -."""
    return {2: "STA", 3: "AP"}.get(_int(wmode), "-")


def found_addresses(d: dict) -> list[str]:
    """Its IPv4 addresses: ipv4 and those in "addresses", whatever form they come in."""
    out: list[str] = []

    def walk(value):
        if isinstance(value, str):
            if _IPV4_TEXT.fullmatch(value) and value not in out:
                out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(d.get("ipv4"))
    walk(d.get("addresses"))
    return out


def merge_found(found: dict, devices: list[dict]) -> None:
    """Into its web's table: one row per device (by its MAC, hwaddr), with the newest data."""
    for d in devices:
        key = str(d.get("hwaddr") or "").replace(":", "").lower() or str(d.get("ipv4") or "")
        found[key] = d


def found_list(found: dict) -> list[dict]:
    return sorted(found.values(), key=lambda d: _ip_key(str(d.get("ipv4") or "")))


def found_line(d: dict) -> str:
    """One line per device for the list: its IP first, as the discovery from the PC."""
    parts = [d.get("hostname") or tr("(no name)"), d.get("product") or "?"]
    if found_mode(d.get("wmode")) != "-":
        parts.append(found_mode(d.get("wmode")))
    fw = web_fw_version(d.get("fwversion")) or d.get("fwversion")
    if fw:
        parts.append(str(fw))
    return f"{d.get('ipv4') or '-'!s:<15} " + " · ".join(parts)


def format_found(d: dict) -> str:
    """The columns of its web's table, in its order, and the uptime it also gives."""
    rows = [(tr("MAC Address"), d.get("hwaddr")), (tr("Device Name"), d.get("hostname")),
            (tr("Mode"), found_mode(d.get("wmode"))), ("SSID", d.get("essid")),
            (tr("Product"), d.get("product")),
            (tr("Firmware"), web_fw_version(d.get("fwversion")) or d.get("fwversion")),
            (tr("IP Address"), d.get("ipv4"))]
    if _int(d.get("uptime")):
        rows.append((tr("Up for"), _fmt_countdown(_int(d.get("uptime")))))
    others = [ip for ip in found_addresses(d) if ip != d.get("ipv4")]
    if others:
        rows.append((tr("Addresses"), ", ".join(others)))
    rows = [(label, value) for label, value in rows if value not in (None, "")]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)


# -- network tools (airOS 8: the web's Network Ping, Traceroute and Speed Test) -----------

PING_COUNT, PING_SIZE = 5, 56                # its defaults
PING_SIZE_RANGE = (28, 65507)
TRACE_MAX_HOPS = 30
SPEEDTEST_PORT, SPEEDTEST_DURATION = "80", 30
SPEEDTEST_DURATIONS = (10, 99)               # seconds
SPEEDTEST_DIRECTIONS = {"dx": N_("Duplex"), "rx": N_("Receive"), "tx": N_("Transmit")}


def valid_ip(text: str) -> bool:
    """An IPv4 or an IPv6 address, as its web's patterns take them (ipv4Address,
    ipv6Address: without a zone)."""
    return bool(WEB_IPV4_RE.fullmatch(text)) or web_ipv6_ok(text)


def _whole_number(text: str) -> int | None:
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def ping_problem(field: str, value) -> str | None:
    """What the web says of a value of its Network Ping (the destination, the packet count
    and the packet size), or None."""
    text = str(value).strip()
    if field == "ip":
        if not text:
            return tr("Destination IP is required")
        return None if valid_ip(text) else tr("Destination IP is invalid")
    n = _whole_number(text)
    if field == "count":
        if not text:
            return tr("Packet count is required")
        return None if n is not None and n >= 1 else tr(
            "Packet count must be greater than or equal to {min}", min=1)
    if not text:
        return tr("Packet size is required")
    low, high = PING_SIZE_RANGE
    return None if n is not None and low <= n <= high else tr(
        "Packet size must be between {low} and {high}", low=low, high=high)


def ping_stats(results: list[dict]) -> dict:
    """The web's results of the replies (its PingView): sent, received, loss (%) and the
    minimum, average and maximum time (ms)."""
    sent = received = loss = 0
    low, high, avg = 999999.0, 0.0, 0.0
    for r in results:
        sent += 1
        if not r.get("timeout"):
            received += 1
            high, low = max(high, r["time"]), min(low, r["time"])
            avg += (r["time"] - avg) / received
        if sent > received:
            loss = js_round(100 * (sent - received) / sent)
    return {"sent": sent, "received": received, "loss": loss,
            "min": 0 if low == 999999.0 else low, "max": high, "avg": js_round(100 * avg) / 100}


def ping_text(results: list[dict]) -> str:
    """Its table of replies (host, time, TTL) and its results."""
    lines = [f"{tr('Host'):<40} {tr('Time'):<18} TTL"]
    for r in results:
        time = (tr("Network error") if r.get("error") else tr("Request timeout")
                if r.get("timeout") else f"{js_str(r['time'])} ms")
        ttl = "" if r.get("timeout") else js_str(r.get("ttl"))
        lines.append(f"{r.get('host') or ''!s:<40.40} {time:<18} {ttl}".rstrip())
    if not results:
        lines.append(tr("No data available."))
    s = ping_stats(results)
    return "\n".join(lines + ["", tr("Packet Loss: {loss}% ({received} of {total} packets "
                                     "received)", loss=s["loss"], received=s["received"],
                                     total=s["sent"]),
                              tr("Min / Avg / Max: {min} / {avg} / {max} ms",
                                 min=js_str(s["min"]), avg=js_str(s["avg"]),
                                 max=js_str(s["max"]))])


def trace_problem(value) -> str | None:
    """Its Traceroute asks for a destination host, and nothing more of it."""
    return None if str(value).strip() else tr("Destination host is required")


def trace_row(res: dict) -> dict:
    """One hop as its web shows it: the times joined, or * · * · * with no answer."""
    if res.get("timeout"):
        return {"hop": res.get("hop"), "host": "", "ip": "", "responses": "* · * · *"}
    return {"hop": res.get("hop"), "host": str(res.get("hostname") or ""),
            "ip": str(res.get("ip") or ""),
            "responses": " · ".join(js_str(t) for t in res.get("rtts") or [])}


def trace_text(rows: list[dict], error: str | None = None) -> str:
    """Its table of hops (#, host, IP, response) and, if it failed, why."""
    lines = [f"{'#':>3}  {tr('Host'):<32} {'IP':<16} {tr('Response')}"]
    lines += [f"{js_str(r['hop']):>3}  {r['host']:<32.32} {r['ip']:<16} {r['responses']}"
              for r in rows]
    if not rows and not error:
        lines.append(tr("No data available."))
    if error:
        lines += ["", tr("Error") + ": " + error]
    return "\n".join(lines)


def speedtest_problem(field: str, value) -> str | None:
    """What the web says of a value of its Speed Test (SpeedTestModel), or None."""
    text = str(value).strip()
    if field == "port":
        if not text:
            return tr("Port is required")
        n = _whole_number(text)
        if n is None or not 1 <= n <= 65535:
            return tr("Port must be between {low} and {high}", low=1, high=65535)
        return tr("Port is used by system") if n in SYSTEM_PORTS else None
    if field == "duration":
        if not text:
            return tr("Duration is required")
        low, high = SPEEDTEST_DURATIONS
        n = _whole_number(text)
        return None if n is not None and low <= n <= high else tr(
            "Duration must be between {low} and {high}", low=low, high=high)
    if field == "user":
        return None if text else tr("User is required")
    return None if text else tr("Password is required")


def speedtest_values(res: dict, direction: str) -> dict:
    """The figures of a status answer as its web keeps them: those of the other direction at
    zero, and the current one, the last of each series."""
    v = {k: res.get(k) or 0 for k in ("avg_rx", "avg_tx", "max_rx", "max_tx", "min_rx",
                                      "min_tx")}
    rx, tx = list(res.get("rx_speed") or []), list(res.get("tx_speed") or [])
    if direction == "tx":
        v.update(avg_rx=0, max_rx=0, min_rx=0)
        rx = []
    elif direction == "rx":
        v.update(avg_tx=0, max_tx=0, min_tx=0)
        tx = []
    v["curr_rx"], v["curr_tx"] = (rx[-1] if rx else 0), (tx[-1] if tx else 0)
    return v


def speedtest_done(res: dict, duration: int) -> bool:
    """Its end: the state 10, or the time asked for gone by."""
    return _int(res.get("state")) == 10 or float(res.get("duration") or 0) >= duration


def ms_to_time(ms: float) -> str:
    """The web's msToTime: minutes:seconds:milliseconds."""
    ms = int(ms)
    return f"{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}:{ms % 1000:03d}"


def speedtest_text(v: dict, direction: str, ping: float | None, stage: str,
                   elapsed_ms: float) -> str:
    """Its panel: the stage and the time, the ping, and TX and RX (Mbps) as asked."""
    lines = [f"{stage}  {ms_to_time(elapsed_ms)}",
             f"{tr('Ping')}: " + (f"{js_to_fixed(ping, 2)} ms" if ping else tr("N/A"))]
    for side in ("tx", "rx"):
        if direction in (side, "dx"):
            lines.append(f"{side.upper()}: {js_to_fixed(v['curr_' + side], 2)} Mbps   "
                         + tr("avg.") + f" {js_to_fixed(v['avg_' + side], 2)} Mbps   "
                         + tr("max") + f" {js_to_fixed(v['max_' + side], 2)} Mbps")
    return "\n".join(lines)
