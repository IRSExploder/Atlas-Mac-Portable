"""Claude over the Anthropic Messages API, for the seats a player wants a real model in.

**This is the one metered path in ATLAS, and it is opt-in by name.** Everything else drives
subscription CLIs, and `atlas.providers.environment` strips ``ANTHROPIC_API_KEY`` from their
children so a stray key can never silently turn a flat-rate call into a billed one. This
provider therefore reads its key from its *own* variable, :data:`API_KEY_ENV`, and passes it
to the SDK explicitly: setting it cannot change what any CLI does, and nothing reaches this
module unless the player asked for ``--api``.

**One model per role, chosen here, and reported honestly.** In API mode every role's pin
points at this provider with the model configured for it (:func:`api_role_models`), so the
request names the model that will serve it and the audit reads ``MATCH``. If something asks
for a different model anyway -- the world simulator's circuit breaker names its own fallback
-- the call is still served by the configured model and the audit says ``MISMATCH`` rather
than pretending, so a fallback can never quietly move the game onto a pricier model.

**Structured answers use the API's own JSON-schema output.** The engine's schemas are written
for the strictest dialect any provider accepts, but two things in them the API does not take
directly: ``"type": [..., "null"]`` lists, which are rewritten as ``anyOf`` before
:func:`anthropic.transform_schema` moves unsupported constraints into descriptions, and
schemas the API may still judge too complex -- refused once, the call is retried asking for
bare JSON in the prompt. Either way the answer then goes through the same gates a CLI's does.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..state.models import Provider, Role
from . import usage
from .base import AuditVerdict, ModelAudit, ProviderRequest, ProviderResult
from .errors import Failure, FailureKind

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_MODEL",
    "ApiProvider",
    "api_key",
    "api_role_models",
]

#: Deliberately not ``ANTHROPIC_API_KEY``: see the module docstring.
API_KEY_ENV = "ATLAS_ANTHROPIC_API_KEY"

#: The default for every API seat. ``ATLAS_API_MODEL`` changes it for all of them, and
#: ``ATLAS_API_MODEL_<ROLE>`` (e.g. ``ATLAS_API_MODEL_WORLD_SIM``) for one.
DEFAULT_MODEL = "claude-haiku-5-5"

#: How hard each seat thinks, overridable with ``ATLAS_API_EFFORT_<ROLE>``. The world
#: simulator referees the whole world into a large structured batch and is worth the most;
#: a reply in talks is a short piece of prose.
_DEFAULT_EFFORT: dict[Role, str] = {
    Role.WORLD_SIM: "high",
    Role.ADVISOR: "medium",
    Role.DIPLOMACY: "low",
    Role.FREEFORM: "low",
    Role.PARSER: "low",
}

#: Output ceilings. Streamed, so a large one costs nothing unless it is used.
_MAX_TOKENS: dict[Role, int] = {Role.WORLD_SIM: 64000}
_DEFAULT_MAX_TOKENS = 16000

_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def api_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def _model_for(role: Role) -> str:
    return (os.environ.get(f"ATLAS_API_MODEL_{role.name}", "").strip()
            or os.environ.get("ATLAS_API_MODEL", "").strip() or DEFAULT_MODEL)


def _effort_for(role: Role) -> str:
    chosen = os.environ.get(f"ATLAS_API_EFFORT_{role.name}", "").strip().lower()
    return chosen if chosen in _EFFORTS else _DEFAULT_EFFORT.get(role, "medium")


def api_role_models() -> dict[Role, tuple[str, str]]:
    """Every role pinned to this provider, with the model it will actually be served by."""
    return {role: ("claude", _model_for(role))
            for role in Role if role not in (Role.ENGINE,)}


def _split_type_lists(schema: Any) -> Any:
    """Rewrite ``"type": [a, b]`` as ``anyOf``, recursively, for `transform_schema`."""
    if isinstance(schema, list):
        return [_split_type_lists(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: _split_type_lists(value) for key, value in schema.items()}
    types = out.get("type")
    if not isinstance(types, list):
        return out
    shared = {k: out.pop(k) for k in ("description", "title") if k in out}
    out.pop("type")
    variants = []
    for kind in types:
        if kind == "null":
            variants.append({"type": "null"})
            continue
        variant = {"type": kind, **out}
        if isinstance(variant.get("enum"), list):
            variant["enum"] = [v for v in variant["enum"] if v is not None]
        variants.append(variant)
    return {"anyOf": variants, **shared}


def api_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The engine's schema, in the form the API's structured output accepts."""
    from anthropic import transform_schema

    return transform_schema(_split_type_lists(schema))


def _failure(kind: FailureKind, message: str, detail: str = "") -> Failure:
    return Failure(kind, "claude-api", message, detail=detail[:2000])


def _classify(exc: Exception) -> Failure:
    """An SDK exception as one of the engine's failure kinds. Most specific first."""
    import anthropic

    said = str(getattr(exc, "message", "") or exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return _failure(FailureKind.NOT_AUTHENTICATED,
                        f"the API key in {API_KEY_ENV} was refused", said)
    if isinstance(exc, anthropic.RateLimitError):
        return _failure(FailureKind.RATE_LIMITED, "the API is rate limiting this key", said)
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.DeadlineExceededError)):
        return _failure(FailureKind.TIMEOUT, "the API call ran out of time", said)
    if isinstance(exc, (anthropic.OverloadedError, anthropic.ServiceUnavailableError,
                        anthropic.InternalServerError)):
        return _failure(FailureKind.CRASHED, "the API is overloaded or failing; retry shortly",
                        said)
    if isinstance(exc, anthropic.APIConnectionError):
        return _failure(FailureKind.CRASHED, "could not reach the Anthropic API", said)
    if isinstance(exc, anthropic.APIStatusError):
        if "credit" in said.lower() or "billing" in said.lower():
            return _failure(FailureKind.QUOTA_EXHAUSTED,
                            "the API account is out of credit", said)
        return _failure(FailureKind.UNKNOWN,
                        f"the API refused the request ({exc.status_code})", said)
    return _failure(FailureKind.UNKNOWN, f"{type(exc).__name__}: {said}", said)


def _schema_refused(exc: Exception) -> bool:
    import anthropic

    said = str(getattr(exc, "message", "") or exc).lower()
    return isinstance(exc, anthropic.BadRequestError) and (
        "schema" in said or "output_config" in said or "format" in said)


class ApiProvider:
    """A :class:`~atlas.providers.base.ModelProvider` on the Anthropic Messages API."""

    name = Provider.CLAUDE

    def __init__(self, key: str | None = None, *, client: Any = None,
                 max_retries: int = 2) -> None:
        self._key = api_key() if key is None else key
        self._client = client
        self._injected = client is not None
        self._loop: object = None
        self._max_retries = max_retries

    def available(self) -> bool:
        return bool(self._key) or self._client is not None

    def _sdk(self):
        """The async client for the running event loop.

        An async HTTP client belongs to the loop it was first used on. The game keeps one
        loop for its whole life, but a caller that runs several (``asyncio.run`` per
        command) would otherwise reuse a client whose loop has closed.
        """
        import asyncio

        if self._injected:
            return self._client
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._key, max_retries=self._max_retries)
            self._loop = loop
        return self._client

    async def _call(self, request: ProviderRequest, model: str, *, structured: bool):
        prompt = request.prompt
        output_config: dict[str, Any] = {"effort": _effort_for(request.role)}
        if request.schema is not None and structured:
            output_config["format"] = {"type": "json_schema",
                                       "schema": api_schema(request.schema)}
        elif request.schema is not None:
            prompt += ("\n\nReply with a single JSON object and nothing else. It must match "
                       "this JSON schema:\n" + json.dumps(request.schema))
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": _MAX_TOKENS.get(request.role, _DEFAULT_MAX_TOKENS),
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
            "timeout": request.timeout_s,
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        async with self._sdk().messages.stream(**kwargs) as stream:
            return await stream.get_final_message()

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        model = _model_for(request.role)

        def result(**fields: Any) -> ProviderResult:
            return ProviderResult(provider=self.name, role=request.role,
                                  duration_s=time.monotonic() - started, **fields)

        if not self.available():
            return result(ok=False, failure=_failure(
                FailureKind.NOT_AUTHENTICATED, f"{API_KEY_ENV} is not set"))

        try:
            try:
                message = await self._call(request, model, structured=True)
            except Exception as exc:  # noqa: BLE001 - classified below, never raised
                if request.schema is None or not _schema_refused(exc):
                    raise
                message = await self._call(request, model, structured=False)
        except Exception as exc:  # noqa: BLE001 - a failure is a value here
            return result(ok=False, failure=_classify(exc))

        served = str(getattr(message, "model", "") or "") or None
        verdict = AuditVerdict.MATCH if served == request.model else AuditVerdict.MISMATCH
        audit = ModelAudit(
            requested=request.model, served=served, verdict=verdict,
            note="" if request.model == model else
            f"API mode serves {request.role.value} with {model}, not {request.model}")
        self._record_usage(request, message, model)

        text = "".join(block.text for block in message.content
                       if getattr(block, "type", "") == "text").strip()
        stop = getattr(message, "stop_reason", None)
        if stop == "refusal":
            return result(ok=False, audit=audit, text=text, failure=_failure(
                FailureKind.UNKNOWN, "the model declined this request (safety refusal)",
                str(getattr(message, "stop_details", "") or "")))
        if not text:
            return result(ok=False, audit=audit, failure=_failure(
                FailureKind.EMPTY_RESPONSE, "the model returned no text"))
        if request.schema is None:
            return result(ok=True, text=text, audit=audit)

        try:
            data = json.loads(_json_body(text))
        except ValueError:
            reason = ("the answer was cut off at the output limit" if stop == "max_tokens"
                      else "the answer was not valid JSON")
            return result(ok=False, text=text, audit=audit,
                          failure=_failure(FailureKind.MALFORMED_OUTPUT, reason, text))
        return result(ok=True, text=text, data=data, audit=audit)

    def _record_usage(self, request: ProviderRequest, message: Any, model: str) -> None:
        """Offer the bill to the usage sinks, as the CLI providers do. Never costs a turn."""
        if not usage._RECORDERS:
            return
        try:
            billed = message.usage
            usage.record(usage.TokenUsage(
                provider="claude-api", model=model, role=request.role.value,
                input_tokens=billed.input_tokens, output_tokens=billed.output_tokens,
                cache_read_tokens=billed.cache_read_input_tokens,
                cache_write_tokens=billed.cache_creation_input_tokens,
                total_tokens=billed.input_tokens + billed.output_tokens,
            ))
        except Exception:  # noqa: BLE001 - measurement must never cost a turn
            return


def _json_body(text: str) -> str:
    """The JSON object in an answer, tolerating a fenced block from the plain-JSON retry."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start:end + 1] if start != -1 and end > start else stripped
