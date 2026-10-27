"""The concrete provider: sandbox, argv, launch, classify, audit.

One class serves all three CLIs by delegating the provider-specific parts to an adapter
module. The shared shape -- create a sandbox, build a scrubbed environment, run the
process, classify the outcome, verify the model pin, tear down -- is identical, and
duplicating it three times would guarantee the three copies drift.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..state.models import Provider, Role
from . import agy_adapter, claude_adapter, codex_adapter, usage
from .base import AuditVerdict, ModelAudit, ProviderRequest, ProviderResult
from .environment import build_child_env
from .errors import Failure, FailureKind
from .process import run_cli
from .resolver import ResolvedCli, resolve
from .sandbox import SandboxDir

__all__ = ["CliProvider", "make_provider", "schema_shortfall"]

log = logging.getLogger(__name__)

_PROVIDER_OF = {
    "claude": Provider.CLAUDE,
    "codex": Provider.CODEX,
    "antigravity": Provider.ANTIGRAVITY,
}


def schema_shortfall(schema: dict, data: object) -> list[str]:
    """Required top-level keys the response failed to supply.

    A cheap shape check, not a validating parser: the CLIs enforce the schema themselves
    when they answer at all, so the case worth catching here is the one they get wrong --
    returning something well-formed that answers nothing. Returning *all* required keys
    means the object is unrelated to what was asked for.

    Empty when the schema declares no required keys, since then there is nothing to
    fall short of.
    """
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list) or not required:
        return []
    if not isinstance(data, dict):
        return [str(key) for key in required]
    missing = [str(key) for key in required if key not in data]
    # A partial answer is the provider's business and the schema's own enforcement
    # problem. Only a response supplying *none* of the required keys is the failure this
    # guard exists for -- flagging partials would reject answers the engine can use.
    return missing if len(missing) == len(required) else []


@dataclass(slots=True)
class CliProvider:
    """Drives one locally installed CLI."""

    key: str
    resolved: ResolvedCli
    keep_sandbox: bool = False

    @property
    def name(self) -> Provider:
        return _PROVIDER_OF[self.key]

    def available(self) -> bool:
        return self.resolved.found

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        if not self.resolved.found or self.resolved.executable is None:
            return self._fail(
                request,
                Failure(
                    FailureKind.NOT_INSTALLED,
                    self.key,
                    self.resolved.note or f"{self.key} is not installed",
                ),
            )

        started = time.monotonic()
        executable = str(self.resolved.executable)

        with SandboxDir.create(keep=self.keep_sandbox) as sandbox:
            env = build_child_env(sandbox.path)
            stdin_payload: str | None = None

            if self.key == "claude":
                argv = claude_adapter.build_argv(executable, request)
                stdin_payload = request.prompt
            elif self.key == "codex":
                argv = codex_adapter.build_argv(executable, request, sandbox.path)
                stdin_payload = request.prompt
            else:
                argv, prompt_file = agy_adapter.build_argv(executable, request, sandbox.path)
                if prompt_file is not None:
                    sandbox.write(agy_adapter.PROMPT_FILE, prompt_file)
                # agy discards stdin entirely; the prompt is already on argv.
                stdin_payload = None

            outcome = await run_cli(
                argv,
                cwd=sandbox.path,
                env=env,
                stdin_payload=stdin_payload,
                timeout_s=request.timeout_s,
            )

            # Read anything the CLI wrote into the sandbox *before* it is torn down.
            text, data, audit = self._interpret(request, outcome)

        # What the provider says the call cost, offered to any installed sink before the
        # result is judged. Recorded here rather than on the success path because a call
        # that timed out or came back malformed **was still billed**, and a cost model
        # that only counts the calls that worked understates the bill.
        self._record_usage(request, outcome)

        if outcome.launch_error is not None:
            return self._fail(
                request,
                Failure(FailureKind.CRASHED, self.key, "could not start the CLI", outcome.launch_error),
                outcome=outcome,
            )

        if outcome.timed_out:
            return self._fail(
                request,
                Failure(
                    FailureKind.TIMEOUT,
                    self.key,
                    f"no response within {request.timeout_s:.0f}s",
                    (outcome.stderr or outcome.stdout)[:2000],
                ),
                outcome=outcome,
            )

        failure = self._classify(outcome, text, request.prompt)
        if failure is not None:
            return self._fail(request, failure, outcome=outcome, audit=audit)

        # A call that returned nothing is a failed call, whatever the exit code says.
        # Checked before the schema branch because an empty answer and a malformed one
        # need different messages and different remedies.
        if not text.strip() and data is None:
            return self._fail(
                request,
                Failure(
                    FailureKind.EMPTY_RESPONSE,
                    self.key,
                    f"{self.key} reported success but returned no answer "
                    f"(model {request.model})",
                    (outcome.stdout or outcome.stderr)[:2000],
                ),
                outcome=outcome,
                audit=audit,
            )

        if request.schema is not None:
            if data is None:
                return self._fail(
                    request,
                    Failure(
                        FailureKind.MALFORMED_OUTPUT,
                        self.key,
                        "the response did not parse as JSON matching the required schema",
                        text[:2000],
                    ),
                    outcome=outcome,
                    audit=audit,
                )

            # Parsing to *some* object is not the same as answering. A provider can hand
            # back a well-formed object that contains none of what was asked for, and
            # every downstream reader then sees an empty result rather than an error.
            missing = schema_shortfall(request.schema, data)
            if missing:
                return self._fail(
                    request,
                    Failure(
                        FailureKind.EMPTY_RESPONSE,
                        self.key,
                        f"{self.key} returned an object with none of the required keys "
                        f"({', '.join(missing)}); model {request.model} did not answer",
                        text[:2000],
                    ),
                    outcome=outcome,
                    audit=audit,
                )

        return ProviderResult(
            provider=self.name,
            role=request.role,
            ok=True,
            text=text,
            data=data,
            audit=audit,
            duration_s=time.monotonic() - started,
            raw_stdout=outcome.stdout[:20000],
            raw_stderr=outcome.stderr[:8000],
            argv=outcome.argv,
        )

    # -- internals ---------------------------------------------------------

    def _interpret(self, request: ProviderRequest, outcome) -> tuple[str, object, ModelAudit]:
        if self.key == "claude":
            text, data, envelope = claude_adapter.parse_result(outcome.stdout)
            return text, data, claude_adapter.audit_from_envelope(request.model, envelope)
        if self.key == "codex":
            sandbox_path = Path(outcome.argv[outcome.argv.index("--cd") + 1])
            text, data = codex_adapter.parse_result(sandbox_path, outcome.stdout)
            return text, data, codex_adapter.audit_from_output(request.model, outcome.stdout, outcome.stderr)
        text, data = agy_adapter.parse_result(outcome.stdout)
        return text, data, agy_adapter.audit(request.model, text)

    def _record_usage(self, request: ProviderRequest, outcome) -> None:
        """Parse whatever this CLI said about tokens and offer it to the sinks.

        Wrapped because measurement is not worth a turn: a CLI that changes its usage
        format should make the numbers stop, not make the game stop.
        """
        if not usage._RECORDERS:
            return
        try:
            if self.key == "claude":
                _t, _d, envelope = claude_adapter.parse_result(outcome.stdout)
                measured = usage.usage_from_claude(envelope)
            elif self.key == "codex":
                measured = usage.usage_from_codex(outcome.stderr, outcome.stdout)
            else:
                measured = usage.usage_from_agy(agy_adapter.envelope_of(outcome.stdout))
            usage.record(
                replace(
                    measured, model=request.model, role=request.role.value,
                    provider=self.key,
                )
            )
        except Exception:  # noqa: BLE001 - see the docstring
            return

    def _classify(self, outcome, text: str, prompt: str = "") -> Failure | None:
        if self.key == "claude":
            _, _, envelope = claude_adapter.parse_result(outcome.stdout)
            return claude_adapter.classify(outcome.exit_code, outcome.stdout, outcome.stderr, envelope)
        if self.key == "codex":
            return codex_adapter.classify(outcome.exit_code, outcome.stdout, outcome.stderr,
                                          prompt=prompt, text=text)
        return agy_adapter.classify(outcome.exit_code, outcome.stdout, outcome.stderr, text=text)

    def _fail(
        self, request: ProviderRequest, failure: Failure, *, outcome=None, audit: ModelAudit | None = None
    ) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            role=request.role,
            ok=False,
            failure=failure,
            audit=audit or ModelAudit(request.model, None, AuditVerdict.UNVERIFIED),
            duration_s=getattr(outcome, "duration_s", 0.0),
            raw_stdout=getattr(outcome, "stdout", "")[:20000],
            raw_stderr=getattr(outcome, "stderr", "")[:8000],
            argv=getattr(outcome, "argv", ()),
        )


def make_provider(key: str, *, keep_sandbox: bool = False) -> CliProvider:
    return CliProvider(key=key, resolved=resolve(key), keep_sandbox=keep_sandbox)


#: Which CLIs report the model that actually served a call. **A fact about the tools, not a
#: preference**: claude carries it in its usage block and codex in its human-readable banner,
#: while agy answers every call `UNVERIFIED` -- see :mod:`atlas.providers.agy_adapter`, which
#: says so in the one place that would know.
#:
#: It is written down here because it is a *routing* input. A role whose output the engine
#: cannot afford to misattribute belongs on a provider that says who answered.
ATTESTING: frozenset[str] = frozenset({"claude", "codex"})


#: Where the world simulation goes when agy has failed repeatedly. Not a per-call
#: alternative -- see :class:`atlas.engine.routing.Breaker` for why the distinction matters.
WORLD_SIM_FALLBACK: tuple[str, str] = ("claude", "claude-sonnet-5")


def default_role_models() -> dict[Role, tuple[str, str]]:
    """The pinned model per role, as ``(provider_key, model)``.

    Only strings verified to exist on this machine. Two notes worth keeping:

    ``Gemini 3.5 Flash-Lite``, named in the original design, is not offered by this
    install at all; Focus Mode uses the nearest real tier instead.

    The agy names use the slug scheme that ``agy models`` reports as of 1.1.8. Version
    1.1.4 listed display names like ``"Gemini 3.6 Flash (Medium)"`` instead, and 1.1.8
    still accepts those -- but pins should follow whatever the installed CLI reports,
    which is why :func:`validate_pins` checks them rather than trusting this table.

    **No role is pinned to a ``-low`` agy tier any more.** Measured against the live CLI:
    a world-sim call that succeeds 3/3 on ``gemini-3.6-flash-medium`` returns an empty
    answer 3/3 on ``gemini-3.5-flash-low``, with exit 0 and ``"status":"SUCCESS"``. That
    tier does not produce schema-constrained output through agy at all, and every role
    in this table asks for exactly that. Focus Mode and the arbiter were both pinned
    there, so both were silently doing nothing.

    Tracked agents are on **codex** rather than agy for the same measurement: on the
    tracked-agent call agy is roughly one-in-three reliable even at its best tier, while
    codex answered 3/3 with usable diffs.

    **The world simulator is back on agy, and the measurement that moved it back is the
    reason.** It was re-pinned to claude after PT33 on the strength of four calls -- 22s and
    37s against agy's 16s and 22s, on one Modern Day prompt -- with the note conceding at the
    time that "that is not enough to characterise the distribution". A night of recorded runs
    put 391 world-sim calls through claude across five playtests, and the distribution it
    characterises does not support the re-pin:

    * **claude is roughly six times slower on this call** than agy's recorded figures, not
      the 1.5x the four-call sample suggested;
    * **and it is not more reliable.** PT34g died at turn 6 to twenty-seven consecutive
      `claude exited with code 1` crashes -- about 5% of that run's calls -- which is the same
      *shape* of failure agy was moved off for, on the same critical path.

    A provider that justifies neither the latency nor the reliability of the every-turn path
    is not worth the swap. So agy is primary again, and the *resilience* is built rather than
    hoped for: one retry per call, and a circuit breaker that falls back to claude only after
    repeated failure (see :class:`atlas.engine.routing.Breaker`). A per-call switch would
    hide exactly the episodic window that made this hard to see the first time; a breaker
    records it.

    **What is measured from here is engine-accepted output, not provider success.** A
    provider that answers every call and proposes diffs the rules refuse is worse than one
    that fails loudly, and the earlier comparison could not have told them apart.
    `JumpReport.world_sim_accepted` carries proposed/applied/refused per turn so the next
    run's tapes answer the question the last one could not. The bar Alex set: revert unless
    agy's *output* is materially worse; broadly comparable means the revert is correct.

    **The arbiter stays on agy deliberately.** It is EVE-only and rare, and EVE's whole
    premise is three genuinely different judgment sources; taking agy out of this table
    entirely would undermine that quietly rather than by decision.
    """
    return {
        # Primary. `Breaker` falls back to WORLD_SIM_FALLBACK after repeated failure.
        Role.WORLD_SIM: ("antigravity", "gemini-3.6-flash-medium"),
        Role.TRACKED_AGENT: ("codex", "gpt-5.6-terra"),
        Role.DIPLOMACY: ("claude", "claude-sonnet-5"),
        # The advisor is the same judgment source as diplomacy, deliberately: holding a
        # position under pressure and reading a strategic position are the same skill,
        # and a council that folds the moment the player pushes back is worth nothing.
        Role.ADVISOR: ("claude", "claude-sonnet-5"),
        Role.PARSER: ("codex", "gpt-5.6-terra"),
        # Freeform answers an order nobody wrote a rule for, so what it needs is judgment
        # about consequence and a straight face. The same source as diplomacy and the
        # council, for the same reason.
        Role.FREEFORM: ("claude", "claude-sonnet-5"),
        # Same pin as freeform: both ask a model to be inventive inside a very small
        # box, which is a different job from refereeing a world.
        Role.GAMBIT: ("claude", "claude-sonnet-5"),
        # **The Security Council had no pin at all until this was added**, and
        # `Router.route` reads `self.pins[role]` directly -- so the first time a body
        # convened in the shipped app the turn died with a `KeyError`, and the whole
        # organisation layer was unreachable outside the harnesses that passed their own
        # pins. `test_every_model_role_has_a_pin` is the guard so a future role cannot
        # ship the same way. Same source as diplomacy: a council under pressure is the
        # same job as holding a position under pressure.
        Role.ORGANISATION: ("claude", "claude-sonnet-5"),
        Role.ARBITER: ("antigravity", "gemini-3.6-flash-medium"),
    }


#: EVE assigns one bloc per provider. agy can route to Claude models
#: (``claude-sonnet-4-6``, ``claude-opus-4-6-thinking`` are both in its list), and pinning
#: bloc C to one of those would put two of the three blocs on the same model family --
#: collapsing the entire premise that EVE contrasts three genuinely different judgment
#: sources rather than three personas over shared priors.
EVE_BLOC_MODELS: dict[str, tuple[str, str]] = {
    "A": ("claude", "claude-sonnet-5"),
    "B": ("codex", "gpt-5.6-terra"),
    "C": ("antigravity", "gemini-3.6-flash-high"),
}


def validate_pins(
    pins: dict[Role, tuple[str, str]], available: dict[str, tuple[str, ...]]
) -> list[str]:
    """Check every pinned model against what the installed CLI actually offers.

    Returns human-readable problems. Only agy publishes a machine-readable list, so the
    other two are checked for obvious emptiness rather than membership.
    """
    from .agy_adapter import normalise_model

    problems: list[str] = []
    agy_models = {normalise_model(name) for name in available.get("antigravity", ())}

    for role, (provider_key, model) in pins.items():
        if not model:
            problems.append(f"{role.value}: no model pinned")
        elif provider_key == "antigravity" and agy_models and normalise_model(model) not in agy_models:
            problems.append(
                f"{role.value}: agy does not offer {model!r}. Run `atlas doctor --models` "
                "for the accepted names."
            )
    return problems


def eve_family_conflict(blocs: dict[str, tuple[str, str]]) -> str | None:
    """Refuse an EVE setup where two blocs resolve to the same model family."""
    families: dict[str, str] = {}
    for bloc, (_provider, model) in blocs.items():
        lowered = model.lower()
        family = next(
            (name for name in ("claude", "gpt", "gemini", "opus", "sonnet") if name in lowered),
            lowered,
        )
        family = {"opus": "claude", "sonnet": "claude"}.get(family, family)
        if family in families:
            return (
                f"blocs {families[family]} and {bloc} both use the {family} family "
                f"({model!r}). EVE contrasts three different judgment sources; two blocs "
                "from one family makes the comparison meaningless."
            )
        families[family] = bloc
    return None
