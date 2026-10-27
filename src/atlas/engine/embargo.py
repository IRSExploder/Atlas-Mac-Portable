"""An export embargo: a government stopping its own trade, on purpose, at its own cost.

**This is not the commercial withdrawal that already exists, and the distinction is the
whole reason the module exists.** :mod:`atlas.engine.refusal` and :mod:`atlas.engine.consent`
between them let either side of a lane stop taking part when the terms stop working -- a
seller pinned at the floor walks away, a buyer priced past its ceiling stops paying. Both
are *engine-derived*: nobody decided anything, the numbers stopped overlapping, and they
start again on their own the turn the numbers overlap once more. The sentence a player reads
is "this deal no longer pays".

An export embargo is the other sentence: **"I accept losing this revenue in order to hurt
or coerce you."** It is a choice, it is aimed, it costs the person making it, and it stands
until they take it back. Nothing in the engine could express that. :mod:`atlas.engine.refusal`
says so in its own docstring -- *"a model that could refuse a sale could embargo anybody by
saying so"* -- and closes the door, correctly, because a free embargo is not a decision. This
opens a governed one: the model names a target and at most a good, and the engine checks that
the trade being stopped is real, computes what stopping it costs the proposer, charges the
diplomatic price, and holds the government to it for a minimum term.

**The propose/decide split, for the fifth time in this layer and in the same shape.** A model
proposes that two powers trade (:mod:`atlas.engine.intent`); :func:`atlas.engine.trade.worth_selling`
decides what and how much; :mod:`atlas.engine.pricing` decides the price;
:mod:`atlas.engine.consent` decides whether either side will wear it. This adds: a government
names *who it will stop selling to*, and the engine decides what that stops, what it costs,
and what it does to the relationship. **Every magnitude here is the engine's.** A model
cannot name the revenue it is giving up, cannot name the damage it does, and cannot embargo
a power it is not actually supplying.

**Four things happen when one is imposed, and each of them is the point:**

*The exports stop.* Every route from the proposer to the target carrying the named good --
or every good, when none is named -- moves no cargo while the embargo stands. Unilateral:
the target is not consulted and cannot decline it.

*The proposer loses the revenue.* Not a penalty the engine invents: it is the money those
routes were actually earning, computed from the volume they would have moved and the price
:mod:`atlas.engine.pricing` would have charged, accumulated into
``trade_embargo.revenue_forgone`` every round the embargo holds. **This is the sacrifice, and
without it there is no decision** -- a lever that costs nothing is one every government pulls
against everybody.

*The relationship pays.* The target's regard for the proposer falls by :data:`REGARD_COST`.
And where the stopped exports were **guaranteed by an agreement**, the embargo is that
agreement being torn up: :func:`atlas.engine.treaties.breach` is called, with everything it
carries -- the broken commitment, the legitimacy charge, the far larger regard charge, and a
casus belli the injured party can point at. See :func:`_treaty_behind`.

*And the target can answer.* It is told, in its own briefing, that it has been embargoed,
what it has lost, and that it may counter-embargo -- which is the same channel the proposer
used, pointed back. Retaliation is a decision the target makes, not a reflex the engine
performs on its behalf.

**Reversible, and not free to reverse.** :func:`lift` exists, because an embargo nobody can
end is a punishment rather than a lever. But it may not be lifted inside
:data:`MINIMUM_MONTHS` of being imposed, and may not be re-imposed inside
:data:`COOLDOWN_MONTHS` of being lifted. Both are on the world's clock, for the reason every
duration in this engine is: how long a coercion has to be meant for cannot depend on how
often somebody looks at the game. Without them an embargo is a switch a staff flips on the
turn it wants leverage and off the turn it wants the money, which is neither a sacrifice nor
a threat anybody would believe.

**Legible before it is enacted, which is what makes it a decision rather than a gamble.**
:func:`prospect` answers, for a pair the proposer is actually supplying, the two numbers the
choice turns on: *what you would stop earning*, and *how much of their consumption you
cover*. Both are printed in the briefing beside the route, so a government chooses knowing
what it is spending and what it is buying with it. A mechanic whose cost is only discovered
after the fact is a trap.

**Behind its own flag, default-off, and it requires pricing.** ``ATLAS_TRADE_EMBARGO=on``
turns it on; :func:`enabled` is ``False`` without :func:`atlas.engine.pricing.enabled`
whatever that flag says, because in a barter world stopping a cargo costs the seller
*nothing* -- the sacrifice that makes this a decision does not exist, and what is left is a
free weapon. With the flag down nothing here runs, no property is offered to any model, and
a world plays exactly as it did.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import pricing
from .pacing import after_months
from .trade import GOODS, demand_of, pricing_columns_present, route_is_open

__all__ = [
    "COOLDOWN_MONTHS",
    "MAX_PER_TURN",
    "MAX_STANDING",
    "MINIMUM_MONTHS",
    "REGARD_COST",
    "Embargo",
    "Prospect",
    "against",
    "bites",
    "briefing_lines",
    "describe",
    "enabled",
    "forgone_diffs",
    "impose",
    "lift",
    "prospect",
    "set_enabled",
    "standing",
    "stopped_by",
    "table_present",
]


def _flag_from_env() -> bool:
    """``ATLAS_TRADE_EMBARGO=on`` turns the mechanic on before anything imports it.

    **Default off: this is staged, not shipped**, exactly as
    :func:`atlas.engine.pricing._flag_from_env` is and for a sharper version of the same
    reason. An embargo is the first mechanic in this layer that lets one government
    unilaterally stop another's supply, and what that does to a long game is a question with
    a measured answer rather than a reasoned one -- `scratchpad/TRADE_AGENCY.md`. Until that
    answer is read and a decision taken on it, the honest default is the one that changes
    nothing.

    Read once, at import, and never again: a coefficient that could change between two turns
    of the same game breaks replay, and the environment is exactly that kind of thing. Tests
    move it through :func:`set_enabled`.
    """
    raw = os.environ.get("ATLAS_TRADE_EMBARGO", "off").strip().lower()
    return raw in {"1", "on", "true", "yes"}


_ENABLED: bool = _flag_from_env()


def enabled() -> bool:
    """Whether a government may embargo anybody in this process.

    **Conjoined with pricing rather than merely documented as needing it.** An embargo in a
    barter world stops a cargo and costs its proposer nothing at all -- the revenue it is
    supposed to be sacrificing does not exist, because no route in such a world has ever
    been paid for. What would be left is a free weapon for stopping somebody else's supply,
    which is precisely the thing :mod:`atlas.engine.refusal` closed the door on. So the
    dependency is enforced here, in the one function every caller reads, rather than left as
    a note somebody has to remember.
    """
    return _ENABLED and pricing.enabled()


def set_enabled(value: bool) -> bool:
    """Move the flag, returning what it was. A function rather than a bare global so every
    reader goes through one door and a test can restore what it found."""
    global _ENABLED
    was, _ENABLED = _ENABLED, bool(value)
    return was


#: How long an embargo must stand before its proposer may lift it, in months of world time.
#:
#: **The commitment is what makes it a threat.** Without a floor, an embargo is a switch a
#: staff flips on in the turn it wants leverage and off in the turn it wants the revenue
#: back -- it costs a month's earnings at most, the target's dependency never actually
#: bites, and no government on the other end would change a policy over it. Six months is
#: long enough that the proposer's own industry feels the lost outlet and the target's
#: warehouse runs down, which are the two things the mechanic is *for*.
#:
#: World clock, like every other duration in the engine: how long a coercion has to be meant
#: for is a fact about the two governments, not about how often somebody looks at the game.
#: Written as a turn count it would be six months at monthly pacing and six *years* at
#: yearly -- the defect `tests/test_which_clock_each_duration_is_on.py` exists to catch.
MINIMUM_MONTHS = 6.0

#: And how long after lifting one before the same pair may be embargoed again.
#:
#: The other half of the same argument, from the other side. A government that could lift an
#: embargo and re-impose it the following turn would have a free way to reset the minimum
#: term -- and the target would face a lane that is on in the months it costs the proposer
#: nothing and off in the months it does. Twelve months rather than six because re-imposing
#: is the cheaper act: the relationship has already taken the hit and the routes are already
#: known to be stoppable, so the deterrent has to be in the wait.
COOLDOWN_MONTHS = 12.0

#: How many embargoes one power may have standing at once.
#:
#: A cap rather than a cost, as :data:`atlas.engine.intent.MAX_STANDING` is, and for a
#: related reason: the *revenue* is the real cost and it is charged per route, so a power
#: with one small lane to a dozen countries could embargo all twelve for very little. Three
#: is a foreign policy; twelve is a country that has withdrawn from the world, which is a
#: different thing and one the engine does not model.
MAX_STANDING = 3

#: How many embargo decisions one government may take in one turn. Two, so a power can lift
#: one and impose another in the same breath -- which is a redirection of policy and a
#: sentence a foreign ministry would actually say -- and no more.
MAX_PER_TURN = 2

#: What being embargoed costs the target's regard for the proposer.
#:
#: **Half of what tearing up a signature costs** (:data:`atlas.engine.treaties._REGARD_COST`
#: is -35), and the ratio is the design. Stopping trade you never promised to continue is a
#: hostile act and is meant to read as one; it is not a betrayal, because nothing was
#: pledged. Where the trade *was* pledged, this is not what is charged: the agreement is
#: breached, and the full 35 lands with the legitimacy charge and the casus belli behind it.
#: See :func:`_treaty_behind`.
#:
#: Comfortably inside the 25-point swing cap ``DIP-002`` imposes on model-proposed batches,
#: though that rule exempts engine batches and this is one -- the margin is kept so the
#: charge is legible next to every other relation move in the engine rather than an outlier
#: that only survives on an exemption.
REGARD_COST = -18.0

#: How far into the future a lifted embargo's cooldown is read from, when the world has no
#: date to measure against. See :func:`_too_soon`: worlds written before schema 48 have no
#: rows at all, so this is a belt-and-braces path rather than a live one, and it keeps the
#: turn arithmetic that a dateless row would otherwise have to invent.
_MONTHS_PER_TURN_FALLBACK = 1.0


@dataclass(frozen=True, slots=True)
class Embargo:
    """One embargo, as the table holds it."""

    id: str
    world_id: str
    proposer_id: str
    target_id: str
    #: The good it covers, or ``""`` for every good this seller ships to that buyer.
    good: str = ""
    opened_turn: int = 0
    lifted_turn: int = -1
    opened_on: str = ""
    lifted_on: str = ""
    status: str = "standing"
    revenue_forgone: float = 0.0
    episodes: int = 1
    note: str = ""

    @property
    def is_standing(self) -> bool:
        return self.status == "standing"

    def as_line(self, name_of) -> str:
        """The embargo in the government's own voice. ``name_of`` maps an id to a name so
        this does not need a connection to render."""
        what = self.good if self.good else "all trade"
        head = (f"{name_of(self.proposer_id)} has stopped selling {what} to "
                f"{name_of(self.target_id)}")
        if not self.is_standing:
            return head + " -- lifted"
        if self.revenue_forgone > 0:
            return head + f" -- {self.revenue_forgone:.1f} in lost revenue so far"
        return head


@dataclass(frozen=True, slots=True)
class Prospect:
    """What an embargo would cost and what it would do, before anybody imposes one.

    **Computed for a pair, from the routes that actually exist**, so every figure here is a
    fact about this world rather than an estimate. Both halves are reported because the
    decision is a trade between them: the first is what it costs you, the second is what it
    buys you.
    """

    proposer_id: str
    target_id: str
    good: str = ""
    #: Routes the embargo would stop.
    routes: int = 0
    #: How many of those routes have **already** stopped on their own terms -- the buyer's
    #: ceiling is below the standing price, or the seller has withdrawn. Cargo is not moving
    #: on them and nobody is being paid, so embargoing them makes permanent and deliberate
    #: something the market had already done, and costs nothing today.
    already_stopped: int = 0
    #: **Why** they had stopped, when it was not the terms: ``"at war"``, ``"sanctioned"``,
    #: or the blockade reason `route_is_open` gives. Empty when the lanes are open and it
    #: was the buyer's ceiling that closed them, which is the ordinary case and the one the
    #: word "price" describes. The offer sentence reads this rather than asserting price,
    #: because telling a government its cargo stopped on price when the two of them are at
    #: war is a false explanation of a true number.
    stopped_reason: str = ""
    #: What those routes earn the proposer, per month, at the standing terms -- **counting
    #: only the ones actually shipping**. See :attr:`already_stopped`: quoting the standing
    #: price of a lane that has stopped tells a government it is about to spend money it is
    #: not currently receiving, which is the opposite of legible.
    revenue_at_risk: float = 0.0
    #: What they cover of the target's monthly consumption of the goods involved, 0..1.
    #: ``0.0`` when the target consumes nothing measurable.
    covers: float = 0.0
    #: How many other powers could reach the target with the good, at the widest reading.
    #: The number that decides whether an embargo bites or is simply bought around.
    alternatives: int = 0

    @property
    def possible(self) -> bool:
        """Whether there is anything here to stop. An embargo of a trade that is not
        happening is not a strategic act, it is a press release."""
        return self.routes > 0

    def as_line(self, target_name: str) -> str:
        """The offer, in the sentence a briefing prints. See :func:`briefing_lines`."""
        what = self.good if self.good else "everything you sell them"
        bite = (
            f"it covers {self.covers * 100:.0f}% of what they consume"
            if self.covers > 0 else "they consume little of it"
        )
        reach = (
            "and nobody else in reach can supply them"
            if self.alternatives == 0
            else f"and {self.alternatives} other "
                 f"{'supplier' if self.alternatives == 1 else 'suppliers'} could replace you"
        )
        # **What it costs is the marginal cost, and where that is nothing the sentence says
        # so rather than quoting a price nobody is paying.** A lane the buyer has already
        # stopped taking earns this government nothing today; telling it that stopping the
        # lane would cost it the standing price is telling it to spend money it is not
        # receiving. The offer is still worth making -- an embargo makes the stoppage
        # deliberate, holds it for a term, and charges the relationship for it -- but on
        # honest terms.
        # **And it says which of the two stopped them.** `stopped_reason` is set when the
        # world closed the lane rather than the terms; saying "on price" there would be a
        # false explanation of a true number, and the difference matters to the government
        # reading it -- a lane stopped on price reopens if the price moves, and a lane
        # stopped by a war does not.
        # Phrased so the ordinary case is **byte-identical to what it has always said**:
        # a lane closed by the buyer's ceiling still reads "already stopped on price", and
        # only a lane the world closed reads differently. That keeps this correction off
        # every recorded prompt except the ones it is actually about.
        why = f"-- {self.stopped_reason}" if self.stopped_reason else "on price"
        if self.already_stopped and self.revenue_at_risk <= 0:
            # **Every lane is cut, and the offer says so before the regard is spent.** The
            # mechanic stays available -- an embargo of a stopped lane holds the stoppage
            # for a term and is a declared act -- but a government reaching for it should
            # be told it is buying a declaration rather than a stoppage, and what the
            # declaration costs it. The price is in the relationship, and that half is not
            # free even where the cargo is.
            every = ("all " if self.routes > 1 else "") + (
                f"{self.routes} of your lanes to them are" if self.routes > 1
                else "your one lane to them is")
            return (
                f"embargo {what} to {target_name}: {every} already stopped {why}, so this "
                f"would stop nothing that is still moving and costs you no revenue -- it "
                f"would hold them stopped, and be a declared act rather than a market "
                f"accident. It still costs {abs(REGARD_COST):.0f} of their regard for you. "
                f"{bite.capitalize()}, {reach}"
            )
        held = (f" ({self.already_stopped} of {self.routes} have already stopped {why})"
                if self.already_stopped else "")
        return (
            f"embargo {what} to {target_name}: it would cost you "
            f"{self.revenue_at_risk:.2f} a month in lost revenue{held}, {bite}, {reach}"
        )


def table_present(conn: sqlite3.Connection) -> bool:
    """Whether this world's schema carries the embargo table at all.

    The pre-migration guarantee, asked of the database rather than assumed -- the same check
    :func:`atlas.engine.trade.pricing_columns_present` and
    :func:`atlas.engine.intent._table_present` make, for the same reason. A world written
    before schema 48 has nowhere to put an embargo and must not be crashed by one.
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trade_embargo'"
    ).fetchone() is not None


def _live(conn: sqlite3.Connection) -> bool:
    """Flag up, pricing up, schema new enough. The three-part guard every entry point makes
    before it touches anything."""
    return enabled() and table_present(conn) and pricing_columns_present(conn)


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row is not None else nation_id


def _namer(conn: sqlite3.Connection):
    """``id -> name``, cached. A briefing names the same handful of powers over and over."""
    seen: dict[str, str] = {}

    def name_of(nation_id: str) -> str:
        if not nation_id:
            return "nobody"
        if nation_id not in seen:
            seen[nation_id] = _name(conn, nation_id)
        return seen[nation_id]

    return name_of


def _addressed(conn: sqlite3.Connection, name_of, nation_id: str) -> str:
    """``Name (`id`)``, which is how every other briefing block names a power.

    **Not cosmetic.** The answer channel wants a nation *id* -- ``trade_embargoes`` names a
    target by id, as ``trade_intents`` names a partner by id -- and an affordance that
    offered "you could embargo Saudi Arabia" while the schema demanded ``sau`` would be a
    lever a model can read and cannot pull. The rest of the view has kept this convention
    since it was written; this block joins it.
    """
    return f"{name_of(nation_id)} (`{nation_id}`)"


def describe(conn: sqlite3.Connection, item: Embargo) -> str:
    """One embargo in the government's own voice, with the powers named."""
    return item.as_line(lambda nation_id: _name(conn, nation_id))


def _embargo_id(proposer_id: str, target_id: str, good: str) -> str:
    """Derived from what the embargo *is*, not from a counter.

    The same argument :func:`atlas.engine.intent._intent_id` makes. Two embargoes of the same
    good against the same power are one embargo, so a lift and a re-imposition reuse the row
    rather than growing the table -- and a rewind that removes an imposition and a replay
    that re-imposes it produce the same id and the same digest. A counter would make the
    fingerprint depend on how many embargoes had ever been declared anywhere in the world,
    which is exactly the hidden ordering a replay is supposed to be free of.
    """
    return f"te_{proposer_id}_{target_id}_{good or 'all'}"


def _row_to_embargo(row: sqlite3.Row) -> Embargo:
    return Embargo(
        id=str(row["id"]), world_id=str(row["world_id"]),
        proposer_id=str(row["proposer_id"]), target_id=str(row["target_id"]),
        good=str(row["good"]), opened_turn=int(row["opened_turn"]),
        lifted_turn=int(row["lifted_turn"]), opened_on=str(row["opened_on"]),
        lifted_on=str(row["lifted_on"]), status=str(row["status"]),
        revenue_forgone=float(row["revenue_forgone"] or 0.0),
        episodes=int(row["episodes"]), note=str(row["note"]),
    )


def standing(
    conn: sqlite3.Connection, world_id: str, proposer_id: str = "",
    *, include_lifted: bool = False,
) -> list[Embargo]:
    """Every embargo this power has imposed, oldest first, ordered by id after that.

    Reads the table whatever the flag says -- an embargo written while the mechanic was on
    is still state, and a briefing that stopped mentioning it because somebody moved a flag
    would be lying about the world. What the flag gates is *imposing* and *biting*.
    """
    if not table_present(conn):
        return []
    wanted = ("standing", "lifted") if include_lifted else ("standing",)
    rows = conn.execute(
        "SELECT * FROM trade_embargo"
        f"  WHERE world_id = ? AND status IN ({', '.join('?' for _ in wanted)})"
        "    AND (? = '' OR proposer_id = ?)"
        "  ORDER BY opened_turn, id",
        (world_id, *wanted, proposer_id, proposer_id),
    ).fetchall()
    return [_row_to_embargo(r) for r in rows]


def against(conn: sqlite3.Connection, world_id: str, target_id: str) -> list[Embargo]:
    """Every standing embargo aimed at this power. The other end of :func:`standing`, and it
    exists because the target is the government that most needs to be told."""
    if not table_present(conn):
        return []
    rows = conn.execute(
        "SELECT * FROM trade_embargo"
        "  WHERE world_id = ? AND target_id = ? AND status = 'standing'"
        "  ORDER BY opened_turn, id",
        (world_id, target_id),
    ).fetchall()
    return [_row_to_embargo(r) for r in rows]


def bites(conn: sqlite3.Connection, world_id: str) -> dict[tuple[str, str], dict[str, str]]:
    """Every standing embargo, as the lookup a shipping round wants.

    ``(proposer, target) -> {good_or_empty: embargo_id}``, where the empty key is an embargo
    on everything. Built **once per round** rather than queried per route, for the reason the
    lane cache in :func:`atlas.engine.trade.assess_trade` exists: a world can carry hundreds
    of routes and a per-route query on a table with a handful of rows is the same answer
    fetched hundreds of times.

    Empty -- and cheap -- whenever the mechanic is down, which is the default, so the
    shipping loop pays a single dictionary lookup for a feature nobody has switched on.
    """
    if not _live(conn):
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    for row in conn.execute(
        "SELECT id, proposer_id, target_id, good FROM trade_embargo"
        "  WHERE world_id = ? AND status = 'standing' ORDER BY id",
        (world_id,),
    ):
        pair = (str(row["proposer_id"]), str(row["target_id"]))
        out.setdefault(pair, {})[str(row["good"])] = str(row["id"])
    return out


def stopped_by(
    lookup: dict[tuple[str, str], dict[str, str]], seller: str, buyer: str, good: str
) -> str:
    """The id of the embargo stopping this cargo, or ``""``.

    **The all-goods embargo wins over the specific one**, and is checked first, because a
    power that has stopped everything has by definition stopped this. Nothing prevents both
    rows existing -- an oil embargo widened to everything a year later leaves the first one
    standing -- and the revenue has to be attributed to exactly one of them or it is counted
    twice.
    """
    covered = lookup.get((seller, buyer))
    if not covered:
        return ""
    return covered.get("") or covered.get(good, "")


def _routes_between(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str = ""
) -> list[sqlite3.Row]:
    """The priced routes an embargo of this pair and good would stop."""
    sql = (
        "SELECT id, good, volume, unit_price, priced_turn FROM trade_route"
        "  WHERE world_id = ? AND seller_id = ? AND buyer_id = ?"
    )
    params: list[object] = [world_id, seller, buyer]
    if good:
        sql += " AND good = ?"
        params.append(good)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def prospect(
    conn: sqlite3.Connection, world_id: str, proposer_id: str, target_id: str,
    good: str = "", *, lanes: dict | None = None, alternatives_cache: dict | None = None,
) -> Prospect:
    """What an embargo of this pair would cost, and what it would do. See :class:`Prospect`.

    **Priced at the standing terms, not at a fresh price.** What the proposer is giving up is
    what these routes are earning it *now*, which is the number on the invoice and the number
    a cabinet would be shown. A freshly derived price would answer a different question --
    what they would earn if the file were reopened today -- and would move under a government
    that had not changed anything.

    An unpriced route is priced once here, because a route the engine has not looked at yet
    still earns nothing and reporting zero would understate the sacrifice to nothing at all.
    """
    routes = _routes_between(conn, world_id, proposer_id, target_id, good)
    if not routes:
        return Prospect(proposer_id=proposer_id, target_id=target_id, good=good)

    # The band decides whether each lane is actually moving. **Imported here rather than at
    # module scope**: `consent` reads this module, and a cycle helps nobody -- the same
    # local-import shape `consent.briefing_lines` uses for `intent`.
    from . import consent

    # **A lane the world has already closed cannot be given up.** `consent.band` below
    # answers only whether the *terms* overlap; it does not know that these two are at war,
    # that a resolution has sanctioned the pair, or that no sea lane connects them any more.
    # `assess_trade` walks `route_is_open` and the band together and only the pair of them
    # says whether a cargo moved, so a prospect that consults one of the two overstates the
    # sacrifice on exactly the lanes a government is most likely to be embargoing -- the
    # ones belonging to somebody it is already fighting. Asked once for the pair, because
    # that is the granularity `route_is_open` answers at.
    open_pair, closed_because = route_is_open(conn, world_id, proposer_id, target_id)

    revenue = 0.0
    already_stopped = 0
    goods: set[str] = set()
    volume_by_good: dict[str, float] = {}
    distress_cache: dict = {}
    for row in routes:
        row_good = str(row["good"])
        goods.add(row_good)
        volume = float(row["volume"] or 0.0)
        volume_by_good[row_good] = volume_by_good.get(row_good, 0.0) + volume
        price = float(row["unit_price"] or 0.0)
        if int(row["priced_turn"]) < 0 or price <= 0:
            price = pricing.price_route(
                conn, world_id, proposer_id, target_id, row_good,
                lanes=lanes, alternatives_cache=alternatives_cache,
            ).unit_price
        # **Only a lane that is moving can be given up.** See :attr:`Prospect.already_stopped`
        # and the note in `atlas.engine.trade.assess_trade`: the regime in which a government
        # wants an embargo is very nearly the regime in which the buyer's ceiling has already
        # closed the lane, and quoting the standing price there overstates the sacrifice to
        # the whole of it.
        if open_pair and consent.band(
            conn, world_id, proposer_id, target_id, row_good, price,
            lanes=lanes, alternatives_cache=alternatives_cache,
            distress_cache=distress_cache,
        ).overlaps:
            revenue += volume * price
        else:
            already_stopped += 1

    # What it covers of the buyer's consumption. `demand_of` is what a country's industry
    # burns of *each* good per month -- the same number `assess_trade` subtracts -- so the
    # share is per good, and the figure reported is the **worst-hit good** rather than an
    # average over them. An embargo that stops a country's entire oil supply and a little of
    # its steel has done the first of those things, and an average would report it as having
    # half-done both.
    demand = demand_of(conn, target_id)
    covers = 0.0
    if demand > 0 and volume_by_good:
        covers = min(1.0, max(volume_by_good.values()) / demand)

    # And who else could supply them. The widest reading over the goods involved: an embargo
    # is bought around by the good that has the most alternatives, so reporting the maximum
    # is the honest warning to the power considering one. **Argument order is the pricing
    # layer's**: `alternatives` asks who else can reach a *buyer*, so the target comes first.
    alternatives = 0
    for row_good in sorted(goods):
        alternatives = max(alternatives, pricing.alternatives(
            conn, world_id, target_id, proposer_id, row_good,
            lanes=lanes, alternatives_cache=alternatives_cache,
        ))

    return Prospect(
        proposer_id=proposer_id, target_id=target_id, good=good, routes=len(routes),
        already_stopped=already_stopped,
        stopped_reason="" if open_pair else closed_because,
        revenue_at_risk=round(revenue, 3), covers=round(covers, 4),
        alternatives=alternatives,
    )


def _existing(
    conn: sqlite3.Connection, world_id: str, embargo_id: str
) -> Embargo | None:
    row = conn.execute(
        "SELECT * FROM trade_embargo WHERE world_id = ? AND id = ?", (world_id, embargo_id)
    ).fetchone()
    return None if row is None else _row_to_embargo(row)


def _months_since(then_on: str, now_on: str, then_turn: int, now_turn: int) -> float:
    """How much world time has passed, by the date where there is one and by turns where
    there is not.

    **The date first, and the turn only as a fallback**, which is the pairing
    ``commitment.expires_on`` keeps and for the same reason: a turn count means one thing at
    monthly pacing and twelve times that at yearly, so a duration written in turns is a
    different duration in every game. Every row this module writes carries a date; the
    fallback is for a row that somehow does not, and it is deliberately conservative --
    one month per turn is the *shortest* a turn can reasonably be, so the fallback lets an
    embargo out no sooner than the date would.
    """
    if then_on and now_on:
        # A month is what the calendar says it is: `after_months` walks the calendar, so
        # comparing ISO dates as strings answers "has that much time passed" exactly.
        return 0.0 if now_on < then_on else _elapsed_months(then_on, now_on)
    return max(0.0, float(now_turn - then_turn) * _MONTHS_PER_TURN_FALLBACK)


def _elapsed_months(earlier: str, later: str) -> float:
    """Whole months of world time between two ISO dates, walking the calendar.

    Counted by asking :func:`atlas.engine.pacing.after_months` rather than dividing days,
    because the durations here are written in months and a division by thirty makes February
    shorter than a month and July longer. Bounded by the two constants above, so this walks
    at most a dozen steps.
    """
    months = 0.0
    limit = max(MINIMUM_MONTHS, COOLDOWN_MONTHS) + 1.0
    while months < limit:
        reached = after_months(earlier, months + 1.0)
        if not reached or reached > later:
            break
        months += 1.0
    return months


def _too_soon(item: Embargo, *, turn: int, date: str) -> str:
    """Why this pair may not be embargoed yet, or ``""``.

    Reads the *previous* episode of the same embargo. A row that has never been lifted has
    nothing to wait for; one lifted recently is inside :data:`COOLDOWN_MONTHS`.
    """
    if item.is_standing:
        return "this power is already under embargo from you; lift it before imposing another"
    waited = _months_since(item.lifted_on, date, item.lifted_turn, turn)
    if waited >= COOLDOWN_MONTHS:
        return ""
    left = COOLDOWN_MONTHS - waited
    return (
        f"you lifted this embargo {waited:.0f} month(s) ago; a lane cannot be closed, "
        f"opened and closed again at will -- {left:.0f} more month(s) before you may "
        "impose it again"
    )


def _treaty_behind(
    conn: sqlite3.Connection, world_id: str, proposer_id: str, target_id: str, good: str
) -> tuple[int, str]:
    """The active agreement whose supply this embargo would stop, and the good it obliges.

    ``(commitment_id, good)``, or ``(0, "")`` when nothing was promised. An ``open_trade``
    term names the power obliged to ship and the good -- see
    :func:`atlas.engine.treaties.term_diffs` -- so this asks the narrow question: **is the
    proposer under a standing obligation to supply this target with this good?**

    **The narrow question and not a wider one, deliberately.** Being at peace with somebody
    is not a promise to trade with them, and an alliance is not a supply contract. Only a
    term that actually says *this power ships this good* is torn up by refusing to ship it,
    and treating a non-aggression pact as breached by an embargo would hand out casus belli
    for an act that broke no promise anybody made.
    """
    try:
        rows = conn.execute(
            "SELECT t.commitment_id AS commitment_id, t.subject_id AS subject_id"
            "  FROM commitment_term t"
            "  JOIN commitment c ON c.id = t.commitment_id"
            "  JOIN commitment_nation p ON p.commitment_id = c.id"
            " WHERE t.world_id = ? AND t.clause = 'open_trade'"
            "   AND t.obliged_id = ? AND p.nation_id = ? AND c.status = 'active'"
            " ORDER BY t.commitment_id, t.seq",
            (world_id, proposer_id, target_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0, ""
    for row in rows:
        subject = str(row["subject_id"])
        if not good or subject == good:
            return int(row["commitment_id"]), subject
    return 0, ""


def impose(  # noqa: PLR0911, PLR0913 - a refusal per reason, and a proposal names all of this
    conn: sqlite3.Connection, world_id: str, *, proposer_id: str, target_id: str,
    good: str = "", turn: int, date: str,
) -> tuple[Embargo | None, str]:
    """Stop selling to a power, on purpose. ``(embargo, problem)``.

    **The one door onto the table**, which is why every column of ``trade_embargo`` is in
    :data:`atlas.state.permissions.ENGINE_ONLY`. Everything a model could abuse is checked
    here: that the good is real, that the target is real, that the two are not the same
    power, that the cooldown has run -- and, above all, **that the proposer is actually
    exporting to the target**. An embargo of a trade that is not happening costs nothing and
    stops nothing; letting one be declared would give every government a free way to insult
    anybody on the map, which is the opposite of the mechanic.

    Refusals name what to do instead, in the shape the rest of the engine keeps: a refusal
    that only says no is one the staff will make again next turn.
    """
    if not enabled():
        return None, "this world does not model export embargoes"
    if not table_present(conn) or not pricing_columns_present(conn):
        return None, ("this world was written before export embargoes existed; "
                      "there is nothing to record one in")
    good = str(good or "").strip().lower()
    target_id = str(target_id or "").strip()
    if good and good not in GOODS:
        return None, (f"{good!r} is not a traded good; name one of {', '.join(GOODS)}, "
                      "or leave it out to stop everything you sell them")
    if not target_id or target_id == proposer_id:
        return None, "an embargo needs somebody to be aimed at, and it cannot be yourself"
    known = conn.execute(
        "SELECT 1 FROM nation WHERE world_id = ? AND id = ? AND is_alive = 1",
        (world_id, target_id),
    ).fetchone()
    if known is None:
        return None, f"there is no power {target_id!r} to embargo"

    mine = standing(conn, world_id, proposer_id)
    embargo_id = _embargo_id(proposer_id, target_id, good)
    previous = _existing(conn, world_id, embargo_id)
    if previous is not None:
        problem = _too_soon(previous, turn=turn, date=date)
        if problem:
            return None, problem
    # A power that has stopped everything has stopped this too, and saying so is more use
    # than writing a second row that can never bite. Checked against the *live* rows rather
    # than the id, because the wider embargo has a different id by construction.
    if good and any(e.target_id == target_id and not e.good for e in mine):
        return None, (f"you have already stopped all trade with {_name(conn, target_id)}; "
                      f"{good} is covered by it")
    # **The cap counts what would be standing afterwards, and re-imposing an old row is not
    # an exemption from it.** `mine` holds the standing rows only, and by this line
    # `previous` is either absent or *lifted* -- a standing one was refused by
    # :func:`_too_soon` above. Either way this embargo would be the (n+1)th, so the same
    # limit applies. Excusing a re-imposition because its row already existed would have let
    # a government hold `MAX_STANDING` embargoes and then re-open every one it had ever
    # lifted on top of them.
    if len(mine) >= MAX_STANDING:
        return None, (f"a government may hold {MAX_STANDING} export embargoes at once; "
                      "lift one before imposing another")

    # **The check the whole mechanic rests on.** Priced routes, from this proposer, to this
    # target, in this good. Nothing else counts as exporting.
    ahead = prospect(conn, world_id, proposer_id, target_id, good)
    if not ahead.possible:
        what = f"{good} to" if good else "anything to"
        return None, (
            f"you do not sell {what} {_name(conn, target_id)}, so there is nothing to "
            "embargo; an embargo is a supply you are choosing to withhold, not a statement "
            "about a power you have no trade with"
        )

    # And what it breaks, if it breaks anything. Read before the row is written so the
    # event can say which it is, and charged below through the treaty layer's own door.
    commitment_id, _obliged_good = _treaty_behind(
        conn, world_id, proposer_id, target_id, good)

    name_of = _namer(conn)
    what = good if good else "all trade"
    told = (
        f"{name_of(proposer_id)} stops selling {what} to {name_of(target_id)}, giving up "
        f"{ahead.revenue_at_risk:.2f} a month to do it"
    )
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        actor_nation_id=proposer_id, text=told[:600], visibility=Visibility.PUBLIC,
        participants=(("actor", proposer_id), ("target", target_id)),
    )

    note = (
        f"{ahead.routes} route(s), {ahead.revenue_at_risk:.2f} a month forgone; covers "
        f"{ahead.covers * 100:.0f}% of their consumption, {ahead.alternatives} "
        "alternative supplier(s)"
    )
    if previous is None:
        columns = {
            "world_id": world_id, "proposer_id": proposer_id, "target_id": target_id,
            "good": good, "opened_turn": turn, "lifted_turn": -1, "opened_on": date,
            "lifted_on": "", "status": "standing", "revenue_forgone": 0.0,
            "episodes": 1, "note": note[:200],
        }
        diffs: list[Diff] = [
            Diff("trade_embargo", embargo_id, "__insert__", DiffOp.INSERT, columns)
        ]
        episodes = 1
    else:
        # **The row is reused rather than replaced**, so the cooldown has something to read
        # and `revenue_forgone` keeps accumulating across episodes -- what this policy has
        # cost the country is the number it should be judged on, and resetting it every time
        # a government blinked would hide exactly the thing worth seeing.
        episodes = previous.episodes + 1
        diffs = [
            Diff("trade_embargo", embargo_id, "status", DiffOp.SET, "standing"),
            Diff("trade_embargo", embargo_id, "opened_turn", DiffOp.SET, turn),
            Diff("trade_embargo", embargo_id, "opened_on", DiffOp.SET, date),
            Diff("trade_embargo", embargo_id, "lifted_turn", DiffOp.SET, -1),
            Diff("trade_embargo", embargo_id, "lifted_on", DiffOp.SET, ""),
            Diff("trade_embargo", embargo_id, "episodes", DiffOp.SET, episodes),
            Diff("trade_embargo", embargo_id, "note", DiffOp.SET, note[:200]),
        ]

    # **The relationship pays, and it pays in the target's direction only.** Regard is
    # directed: `relation` holds a row per ordered pair, and what has changed is what the
    # target thinks of the proposer. The proposer's own view of the target is not improved
    # or worsened by its own decision -- it already held whatever view led to it.
    #
    # **Not charged when a treaty is being broken**, because `treaties.breach` charges its
    # own, larger figure below and two charges for one act would be double-counting a
    # betrayal.
    if not commitment_id:
        diffs.append(
            Diff("relation", f"{target_id}|{proposer_id}", "score", DiffOp.ADJUST,
                 REGARD_COST)
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
        # The same all-or-nothing argument `treaties.breach` makes: a refused batch must not
        # leave the announcement of an embargo behind with none of the embargo.
        from ..state.apply import unrecord_events

        unrecord_events(conn, [event_id])
        conn.commit()
        return None, f"the embargo could not be recorded: {outcome.reason}"

    # **And the signature, if there was one.** Refusing to ship a good you signed an
    # agreement to ship *is* tearing the agreement up, so it goes through the treaty layer's
    # own door and carries everything a breach carries: the commitment marked broken, the
    # legitimacy charge, the full -35 regard, and a casus belli the injured party can point
    # at for five turns. That is the asymmetry the design wants -- embargoing a market
    # counterparty is hostile, and embargoing a treaty partner is a betrayal.
    #
    # After the batch above rather than inside it, because `breach` writes its own events
    # and its own batch and is idempotent by construction; folding the two together would
    # mean re-implementing it here.
    if commitment_id:
        from .treaties import breach

        breach(conn, world_id, commitment_id=commitment_id, violator=proposer_id,
               turn=turn, date=date,
               why="they embargoed the power they had agreed to supply")

    conn.commit()
    return Embargo(
        id=embargo_id, world_id=world_id, proposer_id=proposer_id, target_id=target_id,
        good=good, opened_turn=turn, opened_on=date, status="standing",
        revenue_forgone=previous.revenue_forgone if previous else 0.0,
        episodes=episodes, note=note[:200],
    ), ""


def lift(
    conn: sqlite3.Connection, world_id: str, *, proposer_id: str, target_id: str,
    good: str = "", turn: int, date: str,
) -> tuple[bool, str]:
    """Take an embargo back. ``(lifted, problem)``.

    **Reversible, because an embargo nobody can end is a punishment and not a lever** -- the
    whole point of a coercive measure is that the coerced party can be offered its removal.
    But not immediately: see :data:`MINIMUM_MONTHS`. A government that could lift one the
    turn after imposing it would never actually pay for it, and a threat nobody pays for is
    one nobody believes.

    The row stays, marked ``lifted``, exactly as :func:`atlas.engine.intent.retire` keeps a
    retired intention: the cooldown has to read when it ended, and a deleted row and a
    restored one are the problem the route table already solves by keeping every column.
    """
    if not table_present(conn):
        return False, "this world has no record of export embargoes"
    good = str(good or "").strip().lower()
    embargo_id = _embargo_id(proposer_id, target_id, good)
    item = _existing(conn, world_id, embargo_id)
    if item is None or not item.is_standing:
        what = good if good else "all trade"
        return False, (f"you have no standing embargo on {what} to "
                       f"{_name(conn, target_id)} to lift")
    held = _months_since(item.opened_on, date, item.opened_turn, turn)
    if held < MINIMUM_MONTHS:
        left = MINIMUM_MONTHS - held
        return False, (
            f"this embargo has stood {held:.0f} month(s); a measure taken back before it "
            f"has been felt was never a measure -- {left:.0f} more month(s) before you may "
            "lift it"
        )

    name_of = _namer(conn)
    what = good if good else "all trade"
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        actor_nation_id=proposer_id,
        text=(f"{name_of(proposer_id)} resumes selling {what} to {name_of(target_id)} "
              f"after {held:.0f} month(s), having given up "
              f"{item.revenue_forgone:.1f} to hold it")[:600],
        visibility=Visibility.PUBLIC,
        participants=(("actor", proposer_id), ("target", target_id)),
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=(
                      Diff("trade_embargo", embargo_id, "status", DiffOp.SET, "lifted",
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
                      Diff("trade_embargo", embargo_id, "lifted_turn", DiffOp.SET, turn,
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
                      Diff("trade_embargo", embargo_id, "lifted_on", DiffOp.SET, date,
                           reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
                  )),
    )
    if not outcome.applied:
        from ..state.apply import unrecord_events

        unrecord_events(conn, [event_id])
        conn.commit()
        return False, f"the embargo could not be lifted: {outcome.reason}"
    conn.commit()
    return True, ""


def forgone_diffs(charged: dict[str, float]) -> list[Diff]:
    """What an embargo cost its proposer this round, as diffs on its own row.

    ``ADJUST`` rather than ``SET``, for the reason the treasury settlement gives one function
    over: this accumulates over the life of the embargo and a writer that named the total it
    believed would discard whatever the previous round had added.

    **Returned rather than applied**, so :func:`atlas.engine.trade.assess_trade` can put them
    in the same batch as the cargo they replaced. A rewind that put the shipment back and
    left the sacrifice on the books would be a rewind to a world that never existed -- the
    same argument the money and the goods already make.
    """
    return [
        Diff("trade_embargo", embargo_id, "revenue_forgone", DiffOp.ADJUST,
             round(amount, 3))
        for embargo_id, amount in sorted(charged.items())
        if round(amount, 3) > 0
    ]


#: How many embargo offers one briefing prints, largest revenue first. The same cap
#: :data:`atlas.engine.consent.BRIEFING_ROUTES` keeps and for the same reason: a briefing is
#: already the largest object in the engine, and a great power selling into a dozen countries
#: would otherwise get a page of them.
BRIEFING_OFFERS = 3

#: And how many customers are *examined* to find those three.
#:
#: **A cap on the work rather than on the answer**, and it is the argument
#: :data:`atlas.engine.intent.CANDIDATE_CAP` makes about the same sea walk. :func:`prospect`
#: prices every route into a candidate and asks :func:`atlas.engine.pricing.alternatives` who
#: else could reach them, which is the expensive thing in this layer; a great power in a
#: spread world sells into a hundred countries, and the block prints three lines.
#:
#: Measured on the modern-day map before this cap existed: **one call for China took 1.06
#: seconds**, and building the block for every power in the world took 4.16 -- for a briefing
#: that is rebuilt for five powers every turn. The candidates are examined largest bill
#: first, which is the order the offers are ranked in anyway, so the cap drops the customers
#: that could not have reached the page.
PROSPECTS_EXAMINED = 8


def briefing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this government has stopped, what has been stopped against it, and what it could
    stop -- the whole affordance, in one block.

    **Three parts, and the third is the one that makes the mechanic exist.** A power is told
    what it is already doing, what is being done to it (with the answer named, because
    retaliation is a decision and a government that is not told it may retaliate does not),
    and -- for each customer worth the sentence -- *what an embargo would cost it and what it
    would buy*. A mechanic a model is never told about is a mechanic that is not shipped;
    `tests/test_a_mechanism_a_model_cannot_reach_is_not_shipped.py` is the sweep that
    argument comes from and this is the door it names.

    Empty when the mechanic is down, which is every world by default, so a briefing without
    it is the briefing it always was and no recorded cassette moves.
    """
    if not _live(conn):
        return []
    name_of = _namer(conn)
    mine = [e for e in standing(conn, world_id, nation_id)]
    on_me = against(conn, world_id, nation_id)

    # What could be stopped: the customers this power actually supplies, biggest bill first,
    # and only the ones not already embargoed.
    already = {(e.target_id, e.good) for e in mine}
    wide = {e.target_id for e in mine if not e.good}
    offers: list[tuple[float, str]] = []
    if len(mine) < MAX_STANDING:
        lanes: dict = {}
        alternatives_cache: dict = {}
        customers = [
            str(r["buyer_id"]) for r in conn.execute(
                "SELECT buyer_id, SUM(volume * unit_price) AS bill FROM trade_route"
                "  WHERE world_id = ? AND seller_id = ?"
                "  GROUP BY buyer_id ORDER BY bill DESC, buyer_id",
                (world_id, nation_id),
            )
        ]
        examined = 0
        for buyer in customers:
            if buyer in wide or (buyer, "") in already:
                continue
            if examined >= PROSPECTS_EXAMINED:
                break
            examined += 1
            ahead = prospect(conn, world_id, nation_id, buyer, "",
                             lanes=lanes, alternatives_cache=alternatives_cache)
            # **Offered even where it would cost nothing today.** An embargo of an
            # already-stopped lane is still a decision -- it holds the stoppage for a term,
            # it is declared, and it charges the relationship -- and dropping it from the
            # page because its price tag is zero would hide the mechanic exactly where a
            # hostile government is most likely to want it. What it must not do is claim a
            # cost, and `Prospect.as_line` no longer does.
            if not ahead.possible:
                continue
            offers.append((ahead.revenue_at_risk,
                           "- " + ahead.as_line(_addressed(conn, name_of, buyer))))
    offers.sort(key=lambda pair: (-pair[0], pair[1]))

    if not mine and not on_me and not offers:
        return []

    lines = ["## Trade you have stopped, and trade you could stop"]
    if mine:
        lines.append("")
        lines.append("**Your standing embargoes:**")
        for item in mine:
            held_note = (f" It has cost you {item.revenue_forgone:.1f} so far."
                         if item.revenue_forgone > 0 else "")
            lines.append(f"- {item.as_line(name_of)}.{held_note}")
        lines.append(
            f"  You may lift one once it has stood {MINIMUM_MONTHS:.0f} months, and may "
            f"not re-impose it for {COOLDOWN_MONTHS:.0f} months after that.")
    if on_me:
        lines.append("")
        lines.append("**Embargoes against you:**")
        for item in on_me:
            what = item.good if item.good else "all trade"
            lines.append(
                f"- {_addressed(conn, name_of, item.proposer_id)} has stopped selling you "
                f"{what} since turn {item.opened_turn}. They are losing revenue to do it.")
        lines.append(
            "  You could answer in kind -- embargo something you sell them -- or seek the "
            "good elsewhere, or settle whatever they want settled.")
    if offers:
        lines.append("")
        lines.append("**What an embargo of your own would cost and buy:**")
        lines.extend(entry for _bill, entry in offers[:BRIEFING_OFFERS])
        lines.append(
            f"  An embargo stops those cargoes and their payments, costs you the revenue "
            f"named, and sours the relationship. It must stand {MINIMUM_MONTHS:.0f} months "
            "before you may lift it.")
    lines.append("")
    return lines
