"""What a bounded column can actually absorb.

Some columns have a range rather than a scale. Stability and legitimacy live in 0-100, and
``ECON-003`` holds them there -- **by condemning the batch, not the diff**. So an engine
writer that charges more than a nation has does not get a smaller charge; it gets nothing,
and it takes the rest of its batch down with it.

That has been found and fixed four separate times, in four separate modules, each with its
own inline ``max(0.0, current)`` and its own paragraph explaining why:

* :mod:`atlas.engine.budget` -- one bankrupt minor a point short of its arrears charge, and
  not one treasury in the world moved;
* :mod:`atlas.engine.unrest` -- an occupier at 0.2 taking a 0.5 drag cost *every other*
  occupier its drag as well;
* :mod:`atlas.engine.leaders` -- a coup at stability 4 subtracting 8, so the government fell
  in the report and did not fall in the database;
* :mod:`atlas.engine.treaties` -- a violator with 2.0 legitimacy left could not be charged
  6.0, so the treaty stayed active and nobody was charged anything.

Four fixes, four modules, and **four more writers that never got one**: civil projects,
gambits, nuclear panic and freeform nudges. PT39 found the first of those the same way all
the others were found -- a live run where Spain sat at 0.0 stability and a finished public
work carried a flat -1.0, so the work could not complete, its development and industry never
landed, its construction row was never deleted, and it was retried every turn for the rest
of the game.

So the rule is written once, here, and the ninth writer gets it for free.

**Both ends, which none of the four inline guards did.** They all clamp the floor, because
a collapse is what each of them was found by. A nation at 99 taking a civil project's +9.0
stability is the same bug facing the other way, and was equally unguarded.
"""

from __future__ import annotations

import sqlite3

__all__ = ["BOUNDS", "absorbable"]

#: ``(table, column) -> (low, high)`` for every column an engine writer may not overshoot.
#:
#: This is the same range ``ECON-003`` enforces, and the two must agree: a column here that
#: the rule does not check would be clamped for no reason, and a column the rule checks that
#: is missing here is a writer waiting to lose somebody's batch. ``tests/test_bounds.py``
#: asserts they match.
BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    ("nation", "stability"): (0.0, 100.0),
    ("nation", "legitimacy"): (0.0, 100.0),
}


def absorbable(
    conn: sqlite3.Connection, table: str, row_id: str, field: str, amount: float
) -> float:
    """The part of ``amount`` this row can take without leaving the column's range.

    Returns ``amount`` unchanged for any column that is not bounded, so a caller can route
    every effect through it without first asking which kind it has -- which is the point,
    since the writers that got this wrong were the ones that had to remember to ask.

    Charging what there is, rather than refusing to charge, is also the truer reading: a
    government at zero legitimacy cannot be disgraced further by tearing up one more
    signature. It is already there.
    """
    span = BOUNDS.get((table, field))
    if span is None or not amount:
        return amount

    low, high = span
    # The column name is a key of BOUNDS, never caller input, so it cannot carry SQL.
    row = conn.execute(
        f"SELECT {field} AS value FROM {table} WHERE id = ?", (row_id,)  # noqa: S608
    ).fetchone()
    if row is None or row[0] is None:
        # Nothing to clamp against. Let it through and let the rule speak, rather than
        # silently swallowing a write aimed at a row that does not exist.
        return amount

    current = float(row[0])
    if amount < 0:
        return -min(-amount, max(0.0, current - low))
    return min(amount, max(0.0, high - current))
