"""Forward-only, additive-only schema migrations.

ATLAS deliberately has no general migration framework: a schema change invalidates saved
worlds, and ``newgame`` is cheap by design. That trade is right for changes that
*reinterpret* existing data, and wrong for changes that only *add* to it -- there is no
reason a new column should cost the player the game they are in the middle of.

So this is the narrow version, and the narrowness is the point:

**Additive only, enforced.** Every statement is checked before it runs. ``CREATE TABLE``,
``CREATE INDEX``, ``ALTER TABLE ... ADD COLUMN`` and backfills are allowed; ``DROP``,
``DELETE``, ``RENAME`` and ``ADD COLUMN ... NOT NULL`` without a default are refused. A
migration that wants to do any of those is a migration that should invalidate the world
instead, and it will fail loudly here rather than quietly destroying a save.

**And a backfill may only fill what the same migration added.** ``UPDATE`` used to be
allowed on its first word, so ``UPDATE nation SET treasury = 0`` satisfied a guard whose
whole purpose is that a migration cannot destroy a save. :func:`check_migration` reads a
migration's statements in order and refuses an UPDATE that assigns to any column the
migration did not itself create.

**Forward only.** There is no downgrade path. Opening a world written by a *newer* ATLAS
is an error, not an attempted rollback -- the alternative is silently discarding state
the running version does not understand.

**One transaction per version.** A migration either lands whole or not at all, and the
version stamp moves with it, so an interrupted upgrade cannot leave a half-migrated
world that looks fine.

The authority is ``PRAGMA user_version``, not ``world.schema_version``: the pragma exists
before any row does, which matters because a database is created empty and populated
afterwards.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

__all__ = [
    "SCHEMA_VERSION",
    "MIGRATIONS",
    "Migration",
    "MigrationRefused",
    "NewerWorldError",
    "check_migration",
    "current_version",
    "migrate",
    "needs_migration",
    "rebuilds_pending",
]


class MigrationRefused(RuntimeError):
    """A migration statement is not additive, so it will not be run."""


class NewerWorldError(RuntimeError):
    """This world was written by a newer ATLAS than the one opening it."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    summary: str
    statements: tuple[str, ...]
    #: **This migration rewrites a table and therefore invalidates existing saves.**
    #:
    #: Every other migration here is additive, and the guard below refuses anything that
    #: is not -- that guard is what stops a future migration eating somebody's world, and
    #: it is not weakened lightly. A closed CHECK constraint cannot be widened in SQLite
    #: without rebuilding the table, and rebuilding means DROP and RENAME, so a widening
    #: is genuinely outside what the guard can allow.
    #:
    #: Setting this is a *decision*, not a convenience: it says the person who wrote this
    #: migration accepted that worlds written before it cannot be opened after it. The
    #: guard is skipped for these statements and for no others, `reason` has to say why,
    #: and a test pins the set of migrations allowed to carry it.
    invalidates_saves: bool = False
    #: Why the invalidation was accepted. Required when `invalidates_saves` is set.
    reason: str = ""


#: Statements that only ever add. Checked as a prefix against the normalised statement.
_ALLOWED = (
    "create table",
    "create index",
    "create unique index",
    "create trigger",
    "create view",
    "alter table",     # narrowed further below: must be ADD COLUMN
    "update",          # backfilling a new column from existing data
    "insert",
)

#: Destructive *forms*, not bare keywords. Matching ``\bdelete\b`` anywhere looks
#: stricter and is wrong: every ``REFERENCES ... ON DELETE CASCADE`` in a perfectly
#: additive CREATE TABLE trips it, so the guard would refuse the very migrations it
#: exists to allow. These phrases cannot occur except in a statement that removes
#: something.
_FORBIDDEN = (
    r"\bdrop\s+(table|column|index|view|trigger)\b",
    r"\bdelete\s+from\b",
    r"\brename\s+(to|column)\b",
    r"\btruncate\b",
)


def _normalise(statement: str) -> str:
    return " ".join(statement.strip().lower().split())


def _new_columns(statement: str) -> tuple[set[tuple[str, str]], set[str]]:
    """What a statement makes new: ``({(table, column)}, {whole new table})``.

    A table this migration creates is new in its entirety, so a backfill into it is
    additive whatever it touches -- tracking the table name is cheaper and safer than
    parsing its column list, and it cannot be wrong in the dangerous direction.
    """
    text = _normalise(statement)
    added = re.match(
        r"^alter\s+table\s+([a-z_][a-z0-9_]*)\s+add\s+column\s+([a-z_][a-z0-9_]*)", text
    )
    if added:
        return {(added.group(1), added.group(2))}, set()
    created = re.match(r"^create\s+table(?:\s+if\s+not\s+exists)?\s+([a-z_][a-z0-9_]*)", text)
    if created:
        return set(), {created.group(1)}
    return set(), set()


def _update_targets(text: str) -> tuple[str, tuple[str, ...]] | None:
    """``(table, columns)`` an ``UPDATE`` assigns to, or ``None`` if it is not an UPDATE.

    An empty column tuple means the SET clause could not be read, which the caller treats
    as a refusal: a migration statement nobody can parse is not one to run against a save.
    """
    head = re.match(r"^update\s+([a-z_][a-z0-9_]*)\s+set\s+(.+)$", text)
    if head is None:
        return None
    table, body = head.group(1), head.group(2)

    # A depth-aware scan, because both the WHERE boundary and the commas between
    # assignments can appear inside a function call -- `COALESCE(a, b)` is one assignment
    # and `SET x = (SELECT 1 WHERE y)` has no top-level WHERE.
    depth = 0
    cut = len(body)
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and body.startswith(" where ", index):
            cut = index
            break

    assignments, depth, start = body[:cut], 0, 0
    parts: list[str] = []
    for index, char in enumerate(assignments):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(assignments[start:index])
            start = index + 1
    parts.append(assignments[start:])

    columns: list[str] = []
    for part in parts:
        target = re.match(r"\s*([a-z_][a-z0-9_]*)\s*=", part)
        if target is None:
            return table, ()
        columns.append(target.group(1))
    return table, tuple(columns)


def check_statement(
    statement: str,
    *,
    added: set[tuple[str, str]] | None = None,
    new_tables: set[str] | None = None,
) -> None:
    """Raise unless this statement only adds. Public so tests can assert the boundary.

    ``added`` and ``new_tables`` are what the *same migration* has created before this
    statement, and they are what makes an ``UPDATE`` checkable. Without them an UPDATE has
    no context to be additive in and is refused -- which is deliberate, so a caller that
    forgets to supply them gets a refusal rather than a hole. :func:`check_migration` is
    the caller that has them.
    """
    text = _normalise(statement)
    if not text:
        return

    for pattern in _FORBIDDEN:
        if re.search(pattern, text):
            raise MigrationRefused(
                f"refusing a migration statement containing a destructive keyword: "
                f"{statement.strip()[:120]!r}. Migrations here are additive only; a change "
                "that removes or rewrites data should invalidate the world instead."
            )

    if not text.startswith(_ALLOWED):
        raise MigrationRefused(
            f"refusing an unrecognised migration statement: {statement.strip()[:120]!r}"
        )

    if text.startswith("alter table") and "add column" not in text:
        raise MigrationRefused(
            f"ALTER TABLE is allowed only for ADD COLUMN: {statement.strip()[:120]!r}"
        )

    # SQLite cannot add a NOT NULL column to a populated table without a default, and
    # discovering that mid-upgrade on a real save is exactly the failure this guard is
    # for.
    # `\bdefault\b` and not the bare substring, which is satisfied by a
    # column *named* `default_posture` or `is_defaulted`, so a NOT NULL column with no
    # DEFAULT clause at all passed the one guard standing between it and a populated save.
    # An underscore is a word character, so the boundaries match the keyword and not a name
    # that merely contains it.
    if ("add column" in text and "not null" in text
            and not re.search(r"\bdefault\b", text)):
        raise MigrationRefused(
            f"a NOT NULL column needs a DEFAULT to be addable to an existing world: "
            f"{statement.strip()[:120]!r}"
        )

    # **An UPDATE was allowed on the strength of its first word alone**, so `UPDATE nation
    # SET treasury = 0` passed a guard whose entire purpose is that a migration cannot
    # destroy a save. The prefix list calls it "backfilling a new column from existing
    # data"; nothing checked that it was.
    #
    # It is checkable, so it is checked: every column an UPDATE assigns to must be one this
    # same migration added, or belong to a table this same migration created. All three
    # shipped backfills already satisfy it -- they were written to the rule, and now the
    # rule exists.
    if text.startswith("update"):
        parsed = _update_targets(text)
        if parsed is None or not parsed[1]:
            raise MigrationRefused(
                f"cannot read which columns this UPDATE assigns to, so it cannot be shown "
                f"to be additive: {statement.strip()[:120]!r}"
            )
        table, columns = parsed
        if table not in (new_tables or set()):
            trespass = [c for c in columns if (table, c) not in (added or set())]
            if trespass:
                raise MigrationRefused(
                    f"a migration may only backfill columns it adds itself, and "
                    f"{table}.{trespass[0]} already held player data: "
                    f"{statement.strip()[:120]!r}. A change that rewrites existing state "
                    "should invalidate the world instead."
                )


def check_migration(migration: Migration) -> None:
    """Check every statement of one migration, in order, with its own history in hand.

    The order is the point: a backfill is additive only *after* the column it fills has
    been added, and reading the statements in sequence is what makes that provable rather
    than trusted. This is the entry point :func:`migrate` uses and the one a new migration
    should be tested through.
    """
    if migration.invalidates_saves:
        # Deliberately outside the additive rule; see `Migration.invalidates_saves`. The
        # decision is recorded rather than the check quietly skipped.
        if not migration.reason:
            raise MigrationRefused(
                f"migration {migration.version} invalidates saves and says nothing about "
                "why. A migration that costs somebody their world has to state its reason."
            )
        return
    added: set[tuple[str, str]] = set()
    new_tables: set[str] = set()
    for statement in migration.statements:
        check_statement(statement, added=added, new_tables=new_tables)
        columns, tables = _new_columns(statement)
        added |= columns
        new_tables |= tables


#: Every migration, in order. Version 1 is the original schema and has no entry: a world
#: at version 1 is one created before migrations existed.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=2,
        summary="dynamic borders: contests accumulate pressure, regions remember their home",
        statements=(
            # Who a region belongs to historically, as distinct from who holds it now.
            # Without this a conquered province is indistinguishable from a home one, and
            # a peace settlement has no way to say which is being returned.
            "ALTER TABLE region ADD COLUMN home_nation_id TEXT",
            # Existing worlds: whoever owns it now is its home. True at world creation,
            # which is the only moment an un-migrated world can be reasoned about.
            "UPDATE region SET home_nation_id = owner_nation_id WHERE home_nation_id IS NULL",
            # A contest is a standing push by one nation on one region. Several nations
            # may press the same region at once -- each row accumulates independently,
            # and the first to reach the threshold takes the ground.
            """
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
            )
            """,
            "CREATE INDEX ix_contest_world ON contest(world_id)",
            "CREATE INDEX ix_contest_region ON contest(region_id)",
        ),
    ),
    Migration(
        version=3,
        summary="a region is composed of one or more pack shapes",
        statements=(
            # Natural Earth mixes administrative levels between countries, so one pack
            # entry per region gives Yugoslavia 392 regions and Germany 16. Grouping
            # entries lets a scenario choose a sane count per power without a second
            # bake: geometry stays immutable in the pack, and which shapes compose a
            # region becomes world data.
            """
            CREATE TABLE region_geometry (
                world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                region_id  TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
                geo_key    TEXT NOT NULL,
                PRIMARY KEY (region_id, geo_key)
            )
            """,
            # Existing worlds are one shape per region, which is exactly what the old
            # column already said. Backfilling keeps them working unchanged.
            "INSERT INTO region_geometry (world_id, region_id, geo_key) "
            "SELECT world_id, id, geo_key FROM region WHERE geo_key IS NOT NULL",
            "CREATE INDEX ix_region_geometry_world ON region_geometry(world_id)",
            "CREATE INDEX ix_region_geometry_key ON region_geometry(geo_key)",
        ),
    ),
    Migration(
        version=4,
        summary="a tracked power declares what it is trying to do with its army",
        statements=(
            # Standing intent, not an outcome and not a write path. Every territory
            # column stays engine-only; an objective says what a power is *trying* to do,
            # and reachability, whether a push becomes a battle, and who wins it are all
            # decided afterwards in engine code.
            """
            CREATE TABLE military_objective (
                id             TEXT PRIMARY KEY,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                region_id      TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
                kind           TEXT NOT NULL
                               CHECK (kind IN ('push', 'hold', 'withdraw_from')),
                declared_turn  INTEGER NOT NULL DEFAULT 0,
                UNIQUE (nation_id, region_id)
            )
            """,
            "CREATE INDEX ix_objective_world ON military_objective(world_id)",
            "CREATE INDEX ix_objective_nation ON military_objective(nation_id)",
        ),
    ),
    Migration(
        version=5,
        summary="things are built over time rather than appearing when paid for",
        statements=(
            # How long a kind takes to build. Zero means it appears the turn it is
            # ordered, which is what everything did before this column existed.
            "ALTER TABLE unit_template ADD COLUMN build_turns INTEGER NOT NULL DEFAULT 0",
            # A yard with something in it. Paid for when ordered and delivered when
            # finished, so a half-built battleship is money already spent -- which is what
            # makes a building programme a commitment rather than a shopping list.
            """
            CREATE TABLE construction (
                id              TEXT PRIMARY KEY,
                world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                region_id       TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
                kind            TEXT NOT NULL,
                turns_remaining INTEGER NOT NULL DEFAULT 0,
                started_turn    INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX ix_construction_world ON construction(world_id)",
            "CREATE INDEX ix_construction_nation ON construction(nation_id)",
        ),
    ),
    Migration(
        version=6,
        summary="a yard can hold something other than a ship",
        statements=(
            # What kind of thing is being built. 'unit' is what everything in an existing
            # world is, which is why it is the default.
            "ALTER TABLE construction ADD COLUMN project_kind TEXT NOT NULL DEFAULT 'unit'",
        ),
    ),
    Migration(
        version=7,
        summary="industrial capacity is a thing a nation has, and can build more of",
        statements=(
            # Capacity used to be `round(economy / 20)`, recomputed on the fly -- so it
            # was a consequence of the economy and never a decision. As a column it is
            # something a country can invest in, run down, and lose with a province.
            "ALTER TABLE nation ADD COLUMN industry REAL NOT NULL DEFAULT 0",
            # Existing worlds get the industry their economy implies, which is exactly
            # what the old formula assumed and keeps every queue the size it was.
            "UPDATE nation SET industry = economy * 0.6 WHERE industry = 0",
        ),
    ),
    Migration(
        version=8,
        summary="what a nation has worked out how to do",
        statements=(
            # One row per nation per completed programme. `secret` is set here and read by
            # nothing that decides anything -- what hides a programme is the visibility of
            # the events it wrote, which the engine already enforces everywhere.
            """
            CREATE TABLE technology (
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                tech_id        TEXT NOT NULL,
                completed_turn INTEGER NOT NULL DEFAULT 0,
                secret         INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (nation_id, tech_id)
            )
            """,
            "CREATE INDEX ix_technology_world ON technology(world_id)",
        ),
    ),
    Migration(
        version=9,
        summary="build times are measured in days, so a turn's length means something",
        statements=(
            # `build_turns` counted *turns*, and a turn is a week or a year depending on
            # a button the player presses for pacing reasons. A carrier took eleven weeks
            # of weekly play and ten years of yearly play. Days are the same length in
            # every game.
            "ALTER TABLE unit_template ADD COLUMN build_days REAL NOT NULL DEFAULT 0",
            "ALTER TABLE construction ADD COLUMN days_remaining REAL NOT NULL DEFAULT 0",
            # Thirty, because every balance constant in the engine was calibrated against
            # 30-day turns -- so a world mid-build arrives at exactly the delivery date it
            # was already heading for, provided it was being played monthly, which was the
            # default and the only length any of the tuning assumed.
            "UPDATE unit_template SET build_days = build_turns * 30.0",
            "UPDATE construction SET days_remaining = turns_remaining * 30.0",
        ),
    ),
    Migration(
        version=10,
        summary="nuclear arsenals are state rather than prose",
        statements=(
            # Nuclear weapons were a sentence in a preset's hard constraints and two
            # technology names. Nothing in the engine knew who had one, so nothing could
            # deter anybody and nothing could be launched. This is the state that makes
            # both possible; see :mod:`atlas.engine.nuclear`.
            """
            CREATE TABLE arsenal (
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                warheads       INTEGER NOT NULL DEFAULT 0,
                delivery       TEXT NOT NULL DEFAULT 'none'
                               CHECK (delivery IN ('none','aircraft','missile','sea','triad')),
                PRIMARY KEY (nation_id)
            )
            """,
            "CREATE INDEX ix_arsenal_world ON arsenal(world_id)",
            # **The backfill, and its honest limit.** A finished nuclear programme is the
            # only evidence an existing save carries that anybody has a bomb, so that is
            # what this reads: a nation that completed one gets the arsenal its programme
            # implies, and everybody else gets none.
            #
            # What it cannot recover is a scenario's *starting* arsenals, because before
            # this migration there were none to record -- Modern Day's nine nuclear powers
            # were prose in their character blurbs. So an old Modern Day save migrates to a
            # world where nobody is armed until somebody researches it, and a fresh one
            # seeds the nine. That asymmetry is deliberate: the alternative is a migration
            # that hardcodes one scenario's roster, which would be a migration that knows
            # what era it is.
            """
            INSERT INTO arsenal (world_id, nation_id, warheads, delivery)
            SELECT world_id, nation_id,
                   CASE WHEN MAX(tech_id = 'warhead_modernisation') THEN 8 ELSE 3 END,
                   CASE WHEN MAX(tech_id = 'warhead_modernisation')
                        THEN 'missile' ELSE 'aircraft' END
              FROM technology
             WHERE tech_id IN ('atomic_programme', 'warhead_modernisation')
             GROUP BY world_id, nation_id
            """,
        ),
    ),
    Migration(
        version=11,
        summary="international bodies: a UN that convenes, and the sanctions it imposes",
        statements=(
            # An organisation is a `nation` row with no territory carrying this row. See
            # schema.sql and :mod:`atlas.engine.organisation` for why that representation
            # rather than a table of its own: the nation row is what makes it able to
            # author events and hold a relation to every power.
            """
            CREATE TABLE organisation (
                nation_id      TEXT PRIMARY KEY REFERENCES nation(id) ON DELETE CASCADE,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                kind           TEXT NOT NULL DEFAULT 'universal'
                               CHECK (kind IN ('universal', 'alliance')),
                charter_text   TEXT NOT NULL DEFAULT '',
                authority      REAL NOT NULL DEFAULT 50,
                convened_turn  INTEGER
            )
            """,
            """
            CREATE TABLE organisation_member (
                organisation_id TEXT NOT NULL
                                REFERENCES organisation(nation_id) ON DELETE CASCADE,
                nation_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                is_permanent    INTEGER NOT NULL DEFAULT 0
                                CHECK (is_permanent IN (0,1)),
                joined_turn     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (organisation_id, nation_id)
            )
            """,
            """
            CREATE TABLE sanction (
                id               TEXT PRIMARY KEY,
                world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                organisation_id  TEXT NOT NULL
                                 REFERENCES organisation(nation_id) ON DELETE CASCADE,
                target_nation_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                severity         REAL NOT NULL DEFAULT 1,
                opened_turn      INTEGER NOT NULL DEFAULT 0,
                until_turn       INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX ix_organisation_world ON organisation(world_id)",
            "CREATE INDEX ix_org_member_nation ON organisation_member(nation_id)",
            "CREATE INDEX ix_sanction_world ON sanction(world_id, target_nation_id)",
            # **No backfill, and unlike schema 10 there is nothing to regret about that.**
            # An organisation is seeded by a scenario or it does not exist; there is no
            # evidence in an old save from which one could be inferred, and inventing a
            # UN inside a running 1936 game would be the migration deciding what era it
            # is. Existing worlds get the tables and no rows, which is correct for every
            # one of them.
        ),
    ),
    Migration(
        12,
        "the air layer: who holds the sky over a province",
        (
            """
            CREATE TABLE air_control (
                world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                region_id        TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
                holder_nation_id TEXT REFERENCES nation(id) ON DELETE SET NULL,
                margin           REAL NOT NULL DEFAULT 0,
                last_turn        INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (world_id, region_id)
            )
            """,
            "CREATE INDEX ix_air_control_region ON air_control(region_id)",
            # **No backfill, and none is possible or wanted.** Air control is the product
            # of an air battle, and no air battle has ever been fought in a world written
            # before this. An empty table is exactly right: `superiority_term` returns 0.0
            # for a region with no row, so an existing save resolves its ground war on the
            # same numbers it always did until the first aircraft meet over it.
        ),
    ),
    Migration(
        13,
        "leaders: who is actually running a country",
        (
            """
            CREATE TABLE leader (
                id               TEXT PRIMARY KEY,
                world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                name             TEXT NOT NULL,
                title            TEXT NOT NULL DEFAULT '',
                traits_json      TEXT NOT NULL DEFAULT '[]',
                agenda           TEXT NOT NULL DEFAULT '',
                took_power_turn  INTEGER NOT NULL DEFAULT 0,
                left_power_turn  INTEGER,
                came_to_power    TEXT NOT NULL DEFAULT 'seeded'
                                 CHECK (came_to_power IN ('seeded','succession','coup',
                                                          'election','revolution')),
                is_current       INTEGER NOT NULL DEFAULT 1
                                 CHECK (is_current IN (0,1))
            )
            """,
            "CREATE INDEX ix_leader_nation ON leader(nation_id, is_current)",
            "CREATE UNIQUE INDEX ux_leader_current ON leader(world_id, nation_id)"
            " WHERE is_current = 1",
            # **No backfill, and deliberately so.** Who runs a country is seeded from a
            # scenario table, and there is nothing in an old save from which a name could
            # be inferred -- a migration that invented one would be the migration deciding
            # who governs a world already in progress. An existing world therefore has no
            # leaders, and the layer is inert in it: every trait multiplier is 1.0 for a
            # nation with no leader and no succession roll is taken, so an old save
            # resolves on exactly the numbers it always did. The same choice, for the same
            # reason, as the air layer at schema 12.
        ),
    ),
    Migration(
        14,
        "strategic resources and the trade that moves them",
        (
            """
            CREATE TABLE resource (
                world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                good       TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
                produced   REAL NOT NULL DEFAULT 0,
                stockpile  REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (world_id, nation_id, good)
            )
            """,
            """
            CREATE TABLE trade_route (
                id          TEXT PRIMARY KEY,
                world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                seller_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                buyer_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                good        TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
                volume      REAL NOT NULL DEFAULT 0,
                opened_turn INTEGER NOT NULL DEFAULT 0,
                CHECK (seller_id <> buyer_id)
            )
            """,
            "CREATE INDEX ix_resource_nation ON resource(nation_id)",
            "CREATE INDEX ix_trade_route_world ON trade_route(world_id, buyer_id)",
            "CREATE INDEX ix_trade_route_seller ON trade_route(seller_id)",
            # **Both CHECK constraints are authored, not widened.** They belong to new
            # tables, so no closed enum anywhere in the schema is touched and no existing
            # save is invalidated -- which is the difference between this and adding a
            # `'victory'` kind to `event`, which the scoring layer had to route around.
            #
            # **No backfill, and the layer is inert without one.** A nation with no
            # `resource` row has no shortfall, so an old world builds at exactly the rate
            # it always did. Inventing endowments for a world already in progress would be
            # the migration deciding which countries have oil -- the same objection that
            # kept schema 13 from inventing governments.
        ),
    ),
    Migration(
        15,
        "agreements can now end badly: who broke one, and when",
        (
            "ALTER TABLE commitment ADD COLUMN breached_by_nation_id TEXT",
            "ALTER TABLE commitment ADD COLUMN breached_turn INTEGER",
            "ALTER TABLE commitment ADD COLUMN expires_turn INTEGER",
            # **No backfill, and none is possible.** Nothing has ever written
            # `status = 'broken'` -- the value has been in the CHECK constraint since
            # schema 1 with no code path reaching it -- so there is no historical breach to
            # attribute. An existing world carries NULLs, which reads correctly as "no
            # agreement in this world has been violated", because none has.
            #
            # Nullable rather than defaulted: a default of 0 for `breached_turn` would make
            # every standing treaty look like it was broken on turn zero.
        ),
    ),
    Migration(
        16,
        "what an agreement actually promised, in terms the engine can check",
        (
            # **`condition_text` is prose and always was.** Nothing could read it, so of
            # nine commitment kinds exactly three could ever be broken -- the pacts, whose
            # promise the engine knew because it was hardcoded. `territory_cession`,
            # `military_access` and `tribute` were values in a closed enum that no code
            # anywhere read, and a `trade_agreement` moved `relation.treaty_status` and not
            # one barrel of oil.
            #
            # A term is the machine-readable half of a promise. It is what makes a breach
            # *derivable* rather than hardcoded, and it keeps the discipline the breach
            # detector already had: a term records what was promised, and whether it is
            # being kept is read from state every turn, so no model can assert a betrayal
            # that did not happen or conceal one that did.
            """
            CREATE TABLE commitment_term (
                commitment_id   INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
                seq             INTEGER NOT NULL,
                world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                clause          TEXT NOT NULL
                                CHECK (clause IN ('no_war','open_trade','cede_region')),
                obliged_id      TEXT NOT NULL DEFAULT '',
                subject_id      TEXT NOT NULL DEFAULT '',
                value           REAL NOT NULL DEFAULT 0,
                due_turn        INTEGER,
                PRIMARY KEY (commitment_id, seq)
            )
            """,
            "CREATE INDEX ix_commitment_term_world ON commitment_term(world_id)",
            # **A new table, so its CHECK constraint is authored rather than widened** --
            # no closed enum anywhere is touched and no existing save is invalidated. The
            # same argument schema 14 made, and the reason this is additive rather than a
            # migration nobody's game survives.
            #
            # **No backfill, and the absence is load-bearing rather than merely tolerable.**
            # An agreement with no terms is read as carrying the terms its *kind* implies,
            # which for the three pacts is exactly the rule that was hardcoded before this
            # existed. So every commitment in every existing world -- and every one a
            # scenario seeds -- behaves precisely as it did, and only agreements made in
            # play carry explicit terms. That is also what keeps both eras' seed
            # fingerprints where they are: a seeded trade agreement does not suddenly open
            # a route it never had.
        ),
    ),
    Migration(
        17,
        "who an event happened to, and not only who did it",
        (
            # `event.actor_nation_id` records the doer. Nothing recorded the done-to, so
            # every consumer needing the other end guessed -- and `treaties._aggressor`
            # guessed wrong in a way that blamed the wrong country for a broken pact:
            # it took the most recent war declaration by *any* party without checking whom
            # they had declared war on.
            #
            # Two typed nullable columns rather than one polymorphic `entity_id`, because
            # the foreign keys are what catch a writer naming something that does not
            # exist. `seq` because a role can repeat and SQLite treats NULLs in a
            # non-integer primary key as distinct, so a key over the nullable columns
            # would silently stop enforcing anything.
            """
            CREATE TABLE event_participant (
                event_id   INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                role       TEXT NOT NULL
                           CHECK (role IN ('actor','target','defender',
                                           'beneficiary','aggrieved')),
                seq        INTEGER NOT NULL DEFAULT 0,
                nation_id  TEXT REFERENCES nation(id) ON DELETE CASCADE,
                region_id  TEXT REFERENCES region(id) ON DELETE CASCADE,
                PRIMARY KEY (event_id, role, seq),
                CHECK ((nation_id IS NULL) <> (region_id IS NULL))
            )
            """,
            "CREATE INDEX ix_event_participant_nation ON event_participant(nation_id, role)",
            # **No backfill, and the absence is the compatibility story.** An event with no
            # participants falls back to the behaviour it has today -- the same trick
            # `commitment_term` used at schema 16. Every event in every existing world has
            # none, so an old save behaves exactly as it did and only newly written events
            # are precise. A backfill would have to *infer* who a war was against from
            # prose, which is the guessing this table exists to end.
        ),
    ),
    Migration(
        18,
        "an agreement takes effect when every party has accepted it, not when one says so",
        (
            # `commitment.status` was writable by three model roles, so one signatory could
            # flip a `pending` agreement to `active` on its own -- and `sync_treaty_status`
            # would then dutifully hand both sides a standing treaty neither of the others
            # had agreed to. "Pending" meant nothing enforceable.
            #
            # Nullable and undefaulted: NULL is "has not accepted", which is the right thing
            # for every party of every agreement in every existing world. A default of 0
            # would read as "accepted on turn zero" and activate the lot.
            "ALTER TABLE commitment_nation ADD COLUMN accepted_turn INTEGER",
        ),
    ),
    Migration(
        19,
        "a deterrent carried by boats is its own thing, not a land-based one with a navy",
        (
            # **The only save-invalidating migration in the project.** `arsenal.delivery`
            # carries a closed CHECK and SQLite cannot widen one without rebuilding the
            # table, which means DROP and RENAME -- outside what the additive guard allows,
            # and correctly so. Alex authorised the invalidation explicitly.
            #
            # Why a fifth tier at all: Britain's 1972 deterrent is four Resolution boats and
            # nothing else, and there was no honest way to say that. `triad` claimed two
            # legs it did not have and read them to the player in those words; `missile`
            # claimed a land-based force it never had and, once boats were gated on the
            # tier, left the deterrent resting on nothing.
            #
            # The rebuild is the 12-step SQLite dance, in order, inside the transaction
            # `migrate` already opens.
            """
            CREATE TABLE arsenal_rebuilt (
                world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                warheads   INTEGER NOT NULL DEFAULT 0 CHECK (warheads >= 0),
                delivery   TEXT NOT NULL DEFAULT 'none'
                           CHECK (delivery IN ('none','aircraft','missile','sea','triad')),
                PRIMARY KEY (world_id, nation_id)
            )
            """,
            "INSERT INTO arsenal_rebuilt (world_id, nation_id, warheads, delivery)"
            " SELECT world_id, nation_id, warheads, delivery FROM arsenal",
            "DROP TABLE arsenal",
            "ALTER TABLE arsenal_rebuilt RENAME TO arsenal",
            # The index went with the table it was on. Rebuilding without it left a
            # migrated world reading arsenals by full scan where a fresh one used an
            # index -- the second half of the same parity gap.
            "CREATE INDEX ix_arsenal_world ON arsenal(world_id)",
        ),
        invalidates_saves=True,
        reason=(
            "widening the closed CHECK on arsenal.delivery to admit a sea-based deterrent "
            "requires rebuilding the table, which SQLite cannot do additively. Authorised "
            "deliberately; worlds written before schema 19 cannot be opened after it."
        ),
    ),
    Migration(
        20,
        "a broken agreement points at the nation that broke it, in a migrated world too",
        (
            # **The second save-invalidating migration, and found by strengthening the
            # schema-parity test rather than by anything failing.** Migration 15 added
            # `breached_by_nation_id` with `ALTER TABLE ADD COLUMN`, and SQLite cannot
            # attach a foreign key that way -- so a fresh world has
            # `REFERENCES nation(id) ON DELETE SET NULL` on that column and a migrated one
            # has nothing at all. Delete a nation from a migrated world and every
            # commitment it broke goes on naming it.
            #
            # The same 12-step rebuild schema 19 used on `arsenal`, and the same reason it
            # cannot be additive: a constraint cannot be added to an existing column.
            """
            CREATE TABLE commitment_rebuilt (
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
                expires_turn          INTEGER
            )
            """,
            "INSERT INTO commitment_rebuilt (id, world_id, kind, status, condition_text,"
            " source_event_id, created_turn, resolved_turn, breached_by_nation_id,"
            " breached_turn, expires_turn)"
            " SELECT id, world_id, kind, status, condition_text, source_event_id,"
            " created_turn, resolved_turn, breached_by_nation_id, breached_turn,"
            " expires_turn FROM commitment",
            "DROP TABLE commitment",
            "ALTER TABLE commitment_rebuilt RENAME TO commitment",
            # The index goes with the table, exactly as `ix_arsenal_world` did at 19.
            "CREATE INDEX ix_commitment_world ON commitment(world_id, status)",
        ),
        invalidates_saves=True,
        reason=(
            "attaching a foreign key to an existing column requires rebuilding the table, "
            "which SQLite cannot do additively. Authorised deliberately alongside schema "
            "19; worlds written before it cannot be opened after it."
        ),
    ),
    Migration(
        21,
        "an attack submarine is not a missile boat, and the count says which is which",
        (
            # **Additive, and deliberately nullable.** There is one `submarine` unit kind
            # per era, so a sea-leg power's every hull counted toward its deterrent --
            # Britain's three boats were all ballistic-missile submarines and so were
            # China's four attack boats, in the same year, out of the same template.
            #
            # NULL is "the scenario did not say", and an undeclared arsenal counts every
            # boat exactly as it did before this column existed. That is what keeps an
            # existing world's deterrent where it was, and what lets an era opt in.
            "ALTER TABLE arsenal ADD COLUMN deterrent_boats INTEGER",
        ),
    ),
    Migration(
        22,
        "a patron can back a side without becoming one",
        (
            # **A new table, not a new commitment kind.** The commitment kinds are a closed
            # CHECK and widening one is save-invalidating -- schema 19 did it once, for the
            # `sea` delivery tier, and it cost every save. The nuclear layer reached the
            # same conclusion for the same reason: an additive table costs nothing and a
            # widened enum costs everybody's game.
            #
            # Nothing here writes `relation.at_war`, which is the whole feature: the Soviet
            # Union armed Hanoi for a decade without being at war with the United States,
            # and the engine had no way to express it.
            """CREATE TABLE support (
                world_id     TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                patron_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                client_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                kind         TEXT NOT NULL CHECK (kind IN ('equipment','advisors',
                                                           'intelligence','air_defence',
                                                           'funding')),
                level        REAL NOT NULL DEFAULT 1 CHECK (level > 0 AND level <= 3),
                opened_turn  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (world_id, patron_id, client_id, kind)
            )""",
            "CREATE INDEX ix_support_client ON support(world_id, client_id)",
        ),
    ),
    Migration(
        23,
        "a colony can want to stop being one",
        (
            # Additive, for the reason 22 was: an empire is a hundred and forty-five regions
            # whose home is nobody, so the unrest layer -- which reads `home_nation_id` --
            # could not see any of them, and an empire was free ground with no politics in it.
            """CREATE TABLE independence (
                world_id     TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                region_id    TEXT NOT NULL REFERENCES region(id) ON DELETE CASCADE,
                holder_id    TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                momentum     REAL NOT NULL DEFAULT 0 CHECK (momentum >= 0),
                opened_turn  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (world_id, region_id)
            )""",
            "CREATE INDEX ix_independence_holder ON independence(world_id, holder_id)",
        ),
    ),
    Migration(
        24,
        "a what-if should not cost you the line you were playing",
        (
            # **Its own table, and deliberately not a column on `world`.** A fork is a copy
            # of the world file rewound to a turn, and the promise that makes it worth
            # having is that the copy *is* the original as it stood -- byte for byte, by
            # fingerprint. `world` is a fingerprinted table, so two lineage columns on it
            # made every fork differ from its own origin by exactly the two columns that
            # say it is a fork. Measured, not guessed: the test asserting the equality was
            # the one that failed.
            #
            # This table is left out of `_FINGERPRINT_TABLES` for the same reason. Lineage
            # is a fact about the *file* -- which line it came from -- not about the world
            # inside it, and a digest of the world should not change because somebody
            # copied it.
            #
            # Additive and empty on every existing save: a world with no row here is an
            # original line, which is what every world before this migration was.
            """CREATE TABLE IF NOT EXISTS world_lineage (
                world_id        TEXT PRIMARY KEY REFERENCES world(id) ON DELETE CASCADE,
                forked_from     TEXT NOT NULL,
                forked_at_turn  INTEGER NOT NULL,
                forked_at       TEXT NOT NULL
            )""",
        ),
    ),
    Migration(
        25,
        "a war nobody can say the point of cannot be settled, only stopped",
        (
            # **Additive, and empty on every existing save**, which is the right reading of
            # a world that predates this: those wars were fought for nothing anybody wrote
            # down, and an empty table says exactly that rather than inventing aims for them.
            #
            # Keyed by the belligerent pair because that is what a war *is* here -- see the
            # note in `schema.sql`. It is diffed (`state.apply.PRIMARY_KEY`) and fingerprinted
            # (`state.bootstrap._FINGERPRINT_TABLES`) like `military_objective`, for the same
            # reason: standing intent that a rewind must put back, or the replay fights a
            # different war.
            #
            # **This moves every built-world digest by the width of one table name**, on a
            # table that is empty in all three shipped eras -- the schema-16, -21 and -23
            # situation, harmless for the same reason: digests are only ever compared within
            # a schema version.
            """CREATE TABLE IF NOT EXISTS war_aim (
                id             TEXT PRIMARY KEY,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                enemy_id       TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                kind           TEXT NOT NULL CHECK (kind IN ('defend', 'recover_cores',
                                                             'annex', 'regime_change',
                                                             'independence', 'demilitarise')),
                subject_id     TEXT NOT NULL DEFAULT '',
                declared_turn  INTEGER NOT NULL DEFAULT 0,
                UNIQUE (world_id, nation_id, enemy_id, kind, subject_id)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_war_aim_war"
            " ON war_aim(world_id, nation_id, enemy_id)",
        ),
    ),
    Migration(
        26,
        "a world that remembers what was done to it, as distinct from what it computes",
        (
            # **Memory, not arithmetic**, and the separation is the point -- see the note in
            # `schema.sql`. A fact hangs off the event that minted it and cascades with it,
            # which is what makes a rewind clean without any special case: the events of the
            # undone turns go, and their facts go with them.
            #
            # `predicate` is deliberately open text against a Python registry rather than a
            # CHECK, because widening a CHECK in SQLite is a table rebuild and the whole
            # value of this layer is that the vocabulary can grow.
            #
            # Additive and empty on every existing save, and it moves every built-world
            # digest by the width of one table name -- the schema-16, -21, -23 and -25
            # situation, harmless for the reason recorded in `state/bootstrap.py`.
            """CREATE TABLE IF NOT EXISTS world_fact (
                id              TEXT PRIMARY KEY,
                world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                subject_kind    TEXT NOT NULL
                                CHECK (subject_kind IN ('nation', 'region',
                                                        'organisation', 'world')),
                subject_id      TEXT NOT NULL,
                predicate       TEXT NOT NULL,
                object_kind     TEXT NOT NULL DEFAULT 'none'
                                CHECK (object_kind IN ('none', 'nation', 'region',
                                                       'organisation', 'text')),
                object_id       TEXT NOT NULL DEFAULT '',
                value           TEXT NOT NULL DEFAULT '',
                start_turn      INTEGER NOT NULL DEFAULT 0,
                end_turn        INTEGER,
                source_event_id INTEGER REFERENCES event(id) ON DELETE CASCADE,
                visibility      TEXT NOT NULL DEFAULT 'public'
                                CHECK (visibility IN ('public', 'bilateral', 'secret')),
                confidence      REAL NOT NULL DEFAULT 1.0
                                CHECK (confidence BETWEEN 0 AND 1)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_world_fact_subject"
            " ON world_fact(world_id, subject_kind, subject_id)",
            "CREATE INDEX IF NOT EXISTS ix_world_fact_predicate"
            " ON world_fact(world_id, predicate)",
        ),
    ),
    Migration(
        27,
        "how long it has gone on, and what it has cost",
        (
            # The ledger a war never had -- see the note in `schema.sql`. Keyed like
            # `war_aim`, diffed and fingerprinted for the same reason: a rewind that put the
            # front back and left a country as tired of the war as it was two turns later
            # would replay the peace talks against the wrong exhaustion.
            #
            # Additive, empty on every existing save (a war fought before this has no
            # ledger, which is the true reading of it), and it moves every built-world
            # digest by the width of one table name -- as at schemas 16, 21, 23, 25 and 26.
            """CREATE TABLE IF NOT EXISTS war_effort (
                id                 TEXT PRIMARY KEY,
                world_id           TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id          TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                enemy_id           TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                opened_turn        INTEGER NOT NULL DEFAULT 0,
                last_turn          INTEGER NOT NULL DEFAULT 0,
                casualties         REAL NOT NULL DEFAULT 0 CHECK (casualties >= 0),
                charged_casualties REAL NOT NULL DEFAULT 0
                                   CHECK (charged_casualties >= 0),
                exhaustion         REAL NOT NULL DEFAULT 0
                                   CHECK (exhaustion BETWEEN 0 AND 100),
                UNIQUE (world_id, nation_id, enemy_id)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_war_effort_war"
            " ON war_effort(world_id, nation_id, enemy_id)",
        ),
    ),
    Migration(
        28,
        "a peace that says what it costs, in terms the engine can carry out",
        (
            # A new table rather than a widened `commitment_term` -- see the note in
            # `schema.sql`: that column's CHECK is closed and widening it in SQLite means
            # rebuilding the table, which is the one thing the migration guard refuses.
            #
            # Additive, empty on every save, and it moves every built-world digest by one
            # more table name, as at 25, 26 and 27.
            """CREATE TABLE IF NOT EXISTS peace_term (
                id             TEXT PRIMARY KEY,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                commitment_id  INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
                seq            INTEGER NOT NULL,
                kind           TEXT NOT NULL CHECK (kind IN ('cede_region',
                                                             'restore_region', 'withdraw',
                                                             'reparations', 'demilitarise',
                                                             'guarantee', 'recognise')),
                obliged_id     TEXT NOT NULL DEFAULT '',
                beneficiary_id TEXT NOT NULL DEFAULT '',
                subject_id     TEXT NOT NULL DEFAULT '',
                value          REAL NOT NULL DEFAULT 0,
                outstanding    REAL NOT NULL DEFAULT 0,
                due_turn       INTEGER,
                UNIQUE (commitment_id, seq)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_peace_term_world"
            " ON peace_term(world_id, commitment_id)",
        ),
    ),
    Migration(
        29,
        "a leader with somebody to govern",
        (
            # Five groups per nation, written lazily -- see the note in `schema.sql`. The
            # model has no channel to it: support is derived from the events the engine
            # already wrote, exactly as exhaustion is derived from the fighting.
            #
            # Additive and empty on every existing save, and it moves every built-world
            # digest by the width of one table name, as at 25, 26, 27 and 28.
            """CREATE TABLE IF NOT EXISTS constituency (
                id         TEXT PRIMARY KEY,
                world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                kind       TEXT NOT NULL CHECK (kind IN ('army', 'industry', 'people',
                                                         'patriots', 'establishment')),
                support    REAL NOT NULL DEFAULT 50 CHECK (support BETWEEN 0 AND 100),
                last_turn  INTEGER NOT NULL DEFAULT 0,
                because    TEXT NOT NULL DEFAULT '',
                UNIQUE (world_id, nation_id, kind)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_constituency_nation"
            " ON constituency(world_id, nation_id)",
        ),
    ),
    Migration(
        30,
        "what your service can tell you",
        (
            # One row per (viewer, target), written lazily -- see the note in `schema.sql`.
            # The model has no channel to it: a network is engine state and an estimate is
            # computed from real state, which is what stops a power inventing what is
            # secretly true.
            #
            # Additive and empty on every existing save, and it moves every built-world
            # digest by the width of one table name, as at 25, 26, 27, 28 and 29.
            """CREATE TABLE IF NOT EXISTS intel_network (
                id               TEXT PRIMARY KEY,
                world_id         TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id        TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                target_nation_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                strength         REAL NOT NULL DEFAULT 22
                                 CHECK (strength BETWEEN 0 AND 100),
                opened_turn      INTEGER NOT NULL DEFAULT 0,
                last_turn        INTEGER NOT NULL DEFAULT 0,
                because          TEXT NOT NULL DEFAULT '',
                UNIQUE (world_id, nation_id, target_nation_id)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_intel_network_viewer"
            " ON intel_network(world_id, nation_id)",
        ),
    ),
    Migration(
        31,
        "who a victim believes did it",
        (
            # One row per (covert act, victim), written only once there is something to
            # believe. The model has no channel to it: a tier is computed from the victim's
            # counterintelligence against the actor's tradecraft, which is what stops a
            # power manufacturing a casus belli by asserting guilt.
            #
            # Additive and empty on every existing save, and it moves every built-world
            # digest by the width of one table name, as at 25 through 30.
            """CREATE TABLE IF NOT EXISTS attribution (
                id                TEXT PRIMARY KEY,
                world_id          TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                event_id          INTEGER NOT NULL,
                actor_nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                victim_nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                confidence        REAL NOT NULL DEFAULT 0
                                  CHECK (confidence BETWEEN 0 AND 100),
                tier              TEXT NOT NULL DEFAULT 'none'
                                  CHECK (tier IN ('none', 'suspected', 'likely',
                                                  'proven')),
                opened_turn       INTEGER NOT NULL DEFAULT 0,
                last_turn         INTEGER NOT NULL DEFAULT 0,
                UNIQUE (world_id, event_id, victim_nation_id)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_attribution_victim"
            " ON attribution(world_id, victim_nation_id)",
        ),
    ),
    Migration(
        32,
        "a service that can be defended against",
        (
            # The active half of counterintelligence, as a column on the pairing that already
            # exists rather than a table of its own -- one fewer migration, and the row is
            # the contest: "A's network in B" and "B's work against it" are one fact seen
            # from two ends.
            #
            # ADD COLUMN with a default, which the migration guard allows and which leaves
            # every existing row reading zero -- so a save written before this behaves
            # exactly as it did, with nobody defending against anybody. It moves every
            # built-world digest, because `intel_network` is fingerprinted and the digest
            # covers its columns; that is the first column-shaped digest move rather than
            # the table-name-shaped ones at 25 through 31, and harmless for the same reason:
            # digests are only ever compared within a schema version.
            "ALTER TABLE intel_network ADD COLUMN counter_intel REAL NOT NULL DEFAULT 0",
        ),
    ),
    Migration(
        33,
        "an operation that was blown",
        (
            # One row per exposed operation. The three factors are stored beside the
            # magnitude they produced so the curve can be decomposed and tuned.
            #
            # Additive and empty on every existing save. It moves every built-world digest
            # by the width of one table name, as at 25 through 31 -- unlike 32, which added
            # a column to a table that is empty in every shipped era and therefore moved
            # nothing.
            """CREATE TABLE IF NOT EXISTS exposure (
                id                TEXT PRIMARY KEY,
                world_id          TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                event_id          INTEGER NOT NULL,
                actor_nation_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                victim_nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                importance        REAL NOT NULL DEFAULT 0
                                  CHECK (importance BETWEEN 0 AND 1),
                guardedness       REAL NOT NULL DEFAULT 0
                                  CHECK (guardedness BETWEEN 0 AND 1),
                mismatch          REAL NOT NULL DEFAULT 0 CHECK (mismatch BETWEEN 0 AND 1),
                magnitude         REAL NOT NULL DEFAULT 0
                                  CHECK (magnitude BETWEEN 0 AND 100),
                casus_belli       INTEGER NOT NULL DEFAULT 0 CHECK (casus_belli IN (0, 1)),
                turn              INTEGER NOT NULL DEFAULT 0,
                UNIQUE (world_id, event_id)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_exposure_actor"
            " ON exposure(world_id, actor_nation_id)",
        ),
    ),
    Migration(
        34,
        "an agreement to pool what you believe",
        (
            # The machine-readable half of an intelligence-sharing clause, hanging off the
            # commitment it belongs to -- the same arrangement `peace_term` has, and for the
            # same reason: `commitment_term.clause` is a closed CHECK and widening it in
            # SQLite means rebuilding the table.
            #
            # Additive and empty on every existing save; it moves every built-world digest
            # by the width of one table name, as at 25 through 31 and 33.
            """CREATE TABLE IF NOT EXISTS intel_term (
                id             TEXT PRIMARY KEY,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                commitment_id  INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
                seq            INTEGER NOT NULL,
                sharer_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                recipient_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                subject_id     TEXT NOT NULL DEFAULT '',
                UNIQUE (world_id, commitment_id, seq)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_intel_term_recipient"
            " ON intel_term(world_id, recipient_id)",
        ),
    ),
    Migration(
        35,
        "an agreement that ends on a date rather than on a turn",
        (
            # **When a promise runs out, in world time.** `expires_turn` counted turns, and
            # a turn is not a duration: a three-month ceasefire written as `turn + 3` ran
            # for three months at monthly pacing and three *years* at yearly. The pace
            # control is for how much detail somebody wants to watch, and it was deciding
            # how long a truce lasted.
            #
            # Additive, and the old column stays. Two reasons, and neither is timidity:
            # a save written before this has no date to convert *to* -- the world's calendar
            # at the turn it was signed is not recoverable from the row -- so the turn
            # remains the fallback for those, and every reader below prefers the date when
            # there is one. The guard that refuses non-additive statements is what makes
            # that the cheap option rather than the careful one.
            "ALTER TABLE commitment ADD COLUMN expires_on TEXT",
            # The same for a term with a deadline: a cession promised "within three turns"
            # is a promise about the world's clock, not about how the player spends theirs.
            "ALTER TABLE commitment_term ADD COLUMN due_on TEXT",
        ),
    ),
    Migration(
        36,
        "a standing measure that runs out on a date",
        (
            # **A sanctions regime is world time.** `until_turn = turn + 8` drained an
            # economy for eight months at monthly pacing and eight *years* at yearly, from
            # the same resolution. Found by the audit of what was left counting turns after
            # armistices and cooldowns moved: this is the third duration of the same shape
            # and the last one that was not honestly about the player's attention.
            #
            # Additive, with the turn column kept for the reason `expires_on` keeps
            # `expires_turn`: a save older than this has no date to convert from.
            "ALTER TABLE sanction ADD COLUMN until_on TEXT",
        ),
    ),
    Migration(
        37,
        "what a country is built to do",
        (
            # **The engine-owned counterpart to the player's standing directives.** A
            # directive is a note somebody wrote to themselves, kept in the workspace, that
            # reorders advice and never acts. This is the opposite in every respect: it is
            # world state, every nation has one, it changes the arithmetic, and a rewind
            # undoes it.
            #
            # `stance` is empty while `shifting_to` is set -- a country reorganising holds
            # neither stance's advantages, and that dip is what changing costs. No third
            # stance and no flag to forget.
            #
            # Additive and empty on every existing save, so a world written before this
            # migrates to one with no doctrines and behaves exactly as it did. It moves
            # every built-world digest by the width of one table name, as at 25 through 31,
            # 33 and 34 -- and by more than that in the three shipped eras, which seed it.
            """CREATE TABLE IF NOT EXISTS doctrine (
                id             TEXT PRIMARY KEY,
                world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
                axis           TEXT NOT NULL CHECK (axis IN ('mobilisation',
                                                             'nuclear_posture',
                                                             'alignment')),
                stance         TEXT NOT NULL DEFAULT '',
                adopted_on     TEXT NOT NULL DEFAULT '',
                adopted_turn   INTEGER NOT NULL DEFAULT 0,
                shifting_to    TEXT NOT NULL DEFAULT '',
                shift_ends_on  TEXT NOT NULL DEFAULT '',
                UNIQUE (world_id, nation_id, axis)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_doctrine_nation"
            " ON doctrine(world_id, nation_id)",
        ),
    ),
    Migration(
        38,
        "an axis catalogue that can grow without a migration",
        (
            # **A closed CHECK on a vocabulary that is open by nature**, which schema 37 got
            # wrong and Codex caught before it cost anything. Four more axes are scoped and
            # each would have needed its own table rebuild -- and a rebuild is the one thing
            # SQLite cannot do additively, because a CHECK can only be dropped by recreating
            # the table.
            #
            # So the answer is the one this schema has reached for twice already: **keep the
            # old thing beside the new one.** `expires_turn` kept its place when `expires_on`
            # arrived, and `due_turn` when `due_on` did. Here it is a table rather than a
            # column, and the shape is identical -- the new one has no CHECK, every row is
            # copied into it, and the old one is left where it is because dropping it would
            # be the destructive statement the guard exists to refuse.
            #
            # Which axes exist is decided in `engine.doctrines.AXES` from now on, the way
            # `world_fact.predicate` is decided in `engine.facts`. Adding one is a code
            # entry.
            """CREATE TABLE IF NOT EXISTS doctrine_stance (
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
            )""",
            "CREATE INDEX IF NOT EXISTS ix_doctrine_stance_nation"
            " ON doctrine_stance(world_id, nation_id)",
            # Every row moves. `doctrine` is empty in every world that exists today -- it
            # shipped in schema 37 and nothing on disk is past 12 -- so in practice this
            # copies nothing, and it is written anyway because "in practice" is not a
            # guarantee and a save from a build in between would lose its doctrines.
            "INSERT OR IGNORE INTO doctrine_stance (id, world_id, nation_id, axis, stance,"
            "  adopted_on, adopted_turn, shifting_to, shift_ends_on)"
            " SELECT id, world_id, nation_id, axis, stance, adopted_on, adopted_turn,"
            "        shifting_to, shift_ends_on FROM doctrine",
        ),
    ),
    Migration(
        version=39,
        summary="an arsenal has a readiness, and neglect degrades it rather than removing it",
        statements=(
            # **Never a warhead count.** The obvious way to make an unmaintained arsenal
            # matter is to take warheads off it, and that recreates the bug the launch
            # stake had: a power is broke exactly when it is losing, so deleting its
            # deterrent for non-payment would disarm it precisely when it needs it. This
            # degrades how well the arsenal *works* and is floored well above zero, so a
            # cornered government always keeps a last-ditch launch.
            #
            # Defaults to 1.0, so every existing world migrates to a fully ready arsenal
            # and nothing about a saved game changes until a turn is played.
            "ALTER TABLE arsenal ADD COLUMN readiness REAL NOT NULL DEFAULT 1.0",
        ),
    ),
    Migration(
        version=40,
        summary="a formation digs in over the months it holds one place, and loses it if it moves",
        statements=(
            # **Earned, not granted.** The first attempt at a defensive layer was a flat
            # bonus for standing on your own soil, and it made the defender measurably
            # worse off: an aggressor who has seized a province is, from that moment, the
            # one defending it, so the bonus slowed the taking of ground and then protected
            # the ground that had been taken. What separates a dug-in defender from a fresh
            # conqueror is time in place, and time in place is state -- so it has to be
            # stored rather than inferred.
            #
            # `entrenched_at` is the region the digging was done in, kept so the mechanic
            # can tell for itself that a formation has moved. Units are relocated from four
            # different places in the engine and resetting from each of them would leave the
            # fifth one, whenever it is written, silently exempt.
            #
            # Both default to "has dug nothing", so an existing world migrates to an army
            # standing in the open and nothing about a saved game changes until a turn is
            # played.
            "ALTER TABLE unit ADD COLUMN entrenchment REAL NOT NULL DEFAULT 0",
            "ALTER TABLE unit ADD COLUMN entrenched_at TEXT",
        ),
    ),
    Migration(
        version=41,
        summary="a country may dig deeper for soldiers, and pays for it every month",
        statements=(
            # **The floor the whole war layer kept hitting.** Replacement, garrisons, the
            # economy and allied support were each built and measured, and the Ukraine front
            # still came down to a defender whose manpower reached zero while its treasury
            # piled up unspent. The size of the pool was a fact about population that
            # nothing a government did could change, and a constraint nobody can act on is
            # not a decision.
            #
            # Zero is peacetime, so an existing world migrates to a country that has called
            # nobody up and nothing about a saved game changes until a turn is played.
            "ALTER TABLE nation ADD COLUMN mobilisation INTEGER NOT NULL DEFAULT 0",
        ),
    ),
    Migration(
        version=42,
        summary="some of an arsenal is battlefield weapons, which deter nothing",
        statements=(
            # **A subset of `warheads`, not a second pile.** Every existing reader counts
            # the total and should go on counting it -- upkeep is paid on the whole
            # arsenal, a launch spends from the whole arsenal -- and what changes is which
            # part of it *deters*. Storing a second independent count would mean every one
            # of those readers had to learn to add two numbers, and one of them would not.
            #
            # Zero on every existing row, so a saved world migrates to an arsenal that is
            # entirely strategic, which is exactly what it was.
            "ALTER TABLE arsenal ADD COLUMN tactical INTEGER NOT NULL DEFAULT 0",
        ),
    ),
    Migration(
        version=43,
        summary="a peace may unseat a government or cap an arsenal; peace_term.kind widens",
        invalidates_saves=True,
        reason=(
            "Widening a closed CHECK means rebuilding the table, which SQLite cannot do in "
            "place -- exactly the case `invalidates_saves` documents. Accepted on Alex's "
            "explicit authorisation to buy two things the engine could not previously say. "
            "`regime_change` has been a war aim since war aims landed and `leverage` reasons "
            "about it by name, with no term to satisfy it: the only way to achieve it was to "
            "conquer the country outright, which is the one outcome a negotiated peace exists "
            "to avoid. And a nuclear arsenal was something a war could destroy and nothing a "
            "peace could limit. **A world written before this opens fine; a world written "
            "after it cannot be opened by an older build**, because the older CHECK refuses "
            "the two new values."
        ),
        statements=(
            # **THIS ONE BREAKS SAVES, and it is the first migration in the project that
            # does.** Every other migration here is additive -- a column with a default, a
            # new table -- precisely so a world written by an older build keeps working.
            # This widens a closed CHECK constraint, which SQLite cannot do in place, so the
            # table is rebuilt. **A world saved by an older build and opened by this one is
            # fine; a world saved by THIS build cannot be opened by an older one**, because
            # the older schema's CHECK will refuse any `depose` or `limit_arsenal` row in it.
            #
            # Done deliberately and with authorisation rather than quietly. The alternative
            # was leaving `regime_change` as a war aim no treaty could ever satisfy -- it is
            # in `waraims.AIM_KINDS`, `leverage` reasons about it by name, and there was no
            # term for it, so the only way to achieve it was to conquer the country outright,
            # which is the single outcome a negotiated peace exists to avoid.
            #
            # The rebuild is the standard SQLite dance and the column list is spelled out
            # rather than `SELECT *`, so a future column added between the two halves of
            # this migration cannot silently reorder anybody's data.
            "ALTER TABLE peace_term RENAME TO peace_term_old",
            """CREATE TABLE peace_term (
                   id             TEXT PRIMARY KEY,
                   world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
                   commitment_id  INTEGER NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
                   seq            INTEGER NOT NULL,
                   kind           TEXT NOT NULL CHECK (kind IN ('cede_region', 'restore_region',
                                                 'withdraw', 'reparations',
                                                 'demilitarise', 'guarantee',
                                                 'recognise', 'depose',
                                                 'limit_arsenal')),
                   obliged_id     TEXT NOT NULL DEFAULT '',
                   beneficiary_id TEXT NOT NULL DEFAULT '',
                   subject_id     TEXT NOT NULL DEFAULT '',
                   value          REAL NOT NULL DEFAULT 0,
                   outstanding    REAL NOT NULL DEFAULT 0,
                   due_turn       INTEGER,
                   -- **Carried over from the fresh schema, not left behind.** A rebuild
                   -- that keeps the columns and drops the constraints produces a table
                   -- that is the same shape and a different promise, and
                   -- `test_a_fresh_world_and_a_migrated_one_promise_the_same_contract`
                   -- is right to refuse it. It caught exactly that here.
                   UNIQUE (commitment_id, seq)
               )""",
            "INSERT INTO peace_term (id, world_id, commitment_id, seq, kind, obliged_id,"
            "                        beneficiary_id, subject_id, value, outstanding, due_turn)"
            " SELECT id, world_id, commitment_id, seq, kind, obliged_id,"
            "        beneficiary_id, subject_id, value, outstanding, due_turn"
            "   FROM peace_term_old",
            "DROP TABLE peace_term_old",
            # The index goes with the table it indexes; a rebuild that forgets it leaves a
            # migrated world quietly slower than a fresh one.
            "CREATE INDEX IF NOT EXISTS ix_peace_term_world"
            " ON peace_term(world_id, commitment_id)",
        ),
    ),
    Migration(
        version=44,
        summary="a third power may broker somebody else's peace; event_participant.role widens",
        invalidates_saves=True,
        reason=(
            "The second closed-CHECK widening in the project, and the same shape as the "
            "first: SQLite cannot widen a CHECK in place, so the table is rebuilt. Accepted "
            "on Alex's explicit authorisation to buy the one diplomatic action the layer "
            "never had. Every way a war could end went through one of its own belligerents "
            "-- a power outside it could send equipment, take a side, or watch, and had no "
            "way to *end* it. Mediation is the marquee thing a power leading the "
            "international order does, and the concert route was built without it. A "
            "brokered peace has three parties and the event log could name two of them: "
            "there was no role for the one who did the brokering, and recording the "
            "mediator as an `actor` would make it indistinguishable from a belligerent "
            "settling its own war. **A world written before this opens fine; a world "
            "written after it cannot be opened by an older build**, because the older "
            "CHECK refuses any `mediator` row."
        ),
        statements=(
            # The standard SQLite rebuild, columns spelled out rather than `SELECT *` for
            # the reason migration 43 gives: a future column added between the two halves
            # of this must not silently reorder anybody's data.
            "ALTER TABLE event_participant RENAME TO event_participant_old",
            """CREATE TABLE event_participant (
                   event_id   INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
                   role       TEXT NOT NULL
                              CHECK (role IN ('actor','target','defender','beneficiary',
                                              'aggrieved','mediator')),
                   seq        INTEGER NOT NULL DEFAULT 0,
                   nation_id  TEXT REFERENCES nation(id) ON DELETE CASCADE,
                   region_id  TEXT REFERENCES region(id) ON DELETE CASCADE,
                   PRIMARY KEY (event_id, role, seq),
                   -- Carried over, not left behind. A rebuild that keeps the columns and
                   -- drops the constraints is the same shape and a different promise, and
                   -- the fresh-versus-migrated parity test is right to refuse it.
                   CHECK ((nation_id IS NULL) <> (region_id IS NULL))
               )""",
            "INSERT INTO event_participant (event_id, role, seq, nation_id, region_id)"
            " SELECT event_id, role, seq, nation_id, region_id FROM event_participant_old",
            "DROP TABLE event_participant_old",
            # The index goes with the table it indexes.
            "CREATE INDEX IF NOT EXISTS ix_event_participant_nation"
            " ON event_participant(nation_id, role)",
        ),
    ),
    Migration(
        version=45,
        summary="a government may hold a strategy across turns, and say why it changed it",
        statements=(
            """CREATE TABLE IF NOT EXISTS nation_strategy (
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
)""",
            """CREATE TABLE IF NOT EXISTS strategy_revision (
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
)""",
            """CREATE INDEX IF NOT EXISTS ix_nation_strategy_world
    ON nation_strategy(world_id, nation_id)""",
            """CREATE INDEX IF NOT EXISTS ix_strategy_revision_world
    ON strategy_revision(world_id, nation_id, at_turn)""",
        ),
    ),
    Migration(
        version=46,
        summary="a trade route carries a price, so a shipment can be paid for",
        statements=(
            # Trade was barter. A treaty moved oil every month for its whole life and no
            # treasury in the world moved by a point, so a blockade could starve a country
            # of steel and could not cost it a penny. These two columns are what a
            # settlement needs and nothing else: what a unit goes for, and when the engine
            # last said so.
            #
            # **Both defaulted, and the default is "unpriced".** `priced_turn = -1` is a
            # route nobody has set terms on -- which is every route in every existing save,
            # and every route in a world where `atlas.engine.pricing` is switched off. An
            # unpriced route ships goods and moves no money, which is exactly what a route
            # did before this migration, so a world that upgrades and is then played with
            # the flag down is indistinguishable from one that never upgraded.
            "ALTER TABLE trade_route ADD COLUMN unit_price REAL NOT NULL DEFAULT 0",
            "ALTER TABLE trade_route ADD COLUMN priced_turn INTEGER NOT NULL DEFAULT -1",
        ),
    ),
    Migration(
        version=47,
        summary="a buyer may refuse a price, and a government may say what it is after",
        statements=(
            # **The commercial half of a trade route, kept apart from the diplomatic half.**
            # A route could be cut by war, blockade or sanction, and a seller could decline
            # a month's shipment -- but a shipment declined left no trace, so a player who
            # looked at a route that had stopped could not tell whether a fleet was in the
            # way, whether somebody had refused, or for how long. These two columns are the
            # record of a *commercial* stoppage: which side stopped taking or shipping, and
            # when.
            #
            # **Not a broken agreement, and the default says so.** `suspended_turn = -1` is
            # a route that ships, which is every route in every existing save and every
            # route in a world with `atlas.engine.pricing` down. The `commitment` behind a
            # suspended route is untouched: no breach, no regard charge, no casus belli --
            # the cargo stops and the signatures stand, and the shipments resume the turn
            # the seller's floor and the buyer's ceiling overlap again. See
            # `atlas.engine.consent`.
            "ALTER TABLE trade_route ADD COLUMN suspended_turn INTEGER NOT NULL DEFAULT -1",
            # The CHECK rides along on the ADD COLUMN -- SQLite accepts one there, unlike a
            # PRIMARY KEY or a UNIQUE -- so a migrated world enforces exactly what a freshly
            # built one does. A migration that quietly gave up a constraint `schema.sql`
            # declares would be two databases with the same stamp and different rules.
            "ALTER TABLE trade_route ADD COLUMN suspended_by TEXT NOT NULL DEFAULT ''"
            " CHECK (suspended_by IN ('', 'seller', 'buyer'))",
            # And what a government is *trying* to arrange. Intent, not a write path: a
            # power names a good and at most a counterparty, and the engine decides whether
            # there is anything to ship, how much, and what it costs. Empty in every
            # existing save, so an upgraded world is indistinguishable from one that never
            # upgraded until somebody proposes something. See `atlas.engine.intent`.
            """CREATE TABLE IF NOT EXISTS trade_intent (
    id         TEXT PRIMARY KEY,
    world_id   TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id  TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('seek','sell','diversify')),
    good       TEXT NOT NULL CHECK (good IN ('oil','steel','rare')),
    partner_id TEXT NOT NULL DEFAULT '',
    opened_turn INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'standing'
               CHECK (status IN ('standing','met','retired')),
    note       TEXT NOT NULL DEFAULT '',
    CHECK (nation_id <> partner_id)
)""",
            """CREATE INDEX IF NOT EXISTS ix_trade_intent_world
    ON trade_intent(world_id, nation_id)""",
        ),
    ),
    Migration(
        version=48,
        summary="a government may stop selling to somebody on purpose, and pay for it",
        statements=(
            # **A different thing from the suspension schema 47 added, and the table is
            # separate because the facts are.** A commercial suspension is the engine
            # noticing that two sides no longer overlap on price: it is automatic, nobody
            # chose it, and it reverses itself the turn they do. An export embargo is a
            # government deciding to lose money in order to hurt somebody. Keeping the
            # second inside `trade_route.suspended_by` would have made one column answer
            # "why did this stop" with two incompatible kinds of answer, and a player who
            # read "stopped by the seller" could not tell a price disagreement from an act
            # of coercion.
            #
            # **Empty in every existing save**, so a world that upgrades and is then played
            # with `atlas.engine.embargo` down -- which is the default -- is
            # indistinguishable from one that never upgraded. See `atlas.engine.embargo`.
            """CREATE TABLE IF NOT EXISTS trade_embargo (
    id          TEXT PRIMARY KEY,
    world_id    TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    proposer_id TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    target_id   TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    good        TEXT NOT NULL DEFAULT '' CHECK (good IN ('', 'oil', 'steel', 'rare')),
    opened_turn INTEGER NOT NULL DEFAULT 0,
    lifted_turn INTEGER NOT NULL DEFAULT -1,
    opened_on   TEXT NOT NULL DEFAULT '',
    lifted_on   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'standing'
                CHECK (status IN ('standing', 'lifted')),
    revenue_forgone REAL NOT NULL DEFAULT 0,
    episodes    INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    CHECK (proposer_id <> target_id)
)""",
            """CREATE INDEX IF NOT EXISTS ix_trade_embargo_world
    ON trade_embargo(world_id, proposer_id)""",
            """CREATE INDEX IF NOT EXISTS ix_trade_embargo_target
    ON trade_embargo(world_id, target_id)""",
        ),
    ),
    Migration(
        version=49,
        summary="a suspended lane records when it stopped, so its dwell can be a duration",
        statements=(
            # **A day, beside the turn number schema 47 added, because a turn number cannot
            # answer a day question.** A suspended route now has to serve a dwell before it
            # may reopen -- `atlas.engine.consent.DWELL_DAYS` -- and a dwell counted in
            # turns is eight months at a monthly pace and eight years at a yearly one, which
            # is the mistake `atlas.engine.mediation.MEDIATION_COOLDOWN_DAYS` exists to
            # record. `assess_trade` is already handed the round's date; this is where it
            # goes, written in the same batch as `suspended_turn` so the pair rewinds
            # together.
            #
            # **Empty in every existing save, and empty is "no dwell to serve".** A route
            # suspended before this column existed has no recorded date, and the resume test
            # treats that as a dwell already served rather than as a lane locked shut for
            # ever -- an upgraded world reopens exactly the lanes it would have reopened.
            "ALTER TABLE trade_route ADD COLUMN suspended_date TEXT NOT NULL DEFAULT ''",
        ),
    ),
    Migration(
        version=50,
        summary="a government is told what it asked for last turn and did not get",
        statements=(
            # **The refusals existed and the asker never saw one.** Every layer in the
            # engine words its refusals as *what to write instead* -- that is the rule
            # `causality.Refusal` exists to keep -- and every one of them went to
            # `JumpReport.rejections`, which reaches `ui.overlays` and no view builder at
            # all. So the discipline was being paid for and spent on the player, while the
            # party that had actually been refused was told nothing and asked again.
            #
            # HISTORY, append-only, and deliberately outside `_FINGERPRINT_TABLES` --
            # which is why the table can be added without moving a single fingerprint. It
            # is **not** outside the *comparison*: a row here is quoted into the next
            # turn's prompt, so two runs that disagree about one are two different worlds
            # from that turn on. `tables.REGISTRY` marks it `gates_play`, the flag
            # `event_cause` carries for the same reason.
            """CREATE TABLE IF NOT EXISTS refusal (
    id              INTEGER PRIMARY KEY,
    world_id        TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id       TEXT NOT NULL,
    turn            INTEGER NOT NULL,
    channel         TEXT NOT NULL,
    reason          TEXT NOT NULL
)""",
            """CREATE INDEX IF NOT EXISTS ix_refusal_world_nation
    ON refusal(world_id, nation_id, turn)""",
        ),
    ),
    Migration(
        version=51,
        summary="the routing log says which government a call was playing, not only which model",
        statements=(
            # **A cross-model match needs the pair, and the log carried half of it.** The
            # row already says which model was asked and which answered; it could not say
            # who it was playing, so "what did the Claude nation propose on turn 7" was a
            # question the audit trail could not answer at all.
            #
            # Empty where a call is not about one nation -- `world_sim` speaks for the
            # world, an organisation session for a body, the arbiter for nobody -- and
            # empty is a truthful answer to "whose government was this", not a missing one.
            """ALTER TABLE routing_log ADD COLUMN nation_id TEXT NOT NULL DEFAULT ''""",
        ),
    ),
)

#: What a freshly created world is stamped with.
SCHEMA_VERSION: int = max((m.version for m in MIGRATIONS), default=1)


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def stamp(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept a bound parameter, and the value is an int we control.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def needs_migration(conn: sqlite3.Connection) -> bool:
    return current_version(conn) < SCHEMA_VERSION


def _run(conn: sqlite3.Connection, statement: str) -> None:
    """Execute one migration statement, tolerating a column that is already there.

    **Every other additive statement in this file is re-runnable and one is not.**
    ``CREATE TABLE`` and ``CREATE INDEX`` both take ``IF NOT EXISTS``; SQLite offers no such
    clause for ``ALTER TABLE ... ADD COLUMN``, so a world whose table already carries the
    column but whose stamp is older cannot be opened at all -- it fails on `duplicate column
    name` and there is no way past it.

    That is not only a test-scaffolding problem, though it is how it surfaced: any database
    built from the current ``schema.sql`` and stamped at an earlier version is in exactly
    that state. Narrow on purpose -- the *only* error swallowed is a duplicate column on an
    ADD COLUMN, which means the migration's intent is already satisfied.
    """
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        collapsed = " ".join(statement.split()).lower()
        if not ("add column" in collapsed and "duplicate column name" in str(exc).lower()):
            raise


def rebuilds_pending(conn: sqlite3.Connection) -> list[int]:
    """The versions still to run on this world that rebuild a table rather than add to it.

    Public so :func:`atlas.state.db.connect` can take a copy of the save before any of them
    touches it. Only a save-invalidating migration can carry DROP or RENAME -- the additive
    guard refuses them everywhere else -- so the flag is exactly the set of rebuilds.
    """
    version = current_version(conn)
    return [m.version for m in MIGRATIONS if m.version > version and m.invalidates_saves]


def _foreign_keys_enforced(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])


def _refuse_broken_references(conn: sqlite3.Connection, migration: Migration) -> None:
    """Fail the migration if it left any row pointing at nothing.

    A rebuild runs with enforcement off, so nothing checked the rows it copied or the
    references into the table it replaced. This is that check, run inside the transaction
    so a failure rolls the whole version back rather than committing a world with dangling
    keys in it.
    """
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        sample = ", ".join(f"{row[0]} rowid {row[1]} -> {row[2]}" for row in broken[:5])
        raise sqlite3.IntegrityError(
            f"migration {migration.version} left {len(broken)} row(s) referring to rows "
            f"that do not exist ({sample}); rolled back, the world is unchanged"
        )


def _apply(conn: sqlite3.Connection, migration: Migration) -> None:
    # isolation_level=None means autocommit, so the transaction is explicit.
    conn.execute("BEGIN")
    try:
        for statement in migration.statements:
            _run(conn, statement)
        if migration.invalidates_saves:
            _refuse_broken_references(conn, migration)
        stamp(conn, migration.version)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _apply_rebuild(conn: sqlite3.Connection, migration: Migration) -> None:
    """Run a table rebuild with foreign-key enforcement off, and put it back afterwards.

    **With enforcement on, a rebuild deletes the children of the table it rebuilds.**
    SQLite runs an implicit ``DELETE FROM`` ahead of every ``DROP TABLE`` when foreign keys
    are on, and that DELETE fires every ``ON DELETE CASCADE`` pointing at the table. So
    migration 20's ``DROP TABLE commitment`` emptied ``commitment_nation``,
    ``commitment_term``, ``peace_term`` and ``intel_term`` in every save it opened, and
    left each alliance standing with no parties and no terms. SQLite's own procedure for
    changing a table's schema turns enforcement off first for exactly this reason.

    ``PRAGMA foreign_keys`` is silently ignored inside a transaction, so it is switched
    here, before :func:`_apply` opens one, and read back rather than trusted.
    """
    if conn.in_transaction:
        raise RuntimeError(
            f"migration {migration.version} rebuilds a table and must switch foreign keys "
            "off to do it safely, which SQLite ignores inside an open transaction; commit "
            "or roll back before migrating"
        )
    enforced = _foreign_keys_enforced(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        if _foreign_keys_enforced(conn):  # pragma: no cover - guarded by the check above
            raise RuntimeError(
                f"could not switch foreign keys off for migration {migration.version}")
        _apply(conn, migration)
    finally:
        if enforced:
            conn.execute("PRAGMA foreign_keys = ON")


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Bring a world up to :data:`SCHEMA_VERSION`. Returns the versions applied.

    Safe to call on every open: a world already at the current version does no work.
    """
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise NewerWorldError(
            f"this world is at schema version {version} but this build of ATLAS "
            f"understands {SCHEMA_VERSION}. It was written by a newer version; there is "
            "no downgrade path."
        )

    applied: list[int] = []
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        check_migration(migration)
        if migration.invalidates_saves:
            _apply_rebuild(conn, migration)
        else:
            _apply(conn, migration)
        applied.append(migration.version)

    return applied
