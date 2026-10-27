"""Finding the real executable behind each CLI.

``asyncio.create_subprocess_exec`` cannot run a ``.cmd`` or ``.ps1`` shim, and the npm
installs of both Claude Code and Codex put exactly those on ``PATH``. The obvious
workaround -- launching through ``cmd.exe /c`` -- is rejected outright. It re-parses the
command line, so ``&``, ``^``, ``|`` and ``%VAR%`` inside a prompt become shell syntax;
since prompts contain player free-text and AI-authored nation dialogue, that is a
prompt-injection-to-command-injection path. It also orphans the real process on kill,
because terminating ``cmd.exe`` does not terminate what it spawned. It is ``shell=True``
wearing a costume, and the constraint against ``shell=True`` is about substance.

Fortunately none of that is necessary. Both shims wrap genuine native executables
(verified against the installed versions), so the resolver locates the real binary and
execs it directly:

* ``claude``  -> ``%APPDATA%\\npm\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe``
* ``codex``   -> ``...\\@openai\\codex\\node_modules\\@openai\\codex-win32-x64\\vendor\\<triple>\\bin\\codex.exe``
* ``agy``     -> already a real ``.exe``

When resolution fails the provider reports ``NOT_INSTALLED`` and its role is disabled.
There is no fallback path.

**A PATH hit is held to the same rule, for all three.** :func:`_on_path` refuses a
``.cmd``/``.bat``/``.ps1`` shim -- agy's fallback used to accept one, which put the prompt
through ``cmd.exe`` by the back door -- and refuses anything found in the current
directory. On Windows ``shutil.which`` searches the working directory *before* ``PATH``, so
an ``agy.exe`` or ``claude.exe`` dropped next to a save file or a downloaded mod would
otherwise be run in place of the real one.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ResolvedCli", "resolve", "resolve_all"]


@dataclass(frozen=True, slots=True)
class ResolvedCli:
    name: str
    executable: Path | None
    #: How it was found, shown in Diagnostics so a surprising path is visible.
    via: str = ""
    note: str = ""

    @property
    def found(self) -> bool:
        return self.executable is not None


def _npm_roots() -> list[Path]:
    roots: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "npm" / "node_modules")
    prefix = os.environ.get("NPM_CONFIG_PREFIX")
    if prefix:
        roots.append(Path(prefix) / "node_modules")
    roots.append(Path.home() / "AppData" / "Roaming" / "npm" / "node_modules")
    # Unix-style global installs, for portability.
    roots.append(Path("/usr/local/lib/node_modules"))
    roots.append(Path("/usr/lib/node_modules"))
    return roots


def _rust_target_triple() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "windows":
        return "aarch64-pc-windows-msvc" if "arm" in machine else "x86_64-pc-windows-msvc"
    if system == "darwin":
        return "aarch64-apple-darwin" if "arm" in machine else "x86_64-apple-darwin"
    return "aarch64-unknown-linux-musl" if "arm" in machine else "x86_64-unknown-linux-musl"


def _executable_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def _first_existing(candidates: list[tuple[Path, str]]) -> ResolvedCli | None:
    for path, via in candidates:
        if path.is_file():
            return ResolvedCli(name="", executable=path, via=via)
    return None


#: What a PATH hit may not be. These run through ``cmd.exe`` or PowerShell, which re-parse
#: the command line; see the module docstring for why that is not acceptable.
_SHIM_SUFFIXES = frozenset({".cmd", ".bat", ".ps1"})


def _same_place(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _on_path(stem: str, *, bare_ok: bool) -> Path | None:
    """The first real executable called ``stem`` on ``PATH``, never a shim, never a hit in
    the current directory.

    ``PATH`` is walked one absolute entry at a time, and each folder is asked about by
    handing ``shutil.which`` the *joined path* rather than the bare name. Relative entries
    (``.``, empty) and an entry that is the working directory are dropped. And a name with a
    directory part is looked up in that directory only: given a bare name, ``which`` on
    Windows searches the working directory before ``PATH`` -- even with an explicit
    ``path=`` -- unless ``NoDefaultCurrentDirectoryInExePath`` is set. ``PATHEXT`` still
    applies, so ``agy`` finds ``agy.exe``. The hit is checked against its folder anyway, so
    nothing this returns can have come from anywhere else.

    ``bare_ok`` is whether an extensionless file counts. On Windows it never does -- that
    is the npm POSIX shell script sitting next to the ``.cmd``.
    """
    try:
        here = os.getcwd()
    except OSError:  # the working directory was deleted; nothing can be "in" it
        here = None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        folder = entry.strip().strip('"')
        if not folder or not os.path.isabs(folder):
            continue
        if here is not None and _same_place(folder, here):
            continue
        hit = shutil.which(os.path.join(folder, stem))
        if not hit:
            continue
        found = Path(hit)
        if not found.is_absolute() or not _same_place(found.parent, folder):
            continue
        suffix = found.suffix.lower()
        if suffix in _SHIM_SUFFIXES or (not suffix and not bare_ok):
            continue
        return found
    return None


def _resolve_claude() -> ResolvedCli:
    name = _executable_name("claude")
    candidates: list[tuple[Path, str]] = [
        (root / "@anthropic-ai" / "claude-code" / "bin" / name, "npm package bin")
        for root in _npm_roots()
    ]
    # Native installer location, used when Claude Code is installed outside npm.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append((Path(local) / "Programs" / "claude" / name, "native install"))
    candidates.append((Path.home() / ".claude" / "local" / name, "user-local install"))

    found = _first_existing(candidates)
    if found is not None:
        return ResolvedCli("claude", found.executable, found.via)

    # A PATH hit is only usable if it is a real executable rather than a shim.
    which = _on_path("claude", bare_ok=False)
    if which is not None:
        return ResolvedCli("claude", which, "PATH")
    return ResolvedCli(
        "claude",
        None,
        "",
        note="found only a .cmd/.ps1 shim on PATH; the real executable could not be located",
    )


def _resolve_codex() -> ResolvedCli:
    name = _executable_name("codex")
    triple = _rust_target_triple()
    candidates: list[tuple[Path, str]] = []
    for root in _npm_roots():
        base = root / "@openai" / "codex"
        candidates.append(
            (
                base / "node_modules" / f"@openai/codex-{_platform_package()}" / "vendor" / triple / "bin" / name,
                "vendored native binary",
            )
        )
        candidates.append((base / "vendor" / triple / "bin" / name, "vendored native binary"))

    found = _first_existing(candidates)
    if found is not None:
        return ResolvedCli("codex", found.executable, found.via)

    which = _on_path("codex", bare_ok=False)
    if which is not None:
        return ResolvedCli("codex", which, "PATH")
    return ResolvedCli(
        "codex",
        None,
        "",
        note="found only a .cmd/.ps1 shim on PATH; the vendored native binary could not be located",
    )


def _platform_package() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()
    arch = "arm64" if "arm" in machine or "aarch64" in machine else "x64"
    if system == "windows":
        return f"win32-{arch}"
    if system == "darwin":
        return f"darwin-{arch}"
    return f"linux-{arch}"


def _resolve_agy() -> ResolvedCli:
    """agy ships a real executable, so this is a plain lookup."""
    local = os.environ.get("LOCALAPPDATA")
    candidates: list[tuple[Path, str]] = []
    if local:
        for stem in ("agy.EXE", "agy.exe", "agy"):
            candidates.append((Path(local) / "agy" / "bin" / stem, "agy install"))
    candidates.append((Path.home() / ".agy" / "bin" / _executable_name("agy"), "user install"))

    found = _first_existing(candidates)
    if found is not None:
        return ResolvedCli("antigravity", found.executable, found.via)

    # agy is a real executable wherever it is installed properly, so a shim on PATH is not
    # agy -- and running one would put the prompt through cmd.exe. Refused like the others.
    # Extensionless is allowed off Windows only, where that is what a native binary is.
    which = _on_path("agy", bare_ok=os.name != "nt")
    if which is not None:
        return ResolvedCli("antigravity", which, "PATH")
    return ResolvedCli(
        "antigravity", None, "",
        note="agy was not found (a .cmd/.bat/.ps1 shim or a copy in the current "
             "directory is not accepted)")


_RESOLVERS = {
    "claude": _resolve_claude,
    "codex": _resolve_codex,
    "antigravity": _resolve_agy,
}


def resolve(name: str) -> ResolvedCli:
    try:
        return _RESOLVERS[name]()
    except KeyError:
        raise KeyError(f"unknown provider {name!r}") from None


def resolve_all() -> dict[str, ResolvedCli]:
    return {name: resolve(name) for name in _RESOLVERS}
