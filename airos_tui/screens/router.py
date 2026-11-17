"""App: the router functions and the firewall of the web's Network page (airOS 8): the LAN
screen (its address, its DHCP server or relay, the DNS proxy and the leases it gave), the DHCP
reservations, port forwarding and the firewall (IPv4 and IPv6), each edited as its web's
dialogs and prepared as the rest."""
from __future__ import annotations

import ipaddress

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import gen3, _merge, is_digits
from ..device.network import _network, lan_ifaces, main_iface, net_mode, prefix_len
from ..device.netmode import advanced_network, lan_range
from ..device.firewall import (FW_PROTOS, FW_TARGETS, firewall_on, firewall_rules, fw_cmd_lossless,
                               fw_ports_apply, fw_rule_problem, fw_rule_saved, fw_table,
                               new_fw_rule, new_port_forward, plan_firewall, plan_port_forwarding,
                               port_forward_problem, port_forwarding, PORTFW_PROTOS, proto_name,
                               rule_interfaces, ui_devname)
from ..device.dhcp import (dhcp_lans, dhcp_problem, dhcp_reservations, lan_dhcp, parse_leases,
                           plan_lan_dhcp, plan_reservations, reservation_problem, web_dhcp_range)
from .ui import changed_mark, field_marks

_CMD_FIELDS = ("target", "chain", "etherType", "devname", "devnameOutput", "ipProto", "src",
               "sport", "dst", "dport", "src_inv", "sport_inv", "dst_inv", "dport_inv",
               "table", "vlan")


class RouterScreens:
    """The LAN, the DHCP reservations, the port forwarding and the firewall (airOS 8)."""

    # -- what its Network page shows in this mode --------------------------------------

    def _advanced(self) -> bool:
        """The advanced network configuration (Gen3 boards only have the simple one)."""
        return advanced_network(self.working) and not gen3(self._board())

    def router_rows(self, cfg, orig) -> list[tuple[str, str]]:
        """The Network menu's rows for what its web shows: port forwarding (router and SOHO),
        the DHCP reservations (router, advanced configuration) and the firewalls (advanced
        configuration, any mode)."""
        if self.dev.flavor != "v8":
            return []
        mode, rows = net_mode(cfg), []
        if mode in ("router", "soho"):
            on, rules = port_forwarding(cfg)
            rows.append(("r", changed_mark((on, rules), port_forwarding(orig)) + (
                tr("Port forwarding: enabled, {count} rule(s)", count=len(rules)) if on else
                tr("Port forwarding: disabled, {count} rule(s)", count=len(rules)))))
        if mode == "router" and self._advanced():
            res = dhcp_reservations(cfg)
            rows.append(("d", changed_mark(res, dhcp_reservations(orig))
                         + tr("DHCP reservations: {count}", count=len(res))))
        if self._advanced():
            for tag, v6 in (("f", False), ("f6", True)):
                on, rules = firewall_on(cfg, v6), firewall_rules(cfg, v6)
                now = (on, [dict(r, cmd="") for r in rules])
                before = (firewall_on(orig, v6), [dict(r, cmd="") for r in
                                                  firewall_rules(orig, v6)])
                if v6:
                    text = (tr("IPv6 firewall: enabled, {count} rule(s)", count=len(rules)) if on
                            else tr("IPv6 firewall: disabled, {count} rule(s)",
                                    count=len(rules)))
                else:
                    text = (tr("Firewall: enabled, {count} rule(s)", count=len(rules)) if on
                            else tr("Firewall: disabled, {count} rule(s)", count=len(rules)))
                rows.append((tag, changed_mark(now, before) + text))
        return rows

    def router_choice(self, choice: str) -> bool:
        if choice == "r":
            self.port_forward_menu()
        elif choice == "d":
            self.reservations_menu()
        elif choice in ("f", "f6"):
            self.firewall_menu(choice == "f6")
        else:
            return False
        return True

    def _status_has(self, *path) -> bool:
        value = self.status
        for p in path:
            value = value.get(p) if isinstance(value, dict) else None
        return bool(value)

    def _device_text(self, title: str, getter) -> None:
        self.ui.infobox(title, tr("Querying the device…"))
        try:
            text = getter()
        except AirOSError as e:
            hint = ("\n\n" + tr("Right after applying, the device takes about 15 s to load its "
                                 "rules: try again in a moment.")
                    if "error running" in str(e) else "")
            self.ui.msgbox(title, str(e) + hint)
            return
        self.ui.textbox(title, text.strip() or tr("(empty)"))

    # -- a rule or a reservation, field by field (as its web's dialogs) -------------------

    @staticmethod
    def _field_text(rec: dict, key: str, kind: str, extra) -> str:
        value = rec.get(key)
        if kind == "bool":
            return tr("Yes") if value else tr("No")
        if kind == "choice":
            return next((label for v, label in extra if v == value), str(value or "—"))
        text = str(value or "")
        if kind == "neg" and text and rec.get(f"{key}_inv"):
            text = f"! {text}"
        return text or "—"

    def _edit_record(self, title: str, record: dict, rows, check) -> dict | None:
        """record edited field by field: rows(rec) gives (tag, key, label, kind, extra) per
        field, kind "bool", "choice" (extra: [(value, label)]), "text" or "neg" (extra:
        (prompt, its field's check); "neg" takes a leading "!": all but it); check(rec) lists
        what its web says when accepting it. The record accepted, or None."""
        rec, last = dict(record), None
        while True:
            fields = rows(rec)
            items = [(tag, f"{label}: {self._field_text(rec, key, kind, extra)}")
                     for tag, key, label, kind, extra in fields]
            items.append(("ok", tr("Accept")))
            choice = self.ui.menu(title, tr('Choose a field to change it. "Accept" prepares '
                                            'it as it is (not applied yet).'), items,
                                  default=last or fields[0][0])
            if choice is None:
                if rec == record or self.ui.ask(title, tr("Leave without keeping these "
                                                          "changes?"), default_no=True):
                    return None
                continue
            last = choice
            if choice == "ok":
                problems = check(rec)
                if problems:
                    self.ui.msgbox(tr("Invalid value"), "\n".join(problems))
                    continue
                return rec
            _, key, label, kind, extra = next(f for f in fields if f[0] == choice)
            if kind == "bool":
                rec[key] = not rec.get(key)
            elif kind == "choice":
                pick = self.ui.menu(label, label + ":", [(str(i), lab) for i, (_, lab) in
                                                         enumerate(extra, 1)],
                                    default=next((str(i) for i, (v, _) in enumerate(extra, 1)
                                                  if v == rec.get(key)), "1"))
                if pick is not None:
                    rec[key] = extra[int(pick) - 1][0]
            else:
                prompt, problem = extra
                init = str(rec.get(key) or "")
                if kind == "neg" and init and rec.get(f"{key}_inv"):
                    init = f"! {init}"

                def parse(text, problem=problem, kind=kind):
                    text = text.strip()
                    neg = kind == "neg" and text.startswith("!")
                    value = text[1:].strip() if neg else text
                    msg = problem(value) if problem else None
                    return (None, msg) if msg else ((value, neg and bool(value)), None)
                got = self._ask_valid(label, prompt, init, parse)
                if got is not None:
                    rec[key] = got[0]
                    if kind == "neg":
                        rec[f"{key}_inv"] = got[1]

    def _list_action(self, title: str, line: str, index: int, count: int,
                     movable: bool) -> str | None:
        """What to do with the rule chosen: edit, move it (the firewall's) or delete it."""
        acts = [("e", tr("Edit"))]
        if movable and index > 0:
            acts.append(("u", tr("Move up")))
        if movable and index < count - 1:
            acts.append(("j", tr("Move down")))
        acts.append(("x", tr("Delete")))
        return self.ui.menu(title, line, acts)

    # -- port forwarding (router and SOHO modes) ---------------------------------------

    @staticmethod
    def _pf_line(i: int, r: dict) -> str:
        public = "" if r["dst"] in ("", "0.0.0.0/0") else r["dst"]
        line = (f"#{i} {ui_devname(r['devname'])}  {r['proto'].upper()}  {public}:{r['dport']}"
                f" → {r['host']}" + (f":{r['port']}" if r["port"] else ""))
        if r["src"] not in ("", "0.0.0.0/0"):
            line += "  " + tr("from {source}", source=r["src"])
        if r["comment"]:
            line += f"  ({r['comment']})"
        return line + ("" if r["on"] else "  " + tr("(disabled)"))

    def port_forward_menu(self) -> None:
        """The web's Port Forwarding (router and SOHO modes): its rules, on and off."""
        title, last = tr("Port forwarding"), "o"
        while True:
            on, rules = port_forwarding(self.working)
            oon, orules = port_forwarding(self.original)
            items = [("o", ("* " if on != oon else "") + (
                tr("Port forwarding: enabled") if on else tr("Port forwarding: disabled")))]
            for i, r in enumerate(rules, 1):
                mark = "* " if i > len(orules) or orules[i - 1] != r else ""
                items.append((str(i), mark + self._pf_line(i, r)))
            items.append(("a", tr("Add a rule")))
            if self._status_has("portfw"):
                items.append(("v", tr("Rules in use (on the device)")))
            choice = self.ui.menu(title, tr("The port forwarding of the web's Network page: "
                                            "what comes from the WAN to a public port goes to "
                                            "a device of the LAN. * = changed, not applied "
                                            "yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "o":
                self._accept(plan_port_forwarding(self.working, not on, rules))
            elif choice == "a":
                rule = self._edit_port_forward(new_port_forward(self.working))
                if rule is not None:
                    self._accept(plan_port_forwarding(self.working, on, rules + [rule]))
            elif choice == "v":
                self._device_text(tr("Port forwarding in use (on the device)"),
                                  self.dev.port_forward_text)
            else:
                i = int(choice) - 1
                act = self._list_action(title, self._pf_line(i + 1, rules[i]), i, len(rules),
                                        False)
                if act == "e":
                    rule = self._edit_port_forward(rules[i])
                    if rule is not None:
                        self._accept(plan_port_forwarding(self.working, on,
                                                          rules[:i] + [rule] + rules[i + 1:]))
                elif act == "x" and self.ui.ask(title, tr("Delete the port forwarding rule "
                                                          "#{n}?", n=i + 1), default_no=True):
                    self._accept(plan_port_forwarding(self.working, on,
                                                      rules[:i] + rules[i + 1:]))

    def _edit_port_forward(self, rule: dict) -> dict | None:
        ifcs = [(d, ui_devname(d)) for d in rule_interfaces(self.working)]

        def rows(rec):
            def check(field):
                return lambda v: port_forward_problem(field, v)
            return [("e", "on", tr("Enabled"), "bool", None),
                    ("i", "devname", tr("Interface"), "choice", ifcs),
                    ("h", "host", tr("Private IP"), "text",
                     (tr("The IP of the LAN device it goes to:"), check("host"))),
                    ("p", "port", tr("Private Port"), "text",
                     (tr("Its port (empty: the same; a range as 1000:2000):"), check("port"))),
                    ("t", "proto", tr("Type"), "choice", [(p, p.upper()) for p in PORTFW_PROTOS]),
                    ("s", "src", tr("Source IP/Mask"), "text",
                     (tr("From where (0.0.0.0/0: from anywhere):"), check("src"))),
                    ("d", "dst", tr("Public IP/Mask"), "text",
                     (tr("To which public IP (0.0.0.0/0: any of the device's):"), check("dst"))),
                    ("q", "dport", tr("Public Port"), "text",
                     (tr("The public port (a range as 1000:2000):"), check("dport"))),
                    ("c", "comment", tr("Comment"), "text", (tr("Comment:"), None))]

        def check(rec):
            return [m for m in (port_forward_problem(f, rec[f]) for f in
                                ("host", "port", "src", "dst", "dport")) if m]
        return self._edit_record(tr("Port forwarding rule"), rule, rows, check)

    # -- the firewall (advanced network configuration, any mode) -----------------------

    @staticmethod
    def _fw_line(i: int, r: dict, v6: bool) -> str:
        def side(ip, port, f):
            text = ("! " if r.get(f"{f}_inv") and ip else "") + (ip or (
                "::/0" if v6 else "0.0.0.0/0"))
            if port and fw_ports_apply(r):
                text += " :" + ("! " if r.get(f"{f[0]}port_inv") else "") + port
            return text
        ins = ui_devname(r["devname"]) if r["devname"] else tr("ANY")
        outs = ui_devname(r["devnameOutput"]) if r["devnameOutput"] else tr("ANY")
        line = (f"#{i} {r.get('target') or 'DROP'}  {ins} → {outs}  "
                f"{proto_name(r['ipProto'], v6)}  {side(r['src'], r['sport'], 'src')} → "
                f"{side(r['dst'], r['dport'], 'dst')}")
        if r["comment"]:
            line += f"  ({r['comment']})"
        return line + ("" if r["on"] else "  " + tr("(disabled)"))

    def firewall_menu(self, v6: bool = False) -> None:
        """The web's Firewall or IPv6 Firewall (advanced network configuration): its rules,
        in order (the first that matches decides), on and off."""
        title, last = (tr("IPv6 firewall") if v6 else tr("Firewall")), "o"
        while True:
            on, rules = firewall_on(self.working, v6), firewall_rules(self.working, v6)
            oon, orules = firewall_on(self.original, v6), firewall_rules(self.original, v6)
            items = [("o", ("* " if on != oon else "") + (
                tr("Firewall: enabled") if on else tr("Firewall: disabled")))]
            for i, r in enumerate(rules, 1):
                same = i <= len(orules) and dict(orules[i - 1], cmd="") == dict(r, cmd="")
                items.append((str(i), ("" if same else "* ") + self._fw_line(i, r, v6)))
            items.append(("a", tr("Add a rule")))
            used = self.status.get("firewall")    # (its listing: the IPv4 chain only)
            if not v6 and isinstance(used, dict) and used.get(fw_table(self.original, False)):
                items.append(("v", tr("Rules in use (on the device)")))
            choice = self.ui.menu(title, tr("The firewall of the web's Network page (advanced "
                                            "configuration): its rules go in order and the "
                                            "first one that matches decides. * = changed, not "
                                            "applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "o":
                self._accept(plan_firewall(self.working, v6, not on, rules))
            elif choice == "a":
                rule = self._edit_fw_rule(new_fw_rule(v6), v6)
                if rule is not None:
                    self._accept(plan_firewall(self.working, v6, on, rules + [rule]))
            elif choice == "v":
                self._device_text(tr("Firewall in use (on the device)"), self.dev.firewall_text)
            else:
                i = int(choice) - 1
                act = self._list_action(title, self._fw_line(i + 1, rules[i], v6), i,
                                        len(rules), True)
                new = list(rules)
                if act == "e":
                    rule = self._edit_fw_rule(rules[i], v6)
                    if rule is None:
                        continue
                    new[i] = rule
                elif act in ("u", "j"):
                    j = i - 1 if act == "u" else i + 1
                    new[i], new[j] = new[j], new[i]
                    last = str(j + 1)
                elif act == "x" and self.ui.ask(title, tr("Delete the firewall rule #{n}?",
                                                          n=i + 1), default_no=True):
                    del new[i]
                else:
                    continue
                self._accept(plan_firewall(self.working, v6, on, new))

    def _edit_fw_rule(self, rule: dict, v6: bool) -> dict | None:
        ifcs = [("", tr("ANY"))] + [(d, ui_devname(d)) for d in rule_interfaces(self.working)]
        bits = "128" if v6 else "32"
        whole = fw_cmd_lossless(rule, fw_table(self.working, v6), v6)
        if not whole:                  # (its web would write it again as another rule)
            self.ui.msgbox(tr("Firewall rule"), tr(
                "This rule's command has parts that neither this program nor its web read as "
                "its fields:\n  {cmd}\n\nWritten again from them it would be another rule "
                "(maybe a wider one), so only enabling it and its comment change here (it can "
                'also be moved or deleted). To change it, use "Search and edit any '
                'configuration key".', cmd=rule.get("cmd") or ""))

        def rows(rec):
            def check(field):
                return lambda v: fw_rule_problem(field, v, v6)
            if not whole:
                return [("e", "on", tr("Enabled"), "bool", None),
                        ("c", "comment", tr("Comment"), "text", (tr("Comment:"), None))]
            out = [("e", "on", tr("Enabled"), "bool", None),
                   ("t", "target", tr("Target"), "choice", [(t, t) for t in FW_TARGETS]),
                   ("i", "devname", tr("Input Interface"), "choice", ifcs),
                   ("o", "devnameOutput", tr("Output Interface"), "choice", ifcs),
                   ("p", "ipProto", tr("IP Type"), "choice",
                    [(v, label) for label, v in FW_PROTOS[v6]]),
                   ("s", "src", tr("Source IP/Mask"), "neg",
                    (tr("Address or network (empty: any; ! before it: all but it; bits up to "
                        "{bits}):", bits=bits), check("src")))]
            if fw_ports_apply(rec):
                out.append(("1", "sport", tr("Source Port"), "neg",
                            (tr("Port or range as 1000:2000 (empty: any; ! before it: all but "
                                "it):"), check("sport"))))
            out.append(("d", "dst", tr("Destination IP/Mask"), "neg",
                        (tr("Address or network (empty: any; ! before it: all but it; bits up "
                            "to {bits}):", bits=bits), check("dst"))))
            if fw_ports_apply(rec):
                out.append(("2", "dport", tr("Destination Port"), "neg",
                            (tr("Port or range as 1000:2000 (empty: any; ! before it: all but "
                                "it):"), check("dport"))))
            out.append(("c", "comment", tr("Comment"), "text", (tr("Comment:"), None)))
            return out

        def check(rec):
            if not whole:
                return []
            fields = ("src", "sport", "dst", "dport") if fw_ports_apply(rec) else ("src", "dst")
            return [m for m in (fw_rule_problem(f, rec[f], v6) for f in fields) if m]
        title = tr("IPv6 firewall rule") if v6 else tr("Firewall rule")
        rec = self._edit_record(title, rule, rows, check)
        if rec is None:
            return None
        if not whole:                              # (its command, as it is)
            return dict(rec, cmd=rule.get("cmd") or "")
        rec = fw_rule_saved(rec, v6)
        if any(rec.get(k) != rule.get(k) for k in _CMD_FIELDS):
            rec["cmd"] = ""                        # (written again, as its web builds it)
        if rec.get("ipProto") == "p2p" and rule.get("ipProto") != "p2p":
            self.ui.msgbox(title, tr("Careful: a NanoStation 5AC with airOS 8.7.14 keeps the P2P "
                                     "rules but does not apply them (they are not among its "
                                     'rules in use). Check "Rules in use (on the device)" after '
                                     "applying."))
        return rec

    # -- the LAN (router and SOHO modes) -----------------------------------------------

    @staticmethod
    def _dhcp_state(ds: dict) -> str:
        if ds["mode"] == "enabled":
            return tr("enabled, {start}–{end}", start=ds["start"], end=ds["end"])
        if ds["mode"] == "relay":
            return tr("relay to {ip}", ip=ds["relay_ip"]) if ds["relay_ip"] else tr("relay")
        return tr("disabled")

    def lan_menu(self, lan: dict) -> None:
        """The web's LAN Network Settings: the LAN's address, its DHCP server (or relay), the
        DNS proxy and the leases it gave."""
        n, dev = lan["n"], lan["devname"]
        title, last = f"LAN ({dev})", "1"
        while True:
            cfg, orig = self.working, self.original
            lan = next((i for i in lan_ifaces(cfg) if i["n"] == n), None)
            if lan is None:
                return
            olan = next((i for i in lan_ifaces(orig) if i["n"] == n), None)
            ds = lan_dhcp(cfg, dev)
            ods = lan_dhcp(orig, dev) if olan else None
            addr = f"{lan['ip']}/{prefix_len(lan['netmask']) or lan['netmask']}"
            moved = not olan or (olan["ip"], olan["netmask"]) != (lan["ip"], lan["netmask"])
            items = [("1", ("* " if moved else "") + tr("Address: {address}", address=addr)),
                     ("d", ("* " if not ods or {k: v for k, v in ds.items() if k != "proxy"}
                            != {k: v for k, v in ods.items() if k != "proxy"} else "")
                      + tr("DHCP server: {state}", state=self._dhcp_state(ds))),
                     ("p", ("* " if not ods or ds["proxy"] != ods["proxy"] else "")
                      + (tr("DNS proxy: enabled") if ds["proxy"] else tr("DNS proxy: disabled")))]
            if self._status_has("services", "dhcpd"):
                items.append(("c", tr("Leases given (on the device)")))
            choice = self.ui.menu(title, tr("The LAN of the web's Network page. * = changed, "
                                            "not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "1":
                self._lan_address(lan)
            elif choice == "d":
                self.dhcp_menu(lan)
            elif choice == "p":
                self._dns_proxy(lan, ds)
            elif choice == "c":
                self.show_leases()

    def _wan_devname(self) -> str:
        wan = main_iface(self.working, self.dev.flavor)
        return wan["devname"] if wan else ""

    def _lan_address(self, lan: dict) -> None:
        cfg, dev, title = self.working, lan["devname"], f"LAN ({lan['devname']})"
        ip = self._ask_ip(title, tr("LAN IP address:"), lan["ip"])
        if ip is None:
            return
        mask = self._ask_mask(title, lan["netmask"] or "255.255.255.0")
        if mask is None:
            return
        updates = {f"netconf.{lan['n']}.ip": ip, f"netconf.{lan['n']}.netmask": mask}
        ds, net = lan_dhcp(cfg, dev), _network(ip, mask)
        if ds["mode"] == "enabled" and net is not None:
            try:
                inside = all(ipaddress.IPv4Address(ds[k]) in net for k in ("start", "end"))
            except ValueError:
                inside = False
            proposal = web_dhcp_range(ip, mask)
            if not inside and proposal and self.ui.ask(title, tr(
                    "The DHCP range ({start}–{end}) is not in the new network. Use "
                    "{new_start}–{new_end}, the range its web proposes when the server is "
                    "enabled?", start=ds["start"], end=ds["end"], new_start=proposal[0],
                    new_end=proposal[1])):
                cand = _merge(cfg.copy(), updates)
                updates.update(plan_lan_dhcp(cand, dev, dict(ds, start=proposal[0],
                                                             end=proposal[1],
                                                             netmask=proposal[2]),
                                             self._wan_devname()))
        if self._accept(updates):
            self.ui.msgbox(title, tr("Change prepared (not sent to the device yet).") + "\n\n"
                           + self._ip_change_note())

    def _ask_dhcp(self, title: str, field: str, ds: dict, prompt: str):
        """A field of the DHCP with its web's check (ds: the rest of its settings)."""
        def parse(text):
            problem = dhcp_problem(field, text, ds)
            return (None, problem) if problem else (text.strip(), None)
        return self._ask_valid(title, prompt, ds.get(field) or "", parse)

    def _ask_dns(self, title: str, ds: dict) -> dict | None:
        """The DNS servers its DHCP hands out without the DNS proxy (the first one required)."""
        dns1 = self._ask_dhcp(title, "dns1", ds, tr("Primary DNS:"))
        if dns1 is None:
            return None
        dns2 = self._ask_dhcp(title, "dns2", dict(ds, dns1=dns1),
                              tr("Secondary DNS (empty: none):"))
        if dns2 is None:
            return None
        return dict(ds, dns1=dns1, dns2=dns2)

    def _dns_proxy(self, lan: dict, ds: dict) -> None:
        title, dev = tr("DNS proxy"), lan["devname"]
        if ds["proxy"]:
            if not self.ui.ask(title, tr("Disable the DNS proxy of {dev}?\n\nThen its DHCP hands "
                                         "out the DNS servers you set instead of the device's "
                                         "address.", dev=dev)):
                return
            new = dict(ds, proxy=False)
            if new["mode"] == "enabled" and dhcp_problem("dns1", new["dns1"], new):
                new = self._ask_dns(title, new)
                if new is None:
                    return
        else:
            if not self.ui.ask(title, tr("Enable the DNS proxy on {dev}?\n\nThe device answers "
                                         "the DNS queries of this LAN itself (it asks its own "
                                         "DNS servers) and its DHCP hands out the device's "
                                         "address as the DNS server.", dev=dev)):
                return
            new = dict(ds, proxy=True)
        self._accept(plan_lan_dhcp(self.working, dev, new, self._wan_devname()))

    def dhcp_menu(self, lan: dict) -> None:
        """The DHCP server of the LAN as its web's: disabled, enabled (range, netmask, lease
        time, DNS servers without the DNS proxy) or relay (server IP and agent ID)."""
        dev = lan["devname"]
        title, last = tr("DHCP server · LAN ({dev})", dev=dev), "m"
        labels = {"disabled": tr("disabled"), "enabled": tr("enabled"), "relay": tr("relay")}
        while True:
            cfg = self.working
            ds, ods = lan_dhcp(cfg, dev), lan_dhcp(self.original, dev)

            mark = field_marks(ds, ods)
            items = [("m", mark("mode") + tr("Mode: {mode}", mode=labels[ds["mode"]]))]
            if ds["mode"] == "enabled":
                items += [("r", mark("start", "end") + tr("Range: {start}–{end}",
                                                          start=ds["start"], end=ds["end"])),
                          ("n", mark("netmask") + tr("Netmask: {netmask}", netmask=ds["netmask"])),
                          ("t", mark("lease") + tr("Lease time: {seconds} s",
                                                   seconds=ds["lease"]))]
                if not ds["proxy"]:
                    items += [("1", mark("dns1") + tr("Primary DNS: {ip}", ip=ds["dns1"] or "—")),
                              ("2", mark("dns2") + tr("Secondary DNS: {ip}",
                                                      ip=ds["dns2"] or "—"))]
            elif ds["mode"] == "relay":
                items += [("i", mark("relay_ip") + tr("DHCP server IP: {ip}",
                                                      ip=ds["relay_ip"] or "—")),
                          ("a", mark("agent_id") + tr("Agent ID: {id}", id=ds["agent_id"] or "—"))]
            choice = self.ui.menu(title, tr("The DHCP server of this LAN, as in the web. With the "
                                            "DNS proxy (in the LAN screen) it hands out the "
                                            "device's address as DNS. * = changed, not applied "
                                            "yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = None
            if choice == "m":
                new = self._dhcp_mode(lan, ds, ods)
            elif choice == "r":
                start = self._ask_dhcp(title, "start", ds,
                                       tr("First IP it hands out:"))
                if start is not None:
                    end = self._ask_dhcp(title, "end", dict(ds, start=start),
                                         tr("Last IP it hands out:"))
                    if end is not None:
                        new = dict(ds, start=start, end=end)
            elif choice == "n":
                def parse(text):
                    mask = text.strip()
                    if is_digits(mask.lstrip("/")) and int(mask.lstrip("/")) <= 32:
                        mask = str(ipaddress.IPv4Network(f"0.0.0.0/{mask.lstrip('/')}").netmask)
                    problem = dhcp_problem("netmask", mask, ds) or dhcp_problem(
                        "end", ds["end"], dict(ds, netmask=mask))
                    return (None, problem) if problem else (mask, None)
                mask = self._ask_valid(title, tr("Netmask of the range (or its bits, as 24):"),
                                       ds["netmask"], parse)
                if mask is not None:
                    new = dict(ds, netmask=mask)
            elif choice == "t":
                lease = self._ask_dhcp(title, "lease", ds,
                                       tr("Lease time, in seconds (from 120 to 172800):"))
                if lease is not None:
                    new = dict(ds, lease=lease)
            elif choice in ("1", "2"):
                field = "dns1" if choice == "1" else "dns2"
                value = self._ask_dhcp(title, field, ds, tr("Primary DNS:") if choice == "1"
                                       else tr("Secondary DNS (empty: none):"))
                if value is not None:
                    new = dict(ds, **{field: value})
            elif choice == "i":
                ip = self._ask_dhcp(title, "relay_ip", ds,
                                    tr("IP of the DHCP server it relays to:"))
                if ip is not None:
                    new = dict(ds, relay_ip=ip)
            elif choice == "a":
                agent = self._ask_dhcp(title, "agent_id", ds, tr("Agent ID (option 82):"))
                if agent is not None:
                    new = dict(ds, agent_id=agent)
            if new is not None:
                self._accept(plan_lan_dhcp(self.working, dev, new, self._wan_devname()))

    def _dhcp_mode(self, lan: dict, ds: dict, ods: dict) -> dict | None:
        """Another mode, as its web's select: enabled takes the range it proposes for the LAN's
        address and keeps the lease time and DNS servers the server had (in this session, as
        its web's page keeps its model); relay asks for its server and agent ID; disabled and
        relay drop the server and its reservations (asked first, if it has any)."""
        title = tr("DHCP server · LAN ({dev})", dev=lan["devname"])
        modes = [("disabled", tr("Disable")), ("enabled", tr("Enable")), ("relay", tr("Relay"))]
        pick = self.ui.menu(title, tr("DHCP server mode:"), [
            (str(i), label) for i, (_, label) in enumerate(modes, 1)],
            default=str(1 + [m for m, _ in modes].index(ds["mode"])))
        if pick is None or modes[int(pick) - 1][0] == ds["mode"]:
            return None
        mode = modes[int(pick) - 1][0]
        kept = self.__dict__.setdefault("_dhcp_kept", {})
        if ds["mode"] == "enabled":                # (its web keeps the server's model)
            kept[lan["devname"]] = dict(ds)
        lost = [r for r in dhcp_reservations(self.working) if r["devname"] == lan["devname"]]
        lost_note = ("\n\n" + tr("Its {count} reservation(s) will be lost too, as in its web.",
                                 count=len(lost))) if lost else ""
        if mode == "disabled":
            question = tr("Disable the DHCP server of {dev}?", dev=lan["devname"]) + lost_note
            return dict(ds, mode="disabled") if self.ui.ask(title, question,
                                                           default_no=bool(lost)) else None
        if mode == "relay":
            if lost and not self.ui.ask(title, tr("Relay the DHCP of {dev} to another server?",
                                                  dev=lan["devname"]) + lost_note,
                                        default_no=True):
                return None
            new = dict(ds, mode="relay")
            ip = self._ask_dhcp(title, "relay_ip", new,
                                tr("IP of the DHCP server it relays to:"))
            if ip is None:
                return None
            agent = self._ask_dhcp(title, "agent_id", dict(new, relay_ip=ip),
                                   tr("Agent ID (option 82):"))
            return None if agent is None else dict(new, relay_ip=ip, agent_id=agent)
        new = dict(ds, mode="enabled")
        before = kept.get(lan["devname"]) or (ods if ods["mode"] == "enabled" else None)
        if before:
            new.update(lease=before["lease"], dns1=before["dns1"], dns2=before["dns2"])
        proposal = web_dhcp_range(lan["ip"], lan["netmask"])
        if proposal:
            new.update(start=proposal[0], end=proposal[1], netmask=proposal[2])
        elif lan_range(lan["ip"], lan["netmask"])[0]:   # (a /30: its web proposes none)
            start, end = lan_range(lan["ip"], lan["netmask"])
            new.update(start=start, end=end, netmask=lan["netmask"])
        if not new["proxy"] and dhcp_problem("dns1", new["dns1"], new):
            return self._ask_dns(title, new)
        return new

    def show_leases(self) -> None:
        """The leases its DHCP server gave (the web's "DHCP Leases" of its main page)."""
        title = tr("DHCP leases (on the device)")
        self.ui.infobox(title, tr("Querying the device…"))
        try:
            data = self.dev.dhcp_leases()
        except AirOSError as e:
            self.ui.msgbox(title, str(e))
            return
        v4, v6 = parse_leases(data)
        lines = []
        if v4:
            lines.append(f"{tr('MAC Address'):<18} {tr('IP Address'):<16} "
                         f"{tr('Remaining Lease'):<20} {tr('Hostname')}")
            lines += [f"{r['mac']:<18} {r['ip']:<16} {r['left']:<20} {r['hostname']}"
                      for r in v4]
        if v6:
            lines += ([""] if lines else []) + [
                f"{tr('Unique Identifier'):<20} {tr('IPv6 Address'):<26} {tr('Remaining Lease')}"]
            lines += [f"{r['id']:<20} {r['ip']:<26} {r['left']}" for r in v6]
        self.ui.textbox(title, "\n".join(lines) or tr(
            "The device lists no leases (it may take a while to list the newest ones)."))

    # -- DHCP reservations (router mode, advanced configuration) -------------------------

    @staticmethod
    def _res_line(r: dict) -> str:
        line = f"{ui_devname(r['devname'])}  {r['mac']} → {r['ip']}"
        if r["comment"]:
            line += f"  ({r['comment']})"
        return line + ("" if r["on"] else "  " + tr("(disabled)"))

    def reservations_menu(self) -> None:
        """The web's DHCP Address Reservation: an IP always for the same MAC, on a LAN whose
        DHCP server is enabled."""
        title, last = tr("DHCP reservations"), "a"
        while True:
            items_now, before = dhcp_reservations(self.working), dhcp_reservations(self.original)
            items = [(str(i), ("" if i <= len(before) and before[i - 1] == r else "* ")
                      + self._res_line(r)) for i, r in enumerate(items_now, 1)]
            items.append(("a", tr("Add a reservation")))
            choice = self.ui.menu(title, tr("The DHCP address reservations of the web's Network "
                                            "page: the LAN's DHCP server always gives that IP to "
                                            "that MAC. * = changed, not applied yet."), items,
                                  default=last)
            if choice is None:
                return
            last = choice
            lans = dhcp_lans(self.working)
            if choice == "a":
                if not lans:
                    self.ui.msgbox(title, tr("No LAN has its DHCP server enabled."))
                    continue
                item = self._edit_reservation({"devname": lans[0], "on": True, "mac": "",
                                               "ip": "", "comment": ""}, lans)
                if item is not None:
                    self._accept(plan_reservations(self.working, items_now + [item]))
                continue
            i = int(choice) - 1
            act = self._list_action(title, self._res_line(items_now[i]), i, len(items_now),
                                    False)
            if act == "e":
                item = self._edit_reservation(items_now[i], lans)
                if item is not None:
                    self._accept(plan_reservations(self.working,
                                                   items_now[:i] + [item] + items_now[i + 1:]))
            elif act == "x" and self.ui.ask(title, tr("Delete the reservation of {mac}?",
                                                      mac=items_now[i]["mac"]), default_no=True):
                self._accept(plan_reservations(self.working, items_now[:i] + items_now[i + 1:]))

    def _edit_reservation(self, item: dict, lans: list[str]) -> dict | None:
        def rows(rec):
            return [("e", "on", tr("Enabled"), "bool", None),
                    ("i", "devname", tr("Interface"), "choice",
                     [(d, ui_devname(d)) for d in lans]),
                    ("m", "mac", tr("MAC Address"), "text",
                     (tr("MAC address (as 00:11:22:33:44:55):"),
                      lambda v: reservation_problem("mac", v))),
                    ("p", "ip", tr("IP Address"), "text",
                     (tr("The IP it always gets:"), lambda v: reservation_problem("ip", v))),
                    ("c", "comment", tr("Comment"), "text", (tr("Comment:"), None))]

        def check(rec):
            return [m for m in (reservation_problem(f, rec[f]) for f in ("mac", "ip")) if m]
        return self._edit_record(tr("DHCP reservation"), item, rows, check)
