"""A save carried forward has to be the database a fresh one is, and still rewind.

`test_migrations.py` already checks a great deal of this one migration at a time. What is
missing is the *property*: every supported version, the same assertions, so that the next
migration is covered on the day it is written rather than when somebody remembers.

Four things, and the third is the one nothing else looks at:

* **Idempotent and transactional** -- migrating twice reaches the same schema, and a
  migration that raises leaves the version where it was.
* **Contract preserved** -- columns, keys, indexes, foreign keys and CHECKs, through
  `parity.schema_parity_problems`, which the release audit's own gate calls.
* **Triggers and views** -- which parity does *not* cover. No migration in the chain
  creates a trigger, and `schema.sql` has two; today that is safe only because both have
  been there since the initial commit, so no save predates them. It is a claim about
  history rather than about the code, which is exactly the kind this project writes down.
  A trigger added to `schema.sql` tomorrow without a migration would leave every existing
  save without it, silently, and `CREATE TRIGGER` is on the migration allow-list precisely
  so that it can be added properly.
* **Still a working save** -- a migrated world advances, is closed, is reopened, and
  rewinds to where it was. A schema that matches and a save that cannot be played are not
  the same claim.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import apply_batch, record_event
from atlas.state.bootstrap import create_world
from atlas.state.db import connect, create_world_db
from atlas.state.diff import Diff, DiffBatch
from atlas.state.migrations import MIGRATIONS, SCHEMA_VERSION, stamp
from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from atlas.state.parity import KNOWN_SCHEMA_GAPS, schema_parity_problems
from atlas.state.replay import rewind_to

WORLD = "sandbox"

#: Every version the chain knows how to lift from. One fixture each, which is the whole
#: point: a per-version list cannot fall behind the migrations, because it *is* them.
VERSIONS = [migration.version - 1 for migration in MIGRATIONS]


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,))
    }


def _created_after(version: int) -> set[str]:
    """Tables the chain adds after ``version``, read from the migrations themselves."""
    tables: set[str] = set()
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        for statement in migration.statements:
            text = " ".join(statement.strip().lower().split())
            match = re.match(
                r"^create table(?:\s+if\s+not\s+exists)?\s+([a-z_][a-z0-9_]*)", text)
            if match:
                tables.add(match.group(1))
    return tables


def _aged(path, version: int):
    """A save that reports ``version``, or a skip saying why one cannot be built.

    **There is no historical schema in the repository.** `schema.sql` is the current one
    and the chain is forward-only, so an "old" database can only be made by building a
    current one and taking away what came later -- which is what the release audit's
    parity gate does for a single version, and what this generalises.

    It does not generalise all the way. Dropping the tables a later migration creates is
    enough for 21 of the 48 descents; for the rest an intermediate migration reads a table
    that the drop removed and its own creator has not reached yet, and the descent cannot
    be constructed at all without a real fixture from that era. Those skip with this
    reason rather than being quietly dropped from the parametrisation, so the gap is
    visible in the run.
    """
    old = create_world_db(path)
    for table in _created_after(version):
        old.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608 - names from the chain
    stamp(old, version)
    old.commit()
    old.close()
    try:
        probe = connect(path)
    except sqlite3.OperationalError as exc:
        pytest.skip(
            f"schema {version} cannot be reconstructed from the current schema: {exc}. "
            "No historical schema artifact exists, so this descent needs a real save of "
            "that era to be kept as a fixture.")
    probe.close()


@pytest.fixture
def fresh(tmp_path):
    conn = create_world_db(tmp_path / "fresh.db")
    yield conn
    conn.close()


#: **Declared debt, found by running the parity gate at every version instead of one.**
#:
#: `intel_network` is created by migration 17, so a save at schema 16 or older has that
#: table rebuilt by the chain and then gains `counter_intel` through `ALTER TABLE ADD
#: COLUMN` -- which in SQLite cannot carry a CHECK. The column arrives NOT NULL with its
#: DEFAULT intact and **without** its ``BETWEEN 0 AND 100``:
#:
#:     fresh    counter_intel REAL NOT NULL DEFAULT 0 CHECK (counter_intel BETWEEN 0 AND 100)
#:     migrated counter_intel REAL NOT NULL DEFAULT 0
#:
#: `tests/test_migrations.KNOWN_SCHEMA_GAPS` is empty and its parity test is green,
#: because it checks exactly one descent -- schema 21 -- and at 21 the table is never
#: rebuilt. Closing it means rebuilding the table, which the additive-only guard forbids
#: by design and which the project has done deliberately once (schema 43). That is a
#: save-compatibility decision, not a test fix, so it is declared here rather than papered
#: over, and `test_the_missing_check_is_the_only_gap` fails if a second one appears.
#:
#: In the shape `schema_parity_problems` takes since bug scan S4 (5962e1a9): the exact items
#: tolerated, not a whole aspect skipped. Held per version, because the comparison also
#: reports a gap that has closed, and above schema 16 this one never opened.
_GAP_KEY = ("intel_network", "checks")
_KNOWN_GAPS: dict[tuple[str, str], tuple[str, ...]] = {_GAP_KEY: KNOWN_SCHEMA_GAPS[_GAP_KEY]}


def _known_gaps_at(version: int) -> dict[tuple[str, str], tuple[str, ...]]:
    return _KNOWN_GAPS if version <= 16 else {}


@pytest.mark.parametrize("version", VERSIONS)
def test_a_save_of_any_supported_version_migrates_to_the_same_contract(
        tmp_path, fresh, version):
    """The parity gate, per version rather than once."""
    path = tmp_path / f"v{version}.db"
    _aged(path, version)
    migrated = connect(path)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        problems = schema_parity_problems(fresh, migrated, known_gaps=_known_gaps_at(version))
        assert problems == [], f"from schema {version}: {problems}"
    finally:
        migrated.close()


def test_the_missing_check_is_the_only_gap(tmp_path, fresh):
    """The declared gap is a debt, so it is bounded: exactly one, on the column named.

    Runs the oldest constructible descent with **no** allowance, and requires that the
    only thing parity complains about is the one written down above.
    """
    path = tmp_path / "oldest.db"
    _aged(path, VERSIONS[0])
    migrated = connect(path)
    try:
        problems = schema_parity_problems(fresh, migrated)
        assert len(problems) == 1, f"the declared debt has changed size: {problems}"
        assert "intel_network.checks" in problems[0], problems[0]
        assert "counter_intel" in problems[0], problems[0]
    finally:
        migrated.close()


@pytest.mark.xfail(strict=True, reason=(
    "known: ALTER TABLE ADD COLUMN cannot carry a CHECK, so a save at schema <= 16 gains "
    "intel_network.counter_intel without its 0-100 bound. Closing it needs a table "
    "rebuild, which the additive-only guard forbids by design -- a save-compatibility "
    "decision. When it is fixed this turns red: delete the marker and the gap declaration."))
def test_a_migrated_column_keeps_the_bound_a_fresh_one_has(tmp_path, fresh):
    """What the fix would have to achieve, asserted directly, so the debt has a test that
    tells somebody when it is paid rather than a comment that does not."""
    path = tmp_path / "bound.db"
    _aged(path, VERSIONS[0])
    migrated = connect(path)
    try:
        sql = migrated.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'intel_network'").fetchone()["sql"]
        line = next(row for row in sql.splitlines() if "counter_intel" in row)
        assert "CHECK" in line.upper(), line.strip()
    finally:
        migrated.close()


@pytest.mark.parametrize("version", VERSIONS)
def test_triggers_and_views_survive_every_migration(tmp_path, fresh, version):
    """**The half `schema_parity_problems` does not cover.**

    No migration creates a trigger, and the schema has two. That is safe only while no
    save predates them; this is what fails on the day one does.
    """
    path = tmp_path / f"v{version}.db"
    _aged(path, version)
    migrated = connect(path)
    try:
        for kind in ("trigger", "view"):
            expected, got = _objects(fresh, kind), _objects(migrated, kind)
            assert got == expected, (
                f"from schema {version}, {kind}s differ: missing {sorted(expected - got)}, "
                f"unexpected {sorted(got - expected)}. No migration creates a {kind}, so "
                f"one added to schema.sql needs a migration statement too -- "
                f"`CREATE {kind.upper()}` is on the allow-list for this reason")
    finally:
        migrated.close()


def test_migrating_twice_changes_nothing_the_second_time(tmp_path, fresh):
    """Idempotence, over the whole chain rather than one step."""
    path = tmp_path / "twice.db"
    _aged(path, VERSIONS[0])
    first = connect(path)
    once = sorted(
        (str(r["type"]), str(r["name"]), str(r["sql"] or ""))
        for r in first.execute("SELECT type, name, sql FROM sqlite_schema"))
    first.close()

    second = connect(path)
    try:
        twice = sorted(
            (str(r["type"]), str(r["name"]), str(r["sql"] or ""))
            for r in second.execute("SELECT type, name, sql FROM sqlite_schema"))
        assert twice == once
        assert second.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        second.close()


def test_a_migrated_save_advances_saves_reloads_and_rewinds(tmp_path):
    """**A schema that matches and a save that can be played are two claims.**

    A real world, carried across a migration, then advanced, closed, reopened from disk
    and rewound -- which is the sequence a player performs and the one that touches the
    migration, the diff log and the rewind in the order they actually happen.

    The descent is one version rather than all of them, because this test is about the
    *data* surviving and being playable; the schema half is covered per version above.
    """
    path = tmp_path / "played.db"
    world = create_world(path, sandbox_seed())
    world.close()

    # Age it by exactly one migration, so the world's own rows are real rather than seeded
    # into an already-current file.
    aged = connect(path)
    for table in _created_after(VERSIONS[-1]):
        aged.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608 - names from the chain
    stamp(aged, VERSIONS[-1])
    aged.commit()
    aged.close()

    migrated = connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert migrated.execute(
        "SELECT COUNT(*) FROM nation WHERE world_id = ?", (WORLD,)).fetchone()[0] > 0, (
        "the migration carried the schema and lost the world")

    migrated.execute(
        "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date, end_date)"
        " VALUES (?, 1, 'jump', 'sealed', '1897-03-01', '1897-04-01')", (WORLD,))
    migrated.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    migrated.commit()
    before = migrated.execute(
        "SELECT economy FROM nation WHERE id = 'aurelia'").fetchone()["economy"]

    # advance
    migrated.execute(
        "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date, end_date)"
        " VALUES (?, 2, 'jump', 'sealed', '1897-04-01', '1897-05-01')", (WORLD,))
    migrated.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    migrated.commit()
    event_id = record_event(
        migrated, world_id=WORLD, turn=2, date="1897-05-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, actor_nation_id="aurelia", text="a turn passed",
        visibility=Visibility.PUBLIC)
    outcome = apply_batch(migrated, DiffBatch(
        world_id=WORLD, turn=2, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("nation", "aurelia", "economy", DiffOp.SET, 33.0,
                    reason_event_id=event_id),)))
    assert outcome.applied, outcome.validation.violations
    migrated.commit()
    migrated.close()

    # reload from disk, then rewind
    reopened = connect(path)
    try:
        assert reopened.execute(
            "SELECT economy FROM nation WHERE id = 'aurelia'").fetchone()["economy"] == 33.0
        report = rewind_to(reopened, WORLD, 1)
        assert report.problems == [], report.problems
        assert reopened.execute(
            "SELECT economy FROM nation WHERE id = 'aurelia'").fetchone()["economy"] == before
        assert reopened.execute(
            "SELECT turn_number FROM world WHERE id = ?", (WORLD,)).fetchone()[0] == 1
    finally:
        reopened.close()
