# airos-tui

A terminal user interface to configure Ubiquiti airMAX antennas — **airOS 8** (AC series) and
**airOS 6** (M series) — over HTTP/HTTPS, through the same endpoints the device's own web
interface uses. No SSH, nothing installed on the device.

It mirrors the web: the same pages, the same validations and the same steps (test mode,
backups, first setup, firmware update), in a keyboard-driven [`dialog`][dialog] interface that
works over SSH, on a small screen or anywhere a terminal runs.

```
airos-tui · Ubiquiti airMAX antennas over HTTP

┌─────────AP-North-Tower · LiteBeam 5AC (simulated) · airOS v8.7.19───────────┐
│ Connected to http://127.0.0.28:18200 (ubnt)                                 │
│ Unapplied changes: 0                                                        │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │  1  Device and link status                                              │ │
│ │  e  Stations (1): their data and kicking them                           │ │
│ │  g  Graphs: capacity, throughput and latency, live                      │ │
│ │  2  Quick settings: name, SSID, WPA2 key, power                         │ │
│ │  w  Wireless: mode, SSID, frequency, power, security, MAC ACL…          │ │
│ │  3  Network: mode (bridge/router), management IP and VLAN, DNS          │ │
│ │  s  Services: web, SSH, SNMP, discovery, ping watchdog, UISP and more   │ │
│ │  t  Tools: alignment, site survey, discovery, ping, speed test          │ │
│ │  y  System: firmware, device, accounts, location, maintenance, backups  │ │
│ │  4  Search and edit any configuration key                               │ │
│ │  5  View pending changes (0)                                            │ │
│ │  6  Apply changes (with test mode)                                      │ │
│ │  7  Test mode in progress: confirm or revert                            │ │
│ │  l  Read the device configuration again                                 │ │
│ │  0  Exit                                                                │ │
│ │                                                                         │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│                       <Choose>            < Exit >                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Features

- **Test mode.** Changes can be applied so that the device uses them without saving them and,
  unless you confirm them in time (for example because you lost the connection), goes back to
  the previous configuration by itself — as the web's *Test* button.
- **Safe writes.** Like the web, it sends the whole configuration, so it reads the device's again
  first: if it changed meanwhile (in its web, another session…), it says which keys did and
  prepares your pending changes again on top of it. It follows the device when a change moves
  the IP, the port or the protocol you are connected through.
- **The web's validations,** with the data the web uses: the power against the hardware range
  and the legal limit of the country (from the device's own country tables), channels and
  widths, netmasks, ports and the type of every key.
- **The web's sections:** wireless (mode, SSID, frequency, power, security, MAC ACL…),
  network (bridge, router or SOHO, management VLAN, VLANs and bridges, DHCP, PPPoE, port
  forwarding and the firewall), services, and system (accounts, date, location, device
  maintenance).
- **Live screens:** the main page's graphs, the stations table, antenna alignment, airView's
  spectrum, and the device's network tools (ping, traceroute, speed test). Also the device and
  link status, site survey and the discovery of devices.
- **Backups.** A local backup before applying changes or resetting; the web's *Back Up
  Configuration* and *Upload Configuration*.
- **First setup**, as each web asks it: an airOS 8.7.x just reset (country, language, terms and
  a new administrator, or from a backup) and airOS 6.3's first login.
- **Firmware update** as the web does it; and, for an antenna without internet, the check and
  the download made from your computer, then uploaded to the antenna.
- **Discovery** without logging in: `--discover` lists the airOS antennas on your networks
  (Ubiquiti discovery, CDP and LLDP), or at an IP or a range (`--target 10.3.0.0/24`) behind a
  router. `--active-scan` also finds those with discovery, CDP and LLDP off: it asks each
  address for its web (airOS 8 tells its model; airOS 6, its MAC); on your networks, ARP gives
  their MAC. Only airOS antennas are listed.
- **English and Spanish.**

## Requirements

- Linux (other POSIX systems may work, but are not tested) and a terminal of 60×18 at least
  (80×24 or more reads best). In a narrow window, an option too wide for its menu is cut with
  "…" and shown whole on the last line while it is highlighted; a text too tall for it is cut,
  with an option (`?`) that shows it whole. On Linux's text console (a uConsole's screen, for
  example), whose fonts lack some symbols, ⚠ is shown as `!` and the graphs' bars with `_░▒█`.
- Python 3.10 or newer. Only the standard library.
- [`dialog`][dialog] (recommended) or `whiptail`: `sudo apt install dialog`. With dialog, what
  a screen shows (a WPA key, a password…) is handed to it in a file only you can read; whiptail
  can only take it on its command line, which other users of the computer can see.

## Installation

With [pipx](https://pipx.pypa.io/) (recommended: it gets its own environment):

```sh
pipx install git+https://github.com/Nibblex/airos-tui
```

Or with pip, in a virtual environment:

```sh
python3 -m venv ~/.venvs/airos-tui
~/.venvs/airos-tui/bin/pip install git+https://github.com/Nibblex/airos-tui
```

Both install two commands: `airos-tui` and `airos-tui-simulator`. It also runs without
installing it, from its folder: `python3 -m airos_tui …`

## Usage

```sh
airos-tui                          # connection screen: recent devices, an address, or a search
airos-tui 192.168.1.20 -u ubnt     # that device (the password is asked)
airos-tui https://10.0.0.2:8443    # with its protocol and port
airos-tui --discover               # the antennas on your networks, and exit (no login)
airos-tui --discover --target 10.3.0.0/24  # a network behind a router, address by address
airos-tui --active-scan            # also those with discovery off, by their web
airos-tui --discover --target 10.0.0.255 --json
```

The password is asked on screen (or taken from the `AIROS_PASSWORD` environment variable). If
HTTPS does not answer and the web does over HTTP, it asks before sending the user and the
password unencrypted (with `http://` before the address, or `--http`, it does not).

```
usage: airos-tui [-h] [-u USER] [--http | --https] [--wait SEC] [--discover] [--target IP]
                 [--timeout SEC] [--listen SEC] [--active-scan] [--ports PORTS] [--json]
                 [--lang {en,es}] [--debug] [--version]
                 [DEVICE]

positional arguments:
  DEVICE           IP or name of the device (also http://IP:port or https://IP)

options:
  -u, --user USER  user (asked by default; it is usually ubnt)
  --http           use HTTP without trying HTTPS
  --https          use HTTPS only
  --wait SEC       seconds before checking whether the device came back after applying (default 15
                   on airOS 8 and 20 on airOS 6)
  --lang {en,es}   interface language (default: AIROS_LANG or the system locale)
  --debug          save what is sent to the device and what it answers in
                   ~/.local/share/airos-tui/debug.log, only for you, with passwords, keys and
                   session cookies hidden
  --version        show program's version number and exit

discovery: the devices on the network (no user or password needed):
  --discover       list the airOS antennas on the network (Ubiquiti discovery, CDP and LLDP) and
                   exit; the device, if you give it, is where to look
  --target IP      where to look: a specific IP (it gets there even with routers in between), a
                   range such as 10.3.0.0/24 (one of your networks, with its broadcast; any other,
                   address by address, up to a /22) or the broadcast of one of your networks (only
                   there); by default, all your networks. It also applies to the discovery of the
                   connection screen and of Tools
  --timeout SEC    seconds asking with Ubiquiti discovery (default 3)
  --listen SEC     seconds listening for the CDP and LLDP announcements, which cannot be asked
                   for: they arrive about once a minute and only from your own link, and it needs
                   root. Default: 65 when searching your networks, 0 with a target
  --active-scan    also search actively: each address of your networks (or of --target, up to a
                   /22) is asked for its web, without a user or password; on your networks, ARP
                   gives their MAC. It finds the antennas even with discovery, CDP and LLDP off;
                   it implies --discover
  --ports PORTS    the web's ports the active search asks (default 443,80); also those the
                   discovery screen proposes
  --json           with --discover, the list as JSON
```

The language follows `--lang`, then `AIROS_LANG`, then the system locale.

## What goes where

- **To the antenna:** the requests its own web makes.
- **Outside your network,** only when you ask for it: the firmware check made from your computer
  asks Ubiquiti's update server (`callhome.svc.ui.com`) what the antenna would ask — its model
  id, its firmware version, the md5 of its MAC and its web's language — and downloads the
  firmware it offers (checked against the md5 the server gives) and its license. The release
  notes of a new firmware are fetched by your computer, as the web fetches them in your
  browser.
- **On your computer,** in `~/.local/share/airos-tui/` (or `$XDG_DATA_HOME/airos-tui/`):
  - `backups/`: the configuration backups, with the password hashes: keep them safe;
  - `firmware/`: the firmware downloaded from your computer, to use on other antennas too;
  - `recent.json`: the devices you connected to (address and user, never a password);
  - `debug.log`: with `--debug`, what was sent and answered (secrets hidden);
  - `auto-update.json` (when each device was last checked for updates) and `last-error.txt`
    (the details of an unexpected error of the program).

  `recent.json`, `debug.log` and the backups are readable only by you.

## Trying it without an antenna

The simulator answers the HTTP paths airos-tui uses, with the behavior seen on real firmware:

```sh
airos-tui-simulator --airos 8 --port 8080 &
airos-tui http://127.0.0.1:8080 -u ubnt          # password: ubnt
```

From its folder, `python3 -m airos_simulator` is the same. See its `--help`: airOS 6
(`--airos 6`), router mode (`--mode router`), a device waiting for its first setup
(`--first-setup`), a firmware update to find (`--fw-update v8.7.20`)…

## Compatibility

airOS 8.7.x and airOS 6.3.x. airOS 7 and airOS 5 or older are not tested: the airOS 8 and
airOS 6 paths are used. It has been checked against the simulator; against the real code of the
airOS 6.3.26 web and the airOS 8.7.25 API, run under emulation (QEMU); and used on real
NanoStation 5AC loco and LiteBeam 5AC Gen2 antennas.

## Development

- **Tests:** `python3 tests/run.py` runs the suites in parallel (`-j N`); `--e2e` adds the
  end-to-end tests (`tests/e2e/`), the real interface in tmux (they need `tmux` and `dialog`;
  in a 110×34 window, or the size in `AIROS_E2E_SIZE`, such as `91x24`), and
  `--coverage` reports which lines get to run (Python 3.12+). They all run against the
  simulator on 127.0.0.x, with no traffic to your networks. They run the program in Spanish:
  the texts they expect come from `tr()`, so they check the Spanish catalog too.
- **Emulation:** `tests/emu/emu6.py` and `tests/emu/emu8.py` run the real web of airOS 6.3.26
  and the real API of airOS 8.7.25 under QEMU user mode; they need `qemu-user`,
  `qemu-user-binfmt` and `unsquashfs` (emu8 also `gcc`), and download their firmware once
  (`python3 tests/emu/emu8.py fetch`). Without them, those suites are skipped.
- **Layout:** `airos_tui/core/` (translations, errors, `system.cfg` and small helpers),
  `airos_tui/device/` (the HTTP client and each page of the web as a model), and
  `airos_tui/screens/` (dialog and the menus), with `airos_tui/cli.py` for startup; next to
  them, `airos_simulator/` (its data, the device, the two webs, the server and its command
  line) and `tests/`. No module uses one of a later layer.
- **Style:** `ruff check` (see `ruff.toml`).
- **Translations:** messages are written in English; `airos_tui/locales/es.json` has the
  Spanish ones (`tests/test_i18n.py` checks that none is missing or unused).
- **Design:** `docs/design.md` describes how each part mirrors the web, and the layers of
  its modules.

## License

Copyright © 2026 Sergio Rodríguez.

This program is free software: you can redistribute it and/or modify it under the terms of
the [GNU General Public License, version 3](LICENSE) (GPL-3.0-only), as published by the
Free Software Foundation. It is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.

The sample data in `tests/fixtures/python-airos/` comes from the python-airos project, under
its own MIT license (`tests/fixtures/python-airos/LICENSE`).

Not affiliated with or endorsed by Ubiquiti Inc. Ubiquiti, airMAX, airOS and UISP are
trademarks of Ubiquiti Inc.

[dialog]: https://invisible-island.net/dialog/
