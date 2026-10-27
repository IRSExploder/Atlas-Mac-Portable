"""Winning a sea gave the army the sea's adjacency, and then froze the land war.

Five queries answered "what does this nation hold" with ``COALESCE(controller_nation_id,
owner_nation_id) = ?`` and no ``kind`` test, so a sea a power had taken was a province it
held. Two consequences, and they pull in opposite directions:

**A coast became invadable without ships.** ``_adjacent_to_holdings`` asks whether the
objective borders anything the mover holds. A sea it had taken borders every coast that sea
touches -- so an army walked across open water onto a defended beach with no transports, no
lift cap, and none of what makes an amphibious assault a decision. `amphibious_reach` exists
to ration exactly that reach, and winning the water handed it over for free.

**And the land war stopped.** ``_held_by`` feeds ``_distance_to_objectives``, the graph the
reinforcement march walks. With an ocean in the graph, a division at the coast is ordered
one step "forward" into the sea; rule UNIT-002 names no diff, so it cannot be severed and
the **whole** batch is refused -- every other formation that was going to move stays where
it is. The more successful a power's navy, the more of its own army it paralyses.

A fleet has its own graph in ``_distance_at_sea``, which reads oceans directly, so nothing
here takes anything away from the navy.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.objectives import _held_by as _objectives_held_by
from atlas.engine.objectives import _reachable_from
from atlas.engine.resolution import _adjacent_to_holdings, resolve_turn
from atlas.engine.resolution import _held_by as _resolution_held_by

WORLD = "sandbox"


@pytest.fixture
def taken_sea(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Aurelia has won the Narrow Sea and is at war with Karthos across it.

    Aurelia holds ``r_north`` and ``r_coast`` and controls ``r_sea``; Karthos holds
    ``r_south`` and ``r_west``. The only thing between the two powers is water, which is
    what makes the reachability tests mean something.
    """
    conn.execute("UPDATE region SET controller_nation_id = 'aurelia' WHERE id = 'r_sea'")
    for x, y in (("aurelia", "karthos"), ("karthos", "aurelia")):
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, x, y)
        )
    conn.execute(
        "INSERT INTO unit_template (world_id, kind, domain, base_strength, move_range)"
        " VALUES (?, 'infantry', 'land', 5, 1)", (WORLD,)
    )
    conn.execute(
        "INSERT INTO unit_template (world_id, kind, domain, base_strength, move_range)"
        " VALUES (?, 'transport', 'sea', 5, 2)", (WORLD,)
    )
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('u_gar', ?, 'karthos', 'r_south', 'infantry', 4.0, 50)", (WORLD,)
    )
    conn.commit()
    return conn


def _land_front(conn) -> None:
    """Give Aurelia ``r_east``, which borders Karthos by land.

    The march tests need somewhere ordinary to march *to*, or "the batch was refused" and
    "there was nowhere to go" look identical. The reachability tests above deliberately do
    not have it, because a land border would answer their question for them.
    """
    conn.execute("UPDATE region SET controller_nation_id = 'aurelia' WHERE id = 'r_east'")
    conn.commit()


def _where(conn, unit_id: str) -> str:
    return str(conn.execute(
        "SELECT region_id FROM unit WHERE id = ?", (unit_id,)
    ).fetchone()["region_id"])


# ------------------------------------------------------------------- what a holding is


def test_a_sea_is_not_among_the_regions_a_power_holds(taken_sea):
    assert "r_sea" not in _resolution_held_by(taken_sea, WORLD, "aurelia")
    assert "r_sea" not in _objectives_held_by(taken_sea, WORLD, "aurelia")


def test_a_sea_beside_your_coast_is_not_somewhere_an_army_can_be_sent(conn):
    """No sea control needed for this one: an ocean *beside* a coast you hold was
    reachable ground, so an open-sea `push` was a legal objective that then sat inert."""
    holdings = _objectives_held_by(conn, WORLD, "aurelia")
    assert "r_coast" in holdings, "this test is worthless without a coast to be beside"
    assert "r_sea" not in _reachable_from(conn, WORLD, holdings)


def test_the_far_shore_is_not_reachable_by_land_across_a_sea_you_hold(taken_sea):
    holdings = _objectives_held_by(taken_sea, WORLD, "aurelia")
    assert "r_south" not in _reachable_from(taken_sea, WORLD, holdings)


# ------------------------------------------------- a coast invadable without a transport


def test_holding_the_water_does_not_put_you_next_to_the_far_shore(taken_sea):
    assert not _adjacent_to_holdings(taken_sea, WORLD, "aurelia", "r_south")


def test_an_army_cannot_walk_across_a_sea_it_owns(taken_sea):
    """**The repro.** No transports anywhere, and an order to invade across open water."""
    report = resolve_turn(
        taken_sea, WORLD, turn=1, date="1936-02-01", seed=42, contests=[],
        actions=[{"type": "move_units", "from_region": "r_coast", "to_region": "r_south"}],
        player_nation="aurelia",
    )
    assert report.contests == 0
    assert any("cannot reach" in problem for problem in report.problems)


def test_with_shipping_the_same_order_is_an_invasion_again(taken_sea):
    """The control: this must refuse a *landing without ships*, not landings."""
    taken_sea.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('u_tr', ?, 'aurelia', 'r_coast', 'transport', 2.0, 60)", (WORLD,)
    )
    taken_sea.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('u_inf', ?, 'aurelia', 'r_coast', 'infantry', 9.0, 60)", (WORLD,)
    )
    taken_sea.commit()
    report = resolve_turn(
        taken_sea, WORLD, turn=1, date="1936-02-01", seed=42, contests=[],
        actions=[{"type": "move_units", "from_region": "r_coast", "to_region": "r_south"}],
        player_nation="aurelia",
    )
    assert report.contests == 1


# --------------------------------------------------------- the frozen land war


def test_winning_a_sea_does_not_freeze_the_march(taken_sea):
    """**The other repro.** The division at the coast used to be ordered into the water,
    UNIT-002 refused the whole batch, and the division *behind* it -- which had a perfectly
    ordinary province to march to -- never moved either."""
    _land_front(taken_sea)
    for unit_id, region in (("u_rear", "r_north"), ("u_shore", "r_coast")):
        taken_sea.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, 'aurelia', ?, 'infantry', 6.0, 60)", (unit_id, WORLD, region)
        )
    taken_sea.commit()

    report = resolve_turn(
        taken_sea, WORLD, turn=1, date="1936-02-01", seed=42, contests=[], actions=[],
        player_nation=None,
    )

    assert not [p for p in report.problems if "could not be moved" in p or "sea" in p], (
        report.problems
    )
    assert _where(taken_sea, "u_rear") != "r_north", "the rear formation never moved"
    assert _where(taken_sea, "u_shore") != "r_sea", "a division marched into the ocean"


def test_no_land_formation_ever_ends_a_turn_at_sea(taken_sea):
    """The invariant behind it, stated where a future march can trip over it."""
    _land_front(taken_sea)
    for unit_id, region in (("u_rear", "r_north"), ("u_shore", "r_coast")):
        taken_sea.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, 'aurelia', ?, 'infantry', 6.0, 60)", (unit_id, WORLD, region)
        )
    taken_sea.commit()
    for turn in range(1, 5):
        resolve_turn(
            taken_sea, WORLD, turn=turn, date="1936-02-01", seed=42, contests=[],
            actions=[], player_nation=None,
        )
    stranded = taken_sea.execute(
        """
        SELECT u.id FROM unit u
          JOIN region r ON r.id = u.region_id
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE COALESCE(t.domain, 'land') = 'land' AND r.kind = 'ocean'
        """
    ).fetchall()
    assert not [str(row["id"]) for row in stranded]
