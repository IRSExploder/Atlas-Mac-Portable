"""What a match will cost before it is played, and noticing when a provider has run dry.

**The estimate is structural, not historical.** A round of an EVE match makes a predictable
set of calls: one per bloc nation on its bloc's pin, one for the referee, one per tracked
neutral on the tracked pin, and the gambit channel's fixed take. Only two terms are not
fixed by the board -- how many Security Council sessions fire, which depends on what the
powers do, and how many calls need a retry -- and both are stated as rates with the
measurement they came from, so the estimate says which of its parts are guesses.

**Checked against the mock, not trusted.** `tests/test_what_a_match_will_cost.py` plays the
sandbox on a counting mock and holds the estimate to the count. On the kitchen-sink mock of
2026-09-21 (8 rounds) the structural terms matched the counted calls exactly: 24 bloc calls
per family, 8 referee calls, 35 neutral calls, 14 gambits.

**Exhaustion is read from `routing_log`, where every call's outcome already lands.** The
harness asks after each round whether any provider answered ``quota_exhausted`` and pauses
the match cleanly rather than playing on with a bloc that cannot answer -- which is how a
match silently becomes a two-bloc match.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.models import Role

__all__ = [
    "ORGANISATION_RATE",
    "RETRY_ALLOWANCE",
    "Estimate",
    "estimate_match",
    "exhausted_providers",
]

#: Council sessions per round. **Measured, not derived**: 5 in the 8-round kitchen-sink mock
#: (0.625). Sessions fire on triggers the powers create, so a quieter match has fewer.
ORGANISATION_RATE = 0.625

#: Extra calls for retries, as a share. The attempts layer retries transient failures once;
#: the chaos redo and the kitchen-sink match ran at a few percent. Five percent, stated.
RETRY_ALLOWANCE = 0.05


@dataclass(frozen=True, slots=True)
class Estimate:
    rounds: int
    #: Expected calls per provider key over the whole match, retries included.
    by_provider: dict[str, float] = field(default_factory=dict)
    #: The same total split by role, before retries, for reading where it goes.
    by_role: dict[str, float] = field(default_factory=dict)
    #: One line per term, saying what it is and whether it is fixed or a rate.
    lines: tuple[str, ...] = ()

    def total(self) -> float:
        return sum(self.by_provider.values())

    def describe(self) -> str:
        head = [f"estimated calls over {self.rounds} round(s), retries included:"]
        head += [f"  {k:<12} {v:6.1f}" for k, v in sorted(self.by_provider.items())]
        return "\n".join(head + ["terms:"] + [f"  {line}" for line in self.lines])


def _living_candidates(conn: sqlite3.Connection, world_id: str) -> int:
    """Powers Focus could track: alive, not the player, not playing in a bloc."""
    return int(conn.execute(
        "SELECT COUNT(*) FROM nation WHERE world_id = ? AND is_alive = 1 AND is_player = 0"
        "   AND bloc_id IS NULL", (world_id,)).fetchone()[0])


def _tracked_now(conn: sqlite3.Connection, world_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM nation WHERE world_id = ? AND is_tracked = 1"
        "   AND bloc_id IS NULL", (world_id,)).fetchone()[0])


def estimate_match(  # noqa: PLR0913 - a match's cost is named by all of these
    conn: sqlite3.Connection,
    world_id: str,
    *,
    rounds: int,
    pins: dict[Role, tuple[str, str]],
    eve: bool = True,
    bot_roles: frozenset[str] = frozenset(),
    bot_excluded: frozenset[str] = frozenset(),
    study_subjects: dict[str, str] | None = None,
    gambits_per_turn: int | None = None,
    organisation_rate: float = ORGANISATION_RATE,
    retry_allowance: float = RETRY_ALLOWANCE,
) -> Estimate:
    """Calls a match of ``rounds`` rounds will make, by provider, before any are made.

    ``bot_roles`` takes the same scopes as `atlas.engine.bots` -- a botted scope costs
    nothing -- so the estimate is also the way to price switching bots on.
    """
    from .focus import FOCUS_CAP
    from .gambit import MAX_GAMBITS_PER_TURN

    take = MAX_GAMBITS_PER_TURN if gambits_per_turn is None else gambits_per_turn
    by_provider: dict[str, float] = {}
    by_role: dict[str, float] = {}
    lines: list[str] = []

    def add(role: str, provider: str, calls: float, note: str) -> None:
        if calls <= 0:
            return
        by_role[role] = by_role.get(role, 0.0) + calls
        by_provider[provider] = by_provider.get(provider, 0.0) + calls
        lines.append(f"{role} on {provider}: {calls:.1f} -- {note}")

    if eve:
        from .eve import load_blocs

        for bloc in load_blocs(conn, world_id):
            add("bloc", bloc.provider_key, float(len(bloc.nations) * rounds),
                f"fixed: {len(bloc.nations)} nation(s) of {bloc.name} x {rounds}")

    add(Role.WORLD_SIM.value, pins[Role.WORLD_SIM][0], float(rounds),
        "fixed: one referee call a round")

    # Study subjects are asked every turn on their own pins (`TurnEngine.study_subjects`).
    # One Focus also tracks is still asked once, so this can overcount by the overlap.
    for nation, provider in sorted((study_subjects or {}).items()):
        add("study_subject", provider, float(rounds),
            f"fixed: {nation} every round on its own pin")

    # Focus fills to its cap from the second jump; the first asks whoever is tracked now.
    steady = min(FOCUS_CAP, _living_candidates(conn, world_id))
    first = _tracked_now(conn, world_id)
    neutrals = float(first + steady * max(0, rounds - 1)) if rounds else 0.0
    if "tracked" in bot_roles:
        # Excluded nations stay model-played when Focus tracks them. Whether it will is not
        # known before the match, so this counts them as tracked from the second jump --
        # an UPPER BOUND, said so.
        kept = min(steady, len(bot_excluded))
        kept_calls = float(kept * max(0, rounds - 1))
        lines.append(f"tracked_agent: botted (would be {neutrals:.0f})")
        add(Role.TRACKED_AGENT.value, pins[Role.TRACKED_AGENT][0], kept_calls,
            f"UPPER BOUND: {kept} excluded nation(s) still model-played if tracked")
    else:
        add(Role.TRACKED_AGENT.value, pins[Role.TRACKED_AGENT][0], neutrals,
            f"fixed: {first} now, then Focus's cap of {steady} a round")

    # The gambit pool is the tracked roster, so it is empty on the first jump too.
    gambits = float(min(take, steady) * max(0, rounds - 1) + min(take, first))
    if "gambit" in bot_roles:
        lines.append(f"gambit: botted (would be {gambits:.0f})")
        # A study subject's gambit is never a bot's, and a subject Focus tracks is in the
        # gambit pool -- so up to one gambit a subject a turn stays on the model. Whether
        # Focus tracks a subject is not known in advance: an UPPER BOUND.
        kept = float(min(take, len(study_subjects or {})) * max(0, rounds - 1))
        add(Role.GAMBIT.value, pins[Role.GAMBIT][0], kept,
            "UPPER BOUND: gambits of study subjects Focus may track")
    else:
        add(Role.GAMBIT.value, pins[Role.GAMBIT][0], gambits,
            f"fixed: {take} a turn once anybody is tracked")

    sessions = organisation_rate * rounds
    if "organisation" in bot_roles:
        lines.append(f"organisation: 0 -- botted (would be {sessions:.1f})")
    else:
        add(Role.ORGANISATION.value, pins[Role.ORGANISATION][0], sessions,
            f"RATE: {organisation_rate}/round, measured on one mock -- event-driven")

    with_retries = {k: round(v * (1.0 + retry_allowance), 1) for k, v in by_provider.items()}
    lines.append(f"retries: +{retry_allowance:.0%} on every provider -- RATE, stated")
    return Estimate(rounds=rounds, by_provider=with_retries, by_role=by_role,
                    lines=tuple(lines))


def exhausted_providers(conn: sqlite3.Connection, world_id: str, *, since_turn: int) -> set[str]:
    """Every provider that answered ``quota_exhausted`` at or after ``since_turn``."""
    try:
        return {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT provider FROM routing_log WHERE world_id = ? AND turn >= ?"
            "   AND outcome = 'quota_exhausted'", (world_id, since_turn))}
    except sqlite3.OperationalError:
        return set()
