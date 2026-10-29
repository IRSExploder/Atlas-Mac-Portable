"""An occupied or besieged province is hatched in fine stripes at every zoom.

The hatch is an 8x8 texture, and a texture brush is scaled with everything else the
painter draws. The map's units are degrees, so at the zoom a game opens at each texel was
tens of pixels wide and the stripe became a staircase of huge squares across the province
-- which players reported as grey blocks over Afghanistan and Pakistan. The item now lays
the hatch in screen pixels.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
)

from atlas.map.scene_model import FillPattern, RegionVisual  # noqa: E402
from atlas.ui.map_items import RegionItem  # noqa: E402

ZOOM = 60.0  # one map unit is sixty screen pixels


def _hatched_square() -> RegionItem:
    item = RegionItem("r_test")
    path = QPainterPath()
    path.addRect(0, 0, 10, 10)
    item.apply(RegionVisual(
        region_id="r_test", geo_key="g", lod=0, fill="#505650", outline="#000000",
        outline_width=0.0, pattern=FillPattern.HATCHED, pattern_colour="#B5473F"), path)
    return item


def test_the_hatch_stays_fine_however_far_in_the_map_is_zoomed():
    QApplication.instance() or QApplication([])
    image = QImage(200, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    painter.scale(ZOOM, ZOOM)
    _hatched_square().paint(painter, QStyleOptionGraphicsItem())
    painter.end()

    # Along any 16-pixel run of one row the stripe and the ground both appear: with the
    # texture magnified sixty times, a run that short would be a single flat colour.
    for y in (20, 77, 150):
        colours = {image.pixelColor(x, y).name() for x in range(40, 56)}
        assert len(colours) >= 2, (y, colours)


def test_a_selected_province_draws_no_dotted_box():
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    item = _hatched_square()
    scene.addItem(item)
    item.setFlag(RegionItem.GraphicsItemFlag.ItemIsSelectable, True)
    item.setSelected(True)
    view = QGraphicsView(scene)
    option = QStyleOptionGraphicsItem()
    from PySide6.QtWidgets import QStyle

    option.state |= QStyle.StateFlag.State_Selected
    image = QImage(200, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    painter.scale(ZOOM / 4, ZOOM / 4)
    item.paint(painter, option)
    painter.end()
    assert not option.state & QStyle.StateFlag.State_Selected
    del view


def test_ground_under_attack_is_dotted_and_ground_taken_is_striped(conn):
    """Asked for in play: occupied and besieged land looked the same. Taken ground stripes
    with the occupier; ground still being fought for is dotted with the attacker."""
    from atlas.map.legend import LEGENDS, SwatchKind
    from atlas.map.query import load_map_view
    from atlas.map.render import build_scene
    from atlas.map.scene_model import MapLayer

    conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = 'sandbox' AND"
                 " nation_a IN ('aurelia', 'karthos') AND nation_b IN ('aurelia', 'karthos')")
    conn.execute("INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
                 " pressure, opened_turn, last_turn) VALUES"
                 " ('c1', 'sandbox', 'r_south', 'aurelia', 'karthos', 40, 1, 1)")
    conn.execute("UPDATE region SET controller_nation_id = 'aurelia' WHERE id = 'r_west'")

    class _Flat:
        key = "flat"

        def project(self, lon, lat):
            return lon, -lat

    view = load_map_view(conn, "sandbox", observer_nation_id="aurelia")
    scene = build_scene(view, projection=_Flat(), pack_id="sandbox")
    pattern = {v.region_id: v.pattern for v in scene.regions}
    assert pattern["r_south"] is FillPattern.SIEGE
    assert pattern["r_west"] is FillPattern.HATCHED
    for layer in (MapLayer.POLITICAL, MapLayer.FRONTS):
        assert SwatchKind.SIEGE in {entry.swatch for entry in LEGENDS[layer].entries}


def test_the_siege_texture_is_not_the_occupation_texture():
    from atlas.ui.map_items import _dots, _hatch

    QApplication.instance() or QApplication([])
    base, over = QColor("#505650"), QColor("#B5473F")
    assert _dots(base, over).toImage() != _hatch(base, over).toImage()
