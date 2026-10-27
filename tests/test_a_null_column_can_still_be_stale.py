"""CORE-004 stopped checking exactly where a proposer is most likely to be wrong.

Compare-and-swap exists to catch a batch built against state that has since moved: the
applier records `old_value` immediately before the write, and comparing it to the
proposal's `expected_prior` says what the world looked like at write time.

It read:

    actual = diff.old_value
    if actual is None:
        continue

`old_value is None` means two different things. For an INSERT it records that the row was
*absent*, and for a DELETE `old_value` is the whole departed row rather than a displaced
scalar -- neither has a prior field value, so neither can be stale in this sense, and both
must be skipped. But a plain SET against a column that currently holds NULL arrives with
`old_value = None` too, and that one was skipped with them: a proposal could assert
`expected_prior = 4` against a column reading NULL and apply unconditionally.

Not reachable by any model today -- `permissions.ROLE_WRITABLE` names no nullable column,
and CORE-003 refuses a model that writes one anyway -- so this is a latent-correctness
fix, and the engine-role batches below are how it has to be exercised. The reason to fix it
rather than to note it is that the reachability argument is about today's permission table
and the guard is supposed to outlive it.

The second test is the regression the obvious fix causes: dropping the `None` check
outright makes CORE-004 fire on every INSERT that carries a prior, so the fix gates on the
op instead of on the value.
"""

from __future__ import annotations

from atlas.state.apply import apply_batch
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, Role, Visibility

WORLD = "sandbox"


def _batch(genesis, *diffs):
    return DiffBatch(world_id=WORLD, turn=1, proposer_role=Role.ENGINE,
                     proposer_model=None, diffs=diffs)


def test_a_set_against_a_null_column_is_compared(conn, genesis):
    """The bug. `nation.capital_region_id` is nullable; NULL it, then propose a write
    that claims it held something else."""
    conn.execute("UPDATE nation SET capital_region_id = NULL WHERE id = 'aurelia'")
    conn.commit()

    outcome = apply_batch(conn, _batch(
        genesis,
        Diff("nation", "aurelia", "capital_region_id", DiffOp.SET, "r_north",
             expected_prior="r_south", reason_event_id=genesis,
             visibility_scope=Visibility.PUBLIC),
    ))

    assert not outcome.applied, (
        "the column held NULL and the proposal said it held 'r_south'; that is exactly the "
        "stale-state case compare-and-swap exists for"
    )
    assert any(v.rule_id == "CORE-004" for v in outcome.validation.violations), (
        [v.rule_id for v in outcome.validation.violations])


def test_an_agreeing_null_prior_still_applies(conn, genesis):
    """And the other direction: NULL matching NULL is not staleness."""
    conn.execute("UPDATE nation SET capital_region_id = NULL WHERE id = 'aurelia'")
    conn.commit()

    outcome = apply_batch(conn, _batch(
        genesis,
        Diff("nation", "aurelia", "capital_region_id", DiffOp.SET, "r_north",
             expected_prior=None, reason_event_id=genesis,
             visibility_scope=Visibility.PUBLIC),
    ))
    assert outcome.applied, outcome.validation.violations


def test_an_insert_carrying_a_prior_is_not_stale(conn, genesis):
    """**The regression the naive fix causes.** An INSERT's `old_value` is None because the
    row was absent, not because a column was NULL, so removing the guard rather than
    gating it on the op would refuse every row the engine creates."""
    outcome = apply_batch(conn, _batch(
        genesis,
        Diff("technology", "aurelia|radar", "__insert__", DiffOp.INSERT,
             {"world_id": WORLD, "acquired_turn": 1},
             expected_prior="anything at all", reason_event_id=genesis,
             visibility_scope=Visibility.PUBLIC),
    ))
    assert not any(v.rule_id == "CORE-004" for v in outcome.validation.violations), (
        "an insert has no prior field value and cannot be stale: "
        f"{[(v.rule_id, v.reason) for v in outcome.validation.violations]}"
    )
