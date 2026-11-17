"""App: the advanced network configuration of the airOS 8 web (its Network page): the
configuration mode (simple or advanced) and, in advanced, its VLANs and its bridges, prepared
as the other changes, to apply with test mode."""
from __future__ import annotations

from ..core.i18n import tr
from ..core.config import _merge, gen3
from ..device.network import dhcp_fallback, ip_mode, main_iface, net_mode
from ..device.netmode import network_losses
from ..device.services import network_back
from ..device.firewall import ui_devname
from ..device.advnet import (bridge_list, bridge_ports_problem, CFG_MODE_KEY, cfg_mode_advanced,
                             free_ports, hw_interfaces, in_use, new_vlan, next_bridge_name,
                             plan_cfg_mode, plan_simple, plan_vlans_bridges, simple_blockers,
                             vlan_id_problem, vlan_list)
from .ui import changed_mark


class AdvancedNetworkScreens:
    """Its configuration mode, VLANs and bridges (airOS 8)."""

    def advnet_rows(self) -> list[tuple[str, str]]:
        """The Network menu's rows: the configuration mode and, in advanced, the VLANs and the
        bridges (airOS 8; Gen3 boards have another web)."""
        if self.dev.flavor != "v8" or gen3(self._board()):
            return []
        cfg, orig = self.working, self.original
        adv = cfg_mode_advanced(cfg)
        rows = [("a", changed_mark(adv, cfg_mode_advanced(orig)) + (
            tr("Configuration mode: advanced") if adv else tr("Configuration mode: simple")))]
        if adv:
            vlans, bridges = vlan_list(cfg), bridge_list(cfg)
            rows += [("vl", changed_mark(vlans, vlan_list(orig)) + tr(
                "VLANs: {list}", list=", ".join(ui_devname(v["devname"]) for v in vlans)
                or "—")),
                     ("br", changed_mark(bridges, bridge_list(orig)) + tr(
                         "Bridges: {list}", list=", ".join(ui_devname(b["devname"])
                                                           for b in bridges) or "—"))]
        return rows

    def advnet_choice(self, choice: str) -> bool:
        if self.dev.flavor != "v8":
            return False
        action = {"a": self.cfg_mode_menu, "vl": self.vlans_menu,
                  "br": self.bridges_menu}.get(choice)
        if action is None:
            return False
        action()
        return True

    # -- the configuration mode --

    def cfg_mode_menu(self) -> None:
        """Its "Configuration Mode": to advanced, only the mode; to simple, the mode if the
        network fits it and, if it does not, the network built again as its web does. Back to
        the mode the device has, it offers its network as the device has it too, undoing
        what the change rebuilt (its web keeps the rebuilt one: here it would stay as pending
        changes nobody sees made), as going back to its network mode does."""
        title, cfg, orig = tr("Configuration mode"), self.working, self.original
        advanced = not cfg_mode_advanced(cfg)               # the mode it goes to
        if advanced == cfg_mode_advanced(orig):             # the one the device has
            back = _merge(network_back(cfg, orig, "v8"), plan_cfg_mode(advanced))
            undone = [k for k, _, _ in cfg.diff(back) if k != CFG_MODE_KEY]
            if undone:
                rc = self.ui.yesno(title, (
                    tr("Back to the advanced network configuration, the one the device has.")
                    if advanced else
                    tr("Back to the simple network configuration, the one the device has."))
                    + "\n\n" + tr("Its network can come back too, as the device has it: the "
                                   "{count} network change(s) prepared since are undone (what "
                                   "the change of mode rebuilt, and any other). With «Only the "
                                   "mode», as its web does, the network stays as it is "
                                   "prepared.", count=len(undone)),
                    yes=tr("Bring it back"), no=tr("Only the mode"))
                if rc == 0:
                    if self._accept_cfg(back):
                        self.ui.msgbox(title, tr("Its configuration mode and its network are "
                                                 "back as the device has them."))
                    return
                if rc != 1:
                    return
                if advanced:                                # (it already chose: only the mode)
                    self._accept(plan_cfg_mode(True))
                    return
            elif not advanced:             # its own network, as the device shows it in simple
                if self.ui.ask(title, tr("Switch to the simple network configuration?\n\nThe "
                                         "network as it is fits it: only the mode changes.")):
                    self._accept(plan_cfg_mode(False))
                return
        if advanced:
            if self.ui.ask(title, tr("Switch to the advanced network configuration?\n\nAs in "
                                     "its web, the network is then set as its interfaces, "
                                     "VLANs and bridges; it does not change by switching.")):
                self._accept(plan_cfg_mode(True))
            return
        blockers = simple_blockers(cfg)
        if not blockers:
            if self.ui.ask(title, tr("Switch to the simple network configuration?\n\nThe "
                                     "network as it is fits it: only the mode changes.")):
                self._accept(plan_cfg_mode(False))
            return
        main = tr("the management") if net_mode(cfg) == "bridge" else tr("the WAN")
        ifc = main_iface(cfg, "v8")
        if ifc is None:                    # (its address would be the template's)
            self.ui.msgbox(title, tr("The simple mode is not offered: the interface of {main} "
                                     "is not found in its configuration, so its address could "
                                     "not be kept.", main=main))
            return
        how = ip_mode(cfg, ifc["devname"])
        keep = {"static": tr("keeping the static IP of {main} (with its gateway and DNS).",
                             main=main),
                "dhcp": tr("with {main} still on DHCP (fallback IP {ip}).", main=main,
                           ip=dhcp_fallback(cfg, ifc["devname"])[0]),
                "pppoe": tr("with {main} still on PPPoE (its account).", main=main)}[how]
        text = (tr("Switch to simple mode?") + "\n\n"
                + tr("Warning: Changing to simple mode will erase all current advanced "
                     "networking settings (bridge/vlan/etc).") + "\n\n"
                + tr("The simple mode cannot show its {what}: as its web does, the network is "
                     "built again from its mode's template,", what=", ".join(blockers))
                + " " + keep)
        if net_mode(cfg) != "bridge":
            text += " " + tr("Its NAT, its management blocking, its DMZ, its cloned MAC and "
                             "its LAN (address and DHCP server) are kept too.")
        plan = plan_simple(cfg, self._board())
        after = network_losses(_merge(cfg.copy(), plan))
        lost = [x for x in network_losses(cfg) if x not in after]
        if lost:
            text += "\n\n" + tr("Lost: {items}.", items=", ".join(lost))
        if cfg_mode_advanced(orig):
            text += "\n\n" + tr("Going back to the advanced mode brings its network back, as "
                                 "the device has it.")
        if self.ui.ask(title, text, default_no=True):
            self._accept(plan)

    # -- VLANs --

    def _vlan_label(self, v: dict) -> str:
        text = f"{ui_devname(v['parent'])}  VLAN {v['id']}"
        if not v["on"]:
            text += "  " + tr("(disabled)")
        return text + (f"  · {v['comment']}" if v["comment"] else "")

    def vlans_menu(self) -> None:
        """Its VLANs (advanced configuration): adding one (on a hardware interface), enabling
        or disabling it, its comment and deleting it, as its web saves them."""
        title, last = tr("VLANs"), "+"
        while True:
            vlans, before = vlan_list(self.working), vlan_list(self.original)
            items = [(str(i), ("" if v in before else "* ") + self._vlan_label(v))
                     for i, v in enumerate(vlans, 1)]
            items.append(("+", tr("Add a VLAN")))
            choice = self.ui.menu(title, tr("The VLANs of the web's advanced network "
                                            "configuration. * = changed, not applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "+":
                self._add_vlan()
                continue
            i = int(choice) - 1
            v = vlans[i]
            act = self.ui.menu(title, self._vlan_label(v), [
                ("e", tr("Disable the VLAN") if v["on"] else tr("Enable the VLAN")),
                ("c", tr("Change its comment")), ("x", tr("Delete"))])
            new = [dict(x) for x in vlans]
            if act == "e":
                use = in_use(self.working, v["devname"])
                if v["on"] and use:
                    self.ui.msgbox(title, tr("{name} cannot be disabled: {reason}.",
                                             name=ui_devname(v["devname"]), reason=use))
                    continue
                new[i]["on"] = not v["on"]
            elif act == "c":
                comment = self.ui.inputbox(title, tr("Comment:"), v["comment"])
                if comment is None:
                    continue
                new[i]["comment"] = comment.strip()
            elif act == "x":
                use = in_use(self.working, v["devname"])
                if use:
                    self.ui.msgbox(title, tr("{name} cannot be deleted: {reason}.",
                                             name=ui_devname(v["devname"]), reason=use))
                    continue
                if not self.ui.ask(title, tr("Delete {name}?\n\nAs in its web, its firewall "
                                             "and traffic shaping rules go with it.",
                                             name=ui_devname(v["devname"])), default_no=True):
                    continue
                del new[i]
            else:
                continue
            self._accept(plan_vlans_bridges(self.working, new, bridge_list(self.working)))

    def _add_vlan(self) -> None:
        title, cfg = tr("VLAN"), self.working
        hw = hw_interfaces(cfg)
        parent = hw[0] if len(hw) == 1 else self.ui.menu(
            title, tr("Its interface:"), [(d, ui_devname(d)) for d in hw])
        if parent is None:
            return
        vid = self._ask_text(title, tr("VLAN ID (2 to 4094):"), "",
                             lambda v: vlan_id_problem(cfg, parent, v))
        if vid is None:
            return
        comment = self.ui.inputbox(title, tr("Comment (optional):"), "")
        if comment is None:
            return
        added = new_vlan(cfg, parent, vid, comment.strip())
        if len(added) > 1:
            self.ui.msgbox(title, tr("As its web does on an access point, a wireless VLAN gets "
                                     "a bridge of its own: {bridge}.",
                                     bridge=ui_devname(added[1]["devname"])))
        self._accept(plan_vlans_bridges(cfg, vlan_list(cfg) + added[:1],
                                        bridge_list(cfg) + added[1:]))

    # -- bridges --

    def _bridge_label(self, b: dict) -> str:
        ports = ", ".join(ui_devname(p) for p in b["ports"]) or "—"
        text = f"{ui_devname(b['devname'])}: {ports}"
        if b["stp"]:
            text += "  STP"
        if b["option82"]:
            text += "  " + tr("option 82")
        if not b["on"]:
            text += "  " + tr("(disabled)")
        return text + (f"  · {b['comment']}" if b["comment"] else "")

    def bridges_menu(self) -> None:
        """Its bridges (advanced configuration): adding one, its ports, STP, DHCP option 82
        (bridge mode), enabling or disabling it, its comment and deleting it."""
        title, last = tr("Bridges"), "+"
        while True:
            bridges, before = bridge_list(self.working), bridge_list(self.original)
            items = [(str(i), ("" if b in before else "* ") + self._bridge_label(b))
                     for i, b in enumerate(bridges, 1)]
            items.append(("+", tr("Add a bridge")))
            choice = self.ui.menu(title, tr("The bridges of the web's advanced network "
                                            "configuration. * = changed, not applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "+":
                self._add_bridge(bridges)
                continue
            i = int(choice) - 1
            b = bridges[i]
            acts = [("p", tr("Ports")), ("s", tr("STP: disable it") if b["stp"]
                                         else tr("STP: enable it"))]
            if net_mode(self.working) == "bridge":
                acts.append(("o", tr("DHCP option 82: disable it") if b["option82"]
                             else tr("DHCP option 82: enable it")))
            acts += [("e", tr("Disable the bridge") if b["on"] else tr("Enable the bridge")),
                     ("c", tr("Change its comment")), ("x", tr("Delete"))]
            act = self.ui.menu(title, self._bridge_label(b), acts)
            new = [dict(x) for x in bridges]
            name, use = ui_devname(b["devname"]), in_use(self.working, b["devname"])
            if act == "p":
                ports = self._pick_ports(b["ports"])
                if ports is None:
                    continue
                new[i]["ports"] = ports
            elif act == "s":
                new[i]["stp"] = not b["stp"]
            elif act == "o":
                new[i]["option82"] = not b["option82"]
            elif act == "e":
                if b["on"] and use:
                    self.ui.msgbox(title, tr("{name} cannot be disabled: {reason}.",
                                             name=name, reason=use))
                    continue
                new[i]["on"] = not b["on"]
            elif act == "c":
                comment = self.ui.inputbox(title, tr("Comment:"), b["comment"])
                if comment is None:
                    continue
                new[i]["comment"] = comment.strip()
            elif act == "x":
                if b["devname"] == "br0" and "ath0" in b["ports"]:
                    use = tr("it has the wireless interface")
                if use:
                    self.ui.msgbox(title, tr("{name} cannot be deleted: {reason}.",
                                             name=name, reason=use))
                    continue
                if not self.ui.ask(title, tr("Delete {name}?\n\nAs in its web, its firewall "
                                             "and traffic shaping rules go with it.",
                                             name=name), default_no=True):
                    continue
                del new[i]
            else:
                continue
            self._accept(plan_vlans_bridges(self.working, vlan_list(self.working), new))

    def _pick_ports(self, ports: list[str]) -> list[str] | None:
        """Its ports' list: the ones it has and the free ones (the wireless one stays as it
        is, as in its web); None if cancelled."""
        title = tr("Ports")
        chosen = list(ports)
        offered = list(dict.fromkeys(ports + free_ports(self.working)))
        while True:
            items = [(p, ("[x] " if p in chosen else "[ ] ") + ui_devname(p)) for p in offered]
            items.append(("ok", tr("Done")))
            choice = self.ui.menu(title, tr("Its ports: choose one to put it in or take it out "
                                            "(the free interfaces are offered)."), items,
                                  default="ok")
            if choice is None:
                return None
            if choice == "ok":
                problem = bridge_ports_problem(self.working, chosen)
                if problem:
                    self.ui.msgbox(tr("Invalid value"), problem)
                    continue
                return chosen
            if choice == "ath0":
                self.ui.msgbox(title, tr("As in its web, the wireless interface (WLAN0) cannot "
                                         "be moved."))
                continue
            if choice in chosen:
                chosen.remove(choice)
            else:
                chosen.append(choice)

    def _add_bridge(self, bridges: list[dict]) -> None:
        if not free_ports(self.working):
            self.ui.msgbox(tr("Bridges"), tr("There is no free interface for a new bridge: "
                                             "all of them are in use (add a VLAN first, or "
                                             "take one out of a bridge)."))
            return
        ports = self._pick_ports([])
        if ports is None:
            return
        comment = self.ui.inputbox(tr("Bridges"), tr("Comment (optional):"), "")
        if comment is None:
            return
        bridge = {"devname": next_bridge_name(bridges), "on": True, "stp": False,
                  "ports": ports, "comment": comment.strip(), "option82": False}
        self._accept(plan_vlans_bridges(self.working, vlan_list(self.working),
                                        bridges + [bridge]))
