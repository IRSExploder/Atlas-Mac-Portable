"""Why a number is what it is, and what would change it.

**Nothing here computes anything new.** Every figure is re-derived by calling the same
function the engine calls, with the same constants, and the only thing this module adds is
*naming the terms* -- the engine already knew that a coup risk is five weighted squares and
a loyalty discount, and threw the breakdown away the moment it returned the total.

Two values to start with, chosen because they are the two a player most often disbelieves:

* **why a peace will not be accepted** -- the cost against the tolerance, and what moved the
  tolerance: position, exhaustion, and the nationalists.
* **why a coup is likely** -- which of stability, legitimacy, the officer corps, the
  establishment and the street is actually driving it.

Each reason carries what it is worth *and what would move it*, because a breakdown that only
apportions blame answers half the question. The "what would change it" is derived, never
invented: it names the input, and the input is a number some other layer owns.

Read-only. Every function here is a `SELECT` and some arithmetic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

__all__ = [
    "Reason",
    "Why",
    "why_a_bargaining_position",
    "why_a_coup",
    "why_a_peace_is_refused",
    "why_supply_stops_here",
    "why_the_sky_changed_a_battle",
    "why_they_are_suspected",
    "why_this_war_is_exhausting",
]


@dataclass(frozen=True, slots=True)
class Reason:
    """One term of an answer: what it is, what it contributes, and what would move it."""

    what: str
    #: The term's own contribution, in the units of the thing being explained.
    worth: float
    #: The state that produced it, in the words a briefing uses.
    because: str
    #: What would change it -- named, not invented. Empty when nothing the player controls
    #: would, which is an answer worth giving plainly.
    lever: str = ""

    def rendered(self) -> str:
        line = f"{self.what}: {self.worth:+.3f} -- {self.because}"
        return f"{line} ({self.lever})" if self.lever else line


@dataclass(frozen=True, slots=True)
class Why:
    """An answer: the value, the rule that decided it, and the terms that made it."""

    value: float
    verdict: str
    #: The engine's own vocabulary for the gate that decided this, so a reader can find it.
    rule: str = ""
    reasons: list[Reason] = field(default_factory=list)

    def rendered(self) -> list[str]:
        head = [f"{self.verdict}", f"  [{self.rule}]" if self.rule else ""]
        return [line for line in head if line] + [
            "  " + reason.rendered()
            for reason in sorted(self.reasons, key=lambda r: -abs(r.worth))
        ]


def why_a_coup(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> Why:
    """What is actually driving this government's chance of being removed.

    **Re-derived from `leaders.coup_risk`'s own inputs and constants**, so the terms sum to
    the number the engine uses. A separate model of the same thing would drift, and the
    drift would show up as a screen that disagreed with the coup that happened.
    """
    from .constituencies import RESTING as CIVIC_RESTING
    from .constituencies import support_of
    from .leaders import (
        _ANGER_BELOW,
        _BASE_RISK,
        _COUP_TRAP,
        _DANGER_BELOW,
        _LOYALTY_RELIEF,
        _MAX_RISK,
        _RISK_FROM_DISOWNED,
        _RISK_FROM_ILLEGITIMACY,
        _RISK_FROM_INSTABILITY,
        _RISK_FROM_MUTINY,
        _RISK_FROM_STREETS,
        coup_risk,
        leader_of,
    )

    row = conn.execute(
        "SELECT stability, legitimacy FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    leader = leader_of(conn, nation_id)
    if row is None or leader is None:
        return Why(value=0.0, verdict="nobody governs here, so nobody can be removed")

    stability = float(row["stability"] or 0.0)
    legitimacy = float(row["legitimacy"] or 0.0)
    risk = coup_risk(conn, nation_id, stability=stability, legitimacy=legitimacy)

    def fed_up(support: float) -> float:
        return max(0.0, (_ANGER_BELOW - min(_ANGER_BELOW, support)) / _ANGER_BELOW)

    shaky = max(0.0, (_DANGER_BELOW - min(_DANGER_BELOW, stability)) / _DANGER_BELOW)
    lawless = max(0.0, (_DANGER_BELOW - min(_DANGER_BELOW, legitimacy)) / _DANGER_BELOW)
    army = support_of(conn, nation_id, "army")
    establishment = support_of(conn, nation_id, "establishment")
    people = support_of(conn, nation_id, "people")

    reasons = [
        Reason("the base rate", _BASE_RISK,
               "every government carries some chance of falling", ""),
        Reason("instability", _RISK_FROM_INSTABILITY * shaky * shaky,
               f"stability {stability:.0f}, against a danger line of {_DANGER_BELOW:.0f}",
               "anything that steadies the country" if shaky else ""),
        Reason("illegitimacy", _RISK_FROM_ILLEGITIMACY * lawless * lawless,
               f"legitimacy {legitimacy:.0f}, against {_DANGER_BELOW:.0f}",
               "a signature kept, or a mandate renewed" if lawless else ""),
        Reason("a mutinous army", _RISK_FROM_MUTINY * fed_up(army) * fed_up(army),
               f"the officer corps stands at {army:.0f}",
               "mobilisation, pay, or a war they believe in" if fed_up(army) else ""),
        Reason("a disowning establishment",
               _RISK_FROM_DISOWNED * fed_up(establishment) * fed_up(establishment),
               f"the establishment stands at {establishment:.0f}",
               "agreements kept and money paid" if fed_up(establishment) else ""),
        Reason("the street", _RISK_FROM_STREETS * fed_up(people) * fed_up(people),
               f"the people stand at {people:.0f}",
               "peace, housing, health, land" if fed_up(people) else ""),
    ]
    # **The multiplications, each as the difference it actually made.** `coup_risk` sums
    # the terms above and then scales the total three times over -- a loyal army, whatever
    # a scenario has pinned, and the trap a government that seized power is in -- before
    # capping it. A breakdown that listed only the additive half would not add up to the
    # number beside it, and a reader who checked would be right and the screen wrong.
    #
    # So each scaling is reported as `after - before`, in the order the engine applies
    # them, and the whole list sums to the value exactly. `test_the_terms_sum_to_the_number
    # _the_engine_uses` holds that.
    from .leaders import modifier

    running = sum(reason.worth for reason in reasons)
    relief = _LOYALTY_RELIEF * max(0.0, (army - CIVIC_RESTING) / 50.0)
    if relief:
        after = running * (1.0 - relief)
        reasons.append(Reason(
            "a loyal army", after - running,
            f"the officer corps at {army:.0f} is above its resting point of "
            f"{CIVIC_RESTING:.0f}",
            "the people best placed to mount a coup can also decline to"))
        running = after

    scenario = modifier(conn, nation_id, "coup_risk")
    if scenario != 1.0:
        after = running * scenario
        reasons.append(Reason(
            "this era's own weighting", after - running,
            f"the scenario multiplies coup risk here by {scenario:.2f}",
            "nothing you do: it is a fact about the period"))
        running = after

    if leader.came_to_power in ("coup", "revolution"):
        after = running * _COUP_TRAP
        reasons.append(Reason(
            "how this government arrived", after - running,
            f"it came to power by {leader.came_to_power}",
            "nothing: a government that seized power is easier to seize from"))
        running = after

    if abs(risk - running) > 1e-9:
        reasons.append(Reason(
            "the ceiling", risk - running,
            f"no government is given worse than a {_MAX_RISK:.0%} chance in one year",
            "the risk is already as bad as this layer will report"))

    return Why(
        value=risk,
        verdict=f"{nation_id} faces a {risk:.1%} chance of losing its government this year",
        rule="coup_risk",
        reasons=[reason for reason in reasons if abs(reason.worth) > 1e-9])


def why_a_peace_is_refused(
    conn: sqlite3.Connection, world_id: str, *, offered_to: str, offered_by: str, terms,
) -> Why:
    """Why this offer will not be signed, in the terms `peace.acceptance` decided it on.

    The one number a player disbelieves most, because a refusal reads as stubbornness when
    the reasoning is not shown: the side being asked is weighing what it is being asked to
    give against how much it can bear, and both halves move.
    """
    from .leverage import leverage_between
    from .peace import acceptance, review_terms
    from .wareffort import effort_between

    verdict = acceptance(conn, world_id, offered_to=offered_to, offered_by=offered_by,
                         terms=terms)
    cards = [card for card in review_terms(
        conn, world_id, belligerents=(offered_to, offered_by), terms=terms) if card.ok]
    effort = effort_between(conn, world_id, offered_to, offered_by)
    exhaustion = effort.exhaustion if effort else 0.0
    position = leverage_between(conn, world_id, offered_to, offered_by).position

    reasons = [
        Reason("what you are asking for", verdict.cost,
               ", ".join(card.term.describe() for card in cards
                         if card.term.obliged == offered_to) or "nothing",
               "ask for less, or ask for something cheaper to give"),
        Reason("what they can bear", verdict.tolerance,
               f"position {position:+.2f}, exhaustion {exhaustion:.0f}",
               "beat them harder, or wait for the war to cost them more"),
        Reason("their position", position,
               "how the war is actually going for them",
               "take ground, or make the fighting cost them"),
        Reason("how tired of it they are", exhaustion,
               "what the war has cost them so far",
               "time, and casualties they cannot replace"),
    ]
    if verdict.stiffening:
        reasons.append(Reason(
            "their nationalists", -verdict.stiffening * verdict.tolerance,
            "the offer is a humiliation and the patriots are loud",
            "drop the humiliating clause, or beat them until the patriots go quiet"))

    return Why(
        value=verdict.cost - verdict.tolerance,
        verdict=(f"{offered_to} would sign this" if verdict.accepted
                 else f"{offered_to} will not sign this: {verdict.because}"),
        rule="acceptance",
        reasons=reasons)


def why_a_bargaining_position(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, enemy_id: str
) -> Why:
    """What this side's position in this war is made of.

    **The one value that already carried its own breakdown.** `leverage_between` has always
    returned four `Component`s, each with its weight and a sentence saying what it is about;
    the total was the only part anybody looked at, and the four were thrown away by every
    caller. So this is a translation rather than a derivation, and it is the shortest
    function in this module for exactly that reason.

    The terms sum to the position by construction -- `position` *is* the sum of
    `contribution` -- and the test holds that rather than trusting it.
    """
    from .leverage import leverage_between

    standing = leverage_between(conn, world_id, nation_id, enemy_id)
    #: What would move each of the four, named rather than invented: every one is an input
    #: some other layer owns, and none of it is advice.
    levers = {
        "ground": "take ground, or push them off yours",
        "weariness": "time, casualties they cannot replace, and a treasury of theirs "
                     "that cannot pay",
        "force": "raise more, or destroy more of theirs",
        "aims": "meet an aim you declared, or make one of theirs unreachable",
    }
    return Why(
        value=standing.position,
        verdict=(f"{nation_id} is {standing.band} against {enemy_id} "
                 f"({standing.position:+.2f})"),
        rule="leverage",
        reasons=[
            Reason(component.name, component.contribution,
                   component.because or f"{component.value:+.2f} at weight "
                                        f"{component.weight:.2f}",
                   levers.get(component.name, ""))
            for component in standing.components
        ])


def why_this_war_is_exhausting(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, enemy_id: str
) -> Why:
    """What this month of this war is costing this side's patience, term by term.

    Re-derived from `wareffort._charge`'s own inputs and constants, in the order it applies
    them, so the terms sum to the charge the engine would make. The multiplication --
    fragility, which is what a shaky country's war costs it over and above a settled one's
    -- is reported as the difference it made, for the reason `why_a_coup` reports its three
    that way: a breakdown listing only the additive half does not add up to its own total.
    """
    from .pacing import compounded, span_of
    from .wareffort import (
        CASUALTY_WEIGHT,
        FRAGILITY,
        OCCUPIED_CEILING,
        OCCUPIED_DRIFT,
        SHORTAGE_WEIGHT,
        TIME_CEILING,
        TIME_DRIFT,
        effort_between,
    )

    nation = conn.execute(
        "SELECT name, treasury, stability FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    if nation is None:
        return Why(value=0.0, verdict=f"there is no {nation_id} to be tired")

    effort = effort_between(conn, world_id, nation_id, enemy_id)
    standing = effort.exhaustion if effort else 0.0
    army = float(conn.execute(
        "SELECT COALESCE(SUM(strength), 0) AS total FROM unit"
        "  WHERE world_id = ? AND nation_id = ?", (world_id, nation_id)
    ).fetchone()["total"])
    homeland = int(conn.execute(
        "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean'", (world_id, nation_id)).fetchone()["n"])
    occupied = int(conn.execute(
        "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND controller_nation_id = ?", (world_id, nation_id, enemy_id)
    ).fetchone()["n"])

    span = span_of(30)
    share = (occupied / homeland) if homeland else 0.0
    broke = SHORTAGE_WEIGHT if float(nation["treasury"]) <= 0 else 0.0
    reasons = [
        Reason("the fighting itself",
               max(0.0, TIME_CEILING - standing) * compounded(TIME_DRIFT, span),
               f"a war that is merely going on wears a country to about "
               f"{TIME_CEILING:.0f} and no further"
               + (", and this one is already past that" if standing >= TIME_CEILING
                  else ""),
               ""),
        Reason("ground of yours they hold",
               max(0.0, OCCUPIED_CEILING * share - standing) * compounded(
                   OCCUPIED_DRIFT, span),
               f"{occupied} of {homeland} home province(s) under their boot"
               if homeland else "no homeland to lose",
               "take it back" if occupied else ""),
    ]
    if broke:
        reasons.append(Reason(
            "a treasury that cannot pay", broke * span,
            "the war is being fought on arrears",
            "anything that puts money in the treasury"))
    # Casualties are charged from what the ledger has not charged yet, and are deliberately
    # *not* scaled by the span: the dying already happened over it. See `wareffort._charge`.
    fresh = max(0.0, (effort.casualties - effort.charged_casualties) if effort else 0.0)
    if fresh:
        reasons.append(Reason(
            "casualties not yet felt", CASUALTY_WEIGHT * (fresh / max(1.0, army + fresh)),
            f"{fresh:.0f} strength lost and not yet charged",
            "nothing: it has already happened"))

    running = sum(reason.worth for reason in reasons)
    fragility = 1.0 + FRAGILITY * (1.0 - float(nation["stability"]) / 100.0)
    if fragility != 1.0 and running > 0:
        reasons.append(Reason(
            "how shaky the country already is", running * fragility - running,
            f"stability {float(nation['stability']):.0f}",
            "steady the country and the same war costs less"))

    charge = sum(reason.worth for reason in reasons)
    return Why(
        value=charge,
        verdict=(f"{nation['name']} stands at {standing:.0f} exhaustion against "
                 f"{enemy_id}, and this month adds {charge:+.1f}"),
        rule="war_effort",
        reasons=[reason for reason in reasons if abs(reason.worth) > 1e-9])


def why_they_are_suspected(
    conn: sqlite3.Connection, world_id: str, *, victim_id: str, event_id: int
) -> Why:
    """Why the file on one covert act stands where it does, and what would thicken it.

    **A tier is a threshold on a number that accumulates**, and the number is a contest:
    what the victim's counterintelligence can do against what the actor's tradecraft can
    hide. Both halves are readable, both are the *effective* figures the engine itself uses,
    and neither is invented here.

    From the victim's seat only, which is the scoping `attributions_for` already runs on:
    what a country has worked out is its own, and an unscoped reader would be the leak.
    """
    from .attribution import (
        LIKELY_AT,
        NO_TIER,
        PROVEN_AT,
        SUSPECTED_AT,
        attributions_for,
        counterintel_of,
        tier_of,
        tradecraft_of,
    )

    found = next((a for a in attributions_for(conn, world_id, victim_id)
                  if a.event_id == event_id), None)
    if found is None:
        return Why(value=0.0, verdict=f"{victim_id} has no file on that act at all",
                   rule="attribution")

    theirs = tradecraft_of(conn, world_id, found.actor_id, against=victim_id)
    mine = counterintel_of(conn, world_id, victim_id, against=found.actor_id)
    next_at = next((bound for bound in (SUSPECTED_AT, LIKELY_AT, PROVEN_AT)
                    if bound > found.confidence), None)
    reasons = [
        Reason("what your services can do", mine,
               f"counterintelligence against {found.actor_id}, effective",
               "invest in counterintelligence, or hunt their people at home"),
        Reason("what theirs can hide", -theirs,
               f"the tradecraft of {found.actor_id} against you, effective",
               "nothing you control: it is their investment, not yours"),
        Reason("how long the file has been open",
               float(found.last_turn - found.opened_turn),
               f"opened on turn {found.opened_turn}, last moved on turn {found.last_turn}",
               "time, while the trail is still warm"),
    ]
    if next_at is not None:
        reasons.append(Reason(
            "how far from the next tier", next_at - found.confidence,
            f"{found.confidence:.0f} now, and {next_at:.0f} would make it "
            f"{tier_of(next_at)}",
            "more evidence, at the rate the contest allows"))

    said = ("nothing is pinned on anybody" if found.tier == NO_TIER
            else f"{found.actor_id} is {found.tier}")
    return Why(
        value=found.confidence,
        verdict=f"{said} for that act, at {found.confidence:.0f} confidence",
        rule="attribution",
        reasons=reasons)


def why_the_sky_changed_a_battle(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    region_id: str,
    attacker: str,
    defender: str,
) -> Why:
    """What the air over one theatre was worth to the fighting under it.

    **The air layer was the largest thing in the engine with no entry here.** A battle fought
    under an enemy-held sky was resolved with a signed term on the odds and a multiplier on
    the advance, both of them real, neither of them anywhere a player could read -- so a
    front that went badly for a reason the player could have fixed read as a front that went
    badly.

    Re-derived through :func:`atlas.engine.air.superiority_term` and
    :func:`atlas.engine.air.advance_multiplier` -- the same two calls
    :func:`atlas.sim.resolve.compute_pressure` and the resolution phase make -- so the terms
    here are the terms that were applied, not a second opinion about them.

    ``value`` is the odds term alone, because that is the number the contest read. The
    advance is reported beside it as its own reason with its own units: they are two effects
    of one fact and adding them would produce a total that means nothing.
    """
    from .air import _CONTROL_FLOOR, advance_multiplier, control_of, superiority_term

    holder, margin = control_of(conn, region_id)
    term = superiority_term(conn, region_id, attacker, defender)
    advance = advance_multiplier(conn, region_id, attacker)
    row = conn.execute(
        "SELECT name, kind FROM region WHERE id = ?", (region_id,)).fetchone()
    where = str(row["name"]) if row else region_id
    at_sea = bool(row) and str(row["kind"]) == "ocean"

    if holder is None or margin < _CONTROL_FLOOR:
        return Why(
            value=0.0,
            verdict=f"the sky over {where} was nobody's, and changed nothing",
            rule="air.superiority_term",
            reasons=[Reason(
                "air superiority", 0.0,
                f"margin {margin:.2f} is under the dead band of {_CONTROL_FLOOR:.2f}",
                "put more aircraft within reach of it than they can")])

    # **Named, not id'd.** This entry reaches a player surface as well as the console, and
    # `karthos holds it at a margin of 0.60` is the engine talking to itself. The ids are
    # still what was typed to ask the question.
    def named(nation_id: str) -> str:
        found = conn.execute(
            "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
        return str(found["name"]) if found else nation_id

    whose = "the attacker" if holder == attacker else (
        "the defender" if holder == defender else "a third power")
    reasons = [
        Reason(
            "air superiority", term,
            f"{named(holder)} holds it at a margin of {margin:.2f} — {whose}"
            + (", and this is open water, where it is worth more" if at_sea else ""),
            "hold it yourself, or fly enough here that neither of you does"),
    ]
    if advance > 1.0:
        reasons.append(Reason(
            "the advance it bought", advance - 1.0,
            f"a battle won here carries {advance - 1.0:+.0%} further, because the "
            "attacker owns the sky over it",
            "the same sky; this is the second half of holding it"))
    # Named even at zero, because *the advance bonus is the attacker's own sky* is the part
    # of this rule a player is most likely to have got backwards.
    elif holder == defender:
        reasons.append(Reason(
            "the advance it bought", 0.0,
            "none: the advance bonus is paid on the attacker's own sky, and this one is "
            "the defender's",
            "take the sky before the ground"))

    return Why(
        value=term,
        verdict=(f"the sky over {where} was worth {term:+.3f} to {named(attacker)}'s odds "
                 f"against {named(defender)}"),
        rule="air.superiority_term",
        reasons=reasons)


def why_supply_stops_here(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, region_id: str
) -> Why:
    """Whether supply reaches this province, and what is eating the budget if it does not.

    **The interdiction surcharge was described to nobody.** It is two hops of a three-hop
    budget -- an interdicted corridor runs out in a third of the distance a clear one does --
    and neither the player's surfaces nor the model's air rules block mentioned it. A
    quartermaster's answer that does not name it is not an answer.

    Every term is read out of :mod:`atlas.engine.logistics`' own constants and its own walk.
    The counterfactual -- what the corridor would be with the skies clear -- comes from
    :func:`atlas.engine.airsituation.interdiction_against`, which walks the same graph twice
    and is pinned equal to the engine's walk when nothing is interdicted.
    """
    from .airsituation import interdiction_against
    from .logistics import _INTERDICTION_COST, _SEA_COST, _SUPPLY_RANGE, supply_map

    reach = supply_map(conn, world_id, nation_id)
    row = conn.execute(
        "SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
    where = str(row["name"]) if row else region_id
    distance = reach.get(region_id)
    interdiction = interdiction_against(conn, world_id, nation_id)
    cut = {rid for rid, _name in interdiction.lost}

    reasons = [
        Reason("the budget", float(_SUPPLY_RANGE),
               f"supply carries {_SUPPLY_RANGE} hops from a depot, and a depot is a "
               "province developed enough to be one",
               "develop a province nearer the front, or take one"),
        Reason("a plain land hop", 1.0,
               "ground you hold costs one; ground somebody else holds does not pass supply "
               "at all",
               "hold the ground between the depot and the front"),
        Reason("crossing water", float(_SEA_COST),
               f"a sea lane is longer than the map makes it look, so a crossing costs "
               f"{_SEA_COST} of the {_SUPPLY_RANGE}",
               "a shorter route ashore, if there is one"),
    ]
    if interdiction.active:
        who = ", ".join(name for _id, name in interdiction.by)
        reasons.append(Reason(
            "an enemy-held sky", float(_INTERDICTION_COST),
            f"{who} holds the sky over {len(interdiction.corridors)} province(s) you "
            f"control, and each costs {_INTERDICTION_COST} extra hops to supply through",
            "contest the sky over your own ground, or route around it"))

    if distance is None:
        verdict = f"{where} is out of supply"
        if region_id in cut:
            verdict += " — and would be in supply if those skies were clear"
    else:
        verdict = f"{where} is in supply, {distance} of {_SUPPLY_RANGE} hops out"

    return Why(
        value=float(distance if distance is not None else _SUPPLY_RANGE + 1),
        verdict=verdict,
        rule="logistics.supply_map",
        reasons=reasons)
