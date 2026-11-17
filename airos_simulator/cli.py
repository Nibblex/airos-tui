"""The simulator's command line (airos-tui-simulator, or python3 -m airos_simulator)."""
from __future__ import annotations

import argparse
import re
import sys
from .i18n import LANGUAGES, detect_language, set_language, tr
from .data import PPPOE_PASSWORD, PPPOE_USER
from .server import AnnounceError, make_server


def _lang_arg(argv) -> str | None:
    """--lang LANG or --lang=LANG, read before parsing so that --help comes out translated."""
    args = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(args):
        if a == "--lang" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def fw_update_arg(value: str) -> str:
    """--fw-update: "offline", or the firmware the update check finds as the simulated server
    gives it (WA.v8.7.20, from v8.7.20, 8.7.20 or WA.v8.7.20). ValueError if it is neither."""
    text = value.strip()
    if text.lower() == "offline":
        return "offline"
    m = re.fullmatch(r"(?:WA\.)?v?([0-9]+\.[0-9]+\.[0-9]+)", text, re.I)
    if not m:
        raise ValueError(value)
    return f"WA.v{m.group(1)}"


def main(argv=None) -> None:
    set_language(detect_language(_lang_arg(argv)))
    ap = argparse.ArgumentParser(description=tr("airOS antenna simulator to test airos-tui."))
    ap.add_argument("--airos", type=int, choices=(6, 8), default=8)
    ap.add_argument("--port", type=int, default=8080, metavar=tr("PORT"))
    ap.add_argument("--user", default="ubnt", metavar=tr("USER"))
    ap.add_argument("--password", default="ubnt", metavar=tr("PASSWORD"))
    ap.add_argument("--restart", type=float, default=8.0, metavar=tr("SEC"),
                    help=tr("seconds without answering after applying (default 8)"))
    ap.add_argument("--test-time", type=int, default=180, metavar=tr("SEC"),
                    help=tr("test mode length in seconds (default 180)"))
    ap.add_argument("--mode", choices=("bridge", "router"), default="bridge",
                    help=tr("network mode of the initial configuration"))
    ap.add_argument("--mgmt-ip", metavar="127.0.0.X",
                    help=tr("management IP (or the LAN IP in router mode) within 127.0.0.0/8: "
                            "the simulator answers there and moves if you change it"))
    ap.add_argument("--dhcp-ip", default="127.0.0.77", metavar="127.0.0.X",
                    help=tr('IP it "gets" over DHCP with --mgmt-ip (default 127.0.0.77)'))
    ap.add_argument("--advanced", action="store_true",
                    help=tr("the advanced network configuration of the airOS 8 web (where it "
                            "has the firewall and the DHCP reservations)"))
    ap.add_argument("--station", action="store_true",
                    help=tr("station (client, CPE) instead of access point"))
    ap.add_argument("--web-follow", action="store_true",
                    help=tr("airOS 8: its web answers at the port of its configuration "
                            "(httpd.port, PORT at first) and moves if you change it, as the "
                            "real one does (over HTTP only)"))
    ap.add_argument("--discovery", type=int, metavar=tr("PORT"),
                    help=tr("answer Ubiquiti discovery on 127.0.0.1:PORT"))
    ap.add_argument("--announce", metavar=tr("IFACE"),
                    help=tr("send CDP and LLDP announcements through IFACE as the device would "
                            "(it needs root or CAP_NET_RAW: use it inside a network namespace)"))
    ap.add_argument("--announce-every", type=float, default=60.0, metavar=tr("SEC"),
                    help=tr("seconds between announcements (default 60)"))
    ap.add_argument("--emergency", type=int, choices=(0, 1, 2, 3), metavar=tr("CODE"),
                    help=tr("the notice the device has as it starts (airOS 8): 0 a reboot by "
                            "the ping watchdog, 1 a flash memory failure, 2 or 3 a crash"))
    ap.add_argument("--fw-update", metavar=tr("VERSION"),
                    help=tr('what "Check Now" finds (airOS 8): a newer firmware, for example '
                            'v8.7.20, which the simulator itself serves, or "offline" to fail '
                            'as a device without internet does'))
    ap.add_argument("--no-setup-wizard", action="store_true",
                    help=tr("an airOS 8 older than its first setup: after a reset, user and "
                            "password ubnt (by default, as 8.7.x, it waits for its first setup: "
                            "the country, its terms and a new administrator)"))
    ap.add_argument("--first-setup", action="store_true",
                    help=tr("airOS 8 waiting for its first setup now, as just reset (--user and "
                            "--password: the factory ones)"))
    ap.add_argument("--factory-password", action="store_true",
                    help=tr("airOS 8 set up, but with the factory password still: its web asks "
                            "for a new administrator, or a backup (--user and --password: the "
                            "factory ones)"))
    ap.add_argument("--airview-period", type=float, default=1.0, metavar=tr("SEC"),
                    help=tr("seconds between the reads of its airView (airOS 8, default 1; a "
                            "real one gives one every 6 or so)"))
    ap.add_argument("-v", "--verbose", action="store_true",
                    help=tr("log every request to stderr"))
    ap.add_argument("--lang", choices=LANGUAGES,
                    help=tr("interface language (default: AIROS_LANG or the system locale)"))
    a = ap.parse_args(argv)
    if a.lang:
        set_language(a.lang)
    if a.mgmt_ip and not a.mgmt_ip.startswith("127."):
        ap.error(tr("--mgmt-ip must be within 127.0.0.0/8"))
    fw_update = None
    if a.fw_update is not None:
        if a.airos != 8:
            ap.error(tr("--fw-update only applies to airOS 8"))
        try:
            fw_update = fw_update_arg(a.fw_update)
        except ValueError:
            ap.error(tr('--fw-update: "{value}" is not a version such as v8.7.20, nor "offline"',
                        value=a.fw_update))
    try:
        srv = make_server(a.airos, a.port, a.user, a.password, a.restart, a.test_time,
                          verbose=a.verbose, mode=a.mode, mgmt_ip=a.mgmt_ip,
                          dhcp_ip=a.dhcp_ip, discovery_port=a.discovery, station=a.station,
                          announce=a.announce, announce_every=a.announce_every,
                          emergency=-1 if a.emergency is None else a.emergency,
                          fw_update=fw_update, web_follow=a.web_follow,
                          setup_wizard=not a.no_setup_wizard, first_setup=a.first_setup,
                          factory_password=a.factory_password)
    except AnnounceError as e:
        ap.error(tr("--announce needs root or CAP_NET_RAW ({error})", error=e))
    if a.advanced:
        for cfg in (srv.dev.running, srv.dev.saved, srv.dev.system):
            cfg["gui.network.advanced.status"] = "enabled"
    srv.dev.airview_period = a.airview_period
    host = a.mgmt_ip or "127.0.0.1"
    print(tr("airOS {version} simulator ({mode}) at http://{host}:{port} (user {user}, "
             "password {password}). Ctrl+C to exit.", version=a.airos, mode=a.mode, host=host,
             port=a.port, user=a.user, password=a.password))
    if fw_update == "offline":
        print(tr('"Check Now" fails, as on a device without internet.'))
    elif fw_update:
        print(tr('"Check Now" finds the firmware {version}.', version=fw_update))
    if a.mode == "router":
        print(tr("PPPoE on the WAN: the simulated provider accepts {user} / {password}.",
                 user=PPPOE_USER, password=PPPOE_PASSWORD))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
