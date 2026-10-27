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

    On a Mac the app is the Claude API build: Claude when a key is saved (Settings > AI
    providers), rule-based bots until then. Elsewhere it is the subscription-CLI game it
    always was.
    """
    if sys.platform != "darwin":
        return ["start"]
    from atlas.providers.api_provider import api_key

    return ["start", "--api" if api_key() else "--no-ai"]


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_start())
