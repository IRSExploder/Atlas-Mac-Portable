"""Creating the QApplication, and the one setting that has to follow it.

Every entry point that puts a window on screen goes through :func:`ensure_app` rather
than calling ``QApplication([])`` itself, because there is a piece of tuning that is only
correct in the instant *after* construction, and a helper is the only way to make that
ordering hard to get wrong.
"""

from __future__ import annotations

from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import QApplication

__all__ = ["ensure_app", "tune_pixmap_cache"]

#: Qt ships a 10 MB pixmap cache. The map alone puts 427 `RegionItem`s into a
#: `DeviceCoordinateCache`, and a device-coordinate cache is a pixmap the size of the
#: item's on-screen footprint -- so at any useful zoom the working set does not come
#: close to fitting, and Qt evicts a region's raster only to re-tessellate its coastline
#: a frame later. The cache stops being a cache and becomes a tax on every pan.
_CACHE_LIMIT_KB = 32 * 1024
#: A high-DPI screen rasterises every one of those pixmaps at 2x in each direction, so
#: the same 427 items need roughly four times the bytes.
_CACHE_LIMIT_KB_HIDPI = 64 * 1024


def tune_pixmap_cache(app: QApplication | None = None) -> int:
    """Size `QPixmapCache` for the map's item caches. Returns the limit set, in KB.

    **This has to run after the QApplication exists.** Calling
    ``QPixmapCache.setCacheLimit`` earlier appears to work -- it returns nothing and
    raises nothing -- and is then silently discarded when Qt initialises its own
    application-global cache during construction. The limit reads back as 10240 and the
    map thrashes exactly as it did before, with no sign anywhere that the call was lost.
    """
    app = app or QApplication.instance()
    ratio = 1.0
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            ratio = screen.devicePixelRatio()
    limit = _CACHE_LIMIT_KB_HIDPI if ratio > 1.0 else _CACHE_LIMIT_KB
    QPixmapCache.setCacheLimit(limit)
    return limit


def ensure_app(argv: list[str] | None = None) -> QApplication:
    """Return the process's QApplication, creating and tuning it if there is none."""
    existing = QApplication.instance()
    if existing is not None:
        # A second caller must not undo the first one's tuning, but it also cannot assume
        # the app it was handed came from here -- a test that built its own still wants a
        # cache that fits the map.
        tune_pixmap_cache(existing)
        return existing  # type: ignore[return-value]
    app = QApplication(argv if argv is not None else [])
    tune_pixmap_cache(app)
    return app
