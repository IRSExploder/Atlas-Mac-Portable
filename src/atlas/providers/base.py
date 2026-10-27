"""The provider contract.

Everything above this line -- the turn engine, diplomacy, Focus Mode -- talks to a
``ModelProvider`` and never to a CLI. That seam is what lets a full twenty-turn game run
against a scripted mock with no subprocess, no network and no quota, which is the only
way the engine can be tested at all.

Provider-specific knowledge stops here. The engine never learns that agy needs its prompt
in a file, that codex hangs on an open stdin, or that Claude reports a second model in its
usage block. Those are adapter concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..state.models import Provider, Role
from .errors import Failure

__all__ = ["AuditVerdict", "ModelAudit", "ModelProvider", "ProviderRequest", "ProviderResult"]


class AuditVerdict(StrEnum):
    """Whether the model that answered is the model that was asked for."""

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    #: The CLI gave no usable attestation. Treated as a refusal in EVE, where the whole
    #: premise is that three *different* judgment sources are playing.
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class ModelAudit:
    requested: str
    served: str | None
    verdict: AuditVerdict
    #: Models the CLI used for incidental work alongside the primary one. Claude reports
    #: a Haiku side-task in ``modelUsage``, so an audit asserting exclusivity would fail
    #: on every healthy call.
    side_models: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """One stateless call. Carries everything the adapter needs and nothing more."""

    role: Role
    prompt: str
    model: str
    #: JSON Schema the response must satisfy. All three CLIs enforce this natively.
    schema: dict[str, Any] | None = None
    system_prompt: str | None = None
    timeout_s: float = 300.0
    #: Free-form per-adapter knobs (reasoning effort, print timeout). Deliberately opaque
    #: to the engine.
    options: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """The outcome of one call. A failure is a value, not an exception."""

    provider: Provider
    role: Role
    ok: bool
    text: str = ""
    #: Parsed object when a schema was supplied and the response validated.
    data: Any = None
    audit: ModelAudit | None = None
    failure: Failure | None = None
    duration_s: float = 0.0
    #: Raw stdout/stderr, kept for Diagnostics only.
    raw_stdout: str = ""
    raw_stderr: str = ""
    argv: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return not self.ok


@runtime_checkable
class ModelProvider(Protocol):
    """What the engine may assume about any provider, real or mocked."""

    name: Provider

    async def invoke(self, request: ProviderRequest) -> ProviderResult: ...

    def available(self) -> bool:
        """Whether this provider can currently serve a call at all."""
        ...
