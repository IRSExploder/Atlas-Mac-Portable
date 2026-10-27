"""Recording a run and replaying it without a model.

The headline test is `test_a_recorded_run_replays_to_the_same_world`: two worlds from one
seed, one driven by a provider and one by the tape of what that provider said, ending on the
same fingerprint. Everything else here defends a way that could be true by accident.
"""

from __future__ import annotations

import json

import pytest

from atlas.engine.routing import Router
from atlas.engine.turn_engine import TurnEngine
from atlas.providers.base import ProviderRequest, ProviderResult
from atlas.providers.cassette import (
    FORMAT,
    CassetteMiss,
    Entry,
    MatchMode,
    RecordingProvider,
    ReplayProvider,
    digest_of,
    read_cassette,
)
from atlas.providers.cli_provider import default_role_models
from atlas.providers.errors import FailureKind
from atlas.providers.mock import MockProvider, ScriptedResponse, always, cycle
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.models import Provider, Role


def _world():
    return create_world(":memory:", sandbox_seed())


def _tracked_world():
    """A world with two tracked powers, so the concurrent call path is exercised.

    `_run_tracked_agents` gathers its calls, so arrival order is a race -- which is exactly
    the case a cassette keyed on sequence would replay differently every time. A headline
    test on a world with no tracked nations would never touch it.
    """
    conn = _world()
    conn.execute("UPDATE nation SET is_tracked = 1 WHERE id IN ('karthos', 'veyra')")
    conn.commit()
    return conn


def _engine(conn, provider) -> TurnEngine:
    return TurnEngine(
        conn=conn, world_id="sandbox",
        providers={key: provider for key in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()),
    )


def _request(prompt="ask", role=Role.WORLD_SIM, model="m", schema=None,
             options=None) -> ProviderRequest:
    return ProviderRequest(role=role, prompt=prompt, model=model, schema=schema,
                           options=options or {})


#: A world simulator that actually moves something, so a replay that quietly did nothing
#: would not pass by producing an identical empty world.
_BUSY = always(ScriptedResponse(data={
    "diffs": [{"target_table": "nation", "target_id": "karthos", "field": "stability",
               "op": "adjust", "new_value": -2.0}],
    "events": [{"kind": "narrative", "text": "The Karthian harvest fails.",
                "visibility": "public"}],
}))


# ------------------------------------------------------------------ the headline


async def test_a_recorded_run_replays_to_the_same_world(tmp_path):
    """Record three turns against a provider, then play them into a fresh world.

    This is the whole point: what a live run costs in quota and wall clock, a replay costs
    in file reads. If this holds, every expensive run this project makes from here on is a
    regression fixture rather than a receipt.
    """
    tape = tmp_path / "run.jsonl"

    recorded = _tracked_world()
    engine = _engine(recorded, RecordingProvider(MockProvider(script=_BUSY), tape))
    for _ in range(3):
        await engine.jump(days=30)
    expected = fingerprint(recorded, "sandbox")
    stamped = recorded.execute(
        "SELECT created_at FROM world WHERE id = 'sandbox'").fetchone()[0]
    recorded.close()

    entries = read_cassette(tape)
    assert entries, "nothing was recorded"

    replayed = _tracked_world()
    # **`world.created_at` is wall-clock and it is inside the fingerprint.** It is the one
    # column that cannot match across two separately created worlds, and it made this test
    # flaky in exactly the way that would have been worst: green in isolation and every
    # other full-suite run, red when the two `create_world` calls happened to straddle a
    # second. The digest is a tool for comparing a world against *itself* at another point
    # in time -- which is why `seed_fingerprint.py` hashes a `WorldSeed` rather than a
    # database -- so comparing two worlds means aligning the one field that is about when
    # the file was made rather than what is in it.
    replayed.execute(
        "UPDATE world SET created_at = ? WHERE id = 'sandbox'", (stamped,))
    replayed.commit()
    player = ReplayProvider(entries)
    replay_engine = _engine(replayed, player)
    for _ in range(3):
        await replay_engine.jump(days=30)

    assert fingerprint(replayed, "sandbox") == expected
    assert player.remaining() == 0, "the replay asked fewer questions than were recorded"
    replayed.close()


async def test_a_replay_reaches_nothing_outside_the_tape(tmp_path):
    """The property that makes a cassette worth having: no CLI, no network, no quota.

    A `ReplayProvider` holds recorded entries and nothing else -- there is no inner
    provider it could fall through to -- so this asserts the shape rather than trusting it,
    and drives a whole turn to prove the engine is satisfied by it.
    """
    tape = tmp_path / "run.jsonl"
    recorded = _world()
    await _engine(recorded, RecordingProvider(MockProvider(script=_BUSY), tape)).jump()
    recorded.close()

    player = ReplayProvider.from_file(tape)
    assert not any(
        isinstance(getattr(player, name, None), MockProvider) for name in dir(player)
    ), "a replay must not be able to fall through to a live provider"

    replayed = _world()
    report = await _engine(replayed, player).jump()
    assert report.calls, "the turn made no calls, so this proves nothing"
    assert not report.failures, report.failures
    replayed.close()


# ------------------------------------------------------------------ what is recorded


async def test_attempts_are_recorded_not_calls(tmp_path):
    """A retried failure is two entries, because a replay reproduces what the *provider*
    saw. Recording only the answer that came back after the retries would desynchronise the
    replay the first time a live run hit a rate limit."""
    tape = tmp_path / "run.jsonl"
    provider = RecordingProvider(
        MockProvider(script=cycle([
            ScriptedResponse(failure_kind=FailureKind.RATE_LIMITED),
            ScriptedResponse(data={"diffs": [], "events": []}),
        ])),
        tape,
    )
    conn = _world()
    await _engine(conn, provider).jump()
    conn.close()

    entries = read_cassette(tape)
    assert not entries[0].ok and entries[0].failure_kind == "rate_limited"
    assert entries[1].ok
    assert entries[0].key == entries[1].key, (
        "the retry asked the same question, so it has to carry the same key"
    )


async def test_a_recorded_failure_replays_as_that_failure(tmp_path):
    """A cassette that turned every recorded failure into a success would be a cassette
    that could not reproduce the runs worth reproducing."""
    tape = tmp_path / "run.jsonl"
    recorder = RecordingProvider(
        MockProvider(script=always(ScriptedResponse(
            failure_kind=FailureKind.NOT_AUTHENTICATED))),
        tape,
    )
    live = await recorder.invoke(_request())
    assert live.failed

    player = ReplayProvider.from_file(tape)
    result: ProviderResult = await player.invoke(_request())
    assert result.failed
    assert result.failure is not None
    assert result.failure.kind is FailureKind.NOT_AUTHENTICATED


async def test_the_prompt_is_kept_verbatim(tmp_path):
    """Only the hash is matched on, but a cassette a person cannot read is a cassette
    nobody will ever check against what the model was actually shown."""
    tape = tmp_path / "run.jsonl"
    provider = RecordingProvider(MockProvider(script=_BUSY), tape)
    await provider.invoke(_request(prompt="the world as it stands"))
    assert read_cassette(tape)[0].prompt == "the world as it stands"


# ------------------------------------------------------------------ matching


async def test_matching_does_not_depend_on_the_order_calls_arrive_in(tmp_path):
    """Tracked agents run concurrently, so arrival order is a race -- the same reason the
    engine sorts their results by nation id before using them. A cassette keyed on sequence
    would replay a different world every time the scheduler felt different."""
    tape = tmp_path / "run.jsonl"
    provider = RecordingProvider(MockProvider(script=_BUSY), tape)
    for prompt in ("first", "second", "third"):
        await provider.invoke(_request(prompt=prompt))

    player = ReplayProvider.from_file(tape)
    backwards = [
        (await player.invoke(_request(prompt=prompt))).text
        for prompt in ("third", "first", "second")
    ]
    assert all(text for text in backwards)
    assert player.remaining() == 0


async def test_the_same_question_twice_gets_the_two_answers_it_got(tmp_path):
    tape = tmp_path / "run.jsonl"
    inner = MockProvider(script=cycle([
        ScriptedResponse(data={"n": 1}), ScriptedResponse(data={"n": 2}),
    ]))
    provider = RecordingProvider(inner, tape)
    await provider.invoke(_request())
    await provider.invoke(_request())

    player = ReplayProvider.from_file(tape)
    assert (await player.invoke(_request())).data == {"n": 1}
    assert (await player.invoke(_request())).data == {"n": 2}


async def test_a_changed_prompt_is_a_loud_miss(tmp_path):
    """**The design decision this file exists to defend.** A prompt that no longer matches
    means the engine is asking a different question, and that is the finding -- not
    something to smooth over with a nearest match that would make a replay of a *different*
    engine look like a replay of this one."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=_BUSY), tape).invoke(_request("as asked"))

    player = ReplayProvider.from_file(tape)
    with pytest.raises(CassetteMiss) as raised:
        await player.invoke(_request("asked differently"))
    assert "different question" in str(raised.value)
    assert "LOOSE" in str(raised.value), "the message has to say what to do about it"


async def test_running_out_of_answers_says_so_differently(tmp_path):
    """*Never asked* and *asked more often than recorded* are different problems with
    different fixes, and a message that conflated them would send a reader to the wrong one."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=_BUSY), tape).invoke(_request())

    player = ReplayProvider.from_file(tape)
    await player.invoke(_request())
    with pytest.raises(CassetteMiss) as raised:
        await player.invoke(_request())
    assert "more often than the recorded run" in str(raised.value)


async def test_loose_mode_replays_by_role_when_the_prompt_moved_on(tmp_path):
    """For the case where the prompt changed on purpose and the question is whether the new
    engine copes with last week's answers. Opt-in, so it cannot happen by accident."""
    tape = tmp_path / "run.jsonl"
    provider = RecordingProvider(MockProvider(script=_BUSY), tape)
    await provider.invoke(_request(prompt="the old briefing"))

    player = ReplayProvider.from_file(tape, mode=MatchMode.LOOSE)
    result = await player.invoke(_request(prompt="a briefing rewritten since"))
    assert result.ok
    assert player.remaining() == 0


async def test_a_miss_can_be_a_failure_instead_of_an_exception(tmp_path):
    """For a harness that would rather finish the run and report the gaps. Off by default:
    a replay that silently substitutes *the model failed* for *we have no record of this*
    produces a clean-looking run whose conclusions are about the wrong world."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=_BUSY), tape).invoke(_request())

    player = ReplayProvider.from_file(tape, strict=False)
    result = await player.invoke(_request(prompt="never asked"))
    assert result.failed
    assert player.missed and player.missed[0].prompt == "never asked"


# ------------------------------------------ the weak key, and the hole it used to leave


#: Two schemas that are obviously different questions. The engine asking for one and being
#: handed an answer recorded for the other is the failure this section exists for.
_SCHEMA_A = {"type": "object", "properties": {"a": {"type": "string"}}}
_SCHEMA_B = {"type": "object", "properties": {"totally_different": {"type": "string"}}}


async def test_a_changed_schema_misses_loudly(tmp_path):
    """**The defect, reproduced.** Disclosed in the phase-2 handoff and fixed here.

    `asked()` matches on role, model and canonical prompt -- everything an `Entry` stores,
    which is strictly less than the digest, because an entry records neither the schema nor
    the options. It was added to rescue the PT33 tapes and was consulted unconditionally, so
    a change to the response schema missed the digest and then quietly hit the weak key: the
    engine asked for a different shape of answer and was handed the old one, with no miss
    reported. That defeats the guarantee on exactly the class of change it exists to catch.
    """
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=always(ScriptedResponse(data={"v": 1}))),
                            tape).invoke(_request(schema=_SCHEMA_A))

    player = ReplayProvider.from_file(tape)
    with pytest.raises(CassetteMiss) as raised:
        await player.invoke(_request(schema=_SCHEMA_B))
    assert "schema" in str(raised.value), (
        "the miss has to say which of the three causes it was"
    )


async def test_the_same_schema_still_hits(tmp_path):
    """The other half, and the one that would make the fix worthless if it broke: gating the
    rescue must not stop an honest replay from matching."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=always(ScriptedResponse(data={"v": 1}))),
                            tape).invoke(_request(schema=_SCHEMA_A))

    player = ReplayProvider.from_file(tape)
    result = await player.invoke(_request(schema=_SCHEMA_A))
    assert result.ok and result.data == {"v": 1}
    assert player.remaining() == 0


async def test_changed_options_miss_too(tmp_path):
    """Reasoning effort is part of the question. It is in the digest and not in `asked`, so
    it went through the same hole."""
    tape = tmp_path / "run.jsonl"
    recorder = RecordingProvider(
        MockProvider(script=always(ScriptedResponse(data={"v": 1}))), tape)
    await recorder.invoke(_request(options={"reasoning_effort": "low"}))

    player = ReplayProvider.from_file(tape)
    with pytest.raises(CassetteMiss):
        await player.invoke(_request(options={"reasoning_effort": "high"}))


async def test_new_recordings_are_format_2(tmp_path):
    """The gate is the format, so a tape written now has to carry it -- checked on a real
    recording rather than on the constant, because the constant is not what a replay reads."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=_BUSY), tape).invoke(_request())

    assert FORMAT == 2
    assert json.loads(tape.read_text(encoding="utf-8").splitlines()[0])["format"] == 2
    assert read_cassette(tape)[0].format == 2


async def test_a_format_1_tape_still_gets_its_rescue(tmp_path):
    """**And the rescue must survive for the tapes it was written for.** A PT33 entry keys
    on a prompt containing a random untrusted-block nonce that will never occur again, so the
    digest can never match it and only the weak key can find it."""
    nonce_prompt = (
        "the world as it stands\n<<<UNTRUSTED_A1B2C3D4E5F60718>>>\nhello\n"
        "<<<END_UNTRUSTED_A1B2C3D4E5F60718>>>"
    )
    entry = Entry(
        key="a-key-computed-before-canonicalisation", role="world_sim", provider="mock",
        model="m", prompt=nonce_prompt, ok=True, data={"v": "old"}, format=1,
    )
    player = ReplayProvider([entry])

    # The replay's own nonce differs, as it always will.
    now = nonce_prompt.replace("A1B2C3D4E5F60718", "0000111122223333")
    result = await player.invoke(_request(prompt=now))
    assert result.ok and result.data == {"v": "old"}


async def test_a_format_2_entry_is_never_rescued(tmp_path):
    """The gate itself. The same entry at format 2 must not be findable by the weak key --
    if it were, the fix would be cosmetic."""
    entry = Entry(
        key="a-key-that-will-not-match", role="world_sim", provider="mock", model="m",
        prompt="p", ok=True, data={"v": "old"}, format=2,
    )
    player = ReplayProvider([entry])
    with pytest.raises(CassetteMiss):
        await player.invoke(_request(prompt="p"))


def test_a_format_1_tape_is_still_readable(tmp_path):
    """Gating the rescue must not make the old tapes unreadable -- the PT33 recordings are
    the reason any of this exists."""
    tape = tmp_path / "old.jsonl"
    tape.write_text(
        json.dumps({"format": 1, "key": "k", "role": "world_sim", "provider": "mock",
                    "model": "m", "prompt": "p", "ok": True}) + "\n",
        encoding="utf-8",
    )
    entries = read_cassette(tape)
    assert len(entries) == 1 and entries[0].format == 1


def _entry_line() -> str:
    return json.dumps(Entry(key="k", role="world_sim", provider="mock", model="m",
                            prompt="p", ok=True).as_json())


def test_a_damaged_line_in_the_middle_is_a_hard_failure(tmp_path):
    """**From the Codex review of phase 2.** The interrupted-final-line case was being used
    to excuse every malformed line anywhere, so a tape with a corrupt entry in the middle
    replayed as though that call had never been made -- and the run it produced was quietly
    not the run that was recorded. Interrupted and damaged are different words."""
    tape = tmp_path / "run.jsonl"
    tape.write_text(
        _entry_line() + "\n" + '{"format": 2, "key": "corrupt' + "\n" + _entry_line() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="damaged rather than interrupted"):
        read_cassette(tape)


def test_a_line_missing_a_required_field_in_the_middle_is_a_hard_failure(tmp_path):
    """Readable JSON that is not an entry is damage too, not something to step over."""
    tape = tmp_path / "run.jsonl"
    tape.write_text(
        json.dumps({"format": 2, "role": "world_sim"}) + "\n" + _entry_line() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not an entry is damage"):
        read_cassette(tape)


# ------------------------------------------------------------------ the file itself


def test_an_interrupted_recording_still_replays(tmp_path):
    """The runs worth recording are the ones long enough to be interrupted."""
    tape = tmp_path / "run.jsonl"
    good = Entry(key="k", role="world_sim", provider="mock", model="m", prompt="p", ok=True)
    tape.write_text(
        json.dumps(good.as_json()) + "\n" + '{"format": 2, "key": "trunc', encoding="utf-8"
    )
    assert len(read_cassette(tape)) == 1


def test_a_cassette_from_another_format_is_refused_rather_than_guessed_at(tmp_path):
    """A truncated line is dropped; a whole format that has moved on is not.

    The two look similar and mean opposite things: one is a run that was interrupted, the
    other is a tape whose fields no longer mean what this build thinks they mean, and
    replaying the second would produce answers that are subtly about something else.
    """
    tape = tmp_path / "run.jsonl"
    tape.write_text(json.dumps({"format": 99, "key": "k"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        read_cassette(tape)


def test_the_key_ignores_what_cannot_change_the_answer():
    """The correlation id names the *order* a call was made about, not the question asked.
    Including it would make two identical questions miss each other."""
    base = ProviderRequest(role=Role.PARSER, prompt="p", model="m")
    assert digest_of(base) == digest_of(
        ProviderRequest(role=Role.PARSER, prompt="p", model="m", correlation_id="c1")
    )
    assert digest_of(base) != digest_of(
        ProviderRequest(role=Role.PARSER, prompt="p", model="m2")
    )
    assert digest_of(base) != digest_of(
        ProviderRequest(role=Role.PARSER, prompt="p", model="m",
                        options={"reasoning_effort": "high"})
    )
    # The schema is part of the question. Found by a test that rebuilt a request from a
    # recorded prompt, left the schema off, and missed -- correctly.
    assert digest_of(base) != digest_of(
        ProviderRequest(role=Role.PARSER, prompt="p", model="m",
                        schema={"type": "object"})
    )


def test_the_recorder_reports_the_provider_it_wraps():
    """One replay object stands in for every provider key in a run, so the *recorded*
    entry has to carry which one actually answered."""
    inner = MockProvider(name=Provider.CLAUDE)
    assert RecordingProvider(inner, "unused").name is Provider.CLAUDE


# ------------------------------------------- what "tolerated" means, exactly


def test_only_an_unparseable_final_line_is_tolerated(tmp_path):
    """An interrupted write leaves JSON that stops mid-token. That is the one case."""
    tape = tmp_path / "run.jsonl"
    tape.write_text(_entry_line() + "\n" + '{"format": 2, "key": "trunc', encoding="utf-8")
    assert len(read_cassette(tape)) == 1


def test_a_complete_final_line_that_is_not_an_entry_is_a_hard_failure(tmp_path):
    """**The sharper half of the rule.** A line that parses cleanly and is missing a
    required field was written in full and is wrong, wherever it sits -- tolerating it at
    the end would let a genuinely damaged final entry pass as an interrupted one."""
    tape = tmp_path / "run.jsonl"
    tape.write_text(
        _entry_line() + "\n" + json.dumps({"format": 2, "role": "world_sim"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not an entry is damage"):
        read_cassette(tape)


# ------------------------------------------------------- calls the engine never made


async def test_unused_recorded_calls_are_a_failure_not_a_warning(tmp_path):
    """A miss says the engine asked something the tape lacks. Leftovers say the opposite:
    the recorded run made a call this one did not, which is a call that has gone missing
    from the turn loop -- exactly as much of a regression, and it was a count nobody read."""
    tape = tmp_path / "run.jsonl"
    recorder = RecordingProvider(MockProvider(script=_BUSY), tape)
    await recorder.invoke(_request(prompt="asked once"))
    await recorder.invoke(_request(prompt="asked twice"))

    player = ReplayProvider.from_file(tape)
    await player.invoke(_request(prompt="asked once"))

    assert player.remaining() == 1
    assert [entry.prompt for entry in player.unused()] == ["asked twice"]
    with pytest.raises(CassetteMiss, match="did not make every call"):
        player.exhausted()


async def test_a_fully_consumed_tape_is_exhausted_quietly(tmp_path):
    """The other half: a replay that asked everything must not be told it did not."""
    tape = tmp_path / "run.jsonl"
    await RecordingProvider(MockProvider(script=_BUSY), tape).invoke(_request())

    player = ReplayProvider.from_file(tape)
    await player.invoke(_request())

    assert player.unused() == ()
    player.exhausted()


async def test_the_leftovers_say_which_roles_went_missing(tmp_path):
    """A count is not actionable; "the world sim was never called" is."""
    tape = tmp_path / "run.jsonl"
    recorder = RecordingProvider(MockProvider(script=_BUSY), tape)
    await recorder.invoke(_request(prompt="a", role=Role.WORLD_SIM))
    await recorder.invoke(_request(prompt="b", role=Role.PARSER))

    player = ReplayProvider.from_file(tape)
    with pytest.raises(CassetteMiss) as raised:
        player.exhausted()
    assert "world_sim" in str(raised.value) and "parser" in str(raised.value)
