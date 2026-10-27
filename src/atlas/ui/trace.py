"""Why did that happen? A number, and the chain of events that put it where it is.

The top bar has always carried a delta -- *stability 46, down 4* -- and a delta is half a
sentence. The other half has been in the database since schema 1 and has never been on
screen: the diff that moved the number cites the event that caused it, and that event names
its own causes. Following the second from the first turns *down 4* into *down 4, because of
unrest in the west, which is there because you annexed it in April*.

**This reads; it does not compute.** Everything on this screen comes out of
:func:`atlas.engine.causality.trace_of`, which is a join and a walk over rows the engine
already wrote. Nothing here is stored, nothing is derived twice, and opening the panel
cannot change a save.

**Scoped to the player, not to the referee.** The inspector one screen over is opened by
whoever holds the file and shows everything in it; this is opened by a government, and a
causal chain is exactly the sort of thing that leads back to a covert act. So the walk takes
a viewer and stops where that viewer's entitlement stops -- and *says* it stopped, because a
chain that quietly ends at a secret cannot be told apart from a chain that ended.

Every event on the ladder is clickable and hands off to the inspector, which is where the
question *what else did that do* is already answered.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.causality import (
    CausalStrand,
    CausalTrace,
    latest_move_turn,
    trace_of,
    trace_of_event,
)
from ..engine.snapshot import METERS
from ..humanize import signed
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
    scroll_qss,
)

__all__ = ["TraceOverlay"]

#: How wide one link's text may run before it is cut. The ladder is a shape to read at a
#: glance; the full sentence is one click away in the inspector.
_LINK_TEXT = 170


def _shorten(text: str, limit: int = _LINK_TEXT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _LinkRow(PressFeedback, QFrame):
    """One rung of the ladder: an event, at a depth, that you can open."""

    clicked = Signal(int)

    def __init__(self, link, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._link = link
        self.setObjectName("linkRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open this event in the inspector")

        row = QHBoxLayout(self)
        # Indented by depth, so the shape of the chain is legible before a word of it is
        # read. Bounded: a deep chain that kept indenting would walk off the panel.
        row.setContentsMargins(10 + 18 * min(link.depth, 5), 6, 10, 7)
        row.setSpacing(9)

        self._arrow = TextLabel(
            "←" if link.depth else "●", style="mono", colour="brass",
            theme=theme)
        self._arrow.setFixedWidth(12)
        row.addWidget(self._arrow, 0, Qt.AlignmentFlag.AlignTop)

        stack = QVBoxLayout()
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)
        self._text = TextLabel(
            _shorten(link.event.text) or f"({link.event.kind})", style="prose",
            colour="ink_bright" if not link.depth else "ink_body", theme=theme, wrap=True)
        self._meta = TextLabel(
            f"#{link.event.event_id}  turn {link.event.turn}  {link.event.kind}"
            + (f"  ·  {link.event.actor}" if link.event.actor else ""),
            style="mono_small", colour="ink_ghost", theme=theme)
        stack.addWidget(self._text)
        stack.addWidget(self._meta)
        row.addLayout(stack, 1)

    def _fire(self) -> None:
        self.clicked.emit(self._link.event.event_id)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#linkRow {{ background: transparent;"
            f" border-left: 1px solid {theme.rule_soft}; }}"
            f"QFrame#linkRow:hover {{ background: {theme.raised}; }}"
            + pressed_qss("QFrame#linkRow", theme)
        )
        for child in (self._arrow, self._text, self._meta):
            child.apply_theme(theme)


class _StrandBlock(QWidget):
    """One reason, its size, and the chain behind it."""

    event_selected = Signal(int)

    def __init__(self, strand: CausalStrand, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kids: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(12)
        if strand.amount is not None:
            amount = TextLabel(signed(strand.amount, places=1), style="num_large",
                               colour="good" if strand.amount > 0 else "danger",
                               theme=theme)
            amount.setFixedWidth(74)
            self._kids.append(amount)
            head.addWidget(amount)
        if strand.detail:
            detail = TextLabel(strand.detail, style="num", colour="ink_dim", theme=theme)
            self._kids.append(detail)
            head.addWidget(detail)
        head.addStretch(1)
        if strand.amount is not None or strand.detail:
            holder = QWidget()
            holder.setLayout(head)
            layout.addWidget(holder)

        for link in strand.links:
            row = _LinkRow(link, theme=theme)
            row.clicked.connect(self.event_selected)
            self._kids.append(row)
            layout.addWidget(row)

        if not strand.links:
            self._add(layout, TextLabel(
                "The change cites an event this world no longer holds — a rewind "
                "removed it and left the change behind.",
                style="body_small", colour="ink_ghost", theme=theme, wrap=True))
        if strand.hidden_upstream:
            # Named rather than omitted. The whole value of a causal reader is that it
            # cannot quietly stop; a chain that ends at a secret must look different from
            # one that ends.
            self._add(layout, TextLabel(
                "← the trail goes further back, into something your services have "
                "not established.",
                style="body_small", colour="brass", theme=theme, wrap=True))

    def _add(self, layout: QVBoxLayout, widget: QWidget) -> None:
        self._kids.append(widget)
        layout.addWidget(widget)

    def apply_theme(self, theme: Theme) -> None:
        for child in self._kids:
            child.apply_theme(theme)


class TraceOverlay(Overlay):
    """The causal chain behind one value, or behind one event."""

    #: An event the reader wants the full picture of. The window opens the inspector.
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._conn: sqlite3.Connection | None = None
        self._world = ""
        self._viewer: str | None = None
        self._subject = ""
        self._field = ""
        self._turn = 0
        self._target = ""
        #: The turn the panel had to reach back to, when this turn held still. 0 when it
        #: is showing the turn it was asked for.
        self._fell_back = 0
        self._field_chips: list[tuple[str, Chip]] = []

        self.body.addWidget(SectionLabel("Why did that happen?", theme=theme))
        self.body.addSpacing(SPACE.sm)

        self._heading = TextLabel("", style="modal", colour="ink_bright", theme=theme,
                                  wrap=True)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.xs)
        self._movement = TextLabel("", style="num_large", colour="ink_dim", theme=theme)
        self.body.addWidget(self._movement)
        self.body.addSpacing(SPACE.md)

        # The other five meters, so the panel is a place to read the turn rather than a
        # dead end that has to be closed and re-opened from the top bar.
        self._chip_row = QHBoxLayout()
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(6)
        self._chips = QWidget()
        self._chips.setLayout(self._chip_row)
        self.body.addWidget(self._chips)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.md)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(430)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel(
            "Every line is an event — click one to see everything it changed. "
            "Escape to close.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None, *,
             table: str, target: str, field: str, turn: int, subject: str = "") -> None:
        """Explain one field of one row, over one turn."""
        self._conn = conn
        self._world = world_id
        self._viewer = viewer
        self._subject = subject or target
        self._target = target
        self._field = field
        self._turn = turn
        self._show_field_chips(table, target)
        title = f"{self._subject} · {_pretty_field(field)}"
        found = trace_of(conn, world_id, table=table, target=target, field=field,
                         turn=turn, viewer=viewer, title=title)
        # **A figure that held still this turn is not a figure with no explanation.** Six
        # meters are on the top bar and on a quiet turn most of them did not move, so a
        # panel that answered "nothing happened" to five clicks in six would be a panel
        # nobody clicked twice. Fall back to the last turn that did move it, and say so.
        self._fell_back = 0
        if not found.changed:
            earlier = latest_move_turn(conn, world_id, table=table, target=target,
                                       field=field, upto=turn)
            if earlier is not None and earlier != turn:
                found = trace_of(conn, world_id, table=table, target=target, field=field,
                                 turn=earlier, viewer=viewer, title=title)
                self._fell_back = earlier
        self._render(found)

    def load_event(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
                   event_id: int) -> None:
        """Explain one event: what is recorded behind it."""
        self._fell_back = 0
        self._conn = conn
        self._world = world_id
        self._viewer = viewer
        self._field = ""
        self._show_field_chips("", "")
        found = trace_of_event(conn, world_id, event_id, viewer)
        if found is None:
            self._heading.setText("There is nothing here you are entitled to read.")
            self._movement.setText("")
            clear_layout(self._stack)
            self.apply_theme(self._theme)
            return
        self._turn = found.turn
        self._render(found)

    def _show_field_chips(self, table: str, target: str) -> None:
        """The six meters, when the subject is a nation. Nothing otherwise."""
        clear_layout(self._chip_row)
        self._field_chips.clear()
        self._chips.setVisible(table == "nation" and bool(target))
        if table != "nation" or not target:
            return
        for key, label, _ceiling in METERS:
            chip = Chip(label, theme=self._theme, selected=key == self._field)
            chip.clicked.connect(
                lambda field=key: self._retrace(field))
            self._field_chips.append((key, chip))
            self._chip_row.addWidget(chip)
        self._chip_row.addStretch(1)

    def _retrace(self, field: str) -> None:
        if self._conn is None or not self._target:
            return
        self.load(self._conn, self._world, self._viewer, table="nation",
                  target=self._target, field=field, turn=self._turn,
                  subject=self._subject)

    # -- rendering -----------------------------------------------------------

    def _render(self, trace: CausalTrace) -> None:
        theme = self._theme
        self._heading.setText(trace.title or "—")

        # "— → —, this turn" is noise on a figure nothing has ever moved. The sentence
        # underneath already says it, and better.
        self._movement.setVisible(trace.changed or not trace.field)
        if trace.field:
            when = (f"   (turn {trace.turn}, the last turn it moved)"
                    if self._fell_back else "   (this turn)")
            moved = f"   {signed(trace.moved, places=1)}" if (
                trace.moved is not None and abs(trace.moved) >= 0.05) else ""
            self._movement.setText(f"{trace.before} → {trace.after}{moved}{when}")
        else:
            rungs = sum(len(strand.links) for strand in trace.strands)
            self._movement.setText(
                f"turn {trace.turn}  ·  "
                + (f"{rungs} events on the chain" if rungs != 1
                   else "nothing upstream was recorded"))

        clear_layout(self._stack)

        if not trace.changed:
            self._add(TextLabel(
                f"Nothing has moved this figure at or before turn {trace.turn}. It is "
                f"where the world began.",
                style="body", colour="ink_dim", theme=theme, wrap=True))
            self._stack.addStretch(1)
            self.apply_theme(theme)
            return

        for index, strand in enumerate(trace.strands):
            if index:
                self._add(Divider(theme=theme))
                self._stack.addSpacing(14)
            block = _StrandBlock(strand, theme=theme)
            block.event_selected.connect(self.event_selected)
            self._add(block)
            self._stack.addSpacing(14)

        if trace.withheld:
            # The parts stop summing to the whole, and that is the honest reading rather
            # than a bug: something moved this number that this government cannot see.
            self._add(TextLabel(
                f"{trace.withheld} further change to this figure was made by something "
                f"your services have not established."
                if trace.withheld == 1 else
                f"{trace.withheld} further changes to this figure were made by things "
                f"your services have not established.",
                style="body_small", colour="brass", theme=theme, wrap=True))

        self._stack.addStretch(1)
        self.apply_theme(theme)

    def _add(self, widget: QWidget) -> None:
        self._stack.addWidget(widget)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every chip and block; the bare holders it cannot.

        A layout-holder ``QWidget`` and a scroll viewport take Qt's palette rather than
        the panel's, which is invisible on the dark theme and a pale rectangle on the
        light one.
        """
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner, self._chips):
            surface.setStyleSheet("background: transparent;")


def _pretty_field(field: str) -> str:
    """``military_strength`` as a person would say it."""
    return field.replace("_", " ").strip().capitalize() if field else ""
