"""Three cards on a player's first new game: who they are, how to give orders, what Advance
does -- shown once, ever, and never with technical details on."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.ui import preferences  # noqa: E402
from atlas.ui.guide import FirstTurnGuide, guide_steps  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_the_guide_names_your_country_and_ends_on_advance():
    steps = guide_steps("Turkey")
    assert [heading for heading, _ in steps] == [
        "You are Turkey", "Give your orders", "Press Advance"]


def test_next_walks_the_cards_and_the_last_one_finishes(qapp):
    guide = FirstTurnGuide(DARK)
    done: list[bool] = []
    guide.finished.connect(lambda: done.append(True))
    guide.start("Turkey")
    assert guide.isVisible() and guide.step == 0
    guide._next.clicked.emit()
    guide._next.clicked.emit()
    assert guide.step == 2 and guide._next._label.text() == "Got it"
    guide._next.clicked.emit()
    assert done == [True] and not guide.isVisible()


def _window(technical: bool, seen: bool, monkeypatch):
    from atlas.ui import main_window

    started: list[str] = []
    monkeypatch.setattr(main_window, "load_app_preferences",
                        lambda: preferences.Preferences(guide_seen=seen))
    fake = SimpleNamespace(
        _preferences=preferences.Preferences(technical=technical),
        _conn=SimpleNamespace(execute=lambda *_a: SimpleNamespace(
            fetchone=lambda: {"name": "Turkey"})),
        _world_id="w", _player="tur",
        _guide=SimpleNamespace(start=started.append, move=lambda *_a: None))
    main_window.AtlasWindow._offer_guide(fake)
    return started


def test_shown_on_a_first_game_and_never_again(monkeypatch):
    assert _window(technical=False, seen=False, monkeypatch=monkeypatch) == ["Turkey"]
    assert _window(technical=False, seen=True, monkeypatch=monkeypatch) == []
    assert _window(technical=True, seen=False, monkeypatch=monkeypatch) == []


def test_saving_settings_does_not_bring_the_guide_back(qapp, monkeypatch):
    """The sheet is handed a save's preferences, which never carry the guide's flag; saving
    them used to reset it and the guide came back on the next new game."""
    from atlas.ui import settings

    monkeypatch.setattr(settings, "load_app_preferences",
                        lambda: preferences.Preferences(guide_seen=True))
    sheet = settings.SettingsScreen(preferences.Preferences(), theme=DARK)
    assert sheet.preferences().guide_seen is True
    sheet._guide_chip.clicked.emit()            # "Show the guide again"
    assert sheet.preferences().guide_seen is False


def test_headlines_open_with_a_capital(monkeypatch):
    from atlas.ui.plain import plain_line

    monkeypatch.setattr(preferences, "_CURRENT", preferences.Preferences())
    assert plain_line("the government no longer governs Suriname.") == (
        "The government no longer governs Suriname.")
