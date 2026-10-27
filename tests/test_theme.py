"""The design system, and the two properties that make a second theme possible.

Both themes must define the same fields -- a widget asking for ``theme.rule`` cannot be
allowed to work in one theme and raise in the other -- and the map has to recolour with
the interface, or a light interface gets a black ocean.
"""

from __future__ import annotations

import os
from dataclasses import fields

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from atlas.map.scene_model import DEFAULT_PALETTE, MapPalette  # noqa: E402
from atlas.ui.theme import (  # noqa: E402
    DARK,
    LIGHT,
    SHAPE_STATES,
    TEXT_STATES,
    TYPE,
    Theme,
    ThemeName,
    theme_for,
)


def test_both_themes_define_every_field():
    """A field present in one theme and missing in the other is a palette bug."""
    for theme in (DARK, LIGHT):
        for field in fields(Theme):
            assert getattr(theme, field.name) is not None, field.name


def test_colours_are_hex():
    for theme in (DARK, LIGHT):
        for field in fields(Theme):
            value = getattr(theme, field.name)
            if field.name == "name":
                continue
            assert isinstance(value, str) and value.startswith("#"), (theme.name, field.name)
            assert len(value) == 7, (theme.name, field.name, value)


def test_dark_and_light_are_actually_different():
    """Guards against a light theme that was copied and never edited."""
    differing = sum(
        1 for field in fields(Theme)
        if field.name != "name" and getattr(DARK, field.name) != getattr(LIGHT, field.name)
    )
    assert differing >= 20


def test_theme_lookup_by_name():
    assert theme_for("dark") is DARK
    assert theme_for(ThemeName.LIGHT) is LIGHT
    with pytest.raises(ValueError):
        theme_for("sepia")


def test_is_dark_is_not_a_guess():
    assert DARK.is_dark
    assert not LIGHT.is_dark


def test_map_palette_follows_the_theme():
    """The renderer's colours come from the theme, so the map changes with the interface."""
    dark = DARK.map_palette()
    light = LIGHT.map_palette()
    assert isinstance(dark, MapPalette)
    assert dark.ocean != light.ocean
    assert dark.unclaimed != light.unclaimed
    assert dark.outline != light.outline
    # And neither is silently the module default, which would mean the wiring is dead.
    assert dark != DEFAULT_PALETTE or light != DEFAULT_PALETTE


def test_meter_colours_exist_in_both_themes():
    for theme in (DARK, LIGHT):
        for kind in ("military", "economy", "stability", "legitimacy", "treasury", "manpower"):
            assert theme.meter_colour(kind).startswith("#")
        # An unknown meter must not raise; a new engine meter should render, not crash.
        assert theme.meter_colour("morale").startswith("#")


def test_delta_ink_direction():
    assert DARK.delta_ink(3) == DARK.good
    assert DARK.delta_ink(-3) == DARK.danger
    assert DARK.delta_ink(0) == DARK.ink_ghost


def test_type_scale_is_closed_and_sane():
    """Every style names one of the three vendored families and a usable size."""
    from atlas.ui.theme import FONT

    families = {FONT.serif, FONT.sans, FONT.mono}
    for key, style in TYPE.items():
        assert style.family in families, key
        assert 7 <= style.size <= 60, key
        assert 100 <= style.weight <= 900, key


def test_fonts_are_vendored_with_their_licences():
    """The application must work with no network at all, so the faces ship with it."""
    from pathlib import Path

    directory = Path(__file__).resolve().parents[1] / "src" / "atlas" / "ui" / "fonts"
    faces = list(directory.glob("*.ttf"))
    licences = list(directory.glob("*OFL*"))
    assert len(faces) >= 5
    assert licences, "OFL-licensed fonts must ship with their licence"


# ---------------------------------------------------------------------------
# Contrast
# ---------------------------------------------------------------------------
#
# The palette had a floor of 2.07:1 -- `ink_ghost`, used 111 times, carrying every
# "Escape to close" hint on every panel, so the text telling a player how to leave a
# screen was the least readable text on it. `ink_faint` carried every section heading in
# the program at 3.5-3.9:1. Neither looked broken in a screenshot; both were unreadable
# on a real monitor at a normal viewing distance.
#
# These are computed, not eyeballed, and they run over both themes and every surface a
# widget is allowed to put text on -- because the failure was never one colour, it was a
# palette with no floor.


def _relative_luminance(hex_colour: str) -> float:
    """WCAG 2.1 relative luminance."""
    channels = []
    for offset in (1, 3, 5):
        value = int(hex_colour[offset:offset + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045
                        else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: str) -> float:
    """WCAG 2.1 contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    first, second = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_the_contrast_maths_is_right():
    """The check is only worth what the formula is."""
    assert contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
    assert contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)
    # The canonical mid-grey: #767676 on white is the boundary case in the WCAG notes.
    assert contrast("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.02)


#: Every surface a widget is allowed to draw text on. `raised` is in here because it is
#: the hover and selected-row background, and a row's dimmest text does not stop being
#: text when the pointer is over it -- which is exactly the case a designer checks last.
SURFACES = ("canvas", "panel", "inset", "raised")

#: Every ink role that carries words. AA is 4.5:1 for normal text, and none of this
#: qualifies as large text: the smallest of them is the 9px section heading.
TEXT_INKS = ("ink", "ink_bright", "ink_body", "ink_dim", "ink_faint", "ink_ghost")

#: The accents, which are also set as text -- the current turn, the relation states, a
#: failure line in the status strip.
ACCENT_INKS = ("brass", "brass_hot", "danger", "good", "info")


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
@pytest.mark.parametrize("ink", TEXT_INKS + ACCENT_INKS)
def test_every_ink_clears_wcag_aa_on_every_surface(theme, ink):
    for surface in SURFACES:
        ratio = contrast(getattr(theme, ink), getattr(theme, surface))
        assert ratio >= 4.5, (
            f"{theme.name}: {ink} on {surface} is {ratio:.2f}:1, below AA")


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
def test_the_ink_ramp_is_ordered(theme):
    """Dimmer has to mean dimmer.

    Raising the floor is not enough on its own: lift one grey past the one above it and
    the palette still passes the check while the hierarchy it encodes is inverted.
    """
    ladder = ["ink_bright", "ink", "ink_body", "ink_dim", "ink_faint", "ink_ghost"]
    ratios = [contrast(getattr(theme, name), theme.panel) for name in ladder]
    assert ratios == sorted(ratios, reverse=True), dict(zip(ladder, ratios, strict=True))


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
def test_every_home_front_band_can_be_seen_against_its_meter(theme):
    """Four of the five meters used to be invisible.

    `uneasy` is the modal band and it was drawn in `ink_ghost`, which put the fill at
    1.49:1 against the track -- so the screen whose whole job is *how happy is each
    faction* could not be read at a glance. 3:1 is the AA floor for a graphical object.
    """
    from atlas.ui.homefront import _BAND_COLOUR

    for band, role in _BAND_COLOUR.items():
        # The track the fill is drawn against; see `homefront._Meter.apply_theme`.
        ratio = contrast(getattr(theme, role), theme.rule)
        assert ratio >= 3.0, f"{theme.name}: the {band} band is {ratio:.2f}:1 on its track"


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
def test_the_colour_safe_palette_clears_aa_as_well(theme):
    """The mode somebody turns on *because* they were having trouble seeing it.

    The substitutes were one set of values used in both themes, which put the blue that
    replaces green at 2.59:1 on a light panel -- worse than the green it was standing in
    for.
    """
    from atlas.ui.preferences import colour_safe

    safe = colour_safe(theme)
    for ink in ("good", "danger", "danger_hot"):
        for surface in SURFACES:
            ratio = contrast(getattr(safe, ink), getattr(safe, surface))
            assert ratio >= 4.5, (
                f"{theme.name} colour-safe: {ink} on {surface} is {ratio:.2f}:1")
    # And the pair it exists to separate is still separated *by lightness*. Two colours
    # that clear AA by the same margin are the same grey once the hue is gone.
    assert contrast(safe.good, safe.danger) >= 1.35


# ---------------------------------------------------------------------------
# The state matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
@pytest.mark.parametrize("state", TEXT_STATES, ids=lambda s: s[0].replace(" ", "-"))
def test_every_text_state_clears_aa(theme, state):
    """Resting body text was never the whole question.

    A palette fix that lifts the resting ink leaves the states that do not use it exactly
    where they were: a selected chip is brass on a wash, a disabled field is the faint
    grey on an inset, a hovered row is body text on a lighter background.
    """
    what, ink, surface = state
    ratio = contrast(getattr(theme, ink), getattr(theme, surface))
    assert ratio >= 4.5, f"{theme.name}: {what} ({ink} on {surface}) is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
@pytest.mark.parametrize("state", SHAPE_STATES, ids=lambda s: s[0].replace(" ", "-"))
def test_every_shape_state_clears_the_non_text_floor(theme, state):
    """WCAG 1.4.11: 3:1 for the visual boundary of a control.

    Every button in this program is a QFrame with a one-pixel outline, and that outline
    used the same colour as the line between two panels -- 1.39:1, a boundary you have to
    already know is there. Hover swapped it for another one at 1.88:1.
    """
    what, ink, surface = state
    ratio = contrast(getattr(theme, ink), getattr(theme, surface))
    assert ratio >= 3.0, f"{theme.name}: {what} ({ink} on {surface}) is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=lambda t: str(t.name))
def test_hover_and_focus_are_told_apart_from_resting(theme):
    """A state nobody can distinguish from the resting one is not a state."""
    assert contrast(theme.edge_hover, theme.edge) >= 1.25, "hover is not a visible change"
    assert contrast(theme.brass, theme.edge) >= 1.4, "focus looks like an ordinary edge"
