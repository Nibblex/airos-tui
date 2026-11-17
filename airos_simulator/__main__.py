"""python3 -m airos_simulator: the same as airos-tui-simulator (the tests run this file)."""
import os
import sys

if __package__:                                    # python3 -m airos_simulator
    from .cli import main
else:                                              # python3 airos_simulator/__main__.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from airos_simulator.cli import main

sys.exit(main())
