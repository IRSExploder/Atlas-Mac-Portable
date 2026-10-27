"""What an accepted order does by itself, before anyone narrates it.

The parser tells the player their order will be attempted. Whether anything then happened
depended entirely on the world simulator choosing to write a diff for it -- so "accelerate
rearmament", accepted three quarters running, moved military strength by one and a half
points, and only because one batch out of three survived validation. An order that is
accepted and then silently does nothing is worse than one that is refused.

So every order type with an unambiguous mechanical meaning gets an engine-authored effect
here: small, bounded, deterministic, and paid for. The simulator's narrative diffs are
still the main channel and still carry the interesting consequences. This is the floor
that makes acceptance mean something.

**Nothing here is contested.** Building a factory is not a battle; it either costs what it
costs or the treasury cannot pay. Anything that somebody could resist -- taking ground,
declaring war -- goes through :mod:`atlas.engine.resolution` and the seeded draw instead.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import reference
from .capabilities import by_type
from .causality import OrderOutcome
from .intelligence import (
    invest_counterintel,
    run_espionage,
)
from .naming import resolve_nation, resolve_region
from .pacing import over, span_of
from .production import order_construction, order_project, order_research, raised_strength
from .projects import CIVIL_PROJECTS
from .reference import resolve
from .research import catalogue_for
from .warheads import TACTICAL_PROJECT, WARHEAD_PROJECT

__all__ = [
    "OrderEffects",
    "Replaceable",
    "apply_order_effects",
    "replaceable_now",
]


def _named_or_mentioned(
    conn, world_id: str, kind: str, action: dict, named: str
) -> reference.Reference:
    """Resolve the schema field if it was filled, else look for a name in the prose.

    Two genuinely different questions: a field holds *a name*, and a summary is *a
    sentence with a name in it*. Asking the second question of the first is how a display
    name the player actually read stopped matching anything -- and asking the first of the
    second is how `entry.id in haystack` matched `rail` inside any sentence containing it.
    """
    if named:
        found = reference.resolve(conn, world_id, kind, named)
        if found.ok:
            return found
        found = reference.mentioned_in(conn, world_id, kind, named)
        if found.ok:
            return found
    return reference.mentioned_in(conn, world_id, kind, action.get("summary") or "")


def _unit_kind(conn: sqlite3.Connection, world_id: str, action: dict) -> str | None:
    """Which known kind an order names.

    Read from the order's own field first and from its prose second: the parser has a
    ``unit_kind`` for exactly this, and a player who wrote "lay down two battleships"
    should not lose the ship to a schema field the model left empty. The caller decides
    from the template's build time whether it appears now or enters the yards.
    """
    named = str(action.get("unit_kind") or "").strip()
    return _named_or_mentioned(conn, world_id, "unit_kind", action, named).value

#: What an order costs and what it buys. Deliberately modest -- these are the effects of
#: a quarter's administration, not of a five-year plan.
_INVESTMENT_COST = 6.0
_INVESTMENT_GAIN = 1.5
_UNIT_TREASURY_COST = 6.0
_UNIT_MANPOWER_COST = 10.0
_UNIT_STRENGTH = 4.0
_FORTIFY_MORALE = 4.0
_MORALE_CEILING = 95.0


#: Where the dispatch loop tells a handler how many identical orders came before it this
#: turn. Reserved and double-underscored for the same reason ``__insert__`` is: it travels
#: in the action dict, which is otherwise the parser's vocabulary, and a name a model could
#: plausibly emit would be a name a model could forge.
_REPEAT = "__repeat__"

#: What makes two orders "the same order". Everything a handler branches on that could
#: distinguish one attempt from another -- not the position in the list, which would make
#: an unrelated order typed first move somebody else's roll.
_SIGNIFICANT = ("undertaking", "target_nation", "to_region", "from_region", "unit_kind")


def _signature(action: dict, kind: str) -> tuple:
    return (kind, *(str(action.get(field_name) or "").lower() for field_name in _SIGNIFICANT))


@dataclass(slots=True)
class OrderEffects:
    applied: int = 0
    units_raised: int = 0
    problems: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    #: One record per order, tied to the words that produced it. `problems` says what went
    #: wrong; this says *which order* it went wrong for, which is the difference between a
    #: player knowing something failed and knowing what to write instead.
    outcomes: list[OrderOutcome] = field(default_factory=list)
    #: The per-turn magnitude ceiling, shared by every undertaking in this turn's orders.
    #: `apply_gambit` bounds how far one field may be moved in a turn, and it does that by
    #: accumulating into the dict it is handed -- so handing each order a fresh one turns a
    #: per-turn ceiling into a per-order one, and three orders move a field three times as
    #: far as one is allowed to. One dict per call to :func:`apply_order_effects`, which is
    #: one per turn.
    gambit_ledger: dict[tuple[str, str], float] = field(default_factory=dict)
    #: How many reference months this turn covers. Carried on the effects rather than added
    #: to the handler signature, for the same reason `gambit_ledger` is: it is a fact about
    #: the *turn*, every handler already receives this object, and the signature is shared
    #: by fifteen handlers that do not need it.
    #:
    #: **It was not carried at all, and a player's undertakings were the only thing in the
    #: engine still priced by the turn rather than by the month.** `apply_gambit` takes a
    #: span and defaults it to 1.0; the model-facing channel passes the real one and this
    #: path did not. So on a year-long turn a tracked nation paid a year's stake for a
    #: year's magnitude and the player paid a month's for a month's -- and the handler's own
    #: docstring promised "the odds, the stake, the ledger cap and the roll are identical
    #: whoever asked". Playing on long turns made scheming a twelfth of the price.
    span: float = 1.0


def apply_order_effects(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    actions: list[dict],
    nation_id: str | None,
    days: float = 30.0,
) -> OrderEffects:
    """Give every accepted order a consequence the player can see in the numbers."""
    effects = OrderEffects(span=span_of(days))
    if not nation_id or not actions:
        return effects

    repeats: Counter[tuple] = Counter()
    for index, action in enumerate(actions):
        kind = str(action.get("type") or "")
        # **Assigned here, before anything can refuse it.** An order is a sentence and the
        # things that happen to it are spread across the parser, the handler and the
        # resolver; the id is what lets a refusal three layers down still be reported
        # against the words the player typed. Stable within a turn by construction.
        outcome = OrderOutcome(
            correlation_id=f"{nation_id}:{turn}:{index}",
            order_text=str(action.get("summary") or "")[:200],
            action_type=kind,
        )
        effects.outcomes.append(outcome)

        handler = _HANDLERS.get(kind)
        if handler is None:
            # **Not attempted is not the same as refused**, and telling a player the first
            # was the second sends them to fix a sentence that was fine.
            outcome.status = "not_attempted"
            outcome.reason = f"no part of your government carries out a {kind!r} order"
            capability = by_type(kind)
            if capability is not None and capability.handled_by == "nothing":
                outcome.suggestion = capability.notes[0] if capability.notes else ""
            continue
        action = _without_self_target(conn, world_id, action, nation_id)
        # **How many times this same order has already been given this turn.**
        #
        # An order is a decision, and the *second* time a player gives the same one in a
        # turn is a different decision -- exactly the argument `_resolve_one` makes for
        # putting the round in a contest's stream key. Without it, `gambit:sabotage:a->k#0`
        # is drawn three times for three orders and returns the same number three times: a
        # player who paid for three attempts got one roll and three copies of its answer,
        # so the outcome was three successes or three failures and never a mix.
        #
        # Counted over the order's own identity rather than its position in the list, so
        # an unrelated order typed before it does not move anybody's roll -- which is the
        # ordinal-key trap `atlas.sim.rng` exists to avoid.
        repeats[_signature(action, kind)] += 1
        action = {**action, _REPEAT: repeats[_signature(action, kind)] - 1}
        before = len(effects.problems)
        try:
            handler(conn, world_id, turn, date, action, nation_id, effects)
        except (KeyError, ValueError, TypeError, sqlite3.Error) as exc:
            effects.problems.append(f"{kind} could not be carried out: {exc}")

        # Whatever the handler said about this order, said against this order. A handler
        # that appended nothing succeeded; anything it appended is this order's reason.
        raised = effects.problems[before:]
        if raised:
            outcome.status = "refused"
            outcome.reason = raised[0]
            outcome.suggestion = _suggestion_for(kind, raised[0])

    return effects


#: What to write instead, when the engine can tell. Keyed on a phrase the refusal already
#: contains rather than on an error code, because the refusals are prose and inventing
#: codes for them would be a second vocabulary to keep in step with the first.
_SUGGESTIONS: tuple[tuple[str, str], ...] = (
    ("no such unit", "name a kind from the forces menu in your briefing"),
    ("was not told which undertaking",
     "name the undertaking outright, e.g. `sabotage` or `summit`"),
    ("could be any of", "name which one you mean, by id"),
    ("nowhere to build", "name a region you hold"),
    ("cannot afford", "wait for revenue, or order something cheaper"),
    ("not a thing this world builds",
     "use an id from the menu of works and programmes"),
)


def _suggestion_for(kind: str, reason: str) -> str:
    lowered = reason.lower()
    for phrase, advice in _SUGGESTIONS:
        if phrase in lowered:
            return advice
    capability = by_type(kind)
    if capability is not None and capability.required:
        return "make sure the order names " + ", ".join(capability.required)
    return ""


def _without_self_target(
    conn: sqlite3.Connection, world_id: str, action: dict, nation_id: str
) -> dict:
    """Blank ``target_nation`` when it names the country giving the order.

    A player who writes "invest in Aurelia's economy" while playing Aurelia has named a
    domestic action, and the parser is now told to read it that way -- but it still fills
    in the country it recognised, leaving a target on an action that has no target. Every
    current handler ignores the field for domestic kinds, so this changes no behaviour
    today; it exists so that the *next* handler cannot read a stray value and conclude the
    order was aimed at somebody.

    Returns a copy. The caller's list belongs to the parser's answer and is kept
    unmodified as the record of what the model actually said.
    """
    named = action.get("target_nation")
    if not named:
        return action
    if resolve_nation(conn, world_id, str(named)) != nation_id:
        return action
    return {**action, "target_nation": None}


def _nation(conn: sqlite3.Connection, nation_id: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT name, economy, treasury, manpower, military_strength FROM nation WHERE id = ?",
        (nation_id,),
    ).fetchone()


def _commit(
    conn: sqlite3.Connection,
    world_id: str,
    turn: int,
    diffs: list[Diff],
    effects: OrderEffects,
    what: str,
) -> bool:
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if outcome.applied:
        effects.applied += 1
        effects.headlines.append(what)
        return True
    effects.problems.append(f"{what} was refused: {outcome.reason}")
    return False


def _invest(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Money into the economy. Refused outright if the treasury cannot pay for it."""
    nation = _nation(conn, nation_id)
    if nation is None:
        return
    if float(nation["treasury"]) < _INVESTMENT_COST:
        effects.problems.append(
            f"{nation['name']} could not fund the investment: the treasury is empty"
        )
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ECONOMIC_REFORM, origin=EventOrigin.ENGINE,
        text=f"{nation['name']} directs public funds into industry and rearmament.",
        actor_nation_id=nation_id, visibility=Visibility.PUBLIC,
    )
    _commit(
        conn, world_id, turn,
        [
            Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -_INVESTMENT_COST,
                 reason_event_id=event_id),
            Diff("nation", nation_id, "economy", DiffOp.ADJUST, _INVESTMENT_GAIN,
                 reason_event_id=event_id),
        ],
        effects,
        f"{nation['name']} invested in its economy.",
    )


def _raise_units(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Actually put a formation on the map, and charge for it.

    Rule UNIT-001 requires an engine-authored ``unit_raised`` event in the same batch, so
    the event is written first and every diff cites it.
    """
    nation = _nation(conn, nation_id)
    if nation is None:
        return

    # Anything with a build time goes to the yards instead. A division raised this month
    # is a defensible abstraction; a battleship launched this month is not. An explicitly
    # named kind that the scenario does not define is refused: falling through to the
    # generic infantry path turned a transport order into an army without telling the
    # player.
    named_kind = str(action.get("unit_kind") or "").strip().lower()
    kind = _unit_kind(conn, world_id, action)
    template = (
        conn.execute(
            "SELECT cost_treasury, cost_manpower, base_strength, build_days,"
            "       COALESCE(domain, 'land') AS domain"
            "  FROM unit_template WHERE world_id = ? AND kind = ?",
            (world_id, kind),
        ).fetchone()
        if kind is not None else None
    )
    if named_kind and kind is None and named_kind != "infantry":
        effects.problems.append(
            f"{nation['name']} could not raise or build {named_kind}: "
            "this scenario defines no such unit"
        )
        return
    if template is not None and float(template["build_days"] or 0.0) > 0:
        region_id = _home_region(conn, world_id, nation_id, action)
        if region_id is None:
            effects.problems.append(f"{nation['name']} has nowhere to build")
            return
        report = order_construction(
            conn, world_id, nation_id=nation_id, kind=kind, region_id=region_id,
            quantity=_quantity(action), turn=turn, date=date,
        )
        effects.problems.extend(report.problems)
        effects.headlines.extend(f"{entry} laid down." for entry in report.started)
        # **Laying a division down is carrying the order out.** `applied` was left at zero
        # on this branch, so a scenario whose infantry has a build time -- Modern Day's
        # does, thirty days -- reported every accepted raising order as nothing happening.
        # The player saw an order vanish and a tracked power's reinforcement never showed
        # up in the turn report at all, which is how the channel measured as dead when it
        # was working.
        effects.applied += len(report.started)
        return

    wanted = _quantity(action)
    treasury_cost = float(template["cost_treasury"]) if template else _UNIT_TREASURY_COST
    manpower_cost = float(template["cost_manpower"]) if template else _UNIT_MANPOWER_COST
    # Raised directly rather than through the yards -- the same stamp, so a formation is
    # worth the same whichever door it came through.
    strength = raised_strength(
        conn, world_id, nation_id,
        float(template["base_strength"]) if template else _UNIT_STRENGTH,
        str(template["domain"]) if template else "land")
    immediate_kind = kind or "infantry"
    affordable = min(
        wanted,
        int(float(nation["treasury"]) // max(0.01, treasury_cost)),
        int(float(nation["manpower"]) // max(0.01, manpower_cost)),
    )
    if affordable <= 0:
        effects.problems.append(
            f"{nation['name']} could not raise new formations: neither the treasury nor "
            "the manpower would stretch to it"
        )
        return

    region_id = _home_region(conn, world_id, nation_id, action)
    if region_id is None:
        effects.problems.append(f"{nation['name']} has nowhere to raise forces")
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.UNIT_RAISED, origin=EventOrigin.ENGINE,
        text=(
            f"{nation['name']} raises {affordable} new formation"
            f"{'s' if affordable != 1 else ''}."
        ),
        actor_nation_id=nation_id, visibility=Visibility.PUBLIC,
    )

    existing = conn.execute(
        "SELECT COUNT(*) FROM unit WHERE nation_id = ?", (nation_id,)
    ).fetchone()[0]
    diffs: list[Diff] = []
    for index in range(affordable):
        unit_id = f"u_{nation_id}_new{turn}_{existing + index + 1}"
        diffs.append(
            Diff("unit", unit_id, "__insert__", DiffOp.INSERT,
                 {
                     "world_id": world_id, "nation_id": nation_id, "region_id": region_id,
                     "kind": immediate_kind, "strength": strength, "morale": 50.0,
                 },
                 reason_event_id=event_id)
        )
    diffs += [
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST,
             -treasury_cost * affordable, reason_event_id=event_id),
        Diff("nation", nation_id, "manpower", DiffOp.ADJUST,
             -manpower_cost * affordable, reason_event_id=event_id),
    ]
    if _commit(conn, world_id, turn, diffs, effects,
               f"{nation['name']} raised {affordable} formation(s)."):
        effects.units_raised += affordable




#: What it costs to put a point of strength back into a formation that already exists,
#: as a share of what the same point costs in a formation raised from nothing.
#:
#: **The two halves are deliberately different, and the difference is the mechanic.** A
#: worn division is not a missing division: its guns, its officers and its staff work are
#: still there, so the treasury is only making good small equipment, while its men are
#: simply gone and there is no discount on a man. Reinforcement is therefore cheap in money
#: and expensive in people -- **manpower is the currency of attrition warfare here**, and
#: it is the one a bled country still has. It refills toward a population ceiling, so it is
#: renewable, bounded by demography rather than by cash, and it bites hardest exactly when
#: a nation is being bled fastest.
#:
#: **The 0.02 was measured, not chosen, and the first two attempts at it were wrong.** At
#: a quarter -- the intuitive "reinforcing is cheaper than building" number -- nothing
#: changed for the losing side at all, because the constraint money runs into is not the
#: price. It is that upkeep consumes about 95% of revenue at the force levels every
#: scenario ships: on the Ukraine front the defender nets 0.10 treasury a month and the
#: attacker, a nuclear great power, nets 0.14. Neither has a war budget. Worse, gating
#: replacement on the treasury is *regressive* -- a share of a large treasury is a lot and
#: a share of nothing is nothing, and a power's revenue falls as it loses ground while its
#: upkeep does not. At 0.08 that showed plainly: the attacker rebuilt from 50 strength back
#: up to 78 while the defender still died on schedule. A money-priced mechanic amplifies
#: the snowball it was built to damp.
#:
#: So the money is nominal on purpose and the men are the real price. What this cannot fix
#: is the upkeep-to-revenue calibration one layer below it, which is a separate decision.
REINFORCE_TREASURY_SHARE = 0.25
REINFORCE_MANPOWER_SHARE = 1.0

#: How much of its establishment one formation may recover in a reference month. A wrecked
#: division comes back over about three months rather than overnight -- the point is a
#: grind, and a line that can be restored in a turn is not a grind, it is a reset button.
REINFORCE_MAX_FRACTION = 0.34

#: The most of its treasury a nation will put into reinforcement in one turn. This is what
#: makes the order safe to give as a plain yes: it can never spend a country's last coin,
#: so choosing to reinforce is choosing to reinforce *and* keep building, at half rate,
#: rather than choosing to stop developing. Guns and butter, not guns or butter.
REINFORCE_BUDGET_SHARE = 0.5


@dataclass(slots=True)
class Replaceable:
    """What ``reinforce`` would actually do for a nation this turn, and what it would not.

    **The briefing and the handler disagreed, and the briefing was the one that was wrong.**
    `views._raisable_lines` invited the order off an army-wide sum over unit *kinds* --
    ``n * establishment - total`` -- and printed it whenever that exceeded 0.01. The handler
    works per *formation*: it skips anything at sea, anything out of supply, and anything
    whose own allowance floors to a hundredth or less. The two answer different questions
    and a very ordinary state answers them oppositely: six air wings 0.1 below establishment
    between them are 0.017 short each, which floors to `0.01` and is skipped six times over.

    Measured on `pt77-cold-war`: 56 of 60 tracked briefings carried the invitation, 47
    answers took it, and **13 were refused** -- the United States on nine of its twelve
    turns and West Germany on four of six, with the identical sentence each time. It is the
    second-most-used channel in the whole recorded corpus.

    So the two now read the same function. `formations` and `points` are what the order
    would achieve if money were no object; the three `blocked_*` counts are why the rest are
    not eligible, which is what lets a briefing say something true when the answer is none.
    """

    #: Formations that would take replacements this turn.
    formations: int = 0
    #: Strength they would take between them, ignoring the treasury and the manpower --
    #: which the briefing names separately, because they are a *bound* rather than a reason.
    points: float = 0.0
    #: How short the army actually is, summed over **formations** rather than over kinds.
    #:
    #: **The briefing's old headline was a per-kind net and it could be smaller than what
    #: the order would put back.** `n * establishment - Σstrength` lets a kind's
    #: over-strength formations cancel its under-strength ones, so an army with one
    #: division at half and another at one-and-a-half reads as fully manned -- and the
    #: rendered line came out as "2.1 below establishment, and 2 formations could take 2.9
    #: of it back", which is not a sentence anybody should be asked to act on. Counted per
    #: formation, the two numbers cannot cross: `points` is bounded above by this.
    short_points: float = 0.0
    #: How many formations are under strength at all, eligible or not.
    short_formations: int = 0
    #: Under strength, but on a formation the handler will not consider.
    blocked_at_sea: int = 0
    blocked_out_of_supply: int = 0
    #: Under strength by so little that its allowance floors to a hundredth or less. The
    #: bound nobody was told about and the one that actually fired.
    blocked_too_thin: int = 0

    @property
    def blocked(self) -> int:
        return self.blocked_at_sea + self.blocked_out_of_supply + self.blocked_too_thin


def replaceable_now(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, span: float = 1.0
) -> Replaceable:
    """The formations ``reinforce`` would actually reach, on the handler's own terms.

    Shared with :func:`_reinforce` rather than restated beside it, for the reason
    :data:`atlas.state.rules.economy.SOFT_CAPS` is read rather than copied into the
    briefing: a menu that quotes a number the engine then refuses is worse than one that
    says nothing, because the reader trusts it.

    Money is deliberately absent. The purse bounds *how much* comes back and never *whether
    anything does* -- `REINFORCE_BUDGET_SHARE` guarantees a nation with any treasury at all
    can afford some -- so folding it in here would turn a fact about the army into a fact
    about the balance sheet, and the briefing quotes the price on its own line anyway.
    """
    from .logistics import supply_map

    out = Replaceable()
    span = max(0.0, float(span))
    if span <= 0.0:
        return out

    templates = {
        str(row["kind"]): row
        for row in conn.execute(
            "SELECT kind, base_strength FROM unit_template WHERE world_id = ?", (world_id,)
        )
    }
    reachable = supply_map(conn, world_id, nation_id)
    for unit in conn.execute(
        "SELECT u.id, u.kind, u.strength, u.region_id, r.kind AS terrain"
        "  FROM unit u JOIN region r ON r.id = u.region_id"
        " WHERE u.world_id = ? AND u.nation_id = ?"
        " ORDER BY u.strength ASC, u.id ASC",
        (world_id, nation_id),
    ):
        template = templates.get(str(unit["kind"]))
        establishment = (
            float(template["base_strength"] or 0.0) if template is not None
            else _UNIT_STRENGTH)
        if establishment <= 0.0:
            continue
        missing = establishment - float(unit["strength"])
        if missing <= 0.01:
            continue
        out.short_formations += 1
        out.short_points += missing
        # In the handler's own order: a formation at sea is not a candidate at all, one on
        # land may still be out of reach of a depot, and one the depots can reach may still
        # be too close to establishment to be worth a replacement.
        if str(unit["terrain"]) == "ocean":
            out.blocked_at_sea += 1
            continue
        if str(unit["region_id"]) not in reachable:
            out.blocked_out_of_supply += 1
            continue
        allowance = min(missing, over(establishment * REINFORCE_MAX_FRACTION, span))
        allowance = math.floor(allowance * 100.0) / 100.0
        if allowance <= 0.01:
            out.blocked_too_thin += 1
            continue
        out.formations += 1
        out.points += allowance
    return out


def _reinforce(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Put strength back into the formations a nation already has.

    **The gap this fills.** `raise_units` could add a division and nothing could restore
    one, so an army had exactly one direction to move in. Measured on the Ukraine front:
    the defender ended twenty turns holding twelve formations at zero strength -- the
    divisions were still on the map, still counted, and worth nothing, because the flat
    attrition branch drives strength down and never removes the unit. Twelve cadres with
    no way to be filled is the exact state this order is for.

    Reinforcement is only worth having if it is *cheaper than rebuilding*, so it is priced
    per point of strength rather than per formation, at a quarter of a new formation's
    money and all of its men.

    **Only what can be reached.** A formation out of supply is not reinforced at any price,
    which is what stops this from becoming a way to rebuild an army in a pocket deep in
    somebody else's country, and ties the channel to the logistics layer rather than
    letting it route around it.

    Most worn first, then by id. Deterministic by construction: no draw is made here, so
    two runs of the same turn spend the same coins on the same divisions.
    """
    from .logistics import supply_map

    nation = _nation(conn, nation_id)
    if nation is None:
        return

    span = max(0.0, float(effects.span))
    if span <= 0.0:
        return

    templates = {
        str(row["kind"]): row
        for row in conn.execute(
            "SELECT kind, cost_treasury, cost_manpower, base_strength"
            "  FROM unit_template WHERE world_id = ?", (world_id,)
        )
    }
    reachable = supply_map(conn, world_id, nation_id)

    wanted_kind = str(action.get("unit_kind") or "").strip().lower()
    wanted_region = (resolve_region(conn, world_id, action.get("region"))
                     or resolve_region(conn, world_id, action.get("to_region")))

    worn = conn.execute(
        "SELECT u.id, u.kind, u.strength, u.region_id"
        "  FROM unit u JOIN region r ON r.id = u.region_id"
        " WHERE u.world_id = ? AND u.nation_id = ? AND r.kind <> 'ocean'"
        " ORDER BY u.strength ASC, u.id ASC",
        (world_id, nation_id),
    ).fetchall()

    purse = float(nation["treasury"]) * REINFORCE_BUDGET_SHARE
    men = float(nation["manpower"])
    # ``Diff`` is frozen, so the drafts are held as plain pairs and turned into diffs once
    # the event they all cite exists. Building them first and stamping the id afterwards
    # raises `FrozenInstanceError`, which is the class doing its job.
    drafts: list[tuple[str, float]] = []
    spent_treasury = spent_manpower = 0.0
    points = 0.0
    touched = 0

    # **The eligibility below is mirrored by `replaceable_now`, which is what the briefing
    # prints.** Not shared as one function, deliberately: this loop spends a purse down
    # across formations in order and narrows by the kind and region a player may have named,
    # and folding those into a menu helper would put budget sequencing inside a display
    # function. `test_the_reinforcement_menu_matches_the_reinforcement_handler` runs both
    # over the same worlds and fails the moment they disagree, which is the guarantee that
    # actually matters -- the defect was never that the code was in two places, it was that
    # the two places answered different questions and nobody checked.
    for unit in worn:
        # **Falls back to the module defaults exactly as `_raise_units` does.** A scenario
        # is not obliged to define `unit_template` -- the sandbox defines none at all --
        # and a mechanic that silently does nothing there is worse than one that prices a
        # formation generically, which is the choice raising already made.
        template = templates.get(str(unit["kind"]))
        establishment = (
            float(template["base_strength"] or 0.0) if template is not None
            else _UNIT_STRENGTH)
        new_treasury = (
            float(template["cost_treasury"] or 0.0) if template is not None
            else _UNIT_TREASURY_COST)
        new_manpower = (
            float(template["cost_manpower"] or 0.0) if template is not None
            else _UNIT_MANPOWER_COST)
        missing = establishment - float(unit["strength"])
        if establishment <= 0.0 or missing <= 0.01:
            continue
        if wanted_kind and str(unit["kind"]) != wanted_kind:
            continue
        if wanted_region and str(unit["region_id"]) != wanted_region:
            continue
        if str(unit["region_id"]) not in reachable:
            continue

        per_point_treasury = new_treasury / establishment * REINFORCE_TREASURY_SHARE
        per_point_manpower = new_manpower / establishment * REINFORCE_MANPOWER_SHARE

        allowance = min(missing, over(establishment * REINFORCE_MAX_FRACTION, span))
        if per_point_treasury > 0:
            allowance = min(allowance, purse / per_point_treasury)
        if per_point_manpower > 0:
            allowance = min(allowance, men / per_point_manpower)
        # **Rounded DOWN, and the difference is a rule violation.** `round` goes to the
        # nearest, so an allowance capped at exactly what the purse holds -- 1.235 of a
        # possible 1.235 -- rounds up to 1.24 and spends more manpower than the country
        # has. It lands as a treasury or manpower of -0.0, which UNIT-001 correctly refuses
        # as forces raised without the means to pay, and it takes the whole batch with it:
        # a nation reinforcing with its last men gets nothing instead of nearly enough.
        # Caught by the laboratory on a recorded run, refusing North Vietnam's turn 7.
        allowance = math.floor(allowance * 100.0) / 100.0
        if allowance <= 0.01:
            continue

        purse -= allowance * per_point_treasury
        men -= allowance * per_point_manpower
        spent_treasury += allowance * per_point_treasury
        spent_manpower += allowance * per_point_manpower
        points += allowance
        touched += 1
        drafts.append((str(unit["id"]), allowance))

    if not drafts:
        effects.problems.append(
            f"{nation['name']} had nothing to reinforce: no formation of its own is both "
            "under strength and in supply, or neither the treasury nor the manpower would "
            "stretch to a single replacement"
        )
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.UNIT_RAISED, origin=EventOrigin.ENGINE,
        text=(
            f"{nation['name']} sends {points:.1f} strength in replacements to {touched} "
            f"formations in the line."
        ),
        actor_nation_id=nation_id, visibility=Visibility.PUBLIC,
    )
    restored: list[Diff] = [
        Diff("unit", unit_id, "strength", DiffOp.ADJUST, allowance,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for unit_id, allowance in drafts
    ]
    # **Clamped to the purse, not merely rounded from it.** UNIT-001 refuses a batch that
    # leaves a nation overdrawn, and it caught this one on a recorded run: North Vietnam,
    # turn 7, manpower -0.0, its whole reinforcement thrown out for a rounding error. The
    # per-formation allowance is floored above, which makes the overshoot unlikely, but the
    # charge is a *sum* of floats rounded once at the end and "unlikely" is not a guarantee.
    # This is: what is charged can never exceed what was counted at the top.
    charged_treasury = min(round(spent_treasury, 2), float(nation["treasury"]))
    charged_manpower = min(round(spent_manpower, 2), float(nation["manpower"]))
    restored += [
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST,
             -charged_treasury, reason_event_id=event_id),
        Diff("nation", nation_id, "manpower", DiffOp.ADJUST,
             -charged_manpower, reason_event_id=event_id),
    ]
    if _commit(conn, world_id, turn, restored, effects,
               f"{nation['name']} reinforced {touched} formation(s)."):
        effects.applied += 1


#: Words that appear in a build order without narrowing it down at all. Stripped before
#: looking for a near-miss, or "start a new programme" matches everything with "programme"
#: in its name and the near-miss is worse than no answer.
_ORDER_NOISE = frozenset(
    {
        "a", "an", "and", "at", "begin", "build", "building", "construct", "expand",
        "for", "in", "into", "invest", "more", "new", "of", "on", "our", "programme",
        "program", "project", "start", "the", "to", "up", "with", "work", "works",
    }
)


def _significant(text: str) -> set[str]:
    return {
        word
        for word in re.split(r"[^a-z0-9]+", text.lower())
        if len(word) >= 4 and word not in _ORDER_NOISE
    }


def _closest(request: str, entries) -> list:
    """Everything whose name or id shares a significant word with ``request``.

    A deliberately conservative near-miss: shared *whole* words only, no fuzzy distance.
    The point is not to guess what a staff meant but to turn "I do not know what you
    asked for" into "did you mean this one", which is the difference between a dead end
    and a question.
    """
    wanted = _significant(request)
    if not wanted:
        return []

    def shares(word: str, other: str) -> bool:
        # Whole words, or one a prefix of the other once both are long enough to mean
        # something. That is enough for "semiconductor" to reach `advanced_semiconductors`
        # and "hypersonics" to reach `hypersonic_strike`, which are the near-misses a
        # staff writing prose actually produces, without opening the door to edit
        # distance -- where "housing" and "shipyards" start looking related.
        return word == other or (
            min(len(word), len(other)) >= 5
            and (word.startswith(other) or other.startswith(word))
        )

    scored = []
    for entry in entries:
        vocabulary = _significant(entry.id) | _significant(entry.name)
        scored.append(
            (sum(any(shares(w, v) for v in vocabulary) for w in wanted), entry)
        )
    best = max((score for score, _ in scored), default=0)
    return [entry for score, entry in scored if score and score == best]


def _build_project(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Begin a public work somewhere the nation holds.

    Read from the order's own field first and its prose second, for the same reason
    ``raise_units`` does: a player who wrote "start a housing programme in the north"
    should not lose it to a schema field the parser left empty.
    """
    nation = _nation(conn, nation_id)
    if nation is None:
        return

    named = str(action.get("project") or "").strip()

    # **Warheads share the queue too**, and for the same reason works and research do: a
    # country choosing between another warhead and a steelworks is precisely the decision
    # one queue exists to pose. Checked before the catalogues because it is an exact id and
    # they are fuzzy resolvers -- letting "warhead" fall through to a near-miss against a
    # technology name is how it would quietly become something else.
    if named.lower() in (WARHEAD_PROJECT, TACTICAL_PROJECT):
        from .warheads import order_warhead

        report = order_warhead(
            conn, world_id, nation_id=nation_id, turn=turn, date=date,
            tactical=named.lower() == TACTICAL_PROJECT,
            # The same ordinal the gambit path takes, for the same reason: the second
            # identical order of a turn is a second decision and needs its own row.
            repeat=int(action.get(_REPEAT) or 0))
        effects.problems.extend(report.problems)
        for entry in report.commissioned:
            effects.headlines.append(entry)
            effects.applied += 1
        return

    # **Through the one boundary, both vocabularies.** The field and the prose are each
    # offered to the resolver rather than substring-matched here: `entry.id in haystack`
    # accepted any sentence that happened to contain "rail", and a display name the player
    # actually read -- *All-welded construction* -- matched nothing at all unless they
    # typed it in lower case. See :mod:`atlas.engine.reference`.
    # Research programmes share the order and the queue with public works, because what a
    # country does with the next seven years is one decision rather than three.
    tech_ref = _named_or_mentioned(conn, world_id, "technology", action, named)
    catalogue = catalogue_for(conn, world_id)
    programme = next((t for t in catalogue if t.id == tech_ref.value), None)
    if programme is not None:
        report = order_research(
            conn, world_id, nation_id=nation_id, tech_id=programme.id, turn=turn, date=date
        )
        effects.problems.extend(report.problems)
        for entry in report.started:
            effects.headlines.append(f"{entry} begun.")
            effects.applied += 1
        return

    project_ref = _named_or_mentioned(conn, world_id, "project", action, named)
    chosen = next(
        (project.id for project in CIVIL_PROJECTS if project.id == project_ref.value),
        None,
    )
    if chosen is None:
        # **Three different failures, told apart.** PT20 refused a Chinese build order
        # with "was not told which public work or programme to begin" -- but China had
        # said something; it simply did not match. Answering a misheard request as
        # though it were a silent one sends the staff back to fill in a field it had
        # already filled in.
        everything = [*CIVIL_PROJECTS, *catalogue]
        options = ", ".join(entry.id for entry in everything)
        asked = (named or str(action.get("summary") or "")).strip()
        if not asked:
            effects.problems.append(
                f"{nation['name']} was not told which public work or programme to "
                f"begin; name one of: {options}"
            )
            return
        near = _closest(asked, everything)
        if len(near) == 1:
            effects.problems.append(
                f"{nation['name']} asked to begin {asked!r}, which is not a thing this "
                f"world builds. The closest is `{near[0].id}` ({near[0].name}) -- name "
                "it exactly and it will be laid down."
            )
        elif near:
            effects.problems.append(
                f"{nation['name']} asked to begin {asked!r}, which could be any of: "
                + ", ".join(f"`{entry.id}`" for entry in near)
                + ". Name one exactly."
            )
        else:
            effects.problems.append(
                f"{nation['name']} asked to begin {asked!r}, which is not a thing this "
                f"world builds. This era offers: {options}"
            )
        return

    region_id = _home_region(conn, world_id, nation_id, action)
    if region_id is None:
        effects.problems.append(f"{nation['name']} has nowhere to build it")
        return

    report = order_project(
        conn, world_id, nation_id=nation_id, project_id=chosen, region_id=region_id,
        turn=turn, date=date,
    )
    effects.problems.extend(report.problems)
    for entry in report.started:
        effects.headlines.append(f"{entry} begun.")
        effects.applied += 1


def _counterintel(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Work at blinding somebody else's service inside your own country.

    **The other half of espionage, and the door a model reaches it through.** Everything it
    does is computed: how much a turn of work is worth, how far it degrades the rival's
    network, whether it is enough to start feeding them wrong answers, and how much sooner it
    lets the police prove who has been operating. A power orders the effort; it does not get
    to say what the effort achieved.
    """
    target = resolve_nation(conn, world_id, action.get("target_nation"))
    nation = _nation(conn, nation_id)
    if nation is None:
        return
    if not target or target == nation_id:
        effects.problems.append(
            f"{nation['name']}'s security service was not told whose people to look for"
        )
        return

    told = invest_counterintel(
        conn, world_id, nation_id=nation_id, against=target, turn=turn, date=date)
    if told:
        effects.applied += 1
        effects.headlines.append(told)
    else:
        effects.problems.append(
            f"{nation['name']} could not tighten up any further against "
            f"{_nation(conn, target)['name'] if _nation(conn, target) else target}"
        )


def _espionage(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """The player's door onto `intelligence.run_espionage`.

    Resolves what was typed and renders what came back. Everything that *decides* anything
    is in the runner, which the `espionage` undertaking calls too -- so the two seats cannot
    drift apart.
    """
    target = resolve_nation(conn, world_id, action.get("target_nation"))
    if _nation(conn, nation_id) is None:
        return
    report = run_espionage(
        conn, world_id, nation_id=nation_id, target=target or "", turn=turn, date=date)
    effects.problems.extend(report.problems)
    effects.headlines.extend(report.headlines)
    effects.applied += 1 if report.applied else 0


def _fortify(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Dug-in troops hold better. Morale is engine-only, so this is the only way it moves."""
    nation = _nation(conn, nation_id)
    region_id = _home_region(conn, world_id, nation_id, action)
    if nation is None or region_id is None:
        return

    units = conn.execute(
        "SELECT id, morale FROM unit WHERE nation_id = ? AND region_id = ?",
        (nation_id, region_id),
    ).fetchall()
    if not units:
        effects.problems.append(
            f"{nation['name']} has no forces there to dig in"
        )
        return

    region_name = conn.execute(
        "SELECT name FROM region WHERE id = ?", (region_id,)
    ).fetchone()["name"]
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=f"{nation['name']} fortifies its positions in {region_name}.",
        actor_nation_id=nation_id, visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff("unit", unit["id"], "morale", DiffOp.SET,
             min(_MORALE_CEILING, float(unit["morale"]) + _FORTIFY_MORALE),
             reason_event_id=event_id)
        for unit in units
    ]
    _commit(conn, world_id, turn, diffs, effects,
            f"{nation['name']} fortified {region_name}.")


def _disband(conn, world_id, turn, date, action, nation_id, effects) -> None:
    nation = _nation(conn, nation_id)
    if nation is None:
        return
    wanted = _quantity(action)
    # **The manifest advertises `region` and `unit_kind` and this honoured neither**, so
    # "disband the garrison in the north" -- the manifest's own worked example -- stood down
    # the weakest formations anywhere in the country. Found by the Codex review of phase 2.
    # A capability that advertises a target it ignores is the seam the manifest exists to
    # close, pointing the other way.
    clauses = ["nation_id = ?"]
    values: list[object] = [nation_id]
    where = action.get("region") or action.get("from_region")
    if where:
        region_id = resolve_region(conn, world_id, where)
        if region_id is None:
            effects.problems.append(
                f"{nation['name']} has no region called {str(where)!r} to stand down"
            )
            return
        clauses.append("region_id = ?")
        values.append(region_id)
    kind = action.get("unit_kind")
    if kind:
        # Resolved through the boundary when the world has a catalogue to resolve against,
        # and taken literally when it does not -- a world may carry formations of kinds no
        # `unit_template` describes, and the sandbox has no templates at all. What is being
        # filtered is the units this nation actually has, so the honest failure is "no
        # formations of that kind", which the query below produces on its own.
        found = resolve(conn, world_id, "unit_kind", kind)
        clauses.append("kind = ?")
        values.append(found.value or str(kind))

    units = conn.execute(
        f"SELECT id FROM unit WHERE {' AND '.join(clauses)}"  # noqa: S608 - clauses are literals
        " ORDER BY strength LIMIT ?",
        (*values, wanted),
    ).fetchall()
    if not units:
        effects.problems.append(
            f"{nation['name']} has no formations to disband"
            + (f" in {where}" if where else "")
            + (f" of kind {kind}" if kind else "")
        )
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=f"{nation['name']} stands down {len(units)} formation(s).",
        actor_nation_id=nation_id, visibility=Visibility.PUBLIC,
    )
    diffs: list[Diff] = [
        Diff("unit", unit["id"], "__delete__", DiffOp.DELETE, None, reason_event_id=event_id)
        for unit in units
    ]
    diffs.append(
        Diff("nation", nation_id, "manpower", DiffOp.ADJUST,
             _UNIT_MANPOWER_COST * 0.5 * len(units), reason_event_id=event_id)
    )
    _commit(conn, world_id, turn, diffs, effects,
            f"{nation['name']} disbanded {len(units)} formation(s).")


def _quantity(action: dict, default: int = 1, cap: int = 5) -> int:
    """How many, clamped. An order for a thousand divisions is an order for five."""
    try:
        wanted = int(float(action.get("quantity") or default))
    except (TypeError, ValueError):
        wanted = default
    return max(1, min(cap, wanted))


def _home_region(
    conn: sqlite3.Connection, world_id: str, nation_id: str, action: dict
) -> str | None:
    """The region an order is about: the one it names, else the capital, else anything."""
    for key in ("to_region", "from_region"):
        named = resolve_region(conn, world_id, action.get(key))
        if named is not None:
            owner = conn.execute(
                "SELECT owner_nation_id FROM region WHERE id = ?", (named,)
            ).fetchone()
            if owner is not None and owner["owner_nation_id"] == nation_id:
                return named

    row = conn.execute(
        """
        SELECT COALESCE(
                 (SELECT capital_region_id FROM nation WHERE id = ?),
                 (SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ? LIMIT 1)
               ) AS id
        """,
        (nation_id, world_id, nation_id),
    ).fetchone()
    return str(row["id"]) if row and row["id"] else None


#: Which undertakings a player may name in an order.
#:
#: **`nuclear_launch` is deliberately absent.** It has its own deliberate path, and a
#: channel where a parser's reading of a sentence can put a warhead in the air is not a
#: channel that should exist. Everything else in the catalogue is something a staff could
#: reasonably be told to attempt.
PLAYER_GAMBITS = (
    "sabotage", "air_strike", "sting", "bribe", "incite", "expose", "summit", "envoy",
)

#: Every field an order can name an undertaking's ground in, in the order they are read.
#: The map and the air situation write `region`, a "what can I do about that" prefill
#: writes `target_region`, and the parser writes `to_region`/`from_region`.
#:
#: **One list, read by everybody who reads the order.** The handler below, the
#: confirmation that shows the player the strike and the workbench card that pre-checks it
#: each kept their own pair of these, and each pair was a different pair -- so a strike
#: could be confirmed over one province, pre-checked against none and flown at another.
GAMBIT_GROUND_FIELDS: tuple[str, ...] = ("region", "target_region", "to_region", "from_region")

#: What an air strike that names no aim is aimed at. The parser's schema has no aim, so a
#: strike ordered in a sentence has always been flown at the works; the confirmation reads
#: this rather than a default of its own, or it tells the player "forces" about a strike
#: that will bomb the railways.
DEFAULT_STRIKE_AIM = "infrastructure"


def gambit_ground(action: dict) -> object:
    """The first of :data:`GAMBIT_GROUND_FIELDS` the order actually fills, or ``None``."""
    return next((action[name] for name in GAMBIT_GROUND_FIELDS if action.get(name)), None)


def strike_aim(action: dict) -> str:
    """The aim an air strike order will be flown at, as the handler below reads it."""
    return str(action.get("aim") or DEFAULT_STRIKE_AIM).strip().lower()

#: What an order's prose has to contain for a kind to be inferred when the parser named
#: none.
#:
#: **Every phrase here is one that means the undertaking and nothing else.** A wrong guess
#: spends the player's money on something they did not order, so the test for adding a
#: phrase is not *could this mean X* but *could this mean anything but X*. That is why
#: `summit` does not list "meet" and `bribe` does not list "pay": both are ordinary words
#: in orders about other things.
_GAMBIT_WORDS: dict[str, tuple[str, ...]] = {
    "sabotage": ("sabotage", "sabotag", "blow up", "wreck", "derail", "demolish"),
    "air_strike": ("air strike", "airstrike", "bomb", "air raid", "strike from the air"),
    "summit": ("summit", "conference", "peace talks", "sit down with"),
    "envoy": ("envoy", "emissary", "delegation", "send a mission"),
    "bribe": ("bribe", "buy off", "grease", "pay off"),
    "sting": ("sting operation", "entrap", "set a trap for"),
    "incite": ("incite", "foment", "stir up", "provoke unrest", "back the rebels"),
    "expose": ("expose", "leak", "reveal what", "make public what"),
}


def _undertaking_kind(action: dict) -> str:
    """Which undertaking an order names, or ``""`` if it cannot be told.

    The field first, exactly: an id the parser filled in is the least ambiguous thing
    available, and nothing here second-guesses it. Only when it is absent or unknown does
    the prose get read, and then **a phrase must be claimed by exactly one kind** -- an
    order that reads as two undertakings is not an order for the first one in the table.

    Nothing infers `nuclear_launch`, and nothing can: it is not in
    :data:`PLAYER_GAMBITS`, so even an exact `gambit_kind` of `nuclear_launch` falls
    through to the refusal. See the design note.
    """
    named = str(action.get("gambit_kind") or "").strip().lower().replace(" ", "_")
    if named in PLAYER_GAMBITS:
        return named

    haystack = f"{named} {action.get('summary') or ''}".lower()
    claimed = [
        kind for kind, words in _GAMBIT_WORDS.items()
        if kind in PLAYER_GAMBITS and any(word in haystack for word in words)
    ]
    return claimed[0] if len(claimed) == 1 else ""


def _gambit(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Route an order the player named an undertaking in, into the gambit channel.

    **The design gap a real-provider run found.** "Sabotage Karthian railways" was parsed
    as `espionage`, and the engine dutifully reported that the agents had learned nothing
    -- true of espionage, and not remotely what was ordered. The gambit catalogue has had
    a `sabotage` kind and an `air_strike` kind the whole time; there was simply no way for
    a *player* to reach them, because the channel only ever offered them to tracked
    nations.

    The engine still decides everything that matters. This assembles the same payload the
    model-facing channel produces and hands it to the same :func:`apply_gambit`, so the
    odds, the stake, the ledger cap and the roll are identical whoever asked -- the player
    chooses the undertaking and the target, and nothing else.
    """
    from .gambit import apply_gambit

    nation = _nation(conn, nation_id)
    if nation is None:
        return

    named = _undertaking_kind(action)
    if named not in PLAYER_GAMBITS:
        effects.problems.append(
            f"{nation['name']} was not told which undertaking to attempt; name one of "
            + ", ".join(PLAYER_GAMBITS)
        )
        return

    # Through the one boundary, like every other reference. An ambiguous target is told
    # apart from an absent one, because "which Northmark did you mean" is actionable and
    # "no target" is not.
    target_ref = reference.resolve(conn, world_id, "nation", action.get("target_nation"))
    if target_ref.ambiguous:
        effects.problems.append(f"{nation['name']}: {target_ref.problem}")
        return
    # **Every field an order can name its ground in.** The map and the air situation
    # write `region`, a "what can I do about that" prefill writes `target_region`, and the
    # parser writes `to_region`/`from_region`. Reading only the last two refused, as
    # unaimed, every strike the confirmation had just shown the player aimed at a province.
    for field_name in GAMBIT_GROUND_FIELDS:
        region_ref = reference.resolve(conn, world_id, "region", action.get(field_name))
        if region_ref.ok:
            break
    target, region = target_ref.value, region_ref.value

    # The aim the player confirmed, not one decided here. Checked against the closed
    # vocabulary before the stake is reckoned, so a word the strike cannot act on is a
    # refusal and not a paid-for failure. Absent, it keeps the parser's old default.
    aim = None
    if named == "air_strike":
        from .air import STRIKE_AIMS

        aim = strike_aim(action)
        if aim not in STRIKE_AIMS:
            effects.problems.append(
                f"{nation['name']}: an air strike cannot be aimed at {aim!r}; aim it at "
                + " or ".join(f"`{name}`" for name in STRIKE_AIMS)
            )
            return
    outcome = apply_gambit(
        conn, world_id, nation_id=nation_id,
        payload={
            "attempt": {
                "kind": named,
                "target": target,
                "target_region": region,
                "aim": aim,
                "intent": str(action.get("summary") or "")[:300],
                "stakes": "minor",
                "evidence": [],
                "on_success": "", "on_failure": "",
            },
            "refused": None,
        },
        turn=turn, date=date, seed=_world_seed(conn, world_id),
        # Two things a repeated order was getting for free, and both are the same mistake
        # -- treating each order as if it were the only one of the turn. `index` reaches
        # the roll's stream key, so without it three attempts drew one number; `ledger` is
        # the per-turn magnitude ceiling, so without it three attempts each got the whole
        # of it.
        index=int(action.get(_REPEAT) or 0),
        ledger=effects.gambit_ledger,
        # A season's scheming costs a season's money and moves a season's worth, for the
        # player exactly as for everybody else. See `OrderEffects.span`.
        span=effects.span,
    )
    effects.problems.extend(outcome.problems)
    if outcome.refused:
        effects.problems.append(f"{nation['name']}: {outcome.refused}")
        return
    if outcome.truce_broken:
        effects.headlines.append(outcome.truce_broken)
    if outcome.narrative:
        effects.headlines.append(outcome.narrative)
    if outcome.applied:
        effects.applied += 1


def _world_seed(conn, world_id: str) -> int:
    row = conn.execute(
        "SELECT rng_seed FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    return int(row["rng_seed"]) if row else 0


def _sue_for_peace(  # noqa: PLR0913 - the handler signature every order handler has
    conn: sqlite3.Connection,
    world_id: str,
    turn: int,
    date: str,
    action: dict,
    nation_id: str,
    effects: OrderEffects,
) -> None:
    """Offer terms and find out whether the other side signs.

    **The capability that was advertised and executed by nothing.** `sue_for_peace` has had
    a full entry in the manifest since the manifest existed, the advisor recommends it in
    as many words, and `_HANDLERS` had no key for it -- so every player who took the advice
    got back *nothing in the engine executes a 'sue_for_peace' order*. Only the world
    simulator could ever end a war.

    The terms are the player's proposal and the engine's decision: what cannot be carried
    out is refused with a reason, and whether the other side accepts is
    `atlas.engine.peace.acceptance`, which reads the war rather than the request.
    """
    from .peace import settle

    target = resolve_nation(conn, world_id, action.get("target_nation"))
    if not target:
        effects.problems.append(
            f"there is no nation called {action.get('target_nation')!r} to make peace with")
        return
    if action.get("ceasefire"):
        from .armistice import ARMISTICE_MONTHS, offer_ceasefire

        truce = offer_ceasefire(
            conn, world_id, nation_id=nation_id, enemy_id=target,
            months=int(action.get("quantity") or 0) or ARMISTICE_MONTHS,
            turn=turn, date=date)
        if not truce.agreed:
            effects.problems.append(
                truce.refused[0] if truce.refused else f"{target} would not stop fighting")
            return
        effects.applied += 1
        # **The day, not the turn number.** A ceasefire runs for a number of months of world
        # time, so "to turn 9" means one thing at monthly pacing and something four times
        # longer at seasonal -- and it is the *date* the two governments agreed to. The turn
        # is still there for a world too old to have a date.
        until = truce.expires_on or f"turn {truce.expires_turn}"
        # A truce shortened to the ceiling is carried, and said -- in the headline rather
        # than in `problems`, because `apply_order_effects` reads anything a handler appends
        # to `problems` as *this order's* refusal, whether it was appended before the order
        # succeeded or after. A truce that stands is not a refused order.
        effects.headlines.append(
            f"A ceasefire with {target} until {until}. The war goes on."
            + ("".join(f" ({note})" for note in truce.notes)))
        return

    outcome = settle(
        conn, world_id, proposer=nation_id, target=target,
        terms=action.get("terms") or (), turn=turn, date=date)
    if not outcome.signed:
        effects.problems.append(
            outcome.refused[0] if outcome.refused else f"{target} would not settle")
        return
    effects.applied += 1
    effects.headlines.append(
        f"Peace with {target}"
        + (": " + "; ".join(outcome.applied[:4]) if outcome.applied else "."))
    effects.headlines.extend(outcome.facts[:3])


def _adopt_doctrine(conn, world_id, turn, date, action, nation_id, effects) -> None:
    """Begin changing what this country is built to do.

    The player's door onto `engine.doctrines.adopt`; a tracked nation's is the
    `doctrine_shift` channel on its batch. **One function does the checking and the pricing
    for both**, so the two doors cannot come to disagree about what a shift costs.
    """
    from .doctrines import adopt, axis_of_stance, offered_to

    nation = _nation(conn, nation_id)
    if nation is None:
        return
    written = str(action.get("doctrine") or "").strip()
    if not written:
        offered = offered_to(conn, world_id, nation_id)
        effects.problems.append(
            "an order to change doctrine has to say which one"
            + (" -- " + ", ".join(stance.id for stance in offered) if offered else ""))
        return

    axis = axis_of_stance(written)
    if axis is None:
        from .doctrines import stance_ids

        effects.problems.append(
            f"{written!r} is not a doctrine; the ones there are: " + ", ".join(stance_ids()))
        return

    outcome = adopt(conn, world_id, nation_id=nation_id, axis=axis.id, stance=written,
                    turn=turn, date=date)
    if not outcome.ok:
        effects.problems.append(f"{nation['name']}: {outcome.rendered()}")
        return
    effects.headlines.append(outcome.described)


_HANDLERS = {
    "economic_investment": _invest,
    "build_project": _build_project,
    "espionage": _espionage,
    "counterintelligence": _counterintel,
    "gambit": _gambit,
    "raise_units": _raise_units,
    "reinforce": _reinforce,
    "fortify": _fortify,
    "disband_units": _disband,
    "sue_for_peace": _sue_for_peace,
    "adopt_doctrine": _adopt_doctrine,
}
