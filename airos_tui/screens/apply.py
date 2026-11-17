"""App: applying the changes, test mode and following the device to a new IP or to its web
server's new port."""
from __future__ import annotations

import time

from ..core.i18n import tr
from ..core.errors import AirOSError, AuthError
from ..core.config import Config, _merge, NET_RE, PROTECTED_RE, WIFI_RE
from ..device.network import main_iface, net_mode, netmode_label
from ..device.limits import new_problems
from ..device.discovery import discover, norm_mac
from ..device.netmode import mgmt_blocked
from ..device.services import web_endpoint, web_settings
from ..device.system import accounts
from ..device.status import power_report, short_status


# airOS 6: length of test mode ($test_mode_time in /usr/www/lib/settings.inc).
V6_TEST_SECONDS = 180
# airOS 8: what its test mode lasts (its web says about three minutes).
V8_TEST_SECONDS = 180
# Seconds before starting to check whether the device came back after applying changes
# (the web waits about 15 s on airOS 8 and about 21 s on airOS 6).
DEFAULT_WAIT = {"v8": 15, "v6": 20}
MAX_WAIT = 180


class ApplyFlow:
    """Applying the changes, test mode and following the device."""

    # -- applying ---------------------------------------------------------------------

    def apply_menu(self) -> None:
        n = len(self.changes())
        if not n:
            self.ui.msgbox(tr("Apply"), tr("There are no pending changes."))
            return
        items = [("1", tr("Test: it reverts by itself if you do not confirm (recommended)")),
                 ("2", tr("Apply and save for good")),
                 ("3", tr("View the changes")),
                 ("4", tr("Discard the changes (nothing is sent)"))]
        choice = self.ui.menu(tr("Apply changes"), tr("{count} pending change(s).", count=n),
                              items)
        if choice == "1":
            self.apply(test=True)
        elif choice == "2":
            self.apply(test=False)
        elif choice == "3":
            self.show_changes()
        elif choice == "4":
            if self.ui.ask(tr("Discard"), tr("Discard {count} change(s)?", count=n),
                           default_no=True):
                self.working = self.original.copy()

    def _initial_wait(self, res: dict) -> int:
        if self.wait is not None:
            return self.wait
        if res.get("fast_restart"):
            return 4
        return DEFAULT_WAIT.get(self.dev.flavor or "v8", 15)

    def _wait(self, title: str, initial: int, max_wait: int, first, then, probe,
              pause: float) -> bool:
        """The progress bar while the device restarts: first(seconds left) during the
        initial wait; then, every pause seconds, then(seconds) while probe() looks for it.
        probe() returns what to show when it finds it (and the wait ends), or None."""
        gauge = self.ui.gauge(title, tr("Applying…"))
        t0 = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - t0
                if elapsed >= max_wait:
                    return False
                pct = min(99, int(elapsed * 100 / max_wait))
                if elapsed < initial:
                    gauge.update(pct, first(int(initial - elapsed)))
                    time.sleep(1)
                    continue
                gauge.update(pct, then(int(elapsed)))
                found = probe()
                if found:
                    gauge.update(100, found)
                    return True
                time.sleep(pause)
        finally:
            gauge.close()

    def wait_for_device(self, title: str, initial: int, max_wait: int = MAX_WAIT,
                        alt: str | None = None, macs: set[str] | None = None) -> bool:
        """Wait for the device to come back. alt: another IP where it may show up (the
        previous one). macs: its MACs, to check that whatever answers there is this device
        (at a new address, or at the old one, another antenna may answer)."""
        def same(ip):
            return not macs or self._is_same_device(ip, macs)

        def probe():
            if self.dev.ping() and same(self.dev.host):
                return tr("The device answered.")
            if alt and alt != self.dev.host and self.dev.ping(alt) and same(alt):
                self.dev.retarget(alt)
                return tr("The device answered at {ip}.", ip=alt)
            return None
        return self._wait(
            title, initial, max_wait,
            lambda left: tr("The device is applying the configuration…\nI will check whether "
                            "it came back in {seconds} s.", seconds=left),
            lambda spent: tr("Waiting for the device to answer at {host}… ({seconds} s)",
                             host=self.dev.host, seconds=spent),
            probe, pause=2)

    def _is_same_device(self, ip: str, macs: set[str]) -> bool:
        """Does this antenna answer at ip? Another one may (another antenna with the same
        fallback IP, the factory one for example, or a device with that address), so it is
        checked without giving it the user and password: by its MAC, with Ubiquiti discovery
        (which needs no login; if it answers with another MAC, nothing else is sent there)
        or, if discovery does not answer (turned off, or filtered on the way), with
        status.cgi and this session, which another device does not know (it is not logged
        into there). If it is this one, it stays pointed there."""
        if not self.dev.ping(ip):
            return False
        if macs:
            try:
                found = discover(timeout=1.0, target=ip, port=self.discovery.get("port"),
                                 interfaces=[], first=True)
            except OSError:
                found = []
            if found:
                if any(f["macs"] & macs for f in found):
                    self.dev.retarget(ip)
                    return True
                return False
        st = self.dev.status_at(ip)
        seen = {norm_mac(str(i.get("hwaddr") or "")) for i in (st or {}).get("interfaces") or []
                if isinstance(i, dict)}
        if st is not None and (not macs or seen & macs):
            self.dev.retarget(ip)
            return True
        return False

    def _wait_dhcp(self, title: str, initial: int, plan: dict,
                   max_wait: int = MAX_WAIT) -> bool:
        """The new IP is decided by DHCP or PPPoE: look for the antenna by its MAC with
        Ubiquiti discovery, at its fallback IP and at the previous one."""
        def probe():
            # If it keeps the IP we are connected through (mode change), look there first.
            if plan.get("candidates", [])[:1] == [plan["old"]] and \
                    self._is_same_device(plan["old"], plan["macs"]):
                return tr("Still at {ip}.", ip=plan["old"])
            found_list = discover(timeout=2.0, **self.discovery) if self._discovery_on() else []
            for found in found_list:               # (with discovery off nobody would answer)
                if found["macs"] & plan["macs"] and self.dev.ping(found["ip"]):
                    self.dev.retarget(found["ip"])
                    return tr("I found it at {ip}.", ip=found["ip"])
            for ip in plan.get("candidates", []) + [plan["old"]]:
                if ip and self._is_same_device(ip, plan["macs"]):
                    return tr("I found it at {ip}.", ip=ip)
            return None
        return self._wait(
            title, initial, max_wait,
            lambda left: tr("The device is applying the configuration…\nI will look for it "
                            "in {seconds} s.", seconds=left),
            lambda spent: tr("Looking for the antenna by its MAC… ({seconds} s)", seconds=spent),
            probe, pause=1)

    def _web_move(self) -> dict | None:
        """Does the change move the web server this program talks to (another port, HTTPS on
        or off)? {'old': (scheme, port), 'new': (scheme, port)}."""
        if self.dev.flavor != "v8":
            return None
        return self._web_move_to(web_endpoint(web_settings(self.working)),
                                 web_endpoint(web_settings(self.original)))

    def _web_move_to(self, new: tuple[str, int],
                     before: tuple[str, int] | None) -> dict | None:
        """Its web answers at new with the settings applied, and at before with the previous
        ones (None: not known): {'old', 'new'} if this program must go there, None if it stays
        where it talks to it (the HTTP port while HTTPS is on, for example)."""
        if new == before:
            return None
        old = (self.dev.scheme, self.dev.port)
        return None if new == old else {"old": old, "new": new}

    def _user_move(self) -> dict | None:
        """Does the change rename the administrator this program logs in as (airOS 8)?
        {'old': name, 'new': name}."""
        if self.dev.flavor != "v8":
            return None
        before, after = accounts(self.original)["admin"], accounts(self.working)["admin"]
        if before == after or after == self.dev.username:
            return None
        return {"old": self.dev.username, "new": after}

    def _web_url(self, web: tuple[str, int], host: str | None = None) -> str:
        """The URL of its web at web (scheme, port), at host (by default, where the program
        points now)."""
        scheme, port, host = *web, host or self.dev.host
        host = f"[{host}]" if ":" in host else host
        return f"{scheme}://{host}" + ("" if port == (443 if scheme == "https" else 80)
                                       else f":{port}")

    def _follow_device(self, plan: dict, title: str, initial: int) -> bool:
        if plan["new"]:
            self.dev.retarget(plan["new"])
            return self.wait_for_device(title, initial, alt=plan["old"],
                                        macs=plan.get("macs") or None)
        return self._wait_dhcp(title, initial, plan)

    def _verify(self, changes, cfg: Config) -> list[str]:
        return [k for k, _, new in changes
                if not PROTECTED_RE.match(k) and cfg.get(k) != new]

    def _status_when_up(self, wait: float = 30) -> dict:
        """status.cgi once its web answers again: as it comes back, it takes a few more
        seconds to give its data (asked again for up to wait seconds)."""
        deadline = time.monotonic() + wait
        while True:
            try:
                return self.dev.status()
            except AirOSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)

    def _rebase(self) -> bool:
        """Before writing (this program writes the whole configuration): the device as it is
        now. A test in progress stops it (what it sent would go on top of it); a configuration
        that is not the one read (changed by its web or another session, a test that went back
        by itself, a change saved at once…) is shown and, if accepted, the pending changes are
        prepared again on top of it. Its password hashes are always the device's."""
        info = self.dev.test_mode_info()
        if info and info.get("active"):
            self.ui.msgbox(tr("Test mode"), tr(
                'The device has changes in test mode right now: decide on them first ("Test '
                'mode in progress": keep or revert). What is sent now would go on top of them.'))
            return False
        self._follow_ctx = None                  # (no test: where one moved it is over)
        fresh = Config(self.dev.get_config(), self.dev.flavor)
        pending = self.changes()
        drift = [k for k, _, _ in self.original.diff(fresh) if not PROTECTED_RE.match(k)]
        if drift:
            mine = {k for k, _, _ in pending}
            text = (tr("The device's configuration is not the one read: {count} key(s) changed "
                       "since (in its web, another session, a test that went back by "
                       "itself…):", count=len(drift))
                    + "\n  " + "\n  ".join(drift[:12]) + ("\n  …" if len(drift) > 12 else ""))
            clash = [k for k in drift if k in mine]
            if clash:
                text += "\n\n" + tr("These are among your changes too, and yours stay: {keys}",
                                     keys=", ".join(clash[:6]))
            text += "\n\n" + tr("Your {count} pending change(s) are prepared again on top of "
                                 "it, and that is what will be sent. Continue?",
                                 count=len(pending))
            if not self.ui.ask(tr("Changed meanwhile"), text):
                return False
        if fresh.text() != self.original.text():
            self._rebase_onto(fresh.text())
        return True

    def _rebase_onto(self, device_text: str) -> None:
        """The device has device_text: the pending changes go on top of it (what the device
        changed by itself, such as the time it saved, does not become a pending change back to
        what it was)."""
        pending = self.changes()
        self.set_loaded(device_text)
        _merge(self.working, {k: after for k, _, after in pending})

    def apply(self, test: bool) -> None:
        if not self._rebase():
            return
        changes = self.changes()
        if not changes:
            self.ui.msgbox(tr("Apply"), tr("There are no pending changes."))
            return
        found = new_problems(self.original, self.working, self.limits)
        errors = [p.msg for p in found if p.level == "error"]
        if errors:
            self.ui.msgbox(tr("Cannot apply"), tr("Fix this before applying:") + "\n\n"
                           + "\n\n".join(errors))
            return
        warns = [p.msg for p in found if p.level == "warning"]
        follow, web, user = self._follow_plan(), self._web_move(), self._user_move()
        if not self.ui.ask(tr("Test changes") if test else tr("Apply and save"),
                           self._apply_question(changes, test, follow, warns, web, user),
                           default_no=(not test) or bool(warns)):
            return

        backup = self._safety_backup()
        if backup is None:
            return
        self.ui.infobox(tr("Sending"), tr("Sending the configuration to the device…"))
        res = self.dev.write_config(self.working, test=test)
        t_sent = time.monotonic()                 # (its test time counts from here)
        if web:                                   # (its web answers there from now on)
            self.dev.retarget_web(*web["new"])
        if user:                                  # (and logging in needs the new name)
            self.dev.username = user["new"]
        title = tr("Test mode") if test else tr("Applying")
        if follow:
            ok = self._follow_device(follow, title, self._initial_wait(res))
        else:
            ok = self.wait_for_device(title, self._initial_wait(res))
        if not ok:
            if follow:
                msg = [tr("I could not reach the antenna at its new address: {target}.",
                          target=self._follow_target(follow, found=False))]
                if test:
                    self.dev.retarget(follow["old"])
                    msg += ["", tr("It was in TEST MODE: if it is not confirmed, it goes back "
                                   "to {old} by itself in a few minutes. The program already "
                                   "points there.", old=follow["old"]),
                            tr('To keep it, connect airos-tui to its new address from a network '
                               'that reaches it, before that time runs out, and use "Test mode '
                               'in progress" there.')]
                else:
                    msg += ["", tr("The configuration is already saved: connect to the new "
                                   "address from a network that reaches it.")]
            else:
                msg = [tr("The device does not answer at {address}.", address=self.dev.base)]
                if test:
                    msg += ["", tr("It was in TEST MODE: if it is not confirmed, it goes back to "
                                   "the previous configuration by itself in a few minutes.")]
            if web and test:
                self.dev.retarget_web(*web["old"])
                msg += [tr("Its web will then answer at {address} again: the program already "
                           "points there.", address=self._web_url(web["old"]))]
            if user and test:
                self.dev.username = user["old"]
            msg += ["", tr("Local backup: {path}", path=backup)]
            self.ui.msgbox(tr("No answer"), "\n".join(msg))
            return
        if test:                                 # (where this test moves it, if anywhere)
            self._follow_ctx = dict({"old": follow["old"], "new": self.dev.host} if follow
                                    else {}, **({"web_old": web["old"], "web_new": web["new"]}
                                                if web else {}),
                                    **({"user_old": user["old"], "user_new": user["new"]}
                                       if user else {})) if follow or web or user else None
        self.ui.infobox(tr("Checking"), tr("Reading the configuration that remained active…"))
        self.status = self._status_when_up()
        now_cfg = Config(self.dev.get_config(), self.dev.flavor)
        missing = self._verify(changes, now_cfg)
        if test:
            self._decide_test(changes, missing, t_sent)
            return
        self.set_loaded(now_cfg.text())
        report = power_report(changes, self.status)
        report = f"\n\n{report}" if report else ""
        if missing:
            self.ui.msgbox(tr("Attention"), tr("The device answered, but these keys did not keep "
                                               "the value sent:\n  {keys}",
                                               keys="\n  ".join(missing[:15])) + report)
        else:
            self.ui.msgbox(tr("Done"), tr("Changes applied and saved.") + "\n\n"
                           + short_status(self.status) + report)

    def _apply_question(self, changes, test: bool, follow: dict | None,
                        warns: list[str], web: dict | None = None,
                        user: dict | None = None) -> str:
        """What applying will send and what it may cut, before asking to go on."""
        net = [k for k, _, _ in changes if NET_RE.match(k)]
        wifi = [k for k, _, _ in changes if WIFI_RE.match(k)]
        lines = [tr("{count} change(s) will be sent to {address}.", count=len(changes),
                    address=self.dev.base), ""]
        if not test:
            lines.append(tr("The changes are applied and SAVED directly, with no automatic\n"
                            "way back."))
        elif self.dev.flavor == "v6":
            lines.append(tr("TEST MODE: the device uses the changes without saving them. You "
                            "will have\n{seconds} s to confirm them; otherwise it goes back by "
                            "itself to the current\nconfiguration (also if you lose the "
                            "connection).", seconds=V6_TEST_SECONDS))
        else:
            lines.append(tr("TEST MODE: the device uses the changes without saving them. You "
                            "will have\nabout {seconds} s to confirm them; otherwise it goes "
                            "back by itself to the current\nconfiguration (also if you lose the "
                            "connection).", seconds=V8_TEST_SECONDS))
        old_mode, new_mode = net_mode(self.original), net_mode(self.working)
        if old_mode != new_mode:
            lines += ["", tr("⚠ The network mode changes: {old} → {new}. The antenna's whole "
                             "network is rebuilt.", old=netmode_label(old_mode),
                             new=netmode_label(new_mode))]
        wan = main_iface(self.working, self.dev.flavor) if new_mode != "bridge" else None
        owan = main_iface(self.original, self.dev.flavor) if old_mode != "bridge" else None
        if wan and mgmt_blocked(self.working, wan) and not (owan and mgmt_blocked(self.original,
                                                                                   owan)):
            lines += ["", tr("⚠ Management (web, SSH) will be blocked from the WAN: you will "
                             "only be able to get in through the LAN.")]
        if follow and follow["mode"] == "netmode":
            others = ", ".join(ip for ip in follow["candidates"] if ip != follow["old"])
            if follow["candidates"][:1] == [follow["old"]]:
                text = (tr("⚠ If your PC stays on the same side, the antenna will stay at "
                           "{old}; if it does not answer there, I will look for it by its MAC "
                           "and at {others}.", old=follow["old"], others=others) if others else
                        tr("⚠ If your PC stays on the same side, the antenna will stay at "
                           "{old}; if it does not answer there, I will look for it by its MAC.",
                           old=follow["old"]))
            else:
                text = (tr("⚠ The antenna leaves {old}: I will look for it by its MAC and at "
                           "{others}.", old=follow["old"], others=others) if others else
                        tr("⚠ The antenna leaves {old}: I will look for it by its MAC.",
                           old=follow["old"]))
            lines += ["", text]
        elif follow:
            lines += ["", tr("⚠ The IP you are connected through changes: {old} → {new}.",
                             old=follow["old"], new=self._follow_target(follow)),
                      "  " + tr("The program will reconnect by itself if your computer can "
                                "reach that network.")]
        elif net:
            lines += ["", tr("⚠ You are changing network or web access settings: {keys}",
                             keys=", ".join(net[:3]) + ("…" if len(net) > 3 else "")),
                      "  " + tr("If you stop reaching the device, the test reverts by itself.")]
        if web:
            lines += ["", tr("⚠ Its web moves: {old} → {new}. The program will reconnect there.",
                             old=self._web_url(web["old"]),
                             new=self._web_url(web["new"], (follow or {}).get("new")))]
        if user:
            lines += ["", tr("⚠ The administrator is renamed: {old} → {new}. From then on the "
                             "program logs in with the new name (the password does not "
                             "change).", old=user["old"], new=user["new"])]
        if follow and not follow["new"] and not self._discovery_on():
            where = ", ".join(dict.fromkeys(ip for ip in follow["candidates"] + [follow["old"]]
                                            if ip))
            lines += ["", tr("⚠ Ubiquiti discovery stays disabled: I will not be able to find "
                             "the antenna by its MAC, only at {ips}.", ips=where)]
        if wifi:
            lines += ["", tr("⚠ You are changing wireless settings: if you reach the device "
                             "through\n  that link, you could lose the connection.")]
        if warns:
            lines += ["", tr("⚠ Values the web would not allow:")]
            lines += [f"  • {w}" for w in warns]
        lines += ["", tr("A local backup of the current configuration is saved first."), "",
                  tr("Continue?")]
        return "\n".join(lines)

    def _decide_test(self, changes, missing, t_sent: float) -> None:
        """Keep or revert the test this program started; t_sent: when it was sent (airOS 6
        does not count down the time it shows: it is counted here from then)."""
        ppp = self._pppoe_check(changes)        # before reading the time left
        info = self.dev.test_mode_info()
        if info is not None and not info["active"]:
            ctx, self._follow_ctx = self._follow_ctx, None
            pending = tr("Your changes are still pending in case you want to try again.")
            if not self._went_back(ctx, "\n" + pending):
                return
            self.ui.msgbox(tr("Test mode"),
                           tr("The device is no longer in test mode: the time probably ran out "
                              "and it went back to the previous configuration.") + "\n"
                           + pending)
            self.status = self._status_when_up()
            self._rebase_onto(self.dev.get_config())
            return
        left = info.get("time_left") if info else None
        if self.dev.flavor == "v6":   # airOS 6 does not count down the value it shows
            est = V6_TEST_SECONDS - int(time.monotonic() - t_sent)
            left = min(left, est) if left else est
        lines = [tr("The changes are ACTIVE IN TEST MODE."), "", short_status(self.status)]
        ctx = self._follow_ctx
        if ctx and ctx.get("new") and ctx["new"] != ctx["old"]:
            lines[1:1] = ["", tr("The antenna now answers at {new} (before {old}).",
                                 new=ctx["new"], old=ctx["old"])]
        if ctx and ctx.get("web_old"):
            lines[1:1] = ["", tr("Its web now answers at {new} (before {old}).",
                                 new=self._web_url(ctx["web_new"]),
                                 old=self._web_url(ctx["web_old"], ctx.get("old")))]
        report = power_report(changes, self.status)
        if report:
            lines += ["", report]
        if ppp:
            lines += ["", ppp]
        if missing:
            lines += ["", tr("⚠ These keys did not keep the value sent:"),
                      "  " + ", ".join(missing[:6])]
        if left is not None:
            lines += ["", tr("Time to decide: about {seconds} s.", seconds=max(0, left))]
        lines += ["", tr('"Keep" saves them for good.'),
                  tr('"Revert" goes back to the previous configuration right now.'),
                  tr("If you choose nothing, the device goes back to the previous one by "
                     "itself.")]
        timeout = left - 5 if left else None
        self.resolve_test("\n".join(lines), timeout, changes, missing)

    def _follow_back(self, ctx: dict | None) -> str:
        """The device goes (or went) back to its previous configuration: the program points
        at the address, web and administrator it had before the test (what ctx kept). What to
        tell about it."""
        note = ""
        if not ctx:
            return note
        if ctx.get("new") and ctx["new"] != ctx["old"]:
            self.dev.retarget(ctx["old"])
            note += "\n" + tr("When it reverts it will go back to {old}; the program already "
                              "points there.", old=ctx["old"])
        if ctx.get("web_old"):
            self.dev.retarget_web(*ctx["web_old"])
            note += "\n" + tr("Its web will then answer at {address} again: the program "
                              "already points there.", address=self._web_url(ctx["web_old"]))
        if ctx.get("user_old"):
            self.dev.username = ctx["user_old"]
        return note

    def _went_back(self, ctx: dict | None, pending_note: str = "") -> bool:
        """The test is over and the device goes back to its previous configuration: the program
        points there and waits for it to answer (it may be restarting). False, after saying
        so, if it does not."""
        self._follow_back(ctx)
        if self.wait_for_device(tr("Checking"), 0, max_wait=MAX_WAIT):
            return True
        self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address}.",
                                           address=self.dev.base) + pending_note)
        return False

    def _find_after_test(self, ctx: dict | None) -> str | None:
        """A test ended just as it was being decided (the device stopped answering): where it
        answers now, at its previous address, web and administrator ("old": it went back) or
        at the new ones ("new": it kept them; "same" if they are the same). The program points
        there; None if it answers at neither (then it points at the previous ones, where it
        ends up when the test time is over). Only asked where its web is, without logging in:
        both addresses were this device's, but its previous one not if discovery says another
        device has it now, and its previous web not by an answer that only sends to the new
        one (its HTTP once the test turned its HTTPS on). Where only its administrator
        changed, the one it takes says which."""
        now = (self.dev.host, (self.dev.scheme, self.dev.port),
               (ctx or {}).get("user_new") or self.dev.username)
        self._follow_back(ctx)
        before = (self.dev.host, (self.dev.scheme, self.dev.port), self.dev.username)
        if before[:2] == now[:2]:            # (the same address and web: only one place)
            if not self.wait_for_device(tr("Checking"), 0, max_wait=MAX_WAIT):
                return None
            return self._which_admin(before[2], now[2])
        macs, found = self._device_macs(), {}

        def probe():
            for name, (host, web, user) in (("old", before), ("new", now)):
                if not self.dev.ping(host, web=web, moved_ok=name == "new" or web == now[1]):
                    continue
                if name == "old" and host != now[0] and self._another_device(host, macs):
                    continue
                found.update(name=name, at=(host, web, user))
                return tr("The device answered at {ip}.", ip=host)
            return None
        if not self._wait(tr("Checking"), 0, MAX_WAIT, lambda left: "",
                          lambda spent: tr("Waiting for the device to answer at {host}… "
                                           "({seconds} s)", host=before[0], seconds=spent),
                          probe, pause=2):
            return None
        host, web, user = found["at"]
        self.dev.retarget(host)
        self.dev.retarget_web(*web)
        self.dev.username = user
        return found["name"]

    def _another_device(self, ip: str, macs: set[str]) -> bool:
        """Whether Ubiquiti discovery says another device (another MAC) answers at ip: then
        nothing of this session, nor its password, goes there. Not if discovery does not
        answer there (turned off: nothing says otherwise)."""
        if not macs:
            return False
        try:
            found = discover(timeout=1.0, target=ip, port=self.discovery.get("port"),
                             interfaces=[], first=True)
        except OSError:
            return False
        return bool(found) and not any(f["macs"] & macs for f in found)

    def _which_admin(self, old: str, new: str) -> str:
        """A test that only renamed its administrator ended as it was being decided: "same" if
        it did not; otherwise the name it takes now says what it has ("new": it kept the test;
        "old": it went back), and the program logs in with it."""
        if old == new:
            return "same"
        for name, user in (("new", new), ("old", old)):
            self.dev.username = user
            try:
                self.dev.login()
            except AuthError:
                continue
            return name
        self.dev.username = old                   # (neither: as before, the previous one)
        return "same"

    def resolve_test(self, text: str, timeout: int | None, changes=None,
                     missing=()) -> None:
        """changes=None: this program did not start the test (or forgot it: Esc); what is
        prepared meanwhile goes on top of what the device keeps. However it ends, the program
        points where the device answers then: at the new address, web and administrator if
        the test is kept, at the previous ones if it goes back (also when its time runs out
        just as it is being decided)."""
        own = bool(changes is None and self.changes())
        settle = self._rebase_onto if changes is not None or own else self.set_loaded
        pending_note = ("\n" + tr("Your changes are still pending in case you want to try "
                                  "again.") if changes is not None or own else "")
        ctx, self._follow_ctx = self._follow_ctx, None
        rc = self.ui.yesno(tr("Test mode"), text, yes=tr("Keep"), no=tr("Revert"),
                           timeout=timeout)
        if rc not in (0, 1):                 # (it goes back by itself: the program already too)
            self.ui.msgbox(tr("Not confirmed"), tr("It was not confirmed: the device will go "
                                                   "back to the previous configuration by "
                                                   "itself when the test time is over.")
                           + self._follow_back(ctx) + pending_note)
            return
        self.ui.infobox(tr("Checking"), tr("Checking test mode…"))
        try:
            info = self.dev.test_mode_info()
        except AirOSError:
            # (if the test time has just ended, the device is restarting, with its previous
            # address, web and administrator)
            if not self._find_after_test(ctx):
                self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address}.",
                                                   address=self.dev.base) + pending_note)
                return
            info = self.dev.test_mode_info()
        if info is not None and not info["active"]:
            if not self._went_back(ctx, pending_note):
                return
            self.status = self._status_when_up()
            settle(self.dev.get_config())
            if rc == 0:
                self.ui.msgbox(tr("Too late"), tr("The test time had already ended and the "
                                                  "device went back to the previous "
                                                  "configuration: nothing was saved.")
                               + pending_note)
            else:
                self.ui.msgbox(tr("Reverted"), tr("The device had already gone back to the "
                                                  "previous configuration.") + pending_note)
            return
        if rc == 0:
            self._keep_test(ctx, changes, settle, pending_note, missing)
        else:
            self._revert_test(ctx, settle, pending_note)

    def _keep_test(self, ctx: dict | None, changes, settle, pending_note: str,
                   missing=()) -> None:
        """Keep: the device saves what it tests. If the test time runs out just then, it goes
        back to its previous configuration: the program finds it where it answers. missing:
        the keys that had not taken the value sent already before deciding."""
        self.ui.infobox(tr("Saving"), tr("Saving the configuration for good…"))
        tested, refused = None, None
        try:
            tested = Config(self.dev.get_config(), self.dev.flavor)
            self.dev.confirm_test()
            time.sleep(2)
            info, after_text = self.dev.test_mode_info(), self.dev.get_config()
        except AirOSError as e:
            refused = e                          # (its time ran out just then, or it refused)
            where = self._find_after_test(ctx)
            if where is None:
                self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address}.",
                                                   address=self.dev.base) + pending_note)
                return
            info, after_text = self.dev.test_mode_info(), self.dev.get_config()
            if where == "old":                   # (it went back: nothing was saved)
                self.status = self._status_when_up()
                settle(after_text)
                self.ui.msgbox(tr("Too late"), tr("The test time had already ended and the "
                                                  "device went back to the previous "
                                                  "configuration: nothing was saved.")
                               + pending_note)
                return
        after = Config(after_text, self.dev.flavor)
        self.status = self.dev.status()
        if changes is not None:
            lost = self._verify(changes, after)
        else:
            lost = [k for k, _, _ in (tested if tested is not None else after).diff(after)
                    if not PROTECTED_RE.match(k)]
        if info is not None and info["active"]:
            self._follow_ctx = ctx               # (undecided: "Test mode in progress" follows it)
            self.ui.msgbox(tr("Attention"), tr("The device is still in test mode. Check the "
                                               "web.") + (f"\n\n{refused}" if refused else ""))
        elif lost and set(lost) <= set(missing):    # (the rest was saved)
            settle(after_text)
            self.ui.msgbox(tr("Attention"), tr("Saved for good, except these keys, which the "
                                               "device had not taken (as it said before "
                                               "deciding):\n  {keys}",
                                               keys="\n  ".join(lost[:10])) + pending_note)
        elif lost:
            settle(after_text)
            self.ui.msgbox(tr("Attention"), tr("The device did not keep these changes (the "
                                               "test time probably ran out just "
                                               "before):\n  {keys}",
                                               keys="\n  ".join(lost[:10])) + pending_note)
        else:
            (self.set_loaded if changes is not None else settle)(after_text)
            self.ui.msgbox(tr("Done"), tr("Changes saved for good."))

    def _revert_test(self, ctx: dict | None, settle, pending_note: str) -> None:
        """Revert: the device goes back to its previous configuration, and the program with it
        (to its previous address, web and administrator)."""
        self.ui.infobox(tr("Reverting"), tr("Going back to the previous configuration…"))
        try:
            self.dev.discard_test()
        except AirOSError as e:
            # (its time ran out just then, and it is going back by itself; or it refused, and
            # it is still testing where the program points)
            try:
                still = (self.dev.test_mode_info() or {}).get("active")
            except AirOSError:
                still = False
            if still:
                self._follow_ctx = ctx
                self.ui.msgbox(tr("Attention"), tr("The device did not go back to the previous "
                                                   "configuration: it is still in test mode "
                                                   "({error}).", error=e))
                return
        moved = bool(ctx and ctx.get("new") and ctx["new"] != ctx["old"])
        alt = self.dev.host if moved else None
        self._follow_back(ctx)
        ok = self.wait_for_device(tr("Reverting"), self._initial_wait({}), alt=alt)
        if not ok:
            self.ui.msgbox(tr("No answer"), tr("The device does not answer after reverting "
                                               "(I looked for it at {host}).",
                                               host=self.dev.host))
            return
        self.status = self._status_when_up()
        settle(self.dev.get_config())
        self.ui.msgbox(tr("Reverted"), tr("The device went back to the previous "
                                          "configuration.") + pending_note)

    def pending_test(self, startup: bool = False) -> None:
        try:
            info = self.dev.test_mode_info()
        except AirOSError:
            ctx = self._follow_ctx
            if not ctx:
                raise
            # (its own test moved the device, which does not answer there now: it may have
            # gone back by itself)
            where = self._find_after_test(ctx)
            if where in (None, "old"):
                self._follow_ctx = None
            if where is None:
                self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address}.",
                                                   address=self.dev.base))
                return
            info = self.dev.test_mode_info()
        if not info or not info["active"]:
            self._follow_ctx = None              # (no test: where one moved it is over)
            if not startup:
                self.ui.msgbox(tr("Test mode"), tr("This device has no changes in test mode."))
            return
        self.status = self.dev.status()
        left = info.get("time_left")
        text = (tr("This device has changes ACTIVE IN TEST MODE.") + "\n\n"
                + short_status(self.status)
                + ("\n\n" + tr("Time left: up to {seconds} s.", seconds=left) if left else "")
                + "\n\n" + tr('"Keep" saves them; "Revert" goes back to the saved '
                              'configuration.\nIf you choose nothing, the device reverts by '
                              'itself.'))
        timeout = left - 5 if (left and self.dev.flavor == "v8") else None
        self.resolve_test(text, timeout)
