"""Rewinding a world to an earlier turn.

This is where the diff-first design pays for itself. Every mutation that ever landed is a
row carrying both the value it replaced and the value it wrote, in application order. So
rewinding is not a re-simulation and not a snapshot restore -- it is walking those rows
backwards and putting each field back, which is exact by construction rather than
approximately right.

Two things make it trustworthy:

**Inverse application, in reverse order.** If a field was written three times in a turn,
undoing them newest-first restores the value it held before the first write. Undoing in
forward order would leave the *second* write's prior value, which looks plausible and is
wrong.

**Events and diffs after the target turn are deleted, not hidden.** A rewound world must
be indistinguishable from one that never took that path, because the next simulation reads
history to decide what happens -- and a "cancelled" event still sitting in the table would
influence a future it no longer belongs to.

Rewind deliberately does *not* restore the RNG: the seed lives on the world row and never
changes, so replaying the same turn with the same inputs produces the same outcome. That
is what makes rewind a way to explore rather than a way to reroll.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

# Exactly the tables the applier can address, because undoing has to cover writing.
# This used to be a second copy, and the two drifted the moment a table was added:
# the applier learned to write a contest and rewind did not learn to undo one.
from .apply import PRIMARY_KEY

__all__ = ["RewindReport", "can_rewind", "rewind_to"]


@dataclass(slots=True)
class RewindReport:
    from_turn: int
    to_turn: int
    diffs_undone: int = 0
    events_removed: int = 0
    batches_removed: int = 0
    problems: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    @property
    def ok(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        lines = [
            f"rewound turn {self.from_turn} -> {self.to_turn}: "
            f"{self.diffs_undone} change(s) undone, {self.events_removed} event(s) and "
            f"{self.batches_removed} batch(es) removed"
        ]
        lines.extend(f"  problem: {problem}" for problem in self.problems)
        return "\n".join(lines)


def _table_present(conn: sqlite3.Connection, name: str) -> bool:
    """Whether this world's schema carries a table at all.

    The pre-migration guarantee every layer added since schema 14 makes for itself, asked
    of the database rather than assumed: a rewind is the one operation a player reaches for
    when something has gone wrong, and it must work on a save older than the table.
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def can_rewind(conn: sqlite3.Connection, world_id: str, to_turn: int) -> tuple[bool, str]:
    row = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    if row is None:
        return False, f"no world {world_id!r}"
    current = int(row["turn_number"])
    if to_turn < 0:
        return False, "cannot rewind before the world began"
    if to_turn >= current:
        return False, f"already at turn {current}; rewind only goes backwards"
    return True, ""


def rewind_to(conn: sqlite3.Connection, world_id: str, to_turn: int) -> RewindReport:
    """Put the world back as it stood at the end of ``to_turn``."""
    allowed, reason = can_rewind(conn, world_id, to_turn)
    current = int(
        conn.execute("SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()[
            "turn_number"
        ]
    )
    report = RewindReport(from_turn=current, to_turn=to_turn)
    if not allowed:
        report.problems.append(reason)
        return report

    conn.execute("BEGIN")
    try:
        # Newest first. Undoing forwards would restore an intermediate value rather than
        # the one the field held before the turn began.
        rows = conn.execute(
            """
            SELECT d.id, d.op, d.target_table, d.target_id, d.field, d.old_json
              FROM diff d
              JOIN diff_batch b ON b.id = d.batch_id
             WHERE b.world_id = ? AND b.turn > ? AND d.status = 'applied'
             ORDER BY b.turn DESC, d.batch_id DESC, d.seq DESC
            """,
            (world_id, to_turn),
        ).fetchall()

        for row in rows:
            table = row["target_table"]
            columns = PRIMARY_KEY.get(table)
            if columns is None:
                report.problems.append(f"cannot undo a change to unknown table {table!r}")
                continue

            # A row that appeared is removed again; a row that was removed is restored
            # from the columns the applier kept. Undoing these as if they were field
            # writes would leave a rewound world holding an army it was never given.
            if row["op"] in {"insert", "delete"}:
                if _undo_row_change(conn, table, row, report):
                    report.diffs_undone += 1
                continue

            real_columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if row["field"] not in real_columns:
                report.problems.append(f"cannot undo {table}.{row['field']}: no such column")
                continue

            keys = (
                (row["target_id"],)
                if len(columns) == 1
                else tuple(row["target_id"].split("|"))
            )
            if len(keys) != len(columns):
                report.problems.append(f"cannot undo {table} row {row['target_id']!r}: malformed id")
                continue

            clause = " AND ".join(f"{column} = ?" for column in columns)
            try:
                previous = json.loads(row["old_json"]) if row["old_json"] is not None else None
            except json.JSONDecodeError:
                previous = None

            conn.execute(
                f"UPDATE {table} SET {row['field']} = ? WHERE {clause}",  # noqa: S608 - validated above
                (previous, *keys),
            )
            report.diffs_undone += 1

        # All or nothing. A change that could not be undone means the world cannot be put
        # back exactly, and committing the rest would leave it half-rewound: fields restored
        # around a row that was not, and the diffs that could have been retried deleted with
        # the turns they belonged to. Rolling back leaves it as it was, and the report says
        # why -- a rewind that did not happen is the honest outcome here.
        if report.problems:
            conn.rollback()
            report.diffs_undone = 0
            report.problems.append(
                f"rewind to turn {to_turn} was abandoned; the world is unchanged at turn "
                f"{current}")
            return report

        # Remove the record of the futures that no longer happened. Leaving them would let
        # a later simulation cite a turn that has been undone.
        cursor = conn.execute(
            "DELETE FROM event WHERE world_id = ? AND turn > ?", (world_id, to_turn)
        )
        report.events_removed = cursor.rowcount or 0

        cursor = conn.execute(
            "DELETE FROM diff_batch WHERE world_id = ? AND turn > ?", (world_id, to_turn)
        )
        report.batches_removed = cursor.rowcount or 0

        conn.execute("DELETE FROM action WHERE world_id = ? AND turn > ?", (world_id, to_turn))
        conn.execute("DELETE FROM digest WHERE world_id = ? AND turn_to > ?", (world_id, to_turn))
        conn.execute("DELETE FROM routing_log WHERE world_id = ? AND turn > ?", (world_id, to_turn))
        # A refusal that no longer happened must not go on being read back to the
        # government it was aimed at: the briefing block reads the turn just gone, and a
        # rewound world would open by telling a staff it was refused something it never
        # proposed. `IF EXISTS` in spirit -- a world written before schema 50 has no such
        # table, and a rewind must work on one.
        if _table_present(conn, "refusal"):
            conn.execute(
                "DELETE FROM refusal WHERE world_id = ? AND turn > ?", (world_id, to_turn))
        conn.execute("DELETE FROM turn WHERE world_id = ? AND number > ?", (world_id, to_turn))

        restored_date = conn.execute(
            "SELECT end_date, start_date FROM turn WHERE world_id = ? AND number = ?",
            (world_id, to_turn),
        ).fetchone()
        date = None
        if restored_date is not None:
            date = restored_date["end_date"] or restored_date["start_date"]

        if date:
            conn.execute(
                "UPDATE world SET turn_number = ?, world_date = ? WHERE id = ?",
                (to_turn, date, world_id),
            )
        else:
            conn.execute(
                "UPDATE world SET turn_number = ? WHERE id = ?", (to_turn, world_id)
            )

        conn.commit()
    except Exception as exc:  # noqa: BLE001 - a failed rewind must not leave a half-state
        conn.rollback()
        report.problems.append(f"rewind aborted and rolled back: {type(exc).__name__}: {exc}")

    return report


def _undo_row_change(conn: sqlite3.Connection, table: str, row, report) -> bool:
    """Reverse an insert or a delete. Returns whether anything was undone."""
    columns = PRIMARY_KEY[table]
    keys = (
        (row["target_id"],) if len(columns) == 1 else tuple(row["target_id"].split("|"))
    )
    clause = " AND ".join(f"{c} = ?" for c in columns)

    if row["op"] == "insert":
        conn.execute(
            f"DELETE FROM {table} WHERE {clause}", keys  # noqa: S608 - identifiers are literals
        )
        return True

    try:
        restored = json.loads(row["old_json"]) if row["old_json"] else None
    except json.JSONDecodeError:
        restored = None
    if not isinstance(restored, dict) or not restored:
        report.problems.append(
            f"cannot restore {table} {row['target_id']!r}: the removed row was not recorded"
        )
        return False

    real = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    payload = {k: v for k, v in restored.items() if k in real}
    names = sorted(payload)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) "  # noqa: S608 - validated above
        f"VALUES ({', '.join('?' for _ in names)})",
        tuple(payload[name] for name in names),
    )
    return True
