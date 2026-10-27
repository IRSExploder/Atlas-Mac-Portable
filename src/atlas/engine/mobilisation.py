"""How deep a country digs for soldiers, and what digging costs it.

**The floor the war layer kept hitting.** Replacement, garrisons, the economy and allied
support were each built and measured, and after all four the Ukraine front came down to the
same thing: the defender's manpower fell to zero by turn twelve while its treasury piled up
unspent. It had money it could not turn into soldiers, and the size of its pool was a fact
about its population that nothing it did could change. A constraint nobody can act on is not
a decision -- it is just the answer.

This makes it one. A government may dig deeper for men, and pays for it in the two places a
real mobilisation is paid for: **fewer hands in the factories, and a country that likes it
less the longer it goes on.**

**Four levels rather than a dial**, because the decision is which footing the country is on
and a staff asked for a number between nought and one will pick 0.7 for no reason it could
defend. And **one step a turn in either direction**: a nation cannot go from peacetime to
total war in a month, and cannot stand down overnight either.

Nothing here draws from the RNG. The same turn produces the same call-up twice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility
from .pacing import over, span_of

__all__ = [
    "LEVELS",
    "MAX_LEVEL",
    "MobilisationReport",
    "ceiling_factor",
    "drift_mobilisation",
    "label_of",
    "level_of",
    "recovery_factor",
    "revenue_drag",
    "set_level",
    "stability_cost",
]


@dataclass(frozen=True)
class Footing:
    """One rung of the ladder, and everything it implies."""

    level: int
    label: str
    #: What it multiplies the manpower ceiling by -- how many people can be reached at all.
    ceiling: float
    #: What it multiplies the refill rate by -- how fast they are processed and equipped.
    recovery: float
    #: The share of revenue lost to hands that are no longer in the factories.
    drag: float
    #: Stability lost per reference month at this footing. The cost that *accumulates*, and
    #: the reason total war is not simply correct.
    unrest: float
    blurb: str


#: **Balance numbers, every one of them.** Chosen so the ladder is a curve rather than a
#: ramp: each rung buys *less* than the last and costs *more*, so there is a defensible
#: answer at every level and "always go to the top" is not it. A country at total war reaches
#: about twice the men and pays about a fifth of its economy and a point and a half of
#: stability a month for them.
#:
#: **The first draft got the curve backwards and its own test caught it.** The ceiling gains
#: were 0.35, 0.45 and 0.60 -- accelerating, so every rung bought more than the one below
#: while also costing more, which makes the top of the ladder the obvious answer rather than
#: the desperate one. The test asserting diminishing returns was written from the design
#: intent and failed against the numbers, which is the correct way round for that argument
#: to be settled. Gains are now 0.45, 0.30, 0.20 against costs rising 0.25, 0.45, 0.80.
LEVELS: tuple[Footing, ...] = (
    Footing(0, "peacetime", 1.00, 1.00, 0.00, 0.00,
            "The army is what the army is. Nobody is being called up."),
    Footing(1, "partial mobilisation", 1.45, 1.40, 0.04, 0.25,
            "Reservists are recalled and the classes already trained are brought back. "
            "Barely felt at home."),
    Footing(2, "general mobilisation", 1.75, 1.70, 0.10, 0.70,
            "The call-up is general and the factories notice. A country can hold this for "
            "a long war, and knows it is in one."),
    Footing(3, "total war", 1.95, 1.90, 0.18, 1.50,
            "Everybody who can be spared, and some who cannot. The economy is visibly "
            "poorer and the patience of the country is being spent as fast as its men."),
)

MAX_LEVEL = LEVELS[-1].level

#: Below this a stability charge is not worth a diff, on the same argument every other
#: per-turn drift in the engine makes.
_NOTICEABLE = 0.005


@dataclass
class MobilisationReport:
    """What the call-up did this turn."""

    #: One line per nation whose footing changed, for the turn report.
    changed: list[str] = field(default_factory=list)
    #: How many nations paid a stability charge for standing mobilised.
    strained: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.changed or self.strained)


def _footing(level: int) -> Footing:
    return LEVELS[max(0, min(MAX_LEVEL, int(level)))]


def level_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    """This nation's footing, or peacetime for a world written before schema 41."""
    try:
        row = conn.execute(
            "SELECT mobilisation FROM nation WHERE world_id = ? AND id = ?",
            (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["mobilisation"] or 0) if row is not None else 0


def label_of(level: int) -> str:
    return _footing(level).label


def ceiling_factor(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What the footing multiplies the manpower ceiling by. ``1.0`` at peacetime.

    Multiplies alongside the doctrine's own factor rather than replacing it: *how* a country
    raises its army and *how hard it is currently digging* are different questions, and a
    nation on mass mobilisation at a total-war footing is doing both.
    """
    return _footing(level_of(conn, world_id, nation_id)).ceiling


def recovery_factor(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What it multiplies the manpower refill rate by. ``1.0`` at peacetime."""
    return _footing(level_of(conn, world_id, nation_id)).recovery


def revenue_drag(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """The share of revenue the call-up costs. ``0.0`` at peacetime.

    Lands in `nation_budget` beside `labour_drag`, so the money a player is shown and the
    money the treasury receives stay the same number -- the rule that layer has held since
    it was written.
    """
    return _footing(level_of(conn, world_id, nation_id)).drag


def stability_cost(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """Stability lost per reference month at this footing. ``0.0`` at peacetime."""
    return _footing(level_of(conn, world_id, nation_id)).unrest


def set_level(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    wanted: int,
    turn: int,
    date: str,
    reason_event_id: int | None = None,
) -> tuple[int, str]:
    """Move a nation one step toward ``wanted``. Returns ``(new level, what happened)``.

    **One step, whichever direction.** A staff that asks for total war from a standing start
    gets general mobilisation this month and total war the next, which is both the honest
    answer and the thing that makes the decision a commitment rather than a switch. Asking
    for the level you are already on is not an error and does nothing.
    """
    now = level_of(conn, world_id, nation_id)
    target = max(0, min(MAX_LEVEL, int(wanted)))
    if target == now:
        return now, ""
    step = 1 if target > now else -1
    landed = now + step

    if reason_event_id is None:
        row = conn.execute(
            "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
            (world_id,)).fetchone()
        if row is None:
            return now, "this world has no founding event to cite"
        reason_event_id = int(row["id"])

    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(Diff("nation", nation_id, "mobilisation", DiffOp.SET, landed,
                        reason_event_id=reason_event_id,
                        visibility_scope=Visibility.PUBLIC),),
        ),
    )
    if not outcome.applied:
        return now, outcome.reason or "the change was refused"

    name = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    who = str(name["name"]) if name else nation_id
    moved = "steps up to" if step > 0 else "stands down to"
    return landed, f"{who} {moved} {label_of(landed)}"


def drift_mobilisation(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: float = 30.0
) -> MobilisationReport:
    """Charge every mobilised country the stability its call-up costs.

    **This is the whole reason the ladder has a top rather than a dial.** The manpower a
    footing buys arrives free at the moment it is declared; what it costs arrives every
    month afterwards, and it accumulates. A government that mobilises for a short war pays
    little. One that stands at total war for three years is spending the patience of its
    people at a rate the unrest layer will eventually present a bill for -- which is the
    decision, and which is why nothing here caps the damage.

    Charged per reference month, like every other rate in the engine, so a slow-playing game
    is not a differently balanced one.
    """
    report = MobilisationReport()
    span = span_of(days)
    if span <= 0.0:
        return report

    try:
        rows = conn.execute(
            "SELECT id, name, stability, mobilisation FROM nation"
            " WHERE world_id = ? AND is_alive = 1 AND mobilisation > 0 ORDER BY id",
            (world_id,)).fetchall()
    except sqlite3.OperationalError:
        # A world written before schema 41. Nobody is mobilised, which is what it was.
        return report
    if not rows:
        return report

    event = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,)).fetchone()
    if event is None:
        return report
    event_id = int(event["id"])

    diffs: list[Diff] = []
    for row in rows:
        charge = over(_footing(int(row["mobilisation"])).unrest, span)
        if charge < _NOTICEABLE:
            continue
        # Floored at zero rather than allowed negative: a country at no stability at all is
        # a problem the unrest layer owns, and a negative one is a number nothing downstream
        # can reason about.
        landing = max(0.0, float(row["stability"]) - charge)
        if abs(landing - float(row["stability"])) < _NOTICEABLE:
            continue
        diffs.append(
            Diff("nation", str(row["id"]), "stability", DiffOp.SET, round(landing, 2),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    if not diffs:
        return report
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(
            f"the strain of mobilisation could not be written: {outcome.reason}")
        return report
    report.strained = len(diffs)
    return report


def briefing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What footing this country is on, what the others would buy, and what they cost.

    Every rung is priced, including the one below: a staff that can see only the way up is
    being offered an escalation rather than a decision.
    """
    now = level_of(conn, world_id, nation_id)
    here = _footing(now)
    lines = [
        f"## Your war footing: {here.label}",
        here.blurb,
    ]
    if now:
        lines.append(
            f"It is costing you {here.drag:.0%} of your revenue and "
            f"{here.unrest:.2f} stability a month, every month, for as long as you stay on it."
        )
    rungs = []
    for step in (now - 1, now + 1):
        if 0 <= step <= MAX_LEVEL:
            other = _footing(step)
            rungs.append(
                f"`{other.level}` -- {other.label}: manpower ceiling x{other.ceiling:.2f}, "
                f"refilling x{other.recovery:.2f}, costing {other.drag:.0%} of revenue and "
                f"{other.unrest:.2f} stability a month. {other.blurb}"
            )
    if rungs:
        lines.append(
            "Set `mobilisation` to a level to move toward it. **One step a turn** in either "
            "direction, so a footing is a commitment rather than a switch, and asking for a "
            "level two rungs away simply takes two turns."
        )
        lines.extend(f"- {rung}" for rung in rungs)
    lines.append(
        "Manpower is what replacements are actually paid in. If your formations are worn "
        "and your treasury is not the thing stopping you, this is the thing stopping you."
    )
    lines.append("")
    return lines
