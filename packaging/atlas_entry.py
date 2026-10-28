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

    return main(sys.argv[1:] or _double_click())


def _double_click() -> list[str]:
    """What opening the app with no arguments means.

    The downloadable app, on a Mac and on Windows alike, is the API build: Claude or Gemini
    (whichever is chosen in Settings > AI providers) when a key is saved, rule-based bots
    until then. A player who downloads it has no subscription CLIs installed, so it never
    starts in that mode; from a checkout, `atlas start` still does.
    """
    from atlas.providers.api_choice import chosen, key_for

    return ["start", "--api" if key_for(chosen()) else "--no-ai"]


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_start())
