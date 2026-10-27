"""Behavioural statistics from EVE matches.

The question this answers is **not** "which model is best at this game". It is "who plays
which kind of nation" -- does one provider reach for alliances early, does another escalate
faster, does a third keep its word. That is a description of tendency, not a ranking of
skill, and the difference matters because the numbers here will look like a leaderboard
whether or not they deserve to.

So the honesty framing is built into the data structure rather than left to the UI. Every
report carries its sample size and a caveat, and :meth:`ProviderProfile.confidence` refuses
to dress up three matches as a finding. A single run tells you almost nothing; the value is
in repeated runs from identical starting conditions, where the seeded RNG means divergence
comes from the models rather than from the dice. :mod:`atlas.engine.eve_series` runs those.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field

__all__ = ["MatchStats", "ProviderProfile", "collect_match_stats", "summarise"]

#: Below this many matches, differences between providers are noise.
_MEANINGFUL_SAMPLE = 10

#: A turn no batch is ever stamped with, for the counts that must find nothing because no
#: match has started.
_NEVER = 1 << 62


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """One provider's observed tendencies across the matches recorded so far."""

    provider: str
    matches: int
    nations_played: int
    #: Turns from the start of the match to the first alliance this provider formed,
    #: averaged over the matches in which it formed one. ``None`` if never.
    first_alliance_turn: float | None
    alliances_formed: int
    treaties_broken: int
    wars_declared: int
    peaces_signed: int
    regions_gained: int
    regions_lost: int
    nations_surviving: int
    #: Proposals refused by the validation layer, as a share of all proposals made.
    refusal_rate: float
    #: **Who played, as distinct from which seat.** Filled by :func:`collect_match_stats`
    #: and used by :func:`summarise` to fold matches by provider and model rather than by
    #: the display label, which names the *bloc* -- so a series that rotates providers
    #: through the seats would otherwise never fold a provider's matches together.
    provider_key: str = ""
    model: str = ""
    #: The seat (bloc name) played in one match, or every seat played, with counts, once
    #: merged. A provider that only ever played one seat is confounded with that seat's
    #: starting position, and :func:`summarise` says so.
    seats: tuple[tuple[str, int], ...] = ()
    #: The raw counts behind :attr:`refusal_rate`, so a merge can weight by proposals
    #: rather than by nations.
    proposals: int = 0
    refusals: int = 0
    #: Wars declared per nation played, one figure per match, so a merged profile can show
    #: how far its matches disagree rather than only their mean.
    escalation_by_match: tuple[float, ...] = ()

    @property
    def escalation_rate(self) -> float:
        """Wars declared per nation played. Higher means quicker to reach for force."""
        return self.wars_declared / self.nations_played if self.nations_played else 0.0

    @property
    def promise_keeping(self) -> float:
        """Share of agreements not later broken. ``1.0`` means nothing was reneged on."""
        total = self.alliances_formed + self.treaties_broken
        return 1.0 - (self.treaties_broken / total) if total else 1.0

    @property
    def survival_rate(self) -> float:
        return self.nations_surviving / self.nations_played if self.nations_played else 0.0

    @property
    def confidence(self) -> str:
        """How much weight these numbers can bear. Stated, not implied."""
        if self.matches < 3:
            return "anecdote"
        if self.matches < _MEANINGFUL_SAMPLE:
            return "suggestive"
        return "indicative"

    @property
    def escalation_spread(self) -> tuple[float, float] | None:
        """Lowest and highest per-match escalation, once there is more than one match."""
        if len(self.escalation_by_match) < 2:
            return None
        return min(self.escalation_by_match), max(self.escalation_by_match)

    def describe(self) -> str:
        spread = self.escalation_spread
        return (
            f"{self.provider:<12} {self.matches:>3} match(es)  "
            f"escalation {self.escalation_rate:.2f}/nation"
            + (f" (per match {spread[0]:.2f}-{spread[1]:.2f})" if spread else "")
            + f"  kept {self.promise_keeping:.0%}  "
            f"survived {self.survival_rate:.0%}  "
            f"refused {self.refusal_rate:.0%}  [{self.confidence}]"
        )


@dataclass(slots=True)
class MatchStats:
    world_id: str
    turns: int
    profiles: list[ProviderProfile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def caveat(self) -> str:
        """Shown wherever these numbers are. Not optional, and not in small print."""
        return (
            "These describe behavioural tendency on a small sample, not skill. "
            "One match is an anecdote; differences only become meaningful across many runs "
            "from identical starting conditions. A provider that escalates more is not "
            "worse -- it plays differently."
        )

    def describe(self) -> str:
        lines = [f"EVE statistics for {self.world_id} after {self.turns} turn(s)", ""]
        lines.extend(profile.describe() for profile in self.profiles)
        lines += ["", self.caveat]
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


#: The clause that keeps a scenario's seeded history out of a match's behaviour. See
#: :func:`_match_start`. Takes the start turn twice.
_IN_MATCH = "(turn > ? OR (turn = ? AND created_by_batch IS NOT NULL))"


def _match_start(conn: sqlite3.Connection, world_id: str) -> int | None:
    """The turn the first EVE round was played in, or ``None`` if none has been.

    **A match's record is what happened after it began.** A curated era seeds its history
    into the same `event` table the match writes to: cold-war opens with the Soviet Union
    and North Vietnam each having declared a war and China having broken an alliance, all
    at turn 0. Counted as they were, those gave the Eastern and Third Force blocs an
    escalation rate of 0.33 and the Third Force a kept-word of 0% *before any model had
    been asked anything* -- in every cold-war match, whoever played.

    The turn alone cannot separate them, because round 0 is also turn 0. What can: a
    seeded event is written outside any batch, and everything the blocs do in their round
    comes through one (``created_by_batch``). The world's own answer is played in the jump
    that follows, at the next turn, so everything after the start turn is the match's.
    The start is the first round's own record, which :class:`atlas.engine.eve.EveEngine`
    writes as an ``arbiter`` row and nothing else does.
    """
    row = conn.execute(
        "SELECT MIN(turn) AS t FROM routing_log"
        " WHERE world_id = ? AND role = 'arbiter' AND provider = 'engine'", (world_id,)
    ).fetchone()
    return None if row is None or row["t"] is None else int(row["t"])


def _count_events(conn: sqlite3.Connection, world_id: str, kinds: tuple[str, ...],
                  nations: tuple[str, ...], start: int | None) -> int:
    if not nations or start is None:
        return 0
    placeholders = ",".join("?" * len(nations))
    kind_slots = ",".join("?" * len(kinds))
    return conn.execute(
        f"""
        SELECT COUNT(*) FROM event
         WHERE world_id = ? AND kind IN ({kind_slots})
           AND actor_nation_id IN ({placeholders})
           AND {_IN_MATCH}
        """,  # noqa: S608 - placeholders only, values are parameterised
        (world_id, *kinds, *nations, start, start),
    ).fetchone()[0]


def collect_match_stats(conn: sqlite3.Connection, world_id: str) -> MatchStats:
    """Read one world's behavioural record.

    Everything comes from the event and diff tables, which already record who did what and
    why. Nothing is instrumented specially for statistics, so the numbers cannot drift
    away from what actually happened in the game -- but only what happened *in the match*
    is read: see :func:`_match_start`.
    """
    world = conn.execute(
        "SELECT turn_number, mode FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    stats = MatchStats(world_id=world_id, turns=int(world["turn_number"]) if world else 0)

    if world and world["mode"] != "eve":
        stats.notes.append("this world is not an EVE match; figures describe a single-player game")

    blocs = conn.execute(
        "SELECT id, name, provider, model_pin FROM bloc WHERE world_id = ? ORDER BY id",
        (world_id,),
    ).fetchall()
    if not blocs:
        stats.notes.append("no blocs are assigned, so there is nothing to compare")
        return stats
    start = _match_start(conn, world_id)
    if start is None:
        stats.notes.append(
            "no EVE round has been played yet; the world's seeded history is not behaviour, "
            "so every count below is zero")
    elif start > 0:
        # **A match begun on a world already in play cannot be cut exactly.** The jump
        # that closed the previous turn stamps its batches and events with *this* turn,
        # through batches, just as round 1 does, so nothing on the rows separates them.
        # An exact cut needs the batch high-water mark stored when the blocs are assigned,
        # which is a schema change; until then the report says what it could not separate.
        stats.notes.append(
            f"this match began on a world already at turn {start}: the world's own turn "
            f"that ended there is stamped {start} too and is counted with the match")
    batches_from = _NEVER if start is None else start

    for bloc in blocs:
        nations = tuple(
            row["id"] for row in conn.execute(
                "SELECT id FROM nation WHERE bloc_id = ?", (bloc["id"],)
            )
        )
        alive = conn.execute(
            "SELECT COUNT(*) FROM nation WHERE bloc_id = ? AND is_alive = 1", (bloc["id"],)
        ).fetchone()[0]

        first_alliance = conn.execute(
            f"""
            SELECT MIN(turn) AS t FROM event
             WHERE world_id = ? AND kind = 'alliance_formed'
               AND actor_nation_id IN ({",".join("?" * len(nations)) or "NULL"})
               AND {_IN_MATCH}
            """,  # noqa: S608
            (world_id, *nations, start, start),
        ).fetchone() if nations and start is not None else None

        # **Only rounds that happened.** A bloc proposes before the turn resolves, so a
        # turn that aborts -- a provider outage mid-match is the way it happens -- leaves
        # its batches behind in a round that never counted. The chaos match of 2026-09-20
        # carries twelve tracked-agent batches at a turn that never sealed, about a 7%
        # overcount of that match's proposals, feeding `refusal_rate` in every series
        # summary built from it.
        #
        # **Judged by the turn the round produced, and `partial` counts.** The first
        # version tested the batch's *own* turn for `sealed`, which was wrong twice over,
        # exactly as `atlas.engine.reliability` was: EVE plays at turn N and the jump that
        # follows creates N+1, whose status is the round's outcome; and a turn is `partial`
        # whenever a jump had any refused batch or failed call, which `TurnStatus` says
        # never to discard. Every batch counted here is a bloc's, so N+1 always applies.
        sealed_only = (
            " AND EXISTS (SELECT 1 FROM turn t WHERE t.world_id = b.world_id"
            "              AND t.number = b.turn + 1"
            "              AND t.status IN ('sealed', 'partial'))"
        )
        proposed = conn.execute(
            """
            SELECT COUNT(*) FROM diff_batch b
             WHERE b.world_id = ? AND b.turn >= ? AND b.proposer_nation_id IN (
                   SELECT id FROM nation WHERE bloc_id = ?)
            """ + sealed_only,  # noqa: S608 - the fragment is a literal above
            (world_id, batches_from, bloc["id"]),
        ).fetchone()[0]
        refused = conn.execute(
            """
            SELECT COUNT(*) FROM diff_batch b
             WHERE b.world_id = ? AND b.status = 'rejected' AND b.turn >= ?
               AND b.proposer_nation_id IN (SELECT id FROM nation WHERE bloc_id = ?)
            """ + sealed_only,  # noqa: S608 - the fragment is a literal above
            (world_id, batches_from, bloc["id"]),
        ).fetchone()[0]

        gained = conn.execute(
            """
            SELECT COUNT(*) FROM diff d
              JOIN diff_batch b ON b.id = d.batch_id
             WHERE b.world_id = ? AND d.status = 'applied' AND b.turn >= ?
               AND d.target_table = 'region' AND d.field = 'owner_nation_id'
               AND d.new_json IN (SELECT '"' || id || '"' FROM nation WHERE bloc_id = ?)
            """,
            (world_id, batches_from, bloc["id"]),
        ).fetchone()[0]
        lost = conn.execute(
            """
            SELECT COUNT(*) FROM diff d
              JOIN diff_batch b ON b.id = d.batch_id
             WHERE b.world_id = ? AND d.status = 'applied' AND b.turn >= ?
               AND d.target_table = 'region' AND d.field = 'owner_nation_id'
               AND d.old_json IN (SELECT '"' || id || '"' FROM nation WHERE bloc_id = ?)
            """,
            (world_id, batches_from, bloc["id"]),
        ).fetchone()[0]

        wars = _count_events(conn, world_id, ("war_declared",), nations, start)
        stats.profiles.append(
            ProviderProfile(
                provider=f"{bloc['name']} ({bloc['provider']})",
                matches=1,
                nations_played=len(nations),
                # From the match's first turn, so matches that begin at different turns
                # average to something meaningful.
                first_alliance_turn=(
                    float(first_alliance["t"]) - float(start)
                    if first_alliance and first_alliance["t"] is not None and start is not None
                    else None
                ),
                alliances_formed=_count_events(
                    conn, world_id, ("alliance_formed",), nations, start),
                treaties_broken=_count_events(
                    conn, world_id, ("treaty_broken", "commitment_broken", "alliance_broken"),
                    nations, start,
                ),
                wars_declared=wars,
                peaces_signed=_count_events(
                    conn, world_id, ("peace_signed", "ceasefire"), nations, start),
                regions_gained=gained,
                regions_lost=lost,
                nations_surviving=int(alive),
                refusal_rate=(refused / proposed) if proposed else 0.0,
                provider_key=str(bloc["provider"]),
                model=str(bloc["model_pin"]),
                seats=((str(bloc["name"]), 1),),
                proposals=int(proposed),
                refusals=int(refused),
                escalation_by_match=((wars / len(nations)) if nations else 0.0,),
            )
        )

    return stats


def summarise(runs: list[MatchStats]) -> MatchStats:
    """Fold several matches into one profile per provider.

    Repeated runs from identical starting conditions are the only way these numbers mean
    anything, because a single match is dominated by which opening the world happened to
    take.

    **Folded by who played, not by the seat.** A profile from :func:`collect_match_stats`
    names its bloc for display, and folding on that name keyed the *seat*: a series that
    rotates providers through the blocs -- which is the only way to separate a provider's
    tendency from the position it was dealt -- came back as one profile per seat and
    provider, never per provider. The key is the provider and model where the profile
    records them, and the display label otherwise, so a hand-built profile still folds the
    way it always did.

    **Refusals are weighted by proposals.** A rate merged by nations played treats a match
    where a bloc proposed twice like one where it proposed forty times.
    """
    if not runs:
        return MatchStats(world_id="(none)", turns=0)

    merged = MatchStats(world_id=f"{len(runs)} match(es)", turns=sum(r.turns for r in runs))
    by_provider: dict[tuple[str, str] | str, list[ProviderProfile]] = {}
    for run in runs:
        for profile in run.profiles:
            key = ((profile.provider_key, profile.model) if profile.provider_key
                   else profile.provider)
            by_provider.setdefault(key, []).append(profile)

    for key, profiles in sorted(by_provider.items(), key=lambda item: str(item[0])):
        label = f"{key[1]} ({key[0]})" if isinstance(key, tuple) else key
        alliance_turns = [p.first_alliance_turn for p in profiles if p.first_alliance_turn is not None]
        played = sum(p.nations_played for p in profiles)
        proposals = sum(p.proposals for p in profiles)
        refusals = sum(p.refusals for p in profiles)
        if proposals:
            refusal_rate = refusals / proposals
        else:
            # Profiles that do not carry their counts (built by hand): the old weighting,
            # which is the best those can support.
            weight = sum(p.refusal_rate * p.nations_played for p in profiles)
            refusal_rate = (weight / played) if played else 0.0
        seats: Counter[str] = Counter()
        for profile in profiles:
            for seat, count in profile.seats:
                seats[seat] += count
        merged.profiles.append(
            ProviderProfile(
                provider=label,
                matches=len(profiles),
                nations_played=played,
                first_alliance_turn=(sum(alliance_turns) / len(alliance_turns)) if alliance_turns else None,
                alliances_formed=sum(p.alliances_formed for p in profiles),
                treaties_broken=sum(p.treaties_broken for p in profiles),
                wars_declared=sum(p.wars_declared for p in profiles),
                peaces_signed=sum(p.peaces_signed for p in profiles),
                regions_gained=sum(p.regions_gained for p in profiles),
                regions_lost=sum(p.regions_lost for p in profiles),
                nations_surviving=sum(p.nations_surviving for p in profiles),
                refusal_rate=refusal_rate,
                provider_key=profiles[0].provider_key,
                model=profiles[0].model,
                seats=tuple(sorted(seats.items())),
                proposals=proposals,
                refusals=refusals,
                escalation_by_match=tuple(
                    rate for p in profiles for rate in p.escalation_by_match),
            )
        )

    if len(runs) < _MEANINGFUL_SAMPLE:
        merged.notes.append(
            f"{len(runs)} match(es) recorded; {_MEANINGFUL_SAMPLE} or more before these "
            "differences should be read as anything but noise"
        )
    # **A provider that sat in one seat is measured together with that seat.** Unless every
    # provider played every seat equally often, a difference between two providers may be
    # the difference between the Western Bloc's opening and the Third Force's.
    every_seat = {seat for p in merged.profiles for seat, _ in p.seats}
    unbalanced = [
        p.provider for p in merged.profiles
        if p.seats and (len(p.seats) < len(every_seat) or len({n for _, n in p.seats}) > 1)
    ]
    if unbalanced and len(every_seat) > 1:
        merged.notes.append(
            "seats are not balanced for " + ", ".join(unbalanced) + ": each provider should "
            "play every bloc equally often, or its figures are confounded with the position "
            "it was dealt"
        )
    return merged
