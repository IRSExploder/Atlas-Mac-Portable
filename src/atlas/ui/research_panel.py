"""The Research tab: what your country can learn, what it costs, and a button to begin it.

A thin presentation of :func:`atlas.engine.research_board.board`. **Start writes an order
sentence into the orders box** -- "Research Armour Mk II" -- exactly as the province panel's
buttons do, so nothing begins until the player advances and a click reads back the same as
a typed line (`tests/test_research_is_on_the_screen.py`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.research import mark_name
from ..engine.research_board import Board, Card, LineRow, board
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, scroll_qss

__all__ = ["ResearchBlock"]


class ResearchBlock(QWidget):
    #: The order sentence a Start button chose.
    start = Signal(str)

    def __init__(self, theme: Theme, *, orders_text: Callable[[], str] = str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._player: str | None = None
        #: What the orders box holds now. A programme whose sentence is in it says so on its
        #: button, and stops saying so the moment the line is deleted or the turn is sent.
        self._orders_text = orders_text
        self._board: Board | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(20, 16, 20, 6)
        self._heading = SectionLabel("Research", theme=theme)
        self._means = TextLabel("", style="mono_small", colour="ink_faint", theme=theme)
        # The tab already says "Research"; the row keeps the heading's place for the
        # means to begin one, and the heading stays only for the screen readers' sake.
        self._heading.setVisible(False)
        head_layout.addWidget(self._heading)
        head_layout.addStretch(1)
        head_layout.addWidget(self._means)
        layout.addWidget(head)

        self._hint = TextLabel(
            "Upgrades make your own formations stronger, up to Mk III. Start one and it "
            "goes into your orders; it begins when you advance.",
            style="body_small", colour="ink_faint", theme=theme, wrap=True)
        self._hint.setContentsMargins(20, 0, 20, 10)
        layout.addWidget(self._hint)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setStyleSheet(scroll_qss(theme))
        layout.addWidget(self._area, 1)
        self._body_widget: QWidget | None = None

    # -- data --------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, player: str | None,
             theme: Theme) -> None:
        self._conn, self._world_id, self._player, self._theme = conn, world_id, player, theme
        self._rebuild()

    def rebuild(self) -> None:
        self._rebuild()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._area.setStyleSheet(scroll_qss(theme))
        for label in (self._heading, self._means, self._hint):
            label.apply_theme(theme)
        self._rebuild()

    @property
    def shown(self) -> Board | None:
        return self._board

    # -- building ----------------------------------------------------------

    def _rebuild(self) -> None:
        theme = self._theme
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        column = QVBoxLayout(body)
        column.setContentsMargins(20, 0, 20, 16)
        column.setSpacing(0)
        if self._conn is None or not self._player:
            self._board = None
            self._means.setText("")
            column.addWidget(TextLabel("Choose a country to see what it can research.",
                                       style="body_small", colour="ink_faint", theme=theme,
                                       wrap=True))
            column.addStretch(1)
            self._swap(body)
            return

        found = self._board = board(self._conn, self._world_id, self._player)
        self._means.setText(
            f"money {found.treasury:.0f} · slots {found.slots_used}/{found.slots}")
        self._means.setToolTip(
            "Research is paid from your treasury when it starts, and each programme takes "
            "one build slot until it finishes. Slots come from your industry.")

        running = found.running
        if running:
            column.addWidget(SectionLabel("Under way", theme=theme))
            column.addSpacing(8)
            for card in running:
                column.addWidget(_Running(card, theme))
                column.addSpacing(8)
            column.addSpacing(6)

        if found.lines:
            column.addWidget(SectionLabel("Upgrades", theme=theme))
            column.addSpacing(8)
            for line in found.lines:
                column.addWidget(self._line(line))
                column.addSpacing(10)
            column.addWidget(TextLabel(
                f"Research can make a formation at most {found.ceiling:.0%} stronger than "
                "the standard one.", style="body_small", colour="ink_ghost", theme=theme,
                wrap=True))
            column.addSpacing(14)

        waiting = [c for c in found.programmes if c.state not in ("running", "done")]
        if waiting:
            column.addWidget(Divider(soft=True, theme=theme))
            column.addSpacing(12)
            column.addWidget(SectionLabel("Programmes", theme=theme))
            column.addSpacing(8)
            for card in waiting:
                column.addWidget(self._programme(card))
                column.addSpacing(10)

        done = [c.name for c in found.programmes if c.state == "done"]
        if done:
            column.addSpacing(4)
            column.addWidget(TextLabel("Done: " + ", ".join(done), style="body_small",
                                       colour="ink_faint", theme=theme, wrap=True))
        column.addStretch(1)
        self._swap(body)

    def _swap(self, body: QWidget) -> None:
        old = self._area.takeWidget()
        self._area.setWidget(body)
        self._body_widget = body
        if old is not None:
            old.deleteLater()

    def _line(self, line: LineRow) -> QWidget:
        theme = self._theme
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = TextLabel(f"{line.name} {mark_name(line.mark)}", style="label", colour="ink",
                         theme=theme)
        name.setToolTip(f"Improves: {line.kinds}")
        top.addWidget(name)
        top.addWidget(_Pips(line.mark, line.max_mark, theme,
                            running=bool(line.next and line.next.state == "running")))
        top.addStretch(1)
        nxt = line.next
        if nxt is not None and nxt.state in ("open", "running"):
            top.addWidget(self._button(nxt))
        column.addLayout(top)

        if nxt is None:
            text, colour = "Fully upgraded.", "ink_faint"
        elif nxt.state == "running":
            text, colour = f"{nxt.name}: {nxt.turns_left} turn(s) to go.", "ink_faint"
        else:
            text = f"Next: {nxt.effect} · {nxt.cost:.0f} money · {nxt.turns} turns"
            colour = "ink_body"
        column.addWidget(TextLabel(text, style="body_small", colour=colour, theme=theme,
                                   wrap=True))
        if nxt is not None and nxt.why:
            column.addWidget(TextLabel(nxt.why + ".", style="body_small", colour="ink_ghost",
                                       theme=theme, wrap=True))
        return row

    def _programme(self, card: Card) -> QWidget:
        theme = self._theme
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = TextLabel(card.name, style="label",
                         colour="ink" if card.startable else "ink_dim", theme=theme,
                         wrap=True)
        name.setToolTip(card.blurb)
        top.addWidget(name, 1)
        if card.startable:
            top.addWidget(self._button(card))
        column.addLayout(top)
        column.addWidget(TextLabel(f"{card.effect} · {card.cost:.0f} money · {card.turns} turns",
                                   style="body_small", colour="ink_body", theme=theme,
                                   wrap=True))
        if card.why:
            column.addWidget(TextLabel(card.why + ".", style="body_small", colour="ink_ghost",
                                       theme=theme, wrap=True))
        return row

    def _button(self, card: Card) -> QWidget:
        if card.state == "running":
            label = TextLabel(f"{card.turns_left} to go", style="mono_small",
                              colour="ink_faint", theme=self._theme)
            return label
        queued = card.order.lower() in self._orders_text().lower()
        chip = Chip("In orders ✓" if queued else "Start", theme=self._theme,
                    selected=queued, padding=(10, 3))
        chip.setObjectName(f"research:{card.tech_id}")
        chip.setToolTip(f"Adds “{card.order}” to your orders. Nothing is spent until "
                        "you advance.")
        if not queued:
            chip.clicked.connect(lambda c=card: self._chosen(c))
        return chip

    def _chosen(self, card: Card) -> None:
        self.start.emit(card.order)
        self._rebuild()


class _Pips(QWidget):
    """●●○ -- how far up its line a family of formations is."""

    def __init__(self, mark: int, top: int, theme: Theme, *, running: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mark, self._top, self._theme, self._running = mark, top, theme, running
        self.setFixedSize(self.sizeHint())
        self.setToolTip(f"{mark_name(mark)} of {mark_name(top)}")

    def sizeHint(self) -> QSize:
        return QSize(self._top * 12, 12)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        brass = QColor(self._theme.brass)
        ghost = QColor(self._theme.ink_ghost)
        for index in range(self._top):
            rect = QRectF(index * 12 + 2, 2, 8, 8)
            reached = index < self._mark
            coming = self._running and index == self._mark
            painter.setPen(QPen(brass if (reached or coming) else ghost, 1.4))
            painter.setBrush(brass if reached else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
        painter.end()


class _Running(QWidget):
    """A programme under way: its name, its turns to go, and a bar."""

    def __init__(self, card: Card, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._share = max(0.0, min(1.0, 1.0 - card.turns_left / max(card.turns, 1)))
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(TextLabel(card.name, style="label", colour="ink", theme=theme), 1)
        top.addWidget(TextLabel(f"{card.turns_left} turn(s) to go", style="mono_small",
                                colour="ink_faint", theme=theme))
        column.addLayout(top)
        self._bar = _Bar(self._share, theme)
        column.addWidget(self._bar)
        column.addWidget(TextLabel(card.effect, style="body_small", colour="ink_faint",
                                   theme=theme, wrap=True))


class _Bar(QWidget):
    def __init__(self, share: float, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._share, self._theme = share, theme
        self.setFixedHeight(6)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QRectF(0, 0, self.width(), self.height())
        painter.setBrush(QColor(self._theme.inset))
        painter.drawRoundedRect(track, 3, 3)
        if self._share > 0:
            painter.setBrush(QColor(self._theme.brass))
            painter.drawRoundedRect(QRectF(0, 0, self.width() * self._share, self.height()),
                                    3, 3)
        painter.end()
