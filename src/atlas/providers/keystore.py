"""Where the player's API keys are kept when they are typed into the app.

One account per provider: Anthropic's under :data:`ACCOUNT` (the default, and the only one
there was), Gemini's under its own. Every function takes the account and defaults to
Anthropic's, so the Claude path reads exactly as it always has.

**The system keychain first** -- the macOS Keychain, the Windows Credential Manager, the
Secret Service on Linux -- through ``keyring``, so the key is held the way the OS holds
every other password. Only where no keychain is available (a headless box, a stripped-down
install) does it fall back to a file in the ATLAS data folder readable by this user alone,
and the place it went is always reported, so the player knows which they got.

The environment variable :data:`~atlas.providers.api_provider.API_KEY_ENV` still wins over
anything stored here: it is the explicit, per-session choice.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

__all__ = ["forget_key", "store_key", "stored_key"]

SERVICE = "ATLAS"
ACCOUNT = "anthropic-api-key"


def _fallback_path(account: str = ACCOUNT) -> Path:
    from ..config.paths import app_data_root

    # Anthropic's file keeps the name it always had, so a key saved before Gemini existed
    # is still found.
    name = "api_key" if account == ACCOUNT else account.replace("-", "_")
    return app_data_root() / name


def _keyring():
    """The keyring module, or ``None`` when it is missing or has no usable backend."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as NoBackend
    except ImportError:
        return None
    try:
        if isinstance(keyring.get_keyring(), NoBackend):
            # A frozen app often cannot discover backends (they are found through package
            # metadata), so the platform's own store is named outright.
            if sys.platform == "darwin":
                from keyring.backends import macOS

                keyring.set_keyring(macOS.Keyring())
            elif sys.platform == "win32":
                from keyring.backends import Windows

                keyring.set_keyring(Windows.WinVaultKeyring())
            else:
                return None
    except Exception:  # noqa: BLE001 - a broken backend is the same as none
        return None
    return keyring


def stored_key(account: str = ACCOUNT) -> str:
    """The saved key, or ``""``. Never raises: a missing key is an ordinary answer."""
    ring = _keyring()
    if ring is not None:
        try:
            found = ring.get_password(SERVICE, account)
            if found:
                return found.strip()
        except Exception:  # noqa: BLE001 - fall through to the file
            pass
    try:
        return _fallback_path(account).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def store_key(key: str, account: str = ACCOUNT) -> str:
    """Save ``key``; returns where it went, in words a player can read."""
    key = key.strip()
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, account, key)
            _remove_fallback(account)
            name = type(ring.get_keyring()).__module__
            return ("your Mac's Keychain" if "macOS" in name
                    else "Windows Credential Manager" if "Windows" in name
                    else "the system keychain")
        except Exception:  # noqa: BLE001 - fall back to the file below
            pass
    path = _fallback_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Created owner-only before the key is written, so it is never readable by others.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key)
    os.chmod(path, 0o600)
    return f"a private file ({path})"


def forget_key(account: str = ACCOUNT) -> None:
    ring = _keyring()
    if ring is not None:
        with contextlib.suppress(Exception):  # nothing stored is fine
            ring.delete_password(SERVICE, account)
    _remove_fallback(account)


def _remove_fallback(account: str = ACCOUNT) -> None:
    with contextlib.suppress(OSError):
        _fallback_path(account).unlink()
