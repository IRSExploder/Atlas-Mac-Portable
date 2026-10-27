"""The alternate 1972: a war that is already running when the models are asked.

Three matches on the historical cold-war board produced détente every time, which is what
that year's incentives recommend and tells you nothing about how a model commands troops.
This era removes the choice to stay at peace and leaves every other choice open. These
tests pin the three mechanisms that make the war unavoidable -- the wars themselves, the
ground already lost, and the armies in contact -- and the claim the whole thing rests on:
that adding it moves no other era.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atlas.map.pack import PackSet
from atlas.scenarios import hot_cold_war
from atlas.scenarios.cold_war import SCENARIO as COLD_WAR
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import DERIVED_FROM, SCENARIOS, preset_politics, preset_seed
from atlas.scenarios.seeder import build_world
from atlas.scenarios.territory import load_territory, pack_paths
from atlas.state.bootstrap import create_world, fingerprint

ERA = "hot-cold-war"


def _world(world_id: str = "fulda") -> sqlite3.Connection:
    preset = load_preset(ERA)
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id=world_id)
    conn = create_world(":memory:", seed)
    preset_politics(conn, world_id, preset)
    return conn


@pytest.fixture(scope="module")
def fulda():
    conn = _world()
    yield conn
    conn.close()


# --------------------------------------------------------------------------- the premise


def test_the_era_is_registered_and_playable():
    assert SCENARIOS[ERA] == "atlas.scenarios.hot_cold_war"
    preset = load_preset(ERA)
    assert preset is not None and preset.builtin
    assert preset.start_date == "1972-01-05"


def test_the_rules_do_not_tell_a_government_the_war_cannot_happen():
    """The historical era's rule -- *the superpowers fight through clients, not directly* --
    is the one thing this world has already broken, and leaving it in would tell every
    model that the war it is fighting is impossible."""
    hot = load_preset(ERA)
    cold = load_preset("cold-war")
    assert any("through clients" in rule for rule in cold.simulation_rules)
    assert not any("through clients" in rule for rule in hot.simulation_rules)
    assert any("cannot be wished away" in rule for rule in hot.simulation_rules)
    # The threshold that must survive: a conventional reverse is not a reason for release.
    assert any("First use" in rule for rule in hot.simulation_rules)


def test_it_reads_its_base_era_map_rather_than_a_copy(fulda):
    assert DERIVED_FROM[ERA] == "cold-war"
    mine, base = load_territory(ERA), load_territory("cold-war")
    assert mine is not None and mine.nations == base.nations
    assert fulda.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = 'fulda'").fetchone()[0] > 400


# ------------------------------------------------------------------ the war is unavoidable


def test_every_belligerent_pair_is_at_war_in_both_directions(fulda):
    """`war-is-symmetric` is a SEED invariant, and a pair written one way would fail it."""
    rows = {(str(r["nation_a"]), str(r["nation_b"])) for r in fulda.execute(
        "SELECT nation_a, nation_b FROM relation WHERE world_id = 'fulda' AND at_war = 1")}
    for a, b in hot_cold_war.BELLIGERENTS:
        assert (a, b) in rows and (b, a) in rows, (a, b)
    assert len(rows) == 2 * len(hot_cold_war.BELLIGERENTS)


def test_the_three_blocs_are_each_at_war_with_each_other_somewhere(fulda):
    """A three-cornered war is the point. With one pair at peace the third family would be
    commanding in somebody else's war."""
    from atlas import cli

    rosters = {name: set(members) for name, members in cli._COLD_WAR_BLOCS}
    at_war = {(str(r["nation_a"]), str(r["nation_b"])) for r in fulda.execute(
        "SELECT nation_a, nation_b FROM relation WHERE world_id = 'fulda' AND at_war = 1")}
    names = sorted(rosters)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            assert any((a, b) in at_war
                       for a in rosters[first] for b in rosters[second]), (first, second)


def test_the_quarrels_nobody_is_fighting_over_survive_the_war(fulda):
    """A war does not erase the politics it did not reach: a pair that is not fighting keeps
    its 1972 row exactly. `chn|usa` is such a pair -- Beijing and Washington are not at war
    in this era -- so this is the half of the rule `_at_war` never touches."""
    score = fulda.execute(
        "SELECT score, at_war FROM relation WHERE world_id = 'fulda' AND nation_a = 'chn'"
        "   AND nation_b = 'usa'").fetchone()
    assert score["at_war"] == 0, "the premise: these two are not fighting"
    assert score["score"] == -30, "Beijing's regard for Washington is the era's"


def test_a_pair_that_is_fighting_is_flattened_to_the_floor(fulda):
    """And the half that is lost, stated rather than left to be discovered. The Sino-Soviet
    split was -85/-80 and the superpowers -70/-68; at war they are all -95, so the ranking
    of hostility *between belligerents* does not survive. Deliberate, and revisable."""
    scores = {
        (str(r["nation_a"]), str(r["nation_b"])): float(r["score"])
        for r in fulda.execute(
            "SELECT nation_a, nation_b, score FROM relation WHERE world_id = 'fulda'"
            "   AND at_war = 1")
    }
    assert scores[("chn", "sov")] == scores[("sov", "chn")] == -95
    assert scores[("usa", "sov")] == scores[("sov", "usa")] == -95
    assert set(scores.values()) == {-95.0}


# --------------------------------------------------------------------- the ground and the line


def test_the_pact_holds_west_german_ground_without_owning_it(fulda):
    held = {str(r["id"]): (r["owner_nation_id"], r["controller_nation_id"], r["home_nation_id"])
            for r in fulda.execute(
                "SELECT id, owner_nation_id, controller_nation_id, home_nation_id"
                "  FROM region WHERE world_id = 'fulda' AND controller_nation_id IS NOT NULL")}
    assert set(held) == set(hot_cold_war.FRONT)
    for region, controller in hot_cold_war.FRONT.items():
        owner, holder, home = held[region]
        assert (owner, holder) == ("wger", controller)
        assert home == "wger", "occupied ground is not the occupier's homeland"


def test_the_armies_are_in_contact_rather_than_at_home(fulda):
    """Each front province holds both an occupier and its owner's defenders, so the first
    round has a battle in it rather than a march."""
    for region in hot_cold_war.FRONT:
        sides = {str(r["nation_id"]) for r in fulda.execute(
            "SELECT DISTINCT nation_id FROM unit WHERE world_id = 'fulda' AND region_id = ?",
            (region,))}
        assert hot_cold_war.FRONT[region] in sides
        assert "wger" in sides, f"{region} has no defender"


def test_natos_forward_garrisons_stand_where_1972_had_them(fulda):
    """The counterfactual is that they are fighting, not that they exist.

    Exactly the declared number, not merely at least it: `>=` cannot see a deployment
    placed twice, which is the mistake a second call to the placement loop would make.
    """
    for region, (power, divisions) in hot_cold_war.DEPLOYMENTS.items():
        found = fulda.execute(
            "SELECT COUNT(*) FROM unit WHERE world_id = 'fulda' AND region_id = ?"
            "   AND nation_id = ? AND id LIKE ?",
            (region, power, f"u_{power}_{region.removeprefix('r_')}_inf%")).fetchone()[0]
        assert found == divisions, (region, power, found)


def test_the_whole_front_is_in_supply(fulda):
    """**The distortion this era used to carry, and the fix, pinned together.**

    Until 2026-09-20 a depot had to be ground the nation *owned*, so an army posted on an
    ally's was fed only if it happened to stand next to its own country: 3 of these 21
    divisions were in supply and the other 18 lost strength every month and fought at
    `_UNSUPPLIED_EFFECTIVENESS` from turn 1, whatever any government decided. A forward
    base (`logistics._depots`) feeds an army where it stands when it is standing on an
    ally's ground in peace with it, which is what a garrison abroad is.

    All 21 now. Niedersachsen is the one worth watching: the Soviet spearhead there is fed
    from the station in Thüringen, across ground it controls, which is the ordinary walk --
    the rule adds a source, not a corridor through somebody else's country.
    """
    from atlas.engine.logistics import in_supply

    unfed = [(region, power) for region, (power, _d) in sorted(hot_cold_war.DEPLOYMENTS.items())
             if not in_supply(fulda, "fulda", power, region)]
    assert unfed == [], unfed
    assert sum(d for _p, d in hot_cold_war.DEPLOYMENTS.values()) == 21


def test_a_deployment_to_nowhere_is_refused():
    """A region id that matches nothing is the failure every territory override guards."""
    from dataclasses import replace

    preset = load_preset(ERA)
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked")
    with PackSet.open(paths) as pack:
        with pytest.raises(ValueError, match="not on the map"):
            build_world(replace(hot_cold_war.SCENARIO,
                                deployments={"r_nowhere": ("usa", 2)}),
                        pack, world_id="bad")
        with pytest.raises(ValueError, match="not a power"):
            build_world(replace(hot_cold_war.SCENARIO,
                                deployments={"r_wger_bayern": ("atlantis", 2)}),
                        pack, world_id="bad")


# ------------------------------------------------------------------------ it moves nothing else


def test_the_historical_cold_war_is_untouched():
    """The claim the tape rests on: an era added beside the Cold War must not move it.

    **The digest is of one world id.** `world_id` is part of what `fingerprint` hashes, so
    a constant is only meaningful beside the id it was taken under. `fp` is the id
    `scratchpad/seed_fingerprint.py` uses, and this is that script's cold-war figure on
    master (`968f406` and `bb63448` both), re-measured 2026-09-18.

    It is *not* the `088720df…` in `fingerprint-includes-wall-clock`. That value came from
    running the script without `PYTHONPATH` set to a worktree, where the editable install
    silently serves `atlas` from the main tree instead -- so it is an older branch's world,
    not master's. `tests/` always import the tree under test, which is why this constant is
    the honest one to pin.
    """
    preset = load_preset("cold-war")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id="fp")
    conn = create_world(":memory:", seed)
    try:
        preset_politics(conn, "fp", preset)
        assert fingerprint(conn, "fp") == "e87d25df7e778b769bca75439e66fa8e"
        assert not COLD_WAR.deployments and not COLD_WAR.occupied
    finally:
        conn.close()


def test_the_world_is_the_same_world_twice():
    first, second = _world("twice"), _world("twice")
    try:
        assert fingerprint(first, "twice") == fingerprint(second, "twice")
    finally:
        first.close()
        second.close()


def test_an_authored_clone_wins_the_map_over_a_shipped_derived_era(monkeypatch):
    """Precedence, matching `registry.scenario_for`: authored content first.

    A derived era has no territory file of its own and reads its base's; so does a workshop
    clone. If a user ever saves a clone under an id that is also a shipped derived era, the
    clone is the one they meant -- and it was losing silently, which would have built the
    board out of the wrong era's provinces.
    """
    from atlas.scenarios import territory as territory_module

    real_path = territory_module.territory_path
    monkeypatch.setattr(
        territory_module, "territory_path",
        lambda scenario_id: (Path("no-such-territory-file.json") if scenario_id == ERA
                             else real_path(scenario_id)))
    monkeypatch.setattr("atlas.scenarios.workshop.base_of",
                        lambda scenario_id: "gathering-storm" if scenario_id == ERA else "")

    found = territory_module.load_territory(ERA)
    assert found is not None and found.scenario_id == "gathering-storm", (
        "the authored clone's base, not the shipped era's")
