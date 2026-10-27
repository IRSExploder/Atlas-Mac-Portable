"""Cloning a scenario, changing it, and being told whether it still holds together.

The substrate underneath this panel (:mod:`atlas.scenarios.workshop`) is a base scenario
plus an overlay of data edits, and it has been able to clone, apply, audit and play since it
landed. What it did not have was a way in that was not a JSON file, which meant the one item
of the phase that is genuinely *authoring* rather than surfacing was reachable only by
somebody who already knew where the overlay lived.

**The auditor is the screen.** Anyone can write a scenario file; a text editor will happily
save one that builds a world with a one-sided war in it. What this panel adds is the answer
to *does it hold together* -- the engine's own seed-phase invariants, the same ones the
release audit runs against the shipped eras, plus building the world twice to prove it
reproduces. The verdict is stated in the player's words and its findings are shown verbatim,
because "something is wrong" is not worth the round trip.

**One hundred and ten relations, and no wall of rows.** The list opens on what is worth
looking at -- anything already edited, anything at war, anything strongly held either way --
and a search box finds a pair by nation. Relations are directed, so each direction is a row
of its own; setting one side to war and not the other is a mistake a real player will make,
and the auditor catches it by name rather than the panel quietly fixing it behind them. A
tool that silently corrects you teaches nothing about the world it is editing.

**The ceiling is drawn rather than hidden.** Powers, sovereignty and treaties are listed as
not-yet-editable with the reason attached, from :data:`atlas.scenarios.workshop.NOT_EDITABLE`.
Absent controls with no explanation read as a broken screen rather than a considered one.

**Play is a real button and it costs no lifecycle change**, because this panel lives on the
setup screen: an audited clone is an ordinary preset, so playing it means selecting it in the
list that was already there and pressing the button that was already there.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.nuclear import DELIVERY_TIERS
from ..scenarios.presets import list_presets, load_preset
from ..scenarios.registry import builtin_scenario_for
from ..scenarios.seeder import Scenario
from ..scenarios.workshop import (
    NOT_EDITABLE,
    Verdict,
    check,
    clone_scenario,
    load_overlay,
    save_overlay,
)

#: The edit set itself. Aliased because this module already has an ``Overlay``, and the
#: two mean different things: one is a sheet over the window, the other is a list of edits.
from ..scenarios.workshop import Overlay as Edits
from .overlays import Overlay
from .theme import PANEL, SPACE, TYPE, Theme
from .widgets import (
    Chip,
    Divider,
    SectionLabel,
    TextLabel,
    clear_layout,
    scroll_qss,
    styled_font,
)

__all__ = ["WorkshopOverlay"]

_SAFE = re.compile(r"[^a-z0-9-]+")

#: How many relation rows to draw at once. The list is a way in, not a table of everything:
#: 110 rows is a document, and nobody edits a document by scrolling it.
_SHOWN = 8

#: What is worth showing unasked. A relation at war or strongly held either way is the one
#: somebody opened this screen to change; everything else is found by searching for it.
_NOTABLE = 60.0


def _slug(text: str) -> str:
    return _SAFE.sub("-", text.strip().lower()).strip("-")


def _field_qss(theme: Theme) -> str:
    return (
        f"QLineEdit {{ background: {theme.inset}; border: 1px solid {theme.edge};"
        f" color: {theme.ink}; padding: 5px 8px;"
        f" selection-background-color: {theme.brass_line}; }}"
        f"QLineEdit:focus {{ border-color: {theme.brass_line}; }}"
        f"QLineEdit:disabled {{ color: {theme.ink_faint}; }}"
    )


class _Number(QLineEdit):
    """A small numeric box, deliberately wider than the legal range.

    A relation score the schema accepts runs from -100 to 100, and this will let you type
    900 -- because the auditor is what says so, and being told by the auditor is what this
    screen is for. A widget that silently swallowed the keystroke would teach nothing, and
    one that carried the bound itself would be the second place that bound is written down.
    """

    def __init__(self, value: str, *, theme: Theme, width: int = 74,
                 low: int = -999, high: int = 999, parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self.setValidator(QIntValidator(low, high, self))
        self.setFixedWidth(width)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setFont(styled_font(TYPE["mono"]))
        self.setStyleSheet(_field_qss(theme))

    def number(self, fallback: float = 0.0) -> float:
        try:
            return float(self.text().strip())
        except ValueError:
            return fallback


class _RelationRow(QFrame):
    """One direction of one pair: what A makes of B, and whether A is at war with B."""

    def __init__(self, a: str, b: str, label: str, score: float, at_war: bool,
                 *, edited: bool, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.a, self.b = a, b
        self._theme = theme
        self._edited = edited
        self.setObjectName("editRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(11)
        self._label = TextLabel(label, style="body", colour="ink_bright", theme=theme)
        layout.addWidget(self._label, 1)

        self._mark = TextLabel("edited" if edited else "", style="mono_small",
                               colour="brass", theme=theme)
        layout.addWidget(self._mark)
        self._caption = TextLabel("regard", style="mono_small", colour="ink_ghost",
                                  theme=theme)
        layout.addWidget(self._caption)

        self._score = _Number(str(int(score)), theme=theme)
        layout.addWidget(self._score)
        self._war = Chip("at war" if at_war else "at peace", theme=theme,
                         selected=at_war, padding=(11, 4))
        self._war.clicked.connect(self._toggle)
        layout.addWidget(self._war)

    def _toggle(self) -> None:
        at_war = not self._war.selected
        self._war.set_selected(at_war)
        self._war.set_text("at war" if at_war else "at peace")

    def values(self) -> tuple[float, bool]:
        return self._score.number(), self._war.selected

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = theme.brass_line if self._edited else theme.rule_soft
        self.setStyleSheet(
            f"QFrame#editRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in (self._label, self._mark, self._caption):
            child.apply_theme(theme)
        self._score.apply_theme(theme)
        self._war.apply_theme(theme)


class _ArsenalRow(QFrame):
    """One armed power: how many warheads, what carries them, how many boats are out."""

    def __init__(self, power: str, label: str, warheads: int, delivery: str,
                 boats: int | None, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.power = power
        self._theme = theme
        self._delivery = delivery
        self.setObjectName("editRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 9)
        layout.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(11)
        self._label = TextLabel(label, style="body", colour="ink_bright", theme=theme)
        head.addWidget(self._label, 1)
        self._warheads_caption = TextLabel("warheads", style="mono_small",
                                           colour="ink_ghost", theme=theme)
        head.addWidget(self._warheads_caption)
        self._warheads = _Number(str(int(warheads)), theme=theme, low=-999, high=99999)
        head.addWidget(self._warheads)
        self._boats_caption = TextLabel("boats at sea", style="mono_small",
                                        colour="ink_ghost", theme=theme)
        head.addWidget(self._boats_caption)
        self._boats = _Number("" if boats is None else str(int(boats)), theme=theme,
                              width=58, low=-99, high=999)
        # A power the era gave no submarine count keeps that silence: the column means "the
        # scenario did not say, so every boat counts", which is not a number to invent here.
        self._boats.setEnabled(boats is not None)
        if boats is None:
            self._boats.setPlaceholderText("—")
        head.addWidget(self._boats)
        layout.addLayout(head)

        tiers = QHBoxLayout()
        tiers.setSpacing(5)
        self._tier_chips: dict[str, Chip] = {}
        for tier, spec in DELIVERY_TIERS.items():
            chip = Chip(tier, theme=theme, selected=(tier == delivery), padding=(10, 3))
            chip.setToolTip(spec.blurb)
            chip.clicked.connect(lambda name=tier: self._pick(name))
            self._tier_chips[tier] = chip
            tiers.addWidget(chip)
        tiers.addStretch(1)
        holder = QWidget()
        holder.setLayout(tiers)
        layout.addWidget(holder)

    def _pick(self, tier: str) -> None:
        self._delivery = tier
        for name, chip in self._tier_chips.items():
            chip.set_selected(name == tier)

    def values(self) -> tuple[int, str, int | None]:
        boats = None
        if self._boats.isEnabled() and self._boats.text().strip():
            boats = int(self._boats.number())
        return int(self._warheads.number()), self._delivery, boats

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#editRow {{ background: {theme.inset};"
            f" border-left: 3px solid {theme.rule_soft}; }}")
        for child in (self._label, self._warheads_caption, self._boats_caption):
            child.apply_theme(theme)
        for box in (self._warheads, self._boats):
            box.apply_theme(theme)
        for chip in self._tier_chips.values():
            chip.apply_theme(theme)


class WorkshopOverlay(Overlay):
    """Pick a base, clone it, change what is safe to change, audit it, play it."""

    #: A preset id that has been cloned, edited, and audited coherent. The setup screen
    #: selects it; the ordinary Create world button does everything after that.
    play_requested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.screen)

        self._base_id = ""
        self._base: Scenario | None = None
        self._preset_id = ""
        self._edits: Edits | None = None
        self._verdict: Verdict | None = None
        self._query = ""
        self._playable = False
        self._verdict_edge = "rule"
        self._base_chips: dict[str, Chip] = {}
        self._clone_chips: dict[str, Chip] = {}
        self._relation_rows: list[_RelationRow] = []
        self._arsenal_rows: list[_ArsenalRow] = []
        self._find: QLineEdit | None = None
        self._seed: _Number | None = None
        #: Three buckets, because two of them are destroyed and rebuilt. A label whose C++
        #: side has been deleted is a crash the next time the theme changes.
        self._kids: list[TextLabel] = []
        self._editor_kids: list[TextLabel] = []
        self._relation_kids: list[TextLabel] = []
        self._problem_kids: list[TextLabel] = []

        self.body.addWidget(SectionLabel("Scenario workshop", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self.body.addWidget(self._text(
            "A clone is a copy of a shipped scenario plus the edits you make to it, and the "
            "original is only ever read. What you change is put to the same tests before "
            "you can play it — the ones every shipped era is held to.",
            style="body", colour="ink_dim"))
        self.body.addSpacing(SPACE.lg)

        self.body.addWidget(self._build_source())
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(self._build_editor(), 1)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(self._build_verdict())
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(self._build_controls())

    # -- construction ------------------------------------------------------

    def _text(self, text: str, *, style: str, colour: str,
              bucket: list[TextLabel] | None = None) -> TextLabel:
        label = TextLabel(text, style=style, colour=colour, theme=self._theme, wrap=True)
        (self._kids if bucket is None else bucket).append(label)
        return label

    def _build_source(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._text("Start from", style="label", colour="ink_faint"))
        self._bases = QHBoxLayout()
        self._bases.setSpacing(6)
        bases = QWidget()
        bases.setLayout(self._bases)
        layout.addWidget(bases)

        self._reopen_label = self._text("Or reopen one of yours", style="label",
                                        colour="ink_faint")
        layout.addWidget(self._reopen_label)
        self._clones = QHBoxLayout()
        self._clones.setSpacing(6)
        self._clones_host = QWidget()
        self._clones_host.setLayout(self._clones)
        layout.addWidget(self._clones_host)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._name = QLineEdit()
        self._name.setFixedWidth(320)
        self._name.setPlaceholderText("a name for your copy")
        row.addWidget(self._name)
        self._clone_button = Chip("Clone it", theme=self._theme, selected=True,
                                  padding=(15, 6))
        self._clone_button.clicked.connect(self._clone)
        row.addWidget(self._clone_button)
        self._working_on = self._text("", style="mono_small", colour="ink_ghost")
        row.addWidget(self._working_on, 1)
        holder_row = QWidget()
        holder_row.setLayout(row)
        layout.addWidget(holder_row)
        return holder

    def _build_editor(self) -> QWidget:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(400)
        inner = QWidget()
        self._editor = QVBoxLayout(inner)
        self._editor.setContentsMargins(0, 0, 12, 0)
        self._editor.setSpacing(0)
        self._scroll.setWidget(inner)
        return self._scroll

    def _build_verdict(self) -> QWidget:
        self._verdict_box = QFrame()
        self._verdict_box.setObjectName("verdictBox")
        layout = QVBoxLayout(self._verdict_box)
        layout.setContentsMargins(15, 12, 15, 13)
        layout.setSpacing(6)
        self._verdict_head = self._text("Not audited yet", style="modal",
                                        colour="ink_bright")
        layout.addWidget(self._verdict_head)
        self._verdict_line = self._text("", style="body", colour="ink_dim")
        layout.addWidget(self._verdict_line)
        self._problems = QVBoxLayout()
        self._problems.setSpacing(4)
        self._problems_host = QWidget()
        self._problems_host.setLayout(self._problems)
        self._problems_host.setVisible(False)
        layout.addWidget(self._problems_host)
        self._verdict_mono = self._text("", style="mono_small", colour="ink_ghost")
        layout.addWidget(self._verdict_mono)
        return self._verdict_box

    def _build_controls(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._note = self._text("", style="mono_small", colour="ink_ghost")
        row.addWidget(self._note, 1)
        self._close = Chip("Close", theme=self._theme, padding=(13, 6))
        self._close.clicked.connect(self.close_overlay)
        row.addWidget(self._close)
        self._audit = Chip("Save and audit", theme=self._theme, padding=(14, 6))
        self._audit.clicked.connect(self.run_audit)
        row.addWidget(self._audit)
        self._play = Chip("Play this scenario", theme=self._theme, padding=(15, 6))
        self._play.clicked.connect(self._play_clicked)
        row.addWidget(self._play)
        return holder

    # -- what exists on this machine ---------------------------------------

    def load(self) -> None:
        """Read the scenarios installed here. Opens on the first shipped one."""
        presets = list(list_presets())
        clear_layout(self._bases)
        self._base_chips.clear()
        for preset in (entry for entry in presets if entry.builtin):
            chip = Chip(preset.name, theme=self._theme, padding=(12, 5))
            chip.clicked.connect(lambda pid=preset.id: self.pick_base(pid))
            self._base_chips[preset.id] = chip
            self._bases.addWidget(chip)
        self._bases.addStretch(1)

        clear_layout(self._clones)
        self._clone_chips.clear()
        mine = [preset for preset in presets if not preset.builtin]
        for preset in mine:
            chip = Chip(preset.name, theme=self._theme, padding=(12, 5))
            chip.clicked.connect(lambda pid=preset.id: self.open_clone(pid))
            self._clone_chips[preset.id] = chip
            self._clones.addWidget(chip)
        self._clones.addStretch(1)
        self._reopen_label.setVisible(bool(mine))
        self._clones_host.setVisible(bool(mine))

        if self._preset_id:
            for pid, chip in self._base_chips.items():
                chip.set_selected(pid == self._base_id)
            for pid, chip in self._clone_chips.items():
                chip.set_selected(pid == self._preset_id)
        elif self._base_chips:
            self.pick_base(self._base_id or next(iter(self._base_chips)))
        self.apply_theme(self._theme)

    def pick_base(self, base_id: str) -> None:
        """Choose the scenario to copy. Nothing is written until it is cloned."""
        self._base_id = base_id
        self._base = builtin_scenario_for(base_id)
        self._preset_id = ""
        self._edits = None
        self._verdict = None
        self._query = ""
        for pid, chip in self._base_chips.items():
            chip.set_selected(pid == base_id)
        for chip in self._clone_chips.values():
            chip.set_selected(False)

        preset = load_preset(base_id)
        self._name.setText(f"{preset.name if preset else base_id} (edited)")
        self._working_on.setText("nothing cloned yet")
        self._rebuild_editor()
        self._show_verdict()

    def open_clone(self, preset_id: str) -> None:
        """Re-open a clone that already exists, with the edits it was left with."""
        edits = load_overlay(preset_id)
        if edits is None:
            self._note.setText(f"{preset_id} has no overlay file, so it is not a clone.")
            return
        self._preset_id = preset_id
        self._edits = edits
        self._base_id = edits.base
        self._base = builtin_scenario_for(edits.base)
        self._verdict = None
        self._query = ""
        for pid, chip in self._base_chips.items():
            chip.set_selected(pid == edits.base)
        for pid, chip in self._clone_chips.items():
            chip.set_selected(pid == preset_id)
        preset = load_preset(preset_id)
        self._name.setText(preset.name if preset else preset_id)
        self._say_what_is_open()
        self._rebuild_editor()
        self._show_verdict()

    def _say_what_is_open(self) -> None:
        if self._edits is None:
            return
        self._working_on.setText(
            f"editing {self._preset_id} — {self._edits.edit_count} change(s) "
            f"on {self._edits.base}")

    def _clone(self) -> None:
        """Copy the chosen base into a scenario of its own, under a name that is free."""
        if not self._base_id:
            return
        name = self._name.text().strip() or f"{self._base_id} (edited)"
        wanted = _slug(name) or f"{self._base_id}-edited"
        new_id, suffix = wanted, 2
        while load_preset(new_id) is not None:
            new_id, suffix = f"{wanted}-{suffix}", suffix + 1

        clone_scenario(self._base_id, new_id=new_id, name=name)
        self.load()
        self.open_clone(new_id)

    # -- the editor --------------------------------------------------------

    def _rebuild_editor(self) -> None:
        clear_layout(self._editor)
        self._editor_kids = []
        self._relation_rows = []
        self._arsenal_rows = []
        self._find = None
        self._seed = None
        theme = self._theme
        kids = self._editor_kids

        if self._base is None:
            self._editor.addWidget(self._text(
                f"There is no scenario called {self._base_id!r} on this machine, so there "
                "is nothing to edit — an overlay built on a base that is missing cannot be "
                "applied to anything.", style="body", colour="danger", bucket=kids))
            self._editor.addStretch(1)
            return
        if not self._preset_id:
            self._editor.addWidget(self._text(
                "Clone it first. The copy is what you edit — the scenario you started from "
                "is never written to, so making one cannot cost you the era it came from.",
                style="body", colour="ink_faint", bucket=kids))
            self._editor.addStretch(1)
            return

        self._editor.addWidget(SectionLabel("How powers regard each other", theme=theme))
        self._editor.addSpacing(6)
        self._editor.addWidget(self._text(
            "Directed: what the first power makes of the second. A war is two rows, and "
            "setting only one of them is a mistake the auditor will name.",
            style="mono_small", colour="ink_ghost", bucket=kids))
        self._editor.addSpacing(8)

        find = QHBoxLayout()
        find.setSpacing(9)
        self._find = QLineEdit()
        self._find.setFixedWidth(300)
        self._find.setPlaceholderText("find a pair — type a nation")
        self._find.setText(self._query)
        # Connected after the text is set: a textChanged during construction would rebuild
        # a row list that does not exist yet.
        self._find.textChanged.connect(self._search)
        find.addWidget(self._find)
        self._relation_note = self._text("", style="mono_small", colour="ink_ghost",
                                         bucket=kids)
        find.addWidget(self._relation_note, 1)
        find_host = QWidget()
        find_host.setLayout(find)
        self._editor.addWidget(find_host)
        self._editor.addSpacing(8)

        self._relations = QVBoxLayout()
        self._relations.setSpacing(5)
        relations_host = QWidget()
        relations_host.setLayout(self._relations)
        self._editor.addWidget(relations_host)
        self._rebuild_relations()

        self._editor.addSpacing(18)
        self._editor.addWidget(SectionLabel("Nuclear arsenals", theme=theme))
        self._editor.addSpacing(6)
        arsenals = QVBoxLayout()
        arsenals.setSpacing(5)
        arsenals_host = QWidget()
        arsenals_host.setLayout(arsenals)
        self._editor.addWidget(arsenals_host)
        if not self._base.arsenals:
            arsenals.addWidget(self._text(
                "No power in this scenario has one. The workshop changes an arsenal that "
                "exists rather than inventing one, which is why this is empty rather than "
                "missing.", style="body_small", colour="ink_faint", bucket=kids))
        for power, (warheads, delivery) in sorted(self._base.arsenals.items()):
            edited = self._edits.arsenals.get(power) if self._edits else None
            boats = self._base.deterrent_boats.get(power)
            if self._edits and power in self._edits.deterrent_boats:
                boats = self._edits.deterrent_boats[power]
            row = _ArsenalRow(
                power, self._name_of(power),
                int(edited[0]) if edited else int(warheads),
                str(edited[1]) if edited and len(edited) > 1 else str(delivery),
                boats, theme=theme)
            self._arsenal_rows.append(row)
            arsenals.addWidget(row)

        self._editor.addSpacing(18)
        self._editor.addWidget(SectionLabel("The seed", theme=theme))
        self._editor.addSpacing(6)
        seed_row = QHBoxLayout()
        seed_row.setSpacing(10)
        seed = (self._edits.rng_seed if self._edits and self._edits.rng_seed is not None
                else self._base.rng_seed)
        self._seed = _Number(str(int(seed)), theme=theme, width=120, low=0,
                             high=2_000_000_000)
        seed_row.addWidget(self._seed)
        seed_row.addWidget(self._text(
            "Fixes every roll this world will ever make. A different seed is a different "
            "history from the same starting position.",
            style="mono_small", colour="ink_ghost", bucket=kids), 1)
        seed_host = QWidget()
        seed_host.setLayout(seed_row)
        self._editor.addWidget(seed_host)

        self._editor.addSpacing(18)
        self._editor.addWidget(SectionLabel("Not yet editable", theme=theme))
        self._editor.addSpacing(6)
        counts = {
            "powers": len(self._base.powers),
            "sovereignty": len(self._base.sovereignty),
            "treaties": len(self._base.treaties),
        }
        for name, reason in NOT_EDITABLE.items():
            self._editor.addWidget(self._text(
                f"{name} ({counts.get(name, 0)}) — {reason}",
                style="body_small", colour="ink_faint", bucket=kids))
            self._editor.addSpacing(3)
        self._editor.addStretch(1)

    def _name_of(self, power_id: str) -> str:
        power = self._base.power(power_id) if self._base else None
        return f"{power.name} ({power_id})" if power else power_id

    def _search(self, text: str) -> None:
        self._harvest()
        self._query = text.strip().lower()
        self._rebuild_relations()
        self._say_what_is_open()
        self.apply_theme(self._theme)

    def _shown_relations(self) -> list[tuple[str, str, float, bool, bool]]:
        """``(a, b, score, at_war, edited)`` for the rows worth drawing right now."""
        if self._base is None:
            return []
        edited = {(str(entry[0]), str(entry[1])): entry
                  for entry in (self._edits.relations if self._edits else [])}
        rows: list[tuple[str, str, float, bool, bool]] = []
        for a, b, score, _status, war in self._base.relations:
            change = edited.get((a, b))
            rows.append(
                (a, b, float(change[2]) if change else float(score),
                 bool(change[3]) if change else bool(war), change is not None))

        if not self._query:
            return [row for row in rows if row[4] or row[3] or abs(row[2]) >= _NOTABLE]
        words = [word for word in self._query.replace(",", " ").split() if word]
        return [row for row in rows if all(self._matches(row, word) for word in words)]

    def _matches(self, row: tuple[str, str, float, bool, bool], word: str) -> bool:
        return word in " ".join(
            (row[0], row[1], self._name_of(row[0]), self._name_of(row[1]))).lower()

    def _rebuild_relations(self) -> None:
        clear_layout(self._relations)
        self._relation_rows = []
        self._relation_kids = []
        kept = self._shown_relations()
        total = len(self._base.relations) if self._base else 0
        for a, b, score, war, edited in kept[:_SHOWN]:
            row = _RelationRow(a, b, f"{self._name_of(a)}   →   {self._name_of(b)}",
                               score, war, edited=edited, theme=self._theme)
            self._relation_rows.append(row)
            self._relations.addWidget(row)
        if not kept:
            self._relations.addWidget(self._text(
                "No pair matches that. Try a nation's name, or its three-letter id.",
                style="body_small", colour="ink_faint", bucket=self._relation_kids))

        if self._query:
            self._relation_note.setText(
                f"showing {min(len(kept), _SHOWN)} of {len(kept)} matching · "
                f"{total} relations in all")
        else:
            self._relation_note.setText(
                f"showing {min(len(kept), _SHOWN)} of {total} — the edited, the wars and "
                "the strongly held; type to find any other pair")

    # -- edits -------------------------------------------------------------

    def _harvest(self) -> None:
        """Take what is on screen into the overlay, before anything is rebuilt.

        The relation list is a window onto 110 rows, so this merges rather than replaces:
        an edit to a pair that has since been filtered out of view is still an edit. A value
        put back to what the base says stops being one.
        """
        if self._edits is None or self._base is None:
            return
        base = {(a, b): (float(score), bool(war))
                for a, b, score, _status, war in self._base.relations}
        kept = {(str(entry[0]), str(entry[1])): list(entry)
                for entry in self._edits.relations}
        for row in self._relation_rows:
            score, war = row.values()
            was = base.get((row.a, row.b))
            if was is not None and (score, war) != was:
                kept[(row.a, row.b)] = [row.a, row.b, score, war]
            else:
                kept.pop((row.a, row.b), None)
        self._edits.relations = list(kept.values())

        for row in self._arsenal_rows:
            warheads, delivery, boats = row.values()
            if (warheads, delivery) != tuple(self._base.arsenals.get(row.power, ())):
                self._edits.arsenals[row.power] = [warheads, delivery]
            else:
                self._edits.arsenals.pop(row.power, None)
            if boats is not None:
                if boats != self._base.deterrent_boats.get(row.power):
                    self._edits.deterrent_boats[row.power] = boats
                else:
                    self._edits.deterrent_boats.pop(row.power, None)

        if self._seed is not None:
            seed = int(self._seed.number(self._base.rng_seed))
            self._edits.rng_seed = seed if seed != self._base.rng_seed else None

    # -- the auditor -------------------------------------------------------

    def run_audit(self) -> None:
        """Save the edits and put the scenario through the engine. The point of the screen.

        Synchronous, and it builds the world twice: a few seconds on a full era, and worth
        every one of them, because the alternative is finding out from a game.
        """
        if not self._preset_id or self._edits is None:
            self._note.setText("Clone a scenario first; there is nothing to audit.")
            return
        self._harvest()
        save_overlay(self._preset_id, self._edits)
        self._verdict = check(self._preset_id)
        self._say_what_is_open()
        self._show_verdict()

    def _show_verdict(self) -> None:
        clear_layout(self._problems)
        self._problem_kids = []
        verdict = self._verdict

        if verdict is None:
            self._verdict_head.setText("Not audited yet")
            self._verdict_head.set_colour("ink_bright", self._theme)
            self._verdict_line.setText(
                "Nothing has been built from these edits. Audit it, and you will be told "
                "whether the world holds together and whether it comes out the same twice.")
            self._verdict_mono.setText("")
            self._note.setText(
                "A scenario is audited before it can be played from here."
                if self._preset_id else
                "Name your copy and clone it — nothing is written until you do."
                if self._base_id else "Pick something to start from.")
            self._verdict_edge = "rule"
        else:
            good = verdict.coherent and verdict.reproducible
            self._verdict_head.setText(
                "Coherent and reproducible" if good
                else "Could not be built" if not verdict.built
                else f"{len(verdict.problems)} problem(s)" if verdict.problems
                else "Not reproducible")
            self._verdict_head.set_colour("good" if good else "danger", self._theme)
            self._verdict_line.setText(verdict.sentence())
            for problem in verdict.problems[:6]:
                self._problems.addWidget(self._text(
                    problem, style="mono_small", colour="danger",
                    bucket=self._problem_kids))
            if len(verdict.problems) > 6:
                self._problems.addWidget(self._text(
                    f"…and {len(verdict.problems) - 6} more", style="mono_small",
                    colour="ink_ghost", bucket=self._problem_kids))
            self._verdict_mono.setText(
                f"fingerprint {verdict.fingerprint}" if verdict.fingerprint else "")
            self._note.setText(
                "Ready. Playing it selects it on the setup screen, and everything after "
                "that is an ordinary new world." if good else
                "Fix what the auditor found and audit again — a scenario is not playable "
                "from here until it comes back coherent.")
            self._verdict_edge = "good" if good else "danger"

        self._problems_host.setVisible(bool(verdict and verdict.problems))
        self._playable = bool(verdict and verdict.coherent and verdict.reproducible)
        self._play.setEnabled(self._playable)
        self._play.set_selected(self._playable)
        # The text carries the state as well as the border does: a chip that is merely
        # unlit reads as a chip you have not clicked yet rather than one you cannot.
        self._play.set_text(
            "Play this scenario" if self._playable
            else "Play — the auditor said no" if verdict is not None
            else "Play — audit it first")
        self.apply_theme(self._theme)

    def _play_clicked(self) -> None:
        if self._playable and self._preset_id:
            self.play_requested.emit(self._preset_id)
            self.close_overlay()

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._theme = theme
        self._scroll.setStyleSheet(scroll_qss(theme))
        for box in (self._name, self._find):
            if box is not None:
                box.setFont(styled_font(TYPE["mono"]))
                box.setStyleSheet(_field_qss(theme))
        if self._seed is not None:
            self._seed.apply_theme(theme)
        edge = getattr(theme, self._verdict_edge, theme.rule)
        self._verdict_box.setStyleSheet(
            f"QFrame#verdictBox {{ background: {theme.inset};"
            f" border: 1px solid {theme.rule_soft}; border-left: 3px solid {edge}; }}")
        for label in (*self._kids, *self._editor_kids, *self._relation_kids,
                      *self._problem_kids):
            label.apply_theme(theme)
        for row in (*self._relation_rows, *self._arsenal_rows):
            row.apply_theme(theme)
        for chip in (*self._base_chips.values(), *self._clone_chips.values(),
                     self._clone_button, self._close, self._audit, self._play):
            chip.apply_theme(theme)
