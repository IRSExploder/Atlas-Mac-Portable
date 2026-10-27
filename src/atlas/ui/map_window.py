"""A minimal window around the map, so Phase 2 is something you can actually look at.

The real application shell arrives with the turn loop. What exists here is the map, an
inspector panel showing the clicked region, and the wiring between them -- enough to
confirm the renderer works against real state and real geometry.

Note the shape of ``_rebuild``: every refresh reads world state, builds a *new* scene
description, and applies it. There is no path that mutates an existing description. Zoom
changing the level of detail goes through exactly the same route.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..humanize import count, figure
from ..humanize import strength as say_strength
from ..map.pack import GeometryPack, PackSet
from ..map.projection import get_projection
from ..map.query import load_map_view
from ..map.render import build_scene
from .map_view import MapView

__all__ = ["MapWindow"]


class MapWindow(QMainWindow):
    def __init__(
        self,
        conn: sqlite3.Connection,
        pack: GeometryPack | PackSet,
        world_id: str,
        *,
        observer_nation_id: str | None = None,
        projection_key: str = "equirect",
    ) -> None:
        super().__init__()
        self._conn = conn
        self._pack = pack
        self._world_id = world_id
        self._observer = observer_nation_id
        self._projection = get_projection(projection_key)
        self._selected: str | None = None
        self._label_points = {entry.geo_key: entry.label_point for entry in pack.entries}
        self._bounds = {entry.geo_key: entry.bbox for entry in pack.entries}

        self.setWindowTitle(f"ATLAS - {world_id}")
        self.resize(1280, 800)

        self._view = MapView(pack, self._projection)
        self._inspector = QLabel("Click a region to inspect it.")
        self._inspector.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._inspector.setWordWrap(True)
        self._inspector.setMinimumWidth(280)
        self._inspector.setStyleSheet("padding: 12px;")

        fit = QPushButton("Fit world")
        fit.clicked.connect(self._view.fit_world)

        side = QVBoxLayout()
        side.addWidget(self._inspector, 1)
        side.addWidget(fit)
        side_widget = QWidget()
        side_widget.setLayout(side)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view, 1)
        layout.addWidget(side_widget)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._view.region_clicked.connect(self._on_region_clicked)
        self._view.lod_changed.connect(lambda _level: self._rebuild())
        self._view.viewport_moved.connect(self._rebuild)

        self._rebuild()
        self._view.fit_world()

    def _rebuild(self) -> None:
        """Re-derive the whole scene. Never patches the previous one."""
        view = load_map_view(
            self._conn, self._world_id, observer_nation_id=self._observer
        )
        description = build_scene(
            view,
            projection=self._projection,
            pack_id=self._pack.pack_id,
            lod=self._view.lod,
            label_points=self._label_points,
            selected_region_id=self._selected,
            dissolve=self._view.dissolve,
            viewport=self._view.viewport_rect(),
            bounds=self._bounds,
        )
        self._view.show_scene(description)

    def _on_region_clicked(self, region_id: str) -> None:
        self._selected = region_id
        self._rebuild()

        row = self._conn.execute(
            """
            SELECT r.name, r.terrain, r.kind, r.population, r.development,
                   n.name AS owner, n.colour
              FROM region r
              LEFT JOIN nation n ON n.id = r.owner_nation_id
             WHERE r.id = ?
            """,
            (region_id,),
        ).fetchone()
        if row is None:
            return

        units = self._conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(strength), 0) AS s FROM unit WHERE region_id = ?",
            (region_id,),
        ).fetchone()

        neighbours = self._conn.execute(
            """
            SELECT COUNT(*) FROM region_adjacency
             WHERE region_a = ? OR region_b = ?
            """,
            (region_id, region_id),
        ).fetchone()[0]

        self._inspector.setText(
            "\n".join(
                [
                    f"<b>{row['name']}</b>",
                    f"Owner: {row['owner'] or 'Unclaimed'}",
                    f"Terrain: {row['terrain']} ({row['kind']})",
                    f"Population: {figure(row['population'])}",
                    f"Development: {figure(row['development'])}",
                    f"Borders: {count(neighbours)} region(s)",
                    f"Units: {units['n']} (strength {say_strength(units['s'])})",
                    "",
                    f"<i>id: {region_id}</i>",
                ]
            ).replace("\n", "<br>")
        )
