"""Turning Natural Earth GeoJSON into a baked geometry pack. Offline, one-time.

Three things here are easy to get wrong, and each produces a map that looks *almost*
right -- which is worse than one that looks obviously broken.

**1. Simplification must be topological.** Running a line simplifier over each region
independently guarantees gaps and overlaps along every shared border, because the same
frontier gets simplified twice with different neighbouring context. The fix is to extract
shared arcs first, simplify the *arcs*, then rebuild the rings from them, so a border is
reduced once and both neighbours receive the identical result. That is what TopoJSON is
for, and it is why this module takes a heavy dependency the runtime does not have.

**2. Ring orientation must be repaired.** Natural Earth contains rings in inconsistent
winding order, and simplification can introduce self-intersections. Under an odd-even
fill rule any overlap renders as a hole, so shapes acquire phantom gaps. Orientation is
normalised here (exteriors counter-clockwise, holes clockwise) and the renderer uses a
winding fill.

**3. Polygons crossing the antimeridian must be split.** A shape spanning +180/-180 --
Russia, Fiji, the Aleutians -- otherwise renders as a band straight across the whole map,
because the projection has no way to know the shape went the short way round.

**And one thing that is easy to get wrong and produces no visible symptom at all: a bake
must be a pure function of its source.** Each bake used to write a wall-clock ``baked_at``
into the pack header. The geometry was identical run to run, but the bytes were not, and
``packs.lock.json`` pins a sha256 of the whole file -- so the pin could never be met by a
re-bake, and the repair it prints (``atlas map bake``) could never repair anything. Nothing
time-, host- or path-dependent goes into the artefact. What the header records instead is
the sha256 of the GeoJSON it was baked from, which answers the question the timestamp was
standing in for -- *which input made this?* -- and answers it the same way on every machine.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import packs_root
from .pack import RegionEntry, encode_geometry, write_pack
from .sources import SOURCES, NaturalEarthSource, fetch, sha256_of

__all__ = ["DEFAULT_LODS", "BakeReport", "bake"]

#: Simplification tolerance in degrees, one per level of detail. LOD 0 is unsimplified.
#: The renderer picks a level from the current zoom, so these are "full", "mid" and
#: "far" rather than arbitrary steps.
DEFAULT_LODS: tuple[float, ...] = (0.0, 0.08, 0.35)

#: An edge jumping further than this in longitude cannot be a real edge -- no country has
#: a coastline segment half the globe wide -- so it must be a ring crossing the
#: antimeridian and wrapping around.
#:
#: Testing individual *edges* rather than a shape's total width matters. A bounding-box
#: test flags Antarctica, which genuinely spans every longitude, and Russia, whose
#: eastern islands are separate polygons that individually cross nothing. Shifting either
#: of those produces self-intersecting nonsense that GEOS rejects outright.
_WRAP_EDGE = 180.0


@dataclass(slots=True)
class BakeReport:
    pack_path: Path
    pack_id: str
    source_id: str
    regions: int
    lods: tuple[float, ...]
    points_per_lod: tuple[int, ...]
    split_at_antimeridian: int
    reoriented: int
    floored: int
    #: Parts above the tolerance that simplification dropped and the bake put back.
    restored: int
    duration_s: float

    def describe(self) -> str:
        lines = [
            f"pack        {self.pack_path}",
            f"source      {self.source_id}",
            f"regions     {self.regions}",
        ]
        base = self.points_per_lod[0] or 1
        for index, (tolerance, points) in enumerate(zip(self.lods, self.points_per_lod, strict=True)):
            share = 100.0 * points / base
            lines.append(
                f"  LOD {index}     tolerance {tolerance:<5} {points:>9,} points  ({share:5.1f}% of full)"
            )
        lines.append(f"antimeridian splits applied to {self.split_at_antimeridian} shape(s)")
        lines.append(f"rings reoriented              {self.reoriented}")
        if self.floored:
            lines.append(
                f"kept finer geometry for      {self.floored} region-LOD(s) that would have vanished"
            )
        if self.restored:
            lines.append(
                f"restored                     {self.restored} part(s) above tolerance that "
                "simplification had dropped"
            )
        lines.append(f"baked in {self.duration_s:.1f}s")
        return "\n".join(lines)


def _shift_east(coords: Sequence[Sequence[float]]) -> list[list[float]]:
    """Move negative longitudes into 180..360 so a wrapping ring becomes continuous."""
    return [[x + 360.0 if x < 0 else x, y] for x, y in coords]


def _wraps(ring_coords) -> bool:
    """True if any single edge jumps more than half the globe in longitude."""
    coords = list(ring_coords)
    return any(
        abs(coords[i][0] - coords[i + 1][0]) > _WRAP_EDGE for i in range(len(coords) - 1)
    )


def _split_antimeridian(geom, splits: list[int]):
    """Cut any polygon that wraps the antimeridian into pieces either side of it.

    Each polygon is judged and treated on its own. A multipolygon whose parts each stay
    on one side -- Russia's mainland and its eastern islands, say -- is left untouched.
    """
    from shapely import affinity, make_valid
    from shapely.geometry import MultiPolygon, Polygon, box
    from shapely.ops import unary_union

    polygons = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    result: list = []
    changed = False

    for polygon in polygons:
        if not isinstance(polygon, Polygon) or polygon.is_empty:
            continue
        if not _wraps(polygon.exterior.coords):
            result.append(polygon)
            continue

        shifted = make_valid(
            Polygon(
                _shift_east(polygon.exterior.coords),
                [_shift_east(ring.coords) for ring in polygon.interiors],
            )
        )
        left = shifted.intersection(box(-360.0, -90.0, 180.0, 90.0))
        right = shifted.intersection(box(180.0, -90.0, 360.0, 90.0))

        pieces = [piece for piece in (left, affinity.translate(right, xoff=-360.0)) if not piece.is_empty]
        if not pieces:
            result.append(polygon)
            continue

        changed = True
        for piece in pieces:
            geom_type = getattr(piece, "geom_type", "")
            if geom_type == "Polygon":
                result.append(piece)
            elif geom_type in {"MultiPolygon", "GeometryCollection"}:
                result.extend(p for p in piece.geoms if p.geom_type == "Polygon")

    if not result:
        return geom
    if changed:
        splits.append(1)
    return unary_union(result) if len(result) > 1 else result[0]


def _is_degenerate(parts: Sequence[Sequence[Sequence[float]]]) -> bool:
    """True if nothing survived that could be drawn as a filled shape.

    A ring needs at least four distinct points (eight values) to enclose any area.
    """
    return not any(len(rings) and len(rings[0]) >= 8 for rings in parts)


def _ring_box(ring: Sequence[float]) -> tuple[float, float, float, float]:
    xs = ring[0::2]
    ys = ring[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _boxes_meet(a, b, slack: float) -> bool:
    """Whether two bounding boxes come within ``slack`` of each other."""
    return not (a[2] + slack < b[0] or b[2] + slack < a[0]
                or a[3] + slack < b[1] or b[3] + slack < a[1])


def _restore_lost_parts(
    parts: list[list[list[float]]],
    previous: list[list[list[float]]] | None,
    tolerance: float,
    counters: dict[str, int],
) -> list[list[list[float]]]:
    """Put back any part **above the tolerance** that simplification dropped outright.

    **The region-level floor already above this did not reach a part.** `_is_degenerate`
    asks whether the *whole region* collapsed -- the Monaco case -- and keeps the finer
    geometry if so. A region that keeps two of its five parts is not degenerate, so the
    three that went are lost silently, and one of them can be well above the tolerance:
    the Strait of Gibraltar lost a 0.124 deg part to a 0.08 deg simplification while two
    smaller parts survived, and with it the border it shared with the North Atlantic.

    A thin sliver collapses to a line under Douglas-Peucker, shapely drops the empty
    polygon, and nothing downstream can tell it apart from a part that was never there.

    So: a part whose own smaller dimension exceeds the tolerance is not allowed to vanish.
    It is restored from the finer LOD -- the same geometry the region-level floor falls
    back to, and already in hand. A part **at or below** the tolerance still vanishes,
    which is what simplification is for and what `verify` now excuses by measuring the part
    rather than the region.

    Matching is by bounding box within one tolerance of slack, because a part that survived
    cannot have moved further than that: the whole contract of the simplifier is that no
    vertex moves more than the tolerance.
    """
    if not previous:
        return parts
    boxes = [_ring_box(rings[0]) for rings in parts if rings and rings[0]]
    restored = list(parts)
    for rings in previous:
        if not rings or not rings[0]:
            continue
        box = _ring_box(rings[0])
        if min(box[2] - box[0], box[3] - box[1]) <= tolerance:
            continue                                   # small enough to lose honestly
        if any(_boxes_meet(box, other, tolerance) for other in boxes):
            continue                                   # still here, merely simplified
        restored.append(rings)
        counters["restored"] += 1
    return restored


def _rings_of(geom, counters: dict[str, int]) -> list[list[list[float]]]:
    """Normalise a shapely polygon or multipolygon into oriented flat ring runs."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.geometry.polygon import orient

    polygons = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    parts: list[list[list[float]]] = []

    for polygon in polygons:
        if not isinstance(polygon, Polygon) or polygon.is_empty:
            continue
        # Signed-area repair: exterior counter-clockwise, holes clockwise.
        if not polygon.exterior.is_ccw:
            counters["reoriented"] += 1
        fixed = orient(polygon, sign=1.0)

        rings: list[list[float]] = []
        flat: list[float] = []
        for x, y in fixed.exterior.coords:
            flat.extend((x, y))
        rings.append(flat)
        for interior in fixed.interiors:
            hole: list[float] = []
            for x, y in interior.coords:
                hole.extend((x, y))
            rings.append(hole)
        parts.append(rings)

    return parts


def _simplified_layers(data: dict, lods: Sequence[float]) -> list[dict]:
    """Build one GeoJSON FeatureCollection per LOD, sharing arcs across regions."""
    import topojson as tp

    topology = tp.Topology(data, prequantize=False)
    layers = []
    for tolerance in lods:
        if tolerance <= 0:
            layers.append(data)
            continue
        simplified = topology.toposimplify(tolerance)
        layers.append(json.loads(simplified.to_geojson()))
    return layers


def _wanted(feature: dict, source: NaturalEarthSource) -> bool:
    """Whether a feature survives the source's own filters."""
    props = feature.get("properties", {})
    if source.max_scalerank is not None:
        try:
            rank = int(props.get("scalerank"))
        except (TypeError, ValueError):
            return False
        if rank > source.max_scalerank:
            return False
    return (
        source.keep_featurecla is None
        or str(props.get("featurecla") or "").lower() in source.keep_featurecla
    )


def bake(
    source_id: str = "admin0-50m",
    *,
    lods: Sequence[float] = DEFAULT_LODS,
    destination: Path | None = None,
    refresh: bool = False,
) -> BakeReport:
    """Download, simplify, repair and write a geometry pack."""
    from shapely.geometry import shape

    started = time.monotonic()
    source: NaturalEarthSource = SOURCES[source_id]
    geojson_path = fetch(source, refresh=refresh)
    source_sha256 = sha256_of(geojson_path)
    data = json.loads(geojson_path.read_text(encoding="utf-8"))

    if source.max_scalerank is not None or source.keep_featurecla is not None:
        # Filtered before simplification, not after: topology is extracted across whatever
        # is in the collection, so dropping features afterwards would leave the survivors
        # simplified against arcs shared with shapes that are no longer there.
        data = {**data, "features": [f for f in data["features"] if _wanted(f, source)]}

    layers = _simplified_layers(data, lods)

    counters = {"reoriented": 0, "floored": 0, "restored": 0}
    splits: list[int] = []
    points_per_lod = [0] * len(lods)

    # Index each LOD by geo_key. TopoJSON preserves feature order, but keying by
    # identity rather than position means a reordering upstream cannot silently pair a
    # region with another region's outline.
    def key_of(feature: dict, ordinal: int) -> str:
        raw = feature.get("properties", {}).get(source.key_property)
        if not raw or raw in {"-99", "-1"}:
            # Natural Earth uses -99 for disputed or unrecognised entities; fall back to
            # the name so those regions still get a stable, unique key.
            raw = feature.get("properties", {}).get(source.name_property) or f"f{ordinal}"
        return f"{source.id}:{raw}"

    indexed: list[dict[str, dict]] = []
    for layer in layers:
        table: dict[str, dict] = {}
        for ordinal, feature in enumerate(layer["features"]):
            key = key_of(feature, ordinal)
            suffix = 0
            unique = key
            while unique in table:
                suffix += 1
                unique = f"{key}#{suffix}"
            table[unique] = feature
        indexed.append(table)

    entries: list[tuple[RegionEntry, list[bytes]]] = []

    for geo_key, feature in indexed[0].items():
        blocks: list[bytes] = []
        label: tuple[float, float] | None = None
        bbox: tuple[float, float, float, float] | None = None
        previous_parts: list[list[list[float]]] | None = None

        for lod_index in range(len(lods)):
            source_feature = indexed[lod_index].get(geo_key, feature)
            geometry = shape(source_feature["geometry"])
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            geometry = _split_antimeridian(geometry, splits)

            parts = _rings_of(geometry, counters)

            # A microstate can be smaller than the simplification tolerance and vanish
            # outright -- Monaco, Andorra, Macau all disappear at LOD 2. A region that
            # exists in the world model but cannot be drawn is unclickable and
            # effectively deleted from the game, so keep the finer geometry instead.
            # These shapes are tiny by definition, so the cost is negligible.
            if _is_degenerate(parts) and previous_parts is not None:
                parts = previous_parts
                counters["floored"] += 1
            elif lod_index > 0:
                # ...and the same argument one level down, for a region that kept some of
                # its parts and quietly lost others. See `_restore_lost_parts`.
                parts = _restore_lost_parts(
                    parts, previous_parts, lods[lod_index], counters)

            previous_parts = parts
            points_per_lod[lod_index] += sum(len(ring) // 2 for rings in parts for ring in rings)
            blocks.append(encode_geometry(parts))

            if lod_index == 0:
                # point_on_surface is guaranteed inside the shape, unlike a centroid,
                # which for a crescent or an archipelago lands in the sea.
                point = geometry.point_on_surface()
                label = (point.x, point.y)
                bbox = tuple(geometry.bounds)  # type: ignore[assignment]

        name = feature.get("properties", {}).get(source.name_property) or geo_key
        entries.append(
            (
                RegionEntry(
                    geo_key=geo_key,
                    name=str(name),
                    label_point=label or (0.0, 0.0),
                    bbox=bbox or (0.0, 0.0, 0.0, 0.0),
                    offsets=(),
                ),
                blocks,
            )
        )

    pack_id = f"{source.id}"
    target = destination or (packs_root() / f"{pack_id}.pack")
    write_pack(
        target,
        pack_id=pack_id,
        source=source.url,
        lods=lods,
        entries=entries,
        extra={
            "key_property": source.key_property,
            "name_property": source.name_property,
            # Never a timestamp: see the module doc. Everything here must be a function
            # of the source and the baker, or the lock's whole-file pin cannot be met.
            "source_sha256": source_sha256,
        },
    )

    return BakeReport(
        pack_path=target,
        pack_id=pack_id,
        source_id=source.id,
        regions=len(entries),
        lods=tuple(lods),
        points_per_lod=tuple(points_per_lod),
        split_at_antimeridian=len(splits),
        reoriented=counters["reoriented"],
        floored=counters["floored"],
        restored=counters["restored"],
        duration_s=time.monotonic() - started,
    )
