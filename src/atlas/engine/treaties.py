"""The life of an agreement, including the part where somebody tears it up.

Decision 120 made an active commitment *bind*: `relation.treaty_status` became its
materialised view, so the invasion guard, the gambit odds, the router and four briefings
all read one fact. What it did not do is let an agreement **end badly**. Nothing in the
engine had ever written ``status = 'broken'`` -- the value has been in the CHECK constraint
since schema 1 with no code path reaching it -- so a pact could be signed, honoured, and
never violated. A treaty you cannot betray is set dressing.

**Detection is a query, not a claim.** The breach that matters is *being at war with a
power you have an active pact with*, and that is derivable from state -- so no model can
assert a betrayal that did not happen, and none can conceal one that did. It runs after the
fighting resolves, so a war declared this turn is a breach this turn.

**A breach does five things, and all five or it is not modelled**: marks the commitment
broken and records *who*; costs the violator regard with the other parties and legitimacy at
home; hands the injured party a casus belli, which the engine already gates war declarations
on; takes `treaty_status` back down, which decision 120's recompute does for free; and
reaches the turn report, high, because it is the most consequential thing a diplomatic
system produces.

**Only the three pacts that promise not to fight can be violated by fighting.** A
``ceasefire`` or ``peace_treaty`` between two nations at war is not a breach -- those are the
agreements that exist *because* of the war, and reading them as violated by it would mean
every peace ever signed betrays itself the moment it is recorded.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import (
    CommitmentKind,
    CommitmentStatus,
    DiffOp,
    EventKind,
    EventOrigin,
    Role,
    Visibility,
)
from .bounds import absorbable
from .pacing import after_months, days_between

__all__ = [
    "BREACHABLE",
    "BreachReport",
    "Term",
    "TermReport",
    "STANDING_CLAUSES",
    "STANDING_KINDS",
    "assess_breaches",
    "fulfillment_blockers",
    "breach",
    "expire_commitments",
    "honour_terms",
    "settle_fulfilled",
    "term_diffs",
    "terms_of",
]

#: The kinds a war can violate *when the agreement did not say what it promised*. See the
#: module docstring for why a ceasefire is not here, and :func:`terms_of` for why this is
#: now a fallback rather than the rule.
BREACHABLE: frozenset[str] = frozenset(
    {"non_aggression_pact", "defensive_pact", "alliance"}
)

#: What tearing up your own signature costs, with each other party to the agreement. Large
#: and blunt: this is the loudest thing a state can do short of a declaration.
_REGARD_COST = -35.0

#: ...and at home. A government that breaks its word is worth less as a signatory to
#: everyone, including its own public. Smaller than the regard cost because the domestic
#: audience is not the injured party.
_LEGITIMACY_COST = -6.0


#: How long a promise to hand over a province has before it is a broken promise. Long
#: enough that a government has to mean it, and it is a *term* of the agreement rather than
#: a constant the engine imposes, so a later negotiation can name its own.
#:
#: **World time, and it was on the wrong clock until Codex found it.** This was
#: `CESSION_GRACE_TURNS` and it was classified as the one deliberate exception on the
#: player's clock -- the argument being that it is about a player noticing. That argument
#: was wrong: what the grace period measures is how long a *government* has to hand a
#: province over before not having done so is a breach, which is elapsed history and
#: nothing to do with who is watching. Three turns meant three months at monthly pacing and
#: three **years** at yearly, from the same agreement, which is the exact defect the whole
#: world-clock pass existed to remove.
CESSION_GRACE_MONTHS = 3


@dataclass(frozen=True, slots=True)
class Term:
    """One clause of an agreement, in a form the engine can check.

    ``condition_text`` is prose and always was, which is why exactly three of the nine
    commitment kinds could ever be broken: the engine knew what a pact promised because it
    was written into `BREACHABLE`, and knew nothing at all about what a cession, an access
    grant or a trade agreement promised. A term is the machine-readable half.
    """

    clause: str
    #: Who owes it. Empty means every party -- which is what a mutual promise not to fight
    #: is, and writing it out per party would make the same promise look like two.
    obliged_id: str = ""
    #: What it is about: a region id, a good, or empty.
    subject_id: str = ""
    value: float = 0.0
    #: When it falls due, as a turn. ``None`` for a standing obligation, which is most of
    #: them. **Kept, and no longer the thing that is read**: a save written before schema 35
    #: has a turn and no date, and there is nothing to convert it from -- the same argument
    #: `commitment.expires_turn` and `sanction.until_turn` make next door.
    due_turn: int | None = None
    #: ...and as a world date, which is what a deadline actually is. ``""`` for a standing
    #: obligation and for any term written before schema 35.
    due_on: str = ""
    seq: int = 0


@dataclass(slots=True)
class BreachReport:
    assessed: int = 0
    #: One line per agreement torn up this turn, in the words the report shows.
    breached: list[str] = field(default_factory=list)
    #: Agreements whose term simply ran out. Kept apart: an expiry is not a betrayal, and
    #: a report that showed them together would teach a player to distrust the wrong power.
    expired: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TermReport:
    """What the standing agreements moved this turn, and what they could not.

    A list of headlines was enough while the only outcome considered was success.
    `honour_terms` returned `[]` on a refused batch -- indistinguishable from "there was
    nothing to do" -- so a route that could not be opened was reported to nobody and
    re-attempted silently every turn afterwards. That is the same defect the breach path
    had and the same fix: the failure is a sentence the player reads.
    """

    #: One line per route opened or closed, in the words the turn report shows.
    honoured: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _parties(conn: sqlite3.Connection, commitment_id: int) -> list[str]:
    return [
        str(row["nation_id"])
        for row in conn.execute(
            "SELECT nation_id FROM commitment_nation WHERE commitment_id = ?"
            " ORDER BY nation_id",
            (commitment_id,),
        )
    ]


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def _legitimacy_of(conn: sqlite3.Connection, nation_id: str) -> float:
    row = conn.execute(
        "SELECT legitimacy FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    return float(row["legitimacy"]) if row else 0.0


def terms_of(conn: sqlite3.Connection, commitment_id: int, kind: str) -> list[Term]:
    """What this agreement promised.

    **An agreement with no recorded terms is read as carrying the terms its kind implies**,
    and that fallback is the whole compatibility story. For the three pacts the implied term
    is `no_war`, which is exactly the rule `BREACHABLE` hardcoded before terms existed -- so
    every commitment in every world written before schema 16, and every one a scenario
    seeds, behaves precisely as it did. Only agreements made in play carry explicit terms.

    Deliberately *not* implying `open_trade` for a term-less `trade_agreement`: a seeded
    trade agreement would then open a route the world was not seeded with, which would move
    both eras' fingerprints to no purpose. A seeded world keeps its seeded routes.
    """
    try:
        rows = conn.execute(
            "SELECT clause, obliged_id, subject_id, value, due_turn, due_on, seq"
            "  FROM commitment_term WHERE commitment_id = ? ORDER BY seq",
            (commitment_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return [
            Term(clause=str(r["clause"]), obliged_id=str(r["obliged_id"] or ""),
                 subject_id=str(r["subject_id"] or ""), value=float(r["value"] or 0.0),
                 due_turn=r["due_turn"], due_on=str(r["due_on"] or ""),
                 seq=int(r["seq"]))
            for r in rows
        ]
    if kind in BREACHABLE:
        return [Term(clause="no_war")]
    return []


def term_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    commitment_id: int,
    kind: str,
    parties: tuple[str, ...],
    turn: int,
    event_id: int,
    scope: Visibility,
    date: str = "",
    cede_region: str = "",
    cede_from: str = "",
) -> list[Diff]:
    """The terms a newly recorded agreement carries, as insert diffs.

    **Engine-derived, with exactly one exception, and the exception is the interesting
    part.** `no_war` and `open_trade` follow from the kind and from what the parties
    actually have -- the same discipline that lets a nuclear launch take no effect verbs
    from whoever proposed it. `cede_region` cannot: nothing but the transcript knows *which*
    province was promised, so the extractor may name one and the engine refuses it unless
    the region resolves and is held by the party promising it. **A model may record a
    promise; only the engine may move ground.**
    """
    from .trade import worth_selling

    if len(parties) < 2:
        return []
    diffs: list[Diff] = []
    terms: list[Term] = []

    if kind in BREACHABLE:
        terms.append(Term(clause="no_war"))
    elif kind == CommitmentKind.TRADE_AGREEMENT.value:
        # Both directions are considered and only the ones with cargo behind them are
        # written. An agreement between two powers who each have what the other needs moves
        # goods both ways; one where only one side has a surplus moves them one way; one
        # where neither does is a diplomatic fact and no more.
        for seller, buyer in ((parties[0], parties[1]), (parties[1], parties[0])):
            offer = worth_selling(conn, world_id, seller, buyer)
            if offer is None:
                continue
            good, volume = offer
            terms.append(Term(clause="open_trade", obliged_id=seller,
                              subject_id=good, value=volume))
    elif kind == CommitmentKind.TERRITORY_CESSION.value and cede_region:
        owner = conn.execute(
            "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ? AND id = ?",
            (world_id, cede_region),
        ).fetchone()
        # The promise has to be the promiser's to make. Without this a staff could record
        # that somebody *else* would hand over a province, and the engine would spend three
        # turns waiting to charge the wrong government with breaking it.
        if owner is not None and str(owner["holder"]) == cede_from and cede_from in parties:
            terms.append(Term(clause="cede_region", obliged_id=cede_from,
                              subject_id=cede_region,
                              due_turn=turn + int(CESSION_GRACE_MONTHS),
                              due_on=after_months(date, CESSION_GRACE_MONTHS)))

    for seq, term in enumerate(terms):
        diffs.append(
            Diff("commitment_term", f"{commitment_id}|{seq}", "__insert__", DiffOp.INSERT,
                 {
                     "world_id": world_id,
                     "clause": term.clause,
                     "obliged_id": term.obliged_id,
                     "subject_id": term.subject_id,
                     "value": term.value,
                     "due_turn": term.due_turn,
                     "due_on": term.due_on,
                 },
                 reason_event_id=event_id, visibility_scope=scope)
        )
    return diffs


def breach(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    commitment_id: int,
    violator: str,
    turn: int,
    date: str,
    why: str = "",
) -> str | None:
    """Record that ``violator`` broke an agreement, and make it cost something.

    Returns the line for the turn report, or ``None`` if the commitment was not there to
    break. Idempotent by construction: a commitment already `broken` is not active, and
    only active commitments are offered here.
    """
    row = conn.execute(
        "SELECT kind, status FROM commitment WHERE world_id = ? AND id = ?",
        (world_id, commitment_id),
    ).fetchone()
    if row is None or str(row["status"]) != CommitmentStatus.ACTIVE.value:
        return None

    parties = _parties(conn, commitment_id)
    injured = [nation_id for nation_id in parties if nation_id != violator]
    kind = str(row["kind"]).replace("_", " ")
    told = f"{_name(conn, violator)} broke the {kind} with " + ", ".join(
        _name(conn, nation_id) for nation_id in injured
    ) + (f": {why}" if why else ".")

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TREATY_BROKEN, origin=EventOrigin.ENGINE,
        actor_nation_id=violator, text=told[:600], visibility=Visibility.PUBLIC,
        participants=(("actor", violator), *(("aggrieved", n) for n in injured)),
    )

    # **The grievance is authored per injured party, and cited to the breach.** The war
    # guard reads engine-authored `casus_belli` events keyed on the *aggrieved* nation, so
    # one event naming the violator would give the grievance to the wrong side.
    grievances = [
        record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE,
            actor_nation_id=nation_id,
            text=f"{_name(conn, violator)} broke the {kind} it had signed with "
                 f"{_name(conn, nation_id)}.",
            visibility=Visibility.PUBLIC,
            causes=(event_id,),
            participants=(("actor", nation_id), ("target", violator)),
        )
        for nation_id in injured
    ]

    # **The status change is a diff now, not an UPDATE.** PT31 caught what the UPDATE cost:
    # `commitment` is fingerprinted, so a breach recorded outside the diff pipeline moved
    # the digest and did not unwind. It rides in the same batch as its consequences, so a
    # refused batch leaves the agreement standing rather than broken-and-unpunished.
    diffs: list[Diff] = [
        Diff("commitment", str(commitment_id), "status", DiffOp.SET,
             CommitmentStatus.BROKEN.value,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("commitment", str(commitment_id), "breached_by_nation_id", DiffOp.SET,
             violator, reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("commitment", str(commitment_id), "breached_turn", DiffOp.SET, turn,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("commitment", str(commitment_id), "resolved_turn", DiffOp.SET, turn,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        # **Charged against what there is, because the engine must not author a penalty its
        # own rules will refuse.** ECON-003 holds legitimacy inside 0-100, so a violator
        # with 2.0 left could not be charged 6.0 -- and the *whole* breach was thrown away
        # over the overshoot: the treaty stayed active, nobody was charged anything, and the
        # only thing that happened was the attempt. Losing all the standing you have left is
        # also the truer reading. A government at zero legitimacy cannot be disgraced
        # further by tearing up one more signature; it is already there.
        Diff("nation", violator, "legitimacy", DiffOp.ADJUST,
             absorbable(conn, "nation", violator, "legitimacy", _LEGITIMACY_COST),
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
    ]
    for nation_id in injured:
        diffs.append(
            Diff("relation", f"{nation_id}|{violator}", "score", DiffOp.ADJUST,
                 _REGARD_COST, reason_event_id=event_id,
                 visibility_scope=Visibility.PUBLIC)
        )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        # **A breach happens completely or not at all.**
        #
        # The five things a breach does are one act, and the events have to be written
        # first because every diff must cite one (CORE-001). So a refused batch used to
        # leave the *narrative* of a betrayal behind with none of its consequences: the
        # commitment stayed `active`, nobody was charged anything, and the record said the
        # treaty had been torn up. Reproduced by giving the violator 2.0 legitimacy, where
        # the -6.0 charge takes it below its floor and the batch is refused.
        #
        # The casus belli is the half that matters. It is a standing grant of permission to
        # go to war, keyed on the aggrieved nation and read by the war guard for five turns
        # -- so a failed breach handed a real licence for a betrayal that never happened.
        #
        # These are our own writes from moments ago, not history: nothing has cited them,
        # the batch that would have is rolled back, and their visibility rows go with them.
        unrecord_events(conn, [event_id, *grievances])
        conn.commit()
        return None

    # ...and the column the rest of the engine reads follows from the commitments again.
    from .diplomacy import sync_treaty_status

    for nation_id in injured:
        sync_treaty_status(conn, world_id, (violator, nation_id), event_id=event_id)
    conn.commit()
    return told


def honour_terms(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> TermReport:
    """Do the things an active agreement's terms say the world should be doing.

    **This is where a `trade_agreement` stops being a word and starts moving cargo.**
    `trade_route` was written in exactly one place in the whole tree -- the seeder -- so a
    route could be cut by war, blockade or sanction and could never be opened by anybody.
    Trade was a one-way ratchet toward autarky and a negotiated agreement moved
    `relation.treaty_status` and not one barrel of oil.

    **Idempotent and derived, rather than fired on a transition.** There is no single moment
    an agreement becomes active -- a model may write `commitment.status` in any batch -- so
    reacting to the change would mean finding every place it could happen. Instead this asks,
    every turn, whether the world matches what the standing agreements say, and writes the
    difference. That also means it repairs itself: an agreement activated during a turn whose
    batch was refused is honoured the next turn rather than never.

    Route ids are derived from the term that opened them, so a rewind that removes the route
    and a re-run that re-opens it produce the same id and the same digest.
    """
    report = TermReport()
    told = report.honoured
    try:
        rows = conn.execute(
            "SELECT c.id AS commitment_id, c.kind AS kind, c.status AS status,"
            "       t.seq AS seq, t.obliged_id AS obliged_id, t.subject_id AS subject_id,"
            "       t.value AS value"
            "  FROM commitment_term t JOIN commitment c ON c.id = t.commitment_id"
            " WHERE t.world_id = ? AND t.clause = 'open_trade' ORDER BY t.commitment_id, t.seq",
            (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return report
    if not rows:
        return report

    open_now: list[tuple[str, sqlite3.Row]] = []
    close_now: list[tuple[str, sqlite3.Row]] = []
    for row in rows:
        route_id = f"tr_c{int(row['commitment_id'])}_{int(row['seq'])}"
        exists = conn.execute(
            "SELECT 1 FROM trade_route WHERE id = ?", (route_id,)
        ).fetchone() is not None
        active = str(row["status"]) == CommitmentStatus.ACTIVE.value
        if active and not exists:
            open_now.append((route_id, row))
        elif not active and exists:
            close_now.append((route_id, row))
    if not open_now and not close_now:
        return report

    # The terms are settled at the moment the agreement is honoured, by the engine, from
    # the same state that decided the volume. **Both halves of a deal a model proposes are
    # the engine's answer**: what moves, and what it costs. A route opened without a price
    # would be barter until the next turn's assessment noticed it, which would make every
    # freshly signed agreement briefly free.
    from . import pricing
    from .trade import pricing_columns_present

    priced = pricing.enabled() and pricing_columns_present(conn)
    lanes: dict[tuple[str, str], tuple[bool, str]] = {}

    diffs: list[Diff] = []
    for route_id, row in open_now:
        parties = _parties(conn, int(row["commitment_id"]))
        seller = str(row["obliged_id"])
        buyer = next((p for p in parties if p != seller), "")
        if not buyer:
            continue
        good = str(row["subject_id"])
        columns = {
            "world_id": world_id, "seller_id": seller, "buyer_id": buyer,
            "good": good, "volume": float(row["value"]),
            "opened_turn": turn,
        }
        terms = None
        if priced:
            terms = pricing.price_route(conn, world_id, seller, buyer, good, lanes=lanes)
            columns["unit_price"] = terms.unit_price
            columns["priced_turn"] = turn
        diffs.append(
            Diff("trade_route", route_id, "__insert__", DiffOp.INSERT, columns)
        )
        told.append(
            f"{_name(conn, seller)} begins shipping {good} to "
            f"{_name(conn, buyer)} under their agreement"
            + (f", at {terms.unit_price:.2f} a unit" if terms is not None else "")
        )
    for route_id, row in close_now:
        # The applier reads the row it is about to remove and keeps every column, which is
        # what a rewind restores from -- so a closed route re-opens exactly as it was.
        diffs.append(Diff("trade_route", route_id, "__delete__", DiffOp.DELETE, None))
        told.append(
            f"the {str(row['subject_id'])} shipments from {_name(conn, str(row['obliged_id']))} "
            "stop: the agreement behind them no longer stands"
        )
    if not diffs:
        told.clear()
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        text="; ".join(told)[:600], visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=tuple(
                      Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                      for d in diffs
                  )),
    )
    if not outcome.applied:
        # **The narrative goes with the cargo that did not move.** The event is written
        # first because every diff must cite one, so a refused batch would otherwise leave
        # "Karthos begins shipping oil to Aurelia under their agreement" in the history --
        # which every later briefing quotes back, and which the digest carries forward, so
        # both staffs would go on planning around a route that does not exist. Exactly the
        # hole the breach path had, and `unrecord_events` is here because of it.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(
            "the standing agreements could not be honoured this turn "
            f"({'; '.join(told)[:200]}): {outcome.reason[:200]}"
        )
        told.clear()
        return report
    conn.commit()
    return report


def _violation(
    conn: sqlite3.Connection, world_id: str, *, term: Term, parties: list[str], turn: int,
    since_turn: int = 0, date: str = "",
) -> tuple[str, str] | None:
    """Whether this term is being broken right now, and by whom.

    **Every test is a query over state, never a claim.** That is the property decision 132
    built the breach detector on and the reason a term table does not weaken it: a model may
    record *what was promised* and has no say whatever in whether it was kept.
    """
    if term.clause == "no_war":
        marks = ",".join("?" * len(parties))
        at_war = conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
            f"   AND nation_a IN ({marks}) AND nation_b IN ({marks}) LIMIT 1",  # noqa: S608
            (world_id, *parties, *parties),
        ).fetchone()
        if at_war is None:
            return None
        violator = _aggressor(conn, world_id, parties, since_turn=since_turn)
        return (violator, "they went to war anyway") if violator else None

    if term.clause == "open_trade":
        # A trade agreement is not a promise not to fight, and this is not that rule
        # wearing a different hat: it is the narrower claim that a state which goes to war
        # with its customer has stopped supplying it. The route closes as a consequence,
        # through `honour_terms`, because the agreement behind it is no longer active.
        other = [p for p in parties if p != term.obliged_id]
        if not other:
            return None
        at_war = conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
            "   AND nation_a = ? AND nation_b = ? LIMIT 1",
            (world_id, term.obliged_id, other[0]),
        ).fetchone()
        if at_war is None:
            return None
        # The same floor the `no_war` branch uses: a war declared before the agreement
        # was signed cannot have broken it, or a trade deal agreed mid-war is breached the
        # turn it lands and the aggressor is charged again for every one signed.
        violator = _aggressor(conn, world_id, parties, since_turn=since_turn)
        return (violator, "they went to war with the power they had agreed to supply") \
            if violator else None

    if term.clause == "cede_region":
        # **The date first, and the turn only when there is no date.** A term written before
        # schema 35 has no `due_on` and its turn is all there is; anything written since is
        # judged on the calendar, so the same agreement falls due on the same day whether
        # the player is watching a month or a year at a time.
        if term.due_on:
            waited = days_between(term.due_on, date)
            if waited is None or waited < 0:
                return None
        elif term.due_turn is None or turn < int(term.due_turn):
            return None
        holder = conn.execute(
            "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ? AND id = ?",
            (world_id, term.subject_id),
        ).fetchone()
        if holder is None or str(holder["holder"]) != term.obliged_id:
            return None
        return (
            term.obliged_id,
            f"they still hold {_region_name(conn, term.subject_id)}, which they agreed to "
            "hand over",
        )
    return None


#: Clauses that are **standing**: a promise you go on keeping, which is never *finished*.
#: You stop keeping it, and that is a breach or an expiry -- never a completion.
STANDING_CLAUSES: frozenset[str] = frozenset({"no_war", "open_trade"})

#: Clauses that are **one-off**: a thing you do once, after which there is nothing left owed.
ONE_OFF_CLAUSES: frozenset[str] = frozenset({"cede_region"})

#: Kinds that are standing whatever terms they carry, including none.
#:
#: The terms cannot always say this. A `trade_agreement` with no recorded terms implies
#: nothing (deliberately -- see :func:`terms_of`), and a `military_access` grant is inert by
#: design, so neither has a term to read. Both are nevertheless permissions that *continue*,
#: and a continuing permission is not something you finish.
STANDING_KINDS: frozenset[str] = frozenset({
    "non_aggression_pact", "alliance", "defensive_pact",
    "trade_agreement", "military_access", "ceasefire",
    # A tribute is a stream until somebody says otherwise. If a scenario ever means a single
    # payment it should say so with a term, and then this is the wrong list for it.
    "tribute",
})


def _one_off_is_done(conn: sqlite3.Connection, world_id: str, term: Term) -> bool:
    """Whether a one-off obligation has actually been discharged.

    Only `cede_region` is one-off today, and it is done when the ground has moved: the party
    that promised it no longer holds it. That is the same query `_violation` asks to decide
    the opposite, which is the point -- one fact, read once, rather than two notions of
    "handed over" that can disagree.
    """
    if term.clause != "cede_region":
        return False
    holder = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE world_id = ? AND id = ?",
        (world_id, term.subject_id),
    ).fetchone()
    return holder is not None and str(holder["holder"]) != term.obliged_id


def fulfillment_blockers(
    conn: sqlite3.Connection, world_id: str, *, commitment_id: int, kind: str
) -> list[str]:
    """Why this agreement may not be recorded as `fulfilled`. Empty means it may.

    **"Fulfilled" means the promise is discharged**, and most promises here are not the kind
    that can be. A non-aggression pact is not *completed* by a year of peace; a supply
    arrangement is not *finished* by a month of cargo. Those end by being broken, by expiring,
    or by being deliberately terminated -- and a status that says otherwise deletes a standing
    obligation while telling everybody it was honoured.

    PT34d is the case: a model marked a standing supply agreement `fulfilled` one month in,
    the engine correctly read it as no longer active and cut the trade route, and two other
    powers sent envoys the next turn asking why the shipments had stopped. The write itself is
    closed now -- `commitment.status` is engine-only -- so what this governs is **the engine's
    own** fulfillment derivation, which does not exist yet. This is its specification, and
    rule DIP-005 makes it enforceable before it is written rather than after.

    A **mixed** agreement stays active while any standing term remains, however many of its
    one-off terms are complete. That is the case the naive version gets wrong: a treaty that
    cedes a province *and* promises peace is not finished when the province moves.
    """
    if kind in STANDING_KINDS:
        return [
            f"a {kind.replace('_', ' ')} is a standing obligation: it can be broken, "
            "expire, or be terminated, but it is never completed"
        ]

    blockers: list[str] = []
    for term in terms_of(conn, commitment_id, kind):
        if term.clause in STANDING_CLAUSES:
            blockers.append(
                f"it still carries a standing `{term.clause}` term, which nothing discharges"
            )
        elif term.clause in ONE_OFF_CLAUSES:
            if not _one_off_is_done(conn, world_id, term):
                blockers.append(
                    f"its `{term.clause}` term is not discharged: {term.obliged_id} still "
                    f"holds {term.subject_id}"
                )
        else:
            # An unclassified clause is not assumed harmless. A new clause arrives in
            # `commitment_term`'s CHECK and in one of the two sets above, or it blocks
            # fulfilment until somebody decides which it is.
            blockers.append(
                f"clause `{term.clause}` is not classified as standing or one-off, so "
                "whether it can be discharged is undecided"
            )
    return blockers


def assess_breaches(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> BreachReport:
    """Find every agreement the world has made a lie of, and break it.

    **Derived rather than reported**, and now term by term rather than kind by kind: an
    agreement says what it promised and the engine reads whether the promise is being kept.
    A commitment with no terms is read as carrying the ones its kind implies, which is
    exactly the rule that was hardcoded before -- see :func:`terms_of`.

    Attribution goes to the nation the war record names as the aggressor when there is one,
    and otherwise to nobody -- an agreement cannot be broken by nobody, so an unattributable
    case is left standing rather than charged to a guess.
    """
    report = BreachReport()
    try:
        rows = conn.execute(
            "SELECT id AS commitment_id, kind AS kind, created_turn AS created_turn"
            "  FROM commitment WHERE world_id = ? AND status = ? ORDER BY id",
            (world_id, CommitmentStatus.ACTIVE.value),
        ).fetchall()
    except sqlite3.OperationalError:
        return report

    for row in rows:
        commitment_id = int(row["commitment_id"])
        kind = str(row["kind"])
        created = int(row["created_turn"] or 0)
        terms = terms_of(conn, commitment_id, kind)
        if not terms:
            continue
        report.assessed += 1
        parties = _parties(conn, commitment_id)
        if len(parties) < 2:
            continue

        for term in terms:
            found = _violation(
                conn, world_id, term=term, parties=parties, turn=turn,
                since_turn=created, date=date,
            )
            if found is None:
                continue
            violator, why = found
            told = breach(
                conn, world_id, commitment_id=commitment_id, violator=violator,
                turn=turn, date=date, why=why,
            )
            if told:
                report.breached.append(told)
            else:
                # **A breach that could not be recorded is reported, every turn it recurs.**
                # It used to be dropped on the floor: `breach` returned `None`, nothing was
                # appended, and because the commitment stays `active` and the war goes on,
                # the same failure was attempted and swallowed again on every subsequent
                # turn -- an invisible loop that could run for the rest of the game.
                #
                # Retrying is right, because the reason can clear: a violator too poor in
                # legitimacy to be charged this turn may recover by the next. Retrying
                # *silently* is not. `assess_breaches`'s problems reach the turn report
                # through `JumpReport.rejections`, so a recurring failure is visible each
                # turn rather than never.
                report.problems.append(
                    f"{_name(conn, violator)} broke commitment {commitment_id} and the "
                    "breach could not be recorded; the agreement stands and nobody has "
                    "been charged"
                )
            # One breach per agreement per turn. Two clauses of the same treaty broken by
            # the same war is one betrayal, and charging it twice would say a power that
            # signed a longer document behaved worse.
            break
        else:
            # Nothing was breached. If a war-clause agreement is nevertheless contradicted
            # by a war, say **which** of the two reasons it is, because they call for
            # different things: one is a war with no author, and the other is an agreement
            # signed by two powers who were already fighting. Reported every turn it
            # recurs, for the same reason the failed-breach path is -- an anomaly that says
            # nothing is an anomaly nobody fixes.
            #
            # Widened past `BREACHABLE` deliberately: a `trade_agreement` carrying an
            # explicit `open_trade` term is breachable through its terms and is not in that
            # set, so this case was silent for exactly the agreements a negotiation makes.
            if not _contradicted_by_war(conn, world_id, parties):
                continue
            if not any(term.clause in {"no_war", "open_trade"} for term in terms):
                continue
            if _aggressor(conn, world_id, parties) is None:
                report.problems.append(
                    f"commitment {commitment_id} is contradicted by a war nobody is "
                    "recorded as having started; it stands"
                )
            else:
                report.problems.append(
                    f"commitment {commitment_id} was agreed on turn {created} between "
                    "powers already at war, so that war cannot have broken it; it stands"
                )
    return report


def _contradicted_by_war(
    conn: sqlite3.Connection, world_id: str, parties: list[str]
) -> bool:
    marks = ",".join("?" * len(parties))
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
        f"   AND nation_a IN ({marks}) AND nation_b IN ({marks}) LIMIT 1",  # noqa: S608
        (world_id, *parties, *parties),
    ).fetchone() is not None


def _region_name(conn: sqlite3.Connection, region_id: str) -> str:
    row = conn.execute("SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
    return str(row["name"]) if row else region_id


def _aggressor(
    conn: sqlite3.Connection, world_id: str, parties: list[str], *, since_turn: int = 0
) -> str | None:
    """Who started it, from the war declaration the engine wrote.

    ``since_turn`` is the turn the agreement was made. **An act cannot break an agreement
    that did not exist when it happened**, and without that floor a pact signed *during* a
    war was contradicted the moment it became active and charged to whoever had declared
    that war several turns earlier. Reproduced, and it is farmable: a power under invasion
    that keeps offering pacts its enemy signs takes 35 regard and 6 legitimacy off the
    aggressor every time one lands, for the single declaration that started the war. Three
    signatures floor the regard at -100.

    Same turn still counts, because declaring war on the day you signed is a betrayal and
    the sharpest one there is.

    The declaration's `actor_nation_id` is the aggressor, and it is engine-authored, so
    this cannot be gamed by a model claiming to have been provoked.

    **It has to be a declaration against *another party*, and that was the bug.** This took
    the most recent `war_declared` authored by any signatory without checking whom they had
    declared war on, so an unrelated war contaminated the attribution. Reproduced: Aurelia
    declares war on Karthos, breaking their pact; Karthos then declares war on Veyra, which
    has nothing to do with it; and because Karthos's declaration is the more recent, **the
    pact Aurelia broke was charged to Karthos** -- 35 regard and 6 legitimacy off the wrong
    country, with the real violator handed a casus belli against its victim.

    `event_participant` is what makes the question answerable: a declaration now names its
    target, so this asks for one whose target is also a party.

    **An event with no participants falls back to the old query**, because every event
    written before schema 17 has none. An old save is attributed exactly as it was -- which
    is imperfect and is what it already lived with -- and a world playing on this build gets
    the precise answer from the first declaration it writes.
    """
    marks = ",".join("?" * len(parties))
    row = conn.execute(
        f"""
        SELECT e.actor_nation_id FROM event e
          JOIN event_participant p ON p.event_id = e.id AND p.role = 'target'
         WHERE e.world_id = ? AND e.kind = ? AND e.origin = 'ENGINE'
           AND e.turn >= ?
           AND e.actor_nation_id IN ({marks})
           AND p.nation_id IN ({marks})
           AND p.nation_id <> e.actor_nation_id
         ORDER BY e.id DESC LIMIT 1
        """,  # noqa: S608 - the marks are placeholders, not values
        (world_id, EventKind.WAR_DECLARED.value, since_turn, *parties, *parties),
    ).fetchone()
    if row and row["actor_nation_id"]:
        return str(row["actor_nation_id"])

    # No declaration between these parties carries participants. Either the war predates
    # schema 17 or it was not declared through `_declare_war` at all -- a seeded flashpoint,
    # for instance, which every scenario has. Fall back rather than leave a real breach
    # unattributed.
    row = conn.execute(
        f"""
        SELECT e.actor_nation_id FROM event e
         WHERE e.world_id = ? AND e.kind = ? AND e.origin = 'ENGINE'
           AND e.turn >= ?
           AND e.actor_nation_id IN ({marks})
           AND NOT EXISTS (SELECT 1 FROM event_participant p
                            WHERE p.event_id = e.id AND p.role = 'target')
         ORDER BY e.id DESC LIMIT 1
        """,  # noqa: S608 - the marks are placeholders, not values
        (world_id, EventKind.WAR_DECLARED.value, since_turn, *parties),
    ).fetchone()
    return str(row["actor_nation_id"]) if row and row["actor_nation_id"] else None


def settle_fulfilled(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Close the agreements whose promises are actually discharged.

    **The derivation :func:`fulfillment_blockers` was written to specify, and which did not
    exist.** Nothing anywhere had ever written ``status = 'fulfilled'``: the value has been
    in the CHECK since schema 1, rule DIP-005 has governed who may write it since the
    standing-agreement work, and no code path reached it. So a treaty that ceded a province
    handed the province over and then **stayed open for the rest of the game** -- listed in
    every briefing as a live obligation, re-read by `assess_breaches` every turn, and
    counted against its parties as something still owed.

    A treaty's life should match what a treaty *is*. A one-off cession is discharged by
    being executed. A defensive pact is not discharged by anything: it ends by being broken,
    by expiring, or by being terminated, and never by being finished. That distinction is
    already written down -- :data:`STANDING_KINDS`, :data:`STANDING_CLAUSES`,
    :data:`ONE_OFF_CLAUSES` -- and this is the pass that acts on it.

    **Idempotent and derived**, exactly as :func:`honour_terms` is and for the same reason:
    there is no single moment a promise becomes kept, so this asks every turn whether one
    is, rather than reacting to a transition that has no single site.

    **A mixed agreement stays open while any standing term remains** -- a treaty that cedes
    a province *and* promises peace is not finished when the province moves. That is
    `fulfillment_blockers`' rule, and calling it rather than re-deriving is the whole point:
    the check that refuses a model's write and the check that drives the engine's own must
    be the same check, or one of them will eventually permit what the other forbids.
    """
    try:
        rows = conn.execute(
            "SELECT id, kind FROM commitment WHERE world_id = ? AND status = ?"
            " ORDER BY id",
            (world_id, CommitmentStatus.ACTIVE.value),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    from .diplomacy import sync_treaty_status

    told: list[str] = []
    for row in rows:
        commitment_id, kind = int(row["id"]), str(row["kind"])
        # A kind that is standing whatever it carries is never even asked. The cheap gate
        # first, because it is the great majority of every world's commitments.
        if kind in STANDING_KINDS:
            continue
        terms = terms_of(conn, commitment_id, kind)
        if not terms:
            # **No terms means nothing to discharge, so nothing to close.** A commitment
            # whose promise was only ever prose stays open: reading "we know of no
            # obligation" as "every obligation is met" would close agreements wholesale on
            # the turn this shipped.
            continue
        if fulfillment_blockers(conn, world_id, commitment_id=commitment_id, kind=kind):
            continue

        parties = _parties(conn, commitment_id)
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.COMMITMENT_FULFILLED, origin=EventOrigin.ENGINE,
            text=f"The {kind.replace('_', ' ')} between "
                 + " and ".join(_name(conn, n) for n in parties)
                 + " has been carried out in full.",
            visibility=Visibility.PUBLIC,
        )
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None,
                      diffs=(
                          Diff("commitment", str(commitment_id), "status", DiffOp.SET,
                               CommitmentStatus.FULFILLED.value, reason_event_id=event_id,
                               visibility_scope=Visibility.PUBLIC),
                          Diff("commitment", str(commitment_id), "resolved_turn",
                               DiffOp.SET, turn, reason_event_id=event_id,
                               visibility_scope=Visibility.PUBLIC),
                      )),
        )
        if not outcome.applied:
            unrecord_events(conn, (event_id,))
            continue
        # `treaty_status` is the materialised view of the *active* commitments, so closing
        # one has to recompute it -- the same call an expiry makes, for the same reason.
        for index, first in enumerate(parties):
            for second in parties[index + 1:]:
                sync_treaty_status(conn, world_id, (first, second), event_id=event_id)
        told.append(
            f"the {kind.replace('_', ' ')} between "
            + " and ".join(_name(conn, n) for n in parties) + " was carried out in full"
        )
    if told:
        conn.commit()
    return told


def expire_commitments(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Retire agreements whose term has run out. **Not a betrayal**, and kept apart from
    one, because a report that showed them together would teach a player to distrust a
    power that did nothing wrong."""
    # **The date decides, and the turn is the fallback.** A term is a promise about the
    # world's clock: a three-month ceasefire ran for three months at monthly pacing and
    # three years at yearly when the only thing recorded was `turn + 3`. `expires_on`
    # carries the date for anything written since schema 35; `expires_turn` is what a save
    # older than that has, and there is nothing to convert it from -- the calendar at the
    # turn of signing is not recoverable from the row.
    #
    # Written as one query rather than two passes so an agreement carrying both cannot be
    # retired twice, and so the ordering a report reads is still by id.
    try:
        rows = conn.execute(
            "SELECT id, kind FROM commitment"
            " WHERE world_id = ? AND status = ?"
            "   AND ((expires_on IS NOT NULL AND expires_on <> '' AND expires_on <= ?)"
            "        OR (COALESCE(expires_on, '') = '' AND expires_turn IS NOT NULL"
            "            AND expires_turn <= ?)) ORDER BY id",
            (world_id, CommitmentStatus.ACTIVE.value, date, turn),
        ).fetchall()
    except sqlite3.OperationalError:
        # A save from before the column existed. Fall back rather than refuse to open it.
        try:
            rows = conn.execute(
                "SELECT id, kind FROM commitment"
                " WHERE world_id = ? AND status = ? AND expires_turn IS NOT NULL"
                "   AND expires_turn <= ? ORDER BY id",
                (world_id, CommitmentStatus.ACTIVE.value, turn),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    told: list[str] = []
    from .diplomacy import sync_treaty_status

    for row in rows:
        commitment_id = int(row["id"])
        parties = _parties(conn, commitment_id)
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
            text=f"The {str(row['kind']).replace('_', ' ')} between "
                 + " and ".join(_name(conn, n) for n in parties)
                 + " has run its term.",
            visibility=Visibility.PUBLIC,
        )
        # The event is written first, because the diff that retires the agreement has to
        # cite it -- CORE-001, and the reason an expiry rewinds now rather than persisting
        # through one.
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None,
                      diffs=(
                          Diff("commitment", str(commitment_id), "status", DiffOp.SET,
                               CommitmentStatus.EXPIRED.value, reason_event_id=event_id,
                               visibility_scope=Visibility.PUBLIC),
                          Diff("commitment", str(commitment_id), "resolved_turn",
                               DiffOp.SET, turn, reason_event_id=event_id,
                               visibility_scope=Visibility.PUBLIC),
                      )),
        )
        if not outcome.applied:
            continue
        for index, first in enumerate(parties):
            for second in parties[index + 1:]:
                sync_treaty_status(conn, world_id, (first, second), event_id=event_id)
        told.append(
            f"the {str(row['kind']).replace('_', ' ')} between "
            + " and ".join(_name(conn, n) for n in parties) + " expired"
        )
    if told:
        conn.commit()
    return told
