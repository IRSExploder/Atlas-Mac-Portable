"""A rewind can put back a row it deleted. It cannot put back the rows SQLite took with it.

`_apply_delete` records the departed row in `old_value` so `replay._undo_row_change` can
re-insert it, and that is exact for a row nothing hangs off. With `PRAGMA foreign_keys = ON`
(`db.connect`), deleting a row that is a `ON DELETE CASCADE` parent also removes its
children -- and the diff log records only the parent, so a rewind restores the parent alone
and the children are gone for good.

**No such path exists today**, which is why this is a guard rather than a fix. The tables a
`DiffOp.DELETE` is ever emitted against and the tables that are cascade parents are disjoint
sets, and the whole point of this file is that they stay that way: the day somebody adds
`Diff("commitment", ..., DiffOp.DELETE)` -- which needs no new machinery, only `Role.ENGINE`
-- every `commitment_term` and `commitment_nation` under it disappears from a save that a
rewind will later claim to have restored.

The sibling fuzz file already says as much in prose. `test_the_older_tables_rewind_whatever_you_do_to_them.NOT_FUZZED` excuses `commitment` with
"`commitment_term` is the half a rewind actually loses". This is that sentence, enforced.

Two halves, because either alone is weak: the emitters are read from the source by AST (a
regex misses a `Diff(...)` split across lines, and a scan that cannot see is worse than no
scan), and the parents are read from a live database by `PRAGMA foreign_key_list` rather
than from `schema.sql`, so a foreign key added by a migration counts too.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3

import pytest

from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import PRIMARY_KEY
from atlas.state.bootstrap import create_world

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "atlas"


def _is_delete_op(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "DELETE"
            and isinstance(node.value, ast.Name) and node.value.id == "DiffOp")


def _delete_targets() -> set[str]:
    """Every table a ``DiffOp.DELETE`` is constructed against, anywhere in the engine."""
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(
                node.func, "attr", "")
            if name != "Diff" or not node.args:
                continue
            args = list(node.args) + [kw.value for kw in node.keywords]
            if not any(_is_delete_op(a) for a in args):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def _cascade_parents(conn: sqlite3.Connection) -> set[str]:
    """Every table something else cascades from, read from the database itself."""
    parents: set[str] = set()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table'")]
    for table in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):  # noqa: S608 - names from the schema
            if (fk["on_delete"] or "").upper() == "CASCADE":
                parents.add(fk["table"])
    return parents


@pytest.fixture
def world():
    conn = create_world(":memory:", sandbox_seed(world_id="cascade"))
    yield conn
    conn.close()


def test_the_scan_can_see(world):
    """A scan that finds nothing is indistinguishable from a scan that cannot see, so both
    halves have to turn something up before the assertion below means anything."""
    emitters = _delete_targets()
    assert emitters, "the AST scan found no DiffOp.DELETE at all; it has stopped working"
    assert emitters <= set(PRIMARY_KEY), (
        f"a delete names a table no diff can address: {sorted(emitters - set(PRIMARY_KEY))}")
    assert _cascade_parents(world), "no cascade parents found; PRAGMA read has stopped working"


def test_no_delete_reaches_a_cascade_parent(world):
    """**The guard.** Deleting a cascade parent takes children the diff log never recorded,
    and a rewind then restores the parent alone."""
    overlap = sorted(_delete_targets() & _cascade_parents(world))
    assert overlap == [], (
        f"{overlap} are deleted by a diff and are cascade parents: SQLite will remove their "
        "children, the batch records only the parent, and a rewind restores the parent "
        "without them. Either record and restore the children in `_apply_delete` / "
        "`_undo_row_change`, or retire the row some other way -- a status column costs "
        "nothing and rewinds for free"
    )
