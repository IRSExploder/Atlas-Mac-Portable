"""What a government is playing for, held across turns.

**Every turn began from nothing.** A nation received its briefing, decided, and forgot; the
next turn it decided again with no memory that it had spent six years avoiding a continental
war. Nothing in the engine held an answer to *which ending am I pursuing*, so nothing could
pursue one: a fifty-turn conquest is not a thing you can produce by choosing afresh fifty
times.

Three concepts live here and they are kept rigorously apart, because collapsing any two of
them would quietly replace the rules of the game with a heuristic:

``fitness``
    What this nation's *trajectory* suits. Read from rate and structure -- am I becoming the
    sort of power this route wants -- and **it reads no victory threshold anywhere**. That is
    a hard rule, not a preference: a fitness that consulted a threshold would silently retune
    itself when the threshold moved, and the victory constants are frozen.

``commitment``
    What the government *chose*, which is a fact about the government and not about the
    world. Stored, slow to change, and revisable only at a review or a shock.

``victory progress``
    Whether it is actually nearing a win. That lives in :mod:`atlas.engine.scoring` and is
    the only thing that decides anything. **Fitness must never become a shadow copy of it.**

**Grammar-agnostic on purpose.** :data:`OBJECTIVES` is a catalogue, the way
`doctrines.AXES` is a catalogue and `facts` owns its predicates. The machinery below knows
that an objective has milestones, postures, red lines and replan conditions. It does not
know how many endings the game has, and the victory frame is being reworked -- a layer that
hard-coded today's three conditions would have to be rebuilt along with them.

Measured before any of it was built, and both results shaped what is here:

* the route a power is fittest for **moves** -- it changed hands in 15 of 18 runs over 30
  turns -- which is what makes a commitment worth holding and also what would make a
  government thrash if it acted on every crossover. Hence :data:`SWITCH_MARGIN`.
* **preeminence is an ending, not a route**, and it is classified that way rather than
  merely switched off. Two measurements, neither of them a judgement call: a power handed a
  thousand times the economy, industry and military of the whole world moves from a 1.19
  lead to 1.28, because the five components are each clamped at their own ceiling -- so it
  cannot be grown into. And playing for it is worse than useless: across three seeds and
  three tiers a specialist that out-developed its control in *every* arm, and was the only
  power in any run to research anything, lost on ratio anyway. The field converges toward
  the same clamps, so the ratio is mean-reverting and punishes development. What the
  condition does do is end a world that has already effectively ended -- see
  `atlas.engine.scoring.TERMINATION`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility
from .pacing import days_between

__all__ = [
    "ABSTAIN",
    "ABSTAIN_BELOW",
    "OBJECTIVES",
    "REVIEW_DAYS",
    "SWITCH_HOLDS",
    "SWITCH_MARGIN",
    "Objective",
    "Plan",
    "commit_plan",
    "replan_trigger",
    "review_world",
    "due_for_review",
    "fitness_of",
    "objective_named",
    "plan_of",
    "revisions_of",
    "selectable",
    "select",
    "strategy_id_for",
]

#: What a government holds when it has looked and found nothing worth committing to.
#:
#: **Not the absence of an answer -- an answer.** The tilt below always crowns a winner: it
#: compares three numbers and one of them is always largest, however feeble all three are. A
#: power with no good option should be able to say so, and a strategy layer that assigned
#: every minor on the map a grand design would be worse than none.
ABSTAIN = ""

#: How far ahead a route must be, and for how many consecutive reviews, before a government
#: abandons what it is doing for it.
#:
#: **Hysteresis, built in from the start rather than added when the thrashing showed up.**
#: The fitness measurably moves -- the fittest route changed hands in 15 of 18 measured runs
#: -- and a government that switched on every crossover would produce exactly the
#: turn-to-turn incoherence this layer exists to remove, only now with a table behind it.
SWITCH_MARGIN = 0.08
SWITCH_HOLDS = 3

#: How high a route's own fitness must stand before it is worth pursuing at all.
#:
#: **The tilt says which route suits a power best; this says whether any of them does.** The
#: two are different questions and the controlled test proved it the hard way: a power at the
#: top of all three percentiles has a tilt of zero on all three -- its percentile equals the
#: mean of its percentiles -- so the strongest nation in the world was told it had no
#: strategy. Comparative advantage cannot be the only gate.
FLOOR = 0.30

#: The tilt a route needs before the comparison is meaningful at all. Below this the three
#: are a dead heat and the level decides.
ABSTAIN_BELOW = 0.02

#: How long between scheduled reviews, in days of world time.
#:
#: **A government is asked what it is doing about once a year, not every turn.** That is the
#: whole point of the layer: the model proposes a plan and then *lives with it*, rather than
#: being asked to remember one for fifty turns. Days rather than turns, like every duration
#: in the engine -- a review every eight turns is eight months at monthly pacing and eight
#: years at yearly, and the clock registry is right to refuse that.
REVIEW_DAYS = 365.0

#: How far the ground a government holds has to move, against what it held when it last
#: looked, before what happened to it is a reason to look again rather than a bad quarter.
#:
#: **Edge-triggered, and that is the whole of why a commitment survives having triggers at
#: all.** The comparison is against `seen_ground`, which `commit_plan` rewrites every time
#: it writes a plan -- so a trigger that fires makes its own move the new baseline, and the
#: next one needs a fresh move of its own. A *level* test would re-fire every turn for as
#: long as its condition held, and a government reviewed every turn is exactly the
#: turn-to-turn incoherence this module exists to remove.
#:
#: A quarter of a country is the size of thing that is unambiguously an event: on the
#: shipped map it is several provinces, not a border rectification, and no measurement
#: noise reaches it -- ground is a count of provinces and only moves when territory
#: changes hands.
REPLAN_GROUND_MOVE = 0.25


@dataclass(frozen=True, slots=True)
class Objective:
    """One thing a government can be playing for.

    Deliberately thin. An objective is an id, some words, and the two questions the fitness
    function needs answered -- never a victory condition, a threshold, or a rule.
    """

    id: str
    label: str
    blurb: str
    #: What a government pursuing this should be doing, in its own terms. Rendered into the
    #: briefing so a plan can be written against something.
    pursues: tuple[str, ...] = ()
    #: What this objective *is*. ``route`` is something a government may commit to and
    #: pursue; ``termination`` is an ending that can happen to a world, which no government
    #: should ever be told to chase.
    #:
    #: **A classification rather than a boolean, deliberately.** `offered=False` recorded
    #: that preeminence was not selectable and not *why*, so nothing stopped somebody
    #: flipping it back as a tuning decision. A route and an ending are different kinds of
    #: thing and the catalogue should say so.
    kind: str = "route"
    #: Why an objective is not pursuable, which a briefing shows rather than hiding.
    withheld_because: str = ""

    @property
    def offered(self) -> bool:
        return self.kind == "route"


OBJECTIVES: tuple[Objective, ...] = (
    Objective(
        id="conquest",
        label="taking and holding ground",
        blurb="Grow by taking what is next to you and keeping it.",
        pursues=(
            "a military edge over the strongest power you actually border",
            "wars you can finish, against neighbours you can reach",
            "mobilisation raised before the war rather than during it",
        ),
    ),
    Objective(
        id="concert",
        label="leading the international order",
        blurb="Be the power the world defers to, and end other people's wars.",
        pursues=(
            "agreements kept for years, because the record is what cannot be bought",
            "wars you are not in, brought to an end",
            "staying out of wars you would have to start",
        ),
    ),
    Objective(
        id="preeminence",
        label="standing above every other power",
        blurb="Outweigh the rest of the world on every measure at once.",
        kind="termination",
        withheld_because=(
            "this is how a game ends, not something to play for. Two measurements settle "
            "it. A power handed a thousand times the economy, industry and military of the "
            "entire world moves from a 1.19 lead to 1.28, because the score's five "
            "components are each clamped at their own ceiling -- so it cannot be grown "
            "into. And **playing for it is actively counterproductive**: across three seeds "
            "and three tiers a specialist that genuinely out-developed its control in every "
            "arm, and was the only power in any run to research anything, lost on ratio "
            "anyway. The field converges toward the same clamps, so the ratio is "
            "mean-reverting and punishes development. What it does do is end a world that "
            "has already effectively ended -- see `atlas.engine.scoring.TERMINATION`"
        ),
    ),
)


def objective_named(objective_id: str) -> Objective | None:
    return next((o for o in OBJECTIVES if o.id == objective_id), None)


def selectable() -> tuple[Objective, ...]:
    """The objectives a government may actually commit to -- routes, never endings."""
    return tuple(o for o in OBJECTIVES if o.kind == "route")


def strategy_id_for(nation_id: str) -> str:
    """Derived, because a rewind does not replay: a row the engine rewrites has to be
    addressable by what identifies it. The same argument `doctrine_id_for` makes."""
    return f"s_{nation_id}"


@dataclass
class Plan:
    """What one government is playing for, as it stands."""

    nation_id: str
    objective: str = ABSTAIN
    because: str = ""
    adopted_on: str = ""
    adopted_turn: int = 0
    reviewed_on: str = ""
    milestones: list[str] = field(default_factory=list)
    budget_posture: str = ""
    mobilisation_posture: int = 0
    red_lines: list[str] = field(default_factory=list)
    war_aims: list[str] = field(default_factory=list)
    minimum_peace: list[str] = field(default_factory=list)
    replan_on: list[str] = field(default_factory=list)
    contender: str = ""
    contender_streak: int = 0
    #: What this government looked like when it last wrote a plan. Written by `commit_plan`
    #: from its `seen` argument, never from the `Plan` -- carried here so a trigger can be
    #: asked whether anything has happened *since*, which is the question every one of them
    #: turns out to be.
    seen_on: str = ""
    seen_ground: float = 0.0

    @property
    def committed(self) -> bool:
        return bool(self.objective)


def _loads(raw) -> list[str]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def plan_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> Plan:
    """What this government is playing for. An empty plan when it has never been asked."""
    row = conn.execute(
        "SELECT * FROM nation_strategy WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id)).fetchone()
    if row is None:
        return Plan(nation_id=nation_id)
    return Plan(
        nation_id=nation_id,
        objective=str(row["objective"] or ""),
        because=str(row["because"] or ""),
        adopted_on=str(row["adopted_on"] or ""),
        adopted_turn=int(row["adopted_turn"] or 0),
        reviewed_on=str(row["reviewed_on"] or ""),
        milestones=_loads(row["milestones"]),
        budget_posture=str(row["budget_posture"] or ""),
        mobilisation_posture=int(row["mobilisation_posture"] or 0),
        red_lines=_loads(row["red_lines"]),
        war_aims=_loads(row["war_aims"]),
        minimum_peace=_loads(row["minimum_peace"]),
        replan_on=_loads(row["replan_on"]),
        contender=str(row["contender"] or ""),
        contender_streak=int(row["contender_streak"] or 0),
        seen_on=str(row["seen_on"] or ""),
        seen_ground=float(row["seen_ground"] or 0.0),
    )


def due_for_review(plan: Plan, *, date: str) -> bool:
    """Whether this government is owed a look at what it is doing.

    A plan never reviewed is due immediately; otherwise it is a calendar question.

    **The calendar only.** What else forces a review is `replan_trigger`, kept apart
    because the two answer different questions: this one is *has a year passed*, that one
    is *has something happened*, and a government reviews when either says so.
    """
    if not plan.reviewed_on:
        return True
    waited = days_between(plan.reviewed_on, date)
    return waited is None or waited >= REVIEW_DAYS


def replan_trigger(
    plan: Plan, *, ground: float, level: float, last_trigger: str = "",
) -> str:
    """Which of this government's own replan conditions holds today, or ``""``.

    **The briefing has been promising this all along.** `_commitment_lines` tells a
    government its plan is reviewed "about once a year, *or sooner if something happens
    that you said would force a rethink*", `commit_plan` documents the vocabulary those
    rethinks are recorded under, and the plan carries a `replan_on` list of them -- and
    nothing ever evaluated any of it. A government whose country was overrun in March kept
    ordering its January plan until the following January.

    The vocabulary is `commit_plan`'s and nothing here extends it:

    ``major_shock``
        The ground it holds has collapsed by `REPLAN_GROUND_MOVE` since it last looked.
    ``milestone_reached``
        The same move the other way -- it has materially got what it was playing for.
    ``commitment_cost``
        The route it is committed to no longer clears `FLOOR`, so it is paying for a plan
        it can no longer carry out. **Once per crossing, not once per turn**: unlike the
        other two this is a level and not a move, so it is suppressed while the last thing
        recorded about this government was itself a `commitment_cost` review. Without that
        it would fire every turn for as long as the level stayed down, and three of those
        in three turns would spend a `SWITCH_HOLDS` streak that is meant to take three
        years.

    ``replan_on`` narrows this when a government has stated its own conditions and is
    silent when it has not, which is every plan the engine writes today -- the model-facing
    half of a plan has no writer yet. An empty list therefore means "no conditions stated",
    not "no conditions", and all three are live.

    A trigger asks for a *look*, never for a change: the review it forces runs the same
    `select` as any other and usually reaffirms what was already held.
    """
    if not plan.committed or not plan.reviewed_on:
        # Never reviewed is the calendar's business (`due_for_review` says yes at once),
        # and a government committed to nothing has no commitment to reconsider.
        return ""
    named = set(plan.replan_on)
    if not plan.seen_on or plan.seen_ground <= 0.0:
        moved = 0.0
    else:
        moved = (ground - plan.seen_ground) / plan.seen_ground
    # Fixed order, so a world in which two conditions hold at once records the same one
    # every time it is replayed.
    if (not named or "major_shock" in named) and moved <= -REPLAN_GROUND_MOVE:
        return "major_shock"
    if (not named or "milestone_reached" in named) and moved >= REPLAN_GROUND_MOVE:
        return "milestone_reached"
    if ((not named or "commitment_cost" in named)
            and level < FLOOR and last_trigger != "commitment_cost"):
        return "commitment_cost"
    return ""


def select(
    tilts: dict[str, float], levels: dict[str, float], *, held: str, streak: int,
    contender: str = "",
) -> tuple[str, str, int]:
    """Which objective a government would be committed to. ``(objective, contender, streak)``.

    **Adopting and switching are different decisions**, and the first draft of this treated
    them as one -- it required a route to lead the next by `SWITCH_MARGIN` before a
    government would take it, including when the government held nothing at all. So a power
    whose two best routes were close never started on either, and every nation on the map
    abstained for ever. Hysteresis exists to stop *churn*; there is nothing to churn away
    from before the first plan.

    So: adopting asks only whether a route is worth doing (`FLOOR`), and ties break on the
    level. Switching asks for a clear margin held over consecutive reviews as well.
    """
    offered = [o.id for o in selectable()]
    worth_doing = [r for r in offered if levels.get(r, 0.0) >= FLOOR]
    if not worth_doing:
        # Nothing this government does well enough to build a decade around. A legitimate
        # answer, and the common one for a small power.
        return ABSTAIN, "", 0

    # Ranked on tilt, ties broken on level and then on id, so the same world always
    # produces the same plan and a rewind lands where it was.
    ranked = sorted(worth_doing,
                    key=lambda r: (-tilts.get(r, 0.0), -levels.get(r, 0.0), r))
    leader = ranked[0]

    if held == ABSTAIN or held not in offered:
        return leader, "", 0
    if leader == held:
        return held, "", 0

    runner_up = max((tilts.get(r, 0.0) for r in ranked[1:]), default=-1.0)
    if tilts.get(leader, 0.0) - runner_up < SWITCH_MARGIN:
        # A contender that stops leading clearly loses the case it had accumulated.
        return held, "", 0
    streak = streak + 1 if contender == leader else 1
    if streak >= SWITCH_HOLDS:
        return leader, "", 0
    return held, leader, streak


def commit_plan(  # noqa: PLR0913 - a plan is its objective, its parts and its clock
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    plan: Plan,
    trigger: str,
    turn: int,
    date: str,
    note: str = "",
    seen: dict[str, float] | None = None,
) -> bool:
    """Write this government's plan, and the record of why it changed.

    **Every revision is instrumented.** A strategy layer nobody can audit cannot be shown to
    be working, and the question worth answering afterwards is not *what did it choose* but
    *why did it change its mind*. `trigger` is an open vocabulary -- scheduled_review,
    major_shock, objective_abandoned, milestone_reached, commitment_cost, first_plan.
    """
    before = plan_of(conn, world_id, nation_id)
    row_id = strategy_id_for(nation_id)
    payload = {
        "world_id": world_id,
        "nation_id": nation_id,
        "objective": plan.objective,
        "because": plan.because[:400],
        "adopted_on": date if plan.objective != before.objective else before.adopted_on,
        "adopted_turn": turn if plan.objective != before.objective else before.adopted_turn,
        "reviewed_on": date,
        "milestones": json.dumps(plan.milestones[:8]),
        "budget_posture": plan.budget_posture[:40],
        "mobilisation_posture": max(0, min(3, int(plan.mobilisation_posture))),
        "red_lines": json.dumps(plan.red_lines[:6]),
        "war_aims": json.dumps(plan.war_aims[:6]),
        "minimum_peace": json.dumps(plan.minimum_peace[:6]),
        "replan_on": json.dumps(plan.replan_on[:6]),
        "contender": plan.contender,
        "contender_streak": int(plan.contender_streak),
        # What this government looked like today, so its next review has a trend to read.
        "seen_on": date,
        "seen_ground": float((seen or {}).get("ground", 0.0)),
        "seen_ratio": float((seen or {}).get("ratio", 0.0)),
        "seen_index": float((seen or {}).get("index", 0.0)),
    }

    event = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,)).fetchone()
    if event is None:
        return False
    event_id = int(event["id"])

    exists = conn.execute(
        "SELECT 1 FROM nation_strategy WHERE id = ?", (row_id,)).fetchone() is not None
    diffs: list[Diff] = []
    if exists:
        diffs += [
            Diff("nation_strategy", row_id, column, DiffOp.SET, value,
                 reason_event_id=event_id, visibility_scope=Visibility.SECRET)
            for column, value in payload.items()
            if column not in {"world_id", "nation_id"}
        ]
    else:
        diffs.append(
            Diff("nation_strategy", row_id, "__insert__", DiffOp.INSERT, payload,
                 reason_event_id=event_id, visibility_scope=Visibility.SECRET))

    # The revision goes in the same batch as the change it describes, so a rewind cannot
    # leave a record of a replan that no longer happened.
    seq = conn.execute(
        "SELECT COUNT(*) AS n FROM strategy_revision WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id)).fetchone()["n"]
    diffs.append(
        Diff("strategy_revision", f"sr_{nation_id}_{seq}", "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id,
                 "nation_id": nation_id,
                 "at_turn": turn,
                 "on_date": date,
                 "trigger": trigger[:40],
                 "from_objective": before.objective,
                 "to_objective": plan.objective,
                 "note": note[:400],
             },
             reason_event_id=event_id, visibility_scope=Visibility.SECRET))

    outcome = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(diffs)))
    return bool(outcome.applied)


def review_world(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str,
) -> list[str]:
    """Give every government that is owed a review one, and record what it decided.

    **This is what makes the layer persistent rather than advisory.** A power keeps the plan
    it has until a review comes due or a shock forces one; between those it is simply
    committed, and everything it is shown is framed by that commitment.

    **And a shock really does force one.** The clock and `replan_trigger` are asked
    separately and a government reviews when either answers yes -- see `replan_trigger`
    for what the second one reads and why each condition is edge-triggered.

    Returns one line per government that changed its mind, for the report.
    """
    changed: list[str] = []
    raw = read_raw(conn, world_id, date=date, turn=turn)
    # Two whole-world reads rather than one per nation, the same argument `read_raw` makes
    # about 148 of everything: the ground each power holds now, and the last thing recorded
    # about why each one changed its mind.
    ground_now = _footprints(conn, world_id)
    last_trigger = {
        str(r["nation_id"]): str(r["trigger"] or "") for r in conn.execute(
            "SELECT nation_id, trigger FROM strategy_revision"
            "  WHERE world_id = ? ORDER BY nation_id, at_turn, id", (world_id,))}
    for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,)):
        nation = str(row["id"])
        plan = plan_of(conn, world_id, nation)
        forced = replan_trigger(
            plan, ground=ground_now.get(nation, 0.0),
            level=raw.get(nation, {}).get(plan.objective, 0.0),
            last_trigger=last_trigger.get(nation, ""))
        if not forced and not due_for_review(plan, date=date):
            continue
        fit = fitness_of(raw, nation)
        chosen, contender, streak = select(
            fit.tilt, fit.level, held=plan.objective, streak=plan.contender_streak,
            contender=plan.contender)
        was = plan.objective
        plan.objective = chosen
        plan.contender = contender
        plan.contender_streak = streak
        # What is recorded is *why the government looked*, which is the question
        # `strategy_revision` exists to answer. A forced review outranks the calendar's
        # label -- "objective_abandoned" describes the outcome and the shock describes the
        # cause, and an audit that only ever sees the outcome cannot tell the two apart.
        trigger = ("first_plan" if not plan.reviewed_on
                   else forced or ("objective_abandoned" if was and not chosen
                                   else "scheduled_review"))
        seen = readings_now(conn, world_id, nation, date=date, turn=turn)
        if commit_plan(conn, world_id, nation_id=nation, plan=plan, trigger=trigger,
                       turn=turn, date=date,
                       note=f"level {fit.level.get(chosen or was or '', 0.0):.2f}",
                       seen=seen) and was != chosen:
            changed.append(f"{nation}: {was or 'nothing'} -> {chosen or 'nothing'}")
    return changed


def revisions_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[dict]:
    """Every time this government changed its mind, and what made it."""
    return [
        {
            "turn": int(row["at_turn"]), "date": str(row["on_date"]),
            "trigger": str(row["trigger"]),
            "from": str(row["from_objective"]), "to": str(row["to_objective"]),
            "note": str(row["note"]),
        }
        for row in conn.execute(
            "SELECT * FROM strategy_revision WHERE world_id = ? AND nation_id = ?"
            " ORDER BY at_turn, id", (world_id, nation_id))
    ]


# --------------------------------------------------------------------- fitness
#
# **No victory threshold is read below this line.** Not `_DOMINATION_SHARE`, not
# `_CONCERT_MARGIN`, not `_HEGEMONY_MULTIPLE`. Each route asks whether a power is *becoming*
# the sort of power it suits, which is a question about trajectory and position. Reading a
# threshold instead was measured and is the hazard this design exists to avoid: ranked by
# plain distance-to-threshold, hegemony looks closest for every power on the map, and it is
# the one route the arithmetic cannot deliver.

#: How many observations a trend looks back over.
WINDOW = 6

# **The ground trend reads an ABSOLUTE value, and the first version read a share.**
#
# `ground_share` is a nation's ground over the world's total, and that total *shrinks* as
# occupation spreads -- occupied ground is discounted, so every province anybody takes
# lowers the denominator for everybody. Measured over 48 turns: a nation standing perfectly
# still saw its share drift upward and read it as conquest going well, and committed plans
# across 204 powers went from 3% conquest at turn one to **49%** by turn forty-eight, with
# the count above the level floor rising from 9 to 98. The signal was measuring the
# denominator.
#
# `budget.ground_value` is the numerator on its own -- what this nation's ground is worth to
# it, with occupation already discounted at `_OCCUPIED_YIELD`. A rate built from it needs no
# denominator at all, so nothing anybody else does can move it.
#
# **Not the change in provinces OWNED, which was the obvious alternative and is worse.**
# Ownership does not currently change: every province a campaign takes is *occupied*, and
# the measurement that found this defect also found that a committed conqueror ends 48 turns
# with exactly the 14 provinces it started with and 5 more under occupation. An
# owned-province delta would therefore be zero for a power in the middle of a successful war
# -- it would hide genuine campaigning, which is the opposite failure and the harder one to
# notice.


def _trend(series: list[float]) -> float:
    if len(series) < 2:
        return 0.0
    first, last = series[0], series[-1]
    if first <= 0.0:
        return 1.0 if last > 0.0 else 0.0
    return (last - first) / first / (len(series) - 1)


def _squash(value: float, scale: float) -> float:
    return 0.0 if value <= 0.0 else min(1.0, value / scale)


def midrank(values: list[float], mine: float) -> float:
    """Where ``mine`` sits among ``values``, counting ties at their midpoint.

    **Ties count as half, and the shipped map is why.** A hundred and forty minors sit at
    exactly zero on conquest. Counting only what is strictly below puts every one of them at
    the 0th percentile *and a great power that is also at zero at the 0th too* -- so a route
    the whole field is dead on reads as one this power is uniquely bad at, and the tilt then
    pushes it elsewhere for no reason at all.
    """
    if not values:
        return 0.0
    below = sum(1 for v in values if v < mine)
    equal = sum(1 for v in values if v == mine)
    return (below + equal / 2.0) / len(values)


def read_raw(
    conn: sqlite3.Connection, world_id: str, *, date: str, turn: int
) -> dict[str, dict[str, float]]:
    """Every living power's raw fitness for every objective, in one pass over the world.

    **One pass, because this is 148 nations and a per-nation query would be 148 of
    everything.** The field's median leadership and the score table are read once and shared.

    The trend terms compare against what each government looked like at its last review,
    which is stored on its own plan -- see the `seen_*` columns. A power that has never been
    reviewed has no trend and reads as flat, which is the honest answer rather than a
    flattering one.
    """
    from .concert import leadership_index
    from .scoring import standings

    table = standings(conn, world_id)
    by_total = sorted(table.scores, key=lambda s: -s.total)
    runner = by_total[1].total if len(by_total) > 1 else 0.0
    footprint = _footprints(conn, world_id)
    strength = {
        str(r["id"]): float(r["military_strength"]) for r in conn.execute(
            "SELECT id, military_strength FROM nation WHERE world_id = ?", (world_id,))}
    settled = {
        str(r["id"]): float(r["stability"]) for r in conn.execute(
            "SELECT id, stability FROM nation WHERE world_id = ?", (world_id,))}
    at_war = {
        str(r["nation_a"]) for r in conn.execute(
            "SELECT DISTINCT nation_a FROM relation WHERE world_id = ? AND at_war = 1",
            (world_id,))}
    indices = {
        s.nation_id: leadership_index(conn, world_id, s.nation_id, date=date, turn=turn)
        for s in table.scores}
    ordered = sorted(indices.values())
    median_index = ordered[len(ordered) // 2] if ordered else 0.0
    neighbours = _neighbour_edges(conn, world_id, strength)
    seen = {
        str(r["nation_id"]): r for r in conn.execute(
            "SELECT nation_id, seen_on, seen_ground, seen_ratio, seen_index"
            "  FROM nation_strategy WHERE world_id = ?", (world_id,))}

    out: dict[str, dict[str, float]] = {}
    for score in table.scores:
        nation = score.nation_id
        ground = footprint.get(nation, 0.0)
        ratio = (score.total / runner) if runner else 0.0
        index = indices.get(nation, 0.0)
        was = seen.get(nation)
        months = 1.0
        if was is not None and str(was["seen_on"]):
            gap = days_between(str(was["seen_on"]), date)
            months = max(1.0, (gap or 30.0) / 30.0)
        ground_trend = _rate(float(was["seen_ground"]) if was else 0.0, ground, months)
        ratio_trend = _rate(float(was["seen_ratio"]) if was else 0.0, ratio, months)
        index_trend = _rate(float(was["seen_index"]) if was else 0.0, index, months)

        edge = neighbours.get(nation, (0.0, 0.0))
        conquest = (
            0.55 * _squash(edge[0], 1.0)
            + 0.20 * min(1.0, edge[1] / 3.0)
            + 0.25 * _squash(ground_trend, 0.02)
        ) if edge[1] > 0 else 0.0
        preeminence = (
            0.45 * _squash(ratio_trend, 0.01)
            + 0.35 * min(1.0, max(0.0, (score.total / 0.5) - 1.0))
            + 0.20 * _squash(ground_trend, 0.02)
        )
        # **Standing clear of the field is most of this, and the first weighting had it as
        # a third.** Being peaceful and stable is necessary and nowhere near sufficient:
        # with those two worth 0.45 between them, every settled minor on the map cleared
        # the floor and 196 of 204 nations committed to leading the international order.
        # A power the order does not listen to is not pursuing this route, whatever its
        # domestic arrangements. So the term that discriminates carries the weight, and the
        # rest support it.
        concert = (
            0.55 * max(0.0, min(1.0, (index - median_index) / 0.25))
            + 0.15 * max(0.0, min(1.0, settled.get(nation, 0.0) / 80.0))
            + 0.10 * (0.0 if nation in at_war else 1.0)
            + 0.20 * _squash(index_trend, 0.01)
        )
        out[nation] = {"conquest": conquest, "preeminence": preeminence,
                       "concert": concert}
    return out


def readings_now(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str, turn: int
) -> dict[str, float]:
    """The three raw observations a plan stores so its next review has a trend."""
    from .concert import leadership_index
    from .scoring import standings

    table = standings(conn, world_id)
    by_total = sorted(table.scores, key=lambda s: -s.total)
    runner = by_total[1].total if len(by_total) > 1 else 0.0
    mine = next((s for s in table.scores if s.nation_id == nation_id), None)
    return {
        "ground": _footprints(conn, world_id).get(nation_id, 0.0),
        "ratio": (mine.total / runner) if (mine and runner) else 0.0,
        "index": leadership_index(conn, world_id, nation_id, date=date, turn=turn),
    }


#: What an occupied province counts for against one a nation owns. The same discount
#: `budget._OCCUPIED_YIELD` applies to what ground is worth, kept in step deliberately: a
#: campaign that has taken five provinces and annexed none has done something, and less than
#: it would have done by keeping them.
_OCCUPIED_WEIGHT = 0.4


def _footprints(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """How much ground each power holds, as a COUNT of provinces rather than a value.

    **Two measures were tried before this one and both drifted.**
    `ground_share` is a share of a world total that *shrinks* as occupation spreads, so a
    nation standing still watched its share rise: 3% of the field committed to conquest at
    turn one became 49% by turn forty-eight. Replacing it with `budget.ground_value` --
    absolute, no denominator -- was worse, 66%, because that sums `region.development` and
    development *inflates* as economies grow. Every power's ground was worth more each year
    whether or not it had taken any.
    """
    counts: dict[str, float] = {}
    for row in conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder,"
        "       owner_nation_id AS owner, COUNT(*) AS n"
        "  FROM region WHERE world_id = ? AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) IS NOT NULL"
        " GROUP BY holder, owner", (world_id,)):
        holder = str(row["holder"])
        weight = 1.0 if str(row["owner"]) == holder else _OCCUPIED_WEIGHT
        counts[holder] = counts.get(holder, 0.0) + float(row["n"]) * weight
    return counts


def _rate(before: float, now: float, months: float) -> float:
    """Change per reference month, as a fraction of where it started."""
    if before <= 0.0:
        return 0.0
    return (now - before) / before / max(1.0, months)


def _neighbour_edges(
    conn: sqlite3.Connection, world_id: str, strength: dict[str, float]
) -> dict[str, tuple[float, float]]:
    """``nation -> (edge over its strongest land neighbour, how many it has)``.

    **The strongest neighbour, not the median, and the first draft got that wrong.**
    Measured against the median every great power scores an edge of five to eighteen --
    each borders several tiny states -- so conquest fitness saturated for the whole field
    and discriminated nothing. Against the strongest it reads as it should: the United
    States +4.00, China +0.22, Russia -0.18 and India -0.28 (both border China).
    """
    pairs: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT ra.owner_nation_id AS a, rb.owner_nation_id AS b"
        "  FROM region_adjacency adj"
        "  JOIN region ra ON ra.id = adj.region_a"
        "  JOIN region rb ON rb.id = adj.region_b"
        " WHERE adj.world_id = ? AND adj.crossing = 'land'"
        "   AND ra.owner_nation_id IS NOT NULL AND rb.owner_nation_id IS NOT NULL"
        "   AND ra.owner_nation_id <> rb.owner_nation_id", (world_id,)):
        pairs.setdefault(str(row["a"]), set()).add(str(row["b"]))
        pairs.setdefault(str(row["b"]), set()).add(str(row["a"]))
    out: dict[str, tuple[float, float]] = {}
    for nation, others in pairs.items():
        strongest = max((strength.get(o, 0.0) for o in others), default=0.0) or 0.01
        out[nation] = ((strength.get(nation, 0.0) / strongest) - 1.0, float(len(others)))
    return out


@dataclass(frozen=True, slots=True)
class Fitness:
    """What a nation's trajectory suits, raw and relative.

    ``level`` is how well this power does the route on its own terms; ``tilt`` is how much
    better it does it than the rest of the field does. Both are needed -- see `FLOOR`.
    """

    level: dict[str, float]
    tilt: dict[str, float]


def fitness_of(
    raw: dict[str, dict[str, float]], nation_id: str
) -> Fitness:
    """Turn every power's raw readings into one nation's level and tilt."""
    routes = tuple(o.id for o in OBJECTIVES)
    mine = raw.get(nation_id, {})
    pct = {}
    for route in routes:
        values = sorted(v.get(route, 0.0) for v in raw.values())
        pct[route] = midrank(values, mine.get(route, 0.0))
    mean = sum(pct.values()) / len(routes) if routes else 0.0
    return Fitness(
        level={r: round(mine.get(r, 0.0), 4) for r in routes},
        tilt={r: round(pct[r] - mean, 4) for r in routes},
    )
