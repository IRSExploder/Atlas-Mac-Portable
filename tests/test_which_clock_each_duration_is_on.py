"""Every duration in the engine is on one of two clocks, and which one is written down.

**The world's clock** is elapsed historical time: how long a truce runs, how long a trail
stays warm, how long a sanctions regime drains an economy, how long a power must hold
domination before it is one. None of these may depend on how the player spends their turns,
because the pace control exists for how much detail somebody wants to watch.

**The player's clock** is turns, and some things honestly belong to it: how long a reminder
stays on screen, how many recent turns an estimate is drawn from, how long a player has to
notice a promise before it is called broken. A turn is the unit of the player's attention,
and converting these to dates would be pedantry rather than correctness.

The audit that produced this list found three things still on the wrong clock after the
obvious ones moved — a sanctions regime, the decay of air superiority, and (fixed the day
before) the sustain window on victory. Each is recorded below with which clock it is on and
why, so the next duration added has to answer the same question.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ENGINE = pathlib.Path("src/atlas/engine")

#: Anything whose name suggests it measures time.
#:
#: **`MEMORY` was added because a duration hid behind its name for the life of this file.**
#: `organisation.DEFIANCE_MEMORY` was six turns -- how long a body remembers having sat
#: about a power -- and it was on the player's clock, which is precisely what this sweep
#: exists to catch. It was never caught, and not because the reasoning was wrong: the sweep
#: never saw it, because no word in this pattern appears in its name. A gate that finds its
#: subjects by name is only ever as good as the vocabulary, so the vocabulary is the part
#: to distrust; when a duration turns up that this did not match, widen it here rather than
#: classifying the one constant and moving on.
#: **`LOSS` was added because a rate hid behind its name, exactly as `MEMORY` did.**
#: `air._LOSS_LOSER` is a share of a wing charged *per reference month*, and it was charged
#: flat once per turn -- so a yearly jump cost an air force one month of aircraft and a
#: weekly jump cost it a month of them. It is the same failure this file was written for and
#: the sweep walked straight past it, because no word in the pattern appears in its name.
#: Widened narrowly: `LOSS` adds these two and nothing else, where `RATE` would pull in
#: twelve more constants that are already correctly scaled and turn this into a catalogue
#: exercise. Those are worth sweeping and are not worth sweeping *here*.
_TIMELIKE = re.compile(
    r"(TURNS?|MONTHS?|DAYS?|AFTER|WITHIN|GRACE|WINDOW|COOLDOWN|RECENT|SUSTAIN|MEMORY"
    r"|LOSS)")

#: **On the world's clock**: elapsed historical time, and none of it may move with pacing.
#: Every one of these is a duration in months or days, or a rate charged per month.
WORLD_CLOCK: dict[str, str] = {
    "WAR_DAYS_BEFORE_PEACE": "how long a bot's war must have run before it may ask for a "
                             "truce; the age of a war is elapsed history, not attention",
    "REPEAT_AGGRESSOR_DAYS": "how recently another war must have been declared for the "
                             "council bot to call a power a repeat aggressor; world time",
    "ARMISTICE_MONTHS": "how long a truce runs; the two governments agreed months",
    "_LAPSE_WARNING_MONTHS": "how near a sky the player holds has to be to being let go of "
                             "before the alert list says so. Air superiority decays by a "
                             "rate per month of world time, so how close it is to going is "
                             "elapsed time: at yearly pacing a turn is twelve months of "
                             "decay and at weekly it is a quarter of one, and a threshold "
                             "counted in turns would warn about a sky with most of its "
                             "margin left and then warn again four times about the same one",
    "RESTRAINT_DAYS": "how far back the concert route looks for a war this power started "
                      "or a covert operation of its own that came out. Elapsed history: how "
                      "long the world holds something against you cannot depend on how often "
                      "somebody looks at the game",
    "COOLDOWN_DAYS": "how long after starting a war a power waits before starting another. "
                     "Elapsed history: how often a country can go to war must not depend on "
                     "how often somebody looks at the game, and written as a turn count it "
                     "meant eight months at monthly pacing and eight years at yearly",
    "MEDIATION_COOLDOWN_DAYS": "how long before the same power may put terms to the same "
                               "war again. Elapsed history: a refused mediation is a "
                               "refusal, and how long a rebuff stands cannot depend on how "
                               "often somebody looks at the game -- written as a turn count "
                               "it would mean a year at monthly pacing and twelve at yearly",
    "REVIEW_DAYS": "how long a government holds a strategy before it is asked to look at "
                   "it again. Elapsed history: how often a country rethinks what it is "
                   "playing for is a fact about the country, not about how often somebody "
                   "looks at the game -- and written as a turn count it would mean a year "
                   "at monthly pacing and twelve at yearly, which is the whole defect the "
                   "world-clock pass existed to remove",
    "WARHEAD_BUILD_DAYS": "how long one warhead takes to make. Elapsed history and nothing "
                          "else: fissile material accumulates at a rate physics sets, not "
                          "at a rate that should depend on how often somebody looks at the "
                          "game. Priced in days for the same reason every build time is",
    "ENTRENCH_PER_MONTH": "how much a formation digs in per reference month standing "
                          "still. Elapsed time and nothing else: four months in one place "
                          "is four months' worth of spadework whether the game is played a "
                          "month or a year at a time, and priced per turn it would make a "
                          "slow-playing game a differently balanced one",
    "REPRICE_EVERY_MONTHS": "how long a standing trade price stands before the two parties "
                            "reopen the file. Elapsed history: how often terms are "
                            "renegotiated is a fact about a commercial relationship, not "
                            "about how often somebody looks at the game -- and written as "
                            "the turn count it used to be, it meant six weeks at weekly "
                            "pacing and six *years* at yearly, so a price settled in 1936 "
                            "stood untouched into 1942. The one clock in this list a turn "
                            "cannot always honour: terms are reconsidered at most once per "
                            "turn, so a yearly game gets a year and that is the floor",
    "MINIMUM_MONTHS": "how long an export embargo must stand before the government that "
                      "imposed it may lift it. Elapsed history, and the one duration in "
                      "this file whose whole job is to make a decision cost something: an "
                      "embargo taken back the turn after it was imposed was never a "
                      "measure, and written as a turn count it would be six months of "
                      "sacrifice at monthly pacing and six *years* of it at yearly -- the "
                      "same coercion costing two wildly different amounts because of how "
                      "often somebody looks at the game",
    "COOLDOWN_MONTHS": "and how long after lifting one before the same pair may be "
                       "embargoed again. Elapsed history for the same reason and with a "
                       "sharper edge: without it a government resets the minimum term by "
                       "lifting and re-imposing, so the lane is closed in the months it "
                       "costs nothing and open in the months it pays",
    "HORIZON_MONTHS": "how far ahead the current-policy fiscal estimate runs. Elapsed "
                      "world time, because the thing that makes a year the right horizon "
                      "is that the assumption underneath it -- nothing changes -- is thin "
                      "at a year and absurd at three. Written as a turn count it would be "
                      "a projection to next spring at monthly pacing and to the 2030s at "
                      "yearly, off one constant nobody re-read",
    "MAX_MONTHS": "the longest truce anybody may write, in the same unit",
    "_WARN_WITHIN_DAYS": "how close to running out a truce is worth mentioning",
    "COLD_AFTER_DAYS": "how long a covert trail stays warm enough to be news",
    "SANCTION_MONTHS": "how long a standing measure drains an economy",
    "DEFIANCE_MEMORY_MONTHS": "how long an international body remembers having sat about "
                              "a power, after which it is no longer being *defied* by the "
                              "same power coming back. That a condemned government is in "
                              "front of the Council again is elapsed history: it does not "
                              "become defiance sooner because somebody is watching a month "
                              "at a time, and six turns made it six months at monthly "
                              "pacing and six years at yearly from identical events",
    "_EXPIRY_WARNING_DAYS": "how close to lapsing an agreement is before it is worth a "
                            "line. **The window is about the agreement's life, not the "
                            "reader's attention**: an agreement now ends on a date, so a "
                            "warning counted in turns arrives at a different point in that "
                            "life at every pacing -- two turns before the end is two months "
                            "at monthly and two years at yearly. `_EXPIRY_WARNING` is the "
                            "turn version, kept only for agreements written before there "
                            "was a date to measure against.",
    "ANSWER_MONTHS": "how long an ally has to answer a call to arms before staying home "
                     "is on the record as a refusal; a government does not decide faster "
                     "because somebody is watching a year at a time",
    "SHIFT_MONTHS": "how long a country takes to reorganise itself around a new doctrine; "
                    "half a year of elapsed history, and a country reorganises no faster "
                    "because somebody is watching it a year at a time",
    "SHIFT_AT_WAR": "a multiplier on the above, so it is on whichever clock that is",
    "_DOMINATION_DAYS": "how long domination must hold before it is announced as one",
    "_CONCERT_DAYS": "how long a power must lead the international order before the concert victory is announced. Elapsed history, like the other two victory runs: who has led the world for six months is a fact about the world and not about how often somebody looked at it",
    "_HEGEMONY_DAYS": "and how long hegemony must hold, in the same unit and for the "
                      "same reason",
    "CESSION_GRACE_MONTHS": "how long a government has to hand over a province it "
                            "promised before not having done so is a breach. **This was "
                            "the one deliberate exception and the exception was wrong** -- "
                            "see the note below.",
    "BASE_PER_MONTH": "a rate: what a month of war costs in patience",
    "RECOVERY_PER_MONTH": "a rate: what a month of peace gives back",
    "_ATTRITION_PER_MONTH": "a rate: what a month out of supply costs",
    "_LAPSE_PER_MONTH": "a rate: air superiority given up per quiet month",
    "_LOSS_LOSER": "a rate: the share of a wing that does not come back from a month of "
                   "losing the sky, compounded over the turn's span in _charge_attrition",
    "_LOSS_WINNER": "a rate: the same, for the side that won the sky and still bled for "
                    "it; compounded over the same span and by the same call",
    "MOMENTUM_PER_TURN": "a rate, span-scaled at its one call site",
    "COUNTER_PER_TURN": "a rate: counter-intelligence built per month, span-scaled",
    "PER_TURN_CAP": "a monthly cap, span-scaled where it is applied",
    "_MAX_MONTHS_STORED": "a quantity of stock measured in months of consumption, which is a volume and not a length of time",
    "_MONTHS_OF_STOCK": "the same: how much is in the warehouse, said in months of it",
    "_DEFAULT_MONTHS_STORED": "the same, for a world that has not been told otherwise",
    "WARNING_DAYS": "how much notice a lane about to stop is meant to give: one repricing "
                    "interval of world time, because that is how long the stored price it "
                    "would otherwise be read off stands still for",
    "ESCALATION_DAYS": "when that warning becomes a deadline -- this lane stops before its "
                       "terms are next reopened",
    "RECOVERY_MARGIN_DAYS": "how far a suspended lane's regard must recover past the "
                            "crossing before it reopens, in days of the engine's own drift. "
                            "A duration and not a count of points for the reason "
                            "MEDIATION_COOLDOWN_DAYS is one",
    "DWELL_DAYS": "how long a suspension stands before it may lift at all. The clause that "
                  "binds where a margin cannot: a margin is a position and a long turn "
                  "crosses a position in one step, and assess_trade runs once per round",
}

#: **On the player's clock**: turns, honestly, because they are about somebody's attention
#: rather than about the world.
PLAYER_CLOCK: dict[str, str] = {
    "RECENT_TURNS": "how many recent turns an advance estimate is drawn from; a property "
                    "of the estimator's sample, not of the world",
    "DEFAULT_START_TURN": "where a report starts reading; a reading window",
    "DEFAULT_WINDOW": "the reversal counter's reach: a peace in the same decision round as "
                      "a stated intent to fight on, or the next one. It measures a commander "
                      "contradicting its own last answer, which is about decisions, not "
                      "about how much history elapsed between them",
    "MAX_ORDERS_PER_TURN": "an allowance per decision, not a duration",
    "MAX_GAMBITS_PER_TURN": "the same: how many undertakings one decision may contain",
    "MAX_PER_TURN": "how many embargo decisions one government may take in one answer. An "
                    "allowance per decision like the two above, not a duration -- two, so "
                    "a power can lift one and impose another in the same breath",
    "PER_FIELD_TURN_CAP": "a per-decision ceiling, span-scaled by its caller",
    "NOVELTY_TURNS": "how long a change goes on counting as new on the attention "
                     "substrate's novelty axis. The axis measures whether the player has "
                     "seen a thing yet, and somebody at yearly pacing has looked at the "
                     "world as many times as somebody at monthly -- so turns are the unit "
                     "the question is actually asked in, and converting it to days would "
                     "make 'new' mean something different depending on the pace control",
    "NEWS_RECENT_TURNS": "how far back that substrate's news sweep reaches. The same "
                         "argument: 'recently' means since the player last looked",
    "CAMPAIGN_TURNS": "the campaign's length as a number of decisions the player is asked "
                      "for (engine/campaign.py). Deliberately turns: it sizes a play session, "
                      "so at a slower pace the same campaign spans more history rather than "
                      "asking for more turns. A world can carry its own limit",
}


#: Caught by the name pattern and not about time at all. Listed rather than excluded by a
#: cleverer regex, because "it looked like a duration and is not" is a fact worth writing
#: down once -- and a regex tuned until nothing awkward matches is a regex that will miss
#: the next real one.
NOT_A_DURATION: dict[str, str] = {
    "BREAKOUT_LOSS": "the share of strength an encircled formation loses breaking out, "
                     "charged once at the moment of the breakout -- an event, not a flow, "
                     "so it is the same whatever the turn length",
    "_DAYS_PER_MONTH": "a unit conversion, not a duration: how many days a month is, so a "
                       "window written in months can be compared against days elapsed",
    "REFERENCE_DAYS": "the length of the tuning unit itself, which is what every other "
                      "duration here is expressed against rather than a duration of its own",
    "WINDOW": "how many past observations a fitness trend looks back over -- a count of "
              "readings, not a duration. It is on neither clock because it measures nothing "
              "in the world: the *interval* between those readings is REVIEW_DAYS, which is "
              "on the world clock and is where the pacing question actually lives",
    "_WINDOW": "how many rows down the event log the post-turn report reaches before it "
               "ranks them -- a `limit` on a query, like the `WINDOW` above and for the "
               "same reason. The *span* it covers is one turn, which is stated at the call "
               "site as `since_turn=turn` and is not a constant at all",
    "_MONTHS_PER_TURN_FALLBACK": "how much world time to assume a turn was worth when a "
                                 "row carries no date to measure against -- a unit "
                                 "conversion for a path that should not exist, not a "
                                 "duration of its own. Deliberately the shortest a turn "
                                 "can reasonably be, so the fallback releases an embargo "
                                 "no sooner than the calendar would",
    "_DISGRACE": "a magnitude -- what using a warhead costs a government in standing. It "
                 "matched on nothing but the letters in its name",
    "WARNING_DAYS_UNKNOWN": "a sort sentinel, not a length of time: where a trade warning "
                            "with no deadline attached ranks against ones that have one. It "
                            "is in days-space so the comparison type-checks, and larger "
                            "than any real notice so a lane that can be given a deadline is "
                            "always named first",
    "_MIN_DRIFT_PER_MONTH": "a floor under a *rate* -- the slowest regard is allowed to be "
                            "assumed to drift when a distance in points is converted into "
                            "days. Points per month, not months",
}


def _timelike_constants() -> dict[str, tuple[str, float]]:
    """``NAME -> (module, value)`` for every numeric constant that looks like a duration."""
    found: dict[str, tuple[str, float]] = {}
    for path in sorted(ENGINE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            name = getattr(target, "id", "")
            if not name or not name.replace("_", "").isupper():
                continue
            if not _TIMELIKE.search(name):
                continue
            value = getattr(node.value, "value", None)
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            found[name] = (path.stem, float(value))
    return found


def test_there_are_durations_to_classify():
    """A sweep that finds nothing passes for the wrong reason."""
    assert len(_timelike_constants()) > 10


def test_every_duration_is_on_a_clock_somebody_named():
    """**The rule.** A time-shaped constant must be in one of the two lists, with a reason.
    There is no third category and no unlisted one, so the next duration added has to answer
    which clock it is on before it can be committed."""
    classified = set(WORLD_CLOCK) | set(PLAYER_CLOCK) | set(NOT_A_DURATION)
    unclassified = sorted(set(_timelike_constants()) - classified)

    assert not unclassified, (
        f"time-shaped constant(s) on no stated clock: {unclassified}. Put each in "
        "WORLD_CLOCK (elapsed historical time -- must not move with pacing) or "
        "PLAYER_CLOCK (turns, honestly, because it is about somebody's attention).")


def test_every_reason_says_something():
    for name, why in {**WORLD_CLOCK, **PLAYER_CLOCK, **NOT_A_DURATION}.items():
        assert len(why) > 20, name


def test_nothing_is_on_both_clocks():
    """**There is no longer an exception, and the exception was a bug.**

    `CESSION_GRACE_TURNS` used to be in both lists, with the world-clock entry recording why
    it was on the player's clock instead -- the argument being that the grace period is
    about a player noticing a promise. Codex read that and disagreed, and was right: what it
    measures is how long a *government* has to hand a province over before not having done
    so is a breach. That is elapsed history and has nothing to do with who is watching.

    A deliberate exception with its reasoning written beside it is exactly the shape a wrong
    decision hides in, because the reasoning makes it look considered. There is no exception
    now, so the next one has to argue with this docstring.
    """
    assert set(WORLD_CLOCK) & set(PLAYER_CLOCK) == set()


# ----------------------------------------------------- and nothing on the world's clock
# ----------------------------------------------------- is still counted in bare turns


#: Names that say "turns" and are on the world's clock would be the bug this file exists to
#: catch. The three exceptions are rates whose name predates the rule and whose call site
#: scales them, which the reasons above record.
_RATES_NAMED_PER_TURN = {"MOMENTUM_PER_TURN", "COUNTER_PER_TURN", "PER_TURN_CAP"}


def test_no_world_clock_duration_is_counted_in_turns():
    """A duration on the world's clock named in turns is one nobody has converted yet.

    The exceptions are *rates*, not durations: a quantity charged per month whose constant
    was named before the rule existed. Each is span-scaled at its call site, which the
    `per-round` gate next door checks separately.
    """
    counted_in_turns = sorted(
        name for name in WORLD_CLOCK
        if "TURN" in name and name not in _RATES_NAMED_PER_TURN
        and name not in PLAYER_CLOCK
    )

    assert not counted_in_turns, counted_in_turns


@pytest.mark.parametrize("name", sorted(_RATES_NAMED_PER_TURN))
def test_the_rates_named_per_turn_really_are_rates(name):
    """Each is a small per-month quantity rather than a length of time, which is what makes
    the name a leftover rather than a lie."""
    found = _timelike_constants()

    assert name in found, name
    assert found[name][1] <= 12.0, (name, found[name])
