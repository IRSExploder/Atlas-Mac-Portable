"""Making a turn's effects depend on how long the turn was.

**The player chooses how much time a turn covers** -- a week, a month, a season or a year --
and for a long time nothing in the simulation knew. Every system ticked exactly once per
jump, so the same Russia-Ukraine war took 0.2 years of game time on weekly turns and 14.0
on yearly ones: an eighty-one-fold spread on a choice the player makes for pacing reasons,
not strategic ones. A turn count is not a duration, and the calendar was decoration.

**The month is the reference unit.** Every constant in :mod:`atlas.engine.budget`,
:mod:`atlas.engine.unrest`, :mod:`atlas.engine.production` and :mod:`atlas.sim.resolve` was
calibrated against 30-day turns -- "eleven turns to overrun a comparable power" means
eleven months and nobody wrote it down. Defining ``span = days / 30`` makes every formula
here the identity at the default, so the whole change is a no-op for a monthly game and no
existing balance work is invalidated.

**There are three kinds of per-turn quantity and they scale differently.** Getting the kind
wrong is silent, which is the entire reason this module exists rather than a bare
multiplication at each call site.

*Flows* accumulate linearly: a year collects twelve months of tax and pays twelve months of
upkeep. :func:`over` -- multiply.

*Rates* are expressed as "close this fraction of the gap each turn": economy growth toward
its industrial ceiling, manpower recovery, legitimacy drift. These **compound**. A monthly
0.06 is 0.52 over a year, not 0.72; and multiplying any rate above 0.084 by twelve gives a
figure over 1.0, which would overshoot the ceiling it is supposed to approach.
:func:`compounded`.

*Probabilities* are the same arithmetic for a different reason. A 0.25 chance of a partisan
rising each month is a 0.97 chance over a year, not 3.0 -- multiplying does not merely
overstate it, it stops being a probability. :func:`compounded` again.

*War* is not here. An advance is a flow, but resolving a year of it as one enormous step
would settle a campaign on a single dice roll, so the resolution phase runs **rounds**
instead -- see :func:`rounds` and :mod:`atlas.engine.resolution`.
"""

from __future__ import annotations

__all__ = [
    "REFERENCE_DAYS",
    "after_months",
    "compounded",
    "days_between",
    "over",
    "rounds",
    "span_of",
]

#: The turn length every balance constant in the engine was tuned against.
REFERENCE_DAYS = 30.0


def span_of(days: float) -> float:
    """How many reference months ``days`` is worth. ``1.0`` for the default turn."""
    return max(0.0, float(days)) / REFERENCE_DAYS


def over(amount: float, span: float) -> float:
    """A flow: ``amount`` per month, accumulated over ``span`` months."""
    return amount * span


def compounded(rate: float, span: float) -> float:
    """A per-month rate or probability, applied ``span`` times.

    Equivalent to applying ``rate`` once per month for ``span`` months, and correct for a
    fractional span as well as a long one -- a week is ``span = 0.233``, and a rate has to
    shrink for a short turn exactly as it grows for a long one.

    Clamped into ``[0, 1)`` on the way in because the callers pass tuned constants that a
    later edit could put out of range, and ``(1 - r) ** span`` for ``r > 1`` is either a
    complex number or a sign flip depending on the span -- neither of which is a rate.
    """
    if rate <= 0.0:
        return 0.0
    if rate >= 1.0:
        return 1.0
    return 1.0 - (1.0 - rate) ** span


def rounds(span: float) -> tuple[int, float]:
    """How many engine rounds a jump of ``span`` months resolves, and each round's share.

    One model call buys one *decision*; the engine then grinds out the time that decision
    covers. A year is twelve rounds of ordinary monthly fighting rather than one enormous
    roll: the total advance is the same, but the variance stays where it was tuned and the
    front produces a narrative -- through Sumy in March, Kharkiv in June -- instead of one
    opaque leap.

    Always at least one round, so a *short* jump still fights. A week returns
    ``(1, 0.233)``: one round at a fraction of a month's advance, which is the honest
    reading of seven days of contact.
    """
    count = max(1, round(span))
    return count, span / count


# ---------------------------------------------------------------------------
# The world's own calendar
# ---------------------------------------------------------------------------
#
# **A turn is not a duration**, which is the other half of everything above. The functions
# up there convert a turn's *length* into the right amount of change; these convert a
# promise's *length* into a date, so that a three-month ceasefire is three months of world
# time however many turns the player spends watching them go by.
#
# Kept here rather than in a module of their own because they answer the same question the
# rest of this file does -- how long is that, really -- and a second home for time would be
# the beginning of two answers.


def days_between(earlier: str, later: str) -> float | None:
    """Days of world time from one ISO date to another, or ``None`` if either is unreadable.

    ``None`` rather than zero, so a caller has to decide what an unknown date means. Zero
    would silently read as "no time has passed", which is the answer that grants victories
    and expires nothing.
    """
    from datetime import date

    try:
        return float((date.fromisoformat(later) - date.fromisoformat(earlier)).days)
    except (TypeError, ValueError):
        return None


def after_months(start: str, months: float) -> str:
    """The world date ``months`` after ``start``, or ``""`` if the start is unreadable.

    **Calendar months, not thirty-day blocks.** A ceasefire agreed on the first of March and
    written to run three months should end on the first of June, because that is what the
    two governments think they agreed. `REFERENCE_DAYS` is the engine's tuning unit and has
    no business being the unit a treaty is written in.

    A fractional count falls back to days at the reference length, which is what a caller
    asking for half a month can reasonably mean.
    """
    from datetime import date, timedelta

    try:
        began = date.fromisoformat(start)
    except (TypeError, ValueError):
        return ""
    whole = int(months)
    if whole != months:
        return (began + timedelta(days=months * REFERENCE_DAYS)).isoformat()
    year, month = began.year, began.month + whole
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    # The last day of a short month, for a start date the target month does not have --
    # the 31st of January plus one month is the 28th of February, not the 3rd of March.
    day = min(began.day, [31, 29 if _leap(year) else 28, 31, 30, 31, 30,
                          31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day).isoformat()


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
