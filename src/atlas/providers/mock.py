"""A deterministic provider that never touches a CLI.

This is what makes the engine testable. A twenty-turn game runs against it with no
subprocess, no network, no quota and no wall-clock variance, so a test failure means the
engine is wrong rather than that a model had an off day.

**Its randomness is its own.** The mock holds a ``Random`` seeded independently of the
world. Drawing from the engine's stream instead would mean that adding or removing any
provider call anywhere shifts every subsequent battle result -- destroying exactly the
reproducibility the seeded-RNG design exists to provide. The engine's stream is reserved
for resolving outcomes.

Failures are first-class: the mock can be told to time out, return malformed output, hit
a quota wall or crash, so the degradation paths get tested without waiting for a real
provider to have a bad day.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..state.models import Provider, Role
from .base import AuditVerdict, ModelAudit, ProviderRequest, ProviderResult
from .errors import Failure, FailureKind

__all__ = ["MockProvider", "ScriptedResponse", "always", "cycle"]


@dataclass(frozen=True, slots=True)
class ScriptedResponse:
    """One canned outcome."""

    data: Any = None
    text: str = ""
    failure_kind: FailureKind | None = None
    delay_s: float = 0.0
    #: Emit output that does not satisfy the schema, to exercise the malformed path.
    malformed: bool = False
    served_model: str | None = None


def always(response: ScriptedResponse) -> Callable[[ProviderRequest], ScriptedResponse]:
    return lambda _request: response


def cycle(responses: Sequence[ScriptedResponse]) -> Callable[[ProviderRequest], ScriptedResponse]:
    """Return each response in turn, repeating the last once exhausted."""
    state = {"index": 0}

    def pick(_request: ProviderRequest) -> ScriptedResponse:
        index = min(state["index"], len(responses) - 1)
        state["index"] += 1
        return responses[index]

    return pick


@dataclass(slots=True)
class MockProvider:
    """A provider whose answers are decided by a script rather than a model."""

    name: Provider = Provider.MOCK
    script: Callable[[ProviderRequest], ScriptedResponse] | None = None
    mock_seed: int = 20260728
    #: Every request seen, for assertions about what the engine actually asked for --
    #: including whether a view leaked something it should not have.
    calls: list[ProviderRequest] = field(default_factory=list)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # **`name` is the first field and `script` the second, so `MockProvider(always(...))`
        # binds the script to `name` and leaves `script` unset** -- the mock then answers
        # every call with a schema-shaped default and reports the script function as its
        # provider. It fails silently and convincingly: six scratchpad harnesses had been
        # running scriptless without anybody noticing, and one of them produced a report I
        # read and believed. Cheap to catch, so it is caught.
        if not isinstance(self.name, Provider):
            raise TypeError(
                f"MockProvider(name=...) takes a Provider, not {type(self.name).__name__}. "
                "A script goes in the second field: MockProvider(script=always(...))."
            )
        self._rng = random.Random(self.mock_seed)

    def available(self) -> bool:
        return True

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        response = self.script(request) if self.script else self._default(request)

        if response.delay_s:
            await asyncio.sleep(response.delay_s)

        if response.failure_kind is not None:
            return ProviderResult(
                provider=self.name,
                role=request.role,
                ok=False,
                failure=Failure(
                    response.failure_kind,
                    self.name.value,
                    f"mock provider was scripted to fail with {response.failure_kind.value}",
                ),
                audit=ModelAudit(request.model, None, AuditVerdict.UNVERIFIED),
            )

        if response.malformed:
            return ProviderResult(
                provider=self.name,
                role=request.role,
                ok=False,
                text="not json at all",
                failure=Failure(
                    FailureKind.MALFORMED_OUTPUT,
                    self.name.value,
                    "the response did not parse as JSON matching the required schema",
                ),
                audit=ModelAudit(request.model, request.model, AuditVerdict.MATCH),
            )

        data = response.data
        text = response.text or (json.dumps(data) if data is not None else "")
        served = response.served_model or request.model
        return ProviderResult(
            provider=self.name,
            role=request.role,
            ok=True,
            text=text,
            data=data,
            audit=ModelAudit(
                requested=request.model,
                served=served,
                verdict=AuditVerdict.MATCH if served == request.model else AuditVerdict.MISMATCH,
            ),
            duration_s=response.delay_s,
        )

    def _default(self, request: ProviderRequest) -> ScriptedResponse:
        """An empty but schema-shaped answer, so an unscripted call is still valid."""
        if request.role is Role.PARSER:
            return ScriptedResponse(data={"actions": []})
        # The conversational roles take no schema, so a schema-shaped default would
        # reach the player as a blank reply. `--mock` exists to exercise the flow with
        # no quota, and a council that answers with nothing exercises nothing.
        if request.role is Role.ADVISOR:
            return ScriptedResponse(text=(
                "[mock council] I would hold the present course. Your treasury will "
                "carry one more season of rearmament, and moving before your allies "
                "commit would leave you carrying the war alone."
            ))
        if request.role is Role.DIPLOMACY:
            return ScriptedResponse(text="[mock reply] We will consider your proposal.")
        return ScriptedResponse(data={"narrative": "", "diffs": [], "events": []})
