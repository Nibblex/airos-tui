"""App: the wireless security, the MAC access list and the backup SSID."""
from __future__ import annotations

from ..core.i18n import tr
from ..core.config import _int
from ..device.wireless import MAC_RE, ssid_problem, V6_SECURITY, _v_wpa_psk


class WirelessSecurity:
    """Security, MAC access list and backup SSID of the Wireless menu."""

    # -- security --

    def security_menu(self) -> None:
        last = "t"
        while True:
            st = self._wstate()
            v8, ap = st["flavor"] == "v8", st["ap"]
            wpa = st["security"] == "wpa2" if v8 else st["security"].startswith("wpa")
            items = [("t", tr("Security: {security}", security=self._w_security_text(st)))]
            if wpa and st["wpa_auth"] == "WPA-PSK":
                items.append(("k", tr("WPA key: {key}", key=st["psk"])))
            elif wpa and ap:
                items.append(("r", tr("Authentication server: {server}", server=(
                    f"{st['radius_ip'] or '?'}:{st['radius_port']}"))))
                items.append(("c", tr("Accounting server: {server}", server=(
                    f"{st['acct_ip'] or '?'}:{st['acct_port']}" if st["acct_on"]
                    else tr("disabled")))))
            elif wpa:
                items.append(("u", tr("User: {user}", user=st["eap_user"] or "?")))
            if v8 and ap:
                items.append(("d", tr("Deauthentication protection: {state}",
                                      state=self._w_onoff(st["deauth"]))))
            choice = self.ui.menu(tr("Security"), tr("Wireless security, as in the web."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "t":
                self._security_type(st)
            elif choice == "k":
                self._security_psk(st)
            elif choice == "r":
                self._security_radius(st, "radius")
            elif choice == "c":
                self._security_acct(st)
            elif choice == "u":
                self._security_eap(st)
            elif choice == "d":
                question = (tr("Disable the deauthentication protection?") if st["deauth"]
                            else tr("Enable the deauthentication protection?\n\nThe access "
                                    "point ignores forged deauthentication frames (802.11w "
                                    "style), which some attacks use to drop the stations."))
                if self.ui.ask(tr("Security"), question, default_no=st["deauth"]):
                    self._wchange(deauth=not st["deauth"])

    def _security_type(self, st: dict) -> None:
        v8 = st["flavor"] == "v8"
        wpa = "wpa2" if v8 else "wpa2aes"
        options = [("none", tr("None (open network)")),
                   ("psk", tr("WPA2 personal: a shared key (PSK)")),
                   ("eap", tr("WPA2 enterprise: a RADIUS server") if st["ap"]
                    else tr("WPA2 enterprise: a user and a password (EAP)"))]
        on = st["security"] == "wpa2" if v8 else st["security"].startswith("wpa")
        cur = "none" if not on else ("psk" if st["wpa_auth"] == "WPA-PSK" else "eap")
        note = ""
        if not v8 and st["security"] not in ("none", "wpa2aes"):
            note = tr("Current: {security}. The web no longer offers it (weak security); "
                      "choose one of these.", security=V6_SECURITY.get(st["security"],
                                                                     st["security"])) + "\n\n"
        choice = self.ui.menu(tr("Security"), note + tr("Security of the wireless link:"),
                              options, default=cur)
        # (the same, but on airOS 6 its WPA2 with TKIP, which its web no longer offers: to AES)
        if choice is None or (choice == cur and st["security"] in ("none", wpa)):
            return
        if choice == "none":
            if self.ui.ask(tr("Security"), tr("Leave the wireless network without security?"
                                              "\n\nAnyone in range could connect to it."),
                           default_no=True):
                self._wchange(security="none")
            return
        changes = {"security": wpa, "wpa_auth": "WPA-PSK" if choice == "psk" else "WPA-EAP"}
        if choice == "psk":
            key = self._ask_psk(st["psk"])
            if key is None:
                return
            changes["psk"] = key
        elif st["ap"]:
            server = self._ask_radius(st, "radius", tr("Authentication server"))
            if server is None:
                return
            changes.update(server)
        else:
            creds = self._ask_eap(st)
            if creds is None:
                return
            changes.update(creds)
        self._wchange(**changes)

    def _ask_psk(self, current: str) -> str | None:
        return self._ask_valid(tr("WPA key"), tr("Shared key (PSK), from 8 to 63 printable "
                                                 "characters:"), current,
                               lambda text: (text, _v_wpa_psk(text)))

    def _security_psk(self, st: dict) -> None:
        key = self._ask_psk(st["psk"])
        if key is not None and key != st["psk"]:
            self._wchange(psk=key)

    def _ask_radius(self, st: dict, prefix: str, label: str) -> dict | None:
        ip = self._ask_ip(label, tr("IP of the RADIUS server:"), st[f"{prefix}_ip"] or "")
        if ip is None:
            return None
        default = 1812 if prefix == "radius" else 1813
        port = self._ask_int(label, tr("Port:"), _int(st[f"{prefix}_port"]) or default, 1,
                             65535)
        if port is None:
            return None
        secret = self.ui.inputbox(label, tr("Shared secret (1 to 32 printable characters):"),
                                  st[f"{prefix}_secret"] or "")
        if secret is None:
            return None
        return {f"{prefix}_ip": ip, f"{prefix}_port": port if st["flavor"] == "v8"
                else str(port), f"{prefix}_secret": secret}

    def _security_radius(self, st: dict, prefix: str) -> None:
        server = self._ask_radius(st, prefix, tr("Authentication server"))
        if server:
            self._wchange(**server)

    def _security_acct(self, st: dict) -> None:
        if st["acct_on"] and not self._change_or_disable(
                tr("Accounting server"), tr("Accounting (RADIUS) is enabled."), acct_on=False):
            return
        server = self._ask_radius(st, "acct", tr("Accounting server"))
        if server:
            self._wchange(acct_on=True, **server)

    def _ask_eap(self, st: dict, prefix: str = "eap") -> dict | None:
        title = tr("WPA2 enterprise")
        changes: dict = {}
        if st["flavor"] == "v6" and prefix == "eap":
            method = self.ui.menu(title, tr("EAP method:"), [("TTLS", "EAP-TTLS"),
                                                              ("PEAP", "EAP-PEAP")],
                                  default=st["eap_method"] or "TTLS")
            if method is None:
                return None
            changes["eap_method"] = method
        for key, prompt in ((f"{prefix}_anon", tr("Anonymous identity (optional):")),
                            (f"{prefix}_user", tr("User name:")),
                            (f"{prefix}_pass", tr("Password:"))):
            value = self.ui.inputbox(title, prompt, st[key] or "")
            if value is None:
                return None
            changes[key] = value
        return changes

    def _security_eap(self, st: dict) -> None:
        creds = self._ask_eap(st)
        if creds:
            self._wchange(**creds)

    # -- MAC ACL --

    def acl_menu(self) -> None:
        last = "s"
        while True:
            st = self._wstate()
            rules = st["acl_rules"]
            items = [("s", tr("MAC access control: {state}", state=self._w_onoff(st["acl_on"]))),
                     ("p", tr("Policy: {policy}", policy=tr("deny the listed MACs")
                              if st["acl_policy"] == "deny"
                              else tr("allow only the listed MACs"))),
                     ("a", tr("Add a MAC"))]
            for i, r in enumerate(rules, 1):
                mark = "[x]" if r["on"] else "[ ]"
                items.append((str(i), f"{mark} {r['mac']}  {r.get('comment') or ''}".rstrip()))
            choice = self.ui.menu(tr("MAC access control"),
                                  tr("Which stations may connect, by their MAC. [x] = rule "
                                     "enabled."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "s":
                question = (tr("Disable the MAC access control?") if st["acl_on"] else
                            tr("Enable the MAC access control?\n\nWith \"allow\", a station "
                               "that is not in the list will not be able to connect: check "
                               "that the list is complete."))
                if self.ui.ask(tr("MAC access control"), question, default_no=not st["acl_on"]):
                    self._wchange(acl_on=not st["acl_on"])
            elif choice == "p":
                choice = self.ui.menu(tr("Policy"), tr("What to do with the MACs of the list:"),
                                      [("allow", tr("allow only the listed MACs")),
                                       ("deny", tr("deny the listed MACs"))],
                                      default=st["acl_policy"])
                if choice and choice != st["acl_policy"]:
                    self._wchange(acl_policy=choice)
            elif choice == "a":
                self._acl_add(st)
            else:
                self._acl_rule(st, int(choice) - 1)

    def _rules(self, st: dict) -> list[dict]:
        return [{"mac": r["mac"], "comment": r.get("comment") or "", "on": r["on"]}
                for r in st["acl_rules"]]

    def _acl_add(self, st: dict) -> None:
        mac = self.ui.inputbox(tr("Add a MAC"), tr("MAC of the station (like "
                                                   "00:11:22:33:44:55):"), "")
        if mac is None:
            return
        mac = mac.strip()
        if st["flavor"] == "v6":
            mac = mac.upper()                     # what macacl.cgi does
        if not MAC_RE.match(mac):
            self.ui.msgbox(tr("Invalid value"), tr("The MAC must be like 00:11:22:33:44:55."))
            return
        comment = self.ui.inputbox(tr("Add a MAC"), tr("Comment (optional):"), "")
        if comment is None:
            return
        self._wchange(acl_rules=self._rules(st) + [{"mac": mac, "comment": comment.strip(),
                                                    "on": True}])

    def _acl_rule(self, st: dict, i: int) -> None:
        rules = self._rules(st)
        if not 0 <= i < len(rules):
            return
        rule = rules[i]
        name = rule.get("mac") or "?"
        choice = self.ui.menu(name, rule.get("comment") or tr("(no comment)"),
                              [("t", tr("Disable the rule") if rule["on"]
                                else tr("Enable the rule")),
                               ("c", tr("Change the comment")), ("d", tr("Delete the rule"))])
        if choice == "t":
            rule["on"] = not rule["on"]
        elif choice == "c":
            comment = self.ui.inputbox(name, tr("Comment:"), rule.get("comment") or "")
            if comment is None:
                return
            rule["comment"] = comment.strip()
        elif choice == "d":
            del rules[i]
        else:
            return
        self._wchange(acl_rules=rules)

    # -- backup SSID (airOS 8 stations) --

    def backup_menu(self) -> None:
        last = "s"
        while True:
            st = self._wstate()
            items = [("s", tr("Backup SSID: {state}", state=self._w_onoff(st["bk_on"])))]
            if st["bk_on"]:
                wpa = st["bk_security"] == "wpa2"
                items += [("n", "SSID: " + (st["bk_ssid"] or "?")),
                          ("m", tr("Lock to AP MAC: {mac}", mac=st["bk_mac"] or tr("no"))),
                          ("x", tr("Security: {security}", security=(
                              tr("none") if not wpa else "WPA2 " + (
                                  tr("personal (PSK)") if st["bk_wpa_auth"] == "WPA-PSK"
                                  else tr("enterprise (EAP, RADIUS)")))))]
            choice = self.ui.menu(tr("Backup SSID"), tr("A second access point the station "
                                                        "connects to when it cannot reach "
                                                        "the first one."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "s":
                if st["bk_on"]:
                    if self.ui.ask(tr("Backup SSID"), tr("Disable the backup SSID?")):
                        self._wchange(bk_on=False)
                    continue
                ssid = self.ui.inputbox(tr("Backup SSID"), tr("SSID of the backup access "
                                                              "point:"), st["bk_ssid"])
                if ssid is not None:
                    problem = ssid_problem(ssid, self.dev.flavor)
                    if problem:
                        self.ui.msgbox(tr("Invalid value"), problem)
                        continue
                    self._wchange(bk_on=True, bk_ssid=ssid)
            elif choice == "n":
                ssid = self.ui.inputbox(tr("Backup SSID"), "SSID:", st["bk_ssid"])
                if ssid is not None and ssid != st["bk_ssid"]:
                    problem = ssid_problem(ssid, self.dev.flavor)
                    if problem:
                        self.ui.msgbox(tr("Invalid value"), problem)
                        continue
                    self._wchange(bk_ssid=ssid)
            elif choice == "m":
                mac = self.ui.inputbox(tr("Backup SSID"), tr("MAC of the backup access point "
                                                             "(empty = any):"), st["bk_mac"])
                if mac is not None and mac.strip() != st["bk_mac"]:
                    self._wchange(bk_mac=mac.strip())
            elif choice == "x":
                self._backup_security(st)

    def _backup_security(self, st: dict) -> None:
        wpa = st["bk_security"] == "wpa2"
        cur = "none" if not wpa else ("psk" if st["bk_wpa_auth"] == "WPA-PSK" else "eap")
        choice = self.ui.menu(tr("Security"), tr("Security of the backup access point:"),
                              [("none", tr("None (open network)")),
                               ("psk", tr("WPA2 personal: a shared key (PSK)")),
                               ("eap", tr("WPA2 enterprise: a user and a password (EAP)"))],
                              default=cur)
        if choice is None:
            return
        if choice == "none":
            if cur != "none":
                self._wchange(bk_security="none")
            return
        changes = {"bk_security": "wpa2",
                   "bk_wpa_auth": "WPA-PSK" if choice == "psk" else "WPA-EAP"}
        if choice == "psk":
            key = self._ask_psk(st["bk_psk"])
            if key is None:
                return
            changes["bk_psk"] = key
        else:
            creds = self._ask_eap(st, "bk")
            if creds is None:
                return
            changes.update(creds)
        self._wchange(**changes)
