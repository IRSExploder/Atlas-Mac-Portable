"""What a government is trying to arrange in the market, and the engine going and doing it.

**Trade was something that happened to a country, never something it did.** A route existed
because the seeder plumbed one at world creation or because a treaty was signed and
:func:`atlas.engine.treaties.honour_terms` honoured it. In between, a government watching
its only oil supplier walk a price up had exactly two moves available: sign a whole
diplomatic agreement with somebody else, or do nothing. It could not say *find me another
supplier*. The briefing that :mod:`atlas.engine.consent` now prints tells a player to
diversify, and until this module there was nothing on the other end of that advice.

**The propose/decide split, for the fourth time in this layer and in the same shape.** A
model proposes that two powers trade; :func:`atlas.engine.trade.worth_selling` decides what
and how much; :mod:`atlas.engine.pricing` decides the price;
:mod:`atlas.engine.consent` decides whether either side will wear it. This adds the step in
front of all of them: a government names *an intention* -- a good, a direction, and at most
a counterparty -- and the engine goes looking. Everything a model could abuse is still on
the engine's side of the line. It cannot name a volume, cannot name a price, cannot open a
route to a power it cannot reach, and cannot make anybody sell to it.

**Three kinds, and each is a sentence a trade minister would actually say:**

``seek``
    *Seek oil from Turkey*, or just *seek oil*. This power wants a supply of a good. Named
    counterparty optional: with one, the engine tries that power and reports if it cannot;
    without one, it looks at everybody in reach and takes the cheapest lane it can actually
    open.
``sell``
    *Sell steel to Germany.* The mirror, and it exists because an exporter has a strategic
    problem too -- a seller whose lanes are being cut takes the distress discount, and
    finding another outlet is the thing that ends the discount.
``diversify``
    *Diversify away from Russia.* Names the power to get away from rather than the one to go
    to, which is how the decision is actually framed, and is the direct answer to the
    briefing line that says a captive buyer is paying a captive buyer's premium.

**The band is the gate, and that is the elegant part.** Nothing here asks whether two
powers like each other, because it does not have to: an intent opens a route only where
:func:`atlas.engine.consent.band` says the price the engine would set falls inside what both
sides would accept. Two powers who loathe each other have no overlap, so no route opens, and
the reason the intent reports is the same sentence the briefing prints about every other
suspended lane. One rule, consulted in two places, rather than a second policy about who may
trade with whom.

**A commercial approach, not a treaty, and the distinction is deliberate.** A route opened
from an intention carries no ``commitment``: nothing was signed, nobody is obliged, and it
can suspend or lapse under :mod:`atlas.engine.consent` with no breach, no regard charge and
no casus belli, because there is no agreement to break. That is what buying oil on the open
market *is*. A power that wants the guarantee still has to negotiate one, and that is still
:mod:`atlas.engine.treaties`. See the branch report for the open question this leaves.

**Staged behind the pricing flag.** :func:`pursue` does nothing at all unless
:func:`atlas.engine.pricing.enabled` -- an intention in a barter world would open a route
that moves goods for free, which is a larger change than this module is entitled to make and
one nobody asked for. With pricing down, which is the default, a world plays exactly as it
did.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import consent, embargo, pricing
from .trade import GOODS, is_unreachable, pricing_columns_present, route_is_open, sellable

__all__ = [
    "CANDIDATE_CAP",
    "KINDS",
    "MAX_STANDING",
    "Intent",
    "IntentReport",
    "describe",
    "propose",
    "pursue",
    "retire",
    "standing",
]

#: The three intentions a government may hold. Three rather than a dozen for the reason
#: :mod:`atlas.engine.directives` gives about its own six: a sentence a minister could say
#: is a mechanic, and a list of twenty is a spreadsheet.
KINDS: tuple[str, ...] = ("seek", "sell", "diversify")

#: How many standing intentions one power may hold at once.
#:
#: **A cap rather than a cost, because an intention is free and being free is the point.**
#: Nothing here charges a treasury or occupies a queue slot, so without a limit a staff
#: could name every good against every power on the map and turn the route-seeking walk
#: into a world-sized cross product every turn. Four is one per good with one spare, which
#: is as much purchasing policy as any government has.
MAX_STANDING = 4

#: How many counterparties one intention looks at before it settles for the best it found.
#:
#: The sea walk in :func:`atlas.engine.trade.route_is_open` is the expensive thing in this
#: layer -- the same walk :data:`atlas.engine.pricing._ALTERNATIVES_CAP` exists to bound --
#: and the candidates are examined in surplus order, so the cap keeps the sellers that can
#: actually supply somebody and drops the ones with a barrel to spare.
CANDIDATE_CAP = 6


@dataclass(frozen=True, slots=True)
class Intent:
    """One standing intention, as the table holds it."""

    id: str
    nation_id: str
    kind: str
    good: str
    partner_id: str = ""
    opened_turn: int = 0
    status: str = "standing"
    note: str = ""

    def as_line(self, name_of) -> str:
        """The intention in the government's own voice. ``name_of`` maps an id to a name so
        this does not need a connection to render."""
        if self.kind == "seek":
            head = (f"seek {self.good} from {name_of(self.partner_id)}"
                    if self.partner_id else f"seek a supply of {self.good}")
        elif self.kind == "sell":
            head = (f"sell {self.good} to {name_of(self.partner_id)}"
                    if self.partner_id else f"find a buyer for {self.good}")
        else:
            head = f"diversify {self.good} away from {name_of(self.partner_id)}"
        if self.status == "met":
            return head + " -- arranged"
        return head + (f" -- {self.note}" if self.note else "")


@dataclass(slots=True)
class IntentReport:
    """What a turn's pursuit came to."""

    #: Routes the engine opened off the back of an intention, in the words a player reads.
    opened: list[str] = field(default_factory=list)
    #: Intentions it looked at and could not act on this turn, and why.
    unmet: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row is not None else nation_id


def describe(conn: sqlite3.Connection, item: Intent) -> str:
    """One intention in the government's own voice, with the powers named.

    :meth:`Intent.as_line` takes a mapper rather than a connection because the briefing that
    calls it already holds a cached one; this is the door for every other caller, so that a
    turn report does not end up printing a raw nation id at a player.
    """
    return item.as_line(lambda nation_id: _name(conn, nation_id))


def _table_present(conn: sqlite3.Connection) -> bool:
    """Whether this world's schema carries the intent table at all.

    The pre-migration guarantee, asked of the database rather than assumed -- the same check
    :func:`atlas.engine.trade.pricing_columns_present` makes, for the same reason. A world
    written before schema 47 has nowhere to put an intention and must not be crashed by one.
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trade_intent'"
    ).fetchone() is not None


def standing(
    conn: sqlite3.Connection, world_id: str, nation_id: str = "",
    *, include_met: bool = False,
) -> list[Intent]:
    """Every intention still being worked on, oldest first, ordered by id after that."""
    if not _table_present(conn):
        return []
    wanted = ("standing", "met") if include_met else ("standing",)
    rows = conn.execute(
        "SELECT * FROM trade_intent"
        f"  WHERE world_id = ? AND status IN ({', '.join('?' for _ in wanted)})"
        "    AND (? = '' OR nation_id = ?)"
        "  ORDER BY opened_turn, id",
        (world_id, *wanted, nation_id, nation_id),
    ).fetchall()
    return [
        Intent(
            id=str(r["id"]), nation_id=str(r["nation_id"]), kind=str(r["kind"]),
            good=str(r["good"]), partner_id=str(r["partner_id"]),
            opened_turn=int(r["opened_turn"]), status=str(r["status"]),
            note=str(r["note"]),
        )
        for r in rows
    ]


def _holding_a_slot(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> set[str]:
    """The intentions that consume this government's budget of :data:`MAX_STANDING`.

    **The budget is commitments, not a career total.** A ``met`` intention is one that has
    been arranged, and what it costs to go on holding is the arrangement -- so it occupies a
    place for exactly as long as that trade is running, and gives the place back when the
    lane closes. Counting ``met`` rows forever, which is what this did, meant a government
    that arranged four things well could never adopt a fifth intention for the rest of the
    game; and the refusal it was handed told it to "retire one", which no schema field and
    no model-facing action could do. A budget nothing can free is not a budget, it is an
    expiry date.

    **A suspended lane counts as closed, deliberately**, because nothing is moving on it and
    the government is not being asked to hold anything up. The consequence is that a lane
    which suspends and later resumes can leave a government briefly over its cap: the place
    was given back and spent, and then the commitment returned. That is benign -- `propose`
    refuses at or above the cap, so the state self-corrects as arrangements end, and nothing
    downstream reads this count -- and it is the honest reading of "the budget is what you
    are currently holding up". Counting only *deleted* routes would avoid it, at the price of
    a permanently suspended lane holding a place forever, which is the original defect again
    in a milder form.

    Derived rather than stored: no status column has to be swept, no diff has to be written,
    and a rewind puts the answer back because it puts the routes back. See the
    "one derivation instead of two" argument on the dependency lens.
    """
    met_holds = {}
    for intent in standing(conn, world_id, nation_id, include_met=True):
        if intent.status != "met":
            met_holds[intent.id] = True
            continue
        row = conn.execute(
            "SELECT suspended_turn FROM trade_route WHERE world_id = ? AND id = ?",
            (world_id, f"tr_{intent.id}"),
        ).fetchone()
        met_holds[intent.id] = row is not None and int(row["suspended_turn"]) < 0
    return {i for i, holds in met_holds.items() if holds}


def _intent_id(nation_id: str, kind: str, good: str, partner_id: str) -> str:
    """Derived from what the intention *is*, not from a counter.

    Two identical intentions are one intention, so proposing the same thing twice is not two
    rows -- and a rewind that removes the row and a replay that re-proposes it produce the
    same id and the same digest. A counter would make the digest depend on how many
    intentions had ever been proposed, which is exactly the kind of hidden ordering a replay
    is supposed to be free of.
    """
    return f"ti_{nation_id}_{kind}_{good}" + (f"_{partner_id}" if partner_id else "")


def propose(  # noqa: PLR0913 - a proposal names all of this or it is not one
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, kind: str, good: str,
    partner_id: str = "", turn: int, date: str,
) -> tuple[Intent | None, str]:
    """Record an intention a government has proposed. ``(intent, problem)``.

    **The one door onto the table**, which is why every column of ``trade_intent`` is in
    :data:`atlas.state.permissions.ENGINE_ONLY`: a model that could write the row directly
    could mark its own intention met, plant one on a rival, or name a good the world does
    not have. Here every field is checked against the map before anything is written, and
    the row goes in through the diff pipeline as an engine batch like every other write, so
    a rewind removes it.

    Refusals name what to do instead, in the shape the rest of the engine keeps: a refusal
    that only says no is one the staff will make again next turn.
    """
    if not _table_present(conn):
        return None, ("this world was written before trade intentions existed; "
                      "negotiate a trade agreement instead")
    kind = str(kind or "").strip().lower()
    good = str(good or "").strip().lower()
    partner_id = str(partner_id or "").strip()
    if kind not in KINDS:
        return None, f"{kind!r} is not an intention; name one of {', '.join(KINDS)}"
    if good not in GOODS:
        return None, f"{good!r} is not a traded good; name one of {', '.join(GOODS)}"
    if partner_id == nation_id:
        return None, "a power cannot trade with itself; name somebody else or nobody"
    if kind == "diversify" and not partner_id:
        return None, ("diversifying away from nobody in particular is not a policy; name "
                      "the supplier you want to depend on less, or seek the good instead")
    if partner_id:
        known = conn.execute(
            "SELECT 1 FROM nation WHERE world_id = ? AND id = ? AND is_alive = 1",
            (world_id, partner_id),
        ).fetchone()
        if known is None:
            return None, (f"there is no power {partner_id!r} to trade with; leave the "
                          "counterparty out and the engine will find whoever is in reach")

    existing = _holding_a_slot(conn, world_id, nation_id)
    intent_id = _intent_id(nation_id, kind, good, partner_id)
    if intent_id in existing:
        return None, "this government is already trying to arrange exactly that"
    if len(existing) >= MAX_STANDING:
        return None, (f"a government may pursue {MAX_STANDING} trade intentions at once, "
                      "and each arrangement it has already made holds one of those places "
                      "for as long as that trade is running; wait for one to lapse, or for "
                      "a lane to close, before adopting another")

    # **An intention that has been here before is reopened, not inserted again.**
    # `_intent_id` is derived from what the intention *is*, so going after a lane this
    # government once arranged and has since lost produces the id that is already on the
    # row. Freeing the place without this would hand back a place that could not be spent
    # on the thing that freed it: the duplicate check passes, the insert then fails on the
    # primary key, and the government is told its intention "could not be recorded".
    # Reopening is also the truthful record -- it is the same intention, wanted again --
    # and it keeps the derived id doing the job it was chosen for.
    prior = conn.execute(
        "SELECT status FROM trade_intent WHERE world_id = ? AND id = ?",
        (world_id, intent_id),
    ).fetchone()

    columns = {
        "world_id": world_id, "nation_id": nation_id, "kind": kind, "good": good,
        "partner_id": partner_id, "opened_turn": turn, "status": "standing", "note": "",
    }
    intent = Intent(id=intent_id, nation_id=nation_id, kind=kind, good=good,
                    partner_id=partner_id, opened_turn=turn)
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        actor_nation_id=nation_id,
        text=f"{_name(conn, nation_id)} sets out to {intent.as_line(lambda n: _name(conn, n))}",
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=(
                      Diff("trade_intent", intent_id, "__insert__", DiffOp.INSERT,
                           columns, reason_event_id=event_id,
                           visibility_scope=Visibility.PUBLIC),
                  ) if prior is None else (
                      Diff("trade_intent", intent_id, "status", DiffOp.SET, "standing",
                           reason_event_id=event_id,
                           visibility_scope=Visibility.PUBLIC),
                      Diff("trade_intent", intent_id, "opened_turn", DiffOp.SET, turn,
                           reason_event_id=event_id,
                           visibility_scope=Visibility.PUBLIC),
                      Diff("trade_intent", intent_id, "note", DiffOp.SET, "",
                           reason_event_id=event_id,
                           visibility_scope=Visibility.PUBLIC),
                  )),
    )
    if not outcome.applied:
        return None, f"the intention could not be recorded: {outcome.reason}"
    return intent, ""


def retire(
    conn: sqlite3.Connection, world_id: str, intent_id: str, *, turn: int, date: str,
    why: str = "",
) -> bool:
    """Withdraw a standing intention. The row stays, marked ``retired``, so a rewind can
    put it back the way it found it -- a deleted row and a restored one are the same
    problem the route table already solves by keeping every column."""
    if not _table_present(conn):
        return False
    row = conn.execute(
        "SELECT nation_id FROM trade_intent WHERE world_id = ? AND id = ? AND status = 'standing'",
        (world_id, intent_id),
    ).fetchone()
    if row is None:
        return False
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        actor_nation_id=str(row["nation_id"]),
        text=f"{_name(conn, str(row['nation_id']))} sets aside a trade intention"
             + (f": {why}" if why else ""),
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=(
                      Diff("trade_intent", intent_id, "status", DiffOp.SET, "retired",
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
                      Diff("trade_intent", intent_id, "note", DiffOp.SET, why[:200],
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
                  )),
    )
    return outcome.applied


def _already_supplying(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str
) -> bool:
    return conn.execute(
        "SELECT 1 FROM trade_route"
        "  WHERE world_id = ? AND seller_id = ? AND buyer_id = ? AND good = ? LIMIT 1",
        (world_id, seller, buyer, good),
    ).fetchone() is not None


def _candidates(
    conn: sqlite3.Connection, world_id: str, *, good: str, exclude: set[str],
) -> list[str]:
    """Powers that dig up more of this good than they burn, most spare first.

    Ordered by surplus so the cap keeps the suppliers that could actually carry a country,
    and by id after that so two worlds in the same state look at the same list.
    """
    rows = conn.execute(
        "SELECT r.nation_id AS nation_id, r.produced AS produced FROM resource r"
        "  JOIN nation n ON n.id = r.nation_id"
        " WHERE r.world_id = ? AND r.good = ? AND n.is_alive = 1"
        " ORDER BY r.produced DESC, r.nation_id",
        (world_id, good),
    ).fetchall()
    return [str(r["nation_id"]) for r in rows if str(r["nation_id"]) not in exclude]


def _lane(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str,
    lanes: dict,
) -> bool:
    key = (seller, buyer)
    if key not in lanes:
        lanes[key] = route_is_open(conn, world_id, seller, buyer)
    return lanes[key][0]


@dataclass(frozen=True, slots=True)
class _Match:
    seller: str
    buyer: str
    volume: float
    terms: pricing.Terms


def _best_match(  # noqa: PLR0913 - one walk, and it needs every one of these
    conn: sqlite3.Connection, world_id: str, *, intent: Intent, seeking: bool,
    lanes: dict, alternatives_cache: dict, distress_cache: dict,
    embargoes: dict[tuple[str, str], dict[str, str]] | None = None,
) -> tuple[_Match | None, str]:
    """The counterparty this intention should actually be matched with, and why not if none.

    ``seeking`` says which end of the lane the intending power is on: a ``seek`` or
    ``diversify`` makes it the buyer, a ``sell`` makes it the seller.

    **The cheapest lane that both sides will accept**, not the largest cargo -- a government
    seeking a supply is shopping, and the engine settling the volume does not mean it should
    also decide that more is better. Ties break on the counterparty id so the answer does
    not depend on the order the resource table came back in.
    """
    me = intent.nation_id
    exclude = {me}
    if intent.kind == "diversify" and intent.partner_id:
        # The whole point: the power named is the one to get *away* from.
        exclude.add(intent.partner_id)
    others = (
        [intent.partner_id] if intent.partner_id and intent.kind != "diversify"
        else _candidates(conn, world_id, good=intent.good, exclude=exclude)
    )
    if not others:
        return None, f"nobody else on the map produces {intent.good}"

    looked = 0
    refusals: list[str] = []
    best: _Match | None = None
    for other in others:
        if other in exclude:
            continue
        seller, buyer = (other, me) if seeking else (me, other)
        if _already_supplying(conn, world_id, seller, buyer, intent.good):
            continue
        volume = sellable(conn, world_id, seller, buyer, intent.good)
        if volume <= 0:
            continue
        looked += 1
        if looked > CANDIDATE_CAP:
            break
        if not _lane(conn, world_id, seller, buyer, lanes):
            refusals.append(f"nothing can reach {_name(conn, other)}")
            continue
        # **A standing embargo is a closed door, and this walk could not see it.**
        # `assess_trade` has checked `embargo.stopped_by` on every shipping route since the
        # mechanic was added, so a lane under embargo moves nothing -- but this function
        # priced the same lane, found the terms agreeable, and opened a route on it. The
        # result was a government under embargo being matched with **the very power
        # embargoing it**: the route opened, shipped nothing, and the embargo then billed
        # its proposer for the standing price of a cargo that had never moved, inflating one
        # reported sacrifice by 6.4x.
        #
        # Checked here rather than at the top of the loop and after the lane, in the order
        # the shipping round checks them, so the *reason* a counterparty was passed over is
        # the most specific true one: a power nothing can reach is unreachable first and
        # embargoed second.
        if embargoes:
            stopper = embargo.stopped_by(embargoes, seller, buyer, intent.good)
            if stopper:
                refusals.append(
                    f"{_name(conn, other)} is under a standing embargo on this cargo"
                    if not seeking else
                    f"{_name(conn, other)} is embargoing {intent.good} to you")
                continue
        terms = pricing.price_route(
            conn, world_id, seller, buyer, intent.good,
            lanes=lanes, alternatives_cache=alternatives_cache,
        )
        if terms.embargoed:
            refusals.append(f"{_name(conn, other)} is priced out of reach")
            continue
        state = consent.band(
            conn, world_id, seller, buyer, intent.good, terms.unit_price,
            lanes=lanes, alternatives_cache=alternatives_cache,
            distress_cache=distress_cache,
        )
        if not state.overlaps:
            refusals.append(
                f"{_name(conn, other)} would not agree terms: "
                f"{consent.explain(state, intent.good)}")
            continue
        if best is None or terms.unit_price < best.terms.unit_price:
            best = _Match(seller=seller, buyer=buyer, volume=volume, terms=terms)
    if best is not None:
        return best, ""
    if refusals:
        return None, refusals[0]
    return None, (f"no reachable power has {intent.good} to spare that this one is short of"
                  if seeking else f"nobody in reach is short of {intent.good}")


def pursue(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> IntentReport:
    """Go and act on every standing intention, once per turn.

    Idempotent and derived, in the shape :func:`atlas.engine.treaties.honour_terms` uses and
    for the same reason: there is no single moment an intention becomes actionable, so this
    asks every turn whether the world now allows one and writes the difference. An intention
    the world refuses today is simply asked again next turn, with the note updated to say
    what stopped it -- which is also the sentence the briefing prints.

    **Nothing happens with :mod:`atlas.engine.pricing` down.** An intention in a barter
    world would open a route that moves goods for nothing, and that is a larger change than
    an intention is entitled to make.
    """
    report = IntentReport()
    if not pricing.enabled() or not pricing_columns_present(conn) or not _table_present(conn):
        return report
    intents = standing(conn, world_id)
    if not intents:
        return report

    lanes: dict = {}
    alternatives_cache: dict = {}
    distress_cache: dict = {}
    #: Every standing export embargo, fetched once for the whole walk -- the same lookup
    #: :func:`atlas.engine.trade.assess_trade` builds per round, and for the same reason.
    #: Empty and free whenever :mod:`atlas.engine.embargo` is down, which is the default.
    embargoes = embargo.bites(conn, world_id)
    diffs: list[Diff] = []
    told: list[str] = []

    for intent in intents:
        seeking = intent.kind in {"seek", "diversify"}
        if is_unreachable(conn, world_id, intent.nation_id):
            # A country nothing can reach cannot arrange anything, and saying it failed to
            # would be blaming a government for a hole in the map geometry. See
            # `atlas.engine.trade.is_unreachable`.
            continue
        match, why = _best_match(
            conn, world_id, intent=intent, seeking=seeking, lanes=lanes,
            alternatives_cache=alternatives_cache, distress_cache=distress_cache,
            embargoes=embargoes,
        )
        if match is None:
            if why != intent.note:
                diffs.append(Diff("trade_intent", intent.id, "note", DiffOp.SET, why[:200]))
            report.unmet.append(f"{_name(conn, intent.nation_id)}: {why}")
            continue

        route_id = f"tr_{intent.id}"
        diffs.append(Diff(
            "trade_route", route_id, "__insert__", DiffOp.INSERT,
            {
                "world_id": world_id, "seller_id": match.seller, "buyer_id": match.buyer,
                "good": intent.good, "volume": match.volume, "opened_turn": turn,
                "unit_price": match.terms.unit_price, "priced_turn": turn,
            },
        ))
        diffs.append(Diff("trade_intent", intent.id, "status", DiffOp.SET, "met"))
        diffs.append(Diff(
            "trade_intent", intent.id, "note", DiffOp.SET,
            f"{_name(conn, match.seller)} ships to {_name(conn, match.buyer)} at "
            f"{match.terms.unit_price:.2f}"[:200]))
        line = (
            f"{_name(conn, match.buyer)} arranges {intent.good} from "
            f"{_name(conn, match.seller)} at {match.terms.unit_price:.2f} a unit, "
            f"{match.volume:g} a month"
        )
        told.append(line)
        report.opened.append(line)

    if not diffs:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        text=("; ".join(told)[:600] if told
              else "Trade delegations come back with nothing to report."),
        visibility=Visibility.PUBLIC,
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
        report.problems.append(f"trade intentions could not be applied: {outcome.reason}")
        report.opened.clear()
    return report
