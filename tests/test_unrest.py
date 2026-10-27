"""What it costs to hold ground that is not yours.

The peace layer made annexation possible; unrest makes it expensive. These pin the shape:
own ground is free, foreign core drags on stability, a stable state holds it more cheaply
than a shaky one, the drag is capped and rewindable.
"""

from __future__ import annotations

import sqlite3

from atlas.engine.unrest import (
    _MAX_DRAG,
    assess_partisans,
    assess_unrest,
    nation_unrest,
)


def _occupy(conn: sqlite3.Connection, holder: str, *regions: str) -> None:
    """Put ``holder``'s army on someone else's ground (occupation, home unchanged)."""
    for region in regions:
        conn.execute(
            "UPDATE region SET controller_nation_id = ? WHERE id = ?", (holder, region)
        )
    conn.commit()


def _stability(conn: sqlite3.Connection, nation: str) -> float:
    return conn.execute(
        "SELECT stability FROM nation WHERE id = ?", (nation,)
    ).fetchone()["stability"]


def test_a_nation_on_its_own_ground_pays_nothing(conn):
    """Every nation at the start of a world, since home is seeded to the owner."""
    assert nation_unrest(conn, "sandbox", "aurelia") == 0.0
    before = _stability(conn, "aurelia")
    assess_unrest(conn, "sandbox", turn=1, date="1936-02-01")
    assert _stability(conn, "aurelia") == before


def test_holding_foreign_core_costs_stability(conn):
    """Occupied enemy provinces drag the occupier's stability down."""
    _occupy(conn, "aurelia", "r_west", "r_east")  # karthos's and veyra's ground
    assert nation_unrest(conn, "sandbox", "aurelia") > 0.0

    before = _stability(conn, "aurelia")
    report = assess_unrest(conn, "sandbox", turn=1, date="1936-02-01")
    assert "Aurelia" in report.restive
    assert _stability(conn, "aurelia") < before


def test_more_foreign_ground_costs_more(conn):
    """The drag scales with how much foreign core is held."""
    conn.execute("UPDATE region SET development = 3 WHERE id IN ('r_west', 'r_east')")
    conn.commit()
    one = nation_unrest(conn, "sandbox", "aurelia")
    _occupy(conn, "aurelia", "r_west")
    held_one = nation_unrest(conn, "sandbox", "aurelia")
    _occupy(conn, "aurelia", "r_east")
    held_two = nation_unrest(conn, "sandbox", "aurelia")
    assert one == 0.0 < held_one < held_two


def _garrison(conn: sqlite3.Connection, holder: str, region: str, strength: float) -> None:
    """Station ``holder``'s troops on ``region`` (the guns-on-the-ground half of holding)."""
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
        " VALUES (?, 'sandbox', ?, ?, 'infantry', ?)",
        (f"u_{holder}_{region}", holder, region, strength),
    )
    conn.commit()


def test_troops_stationed_on_restive_ground_ease_its_unrest(conn):
    """An army parked on an occupied province holds it down -- less unrest than bare ground."""
    _occupy(conn, "aurelia", "r_west", "r_east")
    ungarrisoned = nation_unrest(conn, "sandbox", "aurelia")

    _garrison(conn, "aurelia", "r_west", 4.0)
    garrisoned = nation_unrest(conn, "sandbox", "aurelia")
    assert 0.0 < garrisoned < ungarrisoned, "troops on the ground ease the load, not erase it"


def test_a_garrison_only_eases_its_own_province(conn):
    """A division holds down the province it stands in, not the one next door."""
    _occupy(conn, "aurelia", "r_west", "r_east")
    _garrison(conn, "aurelia", "r_west", 20.0)  # far more than r_west's load
    # A huge garrison zeroes r_west but r_east, ungarrisoned, still drags.
    assert nation_unrest(conn, "sandbox", "aurelia") > 0.0
    before = _stability(conn, "aurelia")
    assess_unrest(conn, "sandbox", turn=1, date="1936-02-01")
    assert _stability(conn, "aurelia") < before


def test_a_garrison_never_makes_occupation_a_bonus(conn):
    """Overwhelming troops floor a province at zero unrest, never below."""
    _occupy(conn, "aurelia", "r_west")
    _garrison(conn, "aurelia", "r_west", 1000.0)
    assert nation_unrest(conn, "sandbox", "aurelia") == 0.0


def _controller(conn: sqlite3.Connection, region: str) -> str | None:
    return conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS h FROM region WHERE id = ?",
        (region,),
    ).fetchone()["h"]


def test_badly_held_ground_eventually_rises(conn):
    """A restive, ungarrisoned, developed province held by a shaky state flips back home."""
    conn.execute("UPDATE region SET development = 100 WHERE id = 'r_west'")
    conn.execute("UPDATE nation SET stability = 10 WHERE id = 'aurelia'")
    conn.commit()
    _occupy(conn, "aurelia", "r_west")  # karthos's ground, karthos still alive
    assert _controller(conn, "r_west") == "aurelia"

    rose = False
    for turn in range(1, 60):
        report = assess_partisans(conn, "sandbox", turn=turn, date="1936-02-01", seed=7)
        if report.flipped:
            rose = True
            break
    assert rose, "sustained bad occupation should eventually provoke an uprising"
    assert _controller(conn, "r_west") == "karthos", "control reverted to the home nation"


def test_a_well_garrisoned_province_does_not_rise(conn):
    """Troops on the ground floor the residual to zero, so partisans get no purchase."""
    conn.execute("UPDATE region SET development = 100 WHERE id = 'r_west'")
    conn.commit()
    _occupy(conn, "aurelia", "r_west")
    _garrison(conn, "aurelia", "r_west", 10000.0)  # overwhelming garrison

    for turn in range(1, 60):
        report = assess_partisans(conn, "sandbox", turn=turn, date="1936-02-01", seed=7)
        assert not report.flipped
    assert _controller(conn, "r_west") == "aurelia", "a held-down province stays held"


def test_a_conquered_home_nation_spawns_no_partisans(conn):
    """A people whose state was wholly conquered has no banner left to rally to."""
    conn.execute("UPDATE region SET development = 100 WHERE id = 'r_west'")
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = 'karthos'")
    conn.commit()
    _occupy(conn, "aurelia", "r_west")

    for turn in range(1, 40):
        report = assess_partisans(conn, "sandbox", turn=turn, date="1936-02-01", seed=7)
        assert not report.flipped
    assert _controller(conn, "r_west") == "aurelia"


def test_partisan_flip_rewinds(conn):
    from atlas.state.replay import rewind_to

    conn.execute("UPDATE region SET development = 100 WHERE id = 'r_west'")
    conn.execute("UPDATE nation SET stability = 10 WHERE id = 'aurelia'")
    conn.commit()
    _occupy(conn, "aurelia", "r_west")
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = 'sandbox'")
    conn.execute(
        "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
        " VALUES ('sandbox', 1, 'sealed', 'sealed', '1936-01-01')"
    )
    conn.commit()

    flipped_turn = None
    for turn in range(2, 60):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = 'sandbox'", (turn,))
        conn.execute(
            "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
            " VALUES ('sandbox', ?, 'sealed', 'sealed', '1936-01-01')",
            (turn,),
        )
        if assess_partisans(conn, "sandbox", turn=turn, date="1936-02-01", seed=7).flipped:
            flipped_turn = turn
            break
    conn.commit()
    assert flipped_turn is not None
    assert _controller(conn, "r_west") == "karthos"

    # Rewind to before the uprising: the occupier holds the province again.
    assert rewind_to(conn, "sandbox", flipped_turn - 1).ok
    assert _controller(conn, "r_west") == "aurelia"


def test_a_stable_state_holds_ground_more_cheaply(conn):
    """Stability mitigates unrest -- but never all of it."""
    _occupy(conn, "aurelia", "r_west", "r_east")

    conn.execute("UPDATE nation SET stability = 30 WHERE id = 'aurelia'")
    conn.commit()
    before = _stability(conn, "aurelia")
    assess_unrest(conn, "sandbox", turn=1, date="1936-02-01")
    shaky_drag = before - _stability(conn, "aurelia")

    conn.execute("UPDATE nation SET stability = 90 WHERE id = 'aurelia'")
    conn.commit()
    before = _stability(conn, "aurelia")
    assess_unrest(conn, "sandbox", turn=2, date="1936-02-01")
    stable_drag = before - _stability(conn, "aurelia")

    assert 0.0 < stable_drag < shaky_drag, "a stable state pays less, but still pays"


def test_the_drag_is_capped(conn):
    """Overstretch is a bleed a player can react to, not a one-turn cliff."""
    # An enormous, highly developed occupation at rock-bottom stability.
    conn.execute(
        "UPDATE region SET controller_nation_id = 'aurelia', development = 100 "
        "WHERE id IN ('r_west', 'r_east', 'r_south')"
    )
    conn.execute("UPDATE nation SET stability = 5 WHERE id = 'aurelia'")
    conn.commit()
    before = _stability(conn, "aurelia")
    assess_unrest(conn, "sandbox", turn=1, date="1936-02-01")
    assert before - _stability(conn, "aurelia") <= _MAX_DRAG + 0.001


def test_unrest_rewinds(conn):
    from atlas.state.replay import rewind_to

    _occupy(conn, "aurelia", "r_west", "r_east")
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = 'sandbox'")
    conn.execute(
        "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
        " VALUES ('sandbox', 1, 'sealed', 'sealed', '1936-01-01')"
    )
    conn.commit()
    before = _stability(conn, "aurelia")

    for turn in range(2, 6):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = 'sandbox'", (turn,))
        conn.execute(
            "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
            " VALUES ('sandbox', ?, 'sealed', 'sealed', '1936-01-01')",
            (turn,),
        )
        assess_unrest(conn, "sandbox", turn=turn, date="1936-02-01")
    conn.commit()
    assert _stability(conn, "aurelia") < before

    assert rewind_to(conn, "sandbox", 1).ok
    assert _stability(conn, "aurelia") == before


def test_an_occupier_at_the_end_of_its_rope_does_not_cost_every_other_occupier(conn):
    """The same bug class as the arrears charge, in the same shape.

    ECON-003 names no diff, so it condemns the whole batch -- and this batch charges every
    occupier in the world at once. An occupier down to 0.2 taking a 0.5 drag therefore
    cost every *other* occupier its drag too, and the only thing that happened was a
    problem line.
    """
    conn.execute(
        "UPDATE region SET controller_nation_id = 'karthos'"
        " WHERE id IN ('r_north', 'r_coast')")
    conn.execute("UPDATE region SET controller_nation_id = 'veyra' WHERE id = 'r_west'")
    conn.execute("UPDATE nation SET stability = 0.2 WHERE id = 'karthos'")
    conn.execute("UPDATE nation SET stability = 60 WHERE id = 'veyra'")
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = 'sandbox'")
    conn.commit()

    report = assess_unrest(conn, "sandbox", turn=1, date="1897-04-01")

    assert not report.problems, report.problems
    after = dict(conn.execute("SELECT id, stability FROM nation").fetchall())
    assert after["karthos"] == 0.0, "the drag took what there was and no more"
    assert after["veyra"] < 60, "the other occupier was still charged"
    assert "Karthos" in report.restive and "Veyra" in report.restive
