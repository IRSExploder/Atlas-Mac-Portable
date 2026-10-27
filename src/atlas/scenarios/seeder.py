"""Seeding machinery that belongs to no particular era.

A scenario is mostly *data* -- who exists, how strong they are, who resents whom. The
parts that are not data are the parts that read geometry: turning the water layer into
regions, joining regions to their neighbours, working out which coasts a fleet can put
into, spreading an army over the ground it can actually march to. None of that knows what
year it is, and all of it was written once inside the 1936 scenario because 1936 was the
only scenario that needed it.

So a scenario module is now a :class:`Scenario` -- pure data -- handed to
:func:`build_world` and :func:`apply_politics`. The 1936 module still owns every 1936
*fact*; what it borrows from here is the arithmetic. A second era is a second data file.

**What counts as data and what counts as arithmetic** is worth stating, because the line
moved once already and will be tempting to move again. Anything a period historian would
argue about is data: who exists, how many battleships, who hates whom, how many divisions
a colony was garrisoned with. Anything that would be *the same argument in any century* is
arithmetic: that a fleet needs a port, that an army cannot be marched across an ocean,
that a capital is garrisoned first. When a new era wants to change something in the second
list, that is the signal to add a field rather than to fork the function.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import unicodedata
from dataclasses import dataclass, field, replace

from ..engine.production import BUILD_DAYS
from ..engine.research import TECHNOLOGIES, Technology
from ..engine.trade import demand_of, is_unreachable
from ..map.pack import GeometryPack, PackSet
from ..state.bootstrap import (
    DEFAULT_UNIT_TEMPLATES,
    CitySeed,
    NationSeed,
    RegionSeed,
    UnitSeed,
    UnitTemplateSeed,
    WorldSeed,
)
from ..state.models import CommitmentKind, Difficulty, EventKind, TreatyStatus, Visibility
from .demography import assign_population, rescale_minor_economies
from .development import assign_development
from .era import Era
from .leaders import leader_for
from .resources import GOODS, endowment_for
from .territory import SEA_SOURCE, TerritoryMap, load_territory

__all__ = [
    "NAVAL_TEMPLATES",
    "SEAGOING_UNIT_TEMPLATES",
    "Power",
    "Scenario",
    "ShipClass",
    "apply_politics",
    "build_world",
    "country_of",
    "mark_coasts",
    "naval_adjacency",
    "region_adjacency",
    "sea_regions",
    "slug",
]

#: What a fleet is made of. Three kinds, for the same reason the army has three: each has
#: to mean something the simulation acts on differently.
#:
#: A battleship is surface combat power and nothing else. A carrier is weaker in a gun
#: action and reaches a sea away, which is the whole argument of the 1930s naval staffs
#: and settles itself here the way it settled itself historically. A transport fights
#: badly and is the only thing that puts an army ashore, so a landing is a decision about
#: whether the sea is safe enough to risk them.
#:
#: The *kinds* are engine-level rather than period flavour -- :data:`BUILD_DAYS` names
#: them and :mod:`atlas.engine.naval` looks for a ``transport`` by name. The *costs* are a
#: baseline written against a 1936 treasury, steep on purpose: a capital ship was years of
#: a national budget, and a navy that can be conjured in a turn is not a navy. An era that
#: prices its ships differently supplies its own tuple.
NAVAL_TEMPLATES: tuple[UnitTemplateSeed, ...] = (
    UnitTemplateSeed("battleship", domain="sea", cost_treasury=60, cost_manpower=8,
                     base_strength=14.0, move_range=2,
                     build_days=BUILD_DAYS["battleship"]),
    UnitTemplateSeed("carrier", domain="sea", cost_treasury=70, cost_manpower=7,
                     base_strength=9.0, move_range=2, build_days=BUILD_DAYS["carrier"]),
    UnitTemplateSeed("transport", domain="sea", cost_treasury=18, cost_manpower=3,
                     base_strength=2.0, move_range=2, build_days=BUILD_DAYS["transport"]),
)

#: The engine's default kinds with a fleet alongside them -- and with build times, which
#: the defaults leave at zero because a division raised this month is a defensible
#: abstraction and a battleship launched this month is not.
SEAGOING_UNIT_TEMPLATES: tuple[UnitTemplateSeed, ...] = tuple(
    replace(template, build_days=BUILD_DAYS.get(template.kind, template.build_days))
    for template in DEFAULT_UNIT_TEMPLATES
) + NAVAL_TEMPLATES


def slug(text: str) -> str:
    """A region id fragment: lowercase, ascii, underscores, nothing else."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", folded.lower())).strip("_") or "x"


def country_of(geo_key: str) -> str:
    """The modern country a shape belongs to, as its key spells it.

    Natural Earth's ``adm1_code`` is the country's code and an ordinal --
    ``admin1-10m:DEU-1573`` -- so the country is the first three characters after the
    source. This is only ever used as a *fallback* for a province the scenario never
    named, so the handful of codes that do not follow the convention cost nothing worse
    than one missing border, and the country-level pack supplies that one anyway.
    """
    return geo_key.split(":", 1)[-1][:3]


def sea_regions(pack: GeometryPack | PackSet) -> list[RegionSeed]:
    """One region per stretch of open water the pack knows about.

    Water is a region like any other, which is most of why a navy costs so little new
    machinery: a fleet is units standing in it, sea control is the same
    ``controller_nation_id`` an occupation writes, and a fleet action is a contest
    resolved by the same seeded draw as a battle for a province.

    Every one is ``kind='ocean'``, straits included. The schema distinguishes them and
    nothing yet acts on the distinction, and marking the Channel a strait would let an
    army walk across it -- rule UNIT-002 refuses a land unit in an *ocean* and says
    nothing about a strait.
    """
    seas: list[RegionSeed] = []
    taken: set[str] = set()
    for entry in sorted(pack.entries, key=lambda e: e.geo_key):
        if not entry.geo_key.startswith(f"{SEA_SOURCE}:"):
            continue
        # Named, not numbered. Objectives and orders are written in region ids, and
        # `s_1159116607` is not something a staff can say it is sailing for.
        base = f"s_{slug(entry.name or entry.geo_key)}"
        region_id, suffix = base, 1
        while region_id in taken:
            suffix += 1
            region_id = f"{base}_{suffix}"
        taken.add(region_id)
        seas.append(
            RegionSeed(
                id=region_id,
                name=entry.name or entry.geo_key,
                geo_key=entry.geo_key,
                owner_nation_id=None,
                kind="ocean",
                terrain="water",
                population=0.0,
            )
        )
    return seas


def naval_adjacency(
    pack: GeometryPack | PackSet, regions: list[RegionSeed]
) -> tuple[tuple[str, str, str], ...]:
    """Sea to sea, and sea to the coasts it washes.

    Sea to sea is exact vertex sharing, because both sides come from the same layer. Sea
    to land cannot be: land and water were digitised separately and a coastline appears
    in both with coordinates that are close and never equal, so that side is matched on a
    grid instead. See :func:`~atlas.scenarios.earth.derive_coast_adjacency`.
    """
    from .earth import derive_adjacency, derive_coast_adjacency

    packs = getattr(pack, "packs", (pack,))
    sea_pack = next(
        (p for p in packs if any(e.geo_key.startswith(f"{SEA_SOURCE}:") for e in p.entries)),
        None,
    )
    if sea_pack is None:
        return ()

    of_shape = {key: region.id for region in regions for key in region.geo_keys}
    pairs: set[tuple[str, str, str]] = set()

    for a, b in derive_adjacency(sea_pack):
        first, second = of_shape.get(a), of_shape.get(b)
        if first and second and first != second:
            lo, hi = sorted((first, second))
            pairs.add((lo, hi, "sea"))

    for land in packs:
        if land is sea_pack:
            continue
        for sea_key, land_key in derive_coast_adjacency(sea_pack, land):
            water, coast = of_shape.get(sea_key), of_shape.get(land_key)
            if water and coast and water != coast:
                lo, hi = sorted((water, coast))
                pairs.add((lo, hi, "sea"))

    return tuple(sorted(pairs))


def region_adjacency(
    pack: GeometryPack | PackSet,
    regions: list[RegionSeed],
    territory: TerritoryMap | None,
) -> tuple[tuple[str, str], ...]:
    """Region borders, derived from shape borders in every pack behind ``pack``.

    Two things make this more than a rename of the old one-shape-per-region version.

    Adjacency is derived *within* a pack and never across two. Country outlines and
    province outlines are separately simplified, so a coordinate that appears in both is
    a coincidence rather than a shared border, and treating the packs as one coordinate
    space would invent frontiers.

    A province the scenario never named still resolves to a region -- the country-level
    one its ground belongs to. Without that, a promoted power would border only other
    promoted powers: Germany would have no frontier with Belgium at all, because the
    country pack knows a Germany that this world does not have.
    """
    from .earth import derive_adjacency

    of_shape = {key: region.id for region in regions for key in region.geo_keys}
    of_country = {
        region.geo_key.split(":", 1)[-1]: region.id
        for region in regions
        if region.kind != "ocean"
        and (territory is None or not region.geo_key.startswith(f"{territory.source}:"))
    }

    pairs: set[tuple[str, str]] = set()
    for one in getattr(pack, "packs", (pack,)):
        if any(entry.geo_key.startswith(f"{SEA_SOURCE}:") for entry in one.entries):
            continue  # water is joined up separately; see naval_adjacency
        for a, b in derive_adjacency(one):
            first = of_shape.get(a) or of_country.get(country_of(a))
            second = of_shape.get(b) or of_country.get(country_of(b))
            if first and second and first != second:
                pairs.add((first, second) if first < second else (second, first))
    return tuple(sorted(pairs))


def mark_coasts(
    regions: list[RegionSeed], adjacency: tuple[tuple[str, ...], ...]
) -> list[RegionSeed]:
    """Retag every land region touching water as ``coastal``.

    A coast is land that touches water, which is a fact about the map rather than a
    property of the shape -- so it is discovered from the adjacency and written back. It
    is what decides where a fleet can put in and where an invasion can come ashore.
    """
    water = {region.id for region in regions if region.kind == "ocean"}
    if not water:
        return regions
    shore = {
        other
        for a, b, *_ in adjacency
        for near, other in ((a, b), (b, a))
        if near in water and other not in water
    }
    return [
        replace(region, kind="coastal")
        if region.id in shore and region.kind == "land"
        else region
        for region in regions
    ]


# --------------------------------------------------------------------------- the data


@dataclass(frozen=True, slots=True)
class Power:
    """A power the scenario names, composed from one or more modern regions.

    Deliberately period-neutral. A 1936 Czechoslovakia and a 2026 Saudi Arabia differ in
    every figure and in none of the fields: both are a name, a colour, some ground, a
    capital, a disposition and six numbers.
    """

    id: str
    name: str
    colour: str
    #: ADM0_A3 codes of the modern regions this power holds.
    regions: tuple[str, ...]
    capital: str
    character: str
    goals: tuple[str, ...]
    military: float
    economy: float
    stability: float
    legitimacy: float
    treasury: float = 100.0
    manpower: float = 100.0
    #: The measurable half of `goals`. Prose is what a government reads and a predicate is
    #: what the engine can check, and neither substitutes for the other -- the sentence
    #: carries the *why*, and nothing in `atlas.engine.goals` can score "outlast Western
    #: support for Kyiv". Optional, and empty for every power no one has authored aims for,
    #: which is every nation of two of the three eras.
    aims: tuple = ()


@dataclass(frozen=True, slots=True)
class ShipClass:
    """One line of a fleet's order of battle: what it is, and what one is worth.

    ``scale`` divides the owner's military figure and adds it to ``base``, so a capital
    ship is worth more in the hands of a serious naval power than a nominal one. A
    ``scale`` of ``None`` means the class does not vary that way -- a landing ship is a
    landing ship whoever owns it, and what matters about it is that it exists.

    Which classes an era fields is data, but one of the names is not free: rule and engine
    alike look for ``transport`` by name as the thing that puts an army ashore
    (:func:`atlas.engine.naval.sea_lift`). An era may call its capital ship whatever it
    likes; its amphibious lift must be a ``transport``.
    """

    kind: str
    base: float
    scale: float | None = None

    def strength(self, military: float) -> float:
        return round(self.base + (military / self.scale if self.scale else 0.0), 2)


#: 1936's three, and the default for any era that does not say otherwise.
CLASSIC_SHIP_CLASSES: tuple[ShipClass, ...] = (
    ShipClass("battleship", 12.0, 12.0),
    ShipClass("carrier", 8.0, 18.0),
    ShipClass("transport", 2.0),
)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Everything an era *is*, with nothing about how a world gets built from it.

    ``id`` is load-bearing three times over: it is the preset the referee loads this
    world's lore and hard constraints by, the territory file's name, and the key the
    registry finds this scenario under. They are one string on purpose -- three ids that
    can drift apart is three ways to ship a world whose era rules never arrive.
    """

    id: str
    name: str
    start_date: str
    rng_seed: int
    #: The default world id the CLI and the setup screen offer.
    default_world_id: str
    #: Which period this is, for the three tables keyed by nation rather than by scenario:
    #: leaders, endowments and population. **Stated, never inferred from the start date** --
    #: see :mod:`atlas.scenarios.era`. Required, because a scenario that forgets it would
    #: silently take another era's governments.
    era: Era
    powers: tuple[Power, ...] = ()
    #: Formations beyond the infantry line, by power id. A power absent from these has
    #: none: in 1936 that is most of the map, and it is the difference between an army
    #: that is merely large and one that is mechanised.
    armour: dict[str, int] = field(default_factory=dict)
    air: dict[str, int] = field(default_factory=dict)
    #: What a fleet of this era is made of, in the order :attr:`fleets` counts them.
    ship_classes: tuple[ShipClass, ...] = CLASSIC_SHIP_CLASSES
    #: Hulls by power id, one count per entry in :attr:`ship_classes`. A power absent
    #: from this has no navy, which is the right answer for a landlocked one.
    fleets: dict[str, tuple[int, ...]] = field(default_factory=dict)
    #: Ground a power holds that is not its own nation: ``minor id -> (owner, divisions)``.
    #: The territory file draws it; this says who it belongs to and how hard it is held.
    colonies: dict[str, tuple[str, int]] = field(default_factory=dict)
    unit_templates: tuple[UnitTemplateSeed, ...] = SEAGOING_UNIT_TEMPLATES
    #: What a nation of this era can work out how to do. Defaults to the engine's
    #: interwar set, which is what every world had before eras had their own -- so a
    #: scenario that says nothing keeps exactly the catalogue it always offered.
    #:
    #: **Every effect must name a unit kind this era actually fields.** One that does
    #: not used to vanish silently when the programme completed; it is now reported at
    #: runtime and pinned by a test, because a technology that half-works is worse than
    #: one that is missing.
    technologies: tuple[Technology, ...] = TECHNOLOGIES
    #: Who is armed, as ``power id -> (warheads, delivery tier)``. Empty is the answer for
    #: any era before 1945 and for most eras after it.
    #:
    #: **Written through :func:`apply_politics` rather than through the ``WorldSeed``**, and
    #: that is deliberate rather than incidental: the seed dataclass is what
    #: ``scratchpad/seed_fingerprint.py`` hashes to prove a refactor changed nothing, so
    #: adding a field to it moves every era's fingerprint including the ones that have no
    #: arsenals at all. Nuclear weapons are political state, they belong with the relations
    #: and the treaties, and putting them there leaves 1936 byte-identical.
    arsenals: dict[str, tuple[int, str]] = field(default_factory=dict)
    #: How many of a power's submarines carry ballistic missiles, as ``power id -> count``.
    #:
    #: **A count rather than a kind, and a nation rather than a template.** There is one
    #: ``submarine`` unit kind per era, so nothing on the unit or its template can say that
    #: Britain's boats carry missiles and China's do not -- and a second kind would have to
    #: be taught to :mod:`atlas.engine.naval` and :mod:`atlas.engine.nuclear`, both of which
    #: look ``submarine`` up by name.
    #:
    #: :func:`atlas.engine.nuclear.survivability` takes ``min(afloat, declared)``, so losses
    #: eat the attack boats first and the deterrent last. **That is optimistic for the
    #: defender and it is the honest way to be wrong**: the engine does not know which hull
    #: was which, so an anti-submarine campaign that deliberately hunted the missile boats
    #: cannot be modelled. What it does model is that sinking a navy eventually sinks the
    #: deterrent, and that a power with no missile boats never had one.
    #:
    #: A power absent from this counts every boat, which is what every world did before the
    #: column existed -- so an era that says nothing is unchanged.
    deterrent_boats: dict[str, int] = field(default_factory=dict)
    #: What each power is BUILT to do, as ``power id -> {axis: stance}``. See
    #: :mod:`atlas.engine.doctrines` for the axes and what each stance costs.
    #:
    #: **Seeded here rather than on the ``WorldSeed``**, for the reason the arsenals are:
    #: the seed dataclass is what the seed fingerprint hashes, so a field on it moves every
    #: era including the ones that declare nothing. A doctrine is a standing political fact
    #: about a country, and it belongs with the relations, the treaties and the arsenals.
    #:
    #: A power absent from this has no doctrine, which reads exactly as every world read
    #: before the table existed. **Each entry is a claim about the period**, so each is
    #: checked against the era's manifest by `testing.audit` rather than against this
    #: module -- which would only prove that :func:`apply_politics` can copy a dict.
    doctrines: dict[str, dict[str, str]] = field(default_factory=dict)
    #: International bodies. ``id -> (name, kind, charter, members, permanent members)``.
    #:
    #: Seeded through :func:`apply_politics` for the same two reasons the arsenals are:
    #: they are political state that belongs with the relations and the treaties, and
    #: keeping them out of the ``WorldSeed`` dataclass leaves every era's seed
    #: fingerprint untouched. An organisation is a nation row *and* an ``organisation``
    #: row; :func:`apply_politics` writes both.
    organisations: tuple[
        tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]], ...
    ] = ()
    #: **Who the world outside the named powers actually was, in this era.**
    #:
    #: Every modern map entry no power claims becomes an independent minor, which is right
    #: for a scenario set today and wrong for every other one. A 1972 world built without
    #: this held Bosnia, Croatia, Slovenia, Eritrea, East Timor, Micronesia, Namibia,
    #: Zimbabwe and Belize as sovereign states, and gave Antarctica a government and an
    #: army -- 209 minors, in a scenario whose own hard constraints say *do not invent
    #: nations*. It also inflates the Focus candidate pool, because
    #: :func:`atlas.engine.focus.rank_candidates` scores every living nation on the map.
    #:
    #: Entries are ``(holder id, display name, modern codes)`` and cover three cases with
    #: one mechanism:
    #:
    #: * **a successor state merged back into its parent** -- Yugoslavia's seven, Eritrea
    #:   into Ethiopia. The same thing 1936 does by composing Czechoslovakia and the Soviet
    #:   Union out of successor regions, done for minors instead of for powers.
    #: * **a dependency assigned to whoever held it** -- Portuguese Angola, British Hong
    #:   Kong, the American Pacific trust territory. The holder may be a power or another
    #:   minor; both work, and a colony gets the border guard a minor would have had so it
    #:   is not free ground.
    #: * **a rename**, where the modern name is a different polity: Rhodesia, Zaire, Burma.
    #:
    #: A code named nowhere here is an independent state, exactly as before -- so a
    #: scenario that says nothing gets precisely the world it got before this existed, and
    #: 1936 and Modern Day are untouched.
    sovereignty: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    #: **Holders whose constituent codes are their own homeland, not ground they hold.**
    #:
    #: `_disown_dependencies` decides coreness by asking whether the holder *is* the code --
    #: `m_eth` holds ETH, so Ethiopia keeps its core and only ERI is disowned, which is
    #: exactly right. That reading works for every entry whose parent code is one of its
    #: children, and for every entry whose holder is a power (`gbr` is not `m_hkg`, and Hong
    #: Kong is correctly nobody's core). It has one blind spot: a **minor composed of codes
    #: that do not include its own**. Yugoslavia is SRB, HRV, BIH, SVN, MNE, MKD and KOS and
    #: there is no YUG, so all seven republics -- Belgrade included -- were seeded as
    #: nobody's homeland, and a country made of those republics held them as if they were
    #: colonies.
    #:
    #: The distinction cannot be derived from the tuple, which is why it is declared:
    #: `m_eth`'s ERI genuinely is a dependency and `m_yug`'s HRV genuinely is not. A
    #: frozenset with an empty default leaves every era that has no such composite byte
    #: identical, which is the property the note on `_disown_dependencies` relies on.
    #:
    #: `test_a_composite_minor_is_home_to_its_own_republics` fails if a future era adds a
    #: minor holder whose own code is absent and does not name it here, so the next one
    #: costs a sentence of thought rather than seven silent NULLs.
    composites: frozenset[str] = frozenset()
    #: **Ground one nation owns and another holds**, as ``region id -> controller``.
    #:
    #: The third of the three relationships a region can be in, and the last one no era
    #: expressed. A **dependency** is held ground that is nobody's core (the sovereignty
    #: map). A **colony** is the same at province granularity. An **occupation** is held
    #: ground that *is* somebody's core, and somebody who is still fighting for it -- which
    #: is why it needs its own table rather than another sovereignty entry: the sovereign
    #: does not change, and neither does the core.
    #:
    #: Three columns say it and the engine reads all three differently. ``owner_nation_id``
    #: stays the sovereign, so a peace settlement has something to hand back and the map
    #: still says whose it is. ``controller_nation_id`` is the occupier, and it is what
    #: ``COALESCE(controller_nation_id, owner_nation_id)`` picks up throughout the engine --
    #: supply, delivery range, sea lift, who a front is against. ``home_nation_id`` stays
    #: the sovereign's, which is the part a scenario would most easily get wrong: occupied
    #: ground is not the occupier's homeland, and `nuclear.deterrence` would otherwise
    #: extend a nuclear guarantee over ground the owner is trying to retake.
    #:
    #: **Written through :func:`apply_politics`**, for the reason the arsenals and the
    #: dependencies are: a ``RegionSeed`` field would move every era's seed digest to
    #: express a fact about one.
    occupied: dict[str, str] = field(default_factory=dict)
    #: **Formations standing somewhere other than at home**, as
    #: ``region id -> (power id, divisions)``.
    #:
    #: :func:`order_of_battle` spreads a power's army over its *own* reachable ground, and
    #: :func:`colonial_garrison` covers the one exception that existed -- a colony across
    #: water. Neither can express an army posted in an ally's country or one that has
    #: crossed a border, which is every army that matters in a scenario that opens with a
    #: war already under way: the Seventh Army was in Bayern in 1972 and the Group of
    #: Soviet Forces was in Germany, and a scenario could only say so by pretending the
    #: ground had changed hands.
    #:
    #: The divisions are at the power's own strength and morale, exactly as a colonial
    #: garrison is: this is the same army, posted abroad. Empty for every era that does
    #: not declare one, so nothing else moves.
    deployments: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: The chance, per nation per year, that a government is killed by something its
    #: own numbers did not predict. **Zero for every era unless the era asks**, for the
    #: same reason :attr:`deployments` is empty by default: an era that does not opt in
    #: must be byte-identical, because a world that moves is a cassette that has to be
    #: re-recorded and that is the owner's model quota. At zero no draw is taken at all
    #: -- not a draw that fails -- so not one seeded stream shifts.
    #:
    #: A rate, not a probability per turn: the span scaling in :func:`assess_shocks` is
    #: what stops a player outrunning it by taking longer turns.
    assassination_rate: float = 0.0
    #: Modern map entries that are nobody's state in this era. Their ground is drawn and
    #: owned by no one -- which is what the Antarctic Treaty of 1961 actually says, and a
    #: better answer than a nation with a treasury and a border guard on the ice.
    absent: frozenset[str] = frozenset()
    #: Directed, as ``(from, to, score, treaty, at_war)``.
    relations: tuple[tuple[str, str, float, TreatyStatus, bool], ...] = ()
    #: ``(kind, status, condition, parties)``.
    treaties: tuple[tuple[CommitmentKind, str, str, tuple[str, ...]], ...] = ()
    #: ``(kind, actor, text, visibility)`` -- the causal root every later diff can cite.
    history: tuple[tuple[EventKind, str, str, Visibility], ...] = ()

    def power(self, power_id: str) -> Power | None:
        return next((p for p in self.powers if p.id == power_id), None)

    def playable(self) -> list[tuple[str, str, str]]:
        """``(id, name, colour)`` for the setup screen's power picker.

        From the scenario rather than the map, because a scenario power is not a modern
        country: 1936's "Germany" is one power composed of several modern regions, and no
        amount of looking at the geometry will tell you that.
        """
        return [(power.id, power.name, power.colour) for power in self.powers]


# ---------------------------------------------------------------------- the arithmetic


def order_of_battle(
    power: Power, held: list[str], capital: str, morale: float, *,
    armour: int = 0, air: int = 0,
) -> list[UnitSeed]:
    """Turn a power's abstract military figure into formations on the map.

    A nation's ``military_strength`` is a summary; it is not something an army can be
    ordered to do. Without actual units the parser correctly refuses every military order
    ever written -- "you command no forces" -- and the game has nothing in it. So the
    figure is spent here on divisions, distributed across the territory the power holds
    with the capital garrisoned first.

    ``held`` is region ids, not country codes, because a promoted power holds a dozen
    provinces rather than one country -- and spreading its army over them is what makes a
    border province lightly held and the interior a place reinforcements have to come
    from.

    Formation counts are deliberately coarse. What has to be right is the *shape*: France
    fields the larger standing army and Germany the more mobile one, the Royal Navy is the
    largest fleet afloat, and the United States has almost no army behind an enormous
    economy.
    """
    units: list[UnitSeed] = []

    # Roughly one division per five points of military strength, floored at two so no
    # power on the map is literally defenceless.
    divisions = max(2, round(power.military / 5))

    def place(index: int) -> str:
        """Capital first, then spread over the rest of the territory in order."""
        return capital if index == 0 else held[index % len(held)]

    for index in range(divisions):
        units.append(
            UnitSeed(
                id=f"u_{power.id}_inf{index + 1}",
                nation_id=power.id,
                region_id=place(index),
                kind="infantry",
                strength=round(4.0 + power.military / 40.0, 2),
                morale=morale,
            )
        )
    for index in range(armour):
        units.append(
            UnitSeed(
                id=f"u_{power.id}_arm{index + 1}",
                nation_id=power.id,
                region_id=capital,
                kind="armour",
                strength=round(6.0 + power.military / 30.0, 2),
                morale=morale,
            )
        )
    for index in range(air):
        units.append(
            UnitSeed(
                id=f"u_{power.id}_air{index + 1}",
                nation_id=power.id,
                region_id=capital,
                kind="air_wing",
                strength=round(4.0 + power.military / 35.0, 2),
                morale=morale,
            )
        )
    return units


def fleet(
    power: Power,
    ports: list[str],
    morale: float,
    *,
    classes: tuple[ShipClass, ...],
    counts: tuple[int, ...],
) -> list[UnitSeed]:
    """The power's ships, spread over the coasts it can base them on.

    A fleet with no coast is not a fleet, so a power whose ports the map does not give it
    simply has no navy -- which is the right answer for a landlocked one and a signal that
    something is wrong with the map for anybody else.
    """
    if not ports:
        return []

    units: list[UnitSeed] = []
    for ship, count in zip(classes, counts, strict=False):
        for index in range(count):
            units.append(
                UnitSeed(
                    id=f"u_{power.id}_{ship.kind[:3]}{index + 1}",
                    nation_id=power.id,
                    # Round-robin over the power's own coasts: a fleet in one anchorage is
                    # a fleet one bad afternoon removes from the war.
                    region_id=ports[index % len(ports)],
                    kind=ship.kind,
                    strength=ship.strength(power.military),
                    morale=morale,
                )
            )
    return units


def colonial_garrison(
    owner: Power, colony_slug: str, held: list[str], hub: str, divisions: int, morale: float
) -> list[UnitSeed]:
    """A colonial army, stationed on the colony rather than marched from home.

    The metropole is across water, so :func:`garrisoned` can never reach a colony from the
    capital and :func:`order_of_battle` would leave it undefended. A colony gets its own
    garrison instead, spread over the ground it holds with its hub garrisoned first -- the
    force that makes a colonial war something the colonial power can actually press.
    Divisions are at the metropole's own strength: these are the same army, posted abroad.
    """
    strength = round(4.0 + owner.military / 40.0, 2)

    def place(index: int) -> str:
        return hub if index == 0 else held[index % len(held)]

    return [
        UnitSeed(
            id=f"u_{owner.id}_{colony_slug}_inf{index + 1}",
            nation_id=owner.id,
            region_id=place(index),
            kind="infantry",
            strength=strength,
            morale=morale,
        )
        for index in range(divisions)
    ]


def garrisoned(
    held: list[str], capital: str, adjacency: tuple[tuple[str, ...], ...]
) -> list[str]:
    """The stretch of a power's own territory its capital can be marched to.

    Promotion turned this from a non-question into a real one. France used to be one
    region; it is now eleven, four of which are Corsica, Guadeloupe, Guyane and Reunion --
    so spreading the army evenly over "everything France holds" put a third of the French
    order of battle in the Indian Ocean, where it can neither defend France nor invade
    anyone.
    """
    neighbours: dict[str, set[str]] = {}
    inside = set(held)
    for a, b, *_ in adjacency:
        if a in inside and b in inside:
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)

    seen = {capital}
    frontier = [capital]
    while frontier:
        current = frontier.pop()
        for neighbour in neighbours.get(current, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return [region for region in held if region in seen]


def promotable(
    pack: GeometryPack | PackSet, territory: TerritoryMap | None
) -> TerritoryMap | None:
    """The territory map, if this pack can actually draw it. Otherwise ``None``.

    Promotion is all or nothing. A world with half its provinces would have a Germany of
    twelve regions next to a France of one, which is the imbalance the territory file
    exists to remove -- so a pack without the province geometry plays at country
    granularity instead, exactly as it did before.
    """
    if territory is None:
        return None
    return territory if all(key in pack for key in territory.geo_keys()) else None


def minor_power(minor_id: str, name: str, colour: str, code: str, area: float) -> Power:
    """A neutral minor expressed as a :class:`Power`, purely so a *promoted* minor can
    reuse the power garrison path (:func:`order_of_battle`, :func:`fleet`).

    Its figures are the country-level minor's, unchanged: promoting a minor to provinces
    changes its geometry and how its defence is spread across it, never how strong it is.
    Both the country-level and the promoted minor build their :class:`NationSeed` from one
    of these, so the two can never quietly disagree about what a minor is.
    """
    return Power(
        id=minor_id,
        name=name,
        colour=colour,
        regions=(code,),
        capital=code,
        character=f"{name} has no ambition beyond staying out of other people's wars.",
        goals=("remain neutral", "avoid attracting attention"),
        military=round(min(20.0, 3.0 + area * 0.02), 1),
        economy=round(min(25.0, 4.0 + area * 0.02), 1),
        stability=60.0,
        legitimacy=55.0,
        treasury=15.0,
        manpower=20.0,
    )


def minor_nation_seed(minor: Power, capital_region_id: str) -> NationSeed:
    """The nation row for a minor, from the :class:`Power` :func:`minor_power` built."""
    return NationSeed(
        id=minor.id,
        name=minor.name,
        colour=minor.colour,
        capital_region_id=capital_region_id,
        personality_blurb=minor.character,
        standing_goals=minor.goals,
        military_strength=minor.military,
        economy=minor.economy,
        stability=minor.stability,
        legitimacy=minor.legitimacy,
        treasury=minor.treasury,
        manpower=minor.manpower,
    )


# ------------------------------------------------------------------------- the assembly


def deployed(
    power: Power, region_id: str, divisions: int, morale: float, *, tag: str
) -> list[UnitSeed]:
    """Divisions of ``power`` standing in ``region_id``, wherever that is.

    The same army as :func:`order_of_battle` builds, at the same strength and morale, put
    where the scenario says it stands rather than where reachability would put it. See
    :attr:`Scenario.deployments`.
    """
    strength = round(4.0 + power.military / 40.0, 2)
    return [
        UnitSeed(
            id=f"u_{power.id}_{tag}{index + 1}",
            nation_id=power.id,
            region_id=region_id,
            kind="infantry",
            strength=strength,
            morale=morale,
        )
        for index in range(divisions)
    ]


def build_world(
    scenario: Scenario,
    pack: GeometryPack | PackSet,
    *,
    world_id: str | None = None,
    player: str | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    seed: int | None = None,
    include_minors: bool = True,
) -> WorldSeed:
    """Build ``scenario``'s starting world from the geometry ``pack`` can draw.

    **The powers the territory file describes are promoted on the spot.** A promoted
    Germany is not one region called Germany; it is Berlin, Silesia, East Prussia, Bayern
    and the rest, and a war between France and Germany is fought province by province
    because there are provinces to fight over. Everything the file does not describe stays
    one region per country. Which of the two a nation gets is decided entirely by the
    geometry the pack can draw, so a country-level pack still produces exactly the world
    it used to.

    Regions not held by a listed power become minor nations in their own right when
    ``include_minors`` is set, so the map stays whole rather than showing a ring of
    unclaimed territory around the great powers.
    """
    from .earth import _colour_for, _land_area

    territory = promotable(pack, load_territory(scenario.id))
    # Country-level entries only. With the province pack open as well, every shape in it
    # would otherwise read as a country and the world would gain four thousand minors.
    available = {
        entry.geo_key.split(":", 1)[1]: entry
        for entry in pack.entries
        if not entry.geo_key.startswith(f"{SEA_SOURCE}:")
        and (territory is None or not entry.geo_key.startswith(f"{territory.source}:"))
    }
    # Ground the territory file draws at province granularity. It must not also become a
    # country-level region: that is the same land owned twice, by two different nations.
    covered = territory.countries if territory else frozenset()
    by_id = {power.id: power for power in scenario.powers}

    nations: list[NationSeed] = []
    regions: list[RegionSeed] = []
    cities: list[CitySeed] = []
    units: list[UnitSeed] = []
    claimed: set[str] = set()
    garrisons: list[tuple[Power, list[str], str]] = []
    # Colonies to garrison once adjacency exists: (owner power, colony slug, held, hub,
    # divisions). Kept apart from `garrisons` because a colony is defended from its own
    # ground, not marched to from a capital across the sea.
    colonies: list[tuple[Power, str, list[str], str, int]] = []

    def region_id(code: str) -> str:
        return f"r_{code.lower()}"

    def area_of(keys: tuple[str, ...]) -> float:
        return sum(_land_area(pack, key) for key in keys)

    for power in scenario.powers:
        groups = (territory.nations.get(power.id) or {}) if territory else {}
        # What the power holds that the province map does not cover: the Soviet Union's
        # Central Asian republics, Britain's Ireland. A power can be promoted in Europe
        # and country-level everywhere else, and nothing downstream can tell.
        held_codes = [
            code for code in power.regions if code in available and code not in covered
        ]
        if not groups and not held_codes:
            continue

        held: list[str] = []
        for name, keys in sorted(groups.items()):
            promoted_id = f"r_{power.id}_{slug(name)}"
            regions.append(
                RegionSeed(
                    id=promoted_id,
                    name=name,
                    geo_key=keys[0],
                    owner_nation_id=power.id,
                    population=round(min(120.0, area_of(keys) * 0.4), 2),
                    parts=tuple(keys),
                )
            )
            held.append(promoted_id)

        for code in held_codes:
            entry = available[code]
            claimed.add(code)
            regions.append(
                RegionSeed(
                    id=region_id(code),
                    name=entry.name,
                    geo_key=entry.geo_key,
                    owner_nation_id=power.id,
                    population=round(min(120.0, _land_area(pack, entry.geo_key) * 0.4), 2),
                )
            )
            held.append(region_id(code))

        capital_group = territory.capitals.get(power.id) if territory else None
        if capital_group and capital_group in groups:
            capital = f"r_{power.id}_{slug(capital_group)}"
        elif power.capital in available and power.capital not in covered:
            capital = region_id(power.capital)
        else:
            capital = held[0]

        nations.append(
            NationSeed(
                id=power.id,
                name=power.name,
                colour=power.colour,
                capital_region_id=capital,
                is_player=(power.id == player),
                personality_blurb=power.character,
                standing_goals=power.goals,
                military_strength=power.military,
                economy=power.economy,
                stability=power.stability,
                legitimacy=power.legitimacy,
                treasury=power.treasury,
                manpower=power.manpower,
            )
        )

        cities.append(
            CitySeed(
                id=f"c_{power.id}",
                region_id=capital,
                name=f"{power.name} (capital)",
                tier=4,
            )
        )
        # Armies are placed once adjacency exists, because where a power can actually
        # march is now a question with an answer.
        garrisons.append((power, held, capital))

    # A minor the territory file names but the scenario's powers do not is promoted just
    # as a power is: its province regions come from the same groups, its defence is spread
    # across them by the same garrison path, and its strength stays a neutral minor's.
    # Eritrea and Somalia become ground a war can be fought across, not a single region
    # one battle decides. The machinery is inert until the file names a non-power, so a
    # scenario adds one minor at a time.
    promoted_minors = (
        {nid: g for nid, g in territory.nations.items() if nid not in by_id}
        if territory and include_minors
        else {}
    )
    for minor_id, groups in sorted(promoted_minors.items()):
        if not groups:
            continue
        # The modern country this minor's provinces belong to, so its name, colour and
        # strength are exactly the country-level minor's -- promotion is geometry only.
        code = country_of(next(iter(groups.values()))[0])
        entry = available.get(code)
        if entry is None:
            continue  # the base pack does not know this country; leave it unpromoted
        claimed.add(code)
        area = _land_area(pack, entry.geo_key)

        # A colony's ground belongs to the colonial power and begins as its own core; an
        # independent minor owns its own ground. The province geometry is identical either
        # way -- promotion is what draws the map, ownership is who holds it.
        colony = scenario.colonies.get(minor_id)
        owner = colony[0] if colony else minor_id

        held = []
        for name, keys in sorted(groups.items()):
            promoted_pid = f"r_{minor_id}_{slug(name)}"
            regions.append(
                RegionSeed(
                    id=promoted_pid,
                    name=name,
                    geo_key=keys[0],
                    owner_nation_id=owner,
                    population=round(min(120.0, area_of(keys) * 0.4), 2),
                    parts=tuple(keys),
                )
            )
            held.append(promoted_pid)

        capital_group = territory.capitals.get(minor_id)
        hub = (
            f"r_{minor_id}_{slug(capital_group)}"
            if capital_group and capital_group in groups
            else held[0]
        )
        if colony:
            # No nation of its own: a colony is ground its owner holds, garrisoned from
            # its own hub once adjacency exists (the metropole is across the sea).
            colonies.append((by_id[owner], slug(entry.name), held, hub, colony[1]))
        else:
            minor = minor_power(minor_id, entry.name, _colour_for(entry.geo_key), code, area)
            nations.append(minor_nation_seed(minor, hub))
            garrisons.append((minor, held, hub))

    if include_minors:
        # Who held what, where the era says the modern map is wrong. `held_by` answers
        # "whose is this code", `styled` answers "what was it called", and `grouped`
        # collects the codes one holder ends up with so a composite -- Yugoslavia's seven
        # republics, Portugal's five African territories -- becomes one nation rather than
        # seven or five.
        held_by: dict[str, str] = {}
        styled: dict[str, str] = {}
        # **The seat is the first code the table lists, not the first one encountered.**
        # Codes are walked in alphabetical order, so taking the first arrival would govern
        # Ethiopia from Eritrea and Yugoslavia from Bosnia.
        seat_of: dict[str, str] = {}
        for holder, name, codes in scenario.sovereignty:
            styled[holder] = name
            seat_of[holder] = codes[0]
            for code in codes:
                held_by[code] = holder
        grouped: dict[str, list[str]] = {}

        for code, entry in sorted(available.items()):
            if code in claimed or code in covered:
                continue
            area = _land_area(pack, entry.geo_key)
            if code in scenario.absent:
                # Drawn, and nobody's. `assign_population` skips an unowned region and the
                # ownerless sea regions below prove the rest of the pipeline tolerates one.
                regions.append(
                    RegionSeed(
                        id=region_id(code),
                        name=entry.name,
                        geo_key=entry.geo_key,
                        owner_nation_id=None,
                        population=round(min(120.0, area * 0.4), 2),
                    )
                )
                continue

            holder = held_by.get(code, f"m_{code.lower()}")
            grouped.setdefault(holder, []).append(code)
            regions.append(
                RegionSeed(
                    id=region_id(code),
                    name=entry.name,
                    geo_key=entry.geo_key,
                    owner_nation_id=holder,
                    population=round(min(120.0, area * 0.4), 2),
                )
            )
            # Even a neutral has a border guard: without one, any power at all can walk in
            # against literally no resistance and the map stops meaning anything.
            #
            # **A dependency does not get one, and that is a decision rather than an
            # oversight.** The guard exists because an independent minor has nobody else;
            # a dependency has a metropole, its defence is that power's army, and taking
            # it is an act against that power rather than against nobody -- which is most
            # of the reason for assigning it in the first place. Garrisoning them
            # separately charged Britain thirty formations of upkeep for Pitcairn,
            # Montserrat and the Cayman Islands and bankrupted it in eleven turns, on an
            # economy of thirty. Colonies were lightly held; that is why they changed hands
            # so easily.
            #
            # The guard is raised below, with the nation row, because both belong on the
            # seat of government and the seat is not known until the grouping is done --
            # walked alphabetically, Yugoslavia's first arrival is Bosnia.

        # A nation row for every holder that is not already one. A power holding a colony
        # already exists; a minor -- composite or not -- is built here from the ground it
        # ended up with, so its strength follows its whole extent rather than one province.
        for holder, codes in sorted(grouped.items()):
            if holder in by_id:
                continue  # a power's colony; the power has its own row already
            seat = seat_of.get(holder, codes[0])
            if seat not in codes:  # its own ground went to somebody else
                seat = codes[0]
            entry = available[seat]
            extent = sum(_land_area(pack, available[c].geo_key) for c in codes)
            minor = minor_power(
                holder, styled.get(holder, entry.name), _colour_for(entry.geo_key),
                seat, extent,
            )
            nations.append(minor_nation_seed(minor, region_id(seat)))
            units.append(
                UnitSeed(
                    id=f"u_{holder}_inf1",
                    nation_id=holder,
                    region_id=region_id(seat),
                    kind="infantry",
                    strength=round(2.0 + min(3.0, extent * 0.01), 2),
                    morale=55.0,
                )
            )

    # Water last, so nothing above can accidentally claim it, garrison it or turn it into
    # a minor nation. It belongs to nobody: sea control is something a fleet takes.
    regions.extend(sea_regions(pack))

    adjacency = region_adjacency(pack, regions, territory) + naval_adjacency(pack, regions)
    regions = mark_coasts(regions, adjacency)

    # Morale tracks stability: an army is only as steady as the state behind it, and in
    # 1936 that distinction is most of the difference between the Soviet Union's size and
    # what it could actually do with it.
    coastal = {region.id for region in regions if region.kind == "coastal"}
    for power, held, capital in garrisons:
        home = garrisoned(held, capital, adjacency)
        units.extend(
            order_of_battle(
                power, home, capital, morale=round(power.stability, 1),
                armour=scenario.armour.get(power.id, 0),
                air=scenario.air.get(power.id, 0),
            )
        )
        # Ports, not merely coasts it owns: a fleet based on ground the army cannot reach
        # from home is a fleet in somebody else's war.
        units.extend(
            fleet(
                power,
                [region for region in home if region in coastal],
                morale=round(power.stability, 1),
                classes=scenario.ship_classes,
                counts=scenario.fleets.get(power.id, ()),
            )
        )

    # Colonial garrisons, stationed on the colony itself. `home` is the stretch of the
    # colony its own hub can march to, so a garrison never spills onto ground it cannot
    # actually hold -- the same reachability rule the metropole's army obeys.
    for owner, colony_slug, held, hub, divisions in colonies:
        home = garrisoned(held, hub, adjacency)
        units.extend(
            colonial_garrison(
                owner, colony_slug, home, hub, divisions, morale=round(owner.stability, 1)
            )
        )

    # Armies standing where the scenario posts them: an ally's ground, or ground they have
    # just crossed into. Sorted, because the world digest must not depend on dict order.
    drawn = {region.id for region in regions}
    for region_id, (power_id, divisions) in sorted(scenario.deployments.items()):
        posted = by_id.get(power_id)
        if posted is None:
            raise ValueError(
                f"{scenario.id}: {region_id!r} is deployed to by {power_id!r}, "
                f"which is not a power in this scenario")
        if region_id not in drawn:
            raise ValueError(
                f"{scenario.id}: deployment region {region_id!r} is not on the map")
        units.extend(
            deployed(posted, region_id, divisions, morale=round(posted.stability, 1),
                     tag=f"{region_id.removeprefix('r_')}_inf")
        )

    # **Substance before shape, in three steps that depend on each other.**
    # A minor's economy is a real figure rather than its land area; development is then
    # distributed from those economies; and population is distributed by the same weights
    # development used, because the rich provinces and the populous ones are the same
    # provinces. Last of all, so every region every power ended up holding is present --
    # including colonies, which are added late.
    nations = rescale_minor_economies(nations)
    regions = assign_development(regions, nations)
    regions = assign_population(regions, nations, era=scenario.era)

    return WorldSeed(
        id=world_id or scenario.default_world_id,
        name=scenario.name,
        start_date=scenario.start_date,
        rng_seed=scenario.rng_seed if seed is None else seed,
        difficulty=difficulty,
        nations=tuple(nations),
        regions=tuple(regions),
        adjacency=adjacency,
        cities=tuple(cities),
        unit_templates=scenario.unit_templates,
        units=tuple(units),
        preset_id=scenario.id,
    )


#: How many months of its *demand* a nation starts with in store. A buffer, so a blockade
#: is a mounting problem rather than an instant one. See the comment at the call site for
#: why this is measured against demand rather than against the nation's own production.
_MONTHS_OF_STOCK = 5.0


#: The three ways a world's trade can be plumbed. ``off`` is the greedy rule every world in
#: the project was built with; ``all`` splits every buyer; ``large`` splits only the buyers
#: that carry the concentration and leaves the rest on one supplier.
SPREAD_OFF = "off"
SPREAD_ALL = "all"
SPREAD_LARGE = "large"
_SPREAD_MODES = (SPREAD_OFF, SPREAD_ALL, SPREAD_LARGE)

#: **What "on" means, and it is now ``large`` rather than ``all``. The owner's call.**
#:
#: The three modes are three answers to one question and none of them is free. ``off`` is
#: the greedy original and it grants the top exporter a rent nobody competed for -- 65%,
#: 56% and 45% of world trade income to one power in the three eras. ``all`` halves that and
#: pays for it by taking *every* captive buyer on the board to two suppliers: 98-99% of
#: seeded dependencies to zero, in every era and every good. A dependency one war can sever
#: is a different strategic object from one that needs two, and deleting all of them is a
#: larger change to the game than the concentration numbers looked like.
#:
#: ``large`` is the compromise and the measurements say it is a good one: it keeps 66-100%
#: of the concentration reduction ``all`` bought, for 18-32% more routes instead of 96-99%
#: more, and **retains 66/72/82% of captive buyers** -- the long tail of small dependencies,
#: while the large industrial buyers whose volume made the concentration are the ones split.
#: On cold-war it is strictly dominant. The figures are in
#: `scratchpad/TRADE_CONCENTRATION_OPTIONS.md`.
#:
#: ``all`` is still reachable by name, and it has to be, because it is the arm with the
#: long-game measurements behind it.
_SPREAD_ON = SPREAD_LARGE

#: What the older boolean spelling of the flag means, kept because a bool is what a caller
#: naturally reaches for and because the two committed harnesses already pass one.
#:
#: ``True`` follows :data:`_SPREAD_ON` rather than pinning ``all`` separately: two spellings
#: of "on" that meant different modes would be a harness and an environment variable quietly
#: measuring two different boards.
_SPREAD_FROM_BOOL = {True: _SPREAD_ON, False: SPREAD_OFF}

#: Aliases accepted from the environment. ``on`` means :data:`_SPREAD_ON`; ``all`` and
#: ``large`` still name themselves, so nothing that asked for a specific mode moves.
_SPREAD_ALIASES = {
    "1": _SPREAD_ON, "on": _SPREAD_ON, "true": _SPREAD_ON, "yes": _SPREAD_ON,
    "all": SPREAD_ALL, "large": SPREAD_LARGE,
    "0": SPREAD_OFF, "off": SPREAD_OFF, "false": SPREAD_OFF, "no": SPREAD_OFF, "": SPREAD_OFF,
}


def _spread_mode_from_env() -> str:
    """``ATLAS_TRADE_SPREAD`` picks how a new world's trade is plumbed. Default ``large``.

    **The default is in the code rather than in the environment, deliberately.** A world
    that is plumbed one way under one shell and another way under another is a world whose
    fingerprint depends on who built it, and every pinned digest and every cassette in this
    project is a claim about a board. So the mode ships as a value here; the variable
    remains for measurement, and naming ``off`` still reproduces the greedy board exactly.

    **Default off, and reversible by construction**: the rules are branches of one function
    reading the same endowments, nothing is stored that says which built a world, and a world
    built any way is a world the rest of the engine cannot tell apart. Turning the flag off
    and building again gives back the old board exactly -- which is what "back it out" has to
    mean for a rule that runs once, at world creation, and leaves no trace of itself.

    An unrecognised value reads as the **default**, for the reason it used to read as ``off``:
    a typo must not silently re-plumb every world in the project. That principle used to point
    at ``off`` because ``off`` was what every recorded fingerprint had been taken at; now that
    the shipped board is ``large``, pointing it at ``off`` would mean `ATLAS_TRADE_SPREAD=lrage`
    quietly building greedy worlds against digests taken on the spread board -- the same
    hazard, aimed the other way. A misspelling now yields the board you would have got by not
    setting the variable at all, which is what "does not silently re-plumb" has to mean.

    Read once at import, like the pricing and refusal flags, for the reason those give.
    :func:`set_route_spread` is how a test or a harness moves it.
    """
    raw = os.environ.get("ATLAS_TRADE_SPREAD", _SPREAD_ON).strip().lower()
    return _SPREAD_ALIASES.get(raw, _SPREAD_ON)


_ROUTE_SPREAD: str = _spread_mode_from_env()


def route_spread_mode() -> str:
    """Which matching rule a new world is plumbed with. See :func:`_open_seed_trade`."""
    return _ROUTE_SPREAD


def route_spread_enabled() -> bool:
    """Whether that rule is anything other than the greedy one."""
    return _ROUTE_SPREAD != SPREAD_OFF


def set_route_spread(value: bool | str) -> str:
    """Move the flag, returning the mode it was on.

    Takes a bool or a mode, and **returns a mode either way** -- so the
    ``was = set_route_spread(x) ... set_route_spread(was)`` pattern restores ``large``
    rather than flattening it to ``all``, which a bool round-trip would do silently.
    """
    global _ROUTE_SPREAD
    if isinstance(value, bool):
        mode = _SPREAD_FROM_BOOL[value]
    else:
        mode = str(value).strip().lower()
        if mode not in _SPREAD_MODES:
            raise ValueError(f"unknown trade-spread mode {value!r}; expected one of "
                             f"{', '.join(_SPREAD_MODES)}")
    was, _ROUTE_SPREAD = _ROUTE_SPREAD, mode
    return was


#: How many suppliers one buyer's deficit in one good may be split across, when
#: :func:`route_spread_enabled`.
#:
#: **Two, because two is what was measured**, and nothing here is calibration. The costing
#: in `scratchpad/TRADE_CONCENTRATION_OPTIONS.md` ran this rule at 2, 3, 4 and uncapped
#: against all three seeded eras: two halves the top exporter's share of world trade income
#: (65% -> 46%, 56% -> 29%, 45% -> 34%) while keeping 97-101% of that income and doubling
#: the route table. Three and four spread further and cost proportionally more routes; the
#: uncapped rule spreads furthest and starts *destroying* trade income, because it slices
#: volume past the point where a route is worth having.
#:
#: The route table is what the pricing layer walks a sea for once a turn, so this number is
#: a performance constant as much as a balance one. Raising it is a measurement, not an
#: edit.
SUPPLIERS_PER_BUYER = 2

#: Which buyers ``large`` splits, as a multiple of the median deficit for that good in that
#: world. A buyer short of at least this much gets two suppliers; everybody else keeps one.
#:
#: **Read off the demand curve rather than chosen.** `scratchpad/deficit_shape.py` dumps the
#: shape of every era's import demand, and it is sharply concentrated: at 1.5x the median,
#: 14-39% of buyers carry 41-88% of the world's deficit volume, in every era and every good.
#: That is the trade this mode exists to make -- take the volume, leave the buyers.
#:
#: **1.5 sits on a plateau, which is why it is a safe default rather than a tuned one.**
#: Between 1.5x and 3x the selected set barely moves (36 -> 34 -> 29 buyers for cold-war
#: oil; 30 -> 29 -> 27 for modern-day oil), so the rule is not balanced on a cliff and a
#: pack edit that shifts the curve slightly will not swing the board. Below 1.5 it falls off
#: one: at 1.0x the threshold *is* the median and essentially every buyer qualifies, because
#: `trade._MIN_DEMAND` puts a dense cluster of minor nations at exactly the same deficit.
#:
#: A multiple of the world's own median rather than an absolute tonnage, so the rule scales
#: with whatever `atlas.scenarios.resources` says and does not have to be re-derived when an
#: endowment is edited.
SPREAD_DEFICIT_MULTIPLE = 1.5


def _open_seed_trade(conn, world_id: str, living: set[str]) -> int:
    """Plumb the world's trade at world creation, from the endowments themselves.

    **Derived rather than tabulated, and that is the point.** A hand-written list of who
    sells oil to whom would be a second table to keep in step with the first, and would go
    stale the moment somebody edited an endowment. This reads the endowments, works out who
    has a surplus and who has a deficit, and matches them largest-first -- so editing
    `atlas.scenarios.resources` re-plumbs world trade automatically and the two can never
    disagree.

    A world has to *begin* working. Without this the importers start cut off from goods
    that exist in abundance elsewhere, which is not a neutral starting position but a
    permanent embargo nobody declared -- and it would make the blockade lever meaningless,
    because you cannot cut a route that was never open.

    **The matching rule is the single largest determinant of trade concentration in the
    game, and there are now two of them.** Once cargo was paid for, the greedy rule below
    turned out to be why one power takes 49-71% of every unit of trade income in the world:
    measured on turn one of all three eras, the top exporter's realized *price* is the
    world's mean price to three decimals, so its income is its route count and its volume,
    and both are handed to it right here. See `scratchpad/TRADE_CONCENTRATION_OPTIONS.md`.

    ``greedy`` (the default, and what every world before this was built with)
        Largest seller to largest buyer, walking down both lists. Concentrating by design,
        with a stated reason: the biggest exporters carry the most trade, which is what
        makes them worth blockading.

    ``spread`` (:func:`route_spread_enabled`, default off)
        Each buyer's deficit filled from its largest :data:`SUPPLIERS_PER_BUYER` sellers,
        in proportion to their surplus. Halves the top exporter's share at 97-101% of the
        world's trade income and about double the routes.

    **The two are not equivalent and the difference is not only concentration.** A buyer
    whose top two suppliers cannot between them cover its deficit goes partly unsupplied
    under ``spread``, where ``greedy`` would have walked on down the list to a third and a
    fourth -- that is where the missing few percent of world trade income lives, and it is
    kept rather than patched because it is what was measured.

    **It does not, however, move the price**, which is worth recording because the opposite
    is the obvious guess. :func:`atlas.engine.pricing._alternatives` counts the powers that
    hold a *surplus* and can reach the buyer -- it reads the endowments, not the route
    table -- so giving a buyer a second route does not tell the squeeze term anything it did
    not already know. Measured on all three eras, the median seeded price is identical to
    four decimals under both rules (`scratchpad/spread_measure.py`). What moves is which
    seller collects it.

    **A world built either way is indistinguishable to everything downstream** -- no column
    records which rule ran, and rebuilding with the flag down reproduces the old board
    exactly. What it is *not* is free: the seeded route table is what the pricing layer
    walks a sea for every turn, and every pinned world fingerprint and every recorded
    cassette was taken against the greedy board.
    """
    opened = 0
    for good in GOODS:
        surplus: list[tuple[float, str]] = []
        deficit: list[tuple[float, str]] = []
        for nation_id in sorted(living):
            # A country nothing can reach cannot be sold to. See `trade.is_unreachable`:
            # 35 island provinces carry no adjacency edges at all, and opening routes into
            # them would be opening routes that are cut the instant they are used.
            if is_unreachable(conn, world_id, nation_id):
                continue
            row = conn.execute(
                "SELECT produced FROM resource"
                " WHERE world_id = ? AND nation_id = ? AND good = ?",
                (world_id, nation_id, good),
            ).fetchone()
            if row is None:
                continue
            produced = float(row["produced"])
            need = demand_of(conn, nation_id)
            if produced > need:
                surplus.append((produced - need, nation_id))
            elif need > produced:
                deficit.append((need - produced, nation_id))
        surplus.sort(reverse=True)
        deficit.sort(reverse=True)

        mode = route_spread_mode()
        if mode == SPREAD_OFF:
            opened += _match_greedy(conn, world_id, good, surplus, deficit)
        else:
            opened += _match_spread(
                conn, world_id, good, surplus, deficit,
                threshold=_spread_threshold(deficit) if mode == SPREAD_LARGE else 0.0,
            )
    return opened


def _spread_threshold(deficit) -> float:
    """The deficit a buyer needs before ``large`` gives it a second supplier.

    The median of what everybody is short of, times :data:`SPREAD_DEFICIT_MULTIPLE`. Computed
    per good and per world from the same list the matcher is about to walk, so it needs no
    table, no constant in tonnes, and no maintenance when an endowment is edited.
    """
    if not deficit:
        return 0.0
    return statistics.median(want for want, _buyer in deficit) * SPREAD_DEFICIT_MULTIPLE


def _open_route(conn, world_id: str, good: str, seller: str, buyer: str,
                volume: float) -> None:
    """One seeded route. The id is ``(good, seller, buyer)`` and stays unique under both
    matching rules -- ``spread`` gives a buyer several sellers for one good, which is
    several different ids, not a collision."""
    conn.execute(
        "INSERT INTO trade_route"
        " (id, world_id, seller_id, buyer_id, good, volume, opened_turn)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (f"tr:{good}:{seller}:{buyer}", world_id, seller, buyer, good, round(volume, 2)),
    )


def _match_greedy(conn, world_id: str, good: str, surplus, deficit) -> int:
    """Largest seller to largest buyer, walking down both lists. A seller that runs out
    steps aside for the next one, so the biggest exporters carry the most trade -- which is
    both what happens and what makes them worth blockading.

    **This is the rule every world in the project was built with** and it is unchanged: it
    was lifted out of :func:`_open_seed_trade` without a line of it moving, so that the
    alternative could sit beside it rather than inside an `if`.
    """
    opened = 0
    si = 0
    for want, buyer in deficit:
        while want > 0.01 and si < len(surplus):
            have, seller = surplus[si]
            if have <= 0.01:
                si += 1
                continue
            volume = min(want, have)
            _open_route(conn, world_id, good, seller, buyer, volume)
            opened += 1
            surplus[si] = (have - volume, seller)
            want -= volume
    return opened


def _match_spread(conn, world_id: str, good: str, surplus, deficit,
                  *, threshold: float = 0.0) -> int:
    """Each buyer's deficit split proportionally across its largest few sellers.

    **"Reachable" is a claim this function does not make, and used to.** The only
    reachability test in the seeding path is :func:`atlas.engine.trade.is_unreachable`,
    applied by :func:`_open_seed_trade` to each country before it joins a surplus or
    deficit list -- a country that carries no adjacency edges at all cannot be sold to. No
    *pair* is walked: neither matcher asks :func:`atlas.engine.trade.route_is_open`, so a
    seeded route may run between two countries that have no lane between them today. That
    is deliberate and it is the settlement's job, not the seeder's -- a route blockaded, at
    war or otherwise cut is reported as cut on the turn it is settled, and reopens when the
    world does. Saying "reachable" here read as a promise the pool had already been
    filtered on, and the word is load-bearing elsewhere in this layer.

    ``threshold`` is the deficit at which a buyer earns a second supplier. **Zero is the
    ``all`` mode and is not a special case in the code**: every buyer is short of at least
    nothing, so the test passes for everybody and the function is exactly the rule that was
    measured. A positive threshold is ``large``, and a buyer under it takes the whole of its
    deficit from the single largest seller still holding any -- which is what the greedy rule
    would have done for it, so the strategically interesting object survives: a small country
    dependent on one supplier somebody else can sever.

    **Deterministic to the same standard the greedy rule is held to**, because a seeded
    board that differed between two builds of the same scenario would move a world
    fingerprint for no reason anybody could name. Sellers are chosen by surplus and then by
    id, so two powers holding identical surpluses cannot swap places; routes are written in
    seller-id order; and the remaining surplus is drawn down in that same order, so a
    seller that runs low affects the same later buyers every time.

    A buyer whose chosen sellers cannot cover its deficit is left partly unsupplied rather
    than walked on down the list -- see :func:`_open_seed_trade` for why that is kept.
    """
    opened = 0
    left = {nation: amount for amount, nation in surplus}
    for want, buyer in deficit:
        pool = {n: v for n, v in left.items() if v > 0.01 and n != buyer}
        # One supplier for a buyer below the threshold, the cap for a buyer above it.
        # `>=` so that a threshold of zero admits everybody, which is what makes `all` the
        # same code path rather than a second one.
        cap = SUPPLIERS_PER_BUYER if want >= threshold else 1
        # Biggest first, id to break a tie, then the cap. Sorting on the *current* remaining
        # surplus rather than the starting one, so a seller that has already been drawn down
        # by earlier buyers gives way to one that has not.
        pool = dict(sorted(pool.items(), key=lambda kv: (-kv[1], kv[0]))[:cap])
        total = sum(pool.values())
        if total <= 0:
            continue
        for seller, have in sorted(pool.items()):
            volume = min(want * have / total, have)
            if volume <= 0.01:
                continue
            _open_route(conn, world_id, good, seller, buyer, volume)
            opened += 1
            left[seller] = have - volume
    return opened


def apply_politics(conn, world_id: str, scenario: Scenario) -> dict[str, int]:
    """Lay the scenario's political state over a freshly created world.

    Runs after :func:`atlas.state.bootstrap.create_world`, which sets every pair to
    neutral. Relations, treaties and history are written here rather than in the seed
    because they describe the *world*, not any one nation, and because the historical
    events have to exist before commitments can cite them.
    """
    from ..state.apply import record_event
    from ..state.models import EventOrigin

    counts = {"relations": 0, "treaties": 0, "events": 0, "arsenals": 0, "doctrines": 0,
              "organisations": 0, "leaders": 0, "endowments": 0, "routes": 0}
    living = {row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ?", (world_id,)
    )}

    # History first: commitments cite it, and a diff that cannot name a cause is refused.
    for kind, actor, text, visibility in scenario.history:
        if actor not in living:
            continue
        record_event(
            conn,
            world_id=world_id,
            turn=0,
            date=scenario.start_date,
            kind=kind,
            origin=EventOrigin.ENGINE,
            actor_nation_id=actor,
            text=text,
            visibility=visibility,
        )
        counts["events"] += 1

    for source, target, score, treaty, at_war in scenario.relations:
        if source not in living or target not in living:
            continue
        conn.execute(
            """
            UPDATE relation SET score = ?, treaty_status = ?, at_war = ?
             WHERE world_id = ? AND nation_a = ? AND nation_b = ?
            """,
            (score, treaty.value, int(at_war), world_id, source, target),
        )
        counts["relations"] += 1

    # Who is armed. A row here is the difference between a nuclear power and a power whose
    # blurb says it is one, which is what Modern Day shipped with until schema 10.
    for power_id, (warheads, delivery) in scenario.arsenals.items():
        if power_id not in living:
            continue
        declared = scenario.deterrent_boats.get(power_id)
        conn.execute(
            "INSERT OR REPLACE INTO arsenal (world_id, nation_id, warheads, delivery,"
            "                                deterrent_boats)"
            " VALUES (?, ?, ?, ?, ?)",
            (world_id, power_id, int(warheads), str(delivery),
             None if declared is None else int(declared)),
        )
        counts["arsenals"] += 1

    # **What each power is built to do.** Direct SQL like everything else here, and for the
    # same reason: this is world *creation*, before there is a turn to undo. Every doctrine
    # written after this goes through `engine.doctrines.adopt`, which is priced and diffed.
    from ..engine.doctrines import seed_doctrines

    counts["doctrines"] = seed_doctrines(
        conn, world_id,
        {power: axes for power, axes in scenario.doctrines.items() if power in living})

    # International bodies. Each is a nation row with **no regions, no units and a null
    # capital** plus an `organisation` row, which is what distinguishes it from a country.
    #
    # A body also gets **no leader row**, which is deliberate rather than an oversight of
    # ordering: `create_world` seats a government in every nation on the seed roster, and
    # a body is created after it, so the Security Council has no head of government to
    # overthrow. That is the right answer -- `assess_successions` joins through `leader`,
    # so a body is naturally exempt from a coup without needing a special case for it.
    # The nation row is not decoration: it is what lets the body author events, hold a
    # directed relation to every power, and be addressed by a diff. See
    # :mod:`atlas.engine.organisation`.
    for org_id, name, kind, charter, members, permanent in scenario.organisations:
        present = [m for m in members if m in living]
        if not present:
            continue
        conn.execute(
            """
            INSERT INTO nation (id, world_id, name, colour, is_player, controller,
                                personality_blurb, standing_goals_json,
                                military_strength, economy, industry, stability,
                                legitimacy, treasury, manpower)
            VALUES (?, ?, ?, '#8892A6', 0, 'engine', ?, '[]', 0, 0, 0, 50, 50, 0, 0)
            """,
            (org_id, world_id, name, charter[:400]),
        )
        conn.execute(
            "INSERT INTO organisation (nation_id, world_id, kind, charter_text, authority)"
            " VALUES (?, ?, ?, ?, 50)",
            (org_id, world_id, kind, charter),
        )
        for member in present:
            conn.execute(
                "INSERT OR IGNORE INTO organisation_member"
                " (organisation_id, nation_id, is_permanent, joined_turn)"
                " VALUES (?, ?, ?, 0)",
                (org_id, member, int(member in permanent)),
            )
        # The body is created after `create_world` has already written every ordered pair
        # of the *original* roster, so its own relations do not exist yet. Written here,
        # in both directions, because "the world's regard for the Council" is the lever
        # a condemnation moves.
        conn.executemany(
            "INSERT OR IGNORE INTO relation (world_id, nation_a, nation_b, score)"
            " VALUES (?, ?, ?, 0)",
            [(world_id, a, b)
             for other in living
             for a, b in ((org_id, other), (other, org_id))
             if other != org_id],
        )
        counts["organisations"] = counts.get("organisations", 0) + 1

    # Who actually governs, where the scenario has something to say about it. Every
    # nation already carries a placeholder government from `create_world`; this replaces
    # the ones the era's table names, in place, so the row a coup will later stand down is
    # the same row throughout. See :mod:`atlas.scenarios.leaders`.
    for nation_id in sorted(living):
        government = leader_for(nation_id, era=scenario.era)
        if government is None:
            continue
        conn.execute(
            "UPDATE leader SET name = ?, title = ?, traits_json = ?, agenda = ?"
            " WHERE world_id = ? AND nation_id = ? AND is_current = 1",
            (government.name, government.title, json.dumps(list(government.traits)),
             government.agenda, world_id, nation_id),
        )
        counts["leaders"] = counts.get("leaders", 0) + 1

    # What each country digs out of the ground. Every nation already carries a neutral
    # endowment from `create_world`; this replaces it with the era's, in place.
    #
    # **The store is a multiple of what a country needs, not of what it digs.** The obvious
    # version -- months of its own production -- gives a country that produces no oil no
    # oil at all, and measured on the 2026 map that put Japan and Germany at the 35% floor
    # on turn one before a shot was fired. A world does not begin mid-crisis: these are
    # working economies with the stocks a working economy has, and running them down is
    # what a war is for.
    for nation_id in sorted(living):
        need = demand_of(conn, nation_id)
        for good, produced in endowment_for(nation_id, era=scenario.era).items():
            conn.execute(
                "UPDATE resource SET produced = ?, stockpile = ?"
                " WHERE world_id = ? AND nation_id = ? AND good = ?",
                (produced, round(need * _MONTHS_OF_STOCK, 2), world_id, nation_id, good),
            )
        counts["endowments"] = counts.get("endowments", 0) + 1

    counts["routes"] = _open_seed_trade(conn, world_id, living)

    for kind, status, condition, parties in scenario.treaties:
        present = [party for party in parties if party in living]
        if len(present) < 2:
            continue
        cursor = conn.execute(
            """
            INSERT INTO commitment (world_id, kind, status, condition_text, created_turn)
            VALUES (?, ?, ?, ?, 0)
            """,
            (world_id, kind.value, status, condition),
        )
        commitment_id = int(cursor.lastrowid)
        for party in present:
            conn.execute(
                "INSERT OR IGNORE INTO commitment_nation (commitment_id, nation_id) VALUES (?, ?)",
                (commitment_id, party),
            )
        counts["treaties"] += 1

    counts["dependencies"] = _disown_dependencies(conn, world_id, scenario)
    counts["occupied"] = _lay_the_line_of_control(conn, world_id, scenario)
    counts["reconciled"] = _reconcile_seeded_treaties(conn, world_id)
    conn.commit()
    return counts


def _lay_the_line_of_control(conn, world_id: str, scenario: Scenario) -> int:
    """Occupied ground: the sovereign keeps the region, somebody else holds it.

    Only ``controller_nation_id`` is written. The other two columns are *deliberately* left
    where `create_world` put them -- owner is the sovereign and home is the sovereign's core
    -- and that is the whole distinction between an occupation and a dependency. A colony
    has no core because it is nobody's country; occupied ground has one because it is
    somebody's, and they want it back.

    **A region the scenario does not name is not written**, so an era with no occupations is
    untouched and a typo is loud rather than silent: a region id that matches nothing is
    refused here rather than quietly doing nothing, which is the failure mode every
    territory-file override guard exists for.
    """
    if not scenario.occupied:
        return 0
    held = 0
    for region_id, controller in sorted(scenario.occupied.items()):
        row = conn.execute(
            "SELECT owner_nation_id FROM region WHERE world_id = ? AND id = ?",
            (world_id, region_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"{scenario.id}: occupied region {region_id!r} is not on the map"
            )
        if row["owner_nation_id"] == controller:
            raise ValueError(
                f"{scenario.id}: {region_id!r} is occupied by its own sovereign "
                f"{controller!r}, which is not an occupation"
            )
        conn.execute(
            "UPDATE region SET controller_nation_id = ? WHERE world_id = ? AND id = ?",
            (controller, world_id, region_id),
        )
        held += 1
    return held


def _disown_dependencies(conn, world_id: str, scenario: Scenario) -> int:
    """**A colony is not its owner's homeland**, and `create_world` says it is.

    Every region is seeded with ``home_nation_id = owner_nation_id``, which is right for a
    country's own ground and wrong for everything a power merely *holds*. Codex found what
    it costs: `nuclear.deterrence` applies only to the ground a power is *from*, and this
    made Hong Kong British homeland at 0.105, Djibouti French and the West Bank Israeli --
    so a nuclear power's colonies were nuclear-protected, which is the exact opposite of
    the rule the deterrence term was written to obey. The same column is read by conquest,
    logistics, unrest and settlement.

    A dependency's home is **nobody**. There is no nation for Hong Kong in 1972 and
    inventing one would be inventing a state, which is the thing the sovereignty map exists
    to stop. ``NULL`` says what is true: this ground is held, and it is not anybody's core.

    **Written here rather than in the seed, deliberately**, and for the reason the arsenals
    and the organisations are: a `RegionSeed` field would change the dataclass every era's
    seed digest is computed from, moving three fingerprints to express a fact about two.
    Laid over a created world instead, only the built-world digest moves, and only for an
    era that actually has dependencies.

    Two kinds of held ground, both from the scenario's own tables. A **dependency** is a
    modern code the sovereignty map gave to somebody other than itself. A **colony** is the
    ground `Scenario.colonies` names -- 1936's Eritrea and Italian Somaliland, and 1972's
    West Berlin -- which is the same relationship drawn at province granularity.
    """
    dependencies: set[str] = set()
    for holder, _name, codes in scenario.sovereignty:
        # **A composite is its constituents.** Declared rather than inferred: see
        # `Scenario.composites`. Without this, a minor whose own code is absent from its
        # own composition disowns the whole of itself.
        if holder in scenario.composites:
            continue
        for code in codes:
            # A minor holding its *own* code is a country, not a dependency: the entry is
            # there to compose it or to rename it.
            if holder != f"m_{code.lower()}":
                dependencies.add(f"r_{code.lower()}")

    patterns = [f"r_{minor_id}_%" for minor_id in sorted(scenario.colonies)]
    disowned = 0
    for region_id in sorted(dependencies):
        disowned += conn.execute(
            "UPDATE region SET home_nation_id = NULL WHERE world_id = ? AND id = ?"
            "   AND home_nation_id IS NOT NULL",
            (world_id, region_id),
        ).rowcount
    for pattern in patterns:
        disowned += conn.execute(
            "UPDATE region SET home_nation_id = NULL WHERE world_id = ? AND id LIKE ?"
            "   AND home_nation_id IS NOT NULL",
            (world_id, pattern),
        ).rowcount
    return disowned


def _reconcile_seeded_treaties(conn, world_id: str) -> int:
    """Raise `relation.treaty_status` to what the seeded commitments actually imply.

    **Found by the invariant checker on its first outing**, which is what it is for: 1936
    seeded an active Anglo-German trade agreement and left `treaty_status` reading `none`,
    so an agreement that exists in the commitment table was invisible to the seven consumers
    that read the column -- the invasion guard, the gambit odds, the router and four
    briefings. The scenario and the paperwork beside it were written by hand and nothing
    ever compared them.

    **Upward only, and that is the whole design.** A scenario is authoritative about the
    world it describes: Modern Day asserts NATO as an `alliance` with no commitment row
    behind it, deliberately, and recomputing the column from the commitments would quietly
    demote 52 relationships to `none` and delete most of the map's diplomacy. So this only
    ever *raises* a status the commitments justify and never lowers one the scenario
    asserted. What it cannot fix is the reverse gap -- an alliance with no agreement behind
    it cannot be betrayed, because there is nothing to break -- and that is a scenario change
    with balance consequences rather than a reconciliation.

    Runs after the commitments are written and before the world is handed over, so a played
    turn starts from a consistent position. **Moves no seed fingerprint**: relations and
    commitments are laid over a created world by this function rather than carried in
    `WorldSeed`, which the digests cover.
    """
    from ..engine.diplomacy import implied_treaty_status

    order = ("none", "trade", "non_aggression", "defensive", "alliance")
    raised = 0
    for row in conn.execute(
        "SELECT nation_a, nation_b, treaty_status FROM relation WHERE world_id = ?"
        " ORDER BY nation_a, nation_b",
        (world_id,),
    ).fetchall():
        held = str(row["treaty_status"])
        if held == "vassal":
            continue
        implied = implied_treaty_status(
            conn, world_id, str(row["nation_a"]), str(row["nation_b"])
        )
        if implied == held or implied not in order or held not in order:
            continue
        if order.index(implied) <= order.index(held):
            continue
        conn.execute(
            "UPDATE relation SET treaty_status = ? WHERE world_id = ?"
            "   AND nation_a = ? AND nation_b = ?",
            (implied, world_id, row["nation_a"], row["nation_b"]),
        )
        raised += 1
    return raised
