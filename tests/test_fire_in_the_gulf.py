"""The alternate 2027: Iran has closed Hormuz and holds the Saudi oil coast.

Built from Modern Day by changing decisions, not the board. These pin the war, the ground
already lost, the land bridge through Iraq that keeps Iran's army fed, and the audit that
proves nothing else about the present day moved.
"""

from __future__ import annotations

import pytest

from atlas.map.pack import PackSet
from atlas.scenarios import fire_in_the_gulf
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import DERIVED_FROM, SCENARIOS, preset_politics, preset_seed
from atlas.scenarios.territory import pack_paths
from atlas.state.bootstrap import create_world
from atlas.testing.audit import audit, load_manifest
from atlas.testing.invariants import violations

ERA = "fire-in-the-gulf"
WORLD = "gulf"


@pytest.fixture(scope="module")
def gulf():
    preset = load_preset(ERA)
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id=WORLD)
    conn = create_world(":memory:", seed)
    preset_politics(conn, WORLD, preset)
    yield conn
    conn.close()


def test_the_era_is_registered_built_on_modern_day_and_playable():
    assert SCENARIOS[ERA] == "atlas.scenarios.fire_in_the_gulf"
    assert DERIVED_FROM[ERA] == "modern-day"
    preset = load_preset(ERA)
    assert preset is not None and preset.builtin and preset.start_date == "2027-03-01"
    assert {"tur", "sau", "irn"} <= set(preset.playable)


def test_the_wars_are_running_both_ways(gulf):
    for a, b in fire_in_the_gulf.BELLIGERENTS:
        for x, y in ((a, b), (b, a)):
            row = gulf.execute("SELECT at_war FROM relation WHERE world_id = ? AND"
                               " nation_a = ? AND nation_b = ?", (WORLD, x, y)).fetchone()
            assert row is not None and row[0] == 1, (x, y)
    # Washington has not decided: the United States is at war with nobody here.
    assert not gulf.execute("SELECT 1 FROM relation WHERE world_id = ? AND nation_a = 'usa'"
                            " AND at_war = 1 AND nation_b IN ('irn', 'm_irq')",
                            (WORLD,)).fetchone()


def test_iran_holds_the_oil_coast_and_kuwait_but_owns_neither(gulf):
    for region, holder in fire_in_the_gulf.FRONT.items():
        owner, controller = gulf.execute(
            "SELECT owner_nation_id, controller_nation_id FROM region WHERE id = ?",
            (region,)).fetchone()
        assert controller == holder and owner != holder


def test_iraq_is_the_land_bridge_and_the_invasion_army_is_fed(gulf):
    from atlas.engine.logistics import in_supply

    pact = gulf.execute(
        "SELECT c.status FROM commitment c JOIN commitment_nation a ON a.commitment_id = c.id"
        " JOIN commitment_nation b ON b.commitment_id = c.id WHERE c.world_id = ? AND"
        " c.kind = 'defensive_pact' AND a.nation_id = 'irn' AND b.nation_id = 'm_irq'",
        (WORLD,)).fetchone()
    assert pact is not None and pact[0] == "active"
    unfed = [(region, power) for region, (power, _d) in fire_in_the_gulf.DEPLOYMENTS.items()
             if not in_supply(gulf, WORLD, power, region)]
    assert unfed == []


def test_the_2023_normalisation_is_void_rather_than_left_broken(gulf):
    assert not gulf.execute(
        "SELECT 1 FROM commitment c JOIN commitment_nation a ON a.commitment_id = c.id"
        " JOIN commitment_nation b ON b.commitment_id = c.id WHERE c.world_id = ? AND"
        " c.kind = 'non_aggression_pact' AND a.nation_id = 'sau' AND b.nation_id = 'irn'",
        (WORLD,)).fetchone()


def test_the_world_is_sound_and_the_present_day_is_otherwise_untouched(gulf):
    assert violations(gulf, WORLD) == []
    assert audit(gulf, WORLD, load_manifest(ERA)) == []


def test_liberate_retake_and_backing_the_opposition_are_orders(gulf):
    """Found by a live game: "Liberate Kuwait" was not recognised for seventeen months,
    "Retake Ash Sharqiyah" was refused as a creative order, and funding Kurdish opposition
    groups was not read as backing rebels."""
    from atlas.engine.staff_bots import parse_orders

    def kind(text):
        (action,) = parse_orders(gulf, WORLD, nation_id="sau", raw_text=text)["actions"]
        return action["type"], action.get("to_region"), action.get("gambit_kind")

    assert kind("Liberate Kuwait.") == ("move_units", "r_kwt", None)
    assert kind("Retake Ash Sharqiyah.") == ("move_units", "r_sau_ash_sharqiyah", None)
    assert kind("Fund Kurdish opposition groups inside Iran.")[::2] == ("gambit", "incite")


def test_ground_freed_from_an_occupier_goes_back_to_its_owner(conn):
    """Liberation, not a new occupation: Veyra's province, held by Karthos, taken by
    Aurelia while Veyra is fighting Karthos too, is handed back to Veyra."""
    from atlas.engine.resolution import _liberated_for

    for a, b in (("aurelia", "karthos"), ("veyra", "karthos")):
        conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = 'sandbox' AND"
                     " ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
                     (a, b, b, a))
    region = {"owner_nation_id": "veyra"}
    assert _liberated_for(conn, "sandbox", region, attacker="aurelia",
                          defender="karthos") == "veyra"
    # Not when the owner is the attacker's enemy too, and not when it is not in the war.
    conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = 'sandbox' AND"
                 " nation_a IN ('aurelia', 'veyra') AND nation_b IN ('aurelia', 'veyra')")
    assert _liberated_for(conn, "sandbox", region, attacker="aurelia",
                          defender="karthos") is None
