"""Line coverage for run.py --coverage, with sys.monitoring (Python 3.12 or newer).

Python loads it by itself at startup when this folder is in PYTHONPATH (run.py puts it there),
in every process of the tests: the suites, the simulator and the TUI that e2e launches. It
only acts if AIROS_COV_DIR is set, and saves the lines seen of the files in AIROS_COV_FILES.
"""
import atexit
import json
import os
import signal
import sys

_out = os.environ.get("AIROS_COV_DIR")
if _out and hasattr(sys, "monitoring"):
    _targets = {os.path.realpath(p) for p in os.environ.get("AIROS_COV_FILES", "").split(os.pathsep)
                if p}
    _hits: dict = {}
    _real: dict = {}
    _mon = sys.monitoring
    _tool = _mon.COVERAGE_ID

    def _on_line(code, line):
        f = code.co_filename
        r = _real.get(f)
        if r is None:
            r = _real[f] = os.path.realpath(f)
        if r in _targets:
            _hits.setdefault(r, set()).add(line)
        return _mon.DISABLE

    _mon.use_tool_id(_tool, "airos-cov")
    _mon.register_callback(_tool, _mon.events.LINE, _on_line)
    _mon.set_events(_tool, _mon.events.LINE)

    def _dump():
        try:
            with open(os.path.join(_out, f"{os.getpid()}.json"), "w") as fh:
                json.dump({k: sorted(v) for k, v in _hits.items()}, fh)
        except OSError:
            pass

    atexit.register(_dump)

    # The tests end the simulator with SIGTERM, which it does not handle: without this, what it
    # ran would be lost. Only there: in the TUI it would change what is measured (it sets its
    # own handler only if SIGTERM is at its default, and with it stops dialog and removes its
    # file; it exits through SystemExit, so atexit already saves what it ran).
    _argv0 = os.path.abspath(sys.argv[0])
    if (os.path.basename(_argv0) == "__main__.py"
            and os.path.basename(os.path.dirname(_argv0)) == "airos_simulator"):
        def _term(signum, frame):
            _dump()
            os._exit(128 + signum)

        signal.signal(signal.SIGTERM, _term)
