"""Text scale, a palette that survives colour blindness, reduced motion, remappable keys.

Four settings, and the shape of each was decided by what the interface already had rather
than by what a checklist lists:

* **Text scale.** Every font in the program comes from one type scale and passes through one
  function, so a multiplier applied there moves all twenty-five sizes together. No widget is
  told about it and no widget holds a preferences object; they keep asking for "the label
  style".
* **The colour-safe palette.** The map ramp is already blue-to-amber and safe. The risk is
  the *semantic* pair — `good` is a green and `danger` a red, and between them they colour
  every delta, meter and warning in the program. Replacing the green with a blue leaves the
  pair separable under all three common kinds of colour blindness.
* **Reduced motion**, which is honest about how little there is to reduce. This interface
  has no animations and no easing curves; the only thing that moves by itself is the map
  dropping to a coarser rendering while the hand is on it. `MOTION_SURFACES` says so in the
  source, so the preference cannot quietly come to mean nothing.
* **Remappable shortcuts**, because a fixed `Ctrl+Return` is a problem for anybody whose
  hands or keyboard layout disagree with it.

Kept in the workspace rather than a config file: that is already where the program remembers
the last world, the draft orders and the watchlist, and a second store would be a second
thing to find, migrate and back up.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world
from atlas.ui.preferences import (
    MOTION_SURFACES,
    SCALES,
    SHORTCUTS,
    Preferences,
    colour_safe,
    current,
    key_for,
    load_preferences,
    save_preferences,
    set_current,
)
from atlas.ui.theme import DARK, LIGHT, TYPE

WORLD = "sandbox"


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    """**File-backed, because the setting is.** Preferences live in a sidecar beside the
    save, so a world with no file has nowhere to keep them and every round trip through
    `:memory:` would silently be a no-op that the assertions could not tell from a bug."""
    connection = create_world(str(tmp_path / "world.atlas"), sandbox_seed())
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _restore_module_state():
    """The scale and the current preferences are module-level on purpose; a test that set
    one and walked away would change every test after it."""
    from atlas.ui import widgets

    before_scale, before_current = widgets.text_scale(), current()
    yield
    widgets.set_text_scale(before_scale)
    set_current(before_current)


# ------------------------------------------------------------------------- text scale


def test_every_font_in_the_program_scales_together(qapp=None):
    """**The property the setting rests on.** One funnel, so nothing can be missed and
    nothing has to be maintained: a type style that scaled and one that did not would be a
    layout that comes apart at Large."""
    from atlas.ui.widgets import set_text_scale, styled_font

    set_text_scale(1.0)
    before = {key: styled_font(style).pointSizeF() for key, style in TYPE.items()}
    set_text_scale(1.35)
    after = {key: styled_font(style).pointSizeF() for key, style in TYPE.items()}

    assert before
    for key in TYPE:
        assert after[key] == pytest.approx(before[key] * 1.35, abs=0.02), key


def test_the_scale_is_bounded_at_both_ends():
    """A setting that can be driven to zero is a program somebody cannot read their way
    out of."""
    from atlas.ui.widgets import set_text_scale, text_scale

    set_text_scale(0.0)
    assert text_scale() >= 0.5
    set_text_scale(99.0)
    assert text_scale() <= 3.0


def test_the_offered_scales_include_normal_and_go_both_ways():
    offered = [scale for scale, _label in SCALES]

    assert 1.0 in offered
    assert min(offered) < 1.0 < max(offered)


# ---------------------------------------------------------------------- colour safety


@pytest.mark.parametrize("theme", [DARK, LIGHT])
def test_the_safe_palette_separates_the_pair_that_carried_meaning_by_hue(theme):
    """`good` and `danger` are the only two colours in the program a reader is asked to
    tell apart by hue alone."""
    safe = colour_safe(theme)

    assert safe.good != theme.good
    assert safe.good != safe.danger


@pytest.mark.parametrize("theme", [DARK, LIGHT])
def test_it_changes_nothing_that_was_already_safe(theme):
    """The map ramp is cold-blue to warm-amber and the accent is brass. Repainting a
    palette that is already safe would be change for its own sake."""
    safe = colour_safe(theme)

    assert (safe.map_ramp_cold, safe.map_ramp_hot) == (theme.map_ramp_cold,
                                                       theme.map_ramp_hot)
    assert safe.brass == theme.brass
    assert safe.canvas == theme.canvas


def test_it_is_still_a_theme_everything_else_can_use(conn):
    """`dataclasses.replace`, so nothing downstream can tell the difference -- including
    the map, which takes a palette record and asks no questions about where it came from."""
    safe = colour_safe(DARK)

    assert safe.map_palette().ramp_hot == DARK.map_palette().ramp_hot
    assert safe.delta_ink(1.0) == safe.good
    assert safe.delta_ink(-1.0) == safe.danger


# ------------------------------------------------------------------------- shortcuts


def test_every_shortcut_has_a_default_and_something_it_does():
    for action, (key, explain) in SHORTCUTS.items():
        assert key, action
        assert explain, action


def test_a_binding_overrides_the_default_and_the_rest_stay(conn):
    preferences = Preferences(shortcuts={"send": "Alt+Return"})

    assert preferences.key_for("send") == "Alt+Return"
    assert preferences.key_for("find") == SHORTCUTS["find"][0]


def test_widgets_that_are_never_handed_preferences_can_still_ask(conn):
    """Three shortcuts live on widgets that never see the window. Threading a preferences
    object through twenty constructors to reach three of them would put the setting
    everywhere to be read almost nowhere."""
    set_current(Preferences(shortcuts={"dismiss": "Ctrl+W"}))

    assert key_for("dismiss") == "Ctrl+W"


def test_a_binding_for_an_action_that_no_longer_exists_is_dropped(conn):
    """Otherwise it sits in the workspace for ever, shadowing nothing."""
    from atlas.state.workspace import recall, remember

    assert save_preferences(conn, Preferences(shortcuts={"send": "Alt+Return"}))
    stored = recall(conn, "ui_preferences")
    stored["shortcuts"]["teleport"] = "Ctrl+T"
    remember(conn, "ui_preferences", stored)

    assert set(load_preferences(conn).shortcuts) == {"send"}


# --------------------------------------------------------------------- reduced motion


def test_the_one_thing_that_moves_is_written_down():
    """**A preference that silently does nothing is worse than one that is honestly
    small.** If an animation is ever added, its name belongs in this tuple."""
    assert MOTION_SURFACES


def test_the_map_honours_it_where_it_actually_applies():
    from pathlib import Path

    source = Path("src/atlas/ui/map_view.py").read_text(encoding="utf-8")

    assert "reduced_motion" in source
    assert "self._settles" in source


# ------------------------------------------------------------------- kept and reloaded


def test_what_is_saved_comes_back(conn):
    saved = Preferences(text_scale=1.35, colour_safe=True, reduced_motion=True,
                        shortcuts={"find": "Ctrl+K"})
    save_preferences(conn, saved)

    assert load_preferences(conn) == saved


def test_a_fresh_workspace_is_the_defaults(conn):
    assert load_preferences(conn) == Preferences()


def test_nonsense_in_the_store_is_the_defaults_rather_than_a_crash(conn):
    """A preferences file is not worth refusing to start over, and a person can edit it."""
    from atlas.state.workspace import remember

    remember(conn, "ui_preferences", "not a dict at all")
    assert load_preferences(conn) == Preferences()

    remember(conn, "ui_preferences", {"text_scale": 47.0, "shortcuts": "nope"})
    loaded = load_preferences(conn)

    assert loaded.text_scale == 1.0, "a scale nothing offers is not a scale"
    assert loaded.shortcuts == {}


def test_preferences_are_not_world_state(conn):
    """They live beside the save with the pins and the draft orders, for the reason those
    do: a rewind must not be able to change how big somebody's text is."""
    from atlas.state.bootstrap import fingerprint

    before = fingerprint(conn, WORLD)
    assert save_preferences(conn, Preferences(text_scale=1.6, colour_safe=True))

    assert fingerprint(conn, WORLD) == before
