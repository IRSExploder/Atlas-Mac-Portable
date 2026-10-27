"""Where an unattended power lays its next public work.

The chooser was *fewest works under way, then alphabetically*, which was exactly right
while every province was worth the same and stopped being right the moment development
varied. What is pinned here:

* a work goes on ground the nation **owns** before ground it merely holds, because held
  ground yields 40% of its development -- and that discount lands twice, once in revenue
  and once in the ceiling the economy grows toward;
* it goes on the nation's **own core** before somebody else's, because the unrest layer
  erodes the development of restive ground and a work laid there fills a draining bucket;
* the works still **spread out**, which is what the old chooser was for;
* and the tiebreak is legible rather than alphabetical.
"""

from __future__ import annotations

from atlas.engine.production import _best_investment_region
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty

WORLD = "w_invest"


def _world(regions):
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Invest", start_date="2026-01-01", rng_seed=7,
        difficulty=Difficulty.NORMAL,
        nations=(
            NationSeed(id="a", name="Ayland", colour="#111", capital_region_id="r_home",
                       economy=50.0, industry=40.0, treasury=500.0, manpower=200.0),
            NationSeed(id="b", name="Beeland", colour="#222", economy=50.0),
        ),
        regions=tuple(regions),
    ))


def _region(region_id, owner, development=1.0):
    return RegionSeed(id=region_id, name=region_id, geo_key=f"g:{region_id}",
                      owner_nation_id=owner, development=development)


def _building(conn, region_id, nation_id="a") -> None:
    """A work already under way in this region."""
    conn.execute(
        "INSERT INTO construction (id, world_id, nation_id, region_id, project_kind,"
        "                          kind, turns_remaining, days_remaining, started_turn)"
        " VALUES (?, ?, ?, ?, 'project', 'workshops', 3, 90, 0)",
        (f"c:{region_id}", WORLD, nation_id, region_id),
    )
    conn.commit()


def _hold(conn, region_id, *, controller="a", home="b", owner=None):
    """Make a region one that `a` controls without owning or coring it."""
    conn.execute(
        "UPDATE region SET controller_nation_id = ?, home_nation_id = ?,"
        " owner_nation_id = COALESCE(?, owner_nation_id) WHERE id = ?",
        (controller, home, owner, region_id),
    )
    conn.commit()


def test_owned_ground_beats_held_ground():
    """**The 2.5x difference the old chooser could not see.** A steelworks on conquered
    ground earns two-fifths of the same steelworks at home, for the same money and the
    same years -- and an occupier's provinces sort early in a good many alphabets."""
    conn = _world([
        _region("r_aaa_taken", "b"),
        _region("r_home", "a"),
    ])
    _hold(conn, "r_aaa_taken")
    assert _best_investment_region(conn, WORLD, "a") == "r_home"


def test_own_core_beats_ground_owned_but_not_cored():
    """Annexed at a peace: the nation owns it, but it is somebody else's core, so the
    unrest layer is eroding its development while a work adds to it."""
    conn = _world([
        _region("r_aaa_annexed", "a"),
        _region("r_home", "a"),
    ])
    conn.execute("UPDATE region SET home_nation_id = 'b' WHERE id = 'r_aaa_annexed'")
    conn.commit()
    assert _best_investment_region(conn, WORLD, "a") == "r_home"


def test_the_works_still_spread_out():
    """What the old chooser existed for, and it is kept: among equally good ground, the
    one with the fewest works under way."""
    conn = _world([
        _region("r_one", "a", development=5.0),
        _region("r_two", "a", development=5.0),
    ])
    _building(conn, "r_one")
    assert _best_investment_region(conn, WORLD, "a") == "r_two"


def test_the_tiebreak_is_where_the_country_actually_is():
    """Alphabetical order is not a decision anybody can read. Among ground that is equally
    good and equally idle, the industrial heart is where an industrial programme belongs.

    Honestly a tiebreak: project effects are flat, so the marginal revenue is the same in
    a rich province and a poor one. What it buys is a master AI whose choices a player can
    follow, which is most of how a player learns to trust it.
    """
    conn = _world([
        _region("r_aaa_backwater", "a", development=0.3),
        _region("r_zzz_heartland", "a", development=9.0),
    ])
    assert _best_investment_region(conn, WORLD, "a") == "r_zzz_heartland"


def test_a_nation_holding_nothing_gets_nowhere_to_build():
    conn = _world([_region("r_home", "b")])
    assert _best_investment_region(conn, WORLD, "a") is None


def test_held_ground_is_still_offered_when_it_is_all_there_is():
    """A power driven out of its own country should keep building, not stop. The order is
    a preference, not a filter."""
    conn = _world([_region("r_taken", "b")])
    _hold(conn, "r_taken")
    assert _best_investment_region(conn, WORLD, "a") == "r_taken"


def test_ocean_is_never_a_building_site():
    conn = _world([_region("r_sea", "a"), _region("r_land", "a")])
    conn.execute("UPDATE region SET kind = 'ocean' WHERE id = 'r_sea'")
    conn.commit()
    assert _best_investment_region(conn, WORLD, "a") == "r_land"


def test_the_whole_order_together():
    """Own core and idle beats own core and busy, beats owned-not-cored, beats held."""
    conn = _world([
        _region("r_a_held", "b", development=9.0),
        _region("r_b_annexed", "a", development=9.0),
        _region("r_c_core_busy", "a", development=9.0),
        _region("r_d_core_idle", "a", development=1.0),
    ])
    _hold(conn, "r_a_held")
    conn.execute("UPDATE region SET home_nation_id = 'b' WHERE id = 'r_b_annexed'")
    conn.commit()
    _building(conn, "r_c_core_busy")
    assert _best_investment_region(conn, WORLD, "a") == "r_d_core_idle"
