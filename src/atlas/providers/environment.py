"""The environment handed to a CLI subprocess.

Built as an **allowlist**, not by copying ``os.environ`` and deleting the dangerous keys.
A denylist only removes what someone remembered to name, and the consequence of missing
one here is specific and expensive: if ``ANTHROPIC_API_KEY`` or ``OPENAI_API_KEY`` is
present in the child's environment, the CLI silently switches from the user's flat-rate
subscription to metered API billing. Nothing fails, nothing warns, and the bill arrives
later. That single failure mode is the reason this module exists.

The other half of the problem is the opposite mistake. An environment that is *too* empty
breaks OAuth entirely: the CLIs look up cached credentials under the user profile, and
without ``USERPROFILE``, ``APPDATA`` or ``LOCALAPPDATA`` they cannot find them and report
themselves as logged out. So the allowlist is deliberate on both sides -- everything named
here is needed, and nothing else is passed.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["BANNED_VARS", "PASSTHROUGH_VARS", "build_child_env", "leaked_keys"]

#: Never present in a child environment. Their presence flips a provider from
#: subscription auth to metered billing.
BANNED_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        # Third-party routing would also bypass the subscription.
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)

#: Passed through when present. Each entry is here for a reason:
#:
#: ``PATH``                       the CLI shells out to helpers (git, ripgrep)
#: ``SystemRoot`` / ``ComSpec``   Windows breaks in obscure ways without them
#: ``USERPROFILE`` / ``APPDATA``  where OAuth credentials are cached -- omit and every
#: ``LOCALAPPDATA``               provider reports itself logged out
#: ``HOMEDRIVE`` / ``HOMEPATH``   the POSIX-style home lookup some tools still use
#: ``PROCESSOR_ARCHITECTURE``     native binary selection
PASSTHROUGH_VARS: tuple[str, ...] = (
    "PATH",
    "Path",
    "PATHEXT",
    "SystemRoot",
    "SystemDrive",
    "ComSpec",
    "windir",
    "USERPROFILE",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "LANG",
    "LC_ALL",
    # Node-based CLIs consult these for their own config location.
    "NODE_PATH",
    "NPM_CONFIG_PREFIX",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
)


def build_child_env(sandbox: Path, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Compose the environment for one call.

    ``TEMP`` and ``TMP`` are pointed at the sandbox so anything the CLI writes to a
    temporary file lands inside the directory that gets deleted afterwards, rather than
    accumulating in the user's temp folder.
    """
    env: dict[str, str] = {}
    for name in PASSTHROUGH_VARS:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value

    env["TEMP"] = str(sandbox)
    env["TMP"] = str(sandbox)
    # Machine-readable output: colour escapes would corrupt JSON parsing.
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    # Non-interactive by construction, so nothing tries to open an editor or a pager.
    env["CI"] = "1"
    env["PAGER"] = "cat"

    if extra:
        env.update(extra)

    # Belt to the allowlist's braces: assert rather than filter, because a banned key
    # arriving through `extra` is a programming error worth failing loudly on.
    leaked = leaked_keys(env)
    if leaked:
        raise RuntimeError(
            f"refusing to launch a provider with billing-affecting variables set: {sorted(leaked)}. "
            "Their presence would switch the CLI from subscription auth to metered API billing."
        )
    return env


def leaked_keys(env: dict[str, str]) -> set[str]:
    return {name for name in env if name.upper() in BANNED_VARS}
