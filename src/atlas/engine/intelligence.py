"""What a nation's intelligence service can tell it, and how wrong it is.

**A model has never been allowed to invent what is secretly true, and this is the layer
that makes that a mechanism rather than a rule.** Until now a rival's strength reached a
briefing through `visibility._band`, which took the *true* figure, divided it by the
viewer's own and named the ratio: coarse, but exact -- five buckets computed from ground
truth, identical for every observer, and free. A great power with a worldwide service and a
landlocked minor with no attaches abroad saw the same thing, and both saw something true.

So there are two halves here and the split is the whole design:

*The engine owns the truth.* Nothing in this module is proposed, written or influenced by a
model. Networks are engine state, estimates are computed from real state, and the noise is
drawn from the world's seeded stream -- so a rewind reproduces the same wrong guess, which
is the difference between fog and a coin flip.

*The model receives the estimate and never the value.* :func:`estimate_of` returns a band
and a confidence; the true figure is not a field on it, not in its ``describe()``, and not
recoverable from it. The band is deliberately **not centred on the truth** -- a symmetric
interval hands the real number to anybody who takes the midpoint, which would have made the
whole layer decorative. The centre is displaced by a seeded bias first, and the band is
opened around *that*.

**Networks are per (viewer, target), and that is the interesting part.** Intelligence is not
a stat a country has; it is a relationship it has with another country. Britain reading
Berlin well and Tokyo badly is the ordinary case, and a single "intelligence" number on the
nation row could not say it.

**This is the foundation only.** Networks exist, they gate accuracy, and estimates reach a
briefing. Nothing here discovers a hidden programme, degrades a rival's service, attributes
a covert act or blows an operation -- those read this table and are the next bricks, kept
separate so the numbers can be argued with before anything is built on them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.resolve import uncertain
from ..sim.rng import derive
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .pacing import compounded, span_of

__all__ = [
    "EspionageReport",
    "run_espionage",
    "ARSENAL",
    "ARSENAL_DECEPTION_ERROR",
    "ARSENAL_MAX_ERROR",
    "ARSENAL_MIN_ERROR",
    "BANDS",
    "SHARING",
    "COUNTER_PER_TURN",
    "DECEPTION_AT",
    "SMALLEST_SPREAD",
    "TIERS",
    "Estimate",
    "IntelNetwork",
    "assess_networks",
    "band_of",
    "discovery_odds",
    "discovery_tier",
    "estimate_of",
    "estimate_with",
    "error_span",
    "estimates_for",
    "magnitude_of",
    "invest_counterintel",
    "network_between",
    "network_id_for",
    "share_intelligence",
    "sharing_terms",
    "networks_of",
    "redacted",
]

#: What a service is worth against one target, by upper bound. Named, for the same reason
#: war weariness and constituency standing are: "patchy" is what a briefing should say, and
#: 38.2 is not a thing a foreign ministry has ever said out loud.
BANDS: tuple[tuple[float, str], ...] = (
    (15.0, "blind"),
    (35.0, "patchy"),
    (60.0, "workable"),
    (82.0, "good"),
    (100.1, "excellent"),
)

# --------------------------------------------------------------------------- the dials

#: How wrong a completely blind service is, as a share of the true figure. **A third**, so
#: a blind estimate of an army of 30 spans roughly 20 to 40 -- wrong enough to invade the
#: wrong country, and not so wrong that the briefing becomes noise a model learns to skip.
MAX_ERROR = 0.34

#: And how wrong the best service in the world still is. Never zero: an intelligence layer
#: whose top end hands over the exact number has spent a whole subsystem re-deriving the
#: leak it was built to close.
MIN_ERROR = 0.05

#: **The floor the band is opened around, so that nothing is not perfectly observable.**
#: The noise is multiplicative, so a true figure of zero produced a band of 0-0 whatever the
#: network -- "they have no army" was the one fact every service in the world knew exactly,
#: and disarmament was the only perfectly visible act in the game. Spreading against at
#: least this much means a blind service reports "somewhere between nothing and a little"
#: and an excellent one still rounds to nothing, which is the graded answer both deserve.
SMALLEST_SPREAD = 8.0

# ------------------------------------------------------------- and the foggiest subject

#: The one subject with its own fog, named so that everything reading it agrees which.
ARSENAL = "arsenal"

#: **How wrong a blind service is about a warhead count.** Larger than the whole figure, and
#: that is deliberate: at this width the band runs from nothing to several times the truth,
#: which is not an estimate anybody can plan a war on. TUNABLE.
#:
#: The importance-times-secrecy logic the exposure layer already runs on says a warhead count
#: should be the hardest number on the map to pin: it is the highest-stakes figure a state
#: holds and the one it guards hardest. An army marches in public, an economy trades in
#: public, a factory is photographed from the air -- a warhead sits in a hole in the ground
#: in a place nobody publishes.
ARSENAL_MAX_ERROR = 1.10

#: And how wrong the best service in the world still is about one. **Three and a half times
#: the floor for the other subjects**, so even an excellent network reads an arsenal of two
#: hundred as somewhere around a hundred and fifty to two hundred and fifty -- reasonably
#: tight, which is the most anybody gets, and never exact.
ARSENAL_MIN_ERROR = 0.18

#: What a fed warhead count looks like. Chosen so the label it carries is the one a
#: genuinely *good* network's arsenal read carries -- the same relationship
#: `DECEPTION_ERROR` has to the other three subjects, and the same reason: a lie that is
#: tighter than anything a real service could produce is a lie that announces itself.
ARSENAL_DECEPTION_ERROR = 0.30

#: Below these confidences an arsenal is reported in words rather than numbers. A range of
#: `0-380` is a number a staff will average; *a handful to hundreds* is what the briefing
#: actually knows, and it cannot be averaged.
VAGUE_BELOW: frozenset[str] = frozenset({"blind", "patchy"})

#: The words, by upper bound. Coarse on purpose -- these are the sizes a foreign ministry
#: argues about before it has anything better.
MAGNITUDES: tuple[tuple[float, str], ...] = (
    (1.0, "none"),
    (10.0, "a handful"),
    (40.0, "dozens"),
    (150.0, "many dozens"),
    (1000.0, "hundreds"),
    (float("inf"), "thousands"),
)


#: Where a service settles with nobody working on it. Not zero -- every state reads its
#: neighbours' newspapers -- and not high, because this is what a country knows without
#: trying.
RESTING = 22.0

#: How much of the distance to the resting point a month closes. Slower than a
#: constituency's mood on purpose: a network is built out of people and takes years to
#: grow and years to wither, which is what makes it worth investing in at all.
INERTIA = 0.03

# ------------------------------------------------------------------------ the deception

#: **Where a service stops merely blinding a rival and starts lying to it.** TUNABLE.
#:
#: Below this, counterintelligence widens the band: the rival knows less and *knows* it knows
#: less, because its own reported confidence falls with it. Above it, the defender has enough
#: control of what leaves the country to choose what the rival believes -- so the band comes
#: back tight and the *centre* is moved.
#:
#: That is the interesting half. A rival being fed a confident lie sees an estimate exactly as
#: narrow and exactly as well-attributed as a true one, and there is nothing anywhere in its
#: view that distinguishes the two. It cannot be careful about this; it can only invest in a
#: better service and find out.
DECEPTION_AT = 55.0

#: How far the fed centre is moved, as a share of the true figure, at full deception. Large
#: enough to be a decision made on a wrong premise rather than a rounding error: at 0.45 an
#: army of forty is reported as somewhere near twenty-two or fifty-eight.
DECEPTION_SHIFT = 0.45

#: And what the band looks like while it is lying. Deliberately *tight* -- a good service's
#: width -- because a wide lie is a lie that announces itself.
DECEPTION_ERROR = 0.09


#: What proximity is worth at seed. A neighbour is watched, an enemy is watched harder, and
#: somebody you have signed something with lets you in a little.
_FROM_NEIGHBOUR = 14.0
_FROM_WAR = 18.0
_FROM_TREATY = 8.0

#: And what having a large, rich state behind you is worth. Measured against the target's
#: economy rather than in absolute terms: a great power watching a great power is not the
#: same problem as a great power watching a minor.
_FROM_REACH = 16.0


# ------------------------------------------------------------------------- discovery

#: **What a service brings back, by how good it is.** Ordered worst to best.
#:
#: The grading is the whole of I1. Espionage used to be a flat 45% coin and an all-or-nothing
#: answer: either the full text of a hidden event or "your agents learned nothing useful",
#: with a landlocked minor and a great power drawing on the same odds for the same prize.
#: Now both the chance and the *resolution* come off the network.
TIERS: tuple[str, ...] = ("nothing", "existence", "nature", "detail", "full")

#: Which tier each network band buys. **Only `excellent` buys the real thing**: at every
#: other tier the service comes back with an engine-authored sentence about a hidden event
#: it could not read, and the event itself stays hidden. That is what makes the lower tiers
#: honest -- a patchy network learns *that* there is something, not a redacted version of
#: what it says.
_TIER_BY_BAND: dict[str, str] = {
    "blind": "nothing",
    "patchy": "existence",
    "workable": "nature",
    "good": "detail",
    "excellent": "full",
}

#: The odds a service finds anything at all, blind to excellent. **A blind service is not
#: barred, it is merely bad**: the tier table already gives it nothing to find, and a
#: separate hard gate would be the same claim made twice.
_ODDS_FLOOR = 0.15
_ODDS_CEILING = 0.75

#: **What a hidden event of each kind sounds like from outside.**
#:
#: Three sentences per kind -- existence, nature, detail -- each derived from the real
#: event's kind and, at `detail`, from what the engine can safely name about it. Nothing
#: here is invented: the words describe a hidden event that genuinely happened, at a
#: resolution the network earned. A kind absent from this table falls back to `_UNKNOWN`,
#: which is the honest answer for a secret nobody has written a vocabulary for yet.
#:
#: Templates rather than a stored report row, deliberately: the grading needs no schema,
#: rewinds with the event that carries it, and cannot drift out of step with the thing it
#: describes -- because it is regenerated from that thing every time.
_REDACTIONS: dict[str, tuple[str, str, str]] = {
    "discovery": (
        "is working on something it has not declared",
        "is running a major technical programme it has not declared",
        "is running a major technical programme it has not declared, and it began in "
        "{when}",
    ),
    "construction": (
        "is building something it has not declared",
        "has a major industrial programme under way it has not declared",
        "has a major industrial programme under way it has not declared, laid down in "
        "{when}",
    ),
    "diplomatic_message": (
        "has been talking to somebody it has not named",
        "has been in talks with another power it has not named",
        "has been in talks with {other}",
    ),
    "commitment_made": (
        "has come to an understanding it has not published",
        "has signed something with a power it has not named",
        "has signed something with {other}",
    ),
    "treaty_signed": (
        "has come to an understanding it has not published",
        "has signed something with a power it has not named",
        "has signed something with {other}",
    ),
    "order_issued": (
        "has given its forces orders it has not announced",
        "has given its forces standing orders it has not announced",
        "has given its forces standing orders it has not announced, dated {when}",
    ),
    "espionage": (
        "has people somewhere it should not",
        "is running an intelligence operation of its own",
        "is running an intelligence operation of its own, opened in {when}",
    ),
    "sabotage": (
        "has had a hand in something",
        "is running an operation against somebody",
        "is running an operation against somebody, opened in {when}",
    ),
}

#: For a secret whose kind nobody has written words for. Deliberately vague rather than
#: absent: a service that found *something* and cannot say what has still found something.
_UNKNOWN = (
    "is keeping something from the rest of us",
    "is keeping something material from the rest of us",
    "is keeping something material from the rest of us, dating from {when}",
)


def discovery_tier(strength: float) -> str:
    """How much a service of this strength can bring back."""
    return _TIER_BY_BAND[band_of(strength)]


def discovery_odds(strength: float) -> float:
    """The chance it brings back anything at all."""
    quality = max(0.0, min(1.0, strength / 100.0))
    return round(_ODDS_FLOOR + (_ODDS_CEILING - _ODDS_FLOOR) * quality, 4)


def redacted(kind: str, tier: str, *, when: str = "", other: str = "") -> str:
    """What a service at ``tier`` can say about a hidden event of ``kind``.

    **Derived from a real hidden event every time, never stored.** The sentence is a
    function of the event's kind and of what the engine chooses to let through at this
    resolution, so it cannot describe something that did not happen and cannot drift out of
    step with the thing it describes.
    """
    if tier in ("nothing", "full"):
        return ""
    existence, nature, detail = _REDACTIONS.get(kind, _UNKNOWN)
    if tier == "existence":
        return existence
    if tier == "nature":
        return nature
    return detail.format(when=when or "a while ago", other=other or "a power it has not named")


def band_of(strength: float) -> str:
    for ceiling, name in BANDS:
        if strength < ceiling:
            return name
    return BANDS[-1][1]


@dataclass(frozen=True, slots=True)
class IntelNetwork:
    """What one nation can find out about one other, and what is being done to stop it.

    **One row, both ends of one contest.** ``strength`` is what ``nation_id`` has built
    inside ``target_id``; ``counter_intel`` is what ``target_id`` is doing about it. Keeping
    them together is what let counterintelligence land as a column rather than a table, and
    it is also the thing to get right when reading this: the defender on this row is the
    *target*.
    """

    nation_id: str
    target_id: str
    strength: float = RESTING
    last_turn: int = 0
    because: str = ""
    #: What the target is actively doing to blind this network. Zero on every world written
    #: before schema 32, so an old save behaves exactly as it did.
    counter_intel: float = 0.0

    @property
    def band(self) -> str:
        """What this service is worth *after* the other side's work against it."""
        return band_of(self.effective)

    @property
    def effective(self) -> float:
        """**What the network is actually worth on the ground.**

        Counterintelligence does not delete agents from a ledger; it makes the ones who are
        there useless. So the raw investment survives -- a service driven to nothing by a
        sweep recovers when the sweep stops -- and everything that reads a network reads
        this instead.
        """
        return max(0.0, self.strength - self.counter_intel)

    @property
    def quality(self) -> float:
        """0..1, and the only thing the estimate machinery reads off a network."""
        return max(0.0, min(1.0, self.effective / 100.0))

    def describe(self, target_name: str = "") -> str:
        return (f"{target_name or self.target_id}: **{self.band}** "
                f"({self.strength:.0f}/100)")


@dataclass(frozen=True, slots=True)
class Estimate:
    """What a service believes, and how sure it is.

    **There is deliberately no ``true_value`` field.** Not because a caller could not be
    trusted with one, but because its existence is the leak -- the same argument
    `atlas.state.visibility` makes about an unscoped reader. Every consumer of this class
    gets the band and the confidence, and there is nothing else to get.
    """

    #: What is being estimated, in the words a briefing would use.
    subject: str
    low: float
    high: float
    #: The network band this was produced at, so a reader can weigh it.
    confidence: str
    #: What the figure is measured in, for the sentence.
    unit: str = ""

    def phrase(self) -> str:
        """What this estimate says, without the confidence label.

        **An arsenal read by a bad service comes back in words, and that is not decoration.**
        A range of `0-380` is a pair of numbers a staff will average into a plan; *a handful
        to hundreds* is what the briefing actually knows and cannot be averaged into
        anything. The band underneath is unchanged -- this is how it is *said*.
        """
        if self.subject == ARSENAL and self.confidence in VAGUE_BELOW:
            low, high = magnitude_of(self.low), magnitude_of(self.high)
            return f"{self.subject}: {low}" if low == high else (
                f"{self.subject}: {low} to {high}")
        return (f"{self.subject}: {_figure(self.low)}-{_figure(self.high)}"
                + (f" {self.unit}" if self.unit else ""))

    def describe(self) -> str:
        return f"{self.phrase()} ({self.confidence})"


def network_id_for(nation_id: str, target_id: str) -> str:
    """Derived, not autoincremented, so a network survives a rewind with its identity."""
    return f"int_{nation_id}_{target_id}"


# --------------------------------------------------------------------------- reading


def network_between(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target_id: str
) -> IntelNetwork:
    """What ``nation_id`` can find out about ``target_id``.

    **A pair with no row yet reads its seeded default**, derived from the two countries
    rather than assigned, so a world that has never had a turn carries no rows and answers
    the same as one that has. Same lazy scheme the constituency store uses, and for the
    same reason: 243 nations against 243 targets is fifty-nine thousand rows of nobody
    watching anybody.
    """
    row = conn.execute(
        "SELECT strength, last_turn, because, counter_intel FROM intel_network"
        " WHERE world_id = ? AND nation_id = ? AND target_nation_id = ?",
        (world_id, nation_id, target_id)).fetchone()
    if row is not None:
        return IntelNetwork(
            nation_id=nation_id, target_id=target_id, strength=float(row["strength"]),
            last_turn=int(row["last_turn"]), because=str(row["because"] or ""),
            counter_intel=float(row["counter_intel"] or 0.0))
    return IntelNetwork(
        nation_id=nation_id, target_id=target_id,
        strength=_seeded(conn, world_id, nation_id, target_id))


def networks_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[IntelNetwork]:
    """Every network this nation has that is worth mentioning, best first."""
    targets = [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 AND id <> ?"
            " ORDER BY id", (world_id, nation_id))
    ]
    found = [network_between(conn, world_id, nation_id, target) for target in targets]
    return sorted(found, key=lambda n: (-n.strength, n.target_id))


def _seeded(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target_id: str
) -> float:
    """What a service is worth before anybody has worked on it.

    Derived from the relationship rather than authored per scenario, so every era and every
    mod gets it for nothing: you watch your neighbours, you watch your enemies harder, an
    ally lets you in a little, and a rich state can afford attaches everywhere.
    """
    row = conn.execute(
        """
        SELECT (SELECT economy FROM nation WHERE id = ?)            AS mine,
               (SELECT economy FROM nation WHERE id = ?)            AS theirs,
               COALESCE((SELECT at_war FROM relation
                          WHERE world_id = ? AND nation_a = ? AND nation_b = ?), 0) AS war,
               COALESCE((SELECT treaty_status FROM relation
                          WHERE world_id = ? AND nation_a = ? AND nation_b = ?),
                        'none')                                     AS treaty,
               EXISTS (SELECT 1 FROM region_adjacency a
                         JOIN region ra ON ra.id = a.region_a
                         JOIN region rb ON rb.id = a.region_b
                        WHERE (ra.owner_nation_id = ? AND rb.owner_nation_id = ?)
                           OR (rb.owner_nation_id = ? AND ra.owner_nation_id = ?))
                                                                    AS neighbour
        """,
        (nation_id, target_id, world_id, nation_id, target_id,
         world_id, nation_id, target_id,
         nation_id, target_id, target_id, nation_id)).fetchone()
    if row is None:
        return RESTING
    mine = float(row["mine"] or 0.0)
    theirs = max(1.0, float(row["theirs"] or 0.0))
    strength = RESTING
    strength += _FROM_REACH * min(2.0, mine / theirs) / 2.0
    if int(row["neighbour"] or 0):
        strength += _FROM_NEIGHBOUR
    if int(row["war"] or 0):
        strength += _FROM_WAR
    if str(row["treaty"] or "none") != "none":
        strength += _FROM_TREATY
    return round(max(0.0, min(100.0, strength)), 3)


# ------------------------------------------------------------------------- the estimate


def error_span(subject: str) -> tuple[float, float]:
    """How wrong this kind of figure can be, best case and worst, by what it is.

    One place, so the estimate, the deception and the confidence label all scale off the
    same pair -- which is what keeps a fed arsenal count indistinguishable from an earned
    one *within its own subject*, rather than only within the three that share a scale.
    """
    if subject == ARSENAL:
        return ARSENAL_MIN_ERROR, ARSENAL_MAX_ERROR
    return MIN_ERROR, MAX_ERROR


def magnitude_of(count: float) -> str:
    """A warhead count in the words a briefing would use when it does not know."""
    return next(word for bound, word in MAGNITUDES if count < bound)


def estimate_of(  # noqa: PLR0913 - an estimate is who is asking, about whom, of what
    conn: sqlite3.Connection, world_id: str, *, viewer: str, target: str,
    subject: str, value: float, unit: str = "", turn: int = 0,
) -> Estimate:
    """Turn one true figure into what ``viewer``'s service believes about it.

    **The truth goes in and does not come out.** The band is displaced before it is opened:
    a seeded bias moves the centre off the real value, and the interval is drawn around the
    displaced centre. A band centred on the truth would hand the real number to anyone who
    averaged the ends, which is the failure mode this whole layer exists to avoid and is
    exactly what a naive ``value * (1 ± error)`` does.

    Seeded on ``(world, turn, viewer, target, subject)`` and never on call order, so the
    same question asked twice in a turn gets the same answer, a rewind reproduces the same
    wrong guess, and two different questions are wrong in unrelated ways.
    """
    return estimate_with(
        network_between(conn, world_id, viewer, target), seed=_world_seed(conn, world_id),
        world_id=world_id, turn=turn, viewer=viewer, target=target, subject=subject,
        value=value, unit=unit)


def estimate_with(  # noqa: PLR0913 - the estimate's inputs, without a connection
    network: IntelNetwork, *, seed: int, world_id: str, turn: int, viewer: str,
    target: str, subject: str, value: float, unit: str = "",
) -> Estimate:
    """:func:`estimate_of` with the network and the world's seed already in hand.

    **The same arithmetic, callable without a database**, which is what lets the map show
    a foreign army as the player's service sees it: the scene is a pure function of a view
    read once, and asking the connection again per counter is what that design exists to
    avoid. :func:`estimate_of` calls this, so there is one fog and two doors onto it.
    """
    # **Which fog, by what is being asked about.** Identical to what it always was for the
    # three figures a foreign ministry argues about; three times as wide for the one it
    # cannot get at.
    floor, ceiling = error_span(subject)
    error = floor + (ceiling - floor) * (1.0 - network.quality)
    rng = derive(seed, world_id, turn, f"intel:{viewer}:{target}:{subject}")
    # Displaced, then opened. The bias is drawn over the same width as the band, so a
    # believable estimate is wrong in a believable direction rather than merely fuzzy.
    # Clamped at nothing, because every figure this is asked about is a count or a size and
    # none of them can be negative. A no-op for the three narrow subjects -- their widest
    # error is a third, so a displaced centre never crosses zero -- and load-bearing for an
    # arsenal, whose blind error exceeds the whole figure.
    centre = max(0.0, value * (1.0 + rng.uniform(-error, error)))

    # **And if the target has enough control of what leaves it, the centre is chosen.**
    # Not widened -- moved, with the band drawn tight around the new place. Nothing about
    # the returned estimate says which of the two happened, which is the point: a fed
    # answer and an earned one are indistinguishable from inside.
    if network.counter_intel >= DECEPTION_AT:
        reach = min(1.0, (network.counter_intel - DECEPTION_AT) / (100.0 - DECEPTION_AT))
        away = DECEPTION_SHIFT * (0.4 + 0.6 * reach)
        lie = derive(seed, world_id, turn, f"deceit:{target}:{viewer}:{subject}")
        centre = max(0.0, value * (1.0 + (away if lie.random() < 0.5 else -away)))
        # **And a nuclear power can lie about its warheads exactly as it lies about its
        # army.** Wider in absolute terms, because a warhead count that came back tighter
        # than any real service could read one would be the tell -- so the fed band is the
        # width of a *good* network's arsenal read, and carries that label.
        error = ARSENAL_DECEPTION_ERROR if subject == ARSENAL else DECEPTION_ERROR

    spread = error * max(abs(centre), SMALLEST_SPREAD)
    return Estimate(
        subject=subject,
        low=max(0.0, _round_to(centre - spread, value)),
        high=max(0.0, _round_to(centre + spread, value)),
        # **The confidence a deceived viewer is told matches the band it is being shown.**
        # Reporting the network's real quality here would be the tell: a tight range labelled
        # *blind* is a contradiction a reader could act on, and the whole mechanic is that
        # there is nothing to act on.
        confidence=band_of(100.0 - error / ceiling * 100.0),
        unit=unit)


def _pooled(  # noqa: PLR0913 - a pooled estimate is who, about whom, of what, and when
    conn: sqlite3.Connection, world_id: str, *, viewer: str, target: str, subject: str,
    value: float, unit: str, turn: int, mine: Estimate,
) -> Estimate:
    """The best of what this nation believes and what its allies have agreed to tell it.

    **A shared estimate is still an estimate, and that is the load-bearing guarantee of the
    whole clause.** What crosses is the ally's *belief* -- recomputed here by asking
    `estimate_of` with the ally as the viewer -- so an ally with a superb service hands over
    a narrow band and an ally with a bad one hands over a wide one. There is no path by
    which a treaty transfers the true figure, because nobody on either side of it has the
    true figure to transfer.

    Better means *narrower*: a tighter band is worth more than a wider one, and ties keep
    what the nation worked out itself.
    """
    best = mine
    for sharer, covers in sharing_terms(conn, world_id, viewer):
        if sharer in (viewer, target):
            continue
        if covers and covers != target:
            continue
        theirs = estimate_of(conn, world_id, viewer=sharer, target=target,
                             subject=subject, value=value, unit=unit, turn=turn)
        if (theirs.high - theirs.low) < (best.high - best.low):
            best = theirs
    return best


def estimates_for(
    conn: sqlite3.Connection, world_id: str, *, viewer: str, target: str, turn: int = 0
) -> list[Estimate]:
    """What ``viewer``'s service can say about ``target`` this turn.

    The figures a foreign ministry actually argues about, and no others: how big the army
    is, how rich the country is, how much it can build at once -- and, once anybody on the
    map has one, how many warheads it holds. Deliberately not a dump of the nation row -- an
    estimate of every column would be the leak back again, only noisier.

    **The arsenal line appears for every country or for none, and never only for the ones
    that have warheads.** A subject that showed up exactly when the target was armed would
    hand over the fact of the programme for free, which is a worse leak than the count: the
    fog would be decorative and the presence of the line would be the answer. So the gate is
    the *world* -- has the nuclear age arrived anywhere -- and the target's own figure is
    whatever it is, including nothing, spread over the widest fog in the module.
    """
    row = conn.execute(
        "SELECT n.military_strength, n.economy, n.industry,"
        "       COALESCE((SELECT a.warheads FROM arsenal a"
        "                  WHERE a.world_id = n.world_id AND a.nation_id = n.id), 0)"
        "         AS warheads"
        "  FROM nation n WHERE n.world_id = ? AND n.id = ?",
        (world_id, target)).fetchone()
    if row is None:
        return []
    return [
        _pooled(
            conn, world_id, viewer=viewer, target=target, subject=subject,
            value=float(row[column] or 0.0), unit=unit, turn=turn,
            mine=estimate_of(conn, world_id, viewer=viewer, target=target, turn=turn,
                             subject=subject, value=float(row[column] or 0.0), unit=unit))
        for subject, column, unit in _subjects(conn, world_id)
    ]


def _subjects(
    conn: sqlite3.Connection, world_id: str
) -> tuple[tuple[str, str, str], ...]:
    """What can be estimated in this world, and out of which column."""
    always: tuple[tuple[str, str, str], ...] = (
        ("army", "military_strength", ""),
        ("economy", "economy", ""),
        ("industry", "industry", ""),
    )
    armed = conn.execute(
        "SELECT 1 FROM arsenal WHERE world_id = ? AND warheads > 0 LIMIT 1",
        (world_id,)).fetchone()
    return always + ((ARSENAL, "warheads", "warhead(s)"),) if armed else always


def _round_to(bound: float, reference: float) -> float:
    """Round an end of the band, so the pair cannot be solved back to the true figure.

    Coarser for larger figures, because a briefing that reports an economy as 41.7-58.3 is
    claiming a precision no service has ever had.
    """
    step = 1.0 if abs(reference) < 40 else 5.0
    return round(round(bound / step) * step, 2)


def _figure(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 10 or value == int(value) else f"{value:.1f}"


def _world_seed(conn: sqlite3.Connection, world_id: str) -> int:
    row = conn.execute(
        "SELECT rng_seed FROM world WHERE id = ?", (world_id,)).fetchone()
    return int(row["rng_seed"]) if row else 0


# ---------------------------------------------------------------- what allies pool

#: The clause kinds `intel_term` understands. One, for now, and named rather than implied so
#: the next one is a table edit.
SHARING = "share_estimates"


def sharing_terms(
    conn: sqlite3.Connection, world_id: str, recipient: str
) -> list[tuple[str, str]]:
    """``(sharer, subject)`` for every live clause feeding this nation. ``""`` means all.

    **Only live ones.** The clause hangs off a `commitment`, so a treaty that has lapsed,
    been broken or not yet been accepted shares nothing -- which is what makes an
    intelligence-sharing arrangement a thing that can be taken away, and why it needed no
    expiry machinery of its own.
    """
    try:
        return [
            (str(row["sharer_id"]), str(row["subject_id"] or ""))
            for row in conn.execute(
                """
                SELECT t.sharer_id, t.subject_id
                  FROM intel_term t JOIN commitment c ON c.id = t.commitment_id
                 WHERE t.world_id = ? AND t.recipient_id = ? AND c.status = 'active'
                 ORDER BY t.commitment_id, t.seq
                """,
                (world_id, recipient))
        ]
    except sqlite3.OperationalError:
        # A world written before schema 34 shares nothing, which is what it always did.
        return []


def share_intelligence(  # noqa: PLR0913 - a clause is its parties, its subject and its seq
    conn: sqlite3.Connection, world_id: str, *, commitment_id: int, sharer: str,
    recipient: str, subject: str = "", seq: int = 0, turn: int, event_id: int,
    scope: Visibility = Visibility.BILATERAL,
) -> Diff:
    """The diff that writes one sharing clause. Returned rather than applied, so it rides in
    the same batch as the agreement it belongs to -- a clause that outlived a refused treaty
    would be an alliance nobody signed."""
    return Diff(
        "intel_term", f"it_{commitment_id}_{seq}", "__insert__", DiffOp.INSERT,
        {
            "world_id": world_id, "commitment_id": commitment_id, "seq": seq,
            "sharer_id": sharer, "recipient_id": recipient, "subject_id": subject,
        },
        reason_event_id=event_id, visibility_scope=scope)


# ------------------------------------------------------------------------ the investment

#: What one turn of work adds, and what lapses when nobody keeps it up. TUNABLE.
#: Asymmetric on purpose: a sweep takes months to mount and weeks to fall apart, which is
#: what makes counterintelligence a standing commitment rather than a button.
COUNTER_PER_TURN = 12.0

#: **Halved from 0.10, and the decay is the dial that was wrong rather than the other two.**
#:
#: Measured against the live PT46 run, where no nation came near deceiving anybody. Turns of
#: investment needed to cross `DECEPTION_AT`, by how often a nation actually gets to spend
#: its one undertaking on a sweep:
#:
#: ===============  ==============  ==============
#: investing        at decay 0.10   at decay 0.05
#: ===============  ==============  ==============
#: every turn       6               6
#: every other      20              12
#: one turn in 3    never           21
#: one turn in 4    never           never
#: ===============  ==============  ==============
#:
#: The committed case is *unchanged* -- deception still costs six turns of doing nothing
#: else, which is the property worth keeping. What changed is that a power returning to it
#: among its other business can now get there at all: at 0.10 the fixed point of investing
#: every other turn is 60 and of one-in-three is 40, so anything short of obsession decayed
#: faster than it built and the mechanic was unreachable in play rather than merely
#: expensive. A model has one undertaking a turn and many things it wants.
#:
#: Still forgiving of nothing: from 60, two turns of neglect drop a service back under the
#: line, and one turn in four never gets there at all.
#:
#: `DECEPTION_AT` and `COUNTER_PER_TURN` are deliberately untouched -- the first is the
#: boundary between blinding a rival and choosing what it believes, and the second is what
#: one turn of work is worth. Neither was the thing that made this unreachable.
COUNTER_DECAY = 0.05


def invest_counterintel(  # noqa: PLR0913 - who is defending, against whom, when
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, against: str,
    turn: int, date: str,
) -> str:
    """Work at blinding one rival's service inside your own country.

    The model orders it; the engine decides what it is worth. Written on the row that
    already describes the contest -- ``(against -> nation_id)`` -- because the defender on
    that row is the target, which is what "keyed the other way" means.
    """
    if nation_id == against:
        return ""
    network = network_between(conn, world_id, against, nation_id)
    after = round(min(100.0, network.counter_intel + COUNTER_PER_TURN), 3)
    if after <= network.counter_intel:
        return ""

    names = {nation: _name(conn, nation) for nation in (nation_id, against)}
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(f"{names.get(nation_id, nation_id)} tightens up against "
              f"{names.get(against, against)}'s people."),
        # **Secret, and visible to the defender alone.** A sweep the rival could read about
        # is a sweep it would simply wait out -- and the deception above only works on
        # somebody who does not know they are being handled.
        visibility=Visibility.SECRET, visible_to=(nation_id,))

    row_id = network_id_for(against, nation_id)
    exists = conn.execute(
        "SELECT 1 FROM intel_network WHERE id = ?", (row_id,)).fetchone() is not None
    diff = (
        Diff("intel_network", row_id, "counter_intel", DiffOp.SET, after,
             reason_event_id=event_id, visibility_scope=Visibility.SECRET)
        if exists else
        Diff("intel_network", row_id, "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id, "nation_id": against,
                 "target_nation_id": nation_id, "strength": network.strength,
                 "opened_turn": turn, "last_turn": turn,
                 "because": "somebody started looking", "counter_intel": after,
             },
             reason_event_id=event_id, visibility_scope=Visibility.SECRET))
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=(diff,)))
    if not outcome.applied:
        return ""
    return (f"{names.get(nation_id, nation_id)} is working against "
            f"{names.get(against, against)}'s service at home")


# ------------------------------------------------------------------------ the assessment


def assess_networks(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> list[str]:
    """Let every network drift toward what the relationship alone would sustain.

    **Drift only, in this brick.** Nothing here builds a network up or tears one down --
    that is the espionage and counterintelligence layers, which are the next two -- so what
    this does is let a service that was raised for a war it is no longer fighting settle
    back toward what a country knows without trying. Written as diffs, so a rewind puts the
    intelligence picture back exactly as it stood.
    """
    nations = [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
            (world_id,))
    ]
    existing = {
        (str(row["nation_id"]), str(row["target_nation_id"])):
            (float(row["strength"]), float(row["counter_intel"] or 0.0))
        for row in conn.execute(
            "SELECT nation_id, target_nation_id, strength, counter_intel FROM intel_network"
            " WHERE world_id = ?", (world_id,))
    }
    if not existing:
        # Nothing has been built, so there is nothing to decay. The seeded defaults are
        # already what the relationship sustains.
        return []

    span = span_of(days)
    diffs: list[Diff] = []
    told: list[str] = []
    for (viewer, target), (strength, counter) in sorted(existing.items()):
        if viewer not in nations or target not in nations:
            continue
        floor = _seeded(conn, world_id, viewer, target)
        # **Compounded over the span, not multiplied by it.** A converging pull written as
        # `x + (floor - x) * rate * span` is only the intended curve at span 1: past
        # `1 / INERTIA` it overshoots the floor it is converging toward, and further still
        # it leaves the legal range entirely. At a three-year jump a service of 70 landed
        # on 26 against a floor of 30 -- through it, on the way to negative -- and the
        # out-of-bounds diff then had the *whole batch* refused, so a long jump silently
        # applied no drift and no counterintelligence lapse at all. Found by the scout.
        #
        # Identical at span 1, which is every 30-day turn: `(1 - r)**1` is `1 - r`. No
        # shipped digest and no recorded run moves.
        after = round(strength + (floor - strength) * compounded(INERTIA, span), 3)
        # A sweep nobody is keeping up lapses, and faster than a network withers: people go
        # back to their ordinary jobs long before an agent network dissolves.
        lapsed = round(max(0.0, counter * (1.0 - compounded(COUNTER_DECAY, span))), 3)
        if abs(after - strength) >= 0.01:
            diffs.append(
                Diff("intel_network", network_id_for(viewer, target), "strength",
                     DiffOp.SET, after))
        if abs(lapsed - counter) >= 0.01:
            diffs.append(
                Diff("intel_network", network_id_for(viewer, target), "counter_intel",
                     DiffOp.SET, lapsed))
        was, now = max(0.0, strength - counter), max(0.0, after - lapsed)
        if band_of(now) != band_of(was):
            told.append(
                f"{_name(conn, viewer)}'s service in {_name(conn, target)} is now "
                f"{band_of(now)}")
    if not diffs:
        return []

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE,
        text=(told[0] if len(told) == 1 else
              f"{len(diffs)} intelligence network(s) changed what they could see."),
        # **Secret, and INFO-002 is the reason it has to be.** What one service can see is
        # not a thing the world observes, and a public event here would let a diff about a
        # network be cited by anybody.
        visibility=Visibility.SECRET)
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                     reason_event_id=event_id, visibility_scope=Visibility.SECRET)
                for d in diffs)))
    return told if outcome.applied else []


# ------------------------------------------------------------------ running a service


@dataclass(slots=True)
class EspionageReport:
    """What one attempt to find something out achieved.

    Deliberately not `OrderEffects`: this is the *mechanic*, and the two callers that drive
    it -- a player's typed order and an autonomous nation's undertaking -- carry different
    result objects. Returning the finding lets each render it in its own words while the
    thing that happened is decided once.
    """

    applied: bool = False
    headlines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _counterparty(conn: sqlite3.Connection, event_id: int, besides: str) -> str:
    """Who else was in the room, for the tier that is allowed to name them."""
    row = conn.execute(
        "SELECT n.name FROM event_visibility v JOIN nation n ON n.id = v.nation_id"
        " WHERE v.event_id = ? AND v.nation_id <> ? ORDER BY v.nation_id LIMIT 1",
        (event_id, besides)).fetchone()
    return str(row["name"]) if row else ""


def _partial(  # noqa: PLR0913 - a report is who asked, about whom, and how well
    conn: sqlite3.Connection, world_id: str, turn: int, date: str, *, nation_id: str,
    nation_name: str, target: str, target_name: str, hidden, tier: str,
    report: EspionageReport,
) -> None:
    """What a service short of the top tier comes back with: a sentence, and no grant.

    **The hidden event stays hidden.** This writes one new secret event, visible to the
    asking nation alone, whose text the engine composes from the real event's kind and from
    what the tier lets through. Nothing about the original is copied, so there is no way for
    a partial reveal to leak more than it was graded for -- and no `event_visibility` row is
    written at all, which is the mechanical difference between this path and the full one.

    Deliberately not a diff. There is no state here: the report *is* the event, so a rewind
    that unwrites the turn unwrites the discovery with it and nothing else has to know.
    """
    when = str(hidden["date"] or "")[:7]
    said = redacted(str(hidden["kind"]), tier, when=when or "some time ago",
                    other=_counterparty(conn, hidden["id"], target) or "")
    record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ESPIONAGE, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=f"{nation_name}'s people report that {target_name} {said}.",
        visibility=Visibility.SECRET, visible_to=(nation_id,))
    conn.commit()
    report.applied = True
    report.headlines.append(
        f"{nation_name}'s people in {target_name} came back with something short of the "
        f"papers: {said}.")


def run_espionage(  # noqa: PLR0913 - who is asking, about whom, and when
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, target: str,
    turn: int, date: str,
) -> EspionageReport:
    """Find out something a rival did not tell you.

    **One implementation, two doors.** A player types an espionage order and an autonomous
    nation attempts the `espionage` undertaking; both arrive here, so the odds, the grading
    and the grant cannot drift between the seat a person plays and the seat a model does.
    Splitting them was how the mechanic came to be reachable by the player alone -- see
    `test_a_mechanism_a_model_cannot_reach_is_not_shipped`.

    Intelligence is not new state. It is *permission to see an event that already
    happened* -- a row in ``event_visibility`` -- which is precisely how every secret in
    ATLAS has always worked, and why a hidden atomic programme needs no hiding mechanism of
    its own. Rule INFO-002 then does the rest: a diff is never more observable than the
    event that caused it, so learning the event is learning everything that followed from it.

    Whether the attempt succeeds is drawn from the world's seeded stream, so a rewind
    replays the same intelligence failure.

    **Both the odds and the resolution come off the network** (I1): `discovery_odds` scales
    the chance from 15% to 75%, and `discovery_tier` decides what comes back, from nothing
    at *blind* to the report itself at *excellent*. **Only the top tier grants**, and that is
    what keeps the lower ones honest: below it the service returns a sentence the engine
    wrote *about* a hidden event it could not read, and the event stays hidden.
    """
    report = EspionageReport()
    nation_name = _name(conn, nation_id)
    if not target or target == nation_id:
        report.problems.append(
            f"{nation_name}'s agents were not told whose secrets to go after")
        return report

    # **Which secret this attempt looks at, and why it is an offset rather than a link.**
    #
    # A partial reveal deliberately writes no `event_visibility` row -- that is what keeps
    # it partial -- so the "not already seen" clause alone would hand every attempt in a
    # turn the same secret, and four attempts would be four reports of one thing. Marking
    # the hidden event as reported would need a link from a report the actor can read to an
    # event it cannot, and `causality` follows `event_cause` in both directions without a
    # viewer, which is exactly the leak the constituency layer was caught by.
    #
    # So the attempt is offset by how many reports this actor has already filed this turn.
    # Nothing links to the secret, nothing is stored, and a *failed* attempt writes no
    # report, so the offset does not advance and the retry is the same futile draw.
    filed = int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ? AND turn = ? AND kind = ?"
        "   AND actor_nation_id = ?",
        (world_id, turn, EventKind.ESPIONAGE.value, nation_id)).fetchone()[0])
    hidden = conn.execute(
        """
        SELECT e.id, e.turn, e.date, e.kind, e.text FROM event e
         WHERE e.world_id = ? AND e.actor_nation_id = ? AND e.visibility <> 'public'
           AND NOT EXISTS (SELECT 1 FROM event_visibility v
                            WHERE v.event_id = e.id AND v.nation_id = ?)
         ORDER BY e.id LIMIT 1 OFFSET ?
        """,
        (world_id, target, nation_id, filed),
    ).fetchone()
    target_name = _name(conn, target)

    network = network_between(conn, world_id, nation_id, target)
    tier = discovery_tier(network.strength)
    seed = conn.execute(
        "SELECT rng_seed FROM world WHERE id = ?", (world_id,)).fetchone()["rng_seed"]

    # Even a perfect network finds nothing when there is nothing to find. The report being
    # gone after is part of the key: without it two attempts in a turn drew the same number
    # and the second inherited the first's answer, so a nation that got in once emptied the
    # whole safe for free.
    if hidden is None or tier == "nothing" or not uncertain(
        int(seed), world_id, turn,
        f"espionage:{nation_id}:{target}:{hidden['id']}",
        discovery_odds(network.strength),
    ):
        report.problems.append(
            f"{nation_name}'s agents learned nothing useful about {target_name}"
            + (f" -- its network there is {network.band}" if tier == "nothing" else ""))
        return report

    if tier != "full":
        _partial(conn, world_id, turn, date, nation_id=nation_id,
                 nation_name=nation_name, target=target, target_name=target_name,
                 hidden=hidden, tier=tier, report=report)
        return report

    # **The grant is a diff, and this was a reproduced leak until it was.** A rewind used to
    # delete the espionage action and the espionage event and leave the grant standing: the
    # spy kept the secret it learned in a turn that no longer happened. Found by the Codex
    # review of phase 2; the table is fingerprinted and this write goes through the pipeline
    # like any other. The scope is SECRET because the cause is -- INFO-002 refuses a change
    # more observable than its reason, and an intelligence coup that announced itself would
    # not be one.
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ESPIONAGE, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=f"{nation_name}'s intelligence service obtains a report on {target_name}.",
        visibility=Visibility.SECRET, visible_to=(nation_id,))
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(
                Diff("event_visibility", f"{hidden['id']}|{nation_id}", "__insert__",
                     DiffOp.INSERT, {}, reason_event_id=event_id,
                     visibility_scope=Visibility.SECRET),
            ),
        ),
    )
    if not outcome.applied:
        report.problems.append(
            f"{nation_name}'s agents could not file their report: {outcome.reason}")
        return report
    conn.commit()
    report.applied = True
    report.headlines.append(
        f"{nation_name}'s people in {target_name} got hold of the papers themselves.")
    report.headlines.append(
        f"{nation_name} learns what {target_name} was doing on turn {hidden['turn']}.")
    return report


def _name(conn: sqlite3.Connection, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    return str(row["name"]) if row else nation_id
