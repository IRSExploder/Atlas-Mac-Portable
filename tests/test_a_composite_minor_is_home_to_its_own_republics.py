"""Yugoslavia was seeded as nobody's homeland, in every cold-war game.

`_disown_dependencies` NULLs `region.home_nation_id` for held ground that is not a power's
own core, and decides coreness by asking whether the holder *is* the code. That reading is
right for two of the three shapes in `Scenario.sovereignty`:

* a **power** holding a dependency -- `gbr` is not `m_hkg`, so Hong Kong is disowned, which
  is the entire point of the pass;
* a **minor whose own code is among its constituents** -- `m_eth` holds ETH and ERI, so
  Ethiopia keeps its core and only Eritrea is disowned.

It is wrong for the third: a minor composed of codes that do not include its own.
Yugoslavia is SRB, HRV, BIH, SVN, MNE, MKD and KOS, and there is no YUG -- so all seven
republics, Belgrade included, were seeded as nobody's core, and the country made of them
held them as though they were colonies.

That is not cosmetic. `home_nation_id` is read by `logistics._depots` (so Yugoslavia had no
supply source at all), by `unrest.held_foreign_provinces` (so occupying it was free), by
`capitulation` (so it could not be made to surrender), and by `nuclear.deterrence`.

The last test here is the forcing function, and it matters more than the first two: it
fails for any *future* era that adds a minor holder whose own code is absent without
declaring it a composite. Yugoslavia is the only such entry in the tree today, and the
reason this went unnoticed is that nothing was checking for a second one.
"""

from __future__ import annotations

import pytest

from atlas.map.pack import PackSet
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import SCENARIOS, preset_politics, preset_seed, scenario_for
from atlas.scenarios.territory import pack_paths
from atlas.state.bootstrap import create_world

pytest.importorskip("atlas.map.pack")


@pytest.fixture(scope="module")
def cold_war():
    preset = load_preset("cold-war")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as packs:
        conn = create_world(":memory:", preset_seed(preset, packs, world_id="cw"))
        preset_politics(conn, "cw", preset)
        yield conn
        conn.close()


def test_yugoslavia_is_home_to_its_own_republics(cold_war):
    disowned = [
        str(r["id"]) for r in cold_war.execute(
            "SELECT id FROM region WHERE world_id = 'cw'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = 'm_yug'"
            "   AND home_nation_id IS NULL ORDER BY id")
    ]
    assert disowned == [], (
        f"{len(disowned)} Yugoslav provinces are nobody's homeland: {disowned}")


def test_a_dependency_is_still_disowned(cold_war):
    """The other half, and the one a careless fix breaks: sparing composites must not
    spare colonies. Hong Kong is held by Britain and is nobody's core; Eritrea is held by
    Ethiopia and is nobody's core; Ethiopia proper is Ethiopia's."""
    def home(region_id):
        row = cold_war.execute(
            "SELECT home_nation_id FROM region WHERE id = ?", (region_id,)).fetchone()
        return None if row is None else row["home_nation_id"]

    assert home("r_hkg") is None, "a British dependency is not British homeland"
    assert home("r_eri") is None, "Eritrea is annexed ground, not Ethiopian core"
    assert home("r_eth") == "m_eth", "Ethiopia proper is still Ethiopia's own"


def test_a_living_nation_that_holds_ground_is_home_somewhere(cold_war):
    """The general invariant. It flagged exactly one nation before the fix."""
    homeless = [
        str(r["id"]) for r in cold_war.execute(
            "SELECT n.id FROM nation n WHERE n.world_id = 'cw' AND n.is_alive = 1"
            "   AND EXISTS (SELECT 1 FROM region r WHERE r.world_id = n.world_id"
            "                AND COALESCE(r.controller_nation_id, r.owner_nation_id) = n.id"
            "                AND r.kind <> 'ocean')"
            "   AND NOT EXISTS (SELECT 1 FROM region r WHERE r.world_id = n.world_id"
            "                    AND r.home_nation_id = n.id)"
            " ORDER BY n.id")
    ]
    assert homeless == [], f"nations that hold ground but are home to none of it: {homeless}"


@pytest.mark.parametrize("preset_id", sorted(SCENARIOS))
def test_every_composite_minor_is_declared(preset_id):
    """**The forcing function.** A minor holder whose own code is not among its
    constituents is composed of them, so disowning them disowns the country itself. Any
    era that adds one has to say so in `Scenario.composites`, and fails here until it does.

    Read off the scenario tables rather than a built world, so it costs nothing and covers
    every era including ones with no baked geometry.
    """
    scenario = scenario_for(preset_id)
    assert scenario is not None, f"{preset_id} has no scenario"
    undeclared = [
        holder for holder, _name, codes in scenario.sovereignty
        if holder.startswith("m_")
        and holder not in {f"m_{code.lower()}" for code in codes}
        and holder not in scenario.composites
    ]
    assert undeclared == [], (
        f"{preset_id}: {undeclared} hold only codes that are not their own, so they are "
        "composed of that ground rather than holding it -- declare them in "
        "`Scenario.composites` or their whole territory is seeded as nobody's homeland"
    )
