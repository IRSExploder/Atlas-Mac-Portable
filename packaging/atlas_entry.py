"""The frozen build's entry point: `ATLAS.exe` and `atlas-cli.exe` both start here.

With no arguments it opens the game, which is what a double-click means. With arguments it
is the ordinary `atlas` command line, so `atlas-cli.exe selftest` and `ATLAS.exe play ...`
behave exactly as they do from a checkout.
"""

from __future__ import annotations

import multiprocessing
import sys


def _start() -> int:
    # The crash handler goes in before anything else is imported, so a frozen build that
    # dies on import -- a missing module, a missing DLL -- still leaves a report behind
    # instead of a window that never appears.
    from atlas.runtime.crash import install

    install()
    from atlas.cli import main

    return main(sys.argv[1:] or ["start"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_start())
