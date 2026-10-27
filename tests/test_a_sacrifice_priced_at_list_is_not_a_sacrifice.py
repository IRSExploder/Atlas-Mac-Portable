"""What an intervention cost has to be measured in money that moved.

The embargo's whole claim to being a decision rather than a free weapon is that it costs the
government making it. That cost was reported as ``revenue_forgone``: for every lane the embargo
stopped, the volume it would have carried times the price it was standing at, accumulated each
round. The arithmetic was right. The number was not a cost.

Two things were wrong with it and they are the same thing. It priced a **counterfactual it did
not check** -- shadow-reading the buyer's ceiling against the lanes under a standing embargo
found 237 of 237 in cold-war and 492 of 492 in modern-day already stopped on price, so the
cargo being billed for was not moving. And it was priced at the lane's **standing terms**
rather than at anything anybody paid, so it could not have noticed: a list price is what a lane
would have earned had it been running, and asking it whether the lane was running is a
question it does not answer.

Guard 4 refuses a cost claim that cannot show its receipts. This file proves each way it bites:

* a claim priced at **list**;
* a claim **labelled** settled that still reconciles to a list-price total;
* a claim with **no receipts at all**;
* a claim billing **lanes that were not moving**;
* a claim contradicted by the **purse** of the party said to have paid;
* and, on the other side, a claim built from settled receipts, which passes.
"""

from __future__ import annotations

from atlas.testing.paired import (
    ArmSnapshot,
    CostBasis,
    CounterfactualCost,
    Guard,
    Receipt,
    check_paired_run,
    paired_run,
)

SEED = 2026
LANES = 8
STANDING_PRICE = 0.26
VOLUME = 40.0

#: What the lanes were actually paid over the window. Two of the eight were moving; the other
#: six had already stopped on the buyer's ceiling and settled nothing.
SETTLED_RECEIPTS = tuple(
    Receipt(turn=turn, payer="bel", payee="usa", amount=1.04, detail="oil, settled")
    for turn in range(1, 7)
)
SETTLED_TOTAL = round(sum(r.amount for r in SETTLED_RECEIPTS), 3)

#: And what the list-price arithmetic said: every lane, priced at what it was standing at,
#: whether or not it had carried a unit. The gap between these two numbers is the finding.
LIST_TOTAL = round(LANES * VOLUME * STANDING_PRICE, 3)


def _valid_pair(costs):
    """A run that is sound on guards 1-3, so only guard 4 can be what fails."""
    run = paired_run("embargo-agency", SEED, ["arm2", "arm3"])
    snapshots = {
        arm: ArmSnapshot(run.world_id, run.seed, sections={"treasury": "same"})
        for arm in ("arm2", "arm3")
    }
    return run, check_paired_run(run, snapshots, costs=costs)


def _only_guard_four(validity) -> list[str]:
    assert validity.failed() == [Guard.SETTLED_COST], validity.rendered()
    return [p for p in validity.problems if p.startswith("[settled-cost]")]


def test_a_cost_priced_at_the_standing_price_is_refused() -> None:
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=LIST_TOTAL, basis=CostBasis.LIST,
            lanes_billed=LANES, lanes_moving=LANES),
    })

    problems = _only_guard_four(validity)
    assert any("priced at list, not at what was paid" in p for p in problems)
    assert any("prices cargo that was not moving" in p for p in problems)
    assert any("no receipts behind it" in p for p in problems)


def test_a_basis_the_harness_did_not_state_is_treated_as_harshly() -> None:
    """Defaulting an unstated provenance to 'settled' would make the whole guard decorative."""
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.UNKNOWN, receipts=SETTLED_RECEIPTS,
            lanes_billed=2, lanes_moving=2, purse_delta=-SETTLED_TOTAL),
    })

    problems = _only_guard_four(validity)
    assert any("priced at unknown, not at what was paid" in p for p in problems)


def test_relabelling_a_list_price_as_settled_does_not_settle_it() -> None:
    """The teeth of the guard. The basis is a claim the harness makes about itself, so it
    cannot be the only thing checked -- a harness that changed one enum value would otherwise
    walk straight through. The reconciliation is the part that is not up to the harness: the
    figure has to add up to the payments behind it, and a list-price total does not, because it
    is priced over six lanes that settled nothing.
    """
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=LIST_TOTAL, basis=CostBasis.SETTLED, receipts=SETTLED_RECEIPTS,
            lanes_billed=2, lanes_moving=2, purse_delta=-SETTLED_TOTAL),
    })

    problems = _only_guard_four(validity)
    reconcile = next(p for p in problems if "does not reconcile" in p)
    assert f"{LIST_TOTAL:.3f}" in reconcile and f"{SETTLED_TOTAL:.3f}" in reconcile
    assert "Labelling a figure 'settled' does not settle it." in reconcile


def test_a_claim_with_no_receipts_is_a_price_list() -> None:
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.SETTLED,
            lanes_billed=2, lanes_moving=2, purse_delta=-SETTLED_TOTAL),
    })

    problems = _only_guard_four(validity)
    assert any("with no receipts behind it" in p for p in problems)
    assert any("A cost with no record of anybody paying it is a price list." in p
               for p in problems)


def test_billing_a_lane_that_was_not_moving_is_refused() -> None:
    """The 237-of-237 finding, as a guard: eight lanes billed, two of them carrying cargo."""
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.SETTLED, receipts=SETTLED_RECEIPTS,
            lanes_billed=LANES, lanes_moving=2, purse_delta=-SETTLED_TOTAL),
    })

    problems = _only_guard_four(validity)
    billed = next(p for p in problems if "bills 8 lane(s)" in p)
    assert "only 2 were moving cargo" in billed
    assert "A lane that shipped nothing cannot have stopped shipping anything." in billed


def test_a_sacrifice_that_left_the_payer_richer_is_refused() -> None:
    """Ask the purse, not the ledger that records the sacrifice.

    ``revenue_forgone`` is a column the mechanic writes about itself. The independent question
    is what the proposer's treasury actually did against the same power in the arm where it
    did not embargo anybody -- and a cost that shows up only in its own bookkeeping is not one.
    """
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.SETTLED, receipts=SETTLED_RECEIPTS,
            lanes_billed=2, lanes_moving=2, purse_delta=+4.2),
    })

    problems = _only_guard_four(validity)
    purse = next(p for p in problems if "better off than the control" in p)
    assert "+4.200" in purse
    assert "only in its own ledger and not in the purse" in purse


def test_a_negative_sacrifice_is_a_sign_error_and_is_named_as_one() -> None:
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(claimed=-SETTLED_TOTAL, basis=CostBasis.SETTLED),
    })
    assert any("is negative" in p for p in _only_guard_four(validity))


def test_an_unpaired_purse_is_a_note_and_not_a_failure() -> None:
    """The one thing here that weakens a run without voiding it.

    Pairing the purse needs the control arm to have been read for the same power, which not
    every harness does. That is a thinner claim, not a broken one -- so it is recorded where a
    reader will see it rather than turned into an abort that would push harnesses into not
    declaring a cost at all.
    """
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.SETTLED, receipts=SETTLED_RECEIPTS,
            lanes_billed=2, lanes_moving=2),
    })

    assert validity.ok, validity.rendered()
    assert any("the purse was not paired" in n for n in validity.notes)
    assert any("marking its own homework" in n for n in validity.notes)


def test_a_run_that_claims_no_cost_says_so_out_loud() -> None:
    """Silence is the failure mode this whole family of bugs travelled under.

    A run with no cost claim is not a fault, so guard 4 passes -- but it leaves a note saying
    that any cost figure appearing in a report of this run did not come through the guard,
    because "nothing was checked" and "everything checked out" must never render the same.
    """
    _, validity = _valid_pair(None)

    assert validity.ok
    settled = next(o for o in validity.outcomes if o.guard is Guard.SETTLED_COST)
    assert settled.detail == "no counterfactual cost claimed; nothing to reconcile"
    assert any("did not come through this guard" in n for n in validity.notes)


def test_a_cost_built_from_settled_receipts_is_believed() -> None:
    _, validity = _valid_pair({
        "arm3": CounterfactualCost(
            claimed=SETTLED_TOTAL, basis=CostBasis.SETTLED, receipts=SETTLED_RECEIPTS,
            lanes_billed=2, lanes_moving=2, purse_delta=-SETTLED_TOTAL,
            detail="two lanes were carrying; the other six had already stopped on price"),
    })

    assert validity.ok, validity.rendered()
    assert not validity.notes
    settled = next(o for o in validity.outcomes if o.guard is Guard.SETTLED_COST)
    assert settled.detail == "1 claim(s) reconciled against settled receipts"

    # And the tolerance absorbs the engine's rounding without absorbing a wrong number: the
    # gap between the settled total and the list-price total is over five times the claim.
    assert LIST_TOTAL > 5 * SETTLED_TOTAL
