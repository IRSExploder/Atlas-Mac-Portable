"""The screen a campaign ends on: victory, defeat, superseded or complete, and the score.

Shown by the game window when :func:`atlas.engine.campaign.ending_after` returns an ending.
It decides nothing; it emits :attr:`EndScreen.chosen` with ``"end"`` (go back to the menu)
or ``"play_on"`` (keep the world running -- after a defeat, that means watching it).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from ..engine.campaign import Ending, Reckoning
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, StatBar, TextLabel, restyle

__all__ = ["EndScreen", "TITLES"]

#: ``kind -> (title, colour key, end button, carry-on button)``.
TITLES: dict[str, tuple[str, str, str, str]] = {
    "victory": ("Victory", "brass", "End the campaign here", "Play on"),
    "complete": ("The campaign is complete", "brass", "End the campaign here", "Play on"),
    "superseded": ("Another power has won", "danger", "Back to the menu", "Keep watching"),
    "defeat": ("Defeat", "danger", "Back to the menu", "Keep watching"),
}

_COMPONENT_NAMES = {
    "ground": "Territory", "wealth": "Wealth", "arms": "Arms",
    "standing": "Standing", "knowledge": "Knowledge",
}


class EndScreen(QDialog):
    chosen = Signal(str)

    def __init__(self, ending: Ending, reckoning: Reckoning | None, *, theme: Theme,
                 date: str = "", parent=None) -> None:
        super().__init__(parent)
        title, colour, end_label, on_label = TITLES[ending.kind]
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.ending = ending

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(12)
        root.addWidget(TextLabel(title, style="display", colour=colour, theme=theme))
        root.addWidget(TextLabel(ending.headline, style="prose", theme=theme, wrap=True))
        if date:
            root.addWidget(TextLabel(date, style="date", colour="ink_dim", theme=theme))

        if reckoning is not None:
            root.addWidget(Divider(theme=theme))
            root.addWidget(SectionLabel("Final reckoning", theme=theme))
            self.rank_label = TextLabel(
                f"{reckoning.nation}: rank {reckoning.rank} of {reckoning.of}, "
                f"score {reckoning.total * 100:.0f}", style="heading", theme=theme)
            root.addWidget(self.rank_label)
            if reckoning.leader != reckoning.nation:
                root.addWidget(TextLabel(
                    f"Leading power: {reckoning.leader}, score "
                    f"{reckoning.leader_total * 100:.0f}",
                    style="body_small", colour="ink_dim", theme=theme))
            self.bars: dict[str, StatBar] = {}
            for component, value, weight in reckoning.components:
                bar = StatBar(f"{_COMPONENT_NAMES.get(component, component)}  "
                              f"(weight {weight:.2f})", theme=theme)
                bar.set_value(value * 100, value, theme.info)
                self.bars[component] = bar
                root.addWidget(bar)
        else:
            self.rank_label = None

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.play_on_button = Chip(on_label, theme=theme)
        self.play_on_button.clicked.connect(lambda: self._choose("play_on"))
        self.end_button = Chip(end_label, theme=theme, selected=True)
        self.end_button.clicked.connect(lambda: self._choose("end"))
        buttons.addWidget(self.play_on_button)
        buttons.addWidget(self.end_button)
        root.addLayout(buttons)

        self.setStyleSheet(f"QDialog {{ background: {theme.panel}; }}")
        restyle(self, theme)

    def _choose(self, choice: str) -> None:
        self.chosen.emit(choice)
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 -- Qt override
        # Escape is "not now", which is playing on: an ending is never forced by a stray key.
        if event.key() == Qt.Key.Key_Escape:
            self._choose("play_on")
            return
        super().keyPressEvent(event)
