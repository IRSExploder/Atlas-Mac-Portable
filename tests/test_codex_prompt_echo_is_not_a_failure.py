"""Codex echoes the prompt into stderr; the prompt's own words are not a failure.

Found 2026-09-24: a Situation Room seat's prompt contained "unauthorized leakage", Codex's
banner echoed the whole prompt to stderr, and `classify` -- which reads stderr for failure
markers on every call -- reported "Codex is not signed in" on every attempt of a call that
had succeeded. "insufficient" (a quota marker) and "authentication" are just as ordinary in a
prompt.
"""

from __future__ import annotations

from atlas.providers import codex_adapter
from atlas.providers.errors import FailureKind

PROMPT = ("Is unauthorized leakage of the material plausible? The evidence is insufficient;\n"
          "authentication of the intercepts, the rate limit of the analysts, and quota of cells.")


def banner(prompt: str, tail: str = "") -> str:
    return ("OpenAI Codex v0.144.5\n--------\nmodel: gpt-5.6-terra\n--------\nuser\n"
            + prompt + "\n" + tail)


def test_a_prompt_that_mentions_failure_words_is_not_a_failure():
    assert codex_adapter.classify(0, '{"ok": true}', banner(PROMPT), prompt=PROMPT) is None


def test_an_answer_that_mentions_failure_words_is_not_a_failure():
    # Codex also echoes the model's answer after the prompt; the answer is stdout.
    answer = '{"speech": "unauthorized theft is plausible; the evidence is insufficient"}'
    stderr = banner("Assess the evidence.", "codex\n" + answer + "\ntokens used\n1,234\n")
    assert codex_adapter.classify(0, answer, stderr, prompt="Assess the evidence.") is None


def test_windows_line_endings_in_the_echo_are_still_recognised():
    echoed = banner(PROMPT).replace("\n", "\r\n")
    assert codex_adapter.classify(0, '{"ok": true}', echoed, prompt=PROMPT) is None


def test_a_real_failure_after_the_echo_is_still_caught():
    stderr = banner(PROMPT, "ERROR: unauthorized -- please run `codex login`\n")
    failure = codex_adapter.classify(1, "", stderr, prompt=PROMPT)
    assert failure is not None and failure.kind is FailureKind.NOT_AUTHENTICATED


def test_a_real_quota_failure_is_still_caught():
    stderr = banner("Reply with one word.", "ERROR: usage limit reached\n")
    failure = codex_adapter.classify(1, "", stderr, prompt="Reply with one word.")
    assert failure is not None and failure.kind is FailureKind.QUOTA_EXHAUSTED


def test_either_defence_alone_reads_the_echo_as_an_echo_and_neither_is_the_old_misreading():
    """Two fixes for this bug landed separately and both are kept: the prompt is stripped
    from stderr (this file), and everything under a transcript header such as ``user`` is
    cut (bug scan, `_meaningful_stderr`). Each must hold on its own, so the regression this
    guards is shown on an echo that has neither -- which is misread exactly as before."""
    ok = '{"ok": true}'
    # The transcript header alone: no prompt passed, and the echo is still not a failure.
    assert codex_adapter.classify(0, ok, banner(PROMPT)) is None
    # The prompt alone: an echo with no header to cut it, stripped because it is the prompt.
    bare = "OpenAI Codex v0.144.5\n--------\n" + PROMPT + "\n"
    assert codex_adapter.classify(0, ok, bare, prompt=PROMPT) is None
    # Neither: the old misreading is what happens.
    assert codex_adapter.classify(0, ok, bare) is not None
