"""What a call actually cost, in tokens, as reported by the CLI that made it.

ATLAS runs on subscription CLIs, so the marginal price of a turn is zero and nothing in
the engine has ever needed this. The question it answers is a different one: **what would
this game cost on metered APIs?** That cannot be estimated from prompt sizes, because on
every current provider the output side is 5x the input price and reasoning tokens are
billed as output while being invisible in the response.

**The three CLIs report three different amounts, and the differences are the whole
reason this module is not one regex.**

===========  ===========================================================================
provider     what it reports
===========  ===========================================================================
claude       A full split in the JSON envelope's ``usage`` -- input, output, cache read
             and cache creation -- plus a per-model ``modelUsage`` breakdown carrying
             ``costUSD``, and a ``total_cost_usd`` for the whole call. Thinking is *not*
             broken out; Anthropic bills it inside ``output_tokens``, so that is where it
             is here too.
agy          Input, output, ``thinking_tokens`` and cache reads. The only one of the
             three that separates reasoning from answer, which makes it the only place
             the reasoning share can be measured rather than inferred.
codex        **One number.** ``tokens used  12,394`` on stderr, no split at all. Every
             field except the total is ``None``, and that is recorded rather than
             guessed -- see :func:`split_codex_total` for the calibration that recovers
             an estimate afterwards, and note that it *is* an estimate.
===========  ===========================================================================

**A warning that matters more than the numbers.** All three CLIs wrap the prompt in their
own scaffolding, and it is not small: a fifteen-token prompt measured 9,246 billable input
tokens through Claude Code, 27,575 through agy and 12,394 total through Codex. A cost model
built from ATLAS's own rendered prompt sizes would understate a real API bill several times
over on short calls. What this module records is what the *provider* counted, which is what
an API would have charged for.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TokenUsage",
    "clear_recorders",
    "record",
    "recorder",
    "split_codex_total",
    "usage_from_agy",
    "usage_from_claude",
    "usage_from_codex",
]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """One call's token bill. ``None`` means *the CLI did not say*, never zero.

    The distinction is load-bearing: a zero that is really an absence turns into a
    confidently wrong cost model, and the point of this whole exercise is a number Alex
    can trust. :attr:`missing` names every field the provider withheld.
    """

    provider: str = ""
    model: str = ""
    role: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Reasoning/thinking. Only agy reports it. Anthropic and OpenAI both fold it into
    #: their output count and bill it at the output rate, so a ``None`` here does not
    #: mean "no reasoning happened" -- it means it is already inside ``output_tokens``.
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    #: What the CLI itself said the call cost, where it says so. Claude does; the others
    #: do not. Useful as a cross-check against a modelled price.
    reported_cost_usd: float | None = None
    #: Per-model detail, where the CLI distinguishes a side-model from the pinned one.
    per_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @property
    def billable_input(self) -> int | None:
        """Fresh input plus cache traffic, which is what an API meters on the way in."""
        parts = [self.input_tokens, self.cache_read_tokens, self.cache_write_tokens]
        known = [p for p in parts if p is not None]
        return sum(known) if known else None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def usage_from_claude(envelope: dict | None) -> TokenUsage:
    """Read Claude Code's JSON envelope.

    ``usage`` is the whole call; ``modelUsage`` splits it per model, which matters because
    Claude Code runs a Haiku side-task on healthy calls (the audit path already knows
    this). Both are kept: the total is what an API bill would be, the split is how much of
    it was the model that was actually pinned.
    """
    if not envelope:
        return TokenUsage(provider="claude", missing=("everything: no JSON envelope",))

    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage(provider="claude", missing=("everything: no usage block",))

    per_model: dict[str, dict[str, Any]] = {}
    raw_models = envelope.get("modelUsage")
    if isinstance(raw_models, dict):
        for name, entry in raw_models.items():
            if isinstance(entry, dict):
                per_model[str(name)] = dict(entry)

    return TokenUsage(
        provider="claude",
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        # Not a gap in the reporting: Anthropic bills thinking as output, so it is
        # already counted above and a separate figure would double it.
        reasoning_tokens=None,
        cache_read_tokens=_int_or_none(usage.get("cache_read_input_tokens")),
        cache_write_tokens=_int_or_none(usage.get("cache_creation_input_tokens")),
        reported_cost_usd=(
            float(envelope["total_cost_usd"])
            if isinstance(envelope.get("total_cost_usd"), (int, float)) else None
        ),
        per_model=per_model,
        missing=("reasoning_tokens (billed inside output_tokens)",),
    )


def usage_from_agy(envelope: dict | None) -> TokenUsage:
    """Read agy's transport envelope. The only CLI of the three that separates thinking."""
    if not envelope:
        return TokenUsage(provider="antigravity", missing=("everything: no JSON envelope",))
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage(provider="antigravity", missing=("everything: no usage block",))

    return TokenUsage(
        provider="antigravity",
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        reasoning_tokens=_int_or_none(usage.get("thinking_tokens")),
        cache_read_tokens=_int_or_none(usage.get("cache_read_tokens")),
        total_tokens=_int_or_none(usage.get("total_tokens")),
        missing=("cache_write_tokens", "cost"),
    )


#: ``tokens used`` followed by a grouped integer, on stderr, after the banner.
_CODEX_TOKENS = re.compile(r"tokens\s+used\s*[\r\n]+\s*([\d,]+)", re.IGNORECASE)


def usage_from_codex(stderr: str, stdout: str = "") -> TokenUsage:
    """Recover Codex's single total. There is no split to recover.

    Codex prints ``tokens used`` and one grouped number, and nothing else -- the JSONL
    event stream carries thread/turn/item events and no usage at all, which is the same
    reason the model audit has to parse the human-readable banner. So input, output and
    reasoning are all ``None`` here and are estimated afterwards by
    :func:`split_codex_total` from calls whose prompt size is known.
    """
    match = _CODEX_TOKENS.search(stderr) or _CODEX_TOKENS.search(stdout)
    if match is None:
        return TokenUsage(provider="codex", missing=("everything: no token line on stderr",))
    return TokenUsage(
        provider="codex",
        total_tokens=int(match.group(1).replace(",", "")),
        missing=("input_tokens", "output_tokens", "reasoning_tokens", "cache", "cost"),
    )


def split_codex_total(
    samples: list[tuple[int, int]]
) -> tuple[float, float, float]:
    """Fit ``total = overhead + rate * prompt_chars + mean_output`` over real calls.

    Returns ``(overhead_plus_output, tokens_per_char, r_squared)``. Codex gives one
    number, so the only honest way to split it is to watch how it moves against a prompt
    size we *do* know: the slope is what a character of our prompt costs in tokens, and
    the intercept is everything that does not scale with it -- Codex's own system prompt
    plus the reply and its reasoning.

    Ordinary least squares on two columns, written out rather than pulled in, because a
    numpy dependency for six lines of arithmetic is not a trade worth making. Returns
    zeros for fewer than three samples, where a fit would be decoration.
    """
    if len(samples) < 3:
        return 0.0, 0.0, 0.0
    n = float(len(samples))
    xs = [float(x) for x, _ in samples]
    ys = [float(y) for _, y in samples]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return mean_y, 0.0, 0.0
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    syy = sum((y - mean_y) ** 2 for y in ys)
    predicted = [intercept + slope * x for x in xs]
    residual = sum((y - p) ** 2 for y, p in zip(ys, predicted, strict=True))
    r2 = 1.0 - residual / syy if syy > 0 else 0.0
    return intercept, slope, r2


# ------------------------------------------------------------------ the recording hook


#: Installed sinks. Empty in every normal run, so this costs one truthiness test per call.
#:
#: **The hook lives at the provider layer on purpose.** Two engine paths bypass the turn
#: report entirely -- `digest.consolidate` and `diplomacy` both call `provider.invoke`
#: directly rather than going through `TurnEngine._invoke`, so `report.calls` does not see
#: them. Anything counting calls above this line inherits that blind spot; anything
#: counting here cannot.
_RECORDERS: list[Callable[[TokenUsage], None]] = []


def recorder(sink: Callable[[TokenUsage], None]) -> Callable[[], None]:
    """Install a sink and hand back the function that removes it again."""
    _RECORDERS.append(sink)

    def remove() -> None:
        if sink in _RECORDERS:
            _RECORDERS.remove(sink)

    return remove


def clear_recorders() -> None:
    _RECORDERS.clear()


def record(usage: TokenUsage) -> None:
    """Offer a measurement to every installed sink. Never raises into the caller."""
    for sink in list(_RECORDERS):
        try:
            sink(usage)
        except Exception:  # noqa: BLE001 - measurement must never cost a turn
            continue
