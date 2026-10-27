"""An ally in the sky cancelled the air war -- not one region of it, all of it.

``resolve_air_war`` collected, per region, every power that could project air over it from
some **warring** pair, then took the two strongest and fought them. If those two happened to
be allies it skipped the region, correctly, for not being a war -- and never looked at the
third power, who *was* at war with both and now flew unopposed.

That is bad enough per region. On a map where two allies are the two strongest air forces
almost everywhere -- which is what an alliance usually means -- it is the whole campaign:
the fixture below fights over six regions and produced **zero** battles.

And the region did not lapse either. The lapse was told to spare every region in the
*candidate* list rather than the ones actually fought over, so an air superiority won turns
earlier sat frozen for as long as an ally kept flying near it.

The pair is now the strongest air force present and the strongest one it is at war with, and
the lapse is told what was actually fought.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.air import control_of, projection, resolve_air_war

WORLD = "sandbox"
_REGIONS = ("r_north", "r_coast", "r_east", "r_south", "r_west", "r_sea")


@pytest.fixture
def coalition(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Aurelia and Veyra, allied, both at war with Karthos, all three in the air.

    The allies are the two strongest over every region on the map and Karthos is much the
    weakest, which is the shape that made the old pairing skip everything.
    """
    for kind, domain, reach, strength in (("infantry", "land", 1, 4.0),
                                          ("air_wing", "air", 3, 5.0)):
        conn.execute(
            "INSERT OR REPLACE INTO unit_template (world_id, kind, domain, cost_treasury,"
            " cost_manpower, base_strength, move_range, build_days)"
            " VALUES (?, ?, ?, 10, 10, ?, ?, 60)", (WORLD, kind, domain, strength, reach)
        )
    for a, b in (("aurelia", "karthos"), ("karthos", "aurelia"),
                 ("veyra", "karthos"), ("karthos", "veyra")):
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, a, b)
        )
    for uid, nation, region, strength in (("a1", "aurelia", "r_north", 12.0),
                                          ("v1", "veyra", "r_east", 12.0),
                                          ("k1", "karthos", "r_south", 2.0)):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, ?, ?, 'air_wing', ?, 50)", (uid, WORLD, nation, region, strength)
        )
    conn.commit()
    return conn


def test_the_allies_really_are_the_two_strongest_everywhere(coalition):
    """The premise. Without it the tests below could pass on a map where the old pairing
    happened to pick a belligerent anyway."""
    reach = {n: projection(coalition, WORLD, n) for n in ("aurelia", "veyra", "karthos")}
    for region in _REGIONS:
        ranked = sorted(reach, key=lambda n: (-reach[n].get(region, 0.0), n))
        assert ranked[:2] == sorted(("aurelia", "veyra")) or set(ranked[:2]) == {
            "aurelia", "veyra"
        }, f"{region}: {ranked}"


def test_the_air_war_is_fought_at_all(coalition):
    """**The repro.** Six contested skies, and an ally standing in each of them."""
    outcome = resolve_air_war(coalition, WORLD, turn=1, date="d", seed=4)
    assert outcome.battles == len(_REGIONS)


def test_the_unopposed_third_power_is_actually_opposed(coalition):
    """Karthos was at war with both allies and flew over all six regions untouched."""
    resolve_air_war(coalition, WORLD, turn=1, date="d", seed=4)
    held = {region: control_of(coalition, region)[0] for region in _REGIONS}
    assert set(held.values()) <= {"aurelia", "veyra"}, held
    assert all(holder is not None for holder in held.values()), held


def test_the_belligerent_pair_still_bleeds_both_sides(coalition):
    """The pair chosen has to be a real fight: both of its sides lose aircraft."""
    before = dict(coalition.execute("SELECT id, strength FROM unit").fetchall())
    resolve_air_war(coalition, WORLD, turn=1, date="d", seed=4)
    after = dict(coalition.execute("SELECT id, strength FROM unit").fetchall())
    assert after["k1"] < before["k1"], "the power everyone was at war with never bled"
    assert any(after[uid] < before[uid] for uid in ("a1", "v1"))


def test_a_sky_the_ally_case_used_to_freeze_is_taken_off_its_holder(coalition):
    """The other half, as it shows up in play.

    Karthos held this sky from an earlier campaign. The old pairing neither contested it
    -- an ally was standing between the two belligerents -- nor let it lapse, because the
    lapse was spared every *candidate* region rather than the ones actually fought over.
    So Karthos kept a sky in which nothing was happening, indefinitely.
    """
    coalition.execute(
        "INSERT INTO air_control (world_id, region_id, holder_nation_id, margin, last_turn)"
        " VALUES (?, 'r_east', 'karthos', 1.0, 0)", (WORLD,)
    )
    coalition.commit()
    assert control_of(coalition, "r_east") == ("karthos", 1.0)

    resolve_air_war(coalition, WORLD, turn=1, date="d", seed=4)
    assert control_of(coalition, "r_east")[0] != "karthos"


def test_a_candidate_region_that_produces_no_battle_still_lapses(coalition, monkeypatch):
    """The guard behind the fix, tested at the seam rather than through play.

    **This branch is not reachable in an ordinary game today**, and saying so is the point
    of the docstring: `projection` never records a region at zero strength, so two powers
    that both appear over one region always have something to fight with, and after the
    pairing fix a candidate region always finds a belligerent. The guard exists because
    the lapse must be told *what happened*, not *what might have*, and that stays true the
    moment either of those conditions changes.
    """
    from atlas.engine import air as air_module

    coalition.execute(
        "INSERT INTO air_control (world_id, region_id, holder_nation_id, margin, last_turn)"
        " VALUES (?, 'r_east', 'karthos', 1.0, 0)", (WORLD,)
    )
    coalition.commit()
    monkeypatch.setattr(air_module, "_fight_over", lambda *a, **k: None)

    for turn in range(1, 8):
        resolve_air_war(coalition, WORLD, turn=turn, date="d", seed=4)
    assert control_of(coalition, "r_east") == (None, 0.0), "the sky was never given up"


def test_two_powers_at_war_are_unaffected(coalition):
    """The control: the ordinary two-power case must fight exactly as it did."""
    coalition.execute("DELETE FROM unit WHERE id = 'v1'")
    coalition.commit()
    outcome = resolve_air_war(coalition, WORLD, turn=1, date="d", seed=4)
    assert outcome.battles > 0
    assert control_of(coalition, "r_north")[0] == "aurelia"
