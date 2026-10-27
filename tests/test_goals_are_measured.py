"""What a country said it wanted, in a form the engine can check.

Every nation has had goals since the scenarios were written and **none of them were ever
measured**. They are prose -- "deny China the technology that would close the gap", "outlast
Western support for Kyiv rather than defeat it" -- and prose is the right thing to hand a
model, because it carries the *why* no predicate does. It is also unscoreable: nothing in the
engine can tell whether a country is winning at outlasting somebody.

So `atlas.engine.goals` is a second, narrower thing standing beside the prose rather than
replacing it. **The design constraint is the one `peace.TERM_KINDS` holds itself to**: every
kind is a query against live state, because a goal the engine cannot evaluate is a sentence
in a briefing that means nothing.

Measured over twenty turns of the shipped map, seven of the eight authored powers changed
their attainment -- the United States lost its rivalry goal as China passed it, Ukraine lost
both as its country was occupied, and India, Germany and Japan grew into their targets. That
is the property these tests exist to hold: goals that track the world rather than eight
constants with ticks beside them.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.goals import GOAL_KINDS, Goal, goals_of, progress_of
from atlas.scenarios.modern_day import POWERS
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def test_every_authored_aim_uses_a_kind_the_engine_can_evaluate(conn):
    """**The bar, and the reason the vocabulary is small.**

    A kind the scorer does not implement would render as nothing and be met by nobody, which
    is exactly the silent-inertness this whole layer was built to end. Authoring is checked
    against the implementation rather than trusted to agree with it.
    """
    for power in POWERS:
        for aim in getattr(power, "aims", ()) or ():
            assert aim.kind in GOAL_KINDS, (power.id, aim.kind)


def test_an_aim_that_names_another_power_names_a_real_one(conn):
    """A rivalry goal against a country that is not on the map scores against zero for ever."""
    ids = {power.id for power in POWERS}
    for power in POWERS:
        for aim in getattr(power, "aims", ()) or ():
            if aim.subject:
                assert aim.subject in ids, (power.id, aim.subject)


def test_every_authored_aim_carries_the_sentence_it_came_from(conn):
    """**The prose is the point and the predicate is the instrument.**

    A briefing showing "hold_at_least 16" and nothing else has replaced a government's
    reasons with the engine's bookkeeping.
    """
    for power in POWERS:
        for aim in getattr(power, "aims", ()) or ():
            assert aim.because.strip(), (power.id, aim.kind)


def test_a_power_nobody_authored_aims_for_has_none(conn):
    """Silent rather than generic. **A goal nobody chose is not that country's goal**, and
    inventing one so every nation had some would be worse than showing nothing."""
    assert goals_of(conn, WORLD, "aurelia") == ()
    assert progress_of(conn, WORLD, "aurelia") == ()


def test_keeping_your_country_is_lost_when_it_is_occupied(conn):
    """The goal Ukraine holds, and it has to move when the map does."""
    from atlas.engine import goals as goals_module

    aim = Goal("keep_home", because="get its country back")
    original = goals_module.goals_of
    goals_module.goals_of = lambda *a, **k: (aim,)
    try:
        assert progress_of(conn, WORLD, "aurelia")[0].met

        region = conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = 'aurelia'"
            "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD,)).fetchone()
        conn.execute("UPDATE region SET controller_nation_id = 'veyra' WHERE id = ?",
                     (str(region["id"]),))
        conn.commit()

        entry = progress_of(conn, WORLD, "aurelia")[0]
        assert not entry.met
        assert entry.have == 1.0
    finally:
        goals_module.goals_of = original


def test_staying_out_of_war_is_lost_when_a_war_starts(conn):
    from atlas.engine import goals as goals_module

    aim = Goal("stay_out_of_war", because="stay out of it")
    original = goals_module.goals_of
    goals_module.goals_of = lambda *a, **k: (aim,)
    try:
        assert progress_of(conn, WORLD, "aurelia")[0].met
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = 'aurelia'"
            "   AND nation_b = 'veyra'", (WORLD,))
        conn.commit()
        assert not progress_of(conn, WORLD, "aurelia")[0].met
    finally:
        goals_module.goals_of = original


def test_a_rivalry_goal_turns_over_when_the_rival_passes_you(conn):
    """Measured over the shipped map this is the one that actually flipped: the United
    States held it on turn one and had lost it by turn nine, because China passed it."""
    from atlas.engine import goals as goals_module

    aim = Goal("outweigh", subject="veyra", because="stay ahead")
    original = goals_module.goals_of
    goals_module.goals_of = lambda *a, **k: (aim,)
    try:
        conn.execute("UPDATE nation SET economy = 200 WHERE id = 'aurelia'")
        conn.execute("UPDATE nation SET economy = 1 WHERE id = 'veyra'")
        conn.commit()
        assert progress_of(conn, WORLD, "aurelia")[0].met

        conn.execute("UPDATE nation SET economy = 1 WHERE id = 'aurelia'")
        conn.execute("UPDATE nation SET economy = 200 WHERE id = 'veyra'")
        conn.commit()
        assert not progress_of(conn, WORLD, "aurelia")[0].met
    finally:
        goals_module.goals_of = original


def test_goals_are_not_a_second_victory_condition(conn):
    """`scoring` answers who is winning; this answers whether one country is getting what it
    said it wanted, and for most powers on most maps those have different answers. Nothing
    here writes state or decides a game."""
    from atlas.engine import goals as goals_module

    source = (goals_module.progress_of.__doc__ or "")
    assert "read-only" in source.lower()


def test_one_war_is_counted_as_one(conn, monkeypatch):
    """**`relation` is directed**, and a war is written from both sides -- so counting rows
    told a government trying to stay out of war that it was in two the day it entered one.
    Two enemies are two wars; one enemy is one, whichever side's row says so."""
    from atlas.engine import goals as goals_module
    from atlas.engine.goals import describe

    monkeypatch.setattr(goals_module, "goals_of",
                        lambda *a, **k: (Goal("stay_out_of_war", because="stay out of it"),))

    def at_war(a: str, b: str) -> None:
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ?"
            "   AND ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
            (WORLD, a, b, b, a))
        conn.commit()

    at_war("aurelia", "veyra")
    [entry] = progress_of(conn, WORLD, "aurelia")
    assert entry.have == 1.0
    assert "you are in 1 war(s)" in describe(conn, WORLD, entry)

    at_war("karthos", "aurelia")
    assert progress_of(conn, WORLD, "aurelia")[0].have == 2.0


# ------------------------------------------------------ every kind, measured and described
#
# The tests above follow the three goals that actually flipped on the shipped map. These
# hold the rest of the vocabulary to the same bar: **every kind in `GOAL_KINDS` is a
# promise that the engine can answer it**, and a kind the scorer silently skipped would be
# a line in the table that no briefing ever prints and no power ever meets. So each kind is
# measured against a world that is then moved, and each is described both ways -- the
# sentence a government reads has to agree with the tick beside it.


def _only(monkeypatch, *aims: Goal) -> None:
    """Hand ``aurelia`` these aims, as a scenario author would have."""
    from atlas.engine import goals as goals_module

    monkeypatch.setattr(goals_module, "goals_of", lambda *a, **k: aims)


def _occupy(conn, region: str, by: str) -> None:
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = ?", (by, region))
    conn.commit()


@pytest.mark.parametrize("kind", sorted(GOAL_KINDS))
def test_every_kind_in_the_vocabulary_is_measured_and_can_be_said(conn, monkeypatch, kind):
    """**The vocabulary is the implementation's promise, not a list of hopes.**

    The authoring test checks that every aim uses a kind in `GOAL_KINDS`; this is the other
    half, that every kind in `GOAL_KINDS` is one `progress_of` actually answers. A kind the
    scorer did not implement would be dropped without a word -- the briefing would show one
    fewer line and nobody would know a goal had been authored at all.
    """
    from atlas.engine.goals import describe

    aim = Goal(kind, value=1.0, subject="veyra" if kind == "outweigh" else "",
               because=f"the sentence behind {kind}")
    _only(monkeypatch, aim)

    [entry] = progress_of(conn, WORLD, "aurelia")
    assert entry.goal is aim
    line = describe(conn, WORLD, entry)
    assert line.startswith(f"the sentence behind {kind} -- **")


def test_holding_ground_counts_what_you_control_not_what_you_own(conn, monkeypatch):
    """Russia's goal is to hold what it has taken, and taken ground is *held* ground.

    Counted by who controls a province, falling back to who owns it: a province occupied
    from you stops counting the day it is lost, and one you occupied starts counting the
    day it is won, long before any treaty moves the title.
    """
    _only(monkeypatch, Goal("hold_at_least", value=2.0, because="hold what it has"))

    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert (entry.have, entry.need, entry.met) == (2.0, 2.0, True)

    _occupy(conn, "r_coast", "veyra")
    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert (entry.have, entry.met) == (1.0, False)
    assert entry.fraction == 0.5

    _occupy(conn, "r_west", "aurelia")
    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert (entry.have, entry.met) == (2.0, True), "ground taken in war was not counted"


def test_the_open_sea_is_not_ground_you_hold(conn, monkeypatch):
    """An ocean tile is nobody's province, and counting it would let a navy meet a goal
    about territory by sailing."""
    _only(monkeypatch, Goal("hold_at_least", value=3.0, because="hold three provinces"))
    conn.execute("UPDATE region SET owner_nation_id = 'aurelia' WHERE id = 'r_sea'")
    conn.commit()

    assert progress_of(conn, WORLD, "aurelia")[0].have == 2.0


@pytest.mark.parametrize("kind,column", [("economy_above", "economy"),
                                         ("industry_above", "industry")])
def test_a_growth_target_is_met_on_reaching_it_and_lost_on_falling_below(
        conn, monkeypatch, kind, column):
    """India, Germany and Japan grew into theirs over twenty turns. **At or above**, so a
    country that hits its number exactly has hit it."""
    _only(monkeypatch, Goal(kind, value=30.0, because="grow"))

    def set_to(figure: float) -> None:
        conn.execute(f"UPDATE nation SET {column} = ? WHERE id = 'aurelia'",  # noqa: S608
                     (figure,))
        conn.commit()

    set_to(15.0)
    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert (entry.have, entry.need, entry.met) == (15.0, 30.0, False)
    assert entry.fraction == 0.5

    set_to(30.0)
    assert progress_of(conn, WORLD, "aurelia")[0].met

    set_to(45.0)
    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert entry.met
    assert entry.fraction == 1.0, "overshooting a target is not more than all of it"


def test_the_two_growth_targets_read_their_own_figures(conn, monkeypatch):
    """An economy goal answered from industry, or the other way round, would be met or
    missed for the wrong country's reasons -- and in the sandbox both start at different
    figures, so a crossed wire shows."""
    _only(monkeypatch,
          Goal("economy_above", value=20.0, because="an economy"),
          Goal("industry_above", value=20.0, because="an industry"))

    economy, industry = progress_of(conn, WORLD, "aurelia")
    assert (economy.have, economy.met) == (20.0, True)
    assert (industry.have, industry.met) == (12.0, False)


def test_a_rivalry_against_a_power_that_is_gone_is_won(conn, monkeypatch):
    """A rival that no longer exists scores nothing, and you stand above nothing. The goal
    was to outweigh it; it has been outweighed as thoroughly as a country can be."""
    _only(monkeypatch, Goal("outweigh", subject="veyra", because="outlast them"))
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = 'veyra'")
    conn.commit()

    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert entry.need == 0.0
    assert entry.met


# -------------------------------------------------------------- and each says it both ways


def test_a_kept_country_says_so_and_a_lost_one_says_how_much_is_lost(conn, monkeypatch):
    from atlas.engine.goals import describe

    _only(monkeypatch, Goal("keep_home", because="get its country back"))
    assert describe(conn, WORLD, progress_of(conn, WORLD, "aurelia")[0]) == (
        "get its country back -- **met**: no province of yours is occupied")

    # A goal with no scale is all or nothing. Nothing occupied is the whole of it; one
    # province occupied is none of it, not "one of zero".
    assert progress_of(conn, WORLD, "aurelia")[0].fraction == 1.0

    _occupy(conn, "r_coast", "veyra")
    _occupy(conn, "r_north", "karthos")
    entry = progress_of(conn, WORLD, "aurelia")[0]
    assert describe(conn, WORLD, entry) == (
        "get its country back -- **not met**: 2 province(s) of yours are occupied")
    assert entry.fraction == 0.0


def test_peace_is_described_as_peace(conn, monkeypatch):
    from atlas.engine.goals import describe

    _only(monkeypatch, Goal("stay_out_of_war", because="stay out of it"))
    assert describe(conn, WORLD, progress_of(conn, WORLD, "aurelia")[0]) == (
        "stay out of it -- **met**: you are at peace")


def test_a_rival_is_named_as_the_player_knows_it_not_by_its_id(conn, monkeypatch):
    """"against veyra's" is the database talking. The briefing says Veyra, and shows the
    two scores so a government can see how far ahead or behind it is, not only which."""
    from atlas.engine.goals import describe
    from atlas.engine.scoring import score_of

    _only(monkeypatch, Goal("outweigh", subject="veyra", because="stay ahead"))
    conn.execute("UPDATE nation SET economy = 1 WHERE id = 'aurelia'")
    conn.execute("UPDATE nation SET economy = 200 WHERE id = 'veyra'")
    conn.commit()

    line = describe(conn, WORLD, progress_of(conn, WORLD, "aurelia")[0])
    mine = score_of(conn, WORLD, "aurelia").total
    theirs = score_of(conn, WORLD, "veyra").total
    assert line == (f"stay ahead -- **not met**: you stand at {mine:.2f}"
                    f" against Veyra's {theirs:.2f}")


def test_a_target_is_described_as_where_you_are_against_where_you_said(conn, monkeypatch):
    from atlas.engine.goals import describe

    _only(monkeypatch, Goal("hold_at_least", value=3.0, because="hold three provinces"),
          Goal("economy_above", value=20.0, because="a sound economy"))
    held, economy = progress_of(conn, WORLD, "aurelia")

    assert describe(conn, WORLD, held) == "hold three provinces -- **not met**: 2 of 3"
    assert describe(conn, WORLD, economy) == "a sound economy -- **met**: 20 of 20"


def test_a_goal_authored_without_its_sentence_falls_back_to_saying_what_it_measures(
        conn, monkeypatch):
    """The authoring test forbids this for the shipped scenarios; a scenario built in the
    workshop is not held to it. A line that began with "stay_out_of_war" would be the
    engine's bookkeeping standing where a government's reasons should be."""
    from atlas.engine.goals import describe

    _only(monkeypatch, Goal("stay_out_of_war"))
    line = describe(conn, WORLD, progress_of(conn, WORLD, "aurelia")[0])

    assert line.startswith(GOAL_KINDS["stay_out_of_war"] + " -- **met**")


def test_a_figure_just_short_of_its_target_does_not_read_as_reaching_it(conn, monkeypatch):
    """**The sentence has to agree with the tick beside it.** China's target is an economy
    of 110, and budget drift moves that figure by fractions -- so a turn on which it stands
    at 109.6 is a turn the goal is honestly not met. Rounding both to whole numbers prints
    "110 of 110" beside **not met**, which a player can only read as the engine being
    wrong about its own arithmetic."""
    from atlas.engine.goals import describe

    _only(monkeypatch, Goal("economy_above", value=110.0, because="keep growing"))
    conn.execute("UPDATE nation SET economy = 109.6 WHERE id = 'aurelia'")
    conn.commit()

    [entry] = progress_of(conn, WORLD, "aurelia")
    assert not entry.met
    line = describe(conn, WORLD, entry)
    assert "110 of 110" not in line
    assert "109.6 of 110" in line


# ------------------------------------------------------------------- and the briefing block


def _recast_as_ukraine(conn) -> None:
    """Make the sandbox a Modern Day world with a Ukraine in it, holding Aurelia's ground.

    So the aims under test are the ones the scenario actually authored, read through the
    real lookup -- world, preset, scenario, power -- rather than handed in by a patch.
    """
    columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(nation)")]
    picked = ", ".join(
        {"id": "'ukr'", "name": "'Ukraine'"}.get(column, column) for column in columns)
    conn.execute(
        f"INSERT INTO nation ({', '.join(columns)})"  # noqa: S608 - names from the schema
        f" SELECT {picked} FROM nation WHERE id = 'aurelia'")
    conn.execute("UPDATE region SET owner_nation_id = 'ukr' WHERE owner_nation_id = 'aurelia'")
    conn.execute("UPDATE world SET preset_id = 'modern-day' WHERE id = ?", (WORLD,))
    conn.commit()


def test_goals_are_read_from_the_scenario_the_world_was_built_from(conn):
    """**A goal is a fact about who a country is**, like its character blurb, so it comes
    from the scenario rather than the database. The same nation id on a world of another
    era, or a world that is not there, has none."""
    _recast_as_ukraine(conn)
    [ukraine] = [power for power in POWERS if power.id == "ukr"]

    assert goals_of(conn, WORLD, "ukr") == tuple(ukraine.aims)
    assert goals_of(conn, WORLD, "aurelia") == (), "a power the scenario never wrote"
    assert goals_of(conn, "no-such-world", "ukr") == ()


def test_a_power_with_no_aims_gets_no_heading(conn):
    """**A heading over nothing is worse than no heading.** Most powers on every map, and
    every power on two of the three eras, have no authored aims -- and their briefing is
    exactly what it was before this layer existed."""
    from atlas.engine.goals import briefing_lines

    assert briefing_lines(conn, WORLD, "aurelia") == []


def test_the_briefing_says_what_was_wanted_and_how_much_of_it_is_had(conn):
    """Ukraine's two aims, read from the scenario and measured against the map it is on.

    Nothing of its country is occupied, so the first is met; it holds two provinces against
    a target of nine, so the second is not. The heading counts them, each line leads with
    the sentence the government wrote, and the block ends by saying plainly that meeting
    your own aims is not winning -- because a briefing that let a staff confuse the two
    would have it chasing a victory condition it never needed.
    """
    from atlas.engine.goals import briefing_lines

    _recast_as_ukraine(conn)
    lines = briefing_lines(conn, WORLD, "ukr")

    assert lines[0] == "## What you are trying to achieve (1 of 2 met)"
    assert lines[1] == "- get its country back -- **met**: no province of yours is occupied"
    assert lines[2] == "- hold the country it still has -- **not met**: 2 of 9"
    assert "meeting them is not winning" in lines[3]
    assert lines[-1] == "", "the block runs into whatever the briefing prints next"

    _occupy(conn, "r_coast", "karthos")
    assert briefing_lines(conn, WORLD, "ukr")[0] == (
        "## What you are trying to achieve (0 of 2 met)"), "the map moved; the count did not"


def test_the_nation_the_model_plays_is_told_its_aims(conn):
    """The block is only worth anything if it reaches the government it is about. The
    tracked-agent view is the briefing a model deciding for this country reads."""
    from atlas.engine.views import build_tracked_agent_view

    _recast_as_ukraine(conn)
    view = build_tracked_agent_view(conn, WORLD, nation_id="ukr")

    assert "## What you are trying to achieve (1 of 2 met)" in view.text
