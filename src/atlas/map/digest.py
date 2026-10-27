"""Hashing a scene description, so "the map did not change" is a testable claim.

Used for comparing a scene against *another scene in the same run*: render twice from the
same state and assert the digests match; render A, then B, then A again and assert the
first and third agree. That last one is the anti-drift check -- it is what would catch a
renderer that had started accumulating state between frames.

Deliberately not a golden value checked into the repository. A frozen hash over a large
scene churns on every cosmetic change until regenerating it becomes reflex, and a
reflexively-regenerated assertion has stopped testing anything.
"""

from __future__ import annotations

from dataclasses import fields
from hashlib import blake2b

from .scene_model import MarkerVisual, RegionVisual, SceneDescription

__all__ = ["scene_bytes", "scene_digest"]

#: Captured once. Field *names* go into the hash alongside the values, so adding a field
#: changes the digest rather than silently colliding with the previous layout.
_REGION_FIELDS = tuple(f.name for f in fields(RegionVisual))
_MARKER_FIELDS = tuple(f.name for f in fields(MarkerVisual))


def scene_bytes(scene: SceneDescription) -> bytes:
    """A canonical serialisation of everything visible in a scene."""
    out = bytearray()

    def emit(*values: object) -> None:
        out.extend("\x1f".join(str(value) for value in values).encode("utf-8"))
        out.append(0x1E)

    emit(
        "scene",
        scene.pack_id,
        scene.projection_key,
        scene.layer,
        scene.unit_domains,
        scene.lod,
        scene.turn,
        scene.observer_nation_id,
        scene.background,
    )

    emit("regions", len(scene.regions), *_REGION_FIELDS)
    for region in scene.regions:
        emit(*(getattr(region, name) for name in _REGION_FIELDS))

    emit("markers", len(scene.markers), *_MARKER_FIELDS)
    for marker in scene.markers:
        emit(*(getattr(marker, name) for name in _MARKER_FIELDS))

    return bytes(out)


def scene_digest(scene: SceneDescription) -> str:
    return blake2b(scene_bytes(scene), digest_size=16).hexdigest()
