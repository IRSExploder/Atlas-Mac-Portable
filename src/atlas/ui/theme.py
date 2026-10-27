"""The design system: palette, type, spacing.

Every colour and font size in the interface comes from here. Widgets never write a hex
value inline -- that is what makes a second theme possible at all, and what stops the
palette drifting into forty near-identical greys.

**Two themes, one structure.** :class:`Theme` holds the same field names for dark and
light, so a widget asks for ``theme.rule`` and gets the right value without knowing which
theme is active. Anything that only exists in one theme is a bug in the palette, not a
special case in the widget.

**Fonts are vendored, not fetched.** The design specifies Spectral and IBM Plex, both
OFL-licensed. They ship in ``ui/fonts`` with their licences because the application must
work with no network at all -- the whole point of driving local CLIs is that nothing
depends on a connection at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

__all__ = [
    "DARK", "LIGHT", "PANEL", "PANEL_INSET", "SPACE", "Font", "PanelWidth",
    "Space", "Theme", "ThemeName",
    "load_fonts", "theme_for",
]

log = logging.getLogger(__name__)


class ThemeName(StrEnum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class Font:
    """The three faces the design uses, and what each is for."""

    #: Spectral. Prose, nation names, dates, headlines -- anything meant to be read.
    serif: str = "Spectral"
    #: IBM Plex Sans. Labels, buttons, small caps -- interface furniture.
    sans: str = "IBM Plex Sans"
    #: IBM Plex Mono. Numbers, ids, timings -- anything that should align in a column.
    mono: str = "IBM Plex Mono"


@dataclass(frozen=True, slots=True)
class Theme:
    name: ThemeName

    # -- surfaces ----------------------------------------------------------
    #: Deepest background, behind everything.
    void: str
    #: Main application background.
    canvas: str
    #: Panels and rails.
    panel: str
    #: Inset fields, cards, list rows.
    inset: str
    #: Hover / selected row background.
    raised: str

    # -- lines -------------------------------------------------------------
    #: Structural borders between panels.
    rule: str
    #: Lighter dividers inside a panel.
    rule_soft: str
    #: Border on hover.
    rule_hover: str

    # -- control edges -----------------------------------------------------
    #
    # **A line that separates two panels and a line that says "this is a button" are not
    # the same line.** They were: everything used `rule`, which sits at 1.39:1 against the
    # surface behind it -- fine for structure, where the eye is following a large shape,
    # and not fine for the outline that is the entire visual boundary of a control. Every
    # button in the program was a rectangle you had to already know was there. WCAG asks
    # 3:1 for the boundary of a user interface component, and these clear it against every
    # surface a control is drawn on.
    #: The outline of a control: a chip, an icon button, a text field.
    edge: str
    #: The same, under the pointer. Visibly a change, not a suspicion of one.
    edge_hover: str

    # -- ink ---------------------------------------------------------------
    #: Primary text.
    ink: str
    #: Headline text, slightly brighter than primary.
    ink_bright: str
    #: Body prose.
    ink_body: str
    #: Secondary text.
    ink_dim: str
    #: Labels and captions.
    ink_faint: str
    #: Barely-there text: paths, ids, timings.
    ink_ghost: str

    # -- accents -----------------------------------------------------------
    #: The single accent. Used for the brand, the primary action, and the current turn.
    brass: str
    brass_hot: str
    #: Selected/active background tint behind brass text.
    brass_wash: str
    brass_line: str

    danger: str
    danger_hot: str
    good: str
    info: str

    # -- map ---------------------------------------------------------------
    map_ocean: str
    map_unknown: str
    map_unclaimed: str
    map_outline: str
    map_outline_unknown: str
    map_selected: str
    map_changed: str
    map_contested: str
    map_ramp_cold: str
    map_ramp_hot: str
    map_graticule: str | None
    map_label: str
    map_label_halo: str
    map_marker_pen: str

    @property
    def is_dark(self) -> bool:
        return self.name is ThemeName.DARK

    def meter_colour(self, kind: str) -> str:
        """Colour for a top-bar meter. Kept here so both themes stay in step."""
        return {
            "military": self.info,
            "economy": self.info,
            "stability": self.danger,
            "legitimacy": self.good,
            "treasury": self.brass,
            "manpower": self.ink_dim,
        }.get(kind, self.ink_dim)

    def map_palette(self):
        """This theme expressed as the pure renderer's palette.

        The conversion lives here rather than in ``map/`` so the renderer keeps no
        knowledge of the interface at all -- it takes a record of colours and asks no
        questions about where they came from.
        """
        from ..map.scene_model import MapPalette

        return MapPalette(
            ocean=self.map_ocean,
            unknown=self.map_unknown,
            unclaimed=self.map_unclaimed,
            outline=self.map_outline,
            outline_unknown=self.map_outline_unknown,
            outline_changed=self.map_changed,
            outline_selected=self.map_selected,
            outline_contested=self.map_contested,
            ramp_cold=self.map_ramp_cold,
            ramp_hot=self.map_ramp_hot,
            tracked=self.brass,
            marker_unowned=self.map_label,
            marker_outline=self.map_marker_pen,
            marker_text=self.map_label,
        )

    def delta_ink(self, delta: float) -> str:
        if delta > 0:
            return self.good
        if delta < 0:
            return self.danger
        return self.ink_ghost


DARK = Theme(
    name=ThemeName.DARK,
    void="#05080C",
    canvas="#0A0E14",
    panel="#0D131B",
    inset="#111823",
    raised="#1C2836",
    rule="#24303F",
    rule_soft="#1A2431",
    rule_hover="#33445A",
    edge="#567397",
    edge_hover="#7690B1",
    ink="#E9EEF5",
    ink_bright="#F2F6FB",
    ink_body="#C9D5E2",
    ink_dim="#93A1B2",
    ink_faint="#8795A8",
    ink_ghost="#7E8FA6",
    brass="#E8B44A",
    brass_hot="#F4CE7C",
    brass_wash="#1B1508",
    brass_line="#8A6A28",
    danger="#E16963",
    danger_hot="#EC807B",
    good="#6FBF8C",
    info="#6FA8DC",
    map_ocean="#0C1118",
    map_unknown="#1B222C",
    map_unclaimed="#39424F",
    map_outline="#0A0E14",
    map_outline_unknown="#4E5A6B",
    map_selected="#F4EDDD",
    map_changed="#E8B44A",
    map_contested="#E0625C",
    map_ramp_cold="#1F3A5F",
    map_ramp_hot="#E8B44A",
    map_graticule="#18212C",
    map_label="#E6EDF5",
    map_label_halo="#0A0E14",
    map_marker_pen="#0A0E14",
)

LIGHT = Theme(
    name=ThemeName.LIGHT,
    void="#E7E0D0",
    canvas="#EFE9DB",
    panel="#FBF8F2",
    inset="#F4F0E6",
    raised="#F1E4C9",
    rule="#DCD4C4",
    rule_soft="#E7E1D4",
    rule_hover="#885E16",
    edge="#937F57",
    edge_hover="#885E16",
    ink="#17202B",
    ink_bright="#0F1720",
    ink_body="#2B3542",
    ink_dim="#525965",
    ink_faint="#656155",
    ink_ghost="#6E6655",
    brass="#885E16",
    brass_hot="#6B4A11",
    brass_wash="#F1E4C9",
    brass_line="#885E16",
    danger="#B1403B",
    danger_hot="#93332F",
    good="#3D7051",
    info="#3B6997",
    map_ocean="#E7E0D0",
    map_unknown="#D6CFC0",
    map_unclaimed="#C8C0AE",
    map_outline="#8A8474",
    map_outline_unknown="#807A69",
    map_selected="#16202C",
    map_changed="#A87A22",
    map_contested="#B4413C",
    map_ramp_cold="#9FB6D4",
    map_ramp_hot="#B8791C",
    map_graticule="#D9D1C0",
    map_label="#2B3542",
    map_label_halo="#F6F2E9",
    map_marker_pen="#FBF8F2",
)

FONT = Font()

_THEMES = {ThemeName.DARK: DARK, ThemeName.LIGHT: LIGHT}


def theme_for(name: ThemeName | str) -> Theme:
    return _THEMES[ThemeName(name)]


# ---------------------------------------------------------------------------
# Type scale
# ---------------------------------------------------------------------------
#
# Named after their job rather than their size, so a caller asks for "the label style"
# and cannot quietly invent a fourteenth font size.

@dataclass(frozen=True, slots=True)
class TypeStyle:
    family: str
    size: float
    weight: int
    #: Extra letter spacing in em. The design leans on wide tracking for small caps.
    tracking: float = 0.0
    italic: bool = False
    upper: bool = False


TYPE = {
    "display": TypeStyle(FONT.serif, 52, 300),
    "title": TypeStyle(FONT.serif, 34, 300),
    "heading": TypeStyle(FONT.serif, 26, 400),
    "modal": TypeStyle(FONT.serif, 22, 400),
    "nation": TypeStyle(FONT.serif, 19, 500),
    "date": TypeStyle(FONT.serif, 17, 500),
    "row": TypeStyle(FONT.serif, 15, 500),
    "row_small": TypeStyle(FONT.serif, 14, 500),
    "prose": TypeStyle(FONT.serif, 13.5, 400),
    "prose_italic": TypeStyle(FONT.serif, 13.5, 400, italic=True),
    "brand": TypeStyle(FONT.sans, 14, 600, tracking=0.34),
    "brand_large": TypeStyle(FONT.sans, 20, 600, tracking=0.42),
    "action": TypeStyle(FONT.sans, 13, 600, tracking=0.22, upper=True),
    "button": TypeStyle(FONT.sans, 10, 500, tracking=0.12, upper=True),
    "tab": TypeStyle(FONT.sans, 10, 500, tracking=0.10, upper=True),
    "section": TypeStyle(FONT.sans, 9, 500, tracking=0.18, upper=True),
    "label": TypeStyle(FONT.sans, 9, 500, tracking=0.14, upper=True),
    #: The top bar has six of these side by side; it needs the tighter setting.
    "meter": TypeStyle(FONT.sans, 7.5, 500, tracking=0.05, upper=True),
    "body": TypeStyle(FONT.sans, 12.5, 400),
    "body_small": TypeStyle(FONT.sans, 11, 400),
    "num": TypeStyle(FONT.mono, 11, 500),
    "num_large": TypeStyle(FONT.mono, 19, 500),
    "num_huge": TypeStyle(FONT.mono, 22, 500),
    "mono": TypeStyle(FONT.mono, 10, 400),
    "mono_small": TypeStyle(FONT.mono, 9, 400),
}


# ---------------------------------------------------------------------------
# Spacing scale
# ---------------------------------------------------------------------------
#
# The third leg of the design system, and the one that was missing. There was a colour
# system and a type system but no space system, so seventeen panels came out at thirteen
# widths with sixty-two different margin recipes -- which is the mechanical reason they
# did not read as one family however carefully each was drawn on its own.
#
# Six steps, each a multiple of four, named for what they separate rather than for how
# big they are. A caller asks for "the gap between blocks" and cannot quietly invent a
# twenty-third margin value.

@dataclass(frozen=True, slots=True)
class Space:
    #: Hairline: between a glyph and its label, a value and its unit.
    xs: int = 4
    #: Between items in a row -- chips, meters, buttons.
    sm: int = 8
    #: Inside a small component; the default list-row padding.
    md: int = 12
    #: Between blocks within a panel, and a panel's own inner margin at the small size.
    lg: int = 16
    #: Between sections; the standard panel inner margin.
    xl: int = 24
    #: Around a modal's content, and between a heading and what it heads.
    xxl: int = 32


SPACE = Space()

#: What a panel keeps between its edge and the window's, so nothing runs to the bezel.
#: A panel is capped at ``window - 2 * PANEL_INSET`` in both directions -- which is the
#: whole of the vertical rule, and the reason Branches and the Home Front no longer open
#: at 950px tall in a 950px window with their headers cut off at the top.
PANEL_INSET = SPACE.xxl


@dataclass(frozen=True, slots=True)
class PanelWidth:
    """The three sizes an overlay is allowed to be.

    Seventeen panels came out at thirteen widths and sixteen heights, every one of them a
    number somebody typed while looking at that panel alone. There is no reading of a
    600-vs-660-vs-680 difference other than "these were drawn on different days", and it
    is most of why the panels did not feel like one family.

    A panel is capped at the window's width regardless, so `screen` on a 1100px window is
    1100 - 2 * `PANEL_INSET` rather than something off the edge of it.
    """

    #: A question with two answers. Rewind, Undo, Export, Confirm.
    dialog: int = 680
    #: The ordinary reading surface: a list, a roster, a briefing.
    panel: int = 900
    #: A table with columns that have to line up. Strategy, the Inspector, the Dispatch.
    screen: int = 1200


PANEL = PanelWidth()


# ---------------------------------------------------------------------------
# The states a control has, and what each is made of
# ---------------------------------------------------------------------------
#
# **A palette that clears AA for body text can still have an invisible selection.** The
# floor was raised on resting ink and every state inherited it -- except the ones that do
# not use resting ink: a selected chip is brass on a wash, a disabled field is the faint
# grey, a hover is a border swap, and a focus ring was nothing at all. Each is a different
# pair of colours and each has to be checked as a pair.
#
# This table is the pairs. It is what `widgets.control_qss` and the rest are written
# against and what `test_theme` measures, so a state cannot be styled into existence
# without also being checked -- which is how the hover border spent the whole project at
# 1.88:1 and the focus ring spent it at zero.

#: ``(what it is, foreground role, background role)`` for text. AA wants 4.5:1; none of
#: this qualifies as large text, the smallest being the 9px section heading.
TEXT_STATES: tuple[tuple[str, str, str], ...] = (
    ("resting label", "ink_dim", "panel"),
    ("resting caption", "ink_faint", "panel"),
    ("resting hint", "ink_ghost", "panel"),
    ("selected chip", "brass", "brass_wash"),
    ("pressed control", "ink_dim", "brass_wash"),
    ("hovered row", "ink_body", "raised"),
    ("hovered row caption", "ink_faint", "raised"),
    ("disabled control", "ink_faint", "inset"),
    ("disabled menu entry", "ink_faint", "panel"),
    ("field text", "ink", "inset"),
    ("placeholder", "ink_faint", "inset"),
)

#: The same for anything that is a shape rather than a word -- WCAG 1.4.11, 3:1. A
#: control's outline is its entire visual boundary; ``rule`` at 1.39:1 meant every button
#: in the program was a rectangle you had to already know was there.
SHAPE_STATES: tuple[tuple[str, str, str], ...] = (
    ("control edge on a panel", "edge", "panel"),
    ("control edge on an inset", "edge", "inset"),
    ("control edge on a raised row", "edge", "raised"),
    ("control edge on the canvas", "edge", "canvas"),
    ("hovered edge", "edge_hover", "panel"),
    ("hovered edge on an inset", "edge_hover", "inset"),
    ("selected edge", "brass_line", "brass_wash"),
    ("focus ring", "brass", "panel"),
    ("focus ring on an inset", "brass", "inset"),
    ("focus ring on the canvas", "brass", "canvas"),
)

# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_loaded = False


def load_fonts() -> list[str]:
    """Register the vendored fonts with Qt. Safe to call more than once.

    Returns the families Qt actually registered, so Diagnostics can show whether the
    interface is rendering in its intended type or in a fallback.
    """
    global _loaded
    from PySide6.QtGui import QFontDatabase

    if _loaded:
        return sorted(set(QFontDatabase.families()) & {FONT.serif, FONT.sans, FONT.mono})

    families: set[str] = set()
    try:
        directory = resources.files("atlas.ui").joinpath("fonts")
        paths = [Path(str(entry)) for entry in directory.iterdir() if str(entry).endswith(".ttf")]
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):  # pragma: no cover
        paths = []

    for path in sorted(paths):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            log.warning("could not load font %s", path.name)
            continue
        families.update(QFontDatabase.applicationFontFamilies(font_id))

    missing = {FONT.serif, FONT.sans, FONT.mono} - families
    if missing:
        # Not fatal: Qt substitutes, the interface just looks wrong rather than breaking.
        log.warning("missing font families, falling back to system faces: %s", sorted(missing))

    _loaded = True
    return sorted(families)
