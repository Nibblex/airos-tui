"""The discovery screen, on the connection screen and in Tools: what Ubiquiti discovery,
CDP and LLDP and the active search find (device.discovery), in a list to choose the
antenna to connect to, or to search again (somewhere else, or actively)."""
from __future__ import annotations

from ..core.i18n import N_, tr
from ..device.discovery import (ACTIVE_PORTS, active_scan, airos_only, BROADCAST, discover,
                                discovery_host, discovery_line, format_discovered, local_ipv4,
                                merge_active, parse_ports, scan_ranges, setup_legend,
                                _setup_warning, target_problem)


DISCOVERY_SECONDS = 3.0
NOBODY_HINT = N_("No answer may be because:\n"
                 "  · the antenna has discovery turned off (in its web: Services → "
                 "Discovery);\n"
                 "  · it is on another network behind a router: search for it by its IP or "
                 "by that network's range (for example 10.3.0.0/24);\n"
                 "  · a firewall on your PC blocks UDP 10001.")
# Said with discovery alone (not once the active search was done): what finds them anyway.
# active: how to start it there (--active-scan, or the discovery screen's option a).
ACTIVE_HINT = N_("The active search ({active}) also finds antennas with discovery off: it asks "
                 "each address for its web, without a user or password.")


def _where(target: str) -> str:
    return tr("your networks") if target == BROADCAST else target


def _scan(ui, target: str, port: int | None = None) -> list[dict] | None:
    problem = target_problem(target)
    if problem:
        ui.msgbox(tr("Discovery"), problem)
        return None
    ui.infobox(tr("Discovery"), tr("Looking for Ubiquiti antennas on {where} (about {seconds} "
                                   "s)…", where=_where(target),
                                   seconds=f"{DISCOVERY_SECONDS:.0f}"))
    return discover(DISCOVERY_SECONDS, target=target, port=port)


def _connected(d: dict, current) -> bool:
    """Is d the device this session is connected to (by any of its addresses)?"""
    return bool({d["ip"], d["src"], *d["addrs"]} & set(current))


def mark_connected(lines: list[str], connected: list[bool]) -> tuple[list[str], str]:
    """The lines of a list of devices with → before the one this session is connected to (at
    the start, so that it is never cut off), and the legend to add to its text ("" if none
    is)."""
    if not any(connected):
        return lines, ""
    return ([("→ " if c else "  ") + line for line, c in zip(lines, connected)],
            "\n" + tr("→ = the device you are connected to."))


def _connect_to(d: dict) -> str:
    """What to connect to: its IP, or the URL of its web when it is not on its usual port."""
    web = d.get("web")
    if web and web["port"] != (443 if web["scheme"] == "https" else 80):
        return web["url"]
    return d["ip"]


def _active_screen(ui, target: str, ports: list[int]):
    """The active search from the screen: where (a range, an IP, or empty: your networks) and
    which ports, then the search with its progress. (what it found, its notes, where, the
    ports), or None if cancelled or it cannot ask there (it says why)."""
    title = tr("Active search")
    where = ui.inputbox(title, tr("Where to search, address by address: a range such as "
                                  "10.3.0.0/24 (a /22 at most), an IP, or empty = your networks. "
                                  "It asks each address for its web, without a user or a "
                                  "password, and on your networks it also gets each device's "
                                  "MAC with ARP:"), "" if target == BROADCAST else target)
    if where is None:
        return None
    where = (discovery_host(where) or where.strip()) if where.strip() else BROADCAST
    text = ui.inputbox(title, tr("Ports of the web to ask (443 and 80 are its usual ones):"),
                       ", ".join(str(p) for p in ports))
    if text is None:
        return None
    try:
        ports = parse_ports(text)
    except ValueError:
        ui.msgbox(title, tr('"{ports}" are not ports: numbers from 1 to 65535, separated by '
                            'commas.', ports=text.strip()))
        return None
    try:
        scan_ranges(where, local_ipv4())        # (what cannot be asked, before the progress)
    except ValueError as e:
        ui.msgbox(title, str(e))
        return None
    gauge, shown = ui.gauge(title, tr("Asking each address for its web…")), [-1]

    def progress(done: int, total: int, again: bool) -> None:
        pct = done * 100 // total if total else 100
        if pct != shown[0] or done == total:     # (not a redraw per address: a /22 has 1022)
            shown[0] = pct
            gauge.update(pct, tr("Asking again the {total} addresses that did not answer: "
                                 "{done} done…", done=done, total=total) if again else
                         tr("Asking {total} addresses for their web: {done} done…",
                            done=done, total=total))
    try:
        found, notes = active_scan(where, ports, progress=progress)
    finally:
        gauge.close()
    return found, notes, where, ports


def discovery_screen(ui, target: str = BROADCAST, port: int | None = None,
                     current=(), ports=ACTIVE_PORTS) -> str | None:
    """The airOS antennas on the network without logging into any (no user or password
    needed; other devices that answer are not listed): the list, the data of each one and, if
    you want, the IP (or the URL) to connect to. The active search adds what it finds to the
    list; a new search starts it again. current: the addresses of the device this session is
    connected to (marked, and not offered); ports: those the active search proposes."""
    target = discovery_host(target) or target.strip()       # (as --target came: IP:port too)
    found = _scan(ui, target, port) or []
    ports, scanned = list(ports), None
    while True:
        where = _where(target)
        if scanned is not None:
            where = tr("{where} and, with the active search, {scanned}", where=where,
                       scanned=_where(scanned))
        shown = airos_only(found)
        marked = any(_setup_warning(d) for d in shown)
        lines, legend = mark_connected([discovery_line(d, marked) for d in shown],
                                       [_connected(d, current) for d in shown])
        legend += setup_legend(shown)
        items = [(str(i), line) for i, line in enumerate(lines, 1)]
        items += [("r", tr("Search again")),
                  ("d", tr("Search at an address: an IP, a range (10.3.0.0/24) or a broadcast")),
                  ("a", tr("Active search: each address's web (even with discovery off)"))]
        if shown:
            text = tr("{count} device(s) on {where}. Choose one to see its data; no user or "
                      "password needed.", count=len(shown), where=where) + legend
        else:
            text = (tr("No airOS antenna answered on {where}.", where=where) + "\n\n"
                    + tr(NOBODY_HINT))
            if scanned is None:                  # (not once it was asked that way)
                text += "\n\n" + tr(ACTIVE_HINT, active="a")
        choice = ui.menu(tr("Discovery"), text, items,
                         default="1" if shown else "r", ok=tr("View"), cancel=tr("Back"))
        if choice is None:
            return None
        if choice == "a":
            result = _active_screen(ui, scanned or target, ports)
            if result is not None:
                active, notes, scanned, ports = result
                found = merge_active(found, active)
                if notes:
                    ui.msgbox(tr("Active search"), "\n\n".join(notes))
            continue
        if choice in ("r", "d"):
            if choice == "d":
                new = ui.inputbox(tr("Discovery"), tr("IP of the antenna (it gets there even "
                                                      "with routers in between), a range such "
                                                      "as 10.3.0.0/24 (asked address by "
                                                      "address, up to a /22) or the broadcast "
                                                      "of one of your networks, for example "
                                                      "192.168.1.255, to search only there. "
                                                      "Empty = all your networks:"),
                                  "" if target == BROADCAST else target)
                if new is None:
                    continue
                where_to = (discovery_host(new) or new.strip()) if new.strip() else BROADCAST
            else:
                where_to = target
            result = _scan(ui, where_to, port)
            if result is not None:               # (an address that is not one changes nothing)
                target, found, scanned = where_to, result, None
            continue
        d = shown[int(choice) - 1]
        name = d["hostname"] or d["ip"]
        if _connected(d, current):
            ui.msgbox(name, format_discovered(d) + "\n\n" + tr("You are connected to this device."))
            continue
        if ui.yesno(name, format_discovered(d), yes=tr("Connect"), no=tr("Back")) == 0:
            return _connect_to(d)
