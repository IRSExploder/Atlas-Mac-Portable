"""Lon/lat to plane. Applied once at load, never per frame.

Projection is deliberately an interface with more than one implementation, because
committing to equirectangular everywhere would bake a world-shape assumption into the
renderer. A fictional flat world wants plate carree; an Earth scenario often wants
Mercator; a future globe view wants neither. The renderer only ever asks for
``project()``, so swapping is a one-line change in the scene builder.

Screen conventions: ``x`` grows east, ``y`` grows **down** (Qt's convention), so latitude
is negated. Output is in projection units; the view applies zoom and pan on top via
``QGraphicsView`` transforms, which is why nothing here knows about pixels.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "Equirectangular",
    "Mercator",
    "Projection",
    "PROJECTIONS",
    "get_projection",
]


@runtime_checkable
class Projection(Protocol):
    #: Stable identifier. Forms part of the renderer's path-cache key, so a projection
    #: change invalidates cached geometry without any explicit cache flush.
    key: str

    def project(self, lon: float, lat: float) -> tuple[float, float]: ...

    def invert(self, x: float, y: float) -> tuple[float, float]: ...

    def project_ring(self, ring: array) -> array:
        """Project a flat lon/lat run. Bulk-transforms are worth a dedicated method."""
        ...


@dataclass(frozen=True, slots=True)
class Equirectangular:
    """Plate carree: longitude and latitude map straight to x and y.

    Distorts area badly toward the poles but is exact, cheap, invertible, and never
    surprises anyone. The right default.
    """

    key: str = "equirect"
    scale: float = 1.0

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        return lon * self.scale, -lat * self.scale

    def invert(self, x: float, y: float) -> tuple[float, float]:
        return x / self.scale, -y / self.scale

    def project_ring(self, ring: array) -> array:
        out = array("d", ring)
        scale = self.scale
        for i in range(0, len(out), 2):
            out[i] *= scale
            out[i + 1] *= -scale
        return out


@dataclass(frozen=True, slots=True)
class Mercator:
    """Web Mercator, clamped near the poles.

    Latitude is clamped rather than allowed to run to infinity at +/-90: Antarctica is a
    real polygon in the data, and an unclamped projection sends it to infinity, which
    turns one region into a scene bounding box the size of the universe and makes
    zoom-to-fit useless everywhere else.
    """

    key: str = "mercator"
    scale: float = 1.0
    max_latitude: float = 85.05112878

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        clamped = max(-self.max_latitude, min(self.max_latitude, lat))
        y = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(clamped) / 2)))
        return lon * self.scale, -y * self.scale

    def invert(self, x: float, y: float) -> tuple[float, float]:
        lon = x / self.scale
        lat = math.degrees(2 * math.atan(math.exp(math.radians(-y / self.scale))) - math.pi / 2)
        return lon, lat

    def project_ring(self, ring: array) -> array:
        out = array("d", ring)
        scale = self.scale
        limit = self.max_latitude
        quarter = math.pi / 4
        for i in range(0, len(out), 2):
            out[i] *= scale
            lat = out[i + 1]
            if lat > limit:
                lat = limit
            elif lat < -limit:
                lat = -limit
            out[i + 1] = -math.degrees(math.log(math.tan(quarter + math.radians(lat) / 2))) * scale
        return out


PROJECTIONS: dict[str, type] = {
    "equirect": Equirectangular,
    "mercator": Mercator,
}


def get_projection(key: str = "equirect", *, scale: float = 1.0) -> Projection:
    try:
        factory = PROJECTIONS[key]
    except KeyError:
        raise KeyError(f"unknown projection {key!r}; known: {sorted(PROJECTIONS)}") from None
    return factory(scale=scale)  # type: ignore[call-arg]
