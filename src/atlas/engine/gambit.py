"""Undertakings with an uncertain outcome, proposed by a model and adjudicated here.

Between the two channels that already exist there was a gap. :mod:`atlas.engine.freeform`
answers an order no mechanic covers: a model narrates, the country's feelings move a
little, and nothing is ever in doubt -- it reaches three columns of the actor's *own* row
and it always works. :mod:`atlas.sim.resolve` adjudicates, with a seeded draw against a
closed vocabulary of circumstances, but it only ever asks one question: who holds this
ground.

A **gambit** is the diagonal. A specific attempt at something -- a summit, a sting, a
bribe, an incitement -- aimed at *somebody else*, which costs a stake whether or not it
works, and which the engine rolls for. That is the whole of what it adds over freeform:
adjudication, a target, and a price.

**Every discipline here is freeform's, deliberately.** The model picks words and the engine
picks numbers; there is no numeric field anywhere in the schema. The effects come from a
closed vocabulary, checked against :data:`~atlas.state.permissions.ROLE_WRITABLE` for
:data:`~atlas.state.models.Role.GAMBIT` at the one point where an answer becomes a proposed
change. The batch goes in under ``Role.ENGINE``, which is why ``gambit`` is not among the
roles ``diff_batch`` accepts -- the same arrangement freeform and contests both have, and
the reason this needed no schema change and no migration.

**The stake is charged win or lose.** Without it a gambit is a free lottery ticket and the
correct play is to attempt one every turn forever. With it, attempting something is a
decision.

**Nothing here reaches ground, forces or war.** No verb moves territory, a unit, a contest
or `at_war`, and the reason is structural rather than instructional: those verbs do not
exist. A gambit shapes the world; it cannot conquer through narration.

**It is built as a frame, not a feature.** A kind is a row in :data:`KINDS` -- base odds, an
event kind to file it under, and what the world must look like for it to be attemptable at
all. A missile strike and a nuclear launch are both "a bounded, adjudicated, model-proposed
attempt with a stake and a seeded outcome", so both were meant to arrive later as *content*
here: another row, another requirement predicate, and effects that reach further than these
do.

**The first of those has arrived, and it is worth stating exactly how it differs.**
``nuclear_launch`` is a kind like any other -- it is proposed, it is refused before the roll
if the world does not permit it, it takes a stake, and a seeded draw decides what happens.
It is the one kind whose stake costs no money (see :data:`NO_TREASURY_STAKE`), because the
warhead was paid for when it was built and a treasury is empty exactly when a country is
losing.
What it does *not* do is let the proposer choose effect verbs. Its consequences are fixed,
capped and applied by :mod:`atlas.engine.nuclear`, which is what makes it safe for them to
reach columns :data:`~atlas.state.permissions.ROLE_WRITABLE` refuses to a gambit: no
wording of any proposal selects them, changes them or leaves one out. A staff decides
whether to fire and where. A warhead decides the rest.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.jsonschema import make_strict
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import rerecord
from .air import StrikeOutcome, strike
from .bounds import absorbable
from .causality import Refusal
from .nuclear import (
    WARHEADS_BY_STAKE,
    LaunchOutcome,
    launch,
    nuclear_rules_block,
)
from .untrusted import clip, sanitize

__all__ = [
    "EFFECTS",
    "EVIDENCE",
    "GAMBIT_SCHEMA",
    "KINDS",
    "MAX_EFFECTS",
    "MAX_GAMBITS_PER_TURN",
    "NO_TREASURY_STAKE",
    "STAKES",
    "GambitKind",
    "GambitOutcome",
    "adjudicate",
    "apply_gambit",
    "build_gambit_view",
    "gambit_rules_block",
]


@dataclass(frozen=True, slots=True)
class GambitKind:
    """One thing a nation can attempt. Base odds, where it is filed, and what it needs.

    ``requires`` names predicates checked against the world before the roll: a gambit that
    could not have been attempted is refused with the reason, not rolled for and failed.
    The set is small on purpose -- it is the extension point a launch or a strike would
    use, and an open-ended requirement language would be a second rules engine.
    """

    id: str
    name: str
    blurb: str
    base_odds: float
    event_kind: EventKind
    #: ``needs_target`` -- somebody to do it to. ``at_war`` -- only against an enemy.
    #: ``not_at_war`` -- only where there is still something to negotiate.
    #: ``needs_region`` -- names a piece of ground as well as a nation.
    #: ``has_arsenal`` -- the actor must hold warheads it can deliver.
    #: ``may_target_sea`` -- the named region may be water nobody has won, provided an
    #: enemy fleet is in it. Everything else that names a region needs ground under it.
    requires: tuple[str, ...] = ()
    #: Filed where the world cannot see it. The engine still writes the event; what hides
    #: it is its visibility, the same mechanism a secret research programme uses.
    covert: bool = False


#: The eight attemptable things. Each is filed under an event kind that already exists in
#: the closed CHECK enum, which is why none of them needed the event enum widened -- the
#: nuclear layer's schema change is a new table, not a new kind of event.
KINDS: tuple[GambitKind, ...] = (
    GambitKind(
        id="summit",
        name="A summit",
        blurb="Meet, in public, and try to leave with something.",
        base_odds=0.55,
        event_kind=EventKind.DIPLOMATIC_MESSAGE,
        requires=("needs_target",),
    ),
    GambitKind(
        id="envoy",
        name="An envoy",
        blurb="Send somebody trusted to say what cannot be put in a note.",
        base_odds=0.60,
        event_kind=EventKind.ENVOY_DISPATCHED,
        requires=("needs_target",),
    ),
    GambitKind(
        id="bribe",
        name="Buy a minister",
        blurb="Somebody in their government has debts. Settle them.",
        base_odds=0.50,
        event_kind=EventKind.ESPIONAGE,
        requires=("needs_target",),
        covert=True,
    ),
    GambitKind(
        id="expose",
        name="Expose them",
        blurb="Put what you know in front of the people who did not know it.",
        base_odds=0.45,
        event_kind=EventKind.ESPIONAGE,
        requires=("needs_target",),
    ),
    GambitKind(
        id="sting",
        name="A counterintelligence sting",
        blurb="Let them steal something, and watch who carries it home.",
        base_odds=0.40,
        event_kind=EventKind.ESPIONAGE,
        requires=("needs_target",),
        covert=True,
    ),
    GambitKind(
        id="espionage",
        name="Send your people in",
        blurb="Go and find out what they are not saying.",
        # Unused: what an attempt is worth is `intelligence.discovery_odds`, off the network
        # this nation actually has there. Kept neutral so nothing reads it by accident.
        base_odds=0.0,
        event_kind=EventKind.ESPIONAGE,
        requires=("needs_target",),
        covert=True,
    ),
    GambitKind(
        id="counterintelligence",
        name="A security sweep",
        blurb="Find their people in your own country, and make them useless.",
        # Unused for the same reason: a sweep does not roll. It buys a fixed amount of
        # counterintelligence and the engine decides what that is worth.
        base_odds=0.0,
        event_kind=EventKind.ESPIONAGE,
        requires=("needs_target",),
    ),
    GambitKind(
        id="sabotage",
        name="Sabotage",
        blurb="A fire in the right building, and nobody signs for it.",
        base_odds=0.35,
        event_kind=EventKind.SABOTAGE,
        requires=("needs_target", "at_war"),
        covert=True,
    ),
    GambitKind(
        id="incite",
        name="Incite unrest",
        blurb="Find the people who already wanted to, and pay for the printing.",
        base_odds=0.30,
        event_kind=EventKind.UNREST,
        requires=("needs_target", "at_war"),
        covert=True,
    ),
    GambitKind(
        id="nuclear_launch",
        name="A nuclear strike",
        # Short and flat on purpose. Every other blurb is written to make a kind sound
        # like something worth trying; this one is written to sound like what it is.
        blurb="Fire a nuclear weapon at a named region.",
        # Unused: a launch's odds are the chance of getting *through*, which
        # `nuclear.penetration_odds` computes from the delivery tier against what the
        # target has overhead. Kept at the neutral value so nothing reads it by accident.
        base_odds=0.0,
        event_kind=EventKind.BATTLE,
        requires=("needs_target", "at_war", "needs_region", "has_arsenal"),
    ),
    GambitKind(
        id="air_strike",
        name="An air strike",
        blurb=(
            "Strike a region within reach of your airfields, without occupying it. Aim "
            "it at `forces` or at `infrastructure`. It can be intercepted."
        ),
        # Unused, like a launch's: what a strike risks is being shot down, and that is
        # computed in `air.strike` from what the defender has overhead and what air
        # defence it has researched.
        base_odds=0.0,
        event_kind=EventKind.BATTLE,
        # **`may_target_sea` is the datum, and both the gate and the briefing read it.**
        # Written as a requirement rather than as an `id == "air_strike"` test in two
        # places, for the decision-50 reason this module keeps invoking: a briefing that
        # quietly disagrees with the engine is worse than one that says nothing. A kind
        # that gains or loses the ability to reach water changes here and nowhere else.
        requires=(
            "needs_target", "at_war", "needs_region", "in_air_range", "may_target_sea",
        ),
    ),
)

#: What a stake is worth: the size of what it can move, and what it costs to attempt.
#:
#: The cost is charged **win or lose**, which is the single rule that stops a gambit being
#: a free roll. The magnitudes are close to freeform's -- a gambit is not the efficient
#: route to a number, it is the route to a number you might not get.
#:
#: **Both figures are per month, and both scale with the turn's span.** They were flat per
#: *attempt*, which made intrigue depend on how the turns were sliced rather than on how
#: much time passed: at most two attempts a turn at a flat 6 is 51.4 of scheming a month
#: on weekly turns and 0.99 a month on yearly ones -- a 52x swing in how much a game
#: contains, for no reason a player could see. A month is the identity point, so the
#: monthly-tuned balance every other constant was calibrated against is untouched.
STAKES: dict[str, tuple[float, float]] = {
    #  name        magnitude  treasury cost
    "minor": (1.5, 6.0),
    "serious": (3.0, 16.0),
    "grave": (4.5, 34.0),
}

#: Circumstances a model may report, and what each is worth. The same arrangement as
#: :class:`~atlas.sim.resolve.EvidenceFlag`: the model observes, this decides. Visible,
#: tunable and auditable -- unlike a number a model invented.
EVIDENCE: dict[str, float] = {
    "long_prepared": +0.12,
    "local_sympathisers": +0.15,
    "target_distracted": +0.12,
    "well_funded": +0.10,
    "rushed": -0.15,
    "target_vigilant": -0.18,
    "hostile_ground": -0.12,
}

#: The closed effect vocabulary. Every verb names a column the engine already tracks and
#: already lets somebody move, and the sign is fixed by the verb rather than chosen.
#:
#: ``self_only`` verbs move the actor's own row; the rest move the target's. There is no
#: verb for enriching yourself, taking ground, raising a formation or changing war status:
#: a gambit can make a country poorer, shakier, less respected or better disposed, and
#: nothing else at all.
EFFECTS: dict[str, tuple[str, str, float, bool]] = {
    #  verb            table       column        sign   self?
    "warm": ("relation", "score", +1.0, False),
    "cool": ("relation", "score", -1.0, False),
    "shake": ("nation", "stability", -1.0, False),
    "steady": ("nation", "stability", +1.0, True),
    "discredit": ("nation", "legitimacy", -1.0, False),
    "bolster": ("nation", "legitimacy", +1.0, True),
    "drain": ("nation", "treasury", -1.0, False),
}

#: How many effects one gambit may carry. Three is enough for "they are humiliated, poorer
#: and warier of you" and not enough for a policy.
MAX_EFFECTS = 3

#: **Kinds you do not pay cash to attempt, because you paid when you built them.**
#:
#: The stake is the rule that stops a gambit being a free lottery ticket, and it is right
#: for every kind that buys an *attempt*: a summit, a bribe, a sting. A launch is not an
#: attempt to acquire something. The warhead exists, it was paid for, and firing it is the
#: one decision in this game a government makes when it has nothing left.
#:
#: **Charging for it inverted the whole deterrent.** A play-test annihilated Pakistan --
#: eight provinces to zero in twelve months, arsenal intact at eight warheads -- and the
#: launch was refused at every stake with the same sentence: "costs 6 and you have 0". A
#: treasury is empty exactly when a country is losing, and losing is exactly what a
#: deterrent is for, so the gate made nuclear weapons usable only by powers that did not
#: need them. Given a treasury of 40 the same Pakistan launched and was answered, which is
#: how we know the machinery was never the problem.
#:
#: The stake's *magnitude* still applies: it is what `WARHEADS_BY_STAKE` reads to decide
#: how many warheads leave the silos, so `grave` is still a bigger decision than `minor`.
#: Only the cash changes hands, and only because it already did.
NO_TREASURY_STAKE: frozenset[str] = frozenset({"nuclear_launch"})

#: How many nations get to attempt something in a turn, across the whole world.
#:
#: This is a *cost* lever before it is a balance one. A gambit is a second model call
#: on top of the tracked-agent call, so an unbounded version would double the price of
#: a jump. Two is enough for the world to be visibly up to something and few enough
#: that the Focus cap still means what it says.
MAX_GAMBITS_PER_TURN = 2

#: A partial success is worth this much of a full one. High enough to be worth having,
#: low enough that "nearly" is not "yes".
PARTIAL_SHARE = 0.5

#: Odds are clamped into this band however good the circumstances. Nothing is certain and
#: nothing is hopeless -- the same reason `compute_pressure` clamps.
_ODDS_FLOOR, _ODDS_CEILING = 0.05, 0.95


_GAMBIT_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attempt", "refused"],
    "properties": {
        "attempt": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["kind", "target", "target_region", "aim", "intent", "stakes",
                         "evidence", "on_success", "on_failure"],
            "properties": {
                "kind": {"type": "string", "enum": sorted(k.id for k in KINDS)},
                "target": {
                    "type": ["string", "null"],
                    "description": "The nation id this is aimed at.",
                },
                "target_region": {
                    "type": ["string", "null"],
                    "description": (
                        "The region id this is aimed at, for the kinds that name ground. "
                        "Null for everything else."
                    ),
                },
                "aim": {
                    "type": ["string", "null"],
                    "description": (
                        "For `air_strike` only: `forces` to hit the formations in the "
                        "region, `infrastructure` to hit what is being built there and "
                        "the value of the province. Null for everything else."
                    ),
                },
                "intent": {
                    "type": "string",
                    "description": "One sentence, in world. What you are trying to do.",
                },
                "stakes": {"type": "string", "enum": sorted(STAKES)},
                "evidence": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "enum": sorted(EVIDENCE)},
                    "description": (
                        "Circumstances that make this more or less likely. Report what is "
                        "true; the engine decides what each is worth."
                    ),
                },
                "on_success": {
                    "type": "array",
                    "maxItems": MAX_EFFECTS,
                    "items": {"type": "string", "enum": sorted(EFFECTS)},
                },
                "on_failure": {
                    "type": "array",
                    "maxItems": MAX_EFFECTS,
                    "items": {"type": "string", "enum": sorted(EFFECTS)},
                    "description": "What it costs you if it goes wrong. Usually `cool`.",
                },
            },
        },
        "refused": {
            "type": ["string", "null"],
            "description": (
                "Set instead of `attempt` when there is nothing worth attempting this "
                "turn. That is the normal answer and a good one."
            ),
        },
    },
}

#: Written to the strictest dialect any provider requires; see atlas.state.jsonschema.
GAMBIT_SCHEMA: dict[str, Any] = make_strict(_GAMBIT_SCHEMA_RAW)


@dataclass(slots=True)
class GambitOutcome:
    kind: str = ""
    target: str | None = None
    #: ``success`` | ``partial`` | ``failure``. Empty when nothing was attempted.
    result: str = ""
    odds: float = 0.0
    narrative: str = ""
    refused: str | None = None
    applied: int = 0
    cost: float = 0.0
    #: Effects the caps or the allowlist would not carry, each with the reason.
    trimmed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: Set only by a kind the engine resolves itself. Carries what the engine did, so a
    #: turn report can say so without re-deriving it.
    launch: LaunchOutcome | None = None
    strike: StrikeOutcome | None = None
    #: The event the undertaking was recorded as, so a report quoting it can be held to
    #: that event's scope: a covert undertaking that worked is SECRET to its actor.
    event_id: int | None = None
    #: The breach line, when this act went through a ceasefire and broke it. See
    #: :func:`_through_a_truce`.
    truce_broken: str | None = None

    @property
    def happened(self) -> bool:
        return bool(self.result)


def kind_by_id(kind_id: str) -> GambitKind | None:
    return next((k for k in KINDS if k.id == kind_id), None)


def _apply_nuclear_launch(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    kind: GambitKind,
    outcome: GambitOutcome,
    actor: str,
    target: str | None,
    region: str | None,
    stake_name: str,
    cost: float,
    intent: str,
    turn: int,
    date: str,
    seed: int,
    index: int,
) -> GambitOutcome:
    """Hand a checked, paid-for launch to the engine and report what came back.

    Everything uncertain about it -- whether it is intercepted, what it destroys, how much
    of the target's arsenal it catches on the ground, and whether the reply comes -- is
    decided in :mod:`atlas.engine.nuclear`. Nothing the proposer wrote reaches any of it
    beyond *where* and *how many*, which is the point.
    """
    result = launch(
        conn, world_id, launcher=actor, target=str(target), region_id=str(region),
        warheads=WARHEADS_BY_STAKE.get(stake_name, 1), turn=turn, date=date, seed=seed,
        index=index, stake_treasury=cost,
    )
    outcome.kind, outcome.target, outcome.odds, outcome.cost = (
        kind.id, target, result.odds, cost
    )
    outcome.problems.extend(result.problems)
    outcome.trimmed.extend(result.notes)
    outcome.launch = result

    if result.refused is not None:
        outcome.refused = result.refused
        return outcome

    # Mapped onto the channel's three words so the turn report, the ledger and every
    # existing caller read a launch the same way they read a summit.
    outcome.result = {
        "struck": "success", "degraded": "partial", "intercepted": "failure",
    }.get(result.result, "failure")
    outcome.applied = result.applied
    told = clip(sanitize(intent), 300)
    outcome.narrative = (
        f"{result.narrative}{' -- ' + told if told else ''}"
        + (f"; {result.retaliation.narrative}" if result.retaliation else "")
    )
    return outcome


def _apply_air_strike(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    kind: GambitKind,
    outcome: GambitOutcome,
    actor: str,
    target: str | None,
    region: str | None,
    stake_name: str,
    cost: float,
    intent: str,
    turn: int,
    date: str,
    seed: int,
    index: int,
    aim: str = "",
) -> GambitOutcome:
    """Hand a checked, paid-for strike to the air layer and report what came back.

    The proposer chose a region and one of two words. Everything else -- whether it gets
    through, what it degrades, by how much, and what it loses on the way -- is decided in
    :mod:`atlas.engine.air` against caps written there. The stake buys the attempt, not
    the magnitude: a `grave` strike is not a heavier strike, which is why `stake_name` is
    read for nothing but its price.
    """
    result = strike(
        conn, world_id, attacker=actor, region_id=str(region),
        aim=aim or "forces", turn=turn, date=date, seed=seed, index=index,
    )
    outcome.kind, outcome.target, outcome.odds, outcome.cost = (
        kind.id, target, result.odds, cost
    )
    outcome.problems.extend(result.problems)
    outcome.strike = result

    if result.refused is not None:
        outcome.refused = result.refused
        return outcome

    outcome.result = {
        "struck": "success", "degraded": "partial", "intercepted": "failure",
    }.get(result.result, "failure")
    outcome.narrative = (
        f"{result.region_name}: {result.result}"
        + (" -- " + "; ".join(result.effects) if result.effects else "")
    )
    outcome.applied = len(result.effects)
    return outcome


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def _charge_stake(  # noqa: PLR0913 - a charge is who, how much, why and how loudly
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, cost: float, turn: int,
    date: str, text: str, outcome: GambitOutcome,
) -> int | None:
    """Take the stake, and give the charge a cause to cite. ``None`` if it would not apply.

    **Charged first and regardless**, like every other undertaking: one you did not pay for
    is one you would always take. Scoped SECRET because what it pays for is -- both runners
    below write secret events, and rule INFO-002 refuses state more observable than its
    reason, which is how a covert gambit's public charge once inverted the whole mechanic.
    """
    # **`NARRATIVE`, not `ESPIONAGE`, and the reason is a real interaction.**
    # `run_espionage` offsets which secret an attempt looks at by how many espionage events
    # this actor has already written this turn -- that is what stops two attempts finding
    # the same paper. A bookkeeping event of that kind would be counted as a report, and
    # the first attempt of every turn would look past the only secret there was. Found by
    # the test below going from pass to fail the moment the charge was added.
    #
    # (The same collision exists between the counterintelligence order and the espionage
    # order in one turn, and predates this. Flagged, not changed here: fixing it moves
    # which secrets existing worlds find.)
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, actor_nation_id=nation_id, text=text,
        visibility=Visibility.SECRET, visible_to=(nation_id,))
    outcome.event_id = event_id
    applied = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(
                Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -round(cost, 2),
                     reason_event_id=event_id, visibility_scope=Visibility.SECRET),
            ),
        ),
    )
    if not applied.applied:
        outcome.problems.append(f"the stake could not be charged: {applied.reason}")
        return None
    conn.commit()
    return event_id


def _apply_espionage(  # noqa: PLR0913 - a bespoke effect is the whole gambit frame
    conn: sqlite3.Connection,
    world_id: str,
    *,
    kind: GambitKind,
    outcome: GambitOutcome,
    actor: str,
    target: str | None,
    region: str | None,
    stake_name: str,
    cost: float,
    intent: str,
    turn: int,
    date: str,
    seed: int,
    index: int,
) -> GambitOutcome:
    """Go and find something out, through the same runner the player's order uses.

    **This kind exists because the mechanic was reachable by one seat only.** Espionage and
    counterintelligence were built as *orders*, and orders are applied with
    ``nation_id=self._player_nation()`` -- so no autonomous nation could ever grow a
    network, run a discovery, or invest in the counterintelligence that makes deception
    possible. Deception in particular could only ever be staged by a fixture, never played.
    Caught by the pre-flight before the intelligence capstone run; this is the door.

    `intelligence.run_espionage` decides everything, exactly as it does for a typed order.
    What is added here is the gambit frame the other kinds already have: a stake, a cost
    charged win or lose, and a refusal the model can read.
    """
    del region, seed, index  # a service is aimed at a country, not at ground, and does not roll
    from .intelligence import run_espionage

    outcome.kind, outcome.target, outcome.cost = kind.id, target, cost
    if _charge_stake(
        conn, world_id, nation_id=actor, cost=cost, turn=turn, date=date,
        outcome=outcome,
        text=f"{_name(conn, actor)} sends its people into {_name(conn, target or '')}.",
    ) is None:
        outcome.result = "failure"
        return outcome

    report = run_espionage(
        conn, world_id, nation_id=actor, target=target or "", turn=turn, date=date)
    outcome.result = "success" if report.applied else "failure"
    outcome.applied = 1 if report.applied else 0
    outcome.problems.extend(report.problems)
    told = clip(sanitize(intent), 300)
    outcome.narrative = (
        "; ".join(report.headlines) if report.headlines
        else (report.problems[0] if report.problems else "the attempt came back empty")
    ) + (f" -- {told}" if told else "")
    return outcome


def _apply_counterintelligence(  # noqa: PLR0913 - a bespoke effect is the whole gambit frame
    conn: sqlite3.Connection,
    world_id: str,
    *,
    kind: GambitKind,
    outcome: GambitOutcome,
    actor: str,
    target: str | None,
    region: str | None,
    stake_name: str,
    cost: float,
    intent: str,
    turn: int,
    date: str,
    seed: int,
    index: int,
) -> GambitOutcome:
    """Blind one rival's service inside your own country -- the deception lever.

    Calls `intelligence.invest_counterintel`, which is the same function the player's
    `counterintelligence` order calls and the only place `intel_network.counter_intel`
    moves. Past `DECEPTION_AT` it stops merely blinding the rival and starts choosing what
    the rival believes, which is the thing this whole layer is for and which no model could
    reach until now.

    **Not rolled.** A sweep is work rather than a gamble: it buys `COUNTER_PER_TURN` and
    the engine decides what that is worth. The stake still costs what it costs, so a
    government that wants its counterintelligence badly pays for it at the same exchange
    rate as everything else in this table.
    """
    del region, seed, index
    from .intelligence import invest_counterintel

    outcome.kind, outcome.target, outcome.cost = kind.id, target, cost
    if not target or target == actor:
        outcome.refused = "a security service has to be told whose people to look for"
        return outcome
    if _charge_stake(
        conn, world_id, nation_id=actor, cost=cost, turn=turn, date=date, outcome=outcome,
        text=f"{_name(conn, actor)} orders a sweep against {_name(conn, target)}'s people.",
    ) is None:
        outcome.result = "failure"
        return outcome

    told = invest_counterintel(
        conn, world_id, nation_id=actor, against=target, turn=turn, date=date)
    outcome.result = "success" if told else "failure"
    outcome.applied = 1 if told else 0
    if not told:
        outcome.problems.append("there was nothing left to tighten")
    said = clip(sanitize(intent), 300)
    outcome.narrative = (told or "the sweep found nobody new") + (
        f" -- {said}" if said else "")
    return outcome


#: Kinds the engine resolves itself rather than from a proposer's effect verbs. The
#: dispatch existed for one entry and a design note naming two more; this is the fourth.
_BESPOKE = {
    "nuclear_launch": _apply_nuclear_launch,
    "air_strike": _apply_air_strike,
    "espionage": _apply_espionage,
    "counterintelligence": _apply_counterintelligence,
}


def _at_war_clause(kind: GambitKind) -> str:
    """The wartime-only line a kind's blurb carries, and -- once taped -- its truce cost.

    The ceasefire half is staged in :mod:`atlas.engine.rerecord` (``truce-breach-brief``):
    it is prompt text, so it waits for the next live re-record rather than reddening the
    active cassette. The mechanic it describes is live either way.
    """
    if not rerecord.staged_enabled():
        return " **Only against somebody you are at war with.**"
    return (" **Only against somebody you are at war with**; used on somebody you have "
            "a ceasefire with, it **breaks the ceasefire**"
            + (" if it is caught." if kind.covert else "."))


def gambit_rules_block(nation_id: str, span: float = 1.0) -> list[str]:
    """The vocabulary and every bound, for the briefing.

    Generated from the tables rather than restated beside them, for the reason decision 50
    records: a briefing that quietly disagrees with the engine is worse than one that says
    nothing, because the model then trusts it.
    """
    return [
        "You may attempt **one undertaking** whose outcome is not certain -- a summit, a "
        "bribe, a sting. It is rolled for. It can fail.",
        # The `requires` clause is generated, not written out beside the table, for the
        # decision-50 reason -- and it is *stated at all* because of PT23, where two of the
        # three proposals a real staff ever made were spent aiming a war-only kind at a
        # nation nobody was at war with. The blurbs described what each kind does and never
        # once said which were off the table in peacetime, so the models proposed in good
        # faith and were refused. An undisclosed bound costs a whole proposal: decision 11,
        # for the fourth time.
        "What you may attempt: "
        + "; ".join(
            f"`{k.id}` -- {k.blurb}"
            + (_at_war_clause(k) if "at_war" in k.requires else "")
            + (" **Only against somebody you are not already fighting.**"
               if "not_at_war" in k.requires else "")
            + (" **Name the region in `target_region`.**"
               if "needs_region" in k.requires else "")
            + (" A **sea an enemy fleet is sitting in** counts as a region; aim at "
               "`forces` to go after the ships."
               if "may_target_sea" in k.requires else "")
            + (" **Only if you hold warheads you can deliver.**"
               if "has_arsenal" in k.requires else "")
            for k in KINDS
        ),
        # Scaled by the span, because the engine charges the scaled figure. A briefing
        # that quotes 6 while the treasury is debited 18 is decision 50's exact failure:
        # a staff that trusts it budgets wrong and is refused for a number it was told.
        "Stakes and what each costs your treasury whether or not it works"
        + (f" (this turn covers {span:.2g} months, and a stake is priced by the month): "
           if abs(span - 1.0) > 0.01 else ": ")
        + ", ".join(f"`{name}` costs {cost * span:.0f}" for name, (_m, cost) in
                    sorted(STAKES.items(), key=lambda kv: kv[1][1])),
        "Circumstances you may report, which shift the odds: "
        + ", ".join(f"`{flag}`" for flag in sorted(EVIDENCE))
        + ". Report what is true -- claiming all of them does not help, and the engine "
        "decides what each is worth.",
        "What an outcome may do, and nothing else exists: "
        + ", ".join(f"`{verb}`" for verb in sorted(EFFECTS))
        + f". At most {MAX_EFFECTS} of them. `steady` and `bolster` act on you; the rest "
        "act on your target. **`nuclear_launch` is the one exception and it takes none of "
        "them** -- its effects are fixed by the engine and listed below if you have an "
        "arsenal; whatever you put in `on_success` and `on_failure` is ignored.",
        "You cannot take ground, move a formation, declare a war or enrich yourself this "
        "way. Those have their own orders, and no wording here reaches them.",
        f"You are `{nation_id}`. Attempting nothing is the normal answer -- set `refused` "
        "and say why. A nation that gambles every month is not being clever.",
    ]


def _prominence(other: Any) -> tuple[int, int, int, float]:
    """How much this nation bears on your affairs, for ordering the roster.

    A war outranks everything, then a shared border, then a treaty, and only then the
    strength of feeling either way -- `abs`, because somebody you loathe is exactly as
    worth a gambit as somebody you court. Ties fall back to nothing in particular, which
    is fine: past the first dozen the list is not shown.
    """
    return (
        1 if other.at_war else 0,
        1 if other.is_neighbour else 0,
        1 if other.treaty_status and other.treaty_status != "none" else 0,
        abs(float(other.relation_score or 0.0)),
    )


def build_gambit_view(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, span: float = 1.0
) -> str:
    """The prompt: who you are, who is around you, and what you may try."""
    from ..state import visibility

    # The clock, read directly rather than through the omniscient referee module. A
    # gambit is one nation deciding, not the referee looking at everything, and the
    # date is the only thing the referee would have been asked for -- which the player
    # already sees anyway. A test pins that `referee` is imported nowhere it should not
    # be, and widening that allowlist for a date would be the wrong trade.
    clock = conn.execute(
        "SELECT world_date, turn_number FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    self_block = visibility.nation_self(conn, nation_id)
    others = visibility.other_nations(conn, nation_id)

    treasury = self_block.get("treasury", 0.0)
    lines = [
        f"# You are {self_block.get('name', nation_id)} (`{nation_id}`)",
        self_block.get("character", ""),
        "",
        f"The date is {clock['world_date'] if clock else '?'} "
        f"(turn {clock['turn_number'] if clock else 0}).",
        f"Stability {self_block.get('stability')}, legitimacy "
        f"{self_block.get('legitimacy')}, treasury {treasury}.",
        "",
        "## Who you could aim at",
    ]
    # Ranked by whether they *matter to you*, never alphabetically. `other_nations`
    # returns the whole living world ordered by name, so taking the first twelve of a
    # 200-nation map offered Russia -- at war with Ukraine -- Andorra and American Samoa,
    # and never Ukraine. Every staff in PT23 declined and said so in as many words.
    # A lever nobody can reach is not a lever, which is the decision-11 lesson again.
    for other in sorted(others, key=_prominence, reverse=True)[:12]:
        why = []
        if other.at_war:
            why.append("at war with you")
        if other.is_neighbour:
            why.append("your neighbour")
        if other.treaty_status and other.treaty_status != "none":
            why.append(str(other.treaty_status))
        # Attribute access, not `getattr(..., default)`: the first cut of this line read
        # `other.score`, which does not exist on a NationBrief, so the default silently
        # served every model a regard of zero -- and six separate staffs reasoned from it
        # out loud before anybody noticed. A wrong number that never raises is worse than
        # a crash, so a rename should break this loudly now.
        lines.append(
            f"- **{other.name}** [`{other.nation_id}`]"
            f"{' (' + ', '.join(why) + ')' if why else ''}: "
            f"your regard for them is {other.relation_score}"
        )

    # The nuclear section, shown only to a power that has one. An unusable lever
    # described at length costs tokens and teaches nothing, and a staff reading about
    # warheads it does not own is a staff being invited to imagine it does.
    nuclear = nuclear_rules_block(conn, world_id, nation_id)
    if nuclear:
        lines += ["", "## Your nuclear arsenal, and what would happen if you used it"]
        lines += [f"- {rule}" for rule in nuclear]
        strikeable = _strikeable_regions(conn, world_id, nation_id)
        if strikeable:
            lines.append(
                "- Ground you could name in `target_region`: "
                + "; ".join(f"{name} [`{rid}`] held by {holder}"
                            for rid, name, holder in strikeable)
                + "."
            )

    lines += [
        "",
        "## Your task",
        "Decide whether there is something worth *attempting* this turn -- something with "
        "a real chance of going wrong, aimed at somebody else.",
        "",
        "Rules you cannot argue with:",
    ]
    lines += [f"- {rule}" for rule in gambit_rules_block(nation_id, span)]
    return "\n".join(lines)


#: How much enemy ground to name. Enough to make a real choice of target, few enough that
#: a briefing does not turn into a gazetteer.
_STRIKEABLE_SHOWN = 8


def _strikeable_regions(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[tuple[str, str, str]]:
    """Enemy-held land this nation could legally name, richest first.

    Generated from the same condition ``_requirements_met`` enforces, for the decision-50
    reason: a briefing that offers ground the engine would refuse is worse than one that
    offers none, because the staff then spends its proposal finding out.
    """
    rows = conn.execute(
        """
        SELECT r.id AS id, r.name AS name, n.name AS holder
          FROM relation rel
          JOIN region r ON COALESCE(r.controller_nation_id, r.owner_nation_id) = rel.nation_b
          JOIN nation n ON n.id = rel.nation_b
         WHERE rel.world_id = ? AND rel.nation_a = ? AND rel.at_war = 1
           AND r.world_id = ? AND r.kind <> 'ocean' AND n.is_alive = 1
         ORDER BY r.development DESC, r.population DESC, r.id ASC
         LIMIT ?
        """,
        (world_id, nation_id, world_id, _STRIKEABLE_SHOWN),
    ).fetchall()
    return [(str(r["id"]), str(r["name"]), str(r["holder"])) for r in rows]


def _open_water_refusal(  # noqa: PLR0913 - one call site, every argument load-bearing
    conn: sqlite3.Connection,
    world_id: str,
    kind: GambitKind,
    actor: str,
    target: str | None,
    region: str,
    aim: str,
) -> Refusal | None:
    """Whether this undertaking may name a stretch of sea nobody has won, and why not.

    **Only an air strike may, and only where there is a fleet to hit.** The gate's real
    purpose is intact and is three separate refusals rather than one:

    * a kind that needs ground still gets none -- a warhead aimed at open water has no
      population, no works and no province to destroy, and `nuclear_launch` is the only
      other kind that names a region at all;
    * `infrastructure` is still refused, because there is nothing built on a sea. Refused
      *here* rather than left to `air.strike`'s own guard so the order is turned down
      before its stake is reckoned;
    * an empty sea, or one holding nobody this power is fighting, is still refused. A
      strike at water hits ships; without ships it is an order to bomb the sea.

    **The last check runs through `air._fleet_present`, the same function the strike
    itself picks its victim with**, so the gate and the effect cannot come to disagree
    about whose fleet is in that water. On land the two agree structurally -- the gate
    demands ``holder == target`` and the strike reads the same holder -- and this keeps
    that property at sea instead of leaving it to two queries that happen to match today.
    A power naming the smaller of two enemy fleets is told which one a raid would find,
    which is more useful than accepting an order the effect would not carry out.
    """
    from .air import _fleet_present

    if "may_target_sea" not in kind.requires:
        return Refusal(
            "may_target_sea",
            f"{region} is open water; {kind.name} has to name ground.",
            "name a land region, or use `air_strike`, which is the one undertaking that "
            "reaches a fleet at sea.",
        )
    if aim == "infrastructure":
        return Refusal(
            "may_target_sea",
            f"{region} is open water; there is nothing built on it to bomb.",
            "aim at `forces` to go after the ships.",
        )

    present = _fleet_present(conn, world_id, actor, region)
    if present is None:
        return Refusal(
            "may_target_sea",
            f"there is nothing of {target}'s in {region} to strike; a raid on open water "
            "hits the fleet floating in it, and there is no fleet there.",
            "find where their fleet actually is, or strike ground they hold.",
        )
    if present != target:
        return Refusal(
            "may_target_sea",
            f"the fleet in {region} is {present}'s, not {target}'s; a raid on open water "
            "finds whichever squadron is actually in it.",
            f"name {present} as the target, or strike somewhere {target} actually is.",
        )
    return None


def _requirements_met(
    conn: sqlite3.Connection,
    world_id: str,
    kind: GambitKind,
    actor: str,
    target: str | None,
    region: str | None = None,
    aim: str = "",
) -> Refusal | None:
    """``None`` if it may be attempted, otherwise the reason it may not, and what to
    write instead where there is such a thing.

    Refused *before* the roll, so a gambit that could never have worked does not cost a
    stake and does not read as bad luck. ``aim`` is read for the one refusal that depends
    on it -- see :func:`_open_water_refusal` -- and *here* rather than inside
    ``air.strike`` so that an order which could never have worked is turned down before
    the stake is reckoned rather than after.
    """
    if "needs_target" in kind.requires:
        # The list is drawn by `alternatives`, which runs the real gate over the real map --
        # so nothing offered here is something the next call would turn down.
        from .alternatives import live_nations, phrase

        others = phrase(live_nations(conn, world_id, besides=actor), lead="aim it at")
        if not target:
            return Refusal("needs_target",
                           f"{kind.name} needs somebody to aim it at.", others)
        if target == actor:
            return Refusal("needs_target",
                           f"{kind.name} cannot be aimed at yourself.", others)
        row = conn.execute(
            "SELECT is_alive FROM nation WHERE world_id = ? AND id = ?",
            (world_id, target),
        ).fetchone()
        if row is None:
            return Refusal("needs_target",
                           f"there is no nation called {target!r} on this map.", others)
        if not row["is_alive"]:
            return Refusal("needs_target", f"{target} no longer exists.", others)

    if "at_war" in kind.requires or "not_at_war" in kind.requires:
        row = conn.execute(
            "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? "
            "AND nation_b = ?", (world_id, actor, target),
        ).fetchone()
        at_war = bool(row and row["at_war"])
        if "at_war" in kind.requires and not at_war:
            return Refusal(
                "at_war",
                f"{kind.name} is only open against somebody you are at war with.",
                "declare war first, or choose an undertaking that works short of war.",
            )
        if "not_at_war" in kind.requires and at_war:
            return Refusal(
                "not_at_war",
                f"{kind.name} is not open against somebody you are already fighting.",
                "against an enemy, the undertakings that stay open are the military ones.",
            )

    if "needs_region" in kind.requires:
        from .alternatives import phrase, reachable_regions

        ground = phrase(reachable_regions(conn, world_id, actor), lead="ground you can "
                        "reach includes")
        if not region:
            return Refusal("needs_region",
                           f"{kind.name} has to name the region it is aimed at.", ground)
        row = conn.execute(
            "SELECT kind, owner_nation_id, controller_nation_id FROM region"
            " WHERE world_id = ? AND id = ?", (world_id, region),
        ).fetchone()
        if row is None:
            return Refusal("needs_region",
                           f"there is no region called {region!r} on this map.", ground)
        holder = row["controller_nation_id"] or row["owner_nation_id"]
        if str(row["kind"]) == "ocean" and holder is None:
            # **Water nobody has won is the one target with no holder, and the gate used
            # to refuse it flat.** That refusal predates a strike being able to reach a
            # fleet at all, and it survived the fix that gave one -- so `air.strike` grew
            # the ability to hit ships at sea and no order could get to it, because the
            # requirement check turned every one of them down a layer earlier. A carrier
            # was dangerous only to code that called the air layer directly.
            problem = _open_water_refusal(
                conn, world_id, kind, actor, target, region, aim
            )
            if problem is not None:
                return problem
        # Aimed at ground the named enemy actually holds, or the target is decorative and
        # the strike is really against somebody else -- including, twice over, yourself.
        elif holder != target:
            return Refusal(
                "needs_region",
                f"{region} is not held by {target}; a strike names ground the nation you "
                "named is standing on.",
                (f"aim at ground {target} holds, or name {holder} as the target instead."
                 if holder else "aim at ground somebody actually holds."),
            )

    if "in_air_range" in kind.requires and region:
        from .air import in_air_range, projection, strike_range_of

        if not in_air_range(conn, world_id, actor, region):
            reach = strike_range_of(conn, world_id, actor)
            if not reach:
                return Refusal(
                    "in_air_range",
                    "you have no aircraft, so nothing you own can strike anywhere.",
                    "raise an air wing, or use an undertaking that does not fly.",
                )
            # **The refusal used to end here, and a staff read it as "try harder".** In the
            # recorded runs the same out-of-range strike came back turn after turn against
            # a message that named nothing reachable. `projection` is already computed for
            # the range test one line up, so the nearest thing this power *can* hit costs
            # a dictionary lookup rather than a second walk of the map.
            reachable = projection(conn, world_id, actor)
            held_by_target = [
                (weight, str(row["id"]))
                for row in conn.execute(
                    "SELECT id FROM region WHERE world_id = ?"
                    "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
                    (world_id, target or ""),
                )
                if (weight := reachable.get(str(row["id"]), 0.0)) > 0.0
            ]
            best = max(held_by_target, default=None)
            return Refusal(
                "in_air_range",
                f"{region} is out of range: your aircraft strike within {reach} region(s) "
                "of where they are based, and nothing you own is that close to it.",
                (f"aim at {best[1]}, which {target} holds and your aircraft can reach."
                 if best else
                 "move aircraft closer, take ground nearer the target, or station a "
                 "carrier within reach -- there is nothing of theirs in range today."),
            )

    if "has_arsenal" in kind.requires:
        from .nuclear import arsenal_of

        arsenal = arsenal_of(conn, world_id, actor)
        if arsenal is None or not arsenal.armed:
            from .alternatives import open_undertakings, phrase

            return Refusal(
                "has_arsenal",
                f"{kind.name} needs warheads and a means of delivering them; you have "
                "neither.",
                phrase(open_undertakings(conn, world_id, actor, target=target),
                       lead="what is open to you is"),
            )
    return None


def adjudicate(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    kind: GambitKind,
    actor: str,
    target: str | None,
    evidence: list[str],
    seed: int,
    turn: int,
    index: int = 0,
) -> tuple[str, float, float]:
    """Roll it. Returns ``(result, odds, roll)``.

    The same shape as :func:`atlas.sim.resolve.resolve_contest`: circumstances from a
    closed vocabulary shift a base, capability and resistance shift it further, the whole
    thing is clamped, and one seeded draw decides. The draw's key names the decision --
    who is attempting what against whom, and which attempt of the turn it is -- so adding a
    call elsewhere cannot shift it and two gambits in one turn do not resolve identically.
    """
    odds = kind.base_odds
    for flag in evidence:
        odds += EVIDENCE.get(flag, 0.0)

    # Capability and resistance, both small: a gambit is mostly about circumstance, and a
    # rich nation should not simply win every roll.
    actor_row = conn.execute(
        "SELECT economy, legitimacy FROM nation WHERE world_id = ? AND id = ?",
        (world_id, actor),
    ).fetchone()
    if actor_row:
        odds += min(0.12, float(actor_row["economy"]) / 800.0)
    if target:
        target_row = conn.execute(
            "SELECT stability, legitimacy FROM nation WHERE world_id = ? AND id = ?",
            (world_id, target),
        ).fetchone()
        if target_row:
            # A settled, well-regarded state is harder to bribe, incite or embarrass.
            resistance = (float(target_row["stability"]) + float(target_row["legitimacy"]))
            odds -= min(0.20, resistance / 1000.0)

    # **Appetite, not competence.** A dealmaker's summit goes better and a cautious
    # government's sting goes worse, because the trait describes how much of itself the
    # state is willing to put behind the attempt. It multiplies the shifted odds rather
    # than adding to the base, so it cannot turn a hopeless undertaking into a coin flip
    # -- and the clamp below still binds. See :mod:`atlas.engine.leaders`.
    from .leaders import modifier as leader_modifier

    odds *= leader_modifier(conn, actor, "gambit")

    odds = max(_ODDS_FLOOR, min(_ODDS_CEILING, odds))
    roll = derive(
        seed, world_id, turn, f"gambit:{kind.id}:{actor}->{target or '-'}#{index}"
    ).random()

    if roll < odds * PARTIAL_SHARE:
        return "success", odds, roll
    if roll < odds:
        return "partial", odds, roll
    return "failure", odds, roll


def _effect_to_diff(
    conn: sqlite3.Connection,
    verb: str,
    actor: str,
    target: str | None,
    magnitude: float,
    event_id: int,
    spent: dict[tuple[str, str], float],
    scope: Visibility,
    cap: float,
) -> tuple[Diff | None, str | None]:
    """Turn one verb into a bounded diff, or say why not."""
    from ..state.permissions import may_write

    entry = EFFECTS.get(verb)
    if entry is None:
        return None, f"unknown effect {verb!r}"
    table, column, sign, self_only = entry

    if self_only:
        subject = actor
    elif not target:
        return None, f"`{verb}` needs a target"
    else:
        subject = target

    row_id = f"{actor}|{target}" if table == "relation" else subject
    if table == "relation" and not target:
        return None, f"`{verb}` needs a target"

    # The allowlist, consulted rather than assumed. The batch goes in as ENGINE, so
    # nothing downstream would check it -- which is exactly why it is checked here, at the
    # one point where a model's answer becomes a proposed change.
    allowed, reason = may_write(Role.GAMBIT, table, column)
    if not allowed:
        return None, reason

    # The per-turn ceiling, shared with every other gambit *and* with freeform, so the two
    # channels cannot be combined to walk one column further than either allows alone.
    room = cap - spent.get((row_id, column), 0.0)
    if room <= 0:
        return None, f"{column} has already moved as far as one turn of this allows"

    # One magnitude, one ceiling, every column the same. An earlier version scaled a
    # relation point against a stability point, which sounded right and meant every
    # stake from `minor` upward saturated the per-turn cap -- so the stake stopped
    # differentiating anything, which is most of what a stake is for. A gambit's power
    # is that it reaches somebody else and reaches three things at once, not that it
    # moves a number further than freeform could.
    value = sign * min(magnitude, room)
    # ...and then against what the subject actually has, because a bounded column refused is
    # a whole batch refused. `shake` on a government already at zero stability is the case:
    # it cannot be shaken further, and saying so costs the gambit its effect rather than
    # costing the turn its batch. See :mod:`atlas.engine.bounds`.
    value = absorbable(conn, table, row_id, column, value)
    if not value:
        return None, f"{column} cannot move any further in that direction"
    spent[(row_id, column)] = spent.get((row_id, column), 0.0) + abs(value)
    return (
        Diff(table, row_id, column, DiffOp.ADJUST, round(value, 2),
             reason_event_id=event_id, visibility_scope=scope),
        None,
    )


def apply_gambit(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    payload: str | dict[str, Any],
    turn: int,
    date: str,
    seed: int,
    ledger: dict[tuple[str, str], float] | None = None,
    per_field_cap: float = 6.0,
    index: int = 0,
    span: float = 1.0,
) -> GambitOutcome:
    """Read what the model proposed, check it, roll it, and write the consequence.

    ``ledger`` is the per-turn ceiling and should be the *same dict* the freeform channel
    is given, so the two cannot be combined past what either allows.
    """
    outcome = GambitOutcome()
    try:
        data: dict[str, Any] = (
            json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        )
    except (TypeError, ValueError):
        outcome.problems.append("the answer to a gambit was not readable")
        return outcome

    if refused := data.get("refused"):
        outcome.refused = clip(sanitize(str(refused)), 400)
        return outcome

    attempt = data.get("attempt")
    if not isinstance(attempt, dict):
        outcome.refused = "nothing was attempted"
        return outcome

    kind = kind_by_id(str(attempt.get("kind") or ""))
    if kind is None:
        outcome.problems.append(
            f"there is no undertaking called {attempt.get('kind')!r}; this era offers "
            + ", ".join(k.id for k in KINDS)
        )
        return outcome

    target = attempt.get("target")
    target = str(target) if target else None
    region = attempt.get("target_region")
    region = str(region) if region else None
    # Read once, here, and handed to both the gate and the effect. `air.strike` checks it
    # against the closed `STRIKE_AIMS` vocabulary and refuses anything else by name, so
    # the bound below is only on the length of what gets quoted back in a refusal.
    aim = sanitize(str(attempt.get("aim") or ""))[:40]
    if (problem := _requirements_met(
        conn, world_id, kind, nation_id, target, region, aim
    )) is not None:
        outcome.refused = problem.rendered()
        return outcome

    stake_name = str(attempt.get("stakes") or "minor")
    magnitude, cost = STAKES.get(stake_name, STAKES["minor"])
    # A season's scheming costs a season's money and moves a season's worth. Both halves
    # scale together, so the *exchange rate* a stake offers is identical at every span and
    # only the quantity per decision changes.
    magnitude, cost = magnitude * span, cost * span
    if kind.id in NO_TREASURY_STAKE:
        cost = 0.0

    # Charged win or lose, and refused up front if it cannot be afforded -- with the real
    # figure named, so the answer is actionable rather than a shrug.
    purse = conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?", (world_id, nation_id)
    ).fetchone()
    if purse is None:
        outcome.problems.append(f"{nation_id} is not a nation on this map")
        return outcome
    if float(purse["treasury"]) < cost:
        outcome.refused = (
            f"{kind.name} at `{stake_name}` stakes costs {cost:.0f} and you have "
            f"{float(purse['treasury']):.0f}"
        )
        return outcome

    # A kind whose consequences the engine owns outright. The frame above it is identical
    # -- proposed, checked against the world, refused before the roll if it could not have
    # been attempted, charged its stake -- and only the *effects* stage differs, because a
    # warhead is not a verb a staff gets to pick.
    if kind.id in _BESPOKE:
        done = _BESPOKE[kind.id](
            conn, world_id, kind=kind, outcome=outcome, actor=nation_id, target=target,
            region=region, stake_name=stake_name, cost=cost, intent=str(
                attempt.get("intent") or ""
            ),
            turn=turn, date=date, seed=seed, index=index,
            **({"aim": aim} if kind.id == "air_strike" else {}),
        )
        _through_a_truce(conn, world_id, kind, done, nation_id, turn=turn, date=date)
        return done

    evidence = [
        flag for flag in (attempt.get("evidence") or ()) if str(flag) in EVIDENCE
    ][:4]
    result, odds, _roll = adjudicate(
        conn, world_id, kind=kind, actor=nation_id, target=target,
        evidence=[str(f) for f in evidence], seed=seed, turn=turn, index=index,
    )
    outcome.kind, outcome.target, outcome.result, outcome.odds = (
        kind.id, target, result, round(odds, 3)
    )
    outcome.cost = cost

    intent = clip(sanitize(str(attempt.get("intent") or "")), 300)
    # A covert gambit that *failed* is public: getting caught is how it becomes known,
    # which is the whole risk of trying it. A covert one that worked stays secret.
    scope = (
        Visibility.SECRET
        if kind.covert and result != "failure"
        else Visibility.PUBLIC
    )
    told = f"{kind.name}: {intent}" if intent else kind.name
    outcome.narrative = f"{told} -- {result}"
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=kind.event_kind, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=f"{told} ({result})"[:600],
        visibility=scope,
        visible_to=(nation_id,) if scope is Visibility.SECRET else (),
        # **Who it was done to, structurally.** The undertaking knew its target and the
        # event did not record it, so an act against somebody was not recoverable from the
        # thing that recorded the act -- the same gap the land battle had before the
        # constituency layer needed to know who lost one. `attribution` reads this to decide
        # whose problem the act is, and a covert act with no target participant is by
        # construction not attributable to anybody.
        participants=((("actor", nation_id), ("target", target)) if target
                      else (("actor", nation_id),)),
    )
    outcome.event_id = event_id

    verbs = list(
        attempt.get("on_success" if result != "failure" else "on_failure") or ()
    )[:MAX_EFFECTS]
    share = 1.0 if result == "success" else PARTIAL_SHARE
    spent = {} if ledger is None else ledger

    diffs: list[Diff] = [
        # The stake, charged first and regardless. A gambit you did not pay for is a
        # gambit you would always take.
        #
        # **Scoped like its cause, not hard-coded public.** It was public, and rule
        # INFO-002 -- "a covert cause cannot produce overt state" -- correctly refused the
        # whole batch every time a covert gambit *worked*, because a covert success files
        # a SECRET event. The mechanic was silently inverted: a bribe or a sabotage landed
        # only when it failed. A live Modern Day run caught it on Ukraine's one successful
        # sabotage of the game. Paying for a covert operation is itself covert, so the
        # correct scope was never public.
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -round(cost, 2),
             reason_event_id=event_id, visibility_scope=scope)
    ]
    for verb in verbs:
        diff, problem = _effect_to_diff(
            conn, str(verb), nation_id, target, magnitude * share, event_id, spent, scope,
            per_field_cap,
        )
        if problem:
            outcome.trimmed.append(problem)
        if diff is not None:
            diffs.append(diff)

    applied = apply_batch(
        conn,
        DiffBatch(
            # The engine writes. What came back from the model was evidence, and every
            # number above was chosen here -- the same arrangement a contest has, and the
            # reason `gambit` is not among the roles `diff_batch` accepts.
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, proposer_nation_id=nation_id, diffs=tuple(diffs),
        ),
    )
    if applied.applied:
        outcome.applied = len(diffs)
        _through_a_truce(conn, world_id, kind, outcome, nation_id, turn=turn, date=date)
    else:
        outcome.problems.append(f"the undertaking could not be recorded: {applied.reason}")
    return outcome


def _through_a_truce(  # noqa: PLR0913 - who, to whom, what came of it, and when
    conn: sqlite3.Connection,
    world_id: str,
    kind: GambitKind,
    outcome: GambitOutcome,
    actor: str,
    *,
    turn: int,
    date: str,
) -> None:
    """Charge an act of war against a truce partner as breaking the truce.

    **A ceasefire leaves ``at_war`` at 1**, so the ``at_war`` gate alone let an air strike,
    a warhead, a sabotage or an incitement through a standing truce without breaking it,
    while the air war and the ground war both honoured it. Finding W1 of the 2026-09-26
    scan: three strikes took a formation from 6 to 2.93 and the truce stood.

    **Charged, not refused, because that is what an ordered ground attack gets.**
    `resolution._through_the_truce` lets a contest somebody *ordered* into a paused front
    go ahead and runs it through :func:`~atlas.engine.armistice.break_armistice` -- the
    status, the regard, the legitimacy, and a casus belli for the side that was attacked.
    A strike is an order in exactly that sense, and an armistice is something either side
    can break; refusing it would make a truce the one agreement nobody could tear up.

    **Only once it happened.** Called after the act is recorded, so a strike the air layer
    turned down, or a batch that did not land, breaks nothing. An intercepted strike is
    still an attack and still breaks it.

    **A covert act breaks it only when it is caught.** A sabotage or an incitement that
    works is filed SECRET, and charging it as a breach would publish it: the breach, its
    casus belli and the regard it costs are all public, so the victim would learn from the
    headlines what the secrecy exists to keep from it. A covert act that *fails* is public
    already -- getting caught is how it becomes known -- and then it is charged like any
    other attack. So the truce is exactly as broken as the world can see it to be, which is
    the same rule a covert act's visibility already follows.
    """
    target = outcome.target
    if "at_war" not in kind.requires or not target or not outcome.happened:
        return
    if kind.covert and outcome.result != "failure":
        return
    from .armistice import break_armistice

    outcome.truce_broken = break_armistice(
        conn, world_id, breaker=actor, victim=target, turn=turn, date=date)
