r"""The NOT NULL guard was satisfied by a column whose *name* contained "default".

SQLite cannot add a `NOT NULL` column to a populated table without a `DEFAULT`, and
`check_statement` exists so that this is discovered when the migration is written rather
than mid-upgrade against somebody's save. It asked

    if "add column" in text and "not null" in text and "default" not in text:

and the last clause is a bare substring test. `ALTER TABLE nation ADD COLUMN
default_posture TEXT NOT NULL` contains "default" -- in the column name -- and has no
DEFAULT clause at all, so it passed the one guard standing between it and a real world.

Latent rather than live: no shipped migration has such a column. It is fixed because the
guard's whole value is that it is trusted by whoever writes the next one, and a guard that
can be defeated by a plausible column name is not.

`\b` and not `\B` or a bare substring: an underscore is a word character, so
`\bdefault\b` matches the keyword `DEFAULT` and does not match `default_posture` or
`is_defaulted`.
"""

from __future__ import annotations

import pytest

from atlas.state.migrations import MigrationRefused, check_statement


@pytest.mark.parametrize("statement", [
    "ALTER TABLE nation ADD COLUMN default_posture TEXT NOT NULL",
    "ALTER TABLE nation ADD COLUMN is_defaulted INTEGER NOT NULL",
    "ALTER TABLE nation ADD COLUMN defaults_to TEXT NOT NULL",
])
def test_a_column_whose_name_contains_default_still_needs_one(statement):
    """The bug. Each of these is NOT NULL with no DEFAULT clause anywhere in it."""
    with pytest.raises(MigrationRefused, match="needs a DEFAULT"):
        check_statement(statement)


@pytest.mark.parametrize("statement", [
    "ALTER TABLE nation ADD COLUMN foo TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE nation ADD COLUMN default_rate REAL NOT NULL DEFAULT 0",
    "ALTER TABLE nation ADD COLUMN foo TEXT",
])
def test_a_real_default_is_still_accepted(statement):
    """And the guard must not have become a blanket refusal: a genuine DEFAULT still
    passes, including on a column whose name also contains the word, and a nullable
    column needs no default at all."""
    check_statement(statement)


def test_the_shipped_migrations_still_pass_their_own_guard():
    """The check that would catch an over-strict fix against real data rather than
    invented statements."""
    from atlas.state.migrations import MIGRATIONS, check_migration

    for migration in MIGRATIONS:
        check_migration(migration)
