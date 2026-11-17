"""Simulator of an airOS 8 or airOS 6 antenna to test airos-tui without hardware.

It only mimics the HTTP paths airos-tui uses, with the behavior seen on the real firmware:
cookie session, X-CSRF-ID (airOS 8), hidden token in the POSTs (airOS 6), masked passwords,
a test mode that reverts by itself and a soft restart during which the device does not
answer. It is not a real airOS.

    airos-tui-simulator --airos 8 --port 8080
    python3 -m airos_tui http://127.0.0.1:8080          (user ubnt, password ubnt)

In router mode (--mode router) the WAN can move to PPPoE: the simulated "provider" accepts
the user "customer" with the password "secret" and does not support MPPE encryption.
With --mgmt-ip the simulator answers at the IPs the real device would be reached at
(management in bridge mode; LAN and, unless management is blocked, WAN in router mode), so
the network mode change can be tested. --station makes it a client (CPE) instead of an AP.
With --discovery 10001 it answers discovery on 127.0.0.1, at the real port:
    python3 -m airos_tui --discover --target 127.0.0.1
With --announce IFACE it also sends CDP (if discovery.cdp.status is enabled) and LLDP (airOS 8,
unless discovery.lldp.status disables it) through that interface, which needs root: try it in
a network namespace, for example with unshare -rn and a veth pair.
With --fw-update v8.7.20, "Check Now" (System → Firmware) finds that firmware, whose notes,
license and download the simulator itself serves (nothing goes to Ubiquiti), so the whole
update can be followed; with --fw-update offline it fails as a device without internet does.
Its airView (airOS 8) hears a made-up spectrum, a new read every second (--airview-period).

Its messages follow --lang, AIROS_LANG or the system locale, like airos-tui.

The modules, by layers (each one only uses the ones above it):

    i18n                 the messages, with airos-tui's own catalog
    data, frames         the sample data; the CDP and LLDP announcements
    cfgfile              system.cfg as each web reads and writes it; the password hashes
    device               the simulated device: its configuration, its restarts, its pages' data
    web8, web6           its HTTP paths, as airOS 8's and airOS 6's web answer them
    server               the Handler (web8 and web6 together), the servers and the discovery
    cli                  the command line (airos-tui-simulator, or python3 -m airos_simulator)
"""
from __future__ import annotations

from .cfgfile import cfg_text, parse_cfg, sim_hash
from .cli import fw_update_arg, main
from .data import (BOARD, COMMON_CFG, COUNTRIES, FIRMWARE, LEASES, MAC, MODEL, NET_BRIDGE,
                   NET_ROUTER, NEIGHBORS, PPPOE_PASSWORD, PPPOE_SESSION, PPPOE_USER, SURVEY_APS,
                   airview_payload, regdomain_v6, regdomain_v8)
from .device import Device
from .frames import _checksum, cdp_frame, lldp_frame
from .i18n import detect_language, set_language, tr
from .server import (AnnounceError, Cluster, Handler, make_server, serve_announcements,
                     serve_discovery)

__all__ = [
                   "BOARD",
                   "COMMON_CFG",
                   "COUNTRIES",
                   "FIRMWARE",
                   "LEASES",
                   "MAC",
                   "MODEL",
                   "NEIGHBORS",
                   "NET_BRIDGE",
                   "NET_ROUTER",
                   "PPPOE_PASSWORD",
                   "PPPOE_SESSION",
                   "PPPOE_USER",
                   "SURVEY_APS",
                   "AnnounceError",
                   "Cluster",
                   "Device",
                   "Handler",
                   "_checksum",
                   "airview_payload",
                   "cdp_frame",
                   "cfg_text",
                   "detect_language",
                   "fw_update_arg",
                   "lldp_frame",
                   "main",
                   "make_server",
                   "parse_cfg",
                   "regdomain_v6",
                   "regdomain_v8",
                   "serve_announcements",
                   "serve_discovery",
                   "set_language",
                   "sim_hash",
                   "tr",
]
