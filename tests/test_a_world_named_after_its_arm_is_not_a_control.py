"""The bug that corrupted the agency measurements, reproduced, measured, and caught.

The line was::

    wid = f"e{seed}{arm[-1]}{uptake[:2]}{pressure[:2] if uptake == 'forced' else ''}"

and :func:`atlas.sim.rng.derive` hashes ``world_id`` into every stream in the engine. So the
control and the treatment were never the same world. They shared a seed and a scenario and
nothing else -- not a battle, not a coup roll, not a market draw. The difference the study
reported as the effect of the intervention was sitting on top of a difference that came from
the *name*, and nothing in the harness, the engine, the invariants or the release audit said a
word about it, because a number in range with a plausible sign does not look like a bug.

This file does four things:

* **reproduces the mechanism, and measures it.** Two worlds, one seed, names differing by one
  character, no intervention anywhere: a large fraction of nations end at a different treasury.
  Not asserted as a stored figure -- computed here, from the engine's own ``derive``.
* **shows guard 1 catches it before a turn is played.** The RNG witness differs at turn zero,
  when the *state* is still identical and guard 3 has nothing to see. This is the case that
  matters, because it is the one that costs a night.
* **shows guard 3 catches it once the arms have moved**, which is what happens when the
  intervention lands at turn three rather than turn zero.
* **shows the framework's key removes it.** Same construction through :func:`paired_run`, and
  the two arms are byte-identical to the last decimal.
"""

from __future__ import annotations

from atlas.sim import rng
from atlas.testing.paired import (
    ArmSnapshot,
    Guard,
    PairedRunInvalid,
    assert_paired_run,
    check_paired_run,
    paired_run,
    rng_witness,
)

SEED = 2026
NATIONS = tuple(f"n{i:02d}" for i in range(40))
PRE_INTERVENTION_TURNS = 3


def _the_harness_that_was(seed: int, arm: str, uptake: str = "polled") -> str:
    """The world id the agency harness actually built, kept verbatim as the specimen."""
    return f"e{seed}{arm[-1]}{uptake[:2]}"


def _treasuries(world_id: str, seed: int, turns: int) -> dict[str, float]:
    """Play a stripped-down economy: one income draw per nation per turn, and nothing else.

    Every other subsystem is left out on purpose. The claim under test is not about the
    economy; it is that *any* engine drawing through :func:`atlas.sim.rng.derive` diverges
    between two differently-named worlds, and the smallest thing that draws is the honest
    demonstration of it.
    """
    purse = dict.fromkeys(NATIONS, 100.0)
    for turn in range(turns):
        for nation in NATIONS:
            stream = rng.derive(seed, world_id, turn, f"income:{nation}")
            purse[nation] += round(stream.random() * 10.0, 3)
    return purse


def test_the_name_alone_moves_most_of_the_map() -> None:
    """No intervention, one seed, one character of difference in the name."""
    control = _the_harness_that_was(SEED, "arm2")
    treatment = _the_harness_that_was(SEED, "arm3")
    assert (control, treatment) == ("e20262po", "e20263po")

    left = _treasuries(control, SEED, turns=12)
    right = _treasuries(treatment, SEED, turns=12)
    moved = sum(1 for n in NATIONS if left[n] != right[n])

    # Every nation, in fact: a differently-seeded stream agreeing to three decimals over
    # twelve draws would be a coincidence, and the measured figure of 41-71% in the real
    # harness is lower only because most of that engine is not a coin.
    assert moved == len(NATIONS)
    assert left != right

    # And it is the *name* doing it, which is the half a reader is entitled to see proved:
    # hold the name and change nothing else, and the two runs are identical.
    assert _treasuries(control, SEED, turns=12) == left


def test_guard_one_fires_before_a_single_turn_is_played() -> None:
    """The whole value of the guard: it costs nothing and it fires at turn zero.

    At this point the two worlds hold identical state -- they have not been played. A guard
    that compared their fingerprints would pass them. What already differs is the randomness
    they are *about to* produce, which is why :func:`rng_witness` draws from the streams rather
    than inspecting the configuration that names them.
    """
    control = _the_harness_that_was(SEED, "arm2")
    treatment = _the_harness_that_was(SEED, "arm3")

    assert _treasuries(control, SEED, turns=0) == _treasuries(treatment, SEED, turns=0)
    assert rng_witness(control, SEED) != rng_witness(treatment, SEED)

    run = paired_run("agency", seed=SEED, arms=["arm2", "arm3"])
    at_turn_zero = {
        "arm2": ArmSnapshot(control, SEED, sections={"treasury": "identical"}),
        "arm3": ArmSnapshot(treatment, SEED, sections={"treasury": "identical"}),
    }
    validity = check_paired_run(run, at_turn_zero)

    assert Guard.SAME_WORLD in validity.failed()
    assert Guard.PRE_INTERVENTION_IDENTICAL not in validity.failed()
    assert any("would draw different randomness before either of them is played" in p
               for p in validity.problems)
    assert any("This is measured, not inferred" in p for p in validity.problems)


def test_guard_three_fires_once_the_arms_have_run_up_to_the_intervention() -> None:
    """When the intervention lands at turn three, the divergence is already in the state.

    This is the shape the embargo study had -- the scripted pressure was applied at
    ``pressure_turn = 3``, so by the time the treatment existed the arms had each played three
    turns of their own private randomness. Guard 3 reads the fingerprint at the intervention
    point and finds two different worlds, which is the finding that was missing.
    """
    control = _the_harness_that_was(SEED, "arm2")
    treatment = _the_harness_that_was(SEED, "arm3")

    def digest(world_id: str) -> dict[str, str]:
        purse = _treasuries(world_id, SEED, turns=PRE_INTERVENTION_TURNS)
        return {"treasury": "|".join(f"{n}={purse[n]:.3f}" for n in NATIONS)}

    run = paired_run("agency", seed=SEED, arms=["arm2", "arm3"])
    snapshots = {
        "arm2": ArmSnapshot(control, SEED, sections=digest(control)),
        "arm3": ArmSnapshot(treatment, SEED, sections=digest(treatment)),
    }
    validity = check_paired_run(run, snapshots)

    assert set(validity.failed()) >= {Guard.SAME_WORLD, Guard.PRE_INTERVENTION_IDENTICAL}
    assert any("section 'treasury' differs between the arms before the intervention" in p
               for p in validity.problems)
    assert any("Nothing has been done to either arm yet, so this is the instrument." in p
               for p in validity.problems)


def test_the_run_is_refused_rather_than_reported() -> None:
    """A guard that returns a warning beside a number is a guard nobody reads."""
    run = paired_run("agency", seed=SEED, arms=["arm2", "arm3"])
    snapshots = {
        arm: ArmSnapshot(_the_harness_that_was(SEED, arm), SEED, sections={"treasury": arm})
        for arm in ("arm2", "arm3")
    }
    try:
        assert_paired_run(run, snapshots)
    except PairedRunInvalid as refused:
        report = str(refused)
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("a mis-named pair was accepted")

    assert report.startswith("PAIRED RUN INVALID")
    # The report names the guard, the purpose and the evidence, in that order, so that a
    # person reading it in a log knows what was not measured without opening this file.
    assert "same-world" in report and "e20262po" in report and "e20263po" in report


def test_the_framework_key_removes_the_divergence_entirely() -> None:
    """The same comparison, built the intended way: identical to the last decimal."""
    run = paired_run("agency", seed=SEED, arms=["arm2", "arm3"])

    left = _treasuries(run.world_id, run.seed, turns=12)
    right = _treasuries(run.world_id, run.seed, turns=12)
    assert left == right
    assert rng_witness(run.world_id, run.seed) == rng_witness(run.world_id, run.seed)

    def digest() -> dict[str, str]:
        purse = _treasuries(run.world_id, run.seed, turns=PRE_INTERVENTION_TURNS)
        return {"treasury": "|".join(f"{n}={purse[n]:.3f}" for n in NATIONS)}

    snapshots = {
        arm: ArmSnapshot(run.world_id, run.seed, sections=digest()) for arm in ("arm2", "arm3")
    }
    validity = assert_paired_run(run, snapshots)
    assert validity.ok
    assert validity.failed() == []


def test_the_witness_reads_the_world_id_and_says_so() -> None:
    """A guard that did not actually depend on the argument it claims to would be worthless.

    :func:`rng_witness` takes ``derive_fn`` for exactly this: swap in a derivation that ignores
    the world id, and the witness stops distinguishing the two worlds. That proves the witness
    is reading ``derive``'s answer rather than hashing its own arguments -- and that the
    property being tested lives in :func:`atlas.sim.rng.derive`, where the fix has to hold.
    """
    import random

    def blind_to_the_name(seed: int, world_id: str, turn: int, call_key: str) -> random.Random:
        return random.Random(f"{seed}\x1f{turn}\x1f{call_key}")

    control = _the_harness_that_was(SEED, "arm2")
    treatment = _the_harness_that_was(SEED, "arm3")

    assert rng_witness(control, SEED) != rng_witness(treatment, SEED)
    assert (rng_witness(control, SEED, derive_fn=blind_to_the_name)
            == rng_witness(treatment, SEED, derive_fn=blind_to_the_name))
