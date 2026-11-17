"""App: the Services menu, as the web's Services page saves its settings (Ubiquiti discovery,
CDP, LLDP and, on airOS 8, the ping watchdog, SNMP, the web server, SSH with its authorized
keys, Telnet and the system log), and UISP, as the switch in its web's header."""
from __future__ import annotations

import os
import re

from ..core.i18n import tr
from ..core.config import _merge
from ..device.services import (access_offered, add_ssh_keys, discovery_settings,
                               dmz_mgmt_exceptions, _DMZ_SERVICES, lldp_enabled, lldp_offered,
                               MAX_SSH_KEYS, parse_ssh_keys, plan_discovery, plan_snmp, plan_ssh,
                               plan_ssh_keys, plan_syslog, plan_telnet, plan_watchdog, plan_web,
                               PWDOG_DELAY, PWDOG_FAILURES, service_port, service_port_problem,
                               services_offered, snmp_problem, snmp_settings, SSH_KEY_FILE_MAX,
                               ssh_key_shown, ssh_keys, ssh_settings, syslog_problem,
                               syslog_settings, telnet_settings, unms_key, unms_key_problem,
                               unms_key_shown, unms_on, unms_state, watchdog_problem,
                               watchdog_settings, web_settings)
from .ui import changed_mark, field_marks


class ServicesScreens:
    """What changes here is prepared as the rest (test mode included), except UISP, which its
    web sends to the device at once."""

    def _accept_service(self, up: dict, now: dict | None = None, before: dict | None = None,
                        off: str | None = None, prefix: str = "") -> bool:
        """A service's change with the DMZ's management ports regenerated, as its webs do on
        saving (its ports, and whether it is on, are in that list); where its settings are the
        device's again, its own values (_back_to_device), and then its list too."""
        if now is not None:
            up = self._back_to_device(up, now, before, off, prefix)
        cand = _merge(self.working.copy(), up)
        if any(k.split(".", 1)[0] in _DMZ_SERVICES for k, _, _ in self.original.diff(cand)):
            up = dict(up, **dmz_mgmt_exceptions(cand, self.dev.flavor))
        else:
            dmz = re.compile(r"^iptables\.sys\.dmz\.\d+\.except\.")
            up = dict(up, **{k: self.original.get(k)
                             for k in set(cand.keys()) | set(self.original.keys())
                             if dmz.match(k)})
        return self._accept(up)

    def services_menu(self) -> None:
        flavor = self.dev.flavor
        board = self.limits.board if self.limits else {}
        disc_ok = services_offered(flavor, board)        # (not on Gen3 boards)
        lldp = disc_ok and lldp_offered(flavor, self.dev.fw, self.original)
        offered = access_offered(flavor, board)
        last = "d" if disc_ok else "w"
        while True:
            items, text = self._services_items(disc_ok, lldp, offered)
            choice = self.ui.menu(tr("Services"), text, items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "d" and disc_ok:
                self._toggle_discovery()
            elif choice == "c" and disc_ok:
                self._toggle_cdp()
            elif choice == "l" and lldp:
                self._toggle_lldp()
            elif choice == "w" and flavor == "v8":
                self.watchdog_menu()
            elif choice == "u" and flavor == "v8":
                self.uisp_menu()
            elif choice in ("a", "h", "s", "t", "g"):
                {"a": self.snmp_menu, "h": self.web_server_menu, "s": self.ssh_menu,
                 "t": self.telnet_menu, "g": self.syslog_menu}[choice]()

    def _services_items(self, disc_ok: bool, lldp: bool, offered) -> tuple[list, str]:
        """The rows of the Services menu ("* " where it is not the device's) and its text."""
        flavor = self.dev.flavor
        items = []
        if disc_ok:
            disc, cdp = discovery_settings(self.working, flavor)
            odisc, ocdp = discovery_settings(self.original, flavor)
            items += [("d", ("* " if disc != odisc else "")
                       + (tr("Ubiquiti discovery: enabled") if disc
                          else tr("Ubiquiti discovery: disabled"))),
                      ("c", ("* " if cdp != ocdp else "")
                       + (tr("CDP: enabled") if cdp else tr("CDP: disabled")))]
        if lldp:
            on = lldp_enabled(self.working)
            items.append(("l", ("* " if on != lldp_enabled(self.original) else "")
                          + (tr("LLDP: enabled") if on else tr("LLDP: disabled"))))
        if flavor == "v8":
            ws = watchdog_settings(self.working)
            items.append(("w", ("* " if ws != watchdog_settings(self.original) else "")
                          + tr("Ping Watchdog: {state}", state=(
                              tr("enabled") + f" · {ws['host']}" if ws["on"]
                              else tr("disabled")))))
            items += self._access_rows(offered)
            # (its web has it in its header)
            items.append(("u", tr("UISP: {state}", state=(
                tr("enabled") if unms_on(self.original) else tr("disabled"))
                + " · " + unms_state(self.status))))
            text = tr("The Services page of the web: device discovery, the ping watchdog, "
                      "SNMP, its web server, SSH, Telnet and the system log; UISP is the "
                      "switch in its header. * = changed, not applied yet.")
            if lldp:
                text += " " + tr("LLDP is not in the web.")
            if not disc_ok:
                text += " " + tr("On this board (Gen3) its web has no discovery, CDP, SNMP "
                                 "or Telnet.")
        else:
            text = tr("Device discovery, as in the Services page of the web. * = changed, "
                      "not applied yet.")
        return items, text

    def _toggle_discovery(self) -> None:
        """Ubiquiti discovery: on or off, asked first (what turning it off costs)."""
        disc, cdp = discovery_settings(self.working, self.dev.flavor)
        question = (tr("Disable Ubiquiti discovery?\n\nThe antenna will stop answering "
                       "the discovery query (UDP 10001): neither airos-tui --discover nor "
                       'the "Discovery" tool of other Ubiquiti devices will find it. If '
                       "a later change moves it to DHCP or PPPoE, this program will "
                       "only be able to look for it at its fallback IP.") if disc else
                    tr("Enable Ubiquiti discovery?\n\nThe antenna will answer the "
                       "discovery query (UDP 10001) with its name, model, firmware, "
                       "mode, SSID and addresses, without a password: that is how "
                       'airos-tui --discover and the "Discovery" tool of other Ubiquiti '
                       "devices find it."))
        if self.ui.ask(tr("Ubiquiti discovery"), question, default_no=disc):
            self._accept(plan_discovery(self.working, self.dev.flavor, not disc, cdp))

    def _toggle_cdp(self) -> None:
        """CDP: on or off, asked first."""
        disc, cdp = discovery_settings(self.working, self.dev.flavor)
        question = (tr("Disable CDP?\n\nThe antenna will stop announcing itself with "
                       "CDP (Cisco Discovery Protocol): switches and routers will no "
                       "longer list it among their neighbors. It does not affect access "
                       "to the antenna or Ubiquiti discovery.") if cdp else
                    tr("Enable CDP?\n\nThe antenna will announce itself with CDP "
                       "(Cisco Discovery Protocol), so that the switches and routers "
                       "that understand it list it among their neighbors."))
        if self.ui.ask("CDP", question):
            self._accept(plan_discovery(self.working, self.dev.flavor, disc, not cdp))

    def _toggle_lldp(self) -> None:
        """LLDP: on or off, asked first (the web does not show it: the key the firmware
        reads)."""
        on = lldp_enabled(self.working)
        question = (tr("Disable LLDP?\n\nThe antenna will stop using LLDP (Link Layer "
                       "Discovery Protocol, IEEE 802.1AB): switches and routers will no "
                       "longer list it among their neighbors. It does not affect access "
                       "to the antenna or Ubiquiti discovery.\n\nThe web does not show "
                       "this setting: airos-tui changes the key the firmware reads, "
                       "discovery.lldp.status.") if on else
                    tr("Enable LLDP?\n\nThe antenna will announce itself with LLDP (Link "
                       "Layer Discovery Protocol, IEEE 802.1AB), so that the switches "
                       "and routers that understand it list it among their neighbors."
                       "\n\nThe web does not show this setting: airos-tui changes the "
                       "key the firmware reads, discovery.lldp.status."))
        if self.ui.ask("LLDP", question):
            self._accept_switch("discovery.lldp.status", not on,
                                lldp_enabled(self.original))

    # -- Ping Watchdog (airOS 8) --

    def watchdog_menu(self) -> None:
        """The web's Ping Watchdog (Services page, airOS 8): the device pings an address and
        reboots itself when it does not answer. Its fields only while it is on, as there;
        what changes is prepared as the rest (test mode included)."""
        title = tr("Ping Watchdog")
        last = "s"
        while True:
            ws, orig = watchdog_settings(self.working), watchdog_settings(self.original)

            mark = field_marks(ws, orig)
            items = [("s", mark("on") + tr("Ping Watchdog: {state}", state=(
                tr("enabled") if ws["on"] else tr("disabled"))))]
            if ws["on"]:
                items += [("h", mark("host") + tr("IP Address To Ping: {address}",
                                                  address=ws["host"])),
                          ("i", mark("interval") + tr("Ping Interval: {seconds} sec",
                                                      seconds=ws["interval"])),
                          ("d", mark("delay") + tr("Startup Delay: {seconds} sec",
                                                   seconds=ws["delay"])),
                          ("f", mark("failures") + tr("Failure Count To Reboot: {count}",
                                                      count=ws["failures"])),
                          ("p", mark("support") + tr("Save Support Info: {state}", state=(
                              tr("enabled") if ws["support"] else tr("disabled"))))]
            choice = self.ui.menu(title, tr(
                "The device pings an address and, when it goes unanswered a number of times "
                "in a row, reboots itself. As in the Services page of the web. * = changed, "
                "not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "s" and ws["on"]:
                if self.ui.ask(title, tr("Disable the ping watchdog?\n\nThe device will no "
                                         "longer reboot itself when {address} does not "
                                         "answer.", address=ws["host"])):
                    self._watchdog_set(dict(ws, on=False))
            elif choice == "s":
                self._watchdog_on(ws)
            elif choice == "p" and ws["on"]:
                question = (tr("Turn off «Save Support Info»?\n\nThe ping watchdog will "
                               "reboot the device without saving its support info first.")
                            if ws["support"] else
                            tr("Turn on «Save Support Info»?\n\nBefore the ping watchdog "
                               "reboots the device, the device saves its support info (with "
                               "its logs of that moment) in its persistent memory."))
                if self.ui.ask(title, question):
                    self._watchdog_set(dict(ws, support=not ws["support"]))
            elif choice in ("h", "i", "d", "f") and ws["on"]:
                field = {"h": "host", "i": "interval", "d": "delay", "f": "failures"}[choice]
                value = self._ask_watchdog(field, ws)
                if value is not None:
                    self._watchdog_set(dict(ws, **{field: value}))

    def _watchdog_on(self, ws: dict) -> None:
        """Turning it on: the address to ping (its web requires one) and, as its rules apply
        from then on, any other value the device has wrong."""
        new = dict(ws, on=True)
        host = self._ask_watchdog("host", new)
        if host is None:
            return
        new["host"] = host
        for field in ("interval", "delay", "failures"):
            problem = watchdog_problem(field, new[field])
            if problem:
                self.ui.msgbox(tr("Invalid value"), problem)
                value = self._ask_watchdog(field, new)
                if value is None:
                    return
                new[field] = value
        self._watchdog_set(new)

    def _ask_watchdog(self, field: str, ws: dict):
        """One value of the watchdog, with the rules and messages of its web: the address,
        or a whole number. None if cancelled."""
        if field == "host":
            title, prompt = tr("IP Address To Ping"), tr(
                "The device pings this address every {interval} s and reboots itself after "
                "{failures} pings in a row without an answer. Use one that always answers, "
                "such as the gateway's: otherwise the device keeps rebooting.",
                interval=ws["interval"], failures=ws["failures"])
        elif field == "interval":
            title, prompt = tr("Ping Interval"), tr("Seconds between pings:")
        elif field == "delay":
            title, prompt = tr("Startup Delay"), tr(
                "Seconds the device waits after starting up before the first ping (from {low} "
                "to {high}):", low=PWDOG_DELAY[0], high=PWDOG_DELAY[1])
        else:
            title, prompt = tr("Failure Count To Reboot"), tr(
                "Pings in a row without an answer that make the device reboot (from {low} to "
                "{high}):", low=PWDOG_FAILURES[0], high=PWDOG_FAILURES[1])

        def parse(text):
            problem = watchdog_problem(field, text)
            if problem:
                return None, problem
            return (text.strip() if field == "host" else int(text.strip())), None
        return self._ask_valid(title, prompt, ws[field], parse)

    def _watchdog_set(self, ws: dict) -> None:
        """The watchdog prepared as its web saves it; back to what the device has, its keys
        as they were (nothing pending)."""
        if ws == watchdog_settings(self.original):
            keys = {k for k in list(self.original.keys()) + list(self.working.keys())
                    if k.startswith("pwdog.")}
            self._accept({k: self.original.get(k) for k in keys})
        else:
            self._accept(plan_watchdog(ws))

    def uisp_menu(self) -> None:
        """UISP, as the switch in its web's header (airOS 8): management on or off and its
        key, sent at once (POST /api/unms/enable or disable) and saved by the device, without
        test mode; what is pending here stays pending."""
        title = "UISP"
        while True:
            on, key = unms_on(self.original), unms_key(self.original)
            lines = [tr("UISP Management: {state}", state=tr("enabled") if on else tr("disabled")),
                     tr("UISP Key: {key}", key=unms_key_shown(key) if key
                        else tr("Please add UISP key!")),
                     tr("Status: {state}", state=unms_state(self.status)), "",
                     tr("As its web does, a change here is sent at once and the device saves "
                        "it, without test mode.")]
            items = []
            if key:                             # (its switch needs a key)
                items.append(("s", tr("Turn UISP management off") if on
                              else tr("Turn UISP management on")))
            items += [("k", tr("Change Key") if on else tr("Add Key")),
                      ("r", tr("Read the status again"))]
            choice = self.ui.menu(title, "\n".join(lines), items)
            if choice is None:
                return
            if choice == "s":
                self._uisp_send(not on, key)
            elif choice == "k":
                new = self._ask_unms_key()
                if new is not None:
                    self._uisp_send(True, new)
            else:
                self.status = self.dev.status()

    def _ask_unms_key(self) -> str | None:
        """Its web's "UISP Controller" dialog: the key, with its rules (and the NTP warning if
        the time is not synchronized)."""
        text = tr("Please enter a valid UISP Key.")
        if self.original.get("ntpclient.status") != "enabled":
            text += "\n\n" + tr("Warning: correct time/date settings are required by UISP. "
                                 "Please make sure NTP time synchronization is enabled.")
        while True:
            key = self.ui.inputbox(tr("UISP Controller"), text, "")
            if key is None:
                return None
            err = unms_key_problem(key)
            if err is None:
                return key
            self.ui.msgbox(tr("Invalid value"), err)

    def _uisp_send(self, enable: bool, key: str) -> None:
        """POST /api/unms/enable (with the key) or /api/unms/disable, then the device read
        again with what was pending here on top (as its web, which takes it as saved)."""
        if self._test_running("UISP"):         # (it saves at once, and reads it again)
            return
        pending = self.changes()
        self.ui.infobox("UISP", tr("Sending…"))
        if enable:
            self.dev.unms_enable(key)
        else:
            self.dev.unms_disable()
        self.load()
        _merge(self.working, {k: new for k, _, new in pending if not k.startswith("unms.")})
        self.ui.msgbox("UISP", (tr("UISP management is on: {controller}.",
                                   controller=unms_key_shown(key)) if enable
                                else tr("UISP management is off."))
                       + "\n" + tr("Status: {state}", state=unms_state(self.status)))

    # -- the rest of its Services page (airOS 8): SNMP, the web server, SSH, Telnet, log --

    def _access_rows(self, offered: dict) -> list[tuple[str, str]]:
        cfg, orig = self.working, self.original
        rows = []
        if offered["snmp"]:
            sn = snmp_settings(cfg)
            rows.append(("a", changed_mark(sn, snmp_settings(orig)) + (
                tr("SNMP Agent: enabled") if sn["on"] else tr("SNMP Agent: disabled"))))
        if offered["web"]:
            ws = web_settings(cfg)
            rows.append(("h", changed_mark(ws, web_settings(orig)) + (
                tr("Web Server: HTTPS on port {https}, HTTP on {http}", https=ws["https_port"],
                   http=ws["http_port"]) if ws["https"] else
                tr("Web Server: HTTP on port {http}, without HTTPS", http=ws["http_port"]))))
        if offered["ssh"]:
            ss, keys = ssh_settings(cfg), ssh_keys(cfg)
            rows.append(("s", changed_mark((ss, keys), (ssh_settings(orig), ssh_keys(orig))) + (
                tr("SSH Server: enabled, port {port}, {count} key(s)", port=ss["port"],
                   count=len(keys)) if ss["on"] else tr("SSH Server: disabled"))))
        if offered["telnet"]:
            ts = telnet_settings(cfg)
            rows.append(("t", changed_mark(ts, telnet_settings(orig)) + (
                tr("Telnet Server: enabled, port {port}", port=ts["port"]) if ts["on"]
                else tr("Telnet Server: disabled"))))
        if offered["syslog"]:
            ls = syslog_settings(cfg)
            rows.append(("g", changed_mark(ls, syslog_settings(orig)) + (
                tr("System Log: enabled, also to {ip}:{port}", ip=ls["ip"], port=ls["port"])
                if ls["on"] and ls["remote"] else tr("System Log: enabled") if ls["on"]
                else tr("System Log: disabled"))))
        return rows

    def _ask_text(self, title: str, prompt: str, init: str, problem,
                  strip: bool = True) -> str | None:
        """A value with its web's check (problem(text): its message, or None); strip=False
        keeps it as typed (where its web does not trim it)."""
        def parse(text):
            msg = problem(text)
            return (None, msg) if msg else (text.strip() if strip else text, None)
        return self._ask_valid(title, prompt, init, parse)

    def _ask_port(self, title: str, prompt: str, init: str, service: str,
                  field: str) -> str | None:
        """A server's port with its portSys (the other servers as they will be)."""
        port = self._ask_text(title, prompt, init, lambda v: service_port_problem(
            service, field, v, self.working))
        return None if port is None else service_port(port)

    def snmp_menu(self) -> None:
        """The web's SNMP Agent: on or off, and its community, contact and location (required
        while it is on)."""
        title, last = tr("SNMP Agent"), "o"
        fields = (("c", "community", tr("SNMP Community")), ("n", "contact", tr("Contact")),
                  ("l", "location", tr("Location")))
        while True:
            sn, osn = snmp_settings(self.working), snmp_settings(self.original)
            items = [("o", ("* " if sn["on"] != osn["on"] else "") + (
                tr("SNMP Agent: enabled") if sn["on"] else tr("SNMP Agent: disabled")))]
            if sn["on"]:
                items += [(tag, ("* " if sn[f] != osn[f] else "") + f"{label}: {sn[f] or '—'}")
                          for tag, f, label in fields]
            choice = self.ui.menu(title, tr("The SNMP agent of the web's Services page: other "
                                            "programs read the device's data with it, with the "
                                            "community as their password. * = changed, not "
                                            "applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(sn)
            if choice == "o" and sn["on"]:
                if not self.ui.ask(title, tr("Disable the SNMP agent?")):
                    continue
                new["on"] = False
            elif choice == "o":
                new["on"] = True
                for _tag, f, label in fields:      # (all three required while it is on)
                    if snmp_problem(f, new[f]):
                        value = self._ask_text(title, label + ":", new[f],
                                               lambda v, f=f: snmp_problem(f, v))
                        if value is None:
                            break
                        new[f] = value
                else:
                    self._accept_service(plan_snmp(new), new,
                                         snmp_settings(self.original), "on", "snmp.")
                continue
            else:
                _tag, f, label = next(x for x in fields if x[0] == choice)
                value = self._ask_text(title, label + ":", sn[f], lambda v: snmp_problem(f, v))
                if value is None:
                    continue
                new[f] = value
            self._accept_service(plan_snmp(new), new, snmp_settings(self.original), "on",
                                 "snmp.")

    def web_server_menu(self) -> None:
        """The web's Web Server: HTTPS on or off and both ports. This program reconnects at
        the new address after applying (test mode brings it back if it cannot)."""
        title, last = tr("Web Server"), "s"
        while True:
            ws, ows = web_settings(self.working), web_settings(self.original)

            mark = field_marks(ws, ows)
            items = [("s", mark("https") + (tr("Secure Connection (HTTPS): enabled")
                                            if ws["https"] else
                                            tr("Secure Connection (HTTPS): disabled")))]
            if ws["https"]:
                items.append(("p", mark("https_port") + tr("Secure Server Port: {port}",
                                                           port=ws["https_port"])))
            items.append(("h", mark("http_port") + tr("Server Port: {port}",
                                                      port=ws["http_port"])))
            choice = self.ui.menu(title, tr("The web server of the web's Services page, the one "
                                            "this program talks to too: after applying a change "
                                            "here it reconnects at its new address (if it "
                                            "cannot, test mode brings it back). * = changed, not "
                                            "applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(ws)
            if choice == "s":
                if ws["https"] and not self.ui.ask(title, tr(
                        "Disable HTTPS?\n\nIts web, and this program, will then go over HTTP, "
                        "unencrypted: the password travels in plain text."), default_no=True):
                    continue
                new["https"] = not ws["https"]
                if new["https"] and service_port_problem("web", "https_port", new["https_port"],
                                                         self.working):
                    port = self._ask_port(title, tr("Secure Server Port:"), new["https_port"],
                                          "web", "https_port")
                    if port is None:
                        continue
                    new["https_port"] = port
            else:
                field = "https_port" if choice == "p" else "http_port"
                port = self._ask_port(title, tr("Secure Server Port:") if choice == "p"
                                      else tr("Server Port:"), ws[field], "web", field)
                if port is None:
                    continue
                new[field] = port
            self._accept_service(plan_web(new))

    def ssh_menu(self) -> None:
        """The web's SSH Server: on or off, its port, password authentication and its
        authorized keys."""
        title, last = tr("SSH Server"), "o"
        while True:
            ss, oss = ssh_settings(self.working), ssh_settings(self.original)
            keys = ssh_keys(self.working)

            mark = field_marks(ss, oss)
            items = [("o", mark("on") + (tr("SSH Server: enabled") if ss["on"]
                                         else tr("SSH Server: disabled")))]
            if ss["on"]:
                items += [("p", mark("port") + tr("Server Port: {port}", port=ss["port"])),
                          ("w", mark("passwd") + (tr("Password Authentication: enabled")
                                                  if ss["passwd"] else
                                                  tr("Password Authentication: disabled"))),
                          ("k", ("* " if keys != ssh_keys(self.original) else "")
                           + tr("Authorized Keys: {count}", count=len(keys)))]
            choice = self.ui.menu(title, tr("The SSH server of the web's Services page. * = "
                                            "changed, not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(ss)
            if choice == "o":
                if ss["on"] and not self.ui.ask(title, tr("Disable the SSH server?")):
                    continue
                new["on"] = not ss["on"]
                if new["on"] and service_port_problem("ssh", "port", new["port"], self.working):
                    port = self._ask_port(title, tr("Server Port:"), new["port"], "ssh", "port")
                    if port is None:
                        continue
                    new["port"] = port
            elif choice == "p":
                port = self._ask_port(title, tr("Server Port:"), ss["port"], "ssh", "port")
                if port is None:
                    continue
                new["port"] = port
            elif choice == "w":
                if ss["passwd"] and not any(k["on"] for k in keys) and not self.ui.ask(title, tr(
                        "Disable password authentication?\n\nThere are no authorized keys: "
                        "nobody will be able to log in by SSH."), default_no=True):
                    continue
                new["passwd"] = not ss["passwd"]
            else:
                self.ssh_keys_menu()
                continue
            self._accept_service(plan_ssh(new))

    def ssh_keys_menu(self) -> None:
        """The web's SSH Authorized Keys: its keys (enabled or not, with a comment), new ones
        from a public key file (or pasted) and removing them."""
        title, last = tr("SSH Authorized Keys"), "a"
        while True:
            keys, before = ssh_keys(self.working), ssh_keys(self.original)
            items = [(str(i), ("" if i <= len(before) and before[i - 1] == k else "* ")
                      + ("" if k["on"] else tr("(disabled)") + " ") + ssh_key_shown(k))
                     for i, k in enumerate(keys, 1)]
            items.append(("a", tr("Import public keys (a .pub file, or paste one)")))
            text = (tr("The keys that log in by SSH without a password (as its web: ssh-rsa "
                       "ones). * = changed, not applied yet.") if keys
                    else tr("No keys available."))
            choice = self.ui.menu(title, text, items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "a":
                new = self._import_ssh_keys(keys)
                if new is not None:
                    self._accept(plan_ssh_keys(self.working, new))
                continue
            i = int(choice) - 1
            key = keys[i]
            act = self.ui.menu(title, ssh_key_shown(key), [
                ("e", tr("Disable it") if key["on"] else tr("Enable it")),
                ("c", tr("Change its comment")), ("x", tr("Remove"))])
            new = [dict(k) for k in keys]
            if act == "e":
                new[i]["on"] = not key["on"]
            elif act == "c":
                comment = self.ui.inputbox(title, tr("Comment:"), key["comment"])
                if comment is None:
                    continue
                new[i]["comment"] = comment.strip()
            elif act == "x" and self.ui.ask(title, tr("Remove this key?\n\n{key}",
                                                      key=ssh_key_shown(key)), default_no=True):
                del new[i]
            else:
                continue
            self._accept(plan_ssh_keys(self.working, new))

    def _import_ssh_keys(self, keys: list[dict]) -> list[dict] | None:
        """As its dialog's import: the keys of a file (or the key pasted), the ones already
        there left out; at most MAX_SSH_KEYS."""
        title = tr("SSH Authorized Keys")
        text = self.ui.inputbox(title, tr("Path of a public key file (for example "
                                          "~/.ssh/id_rsa.pub), or the key itself:"), "")
        if text is None or not text.strip():
            return None
        path = os.path.expanduser(text.strip())
        if os.path.isfile(path):
            name = os.path.basename(path)
            try:
                if os.path.getsize(path) > SSH_KEY_FILE_MAX:
                    self.ui.msgbox(title, tr("Uploaded file {name} is too big.", name=name))
                    return None
                with open(path, encoding="utf-8", errors="replace") as f:
                    found = parse_ssh_keys(f.read())
            except OSError as e:
                self.ui.msgbox(title, tr('Error parsing file "{file}".', file=name)
                               + f"\n{e.strerror or e}")
                return None
            if found is None:
                self.ui.msgbox(title, tr('Error parsing file "{file}".', file=name))
                return None
            if found:                               # (as its dialog: nothing if it is empty)
                self.ui.msgbox(title, tr('Found {count} key(s) in "{file}".', count=len(found),
                                         file=name))
        else:
            found = parse_ssh_keys(text.strip())
            if not found:
                self.ui.msgbox(title, tr("That is not a public key as its web takes it: ssh-rsa, "
                                         "the key and, if there is one, a comment without "
                                         "spaces."))
                return None
        new = add_ssh_keys(keys, found)
        if new == keys:                             # (all of them were there already)
            return None
        if len(new) > MAX_SSH_KEYS:
            self.ui.msgbox(title, tr("Error: maximum number of keys allowed is {max}",
                                     max=MAX_SSH_KEYS))
            return None
        return new

    def telnet_menu(self) -> None:
        """The web's Telnet Server: on or off, and its port."""
        title, last = tr("Telnet Server"), "o"
        while True:
            ts, ots = telnet_settings(self.working), telnet_settings(self.original)
            items = [("o", ("* " if ts["on"] != ots["on"] else "") + (
                tr("Telnet Server: enabled") if ts["on"] else tr("Telnet Server: disabled")))]
            if ts["on"]:
                items.append(("p", ("* " if ts["port"] != ots["port"] else "")
                              + tr("Server Port: {port}", port=ts["port"])))
            choice = self.ui.menu(title, tr("The Telnet server of the web's Services page. * = "
                                            "changed, not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(ts)
            if choice == "o":
                if not self.ui.ask(title, tr("Disable the Telnet server?") if ts["on"] else tr(
                        "Enable the Telnet server?\n\nTelnet is not encrypted: the user and the "
                        "password travel in plain text."), default_no=not ts["on"]):
                    continue
                new["on"] = not ts["on"]
                if new["on"] and service_port_problem("telnet", "port", new["port"],
                                                      self.working):
                    port = self._ask_port(title, tr("Server Port:"), new["port"], "telnet",
                                          "port")
                    if port is None:
                        continue
                    new["port"] = port
            else:
                port = self._ask_port(title, tr("Server Port:"), ts["port"], "telnet", "port")
                if port is None:
                    continue
                new["port"] = port
            self._accept_service(plan_telnet(new))

    def syslog_menu(self) -> None:
        """The web's System Log: on or off and, while it is, also sent to a remote server (its
        IP and port)."""
        title, last = tr("System Log"), "o"
        while True:
            ls, ols = syslog_settings(self.working), syslog_settings(self.original)

            mark = field_marks(ls, ols)
            items = [("o", mark("on") + (tr("System Log: enabled") if ls["on"]
                                         else tr("System Log: disabled")))]
            if ls["on"]:
                items.append(("r", mark("remote") + (tr("Remote Log: enabled") if ls["remote"]
                                                     else tr("Remote Log: disabled"))))
                if ls["remote"]:
                    items += [("i", mark("ip") + tr("Remote Log IP Address: {ip}",
                                                    ip=ls["ip"] or "—")),
                              ("p", mark("port") + tr("Remote Log Port: {port}",
                                                      port=ls["port"]))]
            choice = self.ui.menu(title, tr("The system log of the web's Services page: the "
                                            "device keeps its log and can also send it to a "
                                            "syslog server. * = changed, not applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(ls)
            asks = {"i": ("ip", tr("Remote Log IP Address:")),
                    "p": ("port", tr("Remote Log Port:"))}
            if choice == "o":
                new["on"] = not ls["on"]
            elif choice == "r" and ls["remote"]:
                new["remote"] = False
            elif choice == "r":
                new["remote"] = True
                for f, prompt in asks.values():        # (the IP always: it has no default)
                    if f == "ip" or syslog_problem(f, new[f]):
                        value = self._ask_text(title, prompt, new[f],
                                               lambda v, f=f: syslog_problem(f, v))
                        if value is None:
                            break
                        new[f] = value
                else:
                    self._accept_back(plan_syslog(new), new,
                                      syslog_settings(self.original), "remote",
                                      "syslog.remote.")
                continue
            else:
                f, prompt = asks[choice]
                value = self._ask_text(title, prompt, ls[f], lambda v: syslog_problem(f, v))
                if value is None:
                    continue
                new[f] = value
            self._accept_back(plan_syslog(new), new, syslog_settings(self.original),
                              "remote", "syslog.remote.")
