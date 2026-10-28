"""The settings screen: the four existing preferences, plus a slot for the AI providers.

Generated from the tables in :mod:`atlas.ui.preferences` rather than written out by hand --
the text scales from :data:`SCALES`, the shortcuts from :data:`SHORTCUTS` -- so a
preference added there cannot exist without a control here.

It decides nothing about where a preference is kept. It emits :attr:`SettingsScreen.saved`
with a new :class:`Preferences` and the caller stores it: the main menu as the player's
own defaults, an open game window through ``set_preferences``.

**The provider panel** shows the one-line status the caller hands it, a Claude | Gemini
switch for which hosted model ``--api`` play uses, and the key box for the one selected.
Neither is a preference and neither waits for Save: the switch is remembered at once (see
:mod:`atlas.providers.api_choice`), *Save key* writes the key to the system keychain at
once (see :mod:`atlas.providers.keystore`), the provider reads it on its next call, and the
field is cleared afterwards so the key is never left on screen.
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

from .preferences import SCALES, SHORTCUTS, Preferences, load_app_preferences
from .theme import Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, restyle

__all__ = ["SettingsScreen"]


class _KeyTest(QThread):
    """One tiny API call off the UI thread; answers with a sentence for the status line."""

    answered = Signal(str)

    def __init__(self, key: str, choice: str = "claude", parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._choice = choice

    def run(self) -> None:
        import asyncio

        from ..providers.api_choice import api_provider, role_models
        from ..providers.base import ProviderRequest
        from ..state.models import Role

        model = role_models(self._choice)[Role.ADVISOR][1]
        try:
            result = asyncio.run(api_provider(self._choice, key=self._key).invoke(
                ProviderRequest(role=Role.ADVISOR, prompt="Reply with the single word: ready.",
                                model=model, timeout_s=60.0)))
        except Exception as exc:  # noqa: BLE001 - reported, never raised into Qt
            self.answered.emit(f"Test failed: {exc}")
            return
        if result.ok:
            served = result.audit.served if result.audit else model
            self.answered.emit(f"The key works: {served} answered in {result.duration_s:.1f}s.")
        elif result.failure.kind.value == "not_authenticated":
            who = "Google" if self._choice == "gemini" else "Anthropic"
            self.answered.emit(f"{who} refused this key. Check it was copied in full.")
        else:
            self.answered.emit(f"Test failed: {result.failure.message}.")


#: Per provider: the environment variable, the keychain account, how its keys start, and
#: where a player gets one.
_KEY_FACTS = {
    "claude": ("ATLAS_ANTHROPIC_API_KEY", "anthropic-api-key", "sk-ant-",
               "Anthropic API key", "console.anthropic.com"),
    "gemini": ("ATLAS_GEMINI_API_KEY", "gemini-api-key", "AIza",
               "Gemini API key", "aistudio.google.com"),
}


def _key_status(choice: str = "claude") -> str:
    """What the box should say about the key in use, never the key itself."""
    import os

    from ..providers.api_choice import LABELS
    from ..providers.keystore import stored_key

    env, account, _prefix, noun, where = _KEY_FACTS[choice]
    if os.environ.get(env, "").strip():
        return f"Using the key from the {env} environment variable."
    saved = stored_key(account)
    if saved:
        return f"A key is saved (ending …{saved[-4:]}). Paste a new one to replace it."
    return (f"No {noun} saved. Paste one (from {where}) to play with "
            f"{LABELS[choice]} writing the world.")


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
        self._technical = preferences.technical
        # Read from the player's own defaults, where the guide records itself: the sheet is
        # handed a save's preferences, which never carry it, and saving those used to put
        # the guide back on the next new game.
        self._guide_seen = load_app_preferences().guide_seen

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
        self._technical_chip = Chip("Show technical details", theme=theme,
                                    selected=self._technical)
        self._technical_chip.setToolTip(
            "The engine's workings: odds and figures in headlines, why each item was picked, "
            "every power's refused decisions, all fourteen map modes, the seed")
        self._technical_chip.clicked.connect(self._toggle_technical)
        toggles.addWidget(self._technical_chip)
        self._guide_chip = Chip("Show the guide again", theme=theme,
                                selected=not self._guide_seen)
        self._guide_chip.setToolTip(
            "The three getting-started cards, on the next new game you start")
        self._guide_chip.clicked.connect(self._toggle_guide)
        toggles.addWidget(self._guide_chip)
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

        from ..providers.api_choice import CHOICES, LABELS, chosen

        self._choice = chosen()
        pick = QHBoxLayout()
        pick.setSpacing(8)
        pick.addWidget(TextLabel("Model", style="body", theme=theme))
        self._choice_chips: dict[str, Chip] = {}
        for choice in CHOICES:
            chip = Chip(LABELS[choice], theme=theme, selected=choice == self._choice)
            chip.setToolTip({"claude": "Claude Haiku 5.5, over the Anthropic API",
                             "gemini": "Gemini 3.5 Flash-Lite, over the Gemini API"}[choice])
            chip.clicked.connect(lambda choice=choice: self._pick_provider(choice))
            self._choice_chips[choice] = chip
            pick.addWidget(chip)
        pick.addStretch(1)
        root.addLayout(pick)

        self._key_label = TextLabel(_KEY_FACTS[self._choice][3], style="body", theme=theme)
        root.addWidget(self._key_label)
        self._key_box = QLineEdit()
        self._key_box.setObjectName("apiKeyBox")
        self._key_box.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_box.setPlaceholderText(_KEY_FACTS[self._choice][2] + "…")
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
        self._key_status = TextLabel(_key_status(self._choice), style="body_small",
                                     colour="ink_dim", theme=theme, wrap=True)
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

    def _toggle_technical(self) -> None:
        self._technical = not self._technical
        self._technical_chip.set_selected(self._technical)

    def _toggle_guide(self) -> None:
        self._guide_seen = not self._guide_seen
        self._guide_chip.set_selected(not self._guide_seen)

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
                           reduced_motion=self._reduced_motion, shortcuts=shortcuts,
                           technical=self._technical, guide_seen=self._guide_seen)

    # -- the API key: acts at once, independently of Save -----------------------------------

    def _pick_provider(self, choice: str) -> None:
        """Switch the key box to ``choice`` and make it the model API mode plays with.

        Acts at once, like the key itself, because it is read at launch rather than from
        the preferences Save writes.
        """
        from ..providers.api_choice import set_chosen

        self._choice = choice
        set_chosen(choice)
        for name, chip in self._choice_chips.items():
            chip.set_selected(name == choice)
        self._key_label.setText(_KEY_FACTS[choice][3])
        self._key_box.clear()
        self._key_box.setPlaceholderText(_KEY_FACTS[choice][2] + "…")
        from ..providers.api_choice import key_for

        self._key_status.setText(_key_status(choice)
                                 + (self._restart_note() if key_for(choice) else ""))

    def _restart_note(self) -> str:
        """The mode is chosen at launch, so a session without this model in its seats --
        bots only, or the other provider -- picks the change up from the next one."""
        from ..engine import bots
        from ..providers.api_choice import LABELS, launched

        if bots.controls_role("world_sim") or launched() != self._choice:
            return f" Quit and reopen ATLAS to play with {LABELS[self._choice]}."
        return ""

    def _save_key(self) -> None:
        from ..providers.api_choice import set_chosen
        from ..providers.keystore import store_key

        key = self._key_box.text().strip()
        if not key:
            self._key_status.setText("Paste a key into the box first.")
            return
        _env, account, prefix, noun, _where = _KEY_FACTS[self._choice]
        where = store_key(key, account)
        set_chosen(self._choice)
        self._key_box.clear()
        warning = "" if key.startswith(prefix) else (
            f" It does not look like an {noun} (they start {prefix}); press Test.")
        self._key_status.setText(f"Saved in {where}.{warning}{self._restart_note()}")

    def _test_key(self) -> None:
        from ..providers.api_choice import key_for

        key = self._key_box.text().strip() or key_for(self._choice)
        if not key:
            self._key_status.setText("No key to test. Paste one, or save one first.")
            return
        if self._tester is not None and self._tester.isRunning():
            return
        self._key_status.setText("Testing with one tiny request (a fraction of a cent)…")
        self._tester = _KeyTest(key, self._choice, self)
        self._tester.answered.connect(self._key_status.setText)
        self._tester.start()

    def _remove_key(self) -> None:
        from ..providers.keystore import forget_key

        forget_key(_KEY_FACTS[self._choice][1])
        self._key_box.clear()
        self._key_status.setText(_key_status(self._choice))

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
