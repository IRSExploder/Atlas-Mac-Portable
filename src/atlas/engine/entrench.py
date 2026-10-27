"""Digging in, earned by standing still.

**The second attempt at a defensive layer, and the first one is why this one is shaped
like this.** The first was a flat multiplier for defending your own soil. It read well and
measured badly: Ukraine ended a five-seed play-test with *fewer* provinces than without it,
[0, 5, 5, 9, 7] falling to [0, 4, 4, 7, 7]. The reason is that "defender" is a role that
changes hands the instant ground does. An aggressor who has taken a province is, from that
moment, the one defending it -- so a bonus paid to whoever is currently defending slows the
taking of ground and then protects the ground that has been taken. It cemented the conquest
it was meant to hinder, and cost about 25% of the pace of every campaign in every era to do
it.

What actually separates a dug-in defender from a fresh conqueror is *time in place*. So
that is what is measured here: entrenchment accrues over the months a formation holds one
region and is lost the moment it is somewhere else. A defender standing on a line it has
held since the war began is hard to shift; the army that just took the next province over
has dug nothing and gets nothing, and has to hold it for months before it does. On a moving
front the defender is reliably the better dug-in side, which is the asymmetry the flat
version could not express.

Nothing here draws from the RNG. Two runs of the same turn produce the same spades.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility
from .pacing import over, span_of

__all__ = [
    "ENTRENCH_CEILING",
    "ENTRENCH_PER_MONTH",
    "EntrenchReport",
    "drift_entrenchment",
    "entrenchment_bonus",
]

#: The most a fully dug-in formation adds to its own resilience, as a fraction. At the
#: ceiling a defender takes about a fifth longer to shift.
#:
#: **Deliberately a fifth and not the third the flat version used.** That one moved three
#: pacing bounds across the whole project and pushed a comparable-power conquest from two
#: years to two and a half. This is smaller, it is zero at the start of every game, it is
#: zero for anybody who has just arrived somewhere, and it is therefore nowhere near
#: always-on -- which is what keeps it from being a global brake on pace rather than a
#: defensive mechanic. **Balance number, tunable.**
ENTRENCH_CEILING = 0.20

#: How much of that is earned per reference month in place. Four months to be properly dug
#: in: long enough that a front which is moving never gets there, short enough that a front
#: which has stalled becomes genuinely hard to restart -- which is the shape of a grind.
ENTRENCH_PER_MONTH = 0.05

#: Below this a change is not worth a diff. Entrenchment moves in hundredths and a turn
#: that writes six hundred no-op diffs is a turn that costs more to rewind than to play.
_NOTICEABLE = 0.005


@dataclass
class EntrenchReport:
    """What the spades did this turn."""

    dug_in: int = 0
    #: Formations whose entrenchment was lost because they are no longer where they earned
    #: it -- they advanced, retreated, or were thrown out.
    uprooted: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.dug_in or self.uprooted)


def entrenchment_bonus(conn: sqlite3.Connection, nation_id: str, region_id: str) -> float:
    """How dug in this nation's formations in this region are, as a resilience fraction.

    The mean rather than the best of them: a line is held by the formations on it, and one
    well-sited battalion does not entrench a province. Zero when the nation has nothing
    there, which is the same answer as "not dug in" and needs no special case.
    """
    row = conn.execute(
        "SELECT AVG(entrenchment) AS dug FROM unit"
        " WHERE nation_id = ? AND region_id = ?",
        (nation_id, region_id),
    ).fetchone()
    if row is None or row["dug"] is None:
        return 0.0
    return max(0.0, min(ENTRENCH_CEILING, float(row["dug"])))


def drift_entrenchment(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: float = 30.0
) -> EntrenchReport:
    """Advance every formation's entrenchment, or take it away.

    A formation is compared against ``entrenched_at`` -- the region it last did its digging
    in. If it is somewhere else it starts again from nothing, and if it is where it was it
    goes a little deeper.

    **Checked against remembered state rather than hooked into movement.** Units are
    relocated from four separate places in the engine today, and a fifth added next year
    would be silently exempt from a reset that lived in the other four. This cannot be
    routed around: wherever a formation ends up and however it got there, the comparison is
    made here.
    """
    report = EntrenchReport()
    span = span_of(days)
    if span <= 0.0:
        return report

    # Land only. A fleet at sea is not dug in, and neither is a wing in the air; giving
    # them a number nothing reads would be inventing state to keep it tidy.
    units = conn.execute(
        "SELECT u.id, u.region_id, u.entrenchment, u.entrenched_at"
        "  FROM unit u JOIN region r ON r.id = u.region_id"
        " WHERE u.world_id = ? AND r.kind <> 'ocean'"
        " ORDER BY u.id",
        (world_id,),
    ).fetchall()
    if not units:
        return report

    moved: list[sqlite3.Row] = []
    settled: list[tuple[sqlite3.Row, float]] = []
    for unit in units:
        here = str(unit["region_id"])
        where = str(unit["entrenched_at"] or "")
        if where != here:
            moved.append(unit)
            continue
        now = float(unit["entrenchment"] or 0.0)
        step = min(over(ENTRENCH_PER_MONTH, span), ENTRENCH_CEILING - now)
        if step >= _NOTICEABLE:
            settled.append((unit, round(now + step, 3)))

    if not moved and not settled:
        return report

    # Cited to the world's genesis rather than to an event of its own, following
    # `drift_morale` exactly: this happens every turn to every formation on the map, and an
    # event apiece would be six hundred lines of nothing a turn. CORE-001 still requires a
    # cause, and the founding is the one every nation may always name.
    row = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,),
    ).fetchone()
    if row is None:
        return report
    event_id = int(row["id"])

    diffs: list[Diff] = []
    for unit in moved:
        # Two diffs, because the reset has to record *where* it starts again from -- a unit
        # whose entrenchment is zeroed but whose `entrenched_at` still names the old
        # province would reset again every turn and never dig at all.
        if float(unit["entrenchment"] or 0.0) > 0.0:
            diffs.append(
                Diff("unit", str(unit["id"]), "entrenchment", DiffOp.SET, 0.0,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        diffs.append(
            Diff("unit", str(unit["id"]), "entrenched_at", DiffOp.SET,
                 str(unit["region_id"]),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    for unit, value in settled:
        diffs.append(
            Diff("unit", str(unit["id"]), "entrenchment", DiffOp.SET, value,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if not outcome.applied:
        report.problems.append(
            f"entrenchment could not be written: {outcome.reason}")
        return report

    report.dug_in = len(settled)
    report.uprooted = len(moved)
    return report
