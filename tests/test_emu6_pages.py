"""The Wireless (link.cgi) and Advanced (advanced.cgi) pages of a real airOS 6 (its web,
emulated: see emu6.py) against the program. For each change, what the web writes when the page
is saved with it (the difference with saving it as it is, sent as its browser sends it) is what
the program prepares (plan_wireless); and what the page shows is what the program reads, also
with a repeated key (what the device applies may be another value: see Config). Its
JavaScript does not run: what it sets when the page loads is taken from the page itself
(js_init), and the fields it disables are not sent (js_fixes). Skipped if something is
missing."""
import html.parser
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "emu"))
import emu6  # noqa: E402

if os.environ.get("AIROS_EMU6") != "1":            # in namespaces of its own (no root)
    lack = emu6.missing()
    if lack:
        print("skipped: missing " + "; ".join(lack))
        print("FAILURES: none")
        sys.exit(0)
    os.environ["AIROS_EMU6"] = "1"
    os.execvp("unshare", ["unshare", "-rnmpf", "--kill-child", "--mount-proc",
                          sys.executable, os.path.abspath(__file__)])
from _common import check, fails, suite, WORK  # noqa: E402
T, S, XDG = suite("emu6-pages")
FORMS = {"/link.cgi": "this_form", "/advanced.cgi": "adv_form"}


class FormParser(html.parser.HTMLParser):
    """The named fields of one form of a page: (name, value or None, kind, disabled), with
    a checkbox's or radio button's value only when checked; and what each checkbox sends."""

    def __init__(self, form_id):
        super().__init__(convert_charrefs=True)
        self.form_id, self.inside = form_id, False
        self.fields, self.boxes = [], {}
        self.select = self.option = self.textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.inside = a.get("id") == self.form_id
            return
        if not self.inside:
            return
        disabled = "disabled" in a
        if tag == "input" and a.get("name"):
            kind = (a.get("type") or "text").lower()
            if kind in ("button", "submit", "reset", "image", "file"):
                return
            if kind in ("checkbox", "radio"):
                value = a.get("value", "on")
                self.boxes.setdefault(a["name"], value)
                self.fields.append((a["name"], value if "checked" in a else None, kind,
                                    disabled))
            else:
                self.fields.append((a["name"], a.get("value", ""), kind, disabled))
        elif tag == "select" and a.get("name"):
            self.select = [a["name"], disabled, []]
        elif tag == "option" and self.select is not None:
            self.option = [a.get("value"), "selected" in a, ""]
        elif tag == "textarea" and a.get("name"):
            self.textarea = [a["name"], disabled, ""]

    def handle_data(self, data):
        for open_one in (self.option, self.textarea):
            if open_one is not None:
                open_one[2] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "option" and self.select is not None and self.option is not None:
            value = self.option[0] if self.option[0] is not None else self.option[2].strip()
            self.select[2].append((value, self.option[1]))
            self.option = None
        elif tag == "select" and self.select is not None:
            name, disabled, options = self.select
            chosen = [v for v, sel in options if sel] or [v for v, _ in options[:1]]
            self.fields.append((name, chosen[0] if chosen else None, "select", disabled))
            self.select = None
        elif tag == "textarea" and self.textarea is not None:
            name, disabled, text = self.textarea
            self.fields.append((name, text, "textarea", disabled))
            self.textarea = None


def js_init(text, fields):
    """What its JavaScript sets when link.cgi loads, from what the page itself carries:
    $('#x').val("…") and selectRadio("x", "…") with literal values, and (its reinit_form)
    the mode and the extension channel from its ieee_mode_fixer("…", "…"), the frequency and
    the width from its chan_freq and chanbw (the width, as its submit handler adds it)."""
    vals = dict(re.findall(r"""\$\(['"]#(\w+)['"]\)\.val\("([^"]*)"\)""", text))
    radios = dict(re.findall(r'selectRadio\("(\w+)",\s*"([^"]*)"\)', text))
    glob = dict(re.findall(r'^\s*var\s+(\w+)\s*=\s*"([^"]*)"\s*;', text, re.M))
    m = re.search(r'ieee_mode_fixer\("([^"]*)"\.toLowerCase\(\),\s*"([^"]*)"\)', text)
    if m:
        mode = m.group(1).lower()
        vals["ieee_mode"] = mode[:6] if mode[:6] in ("11naht", "11nght") else "11naht"
        ext = mode[8:] if mode[6:8] == "40" else ""
        vals["extchannel"] = ext if ext in ("plus", "minus") else ""
        for name in ("chan_freq", "chanbw"):
            if name in glob:
                vals[name] = glob[name]
    out = []
    for name, value, kind, disabled in fields:
        shown = value
        if kind == "radio" and name in radios:
            if any(f[0] == name for f in out):
                continue
            shown = radios[name]
        elif name in vals and kind != "checkbox":
            shown = vals[name]
        out.append((name, shown, kind, disabled))
    return out


def js_fixes(fields):
    """The fields its JavaScript disables before sending: EAP's for a pre-shared key
    (setWpaEnabled), and a value whose "off" is checked (handleOff)."""
    names = dict(fields)
    drop = set()
    if names.get("wpa_auth") == "WPA-PSK":
        drop |= {"wpa_eap", "wpa_inner", "wpa_ident", "wpa_user", "wpa_passwd"}
    for value, off in (("rts", "rtsoff"), ("rssi_sens", "rssi_sensoff")):
        if off in names:
            drop.add(value)
    return [f for f in fields if f[0] not in drop]


root = emu6.prepare(os.path.join(WORK, "emu6-pages-root"))
web = emu6.start(root)
SYSTEM = os.path.join(root, "tmp", "system.cfg")
BOXES = {}                                      # checkbox -> what it sends when checked
CHECKED = object()


def put_config(text):
    for rel in ("tmp/system.cfg", "tmp/running.cfg"):
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)


def page_fields(page, html_bytes):
    text = html_bytes.decode("utf-8", "replace")
    p = FormParser(FORMS[page])
    p.feed(text)
    BOXES.update(p.boxes)
    fields = js_init(text, p.fields) if page == "/link.cgi" else p.fields
    return [[n, v] for n, v, kind, disabled in fields if not disabled and v is not None]


def post(page, fields):
    body, ctype = T._multipart([(n, v) for n, v in fields], [])
    status, _, data = dev._raw("POST", page, body, {"Content-Type": ctype}, timeout=60)
    assert status == 200, (page, status)
    return data


def save(page, base, change=None):
    """The configuration after saving page from base, its form as its browser sends it,
    changed by change (fields → fields), as a user would change it."""
    put_config(base)
    _, _, html_bytes = dev._req("GET", page)
    fields = js_fixes(page_fields(page, html_bytes))
    if change:
        fields = js_fixes(change(fields))
    post(page, fields)
    return T.Config(open(SYSTEM, encoding="utf-8").read(), "v6")


def setf(**values):
    """A change of fields: their new values (None: not sent, as an unchecked checkbox;
    CHECKED: what its checkbox sends)."""
    def change(fields):
        for name, value in values.items():
            fields = [f for f in fields if f[0] != name]
            if value is CHECKED:
                fields.append([name, BOXES[name]])
            elif value is not None:
                fields.append([name, value])
        return fields
    return change


def compare(label, page, base, change, changes):
    """What the change leaves different from base, saved by the web and planned by the
    program (a default the page writes only when saved as it is is not the change's)."""
    start = T.Config(base, "v6")
    as_is, changed = save(page, base), save(page, base, change)
    web_did = {k: b for k, a, b in as_is.diff(changed) if b != start.get(k)}
    plan = {k: v for k, v in T.plan_wireless(start, "v6", board, changes).items()
            if v != start.get(k)}
    check(label, web_did == plan, "; ".join(
        f"{k}: web={web_did.get(k, '·')!r} program={plan.get(k, '·')!r}"
        for k in sorted(set(web_did) | set(plan)) if web_did.get(k) != plan.get(k)))


def shows(label, base):
    """What link.cgi shows (its form, after its JavaScript loads) is what the program reads."""
    put_config(base)
    _, _, html_bytes = dev._req("GET", "/link.cgi")
    f = dict(page_fields("/link.cgi", html_bytes))
    st = T.wireless_state(T.Config(base, "v6"), "v6", board)
    seen = {"wmode": f.get("wmode"), "essid": f.get("essid"), "txpower": f.get("txpower"),
            "security": f.get("security"), "wpa_key": f.get("wpa_key", ""),
            "chan_freq": f.get("chan_freq"), "chanbw": f.get("chanbw"),
            "ieee_mode": f.get("ieee_mode"), "extchannel": f.get("extchannel"),
            "country": f.get("country")}
    read = {"wmode": st["wmode6"], "essid": st["ssid"], "txpower": str(st["power"]),
            "security": st["security"], "wpa_key": st["psk"], "chan_freq": str(st["freq"]),
            "chanbw": str(st["chanbw"]), "ieee_mode": st["ieee"], "extchannel": st["ext"],
            "country": str(st["country"])}
    check(label, seen == read, "; ".join(f"{k}: page={seen[k]!r} program={read[k]!r}"
                                         for k in seen if seen[k] != read[k]))


try:
    emu6.first_login(root)
    dev = T.AirOS(f"https://{emu6.HOST}", "ubnt", emu6.PASSWORD)
    dev.connect()
    board = dev.board_info()
    STA = open(SYSTEM, encoding="utf-8").read()

    print("=== station (its factory configuration after the first login) ===")
    shows("what Wireless shows is what the program reads (the width, not configured: "
          "40, Auto 20/40)", STA)
    rest = [line for line in STA.splitlines() if not line.startswith("wireless.1.ssid=")]
    shows("a repeated SSID, one of them the first entry: that one, as its web",
          "\n".join(["wireless.1.ssid=First"] + rest + ["wireless.1.ssid=Last"]) + "\n")
    shows("a repeated SSID, neither the first entry: the last one",
          "\n".join(rest[:5] + ["wireless.1.ssid=Earlier"] + rest[5:]
                    + ["wireless.1.ssid=Later"]) + "\n")
    for label, change, changes in (
            ("SSID", setf(essid="New-Network"), {"ssid": "New-Network"}),
            ("power", setf(txpower="20"), {"power": 20}),
            ("Lock to AP MAC", setf(apmac="00:11:22:33:44:55"),
             {"ap_mac": "00:11:22:33:44:55"}),
            ("WPA2 with a key", setf(security="wpa2aes", wpa_auth="WPA-PSK",
                                     wpa_key="test-passphrase"),
             {"security": "wpa2aes", "wpa_auth": "WPA-PSK", "psk": "test-passphrase"}),
            ("channel scan list", setf(channel_scan_list=CHECKED, scan_channels="5745,5765"),
             {"scan_on": True, "scan": [5745, 5765]}),
            ("DFS off", setf(dfs=None), {"dfs_on": False}),
            ("MAC clone (boards with that feature)", setf(macclone="enabled"),
             {"macclone": True}),
            ("WDS", setf(wds_chkbox=CHECKED), {"wds": True})):
        compare("Wireless: " + label, "/link.cgi", STA, change, changes)
    for label, change, changes in (
            ("a fixed distance of 3 km", setf(autoack=None, ackdistance="3000"),
             {"ack_auto": False, "distance": 3000}),
            ("RTS 500", setf(rtsoff=None, rts="500"), {"rts": 500}),
            ("no aggregation", setf(aggr_status=None), {"aggr_on": False}),
            ("no multicast", setf(mcast_status=None), {"mcast_data": False}),
            ("sensitivity -80", setf(rssi_sensoff=None, rssi_sens="-80"), {"rx_sens": -80})):
        compare("Advanced: " + label, "/advanced.cgi", STA, change, changes)

    print("=== access point (made with its web: changing the mode reloads the page) ===")
    put_config(STA)
    _, _, html_bytes = dev._req("GET", "/link.cgi")
    html_bytes = post("/link.cgi", setf(wmode="ap", cc="changed")(
        page_fields("/link.cgi", html_bytes)))
    post("/link.cgi", js_fixes(setf(essid="AP-Emu", chan_freq="5745", ieee_mode="11naht",
                                    chanbw="20", extchannel="", security="wpa2aes",
                                    wpa_auth="WPA-PSK", wpa_key="ap-passphrase")(
        page_fields("/link.cgi", html_bytes))))
    AP = open(SYSTEM, encoding="utf-8").read()
    ap = T.wireless_state(T.Config(AP, "v6"), "v6", board)
    check("its web makes it an access point, on 5745 MHz, with WPA2", (ap["wmode6"], ap["freq"],
          ap["security"], ap["psk"]) == ("ap", 5745, "wpa2aes", "ap-passphrase"),
          str((ap["wmode6"], ap["freq"], ap["security"])))
    shows("what Wireless shows is what the program reads", AP)
    for label, change, changes in (
            ("SSID", setf(essid="Other-AP"), {"ssid": "Other-AP"}),
            ("hide the SSID", setf(hidessid="enabled"), {"hide_ssid": True}),
            ("frequency", setf(chan_freq="5765"), {"freq": 5765}),
            ("40 MHz, upper extension channel", setf(chanbw="40", extchannel="plus"),
             {"chanbw": 40, "ext": "plus"}),
            ("another key", setf(wpa_key="another-wpa-key"), {"psk": "another-wpa-key"}),
            ("MAC list, denying", setf(mac_acl_status="enabled", mac_acl_policy="deny"),
             {"acl_on": True, "acl_policy": "deny"}),
            ("power", setf(txpower="18"), {"power": 18})):
        compare("Wireless (AP): " + label, "/link.cgi", AP, change, changes)
finally:
    web.terminate()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
