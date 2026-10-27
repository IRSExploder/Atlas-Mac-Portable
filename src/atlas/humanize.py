"""How a stored number becomes something a player reads.

**The engine's figures and the player's figures are not the same figures.** A nation's
economy is a float that has been through fourteen turns of compounding and is genuinely
``47.269999999999996``; that is the right thing to keep in the database and the wrong thing
to put on a top bar. Every screen in this program used to format its own, so the same
economy appeared as ``47.3`` on the meter, ``47.27`` in the trace, and ``47.269999999999996``
in a tooltip nobody had written -- three answers to *how rich is Russia* on one screen.

This module is the one answer. It holds no state, imports nothing from the engine or from
Qt, and every function in it is a pure ``value -> str``, which is what lets the map layer and
the widget layer share it without either one depending on the other.

**Three rules it exists to enforce.**

*Round the noise.* A player deciding whether to go to war is not choosing between an economy
of 47.2 and one of 47.3. Precision is kept only where a single point is a different decision
-- a victory margin, a causal ledger -- and dropped everywhere else.

*Never destroy the number.* Everything here has an :func:`exact` counterpart that goes in a
tooltip or an inspector, so the rounding is a layer over the figure rather than a
replacement for it. A screen that rounds without offering the original has lied to somebody
who needed the original.

*Say what the scale is.* ``-0.24`` is not a quantity, it is a coordinate in a system the
player was never shown. A regard, a share and a multiple are three different kinds of thing
and each gets read out in its own units.

The band tables are the one place in here that makes a judgement, and each is anchored to a
threshold the engine already acts on rather than to a feeling -- see :data:`BANDS`.
"""

from __future__ import annotations

from math import isfinite

__all__ = [
    "BANDS",
    "REGARD_EDGES",
    "band",
    "band_and_figure",
    "count",
    "duration",
    "exact",
    "figure",
    "movement",
    "percent",
    "points",
    "ratio",
    "regard",
    "regard_word",
    "signed",
    "strength",
]

# --------------------------------------------------------------------------- quantities

#: Below this a movement is not a movement, it is the last decimal place of a float
#: wobbling. The same floor :class:`atlas.ui.widgets.StatBar` has always applied; it lives
#: here now so the meter, the trace and the cast all agree about what *steady* means.
_STILL = 0.05


def _clean(value: float | int | None) -> float:
    """A number that can be formatted, or zero. Never a ``NaN`` on the glass."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if isfinite(number) else 0.0


def figure(value: float | int | None, *, places: int = 0, unit: str = "") -> str:
    """A quantity as a player reads it: rounded, grouped, and never ``-0``.

    ``47.269999999999996`` is ``47``; ``9876.5`` is ``9,876``; ``-0.2`` at zero places is
    ``0`` rather than the ``-0`` that six top-bar meters were printing. Ties go to the even
    neighbour, which is what Python's own rounding does and what every other figure in this
    program has always done -- a second rule here would make the same value round two ways
    depending on which screen it was on.
    """
    number = round(_clean(value), places)
    if number == 0:
        number = 0.0     # kills the sign on negative zero, which reads as a bug
    text = f"{number:,.{places}f}"
    return f"{text} {unit}" if unit else text


def count(value: float | int | None) -> str:
    """A whole number of things. Grouped, because ``4951 regions`` is a password."""
    return f"{int(round(_clean(value))):,}"


def signed(value: float | int | None, *, places: int = 0) -> str:
    """A figure whose direction is the point of it: ``+70``, ``-100``, ``0``.

    Zero carries no sign. ``+0`` claims a rise that did not happen and ``-0`` is not a
    number at all; both were on the top bar, and both are what this exists to stop.
    """
    number = round(_clean(value), places)
    if number == 0:
        return f"{0:.{places}f}"
    return f"{number:+,.{places}f}"


def movement(value: float | int | None, *, floor: float = _STILL) -> str:
    """What a delta says beside a meter. ``steady``, ``+3``, ``-0.2``.

    **Adaptive, because a fixed precision cannot tell nothing from nearly nothing.** At
    zero places a move of 0.2 prints as ``+0``, which a reader takes for a rounding artefact
    and ignores -- so a stability slide that has begun looks exactly like a stability that
    has not moved. Anything under a whole point keeps its first decimal; anything over it
    does not need one.
    """
    number = _clean(value)
    if abs(number) < floor:
        return "steady"
    return signed(number, places=0 if abs(number) >= 1.0 else 1)


def exact(value: float | int | None) -> str:
    """The stored figure, with the float noise taken off. What a tooltip carries.

    ``47.269999999999996`` is ``47.27`` -- not a rounding, a rendering: the trailing
    ``9999999996`` is an artefact of binary floating point and not a fact about Russia.
    Four places, because that is the most any figure in this program is meaningful to and
    twelve is how you print a number nobody can read.
    """
    # Rounded first and then shed of its sign bit (``-0.0 + 0.0`` is ``0.0``), so that a
    # figure a hair below zero prints as ``0`` and not the ``-0`` this module exists to stop.
    number = round(_clean(value), 4) + 0.0
    text = f"{number:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def strength(value: float | int | None) -> str:
    """A force's weight, on a map counter or in a stack list.

    A whole number once there is more than a token there, and one decimal below that --
    a garrison of 0.27 rounded to ``0`` would read as no garrison at all. A strength that
    lands on a whole number keeps no decimal either way: ``6.0`` on a counter is a
    trailing zero doing the work of a fact.
    """
    number = round(_clean(value), 0 if abs(_clean(value)) >= 10 else 1)
    return figure(number, places=0 if float(number).is_integer() else 1)


# --------------------------------------------------------------------------- proportions


def percent(fraction: float | int | None, *, places: int | None = None) -> str:
    """A 0..1 share as a percentage. ``0.2902`` is ``29%``; ``0.025`` is ``2.5%``.

    ``places=None`` picks: a share under a tenth keeps a decimal, because the difference
    between 2.5% and 3% of the world's ground is several countries.
    """
    number = _clean(fraction) * 100.0
    if places is None:
        places = 1 if abs(number) < 10 else 0
    return f"{figure(number, places=places)}%"


def ratio(value: float | int | None, *, places: int = 2) -> str:
    """A multiple of somebody else's figure: ``0.45x``, ``2x``, ``1.1x``.

    Trailing zeros go: ``2.00x`` is a spreadsheet and ``2x`` is a sentence.
    """
    # ``+ 0.0`` drops the sign of a negative that rounds to zero: ``-0x`` is not a multiple.
    text = f"{round(_clean(value), places) + 0.0:,.{places}f}".rstrip("0").rstrip(".")
    return f"{text or '0'}x"


def points(fraction: float | int | None, *, places: int = 1) -> str:
    """A 0..1 score as a mark out of a hundred. ``0.2902`` is ``29.0``.

    The weights in :data:`atlas.engine.scoring.WEIGHTS` sum to 1.0 *so that a total reads
    as a percentage of a theoretical maximum nobody reaches* -- the scoring module says so
    itself. This is that reading, and it is the only one on which fourth-decimal places
    stop being the difference between two nations and start being a mark a reader can hold
    two of in their head.
    """
    return figure(_clean(fraction) * 100.0, places=places)


# --------------------------------------------------------------------------- durations


def duration(seconds: float | int | None) -> str:
    """A span of real time in the units a person keeps time in. ``84.3`` is ``1m 24s``.

    Nobody converts seconds to minutes in their head while waiting for a turn, and a bar
    that says ``84.3s`` is asking them to. Sub-minute spans keep whole seconds; the tenth
    of a second was never a fact anybody used.
    """
    total = max(0.0, _clean(seconds))
    if total < 1:
        return "under a second"
    whole = int(round(total))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        minutes, rest = divmod(whole, 60)
        return f"{minutes}m {rest}s" if rest else f"{minutes}m"
    hours, rest = divmod(whole, 3600)
    minutes = rest // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


# --------------------------------------------------------------------------- bands

#: **A word for a figure, per field.** Read as ``(below this, called this)``, the shape
#: :data:`atlas.engine.constituencies.BANDS` already uses.
#:
#: Every edge is anchored rather than chosen. Stability and legitimacy break at **50**
#: because :data:`atlas.engine.leaders._DANGER_BELOW` is 50 -- below it a government is at
#: risk of being removed and above it, in the engine's own words, it is merely governed. The
#: outer edges are drawn at the quarters of the same scale. Treasury and manpower are banded
#: against the ceilings :data:`atlas.engine.snapshot.METERS` draws their bars against, which
#: are explicitly *plausible upper bounds* rather than maxima -- so the word describes where
#: a country sits on the bar the player is already looking at, and nothing else.
#:
#: **These name a value; they never replace it.** Every caller shows the word beside the
#: number, because a band on its own is a second opinion the player cannot check.
BANDS: dict[str, tuple[tuple[float, str], ...]] = {
    "military_strength": (
        (5.0, "negligible"), (20.0, "slight"), (40.0, "modest"),
        (60.0, "capable"), (80.0, "strong"), (float("inf"), "formidable"),
    ),
    "economy": (
        (5.0, "collapsed"), (25.0, "weak"), (50.0, "modest"),
        (75.0, "solid"), (100.0, "strong"), (float("inf"), "commanding"),
    ),
    "stability": (
        (20.0, "in crisis"), (35.0, "shaky"), (50.0, "strained"),
        (70.0, "steady"), (85.0, "firm"), (float("inf"), "rock solid"),
    ),
    "legitimacy": (
        (20.0, "rejected"), (35.0, "disputed"), (50.0, "contested"),
        (70.0, "accepted"), (85.0, "settled"), (float("inf"), "unquestioned"),
    ),
    # Fractions of the drawn ceiling, not absolutes: a treasury has no maximum and the
    # only honest scale for it is the one the bar beside it is using.
    "treasury": (
        (0.10, "empty"), (0.25, "thin"), (0.50, "adequate"),
        (0.75, "healthy"), (float("inf"), "flush"),
    ),
    "manpower": (
        (0.10, "exhausted"), (0.25, "stretched"), (0.50, "adequate"),
        (0.75, "deep"), (float("inf"), "abundant"),
    ),
}

#: Which meters are banded on their fraction of the drawn ceiling rather than on the value.
_BY_FRACTION = frozenset({"treasury", "manpower"})


def band(key: str, value: float | int | None, *, ceiling: float = 100.0) -> str:
    """The word for one figure, or ``""`` for a field with no vocabulary."""
    table = BANDS.get(key)
    if not table:
        return ""
    number = _clean(value)
    if key in _BY_FRACTION:
        number = number / ceiling if ceiling else 0.0
    for edge, name in table:
        if number < edge:
            return name
    return table[-1][1]


def band_and_figure(key: str, value: float | int | None, *, ceiling: float = 100.0,
                    places: int = 0) -> str:
    """``47 — modest``. The number, then the word, never the word alone."""
    shown = figure(value, places=places)
    word = band(key, value, ceiling=ceiling)
    return f"{shown} — {word}" if word else shown


# --------------------------------------------------------------------------- regard

#: Where one power's regard for another stops being one thing and starts being another, on
#: the -100..100 scale ``relation.score`` is stored on, as ``(name, the edge it starts at)``.
#: For the vocabulary rather than for the lookup -- a panel that wants to print the scale.
#:
#: **The edges are not symmetric and that is deliberate**: 25 is *friendly* and -25 is
#: *cold*, so the neutral band is open at the bottom and closed at the top. It is copied
#: from :meth:`atlas.engine.cast.CastMember.standing`, which is the authority, and the two
#: are held together by test rather than by hand -- see
#: ``test_a_number_a_player_can_read``.
REGARD_EDGES: tuple[tuple[str, float], ...] = (
    ("hostile", -100.0), ("cold", -60.0), ("neutral", -25.0),
    ("friendly", 25.0), ("warm", 60.0),
)


def regard_word(score: float | int | None, *, at_war: bool = False,
                treaty: str = "none") -> str:
    """The relationship in a word, because ``-37`` is a number, not a feeling.

    War first, then whatever is signed, then the figure -- a defence pact with somebody you
    dislike is still a defence pact, and that is the fact that decides what happens next.

    Written as a chain rather than driven off :data:`REGARD_EDGES` because the bands do not
    close the same way at both ends, and a table that had to carry which end each edge
    belongs to would be harder to check against the engine's five lines than these five are.
    """
    if at_war:
        return "at war"
    if treaty and treaty != "none":
        return treaty.replace("_", " ")
    number = _clean(score)
    if number >= 60:
        return "warm"
    if number >= 25:
        return "friendly"
    if number <= -60:
        return "hostile"
    if number <= -25:
        return "cold"
    return "neutral"


def regard(score: float | int | None, *, at_war: bool = False,
           treaty: str = "none") -> str:
    """A regard read out in full, for a tooltip: ``-80 on a scale of -100 to 100 — hostile``.

    The scale is named because it is the whole difficulty. A player shown ``-0.24`` cannot
    tell a rounding error from an enemy; shown ``-24 of 100`` they can tell it is a quarter
    of the way to one.
    """
    word = regard_word(score, at_war=at_war, treaty=treaty)
    return f"{signed(score)} on a scale of -100 to 100 — {word}"
