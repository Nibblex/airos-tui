"""App: the web's network tools (airOS 8): Network Ping, Traceroute and Speed Test, run by
the device, each one live and then its table."""
from __future__ import annotations

import random
import threading
import time
import uuid

from ..core.i18n import tr
from ..core.errors import AirOSError
from ..core.config import _int, js_parse_float
from ..device.tools import (PING_COUNT, PING_SIZE, ping_problem, ping_text, SPEEDTEST_DIRECTIONS,
                            SPEEDTEST_DURATION, speedtest_done, SPEEDTEST_PORT, speedtest_problem,
                            speedtest_text, speedtest_values, TRACE_MAX_HOPS, trace_problem,
                            trace_row, trace_text)


class NetworkToolsScreens:
    """What these tools were given is kept during the session (the other device's password
    of the speed test only if asked, and only in memory)."""

    def _tool_settings(self, name: str, defaults: dict) -> dict:
        return self.tools.setdefault(name, dict(defaults))

    def _v8_tool(self, title: str) -> bool:
        if self.dev.flavor == "v8":
            return True
        self.ui.msgbox(title, tr("On airOS 6 this tool is in the device's web (Tools)."))
        return False

    def _ask_destination(self, title: str, current: str) -> str | None:
        """The web's "Select Destination IP": one of the addresses the device offers
        (ipscan.cgi) or one written by hand, with its rules."""
        try:
            ips = self.dev.ip_scan()
        except AirOSError:
            ips = []
        if ips:
            items = [("m", tr("specify manually"))] + [(str(i), ip)
                                                       for i, ip in enumerate(ips, 1)]
            default = next((str(i) for i, ip in enumerate(ips, 1) if ip == current), "m")
            choice = self.ui.menu(title, tr("Select Destination IP"), items, default=default)
            if choice is None:
                return None
            if choice != "m":
                return ips[int(choice) - 1]

        def parse(text):
            problem = ping_problem("ip", text)
            return (None, problem) if problem else (text.strip(), None)
        return self._ask_valid(title, tr("Destination IP") + ":", current, parse)

    def _ask_text(self, title: str, prompt: str, current, problem):
        """Ask until problem(text) finds nothing wrong: the text, trimmed; None if
        cancelled."""
        def parse(text):
            error = problem(text)
            return (None, error) if error else (text.strip(), None)
        return self._ask_valid(title, prompt, current, parse)

    def _ask_number(self, title: str, prompt: str, current, problem):
        def parse(text):
            error = problem(text)
            return (None, error) if error else (int(text.strip()), None)
        return self._ask_valid(title, prompt, current, parse)

    # -- Network Ping --

    def ping_tool(self) -> None:
        """The web's Network Ping: the device pings the destination as many times as asked,
        one request each (POST /pingtest_action.cgi)."""
        title = tr("Network Ping")
        if not self._v8_tool(title):
            return
        st = self._tool_settings("ping", {"ip": "", "count": PING_COUNT, "size": PING_SIZE})
        last = "s"
        while True:
            items = [("s", tr("Start")),
                     ("d", tr("Destination IP: {ip}", ip=st["ip"] or "-")),
                     ("c", tr("Packet Count: {count}", count=st["count"])),
                     ("z", tr("Packet Size: {size}", size=st["size"]))]
            choice = self.ui.menu(title, tr("The device pings the destination, as the ping of "
                                            "its web does."), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "d" or (choice == "s" and not st["ip"]):
                ip = self._ask_destination(title, st["ip"])
                if ip is None:
                    continue
                st["ip"] = ip
            if choice == "c":
                value = self._ask_number(title, tr("Packet Count") + ":", st["count"],
                                         lambda t: ping_problem("count", t))
                st["count"] = st["count"] if value is None else value
            elif choice == "z":
                value = self._ask_number(title, tr("Packet Size") + ":", st["size"],
                                         lambda t: ping_problem("size", t))
                st["size"] = st["size"] if value is None else value
            elif choice == "s":
                self._run_ping(title, st)

    def _run_ping(self, title: str, st: dict) -> None:
        results: list[dict] = []
        state = {"done": False, "error": None}

        def render():
            if not state["done"]:
                try:
                    results.append(self.dev.ping_once(st["ip"], st["size"]))
                except AirOSError as e:
                    state["error"] = str(e)
                state["done"] = state["error"] is not None or len(results) >= st["count"]
            return tr("Pinging {ip}…", ip=st["ip"]) + "\n\n" + ping_text(results)
        self.ui.watch(title, render, interval=0.2, until=lambda: state["done"])
        text = ping_text(results)
        if state["error"]:
            text += "\n\n" + tr("Error") + ": " + state["error"]
        self.ui.textbox(tr("Results") + f" · {st['ip']}", text)

    # -- Traceroute --

    def traceroute_tool(self) -> None:
        """The web's Traceroute: the device finds the way to the destination hop by hop, one
        request each (POST /api/traceroute), up to 30."""
        title = tr("Traceroute")
        if not self._v8_tool(title):
            return
        st = self._tool_settings("traceroute", {"host": "", "resolve": False})
        last = "s"
        while True:
            items = [("s", tr("Start")),
                     ("h", tr("Destination Host: {host}", host=st["host"] or "-")),
                     ("r", tr("Resolve IP Addresses: {state}", state=(
                         tr("yes") if st["resolve"] else tr("no"))))]
            choice = self.ui.menu(title, tr("The device finds the way to the destination, as "
                                            "the traceroute of its web does."), items,
                                  default=last)
            if choice is None:
                return
            last = choice
            if choice == "h" or (choice == "s" and not st["host"]):
                host = self._ask_text(title, tr("Destination Host") + ":", st["host"],
                                      trace_problem)
                if host is None:
                    continue
                st["host"] = host
            if choice == "r":
                st["resolve"] = not st["resolve"]
            elif choice == "s":
                self._run_traceroute(title, st)

    def _run_traceroute(self, title: str, st: dict) -> None:
        rows: list[dict] = []
        state = {"done": False, "error": None, "target": st["host"], "hop": 1}

        def render():
            if not state["done"]:
                try:
                    res = self.dev.traceroute_hop(state["target"], state["hop"], st["resolve"])
                    if res.get("error"):
                        state["error"] = str(res["error"])
                    else:
                        if not res.get("timeout") and res.get("target_ip"):
                            state["target"] = str(res["target_ip"])
                        rows.append(trace_row(res))
                        state["done"] = bool(res.get("last_hop"))
                except AirOSError as e:
                    state["error"] = str(e)
                state["hop"] += 1
                state["done"] = (state["done"] or state["error"] is not None
                                 or state["hop"] > TRACE_MAX_HOPS)
            return (tr("Tracing the way to {host}…", host=st["host"]) + "\n\n"
                    + trace_text(rows, state["error"]))
        self.ui.watch(title, render, interval=0.2, until=lambda: state["done"])
        self.ui.textbox(tr("Results") + f" · {st['host']}", trace_text(rows, state["error"]))

    # -- Speed Test --

    def speedtest_tool(self) -> None:
        """The web's Speed Test: the throughput between the device and another one (with its
        web's user and password), by GET /sptest_action.cgi."""
        title = tr("Speed Test")
        if not self._v8_tool(title):
            return
        st = self._tool_settings("speedtest", {
            "ip": "", "port": SPEEDTEST_PORT, "user": "", "password": "", "direction": "dx",
            "duration": SPEEDTEST_DURATION, "remember": False})
        last = "s"
        try:
            while True:
                items = [("s", tr("Start")),
                         ("d", tr("Destination IP: {ip}", ip=st["ip"] or "-")),
                         ("u", tr("User: {user}", user=st["user"] or "-")),
                         ("p", tr("Password: {password}", password=(
                             "••••••" if st["password"] else "-"))),
                         ("w", tr("Remote Web Port: {port}", port=st["port"])),
                         ("r", tr("Direction: {direction}", direction=tr(
                             SPEEDTEST_DIRECTIONS[st["direction"]]))),
                         ("t", tr("Duration: {seconds} seconds", seconds=st["duration"])),
                         ("m", tr("Remember credentials for this session: {state}", state=(
                             tr("yes") if st["remember"] else tr("no"))))]
                choice = self.ui.menu(title, tr(
                    "The throughput between this device and another one, as the speed test of "
                    "its web measures it. It loads the link while it lasts."), items,
                    default=last)
                if choice is None:
                    return
                last = choice
                if choice == "s":
                    if self._speedtest_ready(title, st):
                        self._run_speedtest(title, st)
                else:
                    self._speedtest_setting(title, st, choice)
        finally:
            if not st["remember"]:                  # (as its web: only if asked to)
                st["user"] = st["password"] = ""

    def _speedtest_setting(self, title: str, st: dict, choice: str) -> None:
        if choice == "d":
            ip = self._ask_destination(title, st["ip"])
            st["ip"] = st["ip"] if ip is None else ip
        elif choice == "u":
            user = self._ask_text(title, tr("User") + ":", st["user"],
                                  lambda t: speedtest_problem("user", t))
            st["user"] = st["user"] if user is None else user
        elif choice == "p":
            password = self.ui.passwordbox(title, tr("Password of {user} on {ip}:",
                                                     user=st["user"] or "?", ip=st["ip"] or "?"))
            if password is not None and speedtest_problem("password", password):
                self.ui.msgbox(tr("Invalid value"), speedtest_problem("password", password))
            elif password is not None:
                st["password"] = password
        elif choice == "w":
            port = self._ask_text(title, tr("Remote Web Port") + ":", st["port"],
                                  lambda t: speedtest_problem("port", t))
            st["port"] = st["port"] if port is None else port
        elif choice == "r":
            direction = self.ui.menu(title, tr("Direction") + ":", [
                (k, tr(v)) for k, v in SPEEDTEST_DIRECTIONS.items()], default=st["direction"])
            st["direction"] = direction or st["direction"]
        elif choice == "t":
            duration = self._ask_number(title, tr("Duration (seconds):"), st["duration"],
                                        lambda t: speedtest_problem("duration", t))
            st["duration"] = st["duration"] if duration is None else duration
        elif choice == "m":
            st["remember"] = not st["remember"]

    def _speedtest_ready(self, title: str, st: dict) -> bool:
        """What is missing is asked; what breaks its web's rules is said."""
        for choice, key in (("d", "ip"), ("u", "user"), ("p", "password")):
            if not st[key]:
                self._speedtest_setting(title, st, choice)
                if not st[key]:
                    return False
        problems = [p for p in (speedtest_problem("port", st["port"]),
                                speedtest_problem("duration", st["duration"])) if p]
        if problems:
            self.ui.msgbox(tr("Invalid value"), "\n".join(problems))
        return not problems

    def _run_speedtest(self, title: str, st: dict) -> None:
        ticket = random.randrange(1000)
        base = {"ticket": ticket, "target": st["ip"], "port": st["port"], "login": st["user"],
                "passwd": st["password"]}
        ping: dict = {"time": None}
        threading.Thread(target=self._speedtest_ping, args=(st["ip"], ping), daemon=True).start()
        self.ui.infobox(title, tr("Initializing") + "…")
        try:
            for fields in (dict(base, action="remote", airosid=uuid.uuid4().hex),
                           dict(base, action="start", duration=st["duration"],
                                direction=st["direction"])):
                res = self.dev.speed_test(fields)
                if _int(res.get("status")) != 0:
                    self.ui.msgbox(title, tr("Error") + ": " + str(
                        res.get("message") or tr("invalid response received")))
                    return
        except AirOSError as e:
            self.ui.msgbox(title, tr("Error") + ": " + str(e))
            return
        state = {"values": speedtest_values({}, st["direction"]), "done": False,
                 "t0": time.monotonic()}

        def text(stage):
            elapsed = min(time.monotonic() - state["t0"], st["duration"]) * 1000
            return speedtest_text(state["values"], st["direction"], ping["time"], stage, elapsed)

        def render():
            try:
                res = self.dev.speed_test({"ticket": ticket, "action": "status"})
                if str(res.get("ticket")) == str(ticket):
                    state["values"] = speedtest_values(res, st["direction"])
                    state["done"] = speedtest_done(res, st["duration"])
            except AirOSError:
                pass                                # (as its web: it asks again)
            return text(tr("Started")) + "\n\n" + tr("Any key stops it.")
        self.ui.watch(title, render, interval=0.5, until=lambda: state["done"])
        if not state["done"]:
            try:
                self.dev.speed_test({"ticket": ticket, "action": "stop"})
            except AirOSError:
                pass
        self.ui.msgbox(title, text(tr("Finished")))

    def _speedtest_ping(self, ip: str, out: dict) -> None:
        """Its 3 pings to the other device, while the test starts: the shortest time."""
        for _ in range(3):
            try:
                res = self.dev.ping_once(ip, PING_SIZE)
            except AirOSError:
                return
            t = js_parse_float(res.get("time")) if not res.get("timeout") else None
            if t:
                out["time"] = t if out["time"] is None else min(t, out["time"])
