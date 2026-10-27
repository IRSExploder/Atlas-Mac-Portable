"""A refusal the asker never saw, and three bounds the briefing never stated.

**The engine has always written these sentences and always thrown them away.** Every layer
words its refusals as *what to write instead* -- that is what `causality.Refusal` is for,
and it is why the messages read "declare war on dvn first; an objective cannot open a war"
rather than "invalid" -- and every one of them went to `JumpReport.rejections`, which
reaches `atlas.ui.overlays` and no view builder at all. So the whole discipline was paid
for and spent on the human, while the party that had actually been refused was told
nothing and asked again.

Measured on the recorded tape `pt77-cold-war`: the United States set ``reinforce: true`` on
twelve of twelve turns and was refused on **nine** of them, in the same words each time;
the Soviet Union restated a standing trade intention on three consecutive turns; two powers
named a trade partner that is not on the map eight times between them.
`atlas.testing.laboratory` already said so -- *"the same power refused three turns running
is a staff that has not understood what it was shown"* -- and its neighbouring comment says
the fix belongs in the engine. This is that fix, and the three specific disclosures the
same audit found underneath it.

The four things pinned here:

* **the store**, which must be per nation, per turn, and invisible to a fingerprint;
* **the block**, which must appear only when the last turn actually refused something;
* **the rewind**, because a refusal that no longer happened must not go on being read back;
* **the three menus** -- reinforcement, trade partners, the shared yard queue -- each of
  which quoted a number or named a field that the engine then contradicted.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine import refusals
from atlas.engine.orders import (
    REINFORCE_MAX_FRACTION,
    OrderEffects,
    apply_order_effects,
    replaceable_now,
)
from atlas.engine.views import build_tracked_agent_view
from atlas.state.bootstrap import fingerprint
from atlas.state.replay import rewind_to
from atlas.state.tables import HISTORY, REGISTRY

WORLD = "sandbox"
ME, THEM = "aurelia", "karthos"


# --------------------------------------------------------------------------- the store


def test_the_table_is_history_and_therefore_moves_no_fingerprint(conn):
    """Out of the digest, which is what let this layer ship beside a world change.

    **The reason is narrower than the one first written here.** This docstring used to say
    that "nothing reads it to decide anything, and two worlds that reached the same state
    having been refused different things are the same world" -- which is false, because the
    row is quoted into the next turn's prompt. It stays out of the *digest* because it is
    append-only and truncated rather than reversed by inverse diff, and for no other reason;
    it is very much in the *comparison*. `test_the_refusal_record_survives_a_life` pins both
    halves.
    """
    assert REGISTRY["refusal"].kind == HISTORY
    assert REGISTRY["refusal"].gates_play is True

    before = fingerprint(conn, WORLD)
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="reinforce",
                    reason="nothing to reinforce")
    conn.commit()
    assert fingerprint(conn, WORLD) == before


def test_a_refusal_is_kept_per_nation_and_per_turn(conn):
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="reinforce", reason="one")
    refusals.record(conn, WORLD, nation_id=THEM, turn=1, channel="reinforce", reason="two")
    refusals.record(conn, WORLD, nation_id=ME, turn=2, channel="build_orders", reason="three")
    conn.commit()

    assert [r.reason for r in refusals.since(conn, WORLD, ME, turn=1)] == ["one"]
    assert [r.reason for r in refusals.since(conn, WORLD, THEM, turn=1)] == ["two"]
    assert [r.reason for r in refusals.since(conn, WORLD, ME, turn=2)] == ["three"]


def test_an_empty_reason_is_not_kept(conn):
    """A blank line in a briefing teaches nothing and costs the same tokens as a real one."""
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="reinforce", reason="")
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="reinforce", reason="   ")
    conn.commit()
    assert refusals.since(conn, WORLD, ME, turn=1) == []


def test_a_world_written_before_the_table_is_not_crashed_by_one(conn):
    """The pre-migration guarantee every layer since schema 14 makes for itself."""
    conn.execute("DROP TABLE refusal")
    conn.commit()

    assert refusals.table_present(conn) is False
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="reinforce", reason="x")
    assert refusals.since(conn, WORLD, ME, turn=1) == []
    assert refusals.briefing_lines(conn, WORLD, ME) == []


# --------------------------------------------------------------------------- the block


def _played(conn, turn: int) -> None:
    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
    conn.commit()


def test_the_block_is_absent_when_the_last_turn_refused_nothing(conn):
    """A diagnostic block should be absent unless it has something. Most turns it does not."""
    _played(conn, 3)
    assert refusals.briefing_lines(conn, WORLD, ME) == []


def test_the_block_is_absent_on_the_first_turn(conn):
    _played(conn, 0)
    refusals.record(conn, WORLD, nation_id=ME, turn=0, channel="reinforce", reason="x")
    conn.commit()
    assert refusals.briefing_lines(conn, WORLD, ME) == []


def test_the_block_reads_the_turn_just_gone_and_nothing_older(conn):
    """A refusal from six turns ago that the power stopped repeating is a grudge, not a lesson."""
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="war_aims", reason="ancient")
    refusals.record(conn, WORLD, nation_id=ME, turn=4, channel="reinforce", reason="recent")
    conn.commit()
    _played(conn, 4)

    block = "\n".join(refusals.briefing_lines(conn, WORLD, ME))
    assert "recent" in block
    assert "ancient" not in block


def test_a_repeated_refusal_is_counted_rather_than_repeated(conn):
    """Four regions refused for one reason is one lesson, not four lines of block."""
    for _ in range(4):
        refusals.record(conn, WORLD, nation_id=ME, turn=1,
                        channel="military_objectives", reason="you cannot reach it")
    conn.commit()
    _played(conn, 1)

    body = [line for line in refusals.briefing_lines(conn, WORLD, ME)
            if line.startswith("- ")]
    assert len(body) == 1
    assert "said 4 times" in body[0]


def test_the_block_names_the_field_that_was_refused(conn):
    """"You were refused" teaches nothing; the channel is what points at the fix."""
    refusals.record(conn, WORLD, nation_id=ME, turn=1, channel="trade_intents",
                    reason="there is no power 'zaf' to trade with")
    conn.commit()
    _played(conn, 1)

    block = "\n".join(refusals.briefing_lines(conn, WORLD, ME))
    assert "`trade_intents`" in block
    assert "zaf" in block


def test_a_bad_turn_does_not_become_the_whole_briefing(conn):
    """Truncated, and the remainder is counted rather than dropped."""
    for index in range(refusals.MAX_SHOWN + 4):
        refusals.record(conn, WORLD, nation_id=ME, turn=1,
                        channel=f"channel_{index}", reason=f"reason {index}")
    conn.commit()
    _played(conn, 1)

    lines = refusals.briefing_lines(conn, WORLD, ME)
    body = [line for line in lines if line.startswith("- ")]
    assert len(body) == refusals.MAX_SHOWN + 1
    assert "4 further refusal(s)" in body[-1]


def test_one_government_never_reads_another_governments_refusals(conn):
    """The whole layer is per nation, and this is the asymmetry rule it lives under."""
    refusals.record(conn, WORLD, nation_id=THEM, turn=1, channel="reinforce",
                    reason="karthos had nothing to reinforce")
    conn.commit()
    _played(conn, 1)

    assert refusals.briefing_lines(conn, WORLD, ME) == []
    assert refusals.briefing_lines(conn, WORLD, THEM)


# --------------------------------------------------------------------------- the rewind


def test_a_rewound_refusal_stops_being_read_back(conn):
    """A world put back to turn 2 must not open by naming what was refused on turn 4."""
    for turn in (2, 4):
        conn.execute(
            "INSERT INTO turn (world_id, number, start_date, end_date, status, phase)"
            " VALUES (?, ?, '1897-01-01', '1897-02-01', 'sealed', 'sealed')",
            (WORLD, turn))
        refusals.record(conn, WORLD, nation_id=ME, turn=turn, channel="reinforce",
                        reason=f"refused on turn {turn}")
    _played(conn, 4)

    assert rewind_to(conn, WORLD, 2).problems == []

    assert refusals.since(conn, WORLD, ME, turn=4) == []
    assert [r.reason for r in refusals.since(conn, WORLD, ME, turn=2)] == [
        "refused on turn 2"]


def test_a_rewind_works_on_a_world_older_than_the_table(conn):
    """A rewind is what a player reaches for when something has gone wrong."""
    conn.execute(
        "INSERT INTO turn (world_id, number, start_date, end_date, status, phase)"
        " VALUES (?, 2, '1897-01-01', '1897-02-01', 'sealed', 'sealed')", (WORLD,))
    conn.execute("DROP TABLE refusal")
    _played(conn, 4)

    assert rewind_to(conn, WORLD, 2).problems == []  # must not raise


# ------------------------------------------------------- U1: the reinforcement menu


@pytest.fixture
def army(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Six formations of a kind whose establishment is 7, held by the player's nation."""
    conn.execute(
        "INSERT INTO unit_template (world_id, kind, domain, base_strength, move_range,"
        "                           cost_treasury, cost_manpower)"
        " VALUES (?, 'infantry', 'land', 7.0, 2, 8.0, 8.0)", (WORLD,))
    conn.execute("UPDATE nation SET treasury = 500, manpower = 500 WHERE id = ?", (ME,))
    conn.commit()
    return conn


def _place(conn, strengths: list[float], region: str = "r_north") -> None:
    for index, strength in enumerate(strengths):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, ?, ?, 'infantry', ?, 60)",
            (f"u_{index}", WORLD, ME, region, strength))
    conn.commit()


def test_an_army_a_fraction_under_establishment_has_nothing_to_replace(army):
    """The exact state that cost the United States nine turns on the tape.

    Six formations 0.1 below establishment *between them* are 0.017 short each. The
    handler floors an allowance to two decimals and skips anything at 0.01 or under, so
    every one of the six is skipped -- while the old briefing summed the shortfall over the
    whole kind, found 0.1, and invited the order anyway.
    """
    _place(army, [7.0 - 0.017] * 6)

    what = replaceable_now(army, WORLD, ME)
    assert what.formations == 0
    assert what.blocked_too_thin == 6

    # And the handler agrees, which is the point.
    effects = apply_order_effects(
        army, WORLD, turn=1, date="1897-02-01",
        actions=[{"type": "reinforce", "summary": "send up replacements"}], nation_id=ME)
    assert effects.applied == 0
    assert any("nothing to reinforce" in p for p in effects.problems)


def test_the_menu_says_so_rather_than_inviting_the_order(army):
    """A staff shown a shortfall and no reason it cannot be made good spends the order."""
    _place(army, [7.0 - 0.017] * 6)

    text = build_tracked_agent_view(army, WORLD, nation_id=ME).text
    assert "`reinforce` would do nothing this turn" in text
    assert "short by too little to be worth a draft" in text
    assert "Set `reinforce` to true:" not in text


def test_a_real_shortfall_is_still_invited_and_quantified(army):
    """The other half: when the order would work, the menu says how much it would do."""
    _place(army, [3.0, 3.0])

    what = replaceable_now(army, WORLD, ME)
    assert what.formations == 2
    assert what.points == pytest.approx(2 * (7.0 * REINFORCE_MAX_FRACTION // 0.01) / 100.0)

    text = build_tracked_agent_view(army, WORLD, nation_id=ME).text
    assert "2 formation(s) could take" in text
    assert "Set `reinforce` to true:" in text


def test_the_reinforcement_menu_matches_the_reinforcement_handler(army):
    """The guarantee that matters: the two must never again answer different questions.

    Runs the menu's helper and the real handler over the same worlds and fails if one says
    the order would do something and the other refuses it. `_reinforce` is not refactored
    to call the helper -- it spends a purse down across formations in order and narrows by
    a kind or region a player may have named, and folding that into a display helper would
    put budget sequencing inside a menu. This is what keeps them honest instead.
    """
    for strengths in ([7.0 - 0.017] * 6, [3.0, 3.0], [7.0, 7.0], [6.98], [0.5, 6.999]):
        conn = army
        conn.execute("DELETE FROM unit WHERE world_id = ?", (WORLD,))
        conn.commit()
        _place(conn, strengths)

        predicted = replaceable_now(conn, WORLD, ME).formations
        effects = OrderEffects()
        from atlas.engine.orders import _reinforce

        _reinforce(conn, WORLD, 1, "1897-02-01", {"type": "reinforce"}, ME, effects)
        happened = effects.applied > 0

        assert bool(predicted) == happened, (
            f"{strengths}: menu predicted {predicted} formation(s), "
            f"handler {'acted' if happened else 'refused'}")


# ------------------------------------------------- U2 and U3: two menus that named no source


def test_the_trade_menu_says_where_a_partner_id_comes_from(army):
    """25 of 100 partners ever named were ids absent from the naming model's own prompt.

    Every other id-taking field on this form points at its source and is obeyed: 0 of 822
    `push` objectives and 3 of 1,235 build orders named something the prompt did not
    contain. This one said only "the nation id to trade with".
    """
    from atlas.engine import consent, pricing

    if not pricing.enabled():
        pytest.skip("a barter world is never offered the channel at all")

    # One standing intention, so the block renders. The wording under test is printed
    # whenever the block is -- a government that has never asked for one is the reader who
    # most needs telling -- but the block itself is empty for a power with no trade and no
    # intentions, which is by design and is why this has to be seeded.
    army.execute(
        "INSERT INTO trade_intent (id, world_id, nation_id, kind, good, partner_id,"
        "                          opened_turn, status, note)"
        " VALUES ('i1', ?, ?, 'seek', 'oil', '', 1, 'standing', '')", (WORLD, ME))
    army.commit()

    block = "\n".join(consent.briefing_lines(army, WORLD, ME))
    assert block, "the block should render once an intention stands"
    assert "The other powers, as you see them" in block
    assert "required only for `diversify`" in block
    # And the refusal a standing list invites by existing.
    assert "Do not restate one" in block


def test_the_forces_menu_says_the_yards_are_the_building_queue(army):
    """A power shown "0 free" in one menu and "in the yards" in another loses the order.

    The Soviet Union did exactly that on turn 8 of pt77: shown "4 of 4 slots working, 0
    free" it correctly ordered nothing to build, then ordered three infantry against the
    same counter.
    """
    _place(army, [7.0, 7.0])
    text = build_tracked_agent_view(army, WORLD, nation_id=ME).text
    assert "same queue as your public works and research" in text
    assert "a division laid down is a steelworks you did not build" in text


# ------------------------------------------------------------------- the loop, closed


def test_a_refusal_from_a_real_turn_reaches_the_next_turns_briefing(army):
    """End to end, through the turn engine, which is where the loop was actually open.

    Everything above tests the store and the block in isolation; this is the one that
    fails if the wiring between them is missing -- which is exactly the state the engine
    was in. `_refused` is the single door, so a site that goes back to appending straight
    to `report.rejections` shows up here rather than in a replay six months later.
    """
    from atlas.engine.turn_engine import JumpReport, TurnEngine
    from atlas.state.models import TurnStatus

    report = JumpReport(
        turn=1, status=TurnStatus.SEALED, days_advanced=30, new_date="1897-02-01")
    engine = TurnEngine(conn=army, world_id=WORLD, providers={}, router=None)
    engine._refused(
        report, nation_id=ME, name="Aurelia", turn=1, channel="trade_intents",
        reasons=["there is no power 'zaf' to trade with"],
    )
    army.commit()

    # Still in the player's turn report, exactly as before -- this adds a reader, it does
    # not move one.
    assert report.rejections == ["Aurelia: there is no power 'zaf' to trade with"]

    _played(army, 1)
    text = build_tracked_agent_view(army, WORLD, nation_id=ME).text
    assert "## What you asked for last turn and did not get" in text
    assert "`trade_intents`" in text
    assert "zaf" in text

    # ...and it is this nation's alone.
    other = build_tracked_agent_view(army, WORLD, nation_id=THEM).text
    assert "## What you asked for last turn and did not get" not in other


def test_a_refusal_with_no_nation_is_not_kept_but_is_still_reported(army):
    """The turn engine has refusal sites that belong to the world rather than to anybody."""
    from atlas.engine.turn_engine import JumpReport, TurnEngine
    from atlas.state.models import TurnStatus

    report = JumpReport(
        turn=1, status=TurnStatus.SEALED, days_advanced=30, new_date="1897-02-01")
    engine = TurnEngine(conn=army, world_id=WORLD, providers={}, router=None)
    engine._refused(report, nation_id="", name="", turn=1, channel="diffs",
                    reasons=["the world would not carry it"])
    army.commit()

    assert report.rejections == ["the world would not carry it"]
    assert refusals.since(army, WORLD, ME, turn=1) == []


def test_the_two_numbers_on_the_reinforcement_line_can_never_cross(army):
    """"2.1 below establishment, and 2 formations could take 2.9 back" is not a sentence.

    The old headline was a per-*kind* net, so a kind's over-strength formations cancelled
    its under-strength ones and the shortfall could print smaller than what the order would
    actually put back. Counted per formation the two are ordered by construction, and this
    is the property rather than the example: what can be replaced is a subset of what is
    short, so it can never exceed it.
    """
    for strengths in ([3.0, 10.0], [7.0 - 0.017] * 6, [0.5, 6.99, 12.0], [1.0]):
        army.execute("DELETE FROM unit WHERE world_id = ?", (WORLD,))
        army.commit()
        _place(army, strengths)

        what = replaceable_now(army, WORLD, ME)
        assert what.points <= what.short_points + 1e-9, strengths
        assert what.formations <= what.short_formations, strengths
        assert what.formations + what.blocked == what.short_formations, strengths
