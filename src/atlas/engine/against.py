"""What can I actually do about *that*.

A player who has just found a province or a power in the search box has one question next,
and it is not "what actions exist" -- it is "which of them apply *here*, and can I do them
right now". Both halves are already known: `capabilities.CAPABILITIES` says what each act
needs and what kind of thing it names, `capabilities.Capability.available` says whether this
nation can order it at all, and `gambit._requirements_met` is the real gate for the
undertakings.

So this asks those, rather than describing them again. **Nothing is offered that the engine
would refuse**, which is the same discipline `alternatives` runs on, and it is what makes
this a shortcut rather than a menu of hopes.

**Deliberately not intent-to-plan.** Nothing here reads a sentence, infers a goal, or
composes a sequence: it filters a fixed catalogue by the target's kind and asks each
candidate whether it is available. The form it hands back is prefilled with the target and
nothing else. Turning this into "describe what you want and I will plan it" is a different
feature with a design behind it, and it is held.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["Doable", "what_can_i_do_about"]


@dataclass(frozen=True, slots=True)
class Doable:
    """One thing this nation could order against this target, ready to be written."""

    action_type: str
    explain: str
    #: What it costs, in the catalogue's own words. Empty means nothing directly.
    cost: str = ""
    #: The action a workbench would prefill: the type and the target, and nothing invented.
    prefill: dict[str, str] | None = None
    #: True for the undertakings, which are rolled for and can fail.
    is_gamble: bool = False
    #: How the government's standing policy inclines toward this act: positive for what it
    #: serves, negative for what it counsels against, zero when policy is silent. Ordering
    #: only -- nothing is ever withheld for it. See `engine.directives`.
    policy: float = 0.0
    #: Fields the catalogue says this act needs that naming the target does not supply --
    #: a message still needs its message. Empty means the prefill is a whole order, and a
    #: caller may append it to the pending orders as it stands.
    still_needs: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """True when nothing further is needed: the prefill is already an orderable act."""
        return not self.still_needs


def _kind_of(conn: sqlite3.Connection, world_id: str, target: str) -> str:
    """``nation``, ``region`` or ``""`` -- asked of the world, not guessed from the text."""
    if conn.execute("SELECT 1 FROM nation WHERE world_id = ? AND id = ?",
                    (world_id, target)).fetchone():
        return "nation"
    if conn.execute("SELECT 1 FROM region WHERE world_id = ? AND id = ?",
                    (world_id, target)).fetchone():
        return "region"
    return ""


def what_can_i_do_about(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, target: str,
) -> list[Doable]:
    """Every act this nation could order against this target, right now.

    Ordered by the government's own standing policy first and by the catalogue second --
    what an army does, then what a state does, then what a treasury does, which is the order
    a player thinks in and the order the catalogue was already arranged in.

    **The policy only reorders.** Nothing is withheld because a directive counsels against
    it: a player whose standing policy is to avoid a continental war may still declare one,
    and it will be further down the list with a reason beside it. A list that hid a legal
    act would be a policy governing its own government.
    """
    from .capabilities import CAPABILITIES
    from .directives import active, favour
    from .gambit import KINDS, _requirements_met

    kind = _kind_of(conn, world_id, target)
    if not kind or target == nation_id:
        return []

    policy = active(conn)
    found: list[Doable] = []
    for capability in CAPABILITIES:
        if kind not in capability.targets:
            continue
        # **The catalogue's own availability test, asked about this target.** It used to
        # take a nation and nothing else, so two acts whose legality is about a *pair* --
        # a declaration against a standing enemy, an offer of peace to somebody you are not
        # fighting -- were handled by a private table right here, each naming the engine
        # rule it stood in for. That table is gone: `Capability.available` takes the target
        # now, and the two rules are ordinary predicates in the catalogue where every other
        # fact about an act already lives.
        if capability.available is not None and not capability.available(
                conn, world_id, nation_id, target):
            continue
        # The undertakings are listed one by one below, each through its own gate. The
        # catalogue's single `gambit` entry is the parser's way of naming them and would
        # show up here as a row saying "attempt something, by name" above the names.
        if kind == "nation" and capability.action_type == "gambit":
            continue
        field = "target_nation" if kind == "nation" else "target_region"
        prefill = {"type": capability.action_type, field: target}
        found.append(Doable(
            action_type=capability.action_type,
            explain=capability.explain,
            cost=capability.cost,
            prefill=prefill,
            # **What the catalogue already says it needs.** `Capability.required` is the
            # parser's own list; naming a target answers some of it and never all of it,
            # and a caller that appended a half-written order would get it refused by the
            # schema. So the gap is reported rather than guessed at -- filling it in is the
            # player's sentence to write, not this module's to invent.
            still_needs=tuple(need for need in capability.required if need not in prefill),
            policy=favour(policy, capability.action_type, target)))

    if kind == "nation":
        # And the undertakings, asked of the real gate rather than of a second list. A
        # kind that would be refused for this target is simply not offered.
        for gambit in KINDS:
            aim = "forces" if gambit.id == "air_strike" else ""
            if _requirements_met(conn, world_id, gambit, nation_id, target, None,
                                 aim) is not None:
                continue
            found.append(Doable(
                action_type=gambit.id, explain=gambit.blurb,
                cost="a stake, charged win or lose",
                # The order the engine actually takes: `gambit`, named by kind. The kind is
                # the identity here, so `action_type` carries it and the prefill is whole.
                prefill={"type": "gambit", "gambit_kind": gambit.id,
                         "target_nation": target},
                is_gamble=True,
                policy=favour(policy, gambit.id, target)))

    # **Stable**, so the catalogue's own order survives inside each band of policy weight.
    # Python's sort is stable by construction, which is the whole implementation of "policy
    # first, catalogue second".
    found.sort(key=lambda doable: -doable.policy)
    return found
