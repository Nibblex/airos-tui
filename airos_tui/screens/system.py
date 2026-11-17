"""App: the System menu, as the web's System page: the device name, the rest of its Device
section, the Management Radio, the date settings, the System Accounts (the administrator's
name and password, the read-only account), the Location and its Device Maintenance (reboot,
reset to factory defaults, support info and the configuration backups)."""
from __future__ import annotations

import glob
import os
import re
import tempfile
import time

from ..core.i18n import N_, tr
from ..core.errors import AirOSError, AuthError, SessionEnded
from ..core.config import Config, DATA_DIR, gen3, _int, _merge, PROTECTED_RE
from ..device.services import web_endpoint, web_settings
from ..device.system import (accounts, admin_name_problem, DATE_KEYS, date_problem, date_settings,
                             DEFAULT_PASSWORD, DEFAULT_URL, DEFAULT_USER, backup_config_part,
                             backup_filename, backup_kind, device_name, device_name_problem,
                             device_name_updates, device_switches, height_saved, height_shown,
                             imperial, language_name, location, location_offered, location_problem,
                             management_radio_offered, management_radio_up, password_problem,
                             plan_accounts, plan_date, plan_location, PROVMODE_KEY,
                             provmode_startup, provmode_state, reset_wait, ro_name_problem,
                             ro_password_problem, support_filename, switch_on, tdd_reporting,
                             tdd_reporting_editable, tdd_reporting_offered, TDD_REPORTING,
                             TDD_REPORTING_KEY, time_zones, ui_language, UI_LANGUAGE_KEY,
                             zone_label, zone_short)
from ..device.status import short_status
from .ui import changed_mark, field_marks

BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_MAX = 512 * 1024                     # bytes: the largest backup it reads


def pick_backup(ui, title: str, init: str = "") -> str | None:
    """A backup of this computer: one of its backups folder (the newest first) or another,
    its path typed (init: what the field starts with). None if cancelled."""
    files = sorted((p for p in glob.glob(os.path.join(glob.escape(BACKUP_DIR), "*.cfg"))
                    if os.path.isfile(p)), key=os.path.getmtime, reverse=True)[:40]
    items = [("o", tr("Another file (type the path)…"))]       # (first: always in sight)
    items += [(str(i), os.path.basename(p)) for i, p in enumerate(files, 1)]
    choice = ui.menu(title, tr("Backups in {path}", path=BACKUP_DIR), items)
    if choice is None:
        return None
    if choice != "o":
        return files[int(choice) - 1]
    path = ui.inputbox(title, tr("Path of the .cfg file:"), init)
    return os.path.expanduser(path.strip()) if path and path.strip() else None


def read_local_file(ui, path: str, limit: int) -> bytes | None:
    """A file of this computer, of limit bytes at most; None, after saying why, if it
    cannot be read."""
    try:
        if os.path.getsize(path) > limit:
            raise ValueError(tr("too large"))
        with open(path, "rb") as f:
            return f.read()
    except (OSError, ValueError) as e:
        ui.msgbox(tr("Error"), tr("Could not read {path}: {error}", path=path, error=e))
        return None


class SystemScreens:
    """The settings of the web's System page that airos-tui has."""

    def system_menu(self) -> None:
        last = "v"
        while True:
            board = self._board()
            name = device_name(self.working, self.dev.flavor, board)
            mark = "* " if name != device_name(self.original, self.dev.flavor, board) else ""
            version = (self.status.get("host") or {}).get("fwversion") or "?"
            items = [("v", tr("Firmware: {version}", version=version)),
                     ("n", f"{mark}{tr('Device name')}: {name or tr('(empty)')}")]
            if self.dev.flavor == "v8":
                extra = self._system_extra_rows()         # (in the order of its page)
                ds = date_settings(self.working)
                items += [r for r in extra if r[0] in "em"]
                items.append(("d", ("* " if ds != date_settings(self.original) else "") + tr(
                    "Date settings: {zone}, NTP {state}", zone=zone_short(ds["zone"], board),
                    state=tr("enabled") if ds["ntp"] else tr("disabled"))))
                items += [r for r in extra if r[0] in "al"]
            items += [("p", tr("Change the administrator password")),
                      ("r", tr("Reboot the device")),
                      ("f", tr("Reset to factory defaults")),
                      ("s", tr("Download the support info")),
                      ("b", tr("Save a backup of the configuration")),
                      ("u", tr("Load a backup of the configuration"))]
            choice = self.ui.menu(tr("System"), tr("The settings of the web's System page. * = "
                                                   "changed, not applied yet."),
                                  items, default=last)
            if choice is None:
                return
            last = choice
            {"v": self.firmware_menu, "n": self.edit_device_name, "d": self.date_menu,
             "e": self.device_menu, "m": self.management_radio_menu, "a": self.accounts_menu,
             "l": self.location_menu, "p": self.change_password, "r": self.reboot,
             "f": self.reset_defaults, "s": self.download_support_info, "b": self.save_backup,
             "u": self.restore_backup}[choice]()

    # -- device name --

    def edit_device_name(self) -> None:
        """The web's Device Name field (also from the quick settings): prepared as the
        other changes, to apply with test mode."""
        label = tr("Device name")
        flavor, board = self.dev.flavor, self._board()
        cur = device_name(self.working, flavor, board)
        value = self.ui.inputbox(label, tr('New value for "{setting}":', setting=label), cur)
        if value is None or value == cur:
            return
        err = device_name_problem(value, flavor)
        if err:
            self.ui.msgbox(tr("Invalid value"), err)
            return
        updates = device_name_updates(value, flavor, board)
        if self._accept(updates):
            self._prepared(label, updates)

    # -- date settings (airOS 8) --

    def date_menu(self) -> None:
        """The web's Date Settings (System page, airOS 8): the time zone, the startup date (not
        on Gen3 boards, as there) and the NTP client with its server, prepared as the rest
        (test mode included)."""
        title = tr("Date Settings")
        board = self._board()
        last = "z"
        while True:
            ds, orig = date_settings(self.working), date_settings(self.original)

            mark = field_marks(ds, orig)
            items = [("z", mark("zone") + tr("Time Zone: {zone}",
                                             zone=zone_label(ds["zone"], board)))]
            if not gen3(board):
                items.append(("d", mark("startup", "date") + (
                    tr("Startup Date: {date}", date=ds["date"]) if ds["startup"]
                    else tr("Startup Date: disabled"))))
            items.append(("n", mark("ntp") + tr("NTP Client: {state}", state=(
                tr("enabled") if ds["ntp"] else tr("disabled")))))
            if ds["ntp"]:
                items.append(("s", mark("server") + tr("NTP Server: {server}",
                                                       server=ds["server"])))
            choice = self.ui.menu(title, tr("The date settings of the web's System page. * = "
                                            "changed, not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "z":
                zones = time_zones(board)
                pick = self.ui.menu(tr("Time Zone"), tr("The device's time zone:"), [
                    (str(i), label) for i, (_, label) in enumerate(zones, 1)], default=next(
                        (str(i) for i, (v, _) in enumerate(zones, 1) if v == ds["zone"]), "1"))
                if pick is not None:
                    self._date_set(dict(ds, zone=zones[int(pick) - 1][0]))
            elif choice == "d":
                self._startup_date(ds)
            elif choice == "n" and ds["ntp"]:
                if self.ui.ask(title, tr("Disable the NTP client?\n\nThe device will no longer "
                                         "set its clock from {server}. UISP needs the correct "
                                         "time.", server=ds["server"])):
                    self._date_set(dict(ds, ntp=False))
            elif choice == "n":
                server = ds["server"] or self._ask_ntp_server(ds["server"])
                if server is not None:
                    self._date_set(dict(ds, ntp=True, server=server))
            elif choice == "s":
                server = self._ask_ntp_server(ds["server"])
                if server is not None:
                    self._date_set(dict(ds, server=server))

    def _startup_date(self, ds: dict) -> None:
        """Turning the startup date on (and its date), changing it, or turning it off."""
        title = tr("Startup Date")
        if ds["startup"]:
            choice = self.ui.menu(title, tr("Startup Date: {date}", date=ds["date"]), [
                ("c", tr("Change the date")), ("o", tr("Turn it off"))])
            if choice == "o":
                self._date_set(dict(ds, startup=False))
            if choice != "c":
                return

        def parse(text):
            problem = date_problem("date", text)
            return (None, problem) if problem else (text.strip(), None)
        day = self._ask_valid(title, tr("The date the device takes when it starts up, until "
                                        "NTP (if it is on) sets its clock. As its web writes "
                                        "it, MM/DD/YYYY:"), ds["date"], parse)
        if day is not None:
            self._date_set(dict(ds, startup=True, date=day))

    def _ask_ntp_server(self, current: str) -> str | None:
        def parse(text):
            problem = date_problem("server", text)
            return (None, problem) if problem else (text.strip(), None)
        return self._ask_valid(tr("NTP Server"), tr("NTP Server") + ":", current, parse)

    def _date_set(self, ds: dict) -> None:
        """The date settings prepared as its web saves them; back to what the device has, its
        keys as they were (nothing pending)."""
        if ds == date_settings(self.original):
            self._accept({k: self.original.get(k) for k in DATE_KEYS})
        else:
            self._accept(plan_date(ds))

    # -- administrator password --

    def change_password(self) -> None:
        """As the web's "Change Password" dialog (airOS 8): the current password, the new one
        twice, its rules; the device checks the current one and saves the new one at once."""
        title = tr("Change Password")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the password is changed from the device's "
                                     "web (System page)."))
            return
        if self._test_running(title):          # (it saves the new one at once)
            return
        current = self.ui.passwordbox(title, tr("Current password of {user}:",
                                                user=self.dev.username))
        if current is None:
            return
        while True:
            new = self.ui.passwordbox(title, tr("New password (from 12 characters; with 20 or "
                                                "more, any):"))
            if new is None:
                return
            err = password_problem(new, current)
            if err:
                self.ui.msgbox(tr("Invalid value"), err)
                continue
            again = self.ui.passwordbox(title, tr("Verify New Password:"))
            if again is None:
                return
            if again == new:
                break
            self.ui.msgbox(tr("Invalid value"), tr("Passwords should match."))
        if not self.ui.ask(title, tr("As its web does, the device changes the password right "
                                     "away and saves it, without test mode.\n\nKeep the new "
                                     "one safe: without it you would have to reset the device "
                                     "to its defaults.\n\nChange the password of {user}?",
                                     user=self.dev.username), default_no=True):
            return
        self.ui.infobox(title, tr("Changing the password…"))
        if not self.dev.change_password(new, current):
            self.ui.msgbox(title, tr("Wrong Current Password"))
            return
        self.ui.msgbox(title, tr("Administrator password successfully changed."))

    # -- the rest of its System page (airOS 8): Device, Management Radio, Accounts, Location

    def _languages(self, quiet: bool = False) -> list[dict] | None:
        """The languages its web offers (read once; quiet: None if it cannot)."""
        if getattr(self, "_ui_langs", None) is None:
            try:
                self._ui_langs = self.dev.ui_languages()[1]
            except AirOSError as e:
                if not quiet:
                    self.ui.msgbox(tr("Interface Language"), str(e))
                return None
        return self._ui_langs

    def _device_rows(self, cfg: Config) -> dict:
        """What its Device section shows (but the name), to mark what changed."""
        board, st = self._board(), self._wstate(cfg)
        rows = {"lang": ui_language(cfg)}
        rows.update({key: switch_on(cfg, key) for key, _, _ in device_switches(board)})
        if tdd_reporting_offered(st, board):
            rows["tdd"] = tdd_reporting(cfg)
        return rows

    def _system_extra_rows(self) -> list[tuple[str, str]]:
        """The System menu's rows for the rest of its page (airOS 8)."""
        board, cfg, orig = self._board(), self.working, self.original
        langs = self._languages(quiet=True) or []
        rows = [("e", changed_mark(self._device_rows(cfg), self._device_rows(orig)) + tr(
            "Device: interface language {language}",
            language=language_name(ui_language(cfg), langs)))]
        if management_radio_offered(board):
            on = provmode_startup(cfg)
            rows.append(("m", changed_mark(on, provmode_startup(orig)) + self._startup_row(on)
                         + " · " + (tr("on now") if management_radio_up(self.status)
                                    else tr("off now"))))
        acc = accounts(cfg)
        rows.append(("a", changed_mark(acc, accounts(orig)) + (
            tr("System Accounts: {admin}; read-only account {name}", admin=acc["admin"],
               name=acc["ro_name"] or "—") if acc["ro"]
            else tr("System Accounts: {admin}; no read-only account", admin=acc["admin"]))))
        if location_offered(board):
            loc = location(cfg)
            rows.append(("l", changed_mark(loc, location(orig)) + (
                tr("Location: {latitude}, {longitude}", latitude=loc["lat"] or "—",
                   longitude=loc["lon"] or "—") if loc["lat"] or loc["lon"]
                else tr("Location: not set"))))
        return rows

    def _startup_row(self, on: bool) -> str:
        return (tr("Management Radio on startup: enabled") if on
                else tr("Management Radio on startup: disabled"))

    def device_menu(self) -> None:
        """The rest of the web's Device section: its interface language and the switches it
        shows on this board (and, on an access point, the TDD capacity reporting)."""
        title, last = tr("Device"), "l"
        tags = {"system.external.reset": "x", "airview.status": "v",
                "system.poe_passthrough": "o", "system.autosend_crash_reports": "c",
                "system.leds.nightmode": "n"}
        while True:
            board, st = self._board(), self._wstate()
            now, before = self._device_rows(self.working), self._device_rows(self.original)

            mark = field_marks(now, before)
            langs = self._languages(quiet=True) or []
            items = [("l", mark("lang") + tr("Interface Language: {language}",
                                             language=language_name(now["lang"], langs)))]
            for key, label, _ in device_switches(board):
                items.append((tags[key], mark(key) + f"{tr(label)}: "
                              + (tr("enabled") if now[key] else tr("disabled"))))
            if "tdd" in now:
                items.append(("t", mark("tdd") + tr(
                    "Fixed TDD Framing Capacity Reporting: {mode}",
                    mode=tr(dict(TDD_REPORTING).get(now["tdd"], N_("Default"))))))
            choice = self.ui.menu(title, tr("The Device section of the web's System page (the "
                                            "device name is in the System menu). * = changed, "
                                            "not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "l":
                langs = self._languages()
                if not langs:
                    continue
                code = self.ui.menu(tr("Interface Language"), tr(
                    "The language of the device's web (not of this program)."),
                    [(x["code"], x.get("lang") or x["code"]) for x in langs], default=now["lang"])
                if code is not None and code != now["lang"]:
                    self._accept({UI_LANGUAGE_KEY: code})
            elif choice == "t":
                if not tdd_reporting_editable(st):
                    self.ui.msgbox(title, tr("Its web only lets you change it on an access "
                                             "point in PtMP, with a frame other than "
                                             "\"Flexible (legacy)\"."))
                    continue
                mode = self.ui.menu(tr("Fixed TDD Framing Capacity Reporting"), "",
                                    [(str(v), tr(label)) for v, label in TDD_REPORTING],
                                    default=str(now["tdd"]))
                if mode is not None and int(mode) != now["tdd"]:
                    self._accept({TDD_REPORTING_KEY: mode})
            else:
                key = next(k for k, t in tags.items() if t == choice)
                self._accept_switch(key, not now[key], before[key])

    def management_radio_menu(self) -> None:
        """The web's Management Radio Settings (Gen2 and Gen3 boards): whether it starts with
        the device, and its button, which turns it on for 15 minutes (or off) right away."""
        title, last = tr("Management Radio Settings"), "s"
        while True:
            on, before = provmode_startup(self.working), provmode_startup(self.original)
            up = management_radio_up(self.status)
            left = provmode_state(self.status)[1]
            items = [("s", ("* " if on != before else "") + self._startup_row(on)),
                     ("r", tr("Management Radio: {action}", action=tr("Disable") if up
                              else tr("Enable for 15 minutes")))]
            now = (tr("Now it is on ({minutes} min left).", minutes=(left + 59) // 60)
                   if up and left else tr("Now it is on.") if up else tr("Now it is off."))
            choice = self.ui.menu(title, tr("Temporarily enable or disable the management "
                                            "radio.") + " " + now + " " + tr(
                "* = changed, not applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "s":
                self._accept_switch(PROVMODE_KEY, not on, before)
                continue
            if not self.ui.ask(title, tr("As its web does, this is done right away, without "
                                         "test mode; then its web restarts (a few seconds).")
                               + "\n\n" + (tr("Turn the management radio off?") if up else
                                           tr("Turn the management radio on for 15 "
                                              "minutes?"))):
                continue
            self.ui.infobox(title, tr("Sending…"))
            self.dev.management_radio(start=not up)
            if not self.wait_for_device(title, 3, max_wait=60):
                self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address}.",
                                                   address=self.dev.base))
                return
            self.status = self._status_when_up()
            self.ui.msgbox(title, tr("The management radio is on.")
                           if management_radio_up(self.status)
                           else tr("The management radio is off."))

    def accounts_menu(self) -> None:
        """The web's System Accounts: the administrator's name and password and, but on Gen3
        boards, the read-only account (on or off, its name and its password)."""
        title, last = tr("System Accounts"), "u"
        while True:
            acc, before = accounts(self.working), accounts(self.original)

            mark = field_marks(acc, before)
            items = [("u", mark("admin") + tr("Administrator Username: {name}",
                                              name=acc["admin"])),
                     ("p", tr("Change Password"))]
            if not gen3(self._board()):
                items.append(("o", mark("ro") + (tr("Read-Only Account: enabled") if acc["ro"]
                                                 else tr("Read-Only Account: disabled"))))
                if acc["ro"]:
                    items += [("n", mark("ro_name") + tr("Read-Only Account Name: {name}",
                                                         name=acc["ro_name"] or "—")),
                              ("w", tr("Change Read-Only User Password"))]
            choice = self.ui.menu(title, tr("The System Accounts of the web's System page. As "
                                            "in its web, the passwords change right away; the "
                                            "rest, with the other changes. * = changed, not "
                                            "applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(acc)
            if choice == "p":
                self.change_password()
                continue
            if choice == "w":
                self._ro_password()
                continue
            if choice == "u":
                name = self._ask_text(title, tr("Administrator Username:"), acc["admin"],
                                      lambda v: admin_name_problem(v, acc), strip=False)
                if name is None or name == acc["admin"]:
                    continue
                new["admin"] = name
            elif choice == "o" and acc["ro"]:
                if not self.ui.ask(title, tr("Disable the read-only account?")):
                    continue
                new["ro"] = False
            elif choice in ("o", "n"):
                # (its password is asked after the name, when it has none yet)
                name = self._ask_text(title, tr("Read-Only Account Name:"), acc["ro_name"],
                                      lambda v: ro_name_problem(v, dict(acc, ro_password=True)),
                                      strip=False)
                if name is None:
                    continue
                if not accounts(self.working)["ro_password"]:
                    self.ui.msgbox(title, tr("Read-Only Account Password required.") + "\n\n"
                                   + tr("Set it now: as in its web, the device saves it right "
                                        "away."))
                    if not self._ro_password():
                        continue
                new.update(ro=True, ro_name=name,
                           ro_password=accounts(self.working)["ro_password"])
            self._accept(plan_accounts(new))

    def _ro_password(self) -> bool:
        """As the web's dialog for the read-only account's password: the new one twice (no
        current one); the device saves it at once."""
        title = tr("Change Read-Only User Password")
        if self._test_running(title):          # (it saves it at once)
            return False
        while True:
            new = self.ui.passwordbox(title, tr("New Password:"))
            if new is None:
                return False
            err = ro_password_problem(new)
            if err:
                self.ui.msgbox(tr("Invalid value"), err)
                continue
            again = self.ui.passwordbox(title, tr("Verify New Password:"))
            if again is None:
                return False
            if again == new:
                break
            self.ui.msgbox(tr("Invalid value"), tr("Passwords should match."))
        if not self.ui.ask(title, tr("As its web does, the device changes the read-only "
                                     "account's password right away and saves it, without "
                                     "test mode.\n\nChange it?")):
            return False
        self.ui.infobox(title, tr("Changing the password…"))
        if not self.dev.change_password(new, "", read_only=True):
            self.ui.msgbox(title, tr("The device did not change the password."))
            return False
        # The device saves it at once and, with it, turns the account on (a real 8.7.14 does):
        # what it has now is the starting point; what was changed here stays pending.
        device = Config(self.dev.get_config(), self.dev.flavor)
        was_on = accounts(self.original)["ro"]
        for k in sorted({k for c in (device, self.original) for k in c.keys()
                         if k.startswith("users.2.")}):
            now, before = device.get(k), self.original.get(k)
            if now == before:
                continue
            for cfg in [self.original] + ([self.working] if self.working.get(k) == before
                                          else []):
                if now is None:
                    cfg.delete(k)
                else:
                    cfg.set(k, now)
        note = ""
        if accounts(self.original)["ro"] and not was_on:
            note = "\n\n" + tr("The device has turned the read-only account on already (it "
                                "does so when its password is set); its name goes with the "
                                "other changes.")
        self.ui.msgbox(title, tr("Read-Only Account Password successfully changed.") + note)
        return True

    def location_menu(self) -> None:
        """The web's Location (boards without GPS): latitude, longitude and the device's
        height, all optional; the height in feet where its web shows feet."""
        title, last = tr("Location"), "a"
        feet = imperial(self._wstate().get("country"))
        unit = "ft" if feet else "m"
        while True:
            loc, before = location(self.working), location(self.original)

            mark = field_marks(loc, before)
            height = height_shown(loc["height"], feet)
            items = [("a", mark("lat") + tr("Latitude: {value}", value=loc["lat"] or "—")),
                     ("o", mark("lon") + tr("Longitude: {value}", value=loc["lon"] or "—")),
                     ("h", mark("height") + tr("Device Height: {value}",
                                               value=f"{height} {unit}" if height else "—"))]
            choice = self.ui.menu(title, tr("The Location of the web's System page (optional: "
                                            "leave a field empty to clear it). * = changed, not "
                                            "applied yet."), items, default=last)
            if choice is None:
                return
            last = choice
            new = dict(loc)
            if choice == "h":
                def parse(text):
                    if text.strip() == (height or ""):     # (as shown: no change)
                        return loc["height"], None
                    meters = height_saved(text, feet)
                    msg = location_problem("height", meters)
                    return (None, msg) if msg else (meters, None)
                value = self._ask_valid(title, tr("Device Height ({unit}):", unit=unit), height,
                                        parse)
                field = "height"
            else:
                field = "lat" if choice == "a" else "lon"
                value = self._ask_text(title, (tr("Latitude:") if field == "lat"
                                               else tr("Longitude:")), loc[field],
                                       lambda v: location_problem(field, v))
            if value is None or value == loc[field]:
                continue
            new[field] = value
            self._accept(plan_location(new))

    # -- device maintenance: reboot --

    def reboot(self) -> None:
        if not self.ui.ask(tr("Reboot"), tr("Reboot {device}?\n\nThe link will be cut for one or "
                                            "two minutes.", device=self.title()),
                           default_no=True):
            return
        pending = self.changes()
        self.dev.reboot()
        if not self.wait_for_device(tr("Rebooting"), 40, max_wait=300):
            self.ui.msgbox(tr("No answer"), tr("The device does not answer yet."))
            return
        self.load(restarted=True)
        # Rebooting applies nothing: what was not applied yet stays pending.
        _merge(self.working, {k: new for k, _, new in pending})
        self.ui.msgbox(tr("Done"), tr("The device answers again."))

    # -- device maintenance: reset to factory defaults --

    def reset_defaults(self) -> None:
        """As the web's "Reset to Factory Defaults" (airOS 8): asked once, the device erases its
        configuration and reboots with the factory one, and this session ends. As before
        applying, a local backup of the current configuration is saved first."""
        title = tr("Reset to Factory Defaults")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the reset to factory defaults is in the "
                                     "device's web (System page)."))
            return
        # What comes after it, as the device says before its login (/api/info/public): with
        # its first setup (setup_complete, or default_password), its web asks on the first
        # access for the country, its terms and a new administrator, and the factory user and
        # password are no way in; without it (older firmware), those.
        info = self.dev.public_info()
        wizard = isinstance(info, dict) and ("setup_complete" in info
                                             or "default_password" in info)
        lines = [tr("Do you want to reset the device to defaults?"), ""]
        if wizard:
            lines.append(tr("The device erases its whole configuration at once, without test "
                            "mode, and reboots with the factory one, at {url}: on the first "
                            "access its web asks for the country, to accept its terms and to "
                            "create the administrator's user and password (airos-tui asks for "
                            "them as you connect). The link and this session are lost.",
                            url=DEFAULT_URL))
        else:
            lines.append(tr("The device erases its whole configuration at once, without test "
                            "mode, and reboots with the factory one: at {url}, user {user} and "
                            "password {password}. The link and this session are lost.",
                            url=DEFAULT_URL, user=DEFAULT_USER, password=DEFAULT_PASSWORD))
        pending = len(self.changes())
        if pending:
            lines += ["", tr("The {count} unapplied change(s) are lost too.", count=pending)]
        lines += ["", tr("A local backup of the current configuration is saved first.")]
        if not self.ui.ask(title, "\n".join(lines), yes=tr("Reset"), no=tr("Cancel"),
                           default_no=True):
            return
        backup = self._safety_backup()
        if backup is None:
            return
        self.dev.reset_defaults()
        if wizard:
            after = tr("In about {seconds} s it answers at {url} and waits for its first setup "
                       "(the country, its terms and the administrator's new user and "
                       "password), which airos-tui asks for when you connect again, as its web "
                       "does. airos-tui ends this session.", seconds=reset_wait(self._board()),
                       url=DEFAULT_URL)
        else:
            after = tr("In about {seconds} s it answers at {url} (user {user}, password "
                       "{password}). airos-tui ends this session: connect again then.",
                       seconds=reset_wait(self._board()), url=DEFAULT_URL, user=DEFAULT_USER,
                       password=DEFAULT_PASSWORD)
        raise SessionEnded(title, "\n".join([
            tr("Device has been reset to defaults. Please wait while it reboots..."), "", after,
            "", tr("Local backup: {path}", path=backup)]))

    # -- the emergency notice (airOS 8) --

    def emergency_notice(self, code) -> None:
        """Its web's notice as it starts (checkForEmergency, emergReasonCode of
        /api/warnings): a reboot by the ping watchdog or a flash memory failure, with
        "Download Support Info" and "Dismiss"; an unexpected reboot (2 or 3) as its crash log
        report, without sending it to Ubiquiti. Until it is dismissed it comes back."""
        code = _int(code)
        if self.dev.flavor != "v8" or code is None or code < 0:
            return
        if code in (2, 3):
            title = tr("Crash Log Report")
            text = "\n\n".join((
                tr("Unexpected reboot detected."),
                tr("Device crashes can be caused by unstable power. Please check PoE adapter, "
                   "ethernet cable and connectors."),
                tr("Its web also offers to send a report to Ubiquiti (from the browser); "
                   "airos-tui does not send it.")))
        else:
            title = tr("Warning")
            text = (tr("Flash memory failure detected.") if code == 1
                    else tr("Device was rebooted by Ping Watchdog."))
        text += "\n\n" + tr("Until you dismiss it, its web shows it each time, and so does "
                              "airos-tui.")
        while True:
            choice = self.ui.menu(title, text, [("s", tr("Download Support Info")),
                                                ("d", tr("Dismiss"))],
                                  ok=tr("Choose"), cancel=tr("Later"))
            if choice == "s":
                self.download_support_info()
                continue
            if choice == "d":
                self.dev.dismiss_warnings()
            return

    # -- device maintenance: support info --

    def download_support_info(self) -> None:
        """As the web's "Download Support Info" (airOS 8): the device puts the file together
        and it is saved here, with the name the web gives it."""
        title = tr("Download Support Info")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the support info is downloaded from the "
                                     "device's web (System page)."))
            return
        path = self._ask_save_path(title, tr("The device's support file: its configuration, "
                                             "logs and statistics, for Ubiquiti support.\n\n"
                                             "Where to save it (a folder or a file):"),
                                   support_filename(self.dev.full_version, self._board()))
        if path is None:
            return
        self.ui.infobox(title, tr("Please be patient while we are generating the support file "
                                  "(about half a minute)."))
        try:
            data = self.dev.support_info()
        except AirOSError as e:
            self.ui.msgbox(title, tr("Unexpected error occurred while getting Support Info")
                           + f"\n\n{e}")
            return
        if self._save_file(path, data):
            self.ui.msgbox(title, tr("Saved in {path} ({size} KB).\n\nIt has the device's "
                                     "configuration and its logs: share it only with whom you "
                                     "trust, such as Ubiquiti support.", path=path,
                                     size=max(1, round(len(data) / 1024))))

    def _ask_save_path(self, title: str, text: str, name: str) -> str | None:
        """Where to save a file of the device, as a browser's download: a folder (then with
        that name) or a file, asking before replacing one. None to cancel."""
        path = self.ui.inputbox(title, text, os.path.join(os.path.expanduser("~"), name))
        if not path or not path.strip():
            return None
        path = os.path.expanduser(path.strip())
        if os.path.isdir(path):
            path = os.path.join(path, name)
        if os.path.exists(path) and not self.ui.ask(
                title, tr("{path} already exists. Replace it?", path=path), default_no=True):
            return None
        return path

    def _read_file(self, path: str, limit: int) -> bytes | None:
        """A file of this computer, of limit bytes at most (see read_local_file)."""
        return read_local_file(self.ui, path, limit)

    def _save_file(self, path: str, data: bytes) -> bool:
        """Write it only for the user (0600): into a new file beside it, which then takes its
        place (one that was there is never written with its own permissions, nor left half
        written). False, after saying why, if it cannot."""
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".airos-tui-", dir=os.path.dirname(
                os.path.abspath(path)))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            tmp = None
        except OSError as e:
            self.ui.msgbox(tr("Error"), tr("Could not save {path}: {error}", path=path,
                                                            error=e))
            return False
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return True

    # -- configuration backups --

    def _write_backup(self, data: str | bytes) -> str:
        """A backup in the backups folder (only for the user), named after the device."""
        os.makedirs(BACKUP_DIR, exist_ok=True)
        os.chmod(BACKUP_DIR, 0o700)
        host = (self.status.get("host") or {}).get("hostname") or "device"
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{host}_{self.dev.host}")
        path = os.path.join(BACKUP_DIR, f"{name}_{time.strftime('%Y%m%d-%H%M%S')}.cfg")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def _safety_backup(self) -> str | None:
        """The backup taken before applying changes or resetting. On airOS 8, the device's own
        file (as its web downloads it, with the password hashes), so it can be uploaded back
        as the web does; if the device does not hand it over, the configuration read. If it
        cannot be written here, it asks whether to go on without it (None: not)."""
        data: str | bytes = self.original_text
        if self.dev.flavor == "v8":
            try:
                data = self.dev.backup_config()
            except AirOSError:
                pass
        try:
            return self._write_backup(data)
        except OSError as e:
            if self.ui.ask(tr("Local backup"), tr(
                    "The local backup could not be written in {folder}: {error}\n\nGo on "
                    "without it?", folder=BACKUP_DIR, error=e.strerror or e), default_no=True):
                return "-"
            return None

    def save_backup(self) -> None:
        """As the web's "Back Up Configuration" (airOS 8): the device's backup file, saved
        here with the name it gives it. airOS 6: the configuration read, in the backups
        folder."""
        if self.dev.flavor != "v8":
            try:
                self.ui.msgbox(tr("Backup saved"), self._write_backup(self.original_text))
            except OSError as e:
                self.ui.msgbox(tr("Local backup"), tr(
                    "The local backup could not be written in {folder}: {error}",
                    folder=BACKUP_DIR, error=e.strerror or e))
            return
        title = tr("Back Up Configuration")
        path = self._ask_save_path(title, tr("The device's configuration backup, as its web "
                                             "downloads it.\n\nWhere to save it (a folder or "
                                             "a file):"),
                                   backup_filename(self.dev.full_version, self._board()))
        if path is None:
            return
        data = self.dev.backup_config()
        if self._save_file(path, data):
            self.ui.msgbox(title, tr("Saved in {path} ({size} KB).\n\nIt has the whole "
                                     "configuration, with the hashes of its passwords: keep it "
                                     "safe.", path=path, size=max(1, round(len(data) / 1024))))

    def restore_backup(self) -> None:
        """Load a backup. On airOS 8 one of its web (cfg.cgi) is uploaded as the web's "Upload
        Configuration" does; one airos-tui saved from what it reads (the passwords as "-"),
        and any on airOS 6, is loaded as pending changes, keeping the device's passwords."""
        init = (os.path.join(os.path.expanduser("~"), backup_filename(
            self.dev.full_version, self._board())) if self.dev.flavor == "v8" else "")
        path = pick_backup(self.ui, tr("Load backup"), init)
        if path is None:
            return
        data = self._read_file(path, BACKUP_MAX)
        if data is None:
            return
        text = data.decode("utf-8", "replace")
        kind = backup_kind(text)
        if kind is None:
            self.ui.msgbox(tr("Error"),
                           tr("That file does not look like an airOS system.cfg."))
            return
        if self.dev.flavor == "v8" and kind == "web":
            self._upload_backup(os.path.basename(path), data)
            return
        new = Config(backup_config_part(text) or "", self.dev.flavor)
        # Passwords and the configuration version stay as the device has them.
        for k in new.keys():
            if PROTECTED_RE.match(k):
                new.delete(k)
        for k in self.original.keys():
            if PROTECTED_RE.match(k):
                new.set(k, self.original.get(k))
        self.working = new
        n = len(self.changes())
        note = (tr("It is a backup airos-tui saved without the passwords, so it is not uploaded "
                   "as the web does: it is loaded as pending changes.") + "\n\n"
                if self.dev.flavor == "v8" else "")
        self.ui.msgbox(tr("Backup loaded"), f"{os.path.basename(path)}\n\n{note}"
                       + tr('{count} difference(s) with the device. Check them in "View pending '
                            'changes" and apply them, preferably in test mode.\nThe device\'s '
                            'passwords are not touched.', count=n))

    def _upload_backup(self, name: str, data: bytes) -> None:
        """As the web's "Upload Configuration" (airOS 8): the file to the device, which keeps
        it to apply or discard; then what its web asks."""
        title = tr("Upload Configuration")
        lines = [tr("Upload {name} to the device, as its web does?", name=name), "",
                 tr("The device keeps it until you apply it (without test mode, as its web does) "
                    "or discard it.")]
        pending = len(self.changes())
        if pending:
            lines += ["", tr("The {count} unapplied change(s) are lost.", count=pending)]
        if not self.ui.ask(title, "\n".join(lines), default_no=True):
            return
        running = self.original
        self.ui.infobox(title, tr("Uploading"))
        self.dev.upload_config(name, data)
        self.load()
        if not self.dev.backup_loaded:
            self.ui.msgbox(title, tr("The device took the file but does not show it as a backup "
                                     "waiting to be applied: check its configuration."))
            return
        self.uploaded_backup(running)

    def uploaded_backup(self, running: Config | None = None) -> None:
        """The web's notice when the device has an uploaded backup waiting (it shows it as it
        starts): apply it or discard it. running: the configuration in use, if known, to
        follow the device if the backup moves the IP this program uses."""
        title = tr("Configuration")
        text = "\n\n".join([tr("Configuration backup file uploaded."),
                             tr("Apply: the device uses it and saves it right away, without test "
                                "mode (as its web does). Discard: it keeps the configuration it "
                                "has.")])
        while True:                      # (the web's notice does not go away either)
            answer = self.ui.yesno(title, text, yes=tr("Apply"), no=tr("Discard"),
                                   default_no=True)      # (no test mode: never by chance)
            if answer == 0:
                self._apply_uploaded(running)
                return
            if answer == 1:
                res = self.dev.discard_config()
                # (then it restarts: read before that, it would still hand over the backup)
                if not self.wait_for_device(title, self._initial_wait(res)):
                    self.ui.msgbox(tr("No answer"), tr("The device does not answer at "
                                                       "{address}.", address=self.dev.base))
                    return
                self.load(restarted=True)
                self.ui.msgbox(title, tr("Backup discarded: the device keeps its configuration."))
                return

    def _login_after_backup(self, title: str) -> bool:
        """Once a backup is applied: whether this program logs in again (a new session, as it
        will when this one ends: the device may keep it, logged in with what it had before).
        If it cannot (the backup has another password, as after a first setup from one), its
        user and password are asked, as its web's login page would. False if cancelled."""
        while True:
            try:
                self.dev.login()
                return True
            except AuthError:
                pass
            user = self.ui.inputbox(title, tr("The backup is applied, but it has another user or "
                                              "password: log in with them to go on.\n\nUser:"),
                                    self.dev.username)
            if not user:
                return False
            password = self.ui.passwordbox(title, tr("Password of {user} on {target}:",
                                                     user=user, target=self.dev.host))
            if password is None:
                return False
            self.dev.username, self.dev.password = user, password

    def _apply_uploaded(self, running: Config | None) -> None:
        """As the web's backupApply: apply.cgi, wait for the device where the backup leaves it
        (its IP, its web, which may answer at another port or with HTTPS on or off, and its
        administrator's name) and read it again."""
        title = tr("Applying the backup")
        backup = self.original                   # what getcfg.cgi hands over while it waits
        plan = (self._follow_plan(running, backup) if running is not None
                else self._backup_plan(backup))
        web = admin = None
        if self.dev.flavor == "v8":
            web = self._web_move_to(web_endpoint(web_settings(backup)),
                                    web_endpoint(web_settings(running))
                                    if running is not None else None)
            admin = accounts(backup)["admin"]
            admin = None if admin == self.dev.username else {"old": self.dev.username,
                                                             "new": admin}
        self.ui.infobox(title, tr("Please wait, applying backup configuration..."))
        res = self.dev.apply_config()
        self.working = backup.copy()             # (what the device uses now: its discovery)
        if web:                                  # (its web answers there from now on)
            self.dev.retarget_web(*web["new"])
        if admin:                                # (and logging in needs its name)
            self.dev.username = admin["new"]
        initial = self._initial_wait(res)
        ok = (self._follow_device(plan, title, initial) if plan
              else self.wait_for_device(title, initial))
        notes = ([tr("Its web now answers at {new} (before {old}).",
                     new=self._web_url(web["new"]), old=self._web_url(web["old"]))]
                 if web else [])
        if admin:
            notes.append(tr("Its administrator is now {new} (before {old}): the program logs in "
                            "with that name.", new=admin["new"], old=admin["old"]))
        if not ok:
            if plan:
                msg = [tr("I could not reach the antenna at its new address: {target}.",
                          target=self._follow_target(plan, found=False)), "",
                       tr("The configuration is already saved: connect to the new address from "
                          "a network that reaches it.")]
            else:
                msg = [tr("The device does not answer at {address}.", address=self.dev.base)]
            self.ui.msgbox(tr("No answer"), "\n".join(msg + ([""] + notes if notes else [])))
            return
        if not self._login_after_backup(title):
            raise SessionEnded(title, tr("The backup is applied and the device answers at "
                                         "{address}, but airos-tui cannot log in again: the "
                                         "backup has another user or password. Connect again "
                                         "with them.", address=self.dev.base))
        self.load(restarted=True)
        self.ui.msgbox(title, tr("Backup applied.") + "\n\n" + short_status(self.status)
                       + ("\n\n" + "\n".join(notes) if notes else ""))
