"""A tiny hand-built world used to exercise the engine before real geometry exists.

Six regions, three powers, one player. Small enough to reason about by hand when a rule
misfires, and large enough that adjacency, ownership transfer and relations all have
something to say. Phase 1b replaces the geometry with baked Natural Earth polygons; the
engine cannot tell the difference, which is the point of keeping the shape identical.
"""

from __future__ import annotations

from ..state.bootstrap import NationSeed, RegionSeed, WorldSeed
from ..state.models import Difficulty

__all__ = ["sandbox_seed"]


def sandbox_seed(
    *,
    world_id: str = "sandbox",
    seed: int = 42,
    difficulty: Difficulty = Difficulty.NORMAL,
) -> WorldSeed:
    nations = (
        NationSeed(
            id="aurelia",
            name="Aurelia",
            colour="#3B6EA5",
            capital_region_id="r_north",
            is_player=True,
            personality_blurb="A maritime republic that prefers commerce to conquest, but remembers slights.",
            standing_goals=("secure the northern trade routes", "avoid a two-front war"),
        ),
        NationSeed(
            id="karthos",
            name="Karthos",
            colour="#A5453B",
            capital_region_id="r_south",
            personality_blurb="A militarist power convinced that its neighbours are conspiring against it.",
            standing_goals=("break the northern trade monopoly", "secure a warm-water port"),
            military_strength=32.0,
        ),
        NationSeed(
            id="veyra",
            name="Veyra",
            colour="#4C8C5A",
            capital_region_id="r_east",
            personality_blurb="An inland federation that survives by playing its larger neighbours against each other.",
            standing_goals=("stay independent", "sell to whoever is winning"),
            military_strength=14.0,
        ),
    )

    regions = (
        RegionSeed("r_north", "Northmark", "geo:northmark", "aurelia", population=4.0, development=3.0),
        RegionSeed("r_coast", "The Reaches", "geo:reaches", "aurelia", kind="coastal", population=2.0),
        RegionSeed("r_south", "Karthos Basin", "geo:basin", "karthos", population=5.0, development=3.0),
        RegionSeed("r_west", "Westfell", "geo:westfell", "karthos", terrain="mountains", population=1.0),
        RegionSeed("r_east", "Veyran Plateau", "geo:plateau", "veyra", terrain="hills", population=3.0),
        RegionSeed("r_sea", "The Narrow Sea", "geo:narrowsea", None, kind="ocean"),
    )

    adjacency = (
        ("r_north", "r_coast"),
        ("r_coast", "r_sea"),
        ("r_sea", "r_south"),
        ("r_south", "r_west"),
        ("r_south", "r_east"),
        ("r_east", "r_north"),
        ("r_west", "r_east"),
    )

    return WorldSeed(
        id=world_id,
        name="The Narrow Sea",
        start_date="1897-03-01",
        rng_seed=seed,
        difficulty=difficulty,
        nations=nations,
        regions=regions,
        adjacency=adjacency,
        preset_id="sandbox",
    )
