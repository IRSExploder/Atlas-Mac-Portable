"""Whether the two arms of a comparison were ever the same world.

:mod:`atlas.testing.invariants` says *this cannot be true of a world that is not broken*.
:mod:`atlas.testing.laboratory` reads a run that has happened and says *this is the shape of
a bug*. A preflight (``atlas.testing.preflight``, on the ``experiment-preflight`` line) asks
whether a run is capable of producing evidence at all. This file asks the narrower question
that sits underneath every A/B number this project has published: **were the control and the
treatment the same world, differing only by the intervention?**

**It exists because they were not, and nothing said so.** The agency measurements were run by
harnesses that named each world after the arm it was running --
``wid = f"e{seed}{arm[-1]}{uptake[:2]}"`` -- and :func:`atlas.sim.rng.derive` hashes the world
id into *every* random stream in the engine. So ``arm2`` and ``arm3`` did not share a coin, a
battle, a coup roll or a market draw; they shared a seed and nothing else. Between 41% and 71%
of nations ended a run at a different treasury **from the name alone**, with no intervention
applied anywhere. Every difference the study attributed to the treatment was sitting on top of
that. The second fault was in the same family: the counterfactual "what this embargo cost you"
was priced at the lane's *standing* price, without checking whether the lane had moved a unit
of cargo -- and in the regime where governments actually embargo each other, it mostly had
not. A cost nobody paid, on a lane that was not running, attributed to a decision, in a world
that was not the control's.

Both are **instrumentation** faults, and instrumentation faults share the property that makes
them uniquely expensive: *they do not look like faults*. A broken engine crashes or violates an
invariant. A broken instrument returns a number, in range, with a plausible sign, and the only
thing wrong with it is that it is not measuring the thing in its own column heading. The run
completes, the table renders, the figure is written down, and it is wrong for a reason no
amount of re-reading the table can surface.

So the four guards here are those two nights written down and generalised to the class:

1. :attr:`Guard.SAME_WORLD` -- both arms ran under **one world id and one seed**, and the RNG
   they were about to draw from was byte-identical. Checked against what the arms *actually
   used*, never against what the harness meant to use.
2. :attr:`Guard.LABEL_SEALED` -- the arm label is a **runtime-only** name. Stated as a
   property of the inputs rather than of the string: **nothing** :func:`atlas.sim.rng.derive`
   reads may vary with the arm, and no arm label or fragment of one may appear in any of it.
   The first half is what makes the guard sound, because it does not need to recognise the
   leak to catch it -- a one-character slice of a label is unrecognisable, and a world id that
   moves with the arm is not.
3. :attr:`Guard.PRE_INTERVENTION_IDENTICAL` -- the arms **fingerprint identically up to the
   moment the intervention lands**, section by section, except where a difference was
   *declared in advance* as deliberate. A declared difference that then fails to appear is
   also a failure: seeding that silently did not apply is the same class of fault pointing the
   other way.
4. :attr:`Guard.SETTLED_COST` -- a counterfactual cost is reconciled against **receipts for
   money that actually moved**, not against a list or standing price. The claim must add up to
   the receipts behind it, and every lane it billed for must have been moving.

**What is deliberately not here.** No engine. The framework never builds a world, plays a turn
or reads a table; a harness hands it what its arms did and the framework judges. The one
exception is :func:`atlas.sim.rng.derive`, imported by :func:`rng_witness`, because the fault
guard 1 exists to catch is a property of that exact function -- and a reimplementation of it
here would be a copy free to drift away from the thing it is meant to be watching.

**How a harness adopts this.** Build the run with :func:`paired_run` -- which takes a study
name, a seed and the arm labels, and has **no parameter through which an arm could reach the
world id**. Then either drive it with :func:`run_paired`, whose ``build`` callback is handed
the :class:`WorldKey` and *not* the arm, or run the arms by hand and hand
:func:`assert_paired_run` an :class:`ArmSnapshot` per arm. Either way the run aborts on a
guard rather than producing a number. :func:`preflight_problems` returns the same findings as
a list of strings, which is the shape ``preflight.Experiment.check_initial_state`` returns, so
an experiment already under a preflight adopts all four guards by returning them from there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import blake2b
from typing import Any

from ..sim.rng import derive

__all__ = [
    "ArmLabel",
    "ArmLabelLeak",
    "ArmSnapshot",
    "CostBasis",
    "CounterfactualCost",
    "Guard",
    "GuardOutcome",
    "PairedOutcome",
    "PairedRun",
    "PairedRunInvalid",
    "PairedValidity",
    "Receipt",
    "SeededDifference",
    "WorldKey",
    "assert_paired_run",
    "check_paired_run",
    "paired_run",
    "preflight_problems",
    "rng_witness",
    "run_paired",
    "section_digests",
]


# ------------------------------------------------------------------ the label, and its taint


class ArmLabelLeak(AssertionError):
    """An arm label, or something cut out of one, reached a value the RNG reads.

    Raised at construction time by :class:`WorldKey` rather than reported at the end of the
    run, because the entire cost of this bug is that it is discovered *after* the night is
    spent. A leak found here costs nothing.
    """


class ArmLabel(str):
    """The name of one arm, and a string that remembers where its pieces came from.

    An arm label is **runtime-only**: it selects which flags a run plays under and it titles a
    column in the report. It must never touch anything :func:`atlas.sim.rng.derive` hashes.
    The obvious way to enforce that is to keep the label out of the world id, and the obvious
    way to break it is what the agency harness did::

        wid = f"e{seed}{arm[-1]}{uptake[:2]}"

    Note what is smuggled through: not the label, but *one character sliced out of it*. A
    guard that searched the world id for the string ``"arm3"`` would have found nothing and
    passed the run. So the label is a string subclass whose derived values stay labels --
    ``arm[-1]``, ``arm.lower()``, ``"e" + arm`` and ``f"{arm}"`` all come back as
    :class:`ArmLabel` -- and :class:`WorldKey` refuses any component that is one. The line
    above then raises before a single turn is played.

    **The taint is the early-warning layer and it is explicitly not the guarantee.** Measured,
    not assumed -- it survives ``arm[-1]``, ``arm[:2]``, ``arm.lower()``, ``"e" + arm``,
    ``arm + "x"``, ``f"{arm}"`` and ``f"{arm[-1]}"``; it does **not** survive
    ``f"e2026{arm[-1]}"``, ``"".join([...])``, ``"e%s" % arm`` or a trip through ``int()``,
    because CPython builds a multi-piece f-string by concatenating the already-formatted parts
    into a fresh exact ``str`` and there is no hook on that.

    That last case is the shape the real bug had, which is worth saying plainly rather than
    leaving a reader to assume the taint would have caught it: **it would not have.** What
    catches that form is the pair of guards that do not depend on the harness co-operating --
    :attr:`Guard.SAME_WORLD` compares the world ids and RNG witnesses the arms *actually ran
    under*, and :attr:`Guard.LABEL_SEALED` refuses any value ``rng.derive`` reads that moves
    with the arm at all. A leak of any shape whatsoever trips both. The taint is here because
    it is free and because it turns the subset of leaks it does see into an exception at the
    line that wrote them, rather than a verdict an hour later.
    """

    __slots__ = ()

    def __radd__(self, other: Any) -> Any:
        """``"e" + arm``: a literal prefixed onto a label, which is how ids get built.

        ``str`` has no ``__radd__`` of its own, so this one is written out rather than
        generated with the rest below. Python offers the reflected slot first precisely
        because :class:`ArmLabel` subclasses ``str``, which is what makes the
        literal-on-the-left case catchable at all. Both halves go through ``str.__str__`` to
        shed the taint before concatenating: ``str(self)`` is itself tainted and would come
        straight back here.
        """
        if not isinstance(other, str):
            return NotImplemented
        return _keep(str.__str__(other) + str.__str__(self))


def _keep(value: Any) -> Any:
    """Re-wrap whatever a string operation returned, so the taint survives it."""
    if isinstance(value, ArmLabel):
        return value
    if isinstance(value, str):
        return ArmLabel(value)
    if isinstance(value, list):
        return [_keep(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_keep(item) for item in value)
    return value


def _tainting(method: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(self: ArmLabel, *args: Any, **kwargs: Any) -> Any:
        return _keep(method(self, *args, **kwargs))

    wrapper.__name__ = method.__name__
    wrapper.__qualname__ = f"ArmLabel.{method.__name__}"
    return wrapper


#: The string operations the taint follows. Chosen as the ones a harness plausibly reaches for
#: while building an id out of a label -- indexing and slicing above all, which is the one the
#: real bug used. Set on the class in a loop rather than written out, so that adding a name
#: here is the whole change.
_TAINTED_OPERATIONS = (
    "__add__", "__getitem__", "__mul__", "__rmul__", "__mod__", "__str__",
    "__format__", "capitalize", "casefold", "center", "expandtabs", "format", "join",
    "ljust", "lower", "lstrip", "partition", "removeprefix", "removesuffix", "replace",
    "rjust", "rpartition", "rsplit", "rstrip", "split", "splitlines", "strip", "swapcase",
    "title", "upper", "zfill",
)

for _operation in _TAINTED_OPERATIONS:
    setattr(ArmLabel, _operation, _tainting(getattr(str, _operation)))
del _operation


def _is_tainted(value: Any) -> bool:
    """Whether this value is, or contains, something cut out of an arm label."""
    if isinstance(value, ArmLabel):
        return True
    if isinstance(value, Mapping):
        return any(_is_tainted(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_is_tainted(v) for v in value)
    return False


# --------------------------------------------------------------------------- the world key


@dataclass(frozen=True, slots=True)
class WorldKey:
    """The identity of the world **both** arms are run in: one id, one seed.

    This is the whole fix in one type. There is exactly one of these per paired run, every arm
    is built from it, and it is constructed by :func:`paired_run`, whose signature has no arm
    in it. A harness that wants a per-arm world id has to go out of its way to build a second
    :class:`WorldKey` -- and :attr:`Guard.SAME_WORLD` reads the two ids off the arms afterwards
    and refuses the run.
    """

    world_id: str
    seed: int

    def __post_init__(self) -> None:
        if _is_tainted(self.world_id) or _is_tainted(self.seed):
            raise ArmLabelLeak(
                f"the world id {str(self.world_id)!r} was built out of an arm label. "
                "rng.derive hashes the world id into every stream in the engine, so arms "
                "with different names are different worlds and the comparison measures the "
                "name. Keep the label in the run's runtime config, never in its identity."
            )
        if not str(self.world_id).strip():
            raise ValueError("a world key needs a world id")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError(f"a world seed must be an int, not {type(self.seed).__name__}")

    @property
    def rng_inputs(self) -> dict[str, Any]:
        """Exactly what :func:`atlas.sim.rng.derive` will read, for guard 2 to scan."""
        return {"world_id": str(self.world_id), "seed": int(self.seed)}


@dataclass(frozen=True, slots=True)
class PairedRun:
    """One world, and the arms that take turns wearing a label in it."""

    study: str
    world_key: WorldKey
    arms: tuple[ArmLabel, ...]

    @property
    def world_id(self) -> str:
        return self.world_key.world_id

    @property
    def seed(self) -> int:
        return self.world_key.seed


def paired_run(study: str, seed: int, arms: Sequence[str], *, era: str = "") -> PairedRun:
    """Construct a paired run: one world id, one seed, and arms that are only labels.

    **The arms are not a parameter of the world id, and that is the point.** The id is derived
    from the study, the era and the seed -- the three things that identify *which world this
    is* -- and the arm labels are attached afterwards, as names. There is no argument order, no
    keyword and no formatting hook through which an arm could reach it.

    The id is a short digest rather than the study name spelled out, so that a study whose name
    happens to contain an arm word (``"control-vs-treatment"``) cannot trip guard 2 on a
    technicality, and so that two studies cannot collide on one world by accident.
    """
    if len(arms) < 2:
        raise ValueError("a paired run needs at least two arms")
    labels = tuple(ArmLabel(a) for a in arms)
    if len({str(a) for a in labels}) != len(labels):
        raise ValueError(f"the arm labels are not distinct: {[str(a) for a in labels]}")
    if _is_tainted(study) or _is_tainted(era):
        raise ArmLabelLeak(
            "the study or era name was built out of an arm label; the world id is derived "
            "from both, so this is the same leak by a longer route"
        )
    stem = blake2b(f"{study}\x1f{era}\x1f{seed}".encode(), digest_size=5).hexdigest()
    return PairedRun(study=study, world_key=WorldKey(f"pr{stem}", int(seed)), arms=labels)


# ------------------------------------------------------------------------- the RNG witness

#: The streams :func:`rng_witness` draws from. Deliberately shaped like real call keys -- a
#: battle, a coup, a market draw, a weather roll -- so the witness exercises the same hashing
#: path the engine does rather than a degenerate one.
WITNESS_CALL_KEYS: tuple[str, ...] = (
    "battle:witness:a-vs-b", "coup:witness:a", "market:witness:oil", "weather:witness",
)

#: The turns it draws them at. More than one, because a stream is keyed on the turn as well,
#: and a witness taken at a single turn is a thinner claim than it looks.
WITNESS_TURNS: tuple[int, ...] = (0, 1, 7, 60)

#: Draws taken per stream. Eight is well past the point where two blake2b-seeded streams could
#: agree by accident, and the whole witness is still microseconds.
WITNESS_DRAWS = 8


def rng_witness(
    world_id: str, seed: int, *, derive_fn: Callable[..., Any] | None = None
) -> str:
    """A digest of the randomness a world is *about to produce*, before anything is played.

    This is the guard that takes the harness's word for nothing. Two arms may agree on every
    configuration field and still be different worlds, because the thing that makes them
    different is one argument to :func:`atlas.sim.rng.derive` -- and the only way to be sure is
    to draw from it. So the witness derives a fixed battery of streams from ``(seed, world_id,
    turn, call_key)`` and hashes what comes out. Identical witnesses mean the arms will see
    identical coins for identical decisions; different witnesses mean they are different worlds
    and every figure that follows compares two universes.

    ``derive_fn`` exists so a test can prove the witness reads the argument it claims to.
    Production callers leave it alone: the default is the engine's own function, imported and
    not reimplemented, so that a change to how streams are derived changes this too.
    """
    derive_it = derive_fn or derive
    digest = blake2b(digest_size=16)
    for turn in WITNESS_TURNS:
        for call_key in WITNESS_CALL_KEYS:
            stream = derive_it(int(seed), str(world_id), turn, call_key)
            digest.update(f"\x1e{turn}\x1f{call_key}".encode())
            for _ in range(WITNESS_DRAWS):
                digest.update(f"{stream.random():.17g}".encode())
    return digest.hexdigest()


# ----------------------------------------------------------------- what an arm actually did


@dataclass(frozen=True, slots=True)
class ArmSnapshot:
    """What one arm **actually ran under**, reported by the harness after the fact.

    Every guard reads this rather than the :class:`PairedRun`, and the distinction is the
    reason the guards are worth anything. A harness can construct a perfectly valid paired run
    and then quietly build its worlds some other way; checking the run against itself would
    pass that. So the harness is asked to state the id and seed it handed the engine, and the
    framework compares *those*. It is the same principle the invariants file states as
    "nothing here takes a model's word for anything", pointed at the harness instead.

    ``sections`` is the pre-intervention fingerprint: section name to digest, taken after any
    declared seeding and **before** the intervention lands. Sectioned rather than one hash,
    because a whole-world digest can only say "these differ" and the finding that matters is
    *which part*.

    ``rng_inputs`` is everything the harness fed to :func:`atlas.sim.rng.derive`. It defaults
    to the id and the seed, which is what the engine reads today; a harness that derives
    streams from anything else lists it here and guard 2 scans that too.
    """

    world_id: str
    seed: int
    sections: Mapping[str, str] = field(default_factory=dict)
    rng_inputs: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""

    def inputs(self) -> dict[str, Any]:
        merged: dict[str, Any] = {"world_id": self.world_id, "seed": self.seed}
        merged.update(dict(self.rng_inputs))
        return merged

    def witness(self) -> str:
        return rng_witness(self.world_id, self.seed)


@dataclass(frozen=True, slots=True)
class SeededDifference:
    """A difference between the arms, before the intervention, that is **on purpose**.

    Sometimes an arm legitimately starts somewhere else: a control that warms one relationship
    so the treatment is not confounded with hostility, a stress planted in one arm only. Guard
    3 would otherwise refuse those runs -- and a guard that legitimate work has to switch off
    is a guard that gets switched off.

    So intended differences are *declared*, by section, with the reason. The declaration is
    then checked in both directions. A section named here is allowed to differ. A section named
    here that turns out **not** to differ is a failure, because it means the seeding the arm
    was built around silently did not apply, and a run whose treatment was never planted is the
    other half of the same instrumentation family.
    """

    section: str
    why: str
    #: The arms this difference is expected between. Empty means all of them.
    between: tuple[str, ...] = ()


# --------------------------------------------------------------------------------- the cost


class CostBasis(StrEnum):
    """Where a claimed cost's numbers came from. The distinction that cost the second night."""

    #: Money that changed hands, read off the record of what was actually paid.
    SETTLED = "settled"
    #: A standing, list or quoted price: what a lane *would have* earned had it been running.
    #: Not a cost. It is the number the embargo's sacrifice was reported at, on lanes that -- in
    #: the regime where governments embargo each other -- were mostly not moving cargo at all.
    LIST = "list"
    #: The harness did not say. Treated exactly as harshly as LIST: an unstated provenance is
    #: not evidence, and defaulting it to "settled" would make the guard decorative.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Receipt:
    """One payment that actually happened: who paid whom, how much, and when."""

    turn: int
    payer: str
    payee: str
    amount: float
    detail: str = ""


#: How far a claim may sit from the receipts behind it before it is a different number. The
#: engine rounds money to three places, so a run over hundreds of receipts accumulates a
#: little; the relative term absorbs that without letting a list-price claim through, which
#: misses by multiples rather than by rounding.
RECONCILE_ABSOLUTE = 0.005
RECONCILE_RELATIVE = 0.001


@dataclass(frozen=True, slots=True)
class CounterfactualCost:
    """What an intervention is claimed to have cost, **and the evidence for the claim**.

    The claim is not believed on its own. It must name its basis, it must reconcile with the
    receipts behind it, and where it priced a set of lanes it must say how many of them were
    moving anything -- because a counterfactual cost is exactly the kind of number that can be
    computed correctly from an assumption nobody checked. Which is what happened: the sacrifice
    was ``volume x standing price`` summed over every lane an embargo stopped, and shadow-
    reading the buyer's ceiling against those lanes found **237 of 237** and **492 of 492**
    already stopped on price. The arithmetic was right and the number was not a cost.

    ``purse_delta`` is the independent corroboration: what the payer's treasury actually did,
    treatment minus control. A sacrifice that appears only in the ledger that records it, and
    not in the purse, is a sacrifice nobody made.
    """

    claimed: float
    basis: CostBasis
    receipts: tuple[Receipt, ...] = ()
    #: How many lanes (routes, contracts, whatever the claim priced) the figure billed for.
    lanes_billed: int = 0
    #: How many of those were actually moving cargo in the window. Must equal ``lanes_billed``:
    #: a lane that shipped nothing cannot have stopped shipping anything.
    lanes_moving: int = 0
    #: Treatment-minus-control treasury for the party said to have paid. ``None`` when the
    #: harness did not pair the purse, which is recorded as a note rather than a pass.
    purse_delta: float | None = None
    detail: str = ""

    @property
    def receipted(self) -> float:
        return round(sum(r.amount for r in self.receipts), 6)


# ------------------------------------------------------------------------------ the verdict


class Guard(StrEnum):
    """The four properties a paired A/B comparison has to have to mean anything."""

    SAME_WORLD = "same-world"
    LABEL_SEALED = "label-sealed"
    PRE_INTERVENTION_IDENTICAL = "pre-intervention-identical"
    SETTLED_COST = "settled-cost"


#: What each guard is for, printed beside a failure so a report says *why* the run is void
#: rather than only that it is.
GUARD_PURPOSE: dict[Guard, str] = {
    Guard.SAME_WORLD:
        "both arms ran one world id and one seed, and would draw identical randomness",
    Guard.LABEL_SEALED:
        "the arm label never reached a value rng.derive reads",
    Guard.PRE_INTERVENTION_IDENTICAL:
        "the arms were identical up to the intervention, bar declared seeding",
    Guard.SETTLED_COST:
        "the counterfactual cost reconciles with receipts for money that moved",
}


@dataclass(slots=True)
class GuardOutcome:
    """One guard's finding. ``problems`` void the run; ``notes`` only weaken it."""

    guard: Guard
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(slots=True)
class PairedValidity:
    """Whether this comparison is entitled to be read as a comparison."""

    study: str
    outcomes: list[GuardOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(o.passed for o in self.outcomes)

    @property
    def problems(self) -> list[str]:
        return [f"[{o.guard.value}] {p}" for o in self.outcomes for p in o.problems]

    @property
    def notes(self) -> list[str]:
        return [f"[{o.guard.value}] {n}" for o in self.outcomes for n in o.notes]

    def failed(self) -> list[Guard]:
        return [o.guard for o in self.outcomes if not o.passed]

    def rendered(self) -> str:
        head = "PAIRED RUN VALID" if self.ok else "PAIRED RUN INVALID"
        lines = [f"{head} -- {self.study}"]
        for outcome in self.outcomes:
            mark = "ok  " if outcome.passed else "FAIL"
            lines.append(f"  {mark} {outcome.guard.value}: {GUARD_PURPOSE[outcome.guard]}")
            if outcome.detail:
                lines.append(f"       {outcome.detail}")
            lines.extend(f"       - {p}" for p in outcome.problems)
            lines.extend(f"       ~ {n}" for n in outcome.notes)
        return "\n".join(lines)


class PairedRunInvalid(AssertionError):
    """A paired comparison that is not one. Raised instead of returning a number."""


# ------------------------------------------------------------------------------- the guards


def _guard_same_world(run: PairedRun, snapshots: Mapping[str, ArmSnapshot]) -> GuardOutcome:
    out = GuardOutcome(Guard.SAME_WORLD)
    missing = [str(a) for a in run.arms if str(a) not in snapshots]
    extra = sorted(set(snapshots) - {str(a) for a in run.arms})
    if missing:
        out.problems.append(
            f"no snapshot for arm(s) {missing}: an arm that did not report what it ran under "
            "cannot be vouched for")
    if extra:
        out.problems.append(f"snapshot(s) {extra} for arm(s) the run does not declare")
    present = [(str(a), snapshots[str(a)]) for a in run.arms if str(a) in snapshots]
    if len(present) < 2:
        out.problems.append("fewer than two arms reported; there is nothing to pair")
        return out

    ids = {name: str(s.world_id) for name, s in present}
    seeds = {name: int(s.seed) for name, s in present}
    if len(set(ids.values())) > 1:
        out.problems.append(
            "the arms ran under different world ids -- "
            + ", ".join(f"{n}={v!r}" for n, v in sorted(ids.items()))
            + ". rng.derive hashes the world id into every stream, so these are different "
              "worlds and the difference between them is the name.")
    if len(set(seeds.values())) > 1:
        out.problems.append(
            "the arms ran under different seeds -- "
            + ", ".join(f"{n}={v}" for n, v in sorted(seeds.items())))
    if set(ids.values()) == {run.world_id} and set(seeds.values()) == {run.seed}:
        out.detail = f"world {run.world_id!r} seed {run.seed}, both arms"
    else:
        out.problems.append(
            f"the arms did not run the key the paired run issued ({run.world_id!r}, seed "
            f"{run.seed}); a run that builds its own worlds is not covered by having "
            "constructed a PairedRun")

    witnesses = {name: s.witness() for name, s in present}
    if len(set(witnesses.values())) > 1:
        out.problems.append(
            "the arms would draw different randomness before either of them is played -- "
            + ", ".join(f"{n}={w[:12]}" for n, w in sorted(witnesses.items()))
            + ". This is measured, not inferred: the streams were derived and drawn from.")
    elif not out.problems:
        out.detail += f", rng witness {next(iter(witnesses.values()))[:12]}"
    return out


def _find_labels(value: Any, labels: Sequence[ArmLabel], path: str) -> list[str]:
    """Every place an arm label, or a piece of one, shows up in something the RNG reads."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.extend(_find_labels(item, labels, f"{path}.{key}"))
        return found
    if isinstance(value, (list, tuple, set, frozenset)):
        ordered = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        for index, item in enumerate(ordered):
            found.extend(_find_labels(item, labels, f"{path}[{index}]"))
        return found
    if _is_tainted(value):
        found.append(
            f"{path} = {str(value)!r} was cut out of an arm label -- the taint survived it, "
            "so this is the leak caught at the point it was written")
        return found
    if isinstance(value, str):
        text = value.lower()
        for label in labels:
            plain = str(label).lower()
            if plain and plain in text:
                found.append(f"{path} = {value!r} contains the arm label {str(label)!r}")
    return found


def _distinguishing_fragment(text: str, label: str, others: Sequence[str]) -> str:
    """The longest piece of ``label`` that is in ``text`` and in none of ``others``.

    Diagnosis, not detection. The detection is :func:`_guard_label_sealed`'s invariance check,
    which needs to recognise nothing; this only answers the question that check provokes --
    *which part of the name got in?* -- and it is worth answering because the real leak was one
    character (``arm[-1]``) and a report that said only "the ids differ" would have left
    somebody to find it by eye. Returns ``""`` when no piece of the label explains the
    difference, which is itself informative: the arm reached the RNG by some other route.
    """
    haystack, needle = text.lower(), str.__str__(str(label)).lower()
    elsewhere = [o.lower() for o in others]
    best = ""
    for start in range(len(needle)):
        for end in range(len(needle), start, -1):
            fragment = needle[start:end]
            if len(fragment) <= len(best):
                break
            if fragment in haystack and not any(fragment in o for o in elsewhere):
                best = fragment
    return best


def _guard_label_sealed(run: PairedRun, snapshots: Mapping[str, ArmSnapshot]) -> GuardOutcome:
    """Nothing the RNG reads may vary with the arm, and no label may appear in any of it.

    **The invariance half is the one that is sound**, and it is deliberately stated without
    reference to the label at all. A guard that looked for the *name* in the world id would
    have passed the run that caused all this: ``f"e{seed}{arm[-1]}{uptake[:2]}"`` puts the
    single character ``"3"`` into the id, and no search for ``"arm3"`` finds it -- nor could
    any search for one-character fragments run without firing on every id in the project. But
    a world id that *moves between the arms* is unmistakable whatever the encoding, and by
    construction the only thing that legitimately differs between arms is the label and the
    flags it selects. So: any value ``rng.derive`` reads that is not identical across every arm
    is the arm reaching the RNG, and is refused on that ground alone.

    This overlaps :attr:`Guard.SAME_WORLD`, which reads the same fields for a different reason
    -- that one asks *are these one world*, this one asks *did the label get into it*. They
    fire together on the naming bug because that bug is both faults at once, and each still
    catches cases the other does not: a run whose arms share a hand-built id that is not the
    key the pairing issued fails guard 1 and passes guard 2; a single-arm id carrying the
    label ``"…-treatment"`` in every arm passes guard 1 and fails guard 2.
    """
    out = GuardOutcome(Guard.LABEL_SEALED)
    for name in sorted(snapshots):
        out.problems.extend(
            _find_labels(snapshots[name].inputs(), run.arms, f"{name}.rng_inputs"))

    present = [(str(a), snapshots[str(a)].inputs()) for a in run.arms if str(a) in snapshots]
    if len(present) >= 2:
        for key in sorted({k for _, inputs in present for k in inputs}):
            values = {name: inputs.get(key) for name, inputs in present}
            if len({repr(v) for v in values.values()}) == 1:
                continue
            shown = ", ".join(f"{n}={v!r}" for n, v in sorted(values.items()))
            fragments = []
            for name, value in sorted(values.items()):
                others = [str(v) for n, v in values.items() if n != name]
                fragment = _distinguishing_fragment(str(value), name, others)
                if fragment:
                    fragments.append(f"{fragment!r} of {name!r} is in {str(value)!r} alone")
            out.problems.append(
                f"rng_inputs.{key} varies with the arm -- {shown}. The arms may differ by the "
                "label and the flags it selects and by nothing else, so a value rng.derive "
                "reads that moves with the arm is the label in the seed by some route"
                + (" (" + "; ".join(fragments) + ")" if fragments else
                   " (no piece of any label explains it, so the route is not the name itself)")
                + ".")

    if not out.problems:
        out.detail = (
            f"{len(run.arms)} label(s) {[str.__str__(str(a)) for a in run.arms]} appear in no "
            "value rng.derive reads, and every such value is identical across the arms")
    return out


def _guard_pre_intervention(
    run: PairedRun,
    snapshots: Mapping[str, ArmSnapshot],
    declared: Sequence[SeededDifference],
) -> GuardOutcome:
    out = GuardOutcome(Guard.PRE_INTERVENTION_IDENTICAL)
    present = [(str(a), snapshots[str(a)]) for a in run.arms if str(a) in snapshots]
    if len(present) < 2:
        out.problems.append("fewer than two arms reported a pre-intervention fingerprint")
        return out
    empty = [name for name, s in present if not s.sections]
    if empty:
        out.problems.append(
            f"arm(s) {empty} reported no pre-intervention fingerprint at all. An unmeasured "
            "baseline is not an identical one, and this guard would otherwise pass by having "
            "nothing to compare.")
        return out

    keysets = {name: frozenset(s.sections) for name, s in present}
    if len(set(keysets.values())) > 1:
        out.problems.append(
            "the arms fingerprinted different sections -- "
            + "; ".join(f"{n}={sorted(k)}" for n, k in sorted(keysets.items())))
        return out
    every_section = next(iter(keysets.values()))

    allowed = {d.section: d for d in declared}
    unknown = sorted(set(allowed) - set(every_section))
    if unknown:
        out.problems.append(
            f"declared seeded difference(s) in section(s) {unknown}, which nothing "
            "fingerprints; a declaration nothing checks is a hole, not an exemption")

    differed: set[str] = set()
    for section in sorted(every_section):
        values = {name: s.sections[section] for name, s in present}
        if len(set(values.values())) == 1:
            continue
        differed.add(section)
        if section in allowed:
            continue
        out.problems.append(
            f"section {section!r} differs between the arms before the intervention -- "
            + ", ".join(f"{n}={v[:12]}" for n, v in sorted(values.items()))
            + ". Nothing has been done to either arm yet, so this is the instrument.")
    for section, difference in sorted(allowed.items()):
        if section in every_section and section not in differed:
            out.problems.append(
                f"section {section!r} was declared to differ ({difference.why}) and is "
                "identical in every arm: the seeding this run is built around did not apply")
    if not out.problems:
        out.detail = (
            f"{len(every_section) - len(differed)} section(s) identical across "
            f"{len(present)} arms"
            + (f", {sorted(differed)} differing as declared" if differed else ""))
    return out


def _guard_settled_cost(costs: Mapping[str, CounterfactualCost] | None) -> GuardOutcome:
    out = GuardOutcome(Guard.SETTLED_COST)
    if not costs:
        out.detail = "no counterfactual cost claimed; nothing to reconcile"
        out.notes.append(
            "this run makes no claim about what the intervention cost. If a report of it "
            "prints one, that number did not come through this guard.")
        return out
    for name in sorted(costs):
        cost = costs[name]
        where = f"{name}: "
        if cost.claimed < 0:
            out.problems.append(
                f"{where}a claimed sacrifice of {cost.claimed:.3f} is negative; a cost is a "
                "magnitude, and a sign error here reads as a windfall")
        if cost.basis is not CostBasis.SETTLED:
            out.problems.append(
                f"{where}the cost is priced at {cost.basis.value}, not at what was paid. A "
                "standing price is what a lane would have earned had it been running, and the "
                "lanes an intervention stops are disproportionately the ones that had already "
                "stopped -- so this prices cargo that was not moving.")
        if cost.claimed > 0 and not cost.receipts:
            out.problems.append(
                f"{where}{cost.claimed:.3f} claimed with no receipts behind it. A cost with "
                "no record of anybody paying it is a price list.")
        elif cost.receipts:
            gap = abs(cost.claimed - cost.receipted)
            tolerance = RECONCILE_ABSOLUTE + RECONCILE_RELATIVE * max(
                abs(cost.claimed), abs(cost.receipted))
            if gap > tolerance:
                out.problems.append(
                    f"{where}the claim of {cost.claimed:.3f} does not reconcile with the "
                    f"{len(cost.receipts)} receipt(s) behind it, which total "
                    f"{cost.receipted:.3f} (out by {gap:.3f}, tolerance {tolerance:.3f}). "
                    "Labelling a figure 'settled' does not settle it.")
        if cost.lanes_billed and cost.lanes_moving < cost.lanes_billed:
            out.problems.append(
                f"{where}the figure bills {cost.lanes_billed} lane(s) and only "
                f"{cost.lanes_moving} were moving cargo. A lane that shipped nothing cannot "
                "have stopped shipping anything.")
        if cost.purse_delta is None:
            out.notes.append(
                f"{where}the purse was not paired. The ledger recording the sacrifice is "
                "marking its own homework; pass purse_delta to corroborate it.")
        elif cost.claimed > 0 and cost.purse_delta > 0:
            out.problems.append(
                f"{where}the claimed sacrifice of {cost.claimed:.3f} left the payer "
                f"{cost.purse_delta:+.3f} better off than the control. A cost that appears "
                "only in its own ledger and not in the purse is not a cost.")
    if not out.problems:
        out.detail = f"{len(costs)} claim(s) reconciled against settled receipts"
    return out


# ------------------------------------------------------------------------------ the harness


def check_paired_run(
    run: PairedRun,
    snapshots: Mapping[str, ArmSnapshot],
    *,
    costs: Mapping[str, CounterfactualCost] | None = None,
    declared: Sequence[SeededDifference] = (),
) -> PairedValidity:
    """Run all four guards over what the arms reported, and return the verdict.

    Returns rather than raises, so a harness that wants to record an invalid run and carry on
    can. A harness that wants the abort -- which is nearly all of them, because the value of a
    guard is that it stops a night being spent -- calls :func:`assert_paired_run`.
    """
    return PairedValidity(
        study=run.study,
        outcomes=[
            _guard_same_world(run, snapshots),
            _guard_label_sealed(run, snapshots),
            _guard_pre_intervention(run, snapshots, declared),
            _guard_settled_cost(costs),
        ],
    )


def assert_paired_run(
    run: PairedRun,
    snapshots: Mapping[str, ArmSnapshot],
    *,
    costs: Mapping[str, CounterfactualCost] | None = None,
    declared: Sequence[SeededDifference] = (),
) -> PairedValidity:
    """Check, and raise :class:`PairedRunInvalid` carrying the whole report if anything failed."""
    validity = check_paired_run(run, snapshots, costs=costs, declared=declared)
    if not validity.ok:
        raise PairedRunInvalid(validity.rendered())
    return validity


def preflight_problems(
    run: PairedRun,
    snapshots: Mapping[str, ArmSnapshot],
    *,
    costs: Mapping[str, CounterfactualCost] | None = None,
    declared: Sequence[SeededDifference] = (),
) -> list[str]:
    """The same findings, as a plain list of strings.

    This is the shape ``preflight.Experiment.check_initial_state`` returns, so an experiment
    already running under a preflight adopts all four guards by returning this from it. The
    preflight then aborts the run as INVALID before the clock starts, with these lines in
    ``ExperimentResult.initial_state_problems`` -- which is the right place for them. A
    comparison whose arms are different worlds is not a null result; it is a broken instrument,
    and keeping those two apart is the distinction the preflight exists for.
    """
    return check_paired_run(run, snapshots, costs=costs, declared=declared).problems


@dataclass(slots=True)
class PairedOutcome:
    """What :func:`run_paired` produced: the verdict first, the numbers second."""

    run: PairedRun
    validity: PairedValidity
    snapshots: dict[str, ArmSnapshot] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, CounterfactualCost] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.validity.ok


def run_paired(  # noqa: PLR0913 - a paired run is identified by all of this
    run: PairedRun,
    *,
    build: Callable[[WorldKey], Any],
    sections: Callable[[Any], Mapping[str, str]],
    play: Callable[[Any, ArmLabel], Any],
    prepare: Callable[[Any, ArmLabel], None] | None = None,
    cost_of: Callable[[Any, ArmLabel], CounterfactualCost | None] | None = None,
    declared: Sequence[SeededDifference] = (),
    raise_on_invalid: bool = True,
) -> PairedOutcome:
    """Build a valid paired run, drive every arm through it, and assert the four properties.

    **The split between the callbacks is itself the guard**, which is why they are separate
    arguments rather than one ``run_arm``:

    ``build(world_key)`` -- takes the key and **not the arm**. An arm-dependent world cannot be
    written here, because the arm is not in scope. This is guard 2 expressed as a function
    signature rather than as a check.

    ``prepare(world, arm)`` -- any deliberate seeding, and the only place an arm may differ
    before the intervention. Whatever it changes must be named in ``declared``.

    ``sections(world)`` -- the pre-intervention fingerprint, taken here: after the seeding and
    before the intervention. Arm-blind, so the fingerprint cannot be computed differently per
    arm. This is guard 3's measurement point, and the harness does not choose it.

    ``play(world, arm)`` -- the intervention and the run. Everything the arms are meant to
    differ by lives in here and nowhere above it.

    ``cost_of(world, arm)`` -- the counterfactual cost claim, with the receipts behind it.

    Each arm is built on a world of its own from the shared key, never on the world the last
    arm left behind: arms that shared a database would make the second arm's baseline the first
    arm's aftermath, which is the same fault as running both arms in one world wearing a hat.
    """
    snapshots: dict[str, ArmSnapshot] = {}
    results: dict[str, Any] = {}
    costs: dict[str, CounterfactualCost] = {}
    for arm in run.arms:
        world = build(run.world_key)
        if prepare is not None:
            prepare(world, arm)
        snapshots[str(arm)] = ArmSnapshot(
            world_id=run.world_id, seed=run.seed, sections=dict(sections(world)))
        results[str(arm)] = play(world, arm)
        if cost_of is not None:
            claim = cost_of(world, arm)
            if claim is not None:
                costs[str(arm)] = claim
    validity = check_paired_run(run, snapshots, costs=costs or None, declared=declared)
    outcome = PairedOutcome(
        run=run, validity=validity, snapshots=snapshots, results=results, costs=costs)
    if raise_on_invalid and not validity.ok:
        raise PairedRunInvalid(validity.rendered())
    return outcome


# ------------------------------------------------------------ fingerprinting, by the section

#: Columns a sectioned fingerprint never hashes, by table. ``world_id`` because it is the
#: *name* of the world rather than anything in it: hashing it would make two arms differ for
#: the one reason this module exists to stop mattering, and would bury a real state divergence
#: under the naming one that guard 1 has already reported. ``created_at`` for the reason
#: ``atlas.state.bootstrap._UNHASHED_COLUMNS`` gives -- it is a fact about the file.
DEFAULT_UNHASHED: dict[str, frozenset[str]] = {
    "*": frozenset({"world_id", "created_at"}),
}


def section_digests(
    conn: sqlite3.Connection,
    world_id: str,
    tables: Iterable[str],
    *,
    unhashed: Mapping[str, frozenset[str]] | None = None,
) -> dict[str, str]:
    """One digest per table, over the rows belonging to this world.

    Sectioned because ``atlas.state.bootstrap.fingerprint`` returns a single hash, and a single
    hash can only say *these two worlds differ*. When the finding is "the arms diverged before
    either was touched", the next question is always *where*, and a whole-world digest cannot
    answer it.

    Columns are sorted by name for the reason ``bootstrap.fingerprint`` sorts them: a column
    added by ``ALTER TABLE`` lands in a different position in a migrated database than in a
    fresh one, and the digest would otherwise depend on how the file got here. Rows are ordered
    by the same column list rather than by a primary key, so this works on any table the caller
    names without knowing its keys.

    The table names are the caller's, not this module's, so no schema knowledge is baked in and
    a harness fingerprints exactly the sections its comparison turns on.
    """
    rules = dict(DEFAULT_UNHASHED if unhashed is None else unhashed)
    always = rules.get("*", frozenset())
    out: dict[str, str] = {}
    for table in tables:
        skip = always | rules.get(table, frozenset())
        columns = sorted(
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
            if str(row["name"]) not in skip
        )
        if not columns:
            out[table] = "no-hashable-columns"
            continue
        selected = ", ".join(columns)
        digest = blake2b(digest_size=16)
        digest.update(f"{table}\x1e{selected}".encode())
        rows = conn.execute(
            f"SELECT {selected} FROM {table} WHERE world_id = ? ORDER BY {selected}",  # noqa: S608
            (world_id,),
        ).fetchall()
        for row in rows:
            digest.update(b"\x1f")
            digest.update("\x1f".join(repr(v) for v in tuple(row)).encode())
        out[table] = digest.hexdigest()
    return out
