"""The screens that take over: reading a turn, rewinding, confirming, exporting.

They are the same object underneath -- a dimmed sheet over the whole window with a
panel centred on it -- because they are all the same kind of moment: the world is not
available for input until this is dealt with. Sharing one base is what keeps the escape
behaviour, the dimming and the sizing identical rather than nearly identical -- including
for the one that lives elsewhere: the view a jump is watched through is
:class:`atlas.ui.turning.TurningOverlay`, which is built on this base and kept in its own
module because everything on it is written in the world's words rather than the program's.

They are children of the main window rather than ``QDialog``s. A real dialog is a separate
top-level window: it gets its own entry in the taskbar, its own shadow, and on Windows it
can end up behind the window it belongs to. An overlay child cannot.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QRegion, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..engine.attention import AttentionItem, attention_for
from ..engine.turn_engine import JumpReport
from ..humanize import count
from ..state.models import TurnStatus
from ..state.visibility import by_salience
from .plain import plain_line, someone_elses_pocket, technical
from .preferences import key_for
from .theme import PANEL, PANEL_INSET, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
    restyle,
    scroll_qss,
)

__all__ = [
    "ConfirmOverlay",
    "ExportOverlay",
    "Overlay",
    "RewindOverlay",
    "SummaryOverlay",
    "UndoTurnOverlay",
]


class _Sheet(QScrollArea):
    """A scroll area that asks for the height of what is in it.

    A plain one asks for about 256 pixels whatever it holds, which is how putting every
    panel behind one turned seventeen panels into seventeen identical stubs. The panel
    still wants to be exactly as tall as its contents; the scrolling is only what happens
    when the window will not allow that.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._measured_at = -1

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # How tall the contents are is a question about how wide they have been made, and
        # the layout caches the answer. Without this the panel keeps the height it worked
        # out before it had a width -- which is how every panel came out at the cap.
        if self.viewport().width() != self._measured_at:
            self._measured_at = self.viewport().width()
            self.updateGeometry()

    def sizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:  # pragma: no cover -- always set in the constructor
            return super().sizeHint()
        hint = inner.sizeHint()
        width = self.viewport().width() or hint.width()
        layout = inner.layout()
        if layout is not None and layout.hasHeightForWidth():
            # Wrapped prose is most of what is in these panels, and how tall it is is a
            # question about how wide it is.
            return QSize(hint.width(), layout.heightForWidth(width))
        return hint


class Overlay(QWidget):
    """A dimmed sheet with a panel on it. Subclasses fill the panel.

    **One overlay at a time, and one scrim.** ``open()`` used to be
    ``setVisible(True); raise_()``, which never closed a sibling -- so pressing Strategy
    and then the Home Front, two adjacent controls, left both panels on the screen: the
    second in front, the first showing through it at 28%, and the application dimmed
    twice over to 92%. A third took it to 98%. It did not read as a stack, it read as a
    rendering fault. Opening one now dismisses the others through the same door Escape
    uses, so a panel that has something to say on its way out still says it, and only the
    topmost sheet paints the shade.

    **One panel may be pinned.** Closing siblings is right as a default and wrong as a
    law: half of what these panels are for is comparison -- what the Strategy table says
    against what the Home Front says, what a power wrote against what it then did -- and
    forced closure turns that into a memory test. So a pinnable panel carries a pin. A
    pinned one is not dismissed when the next opens; the two dock to opposite sides of
    the window instead, each taking half the room, and the sheet in front cuts a hole in
    its own shade and its own mask so the pinned one stays lit and stays clickable. Only
    one may be pinned: two is a tiling window manager, which is not what this is.
    """

    dismissed = Signal()

    #: Whether Escape, a click on the dim, and a sibling opening can close it. A jump in
    #: flight must not be dismissable by a stray keypress, so that one says no -- and a
    #: panel opened over it goes above it rather than closing it.
    closable = True

    #: How wide this panel wants to be. One of :data:`atlas.ui.theme.PANEL`'s three, and
    #: capped at the window regardless -- see :meth:`_fit_to_window`.
    panel_width = PANEL.panel

    #: Whether this panel can be kept open beside the next one. A reading surface can; a
    #: question with two answers cannot -- there is nothing to compare a confirmation
    #: with, and one left pinned behind the thing it was confirming is a trap.
    pinnable = True

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        self._row = QHBoxLayout()
        self._row.setContentsMargins(PANEL_INSET, 0, PANEL_INSET, 0)
        self._row.addStretch(1)
        self.panel = QFrame()
        self.panel.setObjectName("overlayPanel")
        # **The body scrolls, and it is called a sheet.** `_scroll` and `_content` are
        # taken: fourteen of the panels built on this base already own attributes by those
        # names for their own inner scroll areas, and a base class that quietly takes a
        # subclass's attribute is a bug that presents as the wrong widget being themed.
        #
        # **The body scrolls.** A panel is capped at the window, and half a window when
        # it is sharing one, and neither of those is a width or a height its contents were
        # written for -- so without this, capping is just clipping with better manners:
        # the Branches footer lost its last line and a halved Home Front lost its
        # headline. Subclasses still build into `body` and never see this.
        self._sheet = _Sheet(self.panel)
        self._sheet.setWidgetResizable(True)
        # A scroll area expands by default, which would make every panel exactly as tall
        # as the cap. A panel is as tall as what is in it; the cap is only a limit.
        self._sheet.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._sheet.setFrameShape(QFrame.Shape.NoFrame)
        # Horizontal too, and only when it is needed. A panel narrowed to half a window
        # is narrower than some of these were drawn for -- the Strategy screen is two
        # columns of table -- and the choice there is between a bar the player can drag
        # and a column silently cut off at the edge.
        self._sheet.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sheet_body = QWidget()
        self.body = QVBoxLayout(self._sheet_body)
        self.body.setContentsMargins(SPACE.xxl, SPACE.xl, SPACE.xxl, SPACE.xl)
        self.body.setSpacing(0)
        self._sheet.setWidget(self._sheet_body)
        sheet = QVBoxLayout(self.panel)
        sheet.setContentsMargins(0, 0, 0, 0)
        sheet.addWidget(self._sheet)
        self._row.addWidget(self.panel)
        self._row.addStretch(1)
        outer.addLayout(self._row)
        outer.addStretch(1)

        #: ``None`` when the panel is centred, otherwise the side it is docked to while
        #: a comparison is on the screen.
        self._dock: str | None = None
        self._pinned = False
        self._pin = Chip("Pin", theme=theme, padding=(SPACE.sm, SPACE.xs))
        self._pin.setToolTip("Keep this open beside the next panel")
        self._pin.setAccessibleName("Pin this panel")
        self._pin.setParent(self)
        self._pin.setVisible(False)
        self._pin.clicked.connect(lambda: self.set_pinned(not self._pinned))
        self.set_panel_width(self.panel_width)

        # Whatever *dismiss* is bound to. Escape out of the box; a preference for anybody
        # whose hands or keyboard disagree with it. See `ui.preferences`.
        #
        # **Only the top sheet holds it.** A `QShortcut` defaults to window context, so
        # every overlay that is up is listening for the same key on the same window -- and
        # Qt answers two claimants by dispatching to neither, firing `activatedAmbiguously`
        # instead. With one panel open that never showed; with a panel *pinned* beside the
        # next one it meant Escape closed nothing at all, which is the one key a player
        # falls back on. `_arbitrate_dismiss` keeps exactly one of these enabled -- see
        # `_arrange_the_comparison`, which is already called on every change of situation.
        self._escape = QShortcut(QKeySequence(key_for("dismiss")), self)
        self._escape.activated.connect(self._maybe_close)

    # -- presentation ------------------------------------------------------

    def set_panel_width(self, width: int) -> None:
        """Ask for one of the three panel widths. The window has the last word."""
        self.panel_width = width
        self._fit_to_window()

    def open(self) -> None:
        for sibling in self._siblings():
            sibling.make_way()
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.setVisible(True)
        self.raise_()
        self.setFocus()
        self._arrange_the_comparison()

    def close_overlay(self) -> None:
        self.setVisible(False)
        self.set_pinned(False)
        self.clearMask()
        for sibling in self._siblings():
            if sibling.showing():
                # Whatever was underneath is the top sheet now: it has a shade to paint,
                # and it has the room to itself again.
                sibling._arrange_the_comparison()  # noqa: SLF001
        # Unconditionally, because the loop above does nothing when this was the last
        # sheet up -- and the key has to come back to whatever opens next.
        self._arbitrate_dismiss()
        self.dismissed.emit()

    # -- pinning -----------------------------------------------------------

    def set_pinned(self, pinned: bool) -> None:
        """Keep this panel open when the next one is asked for.

        Only one at a time: pinning a second releases the first, because the layout this
        drives is two panels side by side and there is no third side.
        """
        pinned = pinned and self.pinnable
        if pinned == self._pinned:
            return
        self._pinned = pinned
        if pinned:
            for sibling in self._siblings():
                sibling.set_pinned(False)
        self._pin.set_selected(pinned)
        self._pin.set_text("Pinned" if pinned else "Pin")
        self._pin.setToolTip("Let this close when the next panel opens" if pinned
                             else "Keep this open beside the next panel")
        for overlay in [self, *self._siblings()]:
            if overlay.showing():
                overlay._arrange_the_comparison()  # noqa: SLF001

    @property
    def pinned(self) -> bool:
        return self._pinned and self.pinnable

    def _pinned_sibling(self):
        for sibling in self._siblings():
            if sibling.pinned and sibling.showing():
                return sibling
        return None

    def _arrange_the_comparison(self) -> None:
        """Put this sheet where the other one leaves room for it, or back in the middle.

        Called on every overlay whose situation just changed -- one opened, one closed,
        one was pinned -- because *am I sharing* is a fact about the pair rather than
        about either one of them.
        """
        pinned = self._pinned_sibling()
        if (pinned is not None and self.showing() and not self.pinned
                and self._room_for_two()):
            pinned._dock, self._dock = "left", "right"  # noqa: SLF001
        else:
            self._dock = None
            if pinned is not None and pinned is not self:
                pinned._dock = None  # noqa: SLF001

        # **Both, and in this order.** The hole is cut from where the pinned panel *is*,
        # and where it is does not change until the layout has run -- a stretch factor and
        # a fixed width are a request. Ask for the mask first and you cut it around the
        # geometry the panel had before it was asked to share, which puts a window-shaped
        # gap through the middle of the sheet in front and leaves the pinned panel half
        # covered. `activate` is what makes the request true now rather than on the next
        # turn of the event loop.
        for overlay in ([pinned] if pinned is not None else []) + [self]:
            overlay._fit_to_window()  # noqa: SLF001
            overlay.layout().activate()
            overlay._place_the_pin()  # noqa: SLF001
            overlay.update()
        self._cut_a_hole_for_the_pinned_panel()
        self._arbitrate_dismiss()

    def _arbitrate_dismiss(self) -> None:
        """Leave the dismiss key enabled on the top sheet and on nothing else.

        Asked of every overlay in the window rather than of the pair that just changed,
        because *who holds Escape* is a fact about the whole stack: a panel can stop being
        topmost because a different one opened over it, and it has to give the key up in
        the same breath. Cheap enough to do wholesale -- there are a score of these and
        the answer is one boolean each.
        """
        parent = self.parentWidget()
        family = [self] if parent is None else [
            child for child in parent.children() if isinstance(child, Overlay)
        ]
        for overlay in family:
            overlay._escape.setEnabled(  # noqa: SLF001
                overlay.showing() and overlay._is_topmost()  # noqa: SLF001
            )

    def make_way(self) -> None:
        """Leave because a sibling is opening, by the same door Escape uses.

        Not `close_overlay`: some panels mean something specific by being dismissed --
        the read-back tells the rail nobody accepted the parse -- and a panel that is
        shoved aside has been dismissed just as surely as one that was escaped out of.
        A pinned panel is the one thing that does not leave; that is what the pin means.
        """
        if self.pinned:
            return
        self._maybe_close()

    def showing(self) -> bool:
        """Whether this sheet is up.

        `isVisibleTo` rather than `isVisible`, so the answer is about this overlay rather
        than about whether the window it lives in has been shown yet -- which is a
        different question, and the wrong one for deciding who closes whom.
        """
        parent = self.parentWidget()
        return self.isVisibleTo(parent) if parent is not None else self.isVisible()

    def _siblings(self) -> list[Overlay]:
        parent = self.parentWidget()
        if parent is None:
            return []
        return [child for child in parent.children()
                if isinstance(child, Overlay) and child is not self]

    def _is_topmost(self) -> bool:
        """Whether this is the sheet the player is actually looking at.

        Sibling order in `children()` is stacking order and `open()` raises, so the last
        visible overlay is the one on top. Only it paints a shade: two shades at 72% make
        one at 92%, which is the whole window a shade darker for no reason a player could
        name.
        """
        parent = self.parentWidget()
        if parent is None:
            return True
        up = [child for child in parent.children()
              if isinstance(child, Overlay) and child.showing()]
        return not up or up[-1] is self

    def _room_for_two(self) -> bool:
        """Whether this window can show two panels or only remember one.

        Below the dialog width a panel stops being a reading surface, so a window that
        cannot give both of them that much does not split. The pin still holds -- the
        older panel is underneath rather than dismissed, and closing the newer one brings
        it back -- it is a bookmark on a small screen and a comparison on a large one.
        """
        room = self.parentWidget() or self.window()
        if room is None or room is self:
            return False
        return (room.width() - 3 * PANEL_INSET) // 2 >= PANEL.dialog

    def _fit_to_window(self) -> None:
        """The single sizing rule: what was asked for, or what the window has.

        Every panel used to name its own width and none of them named a height, so the
        two tallest opened at exactly the window's height -- header cut off at the top,
        footer at the bottom, no margin at either end. A panel is a sheet *on* the
        window; it does not get to be the window. Half of one, when it is sharing.
        """
        for index, factor in ((0, 0 if self._dock == "left" else 1),
                              (2, 0 if self._dock == "right" else 1)):
            self._row.setStretch(index, factor)

        room = self.parentWidget() or self.window()
        inset = 2 * PANEL_INSET
        width = self.panel_width
        height = self.panel.maximumHeight()
        if room is not None and room is not self and room.width() > inset:
            available = room.width() - inset
            if self._dock is not None:
                available = (room.width() - 3 * PANEL_INSET) // 2
            width = min(width, max(PANEL.dialog // 2, available))
            height = max(200, room.height() - inset)
        self.panel.setFixedWidth(width)
        self.panel.setMaximumHeight(height)

    def _place_the_pin(self) -> None:
        """The pin straddles the panel's top edge, so it never lands on a heading.

        Inside the panel it would collide with whatever a subclass put in its top-right
        corner -- a status chip on the turn summary, a letter count on the
        correspondence. Above it, it would need clearance the tallest panels do not have.
        On the edge it needs half of each.
        """
        self._pin.setVisible(self.pinnable and self.showing())
        if not self._pin.isVisible():
            return
        self._pin.adjustSize()
        corner = self.panel.mapTo(self, self.panel.rect().topRight())
        self._pin.move(corner.x() - self._pin.width(),
                       corner.y() - self._pin.height() // 2)
        self._pin.raise_()

    def _hole_for(self, pinned) -> QRegion:
        top_left = pinned.panel.mapTo(self, pinned.panel.rect().topLeft())
        return QRegion(QRect(top_left, pinned.panel.size()))

    def _cut_a_hole_for_the_pinned_panel(self) -> None:
        """Let the sheet underneath stay lit, and stay clickable.

        This overlay covers the whole window, so without a hole in it the pinned panel
        would be dimmed by the newer one's shade and could not be clicked at all -- which
        would make the pin a way of leaving a picture of a panel on the screen rather
        than a way of comparing two of them.
        """
        pinned = self._pinned_sibling()
        if pinned is None or self.pinned or not self.showing():
            self.clearMask()
            return
        self.setMask(QRegion(self.rect()).subtracted(self._hole_for(pinned)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_to_window()
        self._place_the_pin()
        self._cut_a_hole_for_the_pinned_panel()

    def _maybe_close(self) -> None:
        if self.closable and self.showing():
            self.close_overlay()

    def mousePressEvent(self, event) -> None:
        # Only a click on the dim itself, not on the panel. **Asked of where the click
        # landed, not of who received it.** This used to assume a click on a child never
        # reaches here, and Qt says otherwise: a press a child *ignores* -- a label, the
        # panel's own background, an act in the search panel that still needs input -- is
        # passed up to its parent, and so arrived here as if it had landed on the dim, and
        # the panel the player was reading closed under their cursor.
        inside = self.panel.rect().contains(
            self.panel.mapFrom(self, event.position().toPoint()))
        if not inside:
            self._maybe_close()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        if not self._is_topmost():
            return
        painter = QPainter(self)
        pinned = self._pinned_sibling()
        if pinned is not None and not self.pinned:
            painter.setClipRegion(QRegion(self.rect()).subtracted(self._hole_for(pinned)))
        shade = QColor(self._theme.void if self._theme.is_dark else "#3A3527")
        shade.setAlphaF(0.72)
        painter.fillRect(self.rect(), shade)
        painter.end()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._pin.apply_theme(theme)
        self.panel.setStyleSheet(
            f"QFrame#overlayPanel {{ background: {theme.panel};"
            f" border: 1px solid {theme.rule}; }}"
        )
        self._sheet.setStyleSheet(scroll_qss(theme, horizontal=True))
        for surface in (self._sheet.viewport(), self._sheet_body):
            surface.setStyleSheet("background: transparent;")
        restyle(self.panel, theme)
        self.update()


# ---------------------------------------------------------------------------
# Turn summary
# ---------------------------------------------------------------------------


#: How many of *what changed for you* the summary leads with. Small on purpose: this sits
#: above the three columns, and a block that grew past a handful would push the turn's own
#: bookkeeping off the bottom of a panel that does not scroll. Whatever it cannot fit is
#: counted rather than dropped, and the dispatch is one press away underneath.
AFFECTED_SHOWN = 4


class _AffectedRow(PressFeedback, QFrame):
    """One thing that changed for this player this turn, and the way into why.

    The click goes to the first of :attr:`~atlas.engine.attention.AttentionItem
    .source_event_ids` -- the event the item was built on -- which is the inspector's own
    argument, so *what changed* and *why did that happen* are one press apart rather than a
    search away. An item with no source event is still shown; it simply is not a link.
    """

    event_selected = Signal(int)

    def __init__(self, item: AttentionItem, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event = item.source_event_ids[0] if item.source_event_ids else None
        self.setObjectName("affectedRow")
        if self._event is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Open what caused this")

        column = QVBoxLayout(self)
        column.setContentsMargins(12, 7, 12, 8)
        column.setSpacing(2)
        self._what = TextLabel(item.subject, style="body_small", colour="ink_bright",
                               theme=theme, wrap=True)
        column.addWidget(self._what)
        # Why it is in *this* block rather than in the columns below: the engine's own
        # sentence, unedited. A summary that said only what changed and never why is the
        # log the dispatch already is. It is the engine's reasoning ("salience 3 of 3"),
        # so it is shown with technical details and the click still opens the cause.
        self._why = TextLabel(item.reason_selected, style="mono_small", colour="ink_dim",
                              theme=theme, wrap=True)
        self._why.setVisible(technical())
        column.addWidget(self._why)

    def _takes_press(self, event) -> bool:
        return self._event is not None and event.button() == Qt.MouseButton.LeftButton

    def _fire(self) -> None:
        if self._event is not None:
            self.event_selected.emit(self._event)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"QFrame#affectedRow {{ background: {theme.inset};"
            f" border-left: 3px solid {theme.brass_line}; }}"
            + pressed_qss("QFrame#affectedRow", theme))
        self._what.apply_theme(theme)
        self._why.apply_theme(theme)


class SummaryOverlay(Overlay):
    """What affected *you*, and then what the turn did, in three columns.

    The three columns are the turn's bookkeeping -- what happened, what it cost, what was
    refused -- and they are true and they are undifferentiated. A player reading them has
    to work out for themselves which of nine lines was about their country, and the answer
    is already computed: :func:`atlas.engine.attention.attention_for` kept to the items
    whose ``novelty`` is above zero **is** *what changed for you this turn*, which is why
    the block above the columns is a filter on a field and not a second selection. So it
    leads, and the bookkeeping follows it.

    The rejected column is not hidden behind a disclosure. A model proposing something the
    engine would not allow is the single most informative thing that happens in a turn,
    and burying it is how a sandbox starts feeling arbitrary.
    """

    pinnable = False

    #: A province a headline named, to be shown on the map. Emitted only for a line that
    #: names exactly one province the player is entitled to see -- see
    #: `engine.naming.region_named_in`, which is strict for the reason given there.
    region_chosen = Signal(str)
    #: A line in *what affected you*, opened as the event it was built on. The window sends
    #: it to the inspector, which is already the answer to *why did that happen*.
    event_selected = Signal(int)
    #: The reader wants the whole turn rather than the summary of it. **This is where a
    #: turn digest has to be offered from**: the summary is the one screen a player is
    #: guaranteed to be looking at when a jump lands, and a page nobody is shown at the
    #: moment it is written is a page nobody reads. See `ui.newspaper`.
    dispatch_requested = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        head = QHBoxLayout()
        self._heading = TextLabel("", style="modal", colour="ink_bright", theme=theme)
        head.addWidget(self._heading)
        head.addStretch(1)
        self._status = Chip("", theme=theme, padding=(9, 5))
        head.addWidget(self._status)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.xs)

        self._sub = TextLabel("", style="prose", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._sub)
        # The world simulator's own account of the turn, which used to be thrown away.
        self.body.addSpacing(SPACE.sm)
        self._story = TextLabel("", style="prose", colour="ink_bright", theme=theme,
                                wrap=True)
        self._story.setVisible(False)
        self.body.addWidget(self._story)
        #: The provinces the player has pinned, as of the report being shown.
        self._watching: frozenset[str] = frozenset()
        self.body.addSpacing(SPACE.xl)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.lg)

        # **What affected you, above the bookkeeping.** Held in one widget so the whole
        # block -- heading, rows, count and rule -- appears and disappears together: a
        # standing heading over nothing would say a quiet turn had been quiet *about you*,
        # which is a different and unsupported claim.
        self._affected = QWidget()
        # A bare holder takes Qt's own palette rather than the panel's, which is invisible
        # on the dark theme and a pale rectangle on the light one. The dispatch page has
        # the same note against the same mistake.
        self._affected.setStyleSheet("background: transparent;")
        affected = QVBoxLayout(self._affected)
        affected.setContentsMargins(0, 0, 0, 0)
        affected.setSpacing(0)
        affected.addWidget(SectionLabel("What affected you", theme=theme))
        affected.addSpacing(10)
        self._affected_rows = QVBoxLayout()
        self._affected_rows.setContentsMargins(0, 0, 0, 0)
        self._affected_rows.setSpacing(0)
        affected.addLayout(self._affected_rows)
        self._affected_more = TextLabel("", style="mono_small", colour="ink_ghost",
                                        theme=theme, wrap=True)
        affected.addWidget(self._affected_more)
        affected.addSpacing(18)
        affected.addWidget(Divider(theme=theme))
        affected.addSpacing(18)
        self._affected.setVisible(False)
        self.body.addWidget(self._affected)

        # **The bookkeeping scrolls, and it did not used to.** Nine headlines already
        # filled this panel to the bottom of a 950-pixel window, so anything added above
        # them squeezed the column layout below its own minimum and the wrapped lines drew
        # on top of each other -- a real turn, photographed. Something had to give, and it
        # is the part a reader scrolls rather than the part that says what happened to
        # them. Nothing is dropped: the columns keep the ten entries they always kept.
        self._column_scroll = QScrollArea()
        self._column_scroll.setWidgetResizable(True)
        self._column_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._column_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._column_scroll.setMinimumHeight(300)
        self._column_holder = QWidget()
        columns = QHBoxLayout(self._column_holder)
        columns.setContentsMargins(0, 0, 12, 0)
        columns.setSpacing(0)
        self._columns: list[_SummaryColumn] = []
        self._dividers: list[QWidget] = []
        for index, title in enumerate(("What happened", "What it cost", "What was refused")):
            if index:
                divider = Divider(vertical=True, theme=theme)
                self._dividers.append(divider)
                columns.addWidget(divider)
                columns.addSpacing(22)
            column = _SummaryColumn(title, theme=theme)
            column.region_chosen.connect(self.region_chosen)
            self._columns.append(column)
            columns.addWidget(column, 1)
            if index < 2:
                columns.addSpacing(22)
        self._column_scroll.setWidget(self._column_holder)
        self.body.addWidget(self._column_scroll, 1)

        self.body.addSpacing(SPACE.xl)
        controls = QHBoxLayout()
        self._read = Chip("Read the dispatch", theme=theme, padding=(14, 7))
        self._read.clicked.connect(self.dispatch_requested)
        controls.addWidget(self._read)
        controls.addStretch(1)
        self._close = Chip("Continue", theme=theme, selected=True, padding=(14, 7))
        self._close.clicked.connect(self.close_overlay)
        controls.addWidget(self._close)
        self.body.addLayout(controls)

        self.apply_theme(theme)

    def show_report(
        self, report: JumpReport, headlines: list[str], theme: Theme | None = None,
        conn: sqlite3.Connection | None = None, world_id: str = "",
        viewer: str | None = None, watching: frozenset[str] = frozenset(),
    ) -> None:
        # The pinned provinces, for the ranking above the columns. Handed in for the reason
        # `atlas.ui.alerts.AlertsOverlay.load` gives: the watchlist lives beside the save.
        self._watching = watching
        theme = theme or self._theme
        # **Only what this reader may read, before any column is filled.** The report is
        # the referee's whole record and tags a tracked power's secret lines with their
        # audience; filtering once, here, keeps every column below -- the ranking and the
        # refusals alike -- from having to remember to.
        report = report.for_viewer(viewer if viewer is not None else report.viewer)
        abandoned = report.status is TurnStatus.ABORTED
        self._heading.setText(f"Turn {report.turn} · {_pretty(report.new_date)}")
        # **An abandoned turn did not happen, and must not be described as one that did.**
        # This line read "30 days passed. No power acted on its own initiative." for a jump
        # in which every provider call failed and no day was lived at all -- a sentence
        # that is written for a quiet month, next to a heading naming a turn the world
        # never reached. The chip beside it said `aborted`; nothing else on the panel did.
        if abandoned:
            self._sub.setText(
                "The month was not lived through. Nothing in it was kept, and the world "
                "still stands where it did."
            )
        else:
            # A turn in which a warhead went off should not open with how many days passed.
            opener = (
                "**Nuclear weapons were used this turn.**" if report.nuclear
                else f"{count(report.days_advanced)} days passed."
            )
            self._sub.setText(
                f"{opener} "
                + (", ".join(report.tracked_acted) + " acted for themselves."
                   if report.tracked_acted else "No power acted on its own initiative.")
            )
        # "sealed" is a finished turn; anything else means something did not resolve, and
        # the chip drops out of its brass state so that reads at a glance.
        self._status.set_text("sealed" if report.status is TurnStatus.SEALED
                              else str(report.status.value))
        self._status.set_selected(report.status is TurnStatus.SEALED)

        self._show_affected(conn, world_id, viewer, report.turn, theme)

        # **What happened, gravest first.** The ranking is the report's own -- see
        # `JumpReport.significant` -- so the console and this overlay cannot disagree
        # about what mattered. Event headlines fill in underneath rather than leading,
        # because they arrive in row order and a nuclear exchange should not have to
        # queue behind a minor's workshops.
        happened = report.significant()
        for headline in headlines:
            if len(happened) >= 9:
                break
            if headline not in happened:
                happened.append(headline)
        detail = technical()
        if not detail:
            me = None
            if conn is not None and world_id and viewer:
                row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                                   (world_id, viewer)).fetchone()
                me = str(row[0]) if row else None
            happened = list(dict.fromkeys(
                plain_line(line) for line in happened if not someone_elses_pocket(line, me)))
        story = str(getattr(report, "narrative", "") or "").strip()
        self._story.setText(story)
        self._story.setVisible(bool(story) and not abandoned)
        # **A headline that names one province becomes a way to look at it.** The reader
        # has the name in front of them and the map is behind the overlay; making them
        # retype it into the search box is the kind of small tax that adds up. Derived
        # rather than tagged: the engine's prose already contains the database's own names,
        # and `region_named_in` refuses anything but an unambiguous, unfogged single match.
        links: dict[str, str] = {}
        if conn is not None and world_id:
            from ..engine.naming import region_named_in

            for line in happened:
                named = region_named_in(conn, world_id, line, viewer=viewer)
                if named:
                    links[line] = named
        self._columns[0].set_entries(
            happened
            or (["The turn was abandoned before anything in it resolved."] if abandoned
                else ["The world was quiet."]),
            theme, style="prose", links=links,
        )

        # **What it cost, to the world rather than to the engine.** This column used to be
        # model calls and batch counts -- true, and telemetry rather than consequence. The
        # engine's own numbers are kept, last and in one line, because a turn that made
        # eight calls and applied nothing is still worth being able to see.
        cost: list[str] = []
        if report.regions_occupied:
            cost.append(f"{count(report.regions_occupied)} province(s) changed hands")
        if report.retreated or report.encircled:
            cost.append(f"{count(report.retreated)} formation(s) withdrew, "
                        f"{count(report.encircled)} were cut off")
        if report.in_arrears:
            cost.append("in arrears: " + ", ".join(report.in_arrears[:4]))
        if report.restive:
            cost.append(
                f"{count(len(report.restive))} power(s) dragged by ground they hold")
        if report.tracked_built:
            cost.append("laid something down: " + ", ".join(report.tracked_built[:4]))
        if report.world_investments:
            cost.append(f"{count(report.world_investments)} unattended power(s) invested")
        if report.promoted:
            cost.append("now tracked: " + ", ".join(report.promoted))
        if report.demoted:
            cost.append("no longer tracked: " + ", ".join(report.demoted))
        # **The turn's own bookkeeping, not the program's.** This line used to open with
        # the number of model calls the turn had made, which is a fact about what the
        # month cost to compute and about nothing that happened in it. The two figures
        # left are the world's: decisions that were carried into it, and what the record
        # now holds because of them.
        cost.append(
            f"{count(report.applied_batches)} decision"
            f"{'s' if report.applied_batches != 1 else ''} carried out, "
            f"{count(report.events_recorded)} entered in the record"
        )
        self._columns[1].set_title("What it cost" if detail else "Your orders")
        if detail:
            self._columns[1].set_entries(cost, theme)
        else:
            # **Your orders instead of the engine's bookkeeping**: each sentence you wrote,
            # and whether it was carried out -- the refusals that matter to a player are
            # these, and they are now beside the order that caused them.
            self._columns[1].set_entries(
                list(getattr(report, "your_orders", []) or [])
                or ["You gave no orders this turn."], theme, style="body_small",
                colour="ink")
        self._columns[2].setVisible(detail)
        self._dividers[1].setVisible(detail)

        # Individual writes refused inside a batch that otherwise stood belong here too,
        # and they did not used to appear anywhere at all: a partial apply reports as
        # applied, so the changes the rules threw out of it left no trace in the turn.
        refused = (
            list(report.rejections)
            + list(report.refused_diffs)
            + [f"failed: {f}" for f in report.failures]
        )
        self._columns[2].set_entries(
            refused or ["Nothing was refused."], theme,
            colour="danger" if refused else "ink_faint",
        )

    def _show_affected(self, conn: sqlite3.Connection | None, world_id: str,
                       viewer: str | None, turn: int, theme: Theme) -> None:
        """What changed for this player this turn, above everything else the turn did.

        **The whole list, then the filter -- not a shortened call.** ``limit`` cuts the
        ranking before anything here can look at it, so asking for the top four and then
        keeping the novel ones would silently answer a different question: four standing
        conditions would leave the block empty on a turn in which something genuinely
        changed. The read is per-turn and costs a few milliseconds; it happens once, here,
        as the turn lands.

        Nothing is scoped in this method. ``viewer`` goes to the substrate and the substrate
        is where the visibility predicate lives; a spectator has no *you* for the block to
        be about and gets no block at all, which is what an empty list already means.
        """
        clear_layout(self._affected_rows)
        if conn is None or not world_id or not viewer:
            self._affected.setVisible(False)
            return

        changed = [item for item in attention_for(
            conn, world_id, viewer, turn=turn, watching=self._watching)
            if item.novelty > 0]
        if not technical():
            # Another pair's war reaches this block because they border you; that is a
            # reason to watch, not something that happened to you.
            enemies = {str(row[0]) for row in conn.execute(
                "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
                " AND at_war = 1", (world_id, viewer))}
            changed = [item for item in changed
                       if item.source != "news" or not item.nation_id
                       or item.nation_id == viewer or item.nation_id in enemies]
        self._affected.setVisible(bool(changed))
        if not changed:
            return

        for item in changed[:AFFECTED_SHOWN]:
            row = _AffectedRow(item, theme=theme)
            row.event_selected.connect(self.event_selected)
            row.apply_theme(theme)
            self._affected_rows.addWidget(row)
            self._affected_rows.addSpacing(7)
        # Counted rather than dropped, like every other truncation in the interface: a
        # block that quietly stopped at four would read as "four things changed".
        rest = len(changed) - AFFECTED_SHOWN
        self._affected_more.setText(
            f"and {rest} more that changed for you, in the dispatch." if rest > 0
            else "Each of these opens what caused it.")
        self._affected_more.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every label; a scroll area's viewport and a bare
        layout holder it does not -- both take Qt's own palette, which is invisible on the
        dark theme and a pale rectangle on the light one."""
        super().apply_theme(theme)
        self._theme = theme
        self._column_scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._column_scroll.viewport(), self._column_holder):
            surface.setStyleSheet("background: transparent;")


class _SummaryColumn(QWidget):
    #: A province one of these lines names, chosen by clicking the line.
    region_chosen = Signal(str)

    def __init__(self, title: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._title = SectionLabel(title, theme=theme)
        layout.addWidget(self._title)

        self._entries = QVBoxLayout()
        self._entries.setSpacing(9)
        layout.addLayout(self._entries)
        layout.addStretch(1)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_entries(self, entries: list[str], theme: Theme, *,
                    style: str = "body_small", colour: str = "ink_dim",
                    links: dict[str, str] | None = None) -> None:
        """``links`` maps an entry to a province it names, making that line clickable."""
        while self._entries.count():
            item = self._entries.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for entry in entries[:10]:
            region_id = (links or {}).get(entry, "")
            label = TextLabel(entry, style=style,
                              colour="info" if region_id else colour,
                              theme=theme, wrap=True)
            if region_id:
                # Coloured *and* cursored, because colour alone is a claim a person who
                # cannot see it never receives -- the same argument the colour-safe palette
                # is built on, one screen over.
                label.setCursor(Qt.CursorShape.PointingHandCursor)
                label.setToolTip("Show this on the map")
                label.mousePressEvent = (  # type: ignore[method-assign]
                    lambda _event, target=region_id: self.region_chosen.emit(target))
            self._entries.addWidget(label)

    def apply_theme(self, theme: Theme) -> None:
        restyle(self, theme)


# ---------------------------------------------------------------------------
# Confirming what cannot be taken back
# ---------------------------------------------------------------------------


class ConfirmOverlay(Overlay):
    """The last look at the orders in this turn that cannot be undone.

    **It names them, one line each.** "Three of these cannot be undone" is not something a
    person can act on -- *which* three is -- and a confirmation that says only "are you
    sure?" asks nothing at all. Every line here is the act, its target, and the sentence
    from the catalogue saying what the turn will not give back.

    Shown only for the grave ones. A confirmation on an ordinary act is how somebody learns
    to click through confirmations, and after that the one that mattered goes past with the
    rest of them.
    """

    pinnable = False

    #: The player looked and meant it.
    confirmed = Signal()
    #: The player backed out -- *Go back*, Escape, a click on the dim, or another sheet
    #: opening over this one. Every door but *Spend the turn* comes here, so the window
    #: can forget it was waiting: it used to remember, and the next press of Advance went
    #: straight through with the question never asked.
    declined = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)

        self._heading = TextLabel("Before this turn is spent", style="modal",
                                  colour="ink_bright", theme=theme, wrap=True)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.xs)
        self.body.addWidget(TextLabel(
            "These orders do things the next turn will not give back.",
            style="prose", colour="ink_dim", theme=theme, wrap=True))
        self.body.addSpacing(SPACE.lg)

        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._lines = QVBoxLayout(holder)
        self._lines.setContentsMargins(0, 0, 0, 0)
        self._lines.setSpacing(12)
        self.body.addWidget(holder)
        self.body.addSpacing(SPACE.xl)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._back = Chip("Go back", theme=theme, padding=(12, 6))
        self._back.clicked.connect(self._decline)
        self._go = Chip("Spend the turn", theme=theme, selected=True, padding=(14, 6))
        self._go.clicked.connect(self._accept)
        controls.addWidget(self._back)
        controls.addWidget(self._go)
        self.body.addLayout(controls)
        self.apply_theme(theme)

    def show_confirmations(self, found: list) -> None:
        while self._lines.count():
            item = self._lines.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for confirmation in found:
            row = QVBoxLayout()
            row.setSpacing(3)
            row.addWidget(TextLabel(confirmation.what, style="row", colour="ink_bright",
                                    theme=self._theme, wrap=True))
            row.addWidget(TextLabel(confirmation.because, style="prose",
                                    colour="danger", theme=self._theme, wrap=True))
            wrapper = QWidget()
            wrapper.setLayout(row)
            self._lines.addWidget(wrapper)
        self.apply_theme(self._theme)

    def _accept(self) -> None:
        self.close_overlay()
        self.confirmed.emit()

    def _decline(self) -> None:
        self.close_overlay()
        self.declined.emit()

    def _maybe_close(self) -> None:
        """Escape, the dim and a sibling opening all mean *Go back*, by the same door."""
        if self.closable and self.showing():
            self._decline()


# ---------------------------------------------------------------------------
# Rewind
# ---------------------------------------------------------------------------


class RewindOverlay(Overlay):
    """Pick a turn to go back to, and see what that costs before committing.

    The impact preview is the whole point. "Rewind to turn 12" is meaningless until you
    know it discards eight turns, four hundred changes and a war.
    """

    pinnable = False

    confirmed = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._target: int | None = None

        self._heading = TextLabel("Rewind", style="modal", colour="ink_bright", theme=theme)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.xs)
        self.body.addWidget(
            TextLabel(
                "History is undone by reversing every recorded change, newest first. "
                "Anything after the chosen turn is gone for good.",
                style="prose", colour="ink_dim", theme=theme, wrap=True,
            )
        )
        self.body.addSpacing(SPACE.lg)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setFixedHeight(280)
        self._area.setStyleSheet(scroll_qss(theme))
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._turns = QVBoxLayout(holder)
        self._turns.setContentsMargins(0, 0, 12, 0)
        self._turns.setSpacing(8)
        self._area.setWidget(holder)
        self.body.addWidget(self._area)

        self.body.addSpacing(SPACE.lg)
        self._impact = TextLabel("Choose a turn.", style="body_small", colour="ink_faint",
                                 theme=theme, wrap=True)
        self.body.addWidget(self._impact)
        self.body.addSpacing(SPACE.lg)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._cancel = Chip("Keep this history", theme=theme, padding=(12, 6))
        self._cancel.clicked.connect(self.close_overlay)
        self._confirm = Chip("Rewind", theme=theme, selected=True, padding=(14, 6))
        self._confirm.clicked.connect(self._confirm_clicked)
        controls.addWidget(self._cancel)
        controls.addWidget(self._confirm)
        self.body.addLayout(controls)

        self._cards: list[_TurnCard] = []
        self.apply_theme(theme)

    def load(self, conn: sqlite3.Connection, world_id: str) -> None:
        self._conn, self._world_id = conn, world_id
        self._target = None
        self._impact.setText("Choose a turn.")
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        while self._turns.count():
            item = self._turns.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = conn.execute(
            """
            SELECT t.number AS turn,
                   COALESCE(t.end_date, t.start_date) AS date,
                   t.status,
                   (SELECT COUNT(*) FROM event e
                     WHERE e.world_id = t.world_id AND e.turn = t.number) AS events
              FROM turn t WHERE t.world_id = ?
             ORDER BY t.number DESC LIMIT 60
            """,
            (world_id,),
        ).fetchall()
        for row in rows:
            card = _TurnCard(row, theme=self._theme)
            card.chosen.connect(lambda turn=int(row["turn"]): self._select(turn))
            self._cards.append(card)
            self._turns.addWidget(card)
        self._turns.addStretch(1)

    def _select(self, turn: int) -> None:
        from ..state.replay import can_rewind

        self._target = turn
        for card in self._cards:
            card.set_selected(card.turn == turn, self._theme)
        if self._conn is None:
            return

        allowed, reason = can_rewind(self._conn, self._world_id, turn)
        if not allowed:
            self._impact.setText(reason)
            return
        diffs, events = self._conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM diff d JOIN diff_batch b ON b.id = d.batch_id
                WHERE b.world_id = ? AND b.turn > ? AND b.status = 'applied'),
              (SELECT COUNT(*) FROM event WHERE world_id = ? AND turn > ?)
            """,
            (self._world_id, turn, self._world_id, turn),
        ).fetchone()
        current = int(
            self._conn.execute(
                "SELECT turn_number FROM world WHERE id = ?", (self._world_id,)
            ).fetchone()[0]
        )
        self._impact.setText(
            f"Undoes {current - turn} turn(s): {diffs} change(s) reversed and "
            f"{count(events)} event(s) erased. This cannot itself be undone."
        )

    def _confirm_clicked(self) -> None:
        if self._target is not None:
            self.confirmed.emit(self._target)
            self.close_overlay()

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        for card in self._cards:
            card.apply_theme(theme)


class _TurnCard(QFrame):
    chosen = Signal()

    def __init__(self, row, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.turn = int(row["turn"])
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(14)
        self._turn = TextLabel(f"T{self.turn}", style="num", colour="brass", theme=theme)
        self._turn.setFixedWidth(38)
        self._date = TextLabel(_pretty(row["date"]), style="row_small", colour="ink", theme=theme)
        self._detail = TextLabel(f"{count(row['events'])} events · {row['status']}",
                                 style="mono_small", colour="ink_ghost", theme=theme)
        layout.addWidget(self._turn)
        layout.addWidget(self._date, 1)
        layout.addWidget(self._detail)
        self.apply_theme(theme)

    def set_selected(self, selected: bool, theme: Theme) -> None:
        self._selected = selected
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        border = theme.brass_line if self._selected else theme.rule_soft
        background = theme.brass_wash if self._selected else theme.inset
        self.setStyleSheet(
            f"_TurnCard {{ border: 1px solid {border}; background: {background}; }}"
            f"_TurnCard:hover {{ border-color: {theme.rule_hover}; }}"
        )
        for child in (self._turn, self._date, self._detail):
            child.apply_theme(theme)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Going back one turn
# ---------------------------------------------------------------------------


class UndoTurnOverlay(Overlay):
    """Undo the turn just played, which is the rewind with the turn already chosen.

    The engine has been able to do this exactly since the diff-first design existed:
    :func:`atlas.state.replay.rewind_to` walks the recorded changes backwards and puts each
    field back, which is exact by construction. `RewindOverlay` has offered it for as long,
    as a list of sixty turns with an impact preview under it.

    That is the right screen for *going back to somewhere*. It is the wrong screen for the
    thing a player actually wants nine times out of ten, which is **that turn, again** --
    the jump has landed, the summary says something went badly, and the question is not
    which of sixty turns to return to. Making them choose the answer they already have is
    what turns a one-press idea into a decision.

    So this is one press and one confirmation, and the confirmation is the whole reason it
    is not a bare button: rewind cannot itself be undone, and what is about to be discarded
    has to be named before it goes. It names the turn, its date, what it costs in changes
    and events, and the first few things that happened in it, because *undoes 412 changes*
    is not something a person can weigh and *Russia declares war on Ukraine* is.

    **The path is unchanged.** This chooses `current - 1` and emits it; the window calls
    the same `_do_rewind` the turn list calls. There is no second rewind here.
    """

    pinnable = False

    #: The turn to go back to. Same shape as :class:`RewindOverlay`, into the same slot.
    confirmed = Signal(int)

    #: How many of the turn's events to name. Enough to recognise the turn by; not so many
    #: that the confirmation becomes a screen to read.
    NAMED = 4

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)
        self._target: int | None = None

        self.body.addWidget(SectionLabel("Go back a turn", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._heading = TextLabel("", style="modal", colour="ink_bright", theme=theme)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.sm)
        self._sub = TextLabel("", style="prose", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._sub)
        #: The provinces the player has pinned, as of the report being shown.
        self._watching: frozenset[str] = frozenset()
        self.body.addSpacing(SPACE.lg)

        self._what = QVBoxLayout()
        self._what.setSpacing(6)
        self.body.addLayout(self._what)
        self.body.addSpacing(SPACE.lg)

        self._impact = TextLabel("", style="body_small", colour="danger", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._impact)
        self.body.addSpacing(SPACE.lg)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._cancel = Chip("Keep this turn", theme=theme, padding=(12, 6))
        self._cancel.clicked.connect(self.close_overlay)
        self._go = Chip("Go back a turn", theme=theme, selected=True, padding=(14, 6))
        self._go.clicked.connect(self._confirm_clicked)
        controls.addWidget(self._cancel)
        controls.addWidget(self._go)
        self.body.addLayout(controls)
        self.apply_theme(theme)

    # -- data --------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str) -> None:
        """Read what going back one turn would cost, and say it in words."""
        from ..state.replay import can_rewind

        # The shared helper, not a hand-rolled loop: `item.widget()` stops answering once
        # the widget has been reparented, so clearing a layout by calling it three times
        # in a row raises on the third.
        clear_layout(self._what)

        current = int(conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (world_id,)
        ).fetchone()["turn_number"])
        self._target = current - 1

        allowed, reason = can_rewind(conn, world_id, self._target)
        if not allowed:
            # The world has not been played yet. Says so, rather than offering a button
            # that would be refused by the engine a moment later.
            self._target = None
            self._heading.setText("There is no turn to go back from")
            self._sub.setText(reason)
            self._impact.setText("")
            self._go.setVisible(False)
            self._cancel.set_text("Close")
            return

        self._go.setVisible(True)
        self._cancel.set_text("Keep this turn")
        # The *target* turn's date, not this one's. The sentence says where the world
        # will be standing, and the day the current turn ended is the day it is standing
        # on now -- printing that beside "goes back to the end of turn 13" would name the
        # date it is leaving as the date it is going to.
        row = conn.execute(
            "SELECT COALESCE(end_date, start_date) AS date FROM turn"
            " WHERE world_id = ? AND number = ?", (world_id, self._target)).fetchone()
        self._heading.setText(f"Undo turn {current}")
        self._sub.setText(
            f"The world goes back to the end of turn {self._target}"
            + (f", {_pretty(row['date'])}" if row and row["date"] else "")
            + ". Every change that turn made is reversed, newest first, and everything it "
              "recorded is erased. Your orders for it are not restored."
        )

        diffs, events = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM diff d JOIN diff_batch b ON b.id = d.batch_id
                WHERE b.world_id = ?1 AND b.turn > ?2 AND b.status = 'applied'),
              (SELECT COUNT(*) FROM event WHERE world_id = ?1 AND turn > ?2)
            """,
            (world_id, self._target),
        ).fetchone()
        self._impact.setText(
            f"{count(diffs)} change(s) reversed and {count(events)} event(s) erased. Going back cannot "
            f"itself be undone."
        )

        # Ranked by the engine's own salience, not by recency. Which four of sixty-nine
        # events a person needs in order to recognise the turn is a question the codebase
        # has already answered once, and the answer is not "the last four written": a
        # declaration of war is the first thing that happens in the turn it happens in.
        # The pre-filter is a bounded multiple, exactly as `_ranked_window` does it.
        recent = [dict(row) for row in conn.execute(
            "SELECT id, turn, kind, text FROM event WHERE world_id = ? AND turn > ?"
            " ORDER BY id DESC LIMIT 200", (world_id, self._target)).fetchall()]
        named = by_salience(recent)[:self.NAMED]
        if named:
            self._what.addWidget(SectionLabel("What is discarded", theme=self._theme))
            for event in named:
                self._what.addWidget(TextLabel(
                    str(event["text"]), style="prose", colour="ink_body",
                    theme=self._theme, wrap=True))
            if int(events) > len(named):
                self._what.addWidget(TextLabel(
                    f"…and {int(events) - len(named)} more.", style="mono_small",
                    colour="ink_ghost", theme=self._theme))

    # -- internals ---------------------------------------------------------

    def _confirm_clicked(self) -> None:
        if self._target is None:
            return
        self.close_overlay()
        self.confirmed.emit(self._target)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportOverlay(Overlay):
    """Write a quiescent copy of the world somewhere the player can keep it.

    Worth an overlay rather than a menu item because the explanation matters: the live
    database deliberately does not live in the synced folder, so "where is my save" has an
    answer that needs saying once.
    """

    pinnable = False

    export_requested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)

        self._heading = TextLabel("Export a copy", style="modal", colour="ink_bright", theme=theme)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.xs)
        self.body.addWidget(
            TextLabel(
                "The live world runs outside your synced folders, because a sync client "
                "copying a database's sidecar files mid-write is how saves get corrupted. "
                "An export is a single settled file you can put anywhere.",
                style="prose", colour="ink_dim", theme=theme, wrap=True,
            )
        )
        self.body.addSpacing(SPACE.lg)

        self._destination = TextLabel("", style="mono", colour="ink_faint", theme=theme, wrap=True)
        self.body.addWidget(self._destination)
        self.body.addSpacing(SPACE.xl)

        self._result = TextLabel("", style="body_small", colour="good", theme=theme, wrap=True)
        self._result.setVisible(False)
        self.body.addWidget(self._result)
        self.body.addSpacing(SPACE.xs)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._cancel = Chip("Close", theme=theme, padding=(12, 6))
        self._cancel.clicked.connect(self.close_overlay)
        self._go = Chip("Export", theme=theme, selected=True, padding=(14, 6))
        self._go.clicked.connect(lambda: self.export_requested.emit(self._path))
        controls.addWidget(self._cancel)
        controls.addWidget(self._go)
        self.body.addLayout(controls)

        self._path = ""
        self.apply_theme(theme)

    def set_destination(self, path: str) -> None:
        self._path = path
        self._destination.setText(path)
        self._result.setVisible(False)

    def show_result(self, message: str, *, ok: bool = True) -> None:
        self._result.setText(message)
        self._result.set_colour("good" if ok else "danger", self._theme)
        self._result.setVisible(True)


def _pretty(iso: str) -> str:
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"
