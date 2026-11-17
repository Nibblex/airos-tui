"""App: the Network menu (IP, DNS, PPPoE, network mode, LAN)."""
from __future__ import annotations

import ipaddress
import time

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import Config, _find, _indices, _merge, _on
from ..device.network import (bridge_of, default_route, describe_ip, dhcp_fallback, dhcp_server,
                              dns_servers, ip_mode, lan_ifaces, main_iface, main_role, net_ifaces,
                              net_mode, netmode_label, _network, plan_dhcp, plan_dns, plan_lan,
                              plan_pppoe, plan_static, pppoe_limits, pppoe_settings, prefix_len,
                              _role_fix, shift_into)
from ..device.discovery import norm_mac
from ..device.netmode import (advanced_network, board_netmodes, iface_side, lan_range, mgmt_blocked,
                              mgmt_vlan, mgmt_vlan_offered, mgmt_vlan_problem, nat_devname, nat_on,
                              network_losses, plan_block_mgmt, plan_mgmt_vlan, plan_nat, SIDE_TEXT,
                              switch_netmode)
from ..device.services import discovery_settings, network_back
from ..device.dhcp import DHCP_LEASE_RANGE, lan_dhcp
from ..device.status import format_pppoe, pppoe_line
from .ui import changed_mark

# After enabling or restarting PPPoE: how long to wait for pppd to connect, and how often
# to ask.
PPPOE_WAIT = 20
PPPOE_POLL = 2


class NetworkScreens:
    """The Network menu."""

    # -- network ---------------------------------------------------------------------

    def _live_ip(self, devname: str) -> str:
        """IP the interface has right now according to the device (works with DHCP)."""
        for i in self.status.get("interfaces") or []:
            if i.get("ifname") == devname:
                ip = (i.get("status") or {}).get("ipaddr")
                if ip and ip != "0.0.0.0":
                    return ip
        return ""

    def _device_macs(self) -> set[str]:
        macs = {norm_mac(str(i.get("hwaddr") or "")) for i in self.status.get("interfaces") or []}
        if self.limits:
            macs.add(norm_mac(self.limits.board.get("board.hwaddr") or ""))
        return {m for m in macs if m and m != "000000000000"}

    def network_summary(self) -> str:
        cfg, out = self.working, []

        def row(label: str, value) -> str:
            return f"{label:<16} {value}"

        mode = net_mode(cfg)
        out.append(tr("Network mode: {mode}", mode=netmode_label(mode)))
        ifc = main_iface(cfg, self.dev.flavor)
        if ifc:
            dev, role = ifc["devname"], ifc["role"] or "?"
            out += ["", tr("── Management interface: {dev} (role {role}) ──", dev=dev, role=role)
                    if mode == "bridge" else
                    tr("── WAN: {dev} (role {role}) ──", dev=dev, role=role),
                    row(tr("Address:"), describe_ip(cfg, ifc))]
            ipm = ip_mode(cfg, dev)
            if ipm == "static":
                out.append(row("DNS:", ", ".join(x for x in dns_servers(cfg) if x) or "—"))
            if ipm == "pppoe":
                s = pppoe_settings(cfg, dev)
                user = s["name"] or "—"
                prefix = prefix_len(s["fallback_netmask"]) or s["fallback_netmask"]
                out += [row(tr("PPPoE user:"), tr("{user} · password saved", user=user)
                            if s["password"] else tr("{user} · password empty", user=user)),
                        row(tr("Service:"), s["service"] or tr("(any)")),
                        row("MTU / MRU:", tr("{mtu} / {mru} · MPPE encryption required",
                                             mtu=s["mtu"], mru=s["mru"]) if s["mppe"] else
                            tr("{mtu} / {mru} · MPPE encryption not required",
                               mtu=s["mtu"], mru=s["mru"])),
                        row(tr("Fallback IP:"), tr("{ip}/{prefix} (if PPPoE does not connect)",
                                                   ip=s["fallback"], prefix=prefix))]
            live = self._live_ip("ppp0") if ipm == "pppoe" else ""
            live = live or self._live_ip(dev)
            if live:
                out.append(row(tr("IP in use now:"), live))
            out.append(row(tr("Automatic IP:"), tr("169.254.x.x enabled") if ifc["autoip"]
                           else tr("169.254.x.x disabled")))
            def ports_of(b):
                return ", ".join(p for p in (cfg.get(f"bridge.{b}.port.{n}.devname")
                                             for n in _indices(cfg, f"bridge.{b}.port")) if p)
            # (br0 in bridge mode, as the STP row; in the others, the main interface's bridge)
            stp_dev = "br0" if mode == "bridge" else dev
            b = bridge_of(cfg, stp_dev)
            if b is not None:
                ports_text = ports_of(b)
                out.append(row(tr("Bridge {dev}:", dev=stp_dev),
                               tr("{ports} · STP yes", ports=ports_text)
                               if _on(cfg, f"bridge.{b}.stp.status")
                               else tr("{ports} · STP no", ports=ports_text)))
            vid = mgmt_vlan(cfg) if mode == "bridge" else None
            if vid:
                out.append(row(tr("Management VLAN:"), tr(
                    "VLAN {id} · bridge br1: {ports}", id=vid,
                    ports=ports_of(bridge_of(cfg, "br1")))))
        for lan in lan_ifaces(cfg):
            out += ["", tr("── LAN: {dev} ──", dev=lan["devname"]),
                    row(tr("Address:"), self._describe_lan(lan))]
        out += ["", tr("── Interfaces ──")]
        for i in net_ifaces(cfg):
            ip = "" if i["ip"] == "0.0.0.0" else f"{i['ip']}/{prefix_len(i['netmask']) or '?'}"
            state = "" if i["enabled"] else " " + tr("(disabled)")
            out.append(f"{i['devname']:<10}{i['role']:<12}{ip}{state}")
        if _on(cfg, "iptables.sys.masq.status"):
            out.append("\n" + tr("NAT enabled on {ifaces}", ifaces=", ".join(
                cfg.get(f"iptables.sys.masq.{n}.devname") or "?"
                for n in _indices(cfg, "iptables.sys.masq")
                if _on(cfg, f"iptables.sys.masq.{n}.status"))))
        if ifc and mode != "bridge":
            out.append(tr("Management (web, SSH) from the WAN: blocked")
                       if mgmt_blocked(cfg, ifc)
                       else tr("Management (web, SSH) from the WAN: allowed"))
        out += ["", tr("Not changed from here: the interface MTU, IPv6 or the static routes.\n"
                       "(The advanced configuration's VLANs and bridges are in this menu.)\n"
                       'For the rest use the web or "Search and edit any key".')
                if self.dev.flavor == "v8" else
                tr("Not changed from here: the management VLAN, the interface MTU,\n"
                   "IPv6, port forwarding, the firewall or extra routes. For that use\n"
                   'the web or "Search and edit any key".')]
        return "\n".join(out)

    def _lan_dhcp_key(self, cfg: Config, lan: dict):
        """What the LAN row compares to mark it changed: its DHCP as each web reads it."""
        if self.dev.flavor == "v8":
            return lan_dhcp(cfg, lan["devname"])
        return dhcp_server(cfg, lan["devname"])

    def _describe_lan(self, lan: dict) -> str:
        text = f"{lan['ip']}/{prefix_len(lan['netmask']) or lan['netmask']}"
        if self.dev.flavor == "v8":
            ds = lan_dhcp(self.working, lan["devname"])
            if ds["mode"] == "relay":
                return tr("{net}, DHCP relay to {ip}", net=text, ip=ds["relay_ip"] or "?")
            if ds["mode"] == "enabled":
                return tr("{net}, DHCP server {start}–{end}", net=text, start=ds["start"],
                          end=ds["end"])
            return tr("{net}, DHCP server off", net=text)
        srv = dhcp_server(self.working, lan["devname"])
        if srv and srv["on"]:
            return tr("{net}, DHCP server {start}–{end}", net=text, start=srv["start"],
                      end=srv["end"])
        return tr("{net}, DHCP server off", net=text)

    def _live_ips(self, cfg: Config, ifc: dict) -> set[str]:
        """IPs the interface answers at: the configured one, the one the device reports and,
        with PPPoE, the session's (ppp0)."""
        ips = {ifc["ip"], self._live_ip(ifc["devname"])}
        if ip_mode(cfg, ifc["devname"]) == "pppoe":
            ips.add(self._live_ip("ppp0"))
        return ips - {"", "0.0.0.0"}

    def _follow_plan(self, base: Config | None = None, new: Config | None = None) -> dict | None:
        """Does the change move the IP we are connected through?
        {'old', 'new' (None if DHCP or PPPoE gives the IP), 'mode', 'macs', 'candidates'}."""
        base, new = base or self.original, new or self.working
        host = self.dev.host
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return None                     # connected by name: it cannot be followed
        for before in [main_iface(base, self.dev.flavor)] + lan_ifaces(base):
            if not before or host not in self._live_ips(base, before):
                continue
            if net_mode(new) != net_mode(base):
                return self._netmode_plan(new, host)
            # where its address is afterwards: the same interface with the same role or, if
            # it moved (rebuilt networks rename and renumber them), the one with its role
            ifs = net_ifaces(new)
            after = (next((i for i in ifs if i["devname"] == before["devname"]
                           and i["role"] == before["role"]), None)
                     or next((i for i in ifs if before["role"] and i["role"] == before["role"]),
                             None)
                     or next((i for i in ifs if i["devname"] == before["devname"]), None))
            if after is None:
                return None
            dev = after["devname"]
            mode = ip_mode(new, dev)
            plan = {"old": host, "new": None, "mode": mode, "macs": self._device_macs(),
                    "candidates": []}
            if mode in ("dhcp", "pppoe"):
                # If no DHCP server answers or the PPPoE session does not come up, it uses
                # the fallback IP.
                def fallback(cfg: Config, devname: str) -> str:
                    return (dhcp_fallback(cfg, devname)[0] if mode == "dhcp"
                            else pppoe_settings(cfg, devname)["fallback"])
                if ip_mode(base, before["devname"]) == mode:
                    # (connected through its fallback IP: none gives it another, so it moves
                    # with it)
                    new_fb = fallback(new, dev)
                    if host == fallback(base, before["devname"]) and new_fb not in (
                            "", "0.0.0.0", host):
                        return dict(plan, new=new_fb)
                    return None
                plan["candidates"] = [fallback(new, dev)]
                return plan
            if after["ip"] not in (host, "0.0.0.0"):
                return dict(plan, new=after["ip"])
            return None
        return None

    def _backup_plan(self, backup: Config) -> dict | None:
        """Where a backup leaves the device when the configuration it uses now is unknown (while
        a backup waits, getcfg.cgi hands over the backup: as this program starts, and after a
        first setup from one): at the IPs the backup gives it and by its MAC, as with another
        network mode (here first, if the backup keeps it)."""
        try:
            ipaddress.IPv4Address(self.dev.host)
        except ValueError:
            return None                     # connected by name: it cannot be followed
        return dict(self._netmode_plan(backup, self.dev.host), mode="backup")

    def _netmode_plan(self, new: Config, host: str) -> dict:
        """With another network mode every IP changes: look for the antenna by its MAC and at
        the IPs it will have (management, LAN, WAN or their fallback IPs)."""
        cands: list[str] = []
        for i in [main_iface(new, self.dev.flavor)] + lan_ifaces(new):
            if not i:
                continue
            mode = ip_mode(new, i["devname"])
            ip = (i["ip"] if mode == "static" else dhcp_fallback(new, i["devname"])[0]
                  if mode == "dhcp" else pppoe_settings(new, i["devname"])["fallback"])
            if ip and ip != "0.0.0.0" and ip not in cands:
                cands.append(ip)
        if host in cands:                      # if it keeps that IP, look there first
            cands.remove(host)
            cands.insert(0, host)
        return {"old": host, "new": None, "mode": "netmode", "macs": self._device_macs(),
                "candidates": cands}

    @staticmethod
    def _follow_target(plan: dict, found: bool = True) -> str:
        if plan["new"]:
            return plan["new"]
        if plan["mode"] == "backup":
            where = ", ".join(plan["candidates"]) or "-"
            if not found:
                return tr("the IP the backup gives it (I did not find it at {where} nor by its "
                          "MAC)", where=where)
            return tr("the IP the backup gives it (I will look for it by its MAC and at {where})",
                      where=where)
        if plan["mode"] == "netmode":
            if not found:
                return tr("the IP it has in the new mode (I did not find it)")
            if plan["candidates"]:
                return tr("the IP it has in the new mode (I will look for it by its MAC and at "
                          "{where})", where=", ".join(plan["candidates"]))
            return tr("the IP it has in the new mode (I will look for it by its MAC)")
        fb = plan["candidates"][0] if plan["candidates"] else ""
        if plan["mode"] == "dhcp":
            if not found:
                return tr("the IP DHCP gives it (I did not find it)")
            if fb:
                return tr("the IP DHCP gives it (I will look for it by its MAC, or at the "
                          "fallback one {ip})", ip=fb)
            return tr("the IP DHCP gives it (I will look for it by its MAC)")
        if not found:
            return tr("the IP it gets with PPPoE (I did not find it)")
        if fb:
            return tr("the IP it gets with PPPoE (I will look for it by its MAC, or at the "
                      "fallback one {ip})", ip=fb)
        return tr("the IP it gets with PPPoE (I will look for it by its MAC)")

    def _discovery_on(self) -> bool:
        """Will the antenna answer Ubiquiti discovery with the configuration being prepared?"""
        return discovery_settings(self.working, self.dev.flavor)[0]

    def _ip_change_note(self) -> str:
        plan = self._follow_plan()
        note = self._ip_change_text(plan)
        if plan and not plan["new"] and not self._discovery_on():
            note += " " + tr("Careful: Ubiquiti discovery is disabled, so the search by MAC "
                             "will not find it.")
        return note

    def _ip_change_text(self, plan: dict | None) -> str:
        moved = net_mode(self.working) != net_mode(self.original)
        if not plan and moved:
            return tr("With the new mode the antenna's IPs change. If you cannot find it after "
                      "applying, test mode takes it back to the previous configuration by "
                      "itself.")
        if not plan:
            return tr("The IP you are connected through does not change.")
        if plan["mode"] == "netmode":
            stays = plan["candidates"][:1] == [plan["old"]]
            where = ", ".join(plan["candidates"]) or tr("the IP it gets assigned")
            return " ".join([
                tr("You are now connected through {old}.", old=plan["old"]),
                tr("If your PC stays on the same side, the antenna will stay at {old}.",
                   old=plan["old"]) if stays
                else tr("With the new mode the antenna leaves that IP."),
                tr("When applying, the program will look for it by its MAC and at {where}; if "
                   "it does not find it, test mode takes it back to the previous configuration "
                   "by itself.", where=where)])
        if plan["new"]:
            return tr("You are now connected through {old}. When applying, the antenna will "
                      "move to {new} and the program will reconnect there (your computer has to "
                      "be able to reach that network).", old=plan["old"], new=plan["new"])
        fb = plan["candidates"][0] if plan["candidates"] else ""
        if plan["mode"] == "pppoe":
            return tr("You are now connected through {old}, the WAN IP. With PPPoE the WAN will "
                      "no longer have that IP: it will have the one your provider gives it, and "
                      "you will most likely lose access from here. The program will look for the "
                      "antenna by its MAC (Ubiquiti discovery) and at the fallback IP {fallback}, "
                      "the one it takes if PPPoE does not connect. If it does not find it, test "
                      "mode takes it back to the previous configuration by itself.",
                      old=plan["old"], fallback=fb)
        return tr("You are now connected through {old}. The antenna will ask for an IP over "
                  "DHCP: the program will look for it on your network by its MAC (Ubiquiti "
                  "discovery) and at the fallback IP {fallback}, the one it takes if no DHCP "
                  "server answers.", old=plan["old"], fallback=fb)

    def network_menu(self) -> None:
        last = "1"
        while True:
            cfg, orig = self.working, self.original
            mode = net_mode(cfg)
            ifc = main_iface(cfg, self.dev.flavor)
            lans = lan_ifaces(cfg)
            items = self._network_items(cfg, orig, mode, ifc, lans)
            text = tr("Network mode: {mode}. * = changed, not applied yet.",
                      mode=netmode_label(mode))
            if not ifc:
                text += "\n" + tr("I did not find the main interface (role mlan or wan).")
            choice = self.ui.menu(tr("Network"), text, items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "1":
                self.ui.textbox(tr("Network"), self.network_summary())
            elif choice == "m":
                self.change_netmode()
            elif choice == "n" and ifc:
                self.toggle_nat(ifc)
            elif choice == "b" and ifc:
                self.toggle_wan_mgmt(ifc)
            elif choice == "2" and ifc:
                self.edit_main_ip(ifc)
            elif choice == "p":
                self.pppoe_status()
            elif choice == "c":
                self.pppoe_reconnect()
            elif choice == "3" and ifc:
                self.edit_dns(mode != "bridge")
            elif choice == "4" and ifc:
                self.edit_fallback(ifc)
            elif choice == "5" and ifc:
                self._toggle_autoip(ifc)
            elif choice == "v" and ifc:
                self.mgmt_vlan_menu(ifc)
            elif choice == "6" and ifc:
                self._toggle_stp(cfg)
            elif choice.startswith("l") and choice[1:].isdigit():
                if self.dev.flavor == "v8":
                    self.lan_menu(lans[int(choice[1:]) - 1])
                else:
                    self.edit_lan(lans[int(choice[1:]) - 1])
            elif not self.advnet_choice(choice):
                self.router_choice(choice)

    def _network_items(self, cfg: Config, orig: Config, mode: str, ifc: dict | None,
                       lans: list[dict]) -> list[tuple[str, str]]:
        """The rows of the Network menu: each setting, with "* " where it is not the
        device's."""
        oifc = main_iface(orig, self.dev.flavor)
        items = [("1", tr("Network summary")),
                 ("m", changed_mark(mode, net_mode(orig))
                  + tr("Network mode: {mode}", mode=netmode_label(mode)))]
        if ifc:
            dev = ifc["devname"]
            name = tr("Management IP") if mode == "bridge" else f"WAN ({dev})"
            now, before = describe_ip(cfg, ifc), describe_ip(orig, oifc) if oifc else ""
            if "pppoe" in (ip_mode(cfg, dev), ip_mode(orig, dev)):   # password, MTU, service…
                now, before = (now, pppoe_settings(cfg, dev)), (before,
                                                                pppoe_settings(orig, dev))
            items.append(("2", f"{changed_mark(now, before)}{name}: {describe_ip(cfg, ifc)}"))
            if oifc and ip_mode(orig, oifc["devname"]) == "pppoe":   # what the device uses
                items += [("p", tr("PPPoE connection status (on the device)")),
                          ("c", tr("Reconnect PPPoE"))]
            ipm = ip_mode(cfg, dev)
            if ipm == "static":
                shown = ", ".join(x for x in dns_servers(cfg) if x) or tr("(none)")
                items.append(("3", changed_mark(dns_servers(cfg), dns_servers(orig))
                              + tr("DNS servers: {servers}", servers=shown)))
            if ipm == "dhcp":
                fb = dhcp_fallback(cfg, dev)
                items.append(("4", changed_mark(fb, dhcp_fallback(orig, dev))
                              + tr("Fallback IP if DHCP fails: {ip}/{prefix}", ip=fb[0],
                                   prefix=prefix_len(fb[1]) or fb[1])))
            items.append(("5", changed_mark(ifc["autoip"], bool(oifc and oifc["autoip"]))
                          + (tr("Automatic IP 169.254.x.x: enabled") if ifc["autoip"]
                             else tr("Automatic IP 169.254.x.x: disabled"))))
            b = bridge_of(cfg, "br0") if mode == "bridge" else None   # (the web's: br0's)
            if b is not None:
                on = _on(cfg, f"bridge.{b}.stp.status")
                items.append(("6", changed_mark(on, _on(orig, f"bridge.{b}.stp.status"))
                              + (tr("STP on the bridge {dev}: enabled", dev="br0") if on
                                 else tr("STP on the bridge {dev}: disabled", dev="br0"))))
            if mgmt_vlan_offered(cfg, self.dev.flavor, self._board()):
                vid = mgmt_vlan(cfg)
                items.append(("v", changed_mark(vid, mgmt_vlan(orig)) + tr(
                    "Management VLAN: {state}", state=f"VLAN {vid}" if vid else (
                        tr("set by hand (advanced configuration)")
                        if advanced_network(cfg) else tr("disabled")))))
            if mode != "bridge":
                nat = nat_on(cfg, ifc)
                onat = bool(oifc) and net_mode(orig) != "bridge" and nat_on(orig, oifc)
                items.append(("n", changed_mark(nat, onat)
                              + (tr("NAT on the WAN: enabled") if nat
                                 else tr("NAT on the WAN: disabled"))))
                blk = mgmt_blocked(cfg, ifc)
                oblk = bool(oifc) and net_mode(orig) != "bridge" and mgmt_blocked(orig, oifc)
                items.append(("b", changed_mark(blk, oblk)
                              + (tr("Management (web, SSH) from the WAN: blocked") if blk
                                 else tr("Management (web, SSH) from the WAN: allowed"))))
        for j, lan in enumerate(lans[:3], 1):           # (the menu offers these three)
            olan = next((x for x in lan_ifaces(orig) if x["n"] == lan["n"]), None)
            before = (f"{olan['ip']}{olan['netmask']}{self._lan_dhcp_key(orig, olan)}"
                      if olan else "")
            now = f"{lan['ip']}{lan['netmask']}{self._lan_dhcp_key(cfg, lan)}"
            items.append((f"l{j}", f"{changed_mark(now, before)}LAN ({lan['devname']}): "
                                   f"{self._describe_lan(lan)}"))
        items += self.router_rows(cfg, orig) + self.advnet_rows()
        return items

    def _toggle_autoip(self, ifc: dict) -> None:
        """The automatic IP 169.254.x.x of the main interface: on or off, asked first."""
        on = not ifc["autoip"]
        question = (tr("Enable the automatic IP 169.254.x.x on {iface}?\n\nWith it you "
                       "can get into the device over the cable even if you do not know "
                       "its IP.", iface=ifc["devname"]) if on else
                    tr("Disable the automatic IP 169.254.x.x on {iface}?\n\nWith it you "
                       "can get into the device over the cable even if you do not know "
                       "its IP.", iface=ifc["devname"]))
        if self.ui.ask(tr("Automatic IP"), question):
            self._accept({f"netconf.{ifc['n']}.autoip.status":
                          "enabled" if on else "disabled"})

    def _toggle_stp(self, cfg: Config) -> None:
        """STP on br0, the bridge of the main interface in bridge mode (the only one its web
        offers it on): on or off, asked first."""
        b = bridge_of(cfg, "br0")
        on = not _on(cfg, f"bridge.{b}.stp.status")
        question = (tr("Enable STP on the bridge {iface}?\n\nIt avoids loops if there "
                       "are several paths between the same devices.",
                       iface="br0") if on else
                    tr("Disable STP on the bridge {iface}?\n\nIt avoids loops if there "
                       "are several paths between the same devices.",
                       iface="br0"))
        if self.ui.ask("STP", question):
            self._accept({f"bridge.{b}.stp.status": "enabled" if on else "disabled"})

    # -- management VLAN (airOS 8, bridge mode) --

    def mgmt_vlan_menu(self, ifc: dict) -> None:
        """The web's Management VLAN (bridge mode, simple network configuration): the
        management, with its address, on a VLAN of every port, as its web saves it; prepared
        as the rest, to apply with test mode."""
        title = tr("Management VLAN")
        cfg = self.working
        if advanced_network(cfg):
            self.ui.msgbox(title, tr(
                "This device uses the advanced network configuration of its web: there the "
                "VLANs and the bridges are set by hand, and the web does not offer this "
                "switch. Use the VLANs and Bridges of this menu."))
            return
        now = mgmt_vlan(cfg)
        where = (ifc["ip"] if ip_mode(cfg, ifc["devname"]) == "static"
                 else tr("its address"))
        if now:
            choice = self.ui.menu(title, tr("The management is on VLAN {id}.", id=now), [
                ("c", tr("Change the VLAN ID")), ("o", tr("Turn the management VLAN off"))])
            if choice is None:
                return
            if choice == "o":
                if self.ui.ask(title, tr(
                        "Turn the management VLAN off?\n\nAfter applying, the device will "
                        "answer at {ip} without a VLAN (untagged): the computer or the switch "
                        "port you manage it from must not use VLAN {id}, or you will lose "
                        "access to it.\n\nApply it with \"Apply changes\" → \"Test\": if you "
                        "cannot reach it, it goes back by itself.", ip=where, id=now),
                        default_no=True):
                    self._accept(plan_mgmt_vlan(cfg, None))
                return

        def parse(text):
            problem = mgmt_vlan_problem(text)
            return (None, problem) if problem else (int(text.strip()), None)
        vid = self._ask_valid(title, tr("VLAN ID (from 2 to 4094):"), now or "", parse)
        if vid is None or vid == now:
            return
        if self.ui.ask(title, tr(
                "Put the management on VLAN {id}?\n\nAfter applying, the device will answer "
                "at {ip} only with VLAN {id} tagged: the computer or the switch port you manage "
                "it from must use that VLAN, or you will lose access to it. The rest of the "
                "traffic goes on as before, without a VLAN.\n\nApply it with \"Apply "
                "changes\" → \"Test\": if you cannot reach it, it goes back by itself.",
                id=vid, ip=where), default_no=True):
            self._accept(plan_mgmt_vlan(cfg, vid))

    # -- network mode -----------------------------------------------------------------

    def _own_static_ip(self, cfg: Config) -> dict | None:
        """The fixed IP we are connected through now, with its netmask and, if it is the main
        one, its gateway and DNS: {'ip','netmask','gateway','dns1','dns2'}."""
        host = self.dev.host
        main = main_iface(cfg, self.dev.flavor)
        for i in ([main] if main else []) + lan_ifaces(cfg):
            if ip_mode(cfg, i["devname"]) == "static" and i["ip"] == host:
                gw, dns = ("", ["", ""])
                if i is main:
                    gw, dns = default_route(cfg)[1], dns_servers(cfg)
                return {"ip": host, "netmask": i["netmask"] or "255.255.255.0", "gateway": gw,
                        "dns1": dns[0], "dns2": dns[1]}
        return None

    def change_netmode(self) -> None:
        cfg, orig, flavor = self.working, self.original, self.dev.flavor
        board = self.limits.board if self.limits else {}
        if not board:
            self.ui.msgbox(tr("Network mode"),
                           tr("I could not read the board information (which modes it supports "
                              "and how many ports it has) and without it I do not change the "
                              "network mode. Read the configuration again or use the web."))
            return
        cur, modes = net_mode(cfg), board_netmodes(board)
        if set(modes) <= {cur}:
            self.ui.msgbox(tr("Network mode"),
                           tr("This device only supports the {mode} mode (according to the "
                              "board's board.netmodes).", mode=netmode_label(cur)))
            return
        about = {"bridge": tr("a single network and one management IP"),
                 "router": tr("separate WAN and LAN, with NAT (the WAN supports PPPoE)"),
                 "soho": tr("router with the WAN on the cable and a wireless LAN")}
        target = self.ui.menu(tr("Network mode"),
                              tr("Now: {mode}.\nWhich mode do I switch it to?",
                                 mode=netmode_label(cur)),
                              [(m, f"{netmode_label(m)}: {about[m]}") for m in modes],
                              default=cur if cur in modes else modes[0])
        if target is None or target == cur:
            return
        if target == net_mode(orig):            # like the web: the device's network comes back
            if self._accept_cfg(network_back(cfg, orig, flavor)):
                self.ui.msgbox(tr("Network mode"),
                               tr("The network the device has comes back ({mode}), just as it "
                                  "was.", mode=netmode_label(target)))
            return
        lost = network_losses(cfg, flavor)
        text = tr("Switching from {old} to {new} rebuilds the antenna's whole network with the "
                  "new mode's defaults, just like the web.", old=netmode_label(cur),
                  new=netmode_label(target))
        if lost:
            text += "\n\n" + tr("Lost: {items}.", items=", ".join(lost))
        text += "\n\n" + tr('Nothing is sent until you apply, and with "Test" the antenna goes '
                            "back to the previous configuration by itself if you lose "
                            "access.\n\nContinue?")
        if not self.ui.ask(tr("Change the network mode"), text, default_no=True):
            return
        cand = switch_netmode(cfg, flavor, target, board)
        notes: list[str] = []
        cand = self._netmode_access(cfg, cand, target, notes)
        if cand is None or not self._accept_cfg(cand):
            return
        self.ui.msgbox(tr("Network mode"), self._netmode_summary(notes))

    def _netmode_access(self, old: Config, cand: Config, target: str,
                        notes: list[str]) -> Config | None:
        """What the web leaves in the form to edit before saving: here it is decided where the
        IP you are connected through ends up, so as not to lose access."""
        flavor = self.dev.flavor
        mine = self._own_static_ip(old)
        if target == "bridge":
            mg = main_iface(cand, flavor)
            if mg is None:
                return cand
            # The template's static IP is proposed, as its web's form shows it, whatever the
            # WAN had (static, DHCP or PPPoE): it can be changed, or DHCP chosen, below.
            where = tr("You are now connected through {old}.", old=mine["ip"]) + " " if mine else ""
            title = tr("Management IP (bridge mode)")
            choice = self.ui.menu(title, where
                                  + tr("In bridge mode the antenna has a single IP, on {dev}.",
                                       dev=mg["devname"]) + "\n"
                                  + tr("Now: {now}\nHow does it get its IP?",
                                       now=describe_ip(cand, mg)),
                                  [("1", tr("Static IP (fixed)")),
                                   ("2", tr("DHCP: a server on your network assigns it"))],
                                  default="1")
            if choice is None:
                return None
            updates = self._ask_static_or_dhcp(cand, mg, title, choice)
            return None if updates is None else _merge(cand, updates)
        if not mine:
            notes.append(tr("You are not connected through a fixed IP of the antenna: the web's "
                            "defaults stay."))
            return cand
        wan, lans = main_iface(cand, flavor), lan_ifaces(cand)
        ifs = [i for i in [wan] + lans if i]
        by_side: dict[str, dict] = {}
        for i in ifs:
            by_side.setdefault(iface_side(cand, i["devname"]), i)

        def target_for(side: str) -> dict | None:
            return by_side.get(side) or by_side.get("both")

        def label(i: dict | None) -> str:
            if i is None:
                return tr("no interface is left on that side")
            if i is wan:
                return tr("{ip} moves to the WAN ({dev})", ip=mine["ip"], dev=i["devname"])
            return tr("{ip} moves to the LAN ({dev})", ip=mine["ip"], dev=i["devname"])
        options = [("wired", tr("Over the cable (Ethernet): {result}",
                                result=label(target_for("wired")))),
                   ("wireless", tr("Over the wireless link: {result}",
                                   result=label(target_for("wireless")))),
                   ("default", tr("Use the web's defaults (LAN 192.168.1.1, WAN over DHCP)"))]
        side = self.ui.menu(tr("Network mode"),
                            tr("You are now connected through {old}. To keep access, that IP "
                               "stays on the side your PC comes from.\nWhere does your PC reach "
                               "the antenna from?", old=mine["ip"]),
                            options, default="wired")
        if side is None:
            return None
        t = target_for(side) if side in ("wired", "wireless") else None
        if t is None:
            return cand
        ip, mask = mine["ip"], mine["netmask"]
        if t is wan:
            _merge(cand, plan_static(cand, wan, ip, mask, mine["gateway"], mine["dns1"],
                                     mine["dns2"]))
            if mgmt_blocked(cand, wan):
                _merge(cand, plan_block_mgmt(cand, wan, False))
                notes.append(tr("Management from the WAN stays allowed (the web blocks it when "
                                "changing mode), because you are connected through it."))
            wnet = _network(ip, mask)
            for lan in lan_ifaces(cand):
                if wnet and (_network(lan["ip"], lan["netmask"]) or wnet).overlaps(wnet):
                    free = next((n for n in ("192.168.1.1", "192.168.2.1", "10.10.10.1",
                                             "172.16.1.1")
                                 if not ipaddress.IPv4Network(f"{n}/24", strict=False)
                                 .overlaps(wnet)), None)
                    if free:
                        srv = dhcp_server(cand, lan["devname"])
                        if srv and srv["on"]:      # SOHO comes with a DHCP server on the LAN
                            start, end = lan_range(free, "255.255.255.0")
                            up = plan_lan(cand, lan, free, "255.255.255.0", True, start, end,
                                          srv["lease"])
                        else:
                            up = plan_lan(cand, lan, free, "255.255.255.0", False)
                        _merge(cand, up)
                        notes.append(tr("The LAN moves to {net}/24 so as not to be on the same "
                                        "network as the WAN.", net=free))
        else:
            srv = dhcp_server(cand, t["devname"])
            if srv and srv["on"]:
                start, end = lan_range(ip, mask)
                _merge(cand, plan_lan(cand, t, ip, mask, True, start, end, srv["lease"]))
            else:
                _merge(cand, plan_lan(cand, t, ip, mask, False))
        return cand

    def _netmode_summary(self, notes: list[str]) -> str:
        cfg, flavor = self.working, self.dev.flavor
        mode = net_mode(cfg)
        out = [tr("Network mode: {mode} (not sent to the device yet).",
                  mode=netmode_label(mode)), ""]
        ifc = main_iface(cfg, flavor)
        if ifc:
            out.append(tr("{name}: {dev}, through {side}: {address}",
                          name=tr("Management IP") if mode == "bridge" else "WAN",
                          dev=ifc["devname"], side=tr(SIDE_TEXT[iface_side(cfg, ifc["devname"])]),
                          address=describe_ip(cfg, ifc)))
        for lan in lan_ifaces(cfg):
            out.append(tr("LAN: {dev}, through {side}: {address}", dev=lan["devname"],
                          side=tr(SIDE_TEXT[iface_side(cfg, lan["devname"])]),
                          address=self._describe_lan(lan)))
        if ifc and mode != "bridge":
            out.append(" · ".join([
                tr("NAT: enabled") if nat_on(cfg, ifc) else tr("NAT: disabled"),
                tr("management from the WAN: blocked") if mgmt_blocked(cfg, ifc)
                else tr("management from the WAN: allowed")]))
        out += [""] + notes + ([""] if notes else []) + [self._ip_change_note(), ""]
        out.append(tr('Check the network in this menu (for example, the WAN over PPPoE) and then '
                      'apply with "Test".'))
        return "\n".join(out)

    def toggle_nat(self, ifc: dict) -> None:
        on = not nat_on(self.working, ifc)
        dev = nat_devname(self.working, ifc)
        question = (tr("Enable NAT on the WAN ({dev})?\n\nWith NAT, the LAN devices go out with "
                       "the WAN IP. Without NAT, your provider's network has to have routes to "
                       "your LAN.", dev=dev) if on else
                    tr("Disable NAT on the WAN ({dev})?\n\nWith NAT, the LAN devices go out with "
                       "the WAN IP. Without NAT, your provider's network has to have routes to "
                       "your LAN.", dev=dev))
        if self.ui.ask("NAT", question):
            self._accept(plan_nat(self.working, ifc, on))

    def toggle_wan_mgmt(self, ifc: dict) -> None:
        cfg = self.working
        block = not mgmt_blocked(cfg, ifc)
        if block:
            text = tr("Block management (web, SSH…) from the WAN?\n\nYou will only be able to "
                      "manage the antenna from the LAN.")
            wan_ips = {ifc["ip"], self._live_ip(ifc["devname"]), self._live_ip("ppp0")}
            if self.dev.host in wan_ips - {"", "0.0.0.0"}:
                text += "\n\n" + tr("⚠ You are connected through the WAN now ({host}): you "
                                    "would lose access.", host=self.dev.host)
        else:
            text = tr("Allow management (web, SSH…) from the WAN?\n\nAnyone who reaches the WAN "
                      "IP will see the login page: use a strong password.")
        if self.ui.ask(tr("Management from the WAN"), text, default_no=block):
            self._accept(plan_block_mgmt(cfg, ifc, block))

    def _ask_ip_mode(self, cfg: Config, ifc: dict, title: str) -> str | None:
        """'1' static, '2' DHCP or '3' PPPoE (WAN only, as in the web)."""
        bridge = net_mode(cfg) == "bridge"
        options = [("1", tr("Static IP (fixed)")),
                   ("2", tr("DHCP: a server on your network assigns it"))]
        if not bridge:
            options.append(("3", tr("PPPoE: user and password from your provider")))
        default = {"static": "1", "dhcp": "2", "pppoe": "3"}.get(ip_mode(cfg, ifc["devname"]), "1")
        return self.ui.menu(title, tr("Now: {now}\nHow does it get its IP?",
                                      now=describe_ip(cfg, ifc)),
                            options, default=default if int(default) <= len(options) else "1")

    def edit_main_ip(self, ifc: dict) -> None:
        cfg = self.working
        title = tr("Management IP") if net_mode(cfg) == "bridge" else f"WAN ({ifc['devname']})"
        choice = self._ask_ip_mode(cfg, ifc, title)
        if choice is None:
            return
        if choice == "3":
            self.edit_pppoe(ifc)
            return
        updates = self._ask_static_or_dhcp(cfg, ifc, title, choice)
        if updates is None:
            return
        if self._accept(updates):
            self.ui.msgbox(title, tr("Change prepared (not sent to the device yet).") + "\n\n"
                           + self._ip_change_note() + "\n\n"
                           + tr('Apply it with "Apply changes" → "Test": if something goes '
                                'wrong, the antenna goes back to its previous configuration by '
                                'itself.'))

    def _ask_static_or_dhcp(self, cfg: Config, ifc: dict, title: str,
                            choice: str) -> dict[str, str] | None:
        """Ask for the static IP data (choice '1') or the DHCP data ('2'); returns the keys."""
        dev = ifc["devname"]
        bridge = net_mode(cfg) == "bridge"
        if choice == "1":
            ip = self._ask_ip(title, tr("IP address:"),
                              ifc["ip"] if ifc["ip"] != "0.0.0.0" else self._live_ip(dev))
            if ip is None:
                return None
            mask = self._ask_mask(title, ifc["netmask"] or "255.255.255.0")
            if mask is None:
                return None
            _, gw = default_route(cfg)
            optional_gw = bridge and self.dev.flavor == "v8"
            gw = self._ask_ip(title, tr("Gateway (empty = no gateway):") if optional_gw
                              else tr("Gateway:"), gw, allow_empty=optional_gw)
            if gw is None:
                return None
            d1, d2 = dns_servers(cfg)
            dns1 = self._ask_ip(title, tr("Primary DNS (empty = none):") if bridge
                                else tr("Primary DNS:"), d1, allow_empty=bridge)
            if dns1 is None:
                return None
            dns2 = self._ask_ip(title, tr("Secondary DNS (empty = none):"), d2, allow_empty=True)
            if dns2 is None:
                return None
            updates = plan_static(cfg, ifc, ip, mask, gw, dns1, dns2)
        else:
            fb_ip, fb_mask = dhcp_fallback(cfg, dev)
            ip = self._ask_ip(title, tr("Fallback IP (the one it will use if no DHCP server "
                                        "answers):"), fb_ip)
            if ip is None:
                return None
            mask = self._ask_mask(title, fb_mask)
            if mask is None:
                return None
            updates = plan_dhcp(cfg, ifc, ip, mask)
        updates.update(_role_fix(ifc, main_role(cfg)))
        return updates

    def edit_pppoe(self, ifc: dict) -> None:
        cfg, dev = self.working, ifc["devname"]
        title = tr("WAN ({dev}) over PPPoE", dev=dev)
        cur = pppoe_settings(cfg, dev)
        name = self.ui.inputbox(title, tr("PPPoE user (your provider gives it to you):"),
                                cur["name"])
        if name is None:
            return
        password = self.ui.passwordbox(title, tr("PPPoE password (empty = keep the current "
                                                 "one):") if cur["password"]
                                       else tr("PPPoE password:"))
        if password is None:
            return
        service = self.ui.inputbox(title, tr("PPPoE service name (leave it empty unless your "
                                             "provider gives you one):"), cur["service"])
        if service is None:
            return
        max_mtu, max_mru = pppoe_limits(cfg, ifc, self.dev.flavor)
        mtu = self._ask_int(title, tr("PPPoE MTU (64–{max}; the usual is 1492):", max=max_mtu),
                            cur["mtu"], 64, max_mtu)
        if mtu is None:
            return
        mru = self._ask_int(title, tr("PPPoE MRU (64–{max}; the usual is 1492):", max=max_mru),
                            cur["mru"], 64, max_mru)
        if mru is None:
            return
        mppe = self.ui.yesno(title, tr("Require MPPE encryption?\n\nEnable it only if your "
                                       "provider asks for it: if its server does not support "
                                       "it, it will not connect."),
                             default_no=not cur["mppe"])
        if mppe not in (0, 1):
            return
        # Always asked, as the airOS 8 web does. Its airOS 6 web lets you leave it empty, and
        # then the WAN has no IP while PPPoE does not connect: from that side, no way in.
        fb = self._ask_ip(title, tr("WAN fallback IP (the one it will have while PPPoE does not "
                                    "connect):"), cur["fallback"])
        if fb is None:
            return
        fb_mask = self._ask_mask(title, cur["fallback_netmask"])
        if fb_mask is None:
            return
        updates = plan_pppoe(cfg, ifc, {
            "name": name.strip(), "password": password or cur["password"],
            "service": service.strip(), "fallback": fb, "fallback_netmask": fb_mask,
            "mtu": mtu, "mru": mru, "mppe": mppe == 0})
        updates.update(_role_fix(ifc, "wan"))
        if not self._accept(updates):
            return
        note = self._ip_change_note()
        live = self._live_ip("ppp0")
        if live and self.dev.host == live and not self._follow_plan():
            note = tr("You are connected through the PPPoE session IP ({ip}): when applying, the "
                      "session reconnects and your provider could give it another IP.", ip=live)
        self.ui.msgbox(title, tr("Change prepared (not sent to the device yet).") + "\n\n" + note
                       + "\n\n" + tr('Apply it with "Apply changes" → "Test": before you decide, '
                                     'the program will tell you whether the PPPoE session '
                                     'connected.'))

    def _wait_pppoe(self, title: str, max_wait: float | None = None) -> dict:
        """Query the PPPoE session until it connects (pppd takes a few seconds).

        It is called when PPPoE should be active, so "not enabled" is waited out too: airOS 6
        copies the tested configuration to /tmp/running.cfg (what pppinfo.cgi reads) after
        restarting the services, and the web answers before that."""
        max_wait = PPPOE_WAIT if max_wait is None else max_wait
        gauge = self.ui.gauge(title, tr("Waiting for PPPoE to connect…"))
        t0 = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - t0
                gauge.update(min(99, int(elapsed * 100 / max_wait)),
                             tr("Waiting for the PPPoE session to connect… ({seconds} s)",
                                seconds=int(elapsed)))
                try:
                    info = self.dev.pppoe_info()
                except AirOSError as e:
                    info = {"error": str(e), "local": True}
                waiting = info.get("local") or info.get("error", "").strip().lower() in (
                    "not connected", "pppoe is not enabled.")
                if not waiting or time.monotonic() - t0 >= max_wait:
                    return info
                time.sleep(PPPOE_POLL)
        finally:
            gauge.close()

    def _pppoe_check(self, changes) -> str:
        """If the test touches PPPoE, one line with the session status to decide."""
        ifc = main_iface(self.working, self.dev.flavor)
        if not ifc or ip_mode(self.working, ifc["devname"]) != "pppoe" or \
                not any(k.startswith("ppp.") for k, _, _ in changes or []):
            return ""
        info = self._wait_pppoe(tr("Test mode"))
        try:
            self.status = self.dev.status()      # already with the session (ppp0) if it connected
        except AirOSError:
            pass
        return pppoe_line(info)

    def pppoe_status(self) -> None:
        self.ui.infobox("PPPoE", tr("Querying the PPPoE session on the device…"))
        try:
            info = self.dev.pppoe_info()
        except AirOSError as e:
            info = {"error": str(e), "local": True}
        self.ui.msgbox(tr("PPPoE connection"), format_pppoe(info))

    def pppoe_reconnect(self) -> None:
        text = tr("Cut the PPPoE session and connect again?\n\nWhat goes out to the Internet "
                  "through this antenna will be without connection for a few seconds.")
        live = self._live_ip("ppp0")
        if live and self.dev.host == live:
            text += "\n\n" + tr("⚠ You are connected through the PPPoE session IP ({ip}): you "
                                "will lose the connection and your provider could give it "
                                "another IP.", ip=live)
        if not self.ui.ask(tr("Reconnect PPPoE"), text, default_no=True):
            return
        self.ui.infobox(tr("Reconnect PPPoE"), tr("Cutting the PPPoE session…"))
        try:
            self.dev.pppoe_restart()
        except AirOSError as e:
            self.ui.msgbox(tr("Reconnect PPPoE"), tr("Could not restart PPPoE:\n{error}", error=e))
            return
        time.sleep(PPPOE_POLL)          # give pppd time to hang up before asking
        self.ui.msgbox(tr("PPPoE connection"),
                       format_pppoe(self._wait_pppoe(tr("Reconnect PPPoE"))))

    def edit_dns(self, required: bool = False) -> None:
        d1, d2 = dns_servers(self.working)
        dns1 = self._ask_ip(tr("DNS servers"), tr("Primary DNS:") if required
                            else tr("Primary DNS (empty = none):"), d1, allow_empty=not required)
        if dns1 is None:
            return
        dns2 = self._ask_ip(tr("DNS servers"), tr("Secondary DNS (empty = none):"), d2,
                            allow_empty=True)
        if dns2 is not None:
            self._accept(plan_dns(self.working, dns1, dns2))

    def edit_fallback(self, ifc: dict) -> None:
        k = _find(self.working, "dhcpc", devname=ifc["devname"])
        if k is None:
            return
        fb_ip, fb_mask = dhcp_fallback(self.working, ifc["devname"])
        ip = self._ask_ip(tr("Fallback IP"), tr("IP it will use if no DHCP server answers:"),
                          fb_ip)
        if ip is None:
            return
        mask = self._ask_mask(tr("Fallback IP"), fb_mask)
        if mask is not None and self._accept({f"dhcpc.{k}.fallback": ip,
                                              f"dhcpc.{k}.fallback_netmask": mask}):
            self.ui.msgbox(tr("Fallback IP"), tr("Change prepared (not sent to the device "
                                                 "yet).") + "\n\n" + self._ip_change_note())

    def edit_lan(self, lan: dict) -> None:
        cfg, title = self.working, f"LAN ({lan['devname']})"
        ip = self._ask_ip(title, tr("LAN IP address:"), lan["ip"])
        if ip is None:
            return
        mask = self._ask_mask(title, lan["netmask"] or "255.255.255.0")
        if mask is None:
            return
        srv = dhcp_server(cfg, lan["devname"])
        on = self.ui.yesno(title, tr("DHCP server enabled on this LAN?\n(it hands out IPs to the "
                                     "connected devices)"), default_no=not (srv and srv["on"]))
        if on == 255:
            return
        updates = plan_lan(cfg, lan, ip, mask, on == 0)
        if on == 0:
            old_net, new_net = f"{lan['ip']}/{lan['netmask']}", f"{ip}/{mask}"
            start0 = shift_into(srv["start"], old_net, new_net) if srv and srv["start"] else ""
            end0 = shift_into(srv["end"], old_net, new_net) if srv and srv["end"] else ""
            start = self._ask_ip(title, tr("First IP the DHCP hands out:"), start0)
            if start is None:
                return
            end = self._ask_ip(title, tr("Last IP the DHCP hands out:"), end0)
            if end is None:
                return
            # (its web takes from 120 to 172800 s, as the airOS 8 one: network3.js)
            lease = self._ask_int(title, tr("Lease time in seconds:"),
                                  (srv or {}).get("lease") or "600", *DHCP_LEASE_RANGE)
            if lease is None:
                return
            updates = plan_lan(cfg, lan, ip, mask, True, start, end, str(lease))
        if self._accept(updates):
            self.ui.msgbox(title, tr("Change prepared (not sent to the device yet).") + "\n\n"
                           + self._ip_change_note())
