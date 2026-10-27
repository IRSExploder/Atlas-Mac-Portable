"""Taint tracking, so a secret cannot reach a nation that has no right to it.

The naive approach is to grep the finished prompt for canary strings. That catches
verbatim copying and nothing else -- and verbatim copying is the *least* likely leak. The
realistic one is derived: a computed "relative military strength" that silently
incorporates a hidden fleet, a digest sentence summarising a covert pact, a total that
only makes sense if you knew the secret. None of those contain the canary.

So the value is tainted rather than the text. A secret is wrapped at the point it leaves
the database, carrying the set of nations entitled to see it. :func:`render` is the only
thing that unwraps, and it **raises** on any tainted value the viewer does not own. A leak
therefore fails loudly at build time instead of silently reaching a model.

This is a structural guarantee, not a prompt instruction. Nothing here asks a model to
pretend it does not know something -- the information is never assembled in the first
place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

__all__ = ["LeakError", "Tainted", "render", "public"]

T = TypeVar("T")


class LeakError(RuntimeError):
    """A view tried to serialise a value its viewer is not entitled to see."""


@dataclass(frozen=True, slots=True)
class Tainted(Generic[T]):
    """A value only certain nations may observe."""

    value: T
    #: Nations entitled to see it. Empty means nobody -- engine-only knowledge.
    owners: frozenset[str]
    #: What this is, used in the error when a leak is caught.
    label: str = ""

    def visible_to(self, viewer: str | None) -> bool:
        # A ``None`` viewer is the omniscient referee: the world simulator, and the map
        # in spectator mode. It is allowed everything by construction, which is why the
        # world-sim view is built through a separate module under an import lint.
        return viewer is None or viewer in self.owners


def public(value: T) -> T:
    """Identity, for readability at call sites that deliberately expose something."""
    return value


def render(node: Any, viewer: str | None, *, path: str = "") -> Any:
    """Recursively unwrap tainted values, refusing anything the viewer cannot see.

    This is the single unwrapping point in the codebase. Anything that serialises a view
    goes through here, so there is exactly one place where the asymmetry guarantee can be
    broken -- and it raises rather than dropping the field, because a silently missing
    number produces a subtly wrong world rather than an obvious bug.
    """
    if isinstance(node, Tainted):
        if not node.visible_to(viewer):
            raise LeakError(
                f"{path or 'value'}: {node.label or 'a secret'} is visible only to "
                f"{sorted(node.owners) or 'the engine'}, but the view is being built for "
                f"{viewer!r}"
            )
        return render(node.value, viewer, path=path)

    if isinstance(node, dict):
        return {key: render(value, viewer, path=f"{path}.{key}" if path else str(key))
                for key, value in node.items()}

    if isinstance(node, (list, tuple)):
        return type(node)(
            render(item, viewer, path=f"{path}[{index}]") for index, item in enumerate(node)
        )

    return node


def scrub(node: Any, viewer: str | None) -> Any:
    """Like :func:`render`, but drops what the viewer cannot see instead of raising.

    Used only where a partial view is genuinely correct -- an event feed, where a nation
    legitimately sees fewer entries than another. Never used for a nation's own attribute
    block, where a missing field would read as a zero rather than as an absence.
    """
    if isinstance(node, Tainted):
        return node.value if node.visible_to(viewer) else None

    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            cleaned = scrub(value, viewer)
            if cleaned is not None or not isinstance(value, Tainted):
                out[key] = cleaned
        return out

    if isinstance(node, (list, tuple)):
        cleaned_items = [scrub(item, viewer) for item in node]
        return type(node)(item for item in cleaned_items if item is not None)

    return node
