"""airOS 8's web and its API: the paths airos-tui uses, as the real firmware answers
them (cookie session, X-CSRF-ID, masked passwords, test mode)."""
from __future__ import annotations

import secrets
import threading
import time
from .data import (
    BOARD, COUNTRIES, MAC, PASSWORD_RE, SPEEDTEST_PEER, SURVEY_APS, SURVEY_TIME, UI_LANGS, _cc,
    regdomain_v8,)
from .cfgfile import cfg_text, parse_cfg
from .device import Device


V8_ONLY_IF = {           # (airOS 8: what else these paths need; otherwise, 404)
    "/api/auth": lambda h, m: m == "POST",
    "/reset.cgi": lambda h, m: h.form.get("reset") == "yes",
    "/api/provmode": lambda h, m: h.form.get("action") in ("start", "stop"),
    "/reboot.cgi": lambda h, m: h.form.get("reboot") == "yes",
}


class Web8:
    # -- airOS 8 --------------------------------------------------------------

    def _v8(self, method, path):
        """airOS 8's web and its API: its open paths, then (without its session: 403) the
        rest; what only takes a POST, with another method, 404 (see V8_OPEN, V8_ANY and
        V8_POST; and V8_ONLY_IF, what else some take)."""
        dev: Device = self.server.dev
        handler = self.V8_OPEN.get(path)
        if handler and self._v8_takes(path, method):
            return handler(self, dev)
        sess = dev.sessions.get(self.sid or "")
        if not sess or self.headers.get("X-CSRF-ID") != sess["csrf"]:
            return self._send(403, {"error": "unauthorized"})
        handler = self.V8_ANY.get(path)
        if handler:
            return handler(self, dev)
        if method != "POST":
            return self._send(404, "not found")
        handler = self.V8_POST.get(path)
        if handler and self._v8_takes(path, method):
            return handler(self, dev)
        return self._send(404, "not found")

    def _v8_takes(self, path, method) -> bool:
        """Whether a request is one of that path (V8_ONLY_IF: otherwise, 404)."""
        cond = V8_ONLY_IF.get(path)
        return cond is None or cond(self, method)

    def _v8_poll_cgi(self, dev: Device):
        return self._send(200, "")

    def _v8_root(self, dev: Device):
        return self._send(200, "<html>airOS 8 (simulated)</html>")

    def _v8_api_info_public(self, dev: Device):
        # what its web reads before its login
        info = {"ui_lang": dev.running.get("gui.language") or "en_US",
                "product_name": BOARD[8]["board.name"]}
        if dev.wizard:                        # (its first setup: done, or waiting for it)
            info["default_password" if dev.setup_pending else "setup_complete"] = True
            if dev.factory_password:          # (done, but with the factory password)
                info["default_password"] = True
        if dev.setup_pending or self.query.get("force_setup_data") == "true":
            countries = [{"code": cc, "name": name} for cc, (name, _, _) in COUNTRIES.items()]
            info["setup_data"] = {"rd_countries": countries,
                                  "license_agreement": "(simulated terms of use)"}
            if dev.setup_pending:
                info["setup_data"]["default_password"] = True
        if self.query.get("include_langs") == "true":   # (the languages of its web)
            info["ui_langs"] = UI_LANGS
        return self._send(200, info)

    def _v8_api_auth(self, dev: Device):
        setup_error = dev.first_setup(self.form) if self.form.get("device_setup") \
            and dev.wizard else "no setup"
        if setup_error is None or (self.form.get("username") == dev.login_name()
                                   and self.form.get("password") == dev.password
                                   and not self.form.get("device_setup")):
            sid, csrf = secrets.token_hex(16), secrets.token_hex(16)
            dev.sessions[sid] = {"csrf": csrf}
            board = "".join(f"{k}={v}\n" for k, v in BOARD[8].items())
            cc = _cc(dev.running.get("radio.countrycode")) or 484
            return self._send(200, {"fullVersion": f"WA.ar934x.{dev.fw_version}.48279.250811."
                                                   "0636",
                                    "readOnlyUser": False, "boardinfo": board,
                                    "ccode": cc, "rd": regdomain_v8(cc)},
                              headers=[("Set-Cookie", f"AIROS_{MAC}={sid}; Path=/; HttpOnly"),
                                       ("Set-Cookie", "ok=1; Path=/"),
                                       ("X-CSRF-ID", csrf)])
        if self.form.get("device_setup") and dev.wizard:
            return self._send(200, {"error": setup_error})
        return self._send(403, {"error": "Invalid credentials"})

    def _v8_changelog_md(self, dev: Device):
        # the release notes the update check points to
        offered = (dev.fw_available or "WA.v8.7.20").split(".", 1)[-1]
        return self._send(200, f"# airOS {offered} (simulated)\n\n- Fixes.\n",
                          "text/markdown")

    def _v8_api_warnings(self, dev: Device):
        return self._send(200, dev.warnings_info())

    def _v8_status_cgi(self, dev: Device):
        return self._send(200, dev.status())

    def _v8_airviewdata_cgi(self, dev: Device):
        # (with the management radio on: empty)
        body = b"" if dev.airview_state() == 3 else dev.airview_data()
        return self._send(200, body, "application/json; charset=utf-8")

    def _v8_getcfg_cgi(self, dev: Device):
        masked = {k: ("-" if PASSWORD_RE.match(k) else v) for k, v in dev.system.items()}
        return self._send(200, cfg_text(masked), "text/plain",
                          headers=[("X-Cfg-Modified", "1" if dev.backup_loaded else "0")])

    def _v8_cfg_cgi(self, dev: Device):
        # "Back Up Configuration"
        return self._send(200, dev.backup_file(), "text/plain; charset=utf-8",
                          headers=[("Content-Disposition",
                                    f'attachment; filename="WA-{MAC}.cfg"')])

    def _v8_test_mode_cgi(self, dev: Device):
        if dev.test_until:
            return self._send(200, {"active": 1, "countdown_started": 1,
                                    "time_left": int(dev.test_until - time.time())})
        return self._send(200, {"active": 0, "countdown_started": 0, "time_left": 0})

    def _v8_api_regdomain(self, dev: Device):
        cc = self.query.get("cc") or dev.running.get("radio.countrycode") or "484"
        return self._send(200, {"data": regdomain_v8(cc)})

    def _v8_api_firewall_user_rules(self, dev: Device):
        # its main page's device details
        return self._send(200, dev.firewall_listing(), "text/plain; charset=utf-8")

    def _v8_api_firewall_port_forward(self, dev: Device):
        return self._send(200, dev.portfw_listing(), "text/plain; charset=utf-8")

    def _v8_api_dhcp_server_leases(self, dev: Device):
        return self._send(200, dev.leases())

    def _v8_api_pppoe_info(self, dev: Device):
        ppp = dev.pppoe_state()
        if "error" in ppp:
            return self._send(200, ppp)
        return self._send(200, {"interfaces": [dict(ppp, in_bytes=10677, in_packets=173,
                                                    out_bytes=15628, out_packets=115)]})

    def _v8_survey_json_cgi(self, dev: Device):
        # the scan takes a moment: the web asks again
        if self.query.get("update") != "last" or dev.survey_until is None:
            dev.survey_until = time.time() + SURVEY_TIME
        done = time.time() >= dev.survey_until
        return self._send(200, {"scan_status": "completed" if done else "scanning",
                                "scan_data": SURVEY_APS if done else SURVEY_APS[:1]})

    def _v8_ipscan_cgi(self, dev: Device):
        # the destinations the network tools offer
        return self._send(200, "192.168.1.1\n192.168.1.21\n", "text/plain")

    def _v8_sptest_action_cgi(self, dev: Device):
        # Tools → Speed Test (its web's fallback)
        q = self.query
        action, ticket = q.get("action"), q.get("ticket") or ""
        dev.events.append(f"sptest:{action}")
        if not ticket.isdigit() or int(ticket) > 999:   # (as a real one: HTTP 400)
            return self._send(400, {"error": 'Invalid "ticket" parameter'})
        ticket = int(ticket)
        if action == "remote":
            if q.get("target") != SPEEDTEST_PEER or q.get("port") != "80":
                return self._send(200, {"status": 1,
                                        "message": "Unable to connect to remote device"})
            if (q.get("login"), q.get("passwd")) != (dev.user, dev.password):
                return self._send(200, {"status": 1, "message": "Invalid credentials"})
            return self._send(200, {"status": 0, "message": "Success."})
        if action == "start":
            dev.speedtest = {"ticket": ticket, "start": time.time(),
                             "duration": int(q.get("duration") or 30),
                             "direction": q.get("direction") or "dx"}
            return self._send(200, {"status": 0, "message": "Success."})
        if action == "status":
            return self._send(200, dev.speedtest_status(ticket))
        if action == "stop":
            dev.speedtest = None
            return self._send(200, {"status": 0})
        return self._send(200, {"status": 1, "message": "Unknown action"})

    def _v8_support_cgi(self, dev: Device):
        # "Download Support Info"
        return self._send(200, dev.support_file(), ctype="application/force-download",
                          headers=[("Content-Disposition",
                                    f'attachment; filename="WA-{MAC.lower()}.sup"')])

    def _v8_stakick_cgi(self, dev: Device):
        # "Kick" and "Kick All" of the stations
        mac = (self.form.get("staid") or "").upper()
        if self.form.get("staif") != "ath0" or not mac:
            return self._send(200, {"ok": False, "error": "Invalid request"})
        for sta in dev.v8_stations():
            if mac in (sta["mac"], "FF:FF:FF:FF:FF:FF"):
                dev.kicked_until[sta["mac"]] = time.time() + 3
        dev.events.append(f"kick:{mac}")
        return self._send(200, {"ok": True})

    def _v8_api_unms_enable(self, dev: Device):
        # the UISP switch of the web's header
        key = self.form.get("unms_key") or ""
        if not key:
            return self._send(200, {"ok": False, "error": "Invalid key"})
        for c in (dev.saved, dev.system, dev.running):
            c["unms.status"], c["unms.uri"] = "enabled", key
        # (a simulated controller is reached at uisp.example.com; any other, never)
        dev.unms_state = 2 if "//uisp.example.com" in key else 1
        dev.events.append("unms-enabled")
        return self._send(200, {"ok": True})

    def _v8_pingtest_action_cgi(self, dev: Device):
        # Tools → Ping: one ping each
        ip = (self.form.get("ip_addr") or "").strip()
        dev.events.append(f"ping:{ip}")
        return self._send(200, dev.ping_answer(ip), "text/plain")

    def _v8_api_traceroute(self, dev: Device):
        # Tools → Traceroute: one hop each
        target = (self.form.get("target") or "").strip()
        dev.events.append(f"trace:{target}")
        return self._send(200, dev.trace_hop(target, int(self.form.get("hop") or 1),
                                             self.form.get("resolve") == "yes"))

    def _v8_discovery_cgi(self, dev: Device):
        # Tools → Discovery: what the antenna finds
        dev.events.append(f"discovery:{self.form.get('duration')}")
        if self.form.get("discover") != "y":
            return self._send(200, {"devices": []})
        return self._send(200, {"devices": dev.discovered()})

    def _v8_api_warnings_dismiss(self, dev: Device):
        # "Dismiss" of the emergency notice
        dev.emerg_reason = -1
        dev.events.append("warnings-dismissed")
        return self._send(200, {"ok": True})  # (its web does not read the answer)

    def _v8_api_unms_disable(self, dev: Device):
        for c in (dev.saved, dev.system, dev.running):
            c["unms.status"] = "disabled"
        dev.unms_state = 0
        dev.events.append("unms-disabled")
        return self._send(200, {"ok": True})

    def _v8_fwupl_cgi(self, dev: Device):
        # "Upload Firmware"
        return self._send(200, dev.check_firmware(self.files.get("fwfile") or b""))

    def _v8_fwflash_cgi(self, dev: Device):
        if self.form.get("discard") == "1":
            dev.fw_pending, dev.fw_third_party = None, False
            dev.events.append("fw-discarded")
            return self._send(200, {"code": 0})
        if self.form.get("do_update") == "1" and dev.fw_pending:
            if dev.flash_delay:            # (it writes it while its web still answers)
                timer = threading.Timer(dev.flash_delay, dev.flash_firmware)
                timer.daemon = True
                timer.start()
            else:
                dev.flash_firmware()
            return self._send(200, {"code": 0})
        return self._send(200, {"code": 1, "message": "No firmware to update"})

    def _v8_api_fw_update_check(self, dev: Device):
        dev.events.append("fw-check" + ("-forced" if self.form.get("force") == "yes" else ""))
        if dev.fw_check_error:                # (as a NanoStation 5AC without internet)
            return self._send(200, {"status": 0, "error": "connection failed",
                                    "error_details": "host or service not provided, or "
                                                     "not known"})
        if not dev.fw_available or dev.fw_available.split(".", 1)[-1] == dev.fw_version:
            return self._send(200, {"update": False})    # (nothing newer than it runs)
        host, port = self.server.server_address[:2]
        return self._send(200, {"date": "20261001", "version": dev.fw_available,
                                "update": True, "security": "",
                                "changelog": f"http://{host}:{port}/changelog.md"})

    def _v8_api_fw_download(self, dev: Device):
        if dev.fw_download_seconds:
            dev.fw_download_started = time.time()
            dev.lock.release()                    # (meanwhile it answers its progress)
            try:
                time.sleep(dev.fw_download_seconds)
            finally:
                dev.lock.acquire()
            dev.fw_download_started = None
        dev.fw_pending, dev.fw_third_party = dev.fw_available, False
        dev.events.append("fw-downloaded")
        return self._send(200, {"ok": True})

    def _v8_api_fw_download_progress(self, dev: Device):
        since = dev.fw_download_started
        if since is None:
            return self._send(200, {"progress": 100})
        dev.events.append("fw-download-progress")
        return self._send(200, {"progress": min(99, int((time.time() - since) * 100
                                                        / dev.fw_download_seconds))})

    def _v8_api_fw_download_eula(self, dev: Device):
        return self._send(200, "<h1>License</h1><p>Simulated license.</p>")

    def _v8_cfgupl_cgi(self, dev: Device):
        # "Upload Configuration"
        if not dev.upload_backup(self.files.get("cfgfile") or b""):
            return self._send(200, {"code": 1, "message": "Invalid configuration file"})
        return self._send(200, {"ok": True, "code": 0})       # (as a NanoStation 5AC answers)

    def _v8_reset_cgi(self, dev: Device):
        # "Reset to Factory Defaults"
        dev.factory_reset()
        return self._send(200, {"ok": True})

    def _v8_pwd_cgi(self, dev: Device):
        # the "Change Password" dialog
        if self.form.get("change") == "yes" and self.form.get("ro") == "1" \
                and self.form.get("pwd"):     # (the read-only one: no current one)
            dev.set_ro_password(self.form["pwd"])
            return self._send(200, {"success": True})
        if self.form.get("change") != "yes" or self.form.get("ro") != "0" \
                or self.form.get("oldPwd") != dev.password:
            return self._send(200, {"success": False})
        dev.set_password(self.form.get("pwd") or "")
        return self._send(200, {"success": True})

    def _v8_api_provmode(self, dev: Device):
        dev.management_radio(self.form["action"] == "start")
        return self._send(200, {"ok": True})

    def _v8_api_pppoe_restart(self, dev: Device):
        dev.pppoe_restart()
        return self._send(200, {"ok": True})

    def _v8_api_regdomain_country(self, dev: Device):
        if _cc(self.form.get("ccode")) not in COUNTRIES:
            return self._send(200, {"error": "Invalid country code"})
        dev.change_country(_cc(self.form.get("ccode")))
        return self._send(200, {"ok": True})

    def _v8_writecfg_cgi(self, dev: Device):
        new = parse_cfg(self.form.get("cfgData") or "")
        if new.get("radio.status") != "enabled":
            return self._send(200, {"code": 1, "error": "Invalid configuration"})
        for k, v in dev.system.items():       # like the web: if it is missing, it is kept
            if PASSWORD_RE.match(k) and k not in new:
                new[k] = v
        dev.system, dev.running = dict(new), dict(new)
        if self.form.get("testmode") == "yes":
            dev.test_until = time.time() + dev.restart + dev.test_secs
            dev.events.append("test-start")
        else:
            dev.saved, dev.test_until = dict(new), None
            dev.events.append("saved")
        dev.soft_restart()
        # (as a real 8.7.25, emulated: how it restarts, a quick one)
        return self._send(200, {"ok": True, "fast_restart": True, "code": 0})

    def _v8_apply_cgi(self, dev: Device):
        if dev.test_until:
            dev.saved, dev.test_until = dict(dev.running), None
            dev.events.append("test-confirmed")
        elif dev.backup_loaded:
            dev.apply_backup()
        return self._send(200, {"code": 0})

    def _v8_discard_cgi(self, dev: Device):
        if self.form.get("testmode") == "yes" and dev.test_until:
            dev.system, dev.running = dict(dev.saved), dict(dev.saved)
            dev.test_until = None
            dev.soft_restart()
            dev.events.append("test-discarded")
        elif self.form.get("testmode") != "yes" and dev.backup_loaded:
            dev.system, dev.backup_loaded = dict(dev.running), False
            if dev.setup_before_backup:      # (from its first setup: it waits for it again)
                dev.setup_pending, dev.factory_password = dev.setup_before_backup
                dev.setup_before_backup = None
            dev.events.append("backup-discarded")
            return self._send(200, {"ok": True, "fast_restart": True, "code": 0})
        return self._send(200, {"code": 0})

    def _v8_reboot_cgi(self, dev: Device):
        self._reboot()
        return self._send(200, {"ok": True})

    V8_OPEN = {
        "/poll.cgi": _v8_poll_cgi,
        "/": _v8_root,
        "/api/info/public": _v8_api_info_public,
        "/api/auth": _v8_api_auth,
        "/changelog.md": _v8_changelog_md,
    }
    V8_ANY = {
        "/api/warnings": _v8_api_warnings,
        "/status.cgi": _v8_status_cgi,
        "/airviewdata.cgi": _v8_airviewdata_cgi,
        "/getcfg.cgi": _v8_getcfg_cgi,
        "/cfg.cgi": _v8_cfg_cgi,
        "/test_mode.cgi": _v8_test_mode_cgi,
        "/api/regdomain": _v8_api_regdomain,
        "/api/firewall/user-rules": _v8_api_firewall_user_rules,
        "/api/firewall/port-forward": _v8_api_firewall_port_forward,
        "/api/dhcp-server/leases": _v8_api_dhcp_server_leases,
        "/api/pppoe/info": _v8_api_pppoe_info,
        "/survey.json.cgi": _v8_survey_json_cgi,
        "/ipscan.cgi": _v8_ipscan_cgi,
        "/sptest_action.cgi": _v8_sptest_action_cgi,
        "/support.cgi": _v8_support_cgi,
    }
    V8_POST = {
        "/stakick.cgi": _v8_stakick_cgi,
        "/api/unms/enable": _v8_api_unms_enable,
        "/pingtest_action.cgi": _v8_pingtest_action_cgi,
        "/api/traceroute": _v8_api_traceroute,
        "/discovery.cgi": _v8_discovery_cgi,
        "/api/warnings/dismiss": _v8_api_warnings_dismiss,
        "/api/unms/disable": _v8_api_unms_disable,
        "/fwupl.cgi": _v8_fwupl_cgi,
        "/fwflash.cgi": _v8_fwflash_cgi,
        "/api/fw/update-check": _v8_api_fw_update_check,
        "/api/fw/download": _v8_api_fw_download,
        "/api/fw/download-progress": _v8_api_fw_download_progress,
        "/api/fw/download-eula": _v8_api_fw_download_eula,
        "/cfgupl.cgi": _v8_cfgupl_cgi,
        "/reset.cgi": _v8_reset_cgi,
        "/pwd.cgi": _v8_pwd_cgi,
        "/api/provmode": _v8_api_provmode,
        "/api/pppoe/restart": _v8_api_pppoe_restart,
        "/api/regdomain/country": _v8_api_regdomain_country,
        "/writecfg.cgi": _v8_writecfg_cgi,
        "/apply.cgi": _v8_apply_cgi,
        "/discard.cgi": _v8_discard_cgi,
        "/reboot.cgi": _v8_reboot_cgi,
    }

