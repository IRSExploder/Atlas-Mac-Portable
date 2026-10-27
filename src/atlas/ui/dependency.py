"""The screen that answers *who economically controls me, why, and what can I do about it*.

The trade layer has worked for a long time and has been almost entirely invisible. A player
was told *short of steel* and never told who was supposed to send it, how much of their
supply that one lane carried, why it stopped, or what a government could do -- so the
economy read as weather, and the only available response was to play on.

**Nothing on this screen is computed here.** Every figure comes through
:func:`atlas.engine.dependency.lens_for` and :func:`atlas.engine.forecast.forecast_for`,
which walk the settlement the engine's own way. A panel with its own idea of what a lane
ships is how an interface comes to lie with a straight face, and the reconciliation tests
exist to stop exactly that.

**Consequence first, figure second**, which is the shape the home-front screen settled on
and the reason it reads: every block leads with what the number *does* -- what stops, what
the yards lose, when the money runs out -- and the arithmetic sits underneath as the
evidence. A block with nothing to say says so in as many words, because a blank reads as a
rendering fault.

**The map draws this nation's lanes and no others.** A world-spanning trade graph is a
hairball nobody can read; the question this panel asks is about one country, so the picture
is one country's lanes, thickness by what moves and colour by what is stopping it. Where the
caller can supply real positions it uses them; where it cannot -- a synthetic world, a test,
a pack that has not loaded -- it lays the partners out around the subject, which answers the
same question without pretending to be geography.

Read-only, like every other overlay. It opens no connection it writes to, and the screenshot
pass asserts the world fingerprint is unmoved by rendering it.
"""

from __future__ import annotations

import math
import sqlite3

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ..engine.dependency import (
    RUNWAY_WORTH_SAYING,
    STATIC_CAVEAT,
    STATUSES,
    Counterfactual,
    DependencyLens,
    GoodPosition,
    Response,
    RouteView,
    Vulnerability,
    lens_for,
    world_revision,
)
from ..engine.forecast import Forecast, forecast_for
from ..humanize import exact, figure, percent
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import SectionLabel, TextLabel, clear_layout

__all__ = ["DependencyOverlay", "anchors_from"]


#: What each lane status is drawn in. **One vocabulary with the alerts panel and the home
#: front**, so *this is bad* looks the same wherever a player meets it. The three reds are
#: not interchangeable: somebody's navy, somebody's decision and somebody's sanction are
#: different problems, and only one of them can be answered by a fleet.
_STATUS_COLOUR: dict[str, str] = {
    "open": "good",
    "barter": "ink_dim",
    "suspended": "brass",
    "priced_out": "brass_hot",
    "embargoed": "danger",
    "sanctioned": "danger",
    "blockaded": "danger",
    "at_war": "danger_hot",
    "unreachable": "ink_ghost",
}

#: The word on the chip. Shorter than :data:`atlas.engine.dependency.STATUSES`, which is the
#: sentence -- the chip is a label and the sentence is the tooltip.
_STATUS_WORD: dict[str, str] = {
    "open": "shipping",
    "barter": "barter",
    "suspended": "stopped on terms",
    "priced_out": "priced out",
    "embargoed": "embargoed",
    "sanctioned": "sanctioned",
    "blockaded": "blockaded",
    "at_war": "at war",
    "unreachable": "unreachable",
}

#: Spelled, because "3 vulnerabilities" is what a template looks like from the outside.
_COUNTED: tuple[str, ...] = ("No", "One", "Two", "Three")


def anchors_from(
    conn: sqlite3.Connection, world_id: str, labels: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Where each nation sits, from the geometry pack's own label points.

    ``labels`` is ``{geo_key: (lon, lat)}`` -- the mapping the main window already builds
    from the pack for its own labelling, passed in rather than re-read, so the lens and the
    map cannot come to different opinions about where a country is.

    A nation with no geometry is simply absent from the result, and the lane map falls back
    to its own layout for it. That is the honest answer for the thirty-odd single-province
    islands the packs never joined to anything.
    """
    found: dict[str, list[tuple[float, float]]] = {}
    for row in conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) AS who, geo_key"
        "  FROM region WHERE world_id = ? AND kind <> 'ocean'", (world_id,)
    ):
        who, key = row["who"], str(row["geo_key"] or "")
        if not who or key not in labels:
            continue
        found.setdefault(str(who), []).append(labels[key])
    return {
        who: (sum(x for x, _y in points) / len(points),
              sum(y for _x, y in points) / len(points))
        for who, points in found.items() if points}


class _LaneMap(QWidget):
    """This nation's lanes, and nobody else's.

    Thickness is what moves, colour is what is stopping it, and the arrow is which way the
    cargo goes. Partners are placed at their real positions where the caller knows them and
    in a ring around the subject where it does not -- and the ring is not a consolation
    prize: it is sorted by volume, so the lane that decides the month is the one at the top
    of the fan whichever layout is in use.
    """

    #: How thick the fattest lane is drawn, and the thinnest. A lane nobody can see is a
    #: lane that is not on the picture, so the floor is well above a hairline.
    _WIDEST = 9.0
    _THINNEST = 1.6

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._subject = ""
        self._lanes: list[tuple[str, float, str, bool, str]] = []
        self._anchors: dict[str, tuple[float, float]] = {}
        self._subject_id = ""
        self.setMinimumHeight(150)
        self.setObjectName("laneMap")

    def show_lanes(self, lens: DependencyLens,
                   anchors: dict[str, tuple[float, float]] | None = None) -> None:
        self._subject = lens.name
        self._subject_id = lens.nation_id
        self._lanes = [
            (view.other_name, view.settlement.volume, view.status, view.inbound,
             view.other_id)
            for view in sorted(lens.routes,
                               key=lambda v: (-v.settlement.volume, v.route_id))]
        self._anchors = dict(anchors or {})
        # As tall as the fan needs and no taller. A fixed strip left a single lane sitting
        # in a field of empty inset, which reads as a panel that failed to draw.
        rows = max(sum(1 for lane in self._lanes if lane[3]),
                   sum(1 for lane in self._lanes if not lane[3]), 1)
        self.setMinimumHeight(min(320, 150 + 42 * (rows - 1)))
        self.setMaximumHeight(min(320, 150 + 42 * (rows - 1)))
        self.setToolTip(
            f"{len(self._lanes)} standing lane(s). Thickness is the monthly volume; "
            "colour is what the lane is currently doing. Only this nation's lanes are "
            "drawn.")
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    # -- placement ---------------------------------------------------------

    def _places(self, width: float, height: float) -> tuple[QPointF, list[QPointF]]:
        """The subject and each partner in widget coordinates.

        Real positions when every partner has one, and a fan when any of them does not:
        a picture that placed half its partners geographically and half of them on a circle
        would be a picture that is lying about the half it invented.
        """
        margin = 62.0
        usable_w, usable_h = max(1.0, width - 2 * margin), max(1.0, height - 2 * margin)
        wanted = [self._subject_id, *[other for _n, _v, _s, _i, other in self._lanes]]
        if self._anchors and all(who in self._anchors for who in wanted):
            xs = [self._anchors[who][0] for who in wanted]
            ys = [self._anchors[who][1] for who in wanted]
            span_x = max(1e-6, max(xs) - min(xs))
            span_y = max(1e-6, max(ys) - min(ys))
            scale = min(usable_w / span_x, usable_h / span_y)

            def place(who: str) -> QPointF:
                lon, lat = self._anchors[who]
                return QPointF(
                    width / 2 + (lon - (min(xs) + span_x / 2)) * scale,
                    # Latitude runs the other way from screen y.
                    height / 2 - (lat - (min(ys) + span_y / 2)) * scale)

            return place(self._subject_id), [place(o) for _n, _v, _s, _i, o in self._lanes]

        centre = QPointF(width / 2, height / 2)
        # **An ellipse, not a circle.** A panel is three times as wide as this strip is
        # tall, and a circle inscribed in the shorter side left two thirds of the width
        # empty and the lanes too short to read a thickness off. The fan spreads to the
        # room there is.
        reach_x, reach_y = usable_w / 2, usable_h / 2
        count = max(1, len(self._lanes))
        # Inbound to the left, outbound to the right: which way the cargo goes is the first
        # thing a reader wants and a fan that mixed them would need the arrows read one by
        # one to answer it.
        left = [i for i, lane in enumerate(self._lanes) if lane[3]]
        right = [i for i, lane in enumerate(self._lanes) if not lane[3]]
        points: list[QPointF | None] = [None] * count
        for side, indices in ((-1, left), (1, right)):
            for slot, index in enumerate(indices):
                spread = math.pi * 0.72
                # A lone lane on a side goes straight out, not to the top of a fan it is
                # the only member of.
                step = spread / max(1, len(indices) - 1) if len(indices) > 1 else 0.0
                angle = (-spread / 2 + slot * step) if len(indices) > 1 else 0.0
                points[index] = QPointF(
                    centre.x() + side * reach_x * math.cos(angle),
                    centre.y() + reach_y * math.sin(angle))
        return centre, [point or centre for point in points]

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        theme = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.inset))
        if not self._lanes:
            painter.setPen(QColor(theme.ink_ghost))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No standing lanes to draw.")
            painter.end()
            return

        centre, places = self._places(float(self.width()), float(self.height()))
        widest = max(volume for _n, volume, _s, _i, _o in self._lanes) or 1.0

        for (name, volume, status, inbound, _other), point in zip(
                self._lanes, places, strict=True):
            colour = QColor(getattr(theme, _STATUS_COLOUR.get(status, "ink_dim"),
                                    theme.ink_dim))
            thickness = self._THINNEST + (self._WIDEST - self._THINNEST) * (
                volume / widest)
            pen = QPen(colour, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            if status != "open":
                # A stopped lane is drawn as a lane that is not carrying anything: the
                # agreement stands and the cargo does not move, and a dashed line is what
                # that looks like.
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(centre, point)
            _arrow(painter, centre, point, colour, inbound=inbound, size=thickness + 5.0)

            painter.setPen(QColor(theme.ink_body))
            label = f"{name}  {volume:g}"
            # Away from the middle, on both axes: a label drawn over its own lane is the
            # one thing on this picture that cannot be read.
            width = painter.fontMetrics().horizontalAdvance(label)
            x = point.x() - width - 6 if point.x() < centre.x() else point.x() + 6
            # Kept on the canvas. The leftmost lane's label ran off the edge and the
            # partner's name arrived as "UP" instead of "SUP".
            x = max(2.0, min(x, float(self.width()) - width - 2))
            offset = -8 if point.y() < centre.y() else 16
            painter.drawText(QPointF(x, point.y() + offset), label)

        painter.setBrush(QColor(theme.brass))
        painter.setPen(QPen(QColor(theme.panel), 2))
        painter.drawEllipse(centre, 9.0, 9.0)
        painter.setPen(QColor(theme.ink_bright))
        painter.drawText(QPointF(centre.x() - 22, centre.y() - 16), self._subject)
        painter.end()


def _arrow(painter: QPainter, centre: QPointF, point: QPointF, colour: QColor, *,
           inbound: bool, size: float) -> None:
    """A head a third of the way along, pointing the way the cargo goes."""
    tip = QPointF(centre.x() + (point.x() - centre.x()) * (0.34 if inbound else 0.66),
                  centre.y() + (point.y() - centre.y()) * (0.34 if inbound else 0.66))
    dx, dy = point.x() - centre.x(), point.y() - centre.y()
    length = math.hypot(dx, dy) or 1.0
    ux, uy = (dx / length, dy / length) if not inbound else (-dx / length, -dy / length)
    head = QPolygonF([
        QPointF(tip.x() + ux * size, tip.y() + uy * size),
        QPointF(tip.x() - uy * size * 0.5, tip.y() + ux * size * 0.5),
        QPointF(tip.x() + uy * size * 0.5, tip.y() - ux * size * 0.5)])
    path = QPainterPath()
    path.addPolygon(head)
    painter.fillPath(path, colour)


class _Block(QFrame):
    """A bordered block with a coloured left edge. The shape every row on this panel has,
    extracted so eleven of them cannot come out at eleven different insets."""

    def __init__(self, edge: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edge = edge
        self.setObjectName("lensBlock")
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(13, 9, 13, 10)
        self.column.setSpacing(3)
        self._kids: list[QWidget] = []

    def line(self, text: str, *, style: str = "body", colour: str = "ink_body",
             theme: Theme) -> TextLabel:
        label = TextLabel(text, style=style, colour=colour, theme=theme, wrap=True)
        self.column.addWidget(label)
        self._kids.append(label)
        return label

    def apply_theme(self, theme: Theme) -> None:
        edge = getattr(theme, self._edge, theme.rule)
        self.setStyleSheet(
            f"QFrame#lensBlock {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in self._kids:
            if hasattr(child, "apply_theme"):
                child.apply_theme(theme)


def _vulnerability_row(item: Vulnerability, rank: int, theme: Theme) -> _Block:
    """One of the three. The claim, the arithmetic behind it, and the one answer."""
    block = _Block("danger" if item.severity >= 0.75 else "brass", theme=theme)
    block.line(f"{rank}.  {item.headline}", style="row", colour="ink_bright", theme=theme)
    block.line(f"{item.because[:1].upper()}{item.because[1:]}.", style="prose",
               colour="ink_dim", theme=theme)
    answer = block.line(f"→  {item.response.text}", style="body",
                        colour="brass" if item.response.available else "ink_ghost",
                        theme=theme)
    answer.setToolTip(item.response.how)
    if not item.response.available and item.response.blocked_because:
        block.line(f"    Not now: {item.response.blocked_because}.",
                   style="mono_small", colour="ink_faint", theme=theme)
    block.apply_theme(theme)
    return block


def _good_row(item: GoodPosition, theme: Theme) -> _Block:
    """Where this country stands in one good: what it burns, what it has, how long it lasts."""
    dry = item.runway is not None and item.runway <= 6
    block = _Block("danger" if dry else "rule", theme=theme)
    head = QHBoxLayout()
    head.setSpacing(10)
    name = TextLabel(item.good.upper(), style="action", colour="ink_bright", theme=theme)
    head.addWidget(name, 1)
    if item.produced is None:
        # **Never the green sentence for somebody else's store.** The runway is `None`
        # here because the viewer does not know it, not because the store is holding, and
        # "not running down" is a claim no foreign ministry is entitled to make.
        said, ink = ("unknown", "ink_ghost")
    elif item.runway is None:
        said, ink = ("not running down", "good")
    elif item.runway > RUNWAY_WORTH_SAYING:
        # **A figure nobody would act on is a sentence instead.** "160.0 months of cover"
        # is arithmetic showing its working; thirteen years of store is *not a problem*,
        # and that is the thing to say.
        said, ink = ("over a year of cover", "good")
    else:
        said = f"{figure(item.runway, places=1)} months of cover"
        ink = "danger" if dry else "brass"
    cover = TextLabel(said, style="mono_small", colour=ink, theme=theme)
    head.addWidget(cover)
    block.column.addLayout(head)
    block._kids.extend([name, cover])

    if item.produced is None:
        block.line(item.estimated or "unknown", style="prose_italic", colour="ink_dim",
                   theme=theme)
    else:
        line = block.line(
            f"burns {exact(item.demand)} a month  ·  digs up {exact(item.produced)}  ·  "
            f"imports {exact(item.imported)}  ·  exports {exact(item.exported)}  ·  "
            f"store {exact(item.stockpile)}",
            style="mono_small", colour="ink_body", theme=theme)
        line.setToolTip(
            "Every figure is a month at the volumes actually moving, not at the volumes "
            "agreed. The store is exact: it is your own.")
    seen = "" if item.produced is not None else " that you can see"
    if item.top_supplier:
        # **The self-sufficiency clause only where there is a self to be sufficient.** It
        # is `1 - dependency`, which is `produced / demand`, and printing it about somebody
        # else's country handed over the production two lines above had just withheld --
        # while calling it *your own ground* on a page about a foreign power.
        ground = ("" if item.dependency is None else
                  f" Your own ground covers {percent(1.0 - item.dependency, places=0)}.")
        block.line(
            f"{item.top_supplier} carries {percent(item.top_supplier_share, places=0)} of "
            f"the supply{seen}, out of {item.suppliers} "
            + ("lane" if item.suppliers == 1 else "lanes") + "." + ground,
            style="body", colour="ink_dim", theme=theme)
    elif item.produced is not None and item.dependency is not None:
        block.line(
            f"Nobody supplies you: your own ground covers "
            f"{percent(1.0 - item.dependency, places=0)} of what you burn.",
            style="body", colour="ink_dim", theme=theme)
    if item.top_customer:
        block.line(
            f"{item.top_customer} takes {percent(item.top_customer_share, places=0)} of "
            f"what selling it earns{seen}, out of {item.customers} "
            + ("buyer" if item.customers == 1 else "buyers") + ".",
            style="body", colour="ink_dim", theme=theme)
    block.apply_theme(theme)
    return block


def _route_row(view: RouteView, theme: Theme, *, own: bool = True,
               subject: str = "") -> _Block:
    """One lane: what moves, what it costs, what is stopping it, and who else could."""
    status = view.status
    block = _Block(_STATUS_COLOUR.get(status, "rule"), theme=theme)
    head = QHBoxLayout()
    head.setSpacing(10)
    way = "from" if view.inbound else "to"
    what = TextLabel(
        f"{view.settlement.volume:g} {view.good} a month {way} {view.other_name}",
        style="row_small", colour="ink_bright", theme=theme, wrap=True)
    head.addWidget(what, 1)
    chip = TextLabel(_STATUS_WORD.get(status, status), style="mono_small",
                     colour=_STATUS_COLOUR.get(status, "ink_dim"), theme=theme)
    chip.setToolTip(STATUSES.get(status, ""))
    head.addWidget(chip)
    block.column.addLayout(head)
    block._kids.extend([what, chip])

    if view.settlement.unit_price > 0 and not view.settlement.moving:
        # **A stopped lane is not costing anybody nothing a month.** "costing you 0 a month
        # at 0.4212 a unit" is arithmetic run over a cargo that did not sail. The price is
        # still worth printing -- it is the number the two sides are disagreeing about.
        block.line(
            f"standing terms {figure(view.settlement.unit_price, places=2)} a unit; "
            "nothing is moving on it",
            style="mono_small", colour="ink_body", theme=theme)
    elif view.settlement.unit_price > 0:
        # **"You" is the reader, not the subject.** `inbound` is a fact about the country
        # the page is *about*, so on a foreign page the two are opposite people: a lane
        # this player sells on is inbound to the power being read, and the panel told the
        # reader it was costing them the money they were being paid.
        money = (("costing you" if view.inbound else "earning you") if own
                 else f"{'costing' if view.inbound else 'earning'} {subject}")
        block.line(
            f"{money} {exact(view.settlement.bill)} a month at "
            f"{figure(view.settlement.unit_price, places=3)} a unit"
            + (f"; {exact(view.settlement.shipped)} of the "
               f"{view.settlement.volume:g} actually moves"
               if abs(view.settlement.shipped - view.settlement.volume) > 0.005 else ""),
            style="mono_small", colour="ink_body", theme=theme)
    elif status in ("open", "barter"):
        # **Only where the lane is actually a barter lane.** A lane stopped before the
        # engine ever priced it has no terms because nobody got that far, and telling a
        # blockaded country that its oil "moves and no money does" is the opposite of true.
        block.line("barter: the cargo moves and no money does", style="mono_small",
                   colour="ink_faint", theme=theme)
    # The chip already carries the short form; the sentence only earns its line when it
    # says more than the chip does.
    # `view.why` and not `view.settlement.why`: the settlement's sentence carries both
    # sides' positions, and which of the two a reader may have is a disclosure question
    # that `_views` has already answered. A screen answering it again would be a second
    # answer.
    if view.why and view.why != _STATUS_WORD.get(status):
        block.line(view.why, style="prose", colour="ink_dim", theme=theme)
    if view.inbound:
        block.line(
            f"{percent(view.share, places=0)} of the {view.good} you can see arriving"
            if not own else f"{percent(view.share, places=0)} of your {view.good}"
            + ("" if view.alternatives < 0 else
               (", and no other supplier can reach you" if view.alternatives == 0 else
                f", and {view.alternatives} other "
                + ("supplier" if view.alternatives == 1 else "suppliers")
                + " could if it closed")),
            style="body", colour="ink_dim", theme=theme)
    else:
        block.line(
            f"{percent(view.share, places=0)} of what its {view.good} earns"
            if not own else f"{percent(view.share, places=0)} of what your {view.good} earns",
            style="body", colour="ink_dim", theme=theme)
    block.apply_theme(theme)
    return block


def _counterfactual_row(item: Counterfactual, theme: Theme) -> _Block:
    block = _Block("danger" if item.bites else "rule", theme=theme)
    if item.bites:
        when = (f"in about {figure(item.months, places=1)} months"
                if item.months is not None else "once the store runs out")
        block.line(
            f"Without {item.label}: shortages slow your yards from "
            f"{percent(item.pace_year, places=0)} to {percent(item.pace_after, places=0)} "
            f"of their pace, {when}.", style="body", colour="ink_bright", theme=theme)
        block.line(
            "Nothing changes this month -- the yards read the store, and the store is "
            "there. What changes is what is left when it is not. Static and first-order: "
            "another lane, more of your own, or a store that goes on filling all move it.",
            style="prose", colour="ink_dim", theme=theme)
    else:
        block.line(f"Without {item.label}: still "
                   f"{percent(item.pace_year, places=0)} of their pace.",
                   style="body", colour="ink_dim", theme=theme)
        block.line(
            "Your stores of this good outlast the year without that lane.",
            style="prose", colour="ink_faint", theme=theme)
    block.apply_theme(theme)
    return block


def _response_row(item: Response, theme: Theme) -> _Block:
    block = _Block("brass" if item.available else "rule", theme=theme)
    block.line(item.text, style="body",
               colour="ink_bright" if item.available else "ink_dim", theme=theme)
    block.line(item.how, style="prose", colour="ink_dim", theme=theme)
    if not item.available:
        block.line(f"Not available: {item.blocked_because}.", style="mono_small",
                   colour="ink_faint", theme=theme)
    block.apply_theme(theme)
    return block


def _forecast_block(found: Forecast, theme: Theme) -> _Block:
    """The year ahead at current policy, and the two branches off it.

    **Labelled as an estimate in the first line and again at the bottom**, because the one
    way this section can do harm is by being read as a prediction.
    """
    block = _Block("info", theme=theme)
    block.line("The year ahead, at current policy", style="action", colour="ink_bright",
               theme=theme)
    ledger = found.ledger
    block.line(
        f"{exact(ledger.revenue)} in, {exact(ledger.upkeep)} of upkeep, "
        f"{exact(ledger.imports)} of cargo out and {exact(ledger.exports)} in "
        f"— {exact(ledger.net)} a month against a purse of {exact(found.treasury)}.",
        style="mono_small", colour="ink_body", theme=theme)
    if found.arrears_on:
        block.line(f"On these commitments the money runs out on {found.arrears_on}.",
                   style="body", colour="danger", theme=theme)
    else:
        block.line(
            f"On these commitments the purse holds all year and ends at "
            f"{exact(found.treasury_end)}.", style="body", colour="good", theme=theme)
    if found.deliveries:
        due = ", ".join(f"{item.what} in {figure(item.due_in, places=1)} months"
                        for item in found.deliveries[:4])
        block.line(
            f"Already paid for and not yet delivered: {due}. That money has left the "
            f"treasury ({exact(found.already_paid)}); what is ahead is what they cost to "
            "keep.", style="prose", colour="ink_dim", theme=theme)
    for branch in found.counterfactuals:
        block.line(
            f"→  {branch.label}: {exact(branch.treasury_end)} at the year's end "
            f"({exact(branch.delta)} against this page)"
            + (f", and the money runs out on {branch.arrears_on}"
               if branch.arrears_on else "")
            + f". {branch.because[:1].upper()}{branch.because[1:]}",
            style="body", colour="ink_dim", theme=theme)
    for note in found.notes:
        block.line(note, style="mono_small", colour="ink_faint", theme=theme)
    block.line(found.caveat, style="mono_small", colour="ink_ghost", theme=theme)
    block.apply_theme(theme)
    return block


class DependencyOverlay(Overlay):
    """Who supplies this country, what it costs, what breaks, and what it can do.

    ``load`` is the whole interface. It reads the world once through
    :func:`atlas.engine.dependency.lens_for`, lays the answer out, and writes nothing.
    """

    #: Somewhere the player asked to be taken: ``(kind, target)``, the same pair the search
    #: box and the watchlist emit, so the window wires one route and not a third one.
    chosen = Signal(str, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._section = SectionLabel("Who supplies you", theme=theme)
        self.body.addWidget(self._section)
        self.body.addSpacing(SPACE.sm)
        self._headline = TextLabel("", style="title", colour="ink_bright", theme=theme,
                                   wrap=True)
        self.body.addWidget(self._headline)
        self._tally = TextLabel("", style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._tally)
        self.body.addSpacing(SPACE.md)

        self._map = _LaneMap(theme)
        self.body.addWidget(self._map)
        self.body.addSpacing(SPACE.md)

        holder = QWidget()
        self._stack = QVBoxLayout(holder)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)
        self.body.addWidget(holder, 1)

        self._blocks: list[_Block] = []
        self._headings: list[TextLabel] = []
        #: What the page on the screen is a picture *of*: the world, who is reading it,
        #: who it is about, which turn, and how far that world had been written to. See
        #: :meth:`refresh_if_stale` -- without this the panel is a photograph with no date
        #: on it, and there is no way to ask whether it is still true.
        self._showing: tuple | None = None
        self._anchors: dict[str, tuple[float, float]] | None = None

    # -- loading -----------------------------------------------------------

    def load(  # noqa: PLR0913 - a lens is the world, the viewer, the subject and the turn
        self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
        subject: str | None = None, *, turn: int,
        anchors: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Read the world and lay it out. Writes nothing."""
        clear_layout(self._stack)
        self._blocks.clear()
        self._headings.clear()

        lens = lens_for(conn, world_id, viewer, subject, turn=turn)
        self._showing = (id(conn), world_id, viewer, subject, turn,
                         world_revision(conn, world_id))
        self._anchors = anchors
        self._map.show_lanes(lens, anchors)
        if not lens.nation_id:
            self._headline.setText("No economy to read")
            self._tally.setText(lens.unknowns[0] if lens.unknowns else "")
            return

        self._section.setText(
            "WHO SUPPLIES YOU" if lens.own or not lens.nation_id
            else f"WHO SUPPLIES {lens.name.upper()}")
        self._fill(conn, world_id, lens, turn=turn)
        for block in self._blocks:
            block.apply_theme(self._theme)
        for heading in self._headings:
            heading.apply_theme(self._theme)

    def _heading(self, text: str) -> None:
        label = TextLabel(text, style="section", colour="ink_faint", theme=self._theme)
        self._stack.addSpacing(SPACE.lg)
        self._stack.addWidget(label)
        self._stack.addSpacing(SPACE.xs)
        self._headings.append(label)

    def _add(self, block: _Block) -> None:
        self._stack.addWidget(block)
        self._stack.addSpacing(6)
        self._blocks.append(block)

    def _fill(self, conn: sqlite3.Connection, world_id: str, lens: DependencyLens, *,
              turn: int) -> None:
        moving = [view for view in lens.routes if view.settlement.moving]
        one = len(lens.routes) == 1
        if not lens.stopped:
            standing = "and it is shipping" if one else "all of them shipping"
        elif one:
            standing = "and it has stopped"
        elif len(lens.stopped) == len(lens.routes):
            standing = "and every one of them has stopped"
        else:
            standing = f"{len(lens.stopped)} of them stopped"
        self._headline.setText(
            f"{lens.name}: {len(lens.routes)} standing "
            + ("lane, " if one else "lanes, ") + standing)
        if lens.priced:
            self._tally.setToolTip(
                "The net is one running total in lane order, rounded once at each step "
                "exactly as the settlement rounds it -- so it is the figure the treasury "
                "actually moves by, which is not always the two figures beside it "
                "subtracted.")
            whose = "" if lens.own else f"{lens.name}: "
            self._tally.setText(
                f"{whose}{exact(lens.bill)} a month goes out for cargo and "
                f"{exact(lens.receipts)} comes in — {exact(lens.net)} net. "
                f"{len(moving)} of {len(lens.routes)} "
                + ("lane is" if len(lens.routes) == 1 else "lanes are")
                + " actually moving something."
                + ("" if lens.own else
                   " These are the lanes you are at the other end of, and no others."))
        else:
            self._tally.setText(
                "This world barters: cargo moves and no money does, so every figure in "
                "money below is zero by construction.")

        # 1. The three worst things about this position, before anything else.
        if lens.vulnerabilities:
            self._heading(
                f"{_COUNTED[min(3, len(lens.vulnerabilities))]} largest vulnerabilities")
            for rank, item in enumerate(lens.vulnerabilities, start=1):
                self._add(_vulnerability_row(item, rank, self._theme))
        elif lens.own:
            self._heading("Largest vulnerabilities")
            block = _Block("rule", theme=self._theme)
            block.line("Nothing here is a vulnerability at the moment.", style="body",
                       colour="ink_dim", theme=self._theme)
            block.line(
                "No store is running down, no single supplier carries most of a good, and "
                "the import bill is inside what you earn.",
                style="prose", colour="ink_faint", theme=self._theme)
            self._add(block)

        # 2. The three goods.
        if lens.goods:
            self._heading("Where you stand in each good" if lens.own
                          else f"Where {lens.name} stands, as far as you can see")
            for item in lens.goods:
                self._add(_good_row(item, self._theme))

        # 3. Every lane, inbound then outbound.
        inbound = [view for view in lens.routes if view.inbound]
        outbound = [view for view in lens.routes if not view.inbound]
        if inbound:
            self._heading("What comes in, and what is stopping it" if lens.own
                          else "What you send it, and what is stopping it")
            for view in inbound:
                self._add(_route_row(view, self._theme, own=lens.own,
                                     subject=lens.name))
        if outbound:
            self._heading("What goes out")
            for view in outbound:
                self._add(_route_row(view, self._theme, own=lens.own,
                                     subject=lens.name))

        # 4. What the yards lose without each of them.
        if lens.counterfactuals:
            self._heading("What your industry can build, and what it could without a lane")
            head = _Block("info", theme=self._theme)
            head.line(f"{lens.capacity_now} build "
                      + ("slot" if lens.capacity_now == 1 else "slots")
                      + f"; your stores let them work at {percent(lens.pace_now, places=0)}"
                      " of their pace.",
                      style="body", colour="ink_bright", theme=self._theme)
            # **The caveat leads the block rather than trailing it.** These are the numbers
            # on this page most likely to be read as a forecast, and a reader who has taken
            # one as a prediction has already misread it by the time a footnote arrives.
            head.line(STATIC_CAVEAT, style="prose", colour="ink_dim", theme=self._theme)
            self._add(head)
            for item in lens.counterfactuals:
                self._add(_counterfactual_row(item, self._theme))

        # 5. What can actually be done.
        if lens.responses:
            self._heading("What you can do about it")
            for item in lens.responses:
                self._add(_response_row(item, self._theme))

        # 6. And the year ahead, if this is the player's own country.
        if lens.own:
            self._heading("The fiscal picture")
            self._add(_forecast_block(
                forecast_for(conn, world_id, lens.nation_id, turn=turn), self._theme))

        # 7. What could not be seen, said rather than left blank.
        if lens.unknowns:
            self._heading("What this cannot see")
            block = _Block("rule", theme=self._theme)
            for line in lens.unknowns:
                block.line(line, style="prose", colour="ink_dim", theme=self._theme)
            self._add(block)
        self._stack.addStretch(1)

    def refresh_if_stale(self, conn: sqlite3.Connection, world_id: str, *,
                         turn: int) -> bool:
        """Re-read the world if this page is still on the screen and no longer true.

        **A panel is rebuilt by `load` every time it opens, so a closed one is never
        stale** -- and a pinned one never closes. `Overlay.pinnable` is on here, which
        means the player can keep this page up beside the rewind sheet, press it, and go on
        reading a treasury forecast for a world that has been rolled back. Measured before
        this existed: zero of 779 labels changed across a rewind that moved the world, and
        a fresh read differed in six, one of them a counterfactual reading 476.66 where the
        truth was 436.69.

        Returns whether it reloaded. Cheap when nothing has moved -- one indexed `MAX(id)`
        -- and skipped entirely while the panel is closed, which matters because a cold
        read of this page costs a settlement walk and the caller is every turn.
        """
        if self._showing is None or not self.showing():
            return False
        _held, world, viewer, subject, _turn, _at = self._showing
        if world != world_id:
            return False
        if self._showing[4] == turn and self._showing[5] == world_revision(conn, world_id):
            return False
        self.load(conn, world_id, viewer, subject, turn=turn, anchors=self._anchors)
        return True

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._map.apply_theme(theme)
