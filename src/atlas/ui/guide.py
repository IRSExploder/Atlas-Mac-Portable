"""The first-turn guide: three cards on a new game, once, for a player who has never played.

A new player saw a map, a text box and a button, and nothing said which came first. This
says it in three steps -- who you are on the map, how to give orders, what Advance does --
over the map's empty top-left corner, and gets out of the way. It is shown on turn zero of
a new game, without technical details, only until it has been seen once; the choice is
remembered with the player's own defaults (see `ui.preferences.Preferences.guide_seen`).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .theme import Theme
from .widgets import Chip, SectionLabel, TextLabel

__all__ = ["FirstTurnGuide", "guide_steps"]


def guide_steps(nation: str) -> list[tuple[str, str]]:
    """``(heading, body)`` for each card, in order."""
    return [
        (f"You are {nation}",
         "Your country is the one outlined on the map. Allies are teal, anyone at war "
         "with you is red, and everyone else is grey. Scroll to zoom; the house button "
         "shows the whole world."),
        ("Give your orders",
         "Click any country to see what you can do there, and press a button to add it. "
         "Or type orders in plain words in the box on the right, such as "
         "“Raise two infantry divisions”."),
        ("Press Advance",
         "A month passes, the world answers, and a summary tells you what happened to you, "
         "how your orders went and how your wars stand. Then do it again."),
    ]


class FirstTurnGuide(QFrame):
    """Three cards with Next and Skip. Emits ``finished`` when read or skipped."""

    finished = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("firstTurnGuide")
        self._theme = theme
        self._steps: list[tuple[str, str]] = []
        self._index = 0
        self.setFixedWidth(360)

        column = QVBoxLayout(self)
        column.setContentsMargins(20, 16, 20, 16)
        column.setSpacing(8)
        self._count = SectionLabel("", theme=theme)
        column.addWidget(self._count)
        self._heading = TextLabel("", style="row", colour="ink_bright", theme=theme,
                                  wrap=True)
        column.addWidget(self._heading)
        self._body = TextLabel("", style="body", colour="ink_body", theme=theme, wrap=True)
        column.addWidget(self._body)
        column.addSpacing(6)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._skip = Chip("Skip", theme=theme, padding=(10, 5))
        self._skip.clicked.connect(self._done)
        controls.addWidget(self._skip)
        controls.addStretch(1)
        self._next = Chip("Next", theme=theme, selected=True, padding=(14, 5))
        self._next.clicked.connect(self._advance)
        controls.addWidget(self._next)
        column.addLayout(controls)
        self.apply_theme(theme)
        self.hide()

    def start(self, nation: str) -> None:
        self._steps = guide_steps(nation)
        self._index = 0
        self._show_step()
        self.show()
        self.raise_()

    @property
    def step(self) -> int:
        return self._index

    def _show_step(self) -> None:
        heading, body = self._steps[self._index]
        last = self._index == len(self._steps) - 1
        self._count.setText(f"Getting started · {self._index + 1} of {len(self._steps)}")
        self._heading.setText(heading)
        self._body.setText(body)
        self._next.set_text("Got it" if last else "Next")
        self._skip.setVisible(not last)
        # Wrapped labels are measured against the card's own width: left to the layout,
        # they were given one line's height and the paragraph was cut top and bottom.
        inner = self.width() - 40
        for label in (self._heading, self._body):
            label.setFixedHeight(label.heightForWidth(inner))
        self.adjustSize()

    def _advance(self) -> None:
        if self._index >= len(self._steps) - 1:
            self._done()
            return
        self._index += 1
        self._show_step()

    def _done(self) -> None:
        self.hide()
        self.finished.emit()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#firstTurnGuide {{ background: {theme.panel};"
            f" border: 1px solid {theme.brass_line}; border-radius: 6px; }}")
        for child in (self._count, self._heading, self._body, self._skip, self._next):
            child.apply_theme(theme)
