"""Tests of the limits (hardware + country), the validations and their use in the App."""
import sys
import threading

from _common import check, fails, FakeUI, suite  # noqa: E402
T, S, XDG = suite("limits")
tr = T.tr


BOARD = {"radio.1.txpower.max": "25", "radio.1.txpower.min": "-4",
         "radio.1.chanbw": "10,20,40,80", "radio.1.ccode": "484",
         "radio.1.def_antenna": "1", "radio.1.antenna.1.id": "1",
         "radio.1.antenna.1.gain": "16", "radio.1.antenna.2.id": "2",
         "radio.1.antenna.2.gain": "23"}
RD8 = S.regdomain_v8()


def limits(flavor="v8", board=None, rd=None):
    rd = RD8 if rd is None else rd
    if flavor == "v8":
        fetch = lambda cc, bw: T.parse_regdomain_v8(rd) if cc == 484 else None  # noqa: E731
    else:
        fetch = lambda cc, bw: (T.parse_regdomain_v6(S.regdomain_v6(bw))  # noqa: E731
                                if cc == 484 else None)
    return T.DeviceLimits(flavor, dict(BOARD if board is None else board), fetch)


def cfg(**kw):
    base = {"radio.1.countrycode": "484", "radio.1.mode": "Master", "radio.1.freq": "5745",
            "radio.1.center.1.freq": "5745", "radio.1.chanbw": "20",
            "radio.1.ieee_mode": "11acvht20", "radio.1.reg_obey": "enabled",
            "radio.1.obey": "enabled", "radio.1.antenna.gain": "16",
            "radio.1.cable.loss": "0", "radio.1.txpower": "20",
            "httpd.port": "80", "sshd.port": "22", "httpd.https.port": "443"}
    for name, v in kw.items():
        k = name.replace("__", ".")
        if v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return T.Config("".join(f"{k}={v}\n" for k, v in base.items()))


def unit():
    print("=== parsers ===")
    ch = T.parse_regdomain_v8([{"control": 5745, "center": 5755, "bw": 40, "ch": 149,
                                "maxEirp": 36, "dfs": True}, {"bad": 1}, "x"])
    check("v8 dict", ch == [T.Channel("A", 40, 5745, 5755, 36.0, True, True)], str(ch))
    ch = T.parse_regdomain_v8(["Mexico (MX),0,484", "5745 5745 149 A 20 30.0 128", ""])
    check("v8 string", ch == [T.Channel("A", 20, 5745, 5745, 30.0, True, True)], str(ch))
    ch = T.parse_regdomain_v6(["Mexico (MX), 0x0, 484", "5745 149 A 20 36.0 9 1 0",
                               "2412 1 G 20 30.0 1 1", ""])
    check("v6 lines", ch == [T.Channel("A", 20, 5745, 5745, 36.0, True, False, 9),
                             T.Channel("G", 20, 2412, 2412, 30.0, False, True, 1)], str(ch))
    check("v8 garbage", T.parse_regdomain_v8(None) == [] and T.parse_regdomain_v6({}) == [])

    print("=== power limit ===")
    L = limits()
    pl = L.power_limit(cfg())
    check("AP 5745 -> 20", pl["max"] == 20 and pl["hw_max"] == 25 and pl["min"] == -4, str(pl))
    pl = L.power_limit(cfg(radio__1__freq="5180", radio__1__center__1__freq="5180"))
    check("AP 5180 -> 14", pl["max"] == 14, str(pl))
    pl = L.power_limit(cfg(radio__1__mode="Managed", radio__1__freq="0"))
    check("station -> best channel 20", pl["max"] == 20, str(pl))
    pl = L.power_limit(cfg(radio__1__mode="Managed", **{
        "radio.1.scan_list.status": "enabled", "radio.1.scan_list.channels": "5180,5200"}))
    check("station with a list -> 14", pl["max"] == 14
          and tr("your frequency list") in pl["legal"], str(pl))
    pl = L.power_limit(cfg(radio__1__mode="Managed", **{
        "wireless.1.scan_list.status": "enabled", "wireless.1.scan_list.channels": "5180,5200"}))
    check("airOS 8 ignores wireless.1.scan_list (it is airOS 6's)", pl["max"] == 20, str(pl))
    pl = L.power_limit(cfg(radio__1__reg_obey="disabled"))
    check("no EIRP calculation -> hw", pl["max"] == 25 and pl["legal"] is None
          and "reg_obey" in pl["note"], str(pl))
    pl = L.power_limit(cfg(radio__1__countrycode="511"))
    check("Licensed -> hw", pl["max"] == 25 and "Licensed" in pl["note"], str(pl))
    pl = L.power_limit(cfg(radio__1__countrycode="999"))
    check("country without data -> hw + note", pl["max"] == 25 and tr(
        "I could not read the channels allowed in the country") in pl["note"], str(pl))
    pl = L.power_limit(cfg(radio__1__antenna__gain=None, radio__1__antenna__id="2"))
    check("gain from board.info", pl["max"] == 13 and tr("device antenna") in pl["legal"],
          str(pl))
    pl = L.power_limit(cfg(radio__1__antenna__gain="30"))
    check("30 dBi antenna -> 6", pl["max"] == 6, str(pl))
    pl = L.power_limit(cfg(radio__1__antenna__gain="40", radio__1__freq="5180",
                           radio__1__center__1__freq="5180"))
    check("never below the hardware's minimum", pl["max"] == -4, str(pl))
    check("  and it says so: the device's minimum, and what the calculation gives", pl["low"]
          and pl["legal"] == tr("{max} dBm, the minimum of the device (the calculation gives "
                                "{limit} dBm: {formula}), country {cc}", max=-4, limit="-10",
                                formula=tr("EIRP {eirp} dBm on {where} − antenna {gain} dBi "
                                           "({source}) + cable {loss} dB", eirp="30",
                                           where="5180 MHz", gain=40,
                                           source=tr("configured"), loss=0), cc=484),
          str(pl))
    G = limits(board=dict(BOARD, **{"radio.1.txpower.max": "30"}),
               rd=[{"control": 2412, "center": 2412, "bw": 20, "maxEirp": 36}])
    g = cfg(radio__1__countrycode="840", radio__1__ieee_mode="11nght20", radio__1__freq="2412",
            radio__1__center__1__freq="2412", radio__1__antenna__gain="13")
    G._fetch = lambda cc, bw: T.parse_regdomain_v8([{"control": 2412, "center": 2412, "bw": 20,
                                                     "maxEirp": 36}])
    pl = G.power_limit(g)
    check("2.4 GHz FCC rule", pl["max"] == 27 and "FCC" in pl["legal"], str(pl))
    V6 = limits("v6")
    pl = V6.power_limit(cfg(radio__1__ieee_mode="11naht20", radio__1__center__1__freq=None))
    check("v6 AP 5745 -> 20", pl["max"] == 20, str(pl))
    pl = V6.power_limit(cfg(radio__1__ieee_mode="11naht20", radio__1__reg_obey="enabled",
                            radio__1__obey="disabled"))
    check("v6 obey disabled -> hw", pl["max"] == 25 and pl["note"], str(pl))
    V6b = T.DeviceLimits("v6", dict(BOARD), lambda cc, bw: T.parse_regdomain_v6(S.regdomain_v6(bw)))
    pl = V6b.power_limit(cfg(radio__1__countrycode="840", radio__1__ieee_mode="11naht20"))
    check("v6 FCC 5.8 GHz without a limit (as the web)", pl["max"] == 25 and tr(
        "on this channel the web applies no legal limit") in pl["note"], str(pl))
    pl = V6b.power_limit(cfg(radio__1__countrycode="840", radio__1__ieee_mode="11naht20",
                             radio__1__freq="5180"))
    check("v6 FCC 5.1 GHz with a limit", pl["max"] == 14, str(pl))
    check("v6 width 40 from ieee_mode",
          V6.bandwidth(cfg(radio__1__ieee_mode="11naht40plus")) == 40)
    check("v6 without a width (factory default): the station, 40 (Auto 20/40, as link.cgi); the "
          "AP, its mode's", V6.bandwidth(cfg(radio__1__chanbw=None, radio__1__mode="managed",
                                             radio__1__ieee_mode="auto")) == 40
          and V6.bandwidth(cfg(radio__1__chanbw=None, radio__1__mode="master",
                               radio__1__ieee_mode="11naht20")) == 20
          and limits().bandwidth(cfg(radio__1__chanbw=None, radio__1__mode="managed")) == 20)
    builtin = {k: v for k, v in BOARD.items() if not k.startswith("radio.1.antenna.2.")}
    builtin["radio.1.antenna.1.builtin"] = "1"
    pl = limits("v6", board=builtin).power_limit(cfg(
        radio__1__ieee_mode="11naht20", radio__1__antenna__gain="10", radio__1__cable__loss="5"))
    check("v6 with a 16 dBi built-in antenna, and an old gain of 10 and a loss of 5 saved: it "
          "counts 16 and no cable, as its page (not 11 dB more)", pl["max"] == 20
          and tr("EIRP {eirp} dBm on {where} − antenna {gain} dBi ({source}) + cable {loss} dB",
                 eirp="36", where="5745 MHz", gain=16, source=tr("device antenna"), loss=0)
          in pl["legal"], str(pl))
    pl = V6.power_limit(cfg(radio__1__ieee_mode="11naht20", radio__1__antenna__gain="30"))
    check("v6 with 16 and 23 dBi antennas on the board and 30 saved: it counts the nearest one "
          "(23), as its page", pl["max"] == 13
          and tr("EIRP {eirp} dBm on {where} − antenna {gain} dBi ({source}) + cable {loss} dB",
                 eirp="36", where="5745 MHz", gain=23, source=tr("device antenna"), loss=0)
          in pl["legal"], str(pl))

    print("=== problems ===")
    base = cfg()

    def probs(**kw):
        return [(p.level, p.key) for p in T.new_problems(base, cfg(**kw), L)]
    check("20 dBm without problems", probs() == [])
    check("22 dBm -> error, as in both webs (they do not save it)",
          probs(radio__1__txpower="22") == [("error", "radio.1.txpower")])
    over = [p.msg for p in T.new_problems(base, cfg(radio__1__txpower="22"), L)]
    check("  it says the limit and the range the power must be in", over == [tr(
        "{power} dBm exceeds the legal limit the web calculates: {legal}. The web does not "
        "allow it: the power must be between {min} and {max} dBm.", power=22,
        legal=L.power_limit(base)["legal"], min=-4, max=20)], str(over))
    low = dict(radio__1__antenna__gain="40", radio__1__freq="5180",
               radio__1__center__1__freq="5180", radio__1__txpower="-4")
    check("limit below the device's minimum: airOS 8 warns before saving (its checkEirp), "
          "airOS 6 does not", probs(**low) == [("warning", "radio.1.txpower")]
          and [(p.level, p.key) for p in T.new_problems(base, cfg(**low), limits("v6"))] == [],
          str(probs(**low)))
    check("26 dBm -> hw error", probs(radio__1__txpower="26") == [("error", "radio.1.txpower")])
    check("-5 dBm -> hw error", probs(radio__1__txpower="-5") == [("error", "radio.1.txpower")])
    check("abc -> error", probs(radio__1__txpower="abc") == [("error", "radio.1.txpower")])
    check("frequency not allowed -> warning",
          ("warning", "radio.1.freq") in probs(radio__1__freq="5150",
                                               radio__1__center__1__freq="5150"))
    check("inconsistent center (40 MHz) -> warning",
          probs(radio__1__chanbw="40", radio__1__center__1__freq="5775")
          == [("warning", "radio.1.center.1.freq")])
    check("right center (40 MHz), no warning",
          probs(radio__1__chanbw="40", radio__1__center__1__freq="5755") == [])
    check("unsupported width -> warning",
          ("warning", "radio.1.chanbw") in probs(radio__1__chanbw="30"))
    check("invalid frequency list -> warning",
          probs(**{"radio.1.scan_list.status": "enabled",
                   "radio.1.scan_list.channels": "5745, 5150"})
          == [("warning", "radio.1.scan_list.channels")])
    check("repeated port -> error", probs(sshd__port="80") == [("error", "sshd.port")])
    check("repeated port with its service off -> nothing",
          probs(sshd__port="80", sshd__status="disabled") == [])
    check("system port -> warning", probs(sshd__port="61780") == [("warning", "sshd.port")])
    nob = T.check_config(cfg(radio__1__txpower="45"), None)
    check("without the device's limits: generic range", [(p.level, p.key) for p in nob]
          == [("error", "radio.1.txpower")]
          and not T.check_config(cfg(radio__1__txpower="30"), None))
    nohw = limits(board={})
    check("board without txpower.max -> generic", nohw.power_limit(cfg()) is None
          and [p.level for p in T.check_config(cfg(radio__1__txpower="41"), nohw)] == ["error"])

    print("=== format ===")
    v = T.validate_value
    check("netmask ok", v("netconf.3.netmask", "255.255.255.0") is None)
    check("non-contiguous netmask", v("netconf.3.netmask", "255.0.255.0") is not None)
    check("inverted netmask", v("netconf.3.netmask", "0.0.0.255") is not None)
    check("route netmask as a prefix", v("route.1.netmask", "0") is None
          and v("route.1.netmask", "24") is None)
    check("route netmask 33", v("route.1.netmask", "33") is not None)
    check("port 0 / 70000", v("httpd.port", "0") and v("httpd.port", "70000")
          and v("httpd.port", "8080") is None)
    check("enabled/disabled", v("sshd.status", "on", "enabled") is not None
          and v("sshd.status", "disabled", "enabled") is None)
    check("numeric type", v("radio.1.ackdistance", "far", "600") is not None
          and v("radio.1.ackdistance", "900", "600") is None)
    check("gain 0..40", v("radio.1.antenna.gain", "41") is not None
          and v("radio.1.antenna.gain", "23") is None)
    check("IP gateway", v("route.1.gateway", "10.0.0.256") is not None)

    print("=== power report ===")
    st = {"wireless": {"txpower": 20, "polling": {"atpc_status": 0}}}
    ch = [("radio.1.txpower", "20", "22")]
    check("limited", tr("The device limited it.") in T.power_report(ch, st),
          T.power_report(ch, st))
    st2 = {"wireless": {"txpower": 18, "polling": {"atpc_status": 2}}}
    check("ATPC", tr("The automatic power control (ATPC: {state}) adjusts it by itself.",
                     state=tr("automatic")) in T.power_report(ch, st2), T.power_report(ch, st2))
    st3 = {"wireless": {"txpower": 22}}
    check("the same", tr("Power: the radio reports {real} dBm, the one you asked for.", real=22)
          in T.power_report(ch, st3))
    check("no power change", T.power_report([("x", "1", "2")], st) == "")
    check("status with ATPC", tr("TX power: {power}", power=f"18 dBm (ATPC: {tr('automatic')})")
          in T.short_status(st2))

    print("=== airOS 6: a country without channels at a width is not an unknown one ===")
    none_at = T.DeviceLimits("v6", dict(BOARD), lambda cc, bw: [] if cc == 484 else None)
    c10 = cfg(radio__1__chanbw="10", radio__1__ieee_mode="11naht20")
    said = tr("Country {cc} has no {bw} MHz channels for this device.", cc=484, bw=10)
    check("it warns, as for airOS 8; its power limit says the same, not that the channels could "
          "not be read", said in [p.msg for p in none_at.freq_problems(c10)]
          and none_at.power_limit(c10)["note"] == said,
          str(([p.msg for p in none_at.freq_problems(c10)], none_at.power_limit(c10))))
    odd = [p.msg for power in (" 20", "1_0", "٢٠")
           for p in T.check_config(cfg(radio__1__txpower=power), limits())]
    check("a power that int() takes but is not a whole number in dBm (\" 20\", \"1_0\", "
          "\"٢٠\"): an error", odd == [tr("The power must be a whole number in dBm.")] * 3,
          str(odd))


def app_flows(version, port):
    print(f"=== App + simulator, airOS {version} ===")
    srv = S.make_server(version, port, restart=0.6, test_secs=30)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = srv.dev
    hw = 25 if version == 8 else 27
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    check(f"v{version}: limits loaded", app.limits and app.limits.tx_range()[1] == hw,
          str(app.limits and app.limits.tx_range()))

    ui.script = [("menu", "4"), ("input", str(hw + 1)), ("menu", None)]
    app.quick_settings()
    hint = [t for k, ti, t in ui.log if k == "input"][-1]
    check(f"v{version}: power hint", tr("This device supports {min} to {max} dBm.",
                                        min=app.limits.tx_range()[0], max=hw) in hint
          and tr("Legal limit (calculated as the web does): {legal}.").partition("{")[0]
          in hint, hint)
    check(f"v{version}: {hw + 1} dBm rejected", tr("Invalid value") in ui.titles()
          and app.working.get("radio.1.txpower") == "20")

    ui.log.clear()
    ui.script = [("menu", "4"), ("input", "22"), ("menu", None)]
    app.quick_settings()
    check(f"v{version}: 22 dBm, above the legal limit (20): rejected, as in the web",
          tr("Invalid value") in ui.titles() and app.working.get("radio.1.txpower") == "20")
    ui.script = [("menu", "4"), ("input", "19"), ("menu", None)]
    app.quick_settings()
    check(f"v{version}: 19 dBm accepted", app.working.get("radio.1.txpower") == "19")

    ui.log.clear()
    d.effective_power = lambda: 18           # (the radio transmits less than asked for)
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    del d.effective_power
    decide = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test mode")]
    check(f"v{version}: the real power in the decision", decide
          and tr("⚠ You asked for {asked} dBm and the radio reports {real} dBm.", asked=19,
                 real=18) in decide[0]
          and tr("TX power: {power}", power="18 dBm") in decide[0],
          decide[0] if decide else str(ui.titles("yesno")))
    check(f"v{version}: 19 saved", d.saved["radio.1.txpower"] == "19")

    ui.log.clear()
    ui.script = [("input", "netmask"), ("menu", "netconf.3.netmask"), ("menu", "1"),
                 ("input", "255.0.255.0"), ("menu", None)]
    app.edit_keys()
    check(f"v{version}: invalid netmask rejected", tr("Invalid value") in ui.titles()
          and app.working.get("netconf.3.netmask") == "255.255.255.0")
    ui.script = [("input", "sshd.port"), ("menu", "sshd.port"), ("menu", "1"), ("input", "80"),
                 ("menu", None)]
    app.edit_keys()
    check(f"v{version}: repeated port rejected", app.working.get("sshd.port") == "22"
          and any(tr("{key} and {other} use the same port ({port}): one of the two services "
                     "would not start.", key="sshd.port", other="httpd.port", port=80) in t
                  for k, ti, t in ui.log if k == "msgbox"))
    ui.log.clear()
    ui.script = [("input", "radio.1.freq"), ("menu", "radio.1.freq"), ("menu", "1"),
                 ("input", "5150"), ("yesno", 0), ("menu", None)]
    app.edit_keys()
    freq_hint = [t for k, ti, t in ui.log if k == "input" and ti == "radio.1.freq"]
    check(f"v{version}: frequency hint", freq_hint and "5745" in freq_hint[0], str(freq_hint))
    check(f"v{version}: a frequency off the list, accepted after confirming",
          app.working.get("radio.1.freq") == "5150")
    ui.log.clear()
    ui.script = [("yesno", 1)]
    app.apply(test=True)
    confirm = [t for k, ti, t in ui.log if k == "yesno" and ti == tr("Test changes")]
    check(f"v{version}: the review before applying warns again",
          confirm and "5150 MHz" in confirm[0])
    srv.shutdown()


if __name__ == "__main__":
    unit()
    app_flows(8, 18588)
    app_flows(6, 18586)
    print("FAILURES:", fails or "none")
    sys.exit(1 if fails else 0)
