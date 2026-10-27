"""The turn summary as a briefing: what happened to you, your orders, your wars, the world.

The technical summary is three columns of the engine's bookkeeping, and it is the right
view for whoever is tuning the engine. A player reading it had to find their own country
in nine undifferentiated lines, read the same event three times in three places, and work
out from "1 province(s) changed hands" whether they were winning. This is the same report
read for them, in one column, in the order a player asks the questions:

1. **For you** -- what happened to your country this turn.
2. **Your orders** -- each sentence you wrote, and whether it worked.
3. **Your wars** -- who you are fighting and how it is going, in provinces.
4. **Around the world** -- a handful of the gravest things that happened to others.
5. **What you can do now** -- the moves the rules have just opened (an annexation).

:func:`build_brief` decides the content and is a pure read; :class:`BriefView` draws it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .plain import plain_line
from .theme import Theme
from .widgets import PressFeedback, SectionLabel, TextLabel, clear_layout, restyle

__all__ = ["Brief", "BriefLine", "BriefView", "WarState", "build_brief"]

#: How many lines each section may show. A briefing that grew to thirty lines would be the
#: columns it replaces.
FOR_YOU_SHOWN = 5
WORLD_SHOWN = 6

#: The air war, sky by sky: dozens a month in any war with aircraft in it. The fronts say
#: what the sky decided.
_AIR_NOISE = ("the sky over", "the sky.", "fighting in the air", "air superiority",
              "air fighting", "squadrons")


@dataclass(slots=True)
class BriefLine:
    text: str
    #: A second, quieter line: why an order failed, what to write instead.
    detail: str = ""
    #: ``good`` | ``bad`` | ``neutral`` | ``news``.
    tone: str = "news"
    mark: str = ""
    event_id: int | None = None
    region_id: str = ""


@dataclass(slots=True)
class WarState:
    enemy: str
    lines: list[str] = field(default_factory=list)
    #: ``winning`` | ``losing`` | ``even``, for the colour of the heading.
    trend: str = "even"


@dataclass(slots=True)
class Brief:
    for_you: list[BriefLine] = field(default_factory=list)
    orders: list[BriefLine] = field(default_factory=list)
    wars: list[WarState] = field(default_factory=list)
    world: list[BriefLine] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    more_for_you: int = 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().rstrip(".").lower())


def _covered(line: str, seen: list[str]) -> bool:
    """Whether ``line`` says nothing a line already shown has not."""
    said = _norm(line)
    return any(said == other or said.startswith(other) or other.startswith(said)
               for other in seen)


def _sentence(text: str) -> str:
    text = plain_line(str(text)).strip()
    return text[:1].upper() + text[1:] if text else text


def _name(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return str(row[0]) if row else nation_id


def _count(conn, sql: str, *params) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def _provinces(n: int) -> str:
    return "none" if n == 0 else ("1 province" if n == 1 else f"{n} provinces")


def _wars(conn: sqlite3.Connection, world_id: str, me: str) -> list[WarState]:
    land = "kind <> 'ocean'"
    mine = float(conn.execute(
        "SELECT COALESCE(SUM(strength), 0) FROM unit WHERE world_id = ? AND nation_id = ?",
        (world_id, me)).fetchone()[0] or 0)
    out: list[WarState] = []
    for row in conn.execute(
            "SELECT r.nation_b AS id, n.name FROM relation r"
            " JOIN nation n ON n.id = r.nation_b AND n.world_id = r.world_id"
            " WHERE r.world_id = ? AND r.nation_a = ? AND r.at_war = 1 AND n.is_alive = 1"
            " ORDER BY n.name", (world_id, me)).fetchall():
        enemy, name = str(row["id"]), str(row["name"])
        total = _count(conn, f"SELECT COUNT(*) FROM region WHERE world_id = ? AND {land}"
                             " AND owner_nation_id = ?", world_id, enemy)
        taken = _count(conn, f"SELECT COUNT(*) FROM region WHERE world_id = ? AND {land}"
                             " AND owner_nation_id = ? AND controller_nation_id = ?",
                       world_id, enemy, me)
        lost = _count(conn, f"SELECT COUNT(*) FROM region WHERE world_id = ? AND {land}"
                            " AND owner_nation_id = ? AND controller_nation_id = ?",
                      world_id, me, enemy)
        holds = _count(conn, f"SELECT COUNT(*) FROM region WHERE world_id = ? AND {land}"
                             " AND COALESCE(controller_nation_id, owner_nation_id) = ?",
                       world_id, enemy)
        theirs = float(conn.execute(
            "SELECT COALESCE(SUM(strength), 0) FROM unit WHERE world_id = ? AND nation_id = ?",
            (world_id, enemy)).fetchone()[0] or 0)

        state = WarState(enemy=name)
        state.lines.append(
            (f"You occupy {taken or 'none'} of its {_provinces(total)}. "
             if total else "It owns no provinces any more. ")
            + f"It occupies {_provinces(lost)} of yours.")
        for fight in conn.execute(
                "SELECT c.pressure, c.attacker_id, g.name FROM contest c"
                " JOIN region g ON g.id = c.region_id AND g.world_id = c.world_id"
                " WHERE c.world_id = ? AND ((c.attacker_id = ? AND c.defender_id = ?)"
                "   OR (c.attacker_id = ? AND c.defender_id = ?))"
                " ORDER BY c.pressure DESC LIMIT 3",
                (world_id, me, enemy, enemy, me)).fetchall():
            share = f"{float(fight['pressure']):.0%}"
            if str(fight["attacker_id"]) == me:
                state.lines.append(f"Attacking {fight['name']}: {share} of the way to taking it.")
            else:
                state.lines.append(f"{name} is attacking {fight['name']}: "
                                   f"{share} of the way to taking it.")
        if theirs <= 0 and mine > 0:
            balance = "It has no forces left in the field."
        elif mine <= 0:
            balance = "You have no forces in the field."
        else:
            ratio = mine / theirs
            balance = ("Your forces far outnumber its own." if ratio >= 2 else
                       "Your forces are stronger than its own." if ratio >= 1.2 else
                       "Your forces and its own are evenly matched." if ratio > 0.83 else
                       "Its forces are stronger than yours." if ratio > 0.5 else
                       "Its forces far outnumber yours.")
        state.lines.append(balance)
        if holds == 0:
            state.lines.append(f"{name} holds no ground of its own: it cannot go on.")
        state.trend = ("winning" if taken > lost or (holds == 0) else
                       "losing" if lost > taken else "even")
        out.append(state)
    return out


def _next_steps(conn: sqlite3.Connection, world_id: str, me: str,
                wars: list[WarState]) -> list[str]:
    from ..engine.conquest import annexable

    steps: list[str] = []
    for row in conn.execute(
            "SELECT DISTINCT r.owner_nation_id AS id FROM region r"
            " WHERE r.world_id = ? AND r.controller_nation_id = ?"
            "   AND r.owner_nation_id IS NOT NULL AND r.owner_nation_id <> ?"
            "   AND r.kind <> 'ocean' ORDER BY id", (world_id, me, me)).fetchall():
        target = str(row["id"])
        regions, _why = annexable(conn, world_id, me, target)
        if regions:
            them = _name(conn, world_id, target)
            steps.append(f"You can annex {them} now: write “Annex {them}”.")
    for war in wars:
        if any(line.startswith("You occupy none") for line in war.lines) and not any(
                line.startswith("Attacking") for line in war.lines):
            steps.append(f"To take {war.enemy}'s land, send your forces in: "
                         f"“Invade {war.enemy}”.")
    return steps


def build_brief(report, headlines: list[str], *, conn: sqlite3.Connection | None,
                world_id: str, viewer: str | None, affected: list | None = None) -> Brief:
    """The briefing for ``viewer``. ``report`` is already read as the viewer may read it."""
    brief = Brief()
    me = _name(conn, world_id, viewer) if conn is not None and viewer else ""
    seen: list[str] = []

    def about_me(line: str) -> bool:
        return bool(me) and me.lower() in line.lower()

    def readable(text: str) -> bool:
        # The engine's working, or a paragraph: neither is a line in a briefing.
        return bool(text) and len(text) <= 220 and " -> " not in text and "**" not in text

    def airy(text: str) -> bool:
        lowered = text.lower()
        return any(noise in lowered for noise in _AIR_NOISE)

    # 1. For you: the turn's gravest lines that name you, then what the attention ranking
    #    says changed for you, then the event headlines. One line per fact, however many
    #    places reported it.
    # The order lines are told under "Your orders", in the player's words.
    told_as_orders = {str(line) for line in getattr(report, "order_outcomes", []) or []}
    ranked = [line for line in report.significant() if line not in told_as_orders]
    for line in ranked:
        text = _sentence(line)
        if not readable(text) or not about_me(text) or airy(text) or _covered(text, seen):
            continue
        seen.append(_norm(text))
        brief.for_you.append(BriefLine(text))
    for item in affected or []:
        text = _sentence(item.subject)
        if not readable(text) or airy(text) or _covered(text, seen):
            continue
        seen.append(_norm(text))
        brief.for_you.append(BriefLine(
            text, tone="news",
            event_id=item.source_event_ids[0] if item.source_event_ids else None))
    for line in headlines:
        text = _sentence(line)
        if not readable(text) or not about_me(text) or airy(text) or _covered(text, seen):
            continue
        seen.append(_norm(text))
        brief.for_you.append(BriefLine(text))
    if len(brief.for_you) > FOR_YOU_SHOWN:
        brief.more_for_you = len(brief.for_you) - FOR_YOU_SHOWN
        brief.for_you = brief.for_you[:FOR_YOU_SHOWN]

    # 2. Your orders, each with its outcome. A result already told above is "done".
    for outcome in _outcomes(report):
        said = (outcome.order_text or outcome.action_type.replace("_", " ")).strip()
        said = said[:1].upper() + said[1:]
        if outcome.status == "refused":
            hint = outcome.suggestion if outcome.suggestion and "_" not in outcome.suggestion \
                and "by id" not in outcome.suggestion else ""
            detail = _sentence(outcome.reason).rstrip(".") + "." if outcome.reason else ""
            if hint:
                detail += f" Try: {hint}."
            brief.orders.append(BriefLine(said, detail=detail, tone="bad", mark="✗"))
        elif outcome.status == "not_attempted":
            brief.orders.append(BriefLine(
                said, detail="Nothing in the game carries this out yet.", tone="neutral",
                mark="–"))
        else:
            result = _sentence(outcome.result) if outcome.result else ""
            detail = "" if not result or _covered(result, seen) else result
            brief.orders.append(BriefLine(said, detail=detail, tone="good", mark="✓"))
    if not brief.orders:
        for line in getattr(report, "your_orders", []) or []:
            brief.orders.append(BriefLine(str(line), tone="neutral"))

    # 3. Your wars, and 5. what the rules have just opened.
    if conn is not None and viewer:
        brief.wars = _wars(conn, world_id, viewer)
        brief.next_steps = _next_steps(conn, world_id, viewer, brief.wars)

    # 4. Around the world: an allow-list, not a block-list. Only the kinds of fact that
    #    change the map or the balance of power -- the report's gravest fields, then this
    #    turn's public events of the same kinds -- so the plumbing of the world economy
    #    cannot leak in under a phrasing nobody thought to filter.
    candidates: list[str] = []
    for name in _GRAVE_FIELDS:
        candidates += [str(entry) for entry in getattr(report, name, []) or []]
    candidates += [f"{entry} has ceased to exist" for entry in
                   getattr(report, "eliminated", []) or []]
    if conn is not None:
        candidates += _grave_events(conn, world_id, viewer, getattr(report, "turn", None))
    for line in candidates:
        text = _sentence(line)
        if not readable(text) or about_me(text) or airy(text) or _covered(text, seen):
            continue
        seen.append(_norm(text))
        brief.world.append(BriefLine(text))
        if len(brief.world) >= WORLD_SHOWN:
            break
    return brief


#: The report's fields that change the map or the balance of power, gravest first.
_GRAVE_FIELDS = ("nuclear", "victories", "annexed", "capitulated", "betrayed", "fell",
                 "shocks", "freed", "rebelled", "pacts_invoked", "sessions", "exposed")

#: This turn's events of the same order, for what the report keeps only as a count.
_GRAVE_KINDS = ("war_declared", "peace_signed", "ceasefire", "territory_transfer",
                "alliance_formed", "alliance_broken", "treaty_broken", "revolution", "coup",
                "leader_change", "famine", "plague", "disaster")


def _grave_events(conn: sqlite3.Connection, world_id: str, viewer: str | None,
                  turn: int | None) -> list[str]:
    if turn is None:
        return []
    marks = ",".join("?" for _ in _GRAVE_KINDS)
    sql = (f"SELECT e.text FROM event e WHERE e.world_id = ? AND e.turn = ?"
           f" AND e.kind IN ({marks})")
    params: list = [world_id, turn, *_GRAVE_KINDS]
    if viewer:
        sql += (" AND (e.visibility = 'public' OR e.actor_nation_id = ?"
                " OR EXISTS (SELECT 1 FROM event_visibility v"
                "            WHERE v.event_id = e.id AND v.nation_id = ?))")
        params += [viewer, viewer]
    sql += " ORDER BY e.id LIMIT 40"
    return [str(row[0]) for row in conn.execute(sql, tuple(params))]


def _outcomes(report) -> list:
    return list(getattr(report, "order_records", None) or [])


# ---------------------------------------------------------------------------
# Drawing it
# ---------------------------------------------------------------------------

_TONE = {"good": "good", "bad": "danger", "neutral": "ink_faint", "news": "brass"}


class _Row(PressFeedback, QFrame):
    """One line of the briefing: a coloured edge, the text, and an optional second line."""

    clicked = Signal()

    def __init__(self, line: BriefLine, *, theme: Theme, clickable: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line = line
        self._clickable = clickable
        self.setObjectName("briefRow")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Show what caused this")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 12, 8)
        row.setSpacing(10)
        self._mark = None
        if line.mark:
            self._mark = TextLabel(line.mark, style="row_small", colour=_TONE[line.tone],
                                   theme=theme)
            self._mark.setFixedWidth(16)
            row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._text = TextLabel(line.text, style="prose", colour="ink_bright",
                               theme=theme, wrap=True)
        text.addWidget(self._text)
        self._detail = None
        if line.detail:
            self._detail = TextLabel(line.detail, style="body",
                                     colour="danger" if line.tone == "bad" else "ink_dim",
                                     theme=theme, wrap=True)
            text.addWidget(self._detail)
        row.addLayout(text, 1)

    def _takes_press(self, event) -> bool:
        return self._clickable and event.button() == Qt.MouseButton.LeftButton

    def _fire(self) -> None:
        self.clicked.emit()

    def apply_theme(self, theme: Theme) -> None:
        edge = getattr(theme, _TONE.get(self._line.tone, "brass"))
        background = theme.inset if self._line.tone == "news" or self._line.mark else "transparent"
        self.setStyleSheet(
            f"QFrame#briefRow {{ background: {background}; border-left: 3px solid {edge}; }}")
        restyle(self, theme)


class BriefView(QWidget):
    """The briefing, top to bottom. Rebuilt whole for every report."""

    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 14, 0)
        self._layout.setSpacing(0)

    def show_brief(self, brief: Brief, theme: Theme) -> None:
        self._theme = theme
        clear_layout(self._layout)

        def section(title: str) -> None:
            if self._layout.count():
                self._layout.addSpacing(26)
            self._layout.addWidget(SectionLabel(title, theme=theme))
            self._layout.addSpacing(10)

        def note(text: str, colour: str = "ink_dim", style: str = "body") -> None:
            self._layout.addWidget(TextLabel(text, style=style, colour=colour, theme=theme,
                                             wrap=True))

        def row(line: BriefLine) -> None:
            clickable = line.event_id is not None
            widget = _Row(line, theme=theme, clickable=clickable)
            if clickable:
                widget.clicked.connect(lambda e=line.event_id: self.event_selected.emit(e))
            widget.apply_theme(theme)
            self._layout.addWidget(widget)
            self._layout.addSpacing(6)

        section("For you")
        if brief.for_you:
            for line in brief.for_you:
                row(line)
            if brief.more_for_you:
                note(f"and {brief.more_for_you} more in the dispatch.", "ink_faint", "body_small")
        else:
            note("Nothing new happened to your country this turn.")

        if brief.next_steps:
            section("What you can do now")
            for step in brief.next_steps:
                row(BriefLine(step, tone="good", mark="→"))

        section("Your orders")
        if brief.orders:
            for line in brief.orders:
                row(line)
        else:
            note("You gave no orders this turn.")

        if brief.wars:
            section("Your wars")
            for war in brief.wars:
                colour = {"winning": "good", "losing": "danger"}.get(war.trend, "brass")
                self._layout.addWidget(TextLabel(
                    f"At war with {war.enemy}", style="row", colour=colour, theme=theme))
                self._layout.addSpacing(4)
                for line in war.lines:
                    note(line, "ink_body")
                self._layout.addSpacing(12)

        section("Around the world")
        if brief.world:
            for line in brief.world:
                note("•  " + line.text, "ink_body", "prose")
                self._layout.addSpacing(6)
        else:
            note("Elsewhere, a quiet month.")
        self._layout.addStretch(1)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)
