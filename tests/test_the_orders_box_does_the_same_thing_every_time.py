"""Enter checks orders, every time; nothing else checks them by surprise; and Advance
reads anything typed since the last check before the turn runs.

The box used to check on focus-out as well as on Ctrl+Enter, and ignored a second check of
unchanged text -- so a read-back popped up when a player clicked elsewhere, the key
sometimes answered and sometimes did nothing, and orders typed before pressing Advance
could miss the turn they were written for.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QFocusEvent, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.ui.rail_right import _InputBox  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _press(box: _InputBox, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    box._edit.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, modifiers))


def test_enter_checks_every_time_it_is_pressed(qapp):
    box = _InputBox("", height=80, theme=DARK)
    sent: list[str] = []
    box.submitted.connect(sent.append)
    box._edit.setPlainText("Declare war on Iran")
    _press(box)
    _press(box)
    assert sent == ["Declare war on Iran", "Declare war on Iran"]


def test_shift_enter_is_a_new_line_and_not_a_check(qapp):
    box = _InputBox("", height=80, theme=DARK)
    sent: list[str] = []
    box.submitted.connect(sent.append)
    box._edit.setPlainText("Declare war on Iran")
    box._edit.moveCursor(box._edit.textCursor().MoveOperation.End)
    _press(box, Qt.KeyboardModifier.ShiftModifier)
    assert sent == []
    assert box._edit.toPlainText().count("\n") == 1


def test_leaving_the_box_does_not_check_it(qapp):
    box = _InputBox("", height=80, theme=DARK)
    sent: list[str] = []
    box.submitted.connect(sent.append)
    box._edit.setPlainText("Declare war on Iran")
    box._edit.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert sent == []


def _window(text: str, queued: str = ""):
    from atlas.ui.main_window import AtlasWindow

    read: list[str] = []
    fake = SimpleNamespace(
        _right=SimpleNamespace(orders_text=lambda: text), _player="tur",
        _queued_text=queued, _after_reading=None, _on_check_orders=read.append)
    return fake, read, AtlasWindow._read_first


def test_advance_reads_orders_typed_since_the_last_check():
    fake, read, read_first = _window("Capture Iran")
    then = object()
    assert read_first(fake, then) is True
    assert read == ["Capture Iran"] and fake._after_reading is then
    # A second press while it is being read does not read it twice.
    assert read_first(fake, object()) is True and read == ["Capture Iran"]


def test_advance_goes_straight_on_when_the_box_was_already_checked():
    fake, read, read_first = _window("Capture Iran", queued="Capture Iran")
    assert read_first(fake, object()) is False and read == []
    fake, read, read_first = _window("")
    assert read_first(fake, object()) is False and read == []


def _rails(technical: bool):
    from atlas.ui.main_window import AtlasWindow
    from atlas.ui.widgets import Rail

    left = Rail("The record", width=348, opens_right=False, theme=DARK)
    right = Rail("Your turn", width=404, opens_right=True, theme=DARK)
    fake = SimpleNamespace(
        _left=left, _right=right, _rail_choice={},
        _preferences=SimpleNamespace(technical=technical),
        _matters=SimpleNamespace(isVisible=lambda: False, width=lambda: 0))
    return fake, AtlasWindow


def test_the_map_comes_first_the_record_starts_put_away(qapp):
    """Without technical details the record is folded away even on a wide screen, and a
    province click brings it out; with them it stays out where there is room."""
    fake, window = _rails(technical=False)
    window._fit_rails(fake, 2400)
    assert fake._left.collapsed and not fake._right.collapsed
    window._show_record(fake)
    assert not fake._left.collapsed

    fake, window = _rails(technical=True)
    window._fit_rails(fake, 2400)
    assert not fake._left.collapsed and not fake._right.collapsed
