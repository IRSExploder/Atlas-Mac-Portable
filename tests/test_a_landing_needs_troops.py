"""A navy landed itself on the beach, and a border province landed on ground it bordered.

:func:`atlas.sim.resolve._from_the_sea` sums what a power could put ashore across one sea.
It summed **every unit in the embarkation ports whatever its domain** -- so a battle fleet
in harbour went ashore, an air wing waded in beside it, and a transport counted as its own
cargo. A power with a navy and no army could take a defended coast.

The second half is quieter and biases every landing upward. A province touching both the
objective and the water between them was counted **twice**: once by ``_strength``'s
``nearby`` term for marching over the border, and again here for landing on the same ground
it was already standing next to. It cannot do both, and the border is the one it would use.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.sim.resolve import _ADJACENT_WEIGHT, _from_the_sea, _strength

WORLD = "sandbox"


@pytest.fixture
def shore(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Karthos on the far shore of one sea, with two Aurelian ports on the near one.

    ``r_coast`` touches the objective *and* the sea, so it is the double-count case.
    ``r_port`` touches only the sea, so it is the genuine landing case -- and having both
    is what makes the two halves distinguishable.
    """
    conn.execute(
        "INSERT INTO region (id, world_id, name, geo_key, kind)"
        " VALUES ('r_open', ?, 'Open Sea', 'geo:open', 'ocean')", (WORLD,)
    )
    conn.execute(
        "INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id)"
        " VALUES ('r_port', ?, 'Far Port', 'geo:port', 'coastal', 'aurelia')", (WORLD,)
    )
    conn.execute("UPDATE region SET kind = 'coastal' WHERE id IN ('r_coast', 'r_south')")
    for a, b, crossing in (
        ("r_coast", "r_open", "sea"), ("r_south", "r_open", "sea"),
        ("r_port", "r_open", "sea"), ("r_coast", "r_south", "land"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO region_adjacency (world_id, region_a, region_b, crossing)"
            " VALUES (?, ?, ?, ?)", (WORLD, *sorted((a, b)), crossing)
        )
    conn.executemany(
        "INSERT INTO unit_template (world_id, kind, domain, base_strength, move_range)"
        " VALUES (?, ?, ?, 10, 2)",
        [(WORLD, "battleship", "sea"), (WORLD, "transport", "sea"),
         (WORLD, "bomber", "air"), (WORLD, "infantry", "land")],
    )
    for x, y in (("aurelia", "karthos"), ("karthos", "aurelia")):
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, x, y)
        )
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('u_tr', ?, 'aurelia', 'r_port', 'transport', 2.0, 50)", (WORLD,)
    )
    conn.commit()
    return conn


def _put(conn, unit_id: str, kind: str, region: str, strength: float = 10.0) -> None:
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES (?, ?, 'aurelia', ?, ?, ?, 50)",
        (unit_id, WORLD, region, kind, strength),
    )
    conn.commit()


# ------------------------------------------------------------------------ trooplessness


def test_a_fleet_alone_puts_nothing_ashore(shore):
    """**The repro.** A battle fleet and its transports, and not one soldier."""
    _put(shore, "u_bb", "battleship", "r_port", 40.0)
    assert _from_the_sea(shore, "aurelia", "r_south") == 0.0


def test_a_transport_is_not_its_own_cargo(shore):
    """The transport in the fixture is already there; nothing else is."""
    assert _from_the_sea(shore, "aurelia", "r_south") == 0.0


def test_an_air_wing_does_not_wade_ashore(shore):
    """Excluded for the reason every gatherer excludes it: what an air force contributes
    is the sky, which is fought for separately and arrives as its own term."""
    _put(shore, "u_air", "bomber", "r_port", 40.0)
    assert _from_the_sea(shore, "aurelia", "r_south") == 0.0


def test_troops_in_the_same_port_do_go_ashore(shore):
    """The control. Without it every test above passes on a landing that never works."""
    _put(shore, "u_inf", "infantry", "r_port", 10.0)
    assert _from_the_sea(shore, "aurelia", "r_south") > 0.0


def test_the_fleet_does_not_add_to_the_troops_it_carries(shore):
    _put(shore, "u_inf", "infantry", "r_port", 4.0)
    troops_only = _from_the_sea(shore, "aurelia", "r_south")
    _put(shore, "u_bb", "battleship", "r_port", 40.0)
    assert _from_the_sea(shore, "aurelia", "r_south") == pytest.approx(troops_only)


# -------------------------------------------------------------------------- double count


def test_an_army_already_on_the_border_does_not_also_land(shore):
    """``r_coast`` borders the objective. Its divisions march; they do not embark."""
    _put(shore, "u_border", "infantry", "r_coast", 10.0)
    assert _from_the_sea(shore, "aurelia", "r_south") == 0.0


def test_the_border_army_is_still_counted_once(shore):
    """Removing it from the landing must not remove it from the battle."""
    before = _strength(shore, "aurelia", "r_south")
    _put(shore, "u_border", "infantry", "r_coast", 10.0)
    after = _strength(shore, "aurelia", "r_south")
    # Morale 50 makes the morale factor exactly 1, so the adjacency weight is the whole
    # of what a bordering division is worth.
    assert after - before == pytest.approx(10.0 * _ADJACENT_WEIGHT)


def test_a_port_that_does_not_border_the_objective_still_lands(shore):
    """The exclusion is about double counting, not about ports."""
    _put(shore, "u_far", "infantry", "r_port", 10.0)
    assert _from_the_sea(shore, "aurelia", "r_south") > 0.0
