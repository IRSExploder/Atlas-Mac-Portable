"""The value type sitting between world state and Qt.

Rendering is split in two, with this module as the seam::

    MapViewState --build_scene()--> SceneDescription --SceneApplier--> QGraphicsScene
     (pure data)   PURE, no Qt        (pure data)       mechanical        (Qt)

Everything interesting happens on the left of that second arrow, where it can be tested
without a display, hashed for comparison, and reasoned about as plain data. The applier
on the right is deliberately stupid: it copies fields onto graphics items and does no
thinking at all.

**A SceneDescription holds no polygon coordinates.** It says *which* geometry key to draw
at *which* level of detail, in *what* colour -- never the outline itself. Geometry is
immutable and lives in the baked pack; the applier resolves and caches it keyed only by
``(geo_key, lod, projection)``. Keeping coordinates out of this type is what makes the
scene description small enough to build from scratch every single turn, which is in turn
what makes desync impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_PALETTE",
    "FillPattern",
    "MapLayer",
    "MapPalette",
    "MarkerKind",
    "MarkerVisual",
    "OutlineStyle",
    "RegionVisual",
    "SceneDescription",
    "UnitDomain",
]

#: Colour for territory the observer has no knowledge of. Deliberately not black, which
#: reads as ocean.
UNKNOWN_FILL = "#2A2E35"
OCEAN_FILL = "#151A21"


@dataclass(frozen=True, slots=True)
class MapPalette:
    """Every colour the renderer chooses that does not come from a nation.

    A plain frozen record rather than a reference to the interface's theme: ``build_scene``
    must stay a pure function of its arguments with no import path back into Qt, and the
    map has to look right in both themes -- a light interface with a black ocean is not a
    light interface. The caller converts its theme into one of these at the boundary.
    """

    ocean: str = OCEAN_FILL
    unknown: str = UNKNOWN_FILL
    unclaimed: str = "#4A4F58"
    outline: str = "#0D1117"
    #: The coastline drawn around land nobody has scouted yet.
    #:
    #: **Undiscovered land was invisible, not merely dim.** `unknown` and `ocean` sit within
    #: 1.18:1 of one another and the ordinary outline is 1.02:1 against the sea, so a
    #: continent the player had not explored was indistinguishable from open water and a new
    #: world read as an empty map rather than an unlearned one. Separating the *fills* would
    #: mean re-spacing three tones that were designed together; a coastline says the one
    #: thing that was missing without moving any of them.
    #:
    #: It is the same colour for every unknown region, and that is the point: it says
    #: *there is land here* and nothing else. No owner, no name, no identity -- everything
    #: the fog is withholding stays withheld.
    outline_unknown: str = "#4E5A6B"
    outline_changed: str = "#F2C14E"
    outline_selected: str = "#FFFFFF"
    outline_contested: str = "#D9534F"
    #: The two ends of a measured layer's colour ramp -- development, unrest, air. A
    #: choropleth needs a scale that is legible in both themes, and reusing the outline
    #: colours would collide with the outlines drawn on top of it.
    ramp_cold: str = "#1F3A5F"
    ramp_hot: str = "#E8B44A"
    #: Marker for a nation under Focus Mode.
    tracked: str = "#F2C14E"
    #: Fallback for a city whose owner is unknown.
    marker_unowned: str = "#FFFFFF"
    marker_outline: str = "#0D1117"
    marker_text: str = "#FFFFFF"


DEFAULT_PALETTE = MapPalette()


class MapLayer(StrEnum):
    """What the map is coloured by.

    Layers change *emphasis*, never the underlying state. Every one is still a pure
    function of the same MapViewState -- switching layer rebuilds the description exactly
    like any other change, so a layer cannot desync from the world any more than
    ownership can.
    """

    #: Who owns what. The default.
    POLITICAL = "political"
    #: Which powers act on their own each turn; everyone else is dimmed.
    FOCUS = "focus"
    #: Wars, occupations and contested ground; the peaceful world is dimmed.
    FRONTS = "fronts"
    #: What the observer actually knows, with the rest left dark.
    FOG = "fog"
    #: What each province is economically worth. The one layer that reads a column the
    #: map already carried and never showed.
    DEVELOPMENT = "development"
    #: How hard held foreign ground is to hold, after the garrison sitting on it.
    UNREST = "unrest"
    #: Who owns the sky, and how decisively.
    AIR = "air"
    #: How far the observer's supply actually reaches, and where it runs out. Blank where
    #: it does not reach at all, which is the reading that matters: an army there fights at
    #: a fraction of its strength and grinds down where it stands.
    SUPPLY = "supply"
    #: What a warhead of yours could reach, and **what your services believe** would come
    #: back at you from it. Blank beyond your delivery reach, which is the reading that
    #: matters first: there is no decision to make about ground you cannot hit.
    NUCLEAR = "nuclear"
    #: How each power regards **you**, which is the one direction of a relationship you are
    #: entitled to know: a foreign ministry can tell whether it is being received warmly.
    #: What it cannot tell is what they think of anybody else, and this layer never says.
    ATTITUDE = "attitude"
    #: The ground your wars are actually about: what you have declared an aim over, what
    #: has been declared against you, and -- hottest -- the ground both sides have named,
    #: which is where a settlement will hinge. Blank everywhere nobody has claimed
    #: anything, which on most maps is most of the map.
    WAR_AIMS = "war_aims"
    #: **Who you actually trade with**, hottest where the most oil, steel and rare earths
    #: move between you and whoever holds the ground. Your own routes and nobody else's:
    #: a standing agreement to sell is a thing you signed, and what two other powers ship
    #: between themselves is not on your books. Blank where no route runs.
    TRADE = "trade"
    #: **What you have promised and what has been promised to you**, hottest for the
    #: agreements that would put your army in the field. Only agreements you are a party
    #: to, which is the honest scope: a government knows what it signed, and a pact between
    #: two other powers is intelligence rather than a fact on its map.
    OBLIGATIONS = "obligations"
    #: **Which held ground is on its way out.** Momentum toward independence, for every
    #: holder rather than only the observer -- a colony on the way out demonstrates, and
    #: `UNREST` already sets that precedent for exactly this kind of visible restiveness.
    #: Blank where nothing is stirring, which is most of a settled map.
    INDEPENDENCE = "independence"


class OutlineStyle(StrEnum):
    NORMAL = "normal"
    #: Ownership changed on the turn just resolved. Drawn emphatically for one turn.
    CHANGED = "changed"
    SELECTED = "selected"
    #: Occupied but not annexed -- controlled by someone other than the owner.
    CONTESTED = "contested"


class FillPattern(StrEnum):
    SOLID = "solid"
    #: Occupation: the owner's colour, hatched with the occupier's.
    HATCHED = "hatched"
    #: A siege in progress: the holder's colour, dotted with the attacker's. Not stripes,
    #: because "they hold it" and "they are fighting for it" are different news.
    SIEGE = "siege"
    NONE = "none"


class MarkerKind(StrEnum):
    CITY = "city"
    CAPITAL = "capital"
    UNIT = "unit"
    #: A nation under Focus Mode, marked so the player can see why some powers feel
    #: more alive than others.
    TRACKED = "tracked"
    #: A country's name, written on the map at its capital. Text only: no shape.
    NAME = "name"


class UnitDomain(StrEnum):
    """The three independently drawable military layers."""

    LAND = "land"
    SEA = "sea"
    AIR = "air"


@dataclass(frozen=True, slots=True)
class RegionVisual:
    """How one region should look. No geometry, only identity plus appearance."""

    region_id: str
    #: Where the region's label and markers sit.
    geo_key: str
    lod: int
    fill: str
    outline: str
    outline_width: float
    outline_style: OutlineStyle = OutlineStyle.NORMAL
    pattern: FillPattern = FillPattern.SOLID
    #: Secondary colour for hatching, when the region is occupied.
    pattern_colour: str | None = None
    z: int = 0
    label: str = ""
    tooltip: str = ""
    #: False when the observer has no knowledge of this region; it renders as fog.
    known: bool = True
    selectable: bool = True
    #: Every shape to draw for this region. Empty means "just ``geo_key``", which is what
    #: a country-level region is. A promoted province region groups several, and an
    #: applier that drew only the first left the rest of the region as ocean.
    geo_keys: tuple[str, ...] = ()
    #: How far an attacker has pushed here, as a share of the way to taking the ground.
    #: Zero when nobody is pressing.
    pressure: float = 0.0
    #: True when this visual is a whole nation's quiet ground merged into one shape
    #: rather than a region of the world. Not selectable: it is a drawing, not a place.
    dissolved: bool = False

    @property
    def shapes(self) -> tuple[str, ...]:
        return self.geo_keys or (self.geo_key,)


@dataclass(frozen=True, slots=True)
class MarkerVisual:
    """A city, capital or unit counter, already projected into scene coordinates."""

    marker_id: str
    kind: MarkerKind
    x: float
    y: float
    colour: str
    size: float
    z: int = 10
    label: str = ""
    tooltip: str = ""
    #: Present only for unit counters; determines the counter silhouette.
    domain: UnitDomain | None = None
    #: Screen-pixel displacement from the region centroid. Keeping this out of projected
    #: coordinates means counters stay grouped at every zoom without drifting by degrees.
    offset_x: float = 0.0
    offset_y: float = 0.0
    outline: str = "#0D1117"
    label_colour: str = "#FFFFFF"
    #: The province this marker stands in. **Carried so that a click on the marker does
    #: not need a second hit test to find out.** A counter is painted on top of the
    #: province it belongs to, so the scene's topmost item at the press is the counter and
    #: the region under it is never asked -- which is why clicking a unit counter selected
    #: nothing at all. Asking the scene again with the markers hidden would be a second
    #: full hit test per click; the answer is already known here, at the point the marker
    #: was built from a row that had the region id in it.
    region_id: str = ""
    #: Whether this is the formation the player has selected. Presentation only.
    selected: bool = False
    #: The research mark of the formations on a unit counter: 1 for the standard, 2 and 3
    #: once the owner's upgrade lines have improved them.
    mark: int = 1


@dataclass(frozen=True, slots=True)
class SceneDescription:
    """A complete, self-contained description of one frame of the map."""

    pack_id: str
    projection_key: str
    lod: int
    turn: int
    #: Which layer this frame was built for. Part of the identity of the scene, so two
    #: layers of the same world never compare equal.
    layer: MapLayer = MapLayer.POLITICAL
    #: Which unit overlays were requested for this frame. Part of scene identity even
    #: when the world currently has no units in one of the active domains.
    unit_domains: tuple[UnitDomain, ...] = tuple(UnitDomain)
    regions: tuple[RegionVisual, ...] = ()
    markers: tuple[MarkerVisual, ...] = ()
    observer_nation_id: str | None = None
    background: str = OCEAN_FILL

    @property
    def region_ids(self) -> frozenset[str]:
        return frozenset(region.region_id for region in self.regions)

    def region(self, region_id: str) -> RegionVisual | None:
        for candidate in self.regions:
            if candidate.region_id == region_id:
                return candidate
        return None
