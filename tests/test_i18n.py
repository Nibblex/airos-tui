"""Tests of the translation: a complete catalog with nothing left over, matching placeholders,
the choice of language, argparse's help translated, and the texts in English."""
import argparse
import ast
import contextlib
import inspect
import io
import json
import os
import shutil
import string
import sys
import tempfile

from _common import check, fails, SIM_SOURCES, SOURCES, suite, WORK  # noqa: E402
T, S, XDG = suite("i18n", lang="en")      # the language choice is what this suite tests


def fields(text):
    return {f.split("!")[0].split(".")[0].split("[")[0]
            for _, f, _, _ in string.Formatter().parse(text) if f is not None}


def msgids(path):
    """The literals of tr()/N_(), and the calls to tr() with values whose names do not match
    the placeholders."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    ids, bad = {}, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("tr", "N_") and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            continue
        msg = node.args[0].value
        ids.setdefault(msg, node.lineno)
        kws = {k.arg for k in node.keywords}
        if node.func.id == "tr" and (kws or fields(msg)) and kws != fields(msg):
            bad.append((node.lineno, msg[:50], sorted(kws), sorted(fields(msg))))
    return ids, bad


print("=== the es.json catalog against the code ===")
catalog = json.load(open(os.path.join(T.LOCALE_DIR, "es.json"), encoding="utf-8"))
tui_ids, tui_bad = {}, []
for path in SOURCES:                       # every module of the package
    ids, bad = msgids(path)
    for msg, line in ids.items():
        tui_ids.setdefault(msg, line)
    tui_bad += [(os.path.basename(path),) + b for b in bad]
sim_ids, sim_bad = {}, []
for path in SIM_SOURCES:                   # every module of the simulator
    ids, bad = msgids(path)
    for msg, line in ids.items():
        sim_ids.setdefault(msg, line)
    sim_bad += [(os.path.basename(path),) + b for b in bad]
used = set(tui_ids) | set(sim_ids)
missing = sorted(m for m in used if m not in catalog)
unused = sorted(m for m in catalog if m not in used)
check(f"every message has a translation ({len(used)})", not missing, str(missing[:5]))
check("the catalog has no leftover entries", not unused, str(unused[:5]))
check("tr(): the values match the placeholders", not tui_bad and not sim_bad,
      str((tui_bad + sim_bad)[:3]))
diff_ph = [m for m in used & set(catalog) if fields(m) != fields(catalog[m])]
check("the same placeholders in English and in Spanish", not diff_ph, str(diff_ph[:3]))
empty = [m for m, v in catalog.items() if not v.strip()]
check("no empty translation", not empty, str(empty[:3]))
check("argparse's messages exist in this Python",
      all(m in inspect.getsource(argparse) for m in T.ARGPARSE_MESSAGES),
      str([m for m in T.ARGPARSE_MESSAGES if m not in inspect.getsource(argparse)]))

print("=== choice of language ===")
saved = {k: os.environ.pop(k, None) for k in ("AIROS_LANG", "LC_ALL", "LC_MESSAGES", "LANG")}
try:
    check("no variables: English", T.detect_language() == "en")
    os.environ["LANG"] = "es_MX.UTF-8"
    check("LANG=es_MX.UTF-8: Spanish", T.detect_language() == "es")
    os.environ["LC_MESSAGES"] = "en_GB.UTF-8"
    check("LC_MESSAGES wins over LANG", T.detect_language() == "en")
    os.environ["LC_ALL"] = "es_ES"
    check("LC_ALL wins over LC_MESSAGES", T.detect_language() == "es")
    os.environ["AIROS_LANG"] = "en"
    check("AIROS_LANG wins over LC_ALL", T.detect_language() == "en")
    check("--lang wins over everything", T.detect_language("es") == "es")
    os.environ["AIROS_LANG"] = "fr"
    check("a language without a translation: English", T.detect_language() == "en")
    check("C/POSIX: English", T.detect_language("C") == "en")
    check("the simulator chooses the same way", S.detect_language("es") == "es"
          and S.detect_language() == "en")
finally:
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v

check("set_language('es') loads the catalog", T.set_language("es") == "es"
      and T.current_language() == "es"
      and T.tr("Exit") == "Salir")         # (the Spanish catalog's text, on purpose)
check("set_language('xx') stays in English", T.set_language("xx") == "en"
      and T.tr("Exit") == "Exit")
real_dir = T.LOCALE_DIR
T.LOCALE_DIR = tempfile.mkdtemp(prefix="no-locales-", dir=WORK)
try:
    check("without the catalog: English", T.set_language("es") == "en" and T.tr("Exit") == "Exit")
finally:
    shutil.rmtree(T.LOCALE_DIR)
    T.LOCALE_DIR = real_dir

print("=== command-line help and errors ===")


def run_main(argv, mod=T):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = mod.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


os.environ["AIROS_LANG"] = "en"
code, out, _ = run_main(["--help"])
check("--help in English", code == 0 and out.startswith("usage: ") and "--discover" in out
      and "show this help message" in out and "DEVICE" in out, out[:200])
# (The Spanish texts in the checks below are the Spanish catalog's, on purpose: what they check
# is the translation itself.)
code, out, _ = run_main(["--lang", "es", "--help"])
check("--lang es --help", code == 0 and out.startswith("uso: ") and "muestra esta ayuda" in out
      and "EQUIPO" in out and "argumentos posicionales" in out and "opciones:" in out, out[:300])
code, out, _ = run_main(["--lang=es", "--help"])
check("--lang=es too", out.startswith("uso: "))
code, _, err = run_main(["--lang", "es", "--timeout"])
check("an argparse error in Spanish", code == 2 and "falta un valor" in err, err)
code, _, err = run_main(["--lang", "es", "--nothing"])
check("an unknown argument in Spanish", code == 2 and "argumentos no reconocidos" in err, err)
code, _, err = run_main(["--nothing"])
check("and in English", code == 2 and "unrecognized arguments" in err, err)
os.environ["AIROS_LANG"] = "es"
code, out, _ = run_main(["--help"])
check("AIROS_LANG=es without --lang", out.startswith("uso: "))
code, out, _ = run_main(["--lang", "es", "--help"], S)
check("simulator: --help in Spanish", code == 0 and out.startswith("uso: ")
      and "--mgmt-ip" in out and "PUERTO" in out, out[:200])
code, out, _ = run_main(["--lang", "en", "--help"], S)
check("simulator: --help in English", code == 0 and out.startswith("usage: ")
      and "--test-time" in out, out[:200])
code, _, err = run_main(["--lang", "es", "--mgmt-ip", "10.0.0.1"], S)
check("simulator: its own error translated", code == 2 and "debe estar en 127.0.0.0/8" in err,
      err)

print("=== texts in English ===")
T.set_language("en")
cfg = T.Config(S.cfg_text(S.Device(8, "ubnt", "ubnt", 1, 60, mode="router").running))
wan = T.main_iface(cfg, "v8")
check("describe_ip", T.describe_ip(cfg, wan) == "DHCP (fallback 192.168.10.1/24)",
      T.describe_ip(cfg, wan))
bad = cfg.copy()
bad.set("dhcpd.1.start", "192.168.1.250")
bad.set("dhcpd.1.end", "192.168.1.20")
probs = T.net_problems(bad, "v8")
check("net_problems in English", [(p.level, p.msg) for p in probs] == [
    ("error", "The DHCP range starts (192.168.1.250) after it ends (192.168.1.20).")], str(probs))
check("Problem.level in English", {p.level for p in T.check_config(
    T.Config(S.cfg_text(dict(S.Device(8, "u", "p", 1, 60).running, **{
        "radio.1.txpower": "99", "sshd.port": "61780"}))), None)} <= {"error", "warning"})
check("PPPoE not connected", T.pppoe_line({"error": "Not connected"}).startswith(
    "⚠ PPPoE: NOT CONNECTED"))
check("format_pppoe", T.format_pppoe(dict(S.PPPOE_SESSION, duration="00:01:02")).splitlines()[0]
      == "Status              connected")
check("labels in constants (N_)", T.netmode_label("soho") == "SOHO router"
      and T.wireless_mode_label("sta-ptp") == "Station PtP"
      and T.tr(T.SIDE_TEXT[T.iface_side(cfg, "ath0")]) == "the wireless link")
check("an unknown value, as it is", T.netmode_label("weird") == "weird"
      and T.wireless_mode_label(None) is None)
bridged = T.Config(S.cfg_text(S.Device(8, "u", "p", 1, 60).running))   # br0 = eth0 + ath0
check("the interface's side: wired/wireless/both",
      (T.iface_side(cfg, "eth0"), T.iface_side(cfg, "ath0"), T.iface_side(cfg, "br0"),
       T.iface_side(bridged, "br0")) == ("wired", "wireless", "wireless", "both"))
check("without a translation, the message comes out as it is",
      T.tr("Unknown {x}", x=1) == "Unknown 1")
T.set_language("es")
# (the Spanish catalog's texts, on purpose: this checks the translation)
check("and in Spanish, the same, translated", T.netmode_label("bridge") == "Puente (bridge)"
      and T.tr(T.SIDE_TEXT["both"]) == "el cable y el inalámbrico")
check("the backup folder in English", os.path.join(XDG, "airos-tui", "backups") == T.BACKUP_DIR)

print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
