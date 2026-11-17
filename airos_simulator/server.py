"""The HTTP server: a Handler of airOS 8's and airOS 6's classes, one server for
every address the device takes, and the discovery, CDP and LLDP answers."""
from __future__ import annotations

import email.parser
import email.policy
import http.cookies
import json
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .data import MAC
from .device import Device
from .web8 import Web8
from .web6 import Web6


class Handler(Web8, Web6, BaseHTTPRequestHandler):
    server_version = "lighttpd"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("[sim] " + (fmt % args) + "\n")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    # -- helpers --------------------------------------------------------------

    def _send(self, status, body=b"", ctype="text/html", headers=()):
        if isinstance(body, (dict, list)):
            body, ctype = json.dumps(body).encode(), "application/json"
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _parse_body(self) -> tuple[dict, dict]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type") or ""
        form, files = {}, {}
        if ctype.startswith("multipart/form-data"):
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                payload = part.get_payload(decode=True) or b""
                if part.get_filename():
                    files[name] = payload
                else:
                    form[name] = payload.decode("utf-8", "replace")
        elif raw:
            form = dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace"),
                                               keep_blank_values=True))
        return form, files

    def _handle(self, method):
        dev: Device = self.server.dev
        with dev.lock:
            dev.tick()
            if time.time() < dev.down_until:
                self.close_connection = True      # restarting: no answer
                return
            if dev.follow and tuple(self.server.server_address[:2]) not in dev.endpoints():
                self.close_connection = True      # the device no longer has this IP or port
                return
            u = urllib.parse.urlsplit(self.path)
            self.query = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
            self.form, self.files = self._parse_body() if method == "POST" else ({}, {})
            cookies = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
            self.sid = cookies[f"AIROS_{MAC}"].value if f"AIROS_{MAC}" in cookies else None
            (self._v8 if dev.version == 8 else self._v6)(method, u.path)


    def _reboot(self):
        dev: Device = self.server.dev
        dev.system, dev.running = dict(dev.saved), dict(dev.saved)
        dev.test_until, dev.modified, dev.backup_loaded = None, False, False
        dev.sessions.clear()
        dev.boot = time.time()
        dev.soft_restart(2)
        dev.events.append("reboot")



class Cluster:
    """One HTTP server for every 127.x IP (and port) the simulated device gets to have."""

    def __init__(self, dev: Device, port: int, verbose: bool):
        self.dev, self.port, self.verbose = dev, port, verbose
        self.servers: dict[tuple[str, int], ThreadingHTTPServer] = {}
        self.lock = threading.Lock()

    def ensure(self, ip: str, port: int | None = None) -> None:
        port = port or self.port
        if not ip.startswith("127."):
            return
        with self.lock:
            if (ip, port) in self.servers:
                return
            srv = ThreadingHTTPServer((ip, port), Handler)
            srv.dev, srv.verbose = self.dev, self.verbose
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.servers[(ip, port)] = srv

    def shutdown(self) -> None:
        for srv in self.servers.values():
            srv.shutdown()
            srv.server_close()


def serve_discovery(dev: Device, bind: str, port: int) -> threading.Thread:
    """Answer the v1 query of Ubiquiti discovery (01 00 00 00)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, port))

    def loop():
        while True:
            data, addr = sock.recvfrom(2048)
            if data[:2] == b"\x01\x00":
                with dev.lock:
                    if dev.running.get("discovery.status", "enabled") != "enabled":
                        continue                  # discovery turned off: no answer
                    reply = dev.discovery_reply()
                sock.sendto(reply, addr)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


class AnnounceError(OSError):
    """The CDP/LLDP announcements could not start (they need root or CAP_NET_RAW)."""


def serve_announcements(dev: Device, iface: str, every: float) -> threading.Thread:
    """Send the device's CDP and LLDP announcements through iface every so many seconds
    (not while it restarts)."""
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    sock.bind((iface, 0))

    def loop():
        while True:
            with dev.lock:
                frames = [] if time.time() < dev.down_until else dev.announcements()
            for frame in frames:
                try:
                    sock.send(frame)
                except OSError:
                    pass
            time.sleep(every)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def make_server(version: int, port: int, user="ubnt", password="ubnt", restart=8.0,
                test_secs=180, bind="127.0.0.1", verbose=False, mode="bridge",
                mgmt_ip: str | None = None, dhcp_ip="127.0.0.77",
                discovery_port: int | None = None, station=False,
                announce: str | None = None, announce_every: float = 60.0,
                emergency: int = -1, fw_update: str | None = None,
                web_follow: bool = False, flash_delay: float = 0.0, setup_wizard: bool = True,
                first_setup: bool = False, factory_password: bool = False) -> ThreadingHTTPServer:
    """mgmt_ip (127.x): the device answers at that IP and "moves" if you change it.
    web_follow: its web answers at the port of its configuration (httpd.port, which starts
    being port; over HTTP) and "moves" if you change it.
    announce: interface to send CDP and LLDP through (root or CAP_NET_RAW).
    emergency: the notice it has as it starts (airOS 8: emergReasonCode, -1 none).
    fw_update: what its update check finds (airOS 8: WA.v8.7.20), or "offline".
    flash_delay: seconds its web keeps answering, with the old firmware, after the update is
    started (a device writing the new one).
    setup_wizard: airOS 8 with its first setup (8.7.x; False, an older one): after a reset it
    waits for it. Its user and password are not taken as the factory ones otherwise (they are
    the tests' way in), so it says nothing of them (default_password) until then.
    first_setup: and it waits for it now (as just reset: user and password the factory's).
    factory_password: set up, but with the factory user and password still (user and password):
    its web asks for a new administrator, or a backup."""
    dev = Device(version, user, password, restart, test_secs, mode, mgmt_ip, dhcp_ip, station)
    dev.wizard = version == 8 and (setup_wizard or first_setup or factory_password)
    dev.setup_pending = dev.wizard and first_setup
    dev.factory_password = dev.wizard and factory_password and not first_setup
    dev.emerg_reason = emergency
    dev.flash_delay = flash_delay
    if fw_update == "offline":
        dev.fw_check_error = True
    elif fw_update:
        dev.fw_available = fw_update
    srv = ThreadingHTTPServer((mgmt_ip or bind, port), Handler)
    srv.dev, srv.verbose = dev, verbose
    dev.at = (mgmt_ip or bind, port)
    if web_follow:
        for cfg in (dev.running, dev.saved, dev.system):
            cfg["httpd.port"] = str(port)
        dev.web_follow = dev.follow = True
    if mgmt_ip or web_follow:
        cluster = Cluster(dev, port, verbose)
        cluster.servers[(mgmt_ip or bind, port)] = srv
        dev.on_address = cluster.ensure
        srv.cluster = cluster
    if discovery_port:
        serve_discovery(dev, "127.0.0.1", discovery_port)
    if announce:
        try:
            serve_announcements(dev, announce, announce_every)
        except (OSError, AttributeError) as e:
            srv.server_close()
            raise AnnounceError(str(e)) from e
    return srv
