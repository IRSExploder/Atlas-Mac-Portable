"""Copying a scene description onto a ``QGraphicsScene``. Mechanical by design.

The applier makes no decisions. It does not compare a visual against what the item
currently shows, does not decide whether a change is worth applying, and does not know
what a nation or a turn is. Every field named in the description is written on every
call.

Items *are* reused, keyed by id, because destroying and rebuilding a few thousand
``QGraphicsItem`` objects each turn is the thing that actually stutters. Reuse is safe
precisely because the apply is unconditional: a reused item cannot retain anything from
the previous frame, since every one of its properties is overwritten.

Items whose id is absent from the new description are hidden rather than deleted, and
crucially are also removed from the visible set. A stale item left visible was the
previous attempt's failure mode: a region that had been conquered kept its old colour
because nothing told it otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from ..map.scene_model import SceneDescription
from .map_items import MarkerItem, PathCache, RegionItem

__all__ = ["SceneApplier", "SceneProbe", "probe_scene"]


@dataclass(frozen=True, slots=True)
class SceneProbe:
    """What is actually on screen, read back from Qt for testing.

    Read from the live scene graph rather than from the description, so it can catch a
    disagreement between the two -- which is the only interesting kind of bug here.
    """

    visible_region_ids: frozenset[str]
    visible_marker_ids: frozenset[str]
    fills: dict[str, str]
    item_count: int


class SceneApplier:
    """Owns the Qt items for one scene and keeps them in step with a description."""

    __slots__ = ("_scene", "_cache", "_regions", "_markers", "_last", "_last_markers")

    def __init__(self, scene: QGraphicsScene, cache: PathCache) -> None:
        self._scene = scene
        self._cache = cache
        self._regions: dict[str, RegionItem] = {}
        self._markers: dict[str, MarkerItem] = {}
        #: What each region item was last given: ``region_id -> (visual, path)``.
        #: Compared by identity, which is exact because both come from objects this
        #: class was handed and holds a reference to.
        self._last: dict[str, tuple[object, object]] = {}
        self._last_markers: dict[str, object] = {}

    def apply(self, description: SceneDescription) -> None:
        self._scene.setBackgroundBrush(QColor(description.background))

        seen_regions = set()
        for visual in description.regions:
            item = self._regions.get(visual.region_id)
            if item is None:
                item = RegionItem(visual.region_id)
                self._scene.addItem(item)
                self._regions[visual.region_id] = item
            path = self._cache.path(visual.shapes, visual.lod)
            seen_regions.add(visual.region_id)

            # **Skip the whole apply when nothing about this region changed.**
            #
            # Not a partial update: what is compared is the entire visual and the path,
            # so anything at all being different rewrites every field exactly as before.
            # There is no second copy of the world's state to drift -- these are the very
            # objects handed in, and `PathCache` returns the same path object for the
            # same shapes and detail level, so identity is both exact and free.
            #
            # Measured on the real window: a click at world zoom re-applied all 429
            # regions to write the values already there. The visual for a region that did
            # not change is equal, and for 428 of 429 regions on a click, nothing did.
            #
            # Visibility is part of the condition because an item culled out of the
            # viewport still holds the visual it was last given; without this, a region
            # that scrolled off and came back would stay hidden forever.
            previous = self._last.get(visual.region_id)
            if (
                previous is not None
                and previous[0] == visual
                and previous[1] is path
                and item.isVisible()
            ):
                continue

            item.apply(visual, path)
            self._last[visual.region_id] = (visual, path)

        # Anything not in this frame's description must stop being visible. Hiding rather
        # than deleting keeps item identity stable across frames, which keeps selection
        # and hover state coherent.
        for region_id, item in self._regions.items():
            if region_id not in seen_regions:
                item.setVisible(False)

        seen_markers = set()
        for marker in description.markers:
            item = self._markers.get(marker.marker_id)
            if item is None:
                item = MarkerItem(marker.marker_id)
                self._scene.addItem(item)
                self._markers[marker.marker_id] = item
            seen_markers.add(marker.marker_id)
            if self._last_markers.get(marker.marker_id) == marker and item.isVisible():
                continue
            item.apply(marker)
            self._last_markers[marker.marker_id] = marker

        for marker_id, item in self._markers.items():
            if marker_id not in seen_markers:
                item.setVisible(False)

    def declutter_labels(self, to_screen) -> int:
        """Hide the strength captions that would land on top of one another. Returns how
        many were hidden.

        **The counters were never the problem; the captions were.** Over Europe at world
        zoom the provinces are a few pixels apart, so thirty counters put thirty copies of
        `3 / 12` into the same square inch and the result is unreadable pulp exactly where
        a player's own country is. The silhouettes still read at that density -- they are
        distinct shapes in owner colours, and a stack that is hidden behind another is the
        other one's click to answer -- so what has to give way is the text.

        **Greedy, in marker-id order, and that ordering is load-bearing.** The scene
        description is a pure function of its inputs and its digest is used as an equality
        check; a declutter that visited a dictionary in insertion order would hide a
        different caption depending on the order rows arrived in, and two identical worlds
        would stop looking identical. Sorted ids make the choice reproducible.

        **It lives here rather than in the renderer for the same reason.**
        `atlas.map.render` is deliberately zoom-independent -- it cannot know how far apart
        two counters will land on screen, and making its output depend on the transform
        would mean a scene digest that changed when somebody scrolled a wheel.

        `to_screen` maps a scene point to viewport pixels -- the view's own
        `mapFromScene`. **Handed in rather than derived from a scale factor**, because a
        marker's position is in projection degrees while its caption's offset is already in
        device pixels, and reconstructing that mapping from `transform().m11()` silently
        assumes both axes scale alike and neither is flipped. Asking the view is exact, and
        an earlier cut of this that multiplied by a single scale left two captions
        overlapping at one zoom step for precisely that reason.
        """
        placed: list[tuple[float, float, float, float]] = []
        hidden = 0
        for _marker_id, item in sorted(self._markers.items()):
            if not item.isVisible() or not item.is_counter:
                continue
            label = item.strength_label
            if label is None or not label.text():
                continue
            origin = to_screen(item.pos())
            box = label.boundingRect()
            left = origin.x() + label.pos().x()
            top = origin.y() + label.pos().y()
            rect = (left, top, left + box.width(), top + box.height())
            if any(_overlapping(rect, taken) for taken in placed):
                label.setVisible(False)
                hidden += 1
            else:
                label.setVisible(True)
                placed.append(rect)
        return hidden

    def region_item(self, region_id: str) -> RegionItem | None:
        return self._regions.get(region_id)

    def probe(self) -> SceneProbe:
        """Read back what is genuinely visible, for assertions."""
        visible_regions = {
            region_id for region_id, item in self._regions.items() if item.isVisible()
        }
        visible_markers = {
            marker_id for marker_id, item in self._markers.items() if item.isVisible()
        }
        fills = {
            region_id: self._regions[region_id].brush().color().name().upper()
            for region_id in visible_regions
        }
        return SceneProbe(
            visible_region_ids=frozenset(visible_regions),
            visible_marker_ids=frozenset(visible_markers),
            fills=fills,
            item_count=len(self._regions) + len(self._markers),
        )


def probe_scene(applier: SceneApplier) -> SceneProbe:
    return applier.probe()


def _overlapping(
    one: tuple[float, float, float, float], two: tuple[float, float, float, float]
) -> bool:
    """Whether two screen-space caption boxes touch at all.

    Any overlap counts. A caption half-covered by another is not a number a player can
    read, and "mostly legible" is the state this defect was reported as.
    """
    return not (one[2] <= two[0] or two[2] <= one[0] or one[3] <= two[1] or two[3] <= one[1])
