"""What the player's own position looks like, and how it moved last turn.

The top bar shows six meters with a change against the previous turn. That delta is not
stored anywhere -- and deliberately so. Caching "last turn's value" alongside the current
one would be a second copy of state that can fall out of step with the first, which is
exactly the class of bug the diff pipeline exists to prevent.

Instead the delta is *derived*: every applied change already records the value it replaced
and the value it wrote, so summing this turn's diffs for one field gives the movement
exactly, and rewinding gets the right answer for free.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..state.tables import json_list
from .leaders import leader_of

__all__ = ["METERS", "PlayerSnapshot", "load_player_snapshot"]

#: The six meters, with the ceiling each is drawn against. Treasury and manpower have no
#: natural maximum, so they are scaled against a plausible upper bound rather than
#: pretending to be percentages.
METERS: tuple[tuple[str, str, float], ...] = (
    ("military_strength", "Military", 100.0),
    ("economy", "Economy", 100.0),
    ("stability", "Stability", 100.0),
    ("legitimacy", "Legitimacy", 100.0),
    ("treasury", "Treasury", 150.0),
    ("manpower", "Manpower", 200.0),
)


@dataclass(frozen=True, slots=True)
class MeterValue:
    key: str
    label: str
    value: float
    ceiling: float
    delta: float

    @property
    def fraction(self) -> float:
        return max(0.0, min(1.0, self.value / self.ceiling)) if self.ceiling else 0.0


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    nation_id: str | None
    name: str = ""
    colour: str = "#888888"
    character: str = ""
    world_date: str = ""
    turn: int = 0
    days_elapsed: int = 0
    meters: tuple[MeterValue, ...] = ()
    goals: tuple[str, ...] = field(default_factory=tuple)
    #: Who is actually governing, as prose -- "Stanley Baldwin, Prime Minister", or "the
    #: government" for a country no scenario table names. Empty for a world written before
    #: schema 13, which is why the top bar falls back rather than showing a blank.
    government: str = ""

    @property
    def playing(self) -> bool:
        return self.nation_id is not None


def _deltas_for(conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int) -> dict[str, float]:
    """How much each field moved on ``turn``, read from the applied diffs.

    Derived rather than stored: the audit trail already knows, and a cached copy could
    disagree with it after a rewind.
    """
    rows = conn.execute(
        """
        SELECT d.field, d.old_json, d.new_json
          FROM diff d
          JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'nation' AND d.target_id = ?
         ORDER BY d.batch_id, d.seq
        """,
        (world_id, turn, nation_id),
    ).fetchall()

    moved: dict[str, float] = {}
    for row in rows:
        try:
            before = float(json.loads(row["old_json"]) or 0.0)
            after = float(json.loads(row["new_json"]) or 0.0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        moved[row["field"]] = moved.get(row["field"], 0.0) + (after - before)
    return moved


def _days_elapsed(conn: sqlite3.Connection, world_id: str) -> int:
    row = conn.execute(
        """
        SELECT MIN(start_date) AS first, MAX(COALESCE(end_date, start_date)) AS last
          FROM turn WHERE world_id = ?
        """,
        (world_id,),
    ).fetchone()
    if row is None or not row["first"] or not row["last"]:
        return 0
    from datetime import date

    try:
        return (date.fromisoformat(row["last"]) - date.fromisoformat(row["first"])).days
    except (TypeError, ValueError):
        return 0


def load_player_snapshot(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str | None = None
) -> PlayerSnapshot:
    """The top bar's data: who you are, when it is, and how the year treated you."""
    world = conn.execute(
        "SELECT world_date, turn_number FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    if world is None:
        return PlayerSnapshot(nation_id=None)

    turn = int(world["turn_number"])
    if nation_id is None:
        found = conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_player = 1", (world_id,)
        ).fetchone()
        nation_id = found["id"] if found else None

    if nation_id is None:
        return PlayerSnapshot(
            nation_id=None, world_date=world["world_date"], turn=turn,
            days_elapsed=_days_elapsed(conn, world_id),
        )

    row = conn.execute(
        """
        SELECT name, colour, personality_blurb, standing_goals_json,
               military_strength, economy, stability, legitimacy, treasury, manpower
          FROM nation WHERE id = ?
        """,
        (nation_id,),
    ).fetchone()
    if row is None:
        return PlayerSnapshot(nation_id=None, world_date=world["world_date"], turn=turn)

    government = leader_of(conn, nation_id)
    moved = _deltas_for(conn, world_id, nation_id, turn)
    meters = tuple(
        MeterValue(
            key=field_name,
            label=label,
            value=round(float(row[field_name]), 1),
            ceiling=ceiling,
            delta=round(moved.get(field_name, 0.0), 1),
        )
        for field_name, label, ceiling in METERS
    )

    return PlayerSnapshot(
        nation_id=nation_id,
        name=row["name"],
        colour=row["colour"],
        character=row["personality_blurb"],
        world_date=world["world_date"],
        turn=turn,
        days_elapsed=_days_elapsed(conn, world_id),
        meters=meters,
        goals=tuple(json_list(row["standing_goals_json"])),
        government=government.described if government is not None else "",
    )
