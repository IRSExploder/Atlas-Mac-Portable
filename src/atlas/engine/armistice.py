"""A truce: the guns stop, the war does not end, and either side can break it.

Layer 5's self-contained half, and the one thing the war loop still could not express. A
peace treaty *resolves* a war: the flag comes down, ground changes hands, an agreement is
signed and the quarrel is over. An armistice is the other thing that happens far more often
-- **the fighting stops and nothing is settled**. Korea has had one since 1953.

**Structurally distinct from a settlement, deliberately.** A ceasefire carries no terms, no
cessions and no recognition; ``relation.at_war`` stays 1, because the war *is* still on and
every consumer that reasons about a war -- the ledger, leverage, the aims -- should keep
reasoning about it. What changes is that the fighting is paused: contests between the pair
are not fought and the air war passes them over.

**It can end three ways, and all three are real.**

*It lapses.* An armistice is written with an ``expires_turn`` and
:func:`atlas.engine.treaties.expire_commitments` retires it like any other agreement whose
term has run out -- an expiry is not a betrayal and is reported apart from one. The war
simply resumes.

*It is broken.* The first shot is what breaks it: a contest against a power you have a
standing truce with runs through :func:`atlas.engine.treaties.breach`, which is what every
other broken agreement runs through -- the status, the regard, the legitimacy charge and a
casus belli for the side that was attacked. The attack then proceeds, because it happened.

*It becomes a peace.* Signing a settlement with the same power marks the truce fulfilled:
it did what it was for.

**No new table.** ``commitment`` has carried the ``ceasefire`` kind and an ``expires_turn``
since the schema was written and nothing has ever written one; the parties are
``commitment_nation`` rows; whether a pair is paused is a query rather than stored state,
so there is nothing to keep in step and nothing extra to rewind.

**Reachable.** A model offers one through the same ``peace_offer`` channel a settlement goes
through, with ``kind: "ceasefire"``; the simulator's ``ceasefire`` declaration routes here;
a player types one. The engine decides acceptance -- see :func:`will_accept` -- because a
power winning outright and in no distress does not stop shooting to oblige its enemy.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import CommitmentKind, DiffOp, EventKind, EventOrigin, Role, Visibility
from .leverage import leverage_between
from .pacing import after_months, days_between
from .wareffort import effort_between

__all__ = [
    "ARMISTICE_MONTHS",
    "Armistice",
    "armistice_between",
    "assess_armistices",
    "break_armistice",
    "discharge_diffs",
    "fighting_paused",
    "offer_ceasefire",
    "paused_pairs",
    "will_accept",
]

#: How long a truce runs when nobody says. Three months is long enough to matter and short
#: enough that it has to be renewed or converted, which is what makes it a decision.
ARMISTICE_MONTHS = 3

#: The longest one anybody may write. A twenty-year truce is a peace treaty that has not
#: admitted what it is.
MAX_MONTHS = 24

#: How close to running out a truce has to be before the turn report mentions it, **in days
#: of world time**. A month, because that is the horizon at which a government would start
#: deciding whether to renew -- and because "next turn" meant a week to one player and a
#: year to another, from the same row.
_WARN_WITHIN_DAYS = 31.0

#: **Above this, a power that is winning does not stop shooting.** A truce hands a beaten
#: enemy time to dig in, and a commanding power in no distress has no reason to grant it.
REFUSE_ABOVE = 0.45

#: ...unless it is tired too, which is what an armistice historically *is*: both sides
#: needing the same pause for different reasons.
TIRED_ENOUGH = 45.0


@dataclass(frozen=True, slots=True)
class Armistice:
    """A standing truce: who, until when, and the agreement it is."""

    commitment_id: int
    parties: tuple[str, str]
    opened_turn: int
    expires_turn: int

    def describe(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        left, right = (names.get(p, p) for p in self.parties)
        return (f"a ceasefire between {left} and {right}, signed on turn "
                f"{self.opened_turn} and running to turn {self.expires_turn}")


@dataclass(slots=True)
class ArmisticeReport:
    """What came of an offer."""

    agreed: bool = False
    commitment_id: int | None = None
    expires_turn: int = 0
    #: The world date it runs to, which is what the two governments actually agreed and
    #: what a player should be told. ``""`` only for a world with no readable date.
    expires_on: str = ""
    refused: list[str] = field(default_factory=list)
    #: Where the offer was carried but not on the terms asked for. The length is clamped to
    #: :data:`MAX_MONTHS`, and it was clamped silently: a government that asked for five
    #: years got two and nothing said so, in a layer whose every other refusal names itself.
    #: Not a refusal -- the truce stands -- which is why it is its own list.
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- reading


def armistice_between(
    conn: sqlite3.Connection, world_id: str, a: str, b: str
) -> Armistice | None:
    """The standing truce between two powers, if there is one."""
    row = conn.execute(
        """
        SELECT c.id, c.created_turn, c.expires_turn
          FROM commitment c
          JOIN commitment_nation one ON one.commitment_id = c.id AND one.nation_id = ?
          JOIN commitment_nation two ON two.commitment_id = c.id AND two.nation_id = ?
         WHERE c.world_id = ? AND c.kind = ? AND c.status = 'active'
         ORDER BY c.id DESC LIMIT 1
        """,
        (a, b, world_id, CommitmentKind.CEASEFIRE.value)).fetchone()
    if row is None:
        return None
    return Armistice(
        commitment_id=int(row["id"]), parties=(a, b),
        opened_turn=int(row["created_turn"]),
        expires_turn=int(row["expires_turn"] or 0))


def discharge_diffs(
    conn: sqlite3.Connection, world_id: str, a: str, b: str, *, turn: int, event_id: int
) -> list[Diff]:
    """The diffs that end a standing truce between two powers whose war is over.

    **Every way a war ends has to carry these**, in the same batch as the `at_war = 0`
    diffs, or the truce outlives the war it paused: `fighting_paused` reads only the
    commitment, so a later war between the same two powers would find its contests
    silently suppressed by a ceasefire nobody remembers agreeing.

    **`expired`, not `fulfilled`, and rule DIP-005 is why.** A ceasefire is a *standing*
    obligation -- one you go on keeping -- and `fulfillment_blockers` refuses to call any
    such agreement completed: "it can be broken, expire, or be terminated, but it is never
    completed". Terminated because the war ended is an ending, not a completion.
    """
    standing = armistice_between(conn, world_id, a, b)
    if standing is None:
        return []
    return [
        Diff("commitment", str(standing.commitment_id), "status", DiffOp.SET,
             "expired", reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("commitment", str(standing.commitment_id), "resolved_turn",
             DiffOp.SET, turn, reason_event_id=event_id,
             visibility_scope=Visibility.PUBLIC),
    ]


def fighting_paused(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    """Whether these two have a truce standing. The one question the fighting asks."""
    return armistice_between(conn, world_id, a, b) is not None


def paused_pairs(conn: sqlite3.Connection, world_id: str) -> set[frozenset[str]]:
    """Every pair under a standing truce, read once for a turn's fighting."""
    found: set[frozenset[str]] = set()
    for row in conn.execute(
        """
        SELECT one.nation_id AS a, two.nation_id AS b
          FROM commitment c
          JOIN commitment_nation one ON one.commitment_id = c.id
          JOIN commitment_nation two ON two.commitment_id = c.id
         WHERE c.world_id = ? AND c.kind = ? AND c.status = 'active'
           AND one.nation_id < two.nation_id
        """,
        (world_id, CommitmentKind.CEASEFIRE.value)
    ):
        found.add(frozenset((str(row["a"]), str(row["b"]))))
    return found


# ------------------------------------------------------------------------ acceptance


def will_accept(
    conn: sqlite3.Connection, world_id: str, *, offered_to: str, offered_by: str
) -> tuple[bool, str]:
    """Whether ``offered_to`` stops shooting, and why. **The engine's call, not a model's.**

    Deliberately not the settlement's acceptance rule: there are no terms to weigh, so the
    question is not *what does this cost me* but *do I want the pause*. A power winning
    outright and in no distress does not want it -- a truce is time it hands to a beaten
    enemy. A power that is tired takes the breather whatever its position, which is what
    an armistice historically is: both sides needing the same pause for different reasons.
    """
    position = leverage_between(conn, world_id, offered_to, offered_by).position
    effort = effort_between(conn, world_id, offered_to, offered_by)
    exhaustion = effort.exhaustion if effort else 0.0
    if position >= REFUSE_ABOVE and exhaustion < TIRED_ENOUGH:
        return False, (
            f"{offered_to} is winning at {position:+.2f} and is not tired enough to stop "
            f"({exhaustion:.0f}/100); a truce is time it would be handing to the other side")
    return True, (
        f"{offered_to} is at {position:+.2f} and {exhaustion:.0f}/100 war-weary; it takes "
        "the pause")


# --------------------------------------------------------------------------- writing


def offer_ceasefire(  # noqa: PLR0913 - a truce is its parties, its length and its clock
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    enemy_id: str,
    months: int = ARMISTICE_MONTHS,
    turn: int,
    date: str,
) -> ArmisticeReport:
    """Offer to stop shooting for a while. The war stays on; the fighting stops.

    Refused if the two are not at war, if a truce already stands between them, or if the
    other side would rather keep fighting.
    """
    from .diplomacy import next_commitment_id

    report = ArmisticeReport()
    if not _at_war(conn, world_id, nation_id, enemy_id):
        report.refused.append(f"{nation_id} and {enemy_id} are not at war")
        return report
    if fighting_paused(conn, world_id, nation_id, enemy_id):
        report.refused.append(
            f"{nation_id} and {enemy_id} already have a ceasefire standing")
        return report

    accepted, why = will_accept(
        conn, world_id, offered_to=enemy_id, offered_by=nation_id)
    if not accepted:
        report.refused.append(f"{enemy_id} refused the ceasefire: {why}")
        return report

    asked_for = int(months or ARMISTICE_MONTHS)
    span = max(1, min(MAX_MONTHS, asked_for))
    if span != asked_for:
        report.notes.append(
            f"the truce runs {span} months rather than the {asked_for} asked for: "
            f"a ceasefire may stand at most {MAX_MONTHS}"
        )
    # **The turn it ends on, and the date it ends on.** The date is what the two governments
    # agreed and what every reader below prefers; the turn is kept for a save written before
    # schema 35, which has no date to convert to. See the migration.
    expires = turn + span
    expires_on = after_months(date, span)
    names = _names(conn, world_id)
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.CEASEFIRE,
        origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(f"{names.get(nation_id, nation_id)} and {names.get(enemy_id, enemy_id)} "
              f"agreed a ceasefire, to run {span} month(s). The war is not over."),
        visibility=Visibility.PUBLIC,
        participants=(("actor", nation_id), ("target", enemy_id)))

    commitment_id = next_commitment_id(conn)
    diffs = [
        Diff("commitment", str(commitment_id), "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id,
                 "kind": CommitmentKind.CEASEFIRE.value,
                 "status": "active",
                 "condition_text": (
                     f"A ceasefire between {names.get(nation_id, nation_id)} and "
                     f"{names.get(enemy_id, enemy_id)}: the fighting stops until "
                     f"{expires_on or f'turn {expires}'}. Nothing is settled."),
                 "source_event_id": event_id,
                 "created_turn": turn,
                 "expires_turn": expires,
                 "expires_on": expires_on,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
    ]
    diffs += [
        Diff("commitment_nation", f"{commitment_id}|{party}", "__insert__",
             DiffOp.INSERT, {"role": "party", "accepted_turn": turn},
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for party in (nation_id, enemy_id)
    ]
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not outcome.applied:
        unrecord_events(conn, [event_id])
        report.refused.append(f"the ceasefire could not be written: {outcome.reason}")
        return report

    report.agreed = True
    report.commitment_id = commitment_id
    report.expires_turn = expires
    report.expires_on = expires_on
    return report


def break_armistice(
    conn: sqlite3.Connection, world_id: str, *, breaker: str, victim: str,
    turn: int, date: str,
) -> str | None:
    """Somebody attacked through a standing truce. Returns the line for the report.

    **Through the breach machinery, not beside it.** Tearing up an armistice is the same
    act as tearing up any other agreement, and `breach` is what charges for one: the
    status, ``breached_by_nation_id``, the regard with the injured party, the legitimacy,
    and the casus belli that makes their retaliation legitimate. A second implementation
    here would be a second set of consequences to drift out of step.
    """
    from .treaties import breach

    standing = armistice_between(conn, world_id, breaker, victim)
    if standing is None:
        return None
    return breach(
        conn, world_id, commitment_id=standing.commitment_id, violator=breaker,
        turn=turn, date=date,
        why="attacked through a ceasefire it had agreed to keep")


def assess_armistices(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """What the standing truces are doing, for the turn report.

    Expiry itself is `treaties.expire_commitments`, which retires any agreement whose term
    has run out and says so -- an armistice is not special there, and giving it a second
    expiry path would be two ways for the same thing to happen. This reports the ones still
    standing and warns about the ones about to lapse, which is what a staff would be told.
    """
    told: list[str] = []
    names = _names(conn, world_id)
    for row in conn.execute(
        """
        SELECT c.id, c.expires_turn, c.expires_on,
               (SELECT GROUP_CONCAT(cn.nation_id) FROM commitment_nation cn
                 WHERE cn.commitment_id = c.id) AS parties
          FROM commitment c
         WHERE c.world_id = ? AND c.kind = ? AND c.status = 'active'
         ORDER BY c.id
        """,
        (world_id, CommitmentKind.CEASEFIRE.value)
    ):
        parties = [names.get(p, p) for p in str(row["parties"] or "").split(",") if p]
        # **How much longer, in world time.** A turn is not a duration, so "next turn" told
        # a player at yearly pacing that a truce had a year left and a player at weekly
        # pacing that it had a week -- from the same row.
        days = days_between(date, str(row["expires_on"] or "")) if row["expires_on"] else None
        if days is not None:
            if days <= 0:
                told.append(
                    f"the ceasefire between {' and '.join(parties)} has run out")
            elif days <= _WARN_WITHIN_DAYS:
                told.append(
                    f"the ceasefire between {' and '.join(parties)} runs out in "
                    f"{days:.0f} day(s)")
            continue
        # A save from before schema 35: the turn is all there is.
        left = int(row["expires_turn"] or 0) - turn
        if left <= 1:
            told.append(
                f"the ceasefire between {' and '.join(parties)} runs out "
                + ("this turn" if left <= 0 else "next turn"))
    return told


# --------------------------------------------------------------------------- internals


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?"
        "   AND at_war = 1", (world_id, a, b)).fetchone() is not None


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
