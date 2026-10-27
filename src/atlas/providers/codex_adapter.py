"""Codex, driven as ``codex exec``.

Verified against codex-cli 0.144.5:

* ``-p`` means ``--profile``, **not** print. The non-interactive entry point is the
  ``exec`` subcommand.
* With no positional prompt, ``exec`` reads stdin -- but it blocks forever on
  "Reading additional input from stdin..." unless the pipe is *closed*. Feeding without
  closing is the classic hang.
* ``-s read-only`` is a genuine CLI-enforced sandbox, the strongest of the three.
* ``--output-schema`` takes a **file path**, unlike Claude's inline string.
* Default reasoning effort is ``xhigh``, which is wildly disproportionate for a bounded
  parsing task; it is pinned down explicitly.
* ``-o`` writes the final message to a file, which is far more robust than scraping the
  human-readable transcript off stdout.

There is a benign pre-existing warning on this machine about a stale models cache. It is
allowlisted so it never reads as a failure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..state.models import Provider
from .base import AuditVerdict, ModelAudit, ProviderRequest
from .errors import Failure, FailureKind

__all__ = ["BENIGN_STDERR", "audit_from_output", "build_argv", "classify", "parse_result"]

#: Known-harmless stderr lines. Present on a healthy install, so treating them as errors
#: would make every call look broken.
BENIGN_STDERR: tuple[str, ...] = (
    "failed to load models cache",
    "supports_reasoning_summaries",
)

LAST_MESSAGE_FILE = "codex_last_message.txt"
SCHEMA_FILE = "codex_schema.json"


def build_argv(executable: str, request: ProviderRequest, sandbox: Path) -> list[str]:
    """Build the argv. The prompt is omitted so ``exec`` takes it from stdin."""
    effort = request.options.get("reasoning_effort", "low")
    argv = [
        executable,
        "exec",
        "--model", request.model,
        # The one provider with a real, CLI-enforced read-only sandbox.
        "--sandbox", "read-only",
        "--cd", str(sandbox),
        # The sandbox is not a git repository, and Codex refuses to run outside one
        # unless told this is fine.
        "--skip-git-repo-check",
        # Leave nothing behind: no session files, no inherited user config.
        "--ephemeral",
        "--ignore-user-config",
        "--color", "never",
        "-c", f"model_reasoning_effort={effort}",
        "-o", str(sandbox / LAST_MESSAGE_FILE),
    ]
    # Deliberately NOT --json. Verified against 0.144.5: the JSONL stream emits only
    # thread.started / turn.started / item.completed / turn.completed, none of which name
    # the model -- so --json yields no attestation at all. The human-readable banner does
    # print `model: <name>`, and since the payload comes from the -o file either way,
    # leaving stdout human-readable is what makes the model pin checkable.

    if request.schema is not None:
        schema_path = sandbox / SCHEMA_FILE
        schema_path.write_text(json.dumps(request.schema), encoding="utf-8")
        argv += ["--output-schema", str(schema_path)]
    return argv


def parse_result(sandbox: Path, stdout: str) -> tuple[str, Any]:
    """Prefer the ``-o`` file; fall back to the last assistant event on stdout."""
    text = ""
    message_file = sandbox / LAST_MESSAGE_FILE
    if message_file.is_file():
        try:
            text = message_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""

    if not text:
        text = _last_agent_message(stdout)

    data: Any = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    return text, data


def _iter_events(stdout: str):
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _last_agent_message(stdout: str) -> str:
    latest = ""
    for event in _iter_events(stdout):
        payload = event.get("msg") if isinstance(event.get("msg"), dict) else event
        if not isinstance(payload, dict):
            continue
        if payload.get("type") in {"agent_message", "assistant_message"}:
            message = payload.get("message") or payload.get("text") or ""
            if isinstance(message, str) and message.strip():
                latest = message.strip()
    return latest


_BANNER_MODEL = re.compile(r"^\s*model:\s*(\S+)\s*$", re.MULTILINE)


def audit_from_output(requested: str, stdout: str, stderr: str = "") -> ModelAudit:
    """Recover the served model from the session banner Codex prints before it works.

    Two details, both verified against 0.144.5 and both easy to get wrong. The banner is
    the *only* place Codex names the model in non-interactive mode -- the JSONL event
    stream carries just thread/turn/item events and no model at all. And the banner goes
    to **stderr**, while stdout holds only the answer, so an audit reading stdout finds
    nothing and reports a healthy call as unverified.

    Parsing a human-readable line is inelegant, but an unverifiable model pin is worse:
    EVE's premise is knowing which three judgment sources are actually playing.
    """
    served: str | None = None

    # The banner is the reliable source; JSONL events are checked too in case a future
    # version starts including the model there.
    match = _BANNER_MODEL.search(stderr) or _BANNER_MODEL.search(stdout)
    if match:
        served = match.group(1)
    else:
        for event in _iter_events(stdout):
            payload = event.get("msg") if isinstance(event.get("msg"), dict) else event
            if not isinstance(payload, dict):
                continue
            for key in ("model", "model_slug"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    served = value
                    break

    if served is None:
        return ModelAudit(requested, None, AuditVerdict.UNVERIFIED, note="codex did not report a model")
    matched = served == requested or served.startswith(requested) or requested.startswith(served)
    return ModelAudit(
        requested=requested,
        served=served,
        verdict=AuditVerdict.MATCH if matched else AuditVerdict.MISMATCH,
        note="" if matched else f"pinned {requested}, served {served}",
    )


_AUTH_MARKERS = ("not logged in", "please run `codex login`", "codex login", "unauthorized",
                 "http 401", "status 401", "authentication", "no credentials")
_QUOTA_MARKERS = ("quota", "usage limit", "insufficient", "billing")
_RATE_MARKERS = ("rate limit", "http 429", "status 429", "too many requests")


#: Section headers of the session transcript Codex prints to stderr. Everything under one
#: of these is the conversation -- the prompt, the model's reasoning, the answer, commands
#: the model ran and their output -- and none of it is Codex reporting on the call.
_TRANSCRIPT_HEADERS = frozenset({
    "user", "thinking", "codex", "exec", "tool", "mcp", "apply_patch", "file update",
    "turn diff", "plan update", "web search",
})
#: A header, optionally behind the ``[timestamp]`` older versions put on every line.
_HEADER = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?P<name>[a-z_ ]+?)\s*$")
#: Lines Codex writes about the call itself: its own ``ERROR:``, a tracing ``ERROR``/``WARN``
#: record, an ``Error:`` from the process exiting, a stream error. Any of these ends a
#: transcript section, so a failure printed right after an answer is still read.
_REPORT = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)?(?:\d{4}-\d\d-\d\dT\S+\s+)?"
    r"(?:ERROR\b|WARN\b|Error:|error:|stream error\b|tokens used\b)"
)


def _meaningful_stderr(stderr: str, text: str = "") -> str:
    """Codex's own reporting on stderr, with the transcript and the known-benign cut out.

    Codex prints the whole session to stderr -- ``user`` and the prompt, ``codex`` and the
    model's answer (see the capture in tests/test_usage.py). Searching that for failure
    words classified a valid batch whose narrative said "insufficient" or "an import
    quota" as a quota wall: terminal, never retried, and the turn was thrown away.
    """
    answer_lines = {line.strip() for line in text.splitlines() if line.strip()}
    kept: list[str] = []
    in_transcript = False
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header = _HEADER.match(line)
        if header and header.group("name") in _TRANSCRIPT_HEADERS:
            in_transcript = True
            continue
        if _REPORT.match(line):
            in_transcript = False
        if in_transcript or stripped in answer_lines:
            continue
        if any(benign in line.lower() for benign in BENIGN_STDERR):
            continue
        kept.append(line)
    return "\n".join(kept)


def classify(
    exit_code: int | None, stdout: str, stderr: str, prompt: str = "", *, text: str = ""
) -> Failure | None:
    """Map a finished call onto the failure taxonomy, or ``None`` if it succeeded.

    ``text`` is the answer Codex returned (the ``-o`` file). A clean exit that produced one
    is a success whatever words appear around it: a throttle Codex retried through, or a
    stray "quota" anywhere, does not undo an answer that arrived.

    **Codex echoes the conversation into stderr** -- the user's prompt in its banner, and the
    model's answer after it -- so a prompt or an answer that merely *mentions*
    "unauthorized", "insufficient" or "quota" was read as a logged-out or exhausted CLI, on
    successful calls too, since this runs on every call. Found 2026-09-24: a Situation Room
    seat asked whether "unauthorized leakage" of North Korean material was plausible, the
    answer spoke of "unauthorized theft", and every attempt came back "Codex is not signed
    in". Both echoes are removed before any marker is read, and what is left is cut down to
    Codex's own reporting by the transcript headers.
    """
    if exit_code == 0 and text.strip():
        return None

    def _plain(value: str) -> str:
        return value.replace("\r\n", "\n")

    stderr = _plain(stderr)
    for echoed in (prompt, stdout):
        if echoed and echoed.strip():
            stderr = stderr.replace(_plain(echoed).strip(), "")
    # stderr only, cut down to Codex's own reporting. stdout is the model's answer, and
    # the transcript on stderr repeats it; searching either for failure markers reads the
    # story back as a diagnosis.
    filtered = _meaningful_stderr(stderr, text)
    haystack = filtered.lower()

    # Specific before generic, and the order is load-bearing. A throttle message that
    # also mentions authentication -- which providers emit routinely -- was being read
    # as a logged-out CLI: terminal, never retried, and it halted the role. The tell was
    # that four calls in the same turn on the same credentials succeeded and the fifth
    # was "not signed in". A real sign-out fails every call, not one in five.
    if any(marker in haystack for marker in _RATE_MARKERS):
        return Failure(FailureKind.RATE_LIMITED, "codex", "rate limited", filtered[:2000], exit_code)
    if any(marker in haystack for marker in _QUOTA_MARKERS):
        return Failure(FailureKind.QUOTA_EXHAUSTED, "codex", "quota exhausted", filtered[:2000], exit_code)
    if any(marker in haystack for marker in _AUTH_MARKERS):
        return Failure(FailureKind.NOT_AUTHENTICATED, "codex", "Codex is not signed in", filtered[:2000], exit_code)
    if exit_code != 0:
        return Failure(
            FailureKind.CRASHED, "codex", f"codex exited with code {exit_code}", filtered[:2000], exit_code
        )
    return None


PROVIDER = Provider.CODEX
