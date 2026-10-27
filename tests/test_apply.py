"""The diff pipeline: apply, reject, roll back, and keep the evidence."""

from __future__ import annotations

from atlas.state.apply import apply_batch
from atlas.state.bootstrap import fingerprint
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, Role

from .conftest import diff


def _economy(conn, nation="karthos") -> float:
    return conn.execute("SELECT economy FROM nation WHERE id = ?", (nation,)).fetchone()[0]


def test_a_legal_batch_applies(conn, batch_factory):
    before = _economy(conn)
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 22.0)))
    assert outcome.applied, outcome.reason
    assert _economy(conn) == 22.0 != before


def test_adjust_resolves_to_an_absolute_value(conn, batch_factory):
    """``adjust`` adds a delta; the recorded diff stores the resulting absolute value.

    Rules depend on this: they compute change as ``new - old`` without re-checking the
    op. Storing the delta in ``new_value`` would make every magnitude cap double-count.
    """
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 2.0, op=DiffOp.ADJUST))
    )
    assert outcome.applied, outcome.reason
    assert _economy(conn) == 22.0
    assert outcome.diffs[0].old_value == 20.0
    assert outcome.diffs[0].new_value == 22.0


def test_rejected_batch_leaves_world_state_byte_identical(conn, batch_factory):
    """The rollback guarantee."""
    before = fingerprint(conn, "sandbox")
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 99999.0))
    )
    assert not outcome.applied
    assert fingerprint(conn, "sandbox") == before


def test_a_rejected_batch_is_still_recorded(conn, batch_factory):
    """Evidence of an attempt must survive the rollback that undid it.

    This is why proposed rows are committed before the savepoint opens. If they were
    written inside it, the audit trail would contain only the batches that were legal --
    precisely the opposite of what an audit trail is for.
    """
    apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 99999.0)))

    row = conn.execute(
        "SELECT status, rejection_reason FROM diff_batch WHERE world_id = 'sandbox'"
    ).fetchone()
    assert row["status"] == "rejected"
    assert "ECON-001" in row["rejection_reason"]

    stored = conn.execute("SELECT rejected_by_rule FROM diff").fetchone()
    assert stored["rejected_by_rule"] == "ECON-001"


def test_partial_failure_writes_nothing(conn, batch_factory):
    """One bad diff condemns the whole batch; a half-applied batch is never left behind."""
    before = fingerprint(conn, "sandbox")
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("nation", "karthos", "economy", 21.0),   # legal on its own
            diff("nation", "karthos", "stability", 500.0),  # out of bounds
        ),
    )
    assert not outcome.applied
    assert fingerprint(conn, "sandbox") == before
    assert _economy(conn) == 20.0, "the legal diff must not survive its batch being rejected"


def test_compare_and_swap_catches_stale_state(conn, batch_factory):
    """A batch built against a snapshot that has since moved is refused, not merged."""
    apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 22.0)))

    stale = batch_factory(
        diff("nation", "karthos", "economy", 23.0, prior=20.0)  # expects the old value
    )
    outcome = apply_batch(conn, stale)
    assert not outcome.applied
    assert any(v.rule_id == "CORE-004" for v in outcome.validation.violations)
    assert _economy(conn) == 22.0


def test_compare_and_swap_passes_when_state_is_current(conn, batch_factory):
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 22.0, prior=20.0))
    )
    assert outcome.applied, outcome.reason


def test_compare_and_swap_uses_the_precision_the_model_saw(conn, batch_factory):
    """A one-decimal briefing renders 20.58 as 20.6; citing it is not stale state."""
    conn.execute("UPDATE nation SET economy = 20.58 WHERE id = 'karthos'")
    conn.commit()

    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 21.0, prior=20.6))
    )
    assert outcome.applied, outcome.reason


def test_compare_and_swap_still_rejects_a_visible_numeric_change(conn, batch_factory):
    conn.execute("UPDATE nation SET economy = 20.66 WHERE id = 'karthos'")
    conn.commit()

    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 21.0, prior=20.6))
    )
    assert not outcome.applied
    assert any(v.rule_id == "CORE-004" for v in outcome.validation.violations)


def test_unknown_column_is_refused_not_executed(conn, batch_factory):
    """``diff.field`` is model-chosen and interpolated into SQL, so it must be validated.

    A column name cannot be parameterised. This asserts the identifier allowlist holds,
    which is what stops that interpolation being an injection surface.
    """
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy = 1; DROP TABLE nation --", 1))
    )
    assert not outcome.applied
    assert conn.execute("SELECT COUNT(*) FROM nation").fetchone()[0] == 3


def test_unknown_table_is_refused(conn, batch_factory):
    outcome = apply_batch(conn, batch_factory(diff("sqlite_master", "x", "name", "y")))
    assert not outcome.applied


def test_model_events_are_stamped_model_origin(conn, batch_factory):
    """A model can never label its own event as engine-authored."""
    batch = batch_factory(
        diff("nation", "karthos", "economy", 21.0),
        events=({"kind": "diplomatic_message", "text": "We protest.", "actor_nation_id": "karthos"},),
    )
    outcome = apply_batch(conn, batch)
    assert outcome.applied, outcome.reason

    row = conn.execute(
        "SELECT origin FROM event WHERE kind = 'diplomatic_message'"
    ).fetchone()
    assert row["origin"] == "MODEL"


def test_rejected_batch_does_not_leave_its_narrative_behind(conn, batch_factory):
    """Events from a refused batch must not persist as if they had happened."""
    batch = batch_factory(
        diff("nation", "karthos", "economy", 99999.0),
        events=({"kind": "narrative", "text": "Karthos becomes rich beyond measure."},),
    )
    outcome = apply_batch(conn, batch)
    assert not outcome.applied
    assert conn.execute("SELECT COUNT(*) FROM event WHERE kind = 'narrative'").fetchone()[0] == 0


def test_world_date_is_the_in_world_date(conn):
    """Regression: ``current_date`` is a SQL keyword.

    Named that way, ``SELECT current_date FROM world`` silently returns today's real
    date instead of the column, and every event gets stamped with the wrong year.
    """
    row = conn.execute("SELECT world_date FROM world WHERE id = 'sandbox'").fetchone()
    assert row["world_date"] == "1897-03-01"


def test_a_guessed_cause_id_does_not_destroy_the_batch(conn, batch_factory):
    """A model citing an event id it expected to exist must not cost a whole turn.

    Left to the foreign keys this raises IntegrityError and discards everything, reported
    as an unreadable database error. Observed against a real world simulation.
    """
    batch = batch_factory(
        diff("nation", "karthos", "economy", 21.0),
        events=({"kind": "narrative", "text": "something happened", "causes": [9999]},),
    )
    outcome = apply_batch(conn, batch)
    assert outcome.applied, outcome.reason
    assert conn.execute("SELECT COUNT(*) FROM event_cause").fetchone()[0] == 0


def test_a_nation_named_in_prose_is_resolved_to_its_id(conn, batch_factory):
    """Models write "Aurelia" where the schema wants "aurelia".

    Names are unique per world, so resolving is unambiguous and beats discarding a sound
    turn over capitalisation.
    """
    batch = batch_factory(
        diff("nation", "karthos", "economy", 21.0),
        events=({"kind": "narrative", "text": "x", "actor_nation_id": "Aurelia"},),
    )
    outcome = apply_batch(conn, batch)
    assert outcome.applied, outcome.reason
    row = conn.execute("SELECT actor_nation_id FROM event WHERE kind = 'narrative'").fetchone()
    assert row["actor_nation_id"] == "aurelia"


def test_an_unknown_nation_is_dropped_rather_than_fatal(conn, batch_factory):
    batch = batch_factory(
        diff("nation", "karthos", "economy", 21.0),
        events=(
            {
                "kind": "narrative",
                "text": "x",
                "visibility": "secret",
                "visible_to": ["aurelia", "Atlantis"],
            },
        ),
    )
    outcome = apply_batch(conn, batch)
    assert outcome.applied, outcome.reason
    visible = {r["nation_id"] for r in conn.execute("SELECT nation_id FROM event_visibility")}
    assert visible == {"aurelia"}, "the real nation survives, the invented one is dropped"


def test_engine_role_may_write_engine_only_columns(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("region", "r_coast", "owner_nation_id", "aurelia"), role=Role.ENGINE
        ),
    )
    assert outcome.applied, outcome.reason


# --------------------------------------------------------------------------- severing


def test_one_stale_prior_does_not_cost_the_batch(conn, genesis):
    """A model that guessed a directed relation was symmetric discarded three good diffs.

    The rest of the batch is independently valid: each diff carries its own citation and
    its own prior. Losing them cost the player whole turns of narrative.
    """
    from atlas.state.apply import apply_batch
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role

    before = conn.execute("SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0]
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.ADJUST, 2.0, reason_event_id=genesis),
            Diff("nation", "karthos", "stability", DiffOp.ADJUST, 1.0, reason_event_id=genesis),
            Diff("nation", "karthos", "legitimacy", DiffOp.ADJUST, 5.0,
                 expected_prior=9999, reason_event_id=genesis),
        ),
    ))

    assert outcome.applied
    assert outcome.severed == (2,)
    after = conn.execute("SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0]
    assert after == before + 2


def test_a_severed_diff_is_still_on_the_record(conn, genesis):
    """The attempt you most want on record is the one that did not land."""
    from atlas.state.apply import apply_batch
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role

    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.ADJUST, 2.0, reason_event_id=genesis),
            Diff("nation", "karthos", "legitimacy", DiffOp.ADJUST, 5.0,
                 expected_prior=9999, reason_event_id=genesis),
        ),
    ))
    row = conn.execute(
        "SELECT status, rejected_by_rule FROM diff WHERE batch_id = ? AND seq = 1",
        (outcome.batch_id,),
    ).fetchone()
    assert row["status"] == "rejected"
    assert row["rejected_by_rule"] == "CORE-004"

    status = conn.execute(
        "SELECT status FROM diff_batch WHERE id = ?", (outcome.batch_id,)
    ).fetchone()["status"]
    assert status == "partial"


def test_a_real_violation_still_condemns_the_whole_batch(conn, genesis):
    """Severability is an exception for stale priors, never a general softening."""
    from atlas.state.apply import apply_batch
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role

    before = conn.execute("SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0]
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.ADJUST, 2.0, reason_event_id=genesis),
            # Engine-only: a model may never write it, and the batch dies with it.
            Diff("relation", "karthos|aurelia", "at_war", DiffOp.SET, 1,
                 reason_event_id=genesis),
        ),
    ))

    assert not outcome.applied
    assert not outcome.severed
    after = conn.execute("SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0]
    assert after == before


def test_a_batch_that_is_only_stale_priors_is_refused(conn, genesis):
    """Nothing survives severing, so there is nothing to keep."""
    from atlas.state.apply import apply_batch
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role

    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.ADJUST, 2.0,
                 expected_prior=9999, reason_event_id=genesis),
        ),
    ))
    assert not outcome.applied


def test_severability_is_whether_a_violation_names_a_diff():
    """The rules already draw this line and the apply layer now believes them.

    A violation that names a diff condemns that diff; one that names none condemns the
    batch. The second list this used to consult -- one rule id, checked per violation --
    was throwing away every correct write in a batch that had one bad one.
    """
    from atlas.state.validate import Violation

    assert Violation("CORE-001", "nope", 3).severable
    assert not Violation("CORE-001", "nope").severable
    assert not Violation("UNIT-001", "conjured").severable


def test_only_a_stale_prior_may_sever_without_limit():
    """Every addition to *this* set weakens the minority bound, which is the thing now
    standing where the old whitelist stood."""
    from atlas.state.validate import _UNLIMITED_SEVERABLE, Violation

    assert frozenset({"CORE-004"}) == _UNLIMITED_SEVERABLE
    assert Violation("CORE-004", "stale", 1).unlimited
    assert not Violation("CORE-001", "forbidden", 1).unlimited


def _regard(conn, a="karthos", b="aurelia") -> float:
    return conn.execute(
        "SELECT score FROM relation WHERE nation_a = ? AND nation_b = ?", (a, b)
    ).fetchone()[0]


def test_a_relation_score_over_the_hard_bound_is_clamped_not_dropped(conn, batch_factory):
    """PT9/PT10: models overshoot the -100..100 range; clamp to the edge, don't drop the batch.

    Start near the ceiling so the *change* stays inside the DIP-002 per-turn swing cap -- this
    pins the hard bound, not the swing rule.
    """
    conn.execute("UPDATE relation SET score = 90 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()
    outcome = apply_batch(
        conn, batch_factory(diff("relation", "karthos|aurelia", "score", 150.0))
    )
    assert outcome.applied, outcome.reason
    assert _regard(conn) == 100.0, "an overshoot clamps to the ceiling, batch survives"


def test_a_relation_score_below_the_hard_bound_is_clamped(conn, batch_factory):
    conn.execute("UPDATE relation SET score = -90 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()
    outcome = apply_batch(
        conn, batch_factory(diff("relation", "karthos|aurelia", "score", -150.0))
    )
    assert outcome.applied, outcome.reason
    assert _regard(conn) == -100.0


def test_an_in_range_relation_score_is_untouched(conn, batch_factory):
    """The clamp only bites at the edges; a legal value passes through unchanged."""
    conn.execute("UPDATE relation SET score = 10 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()
    outcome = apply_batch(
        conn, batch_factory(diff("relation", "karthos|aurelia", "score", 30.0))
    )
    assert outcome.applied, outcome.reason
    assert _regard(conn) == 30.0


# --------------------------------------------------- CORE-004 comparison precision


def test_the_compare_and_swap_precision_matches_what_briefings_show(conn, genesis):
    """**The guard against the precision table and the prompts drifting apart.**

    CORE-004 compares an expected prior against the value at write time, and it has to do
    that at the precision the model could actually *see*. Both halves are checked here
    against the real view builders rather than against a written-down belief: a field
    shown to one decimal must compare to one, and regard -- shown `{:+.0f}` -- must
    compare to zero, or a model handed `+42` is rejected for not knowing about `42.37`.
    """
    import re

    from atlas.engine.views import build_tracked_agent_view
    from atlas.state.rules.core import shown_precision

    conn.execute("UPDATE nation SET economy = 20.58 WHERE id = 'karthos'")
    conn.execute(
        "UPDATE relation SET score = 42.37 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()

    text = build_tracked_agent_view(conn, "sandbox", nation_id="karthos").text

    # Rendered to one decimal, so CORE-004 compares at one.
    assert "economy 20.6" in text, text[:600]
    assert shown_precision("nation", "economy") == 1

    # Rendered with no decimals at all, so comparing at one would reject a prior the
    # model had no way to know was stale.
    regard = re.search(r"Aurelia \(`aurelia`\): regard ([+-]?[\d.]+)", text)
    assert regard is not None, text[:900]
    assert "." not in regard.group(1), f"regard is now rendered as {regard.group(1)!r}"
    assert shown_precision("relation", "score") == 0


def test_a_prior_taken_from_the_briefing_is_not_rejected_as_stale(conn, genesis):
    """End to end, on the two fields whose renderings differ.

    The model is shown a rounded figure and cites it back. Neither should be refused --
    that is a paid batch thrown away for citing exactly the snapshot it was handed.
    """
    conn.execute("UPDATE nation SET economy = 20.58 WHERE id = 'karthos'")
    conn.execute(
        "UPDATE relation SET score = 42.37 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()

    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.SET, 21.0,
                 expected_prior=20.6, reason_event_id=genesis),
            Diff("relation", "karthos|aurelia", "score", DiffOp.SET, 45.0,
                 expected_prior=42, reason_event_id=genesis),
        ),
    ))
    assert outcome.applied, outcome.reason


def test_a_move_the_briefing_would_have_shown_is_still_caught(conn, genesis):
    """The loosening must not cost the rule its job: a change big enough to be visible in
    the prompt still fails, on both precisions."""
    conn.execute("UPDATE nation SET economy = 25.0 WHERE id = 'karthos'")
    conn.execute(
        "UPDATE relation SET score = 50.0 WHERE nation_a='karthos' AND nation_b='aurelia'")
    conn.commit()

    for moved in (
        Diff("nation", "karthos", "economy", DiffOp.SET, 26.0,
             expected_prior=20.6, reason_event_id=genesis),
        Diff("relation", "karthos|aurelia", "score", DiffOp.SET, 55.0,
             expected_prior=42, reason_event_id=genesis),
    ):
        outcome = apply_batch(conn, DiffBatch(
            world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM,
            proposer_model="test", diffs=(moved,)))
        assert not outcome.applied, f"{moved.field} moved visibly and was not caught"
        assert "CORE-004" in outcome.reason


def test_only_model_batches_are_gated_by_this(conn, genesis):
    """`expected_prior` is set by the parser and by nothing else, so no engine-authored
    diff reaches this comparison. That is what bounds the change to model output."""
    from atlas.state.diff import Diff as _Diff

    assert _Diff.__dataclass_fields__["expected_prior"].default is None


# ---------------------------------------------------- the row a diff says it addresses
#
# Both of these were found by the Codex review of phase 2 and both are the same mistake in
# two ops: the diff layer let the row a change *landed on* differ from the row the audit
# trail *named*. That breaks rewind, which addresses rows by `target_id` and nothing else.


def test_an_update_to_a_row_that_is_not_there_is_refused(conn, genesis):
    """It used to report applied. `UPDATE ... WHERE` on a missing row changes nothing and
    raises nothing, so the batch stood, the diff was recorded with `old_value = None`, and
    a rule computing `new_value - old_value` saw a swing out of nowhere."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("nation", "no_such_nation", "stability", DiffOp.SET, 42.0,
                    reason_event_id=genesis),)))

    assert not outcome.applied
    assert "does not exist" in outcome.reason


def test_a_real_update_still_applies(conn, genesis):
    """The other half: tightening the check must not refuse an ordinary write."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("nation", "karthos", "stability", DiffOp.SET, 42.0,
                    reason_event_id=genesis),)))

    assert outcome.applied, outcome.reason
    assert conn.execute(
        "SELECT stability FROM nation WHERE id = 'karthos'").fetchone()[0] == 42.0


def test_a_column_that_is_legitimately_null_is_not_read_as_a_missing_row(conn, genesis):
    """The reason existence is checked rather than the value: `_read_current` returning
    `None` cannot tell a NULL column from an absent row, and treating the first as the
    second would refuse every first write to a nullable field."""
    assert conn.execute(
        "SELECT tracked_reason FROM nation WHERE id = 'karthos'").fetchone()[0] is None

    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("nation", "karthos", "tracked_reason", DiffOp.SET, "watched",
                    reason_event_id=genesis),)))
    assert outcome.applied, outcome.reason


def test_an_insert_cannot_land_under_a_different_id_than_it_names(conn, genesis):
    """**Reproduced on `construction`:** the diff said `c_named`, the payload said
    `c_actually`, the row appeared as `c_actually`, and the batch reported applied. A
    rewind deletes by `target_id`, so it found nothing and the row outlived its turn."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("construction", "c_named", "__insert__", DiffOp.INSERT,
                    {"id": "c_actually", "world_id": "sandbox", "nation_id": "karthos",
                     "region_id": "r_south", "kind": "infantry", "days_remaining": 30.0,
                     "started_turn": 0},
                    reason_event_id=genesis),)))

    assert not outcome.applied
    assert "c_named" in outcome.reason
    assert conn.execute("SELECT COUNT(*) FROM construction").fetchone()[0] == 0


def test_an_insert_may_repeat_the_id_it_names(conn, genesis):
    """Agreeing with `target_id` is not a conflict. Refusing it would break every writer
    that fills the payload out in full."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("construction", "c_named", "__insert__", DiffOp.INSERT,
                    {"id": "c_named", "world_id": "sandbox", "nation_id": "karthos",
                     "region_id": "r_south", "kind": "infantry", "days_remaining": 30.0,
                     "started_turn": 0},
                    reason_event_id=genesis),)))

    assert outcome.applied, outcome.reason
    assert conn.execute(
        "SELECT COUNT(*) FROM construction WHERE id = 'c_named'").fetchone()[0] == 1


def test_a_composite_key_insert_is_held_to_the_same_rule(conn, genesis):
    """`setdefault` let a payload win on every primary-key column, not only on `id`."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("technology", "karthos|welding", "__insert__", DiffOp.INSERT,
                    {"nation_id": "aurelia", "tech_id": "welding", "world_id": "sandbox",
                     "completed_turn": 0},
                    reason_event_id=genesis),)))

    assert not outcome.applied
    assert "nation_id" in outcome.reason


def test_an_insert_that_names_only_the_rest_of_the_row_still_works(conn, genesis):
    """The normal case, and the one the old `setdefault` existed for: a composite-key
    payload that leaves both key columns to `target_id`."""
    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("technology", "karthos|welding", "__insert__", DiffOp.INSERT,
                    {"world_id": "sandbox", "completed_turn": 0},
                    reason_event_id=genesis),)))

    assert outcome.applied, outcome.reason
    assert conn.execute(
        "SELECT COUNT(*) FROM technology WHERE nation_id = 'karthos'"
        "   AND tech_id = 'welding'").fetchone()[0] == 1


def test_a_refused_insert_leaves_the_world_where_it_was(conn, genesis):
    """The identity check runs inside the savepoint like every other refusal, so a batch
    that trips it rolls back whole rather than half."""
    before = fingerprint(conn, "sandbox")
    apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(
            Diff("nation", "karthos", "stability", DiffOp.SET, 99.0,
                 reason_event_id=genesis),
            Diff("construction", "c_named", "__insert__", DiffOp.INSERT,
                 {"id": "c_elsewhere", "world_id": "sandbox", "nation_id": "karthos",
                  "region_id": "r_south", "kind": "infantry", "days_remaining": 30.0,
                  "started_turn": 0},
                 reason_event_id=genesis),
        )))
    assert fingerprint(conn, "sandbox") == before
