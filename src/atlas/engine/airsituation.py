"""The air war over one theatre, said to the player in the numbers the engine used.

The air layer is real, wired and load-bearing: air superiority is fought for on a seeded
roll every turn, it persists in its own table, and it reaches the rest of the world by five
independent paths -- the odds of a land battle, the odds of a fleet action, how far a won
battle advances, whether a corridor can be supplied at all, and the decay that takes a sky
back off whoever stopped flying over it.

**Every one of those five is written into the model's briefing with its actual number, and
none of them has ever been said to the player.** The player got a map layer, a right-click
menu and a headline. They were not told that their aircraft stopped counting toward a land
battle, that the sky is worth more than twice as much over water, or that an enemy-held sky
adds two hops to a three-hop supply budget and is therefore strangling a front they cannot
see the cause of. That last one is the sharpest: it is among the strongest effects in the
layer, and it was described to *nobody* -- not in the air rules block either.

**This module is a reader and nothing else.** Every figure below is re-derived by importing
the constant the engine reads and calling the function the engine calls; nothing here is
recomputed with a second copy of a rule, nothing is invented, and nothing writes. Opening
the panel this feeds cannot move the world, which is why the fingerprint assertion in
``tests/test_the_air_situation_is_a_reading.py`` is the acceptance criterion it is.

**The adjudicated state, never the roll.** :func:`atlas.engine.air.resolve_air_war` draws a
seeded number per contested region per turn. That number is not here and must not be: a
surface that showed it would be showing the player the die rather than the position, and the
position -- who holds the sky, by how much, and what that is worth -- is the thing a staff
actually reasons about. What is shown is the standing state and the modifiers it produces.
Exact constants belong on a tooltip or in :func:`air_rules`, which is the reference entry.

**Two derivations here are counterfactual, and both say so.** *When control may lapse* runs
the decay forward from the margin that is stored; *what interdiction is costing* walks the
supply graph twice, once as the engine walks it and once with the enemy skies taken away, and
reports the difference. The second is the only place in this module with a walk of its own
rather than a call into the engine's, because :func:`atlas.engine.logistics.supply_map`
charges for interdiction unconditionally and there is no clear sky to ask it about. It is
kept honest by :func:`_supply_reach` being asserted equal to the engine's walk whenever
nothing is interdicted -- if the two ever disagree the test fails rather than the panel
quietly reporting a corridor the quartermaster does not have.
"""

from __future__ import annotations

import math
import sqlite3
from collections import deque
from dataclasses import dataclass, field

from .air import (
    _ADVANCE_BONUS,
    _AIR_DEFENCE_MAX,
    _CARRIER_AIR,
    _CONTROL_FLOOR,
    _DEFAULT_RANGE,
    _LAPSE_PER_MONTH,
    _RANGE_FALLOFF,
    _SUPERIORITY_MAX,
    _SUPERIORITY_MAX_AT_SEA,
    INTERCEPT_TECHS,
    MAX_AIR_RANGE,
    PENETRATION_TECHS,
    STRIKE_CAPS,
    _at_war,
    _neighbours,
    _oceans,
    _within,
    advance_multiplier,
    air_defence_over,
    control_of,
    projection,
    strike_range_of,
)
from .logistics import (
    _INTERDICTION_COST,
    _SEA_COST,
    _SUPPLY_RANGE,
    _depots,
    _enemies_of,
)
from .pacing import over

__all__ = [
    "AirAction",
    "AirEffect",
    "AirForce",
    "AirSituation",
    "EFFECT_PATHS",
    "Interdiction",
    "Lapse",
    "SEVERITY_BANDS",
    "air_rules",
    "air_situation",
    "established_by",
    "interdiction_against",
]


#: What a margin is called. The floor is the engine's own dead band -- below it nobody holds
#: the sky and the modifiers are exactly zero -- and the three above it are named for what a
#: staff would do about them rather than for where a number sits.
#:
#: **Bands, not the number.** The margin is a continuous 0..1 and every term scales linearly
#: with it, so a player reading "0.62" learns less than one reading "held" plus the signed
#: term it produced. Both are shown; the band is what leads.
_BANDS: tuple[tuple[float, str], ...] = (
    (0.60, "decisively held"),
    (0.30, "held"),
    (_CONTROL_FLOOR, "narrowly held"),
)

#: What to call a sky nobody holds. Distinguished from *nobody has ever flown here*, which is
#: a different fact and the ordinary state of most of the map: a contested sky is one two
#: powers can both reach and neither has won.
_CONTESTED = "contested"
_UNFLOWN = "nobody's"


@dataclass(frozen=True, slots=True)
class AirForce:
    """One formation putting air over this theatre, and what it is worth here.

    ``contribution`` is that unit's own share of the projected strength over the region --
    its strength, scaled by morale, discounted for a deck, and fallen off over the distance
    it flew. The shares of every unit a nation has sum to what
    :func:`atlas.engine.air.projection` returns for that nation, which
    ``test_the_forces_named_are_the_forces_counted`` pins.
    """

    nation_id: str
    nation_name: str
    unit_id: str
    kind: str
    #: Where it is based. Not where it is fighting: the point of the air layer is that these
    #: differ, and a player who cannot see the base cannot reason about moving it.
    region_id: str
    region_name: str
    #: How many regions from its base to this one.
    distance: int
    contribution: float
    #: Flying from a deck, at :data:`atlas.engine.air._CARRIER_AIR`.
    from_deck: bool = False


@dataclass(frozen=True, slots=True)
class AirEffect:
    """One thing the sky over this theatre is doing to the rest of the world.

    ``worth`` is the term in the units of what it changes; ``said`` is that term written the
    way the surface should show it. Both, because a player reading ``+0.13`` needs to be told
    it is odds and a player reading "worth 13 points of the odds" needs the figure to compare
    against the terrain.
    """

    #: Which of the five paths this is. One of :data:`EFFECT_PATHS`.
    path: str
    what: str
    worth: float
    said: str
    #: Why it is that and not something else, in the engine's own vocabulary.
    because: str
    #: The exact constant behind it, for a tooltip. Never in the sentence itself.
    rule: str = ""
    #: True for the one row that is *not* about this theatre: the odds figure for the other
    #: kind of ground, which is on the panel so that "the sky is worth more than twice as
    #: much over water" gets said at all. Flagged rather than inferred by the surface,
    #: because a row a player might read as a modifier they are being paid has to be drawn
    #: differently from one that they are.
    informational: bool = False


#: The five ways a held sky reaches the rest of the world. Named here so the panel, the
#: alerts and the reference entry cannot come to disagree about how many there are -- the
#: audit that started this work found five and every one of them is load-bearing.
EFFECT_PATHS: tuple[str, ...] = ("land", "naval", "advance", "supply", "lapse")


@dataclass(frozen=True, slots=True)
class Lapse:
    """When a held sky stops being held, if nobody flies over it again.

    Air superiority is maintained, not owned. The margin decays by
    :data:`atlas.engine.air._LAPSE_PER_MONTH` of a point per month of world time and the
    holder is cleared once it falls under the dead band -- so a sky won in a campaign two
    years dead is nobody's four quiet months later, and a player who does not know that will
    plan around a bonus that has already gone.
    """

    #: False when nobody holds this sky, so there is nothing to lose.
    applies: bool = False
    #: Turns of decay before the holder is cleared, at the pace the player is playing at.
    #: ``0`` means it goes at the end of this one.
    turns: int = 0
    #: How much margin a turn of not flying costs, at that pace.
    per_turn: float = 0.0
    #: True when two powers at war can both still reach this region, so it will be fought
    #: over again rather than decaying. The forecast is then what *would* happen if they
    #: stopped, which is the honest thing to say and not a prediction about their intentions.
    still_contested: bool = False
    #: **The world date the holder row is cleared on**, ISO, or ``""`` where the world has
    #: no readable clock. A turn count is a number about how many times a button gets
    #: pressed; a player plans against a calendar, and at yearly pacing "in 2 turns" and
    #: "in 4 turns" are two years apart while reading almost identically. Derived through
    #: :func:`atlas.engine.pacing.after_months` from the same span the decay itself is
    #: scaled by, so the date and the count can never disagree.
    on: str = ""


#: How bad an interdicted corridor is, as the share of the supply reach the sky has taken
#: away -- ``1 - reach_now / reach_clear``, the counterfactual the panel already walks.
#:
#: **A share and not a count, because a count is not a severity.** Four provinces lost is
#: catastrophic to a power with six and a rounding error to one with sixty, and a player
#: shown "4 provinces" has been handed the arithmetic rather than the finding. The share is
#: what the two walks already measure; these thresholds only name it.
#:
#: **Four bands and the first is not "none".** A corridor that is longer but still inside
#: the budget has cost nothing *yet* and is one province of advance from costing everything,
#: so it is `contained` rather than silence -- the whole point of this surface is that an
#: effect nobody is told about reads as the engine being broken when it finally bites.
SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.0, "contained"),
    (0.34, "constricted"),
    (0.67, "severed"),
    (1.01, "collapsed"),
)


def _severity(reach_now: int, reach_clear: int) -> tuple[str, float]:
    """The band and the share behind it. ``("contained", 0.0)`` when nothing has gone."""
    if reach_clear <= 0 or reach_now >= reach_clear:
        return SEVERITY_BANDS[0][1], 0.0
    share = 1.0 - (reach_now / reach_clear)
    for edge, band in SEVERITY_BANDS:
        if share < edge:
            return band, share
    return SEVERITY_BANDS[-1][1], share


@dataclass(frozen=True, slots=True)
class Interdiction:
    """What an enemy-held sky is costing one power's supply, and over which ground.

    The engine charges :data:`atlas.engine.logistics._INTERDICTION_COST` extra hops to move
    supply through a province whose sky an enemy holds, against a budget of
    :data:`atlas.engine.logistics._SUPPLY_RANGE`. Two of three: an interdicted corridor runs
    out in a third of the distance a clear one does.

    ``lost`` is the counterfactual -- the provinces this power could supply if those skies
    were clear and cannot supply as things stand. It is the one number in this module that is
    not a straight read, and it is the number that makes the mechanic visible at all.
    """

    active: bool = False
    #: This power's own provinces whose sky an enemy holds: ``(region_id, name, holder)``.
    corridors: tuple[tuple[str, str, str], ...] = ()
    #: Who is doing it, as ``(nation_id, name)``. The name because this reaches a sentence
    #: a player reads, and the id because a surface may want to route on it.
    by: tuple[tuple[str, str], ...] = ()
    #: Provinces in supply now, and provinces that would be in supply with the skies clear.
    reach_now: int = 0
    reach_clear: int = 0
    #: The ground the sky is costing: in ``reach_clear`` and not in ``reach_now``.
    lost: tuple[tuple[str, str], ...] = ()
    #: Formations standing in the lost ground. The consequence, rather than the geometry.
    formations_cut: int = 0
    surcharge: int = _INTERDICTION_COST
    budget: int = _SUPPLY_RANGE
    #: One of :data:`SEVERITY_BANDS`' names. How much of the corridor has actually gone,
    #: rather than how many provinces an enemy is flying over.
    severity: str = "contained"
    #: The share behind the band, ``1 - reach_now / reach_clear``.
    share_lost: float = 0.0


@dataclass(frozen=True, slots=True)
class AirAction:
    """One thing the player can do about this sky, and the engine's verdict on it.

    ``order`` is an ordinary action dict of the shape the parser emits, so choosing one joins
    the pending orders down the single pipeline everything else uses. ``None`` on the one
    entry that is not an order: *accept the risk* is a decision rather than an act, and
    dressing it as a button would be inventing a mechanic to make a list of four look tidy.
    """

    key: str
    label: str
    #: What it would actually do, in a sentence.
    how: str
    available: bool = True
    #: The engine's own reason, where it said no.
    why_not: str = ""
    order: dict | None = None
    #: A second field the order still needs before it can be attempted.
    needs: str = ""


@dataclass(frozen=True, slots=True)
class AirSituation:
    """Who holds the sky over one theatre, why, what it is doing, and what to do about it."""

    region_id: str
    region_name: str
    at_sea: bool
    viewer: str

    holder: str = ""
    holder_name: str = ""
    margin: float = 0.0
    band: str = _UNFLOWN
    #: The whole position in one sentence, written from the viewer's side of it.
    verdict: str = ""
    #: True when the viewer is the one holding it.
    mine: bool = False

    lapse: Lapse = field(default_factory=Lapse)
    established_by: tuple[AirForce, ...] = ()
    #: Everybody who can put anything over this region at all, strongest first, whether or
    #: not they hold it. The contest, not just its winner.
    contenders: tuple[tuple[str, str, float], ...] = ()
    effects: tuple[AirEffect, ...] = ()
    interdiction: Interdiction = field(default_factory=Interdiction)
    actions: tuple[AirAction, ...] = ()
    #: What the viewer's own air defence stops over this region, and what its raids carry
    #: through somebody else's. Both are shares, both are capped, and neither was anywhere.
    air_defence: float = 0.0
    penetration: float = 0.0


# --------------------------------------------------------------------------- the state


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    if not nation_id:
        return ""
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def _region(conn: sqlite3.Connection, region_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT name, kind FROM region WHERE id = ?", (region_id,)).fetchone()
    if row is None:
        return "", ""
    return str(row["name"]), str(row["kind"])


def _band(margin: float) -> str:
    for floor, said in _BANDS:
        if margin >= floor:
            return said
    return _CONTESTED


def _contenders(
    conn: sqlite3.Connection, world_id: str, region_id: str
) -> tuple[tuple[str, str, float], ...]:
    """Every power with aircraft that reach this region, strongest first.

    Read through :func:`atlas.engine.air.projection`, which is the same walk the air war runs
    on -- so a power the panel says can reach here is a power the engine would have fight
    here, and one it omits could not have.
    """
    graph = _neighbours(conn, world_id)
    oceans = _oceans(conn, world_id)
    found: list[tuple[str, str, float]] = []
    for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,),
    ).fetchall():
        nation = str(row["id"])
        strength = projection(
            conn, world_id, nation, graph=graph, oceans=oceans).get(region_id, 0.0)
        if strength > 0.0:
            found.append((nation, _name(conn, nation), round(strength, 2)))
    found.sort(key=lambda entry: (-entry[2], entry[0]))
    return tuple(found)


def established_by(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> tuple[AirForce, ...]:
    """Which formations are putting air over this region, and what each is worth here.

    The same arithmetic as :func:`atlas.engine.air.projection`, kept per unit instead of
    summed -- *which* wings hold a sky is the question a player asks next and the total
    cannot answer it. The shares sum to the projection, and a test says so.
    """
    if not nation_id:
        return ()
    graph = _neighbours(conn, world_id)
    oceans = _oceans(conn, world_id)
    found: list[AirForce] = []
    for unit in conn.execute(
        """
        SELECT u.id, u.region_id, u.kind, u.strength, u.morale,
               COALESCE(t.move_range, ?) AS reach, COALESCE(t.domain, 'land') AS domain
          FROM unit u LEFT JOIN unit_template t
            ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
           AND (COALESCE(t.domain, 'land') = 'air' OR u.kind = 'carrier')
         ORDER BY u.id
        """,
        (_DEFAULT_RANGE, world_id, nation_id),
    ).fetchall():
        weight = float(unit["strength"]) * (float(unit["morale"]) / 50.0)
        station = str(unit["region_id"])
        deck = station in oceans
        if str(unit["domain"]) != "air":
            weight *= _CARRIER_AIR
        if weight <= 0:
            continue
        hops = max(0, min(MAX_AIR_RANGE, int(unit["reach"])))
        walked = _within(graph, station, hops, oceans=oceans, deck=deck)
        if region_id not in walked:
            continue
        distance = int(walked[region_id])
        base, _kind = _region(conn, station)
        found.append(AirForce(
            nation_id=nation_id, nation_name=_name(conn, nation_id),
            unit_id=str(unit["id"]), kind=str(unit["kind"]),
            region_id=station, region_name=base, distance=distance,
            contribution=weight * (_RANGE_FALLOFF ** distance),
            from_deck=deck and str(unit["domain"]) != "air",
        ))
    found.sort(key=lambda force: (-force.contribution, force.unit_id))
    return tuple(found)


def _lapse(
    conn: sqlite3.Connection, world_id: str, region_id: str,
    holder: str, margin: float, span: float, today: str = "",
) -> Lapse:
    """How long a held sky survives being ignored, at this pace.

    Run forward from the stored margin with the engine's own decay: subtract
    ``over(_LAPSE_PER_MONTH, span)`` a turn until the margin falls under the dead band, which
    is the turn the holder row is cleared. Whether it *will* be ignored is a different
    question, and ``still_contested`` answers it separately rather than folding a guess about
    the enemy's intentions into a number.
    """
    if not holder or margin < _CONTROL_FLOOR:
        return Lapse()
    decay = over(_LAPSE_PER_MONTH, span)
    if decay <= 0:
        return Lapse(applies=True, turns=0, per_turn=0.0,
                     still_contested=_will_be_contested(conn, world_id, region_id))
    turns = math.floor((margin - _CONTROL_FLOOR) / decay) + 1
    turns = max(0, turns)
    return Lapse(
        applies=True, turns=turns, per_turn=decay,
        still_contested=_will_be_contested(conn, world_id, region_id),
        on=_lapse_date(today, turns, span),
    )


def _lapse_date(today: str, turns: int, span: float) -> str:
    """The world date a sky is let go of, ``turns`` turns of ``span`` months from now.

    ``""`` for a world whose clock will not parse, which is the honest answer and the one
    the surface can test for -- a fabricated date would be worse than a turn count, because
    a player would plan against it. That case is :func:`~atlas.engine.pacing.after_months`'
    own: it returns ``""`` for an unreadable start, so there is nothing to check for here.
    ``turns == 0`` -- the sky goes at the end of this turn -- asks it for no months at all
    and correctly gets today back.
    """
    from .pacing import after_months

    return after_months(today, max(0, turns) * span)


def _will_be_contested(
    conn: sqlite3.Connection, world_id: str, region_id: str
) -> bool:
    """Whether two powers at war can both still put air over this region.

    Exactly the condition :func:`atlas.engine.air.resolve_air_war` fights on, asked of one
    region: if it holds, the sky is fought over again next turn and does not decay.
    """
    reaching = [nation for nation, _n, _s in _contenders(conn, world_id, region_id)]
    return any(
        _at_war(conn, a, b)
        for index, a in enumerate(reaching)
        for b in reaching[index + 1:]
    )


# ------------------------------------------------------------------- the five effects


def _odds_effects(
    conn: sqlite3.Connection, region_id: str, viewer: str,
    *, holder: str, margin: float, at_sea: bool,
) -> list[AirEffect]:
    """The two odds terms: the one this theatre pays, and the one it does not.

    **Both, always.** *The sky is worth more than twice as much over water* is the single
    most consequential thing about this layer a player could learn, and a panel that only
    ever showed the local figure would never say it.
    """
    held_by_me = holder == viewer and margin >= _CONTROL_FLOOR
    against_me = bool(holder) and holder != viewer and margin >= _CONTROL_FLOOR
    who = _name(conn, holder)

    # **Signed from the viewer's side.** `superiority_term` is written attacker-first; a
    # panel is read by a government, and one that showed the same figure whichever way the
    # battle ran would be showing arithmetic rather than a position.
    ceiling = _SUPERIORITY_MAX_AT_SEA if at_sea else _SUPERIORITY_MAX
    term = ceiling * margin if held_by_me else -ceiling * margin if against_me else 0.0

    here = AirEffect(
        path="naval" if at_sea else "land",
        what=f"the odds of a {'fleet action' if at_sea else 'land battle'} here",
        worth=term,
        said=(f"{term:+.2f} to your odds" if term
              else "nothing — nobody holds this sky"),
        because=(
            f"You hold it at a margin of {margin:.2f}." if held_by_me
            else f"{who} holds it at a margin of {margin:.2f}." if against_me
            else "The margin is under the dead band, so it is worth nothing to either side."
        ),
        rule=(f"air._SUPERIORITY_MAX_AT_SEA = {_SUPERIORITY_MAX_AT_SEA}" if at_sea
              else f"air._SUPERIORITY_MAX = {_SUPERIORITY_MAX}"),
    )
    other_ceiling = _SUPERIORITY_MAX if at_sea else _SUPERIORITY_MAX_AT_SEA
    elsewhere = AirEffect(
        path="land" if at_sea else "naval",
        what=("the odds of a land battle — this theatre is water"
              if at_sea else "the odds of a fleet action — this theatre is ground"),
        worth=other_ceiling,
        said=f"up to {other_ceiling:+.2f}, {'less' if at_sea else 'more'} than here",
        because=(
            "At sea, air often decides whether the surface action happens at all; ashore it "
            "decides only how well an attack goes. The same margin is worth more over water."
        ),
        rule=f"air._SUPERIORITY_MAX = {_SUPERIORITY_MAX}, "
             f"_SUPERIORITY_MAX_AT_SEA = {_SUPERIORITY_MAX_AT_SEA}",
        informational=True,
    )
    return [here, elsewhere]


def _effects(
    conn: sqlite3.Connection, world_id: str, region_id: str, viewer: str,
    *, holder: str, margin: float, at_sea: bool, lapse: Lapse, mine_below: bool,
) -> tuple[AirEffect, ...]:
    """Every path a held sky reaches the world by, from the viewer's side of it.

    All five, always -- including the ones worth nothing right now. A list that dropped the
    zero terms would teach a player that air does three things, and the fourth would find
    them as a corridor that collapsed for no reason they were ever shown.
    """
    against_me = bool(holder) and holder != viewer and margin >= _CONTROL_FLOOR
    who = _name(conn, holder)
    out = _odds_effects(conn, region_id, viewer,
                        holder=holder, margin=margin, at_sea=at_sea)

    advance = advance_multiplier(conn, region_id, viewer)
    out.append(AirEffect(
        path="advance",
        what="how far a battle you win here carries",
        worth=advance - 1.0,
        said=(f"{advance - 1.0:+.0%} further" if advance > 1.0
              else "nothing — the bonus is the attacker's own sky, and this one is not yours"),
        because=(
            "Interdiction is the half of air power that shows up as an exploitation the "
            "defender cannot seal, so it is paid on the advance rather than on the odds."
        ),
        rule=f"air._ADVANCE_BONUS = {_ADVANCE_BONUS}",
    ))

    # **Charged only where both halves are true, which the first draft got wrong.** Supply
    # walks ground you *hold*; a province somebody else holds does not pass it at all,
    # whatever the sky over it. So an enemy sky over enemy ground costs the viewer nothing
    # today -- and would cost them the moment they took the ground, which is the useful
    # thing to say rather than a figure they are not paying.
    charged = against_me and mine_below
    out.append(AirEffect(
        path="supply",
        what="what it costs you to supply ground under this sky",
        worth=float(_INTERDICTION_COST) if charged else 0.0,
        said=(
            f"+{_INTERDICTION_COST} hops of a {_SUPPLY_RANGE}-hop budget"
            if charged else
            "nothing yet — you do not supply through this ground" if against_me else
            "nothing — only an enemy-held sky is charged for"
        ),
        because=(
            f"{who} holds this sky and you hold the ground, so a supply thread crossing it "
            f"pays {_INTERDICTION_COST} extra hops. An interdicted corridor runs out in a "
            "third of the distance a clear one does."
            if charged else
            f"Supply only walks ground you hold, and this is not yours. Take it while {who} "
            f"still holds the sky and every thread through it starts paying "
            f"{_INTERDICTION_COST} extra hops."
            if against_me else
            "An enemy-held sky adds hops to any supply thread crossing it. This one is not "
            "enemy-held."
        ),
        rule=f"logistics._INTERDICTION_COST = {_INTERDICTION_COST} against "
             f"_SUPPLY_RANGE = {_SUPPLY_RANGE}",
    ))

    out.append(AirEffect(
        path="lapse",
        what="how long this holds without being flown",
        worth=float(lapse.turns) if lapse.applies else 0.0,
        said=(
            "nothing to lose — nobody holds this sky" if not lapse.applies
            else "fought over again next turn, so it will not decay"
            if lapse.still_contested
            else f"nobody's in {lapse.turns} turn{'s' if lapse.turns != 1 else ''} "
                 "if nobody flies here"
        ),
        because=(
            "Air superiority is maintained, not owned: an unflown sky loses "
            f"{lapse.per_turn:.2f} of its margin a turn and is let go of under "
            f"{_CONTROL_FLOOR:.2f}."
            if lapse.applies else "There is no margin to decay."
        ),
        rule=f"air._LAPSE_PER_MONTH = {_LAPSE_PER_MONTH} per month, "
             f"_CONTROL_FLOOR = {_CONTROL_FLOOR}",
    ))
    return tuple(out)


# ------------------------------------------------------------------ the corridor lost


def _supply_reach(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, interdicted: bool
) -> dict[str, int]:
    """This nation's supply walk, optionally with the enemy skies taken away.

    **The one derivation here with a walk of its own, and it exists only to be subtracted
    from itself.** :func:`atlas.engine.logistics.supply_map` charges for interdiction
    unconditionally, so there is no way to ask it what the corridor would be if the sky were
    clear -- and without that counterfactual the strongest effect in the air layer can be
    stated but not *measured*, which is the state that made it invisible in the first place.

    ``interdicted=True`` is the engine's own walk, step for step, and is asserted equal to it
    in ``test_the_air_situation_is_a_reading``. If a later edit moves the quartermaster's
    arithmetic and not this, the test fails rather than the panel reporting a corridor
    nobody has.
    """
    graph = _neighbours(conn, world_id)
    if not graph:
        return {}
    controlled = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ?"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, nation_id),
        )
    }
    oceans = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    }
    enemies = _enemies_of(conn, world_id, nation_id)

    reach: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque()
    for depot in _depots(conn, world_id, nation_id):
        reach[depot] = 0
        frontier.append((depot, 0))
    passable_sea: dict[str, bool] = {}

    while frontier:
        region, distance = frontier.popleft()
        if distance > reach.get(region, distance):
            continue
        for neighbour in graph.get(region, ()):
            if neighbour in oceans:
                if neighbour not in passable_sea:
                    from .naval import crossable

                    passable_sea[neighbour] = crossable(
                        conn, nation_id, enemies, neighbour)
                if not passable_sea[neighbour]:
                    continue
                step = _SEA_COST
            elif neighbour not in controlled:
                continue
            else:
                step = 1
                if interdicted:
                    holder, _margin = control_of(conn, neighbour)
                    if holder is not None and holder in enemies:
                        step += _INTERDICTION_COST
            moved = distance + step
            if moved > _SUPPLY_RANGE:
                continue
            if moved < reach.get(neighbour, _SUPPLY_RANGE + 1):
                reach[neighbour] = moved
                frontier.append((neighbour, moved))
    return reach


def interdiction_against(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> Interdiction:
    """What enemy-held skies are costing this power's supply, and over which ground.

    The corridors are its own provinces an enemy holds the sky over; the ground lost is the
    difference between the walk the quartermaster actually makes and the walk it would make
    with those skies clear. Both, because the first is *where* and the second is *how much*,
    and a player shown only the first has been told a fact with no consequence attached.
    """
    if not nation_id:
        return Interdiction()
    enemies = _enemies_of(conn, world_id, nation_id)
    if not enemies:
        return Interdiction()

    corridors: list[tuple[str, str, str]] = []
    by: set[str] = set()
    for row in conn.execute(
        "SELECT id, name FROM region WHERE world_id = ?"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?"
        "   AND kind <> 'ocean' ORDER BY id",
        (world_id, nation_id),
    ).fetchall():
        holder, margin = control_of(conn, str(row["id"]))
        if holder is not None and holder in enemies and margin >= _CONTROL_FLOOR:
            corridors.append((str(row["id"]), str(row["name"]), holder))
            by.add(holder)
    if not corridors:
        return Interdiction()

    now = _supply_reach(conn, world_id, nation_id, interdicted=True)
    clear = _supply_reach(conn, world_id, nation_id, interdicted=False)
    lost_ids = sorted(set(clear) - set(now))
    lost = tuple((rid, _region(conn, rid)[0]) for rid in lost_ids)
    cut = 0
    if lost_ids:
        marks = ",".join("?" * len(lost_ids))
        cut = int(conn.execute(
            "SELECT COUNT(*) FROM unit WHERE world_id = ? AND nation_id = ?"  # noqa: S608
            f"   AND region_id IN ({marks})",
            (world_id, nation_id, *lost_ids),
        ).fetchone()[0])

    band, share = _severity(len(now), len(clear))
    return Interdiction(
        active=True, corridors=tuple(corridors),
        by=tuple((nation, _name(conn, nation)) for nation in sorted(by)),
        reach_now=len(now), reach_clear=len(clear), lost=lost, formations_cut=cut,
        severity=band, share_lost=share,
    )


# ----------------------------------------------------------------------- what to do


def _actions(
    conn: sqlite3.Connection, world_id: str, viewer: str, region_id: str,
    *, region_name: str, holder: str, mine: bool,
) -> tuple[AirAction, ...]:
    """Contest, strike, reinforce, or accept it -- with the engine's verdict on each.

    **Three of the four are ordinary orders and go down the ordinary pipeline**, reviewed by
    :func:`atlas.engine.workbench.review_orders` exactly as the region menu's are, so an
    action offered here and one offered there are the same verdict from the same code. The
    fourth is not an order and does not pretend to be.

    *Contest* is deliberately not "order the air war". Nobody orders it: it is fought every
    turn wherever two enemies' aircraft overlap, and cannot be declined. What a player can
    actually do is put more air within reach of this region -- so that is what the line says,
    and saying anything else would be offering a mechanic that does not exist.
    """
    from .mapactions import is_refused
    from .workbench import review_orders

    ground = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE id = ?", (region_id,)).fetchone()
    on_the_ground = str(ground["holder"]) if ground and ground["holder"] else ""

    candidates: list[tuple[str, str, str, dict, str]] = [
        ("contest", f"Base more air within reach of {region_name}",
         "The air war is fought every turn wherever two enemies' aircraft overlap. You "
         "cannot order it and cannot decline it — what you can do is bring more air into "
         "range of this theatre, which is what decides it.",
         {"type": "move_units", "summary": f"Move air within reach of {region_name}",
          "to_region": region_id}, "from_region"),
    ]
    if on_the_ground and on_the_ground != viewer:
        candidates.append((
            "strike", f"Strike {region_name}",
            "A strike hits what is in the theatre without occupying it, and is met by the "
            "defender's fighters and air-defence technology. It never takes ground.",
            {"type": "gambit", "summary": f"air strike in {region_name}",
             "gambit_kind": "air_strike", "target_nation": on_the_ground,
             "region": region_id, "aim": "forces"}, ""))
    candidates.append((
        "reinforce", "Raise another air wing",
        "Air strength over a region is the sum of every wing that can reach it, fallen off "
        "over the distance flown. More aircraft is the only thing that raises it.",
        {"type": "raise_units", "summary": "Raise an air wing", "unit_kind": "air_wing",
         "count": 1}, ""))

    cards = review_orders(
        conn, world_id, nation_id=viewer,
        actions=[order for _k, _l, _h, order, _n in candidates])

    out: list[AirAction] = []
    for (key, label, how, order, needs), card in zip(candidates, cards, strict=True):
        # **An action still waiting for its second province is incomplete, not impossible.**
        # `contest` is a move that needs somewhere to move *from*, so the workbench correctly
        # refuses it as written -- and greying it on that basis would tell the player the one
        # thing they can actually do about a sky cannot be done. Exactly
        # :attr:`atlas.engine.mapactions.MapAction.enabled`'s rule, for exactly its reason,
        # and the refusal it carries is about a field rather than about the world.
        refused = is_refused(card) and not needs
        out.append(AirAction(
            key=key, label=label, how=how,
            available=not refused,
            why_not=(card.refusal.because if refused and card.refusal else ""),
            order=None if refused else dict(order), needs=needs,
        ))

    # **Not an order, and not dressed as one.** Accepting the risk is what a government does
    # by spending the turn on something else, and the only honest thing a surface can add is
    # to name what is being accepted. ``order=None`` is what stops a panel putting a button
    # on it.
    if mine:
        risk = ("You hold this sky. Doing nothing costs nothing this turn, and the margin "
                "decays whether or not you use it.")
    elif holder:
        risk = (f"{_name(conn, holder)} holds this sky. Doing nothing leaves every term "
                "above standing against you for as long as they keep flying here.")
    else:
        risk = ("Nobody holds this sky, so nothing is being paid either way. Doing nothing "
                "leaves it to whoever brings aircraft first.")
    out.append(AirAction(key="accept", label="Accept the risk", how=risk, order=None))
    return tuple(out)


# ------------------------------------------------------------------------ the whole thing


def air_situation(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    region_id: str,
    viewer: str,
    days: float = 30.0,
) -> AirSituation | None:
    """Everything about the sky over one theatre, from one government's side of it.

    ``None`` for a region that is not on the map. Read-only throughout: every figure is a
    ``SELECT`` or a call into the function the engine itself calls, and the only arithmetic
    performed here is the lapse forecast and the interdiction counterfactual, both of which
    say so in their own docstrings.

    ``days`` is how long a turn covers at the pace the player is playing at, because *when
    this lapses* is a question about world time and not about how many times a button gets
    pressed -- a sky that survives four monthly turns survives a third of one yearly turn.
    """
    from .pacing import span_of

    name, kind = _region(conn, region_id)
    if not name:
        return None
    at_sea = kind == "ocean"
    found, margin = control_of(conn, region_id)
    holder = found or ""
    held = bool(holder) and margin >= _CONTROL_FLOOR
    mine = held and holder == viewer

    span = span_of(days)
    clock = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    today = str(clock["world_date"] or "") if clock else ""
    lapse = _lapse(conn, world_id, region_id, holder, margin, span, today)
    contest = _contenders(conn, world_id, region_id)
    ground = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder, kind"
        "  FROM region WHERE id = ?", (region_id,)).fetchone()
    mine_below = bool(ground) and str(ground["holder"] or "") == viewer and not at_sea
    effects = _effects(conn, world_id, region_id, viewer,
                       holder=holder, margin=margin, at_sea=at_sea, lapse=lapse,
                       mine_below=mine_below)

    if not held:
        band = _CONTESTED if len(contest) > 1 else _UNFLOWN
        verdict = f"Nobody holds the sky over {name}." + (
            " Two or more powers can reach it and none has won it." if len(contest) > 1
            else f" Only {contest[0][1]} can reach it." if contest
            else " Nothing has ever flown here.")
    else:
        band = _band(margin)
        verdict = (f"You hold the sky over {name}, {band}." if mine
                   else f"{_name(conn, holder)} holds the sky over {name}, {band}.")

    return AirSituation(
        region_id=region_id, region_name=name, at_sea=at_sea, viewer=viewer,
        holder=holder if held else "",
        holder_name=_name(conn, holder) if held else "",
        margin=margin if held else 0.0,
        band=band, verdict=verdict, mine=mine,
        lapse=lapse,
        established_by=established_by(conn, world_id, holder, region_id) if held else (),
        contenders=contest,
        effects=effects,
        interdiction=interdiction_against(conn, world_id, viewer),
        actions=_actions(conn, world_id, viewer, region_id, region_name=name,
                         holder=holder if held else "", mine=mine),
        air_defence=air_defence_over(conn, world_id, viewer, region_id),
        penetration=sum(
            PENETRATION_TECHS[str(row["tech_id"])]
            for row in conn.execute(
                "SELECT tech_id FROM technology WHERE nation_id = ?", (viewer,))
            if str(row["tech_id"]) in PENETRATION_TECHS
        ),
    )


# ----------------------------------------------------------------------- the reference


def air_rules(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[tuple[str, list[str]]]:
    """The complete air rules, for the player, as ``(heading, lines)``.

    **There was no such thing.** The model's briefing has carried
    :func:`atlas.engine.air.air_rules_block` since the layer shipped; the player had nothing
    -- no reference entry, no advisor block, nothing in the alerts or the trace. A game that
    tells one of its two audiences the rules and not the other is not hiding information, it
    is producing a player who reads a correct engine as a broken one.

    Generated from the tables the engine reads rather than written out, so a constant that
    moves moves here too. Two things are said that the model's own block does not say --
    supply interdiction and the lapse -- because both were missing from it as well, and both
    are load-bearing.
    """
    reach = strike_range_of(conn, world_id, nation_id)
    ids = {
        str(row["tech_id"]) for row in conn.execute(
            "SELECT tech_id FROM technology WHERE nation_id = ?", (nation_id,))
    }
    intercepting = sorted(ids & set(INTERCEPT_TECHS))
    penetrating = sorted(ids & set(PENETRATION_TECHS))

    return [
        ("Who holds a sky", [
            "Where you and a power you are at war with both have aircraft that reach the "
            "same region, the sky over it is contested every turn, automatically. You do "
            "not order it and you cannot decline it.",
            f"The winner holds it by a margin from 0 to 1. Under {_CONTROL_FLOOR:.2f} the "
            "sky is contested and worth nothing to either side; above it, every effect "
            "below scales with the margin.",
            (f"Your aircraft reach {reach} region{'s' if reach != 1 else ''} from where "
             "they are based, and fly over water, mountains and the front line alike. A "
             f"carrier projects air from its deck at {_CARRIER_AIR:.0%} of a land wing's "
             "worth." if reach else
             "You have no aircraft, so you contest no skies and can strike nothing. "
             "Aircraft reach several regions from where they are based and fly over water, "
             "mountains and the front line alike; a carrier projects air from its deck at "
             f"{_CARRIER_AIR:.0%} of a land wing's worth."),
        ]),
        ("What it is worth — five things, and only five", [
            f"**A land battle** fought under a sky you hold: up to {_SUPERIORITY_MAX:+.2f} "
            "to your odds. Under one an enemy holds, the same against you.",
            f"**A fleet action** over open sea: up to {_SUPERIORITY_MAX_AT_SEA:+.2f} — more "
            "than twice the land figure, and the largest single circumstantial modifier in "
            "the game. Ashore, air decides how well an attack goes; at sea it often decides "
            "whether the action happens at all.",
            f"**The advance after a battle you win**: up to {_ADVANCE_BONUS:.0%} more ground "
            "taken, scaled by your margin. Interdiction shows up as an exploitation the "
            "defender cannot seal, so it is paid here rather than on the odds.",
            f"**Your enemy's supply**: a province whose sky you hold costs its owner "
            f"{_INTERDICTION_COST} extra hops out of a {_SUPPLY_RANGE}-hop budget. An "
            "interdicted corridor runs out in a third of the distance a clear one does — "
            "this is the strongest thing a held sky does and the least visible.",
            f"**Nothing, once you stop flying.** An uncontested sky loses "
            f"{_LAPSE_PER_MONTH:.2f} of its margin per month of world time and is let go of "
            f"under {_CONTROL_FLOOR:.2f}. Air superiority is maintained, not owned.",
        ]),
        ("What aircraft no longer do", [
            "**Aircraft do not count toward the strength of a land or a sea battle.** They "
            "used to. What they contribute now is the sky, and what the sky is worth is "
            "above — so an air force that holds nothing contributes nothing to the fight "
            "beneath it.",
            "**A strike never takes ground.** It hits forces or infrastructure in a region "
            "within reach, and occupies nothing. Ground is taken by formations.",
        ]),
        ("Strikes, and what stops them", [
            "A strike is aimed at `forces` or at `infrastructure`. Over open sea it hits the "
            "enemy fleet sitting in it; `infrastructure` is refused there, because nothing "
            "is built on open water.",
            f"It is capped: at most {STRIKE_CAPS['strength']:.0f} strength and "
            f"{STRIKE_CAPS['morale']:.0f} morale off the defenders, or "
            f"{STRIKE_CAPS['development']:.1f} development and "
            f"{STRIKE_CAPS['construction_days']:.0f} days added to what is being built.",
            "Every raid is met by the defender's fighters within reach, their air-defence "
            "technology, and any air defence a patron mans for them. It can get through, be "
            "degraded, or be broken up entirely — and being intercepted costs you aircraft.",
            f"Interception is capped at {_AIR_DEFENCE_MAX:.0%} from fighters overhead, plus "
            "technology on top of it. Something always gets through.",
        ]),
        ("Your air technologies", [
            ("Shooting raids down: " + ", ".join(
                f"`{t}` ({INTERCEPT_TECHS[t]:+.2f})" for t in intercepting))
            if intercepting else "You have nothing that shoots raids down.",
            ("Getting your own through: " + ", ".join(
                f"`{t}` ({PENETRATION_TECHS[t]:+.2f})" for t in penetrating))
            if penetrating else
            "You have nothing that makes your own raids harder to intercept.",
            "The same interception term protects you against a bomb and against a warhead: "
            "the nuclear layer calls it too.",
        ]),
    ]
