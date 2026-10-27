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
