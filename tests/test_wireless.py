"""Tests of "Wireless": reading and saving as each web does (airOS 8: models with cfgMap;
airOS 6: link.cgi, advanced.cgi, ubnt.cgi and macacl.cgi), the exact keys of each change,
the validation and the menus against the simulator. All on 127.0.0.1."""
import math
import os
import shutil
import sys
import threading

from _common import check, fails, FakeUI, HERE, settle, suite  # noqa: E402
T, S, XDG = suite("wireless")
tr = T.tr


def sim_cfg(version, station=False, **kw):
    c = T.Config(S.cfg_text(S.Device(version, "ubnt", "ubnt", 1, 60, station=station).running))
    for k, v in kw.items():
        if v is None:
            c.delete(k.replace("__", "."))
        else:
            c.set(k.replace("__", "."), v)
    return c


B8, B6 = dict(S.BOARD[8]), dict(S.BOARD[6])


def plan(c, flavor, **changes):
    return T.plan_wireless(c, flavor, B8 if flavor == "v8" else B6, changes)


def same(got, want):
    return got == want, f"extra {sorted(set(got.items()) - set(want.items()))} " \
                        f"missing {sorted(set(want.items()) - set(got.items()))}"


def unit_v8():
    print("=== airOS 8: reading as the web's models do ===")
    ap = sim_cfg(8)
    st = T.wireless_state(ap, "v8", B8)
    check("PtMP AP with WPA2-PSK", st["ap"] and not st["ptp"] and st["security"] == "wpa2"
          and st["wpa_auth"] == "WPA-PSK" and st["psk"] == "test-key-123", str(st)[:300])
    check("frequency, width, country and distance", (st["chanbw"], st["center"], st["freq"],
                                                     st["country"], st["distance"])
          == (20, 5745, 5745, 484, 1500))
    check("flexible frame (polling_ff_flex=1 reads as duration 1)",
          st["tdd_flex"] == 1 and st["tdd_duration"] == 1)
    check("advanced", (st["ampdu"], st["amsdu"], st["mcast_enh"], st["sta_priority"],
                       st["rate"], st["atpc_target"]) == (32, 3, 1, 2, "-1", 36))
    empty = T.wireless_state(T.Config("radio.1.mode=Managed\n"), "v8", B8)
    check("missing keys: the models' default values",
          empty["wds"] and empty["ptp"] and empty["loss"] == 2 and empty["obey"]
          and empty["ssid"] == "www.uisp.com" and empty["acl_policy"] == "allow"
          and empty["atpc_sta"] and empty["sta_priority"] == 2 and empty["rate"] == "-1"
          and empty["center"] == -1 and empty["country"] == 484, str(empty)[:300])
    check("empty pollingpri -> 2 (getValue || default)",
          T.wireless_state(sim_cfg(8, radio__1__pollingpri=""), "v8", B8)["sta_priority"] == 2)
    check("rate.mcs 0 reads as 2",
          T.wireless_state(sim_cfg(8, wireless__1__rate__mcs="0"), "v8", B8)["rate"] == "2")
    check("capitalized Master is AP", T.wireless_state(
        sim_cfg(8, radio__1__mode="Master"), "v8", B8)["ap"])
    sta = sim_cfg(8, station=True)
    s2 = T.wireless_state(sta, "v8", B8)
    check("station with WPA2-PSK (wpasupplicant)", not s2["ap"] and s2["security"] == "wpa2"
          and s2["psk"] == "test-key-123")
    check("station without a key (key_mgmt NONE) -> no security", T.wireless_state(
        sim_cfg(8, station=True, wpasupplicant__profile__1__network__1__key_mgmt__1__name="NONE"),
        "v8", B8)["security"] == "none")

    print("=== airOS 8: saving with no changes does not touch what the web saved ===")
    for name, c in (("AP", ap), ("station", sta)):
        saved = T.wireless_save(c, "v8", B8, T.wireless_state(c, "v8", B8))
        changed = [(k, a, b) for k, a, b in c.diff(saved) if a is not None]
        check(f"{name}: no existing key changes", not changed, str(changed))

    print("=== airOS 8: the keys of each change ===")
    check("SSID: the three copies", *same(plan(ap, "v8", ssid="South-Link"), {
        "wireless.1.ssid": "South-Link", "wpasupplicant.profile.1.network.1.ssid": "South-Link",
        "aaa.1.ssid": "South-Link"}))
    check("station SSID: no aaa.1.ssid", *same(plan(sta, "v8", ssid="X"), {
        "wireless.1.ssid": "X", "wpasupplicant.profile.1.network.1.ssid": "X"}))
    check("SSID with &amp; is saved unescaped (_uesc)",
          plan(ap, "v8", ssid="A&amp;B")["wireless.1.ssid"] == "A&B")
    check("power", plan(ap, "v8", power=18) == {"radio.1.txpower": "18"})
    check("WPA key: on both sides", *same(plan(ap, "v8", psk="other-key-1"), {
        "aaa.1.wpa.psk": "other-key-1",
        "wpasupplicant.profile.1.network.1.psk": "other-key-1"}))
    check("AP -> station", *same(plan(ap, "v8", ap=False), {
        "radio.1.mode": "managed", "aaa.status": "disabled", "aaa.1.status": "disabled",
        "wpasupplicant.status": "enabled", "wpasupplicant.device.1.status": "enabled",
        "wpasupplicant.profile.1.network.1.eap.1.status": "disabled",
        "wireless.1.mcast.enhance": "0", "radio.1.polling_ff_flex": "0"}))
    # the multicast enhancement stays as the model read it (0 on the station)
    check("station -> PtMP AP", *same(plan(sta, "v8", ap=True, tdd_duration=1, tdd_flex=1), {
        "radio.1.mode": "master", "aaa.status": "enabled", "aaa.1.status": "enabled",
        "wpasupplicant.status": "disabled", "wpasupplicant.device.1.status": "disabled",
        "radio.1.polling_ff_flex": "1"}))
    check("PtMP -> PtP (AP)", *same(plan(ap, "v8", ptp=True), {
        "radio.1.ptpmode": "1", "radio.1.polling_11ac_11n_compat": "0",
        "radio.1.polling_ff_flex": "0", "wireless.1.mcast.enhance": "0"}))
    check("PtMP at 30 MHz: no 11n compatibility", plan(ap, "v8", chanbw=30)[
        "radio.1.polling_11ac_11n_compat"] == "0")
    check("no security (AP)", *same(plan(ap, "v8", security="none"), {
        "aaa.status": "disabled", "aaa.1.status": "disabled"}))
    eap = plan(ap, "v8", wpa_auth="WPA-EAP", radius_ip="10.0.0.5", radius_secret="s3cr3t")
    check("WPA2 enterprise (AP): RADIUS server", *same(eap, {
        "aaa.1.wpa.key.1.mgmt": "WPA-EAP", "aaa.1.radius.auth.1.status": "enabled",
        "aaa.1.radius.auth.1.ip": "10.0.0.5", "aaa.1.radius.auth.1.secret": "s3cr3t"}))
    acct = plan(ap, "v8", wpa_auth="WPA-EAP", radius_ip="10.0.0.5", radius_secret="s",
                acct_on=True, acct_ip="10.0.0.6", acct_secret="t")
    check("and accounting", acct.get("aaa.1.radius.acct.1.status") == "enabled"
          and acct.get("aaa.1.radius.acct.1.ip") == "10.0.0.6"
          and acct.get("aaa.1.radius.acct.1.secret") == "t", str(acct))
    to_psk = plan(T._merge(ap.copy(), acct), "v8", wpa_auth="WPA-PSK", psk="Sup3r-Secret-Key")
    check("  and from it to a shared key: accounting off too (as airOS 6 does; its menu no "
          "longer shows it, to turn it off)",
          to_psk.get("aaa.1.radius.acct.1.status") == "disabled", str(to_psk))
    check("WPA2 enterprise (station)", *same(plan(sta, "v8", wpa_auth="WPA-EAP", eap_user="ana",
                                                  eap_pass="pw", eap_anon="anon"), {
        "wpasupplicant.profile.1.network.1.key_mgmt.1.name": "WPA-EAP",
        "wpasupplicant.profile.1.network.1.eap.1.status": "enabled",
        "wpasupplicant.profile.1.network.1.eap.1.name": "TTLS PEAP",
        "wpasupplicant.profile.1.network.1.phase2": "auth=MSCHAPV2",
        "wpasupplicant.profile.1.network.1.identity": "ana",
        "wpasupplicant.profile.1.network.1.password": "pw",
        "wpasupplicant.profile.1.network.1.anonymous_identity": "anon"}))
    check("lock to the AP MAC (station)", plan(sta, "v8", ap_mac="00:11:22:33:44:55") == {
        "wpasupplicant.profile.1.network.1.bssid": "00:11:22:33:44:55"})
    check("hide SSID (the firmware's key)", plan(ap, "v8", hide_ssid=True) == {
        "wireless.1.hide_ssid": "enabled"})
    check("country", plan(ap, "v8", country=840) == {"radio.countrycode": "840",
                                                     "radio.1.countrycode": "840"})
    check("fixed distance: acktimeout = 31 + d/150", *same(plan(ap, "v8", distance=3000), {
        "radio.1.ackdistance": "3000", "radio.1.acktimeout": "51"}))
    check("JavaScript rounding (half up)",
          plan(ap, "v8", distance=75)["radio.1.acktimeout"] == "32")
    check("automatic distance", plan(ap, "v8", ack_auto=True) == {"radio.1.ack.auto": "enabled"})
    check("station with automatic width: scanbw and the list disabled",
          *same(plan(sta, "v8", auto_chanbw=True, scan_on=True, scan=[5745]), {
              "radio.1.scanbw.status": "enabled", "radio.1.scan_list.channels": "5745"}))
    check("frequency list (radio.1.scan_list)", *same(
        plan(sta, "v8", scan_on=True, scan=[5745, 5765]), {
            "radio.1.scan_list.status": "enabled", "radio.1.scan_list.channels": "5745,5765"}))
    check("automatic 20/40/80 width on a station: ieee_mode auto",
          plan(sta, "v8", chanbw=0)["radio.1.ieee_mode"] == "auto")
    check("80 MHz (PtP AP)", plan(ap, "v8", ptp=True, chanbw=80, center=5775)[
        "radio.1.ieee_mode"] == "11acvht80")
    check("automatic frequency: freq 0 and center 0", *same(plan(ap, "v8", center=0), {
        "radio.1.center.1.freq": "0", "radio.1.freq": "0"}))
    check("fixed 5 ms frame 67/33", *same(plan(ap, "v8", tdd_duration=5000, tdd_flex=0,
                                               tdd_ratio=67), {
        "radio.1.polling_ff_flex": "0", "radio.1.polling_ff_dur": "5000",
        "radio.1.polling_ff_dl_ratio": "67"}))
    check("custom antenna and cable", *same(plan(ap, "v8", antenna=0, gain=24, loss=2), {
        "radio.1.antenna.id": "0", "radio.1.antenna.gain": "24", "radio.1.cable.loss": "2"}))
    check("PtMP AP advanced settings", *same(plan(ap, "v8", isolation=True, mcast_enh=0, ampdu=16,
                                                  amsdu=1, rate="7", rate_module=1), {
        "wireless.1.l2_isolation": "enabled", "wireless.1.mcast.enhance": "0",
        "wireless.1.ampdu.frames": "16", "wireless.1.amsdu": "1", "wireless.1.rate.mcs": "7",
        "radio.1.rc_mode": "1"}))
    check("sensitivity threshold", plan(ap, "v8", rx_sens_on=True, rx_sens=-70) == {
        "radio.1.rx_sensitivity": "-70"})
    check("the AP's ATPC", *same(plan(ap, "v8", atpc=True, atpc_target=40), {
        "radio.1.atpc.status": "enabled", "radio.1.atpc.threshold": "40"}))
    check("ATPC on a station: the AP's one always disabled",
          plan(sta, "v8", atpc=True) == {})
    check("TDMA filter only with the legacy flexible frame",
          plan(ap, "v8", cmsbias=4) == {} and plan(ap, "v8", cmsbias=4, tdd_duration=0,
                                                   tdd_flex=0)["radio.1.cmsbias"] == "4")
    check("station airMAX priority", plan(sta, "v8", sta_priority=0) == {
        "radio.1.pollingpri": "0"})
    check("channel change on DFS: only a PtMP AP in an ETSI country",
          plan(ap, "v8", radar_change=True) == {}
          and plan(ap, "v8", radar_change=True, country=724).get("radio.1.radarmode") == "1")
    check("deauthentication protection (AP)", plan(ap, "v8", deauth=True) == {
        "radio.1.polling_daprot": "1"})
    off = sim_cfg(8, radio__1__polling_daprot="1")
    check("and removing it deletes the key", plan(off, "v8", deauth=False) == {
        "radio.1.polling_daprot": None})

    print("=== airOS 8: MAC ACL (the dialog renumbers from 1) ===")
    rules = [{"mac": "00:11:22:33:44:55", "comment": "home", "on": True},
             {"mac": "66:77:88:99:AA:BB", "comment": "", "on": False}]
    up = plan(ap, "v8", acl_rules=rules)
    check("two rules (an empty comment is not saved)", *same(up, {
        "wireless.1.mac_acl.1.status": "enabled", "wireless.1.mac_acl.1.mac": "00:11:22:33:44:55",
        "wireless.1.mac_acl.1.comment": "home", "wireless.1.mac_acl.2.status": "disabled",
        "wireless.1.mac_acl.2.mac": "66:77:88:99:AA:BB"}))
    with_rules = T._merge(ap.copy(), up)
    left = plan(with_rules, "v8", acl_rules=rules[1:])
    check("deleting the first: the second becomes 1", *same(left, {
        "wireless.1.mac_acl.1.status": "disabled", "wireless.1.mac_acl.1.mac": "66:77:88:99:AA:BB",
        "wireless.1.mac_acl.1.comment": None, "wireless.1.mac_acl.2.status": None,
        "wireless.1.mac_acl.2.mac": None}))
    check("enabling the ACL with a deny policy", *same(
        plan(with_rules, "v8", acl_on=True, acl_policy="deny"), {
            "wireless.1.mac_acl.status": "enabled", "wireless.1.mac_acl.policy": "deny"}))
    check("on a station the ACL always stays disabled", plan(sta, "v8", acl_on=True) == {})

    print("=== airOS 8: backup SSID (station) ===")
    p2 = "wpasupplicant.profile.1.network.2."
    check("open", *same(plan(sta, "v8", bk_on=True, bk_ssid="Backup"), {
        p2 + "status": "enabled", p2 + "ssid": "Backup"}))
    check("with WPA2-PSK", *same(plan(sta, "v8", bk_on=True, bk_ssid="R", bk_security="wpa2",
                                      bk_wpa_auth="WPA-PSK", bk_psk="backup-key"), {
        p2 + "status": "enabled", p2 + "ssid": "R", p2 + "proto.1.name": "RSN",
        p2 + "pairwise.1.name": "CCMP", p2 + "key_mgmt.1.name": "WPA-PSK",
        p2 + "psk": "backup-key", p2 + "eap.1.status": "disabled"}))

    print("=== airOS 8: bridge (ebtables, like the web's network helper) ===")
    nowds = sim_cfg(8, station=True, wireless__1__wds__status="disabled")
    br = plan(sim_cfg(8, wireless__1__wds__status="disabled"), "v8", ap=False)
    check("station without WDS: ARP-NAT and EAPOL on ath0",
          br.get("ebtables.sys.arpnat.1.devname") == "ath0"
          and br.get("ebtables.sys.arpnat.status") == "enabled", str(br))
    back = plan(T._merge(sim_cfg(8, wireless__1__wds__status="disabled"), br), "v8", wds=True)
    check("enabling WDS removes the ARP-NAT it got as a station without WDS",
          back.get("ebtables.sys.arpnat.1.devname", "x") is None, str(back))
    with_nat = T._merge(nowds.copy(),
                        {"ebtables.status": "enabled", "ebtables.sys.status": "enabled",
                                       "ebtables.sys.arpnat.status": "enabled",
                                       "ebtables.sys.arpnat.1.status": "enabled",
                                       "ebtables.sys.arpnat.1.devname": "ath0"})
    gone = plan(with_nat, "v8", wds=True)
    check("enabling WDS deletes the ARP-NAT entries",
          gone.get("wireless.1.wds.status") == "enabled"
          and gone.get("ebtables.sys.arpnat.1.devname", "x") is None
          and gone.get("ebtables.sys.arpnat.status", "x") is None, str(gone))

    print("=== airOS 8: board, modes and widths ===")
    check("the board's modes", T.v8_modes(B8) == ["ap-ptp", "ap-ptmp", "sta-ptp", "sta-ptmp"]
          and T.v8_modes({"radio.1.ptp_only": "1", "radio.1.ptp_sta": "1"}) == ["ap-ptp", "sta-ptp"]
          and len(T.v8_modes({})) == 4)
    check("bands", T.v8_bands(B8) == (True, False) and T.v8_bands({"radio.1.ieee_modes": "15"})
          == (True, True))
    check("PtMP AP widths (up to 40)", T.v8_widths(st, B8, None) == [40, 30, 20, 10])
    check("PtP AP widths", T.v8_widths(dict(st, ptp=True), B8, {20, 40, 80})
          == [80, 40, 20])
    check("station widths: Auto, 20/40/80 and the rest",
          T.v8_station_widths(dict(st, ap=False, ptp=True), B8, None)
          == [-1, 0, 60, 50, 30, 10])
    check("ieee_mode", [T.v8_ieee_mode(dict(st, chanbw=w), B8) for w in (0, 10, 20, 30, 40, 50, 80)]
          == ["auto", "11acvht20", "11acvht20", "11acvht20", "11acvht40", "11acvht40",
              "11acvht80"])
    check("2.4 GHz", T.v8_ieee_mode(dict(st, chanbw=20), {"radio.1.ieee_modes": "10"})
          == "11nght20")
    check("fixed frame splits", T.v8_tdd_ratios(10000, 20) == [50, 67, 75]
          and T.v8_tdd_ratios(5000, 20) == [50, 67] and T.v8_tdd_ratios(8000, 10) == [67]
          and T.v8_tdd_ratios(1, 20) == [])
    check("Gen3: not handled", not T.wireless_supported("v8", {"feature.g3": "1"})
          and T.wireless_supported("v8", B8) and T.wireless_supported("v6", {"feature.g3": "1"}))
    ants = {"radio.1.antenna.1.id": "3", "radio.1.antenna.1.gain": "16",
            "radio.1.antenna.1.builtin": "0", "radio.1.antenna.2.id": "4",
            "radio.1.antenna.2.gain": "23", "radio.1.antenna.2.builtin": "0",
            "radio.1.def_antenna": "3", "radio.1.antennas": "2"}
    check('antennas: plus "Custom…" if there is an external one',
          [a["id"] for a in T.v8_antenna_choices(ants, "11acvht")] == [3, 4, 0])
    check("the antenna that matches the configured gain", T.wireless_state(
        T.Config("radio.1.antenna.id=3\nradio.1.antenna.gain=20\n"), "v8", ants)["antenna"] == 4)

    print("=== airOS 8: validation (the web's) ===")
    err = T.wireless_errors(dict(st, ssid=""), B8)
    check("empty SSID", err and "SSID" in err[0], str(err))
    check("64-hex-digit key: the web does not accept it",
          T.wireless_errors(dict(st, psk="a" * 64), B8) != [])
    check("8-character key: accepted", T.wireless_errors(dict(st, psk="12345678"), B8) == [])
    bad = T.wireless_errors(dict(st, wpa_auth="WPA-EAP", radius_ip="x", radius_secret=""), B8)
    check("RADIUS: IP and secret", len(bad) == 2, str(bad))
    check("distance above 22.5 km at 20 MHz", T.wireless_errors(
        dict(st, distance=23000), B8) != [] and T.wireless_errors(dict(st, distance=22500),
                                                                   B8) == [])
    check("cable loss greater than the gain", T.wireless_errors(
        dict(st, gain=10, loss=12), B8) != [])
    check("repeated ACL MAC", T.wireless_errors(dict(st, acl_rules=[
        {"mac": "00:11:22:33:44:55"}, {"mac": "00:11:22:33:44:55"}]), B8) != [])
    check("malformed AP MAC (station)", T.wireless_errors(
        dict(s2, ap_mac="00:11:22"), B8) != [])


def unit_v6():
    print("=== airOS 6: reading as link.cgi does ===")
    ap = sim_cfg(6)
    st = T.wireless_state(ap, "v6", B6)
    check("AP with WPA2-AES", st["wmode6"] == "ap" and st["security"] == "wpa2aes"
          and st["wpa_auth"] == "WPA-PSK" and st["psk"] == "test-key-123", str(st)[:300])
    check("base mode, width and distance", (st["ieee"], st["chanbw"], st["ext"], st["distance"])
          == ("11naht", 20, "", 1500))
    check("40 MHz with lower extension", (lambda s: (s["ieee"], s["chanbw"], s["ext"]))(
        T.wireless_state(sim_cfg(6, radio__1__ieee_mode="11naht40minus", radio__1__chanbw="40"),
                         "v6", B6)) == ("11naht", 40, "minus"))
    check("security: WPA-AES, WPA2-TKIP and WEP", [T.wireless_state(sim_cfg(6, **kw), "v6", B6)[
        "security"] for kw in ({"aaa__1__wpa__mode": "1"},
                               {"aaa__1__wpa__1__pairwise": "TKIP"},
                               {"wireless__1__security__type": "wep128"})]
          == ["wpaaes", "wpa2tkip", "wep"])
    check("AP-Repeater: WDS with peers", T.wireless_state(sim_cfg(
        6, wireless__1__wds__status="enabled", wireless__1__wds__1__peer="00:11:22:33:44:55",
        aaa__status="disabled"), "v6", B6)["wmode6"] == "aprepeater")
    check("with WPA the page shows it as AP", T.wireless_state(sim_cfg(
        6, wireless__1__wds__status="enabled", wireless__1__wds__1__peer="00:11:22:33:44:55"),
        "v6", B6)["wmode6"] == "ap")
    check("licensed country (2124)", T.wireless_state(sim_cfg(
        6, radio__countrycode="124", radio__countrylicense="124"), "v6", B6)["country"] == 2124)
    check("sensitivity threshold: 96 + threshold", T.wireless_state(sim_cfg(
        6, wireless__1__sens="16"), "v6", B6)["rx_sens"] == -80)
    sta = sim_cfg(6, station=True)
    s2 = T.wireless_state(sta, "v6", B6)
    check("station with WPA2-AES", s2["wmode6"] == "sta" and s2["security"] == "wpa2aes")

    print("=== airOS 6: saving with no changes does not touch what the web saved ===")
    for name, c in (("AP", ap), ("station", sta)):
        for page in T.V6_PAGE_ORDER:
            saved = T.wireless_save(c, "v6", B6, T.wireless_state(c, "v6", B6), page)
            changed = [(k, a, b) for k, a, b in c.diff(saved) if a is not None]
            check(f"{name}, {page}: no existing key changes", not changed, str(changed))

    print("=== airOS 6: the keys of each change ===")
    check("SSID: wireless and aaa (no wpasupplicant on the AP)", *same(
        plan(ap, "v6", ssid="South-Link"), {"wireless.1.ssid": "South-Link",
                                            "aaa.1.ssid": "South-Link"}))
    check("station SSID: also the wpa_supplicant profile", *same(
        plan(sta, "v6", ssid="X"), {"wireless.1.ssid": "X",
                                    "wpasupplicant.profile.1.network.1.ssid": "X"}))
    check("power", plan(ap, "v6", power=18) == {"radio.1.txpower": "18"})
    check("power below the minimum: the device's minimum", plan(ap, "v6", power=-3) == {
        "radio.1.txpower": "1"})
    w = "wpasupplicant."
    check("AP -> station", *same(plan(ap, "v6", wmode6="sta", ap=False), {
        "radio.1.mode": "managed", "ebtables.sys.arpnat.status": "enabled",
        "aaa.status": "disabled", "aaa.1.status": "disabled",
        w + "status": "enabled", w + "device.1.status": "enabled",
        w + "device.1.driver": "madwifi", w + "device.1.profile": "WPA-PSK",
        w + "profile.1.name": "WPA-PSK", w + "profile.1.network.1.key_mgmt.1.name": "WPA-PSK",
        w + "profile.1.network.1.proto.1.name": "RSN",
        w + "profile.1.network.1.pairwise.1.name": "CCMP",
        w + "profile.1.network.1.bssid": "", w + "profile.1.network.1.eap.1.status": "disabled"}))
    check("station -> AP (automatic frequency, as the page does)", *same(
        plan(sta, "v6", wmode6="ap", ap=True, freq=0), {
            "radio.1.mode": "master", "radio.1.freq": "0", "ebtables.sys.arpnat.status": "disabled",
            "aaa.status": "enabled", "aaa.1.status": "enabled", w + "status": "disabled",
            w + "device.1.status": "disabled",
            **{f"wireless.1.wds.{i}.peer": "" for i in range(1, 7)}}))
    check("WDS on a station: no ARP-NAT", *same(plan(sta, "v6", wds=True), {
        "wireless.1.wds.status": "enabled", "ebtables.sys.arpnat.status": "disabled"}))
    check("no security (AP)", *same(plan(ap, "v6", security="none"), {
        "aaa.status": "disabled", "aaa.1.status": "disabled",
        "ebtables.sys.eap.status": "disabled"}))
    check("WPA2 enterprise (AP)", *same(plan(ap, "v6", wpa_auth="WPA-EAP", radius_ip="10.0.0.5",
                                             radius_secret="s3cr3t"), {
        "aaa.1.wpa.key.1.mgmt": "WPA-EAP", "aaa.1.radius.auth.1.status": "enabled",
        "aaa.1.radius.auth.1.ip": "10.0.0.5", "aaa.1.radius.auth.1.port": "1812",
        "aaa.1.radius.auth.1.secret": "s3cr3t"}))
    eap = plan(sta, "v6", wpa_auth="WPA-EAP", eap_method="PEAP", eap_user="ana", eap_pass="pw",
               eap_anon="anon")
    p1 = "wpasupplicant.profile.1.network.1."
    check("WPA2 enterprise (station)", *same(eap, {
        w + "device.1.profile": "WPA-EAP", w + "profile.1.name": "WPA-EAP",
        p1 + "key_mgmt.1.name": "WPA-EAP", p1 + "eap.1.status": "enabled",
        p1 + "eap.1.name": "PEAP", p1 + "identity": "ana", p1 + "password": "pw",
        p1 + "anonymous_identity": "anon", p1 + "phase2": "auth=MSCHAPV2"}))
    check("from WEP to WPA2-AES", plan(sim_cfg(6, wireless__1__security__type="wep64",
                                               aaa__status="disabled", aaa__1__status="disabled"),
                                       "v6", security="wpa2aes").get("aaa.status") == "enabled")
    check("hide SSID", plan(ap, "v6", hide_ssid=True) == {"wireless.1.hide_ssid": "enabled"})
    check("lock to the AP MAC (station)", *same(plan(sta, "v6", ap_mac="00:11:22:33:44:55"), {
        "wireless.1.ap": "00:11:22:33:44:55",
        "wpasupplicant.profile.1.network.1.bssid": "00:11:22:33:44:55"}))
    check("40 MHz with upper extension (cwm.mode 2 on an AP)", *same(
        plan(ap, "v6", chanbw=40, ext="plus"), {
            "radio.1.chanbw": "40", "radio.1.ieee_mode": "11naht40plus", "radio.1.cwm.mode": "2"}))
    check("40 MHz on a station: no extension and cwm.mode 1", *same(plan(sta, "v6", chanbw=40), {
        "radio.1.chanbw": "40", "radio.1.ieee_mode": "11naht40", "radio.1.cwm.mode": "1"}))
    check("country (link.cgi)", *same(plan(ap, "v6", country=840), {
        "radio.countrycode": "840", "radio.1.countrycode": "840"}))
    check("licensed country: radio.countrylicense", *same(plan(ap, "v6", country=2124), {
        "radio.countrycode": "124", "radio.1.countrycode": "124",
        "radio.countrylicense": "124"}))
    check("calculate the EIRP limit (obey and reg_obey)", *same(plan(ap, "v6", obey=False), {
        "radio.1.obey": "disabled", "radio.1.reg_obey": "disabled"}))
    check("frequency list (wireless.1.scan_list)", *same(
        plan(sta, "v6", scan_on=True, scan=[5745, 5765]), {
            "wireless.1.scan_list.status": "enabled",
            "wireless.1.scan_list.channels": "5745,5765"}))

    print("=== airOS 6: advanced (advanced.cgi) and airMAX (ubnt.cgi) ===")
    check("distance: multiples of 150 m, acktimeout = 21 + d/150", *same(
        plan(ap, "v6", distance=3000), {"radio.1.ackdistance": "3000",
                                        "radio.1.acktimeout": "41"}))
    check("automatic ACK", plan(ap, "v6", ack_auto=True) == {"radio.1.ack.auto": "enabled"})
    check("with airMAX without ACK the web does not save the distance", plan(sim_cfg(
        6, radio__1__pollingnoack="1"), "v6", distance=3000) == {})
    check("aggregation", *same(plan(ap, "v6", aggr_frames=16, aggr_bytes=20000), {
        "radio.1.ampdu.frames": "16", "radio.1.ampdu.bytes": "20000"}))
    check("aggregation disabled: only the status", plan(ap, "v6", aggr_on=False) == {
        "radio.1.ampdu.status": "disabled"})
    check("AP multicast enhancement: 2 or 0", plan(ap, "v6", mcast_enh=False) == {
        "wireless.1.mcast.enhance": "0"})
    check("not saved on a station", plan(sta, "v6", mcast_enh=False) == {})
    check("isolation and multicast data", *same(plan(ap, "v6", isolation=True,
                                                     mcast_data=False), {
        "wireless.1.l2_isolation": "enabled", "netconf.2.allmulti": "disabled"}))
    check("sensitivity threshold", plan(ap, "v6", rx_sens=-80) == {"wireless.1.sens": "16"})
    check("RTS", plan(ap, "v6", rts=500) == {"radio.1.rts": "500"})
    check("the AP's airMAX", plan(ap, "v6", polling=False) == {"radio.1.polling": "disabled"})
    check("station airMAX priority", plan(sta, "v6", sta_priority="1") == {
        "radio.1.pollingpri": "1"})

    print("=== airOS 6: MAC ACL (macacl.cgi) ===")
    rules = [{"mac": "00:11:22:33:44:55", "comment": "", "on": True}]
    up = plan(ap, "v6", acl_rules=rules)
    check("empty comment: written empty", *same(up, {
        "wireless.1.mac_acl.1.status": "enabled", "wireless.1.mac_acl.1.mac": "00:11:22:33:44:55",
        "wireless.1.mac_acl.1.comment": ""}))
    check("status and policy go through link.cgi", *same(plan(ap, "v6", acl_on=True,
                                                              acl_policy="deny"), {
        "wireless.1.mac_acl.status": "enabled", "wireless.1.mac_acl.policy": "deny"}))
    messy = sim_cfg(6, wireless__1__mac_acl__1__mac="bad",
                    wireless__1__mac_acl__2__mac="AA:BB:CC:DD:EE:FF",
                    wireless__1__mac_acl__2__status="enabled")
    check("a malformed rule disappears when the page is saved", [
        r["mac"] for r in T.wireless_state(messy, "v6", B6)["acl_rules"]] == ["AA:BB:CC:DD:EE:FF"])

    print("=== airOS 6: validation ===")
    check("short key", T.wireless_errors(dict(st, psk="1234567"), B6) != [])
    plain, fast = dict(B6, **{"radio.1.caps": "0"}), dict(B6, **{"radio.1.caps": "8192"})

    def far(s, board):
        """v6_max_distance() if it is the longest distance it accepts (150 m steps), else None."""
        top = T.v6_max_distance(s, board)
        return top if (T.wireless_errors(dict(s, distance=top), board) == []
                       and T.wireless_errors(dict(s, distance=top + 150), board) != []) else None
    check("distance: up to (maximum ACK − ACK at 0 m) × 150 m, like the slider of "
          "advanced.cgi (5 GHz and 20 MHz: 409 − 23 µs)", far(st, plain) == 57900,
          str(far(st, plain)))
    check('  with "fast clock" (the 2.4 GHz clock: 372 µs)', far(st, fast) == 52350)
    check('  not knowing the capabilities of the radio: as with "fast clock", the shortest',
          far(st, B6) == 52350)
    widths = [far(dict(st, chanbw=w), plain) for w in (40, 10, 5)]
    check("  40 MHz: 204 − 23 µs; 10 MHz: 819 − 45; 5 MHz: 1638 − 85", widths
          == [27150, 116100, 232950], str(widths))
    check("  with the board's distance limit (30 km): 2·30 000/300 + 0.5 µs, in "
          "multiples of 150 m", far(st, dict(plain, **{"radio.1.distance_limit_km": "30"}))
          == 30150)
    check("  fixed country without HT40: the mode with no width, 204 µs in get_timings()", far(
        st, dict(plain, **{"radio.1.caps": "1024", "radio.1.ccode_fixed": "1"})) == 27150)
    check("  2.4 GHz and 802.11b, as in get_timings()", [T.v6_ack_timings(*a) for a in (
        ("11nght20", 20, False), ("11nght40minus", 40, False), ("11nght20", 10, True),
        ("b", 20, False))] == [(23, 372), (23, 186), (45, 744), (34, 744)])
    check("empty EAP user", T.wireless_errors(dict(s2, wpa_auth="WPA-EAP", eap_user="",
                                                   eap_pass="x"), B6) != [])


def unit_limits():
    print("=== limits: the frequency list of each version ===")
    rd8 = lambda cc, bw: T.parse_regdomain_v8(S.regdomain_v8(cc))   # noqa: E731
    rd6 = lambda cc, bw: T.parse_regdomain_v6(S.regdomain_v6(bw, cc))   # noqa: E731
    L8, L6 = T.DeviceLimits("v8", B8, rd8), T.DeviceLimits("v6", B6, rd6)
    sta8 = sim_cfg(8, station=True, radio__1__scan_list__status="enabled",
                   radio__1__scan_list__channels="5180,5200")
    sta6 = sim_cfg(6, station=True, wireless__1__scan_list__status="enabled",
                   wireless__1__scan_list__channels="5180, 5200")
    check("airOS 8 reads radio.1.scan_list", L8.context(sta8)["scan"] == [5180, 5200])
    check("airOS 6 reads wireless.1.scan_list (with spaces)",
          L6.context(sta6)["scan"] == [5180, 5200])
    check("and the station's power comes from its list", L8.power_limit(sta8)["max"] == 14
          and L6.power_limit(sta6)["max"] == 14)
    ch = T.parse_regdomain_v6(S.regdomain_v6(40))
    check("airOS 6: extension channel flags", {c.control: c.ext & 6 for c in ch}
          == {5180: 2, 5200: 4, 5220: 2, 5240: 4, 5745: 2, 5765: 4, 5785: 2, 5805: 4})
    check("the simulator's countries", [len(S.regdomain_v8(cc)) > 0 for cc in (484, 840, 724, 999)]
          == [True, True, True, False])


def start(version, port, station=False, **extra):
    srv = S.make_server(version, port, restart=0.3, test_secs=60, station=station)
    for k, v in extra.items():
        for c in (srv.dev.saved, srv.dev.system, srv.dev.running):
            c[k.replace("__", ".")] = v
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    return srv, srv.dev, dev, ui, app


def keys(app):
    return {k: b for k, _, b in app.changes()}


def run(app, ui, method, script):
    ui.script = list(script)
    ui.log.clear()
    try:
        getattr(app, method)()
    except AssertionError as e:
        print("     script:", e)
        return False
    return not ui.script


def flows(version, port):
    v = f"v{version}"
    print(f"=== {v}: Wireless menu against the simulator ===")
    srv, d, dev, ui, app = start(version, port)
    ok = run(app, ui, "wireless_menu", [("menu", None)])
    items = dict(ui.menus[-1][1])
    check(f"{v}: the menu shows the status", ok and items["s"] == "SSID: North-Tower"
          and tr("Access point") in items["m"] and "5745 MHz" in items["f"]
          and "WPA2" in items["x"], str(items))
    check(f"{v}: country with its name (the web's list)", app.dev.countries()
          and any(code == 840 for code, _ in app.dev.countries()))

    ok = run(app, ui, "wireless_menu", [("menu", "s"), ("input", "South-Link"), ("menu", None)])
    want = ({"wireless.1.ssid", "aaa.1.ssid", "wpasupplicant.profile.1.network.1.ssid"}
            if version == 8 else {"wireless.1.ssid", "aaa.1.ssid"})
    check(f"{v}: SSID from the menu", ok and set(keys(app)) == want, str(keys(app)))
    items = dict(ui.menus[-1][1])
    check(f"{v}: marked as changed", items["s"].startswith("* SSID"), items["s"])

    # channel width: 40 MHz makes the frequency be chosen again
    script = [("menu", "b"), ("menu", "40")]
    script += [("menu", "5755"), ("menu", "5765")] if version == 8 else []
    ok = run(app, ui, "wireless_menu", script + [("menu", None)])
    k = keys(app)
    if version == 8:
        check(f"{v}: 40 MHz, and the center and control frequencies", ok
              and k.get("radio.1.chanbw") == "40" and k.get("radio.1.center.1.freq") == "5755"
              and k.get("radio.1.freq") == "5765" and k.get("radio.1.ieee_mode") == "11acvht40",
              str(k))
    else:
        check(f"{v}: 40 MHz with the upper extension at 5745", ok and k.get("radio.1.chanbw")
              == "40" and k.get("radio.1.ieee_mode") == "11naht40plus", str(k))

    # security: WPA2 enterprise with RADIUS
    ok = run(app, ui, "wireless_menu", [
        ("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", "10.0.0.5"), ("input", "1812"),
        ("input", "s3cr3t"), ("menu", None), ("menu", None)])
    k = keys(app)
    check(f"{v}: WPA2 enterprise with its server", ok
          and k.get("aaa.1.wpa.key.1.mgmt") == "WPA-EAP"
          and k.get("aaa.1.radius.auth.1.ip") == "10.0.0.5", str(k))
    ok = run(app, ui, "wireless_menu", [
        ("menu", "x"), ("menu", "t"), ("menu", "psk"), ("input", "short"),
                                        ("input", "new-key-1"),
        ("menu", None), ("menu", None)])
    k = keys(app)
    check(f"{v}: back to PSK (with a short key rejected first)", ok
          and "aaa.1.wpa.key.1.mgmt" not in k and k.get("aaa.1.wpa.psk") == "new-key-1"
          and tr("Invalid value") in ui.titles(), str(k))

    # MAC ACL: add two, disable one and delete the other
    mac = "00:11:22:33:44:55" if version == 8 else "00:11:22:33:44:5a"
    ok = run(app, ui, "wireless_menu", [
        ("menu", "y"), ("menu", "a"), ("input", mac), ("input", "home"),
        ("menu", "a"), ("input", "66:77:88:99:AA:BB"), ("input", ""),
        ("menu", "1"), ("menu", "d"), ("menu", "1"), ("menu", "t"),
        ("menu", "s"), ("yesno", 0), ("menu", None), ("menu", None)])
    k = keys(app)
    check(f"{v}: an enabled ACL with one rule, disabled", ok
          and k.get("wireless.1.mac_acl.1.mac") == "66:77:88:99:AA:BB"
          and k.get("wireless.1.mac_acl.1.status") == "disabled"
          and k.get("wireless.1.mac_acl.status") == "enabled"
          and "wireless.1.mac_acl.2.mac" not in k, str(k))

    # advanced
    if version == 8:
        script = [("menu", "v"), ("menu", "2"), ("yesno", 0), ("menu", "8"), ("input", "-75"),
                  ("menu", None), ("menu", None)]
        ok = run(app, ui, "wireless_menu", script)
        k = keys(app)
        check(f"{v}: AMSDU and sensitivity threshold", ok and k.get("wireless.1.amsdu") == "1"
              and k.get("radio.1.rx_sensitivity") == "-75", str(k))
    else:
        script = [("menu", "v"), ("menu", "1"), ("yesno", 0), ("input", "16"), ("input", "30000"),
                  ("menu", "4"), ("yesno", 0), ("menu", None), ("menu", None)]
        ok = run(app, ui, "wireless_menu", script)
        k = keys(app)
        check(f"{v}: aggregation and isolation", ok and k.get("radio.1.ampdu.frames") == "16"
              and k.get("radio.1.ampdu.bytes") == "30000"
              and k.get("wireless.1.l2_isolation") == "enabled", str(k))

    # fixed distance (on airOS 8 the web hides it with the flexible frame: first, the legacy one)
    if version == 8:
        ok = run(app, ui, "wireless_menu", [("menu", "d"), ("menu", None)])
        check("v8: with the flexible frame the distance does not change", ok and any(
            tr("With this frame duration the web hides the distance: the device adjusts the "
               "timing by itself.") in t for k2, ti, t in ui.log if k2 == "msgbox"))
        ok = run(app, ui, "wireless_menu", [("menu", "t"), ("menu", "0"), ("menu", None)])
        check("v8: legacy flexible frame", ok and keys(app).get("radio.1.polling_ff_flex") == "0")
    # (3,1 with a decimal comma, which the program also accepts)
    ok = run(app, ui, "wireless_menu", [("menu", "d"), ("yesno", 1), ("input", "3,1"),
                                        ("menu", None)])
    k = keys(app)
    want = ("3100", "52") if version == 8 else ("3150", "42")
    check(f"{v}: distance of 3.1 km ({'exact' if version == 8 else 'in steps of 150 m'})",
          ok and (k.get("radio.1.ackdistance"), k.get("radio.1.acktimeout")) == want, str(k))

    # apply in test mode and keep
    ui.log.clear()
    ui.script = [("yesno", 0), ("yesno", 0)]
    app.apply(test=True)
    s = d.saved
    check(f"{v}: saved on the device", s.get("wireless.1.ssid") == "South-Link"
          and s.get("radio.1.chanbw") == "40" and s.get("wireless.1.mac_acl.status") == "enabled"
          and s.get("aaa.1.wpa.psk") == "new-key-1" and tr("Done") in ui.titles(),
          str(ui.titles()))
    check(f"{v}: after reading again, nothing pending", app.changes() == [])
    srv.shutdown()

    print(f"=== {v}: mode and country ===")
    srv, d, dev, ui, app = start(version, port + 1)
    if version == 8:
        script = [("menu", "m"), ("menu", "sta-ptmp"), ("menu", None)]
    else:
        script = [("menu", "m"), ("menu", "sta"), ("menu", None)]
    ok = run(app, ui, "wireless_menu", script)
    k = keys(app)
    check(f"{v}: AP -> station", ok and k.get("radio.1.mode") == "managed"
          and k.get("wpasupplicant.status") == "enabled" and tr("Wireless mode") in ui.titles(),
          str(k))
    items = dict(ui.menus[-1][1])
    check(f"{v}: the station menu changes its options", "k" in items and "f" not in items
          and "y" not in items, str(items))
    if version == 8:
        check("v8: station: the width becomes 20/40/80 (the select has no 20)",
              k.get("radio.1.chanbw") == "0" and k.get("radio.1.ieee_mode") == "auto", str(k))
    app.working = app.original.copy()
    if version == 8:
        country_v8(srv, d, ui, app)
    else:
        country_v6(srv, d, ui, app)
    srv.shutdown()


def country_v8(srv, d, ui, app):
    """airOS 8, like its web: POST /api/regdomain/country, applied and saved at once; what
    the new country does not allow is prepared as a pending change."""
    ok = run(app, ui, "wireless_menu", [("menu", "s"), ("input", "Pending"), ("menu", "c"),
                                        ("menu", None)])      # warns before the list
    msg = [t for k2, ti, t in ui.log if k2 == "msgbox" and ti == tr("Country")]
    check("v8: with unapplied changes the country does not change", ok and msg
          and tr("There are {count} unapplied change(s). Like its web, airOS 8 changes the "
                 "country right away and then reads the configuration again: apply or discard "
                 "them first.").partition("{count}")[2] in msg[0]
          and "country" not in d.events, str(msg))
    app.working = app.original.copy()
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "840"), ("yesno", 0),
                                        ("menu", None)])
    asked = [t for k2, ti, t in ui.log if k2 == "yesno" and ti == tr("Country")]
    msg = [t for k2, ti, t in ui.log if k2 == "msgbox" and ti == tr("Country")]
    check("v8: country 840 through the API, saved at once", ok and "country" in d.events
          and d.saved.get("radio.countrycode") == "840"
          and d.saved.get("radio.1.countrycode") == "840", str(d.events))
    check("v8: warns that it is applied and saved at once, without test mode",
          asked and tr("As its web does, airOS 8 applies and SAVES the new country right away, "
                       "without test mode: the wireless link drops for a few seconds while the "
                       "radio restarts.") in asked[0],
          asked[0] if asked else "")
    check("v8: reads the configuration again and nothing is left pending",
          app.original.get("radio.countrycode") == "840" and app.changes() == []
          and msg and tr("The country is now {country}, already saved on the device.")
          .partition("{country}")[2] in msg[-1], str(msg))
    menu = [m for m in ui.menus if m[0] == tr("Country")]
    check("v8: the web's list of countries, with names", menu
          and dict(menu[0][1]).get("840") == "United States", str(menu[:1]))
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "724"), ("yesno", 0),
                                        ("menu", "5200"), ("menu", None)])
    k = keys(app)
    asked = [t for k2, ti, t in ui.log if k2 == "yesno" and ti == tr("Country")]
    offered = [m for m in ui.menus if m[0] == tr("Frequency")]
    check("v8: to Spain: saved; the frequency and the power are left pending",
          ok and d.saved.get("radio.countrycode") == "724" and k == {
              "radio.1.center.1.freq": "5200", "radio.1.freq": "5200", "radio.1.txpower": "7"},
          str(k))
    check("v8: first it warns of what will not be valid in Spain", asked
          and tr("the frequency ({freq}) is not allowed there", freq="5745 MHz") in asked[-1]
          and tr("the power ({power} dBm) is above its limit there ({max} dBm)")
          .partition("{power}")[2].format(max=14) in asked[-1]
          and tr("After this change the web will not let you change the country again.")
          in asked[-1], asked[-1] if asked else "")
    check("v8: offers Spain's frequencies (with DFS marked)", offered and dict(
        offered[-1][1]).get("5500") == "5500 MHz (DFS)" and "5745" not in dict(offered[-1][1]),
        str(offered[-1:]))


def country_v6(srv, d, ui, app):
    """airOS 6: the country goes in the form of link.cgi, like any other change."""
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "840"), ("yesno", 0),
                                        ("menu", None)])
    k = keys(app)
    check("v6: country 840", ok and k.get("radio.countrycode") == "840"
          and k.get("radio.1.countrycode") == "840" and "country" not in d.events, str(k))
    menu = [m for m in ui.menus if m[0] == tr("Country")]
    check("v6: the web's list of countries, with names", menu
          and dict(menu[0][1]).get("840") == "United States", str(menu[:1]))
    app.working = app.original.copy()
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "724"), ("yesno", 0),
                                        ("menu", None)])
    k = keys(app)
    check("v6: to Spain: the frequency becomes automatic (as the page does)",
          ok and k.get("radio.countrycode") == "724" and k.get("radio.1.freq") == "0", str(k))


def special(port):
    print("=== special cases ===")
    srv, d, dev, ui, app = start(8, port)
    app.limits.board["feature.g3"] = "1"
    ok = run(app, ui, "wireless_menu", [])
    check("v8 Gen3: warns and does not offer the menu", ok and tr("Wireless") in ui.titles())
    srv.shutdown()

    srv, d, dev, ui, app = start(8, port + 1, radio__countrycode="724",
                                 radio__1__countrycode="724")
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", None)])
    msg = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Country")]
    check("v8: from Spain the country cannot be changed", ok and msg
          and tr("The web of this device does not let you change the country (it is locked to "
                 "{country}).").partition("{country}")[0] in msg[0], str(msg))
    srv.shutdown()

    S.BOARD[8]["radio.1.ieee_modes"] = "15"                # dual band (board and API)
    try:
        srv, d, dev, ui, app = start(8, port + 4, radio__1__ieee_mode="auto")
        ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "840"), ("yesno", 0),
                                            ("menu", None)])
        got = {k: d.saved.get(k) for k in ("radio.countrycode", "radio.1.ieee_mode")}
        check('v8 dual band in "auto": the country API sets the mode',
              ok and got == {"radio.countrycode": "840", "radio.1.ieee_mode": "11acvht80"},
              str(got))
        srv.shutdown()
    finally:
        S.BOARD[8]["radio.1.ieee_modes"] = "5"

    srv, d, dev, ui, app = start(8, port + 2, station=True)
    ok = run(app, ui, "quick_settings", [("menu", "3"), ("input", "other-key-1"),
                                         ("menu", None)])
    check("quick settings: station WPA key with the web's keys", ok and keys(app) == {
        "wpasupplicant.profile.1.network.1.psk": "other-key-1", "aaa.1.wpa.psk": "other-key-1"},
        str(keys(app)))
    ok = run(app, ui, "wireless_menu", [("menu", "u"), ("menu", "s"), ("input", "Backup"),
                                        ("menu", "x"), ("menu", "psk"), ("input", "backup-key"),
                                        ("menu", None), ("menu", None)])
    k = keys(app)
    check("v8: backup SSID with WPA2", ok
          and k.get("wpasupplicant.profile.1.network.2.status") == "enabled"
          and k.get("wpasupplicant.profile.1.network.2.psk") == "backup-key", str(k))
    ok = run(app, ui, "wireless_menu", [("menu", "b"), ("menu", "-1"), ("menu", "l"),
                                        ("menu", None)])
    k = keys(app)
    check("v8: station with automatic width: scanbw, and the list is not used", ok
          and k.get("radio.1.scanbw.status") == "enabled"
          and tr("With automatic channel width the station scans every frequency: the web does "
                 "not use the list then. Choose a width first.")
          in " ".join(t for kk, ti, t in ui.log if kk == "msgbox"), str(k))
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 3)
    ok = run(app, ui, "quick_settings", [("menu", "3"), ("input", "a" * 64), ("menu", None)])
    check("quick settings: the web does not accept a key of 64 hex digits", ok
          and keys(app) == {} and tr("Invalid value") in ui.titles(), str(keys(app)))
    ok = run(app, ui, "wireless_menu", [("menu", "f"), ("menu", "5765"), ("menu", None)])
    check("v6: AP frequency", ok and keys(app) == {"radio.1.freq": "5765"}, str(keys(app)))
    ok = run(app, ui, "wireless_menu", [("menu", "r"), ("yesno", 0), ("menu", None)])
    check("v6: turning airMAX off", ok and keys(app).get("radio.1.polling") == "disabled")
    srv.shutdown()


def step(app, ui, name, script, want):
    """A step of the walkthrough: the whole script and the keys expected in the changes."""
    ok = run(app, ui, "wireless_menu", script + [("menu", None)])
    k = keys(app)
    bad = {key: (k.get(key, "<unchanged>"), val) for key, val in want.items()
           if k.get(key, "<unchanged>") != val}
    check(name, ok and not bad, str(bad) if ok else "script")


def walkthrough(port):
    print("=== walkthrough of the settings: airOS 8, access point ===")
    srv, d, dev, ui, app = start(8, port)
    step(app, ui, "v8 AP: hide SSID", [("menu", "h"), ("yesno", 0)],
         {"wireless.1.hide_ssid": "enabled"})
    step(app, ui, "v8 AP: frequency list", [("menu", "l"), ("input", "5745, 5765")],
         {"radio.1.scan_list.status": "enabled", "radio.1.scan_list.channels": "5745,5765"})
    step(app, ui, "v8 AP: a list without the frequency in use makes it choose another one",
         [("menu", "l"), ("input", "5765"), ("menu", "5765")],
         {"radio.1.scan_list.channels": "5765", "radio.1.center.1.freq": "5765",
          "radio.1.freq": "5765"})
    step(app, ui, "v8 AP: one built-in antenna: nothing to choose", [("menu", "a")], {})
    check("  (it explains)", any(
        tr("This device has a single built-in antenna of {gain} dBi: there is nothing to "
           "choose.").partition("{gain}")[2] in t for k, ti, t in ui.log if k == "msgbox"))
    step(app, ui, "v8 AP: no EIRP limit calculation", [("menu", "e"), ("yesno", 0)],
         {"radio.1.reg_obey": "disabled"})
    step(app, ui, "v8 AP: power", [("menu", "p"), ("input", "19")], {"radio.1.txpower": "19"})
    step(app, ui, "v8 AP: fixed 8 ms frame, 75/25",
         [("menu", "t"), ("menu", "8000"), ("menu", "75")],
         {"radio.1.polling_ff_flex": "0", "radio.1.polling_ff_dur": "8000",
          "radio.1.polling_ff_dl_ratio": "75"})
    step(app, ui, "v8 AP: enterprise with accounting and deauthentication protection",
         [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", "10.0.0.5"),
          ("input", "1812"), ("input", "sec"), ("menu", "c"), ("input", "10.0.0.6"),
          ("input", "1813"), ("input", "acct"), ("menu", "d"), ("yesno", 0), ("menu", None)],
         {"aaa.1.radius.acct.1.status": "enabled", "aaa.1.radius.acct.1.ip": "10.0.0.6",
          "aaa.1.radius.acct.1.secret": "acct", "radio.1.polling_daprot": "1"})
    step(app, ui, "v8 AP: advanced (AMPDU, isolation, multicast, rate, ATPC)",
         [("menu", "v"), ("menu", "1"), ("input", "16"), ("menu", "4"), ("yesno", 0),
          ("menu", "5"), ("yesno", 0), ("menu", "6"), ("menu", "7"), ("menu", "7"),
          ("menu", "1"), ("menu", "9"), ("input", "40"), ("menu", None)],
         {"wireless.1.ampdu.frames": "16", "wireless.1.l2_isolation": "enabled",
          "wireless.1.mcast.enhance": "0", "wireless.1.rate.mcs": "7", "radio.1.rc_mode": "1",
          "radio.1.atpc.status": "enabled", "radio.1.atpc.threshold": "40"})
    items = dict(ui.menus[-1][1])
    check('v8 AP: "Advanced settings…" marked as changed',
          items.get("v", "").startswith("* "), items.get("v"))
    step(app, ui, "v8 AP: to PtP (and 80 MHz, with its frequency)",
         [("menu", "m"), ("menu", "ap-ptp"), ("menu", "b"), ("menu", "80"), ("menu", "5775"),
          ("menu", "5765")],
         {"radio.1.ptpmode": "1", "radio.1.chanbw": "80", "radio.1.ieee_mode": "11acvht80",
          "radio.1.center.1.freq": "5775"})
    srv.shutdown()

    print("=== walkthrough: airOS 8, station ===")
    srv, d, dev, ui, app = start(8, port + 1, station=True)
    step(app, ui, "v8 station: lock to the AP MAC",
         [("menu", "k"), ("input", "00:11:22:33:44:55")],
         {"wpasupplicant.profile.1.network.1.bssid": "00:11:22:33:44:55"})
    step(app, ui, "v8 station: airMAX priority and ATPC",
         [("menu", "v"), ("menu", "3"), ("menu", "0"), ("menu", "9"), ("yesno", 0),
          ("menu", None)], {"radio.1.pollingpri": "0", "radio.1.atpc.sta.status": "disabled"})
    step(app, ui, "v8 station: enterprise (user) and changing the credentials",
         [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", "anon"), ("input", "ana"),
          ("input", "pw"), ("menu", "u"), ("input", "anon2"), ("input", "ana2"),
          ("input", "pw2"), ("menu", None)],
         {"wpasupplicant.profile.1.network.1.key_mgmt.1.name": "WPA-EAP",
          "wpasupplicant.profile.1.network.1.identity": "ana2",
          "wpasupplicant.profile.1.network.1.password": "pw2"})
    step(app, ui, "v8 station: automatic 20/40 and scan list (of any width)",
         [("menu", "b"), ("menu", "0"), ("menu", "l"), ("input", "5745,5825")],
         {"radio.1.chanbw": "0", "radio.1.ieee_mode": "auto",
          "radio.1.scan_list.status": "enabled", "radio.1.scan_list.channels": "5745,5825"})
    widths = [m for m in ui.menus if m[0] == tr("Channel width")]
    check('  (PtMP: "Automatic" and 20/40 MHz)', widths and widths[-1][1] == [
        ("-1", tr("Automatic")), ("0", "20/40 MHz")], str(widths[-1:]))
    srv.shutdown()

    print("=== walkthrough: airOS 6, access point ===")
    srv, d, dev, ui, app = start(6, port + 2)
    step(app, ui, "v6 AP: WDS", [("menu", "w"), ("yesno", 0)], {"wireless.1.wds.status": "enabled"})
    step(app, ui, "v6 AP: hide SSID", [("menu", "h"), ("yesno", 0)],
         {"wireless.1.hide_ssid": "enabled"})
    step(app, ui, "v6 AP: a list without the frequency in use: offers only the listed ones",
         [("menu", "l"), ("input", "5765,5785"), ("menu", "5785")],
         {"wireless.1.scan_list.status": "enabled", "wireless.1.scan_list.channels": "5765,5785",
          "radio.1.freq": "5785"})
    offered = [m for m in ui.menus if m[0] == tr("Frequency")]
    check('  ("Automatic", 5765 and 5785)', offered and [t for t, _ in offered[-1][1]]
          == ["0", "5765", "5785"], str(offered[-1:]))
    step(app, ui, "v6 AP: one built-in antenna: nothing to choose", [("menu", "a")], {})
    step(app, ui, "v6 AP: no EIRP limit calculation", [("menu", "e"), ("yesno", 0)],
         {"radio.1.reg_obey": "disabled", "radio.1.obey": "disabled"})
    step(app, ui, "v6 AP: advanced (multicast, sensitivity, RTS, no aggregation)",
         [("menu", "v"), ("menu", "2"), ("yesno", 0), ("menu", "3"), ("yesno", 0),
          ("menu", "5"), ("input", "-80"), ("menu", "6"), ("input", "500"), ("menu", "1"),
          ("yesno", 1), ("menu", None)],
         {"netconf.2.allmulti": "disabled", "wireless.1.mcast.enhance": "0",
          "wireless.1.sens": "16", "radio.1.rts": "500", "radio.1.ampdu.status": "disabled"})
    step(app, ui, "v6 AP: enterprise with accounting",
         [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", "10.0.0.5"),
          ("input", "1812"), ("input", "sec"), ("menu", "c"), ("input", "10.0.0.6"),
          ("input", "1813"), ("input", "acct"), ("menu", None)],
         {"aaa.1.wpa.key.1.mgmt": "WPA-EAP", "aaa.1.radius.acct.1.status": "enabled",
          "aaa.1.radius.acct.1.port": "1813"})
    srv.shutdown()

    print("=== walkthrough: airOS 6, station ===")
    srv, d, dev, ui, app = start(6, port + 3, station=True)
    step(app, ui, "v6 station: airMAX priority", [("menu", "r"), ("menu", "1")],
         {"radio.1.pollingpri": "1"})
    step(app, ui, "v6 station: enterprise PEAP",
         [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("menu", "PEAP"), ("input", "anon"),
          ("input", "ana"), ("input", "pw"), ("menu", None)],
         {"wpasupplicant.profile.1.network.1.eap.1.name": "PEAP",
          "wpasupplicant.profile.1.network.1.identity": "ana",
          "wpasupplicant.device.1.profile": "WPA-EAP"})
    step(app, ui, "v6 station: automatic distance", [("menu", "d"), ("yesno", 0)],
         {"radio.1.ack.auto": "enabled"})
    step(app, ui, 'v6 station: 40 MHz is "Automatic 20/40"', [("menu", "b"), ("menu", "40")],
         {"radio.1.chanbw": "40", "radio.1.ieee_mode": "11naht40"})
    widths = [m for m in ui.menus if m[0] == tr("Channel width")]
    check("  (as the web calls it)", widths and dict(widths[-1][1]).get("40")
          == tr("Automatic 20/40 MHz"), str(widths[-1:]))
    srv.shutdown()


# ------------------------------------------------------------------------------------------
# What the walkthrough does not cover: a real device, antennas, uncommon readings, validations,
# cancelling or rejecting in each menu and the edges of the country change.

REAL = os.path.join(HERE, "fixtures", "real", "airos8.7.14-nanostation5ac-sta")


def msgs(ui, title=None):
    """The texts of the message windows of the last run (of that title, if given)."""
    return [t for k, ti, t in ui.log if k == "msgbox" and (title is None or ti == title)]


def texts(ui, kind, title):
    return [t for k, ti, t in ui.log if k == kind and ti == title]


def unchanged(app, ui, name, script):
    """A path of the Wireless menu that ends without preparing anything."""
    before = keys(app)
    ok = run(app, ui, "wireless_menu", script + [("menu", None)])
    check(name, ok and keys(app) == before,
          "script" if not ok else str(set(keys(app).items()) ^ set(before.items())))


def real_device():
    print("=== real device: airOS 8.7.14, NanoStation 5AC loco as a station (sanitized data) ===")
    with open(REAL + ".cfg", encoding="utf-8") as f:
        cfg = T.Config(f.read())
    with open(REAL + ".board.info", encoding="utf-8") as f:
        board = dict(line.split("=", 1) for line in f.read().splitlines() if "=" in line)
    st = T.wireless_state(cfg, "v8", board)
    got = {k: st[k] for k in ("ap", "ptp", "wds", "ssid", "security", "wpa_auth", "country",
                              "power", "antenna", "gain", "ieee", "chanbw", "distance",
                              "ack_auto", "obey", "bk_on")}
    check("reads what its web showed", got == {
        "ap": False, "ptp": False, "wds": True, "ssid": "Example-AP", "security": "wpa2",
        "wpa_auth": "WPA-PSK", "country": 511, "power": 25, "antenna": 1, "gain": 13,
        "ieee": "11acvht", "chanbw": 0, "distance": 643, "ack_auto": True, "obey": False,
        "bk_on": False}, str(got))
    saved = T.wireless_save(cfg, "v8", board, st)
    check("saving with no changes: no key changes or is added", not cfg.diff(saved),
          str(cfg.diff(saved)[:5]))
    check("its built-in antenna (NANO-G2): nothing to choose",
          [a["id"] for a in T.v8_antenna_choices(board, st["ieee"])] == [1])
    check("PtMP station widths: those its menu offered",
          T.v8_station_widths(st, board, None) == [-1, 0, 30, 10])

    def p(**changes):
        return T.plan_wireless(cfg, "v8", board, changes)
    check("SSID (station: no aaa.1.ssid)", *same(p(ssid="Other-Net"), {
        "wireless.1.ssid": "Other-Net", "wpasupplicant.profile.1.network.1.ssid": "Other-Net"}))
    check("WPA key: on both sides", *same(p(psk="other-key-1"), {
        "aaa.1.wpa.psk": "other-key-1",
        "wpasupplicant.profile.1.network.1.psk": "other-key-1"}))
    check("power", p(power=20) == {"radio.1.txpower": "20"})
    check("lock to the AP MAC", *same(p(ap_mac="00:11:22:33:44:55"), {
        "wpasupplicant.profile.1.network.1.bssid": "00:11:22:33:44:55"}))
    check("fixed distance of 5 km (and its ACK)", *same(p(ack_auto=False, distance=5000), {
        "radio.1.ack.auto": "disabled", "radio.1.ackdistance": "5000",
        "radio.1.acktimeout": "64"}))
    up = p(auto_chanbw=True)
    check("automatic width (the AP's)", up.get("radio.1.scanbw.status") == "enabled", str(up))


def antennas(port):
    print("=== antennas: several, external, custom or none in board.info ===")
    srv, d, dev, ui, app = start(8, port)
    app.limits.board.update({"radio.1.antennas": "2", "radio.1.antenna.2.id": "2",
                             "radio.1.antenna.2.name": "Sector 19",
                             "radio.1.antenna.2.gain": "19", "radio.1.antenna.2.builtin": "0"})
    unchanged(app, ui, "v8 with two antennas: cancel the choice", [("menu", "a"), ("menu", None)])
    offered = [m for m in ui.menus if m[0] == tr("Antenna")]
    check('  (offers both and "Custom…")', offered
          and [t for t, _ in offered[-1][1]] == ["1", "2", "0"], str(offered[-1:]))
    unchanged(app, ui, "v8 external antenna: cancel the cable loss",
              [("menu", "a"), ("menu", "2"), ("input", None)])
    power = app._wstate()["power"]
    sector = {"radio.1.antenna.id": "2", "radio.1.antenna.gain": "19", "radio.1.cable.loss": "2"}
    pl = app.limits.power_limit(T._merge(app.working.copy(), sector))
    unchanged(app, ui, "v8 external antenna that leaves the power above its limit: not saved, "
              "as in the web (the power must be lowered first)",
              [("menu", "a"), ("menu", "2"), ("input", "2")])
    check("  (it says so: the limit and the range the power must be in)", pl["max"] < power
          and msgs(ui, tr("Invalid value")) == [tr(
              "{power} dBm exceeds the legal limit the web calculates: {legal}. The web does not "
              "allow it: the power must be between {min} and {max} dBm.", power=power,
              legal=pl["legal"], min=pl["min"], max=pl["max"])], str(msgs(ui)))
    step(app, ui, "v8 external antenna, with the power already at its limit: its gain and the "
         "cable loss", [("menu", "p"), ("input", str(pl["max"])), ("menu", "a"),
                        ("menu", "2"), ("input", "2")],
         dict(sector, **{"radio.1.txpower": str(pl["max"])}))
    row = dict(ui.menus[-1][1]).get("a", "")
    check("  (the menu shows it with the loss)", "Sector 19" in row
          and tr("cable loss {loss} dB", loss=2) in row, row)
    unchanged(app, ui, "v8 custom antenna: cancel the gain",
              [("menu", "a"), ("menu", "0"), ("input", None)])
    custom = {"radio.1.antenna.id": "0", "radio.1.antenna.gain": "23", "radio.1.cable.loss": "3"}
    top = app.limits.power_limit(T._merge(app.working.copy(), custom))["max"]
    step(app, ui, "v8 custom antenna: a gain from 0 to 40 and a loss up to the gain",
         [("menu", "p"), ("input", str(top)), ("menu", "a"), ("menu", "0"), ("input", "45"),
          ("input", "23"), ("input", "24"), ("input", "3")], custom)
    check("  (rejected 45 dBi and a 24 dB loss)", ui.titles().count(tr("Invalid value")) == 2)
    ok = run(app, ui, "wireless_menu", [("menu", "a"), ("menu", "1"), ("menu", None)])
    check("v8 back to the built-in one: its gain, no loss and no questions", ok and not any(
        k.startswith(("radio.1.antenna", "radio.1.cable")) for k in keys(app)), str(keys(app)))
    for k in [k for k in app.limits.board if k.startswith("radio.1.antenna")]:
        del app.limits.board[k]
    app.limits.board.update({"radio.1.antennas": "1", "radio.1.antenna.1.id": "1",
                             "radio.1.antenna.1.name": "Not specified",
                             "radio.1.antenna.1.gain": "0", "radio.1.antenna.1.builtin": "0"})
    single = T.Config(app.original_text)          # (its configuration with that antenna)
    single.set("radio.1.antenna.id", "1")
    single.set("radio.1.antenna.gain", "0")
    app.set_loaded(single.text())
    top = app.limits.power_limit(T._merge(app.working.copy(), custom))["max"]
    step(app, ui, 'v8 with a single external antenna: its choices, "Custom…" too (a gain of its '
         "own: not a loss up to 0 dB)", [("menu", "p"), ("input", str(top)), ("menu", "a"),
                                          ("menu", "0"), ("input", "23"), ("input", "3")], custom)
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 1)
    app.limits.board.update({"radio.1.antenna.2.id": "2", "radio.1.antenna.2.name": "Sector 19",
                             "radio.1.antenna.2.gain": "19", "radio.1.antenna.2.builtin": "0"})
    unchanged(app, ui, "v6 with two antennas: cancel", [("menu", "a"), ("menu", None)])
    unchanged(app, ui, "v6 external antenna: cancel the loss",
              [("menu", "a"), ("menu", "2"), ("input", None)])
    power = app._wstate()["power"]
    step(app, ui, "v6 external antenna: the chosen one, its gain and the loss",
         [("menu", "a"), ("menu", "2"), ("input", "1")],
         {"radio.1.antenna.id": "2", "radio.1.antenna.gain": "19", "radio.1.cable.loss": "1"})
    pl = app.limits.power_limit(app.working)
    check("  (the power, above its new limit, goes down to it by itself, as on its page, and "
          "it says so)", pl["max"] < power and app.working.get("radio.1.txpower") == str(pl["max"])
          and msgs(ui, tr("Output power")) == [tr(
              "The power goes down from {old} to {new} dBm, the legal limit: {legal}. The web "
              "lowers it too.", old=power, new=pl["max"], legal=pl["legal"])], str(msgs(ui)))
    for k in [k for k in app.limits.board if k.startswith("radio.1.antenna.")]:
        del app.limits.board[k]
    app.set_loaded(app.original_text)
    unchanged(app, ui, "v6 without antennas in board.info: cancel the gain",
              [("menu", "a"), ("input", None)])
    step(app, ui, "v6 without antennas in board.info: asks for the gain and the loss",
         [("menu", "a"), ("input", "20"), ("input", "2")],
         {"radio.1.antenna.gain": "20", "radio.1.cable.loss": "2",
          "radio.1.txpower": str(app.limits.power_limit(T._merge(app.working.copy(), {
              "radio.1.antenna.gain": "20", "radio.1.cable.loss": "2"}))["max"])})
    app.limits.board.update({"radio.1.antenna.1.id": "1", "radio.1.antenna.1.name": "External",
                             "radio.1.antenna.1.gain": "0", "radio.1.antenna.1.builtin": "0"})
    app.set_loaded(app.original_text)
    step(app, ui, "v6 with a single external antenna: asks for its gain too (its legal limit "
         "counts it), and the loss", [("menu", "a"), ("input", "25"), ("input", "3")],
         {"radio.1.antenna.gain": "25", "radio.1.cable.loss": "3"})
    srv.shutdown()


def power_over(port):
    print("=== a device with the power above the legal limit: as each web does ===")
    for version in (8, 6):
        srv, d, dev, ui, app = start(version, port + version, radio__1__txpower="23")
        pl = app.limits.power_limit(app.working)
        step(app, ui, f"v{version}: changing something else on its page lowers the power to its "
             "limit, as opening it in the web does", [("menu", "s"), ("input", "Other-Net")],
             {"wireless.1.ssid": "Other-Net", "radio.1.txpower": str(pl["max"])})
        check("  (it says so)", pl["max"] < 23 and msgs(ui, tr("Output power")) == [tr(
            "The power goes down from {old} to {new} dBm, the legal limit: {legal}. The web "
            "lowers it too.", old=23, new=pl["max"], legal=pl["legal"])], str(msgs(ui)))
        ok = run(app, ui, "wireless_menu", [("menu", "p"), ("input", "22"), ("menu", None)])
        check("  and another one above the limit is not saved", ok and keys(app).get(
            "radio.1.txpower") == str(pl["max"]) and tr("Invalid value") in ui.titles())
        if version == 6:
            app.set_loaded(app.original_text)
            ui.log.clear()
            app._wchange(rts=500)
            check("  v6: not a change of another of its pages (Advanced: RTS), which has no power",
                  keys(app) == {"radio.1.rts": "500"} and not msgs(ui, tr("Output power")),
                  str(keys(app)))
        srv.shutdown()


def unit_v6_more():
    print("=== airOS 6: uncommon readings (as link.cgi does them) ===")

    def st(c, board=B6):
        return T.wireless_state(c, "v6", board)
    empty = T.Config("")
    check("band: the one board.info says", T.v6_band_a(empty, {"radio.1.ieee_mode_a": "1"})
          and not T.v6_band_a(empty, {"radio.1.ieee_mode_a": "0"}))
    check("band without board.info: 11ng is 2.4 GHz; without a mode, by the frequency",
          not T.v6_band_a(T.Config("radio.1.ieee_mode=11ngHT20\n"), {})
          and not T.v6_band_a(T.Config("radio.1.freq=2437\n"), {})
          and T.v6_band_a(T.Config("radio.1.freq=5745\n"), {}))
    check('mode "a" (no HT): read as 11naht',
          st(sim_cfg(6, radio__1__ieee_mode="a"))["ieee"] == "11naht")
    locked = dict(B6, **{"radio.1.ccode_locked": "1", "radio.1.ccode": "76"})
    check("country locked on the board: the board's one wins",
          st(sim_cfg(6, radio__countrycode="840"), locked)["country"] == 76)
    check("country 764: the EIRP calculation is mandatory", st(sim_cfg(
        6, radio__countrycode="764", radio__1__countrycode="764",
        radio__1__reg_obey="disabled"))["obey"])
    far = dict(st(sim_cfg(6, radio__1__ack__auto="enabled", radio__1__ackdistance="30000")),
               chanbw=40)
    fixed = T.wireless_errors(dict(far, ack_auto=False), B6)
    check("a width whose distance stops nearer: no error with the automatic distance (it is not "
          "used, as on airOS 8), as with airMAX without ACK; with a fixed one, yes",
          far["distance"] == 30000 and T.wireless_errors(far, B6) == []
          and T.wireless_errors(dict(far, ack_auto=False, autoack_disabled=True), B6) == []
          and len(fixed) == 1, str(fixed))
    ext = {k: v for k, v in B6.items() if not k.startswith("radio.1.antenna.")}
    ext.update({"radio.1.antenna.1.id": "1", "radio.1.antenna.1.gain": "0",
                "radio.1.antenna.1.builtin": "0"})
    first = st(sim_cfg(6, system__eirp__status=None), ext)
    check('first time and no built-in antenna: EIRP enabled, without "obey"',
          first["eirp_status"] == "enabled" and not first["obey"], str(first["eirp_status"]))
    one = st(sim_cfg(6, radio__1__antenna__gain="20"), ext)
    check("a single external antenna: the gain in the configuration",
          (one["antenna"], one["gain"]) == (1, 20), str((one["antenna"], one["gain"])))
    two = dict(B6, **{"radio.1.antenna.2.id": "2", "radio.1.antenna.2.gain": "19",
                      "radio.1.antenna.2.builtin": "0"})
    s = st(sim_cfg(6, radio__1__antenna__gain=None, radio__1__antenna__id="2"), two)
    check("no gain in the configuration: the chosen antenna's",
          (s["antenna"], s["gain"]) == (2, 19), str((s["antenna"], s["gain"])))
    bare = {k: v for k, v in B6.items()
            if not k.startswith("radio.1.antenna.") and k != "radio.1.def_antenna"}
    s = st(sim_cfg(6, radio__1__antenna__gain=None, radio__1__antenna__id=None), bare)
    check("neither antennas nor gain: 0 dBi", (s["antenna"], s["gain"]) == (0, 0))
    sta = st(sim_cfg(6, station=True, radio__1__chanbw=None, radio__1__ieee_mode="auto"))
    ap = st(sim_cfg(6, radio__1__chanbw=None, radio__1__ieee_mode="11naht20"))
    check("no width (as in the factory configuration): the station, 40 (Auto 20/40, as "
          "link.cgi does with what the radio says); the AP, the one of its mode",
          (sta["ap"], sta["chanbw"], ap["ap"], ap["chanbw"]) == (False, 40, True, 20),
          str((sta["ap"], sta["chanbw"], ap["ap"], ap["chanbw"])))
    s = st(sim_cfg(6, radio__1__ackdistance=None, radio__1__acktimeout="40"))
    s10 = st(sim_cfg(6, radio__1__ackdistance=None, radio__1__acktimeout="60",
                     radio__1__chanbw="10"))
    check("no ackdistance: the distance comes from the acktimeout minus the one at 0 m "
          "(cfg_get_ackdistance: 23 µs at 20 MHz, 45 at 10 MHz)",
          (s["distance"], s10["distance"]) == ((40 - 23) * 150, (60 - 45) * 150),
          str((s["distance"], s10["distance"])))
    check("power above the board's maximum: the maximum",
          st(sim_cfg(6, radio__1__txpower="30"))["power"] == 27)
    check("an authentication the form does not offer: read as PSK",
          st(sim_cfg(6, aaa__1__wpa__key__1__mgmt="IEEE8021X"))["wpa_auth"] == "WPA-PSK")
    check("bridged station with MAC cloning", st(sim_cfg(
        6, station=True, wireless__1__macclone="enabled"))["macclone"])
    rep = {"wireless__1__wds__status": "enabled", "wireless__1__autowds": "enabled"}
    check("AP-Repeater (automatic WDS) without WPA", st(sim_cfg(
        6, aaa__status="disabled", **rep))["wmode6"] == "aprepeater")
    check("AP-Repeater with WEP: still an AP-Repeater", st(sim_cfg(
        6, wireless__1__security__type="wep64", **rep))["wmode6"] == "aprepeater")
    check("AP-Repeater with WPA: link.cgi shows it as an access point",
          st(sim_cfg(6, **rep))["wmode6"] == "ap")

    mac = sim_cfg(6, wireless__1__security__type="none", ebtables__sys__eap__status="disabled",
                  aaa__status="enabled",
                  aaa__1__status="enabled", aaa__1__wpa__mode="0", aaa__1__devname="ath0",
                  aaa__1__radius__macacl__status="enabled",
                  aaa__1__radius__macacl__format="XX:XX:XX:XX:XX:XX",
                  aaa__1__radius__macacl__password__status="enabled",
                  aaa__1__radius__auth__1__status="enabled",
                  aaa__1__radius__auth__1__ip="10.0.0.5", aaa__1__radius__auth__1__port="1812",
                  aaa__1__radius__auth__1__secret="s3cr3t",
                  aaa__1__radius__acct__1__status="enabled",
                  aaa__1__radius__acct__1__ip="10.0.0.6", aaa__1__radius__acct__1__port="1813",
                  aaa__1__radius__acct__1__secret="acct")
    s = st(mac)
    check("open AP with RADIUS MAC authentication: read", s["security"] == "none"
          and s["radius_mac_on"] and s["acct_on"], str({k: s[k] for k in ("security",
                                                                          "radius_mac_on",
                                                                          "acct_on")}))
    saved = T.wireless_save(mac, "v6", B6, s)
    changed = [(k, a, b) for k, a, b in mac.diff(saved) if a is not None]
    check("  saving with no changes keeps it as it is", not changed, str(changed))
    up = plan(mac, "v6", ssid="Other")
    check("  changing the SSID does not touch it", up and not any(k.startswith("aaa.1.radius")
                                                                  for k in up), str(up))


def unit_validation():
    print("=== validation: what the web does not let you save ===")
    ap = T.wireless_state(sim_cfg(8), "v8", B8)
    sta = T.wireless_state(sim_cfg(8, station=True), "v8", B8)
    errs = T.wireless_errors(dict(sta, security="wpa2", wpa_auth="WPA-EAP", eap_user="ana",
                                  eap_pass="", eap_anon="x" * 64), B8)
    check("station enterprise: empty password and long anonymous identity", tr(
        "The password must have between 1 and 63 printable ASCII characters.") in errs and tr(
        "The anonymous identity must have between 1 and 63 printable ASCII characters.")
        in errs, str(errs))
    errs = T.wireless_errors(dict(ap, security="wpa2", wpa_auth="WPA-EAP", radius_ip="10.0.0.5",
                                  radius_port=70000, radius_secret="s"), B8)
    check("RADIUS server with an impossible port", tr(
        "{server}: the port must be between 1 and 65535.",
        server=tr("Authentication server")) in errs, str(errs))
    check("an ACL rule without a MAC", tr(
        "Every rule of the MAC ACL needs a MAC like 00:11:22:33:44:55.") in T.wireless_errors(
        dict(ap, acl_rules=[{"mac": "", "on": True, "comment": ""}]), B8))
    check("antenna not chosen (dual-band boards)", tr("Choose the antenna.")
          in T.wireless_errors(dict(ap, antenna=-1), B8))
    check("custom gain outside 0-40", tr(
        "The antenna gain must be a whole number from 0 to 40 dBi.") in T.wireless_errors(
        dict(ap, antenna=0, gain=41), B8))
    errs = T.wireless_errors(dict(sta, bk_on=True, bk_ssid="", bk_security="wpa2",
                                  bk_wpa_auth="WPA-PSK", bk_psk="short", bk_mac="xx"), B8)
    check("backup SSID: invalid SSID, key and MAC", [
        tr("Backup SSID: {error}", error=T._v_ssid("")),
        tr("Backup SSID: {error}", error=T._v_wpa_psk("short")),
        tr("Backup SSID: the AP MAC must be like 00:11:22:33:44:55.")] == [
        e for e in errs if e.startswith(tr("Backup SSID: {error}", error=""))], str(errs))


def menu_edges(port):
    print("=== airOS 8, access point: cancelling, rejecting and edge cases ===")
    srv, d, dev, ui, app = start(8, port)
    unchanged(app, ui, "mode: choosing the same one", [("menu", "m"), ("menu", "ap-ptmp")])
    unchanged(app, ui, "width: choosing the same one", [("menu", "b"), ("menu", "20")])
    unchanged(app, ui, "a width that needs another frequency: cancel the frequency",
              [("menu", "b"), ("menu", "40"), ("menu", None)])
    unchanged(app, ui, "power: cancel", [("menu", "p"), ("input", None)])
    unchanged(app, ui, "power: not a number", [("menu", "p"), ("input", "high")])
    check("  (it explains)", msgs(ui, tr("Invalid value"))
          == [tr("The power must be a whole number in dBm.")])
    unchanged(app, ui, "frame: cancel", [("menu", "t"), ("menu", None)])
    unchanged(app, ui, "frame: the same one", [("menu", "t"), ("menu", "1")])
    unchanged(app, ui, "fixed frame: cancel the ratio",
              [("menu", "t"), ("menu", "5000"), ("menu", None)])
    step(app, ui, "legacy flexible frame and the TDMA filter, which only exists with it",
         [("menu", "t"), ("menu", "0"), ("menu", "v"), ("menu", "t"), ("input", "5"),
          ("menu", None)], {"radio.1.polling_ff_flex": "0", "radio.1.cmsbias": "5"})
    step(app, ui, "sensitivity: rejects outside -90 to -40, cancel, and enable it",
         [("menu", "v"), ("menu", "8"), ("input", "-100"), ("menu", "8"), ("input", None),
          ("menu", "8"), ("input", "-75"), ("menu", None)], {"radio.1.rx_sensitivity": "-75"})
    unchanged(app, ui, "sensitivity enabled: cancel",
              [("menu", "v"), ("menu", "8"), ("yesno", 255), ("menu", None)])
    ok = run(app, ui, "wireless_menu", [("menu", "v"), ("menu", "8"), ("yesno", 1),
                                        ("menu", None), ("menu", None)])
    check("sensitivity: disable it (-96, as the web does)", ok
          and keys(app).get("radio.1.rx_sensitivity", "-96") == "-96", str(keys(app)))
    step(app, ui, "power control: enable it, cancel and change the target",
         [("menu", "v"), ("menu", "9"), ("input", "40"), ("menu", "9"), ("yesno", 255),
          ("menu", "9"), ("yesno", 0), ("input", "30"), ("menu", None)],
         {"radio.1.atpc.status": "enabled", "radio.1.atpc.threshold": "30"})
    ok = run(app, ui, "wireless_menu", [("menu", "v"), ("menu", "9"), ("yesno", 1),
                                        ("menu", None), ("menu", None)])
    check("power control: disable it", ok
          and keys(app).get("radio.1.atpc.status", "disabled") == "disabled")
    ok = run(app, ui, "wireless_menu", [("menu", "v"), ("menu", "5"), ("yesno", 0), ("menu", "5"),
                                        ("yesno", 0), ("menu", None), ("menu", None)])
    check("multicast enhancement: disable it and enable it again", ok
          and "wireless.1.mcast.enhance" not in keys(app)
          and texts(ui, "yesno", tr("Multicast enhancement"))[-1]
          == tr("Enable «{setting}»?", setting=tr("Multicast enhancement")))

    print("=== security: cancelling at each step, RADIUS server and accounting ===")
    unchanged(app, ui, "choosing the same one", [("menu", "x"), ("menu", "t"), ("menu", "psk"),
                                                 ("menu", None)])
    unchanged(app, ui, '"None" and "No"', [("menu", "x"), ("menu", "t"), ("menu", "none"),
                                           ("yesno", 1), ("menu", None)])
    for where, answers in (("the IP", [("input", None)]),
                           ("the port", [("input", "10.0.0.5"), ("input", None)]),
                           ("the secret", [("input", "10.0.0.5"), ("input", "1812"),
                                           ("input", None)])):
        unchanged(app, ui, f"enterprise: cancel {where} of the server",
                  [("menu", "x"), ("menu", "t"), ("menu", "eap")] + answers + [("menu", None)])
    step(app, ui, '"None" and "Yes"', [("menu", "x"), ("menu", "t"), ("menu", "none"),
                                       ("yesno", 0), ("menu", None)],
         {"aaa.status": "disabled", "aaa.1.status": "disabled"})
    unchanged(app, ui, "WPA2 personal: cancel the key",
              [("menu", "x"), ("menu", "t"), ("menu", "psk"), ("input", None), ("menu", None)])
    step(app, ui, "enterprise; then changing the server",
         [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", "10.0.0.5"), ("input", "1812"),
          ("input", "s3cr3t"), ("menu", "r"), ("input", "10.0.0.7"), ("input", "1645"),
          ("input", "other"), ("menu", None)],
         {"aaa.1.radius.auth.1.ip": "10.0.0.7", "aaa.1.radius.auth.1.port": "1645",
          "aaa.1.radius.auth.1.secret": "other"})
    step(app, ui, "accounting: enable it with its server",
         [("menu", "x"), ("menu", "c"), ("input", "10.0.0.6"), ("input", "1813"),
          ("input", "acct"), ("menu", None)],
         {"aaa.1.radius.acct.1.status": "enabled", "aaa.1.radius.acct.1.ip": "10.0.0.6"})
    unchanged(app, ui, "accounting enabled: cancel",
              [("menu", "x"), ("menu", "c"), ("yesno", 255), ("menu", None)])
    step(app, ui, "accounting enabled: change the server",
         [("menu", "x"), ("menu", "c"), ("yesno", 0), ("input", "10.0.0.8"), ("input", "1813"),
          ("input", "acct"), ("menu", None)], {"aaa.1.radius.acct.1.ip": "10.0.0.8"})
    ok = run(app, ui, "wireless_menu", [("menu", "x"), ("menu", "c"), ("yesno", 1),
                                        ("menu", None), ("menu", None)])
    check("accounting: disable it", ok and keys(app).get(
        "aaa.1.radius.acct.1.status", "disabled") == "disabled", str(keys(app)))
    app.set_loaded(app.original_text)

    print("=== MAC ACL ===")
    unchanged(app, ui, "add: cancel the MAC",
              [("menu", "y"), ("menu", "a"), ("input", None), ("menu", None)])
    unchanged(app, ui, "add: an invalid MAC",
              [("menu", "y"), ("menu", "a"), ("input", "00:11:22"), ("menu", None)])
    check("  (it explains)", msgs(ui, tr("Invalid value"))
          == [tr("The MAC must be like 00:11:22:33:44:55.")])
    unchanged(app, ui, "add: cancel the comment", [
        ("menu", "y"), ("menu", "a"), ("input", "00:11:22:33:44:55"), ("input", None),
        ("menu", None)])
    step(app, ui, 'add with a comment, enable the ACL and the "deny" policy', [
        ("menu", "y"), ("menu", "a"), ("input", "00:11:22:33:44:55"), ("input", "camera"),
        ("menu", "s"), ("yesno", 0), ("menu", "p"), ("menu", "deny"), ("menu", None)],
        {"wireless.1.mac_acl.1.mac": "00:11:22:33:44:55", "wireless.1.mac_acl.1.comment": "camera",
         "wireless.1.mac_acl.status": "enabled", "wireless.1.mac_acl.policy": "deny"})
    row = dict(ui.menus[-1][1]).get("y", "")
    check("  (the menu says it denies)", tr("deny the listed MACs ({count})", count=1) in row, row)
    unchanged(app, ui, "rule: cancel the comment", [
        ("menu", "y"), ("menu", "1"), ("menu", "c"), ("input", None), ("menu", None)])
    unchanged(app, ui, "rule: choose nothing", [("menu", "y"), ("menu", "1"), ("menu", None),
                                                ("menu", None)])
    unchanged(app, ui, "a rule that does not exist", [("menu", "y"), ("menu", "9"), ("menu", None)])
    step(app, ui, "rule: another comment, disable it and enable it again", [
        ("menu", "y"), ("menu", "1"), ("menu", "c"), ("input", " gate "), ("menu", "1"),
        ("menu", "t"), ("menu", "1"), ("menu", "t"), ("menu", None)],
        {"wireless.1.mac_acl.1.comment": "gate", "wireless.1.mac_acl.1.status": "enabled"})
    check("  (offers to enable it when it is disabled)", any(
        dict(items).get("t") == tr("Enable the rule") for title, items, _ in ui.menus
        if title == "00:11:22:33:44:55"))
    ok = run(app, ui, "wireless_menu", [("menu", "y"), ("menu", "1"), ("menu", "d"),
                                        ("menu", None), ("menu", None)])
    check("rule: delete it", ok and not any(k.startswith("wireless.1.mac_acl.1.")
                                            for k in keys(app)), str(keys(app)))
    srv.shutdown()

    print("=== airOS 8: without being able to read the device's limits ===")
    srv, d, dev, ui, app = start(8, port + 1)
    app.limits = None
    unchanged(app, ui, "width: it does not know which ones the country allows", [("menu", "b")])
    check("  (it explains)", msgs(ui, tr("Channel width"))
          == [tr("I could not read which widths the country allows.")])
    unchanged(app, ui, "frequency: it cannot read the channels", [("menu", "f")])
    check("  (it explains)", msgs(ui, tr("Frequency"))
          == [tr("I could not read the channels of country {cc}.", cc=484)])
    srv.shutdown()

    print("=== airOS 8, access point at 40 MHz and with a frequency list ===")
    srv, d, dev, ui, app = start(8, port + 2, radio__1__chanbw="40",
                                 radio__1__ieee_mode="11acvht40",
                                 radio__1__center__1__freq="5755", radio__1__freq="5745")
    unchanged(app, ui, "frequency: cancel the control one",
              [("menu", "f"), ("menu", "5795"), ("menu", None)])
    srv.shutdown()
    srv, d, dev, ui, app = start(8, port + 3, radio__1__scan_list__status="enabled",
                                 radio__1__scan_list__channels="5825",
                                 radio__1__center__1__freq="5825", radio__1__freq="5825")
    unchanged(app, ui, "40 MHz with a list that only has 5825: no frequencies",
              [("menu", "b"), ("menu", "40")])
    check("  (it explains)", msgs(ui, tr("Frequency")) == [tr(
        "There are no frequencies for this width in the country (or in the frequency list).")])
    srv.shutdown()

    print("=== airOS 8, station ===")
    srv, d, dev, ui, app = start(8, port + 4, station=True, radio__1__center__1__freq="5500",
                                 radio__1__freq="5500")
    step(app, ui, "SSID of the AP it connects to", [("menu", "s"), ("input", "Other-AP")],
         {"wireless.1.ssid": "Other-AP"})
    check("  (asks for it as a station)", texts(ui, "input", "SSID") == [
        tr("SSID of the access point this station connects to:")], str(texts(ui, "input", "SSID")))
    app.set_loaded(app.original_text)
    unchanged(app, ui, "station enterprise: cancel the credentials",
              [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("input", None), ("menu", None)])
    unchanged(app, ui, "distance: cancel", [("menu", "d"), ("yesno", 255)])
    unchanged(app, ui, "fixed distance: cancel the km", [("menu", "d"), ("yesno", 1),
                                                         ("input", None)])
    unchanged(app, ui, "fixed distance: rejects text and more than the width allows",
              [("menu", "d"), ("yesno", 1), ("input", "far"), ("menu", "d"), ("yesno", 1),
               ("input", "99")])
    check("  (explains it twice)", ui.titles().count(tr("Invalid value")) == 2)
    unchanged(app, ui, "to access point with a frequency the country does not have: cancel",
              [("menu", "m"), ("menu", "ap-ptmp"), ("menu", None)])
    step(app, ui, "to PtMP access point: flexible frame and choosing the frequency",
         [("menu", "m"), ("menu", "ap-ptmp"), ("menu", "5765")],
         {"radio.1.mode": "master", "radio.1.center.1.freq": "5765", "radio.1.freq": "5765",
          "radio.1.polling_ff_flex": "1"})
    app.set_loaded(app.original_text)

    print("=== airOS 8, station: backup SSID ===")
    step(app, ui, "enable it, change the SSID and lock to the MAC of its AP", [
        ("menu", "u"), ("menu", "s"), ("input", "Backup"), ("menu", "n"),
        ("input", "Backup-2"), ("menu", "m"), ("input", "00:11:22:33:44:66"), ("menu", None)],
        {"wpasupplicant.profile.1.network.2.status": "enabled",
         "wpasupplicant.profile.1.network.2.ssid": "Backup-2",
         "wpasupplicant.profile.1.network.2.bssid": "00:11:22:33:44:66"})
    unchanged(app, ui, "an invalid MAC is rejected",
              [("menu", "u"), ("menu", "m"), ("input", "00:11"), ("menu", None)])
    check("  (it explains)", tr("Backup SSID: the AP MAC must be like 00:11:22:33:44:55.")
          in " ".join(msgs(ui)), str(msgs(ui)))
    unchanged(app, ui, "security: cancel", [("menu", "u"), ("menu", "x"), ("menu", None),
                                            ("menu", None)])
    unchanged(app, ui, 'security: "None" when it already has none',
              [("menu", "u"), ("menu", "x"), ("menu", "none"), ("menu", None)])
    unchanged(app, ui, "WPA2 personal: cancel the key", [
        ("menu", "u"), ("menu", "x"), ("menu", "psk"), ("input", None), ("menu", None)])
    unchanged(app, ui, "WPA2 enterprise: cancel", [
        ("menu", "u"), ("menu", "x"), ("menu", "eap"), ("input", None), ("menu", None)])
    step(app, ui, "WPA2 enterprise: user and password", [
        ("menu", "u"), ("menu", "x"), ("menu", "eap"), ("input", "anon"), ("input", "ana"),
        ("input", "pw"), ("menu", None)],
        {"wpasupplicant.profile.1.network.2.key_mgmt.1.name": "WPA-EAP",
         "wpasupplicant.profile.1.network.2.identity": "ana",
         "wpasupplicant.profile.1.network.2.eap.1.status": "enabled"})
    ok = run(app, ui, "wireless_menu", [("menu", "u"), ("menu", "x"), ("menu", "none"),
                                        ("menu", None), ("menu", None)])
    check("no security again (key_mgmt NONE, as it was)", ok
          and app._wstate()["bk_security"] == "none" and keys(app).get(
              "wpasupplicant.profile.1.network.2.key_mgmt.1.name", "NONE") == "NONE",
          str(keys(app)))
    unchanged(app, ui, '"No" when disabling it', [("menu", "u"), ("menu", "s"), ("yesno", 1),
                                                  ("menu", None)])
    ok = run(app, ui, "wireless_menu", [("menu", "u"), ("menu", "s"), ("yesno", 0),
                                        ("menu", None), ("menu", None)])
    check("disabling it", ok and keys(app).get("wpasupplicant.profile.1.network.2.status",
                                               "disabled") == "disabled", str(keys(app)))
    srv.shutdown()

    print("=== airOS 6, access point: cancelling, rejecting and edge cases ===")
    srv, d, dev, ui, app = start(6, port + 5, radio__1__pollingnoack="1")
    unchanged(app, ui, "distance with airMAX without ACK: the web does not let you change it",
              [("menu", "d")])
    check("  (it explains)", msgs(ui, tr("Distance")) == [tr(
        "With airMAX without ACK or airSync the web does not let you change the distance.")])
    unchanged(app, ui, "list: cancel", [("menu", "l"), ("input", None)])
    unchanged(app, ui, "list: something that is not frequencies", [("menu", "l"),
                                                                   ("input", "5745,abc")])
    check("  (it explains)", msgs(ui, tr("Invalid value")) == [tr(
        "Type frequencies in MHz separated by commas, for example 5745,5765.")])
    unchanged(app, ui, "list: empty when it is already disabled", [("menu", "l"), ("input", "")])
    unchanged(app, ui, "a list without the frequency in use: cancel the frequency",
              [("menu", "l"), ("input", "5765"), ("menu", None)])
    unchanged(app, ui, "frequency: cancel", [("menu", "f"), ("menu", None)])
    st = app._wstate()
    check("list errors: the AP frequency outside it, an empty list, or outside the country", [
        app._scan_errors(dict(st, scan_on=True, scan=[5765], freq=5745)),
        app._scan_errors(dict(st, scan_on=True, scan=[], freq=0)),
        app._scan_errors(dict(st, scan_on=True, scan=[5745, 5999], freq=0))] == [
        [tr('The frequency of the access point must be one of the list, or "Automatic".')],
        [tr("The frequency list is empty.")],
        [tr("Some frequencies of the list are not allowed by country {cc} with {bw} MHz: "
            "{sample}.", cc=484, bw=20, sample="5999")]])
    rts = [("menu", "v"), ("menu", "6")]
    step(app, ui, "RTS: enable it", rts + [("input", "500"), ("menu", None)],
         {"radio.1.rts": "500"})
    unchanged(app, ui, "RTS enabled: cancel", rts + [("yesno", 255), ("menu", None)])
    step(app, ui, "RTS enabled: change it", rts + [("yesno", 0), ("input", "800"), ("menu", None)],
         {"radio.1.rts": "800"})
    ok = run(app, ui, "wireless_menu", rts + [("yesno", 1), ("menu", None), ("menu", None)])
    check("RTS: disable it", ok and "radio.1.rts" not in keys(app))
    sens = [("menu", "v"), ("menu", "5")]
    step(app, ui, "sensitivity: enable it", sens + [("input", "-80"), ("menu", None)],
         {"wireless.1.sens": "16"})
    unchanged(app, ui, "sensitivity enabled: cancel", sens + [("yesno", 255), ("menu", None)])
    unchanged(app, ui, "sensitivity: cancel the new value",
              sens + [("yesno", 0), ("input", None), ("menu", None)])
    unchanged(app, ui, "sensitivity: outside -96 to -1", sens + [("yesno", 0), ("input", "-120"),
                                                                 ("menu", None)])
    ok = run(app, ui, "wireless_menu", sens + [("yesno", 1), ("menu", None), ("menu", None)])
    check("sensitivity: disable it", ok and "wireless.1.sens" not in keys(app),
          str(keys(app)))
    unchanged(app, ui, "aggregation: cancel the frames",
              [("menu", "v"), ("menu", "1"), ("yesno", 0), ("input", None), ("menu", None)])
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 6, radio__1__polling_fh="1")
    unchanged(app, ui, "airMAX with airSelect: changed from the web", [("menu", "r")])
    check("  (it explains)", msgs(ui, "airMAX") == [tr(
        "airSelect is on: change airMAX from the web, which also rebuilds the airSelect "
        "frequencies.")])
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 7, radio__1__polling="disabled")
    step(app, ui, "enable airMAX", [("menu", "r"), ("yesno", 0)], {"radio.1.polling": "enabled"})
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 8, radio__countrycode="511",
                                 radio__1__countrycode="511")
    unchanged(app, ui, "EIRP with country 511: the web does not let you change it",
              [("menu", "e")])
    check("  (it explains)", msgs(ui, tr("Calculate EIRP limit")) == [tr(
        "With country {cc} the web does not let you change it.", cc=511)])
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 9, radio__1__reg_obey="disabled", radio__1__freq="5825")
    every = app.limits.channels(484, 20)
    app.limits._cache[(484, 20)] = [c._replace(obey_ok=c.control < 5800) for c in every]
    step(app, ui, "enabling the EIRP calculation: the frequency goes down to the nearest allowed",
         [("menu", "e"), ("yesno", 0)], {"radio.1.reg_obey": "enabled", "radio.1.freq": "5785"})
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 10, radio__1__chanbw="40",
                                 radio__1__ieee_mode="11naht40plus")
    every = app.limits.channels(484, 40)
    app.limits._cache[(484, 40)] = [c._replace(ext=7) if c.control == 5765 else c for c in every]
    unchanged(app, ui, "40 MHz: cancel the extension channel",
              [("menu", "f"), ("menu", "5765"), ("menu", None)])
    step(app, ui, "40 MHz: if the frequency allows both, it asks for the extension channel",
         [("menu", "f"), ("menu", "5765"), ("menu", "minus")],
         {"radio.1.freq": "5765", "radio.1.ieee_mode": "11naht40minus"})
    srv.shutdown()

    print("=== airOS 6: AP-Repeater, and from station to access point ===")
    srv, d, dev, ui, app = start(6, port + 11, wireless__1__wds__status="enabled",
                                 wireless__1__autowds="enabled", aaa__status="disabled",
                                 wireless__1__security__type="none")
    unchanged(app, ui, "AP-Repeater: WDS cannot be removed", [("menu", "w")])
    check("  (it explains)", msgs(ui, "WDS") == [tr("An AP-Repeater always uses WDS.")])
    unchanged(app, ui, "AP-Repeater: the mode menu explains it; cancel",
              [("menu", "m"), ("menu", None)])
    check("  (the note)", texts(ui, "menu", tr("Wireless mode"))[-1].startswith(tr(
        "The device is an AP-Repeater (WDS with peers): airos-tui does not configure that "
        "mode, but you can change it to access point or station.")))
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 12, station=True,
                                 wpasupplicant__profile__1__network__1__proto__1__name="WPA",
                                 wpasupplicant__profile__1__network__1__pairwise__1__name="TKIP")
    unchanged(app, ui, "WPA-TKIP: the web no longer offers it; explained when choosing",
              [("menu", "x"), ("menu", "t"), ("menu", None), ("menu", None)])
    check("  (the note)", any(t.startswith(tr(
        "Current: {security}. The web no longer offers it (weak security); choose one of these.",
        security="WPA-TKIP")) for t in texts(ui, "menu", tr("Security"))),
        str(texts(ui, "menu", tr("Security"))))
    unchanged(app, ui, "enterprise: cancel the EAP method",
              [("menu", "x"), ("menu", "t"), ("menu", "eap"), ("menu", None), ("menu", None)])
    step(app, ui, "from station to access point: automatic frequency and an airMAX warning",
         [("menu", "m"), ("menu", "ap")], {"radio.1.mode": "master"})
    text = " ".join(msgs(ui, tr("Wireless mode")))
    check("  (it explains)", tr("Frequency: automatic, as the web leaves it; choose one in "
                                "Frequency if you want.") in text and tr(
        "airMAX is disabled: enable it in airMAX if the stations use it.") in text, text)
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 13, station=True,
                                 wpasupplicant__profile__1__network__1__proto__1__name="RSN",
                                 wpasupplicant__profile__1__network__1__pairwise__1__name=(
                                     "TKIP CCMP"))
    step(app, ui, "WPA2 with TKIP and AES (the web no longer offers it): WPA2 personal, the "
         "same type, moves it to AES", [("menu", "x"), ("menu", "t"), ("menu", "psk"),
                                         ("input", "Sup3r-Secret-Key"), ("menu", None)],
         {"wpasupplicant.profile.1.network.1.pairwise.1.name": "CCMP",
          "wpasupplicant.profile.1.network.1.psk": "Sup3r-Secret-Key"})
    srv.shutdown()


def country_edges(port):
    print("=== country: locks, errors and what the new country does not allow ===")
    srv, d, dev, ui, app = start(8, port)
    app.limits.board["radio.1.ccode_locked"] = "1"
    unchanged(app, ui, "v8 with the country locked on the board", [("menu", "c")])
    check("  (it explains)", msgs(ui, tr("Country")) == [tr(
        "The web of this device does not let you change the country (it is locked to "
        "{country}).", country=app._w_country_text(app._wstate()))], str(msgs(ui)))
    app.limits.board["radio.1.ccode_locked"] = "0"
    other = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    other.connect()
    c = T.Config(other.get_config())
    c.set("radio.1.txpower", "20")
    other.write_config(c, test=True)
    settle(srv)
    unchanged(app, ui, "v8 with a test in progress on the device", [("menu", "c")])
    check("  (it explains)", msgs(ui, tr("Country")) == [tr(
        "A test is in progress on the device: confirm it or revert it first.")])
    other.discard_test()
    settle(srv)
    app._country_cache = []
    unchanged(app, ui, "without the list of countries: asks for the code; empty changes nothing",
              [("menu", "c"), ("input", "")])
    app._country_cache = None
    unchanged(app, ui, "a country the device does not know: shows its error",
              [("menu", "c"), ("menu", "999"), ("yesno", 0)])
    check("  (the device's error)",
          any("Invalid country code" in m for m in msgs(ui, tr("Country"))), str(msgs(ui)))
    st = app._wstate()
    ui.log.clear()
    check("no channels of the country in the band of the radio: it says so",
          app._after_country(dict(st, ieee="11nght"), 840) is None
          and msgs(ui, tr("Country")) == [tr("The device has no channels in that country in its "
                                             "band.")])
    ui.script = [("menu", "5755"), ("menu", "5745")]
    r = app._after_country(dict(st, chanbw=30), 840)
    check("a width the country does not offer: the first one it does (and its frequency)",
          r and (r.get("chanbw"), r.get("center"), r.get("freq")) == (40, 5755, 5745), str(r))
    ui.script = [("menu", None)]
    check("cancelling the frequency in the new country: nothing is prepared",
          app._after_country(dict(st, scan_on=True, scan=[5745, 5180]), 724) is None)
    with patched_wait(app, False):
        unchanged(app, ui, "if it does not answer again after the change, it says so",
                  [("menu", "c"), ("menu", "840"), ("yesno", 0)])
    check("  (it explains)", tr("The device does not answer yet. When it does, read its "
                                "configuration again (main menu).")
          in msgs(ui, tr("No answer")), str(msgs(ui)))
    check("  (and the country did change: it is saved at once)",
          d.saved.get("radio.countrycode") == "840")
    srv.shutdown()

    srv, d, dev, ui, app = start(8, port + 1, radio__1__scan_list__status="enabled",
                                 radio__1__scan_list__channels="5745,5180")
    unchanged(app, ui, 'to Spain with the list and the frequency outside its band: warns; "No"',
              [("menu", "c"), ("menu", "724"), ("yesno", 1)])
    q = texts(ui, "yesno", tr("Country"))[-1] if texts(ui, "yesno", tr("Country")) else ""
    check("  (what it cannot keep, and that the country cannot be changed again afterwards)",
          tr("some frequencies of the list are not allowed there") in q
          and tr("After this change the web will not let you change the country again.") in q, q)
    real_login = dev.login

    def login_fails():
        raise T.AirOSError("simulated")
    dev.login = login_fails
    ok = run(app, ui, "wireless_menu", [("menu", "c"), ("menu", "724"), ("yesno", 0),
                                        ("menu", None), ("menu", None)])
    dev.login = real_login
    text = " ".join(msgs(ui, tr("Country")))
    check("changing without choosing the frequency: warns that settings are still not allowed",
          ok and d.saved.get("radio.countrycode") == "724" and not keys(app)
          and tr("Some settings are still not allowed in the new country: check the width, the "
                 "frequency and the power.") in text, text)
    srv.shutdown()

    srv, d, dev, ui, app = start(8, port + 2, radio__1__scan_list__status="enabled",
                                 radio__1__scan_list__channels="5745,5180")
    step(app, ui, "changing to Spain: the list keeps what is allowed; the frequency comes from it",
         [("menu", "c"), ("menu", "724"), ("yesno", 0), ("menu", "5180")],
         {"radio.1.scan_list.channels": "5180", "radio.1.center.1.freq": "5180",
          "radio.1.freq": "5180"})
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 3)
    st = app._wstate()
    r = app._after_country(st, 511)
    check('v6 to "Licensed" (511): no EIRP calculation, as its web does', r is not None
          and r.get("obey") is False, str(r))
    for bw in app.limits.supported_bw():          # the widths with no channels there
        if app.limits.channels(840, bw) is None:
            app.limits._cache[(840, bw)] = []
    r = app._after_country(dict(st, chanbw=10), 840)
    check("v6 with a width the country does not have: the first one up to 20 MHz", r is not None
          and r.get("chanbw") == 20, str(r))
    r = app._after_country(st, 764)
    off = T._merge(app.working.copy(), {"radio.1.reg_obey": "disabled"})
    at764 = T._merge(off.copy(), {"radio.countrycode": "764", "radio.1.countrycode": "764"})
    pl = app.limits.power_limit(at764)
    check("v6 to 764: the EIRP calculation, which its page always does there (and prepares); its "
          "legal limit counts it, as the menu shows it, whatever the keys say",
          r is not None and r.get("obey") is True
          and T.plan_wireless(off, "v6", app._board(), r).get("radio.1.reg_obey") == "enabled"
          and T.wireless_state(at764, "v6", app._board())["obey"] and pl is not None
          and pl["note"] != tr("the EIRP calculation is disabled (radio.1.reg_obey)"),
          str((r, pl)))
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 4, radio__1__chanbw="40",
                                 radio__1__ieee_mode="11naht40minus", radio__1__freq="5805",
                                 radio__1__center__1__freq="5805", radio__1__reg_obey="disabled")
    every = app.limits.channels(484, 40)
    app.limits._cache[(484, 40)] = [c._replace(obey_ok=c.control < 5760) for c in every]
    step(app, ui, "v6 at 40 MHz, enabling the EIRP calculation: the frequency goes down to the "
         "nearest allowed, with the extension channel it allows (only the upper one there)",
         [("menu", "e"), ("yesno", 0)], {"radio.1.reg_obey": "enabled", "radio.1.freq": "5745",
                                         "radio.1.ieee_mode": "11naht40plus"})
    app.set_loaded(app.original_text)
    for bw in app.limits.supported_bw():
        app.limits.channels(840, bw)
    app.limits._cache[(840, 40)] = [c._replace(ext=3) if c.control == 5805 else c
                                    for c in app.limits.channels(840, 40)]
    r = app._after_country(app._wstate(), 840)
    check("  another country where its frequency is allowed, but not its extension channel: "
          "the one it allows", r is not None and r.get("ext") == "plus" and "freq" not in r,
          str(r))
    srv.shutdown()

    srv, d, dev, ui, app = start(6, port + 5, station=True)
    step(app, ui, "v6 station: its airMAX priority", [("menu", "r"), ("menu", "1")],
         {"radio.1.pollingpri": "1"})
    rows = dict(ui.menus[-1][1])
    check("  marks the airMAX row only (on airOS 6 it is not an advanced setting)",
          rows["r"].startswith("* ") and not rows["v"].startswith("* "), str(rows))
    srv.shutdown()


class patched_wait:
    """app.wait_for_device answers this for a while."""

    def __init__(self, app, answer):
        self.app, self.answer = app, answer

    def __enter__(self):
        self.app.wait_for_device = lambda *a, **k: self.answer

    def __exit__(self, *exc):
        del self.app.wait_for_device


def review(port):
    print("=== the review: the MAC list with gaps, the SSID and the automatic distance ===")

    def acl(c):
        return [(n, c.get(f"wireless.1.mac_acl.{n}.mac"), c.get(f"wireless.1.mac_acl.{n}.status"))
                for n in range(1, 10) if c.get(f"wireless.1.mac_acl.{n}.mac")]
    for version, flavor in ((8, "v8"), (6, "v6")):
        base = T.Config(S.cfg_text(S.Device(version, "ubnt", "ubnt", 1, 60).running))
        board = dict(S.BOARD[version])
        for nums, add in (((1, 3), True), ((2, 5), False)):
            c = base.copy()
            c.set("wireless.1.mac_acl.status", "enabled")
            for i, n in enumerate(nums):
                for k, v in (("mac", f"00:11:22:33:44:5{i}"), ("status", "enabled"),
                             ("comment", f"sta{i}")):
                    c.set(f"wireless.1.mac_acl.{n}.{k}", v)
            rules = [dict(r) for r in T.wireless_state(c, flavor, board)["acl_rules"]]
            if add:
                rules.append({"mac": "AA:BB:CC:DD:EE:FF", "on": True, "comment": "new"})
            else:
                rules[-1]["on"] = False
            after = T._merge(c.copy(), T.plan_wireless(c, flavor, board, {"acl_rules": rules}))
            want = ([(1, "00:11:22:33:44:50", "enabled"), (2, "00:11:22:33:44:51", "enabled"),
                     (3, "AA:BB:CC:DD:EE:FF", "enabled")] if add else
                    [(1, "00:11:22:33:44:50", "enabled"), (2, "00:11:22:33:44:51", "disabled")])
            check(f"{flavor}: rules at {nums} ({'add' if add else 'disable the last one'}): "
                  "no station is lost and no other changes", acl(after) == want, str(acl(after)))
    srv, d, dev, ui, app = start(8, port, wireless__1__ssid="Café-North")   # (not all ASCII)
    try:
        ui.script = [("input", "")]
        app._w_ssid()
        check("with the current SSID already invalid, an empty one is rejected", not app.changes()
              and msgs(ui, tr("Invalid value")) == [tr(
                  "The SSID must have between 1 and 32 printable ASCII characters.")])
        st = dict(app._wstate(), ack_auto=True, distance=20000, chanbw=30)
        check("with the automatic distance, changing the width does not clash with its limit",
              not [e for e in T.wireless_errors(st, app._board()) if "distance" in e
                   or tr("Distance").lower() in e])    # (in English or in the language in use)
        ui.log.clear()
        ui.script = [("menu", None)]
        app._acl_rule(dict(app._wstate(), acl_rules=[{"mac": None, "on": True,
                                                     "comment": ""}]), 0)
        check("a rule without a MAC: its menu, without crashing", not ui.script
              and ui.menus[-1][0] == "?")
    finally:
        srv.shutdown()
    check("js_number as JavaScript's Number(): trims, empty is 0, only the digits 0-9", [
        T.js_number(x) for x in (" 24", "  ", "1e3", "0x1F")] == [24.0, 0.0, 1000.0, 31.0]
          and math.isnan(T.js_number("٣")) and T.js_int_like(" 24") == 24)


review(18681)
unit_v8()
unit_v6()
unit_limits()
flows(8, 18611)
flows(6, 18621)
special(18631)
walkthrough(18641)
real_device()
unit_v6_more()
unit_validation()
antennas(18651)
power_over(18740)
menu_edges(18653)
country_edges(18667)
shutil.rmtree(XDG, ignore_errors=True)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
