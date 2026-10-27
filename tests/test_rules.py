"""Each validation rule, exercised independently.

Every rule gets a case that trips it and a case that must not. One-sided tests are the
usual way a rule quietly becomes "reject everything" without anyone noticing.
"""

from __future__ import annotations

import pytest

from atlas.state.apply import apply_batch, record_event
from atlas.state.models import (
    EventKind,
    EventOrigin,
    Role,
    TreatyStatus,
    Visibility,
)
from atlas.state.validate import all_rules

from .conftest import diff


def _rules_hit(outcome) -> set[str]:
    return {v.rule_id for v in outcome.validation.violations}


def _event(conn, kind, origin=EventOrigin.ENGINE, actor=None, turn=0,
           visibility=Visibility.PUBLIC, visible_to=(), target=None):
    return record_event(
        conn,
        world_id="sandbox",
        turn=turn,
        date="1897-03-01",
        kind=kind,
        origin=origin,
        actor_nation_id=actor,
        visibility=visibility,
        visible_to=visible_to,
        participants=(("target", target),) if target else (),
    )


# --------------------------------------------------------------------------- CORE


def test_core001_rejects_an_uncited_change(conn, batch_factory):
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 21.0, cite=-1))
    )
    assert "CORE-001" in _rules_hit(outcome)


def test_core001_rejects_a_fabricated_event_id(conn, batch_factory):
    """A fabricated citation must be *recorded* as rejected, not raise IntegrityError.

    This is why ``reason_event_id`` deliberately has no foreign key.
    """
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 21.0, cite=999_999))
    )
    assert "CORE-001" in _rules_hit(outcome)
    stored = conn.execute("SELECT status FROM diff_batch").fetchone()
    assert stored["status"] == "rejected"


def test_core001_rejects_a_cause_from_the_future(conn, batch_factory):
    future = _event(conn, EventKind.NARRATIVE, turn=9)
    conn.commit()
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "karthos", "economy", 21.0, cite=future))
    )
    assert "CORE-001" in _rules_hit(outcome)


def test_core002_blocks_a_model_authored_casus_belli(conn, batch_factory):
    """The two-turn bypass: mint your own casus belli, cite it next turn."""
    forged = _event(conn, EventKind.CASUS_BELLI, origin=EventOrigin.MODEL, actor="karthos")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", -30.0, cite=forged),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "CORE-002" in _rules_hit(outcome)


def test_core002_allows_an_engine_authored_casus_belli(conn, batch_factory):
    real = _event(conn, EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE, actor="karthos")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", -10.0, cite=real),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "CORE-002" not in _rules_hit(outcome)


@pytest.mark.parametrize(
    ("table", "target", "field", "value"),
    [
        ("region", "r_north", "owner_nation_id", "karthos"),
        ("region", "r_north", "geo_key", "geo:forged"),
        ("unit", "u1", "strength", 99.0),
        ("relation", "karthos|aurelia", "at_war", 1),
        ("nation", "karthos", "is_tracked", 1),
    ],
)
def test_core003_blocks_engine_only_columns(conn, batch_factory, table, target, field, value):
    """Conquest by small increments is still the model deciding the outcome.

    The unit case addressed `u1`, which the sandbox does not have. The diff layer used to
    let an update to a missing row through to the rules, so this passed while proving less
    than it claimed -- CORE-003 was being asked about a row that was not there. The unit is
    created first now, so the rule is tested against something real.
    """
    if table == "unit":
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, 'sandbox', 'karthos', 'r_south', 'infantry', 5.0, 50)", (target,))
        conn.commit()

    outcome = apply_batch(
        conn, batch_factory(diff(table, target, field, value), role=Role.WORLD_SIM)
    )
    assert "CORE-003" in _rules_hit(outcome)


def test_core003_blocks_a_role_writing_outside_its_remit(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("nation", "karthos", "economy", 21.0), role=Role.DIPLOMACY, nation="karthos"
        ),
    )
    assert "CORE-003" in _rules_hit(outcome)


def test_core003_allows_diplomacy_to_move_relations(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", 10.0),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "CORE-003" not in _rules_hit(outcome)


def test_core005_catches_a_capital_the_nation_no_longer_owns(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(diff("region", "r_north", "owner_nation_id", "karthos"), role=Role.ENGINE),
    )
    assert "CORE-005" in _rules_hit(outcome)


# --------------------------------------------------------------------------- INFO


def test_info001_blocks_citing_an_event_the_nation_cannot_see(conn, batch_factory):
    """Information asymmetry, checked structurally rather than requested politely."""
    secret = _event(
        conn,
        EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE,
        actor="aurelia",
        visibility=Visibility.SECRET,
        visible_to=("aurelia",),
    )
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", -5.0, cite=secret),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "INFO-001" in _rules_hit(outcome)


def test_info001_allows_the_nation_that_can_see_it(conn, batch_factory):
    secret = _event(
        conn,
        EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE,
        actor="aurelia",
        visibility=Visibility.SECRET,
        visible_to=("aurelia",),
    )
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "aurelia|karthos", "score", -5.0, cite=secret,
                 scope=Visibility.SECRET),
            role=Role.DIPLOMACY,
            nation="aurelia",
        ),
    )
    assert "INFO-001" not in _rules_hit(outcome)


def test_info002_blocks_a_covert_cause_producing_overt_state(conn, batch_factory):
    """The leak that lives in the numbers rather than the prose."""
    secret = _event(
        conn,
        EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE,
        visibility=Visibility.SECRET,
        visible_to=("aurelia", "karthos"),
    )
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "aurelia|karthos", "score", -5.0, cite=secret,
                 scope=Visibility.PUBLIC),
            role=Role.DIPLOMACY,
            nation="aurelia",
        ),
    )
    assert "INFO-002" in _rules_hit(outcome)


# --------------------------------------------------------------------------- ECON


def test_econ001_caps_runaway_growth(conn, batch_factory):
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 500.0)))
    assert "ECON-001" in _rules_hit(outcome)


def test_econ001_permits_a_modest_change(conn, batch_factory):
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 22.0)))
    assert outcome.applied, outcome.reason


def test_econ001_reform_raises_the_cap(conn, batch_factory):
    _event(conn, EventKind.ECONOMIC_REFORM, origin=EventOrigin.ENGINE, actor="karthos")
    conn.commit()
    # 20 -> 28 is 40%: past the 15% baseline, inside the 45% a reform buys.
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 28.0)))
    assert "ECON-001" not in _rules_hit(outcome)


def test_econ002_rejects_a_model_authored_reform(conn, batch_factory):
    _event(conn, EventKind.ECONOMIC_REFORM, origin=EventOrigin.MODEL, actor="karthos")
    conn.commit()
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 21.0)))
    assert "ECON-002" in _rules_hit(outcome)


def test_econ003_catches_out_of_range_stability(conn, batch_factory):
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "stability", 140.0)))
    assert "ECON-003" in _rules_hit(outcome)


# --------------------------------------------------------------------------- DIP / WAR


def test_dip001_blocks_an_alliance_between_nations_that_dislike_each_other(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "treaty_status", TreatyStatus.ALLIANCE.value),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "DIP-001" in _rules_hit(outcome)


def test_dip001_allows_an_alliance_once_regard_is_high_enough(conn, batch_factory):
    conn.execute("UPDATE relation SET score = 70 WHERE world_id = 'sandbox'")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "treaty_status", TreatyStatus.ALLIANCE.value),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "DIP-001" not in _rules_hit(outcome)


def test_dip002_caps_a_wild_relation_swing(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", 90.0),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "DIP-002" in _rules_hit(outcome)


def test_dip002_allows_a_wild_swing_after_a_dramatic_event(conn, batch_factory):
    betrayal = _event(conn, EventKind.TREATY_BROKEN, origin=EventOrigin.ENGINE, actor="aurelia")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "score", -80.0, cite=betrayal),
            role=Role.DIPLOMACY,
            nation="karthos",
        ),
    )
    assert "DIP-002" not in _rules_hit(outcome)


def test_war001_unprovoked_war_must_cost_something(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(diff("relation", "karthos|aurelia", "at_war", 1), role=Role.ENGINE),
    )
    assert "WAR-001" in _rules_hit(outcome)


def test_war001_permits_war_when_legitimacy_is_paid(conn, batch_factory):
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "at_war", 1),
            diff("nation", "karthos", "legitimacy", 40.0),
            role=Role.ENGINE,
        ),
    )
    assert "WAR-001" not in _rules_hit(outcome)


def test_war001_permits_war_with_a_casus_belli(conn, batch_factory):
    """The grievance names the country it is against, as all three writers of these
    events do -- see `test_war001_refuses_a_casus_belli_against_somebody_else`."""
    cb = _event(conn, EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE, actor="karthos",
                target="aurelia")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "at_war", 1, cite=cb), role=Role.ENGINE
        ),
    )
    assert "WAR-001" not in _rules_hit(outcome)


def test_war001_refuses_a_casus_belli_against_somebody_else(conn, batch_factory):
    """A grievance against Veyra is not a licence to attack Aurelia. The check asked only
    whether the aggressor held *a* casus belli in the last five turns, so one earned
    against any neighbour made war on every other neighbour free."""
    cb = _event(conn, EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE, actor="karthos",
                target="veyra")
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("relation", "karthos|aurelia", "at_war", 1, cite=cb), role=Role.ENGINE
        ),
    )
    assert "WAR-001" in _rules_hit(outcome)


def test_war002_peace_needs_a_peace_event(conn, batch_factory):
    conn.execute(
        "UPDATE relation SET at_war = 1 WHERE nation_a = 'karthos' AND nation_b = 'aurelia'"
    )
    conn.commit()
    outcome = apply_batch(
        conn,
        batch_factory(diff("relation", "karthos|aurelia", "at_war", 0), role=Role.ENGINE),
    )
    assert "WAR-002" in _rules_hit(outcome)


def test_focus001_cap_is_enforced_by_the_database(conn):
    """The trigger fires before any Python guard gets a chance to be forgotten."""
    import sqlite3

    for i in range(5):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour, is_tracked) VALUES (?,?,?,?,1)",
            (f"t{i}", "sandbox", f"Tracked {i}", "#888888"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="focus cap"):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour, is_tracked) "
            "VALUES ('t5','sandbox','Tracked 5','#888888',1)"
        )


# --------------------------------------------------------------------------- OWN / UNIT


def test_own001_blocks_territory_appearing_out_of_reach(conn, batch_factory):
    """Territory cannot teleport to a power with no front and no treaty.

    Aurelia holds r_north and r_coast; r_west touches neither.
    """
    outcome = apply_batch(
        conn,
        batch_factory(diff("region", "r_west", "owner_nation_id", "aurelia"), role=Role.ENGINE),
    )
    assert "OWN-001" in _rules_hit(outcome)


def test_own001_allows_conquest_of_an_adjacent_region(conn, batch_factory):
    """Veyra holds r_east, which borders r_north."""
    outcome = apply_batch(
        conn,
        batch_factory(diff("region", "r_north", "owner_nation_id", "veyra"), role=Role.ENGINE),
    )
    assert "OWN-001" not in _rules_hit(outcome)


def _add_unit(conn, unit_id, nation, region, kind="infantry", domain="land"):
    conn.execute(
        "INSERT OR IGNORE INTO unit_template (world_id, kind, domain) VALUES ('sandbox', ?, ?)",
        (kind, domain),
    )
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind) VALUES (?,?,?,?,?)",
        (unit_id, "sandbox", nation, region, kind),
    )
    conn.commit()


def test_unit001_catches_forces_raised_without_the_means_to_pay(conn, batch_factory):
    _add_unit(conn, "u1", "karthos", "r_south")
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("unit", "u1", "strength", 5.0),
            diff("nation", "karthos", "treasury", -50.0),
            role=Role.ENGINE,
        ),
    )
    assert "UNIT-001" in _rules_hit(outcome)


def test_unit001_allows_a_funded_change(conn, batch_factory):
    _add_unit(conn, "u1", "karthos", "r_south")
    outcome = apply_batch(
        conn,
        batch_factory(
            diff("unit", "u1", "strength", 5.0),
            diff("nation", "karthos", "treasury", 50.0),
            role=Role.ENGINE,
        ),
    )
    assert "UNIT-001" not in _rules_hit(outcome)


def test_unit002_catches_a_navy_inland(conn, batch_factory):
    _add_unit(conn, "u_sea", "aurelia", "r_north", kind="frigate", domain="sea")
    outcome = apply_batch(
        conn, batch_factory(diff("unit", "u_sea", "strength", 2.0), role=Role.ENGINE)
    )
    assert "UNIT-002" in _rules_hit(outcome)


def test_unit002_allows_a_navy_at_sea(conn, batch_factory):
    _add_unit(conn, "u_sea", "aurelia", "r_sea", kind="frigate", domain="sea")
    outcome = apply_batch(
        conn, batch_factory(diff("unit", "u_sea", "strength", 2.0), role=Role.ENGINE)
    )
    assert "UNIT-002" not in _rules_hit(outcome)


def test_unit003_catches_units_of_a_fallen_power(conn, batch_factory):
    _add_unit(conn, "u1", "veyra", "r_east")
    outcome = apply_batch(
        conn, batch_factory(diff("nation", "veyra", "is_alive", 0), role=Role.ENGINE)
    )
    assert "UNIT-003" in _rules_hit(outcome)


def test_focus001_rule_flags_an_over_cap_world(conn, batch_factory):
    """The rule is an independent check, not a restatement of the trigger.

    The triggers turn out to catch a bulk ``UPDATE`` as well as row-by-row promotion, so
    they have to be dropped to reach an over-cap state at all. That is worth knowing: the
    rule exists for a future code path that writes tracking some other way, and this test
    proves it would still catch it.
    """
    conn.execute("DROP TRIGGER trg_focus_cap_insert")
    conn.execute("DROP TRIGGER trg_focus_cap_update")
    for i in range(6):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour, is_tracked) VALUES (?,?,?,'#888888',1)",
            (f"t{i}", "sandbox", f"Tracked {i}"),
        )
    conn.commit()
    outcome = apply_batch(conn, batch_factory(diff("nation", "karthos", "economy", 21.0)))
    assert "FOCUS-001" in _rules_hit(outcome)


# --------------------------------------------------------------------------- meta


def test_every_registered_rule_has_a_test():
    """Coverage meta-test.

    A rule that exists but is never exercised is a rule nobody knows is broken. Adding a
    rule to the registry without a case somewhere in the suite fails here, by design.
    """
    from pathlib import Path

    suite = " ".join(
        path.read_text(encoding="utf-8") for path in Path(__file__).parent.glob("test_*.py")
    )
    missing = {rule.id for rule in all_rules() if rule.id not in suite}
    assert not missing, f"rules with no test: {sorted(missing)}"


# ------------------------------------------- the caps scale with the jump (PT20 finding 3)


def _econ_batch(nation: str, field: str, before: float, after: float, cite: int):
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role, Visibility

    return DiffBatch(
        world_id="sandbox", turn=1, proposer_role=Role.WORLD_SIM, proposer_model="mock",
        diffs=(
            Diff(
                target_table="nation", target_id=nation, field=field, op=DiffOp.SET,
                new_value=after, old_value=before, reason_event_id=cite,
                visibility_scope=Visibility.PUBLIC,
            ),
        ),
    )


def _tripped(conn, batch, days: int) -> bool:
    from atlas.state.validate import validate

    result = validate(conn, batch, days=days)
    return any(v.rule_id == "ECON-001" for v in result.violations)


def test_a_year_long_turn_permits_a_years_worth_of_economic_movement(conn, genesis):
    """`state/rules/economy.py` promised this from the initial commit and did not do it.

    No rule read the turn length at all, so the same 20% bound applied to a week and to a
    year: the player who jumped in years was held to a twelfth of the growth the one who
    jumped in months could have. PT20 finding 3.
    """
    # 60% growth in military strength: far past a month's 20%, comfortably inside a year's.
    batch = _econ_batch("karthos", "military_strength", 50.0, 80.0, genesis)
    assert _tripped(conn, batch, days=30), "a month should still refuse this"
    assert not _tripped(conn, batch, days=365), "a year should permit it"


def test_a_week_long_turn_permits_less_than_a_month(conn, genesis):
    """The scaling has to bite downwards too, or a short jump is a free pass."""
    # 10%: inside a month's 20%, outside a week's ~4.3%.
    batch = _econ_batch("karthos", "military_strength", 50.0, 55.0, genesis)
    assert not _tripped(conn, batch, days=30)
    assert _tripped(conn, batch, days=7)


def test_the_monthly_turn_is_exactly_what_it_always_was(conn, genesis):
    """Everything was calibrated at thirty days, so the default must not move."""
    from atlas.state.rules.economy import SOFT_CAPS

    cap = SOFT_CAPS["military_strength"]
    just_under = _econ_batch("karthos", "military_strength", 50.0, 50.0 + 50.0 * cap * 0.99,
                             genesis)
    just_over = _econ_batch("karthos", "military_strength", 50.0, 50.0 + 50.0 * cap * 1.01,
                            genesis)
    assert not _tripped(conn, just_under, days=30)
    assert _tripped(conn, just_over, days=30)


def test_the_cap_scales_linearly_and_stays_a_bound_at_a_year(conn, genesis):
    """The deliberate choice against the more obvious one.

    Compounding is what the *quantity* does -- twelve months of 50% treasury growth is
    139x -- and applying that to the cap made the year-long bound 13,801%, which is not a
    bound. This rule exists to stop one proposal narrating a superpower into being, so a
    guardrail that switches itself off exactly when a decision covers the most time is
    worse than one that is slightly tight.
    """
    from atlas.state.rules.economy import SOFT_CAPS

    # A year permits more than a month, and still refuses the absurd.
    modest = _econ_batch("karthos", "treasury", 100.0, 400.0, genesis)   # 3x
    absurd = _econ_batch("karthos", "treasury", 100.0, 9000.0, genesis)  # 90x
    assert _tripped(conn, modest, days=30)
    assert not _tripped(conn, modest, days=365)
    assert _tripped(conn, absurd, days=365), "a year must still be a bound, not a formality"

    # Linear: a year is twelve times a month, not the compounded 139 times.
    span = 365 / 30.0
    assert SOFT_CAPS["treasury"] * span < 10.0


def test_the_briefing_quotes_the_cap_that_will_actually_be_applied(conn, genesis):
    """The disclosure side of the same bug.

    Decision 50 started quoting the caps to models. Quoting a *monthly* figure to a model
    deciding a year would tell it a bound twelve times stricter than the one about to be
    enforced -- so the briefing is a function of the turn length too, and the two have to
    agree.
    """
    from atlas.engine.pacing import over
    from atlas.engine.views import economy_write_rules
    from atlas.state.rules.economy import SOFT_CAPS

    for days in (7, 30, 90, 365):
        blob = " ".join(economy_write_rules(days))
        for field, cap in SOFT_CAPS.items():
            stated = f"{over(cap, days / 30.0):.0%}"
            assert f"`{field}` {stated}" in blob, (days, field, stated, blob)

    # And a year says so in words, so the number is not read as a monthly one.
    assert "365 days" in " ".join(economy_write_rules(365))
    assert "365 days" not in " ".join(economy_write_rules(30))


# ------------------------------------------------------- DIP-004: pending means something


def _pending(conn, parties=("karthos", "aurelia"), accepted=()):
    cursor = conn.execute(
        "INSERT INTO commitment (world_id, kind, status, condition_text, created_turn)"
        " VALUES ('sandbox', 'non_aggression_pact', 'pending', '', 0)")
    for nation_id in parties:
        conn.execute(
            "INSERT INTO commitment_nation (commitment_id, nation_id, accepted_turn)"
            " VALUES (?, ?, ?)",
            (int(cursor.lastrowid), nation_id, 0 if nation_id in accepted else None))
    conn.commit()
    return int(cursor.lastrowid)


# **These run as ENGINE now, and that is the point rather than a detail.**
# `commitment.status` was writable by three model roles when DIP-004 was written, so the rule
# was a guard on *models*. The column is engine-only since the status hole was closed, so
# DIP-004 is now a guard on the **engine's own** activation path -- `accept_commitment` writes
# ACTIVE and this is what checks it had the right to. A rule whose only job was to stop
# something that can no longer happen would be dead weight; this one still has a job.


def test_dip004_refuses_activation_while_a_party_has_not_accepted(conn, batch_factory):
    """One signatory could once bring an agreement into force on its own, and every
    consumer of `relation.treaty_status` would believe it. An agreement one party can
    impose is not an agreement."""
    commitment_id = _pending(conn, accepted=("karthos",))
    outcome = apply_batch(conn, batch_factory(
        diff("commitment", str(commitment_id), "status", "active"), role=Role.ENGINE))

    assert "DIP-004" in _rules_hit(outcome)
    assert "aurelia" in outcome.reason


def test_dip004_allows_activation_once_everybody_has(conn, batch_factory):
    commitment_id = _pending(conn, accepted=("karthos", "aurelia"))
    outcome = apply_batch(conn, batch_factory(
        diff("commitment", str(commitment_id), "status", "active"), role=Role.ENGINE))

    assert "DIP-004" not in _rules_hit(outcome)


def test_dip004_leaves_an_agreement_from_before_acceptance_existed_alone(conn, batch_factory):
    """The compatibility rule the rest of this phase used. Every agreement in every world
    written before schema 18 has no acceptance recorded for anybody, and refusing those
    would freeze the diplomacy of every existing save."""
    commitment_id = _pending(conn)
    outcome = apply_batch(conn, batch_factory(
        diff("commitment", str(commitment_id), "status", "active"), role=Role.ENGINE))

    assert "DIP-004" not in _rules_hit(outcome)


def test_accepting_last_is_what_brings_an_agreement_into_force(conn):
    """Activation is a consequence rather than a decision."""
    from atlas.engine.diplomacy import accept_commitment

    commitment_id = _pending(conn)
    event_id = conn.execute(
        "SELECT id FROM event WHERE world_id = 'sandbox' LIMIT 1").fetchone()[0]

    assert not accept_commitment(
        conn, "sandbox", commitment_id=commitment_id, nation_id="karthos", turn=0,
        event_id=event_id)
    assert conn.execute(
        "SELECT status FROM commitment WHERE id = ?", (commitment_id,)).fetchone()[0] == "pending"

    assert accept_commitment(
        conn, "sandbox", commitment_id=commitment_id, nation_id="aurelia", turn=0,
        event_id=event_id)
    assert conn.execute(
        "SELECT status FROM commitment WHERE id = ?", (commitment_id,)).fetchone()[0] == "active"


def test_a_nation_that_is_not_a_party_cannot_accept(conn):
    from atlas.engine.diplomacy import accept_commitment

    commitment_id = _pending(conn)
    event_id = conn.execute(
        "SELECT id FROM event WHERE world_id = 'sandbox' LIMIT 1").fetchone()[0]
    assert not accept_commitment(
        conn, "sandbox", commitment_id=commitment_id, nation_id="veyra", turn=0,
        event_id=event_id)
    assert conn.execute(
        "SELECT COUNT(*) FROM commitment_nation WHERE commitment_id = ?"
        "   AND accepted_turn IS NOT NULL", (commitment_id,)).fetchone()[0] == 0


def test_treaty_status_is_no_longer_writable_by_a_model():
    """It is a materialised view of the commitments, recomputed by `sync_treaty_status`.
    Leaving it writable let a model set the column to say something the agreements did
    not -- and seven consumers read the column rather than the agreements."""
    from atlas.state.permissions import ENGINE_ONLY, ROLE_WRITABLE

    assert ("relation", "treaty_status") in ENGINE_ONLY
    for role, writable in ROLE_WRITABLE.items():
        assert ("relation", "treaty_status") not in writable, (
            f"{role.value} can still write the column that is derived from commitments"
        )


# ---------------------------------------------------------------- when a rule itself breaks
#
# `validate`'s docstring states the most important fail-safe in this layer -- *a rule that
# raises is treated as a violation rather than propagating; a buggy rule should fail closed*
# -- and nothing exercised it. The laboratory has the equivalent test for a detector that
# raises, and it was written because a broken detector taking seven working ones with it
# would make a run come back clean. The stakes here are higher: a rule that failed *open*
# would not report a clean run, it would let an illegal change into the world silently.


def test_a_rule_that_raises_refuses_the_batch_rather_than_propagating(
    conn, batch_factory, monkeypatch
):
    """Fail closed. Refusing a legal change is recoverable and visible; letting an illegal
    one through corrupts the world and nothing says so."""
    from atlas.state import validate as validate_module

    def explode(_ctx):
        raise ZeroDivisionError("a rule with a bug in it")
        yield  # pragma: no cover - never reached, makes this a generator like a real rule

    real = _real_rules()
    monkeypatch.setattr(
        validate_module, "all_rules", lambda: (*real, _broken("TEST-999", explode)))

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "karthos", "stability", 51.0)))

    assert not outcome.applied, "a raising rule let the batch through"
    assert "TEST-999" in outcome.reason and "ZeroDivisionError" in outcome.reason
    assert conn.execute(
        "SELECT stability FROM nation WHERE id = 'karthos'").fetchone()[0] != 51.0


def test_a_raising_rule_does_not_stop_the_others_from_running(
    conn, batch_factory, monkeypatch
):
    """Seven working rules must not go down with one broken one, or the batch is refused
    for the wrong reason and the real problem is never named."""
    from atlas.state import validate as validate_module

    def explode(_ctx):
        raise ZeroDivisionError("a rule with a bug in it")
        yield  # pragma: no cover

    real = _real_rules()
    monkeypatch.setattr(
        validate_module, "all_rules", lambda: (_broken("TEST-999", explode), *real))

    # A change that a *real* rule refuses, behind the broken one in the running order.
    outcome = apply_batch(conn, batch_factory(
        diff("region", "r_north", "owner_nation_id", "karthos")))

    assert not outcome.applied
    caught = {v.rule_id for v in outcome.validation.violations}
    assert "TEST-999" in caught, "the broken rule was not reported"
    assert caught - {"TEST-999"}, (
        f"only the broken rule ran; the real ones were skipped: {caught}"
    )
    assert "TEST-999" in outcome.validation.ran


def test_a_raising_rule_condemns_the_whole_batch_rather_than_one_diff(
    conn, batch_factory, monkeypatch
):
    """A rule that broke did not decide anything about any *particular* write, so it cannot
    name one -- and a violation that names no diff condemns the batch. Severing around a
    rule that never ran would apply most of a batch nothing had checked."""
    from atlas.state import validate as validate_module

    def explode(_ctx):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    real = _real_rules()
    monkeypatch.setattr(
        validate_module, "all_rules", lambda: (*real, _broken("TEST-998", explode)))

    outcome = apply_batch(conn, batch_factory(
        diff("nation", "karthos", "stability", 51.0),
        diff("nation", "karthos", "legitimacy", 52.0),
        diff("nation", "aurelia", "stability", 53.0),
    ))

    assert not outcome.applied
    assert outcome.severed == (), "a batch was partly applied around a rule that never ran"


def _broken(rule_id: str, fn):
    """A rule is a callable carrying an `id` and a `description`; `register` decorates one
    into existence exactly this way. Built by hand rather than registered, so the broken
    rule cannot leak into another test's run."""
    fn.id = rule_id
    fn.description = "a rule with a bug in it"
    return fn


def _real_rules():
    from atlas.state.validate import all_rules

    return tuple(all_rules())


# ------------------------------------------------- UNIT-001 is about the nations in the batch


def _raise_for(conn, nation_id: str, unit_id: str):
    """The legitimate engine path: a raise event, then the formation that cites it."""
    from atlas.state.apply import apply_batch, record_event
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

    region = conn.execute(
        "SELECT id FROM region WHERE world_id = 'sandbox' AND owner_nation_id = ? LIMIT 1",
        (nation_id,)).fetchone()
    raised = record_event(
        conn, world_id="sandbox", turn=0, date="1936-01-01",
        kind=EventKind.UNIT_RAISED, origin=EventOrigin.ENGINE,
        text=f"{nation_id} raises a division.", actor_nation_id=nation_id,
        visibility=Visibility.PUBLIC)
    return apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(
            Diff("unit", unit_id, "strength", DiffOp.INSERT,
                 {"world_id": "sandbox", "nation_id": nation_id,
                  "region_id": str(region["id"]), "kind": "infantry",
                  "strength": 4.0, "morale": 50.0},
                 reason_event_id=raised),
        )))


def test_somebody_elses_insolvency_does_not_refuse_your_formation(conn):
    """**The defect this pins, found by the laboratory on PT67.**

    The rule's docstring has always said *the owning nation* must not be driven negative
    paying for a unit. The query said something else entirely -- it scanned every living
    nation in the world -- so one insolvent country refused every unit-touching batch
    anywhere on the map. On turns 6 and 7 of that recording, three then six unrelated
    engine batches were thrown out under South Vietnam's name: a yard could not deliver a
    ship it had finished and paid for months earlier because a country on the other side of
    the world was overdrawn.
    """
    conn.execute("UPDATE nation SET treasury = -12.0 WHERE id = 'veyra'")
    conn.commit()

    outcome = _raise_for(conn, "karthos", "u_unrelated")

    assert outcome.applied, outcome.reason
    assert conn.execute(
        "SELECT COUNT(*) FROM unit WHERE id = 'u_unrelated'").fetchone()[0] == 1


def test_your_own_insolvency_still_refuses_your_formation(conn):
    """The half that must not have been lost narrowing it. A nation driven negative paying
    for a unit is the signal the rule exists for, and scoping it to the batch's nations had
    every chance of scoping it to nobody."""
    conn.execute("UPDATE nation SET treasury = -12.0 WHERE id = 'karthos'")
    conn.commit()

    outcome = _raise_for(conn, "karthos", "u_broke")

    assert not outcome.applied
    assert "UNIT-001" in outcome.reason
    assert conn.execute(
        "SELECT COUNT(*) FROM unit WHERE id = 'u_broke'").fetchone()[0] == 0


def test_a_patron_that_overdraws_paying_for_somebody_else_is_still_caught(conn):
    """The other way into scope. A nation whose treasury or manpower this batch moved is in
    scope whether or not it owns the formation -- otherwise a power could bankrupt itself
    arming a client and the rule would look at the client."""
    from atlas.state.apply import apply_batch, record_event
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

    region = conn.execute(
        "SELECT id FROM region WHERE world_id = 'sandbox' AND owner_nation_id = 'karthos'"
        " LIMIT 1").fetchone()
    purse = float(conn.execute(
        "SELECT treasury FROM nation WHERE id = 'veyra'").fetchone()[0])
    raised = record_event(
        conn, world_id="sandbox", turn=0, date="1936-01-01",
        kind=EventKind.UNIT_RAISED, origin=EventOrigin.ENGINE,
        text="Veyra arms Karthos.", actor_nation_id="veyra",
        visibility=Visibility.PUBLIC)

    outcome = apply_batch(conn, DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(
            Diff("unit", "u_client", "strength", DiffOp.INSERT,
                 {"world_id": "sandbox", "nation_id": "karthos",
                  "region_id": str(region["id"]), "kind": "infantry",
                  "strength": 4.0, "morale": 50.0},
                 reason_event_id=raised),
            Diff("nation", "veyra", "treasury", DiffOp.ADJUST, -(purse + 5.0),
                 reason_event_id=raised),
        )))

    assert not outcome.applied
    assert "UNIT-001" in outcome.reason


def test_a_batch_that_touches_no_nation_and_no_unit_is_not_asked_about_solvency(conn):
    """The narrowing must not have turned the rule into one that fires on everything by
    finding an empty scope and checking the world instead."""
    conn.execute("UPDATE nation SET treasury = -12.0 WHERE id = 'veyra'")
    conn.commit()
    from atlas.state.diff import DiffBatch
    from atlas.state.models import DIFFICULTY_PROFILES, Difficulty, Role
    from atlas.state.rules.military import unit_conservation
    from atlas.state.validate import RuleContext

    ctx = RuleContext(
        conn=conn,
        batch=DiffBatch(world_id="sandbox", turn=0, proposer_role=Role.ENGINE,
                        proposer_model=None, diffs=()),
        profile=DIFFICULTY_PROFILES[Difficulty.NORMAL])

    assert list(unit_conservation(ctx)) == []
