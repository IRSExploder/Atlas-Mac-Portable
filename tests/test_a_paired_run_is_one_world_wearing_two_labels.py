"""The shape a paired A/B comparison has to have, driven end to end.

The three files beside this one prove each guard *bites*. This one proves the framework is
usable -- that a real comparison can be built through it, that a valid one passes all four
guards, and that the helper's shape makes the fault it exists to prevent hard to write rather
than merely detectable afterwards.

The toy engine here is deliberately the smallest thing that has the property under test: it
draws from :func:`atlas.sim.rng.derive`, exactly as the real engine does, so its arms diverge
for exactly the reason the real arms diverged. Nothing else about it matters.

What is pinned:

* **one world, two labels.** :func:`paired_run` issues a single :class:`WorldKey`;
  :func:`run_paired` builds both arms from it and every guard passes.
* **the arm is not in scope where the world is built.** ``build`` takes the key and nothing
  else, which is guard 2 written as a function signature rather than as a check.
* **the intervention is the only difference.** With no treatment applied, both arms land on
  the same state; with it applied, they differ -- and the framework still vouches for the run,
  because a difference *after* the intervention is the finding, not the fault.
* **a declared seeded difference is allowed, and is checked in both directions.**
* **the adoption path into a preflight** returns the same findings as plain strings.
* **the sqlite section helper** digests a real world, and does not hash the world's name.
"""

from __future__ import annotations

from atlas.scenarios.sandbox import sandbox_seed
from atlas.sim import rng
from atlas.state.bootstrap import create_world
from atlas.testing.paired import (
    ArmLabel,
    ArmSnapshot,
    CostBasis,
    CounterfactualCost,
    Guard,
    Receipt,
    SeededDifference,
    WorldKey,
    check_paired_run,
    paired_run,
    preflight_problems,
    run_paired,
    section_digests,
)

NATIONS = ("aurelia", "borealis", "cinnabar", "dorne")
TURNS = 12


class ToyWorld:
    """Four treasuries and a coin, drawn the way the engine draws one.

    Every movement comes from ``rng.derive(seed, world_id, turn, call_key)``, so this world has
    the one property that matters for these tests: **its history is a function of its name.**
    That is not a quirk of the toy, it is the engine's deliberate design -- streams are keyed on
    where and when a draw happens so that adding a provider call somewhere else cannot shift a
    battle -- and it is what made a per-arm world id catastrophic.
    """

    def __init__(self, key: WorldKey) -> None:
        self.world_id = key.world_id
        self.seed = key.seed
        self.treasury = dict.fromkeys(NATIONS, 100.0)
        self.receipts: list[Receipt] = []

    def play(self, turns: int, *, tax: float = 0.0) -> None:
        for turn in range(turns):
            for nation in NATIONS:
                stream = rng.derive(self.seed, self.world_id, turn, f"income:{nation}")
                self.treasury[nation] += round(stream.random() * 10.0, 3)
            if tax:
                for nation in NATIONS:
                    paid = round(min(tax, self.treasury[nation]), 3)
                    self.treasury[nation] -= paid
                    self.receipts.append(
                        Receipt(turn=turn, payer=nation, payee="the-exchequer", amount=paid))

    def sections(self) -> dict[str, str]:
        return {
            "treasury": "|".join(f"{n}={self.treasury[n]:.3f}" for n in NATIONS),
            "receipts": f"{len(self.receipts)}",
        }


def _drive(run, *, tax_in, prepare=None, declared=(), cost_of=None, raise_on_invalid=True):
    """Run every arm, taxing only the arms named in ``tax_in``. The tax is the intervention."""
    return run_paired(
        run,
        build=ToyWorld,
        sections=lambda world: world.sections(),
        prepare=prepare,
        play=lambda world, arm: world.play(
            TURNS, tax=0.5 if str(arm) in tax_in else 0.0) or world,
        cost_of=cost_of,
        declared=declared,
        raise_on_invalid=raise_on_invalid,
    )


def test_one_key_is_issued_and_both_arms_run_it() -> None:
    run = paired_run("toy-agency", seed=2026, arms=["control", "treatment"], era="modern-day")

    assert len(run.arms) == 2
    assert all(isinstance(a, ArmLabel) for a in run.arms)
    # One key, and the labels are nowhere in it -- not even as a fragment.
    assert "control" not in run.world_id and "treatment" not in run.world_id

    outcome = _drive(run, tax_in=())
    assert outcome.ok, outcome.validity.rendered()
    assert {s.world_id for s in outcome.snapshots.values()} == {run.world_id}
    assert {s.seed for s in outcome.snapshots.values()} == {run.seed}


def test_the_same_key_reaches_the_same_world_twice() -> None:
    """The claim underneath everything: no intervention, no difference.

    An A/B harness is only measuring the intervention if withholding it leaves the arms
    identical. This runs both arms with the treatment switched off in both and asserts they
    land on the same treasuries -- which is the paired-run property stated as an outcome rather
    than as a guard, and the thing the naming bug destroyed.
    """
    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    outcome = _drive(run, tax_in=())

    control, treatment = outcome.results["control"], outcome.results["treatment"]
    assert control is not treatment
    assert control.treasury == treatment.treasury
    assert len({s.sections["treasury"] for s in outcome.snapshots.values()}) == 1


def test_the_intervention_is_allowed_to_be_the_difference() -> None:
    """A run stays valid when the arms diverge *after* the fingerprint is taken.

    Guard 3 measures the arms up to the intervention and no further, which is the whole reason
    the ``sections`` callback sits where it does in :func:`run_paired`. A guard that compared
    the arms at the end would refuse every experiment that found anything.
    """
    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    outcome = _drive(run, tax_in=("treatment",))

    assert outcome.ok, outcome.validity.rendered()
    assert (outcome.results["treatment"].treasury["aurelia"]
            < outcome.results["control"].treasury["aurelia"])
    assert outcome.results["treatment"].receipts and not outcome.results["control"].receipts


def test_a_seeded_difference_is_allowed_only_where_it_was_declared() -> None:
    """Declared, and checked both ways: it must be named, and it must actually happen."""
    def warm_the_treatment(world: ToyWorld, arm: ArmLabel) -> None:
        if str(arm) == "treatment":
            world.treasury["aurelia"] += 25.0

    declared = (SeededDifference(
        section="treasury", why="the treatment arm starts one power 25 richer, on purpose"),)

    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    ok = _drive(run, tax_in=(), prepare=warm_the_treatment, declared=declared)
    assert ok.ok, ok.validity.rendered()

    # Undeclared, and the same edit is now a fault.
    undeclared = _drive(
        paired_run("toy-agency", seed=7, arms=["control", "treatment"]),
        tax_in=(), prepare=warm_the_treatment, declared=(), raise_on_invalid=False)
    assert not undeclared.ok
    assert undeclared.validity.failed() == [Guard.PRE_INTERVENTION_IDENTICAL]

    # And a declaration whose seeding silently did not apply is also a fault -- the other half
    # of the same instrumentation family, and the one a permissive guard would wave through.
    never_applied = run_paired(
        paired_run("toy-agency", seed=7, arms=["control", "treatment"]),
        build=ToyWorld, sections=lambda w: w.sections(),
        play=lambda w, arm: w.play(TURNS), declared=declared, raise_on_invalid=False)
    assert not never_applied.ok
    assert any("did not apply" in p for p in never_applied.validity.problems)


def test_a_cost_claim_backed_by_receipts_is_believed() -> None:
    """The fourth guard, on the happy path: what the arm actually collected, and its purse."""
    def claim(world: ToyWorld, arm: ArmLabel) -> CounterfactualCost | None:
        if not world.receipts:
            return None
        return CounterfactualCost(
            claimed=round(sum(r.amount for r in world.receipts), 3),
            basis=CostBasis.SETTLED,
            receipts=tuple(world.receipts),
            lanes_billed=len(NATIONS),
            lanes_moving=len(NATIONS),
            purse_delta=-round(sum(r.amount for r in world.receipts), 3),
        )

    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    outcome = _drive(run, tax_in=("treatment",), cost_of=claim)

    assert outcome.ok, outcome.validity.rendered()
    assert set(outcome.costs) == {"treatment"}
    assert outcome.costs["treatment"].claimed > 0
    assert not outcome.validity.notes  # the purse was paired, so nothing is left uncorroborated


def test_a_harness_adopts_the_guards_through_its_preflight() -> None:
    """The findings come back as plain strings, which is the shape a preflight wants.

    ``preflight.Experiment.check_initial_state`` returns a list of problems and any non-empty
    return aborts the run as INVALID before the clock starts. An experiment adopts all four
    guards by returning :func:`preflight_problems` from it, which puts a paired-run fault where
    it belongs -- among the reasons the *instrument* is not trusted, never among the run's
    findings about the world.
    """
    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    outcome = _drive(run, tax_in=("treatment",))

    assert preflight_problems(run, outcome.snapshots) == []

    broken = dict(outcome.snapshots)
    broken["treatment"] = ArmSnapshot(
        world_id="toy-treatment", seed=run.seed,
        sections=broken["treatment"].sections)
    problems = preflight_problems(run, broken)
    assert problems and all(isinstance(p, str) for p in problems)
    assert any(p.startswith("[same-world]") for p in problems)
    assert any(p.startswith("[label-sealed]") for p in problems)


def test_the_section_helper_digests_a_world_and_not_its_name() -> None:
    """Two real worlds, built from one seed under two names, digest the same.

    That is the point of leaving ``world_id`` out of the hash: if the name were hashed, guard 3
    would fire on every mis-named pair and say only "the arms differ", burying whatever the
    *state* did under the naming fault guard 1 has already reported. Excluding it means a
    guard-3 failure is always about the world, never about the label.
    """
    left = create_world(":memory:", sandbox_seed(world_id="left", seed=42))
    right = create_world(":memory:", sandbox_seed(world_id="right", seed=42))
    try:
        tables = ("nation", "region", "relation")
        assert (section_digests(left, "left", tables)
                == section_digests(right, "right", tables))

        # And it is not blind: move one number and the section that holds it moves with it.
        right.execute("UPDATE nation SET treasury = treasury + 1 WHERE world_id = 'right'")
        moved = section_digests(right, "right", tables)
        base = section_digests(left, "left", tables)
        assert moved["nation"] != base["nation"]
        assert moved["region"] == base["region"] and moved["relation"] == base["relation"]
    finally:
        left.close()
        right.close()


def test_an_arm_that_never_reported_cannot_be_vouched_for() -> None:
    """Silence is not a pass. A missing snapshot voids the run rather than shrinking it."""
    run = paired_run("toy-agency", seed=7, arms=["control", "treatment"])
    outcome = _drive(run, tax_in=())
    only_one = {"control": outcome.snapshots["control"]}

    validity = check_paired_run(run, only_one)
    assert not validity.ok
    assert any("no snapshot for arm" in p for p in validity.problems)
    assert any("nothing to pair" in p for p in validity.problems)
