"""The first screen: Continue, New game, Load, Settings, Quit.

Like the setup screen it decides nothing and opens nothing. It emits what the player
chose and the caller does the work -- opening a world touches the filesystem, the packs
and the provider seam, none of which belongs behind a button handler.

**Load is a list beside the buttons rather than a second screen.** There are rarely more
than a handful of saves, and a player looking for one wants to see them all at once with
their era, date and turn -- which is also what tells two saves of the same era apart.
A save that cannot be opened is still listed, with the reason, and cannot be chosen.

**A provider-status line sits under the buttons** (DEV_TRACKS seam: "leave a
provider-status slot in the main menu"). What it says is the caller's business.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..state.catalog import SavedWorld
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, restyle, scroll_qss

__all__ = ["MainMenu"]


class MainMenu(QWidget):
    new_requested = Signal()
    open_requested = Signal(object)  # SavedWorld
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, theme: Theme, worlds: list[SavedWorld], *,
                 provider_status: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ATLAS")
        self._theme = theme
        self._worlds = list(worlds)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(64, 56, 64, 48)
        outer.setSpacing(48)

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(TextLabel("ATLAS", style="brand_large", colour="brass", theme=theme))
        left.addWidget(TextLabel("An alternate history, written as you play.",
                                 style="prose_italic", colour="ink_dim", theme=theme))
        left.addSpacing(24)

        recent = next((world for world in self._worlds if world.playable), None)
        self.continue_button = Chip("Continue", theme=theme, selected=recent is not None)
        self.continue_button.setEnabled(recent is not None)
        self.continue_button.setToolTip(recent.describe() if recent else "No saved game yet")
        if recent is not None:
            self.continue_button.clicked.connect(lambda: self.open_requested.emit(recent))
        self.new_button = Chip("New game", theme=theme, selected=recent is None)
        self.new_button.clicked.connect(self.new_requested.emit)
        self.settings_button = Chip("Settings", theme=theme)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.quit_button = Chip("Quit", theme=theme)
        self.quit_button.clicked.connect(self.quit_requested.emit)
        for button in (self.continue_button, self.new_button, self.settings_button,
                       self.quit_button):
            left.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        if recent is not None:
            left.addWidget(TextLabel(recent.describe(), style="body_small", colour="ink_faint",
                                     theme=theme, wrap=True))
        self.problem_label = TextLabel("", style="body_small", colour="danger", theme=theme,
                                       wrap=True)
        self.problem_label.hide()
        left.addWidget(self.problem_label)
        left.addStretch(1)
        left.addWidget(Divider(theme=theme))
        self.provider_label = TextLabel(provider_status, style="body_small", colour="ink_dim",
                                        theme=theme, wrap=True)
        left.addWidget(self.provider_label)
        outer.addLayout(left, 2)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(SectionLabel("Load a saved game", theme=theme))
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.load_rows: list[Chip] = []
        if not self._worlds:
            rows.addWidget(TextLabel("No saved games yet. Start a new one.", style="body",
                                     colour="ink_faint", theme=theme))
        for world in self._worlds:
            row = Chip(world.describe(), theme=theme, style="row", wrap=True)
            row.setToolTip(f"{world.path}\nlast played {world.modified:%Y-%m-%d %H:%M}")
            row.setEnabled(world.playable)
            if world.playable:
                row.clicked.connect(lambda world=world: self.open_requested.emit(world))
            self.load_rows.append(row)
            rows.addWidget(row)
        rows.addStretch(1)
        self._scroll.setWidget(holder)
        right.addWidget(self._scroll, 1)
        outer.addLayout(right, 3)
        self.apply_theme(theme)

    def show_problem(self, text: str) -> None:
        """Why the last choice did not open, in the menu rather than as a traceback."""
        self.problem_label.setText(text)
        self.problem_label.setVisible(bool(text))

    def set_provider_status(self, text: str) -> None:
        self.provider_label.setText(text)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(f"MainMenu {{ background: {theme.canvas}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scroll.setStyleSheet(scroll_qss(theme))
        restyle(self, theme)
