"""International bodies: a Council that watches the world, convenes, and is often ignored.

An organisation is the first actor in ATLAS that is not a country. It holds no ground, no
units and no capital; it is a ``nation`` row (so it can author events, hold a directed
relation to every power, and be addressed by a diff) carrying an ``organisation`` row that
says it is a body rather than a state. That representation is chosen for exactly the reason
the nuclear layer chose ``nation`` + ``arsenal``: it buys the entire existing spine without
building any of it.

**The mechanic is the authority loop, not the resolutions.** A Council that condemns and is
defied becomes a Council nobody fears, and it becomes that *measurably*: ``authority`` feeds
the odds its next resolution passes. So the interesting outcome is not a resolution passing
-- it is one being vetoed, or passing and being ignored, and the body being weaker next
time. That is what an international body's power actually is, and here it emerges rather
than being written.

**Triggered, never scheduled.** A session opens only when the engine has recorded one of a
closed set of conditions, all of which it already detected somewhere else -- an unprovoked
war, a war that was already running, a nuclear weapon used, ground annexed. The body
re-derives nothing; it reads the causal spine and the diff log.

**Including the wars it was born into.** The first cut read declarations made *this turn*
only, and every scenario flashpoint in both eras is seeded as ``relation.at_war = 1``, so a
live run had the Council sit out a two-and-a-half-year Russia--Ukraine war it existed to
have an opinion about. It convenes about such a war **once**, when it first notices it, and
the dedupe is exact rather than a cooldown: a session cites its trigger through
``event_cause``, so the war it sat about is identified and can never raise the trigger
again. Escalation inside that war -- an annexation, a warhead -- comes back through the
other arms, which is the intended second bite.

**The body proposes; the engine decides what a resolution does.** This is the nuclear-launch
discipline rather than the gambit-verb one, and deliberately: a resolution's consequences
should not be negotiable by whoever drafts it. The model picks an action from a closed
vocabulary and an accused. Every magnitude below is chosen here.

**Nothing here reaches ground, forces, contests or war status.** A Council can make a war
expensive, lonely, and legitimate to oppose. It cannot end one by saying so. Decision 57's
line, at the one place where a model might most reasonably expect to be able to cross it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.jsonschema import make_strict
from ..state.models import CommitmentKind, DiffOp, EventKind, EventOrigin, Role, Visibility
from .pacing import after_months
from .untrusted import sanitize

__all__ = [
    "has_jurisdiction",
    "ACTIONS",
    "ORGANISATION_SCHEMA",
    "Organisation",
    "SessionOutcome",
    "Trigger",
    "apply_sanctions",
    "build_session_view",
    "convene",
    "organisations_of",
    "sanction_drag",
    "session_rules_block",
    "triggers_this_turn",
]


# --------------------------------------------------------------------------- the body


@dataclass(frozen=True, slots=True)
class Organisation:
    nation_id: str
    name: str
    kind: str
    charter: str
    authority: float

    @property
    def credible(self) -> bool:
        """Below the floor a body can still exist and still be listed; it just cannot act.

        A Council that has been vetoed and ignored into irrelevance stops commanding
        attention -- which is the correct simulation *and* the cost control, since every
        session is a model call.
        """
        return self.authority >= AUTHORITY_FLOOR


#: Below this a body stops convening. Not zero, because a body at zero that could still
#: sit would be a body whose irrelevance had no consequence.
AUTHORITY_FLOOR = 15.0
#: Authority moves. Passing something is worth a little; being vetoed or defied costs
#: more than passing gains, so a body that keeps sitting and failing declines.
AUTH_PASSED = 3.0
AUTH_WATERED = -2.0
AUTH_FAILED = -4.0
AUTH_VETOED = -6.0
AUTH_DEFIED = -8.0
#: How far back the body remembers when deciding whether it has been defied, **in months
#: of world time**.
#:
#: It was six *turns*, which is the same bug :data:`SANCTION_MONTHS` was converted to fix
#: and which this one outlived by being named in a way the audit could not see: the sweep
#: in `tests/test_which_clock_each_duration_is_on.py` finds a constant by its name, and
#: `MEMORY` is not one of the words it looks for. So the one gate whose whole job is to ask
#: which clock a duration is on had never been shown this duration at all.
#:
#: Whether a power the body already sat about has come back in front of it is a fact about
#: the world -- it is the observable definition of the body being ignored, and the loop the
#: entire layer exists for. Six turns made that six months at monthly pacing and six
#: **years** at yearly, from the identical history. Six months is what six monthly turns
#: were worth, so nothing about the default game moves.
DEFIANCE_MEMORY_MONTHS = 6.0


def organisations_of(conn: sqlite3.Connection, world_id: str) -> list[Organisation]:
    rows = conn.execute(
        """
        SELECT o.nation_id, n.name, o.kind, o.charter_text, o.authority
          FROM organisation o JOIN nation n ON n.id = o.nation_id
         WHERE o.world_id = ? AND n.is_alive = 1
         ORDER BY o.nation_id
        """,
        (world_id,),
    ).fetchall()
    return [
        Organisation(str(r["nation_id"]), str(r["name"]), str(r["kind"]),
                     str(r["charter_text"]), float(r["authority"]))
        for r in rows
    ]


def members_of(
    conn: sqlite3.Connection, org_id: str
) -> list[tuple[str, bool]]:
    rows = conn.execute(
        """
        SELECT m.nation_id, m.is_permanent FROM organisation_member m
          JOIN nation n ON n.id = m.nation_id
         WHERE m.organisation_id = ? AND n.is_alive = 1
         ORDER BY m.nation_id
        """,
        (org_id,),
    ).fetchall()
    return [(str(r["nation_id"]), bool(r["is_permanent"])) for r in rows]


# ------------------------------------------------------------------------- triggers


@dataclass(frozen=True, slots=True)
class Trigger:
    """Something that just happened which a body would take an interest in."""

    kind: str
    accused: str
    #: Free text for the briefing. Never parsed.
    detail: str
    #: 1 (annexation) to 3 (nuclear use). Scales what the body may reasonably do.
    severity: int
    #: The event that caused it, so the session can cite it and later readers can find
    #: the accused again through `event_cause` without any new column.
    cause_event_id: int


def triggers_this_turn(
    conn: sqlite3.Connection, world_id: str, turn: int
) -> list[Trigger]:
    """What happened this turn that is a body's business.

    Read from the causal spine and the diff log rather than threaded through every call
    site. Both are ground truth the engine already wrote, so a trigger cannot be
    fabricated and nothing has to be kept in step.
    """
    found: list[Trigger] = []

    # A nuclear weapon used. Detected from the *diff log* rather than event text: an
    # applied negative adjust on `arsenal.warheads` is an unspoofable record that warheads
    # left an arsenal. **But not every warhead that leaves an arsenal was fired by its
    # owner.** Counterforce spends the *victim's* warheads on the ground, cited to the
    # launcher's strike event, and a body that read every decrement as a launch would
    # charge the nation that was struck at severity 3. So a decrement whose cause names
    # *another* nation as its actor is that nation's act, not this one's: it is already
    # accused through its own spend, cited to the same event.
    for row in conn.execute(
        """
        SELECT DISTINCT d.target_id AS nation_id, d.reason_event_id AS cause
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
          LEFT JOIN event e ON e.id = d.reason_event_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'arsenal' AND d.field = 'warheads'
           AND CAST(d.new_json AS REAL) < CAST(d.old_json AS REAL)
           AND (e.actor_nation_id IS NULL OR e.actor_nation_id = d.target_id)
         ORDER BY d.target_id
        """,
        (world_id, turn),
    ).fetchall():
        found.append(Trigger(
            "nuclear_use", str(row["nation_id"]),
            "used a nuclear weapon", 3, int(row["cause"] or 0),
        ))

    # An unprovoked war. `_has_casus_belli` is the same test `_declare_war` used to decide
    # whether to charge the aggressor's legitimacy, so the body's jurisdiction and the
    # engine's own judgement of provocation cannot drift apart.
    from .resolution import _has_casus_belli

    for row in conn.execute(
        """
        SELECT id, actor_nation_id, text FROM event
         WHERE world_id = ? AND turn = ? AND kind = 'war_declared'
           AND origin = 'ENGINE' AND actor_nation_id IS NOT NULL
         ORDER BY id
        """,
        (world_id, turn),
    ).fetchall():
        aggressor = str(row["actor_nation_id"])
        # Against the power this declaration named, when the declaration says. A grievance
        # about somebody else is not a justification for this war -- which is the whole
        # point of the trigger: an *unprovoked* war is what a body takes an interest in.
        victim = conn.execute(
            "SELECT nation_id FROM event_participant WHERE event_id = ? AND role = 'target'"
            "   AND nation_id IS NOT NULL LIMIT 1",
            (int(row["id"]),),
        ).fetchone()
        if _has_casus_belli(conn, world_id, aggressor, turn,
                            against=str(victim["nation_id"]) if victim else None):
            continue
        if any(t.accused == aggressor for t in found):
            continue
        found.append(Trigger(
            "unprovoked_war", aggressor, str(row["text"])[:200], 2, int(row["id"]),
        ))

    # **A war that was already running when the game began, or when the body last sat.**
    #
    # The arm above reads declarations made *this turn*, and PT30 showed what that misses:
    # every scenario flashpoint in both eras -- Russia--Ukraine in 2026, Italy--Abyssinia
    # and Japan--Manchuria in 1936 -- is seeded as `relation.at_war = 1`, so the Council
    # sat out a two-and-a-half-year war it existed to have an opinion about.
    #
    # **No heuristic is needed to say whose fault it is.** The seeded history already
    # records the declaration as a turn-0 `war_declared` event naming the aggressor --
    # "Russia invaded Ukraine in February 2022" -- so this reads the engine's own record
    # rather than guessing from who occupies what. The same event becomes the trigger's
    # cause, which is what makes the dedupe below exact and needs no new state.
    # **The SQL below is a pre-filter and not the whole test.** "This power is at war with
    # somebody" is a necessary condition for the war *this* declaration began still being
    # on, and it is all the query can cheaply say, because whom the war is against lives in
    # `event_participant` rather than in a column. The two checks that decide it are in
    # Python, immediately below, and each is there for a bug it was found by.
    for row in conn.execute(
        """
        SELECT e.id, e.turn, e.actor_nation_id, e.text FROM event e
         WHERE e.world_id = ? AND e.kind = 'war_declared' AND e.origin = 'ENGINE'
           AND e.turn < ? AND e.actor_nation_id IS NOT NULL
           -- at war with somebody, so a power wholly at peace is not read at all
           AND EXISTS (SELECT 1 FROM relation r
                        WHERE r.world_id = e.world_id
                          AND r.nation_a = e.actor_nation_id AND r.at_war = 1)
           -- and no body has sat about *this war* yet. Exact per-war dedupe: a session
           -- cites its trigger through `event_cause`, so a war the Council has already
           -- convened about can never raise this trigger again, however long it runs.
           -- Escalation within it -- a nuclear weapon, an annexation -- comes back
           -- through the arms above, which is the intended second bite.
           AND NOT EXISTS (
                SELECT 1 FROM event_cause ec
                  JOIN event s ON s.id = ec.event_id
                  JOIN organisation o ON o.nation_id = s.actor_nation_id
                 WHERE ec.cause_id = e.id AND o.world_id = e.world_id
           )
         ORDER BY e.id
        """,
        (world_id, turn),
    ).fetchall():
        aggressor = str(row["actor_nation_id"])
        if any(t.accused == aggressor for t in found):
            continue
        # Whom this declaration was against. Every engine-authored declaration records it;
        # the seeded turn-0 history of each scenario does not, and `None` means the two
        # checks below fall back to what they can say about the aggressor alone -- which is
        # exactly the behaviour this arm had before, so no shipped flashpoint moves.
        target = conn.execute(
            "SELECT nation_id FROM event_participant WHERE event_id = ? AND role = 'target'"
            "   AND nation_id IS NOT NULL LIMIT 1",
            (int(row["id"]),),
        ).fetchone()
        victim = str(target["nation_id"]) if target else None

        # **Still a war, not a history lesson -- and *this* war rather than any war.** The
        # query asks whether the aggressor is at war with anybody, which is not the same
        # question and answered yes in two cases it should not have. A power that made
        # peace here and went to war elsewhere put the *concluded* war in front of the
        # Council; and because `relation` is directed and stored both ways, a power merely
        # *being invaded* reads as at war, so the victim of a fresh invasion was arraigned
        # for an old war of their own -- and, being the lower event id, displaced their own
        # invader from the single session the world gets each turn.
        if victim is not None and not _still_at_war(conn, world_id, aggressor, victim):
            continue

        # **The same provocation test the arm above keeps, so the two cannot disagree.**
        # Without it the casus-belli gate was a one-turn delay and nothing more: a war the
        # unprovoked arm correctly declined on the day it was declared came back through
        # this arm on the very next turn, and every war in the world was the body's
        # business. Asked **as at the declaration's own turn**, which is when that arm asked
        # it -- a grievance keeps for five turns, so asking today would make a settled
        # question of history depend on how long ago it happened.
        if _has_casus_belli(conn, world_id, aggressor, int(row["turn"]), against=victim):
            continue

        found.append(Trigger(
            "ongoing_war", aggressor, str(row["text"])[:200], 2, int(row["id"]),
        ))

    # Ground annexed at a peace table.
    for row in conn.execute(
        """
        SELECT id, actor_nation_id, text FROM event
         WHERE world_id = ? AND turn = ? AND kind = 'territory_transfer'
           AND origin = 'ENGINE' AND actor_nation_id IS NOT NULL
         ORDER BY id
        """,
        (world_id, turn),
    ).fetchall():
        actor = str(row["actor_nation_id"])
        if any(t.accused == actor for t in found):
            continue
        found.append(Trigger(
            "annexation", actor, str(row["text"])[:200], 1, int(row["id"]),
        ))

    # Gravest first: one session a turn, and it should be about the worst thing that
    # happened rather than the alphabetically first.
    found.sort(key=lambda t: -t.severity)
    return found


def _still_at_war(
    conn: sqlite3.Connection, world_id: str, aggressor: str, victim: str
) -> bool:
    """Whether the war between these two particular powers is still on.

    Directed, and deliberately asked in the aggressor's direction only: `relation` holds
    both rows and a declaration of war sets both, so either answers -- and asking the one
    the declaration names keeps this reading the same row the declaration wrote.
    """
    row = conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?"
        "   AND at_war = 1",
        (world_id, aggressor, victim),
    ).fetchone()
    return row is not None


def _defied_before(
    conn: sqlite3.Connection, world_id: str, org_id: str, accused: str, turn: int,
    date: str = "",
) -> bool:
    """Has this body already sat about this power, recently?

    If so, whatever it resolved did not stop them -- they are back in front of it. That is
    the observable definition of being ignored, and it needs no new state: a session event
    cites its trigger, and the trigger's actor *is* the accused, so the accused is
    recoverable through ``event_cause``.

    **"Recently" is elapsed history**, not turns -- see :data:`DEFIANCE_MEMORY_MONTHS`.
    ``session.turn < turn`` stays as the "before now" half whichever clock measures the
    window, because a session earlier in *this* turn is not the body being defied.
    """
    since = after_months(date, -DEFIANCE_MEMORY_MONTHS) if date else ""
    if not since:
        # **An unreadable date means "not defied" rather than a fallback window.** There is
        # no dateless case to support -- `event.date` is `NOT NULL` and every `record_event`
        # writes one -- so the alternative was a turns-counted window kept for a save that
        # does not exist, which is the very thing this constant was just converted away
        # from. `doctrines.settle_shifts` answers the same question the same way: a row
        # nobody can date does not land by accident. Under-charging is the safe direction,
        # because this decides whether to apply the harshest penalty the layer has.
        return False
    row = conn.execute(
        """
        SELECT COUNT(*) FROM event session
          JOIN event_cause ec ON ec.event_id = session.id
          JOIN event trigger ON trigger.id = ec.cause_id
         WHERE session.world_id = ? AND session.kind = 'diplomatic_message'
           AND session.origin = 'ENGINE' AND session.actor_nation_id = ?
           AND session.turn < ? AND session.date >= ?
           AND trigger.actor_nation_id = ?
        """,
        (world_id, org_id, turn, since, accused),
    ).fetchone()
    return bool(row and row[0])


# -------------------------------------------------------------------- the vocabulary


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    blurb: str
    base_odds: float
    #: The least severe trigger this action is proportionate to.
    min_severity: int


#: What a resolution may do, and nothing else exists. Each reaches a column the engine
#: already tracks; each is applied by the engine, not chosen freely.
ACTIONS: tuple[Action, ...] = (
    Action("condemn",
           "Name what was done and put every member's name to it.", 0.70, 1),
    Action("sanction",
           "Cut them off. Their economy and treasury bleed while it stands.", 0.50, 1),
    Action("demand_ceasefire",
           "Require the guns to stop. The parties still have to accept.", 0.55, 2),
    Action("authorise_force",
           "Give every member a free hand to act against them militarily.", 0.28, 2),
)

#: Magnitudes, all engine-chosen. A resolution's consequences are not negotiable by
#: whoever drafts it.
CONDEMN_REGARD = 15.0

#: How long a sanctions regime stands, **in months of world time**.
#:
#: It was eight *turns*, which drained an economy for eight months at monthly pacing and
#: eight years at yearly, from the same resolution. A measure's length is a fact about the
#: world -- what the body voted for -- and not about how much detail the player asked to
#: watch. Eight months is what eight monthly turns were worth, so nothing about the default
#: game moves.
SANCTION_MONTHS = 8

#: **Whether a measure is still standing**, as one SQL fragment both readers share so they
#: cannot come to disagree about it. The date decides where there is one; the turn is the
#: fallback for a save written before schema 36, which has no date to convert from.
#:
#: Takes ``(date, turn)`` in that order, and every caller passes both.
#:
#: **The clock is an argument because two readers cannot supply it the same way.** The
#: economic readers below are handed the turn's date and number by a caller already inside
#: a turn. `trade.route_is_open` is not: it is reached from the pricing layer through five
#: frames that carry neither, and threading them would change ten call sites to fix one
#: predicate. So that reader joins `world` and takes the clock off the row, which is what
#: :func:`still_standing` renders for it. Either way there is one definition of "standing"
#: -- a second, hand-rolled expiry test is exactly how the trade layer came to disagree
#: with this one about which measures had lapsed.
def still_standing(date_expr: str = "?", turn_expr: str = "?") -> str:
    """The expiry predicate, over whichever SQL expressions carry the clock."""
    return (
        f"((until_on IS NOT NULL AND until_on <> '' AND until_on > {date_expr})"
        f" OR (COALESCE(until_on, '') = '' AND until_turn > {turn_expr}))"
    )


_STILL_STANDING = still_standing()
#: **Proportional, not absolute.** The first cut subtracted a fixed 0.9 of economy per
#: month of sanction, which is regressive to the point of being wrong: it took Belarus
#: from 4.3 to the floor in six turns (a 77% collapse) while the same resolution against
#: the United States would have been a rounding error. A sanction is a headwind whose
#: strength is a share of what it blows against, so both drags are rates.
SANCTION_ECONOMY_RATE = 0.03
SANCTION_TREASURY_RATE = 0.06
#: A sanctioned economy is crippled, never erased: something below this is a country no
#: later mechanic can reason about, since revenue and industrial capacity both read it.
SANCTION_ECONOMY_FLOOR = 1.0
#: A watered-down resolution is worth this much of a full one.
PARTIAL_SHARE = 0.5
#: A permanent member protects a power it is this fond of. And it always protects itself.
VETO_AFFECTION = 25.0
_ODDS_FLOOR, _ODDS_CEILING = 0.05, 0.95


#: A session's outcome in words: "a motion to condemn it was vetoed".
_MOTIONS = {"condemn": "condemn it", "sanction": "sanction it",
            "authorise_force": "authorise force against it",
            "demand_ceasefire": "demand a ceasefire"}
_VERDICTS = {"passed": "passed", "watered_down": "passed in a weakened form",
             "failed": "failed", "vetoed": "was vetoed"}


def action_by_id(action_id: str) -> Action | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "justification", "refused"],
    "properties": {
        "action": {
            # **An enum member must be a real word.** `engine.parser` records that agy
            # rejects an enum containing `null`; the rule turns out to be broader, and this
            # schema is how that was found. Bisected by holding the role fixed and varying
            # one field: an enum of plain strings passes, the same enum plus `null` fails,
            # and the same enum plus `""` fails too. Both empty members are rejected, so
            # "nothing" has to be spelled.
            #
            # It never cost anything before because this role routed to `claude`, which is
            # not strict about it. The day it was pointed anywhere else, every Security
            # Council session failed with agy's generic "Agent execution terminated due to
            # error" -- which the adapter attributes to exhausted quota, and which was
            # nothing of the kind.
            #
            # `_read` maps any id that is not a real action to "the body resolved nothing",
            # so `none` needs no handling and the meaning is exactly what `null` meant.
            "type": "string",
            "enum": [*sorted(a.id for a in ACTIONS), "none"],
            "description": "What the body resolves to do. `none` if it does nothing.",
        },
        "justification": {
            "type": "string",
            "description": "One sentence, in world, as a communiqué would put it.",
        },
        "refused": {
            "type": ["string", "null"],
            "description": (
                "Set instead of `action` when the body should not act at all. That is a "
                "real answer and sometimes the right one."
            ),
        },
    },
}
ORGANISATION_SCHEMA: dict[str, Any] = make_strict(_SCHEMA_RAW)


# ------------------------------------------------------------------------ the session


@dataclass(slots=True)
class SessionOutcome:
    organisation: str = ""
    accused: str = ""
    action: str = ""
    #: ``passed`` | ``watered_down`` | ``failed`` | ``vetoed``. Empty if it never sat.
    result: str = ""
    odds: float = 0.0
    vetoed_by: list[str] = field(default_factory=list)
    narrative: str = ""
    refused: str | None = None
    applied: int = 0
    authority_before: float = 0.0
    authority_after: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.result)


def has_jurisdiction(
    conn: sqlite3.Connection, org_id: str, kind: str, accused: str
) -> bool:
    """Whether this body may sit in judgement on this power at all.

    **`organisation.kind` was stored and never read.** `turn_engine` picked the first
    credible body that was not itself the accused, so an ``alliance`` -- a body whose
    charter runs to its own members -- would convene on a power that had never joined it
    and had never accepted its authority, exactly as a ``universal`` body does. The column
    existed to draw precisely that distinction. Codex found that nothing drew it.

    A ``universal`` body has jurisdiction over everybody: that is what universal means, and
    it is the whole reason the Security Council can act on a power that is not its friend.
    An ``alliance`` reaches its own members and nobody else.
    """
    if kind != "alliance":
        return True
    return any(member == accused for member, _permanent in members_of(conn, org_id))


def _support(
    conn: sqlite3.Connection, org_id: str, accused: str
) -> tuple[float, list[str]]:
    """How the room feels about the accused, from `relation` rows. Never from a model.

    Returns ``(term, veto_holders)``. The term is the mean dislike across members,
    normalised to roughly -0.2..+0.2 -- a body whose members already loathe the accused
    votes more readily than one whose members are its trading partners.
    """
    members = members_of(conn, org_id)
    if not members:
        return 0.0, []
    scores: list[float] = []
    vetoes: list[str] = []
    for member, permanent in members:
        if member == accused:
            # A body cannot be relied on to condemn itself -- and if the accused holds a
            # veto, that is the whole point of holding one.
            if permanent:
                vetoes.append(member)
            continue
        row = conn.execute(
            "SELECT score FROM relation WHERE nation_a = ? AND nation_b = ?",
            (member, accused),
        ).fetchone()
        regard = float(row["score"]) if row else 0.0
        scores.append(regard)
        if permanent and regard >= VETO_AFFECTION:
            vetoes.append(member)
    if not scores:
        return 0.0, vetoes
    mean_regard = sum(scores) / len(scores)
    return max(-0.2, min(0.2, -mean_regard / 500.0)), vetoes


def build_session_view(
    conn: sqlite3.Connection, world_id: str, *, org: Organisation, trigger: Trigger
) -> str:
    """The prompt: what happened, who is in the room, and what the body may do."""
    accused_row = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (trigger.accused,)
    ).fetchone()
    accused_name = accused_row["name"] if accused_row else trigger.accused
    _term, vetoes = _support(conn, org.nation_id, trigger.accused)

    lines = [
        f"# {org.name}",
        org.charter,
        "",
        f"Your standing in the world is **{org.authority:.0f} of 100**. It falls when you "
        "are vetoed, when a resolution fails, and when a power you have already sat about "
        "comes back in front of you. It rises when you carry something. It decides how "
        "likely your next resolution is to pass.",
        "",
        "## Why you have been convened",
        f"**{accused_name}** [`{trigger.accused}`] {trigger.detail}",
        "",
        "## The room",
    ]
    for member, permanent in members_of(conn, org.nation_id):
        row = conn.execute(
            "SELECT n.name, r.score FROM nation n"
            " LEFT JOIN relation r ON r.nation_a = n.id AND r.nation_b = ?"
            " WHERE n.id = ?",
            (trigger.accused, member),
        ).fetchone()
        if row is None:
            continue
        mark = " **(permanent — holds a veto)**" if permanent else ""
        lines.append(
            f"- {row['name']} [`{member}`]{mark}: regard for {accused_name} "
            f"{float(row['score'] or 0.0):.0f}"
        )
    if vetoes:
        names = [
            conn.execute("SELECT name FROM nation WHERE id = ?", (v,)).fetchone()["name"]
            for v in vetoes
        ]
        lines += [
            "",
            f"**{', '.join(names)} will veto anything against {accused_name}.** A vetoed "
            "resolution does nothing and costs you standing. Resolving to act anyway is a "
            "choice you may make, and it is usually the wrong one.",
        ]
    lines += ["", "## What you may resolve", ""]
    lines += [f"- {rule}" for rule in session_rules_block(trigger.severity)]
    return "\n".join(lines)


def session_rules_block(severity: int) -> list[str]:
    """The vocabulary and every bound, generated from the tables (decision 50)."""
    return [
        "What you may resolve, and nothing else exists: "
        + "; ".join(
            f"`{a.id}` — {a.blurb}"
            + ("" if a.min_severity <= severity
               else " **Out of proportion to this — it would fail.**")
            for a in ACTIONS
        ),
        f"`condemn` moves every member's regard for them by {CONDEMN_REGARD:.0f}. "
        f"`sanction` drains their economy and treasury for {SANCTION_MONTHS} months. "
        "`authorise_force` hands every member a casus belli, meaning they may go to war "
        "with them at no cost to their own legitimacy. `demand_ceasefire` opens a "
        "ceasefire the parties still have to accept.",
        "**You do not choose what a resolution does.** The wording is yours; the "
        "consequences are fixed and applied by the engine.",
        "You cannot take ground, move a formation, raise an army, or end a war by "
        "resolving that it is over. A body makes a war expensive, lonely and legitimate "
        "to oppose. It does not win one.",
        "Resolving nothing is a real answer — set `refused` and say why. A body that "
        "passes something it cannot enforce is weaker afterwards, not stronger.",
    ]


def convene(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    org: Organisation,
    trigger: Trigger,
    payload: str | dict[str, Any],
    turn: int,
    date: str,
    seed: int,
    index: int = 0,
) -> SessionOutcome:
    """Sit, vote, and apply what carried. The engine decides everything but the wording."""
    outcome = SessionOutcome(
        organisation=org.nation_id, accused=trigger.accused,
        authority_before=org.authority, authority_after=org.authority,
    )
    try:
        data: dict[str, Any] = (
            json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        )
    except (TypeError, ValueError):
        outcome.problems.append("the answer from a session was not readable")
        return outcome

    if refused := data.get("refused"):
        outcome.refused = sanitize(str(refused))[:400]
        _record_decline(conn, world_id, org=org, trigger=trigger, outcome=outcome,
                        turn=turn, date=date)
        return outcome
    action = action_by_id(str(data.get("action") or ""))
    if action is None:
        outcome.refused = "the body resolved nothing"
        _record_decline(conn, world_id, org=org, trigger=trigger, outcome=outcome,
                        turn=turn, date=date)
        return outcome

    # **Defiance is priced before the vote, not after it.** A power that is back in front
    # of the same body inside the memory window is a power that ignored it, and the body
    # sits weakened -- which is the loop the whole layer exists for.
    authority = org.authority
    authority_diffs: list[Diff] = []
    defied = _defied_before(conn, world_id, org.nation_id, trigger.accused, turn, date)

    support, vetoes = _support(conn, org.nation_id, trigger.accused)

    odds = action.base_odds + authority / 200.0 + support
    if action.min_severity > trigger.severity:
        odds -= 0.35
    odds = max(_ODDS_FLOOR, min(_ODDS_CEILING, odds))
    roll = derive(
        seed, world_id, turn,
        f"session:{org.nation_id}:{action.id}:{trigger.accused}#{index}",
    ).random()

    if vetoes:
        result, share = "vetoed", 0.0
    elif roll < odds * 0.6:
        result, share = "passed", 1.0
    elif roll < odds:
        result, share = "watered_down", PARTIAL_SHARE
    else:
        result, share = "failed", 0.0

    outcome.action, outcome.result, outcome.odds = action.id, result, round(odds, 3)
    outcome.vetoed_by = list(vetoes)

    justification = sanitize(str(data.get("justification") or ""))[:300]
    names = _names(conn, (org.nation_id, trigger.accused))
    motion = _MOTIONS.get(action.id, action.id.replace("_", " "))
    verdict = _VERDICTS.get(result, result.replace("_", " "))
    told = (
        f"{names[org.nation_id]} on {names[trigger.accused]}: a motion to {motion} "
        f"{verdict}" + (f". {justification}" if justification else ".")
    )
    outcome.narrative = told
    session_event = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.DIPLOMATIC_MESSAGE, origin=EventOrigin.ENGINE,
        actor_nation_id=org.nation_id, text=told[:600], visibility=Visibility.PUBLIC,
        # Citing the trigger is what makes the accused recoverable later, with no column
        # for it -- see `_defied_before`. The accused is now *also* a recorded participant,
        # which says the same thing directly rather than by following a citation, and a
        # `demand_ceasefire` names both belligerents so the parties to the demanded
        # agreement are a fact rather than a sentence.
        causes=(trigger.cause_event_id,) if trigger.cause_event_id else (),
        participants=(("actor", org.nation_id), ("target", trigger.accused)),
    )

    diffs: list[Diff] = []
    # Events a resolution mints as a *consequence*, kept so they can be taken back with the
    # batch. See where they are retracted, below.
    consequences: list[int] = []
    if share > 0:
        resolved, consequences = _resolution_diffs(
            conn, world_id, org=org, action=action, trigger=trigger, share=share,
            turn=turn, date=date, event_id=session_event,
        )
        diffs += resolved

    delta = {
        "passed": AUTH_PASSED, "watered_down": AUTH_WATERED,
        "failed": AUTH_FAILED, "vetoed": AUTH_VETOED,
    }[result] + (AUTH_DEFIED if defied else 0.0)
    authority = max(0.0, min(100.0, authority + delta))
    authority_diffs.append(
        Diff("organisation", org.nation_id, "authority", DiffOp.SET, round(authority, 2),
             reason_event_id=session_event, visibility_scope=Visibility.PUBLIC)
    )
    authority_diffs.append(
        Diff("organisation", org.nation_id, "convened_turn", DiffOp.SET, turn,
             reason_event_id=session_event, visibility_scope=Visibility.PUBLIC)
    )
    outcome.authority_after = authority

    applied = apply_batch(
        conn,
        DiffBatch(
            # The body proposed; the engine writes. `organisation` is not among the roles
            # `diff_batch` accepts, which is why this layer needed no change to that
            # closed enum.
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, proposer_nation_id=org.nation_id,
            diffs=tuple(diffs + authority_diffs),
        ),
    )
    if applied.applied:
        outcome.applied = len(diffs) + len(authority_diffs)
    else:
        # **And the consequences go with it.** `authorise_force` is the one action whose
        # effect is an *event* rather than a diff -- it hands every member a casus belli --
        # and those events are written before the batch, because a diff must cite a cause
        # that already exists. So a batch that was refused used to leave every member
        # holding a free hand to attack the accused, granted by a resolution the world has
        # no record of, while `convene` reported the vote as carried. Retracted rather than
        # left, exactly as `doctrines.adopt` retracts the event behind a shift that could
        # not be charged for.
        if consequences:
            unrecord_events(conn, tuple(consequences))
        # **And it is not reported as carried.** Nothing was written -- not the measure, not
        # the standing -- so a `result` of "passed" and an `authority_after` the database
        # never held are the same lie in two fields. The session itself stays on the record,
        # because the body did meet: that event is what the trigger dedupe reads, and
        # retracting it would put the identical trigger back on every following turn, which
        # is the loop `_record_decline` exists to prevent.
        outcome.result = ""
        outcome.applied = 0
        outcome.authority_after = outcome.authority_before
        outcome.problems.append(f"the resolution could not be recorded: {applied.reason}")
    return outcome


def _record_decline(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    org: Organisation,
    trigger: Trigger,
    outcome: SessionOutcome,
    turn: int,
    date: str,
) -> None:
    """Put a session that resolved nothing on the record, exactly as a resolved one is.

    **A decline was invisible, and invisibility had a mechanical cost as well as a
    historical one.** The trigger dedupe asks whether any organisation-authored event
    cites this cause -- that is what stops a body convening about the same war twice --
    and the early return wrote no event at all. So a Council that declined to act was asked
    about the identical trigger on every subsequent turn, forever: a mock run produced *the
    United Nations Security Council resolved nothing* on turn after turn, which is that
    loop, not a body with nothing to say.

    Priced at :data:`AUTH_FAILED`, the same as a vote that failed, because that is the
    same event from outside the room: the body was asked and did not act. Convening is
    recorded too, so `convened_turn` means "last sat" rather than "last agreed".
    """
    told = f"{_names(conn, (org.nation_id,))[org.nation_id]} met and resolved nothing."
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.DIPLOMATIC_MESSAGE, origin=EventOrigin.ENGINE,
        actor_nation_id=org.nation_id, text=told[:600], visibility=Visibility.PUBLIC,
        # The citation is the load-bearing part: it is what the dedupe reads.
        causes=(trigger.cause_event_id,) if trigger.cause_event_id else (),
    )
    authority = max(0.0, min(100.0, org.authority + AUTH_FAILED))
    applied = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, proposer_nation_id=org.nation_id,
            diffs=(
                Diff("organisation", org.nation_id, "authority", DiffOp.SET,
                     round(authority, 2), reason_event_id=event_id,
                     visibility_scope=Visibility.PUBLIC),
                Diff("organisation", org.nation_id, "convened_turn", DiffOp.SET, turn,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            ),
        ),
    )
    if applied.applied:
        outcome.authority_after = authority
    else:
        outcome.problems.append(f"a declined session was not recorded: {applied.reason}")


def _resolution_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    org: Organisation,
    action: Action,
    trigger: Trigger,
    share: float,
    turn: int,
    date: str,
    event_id: int,
) -> tuple[list[Diff], list[int]]:
    """Turn a carried resolution into bounded, engine-chosen consequences.

    Returns ``(diffs, event_ids)``. The second is every event this minted as a consequence
    of the resolution, so that a batch which does not apply can take them back with it --
    an effect written as an event is still an effect, and it must not outlive the vote that
    produced it. See the retraction in :func:`convene`.
    """
    from ..state.permissions import may_write

    diffs: list[Diff] = []
    consequences: list[int] = []
    members = [m for m, _p in members_of(conn, org.nation_id) if m != trigger.accused]
    names = _names(conn, (trigger.accused, org.nation_id))

    if action.id == "condemn":
        for member in members:
            allowed, _reason = may_write(Role.ORGANISATION, "relation", "score")
            if not allowed:  # pragma: no cover - the allowlist is asserted by a test
                continue
            diffs.append(
                Diff("relation", f"{member}|{trigger.accused}", "score", DiffOp.ADJUST,
                     -round(CONDEMN_REGARD * share, 2), reason_event_id=event_id,
                     visibility_scope=Visibility.PUBLIC)
            )

    elif action.id == "sanction":
        # A row rather than a one-shot hit: this is the only standing state the layer
        # introduces, and it is what makes a sanction something a power lives under
        # rather than something that happened to it once.
        diffs.append(
            Diff("sanction", f"s_{org.nation_id}_{trigger.accused}_{turn}", "__insert__",
                 DiffOp.INSERT,
                 {
                     "world_id": world_id,
                     "organisation_id": org.nation_id,
                     "target_nation_id": trigger.accused,
                     "severity": round(share, 2),
                     "opened_turn": turn,
                     # Both, for the reason `commitment.expires_on` keeps its turn: a save
                     # older than schema 36 has no date to convert from.
                     "until_turn": turn + SANCTION_MONTHS,
                     "until_on": after_months(date, SANCTION_MONTHS),
                 },
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )

    elif action.id == "authorise_force":
        # The same primitive Article 5 uses: an engine-authored casus belli exempts its
        # holder from the unprovoked-war legitimacy cost for five turns. Nobody is sent
        # to war; going becomes free.
        for member in members:
            consequences.append(record_event(
                conn, world_id=world_id, turn=turn, date=date,
                kind=EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE,
                actor_nation_id=member,
                text=(
                    f"{names[org.nation_id]} has authorised action against "
                    f"{names[trigger.accused]}."
                )[:600],
                visibility=Visibility.PUBLIC, causes=(event_id,),
                participants=(("actor", member), ("target", trigger.accused)),
            ))

    elif action.id == "demand_ceasefire":
        belligerents = conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            " AND at_war = 1 ORDER BY nation_b LIMIT 1",
            (world_id, trigger.accused),
        ).fetchone()
        if belligerents:
            # **Diffs, not direct SQL, and this was the third instance of the same bug.**
            # `commitment` is fingerprinted, so a demanded ceasefire written straight to
            # the table survived a rewind and moved the digest -- decision 132 fixed
            # exactly this in `diplomacy` and `treaties` and did not reach here, because
            # nobody grepped for other writers. The id comes from the same allocator the
            # other writer uses; see there for why `MAX(id) + 1` is the correct choice
            # rather than a tolerated one.
            from .diplomacy import next_commitment_id

            commitment_id = next_commitment_id(conn)
            diffs.append(
                Diff("commitment", str(commitment_id), "__insert__", DiffOp.INSERT,
                     {
                         "world_id": world_id,
                         "kind": CommitmentKind.CEASEFIRE.value,
                         "status": "pending",
                         "condition_text": (
                             f"{names[org.nation_id]} requires a ceasefire between "
                             f"{names[trigger.accused]} and {belligerents['nation_b']}."
                         )[:1000],
                         "source_event_id": event_id,
                         "created_turn": turn,
                     },
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            diffs.extend(
                Diff("commitment_nation", f"{commitment_id}|{party}", "__insert__",
                     DiffOp.INSERT, {"role": "party"},
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for party in (trigger.accused, str(belligerents["nation_b"]))
            )
    return diffs, consequences


# ------------------------------------------------------------------------- sanctions


def sanction_drag(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int, date: str = ""
) -> float:
    """Combined severity of every measure standing against this power. 0.0 if none.

    Lapsed measures are filtered by the query rather than deleted. A row that expires
    without anybody writing anything is a row a rewind never has to put back -- and the
    alternative, a per-turn sweep of DELETE diffs, would add write volume to say nothing.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(severity), 0) AS s FROM sanction"
        f" WHERE world_id = ? AND target_nation_id = ? AND {_STILL_STANDING}",
        (world_id, nation_id, date, turn),
    ).fetchone()
    return float(row["s"]) if row else 0.0


def apply_sanctions(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> list[str]:
    """Charge every standing measure, once per turn. Returns what was charged, for the report.

    Deliberately conservative: at severity 1.0 a measure takes **3% of the economy and 6%
    of the treasury a month**, which over the eight turns a measure stands is -21% and -39%
    -- a headwind rather than a collapse. (The figures here used to be quoted as absolutes,
    "about a point of economy and two and a half of treasury"; the rates are proportional,
    so that was true of a power with an economy near 33 and misleading for anybody larger.)

    **The rate compounds rather than scaling linearly, and that was a real bug.** Every
    other per-turn rate in the engine goes through :func:`atlas.engine.pacing.compounded`
    and this one multiplied by the span, which agrees at a month and diverges as the turn
    grows: at a yearly turn a severity-2 measure produced a *negative* treasury multiplier
    and **emptied the purse outright in a single turn**, where twelve monthly charges leave
    21% of it. Severity 3 did the same to the economy. Measured, both ways, and the module
    that exists to prevent exactly this was one import away.
    """
    from .pacing import compounded, span_of

    span = span_of(days)
    targets = conn.execute(
        f"""
        SELECT target_nation_id AS nation_id, SUM(severity) AS severity,
               MIN(organisation_id) AS org
          FROM sanction WHERE world_id = ? AND {_STILL_STANDING}
         GROUP BY target_nation_id ORDER BY target_nation_id
        """,
        (world_id, date, turn),
    ).fetchall()
    if not targets:
        return []

    charged: list[str] = []
    diffs: list[Diff] = []
    for row in targets:
        nation_id, severity = str(row["nation_id"]), float(row["severity"])
        economy = conn.execute(
            "SELECT economy, treasury FROM nation WHERE id = ?", (nation_id,)
        ).fetchone()
        if economy is None:
            continue

        # **What is actually charged, decided before anything is announced.** The event used
        # to be written first, for every standing measure, every turn -- so a power already
        # ground down to the economy floor with an empty treasury published *Sanctions
        # continue to bite* on turn after turn with not one diff behind it. Measured at 240
        # yearly turns: 240 public events, zero writes. That is a false entry in the
        # chronicle rather than a cosmetic one, because the digest and the news ranking read
        # these, so a measure that had stopped doing anything went on being the loudest
        # thing happening to that country forever.
        charges: list[tuple[str, float]] = []
        # **Floored, and written as the floored value rather than as a delta.** A long
        # sanction should grind a country down, not delete it -- and an ADJUST of a
        # rounded delta walks past the floor by floating-point dust after a few turns
        # (measured: economy landed on 0.9999999999999999 against a floor of 1.0). SET of
        # a computed target holds the floor exactly, and rewinds identically, because a
        # diff records the value on both sides of the write whatever the op was.
        target = max(
            SANCTION_ECONOMY_FLOOR,
            float(economy["economy"])
            * (1.0 - compounded(SANCTION_ECONOMY_RATE * severity, span)),
        )
        if target < float(economy["economy"]):
            charges.append(("economy", round(target, 2)))
        purse = max(
            0.0,
            float(economy["treasury"])
            * (1.0 - compounded(SANCTION_TREASURY_RATE * severity, span)),
        )
        if purse < float(economy["treasury"]):
            charges.append(("treasury", round(purse, 2)))
        if not charges:
            # Standing, and biting nothing: there is no longer anything here to take.
            continue

        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.ECONOMIC_REFORM, origin=EventOrigin.ENGINE,
            actor_nation_id=nation_id,
            text=f"Sanctions continue to bite ({severity:.1f} severity).",
            visibility=Visibility.PUBLIC,
        )
        diffs.extend(
            Diff("nation", nation_id, field, DiffOp.SET, value,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for field, value in charges
        )
        charged.append(f"{nation_id} (severity {severity:.1f})")

    if diffs:
        apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None, diffs=tuple(diffs)),
        )
    return charged


def _names(conn: sqlite3.Connection, ids) -> dict[str, str]:
    out: dict[str, str] = {}
    for nation_id in {i for i in ids if i}:
        row = conn.execute(
            "SELECT name FROM nation WHERE id = ?", (nation_id,)
        ).fetchone()
        out[nation_id] = row["name"] if row else nation_id
    return out
