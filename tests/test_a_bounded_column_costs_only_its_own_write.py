"""Stability and legitimacy are bounded, and overshooting one used to cost a whole turn.

``ECON-003`` holds both inside 0-100 by refusing the *batch*. Two failures follow from that
and this file pins both shut:

* an engine writer that charges more than a nation has loses everything else it was
  carrying -- PT39 found a finished public work in Spain whose development, industry and
  construction-row deletion were all thrown away, every turn, because it also carried a
  1.0 stability charge against a government sitting at 0.0;
* the rule used to scan the **whole nation table**, so one country out of range condemned
  every later batch that touched any nation anywhere, whether or not it had anything to do
  with the breach.

The first is fixed at the writers (:mod:`atlas.engine.bounds`), the second at the rule.
Both halves are tested, because either alone still loses somebody's turn.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.bounds import BOUNDS, absorbable
from atlas.state.apply import apply_batch
from atlas.state.models import DiffOp, Role
from atlas.state.rules.economy import _BOUNDED_COLUMNS

from .conftest import diff


def _set(conn: sqlite3.Connection, nation: str, **columns: float) -> None:
    for column, value in columns.items():
        conn.execute(
            f"UPDATE nation SET {column} = ? WHERE world_id = 'sandbox' AND id = ?",  # noqa: S608
            (value, nation),
        )
    conn.commit()


def _read(conn: sqlite3.Connection, nation: str, column: str) -> float:
    return float(conn.execute(
        f"SELECT {column} FROM nation WHERE world_id = 'sandbox' AND id = ?",  # noqa: S608
        (nation,),
    ).fetchone()[0])


# ------------------------------------------------------------------ the clamp itself


def test_a_charge_is_bounded_by_what_the_nation_has(conn):
    _set(conn, "aurelia", stability=0.4)
    assert absorbable(conn, "nation", "aurelia", "stability", -1.0) == pytest.approx(-0.4)


def test_a_government_at_zero_cannot_be_shaken_further(conn):
    """The PT39 case, at the level of the helper: nothing left to take."""
    _set(conn, "aurelia", stability=0.0)
    assert absorbable(conn, "nation", "aurelia", "stability", -1.0) == 0.0


def test_the_ceiling_is_clamped_too(conn):
    """**The half none of the four inline guards had.** A steelworks worth +9.0 stability
    to a country sitting at 97 is the same defect facing the other way."""
    _set(conn, "aurelia", stability=97.0)
    assert absorbable(conn, "nation", "aurelia", "stability", 9.0) == pytest.approx(3.0)


def test_a_charge_that_fits_is_left_alone(conn):
    assert absorbable(conn, "nation", "aurelia", "stability", -8.0) == -8.0
    assert absorbable(conn, "nation", "aurelia", "legitimacy", 12.0) == 12.0


def test_an_unbounded_column_passes_straight_through(conn):
    """A caller must be able to route every effect through this without asking first --
    which is the point, since the writers that got it wrong were the ones that had to
    remember to ask."""
    assert absorbable(conn, "nation", "aurelia", "treasury", -999.0) == -999.0
    assert absorbable(conn, "region", "r_north", "development", -999.0) == -999.0


def test_the_clamp_and_the_rule_agree_on_which_columns_are_bounded():
    """A column the rule checks and the clamp does not is a writer waiting to lose a
    batch; a column the clamp bounds and the rule does not is a silent lie."""
    assert {field for _table, field in BOUNDS} == set(_BOUNDED_COLUMNS)
    assert all(span == (0.0, 100.0) for span in BOUNDS.values())


# --------------------------------------------------- the rule, and who it may condemn


def test_a_pre_existing_breach_does_not_refuse_an_unrelated_batch(conn, batch_factory):
    """**The collateral refusal.** Karthos is out of range; a credit to Veyra has nothing
    to do with it, cannot fix it, and used to be refused for it."""
    _set(conn, "karthos", stability=-1.0)

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "veyra", "treasury", 5.0, op=DiffOp.ADJUST), role=Role.ENGINE))

    assert outcome.applied, outcome.validation.reason()
    assert _read(conn, "veyra", "treasury") > 0


def test_a_batch_that_drives_a_value_out_of_range_is_still_refused(conn, batch_factory):
    """The invariant the rule exists for, unchanged."""
    _set(conn, "aurelia", stability=0.5)

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "aurelia", "stability", -3.0, op=DiffOp.ADJUST), role=Role.ENGINE))

    assert not outcome.applied
    assert "ECON-003" in outcome.validation.reason()
    assert _read(conn, "aurelia", "stability") == 0.5, "the refused charge must not land"


def test_the_refusal_names_the_write_that_caused_it(conn, batch_factory):
    """Naming no diff meant the whole batch went every time. Naming one lets the apply
    layer drop that charge and keep the rest -- which is what PT39's public work needed."""
    _set(conn, "aurelia", stability=0.5)

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "aurelia", "stability", -3.0, op=DiffOp.ADJUST), role=Role.ENGINE))

    condemned = [v for v in outcome.validation.violations if v.rule_id == "ECON-003"]
    assert condemned and all(v.severable for v in condemned), (
        "ECON-003 must name the diff it condemns")


def test_a_breach_elsewhere_does_not_hide_a_breach_here(conn, batch_factory):
    """Narrowing the scan must not narrow the invariant: the batch's own overshoot is
    still caught when some *other* nation happens to be out of range as well."""
    _set(conn, "karthos", stability=-1.0)
    _set(conn, "aurelia", stability=0.5)

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "aurelia", "stability", -3.0, op=DiffOp.ADJUST), role=Role.ENGINE))

    assert not outcome.applied
    assert "Aurelia" in outcome.validation.reason()
    assert "Karthos" not in outcome.validation.reason(), (
        "the batch is answerable for its own writes, not for the state of the world")


def test_the_ceiling_is_enforced_as_well_as_the_floor(conn, batch_factory):
    _set(conn, "aurelia", legitimacy=99.0)

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "aurelia", "legitimacy", 5.0, op=DiffOp.ADJUST), role=Role.ENGINE))

    assert not outcome.applied
    assert "ECON-003" in outcome.validation.reason()


# ------------------------------------------------------- the PT39 case, end to end


def test_a_public_work_completes_for_a_government_at_zero_stability(conn):
    """**The regression PT39 found.** ``schools`` is worth +2.5 economy and +3.0
    legitimacy and costs 1.0 stability -- an argumentative country. Spain sat at 0.0 for
    the last four turns of PT39, so the charge overshot, ``ECON-003`` condemned the batch
    that carried it, and the schools were rebuilt and re-refused every turn: no economy,
    no legitimacy, and the construction row never deleted.

    The work must now complete, keeping everything it earned and paying what it can.
    """
    from atlas.engine.production import advance_construction

    _set(conn, "aurelia", stability=0.0, legitimacy=40.0)
    before = _read(conn, "aurelia", "economy")
    conn.execute(
        """
        INSERT INTO construction
            (id, world_id, nation_id, region_id, project_kind, kind, turns_remaining,
             days_remaining, started_turn)
        VALUES ('c1', 'sandbox', 'aurelia', 'r_north', 'civil', 'schools', 0, 0.0, 0)
        """
    )
    conn.commit()

    report = advance_construction(conn, "sandbox", turn=1, date="1936-02-01", seed=7)

    assert not report.problems, report.problems
    assert any("Schools" in line for line in report.delivered), report.delivered
    assert _read(conn, "aurelia", "economy") > before, "the work must keep what it earned"
    assert _read(conn, "aurelia", "legitimacy") == 43.0
    assert _read(conn, "aurelia", "stability") == 0.0, "paid what there was, and no less"
    assert conn.execute(
        "SELECT COUNT(*) FROM construction WHERE id = 'c1'").fetchone()[0] == 0, (
        "the finished project must be cleared, or it is rebuilt every turn forever")


def test_the_charge_is_clamped_at_the_writer_not_severed_at_the_rule(conn):
    """**Belt and braces, and they are not the same brace.**

    Narrowing the rule alone would already rescue the public work above -- the overshoot
    now names its diff, so the apply layer severs that one charge and lets the rest stand.
    That is a good outcome reached the wrong way: the batch is still refused once,
    re-validated, and recorded with a rejected diff hanging off it, and the writer is
    still authoring a charge it knows the world cannot pay.

    So this asserts the clamp is actually wired in: every diff the completion wrote was
    applied, and none of them was condemned by anybody.
    """
    from atlas.engine.production import advance_construction

    _set(conn, "aurelia", stability=0.0, legitimacy=40.0)
    conn.execute(
        """
        INSERT INTO construction
            (id, world_id, nation_id, region_id, project_kind, kind, turns_remaining,
             days_remaining, started_turn)
        VALUES ('c2', 'sandbox', 'aurelia', 'r_north', 'civil', 'schools', 0, 0.0, 0)
        """
    )
    conn.commit()

    advance_construction(conn, "sandbox", turn=1, date="1936-02-01", seed=7)

    rejected = conn.execute(
        """
        SELECT d.field, d.rejected_by_rule FROM diff d
          JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = 'sandbox' AND d.status <> 'applied'
        """
    ).fetchall()
    assert not rejected, f"the completion should cost nobody a diff: {[dict(r) for r in rejected]}"


# ------------------------------------------- the property, swept across every writer


def test_every_bounded_column_write_in_the_engine_is_clamped_by_its_writer():
    """**The durable form of a sweep that has been done by hand three times.**

    Three separate charges reached this file the same way -- a public work in Spain, a
    doctrine shift, an unanswered call -- and each was found by somebody reproducing it
    rather than by anything refusing it. The class is "an engine writer authors an ADJUST
    on a bounded column without asking what the row can take", and a grep for it produces
    false positives in both directions: the clamp is often applied where the value is
    computed rather than at the `Diff(...)` call, so reading call sites alone reported
    fifteen unclamped writers of which every single one was fine.

    So this asks the question structurally instead. Every module that writes an ADJUST to a
    column in `BOUNDS` must also name one of the two clamps -- `absorbable` for a row keyed
    on an id, `_regard_room` for the one column keyed on a pair -- **or** be listed below
    with the reason its value cannot leave the range by construction.

    A new writer that does neither fails here, which is the point: the next one should be
    caught by a test rather than by a play-test.
    """
    import re
    from pathlib import Path

    #: Writers whose value is bounded by how it is computed, checked by hand and recorded
    #: here so the exemption is a decision rather than an oversight.
    BY_CONSTRUCTION = {
        "budget.py": "healing converges on `_STABILITY_RESTING` from below and `leg_drift` "
                     "converges on stability, so neither can leave 0-100; the arrears "
                     "charge is clamped where it is computed",
        "leaders.py": "every fall is `min(cost, what the government has above the floor)` "
                      "immediately above the diff",
    }

    engine = Path(__file__).resolve().parent.parent / "src" / "atlas" / "engine"
    bounded = {field for _table, field in BOUNDS}
    offenders = []
    for path in sorted(engine.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        writes = {
            m.group(1) for m in re.finditer(
                r'Diff\(\s*"\w+"[^)]*?"(\w+)"\s*,\s*DiffOp\.ADJUST', text, re.S)
        } & bounded
        if not writes:
            continue
        if "absorbable(" in text or "_regard_room(" in text:
            continue
        if path.name in BY_CONSTRUCTION:
            continue
        offenders.append(f"{path.name} writes {sorted(writes)}")

    assert not offenders, (
        "engine module(s) adjusting a bounded column with no clamp and no recorded "
        f"reason: {offenders}. Route the value through `bounds.absorbable`, or add it to "
        "BY_CONSTRUCTION above with the argument for why it cannot leave its range. An "
        "unclamped charge is a severable one: ECON-003 refuses the batch, the apply layer "
        "severs that diff, and the writer reports a success nobody paid for."
    )
