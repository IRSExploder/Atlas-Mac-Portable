"""Creating a world from nothing, and fingerprinting one for comparison.

The GENESIS event is the piece that matters here. Every diff must cite an event that
caused it, but on turn 1 no events exist yet -- the mandatory-citation rule would be
unsatisfiable and the world could never take its first action. GENESIS is the engine-
authored root of the causal graph: "this world began". Everything else descends from it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b
from pathlib import Path

from ..engine import adjacency as adjacency_cache
from .apply import record_event
from .db import create_world_db
from .models import Difficulty, EventKind, EventOrigin, Visibility, WorldMode

__all__ = [
    "DEFAULT_UNIT_TEMPLATES",
    "CitySeed",
    "NationSeed",
    "RegionSeed",
    "UnitSeed",
    "UnitTemplateSeed",
    "WorldSeed",
    "create_world",
    "fingerprint",
]


@dataclass(frozen=True, slots=True)
class NationSeed:
    id: str
    name: str
    colour: str
    capital_region_id: str | None = None
    is_player: bool = False
    personality_blurb: str = ""
    standing_goals: tuple[str, ...] = ()
    military_strength: float = 20.0
    economy: float = 20.0
    #: Steel, yards and machine tools. ``None`` derives it from the economy, which is
    #: what capacity was computed from before industry was a column of its own.
    industry: float | None = None
    stability: float = 50.0
    legitimacy: float = 50.0
    treasury: float = 100.0
    manpower: float = 100.0


@dataclass(frozen=True, slots=True)
class RegionSeed:
    id: str
    name: str
    #: The region's primary shape: used for its label, and the single-shape shorthand.
    geo_key: str
    owner_nation_id: str | None = None
    kind: str = "land"
    terrain: str = "plains"
    population: float = 1.0
    development: float = 1.0
    #: Every pack shape this region is drawn from. Empty means "just ``geo_key``".
    #:
    #: Natural Earth mixes administrative levels between countries -- Germany's 16 are
    #: states, Slovenia's 193 are municipalities -- so one shape per region would make
    #: Yugoslavia 392 regions and Germany 16, and conquering Yugoslavia would take
    #: twenty-four times longer for reasons unrelated to either army. Grouping shapes is
    #: how a scenario picks a sane region count per power.
    parts: tuple[str, ...] = ()

    @property
    def geo_keys(self) -> tuple[str, ...]:
        return self.parts or (self.geo_key,)


@dataclass(frozen=True, slots=True)
class CitySeed:
    id: str
    region_id: str
    name: str
    tier: int = 1


@dataclass(frozen=True, slots=True)
class UnitTemplateSeed:
    """What a kind of unit *is*: its domain, its cost, and what it is worth in a fight.

    The ``domain`` is the load-bearing field. Rule UNIT-002 joins it to decide whether a
    unit is somewhere it could possibly be, which is the only thing standing between the
    simulation and an army corps in the middle of the Atlantic.
    """

    kind: str
    domain: str = "land"
    cost_treasury: float = 0.0
    cost_manpower: float = 0.0
    base_strength: float = 1.0
    move_range: int = 1
    #: Turns from order to delivery. Zero appears the turn it is paid for, which is a
    #: defensible abstraction for a division and an absurd one for a battleship.
    build_days: float = 0.0


@dataclass(frozen=True, slots=True)
class UnitSeed:
    """One formation, in one region, at the start of the world."""

    id: str
    nation_id: str
    region_id: str
    kind: str = "infantry"
    strength: float = 1.0
    morale: float = 50.0


#: What an army is made of, for any scenario that does not say otherwise. Deliberately
#: three kinds and not thirty: the simulation reasons about strength, domain and reach,
#: and a longer list would be flavour the engine cannot act on.
#:
#: There is no fleet here, and its absence is deliberate rather than an oversight. The
#: baked packs are admin-0 land only -- they have no sea regions -- and rule UNIT-002
#: rejects a sea unit standing in a land region. Seeding fleets would mean every world
#: began in violation of its own rules. Naval power stays folded into a nation's
#: ``military_strength``, where the simulation already uses it, until a pack has water to
#: put a fleet on.
DEFAULT_UNIT_TEMPLATES: tuple[UnitTemplateSeed, ...] = (
    UnitTemplateSeed("infantry", domain="land", cost_treasury=6, cost_manpower=10,
                     base_strength=4.0, move_range=1),
    UnitTemplateSeed("armour", domain="land", cost_treasury=14, cost_manpower=6,
                     base_strength=7.0, move_range=2),
    UnitTemplateSeed("air_wing", domain="air", cost_treasury=16, cost_manpower=4,
                     base_strength=5.0, move_range=3),
)


@dataclass(frozen=True, slots=True)
class WorldSeed:
    id: str
    name: str
    start_date: str
    rng_seed: int
    difficulty: Difficulty = Difficulty.NORMAL
    mode: WorldMode = WorldMode.PVE
    nations: tuple[NationSeed, ...] = ()
    regions: tuple[RegionSeed, ...] = ()
    #: ``(a, b)`` for a land frontier, or ``(a, b, crossing)`` where crossing is
    #: ``land``, ``sea`` or ``strait``. Water is a border like any other; what changes is
    #: what can cross it.
    adjacency: tuple[tuple[str, ...], ...] = ()
    cities: tuple[CitySeed, ...] = ()
    unit_templates: tuple[UnitTemplateSeed, ...] = ()
    units: tuple[UnitSeed, ...] = ()
    preset_id: str | None = None


def create_world(path, seed: WorldSeed, *, allow_synced: bool = False) -> sqlite3.Connection:
    """Build a fresh world database and return an open connection.

    Everything happens inside one explicit transaction. The connection runs in autocommit
    mode, and with ``synchronous=FULL`` that means one fsync per statement -- fine for the
    handful of writes a turn produces, ruinous here. A world seeded from real geometry has
    242 nations, and directed relations between every pair is 58,000 rows: as individual
    transactions that takes minutes, and as one transaction it takes under a second.

    **A creation that fails leaves nothing behind.** The schema is applied before that
    transaction opens, so a seed that broke a constraint used to leave a file holding
    empty tables -- and every later attempt at the same path then failed on "table world
    already exists", for a world that did not. On failure the connection is rolled back
    and closed, and a file this call created is deleted with its journal; a file that was
    already there is never touched.
    """
    in_memory = str(path) == ":memory:"
    target = Path(path)
    existed = in_memory or target.exists()
    conn: sqlite3.Connection | None = None
    try:
        conn = create_world_db(path, allow_synced=allow_synced)
        conn.execute("BEGIN")
        _populate(conn, seed)
        conn.commit()
    except BaseException:
        if conn is not None:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
        if not existed:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(f"{target}{suffix}").unlink(missing_ok=True)
        raise
    return conn


def _populate(conn: sqlite3.Connection, seed: WorldSeed) -> None:
    """Write ``seed`` into a freshly created schema, inside the caller's transaction."""
    conn.execute(
        """
        INSERT INTO world (id, name, preset_id, world_date, turn_number, rng_seed,
                           difficulty, mode, schema_version, created_at)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?, 1, ?)
        """,
        (
            seed.id,
            seed.name,
            seed.preset_id,
            seed.start_date,
            seed.rng_seed,
            seed.difficulty.value,
            seed.mode.value,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )

    conn.execute(
        """
        INSERT INTO turn (world_id, number, phase, status, start_date)
        VALUES (?, 0, 'sealed', 'sealed', ?)
        """,
        (seed.id, seed.start_date),
    )

    # Nations before regions: region.owner_nation_id has a real foreign key.
    for nation in seed.nations:
        conn.execute(
            """
            INSERT INTO nation (id, world_id, name, colour, is_player, controller,
                                personality_blurb, standing_goals_json,
                                military_strength, economy, industry, stability,
                                legitimacy, treasury, manpower)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nation.id,
                seed.id,
                nation.name,
                nation.colour,
                int(nation.is_player),
                "player" if nation.is_player else "engine",
                nation.personality_blurb,
                _json_list(nation.standing_goals),
                nation.military_strength,
                nation.economy,
                nation.economy * 0.6 if nation.industry is None else nation.industry,
                nation.stability,
                nation.legitimacy,
                nation.treasury,
                nation.manpower,
            ),
        )

    # **Every nation gets a government here, and only a placeholder one.** Who actually
    # governs is scenario data -- `atlas.scenarios.leaders` overwrites the entries its
    # tables name, right after this -- but *that a nation has somebody to overthrow* is
    # not. Seating the default here is what keeps the layer meaning the same thing in a
    # designed scenario and in a bare test world; the alternative was a world where half
    # the countries cannot have a coup because nobody wrote them an entry.
    conn.executemany(
        "INSERT INTO leader (id, world_id, nation_id, name, title, traits_json,"
        "                    agenda, took_power_turn, came_to_power, is_current)"
        " VALUES (?, ?, ?, '', ?, ?, ?, 0, 'seeded', 1)",
        [(f"ldr:{nation.id}:0", seed.id, nation.id, _DEFAULT_LEADER_TITLE,
          _json_list(_DEFAULT_LEADER_TRAITS), _DEFAULT_LEADER_AGENDA)
         for nation in seed.nations],
    )

    # **An endowment for every nation, neutral until a scenario says otherwise.** Same
    # split as the leader layer: `state` establishes that a country *has* resources, and
    # `atlas.scenarios.resources` decides which -- because `state` does not depend on
    # `scenarios`, and because a world where only designed nations have resources would
    # mean the layer works differently in a scenario and in a test fixture.
    conn.executemany(
        "INSERT INTO resource (world_id, nation_id, good, produced, stockpile)"
        " VALUES (?, ?, ?, ?, ?)",
        [(seed.id, nation.id, good, _default_production(nation),
          round(_default_production(nation) * _DEFAULT_MONTHS_STORED, 2))
         for nation in seed.nations for good in _GOODS],
    )

    for region in seed.regions:
        conn.execute(
            """
            INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id,
                                home_nation_id, terrain, population, development)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                region.id,
                seed.id,
                region.name,
                region.geo_key,
                region.kind,
                region.owner_nation_id,
                # Every province begins as its owner's own core. This is what lets a later
                # peace tell recovering your own ground from annexing someone else's; the
                # migration backfills the same value for worlds made before the column.
                region.owner_nation_id,
                region.terrain,
                region.population,
                region.development,
            ),
        )
        # The composition, always written even for a single-shape region, so every
        # consumer can read one place instead of branching on which kind it is.
        for geo_key in region.geo_keys:
            conn.execute(
                "INSERT OR IGNORE INTO region_geometry (world_id, region_id, geo_key)"
                " VALUES (?, ?, ?)",
                (seed.id, region.id, geo_key),
            )

    # Capitals are set after regions exist, since the reference is unenforced and
    # CORE-005 will check it.
    for nation in seed.nations:
        if nation.capital_region_id:
            conn.execute(
                "UPDATE nation SET capital_region_id = ? WHERE id = ?",
                (nation.capital_region_id, nation.id),
            )

    conn.executemany(
        "INSERT INTO city (id, world_id, region_id, name, tier) VALUES (?, ?, ?, ?, ?)",
        ((city.id, seed.id, city.region_id, city.name, city.tier) for city in seed.cities),
    )

    # Templates before units: UNIT-002 resolves a unit's domain through this table, and a
    # unit whose kind has no template is a unit no rule can reason about.
    conn.executemany(
        """
        INSERT INTO unit_template (world_id, kind, domain, cost_treasury, cost_manpower,
                                   base_strength, move_range, build_days)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (seed.id, t.kind, t.domain, t.cost_treasury, t.cost_manpower,
             t.base_strength, t.move_range, t.build_days)
            for t in seed.unit_templates
        ),
    )

    conn.executemany(
        """
        INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (u.id, seed.id, u.nation_id, u.region_id, u.kind, u.strength, u.morale)
            for u in seed.units
        ),
    )

    # A border is a pair, and how it is crossed is a property of the pair. Entries may be
    # ``(a, b)`` -- a land frontier, the only kind that existed before there was water on
    # the map -- or ``(a, b, crossing)``.
    for entry in seed.adjacency:
        a, b = entry[0], entry[1]
        crossing = entry[2] if len(entry) > 2 else "land"
        lo, hi = sorted((a, b))
        conn.execute(
            "INSERT OR IGNORE INTO region_adjacency (world_id, region_a, region_b, crossing)"
            " VALUES (?, ?, ?, ?)",
            (seed.id, lo, hi, crossing),
        )

    # **This is the only place in the engine that writes a border**, which is what lets
    # :mod:`atlas.engine.adjacency` remember the graph for the life of a world instead of
    # rebuilding it on every supply, trade and air call. Saying so here is the other half
    # of that bargain: a world seeded under an id some other database already used starts
    # from its own map rather than that one's. Blind to the connection on purpose -- an id
    # reused across databases is exactly the case that would otherwise go unnoticed.
    adjacency_cache.forget(seed.id)

    # Directed relations, both ways, starting neutral. executemany keeps this a single
    # prepared statement rather than 58,000 round trips through the parser.
    ids = [n.id for n in seed.nations]
    conn.executemany(
        "INSERT OR IGNORE INTO relation (world_id, nation_a, nation_b, score) VALUES (?, ?, ?, 0)",
        ((seed.id, a, b) for a in ids for b in ids if a != b),
    )

    record_event(
        conn,
        world_id=seed.id,
        turn=0,
        date=seed.start_date,
        kind=EventKind.GENESIS,
        origin=EventOrigin.ENGINE,
        text=f"{seed.name} begins on {seed.start_date}.",
        visibility=Visibility.PUBLIC,
    )


#: The three strategic goods. Strings rather than an import: `state` does not depend on
#: `engine` or `scenarios`, and this is the one place the vocabularies touch. The schema's
#: CHECK constraint on `resource.good` is what actually holds them to three.
_GOODS = ("oil", "steel", "rare")

#: What a nation produces of each good before a scenario says otherwise: **exactly what its
#: own industry burns**, so a world created without a designed scenario is self-sufficient
#: and the trade layer is inert in it.
#:
#: A flat default was tried first and was wrong in a way a test caught: a bare world seeded
#: every nation at 0.4 a month whatever its size, so a country that doubled its industry
#: doubled its demand against a fixed supply and **built more factories to build less**.
#: That is not a supply crisis, it is a unit mismatch -- there is no scenario in a bare
#: world to say who has oil, so the honest default is "enough".
#:
#: This must equal `atlas.engine.trade._DEMAND_PER_INDUSTRY`, and
#: `test_a_world_with_no_scenario_is_self_sufficient` is the guard against the two drifting.
_DEFAULT_PRODUCTION_PER_INDUSTRY = 0.11
_MIN_DEFAULT_PRODUCTION = 0.5

#: Months of its own production a nation starts with in store, so a blockade in a bare world
#: bites over time rather than at once.
_DEFAULT_MONTHS_STORED = 5.0


#: The government every nation starts with before a scenario says otherwise. The traits
#: are the two most inert in :data:`atlas.engine.leaders.TRAITS`, so a nation nobody wrote
#: an entry for behaves close to how it did before this layer existed. Strings rather than
#: an import: `state` does not depend on `engine`, and this is the one place the two
#: vocabularies touch.
_DEFAULT_LEADER_TITLE = "the government"
_DEFAULT_LEADER_TRAITS = ("pragmatist", "cautious")
_DEFAULT_LEADER_AGENDA = "keep the country in one piece"


def _default_production(nation: NationSeed) -> float:
    """What one nation digs up per month in a world nobody wrote a scenario for."""
    industry = nation.industry
    if industry is None:
        industry = nation.economy * 0.6
    return round(
        max(_MIN_DEFAULT_PRODUCTION, industry * _DEFAULT_PRODUCTION_PER_INDUSTRY), 2
    )


def _json_list(values: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(values))


#: Tables whose contents define "the same world state" for comparison purposes.
#: Deliberately excludes diff/diff_batch/routing_log -- those are the audit trail, and
#: two runs that reach identical state by different recorded routes are still identical
#: worlds.
#: How a fingerprinted table is narrowed to one world. Most carry `world_id`; the ones that
#: do not are reached through the row that owns them, which is the same relationship their
#: foreign key already declares.
_BY_ID = "WHERE id = ?"
_BY_WORLD = "WHERE world_id = ?"
_VIA_COMMITMENT = "WHERE commitment_id IN (SELECT id FROM commitment WHERE world_id = ?)"
_VIA_EVENT = "WHERE event_id IN (SELECT id FROM event WHERE world_id = ?)"

_FINGERPRINT_TABLES = (
    ("world", "id", _BY_ID),
    ("nation", "id", _BY_WORLD),
    ("region", "id", _BY_WORLD),
    ("relation", "nation_a, nation_b", _BY_WORLD),
    ("unit", "id", _BY_WORLD),
    ("city", "id", _BY_WORLD),
    ("commitment", "id", _BY_WORLD),
    # What an agreement promised. State, and consequential state: the breach detector reads
    # it every turn, so a rewind that restored the treaty but not its terms would replay
    # the turn against a different promise.
    ("commitment_term", "commitment_id, seq", _BY_WORLD),
    # A campaign in progress is world state. Leaving it out would let a rewind land on a
    # world that looks identical and is mid-siege at a different point, which is the one
    # way this digest could reassure and be wrong.
    ("contest", "id", _BY_WORLD),
    # A yard with a hull in it is state: a rewind that landed on a world with the money
    # spent and the ship neither built nor building would be a rewind that stole it.
    ("construction", "id", _BY_WORLD),
    # What a nation knows, and what that changed about what it builds. A rewind past a
    # completed programme that left the knowledge behind would replay the turn with
    # better ships than the turn had.
    ("technology", "nation_id, tech_id", _BY_WORLD),
    # Who is backing whom in somebody else's war. **This moves every built-world digest by
    # the width of one table name, on a table that is empty in all three shipped eras** --
    # the schema-16 and schema-21 situation, harmless for the same reason: digests are only
    # ever compared within a schema version. It is in here rather than left out because a
    # rewind that restored the front and not the shipments would replay the battle at the
    # wrong odds, which is precisely what this list exists to catch.
    ("support", "patron_id, client_id, kind", _BY_WORLD),
    # And how far each colony has got toward leaving. Same argument, same cost: one table
    # name in every built-world digest, on a table empty in all three shipped eras.
    ("independence", "region_id", _BY_WORLD),
    # Who still holds warheads after an exchange is world state of the sharpest kind: a
    # rewind that restored the map but not the arsenals would replay the turn against a
    # deterrent that had already been spent.
    ("arsenal", "nation_id", _BY_WORLD),
    # What the world's institutions are owed and what they have left to spend. A rewind
    # that restored the war but not the Council's standing -- or left a sanction running
    # for a vote that no longer happened -- would replay the turn in a different world.
    ("organisation", "nation_id", _BY_WORLD),
    ("sanction", "id", _BY_WORLD),
    ("unit_template", "kind", _BY_WORLD),
    # What each staff is trying to do is state as much as where its armies are. A rewind
    # that restored the campaign but not the objectives behind it would replay the turn
    # under different orders.
    # **What a country is built to do.** State, and the most consequential kind: it
    # multiplies the manpower pool, what a formation is worth when it is raised, and how
    # hard a war is felt at home. A rewind that put the map back and left a nation under a
    # doctrine it had not adopted yet would replay the turn with a different army.
    ("doctrine_stance", "id", _BY_WORLD),
    # **And what it is playing for.** A doctrine is how a country fights; this is what it
    # is fighting *toward*, and it is state for the same reason: a rewind that restored the
    # map and left a government committed to a plan it had not adopted yet would replay the
    # turn under a different strategy. The revisions go with it, because the record of why
    # a nation changed its mind is worthless if it survives the change being undone.
    ("nation_strategy", "id", _BY_WORLD),
    ("strategy_revision", "id", _BY_WORLD),
    ("military_objective", "id", _BY_WORLD),
    # And what the war is being fought for, which is the same argument again: two worlds
    # whose fronts are identical and whose declared aims are not are two different wars,
    # and a rewind that restored one and not the other would settle a peace against terms
    # nobody had put on the table.
    #
    # **This moves every built-world digest by the width of one table name**, on a table
    # empty in all three shipped eras -- the schema-16, -21 and -23 situation, and harmless
    # for the reason recorded there: digests are only ever compared within a schema version.
    ("war_aim", "id", _BY_WORLD),
    # ...and what it has cost so far. Two worlds whose fronts are identical and whose
    # armies are equally tired of fighting are the same war; two whose exhaustion differs
    # are not, and only one of them is about to accept terms.
    ("war_effort", "id", _BY_WORLD),
    # ...and what the peace that ended it promised. State for the reason `commitment_term`
    # is: a rewind past a signing has to take the promise with it, or the engine spends the
    # rest of the game paying reparations for a treaty nobody signed.
    ("peace_term", "id", _BY_WORLD),
    # ...and how the country feels about all of it. Two worlds whose armies and treasuries
    # match and whose officer corps are at opposite ends of the scale are not the same
    # world, and only one of them is about to have a coup.
    ("constituency", "id", _BY_WORLD),
    # ...and what each of them can see of the others. Two worlds whose armies match and
    # whose services read each other at opposite ends of the scale are not the same world:
    # one of them is about to be surprised. Same one-table-name digest move as `war_aim`,
    # on a table empty in all three shipped eras.
    ("intel_network", "id", _BY_WORLD),
    # ...and what each of them has worked out about who did what to it. Two worlds that
    # agree about every army and disagree about who burned the shipyard are not the same
    # world: one of them has a casus belli. Same one-table-name digest move as `war_aim`,
    # on a table empty in all three shipped eras.
    ("attribution", "id", _BY_WORLD),
    # ...and which of those acts the world found out about, which is a different fact from
    # who knows who did it. Same one-table-name digest move as `war_aim`, on a table empty
    # in all three shipped eras.
    ("exposure", "id", _BY_WORLD),
    # ...and who has agreed to pool what they believe with whom. A treaty that shares
    # intelligence changes what its parties can see, which is state in the plainest sense.
    ("intel_term", "id", _BY_WORLD),
    # And what the world remembers, which is state in the plainest sense: two worlds that
    # recognise different governments and remember different promises are different worlds,
    # whatever their numbers say. Same one-table-name digest move as `war_aim`, on a table
    # empty in all three shipped eras.
    ("world_fact", "id", _BY_WORLD),
    # What a country has in the ground and what it has agreed to sell. A rewind past a
    # blockade that restored the fleet but not the stockpile it drained would replay the
    # turn with a full magazine, which is the same argument `arsenal` makes one shelf over.
    ("resource", "nation_id, good", _BY_WORLD),
    ("trade_route", "id", _BY_WORLD),
    # What each government is trying to arrange in the market. State, and
    # consequential state: the engine opens routes off the back of it, so a rewind
    # that restored the routes and not the intentions behind them would replay the
    # turn with a purchasing policy nobody had adopted.
    #
    # **This moves every built-world digest by the width of one table name**, on a
    # table that is empty in all three shipped eras -- the schema-16, -21 and -23
    # situation, harmless for the reason recorded there: digests are only ever
    # compared within a schema version.
    ("trade_intent", "id", _BY_WORLD),
    # And what it has stopped selling on purpose. Consequential state by the same
    # argument and more so: an embargo suppresses a cargo and a payment every round it
    # stands, so a rewind that restored the stockpiles and not the embargo behind them
    # would replay the turn with the trade flowing again.
    #
    # **Moves every built-world digest by the width of one table name**, on a table that
    # is empty in all three shipped eras -- the schema-16, -21, -23 and -47 situation,
    # harmless for the reason recorded there: digests are only ever compared within a
    # schema version.
    ("trade_embargo", "id", _BY_WORLD),
    # Who governs a country is state as much as what the country has. A rewind past a
    # coup that restored the treasury and the map but left the new government in the chair
    # would replay the turn under a different administration -- the same argument as
    # `military_objective`, one level up.
    ("leader", "id", _BY_WORLD),
    ("event", "id", _BY_WORLD),
    # **Three tables the digest did not cover, and each was changed experimentally with no
    # fingerprint move at all.** Found by the Codex review of phase 2. A digest that omits
    # state is worse than no digest: it is an assurance that does not hold, and every
    # rewind-exactness claim made about these three was vacuous.
    #
    # Who signed an agreement. `commitment` was fingerprinted and its parties were not, so
    # a rewind could restore the treaty and lose the signatories -- and the party list is
    # what the breach detector reads to decide who broke it.
    ("commitment_nation", "commitment_id, nation_id", _VIA_COMMITMENT),
    # Who holds the sky. Diffable since schema 12 and never digested, so an air campaign
    # that failed to unwind would have looked clean.
    ("air_control", "world_id, region_id", _BY_WORLD),
    # **Who is allowed to see what.** The one that was actively leaking: espionage granted
    # sight of an old event with a direct write, a rewind removed the espionage and left the
    # grant, and nothing noticed because the digest could not see it. Secrecy is state.
    ("event_visibility", "event_id, nation_id", _VIA_EVENT),
    # Who an event happened *to*. Not diffed -- a participant attaches to the event being
    # written now and cascades away with it, which is exactly the argument `event_visibility`
    # fails, since an espionage grant points backwards at an older event. Digested anyway, so
    # that cascade is provable rather than assumed.
    ("event_participant", "event_id, role, seq", _VIA_EVENT),
)


#: Columns hashed by nothing, because they describe when the file was made rather than
#: what is in it.
#:
#: **`world.created_at` is a wall-clock timestamp and it was in the digest.** Two worlds
#: built from the same seed a second apart therefore hashed differently, and the one test
#: that compares two separately-built worlds --
#: `test_the_digest_does_not_depend_on_how_the_database_reached_its_schema` -- failed on
#: about one run of the suite in four hundred. Found by reproducing it in a loop: of every
#: fingerprinted table, the only row that ever differed was `world`, on that one column.
#:
#: **The rewind guarantee was never at risk**, which is why this went unnoticed for so
#: long: a rewind compares a world against *itself* at another moment, and `created_at`
#: does not move within one world. What it broke is the weaker and still useful claim that
#: two worlds holding the same state digest the same -- and, with it, any hope of writing
#: down a reproducible fingerprint for a freshly built era.
#:
#: Kept as a table rather than a single test so the next column of this kind has an
#: obvious home, and named for what it is: a fact about the file, not about the world.
#: **Nothing may be added here that a rewind would have to restore.**
_UNHASHED_COLUMNS: dict[str, frozenset[str]] = {
    "world": frozenset({"created_at"}),
}


def canonical_rows(
    conn: sqlite3.Connection, table: str, order: str, scope: str, world_id: str
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """One table's contribution to a comparison: its columns, and its rows as text.

    Extracted from :func:`fingerprint` so that the semantic dump
    :mod:`atlas.state.tables` builds for paired runs reads a table *exactly* as the digest
    does. A second reader with its own idea of column order, or of how a value becomes
    text, would disagree with the digest about worlds that are identical -- which is the
    class of failure this whole layer exists to prevent.

    **Sorted, not declaration order.** `PRAGMA table_info` reports columns in the
    order the table was *built*, and a column added by `ALTER TABLE ... ADD COLUMN`
    lands at the end of a migrated database while `schema.sql` may declare it in the
    middle -- which is true today of `nation.industry`, `region.home_nation_id` and
    `construction.project_kind`, all three of them fingerprinted. Left as it was, the
    same world reached by the two routes digests differently: the answer would depend
    on how the file got here rather than on what is in it, and a replay comparing a
    migrated save against a freshly built one would report a mismatch that is not one.

    **`repr`, not the value**, for the reason the digest needs it: NULL, the text
    ``'None'``, ``7`` and ``7.0`` are four different worlds.
    """
    skip = _UNHASHED_COLUMNS.get(table, frozenset())
    columns = tuple(
        sorted(
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
            if row["name"] not in skip
        )
    )
    selected = ", ".join(columns)
    rows = conn.execute(
        f"SELECT {selected} FROM {table} {scope} ORDER BY {order}",  # noqa: S608
        (world_id,),
    ).fetchall()
    return columns, tuple(tuple(repr(v) for v in tuple(row)) for row in rows)


def fingerprint(conn: sqlite3.Connection, world_id: str) -> str:
    """A stable digest of world state, for rewind and rollback assertions.

    Used to compare a world against *itself* at another point in time -- after a
    rollback, or after rewinding to turn N and replaying forward. It is not a golden
    value checked into the repository: a frozen hash over many turns churns on every
    schema change until regenerating it becomes reflex, and a reflex assertion has
    stopped testing anything.

    It also compares two worlds built the same way, which is the weaker claim and the one
    :data:`_UNHASHED_COLUMNS` exists to make true.
    """
    digest = blake2b(digest_size=16)
    for table, order, scope in _FINGERPRINT_TABLES:
        digest.update(f"\x1e{table}".encode())
        _columns, rows = canonical_rows(conn, table, order, scope, world_id)
        for row in rows:
            digest.update(b"\x1f")
            digest.update("\x1f".join(row).encode())
    return digest.hexdigest()
