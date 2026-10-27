"""The diplomatic traffic, read as correspondence rather than scrolled as a log.

The models write the best prose in the program and it has never had a screen of its own.
*Ukraine calls publicly on France, Germany, the United Kingdom and the United States to
sustain urgent deliveries of air-defence interceptors.* *China tells Russia privately that
its partnership remains intact.* Those arrive one line high in the left rail, between an
air-superiority line and a completed workshop, and are gone by the next turn.

So this lays them out the way a foreign ministry would put the morning's traffic on a desk:
grouped under the turn they came in, a sender and a colour on each, a subject a reader
already has a word for, the scope it arrived under, and then the whole of what was said, set
in the serif at a reading measure. Nothing is summarised and nothing is truncated. Reading
one is the point of the screen, not a step toward something else.

**It renders; it does not choose.** Every letter comes from
:func:`atlas.engine.correspondence.correspondence_for`, which is a read over the log scoped
through the same visibility predicate the dispatch and the trace use -- so the confidential
channel between two other powers is not in your inbox, and the panel never had it to draw.
This file chooses type sizes.

Every letter is one click from the inspector, because *what else did that do* is the next
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

from ..engine.correspondence import Correspondence, Letter, correspondence_for
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    SectionLabel,
    ShieldSwatch,
    TextLabel,
    clear_layout,
    pressed_qss,
    scroll_qss,
)

__all__ = ["CorrespondenceOverlay"]

#: The three ways to read an inbox: everything, what came to you, what you sent. Filters
#: rather than separate screens, because a correspondence is a conversation and splitting it
#: into two lists is what makes a mail client feel like a database.
_FILTERS = (
    ("all", "All traffic"),
    ("in", "Addressed to you"),
    ("out", "Your own dispatches"),
)


def _pretty_date(iso: str) -> str:
    """``2027-01-26`` becomes ``26 January 2027``, as the rest of the interface writes it."""
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


class _TurnHeading(QWidget):
    """The dateline a run of letters arrived under."""

    def __init__(self, turn: int, date: str, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 20, 0, 8)
        row.setSpacing(12)
        self._turn = TextLabel(f"TURN {turn:02d}", style="label", colour="brass",
                              theme=theme)
        self._date = TextLabel(_pretty_date(date), style="date", colour="ink_dim",
                               theme=theme)
        row.addWidget(self._turn, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._date, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)

    def apply_theme(self, theme: Theme) -> None:
        for child in (self._turn, self._date):
            child.apply_theme(theme)


class _LetterBlock(PressFeedback, QFrame):
    """One piece of correspondence: who sent it, what it was, and what it said."""

    clicked = Signal(int)

    def __init__(self, letter: Letter, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letter = letter
        self._kids: list[QWidget] = []
        self.setObjectName("letter")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open this in the inspector")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(11, 11, 13, 13)
        outer.setSpacing(14)
        self._shield = ShieldSwatch(letter.colour, width=18, height=23)
        outer.addWidget(self._shield, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(11)
        self._add(head, TextLabel(letter.sender, style="row", colour="ink_bright",
                                  theme=theme))
        # The subject, in the ink its gravity earns. A declaration of war set like a trade
        # note is an inbox that has stopped telling a reader anything.
        self._add(head, TextLabel(
            letter.subject.upper(), style="label",
            colour="danger" if letter.grave else "ink_faint", theme=theme))
        head.addStretch(1)
        for tag, ink in self._tags(letter):
            self._add(head, TextLabel(tag, style="label", colour=ink, theme=theme))
        column.addLayout(head)

        if letter.addressed:
            # Only ever filled in for the sender and the referee -- see
            # `engine.correspondence`. A recipient is told it came to them, not who else got
            # a copy.
            self._add(column, TextLabel(
                "to " + ", ".join(letter.addressed), style="mono_small",
                colour="ink_ghost", theme=theme, wrap=True))

        # The whole of it. A correspondence that elides is a correspondence nobody reads,
        # and this is the one screen in the program whose entire purpose is the sentence.
        self._add(column, TextLabel(letter.text, style="prose", colour="ink_body",
                                    theme=theme, wrap=True))
        outer.addLayout(column, 1)

    @staticmethod
    def _tags(letter: Letter) -> list[tuple[str, str]]:
        """The marks in the corner: where it stands relative to the reader, and how widely
        it was said. Words rather than colours alone, for the reason the dispatch gives."""
        tags: list[tuple[str, str]] = []
        if letter.to_you:
            tags.append(("TO YOU", "brass"))
        elif letter.from_you:
            tags.append(("SENT", "info"))
        if letter.scope == "secret":
            tags.append(("YOUR SERVICES ONLY", "danger"))
        elif letter.confidential:
            tags.append(("IN CONFIDENCE", "brass"))
        return tags

    def _add(self, layout, widget: QWidget) -> None:
        self._kids.append(widget)
        layout.addWidget(widget)

    def _fire(self) -> None:
        self.clicked.emit(self._letter.event_id)

    def apply_theme(self, theme: Theme) -> None:
        # A letter addressed to this government is set on the raised surface, so the inbox
        # has a shape before a word of it is read.
        background = theme.inset if self._letter.to_you else "transparent"
        self.setStyleSheet(
            f"QFrame#letter {{ background: {background};"
            f" border-left: 1px solid "
            f"{theme.brass_line if self._letter.to_you else theme.rule_soft}; }}"
            f"QFrame#letter:hover {{ background: {theme.raised}; }}"
            + pressed_qss("QFrame#letter", theme)
        )
        for child in self._kids:
            child.apply_theme(theme)


class CorrespondenceOverlay(Overlay):
    """The diplomatic feed: what the powers said, to the government that was told it."""

    #: A letter the reader wants the whole picture of. The window opens the inspector.
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._conn: sqlite3.Connection | None = None
        self._world = ""
        self._viewer: str | None = None
        self._found: Correspondence | None = None
        self._filter = "all"

        self.body.addWidget(SectionLabel("What the powers said", theme=theme))
        self.body.addSpacing(SPACE.sm)

        head = QHBoxLayout()
        head.setSpacing(16)
        self._heading = TextLabel("The correspondence", style="title",
                                  colour="ink_bright", theme=theme)
        head.addWidget(self._heading)
        head.addStretch(1)
        self._count = TextLabel("", style="num", colour="brass", theme=theme)
        head.addWidget(self._count, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.sm)

        self._chip_row = QHBoxLayout()
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(6)
        self._chips = QWidget()
        self._chips.setLayout(self._chip_row)
        self._filter_chips: dict[str, Chip] = {}
        for key, label in _FILTERS:
            chip = Chip(label, theme=theme, selected=key == self._filter)
            chip.clicked.connect(lambda key=key: self._refilter(key))
            self._filter_chips[key] = chip
            self._chip_row.addWidget(chip)
        self._chip_row.addStretch(1)
        self.body.addWidget(self._chips)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(600)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 0, 14, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel(
            "Only what your government was told — a channel between two other powers is "
            "not in here. Click a letter to see everything it changed; Escape to close.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str,
             viewer: str | None) -> None:
        """Read the traffic and lay it out."""
        self._conn = conn
        self._world = world_id
        self._viewer = viewer
        # A filter left over from the last power looked at is a filter nobody chose; and
        # "addressed to you" means nothing at all to a referee.
        self._filter = "all"
        self.show_correspondence(correspondence_for(conn, world_id, viewer=viewer))

    def show_correspondence(self, found: Correspondence) -> None:
        self._found = found
        self._render()

    def _refilter(self, key: str) -> None:
        self._filter = key
        self._render()

    def _shown(self) -> list[Letter]:
        letters = list(self._found.letters) if self._found else []
        if self._filter == "in":
            return [letter for letter in letters if letter.to_you]
        if self._filter == "out":
            return [letter for letter in letters if letter.from_you]
        return letters

    def _render(self) -> None:
        theme = self._theme
        found = self._found
        letters = self._shown()

        for key, chip in self._filter_chips.items():
            chip.set_selected(key == self._filter)
        # A referee has no inbox and sends nothing, so two of the three filters are
        # questions with no answer rather than filters that happen to be empty.
        self._chips.setVisible(bool(found and found.viewer))

        if found is None:
            self._count.setText("")
        elif found.empty:
            self._count.setText("NOTHING ON FILE")
        else:
            self._count.setText(
                f"{len(found.letters)} LETTERS  ·  BACK TO TURN {found.reach}"
                + (f"  ·  {found.to_you} TO YOU" if found.to_you else ""))

        clear_layout(self._stack)
        if not letters:
            self._stack.addWidget(TextLabel(
                self._nothing_here(), style="body", colour="ink_dim", theme=theme,
                wrap=True))
            self._stack.addStretch(1)
            self.apply_theme(theme)
            return

        turn = None
        for letter in letters:
            if letter.turn != turn:
                turn = letter.turn
                self._stack.addWidget(_TurnHeading(turn, letter.date, theme=theme))
            block = _LetterBlock(letter, theme=theme)
            block.clicked.connect(self.event_selected)
            self._stack.addWidget(block)
            self._stack.addSpacing(6)

        if found is not None and found.truncated and self._filter == "all":
            # Named rather than dropped. A feed that quietly stops reads as a feed that had
            # nothing older, and the left rail is where the rest already is.
            self._stack.addSpacing(10)
            self._stack.addWidget(TextLabel(
                f"Older correspondence than turn {found.reach} is in the event log.",
                style="mono_small", colour="ink_ghost", theme=theme, wrap=True))
        self._stack.addStretch(1)
        self.apply_theme(theme)

    def _nothing_here(self) -> str:
        """Why the page is blank, which is never the same reason twice."""
        if self._found is None or self._found.empty:
            return ("No government in this world has said anything to another yet. The "
                    "feed fills the first time one of them does.")
        if self._filter == "in":
            return ("Nothing has been addressed to you. What is on file was said to the "
                    "world, or to somebody else.")
        return "You have sent nothing. Everything here was said by somebody else."

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every block; the bare holders it cannot.

        A layout-holder ``QWidget`` and a scroll viewport take Qt's own palette rather than
        the panel's, which is invisible on the dark theme and a pale rectangle on the light
        one -- the same two surfaces the dispatch and the trace both have to paint by hand.
        """
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner, self._chips):
            surface.setStyleSheet("background: transparent;")
