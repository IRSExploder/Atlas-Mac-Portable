"""The screen before the world exists: pick a scenario, pick a power, start.

Deliberately a full screen rather than a dialog. This is the only moment where the whole
shape of a game is decided, and the choices deserve room to explain themselves -- a
scenario is a paragraph of context, and a power is a temperament, not a name in a
dropdown.

**Three numbered steps, and at the size this is drawn at only the power list scrolls.**
That list is two dozen rows in Modern Day, and it used to sit in the same scroll as the
difficulty chips, the world name and the seed -- so every setting after the second choice
was below two dozen powers, and most players will never have seen them. The list now
scrolls inside itself and the rest stays on the glass; the column around it keeps a
scroll of its own for windows too short to hold all three steps at once.

The screen decides nothing and creates nothing. It emits :attr:`SetupScreen.create` with
a :class:`WorldRequest`, and the caller does the work; world creation touches the
filesystem and the geometry pack, neither of which belongs behind a button handler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..scenarios.presets import Preset, playable_presets
from ..state.models import Difficulty
from .theme import TYPE, Theme
from .widgets import (
    Chip,
    Divider,
    SectionLabel,
    ShieldSwatch,
    TextLabel,
    restyle,
    scroll_qss,
    styled_font,
)
from .workshop import WorkshopOverlay

__all__ = ["PowerNote", "SetupScreen", "WorldRequest", "playable_powers", "power_notes"]

#: A power list longer than this earns a filter box. Below it the box is only another
#: thing to read: four powers are faster to scan than to type at.
FILTER_FROM = 8

#: Difficulty in the player's terms rather than the engine's. The engine's numbers are
#: real, but "impossible" as a bare word tells you nothing about what changes.
_DIFFICULTIES = (
    (Difficulty.VERY_EASY, "Forgiving", "Powers hesitate, and mistakes rarely compound."),
    (Difficulty.EASY, "Easy", "The world gives you room to recover."),
    (Difficulty.NORMAL, "Normal", "Powers act on their interests without malice."),
    (Difficulty.HARD, "Hard", "Rivals notice weakness and move on it."),
    (Difficulty.IMPOSSIBLE, "Merciless", "Every power plays to win, and none of them are you."),
)


@dataclass(frozen=True, slots=True)
class WorldRequest:
    """Everything needed to create a world. The screen's entire output."""

    world_id: str
    preset: Preset
    player: str | None
    difficulty: Difficulty
    seed: int


def playable_powers(preset: Preset) -> list[tuple[str, str, str]]:
    """``(id, name, colour)`` for every power this scenario offers.

    Reads the scenario's own definition rather than the map, because a designed power is
    not a modern country: "Germany" in the Gathering Storm is one power composed of
    several modern regions, and the map cannot know that.

    A preset with no scenario behind it falls back to the geo keys it names as playable,
    which is all there is to go on for a world nobody wrote.
    """
    from ..scenarios.registry import scenario_for

    scenario = scenario_for(preset.id)
    if scenario is not None:
        return scenario.playable()

    return [(key, key.rsplit(":", 1)[-1], "#7E8AA0") for key in preset.playable]


def _pretty_date(iso: str) -> str:
    """``1936-01-01`` becomes ``1 January 1936``. The screen writes dates like a historian.

    Falls through to whatever it was given: a scenario whose start date is not a date is
    still better shown as it stands than not shown at all.
    """
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


# ------------------------------------------------------------------ where a power starts

#: Read against the rest of the scenario, never against a fixed number. 1972's United
#: States and 1897's Aurelia both sit at the top of their own worlds, and an absolute
#: threshold would call one of them small because its designer wrote in tens rather than
#: hundreds.
_ECONOMY = ((0.80, "one of the largest in this world"), (0.55, "larger than most here"),
            (0.30, "middling"), (0.0, "small"))
_FORCES = ((0.80, "one of the strongest here"), (0.55, "stronger than most"),
           (0.30, "middling"), (0.0, "slight"))
_SEAT = ((0.66, "you would start stronger than most powers here"),
         (0.40, "you would start about level with the rest"),
         (0.0, "you would start weaker than most powers here"))
#: The scale word carried on the row itself, so a list of two dozen can be read for
#: weight without opening each one.
_SCALE = ((0.75, "great power"), (0.40, "middle power"), (0.0, "small power"))

#: Stability and legitimacy are already on the same 0-100 scale for every era, so these
#: two read absolutely where the others read relatively.
_STABILITY = ((70, "settled"), (50, "holding together"), (35, "uneasy"), (0, "fragile"))
_LEGITIMACY = ((70, "under a government few question"),
               (50, "under a government most accept"),
               (35, "under a government being questioned"),
               (0, "under a government few accept"))


@dataclass(frozen=True, slots=True)
class PowerNote:
    """Everything the screen can say about one power, from the scenario's own fields.

    Every string here is a field the scenario actually carries, phrased. A power that
    does not carry one loses that line rather than gaining an invented one -- which is
    why :attr:`aims` is empty for two of the three eras and the panel simply omits it.
    """

    character: str
    scale: str
    facts: tuple[tuple[str, str], ...]
    aims: str


def power_notes(preset: Preset) -> dict[str, PowerNote]:
    """``power id -> PowerNote`` for a scenario, resolved once.

    Once, because resolving a scenario means an authored-clone lookup that touches the
    filesystem, and the old screen did it twice per row: two dozen powers were two dozen
    directory reads to draw one list.

    Empty for a preset with no scenario behind it. There is no starting situation to show
    for a world nobody wrote, and the honest answer is to show nothing.
    """
    from ..scenarios.registry import scenario_for

    scenario = scenario_for(preset.id)
    if scenario is None or not scenario.powers:
        return {}

    economies = [power.economy for power in scenario.powers]
    forces = [power.military for power in scenario.powers]
    notes: dict[str, PowerNote] = {}
    for power in scenario.powers:
        economy = _rank(power.economy, economies)
        force = _rank(power.military, forces)
        seat = (economy + force) / 2
        notes[power.id] = PowerNote(
            character=_first_sentence(power.character),
            scale=_band(seat, _SCALE),
            facts=(
                ("Economy", _band(economy, _ECONOMY)),
                ("Armed forces", _band(force, _FORCES)),
                ("At home", f"{_band(power.stability, _STABILITY)}, "
                            f"{_band(power.legitimacy, _LEGITIMACY)}"),
                ("Expect", _band(seat, _SEAT)),
            ),
            # The first aim only. A government's stated aims run to four sentences and
            # this is a caption under a choice, not a briefing -- the rest of them are
            # what the first turn is for.
            aims=power.goals[0] if power.goals else "",
        )
    return notes


def _rank(value: float, field: list[float]) -> float:
    """Where ``value`` falls among ``field``, from 0.0 to 1.0. Ties share their place."""
    if not field:
        return 0.5
    below = sum(1 for other in field if other < value)
    same = sum(1 for other in field if other == value)
    return (below + same / 2) / len(field)


def _band(value: float, table: tuple[tuple[float, str], ...]) -> str:
    return next((phrase for floor, phrase in table if value >= floor), table[-1][1])


def _first_sentence(character: str) -> str:
    if not character.strip():
        return "A power on the map."
    return character.split(". ")[0].rstrip(".") + "."


class SetupScreen(QWidget):
    """Scenario, power, difficulty, seed."""

    create = Signal(object)
    cancelled = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._presets: list[Preset] = []
        self._preset: Preset | None = None
        self._player: str | None = None
        self._difficulty = Difficulty.NORMAL
        # Resolved once per scenario rather than once per row, and held because the
        # footer's gate asks "is a power required here" on every click.
        self._powers: list[tuple[str, str, str]] = []
        self._notes: dict[str, PowerNote] = {}
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        outer.addWidget(Divider(theme=theme))

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        columns.addWidget(self._build_scenarios(), 0)
        columns.addWidget(Divider(vertical=True, theme=theme))
        columns.addWidget(self._build_detail(), 1)
        outer.addLayout(columns, 1)

        outer.addWidget(Divider(theme=theme))
        outer.addWidget(self._build_footer())

        # The workshop belongs here rather than in the game window, and that is what makes
        # its Play button real: a clone is an ordinary preset, so playing one is choosing it
        # in the list below and pressing the button that was already there. Opened from the
        # running world it would have needed a window rebuilt around a world that does not
        # exist yet, which is the lifecycle change branch switching is still waiting on.
        self._workshop = WorkshopOverlay(theme, self)
        self._workshop.play_requested.connect(self._play_authored)

        self.reload()
        self.apply_theme(theme)

    # -- construction ------------------------------------------------------

    def _build_header(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(52, 32, 52, 24)
        layout.setSpacing(8)
        self._title = TextLabel("ATLAS", style="brand_large", colour="brass", theme=self._theme)
        layout.addWidget(self._title)
        layout.addWidget(
            TextLabel("A new world", style="title", colour="ink_bright", theme=self._theme)
        )
        layout.addWidget(
            TextLabel(
                "Three choices: where history starts, who you speak for, and how hard the "
                "rest of the world plays. Everything after this is decided by what happens.",
                style="prose", colour="ink_dim", theme=self._theme, wrap=True,
            )
        )
        return holder

    def _build_scenarios(self) -> QWidget:
        holder = QWidget()
        holder.setFixedWidth(400)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(52, 30, 34, 30)
        layout.setSpacing(14)
        layout.addWidget(SectionLabel("1 · Scenario", theme=self._theme))

        self._scenario_box = QVBoxLayout()
        self._scenario_box.setSpacing(10)
        layout.addLayout(self._scenario_box)
        layout.addStretch(1)
        return holder

    def _build_detail(self) -> QWidget:
        """Steps two and three, with the power list the only part that scrolls.

        The column is wrapped in a scroll area all the same, and it earns its keep only
        below about 800 pixels of window: the minimum window this program allows is 700
        tall, and a layout that cannot fit has to give somewhere. Squeezing was what it
        did before -- every block to its minimum, the fact rows on top of each other --
        and a column that scrolls at the bottom of its range is the honest version.
        """
        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QFrame.Shape.NoFrame)
        outer.setStyleSheet(scroll_qss(self._theme))
        self._detail_area = outer

        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(40, 24, 46, 18)
        layout.setSpacing(0)

        self._blurb = TextLabel("", style="prose", colour="ink_body", theme=self._theme, wrap=True)
        layout.addWidget(self._blurb)
        layout.addSpacing(20)

        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(SectionLabel("2 · Who you will be", theme=self._theme))
        # The requirement, said where the requirement is. It used to be said in the
        # footer, a screen away from both the list it is about and the button it blocks,
        # where it read as a complaint about nothing.
        self._required = TextLabel("required", style="label", colour="brass",
                                   theme=self._theme)
        head.addWidget(self._required)
        head.addStretch(1)
        self._filter = _FilterBox(theme=self._theme)
        self._filter.changed.connect(self._apply_filter)
        head.addWidget(self._filter)
        layout.addLayout(head)
        layout.addSpacing(10)

        self._no_matches = TextLabel("", style="body_small", colour="ink_faint",
                                     theme=self._theme, wrap=True)
        self._no_matches.setVisible(False)
        layout.addWidget(self._no_matches)

        # **Beside the list, not under it.** Under it, the panel was 130 pixels the power
        # list did not have, on the one screen where the list is the content; beside it,
        # in a column this wide, it costs nothing and stands next to the rows it is
        # describing.
        body = QHBoxLayout()
        body.setSpacing(24)

        self._powers_area = QScrollArea()
        self._powers_area.setWidgetResizable(True)
        self._powers_area.setFrameShape(QFrame.Shape.NoFrame)
        self._powers_area.setStyleSheet(scroll_qss(self._theme))
        self._powers_area.setMinimumHeight(150)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._powers_box = QVBoxLayout(inner)
        self._powers_box.setContentsMargins(0, 0, 12, 0)
        self._powers_box.setSpacing(0)
        # Without this the rows share out whatever height the scroll area gives the
        # widget, and six powers become six very tall powers.
        self._powers_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._powers_area.setWidget(inner)
        body.addWidget(self._powers_area, 1)

        self._situation = _SituationPanel(theme=self._theme)
        self._situation.setFixedWidth(420)
        body.addWidget(self._situation, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(body, 1)
        layout.addSpacing(20)

        layout.addWidget(Divider(soft=True, theme=self._theme))
        layout.addSpacing(14)

        layout.addWidget(SectionLabel("3 · Difficulty and the world", theme=self._theme))
        layout.addSpacing(12)
        self._difficulty_box = QHBoxLayout()
        self._difficulty_box.setSpacing(5)
        self._difficulty_chips: dict[Difficulty, Chip] = {}
        for level, label, tip in _DIFFICULTIES:
            chip = Chip(label, theme=self._theme, selected=(level is Difficulty.NORMAL))
            chip.setToolTip(tip)
            chip.clicked.connect(lambda value=level: self._pick_difficulty(value))
            self._difficulty_chips[level] = chip
            self._difficulty_box.addWidget(chip)
        self._difficulty_box.addStretch(1)
        layout.addLayout(self._difficulty_box)
        layout.addSpacing(8)
        self._difficulty_note = TextLabel("", style="body_small", colour="ink_faint",
                                          theme=self._theme, wrap=True)
        layout.addWidget(self._difficulty_note)
        layout.addSpacing(16)

        fields = QHBoxLayout()
        fields.setSpacing(20)
        # Ghost text, re-grounded whenever a scenario is chosen. Empty rather than
        # hardcoded, because the value it shows before a choice is made is the value the
        # scenario *would* use, and until there is a scenario there is no such value --
        # and a Modern Day player emptying this box should not be shown 1936.
        self._world_id = _Field("World name", "", theme=self._theme)
        fields.addWidget(self._world_id)
        # **The seed is a reproducibility control, not a setting.** Every world already
        # has a good one, and standing beside the world's name as an equal it read as a
        # fourth decision to make before the game could start.
        self._advanced_toggle = Chip("Advanced", theme=self._theme, padding=(12, 6))
        self._advanced_toggle.clicked.connect(self._toggle_advanced)
        fields.addWidget(self._advanced_toggle, 0, Qt.AlignmentFlag.AlignBottom)
        fields.addStretch(1)
        layout.addLayout(fields)

        self._advanced = QWidget()
        self._advanced.setStyleSheet("background: transparent;")
        advanced = QVBoxLayout(self._advanced)
        advanced.setContentsMargins(0, 18, 0, 0)
        advanced.setSpacing(10)
        self._seed = _Field("Seed", "", theme=self._theme, numeric=True)
        advanced.addWidget(self._seed)
        advanced.addWidget(
            TextLabel(
                "The seed fixes every roll this world will ever make. The same seed and the "
                "same orders produce the same history.",
                style="body_small", colour="ink_ghost", theme=self._theme, wrap=True,
            )
        )
        self._advanced.setVisible(False)
        layout.addWidget(self._advanced)
        # No trailing stretch: the room left over belongs to the power list, and a
        # stretch down here would take half of it to hold up the bottom margin.

        outer.setWidget(holder)
        return outer

    def _toggle_advanced(self) -> None:
        """Show or hide the seed. Nothing else lives under it, and that is the point."""
        showing = not self._advanced.isVisible()
        self._advanced.setVisible(showing)
        self._advanced_toggle.set_selected(showing)
        if showing:
            # On a short window the thing just revealed is below the fold, and a
            # disclosure that discloses something off-screen has not disclosed anything.
            # Next turn of the loop, because the layout has not moved yet.
            QTimer.singleShot(0, lambda: self._detail_area.ensureWidgetVisible(self._advanced))

    def _build_footer(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(52, 18, 52, 22)
        layout.setSpacing(12)
        # What this world *is*, not what is wrong with it. The complaint moved to the two
        # places it belongs: beside the list that has to be answered, and beside the
        # button it is holding shut.
        self._status = TextLabel("", style="body_small", colour="ink_faint",
                                 theme=self._theme, wrap=True)
        layout.addWidget(self._status, 1)
        self._workshop_button = Chip("Scenario workshop", theme=self._theme, padding=(13, 7))
        self._workshop_button.clicked.connect(self.open_workshop)
        layout.addWidget(self._workshop_button)
        self._cancel = Chip("Back", theme=self._theme, padding=(13, 7))
        self._cancel.clicked.connect(self.cancelled.emit)
        self._hint = TextLabel("", style="body_small", colour="brass", theme=self._theme)
        self._create = Chip("Create world", theme=self._theme, selected=True, padding=(16, 8))
        self._create.clicked.connect(self._emit_request)
        layout.addWidget(self._cancel)
        # Last but one, so the sentence and the button it is about are the same glance.
        layout.addWidget(self._hint)
        layout.addWidget(self._create)
        return holder

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        # Only presets whose map can be baked: one that cannot is not a game, and picking
        # it ended in a request to bake a source that does not exist.
        self._presets = playable_presets()
        _empty(self._scenario_box)
        self._scenario_cards: list[_ScenarioCard] = []
        for preset in self._presets:
            card = _ScenarioCard(preset, theme=self._theme)
            card.chosen.connect(lambda p=preset: self.select_preset(p))
            self._scenario_cards.append(card)
            self._scenario_box.addWidget(card)
        if self._presets:
            self.select_preset(self._presets[0])

    def select_preset(self, preset: Preset) -> None:
        self._preset = preset
        self._player = None
        for card in self._scenario_cards:
            card.set_selected(card.preset_id == preset.id, self._theme)

        lore = preset.lore_text or preset.description
        self._blurb.setText(_opening(lore))
        # Nothing is lost by shortening it: the whole paragraph is a hover away, and the
        # simulation reads the field itself either way.
        self._blurb.setToolTip(lore if lore != self._blurb.text() else "")
        default_id = preset.id.replace("-", "") or "world"
        self._world_id.set_text(default_id)
        self._world_id.set_placeholder(default_id)
        self._seed.set_text(str(preset.seed))
        self._seed.set_placeholder(str(preset.seed))
        self._pick_difficulty(preset.difficulty)

        _empty(self._powers_box)
        self._power_rows: list[_PowerRow] = []
        #: The rule above each row, held alongside it so filtering can drop the leading
        #: one. A list whose first visible row opens with a horizontal rule looks cut off.
        self._power_rules: list[Divider | None] = []
        self._powers = playable_powers(preset)
        self._notes = power_notes(preset)
        if not self._powers:
            self._powers_box.addWidget(
                TextLabel("This scenario lets you play any power on the map; "
                          "pick one from the map once the world exists.",
                          style="body_small", colour="ink_faint", theme=self._theme, wrap=True)
            )
        for index, (power_id, name, colour) in enumerate(self._powers):
            rule = None
            if index:
                rule = Divider(soft=True, theme=self._theme)
                self._powers_box.addWidget(rule)
            note = self._notes.get(power_id)
            row = _PowerRow(power_id, name, colour,
                            note.character if note else "A power on the map.",
                            scale=note.scale if note else "",
                            theme=self._theme)
            row.chosen.connect(lambda pid=power_id: self._pick_power(pid))
            self._power_rows.append(row)
            self._power_rules.append(rule)
            self._powers_box.addWidget(row)

        # A filter over four names costs more attention than it saves; over twenty-four
        # it is the difference between choosing and hunting.
        self._filter.setVisible(len(self._powers) >= FILTER_FROM)
        self._filter.reset(len(self._powers))
        self._no_matches.setVisible(False)
        self._powers_area.setVisible(bool(self._powers))
        self._powers_area.verticalScrollBar().setValue(0)
        self._situation.clear(self._theme, needed=bool(self._powers))
        self._refresh_status()

    def _pick_power(self, power_id: str) -> None:
        self._player = power_id
        for row in self._power_rows:
            row.set_selected(row.power_id == power_id, self._theme)
        name = next((n for pid, n, _c in self._powers if pid == power_id), power_id)
        self._situation.show_power(name, self._notes.get(power_id), self._theme)
        self._refresh_status()

    def _apply_filter(self, text: str) -> None:
        """Hide the powers that do not match. Nothing about the choice itself changes."""
        needle = text.strip().lower()
        shown = 0
        for row, rule in zip(self._power_rows, self._power_rules, strict=True):
            hit = row.matches(needle)
            row.setVisible(hit)
            if rule is not None:
                rule.setVisible(hit and shown > 0)
            shown += 1 if hit else 0
        self._no_matches.setText(
            f"No power in this scenario matches \u201c{text.strip()}\u201d." if not shown else "")
        self._no_matches.setVisible(not shown)

    def _pick_difficulty(self, level: Difficulty) -> None:
        self._difficulty = level
        for value, chip in self._difficulty_chips.items():
            chip.set_selected(value is level)
        note = next((tip for lvl, _label, tip in _DIFFICULTIES if lvl is level), "")
        self._difficulty_note.setText(note)

    def _refresh_status(self) -> None:
        if self._preset is None:
            self._status.setText("No scenarios are installed.")
            self._refresh_gate()
            return
        # The map's pack identifier used to be the third clause here. "admin0-50m" is a
        # filename, and no answer to anything a person picking a scenario wants.
        self._status.setText(
            f"{self._preset.name} · starts {_pretty_date(self._preset.start_date)}"
        )
        self._status.set_colour("ink_faint", self._theme)
        self._refresh_gate()

    def _refresh_gate(self) -> None:
        """Create world is live exactly when pressing it would do something.

        A button that takes the press and then explains why it will not act is a button
        that has to be pressed to be understood. This one says so before the press, in
        the two places a player is already looking: the step that is unanswered, and the
        button that is waiting on it.
        """
        wanted = bool(self._preset is not None and self._player is None and self._powers)
        self._required.setVisible(wanted)
        self._hint.setText("Choose a power first" if wanted and not self._busy else "")
        self._create.setEnabled(self._preset is not None and not wanted and not self._busy)

    def _emit_request(self) -> None:
        if self._preset is None:
            return
        world_id = _slug(self._world_id.text()) or self._preset.id.replace("-", "")
        # Belt and braces: the button is disabled in this state, and a keyboard or a
        # caller can still reach the slot.
        if self._player is None and self._powers:
            self._refresh_gate()
            return
        self.create.emit(
            WorldRequest(
                world_id=world_id,
                preset=self._preset,
                player=self._player,
                difficulty=self._difficulty,
                seed=int(self._seed.text() or self._preset.seed),
            )
        )

    # -- the workshop ------------------------------------------------------

    def open_workshop(self) -> None:
        """Clone a scenario and edit it. Reads what is installed each time it opens."""
        self._workshop.load()
        self._workshop.open()

    def _play_authored(self, preset_id: str) -> None:
        """An audited clone, selected in the ordinary list as though it had shipped."""
        self.reload()
        chosen = next((preset for preset in self._presets if preset.id == preset_id), None)
        if chosen is None:
            self._status.setText(f"{preset_id} was cloned but is not in the list.")
            return
        self.select_preset(chosen)
        self._status.setText(f"{chosen.name} — audited coherent and reproducible.")

    def show_problem(self, message: str) -> None:
        """A refusal from the far end -- no map pack, a world of that name already there.

        Brass, because in the colour of the scenario line under it a refusal read as one
        more caption. It goes back to a caption on the next choice made.
        """
        self._status.setText(message)
        self._status.set_colour("brass", self._theme)

    def set_busy(self, busy: bool) -> None:
        """Creation derives adjacency from real geometry and takes a few seconds."""
        self._busy = busy
        self._create.set_text("Creating…" if busy else "Create world")
        self._cancel.setEnabled(not busy)
        self._refresh_gate()

    # -- theme -------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._workshop.setGeometry(self.rect())

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(f"SetupScreen {{ background: {theme.canvas}; }}")
        # A QScrollArea has no `apply_theme` for `restyle` to find, so its bar keeps the
        # colours it was built with unless it is told. It was told nowhere, and the power
        # list is now the one thing on this screen that scrolls.
        self._powers_area.setStyleSheet(scroll_qss(theme))
        self._detail_area.setStyleSheet(scroll_qss(theme))
        restyle(self, theme)


# ---------------------------------------------------------------------------


class _ScenarioCard(QFrame):
    chosen = Signal()

    def __init__(self, preset: Preset, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preset_id = preset.id
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 15, 17, 16)
        layout.setSpacing(7)
        self._name = TextLabel(preset.name, style="row", colour="ink", theme=theme, wrap=True)
        # The date this world opens on, written the way the game writes every other date.
        # An ISO string is a value out of a file; "1 January 1936" is when the story starts.
        self._date = TextLabel(_pretty_date(preset.start_date), style="mono_small",
                               colour="brass", theme=theme)
        self._blurb = TextLabel(preset.description, style="body_small", colour="ink_faint",
                                theme=theme, wrap=True)
        layout.addWidget(self._date)
        layout.addWidget(self._name)
        layout.addWidget(self._blurb)
        self.apply_theme(theme)

    def set_selected(self, selected: bool, theme: Theme) -> None:
        self._selected = selected
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        border = theme.brass_line if self._selected else theme.rule
        background = theme.brass_wash if self._selected else theme.panel
        self.setStyleSheet(
            f"_ScenarioCard {{ border: 1px solid {border}; background: {background}; }}"
            f"_ScenarioCard:hover {{ border-color: {theme.rule_hover}; }}"
        )
        for child in (self._name, self._date, self._blurb):
            child.apply_theme(theme)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()
        super().mousePressEvent(event)


class _PowerRow(QFrame):
    """A power, its temperament, and how much of the world it starts with.

    The temperament is the choice being made; the scale word is what lets two dozen of
    these be *read* rather than searched. It is the power's own economy and army placed
    against the other powers of the same scenario, which is the only comparison that
    means anything -- a 1897 fleet and a 2026 one share no units.
    """

    chosen = Signal()

    def __init__(self, power_id: str, name: str, colour: str, character: str,
                 *, theme: Theme, scale: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.power_id = power_id
        self._scale = scale
        self._selected = False
        #: What the filter matches on. The id is in here because "usa" is what a player
        #: types when they mean the United States.
        self._haystack = f"{name} {power_id}".lower()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 13, 12, 13)
        layout.setSpacing(14)
        self._shield = ShieldSwatch(colour, width=20, height=26)
        layout.addWidget(self._shield, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(5)
        self._name = TextLabel(name, style="row", colour="ink", theme=theme)
        self._character = TextLabel(character, style="body_small", colour="ink_faint",
                                    theme=theme, wrap=True)
        column.addWidget(self._name)
        column.addWidget(self._character)
        layout.addLayout(column, 1)

        self._mark = TextLabel(scale, style="label", colour="ink_ghost", theme=theme)
        layout.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)

    def matches(self, needle: str) -> bool:
        return not needle or needle in self._haystack

    def set_selected(self, selected: bool, theme: Theme) -> None:
        self._selected = selected
        self._mark.setText("chosen" if selected else self._scale)
        self._mark.set_colour("brass" if selected else "ink_ghost", theme)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        border = theme.brass_line if self._selected else "transparent"
        background = theme.brass_wash if self._selected else "transparent"
        self.setStyleSheet(
            f"_PowerRow {{ border: 1px solid {border}; background: {background}; }}"
            f"_PowerRow:hover {{ border-color: {theme.rule_hover}; }}"
        )
        for child in (self._name, self._character, self._mark):
            child.apply_theme(theme)
        self._name.set_colour("brass" if self._selected else "ink", theme)
        self._mark.set_colour("brass" if self._selected else "ink_ghost", theme)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()
        super().mousePressEvent(event)


class _FilterBox(QWidget):
    """A one-line filter over the power list.

    Deliberately not the scenario list as well. There are four scenarios and each is a
    card with a paragraph under it; a box that filters four things is a box that has to
    be read before it can be ignored.
    """

    changed = Signal(str)

    def __init__(self, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._edit = QLineEdit()
        self._edit.setObjectName("powerFilter")
        self._edit.setFixedWidth(230)
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self.changed.emit)
        layout.addWidget(self._edit)
        self.apply_theme(theme)

    def reset(self, count: int) -> None:
        """Cleared and re-labelled for a scenario. A filter must not outlive its list."""
        self._edit.blockSignals(True)
        self._edit.clear()
        self._edit.blockSignals(False)
        self._edit.setPlaceholderText(f"Filter {count} powers\u2026")

    def text(self) -> str:
        return self._edit.text()

    def set_text(self, text: str) -> None:
        self._edit.setText(text)

    def apply_theme(self, theme: Theme) -> None:
        self._edit.setFont(styled_font(TYPE["body_small"]))
        self._edit.setStyleSheet(
            f"QLineEdit#powerFilter {{ background: {theme.inset};"
            f" border: 1px solid {theme.edge}; color: {theme.ink};"
            f" padding: 6px 9px; selection-background-color: {theme.brass_line}; }}"
            f"QLineEdit#powerFilter:focus {{ border-color: {theme.brass_line}; }}"
        )


class _SituationPanel(QFrame):
    """Where the chosen power starts, in the scenario's own figures.

    A temperament says who you would be. It does not say whether you would be speaking
    from the top of the world or the bottom of it, and that was already in the scenario
    file -- economy, army, stability, legitimacy, and what the government says it wants.
    None of it reached this screen, and it is the half of the choice a first-time player
    has no other way to guess.
    """

    def __init__(self, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 13, 17, 14)
        layout.setSpacing(0)
        self._heading = TextLabel("", style="row", colour="brass", theme=theme, wrap=True)
        layout.addWidget(self._heading)
        layout.addSpacing(9)
        self._facts = QGridLayout()
        self._facts.setHorizontalSpacing(12)
        self._facts.setVerticalSpacing(5)
        self._facts.setContentsMargins(0, 0, 0, 0)
        self._facts.setColumnStretch(1, 1)
        layout.addLayout(self._facts)
        layout.addSpacing(10)
        self._aims = TextLabel("", style="body_small", colour="ink_faint", theme=theme,
                               wrap=True)
        layout.addWidget(self._aims)
        self.clear(theme)

    def clear(self, theme: Theme, *, needed: bool = True) -> None:
        """No power chosen. Says what is missing, next to where it would be chosen."""
        _empty(self._facts)
        self._aims.setVisible(False)
        self._heading.setText(
            "Choose a power above — how it starts appears here."
            if needed else "Any power on the map, chosen once the world exists."
        )
        self._heading.set_colour("ink_faint", theme)
        self.apply_theme(theme)

    def show_power(self, name: str, note: PowerNote | None, theme: Theme) -> None:
        _empty(self._facts)
        self._heading.setText(name)
        self._heading.set_colour("brass", theme)
        for index, (label, value) in enumerate(note.facts if note else ()):
            _fact_cell(self._facts, index, label, value, theme)
        # Two of the three eras author no goals for anybody, and a power with none says
        # nothing here rather than being given something to want.
        self._aims.setText(f"Wants to {note.aims}." if note and note.aims else "")
        self._aims.setVisible(bool(note and note.aims))
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"_SituationPanel {{ border: 1px solid {theme.rule};"
            f" background: {theme.panel}; }}"
        )
        for child in self.findChildren(TextLabel):
            child.apply_theme(theme)


def _fact_cell(grid: QGridLayout, index: int, label: str, value: str, theme: Theme) -> None:
    """One fact: a small-caps key, and the phrase the scenario's numbers come to.

    A grid rather than a row per fact, so the phrases line up under each other however
    long the keys are.
    """
    key = SectionLabel(label, theme=theme)
    # Wide enough for the longest key the vocabulary has, tracked small caps included.
    # It was 88 and "Armed forces" was arriving as "ARMED FORC".
    key.setFixedWidth(104)
    grid.addWidget(key, index, 0, Qt.AlignmentFlag.AlignTop)
    # **Not wrapped.** Every phrase here comes from a fixed vocabulary a dozen words long,
    # so none of them needs a second line -- and a wrapped label asks for the height of
    # the two lines it might one day take, which is what flattened this panel into a
    # stack of six-pixel rows the first time it was drawn.
    grid.addWidget(TextLabel(value, style="body_small", colour="ink_body", theme=theme,
                             wrap=True), index, 1)


class _Field(QWidget):
    """A label over a single-line input."""

    def __init__(self, label: str, placeholder: str, *, theme: Theme,
                 numeric: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._label = SectionLabel(label, theme=theme)
        layout.addWidget(self._label)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.setFixedWidth(190)
        if numeric:
            self._edit.setValidator(QIntValidator(0, 2_000_000_000, self._edit))
        layout.addWidget(self._edit)
        self.apply_theme(theme)

    def text(self) -> str:
        return self._edit.text().strip()

    def set_text(self, text: str) -> None:
        self._edit.setText(text)

    def set_placeholder(self, text: str) -> None:
        """The ghost text, which follows the chosen scenario rather than naming an era."""
        self._edit.setPlaceholderText(text)

    def apply_theme(self, theme: Theme) -> None:
        self._label.apply_theme(theme)
        self._edit.setFont(styled_font(TYPE["mono"]))
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: {theme.inset}; border: 1px solid {theme.edge};"
            f" color: {theme.ink}; padding: 8px 10px;"
            f" selection-background-color: {theme.brass_line}; }}"
            f"QLineEdit:focus {{ border-color: {theme.brass_line}; }}"
        )


def _opening(lore: str, limit: int = 330) -> str:
    """The first sentences of a scenario's lore, up to about a paragraph's worth.

    **A screen cannot be laid out around a field with no length limit.** Every block
    below this one -- the power list, the standing panel, the difficulty, the world's
    name -- is meant to be reachable without scrolling, and the three shipped scenarios
    open with three, five and seven lines respectively. The longest of them pushed the
    world name off the bottom of the design size on its own, and an authored clone can
    write as much as it likes.

    Cut on sentence boundaries, never mid-word: what is shown has to read as a finished
    thought rather than as text that ran out.
    """
    lore = " ".join(lore.split())
    if len(lore) <= limit:
        return lore
    kept: list[str] = []
    for sentence in lore.split(". "):
        candidate = ". ".join([*kept, sentence])
        if kept and len(candidate) > limit:
            break
        kept.append(sentence)
    return ". ".join(kept).rstrip(".") + ". \u2026"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", text.strip().lower().replace(" ", "-"))


def _empty(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        # Held in a name: unparenting the widget empties the item that was holding it,
        # and a second `item.widget()` then answers None.
        widget = item.widget()
        if widget is not None:
            # Unparented *before* the deferred delete, because a widget waiting to be
            # deleted is still a child of the holder and still painted where it was. A
            # scenario switch drew both scenarios on top of each other for one frame.
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _empty(item.layout())
