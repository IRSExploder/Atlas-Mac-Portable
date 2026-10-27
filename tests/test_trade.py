"""Strategic goods, and the three ways somebody stops them arriving.

Everything a country needed was one abstract number. What is pinned here, in order of how
much damage getting it wrong would do:

* **an old save is inert** -- no resource rows means no shortfall, so a world written
  before schema 14 builds at exactly the rate it always did;
* **a bare world is self-sufficient**, and the constant that makes it so cannot drift from
  the one that decides demand;
* the three levers cut trade, and each is an existing subsystem being asked a question;
* a shortage stops a country *building*, not earning -- which is the whole reason this
  lands on `industrial_capacity` rather than on `economy`;
* stockpiles make a blockade a campaign rather than a switch, and it all rewinds.
"""

from __future__ import annotations

import pytest

from atlas.engine.production import industrial_capacity
from atlas.engine.trade import (
    _STARVED_CAPACITY,
    GOODS,
    assess_trade,
    demand_of,
    is_unreachable,
    route_is_open,
    shortfall_of,
    supply_ratio,
)
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty

WORLD = "w_trade"


def _world():
    """Two countries either side of a sea, so a lane exists to be cut."""
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Trade", start_date="2026-01-01", rng_seed=11,
        difficulty=Difficulty.NORMAL,
        nations=(
            NationSeed(id="a", name="Ayland", colour="#111", capital_region_id="r_a",
                       economy=50.0, industry=30.0, military_strength=20.0),
            NationSeed(id="b", name="Beeland", colour="#222", capital_region_id="r_b",
                       economy=50.0, industry=30.0, military_strength=20.0),
        ),
        regions=(
            RegionSeed(id="r_a", name="Ayland", geo_key="g:a", owner_nation_id="a",
                       development=5.0),
            RegionSeed(id="r_b", name="Beeland", geo_key="g:b", owner_nation_id="b",
                       development=5.0),
            RegionSeed(id="s_mid", name="The Middle Sea", geo_key="g:s", kind="ocean"),
        ),
        adjacency=(("r_a", "s_mid", "sea"), ("s_mid", "r_b", "sea")),
    ))


def _route(conn, seller="a", buyer="b", good="oil", volume=None):
    """A standing agreement, by default large enough to actually cover the buyer's need.

    The volume is derived rather than written down: a first draft used a flat 3.0 against a
    demand of 3.3, so the buyer was permanently a tenth short and a test that meant to
    watch a blockade bite was watching a rounding error instead.
    """
    if volume is None:
        volume = demand_of(conn, buyer) * 1.5
    conn.execute(
        "INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
        "                         opened_turn)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (f"tr:{good}:{seller}:{buyer}", WORLD, seller, buyer, good, volume),
    )
    conn.commit()


def _starve(conn, nation_id="b", good="oil"):
    """Leave a nation producing none of a good and holding none of it."""
    conn.execute(
        "UPDATE resource SET produced = 0, stockpile = 0"
        " WHERE world_id = ? AND nation_id = ? AND good = ?", (WORLD, nation_id, good))
    conn.commit()


# ------------------------------------------------------------------- the safety net


def test_a_world_written_before_this_layer_is_untouched():
    """No resource rows means no shortfall, exactly -- not approximately -- or every old
    save silently rebalances the moment it is opened."""
    conn = _world()
    conn.execute("DELETE FROM resource")
    conn.commit()
    assert shortfall_of(conn, WORLD, "a") == 1.0
    assert supply_ratio(conn, WORLD, "a", "oil") == 1.0
    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01")
    assert report.assessed == 0


def test_a_connection_with_no_resource_table_does_not_raise():
    conn = _world()
    conn.execute("DROP TABLE trade_route")
    conn.execute("DROP TABLE resource")
    conn.commit()
    assert shortfall_of(conn, WORLD, "a") == 1.0
    assert assess_trade(conn, WORLD, turn=1, date="2026-02-01").assessed == 0


def test_a_world_with_no_scenario_is_self_sufficient():
    """**The guard against two constants drifting.** `create_world` seeds production at
    what a nation's own industry burns, and the coefficient it uses has to equal the one
    `demand_of` uses -- otherwise a bare world starves or floods for reasons no scenario
    author chose. A flat default was tried first and made a country that doubled its
    industry build *fewer* things.
    """
    conn = _world()
    for nation_id in ("a", "b"):
        produced = conn.execute(
            "SELECT produced FROM resource WHERE world_id = ? AND nation_id = ?"
            "   AND good = 'oil'", (WORLD, nation_id)).fetchone()[0]
        assert produced == pytest.approx(demand_of(conn, nation_id), rel=0.02)
        assert shortfall_of(conn, WORLD, nation_id) == 1.0


def test_growing_industry_does_not_starve_itself_in_a_bare_world():
    conn = _world()
    before = industrial_capacity(conn, "a")
    conn.execute("UPDATE nation SET industry = 90 WHERE id = 'a'")
    conn.execute("UPDATE resource SET produced = 9.9, stockpile = 49.5"
                 " WHERE world_id = ? AND nation_id = 'a'", (WORLD,))
    conn.commit()
    assert industrial_capacity(conn, "a") > before


# ------------------------------------------------------------------- what it does


def test_a_shortage_slows_a_country_building_and_not_earning():
    """The whole reason this lands on building rather than on `economy`: a blockaded
    country still collects its taxes and its yards work slowly. Since 2026-09-18 that is
    the pace alone (`shortfall_of`, applied in `advance_construction`); the count of yards
    is untouched -- see `production.industrial_capacity` and
    `test_a_blockade_reaches_the_yards.py` for the decision."""
    conn = _world()
    economy_before = conn.execute(
        "SELECT economy FROM nation WHERE id = 'b'").fetchone()[0]
    slots_before = industrial_capacity(conn, "b")

    _starve(conn)
    assert shortfall_of(conn, WORLD, "b") < 1.0
    assert industrial_capacity(conn, "b") == slots_before
    assert conn.execute(
        "SELECT economy FROM nation WHERE id = 'b'").fetchone()[0] == economy_before


def test_the_worst_good_decides_and_not_the_average():
    """An aircraft factory with steel and no fuel is a factory that is not running."""
    conn = _world()
    conn.execute("UPDATE resource SET stockpile = 500 WHERE world_id = ? AND"
                 " nation_id = 'b' AND good IN ('steel','rare')", (WORLD,))
    _starve(conn)
    assert shortfall_of(conn, WORLD, "b") == pytest.approx(_STARVED_CAPACITY)


def test_a_country_with_nothing_can_still_build_something():
    """A production system one severed route can bring to a standstill is a system where
    one lever decides the game."""
    conn = _world()
    for good in GOODS:
        _starve(conn, good=good)
    assert shortfall_of(conn, WORLD, "b") == pytest.approx(_STARVED_CAPACITY)
    assert industrial_capacity(conn, "b") >= 1


# ------------------------------------------------------------------- the three levers


def test_war_stops_trade():
    conn = _world()
    assert route_is_open(conn, WORLD, "a", "b")[0]
    conn.execute("UPDATE relation SET at_war = 1")
    conn.commit()
    ok, why = route_is_open(conn, WORLD, "a", "b")
    assert not ok and why == "at war"


def test_a_sanction_stops_trade_with_the_body_that_imposed_it():
    """The organisation layer already writes these rows; nothing here was added for it."""
    conn = _world()
    conn.execute(
        "INSERT INTO nation (id, world_id, name, colour) VALUES ('un', ?, 'Council', '#888')",
        (WORLD,))
    conn.execute(
        "INSERT INTO organisation (nation_id, world_id, kind, charter_text, authority)"
        " VALUES ('un', ?, 'universal', '', 50)", (WORLD,))
    conn.execute(
        "INSERT INTO organisation_member (organisation_id, nation_id, is_permanent,"
        "                                 joined_turn) VALUES ('un', 'a', 1, 0)")
    conn.execute(
        "INSERT INTO sanction (id, world_id, organisation_id, target_nation_id, severity,"
        "                      opened_turn, until_turn)"
        " VALUES ('s1', ?, 'un', 'b', 1.0, 0, 99)", (WORLD,))
    conn.commit()
    ok, why = route_is_open(conn, WORLD, "a", "b")
    assert not ok and why == "under sanction"


def test_a_blockade_stops_trade_and_nothing_was_written_to_make_it():
    """The lane crosses water, and `naval._sea_is_ours` -- the same function that decides
    whether a landing happens and whether an army eats -- decides whether it gets through.
    """
    conn = _world()
    conn.execute(
        "INSERT INTO nation (id, world_id, name, colour, military_strength)"
        " VALUES ('c', ?, 'Ceeland', '#333', 90)", (WORLD,))
    conn.execute(
        "INSERT INTO unit_template (world_id, kind, domain, cost_treasury, cost_manpower)"
        " VALUES (?, 'destroyer', 'sea', 1, 1)", (WORLD,))
    conn.execute(
        "INSERT INTO relation (world_id, nation_a, nation_b, at_war) VALUES (?,'a','c',1)",
        (WORLD,))
    conn.commit()
    assert route_is_open(conn, WORLD, "a", "b")[0], "the lane was shut before the fleet"

    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
        " VALUES ('blk', ?, 'c', 's_mid', 'destroyer', 500)", (WORLD,))
    conn.commit()
    ok, why = route_is_open(conn, WORLD, "a", "b")
    assert not ok and why == "the lane is cut"


def test_a_country_the_map_cannot_reach_is_not_treated_as_blockaded():
    """35 land regions on the 2026 map carry no adjacency at all. Nobody blockaded Aruba,
    and a nation nothing can reach also cannot be cut off by anyone."""
    conn = _world()
    conn.execute("DELETE FROM region_adjacency WHERE world_id = ?", (WORLD,))
    conn.commit()
    assert is_unreachable(conn, WORLD, "b")
    _starve(conn)
    assert shortfall_of(conn, WORLD, "b") == 1.0


# ------------------------------------------------------------------ the turn's work


def test_goods_are_dug_shipped_and_burned():
    conn = _world()
    _starve(conn)
    _route(conn, volume=demand_of(conn, "b") * 2)
    conn.execute("UPDATE resource SET stockpile = 500 WHERE world_id = ? AND"
                 " nation_id = 'a' AND good = 'oil'", (WORLD,))
    conn.commit()

    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01")
    assert not report.problems, report.problems
    assert conn.execute(
        "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = 'b'"
        "   AND good = 'oil'", (WORLD,)).fetchone()[0] > 0


def test_a_blockade_bites_over_months_rather_than_at_once():
    """Stockpiles are the reason a blockade is a campaign and not a switch."""
    conn = _world()
    _route(conn)
    _starve(conn)
    conn.execute("UPDATE resource SET stockpile = 500 WHERE world_id = ? AND"
                 " nation_id = 'a' AND good = 'oil'", (WORLD,))
    conn.commit()
    for turn in range(1, 4):
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01")
    healthy = shortfall_of(conn, WORLD, "b")
    assert healthy > 0.9, "the route never delivered"

    conn.execute("UPDATE relation SET at_war = 1")
    conn.commit()
    ratios = []
    for turn in range(4, 12):
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01")
        ratios.append(shortfall_of(conn, WORLD, "b"))
    assert ratios[0] > ratios[-1], "the blockade did not bite at all"
    assert ratios[0] > _STARVED_CAPACITY, "it bit instantly instead of over months"
    assert ratios[-1] == pytest.approx(_STARVED_CAPACITY, abs=0.05)


def test_a_seller_cannot_ship_what_it_does_not_have():
    conn = _world()
    _starve(conn, "a")
    _starve(conn, "b")
    _route(conn, volume=50.0)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01")
    assert conn.execute(
        "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = 'b'"
        "   AND good = 'oil'", (WORLD,)).fetchone()[0] == 0.0


def test_a_stockpile_does_not_grow_forever():
    """Without a ceiling a peacetime surplus makes the blockade lever meaningless by about
    year five."""
    conn = _world()
    conn.execute("UPDATE resource SET produced = 50 WHERE world_id = ?", (WORLD,))
    conn.commit()
    for turn in range(1, 60):
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01", days=365)
    stored = conn.execute(
        "SELECT MAX(stockpile) FROM resource WHERE world_id = ?", (WORLD,)).fetchone()[0]
    assert stored <= demand_of(conn, "a") * 12 + 0.5, stored


def test_trade_rewinds():
    from atlas.state.bootstrap import fingerprint
    from atlas.state.replay import rewind_to

    conn = _world()
    _route(conn)
    for number, date in ((1, "2026-02-01"), (2, "2026-03-01")):
        conn.execute(
            "INSERT INTO turn (world_id, number, phase, status, start_date)"
            " VALUES (?, ?, 'sealed', 'sealed', ?)", (WORLD, number, date))
    conn.execute(
        "UPDATE world SET turn_number = 1, world_date = '2026-02-01' WHERE id = ?",
        (WORLD,))
    conn.commit()
    before = fingerprint(conn, WORLD)

    conn.execute(
        "UPDATE world SET turn_number = 2, world_date = '2026-03-01' WHERE id = ?",
        (WORLD,))
    conn.commit()
    assess_trade(conn, WORLD, turn=2, date="2026-03-01")
    assert fingerprint(conn, WORLD) != before

    assert rewind_to(conn, WORLD, 1).ok
    assert fingerprint(conn, WORLD) == before


# ------------------------------------------------------------------ the seed tables


def test_the_endowment_tables_name_only_nations_that_era_has():
    """The same per-era guard the leader tables carry, for the same reason."""
    from atlas.map.pack import PackSet
    from atlas.scenarios.presets import load_preset
    from atlas.scenarios.registry import preset_seed
    from atlas.scenarios.resources import (
        ENDOWMENT_1936,
        ENDOWMENT_1972,
        ENDOWMENT_2026,
    )
    from atlas.scenarios.territory import pack_paths

    for preset_id, label, table in (
        ("gathering-storm", "ENDOWMENT_1936", ENDOWMENT_1936),
        ("cold-war", "ENDOWMENT_1972", ENDOWMENT_1972),
        ("modern-day", "ENDOWMENT_2026", ENDOWMENT_2026),
    ):
        preset = load_preset(preset_id)
        paths = pack_paths(preset.id, preset.map_source)
        if not all(path.exists() for path in paths):
            pytest.skip("map packs not baked; run `atlas map bake`")
        with PackSet.open(paths) as pack:
            seed = preset_seed(preset, pack, world_id="fp", player=None)
        known = {
            n.id[2:] if n.id.startswith("m_") else n.id for n in seed.nations
        }
        unknown = sorted(set(table) - known)
        assert not unknown, f"{label} names nations {preset_id} lacks: {unknown}"


def test_every_endowment_names_only_goods_the_schema_allows():
    from atlas.scenarios.resources import (
        ENDOWMENT_1936,
        ENDOWMENT_1972,
        ENDOWMENT_2026,
    )

    for table in (ENDOWMENT_1936, ENDOWMENT_1972, ENDOWMENT_2026):
        for key, goods in table.items():
            assert set(goods) == set(GOODS), f"{key} has {sorted(goods)}"
