"""Which pack shapes compose which region, for which power, in which era.

Generated mechanically and then corrected by hand, because neither half works alone.

**The mechanical half.** Natural Earth tags every province with the modern country that
holds it today (``adm0_a3``). A period country is mostly a union of modern ones -- 1936
Czechoslovakia is the Czech Republic plus Slovakia -- so the bulk of a scenario's map
falls out of a small table without anyone drawing anything.

**The hand-corrected half.** Two things the mechanical pass cannot know. Borders moved
*within* what is now one country: East Prussia and Danzig are Polish provinces today and
were not German and Free City respectively by accident. And Natural Earth mixes
administrative levels between countries -- Germany's 16 shapes are federal states,
Slovenia's 193 are municipalities -- so one region per shape would give Yugoslavia 392
regions and Germany 16, and overrunning Yugoslavia would take twenty-four times longer
for reasons having nothing to do with either army.

So a territory file groups shapes into named regions, and grouping is where both
corrections live: a group is a region, and a shape may be moved between groups. Every
power lands in a comparable band of regions, chosen deliberately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "GROUP_TARGET",
    "TerritoryMap",
    "load_territory",
    "pack_paths",
    "territory_path",
]

#: Roughly how many regions a major power should hold. Not a hard cap -- a scenario may
#: want more for the country a war is fought across -- but the number the generator aims
#: at, so no power is accidentally twenty times harder to conquer than its neighbour.
GROUP_TARGET = 12

#: The pack holding the water. Optional like the province pack: a world baked without it
#: is all land, which is what every world was before there were ships.
SEA_SOURCE = "marine-10m"


@dataclass(frozen=True, slots=True)
class TerritoryMap:
    """A scenario's regions, as ``{nation_id: {region_name: [geo_key, ...]}}``."""

    scenario_id: str
    source: str
    nations: dict[str, dict[str, tuple[str, ...]]]
    #: Modern country codes this file partitions completely. A country listed here is
    #: drawn at province granularity, so a scenario must not also give it a country-level
    #: region -- that would be the same ground owned twice.
    countries: frozenset[str] = frozenset()
    #: Which region each nation governs from. A promoted power's capital cannot be
    #: inferred from its country code any more, because it no longer has one region.
    capitals: dict[str, str] = field(default_factory=dict)

    def region_count(self, nation_id: str) -> int:
        return len(self.nations.get(nation_id, {}))

    def shapes(self, nation_id: str) -> int:
        return sum(len(keys) for keys in self.nations.get(nation_id, {}).values())

    def geo_keys(self) -> frozenset[str]:
        """Every pack shape this file names, across all nations."""
        return frozenset(
            key
            for groups in self.nations.values()
            for keys in groups.values()
            for key in keys
        )

    def balance(self) -> dict[str, int]:
        """Regions per nation, for asserting no power is wildly out of band."""
        return {nation: len(groups) for nation, groups in self.nations.items()}


def territory_path(scenario_id: str) -> Path:
    return Path(__file__).parent / "territory" / f"{scenario_id}.json"


def pack_paths(scenario_id: str, map_source: str) -> list[Path]:
    """Every pack a scenario should be read through, finest first.

    Finest first is load-bearing rather than tidy. A :class:`~atlas.map.pack.PackSet`
    resolves a key through the first pack that holds it, so a scenario promoted to
    provinces has to meet the province pack before the country one.

    A scenario with no territory file, or one whose finer pack has not been baked, gets
    its country pack alone and plays exactly as it did before -- which is also what makes
    the province pack an optional download rather than a hard requirement.
    """
    from ..config.paths import pack_file

    paths = [pack_file(map_source)]
    territory = load_territory(scenario_id)
    if territory is not None and territory.source != map_source:
        finer = pack_file(territory.source)
        if finer.exists():
            paths.insert(0, finer)
    water = pack_file(SEA_SOURCE)
    if water.exists():
        # Last: sea keys never collide with land keys, so where it sits in the order
        # changes nothing, and appending keeps the land resolution order obvious.
        paths.append(water)
    return paths


def load_territory(scenario_id: str) -> TerritoryMap | None:
    """Read a scenario's territory file, or ``None`` if it has none.

    Absent is not an error: a scenario without one is played at country granularity,
    which is what every scenario did before provinces existed.
    """
    path = territory_path(scenario_id)
    if not path.exists():
        # **A clone inherits its base's map.** An authored scenario has no territory file
        # of its own -- it is an overlay of edits on somebody else's -- and without this it
        # would be built at country granularity while its *sovereignty* still names
        # provinces, which collides on `region.geo_key` and fails to build at all. Found by
        # cloning the Cold War and watching it refuse.
        from .registry import DERIVED_FROM
        from .workshop import base_of

        # A shipped counterfactual reads its base's map for the same reason a clone does:
        # it is an overlay of decisions on somebody else's board.
        #
        # **Authored first**, which is the order `registry.scenario_for` resolves in: a
        # clone saved under an id that a shipped derived era also uses is the user's own
        # scenario, and reading the shipped era's map for it would build the wrong board
        # out of nobody's mistake but this line's.
        base = base_of(scenario_id) or DERIVED_FROM.get(scenario_id)
        if not base:
            return None
        path = territory_path(base)
        if not path.exists():
            return None
    blob: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return TerritoryMap(
        scenario_id=blob["scenario"],
        source=blob["source"],
        nations={
            nation: {name: tuple(keys) for name, keys in entry["regions"].items()}
            for nation, entry in blob["nations"].items()
        },
        countries=frozenset(blob.get("countries") or ()),
        capitals={
            nation: entry["capital"]
            for nation, entry in blob["nations"].items()
            if entry.get("capital")
        },
    )
