"""The stations connected (airOS 8): what the stations table of its web's main page shows of
each one (status.cgi, wireless.sta), with its formats."""
from __future__ import annotations

from ..core.i18n import tr
from ..core.config import _int, js_round, js_to_fixed
from .firmware import fmt_version
from .status import fmt_distance, remote_rows

BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"     # "Kick All": the web kicks this one


def fmt_bps(bps: float | None) -> str:
    """The web's formatBPS: bps, kbps (1 decimal) or Mbps (2 decimals)."""
    if bps is None:
        return "-"
    if js_round(bps) < 1000:
        return f"{js_round(bps)} bps"
    if js_round(bps / 1000) < 1000:
        return f"{js_to_fixed(bps / 1000, 1)} kbps"
    return f"{js_to_fixed(bps / 1e6, 2)} Mbps"


def fmt_elapsed(seconds) -> str:
    """The web's getElapsedTime: HH:MM:SS, with the days before."""
    s = _int(seconds) or 0
    days, rest = divmod(s, 86400)
    text = f"{rest // 3600:02d}:{rest // 60 % 60:02d}:{rest % 60:02d}"
    if days:
        text = f"{days} {tr('day') if days == 1 else tr('days')} {text}"
    return text


def link_potential(sta: dict) -> str:
    """The web's "Link Potential": the mean of the average link scores, or n/a."""
    text = js_to_fixed(((_int(sta.get("dl_avg_linkscore")) or 0)
                      + (_int(sta.get("ul_avg_linkscore")) or 0)) / 2, 1)
    if float(text) == 0:
        return "n/a"
    return "100%" if float(text) > 100 else f"{text}%"


def _mbps(kbps) -> str:
    value = _int(kbps)
    return f"{value / 1000:.2f} Mbps" if value is not None else "-"


def station_rows(status: dict, before: dict | None = None, seconds: float | None = None
                 ) -> list[dict]:
    """The stations of status.cgi with what the web's table shows of each one. before and
    seconds: the bytes of each station in an earlier read and the time since then, for the
    throughput (the web takes it from its own reads, too)."""
    w = status.get("wireless") or {}
    mode = str(w.get("mode") or "")
    ptmp_ap = mode.startswith("ap") and "-ptp" not in mode
    rows = []
    for sta in w.get("sta") or []:
        if not isinstance(sta, dict):
            continue
        remote = sta.get("remote") or {}
        airmax = sta.get("airmax") or {}
        stats = sta.get("stats") or {}
        mac = str(sta.get("mac") or "")
        rx, tx = _int(stats.get("rx_bytes")), _int(stats.get("tx_bytes"))
        thr_rx = thr_tx = None
        old = (before or {}).get(mac)
        if old and None not in old and seconds and seconds > 0 and rx is not None \
                and tx is not None:
            thr_rx = max(0, rx - old[0]) * 8 / seconds
            thr_tx = max(0, tx - old[1]) * 8 / seconds
        rows.append({
            "mac": mac, "name": str(remote.get("hostname") or ""),
            "model": str(remote.get("platform") or ""),
            "firmware": fmt_version(str(remote.get("version") or "")) or "",
            "signal": _int(sta.get("signal")), "remote_signal": _int(remote.get("signal")),
            "noise": _int(sta.get("noisefloor")), "latency": _int(sta.get("tx_latency")),
            "distance": _int(sta.get("distance")), "ip": str(sta.get("lastip") or ""),
            "uptime": _int(sta.get("uptime")),
            "downlink": _mbps(airmax.get("dl_capacity")),
            "uplink": _mbps(airmax.get("ul_capacity")),
            "airtime_tx": f"{(_int((airmax.get('tx') or {}).get('usage')) or 0) / 10:.1f} %",
            "airtime_rx": f"{(_int((airmax.get('rx') or {}).get('usage')) or 0) / 10:.1f} %",
            "potential": link_potential(sta), "bytes": (rx, tx),
            "throughput_rx": thr_rx, "throughput_tx": thr_tx,
            "panel": remote_rows(sta, ptmp_ap)})
    return rows


def station_text(row: dict) -> str:
    """One station: the columns of the web's table and the panel of its main page for that
    end, in blocks: the device, Wireless and Ethernet."""
    def dbm(v):
        return f"{v} dBm" if v is not None else "-"
    ip = row["ip"] if row["ip"] not in ("", "0.0.0.0") else tr("unknown")
    panel = row.get("panel") or {}
    device = [(tr("Device Name"), row["name"] or "-"), (tr("Device Model"), row["model"] or "-"),
              (tr("Firmware"), row["firmware"] or "-"), (tr("Station MAC"), row["mac"]),
              (tr("Last IP"), ip)]
    lines = [(tr("Signal"), dbm(row["signal"])),
             (tr("Remote Signal"), dbm(row["remote_signal"])), (tr("Noise"), dbm(row["noise"])),
             (tr("Latency"), f"{row['latency']} ms" if row["latency"] is not None else "-"),
             (tr("Distance"), fmt_distance(row["distance"]) or "-"),
             (tr("Downlink Capacity"), row["downlink"]), (tr("Uplink Capacity"), row["uplink"]),
             (tr("Airtime TX"), row["airtime_tx"]), (tr("Airtime RX"), row["airtime_rx"]),
             (tr("Throughput RX"), fmt_bps(row["throughput_rx"])),
             (tr("Throughput TX"), fmt_bps(row["throughput_tx"])),
             (tr("Link Potential"), row["potential"]),
             (tr("Connection Time"), fmt_elapsed(row["uptime"]))]
    seen = {label for label, _ in device + lines} | {tr("Version")}
    device += [(k, v) for k, v in panel.get("main") or [] if k not in seen]
    extra = [(k, v) for k, v in panel.get("wireless") or [] if k not in seen]
    mode = [(k, v) for k, v in extra if k == tr("Wireless Mode")]      # (first, as in the web)
    lines = mode + lines + [(k, v) for k, v in extra if k != tr("Wireless Mode")]
    eth = panel.get("ethernet") or []
    width = max(len(label) for label, _ in device + lines + eth)

    def block(rows):
        return [f"{label:<{width}}  {value}" for label, value in rows]
    out = block(device) + ["", tr("── Wireless ──")] + block(lines)
    if eth:
        out += ["", tr("── Ethernet ──")] + block(eth)
    return "\n".join(out)
