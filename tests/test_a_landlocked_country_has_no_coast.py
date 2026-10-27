"""San Marino had a coastline, a navy it could build, and a sea an invader could land on.

Land and sea come from different Natural Earth layers, digitised independently, so a coast
appears twice with coordinates that are close and never equal. `derive_coast_adjacency`
bridges that by snapping both sides onto a 0.25-degree grid -- about 25km -- and calling a
sea and a coast adjacent where they land in the same cell.

**For a country smaller than a grid cell that test cannot mean anything.** San Marino is
0.1 degrees across, entirely surrounded by Italy, and shares a cell with the Adriatic whose
nearest vertex is **14.9km away**. It came out coastal: `kind = 'coastal'`, a sea adjacency,
`sea_lift` and `amphibious_reach` both willing to work with it.

The fix is a real-distance check applied **only to sub-cell shapes**, because that is
exactly where the grid proved nothing. The province pack's sea and land vertices disagree by
up to 27km in places, so a global distance test at any useful threshold would take the coast
off real islands -- the restriction is what separates "the tolerance was wider than the
country" from "the two datasets disagree here".

**This deliberately moves the regions-section fingerprint of both seeded eras**, and only
that section. San Marino is one row of one table, and every other fingerprinted section
hashes identically before and after.
"""

from __future__ import annotations

import pytest

from atlas.scenarios.earth import (
    _COAST_CELL,
    _SMALL_SHAPE_KM,
    _km_between,
    _spans_less_than,
    _within_km,
    derive_coast_adjacency,
)

pytest.importorskip("atlas.map.pack")


# ------------------------------------------------------- the predicate, with no packs at all


def test_a_shape_is_small_when_it_fits_inside_one_cell():
    tiny = [(12.40, 43.90), (12.51, 43.99)]
    wide = [(12.40, 43.90), (13.10, 43.99)]
    assert _spans_less_than(tiny, _COAST_CELL)
    assert not _spans_less_than(wide, _COAST_CELL)


def test_a_shape_is_measured_on_its_longer_axis():
    """A long thin country is not small merely because it is thin."""
    thin = [(12.40, 43.00), (12.45, 44.00)]
    assert not _spans_less_than(thin, _COAST_CELL)


def test_distance_shrinks_a_degree_of_longitude_toward_the_poles():
    """The same latitude correction `_land_area` uses. Without it a northern cell is
    judged by a tolerance a third too generous."""
    equator = _km_between((0.0, 0.0), (1.0, 0.0))
    arctic = _km_between((0.0, 70.0), (1.0, 70.0))
    assert equator > arctic * 2.5


def test_the_check_only_compares_inside_shared_cells():
    """It rules a pairing out, never in: two shapes with no cell in common are not
    brought together by it."""
    sea = {(1, 1): [(0.30, 0.30)]}
    land = {(2, 2): [(0.60, 0.60)]}
    assert not _within_km(sea, land, 1000.0)


def test_a_pairing_survives_when_something_really_is_close():
    sea = {(1, 1): [(0.30, 0.30)], (9, 9): [(2.30, 2.30)]}
    land = {(1, 1): [(0.31, 0.30)]}
    assert _within_km(sea, land, _SMALL_SHAPE_KM)
    assert not _within_km(sea, land, 0.1)


# --------------------------------------------------------------------- and over the real map


@pytest.fixture(scope="module")
def coast():
    """Every sea-to-coast pairing on the country pack, or a skip if it is not baked."""
    from atlas.config.paths import packs_root
    from atlas.map.pack import GeometryPack

    land_path = packs_root() / "admin0-50m.pack"
    sea_path = packs_root() / "marine-10m.pack"
    if not (land_path.exists() and sea_path.exists()):
        pytest.skip("geometry packs not baked; run `atlas map bake`")
    with GeometryPack(sea_path) as sea, GeometryPack(land_path) as land:
        yield {key for _sea, key in derive_coast_adjacency(sea, land)}


def test_san_marino_is_landlocked(coast):
    """**The repro.** 14.9km from the Adriatic, and it had a coast."""
    assert "admin0-50m:SMR" not in coast


def test_monaco_still_has_its_coast(coast):
    """Sub-cell too, and genuinely on the water at 4.6km. The check has to tell them
    apart, or it has traded one wrong answer for another."""
    assert "admin0-50m:MCO" in coast


@pytest.mark.parametrize("key", [
    "admin0-50m:ITA", "admin0-50m:GBR", "admin0-50m:JPN", "admin0-50m:USA",
    "admin0-50m:MAC", "admin0-50m:TWN",
])
def test_the_ordinary_coasts_are_untouched(coast, key):
    assert key in coast


def test_a_landlocked_state_that_was_already_landlocked_stays_so(coast):
    """The other direction: the check must not somehow *add* a coast."""
    for key in ("admin0-50m:AUT", "admin0-50m:CHE", "admin0-50m:LIE",
                "admin0-50m:AND", "admin0-50m:LUX"):
        assert key not in coast


# ------------------------------------------------------------------- and in the seeded eras


@pytest.mark.parametrize("era,player", [("gathering-storm", "ger"), ("modern-day", "usa")])
def test_san_marino_is_land_in_both_eras(era, player):
    """The consequence in a world a game is actually played in: not merely a pairing
    dropped, but `region.kind` and the sea adjacency that follows from it.

    This is the change that moves the regions-section fingerprint, and it is the only
    region either era's table changes.
    """
    from atlas.config.paths import packs_root
    from atlas.map.pack import PackSet
    from atlas.scenarios.presets import load_preset
    from atlas.scenarios.registry import preset_seed
    from atlas.scenarios.territory import pack_paths
    from atlas.state.bootstrap import create_world

    preset = load_preset(era)
    # **Every pack, not merely one of them.** `pack_paths` returns the country pack, the
    # province pack and the water; filtering to the ones that exist and asking only "is the
    # list non-empty" let a *partial* install through -- and this test then failed on San
    # Marino not existing, because the country pack that draws it was the missing one. A
    # precondition that passes on half an install is a precondition that reports a
    # missing download as a bug in the map.
    wanted = pack_paths(era, preset.map_source)
    missing = [path.name for path in wanted if not path.exists()]
    if not (packs_root() / "marine-10m.pack").exists():
        missing.append("marine-10m.pack")
    if missing:
        pytest.skip(f"geometry packs not baked ({', '.join(sorted(set(missing)))}); "
                    "run `atlas map bake`")
    paths = list(wanted)

    with PackSet.open(paths) as pack:
        conn = create_world(
            ":memory:",
            preset_seed(preset, pack, world_id="e", player=player, seed=20260808),
        )
    try:
        row = conn.execute(
            "SELECT kind FROM region WHERE world_id = 'e' AND id = 'r_smr'"
        ).fetchone()
        assert row is not None, "San Marino is not on this map at all"
        assert str(row["kind"]) == "land"
        touching_water = conn.execute(
            """
            SELECT COUNT(*) FROM region_adjacency a
              JOIN region r ON r.id = CASE WHEN a.region_a = 'r_smr' THEN a.region_b
                                           ELSE a.region_a END
             WHERE a.world_id = 'e' AND 'r_smr' IN (a.region_a, a.region_b)
               AND r.kind = 'ocean'
            """
        ).fetchone()[0]
        assert touching_water == 0
    finally:
        conn.close()
