"""Build the macOS app: icon, freeze, ship the map packs, sign, smoke-test, disk image.

    python scripts/build_mac.py --allow-unpinned   # dist/Atlas.app and dist/Atlas-macOS-<arch>.dmg
    python scripts/build_mac.py --skip-freeze      # re-ship packs, re-sign, re-test
    python scripts/build_mac.py --no-dmg           # stop at the tested app

Run on a Mac (the CI workflow `.github/workflows/mac-app.yml` does) with PyInstaller and
ATLAS's `[bake,api]` extras installed and the packs baked. The steps match
`build_windows.py`, and each can fail the build:

1. **Icon.** Drawn with Qt, so the repository carries no binary art, and converted with
   `iconutil`.
2. **Freeze** with `packaging/atlas_mac.spec` into `dist/Atlas.app`.
3. **Ship the packs** into `Contents/Resources/packs`, checked against `packs.lock.json`
   exactly as on Windows (`--allow-unpinned` for packs baked on the build machine).
4. **Sign ad hoc.** Adding the packs changes the bundle after PyInstaller signed it, and an
   Apple-silicon Mac refuses a bundle whose seal is broken ("damaged"), so it is re-signed
   as a whole. Ad hoc is not a Developer ID: the first launch still needs Open Anyway.
5. **Smoke test** the frozen executable's `selftest` and a deliberate crash, as on Windows.
6. **Disk image** with the app and an Applications shortcut to drag it onto.

Off a Mac the script still freezes and smoke-tests a plain folder build (no bundle, icon,
signature or image), which checks the spec's imports and data without Apple's tools.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "atlas_mac.spec"
ON_MAC = sys.platform == "darwin"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from build_windows import ship_packs  # noqa: E402 - the one lock-checked pack copier


def _version() -> str:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["version"]


def draw_icon(build: Path) -> Path | None:
    """The in-game mark -- a brass square inside a brass frame -- on the game's navy."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

    _app = QGuiApplication.instance() or QGuiApplication([])
    size = 1024
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # macOS icon grid: an 824 px rounded square centred in 1024.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111a26"))
    painter.drawRoundedRect(QRectF(100, 100, 824, 824), 185, 185)
    brass = QColor("#e2b04a")
    painter.setBrush(Qt.BrushStyle.NoBrush)
    frame = QPen(brass, 34)
    frame.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(frame)
    painter.drawRect(QRectF(322, 322, 380, 380))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brass)
    painter.drawRect(QRectF(412, 412, 200, 200))
    painter.end()

    build.mkdir(parents=True, exist_ok=True)
    png = build / "Atlas-1024.png"
    image.save(str(png))
    if not ON_MAC:
        print(f"[icon] {png.name} (no .icns off a Mac)", flush=True)
        return None
    iconset = build / "Atlas.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = points * scale
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(["sips", "-z", str(pixels), str(pixels), str(png),
                            "--out", str(iconset / name)], check=True, capture_output=True)
    icns = build / "Atlas.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    print(f"[icon] {icns}", flush=True)
    return icns


def freeze(dist: Path, work: Path, icns: Path | None) -> None:
    print(f"[freeze] {SPEC.name} -> {dist}", flush=True)
    env = {**os.environ, "ATLAS_VERSION": _version()}
    if icns is not None:
        env["ATLAS_ICNS"] = str(icns)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(dist), "--workpath", str(work), str(SPEC)],
        cwd=ROOT, check=True, env=env)


def _layout(dist: Path) -> tuple[Path, Path, Path]:
    """(what ships, the executable, where packs go) for this platform's build."""
    if ON_MAC:
        app = dist / "Atlas.app"
        return app, app / "Contents" / "MacOS" / "ATLAS", app / "Contents" / "Resources"
    folder = dist / "ATLAS"
    return folder, folder / "ATLAS", folder


def sign(app: Path) -> None:
    if not ON_MAC:
        return
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    print("[sign] ad hoc signature applied and verified", flush=True)


def smoke(exe: Path, work: Path) -> None:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "ATLAS_BUNDLED_PACKS", "ATLAS_BOTS")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    # A clean data folder, so the test proves the *bundled* packs are found.
    appdata = work / "smoke-appdata"
    shutil.rmtree(appdata, ignore_errors=True)
    appdata.mkdir(parents=True)
    env["LOCALAPPDATA"] = str(appdata)
    report = work / "selftest.json"
    print(f"[smoke] {exe.name} selftest", flush=True)
    started = time.monotonic()
    done = subprocess.run([str(exe), "selftest", "--report", str(report)],
                          env=env, capture_output=True, text=True, timeout=900)
    print(done.stdout[-4000:], flush=True)
    if done.returncode != 0:
        print(done.stderr[-6000:], file=sys.stderr, flush=True)
        raise SystemExit(f"[smoke] selftest exited {done.returncode}")
    checks = json.loads(report.read_text(encoding="utf-8")).get("checks", [])
    print(f"[smoke] passed in {time.monotonic() - started:.1f}s: "
          + ", ".join(c["name"] for c in checks if c.get("ok")), flush=True)

    crash = work / "crash-appdata"
    shutil.rmtree(crash, ignore_errors=True)
    crash.mkdir(parents=True)
    done = subprocess.run([str(exe), "selftest", "--crash"],
                          env={**env, "LOCALAPPDATA": str(crash), "ATLAS_NO_CRASH_DIALOG": "1"},
                          capture_output=True, text=True, timeout=180)
    reports = list((crash / "Atlas" / "logs").glob("crash-*.txt"))
    if done.returncode == 0 or not reports or "deliberate crash" not in reports[0].read_text(
            encoding="utf-8"):
        raise SystemExit(f"[crash] no crash report from the frozen build "
                         f"(exit {done.returncode}): {done.stderr[-2000:]}")
    print(f"[crash] report written: {reports[0].name}", flush=True)


def disk_image(app: Path, dist: Path) -> Path:
    arch = platform.machine() or "unknown"
    stage = dist / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    # `ditto` keeps the bundle's symlinks, extended attributes and signature intact.
    subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)
    (stage / "Applications").symlink_to("/Applications")
    image = dist / f"Atlas-macOS-{arch}.dmg"
    image.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Atlas", "-srcfolder", str(stage),
                    "-ov", "-format", "UDZO", str(image)], check=True)
    print(f"[dmg] {image}  {image.stat().st_size / 1e6:.0f} MB", flush=True)
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "pyinstaller-mac")
    parser.add_argument("--packs", type=Path, default=None,
                        help="where the baked packs are (default: atlas's own packs folder)")
    parser.add_argument("--allow-unpinned", action="store_true")
    parser.add_argument("--skip-freeze", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--no-dmg", action="store_true")
    args = parser.parse_args(argv)

    shipped, exe, resources = _layout(args.dist)
    if not args.skip_freeze:
        freeze(args.dist, args.work, draw_icon(ROOT / "build"))
    if not exe.exists():
        raise SystemExit(f"no frozen build at {shipped}")

    from atlas.config.paths import packs_root

    ship_packs(resources, allow_unpinned=args.allow_unpinned,
               source=args.packs or packs_root())
    sign(shipped)
    if not args.skip_smoke:
        smoke(exe, args.work)
    size = sum(path.stat().st_size for path in shipped.rglob("*") if path.is_file())
    print(f"[done] {shipped}  {size / 1e6:.0f} MB", flush=True)
    if ON_MAC and not args.no_dmg:
        disk_image(shipped, args.dist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
