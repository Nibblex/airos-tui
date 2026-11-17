# Design

How airos-tui mirrors the web of an airOS 8 or airOS 6 antenna: the same pages, the same validations and the same steps. It uses no SSH and installs nothing on the device.

## The web's addresses

It uses the same addresses as the device's own web interface, so it needs no SSH; these:

  airOS 8 (AC series): /api/auth, /api/info/public, /poll.cgi, /status.cgi, /getcfg.cgi,
                       /writecfg.cgi, /test_mode.cgi, /apply.cgi, /discard.cgi, /reboot.cgi,
                       /reset.cgi, /pwd.cgi, /support.cgi, /cfg.cgi, /cfgupl.cgi, /fwupl.cgi,
                       /fwflash.cgi, /api/fw/*, /api/warnings, /api/warnings/dismiss,
                       /api/unms/*, /api/regdomain, /api/regdomain/country, /api/pppoe/info,
                       /api/pppoe/restart, /api/provmode, /stakick.cgi, /survey.json.cgi,
                       /discovery.cgi, /pingtest_action.cgi, /api/traceroute,
                       /sptest_action.cgi, /ipscan.cgi, /api/firewall/user-rules,
                       /api/firewall/port-forward, /api/dhcp-server/leases, /airviewdata.cgi
  airOS 6 (M series):  /login.cgi, /index.cgi, /poll.cgi, /status.cgi, /cfg.cgi,
                       /getboardinfo.sh, /system.cgi (config upload), /apply.cgi,
                       /discard.cgi, /reboot.cgi, /regdomain_by_country.cgi, /cc_change.cgi,
                       /pppinfo.cgi

## Test mode and safe writes

Changes can be applied in "test mode": the device uses them without saving them and, if
you do not confirm them in time (for example because you lost the connection), it goes
back to the previous configuration by itself.

Like the web, it sends the whole configuration, so before sending it reads the device's
again: if it is not the one read (changed meanwhile in its web or in another session, or a
test that went back by itself), it says which keys changed and, if you accept, prepares your
pending changes again on top of it. If the device's configuration repeats a key with
different values (an edit by hand, a script), it reads the one its web shows, but warns: the
device may be using another (it reorders those lines itself when it applies them), and what
is applied then keeps only the one shown, written once (its web says nothing). While a test
is in progress on the device it sends nothing, nor what the device saves at once (the
country, UISP, the passwords), until that test is kept or reverted. If something goes wrong
in the program itself, it says so and what was prepared stays pending (the details go to
last-error.txt in its data folder, readable only by you).

## Network

The "Network" section changes the network mode (bridge, router or SOHO) the way the web does:
it drops the network and loads the new mode's defaults (the firmware templates), with the
adjustments the web makes when saving, and keeps the IP you are connected through on the side
your PC is on (to bridge mode it proposes the template's IP, as the web's form does, and asks
whether it is static or from DHCP). It also switches the management IP (bridge) or the WAN
(router/SOHO) between static, DHCP and PPPoE (WAN only, as in the web), gateway, DNS, fallback
IP, automatic IP, STP, NAT, blocking management from the WAN, and the LAN with its DHCP
server, touching the same keys as the web. With PPPoE it also shows the session status and
reconnects it, and in test mode it tells you whether it connected before you decide. If you
change the IP you are connected through, it reconnects to the new one by itself, checking by
its MAC that what answers is the same device, with Ubiquiti discovery or with the session it
has, never giving your user and password to whatever answers there (or it looks the device up
by MAC with Ubiquiti discovery, and at its fallback IP, if it moves to DHCP or PPPoE). However
a test ends (kept, reverted, or its time running out just as it is being decided), it then
points where the device answers: at the new address, web and administrator, or at the previous
ones.

On airOS 8 in bridge mode the "Network" section also has the web's Management VLAN (with the
simple network configuration, as its web): the management, with its address, goes to a VLAN
of every port (a VLAN on each hardware interface, the bridge br1 with them and the
management there, and the ebtables entries with which br0 leaves that VLAN's traffic to
them) and back, written as its web writes it, and prepared as the rest: it is applied with
test mode, which brings the device back if the computer is not on that VLAN.

On airOS 8 the "Network" section also has what its web shows of each mode: in router and
SOHO modes the LAN screen (its address; its DHCP server, disabled, enabled with its range,
netmask, lease time and DNS servers, or relaying to another server; the DNS proxy; and the
leases the device gave) and port forwarding; with the advanced network configuration, the
firewall (IPv4 and IPv6, in any mode: ebtables in bridge mode, iptables and ip6tables in
router mode, in order, moving its rules up and down, and the rules the device has in use)
and, in router mode, the DHCP reservations. Each one is read and written as its web does,
with its checks and messages, and prepared as the rest. It also switches the web's
configuration mode (airOS 8): to advanced, only the mode; back to simple, the mode when the
network fits it and, when it does not (VLANs, bridges, IP aliases, firewall or traffic
shaping rules), the network built again from its mode's template, as its web does after
asking, but keeping the address of the management or the WAN (static, DHCP or PPPoE; its
web keeps only a static one) and, in router and SOHO modes, its NAT, management blocking,
DMZ, cloned MAC and LAN with its DHCP server (which its web resets). Switching back to the
mode the device has also offers to bring back its network as the device has it (its web
keeps the rebuilt one). In advanced mode it has the web's
VLANs (on a hardware interface; on an access point a wireless one gets a bridge of its own)
and bridges (their ports, STP and, in bridge mode, DHCP option 82): each with its netconf
entry, and, when one goes away or is disabled, its firewall and traffic shaping rules too.

## Services

The "Services" section turns Ubiquiti discovery and CDP on or off with the same keys as the
web's Services page and, like the web, updates the management ports the DMZ keeps for the
device (UDP 10001 only while discovery is on). On airOS 8.6 and later it also turns LLDP on
or off: the web does not show it, so it sets the key the firmware reads
(discovery.lldp.status; on when it is missing). On airOS 8 it also has the rest of that
page, with the web's defaults, rules and messages, prepared as the other changes: the ping
watchdog (the address to ping, how often, the startup delay, the failures before it reboots
the device and saving the support info first), the SNMP agent (community, contact and
location), the web server (HTTPS and both ports: after applying, the program reconnects at
its new address, and test mode brings it back if it cannot), the SSH server (its port,
password authentication and the authorized keys, imported from a public key file or pasted),
the Telnet server and the system log (also sent to a remote server); changing the web
server, SSH, Telnet or SNMP updates the DMZ's management ports too. A server's port must
not be one of the system's or another server's, as in the web, nor a range (which the web
lets through, leaving the server down), and it is saved as the number the web reads. Gen3
boards get what their web has (no discovery, CDP, SNMP or Telnet). And UISP, as the switch
in its web's header: management on or off and its key, sent at once (/api/unms/enable or
disable) and saved by the device, without test mode, with the connection status. As its
web, when the program starts it shows the notice the device has of a reboot by the ping
watchdog, a flash memory failure or an unexpected reboot, with "Download Support Info" and
"Dismiss" (the crash report the web offers to send to Ubiquiti is not sent).

## Wireless

The "Wireless" section has the settings of the web's Wireless page and its advanced ones:
the wireless mode (access point or station; PtP or PtMP on airOS 8, WDS on airOS 6), the
SSID and hiding it, locking a station to an AP MAC, the country, channel width, frequency
and frequency list, antenna and cable loss, the EIRP calculation, power and distance, the
airMAX frame (airOS 8), the security (none, WPA2 with a key, WPA2 enterprise with RADIUS or
with a user), the MAC access list, the station's backup SSID (airOS 8), airMAX (airOS 6)
and the advanced settings. It reads and saves the page the way each web does (airOS 8: its
models; airOS 6: link.cgi, advanced.cgi, ubnt.cgi and macacl.cgi) and writes only the keys
the change brings, with the values the web gives them: for example, turning an access point
into a station also moves the security to the station's keys and, on a bridge, sets the
ARP-NAT and EAPOL rules. The quick settings for the SSID, the WPA2 key and the power go the
same way. On airOS 8 the country changes as in its web (POST /api/regdomain/country): right
away and saved, without test mode; then what the new country does not allow (frequency,
power…) is prepared as the other changes. airOS 8 Gen3 boards (60 GHz) are left out.

## Stations, graphs and the status screen

"Stations" is the stations table of the web's main page (airOS 8): each one's data, with
the throughput of two reads, kicking one or all (POST /stakick.cgi) and, on a station,
reconnecting to its access point, as the "Reconnect" of that page. "Graphs" are that page's
graphs, live: the capacity, throughput and latency of this device and of the other end (on
an access point with several stations, the one chosen), kept as its web keeps them (a read
of status.cgi every second, its last 120 points) and drawn with text. The status screen
shows that page's distance warning (in PtMP with "Flexible (legacy)" framing, near the limit
of the channel width), the distance between both ends when they have coordinates (their GPS,
or the Location of the System page) and, on boards with GPS, its GPS group of both ends.

## Tools

"Tools" has the web's network tools (airOS 8), run by the device as its web runs them: the
ping (one request per packet, live, with its table and results), the traceroute (hop by hop,
up to 30) and the speed test against another device (with its user and password, which are
kept only in memory and only if asked; any key stops it). It also has the antenna alignment
(the signal of each end and chain, refreshed every second, with the best one seen and its
warnings) and site survey (the access points around; on a station, choosing one prepares its
SSID, its MAC, the PtP or PtMP mode and the security it announces, as the Wireless page
does). First in Tools, where the board has it (airOS 8), airView: the spectrum the device
hears, read as its web's airView dialog reads it (/airviewdata.cgi every 800 ms, or 400 ms
when nothing was new) and computed as its web computes it, in its three views, live and in
text: the waveform (how often each power is heard at each frequency, its ten bands as five
characters, and the power of the moment), the waterfall (its last 24 reads) and the ambient
noise level (each minute's peak or each hour's average, with its time axis), all with its
frequency axis and its legends; with its messages when it is disabled or the management
radio has it.

## System

"System" has the settings of the web's System page: the firmware update (airOS 8, as its web:
"Check for Updates", "Check Now" and the download it offers with its license, uploading a
firmware file, and the notice to update or discard it, which is also asked when the program
starts and one is waiting; with "Check for Updates" on it checks once a day as it starts; after
updating it waits until the device has restarted, since what answers before is the old firmware
still writing the new one; for a device without internet it also checks from this computer,
through its internet: it asks Ubiquiti's update server what the device would ask, downloads the
firmware here, asking first and saying its size (kept in firmware/ in its data folder, for other
devices too, where Upload Firmware looks first), checks it against the md5 the server gives,
shows its license and uploads it as Upload Firmware does), the device name (on airOS 8 as its
web: 1 to 64 characters of any kind, and the model's own name is saved empty), the rest of its
Device section (airOS 8: the language of its web, from the list the device gives, and the
switches its board has: external reset, airView, PoE passthrough, sending crash reports and the
LEDs night mode; on an access point, the TDD capacity reporting), the Management Radio of Gen2
and Gen3 boards (whether it starts with the device, and its button, which turns it on for 15
minutes or off right away, as the web: /api/provmode), the date settings (airOS 8, as its web:
the time zone from its list, the startup date and the NTP client with its server; the status
screen then says whether the date is synchronized), the System Accounts (airOS 8: the
administrator's name, which the program follows after applying it, as test mode does when it
goes back; the administrator password, as the web's dialog: checked by the device, applied and
saved right away; and the read-only account, with its name and its password, which the device
saves right away, as with its web, turning the account on), the Location (airOS 8, without GPS:
latitude, longitude and height, in feet where its web uses them) and its Device Maintenance:
rebooting (what is not applied yet stays pending), the reset to factory defaults and the support
file (airOS 8, as the web: the reset is asked once and ends the session, after saving a backup;
the support file is saved with the name the web gives it) and the backups of the configuration.
On airOS 8 they are the web's: the device's own file (cfg.cgi, with the password hashes) saved
with its name, and uploading one (cfgupl.cgi) to apply it, without test mode and following the
device if it moves (its address, its web or its administrator), or to discard it, which is also
asked when the program starts and the device has one waiting. airos-tui's older backups (without
the passwords) and those of airOS 6 are loaded as pending changes, keeping the device's
passwords. Before applying changes or resetting, a backup is saved in the backups folder (on
airOS 8, the device's own file; if it cannot be written, it asks whether to go on without it).

## Discovery

Discovery (finding the antennas) needs no user or password: it uses Ubiquiti discovery (the
query of its official tool, UDP 10001), which every antenna answers with its name, model,
firmware, mode, SSID, uptime and addresses. It asks on every network interface of this
computer (not only the one with the default route), or a specific IP, which works across
routers, or a range in CIDR notation (10.3.0.0/24): one of this computer's networks with its
broadcast, any other address by address (up to a /22). Leave the IP empty on the connection
screen, open it in Tools while connected to a device (it marks that one), or use --discover
without opening the interface (table or --json). --discover also listens for the CDP and
LLDP announcements the antennas of your own link send by themselves (about once a minute; it
needs root), and says which protocols found each device. With --active-scan (or its option
on the screen) it also finds those that do not announce themselves (discovery, CDP and LLDP
off): it asks each address of your networks, or of a range, for its web, with no user or
password (airOS 8 tells its model at /api/info/public, and whether it waits for its first
setup or keeps the factory password; airOS 6, its MAC in its session cookie), and on your
networks ARP gives their MAC. Only airOS antennas are listed (not other devices that answer
discovery, CDP or LLDP, nor other webs). Tools also has the web's Device Discovery (airOS
8): the antenna itself asks on its networks (/discovery.cgi, as its web: 0.5, 1 and 5
seconds) and lists what it finds with the columns of its web. From either list, connecting
to another device leaves the one connected, as exiting does, once this computer reaches its
web.

## Validations

Before sending anything it validates the values with the same data the web uses: the hardware
power range (board.info), the legal limit for the country, the channel, the antenna and the
cable (the country table the device itself provides), the allowed frequencies and channel
widths, netmasks, ports and the type of every key. A power above the legal limit is refused,
as both webs refuse it, and goes down to it by itself where the web lowers it (airOS 6
whenever its page works the limit out again; both when the device already has it above).

## Languages

The interface speaks English and Spanish: --lang, or AIROS_LANG, or the system locale
(LC_ALL, LC_MESSAGES, LANG). Translations live in locales/<language>.json in this package.

## Usage

Usage (installed: see README.md; or from the folder that holds this package, with
python3 -m airos_tui, or with the path to it):

```text
airos-tui [DEVICE] [-u USER] [--http | --https] [--lang es] [--debug]
airos-tui --discover [--target IP] [--timeout SEC] [--listen SEC] [--json]
          [--active-scan] [--ports PORTS]
python3 path/to/airos_tui [DEVICE] ...          (the folder itself, from anywhere)
```

## The connection screen, first setup and the interface

The password is asked on screen (or taken from the AIROS_PASSWORD environment variable). If
HTTPS does not answer and the web does over HTTP, it asks before sending the user and the
password unencrypted (with http:// before the address, or --http, it does not). Without an
address, the connection screen first offers the devices you connected to lately: their address
(without http://, so that this is asked again) and their user, never a password, in recent.json
in its data folder, readable only by you; they can be forgotten there. With --debug, what it
sends to the device and what it answers goes to debug.log in that folder (only for you, anew
each time), with the passwords and their hashes, the keys, the community, the session's cookies
and its token hidden, and only the size of an uploaded file: to see what a device answers when
something goes wrong. Before any user or password it reads what the device says before its
login, as its web does: an airOS 8 just reset (8.7.x) waits for its first setup, and then,
instead of them, it asks what its web asks, the country, the language of its web and its terms,
and the administrator's new user and password, with its web's rules, and sets it up as its web
does (its API still lets the factory user and password in, but its web never logs in with them
alone). While it has the factory password, its web also offers to set it up from a backup
instead (Upload Backup Configuration), and so does the program: one made by its web (Back Up
Configuration, as System → backups saves them; not one airos-tui saved without the passwords,
which would leave "-" as its password). The device keeps it waiting to be applied, as its web
shows once in; once applied, its user and password are the backup's: the program follows the
device to the IP the backup gives it and asks for them to log in again, as its login page would.
airOS 6.3 does the same on its login page, on its first boot or while it has the factory
password (the country, the language of its web, its terms, and the administrator's new name,
while it is ubnt, and password, with its page's strength meter), and so does the program.
Requires Python 3.10+ and `dialog` (or `whiptail`). Its live screens (graphs, stations,
alignment, airView, the network tools) are drawn with Python's curses, as dialog draws its
boxes, and redrawn in place: only what changes goes to the terminal (a dialog per refresh
cleared the whole screen each time). With dialog, what its screens show (a WPA key, a password…)
is handed to it in a file only you can read, not on its command line, which any user of this
computer can see, and removed afterwards, also if its terminal is closed or it gets SIGTERM
(then it ends as with Ctrl+C, and dialog gives the terminal back); whiptail cannot do that.
The texts are split into lines by the program, to each box's width (the next lines of an item
of a list, or of a "label   value" row, under its text), so that none is lost in a small
window: from 60×18 on; below, it asks for a bigger one. An option too wide for its menu is cut
with "…" and shown whole on the window's last line while it is highlighted; a text too tall,
cut, with an option (?) that shows it whole. The backtitle shows when the box leaves it its
two rows. On Linux's text console (directly, or through tmux on it), whose fonts draw a few
hundred characters, what they lack is drawn with what they have: ⚠ as !, the graphs' bars as
_░▒█.

## The modules

The modules, in three folders by layers (each one only uses the ones above it):

```text
core/                    the base, which knows nothing of the device's pages
  i18n, errors           the translations; the client's errors
  config                 system.cfg and small helpers (a value, a key, a check, the IPs)
  validation             single values, as each web checks its fields; quick settings
  debuglog               --debug: the file of what is sent and answered, secrets hidden
device/                  the device: its HTTP client and each page of its web as a model
  client, limits         the device's HTTP client; its limits
  discovery              finding it: Ubiquiti's discovery, CDP, LLDP and the active search
  network, templates     network keys and their checks; the firmware's network templates
  netmode, dhcp,         the network mode change and the management VLAN; the LANs' DHCP;
  firewall, advnet       port forwarding and the firewall; VLANs and bridges (airOS 8)
  wireless, services     the Wireless and Services pages, as each web saves them
  system, firmware       the System page: its settings and maintenance; firmware
  status, graphs         showing the status; the graphs of the web's main page
  tools, stations,       the web's tools; the stations table; airView
  airview
screens/                 the screens: dialog, the application and its menus
  ui                     dialog (and whiptail): its boxes, what they show and how they fit
  app, ...               the application: the main menu (app) and one mixin per menu
                         (apply, network, router, network_advanced, wireless,
                         wireless_security, wireless_advanced, services, system,
                         firmware, tools, nettools, stations, airview); the discovery
                         screen (discovery)
cli                      startup: command line, connection screen and --discover
```
