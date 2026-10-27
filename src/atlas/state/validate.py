"""The validation layer: the engine's veto over what the models propose.

Rules run **after** the batch has been applied inside a savepoint, against the real
post-apply database. That ordering is the important design choice here.

The obvious alternative -- evaluate rules against a pending overlay before touching the
database -- collapses under its own weight. Rules need arbitrary SQL: adjacency joins,
unit counts, treasury sums, "does this nation have a casus belli against that one". An
overlay that answers those without applying them means reimplementing a query planner in
Python, or writing every rule twice. Applying first and rolling back if a rule fails
gets joint effects for free: a batch that raises three units and pays for them once is
caught, because by the time the treasury rule runs, all three purchases are visible.

The proposed rows are committed *before* the savepoint opens. If they were written
inside it, rolling back a cheating batch would erase the very evidence of the cheating.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

# Pure arithmetic about time, with no imports of its own -- so reaching for it from
# `state` does not drag the engine into the validation layer. (`state.visibility`
# reaches into `engine.tainted` on the same grounds.)
from ..engine.pacing import span_of
from .diff import DiffBatch
from .models import DIFFICULTY_PROFILES, Difficulty, DifficultyProfile

__all__ = [
    "Rule",
    "RuleContext",
    "Violation",
    "ValidationResult",
    "all_rules",
    "register",
    "validate",
]


#: Rules that may sever *without limit* -- a batch may lose any number of diffs to these
#: and still stand. Deliberately a single entry, and the reason is specific to it rather
#: than general: a stale compare-and-swap says one write was reasoned about under
#: conditions that no longer hold, and **nothing whatever about the other diffs**, each of
#: which carries its own citation and its own prior. Ten stale priors are ten independent
#: accidents, not evidence that the batch misunderstood anything.
#:
#: Every other per-diff rule severs too, but only up to :data:`SEVER_MINORITY` -- see
#: :attr:`Violation.severable` for why the two are treated differently.
_UNLIMITED_SEVERABLE = frozenset({"CORE-004"})


#: How much of a batch may be severed before the batch itself is the problem.
#:
#: **A violation that names a diff condemns that diff. A violation that names none
#: condemns the batch.** That is what ``diff_seq`` has always meant, and the apply layer
#: now simply believes it. What the old whitelist was protecting against is real, though,
#: and survives as this bound: a batch where *most* of the writes are refused is a batch
#: that misunderstood the contract, and landing the minority that happened to pass is
#: worse than landing none of it -- the model was not doing what it thought it was doing.
#:
#: A strict minority, so a batch must be more right than wrong to stand. Measured against
#: the case this exists for -- a model that proposed 24 changes and had 3 refused -- there
#: is a great deal of room between "three mistakes" and "half of it was wrong".
SEVER_MINORITY = 0.5


@dataclass(frozen=True, slots=True)
class Violation:
    """One rule refusing one diff. ``reason`` is shown to the player verbatim."""

    rule_id: str
    reason: str
    #: ``None`` means the violation condemns the whole batch rather than one diff.
    diff_seq: int | None = None

    @property
    def severable(self) -> bool:
        """Whether dropping this one diff is enough, or the whole batch must go.

        **A violation that names a diff condemns that diff; one that names none condemns
        the batch.** That is what ``diff_seq`` has meant since it was added, and the apply
        layer now believes it rather than consulting a second, narrower list.

        The narrower list was there for a real reason: a batch that reaches for an
        engine-only column or cites an event it cannot see may have misunderstood the
        contract, and applying the rest of such a batch is applying a half-understood
        change. But that argument is about *how much* of a batch is refused, not about
        which rule caught it -- and as a per-rule veto it was throwing away 21 correct
        changes because 3 were wrong. It survives as :data:`SEVER_MINORITY`, enforced in
        the apply layer, which is where the size of a batch is actually known.

        The rules already draw the line this needs. Of the twenty-seven places a violation
        is raised, sixteen name a diff and eleven do not -- and the eleven are exactly the
        joint ones: units conjured from nothing, an economy moved past its cap in
        aggregate, a mutual reference broken, the Focus Mode ceiling. Those condemn the
        batch because the violation is a property of the combination rather than of any
        one write in it.
        """
        return self.diff_seq is not None

    @property
    def unlimited(self) -> bool:
        """Whether this violation may sever past the minority bound. See
        :data:`_UNLIMITED_SEVERABLE`."""
        return self.severable and self.rule_id in _UNLIMITED_SEVERABLE


@dataclass(slots=True)
class RuleContext:
    """Everything a rule may look at."""

    conn: sqlite3.Connection
    batch: DiffBatch
    profile: DifficultyProfile
    #: How long the turn this batch belongs to covers. Rules that bound *rates* need
    #: it: a cap of "20% per turn" means nothing until you know whether the turn is a
    #: week or a year. Defaults to the reference month, so a caller that does not care
    #: about duration -- which is most of them, and every test -- gets what it always
    #: got. See :attr:`span`.
    days: int = 30

    @property
    def world_id(self) -> str:
        return self.batch.world_id

    @property
    def turn(self) -> int:
        return self.batch.turn

    @property
    def span(self) -> float:
        """The turn's length in reference months. ``1.0`` for the default turn."""
        return span_of(self.days)

    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable = ()):
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row is not None else None


class Rule(Protocol):
    id: str
    description: str

    def __call__(self, ctx: RuleContext) -> Iterable[Violation]: ...


_REGISTRY: dict[str, Rule] = {}


def register(rule_id: str, description: str) -> Callable[[Callable], Rule]:
    """Decorator registering a validation rule.

    Each rule lives in its own function with a stable id so it can be tested in
    isolation and cited by id in a rejection the player reads.
    """

    def decorate(fn: Callable) -> Rule:
        if rule_id in _REGISTRY:
            raise ValueError(f"duplicate rule id {rule_id!r}")
        fn.id = rule_id  # type: ignore[attr-defined]
        fn.description = description  # type: ignore[attr-defined]
        _REGISTRY[rule_id] = fn  # type: ignore[assignment]
        return fn  # type: ignore[return-value]

    return decorate


def all_rules() -> tuple[Rule, ...]:
    # Import for side effects: each module registers its rules on import.
    from . import rules  # noqa: F401

    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    violations: tuple[Violation, ...] = ()
    #: Rule ids that ran, so a coverage test can assert none were silently skipped.
    ran: tuple[str, ...] = field(default_factory=tuple)

    def reason(self) -> str:
        if not self.violations:
            return ""
        return "; ".join(f"[{v.rule_id}] {v.reason}" for v in self.violations)


def validate(
    conn: sqlite3.Connection,
    batch: DiffBatch,
    *,
    difficulty: Difficulty = Difficulty.NORMAL,
    days: int = 30,
) -> ValidationResult:
    """Run every registered rule against the post-apply state.

    A rule that raises is treated as a violation rather than propagating. A buggy rule
    should fail closed -- refusing a legal change is recoverable and visible, whereas
    letting an illegal one through corrupts the world silently.
    """
    ctx = RuleContext(
        conn=conn, batch=batch, profile=DIFFICULTY_PROFILES[difficulty], days=days
    )
    violations: list[Violation] = []
    ran: list[str] = []

    for rule in all_rules():
        ran.append(rule.id)
        try:
            violations.extend(rule(ctx))
        except Exception as exc:  # noqa: BLE001 - fail closed, deliberately
            violations.append(
                Violation(
                    rule_id=rule.id,
                    reason=f"rule raised {type(exc).__name__}: {exc}",
                )
            )

    return ValidationResult(ok=not violations, violations=tuple(violations), ran=tuple(ran))
