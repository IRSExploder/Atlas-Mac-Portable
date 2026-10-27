"""The menu that appears when you ask a province what can be done to it.

A thin presentation of :func:`atlas.engine.mapactions.actions_for_region`, which is itself
the orders workbench asked about one region. Nothing here decides what is possible; it draws
what the engine answered, and hands the chosen action back as an ordinary order.

**A refused line is shown greyed and says why**, because the alternative is a menu that
teaches nothing: a player who never sees *"an air strike is only open against somebody you
are at war with"* learns the rule by having orders bounce, three turns later, with the jump
already spent. The engine's own alternative comes with it where there is one.
"""

from __future__ import annotations

import contextlib
import sqlite3

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from ..engine.mapactions import MapAction, actions_for_region
from .theme import Theme

__all__ = ["RegionMenu"]


def _qss(theme: Theme) -> str:
    return (
        f"QMenu {{ background: {theme.panel}; border: 1px solid {theme.rule};"
        f" padding: 6px; color: {theme.ink_body}; }}"
        f"QMenu::item {{ padding: 7px 16px; }}"
        f"QMenu::item:selected {{ background: {theme.raised};"
        f" color: {theme.ink_bright}; }}"
        f"QMenu::item:disabled {{ color: {theme.ink_faint}; }}"
        f"QMenu::separator {{ height: 1px; background: {theme.rule_soft};"
        f" margin: 5px 8px; }}"
    )


class RegionMenu(QWidget):
    """Builds and shows the context menu for one province."""

    #: An action the player chose. The window appends it to the pending orders.
    action_chosen = Signal(object)
    #: An action that still needs a second province, and the one it already has.
    second_click_needed = Signal(object)
    #: The player wants to read the air situation over this province rather than act on it.
    #: **Above the actions and separated from them**, because it is the only line on this
    #: menu that is not an order: everything below it joins the pending orders and this
    #: opens a panel. A reading and an act in one undifferentiated list is how a player
    #: ends up spending a turn to find something out.
    air_requested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._menu: QMenu | None = None

    def build(self, conn: sqlite3.Connection, world_id: str, nation_id: str | None,
              region_id: str, *, parent: QWidget | None = None) -> QMenu:
        """The menu for this province, ready to be popped up or grabbed.

        **The last one goes as this one is made.** Each menu is parented to the window so
        it is styled and placed with it, and a parented menu lives as long as its parent:
        every right-click used to leave one behind, with its actions and their closures
        over the database -- 21 menus and 187 actions after twenty clicks. Deferred rather
        than immediate, because the previous menu may be the one whose action is still
        being delivered when the next right-click arrives.
        """
        self._let_go()
        menu = QMenu(parent)
        menu.setStyleSheet(_qss(self._theme))
        name = conn.execute(
            "SELECT name FROM region WHERE world_id = ? AND id = ?",
            (world_id, region_id)).fetchone()

        heading = QAction(str(name["name"]) if name else region_id, menu)
        heading.setEnabled(False)
        menu.addAction(heading)
        menu.addSeparator()

        # The one reading on a menu of acts. Offered for every province, including ones
        # nothing can be done to: *who holds the sky over this* is a question worth asking
        # about ground you will never attack, and the answer is what decides whether the
        # ground you will attack is worth attacking.
        air = QAction("Air situation over here", menu)
        air.setToolTip("Who holds the sky, what it is doing, and what can be done about it")
        air.triggered.connect(
            lambda _checked=False, r=region_id: self.air_requested.emit(r))
        menu.addAction(air)
        menu.addSeparator()

        found = actions_for_region(conn, world_id, nation_id, region_id)
        if not found:
            nothing = QAction("nothing you can do here", menu)
            nothing.setEnabled(False)
            menu.addAction(nothing)
            self._menu = menu
            return menu

        separated = False
        for entry in found:
            if not entry.enabled and not separated:
                menu.addSeparator()
                separated = True
            menu.addAction(self._entry(entry, menu))
        self._menu = menu
        return menu

    def _entry(self, entry: MapAction, menu: QMenu) -> QAction:
        label = entry.label
        if entry.needs:
            label += "   (then click where from)"
        elif not entry.enabled:
            # The reason on the line itself rather than in a tooltip: a menu you have to
            # hover to understand is one nobody reads.
            label += f"   — {entry.why_not}"
        action = QAction(label, menu)
        action.setEnabled(entry.enabled)
        if entry.instead:
            action.setToolTip(entry.instead)
        action.triggered.connect(lambda _checked=False, e=entry: self._chose(e))
        return action

    def _chose(self, entry: MapAction) -> None:
        if entry.needs:
            self.second_click_needed.emit(entry)
        else:
            self.action_chosen.emit(entry)

    def _let_go(self) -> None:
        previous, self._menu = self._menu, None
        if previous is not None:
            with contextlib.suppress(RuntimeError):  # already gone with its parent
                previous.deleteLater()

    def popup_at(self, menu: QMenu, where: QPoint) -> None:
        menu.popup(where)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        if self._menu is not None:
            try:
                self._menu.setStyleSheet(_qss(theme))
            except RuntimeError:  # its parent took it down first
                self._menu = None
