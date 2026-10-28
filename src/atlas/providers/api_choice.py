"""Which hosted model plays in API mode: Claude or Gemini.

Decided in this order, so the most explicit choice wins:

1. ``ATLAS_API_PROVIDER`` (``claude`` or ``gemini``), for a session;
2. the choice made in Settings > AI providers, kept in a small file beside the keys (it is
   not a secret, so it is not in the keychain);
3. whichever provider has a key, Claude first, because it was the only one there was.

Everything that builds an API provider asks :func:`api_provider` rather than naming one,
so the launcher, ``atlas start --api``, the probe and the Settings test all agree.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from ..state.models import Role

__all__ = [
    "CHOICES",
    "LABELS",
    "any_key",
    "api_provider",
    "chosen",
    "key_for",
    "launched",
    "mark_launched",
    "role_models",
    "set_chosen",
]

CHOICES = ("claude", "gemini")
LABELS = {"claude": "Claude", "gemini": "Gemini"}
CHOICE_ENV = "ATLAS_API_PROVIDER"

#: Which provider this session's API seats were built with, or ``None`` outside API mode.
_LAUNCHED: str | None = None


def _choice_path() -> Path:
    from ..config.paths import app_data_root

    return app_data_root() / "api_provider"


def key_for(choice: str) -> str:
    """The key the named provider would use now, or ``""``."""
    if choice == "gemini":
        from .gemini_provider import gemini_key

        return gemini_key()
    from .api_provider import api_key

    return api_key()


def any_key() -> bool:
    return any(key_for(choice) for choice in CHOICES)


def chosen() -> str:
    """``"claude"`` or ``"gemini"``: which one API mode uses. See the module docstring."""
    from_env = os.environ.get(CHOICE_ENV, "").strip().lower()
    if from_env in CHOICES:
        return from_env
    with contextlib.suppress(OSError):
        saved = _choice_path().read_text(encoding="utf-8").strip().lower()
        if saved in CHOICES:
            return saved
    for choice in CHOICES:
        if key_for(choice):
            return choice
    return "claude"


def set_chosen(choice: str) -> None:
    """Remember the Settings choice. Ignored for anything but a known provider."""
    if choice not in CHOICES:
        return
    path = _choice_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(choice, encoding="utf-8")


def api_provider(choice: str | None = None, *, key: str | None = None) -> Any:
    """A provider for ``choice`` (default: :func:`chosen`), reading its key on every call
    unless one is given."""
    choice = choice or chosen()
    if choice == "gemini":
        from .gemini_provider import GeminiProvider

        return GeminiProvider(key)
    from .api_provider import ApiProvider

    return ApiProvider(key)


def role_models(choice: str | None = None) -> dict[Role, tuple[str, str]]:
    """Every role's pin for ``choice``: the model each seat will be served by."""
    choice = choice or chosen()
    if choice == "gemini":
        from .gemini_provider import gemini_role_models

        return gemini_role_models()
    from .api_provider import api_role_models

    return api_role_models()


def mark_launched(choice: str | None) -> None:
    """Record which provider this session's seats were built with (``None``: not API mode)."""
    global _LAUNCHED
    _LAUNCHED = choice


def launched() -> str | None:
    return _LAUNCHED
