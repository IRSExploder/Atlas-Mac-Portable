"""A Claude session limit is a used-up allowance, and a match pauses on it.

**The incident.** Three Fronts rotation 2 (2026-09-23): from turn 4 every Claude call came
back as the envelope below, with exit code 1. `classify` read its `subtype` ("success"),
found no "limit" there, and filed the exit code as CRASHED; EVE then logged the bloc's
failure as "failed", which the pause check never reads. So the harness played ten more
rounds with the Eurasian Axis silent instead of pausing. Two gaps, one test each side:

* the adapter must call this envelope QUOTA_EXHAUSTED (and a mere throttle RATE_LIMITED);
* EVE must log a bloc's used-up allowance as ``quota_exhausted``, the row
  `quota.exhausted_providers` -- the harnesses' pause signal -- looks for.
"""

from __future__ import annotations

import json

import pytest

from atlas.engine.eve import Bloc, EveEngine, assign_blocs
from atlas.engine.quota import exhausted_providers
from atlas.engine.routing import Router
from atlas.providers import claude_adapter
from atlas.providers.cli_provider import default_role_models
from atlas.providers.errors import FailureKind
from atlas.providers.mock import MockProvider, ScriptedResponse
from atlas.state.models import Role

WORLD = "sandbox"

#: The envelope as the CLI printed it (trimmed of its zeroed usage block), and the stderr it
#: came with: schema warnings that have nothing to do with the limit.
SESSION_LIMIT = json.dumps({
    "type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
    "duration_ms": 542, "duration_api_ms": 0, "num_turns": 1,
    "result": "You've hit your session limit · resets 4:10am (Asia/Yerevan)",
    "stop_reason": "stop_sequence", "session_id": "9f1bb72a", "total_cost_usd": 0})
STDERR = ('strict mode: use allowUnionTypes to allow union type keyword at '
          '"#/properties/diffs/items/properties/new_value" (strictTypes)')


def _classify(stdout: str, exit_code: int = 1):
    _text, _data, envelope = claude_adapter.parse_result(stdout)
    return claude_adapter.classify(exit_code, stdout, STDERR, envelope)


# ------------------------------------------------------------------------ the adapter


def test_the_session_limit_envelope_is_a_used_up_allowance():
    failure = _classify(SESSION_LIMIT)
    assert failure is not None
    assert failure.kind is FailureKind.QUOTA_EXHAUSTED
    assert "session limit" in failure.message
    # What that kind means downstream: not retried, and it halts rather than degrades.
    assert not failure.kind.retryable
    assert failure.kind.blocks_turn


@pytest.mark.parametrize("said", [
    "Claude usage limit reached. Your limit will reset at 5pm.",
    "You've reached your weekly limit",
])
def test_other_allowance_wordings_are_quota_too(said):
    envelope = json.loads(SESSION_LIMIT) | {"result": said}
    assert _classify(json.dumps(envelope)).kind is FailureKind.QUOTA_EXHAUSTED


def test_a_plain_429_is_a_throttle_and_is_retried():
    envelope = json.loads(SESSION_LIMIT) | {"result": "Too many requests. Please slow down."}
    failure = _classify(json.dumps(envelope))
    assert failure.kind is FailureKind.RATE_LIMITED
    assert failure.kind.retryable


def test_a_model_answer_that_mentions_a_session_limit_is_just_an_answer():
    """stdout is the model's text; only the CLI's own error fields may be read."""
    answer = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                         "result": "{\"narrative\": \"We have hit our session limit of "
                                   "patience with Pyongyang.\"}"})
    assert _classify(answer, exit_code=0) is None


def test_without_a_status_an_errored_envelope_still_falls_through_as_before():
    """The change is keyed on the 429 status; an unrelated error keeps its old reading."""
    envelope = {"type": "result", "subtype": "error_max_structured_output_retries",
                "is_error": True, "result": None}
    assert _classify(json.dumps(envelope)).kind is FailureKind.CRASHED


# ------------------------------------------------------------------------ the pause signal


def _blocs():
    return [Bloc(id=f"bloc_{n}", name=f"Bloc {n}", provider_key=k, model=m,
                 colour="#888888", nations=(n,))
            for n, k, m in (("aurelia", "claude", "claude-sonnet-5"),
                            ("karthos", "codex", "gpt-5.6-terra"),
                            ("veyra", "antigravity", "gemini-3.6-flash-high"))]


def _round(conn, failure_for_claude: FailureKind | None):
    assign_blocs(conn, WORLD, _blocs())

    def answer(request):
        if request.model.startswith("claude") and failure_for_claude is not None:
            return ScriptedResponse(failure_kind=failure_for_claude)
        return ScriptedResponse(data={"narrative": "", "diffs": [], "events": []})

    provider = MockProvider(script=answer)
    engine = EveEngine(conn=conn, world_id=WORLD,
                       providers={k: provider for k in ("claude", "codex", "antigravity")},
                       router=Router(pins=default_role_models()))
    return engine


async def test_a_bloc_whose_allowance_is_used_up_raises_the_pause_signal(conn):
    report = await _round(conn, FailureKind.QUOTA_EXHAUSTED).run_round()

    assert exhausted_providers(conn, WORLD, since_turn=report.turn) == {"claude"}
    outcome = conn.execute("SELECT outcome FROM routing_log WHERE role = ? AND nation_id ="
                           " 'aurelia'", (Role.TRACKED_AGENT.value,)).fetchone()[0]
    assert outcome == "quota_exhausted"


@pytest.mark.parametrize("kind", [FailureKind.CRASHED, FailureKind.EMPTY_RESPONSE])
async def test_an_ordinary_failure_does_not_pause_and_keeps_its_label(conn, kind):
    report = await _round(conn, kind).run_round()

    assert exhausted_providers(conn, WORLD, since_turn=report.turn) == set()
    assert conn.execute("SELECT outcome FROM routing_log WHERE nation_id = 'aurelia'"
                        ).fetchone()[0] == "failed"
