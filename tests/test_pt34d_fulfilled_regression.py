"""PT34d's actual answer, replayed against the engine that now refuses it.

**This is the recorded response, not a reconstruction.** Entry 27 of
`scratchpad/cassettes/pt34d-modern-day.jsonl` is what India's staff really returned on turn 6
of that run, read out of the tape at test time:

    {"target_table": "commitment", "target_id": "11", "field": "status",
     "op": "set", "new_value": "fulfilled", "expected_prior": "active", ...}

Commitment 11 was a `trade_agreement` carrying an `open_trade` term -- India shipping rare
earths to Germany, 0.185 a month, opened by `honour_terms` on turn 6 and the first route any
model has ever negotiated open. That one diff closed it. The engine read `fulfilled` as *no
longer active*, correctly cut the route on turn 7, and Germany and the United States both
dispatched envoys the next turn asking why the shipments had stopped.

Two changes since have to make this impossible, and this asserts both against the real
payload rather than against a paraphrase of it:

* `commitment.status` is engine-only, so the write is refused before any rule looks at it;
* DIP-005 would refuse it even if the engine proposed it, because a standing supply
  arrangement is not the sort of promise that finishes.

If the tape is ever unavailable the test says so rather than passing quietly -- a regression
that silently stops exercising the thing it was written for is worse than no regression.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.engine.treaties import fulfillment_blockers, honour_terms
from atlas.state.apply import apply_batch
from atlas.state.diff import parse_batch
from atlas.state.models import Role

WORLD = "sandbox"
_TAPE = Path(__file__).resolve().parent.parent / "scratchpad" / "cassettes" / "pt34d-modern-day.jsonl"


def _recorded_answer() -> dict:
    """India's turn-6 answer, straight off the tape."""
    if not _TAPE.exists():
        pytest.fail(
            f"the PT34d tape is missing from {_TAPE}; this regression replays a recorded "
            "answer and cannot be run against a paraphrase of it")
    for line in _TAPE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        data = entry.get("data") or {}
        for change in data.get("diffs") or []:
            if (change.get("target_table") == "commitment"
                    and str(change.get("new_value")) == "fulfilled"):
                return data
    pytest.fail("no recorded answer in the PT34d tape marks a commitment fulfilled")
    raise AssertionError  # unreachable; for the type checker


@pytest.fixture
def supplying(conn):
    """Commitment 11 as PT34d had it on turn 6: active, termed, and shipping."""
    conn.execute(
        "INSERT INTO commitment (id, world_id, kind, status, condition_text, created_turn)"
        " VALUES (11, ?, 'trade_agreement', 'active', 'rare earths for hard currency', 4)",
        (WORLD,))
    for nation_id in ("karthos", "aurelia"):
        conn.execute(
            "INSERT INTO commitment_nation (commitment_id, nation_id, accepted_turn)"
            " VALUES (11, ?, 4)", (nation_id,))
    conn.execute(
        "INSERT INTO commitment_term (commitment_id, seq, world_id, clause, obliged_id,"
        " subject_id, value) VALUES (11, 0, ?, 'open_trade', 'karthos', 'rare', 0.185)",
        (WORLD,))
    # **The recorded answer carries a second diff, on `relation ind|deu`.** Those are Modern
    # Day powers and the sandbox has neither, so without them the batch fails at apply time
    # on the *sibling* write and the refusal being asserted here never happens -- a green
    # test proving the wrong thing. Seeding them keeps the payload verbatim and leaves the
    # status write as the only reason the batch can be refused.
    for nation_id, name in (("ind", "India"), ("deu", "Germany")):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour) VALUES (?, ?, ?, '#404040')",
            (nation_id, WORLD, name))
    for a, b in (("ind", "deu"), ("deu", "ind")):
        conn.execute(
            "INSERT INTO relation (world_id, nation_a, nation_b, score) VALUES (?, ?, ?, 0)",
            (WORLD, a, b))
    conn.execute("UPDATE world SET turn_number = 6 WHERE id = ?", (WORLD,))
    conn.commit()
    # The route the agreement opened, written by the engine exactly as it was in the run.
    opened = honour_terms(conn, WORLD, turn=6, date="1897-09-01")
    assert opened.honoured, opened.problems
    assert _routes(conn) == 1, "the fixture must start with the route open"
    return conn


def _routes(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM trade_route WHERE world_id = ? AND id = 'tr_c11_0'",
        (WORLD,)).fetchone()[0]


def _status(conn) -> str:
    return conn.execute("SELECT status FROM commitment WHERE id = 11").fetchone()[0]


def test_the_recorded_answer_still_names_the_commitment_it_closed():
    """A guard on the fixture. If the tape's shape ever changes, this fails loudly instead
    of the regression quietly testing nothing."""
    data = _recorded_answer()
    closing = [
        change for change in data["diffs"]
        if change["target_table"] == "commitment" and change["new_value"] == "fulfilled"
    ]

    assert len(closing) == 1
    assert closing[0]["field"] == "status"
    assert closing[0]["expected_prior"] == "active", (
        "the recorded answer no longer describes closing an active agreement")


def test_replaying_pt34d_s_answer_no_longer_closes_the_agreement(supplying):
    """(1) refused, (2) still active, (3) the route stays open.

    Driven through `parse_batch` as a `tracked_agent`, which is the role and the path the
    answer actually took.
    """
    batch = parse_batch(
        _recorded_answer(), world_id=WORLD, turn=6, role=Role.TRACKED_AGENT,
        model="gpt-5.6-terra", nation_id="karthos")

    outcome = apply_batch(supplying, batch)

    assert not outcome.applied, "the answer that cut the route was accepted again"
    assert "engine-only" in outcome.reason, outcome.reason
    assert _status(supplying) == "active", "the supply arrangement was closed"
    assert _routes(supplying) == 1, "the trade route was cut"


def test_the_route_survives_the_turn_after_as_well(supplying):
    """The failure in PT34d was not the write -- it was turn 7, when `honour_terms` read a
    no-longer-active agreement and closed the route it had opened. So the check that
    matters is the *next* turn."""
    batch = parse_batch(
        _recorded_answer(), world_id=WORLD, turn=6, role=Role.TRACKED_AGENT,
        model="gpt-5.6-terra", nation_id="karthos")
    apply_batch(supplying, batch)

    supplying.execute("UPDATE world SET turn_number = 7 WHERE id = ?", (WORLD,))
    supplying.commit()
    after = honour_terms(supplying, WORLD, turn=7, date="1897-10-01")

    assert _routes(supplying) == 1, "the rare shipments stopped again"
    assert not any("stop" in line for line in after.honoured), after.honoured


def test_and_the_engine_could_not_have_proposed_it_either(supplying):
    """The second lock. Even if a fulfillment derivation existed and reached for this
    agreement, DIP-005 refuses it -- because `open_trade` is a standing term."""
    blockers = fulfillment_blockers(
        supplying, WORLD, commitment_id=11, kind="trade_agreement")

    assert blockers, "a standing supply arrangement was considered dischargeable"
    assert "standing obligation" in blockers[0], blockers
