"""What the player should worry about, ranked so the worst is never below the fold.

Every signal here already existed and was already correct; none of it was anywhere a player
would look. A capital adjacent to an enemy army, a treasury that cannot pay next month's
upkeep, an agreement expiring in two turns, a colony past the point of holding -- all
readable, none surfaced.

**Ranked by the same salience the news uses**, deliberately. Four bands, and the boundaries
are about what a staff must not miss rather than about drama: *three* is force or a state
changing shape, *two* is a commitment made or broken, *one* is everything else that happened
to somebody, *zero* is the tick. Building a second scale here would mean an alert list and a
newspaper that disagreed about what mattered, and the player reading both would trust
neither.

**Two kinds of signal, and the distinction is load-bearing.** Some alerts are *events* -- a
war was declared this turn -- and rank straight off :func:`~atlas.state.visibility.salience_of`.
Others are *standing conditions* that no event announces: nobody writes "your treasury is
short" every turn, and a player whose capital has been adjacent to an enemy for six turns
needs telling on the sixth as much as the first. Those are read from state and given the
band their consequence deserves.

**Nothing is invented.** A category with no live signal is absent rather than reassuring: an
alert list that says "no threats" is making a claim, and this one only ever reports what it
found.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..state.visibility import salience_of
from .pacing import days_between

__all__ = ["ALERT_KINDS", "Alert", "alerts_for"]

#: Every kind of alert, with the band it sits in when it fires.
#:
#: The bands are :data:`atlas.state.visibility.SALIENCE`'s, so an alert and a headline about
#: the same thing sort together. A standing condition is given the band its *consequence*
#: has: losing your capital is a three whether or not anything happened this turn.
ALERT_KINDS: dict[str, tuple[int, str]] = {
    "capital_threatened": (3, "your capital is within reach of an enemy"),
    "capital_lost": (3, "your capital is in someone else's hands"),
    "at_war": (3, "a war began"),
    "nuclear": (3, "a nuclear weapon was used"),
    "front_unsupported": (2, "ground you hold has nothing standing on it"),
    "agreement_expiring": (2, "an agreement is about to lapse"),
    "agreement_broken": (2, "an agreement was broken"),
    "arrears": (2, "you cannot pay what you owe"),
    # **A two, beside the agreement bands, because it is the same kind of fact.** A lane
    # about to stop is a commitment about to stop being honoured in practice, and a player
    # who loses 38% of their imports in one turn without notice has been told the economy is
    # weather. The stop itself is invisible until it fires -- `Band.overlaps` is a sign test
    # and nothing carries the ratio -- which is exactly why the *approach* has to be an
    # alert rather than left to be inferred from a price column that is frozen for six
    # months at a time.
    "trade_stopping": (2, "a supplier's cargo is about to stop being worth taking"),
    "trade_stopped": (2, "a cargo has stopped and the agreement still stands"),
    "trade_stopping_more": (1, "further cargoes are approaching the same threshold"),
    "sky_lost": (2, "an enemy holds the sky over ground you care about"),
    "supply_interdicted": (2, "an enemy sky is strangling your supply"),
    "sky_lapsing": (1, "a sky you hold is about to be let go of"),
    "independence": (1, "a colony is past the point of holding it"),
    "order_refused": (1, "an order was refused"),
    "research_done": (0, "a programme finished"),
}

#: How near an agreement's expiry has to be to be worth saying. Two turns is enough warning
#: to do something about it and not so much that it sits there for a year.
_EXPIRY_WARNING = 2

#: How close to lapsing an agreement has to be before it is worth a line, in **days of
#: world time**. Two months, which is what `_EXPIRY_WARNING` meant when a turn was a month
#: and every agreement was counted in turns. The turn figure is kept for agreements written
#: before schema 35, which have no date to measure against.
_EXPIRY_WARNING_DAYS = 62.0

#: How far below its own upkeep a treasury has to be before it is an alert rather than a
#: tight month.
_ARREARS_MARGIN = 1.0

#: How many approaching trade stoppages are named one at a time before the rest are counted.
#:
#: **A cap with a receipt, not a silent truncation.** One point of regard moves 38% of
#: modern-day's trade by value across 132 of its 181 nations at once, because the crossing is
#: the root of an equation whose only free variable is a dependency the seeder hands out at
#: one value -- so a great power can genuinely have a dozen lanes warning on the same turn,
#: and a dozen alerts is a page that hides the two that matter. The most urgent are named,
#: the remainder are counted in a line of their own, and nothing is dropped without saying
#: so.
_TRADE_ALERTS_NAMED = 4

#: Where a warning with no time attached sorts. Larger than any real notice, so a lane that
#: can be given a deadline is always named before one that cannot.
WARNING_DAYS_UNKNOWN = 10_000


@dataclass(frozen=True, slots=True)
class Alert:
    """One thing worth the player's attention."""

    kind: str
    #: The band, from :data:`ALERT_KINDS` or from the event's own salience.
    salience: int
    #: What it is, in one line.
    headline: str
    #: Why it matters, and what it is about.
    detail: str = ""
    #: For a click through to the map.
    region_id: str = ""
    #: For a click through to the causal inspector.
    event_id: int = 0
    #: For a click through to the agreement.
    commitment_id: int = 0
    #: For a click through to the other party, where that is the most specific thing this
    #: alert is about. **The last resort of the four**, and the one a trade lane falls back
    #: to: a route the seeder opened is not a commitment and sits in no province, so the
    #: government at the other end of it is the nearest thing the program can already
    #: travel to. See :func:`atlas.engine.attention._standing_order_and_nav`, which reads
    #: these four in that order.
    nation_id: str = ""

    @property
    def label(self) -> str:
        return ALERT_KINDS.get(self.kind, (self.salience, self.kind))[1]


def _one(conn: sqlite3.Connection, sql: str, *args) -> str:
    row = conn.execute(sql, args).fetchone()
    return "" if row is None or row[0] is None else str(row[0])


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def _capital_alerts(conn, world_id: str, nation_id: str) -> list[Alert]:
    """The gravest thing that can be true of a country, and the one before it."""
    row = conn.execute(
        "SELECT capital_region_id FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    capital = str(row["capital_region_id"] or "") if row else ""
    if not capital:
        return []

    held = conn.execute(
        "SELECT name, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE id = ?", (capital,)).fetchone()
    if held is None:
        return []
    name = str(held["name"])
    if str(held["holder"]) != nation_id:
        return [Alert("capital_lost", ALERT_KINDS["capital_lost"][0],
                      f"{name} is held by {held['holder']}",
                      "Your seat of government is in someone else's hands.",
                      region_id=capital)]

    # Adjacent to anybody you are at war with. Read from the map and the relation, so it is
    # a fact rather than a guess about intent.
    enemies = _rows(conn, """
        SELECT r.id AS id, r.name AS name,
               COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder
          FROM region_adjacency a
          JOIN region r ON r.id = CASE WHEN a.region_a = ? THEN a.region_b ELSE a.region_a END
         WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
           AND EXISTS (SELECT 1 FROM relation rel WHERE rel.world_id = a.world_id
                        AND rel.nation_a = ? AND rel.at_war = 1
                        AND rel.nation_b = COALESCE(r.controller_nation_id, r.owner_nation_id))
    """, (capital, world_id, capital, capital, nation_id))
    if not enemies:
        return []
    who = sorted({str(row["holder"]) for row in enemies})
    return [Alert("capital_threatened", ALERT_KINDS["capital_threatened"][0],
                  f"{name} borders ground held by {', '.join(who)}",
                  "Your capital is one province from an enemy.",
                  region_id=capital)]


def _money_alerts(conn, world_id: str, nation_id: str) -> list[Alert]:
    """A treasury that cannot pay next month is a decision, not a number."""
    from .budget import nation_budget

    row = conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    if row is None:
        return []
    treasury = float(row["treasury"])
    revenue, upkeep = nation_budget(conn, world_id, nation_id)
    shortfall = upkeep - revenue
    if shortfall <= 0 or treasury >= shortfall * _ARREARS_MARGIN:
        return []
    # **A month, not a turn, and the runway with it.** `nation_budget` is a monthly figure --
    # every upkeep constant is per formation per month and `collect_revenue` scales it by the
    # span. Calling it "a turn" was right only at the default pacing; at yearly it understated
    # the bill twelvefold, and "12 turns of cash" would have promised twelve years of it.
    months = treasury / shortfall if shortfall else 0.0
    return [Alert("arrears", ALERT_KINDS["arrears"][0],
                  f"You are short {shortfall:.1f} a month",
                  f"Upkeep is {upkeep:.1f} against {revenue:.1f} of revenue, and the "
                  f"treasury holds {treasury:.1f} — about {months:.0f} months of it.")]


def _agreement_alerts(conn, world_id: str, nation_id: str, turn: int) -> list[Alert]:
    out: list[Alert] = []
    # **Warned on the calendar, because that is what it now runs on.** An agreement's life
    # became a world date at schema 35 and this still counted turns to it, so a player at
    # yearly pacing was told a pact lapsed "in 2 turns" about something two *years* away --
    # and one at monthly pacing was warned about the same agreement twenty-two months late.
    # The turn is still read for an agreement written before there was a date.
    today = _one(conn, "SELECT world_date FROM world WHERE id = ?", world_id)
    for row in _rows(conn, """
        SELECT c.id, c.kind, c.status, c.expires_turn, c.expires_on, c.condition_text
          FROM commitment c JOIN commitment_nation m ON m.commitment_id = c.id
         WHERE c.world_id = ? AND m.nation_id = ? AND c.status = 'active'
           AND (c.expires_on IS NOT NULL AND c.expires_on <> ''
                OR c.expires_turn IS NOT NULL)
         ORDER BY COALESCE(c.expires_on, ''), c.expires_turn, c.id
    """, (world_id, nation_id)):
        ends_on = str(row["expires_on"] or "")
        if ends_on and today:
            days = days_between(today, ends_on)
            if days is None or days > _EXPIRY_WARNING_DAYS:
                continue
            when = "this month" if days <= 0 else f"on {ends_on}"
        else:
            if row["expires_turn"] is None:
                continue
            left = int(row["expires_turn"]) - turn
            if left > _EXPIRY_WARNING:
                continue
            when = ("this turn" if left <= 0
                    else f"in {left} turn{'s' if left != 1 else ''}")
        out.append(Alert(
            "agreement_expiring", ALERT_KINDS["agreement_expiring"][0],
            f"{str(row['kind']).replace('_', ' ')} lapses {when}",
            str(row["condition_text"] or "")[:160],
            commitment_id=int(row["id"])))

    for row in _rows(conn, """
        SELECT c.id, c.kind, c.breached_by_nation_id AS who, c.condition_text
          FROM commitment c JOIN commitment_nation m ON m.commitment_id = c.id
         WHERE c.world_id = ? AND m.nation_id = ? AND c.status = 'broken'
           AND c.breached_turn = ?
    """, (world_id, nation_id, turn)):
        out.append(Alert(
            "agreement_broken", ALERT_KINDS["agreement_broken"][0],
            f"{str(row['kind']).replace('_', ' ')} was broken by {row['who']}",
            str(row["condition_text"] or "")[:160],
            commitment_id=int(row["id"])))
    return out


def _front_alerts(conn, world_id: str, nation_id: str) -> list[Alert]:
    """Ground you hold, next to somebody at war with you, with nothing standing on it."""
    return [
        Alert("front_unsupported", ALERT_KINDS["front_unsupported"][0],
              f"{row['name']} is held by nobody in arms",
              f"It borders {row['threat']}, who you are at war with, and no formation of "
              "yours is standing in it.",
              region_id=str(row["id"]))
        for row in _rows(conn, """
            SELECT r.id AS id, r.name AS name,
                   COALESCE(e.controller_nation_id, e.owner_nation_id) AS threat
              FROM region r
              JOIN region_adjacency a
                ON a.world_id = r.world_id AND (a.region_a = r.id OR a.region_b = r.id)
              JOIN region e
                ON e.id = CASE WHEN a.region_a = r.id THEN a.region_b ELSE a.region_a END
              JOIN relation rel
                ON rel.world_id = r.world_id AND rel.nation_a = ? AND rel.at_war = 1
               AND rel.nation_b = COALESCE(e.controller_nation_id, e.owner_nation_id)
             WHERE r.world_id = ? AND r.kind <> 'ocean'
               AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
               AND NOT EXISTS (SELECT 1 FROM unit u WHERE u.region_id = r.id
                                AND u.nation_id = ?)
             GROUP BY r.id ORDER BY r.id
        """, (nation_id, world_id, nation_id, nation_id))
    ]


def _colony_alerts(conn, world_id: str, nation_id: str) -> list[Alert]:
    from .decolonisation import INDEPENDENCE_THRESHOLD, restive_dependencies

    return [
        Alert("independence", ALERT_KINDS["independence"][0],
              f"{entry['name']} is at {entry['momentum']:.0f} and will not be held",
              "Past the point where a garrison buys anything; letting it go on your own "
              "terms is the only ending that is not a defeat.",
              region_id=str(entry["region_id"]))
        for entry in restive_dependencies(conn, world_id, nation_id)
        if entry["momentum"] >= INDEPENDENCE_THRESHOLD
    ]


def _turn_alerts(conn, world_id: str, nation_id: str, turn: int) -> list[Alert]:
    """This turn's own events, ranked by the salience the newspaper gives them.

    Only the ones the player is *in*: a war somewhere else is news, not an alert.

    **In means named on it, not only its actor.** A declaration of war is written with the
    aggressor as its actor and the victim as a ``target`` participant, so matching the actor
    alone told the aggressor a war had begun and told the nation it was declared on nothing
    -- the one government that most needed the line. The same is true of a battle fought on
    your ground and a siege of your city -- and of a province taken from you, wherever the
    event names its loser (the settlement's and the peace table's annexations name only the
    gainer so far). A mediator is named on an event without being party to it, so it is the
    one role left out.

    **And named is not the same as told.** The actor witnessed its own act, so matching the
    actor alone could never quote anybody a line they had not seen. A participant can be
    the one nation an event is kept from -- a covert act names its victim as ``target`` so
    attribution can find it -- and the alert quotes the event's text. So a participant is
    alerted only to what it could read anyway: public, or let in on it by an
    ``event_visibility`` row, the predicate :func:`atlas.state.visibility.known_events` uses.
    """
    out: list[Alert] = []
    for row in _rows(conn, """
        SELECT e.id, e.kind, e.text FROM event e
         WHERE e.world_id = ? AND e.turn = ?
           AND e.kind IN ('war_declared', 'battle', 'siege', 'territory_transfer', 'coup')
           AND (e.actor_nation_id = ?
                OR (EXISTS (SELECT 1 FROM event_participant p
                             WHERE p.event_id = e.id AND p.nation_id = ?
                               AND p.role <> 'mediator')
                    AND (e.visibility = 'public'
                         OR EXISTS (SELECT 1 FROM event_visibility v
                                     WHERE v.event_id = e.id AND v.nation_id = ?))))
         ORDER BY e.id
    """, (world_id, turn, nation_id, nation_id, nation_id)):
        rendered = {"kind": str(row["kind"]), "text": str(row["text"] or "")}
        kind = "nuclear" if "nuclear" in rendered["text"].lower() else "at_war"
        out.append(Alert(kind, salience_of(rendered),
                         rendered["text"][:110], "", event_id=int(row["id"])))

    for row in _rows(conn, """
        SELECT b.id, b.rejection_reason FROM diff_batch b
         WHERE b.world_id = ? AND b.turn = ? AND b.status = 'rejected'
           AND b.proposer_nation_id = ?
    """, (world_id, turn, nation_id)):
        out.append(Alert(
            "order_refused", ALERT_KINDS["order_refused"][0],
            "An order was refused", str(row["rejection_reason"] or "")[:160]))

    for row in _rows(conn, """
        SELECT tech_id FROM technology
         WHERE world_id = ? AND nation_id = ? AND completed_turn = ?
    """, (world_id, nation_id, turn)):
        out.append(Alert(
            "research_done", ALERT_KINDS["research_done"][0],
            f"{str(row['tech_id']).replace('_', ' ')} is finished",
            "Whatever it unlocks is available from now on."))
    return out


def _trade_alerts(conn, world_id: str, nation_id: str) -> list[Alert]:
    """Lanes this government is at either end of that are near, or past, a party's line.

    **Both ends, because the end that stops lanes is the one this used to ignore.** The first
    version queried ``buyer_id = ?`` and warned on :func:`atlas.engine.consent.approach`,
    which solves a crossing on the buyer's regard. Classifying every in-play suspension
    across the play battery -- `scratchpad/cliff_inputs.py`, written up in
    `scratchpad/CLIFF_DIAGNOSIS.md` -- **none of them was on that axis**: 95% were the
    *seller* withdrawing after the quote fell to :data:`atlas.engine.pricing.FLOOR_PRICE`, on
    a route this function was not looking at, for a reason it could not express.

    So the query is both sides and the trigger is
    :attr:`atlas.engine.consent.Headroom.warned` -- the margin to whichever line is nearer,
    plus the step waiting at the next repricing, whatever moved either of them.

    **Read fresh, on the terms that are actually moving.** Nothing here is stored: the
    warning is derived when the player looks, from the same closed form the briefing uses, so
    it can never be a stale copy of a threshold that has since moved.

    Empty whenever :mod:`atlas.engine.pricing` is down -- which is every world that has not
    asked for it -- so an alert list in a barter world is the list it always was.
    """
    from . import consent, pricing

    if not pricing.enabled() or not consent.enabled():
        return []
    try:
        routes = _rows(conn, """
            SELECT id, seller_id, buyer_id, good, unit_price, suspended_turn, suspended_by
              FROM trade_route
             WHERE world_id = ? AND (buyer_id = ? OR seller_id = ?) ORDER BY id
        """, (world_id, nation_id, nation_id))
    except sqlite3.OperationalError:
        # A world older than the pricing columns has routes and no terms to be near the edge
        # of. Not an error and not a reason to drop every other alert on the page.
        return []

    out: list[Alert] = []
    #: The approaching stoppages, held back until they can be ranked by how little room is
    #: left rather than by route id -- the route table's order knows nothing about that.
    coming: list[tuple[float, Alert]] = []
    lanes: dict = {}
    alternatives_cache: dict = {}
    distress_cache: dict = {}
    #: ``route id -> commitment id``. One lookup per lane at most, and a great power can
    #: have a hundred of them on a turn where one point of regard moved.
    commitments: dict = {}
    name_of = _namer(conn)
    turn, turn_span = consent.turn_and_span(conn, world_id)
    for row in routes:
        seller, buyer = str(row["seller_id"]), str(row["buyer_id"])
        good = str(row["good"])
        price = float(row["unit_price"] or 0.0)
        if price <= 0:
            continue
        buying = buyer == nation_id
        other = seller if buying else buyer
        if int(row["suspended_turn"]) >= 0:
            side = str(row["suspended_by"] or "")
            # **Which side, and in the words of the party that stopped.** "The two sides no
            # longer overlap" was true of every suspension and identified none of them; the
            # measured majority are the seller withdrawing at the floor, and a government
            # told only that the terms do not overlap cannot tell that from its own customer
            # having walked.
            whose = ("the buyer would not pay it" if side == "buyer"
                     else "the seller would not ship at it" if side == "seller"
                     else "the two sides' terms no longer overlap")
            out.append(Alert(
                "trade_stopped", ALERT_KINDS["trade_stopped"][0],
                (f"{good.capitalize()} from {name_of(seller)} has stopped" if buying
                 else f"{good.capitalize()} to {name_of(buyer)} has stopped"),
                f"At {price:.2f} a unit, {whose}. The agreement stands and nothing was "
                "breached; the cargo moves again the turn the two sides' terms overlap. "
                "Your trade briefing gives the two lines and what is moving them.",
                commitment_id=_route_commitment(conn, str(row["id"]), commitments),
                nation_id=other))
            continue
        near = consent.headroom(
            conn, world_id, seller, buyer, good, standing_price=price,
            route_id=str(row["id"]), turn=turn, turn_span=turn_span,
            seller_name=name_of(seller), lanes=lanes,
            alternatives_cache=alternatives_cache, distress_cache=distress_cache,
        )
        if near is None or not near.warned:
            continue
        cargo = (f"{good.capitalize()} from {name_of(other)}" if buying
                 else f"{good.capitalize()} to {name_of(other)}")
        stepping = near.steps_out
        if stepping:
            # **A review, not a reopening.** The turn is certain; what the file reopens at
            # is re-derived from the world of that day, and the term that decides it moves
            # by an order inside three turns -- so the headline names the date and the
            # course, and stops short of promising the outcome. See
            # `consent.headroom_lines` on `player`; this list is a player surface only.
            headline = (f"{cargo} is on course to stop at its next commercial review, "
                        f"turn {near.next_reprice_turn}")
        elif near.binding == "seller":
            headline = f"{cargo} is {near.margin:.0%} above the seller's floor"
        else:
            headline = f"{cargo} is within {near.margin:.0%} of what the buyer will pay"
        body = " ".join(
            line.lstrip(" -").strip()
            for line in consent.headroom_lines(near, name_of(other), good, buying=buying,
                                                player=True)
        ).replace("**", "")
        # Ranked by how little room is left, so the cap keeps the lanes nearest the edge. A
        # lane whose *next repricing* takes it out ranks ahead of any margin: it has a date.
        coming.append((-1.0 if stepping else near.margin,
                       Alert("trade_stopping", ALERT_KINDS["trade_stopping"][0],
                             headline, body,
                             commitment_id=_route_commitment(
                                 conn, str(row["id"]), commitments),
                             nation_id=other)))

    coming.sort(key=lambda pair: (pair[0], pair[1].headline))
    out.extend(alert for _room, alert in coming[:_TRADE_ALERTS_NAMED])
    rest = len(coming) - _TRADE_ALERTS_NAMED
    if rest > 0:
        # **The rollup travels to the nearest lane it is counting**, which is the first one
        # the cap cut: the list is already sorted by how little room is left, so
        # `coming[_TRADE_ALERTS_NAMED]` is the most urgent thing this line stands for. A
        # counted remainder with nowhere to click is the failure this line was written to
        # avoid one step earlier -- it exists so that nothing is dropped without saying so,
        # and a dead end says so and then drops it anyway.
        nearest = coming[_TRADE_ALERTS_NAMED][1]
        out.append(Alert(
            "trade_stopping_more", ALERT_KINDS["trade_stopping_more"][0],
            f"{rest} further route(s) are also close to one side's terms",
            "Your trade briefing lists each one with both lines and what is moving them.",
            commitment_id=nearest.commitment_id, nation_id=nearest.nation_id))
    return out


#: How :func:`atlas.engine.treaties.honour_terms` builds the id of a route it opened:
#: ``tr_c<commitment_id>_<seq>``. Parsed rather than stored, because the id *is* the
#: derivation -- it is built from the term that opened the route precisely so that a rewind
#: and a re-run produce the same one -- and a column duplicating it could go stale against a
#: route table that is rewritten every time an agreement is honoured.
_TREATY_ROUTE = re.compile(r"^tr_c(\d+)_\d+$")


def _route_commitment(conn: sqlite3.Connection, route_id: str, seen: dict) -> int:
    """The agreement a route came out of, or ``0`` where no agreement opened it.

    **Checked against the table, not just parsed.** A seeded route's id is
    ``tr:<good>:<seller>:<buyer>`` and matches nothing here, which is the honest answer --
    nobody signed for it. A treaty route's id names its commitment, and the lookup confirms
    the commitment is still there, so a click can never be offered into a row that a rewind
    has removed.
    """
    if route_id in seen:
        return seen[route_id]
    match = _TREATY_ROUTE.match(route_id)
    found = 0
    if match:
        commitment_id = int(match.group(1))
        row = conn.execute(
            "SELECT 1 FROM commitment WHERE id = ?", (commitment_id,)).fetchone()
        found = commitment_id if row is not None else 0
    seen[route_id] = found
    return found


def _namer(conn: sqlite3.Connection):
    """``id -> name``, cached. The alert list names the same handful of powers repeatedly
    and each lookup is its own query."""
    seen: dict[str, str] = {}

    def name_of(nation_id: str) -> str:
        if nation_id not in seen:
            row = conn.execute(
                "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
            seen[nation_id] = str(row["name"]) if row is not None else nation_id
        return seen[nation_id]

    return name_of

#: What an interdicted corridor's severity is worth on the attention list, by the band
#: :func:`atlas.engine.airsituation.interdiction_against` measured. A floor, not a
#: replacement: :data:`ALERT_KINDS` still owns the kind's base salience and this can only
#: raise it, so a re-banded alert kind cannot be silently demoted from here.
#:
#: `severed` and `collapsed` are *act now* because by then formations are already out of
#: supply; the two below them are the kind's own band, which is *decide this turn*.
_INTERDICTION_SALIENCE: dict[str, int] = {
    "contained": 0,
    "constricted": 0,
    "severed": 3,
    "collapsed": 3,
}

#: How close to being let go of a sky the player holds has to be before it is worth a line,
#: **in months of world time**.
#:
#: One month, and the unit is the whole point. The first cut of this counted *turns* --
#: ``(margin - floor) // _LAPSE_PER_MONTH + 1`` -- which silently reads a per-month decay as
#: a per-turn one, so at yearly pacing it warned about a sky with eleven months of margin
#: left and at weekly pacing it warned four times about the same one. `atlas.engine.pacing`'s
#: rule exactly: *how near a thing is to happening in the world* is elapsed time and must not
#: move with the pace control, and the way not to get that wrong is to write the unit in the
#: name. Caught by `test_which_clock_each_duration_is_on`, which is what that file is for.
#:
#: One rather than more because a sky you hold is good news, and good news every turn is how
#: a list stops being read.
_LAPSE_WARNING_MONTHS = 1.0

#: How many provinces an alert about a campaign names before it starts counting. Four is
#: enough for the shape of a front and short enough to read in one line; past that, *and
#: eleven more* is the useful sentence and the list is not.
_NAMED_IN_AN_ALERT = 4


def _controlled_by(conn, region_id: str, nation_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM region WHERE id = ?"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
        (region_id, nation_id)).fetchone()
    return row is not None


def _air_alerts(
    conn, world_id: str, nation_id: str, watching: frozenset[str]
) -> list[Alert]:
    """The air war, but only where it is this player's air war.

    **The filter is the whole design.** A sky changes hands somewhere in the world most
    turns of most wars, and an alert for every air contest is an alert list nobody reads --
    the same failure the attention substrate had to fix when twenty-two of Ukraine's
    twenty-eight items were one air war told province by province. So a theatre reaches this
    list only when it is one of four things, read from the world rather than guessed:

    * ground this player holds or owns,
    * ground an ally holds -- somebody they have signed something with,
    * ground a power they are at war with holds,
    * a province they have *pinned*, which is the player saying it matters in as many words.

    Everything else is the world's air war and belongs in the newspaper. `watching` comes
    from the watchlist, which lives beside the save rather than in it -- so it is passed in
    rather than read here, and a caller with no pins passes nothing and loses no other case.
    """
    enemies = {
        str(row[0]) for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND at_war = 1", (world_id, nation_id))
    }
    if not enemies and not watching:
        # No war and nothing pinned: nothing about a sky can be about this player yet.
        # `air_control` rows only exist where two powers at war both flew, so this is the
        # ordinary case and it costs one statement.
        return []
    allies = {
        str(row[0]) for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND at_war = 0 AND treaty_status <> 'none'", (world_id, nation_id))
    }

    from .air import _CONTROL_FLOOR, _LAPSE_PER_MONTH
    from .airsituation import interdiction_against

    # **Grouped by who is doing it, not listed by province.** An air campaign is one fact
    # told over as much ground as it covers: measured on this layer's own driver, a single
    # turn of one war produced *five* separate "Karthos holds the sky over X" alerts, which
    # is the failure the attention substrate was built to end -- twenty-two of Ukraine's
    # twenty-eight items being one air war told province by province. The substrate's
    # per-source budget would have capped the display at three, and three lines saying the
    # same thing is still three lines saying the same thing. So it is one alert per enemy,
    # naming the ground.
    lost: dict[str, list[tuple[str, str]]] = {}
    lapsing: list[tuple[str, str, float]] = []
    for row in _rows(conn, """
        SELECT a.region_id, a.holder_nation_id AS holder, a.margin, r.name,
               COALESCE(r.controller_nation_id, r.owner_nation_id) AS ground
          FROM air_control a JOIN region r ON r.id = a.region_id
         WHERE a.world_id = ? AND a.holder_nation_id IS NOT NULL AND a.margin >= ?
         ORDER BY a.region_id
    """, (world_id, _CONTROL_FLOOR)):
        region_id, holder = str(row["region_id"]), str(row["holder"])
        ground = str(row["ground"] or "")
        relevant = (
            ground == nation_id or ground in allies or ground in enemies
            or region_id in watching
        )
        if not relevant:
            continue
        if holder in enemies:
            lost.setdefault(holder, []).append((region_id, str(row["name"])))
        elif holder == nation_id:
            # **Only when it is about to go.** A sky you hold is good news, and good news
            # every turn is how a list stops being read.
            margin = float(row["margin"])
            if margin - _CONTROL_FLOOR <= _LAPSE_PER_MONTH * _LAPSE_WARNING_MONTHS:
                lapsing.append((region_id, str(row["name"]), margin))

    out: list[Alert] = []
    for holder in sorted(lost):
        where = lost[holder]
        who = _one(conn, "SELECT name FROM nation WHERE id = ?", holder) or holder
        named = ", ".join(name for _id, name in where[:_NAMED_IN_AN_ALERT])
        rest = len(where) - _NAMED_IN_AN_ALERT
        out.append(Alert(
            "sky_lost", ALERT_KINDS["sky_lost"][0],
            f"{who} holds the sky over {named}"
            + (f" and {rest} more" if rest > 0 else ""),
            "It is worth up to a tenth of the odds of any battle fought there, more than "
            "twice that over water, and — where the ground is yours — two extra hops to "
            "supply anything through it.",
            # The click-through goes to ground the player actually holds where there is
            # any, because that is the province where this is costing them something.
            region_id=next(
                (rid for rid, _name in where if _controlled_by(conn, rid, nation_id)),
                where[0][0])))

    if lapsing:
        named = ", ".join(name for _id, name, _m in lapsing[:_NAMED_IN_AN_ALERT])
        rest = len(lapsing) - _NAMED_IN_AN_ALERT
        out.append(Alert(
            "sky_lapsing", ALERT_KINDS["sky_lapsing"][0],
            f"Your hold on the sky over {named}"
            + (f" and {rest} more" if rest > 0 else "") + " is nearly gone",
            "An unflown sky decays every turn and is let go of under the dead band. Air "
            "superiority is maintained, not owned.",
            region_id=lapsing[0][0]))

    # One line for the whole corridor, not one per province: what a quartermaster needs to
    # know is how much ground has gone out of reach, and *that* is one fact.
    interdiction = interdiction_against(conn, world_id, nation_id)
    if interdiction.active and interdiction.lost:
        by = ", ".join(name for _id, name in interdiction.by)
        # **The band the panel shows is the band the list ranks on.** A corridor that has
        # lost two thirds of its reach is not the same news as one that has lost a tenth,
        # and an attention list that files both under *decide this turn* is asking the
        # player to open the panel to find out which one they are looking at. Escalated
        # rather than re-banded from scratch: the kind's own salience stays the floor.
        salience = max(
            ALERT_KINDS["supply_interdicted"][0],
            _INTERDICTION_SALIENCE.get(interdiction.severity, 0))
        out.append(Alert(
            "supply_interdicted", salience,
            f"{by} is interdicting your supply over "
            f"{len(interdiction.corridors)} province(s) — {interdiction.severity}",
            f"{len(interdiction.lost)} province(s) you could otherwise supply are out of "
            f"reach, with {interdiction.formations_cut} formation(s) standing in them: "
            f"{interdiction.share_lost:.0%} of the corridor. "
            "Contest the sky over your own ground, or route around it.",
            region_id=interdiction.corridors[0][0]))
    return out


def alerts_for(
    conn: sqlite3.Connection, world_id: str, nation_id: str | None, *, turn: int,
    watching: frozenset[str] = frozenset(),
) -> list[Alert]:
    """Everything the player should be looking at, gravest first. Read-only.

    Ties break on kind and then on headline, so the same world always produces the same
    order -- a list that shuffled between openings would be unreadable however well ranked.
    """
    if not nation_id:
        return []

    found = [
        *_capital_alerts(conn, world_id, nation_id),
        *_turn_alerts(conn, world_id, nation_id, turn),
        *_front_alerts(conn, world_id, nation_id),
        *_agreement_alerts(conn, world_id, nation_id, turn),
        *_money_alerts(conn, world_id, nation_id),
        *_colony_alerts(conn, world_id, nation_id),
        *_trade_alerts(conn, world_id, nation_id),
        *_air_alerts(conn, world_id, nation_id, watching),
    ]
    found.sort(key=lambda alert: (-alert.salience, alert.kind, alert.headline))
    return found
