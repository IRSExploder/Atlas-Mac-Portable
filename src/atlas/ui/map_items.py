"""Qt graphics items, and the cache that turns baked geometry into painter paths.

``RegionItem`` subclasses ``QGraphicsPathItem`` rather than ``QGraphicsPolygonItem``
because a region is frequently several disjoint polygons with holes -- an archipelago, a
country with an enclave carved out of it. A polygon item can represent neither.

**The cache key is the load-bearing detail.** Paths are keyed on
``(geo_keys, lod, projection)`` and nothing else. Every component of that key is
immutable: geometry never changes, level of detail is chosen from zoom, and the
projection is fixed for a session. Ownership, turn number and observer are deliberately
absent, so the cache physically cannot serve a stale *appearance* -- only a stale
*outline*, which cannot go stale because outlines never change. Adding `turn` or `owner`
to this key would reintroduce exactly the desync this design exists to prevent.

A *set* of shapes is still geometry: which shapes compose a region is world data and can
change, but the outline of any given set cannot. So a region that gains a province gets
a new key rather than a stale path.
"""

from __future__ import annotations

from array import array
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsSimpleTextItem, QStyle

from ..map.projection import Projection
from ..map.scene_model import FillPattern, MarkerKind, MarkerVisual, RegionVisual, UnitDomain

__all__ = ["MarkerItem", "PathCache", "RegionItem"]


class PathCache:
    """Builds and remembers ``QPainterPath`` objects for baked geometry."""

    __slots__ = ("_pack", "_projection", "_paths")

    def __init__(self, pack, projection: Projection) -> None:
        self._pack = pack
        self._projection = projection
        self._paths: dict[tuple[str, int, str], QPainterPath] = {}

    def path(self, geo_keys: str | Sequence[str], lod: int) -> QPainterPath:
        """The outline of one or more shapes, as a single path.

        Several shapes rather than one because a region is no longer one-to-one with a
        pack entry: France's Alsace-Lorraine is four departments and Yugoslavia's regions
        are dozens of municipalities each. The key is still only geometry -- the *set* of
        shapes and the level of detail -- so it stays as immutable as it was when a
        region meant exactly one entry.
        """
        keys = (geo_keys,) if isinstance(geo_keys, str) else tuple(geo_keys)
        key = (keys, lod, self._projection.key)
        cached = self._paths.get(key)
        if cached is not None:
            return cached

        path = QPainterPath()
        # Winding fill, not odd-even. Simplification can leave parts of a shape
        # overlapping, and under odd-even every overlap renders as a hole. It is also
        # what lets several shapes share one path without their touching edges reading as
        # holes in the region they make up.
        path.setFillRule(Qt.FillRule.WindingFill)

        for geo_key in keys:
            try:
                parts: Sequence[Sequence[array]] = self._pack.geometry(geo_key, lod)
            except KeyError:
                continue

            for rings in parts:
                for ring in rings:
                    projected = self._projection.project_ring(ring)
                    if len(projected) < 6:
                        continue
                    path.moveTo(projected[0], projected[1])
                    for i in range(2, len(projected), 2):
                        path.lineTo(projected[i], projected[i + 1])
                    path.closeSubpath()

        if len(keys) > 1:
            # Shapes that abut leave a seam down every shared border, and a region drawn
            # from a dozen provinces reads as a dozen provinces. Simplifying resolves the
            # shared edges into one outline -- which is what makes Germany look like
            # Germany rather than like sixteen states -- and it is affordable because the
            # topology-preserving bake guarantees neighbours share their vertices exactly.
            path = path.simplified()

        self._paths[key] = path
        return path

    def clear(self) -> None:
        self._paths.clear()

    def __len__(self) -> int:
        return len(self._paths)


class RegionItem(QGraphicsPathItem):
    """One region. Stores its identity; appearance is written wholesale on every apply."""

    __slots__ = ()

    def __init__(self, region_id: str) -> None:
        super().__init__()
        self.setData(0, region_id)
        # **Not `setAcceptHoverEvents(True)`.** Every one of the 427 region items used
        # to accept hover events, and nothing anywhere implements `hoverEnterEvent`,
        # `hoverMoveEvent` or `hoverLeaveEvent` -- so Qt ran a full scene hit test on
        # every mouse move to deliver events with no recipient. What identifies the
        # region under the cursor is `MapView.mouseMoveEvent`, which does its own
        # `itemAt`; that is the hit test this map actually needs, and it is one.
        # Rasterise once and blit while the map moves, rather than re-tessellating a
        # several-hundred-point coastline on every frame of a pan. `setPath` invalidates
        # this, which is why it matters that rebuilds are now coalesced rather than fired
        # per scroll step -- the cache only pays if the path stops changing.
        self.setCacheMode(QGraphicsPathItem.CacheMode.DeviceCoordinateCache)

    @property
    def region_id(self) -> str:
        return str(self.data(0))

    def apply(self, visual: RegionVisual, path: QPainterPath) -> None:
        """Write *every* visual property. No conditional updates.

        Skipping an unchanged field would mean tracking what the item currently shows,
        which is a second copy of the world's state living in the scene graph. That copy
        is what drifts.

        Whether this call is worth making at all is the applier's decision, not the
        item's -- see :meth:`SceneApplier.apply`. It lives there because the applier
        already holds the description it was given, in Python, where an identity check is
        exact. An attempt to memo it *here* via ``setData`` silently never fired: Qt
        round-trips a ``QPainterPath`` through ``QVariant`` and hands back a new wrapper,
        so ``is`` was always False.
        """
        self.setPath(path)

        colour = QColor(visual.fill)
        if visual.pattern is FillPattern.HATCHED and visual.pattern_colour:
            # A Qt pattern brush paints ONLY the pattern; the gaps are left unpainted and
            # the scene background shows through. Setting `QBrush(fill, Dense4Pattern)`
            # therefore lost half an occupied region to ocean colour and never drew the
            # occupier at all -- pattern_colour was the one RegionVisual field the applier
            # dropped. A two-colour texture paints both: the owner's colour underneath,
            # the occupier's hatched over it, which is what the legend promises.
            self.setBrush(QBrush(_hatch(colour, QColor(visual.pattern_colour))))
        elif visual.pattern is FillPattern.NONE:
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        else:
            self.setBrush(QBrush(colour))

        pen = QPen(QColor(visual.outline))
        pen.setWidthF(visual.outline_width)
        pen.setCosmetic(True)  # constant on-screen width regardless of zoom
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)

        self.setZValue(visual.z)
        self.setToolTip(visual.tooltip)
        self.setVisible(True)
        self.setFlag(
            QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, visual.selectable
        )

    def paint(self, painter, option, widget=None) -> None:
        """Without Qt's own selection box, and with hatching at screen resolution.

        A selected path item draws a dotted rectangle round its bounding box -- a square
        of dots cutting across the map, nowhere near the province's real shape. The map
        marks selection itself, in the outline.

        **The hatch is laid in screen pixels, not map units.** A texture brush is scaled
        with everything else the painter draws, and the map's units are degrees: at the
        zoom a game opens at, one pixel of the 8x8 hatch tile was drawn tens of pixels
        wide, and the diagonal stripe that says "occupied" became a staircase of
        enormous squares across the province.
        """
        option.state &= ~QStyle.StateFlag.State_Selected
        brush = self.brush()
        if brush.style() != Qt.BrushStyle.TexturePattern:
            super().paint(painter, option, widget)
            return
        painter.save()
        to_screen = painter.worldTransform()
        on_screen = to_screen.map(self.path())
        painter.resetTransform()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)  # untransformed now: one texel is one screen pixel
        painter.drawPath(on_screen)
        painter.restore()
        # The outline over it, as an ordinary path item draws it.
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())


class MarkerItem(QGraphicsPathItem):
    """A city, capital or domain-shaped unit counter.

    **It knows which province it is standing in.** A counter is drawn on top of the ground
    it occupies, so it is the topmost item at a click and the region beneath it is never
    reached -- which is why clicking a unit counter used to select nothing. The province is
    written onto the item as data at apply time, off the same description that positioned
    it, so answering *which province was that* costs a dictionary lookup rather than a
    second hit test against the scene index. See `MapView.mouseReleaseEvent`.
    """

    __slots__ = ()

    def __init__(self, marker_id: str) -> None:
        super().__init__()
        self.setData(0, marker_id)
        # Marker size is in screen pixels, not projection units. Without this the item
        # scales with the map, and since projection units are *degrees*, a size of 5 is
        # five degrees of longitude -- a city marker that swallows the country it sits in.
        # This is the marker equivalent of a cosmetic pen.
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._label = QGraphicsSimpleTextItem(self)

    @property
    def marker_id(self) -> str:
        return str(self.data(0))

    @property
    def region_id(self) -> str:
        """The province this marker stands in, or ``""`` where it stands in none."""
        return str(self.data(1) or "")

    @property
    def strength_label(self) -> QGraphicsSimpleTextItem | None:
        """The counter's strength caption, or ``None`` on a marker that carries none.

        Exposed because the *view* decides whether a caption can be read: whether two
        counters collide is a question about screen pixels and the current zoom, and the
        renderer that built this marker is deliberately zoom-independent.
        """
        for child in self.childItems():
            if isinstance(child, QGraphicsSimpleTextItem):
                return child
        return None

    @property
    def is_counter(self) -> bool:
        """Whether this is a formation rather than a city or a focus pin."""
        return str(self.data(2) or "") == str(MarkerKind.UNIT)

    @property
    def is_name(self) -> bool:
        """Whether this is a country's name written on the map."""
        return str(self.data(2) or "") == str(MarkerKind.NAME)

    def apply(self, visual: MarkerVisual) -> None:
        if visual.kind is MarkerKind.NAME:
            self._apply_name(visual)
            return
        if visual.kind is MarkerKind.UNIT:
            self._apply_counter(visual)
            return
        half = visual.size / 2.0
        label = self.childItems()[0] if self.childItems() else None
        if isinstance(label, QGraphicsSimpleTextItem):
            label.setFont(QFont())
            label.setPen(QPen(Qt.PenStyle.NoPen))
        self.setPath(_marker_path(visual))
        self.setPos(QPointF(visual.x, visual.y))
        # Written on every apply like everything else here: an item is reused across
        # frames and a stale province on a counter would send a click somewhere else.
        self.setData(1, visual.region_id)
        self.setData(2, str(visual.kind))

        self.setBrush(QBrush(QColor(visual.colour)))
        # A selected formation is drawn in the same language a selected province is: the
        # outline thickens and takes the selection ink, which the renderer has already put
        # in `outline` off the palette. Nothing else about it changes -- a counter that
        # changed colour would read as a counter that changed owner.
        pen = QPen(QColor(visual.outline))
        pen.setWidthF(2.5 if visual.selected else 1.0)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(visual.z)
        self.setToolTip(visual.tooltip)

        # Unit counters carry their strength; cities are labelled on the map itself only
        # at close zoom, which the view controls.
        text = visual.label if visual.kind is MarkerKind.UNIT else ""
        label = self.childItems()[0] if self.childItems() else None
        if isinstance(label, QGraphicsSimpleTextItem):
            label.setText(text)
            label.setBrush(QBrush(QColor(visual.label_colour)))
            label.setPos(visual.offset_x + half + 3.0, visual.offset_y - half)
            label.setVisible(bool(text))
        self.setVisible(True)

    def _apply_counter(self, visual: MarkerVisual) -> None:
        """A formation as a token: a medallion in its side's colour with a white rim and a
        white silhouette, its strength on a dark tab beside it, and a shadow under both.

        **It used to be the silhouette itself, filled in the side's colour** -- which is the
        colour of the ground whenever an army is at home, so a player's own divisions
        vanished into their own country and an enemy's into the enemy's: the two places a
        counter matters most. The white rim and the shadow lift the token off any ground,
        and the medallion still says whose it is.
        """
        self.setPos(QPointF(visual.x, visual.y))
        self.setData(1, visual.region_id)
        self.setData(2, str(visual.kind))
        self.setZValue(visual.z)
        self.setToolTip(visual.tooltip)
        for child in self.childItems():
            if isinstance(child, QGraphicsPathItem):  # an icon item from an older layout
                child.setVisible(False)

        label = self.strength_label
        if label is not None:
            font = QFont()
            font.setPointSizeF(8.5)
            font.setWeight(QFont.Weight.Bold)
            label.setFont(font)
            label.setText(visual.label)
            label.setBrush(QBrush(QColor(COUNTER_INK)))
            label.setPen(QPen(Qt.PenStyle.NoPen))
        # Everything the painter needs, kept on the item so the view's declutter can fold
        # the tab away without asking the renderer again.
        diameter = max(21.0, min(28.0, visual.size + 10.0))
        self.setData(3, (visual.offset_x, visual.offset_y, diameter))
        self.setData(4, visual.colour)
        self.setData(5, bool(visual.selected))
        self.setData(6, visual.outline)
        self.setData(7, str(visual.domain or ""))
        self.show_figure(bool(visual.label))
        self.setVisible(True)

    def _token(self) -> tuple[QRectF, QRectF | None]:
        """The medallion's rectangle and the tab's, or ``None`` for a folded tab."""
        cx, cy, diameter = self.data(3)
        medallion = QRectF(cx - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)
        label = self.strength_label
        if label is None or not label.isVisible():
            return medallion, None
        text = label.boundingRect()
        height = diameter * 0.74
        width = diameter / 2.0 + 5.0 + text.width() + 6.0
        tab = QRectF(cx, cy - height / 2.0, width, height)
        return medallion, tab

    def show_figure(self, shown: bool) -> None:
        """Lay the token out with its strength tab, or as the medallion alone when the
        figure would collide with a neighbour's (see `declutter_labels`)."""
        if not self.data(3):
            return
        label = self.strength_label
        if label is not None:
            label.setVisible(shown and bool(label.text()))
        medallion, tab = self._token()
        outline = QPainterPath()
        outline.addEllipse(medallion)
        if tab is not None:
            pill = QPainterPath()
            pill.addRoundedRect(tab, tab.height() / 2.0, tab.height() / 2.0)
            outline = outline.united(pill)
            text = label.boundingRect()
            label.setPos(medallion.right() + 3.0, tab.center().y() - text.height() / 2.0)
        # The path is what a click hits and what the scene culls by; the look is `paint`.
        self.prepareGeometryChange()
        self.setPath(outline)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    def boundingRect(self) -> QRectF:  # noqa: N802 -- Qt override
        rect = super().boundingRect()
        # Room for the shadow, which is drawn below and around the token's outline.
        return rect.adjusted(-3.0, -3.0, 3.0, 5.0) if self.data(3) else rect

    def paint(self, painter, option, widget=None) -> None:
        if not self.is_counter or not self.data(3):
            super().paint(painter, option, widget)
            return
        medallion, tab = self._token()
        side = QColor(str(self.data(4) or "#CCCCCC"))
        selected = bool(self.data(5))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # A soft shadow: two passes, so the edge falls off rather than stopping.
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((2.2, 40), (1.0, 70)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawPath(self.path().translated(0.0, 1.6 + spread * 0.4))

        if tab is not None:
            painter.setBrush(QColor(COUNTER_PLATE))
            rim = QPen(_lifted(side, 0.35))
            rim.setWidthF(1.2)
            painter.setPen(rim)
            painter.drawRoundedRect(tab, tab.height() / 2.0, tab.height() / 2.0)

        shade = QLinearGradient(medallion.topLeft(), medallion.bottomLeft())
        shade.setColorAt(0.0, _lifted(side, 0.35))
        shade.setColorAt(1.0, side.darker(135))
        painter.setBrush(QBrush(shade))
        ring = QPen(QColor(str(self.data(6))) if selected else QColor(COUNTER_RING))
        ring.setWidthF(2.6 if selected else 1.6)
        painter.setPen(ring)
        painter.drawEllipse(medallion)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COUNTER_INK))
        glyph = _glyph(str(self.data(7) or "land"), medallion.width() * 0.68)
        painter.drawPath(glyph.translated(medallion.center()))
        painter.restore()

    def _apply_name(self, visual: MarkerVisual) -> None:
        """A country's name: spaced capitals, centred on the point, with a dark edge so it
        reads on any fill."""
        self.setPath(QPainterPath())
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPos(QPointF(visual.x, visual.y))
        self.setData(1, visual.region_id)
        self.setData(2, str(visual.kind))
        self.setZValue(visual.z)
        self.setToolTip("")
        label = self.childItems()[0] if self.childItems() else None
        if isinstance(label, QGraphicsSimpleTextItem):
            font = QFont()
            font.setPointSizeF(8.5)
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
            label.setFont(font)
            label.setText(visual.label.upper())
            label.setBrush(QBrush(QColor(visual.label_colour)))
            edge = QPen(QColor(visual.outline))
            edge.setWidthF(0.6)
            label.setPen(edge)
            box = label.boundingRect()
            label.setPos(-box.width() / 2.0, -box.height() / 2.0)
            label.setVisible(True)
        self.setVisible(True)


#: A counter's plate and the ink drawn on it: dark enough to read on any ground,
#: whatever colour the country under it is.
COUNTER_PLATE = "#EE121821"
COUNTER_INK = "#F6F8FB"
COUNTER_RING = "#F6F8FB"


def _lifted(colour: QColor, share: float = 0.25) -> QColor:
    """A colour moved ``share`` of the way toward white."""
    return QColor(
        round(colour.red() + (255 - colour.red()) * share),
        round(colour.green() + (255 - colour.green()) * share),
        round(colour.blue() + (255 - colour.blue()) * share),
    )


def _glyph(domain: str, size: float) -> QPainterPath:
    """A unit's silhouette, centred on the origin and ``size`` across.

    Drawn as paths rather than shipped as images, so it stays sharp at any display scale.
    """
    s = size
    path = QPainterPath()
    if domain == "sea":
        # A warship in profile: raked bow, superstructure, bridge, mast and a forward gun.
        hull = QPainterPath()
        hull.moveTo(-0.52 * s, 0.06 * s)
        hull.lineTo(0.56 * s, 0.06 * s)
        hull.lineTo(0.40 * s, 0.30 * s)
        hull.lineTo(-0.44 * s, 0.30 * s)
        hull.closeSubpath()
        path.addPath(hull)
        path.addRect(QRectF(-0.26 * s, -0.12 * s, 0.40 * s, 0.20 * s))
        path.addRect(QRectF(-0.12 * s, -0.30 * s, 0.18 * s, 0.20 * s))
        path.addRect(QRectF(-0.04 * s, -0.50 * s, 0.035 * s, 0.22 * s))
        path.addRect(QRectF(0.18 * s, -0.04 * s, 0.30 * s, 0.05 * s))
        return path.simplified()
    if domain == "air":
        # A jet seen from above: nose, swept wings, tailplane.
        points = (
            (0.00, -0.55), (0.08, -0.30), (0.09, -0.08), (0.52, 0.14), (0.52, 0.24),
            (0.09, 0.14), (0.07, 0.36), (0.24, 0.48), (0.24, 0.55), (0.00, 0.49),
            (-0.24, 0.55), (-0.24, 0.48), (-0.07, 0.36), (-0.09, 0.14), (-0.52, 0.24),
            (-0.52, 0.14), (-0.09, -0.08), (-0.08, -0.30),
        )
        path.moveTo(points[0][0] * s, points[0][1] * s)
        for x, y in points[1:]:
            path.lineTo(x * s, y * s)
        path.closeSubpath()
        return path
    # A tank in profile: tracks with road wheels, a sloped hull, turret and barrel.
    tracks = QPainterPath()
    tracks.addRoundedRect(QRectF(-0.50 * s, 0.06 * s, 1.00 * s, 0.30 * s),
                          0.15 * s, 0.15 * s)
    wheels = QPainterPath()
    for i in range(4):
        x = -0.33 * s + i * 0.22 * s
        wheels.addEllipse(QPointF(x, 0.21 * s), 0.075 * s, 0.075 * s)
    path.addPath(tracks.subtracted(wheels))
    hull = QPainterPath()
    hull.moveTo(-0.44 * s, 0.03 * s)
    hull.lineTo(0.46 * s, 0.03 * s)
    hull.lineTo(0.34 * s, -0.12 * s)
    hull.lineTo(-0.38 * s, -0.12 * s)
    hull.closeSubpath()
    path.addPath(hull)
    path.addRoundedRect(QRectF(-0.22 * s, -0.32 * s, 0.38 * s, 0.21 * s), 0.07 * s, 0.07 * s)
    path.addRect(QRectF(0.14 * s, -0.25 * s, 0.44 * s, 0.06 * s))
    return path.simplified()


def _marker_path(visual: MarkerVisual) -> QPainterPath:
    """Build a small, recognisable silhouette in screen-pixel coordinates.

    These paths are deliberately cheap: a handful of line segments, rebuilt only for
    the visible counters. Geometry-pack paths remain the expensive shapes and continue
    to live in their own cache.
    """
    path = QPainterPath()
    cx, cy = visual.offset_x, visual.offset_y
    size = visual.size
    half = size / 2.0

    if visual.kind is not MarkerKind.UNIT or visual.domain is None:
        path.addEllipse(QRectF(cx - half, cy - half, size, size))
        return path

    if visual.domain is UnitDomain.LAND:
        # Tank: hull, turret and barrel.
        path.addRoundedRect(
            QRectF(cx - half, cy - size * 0.24, size, size * 0.48), 2.0, 2.0
        )
        path.addRect(
            QRectF(cx - size * 0.22, cy - size * 0.42, size * 0.44, size * 0.22)
        )
        path.moveTo(cx + size * 0.18, cy - size * 0.32)
        path.lineTo(cx + size * 0.58, cy - size * 0.32)
    elif visual.domain is UnitDomain.SEA:
        # Ship: a pointed hull with a compact superstructure.
        path.moveTo(cx - half, cy - size * 0.12)
        path.lineTo(cx + half, cy - size * 0.12)
        path.lineTo(cx + size * 0.28, cy + size * 0.36)
        path.lineTo(cx - size * 0.30, cy + size * 0.36)
        path.closeSubpath()
        path.addRect(
            QRectF(cx - size * 0.18, cy - size * 0.38, size * 0.36, size * 0.27)
        )
    else:
        # Aircraft seen from above: nose, swept wings and tailplane.
        points = (
            (0.00, -0.55), (0.11, -0.12), (0.48, 0.08), (0.48, 0.20),
            (0.10, 0.12), (0.08, 0.42), (0.25, 0.52), (0.25, 0.58),
            (0.00, 0.52), (-0.25, 0.58), (-0.25, 0.52), (-0.08, 0.42),
            (-0.10, 0.12), (-0.48, 0.20), (-0.48, 0.08), (-0.11, -0.12),
        )
        path.moveTo(cx + points[0][0] * size, cy + points[0][1] * size)
        for x, y in points[1:]:
            path.lineTo(cx + x * size, cy + y * size)
        path.closeSubpath()

    return path


#: Cache of occupation textures, keyed by the two colours. Building a QPixmap per region
#: per frame would allocate hundreds of small images every redraw.
_HATCH_CACHE: dict[tuple[str, str], QPixmap] = {}


def _hatch(base: QColor, over: QColor) -> QPixmap:
    """An 8x8 tile: the owner's colour, with the occupier's drawn diagonally across it.

    Both colours are present, which is the point -- an occupied region has to say who
    owns it *and* who is standing on it, and a single-colour pattern brush says neither.
    """
    key = (base.name(), over.name())
    cached = _HATCH_CACHE.get(key)
    if cached is not None:
        return cached

    tile = QPixmap(8, 8)
    tile.fill(base)
    painter = QPainter(tile)
    pen = QPen(over)
    pen.setWidth(3)
    painter.setPen(pen)
    # Two strokes so the diagonal wraps cleanly at the tile edges and reads as continuous
    # when the tile repeats.
    painter.drawLine(-2, 10, 10, -2)
    painter.drawLine(6, 10, 10, 6)
    painter.end()

    _HATCH_CACHE[key] = tile
    return tile
