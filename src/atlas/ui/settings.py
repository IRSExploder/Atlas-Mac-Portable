"""The settings screen: the four existing preferences, plus a slot for the AI providers.

Generated from the tables in :mod:`atlas.ui.preferences` rather than written out by hand --
the text scales from :data:`SCALES`, the shortcuts from :data:`SHORTCUTS` -- so a
preference added there cannot exist without a control here.

It decides nothing about where a preference is kept. It emits :attr:`SettingsScreen.saved`
with a new :class:`Preferences` and the caller stores it: the main menu as the player's
own defaults, an open game window through ``set_preferences``.

**The provider panel is a seam, not a feature** (DEV_TRACKS C7). What it should show
depends on how the shipped game reaches its model, which is not decided; until then it
shows whatever one-line status the caller hands it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QKeySequenceEdit, QVBoxLayout

from .preferences import SCALES, SHORTCUTS, Preferences
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, restyle

__all__ = ["SettingsScreen"]


class SettingsScreen(QDialog):
    """A modal sheet over the menu or the game. Save or cancel; nothing applies live."""

    saved = Signal(object)

    def __init__(self, preferences: Preferences, *, theme: Theme,
                 provider_status: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._theme = theme
        self._scale = preferences.text_scale
        self._colour_safe = preferences.colour_safe
        self._reduced_motion = preferences.reduced_motion

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(TextLabel("Settings", style="title", theme=theme))

        root.addWidget(SectionLabel("Text size", theme=theme))
        scales = QHBoxLayout()
        scales.setSpacing(8)
        self._scale_chips: dict[float, Chip] = {}
        for scale, label in SCALES:
            chip = Chip(label, theme=theme, selected=scale == self._scale)
            chip.clicked.connect(lambda scale=scale: self._pick_scale(scale))
            self._scale_chips[scale] = chip
            scales.addWidget(chip)
        scales.addStretch(1)
        root.addLayout(scales)

        root.addWidget(SectionLabel("Display", theme=theme))
        toggles = QHBoxLayout()
        toggles.setSpacing(8)
        self._colour_chip = Chip("Colour-blind safe palette", theme=theme,
                                 selected=self._colour_safe)
        self._colour_chip.clicked.connect(self._toggle_colour)
        self._motion_chip = Chip("Reduced motion", theme=theme, selected=self._reduced_motion)
        self._motion_chip.clicked.connect(self._toggle_motion)
        toggles.addWidget(self._colour_chip)
        toggles.addWidget(self._motion_chip)
        toggles.addStretch(1)
        root.addLayout(toggles)

        root.addWidget(SectionLabel("Shortcuts", theme=theme))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self._keys: dict[str, QKeySequenceEdit] = {}
        for row, (action, (_default, explain)) in enumerate(SHORTCUTS.items()):
            grid.addWidget(TextLabel(explain, style="body", theme=theme, wrap=True), row, 0)
            edit = QKeySequenceEdit(QKeySequence(preferences.key_for(action)))
            edit.setMaximumSequenceLength(1)
            edit.setObjectName(f"shortcut-{action}")
            self._keys[action] = edit
            grid.addWidget(edit, row, 1)
        grid.setColumnStretch(0, 1)
        root.addLayout(grid)

        root.addWidget(Divider(theme=theme))
        root.addWidget(SectionLabel("AI providers", theme=theme))
        self._provider = TextLabel(provider_status or "Provider status is not available yet.",
                                   style="body_small", colour="ink_dim", theme=theme, wrap=True)
        root.addWidget(self._provider)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Chip("Cancel", theme=theme)
        cancel.clicked.connect(self.reject)
        save = Chip("Save", theme=theme, selected=True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(f"QDialog {{ background: {theme.panel}; }}"
                           f"QKeySequenceEdit {{ color: {theme.ink}; }}")
        restyle(self, theme)

    def _pick_scale(self, scale: float) -> None:
        self._scale = scale
        for value, chip in self._scale_chips.items():
            chip.set_selected(value == scale)

    def _toggle_colour(self) -> None:
        self._colour_safe = not self._colour_safe
        self._colour_chip.set_selected(self._colour_safe)

    def _toggle_motion(self) -> None:
        self._reduced_motion = not self._reduced_motion
        self._motion_chip.set_selected(self._reduced_motion)

    def preferences(self) -> Preferences:
        """What the sheet holds now. Only bindings that differ from the default are kept."""
        portable = QKeySequence.SequenceFormat.PortableText
        shortcuts = {}
        for action, edit in self._keys.items():
            key = edit.keySequence().toString(portable)
            # Compared as Qt spells it: the table says "Escape", Qt says "Esc", and a
            # string comparison stored every untouched default as a custom binding.
            default = QKeySequence(SHORTCUTS[action][0]).toString(portable)
            if key and key != default:
                shortcuts[action] = key
        return Preferences(text_scale=self._scale, colour_safe=self._colour_safe,
                           reduced_motion=self._reduced_motion, shortcuts=shortcuts)

    def _save(self) -> None:
        self.saved.emit(self.preferences())
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
