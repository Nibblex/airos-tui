"""The application: the main menu and its screens. App is built from the mixins of the
other modules here (one per menu); this one has the main loop, the quick settings, the key
editor and the other actions of the main menu."""
from __future__ import annotations

import ipaddress
import os
import traceback

from ..core.i18n import tr
from ..core.errors import AirOSError, AuthError, SessionEnded, SwitchDevice
from ..core.config import Config, DATA_DIR, KEY_NAME_RE, _merge, PROTECTED_RE, is_digits
from ..core.validation import QUICK_SETTINGS, QUICK_WIRELESS, validate_value
from ..device.network import net_mode, parse_mask
from ..device.limits import check_config, DeviceLimits, GENERIC_TX_RANGE, new_problems
from ..device.client import AirOS
from ..device.discovery import BROADCAST, DISCOVERY_PORT
from ..device.netmode import mgmt_vlan_offered
from ..device.services import services_offered
from ..device.wireless import wireless_supported
from ..device.system import date_settings, DEVICE_NAME_KEY, device_name
from ..device.status import format_status
from .ui import UI
from .network import NetworkScreens
from .router import RouterScreens
from .network_advanced import AdvancedNetworkScreens
from .services import ServicesScreens
from .wireless import WirelessScreens
from .wireless_security import WirelessSecurity
from .wireless_advanced import WirelessAdvanced
from .apply import ApplyFlow
from .tools import ToolsScreens
from .airview import AirviewScreens
from .nettools import NetworkToolsScreens
from .system import SystemScreens
from .firmware import FirmwareScreens
from .stations import StationsScreens


class App(NetworkScreens, RouterScreens, AdvancedNetworkScreens, ServicesScreens,
          WirelessScreens, WirelessSecurity, WirelessAdvanced, ApplyFlow, ToolsScreens,
          AirviewScreens, NetworkToolsScreens, SystemScreens, FirmwareScreens,
          StationsScreens):
    """The main menu and its screens, over one device (dev) and its configuration:
    original (as read) and working (with the changes not applied yet)."""
    def __init__(self, ui: UI, dev: AirOS, wait: int | None = None):
        self.ui, self.dev = ui, dev
        self.wait = wait
        self.status: dict = {}
        self.original_text = ""
        self.original = Config()
        self.working = Config()
        self.last_filter = ""
        self.limits: DeviceLimits | None = None
        self.discovery = {"target": BROADCAST, "port": DISCOVERY_PORT}
        self.discovery_target: str | None = None   # --target: where Tools → Discovery looks
        self.discovery_ports: list[int] | None = None  # --ports: those its active search asks
        self.next_target: str | None = None        # Tools → Discovery → Connect: that device
        self.tools: dict[str, dict] = {}           # what each network tool was given
        # where the test being decided moved it (its old and new IP, web and administrator)
        self._follow_ctx: dict | None = None
        self._country_cache: list[tuple[int, str]] | None = None   # the web's country list
        self._repeated_warned: set[str] = set()    # the repeated keys already warned of

    # -- local state ---------------------------------------------------------------

    def set_loaded(self, text: str) -> None:
        self.original_text = text
        self.original = Config(text, self.dev.flavor)
        self.working = self.original.copy()

    def load(self, restarted: bool = False) -> None:
        """Its status and configuration, read again (restarted: it has just come back from a
        restart, and its status.cgi may take a few more seconds to give its data: see
        _status_when_up)."""
        self.ui.infobox(tr("Reading"), tr("Reading the device's status and configuration…"))
        self.status = self._status_when_up() if restarted else self.dev.status()
        self.set_loaded(self.dev.get_config())
        try:
            self.limits = self.dev.device_limits()
        except AirOSError:
            self.limits = None
        self._warn_repeated()

    def _warn_repeated(self) -> None:
        """Keys repeated with different values: neither its web nor the program can tell which
        one the device applies (see Config), and applying anything leaves only the one shown.
        Its web says nothing; that can change the management address unnoticed, so the program
        warns (once for the same keys)."""
        rep = self.original.repeated
        if not rep or set(rep) == self._repeated_warned:
            return
        self._repeated_warned = set(rep)
        lines = []
        for k, values in list(rep.items())[:8]:
            if PROTECTED_RE.match(k):              # (password hashes: their number is enough)
                lines.append(f"  {k}: " + tr("{count} values", count=len(values)))
            else:
                lines.append(f"  {k}: " + " | ".join(values) + "   "
                             + tr("(shown: {value})", value=self.original.get(k)))
        if len(rep) > 8:
            lines.append("  …")
        self.ui.msgbox(tr("Repeated keys"), tr(
            "The device's configuration repeats keys with different values:\n\n{keys}\n\n"
            "Its web shows one of them, as this program does, but the device may be using "
            "another: it applies one according to the order of the lines, and it reorders them "
            "itself when it applies the configuration. Applying any change keeps only the "
            "value shown.", keys="\n".join(lines)))

    def changes(self):
        return self.original.diff(self.working)

    # -- validation against the device limits ----------------------------------------

    def _accept(self, updates: dict[str, str | None]) -> bool:
        """Apply updates to the working copy if they break no limit (None = delete)."""
        return self._accept_cfg(_merge(self.working.copy(), updates))

    def _prepared(self, title: str, updates) -> None:
        """The notice once a change is prepared: the keys it touches, sent with "Apply
        changes"."""
        self.ui.msgbox(title, tr('Change prepared (not sent to the device yet).\n\n'
                                 'Changed keys:\n  {keys}\n\n'
                                 'Use "Apply changes" in the main menu.',
                                 keys="\n  ".join(updates)))

    def _test_running(self, title: str) -> bool:
        """Whether the device has a test in progress (then it says so): what the device saves
        at once would go on top of it."""
        if (self.dev.test_mode_info() or {}).get("active"):
            self.ui.msgbox(title, tr("A test is in progress on the device: confirm it or "
                                     "revert it first."))
            return True
        return False

    def _accept_switch(self, key: str, on: bool, before: bool) -> bool:
        """A switch: "enabled" or "disabled" or, back to what the device means (before), its
        own value, even none (it then reads its default): nothing stays pending."""
        return self._accept({key: self.original.get(key) if on == before
                             else "enabled" if on else "disabled"})

    def _back_to_device(self, up: dict, now: dict, before: dict, off: str | None = None,
                        prefix: str = "") -> dict:
        """up, with the device's own values where the settings are the device's again: all
        the keys up writes when now == before and, with a part off as on the device (its
        switch off; its keys, prefix), that part's: what was typed while it was on does not
        stay pending, out of sight."""
        if now == before:
            return {k: self.original.get(k) for k in up}
        if off and not now[off] and not before[off]:
            return {k: self.original.get(k) if k.startswith(prefix) else v
                    for k, v in up.items()}
        return up

    def _accept_back(self, up: dict, now: dict, before: dict, off: str | None = None,
                     prefix: str = "") -> bool:
        return self._accept(self._back_to_device(up, now, before, off, prefix))

    def _accept_cfg(self, cand: Config) -> bool:
        """cand becomes the working copy if it breaks no limit (warnings ask to confirm).
        Only new problems count: neither those of the working copy nor those the device
        already has (for example, when going back to its network mode)."""
        on_device = set(check_config(self.original, self.limits))
        new = [p for p in new_problems(self.working, cand, self.limits) if p not in on_device]
        errors = [p.msg for p in new if p.level == "error"]
        if errors:
            self.ui.msgbox(tr("Invalid value"), "\n\n".join(errors))
            return False
        warns = [p.msg for p in new if p.level == "warning"]
        if warns and not self.ui.ask(tr("Attention"), "\n\n".join(warns)
                                     + "\n\n" + tr("Use this value anyway?"), default_no=True):
            return False
        self.working = cand
        return True

    def power_hint(self) -> str:
        pl = self.limits.power_limit(self.working) if self.limits else None
        if pl is None:
            lo, hi = GENERIC_TX_RANGE
            return tr("I could not read this device's limits: I accept {lo} to {hi} dBm and "
                      "the device decides the rest.", lo=lo, hi=hi)
        text = tr("This device supports {min} to {max} dBm.", min=pl["min"], max=pl["hw_max"])
        if pl["legal"]:
            text += "\n" + tr("Legal limit (calculated as the web does): {legal}.",
                              legal=pl["legal"])
        elif pl["note"]:
            text += "\n" + tr("No legal limit calculated: {note}.", note=pl["note"])
        return text

    def key_hint(self, key: str) -> str:
        if key == "radio.1.txpower":
            return "\n\n" + self.power_hint()
        if self.limits and key in ("radio.1.freq", "radio.1.center.1.freq", "radio.1.chanbw",
                                   "wireless.1.scan_list.channels",
                                   "radio.1.scan_list.channels"):
            hint = self.limits.freq_hint(self.working)
            bws = self.limits.supported_bw()
            if key == "radio.1.chanbw" and bws:
                hint = tr("Channel widths this device supports: {widths} MHz.",
                          widths=", ".join(map(str, bws)))
            return f"\n\n{hint}" if hint else ""
        return ""

    def _services_label(self) -> str:
        """The main menu's Services entry: what its menu has on this device (on airOS 8 Gen3
        boards, without discovery, CDP, SNMP or Telnet; on airOS 6, discovery and CDP)."""
        if self.dev.flavor != "v8":
            return tr("Services: device discovery and CDP")
        if not services_offered(self.dev.flavor, self.limits.board if self.limits else {}):
            return tr("Services: web, SSH, system log, ping watchdog and UISP")   # (Gen3)
        return tr("Services: web, SSH, SNMP, discovery, ping watchdog, UISP and more")

    def title(self) -> str:
        h = self.status.get("host") or {}
        name = h.get("hostname") or self.dev.host
        model = (h.get("devmodel") or "").strip()
        return f"{name} · {model} · airOS {self.dev.fw}" if model else name

    # -- main loop -------------------------------------------------------------------

    def _do(self, action) -> bool:
        """Run a screen, showing what goes wrong. False if the session ended."""
        try:
            action()
        except SessionEnded as e:          # the device was reset: nothing left to do here
            self.ui.msgbox(e.title, str(e))
            return False
        except SwitchDevice as e:          # Tools → Discovery: another device
            self.next_target = e.target
            return False
        except AuthError as e:
            self.ui.msgbox(tr("Access denied"), str(e))
        except AirOSError as e:
            self.ui.msgbox(tr("Error"), str(e))
        except Exception as e:             # (a slip of this program: what was prepared stays)
            if os.environ.get("AIROS_TUI_STRICT"):
                raise
            self.ui.msgbox(tr("Unexpected error"), tr(
                "Something went wrong in this program: {error}\n\nWhat was prepared is still "
                "pending. The details are in {path}.", error=f"{type(e).__name__}: {e}",
                path=self._error_report()))
        return True

    @staticmethod
    def _error_report() -> str:
        """The traceback of the last unexpected error, only for the user (or "-")."""
        path = os.path.join(DATA_DIR, "last-error.txt")
        try:
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            return "-"
        return path

    def run(self) -> None:
        self.load()
        if self.dev.notes:
            self.ui.msgbox(tr("Notice"), "\n".join(self.dev.notes))
        if not self._do(lambda: self.pending_test(startup=True)):
            return
        # As its web when it starts: a backup uploaded and not applied yet is asked about.
        if self.dev.backup_loaded and not self._do(self.uploaded_backup):
            return
        # As its web when it starts (airOS 8, /api/warnings): the emergency notice (a reboot
        # by the ping watchdog…), an uploaded firmware waiting and the daily check.
        warnings = self.dev.warnings() if self.dev.flavor == "v8" else {}
        self._do(lambda: self.emergency_notice(warnings.get("emergReasonCode")))
        self._do(lambda: self.firmware_startup(warnings))
        actions = {
            "1": self.show_status, "e": self.stations_menu, "g": self.graphs_screen,
            "2": self.quick_settings,
            "w": self.wireless_menu,
            "3": self.network_menu,
            "s": self.services_menu, "t": self.tools_menu, "y": self.system_menu,
            "4": self.edit_keys, "5": self.show_changes,
            "6": self.apply_menu, "7": self.pending_test, "l": self.reload,
        }
        last = "1"
        while True:
            n = len(self.changes())
            if net_mode(self.working) != "bridge":
                network = tr("Network: mode, WAN (static, DHCP or PPPoE), LAN")
            elif mgmt_vlan_offered(self.working, self.dev.flavor, self._board()):
                network = tr("Network: mode (bridge/router), management IP and VLAN, DNS")
            else:
                network = tr("Network: mode (bridge/router), management IP, DNS")
            items = [
                ("1", tr("Device and link status")),
                ("e", self._stations_label()),
                ("g", tr("Graphs: capacity, throughput and latency, live")),
                ("2", tr("Quick settings: name, SSID, WPA2 key, power")),
                ("w", tr("Wireless: mode, SSID, frequency, power, security, MAC ACL…")),
                ("3", network),
                ("s", self._services_label()),
                ("t", tr("Tools: alignment, site survey, discovery, ping, speed test")),
                ("y", tr("System: firmware, device, accounts, location, maintenance, backups")
                 if self.dev.flavor == "v8"
                 else tr("System: firmware, name, password, maintenance, backups")),
                ("4", tr("Search and edit any configuration key")),
                ("5", tr("View pending changes ({count})", count=n)),
                ("6", tr("Apply changes (with test mode)")),
                ("7", tr("Test mode in progress: confirm or revert")),
                ("l", tr("Read the device configuration again")),
                ("0", tr("Exit")),
            ]
            text = tr("Connected to {address} ({user})\nUnapplied changes: {count}",
                      address=self.dev.base, user=self.dev.username, count=n)
            choice = self.ui.menu(self.title(), text, items, default=last,
                                  ok=tr("Choose"), cancel=tr("Exit"))
            if choice in (None, "0"):
                if n and not self.ui.ask(
                        tr("Exit"), tr("There are {count} unapplied change(s) that will be "
                                       "lost.\nExit anyway?", count=n), default_no=True):
                    continue
                return
            last = choice
            if not self._do(actions[choice]):
                return

    # -- screens ---------------------------------------------------------------------

    def show_status(self) -> None:
        self.ui.infobox(tr("Status"), tr("Querying the device…"))
        self.status = self.dev.status()
        ntp = date_settings(self.original)["ntp"] if self.dev.flavor == "v8" else None
        self.ui.textbox(tr("Status · {title}", title=self.title()),
                        format_status(self.status, self.dev.flavor, self.dev.full_version, ntp,
                                      cfg=self.original, board=self._board()))

    def quick_settings(self) -> None:
        last = "1"
        while True:
            items = []
            for i, q in enumerate(QUICK_SETTINGS, 1):
                if q.keys[0] == DEVICE_NAME_KEY:        # shown as the System page shows it
                    cur, orig = (device_name(c, self.dev.flavor, self._board())
                                 for c in (self.working, self.original))
                else:
                    cur, orig = q.current(self.working), q.current(self.original)
                shown = tr("(not on this device)") if cur is None else (cur or tr("(empty)"))
                mark = "* " if cur != orig else ""
                items.append((str(i), f"{mark}{tr(q.label)}: {shown}"))
            choice = self.ui.menu(tr("Quick settings"), tr("Choose the setting to change "
                                                           "(* = changed, not applied yet)."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            q = QUICK_SETTINGS[int(choice) - 1]
            if q.keys[0] == DEVICE_NAME_KEY:            # the same as in System
                self.edit_device_name()
                continue
            label = tr(q.label)
            cur = q.current(self.working)
            if cur is None and not q.create:
                self.ui.msgbox(label, tr("This device does not have that key in its "
                                         "configuration (is wireless security disabled?).\n"
                                         "Set it up in Wireless → Security."))
                continue
            note = self.key_hint(q.keys[0])
            if "aaa.1.wpa.psk" in q.keys and self._wstate()["security"] == "none":
                note = "\n\n" + tr("Careful: its wireless security is disabled, so this key is "
                                   "not used until it is enabled (Wireless → Security).")
            value = self.ui.inputbox(label, tr('New value for "{setting}":', setting=label)
                                     + note, cur or "")
            if value is None or value == cur:
                continue
            err = q.validate(value)
            if err:
                self.ui.msgbox(tr("Invalid value"), err)
                continue
            if q.keys[0] in QUICK_WIRELESS and wireless_supported(self.dev.flavor,
                                                                  self._board()):
                updates = self._quick_wireless(q.keys[0], value)
                if not updates:
                    continue
            else:
                updates = q.updates(self.working, value)
                if not self._accept(updates):
                    continue
            self._prepared(label, updates)

    def _quick_wireless(self, key: str, value: str) -> dict[str, str | None] | None:
        """SSID, WPA key and power, with the keys the Wireless page writes."""
        st = self._wstate()
        if key == "radio.1.txpower":
            changes = {"power": int(value)}
        elif key == "wireless.1.ssid":
            changes = {"ssid": value}
        else:
            wpa = st["security"] == "wpa2" if st["flavor"] == "v8" \
                else st["security"].startswith("wpa")
            if not wpa or st["wpa_auth"] != "WPA-PSK":
                self.ui.msgbox(tr("WPA2 key (PSK)"), tr("The wireless security does not use "
                                                        "a shared key: set it up in Wireless "
                                                        "> Security."))
                return None
            changes = {"psk": value}
        updates = self._wplan(**changes)
        if not updates or not self._accept(updates):
            return None
        return updates

    def _ask_valid(self, title: str, prompt: str, init, parse):
        """Ask until the value is valid: parse(text) gives (value, None) or (None, the
        reason). None if cancelled."""
        init = str(init)
        while True:
            text = self.ui.inputbox(title, prompt, init)
            if text is None:
                return None
            value, error = parse(text)
            if error is None:
                return value
            self.ui.msgbox(tr("Invalid value"), error)
            init = text

    def _ask_ip(self, title: str, prompt: str, init: str = "",
                allow_empty: bool = False) -> str | None:
        def parse(text):
            value = text.strip()
            if not value and allow_empty:
                return "", None
            try:
                if int(ipaddress.IPv4Address(value)) != 0:
                    return value, None
            except ValueError:
                pass
            return None, tr('"{value}" is not a valid IPv4 address (for example '
                            '192.168.1.20).', value=value)
        return self._ask_valid(title, prompt, init, parse)

    def _ask_mask(self, title: str, init: str) -> str | None:
        def parse(text):
            mask = parse_mask(text)
            return (mask, None) if mask else (None, tr('"{value}" is not a valid netmask.',
                                                       value=text))
        return self._ask_valid(title, tr("Netmask (255.255.255.0 or the prefix, for example "
                                         "24):"), init, parse)

    def _ask_int(self, title: str, prompt: str, init, lo: int, hi: int) -> int | None:
        def parse(text):
            value = text.strip()
            if is_digits(value) and lo <= int(value) <= hi:
                return int(value), None
            return None, tr('"{value}" is not valid: type a whole number from {lo} to {hi}.',
                            value=value, lo=lo, hi=hi)
        return self._ask_valid(title, prompt, init, parse)

    # -- configuration keys -----------------------------------------------------------

    def _item_text(self, key: str) -> str:
        cur = self.working.get(key, "")
        shown = cur if len(cur) <= 60 else cur[:57] + "…"
        if PROTECTED_RE.match(key):
            shown += "  " + tr("(protected)")
        return ("* " if self.original.get(key) != cur else "") + (shown or tr("(empty)"))

    def edit_keys(self) -> None:
        filt = self.ui.inputbox(
            tr("Search keys"), tr("Type part of the name or of the value (empty = all).\n"
                                  "Examples: ssid · radio.1 · netconf.3 · wpa"), self.last_filter)
        if filt is None:
            return
        self.last_filter = filt = filt.strip()
        last = None
        while True:
            f = filt.lower()
            keys = sorted(k for k in self.working.keys()
                          if f in k.lower() or f in (self.working.get(k) or "").lower())
            gone = sorted(k for k in self.original.keys()
                          if k not in self.working and f in k.lower())
            items = [("+", tr("[ Add a new key ]"))]
            items += [(k, self._item_text(k)) for k in keys]
            items += [(k, tr("(deleted: choose it to restore it)")) for k in gone]
            head = (tr('{count} key(s) with "{text}". * = modified, not applied yet.',
                       count=len(keys), text=filt) if filt else
                    tr("{count} key(s). * = modified, not applied yet.", count=len(keys)))
            choice = self.ui.menu(tr("Configuration (system.cfg)"), head,
                                  items, default=last, ok=tr("Edit"))
            if choice is None:
                return
            last = choice
            if choice == "+":
                self.add_key()
            else:
                self.edit_one(choice)

    def edit_one(self, key: str) -> None:
        if PROTECTED_RE.match(key):
            self.ui.msgbox(tr("Protected key"), tr('{key} is not edited from here.\n\nPasswords '
                                                   "are changed from the device's web (airOS 8 "
                                                   'does not even hand them over: they show as '
                                                   '"-").', key=key))
            return
        orig = self.original.get(key)
        if key not in self.working:
            if self.ui.ask(tr("Restore key"), tr("Put {key}={value} back?", key=key, value=orig)):
                self.working.set(key, orig)
            return
        cur = self.working.get(key)
        opts = [("1", tr("Change the value")), ("2", tr("Delete this key"))]
        if orig is not None and orig != cur:
            opts.append(("3", tr("Go back to the original value: {value}", value=orig[:50])))
        choice = self.ui.menu(key, tr("Current value:\n{value}", value=cur), opts,
                              ok=tr("Choose"))
        if choice == "1":
            value = self.ui.inputbox(key, tr("New value for\n{key}", key=key)
                                     + self.key_hint(key), cur)
            if value is None or value == cur:
                return
            err = validate_value(key, value, cur)
            if err:
                self.ui.msgbox(tr("Invalid value"), err)
                return
            self._accept({key: value})
        elif choice == "2":
            if self.ui.ask(tr("Delete"), tr("Delete {key}?\n(nothing is sent until you apply the "
                                            "changes)", key=key), default_no=True):
                self._accept({key: None})
        elif choice == "3":
            self.working.set(key, orig)

    def add_key(self) -> None:
        key = self.ui.inputbox(tr("Add key"), tr("Key name (example: resolv.nameserver.2.ip):"))
        if not key:
            return
        key = key.strip()
        if not KEY_NAME_RE.match(key) or PROTECTED_RE.match(key):
            self.ui.msgbox(tr("Invalid name"), tr("Use letters, numbers, dots, hyphens and _."))
            return
        if key in self.working:
            self.edit_one(key)
            return
        value = self.ui.inputbox(tr("Add key"), tr("Value for {key}:", key=key)
                                 + self.key_hint(key))
        if value is None:
            return
        err = validate_value(key, value, self.original.get(key))
        if err:
            self.ui.msgbox(tr("Invalid value"), err)
            return
        self._accept({key: value})

    def show_changes(self) -> None:
        ch = self.changes()
        if not ch:
            self.ui.msgbox(tr("Changes"), tr("There are no pending changes."))
            return
        lines = [tr("{count} pending change(s) (not sent yet):", count=len(ch)), ""]
        for k, a, b in ch:
            if a is None:
                lines.append(f"+ {k} = {b}")
            elif b is None:
                lines.append(f"- {k}   " + tr("(was: {value})", value=a))
            else:
                lines += [f"~ {k}", "      " + tr("was: {value}", value=a),
                          "      " + tr("now: {value}", value=b)]
        self.ui.textbox(tr("Pending changes"), "\n".join(lines))

    # -- other ------------------------------------------------------------------------

    def reload(self) -> None:
        n = len(self.changes())
        if n and not self.ui.ask(tr("Read again"), tr("{count} unapplied change(s) will be "
                                                      "lost.\nContinue?", count=n),
                                 default_no=True):
            return
        self.load()
        if self.dev.backup_loaded:
            self.uploaded_backup()
