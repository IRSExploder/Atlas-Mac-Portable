"""Getting an army across water it does not own.

**Land formations never go to sea.** That is the whole design decision, and everything
else follows from it. The obvious alternative -- embark divisions onto the sea map and
sail them -- costs the one invariant that keeps the map honest: rule UNIT-002 says an army
is not at sea, and a rule with an exception in it is a rule that has to be checked twice
everywhere. So an amphibious assault is an ordinary contest whose *reachability* is
unusual, rather than an ordinary movement into an unusual place.

A power may attack ground it does not border when three things hold at once:

**There is water between.** Some sea touches both the objective and something the power
holds. Not an ocean away: one sea, which is the same one-region reach that makes a
bordering province the only province an army can invade.

**That water is not somebody else's.** Water an enemy controls is closed, and so is water
where an enemy has the stronger fleet -- a sea nobody has formally taken is still denied by
whoever is sitting in it, which is what a fleet in being *is*. Without that second half,
Germany could put an army into Britain in 1936 across a North Sea containing the entire
Home Fleet, on the grounds that nobody had got round to declaring it theirs.

**There are ships to carry them.** Transports in that sea, or in a port on it. A landing
is a decision about whether the sea is safe enough to risk them, which is what makes a
transport worth building and worth sinking.

None of this adds a channel, a column or a write path. A power declares ``push`` on a
coastal region the way it declares ``push`` on anything else, and the engine either finds
it a route or refuses it one.
"""

from __future__ import annotations

import sqlite3

__all__ = ["LIFT_KIND", "amphibious_reach", "crossable", "sea_lift"]

#: What one transport can put ashore, in formations. Small on purpose: a landing wants a
#: fleet behind it, and a single requisitioned merchantman is not an invasion.
LIFT_PER_TRANSPORT = 1

#: The ``unit.kind`` an amphibious landing is carried by, **looked up by name**.
#:
#: That is a real coupling between the engine and every scenario's order of battle, and it
#: was written as a bare string in two SQL queries in two modules. An era whose designer
#: called its shipping something else -- ``landing_ship``, ``sealift``, ``lst`` -- would
#: keep every other naval mechanic and silently lose the ability to put an army ashore
#: anywhere on its map, with nothing anywhere reporting it.
#:
#: Named here so there is one place to change and one thing for a test to check. See
#: ``tests/test_every_era_can_land_an_army.py``, which walks the scenario registry rather
#: than naming eras: a fifth era added without shipping fails that test at the moment it is
#: added, which is the only time the fix is cheap.
LIFT_KIND = "transport"


def lift_hulls(
    conn: sqlite3.Connection, world_id: str, nation_id: str, sea_ids
) -> set[str]:
    """The distinct transports this power could load across any of ``sea_ids``.

    Counts hulls in the water itself and in the power's own ports on it. A ship in harbour
    is a ship that can sail this month; the distinction that matters is whether it is on the
    right sea, not whether it has already left.

    **A port is a place on land.** Without ``r.kind <> 'ocean'`` the second branch read an
    *adjacent sea this power controlled* as a harbour, so transports floating one sea over
    counted as lift here -- and then, because the same slip in :func:`amphibious_reach` made
    that neighbouring sea a launching point, the reach compounded. A transport in the water
    itself is the first branch and is unaffected.

    **Ids rather than a count, and several seas rather than one**, because two readers want
    this and want it differently. :func:`sea_lift` asks about one sea and wants a number;
    `resolve._from_the_sea` asks across every sea an assault could cross and must not count
    one hull twice for a port that happens to touch two of them. Both go through here, so
    they cannot come to disagree about what shipping is available -- which they did: the
    strength term kept its own copy of this question, restricted to land regions, and so
    stopped seeing any transport that had put to sea.
    """
    seas = tuple(sea_ids)
    if not seas:
        return set()
    marks = ", ".join("?" for _ in seas)
    rows = conn.execute(
        f"""
        SELECT DISTINCT u.id FROM unit u
          JOIN region r ON r.id = u.region_id
         WHERE u.world_id = ? AND u.nation_id = ? AND u.kind = ?
           AND u.strength > 0
           AND (
                u.region_id IN ({marks})
             OR (r.kind <> 'ocean'
                 AND COALESCE(r.controller_nation_id, r.owner_nation_id) = u.nation_id
                 AND EXISTS (SELECT 1 FROM region_adjacency a
                              WHERE a.world_id = u.world_id
                                AND ((a.region_a = r.id AND a.region_b IN ({marks}))
                                  OR (a.region_b = r.id AND a.region_a IN ({marks})))))
           )
        """,  # noqa: S608 - the placeholders are generated, the values are bound
        (world_id, nation_id, LIFT_KIND, *seas, *seas, *seas),
    ).fetchall()
    return {str(row["id"]) for row in rows}


def sea_lift(conn: sqlite3.Connection, world_id: str, nation_id: str, sea_id: str) -> int:
    """How many formations this power could put ashore from ``sea_id``.

    The count of :func:`lift_hulls` over one sea. See there for what counts as loadable.
    """
    return len(lift_hulls(conn, world_id, nation_id, (sea_id,))) * LIFT_PER_TRANSPORT


def _sea_is_ours(
    conn: sqlite3.Connection, nation_id: str, enemies: set[str], sea_id: str
) -> bool:
    """Whether this power can move troops across ``sea_id`` without meeting a stronger fleet.

    Measured with the same :func:`~atlas.sim.resolve._strength` that decides a fleet action,
    so "who is stronger here" has exactly one answer in the codebase and a landing cannot
    disagree with the battle that would settle it.
    """
    from ..sim.resolve import _strength

    if not enemies:
        return True
    ours = _strength(conn, nation_id, sea_id)
    return all(_strength(conn, enemy, sea_id) <= ours for enemy in sorted(enemies))


def crossable(
    conn: sqlite3.Connection, nation_id: str, enemies: set[str], sea_id: str
) -> bool:
    """**The one question every layer that crosses water was asking differently.**

    A sea is open to a power when *both* halves hold: nobody else has formally taken it,
    and no enemy has the stronger fleet in it. Four layers needed that answer and three of
    them had only one half, so the same water was open to a merchant convoy, closed to a
    landing, and open again to a supply column -- from one state of the world.

    * :func:`amphibious_reach` asked both, and was the only one that did.
    * :func:`atlas.engine.logistics.supply_map` and
      :func:`atlas.engine.trade._lane_open` asked only whose fleet was stronger, so a sea
      an enemy had *won* -- formally taken, controller written on the region -- still
      passed supply and still carried trade. Taking a sea bought its winner nothing that
      the fleet sitting in it had not already bought.
    * :func:`atlas.sim.resolve._from_the_sea` asked only about control, and asked it
      backwards: ``controller_nation_id IS NULL`` refused a landing across a power's
      *own* sea -- winning the water you were going to cross closed the crossing -- while
      admitting one straight through a sea containing an enemy battle fleet.

    ``enemies`` is passed rather than looked up because every caller already has it, and
    because the caller decides *whose* enemies: a blockade is not one-sided, and
    :func:`atlas.engine.trade._lane_open` asks about both ends of a lane.
    """
    row = conn.execute(
        "SELECT controller_nation_id FROM region WHERE id = ?", (sea_id,)
    ).fetchone()
    controller = row["controller_nation_id"] if row else None
    if controller is not None and controller != nation_id:
        return False
    return _sea_is_ours(conn, nation_id, enemies, sea_id)


def amphibious_reach(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> dict[str, str]:
    """Coastal ground this power could land on, mapped to the sea it would come across.

    Returns the objective's region id against the sea that carries the assault, so a
    caller can say *how* a landing is possible rather than only *that* it is.
    """
    holder = {
        row["id"]: (row["kind"], row["owner"], row["holder"])
        for row in conn.execute(
            "SELECT id, kind, owner_nation_id AS owner,"
            " COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?",
            (world_id,),
        )
    }
    beside: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT region_a, region_b FROM region_adjacency WHERE world_id = ?", (world_id,)
    ):
        a, b = row["region_a"], row["region_b"]
        beside.setdefault(a, set()).add(b)
        beside.setdefault(b, set()).add(a)

    # **Ground, not water.** A sea only launches an assault if this power holds a *shore*
    # on it. With seas in `mine`, a sea adjacent to a sea this power had taken counted as
    # touching its territory -- so winning one stretch of water opened a landing on every
    # coast of the next one over, from a country that has no port on it at all. The
    # one-sea reach at the top of this module is the whole design, and that made it two.
    mine = {
        region
        for region, (kind, _o, held) in holder.items()
        if held == nation_id and kind != "ocean"
    }
    enemies = {
        row["nation_b"]
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id),
        )
    }
    routes: dict[str, str] = {}

    for sea in sorted(holder):
        kind, _owner, _controller = holder[sea]
        if kind != "ocean":
            continue
        shores = beside.get(sea, set())
        if not (shores & mine):
            continue
        if sea_lift(conn, world_id, nation_id, sea) < 1:
            continue
        if not crossable(conn, nation_id, enemies, sea):
            continue
        for shore in sorted(shores):
            if shore in mine or holder.get(shore, ("", None, None))[0] == "ocean":
                continue
            routes.setdefault(shore, sea)
    return routes
