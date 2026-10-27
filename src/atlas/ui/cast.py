"""The five powers the world revolves around for you, with memory of what each just did.

Focus Mode is the most expensive thing the engine does -- five dedicated model calls a turn,
each with its own goals and its own reading of events -- and until now the interface spent it
without ever naming a beneficiary. The left rail draws five pips and a count. So the player
was told there were five actors and never who they were, which is the one fact that would
have made them actors rather than a budget.

**A cast list, not a table.** Each power gets its colour, the standing between you in a word
and a number, an arrow for which way that has been going, the reason Focus itself gave for
tracking it, and the last thing it did that you were told about. Read it twice in two turns
and the same five faces are there, each carrying something new -- which is the whole of what
"the world feels populated" means in a strategy game.

**It renders; it does not decide.** Every value comes from :func:`atlas.engine.cast.cast_for`,
which is a read over the roster, the relation table and the log, scoped to what this
government was actually told. This file chooses type sizes.

The act on each row is one click from the inspector, because *what else did that do* is the
next question and it already has a screen.
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

from ..engine.cast import Cast, CastMember, cast_for
from ..humanize import figure, regard, signed
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Divider,
    PressFeedback,
    SectionLabel,
    ShieldSwatch,
    TextLabel,
    clear_layout,
    pressed_qss,
    scroll_qss,
)

__all__ = ["CastOverlay"]

#: How wide one act may run before it is cut. The row is a face to recognise at a glance;
#: the whole sentence is one click away in the inspector.
_ACT_TEXT = 190

#: Which ink a standing is set in. Keyed on the word rather than on the number, so the panel
#: and :attr:`atlas.engine.cast.CastMember.standing` can never disagree about where the
#: boundary is.
_STANDING_INK = {
    "at war": "danger",
    "hostile": "danger",
    "cold": "danger",
    "alliance": "good",
    "defensive": "good",
    "warm": "good",
    "friendly": "good",
    "trade": "info",
    "non aggression": "info",
    "vassal": "info",
}


def _shorten(text: str, limit: int = _ACT_TEXT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_date(iso: str) -> str:
    """``1936-04-30`` becomes ``30 Apr``. The dateline, not the date."""
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


class _MemberRow(PressFeedback, QFrame):
    """One power: who it is, where you stand, and the last thing it did."""

    clicked = Signal(int)

    def __init__(self, member: CastMember, *, span: int, has_viewer: bool,
                 theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._member = member
        self._kids: list[QWidget] = []
        self.setObjectName("castRow")
        if member.latest is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Open this in the inspector")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 12, 12, 13)
        row.setSpacing(14)

        self._shield = ShieldSwatch(member.colour, width=20, height=26)
        row.addWidget(self._shield, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(12)
        self._add(head, TextLabel(member.name, style="nation", colour="ink_bright",
                                  theme=theme))
        head.addStretch(1)
        # The standing, then the figure, then which way it is going. Only where there is a
        # government to stand toward: spectating, these three would all read zero and a
        # column of zeroes is a claim, not a blank.
        if has_viewer:
            self._add(head, TextLabel(
                member.standing.upper(), style="label",
                colour=_STANDING_INK.get(member.standing, "ink_faint"), theme=theme))
            score = self._add(head, TextLabel(
                signed(member.score), style="num_large",
                colour="ink_bright", theme=theme))
            # The scale, on the hover. A player shown `-24` beside a flag has been given
            # a coordinate in a system nobody described to them; the sentence behind it
            # says what the ends of that system are.
            score.setToolTip(
                regard(member.score, at_war=member.at_war, treaty=member.treaty))
            self._add(head, _Trend(member, span=span, theme=theme))
        column.addLayout(head)

        # Why Focus is spending a call on this power, in the ranking's own words.
        since = f"in the foreground since turn {member.since_turn}" if member.since_turn \
            else "in the foreground"
        self._add(column, TextLabel(
            f"{since}  ·  {member.reason}" if member.reason else since,
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True))

        column.addSpacing(4)
        if member.latest is not None:
            act = QHBoxLayout()
            act.setSpacing(9)
            # A mark that is a shape as well as a hue, for the reason the dispatch gives:
            # colour alone is a claim a reader who cannot see it never receives.
            mark = _fixed(TextLabel(
                "◆" if member.latest.weight >= 3 else (
                    "●" if member.latest.weight == 2 else "·"),
                style="num",
                colour="danger" if member.latest.weight >= 3 else (
                    "brass" if member.latest.weight == 2 else "ink_ghost"),
                theme=theme), 14)
            body = TextLabel(
                _shorten(member.latest.text), style="prose", colour="ink_body",
                theme=theme, wrap=True)
            when = TextLabel(
                f"turn {member.latest.turn}  ·  {_short_date(member.latest.date)}",
                style="mono_small", colour="ink_ghost", theme=theme)
            # Top-aligned, all three: an act that wraps to three lines otherwise centres its
            # mark and its dateline against the middle line, and the column stops being a
            # column.
            for child, stretch in ((mark, 0), (body, 1), (when, 0)):
                self._kids.append(child)
                act.addWidget(child, stretch, Qt.AlignmentFlag.AlignTop)
            column.addLayout(act)
        else:
            # Named rather than left blank. A power with nothing under it looks like a
            # power that has done nothing, and the truth is that what it did was not
            # something this government was told.
            self._add(column, TextLabel(
                "Nothing this power has done lately has reached you.",
                style="prose_italic", colour="ink_ghost", theme=theme, wrap=True))

        row.addLayout(column, 1)

    def _add(self, layout, widget: QWidget, stretch: int = 0) -> QWidget:
        self._kids.append(widget)
        layout.addWidget(widget, stretch)
        return widget

    def _takes_press(self, event) -> bool:
        return (self._member.latest is not None
                and event.button() == Qt.MouseButton.LeftButton)

    def _fire(self) -> None:
        if self._member.latest is not None:
            self.clicked.emit(self._member.latest.event_id)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            "QFrame#castRow { background: transparent; }"
            f"QFrame#castRow:hover {{ background: {theme.raised}; }}"
            + pressed_qss("QFrame#castRow", theme)
        )
        for child in self._kids:
            child.apply_theme(theme)


class _Trend(TextLabel):
    """Which way the standing has been going, as an arrow, a figure and sometimes a turn.

    A flat trend prints a rule rather than an arrow and no number at all. An arrow that can
    only point three ways is read as a direction whatever it is drawn as, so a sideways one
    would say *drifting slightly warmer* to a reader who is entitled to be told *nothing has
    moved*.

    A movement older than the window carries the turn it happened on and is set in the
    muted ink, because *cooled by seven, in March* and *cooling now* are different facts and
    the same arrow would tell them the same way.
    """

    def __init__(self, member: CastMember, *, span: int, theme: Theme,
                 parent: QWidget | None = None) -> None:
        direction = member.direction
        glyph = "▲" if direction > 0 else ("▼" if direction < 0 else "–")
        text = glyph if not direction else f"{glyph} {figure(abs(member.trend))}"
        if direction and not member.trend_recent:
            text += f"  t{member.trend_turn}"
        if not direction:
            ink = "ink_ghost"
        elif not member.trend_recent:
            ink = "ink_dim"
        else:
            ink = "good" if direction > 0 else "danger"
        super().__init__(text, style="num", colour=ink, theme=theme, parent=parent)
        self.setFixedWidth(78)
        if not direction:
            self.setToolTip("Your regard for them has never moved")
        elif member.trend_recent:
            self.setToolTip(
                f"{signed(member.trend, places=1)} points of regard over the last "
                f"{span} turns, on a scale of -100 to 100")
        else:
            self.setToolTip(
                f"Unmoved for {span} turns; it last moved on turn {member.trend_turn}")


def _fixed(label: TextLabel, width: int) -> TextLabel:
    label.setFixedWidth(width)
    return label


class CastOverlay(Overlay):
    """The powers the world revolves around for this player."""

    #: An act the reader wants the whole picture of. The window opens the inspector.
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("The powers you are watching", theme=theme))
        self.body.addSpacing(SPACE.sm)

        head = QHBoxLayout()
        head.setSpacing(16)
        self._heading = TextLabel("The cast", style="title", colour="ink_bright",
                                  theme=theme)
        head.addWidget(self._heading)
        head.addStretch(1)
        self._count = TextLabel("", style="num", colour="brass", theme=theme)
        head.addWidget(self._count, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.sm)

        self._blurb = TextLabel(
            "These powers act on their own each turn, with their own goals and their own "
            "reading of events. The rest of the world carries on in the background, and "
            "is no less real for it.",
            style="prose", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._blurb)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(600)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 6, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel(
            "The figure is your regard for them, and the arrow is where it has been "
            "going. Click a line to see everything that act changed — Escape to close.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             turn: int | None = None) -> None:
        """Read the roster and lay it out."""
        self.show_cast(cast_for(conn, world_id, viewer=viewer, turn=turn))

    def show_cast(self, cast: Cast) -> None:
        theme = self._theme
        self._count.setText(
            f"TURN {cast.turn:02d}"
            + (f"  ·  {len(cast.members)} POWERS" if cast.members else ""))

        clear_layout(self._stack)
        if cast.empty:
            # The honest reading of an empty roster, which a world has for as long as
            # nothing in it is worth a dedicated call. Not an error and not a blank.
            self._stack.addWidget(TextLabel(
                "No power is in the foreground yet. Focus promotes a nation the turn it "
                "starts doing something to you — a war, a proposal, a border it shares "
                "with a war — and the roster is empty until one does.",
                style="body", colour="ink_dim", theme=theme, wrap=True))
            self._stack.addStretch(1)
            self.apply_theme(theme)
            return

        for index, member in enumerate(cast.members):
            if index:
                self._stack.addWidget(Divider(soft=True, theme=theme))
            row = _MemberRow(member, span=cast.span, has_viewer=bool(cast.viewer),
                             theme=theme)
            row.clicked.connect(self.event_selected)
            self._stack.addWidget(row)
        self._stack.addStretch(1)
        self.apply_theme(theme)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every row; the bare holders it cannot.

        A layout-holder ``QWidget`` and a scroll viewport take Qt's own palette rather than
        the panel's, which is invisible on the dark theme and a pale rectangle on the light
        one -- the same two surfaces the dispatch and the trace both have to paint by hand.
        """
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner):
            surface.setStyleSheet("background: transparent;")
