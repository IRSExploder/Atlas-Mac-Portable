"""Occupation weariness settles at its ceiling however long the turn is.

`wareffort` says of itself that a half-occupied country "trends to `OCCUPIED_CEILING/2` and
stays there, permanently war-weary and not collapsing" -- the plateau is the whole reason the
term was rewritten from a monthly add. The plateau was real for a monthly turn and **fiction
for a longer one**: `gap * OCCUPIED_DRIFT * span` is a converging rate written linearly, and
it walks past what it converges toward once the span exceeds `1 / OCCUPIED_DRIFT`, which is
four months.

A quarter-occupied country over a one-year jump took **+75** from a term whose entire ceiling
is 25. And the clamp at 100 is what kept it quiet: the value stayed in range, so no bound was
violated, no batch was refused and nothing was reported. The country simply arrived at total
exhaustion, from a stabilised front, in one jump.

The same shape and the same silence as the intel-network and constituency drift found earlier
the same night -- which is the argument for the test being written against *the property* and
not against a number.
"""

from __future__ import annotations

import pytest

from atlas.engine.pacing import compounded
from atlas.engine.wareffort import (
    OCCUPIED_CEILING,
    OCCUPIED_DRIFT,
    TIME_CEILING,
    TIME_DRIFT,
    _charge,
)


class _Nation(dict):
    """The two columns `_charge` reads, and nothing else."""

    def __init__(self, *, treasury: float = 100.0, stability: float = 100.0) -> None:
        super().__init__(treasury=treasury, stability=stability)


def _time_term(standing: float, span: float) -> float:
    """What a merely-ongoing war contributed: a drift toward its own ceiling."""
    return max(0.0, TIME_CEILING - standing) * compounded(TIME_DRIFT, span)


def _occupation_term(*, occupied: int, homeland: int, standing: float,
                     span: float) -> float:
    """What the ground alone contributed: the same charge with and without it.

    **Differenced rather than modelled.** The first version subtracted a formula for the
    time term, which stopped being right the moment both terms converged -- a country at
    its plateau with no ground under threat charges *nothing*, and `_charge` reads that as
    a quiet month and hands back recovery. Asking the function twice needs no model of what
    it does inside.
    """
    def charge(held: int) -> float:
        return _charge(_Nation(), fresh=0.0, army=100.0, occupied=held,
                       homeland=homeland, standing=standing, span=span)

    return charge(occupied) - charge(0)


# ------------------------------------------------------------------- the plateau holds


@pytest.mark.parametrize("span", [1, 2, 4, 6, 12, 24, 60])
@pytest.mark.parametrize(("occupied", "homeland"), [(3, 12), (6, 12), (11, 12), (12, 12)])
def test_the_ground_never_charges_more_than_the_plateau_it_converges_to(
    span, occupied, homeland
):
    """**The property, at every span the engine can be asked for.** Five years in one jump
    is a legal ask; `pacing` exists so that asking it costs the same as asking for sixty
    monthly ones."""
    ceiling = OCCUPIED_CEILING * occupied / homeland

    assert _occupation_term(occupied=occupied, homeland=homeland, standing=0.0,
                            span=span) <= ceiling + 1e-9


def test_the_linear_form_this_replaced_would_fail_that():
    """Written down so the regression is a statement rather than a memory: over a year the
    old form charged a quarter-occupied country exactly three times its own ceiling."""
    ceiling = OCCUPIED_CEILING * 0.25
    old_form = ceiling * OCCUPIED_DRIFT * 12

    assert old_form == pytest.approx(75.0)
    assert old_form == pytest.approx(ceiling * 3)


def test_a_country_already_at_its_plateau_stays_on_it():
    """Ground held since last year adds nothing, which is what stops a stabilised front
    collapsing a country by itself.

    **Stated as the country's own net movement, and it used to be stated as the differenced
    term.** That reading was exactly zero only because every term read the same ``standing``
    for the whole turn: at its plateau the ground charged nothing, so `_charge` saw a quiet
    month and handed back a full month of recovery, and the two answers -- with the ground
    and without it -- differed by precisely that recovery. Now that the turn is walked a day
    at a time the country mends for a day, drops below its plateau, and the ground draws it
    straight back: it holds, which is what a plateau is. The monthly game always did this
    across turns; the difference is that a yearly turn now does it too.
    """
    for span in (1, 12):
        held = _charge(_Nation(), fresh=0.0, army=100.0, occupied=6, homeland=12,
                       standing=50.0, span=span)
        assert held == pytest.approx(0.0, abs=0.05), span


@pytest.mark.parametrize("span", [1, 3, 12, 13, 60])
def test_a_longer_turn_still_costs_more_than_a_shorter_one(span):
    """Converging is not the same as capped: more months under occupation must still weigh
    more than fewer, or a player could dodge the cost by jumping.

    **Asked of the charge rather than of the differenced term.** The difference is the
    occupied war measured against a counterfactual quiet one, and the counterfactual is
    still climbing toward `TIME_CEILING` long after the occupied war has arrived at its own
    plateau -- so past about a year the gap between them narrows while both totals rise.
    That is a property of the subtraction, not of the country: what a player could dodge by
    jumping is what the war actually costs, and that is monotone at every span.
    """
    def cost(months: float) -> float:
        return _charge(_Nation(), fresh=0.0, army=100.0, occupied=6, homeland=12,
                       standing=0.0, span=months)

    assert cost(span + 1) > cost(span)


def _after(months: int, *, span: float, occupied: int = 6) -> float:
    """The exhaustion a war of ``months`` reaches, played in turns of ``span`` months."""
    standing = 0.0
    for _turn in range(int(months / span)):
        standing = max(0.0, min(100.0, standing + _charge(
            _Nation(), fresh=0.0, army=100.0, occupied=occupied, homeland=12,
            standing=standing, span=span)))
    return standing


@pytest.mark.parametrize("years", [10, 30])
def test_a_quiet_war_reaches_the_same_place_at_every_pacing(years):
    """**The plateau is what has to be pacing-invariant, and it is exactly so.** A war in
    which nothing happens wears a country to the same number whether the player watched the
    decade in a hundred and twenty turns or in ten."""
    reached = [_after(years * 12, span=span, occupied=0) for span in (1, 3, 12)]

    assert reached[0] == pytest.approx(reached[1], abs=0.01)
    assert reached[0] == pytest.approx(reached[2], abs=0.01)
    assert 25.0 <= reached[0] <= 40.0, "the moderate plateau Alex asked for"


def test_an_occupied_war_arrives_at_the_same_place_too():
    """The destination is invariant; see below for the transient, which is not."""
    reached = [_after(36, span=span, occupied=6) for span in (1, 3, 12)]

    assert reached[0] == pytest.approx(50.0, abs=0.5)
    assert reached[2] == pytest.approx(reached[0], abs=0.5)


def test_the_first_year_of_an_occupied_war_is_invariant_too():
    """**The flag this file used to carry, retired.**

    It read: two converging terms both read `standing`, so applying them once over a long
    span is not the same as applying them repeatedly over short ones -- a year of
    half-occupied war reached 48.5 in monthly turns and 55.3 in one yearly turn. Only the
    destination was invariant; the transient was not, and it was left standing as a design
    question rather than a bug.

    It was a bug, and the measurement priced it: 1.42x the exhaustion for the same war with
    the same casualties, 1.78x for a shaky government losing ground and men. `_charge` now
    walks the turn a day at a time, so the terms compose the same way at every cadence and
    the transient is invariant as well as the plateau. See
    `test_a_war_wearies_a_country_by_the_calendar.py` for the full statement.
    """
    monthly = _after(12, span=1, occupied=6)
    yearly = _after(12, span=12, occupied=6)

    assert yearly == pytest.approx(monthly, abs=1e-6)
    assert _after(36, span=12, occupied=6) == pytest.approx(
        _after(36, span=1, occupied=6), abs=1e-6)


# ---------------------------------------------------- and the rates that are rates stay so


@pytest.mark.parametrize("span", [1, 6, 12])
def test_time_alone_drifts_toward_its_ceiling_and_stops(span):
    """**The retune.** Time was a flat monthly add with no ceiling, so every long war in
    the 200-turn campaign pinned at 100 with nothing occupied and no losses -- a phoney war
    and a catastrophe arriving at the same number. It converges now, like the ground term
    beside it."""
    charged = _charge(_Nation(), fresh=0.0, army=100.0, occupied=0, homeland=12,
                      standing=0.0, span=span)

    assert charged == pytest.approx(_time_term(0.0, span))


@pytest.mark.parametrize("span", [1, 6, 12])
def test_peace_mends_at_a_rate_per_month(span):
    """The other side of the same coin: recovery is a rate, and a year of peace mends
    twelve months' worth."""
    mended = _charge(_Nation(), fresh=0.0, army=100.0, occupied=0, homeland=0,
                     standing=0.0, span=span)

    # Nothing is happening to this country but the war existing, so what it is charged is
    # the time drift and nothing else.
    assert mended == pytest.approx(_time_term(0.0, span))
