"""What a country is actually trying to do, in a form the engine can check.

**Every nation has had goals since the scenarios were written, and none of them were ever
measured.** They are prose -- "deny China the technology that would close the gap", "outlast
Western support for Kyiv rather than defeat it" -- and prose is the right thing to hand a
model, because it carries the *why* that no predicate does. It is also unscoreable: nothing
in the engine can tell whether a country is winning at outlasting somebody.

So this is a second, narrower thing standing beside the prose rather than replacing it: a
small vocabulary of conditions the engine can evaluate against live state, authored per
power, so that "am I getting what I want" has an answer.

**The vocabulary is deliberately small and every kind is a state query.** That is the whole
design constraint, and it is the same one `peace.TERM_KINDS` holds itself to: a goal the
engine cannot evaluate is a sentence in a briefing that means nothing. Six kinds cover the
shapes the shipped scenarios actually express -- survival, growth, restraint, and rivalry --
and a seventh would need a reason better than "it would be nice to say".

**What this is not.** It is not a second victory condition and nothing here decides a game.
`scoring` answers "who is winning"; this answers "is this particular country getting what it
said it wanted", which for most powers on most maps is a different question with a different
answer. A nation can meet every goal it has and never come near a victory condition, and
that is a perfectly good game for it.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

__all__ = [
    "GOAL_KINDS",
    "Goal",
    "GoalProgress",
    "describe",
    "goals_of",
    "progress_of",
]

#: The kinds, and what each one reads. Every entry is a query against live state.
GOAL_KINDS: dict[str, str] = {
    "keep_home": "none of your own country is under somebody else's control",
    "hold_at_least": "you hold at least this many provinces",
    "economy_above": "your economy is at or above this figure",
    "industry_above": "your industry is at or above this figure",
    "stay_out_of_war": "you are not at war with anybody",
    "outweigh": "your score stands at or above the named power's",
}


@dataclass(frozen=True, slots=True)
class Goal:
    """One thing a country is trying to be true, and how to tell whether it is."""

    kind: str
    #: The figure the goal is measured against, where the kind takes one.
    value: float = 0.0
    #: The other power, where the kind names one.
    subject: str = ""
    #: The prose line this was drawn from, kept so a briefing can show the sentence a
    #: government would actually recognise beside the number. **The prose is the point and
    #: the predicate is the instrument**, not the other way round.
    because: str = ""


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """One goal, and how the country is doing on it."""

    goal: Goal
    have: float
    need: float
    met: bool

    @property
    def fraction(self) -> float:
        """How far along, clamped to ``1.0``. Meaningless for a goal with no scale."""
        if self.need <= 0:
            return 1.0 if self.met else 0.0
        return max(0.0, min(1.0, self.have / self.need))


def goals_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> tuple[Goal, ...]:
    """The structured goals this power was authored with. Empty for most nations.

    Read from the scenario rather than from the database: a goal is a fact about who this
    country is, like its character blurb, not state that play moves. A world whose scenario
    authored none -- every era but Modern Day, so far -- gets an empty tuple and every
    caller here renders nothing, which is what those worlds did before this existed.
    """
    from ..scenarios.registry import scenario_for

    row = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)).fetchone()
    if row is None:
        return ()
    try:
        scenario = scenario_for(str(row["preset_id"] or ""))
    except (KeyError, ValueError):
        return ()
    for power in getattr(scenario, "powers", ()) or ():
        if getattr(power, "id", None) == nation_id:
            return tuple(getattr(power, "aims", ()) or ())
    return ()


def _held(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) c FROM region WHERE world_id = ? AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
        (world_id, nation_id)).fetchone()["c"])


def _occupied_home(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) c FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' AND controller_nation_id IS NOT NULL"
        "   AND controller_nation_id <> owner_nation_id",
        (world_id, nation_id)).fetchone()["c"])


def _figure(conn: sqlite3.Connection, world_id: str, nation_id: str, column: str) -> float:
    row = conn.execute(
        f"SELECT {column} AS value FROM nation"  # noqa: S608 - column is from GOAL_KINDS
        "  WHERE world_id = ? AND id = ?", (world_id, nation_id)).fetchone()
    return float(row["value"]) if row else 0.0


def _wars(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    # **Enemies, not rows.** `relation` is directed -- a war is written once from each
    # side -- so counting rows told a government trying to stay out of war that it was in
    # two of them the day it entered one. The same count, fixed the same way, as the
    # routing score's (T11).
    return int(conn.execute(
        "SELECT COUNT(DISTINCT CASE WHEN nation_a = ? THEN nation_b ELSE nation_a END) c"
        "  FROM relation WHERE world_id = ? AND at_war = 1 AND ? IN (nation_a, nation_b)",
        (nation_id, world_id, nation_id)).fetchone()["c"])


def progress_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> tuple[GoalProgress, ...]:
    """Every authored goal for this power, measured against the world as it stands now.

    Read-only and draws nothing from the RNG, so two readings of the same turn agree.
    """
    out: list[GoalProgress] = []
    for goal in goals_of(conn, world_id, nation_id):
        if goal.kind == "keep_home":
            lost = _occupied_home(conn, world_id, nation_id)
            out.append(GoalProgress(goal, float(lost), 0.0, lost == 0))
        elif goal.kind == "hold_at_least":
            have = float(_held(conn, world_id, nation_id))
            out.append(GoalProgress(goal, have, goal.value, have >= goal.value))
        elif goal.kind in ("economy_above", "industry_above"):
            column = "economy" if goal.kind == "economy_above" else "industry"
            have = _figure(conn, world_id, nation_id, column)
            out.append(GoalProgress(goal, have, goal.value, have >= goal.value))
        elif goal.kind == "stay_out_of_war":
            wars = float(_wars(conn, world_id, nation_id))
            out.append(GoalProgress(goal, wars, 0.0, wars == 0))
        elif goal.kind == "outweigh":
            from .scoring import score_of

            mine = score_of(conn, world_id, nation_id)
            theirs = score_of(conn, world_id, goal.subject)
            have = float(mine.total) if mine else 0.0
            need = float(theirs.total) if theirs else 0.0
            out.append(GoalProgress(goal, have, need, have >= need))
    return tuple(out)


def describe(conn: sqlite3.Connection, world_id: str, entry: GoalProgress) -> str:
    """One line: the sentence a government would recognise, and the number under it."""
    mark = "met" if entry.met else "not met"
    goal = entry.goal
    if goal.kind == "keep_home":
        detail = ("no province of yours is occupied" if entry.met
                  else f"{entry.have:.0f} province(s) of yours are occupied")
    elif goal.kind == "stay_out_of_war":
        detail = ("you are at peace" if entry.met
                  else f"you are in {entry.have:.0f} war(s)")
    elif goal.kind == "outweigh":
        named = conn.execute(
            "SELECT name FROM nation WHERE id = ?", (goal.subject,)).fetchone()
        who = str(named["name"]) if named else goal.subject
        detail = f"you stand at {entry.have:.2f} against {who}'s {entry.need:.2f}"
    else:
        have = f"{entry.have:.0f}"
        if not entry.met and have == f"{entry.need:.0f}":
            # **The number has to agree with the verdict beside it.** Economy and industry
            # drift by fractions, so 109.6 against a target of 110 is honestly not met -- and
            # printed whole it read "110 of 110 -- not met". Shown to a tenth, rounded *down*
            # so it can never read as having arrived.
            have = f"{math.floor(entry.have * 10) / 10:.1f}"
        detail = f"{have} of {entry.need:.0f}"
    return f"{goal.because or GOAL_KINDS.get(goal.kind, goal.kind)} -- **{mark}**: {detail}"


def briefing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this country said it wanted, and whether it is getting it.

    Silent for a power its scenario authored no measurable goals for, which is most of them
    and every nation of two of the three eras. A heading over nothing is worse than no
    heading -- and inventing generic goals so that every nation had some would be worse
    still, because a goal nobody chose is not that country's goal.
    """
    entries = progress_of(conn, world_id, nation_id)
    if not entries:
        return []
    met = sum(1 for entry in entries if entry.met)
    lines = [
        f"## What you are trying to achieve ({met} of {len(entries)} met)",
    ]
    lines += [f"- {describe(conn, world_id, entry)}" for entry in entries]
    lines.append(
        "These are your own aims rather than the engine's scoring, and meeting them is not "
        "winning -- a country can have everything it wanted and never come near a victory "
        "condition. They are what your government would call a good decade."
    )
    lines.append("")
    return lines
