"""Reading world state into the pure input for rendering.

This is the only part of the map subsystem that touches the database. Everything
downstream is a function of :class:`MapViewState`, which means the renderer can be tested
with a hand-written state and no SQLite at all.

Two queries here carry real weight:

``changed_region_ids``
    Joins ``diff`` to ``event`` through ``reason_event_id`` and then filters by what the
    observer can see. An uncited diff therefore *structurally cannot* light up a region,
    which gives the mandatory-citation rule a visible consumer rather than leaving it as
    a check nothing depends on. It also means the player is not shown changes in
    territory they have no knowledge of.

``known_region_ids``
    Fog of war, computed rather than trusted. A region is known if the observer holds it,
    occupies it, borders it, or has forces standing in it -- knowledge from presence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

__all__ = ["MapViewState", "NationStyle", "RegionState", "CityState", "UnitState", "load_map_view"]


@dataclass(frozen=True, slots=True)
class NationStyle:
    nation_id: str
    name: str
    colour: str
    is_player: bool = False
    is_tracked: bool = False
    capital_region_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegionState:
    region_id: str
    #: The region's primary shape: where its label sits, and the whole of it for any
    #: region drawn from a single pack entry.
    geo_key: str
    name: str
    kind: str
    terrain: str
    owner_nation_id: str | None
    controller_nation_id: str | None
    population: float = 0.0
    development: float = 0.0
    #: Every shape the region is drawn from. A promoted province region groups several,
    #: and drawing only the first left the rest of it as ocean.
    geo_keys: tuple[str, ...] = ()

    @property
    def shapes(self) -> tuple[str, ...]:
        return self.geo_keys or (self.geo_key,)


@dataclass(frozen=True, slots=True)
class CityState:
    city_id: str
    region_id: str
    name: str
    tier: int
    is_capital: bool = False


@dataclass(frozen=True, slots=True)
class UnitState:
    unit_id: str
    region_id: str
    nation_id: str
    kind: str
    strength: float
    #: Read from unit_template, the same classification used by movement and combat.
    domain: str = "land"


@dataclass(frozen=True, slots=True)
class MapViewState:
    """Everything the renderer needs, and nothing it does not."""

    world_id: str
    turn: int
    observer_nation_id: str | None
    regions: tuple[RegionState, ...] = ()
    nations: dict[str, NationStyle] = field(default_factory=dict)
    cities: tuple[CityState, ...] = ()
    units: tuple[UnitState, ...] = ()
    #: Regions whose ownership changed on the turn just resolved *and* which the observer
    #: is entitled to know about.
    changed_region_ids: frozenset[str] = frozenset()
    #: Regions the observer has any knowledge of. Everything else renders as fog.
    known_region_ids: frozenset[str] = frozenset()
    #: Ground somebody is pushing on, as ``region_id -> (attacker, share of the way to
    #: falling)``. The furthest-advanced attacker where several are pressing the same
    #: ground: it is the one about to take it.
    contested: dict[str, tuple[str, float]] = field(default_factory=dict)
    #: Held foreign ground and what it costs to hold, as ``region_id -> residual``. The
    #: residual is what is left *after* the garrison standing on it, so a province with
    #: troops on it reads as quiet -- which is the true picture, not a softened one.
    unrest: dict[str, float] = field(default_factory=dict)
    #: Who holds the sky, as ``region_id -> (nation_id, margin 0..1)``. Absent where
    #: nobody has ever flown, which is most of the map and every world before schema 12.
    air_control: dict[str, tuple[str, float]] = field(default_factory=dict)
    #: **The observer's own supply reach**, as ``region_id -> how far supply travelled to
    #: get there``. Empty for an omniscient view, because "whose supply" has no answer
    #: without an observer -- and a supply map of everybody at once would be a picture of
    #: nothing. Absent from a region means supply does not reach it at all.
    supply: dict[str, int] = field(default_factory=dict)
    #: **Which regions the observer could actually throw a warhead at.** ``None`` means
    #: everywhere -- an intercontinental deterrent is not limited to a set of provinces --
    #: and an empty set means the observer has no arsenal, or none that reaches anything.
    nuclear_reach: frozenset[str] | None = frozenset()
    #: **What the observer's services believe would answer a strike**, as ``nation_id ->
    #: (low, high, confidence)`` surviving warheads. An estimate, and deliberately so: the
    #: intelligence layer makes a warhead count the foggiest figure on the map and lets a
    #: rival with strong counterintelligence choose what this nation believes about it. A
    #: player's nuclear-risk picture is exactly as good, or as deceived, as its service.
    nuclear_answer: dict[str, tuple[float, float, str]] = field(default_factory=dict)
    #: **What the observer could throw, which it knows exactly** -- they are its own
    #: warheads. The one true figure in this group, and the reason the layer can read as a
    #: *balance* rather than as a league table of other people's arsenals.
    nuclear_mine: float = 0.0
    #: **How each power regards the observer**, as ``nation_id -> (score, treaty, at war)``.
    #: One direction only, and it is the honest one: whether a government is being received
    #: warmly is a thing its ambassadors can report, and there is no fog over it. What they
    #: think of somebody *else* is not here and is not this layer's business.
    attitude: dict[str, tuple[float, str, bool]] = field(default_factory=dict)
    #: **How far each country's research has improved its formations**, as ``nation_id ->
    #: {unit kind: mark}``. Only marks above Mk I are listed; the counters show them.
    marks: dict[str, dict[str, int]] = field(default_factory=dict)
    #: **The ground the observer's wars are about**, as ``region_id -> ((claimant, kind,
    #: enemy), ...)``. Only wars the observer is in: its own aims and the aims declared
    #: against it. War aims are public -- a demand nobody has heard cannot be met, refused
    #: or negotiated down -- but a map of every belligerent's claims on a world at war
    #: would be a picture of nothing, so the scope is the observer's own quarrels.
    war_aims: dict[str, tuple[tuple[str, str, str], ...]] = field(default_factory=dict)
    #: **What moves between the observer and each other power**, as ``nation_id -> (volume,
    #: (good, ...), sells_to_me)``. Only routes the observer is on: a standing agreement to
    #: sell is a thing it signed, and what two other powers ship between themselves is not
    #: on its books. Empty without an observer, for the reason `supply` is.
    trade: dict[str, tuple[float, tuple[str, ...], bool]] = field(default_factory=dict)
    #: **What the observer has promised each power and been promised by it**, as
    #: ``nation_id -> ((kind, ...), weight)``. Only agreements the observer is a party to:
    #: a government knows what it signed, and a pact between two others is intelligence
    #: rather than a fact on its map.
    obligations: dict[str, tuple[tuple[str, ...], float]] = field(default_factory=dict)
    #: **How far each held province has got toward leaving**, as ``region_id -> momentum``.
    #: Every holder's, not only the observer's -- a colony on its way out demonstrates, and
    #: `unrest` already reads that kind of visible restiveness for the whole map.
    independence: dict[str, float] = field(default_factory=dict)

    def region(self, region_id: str) -> RegionState | None:
        for candidate in self.regions:
            if candidate.region_id == region_id:
                return candidate
        return None


def _nations(conn: sqlite3.Connection, world_id: str) -> dict[str, NationStyle]:
    rows = conn.execute(
        "SELECT id, name, colour, is_player, is_tracked, capital_region_id "
        "FROM nation WHERE world_id = ?", (world_id,)
    ).fetchall()
    return {
        row["id"]: NationStyle(
            nation_id=row["id"],
            name=row["name"],
            colour=row["colour"],
            is_player=bool(row["is_player"]),
            is_tracked=bool(row["is_tracked"]),
            capital_region_id=row["capital_region_id"],
        )
        for row in rows
    }


def _changed_regions(
    conn: sqlite3.Connection, world_id: str, turn: int, observer: str | None
) -> frozenset[str]:
    """Regions whose owner changed this turn, restricted to what the observer may see.

    The INNER JOIN on ``event`` is the point: a diff with no resolvable
    ``reason_event_id`` cannot appear here at all.
    """
    sql = """
        SELECT DISTINCT d.target_id
          FROM diff d
          JOIN diff_batch b ON b.id = d.batch_id
          JOIN event e      ON e.id = d.reason_event_id
         WHERE b.world_id = ?
           AND b.turn = ?
           AND d.status = 'applied'
           AND d.target_table = 'region'
           AND d.field = 'owner_nation_id'
    """
    params: list = [world_id, turn]

    if observer is not None:
        # Visibility of the *cause* is the whole test. Rule INFO-002 already guarantees a
        # diff is never more widely observable than the event that produced it, so an
        # observer entitled to see the cause is entitled to see the effect. Filtering on
        # the diff's own scope as well would be redundant in the safe direction and wrong
        # in the other: it would hide a secret change from the very nations that
        # negotiated it.
        sql += """
           AND (
                e.visibility = 'public'
             OR EXISTS (SELECT 1 FROM event_visibility v
                         WHERE v.event_id = e.id AND v.nation_id = ?)
           )
        """
        params.append(observer)

    return frozenset(row["target_id"] for row in conn.execute(sql, tuple(params)))


def known_regions(
    conn: sqlite3.Connection, world_id: str, observer: str | None
) -> frozenset[str]:
    """Public name for :func:`_known_regions`, so anything else that has to respect the fog
    can ask the same function rather than write the predicate again.

    The search box is the first caller. A search that could find a province the fog is
    hiding would be a hole in the fog with a text box in front of it, and the only reliable
    way to keep two knowledge rules identical is to have one of them.
    """
    return _known_regions(conn, world_id, observer)


def _known_regions(
    conn: sqlite3.Connection, world_id: str, observer: str | None
) -> frozenset[str]:
    """What the observer knows about. A spectator with no nation knows everything.

    Knowledge comes from presence: what you hold, what you occupy, what borders it, and
    where your own forces are standing. There used to be a fourth source -- any region
    owned by a nation whose public event you had seen -- and it collapsed the fog
    entirely. One communique from the Soviet Union revealed eleven regions from Belarus
    to Kamchatka, permanently, to everybody. Within a turn or two of any world where
    anyone did anything, nothing was hidden from anyone.

    Events cannot restore that reveal precisely, because the event table records who
    acted and not where, so tying it to a place would be guesswork. Presence is the
    honest model, and it is the one the fog layer already draws.
    """
    if observer is None:
        rows = conn.execute("SELECT id FROM region WHERE world_id = ?", (world_id,))
        return frozenset(row["id"] for row in rows)

    rows = conn.execute(
        """
        -- Held or occupied.
        SELECT id FROM region
         WHERE world_id = ? AND (owner_nation_id = ? OR controller_nation_id = ?)
        UNION
        -- Bordering something held.
        SELECT CASE WHEN a.region_a = r.id THEN a.region_b ELSE a.region_a END
          FROM region_adjacency a
          JOIN region r ON (r.id = a.region_a OR r.id = a.region_b)
         WHERE r.world_id = ? AND (r.owner_nation_id = ? OR r.controller_nation_id = ?)
        UNION
        -- Where the observer's own forces are standing.
        SELECT u.region_id FROM unit u WHERE u.world_id = ? AND u.nation_id = ?
        """,
        (world_id, observer, observer, world_id, observer, observer, world_id, observer),
    )
    return frozenset(row[0] for row in rows if row[0])


def _unrest(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """What each restive province costs its holder, after the garrison on it.

    Read through `unrest.held_foreign_provinces` rather than recomputed, so the map and
    the stability charge can never quote different numbers for the same ground.
    """
    from ..engine.unrest import held_foreign_provinces

    out: dict[str, float] = {}
    for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,),
    ).fetchall():
        for province in held_foreign_provinces(conn, world_id, str(row["id"])):
            residual = float(province.get("residual") or 0.0)
            if residual > 0:
                out[str(province["region_id"])] = residual
    return out


def _air_control(
    conn: sqlite3.Connection, world_id: str
) -> dict[str, tuple[str, float]]:
    """Who holds each sky. Empty on any world where no aircraft have ever met."""
    try:
        rows = conn.execute(
            "SELECT region_id, holder_nation_id, margin FROM air_control"
            " WHERE world_id = ? AND holder_nation_id IS NOT NULL AND margin > 0"
            " ORDER BY region_id",
            (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # A world written before schema 12 has no such table, and a map that refused to
        # draw it would be a map that refused to open an old save.
        return {}
    return {
        str(r["region_id"]): (str(r["holder_nation_id"]), float(r["margin"]))
        for r in rows
    }


def _contested(conn: sqlite3.Connection, world_id: str) -> dict[str, tuple[str, float]]:
    """Where a front is standing, and how far along it is.

    Not filtered by the observer. A siege is an army in the field on ground you can see;
    unlike the *cause* of a diff, there is nothing secret about it, and hiding it would
    show the player a quiet border with a war on it.
    """
    found: dict[str, tuple[str, float]] = {}
    for row in conn.execute(
        "SELECT region_id, attacker_id, pressure FROM contest WHERE world_id = ?"
        " ORDER BY region_id, attacker_id",
        (world_id,),
    ):
        # A share, as documented on `MapViewState.contested`: the outline width is drawn
        # from it, and an out-of-range value smeared a province into a blob.
        pressure = min(1.0, max(0.0, float(row["pressure"])))
        current = found.get(row["region_id"])
        if current is None or pressure > current[1]:
            found[row["region_id"]] = (row["attacker_id"], pressure)
    return found


def _supply(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> dict[str, int]:
    """How far the observer can push supply, or nothing at all without an observer.

    **Only ever the observer's own**, which is both the honest scope and the only useful
    one: supply reach is not observable about somebody else, and a map of everybody's at
    once would be a picture of nothing.
    """
    if not observer_nation_id:
        return {}
    from ..engine.logistics import supply_map

    return supply_map(conn, world_id, observer_nation_id)


def _nuclear(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> tuple[frozenset[str] | None, dict[str, tuple[float, float, str]], float]:
    """The observer's own reach, and what it believes would answer from inside it.

    **Only ever the observer's, and only ever its beliefs.** Reach is not a property of the
    ground -- it is a property of who is asking and what they fly it on -- and the answering
    counts are estimates from `estimated_retaliation`, so nothing omniscient about anybody
    else's stockpile reaches the screen.

    *Who* holds warheads is read from the truth, and that is the same line the nuclear
    briefing draws: which powers are nuclear is announced, tested for and inspected; how
    many they hold is not. So the list of possible answerers is real and every number
    against it is a guess.
    """
    if not observer_nation_id:
        return frozenset(), {}, 0.0
    from ..engine.nuclear import (
        arsenal_of,
        deliverable_regions,
        estimated_retaliation,
        surviving_warheads,
    )

    mine = arsenal_of(conn, world_id, observer_nation_id)
    if mine is None or not mine.armed:
        return frozenset(), {}, 0.0

    reach = deliverable_regions(
        conn, world_id, launcher=observer_nation_id, delivery=mine.delivery)
    answer = {}
    for row in conn.execute(
        "SELECT a.nation_id FROM arsenal a JOIN nation n ON n.id = a.nation_id"
        " WHERE a.world_id = ? AND a.warheads > 0 AND n.is_alive = 1 AND a.nation_id <> ?"
        " ORDER BY a.nation_id", (world_id, observer_nation_id)
    ):
        other = str(row["nation_id"])
        band = estimated_retaliation(
            conn, world_id, viewer=observer_nation_id, target=other)
        answer[other] = (band.low, band.high, band.confidence)
    return reach, answer, float(surviving_warheads(conn, world_id, observer_nation_id))


def _attitude(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> dict[str, tuple[float, str, bool]]:
    """What everybody else thinks of the observer. Empty without one.

    ``relation`` is directional -- ``nation_a``'s view of ``nation_b`` -- and this reads
    exactly the half pointed at the observer. Reading the other half would be a map of what
    the player thinks of everybody, which the player already knows, and averaging the two
    would invent a number neither government holds.
    """
    if not observer_nation_id:
        return {}
    return {
        str(row["nation_a"]): (float(row["score"] or 0.0),
                               str(row["treaty_status"] or "none"),
                               bool(row["at_war"]))
        for row in conn.execute(
            "SELECT r.nation_a, r.score, r.treaty_status, r.at_war FROM relation r"
            "  JOIN nation n ON n.id = r.nation_a"
            " WHERE r.world_id = ? AND r.nation_b = ? AND n.is_alive = 1",
            (world_id, observer_nation_id))
    }


def _war_aims(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> dict[str, tuple[tuple[str, str, str], ...]]:
    """Where the observer's wars are being fought for, both directions.

    Both directions on purpose. A war aim is a demand, and a demand you have not been told
    about cannot be conceded -- so what has been declared *against* the observer belongs on
    its map exactly as much as what it declared itself, and the two are told apart by the
    tooltip rather than by being on separate screens.
    """
    if not observer_nation_id:
        return {}
    from ..engine.waraims import aims_against, aims_of, implied_regions

    claims: dict[str, list[tuple[str, str, str]]] = {}
    declared = [
        aim
        for group in (aims_of(conn, world_id, observer_nation_id),
                      aims_against(conn, world_id, observer_nation_id))
        for aims in group.values()
        for aim in aims
    ]
    for aim in declared:
        for region_id in implied_regions(conn, world_id, aim):
            claims.setdefault(region_id, []).append(
                (aim.nation_id, aim.kind, aim.enemy_id))
    return {region_id: tuple(found) for region_id, found in claims.items()}


def _trade(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> dict[str, tuple[float, tuple[str, ...], bool]]:
    """What actually moves between the observer and everybody else.

    **The observer's own routes and nobody else's.** A trade route is a standing agreement
    to sell, and the two parties to one are the two who know it exists; what two other
    powers ship between themselves is a fact about their books rather than this map's. Same
    scope rule as `_supply` and `_obligations`, and the same reason.

    Both directions in one number, because the thing a strategic-goods map is for is
    *dependency*: whether the oil is coming in or going out changes what you do about it,
    and that is what the flag and the tooltip are for -- but either way, a route that ends
    is a route that hurts somebody.
    """
    if not observer_nation_id:
        return {}
    found: dict[str, tuple[float, set[str], bool]] = {}
    for row in conn.execute(
        "SELECT seller_id, buyer_id, good, volume FROM trade_route"
        " WHERE world_id = ? AND (seller_id = ? OR buyer_id = ?) ORDER BY id",
        (world_id, observer_nation_id, observer_nation_id),
    ):
        selling = str(row["seller_id"]) == observer_nation_id
        other = str(row["buyer_id"] if selling else row["seller_id"])
        volume, goods, inbound = found.get(other, (0.0, set(), False))
        goods.add(str(row["good"]))
        found[other] = (volume + float(row["volume"] or 0.0), goods,
                        inbound or not selling)
    return {
        nation_id: (volume, tuple(sorted(goods)), inbound)
        for nation_id, (volume, goods, inbound) in found.items()
    }


#: What each kind of agreement is worth on this layer, by what it would actually cost to
#: honour. **Ordered by obligation and not by warmth**: a defensive pact can put your army
#: in the field and a trade agreement cannot, whatever the two governments feel about each
#: other. A kind absent here is an agreement with no standing cost -- it still shows in the
#: tooltip, and it does not move the colour.
OBLIGATION_WEIGHT: dict[str, float] = {
    "alliance": 1.0,
    "defensive_pact": 0.9,
    "military_access": 0.6,
    "peace_treaty": 0.45,
    "non_aggression_pact": 0.4,
    "ceasefire": 0.35,
    "tribute": 0.3,
    "trade_agreement": 0.25,
    "territory_cession": 0.2,
}


def _obligations(
    conn: sqlite3.Connection, world_id: str, observer_nation_id: str | None
) -> dict[str, tuple[tuple[str, ...], float]]:
    """What the observer has promised each power, and been promised by it.

    **Only agreements the observer is a party to.** A government knows what it signed; a
    pact between two other powers is something its services may have heard about, which is
    the intelligence layer's business and not a fact this map is entitled to draw.

    Weighted by what honouring it would cost rather than by how friendly it reads -- see
    :data:`OBLIGATION_WEIGHT`. Only ``active`` agreements: a pending one has not been
    accepted by everybody and a broken one is the opposite of an obligation.
    """
    if not observer_nation_id:
        return {}
    found: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT c.kind, other.nation_id AS other FROM commitment c"
        "  JOIN commitment_nation mine ON mine.commitment_id = c.id"
        "  JOIN commitment_nation other ON other.commitment_id = c.id"
        " WHERE c.world_id = ? AND c.status = 'active' AND mine.nation_id = ?"
        "   AND other.nation_id <> ? ORDER BY c.id",
        (world_id, observer_nation_id, observer_nation_id),
    ):
        found.setdefault(str(row["other"]), set()).add(str(row["kind"]))
    return {
        nation_id: (tuple(sorted(kinds)),
                    max(OBLIGATION_WEIGHT.get(kind, 0.0) for kind in kinds))
        for nation_id, kinds in found.items()
    }


def _independence(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """How far each held province has got toward leaving.

    Every holder's, not only the observer's. A colony on its way out holds meetings and
    stops paying its taxes, which is the same kind of visible restiveness `_unrest` already
    reads for the whole map -- and scoping this one to the observer would make the layer
    blank for every player without an empire, which is not a more honest answer, only a
    less useful one.
    """
    try:
        rows = conn.execute(
            "SELECT region_id, momentum FROM independence"
            " WHERE world_id = ? AND momentum > 0 ORDER BY region_id", (world_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        # A world written before the table existed, and a map that refused to draw it
        # would be a map that refused to open an old save. Same as `_air_control`.
        return {}
    return {str(row["region_id"]): float(row["momentum"]) for row in rows}


def _marks(conn: sqlite3.Connection, world_id: str) -> dict[str, dict[str, int]]:
    try:
        from ..engine.research import all_marks

        return all_marks(conn, world_id)
    except sqlite3.OperationalError:  # a world from before research had a table
        return {}


def load_map_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int | None = None,
    observer_nation_id: str | None = None,
) -> MapViewState:
    """Build the renderer's input from the database.

    ``observer_nation_id`` of ``None`` means an omniscient view -- used by the map editor
    and by EVE spectating. In an ordinary game it is the player's nation, and the fog of
    war applies.
    """
    if turn is None:
        row = conn.execute("SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
        turn = int(row["turn_number"]) if row else 0

    # The composition, read once for the whole world rather than per region: a promoted
    # scenario has a few thousand rows here and a query each would be a few thousand
    # round trips through the parser on every frame.
    composition: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT region_id, geo_key FROM region_geometry WHERE world_id = ?"
        " ORDER BY region_id, geo_key",
        (world_id,),
    ):
        composition.setdefault(row["region_id"], []).append(row["geo_key"])

    regions = tuple(
        RegionState(
            region_id=row["id"],
            geo_key=row["geo_key"],
            name=row["name"],
            kind=row["kind"],
            terrain=row["terrain"],
            owner_nation_id=row["owner_nation_id"],
            controller_nation_id=row["controller_nation_id"],
            population=row["population"],
            development=row["development"],
            geo_keys=tuple(composition.get(row["id"]) or ()),
        )
        for row in conn.execute(
            "SELECT id, geo_key, name, kind, terrain, owner_nation_id, controller_nation_id,"
            " population, development FROM region WHERE world_id = ? ORDER BY id",
            (world_id,),
        )
    )

    capitals = {
        row["capital_region_id"]
        for row in conn.execute(
            "SELECT capital_region_id FROM nation WHERE world_id = ? AND capital_region_id IS NOT NULL",
            (world_id,),
        )
    }

    cities = tuple(
        CityState(
            city_id=row["id"],
            region_id=row["region_id"],
            name=row["name"],
            tier=row["tier"],
            is_capital=row["region_id"] in capitals,
        )
        for row in conn.execute(
            "SELECT id, region_id, name, tier FROM city WHERE world_id = ? ORDER BY id",
            (world_id,),
        )
    )

    units = tuple(
        UnitState(
            unit_id=row["id"],
            region_id=row["region_id"],
            nation_id=row["nation_id"],
            kind=row["kind"],
            strength=row["strength"],
            domain=row["domain"],
        )
        for row in conn.execute(
            "SELECT u.id, u.region_id, u.nation_id, u.kind, u.strength, t.domain"
            " FROM unit u JOIN unit_template t"
            " ON t.world_id = u.world_id AND t.kind = u.kind"
            " WHERE u.world_id = ? ORDER BY u.id",
            (world_id,),
        )
    )

    nuclear_reach, nuclear_answer, nuclear_mine = _nuclear(
        conn, world_id, observer_nation_id)

    return MapViewState(
        world_id=world_id,
        turn=turn,
        observer_nation_id=observer_nation_id,
        regions=regions,
        nations=_nations(conn, world_id),
        cities=cities,
        units=units,
        changed_region_ids=_changed_regions(conn, world_id, turn, observer_nation_id),
        known_region_ids=_known_regions(conn, world_id, observer_nation_id),
        contested=_contested(conn, world_id),
        unrest=_unrest(conn, world_id),
        air_control=_air_control(conn, world_id),
        supply=_supply(conn, world_id, observer_nation_id),
        nuclear_reach=nuclear_reach,
        nuclear_answer=nuclear_answer,
        nuclear_mine=nuclear_mine,
        attitude=_attitude(conn, world_id, observer_nation_id),
        marks=_marks(conn, world_id),
        war_aims=_war_aims(conn, world_id, observer_nation_id),
        trade=_trade(conn, world_id, observer_nation_id),
        obligations=_obligations(conn, world_id, observer_nation_id),
        independence=_independence(conn, world_id),
    )
