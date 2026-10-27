"""Morale, which already existed and never moved.

`unit.morale` has scaled combat weight since the first commit -- ``strength * (morale / 50)``
in the ground resolver and in the air layer both, and it scales how long ground is held. It
is seeded with real spread and then, in ordinary play, **frozen**: a twenty-turn game moved
it on **0 of 645 units**. One thing raises it (digging in, +4) and two lower it (an air
strike, a warhead), none of which happens in a game nobody orders them in. So the axis that
multiplies every battle in the engine was inert, and the variety in it was whatever the
scenario wrote down.

This is the probe that turns it on. It is deliberately the smallest thing that could:
no schema, no new column, one function, three signals.

**Out of supply is the sharp one.** The supply layer already walks the map every turn and
already knows which formations are cut off -- it charges them *attrition*, which is
permanent. Morale is the half that is not: an army out of supply is worse at fighting
before it is smaller, and it gets better again when the line reopens.

**Economic strain is the graded one, and it is deliberately not the arrears flag.** A
nation is only "in arrears" when it cannot pay at all, which across five thirty-turn
play-test games happened **zero times** -- so an arrears-driven mechanic is a mechanic that
never fires. What does vary is how much of a country's revenue its forces eat: most powers
sit near 0.8 and the stretched ones pass 0.9, so the penalty starts there and scales.

**Recovery pulls up and never down.** Drifting everything toward the mean would erase the
seeded spread, which is the only variety the axis currently has -- an elite formation would
decay to average for no reason anyone could name. A unit above the baseline stays there
until something knocks it down; a unit below it recovers toward the baseline and stops.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility
from .budget import strain_pressure
from .logistics import supply_map
from .pacing import over

__all__ = [
    "MORALE_BASELINE",
    "MORALE_CEILING",
    "MORALE_FLOOR",
    "MoraleReport",
    "drift_morale",
]

#: What a formation settles at when nothing is happening to it. The value every unit is
#: created with, and the divisor combat already uses -- ``strength * (morale / 50)`` -- so
#: a unit at the baseline fights at exactly its strength and the whole axis is a modifier
#: around one rather than a second strength stat.
MORALE_BASELINE = 50.0

#: **A formation is never worthless and never invincible.** The ceiling matches the one
#: `orders` already enforces when digging in; the floor is well above zero because an army
#: at a third of its fighting spirit is still an army, and a multiplier that can reach zero
#: makes a battle a formality rather than a fight.
MORALE_FLOOR = 15.0
MORALE_CEILING = 95.0

#: Per reference month. **Recovery is slower than either way of losing it**, which is the
#: same asymmetry the arsenal readiness carries and for the same reason: an army's
#: confidence is quicker to lose than to rebuild.
MORALE_RECOVERY = 1.5
MORALE_UNSUPPLIED = 3.0
MORALE_STRAIN = 2.0



@dataclass(slots=True)
class MoraleReport:
    #: ``"<nation>'s forces are ..."``, one line per nation whose morale moved, not per
    #: unit -- six hundred formations drifting is one piece of news, not six hundred.
    news: list[str] = field(default_factory=list)
    moved: int = 0
    problems: list[str] = field(default_factory=list)


def drift_morale(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    span: float = 1.0,
    reason_event_id: int | None = None,
) -> MoraleReport:
    """Move every formation's morale toward what its supply and its treasury support.

    Written as diffs like everything else that has to survive a rewind, and cited to the
    world's genesis event rather than to one of its own: morale drifts every turn for every
    formation, and an event apiece would be six hundred lines of nothing a turn.
    """
    report = MoraleReport()

    if reason_event_id is None:
        row = conn.execute(
            "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis'"
            " ORDER BY id LIMIT 1",
            (world_id,),
        ).fetchone()
        if row is None:
            return report
        reason_event_id = int(row["id"])

    armed = [
        (str(r["id"]), str(r["name"])) for r in conn.execute(
            "SELECT DISTINCT n.id, n.name FROM nation n JOIN unit u ON u.nation_id = n.id"
            "  AND u.world_id = n.world_id"
            " WHERE n.world_id = ? AND n.is_alive = 1 ORDER BY n.id",
            (world_id,))
    ]
    if not armed:
        return report

    diffs: list[Diff] = []
    for nation_id, name in armed:
        # **One copy of this, in `budget`.** The arsenal readiness reads the same signal,
        # and a second implementation would drift from this one the first time the revenue
        # formula changed.
        over_line = strain_pressure(conn, world_id, nation_id)
        reach = supply_map(conn, world_id, nation_id)
        units = conn.execute(
            "SELECT u.id, u.morale, u.region_id FROM unit u"
            "  JOIN region r ON r.id = u.region_id AND r.world_id = u.world_id"
            " WHERE u.world_id = ? AND u.nation_id = ? AND r.kind <> 'ocean'"
            " ORDER BY u.id",
            (world_id, nation_id),
        ).fetchall()
        cut = 0
        for unit in units:
            now = float(unit["morale"])
            step = 0.0
            if str(unit["region_id"]) not in reach:
                step -= MORALE_UNSUPPLIED
                cut += 1
            step -= MORALE_STRAIN * over_line
            if step == 0.0 and now < MORALE_BASELINE:
                # **Up only.** Pulling a unit above the baseline back down would erase the
                # seeded spread, which is the only variety this axis has.
                step = min(MORALE_RECOVERY, MORALE_BASELINE - now)
            if step == 0.0:
                continue
            target = max(MORALE_FLOOR, min(MORALE_CEILING, now + over(step, span)))
            if abs(target - now) < 0.05:
                continue
            diffs.append(Diff(
                "unit", str(unit["id"]), "morale", DiffOp.SET, round(target, 2),
                reason_event_id=reason_event_id,
                visibility_scope=Visibility.PUBLIC))
        if cut:
            report.news.append(
                f"{name}: {cut} formation(s) losing heart out of supply")
        elif over_line > 0.5:
            report.news.append(
                f"{name}: the forces are eating the budget and it shows")

    if not diffs:
        return report
    applied = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
        proposer_model=None, diffs=tuple(diffs)))
    if applied.applied:
        report.moved = len(diffs)
    else:
        report.problems.append(f"morale could not be written: {applied.reason}")
        report.news.clear()
    return report
