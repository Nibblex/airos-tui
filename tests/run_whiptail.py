"""The TUI with whiptail instead of dialog, as if dialog were not installed:
python3 tests/run_whiptail.py [airos_tui's arguments]."""
import shutil
import sys

from _paths import ROOT

sys.path.insert(0, ROOT)
from airos_tui.cli import main  # noqa: E402

_which = shutil.which
shutil.which = lambda n, *a, **k: None if n == "dialog" else _which(n, *a, **k)
sys.exit(main(sys.argv[1:]))
