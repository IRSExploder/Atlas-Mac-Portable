"""Why the player should care about this now, decided once and read by everything.

Every hierarchy surface the interface is growing -- the first-turn funnel, the post-turn
report, the event feed, the alert list, a diplomacy list that puts the war above the trade
partner, the watchlist -- has to answer the same question before it can order anything:
*of everything true in this world right now, what is worth this player's attention, and
why?* Six surfaces answering it six times is six rankings that disagree, and a player
reading two of them trusts neither. This answers it once.

**The engine selects and justifies. A model may only phrase.** Nothing here calls a
provider, and the ranking is a weighted sum of numbers read out of the world -- so the same
world and the same viewer produce the same list in the same order, every time, and a
fingerprint taken across a call is unmoved. A model may later be handed an item's
``subject`` and asked for a better sentence; it may never be handed the list and asked what
belongs in it. That boundary is the whole reason this is a module and not a prompt.

**Four axes, and they are different questions.** Severity is how large the consequence is
if this is ignored; urgency is how soon it forces a decision; player relevance is how much
it is about *this* viewer rather than about the world; novelty is how recently it changed.
A war going badly is severe and not novel. A neighbour's coup is novel and, to a viewer on
the other side of the map, barely relevant. Collapsing them into one number before the
weighting is what makes a feed put a finished workshop beside a lost capital.

**Viewer-scoped, through the readers that already were.** The standing conditions come from
:func:`atlas.engine.alerts.alerts_for`, which only ever reads the viewer's own state; the
news comes from :func:`atlas.state.visibility.known_events`, which is the same visibility
predicate the dispatch and the trace panels use. Nothing here opens a second route to the
event log, so a secret this viewer is not entitled to cannot reach the list by coming in
through the ranking.

**Nothing here writes, gates, or scores the game.** Every dial in this module orders a
list. None of them is read by a turn, a rule, a validation or a victory condition, and a
different weighting changes what a player sees first and nothing about what is true.

**A per-turn read, not a per-frame one.** Bargaining positions and unrest loads are
world-wide aggregates. Measured on the played Modern Day world at turn 14: 2.7 ms for the
average power and 7.2 ms for Russia, which has the most to answer for -- inside the 16 ms
frame budget, but only just, and it is a whole frame spent on a list that changes once a
turn. A caller behind a repaint should hold the list it built when the turn landed rather
than rebuilding it on a scroll.

**What each surface asks for.** Nothing consumes this yet -- the surfaces are a later tier
and some of them are being edited on another branch -- so the shape each one needs is
recorded here rather than discovered six separate times:

* *The first turn's "three matters"*: ``attention_for(..., limit=3)``. The limit is a prefix
  of the same ranked list every other surface reads, which is what makes "the three things"
  and "the ten things" agree about the first three. Show ``subject`` and ``reason_selected``,
  with a control per item built from ``nav_target`` and ``suggested_order_category``.
* *The post-turn "what affected you"*: the same call at the new turn, kept to the items with
  ``novelty > 0`` -- which is precisely "what changed", and is a filter on a field rather
  than a second selection. ``source_event_ids`` gives the report its click-through into the
  causal inspector.
* *Event ordering*: the feed already has the events; what it lacks is a reason to put one
  above another. Take the items whose ``source`` is ``"news"``, lead with them in ``score``
  order, and leave the remainder of the feed in recency order beneath.
* *Alerts*: this is a strict superset of :func:`atlas.engine.alerts.alerts_for`, which it
  calls -- every alert kind is a source here. The panel becomes a filter on
  ``source in ALERT_KINDS`` ordered by ``score``, and its own band sort goes away.
* *Diplomacy priority*: group by ``nation_id``, take each nation's best ``score``, and order
  the contact list by that. A power you are at war with outranks a trade partner because the
  war outranks the trade agreement, rather than because the list was told to put wars first.
* *The watchlist*: a pin resolves to a region, nation, event or treaty -- the same four kinds
  ``nav_target`` uses. Match a pinned target against ``nav_target.target`` to mark the pins
  that currently have something to say, and use ``reason_selected`` as the line that says
  it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace

from ..state.visibility import known_events, salience_of
from .alerts import ALERT_KINDS, Alert, alerts_for
from .capabilities import by_type
from .leverage import Leverage, leverage_between
from .search import HIT_KINDS
from .unrest import held_foreign_provinces
from .wareffort import WarEffort, effort_between

__all__ = [
    "ACTIONABLE_BONUS",
    "ATTENTION_SOURCES",
    "AXES",
    "NAV_KINDS",
    "WEIGHTS",
    "AttentionItem",
    "NavTarget",
    "attention_for",
    "score_of",
]


# --------------------------------------------------------------------------- vocabulary

#: Where an item can send the player. **The search box's own kinds**, deliberately: a hit
#: kind is a kind the main window already knows how to travel to, so an attention item
#: cannot point somewhere there is no route to. Adding a nav kind means adding a way to
#: reach it, which is exactly the constraint :data:`atlas.engine.search.HIT_KINDS` carries.
NAV_KINDS: tuple[str, ...] = HIT_KINDS

#: The four axes, in the order a reader should think about them.
AXES: tuple[str, ...] = ("severity", "urgency", "player_relevance", "novelty")

#: What each axis is worth in the ranking. **Severity leads and novelty trails**, and the
#: reason is the failure mode of the surfaces this feeds: a feed ordered by recency puts a
#: finished workshop above a war that has been going badly for six turns, because the
#: workshop is what happened most recently. Novelty earns a weight because a thing that
#: *changed* is a thing the player has not priced in yet -- but it is the smallest, so it
#: reorders within a band rather than across one.
#:
#: Tunable: these are ranking dials, and nothing outside this module reads them.
WEIGHTS: dict[str, float] = {
    "severity": 0.40,
    "urgency": 0.25,
    "player_relevance": 0.20,
    "novelty": 0.15,
}

#: What being able to do something about it this turn is worth. Small on purpose -- it
#: breaks ties between comparable items in favour of the one the player can answer, and is
#: never enough to lift something trivial over something grave. A lost capital nothing can
#: be done about this turn stays above an investment that can.
ACTIONABLE_BONUS = 0.05

#: How many turns it takes for a change to stop being news, and how far back the news sweep
#: reaches. **Both are on the player's clock and both say so in their names**, which is
#: :mod:`tests.test_which_clock_each_duration_is_on`'s whole subject: a duration that is
#: honestly about somebody's attention is written in turns, and one that is about the world
#: is written in days and must not move with the pace control. Novelty is neither more nor
#: less than "have you seen this yet", and how many turns ago a thing happened is exactly
#: the right unit for that -- a player at yearly pacing has looked at the world as many
#: times as one at monthly, which is what the axis is measuring.
#:
#: Six and two. Six is long enough that a war declared last quarter still reads as new,
#: short enough that a standing condition never masquerades as one; two is what "recently"
#: means to somebody who has just taken a turn.
NOVELTY_TURNS = 6.0
NEWS_RECENT_TURNS = 2

#: How much of the news sweep survives. The window is ranked by salience before it is cut,
#: so the cap spends itself on the gravest news the viewer knows rather than on the most
#: recent.
NEWS_LIMIT = 6

#: **The sources that report an event rather than describe a condition.**
#:
#: The distinction runs through everything below it. A war, a threatened capital, a contest,
#: a restive province are *conditions*: they are true right now, they will still be true next
#: turn if nothing is done, and their severity is the size of what happens if nothing is.
#: A battle report, a refused order, a finished programme, a line of news are *reports*: they
#: are true of a moment that has passed.
#:
#: Both are worth a player's attention and neither can be dropped, but they cannot be
#: measured on the same scale, and two consequences follow.
_REPORTED_SOURCES: frozenset[str] = frozenset(
    {"at_war", "nuclear", "order_refused", "research_done", "news"})

#: **The first consequence: a report is not the condition it reports.**
#:
#: :func:`atlas.state.visibility.salience_of` bands an event by how much newspaper space it
#: deserves, and band three holds a nuclear launch and a skirmish alike -- which is right for
#: a newspaper and wrong for "how bad is it if you ignore this". Where a report *is* about a
#: standing condition, that condition is selected on its own account and carries its own
#: severity; letting the report carry the same weight counts one fact twice. Measured on the
#: played modern-day world at turn 14: undiscounted, three separate reports of the same air
#: war outranked *Ukraine is at war with Russia* on Ukraine's own list.
#:
#: Applied to every reported source rather than carved out kind by kind. A nuclear launch
#: discounted still sits at the top of everything else it is ranked against, and a carve-out
#: would be the beginning of a second severity table.
REPORT_SEVERITY = 0.70

#: **The second consequence: how many items of one kind a list may carry.**
#:
#: Measured too. Ukraine's list came back twenty-eight items long and the first twenty-two
#: were the same air war told province by province, because the alert layer writes one
#: `at_war` alert per *battle event*. A substrate that exists to stop surfaces flattening
#: cannot itself hand them a list flattened by whichever source happened to be most numerous.
#:
#: The default is three: enough that a front with several bad provinces reads as several, few
#: enough that no kind can own the top of the list. A reported source gets **one**, because
#: a turn's fighting is one fact told many times and even three is three lines saying it.
#: News is the exception among reports: each line is a different thing that happened, so it
#: keeps the default.
#:
#: **Every budget is at least one.** A kind worth selecting at all is worth one line, and a
#: budget of zero would leave the note saying what was dropped nowhere to go -- so it is a
#: contradiction rather than a setting, and :func:`_within_budget` raises on it rather than
#: quietly deleting a whole kind.
SOURCE_BUDGET_DEFAULT = 3
SOURCE_BUDGET: dict[str, int] = {
    source: 1 for source in sorted(_REPORTED_SOURCES) if source != "news"
}

#: The residual unrest at which a province reads as *not being held down*. A garrison strong
#: enough for the ground drives the residual to zero; this is where what is left over stops
#: being a nuisance. A ranking dial -- :mod:`atlas.engine.unrest` owns the arithmetic, and
#: this only decides how loudly to say it.
UNREST_URGENT_RESIDUAL = 2.0

#: Where a bargaining position becomes worth acting on, and where exhaustion does. The first
#: is :attr:`atlas.engine.leverage.Leverage.band`'s own boundary for *the stronger hand*;
#: the second is :data:`atlas.engine.wareffort.BANDS`' for *exhausted*. Taken from the bands
#: rather than invented, so a settlement item and the sentence describing it cannot disagree
#: about which side of the line the war is on.
SETTLE_FROM_STRENGTH = 0.12
SETTLE_FROM_EXHAUSTION = 70.0

#: Every kind of thing that can reach the list, and what it means. The standing conditions
#: are **generated from** :data:`atlas.engine.alerts.ALERT_KINDS` rather than restated: the
#: alert list is one of the surfaces this substrate exists to feed, and a kind added there
#: has to become selectable here without anybody remembering to add it in two places.
_OWN_SOURCES: dict[str, str] = {
    "war": "a war you are fighting",
    "settlement": "a war that could be ended now",
    "border_contested": "ground being pushed for",
    "opening": "enemy ground with nothing standing on it",
    "unrest": "held ground that will not stay held",
    "agreement_offered": "an agreement waiting on your answer",
    "news": "something that happened that you know about",
    # **The economic five, and they are crossings rather than conditions.** Every other
    # source here is true until somebody changes it, and says so every turn until they do.
    # An economy cannot be reported that way: a country is always dependent on somebody for
    # something, so a line that fired whenever a dependency was over sixty percent would be
    # a line a player stops reading by the third turn. These fire on the turn the measure
    # crosses and are silent afterwards however bad the number stays -- see
    # :func:`atlas.engine.dependency.warnings_for`, which reads the turn's own diff log to
    # find out where the number was before the turn started.
    "econ_dependency": "one power supplying most of a good you need",
    "econ_runway": "a store that will throttle your industry soon",
    "econ_price": "a lane whose terms moved further than anybody budgets for",
    "econ_customer": "one customer that has become most of an export income",
    "econ_arrears": "commitments the treasury cannot go on meeting",
}
ATTENTION_SOURCES: dict[str, str] = {
    **{kind: what for kind, (_band, what) in ALERT_KINDS.items()},
    **_OWN_SOURCES,
}

#: How soon each standing condition forces a decision, by the alert kind that carries it.
#: **Severity is generated** -- it is the alert's own salience band over three, so the panel
#: and this cannot come to disagree about how grave something is -- but urgency is a
#: different question that no existing scale answers: a capital in enemy hands is as grave as
#: a capital that could be lost next turn and there is nothing to decide about it today,
#: while an agreement lapsing this month is a smaller thing on a shorter fuse.
#:
#: Every key of :data:`ALERT_KINDS` must appear; a test asserts it, so the enum cannot grow
#: a kind that silently ranks at the default.
_STANDING_URGENCY: dict[str, float] = {
    "capital_threatened": 1.00,   # next turn, and there may not be another
    "capital_lost": 0.30,         # already happened; retaking it is a campaign, not a turn
    "at_war": 0.85,
    "nuclear": 0.90,
    "front_unsupported": 0.80,    # ground with nothing on it changes hands quickly
    # The air war's three. A lost sky is standing rather than urgent -- it is worth a tenth
    # of every battle fought under it and will still be worth that next month -- while an
    # interdicted corridor is a pocket forming, which is the shortest fuse on this table
    # after a threatened capital. A sky about to lapse is pure clock, like an expiring
    # agreement, and is ranked with it.
    "sky_lost": 0.55,
    "supply_interdicted": 0.85,
    "sky_lapsing": 0.70,
    "agreement_expiring": 0.70,   # the whole content of the warning is the clock
    "agreement_broken": 0.40,     # the damage is done; what follows is a choice
    "arrears": 0.75,
    # The three trade conditions sit **below every war alert and above the routine
    # economic ones**: a lane that has stopped is money not moving this turn, which is not
    # a front collapsing and is not a programme finishing either. Ordered by how much has
    # already happened rather than by band -- `trade_stopping_more` outranks the single
    # `trade_stopping` it is counting because breadth is the thing to act on: one lane near
    # the edge is a lane, several is the terms moving under you. **Deliberately the opposite
    # of the salience order** (bands 2, 2, 1), which is what having two axes is for --
    # severity says how grave one lane is, urgency says how soon you must answer.
    "trade_stopped": 0.65,        # already stopped, and stays stopped until terms overlap
    "trade_stopping_more": 0.58,  # several lanes at the edge at once
    "trade_stopping": 0.50,       # one lane, with room still in it
    "independence": 0.55,
    "order_refused": 0.60,        # re-issuable this turn and not after it
    "research_done": 0.25,        # the slot is free whenever you want it
}

#: **The order that answers each standing condition**, by the alert kind that carries it.
#: Ground is answered by moving forces onto it, an agreement by talking, a treasury by
#: spending less on formations. ``""`` where a condition has no answer a player can order --
#: a programme that has finished is worth knowing and there is nothing to do about it, and
#: an empty category is what that means everywhere.
#:
#: Exhaustive over :data:`ALERT_KINDS`, and asserted to be. A kind added to the alert layer
#: without an entry here would reach the player as something to worry about with no
#: affordance attached, which is the state the whole hierarchy effort exists to leave behind.
_STANDING_ORDER: dict[str, str] = {
    "capital_threatened": "move_units",
    "capital_lost": "move_units",
    "at_war": "move_units",
    "nuclear": "sue_for_peace",
    "front_unsupported": "move_units",
    # **All three are answered by moving aircraft, and none of them by ordering the air
    # war.** Nobody orders it: it is fought wherever two enemies' aircraft overlap and
    # cannot be declined, so the affordance is basing more air within reach -- which is an
    # ordinary `move_units` and the only thing a player can actually do about a sky.
    "sky_lost": "move_units",
    "supply_interdicted": "move_units",
    "sky_lapsing": "move_units",
    "agreement_expiring": "propose_treaty",
    "agreement_broken": "diplomatic_message",
    "arrears": "disband_units",
    # **The route is an agreement, so the answer is the one that reopens an agreement.**
    # A trade route is a `trade_agreement` commitment, and `propose_treaty` is already what
    # answers `agreement_expiring` above -- a commitment on a clock, answered by going back
    # to the other party with terms. Nothing new is added to the vocabulary for these:
    # `capabilities.action_types()` has no trade order, and inventing one here would put a
    # button in front of the player that no layer handles.
    "trade_stopping": "propose_treaty",
    "trade_stopped": "propose_treaty",
    "trade_stopping_more": "propose_treaty",
    "independence": "diplomatic_message",
    "order_refused": "freeform",
    "research_done": "",
}

#: What an alert kind neither table has ranked is worth. Both tables are asserted exhaustive,
#: so this is reached only by a kind added between a release and its test run -- and the
#: middle of the scale is the one answer that neither buries it nor floats it to the top.
_UNRANKED_URGENCY = 0.5

#: What a piece of news is worth on the urgency axis. **Low, and flat.** News is something
#: to know rather than a deadline; where a report genuinely does impose one, the condition
#: it created is selected on its own account and carries the urgency there. Giving news a
#: rising urgency would double-count every event that already has a standing condition.
_NEWS_URGENCY = 0.15

#: How much of the world a piece of news has to touch to be about the viewer. One when they
#: did it or it was done to them, and this when it involves somebody they are at war with,
#: bound to by treaty, or share a border with. Everything else is the world going on.
_NEWS_RELEVANCE_CONCERNED = 0.70
_NEWS_RELEVANCE_WORLD = 0.35


# ------------------------------------------------------------------------------- shapes


@dataclass(frozen=True, slots=True)
class NavTarget:
    """Where looking into this item takes the player.

    ``kind`` is one of :data:`NAV_KINDS` and ``target`` is what that kind's route takes -- a
    region id, a nation id, an event id, a commitment id. ``label`` is what to put on the
    control; it is a display string and nothing resolves against it.
    """

    kind: str = ""
    target: str = ""
    label: str = ""

    def __bool__(self) -> bool:
        return bool(self.kind and self.target)


@dataclass(frozen=True, slots=True)
class AttentionItem:
    """One thing worth this player's attention, with the arithmetic that put it there."""

    #: Which selector produced it. A key of :data:`ATTENTION_SOURCES`.
    source: str
    #: What it is, in one line. The one field a model may be asked to phrase better.
    subject: str
    #: **Why this made the list**, in the player's language and out of the numbers that
    #: decided it. Not a restatement of ``subject``: an item that cannot say why it is here
    #: is an item that should not be.
    reason_selected: str

    #: How large the consequence is if it is ignored. 0..1.
    severity: float = 0.0
    #: How soon it forces a decision. 0..1.
    urgency: float = 0.0
    #: How much it is about this viewer rather than about the world. 0..1.
    player_relevance: float = 0.0
    #: How recently it changed. 0..1, and zero is an ordinary answer -- a condition that has
    #: been true for a year is not news, and severity is what keeps it high anyway.
    novelty: float = 0.0

    #: Whether there is an order this player could give about it **this turn**. The engine's
    #: own verdict: :attr:`suggested_order_category`'s availability predicate from
    #: :mod:`atlas.engine.capabilities`, never a guess made here.
    actionable_now: bool = False

    #: What it is about. Empty where the kind has no such subject.
    nation_id: str = ""
    region_id: str = ""
    commitment_id: int = 0

    #: Where to send the player.
    nav_target: NavTarget = field(default_factory=NavTarget)
    #: The action type an order about this would have. A member of
    #: :func:`atlas.engine.capabilities.action_types`, or empty when there is nothing to
    #: order -- which is the honest answer for a piece of news.
    suggested_order_category: str = ""
    #: The events this item is built on, for a click through to the causal inspector and so
    #: a consumer can avoid showing the same event twice under two headings.
    source_event_ids: tuple[int, ...] = ()

    @property
    def score(self) -> float:
        return score_of(self)


def score_of(item: AttentionItem) -> float:
    """The ranking number, out of the four axes and nothing else.

    Rounded, so the tie-break below is reached by items that genuinely tie rather than being
    decided by the last bit of a float.
    """
    total = sum(WEIGHTS[axis] * float(getattr(item, axis)) for axis in AXES)
    if item.actionable_now:
        total += ACTIONABLE_BONUS
    return round(total, 6)


def _order(item: AttentionItem) -> tuple:
    """Highest first, with every remaining tie broken on something stable.

    A list that reordered itself between two openings of the same world would be unreadable
    however well it ranked, so the key bottoms out in ids rather than in anything derived.
    """
    return (-item.score, item.source, item.subject, item.region_id,
            item.nation_id, item.commitment_id)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _report_severity(source: str, band: float) -> float:
    """A salience band as a severity, discounted where the source is a report.

    See :data:`REPORT_SEVERITY`. A condition passes through untouched, which is why this
    takes the source rather than being applied at one call site: the standing conditions and
    the news come from different selectors and must not answer this differently.
    """
    return _clamp(band * (REPORT_SEVERITY if source in _REPORTED_SOURCES else 1.0))


def _novelty(turn: int, since: int | None) -> float:
    """How new a thing that started on ``since`` still is, at ``turn``.

    ``None`` -- a condition with no recorded onset -- is zero rather than a guess. It is
    not new; that is what the axis is for, and severity is what keeps it on the list.
    """
    if since is None:
        return 0.0
    return _clamp(1.0 - max(0, turn - int(since)) / NOVELTY_TURNS)


# ------------------------------------------------------------------------ shared reads


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    """Every nation's display name, once. Called for every source, so read once."""
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }


def _region_name(conn: sqlite3.Connection, region_id: str) -> str:
    row = conn.execute("SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
    return str(row["name"]) if row else region_id


def _nation_name(conn: sqlite3.Connection, nation_id: str) -> str:
    """One name, not the whole table. :func:`_names` is read once for a selector that walks
    every power; this is read from a label on a single item, and a great power's alert list
    can carry a dozen trade lanes at once."""
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id


def _enemies(conn: sqlite3.Connection, world_id: str, viewer: str) -> list[str]:
    return [
        str(row["nation_b"])
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND at_war = 1 ORDER BY nation_b", (world_id, viewer))
    ]


def _can_order(
    conn: sqlite3.Connection, world_id: str, viewer: str, action_type: str, target: str = ""
) -> bool:
    """Whether this player could give this order, **as the capability itself decides it**.

    Not a second opinion and not a guess: the same predicate the prompt menu and the
    workbench consult. A capability with no predicate is always available, which is what
    the catalogue means by an absent one.
    """
    capability = by_type(action_type)
    if capability is None:
        return False
    if capability.available is None:
        return True
    try:
        return bool(capability.available(conn, world_id, viewer, target))
    except sqlite3.Error:
        # Fail open, for the reason `capabilities.available_to` fails open: a world that
        # cannot answer must not quietly delete a mechanic the engine would still run.
        return True


def _occupied_share(
    conn: sqlite3.Connection, world_id: str, viewer: str, enemy: str
) -> tuple[int, int]:
    """``(how many of the viewer's home provinces this enemy holds, how many there are)``."""
    row = conn.execute(
        """
        SELECT SUM(CASE WHEN COALESCE(controller_nation_id, owner_nation_id) = ?
                        THEN 1 ELSE 0 END) AS theirs,
               COUNT(*) AS total
          FROM region
         WHERE world_id = ? AND kind <> 'ocean' AND home_nation_id = ?
        """, (enemy, world_id, viewer)).fetchone()
    if row is None:
        return 0, 0
    return int(row["theirs"] or 0), int(row["total"] or 0)


def _concerns(conn: sqlite3.Connection, world_id: str, viewer: str) -> set[str]:
    """Powers a piece of news about them is partly news about the viewer.

    Somebody they are fighting, somebody they have signed something with, or somebody whose
    ground touches theirs. Read rather than assumed, and read once per call.
    """
    found = {
        str(row[0])
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND (at_war = 1 OR treaty_status <> 'none')", (world_id, viewer))
    }
    found |= {
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT COALESCE(t.controller_nation_id, t.owner_nation_id)
              FROM region m
              JOIN region_adjacency a
                ON a.world_id = m.world_id AND (a.region_a = m.id OR a.region_b = m.id)
              JOIN region t
                ON t.id = CASE WHEN a.region_a = m.id THEN a.region_b ELSE a.region_a END
             WHERE m.world_id = ? AND m.kind <> 'ocean' AND t.kind <> 'ocean'
               AND COALESCE(m.controller_nation_id, m.owner_nation_id) = ?
            """, (world_id, viewer))
        if row[0]
    }
    found.discard(viewer)
    return found


# ---------------------------------------------------------------------------- selectors


def _from_standing_conditions(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int,
    watching: frozenset[str] = frozenset(),
) -> list[AttentionItem]:
    """Everything :func:`atlas.engine.alerts.alerts_for` already derives, given its axes.

    **The alert list is a consumer of this, not a rival to it**, so its selection is taken
    whole rather than reimplemented -- a capital beside an enemy is found in exactly one
    place in this program. What is added here is the other three axes: the alert knows how
    grave it is, and nothing in it says how soon it forces a hand, how new it is, or what
    order would answer it.
    """
    return [_standing_item(conn, world_id, viewer, turn, alert)
            for alert in alerts_for(conn, world_id, viewer, turn=turn, watching=watching)]


def _standing_item(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int, alert: Alert
) -> AttentionItem:
    order, nav = _standing_order_and_nav(conn, world_id, alert)
    since = _standing_onset(conn, alert)
    return AttentionItem(
        source=alert.kind,
        subject=alert.headline,
        reason_selected=_standing_reason(alert),
        severity=_report_severity(alert.kind, alert.salience / 3.0),
        urgency=_STANDING_URGENCY.get(alert.kind, _UNRANKED_URGENCY),
        player_relevance=1.0,
        novelty=_novelty(turn, since),
        actionable_now=bool(order) and _can_order(
            conn, world_id, viewer, order, alert.region_id),
        nation_id=alert.nation_id,
        region_id=alert.region_id,
        commitment_id=alert.commitment_id,
        nav_target=nav,
        suggested_order_category=order,
        source_event_ids=(alert.event_id,) if alert.event_id else (),
    )


def _standing_reason(alert: Alert) -> str:
    """Why a standing condition made the list, in the words it already had.

    The alert's own detail is the justification -- it was written to say why the headline
    matters -- and the band it sits in is named beside it, because that band *is* the number
    the severity axis is made of. A kind with no detail says what the kind is instead of
    saying it twice.
    """
    what = ATTENTION_SOURCES.get(alert.kind, alert.kind)
    band = f"salience {alert.salience} of 3"
    if alert.detail:
        return f"{alert.detail} ({what}, {band})"
    return f"{what[:1].upper()}{what[1:]} ({band})"


def _standing_order_and_nav(
    conn: sqlite3.Connection, world_id: str, alert: Alert
) -> tuple[str, NavTarget]:
    """The route to look at this condition, and the order from :data:`_STANDING_ORDER`.

    **Most specific first**, which is the order the four ids are tried in: a province is the
    thing itself, an agreement is the paper behind it, a government is the party to it, and
    an event is the moment it was written down. A trade lane reaches the last two -- it has
    no province, and only a lane somebody signed for has a commitment -- which is why
    :attr:`atlas.engine.alerts.Alert.nation_id` exists at all.
    """
    if alert.region_id:
        nav = NavTarget("region", alert.region_id, _region_name(conn, alert.region_id))
    elif alert.commitment_id:
        nav = NavTarget("treaty", str(alert.commitment_id),
                        _commitment_label(conn, alert.commitment_id))
    elif alert.nation_id:
        nav = NavTarget("nation", alert.nation_id, _nation_name(conn, alert.nation_id))
    elif alert.event_id:
        nav = NavTarget("event", str(alert.event_id), alert.headline[:60])
    else:
        nav = NavTarget()
    return _STANDING_ORDER.get(alert.kind, ""), nav


#: Kinds whose ``commitment_id`` is a route to click through to and **not** a date.
#:
#: An agreement's own turns are when it was made, resolved or breached, which is exactly
#: when `agreement_expiring` and `agreement_broken` started. A cargo is different: the
#: agreement it was opened under may have been signed years before the price crossed a
#: line, so reading its `created_turn` as the onset would file a lane that stopped this turn
#: as a year-old condition. The turn the cargo stopped is on the route, and nothing lifts it
#: this far -- so these say they do not know, which :func:`_novelty` already has an answer
#: for.
_COMMITMENT_IS_NOT_AN_ONSET: frozenset[str] = frozenset(
    {"trade_stopping", "trade_stopped", "trade_stopping_more"})


def _standing_onset(conn: sqlite3.Connection, alert: Alert) -> int | None:
    """When the thing behind a standing condition started, where the world records it."""
    if alert.event_id:
        row = conn.execute(
            "SELECT turn FROM event WHERE id = ?", (alert.event_id,)).fetchone()
        return int(row["turn"]) if row else None
    if alert.commitment_id and alert.kind not in _COMMITMENT_IS_NOT_AN_ONSET:
        row = conn.execute(
            "SELECT COALESCE(breached_turn, resolved_turn, created_turn) AS since"
            "  FROM commitment WHERE id = ?", (alert.commitment_id,)).fetchone()
        return int(row["since"]) if row and row["since"] is not None else None
    return None


def _commitment_label(conn: sqlite3.Connection, commitment_id: int) -> str:
    row = conn.execute(
        "SELECT kind FROM commitment WHERE id = ?", (commitment_id,)).fetchone()
    return str(row["kind"]).replace("_", " ") if row else str(commitment_id)


def _wars(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int, names: dict[str, str]
) -> list[AttentionItem]:
    """Every war this power is fighting, and every one of them that could be ended now.

    **Severity is the bargaining position, inverted.** A war is always serious; a war being
    lost is graver, and :func:`atlas.engine.leverage.leverage_between` is the engine's own
    answer to which of those this is -- so the number that ranks the war and the sentence
    explaining it come from one call, and the justification is literally the arithmetic.

    **Both kinds of item come out of one pass**, because they come out of the same two
    readings. A bargaining position is three world-wide aggregates; asking for it once to
    rank the war and again to decide whether it can be settled would double the most
    expensive thing this module does, and would leave two places able to disagree about
    which side is winning.
    """
    out: list[AttentionItem] = []
    for enemy in _enemies(conn, world_id, viewer):
        position = leverage_between(conn, world_id, viewer, enemy)
        effort = effort_between(conn, world_id, viewer, enemy)
        theirs, total = _occupied_share(conn, world_id, viewer, enemy)
        share = theirs / total if total else 0.0
        events = _war_events(conn, world_id, viewer, enemy)
        who = names.get(enemy, enemy)
        out.append(AttentionItem(
            source="war",
            subject=f"You are at war with {who}",
            reason_selected=(
                position.sentence(names)
                + (f" They hold {theirs} of your {total} home provinces."
                   if theirs else " None of your own ground is under their boot.")),
            severity=_clamp(0.60 - 0.30 * position.position),
            urgency=_clamp(0.35 + 0.65 * share),
            player_relevance=1.0,
            # The ledger's opening turn where there is a ledger, and the declaration that
            # started it where there is not -- a world played before `war_effort` existed
            # still knows when its wars began, and reading zero there would make every one
            # of them read as freshly declared.
            novelty=_novelty(
                turn, effort.opened_turn if effort else _declared_turn(conn, events)),
            actionable_now=_can_order(conn, world_id, viewer, "move_units"),
            nation_id=enemy,
            nav_target=NavTarget("nation", enemy, who),
            suggested_order_category="move_units",
            source_event_ids=events,
        ))
        settlement = _settlement(conn, world_id, viewer, turn, names, position, effort)
        if settlement is not None:
            out.append(settlement)
    return out


def _settlement(  # noqa: PLR0913 - a settlement is the pair, the turn, and both ledgers
    conn: sqlite3.Connection,
    world_id: str,
    viewer: str,
    turn: int,
    names: dict[str, str],
    position: Leverage,
    effort: WarEffort | None,
) -> AttentionItem | None:
    """This war if it could be ended now, and the two quite different reasons to end one.

    Either the position is strong enough that terms offered today are terms worth having, or
    the country is too tired to go on and the terms will only get worse. Both are
    ``sue_for_peace``; the reason says which, because a player told only "settle" cannot tell
    an opportunity from a warning.
    """
    enemy = position.enemy_id
    exhaustion = effort.exhaustion if effort else 0.0
    strong = position.position >= SETTLE_FROM_STRENGTH
    spent = exhaustion >= SETTLE_FROM_EXHAUSTION
    if not (strong or spent):
        return None
    who = names.get(enemy, enemy)
    if spent:
        reason = (
            f"This war has worn you to {effort.band} ({exhaustion:.0f}/100) and the terms "
            f"will not improve while it goes on. {position.sentence(names)}")
    else:
        reason = (
            f"{position.sentence(names)} Terms put to {who} now are terms they might take.")
    return AttentionItem(
        source="settlement",
        subject=f"The war with {who} could be settled",
        reason_selected=reason,
        severity=max(0.75 if spent else 0.0, 0.45 if strong else 0.0),
        urgency=_clamp(0.45 + 0.50 * exhaustion / 100.0),
        player_relevance=1.0,
        novelty=_novelty(turn, effort.last_turn if effort else None),
        actionable_now=_can_order(conn, world_id, viewer, "sue_for_peace", enemy),
        nation_id=enemy,
        nav_target=NavTarget("nation", enemy, who),
        suggested_order_category="sue_for_peace",
    )


def _war_events(
    conn: sqlite3.Connection, world_id: str, viewer: str, enemy: str
) -> tuple[int, ...]:
    """The declarations behind this war that the viewer may cite. Their own and the enemy's
    against them, which are public events by construction."""
    return tuple(
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM event WHERE world_id = ? AND kind = 'war_declared'"
            "   AND actor_nation_id IN (?, ?) ORDER BY id DESC LIMIT 2",
            (world_id, viewer, enemy))
    )


def _declared_turn(conn: sqlite3.Connection, events: tuple[int, ...]) -> int | None:
    """The turn the earliest of these declarations was written, or ``None`` for none."""
    if not events:
        return None
    row = conn.execute(
        "SELECT MIN(turn) AS turn FROM event WHERE id IN "
        f"({', '.join('?' * len(events))})", events).fetchone()
    return int(row["turn"]) if row and row["turn"] is not None else None


def _contests(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int, names: dict[str, str]
) -> list[AttentionItem]:
    """Ground somebody is pushing for, from the contests the engine is actually running.

    Both directions. Being pushed off ground is graver than failing to take some, which is
    why the two carry different severities out of the same pressure -- but a push of the
    player's own that is nearly home is the most time-sensitive thing on the map, and
    urgency is the same number for both.
    """
    threshold = _capture_threshold()
    out: list[AttentionItem] = []
    for row in conn.execute(
        "SELECT id, region_id, attacker_id, defender_id, pressure, opened_turn, last_turn"
        "  FROM contest WHERE world_id = ? AND (attacker_id = ? OR defender_id = ?)"
        " ORDER BY pressure DESC, id", (world_id, viewer, viewer)):
        pressure = float(row["pressure"]) / threshold if threshold else 0.0
        region = _region_name(conn, str(row["region_id"]))
        mine = str(row["attacker_id"]) == viewer
        other = str(row["defender_id"] if mine else row["attacker_id"])
        who = names.get(other, other)
        out.append(AttentionItem(
            source="border_contested",
            subject=(f"You are {pressure:.0%} of the way to taking {region}" if mine
                     else f"{who} is {pressure:.0%} of the way to taking {region}"),
            reason_selected=(
                f"A contest has been open on {region} since turn {int(row['opened_turn'])} "
                f"and moved again on turn {int(row['last_turn'])}; "
                + ("the ground falls to you when the pressure reaches all of it."
                   if mine else
                   f"{who} takes the ground when the pressure reaches all of it.")),
            severity=_clamp((0.45 + 0.45 * pressure) if not mine
                            else (0.25 + 0.35 * pressure)),
            urgency=_clamp(pressure),
            player_relevance=1.0,
            novelty=_novelty(turn, int(row["last_turn"])),
            actionable_now=_can_order(conn, world_id, viewer, "move_units"),
            nation_id=other,
            region_id=str(row["region_id"]),
            nav_target=NavTarget("region", str(row["region_id"]), region),
            suggested_order_category="move_units",
        ))
    return out


def _openings(
    conn: sqlite3.Connection, world_id: str, viewer: str, names: dict[str, str]
) -> list[AttentionItem]:
    """Enemy ground next to the player's with nothing of the enemy's standing on it.

    The mirror of the alert layer's ``front_unsupported``, pointed the other way: the same
    fact about a province is a danger when it is yours and an opening when it is theirs.
    An opportunity rather than a threat, so the severity is low and the urgency is not --
    an undefended province does not stay undefended.
    """
    rows = conn.execute(
        """
        SELECT t.id AS id, t.name AS name, t.development AS development,
               COALESCE(t.controller_nation_id, t.owner_nation_id) AS holder,
               MIN(m.name) AS from_name
          FROM region m
          JOIN region_adjacency a
            ON a.world_id = m.world_id AND (a.region_a = m.id OR a.region_b = m.id)
          JOIN region t
            ON t.id = CASE WHEN a.region_a = m.id THEN a.region_b ELSE a.region_a END
          JOIN relation rel
            ON rel.world_id = m.world_id AND rel.nation_a = ? AND rel.at_war = 1
           AND rel.nation_b = COALESCE(t.controller_nation_id, t.owner_nation_id)
         WHERE m.world_id = ? AND m.kind <> 'ocean' AND t.kind <> 'ocean'
           AND COALESCE(m.controller_nation_id, m.owner_nation_id) = ?
           AND NOT EXISTS (SELECT 1 FROM unit u WHERE u.region_id = t.id
                            AND u.nation_id = COALESCE(t.controller_nation_id,
                                                       t.owner_nation_id))
         GROUP BY t.id
         ORDER BY t.development DESC, t.id
        """, (viewer, world_id, viewer)).fetchall()
    can_move = _can_order(conn, world_id, viewer, "move_units")
    return [
        AttentionItem(
            source="opening",
            subject=f"{row['name']} is held by nobody in arms",
            reason_selected=(
                f"{names.get(str(row['holder']), str(row['holder']))} holds {row['name']}, "
                f"which borders {row['from_name']} where you stand, and no formation of "
                f"theirs is in it."
                + (f" It is the most developed of {len(rows)} such provinces on this front."
                   if len(rows) > 1 else "")),
            severity=0.30,
            urgency=0.55,
            player_relevance=0.90,
            novelty=0.0,
            actionable_now=can_move,
            nation_id=str(row["holder"]),
            region_id=str(row["id"]),
            nav_target=NavTarget("region", str(row["id"]), str(row["name"])),
            suggested_order_category="move_units",
        )
        for row in rows
    ]


def _unrest(
    conn: sqlite3.Connection, world_id: str, viewer: str, names: dict[str, str]
) -> list[AttentionItem]:
    """Foreign ground this power holds that its garrison is not holding down.

    :func:`atlas.engine.unrest.held_foreign_provinces` answers it already, worst first and
    with the two numbers the decision turns on -- what the province costs and what is
    standing on it. Only the ones with something left over after the garrison: a province
    held properly is not a call on anybody's attention.
    """
    restive = [entry for entry in held_foreign_provinces(conn, world_id, viewer)
               if entry["residual"] > 0.0]
    can_move = _can_order(conn, world_id, viewer, "move_units")
    return [
        AttentionItem(
            source="unrest",
            subject=f"{entry['name']} is not being held down",
            reason_selected=(
                f"You hold {entry['name']}, whose people are "
                f"{names.get(entry['home_nation_id'], entry['home_nation_id'])}'s, against "
                f"a garrison of {entry['garrison']:.1f} — {entry['residual']:.2f} of unrest "
                f"is left over, which bleeds stability every turn and can rise."
                + (f" The worst of {len(restive)} such provinces."
                   if len(restive) > 1 else "")),
            severity=_clamp(0.25 + entry["residual"] / 4.0),
            urgency=_clamp(entry["residual"] / UNREST_URGENT_RESIDUAL),
            player_relevance=1.0,
            novelty=0.0,
            actionable_now=can_move,
            nation_id=str(entry["home_nation_id"]),
            region_id=str(entry["region_id"]),
            nav_target=NavTarget("region", str(entry["region_id"]), str(entry["name"])),
            suggested_order_category="move_units",
        )
        for entry in restive
    ]


def _economy(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int
) -> list[AttentionItem]:
    """The economic thresholds this turn crossed, if any.

    **Selected elsewhere, on purpose.** :mod:`atlas.engine.dependency` is the module that
    knows what a dependency is, what a runway is and what the settlement will actually do,
    and the lens a player opens from one of these items is built out of the same reading --
    so the warning and the page it leads to cannot come to disagree about a share or a
    month. This selector is the seam and holds no arithmetic of its own.

    The import is local because that module builds :class:`AttentionItem` values and would
    otherwise be a cycle. Nothing here reaches a provider, and a failure to read the
    economy is not a reason to lose the war on the rest of the list -- an old save with no
    resource table simply has no economic crossings to report.
    """
    from .dependency import warnings_for

    try:
        return warnings_for(conn, world_id, viewer, turn=turn)
    except sqlite3.Error:
        return []


def _offers(
    conn: sqlite3.Connection, world_id: str, viewer: str, turn: int
) -> list[AttentionItem]:
    """Agreements standing open on this player's answer.

    A pending commitment the viewer is party to and has not accepted is the one kind of
    opportunity that expires by being ignored -- rule DIP-004 will not let it become active
    while any party's acceptance is missing, so nothing happens until somebody decides.
    """
    return [
        AttentionItem(
            source="agreement_offered",
            subject=f"{str(row['kind']).replace('_', ' ')} is waiting on your answer",
            reason_selected=(
                f"Proposed on turn {int(row['created_turn'])} and still unanswered: "
                f"{str(row['condition_text'] or '')[:160]}"),
            severity=0.35,
            urgency=0.70,
            player_relevance=1.0,
            novelty=_novelty(turn, int(row["created_turn"])),
            actionable_now=_can_order(conn, world_id, viewer, "propose_treaty"),
            commitment_id=int(row["id"]),
            nav_target=NavTarget("treaty", str(row["id"]),
                          str(row["kind"]).replace("_", " ")),
            suggested_order_category="propose_treaty",
            source_event_ids=((int(row["source_event_id"]),)
                              if row["source_event_id"] else ()),
        )
        for row in conn.execute(
            """
            SELECT c.id, c.kind, c.condition_text, c.created_turn, c.source_event_id
              FROM commitment c
              JOIN commitment_nation m ON m.commitment_id = c.id AND m.nation_id = ?
             WHERE c.world_id = ? AND c.status = 'pending' AND m.accepted_turn IS NULL
             ORDER BY c.id
            """, (viewer, world_id))
    ]


def _news(
    conn: sqlite3.Connection,
    world_id: str,
    viewer: str,
    turn: int,
    names: dict[str, str],
    already: set[int],
) -> list[AttentionItem]:
    """What happened recently that this viewer knows about, gravest first.

    **The only route to the event log is** :func:`atlas.state.visibility.known_events`, so
    the scoping is the dispatch panel's own -- public, or the viewer's own act, or an
    explicit grant. Events already cited by another item are dropped rather than repeated:
    a war that is on the list as a war does not also need to be on it as the news that it
    was declared.
    """
    concerns = _concerns(conn, world_id, viewer)
    out: list[AttentionItem] = []
    for event in known_events(
        conn, viewer, since_turn=max(0, turn - NEWS_RECENT_TURNS),
        limit=NEWS_LIMIT * 4, rank_by_salience=True,
    ):
        if int(event["id"]) in already:
            continue
        actor = str(event["actor"] or "")
        salience = salience_of(event)
        if actor == viewer:
            relevance = 1.0
        elif actor in concerns:
            relevance = _NEWS_RELEVANCE_CONCERNED
        else:
            relevance = _NEWS_RELEVANCE_WORLD
        who = names.get(actor, actor) if actor else "somebody"
        out.append(AttentionItem(
            source="news",
            subject=str(event["text"] or "")[:160],
            reason_selected=(
                f"A {str(event['kind']).replace('_', ' ')} on turn {int(event['turn'])} "
                f"({event['date']}), salience {salience} of 3, and "
                + ("you are the one who did it." if actor == viewer
                   else f"{who} is a power you are at war with, bound to, or share a "
                        "border with." if actor in concerns
                   else "it is the world going on around you.")),
            severity=_report_severity("news", salience / 3.0),
            urgency=_NEWS_URGENCY,
            player_relevance=relevance,
            novelty=_novelty(turn, int(event["turn"])),
            actionable_now=False,
            nation_id=actor,
            nav_target=NavTarget("event", str(event["id"]), str(event["text"] or "")[:60]),
            source_event_ids=(int(event["id"]),),
        ))
        if len(out) >= NEWS_LIMIT:
            break
    return out


# ------------------------------------------------------------------------- the substrate


def attention_for(
    conn: sqlite3.Connection,
    world_id: str,
    viewer: str | None,
    *,
    turn: int,
    limit: int = 0,
    watching: frozenset[str] = frozenset(),
) -> list[AttentionItem]:
    """Everything worth this viewer's attention right now, best claim first. Read-only.

    ``limit`` of zero is everything; a positive one is the top N, which is what a surface
    with a fixed number of slots wants. Cutting here rather than at the call site means
    every surface cuts the same ranked list and a "top three" is the same three everywhere.

    ``watching`` is the provinces the player has pinned, passed through to the standing
    conditions untouched. It is the fourth of the four things that make a theatre's air war
    this player's air war -- see :func:`atlas.engine.alerts._air_alerts` -- and it is passed
    rather than read because the watchlist lives beside the save rather than inside it.

    A viewer of ``None`` -- a spectator with no country -- gets nothing, for the reason
    :func:`atlas.engine.alerts.alerts_for` gives it nothing: there is no *this player* for
    the relevance axis to be about, and a list of things that matter to nobody in particular
    is the undifferentiated feed this module exists to replace.
    """
    if not viewer:
        return []

    names = _names(conn, world_id)
    found: list[AttentionItem] = [
        *_from_standing_conditions(conn, world_id, viewer, turn, watching),
        *_wars(conn, world_id, viewer, turn, names),
        *_contests(conn, world_id, viewer, turn, names),
        *_openings(conn, world_id, viewer, names),
        *_unrest(conn, world_id, viewer, names),
        *_offers(conn, world_id, viewer, turn),
        *_economy(conn, world_id, viewer, turn),
    ]
    cited = {event_id for item in found for event_id in item.source_event_ids}
    found.extend(_news(conn, world_id, viewer, turn, names, cited))
    found.sort(key=_order)
    kept = _within_budget(found)
    return kept[:limit] if limit > 0 else kept


def _within_budget(ranked: list[AttentionItem]) -> list[AttentionItem]:
    """Let no one source own the list, and say so where it does not fit.

    Applied after the ranking rather than inside each selector, so what survives a kind's
    budget is that kind's *best* claim on the player's attention rather than whichever row
    the database happened to return first.

    **What was left out is said, not swallowed.** A budget that silently truncated would
    make the list read as "this is everything", which is the failure a cap of this sort
    always has; the survivor that carries the note is the highest-ranked one, because a
    surface showing only the top item must still be told there were twenty.
    """
    seen: dict[str, int] = {}
    dropped: dict[str, int] = {}
    kept: list[AttentionItem] = []
    first: dict[str, int] = {}
    for item in ranked:
        budget = SOURCE_BUDGET.get(item.source, SOURCE_BUDGET_DEFAULT)
        seen[item.source] = seen.get(item.source, 0) + 1
        if seen[item.source] > budget:
            dropped[item.source] = dropped.get(item.source, 0) + 1
            continue
        first.setdefault(item.source, len(kept))
        kept.append(item)
    for source, count in dropped.items():
        at = first[source]
        kept[at] = replace(kept[at], reason_selected=(
            f"{kept[at].reason_selected} "
            f"({count} more of this kind "
            f"{'is' if count == 1 else 'are'} not shown.)"))
    return kept


def _capture_threshold() -> float:
    """:data:`atlas.sim.resolve.CAPTURE_THRESHOLD`, read rather than restated, so the
    urgency of a push and the turn it lands on cannot come to disagree.

    Imported inside the function because :mod:`atlas.sim.resolve` imports a good deal of the
    engine, and this module is meant to be cheap to import from a panel.
    """
    from ..sim.resolve import CAPTURE_THRESHOLD

    return float(CAPTURE_THRESHOLD)
