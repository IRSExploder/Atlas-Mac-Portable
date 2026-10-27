"""Structural checks on a baked pack.

The claim worth testing is that simplification preserved topology. Asserting it in a
docstring is free and proves nothing; the check that bites is whether two regions which
shared a border at full detail *still* share the identical coordinates at every reduced
level. Independent per-polygon simplification fails that immediately, which is exactly
the failure that produces hairline gaps between countries at low zoom.

This runs on the pack alone, with no shapely, so it can be part of the ordinary test
suite rather than only of the bake environment.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from itertools import combinations

from .pack import BAKER_VERSION, GeometryPack

__all__ = ["VerifyReport", "verify_pack"]


@dataclass(slots=True)
class VerifyReport:
    pack_id: str
    regions: int
    lods: int
    problems: list[str] = field(default_factory=list)
    #: Expected, explained consequences of simplification rather than defects. Reported
    #: so they stay visible: a silent warning list is just a hidden failure list.
    warnings: list[str] = field(default_factory=list)
    shared_borders_checked: int = 0
    #: Regions whose geometry is empty at some LOD, i.e. simplified out of existence.
    vanished: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        lines = [
            f"pack {self.pack_id}: {self.regions} regions, {self.lods} LODs",
            f"shared borders checked: {self.shared_borders_checked}",
        ]
        if self.vanished:
            lines.append(
                f"VANISHED at some LOD ({len(self.vanished)}): {', '.join(self.vanished[:8])}"
            )
        if self.warnings:
            lines.append(f"expected simplification effects ({len(self.warnings)}):")
            lines.extend(f"  . {w}" for w in self.warnings[:12])
        if self.problems:
            lines.append(f"PROBLEMS ({len(self.problems)}):")
            lines.extend(f"  - {p}" for p in self.problems[:40])
        else:
            lines.append("all checks passed")
        return "\n".join(lines)


def _points(ring: array) -> set[tuple[float, float]]:
    return {(ring[i], ring[i + 1]) for i in range(0, len(ring), 2)}


def _region_points(pack: GeometryPack, geo_key: str, lod: int) -> set[tuple[float, float]]:
    out: set[tuple[float, float]] = set()
    for rings in pack.geometry(geo_key, lod):
        for ring in rings:
            out |= _points(ring)
    return out


def verify_pack(pack: GeometryPack, *, max_border_pairs: int = 400) -> VerifyReport:
    """Check bijection, ring closure, orientation and shared-border preservation."""
    report = VerifyReport(pack_id=pack.pack_id, regions=len(pack), lods=len(pack.lods))
    lod_count = len(pack.lods)

    # 0. **Is this pack even current?** First, because every check below it is a check on
    # geometry, and geometry produced by an old baker is not evidence about this tree. A
    # stale pack reporting a shared-border failure reads exactly like a regression in the
    # simplifier, and the difference is a re-bake rather than a debugging session.
    if pack.baked_by < BAKER_VERSION:
        report.problems.append(
            f"baked by version {pack.baked_by}, current is {BAKER_VERSION}; this pack "
            f"predates a geometry fix and its contents are not evidence about this tree. "
            f"Re-bake it: atlas map bake --source {pack.pack_id}")
        return report

    # 1. Every region resolves at every LOD, every ring is closed and has enough points.
    for entry in pack.entries:
        if len(entry.offsets) != lod_count:
            report.problems.append(f"{entry.geo_key}: has {len(entry.offsets)} LODs, expected {lod_count}")
            continue
        for lod in range(lod_count):
            parts = pack.geometry(entry.geo_key, lod)
            if not parts:
                report.vanished.append(f"{entry.geo_key}@{lod}")
                continue
            for part_index, rings in enumerate(parts):
                for ring_index, ring in enumerate(rings):
                    where = f"{entry.geo_key}@{lod} part{part_index} ring{ring_index}"
                    if len(ring) < 8:
                        report.problems.append(f"{where}: only {len(ring) // 2} points, cannot be a polygon")
                        continue
                    if (ring[0], ring[1]) != (ring[-2], ring[-1]):
                        report.problems.append(f"{where}: ring is not closed")
                    if _signed_area(ring) == 0.0:
                        report.problems.append(f"{where}: zero area")

    # 2. Orientation: exterior counter-clockwise, holes clockwise.
    for entry in pack.entries:
        for lod in range(lod_count):
            for part_index, rings in enumerate(pack.geometry(entry.geo_key, lod)):
                for ring_index, ring in enumerate(rings):
                    if len(ring) < 8:
                        continue
                    area = _signed_area(ring)
                    if ring_index == 0 and area < 0:
                        report.problems.append(
                            f"{entry.geo_key}@{lod} part{part_index}: exterior is clockwise; "
                            "under a winding fill this inverts the shape"
                        )
                    elif ring_index > 0 and area > 0:
                        report.problems.append(
                            f"{entry.geo_key}@{lod} part{part_index} hole{ring_index}: "
                            "hole is counter-clockwise and will not be subtracted"
                        )

    # 3. The real test: borders shared at full detail survive simplification.
    keys = [entry.geo_key for entry in pack.entries]
    full = {key: _region_points(pack, key, 0) for key in keys}

    neighbours: list[tuple[str, str, int]] = []
    for a, b in combinations(keys, 2):
        shared = len(full[a] & full[b])
        if shared >= 2:
            neighbours.append((a, b, shared))
    neighbours.sort(key=lambda item: -item[2])
    neighbours = neighbours[:max_border_pairs]

    for lod in range(1, lod_count):
        tolerance = pack.lods[lod]
        reduced = {key: _region_points(pack, key, lod) for key in keys}
        for a, b, _ in neighbours:
            report.shared_borders_checked += 1
            if reduced[a] & reduced[b]:
                continue

            # **A part smaller than the tolerance cannot keep its border, and that is
            # simplification rather than a topology failure.** What was measured before was
            # the whole *region's* bounding box, which is the wrong box for a multipart
            # region: the Drake Passage is seven scraps of water strewn across eight degrees
            # of ocean, none of them wider than 0.064 deg, and its bbox therefore reported
            # 8.428 -- "a large region", when nothing in it is larger than a rounding error.
            # It was failed for losing borders whose entire geometry was fifty times below
            # the tolerance that erased them.
            #
            # So the question is asked of the parts that actually vanished and actually
            # carried the border. The slack is gone with the proxy: the old rule needed
            # `tolerance * 2` because a bounding box overstates a diagonal coastline, and
            # measuring the real part needs no such allowance -- Hong Kong's vanished part
            # is 0.269 deg against the 0.35 that erased it, comfortably inside, where its
            # bbox said 0.370 and had to be forgiven.
            lost = _largest_vanished_part(pack, a, b, lod, full[a] & full[b])
            if lost is not None and lost <= tolerance:
                report.warnings.append(
                    f"LOD {lod}: {a}/{b} border not preserved; the largest part that "
                    f"carried it spans {lost:.3f} deg against a {tolerance} deg tolerance"
                )
            else:
                report.problems.append(
                    f"LOD {lod}: {a} and {b} shared a border at full detail but share no "
                    "coordinates after simplification -- a gap has opened between them"
                )

    return report


def _smallest_extent(pack: GeometryPack, geo_key: str) -> float:
    """The smaller dimension of a region's whole bounding box.

    Kept because the bake still reports it, and **no longer used to excuse a lost border**:
    for a multipart region it describes the scatter rather than the geometry. See
    :func:`_largest_vanished_part`.
    """
    minx, miny, maxx, maxy = pack.entry(geo_key).bbox
    return min(maxx - minx, maxy - miny)


def _part_extent(ring: array) -> float:
    """The smaller dimension of one ring's own bounding box."""
    xs = ring[0::2]
    ys = ring[1::2]
    return min(max(xs) - min(xs), max(ys) - min(ys))


def _largest_vanished_part(
    pack: GeometryPack, a: str, b: str, lod: int, shared: set[tuple[float, float]]
) -> float | None:
    """The biggest part that carried this shared border and did not survive simplification.

    ``None`` when nothing that carried the border vanished -- which means the border was
    lost some other way, and an excuse would be guessing.

    Only ever called for a pair that has already lost its border, which is a handful of
    pairs across every pack, so it can afford to re-read the geometry.
    """
    worst: float | None = None
    for key in (a, b):
        survivors = _region_points(pack, key, lod)
        for rings in pack.geometry(key, 0):
            for ring in rings:
                points = _points(ring)
                if not (points & shared):
                    continue          # this part never carried the border
                if points & survivors:
                    continue          # ...and this one is still here
                extent = _part_extent(ring)
                if worst is None or extent > worst:
                    worst = extent
    return worst


def _signed_area(ring: array) -> float:
    """Shoelace formula over a flat lon/lat run. Positive means counter-clockwise."""
    total = 0.0
    count = len(ring)
    for i in range(0, count - 2, 2):
        x1, y1 = ring[i], ring[i + 1]
        x2, y2 = ring[i + 2], ring[i + 3]
        total += x1 * y2 - x2 * y1
    return total / 2.0
