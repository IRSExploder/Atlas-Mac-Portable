"""The air layer: a war for the sky, fought above the war for the ground.

Until now air was data. ``domain='air'`` reached upkeep, a build time and a schema CHECK,
and nowhere else: :func:`atlas.sim.resolve._strength` bucketed domains as ``'sea'`` or
*not* ``'sea'``, so an air wing was summed into a land battle exactly like an infantry
division -- at full weight, with no range, and with no way to lose that did not involve
being overrun on the ground. Three technologies named a layer the engine could not express.

This module is that layer. Four ideas, in the order they depend on each other:

**Reach.** ``unit_template.move_range`` has said 3 for an air wing against 1 for infantry
since the first commit and nothing has ever read it. It is read here, and it is the whole
mechanical identity of the arm: air projects over a *radius* of regions, and it overflies
terrain, coastline and front line alike. Nothing else in the engine reaches past one hop,
and nothing else ignores what it is flying over.

**Superiority.** Where two powers at war both project air over the same region, the sky
above it is contested on its own seeded roll, before anything on the ground moves. The
winner holds it, by a margin, and the margin is recorded state that persists between turns.

**Consequence.** Holding the sky is worth pressure and pace in the ground or naval contest
below -- and air units no longer count toward ground strength at all. That is the trade,
and it is deliberate: an air force stops being expensive infantry and becomes the thing
that decides how well the infantry does.

**Strikes and interception.** A strike reaches out to the same radius to degrade what it
finds -- formations, or the works and yards behind them -- and is met by the defender's
own fighters and air defence. This is where ``radar``, ``integrated_air_defence`` and
``hypersonic_strike`` stop being one-off ``base_strength`` bumps and start being what
their own blurbs say they are.

**Air never takes ground.** No function here writes ``region.controller_nation_id``,
``contest.pressure``, ``relation.at_war`` or a unit's position. A strike can make a
province cost more to hold and slow what is being built in it. It cannot occupy it. That
is decision 57's line, and the air layer is the arm most likely to be asked to cross it.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .adjacency import Adjacency, graph_of
from .pacing import compounded, over

__all__ = [
    "AirOutcome",
    "STRIKE_AIMS",
    "STRIKE_CAPS",
    "StrikeOutcome",
    "air_defence_over",
    "air_rules_block",
    "control_of",
    "in_air_range",
    "lapse_air_control",
    "projection",
    "resolve_air_war",
    "strike",
    "strike_range_of",
    "superiority_term",
]


# ----------------------------------------------------------------------------- reach


#: What a carrier's air group is worth against a land-based wing of the same strength.
#: A carrier is a ``sea`` unit whose entire purpose is aircraft, and a layer that ignored
#: that would leave the most expensive hull in either era contributing nothing to the one
#: thing it exists for. Discounted because a deck is a smaller airfield than an airfield.
_CARRIER_AIR = 0.6

#: Strength falls off with distance from the field: fuel spent getting there is not spent
#: fighting, and time on station is what a fighter over a battle actually contributes.
#: 0.72 per hop, so a wing three regions away is worth 37% of one overhead.
_RANGE_FALLOFF = 0.72

#: The reach of a unit whose template is missing or says nothing. One hop, like everything
#: else in the engine -- a unit the templates do not describe should not fly further than
#: an infantryman walks.
_DEFAULT_RANGE = 1

#: Nothing reaches further than this however good its template claims to be. A radius of
#: 5 already covers a continent on the 1936 map, and an unbounded BFS over 488 regions
#: once per nation per turn is a cost with no gameplay behind it.
MAX_AIR_RANGE = 5

#: Extra distance charged for a stretch of open water, and deliberately the same figure
#: :data:`atlas.engine.logistics._SEA_COST` charges for the same reason: a sea is longer
#: than the map makes it look, and there is no field underneath it.
_OCEAN_HOP = 2

#: **How many stretches of water a land-based wing may cross. One.**
#:
#: This is the whole fix, and the bug it removes was found by a live 1972 playtest: the
#: United States held the sky over four North Korean provinces, at peace, with all six of
#: its air wings sitting in the continental United States. An ocean is one region however
#: wide it is, so a plain breadth-first walk crossed the North Pacific in a single hop and
#: the Sea of Japan in a second, and three hops of `move_range` reached Ohio to Ryanggang.
#:
#: Measured on the seeded maps before this existed, from aircraft that had not moved: the
#: 1936 United States projected over **134 foreign land provinces**, Modern Day's over 115,
#: and 1972's over 125. It is the same shape as the free hop across open water that
#: :func:`atlas.engine.logistics.supplied_at_front` was fixed for, one layer along.
#:
#: One crossing is the honest limit for a land-based aeroplane: the Channel, the Sea of
#: Japan from Japan, the Taiwan Strait -- real capabilities, and all one stretch of water
#: from a real airfield. Two chained oceans is transoceanic reach, and the only thing that
#: buys that is a deck (see ``deck`` in :func:`_within`), which is precisely what a carrier
#: is for and what makes the most expensive hull in either era worth its price.
_MAX_WATER_CROSSINGS = 1


def _neighbours(conn: sqlite3.Connection, world_id: str) -> Adjacency:
    """The whole adjacency graph, once, undirected.

    Built in full rather than queried per region because the air pass walks it from every
    airfield in the world every turn, and 1,280 edges in a dict is cheaper than several
    hundred round trips into SQLite.

    ``crossing`` is deliberately ignored. An aeroplane does not care whether the border
    below it is a river, a coastline or a strait, and that indifference is the reason the
    arm is worth modelling separately at all.

    **Built once per world rather than once per call**, in :mod:`atlas.engine.adjacency` --
    supply, trade and air between them asked for this graph 10,867 times in a twelve-turn
    run, and the map has not moved since the world was seeded. The returned mapping is
    read-only because it is shared; see that module for why the key is what it is. This
    stays as the name the layer reads because every caller here means *the map*, and the
    indirection is the caching, not a second concept.
    """
    return graph_of(conn, world_id)


def _within(
    graph: Adjacency,
    origin: str,
    hops: int,
    *,
    oceans: frozenset[str] = frozenset(),
    deck: bool = False,
) -> dict[str, int]:
    """Every region within ``hops`` of ``origin``, mapped to its distance.

    **Water costs, and a land-based wing may only cross one stretch of it.** See
    :data:`_OCEAN_HOP` and :data:`_MAX_WATER_CROSSINGS` for what that removes. ``deck`` is
    set for a carrier: the hull is the forward base, so it pays the distance like anybody
    else and is not held to the crossing limit, which is the capability it exists to buy.

    Shaped like :func:`atlas.engine.logistics.supply_map` rather than like a plain
    breadth-first search, and for the same reason: once a step can cost two, the first
    path to a region is no longer necessarily the shortest, so a region is re-examined
    whenever a cheaper route to it turns up. Deterministic -- no randomness, no dict
    ordering dependence, and the same graph always yields the same distances.

    **The state is ``(region, crossings)``, not ``region``.** A path that has spent its
    one crossing and a path that has not are not interchangeable at the same province: the
    second can still go on across water and the first cannot. Collapsing them lets a wing
    reach a coast cheaply overland and then continue out to sea as though it had never
    flown over water at all.

    **:func:`atlas.engine.naval.crossable` is deliberately not consulted**, though supply,
    trade and landings all gate their water on it. Those move things through the water;
    this passes over it, and an enemy battle fleet does not stop an aeroplane -- the
    Luftwaffe flew the Channel while the Royal Navy owned it. :func:`_neighbours` already
    states the principle: air ignores what kind of border it is crossing. What water costs
    an aircraft is *distance*, and distance is what this charges.
    """
    limit = max(0, hops)
    # (region, crossings spent) -> best distance found for that state.
    best: dict[tuple[str, int], int] = {(origin, 0): 0}
    reach: dict[str, int] = {origin: 0}
    frontier: deque[tuple[str, int, int]] = deque([(origin, 0, 0)])

    while frontier:
        region, crossings, distance = frontier.popleft()
        if distance > best.get((region, crossings), distance):
            continue
        for neighbour in sorted(graph.get(region, ())):
            over_water = neighbour in oceans
            spent = crossings + (1 if over_water else 0)
            if over_water:
                if not deck and spent > _MAX_WATER_CROSSINGS:
                    continue
                step = _OCEAN_HOP
            else:
                step = 1
            moved = distance + step
            if moved > limit:
                continue
            key = (neighbour, spent)
            if moved < best.get(key, limit + 1):
                best[key] = moved
                frontier.append((neighbour, spent, moved))
                if moved < reach.get(neighbour, limit + 1):
                    reach[neighbour] = moved
    return reach


def _oceans(conn: sqlite3.Connection, world_id: str) -> frozenset[str]:
    """Every stretch of open water on this map, once per call that needs it."""
    return frozenset(
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    )


def strike_range_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    """How far this nation's longest-ranged aircraft reach, in regions.

    Read from ``unit_template.move_range`` -- the column that has existed and gone unread
    since the beginning. A nation with no aircraft reaches nowhere, which is why this can
    return 0 and callers must handle it.
    """
    row = conn.execute(
        """
        SELECT MAX(COALESCE(t.move_range, ?)) AS r
          FROM unit u JOIN unit_template t
            ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
           AND (t.domain = 'air' OR u.kind = 'carrier')
        """,
        (_DEFAULT_RANGE, world_id, nation_id),
    ).fetchone()
    if row is None or row["r"] is None:
        return 0
    return max(0, min(MAX_AIR_RANGE, int(row["r"])))


def projection(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    *,
    graph: Adjacency | None = None,
    oceans: frozenset[str] | None = None,
) -> dict[str, float]:
    """Air strength this nation can put over each region it can reach.

    Morale counts, exactly as it does in a ground contest: a wing that will not fly is not
    an air force. Each unit spreads its own strength over its own radius at the falloff
    above, and overlapping fields add -- which is how a concentration of airfields behind
    a front produces local superiority over it.

    **Water is charged for and cannot be chained**, which is what stops a wing that never
    left home covering another hemisphere -- see :data:`_MAX_WATER_CROSSINGS`. A carrier
    flies from where it floats, so its group is walked with ``deck=True``.
    """
    graph = _neighbours(conn, world_id) if graph is None else graph
    oceans = _oceans(conn, world_id) if oceans is None else oceans
    fields = conn.execute(
        """
        SELECT u.region_id, u.kind, u.strength, u.morale,
               COALESCE(t.move_range, ?) AS reach, COALESCE(t.domain, 'land') AS domain
          FROM unit u LEFT JOIN unit_template t
            ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
           AND (COALESCE(t.domain, 'land') = 'air' OR u.kind = 'carrier')
         ORDER BY u.id
        """,
        (_DEFAULT_RANGE, world_id, nation_id),
    ).fetchall()

    reach: dict[str, float] = {}
    for unit in fields:
        weight = float(unit["strength"]) * (float(unit["morale"]) / 50.0)
        if str(unit["domain"]) != "air":
            weight *= _CARRIER_AIR
        if weight <= 0:
            continue
        hops = max(0, min(MAX_AIR_RANGE, int(unit["reach"])))
        # **The exemption is about flying from water, not about being a carrier.** A
        # carrier alongside in its home port is a coastal airfield and reaches what one
        # reaches; the same hull in the middle of an ocean is the forward base the
        # crossing limit exists to require. Keying on the kind would have given a fleet
        # that never left harbour the reach of one on station.
        station = str(unit["region_id"])
        walked = _within(
            graph, station, hops, oceans=oceans, deck=station in oceans
        )
        for region, distance in walked.items():
            reach[region] = reach.get(region, 0.0) + weight * (_RANGE_FALLOFF**distance)
    return reach


def in_air_range(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> bool:
    """Whether this nation has aircraft that could reach that region at all."""
    return projection(conn, world_id, nation_id).get(region_id, 0.0) > 0.0


# ------------------------------------------------------------------- what it is worth


#: The most a won sky is worth to the odds of the battle underneath it. Bounded low on
#: purpose: air decides how well a ground attack goes, never whether it happens. At the
#: ceiling this is worth about as much as favourable terrain and less than surprise.
_SUPERIORITY_MAX = 0.10

#: And what it is worth over **water**, where the sentence above is the wrong way round.
#:
#: On land, air decides how well an attack goes; at sea in the carrier era it very often
#: decides whether the surface action happens at all. The layer was applying the land
#: figure to a fleet action -- `compute_pressure` has always called `superiority_term` for
#: every contest whatever the region's kind -- so a carrier group that owned the sky over
#: a sea got the same 0.10 as a fighter wing over a plain, which is invisible next to the
#: roll across the two or three exchanges a fleet action lasts. Measured on an even
#: sandbox fleet action: pressure 0.500 with nobody holding the sky, 0.600 with it.
#:
#: **This is a balance choice and it is sized against the terms it sits beside**, not
#: guessed. The force ratio spans about +/-0.20 through `0.5 + (base - 0.5) * 1.4`; the
#: largest evidence flag is +/-0.18 (`ATTACKER_OVEREXTENDED`); the worst terrain is 0.15.
#: At 0.22 a fleet with *total* air superiority fighting at material parity wins 72% of
#: exchanges instead of 60% -- the largest single circumstantial modifier in the game,
#: which is the historical claim, and still beaten outright by a 2:1 material advantage.
#: It cannot auto-win: the loser of the air war still takes 28% of exchanges at parity,
#: `margin` scales the whole term so a narrowly-held sky is worth a fraction of it, and
#: `compute_pressure` clamps to [0.05, 0.95] regardless. Down to 0.15 if a carrier power
#: feels unbeatable, up to 0.30 if the arm still feels ornamental.
#:
#: **`_ADVANCE_BONUS` is deliberately not given a naval twin.** How fast a sea falls was
#: not what went wrong; who won the exchanges was. Two levers moving at once make neither
#: measurable, and the pace half of air power -- interdicting reserves a defender cannot
#: move by day -- is the half with the least to say about open water.
_SUPERIORITY_MAX_AT_SEA = 0.22

#: And what it is worth to how far a won battle advances. Interdiction is the half of air
#: power that shows up as an exploitation the defender cannot seal, so it belongs on the
#: advance rather than on the odds.
_ADVANCE_BONUS = 0.40

#: Below this margin nobody holds the sky -- it is contested, and worth nothing to either
#: side. Without a dead band a 51/49 air battle would hand out a full modifier.
_CONTROL_FLOOR = 0.08


def control_of(conn: sqlite3.Connection, region_id: str) -> tuple[str | None, float]:
    """Who holds the sky over a region, and how decisively (0..1)."""
    row = conn.execute(
        "SELECT holder_nation_id, margin FROM air_control WHERE region_id = ?",
        (region_id,),
    ).fetchone()
    if row is None or row["holder_nation_id"] is None:
        return None, 0.0
    return str(row["holder_nation_id"]), float(row["margin"])


def superiority_term(
    conn: sqlite3.Connection, region_id: str, attacker: str, defender: str
) -> float:
    """What the sky over this region is worth to the attacker's odds, signed.

    Positive if the attacker holds it, negative if the defender does, zero if it is
    contested or if neither has ever flown here. Exactly zero on any world where no air
    battle has been fought, which is every world written before schema 12.

    **Worth more over water**, which is the one place the land reasoning inverts -- see
    :data:`_SUPERIORITY_MAX_AT_SEA`. The kind lookup is one indexed read on the primary
    key and only happens for a region somebody actually holds the sky over, so it costs
    nothing on the overwhelming majority of contests, which return above it.
    """
    holder, margin = control_of(conn, region_id)
    if holder is None or margin < _CONTROL_FLOOR:
        return 0.0
    if holder not in (attacker, defender):
        return 0.0

    row = conn.execute("SELECT kind FROM region WHERE id = ?", (region_id,)).fetchone()
    ceiling = (
        _SUPERIORITY_MAX_AT_SEA
        if row is not None and str(row["kind"]) == "ocean"
        else _SUPERIORITY_MAX
    )
    return ceiling * margin * (1.0 if holder == attacker else -1.0)


def advance_multiplier(conn: sqlite3.Connection, region_id: str, attacker: str) -> float:
    """How much further a won battle carries when its air force owns the sky above it."""
    holder, margin = control_of(conn, region_id)
    if holder != attacker or margin < _CONTROL_FLOOR:
        return 1.0
    return 1.0 + _ADVANCE_BONUS * margin


# ----------------------------------------------------------- interception, and the techs


#: Technologies that make aircraft harder to shoot down, and what each is worth against
#: the interception an attacker suffers. `hypersonic_strike`'s own blurb calls it
#: "warheads too fast to intercept"; until now it made destroyers better at surface
#: gunnery and did nothing whatever to penetration.
#: `mirv` is the 1972 entry and it is the purest case of the shape: several warheads on one
#: missile is worth nothing at all as a better gun and everything as a way through.
PENETRATION_TECHS: dict[str, float] = {"mirv": 0.12, "hypersonic_strike": 0.12}

#: And the technologies that shoot them down. `radar` in 1936, `sam_network` in 1972 and
#: `integrated_air_defence` in 2026 -- one layer, ninety years apart. The first and last
#: used to be a one-off `base_strength` bump on an air wing, which made them help you
#: *attack*.
#:
#: **This dict and the one above are how an era reaches the lever**, and adding a key is
#: the sanctioned way to do it: a technology with no `TechEffect` at all is legitimate
#: precisely when it is named here, which `test_research_eras.py` checks in both
#: directions.
INTERCEPT_TECHS: dict[str, float] = {
    "radar": 0.10, "sam_network": 0.15, "integrated_air_defence": 0.15,
}

#: Air strength over a region, divided by this, is what it contributes to interception.
_AIR_DEFENCE_DIVISOR = 40.0
#: However many fighters are overhead, something always gets through.
_AIR_DEFENCE_MAX = 0.35

_ODDS_FLOOR, _ODDS_CEILING = 0.05, 0.95


def _techs(conn: sqlite3.Connection, nation_id: str, table: dict[str, float]) -> float:
    """The sum of a nation's completed technologies from one table.

    The engine applies a technology's effect once, as a diff, at the moment it completes;
    this is a *runtime* lookup instead, because an interception term is not a property of
    a unit template that can be written down at completion -- it is a property of the
    engagement. The nuclear layer already needed exactly one of these.
    """
    if not table:
        return 0.0
    marks = ",".join("?" * len(table))
    rows = conn.execute(
        f"SELECT tech_id FROM technology WHERE nation_id = ? AND tech_id IN ({marks})",  # noqa: S608 - placeholders only
        (nation_id, *table),
    ).fetchall()
    return sum(table[str(r["tech_id"])] for r in rows)


def air_defence_over(
    conn: sqlite3.Connection,
    world_id: str,
    defender: str,
    region_id: str,
    *,
    graph: Adjacency | None = None,
) -> float:
    """How much of an incoming strike this defender stops over this region.

    The single interception term in the engine. The nuclear layer had its own copy, in
    which air defence one province away was worth nothing and a wing with no morale left
    was worth as much as a fresh one; it calls this instead now, so a nation that has
    invested in air defence is protected against a warhead and a bomb by the same state.

    Three things add: fighters that can reach the region, the ground layer's own
    technology, and nothing else. There is no flat national term -- air defence you did
    not build over ground you did not cover does not exist.
    """
    from .support import air_defence_bonus

    overhead = projection(conn, world_id, defender, graph=graph).get(region_id, 0.0)
    # **A SAM belt somebody else paid for stops the same aircraft.** Added here rather than
    # given its own term anywhere else, because this is *the* interception term -- the
    # nuclear layer calls it too, so a client whose patron mans its air defences is
    # protected against a bomb and a warhead by the same state, which is what actually
    # happened over Hanoi.
    return (
        min(_AIR_DEFENCE_MAX, overhead / _AIR_DEFENCE_DIVISOR)
        + _techs(conn, defender, INTERCEPT_TECHS)
        + air_defence_bonus(conn, world_id, defender)
    )


# ------------------------------------------------------------------- the air war itself


@dataclass(slots=True)
class AirOutcome:
    """One turn of the air war."""

    battles: int = 0
    #: Regions whose sky changed hands, as ``(region_id, winner)``.
    taken: list[tuple[str, str]] = field(default_factory=list)
    #: Skies let go of because nobody flew over them.
    lapsed: int = 0
    headlines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


#: What losing the air battle costs the loser, as a share of the strength it committed,
#: and what winning it costs the winner. Both sides bleed: an air battle nobody paid for
#: would make a standing air force free to use and impossible to attrit.
#:
#: **Per reference month, and compounded over the turn's span** -- see
#: :func:`_charge_attrition`. Read as a flat charge these were a year of fighting costing
#: what a month of it costs.
_LOSS_LOSER = 0.10
_LOSS_WINNER = 0.045

#: How hard the strength ratio bends into odds. Matches the ground adjudicator's 1.4, so
#: the two contests feel like the same engine, because they are.
_RATIO_TILT = 1.4

#: A power fighting over ground it holds is fighting over its own radar, its own fields
#: and its own wrecked pilots. Worth this much to the odds.
_HOME_SKY = 0.06

#: How much of its margin an uncontested sky loses each turn.
#:
#: **Air superiority is maintained, not owned**, and the first cut of this layer did not
#: say so. A live Modern Day run made the omission plain: Russia won the sky over 41
#: provinces during the Ukrainian war, the war ended at turn 6, and Russia still held all
#: 41 at turn 10 -- four turns of peace later -- because nothing ever cleared the rows.
#: Any later war fought in those provinces would have opened with a bonus won in a
#: campaign two years dead. The decay converges: a sky at full margin is nobody's after
#: four quiet turns, and a row that has reached the floor stops being written at all, so
#: this costs nothing in a world at peace.
#:
#: **Per month, and multiplied by the turn's span.** It was per *turn*: a sky won in a
#: campaign two years dead cleared after four quiet months at monthly pacing and four quiet
#: *years* at yearly, from the same row. Found by the audit of what was still counting turns
#: after the durations moved to the calendar -- this one is a rate rather than a duration,
#: which is why it survived the first sweep.
#:
#: **A straight-line subtraction, not a compounded one**, and this note says so because the
#: two are easy to confuse and only one of them is the shipped rule. The code is
#: ``max(0.0, margin - over(_LAPSE_PER_MONTH, span))``: :func:`atlas.engine.pacing.over`
#: multiplies, so a span of *n* months takes ``0.25 * n`` off the margin, and the ``max``
#: catches the overshoot a linear decay would otherwise walk into once the span passes
#: ``1 / rate``. A full margin is therefore nobody's after four quiet months at any pacing
#: -- one twelve-month jump clears it exactly as twelve monthly turns do, which is the
#: property the span was added for.
#:
#: Compounding it -- ``margin * (1 - rate) ** span``, which is what
#: :func:`atlas.engine.pacing.compounded` expresses -- would be a *different rule*, not a
#: tidier spelling of this one: it converges without ever arriving, so a full margin would
#: still sit above :data:`_CONTROL_FLOOR` in the **ninth** quiet month. That is a change to
#: how long a dead campaign's bonus outlives it, and it is not made here.
_LAPSE_PER_MONTH = 0.25


def _airborne_belligerents(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """Powers at war that own something that flies. Nobody else can contest a sky."""
    return [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT DISTINCT n.id
              FROM nation n
              JOIN relation rel ON rel.nation_a = n.id AND rel.at_war = 1
              JOIN unit u ON u.nation_id = n.id AND u.strength > 0
              LEFT JOIN unit_template t
                     ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE n.world_id = ? AND n.is_alive = 1
               AND (COALESCE(t.domain, 'land') = 'air' OR u.kind = 'carrier')
             ORDER BY n.id
            """,
            (world_id,),
        ).fetchall()
    ]


def _at_war(conn: sqlite3.Connection, a: str, b: str) -> bool:
    row = conn.execute(
        "SELECT at_war FROM relation WHERE nation_a = ? AND nation_b = ?", (a, b)
    ).fetchone()
    return bool(row and row["at_war"])


def resolve_air_war(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    seed: int,
    span: float = 1.0,
) -> AirOutcome:
    """Contest every sky two warring powers both reach, before the ground moves.

    Engine-driven and unprompted, like the front line marching. An air campaign is not a
    decision a staff makes each month -- it is what having an air force *means*, and
    routing it through an intent channel would have made it a thing only the player and
    five tracked nations ever did.

    One roll per contested region, keyed on where and against whom, so a rewind and a
    replay fight the same air war.
    """
    outcome = AirOutcome()
    belligerents = _airborne_belligerents(conn, world_id)
    if len(belligerents) < 2:
        # No air war to fight -- but the skies won by the last one still have to lapse,
        # or a peace freezes the map's air superiority exactly as the war left it.
        _lapse_uncontested(conn, world_id, fought=set(), turn=turn, date=date,
                           outcome=outcome, span=span)
        return outcome

    graph = _neighbours(conn, world_id)
    # The water set is read once and handed down, exactly as the graph is: both are facts
    # about the map rather than about a nation, and both are walked once per belligerent.
    oceans = _oceans(conn, world_id)
    reach = {
        n: projection(conn, world_id, n, graph=graph, oceans=oceans)
        for n in belligerents
    }

    # Whose skies overlap whose. Sorted throughout: the pairing, the region order and the
    # attacker/defender assignment must not depend on dict iteration order, or a replay
    # could draw a different stream key for the same battle.
    fights: dict[str, list[str]] = {}
    # A truce stops the air war as well as the ground war. Read once for the turn: an
    # air campaign that flew on through a ceasefire would make the ceasefire a fiction
    # nobody had broken.
    from .armistice import paused_pairs

    truces = paused_pairs(conn, world_id)
    for index, nation in enumerate(belligerents):
        for other in belligerents[index + 1 :]:
            if not _at_war(conn, nation, other):
                continue
            if frozenset((nation, other)) in truces:
                continue
            for region in sorted(set(reach[nation]) & set(reach[other])):
                fights.setdefault(region, []).append(nation)
                fights[region].append(other)

    #: Skies that were actually fought over, which is not the same as skies somebody could
    #: have fought over. Only these are spared the lapse below -- see the two ways a
    #: candidate region can produce no battle.
    fought: set[str] = set()

    #: Unit id -> the loss share it was charged over each sky it flew in this turn.
    #:
    #: **Accumulated rather than applied.** Attrition used to be charged inside each
    #: region's own batch, so a wing within range of many contested skies paid for every
    #: one of them and the charges *compounded*. On the sandbox's six-region map one month
    #: of fighting cost the loser 44% of its strength; on the 1936 and 2026 maps, where a
    #: wing can reach forty-one contested regions, the winner ends the month at 15% and
    #: the loser at 1.4%. An air force was annihilated by the map being large.
    flown: dict[str, list[float]] = {}

    #: Unit id -> the powers it actually fought this month. **The attribution the war
    #: ledger needs**: a wing's losses belong to the war they were suffered in, and after
    #: `flown` is averaged the opponent is gone. Both sides of every sky are at war with
    #: each other by construction here, so every entry names a real war.
    against: dict[str, list[str]] = {}

    for region_id in sorted(fights):
        # The strongest air force present, and the strongest one it is **at war with**; a
        # third power's aircraft over the same ground are a complication the layer does
        # not need to model to be honest.
        #
        # **The pair used to be the top two, and an ally standing between them cancelled
        # the war.** Two allies both fighting a third power over the same ground could be
        # the two strongest, and the fight was then skipped for the entirely correct
        # reason that they were not at war with each other -- while the third power, who
        # *was*, went unopposed. The sky was neither contested nor let go of: the region
        # counted as fought over, so the lapse below skipped it too, and an air
        # superiority won turns ago sat frozen for as long as the ally kept flying.
        present = sorted(set(fights[region_id]))
        ranked = sorted(present, key=lambda n: (-reach[n].get(region_id, 0.0), n))
        challenger = ranked[0]
        opponent = next(
            (other for other in ranked[1:] if _at_war(conn, challenger, other)), None
        )
        if opponent is None:
            continue
        sides = _fight_over(
            conn, world_id, region_id, challenger, opponent, reach,
            turn=turn, date=date, seed=seed, outcome=outcome,
        )
        if sides is None:
            continue
        fought.add(region_id)
        winner, loser = sides
        for nation, share, other in (
            (winner, _LOSS_WINNER, loser), (loser, _LOSS_LOSER, winner)
        ):
            for unit_id in _engaged_over(conn, world_id, region_id, nation, graph):
                flown.setdefault(unit_id, []).append(share)
                against.setdefault(unit_id, []).append(other)

    _charge_attrition(conn, world_id, flown, against=against, turn=turn, date=date,
                      outcome=outcome, span=span)

    # **`fought`, not `fights`.** A region nobody actually fought over has to decay like
    # any other quiet sky, and there are two ways to reach one: no belligerent pair
    # among the powers present, and both sides bringing nothing that could reach it.
    # Passing the candidate list froze both.
    _lapse_uncontested(conn, world_id, fought=fought, turn=turn, date=date,
                       outcome=outcome, span=span)
    return outcome


def lapse_air_control(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, span: float = 1.0
) -> AirOutcome:
    """Decay every sky, when there is no air war at all to decay them alongside.

    ``resolve_air_war`` returns early when fewer than two powers can put anything in the
    air against each other -- which is exactly the state a world reaches the moment a war
    ends, and exactly when the skies won during it need to start lapsing. So the lapse is
    reachable on its own.
    """
    outcome = AirOutcome()
    _lapse_uncontested(conn, world_id, fought=set(), turn=turn, date=date,
                       outcome=outcome, span=span)
    return outcome


def _lapse_uncontested(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    fought: set[str],
    turn: int,
    date: str,
    outcome: AirOutcome,
    span: float = 1.0,
) -> None:
    """Let go of every sky nobody flew over this turn, by how much time went by."""
    stale = [
        row
        for row in conn.execute(
            "SELECT region_id, holder_nation_id, margin FROM air_control"
            " WHERE world_id = ? AND holder_nation_id IS NOT NULL AND margin > 0"
            " ORDER BY region_id",
            (world_id,),
        ).fetchall()
        if str(row["region_id"]) not in fought
    ]
    if not stale:
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.BATTLE, origin=EventOrigin.ENGINE,
        text="Air superiority not flown for is air superiority given up.",
        visibility=Visibility.PUBLIC,
    )
    diffs: list[Diff] = []
    for row in stale:
        region_id = str(row["region_id"])
        margin = max(0.0, float(row["margin"]) - over(_LAPSE_PER_MONTH, span))
        key = f"{world_id}|{region_id}"
        diffs.append(
            Diff("air_control", key, "margin", DiffOp.SET, round(margin, 3),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )
        if margin < _CONTROL_FLOOR:
            diffs.append(
                Diff("air_control", key, "holder_nation_id", DiffOp.SET, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            outcome.lapsed += 1
    apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
        proposer_model=None, diffs=tuple(diffs),
    ))


def _fight_over(  # noqa: PLR0913 - one call site, and every argument is load-bearing
    conn: sqlite3.Connection,
    world_id: str,
    region_id: str,
    a: str,
    b: str,
    reach: dict[str, dict[str, float]],
    *,
    turn: int,
    date: str,
    seed: int,
    outcome: AirOutcome,
) -> tuple[str, str] | None:
    """Fight for one sky. Returns ``(winner, loser)``, or ``None`` if nobody flew.

    The caller needs both answers. A region where neither side could put anything over
    the ground is a quiet sky and has to lapse like any other, and reporting it as fought
    froze whatever air superiority was already there. And the *pair* goes back because
    attrition is charged once for the month, after every sky has been fought -- see
    ``flown`` in :func:`resolve_air_war`.
    """
    strength_a = reach[a].get(region_id, 0.0)
    strength_b = reach[b].get(region_id, 0.0)
    total = strength_a + strength_b
    if total <= 0:
        return None

    odds = 0.5 + (strength_a / total - 0.5) * _RATIO_TILT
    ground = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS held, name"
        "  FROM region WHERE id = ?",
        (region_id,),
    ).fetchone()
    held = str(ground["held"]) if ground and ground["held"] else None
    if held == a:
        odds += _HOME_SKY
    elif held == b:
        odds -= _HOME_SKY
    odds += _techs(conn, a, INTERCEPT_TECHS) - _techs(conn, b, INTERCEPT_TECHS)
    odds = min(_ODDS_CEILING, max(_ODDS_FLOOR, odds))

    rng = derive(seed, world_id, turn, f"air:{region_id}:{a}-vs-{b}")
    roll = rng.random()
    winner, loser = (a, b) if roll < odds else (b, a)

    # **The roll decides who won the day; the force ratio decides how much of the sky that
    # is worth.** Margin was the distance between roll and odds at first, which measured
    # luck: against a 3.5-to-1 Luftwaffe every French roll was a long way from the odds,
    # so the margin pinned at 0.96 and the modifier was effectively binary. Taken from the
    # ratio instead it is graded, and it has a property worth having -- a weaker air force
    # that wins the day gets a *negative* share and therefore no control at all. It
    # contested the sky rather than seizing it, which is what an upset in the air is.
    share = (strength_a if winner == a else strength_b) / total
    margin = max(0.0, min(1.0, (share - 0.5) * 2.0))

    previous, _ = control_of(conn, region_id)
    name = str(ground["name"]) if ground else region_id
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.BATTLE, origin=EventOrigin.ENGINE, actor_nation_id=winner,
        # **Who fought and who came out ahead**, structurally, the same three roles the
        # land battle carries. Without them an air campaign was a month of fighting that
        # nobody could be said to have won or lost: the constituency mapping reads the
        # participants and silently attributed nothing, and the attribution layer will
        # read them too. `beneficiary` is the side that prevailed.
        participants=(
            ("actor", winner), ("defender", loser), ("beneficiary", winner)),
        text=(
            f"Fighting in the air over {name}. "
            + (
                f"{_name(conn, winner)} holds the sky."
                if margin >= _CONTROL_FLOOR
                else "Neither side can claim it."
            )
        ),
        visibility=Visibility.PUBLIC,
    )

    # A first air battle over a region creates the row; later ones move it. Both are
    # diffs, so a rewind past the first one removes the row rather than leaving a sky
    # nobody ever fought for owned by somebody.
    held_sky = winner if margin >= _CONTROL_FLOOR else None
    key = f"{world_id}|{region_id}"
    existing = conn.execute(
        "SELECT 1 FROM air_control WHERE world_id = ? AND region_id = ?",
        (world_id, region_id),
    ).fetchone()
    if existing is None:
        diffs = [
            Diff("air_control", key, "__insert__", DiffOp.INSERT,
                 {"world_id": world_id, "region_id": region_id,
                  "holder_nation_id": held_sky, "margin": round(margin, 3),
                  "last_turn": turn},
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        ]
    else:
        diffs = [
            Diff("air_control", key, "holder_nation_id", DiffOp.SET, held_sky,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            Diff("air_control", key, "margin", DiffOp.SET, round(margin, 3),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            Diff("air_control", key, "last_turn", DiffOp.SET, turn,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        ]

    apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
        proposer_model=None, diffs=tuple(diffs),
    ))
    outcome.battles += 1
    if margin >= _CONTROL_FLOOR:
        if previous != winner:
            outcome.taken.append((region_id, winner))
        outcome.headlines.append(
            f"{_name(conn, winner)} holds the sky over {name}."
        )
    return winner, loser


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def _engaged_over(
    conn: sqlite3.Connection,
    world_id: str,
    region_id: str,
    nation_id: str,
    graph: Adjacency,
) -> list[str]:
    """The aircraft that actually flew over this region: only the ones in range.

    Losses go against units near the fighting rather than against an air force in the
    abstract, so an air campaign wears down the wings flying it. What this no longer does
    is *charge* them -- see :func:`_charge_attrition`, which does that once for the month.
    """
    # Same water rules as `projection`, and they have to be the same or the two disagree
    # about who was overhead: a wing the reach walk refused would still be charged for the
    # fighting it was refused permission to join.
    oceans = _oceans(conn, world_id)
    reachable = {
        r
        for row in conn.execute(
            """
            SELECT u.region_id, COALESCE(t.move_range, ?) AS reach,
                   COALESCE(t.domain, 'land') AS domain
              FROM unit u LEFT JOIN unit_template t
                ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
               AND (COALESCE(t.domain, 'land') = 'air' OR u.kind = 'carrier')
            """,
            (_DEFAULT_RANGE, world_id, nation_id),
        ).fetchall()
        for r in ([str(row["region_id"])]
                  if region_id in _within(
                      graph, str(row["region_id"]),
                      min(MAX_AIR_RANGE, int(row["reach"])),
                      oceans=oceans, deck=str(row["region_id"]) in oceans)
                  else [])
    }
    if not reachable:
        return []
    marks = ",".join("?" * len(reachable))
    return [
        str(row["id"])
        for row in conn.execute(
            f"""
            SELECT u.id FROM unit u
              LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
               AND (COALESCE(t.domain, 'land') = 'air' OR u.kind = 'carrier')
               AND u.region_id IN ({marks})
             ORDER BY u.id
            """,  # noqa: S608 - placeholders only
            (world_id, nation_id, *sorted(reachable)),
        )
    ]


def _wing_losses(
    conn: sqlite3.Connection,
    world_id: str,
    shares: dict[str, float],
    event_id: int,
) -> list[Diff]:
    """Charge each named wing its own share of its strength, never below the floor.

    The one place aircraft losses are turned into diffs, so the air battle and an
    intercepted raid cannot come to disagree about what a loss is -- or about
    :data:`_UNIT_FLOOR`, which the raid path did not have at all.
    """
    if not shares:
        return []
    marks = ",".join("?" * len(shares))
    rows = conn.execute(
        f"SELECT id, strength FROM unit WHERE world_id = ? AND id IN ({marks})"
        " ORDER BY id",  # noqa: S608 - placeholders only
        (world_id, *sorted(shares)),
    ).fetchall()

    diffs: list[Diff] = []
    for row in rows:
        strength = float(row["strength"])
        loss = round(
            max(0.0, min(strength * shares[str(row["id"])], strength - _UNIT_FLOOR)), 3
        )
        if loss > 0.001:
            diffs.append(Diff("unit", str(row["id"]), "strength", DiffOp.ADJUST, -loss,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
    return diffs


def _air_casualty_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    losses: list[Diff],
    against: dict[str, list[str]],
    *,
    turn: int,
    event_id: int,
) -> list[Diff]:
    """Put this month's air losses on the ledgers of the wars they were suffered in.

    A wing that flew against two enemies is charged to the one it met most often: the
    engine knows which skies it was in and not which aircraft the loss came out of, and
    picking the commonest opponent is the honest reading of that. Ties break by name, so a
    replay charges the same war.
    """
    from .wareffort import casualty_diffs

    if not losses or not against:
        return []
    owners = {
        str(row["id"]): str(row["nation_id"])
        for row in conn.execute(
            "SELECT id, nation_id FROM unit WHERE world_id = ?", (world_id,))
    }
    totals: dict[tuple[str, str], float] = {}
    for diff in losses:
        unit_id = str(diff.target_id)
        owner = owners.get(unit_id)
        faced = against.get(unit_id) or []
        if not owner or not faced:
            continue
        enemy = sorted(set(faced), key=lambda n: (-faced.count(n), n))[0]
        totals[(owner, enemy)] = totals.get((owner, enemy), 0.0) + abs(
            float(diff.new_value or 0.0))

    diffs: list[Diff] = []
    for (owner, enemy), lost in sorted(totals.items()):
        diffs += casualty_diffs(
            conn, world_id, losses={owner: lost}, enemy_of={owner: enemy},
            turn=turn, event_id=event_id)
    return diffs


def _charge_attrition(
    conn: sqlite3.Connection,
    world_id: str,
    flown: dict[str, list[float]],
    *,
    against: dict[str, list[str]] | None = None,
    turn: int,
    date: str,
    outcome: AirOutcome,
    span: float = 1.0,
) -> None:
    """Charge each wing **once** for the turn, at the rate its own fighting went.

    The share is the mean over the skies that wing flew in, which is the honest aggregate:
    a wing committed to forty-one contested regions did not commit forty-one times its
    strength, it committed its strength and spread it. A wing that lost everywhere pays
    what one that lost a single battle pays; one that mostly won pays close to the
    winner's rate; and neither is charged extra for the map being large.

    **The mean is a rate per reference month, and the turn is not one month long.** This
    charged it flat, so a yearly jump cost an air force exactly what a monthly one did and
    a weekly jump cost twelve times what seven days of fighting should -- the air war was
    the one part of this layer still reading a turn as a unit of time. The sky it is fought
    over already lapsed by elapsed time (:func:`_lapse_uncontested` takes ``span``); only
    the aircraft did not. `test_which_clock_each_duration_is_on` is the file that exists to
    catch exactly this and did not cover the attrition path.

    **Compounded, not multiplied**, which is :func:`atlas.engine.pacing.compounded` and the
    same rule every other per-turn probability in the engine follows -- an attrition share
    is the fraction of a wing that does not come back, so what survives ``span`` months is
    ``(1 - share) ** span`` and what is lost is one minus that. Linear scaling would charge
    a loser 120% of its strength over a year, and even short of that it charges the second
    month against aircraft the first month already destroyed. Compounding also makes the
    total depend only on elapsed time and never on how the player sliced it: twelve monthly
    jumps, four seasonal ones and one yearly one cost the same, which
    ``test_the_same_elapsed_time_costs_the_same_however_it_is_sliced`` pins at all four
    cadences.

    :data:`_UNIT_FLOOR` applies, the same floor the strike path has and for the same
    reason: air power is not allowed to remove a formation from the order of battle. A
    wing ground down over months becomes a cadre, and the ground war is what finishes it.
    It is a floor on the *stock* and not on the rate, so it binds sooner on a long jump --
    which is correct, and is why the equal-elapsed-time claim above holds up to it rather
    than through it.
    """
    if not flown:
        return
    shares = {
        unit_id: compounded(sum(taken) / len(taken), span)
        for unit_id, taken in flown.items()
    }
    # Written before the event, so a month whose fighting cost nobody anything does not
    # leave a headline saying it did.
    if not _wing_losses(conn, world_id, shares, 0):
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.BATTLE, origin=EventOrigin.ENGINE,
        text="A month of air fighting cost the squadrons flying it.",
        visibility=Visibility.PUBLIC,
    )
    # **Aircraft losses reach the war ledger too, and ground attrition does not.** A wing
    # shot down over a front is a cost of that war and is attributable to the power that
    # shot it down -- the sky was contested between two named belligerents. Logistics
    # attrition and encirclement are deliberately left out: neither is attributable to an
    # enemy at all, and a ledger that credited them to whoever happened to be at war would
    # be inventing the cause of a loss. See DESIGN_WAR_LOOP.md.
    losses = _wing_losses(conn, world_id, shares, event_id)
    applied = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(losses + _air_casualty_diffs(
            conn, world_id, losses, against or {}, turn=turn, event_id=event_id)),
    ))
    if not applied.applied:
        # The same discipline the resolution layer takes: news of losses nobody took is
        # history with no state behind it.
        unrecord_events(conn, [event_id])
        outcome.problems.append(
            f"the air campaign's losses could not be written: {applied.reason}"
        )


# ------------------------------------------------------------------------ strikes


#: What a strike may be aimed at. Closed, and short: every entry costs a branch in the
#: engine-owned effect code below, and a vocabulary a model can choose freely from is a
#: vocabulary where the engine has stopped deciding what happens.
STRIKE_AIMS: dict[str, str] = {
    "forces": "Hit the formations in the region: strength and morale, not the ground.",
    "infrastructure": "Hit what is behind them: the works under construction and the "
                      "developed value of the province.",
}

#: Hard ceilings on one strike, whatever the attacker brings. The nuclear layer's
#: discipline, for the same reason: an unbounded degrade is an escape hatch through which
#: a sufficiently large air force wins a war without ever taking a province.
#: A formation cannot be bombed below this. The same floor the nuclear layer uses, and
#: the playtest is why it is here: halving a garrison each raid approaches zero without
#: reaching it in arithmetic, but twelve raids on a 1.3-strength French division rounded
#: it to exactly 0.0 -- a unit still on the map, still counted as a formation, worth
#: nothing. Air power is not allowed to remove a division from the order of battle; that
#: is what the ground war is for.
_UNIT_FLOOR = 0.1

STRIKE_CAPS: dict[str, float] = {
    "strength": 5.0,
    "morale": 8.0,
    "development": 1.5,
    "construction_days": 25.0,
}

#: Before defences, what gets through. Not 1.0: weather, navigation and the fact that
#: bombing something is harder than reaching it.
_BASE_PENETRATION = 0.78

#: A strike flown into a sky the attacker owns is a different proposition from one flown
#: into a sky the defender owns.
_SUPERIORITY_PENETRATION = 0.20

#: What a partially-stopped strike achieves.
_DEGRADED_SHARE = 0.4

#: What the attacker loses when the interceptors get among them, as a share of the
#: aircraft in range. Being intercepted has to cost something or a strike is a free roll.
_INTERCEPTED_COST = 0.08
_DEGRADED_COST = 0.04

#: Strike weight: attacker's projected air strength over the region, divided by this, is
#: the fraction of each cap the strike delivers.
#:
#: Calibrated against the two real maps rather than guessed. Measured over every nation
#: that owns aircraft, projected strength where it is non-zero runs median 4.5 / p90 19 in
#: 1936 and median 9.3 / p90 46 in 2026. At 12 a median 1936 raid delivers about a third
#: of a cap and a massed one the lot, and a median 2026 raid about three quarters -- which
#: is the era difference doing the work rather than a constant per era. The first cut used
#: 25 and the playtest caught it: six French raids on Baden-Wurttemberg took 0.3 strength
#: off a 23.6 garrison, which is not a bombing campaign, it is weather.
_WEIGHT_DIVISOR = 12.0

#: A province cannot be bombed below this. The same floor development is seeded at and
#: eroded to elsewhere (`scenarios.development.FLOOR`, `budget._DEVELOPMENT_FLOOR`) --
#: three mechanics that all push the same number down had better stop at the same place,
#: or the lowest floor silently becomes the real one. Set at 0.5 when every province in
#: both eras was seeded at exactly 1.0 and lowered to match once they were not: a floor
#: *above* the poorest province's seeded value makes bombing it a no-op.
_DEVELOPMENT_FLOOR = 0.25


@dataclass(slots=True)
class StrikeOutcome:
    result: str = "refused"
    odds: float = 0.0
    aim: str = ""
    region_id: str = ""
    region_name: str = ""
    victim: str | None = None
    #: Human-readable, engine-generated: what the strike actually did.
    effects: list[str] = field(default_factory=list)
    refused: str | None = None
    problems: list[str] = field(default_factory=list)


def strike(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    attacker: str,
    region_id: str,
    aim: str,
    turn: int,
    date: str,
    seed: int,
    index: int = 0,
) -> StrikeOutcome:
    """Fly a strike at a region within reach, and let the defender shoot at it.

    Everything this does is chosen here. The proposer picks a target and an aim from two
    words; it does not pick a magnitude, and it cannot reach anything not on the list
    above -- notably not ``region.controller_nation_id``, and not the war itself.
    """
    out = StrikeOutcome(aim=aim, region_id=region_id)
    if aim not in STRIKE_AIMS:
        out.refused = f"'{aim}' is not something a strike can be aimed at."
        return out

    region = conn.execute(
        "SELECT name, kind, development,"
        "       COALESCE(controller_nation_id, owner_nation_id) AS held"
        "  FROM region WHERE id = ?",
        (region_id,),
    ).fetchone()
    if region is None:
        out.refused = f"no region '{region_id}'."
        return out
    out.region_name = str(region["name"])
    out.victim = str(region["held"]) if region["held"] else None

    if out.victim is None and str(region["kind"]) == "ocean" and aim == "infrastructure":
        # There are no works on open water and no province to devalue. Left to
        # `_infrastructure_diffs` this would bomb the `development` column of a stretch of
        # sea, which is a number that means nothing there.
        out.refused = (
            f"{out.region_name} is open water. There is nothing built on it to bomb -- "
            "aim at `forces` to go after the ships."
        )
        return out

    if out.victim is None:
        # **Nobody holds this water, so the strike takes its victim from what is floating
        # in it.** The aim used to come from `COALESCE(controller_nation_id,
        # owner_nation_id)` alone, and open sea has neither -- so a carrier raid on an
        # enemy battle fleet returned `result='struck'` with `victim=None`, wrote no
        # diffs, and left the fleet at full strength. Reported as a success, every time,
        # over every sea nobody had yet won, which is every sea a fleet action is fought
        # over. The one thing a carrier exists to do was a no-op.
        #
        # **This branch checks `at_war` and the holder branch above does not**, and the
        # asymmetry is deliberate. A holder is a claim on the ground and the order names
        # it: the striker has said who it is bombing. Open water names nobody, so the
        # engine is *choosing* a victim -- and an engine choosing had better not choose
        # somebody this power is not fighting.
        out.victim = _fleet_present(conn, world_id, attacker, region_id)

    graph = _neighbours(conn, world_id)
    weight = projection(conn, world_id, attacker, graph=graph).get(region_id, 0.0)
    if weight <= 0:
        out.refused = (
            f"nothing you own can reach {out.region_name}. Aircraft strike within "
            f"{strike_range_of(conn, world_id, attacker)} regions of where they are based."
        )
        return out

    defender = out.victim
    odds = _BASE_PENETRATION + _techs(conn, attacker, PENETRATION_TECHS)
    holder, margin = control_of(conn, region_id)
    if holder == attacker:
        odds += _SUPERIORITY_PENETRATION * margin
    elif holder is not None and holder == defender:
        odds -= _SUPERIORITY_PENETRATION * margin
    if defender:
        odds -= air_defence_over(conn, world_id, defender, region_id, graph=graph)
    odds = min(_ODDS_CEILING, max(_ODDS_FLOOR, odds))
    out.odds = round(odds, 3)

    rng = derive(seed, world_id, turn, f"strike:{attacker}->{region_id}#{index}")
    roll = rng.random()
    if roll < odds * 0.6:
        out.result, share = "struck", 1.0
    elif roll < odds:
        out.result, share = "degraded", _DEGRADED_SHARE
    else:
        out.result, share = "intercepted", 0.0

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.BATTLE, origin=EventOrigin.ENGINE, actor_nation_id=attacker,
        text=_strike_text(conn, attacker, out, region["name"]),
        visibility=Visibility.PUBLIC,
        # An intercepted raid is a defensive win, which is the whole reason `beneficiary`
        # is computed from the result rather than assumed to be the attacker. A strike on
        # ground nobody holds has no defender to name, and names none.
        participants=(
            ("actor", attacker),
            *((("defender", out.victim),) if out.victim else ()),
            ("beneficiary",
             attacker if out.result in ("struck", "degraded") else (out.victim or attacker)),
        ),
    )

    scale = share * min(1.0, weight / _WEIGHT_DIVISOR)
    diffs: list[Diff] = []
    if scale > 0 and defender:
        diffs = (
            _forces_diffs(conn, world_id, region_id, defender, scale, event_id, out)
            if aim == "forces"
            else _infrastructure_diffs(
                conn, world_id, region_id, defender, scale, event_id, out, region
            )
        )
    cost = {"intercepted": _INTERCEPTED_COST, "degraded": _DEGRADED_COST}.get(
        out.result, 0.0
    )
    if cost:
        raiders = _engaged_over(
            conn, world_id, region_id, attacker, _neighbours(conn, world_id)
        )
        diffs.extend(
            _wing_losses(conn, world_id, dict.fromkeys(raiders, cost), event_id)
        )
        out.effects.append("aircraft lost to the defences")

    if diffs:
        applied = apply_batch(conn, DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ))
        if not applied.applied:
            out.problems.append(f"strike batch refused: {applied.reason}")
    return out


def _strike_text(
    conn: sqlite3.Connection, attacker: str, out: StrikeOutcome, name: str
) -> str:
    who = _name(conn, attacker)
    target = "the defences" if out.aim == "forces" else "the works"
    return {
        "struck": f"{who} strikes {target} at {name}.",
        "degraded": f"{who} strikes at {name}; most of the raid turns back.",
        "intercepted": f"{who}'s raid on {name} is broken up before it arrives.",
    }.get(out.result, f"{who} strikes at {name}.")


def _fleet_present(
    conn: sqlite3.Connection, world_id: str, attacker: str, region_id: str
) -> str | None:
    """Whose forces are standing in ``region_id`` to be struck, when nobody holds it.

    The strongest enemy present, by summed strength. **Enemy** because the engine is
    picking the target rather than being told it -- see the call site -- and **strongest**
    because that is the fleet a raid flies out to find; a squadron of one damaged
    destroyer is not what the strike was aimed at because a bigger one is in the same
    water.

    Ordered by weight and then by nation id, so two fleets of exactly equal strength
    resolve the same way on a replay as they did on the run.
    """
    rows = conn.execute(
        """
        SELECT u.nation_id AS nation, SUM(u.strength) AS weight
          FROM unit u
         WHERE u.world_id = ? AND u.region_id = ? AND u.strength > 0
           AND u.nation_id <> ?
         GROUP BY u.nation_id
         ORDER BY weight DESC, u.nation_id
        """,
        (world_id, region_id, attacker),
    ).fetchall()
    for row in rows:
        if _at_war(conn, attacker, str(row["nation"])):
            return str(row["nation"])
    return None


def _forces_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    region_id: str,
    defender: str,
    scale: float,
    event_id: int,
    out: StrikeOutcome,
) -> list[Diff]:
    """Strength and morale off the formations standing in the region.

    Spread across them rather than concentrated, and floored: a strike degrades a defence,
    it does not delete a division. A unit reduced to nothing by bombing alone would be air
    power taking ground with extra steps.
    """
    units = conn.execute(
        "SELECT id, strength, morale FROM unit"
        " WHERE world_id = ? AND nation_id = ? AND region_id = ? AND strength > 0"
        " ORDER BY strength DESC, id",
        (world_id, defender, region_id),
    ).fetchall()
    if not units:
        out.effects.append("nothing was standing there")
        return []

    budget = STRIKE_CAPS["strength"] * scale
    morale_hit = STRIKE_CAPS["morale"] * scale
    total = sum(float(u["strength"]) for u in units)
    diffs: list[Diff] = []
    spent = 0.0
    for unit in units:
        current = float(unit["strength"])
        loss = round(
            max(0.0, min(current - _UNIT_FLOOR, current * 0.5, budget * (current / total))),
            3,
        )
        if loss > 0.001:
            spent += loss
            diffs.append(Diff("unit", str(unit["id"]), "strength", DiffOp.ADJUST, -loss,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
        drop = round(min(float(unit["morale"]), morale_hit), 3)
        if drop > 0.001:
            diffs.append(Diff("unit", str(unit["id"]), "morale", DiffOp.ADJUST, -drop,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
    if spent:
        out.effects.append(f"{spent:.1f} strength off the defenders")
        out.effects.append(f"{morale_hit:.1f} morale off every formation there")
    return diffs


def _infrastructure_diffs(  # noqa: PLR0913 - mirrors _forces_diffs
    conn: sqlite3.Connection,
    world_id: str,
    region_id: str,
    defender: str,
    scale: float,
    event_id: int,
    out: StrikeOutcome,
    region,
) -> list[Diff]:
    """The works and the yards: what is being built here, and what the province is worth.

    ``construction.days_remaining`` is the honest target -- bombing a shipyard should show
    up as the ship arriving later, which is what strategic bombing mostly achieved.
    """
    diffs: list[Diff] = []
    pushed = 0.0
    for job in conn.execute(
        "SELECT id, days_remaining FROM construction"
        " WHERE world_id = ? AND nation_id = ? AND region_id = ? AND days_remaining > 0"
        " ORDER BY id",
        (world_id, defender, region_id),
    ).fetchall():
        delay = round(STRIKE_CAPS["construction_days"] * scale, 2)
        if delay > 0.01:
            pushed += delay
            diffs.append(Diff("construction", str(job["id"]), "days_remaining",
                              DiffOp.ADJUST, delay, reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
    if pushed:
        out.effects.append(f"{pushed:.0f} days added to what was being built")

    # Floored for the same reason a sanctioned economy is: a region at zero development
    # is a region no later mechanic can reason about.
    current = float(region["development"] or 0.0)
    target = max(_DEVELOPMENT_FLOOR, current - STRIKE_CAPS["development"] * scale)
    if target < current:
        diffs.append(Diff("region", region_id, "development", DiffOp.SET,
                          round(target, 2), reason_event_id=event_id,
                          visibility_scope=Visibility.PUBLIC))
        out.effects.append(f"development {current:.1f} -> {target:.1f}")
    if not diffs:
        out.effects.append("there was nothing there worth wrecking")
    return diffs


# ------------------------------------------------------------------ discoverability


def air_rules_block(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    """What the air layer does, generated from the tables the engine actually reads.

    Decision 11/50's pattern. A model that cannot see that its aircraft reach three
    regions will fly them nowhere, and a model that cannot see that air defence exists
    will read an intercepted raid as the engine being broken.
    """
    reach = strike_range_of(conn, world_id, nation_id)
    lines = [
        "**The air war.** Where you and an enemy both have aircraft that can reach the "
        "same region, the sky over it is contested every turn, automatically. You do not "
        "order it and cannot decline it.",
    ]
    if reach:
        lines.append(
            f"- Your aircraft reach **{reach} region{'s' if reach != 1 else ''}** from "
            "where they are based, and fly over water, mountains and the front line "
            "alike. Carriers project air too, at a discount."
        )
    else:
        lines.append("- You have no aircraft, so you contest no skies and can strike nothing.")
    lines.append(
        "- Holding the sky over a region is worth up to "
        f"**{_SUPERIORITY_MAX:+.2f}** to the odds of a battle fought there and up to "
        f"**{_ADVANCE_BONUS:.0%}** more ground gained when you win it. Losing it is worth "
        "the same against you."
    )
    # **The naval figure is stated, because a rules block that quoted only the land one
    # would be describing a different game to the staff planning a fleet action.** It is
    # the largest single circumstantial modifier in the engine and a power deciding
    # whether to build carriers has no other way to learn that.
    lines.append(
        "- **Over open sea it is worth more:** up to "
        f"**{_SUPERIORITY_MAX_AT_SEA:+.2f}** to the odds of a fleet action. On land air "
        "decides how well an attack goes; at sea it often decides the action itself."
    )
    lines.append(
        "- **Aircraft no longer count toward the strength of a land or sea battle.** "
        "What they contribute is the sky, and what the sky is worth is above."
    )
    lines.append(
        "- A **strike** (`air_strike`) hits a region within reach without occupying it. "
        "Aim it at `forces` or at `infrastructure`. It is met by the defender's fighters "
        "and air-defence technology, and it can be broken up entirely. **A strike never "
        "takes ground.**"
    )
    # Told, because a staff with no way to learn this will never order it, and hitting an
    # enemy fleet at sea is most of what carrier aircraft are for.
    lines.append(
        "- **A strike at open sea hits the enemy fleet in it**, not the water: aim at "
        "`forces` over a sea an enemy squadron is sitting in. `infrastructure` is refused "
        "there -- there is nothing built on open water."
    )
    # **Recorded, and on.** Both bullets are prompt text: they were written behind
    # :mod:`atlas.engine.rerecord`'s switch, and the live re-record that pays for a changed
    # prompt has since been run with the switch on, so the active tape holds them. The
    # switch stays as the seam a test reads both ways.
    from .rerecord import enabled as _rebrief

    if _rebrief():
        from .logistics import _INTERDICTION_COST, _SUPPLY_RANGE

        lines.append(
            "- **An enemy-held sky interdicts your supply underneath it.** Moving supply "
            f"through a province whose sky an enemy holds costs **{_INTERDICTION_COST} "
            f"extra hops** out of a **{_SUPPLY_RANGE}-hop** budget, so an interdicted "
            "corridor runs out in about a third of the distance a clear one does. "
            "Formations beyond the end of it are out of supply. This is the strongest "
            "single thing the air war does to a land campaign."
        )
        lines.append(
            f"- **A sky is maintained, not owned.** An unflown sky loses "
            f"**{_LAPSE_PER_MONTH}** of its margin a month and is let go of entirely under "
            f"**{_CONTROL_FLOOR}**. Where both sides can still reach a region it is fought "
            "over again instead, and does not decay."
        )
    known = [
        t for t in conn.execute(
            "SELECT tech_id FROM technology WHERE nation_id = ?", (nation_id,)
        ).fetchall()
    ]
    ids = {str(r["tech_id"]) for r in known}
    mine = sorted(ids & (set(INTERCEPT_TECHS) | set(PENETRATION_TECHS)))
    if mine:
        lines.append("- Your air technologies: " + ", ".join(f"`{t}`" for t in mine) + ".")
    return "\n".join(lines)
