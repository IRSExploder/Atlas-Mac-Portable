"""The handful of powers the world revolves around for one player, and what each just did.

Focus Mode has always known this. Five nations get a dedicated call each turn, chosen by a
deterministic ranking that already writes down *why* each one qualified -- and the whole of
that on screen is a progress strip in the left rail reading ``4 / 5``. The player is told
how many actors there are and never who they are, so a world with five named rivals in it
reads as a world with none.

**This is the roster as a cast list.** Each tracked power with its colour, where the two of
you stand, which way that has been moving, whether there is a war or a treaty between you,
the reason Focus itself gave for tracking it, and the last thing it did that you know about.
The point is continuity: the same five faces turn after turn, each carrying what it just
did, so the player recognises them rather than re-reading a list.

**It reads; it does not rank.** Nothing here chooses the roster, and nothing here can. The
roster is ``nation.is_tracked``, written by :func:`atlas.engine.focus.apply_focus` through
the diff pipeline, and this module's only relationship to it is a ``SELECT``.

**Regard is the reader's own row**, ``relation`` where ``nation_a`` is the player -- the
same figure the diplomacy rail, the workshop and the briefing's power roster all show. The
schema stores the two directions separately and they do differ (in the saved Gathering
Storm world they differ on 84 of 48,620 rows, all of them hand-authored openings), so the
choice is a real one: showing *their* row would be telling a government the private opinion
of a foreign cabinet, which no other screen does and which :mod:`atlas.state.visibility`
exists to prevent. What expresses their attitude here instead is the pair of facts that
genuinely are public -- war and treaty -- the reason Focus recorded, and the act.

**The act is gated through the same predicate as everything else.** Candidates come out of
the log by actor and go through :func:`atlas.engine.causality.visible_event_ids` before one
is chosen, so a tracked power's most recent deed is its most recent deed *that this
government was told about*. A power whose whole recent record is covert shows nothing, and
says so, rather than quietly showing the last thing it did in the open six turns ago as
though it were news.

Read-only from end to end. Opening the cast cannot change a save.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..state.visibility import salience_of
from .causality import visible_event_ids
from .dispatch import DECISIONS
from .focus import tracked_nations

__all__ = [
    "Act",
    "Cast",
    "CastMember",
    "cast_for",
]

#: How many turns of movement the trend describes. Three, because one turn is noise -- most
#: pairs of powers do not move at all in a given month -- and a longer window stops being a
#: *trend* and becomes the history of the game. Stated on the panel rather than implied, so
#: the arrow means something specific.
_TREND_SPAN = 3

#: Below this a movement is drift rather than a direction, and the arrow is flat. The same
#: floor :mod:`atlas.engine.dispatch` applies to the same figure, for the same reason: every
#: pair of neighbours nudges every turn.
_TREND_FLOOR = 1.0

#: How far a decision outranks gravity when both are in the same turn. See
#: :data:`atlas.engine.dispatch.DECISIONS`: a battle and a declaration of war both weigh 3,
#: the engine writes the front *after* the decision, and gravity plus recency therefore
#: reports the month a power declared war as the month it flew some sorties.
_DECISION_RANK = {kind: len(DECISIONS) - index for index, kind in enumerate(DECISIONS)}

#: How far back to look for each power's last known act, in events per power. Generous,
#: because the candidates are filtered by entitlement *after* they are fetched, and a power
#: whose recent record is mostly covert would otherwise come back empty while a perfectly
#: readable act sat one row outside the window.
_ACTS_PER_MEMBER = 25

#: Kinds that are not an *act* by anybody. ``genesis`` is the world being written down, and
#: the routine paperwork of a working economy is not what a rival is remembered for -- a
#: cast list whose every entry read "began Workshops and light industry" would be worse than
#: an empty one, because it would look like an answer.
_NOT_AN_ACT = frozenset({"genesis", "construction", "economic_reform"})


@dataclass(frozen=True, slots=True)
class Act:
    """The last thing a power did that the reader knows about."""

    event_id: int
    turn: int
    date: str
    kind: str
    text: str
    #: :func:`atlas.state.visibility.salience_of`, so the panel can mark a grave act without
    #: forming a second opinion about what grave means.
    weight: int


@dataclass(frozen=True, slots=True)
class CastMember:
    """One tracked power, as it stands toward the reader."""

    nation_id: str
    name: str
    colour: str
    #: The turn Focus promoted it, and the reason it recorded. Verbatim: the ranking is
    #: allowed to explain itself, and this is not the place to paraphrase it.
    since_turn: int
    reason: str

    #: The reader's own regard, and how far it has moved. Both are ``0.0`` when there is no
    #: reader -- see :func:`cast_for`.
    score: float
    trend: float
    at_war: bool
    treaty: str

    #: The turn :attr:`trend` describes, and whether that is inside the window. **A figure
    #: that held still this turn is not a figure that has never moved**, which is the
    #: argument `ui.trace` makes about the same class of number: a panel answering "no
    #: change" on four rows in five is a panel nobody reads twice. So a flat window falls
    #: back to the last turn the regard did move, and the row says which turn that was.
    trend_turn: int = 0
    trend_recent: bool = True

    latest: Act | None = None

    @property
    def direction(self) -> int:
        """``+1``, ``-1`` or ``0``. What the arrow points at."""
        if self.trend >= _TREND_FLOOR:
            return 1
        if self.trend <= -_TREND_FLOOR:
            return -1
        return 0

    @property
    def standing(self) -> str:
        """The relationship in a word. The vocabulary the diplomacy rail already uses."""
        if self.at_war:
            return "at war"
        if self.treaty and self.treaty != "none":
            return self.treaty.replace("_", " ")
        if self.score >= 60:
            return "warm"
        if self.score >= 25:
            return "friendly"
        if self.score <= -60:
            return "hostile"
        if self.score <= -25:
            return "cold"
        return "neutral"


@dataclass(frozen=True, slots=True)
class Cast:
    """The roster, as one reader sees it."""

    turn: int
    members: tuple[CastMember, ...]
    #: The reader. Empty when the save is open without a government, in which case there is
    #: nobody for the cast to stand toward and only the acts are shown.
    viewer: str = ""
    #: How many turns of movement :attr:`CastMember.trend` covers.
    span: int = _TREND_SPAN

    @property
    def empty(self) -> bool:
        return not self.members

    def lines(self) -> list[str]:
        """The whole roster as text. What a test reads."""
        return [
            f"{member.name} - {member.standing} ({member.score:+.0f}) - "
            + (member.latest.text if member.latest else "nothing known")
            for member in self.members
        ]


def _relations(
    conn: sqlite3.Connection, viewer: str, ids: list[str]
) -> dict[str, tuple[float, str, bool]]:
    """Where the reader stands with each of them. One query, never one per member."""
    if not viewer or not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        str(row["nation_b"]): (
            float(row["score"]), str(row["treaty_status"]), bool(row["at_war"]))
        for row in conn.execute(
            f"""
            SELECT nation_b, score, treaty_status, at_war
              FROM relation
             WHERE nation_a = ? AND nation_b IN ({marks})
            """,  # noqa: S608 - placeholders only
            (viewer, *ids),
        )
    }


def _trends(
    conn: sqlite3.Connection, world_id: str, viewer: str, ids: list[str], turn: int
) -> dict[str, tuple[float, int, bool]]:
    """``nation -> (movement, the turn it happened, whether that is recent)``.

    Read from the applied diffs rather than by differencing two snapshots, because there is
    no snapshot to difference: the world holds today's number and the log holds how it got
    there. Earliest ``old`` to latest ``new`` within a turn range, so a figure that fell and
    recovered reports the net rather than the sum of the swings.

    **A flat window falls back to the last turn that moved.** Regard moves in steps, not
    continuously -- across fourteen recorded turns of the Modern Day world, Washington's
    view of Moscow moved on three of them -- so a strict three-turn window would draw a flat
    rule beside every power in most turns of most games, and a trend that is almost always
    absent is a trend nobody looks at. The fallback is the shape `ui.trace` already uses for
    a meter that held still, and it carries its turn for the same reason: *unmoved since
    March* and *unmoved ever* must not print the same.

    **Not gated, and that is not an oversight.** The figure itself is on the diplomacy rail
    every turn, so its movement between two turns is arithmetic on something the reader
    already had. What a covert cause hides is the *reason*, and this reports no reason.
    """
    if not viewer or not ids:
        return {}
    pairs = [f"{viewer}|{other}" for other in ids]
    marks = ",".join("?" * len(pairs))
    rows = conn.execute(
        f"""
        SELECT b.turn AS turn, d.target_id AS pair, d.old_json AS before, d.new_json AS after
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn <= ?
           AND d.status = 'applied' AND d.target_table = 'relation'
           AND d.field = 'score' AND d.target_id IN ({marks})
         ORDER BY b.turn, d.batch_id, d.seq
        """,  # noqa: S608 - placeholders only
        (world_id, turn, *pairs),
    ).fetchall()

    #: ``nation -> turn -> [first old, last new]``. Per turn, because the fallback needs one
    #: turn's net and the window needs several turns' -- and building the second from the
    #: first is free while the reverse is not.
    steps: dict[str, dict[int, list[float]]] = {}
    for row in rows:
        other = str(row["pair"]).split("|")[-1]
        try:
            before = float(json.loads(row["before"]) or 0)
            after = float(json.loads(row["after"]) or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        held = steps.setdefault(other, {})
        if int(row["turn"]) in held:
            held[int(row["turn"])][1] = after
        else:
            held[int(row["turn"])] = [before, after]

    floor = max(0, turn - _TREND_SPAN + 1)
    found: dict[str, tuple[float, int, bool]] = {}
    for other, by_turn in steps.items():
        recent = sorted(number for number in by_turn if number >= floor)
        if recent:
            net = by_turn[recent[-1]][1] - by_turn[recent[0]][0]
            if abs(net) >= _TREND_FLOOR:
                found[other] = (net, recent[-1], True)
                continue
        moved = [
            (number, by_turn[number][1] - by_turn[number][0])
            for number in sorted(by_turn)
            if abs(by_turn[number][1] - by_turn[number][0]) >= _TREND_FLOOR
        ]
        if moved:
            last_turn, delta = moved[-1]
            found[other] = (delta, last_turn, last_turn >= floor)
    return found


def _latest_acts(
    conn: sqlite3.Connection, world_id: str, ids: list[str], viewer: str | None
) -> dict[str, Act]:
    """The last thing each of them did that this reader knows about.

    One window query for the whole cast rather than one per power, then the entitlement
    filter, then the choice. **Ranked in Python**, for the reason
    :func:`atlas.state.visibility.known_events` gives: :func:`salience_of` reads an event's
    text as well as its kind, and a CASE expression that had to duplicate that would be a
    second copy of the ranking to keep in step with the first.

    **The newest turn wins, and inside it the editorial order does.** A cast list answers
    *what did they just do*, so the gravest thing a power did six turns ago is not the
    answer -- but within one turn, gravity alone picks the front over the decision that
    caused it, which is the inversion :data:`atlas.engine.dispatch.DECISIONS` was written
    for. Imported rather than restated: a declaration of war must not be the news on one
    screen and the substrate on another.
    """
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    excluded = sorted(_NOT_AN_ACT)
    kinds = ",".join("?" * len(excluded))
    rows = conn.execute(
        f"""
        SELECT id, turn, date, kind, text, actor_nation_id FROM (
            SELECT e.id, e.turn, e.date, e.kind, e.text, e.actor_nation_id,
                   ROW_NUMBER() OVER (PARTITION BY e.actor_nation_id
                                      ORDER BY e.turn DESC, e.id DESC) AS depth
              FROM event e
             WHERE e.world_id = ? AND e.actor_nation_id IN ({marks})
               AND e.kind NOT IN ({kinds}) AND TRIM(e.text) <> ''
        ) WHERE depth <= ?
        """,  # noqa: S608 - placeholders only
        (world_id, *ids, *excluded, _ACTS_PER_MEMBER),
    ).fetchall()
    if not rows:
        return {}

    allowed = visible_event_ids(conn, world_id, {int(r["id"]) for r in rows}, viewer)
    best: dict[str, Act] = {}
    for row in rows:
        event_id = int(row["id"])
        if event_id not in allowed:
            continue
        actor = str(row["actor_nation_id"])
        act = Act(
            event_id=event_id,
            turn=int(row["turn"]),
            date=str(row["date"] or ""),
            kind=str(row["kind"]),
            text=str(row["text"] or "").strip(),
            weight=salience_of({"kind": row["kind"], "text": row["text"]}),
        )
        held = best.get(actor)
        if held is None or _standing_of(act) > _standing_of(held):
            best[actor] = act
    return best


def _standing_of(act: Act) -> tuple[int, int, int, int]:
    """How strongly an act claims a power's row. Higher wins."""
    return (act.turn, _DECISION_RANK.get(act.kind, 0), act.weight, act.event_id)


def cast_for(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    viewer: str | None = None,
    turn: int | None = None,
) -> Cast:
    """The tracked roster, as this reader is entitled to see it.

    ``viewer=None`` is the referee rather than a nation -- whoever holds the save, opening it
    without a government. There is then no relationship for the cast to stand in, so the
    standing columns are empty and the acts are read from the log.
    """
    if turn is None:
        row = conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
        turn = int(row["turn_number"]) if row else 0

    # **You are not a member of your own cast.** The ranking already declines to promote the
    # player, so in a played game this removes nothing -- but a recorded engine-vs-engine
    # world has no player at all, and opening one as a government puts that government on
    # its own roster, standing at zero toward itself.
    tracked = [entry for entry in tracked_nations(conn, world_id)
               if str(entry["id"]) != (viewer or "")]
    if not tracked:
        return Cast(turn=turn, members=(), viewer=viewer or "")

    ids = [str(entry["id"]) for entry in tracked]
    marks = ",".join("?" * len(ids))
    colours = {
        str(row["id"]): str(row["colour"] or "#888888")
        for row in conn.execute(
            f"SELECT id, colour FROM nation WHERE world_id = ? AND id IN ({marks})",  # noqa: S608
            (world_id, *ids),
        )
    }
    standing = _relations(conn, viewer or "", ids)
    trends = _trends(conn, world_id, viewer or "", ids, turn)
    acts = _latest_acts(conn, world_id, ids, viewer)
    nowhere = (0.0, "none", False)
    unmoved = (0.0, 0, True)

    members = [
        CastMember(
            nation_id=nation_id,
            name=str(entry["name"]),
            colour=colours.get(nation_id, "#888888"),
            since_turn=int(entry["since_turn"] or 0),
            reason=str(entry["reason"] or ""),
            score=standing.get(nation_id, nowhere)[0],
            trend=trends.get(nation_id, unmoved)[0],
            trend_turn=trends.get(nation_id, unmoved)[1],
            trend_recent=trends.get(nation_id, unmoved)[2],
            treaty=standing.get(nation_id, nowhere)[1],
            at_war=standing.get(nation_id, nowhere)[2],
            latest=acts.get(nation_id),
        )
        for entry in tracked
        for nation_id in (str(entry["id"]),)
    ]
    # A war outranks everything, then the powers held most strongly either way, then the
    # alphabet. The order the diplomacy rail already puts powers in, so the same five do not
    # appear in two different orders on two screens.
    members.sort(key=lambda member: (not member.at_war, -abs(member.score), member.name))
    return Cast(turn=turn, members=tuple(members), viewer=viewer or "")
