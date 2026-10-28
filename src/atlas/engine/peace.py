"""What a peace says, whether it can be carried out, and whether the loser will sign it.

Layer 4 of the war loop, and the one where the pieces below it pay off. Until now a
settlement was *stop shooting, and here is some ground*: `_settle_war` clears two flags and
moves occupied provinces, writes no agreement, and nothing anywhere decided whether the
losing side would have accepted it. Both models simply had to agree.

**Three jobs, and the split between them is the whole design.**

*The model proposes.* A term set: what is to be ceded, restored, paid, emptied, guaranteed,
recognised. Prose becomes a list of structured terms and nothing more.

*The engine enforces.* :func:`review_terms` puts every term through the same kind of check
the orders workbench does -- you cannot cede what neither of you holds, reparations are
bounded by what the treasury can bear, a demilitarised zone names real ground -- and refuses
what cannot be done, with a reason and, where there is one, the legal thing to write instead.

*The engine decides acceptance, alone.* :func:`acceptance` weighs what the terms cost the
side being asked against what :mod:`atlas.engine.leverage` says its position is worth. A
power in a commanding position refuses a punitive demand; a beaten one signs away provinces.
**Neither model has a vote**, for the same reason neither decides a battle: a winner who
could talk a loser into surrender would be deciding the war by narration.

**Exhaustion never forces a signature.** At the top of the scale the engine *offers* --
:func:`suggested_terms` builds what the position would bear and the briefing shows it -- and
the power may refuse and fight on. Nothing here ends a war against a belligerent's will.

**The peace is a real agreement.** It writes a ``peace_treaty`` commitment -- the kind that
has been in the schema since it was written and never once used -- with both parties'
acceptance recorded, so breaking it runs through the breach machinery that already exists:
the casus belli, the regard, the legitimacy charge. Its machine-readable half is
:mod:`peace_term` rather than ``commitment_term``, because that column's CHECK is closed and
widening it in SQLite means rebuilding the table.

**And it mints facts.** Recognition, a renounced claim, a demilitarised zone: each is
something the world now takes for granted, recorded through :mod:`atlas.engine.facts` with
the treaty as its cause. That is the seam between this thread and the other one.

**Separate peace falls out of the keying.** Everything here is per belligerent pair, so
settling with France does not touch the war with Britain -- and a test says so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import (
    CommitmentKind,
    DiffOp,
    EventKind,
    EventOrigin,
    Role,
    Visibility,
)
from .constituencies import RESTING as CIVIC_RESTING
from .constituencies import band_of, support_of
from .facts import record_fact
from .leverage import leverage_between
from .wareffort import effort_between

__all__ = [
    "EXPECTATION_FROM_LEVERAGE",
    "PATRIOTS_STIFFEN",
    "TERM_KINDS",
    "Acceptance",
    "patriot_stiffening",
    "ProposedTerm",
    "Settlement",
    "TermCard",
    "acceptance",
    "offer_peace",
    "term_schema",
    "terms_from",
    "honour_peace_terms",
    "review_terms",
    "settle",
    "suggested_terms",
]

VALID, REFUSED = "VALID", "REFUSED"


@dataclass(frozen=True, slots=True)
class TermKind:
    """One thing a peace can say, and what it costs the side that has to do it."""

    name: str
    explain: str
    #: What the term names: a region, a nation, or nothing.
    subject: str
    #: Roughly what it costs the obliged party, as a share of everything, before the
    #: specifics are worked out. The real cost is computed per term in :func:`_cost_of`;
    #: this is the floor a term of this kind can never be cheaper than.
    weight: float
    #: Whether the term is discharged the moment it is signed, or stands afterwards.
    standing: bool = False


#: The vocabulary. Seven, and each one maps to machinery that already exists -- which is
#: the test of whether a term belongs here at all: a term the engine cannot carry out is a
#: sentence in a treaty nobody enforces, and this layer exists precisely to stop that.
TERM_KINDS: dict[str, TermKind] = {
    "cede_region": TermKind(
        "cede_region", "ground changes hands for good", "region", 0.0),
    "restore_region": TermKind(
        "restore_region", "occupied ground goes back to whoever owns it", "region", 0.0),
    "withdraw": TermKind(
        "withdraw", "all forces leave the named ground", "region", 0.05),
    "reparations": TermKind(
        "reparations", "money, paid over the turns that follow", "none", 0.0,
        standing=True),
    "demilitarise": TermKind(
        "demilitarise", "the named ground is to stay empty of the obliged side's forces",
        "region", 0.08, standing=True),
    "guarantee": TermKind(
        "guarantee", "the obliged power underwrites another's defence", "nation", 0.10,
        standing=True),
    "recognise": TermKind(
        "recognise", "the obliged power accepts the other's standing", "nation", 0.05),
    # **The term that makes `regime_change` an aim a war can end on.** It has been in
    # `waraims.AIM_KINDS` since war aims landed and `leverage` reasons about it by name, and
    # there was no matching term -- so the only way to satisfy it was to conquer the country
    # outright, which is the one outcome a negotiated peace exists to avoid.
    #
    # Priced above every other term because it is the largest thing a state can be made to
    # concede short of its territory: the government signing it is signing its own end.
    "depose": TermKind(
        "depose", "the government that signed this stands down", "none", 0.30),
    # Arms control. `value` is the ceiling; exceeding it afterwards is a broken agreement
    # through the same `treaties.breach` a remilitarised zone goes through, which is what
    # keeps this from being a sentence nobody enforces.
    "limit_arsenal": TermKind(
        "limit_arsenal", "the obliged power holds no more warheads than the stated number",
        "none", 0.18, standing=True),
}

# --------------------------------------------------------------------------- the dials

#: **What a beaten power will swallow.** Leverage runs -1..+1; a side at -1 has a tolerance
#: of this much "cost", where cost is measured as a share of what it has -- so a wholly
#: beaten country will sign away most of its homeland and most of its treasury, and a
#: country at parity will sign away almost nothing.
TOLERANCE_FROM_LEVERAGE = 1.10

#: **And what being sick of the war adds to that.** A country that is collapsing accepts
#: terms an unbroken one in the same military position would refuse -- which is the whole
#: historical phenomenon this layer exists to model.
TOLERANCE_FROM_EXHAUSTION = 0.45

#: A little is always tolerable: nobody fights on over a demilitarised valley.
TOLERANCE_FLOOR = 0.08

#: **What a winning power thinks it is owed.** At leverage +1 it expects concessions worth
#: this much, and terms that fall short of it are refused however little they cost.
#:
#: **This is the half that was missing, and its absence was a real hole.** Acceptance
#: measured only what terms cost the side being asked, so a power winning outright -- at
#: +0.78, holding two of the other's home provinces -- signed "stop fighting, nothing
#: changes hands", because a white peace costs the recipient nothing. Any losing side could
#: walk out of a lost war for free by proposing nothing at all, and the map was left with a
#: settled occupation that no treaty converted into anything. Found in the reachability
#: pre-flight for the live proof, before the run was spent.
#:
#: At 0.60 a power at +0.78 wants about 0.47 back -- roughly four provinces of a ten-province
#: country, which is what that position is worth.
EXPECTATION_FROM_LEVERAGE = 0.60

#: The most of a treasury a settlement may demand. Above this the term is not enforceable
#: at all -- a country cannot pay what it does not have, and a reparation nobody can pay is
#: a broken treaty written on the day it was signed.
REPARATION_CAP = 0.60

#: How much of the outstanding reparation is paid each month.
REPARATION_INSTALMENT = 0.10

#: Where the engine starts offering terms rather than waiting to be asked.
OFFER_AT_EXHAUSTION = 90.0

# ------------------------------------------------------------------ and what the country says

#: **What devoted nationalists take off what a country will swallow.** At their most devoted,
#: on a settlement made entirely of humiliations, they cut its tolerance by this much -- so a
#: power whose politics are inflamed refuses terms its position says it should take, and
#: fights a war it is losing because it cannot survive ending it.
#:
#: This is the domestic layer's first bite, and the size is the argument. Below about 0.3 it
#: is a rounding error on a number that already spans 0.08 to 1.55; above about 0.6 the
#: nationalists, not the fighting, decide every war.
PATRIOTS_STIFFEN = 0.45

#: And what nationalists who have given up add to it. Deliberately smaller than the
#: stiffening: a country whose patriots are hostile to the government has *already* lost the
#: argument at home, and a beaten power signs anyway -- the resigned case makes a hard peace
#: a little easier, it does not make a country eager to be dismembered.
PATRIOTS_RESIGNED = 0.20

#: **Where the nationalists stop being consulted.** A power at this leverage is not choosing
#: between peace and war, it is choosing between terms and occupation, and no domestic mood
#: survives that. Full voice above `PATRIOTS_HEARD_ABOVE`, none at or below the floor, and a
#: straight ramp between -- so an annihilated power folds whatever its politics say, which is
#: the bound that keeps this from overriding everything the war loop measured.
PATRIOTS_SILENT_AT = -0.90
PATRIOTS_HEARD_ABOVE = -0.65

#: **How much of an insult each clause is**, which is not the same as what it costs. The
#: nationalists are not the treasury: they mind ground and standing, and a reparation is
#: money. So the stiffening is scaled by the share of the demand that is humiliation rather
#: than expense, and a peace of pure indemnity is barely political at all.
_HUMILIATION: dict[str, float] = {
    "cede_region": 1.0,
    "recognise": 0.8,
    "demilitarise": 0.7,
    "guarantee": 0.5,
    "withdraw": 0.4,
    "restore_region": 0.4,
    "reparations": 0.2,
}


@dataclass(frozen=True, slots=True)
class ProposedTerm:
    """One clause, as proposed. ``obliged`` is who must do it."""

    kind: str
    obliged: str
    beneficiary: str
    subject: str = ""
    value: float = 0.0

    def describe(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        who = names.get(self.obliged, self.obliged)
        to = names.get(self.beneficiary, self.beneficiary)
        what = names.get(self.subject, self.subject)
        if self.kind == "reparations":
            return f"{who} pays {to} {self.value:.0f}"
        if self.kind == "recognise":
            return f"{who} recognises {to}"
        if self.kind == "guarantee":
            return f"{who} guarantees {what or to}"
        # **Who gives what to whom, in the sentence.** The fallback below printed a region
        # term as "North Korea: cede region Kangwon-do" -- a label, not a clause, with the
        # receiving side nowhere in it. In the Three Fronts match (2026-09-23) South Korea
        # won Kangwon-do by exactly that treaty, read it in its own briefing beside
        # "Kangwon-do (North Korea's core)", and announced three times that it was
        # "honouring the peace treaty's cession to North Korea". A term a commander can read
        # backwards is a term the engine wrote badly.
        if self.kind == "cede_region":
            return f"{who} cedes {what} to {to}"
        if self.kind == "restore_region":
            return f"{who} hands {what} back to {to}"
        if self.kind == "withdraw":
            return f"{who} withdraws its forces from {what}"
        if self.kind == "demilitarise":
            return f"{who} keeps its forces out of {what}"
        return f"{who}: {self.kind.replace('_', ' ')} {what}"


@dataclass(frozen=True, slots=True)
class TermCard:
    """What the engine makes of one proposed term."""

    term: ProposedTerm
    verdict: str
    because: str = ""
    instead: str = ""
    cost: float = 0.0

    @property
    def ok(self) -> bool:
        return self.verdict == VALID


@dataclass(frozen=True, slots=True)
class Acceptance:
    """Whether the side being asked will sign, and the engine's reasoning."""

    accepted: bool
    cost: float
    tolerance: float
    position: float
    because: str = ""
    #: What the nationalists took off the tolerance, 0..1. Zero when they are indifferent,
    #: when the offer is not a humiliation, or when the position is past caring.
    stiffening: float = 0.0


@dataclass(slots=True)
class Settlement:
    """What a signed peace did."""

    signed: bool = False
    commitment_id: int | None = None
    #: The event the settlement was recorded under, so anything that follows from the peace
    #: can cite it -- a diff without a reason event is a diff a rewind cannot explain.
    event_id: int | None = None
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    #: What the world should be told, in the words the resolver has always used: a power
    #: *recovered* its own people's ground and *annexed* somebody else's.
    headlines: list[str] = field(default_factory=list)
    #: Set instead of signing when the offer was made *to* the player: what to tell them.
    to_player: str = ""


# ------------------------------------------------------------------------ enforceability


def review_terms(
    conn: sqlite3.Connection, world_id: str, *, belligerents: tuple[str, str],
    terms,
) -> list[TermCard]:
    """Every proposed term, checked against the map and the two treasuries.

    The same discipline the orders workbench keeps: a refusal names the gate it failed and,
    where there is a legal equivalent, says what to write instead. A term set with one bad
    clause is not thrown away wholesale -- the caller decides whether to sign the rest.
    """
    cards: list[TermCard] = []
    for raw in terms or ():
        term = _as_term(raw)
        if term is None:
            cards.append(TermCard(
                ProposedTerm("", "", ""), REFUSED, "a term must name a kind and who owes it"))
            continue
        spec = TERM_KINDS.get(term.kind)
        if spec is None:
            cards.append(TermCard(
                term, REFUSED,
                f"{term.kind!r} is not something a peace can say; the terms are "
                + ", ".join(sorted(TERM_KINDS))))
            continue
        if term.obliged not in belligerents or term.beneficiary not in belligerents:
            cards.append(TermCard(
                term, REFUSED,
                "a term binds the two powers at the table and nobody else"))
            continue
        if term.obliged == term.beneficiary:
            cards.append(TermCard(term, REFUSED, "a power cannot owe itself a term"))
            continue
        problem, instead = _enforceable(conn, world_id, term, belligerents)
        if problem:
            cards.append(TermCard(term, REFUSED, problem, instead))
            continue
        cards.append(TermCard(term, VALID, spec.explain,
                              cost=_cost_of(conn, world_id, term)))
    return cards


def _enforceable(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm,
    belligerents: tuple[str, str],
) -> tuple[str, str]:
    """``(why not, what to write instead)``, or two empty strings if the term stands."""
    spec = TERM_KINDS[term.kind]
    if spec.subject == "region":
        region = conn.execute(
            "SELECT id, name, owner_nation_id AS owner, home_nation_id AS home,"
            "       COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ? AND id = ?",
            (world_id, term.subject)).fetchone()
        if region is None:
            return f"{term.subject or '(nowhere)'} is not a region on this map.", ""
        owner, holder = region["owner"], region["holder"]

        if term.kind == "cede_region":
            if owner != term.obliged:
                return (f"{region['name']} does not belong to {term.obliged}, so it is "
                        "not theirs to cede.", "")
            # **The line the existing cession path draws, kept.** A peace may ratify an
            # occupation the fighting reached; it may not hand over peaceful ground the
            # winner never touched, which would be conquest by paperwork.
            if holder != term.beneficiary:
                return (f"{region['name']} is not occupied by {term.beneficiary}; a peace "
                        "can only settle ground the fighting actually reached.",
                        "take it first, or ask for something you hold.")
            # A capital *may* be ceded, and the seat of government moves with it -- the
            # cession path the resolver has always had does exactly this under rule
            # CORE-005, and a settlement layer that refused would be narrower than the
            # thing it replaced. What is refused is dissolving the country.
            if _is_capital(conn, world_id, term.obliged, str(region["id"])) and not _elsewhere(
                conn, world_id, term.obliged, {str(region["id"])}
            ):
                return (f"{region['name']} is {term.obliged}'s last seat of government; a "
                        "state is not dissolved at the table.",
                        "demand something other than their capital.")
            if _last_province(conn, world_id, term.obliged):
                return (f"{term.obliged} would be left with nothing; a state is not "
                        "dissolved at the table.", "")
        elif term.kind == "restore_region":
            if holder != term.obliged or owner == term.obliged:
                return (f"{term.obliged} is not occupying {region['name']}, so there is "
                        "nothing to restore.", "")
            if owner != term.beneficiary:
                return (f"{region['name']} belongs to {owner}, who is not at this table.",
                        "")
        elif term.kind == "withdraw":
            if not _units_in(conn, world_id, term.obliged, str(region["id"])):
                return (f"{term.obliged} has no forces in {region['name']} to withdraw.",
                        "")
        elif term.kind == "demilitarise" and term.obliged not in (
            region["owner"], region["home"], holder
        ):
            return (f"{region['name']} is nothing to do with {term.obliged}; a "
                    "demilitarised zone names ground one of you holds or owns.", "")
        elif term.kind == "demilitarise" and _stranded(conn, world_id, term):
            # Signing empties the zone (see `_term_diffs`), and forces with nowhere of their
            # own beside it to fall back to would be interned: a whole army disbanded for
            # the price of a demilitarised zone, which is not what the term is priced as.
            return (f"{term.obliged}'s forces in {region['name']} have no ground of their "
                    "own beside it to fall back to.",
                    "demilitarise ground they can withdraw from.")

    if term.kind == "reparations":
        treasury = _treasury(conn, world_id, term.obliged)
        if term.value <= 0:
            return "a reparation has to be worth something.", ""
        if term.value > treasury * REPARATION_CAP:
            return (f"{term.obliged} cannot pay {term.value:.0f}: it holds "
                    f"{treasury:.0f}, and a settlement may ask at most "
                    f"{REPARATION_CAP:.0%} of a treasury.",
                    f"ask for {treasury * REPARATION_CAP:.0f} or less.")
    if term.kind in {"guarantee", "recognise"}:
        subject = term.subject or term.beneficiary
        alive = conn.execute(
            "SELECT is_alive FROM nation WHERE world_id = ? AND id = ?",
            (world_id, subject)).fetchone()
        if alive is None or not int(alive["is_alive"]):
            return f"{subject or '(nobody)'} is not a living nation.", ""
    return "", ""


# --------------------------------------------------------------------------- acceptance


def _cost_of(conn: sqlite3.Connection, world_id: str, term: ProposedTerm) -> float:
    """What one term costs the power that has to carry it out, as a share of what it has.

    Comparable across kinds by construction: a province is measured against the homeland
    it comes out of, money against the treasury it comes out of. That is what lets one
    tolerance number decide a whole term set.
    """
    spec = TERM_KINDS[term.kind]
    if term.kind == "cede_region":
        home = _homeland(conn, world_id, term.obliged)
        row = conn.execute(
            "SELECT home_nation_id FROM region WHERE world_id = ? AND id = ?",
            (world_id, term.subject)).fetchone()
        if row is not None and str(row["home_nation_id"] or "") == term.obliged:
            # Its own people. Measured against the homeland, so one province out of four
            # hurts four times what one out of sixteen does.
            return 1.0 / max(1, home)
        # Somebody else's ground it happened to own -- a colony, a conquest. Real, and
        # nothing like as dear.
        return 0.4 / max(1, home)
    if term.kind == "restore_region":
        held = _occupied_by(conn, world_id, term.obliged)
        return 0.5 / max(1, held)
    if term.kind == "reparations":
        return min(1.0, term.value / max(1.0, _treasury(conn, world_id, term.obliged)))
    return spec.weight


def acceptance(
    conn: sqlite3.Connection, world_id: str, *, offered_to: str, offered_by: str,
    terms, good_offices: float = 0.0,
) -> Acceptance:
    """Whether ``offered_to`` signs. **The engine's decision, and nobody else's.**

    Tolerance is what the position will bear: how far leverage has moved against this
    power, plus how sick of the war it is, and never less than a floor -- nobody fights on
    over a demilitarised valley.
    """
    reviewed = review_terms(
        conn, world_id, belligerents=(offered_to, offered_by), terms=terms)
    cards = [card for card in reviewed if card.ok]
    # **An offer whose every clause was refused is not a white peace.** Filtering to the
    # valid terms and finding nothing left would answer "it signs" to a demand the engine
    # had just thrown out -- which is how an unenforceable demand became a free peace.
    if reviewed and not cards:
        return Acceptance(
            accepted=False, cost=0.0, tolerance=0.0, position=0.0,
            because="none of these terms can be carried out")
    cost = round(sum(card.cost for card in cards
                     if card.term.obliged == offered_to), 4)
    # What the other side is giving up counts for something: a peace in which the winner
    # also restores ground is easier to sign than one in which it does not.
    given = round(sum(card.cost for card in cards
                      if card.term.obliged == offered_by), 4)

    position = leverage_between(conn, world_id, offered_to, offered_by).position
    effort = effort_between(conn, world_id, offered_to, offered_by)
    exhaustion = effort.exhaustion if effort else 0.0
    bearable = (
        max(0.0, -position) * TOLERANCE_FROM_LEVERAGE
        + (exhaustion / 100.0) * TOLERANCE_FROM_EXHAUSTION
        # **And what a respected third party is worth at the table.** Zero for every
        # bilateral offer, which is every offer this function took before mediation
        # existed. Added to what the position will bear rather than to the floor, so the
        # nationalists below still get their say on it -- a broker widens what a country
        # can accept and does not silence the people it has to answer to.
        # See `atlas.engine.mediation.GOOD_OFFICES_MAX`.
        + max(0.0, good_offices))

    # **And then the country has an opinion.** What the position will bear is one thing; what
    # the government can survive signing is another, and the two come apart exactly where the
    # interesting wars are. The floor is applied after, so no mood can push a country below
    # the point where nobody fights on over a demilitarised valley.
    stiffening = patriot_stiffening(
        conn, offered_to, position=position,
        humiliation=_humiliation_of(cards, offered_to))
    tolerance = round(max(TOLERANCE_FLOOR, bearable * (1.0 - stiffening)), 4)

    # **What it is being asked to give, less half of what it is being given back.** A peace
    # in which the winner also restores ground is easier to sign than one in which it does
    # not.
    demanded = round(max(0.0, cost - given * 0.5), 4)

    # **And what its own position says it is owed.** Symmetric with the cost side: a power
    # in a commanding position refuses terms that fall short of what it is winning, which is
    # what stops a beaten enemy buying its way out with a white peace.
    expectation = round(max(0.0, position) * EXPECTATION_FROM_LEVERAGE, 4)
    shortfall = round(max(0.0, expectation - given), 4)

    net = round(demanded + shortfall, 4)
    accepted = net <= tolerance
    if shortfall > demanded and shortfall > 0:
        why = (f"{offered_to} is winning at {position:+.2f} and expects about "
               f"{expectation:.2f} in concessions; this offers it {given:.2f} -- "
               + ("enough." if accepted else "not enough, and it fights on."))
    else:
        why = (f"{offered_to} is at {position:+.2f} and {int(exhaustion)}/100 war-weary, so "
               f"it will bear about {tolerance:.2f}; these terms cost it {net:.2f} -- "
               + ("it signs." if accepted else "it fights on."))
    # Named only when it decided something. A stiffening that changed nothing is noise in a
    # sentence a player has to read every time a war ends.
    if stiffening > 0.01 and (not accepted or net > round(bearable, 4) * (1.0 - stiffening)):
        why += (f" Its nationalists are {band_of(support_of(conn, offered_to, 'patriots'))} "
                f"and took {stiffening * 100:.0f}% off what it could accept.")
    return Acceptance(
        accepted=accepted, cost=net, tolerance=tolerance, position=position, because=why,
        stiffening=round(stiffening, 4))


def patriot_stiffening(
    conn: sqlite3.Connection, nation_id: str, *, position: float, humiliation: float
) -> float:
    """How much the nationalists take off what this power will accept, 0..1.

    Three factors, and each one is a bound. **Mood** is how far the patriots sit from where a
    country rests, so an ordinary nation contributes nothing. **Humiliation** is how much of
    the demand is insult rather than expense, so an indemnity is not a nationalist question.
    **Voice** is whether the position still leaves room for politics, so an annihilated power
    folds regardless -- which is what stops this from overriding the war it is attached to.
    """
    mood = (support_of(conn, nation_id, "patriots") - CIVIC_RESTING) / 50.0
    mood = max(-1.0, min(1.0, mood))
    span = PATRIOTS_HEARD_ABOVE - PATRIOTS_SILENT_AT
    voice = max(0.0, min(1.0, (position - PATRIOTS_SILENT_AT) / span))
    scale = PATRIOTS_STIFFEN if mood > 0 else PATRIOTS_RESIGNED
    return max(-1.0, min(1.0, mood * scale * max(0.0, min(1.0, humiliation)) * voice))


def _humiliation_of(cards, offered_to: str) -> float:
    """The share of what this power is being asked to give that is insult rather than cost.

    Weighted by what each clause costs, so one province out of four counts for more than a
    demilitarised valley -- the nationalists are angrier about a bigger loss, not merely
    about a longer list.
    """
    mine = [card for card in cards if card.term.obliged == offered_to]
    total = sum(card.cost for card in mine)
    if total <= 0:
        return 0.0
    return sum(card.cost * _HUMILIATION.get(card.term.kind, 0.5)
               for card in mine) / total


def suggested_terms(
    conn: sqlite3.Connection, world_id: str, *, loser: str, winner: str
) -> list[ProposedTerm]:
    """What the winner could ask for and the loser would actually sign.

    **Offered, never imposed.** This is what the engine puts in front of a power at the top
    of the exhaustion scale; the model may take it, counter it, or fight on.

    **Every package is put to `acceptance` itself, which is the only way this function can
    keep its own promise.** The first version re-derived the loser's tolerance by hand --
    `acceptance(terms=()).tolerance` scaled by a stiffening computed at humiliation 1.0 --
    and then filled greedily up to that number. It got two things wrong, and the second is
    the one that mattered:

    * the hand-rolled tolerance is not the one `acceptance` computes for the actual
      package, because the stiffening depends on what is in it; and
    * it compared only what the terms *cost*, and `acceptance` decides on
      ``demanded + shortfall`` -- where the shortfall is *the loser's own expectation of
      concessions*, `max(0, position) * EXPECTATION_FROM_LEVERAGE`. A power that is losing
      ground while still **winning the war overall** expects to be given something, and
      none of that appeared in the walk.

    Measured on the modern map, Poland holding one Russian province: Russia at position
    +0.10 expects 0.06, the cession demands 0.08, so `acceptance` sees 0.14 against a
    tolerance of 0.09 and refuses -- while the walk, seeing only the 0.08, had offered it.
    **8 of 9 suggestions were refused by the engine's own acceptance model**, which is
    exactly the "offer nobody would sign" this function's comments say it exists to avoid.

    So the walk now grows a package one province at a time and keeps it only while the
    whole package still passes the same `acceptance` call a settlement would make. That is
    slower -- one acceptance per candidate rather than one per function -- and it is the
    only construction that makes "would actually sign" a property rather than a hope.
    Cheapest first, so the package is as large as the loser will bear; ties on region id,
    so the same world suggests the same treaty twice.

    Returns an empty list when nothing the winner holds can be ceded on terms the loser
    would take -- including the case where the loser is winning badly enough to expect
    concessions instead. Saying so is better than suggesting a treaty that will be refused.
    """
    priced: list[tuple[float, str, ProposedTerm]] = []
    for row in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND controller_nation_id = ? ORDER BY id", (world_id, loser, winner)
    ):
        region_id = str(row["id"])
        term = ProposedTerm("cede_region", obliged=loser, beneficiary=winner,
                            subject=region_id)
        card = review_terms(
            conn, world_id, belligerents=(loser, winner), terms=[term])[0]
        if card.ok:
            priced.append((card.cost, region_id, term))
    priced.sort(key=lambda entry: (entry[0], entry[1]))

    offered: list[ProposedTerm] = []
    for _cost, _region_id, term in priced:
        trial = [*offered, term]
        if acceptance(conn, world_id, offered_to=loser, offered_by=winner,
                      terms=trial).accepted:
            offered = trial
    return offered



# ------------------------------------------------------------------------ carrying it out


def settle(  # noqa: PLR0913 - a settlement is its parties, its terms and its clock
    conn: sqlite3.Connection,
    world_id: str,
    *,
    proposer: str,
    target: str,
    terms,
    turn: int,
    date: str,
    check_acceptance: bool = True,
    mediator: str = "",
) -> Settlement:
    """End the war between two powers on stated terms, if the other side will sign.

    ``mediator`` names a third power that brought them to it -- see
    :mod:`atlas.engine.mediation`. It changes nothing about the settlement itself and is
    recorded on the event, because who ended a war is a fact about the world and the log
    had no word for it until schema 44.

    Everything lands in one batch behind one event: the war ends, the ground moves, the
    agreement is recorded and its standing terms are written down. A refused batch leaves
    the war exactly as it was and takes its own news with it.
    """
    from .diplomacy import next_commitment_id

    outcome = Settlement()
    if not _at_war(conn, world_id, proposer, target):
        outcome.refused.append(f"{proposer} and {target} are not at war")
        return outcome

    cards = review_terms(
        conn, world_id, belligerents=(proposer, target), terms=terms)
    outcome.refused.extend(
        f"{card.term.describe()}: {card.because}"
        + (f" ({card.instead})" if card.instead else "")
        for card in cards if not card.ok)
    good = [card.term for card in cards if card.ok]

    # **A term set with a bad clause in it is not signed without the clause.** Dropping
    # what cannot be done and settling on the rest would hand the proposer a different
    # treaty from the one it offered -- in the worst case a white peace, because every
    # demand it made was refused. The refusals go back with a reason; offering again is
    # cheap, and this is the same discipline the orders workbench keeps.
    if outcome.refused:
        return outcome

    # **A term set can dissolve a country that no single term would.** Each cession is
    # checked against `_last_province` on its own, so two individually legal ones can take
    # everything a power owns between them -- and the batch then fails on CORE-005 with a
    # message about a capital nobody asked to move. Found by the constituencies layer
    # asking what a two-province peace does to the nationalists.
    for who, count in _cessions_by(good).items():
        if _owned(conn, world_id, who) - count < 1:
            outcome.refused.append(
                f"these terms would leave {who} owning nothing; a state is not dissolved "
                "at the table, however many provinces it is asked for one at a time")
            return outcome

    # **The reparations cap is on what a power owes, not on each line of the bill.**
    # `_enforceable` checks every reparation term against the treasury on its own, so four
    # terms at the cap each passed and between them demanded well over twice what the
    # payer holds -- the "reparation nobody can pay" the cap exists to refuse, split up.
    for who, owed in _reparations_by(good).items():
        treasury = _treasury(conn, world_id, who)
        if owed > treasury * REPARATION_CAP + 0.005:
            outcome.refused.append(
                f"{who} cannot pay {owed:.0f} in reparations between these terms: it "
                f"holds {treasury:.0f}, and a settlement may ask at most "
                f"{REPARATION_CAP:.0%} of a treasury in all "
                f"(ask for {treasury * REPARATION_CAP:.0f} or less altogether)")
            return outcome

    # **Nor can a zone be emptied into ground the same peace is also taking away.** Each
    # demilitarise term finds its garrison somewhere to fall back to on its own, so two
    # zones side by side each passed -- and the first zone's garrison was marched into the
    # second, which broke the peace at the first honour pass exactly as leaving it where it
    # stood had. Ground the settlement hands back, cedes, withdraws from or demilitarises
    # is not ground of one's own to fall back to.
    barred = _given_up(good)
    for term in good:
        if term.kind == "demilitarise" and _stranded(conn, world_id, term, barred):
            zone = conn.execute(
                "SELECT name FROM region WHERE world_id = ? AND id = ?",
                (world_id, term.subject)).fetchone()
            outcome.refused.append(
                f"{term.obliged}'s forces in {zone['name'] if zone else term.subject} have "
                "no ground of their own beside it to fall back to that these same terms do "
                "not also take away; demilitarise ground they can withdraw from")
            return outcome

    if check_acceptance:
        verdict = acceptance(
            conn, world_id, offered_to=target, offered_by=proposer, terms=good)
        if not verdict.accepted:
            outcome.refused.append(f"{target} refused the terms: {verdict.because}")
            return outcome

    names = _names(conn, world_id, (proposer, target))
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.PEACE_SIGNED,
        origin=EventOrigin.ENGINE, actor_nation_id=proposer,
        text=(f"{names[proposer]} and {names[target]} signed a peace"
              + (f" brokered by {_names(conn, world_id, (mediator,))[mediator]}"
                 if mediator else "")
              + (": " + "; ".join(term.describe(names) for term in good[:6])
                 if good else " on no terms but an end to the fighting")),
        visibility=Visibility.PUBLIC,
        participants=(("actor", proposer), ("target", target),
                      *((("mediator", mediator),) if mediator else ())))

    commitment_id = next_commitment_id(conn)
    diffs: list[Diff] = [
        Diff("relation", f"{a}|{b}", "at_war", DiffOp.SET, 0,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for a, b in ((proposer, target), (target, proposer))
    ]
    diffs.append(
        Diff("commitment", str(commitment_id), "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id,
                 "kind": CommitmentKind.PEACE_TREATY.value,
                 "status": "active",
                 "condition_text": "; ".join(
                     term.describe(names) for term in good) or "an end to the fighting",
                 "source_event_id": event_id,
                 "created_turn": turn,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    diffs += [
        Diff("commitment_nation", f"{commitment_id}|{nation_id}", "__insert__",
             DiffOp.INSERT, {"role": "party", "accepted_turn": turn},
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for nation_id in (proposer, target)
    ]

    # Every province this settlement moves, so a relocated capital cannot be sent to
    # ground the same treaty is also signing away -- see `_elsewhere`.
    changing = {term.subject for term in good if term.kind == "cede_region"}
    # **One commitment id per guarantee.** Each guarantee is a defensive pact of its own,
    # written in this batch after the treaty itself takes `commitment_id`. Every one of
    # them used to be given `commitment_id + 1`, so a settlement with two guarantees
    # collided on the primary key and was refused whole.
    pact_ids = iter(range(commitment_id + 1, commitment_id + 1 + len(good)))
    for seq, term in enumerate(good):
        diffs += _term_diffs(conn, world_id, term, commitment_id=commitment_id, seq=seq,
                             turn=turn, event_id=event_id, changing=changing,
                             pact_id=next(pact_ids) if term.kind == "guarantee" else None,
                             barred=barred)
        outcome.applied.append(term.describe(names))

    applied = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not applied.applied:
        unrecord_events(conn, [event_id])
        outcome.refused.append(f"the settlement could not be written: {applied.reason}")
        outcome.applied = []
        return outcome

    outcome.signed = True
    outcome.commitment_id = commitment_id
    outcome.event_id = event_id

    # **Carried out after the settlement batch rather than inside it.** `leaders.seat`
    # writes its own diffs and applies its own batch -- it has since PT31 caught a coup a
    # rewind could not undo -- so the change of government is a second batch citing the same
    # settlement event. Folding it in would mean a second copy of "who sits in the chair"
    # here, which is the drift that rule exists to prevent.
    for term in good:
        if term.kind != "depose":
            continue
        from .leaders import leader_of, seat

        going = leader_of(conn, term.obliged)
        if going is None:
            continue
        seat(
            conn, world_id, nation_id=term.obliged, name="", title=going.title,
            # A government installed by the enemy's terms did not choose its own line and
            # the country knows it: no traits carried over, and an agenda that says so.
            traits=(),
            agenda="hold the country together under the terms it was made to sign",
            turn=turn, event_id=event_id, came_to_power="coup",
        )
        outcome.applied.append(
            f"{names.get(term.obliged, term.obliged)} changed its government "
            "under the settlement")

    # **A truce that becomes a peace has done its job.** Left active it would go on
    # pausing the fighting in a war that no longer exists, and would eventually lapse and
    # be reported as an agreement running out between two powers at peace.
    _discharge_truce(conn, world_id, proposer, target, turn=turn, event_id=event_id)

    # **Ground changing hands at a peace table is news, and one kind of it is a `Council`
    # trigger.** The path this replaced wrote a `territory_transfer` event for every
    # annexation and said "recovered" or "annexed" in the headline; both are load-bearing --
    # the Security Council reads those events, and the distinction between taking back your
    # own and keeping somebody else's is the whole of what `home_nation_id` is for. Losing
    # them silently is exactly the sort of regression a rewrite makes.
    for term in good:
        if term.kind != "cede_region":
            continue
        row = conn.execute(
            "SELECT name, home_nation_id AS home FROM region WHERE world_id = ? AND id = ?",
            (world_id, term.subject)).fetchone()
        if row is None:
            continue
        recovered = str(row["home"] or "") == term.beneficiary
        who = names.get(term.beneficiary, term.beneficiary)
        outcome.headlines.append(
            f"{who} {'recovered' if recovered else 'annexed'} {row['name']}.")
        if not recovered:
            record_event(
                conn, world_id=world_id, turn=turn, date=date,
                kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
                actor_nation_id=term.beneficiary,
                text=f"{who} annexed {row['name']}.", visibility=Visibility.PUBLIC,
                participants=(("actor", term.beneficiary),
                              ("beneficiary", term.beneficiary)),
                region_participants=(("target", term.subject),))
    # **The facts come after the batch, not inside it.** A fact is memory of something that
    # happened, and until the batch is applied nothing has. Each is minted with the treaty
    # as its cause, so a rewind past the signing takes them with it.
    for term in good:
        outcome.facts.extend(
            _facts_for(conn, world_id, term, turn=turn, event_id=event_id))
    return outcome


def _cessions_by(terms) -> dict[str, int]:
    """How many provinces each side is being asked to give up, across the whole offer."""
    counted: dict[str, int] = {}
    for term in terms:
        if term.kind == "cede_region":
            counted[term.obliged] = counted.get(term.obliged, 0) + 1
    return counted


def _reparations_by(terms) -> dict[str, float]:
    """Reparations demanded of each power across one settlement's terms."""
    owed: dict[str, float] = {}
    for term in terms:
        if term.kind == "reparations":
            owed[term.obliged] = owed.get(term.obliged, 0.0) + float(term.value)
    return owed


def _owned(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND owner_nation_id = ?",
        (world_id, nation_id)).fetchone()[0])


def _discharge_truce(
    conn: sqlite3.Connection, world_id: str, a: str, b: str, *, turn: int, event_id: int
) -> None:
    """End any standing ceasefire between these two: the peace supersedes it.

    **`expired`, not `fulfilled`, and rule DIP-005 is why.** A ceasefire is a *standing*
    obligation -- one you go on keeping -- and `fulfillment_blockers` refuses to call any
    such agreement completed: "it can be broken, expire, or be terminated, but it is never
    completed". That is right, and it is what caught this: the first version marked the
    truce fulfilled and the batch was refused, silently leaving it active in a war that no
    longer existed. Terminated by a settlement is an ending, not a completion.
    """
    from .armistice import discharge_diffs

    diffs = discharge_diffs(conn, world_id, a, b, turn=turn, event_id=event_id)
    if not diffs:
        return
    apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(diffs)))

def _warheads_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """How many warheads this power holds, or zero if it has no arsenal at all.

    Counts the whole pile, strategic and battlefield alike: an arms-control ceiling is about
    how many weapons exist, and a treaty that counted only some of them would be one every
    signatory could keep by relabelling.
    """
    try:
        row = conn.execute(
            "SELECT warheads FROM arsenal WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    return float(row["warheads"]) if row else 0.0


def _term_diffs(  # noqa: PLR0913 - the identity of a term is five things
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm, *,
    commitment_id: int, seq: int, turn: int, event_id: int,
    changing: set[str] | None = None, pact_id: int | None = None,
    barred: frozenset[str] = frozenset(),
) -> list[Diff]:
    """The row that records a term, plus whatever carrying it out changes now.

    ``pact_id`` is the commitment id a ``guarantee`` term's pact is written under; the
    caller allocates it, because only the caller knows how many other guarantees the same
    batch is writing.
    """
    spec = TERM_KINDS[term.kind]
    outstanding = term.value if term.kind == "reparations" else 0.0
    diffs = [
        Diff("peace_term", f"pt_{commitment_id}_{seq}", "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id, "commitment_id": commitment_id, "seq": seq,
                 "kind": term.kind, "obliged_id": term.obliged,
                 "beneficiary_id": term.beneficiary, "subject_id": term.subject,
                 "value": term.value, "outstanding": outstanding,
                 "due_turn": turn if spec.standing else None,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
    ]

    if term.kind == "cede_region":
        # Owner and controller together: the "ratified cession" the permission model
        # reserves for exactly this moment.
        diffs += [
            Diff("region", term.subject, column, DiffOp.SET, term.beneficiary,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for column in ("owner_nation_id", "controller_nation_id")
        ]
        # **A ceded capital moves, in the same batch.** Rule CORE-005 refuses a nation
        # whose seat of government is somebody else's province, so the two writes have to
        # travel together or the settlement is refused whole.
        if _is_capital(conn, world_id, term.obliged, term.subject):
            moved = _elsewhere(conn, world_id, term.obliged,
                               (changing or {term.subject}) | {term.subject})
            if moved:
                diffs.append(
                    Diff("nation", term.obliged, "capital_region_id", DiffOp.SET, moved,
                         reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    elif term.kind == "restore_region":
        diffs.append(
            Diff("region", term.subject, "controller_nation_id", DiffOp.SET, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    elif term.kind == "withdraw":
        diffs += _withdrawal_diffs(conn, world_id, term, event_id=event_id)
    elif term.kind == "demilitarise":
        # **A zone is emptied when it is signed, then kept empty.** The term reads "the
        # named ground is to *stay* empty", and a war aim of this kind is that the ground
        # "must be emptied of its forces" -- so the settlement carries the emptying out, as
        # `withdraw` does, rather than signing a promise the obliged side is already
        # breaking. Left standing, a garrison broke the peace at the first honour pass and
        # every other term of the treaty stopped being honoured with it.
        #
        # Forces only: who holds the ground is `cede_region`/`restore_region`/`withdraw`
        # business, and the Rhineland stayed German.
        diffs += _withdrawal_diffs(conn, world_id, term, event_id=event_id,
                                   release_ground=False, barred=barred)
    elif term.kind == "guarantee":
        diffs += _guarantee_diffs(
            conn, world_id, term, turn=turn, event_id=event_id,
            pact_id=pact_id if pact_id is not None else commitment_id + 1)
    return diffs


def _withdrawal_diffs(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm, *, event_id: int,
    release_ground: bool = True, barred: frozenset[str] = frozenset(),
) -> list[Diff]:
    """Pull the obliged power's formations out of the named ground.

    **Back a step if there is one, disbanded if there is not.** An army that agreed to
    leave and has nowhere to go is interned, which is what happens to one -- and which is
    better than the alternatives: leaving it there makes the term a lie, and teleporting it
    home makes a withdrawal a free redeployment.
    """
    diffs: list[Diff] = []
    for unit_id, domain in _formations_in(conn, world_id, term.obliged, term.subject):
        fallback = _fallback_for(conn, world_id, term, domain=domain, barred=barred)
        if fallback:
            diffs.append(
                Diff("unit", unit_id, "region_id", DiffOp.SET, fallback,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        else:
            diffs.append(
                Diff("unit", unit_id, "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    if not release_ground:
        return diffs
    # Ground somebody withdraws from is ground they no longer hold.
    row = conn.execute(
        "SELECT owner_nation_id AS owner, controller_nation_id AS holder FROM region"
        " WHERE world_id = ? AND id = ?", (world_id, term.subject)).fetchone()
    if row is not None and str(row["holder"] or "") == term.obliged:
        diffs.append(
            Diff("region", term.subject, "controller_nation_id", DiffOp.SET, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    return diffs


def _guarantee_diffs(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm, *,
    turn: int, event_id: int, pact_id: int,
) -> list[Diff]:
    """A guarantee is a defensive pact, written as one so Article 5 reads it."""
    beneficiary = term.subject or term.beneficiary
    return [
        Diff("commitment", str(pact_id), "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id,
                 "kind": CommitmentKind.DEFENSIVE_PACT.value,
                 "status": "active",
                 "condition_text": f"{term.obliged} guarantees {beneficiary} by treaty",
                 "source_event_id": event_id,
                 "created_turn": turn,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        *[
            Diff("commitment_nation", f"{pact_id}|{nation_id}", "__insert__",
                 DiffOp.INSERT, {"role": "party", "accepted_turn": turn},
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for nation_id in sorted({term.obliged, beneficiary})
        ],
    ]


def _facts_for(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm, *,
    turn: int, event_id: int,
) -> list[str]:
    """What the world now takes for granted because of this term."""
    minted: list[str] = []
    if term.kind == "cede_region":
        report = record_fact(
            conn, world_id, subject_kind="nation", subject_id=term.obliged,
            predicate="renounces", object_id=term.subject, turn=turn,
            source_event_id=event_id)
        minted.extend(fact.describe() for fact in report.recorded)
    elif term.kind == "recognise":
        report = record_fact(
            conn, world_id, subject_kind="nation", subject_id=term.obliged,
            predicate="recognises", object_id=term.subject or term.beneficiary,
            turn=turn, source_event_id=event_id)
        minted.extend(fact.describe() for fact in report.recorded)
    elif term.kind == "demilitarise":
        report = record_fact(
            conn, world_id, subject_kind="region", subject_id=term.subject,
            predicate="demilitarised",
            value=f"no {term.obliged} forces, by the peace of turn {turn}",
            turn=turn, source_event_id=event_id)
        minted.extend(fact.describe() for fact in report.recorded)
    return minted


# ---------------------------------------------------------------- keeping the promises


#: Why a broken term is broken, per kind: the clause on the public `treaty_broken` event,
#: and the phrase in the turn report. Keyed by the term's kind so an arms-control breach
#: is not reported as a march into a zone -- the only kind there used to be.
_BREACH_WHY = {
    "demilitarise": "marched back into a zone it had agreed to leave",
    "limit_arsenal": "built its arsenal past the ceiling it had agreed to",
}
_BREACH_DID = {
    "demilitarise": "remilitarising a zone it had agreed to leave",
    "limit_arsenal": "building its arsenal past the ceiling it had agreed to",
}


def honour_peace_terms(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Pay this turn's instalments, and notice a zone that has been remilitarised.

    Run each turn from the turn engine, after the fighting. Reparations move money; a
    demilitarised zone with the obliged power's forces standing in it is a broken promise,
    and it is broken through the machinery every other agreement is broken through.
    """
    from .treaties import breach

    said: list[str] = []
    # Which kind of term each breach in `broken` broke, so its reason can say so.
    broke_kind: dict[tuple[int, str], str] = {}
    outstanding = list(conn.execute(
        "SELECT p.id, p.commitment_id, p.kind, p.obliged_id, p.beneficiary_id,"
        "       p.subject_id, p.outstanding, p.value FROM peace_term p"
        "  JOIN commitment c ON c.id = p.commitment_id"
        " WHERE p.world_id = ? AND c.status = 'active'"
        " ORDER BY p.commitment_id, p.seq", (world_id,)))
    if not outstanding:
        return said

    diffs: list[Diff] = []
    broken: list[tuple[int, str]] = []
    # **A running balance, not the treasury as it stood before the pass.** Every instalment
    # this pass lands in one batch, so reading `_treasury` per term saw the same untouched
    # figure each time: a payer owing on several terms passed every check and was
    # overdrawn by their sum. Receipts count as well, since they land in the same batch.
    balance: dict[str, float] = {}

    def _balance(nation_id: str) -> float:
        if nation_id not in balance:
            balance[nation_id] = _treasury(conn, world_id, nation_id)
        return balance[nation_id]

    for row in outstanding:
        if str(row["kind"]) == "reparations" and float(row["outstanding"]) > 0.01:
            due = round(min(float(row["outstanding"]),
                            max(1.0, float(row["outstanding"]) * REPARATION_INSTALMENT)), 2)
            payer = str(row["obliged_id"])
            payee = str(row["beneficiary_id"])
            # **What cannot be paid is not paid, and is still owed**: the instalment is
            # skipped and the outstanding sum carries over to next month, which is what
            # an unaffordable instalment has always done here. It is never paid by
            # overdrawing the treasury.
            if _balance(payer) < due:
                continue
            balance[payer] = _balance(payer) - due
            balance[payee] = _balance(payee) + due
            diffs += [
                Diff("nation", payer, "treasury", DiffOp.ADJUST, -due),
                Diff("nation", str(row["beneficiary_id"]), "treasury", DiffOp.ADJUST, due),
                Diff("peace_term", str(row["id"]), "outstanding", DiffOp.SET,
                     round(float(row["outstanding"]) - due, 2)),
            ]
            said.append(f"{payer} paid {due:.0f} of its reparations")
        elif str(row["kind"]) == "demilitarise" and _units_in(
            conn, world_id, str(row["obliged_id"]), str(row["subject_id"])
        ):
            broken.append((int(row["commitment_id"]), str(row["obliged_id"])))
            broke_kind.setdefault(broken[-1], "demilitarise")
        elif str(row["kind"]) == "limit_arsenal" and _warheads_of(
            conn, world_id, str(row["obliged_id"])
        ) > float(row["value"]):
            # **Checked rather than enforced.** Nothing here confiscates a warhead: an arms
            # control treaty does not reach into a silo, it gives the other side a reason.
            # Building past the ceiling is a broken agreement and is charged as one.
            broken.append((int(row["commitment_id"]), str(row["obliged_id"])))
            broke_kind.setdefault(broken[-1], "limit_arsenal")

    if diffs:
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.COMMITMENT_FULFILLED, origin=EventOrigin.ENGINE,
            text="This month's reparations were paid.", visibility=Visibility.PUBLIC)
        applied = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None,
                      diffs=tuple(
                          Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                               reason_event_id=event_id,
                               visibility_scope=Visibility.PUBLIC) for d in diffs)))
        if not applied.applied:
            unrecord_events(conn, [event_id])
            said = []

    for commitment_id, by_whom in dict.fromkeys(broken):
        # The reason is per-kind, so a report says which promise was broken rather than
        # naming the commonest one for every breach.
        # **The existing machinery, not a second copy of it.** Marching back into a zone you
        # promised to leave is a broken agreement, and `breach` is what charges for one:
        # the status, the regard, the legitimacy, and a casus belli for the injured party.
        if breach(conn, world_id, commitment_id=commitment_id, violator=by_whom,
                  turn=turn, date=date,
                  why=_BREACH_WHY[broke_kind[(commitment_id, by_whom)]]):
            said.append(f"{by_whom} broke the peace by "
                        f"{_BREACH_DID[broke_kind[(commitment_id, by_whom)]]}")
    return said



# ------------------------------------------------------------------- the way in


#: How a term reads when a *model* writes one. Deliberately not the internal shape: a model
#: says which side must do a thing ("me" or "them") rather than naming nation ids, so it
#: cannot bind a third party by typing the wrong string, and the engine resolves the sides
#: from the war it is already in.
TERM_SIDES = ("me", "them")


def term_schema() -> dict:
    """The JSON shape of a proposed term. One vocabulary, three doors.

    The tracked-agent channel, the world simulator's declarations and the player's parser
    all describe a term this way, so a peace offered by a model, by the simulator or by a
    human is the same object by the time :func:`review_terms` sees it.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "from"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(TERM_KINDS),
                "description": "; ".join(
                    f"{name}: {spec.explain}" for name, spec in sorted(TERM_KINDS.items())),
            },
            "from": {
                "type": "string",
                "enum": list(TERM_SIDES),
                "description": (
                    "Which side carries this term out: 'them' for a concession you are "
                    "asking for, 'me' for one you are offering."
                ),
            },
            "region": {
                "type": "string",
                "description": (
                    "The region id, for cede_region, restore_region, withdraw and "
                    "demilitarise."
                ),
            },
            "value": {
                "type": "number",
                "description": "For reparations: how much, in treasury.",
            },
        },
    }


def terms_from(declared, *, me: str, them: str) -> list[ProposedTerm]:
    """Turn model-written terms into the internal shape, resolving 'me' and 'them'."""
    found: list[ProposedTerm] = []
    for raw in declared or ():
        if not isinstance(raw, dict):
            continue
        side = str(raw.get("from") or "them").strip().lower()
        obliged = me if side == "me" else them
        try:
            value = float(raw.get("value") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        found.append(ProposedTerm(
            kind=str(raw.get("kind") or "").strip().lower(),
            obliged=obliged,
            beneficiary=them if obliged == me else me,
            subject=str(raw.get("region") or raw.get("subject") or ""),
            value=value))
    return found


def offer_peace(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, declared,
    turn: int, date: str,
) -> Settlement:
    """A power's peace offer, as its own answer wrote it.

    **The channel this layer was missing.** Everything above was reachable only from engine
    code and from the player's typed orders: a tracked power could be told it was beaten,
    shown its exhaustion and its position, and had no sentence available for *I will stop
    on these terms*. Caught by the reachability pre-flight before a live run was spent on
    proving it -- the same gap the proxy-war layer shipped with.

    Intent, checked and then written, exactly like the objectives and war-aims channels:
    the engine reviews every term, decides acceptance itself, and refuses with a reason the
    power is told.
    """
    from . import reference

    outcome = Settlement()
    if not isinstance(declared, dict) or not declared:
        return outcome
    against = reference.resolve(conn, world_id, "nation", declared.get("against"))
    if against.ambiguous:
        outcome.refused.append(against.problem or "the offer named nobody the map knows")
        return outcome
    enemy = against.value or str(declared.get("against") or "")
    if not enemy or enemy == nation_id:
        outcome.refused.append(
            f"{declared.get('against')!r} is nobody you can make peace with")
        return outcome

    # **An offer to the player is the player's to answer**, never the engine's on their
    # behalf. See `resolution._put_to_the_player` for the play-test that found it.
    if _played(conn, world_id, enemy) and not _played(conn, world_id, nation_id):
        from .resolution import offer_to_player

        kind = str(declared.get("kind") or "peace").strip().lower()
        name = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                            (world_id, nation_id)).fetchone()
        outcome.to_player = offer_to_player(
            str(name["name"]) if name else nation_id,
            "ceasefire" if kind == "ceasefire" else "peace")
        outcome.refused.append(f"{enemy} is played by a person; the offer was put to them")
        return outcome

    # **A truce and a settlement come through the same door**, because from a staff's
    # point of view they are the same decision made two ways: stop for a while, or stop
    # for good. What separates them is what the engine then does -- see
    # :mod:`atlas.engine.armistice`.
    if str(declared.get("kind") or "peace").strip().lower() == "ceasefire":
        from .armistice import ARMISTICE_MONTHS, offer_ceasefire

        truce = offer_ceasefire(
            conn, world_id, nation_id=nation_id, enemy_id=enemy,
            months=int(declared.get("months") or 0) or ARMISTICE_MONTHS,
            turn=turn, date=date)
        outcome.refused.extend(truce.refused)
        if truce.agreed:
            outcome.signed = True
            outcome.commitment_id = truce.commitment_id
            outcome.applied.append(
                f"a ceasefire with {enemy} until "
                f"{truce.expires_on or f'turn {truce.expires_turn}'}; the war goes on")
            # A shortened truce is not a refused one -- it stands -- so it goes with what
            # the settlement *did* rather than with what it could not do. Filing it under
            # `refused` would have the turn report announce a signed ceasefire and a
            # rejection for the same act.
            outcome.applied.extend(truce.notes)
        else:
            outcome.refused.extend(truce.notes)
        return outcome

    terms = terms_from(declared.get("terms"), me=nation_id, them=enemy)
    # Region names are resolved the way every other channel resolves them: a staff writing
    # `Bayern` should not be refused for not writing `r_wger_bayern`.
    resolved: list[ProposedTerm] = []
    for term in terms:
        subject = term.subject
        if subject:
            kind = "nation" if TERM_KINDS.get(term.kind) and TERM_KINDS[
                term.kind].subject == "nation" else "region"
            found = reference.resolve(conn, world_id, kind, subject)
            if found.ambiguous:
                outcome.refused.append(found.problem or f"{subject!r} is unclear")
                return outcome
            subject = found.value or subject
        resolved.append(
            ProposedTerm(term.kind, term.obliged, term.beneficiary, subject, term.value))

    return settle(conn, world_id, proposer=nation_id, target=enemy, terms=resolved,
                  turn=turn, date=date)

# --------------------------------------------------------------------------- internals


def _as_term(raw) -> ProposedTerm | None:
    if isinstance(raw, ProposedTerm):
        return raw
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    obliged = str(raw.get("obliged") or raw.get("from") or "")
    beneficiary = str(raw.get("beneficiary") or raw.get("to") or "")
    if not kind or not obliged or not beneficiary:
        return None
    try:
        value = float(raw.get("value") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return ProposedTerm(
        kind=kind, obliged=obliged, beneficiary=beneficiary,
        subject=str(raw.get("region") or raw.get("subject") or ""), value=value)


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?"
        "   AND at_war = 1", (world_id, a, b)).fetchone() is not None


def _units_in(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> list[str]:
    return [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM unit WHERE world_id = ? AND nation_id = ? AND region_id = ?"
            " ORDER BY id", (world_id, nation_id, region_id))
    ]


def _fallback_for(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm, *,
    domain: str | None = None, barred: frozenset[str] = frozenset(),
) -> str | None:
    """The ground of its own, beside the named ground, the obliged side falls back to.

    **Ground a formation of this ``domain`` can stand on.** A fleet in a sea the obliged
    side had agreed to clear was sent to the nearest *land* province of its own, rule
    UNIT-002 refused the batch, and the whole settlement went with it -- over a warship.
    A fleet falls back to water or a coast; everything else to land or a coast, as before.

    ``barred`` is ground the same settlement takes away from the obliged side, which is
    not somewhere of its own to go. See :func:`_given_up`.
    """
    neighbours = {
        str(row["other"])
        for row in conn.execute(
            "SELECT CASE WHEN region_a = ? THEN region_b ELSE region_a END AS other"
            "  FROM region_adjacency WHERE world_id = ? AND ? IN (region_a, region_b)",
            (term.subject, world_id, term.subject))
    } - barred
    unsuitable = "land" if domain == "sea" else "ocean"
    return next(
        (str(row["id"]) for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> ?"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ? ORDER BY id",
            (world_id, unsuitable, term.obliged))
         if str(row["id"]) in neighbours),
        None)


def _formations_in(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> list[tuple[str, str | None]]:
    """``(unit id, domain)`` for each of this power's formations in the named ground."""
    return [
        (str(row["id"]), row["domain"]) for row in conn.execute(
            "SELECT u.id, t.domain FROM unit u"
            "  LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind"
            " WHERE u.world_id = ? AND u.nation_id = ? AND u.region_id = ?"
            " ORDER BY u.id", (world_id, nation_id, region_id))
    ]


def _stranded(
    conn: sqlite3.Connection, world_id: str, term: ProposedTerm,
    barred: frozenset[str] = frozenset(),
) -> list[str]:
    """The obliged side's formations in the named ground with nowhere of their own to go."""
    return [
        unit_id
        for unit_id, domain in _formations_in(conn, world_id, term.obliged, term.subject)
        if _fallback_for(conn, world_id, term, domain=domain, barred=barred) is None
    ]


#: The terms that take a province away from somebody's forces: handed over, handed back,
#: left, or kept empty. None of them is ground to fall back to.
_GIVES_UP_GROUND = frozenset({"cede_region", "restore_region", "withdraw", "demilitarise"})


def _given_up(terms) -> frozenset[str]:
    """Every province one settlement's terms take away, whoever from."""
    return frozenset(term.subject for term in terms
                     if term.kind in _GIVES_UP_GROUND and term.subject)


def _treasury(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    row = conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    return float(row["treasury"]) if row else 0.0


def _homeland(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean'", (world_id, nation_id)).fetchone()[0])


def _occupied_by(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND controller_nation_id = ?"
        "   AND owner_nation_id <> ?", (world_id, nation_id, nation_id)).fetchone()[0])


def _is_capital(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> bool:
    row = conn.execute(
        "SELECT capital_region_id FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    return row is not None and str(row["capital_region_id"] or "") == region_id


def _elsewhere(
    conn: sqlite3.Connection, world_id: str, nation_id: str, besides: set[str]
) -> str:
    """Somewhere else this nation keeps, for a seat of government to move to.

    **Every province the settlement moves, not just the one this term names.** Ceding two
    provinces where one is the capital sent the seat of government to the other one -- also
    changing hands in the same batch -- and CORE-005 refused the whole treaty with a
    message about a capital nobody had asked to move. Found by the constituencies layer
    asking what a two-province peace does to the nationalists.
    """
    marks = ",".join("?" * len(besides)) or "''"
    row = conn.execute(
        f"SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"  # noqa: S608
        f"   AND kind <> 'ocean' AND id NOT IN ({marks})"
        " ORDER BY development DESC, id LIMIT 1",
        (world_id, nation_id, *sorted(besides))).fetchone()
    return str(row["id"]) if row else ""


def _last_province(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    return int(conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND owner_nation_id = ?",
        (world_id, nation_id)).fetchone()[0]) <= 1


def _names(
    conn: sqlite3.Connection, world_id: str, ids: tuple[str, ...]
) -> dict[str, str]:
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    names.update({
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM region WHERE world_id = ?", (world_id,))
    })
    for nation_id in ids:
        names.setdefault(nation_id, nation_id)
    return names


def _played(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    row = conn.execute("SELECT is_player FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return bool(row and row["is_player"])
