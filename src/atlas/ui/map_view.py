"""The map widget: zoom, pan, click to inspect, hover to identify.

The view owns interaction and nothing else. It does not decide what anything looks like;
it asks for a scene description and hands it to the applier. Zoom changes the level of
detail, which means asking for a *new description* rather than mutating the current one.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
)

from ..map.render import choose_lod, should_dissolve
from ..map.scene_model import SceneDescription
from .map_applier import SceneApplier
from .map_items import MarkerItem, PathCache, RegionItem
from .preferences import current

__all__ = ["MapView"]

_MIN_ZOOM = 0.35
_MAX_ZOOM = 60.0

#: How long the view waits after the last pan or zoom before asking for a new scene.
#:
#: **This is the single biggest thing standing between a drag and a slideshow.**
#: ``scrollContentsBy`` fires once per mouse-move -- a hundred times a second on a normal
#: drag -- and each one used to trigger a full rebuild: a database read of every region,
#: a fresh scene description, and an unconditional re-apply of every visible item, which
#: calls ``setPath`` and throws away each item's render cache. Measured on the real Modern
#: Day world that is ~9 ms of Python per step before Qt repaints anything, against a 16 ms
#: frame budget.
#:
#: Only the *last* viewport in a drag is worth drawing, so the signal is coalesced: each
#: scroll restarts the timer and the rebuild happens once, when the hand stops moving.
#: 70 ms is under the threshold where a pause feels like lag and long enough that a
#: continuous drag rebuilds once rather than sixty times. Nothing about correctness
#: depends on it -- a missed intermediate viewport was never drawn.
_SETTLE_MS = 70


class MapView(QGraphicsView):
    """A pannable, zoomable world map."""

    region_clicked = Signal(str)
    #: A unit counter and the province it stands in: ``(marker_id, region_id)``.
    #:
    #: **Separate from `region_clicked` because a counter is two things at once.** It is a
    #: formation, and it is a formation *somewhere*, and a click on it means both -- so the
    #: window selects the formation and the province off one signal rather than guessing
    #: from a bare region id whether the press landed on the ground or on the counter.
    counter_clicked = Signal(str, str)
    #: A region and where on screen it was asked about, for a context menu.
    region_context = Signal(str, object)
    region_hovered = Signal(str)
    #: Emitted when zoom crosses a level-of-detail boundary, so the owner can rebuild
    #: the description at the new detail level.
    lod_changed = Signal(int)

    #: Emitted when the map has been panned, so the owner can re-derive a scene culled to
    #: what is now on screen. Cheap to answer: a description is a fraction of a
    #: millisecond and the geometry behind it is cached.
    viewport_moved = Signal()

    def __init__(self, pack, projection, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._cache = PathCache(pack, projection)
        self._applier = SceneApplier(self._scene, self._cache)
        self._lod_count = len(pack.lods)
        self._zoom = 1.0
        self._lod = choose_lod(self._zoom, self._lod_count)
        #: Set once the map has been fitted to a viewport with a real size.
        self._fitted = False

        #: Fires once after panning or zooming stops. See :data:`_SETTLE_MS`.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(_SETTLE_MS)
        self._settle.timeout.connect(self._settled)
        #: True while a pan or zoom is in progress and the map is drawn for speed.
        self._fast = False
        #: **The only thing in this interface that moves by itself.** There are no
        #: animations, no transitions and no easing curves anywhere in the program; what a
        #: person sensitive to motion would notice is this -- the map dropping to a coarser
        #: rendering while the hand is moving and snapping back a beat after it stops.
        #: With reduced motion asked for, it simply never drops, and a pan is slower and
        #: perfectly still. See `ui.preferences.MOTION_SURFACES`, which says as much and
        #: says how little else there is.
        self._settles = not current().reduced_motion
        #: Where the left button went down, until it comes back up. See
        #: :meth:`mouseReleaseEvent` for why selection waits for the release.
        self._press_at = None
        #: The region the hover bar is currently describing, so that the hundred
        #: mouse-moves it takes to cross one country ask about it once. See
        #: :meth:`mouseMoveEvent`.
        self._hovered: str | None = None

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        # Qt keeps a rendered copy of the static background instead of re-rasterising the
        # ocean on every scroll, and is told it may not assume anything about what items
        # draw outside their own bounding rects -- which lets it blit rather than repaint
        # while the hand is moving.
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontSavePainterState, True
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)

    # -- state -------------------------------------------------------------

    @property
    def lod(self) -> int:
        return self._lod

    @property
    def dissolve(self) -> bool:
        """Whether the map is far enough out that provinces should read as countries."""
        return should_dissolve(self._zoom)

    @property
    def applier(self) -> SceneApplier:
        return self._applier

    def viewport_rect(self) -> tuple[float, float, float, float] | None:
        """What is on screen, in scene units, with a margin. ``None`` before fitting.

        The margin is a screenful either way, so a pan that outruns the rebuild reveals
        ground already drawn rather than ocean.
        """
        if not self._fitted:
            return None
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if visible.isEmpty():
            return None
        wide = visible.adjusted(
            -visible.width(), -visible.height(), visible.width(), visible.height()
        )
        return (wide.left(), wide.top(), wide.right(), wide.bottom())

    # -- drawing for speed while the map is moving -------------------------

    def _enter_fast(self, *, drop_item_cache: bool) -> None:
        """Draw for speed until the map settles.

        Two knobs, and which one to pull depends on the gesture -- measured on the real
        window at world zoom, 429 regions:

        =========================  ============  ================
        configuration              zoom / event  static repaint
        =========================  ============  ================
        cached + antialiased            25.1 ms           4.5 ms
        uncached + antialiased          19.4 ms          17.5 ms
        cached, no antialiasing         16.2 ms           4.6 ms
        uncached, no antialiasing       10.9 ms           9.2 ms
        =========================  ============  ================

        Antialiasing costs about 40% of every frame, so it goes for the duration of any
        gesture. The item cache is not so simple: it is what makes a *pan* cheap, because
        Qt blits rasterised regions instead of re-tessellating coastlines -- but a *zoom*
        changes the transform, which invalidates every cached pixmap and makes the cache a
        pure cost. So panning keeps it and zooming drops it.

        Switching all 429 items costs 0.30 ms and happens twice per gesture rather than
        per event, which is why this is worth doing at all.
        """
        if not self._settles:
            # Reduced motion asked for: the map is never redrawn coarser and never snaps
            # back, so a pan is slower and perfectly still. See `_settles`.
            return
        if not self._fast:
            self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            self._fast = True
        if drop_item_cache:
            self._set_item_cache(QGraphicsPathItem.CacheMode.NoCache)

    def _settled(self) -> None:
        """Restore full quality, then ask for the scene the new viewport deserves."""
        if self._fast:
            self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._set_item_cache(QGraphicsPathItem.CacheMode.DeviceCoordinateCache)
            self._fast = False
        self.viewport_moved.emit()

    def _set_item_cache(self, mode) -> None:
        for item in self._scene.items():
            if isinstance(item, QGraphicsPathItem):
                item.setCacheMode(mode)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Panning changes which regions are worth resolving geometry for.

        Coalesced rather than emitted per scroll step: see :data:`_SETTLE_MS`.
        """
        super().scrollContentsBy(dx, dy)
        if self._fitted:
            # A pan keeps the item cache: it is exactly what a pan is fast because of.
            self._enter_fast(drop_item_cache=False)
            self._settle.start()

    def showEvent(self, event) -> None:
        """Fit once the widget actually has a size.

        ``fit_world`` during construction fits to a viewport that is still a few pixels
        wide, which leaves the world drawn as a postage stamp in the middle of a full
        window. Waiting for the first real show is what makes the map open usable.
        """
        super().showEvent(event)
        if not self._fitted and self.viewport().width() > 50:
            self._fitted = True
            self.fit_world()

    def resizeEvent(self, event) -> None:
        """Refit only if the map has never been fitted to a real viewport.

        Deliberately not a refit on every resize -- that would throw away the player's
        zoom and pan every time the window changed size.
        """
        super().resizeEvent(event)
        if not self._fitted and self.viewport().width() > 50:
            self._fitted = True
            self.fit_world()

    def show_scene(self, description: SceneDescription) -> None:
        # A new description can change what the hover bar should say about the region the
        # cursor is already sitting on -- an owner it lost this turn, say. The bar is a
        # pure function of the last region asked about, so forgetting that answer is all
        # it takes to make the next move re-read it.
        self._hovered = None
        self._applier.apply(description)
        if self._scene.sceneRect().isEmpty():
            self.fit_world()
        self._declutter()

    def fit_world(self) -> None:
        bounds: QRectF = self._scene.itemsBoundingRect()
        if bounds.isEmpty():
            return
        self._scene.setSceneRect(bounds)
        self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self._update_lod()
        self._declutter()

    # -- interaction -------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.zoom_by(1.0015 ** event.angleDelta().y())

    def zoom_by(self, step: float) -> None:
        """Multiply the zoom, clamped. Shared by the wheel and the on-map controls."""
        target = max(_MIN_ZOOM, min(_MAX_ZOOM, self._zoom * step))
        factor = target / self._zoom
        if factor == 1.0:
            return
        self._zoom = target
        if self._fitted:
            # A zoom changes the transform, so every cached pixmap is stale anyway.
            self._enter_fast(drop_item_cache=True)
        self.scale(factor, factor)
        self._update_lod()
        # Whether two captions collide is a question about screen pixels, so the answer
        # changes every time the transform does. Zooming into a crowded theatre is exactly
        # how a player asks for the numbers this hides.
        self._declutter()
        if self._fitted:
            self._settle.start()

    def _declutter(self) -> int:
        """Re-decide which strength captions can be read at the current transform.

        The view's own `mapFromScene` is handed over rather than a scale factor: it already
        accounts for the fit, the scroll and both axes, and `fitInView` sets a scale this
        view never multiplied, so anything reconstructed from `self._zoom` would disagree
        with it exactly when the map first appears.
        """
        return self._applier.declutter_labels(self.mapFromScene)

    def _update_lod(self) -> None:
        level = choose_lod(self._zoom, self._lod_count)
        if level != self._lod:
            self._lod = level
            self.lod_changed.emit(level)

    def mousePressEvent(self, event) -> None:
        """Remember where the left button went down; select nothing yet.

        **A drag used to begin by selecting a province.** Selection was emitted here, on
        the press, before Qt had started its own hand-drag -- so grabbing the map to pan
        it synchronously rebuilt the scene, rebuilt the selected-region rail and
        repainted, and only then began to drag. The cost of a click was paid on every
        drag, at the one moment the frame budget was already spent.

        **Left selects, right asks.** A right-click carries the menu and deliberately does
        not also move the selection -- asking what is possible somewhere is not the same
        as going there. The menu still opens on the press, because a context menu that
        waited for the release would feel broken; only selection moved.
        """
        if event.button() == Qt.MouseButton.RightButton:
            self._press_at = None
            # Off the marker's own province where the press landed on one, for the reason
            # `_select_under` gives: the counter is on top and the ground under it is not
            # reached. Asking what can be done *here* has to work where a formation is
            # standing, which is most of where it is worth asking.
            item = self.itemAt(event.position().toPoint())
            marker = _marker_of(item)
            region_id = marker.region_id if marker is not None and marker.region_id else None
            if region_id is None:
                region = _region_of(item)
                region_id = region.region_id if region is not None else None
            if region_id is not None:
                self.region_context.emit(region_id, event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Select, if the pointer never travelled far enough to have been a drag.

        Qt's own ``startDragDistance`` is the threshold, so what counts as a click here is
        what counts as a click everywhere else on the platform. Past it the gesture was a
        pan, and a pan must not change the selection.

        ``super()`` goes first: the base class ends the hand-drag on the release, and the
        selection handler is expensive enough that running it before Qt has let go of the
        gesture would leave the view in a dragging state for the duration.
        """
        press, self._press_at = self._press_at, None
        super().mouseReleaseEvent(event)
        if press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        moved = (event.position().toPoint() - press).manhattanLength()
        if moved > QApplication.startDragDistance():
            return
        self._select_under(event.position().toPoint())

    def _select_under(self, point) -> None:
        """Say what was clicked, from the one hit test the click already pays for.

        **A counter answers for the ground it is standing on.** Markers are drawn above
        regions, so `itemAt` at a unit counter returns the counter and the province under
        it is never reached -- which is the whole of the bug this replaced. Measured on a
        real Modern Day world before the fix: of the four counters reachable at world zoom,
        four selected nothing and none selected their province. It was every counter on
        the map, because _region_of on a marker cannot return anything else.

        The province is not recovered by asking the scene again. A second `itemAt` with
        the markers hidden, or a walk down `items(point)` looking for the first region,
        is a second full hit test on the frame a click already costs the most on -- and it
        is unnecessary, because the marker was built from a row that named its province
        and has carried it ever since. See `atlas.map.scene_model.MarkerVisual.region_id`.

        Stacked counters need nothing extra: land, sea and air are three items at three
        screen offsets, and `itemAt` returns the one the pointer is actually inside.
        """
        item = self.itemAt(point)
        marker = _marker_of(item)
        if marker is not None and marker.region_id:
            if marker.is_counter:
                self.counter_clicked.emit(marker.marker_id, marker.region_id)
            else:
                self.region_clicked.emit(marker.region_id)
            return
        region = _region_of(item)
        if region is not None:
            self.region_clicked.emit(region.region_id)

    def mouseMoveEvent(self, event) -> None:
        """Identify what is under the cursor -- but not while the map is being dragged.

        ``itemAt`` is a hit test against the scene index and it ran on every mouse-move,
        including the hundred a second that make up a pan. Nobody is reading the hover
        readout while they are hauling the map across the screen, and the work landed on
        exactly the frames that could least afford it.
        """
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            item = self.itemAt(event.position().toPoint())
            region = _region_of(item)
            # **Only when it changes.** Every move used to re-emit, and the far end of
            # that signal is a database read and a chrome update -- so crossing one
            # country asked the same question of SQLite a hundred times and got the same
            # answer. `show_scene` forgets this, so a redraw still refreshes the bar.
            if region is not None and region.region_id != self._hovered:
                self._hovered = region.region_id
                self.region_hovered.emit(region.region_id)
        super().mouseMoveEvent(event)


def _region_of(item) -> RegionItem | None:
    """Walk up to the owning region, since a click may land on a child label."""
    while item is not None:
        if isinstance(item, RegionItem):
            return item
        item = item.parentItem()
    return None


def _marker_of(item) -> MarkerItem | None:
    """Walk up to the owning marker, since a counter's strength label is a child of it.

    A marker is not a child of the region it stands in -- both are top-level items in the
    scene -- so this walk terminates at the marker and `_region_of` on the same item finds
    nothing. That asymmetry is why a click on a counter was silently discarded.
    """
    while item is not None:
        if isinstance(item, MarkerItem):
            return item
        item = item.parentItem()
    return None
