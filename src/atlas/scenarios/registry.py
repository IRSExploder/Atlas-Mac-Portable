"""Which presets have a scenario module behind them.

A :class:`~atlas.scenarios.presets.Preset` is the *description* of a world -- its name,
its lore, its hard constraints, the map it wants. A :class:`~atlas.scenarios.seeder.Scenario`
is the *content* -- who exists, how strong, who resents whom. Most presets have both; a
preset can have only the first, and then it is seeded generically from the pack.

Before this existed, the join between the two was four ``if preset.id ==
"gathering-storm"`` branches in two files, each of which would have needed a second arm
for every era added. Now the question is "is there a scenario for this preset", asked in
one place.

**Imported lazily and by name.** A scenario module is a large data file, and the setup
screen asking who is playable should not pay for parsing every era's order of battle.
"""

from __future__ import annotations

import sqlite3
from importlib import import_module
from typing import TYPE_CHECKING

from ..state.models import Difficulty
from .seeder import Scenario, apply_politics, build_world

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..map.pack import GeometryPack, PackSet
    from ..state.bootstrap import WorldSeed
    from .presets import Preset

__all__ = ["SCENARIOS", "builtin_scenario_for", "preset_politics",
           "preset_seed", "scenario_for"]

#: Preset id -> the module whose ``SCENARIO`` builds it. A preset absent from this map
#: has no designed content and is seeded generically from the geometry pack.
SCENARIOS: dict[str, str] = {
    "gathering-storm": "atlas.scenarios.gathering_storm",
    "cold-war": "atlas.scenarios.cold_war",
    "modern-day": "atlas.scenarios.modern_day",
    "hot-cold-war": "atlas.scenarios.hot_cold_war",
}

#: Eras built from another era, as ``derived -> base``. A derived era is a shipped
#: counterfactual: the same board and the same territory file, with a declared divergence.
#:
#: It exists so the map is not copied. :func:`atlas.scenarios.territory.load_territory`
#: keys on the scenario id, so a derived era would otherwise need a byte-identical
#: duplicate of its base's territory file -- two files to keep in step, and a silent
#: country-granularity build the day they drift.
DERIVED_FROM: dict[str, str] = {
    "hot-cold-war": "cold-war",
}


def builtin_scenario_for(preset_id: str | None) -> Scenario | None:
    """The shipped scenario behind ``preset_id``, ignoring anything authored.

    Split out so the workshop can build on a base without recursing through the authored
    lookup that is about to call it.
    """
    module = SCENARIOS.get(preset_id or "")
    if module is None:
        return None
    return import_module(module).SCENARIO


def scenario_for(preset_id: str | None) -> Scenario | None:
    """The designed scenario behind ``preset_id``, or ``None`` for a generic world.

    A module named here that will not import is a packaging fault rather than a missing
    scenario, so it is left to raise. Silently falling back to the generic seeder would
    hand the player an unrecognisable world and no reason why.
    """
    # **Authored first.** A clone resolves to its edited scenario everywhere a shipped one
    # resolves to its module, which is what lets everything downstream -- the seeder, the
    # auditor, the turn engine -- take it without knowing it was authored.
    from .workshop import authored_scenario

    authored = authored_scenario(preset_id)
    if authored is not None:
        return authored
    return builtin_scenario_for(preset_id)


def preset_seed(
    preset: Preset | None,
    pack: GeometryPack | PackSet,
    *,
    world_id: str,
    player: str | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    seed: int | None = None,
) -> WorldSeed:
    """The starting world for ``preset``, designed if there is one and generic if not.

    The one place that decides. ``player`` means whichever the scenario understands: a
    power id (``ger``) for a designed scenario, a geo key (``admin0-50m:FRA``) for a
    generic one -- the caller does not have to know which, because the setup screen
    offers exactly what the scenario named.

    ``preset`` may be ``None``: that is ``atlas map world --preset earth``, the raw pack
    with no scenario and no era behind it.
    """
    from .earth import earth_seed

    scenario = scenario_for(preset.id if preset else None)
    if scenario is not None:
        return build_world(
            scenario, pack, world_id=world_id, player=player,
            difficulty=difficulty, seed=seed,
        )

    extra = (
        {
            "name": preset.name,
            "start_date": preset.start_date,
            "preset_id": preset.id,
            "seed": preset.seed if seed is None else seed,
        }
        if preset is not None
        else ({} if seed is None else {"seed": seed})
    )
    return earth_seed(
        pack, world_id=world_id, player_geo_key=player, difficulty=difficulty, **extra
    )


def preset_politics(
    conn: sqlite3.Connection, world_id: str, preset: Preset | None
) -> dict[str, int] | None:
    """Lay ``preset``'s starting relations, treaties and history over a created world.

    ``None`` when the preset has no designed scenario -- a generic world begins with
    every pair neutral and no past, which is honest rather than broken: there is nothing
    to say about who resents whom on a map that was never written.
    """
    scenario = scenario_for(preset.id if preset else None)
    if scenario is None:
        return None
    return apply_politics(conn, world_id, scenario)
