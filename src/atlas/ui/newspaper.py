"""The turn, read as a page rather than scrolled as a log.

A month of the Modern Day map writes about a hundred and fifty events. They are all in the
left rail, in the order the engine wrote them, and the declaration of war is between two
completed workshops and an air-superiority line. Nothing is missing and nothing is findable.

So this lays the same turn out the way a foreign ministry would put it on a desk: the one
thing the month will be remembered for at the top, a strip of counted facts under it, and
then sections a reader already has names for -- what the fighting did, who signed what, who
moved against whom, what changed hands.

**It renders; it does not decide.** Every line, every heading and every ranking comes from
:func:`atlas.engine.dispatch.dispatch_for`, which is a read over events and diffs the engine
already wrote, scoped to what this government was actually told. This file chooses type
sizes.

Every line is one click from the inspector, because *what else did that do* is the next
question and it already has a screen.
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

from ..engine.dispatch import Dispatch, Item, Section, dispatch_for
from ..humanize import count
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Divider,
    PressFeedback,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
    scroll_qss,
)

__all__ = ["NewspaperOverlay"]

#: Sections that go in the wide column on the left. The rest fill the narrow one, so the
#: page reads as a page rather than as one long strip -- and the split is by *what the
#: reader came for*, not by length.
_LEAD_COLUMN = (
    "War and peace", "Ground that changed hands", "The fighting",
    "Who moved against whom",
)


def _pretty_date(iso: str) -> str:
    """``2026-01-31`` becomes ``31 January 2026``, as the rest of the interface writes it."""
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


class _Line(PressFeedback, QFrame):
    """One line of the report, and a way into what it did."""

    clicked = Signal(int)

    def __init__(self, item: Item, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self.setObjectName("dispatchLine")
        if item.event_id is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Open this in the inspector")

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 5, 8, 6)
        row.setSpacing(9)
        # A rule the width of the line's gravity. Colour alone would be a claim a reader
        # who cannot see it never receives, which is the argument the colour-safe palette
        # is built on; the mark is a shape as well as a hue.
        self._mark = TextLabel(
            "◆" if item.weight >= 3 else ("●" if item.weight == 2 else "·"),
            style="num",
            colour="danger" if item.weight >= 3 else (
                "brass" if item.weight == 2 else "ink_ghost"),
            theme=theme)
        self._mark.setFixedWidth(16)
        row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        self._text = TextLabel(item.text, style="prose", colour="ink_body", theme=theme,
                               wrap=True)
        row.addWidget(self._text, 1)

    def _takes_press(self, event) -> bool:
        return (self._item.event_id is not None
                and event.button() == Qt.MouseButton.LeftButton)

    def _fire(self) -> None:
        if self._item.event_id is not None:
            self.clicked.emit(self._item.event_id)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            "QFrame#dispatchLine { background: transparent; }"
            f"QFrame#dispatchLine:hover {{ background: {theme.raised}; }}"
            + pressed_qss("QFrame#dispatchLine", theme)
        )
        self._mark.apply_theme(theme)
        self._text.apply_theme(theme)


class _SectionBlock(QWidget):
    """One heading and the lines under it."""

    event_selected = Signal(int)

    def __init__(self, section: Section, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kids: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        head = SectionLabel(section.title, theme=theme)
        self._kids.append(head)
        layout.addWidget(head)
        layout.addSpacing(4)

        for item in section.items:
            line = _Line(item, theme=theme)
            line.clicked.connect(self.event_selected)
            self._kids.append(line)
            layout.addWidget(line)

        if section.more:
            # Named rather than dropped. A section that quietly truncates reads as a
            # section that was complete, and the left rail is where the rest already is.
            rest = TextLabel(
                f"and {count(section.more)} more, in the event log.",
                style="mono_small", colour="ink_ghost", theme=theme)
            self._kids.append(rest)
            layout.addSpacing(3)
            layout.addWidget(rest)

    def apply_theme(self, theme: Theme) -> None:
        for child in self._kids:
            child.apply_theme(theme)


class NewspaperOverlay(Overlay):
    """The dispatch: one turn of the world, laid out to be read."""

    #: A line the reader wants the whole picture of. The window opens the inspector.
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.screen)

        self.body.addWidget(SectionLabel("The dispatch", theme=theme))
        self.body.addSpacing(SPACE.sm)

        head = QHBoxLayout()
        head.setSpacing(16)
        self._date = TextLabel("", style="title", colour="ink_bright", theme=theme)
        head.addWidget(self._date)
        head.addStretch(1)
        self._turn = TextLabel("", style="num", colour="brass", theme=theme)
        head.addWidget(self._turn, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.sm)

        self._lede = TextLabel("", style="prose", colour="ink", theme=theme, wrap=True)
        self.body.addWidget(self._lede)
        self.body.addSpacing(SPACE.md)

        self._tally_row = QHBoxLayout()
        self._tally_row.setContentsMargins(0, 0, 0, 0)
        self._tally_row.setSpacing(26)
        self._tallies = QWidget()
        self._tallies.setLayout(self._tally_row)
        self.body.addWidget(self._tallies)
        self.body.addSpacing(SPACE.md)

        self._standing = TextLabel("", style="prose_italic", colour="ink_dim",
                                   theme=theme, wrap=True)
        self.body.addWidget(self._standing)

        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.lg)

        self._scroll = QScrollArea()
        scroll = self._scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(470)
        self._inner = QWidget()
        inner = self._inner
        columns = QHBoxLayout(inner)
        columns.setContentsMargins(0, 0, 12, 0)
        columns.setSpacing(0)
        self._lead = QVBoxLayout()
        self._lead.setSpacing(0)
        self._aside = QVBoxLayout()
        self._aside.setSpacing(0)
        left = QWidget()
        left.setLayout(self._lead)
        right = QWidget()
        right.setLayout(self._aside)
        columns.addWidget(left, 3)
        columns.addSpacing(24)
        columns.addWidget(Divider(vertical=True, theme=theme))
        columns.addSpacing(24)
        columns.addWidget(right, 2)
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel(
            "Every line is an event — click one to see everything it changed. "
            "Escape to close.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             turn: int) -> None:
        """Read one turn and lay it out."""
        self.show_dispatch(dispatch_for(conn, world_id, turn, viewer=viewer))

    def show_dispatch(self, report: Dispatch) -> None:
        self._date.setText(_pretty_date(report.date) or f"Turn {report.turn}")
        self._turn.setText(f"TURN {report.turn:02d}")
        self._lede.setText(report.lede or "Nothing of consequence reached the desk.")
        self._standing.setText(report.standing)
        self._standing.setVisible(bool(report.standing))

        clear_layout(self._tally_row)
        for label, value in report.tallies:
            column = QVBoxLayout()
            column.setSpacing(2)
            # Grouped: a world can carry four figures of provinces under occupation, and
            # `1702` is a password rather than a number. `count` leaves a small tally as
            # it was.
            column.addWidget(TextLabel(count(value), style="num_large",
                                       colour="ink_bright", theme=self._theme))
            column.addWidget(TextLabel(label, style="label", colour="ink_faint",
                                       theme=self._theme))
            holder = QWidget()
            holder.setLayout(column)
            self._tally_row.addWidget(holder)
        self._tally_row.addStretch(1)

        clear_layout(self._lead)
        clear_layout(self._aside)
        for section in report.sections:
            block = _SectionBlock(section, theme=self._theme)
            block.event_selected.connect(self.event_selected)
            target = (self._lead if section.title in _LEAD_COLUMN else self._aside)
            target.addWidget(block)
            target.addSpacing(20)
        if report.quiet:
            self._lead.addWidget(TextLabel(
                "The world was quiet. No power moved, nothing was signed, and no "
                "ground changed hands.",
                style="body", colour="ink_dim", theme=self._theme, wrap=True))
        self._lead.addStretch(1)
        self._aside.addStretch(1)
        self.apply_theme(self._theme)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every label; these three surfaces it cannot.

        A bare ``QWidget`` used as a layout holder, and a scroll area's viewport, take
        Qt's own palette rather than the panel's. On the dark theme that is invisible;
        on the light one the page grew two pale rectangles where a holder happened to be.
        """
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner, self._tallies):
            surface.setStyleSheet("background: transparent;")
