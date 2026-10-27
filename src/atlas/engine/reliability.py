"""How much of a match each commander was actually present for.

**Why this is engine code and not a line in a report.** Every behavioural figure a match
produces -- objectives declared, battles fought, peace sought -- is a count of what a model
did while it was answering. A model that was not asked, or that answered nothing, did not
*decline* to act, and the study's rule since 2026-09-20 is respond-or-abstain: a call that
survives its retries and still returns nothing is a recorded **abstention**, never a
fabricated order. That rule is only worth having if the abstentions can be counted
correctly, and counting them correctly is harder than it looks for two reasons that both
bit a real match on the day the rule was adopted.

**A call is only evidence if its round happened.** ``routing_log`` records the attempt, not
the outcome of the turn it belonged to. When the codex quota ran out mid-match the engine
logged three Eastern calls, aborted the turn, and logged three more when the harness tried
again -- six rows describing *one* outage in *no* completed round. Reported raw, that bloc
looked six times less reliable than it was.

**"Happened" means ``sealed`` or ``partial``, and the first version of this module got that
wrong.** It counted ``sealed`` only. But the turn engine marks a turn ``partial`` whenever
the jump had *any* rejected batch or *any* failed call -- one neutral power's call running
into a quota, one world-simulator diff refused -- and :class:`~atlas.state.models.TurnStatus`
is explicit that such a turn is never to be discarded, because it resolved. The 14-round
chaos redo of 2026-09-21 had two: turn 6 (a neutral Egypt call hit ``quota_exhausted``) and
turn 13. Counting sealed only threw away eighteen fully applied bloc moves, including every
move of the round the assassination fired in. Only ``aborted`` and ``open`` mean the round
did not complete.

**And a bloc's round is judged by the turn it produced, not the turn it is filed under.**
`EveEngine` plays a round at world turn *N* and logs its calls at *N*; the jump that follows
then creates turn *N + 1*, and that row's status is the round's outcome. Calls made *by* the
turn engine during a jump are logged at the turn the jump creates, so for those the row they
are filed under is their own. Bloc nations are played by EVE and everyone else by the turn
engine -- the tracked roster and the blocs have never overlapped, see the note on the
dedupe below -- so the nation says which row to read. Reading row *N* for a bloc call was an
off-by-one that happened to give the right answer on 2026-09-20 and the wrong one on the
21st.

**A replayed turn logs twice, and the first time did not happen either.** ``rewind_to``
truncates this table *past* the target turn, which leaves the rows *at* the target turn in
place -- and a replay then re-runs that same turn and appends its own. Two rows, one round.
The later attempt is the one whose answer shaped the world.

Both exclusions are **named rather than silently applied**: :class:`MatchReliability`
carries the turns it dropped and the calls it superseded, so a report says *this match was
rewound* and *this match lost a turn to a quota outage* instead of quietly showing a
different number than the log.

**The quarantine is offered and not taken.** Gemini's external review argued that any
bloc-match above 15% dropout should be excluded outright, and that a crippled agent
invalidates the match for all three blocs. The owner adopted GPT's respond-or-abstain rule
instead. :func:`quarantine_view` computes Gemini's answer anyway, as an opt-in report, so
the disagreement stays visible in the data rather than being settled by which tool got
written. It is never the default and nothing in the engine acts on it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

__all__ = [
    "DELIVERED_OUTCOMES",
    "BlocReliability",
    "MatchReliability",
    "NationReliability",
    "QuarantineView",
    "measure_reliability",
    "quarantine_view",
]

#: The dropout share above which Gemini's review would discard a bloc-match. Recorded so
#: the number has a source; nothing uses it unless a caller asks for the quarantine view.
GEMINI_QUARANTINE_THRESHOLD = 0.15

#: Outcomes in which **the model was heard from**, whatever became of what it said.
#:
#: **This distinction is the whole of GPT's "failed observation is not refusal to act".**
#: `EveEngine` writes five outcomes and only two of them mean the answer never arrived:
#:
#: * ``ok`` -- answered, and the world changed;
#: * ``silent`` -- answered, and the batch carried nothing to act on. That is a commander
#:   *choosing* to do nothing, which is a decision and a datum;
#: * ``refused`` -- answered, and the validation layer rejected what it proposed. Also a
#:   decision, and one the study actively wants to count;
#: * anything else (``failed``, ``quota_exhausted``, a timeout, a crash) -- **no answer
#:   reached the engine at all**.
#:
#: Folding the middle two into the last would report a model that proposed an illegal move
#: as a model that was absent, and a reliability figure would quietly absorb behaviour it
#: has no business measuring.
DELIVERED_OUTCOMES = frozenset({"ok", "silent", "refused"})

#: Turn statuses under which a round *happened*. ``partial`` is in here on purpose -- see the
#: module docstring for the eighteen bloc moves its absence once threw away.
_HAPPENED = frozenset({"sealed", "partial"})


@dataclass(frozen=True, slots=True)
class NationReliability:
    """One nation's answering record, over the rounds that actually happened."""

    nation_id: str
    #: Calls whose answer reached the engine, whatever became of it.
    answered: int
    #: Calls whose answer never arrived -- a provider failure, not a decision.
    abstained: int
    #: The sealed turns on which this nation was asked and no answer arrived.
    abstained_turns: tuple[int, ...] = ()
    #: Of :attr:`answered`, those that carried nothing to act on. A commander choosing to
    #: do nothing is a decision, and is kept apart from one that was never heard.
    said_nothing: int = 0
    #: Of :attr:`answered`, those the validation layer rejected. Also a decision.
    refused: int = 0

    @property
    def asked(self) -> int:
        return self.answered + self.abstained

    @property
    def dropout_rate(self) -> float:
        return self.abstained / self.asked if self.asked else 0.0

    @property
    def worst_consecutive(self) -> int:
        """The longest unbroken run of silence.

        **Not derivable from the rate, and it is the figure that matters.** Six scattered
        silences leave a commander present in every phase of the war; six consecutive leave
        it absent for a third of it while the front moves. A single average reports these
        as the same match.
        """
        best = run = 0
        previous: int | None = None
        for turn in self.abstained_turns:
            run = run + 1 if previous is not None and turn == previous + 1 else 1
            best = max(best, run)
            previous = turn
        return best


@dataclass(frozen=True, slots=True)
class BlocReliability:
    """A bloc's answering record: its nations' counts, and the worst run among them."""

    nation_ids: tuple[str, ...]
    answered: int
    abstained: int
    said_nothing: int = 0
    refused: int = 0
    #: The longest run of silence any *one* of its nations had -- never a run computed
    #: across the union of them. See :meth:`MatchReliability.for_nations`.
    worst_consecutive: int = 0
    worst_nation: str = ""
    abstained_turns_by_nation: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @property
    def asked(self) -> int:
        return self.answered + self.abstained

    @property
    def dropout_rate(self) -> float:
        return self.abstained / self.asked if self.asked else 0.0


@dataclass(frozen=True, slots=True)
class MatchReliability:
    """What the whole match's call log says, once it is restricted to what happened."""

    by_nation: dict[str, NationReliability] = field(default_factory=dict)
    #: Turns whose round completed. Only these contribute.
    sealed_turns: tuple[int, ...] = ()
    #: Turns the log has calls for whose round did not complete -- aborted by a provider
    #: outage, or still open. **Named, not silently dropped.**
    unsealed_turns: tuple[int, ...] = ()
    #: Calls at a sealed turn that a later attempt at the same turn superseded, which is
    #: the fingerprint of a rewind-and-replay. Also named rather than subtracted.
    superseded_calls: int = 0
    #: Rounds in which every bloc was heard from at least once.
    rounds_all_blocs_answered: int = 0
    #: Rounds counted for the line above.
    rounds_considered: int = 0

    @property
    def was_replayed(self) -> bool:
        return self.superseded_calls > 0

    @property
    def lost_a_round(self) -> bool:
        return bool(self.unsealed_turns)

    def for_nations(self, nation_ids: tuple[str, ...]) -> BlocReliability:
        """One combined record for a bloc, from its members.

        **A bloc has no "run of silence" of its own, and pretending otherwise inflates it.**
        The obvious implementation unions the members' silent turns and measures the run
        across that -- which reports three nations each missing alternate turns as one
        unbroken six-round blackout, when no commander was ever silent twice running. The
        longest run is a *nation's* property; a bloc's is the worst of its nations', and it
        is reported with the nation's name so the figure can be checked.
        """
        members = [self.by_nation[n] for n in nation_ids if n in self.by_nation]
        worst = max(members, key=lambda m: m.worst_consecutive, default=None)
        return BlocReliability(
            nation_ids=tuple(nation_ids),
            answered=sum(m.answered for m in members),
            abstained=sum(m.abstained for m in members),
            said_nothing=sum(m.said_nothing for m in members),
            refused=sum(m.refused for m in members),
            worst_consecutive=worst.worst_consecutive if worst else 0,
            worst_nation=worst.nation_id if worst and worst.abstained else "",
            abstained_turns_by_nation={
                m.nation_id: m.abstained_turns for m in members if m.abstained_turns},
        )

    def describe(self) -> str:
        lines = [
            f"{len(self.sealed_turns)} completed turn(s) with calls counted"
            + (f"; {len(self.unsealed_turns)} not counted "
               f"(turn(s) {list(self.unsealed_turns)} did not complete)"
               if self.unsealed_turns else "")
            + (f"; {self.superseded_calls} call(s) superseded by a replay"
               if self.superseded_calls else "")
        ]
        lines.append(
            f"{self.rounds_all_blocs_answered}/{self.rounds_considered} round(s) had every "
            "bloc answer at least once")
        return "\n".join(lines)


def measure_reliability(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    bloc_of: dict[str, str] | None = None,
    role: str = "tracked_agent",
) -> MatchReliability:
    """Count answers and abstentions over the rounds that actually happened.

    ``bloc_of`` maps nation id to bloc id and is only needed for the
    "every bloc answered" figure; without it that figure is zero.
    """
    try:
        status_of = {
            int(r["number"]): str(r["status"]) for r in conn.execute(
                "SELECT number, status FROM turn WHERE world_id = ?", (world_id,))}
        in_a_bloc = {
            str(r["id"]) for r in conn.execute(
                "SELECT id FROM nation WHERE world_id = ? AND bloc_id IS NOT NULL",
                (world_id,))}
        rows = list(conn.execute(
            "SELECT id, turn, nation_id, outcome FROM routing_log"
            " WHERE world_id = ? AND role = ? AND nation_id IS NOT NULL AND nation_id <> ''"
            " ORDER BY id",
            (world_id, role)))
    except sqlite3.OperationalError:
        # A world written before these tables existed. Nothing to count is not an error.
        return MatchReliability()

    def completed(turn: int, nation_id: str) -> bool:
        """Did the round this call belongs to actually happen? See the module docstring."""
        decided_by = turn + 1 if nation_id in in_a_bloc else turn
        return status_of.get(decided_by) in _HAPPENED

    unsealed = tuple(sorted({
        int(r["turn"]) for r in rows
        if not completed(int(r["turn"]), str(r["nation_id"]))}))

    # **Last attempt per (turn, nation) wins.** Ordered by id, so a later row overwrites an
    # earlier one for the same round -- which is exactly and only what a replay produces,
    # because the retry deliberately logs one row per *call* rather than per attempt.
    #
    # **The assumption, stated because it is checkable and could stop holding.** This takes
    # two rows for one (turn, nation) to mean the round was played twice. `tracked_nations`
    # has no bloc exclusion, so a bloc nation that ever became focus-tracked would be called
    # once by EVE and once by the turn engine in the same turn, under this same role, and
    # the second would silently swallow the first. It does not happen today: across the
    # three matches of 2026-09-20 the tracked roster and the blocs never overlap, and every
    # duplicate pair is a known replay or aborted round. If the rosters ever do overlap, the
    # log itself becomes ambiguous and wants a column to tell the two callers apart.
    latest: dict[tuple[int, str], str] = {}
    superseded = 0
    for row in rows:
        turn = int(row["turn"])
        if not completed(turn, str(row["nation_id"])):
            continue
        key = (turn, str(row["nation_id"]))
        if key in latest:
            superseded += 1
        latest[key] = str(row["outcome"])

    answered: dict[str, int] = {}
    abstained: dict[str, list[int]] = {}
    said_nothing: dict[str, int] = {}
    refused: dict[str, int] = {}
    for (turn, nation_id), outcome in latest.items():
        if outcome in DELIVERED_OUTCOMES:
            answered[nation_id] = answered.get(nation_id, 0) + 1
            if outcome == "silent":
                said_nothing[nation_id] = said_nothing.get(nation_id, 0) + 1
            elif outcome == "refused":
                refused[nation_id] = refused.get(nation_id, 0) + 1
        else:
            abstained.setdefault(nation_id, []).append(turn)

    by_nation = {
        nation_id: NationReliability(
            nation_id=nation_id,
            answered=answered.get(nation_id, 0),
            abstained=len(abstained.get(nation_id, ())),
            abstained_turns=tuple(sorted(abstained.get(nation_id, ()))),
            said_nothing=said_nothing.get(nation_id, 0),
            refused=refused.get(nation_id, 0),
        )
        for nation_id in sorted(set(answered) | set(abstained))
    }

    heard: dict[int, set[str]] = {}
    blocs = set((bloc_of or {}).values())
    for (turn, nation_id), outcome in latest.items():
        if outcome in DELIVERED_OUTCOMES and bloc_of and nation_id in bloc_of:
            heard.setdefault(turn, set()).add(bloc_of[nation_id])
    all_answered = sum(1 for seen in heard.values() if blocs and seen == blocs)

    # **The denominator is the rounds a bloc was asked in**, not every turn with a counted
    # call. The final jump of a match logs the neutral powers' calls at a turn no bloc plays,
    # so counting every turn made the 14-round chaos redo read "14 of 15 rounds had every
    # bloc answer" when every bloc answered in all fourteen rounds there were.
    if bloc_of:
        considered = len({t for t, nation_id in latest if nation_id in bloc_of})
    else:
        considered = len({t for t, _ in latest})
    return MatchReliability(
        by_nation=by_nation,
        sealed_turns=tuple(sorted({t for t, _ in latest})),
        unsealed_turns=unsealed,
        superseded_calls=superseded,
        rounds_all_blocs_answered=all_answered,
        rounds_considered=considered,
    )


@dataclass(frozen=True, slots=True)
class QuarantineView:
    """Gemini's rule, computed but not applied. See the module docstring."""

    threshold: float
    #: Bloc label to that bloc's dropout rate, for every bloc over the threshold.
    over: dict[str, float] = field(default_factory=dict)
    #: Every bloc's rate, so a reader sees the ones that passed as well.
    rates: dict[str, float] = field(default_factory=dict)

    @property
    def match_invalid(self) -> bool:
        """Gemini's stronger claim: one crippled agent spoils the match for all three."""
        return bool(self.over)

    def describe(self) -> str:
        if not self.rates:
            return "no blocs to assess"
        listed = ", ".join(f"{name} {rate:.0%}" for name, rate in sorted(self.rates.items()))
        if not self.over:
            return f"no bloc over {self.threshold:.0%} ({listed})"
        return (
            f"over {self.threshold:.0%}: "
            + ", ".join(f"{name} {rate:.0%}" for name, rate in sorted(self.over.items()))
            + f" -- Gemini's rule would discard this match for every bloc ({listed})"
        )


def quarantine_view(
    reliability: MatchReliability,
    blocs: dict[str, tuple[str, ...]],
    *,
    threshold: float = GEMINI_QUARANTINE_THRESHOLD,
) -> QuarantineView:
    """Compute the dissenting reviewer's verdict without adopting it.

    ``blocs`` maps a label to that bloc's nation ids. Nothing in the engine calls this; it
    exists so a match report can show both reviewers' readings side by side and let the
    reader see how much the choice of rule changes the answer.
    """
    rates = {
        label: reliability.for_nations(nations).dropout_rate
        for label, nations in blocs.items()
    }
    return QuarantineView(
        threshold=threshold,
        over={label: rate for label, rate in rates.items() if rate > threshold},
        rates=rates,
    )
