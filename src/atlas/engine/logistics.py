"""Supply: how far an army can get from the country behind it.

An army could march to the far side of the world and fight exactly as well there as at
home, and a province taken behind a front changed nothing about the front. This is the
answer, and it needed **no schema at all** -- which is the design decision worth arguing
with rather than a happy accident.

**Supply is a reading of the map, not a fact about a unit.** Who controls what, which seas
are held, where the sky is contested -- all of it changes several times a turn, so a stored
supply state would be a cache invalidated by every conquest, every landing and every air
battle, and one a rewind would have to unwind. Derived, it cannot be stale, and
rewind-exactness comes free. The only thing written down is what being out of supply
*cost*: attrition, as ordinary diffs on ``unit.strength`` citing one event.

The trace is a multi-source breadth-first walk over the adjacency graph the air layer
already builds, and each of its three rules answers something that should be possible:

**Depots are your own core, not merely ground you hold.** A supply base needs an
administration behind it and occupied ground has somebody else's. This is what makes a deep
invasion a *corridor* rather than a spreading stain -- an army in Belgium is supplied from
Germany through the ground between, and the length of that thread is the campaign's real
constraint.

**Ground you do not control does not pass supply.** Take one province behind the front and
everything beyond it is cut, this turn, with no encirclement rule written anywhere.

**Seas are regions, so supply crosses them the way armies do.** The graph already holds a
hundred-odd ocean regions as real nodes, so an overseas theatre is supplied by walking
through the sea -- and :func:`atlas.engine.naval.crossable`, the same function that
decides whether a landing can happen, decides whether the walk passes. A blockade therefore
strangles supply without a line of code saying so. Both halves of it: a sea an enemy has
*taken* is shut as firmly as one its fleet merely sits in.

**Enemy air control costs range rather than blocking it**, so interdiction shortens the
thread and can snap it at the far end. Blocking outright would make air superiority
decisive on its own, which is a larger claim about air power than the air layer itself
makes.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .air import _neighbours, control_of
from .pacing import over, span_of

__all__ = [
    "SupplyReport",
    "assess_attrition",
    "in_supply",
    "supplied_at_front",
    "supply_map",
    "supply_multiplier",
]


#: How many provinces a depot's supply reaches.
#:
#: **Set by sweeping it on the real map, and the first guess was three times too generous.**
#: ATLAS's maps are coarse -- 429 regions across 242 nations, so most countries are a single
#: province and only a handful are subdivided -- which means hop counts are far smaller than
#: a province-level wargame would suggest. Measured: a Russia holding thirty provinces from
#: Vladivostok to the Rhine never needs supply to travel more than **three** hops, because it
#: has eight depots spread through its own core. At the first value of 6 the range constraint
#: could not bite on any campaign the map can produce, and the layer would have been
#: continuity and interdiction with a dead parameter attached.
#:
#: The sweep, own-country coverage against a Russia occupying fifteen countries:
#:
#: ===== ==================== =================
#: range worst power at home  deep conquest fed
#: ===== ==================== =================
#: 2     90% (India)          66%
#: 3     100%                 91%
#: 4     100%                 97%
#: 5     100%                 100%
#: ===== ==================== =================
#:
#: 3 is the only value that feeds every power's own country while making an overextended
#: conquest go short. 2 fails India at home, which would be a tax on defending rather than
#: a supply layer.
#:
#: **That sweep was Modern Day only, and the sentence above is not true of 1936.** Measured
#: since, on both maps: 2026 feeds 316 of 316 owned land regions, and 1936 feeds 352 of 375
#: -- because **Italy feeds 11 of its 30**, which is Italian East Africa, and France 8 of 12.
#: Raising the range does not fix it (5 recovers one region in the whole era); lowering
#: :data:`_DEPOT_DEVELOPMENT` from 0.8 to 0.3 recovers 19 of the 23. So the constant that
#: does not survive a finer map is the **depot threshold**, not the range: an absolute
#: development bar applied to a quantity that is divided among however many provinces a
#: country happens to be drawn with. Italy has nine provinces over 0.8 and twenty-one under.
#:
#: The consequence is not academic and is left for Alex rather than tuned here, because it
#: is a balance judgement with scenario weight: eight Italian formations in Eritrea are out
#: of supply on turn 1 of every 1936 game, at peace, and **Italy loses 16% of its total
#: military strength over the first peaceful year**. World-wide it is those eight formations
#: out of 442, so this is Italy alone -- and it is every game.
_SUPPLY_RANGE = 3

#: What a province has to be worth before it can supply an army. A depot is railheads,
#: warehouses and a garrison town, not a map square.
#:
#: **0.8 to 0.3, which is the prescription this module has carried since 2026-08-08 and
#: could not act on until the depot query stopped asking `home_nation_id`.** The reasoning
#: is unchanged and is above: an *absolute* development bar applied to a quantity divided
#: among however many provinces a country happens to be drawn with is a bar that means
#: something different for Italy's thirty provinces than for a minor's one. Italy has nine
#: provinces over 0.8 and twenty-one under, and the twenty-one are not villages.
#:
#: Re-swept on gathering-storm with the ownership fix in, over 374 owned land regions:
#:
#: ==== ===== ================= =========================
#: bar  unfed worst power       what it costs
#: ==== ===== ================= =========================
#: 0.8  130   gbr 14/81         eight Italian formations starve at peace
#: 0.3  8     m_prt 4/8         Eritrea and Somaliland fed; Portugal's islands not
#: 0.2  0     --                every power feeds everything it holds
#: ==== ===== ================= =========================
#:
#: **0.3 rather than 0.2 deliberately.** 0.2 is not a better number for feeding a country,
#: it is the number at which the bar stops existing: it admits every land province on the
#: 1936 map, and a constant that refuses nothing is a dead parameter. The eight left short
#: at 0.3 are Portuguese and Spanish Atlantic islands and two Somali provinces, all of them
#: genuinely remote, and a supply layer that never says no anywhere is not one.
#:
#: **This is the balance judgement, and it is the one to argue with.** It makes overseas
#: empire cheap to hold: no colonial garrison anywhere starves at peace, which is right for
#: 1936 Eritrea and is a gift to a player who takes Java. The alternative shape -- a
#: separate, higher bar for ground whose `home_nation_id` is NULL, so a colony supplies but
#: costs more to make into a base -- is one query away and is not taken here because it
#: invents a second constant to avoid arguing about the first.
#:
#: **Settled 2026-09-18**, with the one question the depot battery left open: a colony may
#: also supply a front opened *from* it (Italy into Ethiopia through Eritrea, as in 1935).
#: No policy feeds Eritrea at peace and refuses that front, so refusing it would bring the
#: peacetime starvation back. Pinned in `tests/test_a_colony_can_never_be_a_depot.py`.
_DEPOT_DEVELOPMENT = 0.3

#: Extra distance charged for crossing a province whose sky the enemy holds. Not a block --
#: see the module docstring. Two hops means an interdicted corridor runs out in a third of
#: the distance a clear one does.
_INTERDICTION_COST = 2

#: Extra distance charged for crossing water. A sea lane is longer than the map makes it
#: look, and this is what stops an island chain being as cheap to supply as a plain.
_SEA_COST = 2

#: What an out-of-supply formation loses per month, as a share of its strength. A pocket
#: dies in about a year unaided -- long enough that its owner has several turns to break
#: out, relieve it or give the ground back, which is the whole point of a supply layer that
#: is a problem rather than a verdict.
_ATTRITION_PER_MONTH = 0.08

#: The least strength attrition will grind a formation down to. Below this it is a cadre
#: rather than a unit, and deleting it is the resolver's business rather than the
#: quartermaster's.
_ATTRITION_FLOOR = 0.5

#: What an unsupplied force is worth in a fight. Applied through the same doors the air and
#: leader layers use, so the three compose and none can secretly mean something else.
_UNSUPPLIED_EFFECTIVENESS = 0.55


@dataclass(slots=True)
class SupplyReport:
    assessed: int = 0
    #: One line per formation that starved this turn.
    starved: list[str] = field(default_factory=list)
    #: Nations with at least one formation out of supply, worst first.
    cut_off: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _enemies_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> set[str]:
    return {
        str(row["other"])
        for row in conn.execute(
            "SELECT nation_b AS other FROM relation"
            " WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id),
        )
    }


def _depots(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    """Where this nation's supply comes from.

    Its own territory, still held by it, and developed enough to hold anything. The capital
    is always one whatever its development says, because a country that cannot supply an
    army from its own capital is not a country the rest of the engine would recognise.

    **Sovereignty is asked of ``owner_nation_id``, not ``home_nation_id``, and the
    difference is a regression this restores.** The rule the module docstring states --
    depots are your own core, not merely ground you hold -- is about *conquest*: an army in
    Belgium is fed from Germany through the ground between, and taking a province does not
    hand you a supply base in it. ``home_nation_id`` expressed that exactly for as long as
    `create_world` seeded it equal to the owner.

    It stopped expressing it on 2026-08-09, when `24ef42d` made
    :func:`atlas.scenarios.seeder._disown_dependencies` NULL the column on every colony and
    dependency -- correctly, and for a reason that has nothing to do with supply: Hong Kong
    was British homeland and therefore nuclear-protected, which is the opposite of the rule
    `nuclear.deterrence` was written to obey. A dependency's *home* is nobody. Its *owner*
    is Britain, and Britain has an administration in it.

    ``NULL = 'gbr'`` is NULL, so from that day no colony could be a depot at any
    development, behind any port. Measured on gathering-storm: 146 of 374 owned land
    regions unfed, Britain feeding 14 of the 81 it holds, and eight Italian formations in
    East Africa out of supply on turn 1 of every game, at peace. The supply sweep in the
    module docstring predates it by one day and its prescription -- lower
    :data:`_DEPOT_DEVELOPMENT` to 0.3 -- has recovered nothing since, because the bar it
    lowers is never reached.

    Two columns, two questions, and only one of them was ever about supply: ``home`` is
    "whose people are these", which deterrence and unrest want; ``owner`` is "whose
    administration is this", which supply wants. Conquered ground still supplies nobody --
    an occupier holds the controller column and not the owner column, so the corridor rule
    is untouched.

    **And an army posted on an ally's ground supplies itself from where it stands.** Added
    2026-09-20, after the Fulda Gap match: `Scenario.deployments` can post the Seventh Army
    in Bayern and the Group of Soviet Forces in Thüringen, and supply had no way to feed
    either, because a depot had to be ground the nation *owned*. 18 of that era's 21 posted
    divisions began out of supply, losing strength every month and fighting at
    :data:`_UNSUPPLIED_EFFECTIVENESS`, before any government decided anything -- a tax on
    the one era built to watch governments decide.

    A forward base is the narrow answer to that, and deliberately narrower than the obvious
    one. **Supply still does not travel *through* an ally**: the walk below crosses only
    ground this nation holds, unchanged. What this adds is a *source* where the army
    already is, if four things are true -- it has forces standing there, the ground is held
    by a power it has a defensive pact or better with, neither is at war with the other,
    and the province clears the same development bar its own ground has to clear. That is
    what a garrison abroad is: Rhine Army had depots on the Rhine, not a supply line from
    Catterick.

    The alternative was to let supply cross allied ground generally, which is more
    realistic still and much larger: it changes the walk for every nation in every era, and
    so changes played worlds and the recorded tape. This rule cannot reach an era where
    nobody stands on an ally's ground, which is every shipped era but the Fulda Gap. To
    revert it, delete the second query below; nothing else reads it.
    """
    rows = conn.execute(
        """
        SELECT r.id FROM region r
         WHERE r.world_id = ? AND r.kind <> 'ocean'
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
           AND r.owner_nation_id = ?
           AND (r.development >= ?
                OR r.id = (SELECT capital_region_id FROM nation WHERE id = ?))
         ORDER BY r.development DESC, r.id
        """,
        (world_id, nation_id, nation_id, _DEPOT_DEVELOPMENT, nation_id),
    ).fetchall()
    stations = conn.execute(
        """
        SELECT r.id FROM region r
          JOIN relation host
            ON host.world_id = r.world_id
           AND host.nation_a = COALESCE(r.controller_nation_id, r.owner_nation_id)
           AND host.nation_b = ?
         WHERE r.world_id = ? AND r.kind <> 'ocean'
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) <> ?
           AND host.at_war = 0
           AND host.treaty_status IN ('defensive', 'alliance', 'vassal')
           AND r.development >= ?
           AND EXISTS (SELECT 1 FROM unit u
                        WHERE u.world_id = r.world_id AND u.nation_id = ?
                          AND u.region_id = r.id)
         ORDER BY r.development DESC, r.id
        """,
        (nation_id, world_id, nation_id, _DEPOT_DEVELOPMENT, nation_id),
    ).fetchall()
    # Own ground first and in its own order, so a nation with no army abroad -- every
    # nation of every era but one -- gets exactly the list it got before.
    return [str(row["id"]) for row in rows] + [str(row["id"]) for row in stations]


def supply_map(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> dict[str, int]:
    """Every region this nation can supply, mapped to the distance supply travelled.

    A weighted multi-source walk: a plain land step costs one, water and interdicted sky
    cost more, and anything past :data:`_SUPPLY_RANGE` is out of reach. Dijkstra-shaped
    rather than plain breadth-first because the steps are no longer all the same size --
    with weights, the first time a node is *seen* is no longer the cheapest way to it.
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

    # Cached per region, because the sea test runs a strength query and a busy ocean can
    # be reached from several directions in one walk.
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
                        conn, nation_id, enemies, neighbour
                    )
                if not passable_sea[neighbour]:
                    continue
                step = _SEA_COST
            elif neighbour not in controlled:
                # Ground somebody else holds does not pass supply. The whole point.
                continue
            else:
                step = 1
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


def in_supply(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str,
    *, reach: dict[str, int] | None = None,
) -> bool:
    """Whether this nation can supply a force at ``region_id``.

    ``reach`` is the nation's :func:`supply_map`, passed in when a caller is asking about
    several regions at once -- the walk is much the more expensive half.
    """
    if reach is None:
        reach = supply_map(conn, world_id, nation_id)
    return region_id in reach


def supplied_at_front(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> bool:
    """Whether this nation's supply reaches a fight *over* ``region_id``.

    **Not the same question as :func:`in_supply`, and conflating the two was a real bug
    rather than a nicety.** An attacker by definition does not control the province it is
    attacking, so it is never in supply *there* -- testing it that way made every attack
    anywhere in the world unsupplied, and a conquest that took 16 turns took 34.

    The honest test is whether the thread reaches the front: the region itself, or any
    province beside it. A pocket still fails it, because a pocket is precisely a place
    whose neighbours are all held by somebody else -- so the encirclement lever survives
    intact, which is the thing this must not give away to fix the other.

    **Beside it by land, or across water an assault could actually cross.** "Any
    neighbour" included the ocean, and that is a free crossing. :func:`supply_map` walks
    seas deliberately and *pays* for them -- :data:`_SEA_COST` a crossing, inside
    :data:`_SUPPLY_RANGE`, and only across water :func:`atlas.engine.naval.crossable`
    allows. This then handed back a hop that paid nothing, so a coast the supply map had
    refused as out of range was supplied anyway, because the water in front of it was not.
    One free step past the limit the whole layer is built on, with no shipping needed.

    A water hop is admitted only where :func:`atlas.engine.naval.amphibious_reach` says a
    landing could be mounted -- ships present, the sea passable, one crossing. The same
    predicate that decides whether the assault is possible decides whether it can be fed,
    which is the arrangement the rest of this layer already has.
    """
    reach = supply_map(conn, world_id, nation_id)
    if region_id in reach:
        return True

    oceans = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    }
    graph = _neighbours(conn, world_id)
    if any(
        neighbour in reach and neighbour not in oceans
        for neighbour in graph.get(region_id, ())
    ):
        return True

    from .naval import amphibious_reach

    route = amphibious_reach(conn, world_id, nation_id).get(region_id)
    return route is not None and route in reach


def supply_multiplier(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> float:
    """What a force fighting over ``region_id`` is worth, given whether it can be supplied.

    ``1.0`` in supply, :data:`_UNSUPPLIED_EFFECTIVENESS` out of it. Read by
    :mod:`atlas.sim.resolve` through the same doors the air and leader layers use, and it
    asks :func:`supplied_at_front` rather than :func:`in_supply` -- see there for why the
    distinction is load-bearing.
    """
    return 1.0 if supplied_at_front(conn, world_id, nation_id, region_id) else (
        _UNSUPPLIED_EFFECTIVENESS
    )


def assess_attrition(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> SupplyReport:
    """Grind down every formation that cannot be supplied where it stands.

    The one part of this layer that writes anything, and it writes the *consequence* rather
    than the state: strength, as ordinary diffs, so it rewinds like everything else.

    Only nations that actually have units are walked, and each nation's supply map is built
    once and reused across all of its formations -- the walk is the expensive half and a
    world with a hundred armed powers would otherwise rebuild the same graph a hundred
    times.

    A *flow*, so a season-long turn costs a season of starvation rather than a month of it.
    """
    span = span_of(days)
    report = SupplyReport()

    armed = [
        str(row["nation_id"])
        for row in conn.execute(
            "SELECT DISTINCT nation_id FROM unit WHERE world_id = ? ORDER BY nation_id",
            (world_id,),
        )
    ]
    if not armed:
        return report

    starving: list[tuple[str, str, str, float, float]] = []
    for nation_id in armed:
        reach = supply_map(conn, world_id, nation_id)
        units = conn.execute(
            """
            SELECT u.id, u.region_id, u.strength, u.kind, r.name AS where_name,
                   n.name AS whose
              FROM unit u
              JOIN region r ON r.id = u.region_id
              JOIN nation n ON n.id = u.nation_id
             WHERE u.world_id = ? AND u.nation_id = ? AND r.kind <> 'ocean'
             ORDER BY u.id
            """,
            (world_id, nation_id),
        ).fetchall()
        for unit in units:
            report.assessed += 1
            if str(unit["region_id"]) in reach:
                continue
            strength = float(unit["strength"])
            if strength <= _ATTRITION_FLOOR:
                continue
            lost = min(
                strength - _ATTRITION_FLOOR,
                round(over(strength * _ATTRITION_PER_MONTH, span), 2),
            )
            if lost < 0.01:
                continue
            starving.append((
                str(unit["id"]), str(unit["whose"]), str(unit["where_name"]),
                strength, lost,
            ))

    if not starving:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text="Formations beyond the reach of their depots go short.",
        visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff("unit", unit_id, "strength", DiffOp.ADJUST, -lost,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for unit_id, _whose, _where, _strength, lost in starving
    ]
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"attrition could not be applied: {outcome.reason}")
        return report

    for _id, whose, where, strength, lost in starving:
        report.starved.append(
            f"{whose} in {where} is cut off and going short "
            f"({strength:.1f} -> {strength - lost:.1f})"
        )
        if whose not in report.cut_off:
            report.cut_off.append(whose)
    return report
