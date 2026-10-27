"""What a table actually promises, and whether two paths to the same schema agree.

**Extracted from `tests/test_migrations.py` unchanged**, because it was only ever callable
as a test. `atlas release-audit` needs the same comparison as a gate, and a second copy of
it would be a second thing to keep in step with the first -- which is precisely the class of
divergence this check exists to catch.

The check itself is Codex's finding made mechanical. Names and columns matched while the two
paths disagreed about everything that mattered: a fresh schema-19 world keyed ``arsenal`` on
``nation_id`` alone, allowed negative warheads and carried an index; a migrated one keyed it
on ``(world_id, nation_id)``, refused negative warheads and had no index. Three of those are
correctness and the fourth is performance, and comparing names could see none of them.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "KNOWN_SCHEMA_GAPS",
    "build_oldest_world",
    "contract_of",
    "schema_parity_problems",
    "tables_of",
]


#: **Where a world carried forward by the migration chain and a fresh one really differ.**
#: Keyed ``(table, aspect)``; the value is the exact items the *fresh* schema has in that
#: aspect and a migrated world lacks. The comparison tolerates precisely those items and
#: nothing else in the same aspect, and reports an item that has stopped being missing, so
#: an entry can neither hide a second drift nor outlive its own fix.
#:
#: A debt, not a dispensation. All three are constraints `schema.sql` declares on a column
#: whose migration added it with a bare ``ALTER TABLE ... ADD COLUMN``, and SQLite cannot
#: attach a CHECK or a foreign key to an existing column without rebuilding the table.
#: Closing them takes a table-rebuild migration per table.
#:
#: They were invisible until 2026-09-26 (bug scan S4): the parity check started from the
#: fresh schema with only the newest migrations stripped, so every older ``ADD COLUMN``
#: was already present, constraint and all, when the chain ran over it.
KNOWN_SCHEMA_GAPS: dict[tuple[str, str], tuple[Any, ...]] = {
    # Migration 2 added `home_nation_id TEXT` with no REFERENCES. In a migrated world
    # nothing stops a region naming a nation that does not exist, and deleting a nation
    # leaves the id dangling instead of setting it NULL.
    ("region", "foreign_keys"): (("nation", "home_nation_id", "id", "SET NULL"),),
    # Migration 41 added `mobilisation` with no CHECK; a migrated world accepts any
    # integer where a fresh one refuses anything outside the four footings 0..3.
    ("nation", "checks"): ("mobilisationbetween0and3",),
    # Migration 32 added `counter_intel` with no CHECK; a migrated world accepts values
    # outside 0..100 where a fresh one refuses them.
    ("intel_network", "checks"): ("counter_intelbetween0and100",),
}


def _without_comments(script: str) -> str:
    """``script`` with every ``--`` comment removed, string literals left alone.

    ``DROP COLUMN`` rewrites the stored DDL textually and fails with "incomplete input"
    when a comment sits between the column and the one before it, which is how
    `unit.entrenched_at` and `commitment.expires_turn` are written in `schema.sql`.
    """
    out: list[str] = []
    i, quoted = 0, False
    while i < len(script):
        ch = script[i]
        if ch == "'":
            quoted = not quoted
        elif not quoted and script.startswith("--", i):
            end = script.find("\n", i)
            i = len(script) if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def build_oldest_world(path: Path | str) -> None:
    """Write, at ``path``, the schema-1 world the migration chain starts from.

    Built from ``schema.sql`` by removing **everything any migration adds** -- every table,
    index, trigger and view it creates and every column it adds -- and stamping version 1,
    so that running the chain rebuilds all of it the way a real old save gets it.

    Stripping only the newest migrations, as the parity check used to, left every older
    migration's ``ADD COLUMN`` already in place with `schema.sql`'s constraints attached;
    the chain's duplicate-column tolerance then skipped the migration's own statement, and
    whatever it would have written was never compared. Scaffolding, not a migration.
    """
    from .db import _schema_sql, connect
    from .migrations import MIGRATIONS, _new_columns, _normalise, stamp

    tables: list[str] = []
    columns: list[tuple[str, str]] = []
    others: list[tuple[str, str]] = []
    for migration in MIGRATIONS:
        for statement in migration.statements:
            added, created = _new_columns(statement)
            tables += sorted(t for t in created if not t.endswith("_rebuilt"))
            columns += sorted(added)
            made = re.match(
                r"^create\s+(?:unique\s+)?(index|trigger|view)\s+(?:if\s+not\s+exists\s+)?"
                r"([a-z_][a-z0-9_]*)", _normalise(statement))
            if made:
                others.append((made.group(1), made.group(2)))

    conn = connect(path)
    try:
        conn.executescript(_without_comments(_schema_sql()))
        conn.execute("PRAGMA foreign_keys = OFF")
        for kind, name in reversed(others):
            conn.execute(f"DROP {kind.upper()} IF EXISTS {name}")
        for table in reversed(tables):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        for table, column in reversed(columns):
            if column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        stamp(conn, 1)
        conn.commit()
    finally:
        conn.close()


def tables_of(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    )


def contract_of(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """What this table promises: columns, key, indexes, foreign keys and CHECKs."""
    columns = [
        {
            "name": row["name"],
            "type": (row["type"] or "").upper(),
            "notnull": int(row["notnull"]),
            "default": row["dflt_value"],
            "pk": int(row["pk"]),
        }
        for row in conn.execute(f"PRAGMA table_info({table})")  # noqa: S608 - pragma, no user input
    ]
    columns.sort(key=lambda c: c["name"])
    key = [c["name"] for c in sorted(
        (c for c in columns if c["pk"]), key=lambda c: c["pk"])]
    indexes = {}
    for row in conn.execute(f"PRAGMA index_list({table})"):  # noqa: S608
        indexes[str(row["name"])] = {
            "unique": int(row["unique"]),
            "columns": [str(r["name"]) for r in conn.execute(
                f"PRAGMA index_info({row['name']})")],  # noqa: S608
        }
    foreign = sorted(
        (str(r["table"]), str(r["from"]), str(r["to"] or ""), str(r["on_delete"]))
        for r in conn.execute(f"PRAGMA foreign_key_list({table})")  # noqa: S608
    )
    # The CHECK constraints live only in the stored DDL, so they are compared as normalised
    # text -- coarse, and the only way SQLite offers to see them at all.
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    # Whitespace stripped entirely rather than collapsed: the same constraint is written
    # across two lines in one source and one in the other, and a line break is not a
    # difference in what the table promises.
    checks = sorted(
        "".join(fragment.split()).lower()
        for fragment in re.findall(r"check\s*\((.*?)\)\s*[,)]", str(ddl["sql"] or ""),
                                   re.IGNORECASE | re.DOTALL)
    )
    return {"columns": columns, "primary_key": key, "indexes": indexes,
            "foreign_keys": foreign, "checks": checks}


def schema_parity_problems(
    fresh: sqlite3.Connection,
    migrated: sqlite3.Connection,
    *,
    known_gaps: Mapping[tuple[str, str], tuple[Any, ...]] | None = None,
) -> list[str]:
    """Every way these two disagree about what the schema promises. Empty means they agree.

    ``known_gaps`` maps ``(table, aspect)`` to the exact items the fresh schema has there
    and a migrated world is known to lack; :data:`KNOWN_SCHEMA_GAPS` is the one list the
    migration test and the release gate both pass. Those items are tolerated and nothing
    else in the aspect is, and a gap that has closed is itself reported, so the list
    cannot go stale silently.
    """
    known_gaps = known_gaps or {}
    problems: list[str] = []
    here, there = tables_of(migrated), tables_of(fresh)
    if here != there:
        missing = sorted(set(there) - set(here))
        extra = sorted(set(here) - set(there))
        if missing:
            problems.append(f"a migrated world is missing table(s): {', '.join(missing)}")
        if extra:
            problems.append(f"a migrated world has extra table(s): {', '.join(extra)}")

    for table in sorted(set(here) & set(there)):
        mine, yours = contract_of(migrated, table), contract_of(fresh, table)
        for aspect in sorted(yours):
            expected = yours[aspect]
            lacking = known_gaps.get((table, aspect))
            if lacking is not None:
                if any(item in mine[aspect] for item in lacking):
                    problems.append(
                        f"{table}.{aspect}: a migrated world now has {list(lacking)!r}; the "
                        "known gap has closed, so remove it from KNOWN_SCHEMA_GAPS")
                    continue
                expected = [item for item in expected if item not in lacking]
            if mine[aspect] != expected:
                problems.append(
                    f"{table}.{aspect} means different things depending on how the world "
                    f"got here: migrated={mine[aspect]!r} fresh={yours[aspect]!r}"
                )
    return problems
