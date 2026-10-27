"""The settings screen: the four existing preferences, plus a slot for the AI providers.

Generated from the tables in :mod:`atlas.ui.preferences` rather than written out by hand --
the text scales from :data:`SCALES`, the shortcuts from :data:`SHORTCUTS` -- so a
preference added there cannot exist without a control here.

It decides nothing about where a preference is kept. It emits :attr:`SettingsScreen.saved`
with a new :class:`Preferences` and the caller stores it: the main menu as the player's
own defaults, an open game window through ``set_preferences``.

**The provider panel** shows the one-line status the caller hands it, and holds the
Anthropic API key box for ``--api`` play. The key is not a preference and does not wait for
Save: *Save key* writes it to the system keychain at once (see
:mod:`atlas.providers.keystore`), the API provider reads it on its next call, and the field
is cleared afterwards so the key is never left on screen.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QVBoxLayout,
)

from .preferences import SCALES, SHORTCUTS, Preferences
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, restyle

__all__ = ["SettingsScreen"]


class _KeyTest(QThread):
    """One tiny API call off the UI thread; answers with a sentence for the status line."""

    answered = Signal(str)

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self._key = key

    def run(self) -> None:
        import asyncio

        from ..providers.api_provider import ApiProvider, api_role_models
        from ..providers.base import ProviderRequest
        from ..state.models import Role

        model = api_role_models()[Role.ADVISOR][1]
        try:
            result = asyncio.run(ApiProvider(self._key).invoke(ProviderRequest(
                role=Role.ADVISOR, prompt="Reply with the single word: ready.",
                model=model, timeout_s=60.0)))
        except Exception as exc:  # noqa: BLE001 - reported, never raised into Qt
            self.answered.emit(f"Test failed: {exc}")
            return
        if result.ok:
            served = result.audit.served if result.audit else model
            self.answered.emit(f"The key works: {served} answered in {result.duration_s:.1f}s.")
        elif result.failure.kind.value == "not_authenticated":
            self.answered.emit("Anthropic refused this key. Check it was copied in full.")
        else:
            self.answered.emit(f"Test failed: {result.failure.message}.")


def _key_status() -> str:
    """What the box should say about the key in use, never the key itself."""
    import os

    from ..providers.api_provider import API_KEY_ENV
    from ..providers.keystore import stored_key

    if os.environ.get(API_KEY_ENV, "").strip():
        return f"Using the key from the {API_KEY_ENV} environment variable."
    saved = stored_key()
    if saved:
        return f"A key is saved (ending …{saved[-4:]}). Paste a new one to replace it."
    return ("No API key saved. Paste your Anthropic API key to play in Claude API mode "
            "(atlas start --api).")


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

        root.addWidget(TextLabel("Anthropic API key", style="body", theme=theme))
        self._key_box = QLineEdit()
        self._key_box.setObjectName("apiKeyBox")
        self._key_box.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_box.setPlaceholderText("sk-ant-…")
        root.addWidget(self._key_box)
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        for label, slot in (("Save key", self._save_key), ("Test", self._test_key),
                            ("Remove", self._remove_key)):
            chip = Chip(label, theme=theme)
            chip.clicked.connect(slot)
            key_row.addWidget(chip)
        key_row.addStretch(1)
        root.addLayout(key_row)
        self._key_status = TextLabel(_key_status(), style="body_small", colour="ink_dim",
                                     theme=theme, wrap=True)
        root.addWidget(self._key_status)
        self._tester: _KeyTest | None = None

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
                           f"QKeySequenceEdit {{ color: {theme.ink}; }}"
                           f"QLineEdit#apiKeyBox {{ background: {theme.inset};"
                           f" color: {theme.ink_bright}; border: 1px solid {theme.edge};"
                           f" padding: 8px 10px; }}"
                           f"QLineEdit#apiKeyBox:focus {{ border: 1px solid {theme.brass}; }}")
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

    # -- the API key: acts at once, independently of Save -----------------------------------

    def _save_key(self) -> None:
        from ..providers.keystore import store_key

        key = self._key_box.text().strip()
        if not key:
            self._key_status.setText("Paste a key into the box first.")
            return
        where = store_key(key)
        self._key_box.clear()
        warning = "" if key.startswith("sk-ant-") else (
            " It does not look like an Anthropic key (they start sk-ant-); press Test.")
        self._key_status.setText(f"Saved in {where}.{warning}")

    def _test_key(self) -> None:
        from ..providers.api_provider import api_key

        key = self._key_box.text().strip() or api_key()
        if not key:
            self._key_status.setText("No key to test. Paste one, or save one first.")
            return
        if self._tester is not None and self._tester.isRunning():
            return
        self._key_status.setText("Testing with one tiny request (a fraction of a cent)…")
        self._tester = _KeyTest(key, self)
        self._tester.answered.connect(self._key_status.setText)
        self._tester.start()

    def _remove_key(self) -> None:
        from ..providers.keystore import forget_key

        forget_key()
        self._key_box.clear()
        self._key_status.setText(_key_status())

    def done(self, result: int) -> None:  # noqa: D401 -- Qt override
        # Closing while a test is in flight must not destroy its thread under it.
        if self._tester is not None and self._tester.isRunning():
            self._tester.wait(65000)
        super().done(result)

    def _save(self) -> None:
        self.saved.emit(self.preferences())
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
