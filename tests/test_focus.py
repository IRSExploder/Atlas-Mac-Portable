"""Focus Mode: ranking, the hard cap, and promotion without personality whiplash."""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.eve import Bloc, assign_blocs
from atlas.engine.focus import (
    FOCUS_CAP,
    MERIT_SEATS,
    FocusCandidate,
    apply_focus,
    choose_roster,
    rank_candidates,
    tracked_nations,
)
from atlas.engine.parser import ACTION_SCHEMA, parse_orders
from atlas.engine.reference import resolve
from atlas.engine.routing import Router
from atlas.engine.turn_engine import TurnEngine
from atlas.engine.views import build_tracked_agent_view
from atlas.providers.cli_provider import default_role_models
from atlas.providers.errors import FailureKind
from atlas.providers.mock import MockProvider, ScriptedResponse, always, cycle


def _at_war(conn, a: str, b: str) -> None:
    conn.execute("UPDATE relation SET at_war = 1 WHERE nation_a = ? AND nation_b = ?", (a, b))
    conn.execute("UPDATE relation SET at_war = 1 WHERE nation_a = ? AND nation_b = ?", (b, a))
    conn.commit()


def _crowd(conn, count: int) -> None:
    """Add extra nations, each at war with the player, so the cap is genuinely pressured."""
    for index in range(count):
        nation = f"x{index}"
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour) VALUES (?, 'sandbox', ?, '#888888')",
            (nation, f"Power {index}"),
        )
        for other in ("aurelia",):
            conn.execute(
                "INSERT INTO relation (world_id, nation_a, nation_b, score, at_war) "
                "VALUES ('sandbox', ?, ?, 0, 1)",
                (nation, other),
            )
            conn.execute(
                "INSERT INTO relation (world_id, nation_a, nation_b, score, at_war) "
                "VALUES ('sandbox', ?, ?, 0, 1)",
                (other, nation),
            )
    conn.commit()


# --------------------------------------------------------------------------- ranking


def test_war_with_the_player_outranks_everything(conn):
    _at_war(conn, "karthos", "aurelia")
    ranked = rank_candidates(conn, "sandbox", player_nation="aurelia")
    assert ranked[0].nation_id == "karthos"
    assert "at war with you" in ranked[0].reasons


def test_a_bordering_nation_outranks_a_distant_one(conn):
    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    # Veyra borders Aurelia; Karthos does not.
    assert ranked["veyra"].score > 0
    assert "borders your territory" in ranked["veyra"].reasons


def test_every_candidate_carries_its_reason(conn):
    """The player is shown why a nation is tracked, so it reads as design not caprice."""
    _at_war(conn, "karthos", "aurelia")
    for candidate in rank_candidates(conn, "sandbox", player_nation="aurelia"):
        if candidate.score > 0:
            assert candidate.reasons
            assert candidate.primary_reason


def test_ranking_is_deterministic(conn):
    """No randomness and no model call: the same world gives the same roster."""
    _at_war(conn, "karthos", "aurelia")
    first = [(c.nation_id, c.score) for c in rank_candidates(conn, "sandbox", player_nation="aurelia")]
    second = [(c.nation_id, c.score) for c in rank_candidates(conn, "sandbox", player_nation="aurelia")]
    assert first == second


def test_a_microstate_does_not_outrank_a_great_power_for_sharing_a_border(conn):
    """Proximity is scaled by standing; war is not.

    Unscaled, "borders your territory" scores identically for Andorra and for Germany,
    and a dedicated agent goes to the microstate while the real neighbour is simulated in
    bulk. Observed on the real Earth map, where Andorra was tracked as a great power.
    """
    conn.execute("UPDATE nation SET military_strength = 2, economy = 1 WHERE id = 'veyra'")
    conn.execute("UPDATE nation SET military_strength = 80, economy = 80 WHERE id = 'karthos'")
    # Give both a border with the player so proximity is the only difference.
    conn.execute(
        "INSERT OR IGNORE INTO region_adjacency (world_id, region_a, region_b) "
        "VALUES ('sandbox', 'r_coast', 'r_south')"
    )
    conn.commit()

    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["karthos"].score > ranked["veyra"].score


def test_a_power_fighting_you_outranks_one_that_merely_borders_you(conn):
    """Provinces made this distinction possible and then necessary.

    Before them a war was a flag on a relation, so a neighbour who had declared and done
    nothing ranked level with one whose army was three provinces into your territory.
    Now there is ground being fought over, and the roster should reflect it.
    """
    _at_war(conn, "karthos", "aurelia")
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn)"
        " VALUES ('k1', 'sandbox', 'r_north', 'karthos', 'aurelia', 0.5, 1, 1)"
    )
    conn.commit()

    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["karthos"].score > ranked["veyra"].score
    assert ranked["karthos"].primary_reason.startswith("fighting you")


def test_a_war_on_paper_ranks_below_a_war_being_fought(conn):
    """Both are wars. Only one of them is where the player's attention should go."""
    _at_war(conn, "karthos", "aurelia")
    _at_war(conn, "veyra", "aurelia")
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn)"
        " VALUES ('k1', 'sandbox', 'r_north', 'karthos', 'aurelia', 0.5, 1, 1)"
    )
    conn.commit()

    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["karthos"].score > ranked["veyra"].score


def test_a_microstate_fighting_you_still_outranks_a_quiet_great_power(conn):
    """Fighting is not scaled by standing, for the same reason war never was."""
    conn.execute("UPDATE nation SET military_strength = 2, economy = 1 WHERE id = 'veyra'")
    conn.execute("UPDATE nation SET military_strength = 80, economy = 80 WHERE id = 'karthos'")
    _at_war(conn, "veyra", "aurelia")
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn)"
        " VALUES ('v1', 'sandbox', 'r_north', 'veyra', 'aurelia', 0.7, 1, 1)"
    )
    conn.commit()

    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["veyra"].score > ranked["karthos"].score


def test_war_is_not_scaled_by_size(conn):
    """A small nation at war with you still demands attention."""
    conn.execute("UPDATE nation SET military_strength = 1, economy = 1 WHERE id = 'veyra'")
    conn.commit()
    _at_war(conn, "veyra", "aurelia")

    ranked = {c.nation_id: c for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["veyra"].score >= 100.0
    assert "at war with you" in ranked["veyra"].reasons


def test_the_player_is_never_a_candidate(conn):
    ids = {c.nation_id for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert "aurelia" not in ids


def test_a_bloc_nation_is_never_a_candidate(conn):
    """An EVE bloc plays its own nations; Focus calling them too would blend two families.

    The positive control matters: without it this passes on a world where the nation was
    never going to rank at all.
    """
    _at_war(conn, "karthos", "aurelia")
    before = {c.nation_id for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert "karthos" in before, "a nation at war with the player must rank to begin with"

    assign_blocs(conn, "sandbox", [Bloc(
        id="bloc_b", name="Bloc B", provider_key="codex", model="gpt-5.6-terra",
        colour="#888888", nations=("karthos",),
    )])
    after = {c.nation_id for c in rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert "karthos" not in after
    assert after == before - {"karthos"}, "and nobody else is removed with it"


# --------------------------------------------------------------------------- the cap


def test_the_cap_holds_when_everyone_qualifies(conn):
    """The moment Focus Mode matters most is exactly when a heuristic would overrun it.

    Eight nations at war with the player all qualify; only five may be tracked.
    """
    _crowd(conn, 8)
    promoted, _ = apply_focus(conn, "sandbox", player_nation="aurelia")
    assert len(promoted) == FOCUS_CAP
    assert len(tracked_nations(conn, "sandbox")) == FOCUS_CAP


def test_the_database_refuses_a_sixth_even_if_code_tries(conn):
    """A ceiling that exists only in code is not a ceiling."""
    _crowd(conn, 8)
    apply_focus(conn, "sandbox", player_nation="aurelia")
    with pytest.raises(sqlite3.IntegrityError, match="focus cap"):
        conn.execute("UPDATE nation SET is_tracked = 1 WHERE id = 'karthos'")


def test_a_more_relevant_nation_displaces_a_less_relevant_one(conn):
    """Demotion happens before promotion, or the trigger would abort a full roster."""
    _crowd(conn, 5)
    apply_focus(conn, "sandbox", player_nation="aurelia")
    assert len(tracked_nations(conn, "sandbox")) == FOCUS_CAP

    # Karthos declares war: it now outranks a merely-bordering power.
    _at_war(conn, "karthos", "aurelia")
    for index in range(3):
        conn.execute(
            "UPDATE relation SET at_war = 0 WHERE nation_a = ? OR nation_b = ?",
            (f"x{index}", f"x{index}"),
        )
    conn.commit()

    promoted, demoted = apply_focus(conn, "sandbox", player_nation="aurelia")
    assert "karthos" in promoted
    assert demoted
    assert len(tracked_nations(conn, "sandbox")) <= FOCUS_CAP


def test_a_quiet_world_tracks_nobody_it_does_not_need_to(conn):
    """Filling five slots in a peaceful world would spend calls on powers doing nothing."""
    conn.execute("DELETE FROM region_adjacency")
    conn.commit()
    promoted, _ = apply_focus(conn, "sandbox", player_nation="aurelia")
    assert len(promoted) < FOCUS_CAP


def test_the_roster_is_stable_between_turns(conn):
    """Without a hysteresis bias, near-tied nations swap every turn for no reason."""
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    first = {t["id"] for t in tracked_nations(conn, "sandbox")}

    promoted, demoted = apply_focus(conn, "sandbox", player_nation="aurelia")
    assert not promoted and not demoted
    assert {t["id"] for t in tracked_nations(conn, "sandbox")} == first


def test_demotion_clears_the_recorded_reason(conn):
    _crowd(conn, 6)
    apply_focus(conn, "sandbox", player_nation="aurelia")
    conn.execute("UPDATE relation SET at_war = 0")
    conn.execute("DELETE FROM region_adjacency")
    conn.commit()
    apply_focus(conn, "sandbox", player_nation="aurelia")

    leftovers = conn.execute(
        "SELECT COUNT(*) FROM nation WHERE is_tracked = 0 AND tracked_reason IS NOT NULL"
    ).fetchone()[0]
    assert leftovers == 0


# --------------------------------------------------------------------------- personality


def test_every_nation_has_a_character_before_it_is_ever_promoted(conn):
    """Promotion means "gets its own call", not "invents who it is".

    Without this, a background power acquires a personality the moment it becomes
    important, and the player watches it change character mid-game.
    """
    rows = conn.execute(
        "SELECT id, personality_blurb, standing_goals_json FROM nation WHERE world_id='sandbox'"
    ).fetchall()
    for row in rows:
        assert row["personality_blurb"], f"{row['id']} has no character"
        assert row["standing_goals_json"] != "[]", f"{row['id']} has no goals"


def test_a_tracked_agents_view_is_scoped_like_any_other(conn):
    """Being tracked buys attention, not privilege or extra knowledge.

    A nation knows its own figures exactly and its rivals' only as bands. The rival's
    strength is set to a distinctive value here so the assertion cannot accidentally
    match the viewer's own numbers.
    """
    conn.execute("UPDATE nation SET military_strength = 77.7 WHERE id = 'aurelia'")
    conn.commit()

    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "You are Karthos" in view.text
    assert view.viewer == "karthos"

    assert "32.0" in view.text, "a nation knows its own strength exactly"
    assert "77.7" not in view.text, "a rival's exact strength must not appear"
    assert "stronger than you" in view.text, "rivals are described in bands"


def test_a_tracked_agent_is_told_it_cannot_decide_outcomes(conn):
    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "never decide who wins a battle" in view.text
    assert "the engine resolves all of that" in view.text


def test_a_tracked_agent_is_given_the_ids_it_must_cite(conn):
    """Diffs address rows by id, and the briefing spoke only in names.

    A nation shown "Karthos" and "Aurelia" writes the relation as `Karthos|Aurelia`,
    which INFO-003 refuses as belonging to somebody else -- so the nation loses its whole
    turn for acting on its own behalf under the wrong name.
    """
    _at_war(conn, "karthos", "aurelia")
    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "`karthos`" in view.text, "a nation must know its own id"
    assert "`aurelia`" in view.text, "and the ids of the powers it can act toward"
    assert "karthos|" in view.text, "and how a relation row is addressed"


def test_a_tracked_agent_is_offered_a_citable_event_of_last_resort(conn):
    """Models invent `reason_event_id`s for changes with no clean cause; give them a real one.

    The real-model runs lost ~5-6 diffs a game to fabricated citations. The briefing now
    surfaces the founding event id as an always-valid fallback and warns that an invented id
    takes its whole diff down -- the decision-11/21 discoverability pattern for CORE-001.
    """
    from atlas.engine.views import _founding_event_id

    fid = _founding_event_id(conn, "sandbox")
    assert fid is not None
    text = build_tracked_agent_view(conn, "sandbox", nation_id="karthos").text
    assert f"`[{fid}]`" in text, "the always-citable founding id is named"
    assert "does not exist to the" in text, "and inventing one is warned against"


def test_the_world_sim_is_offered_a_citable_event_of_last_resort(conn):
    """The referee gets the same fallback: cite the founding event, never a fabricated id."""
    from atlas.engine.views import _founding_event_id, build_world_sim_view

    fid = _founding_event_id(conn, "sandbox")
    text = build_world_sim_view(conn, "sandbox").text
    assert f"`[{fid}]`" in text
    assert "does not exist is rejected and takes its whole diff" in text


def test_the_advertised_founding_event_is_genuinely_citable(conn):
    """Guard against advertising an id the rules would then reject.

    The briefings promise the founding event is always citable; this proves it -- a batch
    that cites it for an ordinary change applies cleanly (CORE-001 and INFO-001/002 pass).
    """
    from atlas.engine.views import _founding_event_id
    from atlas.state.apply import apply_batch as _apply
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, Role, Visibility

    fid = _founding_event_id(conn, "sandbox")
    batch = DiffBatch(
        world_id="sandbox", turn=0, proposer_role=Role.WORLD_SIM, proposer_model=None,
        diffs=(Diff("nation", "karthos", "economy", DiffOp.ADJUST, 1.0,
                    reason_event_id=fid, visibility_scope=Visibility.PUBLIC),),
    )
    assert _apply(conn, batch).applied, "the founding event the view advertises must be citable"


def test_an_overseas_belligerent_is_told_how_to_press_its_offensive(conn):
    """A war across water yields no land front, so the army-orders lever was never shown.

    Regression for the France-1936 real-model run: an overseas belligerent (in that run,
    Italy at war with an Ethiopia it did not then border) was never told it could set a
    `military_objectives` push -- the instruction was gated behind having a land front --
    so its war stalled untouched. A belligerent must always be shown the lever, and its own
    standing offensive across the water surfaced so it has the region id to keep pressing.
    (Italy has since been given its East-African colonies, so it now borders Ethiopia by
    land; the lever must still be shown to any belligerent whose war is across the water.)
    """
    _at_war(conn, "karthos", "aurelia")  # karthos borders no aurelian ground
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn)"
        " VALUES ('k1', 'sandbox', 'r_north', 'karthos', 'aurelia', 0.5, 1, 1)"
    )
    conn.commit()

    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "military_objectives" in view.text, "a belligerent must always see the army lever"
    assert "offensives across the water" in view.text
    # The id of the ground it is already pressing, in the one form every model-facing
    # renderer now uses -- and asserted through the resolver rather than as a literal, so
    # this tests the property the briefing owes a staff rather than a house style.
    assert "r_north" in view.text, "the region id of the ground it is already pressing"
    assert resolve(conn, "sandbox", "region", "Northmark [r_north]").value == "r_north"
    assert "50%" in view.text, "with how far along the attack is"


def test_a_tracked_agent_is_told_the_relation_write_bounds(conn):
    """PT9: tracked agents kept proposing relation writes the guardrails rejected.

    They were never told the range or the per-turn cap. The briefing now states both.
    """
    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "-100" in view.text and "+100" in view.text
    assert "25 points in one turn" in view.text
    assert "same visibility scope" in view.text
    # PT13: a real agent invented a treaty_status outside the closed set and lost its whole
    # batch to the CHECK. The valid values are now named, like the relation bounds above.
    assert "non_aggression" in view.text and "vassal" in view.text


def test_a_tracked_agent_sees_each_events_visibility_scope(conn):
    """PT14: the commonest tracked-agent rejection was a relation change marked more widely
    seen than the event it cited (INFO-002). The model could see the event but not how widely,
    so it guessed. Each event now carries its scope, so a cited cause can be matched.
    """
    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "## What you know has happened" in view.text
    # The genesis event is public, and every event line now shows its scope in parentheses.
    assert "(public)" in view.text


def test_a_nation_at_peace_is_not_told_to_set_military_objectives(conn):
    """The army-orders lever is for belligerents; a nation at peace should not see it."""
    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "military_objectives" not in view.text
    assert "offensives across the water" not in view.text


def test_irrelevant_states_are_counted_not_listed(conn):
    """On a full map this section was 10k characters of neutral microstates.

    Listing all 220 crowded out the handful of powers the turn was about, and measurably
    made the smaller models answer worse. They are still accounted for, as a count.
    """
    _crowd(conn, 40)
    # Make the crowd irrelevant: no war, no border, no treaty, no feeling either way,
    # and weaker than the viewer.
    conn.execute("UPDATE relation SET at_war = 0, score = 0 WHERE nation_a LIKE 'x%'")
    conn.execute("UPDATE relation SET at_war = 0, score = 0 WHERE nation_b LIKE 'x%'")
    conn.execute("UPDATE nation SET military_strength = 0.1 WHERE id LIKE 'x%'")
    conn.execute("UPDATE nation SET military_strength = 90 WHERE id = 'karthos'")
    conn.commit()

    view = build_tracked_agent_view(conn, "sandbox", nation_id="karthos")
    assert "Power 0" not in view.text, "a neutral, distant, weaker state is noise"
    assert "further states are neutral" in view.text, "but it is still accounted for"


def test_a_power_you_care_about_is_always_named(conn):
    """Every reason to name a power is honoured, so nothing relevant is trimmed away."""
    _crowd(conn, 6)
    conn.execute("UPDATE relation SET at_war = 0, score = 0 WHERE nation_a LIKE 'x%'")
    conn.execute("UPDATE relation SET at_war = 0, score = 0 WHERE nation_b LIKE 'x%'")
    conn.execute("UPDATE nation SET military_strength = 0.1 WHERE id LIKE 'x%'")
    conn.execute("UPDATE nation SET military_strength = 90 WHERE id = 'karthos'")
    # _crowd only wires the newcomers to the player, so give karthos its own rows.
    for other in ("x0", "x1", "x2", "x3"):
        for a, b in ((("karthos"), other), (other, "karthos")):
            conn.execute(
                "INSERT OR IGNORE INTO relation (world_id, nation_a, nation_b, score) "
                "VALUES ('sandbox', ?, ?, 0)",
                (a, b),
            )
    # One is hated, one has signed something, one is at war. x3 is none of these.
    conn.execute("UPDATE relation SET score = -55 WHERE nation_a = 'karthos' AND nation_b = 'x0'")
    conn.execute(
        "UPDATE relation SET treaty_status = 'alliance' WHERE nation_a = 'karthos' AND nation_b = 'x1'"
    )
    conn.commit()
    _at_war(conn, "karthos", "x2")

    text = build_tracked_agent_view(conn, "sandbox", nation_id="karthos").text
    for named in ("Power 0", "Power 1", "Power 2"):
        assert named in text
    assert "Power 3" not in text


# --------------------------------------------------------------------------- turn loop


def _engine(conn, provider) -> TurnEngine:
    """A turn engine for measuring the *tracked-agent* call accounting.

    Gambits are off here. They are a second model call on a different channel, and
    letting them count would mean these tests silently measured two things at once and
    failed whenever either moved -- which is exactly what happened when the channel was
    added. What a gambit costs is pinned in its own tests.
    """
    return TurnEngine(
        conn=conn,
        world_id="sandbox",
        providers={k: provider for k in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()),
        gambits_per_turn=0,
    )


async def test_tracked_nations_each_get_their_own_call(conn):
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    tracked = len(tracked_nations(conn, "sandbox"))

    provider = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "The chancelleries stir.",
        "events": [{"kind": "narrative", "text": "An envoy is dispatched."}],
    })))
    report = await _engine(conn, provider).jump(days=30)

    # One world simulation plus one call per tracked nation.
    assert report.calls == 1 + tracked
    assert len(report.tracked_acted) == tracked


async def test_the_routing_log_says_whose_government_each_call_was_for(conn):
    """A row that says a model answered, and not who it was playing, cannot be audited.

    A tracked agent's row names its nation; the world simulator speaks for nobody, and an
    empty id is the truthful answer for it rather than a missing one.
    """
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    tracked = {nation["id"] for nation in tracked_nations(conn, "sandbox")}
    assert tracked, "this test is meaningless without a tracked nation"

    provider = MockProvider(script=always(ScriptedResponse(data={
        "events": [{"kind": "narrative", "text": "An envoy is dispatched."}],
    })))
    await _engine(conn, provider).jump(days=30)

    by_role: dict[str, list[str]] = {}
    for row in conn.execute("SELECT role, nation_id FROM routing_log"):
        by_role.setdefault(row["role"], []).append(row["nation_id"])
    assert sorted(by_role["tracked_agent"]) == sorted(tracked)
    assert set(by_role["world_sim"]) == {""}


async def test_a_nation_that_proposes_nothing_is_not_reported_as_acting(conn):
    """The report that hid a dead subsystem for a whole game.

    An empty batch applies cleanly -- there is nothing in it to refuse -- so five nations
    doing nothing every turn read as five nations taking their turn. The turn engine now
    distinguishes acting from being asked.

    It also distinguishes both from *failing*. Reporting a quiet month as a failure marked
    every turn of a peaceful world partial and buried the real failures under five lines a
    turn, which a sixteen-turn playthrough made unmissable. The signal survives; it just
    has its own list.
    """
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    tracked = len(tracked_nations(conn, "sandbox"))
    assert tracked, "this test is meaningless without a tracked nation"

    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": [], "events": []})))
    report = await _engine(conn, provider).jump(days=30)

    assert report.calls == 1 + tracked, "they were still asked"
    assert report.tracked_acted == [], "but none of them did anything"
    assert len(report.tracked_quiet) == tracked, (
        "and the player is told, rather than the turn looking healthy"
    )
    assert not any("nothing to act on" in f for f in report.failures), (
        "without a quiet month counting as something going wrong"
    )


async def test_a_tracked_agent_failure_does_not_lose_the_turn(conn):
    """The world simulation is already committed by then.

    Discarding an expensive, successful simulation because an optional extra failed would
    be the worst possible trade.
    """
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")

    provider = MockProvider(script=cycle([
        ScriptedResponse(data={"diffs": [], "events": []}),          # world sim succeeds
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),          # tracked agents fail
    ]))
    report = await _engine(conn, provider).jump(days=30)

    assert report.applied_batches >= 1, "the world simulation must be kept"
    assert report.failures, "the tracked-agent failure must be reported"
    assert conn.execute("SELECT turn_number FROM world").fetchone()[0] == 1


async def test_a_tracked_agent_is_bound_by_the_same_rules(conn):
    """Being tracked does not exempt a nation from validation."""
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    genesis = conn.execute("SELECT id FROM event WHERE kind='genesis'").fetchone()["id"]

    provider = MockProvider(script=cycle([
        ScriptedResponse(data={"diffs": [], "events": []}),
        ScriptedResponse(data={"diffs": [{
            "target_table": "region", "target_id": "r_north", "field": "owner_nation_id",
            "op": "set", "new_value": "karthos", "reason_event_id": genesis,
            "visibility_scope": "public"}]}),
    ]))
    report = await _engine(conn, provider).jump(days=30)

    assert report.rejected_batches >= 1
    assert any("CORE-003" in reason for reason in report.rejections)
    assert conn.execute(
        "SELECT owner_nation_id FROM region WHERE id='r_north'"
    ).fetchone()[0] == "aurelia"


async def test_a_tracked_agent_builds_its_economy(conn):
    """A tracked power develops itself, not just fights.

    Decision B: without a build channel only the human ever industrialised and the AI
    world stood frozen. A tracked agent now names public works in `build_orders`, and the
    engine runs each through the same order handler the player uses -- charging its
    treasury and occupying its queue -- so its industry actually rises.
    """
    _at_war(conn, "karthos", "aurelia")
    apply_focus(conn, "sandbox", player_nation="aurelia")
    conn.execute("UPDATE nation SET treasury = 200, industry = 40 WHERE id = 'karthos'")
    # Stocked and supplied for the industry it has just been given. The sandbox seeds five
    # months of demand at industry 12, and demand scales with industry, so a country raised
    # to 40 and left with the old rows runs dry inside six turns -- and the sandbox has no
    # trade routes to refill it. That is a real shortage and `advance_construction` now
    # slows the yards for it; this test is about whether a tracked agent develops itself,
    # so it is given the materials rather than the shortage.
    conn.execute(
        "UPDATE resource SET stockpile = 40 * 0.11 * 5, produced = 40 * 0.11"
        " WHERE world_id = 'sandbox' AND nation_id = 'karthos'"
    )
    conn.commit()
    before = conn.execute("SELECT industry FROM nation WHERE id='karthos'").fetchone()[0]

    def script(request):
        from atlas.state.models import Role
        if request.role is Role.TRACKED_AGENT:
            return ScriptedResponse(data={
                "diffs": [], "build_orders": [{"project": "workshops"}]})
        return ScriptedResponse(data={"diffs": []})

    report = await _engine(conn, MockProvider(script=script)).jump(days=30)

    assert "Karthos" in report.tracked_built
    # The order took a queue slot and was charged; the works is under construction.
    assert conn.execute(
        "SELECT COUNT(*) FROM construction WHERE nation_id='karthos'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT treasury FROM nation WHERE id='karthos'"
    ).fetchone()[0] < 200, "the build was paid for"

    # Run it out to completion and the industry has actually grown.
    for _ in range(5):
        await _engine(conn, MockProvider(script=script)).jump(days=30)
    after = conn.execute("SELECT industry FROM nation WHERE id='karthos'").fetchone()[0]
    assert after > before


async def test_focus_is_recomputed_after_the_world_moves(conn):
    """A war that starts this turn should be reflected in next turn's roster."""
    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": [], "events": []})))
    report = await _engine(conn, provider).jump(days=30)
    assert report.promoted or report.demoted or tracked_nations(conn, "sandbox")


# --------------------------------------------------------------------------- parser


def test_the_action_schema_cannot_express_an_outcome():
    """An action is an *attempt*. Whether it succeeds is resolved later, by the engine."""
    item_fields = set(ACTION_SCHEMA["properties"]["actions"]["items"]["properties"])
    assert not item_fields & {"outcome", "result", "success", "casualties", "winner"}
    assert "rejected" in ACTION_SCHEMA["properties"]


async def test_orders_are_parsed_and_recorded(conn):
    provider = MockProvider(script=always(ScriptedResponse(data={
        "actions": [{"type": "diplomatic_message", "summary": "Warn Karthos",
                     "target_nation": "karthos", "tone": "firm"}],
        "rejected": [],
    })))
    parsed = await parse_orders(
        conn, "sandbox", nation_id="aurelia", raw_text="Send a firm warning to Karthos",
        providers={"codex": provider}, router=Router(pins=default_role_models()),
    )
    assert parsed.ok
    assert parsed.summaries == ["Warn Karthos"]

    row = conn.execute("SELECT raw_text, validation_status FROM action").fetchone()
    assert row["validation_status"] == "accepted"
    assert "Karthos" in row["raw_text"]


async def test_an_impossible_order_comes_back_with_a_reason(conn):
    """Rejection has to be actionable before the player spends a jump on it."""
    provider = MockProvider(script=always(ScriptedResponse(data={
        "actions": [],
        "rejected": [{"order": "Attack the moon",
                      "reason": "You hold no forces capable of leaving the surface."}],
    })))
    parsed = await parse_orders(
        conn, "sandbox", nation_id="aurelia", raw_text="Attack the moon",
        providers={"codex": provider}, router=Router(pins=default_role_models()),
    )
    assert parsed.rejection_lines
    assert "no forces" in parsed.rejection_lines[0]
    assert conn.execute("SELECT validation_status FROM action").fetchone()[0] == "rejected"


async def test_an_unavailable_parser_says_so(conn):
    parsed = await parse_orders(
        conn, "sandbox", nation_id="aurelia", raw_text="do something",
        providers={}, router=Router(pins=default_role_models()),
    )
    assert not parsed.ok
    assert "unavailable" in (parsed.error or "")


async def test_empty_orders_cost_nothing(conn):
    provider = MockProvider()
    parsed = await parse_orders(
        conn, "sandbox", nation_id="aurelia", raw_text="   ",
        providers={"codex": provider}, router=Router(pins=default_role_models()),
    )
    assert parsed.ok and not parsed.actions
    assert not provider.calls, "an empty order must not spend a call"


# ------------------------------------------------- a war on somebody else's border


def _contest(conn, region: str, attacker: str, defender: str) -> None:
    """Open a contest, which is what 'a war is actually being fought here' looks like."""
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id, pressure)"
        " VALUES (?, 'sandbox', ?, ?, ?, 0.4)",
        (f"c_{region}_{attacker}", region, attacker, defender),
    )
    conn.commit()


def test_a_bystander_next_to_a_war_is_noticed(conn):
    """The nervous neighbour, which no term saw before.

    Every other term asks what a nation is doing to *you* or to a tracked power. A country
    watching an invasion arrive at its frontier did none of those things, stayed in the
    referee's bulk pass, and never got to react in its own voice -- which is exactly the
    nation with the most to say.
    """
    from atlas.engine.focus import wars_on_the_frontier

    before = {c.nation_id: c.score for c in
              rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert before["karthos"] == 0.0, "Karthos starts as a background power"

    # The player invades Veyra's plateau. r_east borders r_north (the player's), r_south
    # and r_west (both Karthos's) -- so Karthos is the bystander watching it happen.
    _at_war(conn, "aurelia", "veyra")
    _contest(conn, "r_east", "aurelia", "veyra")

    frontier = wars_on_the_frontier(conn, "sandbox")
    assert frontier.get("karthos"), "the neighbour should see a war on its border"
    assert "aurelia" not in frontier, "a belligerent is not a bystander"
    assert "veyra" not in frontier, "nor is the defender"

    ranked = {c.nation_id: c for c in
              rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["karthos"].score > before["karthos"], (
        "the power watching a war arrive on its frontier was not noticed"
    )
    assert any("not part of" in reason for reason in ranked["karthos"].reasons), (
        f"and the player should be told why: {ranked['karthos'].reasons}"
    )


def test_a_bystander_scores_below_an_actual_belligerent(conn):
    """The failure mode this term could introduce, pinned.

    A bystander that outranked the powers actually fighting would spend the roster on
    spectators during the exact turn Focus matters most.
    """
    # Veyra is fighting the player; Karthos merely borders the fighting.
    _at_war(conn, "veyra", "aurelia")
    _contest(conn, "r_east", "veyra", "aurelia")

    ranked = {c.nation_id: c.score for c in
              rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["veyra"] > ranked["karthos"], (
        f"bystander {ranked['karthos']} must not outrank belligerent {ranked['veyra']}"
    )


def test_a_declared_war_nobody_is_fighting_promotes_nobody(conn):
    """Keyed on contests, not on `at_war`: a war on paper is not a war on your border.

    Costs one turn of lag once the fighting starts, which is the honest reading -- and is
    the reason this reads the contest table rather than the relation flag.
    """
    from atlas.engine.focus import wars_on_the_frontier

    _at_war(conn, "karthos", "veyra")
    assert wars_on_the_frontier(conn, "sandbox") == {}


def test_the_bystander_term_scales_with_standing(conn):
    """A microstate next to a great-power war does not rank level with a great power.

    Same term, same border, same war -- only the weight differs, which is the whole job
    of `standing`. Without it Andorra gets a dedicated agent for adjoining a war it could
    not affect.
    """
    for nation_id, name, weight in (("speck", "Speck", 1), ("titan", "Titan", 400)):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour, military_strength, economy)"
            " VALUES (?, 'sandbox', ?, '#111', ?, ?)",
            (nation_id, name, weight, weight),
        )
        conn.execute(
            "INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id)"
            " VALUES (?, 'sandbox', ?, ?, 'land', ?)",
            (f"r_{nation_id}", name, f"geo:{nation_id}", nation_id),
        )
        # Sorted, because region_adjacency CHECKs region_a < region_b.
        lo, hi = sorted(("r_east", f"r_{nation_id}"))
        conn.execute(
            "INSERT INTO region_adjacency (world_id, region_a, region_b) VALUES (?, ?, ?)",
            ("sandbox", lo, hi),
        )
    conn.commit()
    _at_war(conn, "karthos", "veyra")
    _contest(conn, "r_east", "karthos", "veyra")

    ranked = {c.nation_id: c.score for c in
              rank_candidates(conn, "sandbox", player_nation="aurelia")}
    assert ranked["speck"] > 0, "the speck did notice"
    assert ranked["titan"] > ranked["speck"], (
        f"same war, same border: titan {ranked['titan']} vs speck {ranked['speck']}"
    )


async def test_a_bystander_can_be_promoted_by_a_war_next_door(conn):
    """End to end: the war starts, and the neighbour gets its own call next turn."""
    conn.execute("UPDATE nation SET is_tracked = 0")
    conn.commit()
    _at_war(conn, "veyra", "aurelia")
    _contest(conn, "r_east", "veyra", "aurelia")

    promoted, _demoted = apply_focus(conn, "sandbox", player_nation="aurelia")
    assert "karthos" in promoted, (
        "the power watching a war arrive on its border was not given a voice"
    )
    reason = conn.execute(
        "SELECT tracked_reason FROM nation WHERE id = 'karthos'"
    ).fetchone()[0]
    assert reason, "and the player can be told why"


# --------------------------------------------------- the spotlight has to travel


def _pool(n: int) -> list[FocusCandidate]:
    return [FocusCandidate(nation_id=f"n{i}", name=f"N{i}", score=100.0 - i, reasons=())
            for i in range(n)]


def test_the_urgent_seats_are_held_and_the_rest_rotate():
    """**A ranked list sliced at a cap names the same nations forever.**

    Scores barely move between turns -- a war is still a war next month -- and the
    already-tracked bias adds eight points to whoever is there, so the top five entrench.
    Measured on Modern Day: five thirty-turn games gave an agent to eight of twenty-four
    powers and never once to the other sixteen.

    The fix cannot be to rotate everything, because that would demote a belligerent in the
    middle of its war. The top seats are held on merit; the rest travel.
    """
    pool = _pool(12)
    first = [c.nation_id for c in choose_roster(pool, cap=5, turn=0)]
    assert first[:MERIT_SEATS] == ["n0", "n1", "n2"], first

    for turn in range(8):
        roster = [c.nation_id for c in choose_roster(pool, cap=5, turn=turn)]
        assert len(roster) == 5
        assert roster[:MERIT_SEATS] == ["n0", "n1", "n2"], f"turn {turn}: {roster}"

    seen = {c.nation_id for turn in range(40)
            for c in choose_roster(pool, cap=5, turn=turn)}
    assert seen == {c.nation_id for c in pool}, sorted(seen)


def test_the_roster_is_a_pure_function_of_the_turn():
    """`apply_focus` writes the roster as diffs so a rewind restores it.

    That only means anything if replaying turn seven *chooses* what turn seven chose. The
    offset is the clock and nothing else -- no counter, no wall clock, no RNG.
    """
    pool = _pool(9)
    for turn in (0, 3, 7, 31):
        once = [c.nation_id for c in choose_roster(pool, cap=5, turn=turn)]
        twice = [c.nation_id for c in choose_roster(pool, cap=5, turn=turn)]
        assert once == twice


def test_a_world_with_fewer_candidates_than_seats_seats_them_all():
    """The rotation is inert until there is somebody to rotate, which is most worlds."""
    pool = _pool(3)
    assert [c.nation_id for c in choose_roster(pool, cap=5, turn=0)] == ["n0", "n1", "n2"]
    assert [c.nation_id for c in choose_roster(pool, cap=5, turn=9)] == ["n0", "n1", "n2"]
    assert choose_roster([], cap=5, turn=2) == []
    assert choose_roster(_pool(4), cap=0, turn=1) == []
