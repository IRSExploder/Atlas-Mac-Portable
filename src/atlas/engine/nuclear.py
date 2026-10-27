"""Nuclear weapons: an arsenal, a deterrent, a launch, and a second strike.

Before this module the entire nuclear content of ATLAS was prose -- a sentence in the
Modern Day preset's hard constraints telling the referee that use is close to unthinkable,
two technology names that moved ``military_strength``, and nine power blurbs that mention
warheads nobody had. Nothing in the engine knew who was armed, so nothing could be
deterred and nothing could be fired.

**The design goal is that a nuclear power is harder to fight without anybody ever
launching, and that launching is available, terrible, and almost never correct.** Three
pieces do that, and only the first of them involves a weapon going off:

1. :func:`deterrence` -- a term in :func:`~atlas.sim.resolve.compute_pressure` against the
   *homeland* of a power with a survivable arsenal. It costs one query per contest, it is
   exactly zero on a map with no arsenals (so 1936 is untouched), and it is where most of
   the value is. Russia and Ukraine fight over Ukraine; nobody marches on Moscow.
2. :func:`launch` -- a bounded, adjudicated, seeded attempt, arriving as a *gambit kind*
   rather than as new engine machinery. The stake is warheads and reputation; the roll is
   interception; the effects come from a closed vocabulary the engine applies itself.
3. :func:`second_strike` -- **engine-authored and automatic.** If the target has a
   surviving arsenal, it fires back. No model call, no decision, no chance to reconsider.
   This is the single most important rule here, because it is what stops a first strike
   being a winning move: the attacker does not get to find out whether the defender would
   have dared.

**The model chooses the target, not the consequences.** Every other gambit kind lets the
proposer pick effect verbs from a vocabulary; a launch does not, and that asymmetry is the
whole safety argument for letting this reach columns the gambit allowlist refuses. A
nuclear weapon does what a nuclear weapon does. What a staff decides is whether to use one
and where, which is the decision worth having.

**Everything is a diff, so a rewind is exact.** Warheads spent, warheads destroyed on the
ground, a crushed province, degraded formations, a collapsed reputation and the retaliation
that followed all land as ordinary rows under ``Role.ENGINE``, and undo newest-first like
any battle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .adjacency import graph_of
from .bounds import absorbable
from .budget import strain_pressure
from .intelligence import ARSENAL, Estimate, estimate_of

__all__ = [
    "DELIVERY_TIERS",
    "MAX_WARHEADS_PER_LAUNCH",
    "NUCLEAR_CAPS",
    "Arsenal",
    "LaunchOutcome",
    "READINESS_DECAY",
    "READINESS_FLOOR",
    "READINESS_RECOVERY",
    "arsenal_of",
    "deliverable_regions",
    "drift_readiness",
    "deterrence",
    "estimated_retaliation",
    "launch",
    "nuclear_balance_lines",
    "nuclear_rules_block",
    "penetration_odds",
    "second_strike",
    "strike_region_for",
    "surviving_warheads",
    "TACTICAL_WEIGHT",
    "tactical_edge",
    "survivability",
    "within_delivery_range",
]


# --------------------------------------------------------------------------- the arsenal


@dataclass(frozen=True, slots=True)
class Arsenal:
    nation_id: str
    warheads: int
    delivery: str
    #: How well the force works, 0.25 to 1.0. Never how much of it there is -- see
    #: :func:`drift_readiness`. Defaults to fully ready so any caller that builds an
    #: `Arsenal` by hand gets the behaviour that existed before the column did.
    readiness: float = 1.0

    @property
    def armed(self) -> bool:
        return self.warheads > 0 and self.delivery != "none"


@dataclass(frozen=True, slots=True)
class DeliveryTier:
    """What a delivery tier is worth, in the two things that matter about one.

    ``survival`` is the share of an arsenal that cannot be caught on the ground -- boats
    at sea, road-mobile launchers, aircraft already dispersed. ``penetration`` is the base
    chance of getting through what the target has to stop it with.
    """

    survival: float
    penetration: float
    blurb: str


#: Five tiers, and the count is still deliberate: these are the distinctions that change
#: what a first strike can accomplish against you, and a longer list would be airframes.
#:
#: **``sea`` is the one added last, at schema 19, and it is the honest answer to a question
#: the other four could not take.** A deterrent carried only by submarines -- Britain's
#: from 1969 -- fitted nowhere. ``triad`` claimed an air leg and a land leg it did not have
#: and read them to the player in those words; ``missile`` claimed a land-based force it
#: never had, and once boats were gated on the tier (see :data:`SEA_LEG_TIERS`) it left the
#: deterrent resting on nothing at all.
#:
#: Its numbers sit where the thing sits. **Survival above ``missile`` and below ``triad``**:
#: a boat on patrol cannot be caught, which is the whole point of one, but a force that is
#: *only* boats has no second basket -- an anti-submarine campaign that finds them takes
#: the deterrent with it, where a triad still has silos. **Penetration between them too**:
#: a submarine-launched missile flies a shorter, flatter path from an unpredictable bearing
#: and is harder to defend against than a land-based one, and the tier that has every
#: option still has the best of it.
DELIVERY_TIERS: dict[str, DeliveryTier] = {
    "none": DeliveryTier(0.0, 0.0, "no means of delivery"),
    "aircraft": DeliveryTier(0.20, 0.55, "gravity bombs on aircraft"),
    "missile": DeliveryTier(0.45, 0.82, "land-based missiles"),
    "sea": DeliveryTier(0.65, 0.85, "ballistic missiles in submarines"),
    "triad": DeliveryTier(0.80, 0.88, "aircraft, land-based missiles and boats at sea"),
}

#: A boat at sea is the classic answer to a first strike, and Modern Day already fields
#: them, so survivability reads the map rather than a stored number: sink the submarines
#: and the deterrent weakens without anybody editing a column.
_SUBMARINE_KIND = "submarine"
_SURVIVAL_PER_BOAT = 0.05
_SURVIVAL_CEILING = 0.95

#: **Whose boats are a deterrent, and whose are just boats.**
#:
#: Every submarine used to count. There is one ``submarine`` unit kind per era, so China's
#: four *attack* boats raised its nuclear survivability from 0.20 to 0.40 -- and the
#: People's Republic had no ballistic-missile submarine at all in 1972; the first went to
#: sea in 1981. Found by Codex reviewing the Cold War era.
#:
#: **The fix is not a template flag, and that is worth writing down because a template flag
#: was the obvious answer and cannot work.** A flag on ``unit_template`` is per *era*: with
#: one submarine kind it makes every boat in 1972 a deterrent or none of them, and the
#: thing that actually differs is the *nation* -- Britain's boats carry missiles and
#: China's do not, in the same year, out of the same template.
#:
#: What already knows that, per nation, is the arsenal's own delivery tier. A power whose
#: deterrent has no sea leg has no missile submarines by construction, so its boats are a
#: navy and nothing more. That is the gate: a hull contributes to *nuclear* survivability
#: only where the arsenal it would be carrying says there is something to carry.
#:
#: Attack submarines keep every naval property they had. This changes what they mean to
#: `nuclear`, and nothing about what they mean to `naval`.
SEA_LEG_TIERS: frozenset[str] = frozenset({"sea", "triad"})

#: What a submarine deterrent is worth with no boat left afloat. Small and not zero: the
#: warheads still exist and a boat alongside is still a boat, but nothing is on patrol and
#: nothing is hidden, so a first strike finds almost all of it. A `triad` in the same
#: position keeps its tier, because its floor *is* the two legs that do not sink.
_SEA_WITHOUT_BOATS = 0.10

#: **How far a tier can actually throw a warhead**, in region hops from the launcher's own
#: ground. ``None`` is intercontinental.
#:
#: Delivery had no geography at all: a nation with a handful of gravity bombs on medium
#: bombers could strike any province on earth it was at war with, so a regional arsenal
#: reached another hemisphere. Codex flagged it; Alex asked for it closed.
#:
#: **Only ``aircraft`` is limited, and that is the whole of the honest version.** A ballistic
#: missile, in a silo or in a boat, *is* intercontinental -- that is what the word means and
#: what the tier is for -- and a submarine goes where the sea goes, so gating either on a
#: land-hop radius would model something that is not true. What is true is that a bomber has
#: a combat radius: it flies from a field, over ground, and comes back. Six hops is generous
#: on both maps and is meant to be -- it says *your own theatre and its neighbours*, not
#: *the far side of the planet*.
#:
#: Measured from every region the launcher **holds**, not from its capital, so forward
#: basing counts: a power that has taken ground closer to its enemy can reach further from
#: it, which is what forward basing is. The walk is the same breadth-first read of
#: ``region_adjacency`` the rest of the engine uses -- no randomness, no stored state, and
#: nothing to rewind, because it decides a refusal rather than writing one.
_TIER_REACH: dict[str, int | None] = {
    "none": 0, "aircraft": 6, "missile": None, "sea": None, "triad": None,
}


def _posture(
    conn: sqlite3.Connection, world_id: str, nation_id: str, lever: str
) -> float:
    """What this nation's nuclear posture does to ``lever``. ``1.0`` when it has none.

    Imported inside the function for the reason `sim.resolve` imports this module inside
    one: doctrines are a layer *on top of* the nuclear content, and a module-level import
    here would invert that. A dict lookup after the first call.
    """
    from .doctrines import factor

    return factor(conn, world_id, nation_id, "nuclear_posture", lever)


def deliverable_regions(
    conn: sqlite3.Connection, world_id: str, *, launcher: str, delivery: str
) -> frozenset[str] | None:
    """Every region this tier could throw a warhead at, or ``None`` for *everywhere*.

    ``None`` rather than the whole map, so the intercontinental case -- which is three of
    the five tiers and every superpower -- costs one dict lookup and no query at all. It is
    also the honest answer: a ballistic missile is not limited to a set of provinces that
    happens to be on this map.

    The walk is the same breadth-first read of ``region_adjacency`` the rest of the engine
    uses, measured from every region the launcher *holds* rather than from its capital, so
    forward basing counts.
    """
    hops = _TIER_REACH.get(delivery)
    if hops is None:
        return None
    if hops <= 0:
        return frozenset()

    held = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ?"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, launcher),
        )
    }
    if not held:
        return frozenset()

    # The third copy of this read, and now the same one: identical query, identical
    # undirected graph, and it was already documented as "the same breadth-first read of
    # region_adjacency the rest of the engine uses". It is that read now rather than a
    # duplicate of it.
    graph = graph_of(conn, world_id)

    seen = set(held)
    frontier = sorted(held)
    for _step in range(hops):
        nxt: list[str] = []
        for region in frontier:
            for neighbour in sorted(graph.get(region, ())):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                nxt.append(neighbour)
        frontier = nxt
        if not frontier:
            break
    return frozenset(seen)


def within_delivery_range(
    conn: sqlite3.Connection, world_id: str, *, launcher: str, region_id: str, delivery: str
) -> bool:
    """Whether ``launcher`` can physically throw a warhead of this tier at ``region_id``.

    One question against the set above, so the refusal the launch path makes and the reach
    the map draws can never disagree -- they are the same walk.
    """
    reach = deliverable_regions(conn, world_id, launcher=launcher, delivery=delivery)
    return True if reach is None else region_id in reach


#: **The floor an unmaintained arsenal falls to and never below.**
#:
#: The obvious way to make maintenance matter is to take warheads off a power that will
#: not pay, and it is the wrong way: a treasury is empty exactly when a country is losing,
#: so non-payment would confiscate a deterrent at the moment one exists for. That is the
#: bug `NO_TREASURY_STAKE` was written to remove from the launch, and putting it back in
#: through the upkeep would be the same mistake wearing a different hat.
#:
#: So neglect degrades how well the force *works* and stops well short of nothing. A
#: quarter is a deliberate quarter: enough that a cornered government always has a
#: last-ditch launch, little enough that letting a deterrent rot is a real loss.
READINESS_FLOOR: float = 0.25

#: How fast readiness moves, per reference month, in each direction.
#:
#: **Recovery is slower than decay, which is the honest asymmetry.** Letting an alert
#: state lapse is quicker than rebuilding one: crews, exercises and certification take
#: longer to restore than to lose. Both are slow enough that a single bad year is a
#: setback rather than a disarmament -- from full readiness it takes about a decade of
#: unbroken arrears to reach the floor.
READINESS_DECAY: float = 0.02
READINESS_RECOVERY: float = 0.012


def drift_readiness(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    span: float = 1.0,
) -> list[str]:
    """Move every arsenal's readiness toward what its owner is paying for.

    **Driven by how strained the budget is, not by whether it broke.** This was written
    against `budget.collect_revenue`'s `in_arrears` -- a government that cannot pay its
    establishments is not paying silo crews either, which is true and useless: across five
    thirty-turn play-test games `in_arrears` fired *zero* times, so the mechanic was
    correct and dormant. `budget.strain_pressure` is the graded reading the morale probe
    uses, and it moves on the map we actually ship.

    Returns one line per arsenal that moved, for the turn report. Written as diffs like
    everything else that has to survive a rewind.
    """
    rows = conn.execute(
        "SELECT nation_id, warheads, readiness FROM arsenal"
        " WHERE world_id = ? AND warheads > 0 ORDER BY nation_id",
        (world_id,),
    ).fetchall()
    if not rows:
        return []

    names = {
        str(r["id"]): str(r["name"]) for r in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    # **Cited to genesis, and not to an event of its own.** Every diff must name the
    # event that caused it (CORE-001), and readiness drifts every single turn for every
    # armed power -- an event apiece would be nine lines of "nothing happened" a turn in
    # a world with nine arsenals. Genesis is engine-authored, public and always citable,
    # which is the fallback `views._founding_event_id` exists for and the same reason.
    genesis = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,),
    ).fetchone()
    if genesis is None:
        return []

    diffs: list[Diff] = []
    news: list[str] = []
    for row in rows:
        nation_id = str(row["nation_id"])
        now = float(row["readiness"])
        # Graded both ways: a budget well under the line recovers at full rate, one over
        # it decays in proportion to how far over. A country exactly at the threshold does
        # neither, which is the honest reading of "it is affordable and no more".
        pressure = strain_pressure(conn, world_id, nation_id)
        starved = pressure > 0.0
        step = ((-READINESS_DECAY * pressure) if starved
                else READINESS_RECOVERY) * max(0.0, span)
        target = max(READINESS_FLOOR, min(1.0, now + step))
        if abs(target - now) < 0.0005:
            continue
        diffs.append(Diff(
            "arsenal", nation_id, "readiness", DiffOp.SET, round(target, 4),
            reason_event_id=int(genesis["id"]),
            visibility_scope=Visibility.SECRET))
        if starved:
            news.append(
                f"{names.get(nation_id, nation_id)}'s deterrent is going unmaintained "
                f"({target:.0%} ready)")
    if not diffs:
        return []
    applied = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
        proposer_model=None, diffs=tuple(diffs)))
    return news if applied.applied else []


def arsenal_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> Arsenal | None:
    """What this nation holds, or ``None`` if it holds nothing.

    Absent rather than zeroed, because "has no nuclear weapons" is the answer for almost
    every nation on every map and a row per nation would be 200 rows of nothing.
    """
    row = conn.execute(
        "SELECT nation_id, warheads, delivery, readiness FROM arsenal"
        " WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id),
    ).fetchone()
    if row is None:
        return None
    return Arsenal(str(row["nation_id"]), int(row["warheads"]), str(row["delivery"]),
                   float(row["readiness"]))


def survivability(conn: sqlite3.Connection, world_id: str, arsenal: Arsenal) -> float:
    """The share of this arsenal that would still exist after being struck first.

    Tier plus boats -- **and only the boats that would be carrying anything.** A power
    whose deterrent has no sea leg (see :data:`SEA_LEG_TIERS`) has no missile submarines
    by construction, so its hulls are a navy and contribute nothing here. A triad power
    cannot be disarmed by a first strike and a land-based one can, which is the
    distinction the whole deterrent turns on.
    """
    tier = DELIVERY_TIERS.get(arsenal.delivery, DELIVERY_TIERS["none"])
    if tier.survival <= 0:
        return 0.0
    # **And what the country's posture does with the same hardware.** A force held on
    # alert, dispersed and exercised survives a first strike better than the same missiles
    # in the same silos under a posture that keeps them cheap. Exactly 1.0 for a nation
    # with no doctrine, so a world without them reads as it always did. The ceiling still
    # applies: no posture makes a deterrent unkillable.
    #
    # **Applied to whatever each branch below concludes, not inside one of them.** Written
    # into the land-only branch first, it silently did nothing for a triad or a boat force
    # -- which is every arsenal the axis exists for. A lever that looks built and is not is
    # the failure this project keeps finding; this one was caught by printing it.
    posture = _posture(conn, world_id, arsenal.nation_id, "survivability")
    # Readiness rides alongside the doctrine posture and does the same kind of work: a
    # force held on alert survives being struck first better than the same one nobody has
    # exercised. Bounded the same way and floored, so neglect never disarms.
    posture *= max(READINESS_FLOOR, min(1.0, arsenal.readiness))
    if arsenal.delivery not in SEA_LEG_TIERS:
        return min(_SURVIVAL_CEILING, tier.survival * posture)

    boats = conn.execute(
        "SELECT COUNT(*) AS n FROM unit WHERE world_id = ? AND nation_id = ? AND kind = ?"
        "   AND strength > 0",
        (world_id, arsenal.nation_id, _SUBMARINE_KIND),
    ).fetchone()
    afloat = int(boats["n"]) if boats else 0

    # **Only the boats that carry missiles.** There is one `submarine` kind per era, so
    # nothing on the unit or its template distinguishes a ballistic-missile boat from an
    # attack boat -- Britain's three and China's four came out of the same template in the
    # same year. `arsenal.deterrent_boats` is the nation-level count that does, and NULL is
    # "the scenario did not say", which counts every boat exactly as this did before the
    # column existed.
    #
    # `min` rather than a subtraction, so **losses eat the attack boats first and the
    # deterrent last**. That is optimistic for the defender and it is the honest way to be
    # wrong: the engine does not know which hull was which, so an anti-submarine campaign
    # that deliberately hunted the missile boats cannot be modelled. What it does model is
    # that sinking a navy eventually sinks the deterrent.
    declared = conn.execute(
        "SELECT deterrent_boats FROM arsenal WHERE world_id = ? AND nation_id = ?",
        (world_id, arsenal.nation_id),
    ).fetchone()
    if declared is not None and declared["deterrent_boats"] is not None:
        afloat = min(afloat, max(0, int(declared["deterrent_boats"])))

    # **A deterrent carried only by boats is nothing once the boats are gone.**
    #
    # `sea` kept its 0.65 floor with every hull on the bottom, so a power whose entire
    # second strike had been sunk still rode out a first strike with two thirds of its
    # arsenal and answered. Codex found it. `triad` keeps its floor because the floor is
    # what the other two legs *are* -- silos and dispersed aircraft do not sink -- and that
    # difference is the whole reason the two tiers are separate.
    #
    # The floor is not zero: a boat alongside is still a boat, and a country that has lost
    # its patrols has not lost its warheads. `_SEA_WITHOUT_BOATS` is what a submarine force
    # is worth caught in harbour, which is little and is not nothing.
    if not afloat:
        return min(_SURVIVAL_CEILING, (
            _SEA_WITHOUT_BOATS if arsenal.delivery == "sea" else tier.survival) * posture)
    return min(_SURVIVAL_CEILING,
               (tier.survival + afloat * _SURVIVAL_PER_BOAT) * posture)


def surviving_warheads(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> int:
    """How many warheads this nation could still fire after absorbing a first strike.

    Rounded down, but never to zero while it holds anything at all and has any means of
    dispersing it -- a deterrent that rounds away is a deterrent that stops deterring at
    exactly the moment somebody is doing the arithmetic on whether to attack it.
    """
    arsenal = arsenal_of(conn, world_id, nation_id)
    if arsenal is None or not arsenal.armed:
        return 0
    share = survivability(conn, world_id, arsenal)
    if share <= 0:
        return 0
    return max(1, int(arsenal.warheads * share))


# ------------------------------------------------------------------------- deterrence


#: The most a survivable arsenal can take off an attacker's odds against the homeland it
#: guards. Capped low on purpose: this is meant to make invading a nuclear power *hard*,
#: not impossible. Ukraine is being invaded by a nuclear power in the scenario that
#: motivated this, and a term that made homelands unattackable would have quietly said
#: the war on the map cannot happen.
_DETERRENCE_CEILING = 0.18
_DETERRENCE_PER_WARHEAD = 0.015
#: A tier that can ride out a first strike deters more than one that has to be used first.
_DETERRENCE_TIER_BONUS = {"none": 0.0, "aircraft": 0.0, "missile": 0.04,
                          "sea": 0.06, "triad": 0.07}


def deterrence(conn: sqlite3.Connection, defender: str, region_id: str) -> float:
    """How much harder a survivable arsenal makes this ground to take. Zero, usually.

    **Only the homeland.** The term applies where ``region.home_nation_id`` is the
    defender -- their own core, not ground they occupy. A nuclear power's conquests are
    exactly as contestable as anybody else's, which is what keeps the mechanic from
    turning every annexation permanent.

    Exactly ``0.0`` on a map with no arsenal rows, which is every 1936 world and every
    world written before schema 10.
    """
    row = conn.execute(
        """
        SELECT a.warheads AS warheads, a.delivery AS delivery, r.world_id AS world_id,
               COALESCE(a.tactical, 0) AS tactical, a.readiness AS readiness
          FROM region r
          JOIN arsenal a ON a.nation_id = r.home_nation_id AND a.world_id = r.world_id
         WHERE r.id = ? AND r.home_nation_id = ?
        """,
        (region_id, defender),
    ).fetchone()
    if row is None or int(row["warheads"]) <= 0:
        return 0.0

    # **Only the strategic half deters.** A deterrent works by threatening what an attacker
    # cannot accept losing, and a battlefield weapon threatens the battlefield -- which the
    # attacker is already on and has already decided to accept. Splitting an arsenal
    # tactically is therefore a real cost paid in deterrence, and that is the trade the
    # split exists to pose: weapons that help you fight, or weapons that stop the fight.
    strategic = max(0, int(row["warheads"]) - int(row["tactical"] or 0))
    if strategic <= 0:
        return 0.0

    # **The real readiness column, not the dataclass default.** Building the `Arsenal` by
    # hand here left `readiness` at its 1.0 default, so `survivability` scored a rotted
    # deterrent exactly like an exercised one and this term disagreed with the number the
    # briefing prints from `surviving_warheads`, which reads the column. One arsenal, one
    # readiness, both places.
    arsenal = Arsenal(defender, strategic, str(row["delivery"]),
                      float(row["readiness"] if row["readiness"] is not None else 1.0))
    if not arsenal.armed:
        return 0.0
    share = survivability(conn, str(row["world_id"]), arsenal)
    if share <= 0:
        return 0.0

    survivors = max(1, int(arsenal.warheads * share))
    term = (
        survivors * _DETERRENCE_PER_WARHEAD
        + _DETERRENCE_TIER_BONUS.get(arsenal.delivery, 0.0)
    )
    # **What the posture says it would do with them, applied after the ceiling.**
    #
    # Deterrence is a belief about intent as much as a count of warheads: a country that
    # has said it would answer anything in force is harder to discount than one that has
    # said its weapons are for the last resort only. `_DETERRENCE_CEILING` is 0.18 and one
    # warhead is worth 0.015, so **every arsenal above a dozen survivors is already at the
    # ceiling** -- which is every arsenal this axis is about. Multiplying before the clamp
    # therefore did nothing at all for the United States, the Soviet Union, Russia and
    # China, and something only for Israel. Found by printing it rather than by reading it.
    #
    # After the clamp, the posture moves what the deterrent is *worth* rather than how many
    # warheads it counts, which is what a posture is. The ceiling still holds its meaning:
    # 0.18 is the most a count of warheads can be worth, and the +-15% either side of it is
    # what saying out loud what you would do with them is worth.
    return min(_DETERRENCE_CEILING, term) * _posture(
        conn, str(row["world_id"]), defender, "deterrence")


# ------------------------------------------------------------- what a warhead does


@dataclass(frozen=True, slots=True)
class NuclearEffect:
    """One thing a warhead does, and the hard limit on how far it does it.

    The vocabulary is closed and **the engine applies all of it** -- unlike every other
    gambit kind, the proposer does not choose from this list. That is what makes it safe
    for these to reach columns the gambit allowlist refuses: no wording of any proposal
    selects them, changes them, or leaves one out.
    """

    verb: str
    reaches: str
    cap: str


#: Disclosed verbatim in the briefing, for the reason decision 50 records: a bound a model
#: cannot see is a bound it discovers by spending a proposal on it -- and here the
#: proposal costs a city.
NUCLEAR_CAPS: tuple[NuclearEffect, ...] = (
    NuclearEffect(
        "crater",
        "the struck region's population and development",
        "at most 70% of each, and never below 0.1",
    ),
    NuclearEffect(
        "incinerate",
        "the strength and morale of every formation standing in it, whoever owns them",
        "at most 75% of strength, floored at 0.1; morale down 25",
    ),
    NuclearEffect(
        "fallout",
        "the population and development of up to 3 neighbouring land regions",
        "12% of each, whoever owns them",
    ),
    NuclearEffect(
        "panic",
        "the stability of the nation whose ground was struck",
        "at most 14 points",
    ),
    NuclearEffect(
        "disgrace",
        "your own legitimacy",
        "12 points, charged even if the warhead is intercepted",
    ),
    NuclearEffect(
        "outrage",
        "every other living nation's regard for you",
        "20 points each, 40 for the nation struck, floored at -100",
    ),
)

#: How many warheads one attempt may commit. Three is enough to be decisive against a
#: province and few enough that a launch is a strike rather than a campaign.
MAX_WARHEADS_PER_LAUNCH = 3

#: Stake name to warheads committed. The treasury cost the gambit charges is beside the
#: point here -- the real stake is the warheads, the reputation and the reply.
WARHEADS_BY_STAKE: dict[str, int] = {"minor": 1, "serious": 2, "grave": 3}

_CRATER_PER_WARHEAD = 0.30
_CRATER_MAX = 0.70
_REGION_FLOOR = 0.1
_INCINERATE_PER_WARHEAD = 0.35
_INCINERATE_MAX = 0.75
_UNIT_FLOOR = 0.1
_MORALE_HIT = 25.0
_FALLOUT_FRACTION = 0.12
_FALLOUT_NEIGHBOURS = 3
_PANIC_PER_WARHEAD = 5.0
_PANIC_MAX = 14.0
_DISGRACE = 12.0
_OUTRAGE_THIRD_PARTY = 20.0
_OUTRAGE_VICTIM = 40.0
#: A second strike is a reply, and the world reads it as one. Half the reputational cost
#: of going first, which is what makes the *relative* ledger favour never going first.
_RETALIATION_ODIUM = 0.5

#: What a partially intercepted strike still does. Not zero and not one: some got through.
_DEGRADED_SHARE = 0.35

#: Interception can never be certain in either direction, the same band a contest clamps
#: to and for the same reason.
_ODDS_FLOOR, _ODDS_CEILING = 0.05, 0.95


def penetration_odds(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    delivery: str,
    defender: str,
    region_id: str,
) -> float:
    """The chance a warhead reaches its target rather than being stopped short of it.

    Interception used to be computed here, against what honestly existed at the time: the
    defender's air-domain strength *standing in the target region*, and one technology
    flag. That was written with a note saying the air layer would take the term over when
    it was built. It has been: :func:`atlas.engine.air.air_defence_over` is now the single
    interception term in the engine, and this calls it.

    Three things change as a result, all of them corrections. Air defence one province
    away is no longer worth nothing -- interceptors have range, which is the whole point
    of the arm. A wing with no morale left no longer defends as well as a fresh one. And
    ``radar`` counts in 1936, so a warhead in an early world meets the defence that world
    actually has rather than only the one the 2026 catalogue names.
    """
    base = DELIVERY_TIERS.get(delivery, DELIVERY_TIERS["none"]).penetration
    if base <= 0:
        return 0.0

    # Imported here rather than at module scope for the ordinary layering reason: the air
    # layer is content, this is content, and neither should be the other's import-time
    # dependency. A dict lookup after the first call.
    from .air import air_defence_over

    base -= air_defence_over(conn, world_id, defender, region_id)
    return max(_ODDS_FLOOR, min(_ODDS_CEILING, base))


# ---------------------------------------------------------------------------- launching


@dataclass(slots=True)
class LaunchOutcome:
    launcher: str = ""
    target: str = ""
    region_id: str = ""
    warheads: int = 0
    #: ``struck`` | ``degraded`` | ``intercepted``.
    result: str = ""
    odds: float = 0.0
    narrative: str = ""
    refused: str | None = None
    applied: int = 0
    #: The engine's reply, if there was one. Never more than one deep.
    retaliation: LaunchOutcome | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        """**Whether a warhead actually went off**, which is not the same as whether one
        was rolled for.

        This used to be `bool(self.result)` -- set the moment the interception roll landed
        and before the batch that spends the warhead was applied. A refused batch therefore
        left `happened` true, the outcome event standing, and the turn report announcing a
        strike that changed nothing: no warhead spent, no crater, no reply. The same class
        of bug as a refused battle reporting a capture and a refused war staying declared,
        and it is fixed the same way -- the news is retracted and the claim withdrawn.
        """
        return bool(self.result) and self.applied > 0


def strike_region_for(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> str | None:
    """Where the engine aims a retaliation: the capital, or the best thing still held.

    Deterministic, because a second strike has to rewind and replay to the same place.
    """
    row = conn.execute(
        """
        SELECT r.id AS id FROM nation n
          JOIN region r ON r.id = n.capital_region_id
         WHERE n.id = ? AND r.world_id = ? AND r.kind <> 'ocean'
        """,
        (nation_id, world_id),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    fallback = conn.execute(
        """
        SELECT id FROM region
         WHERE world_id = ? AND owner_nation_id = ? AND kind <> 'ocean'
         ORDER BY development DESC, population DESC, id ASC LIMIT 1
        """,
        (world_id, nation_id),
    ).fetchone()
    return str(fallback["id"]) if fallback else None


def _region_row(conn: sqlite3.Connection, world_id: str, region_id: str):
    return conn.execute(
        "SELECT id, name, kind, owner_nation_id, controller_nation_id, home_nation_id,"
        " population, development FROM region WHERE world_id = ? AND id = ?",
        (world_id, region_id),
    ).fetchone()


def _neighbouring_land(
    conn: sqlite3.Connection, world_id: str, region_id: str
) -> list[str]:
    rows = conn.execute(
        """
        SELECT r.id AS id FROM region_adjacency a
          JOIN region r ON r.id = CASE WHEN a.region_a = ? THEN a.region_b ELSE a.region_a END
         WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
           AND r.kind <> 'ocean'
         ORDER BY r.id ASC
        """,
        (region_id, world_id, region_id, region_id),
    ).fetchall()
    return [str(row["id"]) for row in rows][:_FALLOUT_NEIGHBOURS]


def _reduce(current: float, fraction: float, floor: float) -> float:
    """The delta that takes ``current`` down by ``fraction``, never past ``floor``."""
    target = max(floor, current * (1.0 - fraction))
    return round(min(0.0, target - current), 3)


def _effect_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    launcher: str,
    victim: str,
    region_id: str,
    warheads: int,
    share: float,
    event_id: int,
    odium: float,
) -> list[Diff]:
    """The closed vocabulary, applied. Every magnitude is decided here and capped here."""
    diffs: list[Diff] = []
    scope = Visibility.PUBLIC

    def adjust(table: str, row_id: str, column: str, delta: float) -> None:
        # Every nuclear consequence goes through here, so the bounded-column clamp goes
        # here too and `panic`, `disgrace` and `outrage` all get it at once. A government
        # already at zero stability cannot be panicked further, and charging it anyway
        # would lose the entire strike -- craters, casualties and all -- to ECON-003.
        # See :mod:`atlas.engine.bounds`.
        delta = absorbable(conn, table, row_id, column, delta)
        if abs(delta) < 1e-9:
            return
        diffs.append(
            Diff(table, row_id, column, DiffOp.ADJUST, round(delta, 3),
                 reason_event_id=event_id, visibility_scope=scope)
        )

    if share > 0:
        # `crater` -- the ground itself.
        region = _region_row(conn, world_id, region_id)
        if region is not None:
            fraction = min(_CRATER_MAX, _CRATER_PER_WARHEAD * warheads) * share
            adjust("region", region_id, "population",
                   _reduce(float(region["population"]), fraction, _REGION_FLOOR))
            adjust("region", region_id, "development",
                   _reduce(float(region["development"]), fraction, _REGION_FLOOR))

        # `incinerate` -- everything standing in it, whoever owns it. A warhead does not
        # check the flag on the vehicles, and a launcher hitting a province its own army
        # is fighting over burns its own army too.
        fraction = min(_INCINERATE_MAX, _INCINERATE_PER_WARHEAD * warheads) * share
        for unit in conn.execute(
            "SELECT id, strength, morale FROM unit WHERE world_id = ? AND region_id = ?",
            (world_id, region_id),
        ).fetchall():
            adjust("unit", str(unit["id"]), "strength",
                   _reduce(float(unit["strength"]), fraction, _UNIT_FLOOR))
            # **Floored at zero, the way the air layer already floors it.** A flat
            # subtraction took a formation at morale 10 to -15, and `resolve` multiplies
            # strength by `morale / 50` -- so a unit with negative morale *subtracted* from
            # the strength of the army it was standing in, and a warhead on a shaken
            # garrison made its own side's attack easier. `air._charge_attrition` clamps the
            # drop to what the unit has; this now does the same, and for the same reason.
            adjust("unit", str(unit["id"]), "morale",
                   -round(min(float(unit["morale"]), _MORALE_HIT * share), 3))

        # `fallout` -- the neighbours, deterministically the first three by id.
        for neighbour in _neighbouring_land(conn, world_id, region_id):
            row = _region_row(conn, world_id, neighbour)
            if row is None:
                continue
            drift = _FALLOUT_FRACTION * share
            adjust("region", neighbour, "population",
                   _reduce(float(row["population"]), drift, _REGION_FLOOR))
            adjust("region", neighbour, "development",
                   _reduce(float(row["development"]), drift, _REGION_FLOOR))

        # `panic` -- the government of the ground that was struck.
        if victim:
            adjust("nation", victim, "stability",
                   -round(min(_PANIC_MAX, _PANIC_PER_WARHEAD * warheads) * share, 3))

    # `disgrace` and `outrage` are charged on the *attempt*, intercepted or not. You fired
    # a nuclear weapon at somebody; whether their air defences worked is their news, not
    # your excuse.
    adjust("nation", launcher, "legitimacy", -round(_DISGRACE * odium, 3))

    for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 AND id <> ?"
        " ORDER BY id ASC",
        (world_id, launcher),
    ).fetchall():
        other = str(row["id"])
        magnitude = _OUTRAGE_VICTIM if other == victim else _OUTRAGE_THIRD_PARTY
        # **A country that had promised to do this is judged for having meant it.** The
        # world's reaction to a launch is not only to the launch: a power whose declared
        # posture was massive retaliation has been telling everybody for years that it
        # would, and gets less benefit of the doubt than one that said its weapons were a
        # last resort.
        magnitude *= _posture(conn, world_id, launcher, "outrage")
        adjust("relation", f"{other}|{launcher}", "score",
               -round(magnitude * odium, 3))

    return diffs


def _spend(
    conn: sqlite3.Connection, world_id: str, nation_id: str, count: int, event_id: int
) -> list[Diff]:
    """Take ``count`` warheads off this arsenal, battlefield ones last.

    **`tactical` is a subset of `warheads`, so it moves when the pile shrinks under it.**
    Building one moves both (`warheads.arsenal_diffs`); spending one used to move only the
    total, so an arsenal fired or cratered down to nothing still carried its battlefield
    count and `tactical_edge` kept paying for weapons that no longer existed. The strategic
    part is spent first -- a strike is a strategic act, and counterforce finds silos -- and
    the tactical count is cut only by what the strategic part could not cover, so an
    arsenal that still has strategic weapons to lose keeps its battlefield ones untouched.
    """
    diffs = [Diff("arsenal", nation_id, "warheads", DiffOp.ADJUST, -int(count),
                  reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)]
    try:
        row = conn.execute(
            "SELECT warheads, COALESCE(tactical, 0) AS tactical FROM arsenal"
            " WHERE world_id = ? AND nation_id = ?", (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:  # a world before schema 42 has no tactical column
        row = None
    if row is not None:
        tactical = int(row["tactical"] or 0)
        left = max(0, int(row["warheads"]) - int(count))
        if tactical > left:
            diffs.append(Diff("arsenal", nation_id, "tactical", DiffOp.ADJUST,
                              -(tactical - left), reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
    return diffs


def launch(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    launcher: str,
    target: str,
    region_id: str,
    warheads: int,
    turn: int,
    date: str,
    seed: int,
    index: int = 0,
    retaliation: bool = False,
    stake_treasury: float = 0.0,
) -> LaunchOutcome:
    """Fire, adjudicate the interception, apply the consequence, and invite the reply.

    ``retaliation`` marks the engine's own second strike. It stops the recursion at one
    exchange -- deliberately, because two nuclear powers trading until one runs out is not
    a mechanic, it is a loop -- and it halves the reputational cost, because the world
    blames the state that went first.
    """
    outcome = LaunchOutcome(
        launcher=launcher, target=target, region_id=region_id, warheads=warheads
    )

    arsenal = arsenal_of(conn, world_id, launcher)
    if arsenal is None or not arsenal.armed:
        outcome.refused = f"{launcher} holds no usable nuclear arsenal"
        return outcome
    # **What this country's posture considers a proportionate reply.** Massive retaliation
    # commits more of the force than was asked for; minimum deterrence commits less. The
    # hard cap and the arsenal still bound it, so a posture can never conjure a warhead.
    wanted = int(warheads) * _posture(conn, world_id, launcher, "warheads_committed")
    committed = max(1, min(MAX_WARHEADS_PER_LAUNCH, int(wanted), arsenal.warheads))
    if committed < warheads:
        outcome.notes.append(
            f"committed {committed} warhead(s) rather than {warheads}: the cap is "
            f"{MAX_WARHEADS_PER_LAUNCH} and the arsenal holds {arsenal.warheads}"
        )
    outcome.warheads = committed

    region = _region_row(conn, world_id, region_id)
    if region is None:
        outcome.refused = f"there is no region called {region_id!r} on this map"
        return outcome
    if str(region["kind"]) == "ocean":
        outcome.refused = f"{region['name']} is open water"
        return outcome
    if not within_delivery_range(
        conn, world_id, launcher=launcher, region_id=region_id, delivery=arsenal.delivery
    ):
        tier = DELIVERY_TIERS.get(arsenal.delivery, DELIVERY_TIERS["none"])
        outcome.refused = (
            f"{region['name']} is beyond the reach of {tier.blurb}; "
            f"{launcher} cannot deliver a warhead that far from ground it holds"
        )
        return outcome

    # Whose ground it is *now*: an occupied province belongs to whoever is standing on it
    # for the purposes of who this hurts, which is also who will be asked to reply.
    victim = str(region["controller_nation_id"] or region["owner_nation_id"] or "")

    odds = penetration_odds(
        conn, world_id, delivery=arsenal.delivery, defender=victim or target,
        region_id=region_id,
    )
    # **A force nobody has maintained arrives less reliably.** Applied here, where the
    # arsenal is known, rather than inside `penetration_odds`, which is given a delivery
    # tier and not a launcher. Readiness multiplies what gets *through* and never how many
    # are fired -- the count is the one thing neglect must not touch, because a treasury is
    # empty exactly when a country is losing.
    odds = max(_ODDS_FLOOR, odds * max(READINESS_FLOOR, min(1.0, arsenal.readiness)))
    key = f"nuclear:{launcher}->{region_id}#{index}{':reply' if retaliation else ''}"
    roll = derive(seed, world_id, turn, key).random()
    if roll < odds * 0.6:
        result, share = "struck", 1.0
    elif roll < odds:
        result, share = "degraded", _DEGRADED_SHARE
    else:
        result, share = "intercepted", 0.0
    outcome.result, outcome.odds = result, round(odds, 3)

    told = (
        f"{'Retaliatory nuclear strike' if retaliation else 'Nuclear strike'} on "
        f"{region['name']} ({result})"
    )
    outcome.narrative = told
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.BATTLE, origin=EventOrigin.ENGINE, actor_nation_id=launcher,
        text=told[:600], visibility=Visibility.PUBLIC,
        # **The one battle event where naming the two sides matters most.** An attribution
        # layer that could not say who a warhead landed on would be unable to price the
        # only act in this game that no diplomacy survives. Interception is the victim's
        # win, like any other defence that held.
        participants=(
            ("actor", launcher),
            *((("defender", victim),) if victim else ()),
            ("beneficiary",
             launcher if result != "intercepted" else (victim or launcher)),
        ),
    )

    odium = _RETALIATION_ODIUM if retaliation else 1.0
    diffs = _spend(conn, world_id, launcher, committed, event_id)
    if stake_treasury:
        # The gambit channel's stake, charged in the same batch as the strike it paid for
        # so the two cannot come apart on a rewind. It is the least of what a launch
        # costs, and it is charged for the ordinary reason: an attempt has a price.
        diffs.append(
            Diff("nation", launcher, "treasury", DiffOp.ADJUST,
                 -round(float(stake_treasury), 2), reason_event_id=event_id,
                 visibility_scope=Visibility.PUBLIC)
        )
    diffs += _effect_diffs(
        conn, world_id, launcher=launcher, victim=victim, region_id=region_id,
        warheads=committed, share=share, event_id=event_id, odium=odium,
    )

    # **Counterforce.** A warhead that lands takes with it the share of the target's
    # arsenal that was not survivable in the first place -- silos found, airfields
    # cratered. A triad power keeps almost all of it and answers; a land-based one may
    # not. This is where `submarine` earns its place in the order of battle.
    if share > 0 and victim:
        victim_arsenal = arsenal_of(conn, world_id, victim)
        if victim_arsenal is not None and victim_arsenal.warheads > 0:
            exposed = 1.0 - survivability(conn, world_id, victim_arsenal)
            destroyed = min(
                victim_arsenal.warheads,
                int(round(committed * exposed * share)),
            )
            if destroyed > 0:
                diffs += _spend(conn, world_id, victim, destroyed, event_id)
                outcome.notes.append(
                    f"{destroyed} of {victim}'s warheads destroyed on the ground"
                )

    applied = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, proposer_nation_id=launcher, diffs=tuple(diffs),
        ),
    )
    if applied.applied:
        outcome.applied = len(diffs)
    else:
        # **The outcome event has to be retracted, not merely reported.** It was written
        # before the batch because the batch's diffs cite it, which is the shape every
        # engine act that can be refused has. Left standing it is indistinguishable from
        # news of a real strike: every later briefing quotes it, the Security Council's
        # `triggers_this_turn` reads a nuclear use off the *diff log* and would find
        # nothing to match, and the world reasons from an explosion that never happened.
        unrecord_events(conn, [event_id])
        outcome.problems.append(f"the strike could not be recorded: {applied.reason}")
        outcome.result = ""
        outcome.narrative = ""
        return outcome

    if not retaliation and victim:
        outcome.retaliation = second_strike(
            conn, world_id, launcher=launcher, victim=victim, provoking=committed,
            turn=turn, date=date, seed=seed, index=index,
        )
    return outcome



#: **Balance number.** What a battlefield arsenal is worth to the side holding it in a
#: contest, at full weight. Small on purpose and deliberately smaller than the strategic
#: deterrence term it is traded against: tactical weapons are a thumb on the scale of a
#: battle, and strategic ones are a reason there is no battle. A power that converts its
#: whole deterrent into battlefield weapons has bought a modest combat edge with the thing
#: that was keeping its capital off the table.
TACTICAL_WEIGHT = 0.05


def tactical_edge(conn: sqlite3.Connection, nation_id: str, region_id: str) -> float:
    """What this power's battlefield weapons are worth in a contest over this ground.

    **Anywhere it is fighting, not only at home.** That is the whole difference from
    `deterrence`, which applies only over a power's own core: a deterrent is about what
    cannot be taken from you, and a battlefield weapon is a weapon, and it works wherever
    it is carried. It is the reason to hold any.

    Scaled by readiness like everything else in this module -- an arsenal nobody has
    maintained is not one anybody fires on short notice -- and zero for a world written
    before schema 42, which is every world that had no tactical weapons in it.
    """
    try:
        row = conn.execute(
            "SELECT COALESCE(tactical, 0) AS tactical, warheads, delivery, readiness"
            "  FROM arsenal WHERE nation_id = ?", (nation_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if row is None:
        return 0.0
    # Never more battlefield weapons than weapons. `_spend` keeps the two coherent from
    # now on; the clamp is for a save written before it did, where a spent arsenal could
    # still carry its old battlefield count.
    tactical = min(int(row["tactical"] or 0), int(row["warheads"] or 0))
    if tactical <= 0 or str(row["delivery"] or "none") == "none":
        return 0.0
    # Diminishing, and hard: the tenth battlefield weapon changes a battle far less than
    # the first, and nothing here should make a large arsenal decide a war on its own.
    from math import sqrt

    weight = min(1.0, sqrt(tactical) / 6.0)
    return TACTICAL_WEIGHT * weight * float(row["readiness"] or 1.0)

def second_strike(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    launcher: str,
    victim: str,
    provoking: int,
    turn: int,
    date: str,
    seed: int,
    index: int = 0,
) -> LaunchOutcome | None:
    """The reply. Engine-authored, automatic, and not open to reconsideration.

    **This is the rule the whole layer exists for.** A first strike is only a winning move
    if the attacker gets to find out whether the defender would have dared; making the
    reply mechanical removes that question from the board. It runs whether or not the
    first strike got through -- an intercepted warhead is still a warhead somebody fired
    at you -- and it is proportional: never more than was thrown, never more than survives.
    """
    survivors = surviving_warheads(conn, world_id, victim)
    if survivors < 1:
        return None
    row = conn.execute(
        "SELECT is_alive FROM nation WHERE world_id = ? AND id = ?", (world_id, victim)
    ).fetchone()
    if row is None or not row["is_alive"]:
        return None

    region_id = strike_region_for(conn, world_id, launcher)
    if region_id is None:
        return None

    return launch(
        conn, world_id, launcher=victim, target=launcher, region_id=region_id,
        warheads=min(survivors, provoking, MAX_WARHEADS_PER_LAUNCH),
        turn=turn, date=date, seed=seed, index=index, retaliation=True,
    )


# ---------------------------------------------------------------------- discoverability


def estimated_retaliation(
    conn: sqlite3.Connection, world_id: str, *, viewer: str, target: str
) -> Estimate:
    """What ``viewer`` **believes** would come back at it from ``target``.

    **The split this function exists to draw.** A staff plans against its estimate of the
    other side's arsenal; the engine resolves a launch against the warheads that actually
    exist. Both numbers are in this module and they are deliberately not the same number:
    `surviving_warheads` is the truth and is what `launch` and `second_strike` fire, and
    this is a band around it with the widest fog in the intelligence layer over the top.

    Fogged on the *surviving* count rather than on the stockpile, because that is the figure
    a deterrent calculation is actually made of -- and under the same subject key as the
    stockpile estimate, so a nation that overestimates a rival's arsenal overestimates what
    would answer it too. Being wrong about a country in two unrelated directions at once is
    not what being wrong about a country looks like.
    """
    turn = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
    return estimate_of(
        conn, world_id, viewer=viewer, target=target, subject=ARSENAL,
        value=float(surviving_warheads(conn, world_id, target)), unit="warhead(s)",
        turn=int(turn["turn_number"]) if turn else 0)


def _believed(estimate: Estimate) -> float:
    """The single number a staff would actually plan on, out of a band.

    The midpoint, which is the honest reading of *what we think they have* -- and the reason
    a deceived nation misjudges the balance rather than merely being unsure about it. The
    range is quoted alongside it every time, so nothing here hides how much of a guess it is.
    """
    return (estimate.low + estimate.high) / 2.0


def nuclear_balance_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """The deterrent balance, for the *strategic* briefing rather than the gambit prompt.

    **The MAD picture only reached a leader inside a gambit prompt**, which is to say only
    while it was already contemplating a strike. So a nuclear power reasoned about
    deterrence exactly when it was deciding whether to launch, and never when it was
    deciding whether to invade, escalate, sign, or back down -- which is where deterrence
    actually does its work. A balance a staff sees only at the moment of using it is not a
    deterrent, it is a countdown.

    Two or three sentences, not the gambit block: what I have, how much of it survives being
    hit first, and who answers with how much. It **recomputes nothing** -- every number here
    comes from :func:`arsenal_of`, :func:`survivability` and :func:`surviving_warheads`, the
    same three the launch path and the gambit block use, so the briefing and the adjudicator
    cannot drift apart.

    Empty for a nation with no arsenal, which is almost all of them.

    **Who counts as the adversary**: whoever this nation is at war with, if anyone -- and
    otherwise the largest arsenal on the map that is not its own. A deterrent is aimed at a
    capability rather than at a current quarrel, and a briefing that named nobody in
    peacetime would go silent in exactly the years the balance matters most.
    """
    arsenal = arsenal_of(conn, world_id, nation_id)
    if arsenal is None or not arsenal.armed:
        return []

    tier = DELIVERY_TIERS.get(arsenal.delivery, DELIVERY_TIERS["none"])
    # `surviving_warheads` is `survivability` rounded the way the launch path rounds it.
    # Calling it rather than doing the multiplication here is the whole point: the number
    # the briefing quotes is the number the engine would actually fire.
    mine = surviving_warheads(conn, world_id, nation_id)
    reach = _TIER_REACH.get(arsenal.delivery)
    lines = [
        f"**Your deterrent.** {arsenal.warheads} warhead(s) delivered by {tier.blurb}; "
        f"about {mine} of them would survive a first strike and could still be fired"
        + (f", and they reach {reach} regions from ground you hold."
           if reach is not None else ", and their reach is intercontinental."),
    ]

    enemies = [
        str(row["id"])
        for row in conn.execute(
            "SELECT n.id AS id FROM relation rel JOIN nation n ON n.id = rel.nation_b"
            " WHERE rel.world_id = ? AND rel.nation_a = ? AND rel.at_war = 1"
            "   AND n.is_alive = 1 ORDER BY n.id",
            (world_id, nation_id),
        )
    ]
    # **Being at war with somebody is not the same as facing somebody.** The United States
    # in 1972 is at war with North Vietnam, which has no warheads, and the arsenal that
    # actually shapes every decision it makes is the one it is *not* fighting. So the
    # fallback is not "if you are at peace" -- it is "if nobody you are fighting could
    # answer you", which is the Cold War's whole shape: a proxy war under a balance held
    # somewhere else.
    if not any(surviving_warheads(conn, world_id, other) > 0 for other in enemies):
        enemies = [
            str(row["nation_id"])
            for row in conn.execute(
                "SELECT a.nation_id FROM arsenal a JOIN nation n ON n.id = a.nation_id"
                " WHERE a.world_id = ? AND a.nation_id <> ? AND a.warheads > 0"
                "   AND n.is_alive = 1 ORDER BY a.warheads DESC, a.nation_id LIMIT 1",
                (world_id, nation_id),
            )
        ]

    named = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    # **Who could answer is public; how many is not.** Which powers hold warheads at all is
    # a thing states announce, test for and are inspected about -- so the list above is
    # drawn from the truth. The counts are not: each one is now what *this* nation's service
    # believes, at the widest fog in the intelligence layer, and a rival with strong
    # counterintelligence can make that belief confidently wrong.
    answering = [
        (named.get(other, other),
         estimated_retaliation(conn, world_id, viewer=nation_id, target=other))
        for other in enemies
    ]
    answering = [(name, band) for name, band in answering if band.high > 0]
    if not answering:
        lines.append(
            "**Nobody on this map could answer a launch of yours in kind today.** That is "
            "a fact about them and not a permission: the disgrace, the outrage and the "
            "casus belli are charged whatever the other side has."
        )
        return lines

    believed = sum(_believed(band) for _name, band in answering)
    at_war_with_them = set(enemies) & {
        str(row["nation_b"]) for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id))
    }
    lines.append(
        ("**Likely retaliation.**" if at_war_with_them
         else "**Who could answer you, though you are not fighting them.**")
        + " If you launch, the engine launches back for them "
        "automatically, in the same turn, proportionally, aimed at your capital, and "
        "whether or not your own strike got through: "
        + ", ".join(f"{name} ({band.phrase().split(': ', 1)[-1]} surviving, "
                    f"{band.confidence})" for name, band in answering)
        + "."
    )
    lines.append(
        "**The balance, therefore, as your services read it:** you could throw about "
        f"{mine} -- a figure you know, because they are yours -- and absorb something like "
        f"{believed:.0f}, which you do not know and are estimating. "
        + (
            "You do not have a disarming first strike; there is no order of operations "
            "here that leaves your capital standing."
            if believed >= mine
            else "You would come out of an exchange with more left than they have -- "
            "which is not the same as coming out of it intact."
        )
        + " **Every count above is an estimate and could be wrong, including wrong in the "
        "direction that makes this look survivable.** A power that guards its own numbers "
        "well enough can choose what you believe about them."
    )
    return lines


def nuclear_rules_block(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What a launch would do, what it would cost, and who would answer it.

    **MAD as discoverability, not as a lecture.** Nowhere here does this say that using a
    nuclear weapon is unthinkable. It states the vocabulary, the caps and the fact that the
    reply is automatic and engine-run, and quotes the specific arsenals that would fire
    back. A staff that can see it will not be disarmed and that its own capital is in the
    same turn's report can work out the rest, which is a far better constraint than an
    instruction it could be talked out of.

    Returns an empty list for a nation with no arsenal, which is almost all of them: an
    unusable lever described at length is a lever that costs tokens and teaches nothing.
    """
    arsenal = arsenal_of(conn, world_id, nation_id)
    if arsenal is None or not arsenal.armed:
        return []

    share = survivability(conn, world_id, arsenal)
    tier = DELIVERY_TIERS.get(arsenal.delivery, DELIVERY_TIERS["none"])
    lines = [
        f"**Your nuclear arsenal.** {arsenal.warheads} warhead(s), delivered by "
        f"{tier.blurb}. About {max(1, int(arsenal.warheads * share))} of them would "
        f"survive being struck first and could still be fired.",
        # **The disclosure half of `NO_TREASURY_STAKE`.** Removing the stake made a launch
        # mechanically possible for a broke government; without this line a staff still
        # reads "undertakings cost 6, 16 and 34", checks an empty treasury and declines,
        # so the fix would be present and inert. It was held back once because it changes
        # a prompt the active cassette recorded -- and is restored with that tape.
        "**A launch costs no treasury.** Every other undertaking charges its stake win or "
        "lose; this one does not, because the warhead was paid for when it was built. The "
        "stake still decides how many leave the silos, and an empty treasury is not a "
        "reason you cannot fire.",
        "A `nuclear_launch` names a region rather than only a nation, and commits "
        "warheads by stake: "
        + ", ".join(
            f"`{name}` = {count}"
            for name, count in sorted(WARHEADS_BY_STAKE.items(), key=lambda kv: kv[1])
        )
        + f" (never more than {MAX_WARHEADS_PER_LAUNCH}).",
        "**You do not choose what it does.** Unlike every other undertaking, the effects "
        "of a launch are fixed and applied by the engine: "
        + "; ".join(f"`{e.verb}` -- {e.reaches} ({e.cap})" for e in NUCLEAR_CAPS)
        + ".",
        "It may be intercepted. Getting through depends on how you deliver it and what "
        "the target has overhead; an intercepted warhead is spent, and `disgrace` and "
        "`outrage` are charged anyway.",
    ]

    # The deterrence disclosure: named, quantified, and specific to whoever could answer.
    enemies = conn.execute(
        """
        SELECT n.id AS id, n.name AS name
          FROM relation rel JOIN nation n ON n.id = rel.nation_b
         WHERE rel.world_id = ? AND rel.nation_a = ? AND rel.at_war = 1 AND n.is_alive = 1
         ORDER BY n.id ASC
        """,
        (world_id, nation_id),
    ).fetchall()
    # Estimates here too. A leader deciding whether to launch is the *last* place an
    # omniscient warhead count belongs: it is the moment the misjudgement matters most.
    armed_enemies = [
        (str(row["name"]),
         estimated_retaliation(conn, world_id, viewer=nation_id, target=str(row["id"])))
        for row in enemies
    ]
    armed_enemies = [(name, band) for name, band in armed_enemies if band.high > 0]

    lines.append(
        "**If you launch, the engine launches back for them. Automatically, in the same "
        "turn, with no call to any model and no opportunity for either side to "
        "reconsider.** The reply is proportional -- never more warheads than you threw -- "
        "and it is aimed at your capital. It fires whether or not your own strike got "
        "through."
    )
    if armed_enemies:
        lines.append(
            "Who could answer you today, and -- as best your services can tell, which is "
            "not very well -- with how many warheads after absorbing your first strike: "
            + ", ".join(f"{name} ({band.phrase().split(': ', 1)[-1]}, {band.confidence})"
                        for name, band in armed_enemies)
            + ". Those counts are estimates and the true ones are what would actually fly. "
            "A first strike does not disarm them unless it destroys what is not "
            "already dispersed, and boats at sea are never on the ground to destroy."
        )
    else:
        lines.append(
            "No power you are currently at war with holds warheads that would survive to "
            "answer. That is a fact about today and not a policy; it changes the moment "
            "somebody else's programme finishes."
        )
    return lines
