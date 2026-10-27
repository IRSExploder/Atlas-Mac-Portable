"""Why a world could not be opened, created, copied or rewound -- in a player's words.

One place for the sentence, so the main menu, the setup screen, the game window's export,
fork and rewind, and every `atlas` subcommand say the same thing about the same failure.
Before this the four errors a save can raise on open -- :class:`NewerWorldError`,
:class:`SyncedPathError`, :class:`MigrationRefused` and a damaged file -- were caught
nowhere, and a player hitting one saw a traceback.
"""

from __future__ import annotations

import sqlite3

__all__ = ["WORLD_ERRORS", "describe"]


def _classes() -> tuple[type[BaseException], ...]:
    from .db import SyncedPathError
    from .migrations import MigrationRefused, NewerWorldError

    return (NewerWorldError, SyncedPathError, MigrationRefused, sqlite3.DatabaseError)


#: The failures a world file can produce that are the file's fault, not the program's.
#: Deliberately not ``FileNotFoundError``: a missing CLI binary raises that too, and
#: telling a player their save had vanished would be the wrong sentence.
WORLD_ERRORS: tuple[type[BaseException], ...] = _classes()


def describe(exc: BaseException, *, what: str = "This save") -> str:
    """One or two sentences saying what went wrong and what to do about it."""
    from .db import SyncedPathError
    from .migrations import MigrationRefused, NewerWorldError

    if isinstance(exc, NewerWorldError):
        return (f"{what} was made by a newer version of ATLAS and cannot be opened by "
                "this one. Update the game to continue it.")
    if isinstance(exc, SyncedPathError):
        return (f"{what} is inside a cloud-synced folder (such as OneDrive), where the "
                "database can be corrupted. Move it to a local folder to play it.")
    if isinstance(exc, MigrationRefused):
        return f"{what} could not be brought up to date: {exc}"
    if isinstance(exc, sqlite3.DatabaseError):
        return f"{what} could not be read; the file may be damaged ({exc})."
    if isinstance(exc, FileNotFoundError):
        return f"{what} is no longer there: {exc}"
    return f"{what} could not be opened: {type(exc).__name__}: {exc}"
