"""How provider calls fail, and what the player should do about each one.

Every failure is classified, because the right response differs completely between them:
a timeout is worth retrying, a logged-out CLI never is, and a quota exhaustion should
stop the turn rather than hammer the provider. The taxonomy exists so the turn engine can
decide without pattern-matching on error strings, and so the UI can show an instruction
rather than a stack trace.

**Nothing here ever falls back to an API path.** If a subscription-authenticated CLI is
logged out, the correct behaviour is to say so and stop. Silently switching to an API key
would turn a flat-rate call into a metered one, which defeats the entire point of driving
these tools as subprocesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["ProviderError", "FailureKind", "Failure"]


class FailureKind(StrEnum):
    NOT_INSTALLED = "not_installed"
    NOT_AUTHENTICATED = "not_authenticated"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    #: The CLI reported success and returned nothing usable: an empty answer, or an
    #: object with none of the schema's required keys. agy does exactly this -- exit 0,
    #: ``"status":"SUCCESS"``, ``"response":""`` -- and without this kind it was read as a
    #: successful *empty* batch. Every tracked nation "acted" and changed nothing, for
    #: every turn of a game, with no error anywhere. A call that produced no answer is a
    #: failed call, whatever the exit code says.
    EMPTY_RESPONSE = "empty_response"
    #: The prompt never reached the model. Specific to agy, which answers a missing
    #: prompt with a canned greeting instead of failing -- see the adapter.
    PROMPT_NOT_DELIVERED = "prompt_not_delivered"
    MODEL_MISMATCH = "model_mismatch"
    CRASHED = "crashed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        """Whether trying the same call again could plausibly work.

        Authentication and installation problems need a human. Retrying them just wastes
        time and hides the real message.
        """
        return self in {
            FailureKind.TIMEOUT,
            FailureKind.RATE_LIMITED,
            FailureKind.MALFORMED_OUTPUT,
            # Empty answers are intermittent rather than deterministic: the same call
            # that returns nothing will often answer on the next attempt, so this is
            # exactly the case retrying exists for.
            FailureKind.EMPTY_RESPONSE,
            FailureKind.CRASHED,
        }

    @property
    def blocks_turn(self) -> bool:
        """Whether the turn should halt rather than continue with a degraded result."""
        return self in {
            FailureKind.NOT_INSTALLED,
            FailureKind.NOT_AUTHENTICATED,
            FailureKind.QUOTA_EXHAUSTED,
            FailureKind.MODEL_MISMATCH,
        }


#: What the player should actually do, per failure. Shown verbatim in the UI, so these
#: are written as instructions rather than descriptions.
REMEDIES: dict[tuple[FailureKind, str], str] = {
    (FailureKind.NOT_AUTHENTICATED, "claude"): "Run `claude` in a terminal and sign in, then retry.",
    (FailureKind.NOT_AUTHENTICATED, "codex"): "Run `codex login` in a terminal, then retry.",
    (FailureKind.NOT_AUTHENTICATED, "antigravity"): "Run `agy` in a terminal and sign in, then retry.",
    (FailureKind.NOT_INSTALLED, "claude"): "Install Claude Code: npm i -g @anthropic-ai/claude-code",
    (FailureKind.NOT_INSTALLED, "codex"): "Install Codex: npm i -g @openai/codex",
    (FailureKind.NOT_INSTALLED, "antigravity"): "Install the Antigravity CLI and run `agy install`.",
}

_GENERIC_REMEDIES: dict[FailureKind, str] = {
    FailureKind.RATE_LIMITED: "The provider is rate limiting. Wait a few minutes and retry.",
    FailureKind.QUOTA_EXHAUSTED: "This provider's quota is used up. Switch roles to another provider in Settings, or wait for the quota to reset.",
    FailureKind.TIMEOUT: "The call exceeded its time budget. Retry, or lower the jump length.",
    FailureKind.MALFORMED_OUTPUT: "The model returned output that did not match the required schema.",
    FailureKind.EMPTY_RESPONSE: "The provider reported success but returned no answer. This is usually the pinned model failing to produce structured output; check the model pin for this role in Diagnostics.",
    FailureKind.PROMPT_NOT_DELIVERED: "The prompt did not reach the model. This is a transport bug; see Diagnostics.",
    FailureKind.MODEL_MISMATCH: "The provider served a different model than the one pinned. Check Diagnostics before continuing.",
    FailureKind.CRASHED: "The CLI exited unexpectedly.",
    FailureKind.CANCELLED: "Cancelled.",
    FailureKind.UNKNOWN: "The call failed for an unrecognised reason. See Diagnostics for the raw output.",
}


def remedy_for(kind: FailureKind, provider: str) -> str:
    return REMEDIES.get((kind, provider)) or _GENERIC_REMEDIES.get(kind, "")


@dataclass(frozen=True, slots=True)
class Failure:
    kind: FailureKind
    provider: str
    message: str
    #: Trimmed CLI output, for Diagnostics. Never shown in the main UI.
    detail: str = ""
    exit_code: int | None = None

    @property
    def remedy(self) -> str:
        return remedy_for(self.kind, self.provider)

    def describe(self) -> str:
        parts = [f"[{self.provider}] {self.kind.value}: {self.message}"]
        if self.remedy:
            parts.append(f"  -> {self.remedy}")
        return "\n".join(parts)


class ProviderError(RuntimeError):
    """Raised only when a caller asked for a result and cannot be given one."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.describe())
        self.failure = failure
