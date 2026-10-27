"""The 348-pixel left rail: Events, Focus, Region, Stats.

The event feed is the one the player reads most, so it gets the most care: entries are
grouped under a sticky turn heading, each carries a coloured kind chip and the actor, and
the prose is set in the serif at a comfortable measure. It is a chronicle, not a log.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..engine.focus import FOCUS_CAP, rank_candidates, tracked_nations
from ..humanize import count, duration, exact
from ..humanize import strength as force_of
from .plain import technical
from .theme import Theme
from .widgets import (
    Chip,
    Divider,
    Rail,
    SectionLabel,
    ShieldSwatch,
    StatBar,
    TextLabel,
    restyle,
    scaled,
    scroll_qss,
)

__all__ = ["LeftRail"]

#: Event kinds grouped into the five families the design filters by, with a colour each.
_KIND_FAMILY = {
    "war_declared": ("WAR", "danger"), "battle": ("WAR", "danger"),
    "siege": ("WAR", "danger"), "casus_belli": ("WAR", "danger"),
    "peace_signed": ("TREATY", "good"), "ceasefire": ("TREATY", "good"),
    "treaty_signed": ("TREATY", "good"), "alliance_formed": ("TREATY", "good"),
    "trade_agreement": ("TREATY", "good"), "commitment_made": ("TREATY", "good"),
    "treaty_broken": ("WAR", "danger"), "alliance_broken": ("WAR", "danger"),
    "commitment_broken": ("WAR", "danger"),
    "move": ("MOVEMENT", "brass"), "territory_transfer": ("MOVEMENT", "brass"),
    "order_issued": ("MOVEMENT", "brass"), "unit_raised": ("MOVEMENT", "brass"),
    "diplomatic_message": ("TALKS", "info"), "ultimatum": ("TALKS", "info"),
    "envoy_dispatched": ("TALKS", "info"),
}
_FILTERS = (("all", "All"), ("war", "War"), ("treaty", "Treaties"), ("talks", "Talks"))
_FILTER_KINDS = {
    "war": ("war_declared", "casus_belli", "treaty_broken", "alliance_broken",
            "commitment_broken", "battle", "siege", "territory_transfer"),
    "treaty": ("treaty_signed", "peace_signed", "ceasefire", "alliance_formed",
               "trade_agreement", "commitment_made"),
    "talks": ("diplomatic_message", "ultimatum", "envoy_dispatched"),
}


def _scroller(theme: Theme) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """A transparent scroll area with a column inside. Used by every tab."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet(scroll_qss(theme))

    body = QWidget()
    body.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(16, 12, 16, 20)
    layout.setSpacing(0)
    area.setWidget(body)
    return area, body, layout


def _clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


class LeftRail(Rail):
    """The record: what has happened, what you are watching, where you are standing.

    Collapsible, because on a narrow window the map matters more than the feed does --
    see :class:`atlas.ui.widgets.Rail`.
    """

    region_selected = Signal(str)
    #: The reader wants the air situation over the selected province. On the region tab
    #: rather than only on the right-click menu, because a mechanic nobody knows exists is
    #: not discovered by right-clicking to see whether it is there.
    air_requested = Signal(str)
    #: An order chosen from the province panel's buttons (a `mapactions.MapAction`). The
    #: window writes it into the orders box, exactly as the map's right-click menu does.
    order_chosen = Signal(object)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__("The record", width=scaled(348), opens_right=False, theme=theme,
                         parent=parent)
        self._theme = theme
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._observer: str | None = None
        self._filter = "all"
        self._selected_region: str | None = None

        layout = self.body

        # "Focus" is the powers the engine follows closely; to a player it is who matters.
        self._tabs = _TabStrip(("Events", "Powers", "Region", "Stats"), theme=theme)
        self._tabs.changed.connect(self._on_tab)
        layout.addWidget(self._tabs)
        layout.addWidget(Divider(theme=theme))

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        layout.addWidget(self._stack, 1)

        self._events_page = self._build_events()
        self._focus_page, self._focus_body = self._build_scroll_page()
        self._region_page, self._region_body = self._build_scroll_page()
        self._stats_page, self._stats_body = self._build_scroll_page()
        for page in (self._events_page, self._focus_page, self._region_page, self._stats_page):
            self._stack.addWidget(page)

        self.apply_theme(theme)

    # -- construction ------------------------------------------------------

    def _build_scroll_page(self) -> tuple[QWidget, QVBoxLayout]:
        area, _body, layout = _scroller(self._theme)
        return area, layout

    def _build_events(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 12, 16, 12)
        bar_layout.setSpacing(5)
        self._filter_chips: dict[str, Chip] = {}
        for key, label in _FILTERS:
            chip = Chip(label, theme=self._theme, selected=(key == "all"),
                        padding=(9, 5))
            chip.clicked.connect(lambda k=key: self._set_filter(k))
            self._filter_chips[key] = chip
            bar_layout.addWidget(chip)
        bar_layout.addStretch(1)
        layout.addWidget(bar)
        layout.addWidget(Divider(soft=True, theme=self._theme))

        area, _body, self._events_body = _scroller(self._theme)
        layout.addWidget(area, 1)
        return page

    # -- binding -----------------------------------------------------------

    def bind(self, conn: sqlite3.Connection, world_id: str, observer: str | None) -> None:
        self._conn, self._world_id, self._observer = conn, world_id, observer
        self.refresh()

    def set_selected_region(self, region_id: str | None) -> None:
        self._selected_region = region_id
        if region_id:
            self._tabs.select(2)
            self._stack.setCurrentIndex(2)
        self._refresh_region()

    def refresh(self) -> None:
        if self._conn is None:
            return
        self._refresh_events()
        self._refresh_focus()
        self._refresh_region()
        self._refresh_stats()

    def _on_tab(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for name, chip in self._filter_chips.items():
            chip.set_selected(name == key)
        self._refresh_events()

    # -- events ------------------------------------------------------------

    def _refresh_events(self) -> None:
        _clear(self._events_body)
        if self._conn is None:
            return

        sql = """
            SELECT e.id, e.turn, e.date, e.kind, e.text, e.actor_nation_id, e.origin,
                   n.name AS actor_name
              FROM event e LEFT JOIN nation n ON n.id = e.actor_nation_id
             WHERE e.world_id = ?
        """
        params: list = [self._world_id]
        if self._observer:
            # The feed is viewer-scoped like every other view.
            sql += """
               AND (e.visibility = 'public' OR e.actor_nation_id = ?
                    OR EXISTS (SELECT 1 FROM event_visibility v
                                WHERE v.event_id = e.id AND v.nation_id = ?))
            """
            params += [self._observer, self._observer]
        kinds = _FILTER_KINDS.get(self._filter)
        if kinds:
            sql += f" AND e.kind IN ({','.join('?' * len(kinds))})"
            params += list(kinds)
        sql += " ORDER BY e.turn DESC, e.id DESC LIMIT 800"

        rows = self._conn.execute(sql, tuple(params)).fetchall()
        # Other powers' routine administration and the engine's own notices, unless
        # technical details are on. See `atlas.ui.plain`.
        from .plain import plain_line, routine_event

        rows = [row for row in rows if not routine_event(
            row["kind"], row["text"], row["actor_nation_id"], self._observer, row["origin"])]
        if not rows:
            self._events_body.addWidget(
                TextLabel("Nothing recorded yet.", style="prose", colour="ink_faint",
                          theme=self._theme, wrap=True)
            )
            self._events_body.addStretch(1)
            return

        current_turn = None
        for row in rows:
            if row["turn"] != current_turn:
                current_turn = row["turn"]
                self._events_body.addWidget(
                    _TurnHeading(f"T{current_turn}", _short_date(row["date"]), theme=self._theme)
                )
            family, colour = _KIND_FAMILY.get(row["kind"], ("NOTE", "ink_dim"))
            self._events_body.addWidget(
                _EventRow(
                    family, colour, plain_line(row["actor_name"] or "The world"),
                    plain_line(row["text"]), theme=self._theme,
                )
            )
        self._events_body.addStretch(1)

    # -- focus -------------------------------------------------------------

    def _refresh_focus(self) -> None:
        _clear(self._focus_body)
        if self._conn is None:
            return

        self._focus_body.addWidget(
            TextLabel(
                f"Up to {FOCUS_CAP} powers act on their own each turn, with their own "
                "goals. The rest of the world carries on in the background.",
                style="prose", colour="ink_dim", theme=self._theme, wrap=True,
            )
        )
        self._focus_body.addSpacing(14)

        tracked = tracked_nations(self._conn, self._world_id)
        slots = QHBoxLayout()
        slots.setSpacing(3)
        for index in range(FOCUS_CAP):
            pip = QFrame()
            pip.setFixedHeight(4)
            filled = index < len(tracked)
            pip.setStyleSheet(
                f"background: {self._theme.brass if filled else self._theme.rule};"
            )
            slots.addWidget(pip, 1)
        count = TextLabel(f"{len(tracked)} / {FOCUS_CAP}", style="num", colour="brass",
                          theme=self._theme)
        slots.addWidget(count)
        self._focus_body.addLayout(slots)
        self._focus_body.addSpacing(10)

        colours = {
            row["id"]: row["colour"]
            for row in self._conn.execute(
                "SELECT id, colour FROM nation WHERE world_id = ?", (self._world_id,)
            )
        }
        for entry in tracked:
            self._focus_body.addWidget(
                _FocusRow(entry, colours.get(entry["id"], "#888888"), theme=self._theme)
            )

        if not tracked:
            self._focus_body.addWidget(
                TextLabel("No power is tracked yet.", style="prose", colour="ink_faint",
                          theme=self._theme)
            )

        ranked = rank_candidates(self._conn, self._world_id, player_nation=self._observer)
        waiting = [c for c in ranked if c.score > 0 and c.nation_id not in {t["id"] for t in tracked}]
        if waiting:
            self._focus_body.addSpacing(14)
            self._focus_body.addWidget(
                TextLabel(
                    "Next in line if a slot frees: " + ", ".join(c.name for c in waiting[:3]),
                    style="body_small", colour="ink_faint", theme=self._theme, wrap=True,
                )
            )
        self._focus_body.addStretch(1)

    # -- region ------------------------------------------------------------

    def _refresh_region(self) -> None:
        _clear(self._region_body)
        if self._conn is None:
            return
        if not self._selected_region:
            self._region_body.addWidget(
                TextLabel("Click a region on the map to inspect it.", style="prose",
                          colour="ink_faint", theme=self._theme, wrap=True)
            )
            self._region_body.addStretch(1)
            return

        row = self._conn.execute(
            """
            SELECT r.id, r.name, r.terrain, r.kind, r.population, r.development,
                   n.name AS owner, n.colour AS colour, r.controller_nation_id
              FROM region r LEFT JOIN nation n ON n.id = r.owner_nation_id
             WHERE r.id = ?
            """,
            (self._selected_region,),
        ).fetchone()
        if row is None:
            return

        self._region_body.addWidget(SectionLabel("Selected region", theme=self._theme))
        self._region_body.addSpacing(10)
        self._region_body.addWidget(
            TextLabel(row["name"], style="heading", colour="ink_bright", theme=self._theme, wrap=True)
        )
        self._region_body.addSpacing(8)

        owner_row = QHBoxLayout()
        owner_row.setSpacing(8)
        swatch = QFrame()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(f"background: {row['colour'] or self._theme.ink_ghost};")
        owner_row.addWidget(swatch)
        owner_row.addWidget(
            TextLabel(row["owner"] or "Unclaimed", style="prose", colour="ink_body", theme=self._theme)
        )
        owner_row.addStretch(1)
        self._region_body.addLayout(owner_row)
        self._region_body.addSpacing(18)

        if row["controller_nation_id"] and row["controller_nation_id"] != row["owner"]:
            self._region_body.addWidget(
                _Notice("Occupied, not annexed. Ownership has not changed.",
                        tone="warn", theme=self._theme)
            )
            self._region_body.addSpacing(16)

        self._add_region_orders()

        # **Rounded, and given no unit it does not have.** Both of these are the engine's
        # own abstract quantities -- the world's population sums to about 3,600 of
        # whatever they are -- so the honest presentation is a whole number and the stored
        # figure on the hover, not an invented "million". `round(value, 1)` used to reach
        # the label and `:g` printed whatever came out of it.
        for label, value, ceiling in (
            ("Population", row["population"], 120.0),
            ("Development", row["development"], 10.0),
        ):
            bar = StatBar(label, theme=self._theme)
            bar.set_value(value, value / ceiling if ceiling else 0, self._theme.info)
            bar.setToolTip(f"{label} of this province, exactly: {exact(value)}")
            self._region_body.addWidget(bar)
            self._region_body.addSpacing(12)

        units = self._conn.execute(
            """
            SELECT n.name, n.colour, COUNT(*) AS n, SUM(u.strength) AS strength
              FROM unit u JOIN nation n ON n.id = u.nation_id
             WHERE u.region_id = ? GROUP BY n.id
            """,
            (self._selected_region,),
        ).fetchall()
        if units:
            self._region_body.addWidget(SectionLabel("Forces present", theme=self._theme))
            self._region_body.addSpacing(10)
            for unit in units:
                self._region_body.addWidget(
                    _ForceRow(unit["name"], unit["colour"], unit["strength"], theme=self._theme)
                )
                self._region_body.addSpacing(6)

        # **Who holds the sky, on the province panel, where it was never said.** The map has
        # had an Air layer since the layer shipped and the region tab has never mentioned
        # the air war at all -- so a player looking at a province they cannot supply had
        # nothing here to tell them why. One line and a way in: the panel is the whole
        # answer and this is the only thing on the tab that has to say it exists.
        from ..engine.air import _CONTROL_FLOOR, control_of

        holder, margin = control_of(self._conn, self._selected_region)
        self._region_body.addSpacing(16)
        self._region_body.addWidget(SectionLabel("The sky over it", theme=self._theme))
        self._region_body.addSpacing(8)
        if holder and margin >= _CONTROL_FLOOR:
            whose = self._conn.execute(
                "SELECT name FROM nation WHERE id = ?", (holder,)).fetchone()
            said = (f"{whose['name'] if whose else holder} holds it "
                    f"(margin {margin:.2f}).")
        else:
            said = "Nobody holds it."
        self._region_body.addWidget(
            TextLabel(said, style="prose", colour="ink_body", theme=self._theme, wrap=True))
        self._region_body.addSpacing(8)
        chip = Chip("Air situation", theme=self._theme, padding=(12, 5))
        chip.setToolTip(
            "Who holds the sky, what it is doing to battles, advance and supply, and what "
            "can be done about it")
        chip.clicked.connect(
            lambda r=self._selected_region: self.air_requested.emit(r))
        # **Not `row`.** That name is the region's own `sqlite3.Row`, read again below for
        # the id, the terrain and the border count -- shadowing it with a layout made every
        # province panel raise `TypeError` the moment one was selected. Caught by
        # `test_shell.test_left_rail_selecting_a_region_opens_the_inspector`.
        chip_row = QHBoxLayout()
        chip_row.addWidget(chip)
        chip_row.addStretch(1)
        self._region_body.addLayout(chip_row)

        borders = self._conn.execute(
            "SELECT COUNT(*) FROM region_adjacency WHERE region_a = ? OR region_b = ?",
            (self._selected_region, self._selected_region),
        ).fetchone()[0]
        self._region_body.addSpacing(10)
        self._region_body.addWidget(
            TextLabel(f"{row['id']} · borders {count(borders)} region(s) · {row['terrain']}"
                      if technical() else
                      f"{str(row['terrain']).capitalize()} · borders {count(borders)}",
                      style="mono", colour="ink_ghost", theme=self._theme)
        )
        self._region_body.addStretch(1)

    def _add_region_orders(self) -> None:
        """What you can order here, as buttons: the map's right-click menu, where it shows.

        A Mac has no obvious right click, and a menu nobody opens is a feature nobody has.
        Each button writes its order into the orders box (see `AtlasWindow._write_order`),
        so a click and a typed sentence end up in the same place and read the same way.
        """
        if not self._observer:
            return
        from ..engine.mapactions import actions_for_region

        found = actions_for_region(self._conn, self._world_id, self._observer,
                                   self._selected_region)
        if not found:
            return
        self._region_body.addWidget(SectionLabel("Orders here", theme=self._theme))
        self._region_body.addSpacing(8)
        for entry in [e for e in found if e.enabled]:
            label = entry.label + ("…" if entry.needs else "")
            chip = Chip(label, theme=self._theme, padding=(12, 5))
            chip.setToolTip(
                ("Then click the province your forces should come from. " if entry.needs
                 else "") + "Adds this to your orders box. Nothing happens until you advance.")
            chip.clicked.connect(lambda e=entry: self.order_chosen.emit(e))
            line = QHBoxLayout()
            line.addWidget(chip)
            line.addStretch(1)
            self._region_body.addLayout(line)
            self._region_body.addSpacing(5)
        # Not possible yet, grouped by reason: "Air strike, Incite, Sabotage: only open
        # against somebody you are at war with" is one fact, not three.
        reasons: dict[str, list[str]] = {}
        for entry in (e for e in found if not e.enabled):
            what = entry.label.split(" in ")[0] if " in " in entry.label else entry.label
            why = entry.why_not.rstrip(".")
            _, _, rest = why.partition(" is only ")
            reasons.setdefault(f"only {rest}" if rest else why, []).append(what)
        for said, acts in reasons.items():
            self._region_body.addWidget(TextLabel(
                f"Not yet: {', '.join(acts)}. {said[:1].upper() + said[1:]}.",
                style="body_small", colour="ink_faint", theme=self._theme, wrap=True))
            self._region_body.addSpacing(3)
        self._region_body.addSpacing(16)

    # -- stats -------------------------------------------------------------

    def _refresh_stats(self) -> None:
        _clear(self._stats_body)
        if self._conn is None:
            return

        from ..engine.statistics import collect_match_stats

        stats = collect_match_stats(self._conn, self._world_id)
        self._stats_body.addWidget(_Notice(stats.caveat, tone="info", theme=self._theme))
        self._stats_body.addSpacing(20)

        if stats.profiles:
            self._stats_body.addWidget(SectionLabel("Behaviour by bloc", theme=self._theme))
            self._stats_body.addSpacing(12)
            for profile in stats.profiles:
                self._stats_body.addWidget(_BlocCard(profile, theme=self._theme))
                self._stats_body.addSpacing(12)

        rows = self._conn.execute(
            """
            SELECT role, provider, requested_model, audit_verdict,
                   COUNT(*) AS n, AVG(duration_ms) AS ms
              FROM routing_log WHERE world_id = ?
             GROUP BY role, provider, requested_model
             ORDER BY n DESC LIMIT 20
            """,
            (self._world_id,),
        ).fetchall()
        if rows:
            self._stats_body.addWidget(SectionLabel("Model routing", theme=self._theme))
            self._stats_body.addSpacing(10)
            for row in rows:
                self._stats_body.addWidget(_RoutingRow(row, theme=self._theme))
        self._stats_body.addStretch(1)

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"LeftRail {{ background: {theme.panel}; border-right: 1px solid {theme.rule}; }}"
        )
        restyle(self, theme)
        # The rows themselves are rebuilt rather than re-themed: they are generated from
        # the database on every refresh anyway, so regenerating is simpler than teaching
        # each one to change colour, and cannot leave a stale row behind.
        self.refresh()


# ---------------------------------------------------------------------------
# Rows and cards
# ---------------------------------------------------------------------------


class _TabStrip(QWidget):
    changed = Signal(int)

    def __init__(self, labels: tuple[str, ...], *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._index = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._tabs: list[_Tab] = []
        for position, label in enumerate(labels):
            tab = _Tab(label, theme=theme, active=(position == 0))
            tab.clicked.connect(lambda p=position: self.select(p))
            self._tabs.append(tab)
            layout.addWidget(tab, 1)

    def select(self, index: int) -> None:
        self._index = index
        for position, tab in enumerate(self._tabs):
            tab.set_active(position == index)
        self.changed.emit(index)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for tab in self._tabs:
            tab.apply_theme(theme)


class _Tab(QFrame):
    clicked = Signal()

    def __init__(self, label: str, *, theme: Theme, active: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = active
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 10)
        layout.setSpacing(6)
        self._label = TextLabel(label, style="tab", theme=theme)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_theme(theme)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.apply_theme(self._theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        rule = theme.brass if self._active else "transparent"
        background = theme.inset if self._active else "transparent"
        self.setStyleSheet(
            f"_Tab {{ background: {background}; border-bottom: 2px solid {rule}; }}"
        )
        self._label.set_colour("ink" if self._active else "ink_faint", theme)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _TurnHeading(QWidget):
    """The sticky-looking turn divider between groups of events."""

    def __init__(self, turn: str, date: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 10)
        layout.setSpacing(10)
        self._turn = TextLabel(turn, style="num", colour="brass", theme=theme)
        self._date = TextLabel(date, style="label", colour="ink_ghost", theme=theme)
        self._rule = Divider(soft=True, theme=theme)
        layout.addWidget(self._turn)
        layout.addWidget(self._date)
        layout.addWidget(self._rule, 1)

    def apply_theme(self, theme: Theme) -> None:
        for child in (self._turn, self._date, self._rule):
            child.apply_theme(theme)


class _EventRow(QWidget):
    """A diamond, a kind chip, an actor, and the sentence itself."""

    def __init__(self, kind: str, colour_key: str, actor: str, text: str,
                 *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour_key = colour_key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(12)

        self._marker = _Diamond(theme, colour_key)
        marker_column = QVBoxLayout()
        marker_column.setContentsMargins(0, 4, 0, 0)
        marker_column.addWidget(self._marker)
        marker_column.addStretch(1)
        layout.addLayout(marker_column)

        column = QVBoxLayout()
        column.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._kind = TextLabel(kind, style="label", colour=colour_key, theme=theme)
        self._actor = TextLabel(actor, style="mono_small", colour="ink_ghost", theme=theme)
        head.addWidget(self._kind)
        head.addWidget(self._actor)
        head.addStretch(1)
        column.addLayout(head)
        self._text = TextLabel(text, style="prose", colour="ink_body", theme=theme, wrap=True)
        column.addWidget(self._text)
        layout.addLayout(column, 1)

    def apply_theme(self, theme: Theme) -> None:
        self._marker.apply_theme(theme)
        self._kind.set_colour(self._colour_key, theme)
        self._actor.apply_theme(theme)
        self._text.apply_theme(theme)


class _Diamond(QWidget):
    def __init__(self, theme: Theme, colour_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._colour_key = colour_key
        self.setFixedSize(8, 8)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QPainter, QPolygonF

        colour = getattr(self._theme, self._colour_key, self._theme.ink_dim)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(colour))
        painter.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        painter.drawPolygon(
            QPolygonF([QPointF(w / 2, 0), QPointF(w, h / 2), QPointF(w / 2, h), QPointF(0, h / 2)])
        )
        painter.end()


class _FocusRow(QWidget):
    def __init__(self, entry: dict, colour: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 14)
        layout.setSpacing(12)

        self._shield = ShieldSwatch(colour, width=18, height=24)
        layout.addWidget(self._shield, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._name = TextLabel(entry["name"], style="row", colour="ink", theme=theme)
        self._since = TextLabel(f"since t{entry['since_turn']}", style="mono_small",
                                colour="ink_ghost", theme=theme)
        head.addWidget(self._name)
        head.addStretch(1)
        head.addWidget(self._since)
        column.addLayout(head)
        self._reason = TextLabel(entry["reason"], style="body_small", colour="ink_dim",
                                 theme=theme, wrap=True)
        column.addWidget(self._reason)
        layout.addLayout(column, 1)

        self._rule = Divider(soft=True, theme=theme)

    def apply_theme(self, theme: Theme) -> None:
        for child in (self._name, self._since, self._reason):
            child.apply_theme(theme)


class _ForceRow(QFrame):
    def __init__(self, nation: str, colour: str, strength: float, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(10)
        swatch = QFrame()
        swatch.setFixedSize(11, 11)
        swatch.setStyleSheet(f"background: {colour};")
        layout.addWidget(swatch)
        self._nation = TextLabel(nation, style="body", colour="ink_body", theme=theme)
        self._strength = TextLabel(force_of(strength), style="num", colour="brass",
                                   theme=theme)
        self._strength.setToolTip(f"{exact(strength)} strength")
        layout.addWidget(self._nation, 1)
        layout.addWidget(self._strength)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(f"_ForceRow {{ background: {theme.inset}; border: 1px solid {theme.rule_soft}; }}")
        for child in (self._nation, self._strength):
            child.apply_theme(theme)


class _Notice(QFrame):
    """The bordered callout used for warnings and the statistics caveat."""

    def __init__(self, text: str, *, tone: str, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = tone
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self._text = TextLabel(text, style="body", theme=theme, wrap=True)
        layout.addWidget(self._text, 1)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        if self._tone == "warn":
            border, background, ink = "#7A4A28", "#221609", "#E4C58A"
            if not theme.is_dark:
                border, background, ink = theme.brass, theme.brass_wash, theme.brass_hot
        else:
            border, background, ink = "#2E4560", "#101A26", "#A8C4DD"
            if not theme.is_dark:
                border, background, ink = theme.info, "#E4EDF5", theme.info
        self.setStyleSheet(f"_Notice {{ border: 1px solid {border}; background: {background}; }}")
        self._text.set_colour(ink, theme)


class _BlocCard(QFrame):
    def __init__(self, profile, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(11)

        head = QHBoxLayout()
        head.setSpacing(9)
        self._name = TextLabel(profile.provider, style="body", colour="ink", theme=theme)
        self._confidence = Chip(f"{profile.confidence} confidence", theme=theme,
                                style="label", padding=(6, 3))
        head.addWidget(self._name)
        head.addStretch(1)
        head.addWidget(self._confidence)
        layout.addLayout(head)

        counts = QHBoxLayout()
        counts.setSpacing(10)
        self._counts = []
        for value, label, colour in (
            (profile.wars_declared, "wars declared", "danger"),
            (profile.alliances_formed, "alliances", "good"),
            (profile.treaties_broken, "agreements broken", "brass"),
        ):
            column = QVBoxLayout()
            column.setSpacing(4)
            number = TextLabel(str(value), style="row", colour=colour, theme=theme)
            caption = TextLabel(label, style="mono_small", colour="ink_faint", theme=theme, wrap=True)
            column.addWidget(number)
            column.addWidget(caption)
            counts.addLayout(column, 1)
            self._counts += [number, caption]
        layout.addLayout(counts)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(f"_BlocCard {{ border: 1px solid {theme.rule_soft}; background: {theme.inset}; }}")
        self._name.apply_theme(theme)
        self._confidence.apply_theme(theme)
        for child in self._counts:
            child.apply_theme(theme)


class _RoutingRow(QWidget):
    def __init__(self, row, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 9, 0, 9)
        layout.setSpacing(10)

        column = QVBoxLayout()
        column.setSpacing(3)
        self._role = TextLabel(f"{row['role']}  ×{count(row['n'])}", style="body_small",
                               colour="ink_body", theme=theme)
        self._model = TextLabel(f"{row['provider']} / {row['requested_model']}",
                                style="mono_small", colour="ink_ghost", theme=theme)
        column.addWidget(self._role)
        column.addWidget(self._model)
        layout.addLayout(column, 1)

        self._timing = TextLabel(duration(float(row["ms"] or 0) / 1000.0), style="mono",
                                 colour="ink_faint", theme=theme)
        self._timing.setToolTip(
            f"{float(row['ms'] or 0) / 1000.0:.1f} seconds on average, over "
            f"{count(row['n'])} call(s)")
        layout.addWidget(self._timing)

        verdict = row["audit_verdict"] or "-"
        self._verdict = TextLabel(verdict, style="label",
                                  colour={"MATCH": "good", "MISMATCH": "danger"}.get(verdict, "ink_faint"),
                                  theme=theme)
        layout.addWidget(self._verdict)

    def apply_theme(self, theme: Theme) -> None:
        for child in (self._role, self._model, self._timing, self._verdict):
            child.apply_theme(theme)


def _short_date(iso: str) -> str:
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"
