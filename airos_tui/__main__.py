"""python3 -m airos_tui, or python3 path/to/airos_tui (the folder), from anywhere."""
import os
import sys

if not __package__:                  # run as a folder: make the package importable
    sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from airos_tui.cli import main  # noqa: E402

sys.exit(main())
