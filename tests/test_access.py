"""Tests of the rest of "Services" (airOS 8): SNMP, the web server, SSH with its authorized
keys, Telnet and the system log, as its web reads and saves them (with the real configuration
of a NanoStation 5AC loco with 8.7.14); and following its web when it changes its port or
HTTPS: test and keep, revert, not confirmed and when it does not answer.
All on 127.0.0.1."""
import os
import shutil
import sys
import threading
import time

from _common import check, fails, FakeUI, suite, WORK  # noqa: E402
T, S, XDG = suite("access")
tr = T.tr
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "fixtures", "real", "airos8.7.14-nanostation5ac-sta.cfg")
FILES = os.path.join(WORK, "access")
shutil.rmtree(FILES, ignore_errors=True)
os.makedirs(FILES)


def cfg(**kv):
    return T.Config("".join(f"{k.replace('__', '.')}={v}\n" for k, v in kv.items()))


def key(n):
    """A made-up RSA public key (it only has to look like one)."""
    return f"AAAAB3NzaC1yc2EAAAADAQABAAABAQ{n:04d}" + "x" * 40 + "=="


def write(name, text):
    path = os.path.join(FILES, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


print("=== what its web reads: the real NanoStation (8.7.14) ===")
real = T.Config(open(REAL, encoding="utf-8").read())
check("SNMP off, with the community public", T.snmp_settings(real) == {
    "on": False, "community": "public", "contact": "", "location": ""}, str(T.snmp_settings(real)))
check("web: HTTPS on 443 and HTTP on 80",
      T.web_settings(real) == {"https": True, "https_port": "443", "http_port": "80"})
check("SSH off (port 22, with password) and no keys", T.ssh_settings(real) == {
    "on": False, "port": "22", "passwd": True} and T.ssh_keys(real) == [])
check("Telnet off (23)", T.telnet_settings(real) == {"on": False, "port": "23"})
check("local log, without the remote one (514)", T.syslog_settings(real) == {
    "on": True, "remote": False, "ip": "", "port": "514"})
check("its web answers over HTTPS on 443", T.web_endpoint(T.web_settings(real))
      == ("https", 443))

print("=== without keys: the defaults of its models ===")
empty = cfg()
check("SNMP off with public; HTTPS on 443 and HTTP on 80",
      T.snmp_settings(empty) == {"on": False, "community": "public", "contact": "",
                                 "location": ""}
      and T.web_settings(empty) == {"https": True, "https_port": "443", "http_port": "80"})
check("SSH on, on 22 with password; Telnet off, on 23",
      T.ssh_settings(empty) == {"on": True, "port": "22", "passwd": True}
      and T.telnet_settings(empty) == {"on": False, "port": "23"})
check("log on, no remote, 514", T.syslog_settings(empty) == {
    "on": True, "remote": False, "ip": "", "port": "514"})
check("yes is enabled, true or 1, as in its web (empty or Enabled: no)",
      [T.ssh_settings(cfg(sshd__status=v))["on"] for v in ("enabled", "true", "1", "", "Enabled",
                                                           "disabled")]
      == [True, True, True, False, False, False])
check("the port, as parseInt reads it", T.ssh_settings(cfg(sshd__port="0x8AE"))["port"] == "2222"
      and T.telnet_settings(cfg(telnetd__port="2323abc"))["port"] == "2323")
check("where its web answers: HTTPS if it is enabled, otherwise HTTP",
      T.web_endpoint({"https": True, "https_port": "8443", "http_port": "8080"})
      == ("https", 8443)
      and T.web_endpoint({"https": False, "https_port": "8443", "http_port": "8080"})
      == ("http", 8080))
check("what its page has: everything; on Gen3 no SNMP or Telnet; airOS 6, another web",
      T.access_offered("v8", {}) == dict.fromkeys(("snmp", "web", "ssh", "telnet", "syslog"),
                                                   True)
      and T.access_offered("v8", {"feature.g3": "1"}) == {
          "snmp": False, "web": True, "ssh": True, "telnet": False, "syslog": True}
      and not any(T.access_offered("v6", {}).values()))

print("=== the servers' ports (its web's portSys) ===")
on = cfg(httpd__port="80", httpd__https__status="enabled", httpd__https__port="443",
         sshd__status="enabled", sshd__port="22", telnetd__status="enabled", telnetd__port="23")
off = cfg(httpd__port="80", httpd__https__status="disabled", httpd__https__port="443",
          sshd__status="disabled", sshd__port="22", telnetd__status="disabled",
          telnetd__port="23")


def port(service, field, value, c=on):
    return T.service_port_problem(service, field, value, c)


required, invalid = tr("Server Port is required"), tr("Server Port is invalid")
out = tr("Server Port must be between {low} and {high}", low=1, high=65535)
system, other = tr("Server Port is used by system"), tr("Server Port is used by another service")
check("required and from 1 to 65535 (an integer)", [port("ssh", "port", v) for v in (
    " ", "0", "65536", "2.5", "abc", "1e3")] == [required, out, out, out, out, out])
check("a range a:b is not valid (its web lets it through and the server would not start)",
      port("ssh", "port", "2222:2223") == invalid and port("ssh", "port", "x:y") == invalid)
check("the system's: airView, the speed test and ubridge",
      [port("telnet", "port", p) for p in ("50841", "53762", "61780")] == [system] * 3)
check("those of the other servers that are on", [port(*a) for a in (
    ("ssh", "port", "80"), ("ssh", "port", "443"), ("ssh", "port", "23"),
    ("telnet", "port", "22"), ("web", "http_port", "443"), ("web", "http_port", "22"))]
      == [other] * 6)
check("its own port, yes", [port(*a) for a in (
    ("ssh", "port", "22"), ("telnet", "port", "23"), ("web", "http_port", "80"),
    ("web", "https_port", "443"), ("ssh", "port", "2222"))] == [None] * 5)
check("the ones that are off do not count (HTTPS, SSH and Telnet); the web's HTTP always does",
      [port(*a, c=off) for a in (("telnet", "port", "443"), ("telnet", "port", "22"),
                                 ("ssh", "port", "23"), ("ssh", "port", "80"))]
      == [None, None, None, other])
check("the secure port, with its messages", [port("web", "https_port", v) for v in (
    "", "70000", "1:2", "61780", "80")] == [
    tr("Secure Server Port is required"),
    tr("Secure Server Port must be between {low} and {high}", low=1, high=65535),
    tr("Secure Server Port is invalid"), tr("Secure Server Port is used by system"),
    tr("Secure Server Port is used by another service")])
check("the number its web reads is saved (0x8AE, +2222 or 02222 are 2222)",
      [T.service_port(v) for v in ("0x8AE", "+2222", "02222", " 2222 ")] == ["2222"] * 4
      and all(port("ssh", "port", v) is None for v in ("0x8AE", "+2222", "02222")))

print("=== SNMP and the remote log ===")
check("SNMP: its three fields, required", [T.snmp_problem(f, "  ") for f in (
    "community", "contact", "location")] == [tr("SNMP Community is required"),
                                             tr("SNMP Contact is required"),
                                             tr("SNMP Location is required")]
      and T.snmp_problem("contact", "noc") is None)
check("remote log: the IP, required and valid", [T.syslog_problem("ip", v) for v in (
    "", "192.168.1", "192.168.1.300", " 192.168.1.100 ")] == [
    tr("Remote Log IP Address is required"), tr("Remote Log IP Address is invalid"),
    tr("Remote Log IP Address is invalid"), None])
check("  and its port, from 1 to 65535", [T.syslog_problem("port", v) for v in (
    "", "0", "70000", "5514")] == [
    tr("Remote Log Port is required"),
    tr("Remote Log Port must be between {low} and {high}", low=1, high=65535),
    tr("Remote Log Port must be between {low} and {high}", low=1, high=65535), None])

print("=== what its web saves ===")
check("SNMP: all its fields (an empty one, deleted)", T.plan_snmp({
    "on": True, "community": "c0m", "contact": "", "location": "Tower 9"}) == {
    "snmp.status": "enabled", "snmp.community": "c0m", "snmp.contact": None,
    "snmp.location": "Tower 9"})
check("web: httpd.status always enabled", T.plan_web({
    "https": False, "https_port": "443", "http_port": "8080"}) == {
    "httpd.status": "enabled", "httpd.https.status": "disabled", "httpd.https.port": "443",
    "httpd.port": "8080"})
check("SSH and Telnet", T.plan_ssh({"on": True, "port": "2222", "passwd": False}) == {
    "sshd.status": "enabled", "sshd.port": "2222", "sshd.auth.passwd": "disabled"}
      and T.plan_telnet({"on": False, "port": "23"}) == {"telnetd.status": "disabled",
                                                         "telnetd.port": "23"})
check("log: without an IP, it deletes it", T.plan_syslog({
    "on": True, "remote": False, "ip": "", "port": "514"}) == {
    "syslog.status": "enabled", "syslog.remote.status": "disabled", "syslog.remote.ip": None,
    "syslog.remote.port": "514"})

print("=== the SSH authorized keys ===")
ten = cfg(**{f"sshd__auth__key__{n}__value": key(n) for n in range(1, 11)})
check("it reads all 10 (its web reads up to the 9th and would lose the 10th on saving)",
      [k["value"] for k in T.ssh_keys(ten)] == [key(n) for n in range(1, 11)])
check("its status, type and comment; without a status, enabled (its model)", T.ssh_keys(cfg(
    sshd__auth__key__1__value="AAAA", sshd__auth__key__1__status="disabled",
    sshd__auth__key__1__type="ssh-rsa", sshd__auth__key__1__comment="me@pc",
    sshd__auth__key__2__comment="no value", sshd__auth__key__3__value="BBBB")) == [
    {"value": "AAAA", "type": "ssh-rsa", "comment": "me@pc", "on": False},
    {"value": "BBBB", "type": "", "comment": "", "on": True}])
check("a file: one per line, without empty lines", T.parse_ssh_keys(
    f"ssh-rsa {key(1)} a@pc\n\n   ssh-rsa {key(2)}  \nssh-dsa {key(3)} b\n") == [
    {"type": "ssh-rsa", "value": key(1), "comment": "a@pc", "on": True},
    {"type": "ssh-rsa", "value": key(2), "comment": "", "on": True},
    {"type": "ssh-dsa", "value": key(3), "comment": "b", "on": True}])
check("only ssh-rsa and ssh-dsa, and the comment without spaces, as in its web",
      T.parse_ssh_keys(f"ssh-ed25519 {key(1)} x") is None
      and T.parse_ssh_keys(f"ssh-rsa {key(1)} two words") is None
      and T.parse_ssh_keys(f"ssh-rsa {key(1)}\njunk") is None and T.parse_ssh_keys("\n") == [])
mine = T.parse_ssh_keys(f"ssh-rsa {key(1)} a")
check("when adding, the ones already there are left out", T.add_ssh_keys(mine, T.parse_ssh_keys(
    f"ssh-rsa {key(1)} another\nssh-rsa {key(2)} b\nssh-rsa {key(2)} c")) == mine + [
    {"type": "ssh-rsa", "value": key(2), "comment": "b", "on": True}])
check("as its web lists them: RSA or DSA (only ssh-dss), start and end, comment",
      T.ssh_key_shown({"type": "ssh-rsa", "value": "A" * 20 + "B" * 20, "comment": "me"})
      == "RSA  AAAAAAAAAAAAAAAA … BBBBBBBBBBBBBBBB  me"
      and T.ssh_key_shown({"type": "ssh-dss", "value": "CCCC", "comment": ""}) == "DSA  CCCC"
      and T.ssh_key_shown({"type": "ssh-dsa", "value": "CCCC", "comment": ""}) == "RSA  CCCC")
three = cfg(**{f"sshd__auth__key__{n}__{f}": v for n in (1, 2, 3)
               for f, v in (("value", key(n)), ("type", "ssh-rsa"), ("comment", f"c{n}"),
                            ("status", "enabled"))})
keys = T.ssh_keys(three)
plan = T.plan_ssh_keys(three, [keys[0], dict(keys[2], on=False, comment="")])
check("removing the 2nd: the 3rd becomes the 2nd and no 3rd is left", plan == {
    "sshd.auth.key.2.value": key(3), "sshd.auth.key.2.comment": None,
    "sshd.auth.key.2.status": "disabled", "sshd.auth.key.3.value": None,
    "sshd.auth.key.3.type": None, "sshd.auth.key.3.comment": None,
    "sshd.auth.key.3.status": None}, str(plan))


def start(port, web_follow=False):
    srv = S.make_server(8, port, restart=0.3, test_secs=60, web_follow=web_follow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dev = T.AirOS(f"http://127.0.0.1:{port}", "ubnt", "ubnt")
    dev.connect()
    ui = FakeUI()
    app = T.App(ui, dev, wait=1)
    app.load()
    return srv, srv.dev, ui, app


def errors(ui):
    return [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Invalid value")]


def rows(ui, title=None):
    """The options of the last menu with that title (Services, if none is given)."""
    return dict([items for t, items, _ in ui.menus if t == (title or tr("Services"))][-1])


def flow(port):
    print("=== v8: SNMP, Telnet, the remote log and SSH with its keys; test and keep ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [("menu", None)]
        app.services_menu()
        check("in the Services menu, as on its page (and the main menu's summary)", {
            k: v for k, v in rows(ui).items() if k in "ahstg"} == {
            "a": tr("SNMP Agent: disabled"),
            "h": tr("Web Server: HTTP on port {http}, without HTTPS", http=80),
            "s": tr("SSH Server: enabled, port {port}, {count} key(s)", port=22, count=0),
            "t": tr("Telnet Server: disabled"), "g": tr("System Log: enabled")}
              and [k for k in rows(ui)] == list("dclwahstgu")
              and app._services_label() == tr("Services: web, SSH, SNMP, discovery, ping "
                                              "watchdog, UISP and more"), str(rows(ui)))
        pub = write("keys.pub", f"ssh-rsa {key(1)} a@pc\n\nssh-rsa {key(2)} b@pc\n"
                                f"ssh-rsa {key(1)} a@pc\n")
        ui.menus.clear()
        ui.log.clear()
        ui.script = [
            ("menu", "a"), ("menu", "o"),                      # SNMP: it asks for what is missing
            ("input", " "), ("input", "noc@example.com"), ("input", "Tower 9"),
            ("menu", "c"), ("input", ""), ("input", "s3cret"), ("menu", None),
            ("menu", "t"), ("menu", "o"), ("yesno", 0),          # Telnet: warns, not encrypted
            ("menu", "p"), ("input", "22"), ("input", "80"), ("input", "53762"),
            ("input", "2323"), ("menu", None),
            ("menu", "g"), ("menu", "r"), ("input", "192.168.1"),  # remote log
            ("input", "192.168.1.100"), ("menu", "p"), ("input", "70000"), ("input", "5514"),
            ("menu", None),
            ("menu", "s"), ("menu", "p"), ("input", "2323"),     # SSH: Telnet's, not any more
            ("input", "0x8AE"), ("menu", "w"), ("yesno", 1),     # without keys it warns: "No"
            ("menu", "k"), ("menu", "a"), ("input", pub),        # keys from a file
            ("menu", "a"), ("input", f"ssh-ed25519 {key(9)} x"),  # one its web does not take
            ("menu", "a"), ("input", f"ssh-rsa {key(3)} pasted"),  # a pasted one
            ("menu", "2"), ("menu", "e"),                        # disable the 2nd
            ("menu", "1"), ("menu", "c"), ("input", " laptop "),
            ("menu", "3"), ("menu", "x"), ("yesno", 0),          # remove the 3rd
            ("menu", None), ("menu", "w"), ("menu", None),       # with keys it does not ask
            ("menu", None)]
        app.services_menu()
        check("script used up", not ui.script, str(ui.script[:3]))
        check("its web's messages, in order", errors(ui) == [
            tr("SNMP Contact is required"), tr("SNMP Community is required"), other, other,
            system, tr("Remote Log IP Address is invalid"),
            tr("Remote Log Port must be between {low} and {high}", low=1, high=65535), other],
              str(errors(ui)))
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("it warns: Telnet unencrypted, nobody gets in without a password or keys, removing "
              "the key",
              len(asked) == 3 and tr("Enable the Telnet server?\n\nTelnet is not encrypted: the "
                                     "user and the password travel in plain text.") in asked[0]
              and tr("Disable password authentication?\n\nThere are no authorized keys: nobody "
                     "will be able to log in by SSH.") in asked[1]
              and asked[2].startswith(tr("Remove this key?\n\n{key}",
                                         key=f"RSA  {key(3)[:16]} … {key(3)[-16:]}  pasted")),
              str(asked))
        notes = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("SSH Authorized Keys")]
        check("the keys: the file's (3, one repeated) and the one not of its type", notes == [
            tr('Found {count} key(s) in "{file}".', count=3, file="keys.pub"), tr(
                "That is not a public key as its web takes it: ssh-rsa, the key and, if there "
                "is one, a comment without spaces.")], str(notes))
        keys_menu = rows(ui, tr("SSH Authorized Keys"))
        check("  and two are left, marked", keys_menu == {
            "1": f"* RSA  {key(1)[:16]} … {key(1)[-16:]}  laptop",
            "2": "* " + tr("(disabled)") + f" RSA  {key(2)[:16]} … {key(2)[-16:]}  b@pc",
            "a": tr("Import public keys (a .pub file, or paste one)")}, str(keys_menu))
        check("Services marks what changed", {k: v for k, v in rows(ui).items() if k in "ahstg"}
              == {"a": "* " + tr("SNMP Agent: enabled"),
                  "h": tr("Web Server: HTTP on port {http}, without HTTPS", http=80),
                  "s": "* " + tr("SSH Server: enabled, port {port}, {count} key(s)", port=2222,
                                 count=2),
                  "t": "* " + tr("Telnet Server: enabled, port {port}", port=2323),
                  "g": "* " + tr("System Log: enabled, also to {ip}:{port}", ip="192.168.1.100",
                                 port=5514)},
              str(rows(ui)))
        ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 0)]       # test and keep
        app.apply_menu()
        want = {"snmp.status": "enabled", "snmp.community": "s3cret",
                "snmp.contact": "noc@example.com", "snmp.location": "Tower 9",
                "telnetd.status": "enabled", "telnetd.port": "2323",
                "syslog.remote.status": "enabled", "syslog.remote.ip": "192.168.1.100",
                "syslog.remote.port": "5514", "sshd.status": "enabled", "sshd.port": "2222",
                "sshd.auth.passwd": "disabled",
                "sshd.auth.key.1.value": key(1), "sshd.auth.key.1.type": "ssh-rsa",
                "sshd.auth.key.1.comment": "laptop", "sshd.auth.key.1.status": "enabled",
                "sshd.auth.key.2.value": key(2), "sshd.auth.key.2.comment": "b@pc",
                "sshd.auth.key.2.status": "disabled"}
        got = {k: d.saved.get(k) for k in want}
        check("saved on the device", got == want and not [
            k for k in d.saved if k.startswith("sshd.auth.key.3.")]
              and ui.titles()[-1] == tr("Done"), str({k: v for k, v in got.items()
                                                      if v != want[k]}) + str(ui.titles()))
        check("no pending changes", not app.changes(), str(app.changes()))
        print("=== the keys: at most 10, files that are no good ===")
        keys = T.ssh_keys(app.working)
        ui.log.clear()
        eight = write("eight.pub", "".join(f"ssh-rsa {key(n)} n{n}\n" for n in range(10, 18)))
        ui.script = [("input", eight)]
        ten = app._import_ssh_keys(keys)
        ui.script = [("input", f"ssh-rsa {key(30)}")]
        eleven = app._import_ssh_keys(ten)
        check("up to 10; with one more, its web's error", len(ten) == 10 and eleven is None
              and ui.log[-1][2] == tr("Error: maximum number of keys allowed is {max}", max=10),
              str(ui.log[-1:]))
        big = os.path.join(FILES, "big.pub")
        with open(big, "wb") as f:
            f.truncate(T.SSH_KEY_FILE_MAX + 1)
        bad, blank = write("bad.pub", f"ssh-rsa {key(1)} a\nnothing\n"), write("blank.pub", "\n \n")
        ui.log.clear()
        ui.script = [("input", big), ("input", bad), ("input", blank), ("input", "  "),
                     ("input", None), ("input", eight)]
        got = [app._import_ssh_keys(keys) for _ in range(5)] + [app._import_ssh_keys(ten)]
        check("too big, badly written, empty, nothing or cancelled: no changes",
              got == [None] * 6 and [t for k, ti, t in ui.log if k == "msgbox"] == [
                  tr("Uploaded file {name} is too big.", name="big.pub"),
                  tr('Error parsing file "{file}".', file="bad.pub"),
                  tr('Found {count} key(s) in "{file}".', count=8, file="eight.pub")],
              str(ui.log))
    finally:
        srv.cluster.shutdown() if hasattr(srv, "cluster") else srv.shutdown()


def toggles(port):
    print("=== v8: turning off and on; on turning on, a port another one uses is asked again ===")
    srv, d, ui, app = start(port)
    try:
        ui.script = [
            ("menu", "a"), ("menu", "o"), ("input", None),             # SNMP: cancel
            ("menu", "o"), ("input", "noc"), ("input", "tower"),
            ("menu", "o"), ("yesno", 1), ("menu", "o"), ("yesno", 0),  # turn it off: "No", "Yes"
            ("menu", None),
            ("menu", "s"), ("menu", "o"), ("yesno", 0), ("menu", None),  # SSH off
            ("menu", "t"), ("menu", "o"), ("yesno", 0), ("menu", "p"), ("input", "22"),
            ("menu", None),                                            # Telnet on 22
            ("menu", "s"), ("menu", "o"), ("input", None),             # SSH: its 22 is taken
            ("menu", "o"), ("input", "2222"), ("menu", None),
            ("menu", "t"), ("menu", "o"), ("yesno", 1), ("menu", "o"), ("yesno", 0),
            ("menu", None),                                            # Telnet off
            ("menu", "s"), ("menu", "p"), ("input", "22"), ("menu", None),
            ("menu", "t"), ("menu", "o"), ("yesno", 0), ("input", None),  # SSH uses its 22
            ("menu", "o"), ("yesno", 0), ("input", "2323"), ("menu", None),
            ("menu", "s"), ("menu", "p"), ("input", "443"), ("menu", None),  # free without HTTPS
            ("menu", "h"), ("menu", "s"), ("input", None),             # HTTPS: its 443, SSH's
            ("menu", "s"), ("input", "8443"), ("menu", "s"), ("yesno", 1), ("menu", None),
            ("menu", "g"), ("menu", "r"), ("input", None), ("menu", "o"), ("menu", "o"),
            ("menu", "r"), ("input", "10.0.0.5"), ("menu", "r"), ("menu", None),
            ("menu", None)]
        app.services_menu()
        check("script used up and no errors; each taken port was asked again (22 for SSH and "
              "for Telnet, 443 for HTTPS: twice, the first time cancelled)",
              not ui.script and not errors(ui) and ui.inputs == [
                  "", "", "", "23", "22", "22", "2222", "22", "22", "22", "443", "443", "", ""],
              str((ui.script[:2], errors(ui), ui.inputs)))
        check("the last choice stays (SNMP and the remote log, off as on the device: without "
              "what was typed while they were on)", app.changes() == [
            ("httpd.https.port", "443", "8443"), ("httpd.https.status", "disabled", "enabled"),
            ("sshd.port", "22", "443"),
            ("telnetd.port", "23", "2323"), ("telnetd.status", "disabled", "enabled")],
              str(app.changes()))
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("  and it asked when turning off (SNMP, SSH, Telnet, HTTPS) and when turning Telnet "
              "on",
              len(asked) == 9 and asked[-1].startswith(tr(
                  "Disable HTTPS?\n\nIts web, and this program, will then go over HTTP, "
                  "unencrypted: the password travels in plain text.")), str(len(asked)))
    finally:
        srv.shutdown()
        srv.server_close()


def web_moves(port):
    print("=== v8: the web changes its port and the program follows it ===")
    srv, d, ui, app = start(port, web_follow=True)
    new, newer, https = port + 1, port + 3, port + 5
    old_url, new_url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{new}"
    try:
        ui.script = [("menu", "h"), ("menu", "h"), ("input", "22"), ("input", str(new)),
                     ("menu", None), ("menu", None)]
        app.services_menu()
        web_menu = rows(ui, tr("Web Server"))
        check("its page: HTTPS disabled and the port; SSH's is not valid", errors(ui) == [other]
              and web_menu == {"s": tr("Secure Connection (HTTPS): disabled"),
                               "h": "* " + tr("Server Port: {port}", port=new)}
              and rows(ui)["h"] == "* " + tr("Web Server: HTTP on port {http}, without HTTPS",
                                             http=new),
              str(web_menu))
        ui.log.clear()
        ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 1)]       # test and revert
        app.apply_menu()
        asked = [t for k, ti, t in ui.log if k == "yesno"]
        check("it warns first: its web moves and the program follows it", asked and tr(
            "⚠ Its web moves: {old} → {new}. The program will reconnect there.",
            old=old_url, new=new_url) in asked[0], str(asked[:1]))
        check("  and in test mode: where it answers now", len(asked) > 1 and tr(
            "Its web now answers at {new} (before {old}).", new=new_url, old=old_url)
              in asked[1], str(asked[1:2]))
        check("revert: back to its port, and the program with it", ui.titles()[-1] == tr(
            "Reverted") and app.dev.base == old_url and d.running.get("httpd.port") == str(port)
              and app.original.get("httpd.port") == str(port) and app.dev.ping()
              and app.changes() == [("httpd.port", str(port), str(new))],
              str((ui.titles(), app.dev.base)))
        ui.log.clear()
        ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 0)]       # test and keep
        app.apply_menu()
        gone = T.AirOS(old_url, "ubnt", "ubnt")
        check("keep: saved, and the program stays on the new port", ui.titles()[-1] == tr(
            "Done") and app.dev.base == new_url and d.saved.get("httpd.port") == str(new)
              and app.dev.ping() and not gone.ping() and not app.changes(),
              str((ui.titles(), app.dev.base)))
        ui.script = [("menu", "h"), ("menu", "h"), ("input", str(newer)), ("menu", None),
                     ("menu", None)]
        app.services_menu()
        ui.log.clear()
        ui.script = [("menu", "1"), ("yesno", 0), ("yesno", 255)]     # not confirmed
        app.apply_menu()
        note = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Not confirmed")]
        check("not confirmed: the program points to its port from before again", note and tr(
            "Its web will then answer at {address} again: the program already points there.",
            address=new_url) in note[0] and app.dev.base == new_url, str(note))
        other_client = T.AirOS(f"http://127.0.0.1:{newer}", "ubnt", "ubnt")
        other_client.connect()
        other_client.discard_test()                    # (what the end of the test would do)
        back = any(app.dev.ping() or time.sleep(0.3) for _ in range(30))
        check("  and it is there when it reverts", back and d.running.get("httpd.port")
              == str(new))
        app.working = app.original.copy()
        print("=== v8: HTTPS on another port that does not answer (the simulator only speaks "
              "HTTP) ===")
        ui.menus.clear()
        ui.log.clear()
        ui.script = [("menu", "h"), ("menu", "s"), ("menu", "p"), ("input", str(https)),
                     ("menu", None), ("menu", None)]
        app.services_menu()
        check("HTTPS: enabled, on its port", rows(ui)["h"] == (
            "* " + tr("Web Server: HTTPS on port {https}, HTTP on {http}", https=https,
                      http=new)), str(rows(ui)))
        real_wait = app.wait_for_device
        app.wait_for_device = lambda title, initial, max_wait=None, alt=None: real_wait(
            title, initial, 3, alt)
        ui.log.clear()
        ui.script = [("menu", "1"), ("yesno", 0)]
        app.apply_menu()
        app.wait_for_device = real_wait
        msg = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("No answer")]
        check("no answer: it says so and the program goes back to its web from before", msg and tr(
            "The device does not answer at {address}.", address=f"https://127.0.0.1:{https}")
              in msg[0] and tr("Its web will then answer at {address} again: the program "
                               "already points there.", address=new_url) in msg[0]
              and app.dev.base == new_url and app.dev.ping(), str(msg))
        app.dev.discard_test()
        print("=== when its web moves ===")
        app.working = app.original.copy()
        app.original.set("httpd.https.status", "enabled")
        app.working.set("httpd.https.status", "enabled")
        app.working.set("httpd.port", "8081")
        check("with HTTPS, changing the HTTP port does not move it", app._web_move() is None)
        app.working.set("httpd.https.status", "disabled")
        check("  turning HTTPS off does", app._web_move() == {"old": ("http", new),
                                                              "new": ("http", 8081)})
        app.dev.flavor = "v6"
        check("airOS 6: no (it does not have these settings here)", app._web_move() is None)
        app.dev.flavor = "v8"
        app.working = app.original.copy()
    finally:
        srv.cluster.shutdown()


def backup_moves(port):
    print("=== an uploaded backup that moves its web and changes its administrator: the program "
          "follows it ===")
    srv, d, ui, app = start(port, web_follow=True)
    new = port + 2
    old_url, new_url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{new}"
    try:
        data = app.dev.backup_config()
        text = data.decode() if isinstance(data, bytes) else data
        text = text.replace(f"httpd.port={port}\n", f"httpd.port={new}\n").replace(
            "users.1.name=ubnt\n", "users.1.name=boss\n")              # (the same password)
        os.makedirs(XDG, exist_ok=True)
        path = os.path.join(XDG, "moving-backup.cfg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        ui.log.clear()
        ui.script = [("menu", "o"), ("input", path), ("yesno", 0), ("yesno", 0)]
        try:
            app.restore_backup()
        except AssertionError as e:
            print("     script:", e)
        done = [t for k, ti, t in ui.log if k == "msgbox" and ti == tr("Applying the backup")]
        check("it applies it and follows it: its web on the new port, and it logs in with its "
              "administrator; it says so",
              not ui.script and len(done) == 1 and done[0].startswith(tr("Backup applied."))
              and app.dev.base == new_url and app.dev.username == "boss"
              and app.original.get("httpd.port") == str(new) and tr(
                  "Its web now answers at {new} (before {old}).", new=new_url, old=old_url)
              in done[0] and tr("Its administrator is now {new} (before {old}): the program "
                                "logs in with that name.", new="boss", old="ubnt") in done[0],
              str((app.dev.base, app.dev.username, ui.titles(), done[:1])))
    finally:
        srv.cluster.shutdown()


flow(19701)
toggles(19721)
web_moves(19711)
backup_moves(19731)
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
