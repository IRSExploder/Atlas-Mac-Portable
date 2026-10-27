# PyInstaller spec for the macOS build. Run through `scripts/build_mac.py`, which also
# draws the icon, ships the map packs into the bundle, re-signs it, smoke-tests it and wraps
# it in a disk image; running this file alone produces an app with no maps.
#
# One executable, unlike Windows: a Mac has no console/windowed split, so the same
# `Contents/MacOS/ATLAS` opens the game on a double-click and is the ordinary `atlas`
# command line when run from Terminal with arguments (`.../ATLAS selftest`).
#
# Kept beside `atlas.spec` rather than merged into it: the two differ in what they bundle
# (the API provider and the Keychain here) and in how the result is laid out.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ICON = Path(os.environ.get("ATLAS_ICNS", ROOT / "build" / "Atlas.icns"))
VERSION = os.environ.get("ATLAS_VERSION", "0.1.0")

# Same globs, and the same reason for reading them from the tree, as `atlas.spec`.
PACKAGE_DATA = [
    "state/*.sql",
    "scenarios/*.json",
    "scenarios/territory/*.json",
    "testing/manifests/*.json",
    "ui/fonts/*.ttf",
    "ui/fonts/*.txt",
]

SRC = ROOT / "src" / "atlas"
datas = []
for pattern in PACKAGE_DATA:
    for found in sorted(SRC.glob(pattern)):
        datas.append((str(found), str(Path("atlas") / found.parent.relative_to(SRC))))
if not any(dest.endswith("fonts") for _src, dest in datas):
    raise SystemExit("no fonts found under src/atlas/ui/fonts; refusing to build")

hiddenimports = sorted(
    ".".join(("atlas", *found.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
    for found in SRC.rglob("*.py")
)
# The API provider is imported lazily (only `--api` reaches it) and keyring finds its
# backends through package metadata at run time; both are invisible to the import graph.
hiddenimports += ["anthropic", "keyring", "keyring.backends.macOS", "keyring.backends.fail"]

EXCLUDES = [
    "shapely", "topojson", "requests", "numpy", "pytest", "_pytest", "ruff",
    "tkinter", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
]

a = Analysis(
    [str(ROOT / "packaging" / "atlas_entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], name="ATLAS", console=False, exclude_binaries=True,
          debug=False, strip=False, upx=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ATLAS")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Atlas.app",
        icon=str(ICON) if ICON.exists() else None,
        bundle_identifier="io.github.irsexploder.atlas",
        version=VERSION,
        info_plist={
            "CFBundleName": "Atlas",
            "CFBundleDisplayName": "Atlas",
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            # Follow the system's light or dark appearance, as the game's own theme does.
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.strategy-games",
        },
    )
