"""Whether the player's campaign has reached an ending, and what the final reckoning is.

The engine already knows every fact an ending is made of; nothing here writes anything.
:mod:`atlas.engine.scoring` records victories as events, :mod:`atlas.engine.capitulation`
ends wars and marks the erased as not alive -- but nothing *told the player*. A nation
could be partitioned out of existence and the next screen was the ordinary turn summary.
This module reads the state after a turn and answers one question for the interface: is
this an ending, and which kind.

**Four endings, and only one is forced.**

* ``defeat`` -- the player's nation has been eliminated (not alive), or holds no ground at
  all, which is capitulation: occupied everywhere, nowhere left to fight from. The player
  can keep watching or go back to the menu; the world does not stop.
* ``victory`` -- the player met a victory condition this turn. *End here, or play on* --
  scoring's own rule is that winning does not end a sandbox.
* ``superseded`` -- another power met a condition in :data:`scoring.TERMINATION`: the game
  has effectively ended, and not in the player's favour.
* ``complete`` -- the campaign's turn limit is reached. Also *end here or play on*.

**Rewind-exact by construction.** Every input is world state or a turn report; the only
thing remembered is which endings the player chose to play past, and that is kept in the
workspace sidecar beside the save (like pins and drafts), never in the world -- so it does
not move the fingerprint and a rewind past it re-arms nothing it should not.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .scoring import CONDITIONS, TERMINATION, WEIGHTS, standings

__all__ = [
    "CAMPAIGN_TURNS",
    "Ending",
    "Reckoning",
    "campaign_turn_limit",
    "ending_after",
    "play_on",
    "reckoning",
]

#: The default campaign length: one hundred and fifty monthly turns, about twelve years.
#: The number every long validation run in this project has used for "a full campaign".
#: A world can carry its own limit in its workspace (``campaign_turn_limit``); 0 there
#: means no limit.
CAMPAIGN_TURNS = 150

_PLAYED_ON = "campaign_played_on"
_LIMIT = "campaign_turn_limit"


@dataclass(frozen=True, slots=True)
class Ending:
    #: ``defeat`` | ``victory`` | ``superseded`` | ``complete``
    kind: str
    #: One line for the screen's title area.
    headline: str
    #: The condition behind a victory or a supersession, for remembering a "play on".
    condition: str = ""

    @property
    def key(self) -> str:
        """What a "play on" is remembered under: one prompt per distinct ending."""
        return f"{self.kind}:{self.condition}" if self.condition else self.kind


@dataclass(frozen=True, slots=True)
class Reckoning:
    """The final score, as the end screen lays it out."""

    nation: str
    rank: int
    of: int
    total: float
    #: ``(component, value 0-1, weight)``, in the order :data:`WEIGHTS` lists them.
    components: tuple[tuple[str, float, float], ...]
    leader: str
    leader_total: float


def campaign_turn_limit(conn: sqlite3.Connection) -> int:
    from ..state.workspace import recall

    stored = recall(conn, _LIMIT, None)
    try:
        return CAMPAIGN_TURNS if stored is None else max(0, int(stored))
    except (TypeError, ValueError):
        return CAMPAIGN_TURNS


def _player(conn: sqlite3.Connection, world_id: str, nation_id: str | None):
    if nation_id:
        return conn.execute(
            "SELECT id, name, is_alive FROM nation WHERE world_id = ? AND id = ?",
            (world_id, nation_id)).fetchone()
    return conn.execute(
        "SELECT id, name, is_alive FROM nation WHERE world_id = ? AND is_player = 1",
        (world_id,)).fetchone()


def _condition_of(headline: str) -> tuple[str, str] | None:
    """``(nation name, condition)`` from a victory headline, as scoring writes them."""
    for condition, phrase in CONDITIONS.items():
        if headline.endswith(f" {phrase}"):
            return headline[: -len(phrase) - 1], condition
    return None


def ending_after(conn: sqlite3.Connection, world_id: str, *, turn: int,
                 victories: list[str] | tuple[str, ...] = (),
                 player: str | None = None) -> Ending | None:
    """The ending this turn reached for the player, or ``None`` to carry on as normal.

    An ending the player already chose to play past is not offered again; defeat is the
    exception only in that it has no "play on" -- watching on after a defeat is remembered
    the same way, so it is said once.
    """
    from ..state.workspace import recall

    row = _player(conn, world_id, player)
    if row is None:
        return None
    nation_id, name = str(row["id"]), str(row["name"])
    played_on = set(recall(conn, _PLAYED_ON, []) or [])

    def fresh(ending: Ending) -> Ending | None:
        return None if ending.key in played_on else ending

    if not int(row["is_alive"]):
        return fresh(Ending("defeat", f"{name} has been partitioned out of existence."))
    held = conn.execute(
        "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
        (world_id, nation_id)).fetchone()
    if int(held["n"] if held else 0) == 0:
        return fresh(Ending(
            "defeat", f"{name} holds no ground anywhere and can fight no longer."))

    for headline in victories:
        parsed = _condition_of(headline)
        if parsed is None:
            continue
        who, condition = parsed
        if who == name:
            ending = fresh(Ending("victory", headline, condition))
        elif condition in TERMINATION:
            ending = fresh(Ending("superseded", headline, condition))
        else:
            continue
        if ending is not None:
            return ending

    limit = campaign_turn_limit(conn)
    if limit and turn >= limit:
        return fresh(Ending("complete", f"The campaign has run its {limit} turns."))
    return None


def play_on(conn: sqlite3.Connection, ending: Ending) -> bool:
    """Remember that the player chose to carry on past ``ending``."""
    from ..state.workspace import recall, remember

    kept = list(recall(conn, _PLAYED_ON, []) or [])
    if ending.key not in kept:
        kept.append(ending.key)
    return remember(conn, _PLAYED_ON, kept)


def reckoning(conn: sqlite3.Connection, world_id: str,
              player: str | None = None) -> Reckoning | None:
    """The player's final standing against the field. ``None`` if there is no table."""
    row = _player(conn, world_id, player)
    table = standings(conn, world_id)
    if row is None or not table.scores:
        return None
    nation_id = str(row["id"])
    mine = next((score for score in table.scores if score.nation_id == nation_id), None)
    leader = table.scores[0]
    if mine is None:
        # Eliminated powers drop out of the table: last, with nothing.
        return Reckoning(str(row["name"]), len(table.scores) + 1, len(table.scores) + 1,
                         0.0, tuple((c, 0.0, w) for c, w in WEIGHTS.items()),
                         leader.name, leader.total)
    return Reckoning(
        nation=mine.name, rank=table.rank_of(nation_id), of=len(table.scores),
        total=mine.total,
        components=tuple((c, mine.components[c], w) for c, w in WEIGHTS.items()),
        leader=leader.name, leader_total=leader.total)
