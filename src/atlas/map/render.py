"""``build_scene`` -- the pure heart of the map.

This module must never import PySide6. A test enforces that in a subprocess, because the
guarantee is not "we intend to keep Qt out" but "Qt is provably absent".

**The purity contract.** ``build_scene`` is a function of its arguments and nothing else.
Same state in, byte-identical scene out. It reads no globals, caches nothing, consults no
clock, and holds no reference to the previous frame. After a turn resolves, the scene is
re-derived from scratch rather than patched.

That last point is the whole design. An incrementally patched scene graph is a second
copy of the world's ownership data, maintained by different code from the one that owns
the real copy, and the two drift. The drift is silent, it accumulates, and it shows up as
a map that disagrees with the simulation -- which is precisely the failure that killed
the previous attempt at this program. Re-deriving is cheap here because a scene
description holds no coordinates: a few hundred small frozen records, built in under a
millisecond. The expensive part -- decoding and projecting polygons -- is keyed on
immutable inputs and cached by the applier, where it cannot encode anything about
ownership, turn, or observer.
"""

from __future__ import annotations

from math import sqrt

from ..humanize import strength as say_strength
from .projection import Projection
from .query import MapViewState
from .scene_model import (
    DEFAULT_PALETTE,
    FillPattern,
    MapLayer,
    MapPalette,
    MarkerKind,
    MarkerVisual,
    OutlineStyle,
    RegionVisual,
    SceneDescription,
    UnitDomain,
)

__all__ = ["GeoIndexEntry", "build_scene", "choose_lod"]

#: Minimal geometry metadata the renderer needs. Supplied by the caller from the pack, so
#: this module never performs I/O.
GeoIndexEntry = tuple[float, float]  # label point (lon, lat)


def choose_lod(zoom: float, lod_count: int) -> int:
    """Pick a level of detail from the view's zoom factor.

    Chosen here rather than in the view so that a scene description is fully determined
    by its inputs -- including which geometry the applier will resolve.
    """
    if lod_count <= 1:
        return 0
    if zoom >= 4.0:
        return 0
    if zoom >= 1.25:
        return min(1, lod_count - 1)
    return min(2, lod_count - 1)


def should_dissolve(zoom: float) -> bool:
    """Whether the map is far enough out that provinces should read as countries.

    Promotion made Europe unreadable at continental zoom: a dozen provinces per power,
    every border drawn, and a mesh of hairlines where a political map should be. Below
    this the question a player is asking is "who holds what", and the answer is a
    country.
    """
    return zoom < DISSOLVE_BELOW


#: Below this zoom provinces merge into countries. It was 4.0, above the zoom the game
#: opens at (3.0), so a player never saw a province unless they went looking -- and asked
#: whether the map had any. At 2.0 the opening view shows them; a whole continent still
#: reads as countries.
DISSOLVE_BELOW = 2.0


def _tooltip(view: MapViewState, region, owner_name: str | None) -> str:
    parts = [region.name]
    parts.append(f"Owner: {owner_name}" if owner_name else "Unclaimed")
    if region.controller_nation_id and region.controller_nation_id != region.owner_nation_id:
        occupier = view.nations.get(region.controller_nation_id)
        parts.append(f"Occupied by: {occupier.name if occupier else region.controller_nation_id}")
    siege = view.contested.get(region.region_id)
    if siege:
        pressing = view.nations.get(siege[0])
        parts.append(
            f"Under attack by {pressing.name if pressing else siege[0]}: "
            f"{siege[1]:.0%} of the way to falling"
        )
    if view.war_aims:
        parts.extend(_war_aim_lines(view, region))
    if view.attitude:
        line = _attitude_line(view, region)
        if line:
            parts.append(line)
    for line in (_trade_line(view, region), _obligation_line(view, region),
                 _independence_line(view, region)):
        if line:
            parts.append(line)
    # Only for an observer that actually has something to throw. `None` is *everywhere*
    # and is the intercontinental case, so it cannot be tested for truthiness.
    if view.nuclear_reach is None or view.nuclear_reach:
        parts.append(_nuclear_line(view, region))
    parts.append(f"Terrain: {region.terrain}")
    if region.population:
        parts.append(f"Population: {region.population:,.1f}")
    return "\n".join(parts)


def _war_aim_lines(view: MapViewState, region) -> list[str]:
    """Every declared aim that lands on this ground, said from the observer's seat.

    *Yours* and *theirs* rather than two names, because which side of a demand a government
    is on is the first thing it needs to know and the last thing it should have to work out
    from a pair of nation ids.
    """
    claims = view.war_aims.get(region.region_id)
    if not claims:
        return []
    lines = []
    for claimant, kind, enemy in claims:
        mine = claimant == view.observer_nation_id
        who = view.nations.get(claimant)
        against = view.nations.get(enemy)
        said = kind.replace("_", " ")
        lines.append(
            f"Your war aim here: {said}, against "
            f"{against.name if against else enemy}"
            if mine else
            f"{who.name if who else claimant} claims this: {said}")
    return lines


def _holder_of(view: MapViewState, region) -> str:
    return region.controller_nation_id or region.owner_nation_id or ""


def _trade_line(view: MapViewState, region) -> str:
    """What moves between the observer and whoever holds this ground, and which way."""
    holder = _holder_of(view, region)
    moving = view.trade.get(holder) if holder != view.observer_nation_id else None
    if not moving:
        return ""
    volume, goods, inbound = moving
    who = view.nations.get(holder)
    named = ", ".join(goods)
    return (f"{'Ships you' if inbound else 'Buys from you'} {named}: "
            f"{volume:,.0f} with {who.name if who else holder}")


def _obligation_line(view: MapViewState, region) -> str:
    """What the two of you have signed. Kinds in the words the schema uses, so a reader
    can find the agreement itself rather than a paraphrase of it."""
    holder = _holder_of(view, region)
    promised = view.obligations.get(holder) if holder != view.observer_nation_id else None
    if not promised:
        return ""
    kinds, _weight = promised
    return "Signed with you: " + ", ".join(kind.replace("_", " ") for kind in kinds)


def _independence_line(view: MapViewState, region) -> str:
    """How close this province is to leaving, in the words the layer bands it by."""
    stirring = view.independence.get(region.region_id)
    if not stirring:
        return ""
    return f"Independence movement: {independence_word(stirring)} ({stirring:.0f})"


#: What a momentum means, by upper bound. Words for the reason every other band on this map
#: has them: *stirring* is what a briefing says, and 41.2 is not. The two upper bounds are
#: `decolonisation.MOVEMENT_THRESHOLD` and `INDEPENDENCE_THRESHOLD` -- read from there
#: rather than restated, so the map and the engine cannot come to disagree about when a
#: colony is ripe.
def independence_word(momentum: float) -> str:
    from ..engine.decolonisation import INDEPENDENCE_THRESHOLD, MOVEMENT_THRESHOLD

    if momentum >= INDEPENDENCE_THRESHOLD:
        return "ripe"
    if momentum >= MOVEMENT_THRESHOLD:
        return "organised"
    return "stirring"


#: What a score means, by upper bound. Words rather than a number alone for the reason the
#: intelligence bands are named: *hostile* is what a briefing says, and -63 is not.
ATTITUDES: tuple[tuple[float, str], ...] = (
    (-60.0, "hostile"),
    (-25.0, "cold"),
    (25.0, "indifferent"),
    (60.0, "friendly"),
    (101.0, "warm"),
)


def attitude_word(score: float) -> str:
    return next(word for bound, word in ATTITUDES if score < bound)


def _attitude_line(view: MapViewState, region) -> str:
    """How whoever holds this ground regards the observer, in a sentence.

    Empty for the observer's own ground and for anybody it has no relationship with, rather
    than a placeholder: a tooltip line that said *indifferent* about your own provinces
    would be noise on every hover.
    """
    holder = region.controller_nation_id or region.owner_nation_id or ""
    if not holder or holder == view.observer_nation_id:
        return ""
    standing = view.attitude.get(holder)
    if standing is None:
        return ""
    score, treaty, at_war = standing
    them = view.nations.get(holder)
    said = (f"{them.name if them else holder} regards you: "
            f"{attitude_word(score)} ({score:+.0f})")
    if at_war:
        return said + " — at war with you"
    return said + ("" if treaty == "none" else f" — {treaty.replace('_', ' ')}")


def _nuclear_line(view: MapViewState, region) -> str:
    """One sentence about striking this ground, in estimates and never in figures.

    The band and the confidence are both quoted, because a range without a confidence is a
    number a staff will average and a confidence without a range says nothing. A fed count
    reads exactly like an earned one here, which is the mechanic rather than an oversight.
    """
    if view.nuclear_reach is not None and region.region_id not in view.nuclear_reach:
        return "Beyond the reach of your warheads"
    holder = region.controller_nation_id or region.owner_nation_id or ""
    band = view.nuclear_answer.get(holder)
    if band is None:
        return "In reach; nobody holding this could answer in kind"
    low, high, confidence = band
    return (f"In reach; your services believe {low:.0f}-{high:.0f} warhead(s) "
            f"could answer ({confidence})")


def _on_screen(
    shapes: tuple[str, ...],
    viewport: tuple[float, float, float, float],
    bounds: dict[str, tuple[float, float, float, float]],
    projection: Projection,
) -> bool:
    """Whether any of a visual's shapes could put a pixel inside the viewport.

    A shape with no known bounds is kept. Drawing something the player cannot see costs a
    frame; dropping something they can costs the map its one promise.
    """
    left, top, right, bottom = viewport
    for shape in shapes:
        box = bounds.get(shape)
        if box is None:
            return True
        # The pack stores lon/lat; the viewport is in projected units, and every
        # projection here is monotone in each axis, so projecting the two corners bounds
        # the shape.
        x0, y0 = projection.project(box[0], box[1])
        x1, y1 = projection.project(box[2], box[3])
        if (
            max(x0, x1) >= left
            and min(x0, x1) <= right
            and max(y0, y1) >= top
            and min(y0, y1) <= bottom
        ):
            return True
    return False


def _muted(colour: str, toward: str, amount: float = 0.72) -> str:
    """Pull a colour toward the ocean, for regions a layer wants to de-emphasise.

    Computed here rather than by lowering item opacity: opacity is a property of the Qt
    item, which would put appearance back into the mutable scene graph. A layer must be
    able to change what a region *looks like* without the applier deciding anything.
    """
    try:
        value = colour.lstrip("#")
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return colour
    base = toward.lstrip("#")
    br, bg, bb = (int(base[i : i + 2], 16) for i in (0, 2, 4))
    mix = lambda c, d: int(c + (d - c) * amount)  # noqa: E731
    return f"#{mix(r, br):02X}{mix(g, bg):02X}{mix(b, bb):02X}"


#: The layers that colour by a *measured value* rather than by who owns the ground.
#: Ownership is still the outline and the hatching, so a choropleth never hides the
#: political map underneath -- it repaints the fill and leaves the borders saying who is
#: where.
MEASURED_LAYERS = (MapLayer.DEVELOPMENT, MapLayer.UNREST, MapLayer.AIR, MapLayer.SUPPLY,
                   MapLayer.NUCLEAR, MapLayer.ATTITUDE, MapLayer.WAR_AIMS,
                   MapLayer.TRADE, MapLayer.OBLIGATIONS, MapLayer.INDEPENDENCE)


#: The one colour a calm map gives every power at war with the player, whatever its own.
ENEMY_FILL = "#B5473F"
#: And every power bound to the player by an alliance or a defensive pact. One shared hue
#: for the same reason: an ally whose own colour happened to be red read as an enemy.
ALLY_FILL = "#3E8A8F"


def _calm_fill(view: MapViewState, nation_id: str, colour: str, palette: MapPalette) -> str:
    """The political map read for a player: you in full, allies teal, enemies red, the rest
    quiet.

    **Two hundred saturated colours say nothing.** Every power in its own bright flag colour
    made the one fact a player looks for -- where am I, and who is fighting me -- the hardest
    to find on the screen. So the player's own ground keeps its colour, every power at war
    with the player shares one red, allies are softened a little and everybody else a lot.
    The colours are still each power's own, so neighbours stay told apart.
    """
    if nation_id == view.observer_nation_id:
        return colour
    feeling = view.attitude.get(nation_id)
    if feeling is not None and feeling[2]:
        return _muted(ENEMY_FILL, palette.ocean, 0.12)
    if feeling is not None and feeling[1] in ("alliance", "defensive"):
        return _muted(ALLY_FILL, palette.ocean, 0.12)
    # Toward the theme's own neutral land, which takes the saturation out: a hint of hue to
    # tell neighbours apart by, and no more.
    return _muted(colour, palette.unclaimed, 0.8)


def _counter_fill(view: MapViewState, nation_id: str, colour: str,
                  palette: MapPalette) -> str:
    """A unit counter in the calm map's colours: yours, an ally's, an enemy's, or anyone's.

    A counter in its owner's flag colour sat on a map that had stopped using flag colours,
    so a Russian army read as *brown* on ground that read as *enemy*. The same rule as the
    ground, so a counter and the country it belongs to say the same thing; a neutral power's
    counter keeps a little more of its colour than its ground does, so it stays a counter.
    """
    if nation_id == view.observer_nation_id:
        return colour
    feeling = view.attitude.get(nation_id)
    if feeling is not None and feeling[2]:
        return ENEMY_FILL
    if feeling is not None and feeling[1] in ("alliance", "defensive"):
        return ALLY_FILL
    return _muted(colour, palette.unclaimed, 0.55)


#: Below this much ground (square degrees of bounding box) a country is named only if it
#: matters to the player: Andorra, Malta and the Comoros are real, and at continent zoom
#: their names are clutter on top of their neighbours'.
SMALL_COUNTRY = 2.0


def _country_names(view: MapViewState, place, palette: MapPalette, me: str | None,
                   enemies: set[str],
                   bounds: dict[str, tuple[float, float, float, float]]) -> list[MarkerVisual]:
    """One name per country on screen, at its capital, most relevant first.

    **Nothing on the map said which country was which.** Every region visual carried a
    label and nothing drew it; at the zoom the game opens on, a player had to hover to tell
    Syria from Iraq. The marker ids sort in priority order -- you, your enemies, your
    allies, then the countries with the most ground -- because the view's declutter walks
    them in id order and drops a name that would land on one already placed: the names a
    player needs survive a crowded continent.
    """
    held: dict[str, list[str]] = {}
    size: dict[str, float] = {}
    for region in view.regions:
        owner = region.owner_nation_id
        if owner and region.kind != "ocean":
            held.setdefault(owner, []).append(region.region_id)
            for shape in region.shapes:
                box = bounds.get(shape)
                if box is not None:
                    size[owner] = size.get(owner, 0.0) + abs(
                        (box[2] - box[0]) * (box[3] - box[1]))
    out: list[MarkerVisual] = []
    for nation_id, regions in held.items():
        nation = view.nations.get(nation_id)
        if nation is None:
            continue
        anchor = (nation.capital_region_id if nation.capital_region_id in regions
                  else sorted(regions)[0])
        position = place(anchor)
        if position is None:
            continue
        feeling = view.attitude.get(nation_id)
        rank = (0 if nation_id == me else 1 if nation_id in enemies
                else 2 if feeling is not None and feeling[1] in ("alliance", "defensive")
                else 3)
        ground = size.get(nation_id, 0.0)
        if rank == 3 and bounds and ground < SMALL_COUNTRY:
            continue
        # Bigger countries first within a rank: they have the room, and they are the ones
        # a reader orients by.
        weight = 99999 - min(int(ground * 10), 99999)
        # Your own name sits on your own colour, which may be a light one (Ukraine's
        # yellow): dark ink with a light edge there, light ink with a dark edge elsewhere.
        dark = rank == 0 and _luminance(nation.colour) > 0.5
        out.append(MarkerVisual(
            marker_id=f"name:{rank}:{weight:05d}:{nation_id}",
            kind=MarkerKind.NAME,
            x=position[0],
            y=position[1],
            colour="#00000000",
            size=0.0,
            z=50,
            label=nation.name,
            tooltip="",
            label_colour="#10161F" if dark else palette.marker_text,
            outline="#00000000" if dark else "#96000000",
            region_id=anchor,
        ))
    return out


def _luminance(colour: str) -> float:
    """Perceived lightness of ``#RRGGBB``, 0 (black) to 1 (white)."""
    try:
        value = colour.lstrip("#")[-6:]
        r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 0.0
    return 0.299 * r + 0.587 * g + 0.114 * b


def _ramp(cold: str, hot: str, share: float) -> str:
    """A colour ``share`` of the way from cold to hot, clamped.

    `_muted` is already a two-colour mix with the fraction as its third argument, so the
    ramp is that function read the other way round rather than a second copy of the
    arithmetic.
    """
    return _muted(cold, hot, max(0.0, min(1.0, share)))


def _answering(view: MapViewState, region) -> float:
    """What the observer *believes* would answer a strike on this ground: the midpoint of
    its estimate of whoever holds it. Zero for ground held by somebody with no warheads,
    and zero for the observer's own -- which are different facts that happen to share a
    number, and both of them mean *nothing comes back*."""
    holder = region.controller_nation_id or region.owner_nation_id or ""
    band = view.nuclear_answer.get(holder)
    return (band[0] + band[1]) / 2.0 if band else 0.0


def _measured(view: MapViewState, region, layer: MapLayer) -> float | None:
    """What this layer is measuring here, normalised to 0..1, or None for 'no reading'.

    None and zero are deliberately different. A province with no unrest is *quiet* and
    should read as the floor of the scale; a province nobody has ever flown over has **no
    reading at all** and should read as blank, because colouring it as "nobody holds the
    sky" would claim a fact the world does not have.
    """
    if layer is MapLayer.DEVELOPMENT:
        # Scaled against the richest province on the map rather than a constant, so the
        # ramp still spreads across the whole scale on a poor map or an early era.
        top = max((r.development for r in view.regions), default=0.0)
        return (region.development / top) if top > 0 else 0.0
    if layer is MapLayer.UNREST:
        worst = max(view.unrest.values(), default=0.0)
        residual = view.unrest.get(region.region_id, 0.0)
        if worst <= 0:
            return None
        return residual / worst
    if layer is MapLayer.AIR:
        held = view.air_control.get(region.region_id)
        return held[1] if held else None
    if layer is MapLayer.WAR_AIMS:
        # **Ground nobody has claimed has no reading**, which is what makes this a map of
        # the war rather than a map of the world: on a planet at war, most provinces are
        # not what anybody is fighting for, and colouring them the floor of the scale would
        # bury the handful that are.
        #
        # Hottest where several aims land on the same province -- both sides wanting the
        # same ground is the ground a settlement will hinge on, and the one thing on this
        # layer worth picking out at a glance.
        claims = view.war_aims.get(region.region_id)
        if not claims:
            return None
        most = max(len(found) for found in view.war_aims.values())
        return 1.0 if most <= 1 else len(claims) / most
    if layer is MapLayer.ATTITUDE:
        # **Hot is warm**, which is the one place in this module where the ramp's name and
        # its meaning line up by accident rather than by convention -- and it is still the
        # right way round, because every other measured layer reads "more is hotter" and
        # regard is a quantity that goes up.
        #
        # Your own ground has no reading. A government does not have an opinion of itself,
        # and painting it at either end of the scale would be a claim rather than a blank.
        holder = region.controller_nation_id or region.owner_nation_id or ""
        if not holder or holder == view.observer_nation_id:
            return None
        standing = view.attitude.get(holder)
        if standing is None:
            return None
        return max(0.0, min(1.0, (standing[0] + 100.0) / 200.0))
    if layer is MapLayer.NUCLEAR:
        # **Out of reach is no reading at all**, for the reason the docstring gives and
        # more sharply here than anywhere else: there is no decision to be made about
        # ground you cannot hit, and colouring it "no risk" would read as an invitation.
        if view.nuclear_reach is not None and region.region_id not in view.nuclear_reach:
            return None
        answer = _answering(view, region)
        worst = max((high + low) / 2.0
                    for low, high, _c in view.nuclear_answer.values()) if (
            view.nuclear_answer) else 0.0
        if worst <= 0:
            return 0.0
        return min(1.0, answer / worst)
    if layer is MapLayer.TRADE:
        # **Ground you trade nothing with has no reading**, for the reason the war-aims
        # layer blanks unclaimed ground: on a map where most powers ship you nothing, a
        # floor-of-the-scale colour on all of them buries the handful that matter. Your own
        # ground is blank too -- a country does not run a trade route with itself.
        holder = region.controller_nation_id or region.owner_nation_id or ""
        if not holder or holder == view.observer_nation_id:
            return None
        moving = view.trade.get(holder)
        if not moving:
            return None
        most = max(volume for volume, _goods, _in in view.trade.values())
        return 1.0 if most <= 0 else min(1.0, moving[0] / most)
    if layer is MapLayer.OBLIGATIONS:
        # Hot is *most obliged*, not most friendly: the scale is what it would cost to
        # honour. See `query.OBLIGATION_WEIGHT`.
        holder = region.controller_nation_id or region.owner_nation_id or ""
        if not holder or holder == view.observer_nation_id:
            return None
        promised = view.obligations.get(holder)
        return promised[1] if promised else None
    if layer is MapLayer.INDEPENDENCE:
        # Scaled against the most restive province on the map rather than the threshold,
        # so an empire where nothing is close to leaving still shows which of it is
        # closest -- which is the whole use of the layer before anything is ripe.
        stirring = view.independence.get(region.region_id)
        if not stirring:
            return None
        worst = max(view.independence.values(), default=0.0)
        return 1.0 if worst <= 0 else min(1.0, stirring / worst)
    if layer is MapLayer.SUPPLY:
        # **Distance inverted, so the hot end is where supply is strongest.** Every other
        # measured layer reads "more is hotter" and a raw distance would read backwards --
        # the depot, which is the strongest place on the map, would be the coldest colour
        # on it. Out of reach is `None` rather than zero, for the reason the docstring
        # gives: no reading at all is not the same as a reading of nothing.
        reach = view.supply.get(region.region_id)
        if reach is None:
            return None
        furthest = max(view.supply.values(), default=0)
        return 1.0 if furthest <= 0 else 1.0 - (reach / furthest)
    return None


def build_scene(
    view: MapViewState,
    *,
    projection: Projection,
    pack_id: str,
    lod: int = 0,
    label_points: dict[str, GeoIndexEntry] | None = None,
    selected_region_id: str | None = None,
    # The unit counter the player has picked, by marker_id. Drawn with the selection ink
    # and a heavier outline, in the same language the selected province is drawn in -- a
    # formation that has been selected has to look selected or the click reads as a miss.
    selected_marker_id: str | None = None,
    layer: MapLayer = MapLayer.POLITICAL,
    unit_domains: frozenset[UnitDomain] | set[UnitDomain] | tuple[UnitDomain, ...] = frozenset(UnitDomain),
    palette: MapPalette = DEFAULT_PALETTE,
    dissolve: bool = False,
    viewport: tuple[float, float, float, float] | None = None,
    bounds: dict[str, tuple[float, float, float, float]] | None = None,
    calm: bool = False,
) -> SceneDescription:
    """Derive a complete scene description from world state. Pure.

    ``label_points`` maps ``geo_key`` to a lon/lat guaranteed inside the shape, taken
    from the baked pack. Passing it in rather than reading the pack here keeps this
    function free of I/O and therefore trivially testable.

    ``dissolve`` merges each power's quiet ground into a single shape, so a map far
    enough out to show a continent shows countries rather than a mesh of province
    hairlines. It changes how many items carry an answer, never the answer: every region
    is still decided one at a time, immediately above, and contested or newly-changed
    ground is deliberately left out of the merge so that the war is visible at any zoom.

    ``calm`` is the political map read for a player rather than the referee: their own
    country in full colour and outlined, enemies one red, everyone else muted (see
    `_calm_fill`); and, zoomed out, only their own and their enemies' forces on it. Only the
    political layer is affected -- every other layer is already a reading of its own.

    ``viewport`` drops everything outside the rectangle currently on screen, given as
    ``(x0, y0, x1, y1)`` in projected units, using the lon/lat ``bounds`` the pack keeps
    per shape. Culling happens *after* the merge above, so a dissolved country is kept or
    dropped whole and its outline never changes with the scroll position. Deriving a
    scene is a fraction of a millisecond; resolving geometry for four thousand provinces
    the player cannot see is most of a second.
    """
    active_domains = frozenset(UnitDomain(domain) for domain in unit_domains)
    labels = label_points or {}
    regions: list[RegionVisual] = []
    #: Quiet ground, gathered per holder while the loop runs and emitted as one shape
    #: each afterwards. Nothing is decided here that is not decided for every region --
    #: dissolving only changes how many items carry the same answer.
    quiet: dict[str, list] = {}

    calm = calm and layer is MapLayer.POLITICAL
    me = view.observer_nation_id
    enemies = {nation for nation, (_score, _treaty, at_war) in view.attitude.items() if at_war}

    def edge(colour: str) -> str:
        # The player's own border: their colour lifted toward white, so it reads on both
        # themes without borrowing the selection or "changed hands" inks.
        return _muted(colour, "#FFFFFF", 0.55)

    for region in view.regions:
        known = region.region_id in view.known_region_ids
        owner = view.nations.get(region.owner_nation_id or "")
        # **Borders are public; armies are not.** Who owns a province is on every atlas, so
        # unscouted land keeps its owner's colour (muted) and its name. What fog hides --
        # forces, sieges, occupation -- stays hidden. The Fog layer still shows the strict
        # view: nothing but coastline beyond what the player has seen.
        charted = (not known and region.kind != "ocean" and owner is not None
                   and layer is not MapLayer.FOG)
        controller = view.nations.get(region.controller_nation_id or "")
        occupied = (
            controller is not None
            and region.controller_nation_id != region.owner_nation_id
        )

        if region.kind == "ocean":
            fill = palette.ocean
            selectable = False
        elif charted:
            fill = (_calm_fill(view, owner.nation_id, owner.colour, palette) if calm
                    else _muted(owner.colour, palette.ocean, 0.35))
            selectable = True
        elif not known:
            fill = palette.unknown
            selectable = True
        elif owner is not None:
            fill = (_calm_fill(view, owner.nation_id, owner.colour, palette) if calm
                    else owner.colour)
            selectable = True
        else:
            fill = palette.unclaimed
            selectable = True

        # Layers change emphasis only. Every one is still a pure function of the same
        # state, so switching layer is an ordinary re-derive rather than a special path.
        if known and region.kind != "ocean":
            if layer is MapLayer.FOCUS and not (owner and owner.is_tracked) or layer is MapLayer.FRONTS and not occupied and region.region_id not in view.changed_region_ids:
                fill = _muted(fill, palette.ocean)
            elif layer is MapLayer.FOG:
                # Fog mode says only what the observer knows, ownership included.
                fill = owner.colour if owner else palette.unclaimed
            elif layer in MEASURED_LAYERS:
                reading = _measured(view, region, layer)
                if reading is None:
                    # No reading is not a reading of zero. Ground nobody has flown over
                    # is left blank rather than coloured "uncontested".
                    fill = _muted(fill, palette.ocean)
                elif layer is MapLayer.AIR:
                    # The sky is coloured by *whose* it is, deepened by how decisively --
                    # the one measured layer where the interesting fact is an owner.
                    holder = view.nations.get(view.air_control[region.region_id][0])
                    fill = _ramp(_muted(fill, palette.ocean),
                                 holder.colour if holder else palette.ramp_hot, reading)
                else:
                    fill = _ramp(palette.ramp_cold, palette.ramp_hot, reading)

        siege = view.contested.get(region.region_id) if known else None
        pressure = siege[1] if siege else 0.0
        besieger = view.nations.get(siege[0]) if siege else None

        if region.region_id == selected_region_id:
            style = OutlineStyle.SELECTED
            outline, width = palette.outline_selected, 2.5
        elif known and region.region_id in view.changed_region_ids:
            style = OutlineStyle.CHANGED
            outline, width = palette.outline_changed, 2.0
        elif known and (occupied or siege):
            style = OutlineStyle.CONTESTED
            # A front that is nearly through is drawn as one. The width carries the
            # pressure the contest table is accumulating, so a player can see where the
            # line is about to break without opening anything.
            outline, width = palette.outline_contested, 1.5 + 2.0 * pressure
        elif not known and not charted and region.kind != "ocean":
            # **A coastline, and nothing more.** Land the player has not scouted is drawn at
            # 1.18:1 against the sea and its ordinary outline at 1.02:1 -- so an unexplored
            # continent read as open water. One line at the land/sea boundary says *there is
            # ground here*, which is what a map is for, while the fill stays exactly as fog
            # of war left it.
            #
            # The same colour for every unknown region, deliberately: it carries no owner,
            # no name and no identity, so a player learns that the world is not empty and
            # not one thing more than that. `OutlineStyle.NORMAL` is kept rather than a new
            # style, because this is the ordinary border drawn legibly rather than a border
            # that means something new.
            style = OutlineStyle.NORMAL
            outline, width = palette.outline_unknown, 0.8
        else:
            style = OutlineStyle.NORMAL
            outline, width = palette.outline, 0.5

        # Occupation hatches with whoever is standing on the ground; a siege in progress
        # hatches with whoever is pushing. Both say "this is not simply held".
        if known and occupied:
            pattern, pattern_colour = FillPattern.HATCHED, controller.colour
        elif known and besieger is not None:
            pattern, pattern_colour = FillPattern.HATCHED, besieger.colour
        else:
            pattern, pattern_colour = FillPattern.SOLID, None

        visual = RegionVisual(
            region_id=region.region_id,
            geo_key=region.geo_key,
            lod=lod,
            fill=fill,
            outline=outline,
            outline_width=width,
            outline_style=style,
            pattern=pattern,
            pattern_colour=pattern_colour,
            z=0 if region.kind == "ocean" else 1,
            label=region.name if known or charted else "",
            tooltip=_tooltip(view, region, owner.name if owner else None)
            if known
            else f"{region.name}\nOwner: {owner.name}\nNo reliable information about forces here."
            if charted
            else f"{region.name}\nNo reliable information.",
            known=known,
            selectable=selectable,
            geo_keys=region.shapes,
            pressure=pressure,
        )

        # Zoomed out, quiet ground reads as a country and contested ground stays a place.
        # Ownership that changed this turn stays a place too: it is the one thing on the
        # map a player must not have to zoom in to notice.
        interesting = (
            style is not OutlineStyle.NORMAL
            or pattern is not FillPattern.SOLID
            or region.kind == "ocean"
        )
        # **Your own ground and your enemies' are never merged.** That is where the war
        # is, and a front is a line between provinces: merged into one shape each, two
        # countries at war read as two blocks of colour with nothing between them.
        frontline = calm and region.owner_nation_id in ({me} | enemies)
        if (dissolve and (known or charted) and not interesting and not frontline
                and region.owner_nation_id):
            quiet.setdefault((region.owner_nation_id, known), []).append(visual)
        else:
            regions.append(visual)

    for (nation_id, seen), held in sorted(quiet.items()):
        nation = view.nations[nation_id]
        regions.append(
            RegionVisual(
                # Not a region id: this is a drawing of a nation, and nothing in the world
                # answers to it. Prefixed so it can never collide with one either.
                region_id=f"whole:{nation_id}" if seen else f"whole:{nation_id}:charted",
                geo_key=held[0].geo_key,
                lod=lod,
                fill=held[0].fill,
                outline=edge(nation.colour) if calm and nation_id == me else palette.outline,
                outline_width=1.8 if calm and nation_id == me else 0.5,
                z=1,
                # One name per country: on the part the player has seen, if any.
                label=nation.name if seen or (nation_id, True) not in quiet else "",
                tooltip=f"{nation.name}\n{len(held)} regions",
                geo_keys=tuple(key for visual in held for key in visual.shapes),
                selectable=False,
                dissolved=True,
            )
        )

    if viewport is not None:
        regions = [
            visual
            for visual in regions
            if _on_screen(visual.shapes, viewport, bounds or {}, projection)
        ]

    markers: list[MarkerVisual] = []
    geo_by_region = {region.region_id: region.geo_key for region in view.regions}

    def place(region_id: str) -> tuple[float, float] | None:
        geo_key = geo_by_region.get(region_id)
        if geo_key is None:
            return None
        point = labels.get(geo_key)
        if point is None:
            return None
        position = projection.project(point[0], point[1])
        if viewport is not None:
            left, top, right, bottom = viewport
            if not (left <= position[0] <= right and top <= position[1] <= bottom):
                return None
        return position

    for city in view.cities:
        if city.region_id not in view.known_region_ids:
            continue
        position = place(city.region_id)
        if position is None:
            continue
        region = view.region(city.region_id)
        owner = view.nations.get((region.owner_nation_id or "") if region else "")
        markers.append(
            MarkerVisual(
                marker_id=f"city:{city.city_id}",
                kind=MarkerKind.CAPITAL if city.is_capital else MarkerKind.CITY,
                x=position[0],
                y=position[1],
                colour=owner.colour if owner else palette.marker_unowned,
                # Screen pixels, not projection units -- the marker item ignores the view
                # transform so it stays legible at every zoom.
                size=5.0 + city.tier * 1.2,
                z=20 if city.is_capital else 15,
                label=city.name,
                tooltip=f"{city.name} (tier {city.tier})",
                outline=palette.marker_outline,
                label_colour=palette.marker_text,
                region_id=city.region_id,
            )
        )

    # Tracked nations get a marker at their capital, so the player can see *why* some
    # powers behave more distinctly than others rather than wondering at it.
    for nation in sorted(view.nations.values(), key=lambda n: n.nation_id):
        if calm or not nation.is_tracked or not nation.capital_region_id:
            continue  # which powers the engine follows closely is its business, not the map's
        if nation.capital_region_id not in view.known_region_ids:
            continue
        position = place(nation.capital_region_id)
        if position is None:
            continue
        markers.append(
            MarkerVisual(
                marker_id=f"tracked:{nation.nation_id}",
                kind=MarkerKind.TRACKED,
                x=position[0],
                y=position[1] - 2.0,
                colour=palette.tracked,
                size=9.0,
                z=40,
                label="",
                tooltip=f"{nation.name} is under Focus Mode: it acts on its own each turn",
                outline=palette.marker_outline,
                label_colour=palette.marker_text,
                region_id=nation.capital_region_id,
            )
        )

    # Units are aggregated per region and domain: one counter for all ground forces, one
    # for fleets and one for aircraft. The owner breakdown is retained for the tooltip
    # and the strongest contributor supplies the counter colour, so aggregation does not
    # erase a coalition standing in the same place.
    #
    # **And the composition is retained for the same reason the owners are.** "Land forces:
    # 7 units, strength 44" is a number a player cannot plan against: seven infantry
    # divisions and seven armoured ones are the same counter and a completely different
    # problem, and the difference is already in the row -- it was simply being summed away.
    stacks: dict[tuple[str, UnitDomain], dict[str, object]] = {}
    for unit in view.units:
        if unit.region_id not in view.known_region_ids:
            continue
        domain = UnitDomain(unit.domain)
        if domain not in active_domains:
            continue
        key = (unit.region_id, domain)
        stack = stacks.setdefault(
            key, {"count": 0, "strength": 0.0, "owners": {}, "kinds": {}})
        stack["count"] = int(stack["count"]) + 1
        stack["strength"] = float(stack["strength"]) + unit.strength
        owners = stack["owners"]
        assert isinstance(owners, dict)
        owners[unit.nation_id] = float(owners.get(unit.nation_id, 0.0)) + unit.strength
        kinds = stack["kinds"]
        assert isinstance(kinds, dict)
        kinds[unit.kind] = int(kinds.get(unit.kind, 0)) + 1

    offsets = {
        UnitDomain.LAND: (-18.0, 13.0),
        UnitDomain.SEA: (0.0, 18.0),
        UnitDomain.AIR: (18.0, 13.0),
    }
    names = {
        UnitDomain.LAND: "Land forces",
        UnitDomain.SEA: "Naval forces",
        UnitDomain.AIR: "Air forces",
    }
    for (region_id, domain), stack in sorted(stacks.items()):
        position = place(region_id)
        if position is None:
            continue
        if calm and dissolve and not ({me} | enemies) & set(stack["owners"]):
            # Zoomed out, a counter for every garrison on the continent is noise: yours and
            # your enemies' are the ones a player plans around. Zoom in to see the rest.
            continue
        count = int(stack["count"])
        strength = float(stack["strength"])
        owners = stack["owners"]
        assert isinstance(owners, dict)
        dominant_id = min(owners, key=lambda nation_id: (-float(owners[nation_id]), nation_id))
        nation = view.nations.get(dominant_id)
        breakdown = ", ".join(
            f"{view.nations[nation_id].name if nation_id in view.nations else nation_id} {value:g}"
            for nation_id, value in sorted(owners.items())
        )
        kinds = stack["kinds"]
        assert isinstance(kinds, dict)
        # Commonest first, and ties broken by name so the same stack reads the same way
        # twice. A scene description is a pure function of its inputs, and a tooltip that
        # reordered itself between frames would quietly break that.
        composition = ", ".join(
            f"{tally}x {str(kind).replace('_', ' ')}"
            for kind, tally in sorted(kinds.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        )
        offset_x, offset_y = offsets[domain]
        chosen = f"units:{region_id}:{domain}" == selected_marker_id
        markers.append(
            MarkerVisual(
                marker_id=f"units:{region_id}:{domain}",
                kind=MarkerKind.UNIT,
                x=position[0],
                y=position[1],
                colour=(_counter_fill(view, dominant_id, nation.colour if nation else "#CCCCCC",
                                      palette) if calm
                        else nation.colour if nation else "#CCCCCC"),
                # A bounded square-root cue makes large forces visibly heavier without
                # allowing one strategic stack to cover a small country.
                size=11.0 + min(7.0, sqrt(max(0.0, strength)) * 1.2),
                z=30,
                # **The counter is read at a glance or not at all.** `1 / 2.3100000001`
                # is what `:g` prints for a stack that has taken losses, and thirty of
                # them across a theatre is the map covered in decimals. The exact
                # strength is in the tooltip below, where somebody who wants it is
                # already pointing.
                # Calm: the one number that decides a battle. "4 / 28" asked a player to
                # know that the first figure counts formations and the second is strength.
                label=(say_strength(strength) if calm
                       else f"{count} / {say_strength(strength)}"),
                tooltip=(
                    f"{names[domain]}: {count} unit{'s' if count != 1 else ''}, "
                    f"strength {strength:g}\n{composition}\n{breakdown}"
                ),
                domain=domain,
                offset_x=offset_x,
                offset_y=offset_y,
                outline=(palette.outline_selected if chosen else palette.marker_outline),
                label_colour=palette.marker_text,
                # **The province, carried on the counter.** A click lands on the counter,
                # not on the ground under it, and this is what lets the view answer with
                # the province anyway without hit-testing the scene a second time.
                region_id=region_id,
                selected=chosen,
            )
        )

    if calm:
        markers.extend(_country_names(view, place, palette, me, enemies, bounds or {}))

    # Sorting makes the output independent of the order rows arrived in, which is what
    # lets the scene digest be a meaningful equality check.
    return SceneDescription(
        pack_id=pack_id,
        projection_key=projection.key,
        layer=layer,
        unit_domains=tuple(sorted(active_domains)),
        lod=lod,
        turn=view.turn,
        regions=tuple(sorted(regions, key=lambda r: r.region_id)),
        markers=tuple(sorted(markers, key=lambda m: m.marker_id)),
        observer_nation_id=view.observer_nation_id,
        background=palette.ocean,
    )
