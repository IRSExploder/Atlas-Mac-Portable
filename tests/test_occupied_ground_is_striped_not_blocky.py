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
