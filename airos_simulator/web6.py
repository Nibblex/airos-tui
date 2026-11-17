"""airOS 6's web: the paths airos-tui uses, as the real 6.3 web answers them (cookie
session, a hidden token in the POSTs, a page per section)."""
from __future__ import annotations

import secrets
import time
from .data import BOARD, MAC, PPPINFO_LABELS, _countdown, country_options, regdomain_v6
from .cfgfile import cfg_text, parse_cfg
from .device import Device


LOGIN_V6 = """<html><head><title>Login</title><script>
var globals = { first_login : false, postback : false };
</script></head><body><form method="post" action="/login.cgi">
<input name="username"><input name="password" type="password"></form>%s</body></html>"""


class Web6:
    # -- airOS 6 --------------------------------------------------------------

    def _v6(self, method, path):
        dev: Device = self.server.dev
        set_cookie = []
        if not self.sid or self.sid not in dev.sessions:
            self.sid = secrets.token_hex(16)
            dev.sessions[self.sid] = {"auth": False, "token": secrets.token_hex(16)}
            set_cookie = [("Set-Cookie", f"AIROS_{MAC}={self.sid}; Path=/")]
            if path not in ("/login.cgi", "/poll.cgi"):
                return self._send(302, "", headers=set_cookie
                                  + [("Location", f"/cookiechecker?uri={path}")])
        sess = dev.sessions[self.sid]
        if path == "/poll.cgi":
            return self._send(200, "", headers=set_cookie)
        if path == "/login.cgi":
            if method == "POST":
                if (self.form.get("username") == dev.user
                        and self.form.get("password") == dev.password):
                    sess["auth"] = True
                    return self._send(302, "", headers=[
                        ("Location", self.form.get("uri") or "/index.cgi")])
                return self._send(200, LOGIN_V6 % "<div>Invalid credentials.</div>")
            return self._send(200, LOGIN_V6 % "", headers=set_cookie)
        if not sess["auth"]:
            return self._send(302, "", headers=[("Location", f"/login.cgi?uri={path}")])
        if path in ("/", "/index.cgi"):
            return self._send(200, "<html>airOS 6 (simulated)</html>")
        if path == "/status.cgi":
            return self._send(200, dev.status())
        if path == "/cfg.cgi":
            return self._send(200, cfg_text(dev.system), "application/force-download")
        if path == "/getboardinfo.sh":
            return self._send(200, "".join(f"{k}={v}\n" for k, v in BOARD[6].items()),
                              "text/plain")
        if path == "/regdomain_by_country.cgi":
            bw = int(self.query.get("chanbw") or 20)
            cc = self.query.get("country") or dev.running.get("radio.countrycode") or "484"
            return self._send(200, regdomain_v6(bw, cc))
        if path == "/cc_change.cgi":
            return self._send(200, '<select name="country_select" id="country_select">'
                              '<option value="0">Select New Country</option>'
                              + country_options() + "</select>")
        if path == "/system.cgi":
            error = ""
            if method == "POST" and self.form.get("token") == sess["token"]:
                if self.form.get("cfgupload") and "cfgfile" in self.files:
                    new = parse_cfg(self.files["cfgfile"].decode("utf-8", "replace"))
                    radio = (new.get("radio.status"), new.get("radio.1.status"))
                    if radio != ("enabled", "enabled"):
                        error = "Invalid configuration file structure"
                    else:
                        dev.system, dev.modified = new, True
            return self._send(200, self._system_page(sess, error))
        # apply.cgi, discard.cgi and pppinfo.cgi's restart act, as 6.3's, only on a POST with
        # the session's token (its web's buttons): a GET, or no token, does nothing.
        posted = method == "POST" and self.form.get("token") == sess["token"]
        if path == "/apply.cgi" and not posted:
            return self._send(200, "")
        if path == "/discard.cgi" and not posted:
            return self._send(200, "OK")
        if path == "/apply.cgi":
            if self.form.get("testmode"):
                dev.test_until = time.time() + dev.restart + dev.test_secs
                dev.apply_running(dev.system)
                dev.events.append("test-start")
            elif dev.test_until:
                dev.saved, dev.test_until, dev.modified = dict(dev.running), None, False
                dev.events.append("test-confirmed")
            else:
                if dev.running != dev.system:
                    dev.apply_running(dev.system)
                dev.saved, dev.modified = dict(dev.system), False
                dev.events.append("saved")
            return self._send(200, "")
        if path == "/discard.cgi":
            dev.system, dev.modified = dict(dev.saved), False
            if self.form.get("testmode"):
                dev.running, dev.test_until = dict(dev.saved), None
                dev.soft_restart()
                dev.events.append("test-discarded")
            return self._send(200, "OK")
        if path == "/reboot.cgi":
            if method == "POST" and self.form.get("token") == sess["token"]:
                self._reboot()
                return self._send(200, "<html>Rebooting...</html>")
            return self._send(200, f'<form method="POST"><input type="hidden" name="token" '
                                   f'value="{sess["token"]}" /></form>')
        if path == "/pppinfo.cgi":
            if posted and self.form.get("action") == "restartppp":   # killall pppd; sleep(2)
                dev.pppoe_restart()
            return self._send(200, self._pppinfo_page(sess))
        return self._send(404, "not found")

    def _pppinfo_page(self, sess) -> str:
        """The same page airOS 6 builds with pppinfo.cgi + lib/ppp_info.tmpl."""
        dev: Device = self.server.dev
        ppp = dev.pppoe_state()
        lab = PPPINFO_LABELS.get(dev.ui_lang, PPPINFO_LABELS["en"])
        restart = ""
        if ppp.get("error") == "PPPoE is not enabled.":
            body = f'<tr>\n<td valign="top" colspan="2">{ppp["error"]}</td>\n</tr>\n'
        elif "error" in ppp:
            body = '<tr>\n<td valign="top" colspan="2">Not Connected</td>\n</tr>\n'
        else:
            rows = list(zip(lab[:4], (ppp["name"], ppp["iplocal"], ppp["ipremote"], ppp["dns1"])))
            if ppp["dns2"]:
                rows.append((lab[4], ppp["dns2"]))
            first = "".join(f'<tr><td class="f">{k}:</td><td>{v}</td></tr>\n' for k, v in rows)
            body = ('<tr>\n<td valign="top">\n<table>\n' + first + '</table>\n'
                    '</td><td valign="top"><table>\n'
                    f'<tr><td class="f">{lab[5]}:</td><td>{_countdown(ppp["duration"])}</td></tr>\n'
                    f'<tr><td class="f">{lab[6]}:</td>\n\t<td>15628 (15.3 KiB)</td>\n</tr>\n'
                    f'<tr><td class="f">{lab[7]}:</td>\n\t<td>10677 (10.4 KiB)</td>\n</tr>\n'
                    '</table></td>\n</tr>\n')
        if "not enabled" not in ppp.get("error", ""):
            restart = ('<input type="button" onClick="return refresh(\'/pppinfo.cgi?action='
                       'restartppp\');" value=" Restart Service ">')
        return ('<html><head><title>PPPoE Information</title></head><body class="popup">\n'
                '<form enctype="multipart/form-data" action="/pppinfo.cgi" method="POST">\n'
                f'<input type="hidden" name="token" value="{sess["token"]}" />\n'
                '<table cellspacing="0" cellpadding="0" align="center">\n<tr><td>\n'
                '<table cellspacing="0" cellpadding="0" class="listhead" id="pppinfo">\n'
                '  <tr><th colspan="2">PPPoE Information</th></tr>\n' + body +
                '</table>\n</td></tr>\n<tr><td>&nbsp;</td><td class="change">\n' + restart +
                '<input type="button" onClick="return refresh(\'/pppinfo.cgi\');" '
                'value=" Refresh ">\n</td>\n</tr></table>\n</form>\n</body>\n</html>\n')

    def _system_page(self, sess, error) -> str:
        dev: Device = self.server.dev
        mod = ""
        if dev.modified or dev.test_until:
            testmode, left = ("on", dev.test_secs) if dev.test_until else ("", 0)
            mod = f"<script>var testmode = '{testmode}';\nvar testtime_left = {left};</script>"
        return (f"<html><body><script>var radio_caps = {dev.radio_caps};</script>{mod}"
                f"<div id=\"error\">{error}</div>"
                f'<form id="sys_form" enctype="multipart/form-data" action="system.cgi" '
                f'method="POST">\n<input type="hidden" name="token" value="{sess["token"]}" />'
                f"</form></body></html>")
