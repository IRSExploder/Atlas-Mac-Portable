"""A numeric column agreed with `expected_prior: true` for any value that was not zero.

`_differs` has an explicit boolean branch, and it needs one: SQLite hands back `int` 0/1
for an INTEGER column while a JSON `true` parses to a Python `bool`, so a model that
correctly cited `is_alive` had to be read as agreeing. The branch got there by truthiness:

    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) != bool(expected)

which also made `_differs(10, True)` False. Any nonzero number agreed with `true` and any
zero agreed with `false`, so a proposal that named a type the column does not have was read
as agreeing about its value, and compare-and-swap passed a write it should have refused.

Worth being precise about the severity, because it is easy to overstate: CAS is a
concurrency guard, not the anti-cheat perimeter, and `expected_prior` is optional -- a model
wanting to clobber a concurrent write can simply omit it, which is cheaper and equally
unpoliced. So this buys an attacker nothing. It is a correctness fix to a comparison, and
the argument for making it is that a comparison which says two values agree when they do
not will eventually be believed by something else.

The first test is the defect. The second is the reason the branch cannot simply be deleted.
"""

from __future__ import annotations

import pytest

from atlas.state.rules.core import _differs


@pytest.mark.parametrize(("actual", "expected"), [
    (10, True),      # the reviewer's case
    (0.4, True),
    (2.0, True),
    (5, False),
    (0.04, False),
])
def test_a_number_that_is_not_boolean_shaped_differs_from_a_boolean(actual, expected):
    assert _differs(actual, expected) is True, (
        f"{actual!r} is not {expected!r}; agreeing here lets a proposal cite a type the "
        "column does not have and be read as citing its value correctly"
    )


@pytest.mark.parametrize(("actual", "expected", "differs"), [
    (1, True, False),    # SQLite int 1 vs JSON true -- must still agree
    (0, False, False),
    (1, False, True),
    (0, True, True),
    (True, True, False),
])
def test_a_genuine_boolean_column_still_compares_as_one(actual, expected, differs):
    """Why the branch exists at all. `nation.is_alive` is the one model-writable boolean
    column, it is declared INTEGER, and the value that comes back from a read is `int`."""
    assert _differs(actual, expected) is differs


def test_the_boolean_column_a_model_can_actually_write_reads_back_as_an_int(conn):
    """The premise under the test above, checked against a real world rather than assumed."""
    row = conn.execute("SELECT is_alive FROM nation WHERE id = 'aurelia'").fetchone()
    assert isinstance(row["is_alive"], int) and not isinstance(row["is_alive"], bool)
    assert _differs(row["is_alive"], True) is False
