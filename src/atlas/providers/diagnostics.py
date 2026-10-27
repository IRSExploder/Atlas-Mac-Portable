"""What is installed, what it can do, and how well it is actually contained.

Capabilities are probed at startup rather than hardcoded, because CLI flags drift and a
flag renamed upstream should surface as a Diagnostics row rather than as a mysterious
mid-turn failure.

The sandbox badge is deliberately blunt. Codex has a real CLI-enforced read-only mode.
Claude can have every tool switched off at the flag level. agy has neither in any form we
can verify -- and because it ignores the working directory entirely, the per-call temp
directory is not even weak containment for it. Saying "sandboxed" for all three would be
false, and the failure mode of a false safety claim is that nobody looks again.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum

from ..state.models import Role
from .agy_adapter import parse_models
from .base import ProviderRequest
from .cli_provider import CliProvider, default_role_models, make_provider
from .environment import BANNED_VARS
from .resolver import resolve_all

__all__ = ["Containment", "ProviderDiagnostic", "collect", "probe_live"]


class Containment(StrEnum):
    CLI_ENFORCED = "cli_enforced"
    TOOLS_DISABLED = "tools_disabled"
    #: The sandbox directory is not a boundary for this provider at all.
    ADD_DIR_ONLY = "add_dir_only"
    NONE = "none"

    @property
    def badge(self) -> str:
        return {
            Containment.CLI_ENFORCED: "CLI-enforced read-only sandbox",
            Containment.TOOLS_DISABLED: "All tools disabled at the flag level",
            Containment.ADD_DIR_ONLY: "Directory grant only - NOT a verified sandbox",
            Containment.NONE: "No enforced containment",
        }[self]

    @property
    def is_warning(self) -> bool:
        return self in {Containment.ADD_DIR_ONLY, Containment.NONE}


#: How each provider is actually contained, with the reason stated.
CONTAINMENT: dict[str, tuple[Containment, str]] = {
    "codex": (
        Containment.CLI_ENFORCED,
        "`--sandbox read-only` is enforced by the CLI itself. The strongest of the three.",
    ),
    "claude": (
        Containment.TOOLS_DISABLED,
        "`--tools \"\"` removes every built-in tool, so there is nothing to escape with. "
        "The per-call temp directory is a second layer.",
    ),
    "antigravity": (
        Containment.ADD_DIR_ONLY,
        "agy ignores the working directory it is given and runs in its own scratch folder, "
        "so per-call temp-dir isolation does NOT contain it. What limits its reach is "
        "`--add-dir`, scoped to the single call sandbox. `--sandbox` is also passed, but its "
        "enforcement strength is unverified, so it is not relied on.",
    ),
}


@dataclass(slots=True)
class ProviderDiagnostic:
    key: str
    installed: bool
    executable: str | None
    via: str
    version: str | None
    containment: Containment
    containment_note: str
    models: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        mark = "OK  " if self.installed else "MISS"
        lines = [f"[{mark}] {self.key}"]
        if self.executable:
            lines.append(f"       path      {self.executable}  ({self.via})")
        if self.version:
            lines.append(f"       version   {self.version}")
        flag = "!" if self.containment.is_warning else " "
        lines.append(f"     {flag} sandbox   {self.containment.badge}")
        lines.append(f"       {self.containment_note}")
        if self.models:
            lines.append(f"       models    {len(self.models)} available, e.g. {self.models[0]!r}")
        lines.extend(f"       note      {note}" for note in self.notes)
        return "\n".join(lines)


def _version_of(executable: str, key: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else None


def _agy_models(executable: str) -> tuple[str, ...]:
    """Read the accepted ``--model`` strings straight from the CLI.

    Hardcoding these would be a trap: the accepted value includes a tier suffix and the
    set changes between releases.
    """
    try:
        result = subprocess.run(
            [executable, "models"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return parse_models(result.stdout or "")


def collect() -> list[ProviderDiagnostic]:
    """Inspect every provider without spending a single model call."""
    out: list[ProviderDiagnostic] = []
    for key, resolved in resolve_all().items():
        containment, note = CONTAINMENT[key]
        diagnostic = ProviderDiagnostic(
            key=key,
            installed=resolved.found,
            executable=str(resolved.executable) if resolved.executable else None,
            via=resolved.via,
            version=None,
            containment=containment,
            containment_note=note,
        )
        if resolved.found and resolved.executable:
            diagnostic.version = _version_of(str(resolved.executable), key)
            if key == "antigravity":
                diagnostic.models = _agy_models(str(resolved.executable))
                if not diagnostic.models:
                    diagnostic.notes.append("could not read `agy models`; model pins cannot be validated")
        elif resolved.note:
            diagnostic.notes.append(resolved.note)

        out.append(diagnostic)

    leaked = sorted(name for name in BANNED_VARS if _env_has(name))
    if leaked:
        for diagnostic in out:
            diagnostic.notes.append(
                f"WARNING: {', '.join(leaked)} present in this shell. ATLAS strips these from "
                "child processes, but their presence suggests metered billing is configured "
                "somewhere."
            )
    return out


def _env_has(name: str) -> bool:
    import os

    return bool(os.environ.get(name))


async def probe_live(key: str, *, model: str | None = None, timeout_s: float = 180.0):
    """Spend one tiny real call to prove the whole path works end to end.

    This is what catches the failures that static inspection cannot: an expired login, a
    renamed flag, a model string the CLI no longer accepts, or a prompt that never
    arrives.
    """
    provider: CliProvider = make_provider(key)
    if not provider.available():
        return None

    pins = default_role_models()
    chosen = model or next(m for k, m in pins.values() if k == key)

    request = ProviderRequest(
        role=Role.PARSER,
        prompt="Reply with exactly the word: ATLAS",
        model=chosen,
        timeout_s=timeout_s,
        options={"reasoning_effort": "low"},
    )
    return await provider.invoke(request)


def probe_live_sync(key: str, *, model: str | None = None, timeout_s: float = 180.0):
    return asyncio.run(probe_live(key, model=model, timeout_s=timeout_s))
