"""Claude Code, driven as ``claude -p``.

Verified against claude 2.1.212:

* The prompt goes over **stdin**. Keeping argv to flags only avoids the 32,767-character
  Windows command-line ceiling, which a rendered world-state view exceeds easily.
* ``--tools ""`` disables every built-in tool. This is the documented kill switch; the
  spec's "empty ``--allowedTools``" is a different flag that does not do this.
* ``--json-schema`` takes the schema **inline as a string** and genuinely constrains the
  output.
* ``--output-format json`` wraps the answer in an envelope whose ``result`` field holds
  the structured answer *as a JSON string*, needing one further parse.
* That envelope also carries ``modelUsage``, naming every model that served the call --
  a real attestation channel, used here to verify the pin actually held.

Never ``--bare``: it skips OAuth and the keychain and demands ``ANTHROPIC_API_KEY``,
which is precisely the metered path this design exists to avoid.
"""

from __future__ import annotations

import json
from typing import Any

from ..state.models import Provider
from .base import AuditVerdict, ModelAudit, ProviderRequest
from .errors import Failure, FailureKind

__all__ = ["build_argv", "classify", "audit_from_envelope", "parse_result"]


def build_argv(executable: str, request: ProviderRequest) -> list[str]:
    """Flags only. The prompt is delivered on stdin."""
    argv = [
        executable,
        "-p",
        "--model", request.model,
        # Tool access is denied at the CLI level, not by asking the model nicely.
        # A prompt-level instruction is not a security boundary.
        "--tools", "",
        "--output-format", "json",
        # Nothing about this call should persist or be resumable.
        "--no-session-persistence",
        # Ignore any MCP servers or settings files the user has configured globally:
        # a game turn must not inherit the developer's environment.
        "--strict-mcp-config",
        "--setting-sources", "",
    ]

    if request.schema is not None:
        argv += ["--json-schema", json.dumps(request.schema, separators=(",", ":"))]
    if request.system_prompt:
        argv += ["--append-system-prompt", request.system_prompt]

    budget = request.options.get("max_budget_usd")
    if budget:
        argv += ["--max-budget-usd", str(budget)]
    return argv


def parse_result(stdout: str) -> tuple[str, Any, dict | None]:
    """Unwrap the envelope. Returns ``(text, parsed_data, envelope)``.

    ``result`` is a *string* even when a schema was enforced, so structured answers need
    a second ``json.loads``. A response that fails that second parse is returned as plain
    text and classified as malformed upstream.
    """
    stripped = stdout.strip()
    if not stripped:
        return "", None, None

    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped, None, None

    if not isinstance(envelope, dict):
        return stripped, None, None

    text = str(envelope.get("result", ""))
    data: Any = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    return text, data, envelope


def audit_from_envelope(requested: str, envelope: dict | None) -> ModelAudit:
    """Check that the pinned model actually served the call.

    ``modelUsage`` is not exclusive: a Haiku side-task appears alongside the primary model
    on healthy calls, so asserting exclusivity would fail constantly. The primary is taken
    to be the model with the most output tokens, and everything else is recorded as a side
    model rather than treated as a violation.
    """
    if not envelope:
        return ModelAudit(requested, None, AuditVerdict.UNVERIFIED, note="no JSON envelope")

    usage = envelope.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return ModelAudit(requested, None, AuditVerdict.UNVERIFIED, note="no modelUsage block")

    def matches(name: str) -> bool:
        # An alias like "sonnet" legitimately resolves to a dated id, so compare by
        # prefix in both directions rather than demanding an exact string match.
        return name == requested or name.startswith(requested) or requested.startswith(name)

    # The question is "did the pinned model serve this call?", not "which model did the
    # most work?". Ranking by token count gets that wrong on short answers: a one-word
    # reply from Sonnet is outweighed by the Haiku side-task Claude Code runs for session
    # bookkeeping, and the audit reports a mismatch on a perfectly healthy call. Presence
    # is the correct test; everything else is a side model.
    served = next((name for name in usage if matches(name)), None)
    others = tuple(name for name in usage if name != served)

    if served is None:
        busiest = max(usage, key=lambda name: _output_tokens(usage[name]), default=None)
        return ModelAudit(
            requested=requested,
            served=busiest,
            verdict=AuditVerdict.MISMATCH,
            side_models=others,
            note=f"pinned {requested}, but it does not appear in modelUsage",
        )

    return ModelAudit(
        requested=requested,
        served=served,
        verdict=AuditVerdict.MATCH,
        side_models=others,
    )


def _output_tokens(entry: object) -> int:
    return int(entry.get("outputTokens", 0)) if isinstance(entry, dict) else 0


_AUTH_MARKERS = ("not logged in", "please run /login", "authentication_error", "invalid api key",
                 "oauth token", "unauthorized", "please log in")
_QUOTA_MARKERS = ("usage limit", "quota", "insufficient credit", "out of credits")
_RATE_MARKERS = ("rate limit", "http 429", "status 429", "overloaded", "too many requests")
#: In a 429's own error text: an allowance used up until a reset, which waiting seconds
#: will not fix -- as opposed to a throttle, which a retry will.
_ALLOWANCE_MARKERS = ("session limit", "usage limit", "weekly limit", "limit reached",
                      "resets ")


def classify(exit_code: int | None, stdout: str, stderr: str, envelope: dict | None) -> Failure | None:
    """Map a finished call onto the failure taxonomy, or ``None`` if it succeeded."""
    # stderr only. stdout is the model's answer, and reading it for failure markers means
    # a reply that happens to say "overloaded" or to quote a number classifies a perfectly
    # good turn as a provider outage. See agy_adapter.classify for the case that found it.
    haystack = stderr.lower()

    if envelope and envelope.get("is_error"):
        subtype = str(envelope.get("subtype", "")).lower()
        if "limit" in subtype or "quota" in subtype:
            return Failure(FailureKind.QUOTA_EXHAUSTED, "claude", "usage limit reached", stderr[:2000])
        # **The session limit arrives as an HTTP status inside the envelope, not a subtype.**
        # Observed 2026-09-23, 90 times in a row from turn 4 of a live match:
        #   {"type": "result", "subtype": "success", "is_error": true,
        #    "api_error_status": 429, "num_turns": 1, "total_cost_usd": 0,
        #    "result": "You've hit your session limit · resets 4:10am (Asia/Yerevan)"}
        # with exit code 1. `subtype` says "success", so the branch above missed it and the
        # exit code filed it as CRASHED -- an ordinary failure, so the match played ten more
        # rounds with a silent bloc instead of pausing. Read only when the CLI itself says
        # the call errored with a status, so `result` is the CLI's error text and never a
        # model's answer (the reason stdout is otherwise off limits here).
        if envelope.get("api_error_status") == 429:
            said = str(envelope.get("result", ""))
            if any(marker in said.lower() for marker in _ALLOWANCE_MARKERS):
                return Failure(FailureKind.QUOTA_EXHAUSTED, "claude",
                               f"usage limit reached: {said[:160]}", stderr[:2000], exit_code)
            return Failure(FailureKind.RATE_LIMITED, "claude", f"rate limited: {said[:160]}",
                           stderr[:2000], exit_code)

    # Specific before generic, and the order is load-bearing. A throttle message that
    # also mentions authentication -- which providers emit routinely -- was being read
    # as a logged-out CLI: terminal, never retried, and it halted the role. The tell was
    # that four calls in the same turn on the same credentials succeeded and the fifth
    # was "not signed in". A real sign-out fails every call, not one in five.
    if any(marker in haystack for marker in _RATE_MARKERS):
        return Failure(FailureKind.RATE_LIMITED, "claude", "rate limited", stderr[:2000], exit_code)
    if any(marker in haystack for marker in _QUOTA_MARKERS):
        return Failure(FailureKind.QUOTA_EXHAUSTED, "claude", "usage limit reached", stderr[:2000], exit_code)
    if any(marker in haystack for marker in _AUTH_MARKERS):
        return Failure(FailureKind.NOT_AUTHENTICATED, "claude", "Claude Code is not signed in", stderr[:2000], exit_code)

    if exit_code != 0:
        return Failure(
            FailureKind.CRASHED, "claude", f"claude exited with code {exit_code}", stderr[:2000], exit_code
        )
    return None


PROVIDER = Provider.CLAUDE
