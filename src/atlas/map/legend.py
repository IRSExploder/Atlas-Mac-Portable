"""What each map layer is measuring, in the world's words.

**A static legend on a map that changes is not a legend, it is a caption that lies.** The
interface offered four sentences -- *France — you*, *Changed hands this turn*, *Occupied,
not annexed*, *Acting for itself* -- and went on offering exactly those four while the
fill underneath became development, then unrest, then air control, then the ground your
wars are about. Fourteen layers, one key, and thirteen of them wrong.

The answer is not fourteen hand-written legends. It is that a layer says what it is: a
scale kind, what the reading is in, what the two ends of it mean, what a blank province
means, how to put a value into words, and the one line saying what moves it. The legend
widget then has nothing to know and nothing to get out of step with -- it draws whatever
the layer says it is. `test_the_map_says_what_it_is_showing` checks the table is complete,
which is what stops the fifteenth layer shipping with the fourteenth's key.

Pure data and pure functions: no Qt here. What the colours *are* is the theme's business
(:meth:`atlas.ui.theme.Theme.map_palette`); this module only says which of them a layer
uses and what they stand for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .scene_model import MapLayer

__all__ = [
    "LEGENDS",
    "LayerLegend",
    "LegendEntry",
    "ScaleKind",
    "SwatchKind",
    "legend_for",
]


class ScaleKind(StrEnum):
    """How a layer turns the world into colour."""

    #: A handful of named conditions, each with its own swatch. Ownership and the three
    #: emphasis layers built on it.
    CATEGORICAL = "categorical"
    #: A continuous reading painted along the cold-to-hot ramp. The ten measured layers.
    RAMP = "ramp"


class SwatchKind(StrEnum):
    """How to draw the small block beside a legend line.

    Named for the convention rather than for the shape, so the widget that draws them can
    stay the one place that knows what "changed hands" looks like.
    """

    PLAYER = "player"
    CHANGED = "changed"
    CONTESTED = "contested"
    SIEGE = "siege"
    FOCUS = "focus"
    DIMMED = "dimmed"
    UNKNOWN = "unknown"
    RAMP = "ramp"
    BLANK = "blank"


@dataclass(frozen=True, slots=True)
class LegendEntry:
    """One line of a legend: a swatch and a sentence."""

    swatch: SwatchKind
    text: str


def _share(value: float) -> str:
    """A normalised reading as a share of the strongest on the map.

    Most of these layers are scaled against the best or worst province in this world
    rather than against a constant, so the honest unit is a share and not a number: a
    development of 4.2 means nothing without knowing what 4.2 is next to.
    """
    return f"{round(max(0.0, min(1.0, value)) * 100)}% of the strongest here"


def _regard(value: float) -> str:
    """Attitude runs -100..100 and is normalised to 0..1 for the ramp; this reads it back."""
    return f"{round(value * 200 - 100):+d} regard"


def _reach(value: float) -> str:
    """Supply is a distance inverted, so the hot end is the depot."""
    if value >= 0.99:
        return "at the depot"
    return f"{round((1 - value) * 100)}% of the way to the edge of supply"


def _claims(value: float) -> str:
    return "claimed by every side" if value >= 0.99 else "claimed"


@dataclass(frozen=True, slots=True)
class LayerLegend:
    """Everything a legend needs in order to describe a layer without knowing which one."""

    layer: MapLayer
    #: What the layer is called on the bar.
    title: str
    kind: ScaleKind
    #: One line: what moves this reading. The question a player asks second, after
    #: working out what the colour means.
    driven_by: str
    #: RAMP only: what the reading is a measurement of.
    unit: str = ""
    #: RAMP only: what the two ends of the ramp mean, in words. Kept to about twenty
    #: characters, because they sit at the two ends of one line under the ramp -- and a
    #: label for the cold end that elides into the label for the hot end is worse than no
    #: label at all. The long version is `unit`, which has a line of its own.
    cold: str = ""
    hot: str = ""
    #: What a province with no colour on this layer means. Empty when nothing is blank --
    #: and the difference matters: on the nuclear layer blank is *out of your reach*, and
    #: painting it the floor of the scale would read as an invitation.
    blank: str = ""
    #: CATEGORICAL only: the conditions this layer distinguishes.
    entries: tuple[LegendEntry, ...] = ()
    #: RAMP only: a normalised 0..1 reading, in words.
    value: Callable[[float], str] = field(default=_share, compare=False)


#: The four conventions the political map uses everywhere, which the old static legend
#: described -- correctly, for one layer out of fourteen.
_POLITICAL = (
    LegendEntry(SwatchKind.PLAYER, "{nation} — you"),
    LegendEntry(SwatchKind.CHANGED, "Changed hands this turn"),
    LegendEntry(SwatchKind.CONTESTED, "Occupied, not annexed"),
    LegendEntry(SwatchKind.SIEGE, "Under attack"),
    LegendEntry(SwatchKind.FOCUS, "Acting for itself"),
)

LEGENDS: dict[MapLayer, LayerLegend] = {
    MapLayer.POLITICAL: LayerLegend(
        MapLayer.POLITICAL, "Political", ScaleKind.CATEGORICAL,
        driven_by="Conquest, annexation and the treaties that confirm them",
        entries=_POLITICAL),
    MapLayer.FOCUS: LayerLegend(
        MapLayer.FOCUS, "Focus", ScaleKind.CATEGORICAL,
        driven_by="How many powers you are tracking, and which the world is watching",
        entries=(
            LegendEntry(SwatchKind.FOCUS, "Deciding for itself this turn"),
            LegendEntry(SwatchKind.DIMMED, "Carried along by events"),
            LegendEntry(SwatchKind.PLAYER, "{nation} — you"))),
    MapLayer.FRONTS: LayerLegend(
        MapLayer.FRONTS, "Fronts", ScaleKind.CATEGORICAL,
        driven_by="Declarations of war, offensives, and ground that changed hands",
        entries=(
            LegendEntry(SwatchKind.CONTESTED, "Occupied, not annexed"),
            LegendEntry(SwatchKind.SIEGE, "Under attack"),
            LegendEntry(SwatchKind.CHANGED, "Changed hands this turn"),
            LegendEntry(SwatchKind.DIMMED, "At peace"))),
    MapLayer.FOG: LayerLegend(
        MapLayer.FOG, "Fog", ScaleKind.CATEGORICAL,
        driven_by="Your embassies, your services, and where your forces have been",
        entries=(
            LegendEntry(SwatchKind.PLAYER, "{nation} — you"),
            LegendEntry(SwatchKind.CHANGED, "Reported to you"),
            LegendEntry(SwatchKind.UNKNOWN, "No report — you do not know"))),
    MapLayer.DEVELOPMENT: LayerLegend(
        MapLayer.DEVELOPMENT, "Wealth", ScaleKind.RAMP,
        unit="What each province is economically worth",
        cold="poorest", hot="richest here",
        driven_by="Public works, industry, and the ground being at peace long enough to use"),
    MapLayer.UNREST: LayerLegend(
        MapLayer.UNREST, "Unrest", ScaleKind.RAMP,
        unit="How hard held foreign ground is to hold, after the garrison on it",
        cold="quiet", hot="worst here",
        blank="Nothing is restive anywhere yet",
        driven_by="Occupation, conscription, and how long ago the ground was taken"),
    MapLayer.AIR: LayerLegend(
        MapLayer.AIR, "Air", ScaleKind.RAMP,
        unit="Who holds the sky, and how decisively",
        cold="contested", hot="uncontested",
        blank="Nobody has flown over this",
        driven_by="Air formations in range, and what has been shot down"),
    MapLayer.SUPPLY: LayerLegend(
        MapLayer.SUPPLY, "Supply", ScaleKind.RAMP,
        unit="How far your supply reaches",
        cold="edge of supply", hot="at the depot",
        blank="Out of supply — an army here fights at a fraction of its strength",
        driven_by="Where your depots are, and what stands between them and here",
        value=_reach),
    MapLayer.NUCLEAR: LayerLegend(
        MapLayer.NUCLEAR, "Nuclear", ScaleKind.RAMP,
        unit="What your services believe would answer a strike here",
        cold="nothing answers", hot="the heaviest answer",
        blank="Out of your delivery reach — there is no decision to make here",
        driven_by="Your delivery range, and what your services believe others hold"),
    MapLayer.ATTITUDE: LayerLegend(
        MapLayer.ATTITUDE, "Attitude", ScaleKind.RAMP,
        unit="How each power regards you",
        cold="hostile", hot="warm",
        blank="Your own ground, or a power with no view of you",
        driven_by="What you have signed, what you have taken, and what you have said",
        value=_regard),
    MapLayer.WAR_AIMS: LayerLegend(
        MapLayer.WAR_AIMS, "War aims", ScaleKind.RAMP,
        unit="The ground your wars are actually about",
        cold="one side wants it", hot="every side does",
        blank="Nobody is fighting for this",
        driven_by="The aims declared when a war opened, and any added since",
        value=_claims),
    MapLayer.TRADE: LayerLegend(
        MapLayer.TRADE, "Trade", ScaleKind.RAMP,
        unit="What moves between you and whoever holds this ground",
        cold="a trickle", hot="your heaviest route",
        blank="No route runs here",
        driven_by="Standing agreements to sell, and whether the sea lanes are open"),
    MapLayer.OBLIGATIONS: LayerLegend(
        MapLayer.OBLIGATIONS, "Obligations", ScaleKind.RAMP,
        unit="What honouring what you signed would cost",
        cold="a formality", hot="an army in the field",
        blank="Nothing signed either way",
        driven_by="Pacts, guarantees and alliances you are a party to"),
    MapLayer.INDEPENDENCE: LayerLegend(
        MapLayer.INDEPENDENCE, "Independence", ScaleKind.RAMP,
        unit="Which held ground is on its way out",
        cold="settled", hot="closest to leaving",
        blank="Not held by anyone it wants to leave",
        driven_by="How long the ground has been held, how it was taken, and what it costs"),
}


def legend_for(layer: MapLayer) -> LayerLegend:
    """What to draw beside the map right now.

    Falls back to the political key rather than raising: a layer with no entry is a bug in
    this table, and a map with a slightly wrong legend is a better failure than a map with
    no interface at all. The test is what makes sure it never comes to that.
    """
    return LEGENDS.get(layer, LEGENDS[MapLayer.POLITICAL])
