"""Forking a line, and seeing the lines you have.

Rewind is destructive by design: it puts the world back and the turns you undid are gone.
That is the right default for a mistake and the wrong one for a question. *What if I had not
signed that treaty* should not cost you the game you were playing, and until now the only
way to ask it was to lose the answer either way.

A fork copies the world to its own directory and rewinds the copy. The original is opened
for reading and never written, which is what makes it safe -- and the copy is the original
as it stood, by fingerprint, because lineage is recorded outside the digest.

**v1 creates and lists. It does not switch.** Loading a branch into the running window means
rebuilding the window around a different connection and engine, which is a lifecycle change
rather than a panel, and doing it badly would mean a half-swapped window holding one world's
map and another's turn engine. The list says where each branch lives so it can be opened the
way any world is opened; switching in place is flagged.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..state.branch import Branch, branches, fork_world
from ..state.chronicle import tree
from ..state.problems import WORLD_ERRORS, describe
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import Chip, Divider, SectionLabel, TextLabel, clear_layout, restyle

__all__ = ["BranchOverlay"]

#: What a branch directory may be called. Anything else is a path waiting to be a bug.
_SAFE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("-", text.strip().lower()).strip("-")


def _scrolled(host: QWidget, *, height: int) -> QScrollArea:
    """A panel section that grows past its room without squeezing what is in it."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setMinimumHeight(height)
    # Transparent down to the viewport, not just on the frame. A scroll area paints its own
    # viewport in the palette's base colour, which is how a section came to keep the dark
    # theme's ground after the panel around it had gone light.
    area.setStyleSheet(
        "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
    area.viewport().setAutoFillBackground(False)
    area.setWidget(host)
    # **After `setWidget`, which turns it back on.** Qt fills a scrolled widget with the
    # palette's window colour, and the palette is the desktop's rather than the theme's --
    # so a section that should have shown the panel through it came out as a grey slab in
    # one theme and a dark one in the other. Setting this before `setWidget` is silently
    # undone, which is how it survived a first fix.
    host.setAutoFillBackground(False)
    return area


class _BranchRow(QFrame):
    """One line: what it is, where it came from, and where it lives."""

    #: This row was picked, or unpicked, for a comparison.
    picked = Signal(str, bool)

    def __init__(self, branch: Branch, *, current: bool, theme: Theme, depth: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._current = current
        self.branch = branch
        self.setObjectName("branchRow")

        layout = QVBoxLayout(self)
        # **Indented by lineage.** The flat list showed six lines off one afternoon as six
        # unrelated worlds; which came from which was stored all along and shown as a
        # sentence per row. Indentation is the whole of the tree -- the parent each world
        # names is already in `Branch.forked_from`.
        layout.setContentsMargins(13 + depth * 22, 10, 13, 11)
        layout.setSpacing(3)
        self._kids: list[TextLabel] = []

        head = QHBoxLayout()
        head.setSpacing(10)
        self._name = TextLabel(branch.name, style="label", colour="ink_bright", theme=theme)
        head.addWidget(self._name)
        head.addStretch(1)
        self._where = TextLabel(
            f"turn {branch.turn}   {branch.date}", style="mono_small",
            colour="ink_body", theme=theme)
        head.addWidget(self._where)
        self.pick = Chip("compare", theme=theme)
        self.pick.clicked.connect(self._toggle)
        head.addWidget(self.pick)
        layout.addLayout(head)
        self._kids.extend([self._name, self._where])

        origin = (f"forked from {branch.forked_from} at turn {branch.forked_at}"
                  if branch.is_fork else "an original line")
        if current:
            origin += "   ·   the world you are playing"
        self._origin = TextLabel(origin, style="mono_small", colour="ink_ghost",
                                 theme=theme, wrap=True)
        layout.addWidget(self._origin)
        self._kids.append(self._origin)

        self._path = TextLabel(str(branch.path), style="mono_small", colour="ink_ghost",
                               theme=theme, wrap=True)
        layout.addWidget(self._path)
        self._kids.append(self._path)

    def _toggle(self) -> None:
        self.set_picked(not self.pick.selected)
        self.picked.emit(self.branch.name, self.pick.selected)

    def set_picked(self, picked: bool) -> None:
        self.pick.set_selected(picked)
        self.pick.set_text("comparing" if picked else "compare")

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = theme.brass_line if self._current else theme.rule_soft
        self.setStyleSheet(
            f"QFrame#branchRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in self._kids:
            child.apply_theme(theme)
        self.pick.apply_theme(theme)


class BranchOverlay(Overlay):
    """Fork this world at a turn, and see the lines that exist."""

    #: ``(turn, name)`` -- the window does the forking, since it owns the connection.
    fork_requested = Signal(int, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._turn = 0
        self._world = ""
        self._viewer = ""
        self._turn_chips: dict[int, Chip] = {}
        self._rows: list[_BranchRow] = []
        self._picked: list[str] = []
        self._verdict: list[TextLabel] = []
        self._problem = ""

        self.body.addWidget(SectionLabel("Branches", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._blurb = TextLabel(
            "A fork copies this world and puts the copy back to the turn you choose. The "
            "line you are playing is left exactly as it is -- rewind changes your world, "
            "this one does not.",
            style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._blurb)
        self.body.addSpacing(SPACE.lg)

        self._pick_title = TextLabel("Fork from turn", style="label", colour="ink_faint",
                                     theme=theme)
        self.body.addWidget(self._pick_title)
        self.body.addSpacing(SPACE.sm)
        self._picker = QHBoxLayout()
        self._picker.setSpacing(6)
        picker_host = QWidget()
        picker_host.setLayout(self._picker)
        self.body.addWidget(picker_host)
        self.body.addSpacing(SPACE.md)

        self._fork = Chip("Fork from this turn", theme=theme, selected=True,
                          padding=(20, 9))
        self._fork.clicked.connect(self._request)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._fork)
        row.addStretch(1)
        host = QWidget()
        host.setLayout(row)
        self.body.addWidget(host)

        self._note = TextLabel("", style="mono_small", colour="danger", theme=theme,
                               wrap=True)
        self.body.addSpacing(SPACE.sm)
        self.body.addWidget(self._note)

        self.body.addSpacing(SPACE.lg)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.md)
        self._list_title = TextLabel("The lines that exist", style="label",
                                     colour="ink_faint", theme=theme)
        self.body.addWidget(self._list_title)
        self.body.addSpacing(SPACE.sm)
        self._list = QVBoxLayout()
        self._list.setSpacing(7)
        list_host = QWidget()
        list_host.setLayout(self._list)
        self.body.addWidget(_scrolled(list_host, height=210))

        self.body.addSpacing(SPACE.lg)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.md)
        self._compare_title = TextLabel("What that decision cost", style="label",
                                        colour="ink_faint", theme=theme)
        self.body.addWidget(self._compare_title)
        self.body.addSpacing(SPACE.sm)
        self._compare_hint = TextLabel(
            "Mark two lines above to hold them against each other. They are read on the "
            "last day they both reached -- comparing turn 30 against turn 25 would call "
            "the gap a consequence. Neither save is touched.",
            style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._compare_hint)
        self.body.addSpacing(SPACE.sm)
        self._compare = QVBoxLayout()
        self._compare.setSpacing(6)
        compare_host = QWidget()
        compare_host.setLayout(self._compare)
        # **Scrolled, and that is not cosmetic.** Nine headings and two lines each do not
        # fit under a list of branches in a fixed panel, and a layout asked for more height
        # than it has does not clip -- it compresses every label below its own minimum and
        # draws each heading through the answer above it.
        self.body.addWidget(_scrolled(compare_host, height=330), 1)

        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel(
            "Open a branch the way you open any world — each line is a save of its "
            "own.", style="mono_small", colour="ink_ghost", theme=theme,
            wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str,
             root: Path | None = None) -> None:
        """Read the turns this world can be forked at, and the lines that already exist."""
        self._world = world_id
        row = conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
        now = int(row["turn_number"]) if row else 0
        self._turn = max(0, now)
        self._problem = ""
        self._note.setText("")
        seat = conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_player = 1", (world_id,)
        ).fetchone()
        # Whose campaign a comparison is read as. Without a seat the headline still has
        # answers -- it just reports who leads rather than where you stand.
        self._viewer = str(seat[0]) if seat else ""

        clear_layout(self._picker)
        self._turn_chips.clear()
        # The last few turns, newest first: a fork is nearly always "just before that".
        for turn in range(now, max(-1, now - 6), -1):
            chip = Chip(f"turn {turn}", theme=self._theme, selected=(turn == self._turn))
            chip.clicked.connect(lambda t=turn: self._choose(t))
            self._turn_chips[turn] = chip
            self._picker.addWidget(chip)
        self._picker.addStretch(1)

        self.show_branches(root, current=world_id)
        self._show_comparison()
        self.apply_theme(self._theme)

    def show_branches(self, root: Path | None = None, *, current: str = "") -> None:
        clear_layout(self._list)
        self._rows.clear()
        found = branches(root)
        if not found:
            label = TextLabel(
                "No world directories were found. A world played in memory has nowhere to "
                "fork to; save it first.",
                style="body", colour="ink_ghost", theme=self._theme, wrap=True)
            self._list.addWidget(label)
            return
        def add(node, depth: int) -> None:
            branch = node.branch
            row = _BranchRow(branch, current=(branch.path.parent.name == current
                                              or branch.world_id == current
                                              and not branch.is_fork),
                             theme=self._theme, depth=depth)
            row.picked.connect(self._pick)
            row.set_picked(branch.name in self._picked)
            self._rows.append(row)
            self._list.addWidget(row)
            for child in node.children:
                add(child, depth + 1)

        for node in tree(root):
            add(node, 0)
        self._list.addStretch(1)

    # -- comparing two lines --------------------------------------------------

    def _pick(self, name: str, picked: bool) -> None:
        """Mark a line for comparison, keeping at most the last two.

        A third pick drops the oldest rather than refusing: a player correcting a mis-click
        should not have to find and unmark the row they no longer want.
        """
        if picked:
            self._picked = [*[n for n in self._picked if n != name], name][-2:]
        else:
            self._picked = [n for n in self._picked if n != name]
        for row in self._rows:
            row.set_picked(row.branch.name in self._picked)
        self._show_comparison()

    @property
    def picked(self) -> list[str]:
        return list(self._picked)

    def _show_comparison(self) -> None:
        """Read both lines and lay the nine headings out, or say what is still needed.

        **Read on this thread, deliberately.** The work is a file copy and a rewind of the
        copy, which is seconds at worst, and a screen that answered a question asynchronously
        would have to explain a half-drawn table. If a world large enough to stall this ever
        shows up, that is the moment to move it, not before.
        """
        from ..state.compare import compare

        clear_layout(self._compare)
        self._verdict.clear()
        if len(self._picked) < 2:
            self._say("Mark one more line." if self._picked else "No lines marked.",
                      colour="ink_ghost")
            self.apply_theme(self._theme)
            return

        by_name = {row.branch.name: row.branch for row in self._rows}
        # A marked line can stop existing between one look at the list and the next -- a
        # directory moved or deleted outside the game, and the panel reloads around it.
        # Forgotten rather than crashed on, and the message goes back to asking for two.
        self._picked = [name for name in self._picked if name in by_name]
        if len(self._picked) < 2:
            self._say("Mark one more line." if self._picked else "No lines marked.",
                      colour="ink_ghost")
            self.apply_theme(self._theme)
            return
        left, right = (by_name[name] for name in self._picked)
        result = compare(left, right, viewer=self._viewer)
        if result.refused:
            self._say(f"No comparison: {result.refused}", colour="danger")
            self.apply_theme(self._theme)
            return

        self._say(f"{left.name} against {right.name}, on {result.on}", colour="ink_bright")
        if result.forked_at:
            self._say(result.forked_at, colour="ink_ghost")
        if not result.differences:
            self._say("These two lines are the same in every way this screen looks at.",
                      colour="ink_dim")
        for heading in result.headings:
            # Differences first-class, matches muted: a comparison screen exists to pick
            # out what moved, and a wall of identical rows in the same ink hides it.
            body = "ink_body" if heading.differs else "ink_ghost"
            self._compare.addSpacing(8)
            self._say(heading.what, colour="brass" if heading.differs else "ink_faint")
            self._say(f"{left.name}:  {heading.left}", colour=body, mono=True, indent=True)
            self._say(f"{right.name}:  {heading.right}", colour=body, mono=True, indent=True)
        self._compare.addStretch(1)
        self.apply_theme(self._theme)

    def _say(self, text: str, *, colour: str, mono: bool = False,
             indent: bool = False) -> None:
        label = TextLabel(text, style="mono_small" if mono else "body", colour=colour,
                          theme=self._theme, wrap=True)
        label.set_colour(colour, self._theme)
        # A wrapped label reports the height of one line unless it is told the width it
        # will be wrapped at, and a layout that believes a three-line answer is one line
        # tall draws the next heading through it.
        label.setMinimumWidth(1)
        if indent:
            label.setContentsMargins(22, 0, 0, 0)
        self._verdict.append(label)
        self._compare.addWidget(label)

    @property
    def chosen_turn(self) -> int:
        return self._turn

    def _choose(self, turn: int) -> None:
        self._turn = turn
        for value, chip in self._turn_chips.items():
            chip.set_selected(value == turn)

    def _request(self) -> None:
        self.fork_requested.emit(self._turn, self.suggested_name())

    def suggested_name(self) -> str:
        """A name that cannot collide with a live one and cannot be a path.

        Derived rather than asked for: v1 does not have a text field, and a fork that
        refused because the player typed a slash would be a worse first experience than one
        that names itself.
        """
        base = _slug(f"{self._world}-t{self._turn}") or "branch"
        existing = {branch.name for branch in branches()}
        if base not in existing:
            return base
        for suffix in range(2, 99):
            candidate = f"{base}-{suffix}"
            if candidate not in existing:
                return candidate
        return f"{base}-many"

    def report(self, message: str, *, problem: bool = False,
               root: Path | None = None, current: str = "") -> None:
        """Say what the fork did, and refresh the list around it."""
        self._problem = message if problem else ""
        self._note.setText(message)
        self._note.set_colour("danger" if problem else "good", self._theme)
        self.show_branches(root, current=current or self._world)
        self.apply_theme(self._theme)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        for chip in (*self._turn_chips.values(), self._fork):
            chip.apply_theme(theme)
        for label in (self._blurb, self._pick_title, self._list_title, self._note,
                      self._hint, self._compare_title, self._compare_hint,
                      *self._verdict):
            label.apply_theme(theme)
        for row in self._rows:
            row.apply_theme(theme)
        restyle(self.panel, theme)
        for row in self._rows:
            row.apply_theme(theme)
        self._note.set_colour("danger" if self._problem else "good", theme)


def fork_here(conn: sqlite3.Connection, world_id: str, turn: int, name: str) -> str:
    """Do the fork and say what happened, in one sentence a panel can show.

    Kept beside the screen rather than inside it so the window has one call to make and the
    refusals -- a name already taken, a turn the history cannot reach -- come back as text
    rather than as an exception the UI would have to know how to phrase.
    """
    try:
        branch = fork_world(conn, world_id, at_turn=turn, name=name)
    except FileExistsError:
        return f"There is already a line called {name!r}."
    except WORLD_ERRORS as exc:
        return describe(exc, what="The new line")
    except (ValueError, OSError) as exc:
        return f"That fork could not be made: {exc}"
    return (f"Forked at turn {branch.forked_at} — {branch.name} is now its own line, "
            f"starting {branch.date}.")
