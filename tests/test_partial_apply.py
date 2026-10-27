"""Partial apply: three bad diffs should not cost twenty-one good ones.

A batch was all-or-nothing except for one whitelisted rule. Measured against a real
model's output, that meant a proposal with 24 changes and 3 mistakes landed *nothing* --
the turn's whole narrative thrown away, and the cost of generating it thrown away with it.

What is pinned here, in order of how much damage getting it wrong would do:

* **rewind stays exact** -- a partially applied batch must rewind to a byte-identical
  world, because per-diff status is what replay reads and a half-honoured batch is the
  one shape that could quietly diverge;
* a violation that names no diff still condemns the whole batch, because those are the
  joint ones and no subset of the batch answers them;
* a batch that is *mostly* refused is still refused whole -- the bound that replaced the
  old whitelist;
* the refusals survive, with the rule that caught each one and its own words;
* a stale prior still severs without limit, which is the behaviour that already existed
  and must not regress.
"""

from __future__ import annotations

from atlas.state.apply import apply_batch
from atlas.state.bootstrap import fingerprint
from atlas.state.diff import DiffBatch
from atlas.state.models import DiffOp, Role

from .conftest import diff

#: An event id nothing wrote. CORE-001 refuses the diff that cites it and names that diff,
#: which makes it the cleanest way to put one bad write in an otherwise good batch.
NO_SUCH_EVENT = 987654


def _good(field: str, value: float, cite: int):
    return diff("nation", "karthos", field, value, cite=cite)


def _bad(field: str, value: float):
    return diff("nation", "karthos", field, value, cite=NO_SUCH_EVENT)


def _batch(*diffs, turn: int = 0) -> DiffBatch:
    return DiffBatch(
        world_id="sandbox", turn=turn, proposer_role=Role.WORLD_SIM,
        proposer_model="test", diffs=tuple(diffs),
    )


# ----------------------------------------------------------------- the thing it is for


def test_a_minority_of_bad_diffs_costs_only_itself(conn, genesis):
    """The case this exists for, in miniature: four good writes and one bad one."""
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _good("legitimacy", 52.0, genesis),
        _good("treasury", 120.0, genesis),
        _bad("military_strength", 34.0),
    ))
    assert outcome.applied, outcome.reason
    assert len(outcome.severed) == 1
    row = conn.execute(
        "SELECT economy, stability, legitimacy, military_strength"
        "  FROM nation WHERE id = 'karthos'"
    ).fetchone()
    assert row["economy"] == 21.0
    assert row["stability"] == 55.0
    assert row["legitimacy"] == 52.0
    assert row["military_strength"] != 34.0, "the refused write landed anyway"


def test_the_refusal_keeps_the_rule_that_caught_it_and_its_own_words(conn, genesis):
    """"Three changes were dropped" is not a report. Which three, and why, is."""
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
    ))
    assert outcome.applied, outcome.reason
    assert len(outcome.refusals) == 1
    seq, rule_id, reason = outcome.refusals[0]
    assert rule_id == "CORE-001"
    assert str(NO_SUCH_EVENT) in reason

    stored = conn.execute(
        "SELECT status, rejected_by_rule, rejection_reason FROM diff"
        " WHERE batch_id = ? AND seq = ?", (outcome.batch_id, seq),
    ).fetchone()
    assert stored["status"] == "rejected"
    assert stored["rejected_by_rule"] == "CORE-001"
    assert str(NO_SUCH_EVENT) in stored["rejection_reason"]

    batch_row = conn.execute(
        "SELECT status, rejection_reason FROM diff_batch WHERE id = ?",
        (outcome.batch_id,),
    ).fetchone()
    assert batch_row["status"] == "partial"
    assert "CORE-001" in batch_row["rejection_reason"]


def test_the_diffs_that_stood_are_recorded_as_applied(conn, genesis):
    """Replay reads per-diff status, so what stood has to say so in the database and not
    only in the returned outcome."""
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
    ))
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) AS n FROM diff WHERE batch_id = ? GROUP BY status",
        (outcome.batch_id,),
    ).fetchall())
    assert counts == {"applied": 2, "rejected": 1}


# --------------------------------------------------------------------- what still fails


def test_a_batch_that_is_mostly_refused_is_refused_whole(conn, genesis):
    """The bound that replaced the whitelist. A batch where most of the writes are wrong
    misunderstood the contract, and landing the minority that happened to pass is worse
    than landing none of it."""
    before = fingerprint(conn, "sandbox")
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
        _bad("legitimacy", 51.0),
        _bad("manpower", 110.0),
    ))
    assert not outcome.applied
    assert fingerprint(conn, "sandbox") == before


def test_exactly_half_is_not_a_minority(conn, genesis):
    """A batch has to be more right than wrong to stand, not merely not-mostly-wrong."""
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
        _bad("legitimacy", 51.0),
    ))
    assert not outcome.applied


def test_a_violation_that_names_no_diff_still_condemns_the_batch(conn, genesis):
    """The joint rules. An unpaid-for war is a property of the batch, not of one write in
    it: relations are directed, so a declaration is two rows and an event, and no subset
    of those is a smaller war.

    **This used to be demonstrated with `stability` out of range**, which is no longer a
    joint violation: ECON-003 now names the diff that would leave a column out of range,
    so the apply layer drops that one charge instead of losing the turn's work with it. A
    finished public work carrying development, industry and one unpayable stability charge
    was costing all three, every turn, for the rest of a game. See
    `tests/test_a_bounded_column_costs_only_its_own_write.py`.
    """
    before = fingerprint(conn, "sandbox")
    outcome = apply_batch(conn, _batch(
        diff("relation", "karthos|veyra", "at_war", 1, cite=genesis),
        diff("relation", "veyra|karthos", "at_war", 1, cite=genesis),
    ))
    assert not outcome.applied
    assert "WAR-001" in outcome.reason
    assert outcome.severed == (), "a joint violation names no diff, so none can be severed"
    assert fingerprint(conn, "sandbox") == before, (
        "half a war is worse than none: the mirror row must not survive on its own")


def test_a_batch_of_one_bad_diff_is_simply_rejected(conn):
    """Severing it would leave nothing, which is a rejection wearing a different word."""
    outcome = apply_batch(conn, _batch(_bad("economy", 21.0)))
    assert not outcome.applied
    assert outcome.severed == ()


# ------------------------------------------------------------------- the rewind promise


def test_a_partially_applied_batch_rewinds_exactly(conn, genesis):
    """**The one shape that could quietly diverge.** Replay reads `diff.status`, so a
    batch that half-happened has to unwind by exactly the half that did."""
    from atlas.state.replay import rewind_to

    # The baseline is taken standing *on* turn 1 and the batch is put on turn 2, because a
    # rewind restores the world's date from the turn row it lands on. A baseline taken at
    # turn 1 and compared after rewinding to turn 0 differs in `world.world_date` for a
    # reason that has nothing to do with this batch -- which is how a rewind test passes,
    # or fails, for the wrong reason.
    for number, date in ((1, "1936-02-01"), (2, "1936-03-01")):
        conn.execute(
            "INSERT INTO turn (world_id, number, phase, status, start_date)"
            " VALUES ('sandbox', ?, 'sealed', 'sealed', ?)", (number, date))
    conn.execute(
        "UPDATE world SET turn_number = 1, world_date = '1936-02-01' WHERE id = 'sandbox'")
    conn.commit()
    before = fingerprint(conn, "sandbox")

    conn.execute(
        "UPDATE world SET turn_number = 2, world_date = '1936-03-01' WHERE id = 'sandbox'")
    conn.commit()
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
        turn=2,
    ))
    assert outcome.applied and len(outcome.severed) == 1

    report = rewind_to(conn, "sandbox", 1)
    assert report.ok, report.problems
    assert fingerprint(conn, "sandbox") == before, (
        "a partial batch did not unwind to where it started")


# ---------------------------------------------------------- what already worked, still


def test_a_stale_prior_still_severs_past_the_minority(conn, genesis):
    """The pre-existing behaviour, and the one exemption from the bound: a stale
    compare-and-swap says one write was reasoned about under conditions that no longer
    hold and nothing whatever about the others, so ten of them are ten independent
    accidents rather than evidence about the batch."""
    outcome = apply_batch(conn, _batch(
        diff("nation", "karthos", "economy", 21.0, cite=genesis),
        diff("nation", "karthos", "stability", 55.0, cite=genesis, prior=9999),
        diff("nation", "karthos", "legitimacy", 52.0, cite=genesis, prior=9999),
        diff("nation", "karthos", "treasury", 120.0, cite=genesis, prior=9999),
    ))
    assert outcome.applied, outcome.reason
    assert len(outcome.severed) == 3, "the stale writes were bounded by the minority rule"
    assert conn.execute(
        "SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0] == 21.0


def test_a_clean_batch_is_untouched_by_any_of_this(conn, genesis):
    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
    ))
    assert outcome.applied and outcome.severed == () and outcome.refusals == ()


def test_severing_is_bounded_and_terminates(conn, genesis):
    """A batch of nothing but bad writes must not loop: it fails the bound on the first
    pass and is refused, rather than shrinking one diff at a time."""
    outcome = apply_batch(conn, _batch(*[
        _bad(field, 1.0) for field in
        ("economy", "stability", "legitimacy", "treasury", "manpower")
    ]))
    assert not outcome.applied
    assert outcome.severed == ()


def test_an_adjust_that_is_severed_does_not_leave_its_arithmetic_behind(conn, genesis):
    """`adjust` reads the live value, so a severed adjust in the middle of a batch must
    not shift what the ones after it computed from."""
    outcome = apply_batch(conn, _batch(
        diff("nation", "karthos", "economy", 1.0, op=DiffOp.ADJUST, cite=genesis),
        _bad("treasury", 120.0),
        diff("nation", "karthos", "economy", 1.0, op=DiffOp.ADJUST, cite=genesis),
    ))
    assert outcome.applied, outcome.reason
    assert conn.execute(
        "SELECT economy FROM nation WHERE id = 'karthos'").fetchone()[0] == 22.0


def test_two_different_rules_may_condemn_two_different_diffs(conn, genesis):
    """Severing is not one rule's privilege any more.

    A fabricated citation and an over-cap aggregate are caught by different rules in
    different files, and the batch survives both. `SEVER_MINORITY` is what stops this
    generalising into "any batch, however wrong, keeps whatever passed".
    """
    outcome = apply_batch(conn, _batch(
        _good("stability", 55.0, genesis),
        _good("legitimacy", 52.0, genesis),
        _good("treasury", 120.0, genesis),
        _good("manpower", 110.0, genesis),
        _bad("military_strength", 34.0),
        # 20 -> 26 is 30%, over the 15% per-turn economy cap. ECON-001 names it.
        _good("economy", 26.0, genesis),
    ))
    assert outcome.applied, outcome.reason
    caught = {rule for _seq, rule, _reason in outcome.refusals}
    assert caught == {"CORE-001", "ECON-001"}, outcome.refusals
    row = conn.execute(
        "SELECT economy, stability, manpower FROM nation WHERE id = 'karthos'").fetchone()
    assert row["economy"] == 20.0, "the over-cap economy write landed"
    assert row["stability"] == 55.0 and row["manpower"] == 110.0


def test_the_turn_report_shows_what_a_partial_batch_lost(conn, genesis):
    """**Without this a partial apply is invisible.** The batch reports as applied, so a
    report that only counts rejected *batches* shows a clean turn and says nothing about
    the writes the rules threw out of it."""
    from atlas.engine.turn_engine import JumpReport, _note_refusals
    from atlas.state.models import TurnStatus

    outcome = apply_batch(conn, _batch(
        _good("economy", 21.0, genesis),
        _good("stability", 55.0, genesis),
        _bad("treasury", 120.0),
    ))
    assert outcome.applied

    report = JumpReport(turn=1, status=TurnStatus.SEALED, days_advanced=30,
                        new_date="1936-02-01")
    _note_refusals(report, outcome, "Karthos")
    assert len(report.refused_diffs) == 1
    assert "CORE-001" in report.refused_diffs[0]
    assert "Karthos" in report.refused_diffs[0]
    # ...and it is kept apart from whole proposals refused, which is a different fact.
    assert report.rejections == []
    assert any("refused" in line for line in report.significant())


# ------------------------------------------- severing a row into or out of existence
#
# **The first handoff's own question about this file:** *does `SEVER_MINORITY` interact
# correctly with insert/delete diffs? I only tested it on SETs.* Everything above is a SET,
# and it stayed that way because creating and destroying rows is engine-only (CORE-003), so
# reaching the case needs `Role.ENGINE`.
#
# It is worth reaching. A severed SET leaves a column at the value it already had, which is
# a small wrong answer. A severed INSERT that landed anyway leaves a **row nothing accounts
# for**, and a severed DELETE that went through anyway destroys one -- and in both cases the
# audit trail says it did not happen, so a rewind would not undo it.


def _engine_batch(*diffs, turn: int = 0) -> DiffBatch:
    return DiffBatch(
        world_id="sandbox", turn=turn, proposer_role=Role.ENGINE,
        proposer_model=None, diffs=tuple(diffs),
    )


def _insert(target: str, payload: dict, cite: int):
    return diff("city", target, "__insert__", payload, op=DiffOp.INSERT, cite=cite)


def _delete(table: str, target: str, cite: int):
    return diff(table, target, "__delete__", None, op=DiffOp.DELETE, cite=cite)


def _city(region_id: str = "r_north", name: str = "Somewhere") -> dict:
    return {"world_id": "sandbox", "region_id": region_id, "name": name, "tier": 1}


def test_a_severed_insert_leaves_no_row_behind(conn, genesis):
    """The row must be as absent as if it had never been proposed."""
    outcome = apply_batch(conn, _engine_batch(
        _good("stability", 55.0, genesis),
        _insert("c_kept", _city(name="Kept"), genesis),
        _insert("c_lost", _city(name="Lost"), NO_SUCH_EVENT),
    ))

    assert outcome.applied, outcome.reason
    assert outcome.severed == (2,), outcome.severed
    present = {row[0] for row in conn.execute("SELECT id FROM city")}
    assert "c_kept" in present, "the good insert did not stand"
    assert "c_lost" not in present, "a refused insert created a row anyway"
    assert conn.execute(
        "SELECT stability FROM nation WHERE id = 'karthos'").fetchone()[0] == 55.0


def test_a_severed_delete_leaves_the_row_alone(conn, genesis):
    """And the other direction, which is the one that destroys information."""
    apply_batch(conn, _engine_batch(
        _insert("c_one", _city(name="One"), genesis),
        _insert("c_two", _city(region_id="r_coast", name="Two"), genesis),
    ))
    assert {r[0] for r in conn.execute("SELECT id FROM city")} == {"c_one", "c_two"}

    outcome = apply_batch(conn, _engine_batch(
        _good("stability", 44.0, genesis),
        _delete("city", "c_one", genesis),
        _delete("city", "c_two", NO_SUCH_EVENT),
    ))

    assert outcome.applied, outcome.reason
    assert outcome.severed == (2,), outcome.severed
    assert {r[0] for r in conn.execute("SELECT id FROM city")} == {"c_two"}, (
        "the wrong city was stood down"
    )


def test_a_batch_that_severs_a_row_change_still_rewinds_exactly(conn, genesis):
    """The property everything else rests on, over the ops that were never tested for it.

    A partially applied batch is the one shape that could quietly diverge, because per-diff
    status is what replay reads -- and a row that appeared or vanished is the change a
    wrong status costs most.
    """
    from atlas.state.replay import rewind_to

    conn.execute("UPDATE world SET turn_number = 1, world_date = '1897-04-01'"
                 " WHERE id = 'sandbox'")
    conn.execute("INSERT INTO turn (world_id, number, phase, status, start_date, end_date)"
                 " VALUES ('sandbox', 1, 'sealed', 'sealed', '1897-04-01', '1897-04-01')")
    apply_batch(conn, _engine_batch(
        _insert("c_before", _city(name="Before"), genesis), turn=1))
    conn.commit()
    before = fingerprint(conn, "sandbox")

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = 'sandbox'")
    conn.execute("INSERT INTO turn (world_id, number, phase, status, start_date)"
                 " VALUES ('sandbox', 2, 'sealed', 'sealed', '1897-04-01')")
    conn.commit()
    outcome = apply_batch(conn, _engine_batch(
        _good("stability", 33.0, genesis),
        _insert("c_new", _city(region_id="r_coast", name="New"), genesis),
        _delete("city", "c_before", genesis),
        _insert("c_refused", _city(name="Refused"), NO_SUCH_EVENT),
        turn=2,
    ))
    assert outcome.applied and outcome.severed == (3,), outcome.severed

    assert rewind_to(conn, "sandbox", 1).ok
    assert {r[0] for r in conn.execute("SELECT id FROM city")} == {"c_before"}
    assert fingerprint(conn, "sandbox") == before


def test_a_batch_mostly_of_bad_row_changes_is_refused_whole(conn, genesis):
    """`SEVER_MINORITY` is about how much of a batch was wrong, not about which op it
    used -- so a batch that is mostly refused inserts loses the good one with them."""
    outcome = apply_batch(conn, _engine_batch(
        _insert("c_ok", _city(name="Ok"), genesis),
        _insert("c_bad1", _city(name="Bad1"), NO_SUCH_EVENT),
        _insert("c_bad2", _city(region_id="r_coast", name="Bad2"), NO_SUCH_EVENT),
    ))

    assert not outcome.applied
    assert conn.execute("SELECT COUNT(*) FROM city").fetchone()[0] == 0
