"""A world seeded from real Natural Earth geometry.

Every country in the pack becomes a region owned by a nation of the same name. That is
not yet a designed scenario -- there are no alliances, no history, no starting tensions --
but it is a real political world map driven entirely by world state, which is what the
renderer needs in order to be exercised honestly.

**Adjacency is derived from the geometry, not hand-authored.** Two regions border each
other if their outlines share coordinates, which the topology-preserving bake guarantees
they do. That matters beyond convenience: rule OWN-001 refuses a territorial transfer to
a nation holding no adjacent land, so a wrong adjacency table would silently make
conquest either impossible or unbounded.
"""

from __future__ import annotations

import colorsys
import math
from hashlib import blake2b

from ..map.pack import GeometryPack, PackSet
from ..state.bootstrap import (
    CitySeed,
    NationSeed,
    RegionSeed,
    UnitSeed,
    WorldSeed,
)
from ..state.models import Difficulty
from .demography import assign_population, rescale_minor_economies
from .development import assign_development
from .seeder import (
    SEAGOING_UNIT_TEMPLATES,
    mark_coasts,
    naval_adjacency,
    region_adjacency,
    sea_regions,
)
from .territory import SEA_SOURCE

__all__ = ["derive_adjacency", "earth_seed"]


def _colour_for(key: str) -> str:
    """A stable, readable colour per nation.

    Hue comes from a hash so it is deterministic across runs, while saturation and
    lightness are held in a narrow band -- unconstrained random colours produce
    neighbouring countries that are indistinguishable, or text that cannot be read on
    top of them.
    """
    digest = blake2b(key.encode("utf-8"), digest_size=4).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    saturation = 0.42 + (digest[2] / 255.0) * 0.26
    lightness = 0.46 + (digest[3] / 255.0) * 0.18
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def derive_adjacency(pack: GeometryPack, *, lod: int = 0) -> tuple[tuple[str, str], ...]:
    """Find every pair of regions sharing at least two boundary coordinates.

    Built by indexing points to regions rather than comparing every pair: with a few
    hundred regions the pairwise version is tens of thousands of set intersections, while
    this is a single pass over the coordinates.
    """
    owners: dict[tuple[float, float], set[str]] = {}
    for entry in pack.entries:
        for rings in pack.geometry(entry.geo_key, lod):
            for ring in rings:
                for i in range(0, len(ring), 2):
                    owners.setdefault((ring[i], ring[i + 1]), set()).add(entry.geo_key)

    counts: dict[tuple[str, str], int] = {}
    for sharers in owners.values():
        if len(sharers) < 2:
            continue
        ordered = sorted(sharers)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                counts[(a, b)] = counts.get((a, b), 0) + 1

    # A single shared vertex is a corner touch, not a border.
    return tuple(pair for pair, count in sorted(counts.items()) if count >= 2)


#: How coarse the grid is when matching a coastline to the water beside it, in degrees.
#: Roughly 25km at European latitudes: fine enough that the North Sea does not reach
#: Bavaria, coarse enough to bridge the gap between two datasets that were digitised
#: separately and do not agree to the metre.
_COAST_CELL = 0.25

#: How near the water a **sub-cell** land shape has to actually be, in kilometres.
#:
#: A grid cell is about 25km across, so for a shape smaller than that a same-cell match
#: proves nothing at all: the tolerance is wider than the country. San Marino is 0.1 deg
#: across, entirely surrounded by Italy, and landed in a cell the Adriatic also touches --
#: **14.9km away** -- so the engine gave a landlocked republic a coastline, a navy it could
#: build, and a sea an invader could land across.
#:
#: **12km is measured, not chosen.** These are every sub-cell shape either seeded era
#: actually contains, by true distance to the nearest sea vertex:
#:
#: ======  ============  =========================================
#:   4.6km Monaco        on the water; a real principality-sized coast
#:   7.0km Taipei City   on the Tamsui estuary
#:   8.1km Kinmen        an island in the Taiwan Strait
#:  14.9km San Marino    **landlocked, surrounded entirely by Italy**
#: ======  ============  =========================================
#:
#: So the threshold has to sit between 8.1 and 14.9, and the gap is wide enough that no
#: value in it is a judgement call. Below 8.1 it would take the coast off an island; above
#: 14.9 it would do nothing at all.
#:
#: **Applied only to sub-cell shapes**, which is the whole of why this is safe. The
#: province pack has ~1,400 ordinary coastal regions whose grid match is untouched, and its
#: sea-versus-land vertices disagree by up to 27km in places -- a global distance test at
#: any useful threshold would strip the coast from real islands. Restricting it to shapes
#: smaller than the tolerance is what separates "the test could not have meant anything"
#: from "the two datasets disagree here".
_SMALL_SHAPE_KM = 12.0


def derive_coast_adjacency(
    sea_pack: GeometryPack,
    land_pack: GeometryPack,
    *,
    lod: int = 0,
    cell: float = _COAST_CELL,
) -> tuple[tuple[str, str], ...]:
    """Find which stretches of water touch which stretches of coast.

    Shared vertices cannot answer this. Land and sea come from different Natural Earth
    layers, digitised independently, so a coastline appears twice with coordinates that
    are close and never equal -- and the exact-match test that finds land borders finds
    nothing at all here.

    So the test is proximity rather than identity: both sides are snapped onto a grid and
    a sea meets a coast where they land in the same cell. The same index-points-to-regions
    pass as :func:`derive_adjacency`, one grid rounding wider.

    **Full detail on both sides, not the simplified outline.** A simplification that moves
    a coastline by a few kilometres is invisible on screen and decisive here: at level 1
    it straightened Israel's hundred-mile Mediterranean shore out of existence, along with
    three other countries' and 288 provinces', and a country the game thinks is landlocked
    silently loses its entire navy. Reading level 0 costs about a third of a second on the
    province pack, once, at world creation.

    Used **only** for sea-to-land. Land borders stay on exact vertex sharing, where a grid
    this coarse would put England next to France across the Channel.

    **And a shape smaller than the grid gets its distance checked for real.** See
    :data:`_SMALL_SHAPE_KM`: for a country 0.1 degrees across, landing in the same 0.25
    degree cell as the sea says nothing, and San Marino was coastal on the strength of an
    Adriatic vertex 14.9km away.
    """
    cells: dict[tuple[int, int], set[str]] = {}
    sea_points: dict[str, dict[tuple[int, int], list[tuple[float, float]]]] = {}
    for entry in sea_pack.entries:
        for rings in sea_pack.geometry(entry.geo_key, lod):
            for ring in rings:
                for i in range(0, len(ring), 2):
                    key = (int(ring[i] / cell), int(ring[i + 1] / cell))
                    cells.setdefault(key, set()).add(entry.geo_key)
                    sea_points.setdefault(entry.geo_key, {}).setdefault(key, []).append(
                        (ring[i], ring[i + 1])
                    )

    found: set[tuple[str, str]] = set()
    #: Only for the shapes the check applies to, so the ordinary case pays nothing.
    small_points: dict[str, dict[tuple[int, int], list[tuple[float, float]]]] = {}
    for entry in land_pack.entries:
        outline = [
            (ring[i], ring[i + 1])
            for rings in land_pack.geometry(entry.geo_key, lod)
            for ring in rings
            for i in range(0, len(ring), 2)
        ]
        if not outline:
            continue
        small = _spans_less_than(outline, cell)
        for point in outline:
            key = (int(point[0] / cell), int(point[1] / cell))
            if small:
                small_points.setdefault(entry.geo_key, {}).setdefault(key, []).append(point)
            for sea in cells.get(key, ()):  # noqa: PERF401 - set of pairs, not a list
                found.add((sea, entry.geo_key))

    return tuple(sorted(
        pair for pair in found
        if pair[1] not in small_points
        or _within_km(sea_points.get(pair[0], {}), small_points[pair[1]], _SMALL_SHAPE_KM)
    ))


def _spans_less_than(outline: list[tuple[float, float]], degrees: float) -> bool:
    """Whether the whole shape fits inside one grid cell's worth of degrees."""
    longitudes = [point[0] for point in outline]
    latitudes = [point[1] for point in outline]
    return max(
        max(longitudes) - min(longitudes), max(latitudes) - min(latitudes)
    ) < degrees


def _km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Rough great-circle distance, with the same latitude correction `_land_area` uses.

    Equirectangular rather than haversine: the distances being judged are single-digit
    kilometres and the error at that scale is metres, which is well inside the tolerance
    this is compared against.
    """
    mid = math.radians((a[1] + b[1]) / 2.0)
    return math.hypot((b[0] - a[0]) * 111.32 * math.cos(mid), (b[1] - a[1]) * 110.57)


def _within_km(
    sea_cells: dict[tuple[int, int], list[tuple[float, float]]],
    land_cells: dict[tuple[int, int], list[tuple[float, float]]],
    limit: float,
) -> bool:
    """Whether any vertex of the two shapes is genuinely within ``limit`` kilometres.

    Compared inside the cells they share, which is the only place the grid claimed they
    met -- so this rules a pairing out, never in, and costs a handful of comparisons.
    """
    for key in sea_cells.keys() & land_cells.keys():
        for a in sea_cells[key]:
            for b in land_cells[key]:
                if _km_between(a, b) <= limit:
                    return True
    return False


def _land_area(pack: GeometryPack | PackSet, geo_key: str) -> float:
    """Approximate land area, used as a rough proxy for national scale.

    A bounding box will not do. It spans every part of a multi-part country, so the
    Netherlands reads as larger than China on the strength of its Caribbean territories;
    it spans the globe for anything crossing the antimeridian, which made Fiji the
    strongest power on Earth; and it ignores that a degree of longitude shrinks toward the
    poles, which turned Greenland into a superpower. Summing the actual polygons, with a
    cosine correction for latitude, avoids all three.

    A designed scenario sets these figures deliberately. This only has to be plausible.
    """
    total = 0.0
    try:
        parts = pack.geometry(geo_key, 0)
    except KeyError:
        return 0.05

    for rings in parts:
        if not rings:
            continue
        exterior = rings[0]
        if len(exterior) < 8:
            continue
        # Shoelace, with each vertex's longitude scaled by cos(latitude) so the area is
        # in comparable units rather than raw square degrees.
        area = 0.0
        for i in range(0, len(exterior) - 2, 2):
            x1 = exterior[i] * math.cos(math.radians(exterior[i + 1]))
            y1 = exterior[i + 1]
            x2 = exterior[i + 2] * math.cos(math.radians(exterior[i + 3]))
            y2 = exterior[i + 3]
            area += x1 * y2 - x2 * y1
        total += abs(area) / 2.0

    return max(0.05, total)


def earth_seed(
    pack: GeometryPack | PackSet,
    *,
    world_id: str = "earth",
    name: str = "Earth",
    player_geo_key: str | None = None,
    seed: int = 1936,
    difficulty: Difficulty = Difficulty.NORMAL,
    start_date: str = "1936-01-01",
    preset_id: str | None = None,
) -> WorldSeed:
    """Build a world where each country in ``pack`` is a nation holding its own region.

    **Water is not a country.** The pack a scenario is read through carries the marine
    layer alongside the land, and a loop that treats every entry as a nation hands the
    North Atlantic a government, a capital city and an army. So the marine shapes are
    routed to :func:`~atlas.scenarios.seeder.sea_regions` instead, exactly as the 1936
    scenario routes them: unowned ``kind='ocean'`` regions that a fleet sails and nobody
    is born in.

    **``preset_id`` is the scenario's id, not the pack's.** It is the key the referee
    loads a preset's lore and hard constraints by, so a world that records
    ``admin0-50m+marine-10m`` there is a world whose era rules never reach the
    simulation -- the modern scenario would be played with no nuclear taboo in force.
    Callers that have a preset must pass its id; the fallback is the pack, which is what
    a bare ``atlas map world`` has to go on.
    """
    nations: list[NationSeed] = []
    regions: list[RegionSeed] = []
    cities: list[CitySeed] = []
    units: list[UnitSeed] = []

    for entry in pack.entries:
        geo_key = entry.geo_key
        if geo_key.startswith(f"{SEA_SOURCE}:"):
            continue  # water is added below, owned by nobody
        nation_id = f"n_{geo_key.split(':', 1)[-1]}".lower().replace(" ", "_")
        region_id = f"r_{geo_key.split(':', 1)[-1]}".lower().replace(" ", "_")
        colour = _colour_for(geo_key)

        extent = _land_area(pack, geo_key)

        regions.append(
            RegionSeed(
                id=region_id,
                name=entry.name,
                geo_key=geo_key,
                owner_nation_id=nation_id,
                kind="land",
                population=round(min(120.0, extent * 0.4), 2),
            )
        )
        cities.append(
            CitySeed(
                id=f"c_{region_id}",
                region_id=region_id,
                name=f"{entry.name} (capital)",
                tier=3,
            )
        )
        nations.append(
            NationSeed(
                id=nation_id,
                name=entry.name,
                colour=colour,
                capital_region_id=region_id,
                is_player=(geo_key == player_geo_key),
                personality_blurb=f"{entry.name} pursues its own interests.",
                military_strength=round(min(60.0, 5.0 + extent * 0.05), 1),
                economy=round(min(60.0, 5.0 + extent * 0.05), 1),
            )
        )

        # A standing army, scaled to the country. Without units the order parser tells
        # the player they command no forces and refuses every military order they write,
        # which reads as the whole game being broken.
        strength = min(60.0, 5.0 + extent * 0.05)
        for index in range(max(1, round(strength / 12))):
            units.append(
                UnitSeed(
                    id=f"u_{nation_id}_inf{index + 1}",
                    nation_id=nation_id,
                    region_id=region_id,
                    kind="infantry",
                    strength=round(3.0 + strength / 20.0, 2),
                    morale=50.0,
                )
            )

    # Water last, so nothing above can claim it, garrison it or turn it into a nation.
    regions.extend(sea_regions(pack))

    adjacency: tuple[tuple[str, ...], ...] = (
        region_adjacency(pack, regions, None) + naval_adjacency(pack, regions)
    )
    regions = mark_coasts(regions, adjacency)

    # Substance before shape: real minor economies, then development distributed from
    # them, then population by the same weights. See `atlas.scenarios.demography`.
    nations = rescale_minor_economies(nations)
    regions = assign_development(regions, nations)
    regions = assign_population(regions, nations)

    return WorldSeed(
        id=world_id,
        name=name,
        start_date=start_date,
        rng_seed=seed,
        difficulty=difficulty,
        nations=tuple(nations),
        regions=tuple(regions),
        adjacency=adjacency,
        cities=tuple(cities),
        unit_templates=SEAGOING_UNIT_TEMPLATES,
        units=tuple(units),
        preset_id=preset_id or pack.pack_id,
    )
