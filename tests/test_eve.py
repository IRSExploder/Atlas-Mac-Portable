"""EVE: bloc assignment, the arbiter, and honest statistics."""

from __future__ import annotations

import pytest

from atlas import cli
from atlas.engine.eve import Bloc, BlocConflict, EveEngine, assign_blocs, family_of, load_blocs
from atlas.engine.routing import Router
from atlas.engine.statistics import collect_match_stats, summarise
from atlas.providers.cli_provider import EVE_BLOC_MODELS, default_role_models
from atlas.providers.errors import FailureKind
from atlas.providers.mock import MockProvider, ScriptedResponse, always, cycle


def _blocs(**overrides) -> list[Bloc]:
    base = {
        "a": ("claude", "claude-sonnet-5", ("aurelia",)),
        "b": ("codex", "gpt-5.6-terra", ("karthos",)),
        "c": ("antigravity", "gemini-3.6-flash-high", ("veyra",)),
    }
    base.update(overrides)
    return [
        Bloc(id=f"bloc_{key}", name=f"Bloc {key.upper()}", provider_key=provider,
             model=model, colour="#888888", nations=nations)
        for key, (provider, model, nations) in base.items()
    ]


# --------------------------------------------------------------------------- families


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-sonnet-5", "claude"),
        ("claude-opus-4-6-thinking", "claude"),
        ("gpt-5.6-terra", "openai"),
        ("gemini-3.6-flash-high", "google"),
    ],
)
def test_model_families_are_recognised(model, family):
    assert family_of(model) == family


def test_the_default_eve_pins_are_three_distinct_families():
    families = {family_of(model) for _provider, model in EVE_BLOC_MODELS.values()}
    assert len(families) == 3


def test_two_blocs_from_one_family_are_refused(conn):
    """agy can route to Claude models, which would collapse the comparison silently.

    Two blocs on one family compares personalities, not priors -- which is the entire
    thing EVE exists to avoid.
    """
    clashing = _blocs(c=("antigravity", "claude-sonnet-4-6", ("veyra",)))
    with pytest.raises(BlocConflict, match="claude"):
        assign_blocs(conn, "sandbox", clashing)


def test_same_family_can_be_requested_deliberately(conn):
    """The accident is far more likely than the intention, so it has to be asked for."""
    clashing = _blocs(c=("antigravity", "claude-sonnet-4-6", ("veyra",)))
    assign_blocs(conn, "sandbox", clashing, allow_same_family=True)
    assert len(load_blocs(conn, "sandbox")) == 3


def test_a_nation_cannot_belong_to_two_blocs(conn):
    overlapping = _blocs(b=("codex", "gpt-5.6-terra", ("aurelia",)))
    with pytest.raises(BlocConflict, match="more than one bloc"):
        assign_blocs(conn, "sandbox", overlapping)


# --------------------------------------------------------------------------- assignment


def test_assignment_records_blocs_and_controllers(conn):
    assign_blocs(conn, "sandbox", _blocs())

    assert conn.execute("SELECT mode FROM world").fetchone()[0] == "eve"
    loaded = {bloc.id: bloc for bloc in load_blocs(conn, "sandbox")}
    assert set(loaded) == {"bloc_a", "bloc_b", "bloc_c"}
    assert loaded["bloc_a"].nations == ("aurelia",)

    controller = conn.execute(
        "SELECT controller FROM nation WHERE id = 'karthos'"
    ).fetchone()[0]
    assert controller == "codex"


def test_reassignment_replaces_rather_than_accumulates(conn):
    assign_blocs(conn, "sandbox", _blocs())
    assign_blocs(conn, "sandbox", _blocs())
    assert len(load_blocs(conn, "sandbox")) == 3


def test_a_dead_nation_leaves_its_bloc(conn):
    assign_blocs(conn, "sandbox", _blocs())
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = 'karthos'")
    conn.commit()
    loaded = {bloc.id: bloc for bloc in load_blocs(conn, "sandbox")}
    assert loaded["bloc_b"].nations == ()


# --------------------------------------------------------------------------- the round


def _engine(conn, provider, **kwargs) -> EveEngine:
    return EveEngine(
        conn=conn,
        world_id="sandbox",
        providers={k: provider for k in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()),
        **kwargs,
    )


async def test_every_bloc_acts_once_per_round(conn):
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "The bloc moves.",
        "events": [{"kind": "narrative", "text": "A statement of intent."}],
    })))
    report = await _engine(conn, provider).run_round()

    assert report.calls == 3
    assert len(report.acted) == 3


async def test_a_silent_bloc_is_not_counted_as_a_participant(conn):
    """A match where one bloc never moves is what this mode exists to detect.

    An empty batch applies cleanly, so a provider returning nothing would otherwise be
    indistinguishable from one that played -- and the statistics would compare three
    blocs when only two were present.
    """
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": [], "events": []})))
    report = await _engine(conn, provider).run_round()

    assert report.calls == 3, "they were still asked"
    assert report.acted == [], "but none of them played"
    assert len(report.failures) == 3


async def test_reconciliation_is_deterministic_code_not_a_combatant(conn):
    """The chairman-is-also-a-debater problem, closed structurally.

    Whose proposals stand is decided by the same validation layer every role passes
    through -- code with no stake in the outcome. No bloc ever judges another.
    """
    assign_blocs(conn, "sandbox", _blocs())
    genesis = conn.execute("SELECT id FROM event WHERE kind='genesis'").fetchone()["id"]

    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": [{
        "target_table": "region", "target_id": "r_south", "field": "owner_nation_id",
        "op": "set", "new_value": "aurelia", "reason_event_id": genesis,
        "visibility_scope": "public"}]})))
    report = await _engine(conn, provider).run_round()

    assert report.rejected >= 1
    assert any("CORE-003" in reason for reason in report.rejections)

    # The arbiter is logged as the engine, never as one of the three players.
    row = conn.execute(
        "SELECT provider, requested_model FROM routing_log WHERE role = 'arbiter'"
    ).fetchone()
    assert row["provider"] == "engine"
    assert row["requested_model"] == "deterministic-validation"


async def test_a_bloc_never_sees_another_blocs_position(conn):
    """Overtures go through state; no bloc reads another's raw output."""
    assign_blocs(conn, "sandbox", _blocs())
    conn.execute("UPDATE nation SET military_strength = 91.5 WHERE id = 'karthos'")
    conn.commit()

    provider = MockProvider(script=always(ScriptedResponse(data={"diffs": []})))
    await _engine(conn, provider).run_round()

    aurelia_prompt = next(
        call.prompt for call in provider.calls if "You are Aurelia" in call.prompt
    )
    assert "91.5" not in aurelia_prompt, "a rival bloc's exact strength must not appear"


async def test_a_failing_bloc_does_not_stop_the_others(conn):
    assign_blocs(conn, "sandbox", _blocs())
    played = {"events": [{"kind": "narrative", "text": "The bloc states its intent."}]}
    # **Three timeouts, not one, because a bloc's call is now retried.** One scripted
    # failure used to mean one failed bloc; since `EveEngine` goes through
    # `invoke_with_retries` it means a bloc that drops once and answers on the retry, which
    # is the whole point of the change and not what this test is about. The first bloc
    # exhausts its attempts, and the question -- do the other two still play? -- is the
    # same one, asked of a failure that is actually final.
    provider = MockProvider(script=cycle([
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(data=played),
        ScriptedResponse(data=played),
    ]))
    report = await _engine(conn, provider).run_round()

    assert report.failures
    assert len(report.acted) == 2, "the other two blocs still played"


async def test_an_unattested_model_is_flagged(conn):
    """agy attests nothing, and EVE's premise depends on knowing who is playing."""
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(
        script=always(ScriptedResponse(data={"diffs": []}, served_model="something-else"))
    )
    report = await _engine(conn, provider).run_round()
    assert report.unverified


async def test_attestation_can_be_required(conn):
    """Opt-in, because requiring it would exclude agy from every match."""
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(
        script=always(ScriptedResponse(data={"diffs": []}, served_model="something-else"))
    )
    report = await _engine(conn, provider, require_attestation=True).run_round()

    assert report.acted == []
    assert any("did not attest" in reason for reason in report.rejections)


# --------------------------------------------------------------------------- attribution
#
# The fixes that made EVE a showcase shipped without tests of their own. Each test here
# pins what its fix exists for -- who played whom, and the log saying so -- rather than the
# code that implements it.


_PLAYED = {"events": [{"kind": "narrative", "text": "The bloc states its intent."}]}


async def test_each_bloc_is_logged_against_the_model_that_played_it(conn):
    """The audit trail must say which family played whom, not which model the role pins.

    Before the fix every bloc row carried the router's tracked-agent pin, so three
    families wrote three identical rows and the log said the opposite of what happened.
    """
    assign_blocs(conn, "sandbox", _blocs())
    await _engine(conn, MockProvider(script=always(ScriptedResponse(data=_PLAYED)))).run_round()

    rows = {
        (row["provider"], row["requested_model"], row["nation_id"])
        for row in conn.execute(
            "SELECT provider, requested_model, nation_id FROM routing_log "
            "WHERE role = 'tracked_agent'"
        )
    }
    assert rows == {
        ("claude", "claude-sonnet-5", "aurelia"),
        ("codex", "gpt-5.6-terra", "karthos"),
        ("antigravity", "gemini-3.6-flash-high", "veyra"),
    }


async def test_each_bloc_is_asked_with_its_own_model(conn):
    """What is logged and what is asked are the same model, or the log is decoration."""
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(script=always(ScriptedResponse(data=_PLAYED)))
    await _engine(conn, provider).run_round()

    assert sorted(call.model for call in provider.calls) == sorted(
        ["claude-sonnet-5", "gpt-5.6-terra", "gemini-3.6-flash-high"]
    )


async def test_a_bloc_plays_every_nation_it_holds(conn):
    """One nation a round left a bloc's other powers unplayed by the model they were given."""
    for extra in ("xa", "xb"):
        conn.execute(
            "INSERT INTO nation (id, world_id, name, colour) VALUES (?, 'sandbox', ?, '#888888')",
            (extra, f"Power {extra}"),
        )
    conn.commit()
    assign_blocs(conn, "sandbox", _blocs(a=("claude", "claude-sonnet-5",
                                            ("aurelia", "xa", "xb"))))
    provider = MockProvider(script=always(ScriptedResponse(data=_PLAYED)))
    report = await _engine(conn, provider).run_round()

    assert report.calls == 5
    played_by_claude = {move.nation_id for move in report.moves if move.provider_key == "claude"}
    assert played_by_claude == {"aurelia", "xa", "xb"}


async def test_every_move_says_who_played_it_and_what_became_of_it(conn):
    """The spectator's unit: one line per government, with its model and its outcome."""
    assign_blocs(conn, "sandbox", _blocs())
    # The first bloc exhausts its retries and fails; see the note in
    # `test_a_failing_bloc_does_not_stop_the_others`. `report.calls` staying at 3 is the
    # other half of it: attempts are not calls.
    provider = MockProvider(script=cycle([
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(failure_kind=FailureKind.TIMEOUT),
        ScriptedResponse(data={"diffs": [], "events": []}),
        ScriptedResponse(data=_PLAYED),
    ]))
    report = await _engine(conn, provider).run_round()

    assert len(report.moves) == report.calls == 3
    assert {move.nation_id for move in report.moves} == {"aurelia", "karthos", "veyra"}
    assert sorted(move.outcome for move in report.moves) == ["applied", "failed", "silent"]
    for move in report.moves:
        bloc = next(b for b in _blocs() if move.nation_id in b.nations)
        assert (move.bloc, move.model) == (bloc.name, bloc.model)
        assert move.model in move.describe()


# --------------------------------------------------------------------------- statistics


def _a_round_was_played(conn, turn: int = 0) -> None:
    """Open the match the way the engine does: the round's own `arbiter` record."""
    from atlas.engine.eve import EveRound

    _engine(conn, MockProvider())._record_round(turn, EveRound(turn=turn))


def test_statistics_carry_their_own_caveat(conn):
    """The numbers look like a leaderboard whether or not they deserve to."""
    assign_blocs(conn, "sandbox", _blocs())
    stats = collect_match_stats(conn, "sandbox")

    assert "not skill" in stats.caveat
    assert "anecdote" in stats.caveat
    assert "plays differently" in stats.caveat


def test_a_single_match_is_labelled_an_anecdote(conn):
    assign_blocs(conn, "sandbox", _blocs())
    for profile in collect_match_stats(conn, "sandbox").profiles:
        assert profile.confidence == "anecdote"


def test_confidence_rises_only_with_sample_size(conn):
    assign_blocs(conn, "sandbox", _blocs())
    single = collect_match_stats(conn, "sandbox")

    assert summarise([single] * 2).profiles[0].confidence == "anecdote"
    assert summarise([single] * 5).profiles[0].confidence == "suggestive"
    assert summarise([single] * 12).profiles[0].confidence == "indicative"


def test_summarising_few_matches_says_so(conn):
    assign_blocs(conn, "sandbox", _blocs())
    merged = summarise([collect_match_stats(conn, "sandbox")] * 3)
    assert any("noise" in note for note in merged.notes)


def test_statistics_on_a_non_eve_world_say_so(conn):
    stats = collect_match_stats(conn, "sandbox")
    assert any("not an EVE match" in note or "no blocs" in note for note in stats.notes)


def test_promise_keeping_reflects_broken_agreements(conn):
    from atlas.state.apply import record_event
    from atlas.state.models import EventKind, EventOrigin

    assign_blocs(conn, "sandbox", _blocs())
    _a_round_was_played(conn)
    for kind in (EventKind.ALLIANCE_FORMED, EventKind.TREATY_BROKEN):
        record_event(
            conn, world_id="sandbox", turn=1, date="1897-03-01",
            kind=kind, origin=EventOrigin.ENGINE, actor_nation_id="aurelia",
        )
    conn.commit()

    profile = next(
        p for p in collect_match_stats(conn, "sandbox").profiles if "Bloc A" in p.provider
    )
    assert profile.alliances_formed == 1
    assert profile.treaties_broken == 1
    assert profile.promise_keeping == pytest.approx(0.5)


def test_escalation_is_measured_per_nation_not_in_total(conn):
    """A bloc with more nations is not automatically more aggressive."""
    from atlas.state.apply import record_event
    from atlas.state.models import EventKind, EventOrigin

    # Bloc A takes two nations; Bloc C gives one up so nothing is double-assigned.
    assign_blocs(
        conn,
        "sandbox",
        _blocs(
            a=("claude", "claude-sonnet-5", ("aurelia", "veyra")),
            c=("antigravity", "gemini-3.6-flash-high", ()),
        ),
    )
    _a_round_was_played(conn)
    record_event(
        conn, world_id="sandbox", turn=1, date="1897-03-01",
        kind=EventKind.WAR_DECLARED, origin=EventOrigin.ENGINE, actor_nation_id="aurelia",
    )
    conn.commit()

    profile = next(
        p for p in collect_match_stats(conn, "sandbox").profiles if "Bloc A" in p.provider
    )
    assert profile.wars_declared == 1
    assert profile.escalation_rate == pytest.approx(0.5)


# --------------------------------------------------------------------------- headlines
#
# The match report is the whole spectator interface for a headless match. These pin what
# it is obliged to show: what the models did, and not only what the world did afterwards.


def _headline(conn, turn: int, text: str, *, origin: str = "ENGINE",
              visibility: str = "public", actor: str | None = None) -> None:
    conn.execute(
        "INSERT INTO event (world_id, turn, date, kind, origin, visibility, text,"
        " actor_nation_id) VALUES ('sandbox', ?, '1897-03-01', 'narrative', ?, ?, ?, ?)",
        (turn, origin, visibility, text, actor),
    )
    conn.commit()


async def test_a_bloc_writes_its_events_at_the_turn_the_round_reports(conn):
    """The premise the match report's window rests on, asserted rather than assumed.

    A round is asked while turn N is open and `apply_batch` stamps its batch with N; the
    jump that follows plays N+1. Any report that reads only the current turn number is
    therefore reading the turn *after* the one the models acted in.
    """
    assign_blocs(conn, "sandbox", _blocs())
    provider = MockProvider(script=always(ScriptedResponse(data={
        "events": [{"kind": "narrative", "text": "Aurelia mobilises."}]
    })))
    report = await _engine(conn, provider).run_round()

    turns = {row["turn"] for row in conn.execute(
        "SELECT turn FROM event WHERE world_id = 'sandbox' AND origin = 'MODEL'")}
    assert turns == {report.turn}


def test_the_match_report_shows_what_the_blocs_themselves_did(conn):
    """The regression: the models' own news was dropped from every round.

    The window was the current turn number, which after the jump is the jump's turn, so a
    match report showed the world simulator answering moves the reader never saw.
    """
    _headline(conn, 1, "Karthos closes the strait.", origin="MODEL")
    _headline(conn, 2, "Grain prices rise in Veyra.")
    conn.execute("UPDATE world SET turn_number = 2 WHERE id = 'sandbox'")
    conn.commit()

    assert cli._eve_headlines(conn, "sandbox", 1) == [
        "Karthos closes the strait.",
        "Grain prices rise in Veyra.",
    ]


def test_a_talkative_world_cannot_crowd_out_the_blocs_news(conn):
    """Widening the window is not enough on its own.

    A jump writes far more public events than three blocs do, and they all have higher
    ids, so taking the last `limit` rows by id alone drops the models' lines again -- the
    same defect arrived at by a slower route.
    """
    _headline(conn, 1, "Aurelia denounces the pact.", origin="MODEL")
    _headline(conn, 1, "Karthos closes the strait.", origin="MODEL")
    for index in range(10):
        _headline(conn, 2, f"World event {index}.")

    lines = cli._eve_headlines(conn, "sandbox", 1)
    assert len(lines) == 6
    assert lines[:2] == ["Aurelia denounces the pact.", "Karthos closes the strait."]
    assert lines[2:] == [f"World event {index}." for index in range(6, 10)]


def test_the_blocs_do_not_crowd_out_the_world_either(conn):
    """The reservation is a floor, not a quota: unused half goes back to the engine."""
    _headline(conn, 1, "Aurelia denounces the pact.", origin="MODEL")
    for index in range(10):
        _headline(conn, 2, f"World event {index}.")

    lines = cli._eve_headlines(conn, "sandbox", 1)
    assert lines == ["Aurelia denounces the pact."] + [
        f"World event {index}." for index in range(5, 10)
    ]


def test_a_headline_is_public_and_nothing_else(conn):
    """A spectator's feed is not a referee's view; a secret stays secret in a match too."""
    _headline(conn, 1, "A secret approach is made.", origin="MODEL", visibility="secret")
    _headline(conn, 1, "A quiet word between two capitals.", visibility="bilateral")
    _headline(conn, 1, "The fleet sails.", origin="MODEL")

    assert cli._eve_headlines(conn, "sandbox", 1) == ["The fleet sails."]


def test_headlines_stop_at_the_turn_the_round_began(conn):
    """Earlier turns are `atlas chronicle`'s job. A round reports its own round."""
    _headline(conn, 1, "Last round's news.", origin="MODEL")
    _headline(conn, 2, "This round's news.", origin="MODEL")

    assert cli._eve_headlines(conn, "sandbox", 2) == ["This round's news."]


# --------------------------------------------------------------------------- the rosters


def _build_cold_war():
    from atlas.map.pack import PackSet
    from atlas.scenarios.presets import load_preset
    from atlas.scenarios.registry import preset_politics, preset_seed
    from atlas.scenarios.territory import pack_paths
    from atlas.state.bootstrap import create_world

    preset = load_preset("cold-war")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id="cw")
    connection = create_world(":memory:", seed)
    preset_politics(connection, "cw", preset)
    return connection


@pytest.fixture(scope="module")
def cold_war():
    connection = _build_cold_war()
    yield connection
    connection.close()


@pytest.fixture
def own_cold_war():
    """A cold-war world of this test's own, for anything that commits."""
    connection = _build_cold_war()
    yield connection
    connection.close()


def test_the_cold_war_is_played_by_its_historical_blocs(cold_war):
    """The default era takes the historical roster, every nation of it, in pin order."""
    blocs = cli._eve_blocs(cold_war, "cw", EVE_BLOC_MODELS)

    assert [b.name for b in blocs] == [name for name, _ in cli._COLD_WAR_BLOCS]
    assert [b.nations for b in blocs] == [members for _, members in cli._COLD_WAR_BLOCS]
    assert [(b.provider_key, b.model) for b in blocs] == list(EVE_BLOC_MODELS.values())
    assert len({b.colour for b in blocs}) == 3


def test_a_missing_historical_power_falls_back_to_the_strongest_nine(cold_war):
    """**The fallback, which the default era never reaches.** One historical member gone and
    the whole roster is re-dealt from the strength ranking, round-robin, so each bloc gets a
    strong, a middling and a weak power rather than one bloc getting the top three."""
    cold_war.execute("SAVEPOINT fallback")
    try:
        cold_war.execute("UPDATE nation SET is_alive = 0 WHERE world_id = 'cw' AND id = 'dvn'")
        ranked = [row["id"] for row in cold_war.execute(
            "SELECT id FROM nation WHERE world_id = 'cw' AND is_alive = 1"
            " ORDER BY (military_strength + economy) DESC, id LIMIT 9")]
        blocs = cli._eve_blocs(cold_war, "cw", EVE_BLOC_MODELS)
    finally:
        cold_war.execute("ROLLBACK TO fallback")
        cold_war.execute("RELEASE fallback")

    assert [b.nations for b in blocs] == [tuple(ranked[i::3]) for i in range(3)]
    assert [b.name for b in blocs] == ["Bloc A", "Bloc B", "Bloc C"]
    assert "dvn" not in {n for b in blocs for n in b.nations}
    everyone = [n for b in blocs for n in b.nations]
    assert len(everyone) == len(set(everyone)) == 9


def test_three_nations_are_enough_for_three_blocs(conn):
    """The sandbox has exactly three nations: one each, strongest to bloc A."""
    blocs = cli._eve_blocs(conn, "sandbox", EVE_BLOC_MODELS)
    assert [b.nations for b in blocs] == [("karthos",), ("aurelia",), ("veyra",)]


def test_two_living_nations_are_not_enough(conn):
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = 'veyra'")
    assert cli._eve_blocs(conn, "sandbox", EVE_BLOC_MODELS) is None


def test_a_short_pin_table_is_refused_rather_than_crashing(conn):
    """RED before 2026-09-18: two pins raised ``IndexError`` from the fallback, where the
    caller's ``None`` branch prints a reason and exits 1."""
    two = dict(list(EVE_BLOC_MODELS.items())[:2])
    assert cli._eve_blocs(conn, "sandbox", two) is None


# --------------------------------------------------------------------------- the boundary
#
# A curated era seeds its history into the same event table a match writes to. The
# statistics are about the match.


def test_seeded_history_is_not_the_blocs_behaviour(own_cold_war):
    """RED before 2026-09-18. With no move played, cold-war credited the Eastern and Third
    Force blocs with a war each (the seeded Soviet and North Vietnamese declarations) and
    the Third Force with a broken alliance (China's), in every match, whoever played."""
    from atlas.engine.eve import EveRound

    world = own_cold_war
    assert world.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = 'cw' AND kind = 'war_declared'"
    ).fetchone()[0] >= 2, "the premise: the era seeds declarations of war"

    assign_blocs(world, "cw", cli._eve_blocs(world, "cw", EVE_BLOC_MODELS))
    before = collect_match_stats(world, "cw")
    EveEngine(conn=world, world_id="cw", providers={},
              router=Router(pins=default_role_models()))._record_round(0, EveRound(turn=0))
    after = collect_match_stats(world, "cw")

    assert any("no EVE round" in note for note in before.notes)
    for stats in (before, after):
        for profile in stats.profiles:
            assert profile.wars_declared == 0, profile
            assert profile.treaties_broken == 0, profile


async def test_what_a_bloc_does_in_the_first_round_is_counted(conn):
    """The other side of the boundary: round 0 is turn 0, the same turn as the seeded
    history, and a bloc's own declaration in it must still count."""
    assign_blocs(conn, "sandbox", _blocs())
    hawk = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "We march.",
        "events": [{"kind": "war_declared", "text": "War.", "actor_nation_id": "karthos"}],
    })))
    calm = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "We wait.", "events": [{"kind": "narrative", "text": "Quiet."}],
    })))
    engine = EveEngine(conn=conn, world_id="sandbox",
                       providers={"claude": calm, "codex": hawk, "antigravity": calm},
                       router=Router(pins=default_role_models()))
    report = await engine.run_round()
    assert report.turn == 0

    by_seat = {p.seats[0][0]: p for p in collect_match_stats(conn, "sandbox").profiles}
    assert by_seat["Bloc B"].wars_declared == 1
    assert by_seat["Bloc A"].wars_declared == by_seat["Bloc C"].wars_declared == 0


# --------------------------------------------------------------------------- folding


def _profile(seat: str, provider_key: str, model: str, *, wars: int = 0,
             proposals: int = 0, refusals: int = 0):
    from atlas.engine.statistics import MatchStats, ProviderProfile

    return MatchStats(world_id="m", turns=3, profiles=[ProviderProfile(
        provider=f"{seat} ({provider_key})", matches=1, nations_played=3,
        first_alliance_turn=None, alliances_formed=0, treaties_broken=0,
        wars_declared=wars, peaces_signed=0, regions_gained=0, regions_lost=0,
        nations_surviving=3, refusal_rate=(refusals / proposals) if proposals else 0.0,
        provider_key=provider_key, model=model, seats=((seat, 1),),
        proposals=proposals, refusals=refusals, escalation_by_match=(wars / 3,))])


def test_a_provider_that_changes_seat_is_still_one_provider():
    """RED before 2026-09-18: folding keyed the display label, which names the bloc, so a
    rotated series came back as one profile per seat and never per provider."""
    runs = [_profile(seat, "codex", "gpt-5.6-terra", wars=w)
            for seat, w in (("Western Bloc", 0), ("Eastern Bloc", 3), ("Third Force", 0))]
    merged = summarise(runs)

    assert len(merged.profiles) == 1
    only = merged.profiles[0]
    assert only.matches == 3
    assert dict(only.seats) == {"Western Bloc": 1, "Eastern Bloc": 1, "Third Force": 1}
    assert only.escalation_spread == (0.0, 1.0)
    assert "per match 0.00-1.00" in only.describe()


def test_refusals_are_weighted_by_what_was_proposed():
    """RED before 2026-09-18: 1 of 2 refused in one match and 0 of 40 in another merged to
    25% by nations played. It is 1 in 42."""
    merged = summarise([
        _profile("Western Bloc", "claude", "claude-sonnet-5", proposals=2, refusals=1),
        _profile("Eastern Bloc", "claude", "claude-sonnet-5", proposals=40, refusals=0),
    ])
    assert merged.profiles[0].refusal_rate == pytest.approx(1 / 42)


def test_a_provider_stuck_in_one_seat_is_flagged_as_confounded():
    runs = []
    for _ in range(2):
        runs.append(_profile("Western Bloc", "claude", "claude-sonnet-5"))
        runs.append(_profile("Eastern Bloc", "codex", "gpt-5.6-terra"))
    merged = summarise(runs)
    assert any("not balanced" in note for note in merged.notes)


def test_a_balanced_rotation_is_not_flagged():
    seats = ("Western Bloc", "Eastern Bloc", "Third Force")
    pins = (("claude", "claude-sonnet-5"), ("codex", "gpt-5.6-terra"),
            ("antigravity", "gemini-3.6-flash-high"))
    runs = [_profile(seats[(i + k) % 3], *pins[k]) for i in range(3) for k in range(3)]
    merged = summarise(runs)
    assert not any("not balanced" in note for note in merged.notes)
    assert [p.matches for p in merged.profiles] == [3, 3, 3]


def test_a_match_begun_mid_game_says_what_it_cannot_separate(conn):
    """A world played to turn 3 and then handed to the blocs: the jump that ended turn 2 is
    stamped 3 through batches, like round 1. The report must say so rather than pretend."""
    assign_blocs(conn, "sandbox", _blocs())
    _a_round_was_played(conn, turn=3)
    stats = collect_match_stats(conn, "sandbox")
    assert any("already at turn 3" in note for note in stats.notes)


def test_a_match_begun_at_turn_zero_has_nothing_to_confess(conn):
    assign_blocs(conn, "sandbox", _blocs())
    _a_round_was_played(conn)
    assert not any("already at turn" in note
                   for note in collect_match_stats(conn, "sandbox").notes)


# --------------------------------------------------------------------------- a hard round
#
# From a read-only bug scout of this module (2026-09-18); each was reproduced on master.


def _scripted(**by_provider):
    calm = always(ScriptedResponse(data={
        "narrative": "We wait.", "events": [{"kind": "narrative", "text": "Quiet."}]}))
    return {key: MockProvider(script=by_provider.get(key, calm))
            for key in ("claude", "codex", "antigravity")}


def _routing_outcomes(conn) -> dict[str, str]:
    return {str(row["nation_id"]): str(row["outcome"]) for row in conn.execute(
        "SELECT nation_id, outcome FROM routing_log"
        " WHERE world_id = 'sandbox' AND role = 'tracked_agent' ORDER BY id")}


async def test_an_unreadable_answer_is_one_refused_move_not_the_end_of_the_match(conn):
    """RED before: a diff with no `target_id` raised out of `run_round`, so the blocs after
    it never played and the round was never recorded."""
    assign_blocs(conn, "sandbox", _blocs())
    broken = always(ScriptedResponse(data={
        "narrative": "x", "diffs": [{"op": "update", "target_table": "nation",
                                     "field": "stability", "new_value": 1}]}))
    engine = EveEngine(conn=conn, world_id="sandbox", providers=_scripted(codex=broken),
                       router=Router(pins=default_role_models()))
    report = await engine.run_round()

    by_nation = {move.nation_id: move.outcome for move in report.moves}
    assert by_nation["karthos"] == "refused"
    assert by_nation["veyra"] == "applied", "the bloc after the broken one still played"
    assert report.rejected == 1
    assert conn.execute("SELECT COUNT(*) FROM routing_log WHERE world_id = 'sandbox'"
                        " AND role = 'arbiter'").fetchone()[0] == 1, "the round is recorded"


async def test_the_routing_log_says_what_became_of_each_move(conn):
    """RED before: every answered call was logged `ok`, refused and silent alike."""
    assign_blocs(conn, "sandbox", _blocs())
    silent = always(ScriptedResponse(data={"diffs": [], "events": []}))
    engine = EveEngine(conn=conn, world_id="sandbox",
                       providers=_scripted(antigravity=silent),
                       router=Router(pins=default_role_models()))
    await engine.run_round()
    assert _routing_outcomes(conn) == {"aurelia": "ok", "karthos": "ok", "veyra": "silent"}


async def test_a_move_refused_for_attestation_is_counted_and_logged_as_refused(conn):
    """RED before: three refusals read `applied=0 rejected=0` and logged `ok`."""
    assign_blocs(conn, "sandbox", _blocs())
    unattested = always(ScriptedResponse(
        data={"narrative": "x", "events": [{"kind": "narrative", "text": "x"}]},
        served_model="some-other-model"))
    providers = {key: MockProvider(script=unattested) for key in ("claude", "codex", "antigravity")}
    engine = EveEngine(conn=conn, world_id="sandbox", providers=providers,
                       router=Router(pins=default_role_models()), require_attestation=True)
    report = await engine.run_round()

    assert report.rejected == 3 and report.applied == 0
    assert set(_routing_outcomes(conn).values()) == {"refused"}
    assert len(report.unverified) == 3, "one line per bloc"
    assert all("/" in line for line in report.rejections), "each names its nation"


async def test_a_nation_tracked_before_it_joined_a_bloc_is_not_also_a_tracked_agent(conn):
    """RED before: it was asked twice in the first turn -- by its bloc's model in the round
    and by the role pin in the jump -- until Focus dropped it at the end of that jump."""
    from atlas.engine.turn_engine import TurnEngine

    conn.execute("UPDATE nation SET is_tracked = 1, tracked_since_turn = 0 WHERE id = 'karthos'")
    conn.commit()
    assign_blocs(conn, "sandbox", _blocs())
    providers = _scripted()
    router = Router(pins=default_role_models())
    await EveEngine(conn=conn, world_id="sandbox", providers=providers,
                    router=router).run_round()
    await TurnEngine(conn=conn, world_id="sandbox", providers=providers,
                     router=router).jump(days=30)

    asked = [str(row["nation_id"]) for row in conn.execute(
        "SELECT nation_id FROM routing_log WHERE world_id = 'sandbox'"
        " AND role = 'tracked_agent' AND nation_id = 'karthos'")]
    assert asked == ["karthos"], f"karthos was asked {len(asked)} times"


# --------------------------------------------------------------------------- the console


def test_a_name_the_console_cannot_encode_does_not_end_the_run(tmp_path, monkeypatch):
    """**A live match died on this.** Two rounds in, a model wrote a Vietnamese name into
    an event and `print` raised UnicodeEncodeError out of the match loop, because a
    redirected stdout on Windows is cp1252. A game about the whole world writes such names
    in the ordinary course of play."""
    import sys

    path = tmp_path / "out.txt"
    with path.open("w", encoding="cp1252") as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        with pytest.raises(UnicodeEncodeError):
            print("Chiến dịch")  # the premise: this stream cannot hold the name
        cli._use_utf8_output()
        print("Chiến dịch")
    assert "Chiến dịch" in path.read_text(encoding="utf-8")


def test_the_command_line_sets_that_up_before_it_runs_anything(tmp_path, monkeypatch):
    import sys

    path = tmp_path / "out.txt"
    with path.open("w", encoding="cp1252") as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        cli.main(["rules"])
        assert sys.stdout.encoding.lower().replace("-", "") == "utf8"

# ------------------------------------------------------------------ ranking and the ceiling
#
# Both from watching a live match on 2026-09-18.


def test_a_blocs_own_line_outranks_another_powers(conn):
    """**The reserved half is for the blocs, and it was filling up without them.** A jump
    plays every other tracked power on the role pin, and those events are `origin =
    'MODEL'` as well: in the live match's third round, two of the three model lines were
    powers no bloc was playing."""
    # Veyra is left out of the blocs, so it stands for every power a jump plays on the
    # role pin: model-authored, and nobody's bloc.
    assign_blocs(conn, "sandbox", _blocs(c=("antigravity", "gemini-3.6-flash-high", ())))
    # The blocs speak first and the neutrals last, so recency alone would take the
    # neutrals: only the ranking can put a bloc's line back on the page.
    _headline(conn, 1, "Karthos closes the strait.", origin="MODEL", actor="karthos")
    _headline(conn, 1, "Aurelia answers.", origin="MODEL", actor="aurelia")
    for index in range(4):
        _headline(conn, 1, f"The world turns {index}.")
    for index in range(4):
        _headline(conn, 1, f"A neutral speaks {index}.", origin="MODEL", actor="veyra")

    headlines = cli._eve_headlines(conn, "sandbox", 1)
    assert "Karthos closes the strait." in headlines
    assert "Aurelia answers." in headlines
    assert sum(1 for line in headlines if "neutral" in line) <= 1, headlines
    assert sum(1 for line in headlines if "world turns" in line) == 3, headlines


def test_a_bloc_line_does_not_crowd_out_the_world_either(conn):
    """The ranking is about which model line is shown, not about taking the engine's half."""
    assign_blocs(conn, "sandbox", _blocs())
    for index in range(6):
        _headline(conn, 1, f"Karthos says {index}.", origin="MODEL", actor="karthos")
    for index in range(6):
        _headline(conn, 1, f"The world turns {index}.")

    headlines = cli._eve_headlines(conn, "sandbox", 1)
    assert len(headlines) == 6
    assert sum(1 for line in headlines if "world turns" in line) == 3


def test_a_neutral_still_speaks_when_no_bloc_did(conn):
    """A round in which no bloc's event reached the log is not a round with no news."""
    assign_blocs(conn, "sandbox", _blocs(c=("antigravity", "gemini-3.6-flash-high", ())))
    _headline(conn, 1, "A neutral speaks.", origin="MODEL", actor="veyra")
    _headline(conn, 1, "The world turns.")

    assert cli._eve_headlines(conn, "sandbox", 1) == ["A neutral speaks.", "The world turns."]


def test_an_old_round_reports_its_own_round_and_not_the_latest(conn):
    """**The window had a floor and no ceiling.** Re-read after the match -- a replay, a
    later reader -- every round returned the last turn's news. It looked right live only
    because the later turns did not exist yet. The ceiling is the round and the jump that
    answers it."""
    _headline(conn, 1, "Round one's news.", origin="MODEL")
    _headline(conn, 2, "The world answers round one.")
    _headline(conn, 3, "Round three's news.", origin="MODEL")

    assert cli._eve_headlines(conn, "sandbox", 1) == [
        "Round one's news.", "The world answers round one."]
    assert "Round three's news." not in cli._eve_headlines(conn, "sandbox", 1)


# ------------------------------------------------------------------ the round's own edges
#
# Three leftovers from the 2026-09-18 bug scout, fixed 2026-09-20.


def test_a_nation_dropped_from_the_blocs_stops_being_played_by_a_model(conn):
    """RED before: `bloc_id` was cleared by cascade and `controller` was not, so a power
    left out of a reassignment stayed marked as somebody's model for good."""
    assign_blocs(conn, "sandbox", _blocs())
    assert conn.execute(
        "SELECT controller FROM nation WHERE id = 'veyra'").fetchone()[0] == "antigravity"

    assign_blocs(conn, "sandbox", _blocs(c=("antigravity", "gemini-3.6-flash-high", ())))

    row = conn.execute(
        "SELECT bloc_id, controller FROM nation WHERE id = 'veyra'").fetchone()
    assert row["bloc_id"] is None and row["controller"] == "engine"


def test_the_player_gets_their_own_country_back(conn):
    """Aurelia is the sandbox's player. Seating it in a bloc takes the seat; dropping it
    from the blocs has to give the seat back, not leave it with the engine."""
    assert conn.execute(
        "SELECT controller FROM nation WHERE id = 'aurelia'").fetchone()[0] == "player"
    assign_blocs(conn, "sandbox", _blocs())
    assign_blocs(conn, "sandbox", _blocs(a=("claude", "claude-sonnet-5", ())))

    assert conn.execute(
        "SELECT controller FROM nation WHERE id = 'aurelia'").fetchone()[0] == "player"


async def test_a_bloc_whose_provider_is_missing_still_appears_in_the_round(conn):
    """RED before: it was reported once in `failures` and left out of the per-move listing
    altogether, which reads like a bloc that chose to sit still."""
    assign_blocs(conn, "sandbox", _blocs())
    calm = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "We wait.", "events": [{"kind": "narrative", "text": "Quiet."}]})))
    engine = EveEngine(conn=conn, world_id="sandbox",
                       providers={"claude": calm, "antigravity": calm},  # no codex
                       router=Router(pins=default_role_models()))
    report = await engine.run_round()

    missing = [move for move in report.moves if move.outcome == "unavailable"]
    assert [move.nation_id for move in missing] == ["karthos"]
    assert missing[0].bloc == "Bloc B" and "unavailable" in missing[0].detail
    assert any("codex is unavailable" in line for line in report.failures)
    assert len(report.moves) == 3, "every nation the round would have played is listed"


async def test_a_round_is_judged_against_its_own_length(conn):
    """The round's span reaches validation, where rate-bounded rules read it.

    **Consistency rather than a live fix, and the difference matters.** `ECON-001` is the
    only span-scaled rule, and `CORE-003` bars a tracked agent -- which is what a bloc is --
    from writing every column it caps, so no proposal a bloc can make is judged differently
    today. What changes now is that the cap quoted in the refusal is the round's own: 15%
    for a month, 45% for the quarter `atlas eve` advances by default. It stops being
    cosmetic the moment a span-scaled rule reaches a role a bloc can use, and a tracked
    agent in an ordinary turn has always been judged against its own turn's length.
    """
    assign_blocs(conn, "sandbox", _blocs())
    genesis = conn.execute(
        "SELECT id FROM event WHERE world_id = 'sandbox' AND kind = 'genesis'").fetchone()[0]
    bold = always(ScriptedResponse(data={"diffs": [{
        "target_table": "nation", "target_id": "karthos", "field": "economy",
        "op": "set", "new_value": 30.0,
        "reason_event_id": genesis, "visibility_scope": "public"}]}))
    quiet = always(ScriptedResponse(data={"diffs": [], "events": []}))

    async def cap_quoted(days: int) -> str:
        providers = {"codex": MockProvider(script=bold),
                     "claude": MockProvider(script=quiet),
                     "antigravity": MockProvider(script=quiet)}
        report = await EveEngine(
            conn=conn, world_id="sandbox", providers=providers,
            router=Router(pins=default_role_models()), days=days).run_round()
        return next(line for line in report.rejections if "ECON-001" in line)

    assert "above the 15%" in await cap_quoted(30)
    assert "above the 45%" in await cap_quoted(90)


async def test_the_round_hands_its_length_to_the_applier(conn, monkeypatch):
    """The plumbing the test above rests on, checked where it is used rather than by
    reading the source: both callers pass `--days` in, and the engine passes it on."""
    import atlas.engine.eve as eve_module

    seen: list[int] = []
    real = eve_module.apply_batch

    def spy(conn_, batch, **kwargs):
        seen.append(kwargs.get("days"))
        return real(conn_, batch, **kwargs)

    monkeypatch.setattr(eve_module, "apply_batch", spy)
    assign_blocs(conn, "sandbox", _blocs())
    quiet = MockProvider(script=always(ScriptedResponse(data={
        "narrative": "We wait.", "events": [{"kind": "narrative", "text": "Quiet."}]})))
    await EveEngine(conn=conn, world_id="sandbox",
                    providers={k: quiet for k in ("claude", "codex", "antigravity")},
                    router=Router(pins=default_role_models()), days=90).run_round()

    assert seen and set(seen) == {90}, seen


# --------------------------------------------------------------- the orders a bloc gives
#
# Found 2026-09-20: `apply_batch` takes the diffs and the events, and every other channel a
# tracked agent has -- objectives, war aims, the call-up, the yards, the recruits -- is
# applied *after* it by the turn engine. EVE did none of that, so a bloc's orders were
# parsed and dropped in silence. The first Fulda Gap match recorded no objective from any
# bloc; this is why.


def _ordering(**fields) -> MockProvider:
    body = {"narrative": "Orders issued.", "diffs": [], "events": []}
    body.update(fields)
    return MockProvider(script=always(ScriptedResponse(data=body)))


async def test_a_blocs_objectives_are_recorded(conn):
    """RED before: `military_objectives` reached `parse_batch` and went no further."""
    assign_blocs(conn, "sandbox", _blocs())
    conn.execute("UPDATE relation SET at_war = 1 WHERE nation_a IN ('aurelia','karthos')"
                 "   AND nation_b IN ('aurelia','karthos')")
    conn.commit()
    quiet = _ordering()
    ordering = _ordering(military_objectives={"hold": ["r_north"]})
    report = await EveEngine(
        conn=conn, world_id="sandbox",
        providers={"claude": ordering, "codex": quiet, "antigravity": quiet},
        router=Router(pins=default_role_models())).run_round()

    rows = [(str(r["nation_id"]), str(r["kind"]), str(r["region_id"])) for r in conn.execute(
        "SELECT nation_id, kind, region_id FROM military_objective WHERE world_id = 'sandbox'")]
    assert rows == [("aurelia", "hold", "r_north")]
    move = next(m for m in report.moves if m.nation_id == "aurelia")
    assert "objective" in move.detail, move.detail


async def test_a_blocs_call_up_reaches_its_country(conn):
    """Mobilisation is a column a bloc could never move: `apply_batch` does not read it."""
    assign_blocs(conn, "sandbox", _blocs())
    quiet = _ordering()
    report = await EveEngine(
        conn=conn, world_id="sandbox",
        providers={"claude": _ordering(mobilisation=2), "codex": quiet, "antigravity": quiet},
        router=Router(pins=default_role_models())).run_round()

    # One step a turn, in either direction: asking for 2 from a standing start lands on
    # 1, which is `set_level`'s own rule and not something EVE should reinvent.
    level = conn.execute(
        "SELECT mobilisation FROM nation WHERE id = 'aurelia'").fetchone()[0]
    assert level == 1
    assert any("mobilis" in m.detail.lower() for m in report.moves if m.nation_id == "aurelia")


async def test_a_blocs_recruits_are_raised_and_charged(conn):
    """The yards and the barracks run through the player's own order handler, so a bloc's
    division is priced and stamped exactly as a human's is."""
    assign_blocs(conn, "sandbox", _blocs())
    before = conn.execute(
        "SELECT COUNT(*) FROM unit WHERE world_id = 'sandbox' AND nation_id = 'aurelia'"
    ).fetchone()[0]
    purse = conn.execute("SELECT treasury FROM nation WHERE id = 'aurelia'").fetchone()[0]
    quiet = _ordering()
    await EveEngine(
        conn=conn, world_id="sandbox",
        providers={"claude": _ordering(raise_formations=[{"unit_kind": "infantry"}]),
                   "codex": quiet, "antigravity": quiet},
        router=Router(pins=default_role_models())).run_round()

    after = conn.execute(
        "SELECT COUNT(*) FROM unit WHERE world_id = 'sandbox' AND nation_id = 'aurelia'"
    ).fetchone()[0]
    spent = purse - conn.execute(
        "SELECT treasury FROM nation WHERE id = 'aurelia'").fetchone()[0]
    assert after == before + 1
    assert spent > 0, "a formation nobody paid for is a formation nobody ordered"


async def test_a_bloc_that_orders_nothing_but_orders_is_not_silent(conn):
    """A batch with no diffs and no events used to read as `silent`. An objective is an
    order, so a round in which a bloc only gave orders is a round in which it acted."""
    assign_blocs(conn, "sandbox", _blocs())
    quiet = _ordering()
    report = await EveEngine(
        conn=conn, world_id="sandbox",
        providers={"claude": _ordering(mobilisation=1), "codex": quiet, "antigravity": quiet},
        router=Router(pins=default_role_models())).run_round()

    move = next(m for m in report.moves if m.nation_id == "aurelia")
    assert move.outcome == "applied", move.detail
    assert [m.outcome for m in report.moves if m.nation_id == "karthos"] == ["silent"]
