"""Choosing which model handles which call. In Python, never by asking a model.

Two reasons this is code rather than a prompt. A model asked to rate its own task's
difficulty has an obvious incentive to escalate, and the answer would cost a call to
obtain. More importantly, routing must be inspectable: the player can read exactly why a
jump went to the expensive tier, and every decision is logged so a silent misroute shows up
afterwards.

Escalation is by *stakes*, not by preference. Opus is available for the moments that
deserve it -- an ultimatum, a war ending, an alliance forming -- and is opt-in, because the
Max pool it draws from is shared with other work.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from enum import StrEnum

from ..state.models import Role

__all__ = [
    "Breaker",
    "Band",
    "RoutingDecision",
    "Router",
    "diplomacy_stakes",
    "jump_complexity",
]


class Band(StrEnum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    role: Role
    provider_key: str
    model: str
    band: Band
    score: float
    #: Human-readable reasons, shown in the routing log so a choice can be understood.
    factors: tuple[str, ...] = ()

    def describe(self) -> str:
        return (
            f"{self.role.value} -> {self.provider_key}/{self.model} "
            f"[{self.band.value}, score {self.score:.2f}] {'; '.join(self.factors)}"
        )


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Editable in Settings. Defaults chosen to keep ordinary turns cheap."""

    jump_mid: float = 0.35
    jump_high: float = 0.70
    diplomacy_high: float = 0.60
    #: Opus stays off until the player turns it on; the pool is shared with other work.
    allow_opus: bool = False


def jump_complexity(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    days_advanced: int,
    action_count: int,
) -> tuple[float, tuple[str, ...]]:
    """Score how hard a time jump will be to simulate. Range roughly 0..1."""
    factors: list[str] = []
    score = 0.0

    # A five-year leap has vastly more to resolve than a one-week one.
    span = min(1.0, days_advanced / 365.0)
    score += span * 0.30
    factors.append(f"span {days_advanced}d (+{span * 0.30:.2f})")

    actions = min(1.0, action_count / 8.0)
    score += actions * 0.15
    if action_count:
        factors.append(f"{action_count} orders (+{actions * 0.15:.2f})")

    # **Pairs, not rows.** `relation` is directed -- a war is written once from each side --
    # so counting rows scored every war twice and logged double the wars there were.
    wars = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT MIN(nation_a, nation_b), MAX(nation_a, nation_b)"
        "  FROM relation WHERE world_id = ? AND at_war = 1)", (world_id,)
    ).fetchone()[0]
    war_weight = min(1.0, wars / 6.0)
    score += war_weight * 0.25
    if wars:
        factors.append(f"{wars} active wars (+{war_weight * 0.25:.2f})")

    pending = conn.execute(
        "SELECT COUNT(*) FROM commitment WHERE world_id = ? AND status = 'pending'",
        (world_id,),
    ).fetchone()[0]
    pending_weight = min(1.0, pending / 5.0)
    score += pending_weight * 0.15
    if pending:
        factors.append(f"{pending} pending commitments (+{pending_weight * 0.15:.2f})")

    tracked = conn.execute(
        "SELECT COUNT(*) FROM nation WHERE world_id = ? AND is_tracked = 1", (world_id,)
    ).fetchone()[0]
    tracked_weight = min(1.0, tracked / 5.0)
    score += tracked_weight * 0.15
    if tracked:
        factors.append(f"{tracked} tracked nations (+{tracked_weight * 0.15:.2f})")

    return min(1.0, score), tuple(factors)


def diplomacy_stakes(
    conn: sqlite3.Connection,
    *,
    nation_id: str,
    counterpart_id: str,
    message: str,
    ultimatum: bool = False,
    treaty_on_table: bool = False,
) -> tuple[float, tuple[str, ...]]:
    """Score how consequential a diplomatic exchange is."""
    factors: list[str] = []
    score = 0.0

    row = conn.execute(
        "SELECT score, treaty_status, at_war FROM relation WHERE nation_a = ? AND nation_b = ?",
        (nation_id, counterpart_id),
    ).fetchone()

    if row and row["at_war"]:
        score += 0.35
        factors.append("at war (+0.35)")

    if ultimatum:
        score += 0.30
        factors.append("ultimatum (+0.30)")

    if treaty_on_table:
        score += 0.25
        factors.append("treaty on the table (+0.25)")

    if row:
        # Relations near a threshold are where a conversation actually changes something.
        volatility = 1.0 - min(1.0, abs(row["score"]) / 60.0)
        score += volatility * 0.15
        factors.append(f"relation volatility (+{volatility * 0.15:.2f})")

    length = min(1.0, len(message) / 1200.0)
    score += length * 0.10
    if length > 0.2:
        factors.append(f"long message (+{length * 0.10:.2f})")

    return min(1.0, score), tuple(factors)


@dataclass(slots=True)
class Router:
    """Maps a score onto a concrete provider and model."""

    pins: dict[Role, tuple[str, str]]
    thresholds: Thresholds = Thresholds()
    #: Per-role, per-band overrides. Populated from Settings.
    bands: dict[tuple[Role, Band], tuple[str, str]] | None = None

    def _band_for(self, role: Role, score: float) -> Band:
        if role is Role.WORLD_SIM:
            if score >= self.thresholds.jump_high:
                return Band.HIGH
            if score >= self.thresholds.jump_mid:
                return Band.MID
            return Band.LOW
        if role is Role.DIPLOMACY:
            return Band.HIGH if score >= self.thresholds.diplomacy_high else Band.LOW
        return Band.LOW

    def route(self, role: Role, score: float, factors: tuple[str, ...] = ()) -> RoutingDecision:
        band = self._band_for(role, score)
        provider_key, model = self.pins[role]

        override = (self.bands or {}).get((role, band))
        if override is not None:
            provider_key, model = override

        notes = list(factors)
        if role is Role.DIPLOMACY and band is Band.HIGH:
            if self.thresholds.allow_opus:
                model = "claude-opus-5"
                notes.append("high stakes -> Opus (enabled in Settings)")
            else:
                notes.append("high stakes, but Opus escalation is off in Settings")

        return RoutingDecision(
            role=role,
            provider_key=provider_key,
            model=model,
            band=band,
            score=score,
            factors=tuple(notes),
        )


def log_decision(
    conn: sqlite3.Connection,
    world_id: str,
    turn: int,
    decision: RoutingDecision,
    *,
    served_model: str | None = None,
    audit_verdict: str | None = None,
    duration_ms: int | None = None,
    outcome: str = "",
    nation_id: str = "",
) -> None:
    """Record the choice so the player can audit it after the fact.

    ``nation_id`` is whose government the call was for, empty where it was not about one --
    `world_sim` speaks for the world, an organisation session for a body, the arbiter for
    nobody. In an EVE match it is what turns "a Claude model answered" into "the Claude
    model was playing the Soviet Union", which is the question the match exists to ask.
    """
    from datetime import UTC, datetime

    conn.execute(
        """
        INSERT INTO routing_log (world_id, turn, role, provider, requested_model,
                                 served_model, audit_verdict, complexity, band,
                                 duration_ms, outcome, nation_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            world_id,
            turn,
            decision.role.value,
            decision.provider_key,
            decision.model,
            served_model,
            audit_verdict,
            decision.score,
            decision.band.value,
            duration_ms,
            outcome,
            nation_id,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )


@dataclass(slots=True)
class Breaker:
    """Consecutive failures on one role, and whether to stop asking.

    **A breaker rather than a per-call switch, and the difference is the whole point.**
    agy's characteristic failure is *episodic*: exit 0, ``"status":"SUCCESS"``, no answer,
    for a window of minutes, and then it is the fastest provider on the table again. A
    per-call fallback answers each of those by quietly using somebody else, which is
    indistinguishable from the provider working -- so the window never appears in a report
    and the pin can never be judged. A breaker makes the window a fact: it takes `threshold`
    consecutive failures to trip, it says so once, and it stays tripped for the session
    rather than flapping back on the next lucky call.

    Consecutive, not cumulative: one flake an hour is a provider being normal, and three in
    a row is a provider being unavailable. `record` resets on any success for that reason.
    """

    threshold: int = 3
    failures: int = 0
    tripped: bool = False

    def record(self, ok: bool) -> bool:
        """Note one call's outcome. Returns True on the call that trips it."""
        if ok:
            self.failures = 0
            return False
        self.failures += 1
        if self.tripped or self.failures < self.threshold:
            return False
        self.tripped = True
        return True

    def route(
        self, primary: RoutingDecision, fallback: tuple[str, str]
    ) -> RoutingDecision:
        """The decision to use, given what has happened so far."""
        if not self.tripped:
            return primary
        key, model = fallback
        return replace(primary, provider_key=key, model=model)
