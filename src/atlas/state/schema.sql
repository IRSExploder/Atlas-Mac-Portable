-- ATLAS world state.
--
-- Applied once, in full, at world creation. There is deliberately no migration
-- framework: this is a personal single-user app, and a schema change means starting a
-- new world. That trade buys a schema that can be read top-to-bottom as one document.
--
-- Design rules encoded here, each of which closes a specific way the models could cheat:
--
--   * event.kind is a CLOSED enum and event.origin distinguishes ENGINE from MODEL.
--     Without both, a nation wanting war simply emits kind='casus_belli' on turn N and
--     cites it on turn N+1 -- every privileged rule would self-certify in two turns.
--
--   * diff.reason_event_id has NO foreign key, on purpose. It is enforced by rule
--     CORE-001 instead. An FK would raise IntegrityError on a fabricated event id, and
--     the row would never land -- but a fabricated citation is exactly the attempt we
--     most want preserved in the audit trail. A rule rejects it *and records it*.
--
--   * Every world gets a GENESIS event so turn 1 diffs have something legal to cite.
--     Without it the mandatory-citation rule is unsatisfiable on the first turn.
--
--   * event_visibility is a join table rather than a JSON array, so the information
--     asymmetry rule (INFO-001) is a NOT EXISTS join the query planner can index,
--     instead of a json_each scan that quietly becomes filtering in Python.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- World
-- ---------------------------------------------------------------------------

CREATE TABLE world (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    preset_id       TEXT,
    world_date      TEXT NOT NULL,              -- ISO date, in-world (NOT "current_date":
                                                -- that is a SQL keyword and silently
                                                -- returns the real-world date)
    turn_number     INTEGER NOT NULL DEFAULT 0,
    rng_seed        INTEGER NOT NULL,
    difficulty      TEXT NOT NULL
                    CHECK (difficulty IN ('very_easy','easy','normal','hard','impossible')),
    mode            TEXT NOT NULL DEFAULT 'pve' CHECK (mode IN ('pve','eve')),
    map_pack_id     TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

-- One row per turn. The engine's state machine lives here, so a crash mid-jump is
-- recoverable and a partially resolved turn is distinguishable from a lost one.
-- Where a forked line came from. Absent for an original world, which is what every
-- world was before branching existed. Deliberately its own table rather than columns on
-- `world`: `world` is fingerprinted, and a fork must be byte-identical to the turn it was
-- taken from -- including in its digest.
CREATE TABLE world_lineage (
    world_id        TEXT PRIMARY KEY REFERENCES world(id) ON DELETE CASCADE,
    forked_from     TEXT NOT NULL,
    forked_at_turn  INTEGER NOT NULL,
    forked_at       TEXT NOT NULL
);

CREATE TABLE turn (
    id          INTEGER PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,
    phase       TEXT NOT NULL
                CHECK (phase IN ('read','consult','act','negotiate','jump','resolve','sealed')),
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','sealed','partial','aborted')),
    start_date  TEXT NOT NULL,
    end_date    TEXT,
    UNIQUE (world_id, number)
);

-- ---------------------------------------------------------------------------
-- Nations, blocs, geography
-- ---------------------------------------------------------------------------

-- EVE blocs. A bloc binds a set of nations to one provider. The three-distinct-family
-- assertion lives in code (engine/routing.py), not here, because it depends on the
-- configured model pins rather than on stored state.
CREATE TABLE bloc (
    id          TEXT PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    provider    TEXT NOT NULL CHECK (provider IN ('claude','codex','antigravity','engine','mock')),
    model_pin   TEXT NOT NULL,
    colour      TEXT NOT NULL
);

CREATE TABLE nation (
    id                  TEXT PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    colour              TEXT NOT NULL,              -- "#RRGGBB"
    is_player           INTEGER NOT NULL DEFAULT 0 CHECK (is_player IN (0,1)),
    controller          TEXT NOT NULL DEFAULT 'engine'
                        CHECK (controller IN ('player','engine','claude','codex','antigravity')),
    bloc_id             TEXT REFERENCES bloc(id) ON DELETE SET NULL,
    -- No FK: nation.capital_region_id and region.owner_nation_id are mutually
    -- referential, so one of the two must be an unenforced reference. Integrity is
    -- checked by rule CORE-005 instead.
    capital_region_id   TEXT,

    -- Every nation gets a personality at world creation, tracked or not. Promotion to
    -- Focus Mode then means "gets a dedicated call", not "invents who it is" -- which
    -- is what otherwise produces personality whiplash the moment a background nation
    -- steps into the spotlight.
    personality_blurb   TEXT NOT NULL DEFAULT '',
    standing_goals_json TEXT NOT NULL DEFAULT '[]',

    military_strength   REAL NOT NULL DEFAULT 0,
    economy             REAL NOT NULL DEFAULT 0,
    -- Steel, yards and machine tools: what the country can have under construction at
    -- once. Distinct from `economy`, which is how rich it is. A rich country with no
    -- industry buys its ships abroad or does without, and building industry is itself
    -- something that takes a queue slot and years.
    industry            REAL NOT NULL DEFAULT 0,
    stability           REAL NOT NULL DEFAULT 50,
    legitimacy          REAL NOT NULL DEFAULT 50,
    treasury            REAL NOT NULL DEFAULT 0,
    manpower            REAL NOT NULL DEFAULT 0,

    is_tracked          INTEGER NOT NULL DEFAULT 0 CHECK (is_tracked IN (0,1)),
    tracked_since_turn  INTEGER,
    tracked_reason      TEXT,

    is_alive            INTEGER NOT NULL DEFAULT 1 CHECK (is_alive IN (0,1)),
    -- How deep this country is currently digging for soldiers: 0 peacetime to 3 total war.
    -- Appended last because migration 41 adds it last, and a fresh world has to match a
    -- migrated one column for column -- `test_fingerprint_canonical` catches the
    -- alternative within one run.
    mobilisation        INTEGER NOT NULL DEFAULT 0 CHECK (mobilisation BETWEEN 0 AND 3),
    UNIQUE (world_id, name)
);

CREATE TABLE region (
    id                    TEXT PRIMARY KEY,
    world_id              TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    name                  TEXT NOT NULL,
    geo_key               TEXT NOT NULL,            -- key into the geometry pack; ENGINE-only
    kind                  TEXT NOT NULL DEFAULT 'land'
                          CHECK (kind IN ('land','coastal','ocean','strait')),
    owner_nation_id       TEXT REFERENCES nation(id) ON DELETE SET NULL,
    controller_nation_id  TEXT REFERENCES nation(id) ON DELETE SET NULL,  -- occupation
    -- Who this region belongs to historically, as distinct from who holds it now. A
    -- conquered province is otherwise indistinguishable from a home one, and a peace
    -- settlement has no way to say which is being handed back.
    home_nation_id        TEXT REFERENCES nation(id) ON DELETE SET NULL,
    terrain               TEXT NOT NULL DEFAULT 'plains',
    population            REAL NOT NULL DEFAULT 0,
    development           REAL NOT NULL DEFAULT 0,
    UNIQUE (world_id, geo_key)
);

-- Undirected adjacency stored once per pair with a CHECK forcing a < b, so
-- "is X adjacent to Y" has exactly one row and cannot disagree with itself.
CREATE TABLE region_adjacency (
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    region_a    TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    region_b    TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    crossing    TEXT NOT NULL DEFAULT 'land' CHECK (crossing IN ('land','sea','strait')),
    PRIMARY KEY (region_a, region_b),
    CHECK (region_a < region_b)
);

-- A standing push by one nation on one region. Ground does not change hands on a single
-- roll: a contest accumulates pressure turn by turn, at a rate set mostly by how strong
-- the defender is, and control moves only when it crosses the threshold.
--
-- Several nations may press the same region at once -- each row accumulates
-- independently and the first to arrive takes the ground -- which is why this is a table
-- rather than a pair of columns on `region`.
--
-- The id is derived from (region, attacker) rather than autoincremented, so a contest
-- has the same identity across a rewind and replay.
CREATE TABLE contest (
    id            TEXT PRIMARY KEY,
    world_id      TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    region_id     TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    attacker_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    defender_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    pressure      REAL NOT NULL DEFAULT 0,
    opened_turn   INTEGER NOT NULL DEFAULT 0,
    last_turn     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (world_id, region_id, attacker_id)
);

-- What a power's staff has decided to do with its army: push here, hold there, get out
-- of somewhere else. Standing intent, not an outcome and not a write path -- every
-- territory column stays engine-only, and an objective only tells the engine what this
-- power is *trying* to do. Reachability, whether a push becomes a battle, and who wins
-- it are all decided afterwards, in engine code.
--
-- One objective per (nation, region): a power cannot be simultaneously pushing into a
-- province and holding it. Declaring a new kind for the same ground replaces the old one,
-- which is how a staff changes its mind.
--
-- The id is derived from (nation, region) for the same reason a contest's is: an
-- objective must keep its identity across a rewind and replay.
CREATE TABLE military_objective (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    region_id      TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('push', 'hold', 'withdraw_from')),
    declared_turn  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (nation_id, region_id)
);

-- What a belligerent says it is fighting FOR, per war and per belligerent.
--
-- A war today is two directed `relation` rows and nothing else: there is no war entity, no
-- war id, and nowhere at all to record why anybody is in one. So a peace could only ever be
-- "stop shooting, and here is some ground" -- there was no statement of what either side
-- wanted, which means nothing to measure a settlement against and nothing for a negotiator
-- to concede.
--
-- Keyed by (nation, enemy) rather than by a war id, deliberately. A war *is* the pair here,
-- so the pair is the only identifier that exists, and it is exactly as expressive: each
-- belligerent holds its own aims against each enemy, which is what makes a coalition work
-- without a coalition entity. France's aims against Germany are France's rows; Britain's are
-- Britain's, in the same war, and either can be settled separately.
--
-- Standing intent, like `military_objective`, and ENGINE-only for the same reason: an aim is
-- checked against the map before it is written, and nothing here decides an outcome.
--
-- The id is derived from what identifies the aim, so it survives a rewind and replay.
CREATE TABLE war_aim (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    enemy_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('defend', 'recover_cores', 'annex',
                                                 'regime_change', 'independence',
                                                 'demilitarise')),
    -- The ground the aim is about, for the kinds that name ground. Empty for the two that
    -- are about the war itself rather than a province.
    subject_id     TEXT NOT NULL DEFAULT '',
    declared_turn  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (world_id, nation_id, enemy_id, kind, subject_id)
);

CREATE INDEX ix_war_aim_war ON war_aim(world_id, nation_id, enemy_id);

-- What a war has COST one belligerent, and how tired of it that side is.
--
-- **The ledger a war never had.** A war is two `relation` rows with no start turn, no
-- duration and no accumulator, and casualties existed only as `unit.strength` diffs that
-- nobody added up -- so nothing in the engine could answer *how long has this gone on* or
-- *what has it cost*, which are the two questions every peace in history turned on.
--
-- Keyed like `war_aim`, by the belligerent pair, so each side keeps its own ledger of the
-- same war: France's exhaustion is France's, and a coalition partner's is its own.
--
-- `charged_casualties` is bookkeeping rather than history: it is how much of `casualties`
-- exhaustion has already been charged for, so a turn's fighting is counted once. Kept as a
-- column instead of re-derived from the diff log because the diff log is the audit trail,
-- not an index, and a rewind has to put this back exactly.
CREATE TABLE war_effort (
    id                 TEXT PRIMARY KEY,
    world_id           TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id          TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    enemy_id           TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    opened_turn        INTEGER NOT NULL DEFAULT 0,
    last_turn          INTEGER NOT NULL DEFAULT 0,
    casualties         REAL NOT NULL DEFAULT 0 CHECK (casualties >= 0),
    charged_casualties REAL NOT NULL DEFAULT 0 CHECK (charged_casualties >= 0),
    exhaustion         REAL NOT NULL DEFAULT 0 CHECK (exhaustion BETWEEN 0 AND 100),
    UNIQUE (world_id, nation_id, enemy_id)
);

CREATE INDEX ix_war_effort_war ON war_effort(world_id, nation_id, enemy_id);

-- Who a government has to keep, and how they feel about it.
--
-- **A leader has had nobody to govern.** Stability and legitimacy move for reasons the
-- country never has an opinion about, so a battleship, a land reform and a humiliating
-- peace all read as the same arithmetic. Five groups per nation is enough that different
-- decisions anger different people and few enough that a briefing can name them all.
--
-- Engine-derived: reactions are read from the events the engine already wrote, and no model
-- channel reaches this table. A power cannot set its own support any more than it can set
-- its own exhaustion.
--
-- **Written lazily.** A row appears the first time that constituency actually moves; until
-- then `constituencies.standing_of` answers with the value seeded from the nation's own
-- state, which is the same answer. Five rows for each of 243 nations would be twelve
-- hundred rows of nothing having happened.
CREATE TABLE constituency (
    id         TEXT PRIMARY KEY,
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('army', 'industry', 'people',
                                             'patriots', 'establishment')),
    support    REAL NOT NULL DEFAULT 50 CHECK (support BETWEEN 0 AND 100),
    last_turn  INTEGER NOT NULL DEFAULT 0,
    -- What moved it last, in the engine's own words, so a briefing can say why.
    because    TEXT NOT NULL DEFAULT '',
    UNIQUE (world_id, nation_id, kind)
);

CREATE INDEX ix_constituency_nation ON constituency(world_id, nation_id);

-- What one nation's intelligence service can find out about one other.
--
-- **Per (viewer, target), and the pairing is the design.** Intelligence is not a stat a
-- country has; it is a relationship it has with another country. Britain reading Berlin
-- well and Tokyo badly is the ordinary case, and a single number on `nation` could not
-- say it.
--
-- **Written lazily**, like `constituency`: a row appears the first time a network actually
-- moves, and `intelligence.network_between` answers with the seeded default until then.
-- 243 nations against 243 targets is fifty-nine thousand rows of nobody watching anybody.
--
-- Engine-only. A power that could write its own network could award itself perfect sight
-- of a rival, and one that could write another's could blind it -- which is the whole
-- layer decided by narration.
CREATE TABLE intel_network (
    id               TEXT PRIMARY KEY,
    world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    target_nation_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    strength         REAL NOT NULL DEFAULT 22 CHECK (strength BETWEEN 0 AND 100),
    opened_turn      INTEGER NOT NULL DEFAULT 0,
    last_turn        INTEGER NOT NULL DEFAULT 0,
    -- What moved it last, in the engine's own words.
    because          TEXT NOT NULL DEFAULT '',
    -- **The other half of this pairing, keyed the other way, and the direction is the one
    -- thing to get right when reading this table.** The row is "A's network inside B"; this
    -- column is *B's counter-operation against that network*. One row therefore holds both
    -- sides of one adversarial contest, which is why counterintelligence needed no table of
    -- its own -- and why `counterintel_of(victim, against=actor)` reads the row keyed
    -- (actor, victim) rather than the one keyed (victim, actor).
    --
    -- Engine-only like everything else here. A power that could write this would make itself
    -- unseeable by saying so.
    counter_intel    REAL NOT NULL DEFAULT 0 CHECK (counter_intel BETWEEN 0 AND 100),
    UNIQUE (world_id, nation_id, target_nation_id)
);

CREATE INDEX ix_intel_network_viewer ON intel_network(world_id, nation_id);

-- What one victim has worked out about who was behind one covert act.
--
-- **Per (act, victim), because attribution is knowledge and knowledge belongs to somebody.**
-- Two countries hit by the same operation do not learn the same amount about it, and the
-- one with the better police service should not be told what the careless one worked out.
--
-- Engine-only. A power that could write its own attribution would manufacture a casus belli
-- by asserting guilt, which is the one thing this layer exists to make impossible: a model
-- may accuse anybody it likes through the diplomacy channel, and the accusation carries
-- exactly the mechanical weight the computed tier gives it.
--
-- The rows are written at the victim's own visibility, so an investigation is private until
-- somebody blows the operation -- which is exposure, and a different layer.
CREATE TABLE attribution (
    id                TEXT PRIMARY KEY,
    world_id          TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- Deliberately NOT a foreign key, for the reason `diff.reason_event_id` is not one:
    -- a rewind deletes events and these rows go with them by their own cascade on world,
    -- and an FK here would refuse the delete order the replay happens to choose.
    event_id          INTEGER NOT NULL,
    actor_nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    victim_nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- 0-100. The gradient matters as much as the tier: a victim at 54 is a turn from being
    -- able to raise it, and a briefing that showed only the tier could not say so.
    confidence        REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    tier              TEXT NOT NULL DEFAULT 'none'
                      -- 'none' is a real stored state, not a placeholder: an
                      -- investigation accumulates for turns before it is worth
                      -- anything, and a row that could not exist until it crossed
                      -- the first threshold would throw that away every turn.
                      CHECK (tier IN ('none', 'suspected', 'likely', 'proven')),
    opened_turn       INTEGER NOT NULL DEFAULT 0,
    last_turn         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (world_id, event_id, victim_nation_id)
);

CREATE INDEX ix_attribution_victim ON attribution(world_id, victim_nation_id);

-- An operation that was blown, and what the scandal cost.
--
-- **One row per operation, not per observer.** Exposure is the public path: everybody reads
-- the same scandal, which is exactly what distinguishes it from `attribution`, where two
-- victims of one act can know different amounts.
--
-- The three factors are stored beside the magnitude they produced, because a number nobody
-- can decompose is a number nobody can tune -- and this is the layer whose curve was meant
-- to be eyeballed before it locked.
--
-- Engine-only. A model cannot fake an exposure, avoid one by protesting innocence, or
-- manufacture the casus belli that a large enough one carries.
CREATE TABLE exposure (
    id                TEXT PRIMARY KEY,
    world_id          TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- Deliberately not a foreign key, for the reason `attribution.event_id` is not one.
    event_id          INTEGER NOT NULL,
    actor_nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    victim_nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    importance        REAL NOT NULL DEFAULT 0 CHECK (importance BETWEEN 0 AND 1),
    guardedness       REAL NOT NULL DEFAULT 0 CHECK (guardedness BETWEEN 0 AND 1),
    mismatch          REAL NOT NULL DEFAULT 0 CHECK (mismatch BETWEEN 0 AND 1),
    magnitude         REAL NOT NULL DEFAULT 0 CHECK (magnitude BETWEEN 0 AND 100),
    casus_belli       INTEGER NOT NULL DEFAULT 0 CHECK (casus_belli IN (0, 1)),
    turn              INTEGER NOT NULL DEFAULT 0,
    UNIQUE (world_id, event_id)
);

CREATE INDEX ix_exposure_actor ON exposure(world_id, actor_nation_id);

-- What a treaty says about sharing intelligence.
--
-- **A new table rather than a widened `commitment_term`, and that is the same constraint
-- the peace layer hit.** `commitment_term.clause` is a closed CHECK with three values, and
-- SQLite cannot widen a CHECK without rebuilding the table -- which the migration guard
-- refuses. So the machine-readable half of a sharing clause lives here, hanging off the
-- same `commitment` row, exactly as `peace_term` does.
--
-- What flows is an *estimate*. There is no column here that could carry a true figure, and
-- that is deliberate: the clause names who shares with whom about what, and the engine
-- computes the sharing at read time from the sharer's own belief. An ally's certainty is
-- not transferable because the ally has none.
CREATE TABLE intel_term (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    commitment_id  INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    -- Who is handing over what they believe, and who receives it.
    sharer_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    recipient_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- The third party the clause is about. Empty means everything the sharer sees, which is
    -- the widest a clause may be and still be a clause.
    subject_id     TEXT NOT NULL DEFAULT '',
    UNIQUE (world_id, commitment_id, seq)
);

CREATE INDEX ix_intel_term_recipient ON intel_term(world_id, recipient_id);

-- What a peace actually SAYS, in terms the engine can carry out and check.
--
-- **A new table rather than a widened `commitment_term`, and that is a constraint rather
-- than a preference.** `commitment_term.clause` is a closed CHECK with three values, and
-- SQLite cannot widen a CHECK without rebuilding the table -- which the migration guard
-- refuses, and which would be the third save-invalidating migration ever. So the peace's
-- machine-readable half lives here, hanging off the same `commitment` row, and
-- `commitment_term` is left exactly as it was.
--
-- The peace itself is an ordinary `peace_treaty` commitment (the kind that was defined
-- when the schema was written and never once written to), so breaking one runs through the
-- breach machinery that already exists: the casus belli, the regard, the legitimacy charge.
CREATE TABLE peace_term (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    commitment_id  INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    -- Widened at schema 43 to carry `depose` and `limit_arsenal`. That was the first
    -- save-invalidating migration in the project: a world written by THIS build cannot be
    -- opened by an older one, because the older CHECK refuses those two values.
    kind           TEXT NOT NULL CHECK (kind IN ('cede_region', 'restore_region',
                                                 'withdraw', 'reparations',
                                                 'demilitarise', 'guarantee',
                                                 'recognise', 'depose',
                                                 'limit_arsenal')),
    -- Who must do it, and who gets it. Soft references for the reason `world_fact`'s are.
    obliged_id     TEXT NOT NULL DEFAULT '',
    beneficiary_id TEXT NOT NULL DEFAULT '',
    -- The ground or the nation the term is about.
    subject_id     TEXT NOT NULL DEFAULT '',
    -- Money for reparations, months for a demilitarised zone, nothing for the rest.
    value          REAL NOT NULL DEFAULT 0,
    -- What is left to pay. A term that is discharged the moment it is signed -- a cession,
    -- a withdrawal -- is written settled.
    outstanding    REAL NOT NULL DEFAULT 0,
    due_turn       INTEGER,
    UNIQUE (commitment_id, seq)
);

CREATE INDEX ix_peace_term_world ON peace_term(world_id, commitment_id);

-- What the world REMEMBERS, as distinct from what the engine COMPUTES.
--
-- **That distinction is the whole design and it is enforced rather than intended.** A city
-- renamed, a disputed state recognised, a doctrine announced, a no-first-use pledge, a day
-- of remembrance: each is a fact the world holds and reasons from, and none of them moves a
-- number. A fact never implies a mechanical effect. The engine's numbers come from the
-- engine's own layers; this table is memory, and memory is shown separately from arithmetic
-- so that neither can be mistaken for the other.
--
-- Every fact hangs off the event that created it -- `source_event_id`, with the cascade
-- that gives it -- which is what makes a fact causal (you can ask *why* the world believes
-- this) and what makes a rewind clean: a rewind deletes the events of the turns it undoes,
-- and the facts those events minted go with them.
--
-- `subject_id` and `object_id` are soft references rather than foreign keys, because a
-- subject may be a nation, a region, an international body or the world itself, and one
-- column cannot point at four tables. They are validated when the fact is written.
--
-- `confidence` is *recognition*, not certainty about reality: a border claim two powers
-- accept and three do not is one fact per believer, not one fact with a probability.
CREATE TABLE world_fact (
    id              TEXT PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    subject_kind    TEXT NOT NULL
                    CHECK (subject_kind IN ('nation', 'region', 'organisation', 'world')),
    subject_id      TEXT NOT NULL,
    -- Deliberately NOT a closed enum. The vocabulary of things a world can remember is
    -- open by nature, and every widening of a CHECK in SQLite is a table rebuild. The
    -- registry that decides what a predicate means lives in `atlas.engine.facts`, where it
    -- can grow without a migration.
    predicate       TEXT NOT NULL,
    object_kind     TEXT NOT NULL DEFAULT 'none'
                    CHECK (object_kind IN ('none', 'nation', 'region', 'organisation', 'text')),
    object_id       TEXT NOT NULL DEFAULT '',
    value           TEXT NOT NULL DEFAULT '',
    start_turn      INTEGER NOT NULL DEFAULT 0,
    -- NULL means the world still believes it. A fact is retired rather than deleted, so
    -- "Danzig was called Gdansk between 1945 and 1990" stays askable.
    end_turn        INTEGER,
    source_event_id INTEGER REFERENCES event(id) ON DELETE CASCADE,
    visibility      TEXT NOT NULL DEFAULT 'public'
                    CHECK (visibility IN ('public', 'bilateral', 'secret')),
    confidence      REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1)
);

CREATE INDEX ix_world_fact_subject ON world_fact(world_id, subject_kind, subject_id);
CREATE INDEX ix_world_fact_predicate ON world_fact(world_id, predicate);

-- Which pack shapes compose a region. Natural Earth mixes administrative levels
-- between countries -- Germany's 16 are states, Slovenia's 193 are municipalities -- so
-- one shape per region would make Yugoslavia 392 regions and Germany 16. Grouping
-- shapes lets a scenario pick a sane count per power without a second bake: geometry
-- stays immutable in the pack, and the composition is world data.
--
-- region.geo_key remains the primary shape, used for labels and as the single-shape
-- shorthand; this table is the full composition.
CREATE TABLE region_geometry (
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    region_id  TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    geo_key    TEXT NOT NULL,
    PRIMARY KEY (region_id, geo_key)
);

-- What a nation has worked out how to do. One row per completed programme.
--
-- `secret` records that a programme was pursued in secret; it is not what *keeps* it
-- secret. What hides a programme is the visibility of the events it writes, which the
-- engine already enforces everywhere and rule INFO-002 already ties diffs to.
CREATE TABLE technology (
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    tech_id        TEXT NOT NULL,
    completed_turn INTEGER NOT NULL DEFAULT 0,
    secret         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (nation_id, tech_id)
);

-- What a nation could destroy, and whether it could still do it after being hit first.
--
-- Two columns and no more, because everything else that matters about an arsenal is
-- *derived from state that already moves*: how much of it survives a first strike is a
-- function of the delivery tier and how many boats are at sea, so sinking a submarine
-- weakens a deterrent without anybody editing this table.
--
-- Absent is the answer for almost every nation on almost every map. A world with no rows
-- here is a world with no nuclear weapons in it, which is exactly right for 1936 and is
-- what every save written before schema 10 migrates to.
--
-- Both columns are ENGINE-only. A model that could write its own warhead count would
-- have an instant-win button, and one that could write somebody else's could disarm them
-- by narration.
CREATE TABLE arsenal (
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- Never negative. `_spend` adjusts downward and a launch clamps what it commits to
    -- what is held, so this should be unreachable -- which is exactly when a constraint
    -- is worth having, because the day it is reachable nobody will be watching.
    warheads       INTEGER NOT NULL DEFAULT 0 CHECK (warheads >= 0),
    -- How they would be delivered, worst to best. A closed enum, and widening it is
    -- save-invalidating -- schema 19 did it once, for `sea`. The tiers are the five that
    -- mean genuinely different things about surviving a first strike, not a catalogue of
    -- airframes.
    delivery       TEXT NOT NULL DEFAULT 'none'
                   CHECK (delivery IN ('none','aircraft','missile','sea','triad')),
    -- How many of this nation's submarines actually carry missiles. NULL means the
    -- scenario did not say, and every boat counts -- which is what every world did before
    -- this column, so an era that declares nothing behaves exactly as it did.
    deterrent_boats INTEGER,
    -- **How well the arsenal works, never how much of it there is.** Neglect degrades
    -- this and never the warhead count: a power is broke exactly when it is losing, so
    -- deleting warheads for non-payment would disarm a government at the moment a
    -- deterrent exists for. Floored well above zero in `nuclear.drift_readiness`, so a
    -- cornered state always keeps a last-ditch launch -- at worse odds.
    --
    -- **Last on purpose.** `ALTER TABLE ... ADD COLUMN` appends, so a column placed
    -- higher here would give a fresh world a different column *order* from a migrated
    -- one -- which `test_fingerprint_canonical` caught within one run of adding it.
    readiness      REAL NOT NULL DEFAULT 1.0,
    -- How many of the warheads above are battlefield weapons rather than city-killers.
    -- A subset of `warheads`, never a separate pile: upkeep and launches count the whole
    -- arsenal, and what this changes is which part of it deters. Appended last because
    -- migration 42 adds it last.
    -- No CHECK, deliberately: SQLite cannot add one through ALTER TABLE, so a
    -- constraint here would exist on a fresh world and not on a migrated one, and
    -- `test_a_fresh_world_and_a_migrated_one_promise_the_same_contract` is right to
    -- refuse that. `readiness` carries none for the same reason.
    tactical       INTEGER NOT NULL DEFAULT 0,
    -- **Composite, and `nation_id` alone was wrong.** Every other table in this schema is
    -- keyed by world first because one file can hold several worlds; keyed by nation alone,
    -- two worlds in one file could not both give the United States an arsenal. The
    -- migrated schema had this right and the fresh one did not, which is the divergence
    -- the parity test now exists to catch.
    PRIMARY KEY (world_id, nation_id)
);

-- What a country is BUILT to do, as opposed to what it wants. Engine-owned world state,
-- and the counterpart to the player's own standing directives -- which live in the
-- workspace, reorder advice, and never touch the arithmetic. This does the opposite: it
-- changes the calculation, it applies to every nation including the player's, and a rewind
-- undoes it like any other state.
--
-- Absence is the answer for most nations on most axes, and it means today's behaviour
-- exactly: no row, no stance, every multiplier 1.0. Same arrangement `arsenal` has, and it
-- is what lets this ship without moving any world that does not seed it.
--
-- `stance` is EMPTY while `shifting_to` is set. A country in the middle of reorganising
-- holds neither stance's advantages, and that dip is the cost of changing -- so there is no
-- third stance and no in-transition flag anybody can forget to check.
--
-- The id is derived from (nation, axis) for the reason `military_objective` and `war_aim`
-- derive theirs: a rewind does not replay, so a row the engine rewrites has to be
-- addressable by what identifies it.
--
-- Every column is ENGINE-only. A model that could write its own doctrine would be granting
-- itself the bonus, which is the argument `arsenal` makes one shelf over.
CREATE TABLE doctrine (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- A closed enum, and widening it is save-invalidating. The axes are the questions
    -- every country has answered whether or not it ever said so, not a catalogue of
    -- adjectives.
    axis           TEXT NOT NULL CHECK (axis IN ('mobilisation', 'nuclear_posture',
                                                 'alignment')),
    -- Not a CHECK: which stances an axis has is declared once in `engine.doctrines`, and a
    -- second copy here would be the one that goes stale. A stance the catalogue does not
    -- know reads as no stance, which is the safe direction to be wrong in.
    stance         TEXT NOT NULL DEFAULT '',
    adopted_on     TEXT NOT NULL DEFAULT '',
    adopted_turn   INTEGER NOT NULL DEFAULT 0,
    shifting_to    TEXT NOT NULL DEFAULT '',
    shift_ends_on  TEXT NOT NULL DEFAULT '',
    UNIQUE (world_id, nation_id, axis)
);

-- The same thing as `doctrine` above, with the closed CHECK removed. Which axes exist is
-- decided in `engine.doctrines.AXES` from now on, exactly as `world_fact.predicate` is
-- decided in `engine.facts` -- an open vocabulary belongs in code, where it can grow
-- without a table rebuild, and four more axes are already scoped.
--
-- `doctrine` is kept rather than dropped because dropping it is the destructive statement
-- the migration guard exists to refuse, and because this schema has answered exactly this
-- question twice before the same way: `expires_turn` stayed when `expires_on` arrived, and
-- `due_turn` when `due_on` did. Nothing reads it after schema 38.
CREATE TABLE doctrine_stance (
    id             TEXT PRIMARY KEY,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    axis           TEXT NOT NULL,
    stance         TEXT NOT NULL DEFAULT '',
    adopted_on     TEXT NOT NULL DEFAULT '',
    adopted_turn   INTEGER NOT NULL DEFAULT 0,
    shifting_to    TEXT NOT NULL DEFAULT '',
    shift_ends_on  TEXT NOT NULL DEFAULT '',
    UNIQUE (world_id, nation_id, axis)
);

-- An international body: a UN, a NATO council. It is a `nation` row with no territory,
-- no units and no capital, and this table is what makes it one of these instead of a
-- country. Same arrangement `arsenal` has: the nation row buys the entire existing spine
-- (it can author events, it has a directed `relation` to every power, diffs address it,
-- rewind undoes it) and this row carries what is specific.
--
-- `authority` is the load-bearing column and the reason this is a mechanic rather than
-- flavour. It falls when a resolution is vetoed, or passes and is ignored; it rises when
-- a member complies. It feeds the odds the next resolution passes. A body defied twice
-- becomes a body nobody fears, and that emerges rather than being written.
--
-- Both columns are ENGINE-only. A model that could write its own authority could vote
-- itself credibility.
CREATE TABLE organisation (
    nation_id      TEXT PRIMARY KEY REFERENCES nation(id) ON DELETE CASCADE,
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- 'universal' -- everyone is a member, and aggression anywhere is its business.
    -- 'alliance'  -- members only, and an attack on one is what convenes it.
    kind           TEXT NOT NULL DEFAULT 'universal'
                   CHECK (kind IN ('universal', 'alliance')),
    charter_text   TEXT NOT NULL DEFAULT '',
    authority      REAL NOT NULL DEFAULT 50,
    convened_turn  INTEGER
);

-- Who belongs, and who can stop a resolution on their own. `is_permanent` is the veto,
-- and it is deliberately the one input to a session that is decided rather than rolled.
CREATE TABLE organisation_member (
    organisation_id TEXT NOT NULL REFERENCES organisation(nation_id) ON DELETE CASCADE,
    nation_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    is_permanent    INTEGER NOT NULL DEFAULT 0 CHECK (is_permanent IN (0,1)),
    joined_turn     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (organisation_id, nation_id)
);

-- A standing measure against one power, expiring on a turn. The only genuinely new
-- *standing* state in the organisation layer, and the thing the gambit design note
-- flagged as having nowhere to live ("the vocabulary is stateless effects on existing
-- columns"). Narrow on purpose: a general `standing_effect` table would invite every
-- mechanic to become one.
CREATE TABLE sanction (
    id               TEXT PRIMARY KEY,
    world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    organisation_id  TEXT NOT NULL REFERENCES organisation(nation_id) ON DELETE CASCADE,
    target_nation_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    severity         REAL NOT NULL DEFAULT 1,
    opened_turn      INTEGER NOT NULL DEFAULT 0,
    -- When it lifts, both ways: `until_on` is the world date and is what every reader
    -- prefers, `until_turn` is what a save older than schema 36 has. Same pairing and same
    -- reason as `commitment.expires_on`.
    until_turn       INTEGER NOT NULL DEFAULT 0,
    until_on         TEXT
);

-- The sky over a province, which is contested separately from the ground under it and
-- persists between turns. One row per region anybody has ever fought over: a holder (NULL
-- when the air battle settled nothing) and how decisively, which is what the ground
-- adjudicator reads. Written only by the engine, only through diffs, so it rewinds.
CREATE TABLE air_control (
    world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    region_id        TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    holder_nation_id TEXT REFERENCES nation(id) ON DELETE SET NULL,
    margin           REAL NOT NULL DEFAULT 0,
    last_turn        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (world_id, region_id)
);

CREATE INDEX ix_air_control_region ON air_control(region_id);

-- Who is actually running a country, as opposed to what the country is.
--
-- A nation had a personality and no *person*. `stability` and `legitimacy` moved every
-- turn with nobody's name attached to them, so a coup was a number falling rather than
-- somebody being removed, and the tracked-agent briefing spoke in the voice of a colour
-- on a map. This gives every nation somebody to be.
--
-- History is kept rather than overwritten: a deposed leader stays as a row with
-- `is_current = 0` and a `left_power_turn`, because "the third government this year" is a
-- fact about a country that a single mutable row cannot express. Exactly one row per
-- nation may be current, enforced by a partial unique index rather than by convention.
CREATE TABLE leader (
    id               TEXT PRIMARY KEY,
    world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    -- A JSON array of trait names from `atlas.engine.leaders.TRAITS`. Unknown names are
    -- ignored rather than refused, so a scenario may carry flavour the engine has no
    -- mechanic for yet -- the same tolerance `tech.effects` has.
    traits_json      TEXT NOT NULL DEFAULT '[]',
    -- One sentence in the leader's own interest, shown to the model that plays this
    -- nation. Not a goal the engine scores: a goal the engine can hold it to.
    agenda           TEXT NOT NULL DEFAULT '',
    took_power_turn  INTEGER NOT NULL DEFAULT 0,
    left_power_turn  INTEGER,
    -- How they got the job. Read by the briefing and by the succession roll: a government
    -- that came in on a coup is more likely to go out on one.
    came_to_power    TEXT NOT NULL DEFAULT 'seeded'
                     CHECK (came_to_power IN ('seeded','succession','coup','election',
                                              'revolution')),
    is_current       INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
);

CREATE INDEX ix_leader_nation ON leader(nation_id, is_current);
CREATE UNIQUE INDEX ux_leader_current ON leader(world_id, nation_id)
    WHERE is_current = 1;

-- What a country digs out of the ground, and what it has put by.
--
-- Three goods, not a dozen: every resource is a number a model has to reason about in a
-- prompt, and a dozen would buy spreadsheet depth and pay for it in the thing that makes
-- the engine work. See :mod:`atlas.engine.trade`.
--
-- A shortage does not make a country *poorer*, it makes it unable to *build*: the shortfall
-- scales `industrial_capacity`, which is a door that already existed. A blockaded country
-- still collects its taxes and cannot lay down a battleship.
CREATE TABLE resource (
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    good       TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
    -- What the country produces per month from its own ground.
    produced   REAL NOT NULL DEFAULT 0,
    -- What it has put by. A buffer, so a blockade bites over months rather than instantly.
    stockpile  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (world_id, nation_id, good)
);

-- A standing agreement to move one good from one country to another.
--
-- It exists because somebody agreed to it, and it stops for reasons that already exist:
-- a sanction, a blockaded sea lane, or a war. None of those needed new machinery.
CREATE TABLE trade_route (
    id         TEXT PRIMARY KEY,
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    seller_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    buyer_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    good       TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
    volume     REAL NOT NULL DEFAULT 0,
    opened_turn INTEGER NOT NULL DEFAULT 0,
    -- What the engine has settled this cargo at, in treasury per unit per month, and the
    -- turn it last looked at those terms. `priced_turn = -1` means nobody has priced it
    -- yet: a route
    -- seeded before the engine looked at it, or one in a world where pricing is switched
    -- off. Such a route moves goods and no money, which is exactly what every route did
    -- before this column existed. See `atlas.engine.pricing`.
    unit_price REAL NOT NULL DEFAULT 0,
    priced_turn INTEGER NOT NULL DEFAULT -1,
    -- Whether the cargo is currently moving, and which side stopped it. A commercial
    -- suspension, NOT a broken agreement: the commitment that opened this route is
    -- untouched, the row stays, and the shipments resume the turn the seller's floor and
    -- the buyer's ceiling overlap again. `suspended_turn = -1` is a route that is
    -- shipping, which is every route in every world before this column and every route in
    -- a world played with `atlas.engine.pricing` switched off.
    -- See `atlas.engine.consent`.
    suspended_turn INTEGER NOT NULL DEFAULT -1,
    suspended_by TEXT NOT NULL DEFAULT '' CHECK (suspended_by IN ('', 'seller', 'buyer')),
    -- And *when* it stopped, in world time. `suspended_turn` is a turn number and cannot
    -- answer a day question: eight turns is eight months at a monthly pace and eight years
    -- at a yearly one, and the dwell a suspension has to serve before it may lift is a
    -- duration, not a count of turns. Written in the same diff batch as `suspended_turn`,
    -- so it rewinds with it. Empty on a route that is shipping and on every route written
    -- before this column. See `atlas.engine.consent.DWELL_DAYS`.
    suspended_date TEXT NOT NULL DEFAULT '',
    CHECK (seller_id <> buyer_id)
);

-- What a government is trying to arrange, standing, across turns. **Intent, not a write
-- path**: a power names a good and at most a counterparty, and the engine decides whether
-- there is anything to ship, how much, and what it costs -- exactly the split
-- `trade_route.volume` and `trade_route.unit_price` are already under. A staff that could
-- name the volume could write itself a hundred barrels a month; one that can name an
-- *intention* has said what its purchasing ministry is for and nothing more.
--
-- Kept in the database rather than the workspace, unlike `atlas.engine.directives`, and
-- the difference is who it belongs to: a directive is a private note by the person at the
-- keyboard that weighs a ranking, and this is a fact about a country that the engine acts
-- on, that a rewind must restore, and that any power on the map may hold.
CREATE TABLE trade_intent (
    id         TEXT PRIMARY KEY,
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- `seek` wants a supply of the good, `sell` wants an outlet for it, `diversify` wants
    -- a second supplier that is not `partner_id`.
    kind       TEXT NOT NULL CHECK (kind IN ('seek','sell','diversify')),
    good       TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
    -- Who it names, if it names anybody. `seek` and `sell` may leave this empty and let
    -- the engine find whoever is reachable; `diversify` requires it, because diversifying
    -- away from nobody in particular is not a policy.
    partner_id TEXT NOT NULL DEFAULT '',
    opened_turn INTEGER NOT NULL DEFAULT 0,
    -- `standing` while the engine is still looking, `met` once it opened a route for it,
    -- `retired` when the government withdrew it or it stopped being possible.
    status     TEXT NOT NULL DEFAULT 'standing'
               CHECK (status IN ('standing','met','retired')),
    -- Why the engine has not been able to act on it, in the words a briefing prints.
    note       TEXT NOT NULL DEFAULT '',
    CHECK (nation_id <> partner_id)
);

CREATE INDEX ix_trade_intent_world ON trade_intent(world_id, nation_id);

-- An export embargo: one power's deliberate refusal to sell to another, at its own cost.
--
-- **Not the commercial suspension two columns up, and the distinction is the whole point.**
-- `trade_route.suspended_by` records a lane that stopped because the two sides could not
-- agree terms -- engine-derived, automatic, nobody decided anything, and it reverses itself
-- the turn the numbers overlap. This is a government saying *I will take the loss to hurt
-- you*: it is proposed by a model, it costs the seller the revenue it was earning, it
-- damages the relationship, and it stands until the same government lifts it.
--
-- **Engine-only, like every other number in this layer.** A power names a target and at
-- most a good; `atlas.engine.embargo.impose` checks it is actually exporting to that
-- target, checks the cooldown, writes the row and charges the diplomatic cost. Every
-- column here is in `atlas.state.permissions.ENGINE_ONLY`, because a model that could write
-- `revenue_forgone` could sacrifice nothing and claim it had, and one that could write
-- `status` could lift somebody else's embargo of it.
--
-- One row per (proposer, target, good) triple for the life of the world, its lifecycle in
-- `status`/`opened_turn`/`lifted_turn` rather than a new row per episode -- the same
-- derived-id argument `trade_intent` makes: an embargo is *what it is*, and a counter in
-- the id would make the fingerprint depend on how many had ever been imposed. `episodes`
-- counts the re-impositions the row has carried, and `revenue_forgone` accumulates across
-- all of them, because what this has cost its proposer is the number the decision is
-- supposed to be judged on.
CREATE TABLE trade_embargo (
    id          TEXT PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- The seller doing the embargoing, and the buyer it is aimed at.
    proposer_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    target_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- The good it covers, or '' for every good this seller ships to that buyer.
    good        TEXT NOT NULL DEFAULT '' CHECK (good IN ('', 'oil', 'steel', 'rare')),
    opened_turn INTEGER NOT NULL DEFAULT 0,
    -- When it was last lifted, which is what the cooldown is measured from. `-1` is one
    -- that has never been lifted.
    lifted_turn INTEGER NOT NULL DEFAULT -1,
    -- The world dates it was imposed and lifted on, so the minimum duration and the
    -- cooldown are elapsed history rather than a count of somebody's turns. See
    -- `atlas.engine.embargo.MINIMUM_MONTHS`.
    opened_on   TEXT NOT NULL DEFAULT '',
    lifted_on   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'standing'
                CHECK (status IN ('standing', 'lifted')),
    -- What the proposer has given up for it, in treasury, accumulated every round the
    -- embargo stops a cargo that would otherwise have been paid for. Engine-computed from
    -- the volume the route would have moved and the price the engine would have charged.
    revenue_forgone REAL NOT NULL DEFAULT 0,
    -- How many times this embargo has been imposed. `impose` always writes it explicitly
    -- and writes 1 the first time, so the default is what a row that has never been
    -- imposed would carry -- which is none, not one. A default of 1 would have made a
    -- hypothetical unimposed row claim an episode it never had.
    episodes    INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    CHECK (proposer_id <> target_id)
);

CREATE INDEX ix_trade_embargo_world ON trade_embargo(world_id, proposer_id);
CREATE INDEX ix_trade_embargo_target ON trade_embargo(world_id, target_id);

CREATE INDEX ix_resource_nation ON resource(nation_id);
CREATE INDEX ix_trade_route_world ON trade_route(world_id, buyer_id);
CREATE INDEX ix_trade_route_seller ON trade_route(seller_id);

CREATE TABLE city (
    id          TEXT PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    region_id   TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    tier        INTEGER NOT NULL DEFAULT 1 CHECK (tier BETWEEN 1 AND 5)
);

-- ---------------------------------------------------------------------------
-- Military
-- ---------------------------------------------------------------------------

-- Gives the unit rules real columns to test against. Without this table,
-- "units cannot be created from nothing" and "naval units cannot enter inland
-- regions" are assertions with nothing to evaluate.
CREATE TABLE unit_template (
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    domain          TEXT NOT NULL CHECK (domain IN ('land','sea','air')),
    cost_treasury   REAL NOT NULL DEFAULT 0,
    cost_manpower   REAL NOT NULL DEFAULT 0,
    base_strength   REAL NOT NULL DEFAULT 1,
    move_range      INTEGER NOT NULL DEFAULT 1,
    -- Superseded by build_days at schema 9 and kept only because migrations are
    -- additive-only: an existing world cannot drop it, and a fresh world has to match
    -- a migrated one column for column. Nothing reads it.
    build_turns     INTEGER NOT NULL DEFAULT 0,
    -- Days from order to delivery. Zero means it appears the turn it is ordered, which
    -- is what everything did before there was anything worth waiting for. Days rather
    -- than turns because a turn is a week or a year depending on which button the
    -- player pressed, and a carrier should not take eleven weeks in one game and ten
    -- years in another.
    build_days      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (world_id, kind)
);

-- A yard with something in it. Paid for when ordered and delivered when finished, so a
-- half-built battleship is money already spent -- which is what makes a building
-- programme a commitment rather than a shopping list, and what a rival's intelligence
-- would most like to know.
CREATE TABLE construction (
    id              TEXT PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    region_id       TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    -- What is being built: a formation, or a public work. The queue is deliberately one
    -- queue -- a country deciding between a battleship and a housing programme is the
    -- decision the whole system exists to pose.
    project_kind    TEXT NOT NULL DEFAULT 'unit',
    kind            TEXT NOT NULL,
    -- Superseded by days_remaining at schema 9; see the note on build_turns above.
    turns_remaining INTEGER NOT NULL DEFAULT 0,
    days_remaining  REAL NOT NULL DEFAULT 0,
    started_turn    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE unit (
    id          TEXT PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    region_id   TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    strength    REAL NOT NULL DEFAULT 1,
    morale      REAL NOT NULL DEFAULT 50,
    -- How well dug in, earned over the months this formation holds one place and lost the
    -- moment it moves. Appended in this order because migration 40 adds them last and a
    -- fresh world has to match a migrated one column for column -- `test_fingerprint_canonical`
    -- catches the alternative within one run.
    entrenchment    REAL NOT NULL DEFAULT 0,
    -- The region the digging was done in, so the mechanic can see for itself that a
    -- formation has moved rather than relying on four separate movement paths to say so.
    entrenched_at   TEXT
);

-- ---------------------------------------------------------------------------
-- Diplomacy
-- ---------------------------------------------------------------------------

-- Directed: (a -> b) and (b -> a) are separate rows. Nations need not feel the same
-- way about each other, and collapsing that loses the asymmetry that makes betrayal
-- legible.
CREATE TABLE relation (
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_a        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    nation_b        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    score           REAL NOT NULL DEFAULT 0 CHECK (score BETWEEN -100 AND 100),
    treaty_status   TEXT NOT NULL DEFAULT 'none'
                    CHECK (treaty_status IN ('none','non_aggression','trade','defensive','alliance','vassal')),
    at_war          INTEGER NOT NULL DEFAULT 0 CHECK (at_war IN (0,1)),
    since_event_id  INTEGER,
    PRIMARY KEY (nation_a, nation_b),
    CHECK (nation_a <> nation_b)
);

-- An agreement, and how it ended.
--
-- `breached_by_nation_id` and `breached_turn` are two columns rather than one because
-- "this agreement ended badly" and "Karthos ended it" are different facts, and only the
-- second supports a grievance the injured party can point at. `expires_turn` NULL means
-- the agreement stands until somebody breaks it.
--
-- **The comments live here rather than beside the columns**, and that is not style: SQLite
-- re-parses a table's stored SQL on `ALTER TABLE ... DROP COLUMN`, and a comment left
-- dangling by the removed text makes the re-parse fail with *incomplete input*. The
-- migration tests drop these columns to simulate an older world, so a comment between them
-- breaks the downgrade path that proves the migration works.
CREATE TABLE commitment (
    id                  INTEGER PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL
                        CHECK (kind IN ('non_aggression_pact','alliance','trade_agreement',
                                        'territory_cession','military_access','tribute',
                                        'ceasefire','peace_treaty','defensive_pact')),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','active','broken','fulfilled','expired')),
    condition_text      TEXT NOT NULL DEFAULT '',
    source_event_id     INTEGER,
    created_turn        INTEGER NOT NULL,
    resolved_turn       INTEGER,
    breached_by_nation_id TEXT REFERENCES nation(id) ON DELETE SET NULL,
    breached_turn         INTEGER,
    -- When it runs out, both ways. `expires_on` is the world date and is what every
    -- reader prefers; `expires_turn` is kept because a save written before schema 35 has
    -- no date to convert to -- the calendar at the turn of signing is not recoverable
    -- from the row. A turn is not a duration: `turn + 3` was three months at monthly
    -- pacing and three years at yearly, which made the pace control decide how long a
    -- truce lasted.
    expires_turn          INTEGER,
    expires_on            TEXT
);

CREATE TABLE commitment_nation (
    commitment_id   INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
    nation_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'party' CHECK (role IN ('party','guarantor','target')),
    -- When this party accepted. NULL until it does, and an agreement may not become
    -- active while any party's is NULL -- see rule DIP-004.
    accepted_turn   INTEGER,
    PRIMARY KEY (commitment_id, nation_id)
);

-- What an agreement actually promised, in terms the engine can check. `condition_text` is
-- prose and always was; this is the machine-readable half, and it is what lets a breach be
-- *derived* for a kind other than the three pacts. See atlas.engine.treaties.
CREATE TABLE commitment_term (
    commitment_id   INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    clause          TEXT NOT NULL
                    CHECK (clause IN ('no_war','open_trade','cede_region')),
    obliged_id      TEXT NOT NULL DEFAULT '',
    subject_id      TEXT NOT NULL DEFAULT '',
    value           REAL NOT NULL DEFAULT 0,
    -- The same pairing as `commitment.expires_turn` / `expires_on`, for the same reason.
    due_turn        INTEGER,
    due_on          TEXT,
    PRIMARY KEY (commitment_id, seq)
);

CREATE INDEX ix_commitment_term_world ON commitment_term(world_id);

-- ---------------------------------------------------------------------------
-- Events: the causal spine of the world
-- ---------------------------------------------------------------------------

CREATE TABLE event (
    id                INTEGER PRIMARY KEY,
    world_id          TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    turn              INTEGER NOT NULL,
    date              TEXT NOT NULL,
    actor_nation_id   TEXT REFERENCES nation(id) ON DELETE SET NULL,

    -- CLOSED enum. A model cannot invent a new event kind, and in particular cannot
    -- mint a gating kind under a name the rules do not recognise.
    kind              TEXT NOT NULL CHECK (kind IN (
                          'genesis',
                          'order_issued','order_rejected',
                          'move','battle','siege','territory_transfer',
                          'casus_belli','war_declared','peace_signed','ceasefire',
                          'alliance_formed','alliance_broken',
                          'treaty_signed','treaty_broken','trade_agreement',
                          'diplomatic_message','ultimatum','envoy_dispatched',
                          'commitment_made','commitment_broken','commitment_fulfilled',
                          'economic_reform','construction','unit_raised','unit_disbanded',
                          'unrest','revolution','coup','election','leader_change',
                          'famine','plague','disaster','discovery',
                          'espionage','sabotage','narrative')),

    -- ENGINE events are written only by the applier, never by a model diff. Every
    -- privileged rule requires origin='ENGINE' on the event it cites.
    origin            TEXT NOT NULL CHECK (origin IN ('ENGINE','MODEL')),

    visibility        TEXT NOT NULL DEFAULT 'public'
                      CHECK (visibility IN ('public','bilateral','secret')),
    text              TEXT NOT NULL DEFAULT '',
    created_by_batch  INTEGER
);

-- The causes[] edge list. Self-reference is impossible; a cause must precede its effect.
CREATE TABLE event_cause (
    event_id    INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    cause_id    INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, cause_id),
    CHECK (event_id <> cause_id)
);


-- **Backing a side without becoming one.** A patron ships equipment, sends advisors,
-- passes intelligence, mans the air defences or pays the bills, and stays out of the
-- war. Its own table rather than a `commitment` kind, for the reason the nuclear
-- layer got one: the commitment kinds are a closed CHECK and widening one is
-- save-invalidating, while a new table is additive.
CREATE TABLE support (
    world_id     TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- Who is backing whom. A patron is never made a belligerent by this table: nothing
    -- here writes `relation.at_war`, and `defensive_pact_allies` does not read it. That
    -- is the whole point -- the Soviet Union armed Hanoi for a decade without being at
    -- war with the United States, and the engine had no way to say so.
    patron_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    client_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- Five, each with a distinct mechanical effect. A kind the engine cannot act on
    -- differently from another is flavour a patron will waste an order on.
    kind         TEXT NOT NULL CHECK (kind IN ('equipment','advisors','intelligence',
                                               'air_defence','funding')),
    -- 0 is not stored: withdrawing deletes the row, so "backed at nothing" and "not
    -- backed" cannot disagree. 3 is the ceiling, and it is a balance number -- see
    -- atlas.engine.support.
    level        REAL NOT NULL DEFAULT 1 CHECK (level > 0 AND level <= 3),
    opened_turn  INTEGER NOT NULL DEFAULT 0,
    -- One row per patron, client and kind: raising a level replaces it rather than
    -- stacking, which is what stops a patron declaring the same shipment ten times.
    PRIMARY KEY (world_id, patron_id, client_id, kind)
);

CREATE INDEX ix_support_client ON support(world_id, client_id);


-- **A colony wanting to stop being one.** 1936 holds about a hundred and forty-five
-- dependencies and not one of them could ever want anything: the unrest layer reads
-- `home_nation_id`, a dependency's home is NULL, so an empire was free ground with no
-- politics in it. Additive table, for the reason `support` got one.
CREATE TABLE independence (
    world_id     TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- A dependency is a *region*, not a nation: it has no government to be restive on
    -- behalf of, which is exactly why the ordinary unrest layer cannot see it --
    -- `held_foreign_provinces` requires `home_nation_id IS NOT NULL` and a dependency's
    -- home is nobody. So the movement is keyed by the ground it wants.
    region_id    TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
    holder_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- How far it has got. Accumulates every turn and is spent by nothing except
    -- independence itself; a garrison slows it and never reverses it, which is the
    -- historical shape and also the thing that stops a holder sitting on it forever.
    momentum     REAL NOT NULL DEFAULT 0 CHECK (momentum >= 0),
    opened_turn  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (world_id, region_id)
);

CREATE INDEX ix_independence_holder ON independence(world_id, holder_id);

-- Who may see a non-public event. Empty for 'public' events; the visibility query
-- treats public as universally visible without needing a row per nation.
-- Who an event happened *to*. `event.actor_nation_id` says who did it; without this every
-- consumer that needs the other end guesses, and the guesses were wrong -- see
-- atlas.engine.treaties._aggressor, which blamed the wrong country for a broken pact.
CREATE TABLE event_participant (
    event_id   INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    role       TEXT NOT NULL
               CHECK (role IN ('actor','target','defender','beneficiary','aggrieved','mediator')),
    seq        INTEGER NOT NULL DEFAULT 0,
    nation_id  TEXT REFERENCES nation(id) ON DELETE CASCADE,
    region_id  TEXT REFERENCES region(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, role, seq),
    CHECK ((nation_id IS NULL) <> (region_id IS NULL))
);

CREATE INDEX ix_event_participant_nation ON event_participant(nation_id, role);

-- **What a government is playing for, standing, across turns.** Strategy, not doctrine and
-- not a directive: a doctrine is a standing commitment about *method* that changes the
-- arithmetic, a directive is the player's private note, and this is the answer to "which
-- ending am I pursuing and what am I doing about it".
--
-- **Deliberately grammar-agnostic.** `objective` is an open vocabulary decided in
-- `engine.strategy.OBJECTIVES`, exactly as `doctrine_stance.axis` is decided in
-- `engine.doctrines.AXES` and `world_fact.predicate` in `engine.facts`. It does NOT name
-- the three victory conditions and must not: the victory frame is being reworked, and a
-- table that hard-coded today's grammar would need a rebuild the moment it changed. What
-- the machinery knows is that an objective has milestones, postures, red lines and replan
-- conditions -- never how many endings the game has.
--
-- The list columns hold JSON. A milestone set is a sequence a government wrote, read back
-- whole and never joined against, which is the one shape where a blob is honest rather than
-- lazy -- the alternative is four more tables to express "a strategy has some milestones".
CREATE TABLE nation_strategy (
    id                 TEXT PRIMARY KEY,
    world_id           TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id          TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    -- '' means this government has looked and found no route worth committing to, which is
    -- a legitimate answer and not the absence of one. See `strategy.ABSTAIN`.
    objective          TEXT NOT NULL DEFAULT '',
    because            TEXT NOT NULL DEFAULT '',
    adopted_on         TEXT NOT NULL DEFAULT '',
    adopted_turn       INTEGER NOT NULL DEFAULT 0,
    reviewed_on        TEXT NOT NULL DEFAULT '',
    milestones         TEXT NOT NULL DEFAULT '[]',
    budget_posture     TEXT NOT NULL DEFAULT '',
    mobilisation_posture INTEGER NOT NULL DEFAULT 0,
    red_lines          TEXT NOT NULL DEFAULT '[]',
    war_aims           TEXT NOT NULL DEFAULT '[]',
    minimum_peace      TEXT NOT NULL DEFAULT '[]',
    replan_on          TEXT NOT NULL DEFAULT '[]',
    -- How many consecutive reviews another route has led by a clear margin. Hysteresis
    -- state: a government that switched on every crossover would thrash, and the fitness
    -- was measured changing hands in 15 of 18 runs.
    -- **What this government last looked like, so a trend has something to be a trend
    -- against.** Fitness is read from rates, and a rate needs two observations. Storing the
    -- previous reading on the plan itself rather than keeping a history table is the same
    -- argument `commitment_term` makes: this is one row per government, read back whole,
    -- never joined against, and a rewind takes it with the plan it belongs to.
    seen_on            TEXT NOT NULL DEFAULT '',
    seen_ground        REAL NOT NULL DEFAULT 0,
    seen_ratio         REAL NOT NULL DEFAULT 0,
    seen_index         REAL NOT NULL DEFAULT 0,
    contender          TEXT NOT NULL DEFAULT '',
    contender_streak   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (world_id, nation_id)
);

-- **Every revision, with what caused it.** The instrumentation is the point: a strategy
-- layer nobody can audit is a strategy layer that cannot be shown to be working, and the
-- question this answers is *why did this nation change its mind*.
--
-- A table rather than events, because a trigger is structured and wants querying by kind.
-- Fingerprinted and rewound like everything else, so a rewind past a replan takes the
-- record of it too.
CREATE TABLE strategy_revision (
    id           TEXT PRIMARY KEY,
    world_id     TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    at_turn      INTEGER NOT NULL DEFAULT 0,
    on_date      TEXT NOT NULL DEFAULT '',
    -- Open vocabulary again, and for the same reason. Today: scheduled_review,
    -- major_shock, objective_abandoned, milestone_reached, commitment_cost, first_plan.
    trigger      TEXT NOT NULL DEFAULT '',
    from_objective TEXT NOT NULL DEFAULT '',
    to_objective   TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_nation_strategy_world
    ON nation_strategy(world_id, nation_id);
CREATE INDEX IF NOT EXISTS ix_strategy_revision_world
    ON strategy_revision(world_id, nation_id, at_turn);


CREATE TABLE event_visibility (
    event_id    INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, nation_id)
);

-- ---------------------------------------------------------------------------
-- Player actions
-- ---------------------------------------------------------------------------

CREATE TABLE action (
    id                  INTEGER PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    turn                INTEGER NOT NULL,
    nation_id           TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    raw_text            TEXT NOT NULL,
    parsed_json         TEXT,
    validation_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK (validation_status IN ('pending','accepted','rejected','failed')),
    rejection_reason    TEXT,
    -- Accepted actions are materialised as ENGINE events so that diffs resulting from
    -- a player order have something legal to cite.
    event_id            INTEGER REFERENCES event(id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Diffs: every mutation passes through here first
-- ---------------------------------------------------------------------------

CREATE TABLE diff_batch (
    id                  INTEGER PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    turn                INTEGER NOT NULL,
    proposer_role       TEXT NOT NULL
                        CHECK (proposer_role IN ('world_sim','diplomacy','parser',
                                                 'tracked_agent','engine','arbiter')),
    proposer_model      TEXT,
    -- Subject of the information-asymmetry rule. NULL for world_sim, which is a
    -- deliberate omniscient referee rather than a participant.
    proposer_nation_id  TEXT REFERENCES nation(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','applied','rejected','partial')),
    rejection_reason    TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE diff (
    id                  INTEGER PRIMARY KEY,
    batch_id            INTEGER NOT NULL REFERENCES diff_batch(id) ON DELETE CASCADE,
    seq                 INTEGER NOT NULL,
    target_table        TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    field               TEXT NOT NULL,

    -- 'set' writes new_json outright and checks expected_prior_json (compare-and-swap);
    -- 'adjust' adds a delta. Distinct ops, never both -- a diff carrying a 'set' op and
    -- a delta is self-contradictory and there is no honest way to resolve it.
    op                  TEXT NOT NULL CHECK (op IN ('set','adjust','insert','delete')),
    old_json            TEXT,
    new_json            TEXT,
    expected_prior_json TEXT,

    -- Deliberately NOT a foreign key. See the header note.
    reason_event_id     INTEGER,

    -- Defaults to the intersection of the citing events' visibility, so a covert cause
    -- cannot write state that everyone can observe.
    visibility_scope    TEXT NOT NULL DEFAULT 'public'
                        CHECK (visibility_scope IN ('public','bilateral','secret')),

    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','applied','rejected')),
    rejected_by_rule    TEXT,
    rejection_reason    TEXT,
    UNIQUE (batch_id, seq)
);

-- ---------------------------------------------------------------------------
-- Consolidation, routing, provenance
-- ---------------------------------------------------------------------------

-- Older turns are summarised into digests and fed to later calls instead of raw
-- history. This is what makes turn 200 reachable at all.
CREATE TABLE digest (
    id                  INTEGER PRIMARY KEY,
    world_id            TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    turn_from           INTEGER NOT NULL,
    turn_to             INTEGER NOT NULL,
    level               INTEGER NOT NULL DEFAULT 1,
    -- Digests are built per visibility scope, so a secret never leaks into the
    -- summary handed to a nation that could not see the underlying events.
    scope_nation_id     TEXT REFERENCES nation(id) ON DELETE CASCADE,
    summary_text        TEXT NOT NULL
);

-- Every routing decision is logged so the player can see which model handled which
-- call, and so a silent misroute is detectable after the fact.
CREATE TABLE routing_log (
    id              INTEGER PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    turn            INTEGER NOT NULL,
    role            TEXT NOT NULL,
    provider        TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    served_model    TEXT,
    audit_verdict   TEXT CHECK (audit_verdict IN ('MATCH','MISMATCH','UNVERIFIED')),
    complexity      REAL,
    band            TEXT,
    duration_ms     INTEGER,
    outcome         TEXT,
    created_at      TEXT NOT NULL,
    -- **Whose government this call was for**, empty where the call was not about one
    -- nation (`world_sim`, an organisation session, the arbiter). Without it the log can
    -- say a Claude model answered and cannot say who it was playing, which is the whole
    -- question in an EVE match.
    --
    -- Last, after `created_at`, because migration 51 adds it with `ADD COLUMN` and that
    -- appends: declared anywhere else, a fresh world and an upgraded save disagree about
    -- this table's column order (`test_fingerprint_canonical`).
    nation_id       TEXT NOT NULL DEFAULT ''
);

-- What a government asked for and did not get, so that its next briefing can say so.
--
-- The engine has always computed these sentences and always thrown them away as far as
-- the asker was concerned: they go to `JumpReport.rejections`, which is the *player's*
-- turn report, and no view builder has ever read one. So a tracked power refused for a
-- rule it was never told proposed the identical thing again the next turn, and the turn
-- after -- measured at nine identical refusals in twelve turns for one power on one tape.
--
-- HISTORY, and append-only: it records what was said rather than what is true, and
-- `replay.rewind_to` truncates it by turn like every other row in its class. It is not,
-- however, history nothing reads: `engine.refusals.briefing_lines` quotes a row into the
-- next turn's prompt, so the registry marks it `gates_play` and a paired run compares it.
-- Out of the digest, in the comparison -- see `state/tables.py`.
CREATE TABLE refusal (
    id              INTEGER PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id       TEXT NOT NULL,
    turn            INTEGER NOT NULL,
    -- Which field of the answer it was about (`reinforce`, `trade_intents`, `diffs`...),
    -- so the briefing can group and so a channel that is refused every turn is countable.
    channel         TEXT NOT NULL,
    -- The sentence the engine already wrote, verbatim. Not a code: these are prose because
    -- every refusal in this engine is prose that names what to write instead.
    reason          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indices
-- ---------------------------------------------------------------------------

CREATE INDEX ix_event_world_turn      ON event(world_id, turn);
CREATE INDEX ix_event_kind_origin     ON event(world_id, kind, origin);
CREATE INDEX ix_event_actor           ON event(actor_nation_id);
CREATE INDEX ix_event_vis_nation      ON event_visibility(nation_id);
CREATE INDEX ix_event_cause_cause     ON event_cause(cause_id);
CREATE INDEX ix_region_owner          ON region(owner_nation_id);
CREATE INDEX ix_region_world          ON region(world_id);
CREATE INDEX ix_unit_region           ON unit(region_id);
CREATE INDEX ix_unit_nation           ON unit(nation_id);
CREATE INDEX ix_city_region           ON city(region_id);
CREATE INDEX ix_adjacency_b           ON region_adjacency(region_b);
CREATE INDEX ix_contest_world          ON contest(world_id);
CREATE INDEX ix_contest_region         ON contest(region_id);
CREATE INDEX ix_technology_world       ON technology(world_id);
CREATE INDEX ix_arsenal_world          ON arsenal(world_id);
CREATE INDEX ix_doctrine_nation ON doctrine(world_id, nation_id);
CREATE INDEX ix_doctrine_stance_nation ON doctrine_stance(world_id, nation_id);
CREATE INDEX ix_organisation_world     ON organisation(world_id);
CREATE INDEX ix_org_member_nation      ON organisation_member(nation_id);
CREATE INDEX ix_sanction_world         ON sanction(world_id, target_nation_id);
CREATE INDEX ix_construction_world     ON construction(world_id);
CREATE INDEX ix_construction_nation    ON construction(nation_id);
CREATE INDEX ix_objective_world        ON military_objective(world_id);
CREATE INDEX ix_objective_nation       ON military_objective(nation_id);
CREATE INDEX ix_region_geometry_world  ON region_geometry(world_id);
CREATE INDEX ix_region_geometry_key    ON region_geometry(geo_key);
CREATE INDEX ix_relation_b            ON relation(nation_b);
CREATE INDEX ix_diff_batch            ON diff(batch_id);
CREATE INDEX ix_diff_target           ON diff(target_table, target_id);
CREATE INDEX ix_diff_reason           ON diff(reason_event_id);
CREATE INDEX ix_batch_world_turn      ON diff_batch(world_id, turn);
CREATE INDEX ix_action_world_turn     ON action(world_id, turn);
CREATE INDEX ix_commitment_world      ON commitment(world_id, status);
CREATE INDEX ix_commitment_nation_n   ON commitment_nation(nation_id);
CREATE INDEX ix_digest_world          ON digest(world_id, turn_from, turn_to);
CREATE INDEX ix_nation_world          ON nation(world_id);
CREATE INDEX ix_nation_tracked        ON nation(world_id, is_tracked);
CREATE INDEX ix_routing_world_turn    ON routing_log(world_id, turn);
CREATE INDEX ix_refusal_world_nation  ON refusal(world_id, nation_id, turn);

-- ---------------------------------------------------------------------------
-- Focus Mode cap, enforced by the database
-- ---------------------------------------------------------------------------
--
-- The cap of 5 is enforced here as well as in the promotion logic. A multi-front war
-- is exactly when Focus Mode matters most and exactly when a heuristic would quietly
-- promote eight nations and blow the turn budget. A trigger cannot be forgotten by a
-- future code path the way a guard clause can.

CREATE TRIGGER trg_focus_cap_insert
BEFORE INSERT ON nation
WHEN NEW.is_tracked = 1
BEGIN
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM nation
        WHERE world_id = NEW.world_id AND is_tracked = 1
    ) >= 5
    THEN RAISE(ABORT, 'focus cap exceeded: at most 5 tracked nations per world')
    END;
END;

CREATE TRIGGER trg_focus_cap_update
BEFORE UPDATE OF is_tracked ON nation
WHEN NEW.is_tracked = 1 AND OLD.is_tracked = 0
BEGIN
    SELECT CASE WHEN (
        SELECT COUNT(*) FROM nation
        WHERE world_id = NEW.world_id AND is_tracked = 1
    ) >= 5
    THEN RAISE(ABORT, 'focus cap exceeded: at most 5 tracked nations per world')
    END;
END;
