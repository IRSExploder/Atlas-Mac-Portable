"""The provider layer, tested without spending a single model call.

Everything here is hermetic. The handful of tests that genuinely need a real CLI are
marked ``live`` and deselected by default, so an ordinary ``pytest`` run never touches a
subscription.
"""

from __future__ import annotations

import json

import pytest

from atlas.providers import agy_adapter, claude_adapter, codex_adapter
from atlas.providers.base import AuditVerdict, ProviderRequest
from atlas.providers.cli_provider import (
    EVE_BLOC_MODELS,
    default_role_models,
    eve_family_conflict,
    schema_shortfall,
    validate_pins,
)
from atlas.providers.environment import BANNED_VARS, build_child_env, leaked_keys
from atlas.providers.errors import Failure, FailureKind
from atlas.providers.mock import MockProvider, ScriptedResponse, always, cycle
from atlas.providers.resolver import resolve_all
from atlas.providers.sandbox import SandboxDir
from atlas.state.models import Role

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def _request(**kwargs) -> ProviderRequest:
    base = {"role": Role.PARSER, "prompt": "hello", "model": "test-model", "timeout_s": 30.0}
    base.update(kwargs)
    return ProviderRequest(**base)


# --------------------------------------------------------------------------- environment


@pytest.mark.parametrize("banned", sorted(BANNED_VARS))
def test_billing_variables_never_reach_a_child(tmp_path, monkeypatch, banned):
    """The expensive failure mode.

    A stray API key silently switches the CLI from the user's flat-rate subscription to
    metered billing. Nothing fails and nothing warns -- the bill just arrives later.
    """
    monkeypatch.setenv(banned, "leaked-value")
    env = build_child_env(tmp_path)
    assert banned not in env
    assert not leaked_keys(env)


def test_credentials_lookup_still_works(tmp_path, monkeypatch):
    """The opposite mistake: an environment so bare that OAuth cannot find its cache.

    Strip these and every provider reports itself logged out, which looks like an auth
    problem and is actually a bug here.
    """
    monkeypatch.setenv("USERPROFILE", r"C:\Users\test")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    env = build_child_env(tmp_path)
    for required in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        assert required in env


def test_temp_is_redirected_into_the_sandbox(tmp_path):
    env = build_child_env(tmp_path)
    assert env["TEMP"] == str(tmp_path)
    assert env["TMP"] == str(tmp_path)


def test_a_banned_key_passed_explicitly_is_refused(tmp_path):
    """Fail loudly rather than silently filtering: this can only be a programming error."""
    with pytest.raises(RuntimeError, match="billing"):
        build_child_env(tmp_path, extra={"OPENAI_API_KEY": "sk-whatever"})


# --------------------------------------------------------------------------- sandbox


def test_a_sandbox_is_created_inside_the_runs_root_and_removed():
    with SandboxDir.create() as sandbox:
        assert sandbox.path.exists()
        path = sandbox.path
        sandbox.write("note.txt", "hi")
        assert sandbox.read("note.txt") == "hi"
    assert not path.exists()


def test_debug_mode_keeps_the_sandbox():
    with SandboxDir.create(keep=True) as sandbox:
        path = sandbox.path
    assert path.exists()
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- claude


def test_claude_keeps_the_prompt_off_argv():
    """argv holds flags only.

    Windows caps a command line at 32,767 characters and a rendered world-state view
    exceeds that; the prompt goes over stdin instead.
    """
    argv = claude_adapter.build_argv("claude.exe", _request(prompt="x" * 50_000))
    assert not any("x" * 100 in arg for arg in argv)
    assert all(len(arg) < 2048 for arg in argv if not arg.startswith("{"))


def test_claude_disables_every_tool():
    """`--tools ""` is the documented kill switch; --allowedTools is a different flag."""
    argv = claude_adapter.build_argv("claude.exe", _request())
    assert argv[argv.index("--tools") + 1] == ""


def test_claude_never_uses_bare_mode():
    """--bare forces ANTHROPIC_API_KEY, which is the metered path."""
    argv = claude_adapter.build_argv("claude.exe", _request())
    assert "--bare" not in argv


def test_claude_passes_the_schema_inline():
    argv = claude_adapter.build_argv("claude.exe", _request(schema=SCHEMA))
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA


def test_claude_unwraps_the_double_encoded_result():
    """`result` is a JSON *string* even when a schema was enforced."""
    envelope = {"result": json.dumps({"x": "hi"}), "is_error": False}
    text, data, parsed = claude_adapter.parse_result(json.dumps(envelope))
    assert data == {"x": "hi"}
    assert parsed is not None
    assert text == '{"x": "hi"}'


def test_claude_audit_accepts_a_side_model():
    """A Haiku side-task rides along on healthy calls.

    Ranking by token count would call this a mismatch: a one-word Sonnet answer is
    outweighed by the bookkeeping call. Presence of the pinned model is the real test.
    """
    envelope = {
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"outputTokens": 900},
            "claude-sonnet-5": {"outputTokens": 4},
        }
    }
    audit = claude_adapter.audit_from_envelope("claude-sonnet-5", envelope)
    assert audit.verdict is AuditVerdict.MATCH
    assert audit.served == "claude-sonnet-5"
    assert "claude-haiku-4-5-20251001" in audit.side_models


def test_claude_audit_flags_a_genuine_substitution():
    envelope = {"modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 900}}}
    audit = claude_adapter.audit_from_envelope("claude-opus-5", envelope)
    assert audit.verdict is AuditVerdict.MISMATCH


def test_claude_audit_is_unverified_without_an_envelope():
    assert claude_adapter.audit_from_envelope("m", None).verdict is AuditVerdict.UNVERIFIED


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Error: not logged in", FailureKind.NOT_AUTHENTICATED),
        ("usage limit reached for this account", FailureKind.QUOTA_EXHAUSTED),
        ("429 rate limit exceeded", FailureKind.RATE_LIMITED),
    ],
)
def test_claude_classifies_failures(text, kind):
    assert claude_adapter.classify(1, "", text, None).kind is kind


# --------------------------------------------------------------------------- codex


def test_codex_uses_the_exec_subcommand_not_dash_p(sandbox):
    """`-p` means --profile in codex, not print. Getting this wrong is silent."""
    argv = codex_adapter.build_argv("codex.exe", _request(), sandbox)
    assert argv[1] == "exec"
    assert "-p" not in argv


@pytest.fixture
def sandbox():
    """A real per-call sandbox.

    Not ``tmp_path``: ``assert_grantable`` refuses any directory outside the runs root,
    which is the whole point of the allowlist. Using a scratch directory here would test
    a path production never takes.
    """
    with SandboxDir.create() as created:
        yield created.path


def test_codex_uses_the_read_only_sandbox(sandbox):
    argv = codex_adapter.build_argv("codex.exe", _request(), sandbox)
    assert argv[argv.index("--sandbox") + 1] == "read-only"


def test_codex_pins_reasoning_effort_down(sandbox):
    """The default is xhigh, which is absurd for a bounded parsing task."""
    argv = codex_adapter.build_argv("codex.exe", _request(), sandbox)
    assert "model_reasoning_effort=low" in argv


def test_codex_writes_the_schema_to_a_file(sandbox):
    """Unlike Claude, codex takes a path rather than an inline string."""
    argv = codex_adapter.build_argv("codex.exe", _request(schema=SCHEMA), sandbox)
    from pathlib import Path

    written = Path(argv[argv.index("--output-schema") + 1])
    assert json.loads(written.read_text()) == SCHEMA


def test_codex_audit_reads_the_banner_from_stderr():
    """The banner is on stderr; stdout holds only the answer.

    An audit that reads stdout finds nothing and reports a healthy call as unverified.
    """
    stderr = "workdir: C:\\x\nmodel: gpt-5.6-terra\nprovider: openai\nsandbox: read-only\n"
    audit = codex_adapter.audit_from_output("gpt-5.6-terra", "ATLAS", stderr)
    assert audit.verdict is AuditVerdict.MATCH
    assert audit.served == "gpt-5.6-terra"


def test_codex_benign_stderr_is_not_a_failure():
    """A stale models-cache warning is present on a healthy install."""
    noise = "ERROR codex_models_manager::cache: failed to load models cache: missing field `supports_reasoning_summaries`"
    assert codex_adapter.classify(0, "fine", noise) is None


def _codex_session(answer: str, *, prompt: str = "", tail: str = "") -> str:
    """codex 0.144.5 stderr in the shape captured verbatim in tests/test_usage.py.

    The banner, then the transcript: ``user`` and the prompt when it is echoed, ``codex``
    and the model's answer. Codex's own reporting follows as ``tail``.
    """
    user = f"user\n{prompt}\n" if prompt else ""
    return (
        "OpenAI Codex v0.144.5\n--------\nmodel: gpt-5.6-terra\nreasoning effort: low\n"
        f"--------\n{user}codex\n{answer}\n{tail}tokens used\n12,394\n"
    )


@pytest.mark.parametrize("narrative", [
    "Our reserves are insufficient for a second front; we hold the Rhine.",
    "We impose an import quota on Veyran steel.",
    "The billing of the war loan falls to the treasury.",
    "Parliament withholds authentication of the treaty until spring.",
    "The rate limit on grain exports holds; 429 men cross at dawn.",
    "Not logged in the registry: the envoy is unauthorized to sign.",
])
def test_codex_an_answer_in_the_transcript_is_not_a_failure(narrative):
    """Codex echoes the model's answer to stderr, so the answer is not a diagnosis.

    A valid batch whose narrative said "insufficient" or "an import quota" was read as
    a quota wall: terminal, never retried, the turn blocked and the batch discarded.
    Deliberately passes no ``text``, so the transcript cut alone has to hold.
    """
    answer = json.dumps({"narrative": narrative, "diffs": []})
    assert codex_adapter.classify(0, answer, _codex_session(answer)) is None


def test_codex_an_echoed_prompt_is_not_a_failure():
    """The ``user`` section is the game's own prompt, and game prompts talk about quotas."""
    stderr = _codex_session(
        '{"answer":"OK"}',
        prompt="Veyra's import quota is insufficient.\n\nBilling and authentication follow.",
    )
    assert codex_adapter.classify(0, '{"answer":"OK"}', stderr) is None


def test_codex_a_clean_exit_with_an_answer_is_a_success_whatever_else_it_said():
    """A throttle Codex retried through does not undo the answer that then arrived."""
    stderr = _codex_session(
        '{"answer":"OK"}',
        tail="stream error: exceeded retry limit, last status: 429 Too Many Requests; retrying 1/5\n",
    )
    assert codex_adapter.classify(0, "", stderr, text='{"answer":"OK"}') is None
    # Without an answer the same stderr is a throttle, and is still read as one.
    assert codex_adapter.classify(0, "", stderr).kind is FailureKind.RATE_LIMITED


@pytest.mark.parametrize(("report", "kind"), [
    ("ERROR: You've hit your usage limit. Try again in 3 hours.", FailureKind.QUOTA_EXHAUSTED),
    ("ERROR: Quota exceeded. Check your plan and billing details.", FailureKind.QUOTA_EXHAUSTED),
    ("ERROR: exceeded retry limit, last status: 429 Too Many Requests", FailureKind.RATE_LIMITED),
    ("ERROR: unexpected status 401 Unauthorized: token expired", FailureKind.NOT_AUTHENTICATED),
    ("stream error: rate limit reached for requests", FailureKind.RATE_LIMITED),
    ("[2025-08-01T12:00:03] ERROR: You've hit your usage limit.", FailureKind.QUOTA_EXHAUSTED),
])
def test_codex_a_real_failure_after_the_transcript_is_still_read(report, kind):
    """Cutting the transcript must not cut Codex's own report printed right after it."""
    stderr = _codex_session("partial", prompt="Order the fleet south.", tail=report + "\n")
    failure = codex_adapter.classify(1, "", stderr)
    assert failure is not None
    assert failure.kind is kind
    assert report in failure.detail


def test_codex_a_failure_before_any_transcript_is_still_read():
    """A logged-out CLI fails before the session starts: no banner, no transcript."""
    failure = codex_adapter.classify(1, "", "Error: Not logged in. Please run `codex login`.\n")
    assert failure.kind is FailureKind.NOT_AUTHENTICATED


def test_codex_a_crash_mid_answer_is_a_crash_not_a_quota_wall():
    """A non-zero exit still fails, but the answer's words do not name the failure."""
    answer = '{"narrative":"Our reserves are insufficient"'
    failure = codex_adapter.classify(1, "", _codex_session(answer), text=answer)
    assert failure.kind is FailureKind.CRASHED


async def test_codex_provider_keeps_a_valid_answer_that_mentions_a_quota(monkeypatch):
    """End to end through CliProvider: the batch in the ``-o`` file survives."""
    from pathlib import Path

    from atlas.providers import cli_provider
    from atlas.providers.process import ProcessOutcome
    from atlas.providers.resolver import ResolvedCli

    answer = json.dumps({"x": "We impose an import quota; our reserves are insufficient."})

    async def fake_run_cli(argv, *, cwd, env, stdin_payload=None, timeout_s=300.0):
        Path(argv[argv.index("-o") + 1]).write_text(answer, encoding="utf-8")
        return ProcessOutcome(tuple(argv), 0, "", _codex_session(answer), 0.1)

    monkeypatch.setattr(cli_provider, "run_cli", fake_run_cli)
    provider = cli_provider.CliProvider("codex", ResolvedCli("codex", Path("codex")))
    result = await provider.invoke(_request(model="gpt-5.6-terra", schema=SCHEMA))
    assert result.ok, result.failure and result.failure.describe()
    assert result.data == json.loads(answer)


# --------------------------------------------------------------------------- agy


def test_agy_puts_the_prompt_on_argv_not_stdin(sandbox):
    """agy silently discards a piped prompt."""
    argv, prompt_file = agy_adapter.build_argv("agy.exe", _request(prompt="short"), sandbox)
    assert prompt_file is None
    assert argv[argv.index("--print") + 1] == "short"


def test_agy_moves_a_long_prompt_into_a_file(sandbox):
    """Past the command-line ceiling the prompt is written out and pointed at."""
    long_prompt = "y" * 20_000
    argv, prompt_file = agy_adapter.build_argv("agy.exe", _request(prompt=long_prompt), sandbox)
    assert prompt_file == long_prompt
    assert "--add-dir" in argv
    pointer = argv[argv.index("--print") + 1]
    assert len(pointer) < 500
    assert agy_adapter.PROMPT_FILE in pointer


def test_agy_is_told_to_read_the_prompt_file_with_its_file_tool_and_never_a_shell(sandbox):
    """**The fix for Gemini's empty answers.** Left to choose, agy often read the file with a
    shell command, which headless mode auto-denies; it then exits SUCCESS with an empty
    response. The pointer names the file tool and forbids the shell (A/B 2026-09-23: old
    wording 5 of 10 denied, this 0 of 10)."""
    argv, _ = agy_adapter.build_argv("agy.exe", _request(prompt="y" * 20_000), sandbox)
    pointer = argv[argv.index("--print") + 1]
    assert "file-viewing tool" in pointer
    assert "do not run any shell or terminal command" in pointer
    assert str(sandbox / agy_adapter.PROMPT_FILE) in pointer


def test_agy_always_passes_print_explicitly(sandbox):
    """A bare agy with no TTY waits forever for an approval prompt that never renders."""
    argv, _ = agy_adapter.build_argv("agy.exe", _request(), sandbox)
    assert "--print" in argv


def test_agy_sets_its_own_timeout_under_the_wall_deadline(sandbox):
    """print-timeout defaults to 5m; agy should self-terminate before we kill it."""
    argv, _ = agy_adapter.build_argv("agy.exe", _request(timeout_s=200.0), sandbox)
    assert argv[argv.index("--print-timeout") + 1] == "180s"


@pytest.mark.parametrize(
    "greeting",
    [
        "I am currently running on **Gemini 3.6 Flash (High)**.",
        "I'm running on gemini-3.6-flash-low",
        "I am running on Claude Sonnet 4.6.",
    ],
)
def test_agy_canned_greeting_is_detected(greeting):
    """The failure that looks like success.

    Given no prompt agy answers with this instead of erroring, and it would otherwise
    flow into the engine as though a nation had spoken.
    """
    assert agy_adapter.looks_like_canned_greeting(greeting)
    failure = agy_adapter.classify(0, "", "", text=greeting)
    assert failure is not None
    assert failure.kind is FailureKind.PROMPT_NOT_DELIVERED


def test_a_real_answer_is_not_mistaken_for_the_greeting():
    for real in (
        "Poland proposes a non-aggression pact.",
        '{"diffs": []}',
        "I am running low on manpower after the winter offensive, and my ministers are restless.",
    ):
        assert not agy_adapter.looks_like_canned_greeting(real)


def test_agy_rejected_model_is_reported_usefully():
    stderr = 'Error: invalid model selection (--model "Gemini 3.6 Flash"): model is not recognized as a known model'
    failure = agy_adapter.classify(1, "", stderr)
    assert failure.kind is FailureKind.MODEL_MISMATCH
    assert "agy models" in failure.message


@pytest.mark.parametrize(
    "listing",
    [
        "Gemini 3.6 Flash (High)\nGemini 3.1 Pro (Low)\n",           # agy 1.1.4
        "gemini-3.6-flash-high\ngemini-3.1-pro-low\n",               # agy 1.1.8
    ],
)
def test_agy_model_listing_parses_in_both_schemes(listing):
    """1.1.4 listed display names, 1.1.8 lists slugs. Both must parse."""
    assert len(agy_adapter.parse_models(listing)) == 2


def test_agy_model_names_normalise_across_schemes():
    assert agy_adapter.normalise_model("Gemini 3.6 Flash (High)") == agy_adapter.normalise_model(
        "gemini-3.6-flash-high"
    )


def test_agy_audit_is_honestly_unverified():
    """agy attests nothing, and its greeting proves self-reports are worthless."""
    assert agy_adapter.audit("gemini-3.6-flash-low", "anything").verdict is AuditVerdict.UNVERIFIED


# ------------------------------------------------------- the empty answer that "succeeded"

#: Exactly what agy returns when the model produces nothing: exit 0, SUCCESS, and an
#: empty `response`. The envelope even echoes the request's schema back, so it looks
#: structured while carrying no answer at all.
_EMPTY_ENVELOPE = json.dumps(
    {
        "conversation_id": "abc-123",
        "status": "SUCCESS",
        "response": "",
        "duration_seconds": 8.9,
        "num_turns": 1,
        "json_schema": SCHEMA,
        "usage": {"input_tokens": 26051, "output_tokens": 0},
    }
)


def test_agy_empty_response_does_not_become_the_answer():
    """The regression that silenced every tracked nation for a whole game.

    The envelope was handed back as `data`, so a call that answered nothing parsed into
    an empty batch, applied cleanly, and reported that the nation had acted.
    """
    text, data = agy_adapter.parse_result(_EMPTY_ENVELOPE)
    assert data is None
    assert text == ""


def test_agy_transport_envelope_is_recognised():
    assert agy_adapter.looks_like_transport_envelope(json.loads(_EMPTY_ENVELOPE))


def test_a_real_structured_answer_is_not_mistaken_for_the_envelope():
    """agy returns the caller's object directly when a schema is enforced."""
    answer = {"narrative": "Poland mobilises.", "diffs": [], "events": []}
    assert not agy_adapter.looks_like_transport_envelope(answer)
    _text, data = agy_adapter.parse_result(json.dumps(answer))
    assert data == answer


def test_an_answer_carrying_its_own_status_field_is_still_an_answer():
    """Both markers must line up, or a legitimate `status` key trips the guard."""
    answer = {"status": "ok", "response": "Poland agrees to talks.", "diffs": []}
    assert not agy_adapter.looks_like_transport_envelope(answer)


# --------------------------------------------------------------------- schema shortfall


def test_a_response_with_none_of_the_required_keys_is_a_shortfall():
    assert schema_shortfall(SCHEMA, {"conversation_id": "abc", "status": "SUCCESS"}) == ["x"]


def test_a_response_that_answers_is_not_a_shortfall():
    assert schema_shortfall(SCHEMA, {"x": "hello"}) == []


def test_a_partial_answer_is_left_to_the_schema():
    """Only a response supplying *nothing* is caught here; partials are usable."""
    schema = {"type": "object", "required": ["a", "b"]}
    assert schema_shortfall(schema, {"a": 1}) == []
    assert schema_shortfall(schema, {"c": 1}) == ["a", "b"]


def test_a_schema_without_required_keys_can_never_fall_short():
    assert schema_shortfall({"type": "object"}, {}) == []


def test_a_non_object_response_falls_short_of_every_key():
    assert schema_shortfall(SCHEMA, ["not", "an", "object"]) == ["x"]


def test_empty_response_is_retryable_but_does_not_block_the_turn():
    """It is intermittent: the same call usually answers on a later attempt."""
    assert FailureKind.EMPTY_RESPONSE.retryable
    assert not FailureKind.EMPTY_RESPONSE.blocks_turn
    assert Failure(FailureKind.EMPTY_RESPONSE, "antigravity", "no answer").remedy


# --------------------------------------------------------------------------- pins


def test_default_pins_avoid_the_model_that_does_not_exist():
    """`Gemini 3.5 Flash-Lite` was in the original design and is not offered."""
    for _role, (_provider, model) in default_role_models().items():
        assert "flash-lite" not in model.lower()


def test_no_role_is_pinned_to_a_low_agy_tier():
    """Measured: a call that succeeds 3/3 on flash-medium is empty 3/3 on flash-low.

    That tier does not produce schema-constrained output through agy at all, and every
    role asks for exactly that. Focus Mode and the arbiter were both pinned there.
    """
    for role, (provider, model) in default_role_models().items():
        if provider == "antigravity":
            assert not model.endswith("-low"), f"{role.value} is pinned to a -low agy tier"


def test_world_sim_and_tracked_agents_stay_on_different_providers():
    """Focus Mode exists to add a second judgment source, not to echo the first."""
    pins = default_role_models()
    assert pins[Role.WORLD_SIM][0] != pins[Role.TRACKED_AGENT][0]


def test_the_world_simulator_s_attestation_cost_is_recorded_rather_than_forgotten():
    """**This test used to require the opposite, and the change is the point of it.**

    PT33 moved the world simulation to claude partly because agy does not attest which model
    served a call, so the single most consequential output in the engine was permanently
    `UNVERIFIED`. That guard said a future re-pin "should have to argue with this". It did:
    391 recorded world-sim calls put claude at roughly six times agy's latency on the same
    prompt and at a comparable crash rate -- 27 consecutive `claude exited with code 1` in
    one run -- so the swap bought neither speed nor reliability, and attestation alone did
    not carry it.

    What replaces the old assertion is not nothing. The role is back on a provider that
    cannot attest, and that is a **known, chosen cost**: the fallback the breaker reaches
    for does attest, so a session that trips it becomes verifiable at the moment reliability
    is actually in question, which is when it matters most.
    """
    from atlas.providers.cli_provider import ATTESTING, WORLD_SIM_FALLBACK

    provider, _model = default_role_models()[Role.WORLD_SIM]
    assert provider not in ATTESTING, (
        "the world simulator is on an attesting provider again -- if that was deliberate, "
        "this test and the note in `default_role_models` are what should change with it"
    )
    assert WORLD_SIM_FALLBACK[0] in ATTESTING, (
        "the fallback must attest, or a tripped breaker gives up reliability *and* "
        "verifiability at once"
    )


def test_the_world_simulation_falls_back_to_a_different_provider_than_it_starts_on():
    """A fallback to the same place is not a fallback."""
    from atlas.providers.cli_provider import WORLD_SIM_FALLBACK

    assert default_role_models()[Role.WORLD_SIM][0] != WORLD_SIM_FALLBACK[0]


def test_pins_are_validated_against_the_installed_cli():
    problems = validate_pins(
        {Role.WORLD_SIM: ("antigravity", "gemini-9.9-imaginary")},
        {"antigravity": ("gemini-3.6-flash-high",)},
    )
    assert problems and "does not offer" in problems[0]


def test_eve_blocs_are_three_distinct_families():
    """The premise of EVE is three different judgment sources, not three personas."""
    assert eve_family_conflict(EVE_BLOC_MODELS) is None


def test_eve_rejects_two_blocs_from_one_family():
    """agy can route to Claude models, which would quietly collapse the comparison."""
    conflict = eve_family_conflict(
        {
            "A": ("claude", "claude-sonnet-5"),
            "B": ("codex", "gpt-5.6-terra"),
            "C": ("antigravity", "claude-sonnet-4-6"),
        }
    )
    assert conflict is not None and "claude" in conflict


# --------------------------------------------------------------------------- failures


def test_auth_and_install_failures_are_not_retried():
    """Retrying these wastes time and hides the message that would fix them."""
    assert not FailureKind.NOT_AUTHENTICATED.retryable
    assert not FailureKind.NOT_INSTALLED.retryable
    assert FailureKind.TIMEOUT.retryable
    assert FailureKind.RATE_LIMITED.retryable


def test_blocking_failures_stop_the_turn():
    assert FailureKind.NOT_AUTHENTICATED.blocks_turn
    assert FailureKind.QUOTA_EXHAUSTED.blocks_turn
    assert not FailureKind.TIMEOUT.blocks_turn


def test_every_failure_offers_an_actionable_remedy():
    for kind in FailureKind:
        failure = Failure(kind, "claude", "x")
        assert failure.remedy, f"{kind} has no remedy text"


def test_auth_remedy_names_the_right_command():
    assert "claude" in Failure(FailureKind.NOT_AUTHENTICATED, "claude", "").remedy
    assert "codex login" in Failure(FailureKind.NOT_AUTHENTICATED, "codex", "").remedy


# --------------------------------------------------------------------------- mock


async def test_mock_returns_scripted_data():
    provider = MockProvider(script=always(ScriptedResponse(data={"x": "hi"})))
    result = await provider.invoke(_request())
    assert result.ok
    assert result.data == {"x": "hi"}


async def test_mock_records_every_request():
    """Used to assert what a context view actually contained."""
    provider = MockProvider()
    await provider.invoke(_request(prompt="poland view"))
    assert provider.calls[0].prompt == "poland view"


async def test_mock_can_fail_on_demand():
    provider = MockProvider(script=always(ScriptedResponse(failure_kind=FailureKind.QUOTA_EXHAUSTED)))
    result = await provider.invoke(_request())
    assert result.failed
    assert result.failure.kind is FailureKind.QUOTA_EXHAUSTED


async def test_mock_can_return_malformed_output():
    provider = MockProvider(script=always(ScriptedResponse(malformed=True)))
    result = await provider.invoke(_request(schema=SCHEMA))
    assert result.failure.kind is FailureKind.MALFORMED_OUTPUT


async def test_mock_cycles_through_a_script():
    provider = MockProvider(
        script=cycle([ScriptedResponse(data={"n": 1}), ScriptedResponse(data={"n": 2})])
    )
    first = await provider.invoke(_request())
    second = await provider.invoke(_request())
    third = await provider.invoke(_request())
    assert (first.data, second.data) == ({"n": 1}, {"n": 2})
    assert third.data == {"n": 2}, "the last response repeats once exhausted"


def test_mock_randomness_is_independent_of_the_engine():
    """Seeding the mock from the world stream would shift every battle result whenever a
    provider call is added or removed, destroying replay."""
    a = MockProvider()
    b = MockProvider()
    assert a._rng.random() == b._rng.random()


# --------------------------------------------------------------------------- live


@pytest.mark.live
async def test_live_every_provider_answers_and_attests():
    """Opt-in. Proves the flags have not drifted and the logins are current."""
    from atlas.providers.diagnostics import probe_live

    for key in resolve_all():
        result = await probe_live(key, timeout_s=180.0)
        if result is None:
            continue
        assert result.ok, f"{key}: {result.failure.describe() if result.failure else '?'}"
        assert "ATLAS" in result.text.upper()


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize("module_name, provider", [
    ("agy_adapter", "antigravity"),
    ("claude_adapter", "claude"),
    ("codex_adapter", "codex"),
])
def test_a_throttle_that_mentions_auth_is_still_a_throttle(module_name, provider):
    """Specific before generic, and the order is load-bearing.

    Providers routinely mention authentication in a throttle message. Read as a
    logged-out CLI it is terminal and never retried, so one call in five would halt its
    role while the other four succeeded on the same credentials.
    """
    import importlib

    from atlas.providers.errors import FailureKind

    module = importlib.import_module(f"atlas.providers.{module_name}")
    failure = _classify(module, "429 too many requests; re-authentication may be required")
    assert failure is not None
    assert failure.kind is FailureKind.RATE_LIMITED
    assert failure.kind.retryable


@pytest.mark.parametrize("module_name", ["agy_adapter", "claude_adapter", "codex_adapter"])
def test_a_genuine_sign_out_is_still_terminal(module_name):
    """Reordering must not turn a real logged-out CLI into an endless retry."""
    import importlib

    from atlas.providers.errors import FailureKind

    module = importlib.import_module(f"atlas.providers.{module_name}")
    failure = _classify(module, "not logged in: please log in to continue")
    assert failure is not None
    assert failure.kind is FailureKind.NOT_AUTHENTICATED
    assert not failure.kind.retryable


@pytest.mark.parametrize("module_name", ["agy_adapter", "claude_adapter", "codex_adapter"])
def test_quota_outranks_a_generic_auth_mention(module_name):
    """A quota wall needs a human too, but it is not a sign-in problem."""
    import importlib

    from atlas.providers.errors import FailureKind

    module = importlib.import_module(f"atlas.providers.{module_name}")
    failure = _classify(module, "quota exceeded for this account; sign in again")
    assert failure is not None
    assert failure.kind is FailureKind.QUOTA_EXHAUSTED


def _classify(module, stderr: str):
    """Call an adapter's classify however that adapter happens to declare it."""
    import inspect

    params = inspect.signature(module.classify).parameters
    extra: dict = {}
    if "envelope" in params:
        extra["envelope"] = None
    if "text" in params:
        extra["text"] = ""
    return module.classify(1, "", stderr, **extra)


# ------------------------------------- isolation, in whatever environment this is


def test_the_isolation_check_accepts_a_sandbox_it_just_created():
    r"""**The end-to-end check that would have caught the packaged-app failure.**

    Every one of the ten tests that needs a sandbox errored on this machine because
    `assert_isolated` refused the directory `SandboxDir.create()` had just made: Windows
    MSIX virtualization redirects writes under `%LOCALAPPDATA%` into the package's private
    store, so a freshly created sandbox resolved to
    `...\Packages\<pkg>\LocalCache\Local\Atlas
uns\...` while `runs_root()` -- older
    than the redirect -- resolved to itself, and a string comparison called the same place
    two places.

    Asserted through the real object rather than against a hardcoded shape, so it holds
    on any machine and fails on any machine where the two disagree again.
    """
    from atlas.config.paths import assert_grantable, assert_isolated

    with SandboxDir.create() as sandbox:
        assert assert_isolated(sandbox.path)
        assert assert_grantable(sandbox.path)
        nested = sandbox.path / "deeper"
        nested.mkdir()
        assert assert_isolated(nested)


@pytest.mark.parametrize(
    "where",
    [
        "project_root",
        "home",
        "tempdir",
        "worlds_root",
        "fake_virtualized",
    ],
)
def test_the_isolation_check_still_refuses_everywhere_else(where):
    r"""**The property the fix must not have bought its way past.**

    Containment is decided by filesystem identity now rather than by string prefix, which
    is what lets the redirected sandbox through -- so this pins that nothing *else* came
    through with it. `fake_virtualized` is the specific hole a textual rewrite of
    `Packages\<pkg>\LocalCache\Local` back to `Local` would have opened: a path spelled
    like the private store of some other package. It refuses because no such directory
    exists to have an identity, which is the whole reason the fix asks the filesystem.
    """
    import tempfile
    from pathlib import Path

    from atlas.config.paths import (
        IsolationError,
        assert_isolated,
        project_root,
        worlds_root,
    )

    target = {
        "project_root": project_root(),
        "home": Path.home(),
        "tempdir": Path(tempfile.gettempdir()),
        "worlds_root": worlds_root(),
        "fake_virtualized": Path.home()
        / "AppData" / "Local" / "Packages" / "Fake_pkg" / "LocalCache" / "Local"
        / "Atlas" / "runs" / "evil",
    }[where]

    with pytest.raises(IsolationError):
        assert_isolated(target)


def test_no_provider_test_is_quietly_switched_off():
    """A broken environment must not be "fixed" by marking the tests skip.

    That is the shape this regresses into: the sandbox failures were loud -- eight errors
    and two failures -- and became invisible only once the module was excluded from the
    run. The `live` mark is the one legitimate exclusion, because those tests spend real
    quota; anything else carrying a skip or xfail here is a test that has stopped
    defending what it was written for.
    """
    import pathlib
    import re

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"@pytest\.mark\.(skip|xfail)", line)
        or re.search(r"pytest\.skip\(.*allow_module_level", line)
        or re.match(r"\s*pytestmark\s*=", line)
    ]
    assert not offenders, offenders
