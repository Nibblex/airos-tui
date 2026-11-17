#!/usr/bin/env python3
"""Run airos-tui's tests and sum up the result.

usage: python3 tests/run.py [--e2e] [--coverage] [-j N] [SUITE ...]   (for example: run.py
test_wireless)

It runs several suites at a time (-j N: up to N; by default, as many as the computer has
cores; -j 1, one after another), starting with those that took longest last time (it keeps
that in AIROS_TEST_WORK/durations.json); the QEMU ones (the emu* ones), two at a time
(AIROS_TEST_HEAVY). With --e2e, at the end and by themselves (their screen timings do not get
along with the load of the others), the scenarios of e2e/e2e.py, also at the same time: each
group (e2e/e2e.py --list) with its own folder (AIROS_TEST_WORK/e2e/GROUP, with its screens) and tmux
server; their result, in one line. Their window is 110×34, or the size in AIROS_E2E_SIZE (such
as 91x24, a uConsole's console in tmux). Each suite writes its output to
AIROS_TEST_WORK/logs/SUITE.log (each e2e group, to e2e-GROUP.log), and its line comes out when
it ends. Each suite (and each scenario) uses its own ports (127.0.0.x): one shared by two fails
when they run at the same time.

They all use the simulator on 127.0.0.x, with no traffic to your networks, and leave their
temporary files in /tmp/airos-tui-tests (or in AIROS_TEST_WORK). test_neighbors_netns needs
unshare (an isolated network namespace, without privileges), and e2e needs tmux and dialog.
test_emu6 and test_emu6_pages test against the web of a real airOS 6, emulated
(emu/emu6.py), and test_emu8 against the API of a real airOS 8 (emu/emu8.py, which also
compares the simulator with it): they need qemu-user and qemu-user-binfmt, unsquashfs and the
firmware, downloaded once with python3 tests/emu/emu6.py fetch (or emu8.py fetch); test_emu8
needs gcc too (it links statically). If something is missing, they are skipped.

The suites run the program in Spanish (the texts they expect come from tr(), so they also check
the Spanish catalog); test_i18n and an e2e scenario, in English.

--coverage measures which lines of the program and of the simulator get to run (with
sys.monitoring: Python 3.12 or newer, nothing to install) and sums it up at the end: the
program, the simulator, each module of airos_tui/ and of airos_simulator/ and the functions
with the least coverage. A line that runs does not mean its result is checked.
"""
from __future__ import annotations
import ast
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types

from _common import HERE, ROOT, SIM_SOURCES, SOURCES, WORK

SUITES = ["test_client", "test_app", "test_actions", "test_limits", "test_network",
          "test_pppoe", "test_netmode", "test_discovery", "test_i18n", "test_services",
          "test_neighbors", "test_neighbors_netns", "test_wireless", "test_tools",
          "test_firmware", "test_stations", "test_router", "test_access", "test_system",
          "test_graphs", "test_advnet", "test_airview", "test_ui", "test_emu6",
          "test_emu6_pages", "test_emu8"]
COVERED = SOURCES + SIM_SOURCES            # the modules of airos_tui/ and the simulator
HEAVY = {"test_emu6", "test_emu6_pages", "test_emu8"}   # (QEMU: MAX_HEAVY at a time)
MAX_HEAVY = int(os.environ.get("AIROS_TEST_HEAVY", "2"))
DURATIONS = os.path.join(WORK, "durations.json")
LOGS = os.path.join(WORK, "logs")
TIMEOUT = 1800


def _jobs(argv) -> tuple[int, list[str]]:
    """-j N (or -jN) out of argv: (N, the rest)."""
    jobs, rest, it = os.cpu_count() or 1, [], iter(argv)
    for a in it:
        if a == "-j":
            jobs = int(next(it, "1"))
        elif a.startswith("-j") and a[2:].isdigit():
            jobs = int(a[2:])
        else:
            rest.append(a)
    return max(1, jobs), rest


E2E = os.path.join(HERE, "e2e", "e2e.py")


def suite_jobs(names, env) -> list[tuple]:
    """A job per suite: (its name, what runs it, its environment)."""
    return [(name, [sys.executable, os.path.join(HERE, f"{name}.py")], env) for name in names]


def e2e_jobs(env) -> list[tuple]:
    """A job per group of e2e scenarios (e2e.py --list: those that can run at the same time),
    each in its own folder (AIROS_TEST_WORK/e2e/GROUP, with its screens) and with its own tmux
    server (airos-tui-e2e-GROUP)."""
    out = subprocess.run([sys.executable, E2E, "--list"], capture_output=True, text=True,
                         check=True, env=env).stdout
    jobs = []
    for group in (line.split() for line in out.splitlines() if line.strip()):
        label = "+".join(group)
        work = os.path.join(WORK, "e2e", label)
        shutil.rmtree(work, ignore_errors=True)
        jobs.append((f"e2e-{label}", [sys.executable, E2E, *group],
                     dict(env, AIROS_TEST_WORK=work, AIROS_E2E_TMUX=f"airos-tui-e2e-{label}")))
    return jobs


def _start(name, cmd, env):
    """A job running, its output to its log."""
    os.makedirs(LOGS, exist_ok=True)
    log = open(os.path.join(LOGS, f"{name}.log"), "w")
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    return proc, log, time.monotonic()


def run_all(todo, jobs):
    """Run the jobs (name, command, environment), jobs at a time (the longest last time
    first; HEAVY ones MAX_HEAVY at a time), and yield (name, output, exit code, seconds) as
    each ends."""
    try:
        last = json.load(open(DURATIONS))
    except (OSError, ValueError):
        last = {}
    pending = sorted(todo, key=lambda job: -last.get(job[0], 1e9))   # (new ones, first)
    running = {}
    while pending or running:
        while len(running) < jobs:
            busy = sum(n in HEAVY for n in running) >= MAX_HEAVY
            job = next((j for j in pending if not (busy and j[0] in HEAVY)), None)
            if job is None:
                break
            pending.remove(job)
            running[job[0]] = _start(*job)
        time.sleep(0.2)
        for name, (proc, log, t0) in list(running.items()):
            code = proc.poll()
            if code is None and time.monotonic() - t0 > TIMEOUT:
                proc.kill()
                code = proc.wait()
            if code is None:
                continue
            log.close()
            del running[name]
            out = open(os.path.join(LOGS, f"{name}.log"), errors="replace").read()
            yield name, out, code, time.monotonic() - t0


def main(argv) -> int:
    jobs, argv = _jobs(argv)
    names = [a[:-3] if a.endswith(".py") else a for a in argv
             if not a.startswith("--")] or list(SUITES)
    env = dict(os.environ)
    data = None
    if "--coverage" in argv:
        if not hasattr(sys, "monitoring"):
            print("--coverage needs Python 3.12 or newer: running without measuring.\n")
        else:
            data = os.path.join(WORK, "coverage")
            shutil.rmtree(data, ignore_errors=True)
            os.makedirs(data)
            env["PYTHONPATH"] = os.pathsep.join(
                [os.path.join(HERE, "_cov")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                                                else []))
            env["AIROS_COV_DIR"] = data
            env["AIROS_COV_FILES"] = os.pathsep.join(COVERED)
    total_ok = total_bad = 0
    failed, took = [], {}
    t_all = time.monotonic()
    # (the e2e ones at the end, by themselves: under the load of the suites, their screens
    # take their time; in one line, all of them)
    rounds = [suite_jobs(names, env), e2e_jobs(env) if "--e2e" in argv else []]
    for todo in rounds:
        t_round, e2e = time.monotonic(), [0, 0, 0]          # (e2e: OK, FAIL, groups)
        for name, out, code, secs in run_all(todo, jobs):
            took[name] = round(secs)
            ok = len(re.findall(r"^\s+OK ", out, re.M))
            bad = re.findall(r"^\s+FAIL.*$", out, re.M)
            total_ok, total_bad = total_ok + ok, total_bad + len(bad)
            if name.startswith("e2e-"):
                e2e[0], e2e[1], e2e[2] = e2e[0] + ok, e2e[1] + len(bad), e2e[2] + 1
                if bad or code != 0:
                    print(f"{name:<22} {ok:4d} OK {len(bad):3d} FAIL  ({secs:.0f} s)"
                          + ("" if code == 0 else f"  exit code {code}"), flush=True)
            else:
                print(f"{name:<22} {ok:4d} OK {len(bad):3d} FAIL  ({secs:.0f} s)"
                      + ("" if code == 0 else f"  exit code {code}"), flush=True)
            for line in bad[:10]:
                print("    " + line.strip()[:200])
            if code != 0:
                failed.append(name)
                if not bad:
                    print("    " + out[-1500:].replace("\n", "\n    "))
                if "Address already in use" in out and jobs > 1:
                    print("    (a port another suite uses at the same time? -j 1 rules it out)")
        if e2e[2]:
            print(f"{'e2e':<22} {e2e[0]:4d} OK {e2e[1]:3d} FAIL  ({time.monotonic() - t_round:.0f}"
                  f" s; {e2e[2]} groups of scenarios)", flush=True)
    try:
        last = json.load(open(DURATIONS))
    except (OSError, ValueError):
        last = {}
    with open(DURATIONS, "w") as f:
        json.dump(dict(last, **took), f)
    print(f"\ntotal: {total_ok} OK, {total_bad} FAIL"
          + (f"; failed: {', '.join(failed)}" if failed else "")
          + f"  (in {time.monotonic() - t_all:.0f} s, up to {jobs} at a time)")
    if data:
        report(data)
    return 1 if failed else 0


def _executable(code: types.CodeType) -> set[int]:
    """The lines with bytecode (the ones that can run)."""
    out = {line for _, _, line in code.co_lines() if line}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= _executable(const)
    return out


def _functions(tree) -> dict[int, str]:
    """Line -> the (innermost) function it belongs to."""
    owner: dict[int, str] = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                for n in range(child.lineno, child.end_lineno + 1):
                    owner[n] = name
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)
    walk(tree, "")
    return owner


def _label(path) -> str:
    """A module, as it is named from the project (airos_tui/device/network…)."""
    return os.path.relpath(path, ROOT)[:-3]


def report(data: str, worst: int = 15) -> None:
    hits: dict[str, set] = {}
    for path in glob.glob(os.path.join(data, "*.json")):
        try:
            for f, lines in json.load(open(path)).items():
                hits.setdefault(f, set()).update(lines)
        except (OSError, ValueError):
            continue
    seen = {}                                  # path -> (source, executable lines, run)
    for path in COVERED:
        src = open(path, encoding="utf-8").read()
        exe = _executable(compile(src, path, "exec"))
        seen[path] = (src, exe, hits.get(os.path.realpath(path), set()) & exe)

    def pct(paths):
        e = sum(len(seen[p][1]) for p in paths)
        g = sum(len(seen[p][2]) for p in paths)
        return f"{100 * g / e:5.1f} %  ({g} of {e})" if e else "  -"
    print("\ncoverage (lines that get to run):")
    print(f"  {'airos_tui (the program)':<26} {pct(SOURCES)}")
    print(f"  {'airos_simulator':<26} {pct(SIM_SOURCES)}")
    print("  per module:")
    for path in SOURCES + SIM_SOURCES:
        if seen[path][1]:
            print(f"    {_label(path):<36} {pct([path])}")
    per: dict[str, list] = {}
    for path in SOURCES + SIM_SOURCES:
        src, exe, got = seen[path]
        owner = _functions(ast.parse(src))
        module = _label(path)
        for n in exe:
            if n in owner:
                count = per.setdefault(f"{module}: {owner[n]}", [0, 0])
                count[0] += 1
                count[1] += n in got
    never = [name for name, (e, g) in per.items() if not g]
    print(f"  functions: {len(per)}; never run: {len(never)}"
          + (f" ({', '.join(sorted(never)[:10])})" if never else ""))
    missing = sorted(((e - g, name, g / e) for name, (e, g) in per.items() if g < e),
                     reverse=True)[:worst]
    print(f"  the {len(missing)} with the most lines not run:")
    for miss, name, ratio in missing:
        print(f"    {miss:4d} not run ({100 * ratio:3.0f} %)  {name}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
