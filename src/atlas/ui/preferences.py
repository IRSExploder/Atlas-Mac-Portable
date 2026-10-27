"""How the interface is set up for the person using it, and where that is kept.

Four settings, and the shape of each was decided by what the interface already had rather
than by what an accessibility checklist lists:

* **Text scale.** Every font in the program comes from one type scale and passes through
  one function, so a scale factor applied there moves all twenty-five of them together and
  nothing has to be told about it. Widgets keep asking for "the label style".
* **A palette that survives colour blindness.** The map ramp is already blue-to-amber and
  safe; the risk is the *semantic* pair -- `danger` and `good` are a red and a green, and
  they are what a delta, a meter and a warning are coloured by. The safe palette swaps the
  green for a blue of the same weight, which the two most common kinds of colour blindness
  can tell from red and the third can tell by lightness.
* **Reduced motion.** Recorded, and honest about how little there is to reduce: see
  :data:`MOTION_SURFACES`.
* **Shortcuts.** Four of them, and they are remappable because a fixed `Ctrl+Return` is a
  problem for anybody whose hands or keyboard layout disagree with it.

**Kept in the workspace, not in a config file**, because the workspace is already where the
program remembers things between runs -- the last world, the draft orders, the watchlist --
and a second store would be a second thing to find, migrate and back up.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass, field

from .theme import Theme, ThemeName

__all__ = [
    "MOTION_SURFACES",
    "SCALES",
    "SHORTCUTS",
    "Preferences",
    "colour_safe",
    "app_preferences_path",
    "load_app_preferences",
    "load_preferences",
    "save_app_preferences",
    "save_preferences",
    "scaled_size",
]

#: The offered text scales. Steps rather than a slider: the type scale is a designed set of
#: sizes with relationships between them, and a continuous factor invites a setting where
#: the heading is a hair larger than the body and the hierarchy is gone.
SCALES: tuple[tuple[float, str], ...] = (
    (0.85, "Compact"),
    (1.0, "Normal"),
    (1.15, "Large"),
    (1.35, "Larger"),
    (1.6, "Largest"),
)

#: Every action that can be re-bound, with what it does and what it is bound to by default.
#: A dict rather than a scatter of `QShortcut` constructions, so the settings screen can be
#: generated from it and a binding cannot exist that nothing offers to change.
SHORTCUTS: dict[str, tuple[str, str]] = {
    "find": ("Ctrl+F", "Find a province, a power, a headline or an agreement"),
    "dismiss": ("Escape", "Close whatever is open"),
    "send": ("Ctrl+Return", "Send the message you have written"),
    "settings": ("Ctrl+,", "Open settings"),
}

#: **What "reduced motion" actually turns off, which is very little.** Written down because
#: a preference that silently does nothing is worse than one that is honestly small: this
#: interface has no animations, no transitions and no easing curves. The only thing that
#: moves by itself is the map's own draw quality while a pan is in progress -- it drops to a
#: fast path and settles back a beat after the hand stops -- and that flicker between two
#: renderings is the one thing here a person sensitive to motion would notice.
#:
#: If an animation is ever added, its name belongs in this tuple and its `if` belongs
#: behind `Preferences.reduced_motion`.
MOTION_SURFACES: tuple[str, ...] = ("map settle",)

_KEY = "ui_preferences"


@dataclass(frozen=True, slots=True)
class Preferences:
    """Everything the interface asks the person using it, in one record."""

    #: A multiplier on every font size in the type scale. See :data:`SCALES`.
    text_scale: float = 1.0
    #: Whether to use the palette that does not rely on telling red from green.
    colour_safe: bool = False
    #: Whether to hold the map at its full rendering rather than dropping to the fast path
    #: while it is being moved. See :data:`MOTION_SURFACES`.
    reduced_motion: bool = False
    #: ``action -> key sequence``, overriding :data:`SHORTCUTS` where present.
    shortcuts: dict[str, str] = field(default_factory=dict)

    def key_for(self, action: str) -> str:
        """What ``action`` is bound to now: the person's binding, or the default."""
        bound = self.shortcuts.get(action)
        if bound:
            return bound
        default, _explain = SHORTCUTS.get(action, ("", ""))
        return default

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def scaled_size(size: float, preferences: Preferences | None) -> float:
    """One type-scale size, at the current scale. The whole of the text setting."""
    if preferences is None:
        return size
    return round(size * preferences.text_scale, 2)


#: The safe palette, as the fields it replaces. **Only the pair that carries meaning by
#: hue alone**: `good` is a green and `danger` is a red, and between them they colour every
#: delta, meter and warning in the program. Replacing the green with a blue of the same
#: weight leaves the pair distinguishable under deuteranopia and protanopia by hue, and
#: under tritanopia by lightness -- which is more than red-and-green manages under any of
#: the three.
#:
#: Nothing else moves. The map ramp is already cold-blue to warm-amber, the accent is brass,
#: and repainting a palette that is already safe would be change for its own sake.
_SAFE: dict[ThemeName, dict[str, str]] = {
    ThemeName.DARK: {
        "good": "#7FB2E2",
        "danger": "#D77254",
        "danger_hot": "#EA835C",
    },
    # Darker, because the surface underneath is paper rather than ink. The dark values
    # sit at 2.3-3.0:1 on a light panel -- a colour-blind palette that cannot be read is
    # not an accessible one, and this is the mode somebody turns on *because* they were
    # having trouble seeing it.
    ThemeName.LIGHT: {
        "good": "#1D5080",
        "danger": "#AC4628",
        "danger_hot": "#9A3914",
    },
}


def colour_safe(theme: Theme) -> Theme:
    """``theme`` with the red/green pair replaced by one that survives colour blindness."""
    return dataclasses.replace(theme, **_SAFE[theme.name])


def _from_dict(stored: object) -> Preferences | None:
    """A stored record as preferences, or ``None`` when there is nothing usable there."""
    if not isinstance(stored, dict):
        return None
    offered = {scale for scale, _label in SCALES}
    scale = stored.get("text_scale", 1.0)
    shortcuts = stored.get("shortcuts") or {}
    return Preferences(
        text_scale=float(scale) if scale in offered else 1.0,
        colour_safe=bool(stored.get("colour_safe", False)),
        reduced_motion=bool(stored.get("reduced_motion", False)),
        # Only actions that exist. A binding for an action that has been removed would sit
        # in the workspace for ever and shadow nothing.
        shortcuts={str(action): str(key) for action, key in shortcuts.items()
                   if action in SHORTCUTS and key} if isinstance(shortcuts, dict) else {},
    )


def load_preferences(conn: sqlite3.Connection) -> Preferences:
    """What this workspace remembers, else the player's own defaults, else the defaults.

    Anything unreadable is the defaults rather than an error: a preference file is not
    worth refusing to start over, and the next save writes a good one.

    **The player's defaults sit under the workspace, not over it.** They are what the main
    menu's Settings writes, before any world is open; a world that already remembers its
    own setup keeps it. A world that has never been told anything -- every new game --
    starts the way the player set the game up.
    """
    from ..state.workspace import recall

    return _from_dict(recall(conn, _KEY, None)) or load_app_preferences()


def save_preferences(conn: sqlite3.Connection, preferences: Preferences) -> bool:
    from ..state.workspace import remember

    return remember(conn, _KEY, preferences.as_dict())


def app_preferences_path():
    """The player's own defaults: one small JSON file in app data, beside the worlds."""
    from ..config.paths import app_data_root

    return app_data_root() / "preferences.json"


def load_app_preferences() -> Preferences:
    import json

    try:
        stored = json.loads(app_preferences_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Preferences()
    return _from_dict(stored) or Preferences()


def save_app_preferences(preferences: Preferences) -> bool:
    """Write the player's defaults atomically. ``False`` (never an exception) on failure."""
    import json
    import os

    path = app_preferences_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".json.tmp")
        staging.write_text(json.dumps(preferences.as_dict(), indent=2) + "\n",
                           encoding="utf-8")
        os.replace(staging, path)
    except OSError:
        return False
    return True


#: **The preferences in force, for the widgets that are not handed them.** Three shortcuts
#: live on widgets that never see the window -- the overlay base class and the message box --
#: and threading a preferences object through every constructor to reach them would put the
#: setting in twenty signatures to be read by three. Same argument as the text scale, which
#: is a module-level multiplier for the same reason and in the same spirit: a preference is
#: a property of the person using the program, not of any one widget.
_CURRENT = Preferences()


def current() -> Preferences:
    return _CURRENT


def set_current(preferences: Preferences) -> None:
    global _CURRENT
    _CURRENT = preferences


def key_for(action: str) -> str:
    """What ``action`` is bound to right now. The form a widget constructor wants."""
    return _CURRENT.key_for(action)
