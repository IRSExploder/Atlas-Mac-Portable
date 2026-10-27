"""Cloning a scenario, changing it as data, and being told whether it holds together.

The claim the workshop makes that a text editor cannot is not "this file parsed". It is
*this is coherent and reproducible* -- the same seed-phase invariants the release audit runs
against the shipped eras, plus building twice and getting the same world. An authored
scenario that does not reproduce is a broken save waiting to happen, and one that violates an
invariant is playable and wrong.

So the tests are: the base survives cloning untouched, an edit reaches the built world, the
clone has its own identity, the check catches something genuinely broken rather than
congratulating it, and the result plays through the ordinary turn pipeline.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from atlas.scenarios import workshop
from atlas.scenarios.presets import delete_preset, list_presets, load_preset
from atlas.scenarios.registry import builtin_scenario_for, scenario_for

# **`workshop_root` is deliberately not imported by name.** `conftest` redirects it to a
# per-run directory so that two suites cannot delete each other's clones, and a name bound
# here at import time would keep pointing at the real one -- which would put the directory
# in one place and the overlay written into it in another. Reached through the module.
from atlas.scenarios.workshop import (
    EDITABLE,
    Overlay,
    authored_scenario,
    base_of,
    check,
    clone_scenario,
    overlay_path,
)

BASE = "cold-war"


def _forget(preset_id: str) -> None:
    with contextlib.suppress(Exception):
        delete_preset(preset_id)
    with contextlib.suppress(Exception):
        overlay_path(preset_id).unlink()


@pytest.fixture
def clone():
    """A clone of the Cold War with three edits in it, cleaned up afterwards."""
    _forget("test-detente")
    yield clone_scenario(
        BASE, new_id="test-detente", name="Test Detente",
        overlay=Overlay(base=BASE,
                        relations=[["usa", "sov", 60, False]],
                        arsenals={"gbr": [40, "triad"]},
                        deterrent_boats={"gbr": 6}))
    _forget("test-detente")


# ------------------------------------------------------------------- the clone itself


def test_the_base_scenario_is_untouched(clone):
    """The discipline a forked world keeps: the thing you copied is only ever read."""
    base = builtin_scenario_for(BASE)
    assert base.arsenals["gbr"] == (4, "sea"), "the shipped arsenal is what it always was"
    assert base.id == BASE
    assert any(entry[2] == -70 for entry in base.relations
               if (entry[0], entry[1]) == ("usa", "sov"))


def test_the_edits_are_in_the_scenario_the_clone_resolves_to(clone):
    edited = scenario_for("test-detente")

    assert edited is not None and edited.id == "test-detente"
    assert edited.arsenals["gbr"] == (40, "triad")
    assert edited.deterrent_boats["gbr"] == 6
    assert next(entry[2] for entry in edited.relations
                if (entry[0], entry[1]) == ("usa", "sov")) == 60


def test_everything_else_comes_from_the_base(clone):
    """An overlay is edits, not a rewrite: what it does not mention is inherited."""
    base = builtin_scenario_for(BASE)
    edited = scenario_for("test-detente")

    assert edited.powers == base.powers
    assert edited.sovereignty == base.sovereignty
    assert edited.treaties == base.treaties
    assert len(edited.relations) == len(base.relations)
    assert edited.arsenals["usa"] == base.arsenals["usa"], "an untouched arsenal is the base's"


def test_a_clone_is_listed_and_is_not_builtin(clone):
    found = {preset.id: preset for preset in list_presets()}
    assert "test-detente" in found
    assert not found["test-detente"].builtin
    assert found[BASE].builtin, "and the base is still there, still shipped"


def test_a_clone_knows_what_it_came_from(clone):
    assert base_of("test-detente") == BASE
    assert base_of(BASE) == "", "a shipped scenario is not a clone"
    assert base_of(None) == ""


def test_cloning_onto_a_name_that_exists_is_refused(clone):
    with pytest.raises(FileExistsError):
        clone_scenario(BASE, new_id="test-detente", name="Again")


def test_cloning_something_that_does_not_exist_is_refused():
    with pytest.raises(ValueError):
        clone_scenario("no-such-scenario", new_id="test-nope", name="Nope")


def test_a_shipped_scenario_still_resolves_to_its_module():
    """The authored lookup runs first, and must not shadow anything shipped."""
    for preset_id in (BASE, "gathering-storm", "modern-day"):
        assert scenario_for(preset_id) is builtin_scenario_for(preset_id)


# --------------------------------------------------------- coherent and reproducible


def test_a_clone_builds_coherent_and_reproducible(clone):
    verdict = check("test-detente")

    assert verdict.built and verdict.coherent and verdict.reproducible
    assert verdict.nations > 100 and verdict.regions > 100
    assert "Coherent and reproducible" in verdict.sentence()


def test_a_clone_has_its_own_identity(clone):
    """Its own fingerprint, like a forked world has its own directory."""
    assert check("test-detente").fingerprint != check(BASE).fingerprint


def test_the_base_still_checks_out_after_being_cloned(clone):
    assert check(BASE).coherent


def test_an_overlay_built_on_nothing_is_caught_rather_than_congratulated():
    """**The failure the check exists for.**

    `scenario_for` answers ``None`` for an unknown scenario and the seeder obligingly builds
    a *generic* world from it -- perfectly coherent, and not remotely the one that was
    authored. Without this the workshop would report a scenario whose every edit had been
    silently discarded as fine.
    """
    _forget("test-groundless")
    try:
        clone_scenario(BASE, new_id="test-groundless", name="Groundless")
        # Written directly: `clone_scenario` fixes the base, which is what stops this
        # happening by accident. A hand-edited or half-copied overlay can still do it.
        overlay_path("test-groundless").write_text(
            json.dumps({"base": "nonesuch", "relations": [], "arsenals": {},
                        "deterrent_boats": {}, "rng_seed": None}), encoding="utf-8")

        assert authored_scenario("test-groundless") is None
        verdict = check("test-groundless")
        assert not verdict.coherent
        assert any("nonesuch" in problem for problem in verdict.problems)
    finally:
        _forget("test-groundless")


def test_a_malformed_overlay_does_not_take_anything_down():
    """It is read by the territory loader too, which runs for every scenario there is."""
    _forget("test-malformed")
    try:
        workshop.workshop_root().mkdir(parents=True, exist_ok=True)
        overlay_path("test-malformed").write_text("{ not json", encoding="utf-8")
        assert authored_scenario("test-malformed") is None
        assert base_of("test-malformed") == ""
        assert scenario_for(BASE) is not None, "and the shipped ones still load"
    finally:
        _forget("test-malformed")


# ------------------------------------------------------------- it plays like any other


def test_a_clone_builds_a_world_with_the_edits_in_it(clone):
    from atlas.map.pack import PackSet
    from atlas.scenarios.registry import preset_politics, preset_seed
    from atlas.scenarios.territory import pack_paths
    from atlas.state.bootstrap import create_world

    preset = load_preset("test-detente")
    with PackSet.open(pack_paths(preset.id, preset.map_source)) as packs:
        seed = preset_seed(preset, packs, world_id="d", player=None)
    conn = create_world(":memory:", seed)
    preset_politics(conn, "d", preset)

    assert tuple(conn.execute(
        "SELECT warheads, delivery FROM arsenal WHERE nation_id = 'gbr'").fetchone()) \
        == (40, "triad")
    assert conn.execute(
        "SELECT score FROM relation WHERE nation_a='usa' AND nation_b='sov'"
    ).fetchone()[0] == 60
    conn.close()


def test_a_clone_inherits_the_territory_of_what_it_came_from(clone):
    """An authored scenario has no territory file of its own. Without the fallback it is
    built at country granularity while its sovereignty still names provinces, which
    collides on `region.geo_key` and fails to build at all -- found by cloning and watching
    it refuse."""
    from atlas.scenarios.territory import load_territory

    inherited = load_territory("test-detente")
    assert inherited is not None
    assert inherited.scenario_id == BASE
    assert check("test-detente").regions == check(BASE).regions


def test_a_clone_plays_a_turn_through_the_ordinary_pipeline(clone):
    """No second path: it is a preset and a scenario, so the turn engine takes it."""
    import asyncio

    from atlas.engine.routing import Router
    from atlas.engine.turn_engine import TurnEngine
    from atlas.map.pack import PackSet
    from atlas.providers.cli_provider import default_role_models
    from atlas.providers.mock import MockProvider, ScriptedResponse, always
    from atlas.scenarios.registry import preset_politics, preset_seed
    from atlas.scenarios.territory import pack_paths
    from atlas.state.bootstrap import create_world

    preset = load_preset("test-detente")
    with PackSet.open(pack_paths(preset.id, preset.map_source)) as packs:
        seed = preset_seed(preset, packs, world_id="d", player=None)
    conn = create_world(":memory:", seed)
    preset_politics(conn, "d", preset)

    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": [], "events": []})))
    engine = TurnEngine(
        conn=conn, world_id="d",
        providers={key: provider for key in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()))
    report = asyncio.run(engine.jump(days=30))

    assert report.turn == 1
    assert conn.execute("SELECT turn_number FROM world WHERE id = 'd'").fetchone()[0] == 1
    conn.close()


def test_what_is_editable_is_stated_and_small():
    """The list is a promise about what cannot break: each of these is a value with no
    referential tail. Adding a power or a treaty can point at something absent, and those
    are deliberately not offered."""
    assert set(EDITABLE) == {"relation", "arsenal", "deterrent_boats", "rng_seed"}
    for description in EDITABLE.values():
        assert len(description) > 20
