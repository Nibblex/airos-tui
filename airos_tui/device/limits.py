"""Device limits: the same calculation the web does in the browser (power, country,
channels, antenna) and the checks before applying."""
from __future__ import annotations

import math
import re
from typing import NamedTuple

from ..core.i18n import N_, tr
from ..core.errors import AirOSError
from ..core.config import Config, _freqs, _int, INT_RE, is_digits
from ..core.validation import Problem, SERVICE_PORTS
from .network import net_problems


# Generic power range when the device limits cannot be read.
GENERIC_TX_RANGE = (-10, 40)
# Countries with FCC rules in the web's calculation (airOS 8 and airOS 6 use different
# lists).
FCC_COUNTRIES = {"v8": {124, 630, 840},
                 "v6": {84, 124, 2124, 188, 214, 222, 320, 340, 388, 484, 530, 591, 630,
                        780, 840}}
LICENSED_CC = 511
# airOS 6: the radio's capabilities ($radio1_caps in board.inc). board.info does not have
# them; the System page publishes them (its radio_caps), and the client adds them to the board
# under this key.
V6_CAPS_KEY = "radio.1.caps"
# Ports airOS 8 keeps for itself (PortsUsedBySystem in the web).
SYSTEM_PORTS = {50841: "airView", 53762: N_("speed test"), 61780: "ubridge"}


def parse_kv(text: str) -> dict[str, str]:
    out = {}
    for line in text.replace("\r", "").split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


class Channel(NamedTuple):
    band: str        # "A" (5 GHz), "G" (2.4 GHz), "AD" (60 GHz)
    bw: int          # channel width in MHz
    control: int     # control frequency (radio.1.freq)
    center: int      # center frequency (radio.1.center.1.freq on airOS 8)
    eirp: float      # maximum EIRP the country allows, in dBm
    dfs: bool
    obey_ok: bool    # airOS 6: the web offers it with "obey regulations" enabled
    ext: int = 0     # airOS 6: regdomain flags (extension channel above: &3 == 3, below: &5 == 5)


def _band_of(center: int) -> str:
    return "AD" if center > 50000 else "A" if center > 4000 else "G"


def parse_regdomain_v8(data) -> list[Channel]:
    """`rd` from the login or `data` from /api/regdomain: objects or text lines."""
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            if isinstance(item, dict):
                control = int(item["control"])
                center = int(item.get("center") or control)
                out.append(Channel(_band_of(center), int(item["bw"]), control, center,
                                   float(item["maxEirp"]), bool(item.get("dfs")), True))
            elif isinstance(item, str):
                p = item.split()    # control center channel mode width power ext
                if len(p) >= 6 and is_digits(p[0]):
                    ext = int(p[6]) if len(p) > 6 and INT_RE.fullmatch(p[6]) else 0
                    out.append(Channel(p[3].upper(), int(p[4]), int(p[0]), int(p[1]),
                                       float(p[5]), bool(ext & 128), True))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parse_regdomain_v6(lines) -> list[Channel]:
    """/regdomain_by_country.cgi: the same columns parse_full_regdomain reads."""
    out = []
    for line in lines if isinstance(lines, list) else []:
        p = line.split() if isinstance(line, str) else []
        if len(p) < 5 or not is_digits(p[0]):
            continue
        try:
            flags = int(p[5]) if len(p) > 5 and INT_RE.fullmatch(p[5]) else 0
            obey_ok = int(p[7]) != 0 if len(p) > 7 and INT_RE.fullmatch(p[7]) else True
            freq = int(p[0])
            out.append(Channel(p[2].upper(), int(p[3]), freq, freq, float(p[4]),
                               flags & 9 == 9, obey_ok, flags))
        except ValueError:
            continue
    return out


def _sample(values: list[int], n: int = 6) -> str:
    if len(values) <= n:
        return ", ".join(map(str, values))
    return ", ".join(map(str, values[:n - 1])) + f", … {values[-1]}"


def board_antennas(board: dict) -> list[dict]:
    """The antennas of board.info (radio.1.antenna.N.*), in their order."""
    pat = re.compile(r"^radio\.1\.antenna\.(\d+)\.id$")
    out = []
    for n in sorted({int(m.group(1)) for m in map(pat.match, board) if m}):
        p = f"radio.1.antenna.{n}."
        ant_id = _int(board.get(p + "id"))
        if ant_id is not None:
            out.append({"id": ant_id, "name": board.get(p + "name") or "",
                        "gain": _int(board.get(p + "gain")) or 0,
                        "builtin": _int(board.get(p + "builtin")) == 1,
                        "band": _int(board.get(p + "band")) or 0})
    return out


def v6_active_antenna(cfg: Config, board: dict) -> tuple[int, int]:
    """airOS 6's get_active_antenna(), as its link page shows it: (the antenna, its gain);
    the configured gain goes to the board antenna closest to it."""
    ants = board_antennas(board)
    by_id = {a["id"]: a for a in ants}
    def_id = _int(board.get("radio.1.def_antenna"))
    gain_cfg = _int(cfg.get("radio.1.antenna.gain")) if cfg.get("radio.1.antenna.gain") else None
    id_cfg = _int(cfg.get("radio.1.antenna.id")) if cfg.get("radio.1.antenna.id") else None
    if gain_cfg is not None and gain_cfg != -1:
        idx = id_cfg if id_cfg not in (None, -1) else (def_id or 0)
        if len(ants) <= 1 and not any(a["builtin"] for a in ants):   # (none, too)
            return idx, gain_cfg
        best = min(abs(a["gain"] - gain_cfg) for a in ants)
        near = [a for a in ants if abs(a["gain"] - gain_cfg) == best]
        same = next((a for a in near if a["id"] == idx and a["gain"] == gain_cfg), None)
        pick = same or near[0]
        return pick["id"], pick["gain"]
    for idx in (id_cfg, def_id):
        if idx is not None and idx != -1 and idx in by_id:
            return idx, by_id[idx]["gain"]
    return 0, 0


class DeviceLimits:
    """Hardware (board.info) and country (regdomain) limits, as the web uses them."""

    def __init__(self, flavor: str, board: dict[str, str], fetch_regdomain):
        self.flavor, self.board = flavor, board
        self._fetch = fetch_regdomain
        self._cache: dict = {}

    # -- hardware ---------------------------------------------------------------

    def tx_range(self) -> tuple[int, int] | None:
        hi = _int(self.board.get("radio.1.txpower.max"))
        if hi is None or hi < 1:
            return None
        lo = _int(self.board.get("radio.1.txpower.min"))
        return (GENERIC_TX_RANGE[0] if lo is None else lo), hi

    def supported_bw(self) -> list[int]:
        raw = (self.board.get("radio.1.chanbw") or "").replace('"', "")
        return sorted({int(x) for x in raw.split(",") if is_digits(x.strip())})

    def antenna_gain(self, cfg: Config) -> tuple[int, str]:
        """The antenna gain its web counts for the legal limit, and where it comes from. On
        airOS 6, its link page's: the board antenna the configured gain goes to (a stale gain
        in the configuration is not the one it counts)."""
        if self.flavor == "v6":
            ant_id, gain = v6_active_antenna(cfg, self.board)
            ant = next((a for a in board_antennas(self.board) if a["id"] == ant_id), None)
            if ant is not None and (ant["builtin"] or ant["gain"] == gain):
                return gain, tr("device antenna")
            if cfg.get("radio.1.antenna.gain"):
                return gain, tr("configured")
            return gain, tr("unknown, 0 assumed")
        gain = _int(cfg.get("radio.1.antenna.gain"))
        if gain is not None and gain >= 0:
            return gain, tr("configured")
        ant_id = _int(cfg.get("radio.1.antenna.id"))
        if ant_id is None or ant_id < 0:
            ant_id = _int(self.board.get("radio.1.def_antenna"))
        for k, v in self.board.items():
            m = re.fullmatch(r"radio\.1\.antenna\.(\d+)\.id", k)
            if m and ant_id is not None and _int(v) == ant_id:
                gain = _int(self.board.get(f"radio.1.antenna.{m.group(1)}.gain"))
                if gain is not None:
                    return gain, tr("device antenna")
        return 0, tr("unknown, 0 assumed")

    def cable_loss(self, cfg: Config) -> int:
        """The cable loss its web counts: on airOS 6 none with a built-in antenna (its page
        disables that field), whatever the configuration keeps."""
        if self.flavor == "v6":
            ant_id, _ = v6_active_antenna(cfg, self.board)
            if any(a["id"] == ant_id and a["builtin"] for a in board_antennas(self.board)):
                return 0
        return _int(cfg.get("radio.1.cable.loss")) or 0

    # -- country and channel --------------------------------------------------------

    def country(self, cfg: Config) -> int:
        for k in ("radio.1.countrycode", "radio.countrycode"):
            cc = _int(cfg.get(k))
            if cc:
                return cc
        return _int(self.board.get("radio.1.ccode")) or 840

    @property
    def scan_prefix(self) -> str:
        """The frequency list: radio.1.scan_list on airOS 8, wireless.1.scan_list on 6."""
        return "wireless.1.scan_list" if self.flavor == "v6" else "radio.1.scan_list"

    def bandwidth(self, cfg: Config) -> int:
        ieee = (cfg.get("radio.1.ieee_mode") or "").lower()
        if self.flavor == "v6" and ieee[6:8] == "40":     # 11naht40plus/minus
            return 40
        bw = _int(cfg.get("radio.1.chanbw"))
        if bw or self.flavor != "v6":
            return bw or 20
        # airOS 6 without one (as its factory configuration): link.cgi asks the radio, and a
        # station's 20 it shows as 40 (Automatic 20/40)
        return 20 if (cfg.get("radio.1.mode") or "").lower() == "master" else 40

    def channels(self, cc: int, bw: int) -> list[Channel] | None:
        key = (cc, bw if self.flavor == "v6" else 0)
        if key not in self._cache:
            try:
                self._cache[key] = self._fetch(cc, bw)
            except AirOSError:
                self._cache[key] = None
        return self._cache[key]

    def context(self, cfg: Config) -> dict:
        cc, bw = self.country(cfg), self.bandwidth(cfg)
        ieee = (cfg.get("radio.1.ieee_mode") or "").lower()
        if self.flavor == "v8":
            obey = (cfg.get("radio.1.reg_obey") or "enabled") == "enabled"
            band = "G" if ieee.startswith("11ng") else "A"
        else:
            # (with country 764 its page always calculates it, force_obey_regulatory(): its
            # power slider too, as the Wireless menu shows it, whatever the keys say)
            obey = cc == 764 or ((cfg.get("radio.1.reg_obey") or "disabled") == "enabled"
                                 and (cfg.get("radio.1.obey") or "enabled") == "enabled")
            band = "A" if ieee[:6] == "11naht" or ieee == "a" else "B" if ieee == "b" else "G"
        raw = self.channels(cc, bw)              # (None: not known; []: none at this width)
        every = raw or []
        if every and band not in {c.band for c in every}:      # e.g. ieee_mode=auto
            band = "A" if any(c.band == "A" for c in every) else every[0].band
        scan = None
        if (cfg.get(f"{self.scan_prefix}.status") or "") == "enabled":
            scan = _freqs(cfg.get(f"{self.scan_prefix}.channels"))
        return {
            "cc": cc, "bw": bw, "band": band, "obey": obey, "known": raw is not None,
            "chans": [c for c in every if c.band == band and c.bw == bw
                      and (c.obey_ok or not obey)],
            "is_ap": (cfg.get("radio.1.mode") or "").lower() == "master",
            "freq": _int(cfg.get("radio.1.freq")) or 0,
            "center": _int(cfg.get("radio.1.center.1.freq")) or 0,
            "scan": scan,
        }

    def power_limit(self, cfg: Config) -> dict | None:
        """{'min','hw_max','max','legal'|'note','low'}; None if there is no hardware data.
        low: the calculation gives less than the device's minimum (max is then that minimum,
        as the web's slider; airOS 8 warns before saving)."""
        rng = self.tx_range()
        if rng is None:
            return None
        lo, hi = rng
        res = {"min": lo, "hw_max": hi, "max": hi, "legal": None, "note": None, "low": False}
        ctx = self.context(cfg)
        cc, band, freq = ctx["cc"], ctx["band"], ctx["freq"]
        if not ctx["obey"]:
            res["note"] = tr("the EIRP calculation is disabled (radio.1.reg_obey)")
            return res
        if cc == LICENSED_CC:
            res["note"] = tr('"Licensed" country: the web applies no legal limit')
            return res
        if self.flavor == "v6" and (cc in (124, 2124) or (cc in (840, 630) and band == "A")):
            unii = (self.board.get("board.fcc_unii_activated") or "0") == "1"
            if not ((band == "A" and freq < 5730) or (cc in (840, 630, 2124) and unii)):
                res["note"] = tr("on this channel the web applies no legal limit")
                return res
        chans = ctx["chans"]
        if not chans:
            res["note"] = (tr("Country {cc} has no {bw} MHz channels for this device.",
                              cc=ctx["cc"], bw=ctx["bw"]) if ctx["known"]
                           else tr("I could not read the channels allowed in the country"))
            return res
        if ctx["is_ap"] and freq:
            match = [c for c in chans if c.control == freq and (
                self.flavor == "v6" or not ctx["center"] or c.center == ctx["center"])]
            where = f"{freq} MHz" if match else tr("the most permissive channel of the country")
        else:
            match = [c for c in chans if ctx["scan"] and c.control in ctx["scan"]]
            where = (tr("your frequency list") if match
                     else tr("the most permissive channel of the country"))
        eirp = max(c.eirp for c in (match or chans))
        gain, gain_src = self.antenna_gain(cfg)
        loss = self.cable_loss(cfg)
        limit = eirp - gain + loss
        formula = tr("EIRP {eirp} dBm on {where} − antenna {gain} dBi ({source}) + cable "
                     "{loss} dB", eirp=f"{eirp:g}", where=where, gain=gain, source=gain_src,
                     loss=loss)
        excluded = {124} if self.flavor == "v8" else {124, 2124}
        if (cc in FCC_COUNTRIES[self.flavor] and cc not in excluded and band == "G"
                and gain >= 6):
            limit = 32 + loss - math.ceil(gain / 3)
            formula = tr("2.4 GHz FCC rule: 32 + cable {loss} − antenna {gain}/3",
                         loss=loss, gain=gain)
        res["max"] = max(lo, min(hi, math.floor(limit)))
        res["low"] = limit < lo
        res["legal"] = (tr("{max} dBm, the minimum of the device (the calculation gives "
                           "{limit} dBm: {formula}), country {cc}", max=res["max"],
                           limit=f"{limit:g}", formula=formula, cc=cc) if res["low"]
                        else tr("{max} dBm = {formula}, country {cc}", max=res["max"],
                                formula=formula, cc=cc))
        return res

    def freq_problems(self, cfg: Config) -> list[Problem]:
        out = []
        bws = self.supported_bw()
        bw_cfg = _int(cfg.get("radio.1.chanbw"))
        if bws and bw_cfg and bw_cfg not in bws:
            out.append(Problem("warning", "radio.1.chanbw",
                               tr("This device has no {bw} MHz channels (it supports "
                                  "{supported} MHz).", bw=bw_cfg,
                                  supported=", ".join(map(str, bws)))))
        ctx = self.context(cfg)
        if not ctx["known"]:
            return out
        bw, cc, chans = ctx["bw"], ctx["cc"], ctx["chans"]
        if not chans:
            out.append(Problem("warning", "radio.1.chanbw",
                               tr("Country {cc} has no {bw} MHz channels for this device.",
                                  cc=cc, bw=bw)))
            return out
        controls = sorted({c.control for c in chans})
        freq = ctx["freq"]
        if ctx["is_ap"] and freq and freq not in controls:
            out.append(Problem("warning", "radio.1.freq",
                               tr("{freq} MHz is not among the frequencies the web offers with "
                                  "{bw} MHz in country {cc} ({sample}).", freq=freq, bw=bw,
                                  cc=cc, sample=_sample(controls))))
        elif self.flavor == "v8" and ctx["is_ap"] and freq and ctx["center"]:
            centers = sorted({c.center for c in chans if c.control == freq})
            if ctx["center"] not in centers:
                out.append(Problem("warning", "radio.1.center.1.freq",
                                   tr("With {freq} MHz and {bw} MHz, the center frequency "
                                      "should be {centers} MHz, not {center} MHz.",
                                      freq=freq, bw=bw,
                                      centers=f" {tr('or')} ".join(map(str, centers)),
                                      center=ctx["center"])))
        bad = [f for f in ctx["scan"] or [] if f not in controls]
        if bad:
            out.append(Problem("warning", f"{self.scan_prefix}.channels",
                               tr("Some frequencies of the list are not allowed by country "
                                  "{cc} with {bw} MHz: {sample}.", cc=cc, bw=bw,
                                  sample=_sample(bad))))
        return out

    def freq_hint(self, cfg: Config) -> str:
        ctx = self.context(cfg)
        if not ctx["chans"]:
            return ""
        return tr("Frequencies the web offers with {bw} MHz in country {cc}: {sample}.",
                  bw=ctx["bw"], cc=ctx["cc"],
                  sample=_sample(sorted({c.control for c in ctx["chans"]}), 12))


def check_config(cfg: Config, limits: DeviceLimits | None) -> list[Problem]:
    """What the web would not let you save (error) or would warn about (warning)."""
    out: list[Problem] = []
    raw = cfg.get("radio.1.txpower")
    if raw not in (None, ""):
        power = int(raw) if INT_RE.fullmatch(raw) else None     # (not " 20" or "1_0")
        pl = limits.power_limit(cfg) if limits else None
        if power is None:
            out.append(Problem("error", "radio.1.txpower",
                               tr("The power must be a whole number in dBm.")))
        elif pl is None:
            lo, hi = GENERIC_TX_RANGE
            if not lo <= power <= hi:
                out.append(Problem("error", "radio.1.txpower",
                                   tr("{power} dBm is outside the generic range ({lo} to {hi} "
                                      "dBm); I could not read this device's limits.",
                                      power=power, lo=lo, hi=hi)))
        elif not pl["min"] <= power <= pl["hw_max"]:
            out.append(Problem("error", "radio.1.txpower",
                               tr("This device supports {min} to {max} dBm; {power} dBm is out "
                                  "of range.", min=pl["min"], max=pl["hw_max"], power=power)))
        elif power > pl["max"]:                # (both webs refuse to save it)
            out.append(Problem("error", "radio.1.txpower",
                               tr("{power} dBm exceeds the legal limit the web calculates: "
                                  "{legal}. The web does not allow it: the power must be "
                                  "between {min} and {max} dBm.", power=power,
                                  legal=pl["legal"], min=pl["min"], max=pl["max"])))
        elif pl["low"] and limits.flavor == "v8":    # (its checkEirp(), when saving)
            out.append(Problem("warning", "radio.1.txpower",
                               tr("The legal limit is below the minimum power of this device: "
                                  "{legal}. The power may exceed the maximum EIRP; the web "
                                  "asks before saving (EIRP Warning).", legal=pl["legal"])))
    if limits:
        out += limits.freq_problems(cfg)
    used: dict[int, str] = {}
    for key, status_key in SERVICE_PORTS.items():
        port = _int(cfg.get(key))
        if port is None or (cfg.get(status_key) or "enabled") == "disabled":
            continue
        if port in used:
            out.append(Problem("error", key, tr("{key} and {other} use the same port ({port}): "
                                                "one of the two services would not start.",
                                                key=key, other=used[port], port=port)))
        used.setdefault(port, key)
        if limits and limits.flavor == "v8" and port in SYSTEM_PORTS:
            out.append(Problem("warning", key, tr("Port {port} is used by the device itself "
                                                  "({name}).", port=port,
                                                  name=tr(SYSTEM_PORTS[port]))))
    out += net_problems(cfg, limits.flavor if limits else None)
    return out


def new_problems(base: Config, cand: Config, limits: DeviceLimits | None) -> list[Problem]:
    """Problems of cand that base did not have (the ones the change brings)."""
    before = set(check_config(base, limits))
    return [p for p in check_config(cand, limits) if p not in before]
