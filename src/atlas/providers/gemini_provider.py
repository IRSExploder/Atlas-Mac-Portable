"""Gemini over Google's Generative Language API, the second metered path beside Claude's.

The same arrangement as :mod:`atlas.providers.api_provider`, kept deliberately parallel so a
reader of one can read the other:

* **Its own key variable**, :data:`GEMINI_KEY_ENV`, never ``GEMINI_API_KEY`` or
  ``GOOGLE_API_KEY``, so a key set for some other tool cannot quietly turn this on. A key
  typed into Settings lives in the system keychain under its own account (see
  :mod:`atlas.providers.keystore`); the variable, when set, wins.
* **One model for every seat**, :data:`DEFAULT_GEMINI_MODEL`, overridable with
  ``ATLAS_GEMINI_MODEL`` (all seats) or ``ATLAS_GEMINI_MODEL_<ROLE>`` (one), and reported
  honestly: the audit compares what was asked for with what Google says served it.
* **Structured answers through the API's own JSON mode, with the same fallback.** The
  engine's schema is offered as ``responseJsonSchema``; if Google refuses the schema, the
  call is retried once asking for bare JSON in the prompt. Either way the answer then goes
  through exactly the gates a Claude answer does -- nothing here trusts the model more.

Plain HTTPS through ``httpx`` (which the API extra installs, with its certificate bundle) and
no Google SDK: the surface used is one endpoint, and an SDK would be a second large
dependency in the frozen app for the sake of one POST.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from ..state.models import Provider, Role
from . import usage
from .api_provider import _json_body
from .base import AuditVerdict, ModelAudit, ProviderRequest, ProviderResult
from .errors import Failure, FailureKind

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "GEMINI_KEY_ENV",
    "GeminiProvider",
    "gemini_key",
    "gemini_role_models",
]

#: Deliberately not ``GEMINI_API_KEY``: see the module docstring.
GEMINI_KEY_ENV = "ATLAS_GEMINI_API_KEY"

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

#: The keychain account the key is saved under. Claude's is the keystore's default.
KEY_ACCOUNT = "gemini-api-key"

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Output ceilings, as for Claude: the world simulator writes the largest answer.
_MAX_TOKENS: dict[Role, int] = {Role.WORLD_SIM: 65536}
_DEFAULT_MAX_TOKENS = 16000

#: Retried, with a doubling pause, before a failure is reported: what the Anthropic SDK
#: does for the Claude path, done by hand here.
_TRANSIENT = frozenset({429, 500, 502, 503, 504})
_RETRIES = 2


def gemini_key() -> str:
    """The key to use now: the environment variable if set, else the one saved in the app."""
    from_env = os.environ.get(GEMINI_KEY_ENV, "").strip()
    if from_env:
        return from_env
    from .keystore import stored_key

    return stored_key(KEY_ACCOUNT)


def _model_for(role: Role) -> str:
    return (os.environ.get(f"ATLAS_GEMINI_MODEL_{role.name}", "").strip()
            or os.environ.get("ATLAS_GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL)


def gemini_role_models() -> dict[Role, tuple[str, str]]:
    """Every role pinned to this provider, with the model it will actually be served by.

    Filed under ``antigravity``, the engine's name for the Google family of models; in API
    mode every provider key points at the one provider anyway.
    """
    return {role: ("antigravity", _model_for(role))
            for role in Role if role not in (Role.ENGINE,)}


def _failure(kind: FailureKind, message: str, detail: str = "") -> Failure:
    return Failure(kind, "gemini-api", message, detail=detail[:2000])


class _HttpError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body

    @property
    def message(self) -> str:
        try:
            return str(json.loads(self.body)["error"]["message"])
        except (ValueError, KeyError, TypeError):
            return self.body[:500]


def _classify(exc: Exception) -> Failure:
    """A transport or HTTP failure as one of the engine's failure kinds."""
    if isinstance(exc, _HttpError):
        said = exc.message
        low = said.lower()
        if exc.status in (401, 403) or "api key not valid" in low or "api_key_invalid" in low:
            return _failure(FailureKind.NOT_AUTHENTICATED,
                            "Google refused the Gemini API key; check it under Settings > AI "
                            "providers", said)
        if exc.status == 429:
            if "quota" in low or "billing" in low:
                return _failure(FailureKind.QUOTA_EXHAUSTED,
                                "the Gemini key is out of quota", said)
            return _failure(FailureKind.RATE_LIMITED, "the API is rate limiting this key",
                            said)
        if exc.status in (500, 502, 503, 504):
            return _failure(FailureKind.CRASHED,
                            "the Gemini API is overloaded or failing; retry shortly", said)
        return _failure(FailureKind.UNKNOWN,
                        f"the Gemini API refused the request ({exc.status})", said)
    name = type(exc).__name__
    if "Timeout" in name:
        return _failure(FailureKind.TIMEOUT, "the API call ran out of time", str(exc))
    if "Connect" in name or "Network" in name:
        return _failure(FailureKind.CRASHED, "could not reach the Gemini API", str(exc))
    return _failure(FailureKind.UNKNOWN, f"{name}: {exc}", str(exc))


def _schema_refused(exc: Exception) -> bool:
    if not isinstance(exc, _HttpError) or exc.status != 400:
        return False
    low = exc.message.lower()
    return "schema" in low or "response_json_schema" in low or "responsejsonschema" in low


def _answer_text(body: dict[str, Any]) -> tuple[str, str]:
    """The answer's text (thought summaries left out) and why generation stopped."""
    candidates = body.get("candidates") or []
    if not candidates:
        blocked = (body.get("promptFeedback") or {}).get("blockReason") or ""
        return "", f"BLOCKED:{blocked}" if blocked else ""
    first = candidates[0]
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if not p.get("thought"))
    return text.strip(), str(first.get("finishReason") or "")


class GeminiProvider:
    """A :class:`~atlas.providers.base.ModelProvider` on the Gemini API."""

    name = Provider.ANTIGRAVITY

    def __init__(self, key: str | None = None, *, post: Any = None,
                 backoff: float = 2.0) -> None:
        # ``None`` means "look it up on every call", so a key saved in Settings mid-session
        # is used from the next call on, with no restart.
        self._fixed_key = key
        # ``post(url, headers, payload, timeout) -> (status, body_text)``; injectable so
        # the tests never touch the network.
        self._post = post or _httpx_post
        self._backoff = backoff

    def _key(self) -> str:
        return self._fixed_key if self._fixed_key is not None else gemini_key()

    def available(self) -> bool:
        return bool(self._key())

    async def _call(self, request: ProviderRequest, model: str, *,
                    structured: bool) -> dict[str, Any]:
        prompt = request.prompt
        config: dict[str, Any] = {
            "maxOutputTokens": _MAX_TOKENS.get(request.role, _DEFAULT_MAX_TOKENS),
        }
        if request.schema is not None:
            config["responseMimeType"] = "application/json"
            if structured:
                config["responseJsonSchema"] = request.schema
            else:
                prompt += ("\n\nReply with a single JSON object and nothing else. It must "
                           "match this JSON schema:\n" + json.dumps(request.schema))
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        if request.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        for attempt in range(_RETRIES + 1):
            status, text = await self._post(
                _ENDPOINT.format(model=model),
                {"x-goog-api-key": self._key(), "Content-Type": "application/json"},
                payload, request.timeout_s)
            if status in _TRANSIENT and attempt < _RETRIES:
                await asyncio.sleep(self._backoff * (2 ** attempt))
                continue
            if status != 200:
                raise _HttpError(status, text)
            return json.loads(text)
        raise AssertionError("unreachable")  # pragma: no cover

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        model = _model_for(request.role)

        def result(**fields: Any) -> ProviderResult:
            return ProviderResult(provider=self.name, role=request.role,
                                  duration_s=time.monotonic() - started, **fields)

        if not self.available():
            return result(ok=False, failure=_failure(
                FailureKind.NOT_AUTHENTICATED,
                "no Gemini API key yet: add it under Settings > AI providers"))

        try:
            try:
                body = await self._call(request, model, structured=True)
            except Exception as exc:  # noqa: BLE001 - classified below, never raised
                if request.schema is None or not _schema_refused(exc):
                    raise
                body = await self._call(request, model, structured=False)
        except Exception as exc:  # noqa: BLE001 - a failure is a value here
            return result(ok=False, failure=_classify(exc))

        served = str(body.get("modelVersion") or "") or None
        verdict = (AuditVerdict.MATCH if served and served.startswith(str(request.model))
                   else AuditVerdict.MISMATCH)
        audit = ModelAudit(
            requested=request.model, served=served, verdict=verdict,
            note="" if request.model == model else
            f"Gemini mode serves {request.role.value} with {model}, not {request.model}")
        self._record_usage(request, body, model)

        text, stop = _answer_text(body)
        if stop.startswith("BLOCKED") or stop in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            return result(ok=False, audit=audit, text=text, failure=_failure(
                FailureKind.UNKNOWN, "the model declined this request (safety filter)", stop))
        if not text:
            return result(ok=False, audit=audit, failure=_failure(
                FailureKind.EMPTY_RESPONSE, "the model returned no text"))
        if request.schema is None:
            return result(ok=True, text=text, audit=audit)

        try:
            data = json.loads(_json_body(text))
        except ValueError:
            reason = ("the answer was cut off at the output limit" if stop == "MAX_TOKENS"
                      else "the answer was not valid JSON")
            return result(ok=False, text=text, audit=audit,
                          failure=_failure(FailureKind.MALFORMED_OUTPUT, reason, text))
        return result(ok=True, text=text, data=data, audit=audit)

    def _record_usage(self, request: ProviderRequest, body: dict[str, Any],
                      model: str) -> None:
        """Offer the bill to the usage sinks, as the other providers do. Never costs a turn."""
        if not usage._RECORDERS:
            return
        try:
            meta = body.get("usageMetadata") or {}
            prompt = int(meta.get("promptTokenCount") or 0)
            output = (int(meta.get("candidatesTokenCount") or 0)
                      + int(meta.get("thoughtsTokenCount") or 0))
            usage.record(usage.TokenUsage(
                provider="gemini-api", model=model, role=request.role.value,
                input_tokens=prompt, output_tokens=output,
                cache_read_tokens=int(meta.get("cachedContentTokenCount") or 0),
                cache_write_tokens=0,
                total_tokens=int(meta.get("totalTokenCount") or prompt + output),
            ))
        except Exception:  # noqa: BLE001 - measurement must never cost a turn
            return


async def _httpx_post(url: str, headers: dict[str, str], payload: dict[str, Any],
                      timeout: float) -> tuple[int, str]:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        return response.status_code, response.text
