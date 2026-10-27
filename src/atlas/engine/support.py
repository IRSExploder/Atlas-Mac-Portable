"""Backing a side without becoming one.

**The commonest thing a great power does in the twentieth century, and the engine could not
express it.** The Soviet Union armed Hanoi for a decade, the United States armed Saigon, and
in the database each of those relationships was either nothing at all or a declaration of
war on the other superpower. A patron had exactly two settings -- neutral, or belligerent --
in an era whose entire shape is the third one.

The Cold War playtest showed the cost directly: both superpowers reasoned correctly that
attacking each other was unthinkable, and then had nothing to do about a proxy war except
watch it. What was missing was not restraint; it was a lever.

**Five kinds, each with an effect no other kind has.** A kind the engine cannot act on
differently is flavour a patron will waste an order on, which is the same rule
:data:`~atlas.engine.objectives.OBJECTIVE_KINDS` is held to:

``equipment``
    tanks, aircraft, shells. Raises the client's odds in a contest, and it is the largest
    of the combat terms because it is the one that is actually a war-winning quantity.
``advisors``
    people. A smaller combat term than equipment and it also steadies the line -- the thing
    a training mission buys is a unit that does not come apart.
``intelligence``
    knowing where they are. The smallest combat term of the three, and deliberately: it is
    a multiplier on competence rather than a substitute for it.
``air_defence``
    missiles and the crews to fire them. Adds directly to what the client stops overhead,
    through the engine's single interception term, so it protects against a bomb and a
    warhead by the same state.
``funding``
    money, moved every turn from the patron's treasury to the client's. The one kind with a
    cost to the patron that is visible in its own numbers rather than only in its standing.

**Nothing here writes ``relation.at_war``, and that is the feature.** Backing a side does
not make a patron a belligerent, does not put it at war with its client's enemy, and is not
read by :func:`~atlas.engine.resolution.defensive_pact_allies`, so it triggers no Article 5
anywhere. What it costs is regard with the other side, which is exactly what it cost.

**The numbers below are a balance call and they are flagged as one.** They were chosen so
that a fully-backed client fights roughly like a power one tier above itself and never like
its patron -- support decides whether a client holds, never whether it wins outright. A
level-3 equipment-and-advisors package is worth about nine points of contest pressure, which
is a little more than favourable terrain and a little less than air superiority. If play
shows a proxy beating a great power on shipments alone, :data:`COMBAT_WEIGHT` is the dial.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

__all__ = [
    "AIR_DEFENCE_WEIGHT",
    "COMBAT_WEIGHT",
    "DENIABLE_KINDS",
    "EXPOSURE_PER_LEVEL",
    "FUNDING_PER_LEVEL",
    "MATERIEL_INDUSTRY_PER_LEVEL",
    "MATERIEL_KINDS",
    "MATERIEL_TREASURY_PER_LEVEL",
    "MAX_LEVEL",
    "SUPPORT_KINDS",
    "SupportReport",
    "air_defence_bonus",
    "combat_bonus",
    "declare_support",
    "move_funding",
    "sustain_support",
    "upkeep_of",
    "patrons_of",
    "support_lines",
    "support_of",
]

#: The five, and what each one is in one phrase. The keys are the CHECK's vocabulary.
SUPPORT_KINDS: dict[str, str] = {
    "equipment": "tanks, aircraft and shells",
    "advisors": "officers, trainers and the people who run the radios",
    "intelligence": "knowing where the other side is",
    "air_defence": "missiles, radars and the crews to fire them",
    "funding": "money, every turn, out of your own treasury",
}

#: The ceiling on any one kind. Three, so that "some", "a lot" and "everything we have" are
#: distinguishable and a fourth setting would not be.
MAX_LEVEL = 3.0

#: **Balance number.** Contest pressure per level, by kind. Pressure is a probability, so
#: these are small on purpose: the three combat kinds at full level come to 0.09, which sits
#: between favourable terrain and air superiority. A client that is backed to the hilt fights
#: like a power one tier above itself and never like its patron.
COMBAT_WEIGHT: dict[str, float] = {
    "equipment": 0.020,
    "advisors": 0.007,
    "intelligence": 0.003,
}

#: **Balance number.** What a level of air defence adds to what the client stops overhead.
#: Applied through :func:`atlas.engine.air.air_defence_over`, the engine's single
#: interception term, so a SAM belt is worth the same against a bomb and against a warhead.
#: Three levels is 0.18, which is half again what the engine's own ceiling on fighters gives.
AIR_DEFENCE_WEIGHT = 0.06

#: **Balance number.** Treasury moved per level per turn, patron to client. Small enough
#: that a superpower can back three clients without noticing and a middling power cannot.
FUNDING_PER_LEVEL = 2.0

#: **Balance numbers, and the reason they exist.** PT37 ran twelve turns in which both
#: superpowers went to the ceiling on every kind by turn nine and never came down. That is
#: what a free ratchet looks like: four of the five kinds cost nothing but standing, so
#: maximum was always correct and no decision was ever posed. The one kind with a visible
#: price behaved differently -- Washington dropped `funding` on turn 10, the only
#: de-escalation in the run.
#:
#: So materiel is now built and shipped rather than wished. **`equipment` and `air_defence`
#: draw treasury and industry every turn they are sustained**, because a tank sent to Hanoi
#: is a tank not kept, and the industry term is what stops a rich but unindustrialised power
#: arming three clients.
#: **Read these against *disposable* treasury, not against the scenario's starting
#: figure.** They were first set against 1972's opening numbers -- 130 for the Soviet Union,
#: 200 for the United States -- which is not money anybody gets to spend on clients. PT38
#: measured what a patron actually has spare once the rest of its economy has taken its cut:
#: about **22 for Moscow on turn 1, and falling**. So a full four-kind package is roughly
#: **four turns of reserve, not twenty-seven**, and this cost binds much harder than the
#: opening arithmetic suggested.
#:
#: **PT38's verdict: leave them alone.** Twelve live turns in which Moscow opened below
#: maximum, suspended everything twice when it ran dry, and substituted `advisors` and
#: `intelligence` -- the kinds that cost standing rather than money -- when it was broke.
#: Nine de-escalations against PT37's one. That is the decision this was built to pose.
#:
#: **If a future run shows a superpower unable to sustain any package at all**, the dial is
#: `MATERIEL_TREASURY_PER_LEVEL` and it goes **down**, not up. It sits close to the edge on
#: PT38's evidence, and on the right side of it. `COMBAT_WEIGHT` is not the thing to touch
#: either way -- the magnitude was never the problem.
MATERIEL_TREASURY_PER_LEVEL = 1.6
MATERIEL_INDUSTRY_PER_LEVEL = 0.5

#: ...and **`advisors` and `intelligence` are the deniable ones**, so their price is not
#: money: it is being seen. Each level costs standing with the client's *enemies* every
#: turn, which is the cost those two actually carried -- an adviser captured or a listening
#: post exposed is a diplomatic incident, not a line in a budget.
#:
#: Charged against the enemy's regard for the patron, so it accumulates into exactly the
#: thing a patron running a deniable war has to weigh: at some point the other side stops
#: treating you as a neutral.
EXPOSURE_PER_LEVEL = -0.6

#: Which kinds cost what. A kind absent from both is free, and none is.
MATERIEL_KINDS: frozenset[str] = frozenset({"equipment", "air_defence"})
DENIABLE_KINDS: frozenset[str] = frozenset({"advisors", "intelligence"})


@dataclass(slots=True)
class SupportReport:
    """What a patron changed about who it is backing."""

    opened: list[str] = field(default_factory=list)
    raised: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: The event each opened/raised/withdrawn line was written from, so a reader of the
    #: line can be held to the event's scope -- covert backing is SECRET.
    sources: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.opened) + len(self.raised) + len(self.withdrawn)


def support_of(
    conn: sqlite3.Connection, world_id: str, client_id: str
) -> dict[str, float]:
    """Everything this client is receiving, summed by kind across all its patrons.

    Summed rather than maxed: two patrons arming the same client is more than one, which is
    what a client with two patrons was actually getting. The cap is per patron per kind, so
    the total is bounded by how many powers are willing to be seen doing it.
    """
    totals: dict[str, float] = {}
    try:
        rows = conn.execute(
            "SELECT kind, SUM(level) AS level FROM support"
            " WHERE world_id = ? AND client_id = ? GROUP BY kind",
            (world_id, client_id),
        ).fetchall()
    except sqlite3.OperationalError:
        # A world written before schema 22. Nothing is backed, which is what it was.
        return {}
    for row in rows:
        totals[str(row["kind"])] = float(row["level"])
    return totals


def patrons_of(
    conn: sqlite3.Connection, world_id: str, client_id: str
) -> dict[str, dict[str, float]]:
    """``patron -> {kind: level}``, for a briefing that has to name who is paying."""
    out: dict[str, dict[str, float]] = {}
    try:
        rows = conn.execute(
            "SELECT patron_id, kind, level FROM support"
            " WHERE world_id = ? AND client_id = ? ORDER BY patron_id, kind",
            (world_id, client_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    for row in rows:
        out.setdefault(str(row["patron_id"]), {})[str(row["kind"])] = float(row["level"])
    return out


def combat_bonus(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What outside backing is worth to this nation's odds in a contest.

    Zero for the great majority of nations, and exactly zero on a world with no ``support``
    table at all -- the same shape :func:`atlas.engine.nuclear.deterrence` has, and for the
    same reason: a term that is usually nothing must cost nothing when it is nothing.
    """
    received = support_of(conn, world_id, nation_id)
    return round(
        sum(weight * received.get(kind, 0.0) for kind, weight in COMBAT_WEIGHT.items()), 4
    )


def air_defence_bonus(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What outside backing adds to what this nation stops overhead."""
    return round(
        AIR_DEFENCE_WEIGHT * support_of(conn, world_id, nation_id).get("air_defence", 0.0), 4
    )


def _alive(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    row = conn.execute(
        "SELECT is_alive FROM nation WHERE world_id = ? AND id = ?", (world_id, nation_id)
    ).fetchone()
    return row is not None and bool(row["is_alive"])


def declare_support(  # noqa: PLR0913 - a declaration is who, for whom, of what, and how
    conn: sqlite3.Connection,
    world_id: str,
    *,
    patron_id: str,
    declared: dict,
    turn: int,
    date: str,
    covert: bool = False,
) -> SupportReport:
    """Open, raise, lower or withdraw a patron's backing. ``{client: {kind: level}}``.

    **Level 0 withdraws rather than storing a nought**, so "backed at nothing" and "not
    backed" cannot disagree -- the same reason a dependency's home is NULL rather than
    itself.

    ``covert`` sends the deniable kinds without saying so. It is refused for materiel and
    for funding: a tank is a tank the other side can photograph, and money leaves a ledger.
    What deniability buys is *time* -- the client's enemies do not read the shipment, and
    what they work out instead is an attribution that climbs while the backing is sustained,
    until at some point they stop pretending. See :mod:`atlas.engine.attribution`.

    Every refusal says why, in the shape :class:`~atlas.engine.causality.Refusal` sets: a
    patron told only "no" ships the same crates again next turn.
    """
    from .causality import Refusal

    report = SupportReport()
    if not declared:
        return report

    for client_id in sorted(declared):
        wanted = declared[client_id]
        if not isinstance(wanted, dict):
            report.refused.append(
                Refusal("support_shape",
                        f"backing for {client_id} was not a map of kind to level.",
                        'write {"equipment": 2, "funding": 1}.').rendered())
            continue
        if client_id == patron_id:
            from .alternatives import live_nations, phrase

            report.refused.append(
                Refusal("needs_client",
                        "you cannot back yourself.",
                        phrase(live_nations(conn, world_id, besides=patron_id),
                               lead="powers you could back include")).rendered())
            continue
        if not _alive(conn, world_id, client_id):
            from .alternatives import live_nations, phrase

            report.refused.append(
                Refusal("needs_client",
                        f"there is no nation called {client_id!r} still standing on this "
                        "map.",
                        phrase(live_nations(conn, world_id, besides=patron_id),
                               lead="powers you could back include")).rendered())
            continue

        for kind in sorted(wanted):
            if kind not in SUPPORT_KINDS:
                report.refused.append(
                    Refusal("support_kind", f"{kind!r} is not something you can send.",
                            "the kinds are " + ", ".join(sorted(SUPPORT_KINDS))
                            + ".").rendered())
                continue
            try:
                level = float(wanted[kind])
            except (TypeError, ValueError):
                report.refused.append(
                    Refusal("support_level",
                            f"the level of {kind} for {client_id} was not a number.",
                            f"0 to withdraw, up to {MAX_LEVEL:.0f}.").rendered())
                continue
            level = max(0.0, min(MAX_LEVEL, level))
            if covert and kind not in DENIABLE_KINDS:
                # **A tank is a tank the other side can photograph, and money leaves a
                # ledger.** Only the two kinds this module already calls deniable can be
                # sent without saying so; the rest are physical and traceable, and letting
                # them go covert would make deniability a free option rather than a choice
                # about what to send.
                report.refused.append(
                    Refusal("support_covert",
                            f"{kind} cannot be sent quietly.",
                            "the deniable kinds are "
                            + ", ".join(sorted(DENIABLE_KINDS)) + ".").rendered())
                continue
            # **Refused at the door if the first turn of it is unaffordable**, rather than
            # opened and lapsed one turn later. A patron told the price can pick a level it
            # can actually sustain; one told nothing ships crates it has not got and finds
            # out next month.
            if level > 0 and (afford := _unaffordable(
                conn, world_id, patron_id, kind=kind, level=level
            )):
                report.refused.append(afford)
                continue
            _record(conn, world_id, report, patron_id=patron_id, client_id=client_id,
                    kind=kind, level=level, turn=turn, date=date, covert=covert)

    if report.total:
        conn.commit()
    return report


def _unaffordable(
    conn: sqlite3.Connection, world_id: str, patron_id: str, *, kind: str, level: float
) -> str | None:
    """``None`` if this patron can pay a turn of it, otherwise why not and what it could.

    Counts only what this *one* commitment costs, not the whole bill: the sustain pass is
    what enforces the total, and refusing an affordable kind because of an unrelated one
    would be telling a patron it is broke when what it is, is overcommitted.
    """
    from .causality import Refusal

    treasury = round(
        (FUNDING_PER_LEVEL if kind == "funding"
         else MATERIEL_TREASURY_PER_LEVEL if kind in MATERIEL_KINDS else 0.0) * level, 2)
    industry = round(
        (MATERIEL_INDUSTRY_PER_LEVEL if kind in MATERIEL_KINDS else 0.0) * level, 2)
    if treasury <= 0 and industry <= 0:
        return None

    row = conn.execute(
        "SELECT treasury, industry FROM nation WHERE world_id = ? AND id = ?",
        (world_id, patron_id)).fetchone()
    if row is None:
        return None
    held_treasury, held_industry = float(row["treasury"]), float(row["industry"])
    if treasury <= held_treasury and industry <= held_industry:
        return None

    per_level_t = treasury / level if level else 0.0
    per_level_i = industry / level if level else 0.0
    affordable = min(
        MAX_LEVEL,
        held_treasury / per_level_t if per_level_t else MAX_LEVEL,
        held_industry / per_level_i if per_level_i else MAX_LEVEL,
    )
    return Refusal(
        "support_upkeep",
        f"{kind.replace('_', ' ')} at {level:g} costs {treasury:g} treasury"
        + (f" and {industry:g} industry" if industry else "")
        + f" a turn, and you have {held_treasury:g} and {held_industry:g}.",
        (f"you could sustain it at {int(affordable)}." if affordable >= 1
         else "you cannot sustain any of it; try `intelligence` or `advisors`, which cost "
              "standing rather than money."),
    ).rendered()


def _record(  # noqa: PLR0913 - one call site, and every argument is a separate fact
    conn: sqlite3.Connection,
    world_id: str,
    report: SupportReport,
    *,
    patron_id: str,
    client_id: str,
    kind: str,
    level: float,
    turn: int,
    date: str,
    covert: bool = False,
) -> None:
    """One (patron, client, kind) decision, written as a diff so it rewinds."""
    row = conn.execute(
        "SELECT level FROM support WHERE world_id = ? AND patron_id = ? AND client_id = ?"
        "   AND kind = ?",
        (world_id, patron_id, client_id, kind),
    ).fetchone()
    was = float(row["level"]) if row is not None else 0.0
    if abs(was - level) < 1e-9:
        return

    verb = "begins" if was == 0 else ("ends" if level == 0 else
                                      ("steps up" if level > was else "scales back"))
    # **Covert backing is secret, and its shape is what makes it attributable.** Actor is
    # the patron, `beneficiary` is the client, and every one of the client's enemies is a
    # `target` -- which is the triple `attribution._open_acts` recognises as a backing, and
    # which no other event in the engine writes. The enemies are who the deniability is
    # aimed at, so they are who gets to work it out.
    enemies = _enemies_of(conn, world_id, client_id) if covert else []
    scope = Visibility.SECRET if covert else Visibility.PUBLIC
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE, actor_nation_id=patron_id,
        text=(f"{_name(conn, patron_id)} {verb} {kind.replace('_', ' ')} support for "
              f"{_name(conn, client_id)}."
              + (" Nobody is to know." if covert else "")),
        # **Public unless the patron chose otherwise.** A shipment is deniable in the
        # newspapers and not in the field: the other side can see what its enemy is firing.
        # The two kinds this module already calls deniable are the exception, and sending
        # them quietly is what I5 made mechanical rather than flavour.
        visibility=scope,
        visible_to=((patron_id, client_id) if covert else ()),
        participants=((("actor", patron_id), ("beneficiary", client_id),
                       *(("target", enemy) for enemy in enemies))
                      if covert else ()),
    )
    # Three ops, because three things can happen to a row: opening it is an INSERT,
    # changing it is a SET, ending it is a DELETE. All three are diffs, which is what makes
    # a patron's decision rewind like everything else.
    row_id = f"{world_id}|{patron_id}|{client_id}|{kind}"
    if level == 0:
        diff = Diff("support", row_id, "level", DiffOp.DELETE, None,
                    reason_event_id=event_id, visibility_scope=scope)
    elif was == 0:
        diff = Diff("support", row_id, "level", DiffOp.INSERT,
                    {"level": round(level, 2), "opened_turn": turn},
                    reason_event_id=event_id, visibility_scope=scope)
    else:
        diff = Diff("support", row_id, "level", DiffOp.SET, round(level, 2),
                    reason_event_id=event_id, visibility_scope=scope)
    diffs = (diff,)
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=diffs),
    )
    if not outcome.applied:
        unrecord_events(conn, (event_id,))
        report.refused.append(
            f"the backing for {client_id} could not be recorded: {outcome.reason}")
        return

    line = f"{kind.replace('_', ' ')} for {_name(conn, client_id)}"
    if level == 0:
        report.withdrawn.append(line)
    elif was == 0:
        line = f"{line} at {level:g}"
        report.opened.append(line)
    else:
        line = f"{line}, {was:g} to {level:g}"
        report.raised.append(line)
    report.sources[line] = event_id


def _has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'support'"
    ).fetchone() is not None


def upkeep_of(conn: sqlite3.Connection, world_id: str, patron_id: str) -> dict[str, float]:
    """What one turn of this patron's commitments costs it. Zero everywhere for most.

    Read by the briefing so a patron can see the bill *before* it decides, which is most of
    what turns this from a ratchet into a decision.
    """
    treasury = industry = exposure = 0.0
    rows = conn.execute(
        "SELECT client_id, kind, level FROM support"
        " WHERE world_id = ? AND patron_id = ? ORDER BY client_id, kind",
        (world_id, patron_id),
    ).fetchall() if _has_table(conn) else ()
    for row in rows:
        kind, level = str(row["kind"]), float(row["level"])
        if kind in MATERIEL_KINDS:
            treasury += MATERIEL_TREASURY_PER_LEVEL * level
            industry += MATERIEL_INDUSTRY_PER_LEVEL * level
        elif kind == "funding":
            treasury += FUNDING_PER_LEVEL * level
        elif kind in DENIABLE_KINDS and _scope_of(
            conn, world_id, patron_id, str(row["client_id"]), kind
        ) is Visibility.PUBLIC:
            # Covert backing is not charged in open regard: see `sustain_support`.
            exposure += abs(EXPOSURE_PER_LEVEL) * level
    return {"treasury": round(treasury, 2), "industry": round(industry, 2),
            "exposure": round(exposure, 2)}


def sustain_support(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Charge a patron for what it is sustaining, and drop what it cannot pay for.

    **PT37 is why this exists.** Twelve live turns in which both superpowers reached the
    ceiling on every kind by turn nine and stayed there: four of the five kinds cost nothing
    but standing, so maximum was always correct and no decision was ever posed. The only
    de-escalation in the entire run was Washington dropping `funding` -- the one kind that
    had a price.

    Three prices, and each is the one that kind actually carried:

    * **funding** moves treasury from the patron to the client, as it always did;
    * **equipment and air_defence** are materiel and draw treasury *and* industry -- a tank
      sent to Hanoi is a tank not kept, and the industry term is what stops a rich but
      unindustrialised power arming three clients at once;
    * **advisors and intelligence** are the deniable ones, so their price is being seen:
      standing with the client's enemies, every turn, accumulating into the point where the
      other side stops treating the patron as a neutral.

    **What cannot be paid for lapses**, loudly, rather than going into debt or being quietly
    discounted. That is the design goal stated plainly: a patron at its limits has to choose
    which client and which kind to sustain, and if it will not choose, the shipments stop and
    it is told they stopped. Kinds are walked in a stable ``(patron, client, kind)`` order so
    a rewind and replay lapse the same ones -- arbitrary, but replayable, and a patron that
    wants to choose for itself should lower a level deliberately.

    Nothing here can make the patron a belligerent: the exposure term moves regard, which is
    a number and not a war.

    **Covert backing keeps its scope here too** (finding E2). ``declare_support(covert=True)``
    opens a row with a SECRET event seen by patron and client only; this pass then wrote a
    PUBLIC "X sustains advisors for Y" every turn after, with public regard diffs, so the
    deniable channel was announced from its second turn. A row's scope is the scope it was
    last written at (:func:`_scope_of`), and everything this pass writes about a covert row
    -- event and diffs -- is written at that scope, visible to patron and client.

    **And a covert row is not charged exposure.** The open regard penalty is the price of
    being *seen* backing a side, and a covert patron has paid for not being seen: its price
    is the attribution its client's enemies work out while the backing continues
    (:mod:`atlas.engine.attribution`, design I5), which sours their regard at their own
    scope once they suspect. Charging both would bill the patron twice for one exposure,
    and a regard drop on the enemy's own relation row cannot be kept secret from the enemy
    -- it would be the tell by another route.
    """
    if not _has_table(conn):
        return []
    rows = conn.execute(
        "SELECT patron_id, client_id, kind, level FROM support WHERE world_id = ?"
        " ORDER BY patron_id, client_id, kind", (world_id,)).fetchall()
    if not rows:
        return []

    purse = {
        str(r["id"]): [float(r["treasury"]), float(r["industry"])]
        for r in conn.execute(
            "SELECT id, treasury, industry FROM nation WHERE world_id = ?", (world_id,))
    }
    told: list[str] = []
    for row in rows:
        patron, client = str(row["patron_id"]), str(row["client_id"])
        kind, level = str(row["kind"]), float(row["level"])
        if patron not in purse:
            continue
        if not (_alive(conn, world_id, client) and _alive(conn, world_id, patron)):
            # **Nobody left to ship to.** `declare_support` refuses a dead client, but a
            # client eliminated *after* the declaration kept its row, and the patron was
            # billed for it every turn for the rest of the game -- funding paid into the
            # treasury of a nation that no longer exists.
            #
            # **Out of sight when it could have been secret.** Only the deniable kinds may
            # be sent covertly (`declare_support` refuses the rest), and a lapse is written
            # in public -- so ending a covert channel here would announce, on the day its
            # client died, the backing the patron had kept deniable for its whole life: in
            # the event log every briefing reads, and in the turn report, which has no scope
            # of its own. They end at SECRET, seen by the two parties, and are not reported.
            # Nobody else is owed the news that a shipment to a country that no longer
            # exists has stopped. Funding and materiel are never covert and end as before.
            quiet = kind in DENIABLE_KINDS
            lapsed = _lapse(conn, world_id, patron=patron, client=client, kind=kind,
                            turn=turn, date=date,
                            scope=Visibility.SECRET if quiet else Visibility.PUBLIC)
            if not quiet:
                told.append(lapsed)
            continue

        treasury = round(
            (FUNDING_PER_LEVEL if kind == "funding"
             else MATERIEL_TREASURY_PER_LEVEL if kind in MATERIEL_KINDS else 0.0) * level, 2)
        industry = round(
            (MATERIEL_INDUSTRY_PER_LEVEL if kind in MATERIEL_KINDS else 0.0) * level, 2)
        held_treasury, held_industry = purse[patron]
        scope = _scope_of(conn, world_id, patron, client, kind)
        covert = scope is not Visibility.PUBLIC

        if treasury > held_treasury or industry > held_industry:
            # **Lapsed, not discounted and not borrowed.** A patron that cannot pay for what
            # it promised stops shipping, and is told where it can act on it.
            lapsed = _lapse(conn, world_id, patron=patron, client=client, kind=kind,
                            turn=turn, date=date, scope=scope)
            # Defensive: only the deniable kinds may go covert and they carry no bill, so a
            # covert row cannot lapse today. If one ever could, this line goes to the turn
            # report, which has no scope of its own.
            if not covert:
                told.append(lapsed)
            continue

        moves: list[tuple[str, str, str, float]] = []
        if treasury > 0:
            moves.append(("nation", patron, "treasury", -treasury))
            if kind == "funding":
                moves.append(("nation", client, "treasury", treasury))
        if industry > 0:
            moves.append(("nation", patron, "industry", -industry))
        if kind in DENIABLE_KINDS and not covert:
            # Seen by the people it is aimed at, and by nobody else.
            for enemy in _enemies_of(conn, world_id, client):
                if enemy != patron:
                    moves.append(("relation", f"{enemy}|{patron}", "score",
                                  round(EXPOSURE_PER_LEVEL * level, 2)))
        if not moves:
            continue

        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
            origin=EventOrigin.ENGINE, actor_nation_id=patron,
            text=f"{_name(conn, patron)} sustains {kind.replace('_', ' ')} for "
                 f"{_name(conn, client)}.",
            visibility=scope,
            visible_to=((patron, client) if covert else ()),
        )
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None,
                      diffs=tuple(
                          Diff(table, row_id, column, DiffOp.ADJUST, value,
                               reason_event_id=event_id, visibility_scope=scope)
                          for table, row_id, column, value in moves)),
        )
        if not outcome.applied:
            unrecord_events(conn, (event_id,))
            continue
        purse[patron] = [held_treasury - treasury, held_industry - industry]
        if (treasury or industry) and not covert:
            told.append(
                f"{_name(conn, patron)} spent {treasury:g} treasury"
                + (f" and {industry:g} industry" if industry else "")
                + f" on {kind.replace('_', ' ')} for {_name(conn, client)}")
    if told:
        conn.commit()
    return told


def _enemies_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    return [
        str(row["nation_b"]) for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND at_war = 1 ORDER BY nation_b", (world_id, nation_id))
    ]


def _scope_of(
    conn: sqlite3.Connection, world_id: str, patron: str, client: str, kind: str
) -> Visibility:
    """The scope a support row was last written at: SECRET for covert backing.

    **Read from the row's own diffs rather than stored in a column.** Every write to a
    ``support`` row is a diff carrying the scope of the declaration that caused it, and
    that is the whole fact -- a column would be a second copy that could disagree with it,
    and would cost a migration for nothing the log does not already say. It is rewind-safe
    for the same reason: a rewind deletes the batches of the turns it undoes, so the latest
    surviving diff is the declaration that stands. A row no diff ever wrote (a scenario's
    opening backing) is public, which is what a scenario means by it.
    """
    row = conn.execute(
        """
        SELECT d.visibility_scope AS scope
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND d.status = 'applied'
           AND d.target_table = 'support' AND d.target_id = ?
         ORDER BY d.id DESC LIMIT 1
        """,
        (world_id, f"{world_id}|{patron}|{client}|{kind}"),
    ).fetchone()
    return Visibility(str(row["scope"])) if row is not None else Visibility.PUBLIC


def _lapse(conn: sqlite3.Connection, world_id: str, *, patron: str, client: str,
           kind: str, turn: int, date: str,
           scope: Visibility = Visibility.PUBLIC) -> str:
    """Stop a shipment the patron can no longer pay for. A deletion, so it rewinds.

    At the row's own scope: a covert shipment that stops is as secret as one that runs.
    """
    covert = scope is not Visibility.PUBLIC
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, actor_nation_id=patron,
        text=f"{_name(conn, patron)} can no longer sustain {kind.replace('_', ' ')} for "
             f"{_name(conn, client)}, and it stops.",
        visibility=scope,
        visible_to=((patron, client) if covert else ()),
    )
    apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=(Diff("support", f"{world_id}|{patron}|{client}|{kind}", "level",
                              DiffOp.DELETE, None, reason_event_id=event_id,
                              visibility_scope=scope),)),
    )
    return (f"{_name(conn, patron)} could not sustain {kind.replace('_', ' ')} for "
            f"{_name(conn, client)}; it lapsed")


def move_funding(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """The name the turn engine used when `funding` was the only kind with a price."""
    return sustain_support(conn, world_id, turn=turn, date=date)


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def support_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this nation is sending and what it is receiving, for its own briefing.

    Empty when it is doing neither, which is the great majority of nations -- the rule the
    nuclear block and the pact block both follow.
    """
    try:
        sending = conn.execute(
            "SELECT client_id, kind, level FROM support"
            " WHERE world_id = ? AND patron_id = ? ORDER BY client_id, kind",
            (world_id, nation_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    lines: list[str] = []
    if sending:
        by_client: dict[str, list[str]] = {}
        for row in sending:
            by_client.setdefault(str(row["client_id"]), []).append(
                f"{str(row['kind']).replace('_', ' ')} at {float(row['level']):g}")
        bill = upkeep_of(conn, world_id, nation_id)
        purse = conn.execute(
            "SELECT treasury, industry FROM nation WHERE world_id = ? AND id = ?",
            (world_id, nation_id)).fetchone()
        lines.append(
            "**You are backing, without being at war on their behalf:** "
            + "; ".join(f"{_name(conn, client)} -- {', '.join(what)}"
                        for client, what in sorted(by_client.items()))
            + ". Raising or ending any of it is a `support` declaration."
        )
        lines.append(
            f"**What it costs you every turn: {bill['treasury']:g} treasury, "
            f"{bill['industry']:g} industry"
            + (f", and {bill['exposure']:g} regard with your clients' enemies"
               if bill["exposure"] else "")
            + ".** "
            + (f"You hold {float(purse['treasury']):g} treasury and "
               f"{float(purse['industry']):g} industry. " if purse else "")
            + "Anything you cannot pay for in a turn **stops that turn** -- so keep what you "
            "mean to keep and let the rest go deliberately rather than by arithmetic."
        )

    if not sending:
        # **Telling a patron the lever exists.** `support_lines` rendered what a nation
        # already had, which is no use at all to one that has none: it is precisely the
        # power backing nobody that needs to be told it could. Offered only where there is
        # somebody plausible to back -- a war it is not in, involving somebody it does not
        # hate -- so it is a lever with a target rather than a paragraph of vocabulary.
        candidates = [
            str(row["id"])
            for row in conn.execute(
                """
                SELECT DISTINCT n.id AS id
                  FROM relation war
                  JOIN nation n ON n.id = war.nation_a
                  LEFT JOIN relation mine
                         ON mine.world_id = war.world_id AND mine.nation_a = ?
                        AND mine.nation_b = n.id
                 WHERE war.world_id = ? AND war.at_war = 1 AND n.is_alive = 1
                   AND n.id <> ? AND COALESCE(mine.at_war, 0) = 0
                   AND COALESCE(mine.score, 0) >= 0
                 ORDER BY COALESCE(mine.score, 0) DESC, n.id LIMIT 4
                """,
                (nation_id, world_id, nation_id),
            )
        ]
        if candidates:
            lines.append(
                "**You may back somebody else's war without entering it.** Set `support` "
                'to {"<nation id>": {"equipment": 2}} and so on -- kinds are '
                + ", ".join(f"`{kind}`" for kind in SUPPORT_KINDS)
                + f", levels 0 to {MAX_LEVEL:.0f}, 0 withdraws. It raises what they can do "
                "and it does **not** put you at war with anybody or oblige you to anything. "
                "Currently fighting, and not hostile to you: "
                + ", ".join(_name(conn, other) for other in candidates) + "."
            )

    receiving = patrons_of(conn, world_id, nation_id)
    if receiving:
        lines.append(
            "**You are being backed by:** "
            + "; ".join(
                f"{_name(conn, patron)} -- "
                + ", ".join(f"{k.replace('_', ' ')} at {v:g}" for k, v in sorted(what.items()))
                for patron, what in sorted(receiving.items()))
            + ". It is not a guarantee: none of them is at war on your account, and none "
            "of them has to be."
        )
    return lines
