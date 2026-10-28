# PyInstaller spec for the Windows build. Run through `scripts/build_windows.py`, which
# also ships the map packs and smoke-tests the result; running this file alone produces a
# build with no maps.
#
# One folder, two executables over the same files:
#   ATLAS.exe      -- windowed, what a player double-clicks
#   atlas-cli.exe  -- console, for `selftest`, `doctor` and every other subcommand
#
# The geometry stack (`[bake]`: shapely, topojson, requests) and the dev tools are
# excluded: a player never bakes a map, and the packs ship pre-baked.

from pathlib import Path

import os

ROOT = Path(SPECPATH).resolve().parent
# Drawn by `scripts/build_windows.py` (the same mark as the Mac icon) and handed over here.
ICON = Path(os.environ.get("ATLAS_ICO") or ROOT / "packaging" / "atlas.ico")

# The same globs as `[tool.setuptools.package-data]` in pyproject.toml, read straight from
# the source tree. Not `collect_data_files("atlas")`: that imports the package in a clean
# subprocess, which in the build venv (ATLAS is not installed there) finds nothing and
# collects nothing -- the first frozen build shipped no fonts, no schema and no scenarios,
# and only the smoke test noticed.
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

# Every module under src/atlas, because scenarios (and a few other things) are imported by
# name -- `importlib.import_module(f"atlas.scenarios.{...}")` -- which PyInstaller's static
# import graph cannot see. Found the same way as the data files: the second frozen build
# loaded its fonts and packs and then failed to build a world on
# `No module named 'atlas.scenarios.modern_day'`.
hiddenimports = sorted(
    ".".join(("atlas", *found.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
    for found in SRC.rglob("*.py")
)
# The API provider is imported lazily (only `--api` reaches it) and keyring finds its
# backends through package metadata at run time; both are invisible to the import graph.
hiddenimports += ["anthropic", "httpx", "keyring", "keyring.backends.Windows",
                  "keyring.backends.fail"]

EXCLUDES = [
    "shapely", "topojson", "requests", "numpy", "pytest", "_pytest", "ruff",
    "tkinter", "pydoc_data",
    # Qt modules ATLAS never imports; the PySide6 hook only collects what is imported,
    # these just make that explicit.
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

common = dict(
    exclude_binaries=True,
    debug=False,
    strip=False,
    upx=False,
    icon=str(ICON) if ICON.exists() else None,
)
gui = EXE(pyz, a.scripts, [], name="ATLAS", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="atlas-cli", console=True, **common)

COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="ATLAS")
