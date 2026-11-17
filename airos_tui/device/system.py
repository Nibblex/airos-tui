"""The web's System page: the device name, as each web saves it, its date settings (airOS
8: the time zone, the startup date and the NTP client), the rules of the administrator
password (airOS 8: its "Change Password" dialog), what its Device Maintenance needs (airOS 8:
where the device is after a reset, the names of its files and the kind of a configuration
backup) and, on airOS 8, the rest of that page as its models read and save it: the Device
section's other settings, the Management Radio, the System Accounts and the Location."""
from __future__ import annotations

import datetime
import math
import re

from ..core.i18n import N_, tr
from ..core.config import Config, gen3, _int, js_parse_int, looks_like_cfg, _model_bool
from ..core.validation import _v_hostname

# -- device name -------------------------------------------------------------------------

DEVICE_NAME_KEY = "resolv.host.1.name"
DEVICE_NAME_MAX = 64              # airOS 8: DeviceModel, rangeLength [1, 64]


def device_name(cfg: Config, flavor: str | None, board: dict) -> str:
    """The device name as the System page shows it. airOS 8 shows the model's name
    (board.name) when the configuration has none."""
    name = cfg.get(DEVICE_NAME_KEY) or ""
    if not name and flavor == "v8":
        return board.get("board.name") or ""
    return name


def device_name_problem(name: str, flavor: str | None) -> str | None:
    """What the web says of a device name, or None if it takes it. airOS 8: required and
    1 to 64 characters, any of them; airOS 6 keeps 1 to 64 printable ASCII characters."""
    if flavor != "v8":
        return _v_hostname(name)
    if not name.strip():
        return tr("The device name is required.")
    if _js_len(name) > DEVICE_NAME_MAX:
        return tr("The device name must be between 1 and {max} characters.",
                  max=DEVICE_NAME_MAX)
    return None


def device_name_updates(name: str, flavor: str | None, board: dict) -> dict[str, str]:
    """The keys saving the name writes. airOS 8 saves the model's own name as empty (the
    device then uses its model name) and enables the section (its network model does it
    on every save)."""
    if flavor == "v8" and name == (board.get("board.name") or None):
        name = ""
    return {DEVICE_NAME_KEY: name, "resolv.host.1.status": "enabled"}


# -- device maintenance (airOS 8: MaintenanceView) ---------------------------------------

# -- date settings (airOS 8: the DateModel of its System page) ----------------------------

# The time zones its web offers: the value it saves (a POSIX TZ string) and its label.
TIME_ZONES = (
    ('GMT+12', '(GMT-12:00) International Date Line West'),
    ('SST11', '(GMT-11:00) Midway Island, Samoa'),
    ('HST10', '(GMT-10:00) Hawaii'),
    ('AKST9AKDT,M3.2.0,M11.1.0', '(GMT-09:00) Alaska'),
    ('PST8PDT,M3.2.0,M11.1.0', '(GMT-08:00) Pacific Standard Time'),
    ('MST7MDT,M3.2.0,M11.1.0', '(GMT-07:00) Mountain Standard Time'),
    ('CST6CDT,M3.2.0,M11.1.0', '(GMT-06:00) Central Standard Time'),
    ('EST5EDT,M3.2.0,M11.1.0', '(GMT-05:00) Eastern Standard Time'),
    ('VET4:30', '(GMT-04:30) Caracas'),
    ('AST4', '(GMT-04:00) Eastern Caribbean Time'),
    ('NST3:30NDT,M3.2.0/0:01,M11.1.0/0:01', '(GMT-03:30) Newfoundland'),
    ('ART3', '(GMT-03:00) Greenland, Buenos Aires'),
    ('GST2', '(GMT-02:00) Mid-Atlantic'),
    ('CVT1', '(GMT-01:00) Cape Verde Time'),
    ('WET0WEST,M3.5.0/1,M10.5.0', '(GMT) Western European Time'),
    ('UTC', '(UTC/GMT) Universal Time Coordinated'),
    ('CET-1CEST,M3.5.0,M10.5.0/3', '(GMT+01:00) Central European Time'),
    ('EET-2EEST,M3.5.0/3,M10.5.0/4', '(GMT+02:00) Eastern European Time'),
    ('AST-3', '(GMT+03:00) Baghdad, Riyadh, St. Petersburg'),
    ('IRST-3:30IRDT,80/0,264/0', '(GMT+03:30) Tehran'),
    ('GST-4', '(GMT+04:00) Abu Dhabi, Muscat, Baku, Tbilisi'),
    ('AFT-4:30', '(GMT+04:30) Kabul'),
    ('YEKT-5YEKST,M3.5.0,M10.5.0/3', '(GMT+05:00) Ekaterinburg, Islamabad, Karachi, Tashkent'),
    ('IST-5:30', '(GMT+05:30) Bombay, Calcutta, Madras, New Delhi'),
    ('NPT-5:45', '(GMT+05:45) Kathmandu'),
    ('BDT-6', '(GMT+06:00) Almaty, Dhaka, Colombo'),
    ('WIT-7', '(GMT+07:00) Bangkok, Hanoi, Jakarta'),
    ('WST-8', '(GMT+08:00) Beijing, Perth, Singapore, Hong Kong'),
    ('JST-9', '(GMT+09:00) Tokyo, Seoul, Osaka, Sapporo, Yakutsk'),
    ('CST-9:30CST,M10.1.0,M4.1.0/3', '(GMT+09:30) Adelaide, Darwin'),
    ('EST-10', '(GMT+10:00) Eastern Australia, Guam, Vladivostok'),
    ('MAGT-11MAGST,M3.5.0,M10.5.0/3', '(GMT+11:00) Magadan, Solomon Islands, New Caledonia'),
    ('NZST-12NZDT,M9.5.0,M4.1.0/3', '(GMT+12:00) Auckland, Wellington, Fiji, Kamchatka'),
)
# On Gen3 boards (60 GHz) its list has these two, at the same places.
_GEN3_ZONES = {14: ('GMT0BST,M3.5.0/1,M10.5.0', '(GMT/BST) Europe/London'),
               15: ('UTC', '(GMT) Western Europe Time')}
NTP_SERVER = "0.ubnt.pool.ntp.org"          # its default
DATE_KEYS = ("system.timezone", "system.date.status", "system.date.timestamp",
             "ntpclient.status", "ntpclient.1.status", "ntpclient.1.server")
_TIMESTAMP_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})[0-9]{4}$")


def time_zones(board: dict) -> tuple:
    """The time zones its web offers on this board."""
    if not gen3(board):
        return TIME_ZONES
    return tuple(_GEN3_ZONES.get(i, zone) for i, zone in enumerate(TIME_ZONES))


def zone_label(zone: str, board: dict) -> str:
    """The time zone as its web's list names it (as it is, if not in the list)."""
    return next((label for value, label in time_zones(board) if value == zone), zone)


def zone_short(zone: str, board: dict) -> str:
    """Its offset, as the label starts with it (GMT-03:00), or the value itself."""
    m = re.match(r"\(([^)]*)\)", zone_label(zone, board))
    return m.group(1) if m else zone


def date_settings(cfg: Config) -> dict:
    """The date settings as its web reads them (its model's load): a missing key keeps the
    model's default (UTC, no startup date, the NTP client off, with 0.ubnt.pool.ntp.org)."""
    def on(key):
        return cfg.get(key) in ("enabled", "true", "1")
    zone, server = cfg.get("system.timezone"), cfg.get("ntpclient.1.server")
    m = _TIMESTAMP_RE.match(cfg.get("system.date.timestamp") or "")
    return {"zone": "UTC" if zone is None else zone, "startup": on("system.date.status"),
            "date": f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else "",
            "ntp": on("ntpclient.status"), "server": NTP_SERVER if server is None else server}


def startup_date(text: str) -> datetime.date | None:
    """A date as its web's field takes it: MM/DD/YYYY, as its calendar writes it (and
    YYYY-MM-DD)."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            pass
    return None


def date_problem(field: str, value) -> str | None:
    """What the web says of the startup date and the NTP server (their rules apply while each
    one is on). An empty date is an invalid one there too: its check comes first."""
    if field == "server":
        return None if str(value).strip() else tr("NTP Server is required")
    return None if startup_date(value) else tr("Invalid Startup Date")


def plan_date(ds: dict) -> dict[str, str | None]:
    """The keys its web saves for the date settings (None = delete: its configuration leaves
    empty values out): the startup date as YYYYMMDD0000, the NTP client in both its keys."""
    day = startup_date(ds["date"]) if ds["date"] else None
    ntp = "enabled" if ds["ntp"] else "disabled"
    return {"system.timezone": ds["zone"] or None,
            "system.date.status": "enabled" if ds["startup"] else "disabled",
            "system.date.timestamp": day.strftime("%Y%m%d") + "0000" if day else None,
            "ntpclient.status": ntp, "ntpclient.1.status": ntp,
            "ntpclient.1.server": ds["server"] or None}


# -- device maintenance --------------------------------------------------------------------

DEFAULT_URL = "http://192.168.1.20/"       # the web's defaultUrl: where it goes after a reset
DEFAULT_USER = DEFAULT_PASSWORD = "ubnt"    # its defaultUserName and defaultPassword
RESET_WAIT = 25                            # seconds it waits after a reset, plus board.reboot


def reset_wait(board: dict) -> int:
    """How long the web waits after a reset before going to the default address: 25 seconds
    plus the board's reboot time (board.reboot)."""
    return RESET_WAIT + (_int(board.get("board.reboot")) or 0)


def _device_file(full_version: str, board: dict, ext: str) -> str:
    prefix = (full_version or "").split(".")[0]
    mac = (board.get("board.hwaddr") or "").replace(":", "")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{prefix}-{mac}.{ext}")


def support_filename(full_version: str, board: dict) -> str:
    """The name the web gives the support file: the firmware prefix and the MAC, without
    its colons (WA-68D79A98FBFF.sup)."""
    return _device_file(full_version, board, "sup")


def backup_filename(full_version: str, board: dict) -> str:
    """The name the device gives its configuration backup (cfg.cgi): WA-68D79A98FBFF.cfg."""
    return _device_file(full_version, board, "cfg")


# Between the parts of the device's backup file: "### PART ###", or with its marks, as 8.7.x
# writes it when the password is SHA-512 ("### PART SHA512 ###"; its backupcfg splits on
# /^### PART.*###$/).
PART_RE = re.compile(r"### PART.*###")
# The passwords airOS 8 hands over as "-" in getcfg.cgi (its web strips users.1-3.password
# before saving): the only ones a backup made from what airos-tui reads lacks.
_MASKED_KEY = re.compile(r"^users\.\d+\.password$")


def backup_config_part(text: str) -> str | None:
    """The configuration (system.cfg) in a backup file: the last of its parts that is one
    (cfg.cgi puts others before it). None if there is none."""
    parts, cur = [], []
    for line in text.splitlines():
        if PART_RE.fullmatch(line):
            parts.append("\n".join(cur) + "\n")
            cur = []
        else:
            cur.append(line)
    parts.append("\n".join(cur) + "\n")
    return next((p for p in reversed(parts) if looks_like_cfg(p)), None)


def backup_kind(text: str) -> str | None:
    """"web": a backup as the device's web makes it (cfg.cgi, with the password hashes), to
    upload as it does; "airos-tui": one airos-tui saved from what it reads, where airOS 8
    shows the passwords as "-" (uploading it would put "-" as the password); None: not an
    airOS configuration."""
    part = backup_config_part(text)
    if part is None:
        return None
    cfg = Config(part)
    if any(_MASKED_KEY.match(k) and cfg.get(k) == "-" for k in cfg.keys()):
        return "airos-tui"
    return "web"


# -- password (airOS 8: the ChangePasswordView and its strength check) ------------------

PASSWORD_MAX = 63                 # maxlength of the web's password fields
_PWD_MIN, _PWD_GREAT = 12, 20     # MIN_LENGTH (and MIN_LENGTH_GOOD) and MIN_LENGTH_GREAT
_PWD_KNOWN = ("ubnt", "password")


def _js(text: str) -> str:
    """The text as JavaScript sees it: one character per UTF-16 code unit."""
    raw = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(int.from_bytes(raw[i:i + 2], "little")) for i in range(0, len(raw), 2))


def _js_len(text: str) -> int:
    """The length JavaScript counts (UTF-16 code units)."""
    return len(_js(text))


def _owasp_strong(text: str) -> tuple[bool, list[int]]:
    """owasp-password-strength-test as the web configures it (minLength 12, maxLength 128,
    passphrases from 12 characters, 3 optional tests): (strong, failed tests)."""
    text = _js(text)
    n = len(text)
    failed = [i for i, bad in enumerate((n < 12, n > 128, re.search(r"(.)\1{2,}", text)))
              if bad]
    strong = not failed
    # (password_problem asks it only from 12 characters on, as its web does: always a
    # passphrase then, and only the repeated-characters test can fail, there as well)
    if n < 12:                                 # not a passphrase: the optional tests count
        passed = 0
        for i, pattern in enumerate((r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"), 3):
            if re.search(pattern, text):
                passed += 1
            else:
                failed.append(i)
        strong = strong and passed >= 3
    return strong, failed


def password_problem(new: str, current: str = "", longest: int = PASSWORD_MAX) -> str | None:
    """What the airOS 8 web says of a new password, or None if it takes it: different from
    the current one, then its strength check (its field takes longest characters at most)."""
    if not new:
        return tr("Type the new password.")
    if _js_len(new) > longest:
        return tr("At most {max} characters.", max=longest)
    if current and new == current:
        return tr("Entered password is the same as current. Please choose a different one.")
    n = _js_len(new)
    if n >= _PWD_GREAT:
        return None
    if n < _PWD_MIN:
        return tr("Use at least 12 characters.")
    strong, failed = _owasp_strong(new)
    if 2 in failed:
        return tr("The password may not contain sequences of three or more repeated "
                  "characters.")
    if not strong:
        return tr("Please use a longer password or add characters from different groups "
                  "(mixed case letters, numbers, symbols).")
    if any(word in new.lower() for word in _PWD_KNOWN):
        return tr("Please avoid using known facts like 'ubnt', 'password', your username or "
                  "hostname.")
    return None


# -- the rest of its System page (airOS 8) -----------------------------------------------
#
# As its models read them: a missing key keeps the model's default and a yes/no is
# "enabled", "true" or "1". Which rows it shows depends on the board (board.info).

def _board_flag(board: dict, key: str) -> bool:
    return js_parse_int(board.get(key)) == 1


def airview_offered(board: dict) -> bool:
    """Its hasFeatureAirView: not on Gen3 boards, and only if a radio of the board is left
    out of the web (radio.N.web_exclude, the 2.4 GHz one of Gen2 boards)."""
    if gen3(board):
        return False
    radios = {int(m.group(1)) for k in board for m in [re.match(r"radio\.(\d+)\.", k)] if m}
    return any(board.get(f"radio.{n}.web_exclude") for n in range(1, max(radios, default=0) + 1))


# The switches of its Device section: key, label, its model's default, when it shows them.
DEVICE_SWITCHES = (
    ("system.external.reset", N_("External Reset"), True, "feature.ext_reset"),
    ("airview.status", N_("Airview"), True, "airview"),
    ("system.poe_passthrough", N_("POE Passthrough"), False, "feature.poe_passthrough"),
    ("system.autosend_crash_reports", N_("Auto-send Crash Reports"), False, None),
    ("system.leds.nightmode", N_("LEDs Night Mode"), False, None),
)
UI_LANGUAGE_KEY = "gui.language"
UI_LANGUAGE = "en_US"                       # its model's default
TDD_REPORTING_KEY = "radio.1.ff_cap_rep"
TDD_REPORTING = ((0, N_("Default")), (1, N_("DL/UL split based")))


def device_switches(board: dict) -> list[tuple[str, str, bool]]:
    """The switches its Device section shows on this board: (key, label, default)."""
    out = []
    for key, label, default, need in DEVICE_SWITCHES:
        if need == "airview":
            shown = airview_offered(board)
        else:
            shown = need is None or _board_flag(board, need)
        if shown:
            out.append((key, label, default))
    return out


def switch_on(cfg: Config, key: str) -> bool:
    default = next(d for k, _, d, _ in DEVICE_SWITCHES if k == key)
    return _model_bool(cfg, key, default)


def ui_language(cfg: Config) -> str:
    return cfg.get(UI_LANGUAGE_KEY) or UI_LANGUAGE


def language_name(code: str, languages: list[dict]) -> str:
    return next((x.get("lang") or code for x in languages if x.get("code") == code), code)


def tdd_reporting_offered(st: dict, board: dict) -> bool:
    """Its "Fixed TDD Framing Capacity Reporting" row: an access point, not a Gen3 board."""
    return not gen3(board) and bool(st.get("ap"))


def tdd_reporting_editable(st: dict) -> bool:
    """It can only be changed in PtMP with a frame other than "Flexible (legacy)"."""
    return bool(st.get("ap")) and not st.get("ptp") and bool(st.get("tdd_duration"))


def tdd_reporting(cfg: Config) -> int:
    n = js_parse_int(cfg.get(TDD_REPORTING_KEY)) if cfg.get(TDD_REPORTING_KEY) else None
    return 0 if n is None else n


# -- Management Radio (Gen2 and Gen3 boards) --

PROVMODE_KEY = "system.airosx.prov.status"
PROVMODE_DISABLED = -1                  # its Provmodes' DISABLED


def management_radio_offered(board: dict) -> bool:
    return _board_flag(board, "feature.g2") or gen3(board)


def provmode_startup(cfg: Config) -> bool:
    """"Management Radio on startup"."""
    return _model_bool(cfg, PROVMODE_KEY, True)


def provmode_state(status: dict) -> tuple[int, int | None]:
    """(its provmode.status, seconds left) from status.cgi; DISABLED when it has none."""
    pm = status.get("provmode") if isinstance(status.get("provmode"), dict) else {}
    st = _int(pm.get("status"))
    return (PROVMODE_DISABLED if st is None else st), _int(pm.get("timeout"))


def management_radio_up(status: dict) -> bool:
    """Its isManagementRadioUp: anything but DISABLED (its button then says "Disable")."""
    return provmode_state(status)[0] != PROVMODE_DISABLED


# -- System Accounts --

USERNAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
USERNAME_MAX = 64
ADMIN_USER = "ubnt"                         # its model's default


def accounts(cfg: Config) -> dict:
    """The administrator's name and the read-only account (on, its name, and whether it has
    a password: its roPassPresent)."""
    return {"admin": cfg.get("users.1.name") or ADMIN_USER,
            "ro": _model_bool(cfg, "users.2.status", False),
            "ro_name": cfg.get("users.2.name") or "",
            "ro_password": cfg.get("users.2.password") is not None}


# -- airOS 6.3's first login: its password check (pstrength.js) -------------------------------

V6_USERNAME_MAX = 31              # maxlength of its login page's user name
_JS_DOT = "[^\n\r\u2028\u2029]"   # JavaScript's "." (no line terminators)
_PSTRENGTH_SYMBOL = "[!,@#$%^&*?_~]"   # its [!,@,#,$,%,^,&,*,?,_,~]


def _repetition(n: int, text: str) -> str:
    """pstrength.js's checkRepetition: text without its runs of an n-character piece."""
    out, i = [], 0
    while i < len(text):
        j, repeated = 0, True
        while j < n and j + i + n < len(text):
            repeated = repeated and text[j + i] == text[j + i + n]
            j += 1
        if j < n:
            repeated = False
        if repeated:
            i += n - 1
        else:
            out.append(text[i])
        i += 1
    return "".join(out)


def v6_password_verdict(password: str, username: str) -> str:
    """pstrength.js's calculateScore, as its login page uses it: "short", "weak", "normal" or
    "strong" (it lets the new password through when "normal" or "strong"). As JavaScript runs
    it: on UTF-16 code units; its "." takes no line terminator, so a password with one (\\n,
    \\r, U+2028, U+2029) is weak; its toLowerCase works on whole characters (also those beyond
    U+FFFF). Compared with the original under node."""
    a, dot, sym = _js(password), _JS_DOT, _PSTRENGTH_SYMBOL
    lower_a, lower_b = _js(password.lower()), _js(username.lower())
    if len(a) < 12:
        return "short"
    if not re.fullmatch(f"(?={dot}*[A-Z])(?={dot}*[a-z])(?={dot}*[0-9])(?={dot}*[^A-Za-z0-9])"
                        f"{dot}*", a):
        return "weak"
    if lower_a == lower_b:
        return "weak"
    score = 0
    if lower_b in lower_a:
        score -= 10
    if lower_a in lower_b:
        score -= 10
    score += len(a) * 4
    for n in (1, 2, 3, 4):
        score += len(_repetition(n, a)) - len(a)
    if re.search(f"{dot}*[0-9]{dot}*[0-9]{dot}*[0-9]", a):
        score += 5
    if re.search(f"{dot}*{sym}{dot}*{sym}", a):
        score += 5
    if re.search(f"[a-z]{dot}*[A-Z]|[A-Z]{dot}*[a-z]", a):
        score += 10
    if re.search(r"[a-zA-Z]", a) and re.search(r"[0-9]", a):
        score += 15
    if re.search(sym, a) and re.search(r"[0-9]", a):
        score += 15
    if re.search(sym, a) and re.search(r"[a-zA-Z]", a):
        score += 15
    if re.fullmatch(r"[A-Za-z0-9_]+", a) or re.fullmatch(r"[0-9]+", a):
        score -= 12
    score = max(0, min(100, score))
    return "weak" if score < 34 else "normal" if score < 68 else "strong"


def v6_password_problem(new: str, username: str) -> str | None:
    """What airOS 6.3's login page says of a new password (its button stays off until the
    meter says "Normal" or "Strong"; its server, not ubnt), or None if it takes it."""
    if _js_len(new) > PASSWORD_MAX:
        return tr("At most {max} characters.", max=PASSWORD_MAX)
    verdict = v6_password_verdict(new, username)
    if verdict == "short":
        return tr("Too short: its web asks for at least 12 characters.")
    if verdict == "weak" or new == "ubnt":
        return tr("Weak: its web asks for upper and lower case letters, numbers and symbols, "
                  "not the user name, and not too repetitive.")
    return None


def v6_username_problem(name: str) -> str | None:
    """Its login page's user name: not empty (its server; its page, trimmed) and 31 characters
    at most (its field)."""
    if not name.strip():
        return tr("Username is required")
    if _js_len(name) > V6_USERNAME_MAX:
        return tr("At most {max} characters.", max=V6_USERNAME_MAX)
    return None


SETUP_FIELD_MAX = 64              # maxlength of its first setup's user and password fields


def setup_username_problem(name: str) -> str | None:
    """The username of its first setup (LoginModelDefault): required, and its pattern."""
    if not name:
        return tr("Username is required")
    if _js_len(name) > SETUP_FIELD_MAX:
        return tr("At most {max} characters.", max=SETUP_FIELD_MAX)
    return None if USERNAME_RE.fullmatch(name) else tr("Invalid username.")


def _username_problem(name: str, required: str, length: str) -> str | None:
    if not name.strip():
        return tr(required)
    if not 1 <= _js_len(name) <= USERNAME_MAX:
        return tr(length, low=1, high=USERNAME_MAX)
    return None if USERNAME_RE.fullmatch(name) else tr("Invalid username.")


def admin_name_problem(name: str, acc: dict) -> str | None:
    """Its rules for the administrator's name (and, with the read-only account on, that both
    names differ, which its web says on the read-only one)."""
    msg = _username_problem(name, N_("Administrator Username is required"),
                            N_("Administrator Username must be between {low} and {high} "
                               "characters"))
    if msg is None and acc["ro"] and name == acc["ro_name"]:
        return tr("Read-Only and Administrator Account usernames should differ.")
    return msg


def ro_name_problem(name: str, acc: dict) -> str | None:
    """Its rules for the read-only account's name, while it is on (its label for it is the
    field's name: "Read only user name")."""
    if not name.strip():
        return tr("Read only user name is required")
    if not 1 <= _js_len(name) <= USERNAME_MAX:
        return tr("Read only user name must be between {low} and {high} characters", low=1,
                  high=USERNAME_MAX)
    if name == acc["admin"]:
        return tr("Read-Only and Administrator Account usernames should differ.")
    if not acc["ro_password"]:
        return tr("Read-Only Account Password required.")
    return None if USERNAME_RE.fullmatch(name) else tr("Invalid username.")


def plan_accounts(acc: dict) -> dict[str, str | None]:
    """What its model writes: both names, the read-only account's state and, always, its
    shell and ids."""
    return {"users.1.name": acc["admin"], "users.2.name": acc["ro_name"] or None,
            "users.2.shell": "/bin/false", "users.2.gid": "100", "users.2.uid": "100",
            "users.2.status": "enabled" if acc["ro"] else "disabled"}


def ro_password_problem(new: str) -> str | None:
    """Its dialog for the read-only password has no current password, so its checks (which
    need one) do not run: any password; here, not an empty one."""
    if not new:
        return tr("Type the new password.")
    if _js_len(new) > PASSWORD_MAX:
        return tr("At most {max} characters.", max=PASSWORD_MAX)
    return None


# -- Location (boards without GPS) --

LOCATION_FIELDS = (("lat", "system.latitude", N_("Latitude"), -90, 90),
                   ("lon", "system.longitude", N_("Longitude"), -180, 180),
                   ("height", "system.height", N_("Device Height"), 0, 10000))
_LOCATION_MSG = {"lat": N_("Latitude must be between {low} and {high}"),
                 "lon": N_("Longitude must be between {low} and {high}"),
                 "height": N_("Device Height must be between {low} and {high}")}
_NUMBER_RE = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")   # its isNumber (without "1,000", which it
FEET_PER_METER = 3.2808399                          # takes and then cannot compare)


def location_offered(board: dict) -> bool:
    return not _board_flag(board, "feature.gps")


def location(cfg: Config) -> dict:
    return {f: cfg.get(key) or "" for f, key, _, _, _ in LOCATION_FIELDS}


def location_problem(field: str, value) -> str | None:
    """Its range for a field (meters for the height); empty is fine: they are optional."""
    text = str(value).strip()
    if not text:
        return None
    low, high = next((lo, hi) for f, _, _, lo, hi in LOCATION_FIELDS if f == field)
    if _NUMBER_RE.fullmatch(text) and low <= float(text) <= high:
        return None
    return tr(_LOCATION_MSG[field], low=low, high=high)


def plan_location(loc: dict) -> dict[str, str | None]:
    return {key: loc[f].strip() or None for f, key, _, _, _ in LOCATION_FIELDS}


def imperial(country: int | None) -> bool:
    """Its web shows the height in feet in the United States and the United Kingdom."""
    return country in (840, 826)


def height_shown(meters: str, feet: bool) -> str:
    """As its field shows it: meters, or whole feet."""
    if not meters or not feet or not _NUMBER_RE.fullmatch(meters.strip()):
        return meters
    return str(math.floor(float(meters) * FEET_PER_METER + 0.5))


def height_saved(text: str, feet: bool) -> str:
    """What it keeps of what is typed: meters (from feet, with one decimal)."""
    text = text.strip()
    if not text or not feet or not _NUMBER_RE.fullmatch(text):
        return text
    return f"{float(text) / FEET_PER_METER:.1f}"
