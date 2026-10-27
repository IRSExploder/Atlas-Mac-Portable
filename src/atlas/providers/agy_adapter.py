"""Antigravity, driven as ``agy --print``.

This adapter carries more hard-won detail than the other two, because agy's behaviour
contradicts both its own ``--help`` output and the assumptions the project started with.
All of the following was verified against agy 1.1.4:

**It ignores stdin.** A piped prompt is silently discarded. The prompt must be the value
of ``--print``. Worse, given no prompt it does not fail -- it answers with a canned
"I am currently running on <model>" greeting, which is a *plausible-looking* response
that would otherwise flow into the engine as if the model had spoken. That shape is
detected and reported as ``PROMPT_NOT_DELIVERED``.

**It ignores the working directory.** agy runs in its own scratch folder no matter what
``cwd`` the parent sets, so per-call temp-dir isolation -- the boundary that constrains
Claude and Codex -- does *nothing* here. What actually constrains agy is ``--add-dir``.
Diagnostics says so plainly rather than implying a guarantee that does not exist.

**Model names include their tier.** ``"Gemini 3.6 Flash"`` is rejected outright; the
accepted string is ``"Gemini 3.6 Flash (Low)"``. ``agy models`` prints the legal set.
``--effort`` is therefore never passed alongside a tiered name.

**Long prompts need a file.** Windows caps a command line at 32,767 characters and a
rendered world-state view exceeds that, so past a threshold the prompt is written into
the sandbox and agy is pointed at it with ``--add-dir``. Verified end to end.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config.paths import assert_grantable
from ..state.models import Provider
from .base import AuditVerdict, ModelAudit, ProviderRequest
from .errors import Failure, FailureKind

__all__ = [
    "CANNED_GREETING",
    "PROMPT_FILE",
    "build_argv",
    "classify",
    "envelope_of",
    "looks_like_canned_greeting",
    "looks_like_transport_envelope",
    "parse_models",
    "parse_result",
]

PROMPT_FILE = "prompt.md"

#: Beyond this many characters the prompt goes into a file. Kept well under the 32,767
#: command-line ceiling to leave room for flags, the model name and quoting overhead.
#: Both paths are exercised in normal play -- tracked-agent calls are short, world-sim
#: calls are not -- so neither branch is the one that only breaks in production.
_ARGV_PROMPT_LIMIT = 6000

#: What agy says when it received no prompt at all. Matching this is the difference
#: between an obvious transport bug and a nation mysteriously talking about itself.
CANNED_GREETING = re.compile(
    r"^\s*(?:I am|I'm)\s+(?:currently\s+)?running\s+on\s+\*{0,2}[\w.\s()-]+\*{0,2}\.?\s*$",
    re.IGNORECASE,
)


def looks_like_canned_greeting(text: str) -> bool:
    """True when the response is agy's no-prompt greeting rather than a real answer."""
    stripped = text.strip()
    if not stripped or len(stripped) > 200:
        return False
    return bool(CANNED_GREETING.match(stripped))


def build_argv(
    executable: str, request: ProviderRequest, sandbox: Path
) -> tuple[list[str], str | None]:
    """Return ``(argv, prompt_file_contents)``.

    The second element is non-``None`` when the prompt was too large for the command
    line and must be written into the sandbox before launching.
    """
    prompt = request.prompt
    file_contents: str | None = None

    if len(prompt) > _ARGV_PROMPT_LIMIT:
        file_contents = prompt
        prompt_path = sandbox / PROMPT_FILE
        # **Name the tool, and forbid the shell.** "Read the file at ..." let agy choose how
        # to read it, and it often chose a shell command -- which headless mode auto-denies,
        # after which agy exits reporting SUCCESS with an empty `response`. That was the
        # whole of Gemini's "reported success but returned no answer" in the EVE matches
        # (fulda-2 lost 14 of 42 bloc calls, Three Fronts 13 of 42): every failed attempt
        # kept by `--keep-failures` on 2026-09-23 had the same stderr, "a tool required the
        # 'command' permission that headless mode cannot prompt for". An interleaved A/B on
        # the same 40k-character briefing: old wording 5 of 10 denied, this wording 0 of 10.
        prompt = (
            f"Use your file-viewing tool to open the file at {prompt_path} -- do not run any "
            "shell or terminal command, which is not permitted here -- and carry out the "
            "instructions it contains exactly. Reply only with what those instructions ask "
            "for."
        )

    # agy ignores cwd, so the granted directory is the real boundary. Check it against
    # the same allowlist the sandbox itself passes.
    assert_grantable(sandbox)

    # print-timeout is a Go duration and defaults to 5m, which is too short for a heavy
    # turn. Set slightly under the wall deadline so agy self-terminates first and we get
    # its own error rather than a bare kill.
    print_timeout = max(30, int(request.timeout_s) - 20)

    argv = [
        executable,
        # Always explicit: a bare `agy` with no TTY waits forever for an approval prompt
        # that will never render.
        "--print", prompt,
        "--model", request.model,
        "--output-format", "json",
        "--print-timeout", f"{print_timeout}s",
        # Exists despite claims to the contrary. Its enforcement strength is unverified,
        # so it is defence in depth rather than a boundary we rely on.
        "--sandbox",
        "--add-dir", str(sandbox),
    ]

    if request.schema is not None:
        argv += ["--json-schema", json.dumps(request.schema, separators=(",", ":"))]
    return argv, file_contents


#: Keys that mark agy's own call-transport envelope rather than a model answer. A
#: schema-constrained answer is the caller's object and carries none of these.
_TRANSPORT_KEYS = frozenset(
    {"conversation_id", "status", "duration_seconds", "num_turns", "json_schema", "usage"}
)


def looks_like_transport_envelope(envelope: dict) -> bool:
    """True when this is agy reporting on the call rather than answering it.

    Two markers have to line up: a transport-only key, and the ``response`` field that
    would have carried the answer being present but empty. Requiring both means an
    answer that happens to contain a ``status`` field of its own is not mistaken for
    an envelope.
    """
    if not _TRANSPORT_KEYS & envelope.keys():
        return False
    return not str(envelope.get("response") or "").strip()


def parse_result(stdout: str) -> tuple[str, Any]:
    """Unwrap agy's output, which may be a JSON envelope or bare text."""
    stripped = stdout.strip()
    if not stripped:
        return "", None

    envelope: Any = None
    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        # Fall back to the last JSON object on its own line, which is how stream-json
        # output ends up looking.
        for line in reversed(stripped.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    envelope = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

    if isinstance(envelope, dict):
        # When a schema was enforced, agy returns the parsed object directly. Preferring
        # it avoids re-parsing the same JSON out of the `response` string.
        structured = envelope.get("structured_output")
        if isinstance(structured, (dict, list)):
            return json.dumps(structured), structured

        for key in ("response", "text", "output", "result", "content", "message"):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    return value.strip(), json.loads(value)
                except json.JSONDecodeError:
                    return value.strip(), None

        # Nothing usable in any answer field. If this is agy's transport envelope, the
        # model returned nothing -- and handing the envelope back as though it were the
        # answer is how an empty response became a successful empty batch. The envelope
        # even echoes the request's `json_schema`, so it looks structured while carrying
        # no answer at all. Report "no data" and let the caller classify it.
        if looks_like_transport_envelope(envelope):
            return "", None

        # Otherwise the envelope may genuinely *be* the structured answer, which is how
        # agy returns a schema-constrained result.
        return stripped, envelope

    try:
        return stripped, json.loads(stripped)
    except json.JSONDecodeError:
        return stripped, None


def envelope_of(stdout: str) -> dict | None:
    """The transport envelope, for readers that want its metadata rather than its answer.

    :func:`parse_result` deliberately throws the envelope away -- its job is to find the
    model's answer, and returning the wrapper instead is the bug that turned an empty
    response into a successful empty batch. But the wrapper carries the ``usage`` block,
    which is the only place agy reports what a call cost, so token accounting needs a
    way to reach it without going back through the answer path.
    """
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
        for line in reversed(stripped.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return parsed if isinstance(parsed, dict) else None


def parse_models(output: str) -> tuple[str, ...]:
    """Read the legal ``--model`` strings from ``agy models``.

    **Two naming schemes exist and both are accepted by the CLI.** agy 1.1.4 listed
    display names with a parenthetical tier (``"Gemini 3.6 Flash (High)"``); 1.1.8 lists
    slugs (``gemini-3.6-flash-high``). Verified: 1.1.8 still accepts the old form, so the
    change is presentational -- but anything parsing the listing has to handle both, and
    anything *pinning* a model should use whatever the installed CLI reports, which is why
    pins are validated against this rather than trusted.

    In both schemes the tier is part of the name, so ``--effort`` is never passed
    alongside it.
    """
    models: list[str] = []
    for line in output.splitlines():
        name = line.strip()
        if not name:
            continue
        # **1.1.11 lists `slug<TAB>Display Name` and this took the whole line as one
        # name.** `normalise_model` then turned `gemini-3.6-flash-high` into
        # `gemini-3.6-flash-high-gemini-3.6-flash-high`, so `validate_pins` matched
        # nothing and `atlas doctor` reported both Antigravity pins as models the CLI
        # does not offer -- while the CLI answered on them perfectly. A third naming
        # scheme in three releases, which is exactly why this is parsed rather than
        # hardcoded. The slug is the part the CLI accepts, so the slug is what is kept.
        if "	" in name:
            name = name.split("	", 1)[0].strip()
            if not name:
                continue
        lowered = name.lower()
        if lowered.startswith(("usage", "available", "error", "unknown", "no ")):
            continue
        # Display form: "Gemini 3.6 Flash (High)"
        if name.endswith(")") and "(" in name:
            models.append(name)
            continue
        # Slug form: gemini-3.6-flash-high
        if "-" in name and " " not in name and not name.startswith("-"):
            models.append(name)
    return tuple(models)


def normalise_model(name: str) -> str:
    """Reduce either naming scheme to a comparable key.

    ``"Gemini 3.6 Flash (High)"`` and ``gemini-3.6-flash-high`` denote the same model, so
    validating a pin against the CLI's listing must not depend on which scheme each side
    happens to use.
    """
    lowered = name.strip().lower().replace("(", " ").replace(")", " ")
    return re.sub(r"[^a-z0-9.]+", "-", lowered).strip("-")


_AUTH_MARKERS = ("not logged in", "sign in", "unauthorized", "authentication", "401",
                 "please log in", "no credentials")
_QUOTA_MARKERS = ("quota", "usage limit", "resource exhausted", "exhausted")
_RATE_MARKERS = ("rate limit", "http 429", "status 429", "too many requests", "resource_exhausted")


def envelope_error(stdout: str, stderr: str) -> str:
    """Recover agy's own error text from its JSON envelope.

    A failed call exits 1 and prints ``{"status":"ERROR","error":"..."}``. Reporting
    "agy exited with code 1" throws that away and leaves the player with nothing to act
    on, so the envelope is read first and its message used instead.
    """
    for blob in (stdout, stderr):
        for line in blob.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and str(parsed.get("status", "")).upper() == "ERROR":
                return str(parsed.get("error") or "").strip()
    return ""


def classify(
    exit_code: int | None, stdout: str, stderr: str, *, text: str = ""
) -> Failure | None:
    # Diagnosis reads the CLI's own reporting, never the model's answer. stdout carries
    # the response: a valid batch whose narrative said a levy arrived "exhausted" was read
    # as a quota wall and aborted the turn, and a reply mentioning 429 men tripped the
    # rate-limit branch. The failure is a property of the call, not of the prose it
    # returned. A non-zero exit still reaches the generic handler further down.
    haystack = stderr.lower()

    if "not recognized as a known model" in haystack or "invalid model selection" in haystack:
        return Failure(
            FailureKind.MODEL_MISMATCH,
            "antigravity",
            "the pinned model was rejected; run `agy models` to see the accepted names "
            "(the tier suffix, e.g. ' (Low)', is part of the name)",
            stderr[:2000],
            exit_code,
        )

    if text and looks_like_canned_greeting(text):
        return Failure(
            FailureKind.PROMPT_NOT_DELIVERED,
            "antigravity",
            "agy answered with its no-prompt greeting, so the prompt never reached the model",
            text[:500],
            exit_code,
        )

    # Specific before generic, and the order is load-bearing. A throttle message that
    # also mentions authentication -- which providers emit routinely -- was being read
    # as a logged-out CLI: terminal, never retried, and it halted the role. The tell was
    # that four calls in the same turn on the same credentials succeeded and the fifth
    # was "not signed in". A real sign-out fails every call, not one in five.
    if any(marker in haystack for marker in _RATE_MARKERS):
        return Failure(FailureKind.RATE_LIMITED, "antigravity", "rate limited", stderr[:2000], exit_code)
    if any(marker in haystack for marker in _QUOTA_MARKERS):
        return Failure(FailureKind.QUOTA_EXHAUSTED, "antigravity", "quota exhausted", stderr[:2000], exit_code)
    if any(marker in haystack for marker in _AUTH_MARKERS):
        return Failure(FailureKind.NOT_AUTHENTICATED, "antigravity", "agy is not signed in", stderr[:2000], exit_code)
    if exit_code != 0:
        reported = envelope_error(stdout, stderr)
        # agy's generic failure is indistinguishable from a quota wall by its text alone,
        # and it exits within a second or two either way. Say what it said rather than
        # inventing a diagnosis, and point at the one place that can settle it.
        message = (
            f"agy reported: {reported}" if reported else f"agy exited with code {exit_code}"
        )
        if reported and "terminated due to error" in reported.lower():
            message += (
                ". This is agy's generic failure and most often means the shared "
                "Antigravity quota is exhausted -- check `/usage` in the Antigravity app, "
                "since the CLI cannot see its own remaining quota."
            )
        return Failure(
            FailureKind.CRASHED, "antigravity", message, (stderr or stdout)[:2000], exit_code
        )
    return None


def audit(requested: str, envelope_text: str) -> ModelAudit:
    """agy provides no reliable served-model attestation.

    Its JSON envelope does not name the model, and asking the model itself is worthless:
    the canned greeting proves it will state a tier that was not requested. Reporting
    ``UNVERIFIED`` honestly is better than inventing confidence -- EVE treats that as a
    refusal precisely because the mode's premise depends on knowing who is playing.
    """
    return ModelAudit(
        requested=requested,
        served=None,
        verdict=AuditVerdict.UNVERIFIED,
        note="agy does not attest which model served the call",
    )


PROVIDER = Provider.ANTIGRAVITY
