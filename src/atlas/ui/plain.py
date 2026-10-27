"""The interface in a player's words: what to leave out unless technical details are on.

The engine writes for two readers at once. Its headlines carry their working -- "(odds 88%,
standing 38 -> 24)", "(1.7 -> 1.6)" -- and its reports carry their bookkeeping: which
powers were promoted into Focus Mode, how many batches applied, every power's refused
decision. All of it is true and useful to whoever is tuning the engine, and to a player it
is noise between them and what happened.

So the interface asks one question, :func:`technical`, and this module holds the rules for
the plain reading. Nothing is deleted from the record: switching technical details on in
Settings shows all of it again.
"""

from __future__ import annotations

import re

from .preferences import current

__all__ = ["plain_line", "routine_event", "someone_elses_pocket", "technical"]


def technical() -> bool:
    return current().technical


#: A parenthesis carrying the engine's working: odds, before -> after, standings, salience.
_WORKING = re.compile(
    r"\s*\((?=[^()]*(?:->|odds|standing|salience|\d+(?:\.\d+)?\s*/\s*\d+))[^()]*\)")


def plain_line(text: str) -> str:
    """A headline without its working, when technical details are off."""
    if technical():
        return text
    said = _WORKING.sub("", str(text))
    # Identifiers leaking into prose ("watered_down", "condemn_annexation").
    said = re.sub(r"(?<=[a-z])_(?=[a-z])", " ", said)
    return re.sub(r"\s{2,}", " ", said).strip()


def someone_elses_pocket(line: str, viewer_name: str | None) -> bool:
    """A line about another power's army running short: theirs to worry about."""
    if technical() or not viewer_name:
        return False
    return "cut off" in line and viewer_name not in line


#: Event kinds that are another power's routine administration rather than news.
_ROUTINE_KINDS = frozenset({"construction", "unit_raised", "order_issued", "research"})

#: Engine notices about its own bookkeeping, matched by how they open.
_ROUTINE_TEXT = (
    "the powers worth watching change",
    "the war ledgers were brought up to date",
    # The engine's monthly stage directions: one each per turn, never news.
    "the quarter's revenues are collected",
    "cargoes move, and the furnaces burn",
    "formations beyond the reach of their depots",
    "occupied provinces chafe under the flags",
    "ground held down, fought over or shut out of trade",
    "air superiority not flown for",
    "a month of air fighting cost",
)
_ROUTINE_FRAGMENTS = (
    "changed their mind about their government",
    # Dozens a turn in any air war; the turn summary keeps the ones that matter.
    "fighting in the air over",
    "strength in replacements",
    "set its objectives",
    "lays down",
)


def routine_event(kind: str, text: str, actor: str | None, viewer: str | None,
                  origin: str | None = None) -> bool:
    """Whether an event is bookkeeping a player need not read: hidden unless technical.

    Anything the player's own country did, or that names them, is never routine.
    """
    if technical():
        return False
    if viewer and actor == viewer:
        return False
    said = str(text or "").strip().lower()
    if said.startswith(_ROUTINE_TEXT):
        return True
    if any(fragment in said for fragment in _ROUTINE_FRAGMENTS):
        return True
    if str(kind) == "narrative" and str(origin or "").upper() == "ENGINE" and not actor:
        # A notice the engine writes about itself, with nobody doing anything.
        return True
    return str(kind) in _ROUTINE_KINDS
