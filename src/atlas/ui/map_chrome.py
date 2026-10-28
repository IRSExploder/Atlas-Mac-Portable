"""Everything that floats over the map.

The map itself fills the middle of the window edge to edge; the chrome sits on top of it
as small frosted panels in the four corners plus a readout bar along the bottom. They are
children of this widget rather than rows in a layout, because a legend that pushed the
map sideways would make the map smaller every time it had something to say.

Positioning is done by hand in :meth:`resizeEvent`. A layout cannot express "16px from
the bottom-left corner, floating" without a stack of nested spacers, and the arithmetic
here is three lines.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ..map.legend import ScaleKind, SwatchKind, legend_for
from ..map.scene_model import MapLayer, UnitDomain
from .theme import SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    TextLabel,
    clear_layout,
    focus_qss,
    pressed_qss,
    restyle,
    scaled,
    tint,
)

__all__ = ["MapChrome"]

#: Qt's own "no maximum". PySide6 does not export the constant, so it is named here
#: rather than written as a bare 16777215 in the middle of a layout.
QWIDGETSIZE_MAX = (1 << 24) - 1

_LAYERS = (
    (MapLayer.POLITICAL, "Political", "Who owns what"),
    (MapLayer.FOCUS, "Focus", "Which powers act for themselves"),
    (MapLayer.FRONTS, "Fronts", "Wars, occupations, contested ground"),
    (MapLayer.FOG, "Fog", "What you actually know"),
    (MapLayer.DEVELOPMENT, "Wealth", "What each province is worth"),
    (MapLayer.UNREST, "Unrest", "Held ground, after the garrison on it"),
    (MapLayer.AIR, "Air", "Who holds the sky"),
    (MapLayer.SUPPLY, "Supply", "How far your supply reaches, and where it stops"),
    (MapLayer.NUCLEAR, "Nuclear", "What you could hit, and what you believe answers"),
    (MapLayer.ATTITUDE, "Attitude", "How each power regards you"),
    (MapLayer.WAR_AIMS, "War aims", "The ground your wars are actually about"),
    (MapLayer.TRADE, "Trade", "Who ships you oil, steel and rare earths, and who you ship"),
    (MapLayer.OBLIGATIONS, "Obligations", "What you have signed, and what it would cost"),
    (MapLayer.INDEPENDENCE, "Independence", "Which held ground is on its way out"),
)

#: The map modes a player is shown without technical details: who owns what, where the
#: fighting is, who likes you, what land is worth, and where it is restless. The rest are
#: the engine's diagnostic lenses and come back with technical details on.
_PLAIN_LAYERS = frozenset({MapLayer.POLITICAL, MapLayer.FRONTS, MapLayer.ATTITUDE,
                           MapLayer.DEVELOPMENT, MapLayer.UNREST})

_UNIT_DOMAINS = (
    (UnitDomain.LAND, "Land", "Show ground formations"),
    (UnitDomain.SEA, "Sea", "Show fleets and naval formations"),
    (UnitDomain.AIR, "Air", "Show aircraft and air formations"),
)

#: How much room the layer bar leaves the placard beside it. Both used to be placed
#: from their own corner with no knowledge of the other, which on any window put 87px of
#: chip row on top of the scenario's name.
_BAR_GAP = 20

_INSET = 16


class MapChrome(QWidget):
    """The map view with its overlays. Owns nothing about the map's contents."""

    layer_changed = Signal(str)
    unit_domains_changed = Signal(object)
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()
    fit_requested = Signal()

    def __init__(self, map_view: QWidget, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._layer = MapLayer.POLITICAL
        self._player_colour = theme.ink_dim
        self._player_name = "no nation"

        # Child order is z-order: the map at the back, the shading over it, the panels
        # over that. Creating them in this sequence is what stacks them correctly.
        self._map = map_view
        self._map.setParent(self)
        self._vignette = _Vignette(theme, self)

        self._where = _Placard(theme, self)
        self._layers = _LayerBar(theme, self)
        self._layers.chosen.connect(self._on_layer)
        self._unit_layers = _UnitLayerBar(theme, self)
        self._unit_layers.changed.connect(self.unit_domains_changed.emit)
        self._zoom = _ZoomStack(theme, self)
        self._zoom.zoom_in.connect(self.zoom_in_requested.emit)
        self._zoom.zoom_out.connect(self.zoom_out_requested.emit)
        self._zoom.fit.connect(self.fit_requested.emit)
        self._legend = _Legend(theme, self)
        self._legend.toggled.connect(self._tell_the_legend)
        self._readout = _Readout(theme, self)

        from .plain import technical

        self.set_technical(technical())
        self.apply_theme(theme)

    def set_side_panel(self, panel: QWidget | None) -> None:
        """Float ``panel`` over the map's left edge, full height, or stop floating one.

        The map underneath keeps its whole width. The panel is raised over the map and
        its overlays and placed again whenever it opens, folds or the map is resized;
        the key at the bottom left moves out from under it.
        """
        old = getattr(self, "_side", None)
        if old is not None:
            old.removeEventFilter(self)
            old.setParent(None)
        self._side = panel
        if panel is not None:
            panel.setParent(self)
            panel.installEventFilter(self)
            panel.raise_()
            panel.show()
        self.resizeEvent(None)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 -- Qt override
        from PySide6.QtCore import QEvent

        if watched is getattr(self, "_side", None) and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self._place_side()
        return super().eventFilter(watched, event)

    def _side_width(self) -> int:
        side = getattr(self, "_side", None)
        return side.width() if side is not None and side.isVisible() else 0

    def _place_side(self) -> None:
        side = getattr(self, "_side", None)
        if side is None:
            return
        side.setGeometry(0, 0, side.width(), self.height())
        side.raise_()
        self._legend.move(_INSET + self._side_width(),
                          self.height() - self._legend.height() - _INSET)

    def set_technical(self, on: bool) -> None:
        """Show the projection placard and every map mode, or only what a player needs.

        Without technical details the map keeps one row of controls: the Land/Sea/Air
        filter goes (ground forces are what a player reads on the map; fleets and air
        wings have their own screens), and the key starts folded to a line.
        """
        self._where.setVisible(on)
        self._layers.show_layers(None if on else _PLAIN_LAYERS)
        self._unit_layers.setVisible(on)
        self._legend.set_folded(not on)
        self.resizeEvent(None)

    # -- data --------------------------------------------------------------

    def set_place(self, projection: str, lod: int, regions: int, scope: str = "World") -> None:
        """Where you are looking, and at how much of it.

        ``lod`` is still taken and is still what the map redraws on -- it is simply not
        printed. A level-of-detail number on the placard over a map is the renderer talking
        about itself; the projection and the count of places are facts about the world.
        """
        self._where.set_lines(f"{scope} · {projection}", f"{regions:,} regions")

    def set_player(self, name: str, colour: str) -> None:
        self._player_name = name or "no nation"
        self._player_colour = colour or self._theme.ink_dim
        self._legend.set_player(self._player_name, self._player_colour, self._theme)

    def set_hover(self, text: str) -> None:
        """The bar under the map. Empty text hides it rather than showing a blank slab."""
        self._readout.set_text(text)
        self._readout.setVisible(bool(text))
        self._place_readout()

    @property
    def layer(self) -> MapLayer:
        return self._layer

    def _tell_the_legend(self) -> None:
        self._legend.set_layer(self._layer)
        self._legend.adjustSize()
        self._legend.move(_INSET + self._side_width(),
                          self.height() - self._legend.height() - _INSET)

    def set_layer(self, layer: MapLayer) -> None:
        self._layer = MapLayer(layer)
        self._layers.select(self._layer)
        self._tell_the_legend()

    def _on_layer(self, key: str) -> None:
        self._layer = MapLayer(key)
        self._tell_the_legend()
        self.layer_changed.emit(str(self._layer))

    @property
    def unit_domains(self) -> frozenset[UnitDomain]:
        return self._unit_layers.active

    def set_unit_domains(self, domains) -> None:
        self._unit_layers.select(domains)

    # -- geometry ----------------------------------------------------------

    def resizeEvent(self, event) -> None:
        if event is not None:
            super().resizeEvent(event)
        self._map.setGeometry(self.rect())
        self._vignette.setGeometry(self.rect())

        self._where.move(_INSET, _INSET)
        self._where.adjustSize()
        placard = self._where.width() if self._where.isVisible() else 0
        self._layers.set_available_width(
            self.width() - placard - 2 * _INSET - _BAR_GAP)
        self._layers.adjustSize()
        self._layers.move(self.width() - self._layers.width() - _INSET, _INSET)
        self._unit_layers.adjustSize()
        self._unit_layers.move(
            self.width() - self._unit_layers.width() - _INSET,
            self._layers.y() + self._layers.height() + 8,
        )

        self._zoom.adjustSize()
        self._zoom.move(
            self.width() - self._zoom.width() - _INSET,
            self.height() - self._zoom.height() - _INSET,
        )
        self._legend.adjustSize()
        self._legend.move(_INSET + self._side_width(),
                          self.height() - self._legend.height() - _INSET)
        self._place_side()
        self._place_readout()

    def _place_readout(self) -> None:
        if not self._readout.isVisible():
            return
        self._readout.adjustSize()
        self._readout.move(
            (self.width() - self._readout.width()) // 2,
            self.height() - self._readout.height() - _INSET,
        )

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(f"MapChrome {{ background: {theme.canvas}; }}")
        for panel in (
            self._vignette, self._where, self._layers, self._unit_layers,
            self._zoom, self._legend, self._readout,
        ):
            panel.apply_theme(theme)
        self._legend.set_player(self._player_name, self._player_colour, theme)
        self.update()


class _Vignette(QWidget):
    """Shading at the top and bottom edges, so panels sit on shade not on coastline.

    A widget rather than a paint in :class:`MapChrome` itself: a parent paints *behind*
    its children, and shading that lands behind the map is shading nobody can see.
    """

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        base = QColor(self._theme.canvas)
        for rect, alphas in (
            (QRect(0, 0, self.width(), 90), (0.55, 0.0)),
            (QRect(0, self.height() - 110, self.width(), 110), (0.0, 0.62)),
        ):
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            for stop, alpha in zip((0.0, 1.0), alphas, strict=True):
                shade = QColor(base)
                shade.setAlphaF(alpha)
                gradient.setColorAt(stop, shade)
            painter.fillRect(rect, gradient)
        painter.end()


class _Frosted(QFrame):
    """The shared look: a translucent panel with a hairline border, over the map."""

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"{type(self).__name__} {{"
            f" background: {tint(theme.panel, 0.88 if theme.is_dark else 0.92)};"
            f" border: 1px solid {theme.edge}; }}"
        )
        restyle(self, theme)


class _Placard(_Frosted):
    """Top-left: where you are looking and at what detail."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 9, 13, 9)
        layout.setSpacing(4)
        self._scope = TextLabel("", style="label", colour="ink_dim", theme=theme)
        self._detail = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme)
        layout.addWidget(self._scope)
        layout.addWidget(self._detail)
        self.apply_theme(theme)

    def set_lines(self, scope: str, detail: str) -> None:
        self._scope.setText(scope)
        self._detail.setText(detail)
        self.adjustSize()


class _LayerBar(_Frosted):
    """Top-right: what the colours mean right now."""

    chosen = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(5, 5, 5, 5)
        self._rows.setSpacing(4)
        self._available = 10_000
        self._chips: dict[str, Chip] = {}
        self._order: list[Chip] = []
        self._allowed: frozenset[MapLayer] | None = None
        for layer, label, tip in _LAYERS:
            chip = Chip(label, theme=theme, selected=(layer is MapLayer.POLITICAL))
            chip.setToolTip(tip)
            chip.setAccessibleName(f"{label}: {tip}")
            chip.clicked.connect(lambda key=str(layer): self._pick(key))
            self._chips[str(layer)] = chip
            self._order.append(chip)
        self._reflow()
        self.apply_theme(theme)

    def set_available_width(self, width: int) -> None:
        """How much room there is beside the placard. Fourteen chips is 1,240 pixels.

        One row of them is wider than most windows, so it ran off the left-hand edge of
        the map and through the scenario's name on the way. They wrap now -- the same
        chips, in as many rows as the room allows -- because these are the control for
        what the map is showing and a control you cannot reach is not one.
        """
        width = max(200, width)
        if width == self._available:
            return
        self._available = width
        self._reflow()

    def show_layers(self, allowed: frozenset[MapLayer] | None) -> None:
        """Only these map modes (``None``: all of them)."""
        self._allowed = allowed
        self._reflow()

    def _reflow(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.layout() is not None:
                while item.layout().count():
                    item.layout().takeAt(0)
        room = self._available - 10
        row: QHBoxLayout | None = None
        used = 0
        for (layer, _label, _tip), chip in zip(_LAYERS, self._order, strict=True):
            shown = self._allowed is None or layer in self._allowed
            chip.setVisible(shown)
            if not shown:
                continue
            chip.adjustSize()
            wanted = chip.width() + 4
            if row is None or used + wanted > room:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                row.addStretch(1)
                self._rows.addLayout(row)
                used = 0
            row.addWidget(chip)
            used += wanted
        self.adjustSize()

    def select(self, layer: MapLayer) -> None:
        for key, chip in self._chips.items():
            chip.set_selected(key == str(layer))

    def _pick(self, key: str) -> None:
        self.select(MapLayer(key))
        self.chosen.emit(key)


class _UnitLayerBar(_Frosted):
    """Top-right, second row: independently switch military domains."""

    changed = Signal(object)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._active = set(UnitDomain)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 5, 5)
        layout.setSpacing(4)
        layout.addWidget(TextLabel("Units", style="label", colour="ink_ghost", theme=theme))
        self._chips: dict[UnitDomain, Chip] = {}
        for domain, label, tip in _UNIT_DOMAINS:
            chip = Chip(label, theme=theme, selected=True, padding=(8, 5))
            chip.setToolTip(tip)
            chip.setAccessibleName(f"{label}: {tip}")
            chip.clicked.connect(lambda selected=domain: self._toggle(selected))
            self._chips[domain] = chip
            layout.addWidget(chip)
        self.apply_theme(theme)

    @property
    def active(self) -> frozenset[UnitDomain]:
        return frozenset(self._active)

    def select(self, domains) -> None:
        self._active = {UnitDomain(domain) for domain in domains}
        for domain, chip in self._chips.items():
            chip.set_selected(domain in self._active)

    def _toggle(self, domain: UnitDomain) -> None:
        if domain in self._active:
            self._active.remove(domain)
        else:
            self._active.add(domain)
        self._chips[domain].set_selected(domain in self._active)
        self.changed.emit(self.active)


class _ZoomStack(_Frosted):
    """Bottom-right: three square buttons in a column."""

    zoom_in = Signal()
    zoom_out = Signal()
    fit = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        for glyph, tip, signal in (
            ("+", "Zoom in", self.zoom_in),
            ("−", "Zoom out", self.zoom_out),
            ("⌂", "Fit the whole world", self.fit),
        ):
            button = _SquareButton(glyph, tip, theme=theme)
            button.clicked.connect(signal.emit)
            layout.addWidget(button)
        self.apply_theme(theme)


class _SquareButton(PressFeedback, QFrame):
    clicked = Signal()

    def __init__(self, glyph: str, tooltip: str, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.make_focusable()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._glyph = TextLabel(glyph, style="body", colour="ink_dim", theme=theme)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._glyph)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"_SquareButton {{ background: {tint(theme.inset, 0.9)};"
            f" border: 1px solid {theme.rule_soft}; }}"
            f"_SquareButton:hover {{ border-color: {theme.edge_hover}; }}"
            + focus_qss("_SquareButton", theme)
            + pressed_qss("_SquareButton", theme)
        )
        self._glyph.apply_theme(theme)

    def _fire(self) -> None:
        self.clicked.emit()


class _Legend(_Frosted):
    """Bottom-left: what the colours mean **on the layer that is showing**.

    It used to explain the political map and go on explaining it while the fill under it
    became development, then unrest, then air control -- fourteen layers, one key, and
    thirteen of them saying something that was not true. Nothing here knows which layer
    it is drawing: it is handed a :class:`atlas.map.legend.LayerLegend` and lays out
    whatever that says, which is what stops the fifteenth layer shipping with the
    fourteenth's key. See `atlas.map.legend`.
    """

    #: Folded to a single line and opened again by a click. See `set_folded`.
    toggled = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._player_name = "You"
        self._player_colour = theme.ink_dim
        self._folded = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._legend = legend_for(MapLayer.POLITICAL)
        # A definite measure, because most of what goes in here now is a sentence rather
        # than a label -- and a column of wrapping text with no width to wrap to collapses
        # to the widest single word in it.
        self._measure = scaled(300)
        self.setFixedWidth(self._measure)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(13, 11, 15, 12)
        self._layout.setSpacing(SPACE.sm)
        self._build()
        self.apply_theme(theme)

    def set_layer(self, layer: MapLayer) -> None:
        if layer is self._legend.layer:
            return
        self._legend = legend_for(layer)
        self._build()

    def set_player(self, name: str, colour: str, theme: Theme) -> None:
        self._player_name = name or "You"
        self._player_colour = colour or theme.ink_dim
        self._theme = theme
        self._build()

    def set_folded(self, folded: bool) -> None:
        """**A key you can put away.** Open, it covered a fifth of the map -- most of
        Africa at the default zoom -- to explain five colours a player learns in a turn.
        Folded, it is one line saying where the key is; a click opens or folds it."""
        if folded != self._folded:
            self._folded = folded
            self._build()
            self.toggled.emit()

    def mousePressEvent(self, event) -> None:
        self.set_folded(not self._folded)
        super().mousePressEvent(event)

    def _build(self) -> None:
        # Let go of the height the last legend was pinned to before measuring this one,
        # or the layout answers with the old key's height and then with nothing.
        self.setMinimumHeight(0)
        self.setMaximumHeight(QWIDGETSIZE_MAX)
        clear_layout(self._layout)
        theme, legend = self._theme, self._legend
        if self._folded:
            self.setFixedWidth(scaled(150))
            self._layout.addWidget(TextLabel(
                f"{legend.title} · key ▸", style="label", colour="ink_dim", theme=theme))
            self.setToolTip("Show what the colours mean")
            for child in self.findChildren(QWidget):
                child.show()
            self._layout.activate()
            self.setFixedHeight(self._layout.sizeHint().height())
            return
        self.setFixedWidth(self._measure)
        self.setToolTip("Click to fold the key away")
        self._layout.addWidget(
            TextLabel(legend.title, style="label", colour="ink_faint", theme=theme))

        if legend.kind is ScaleKind.RAMP:
            self._layout.addWidget(
                TextLabel(legend.unit, style="body_small", colour="ink_dim", theme=theme,
                          wrap=True))
            # Added straight into the legend's own column rather than wrapped in a
            # widget of their own: a plain QWidget does not pass its layout's
            # height-for-width up to its parent, and a column of wrapping sentences with
            # one of those in the middle of it comes out 25 pixels tall.
            self._layout.addWidget(_RampBar(theme))
            ends = QHBoxLayout()
            ends.setContentsMargins(0, 0, 0, 0)
            ends.setSpacing(SPACE.sm)
            ends.addWidget(TextLabel(legend.cold, style="mono_small", colour="ink_faint",
                                     theme=theme))
            ends.addStretch(1)
            hot = TextLabel(legend.hot, style="mono_small", colour="ink_faint",
                            theme=theme)
            hot.setAlignment(Qt.AlignmentFlag.AlignRight)
            ends.addWidget(hot)
            self._layout.addLayout(ends)
        from .plain import technical

        detail = technical()
        for entry in legend.entries:
            if entry.swatch is SwatchKind.FOCUS and not detail:
                continue  # which powers the engine tracks: its working, not the map's
            text = entry.text.replace("{nation}", self._player_name)
            row = _LegendRow(text, entry.swatch, theme=theme)
            if entry.swatch is SwatchKind.PLAYER:
                row.set_swatch(self._player_colour, theme)
            self._layout.addWidget(row)
            if entry.swatch is SwatchKind.PLAYER and not detail \
                    and legend.layer is MapLayer.POLITICAL:
                # The calm map's one shared colour: see `map.render._calm_fill`.
                from ..map.render import ALLY_FILL, ENEMY_FILL

                for said, colour in (("Your allies", ALLY_FILL),
                                     ("At war with you", ENEMY_FILL)):
                    extra = _LegendRow(said, SwatchKind.PLAYER, theme=theme)
                    extra.set_swatch(colour, theme)
                    self._layout.addWidget(extra)
        if legend.blank:
            self._layout.addWidget(_LegendRow(legend.blank, SwatchKind.BLANK, theme=theme))

        # The second question, once the colour has been read: what moves this. A design
        # note more than a key, so it is a technical detail.
        if detail:
            self._layout.addWidget(Divider(soft=True, theme=theme))
            self._layout.addWidget(
                TextLabel(legend.driven_by, style="mono_small", colour="ink_faint",
                          theme=theme, wrap=True))
        # **Shown, then measured.** A widget built after its parent is already on screen
        # is hidden until it is told otherwise, and a hidden widget answers a layout with
        # a size of nothing -- so the first legend, built before the map was shown, laid
        # itself out correctly and every one after it came out empty. Rebuilding is the
        # whole point of this widget, so this is the line that makes it work at all.
        for child in self.findChildren(QWidget):
            child.show()
        self._layout.activate()
        self.setFixedHeight(self._layout.heightForWidth(self._measure))

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._theme = theme
        self._build()


class _RampBar(QWidget):
    """Ten steps of the same ramp the renderer paints with."""

    STEPS = 10

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(8)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        from atlas.map.render import _ramp

        painter = QPainter(self)
        palette = self._theme.map_palette()
        width = self.width() / self.STEPS
        for step in range(self.STEPS):
            share = step / (self.STEPS - 1)
            painter.fillRect(int(step * width), 0, int(width) + 1, self.height(),
                             QColor(_ramp(palette.ramp_cold, palette.ramp_hot, share)))
        painter.end()


class _LegendRow(QWidget):
    """A swatch and a sentence. The swatch shape encodes which convention it is."""

    def __init__(self, text: str, key, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._override: str | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self._swatch = _LegendSwatch(theme, key)
        self._text = TextLabel(text, style="body_small", colour="ink_dim", theme=theme,
                               wrap=True)
        layout.addWidget(self._swatch)
        layout.addWidget(self._text)
        layout.addStretch(1)

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def set_swatch(self, colour: str, theme: Theme) -> None:
        self._override = colour
        self._swatch.set_colour(colour)
        self._swatch.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self._swatch.apply_theme(theme)
        self._text.apply_theme(theme)


class _LegendSwatch(QWidget):
    """14x11 of paint, drawn the same way the map draws the thing it stands for."""

    def __init__(self, theme: Theme, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._key = key
        self._colour: str | None = None
        self.setFixedSize(14, 11)

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QPen

        theme = self._theme
        painter = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)

        if self._key == SwatchKind.PLAYER:
            painter.fillRect(rect, QColor(self._colour or theme.ink_dim))
        elif self._key == SwatchKind.CHANGED:
            painter.fillRect(rect, QColor(theme.map_unclaimed))
            pen = QPen(QColor(theme.map_changed))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect)
        elif self._key == SwatchKind.CONTESTED:
            painter.fillRect(rect, QColor(theme.map_unclaimed))
            painter.setPen(QPen(QColor(theme.map_contested), 1))
            for offset in range(-rect.height(), rect.width(), 4):
                painter.drawLine(
                    offset, rect.height(), offset + rect.height(), 0
                )
        else:
            painter.fillRect(rect, QColor(theme.map_unclaimed))
            pen = QPen(QColor(theme.brass))
            pen.setWidth(1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)
        painter.end()


class _Readout(_Frosted):
    """The hover bar. One line, centred, only present when the cursor is over land."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 15, 8)
        self._text = TextLabel("", style="body_small", colour="ink_body", theme=theme)
        layout.addWidget(self._text)
        self.setVisible(False)
        self.apply_theme(theme)

    def set_text(self, text: str) -> None:
        self._text.setText(text)
        self.adjustSize()
