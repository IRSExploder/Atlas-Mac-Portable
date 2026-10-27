"""Trying a call again, once, in one place.

**One policy, because there were two call sites and only one of them had a policy.**
`TurnEngine._invoke` has retried retryable failures since the day `Failure.retryable` was
made to mean something. `EveEngine` called `provider.invoke` directly, so every bloc call
in every AI-vs-AI match got exactly one attempt -- and the failure kind that retrying
exists for, :attr:`~atlas.providers.errors.FailureKind.EMPTY_RESPONSE`, is the one agy
produces constantly. The Fulda Gap matches of 2026-09-20 lost 14 of 42 and 19 of 36 bloc
calls that way, which is a bloc playing most of a match two nations short.

The loop itself is four lines. It lives here rather than in either caller so that the next
place that calls a provider inherits the behaviour instead of rediscovering that it needs
it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .base import ModelProvider, ProviderRequest, ProviderResult

__all__ = ["RETRY_DELAYS", "invoke_with_retries"]

#: Backoff between retries, and therefore also how many: three attempts total.
#:
#: **Short on purpose, and the evidence is in the matches.** The worry on record was that
#: agy's empty answers come in a window that fifteen seconds will not clear, which would
#: make a retry a minute of waiting to be told the same thing. The call logs say otherwise:
#: the failures are scattered rather than clustered, and a call that returned nothing is
#: repeatedly followed *seconds later* by one that answers -- in the rotated match, three
#: of the twelve rounds have a failure and a success back to back in the same round. So the
#: next attempt is worth taking soon, and 3s is already unhurried by that standard. The
#: longer second delay is for a rate limit, which does need time and which a burst of
#: nine bloc calls is exactly the way to trip.
RETRY_DELAYS: tuple[float, ...] = (3.0, 12.0)


async def invoke_with_retries(
    provider: ModelProvider,
    request: ProviderRequest,
    *,
    delays: tuple[float, ...] | None = None,
    on_retry: Callable[[], None] | None = None,
) -> ProviderResult:
    """Call ``provider``, trying again for the failures that are worth trying again.

    ``Failure.retryable`` decides: a rate limit, a timeout, a malformed answer and an empty
    one are all worth another attempt; a logged-out CLI and a missing install are not, and
    retrying those wastes minutes and buries the message that would fix them.

    Returns the last attempt's result, whatever it is. **The caller logs one line per
    call, not per attempt** -- the turn a match got is the answer it ended with, and a
    routing log that grew a row per attempt would make every failure rate in every report
    depend on how many times the engine happened to try.
    """
    # Read at call time rather than bound as a default, so the one place that
    # neutralises backoff for the test suite can reach this too.
    budget = RETRY_DELAYS if delays is None else delays
    result = await provider.invoke(request)
    for delay in budget:
        if not result.failed or result.failure is None:
            return result
        if not result.failure.kind.retryable:
            return result
        if on_retry is not None:
            on_retry()
        await asyncio.sleep(delay)
        result = await provider.invoke(request)
    return result
