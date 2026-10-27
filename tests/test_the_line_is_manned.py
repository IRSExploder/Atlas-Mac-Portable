"""A front line, rather than an army in one province and a country standing open.

Measured before this existed, on a twenty-four power map: **only half of all threatened
provinces had anybody standing on them at all**, and on the Ukraine front the defender's
entire army -- thirteen formations -- was stacked in a single oblast while five of its own
provinces stood empty. The attacker did the same with fourteen and fifteen. Ground changed
hands with nobody on it.

That is why every defensive mechanic in the engine measured as inert. Terrain, entrenchment,
resilience and the supply swing are all multipliers applied to a defender, and in a province
with no defender in it they all multiply nothing.

Two causes, and both are here. Declaring an offensive *replaced* a power's defensive
frontier in the march graph instead of joining it, so naming one target sent the whole army
walking toward it; and nothing was ever kept back, so the last formation in a threatened
province would leave it.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.objectives import declare_objectives
from atlas.engine.resolution import GARRISON_TARGET, ResolutionReport, _march_to_the_front
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
US, THEM = "aurelia", "veyra"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = ?"
        "   AND ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
        (WORLD, US, THEM, THEM, US))
    connection.commit()
    yield connection
    connection.close()


def _ours(conn) -> list[str]:
    return [str(r["id"]) for r in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id", (WORLD, US))]


def _theirs(conn) -> list[str]:
    return [str(r["id"]) for r in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id", (WORLD, THEM))]


def _frontier(conn) -> list[str]:
    """Our provinces that border theirs, which is what the march calls threatened."""
    return [str(r["id"]) for r in conn.execute(
        "SELECT DISTINCT r.id FROM region r"
        "  JOIN region_adjacency a ON (a.region_a = r.id OR a.region_b = r.id)"
        "                        AND a.world_id = r.world_id"
        "  JOIN region o ON o.id = CASE WHEN a.region_a = r.id THEN a.region_b"
        "                              ELSE a.region_a END"
        " WHERE r.world_id = ? AND r.owner_nation_id = ? AND r.kind <> 'ocean'"
        "   AND o.owner_nation_id = ? AND o.kind <> 'ocean' ORDER BY r.id",
        (WORLD, US, THEM))]


def _army(conn, region: str, count: int, *, nation: str = US) -> None:
    for index in range(count):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, ?, ?, 'infantry', 6.0, 60)",
            (f"u_{nation}_{region}_{index}", WORLD, nation, region))
    conn.commit()


def _march(conn, turn: int = 1) -> ResolutionReport:
    report = ResolutionReport()
    _march_to_the_front(conn, WORLD, turn=turn, date="1897-04-01",
                        spoken_for=set(), report=report)
    return report


def _where(conn, nation: str = US) -> dict[str, int]:
    return {str(r["region_id"]): int(r["n"]) for r in conn.execute(
        "SELECT region_id, COUNT(*) n FROM unit WHERE world_id = ? AND nation_id = ?"
        " GROUP BY region_id", (WORLD, nation))}


def test_the_last_formation_on_threatened_ground_does_not_leave_it(conn):
    """**The garrison, taken off the top.** Somebody has to still be there.

    A province a power holds that borders an enemy keeps its formation before anything is
    released to march, because a defensive bonus is paid to a defender and the engine had
    no way to ensure there was one.
    """
    frontier = _frontier(conn)
    assert frontier, "the sandbox must put these two in contact for this to mean anything"
    _army(conn, frontier[0], GARRISON_TARGET)

    _march(conn)

    assert _where(conn).get(frontier[0], 0) == GARRISON_TARGET


def test_declaring_an_offensive_no_longer_empties_the_country(conn):
    """The defect, in one test.

    The frontier used to be a *fallback* consulted only when a staff had declared nothing,
    so naming a single target deleted the entire defensive line from the march graph and
    the whole army walked off it.
    """
    frontier, theirs = _frontier(conn), _theirs(conn)
    for region in frontier:
        _army(conn, region, 1)
    declare_objectives(conn, WORLD, nation_id=US, turn=1, date="1897-04-01",
                       declared={"push": [theirs[0]]})

    _march(conn)

    manned = sum(1 for region in frontier if _where(conn).get(region, 0) > 0)
    assert manned == len(frontier), (
        f"only {manned} of {len(frontier)} threatened provinces still have a garrison")


def test_the_surplus_goes_to_the_part_of_the_line_that_has_nobody_on_it(conn):
    """**Man the line, then attack**, which is the half a union of the two sets misses.

    A `push` makes the province the assault is staged from a destination in its own right.
    Every formation standing there is then at distance zero and stays, so the whole army
    sits in one place while the rest of the frontier stands open -- measured, and it was
    still eight formations in one oblast with five empty beside it.
    """
    # The sandbox gives Aurelia a one-province frontier, and a front one province wide has
    # nowhere to spread *to*. Westfell already borders Veyra, so handing it over widens the
    # line to two without inventing a map: this is the smallest world in which "the surplus
    # goes somewhere else" is a statement that can be false.
    conn.execute(
        "UPDATE region SET owner_nation_id = ?, controller_nation_id = NULL"
        " WHERE world_id = ? AND id = 'r_west'", (US, WORLD))
    # ...and joined to the rest of the country, because a march is a march. Two provinces
    # a formation cannot walk between are not a front two provinces wide.
    conn.execute(
        "INSERT OR IGNORE INTO region_adjacency (world_id, region_a, region_b)"
        " VALUES (?, 'r_north', 'r_west')", (WORLD,))
    conn.commit()
    frontier, theirs = _frontier(conn), _theirs(conn)
    assert len(frontier) >= 2, frontier
    _army(conn, frontier[0], 6)
    declare_objectives(conn, WORLD, nation_id=US, turn=1, date="1897-04-01",
                       declared={"push": [theirs[0]]})

    for turn in range(1, 5):
        _march(conn, turn=turn)

    placed = _where(conn)
    assert placed.get(frontier[0], 0) < 6, "the stack never broke up"
    assert sum(1 for region in frontier if placed.get(region, 0) > 0) > 1, (
        f"the army is still in one place: {placed}")


def test_ground_the_staff_is_giving_up_is_not_garrisoned(conn):
    """A withdrawal is a decision, and a quota must not quietly countermand it.

    The `hold` is what gives the retreat somewhere to go: this map is three provinces wide,
    and a formation with no destination in the graph stays put for reasons that have
    nothing to do with garrisons -- which would make this test pass without testing
    anything.
    """
    frontier = _frontier(conn)
    rear = [region for region in _ours(conn) if region not in frontier]
    if not rear:
        pytest.skip("this map gives the defender no ground behind its own frontier")
    _army(conn, frontier[0], 1)
    declare_objectives(
        conn, WORLD, nation_id=US, turn=1, date="1897-04-01",
        declared={"withdraw_from": [frontier[0]], "hold": [rear[0]]})

    _march(conn)

    assert _where(conn).get(frontier[0], 0) == 0, (
        "a formation was held on ground its own staff said it was leaving")
    assert _where(conn).get(rear[0], 0) == 1


def test_a_power_at_peace_marches_nowhere(conn):
    """No enemies, no frontier, no reason to move anybody. Unchanged by any of this."""
    conn.execute("UPDATE relation SET at_war = 0 WHERE world_id = ?", (WORLD,))
    conn.commit()
    frontier = _frontier(conn)
    _army(conn, frontier[0], 3)
    before = _where(conn)

    _march(conn)

    assert _where(conn) == before


def test_the_garrison_is_decided_the_same_way_twice(conn):
    """Deterministic by construction: which formation stays is settled in unit-id order.

    Nothing here draws from the seeded stream, so a rewind lands on the world that was
    played rather than on a differently-garrisoned one.
    """
    frontier, theirs = _frontier(conn), _theirs(conn)
    for region in frontier:
        _army(conn, region, 3)
    declare_objectives(conn, WORLD, nation_id=US, turn=1, date="1897-04-01",
                       declared={"push": [theirs[0]]})
    _march(conn)
    first = _where(conn)

    second = create_world(":memory:", sandbox_seed())
    second.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    second.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = ?"
        "   AND ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
        (WORLD, US, THEM, THEM, US))
    second.commit()
    for region in _frontier(second):
        _army(second, region, 3)
    declare_objectives(second, WORLD, nation_id=US, turn=1, date="1897-04-01",
                       declared={"push": [_theirs(second)[0]]})
    report = ResolutionReport()
    _march_to_the_front(second, WORLD, turn=1, date="1897-04-01",
                        spoken_for=set(), report=report)
    again = _where(second)
    second.close()

    assert first == again
