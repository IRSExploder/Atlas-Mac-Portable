"""The political map read for a player: their own country in full, allies one teal, enemies
one red, everyone else muted -- whatever colours those powers happen to have."""

from __future__ import annotations

from atlas.map.query import MapViewState
from atlas.map.render import ALLY_FILL, ENEMY_FILL, _calm_fill, _muted
from atlas.map.scene_model import DEFAULT_PALETTE as P

VIEW = MapViewState(
    world_id="w", turn=1, observer_nation_id="me",
    attitude={"foe": (-90.0, "none", True),
              "pal": (70.0, "defensive", False),
              "red_pal": (70.0, "alliance", False),
              "other": (10.0, "trade", False)})


def test_you_keep_your_colour():
    assert _calm_fill(VIEW, "me", "#C0703A", P) == "#C0703A"


def test_every_enemy_is_the_same_red_and_every_ally_the_same_teal():
    assert _calm_fill(VIEW, "foe", "#3070C0", P) == _muted(ENEMY_FILL, P.ocean, 0.12)
    # An ally whose own colour is red must not read as an enemy.
    assert _calm_fill(VIEW, "red_pal", "#C03030", P) == _calm_fill(VIEW, "pal", "#30C030", P)
    assert _calm_fill(VIEW, "pal", "#30C030", P) == _muted(ALLY_FILL, P.ocean, 0.12)


def test_everyone_else_is_muted_toward_neutral_land():
    for nation in ("other", "stranger"):
        quiet = _calm_fill(VIEW, nation, "#8E44AD", P)
        assert quiet == _muted("#8E44AD", P.unclaimed, 0.8)
        assert quiet not in (_muted(ENEMY_FILL, P.ocean, 0.12), _muted(ALLY_FILL, P.ocean, 0.12))


def _scene(conn, *, at_war: bool):
    from atlas.map.projection import get_projection
    from atlas.map.query import load_map_view
    from atlas.map.render import build_scene

    for a, b in (("aurelia", "karthos"), ("karthos", "aurelia")):
        conn.execute(
            "INSERT INTO relation (world_id, nation_a, nation_b, at_war) VALUES"
            " ('sandbox', ?, ?, ?) ON CONFLICT (nation_a, nation_b)"
            " DO UPDATE SET at_war = excluded.at_war", (a, b, int(at_war)))
    from atlas.map.query import UnitState

    view = load_map_view(conn, "sandbox", observer_nation_id="aurelia")
    # The sandbox defines no unit templates, so its units never reach the view; the map is
    # handed one directly. Everything is known, so fog does not decide the answer.
    view = MapViewState(**{
        **{f: getattr(view, f) for f in view.__dataclass_fields__},
        "known_region_ids": frozenset(r.region_id for r in view.regions),
        "units": (UnitState("u_k", "r_south", "karthos", "infantry", 7.25),)})
    labels = {r.geo_key: (float(i), float(i)) for i, r in enumerate(view.regions)}
    return build_scene(view, projection=get_projection(), pack_id="test",
                       label_points=labels, calm=True, dissolve=True)


def test_every_country_is_named_and_yours_first(conn):
    from atlas.map.scene_model import MarkerKind

    scene = _scene(conn, at_war=True)
    names = {m.label: m.marker_id for m in scene.markers if m.kind is MarkerKind.NAME}
    assert set(names) == {"Aurelia", "Karthos", "Veyra"}
    assert names["Aurelia"].startswith("name:0:")   # you
    assert names["Karthos"].startswith("name:1:")   # at war with you
    assert names["Veyra"].startswith("name:3:")     # everyone else
    assert sorted(names.values())[0] == names["Aurelia"]


def test_an_enemy_counter_is_red_and_says_one_number(conn):
    from atlas.map.scene_model import MarkerKind

    scene = _scene(conn, at_war=True)
    (counter,) = [m for m in scene.markers
                  if m.kind is MarkerKind.UNIT and m.region_id == "r_south"]
    assert counter.colour == ENEMY_FILL
    assert "/" not in counter.label and counter.label
