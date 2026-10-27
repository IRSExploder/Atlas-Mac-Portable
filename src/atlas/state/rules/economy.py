"""Economic magnitudes.

Nations grow and collapse, but not instantly and not without cause. These caps are what
stop a model narrating a nation into a superpower in one jump because the story called
for it.

**The caps scale with the length of the jump**, since a year should permit more movement
than a week. This file claimed that from the initial commit and did not do it until PT20
noticed: no rule read the turn length at all, so the same 20% bound applied to a week and
to a year, and the player who jumped in years was held to a twelfth of the growth the one
who jumped in months could have.

The scaling is linear in the turn's length rather than compounded, which is argued at the
call site: compounding is right for the quantity and wrong for a bound on one proposal.
"""

from __future__ import annotations

from ...engine.pacing import over
from ..models import EventKind, Role
from ..validate import RuleContext, Violation, register

__all__ = [
    "REFORM_MULTIPLIER",
    "SOFT_CAPS",
    "econ_bounds",
    "econ_change_capped",
    "econ_reform_justified",
]

#: Fractional change permitted per turn without a reform event, per field.
#:
#: Public because the briefings quote it: a model that is never told the cap
#: keeps proposing changes the engine throws away, losing the rest of its batch
#: with them. Read rather than restated, so the briefing cannot drift from what
#: is enforced.
SOFT_CAPS: dict[str, float] = {
    "economy": 0.15,
    "military_strength": 0.20,
    "treasury": 0.50,
    "manpower": 0.25,
}

#: What a ratified reform buys you: the cap is multiplied by this. Public for the
#: same reason as SOFT_CAPS.
REFORM_MULTIPLIER = 3.0

#: The nation columns ``ECON-003`` holds inside 0-100. Kept beside the rule that enforces
#: it and mirrored by :data:`atlas.engine.bounds.BOUNDS`, which is what stops a writer
#: authoring the overshoot in the first place. ``tests/test_bounds.py`` asserts they agree.
_BOUNDED_COLUMNS = ("stability", "legitimacy")


@register("ECON-001", "Economic aggregates may not jump more than the per-turn cap.")
def econ_change_capped(ctx: RuleContext):
    """Bound how fast the numbers can move.

    The cap is computed from the value *before* the change, so a nation at zero is not
    permitted to grow infinitely fast from an epsilon base -- a floor is applied.
    """
    if ctx.batch.proposer_role is Role.ENGINE:
        return

    reformers = _nations_with_reform(ctx)

    # Aggregated per field, not per diff. Checking each diff alone means N sub-cap steps
    # on the same field multiply the cap by N: forty diffs each under 15% took one nation
    # from 20 to 132 in a single turn, every one of them individually legal. The cap is a
    # statement about how fast a number may move in a turn, so it has to be measured over
    # everything that moved it.
    moves: dict[tuple[str, str], list] = {}
    for diff in ctx.batch.diffs:
        if diff.target_table != "nation" or diff.field not in SOFT_CAPS:
            continue
        before = _as_float(diff.old_value)
        after = _as_float(diff.new_value)
        if before is None or after is None:
            continue
        entry = moves.setdefault((diff.target_id, diff.field), [before, after, diff.seq])
        # First value seen is the true starting point; last is where the field ended up.
        entry[1] = after
        entry[2] = diff.seq

    for (target_id, column), (before, after, seq) in moves.items():
        # **The cap is per month, so a longer turn permits proportionally more** -- and
        # linearly, not compounded, which is a deliberate choice against the more
        # obvious one. Compounding is what the *quantity* does (twelve months of 50%
        # treasury growth is 139x, not 7x), and applying that here made the year-long
        # cap 13,801%: not a bound at all. A guardrail that switches itself off exactly
        # when one decision covers the most time is worse than one that is slightly
        # tight, because this rule exists to stop a single proposal narrating a
        # superpower into being, not to budget a nation's growth.
        #
        # So twelve monthly turns still permit more in total than one yearly turn. That
        # is not the asymmetry the pacing work set out to remove: a staff making twelve
        # decisions against twelve turns of feedback *should* achieve more than one
        # making a single decision. That is what finer control is.
        cap = over(SOFT_CAPS[column], ctx.span)
        allowed = cap * (REFORM_MULTIPLIER if target_id in reformers else 1.0)
        # Floor the base so growth from near-zero is bounded in absolute terms too.
        base = max(abs(before), 10.0)
        change = abs(after - before)
        if change > base * allowed:
            yield Violation(
                "ECON-001",
                f"{column} for {target_id} moved {change:.1f} "
                f"({before:.1f} -> {after:.1f}) across this batch, above the "
                f"{allowed:.0%} per-turn cap; a change this large needs a ratified "
                "economic reform",
                seq,
            )


def _nations_with_reform(ctx: RuleContext) -> set[str]:
    """Nations with an engine-authored reform event this turn.

    ``origin='ENGINE'`` matters: otherwise a nation writes itself a reform event and
    triples its own cap, which is the whole exploit this design exists to close.
    """
    rows = ctx.query(
        """
        SELECT DISTINCT actor_nation_id FROM event
         WHERE world_id = ? AND turn = ? AND kind = ? AND origin = 'ENGINE'
           AND actor_nation_id IS NOT NULL
        """,
        (ctx.world_id, ctx.turn, EventKind.ECONOMIC_REFORM.value),
    )
    return {row["actor_nation_id"] for row in rows}


@register("ECON-002", "A reform event must be engine-authored to unlock a larger change.")
def econ_reform_justified(ctx: RuleContext):
    """Catch a model-authored reform event outright.

    ``economic_reform`` is absent from the event schema handed to models, so this should
    be unreachable. It is kept because "should be unreachable" is a statement about
    today's code, and this is cheap.
    """
    rows = ctx.query(
        """
        SELECT id, actor_nation_id FROM event
         WHERE world_id = ? AND turn = ? AND kind = ? AND origin <> 'ENGINE'
        """,
        (ctx.world_id, ctx.turn, EventKind.ECONOMIC_REFORM.value),
    )
    for row in rows:
        yield Violation(
            "ECON-002",
            f"event {row['id']} claims an economic reform for {row['actor_nation_id']} but was "
            "not authored by the engine",
        )


@register("ECON-003", "Stability and legitimacy stay within their defined range.")
def econ_bounds(ctx: RuleContext):
    """Keep bounded scores bounded -- **for the writes this batch actually made**.

    SQLite has a CHECK on ``relation.score`` but not on these, because they are adjusted
    by deltas that can legitimately overshoot mid-computation. Clamping silently would
    hide a resolution bug, so this reports instead.

    What it used to report was every out-of-range nation *in the world*, on any batch that
    touched the nation table at all. Two things followed, and PT39 found both:

    * **collateral refusal.** One nation left out of range by anything at all condemned
      every later batch that touched any nation anywhere -- a treasury credit to France
      refused because Germany was out of range, on a batch that named neither the column
      nor the country. The batch did not cause the breach and could not fix it, so the
      world simply stopped being able to write to the nation table.
    * **an unseverable violation.** Naming no diff, it took the whole batch every time.
      A finished public work in PT39 carried development, industry, a construction
      deletion and one stability charge of -1.0 against a government sitting at 0.0. The
      charge was the only thing wrong with it and all four were thrown away, every turn,
      for the rest of the game.

    So the rule asks only about the ``(nation, column)`` pairs the batch wrote, and names
    the diff that wrote them. The invariant is unchanged -- no batch may leave a bounded
    column outside its range -- and it is now enforced against the batch responsible.

    A pre-existing breach is therefore no longer this batch's problem. That is deliberate:
    nothing in the pipeline can write a value out of range any more (see
    :mod:`atlas.engine.bounds`), and if something does, the batch that did it is the one
    that gets refused.
    """
    written: dict[str, list[int]] = {}
    for diff in ctx.batch.diffs:
        if diff.target_table == "nation" and diff.field in _BOUNDED_COLUMNS:
            written.setdefault(diff.field, []).append(diff.seq)
    if not written:
        return

    ids = sorted({
        str(d.target_id) for d in ctx.batch.diffs
        if d.target_table == "nation" and d.field in _BOUNDED_COLUMNS
    })
    placeholders = ",".join("?" * len(ids))
    rows = ctx.query(
        f"""
        SELECT id, name, stability, legitimacy FROM nation
         WHERE world_id = ? AND is_alive = 1 AND id IN ({placeholders})
        """,  # noqa: S608 - placeholders only, ids are bound
        (ctx.world_id, *ids),
    )
    for row in rows:
        for column in sorted(written):
            value = row[column]
            if value is None or 0 <= float(value) <= 100:
                continue
            # Name the write, so the apply layer can drop that one charge and let the rest
            # of the batch stand rather than losing a whole turn's work to it.
            seq = next(
                (d.seq for d in ctx.batch.diffs
                 if d.target_table == "nation" and d.target_id == row["id"]
                 and d.field == column),
                None,
            )
            yield Violation(
                "ECON-003",
                f"{row['name']} would be left with {column} {float(value):.1f}; "
                "it must stay within 0-100",
                diff_seq=seq,
            )


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


