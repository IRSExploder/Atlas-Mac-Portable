"""Folding old turns into summaries, so a long game stays possible.

Around turn fifteen the raw history stops fitting in a briefing, and by turn fifty
feeding it verbatim would crowd out the present. Consolidation is therefore not an
optimisation -- it is what makes turn 200 reachable at all.

**Digests are built per audience, not once for the world.** A single summary of "what
happened" would have to be written from the referee's omniscient view, and handing that to
a nation leaks every secret it summarises. Compressing information does not declassify it.
So each nation gets a digest of what *it* witnessed, and the referee gets its own.

**Every audience gets a row; only the ones somebody reads get a model call.** This is the
distinction the first version missed, and it was expensive. Consolidation looped over every
*living* nation and paid for a summarising call for each -- 243 model calls per five-turn
chunk on a Modern Day map, 486 on the turn that clears the initial backlog. Measured
against metered API prices that came to roughly $107 per hundred turns, against $18-38 for
the actual gameplay: the compression step cost several times the game it existed to make
possible.

Nobody read 236 of those. A digest is consumed by :func:`digest_for`, and the only caller
of that is :func:`atlas.engine.dispatch.dispatch_for` -- the turn's newspaper, opened by the
player for the player's own scope, or with no scope when spectating. **No model briefing
reads a digest.** The ``digest=`` parameter on every view builder in
:mod:`atlas.engine.views` defaults to empty and nothing passes it, so a tracked nation's
digest, which used to get a summarising call on the belief that its briefing opened it, was
paid for and never read by anyone.

So the split is by *reader*, and the readers are the two the newspaper can be opened for:
the referee and the player. Everybody else -- tracked nations included -- gets
:func:`mechanical_summary`, which costs nothing and is the same fallback a failed call
already used. **A row is still written for every nation**, which matters more than it looks
-- the player's nation can change, and a briefing that starts reading digests would find a
hole where the first thirty turns should be. What it finds instead is the mechanical
summary: worse prose, complete history. Feeding digests to the briefings is a prompt change
and a cassette re-record, and when it lands :func:`readable_audiences` is the one place that
has to learn about the new readers.

**A digest covers every turn it claims.** The stretch is fetched whole, not capped: a
five-turn chunk on a busy map holds well over any fixed window, and the window used to keep
only the newest 400 events of it, so the row said ``turns 10-14`` over events from 12-14.
The *model's* prompt is still bounded, by :data:`PROMPT_EVENTS`, but the bound is shared out
across the turns of the stretch rather than spent newest-first, and the mechanical summary
counts everything.

**Summarising is a model call, but a cheap one.** It is bounded prose over material the
engine has already selected, so it routes at the lowest tier. A digest that fails is not
fatal: the engine falls back to a mechanical summary, which reads worse and works.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..providers.base import ModelProvider, ProviderRequest, ProviderResult
from ..state import referee, visibility
from ..state.models import Role
from ..state.visibility import salience_of
from .untrusted import wrap_untrusted

#: How a summarising call is actually made. The turn engine passes its own ``_invoke`` so
#: digests are retried and counted like every other model call in a turn.
InvokeFn = Callable[[ModelProvider, ProviderRequest], Awaitable[ProviderResult]]

__all__ = [
    "DEFAULT_CHUNK",
    "DEFAULT_START_TURN",
    "PROMPT_EVENTS",
    "DigestSettings",
    "InvokeFn",
    "consolidate",
    "readable_audiences",
    "digest_for",
    "mechanical_summary",
]

#: Turn at which consolidation begins. Before this, raw history is small enough to feed.
DEFAULT_START_TURN = 15
#: How many turns are folded into one digest.
DEFAULT_CHUNK = 5
#: The most events one summarising prompt lists. Shared across the turns of the stretch by
#: :func:`_spread`, so a loud turn cannot crowd a quiet one out of its own digest.
PROMPT_EVENTS = 400


@dataclass(frozen=True, slots=True)
class DigestSettings:
    start_turn: int = DEFAULT_START_TURN
    chunk: int = DEFAULT_CHUNK
    #: How many recent turns always stay raw, however old the world is.
    keep_raw: int = 3


#: Module-level singleton, so it is not constructed on every call signature evaluation.
DEFAULT_SETTINGS = DigestSettings()


def readable_audiences(conn: sqlite3.Connection, world_id: str) -> set[str | None]:
    """Whose digest anything will actually open. ``None`` is the referee.

    Derived from the one caller of :func:`digest_for`, which is the turn's newspaper
    (:func:`atlas.engine.dispatch.dispatch_for`): the main window opens it scoped to the
    player, or unscoped when spectating, which reads the referee's. So at most two entries on
    any map. **Tracked nations are not readers**: no briefing is passed a digest, and paying
    for prose about their history bought nothing -- up to five calls a chunk. If briefings
    start reading digests, this is where their audiences join.

    Read fresh each chunk rather than cached, because the player's nation is a setting and
    the right answer is *who can open it now*.
    """
    rows = conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 AND is_player = 1",
        (world_id,),
    ).fetchall()
    return {None, *(str(row["id"]) for row in rows)}


def mechanical_summary(events: list[dict]) -> str:
    """A summary with no model involved.

    Used when the summarising call fails, and as the baseline the model's version has to
    beat. Reads poorly; never lies.
    """
    if not events:
        return "Nothing of note was recorded."

    by_kind: dict[str, int] = {}
    actors: dict[str, int] = {}
    for event in events:
        by_kind[event["kind"]] = by_kind.get(event["kind"], 0) + 1
        if event.get("actor"):
            actors[event["actor"]] = actors.get(event["actor"], 0) + 1

    busiest = sorted(actors.items(), key=lambda item: -item[1])[:4]
    kinds = sorted(by_kind.items(), key=lambda item: -item[1])[:6]

    parts = [
        f"{len(events)} recorded event(s) between turns "
        f"{events[0]['turn']} and {events[-1]['turn']}."
    ]
    if kinds:
        parts.append("Mostly " + ", ".join(f"{kind} ({count})" for kind, count in kinds) + ".")
    if busiest:
        parts.append("Most active: " + ", ".join(f"{actor} ({count})" for actor, count in busiest) + ".")
    return " ".join(parts)


def _spread(events: list[dict], cap: int) -> list[dict]:
    """At most ``cap`` of ``events``, shared fairly across the turns they fall in.

    Water-filling: the quietest turn takes all it has or an even share of what is left,
    whichever is smaller, and what it does not use passes on to the louder ones. So every
    turn of the stretch that recorded anything is represented, and a turn is only thinned
    when it alone would take more than its share. Within a turn the gravest are kept, by
    the salience the newspaper and the briefings rank with, newest first among equals -- so
    a treaty survives a turn of finished workshops. Order is preserved.
    """
    if len(events) <= cap:
        return events
    by_turn: dict[int, list[int]] = {}
    for index, event in enumerate(events):
        by_turn.setdefault(int(event["turn"]), []).append(index)
    kept: set[int] = set()
    remaining = cap
    ordered = sorted(by_turn.values(), key=len)
    for position, indices in enumerate(ordered):
        take = min(len(indices), remaining // (len(ordered) - position))
        gravest = sorted(indices, key=lambda i: (-salience_of(events[i]), -i))
        kept.update(gravest[:take])
        remaining -= take
    return [event for index, event in enumerate(events) if index in kept]


async def _summarise(
    events: list[dict],
    *,
    audience: str,
    providers: dict[str, ModelProvider],
    router,
    invoke: InvokeFn | None = None,
) -> str:
    """Ask a model for readable prose. ``invoke`` is how the call is made.

    Defaulting to ``provider.invoke`` is what made this path invisible: every other model
    call in the engine goes through ``TurnEngine._invoke``, which is where retries and the
    turn's call count live, and this one went around it. A turn that made 486 calls
    reported eight. The turn engine now passes its own invoker in, so the count is honest
    and a rate-limited digest is retried like anything else.

    **The record is fenced.** Event text is largely model-written narrative, and this
    prompt used to paste it in bare, so a nation's speech could address the summariser
    directly. It goes through :func:`atlas.engine.untrusted.wrap_untrusted` like every other
    quoted text. No tape records a digest call -- consolidation starts at turn
    :data:`DEFAULT_START_TURN` and the active tape is twelve turns -- so this reaches no
    recorded prompt.

    ``events`` is the whole stretch; the prompt lists at most :data:`PROMPT_EVENTS` of it,
    spread across its turns, and says how many it left out. The mechanical fallback counts
    all of them.
    """
    if not events:
        return "Nothing of note was recorded."

    listed = _spread(events, PROMPT_EVENTS)
    record = [f"- turn {e['turn']}: {e['text']}" for e in listed]
    if len(listed) < len(events):
        record.append(f"- ({len(events) - len(listed)} further event(s) in these turns are "
                      "not listed)")
    lines = [
        f"Summarise this stretch of history for {audience}.",
        "",
        "Write three or four sentences of continuous prose. Keep what still matters: who "
        "fell out with whom, what was agreed, what changed hands. Drop detail that has "
        "since been overtaken. Do not invent anything that is not below.",
        "",
        wrap_untrusted("\n".join(record), source="the record of these turns",
                       kind="recorded history"),
    ]

    decision = router.route(Role.PARSER, 0.0)
    provider = providers.get(decision.provider_key)
    if provider is None or not provider.available():
        return mechanical_summary(events)

    request = ProviderRequest(
        role=Role.PARSER,
        prompt="\n".join(lines),
        model=decision.model,
        timeout_s=120.0,
        options={"reasoning_effort": "low"},
    )
    result = await (invoke(provider, request) if invoke else provider.invoke(request))
    # A failed digest degrades to the mechanical one rather than losing the history.
    return result.text.strip() if result.ok and result.text.strip() else mechanical_summary(events)


async def consolidate(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    providers: dict[str, ModelProvider],
    router,
    settings: DigestSettings | None = None,
    invoke: InvokeFn | None = None,
) -> list[int]:
    """Fold any newly eligible stretch of history into digests. Returns the ids written."""
    settings = settings or DEFAULT_SETTINGS
    turn = int(
        conn.execute("SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()[
            "turn_number"
        ]
    )
    if turn < settings.start_turn:
        return []

    horizon = turn - settings.keep_raw
    written: list[int] = []

    nations = [row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1", (world_id,)
    )]
    read_by_somebody = readable_audiences(conn, world_id)

    # One digest row per audience per chunk: the referee, plus every living nation. Only
    # the handful in `read_by_somebody` costs a model call; see the module docstring.
    for audience_id in [None, *nations]:
        covered = conn.execute(
            """
            SELECT COALESCE(MAX(turn_to), -1) AS t FROM digest
             WHERE world_id = ? AND (scope_nation_id IS ? OR scope_nation_id = ?)
            """,
            (world_id, audience_id, audience_id),
        ).fetchone()["t"]

        start = covered + 1
        while start + settings.chunk <= horizon:
            end = start + settings.chunk - 1
            # **The whole stretch, not the newest 400 of it.** Every row claims
            # `start..end`, and a fixed cap on a busy map kept only the last two or three
            # turns of that -- a digest reading "between turns 12 and 14" filed as 10-14,
            # and never revisited. The world's own count for the stretch is an upper bound
            # on what any audience witnessed, so it is a cap that never binds.
            everything = int(conn.execute(
                "SELECT COUNT(*) FROM event WHERE world_id = ? AND turn BETWEEN ? AND ?",
                (world_id, start, end),
            ).fetchone()[0])
            window = max(everything, 1)

            # **Bounded at both ends, which is the whole of the fix.** Both fetches are
            # ordered newest-first and capped, so asking only for `since_turn=start` gets
            # the newest 400 events of the *game*, not of the stretch. On a map that
            # records more than 400 events after turn `end` -- an ordinary mid-game
            # Modern Day turn does hundreds -- none of the stretch survives the cap, the
            # filter below deletes what is left, and the digest is written over an empty
            # list as "Nothing of note was recorded". The row then covers the right turns,
            # so consolidation never comes back to it: the history is not compressed, it
            # is lost, and every later briefing reads the assurance that nothing happened.
            if audience_id is None:
                events = [
                    e for e in referee.all_events(
                        conn, world_id, since_turn=start, until_turn=end, limit=window
                    )
                    if start <= e["turn"] <= end
                ]
                audience = "the historical record"
            else:
                events = [
                    e for e in visibility.known_events(
                        conn, audience_id, since_turn=start, until_turn=end, limit=window
                    )
                    if start <= e["turn"] <= end
                ]
                name = conn.execute(
                    "SELECT name FROM nation WHERE id = ?", (audience_id,)
                ).fetchone()
                audience = name["name"] if name else audience_id

            # The whole cost fix, in one branch. An audience nobody reads gets the
            # mechanical summary -- complete, free, and already the failure path -- so its
            # history is preserved against a later promotion without paying for prose no
            # one will read.
            if audience_id in read_by_somebody:
                summary = await _summarise(
                    events, audience=audience, providers=providers, router=router,
                    invoke=invoke,
                )
            else:
                summary = mechanical_summary(events)
            cursor = conn.execute(
                """
                INSERT INTO digest (world_id, turn_from, turn_to, level, scope_nation_id,
                                    summary_text)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (world_id, start, end, audience_id, summary),
            )
            written.append(int(cursor.lastrowid))
            start = end + 1

    conn.commit()
    return written


def digest_for(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str | None = None
) -> str:
    """The consolidated history to prepend to a view.

    Scoped to the audience, so a nation's digest never summarises events it did not
    witness.
    """
    rows = conn.execute(
        """
        SELECT turn_from, turn_to, summary_text FROM digest
         WHERE world_id = ? AND (scope_nation_id IS ? OR scope_nation_id = ?)
         ORDER BY turn_from
        """,
        (world_id, nation_id, nation_id),
    ).fetchall()
    if not rows:
        return ""
    return "\n".join(
        f"Turns {row['turn_from']}-{row['turn_to']}: {row['summary_text']}" for row in rows
    )
