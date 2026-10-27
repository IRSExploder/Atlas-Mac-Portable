"""The top bar reads in words -- *Weak*, *42 ↓* -- unless technical details are on."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.ui import preferences  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402
from atlas.ui.topbar import _AskableMeter  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _meter(monkeypatch, *, technical: bool, value: float, delta: float):
    monkeypatch.setattr(preferences, "_CURRENT", preferences.Preferences(technical=technical))
    meter = _AskableMeter("economy", "Economy", theme=DARK)
    meter.set_value(value, value / 100, "#ffffff", delta=delta)
    meter.read(value, 100.0, delta)
    return meter


def test_a_weak_economy_says_so_and_which_way_it_is_going(qapp, monkeypatch):
    meter = _meter(monkeypatch, technical=False, value=14, delta=-0.5)
    assert meter._value.text() == "Weak"
    assert meter._value._colour_key == "danger"  # one of the bottom two bands
    assert meter._delta.text() == "14 ↓"


def test_a_sound_economy_is_not_alarming_and_a_still_one_has_no_arrow(qapp, monkeypatch):
    meter = _meter(monkeypatch, technical=False, value=60, delta=0.0)
    assert meter._value.text() == "Solid"
    assert meter._value._colour_key == "ink"
    assert meter._delta.text() == "60"


def test_technical_details_keep_the_numbers(qapp, monkeypatch):
    meter = _meter(monkeypatch, technical=True, value=14, delta=-0.5)
    assert meter._value.text() == "14"
    assert meter._delta.text() == "-0.5"
