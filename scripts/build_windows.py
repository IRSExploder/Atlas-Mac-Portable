"""Build the Windows release folder: freeze, ship the map packs, smoke-test the result.

    python scripts/build_windows.py                 # dist/ATLAS, packs must match the lock
    python scripts/build_windows.py --allow-unpinned  # dev build with whatever packs are baked
    python scripts/build_windows.py --skip-freeze     # re-ship packs and re-run the smoke test
    python scripts/build_windows.py --zip             # also write dist/ATLAS-<version>-win64.zip

Run it with an interpreter that has PyInstaller and PySide6 installed (the project keeps a
separate build venv for this so the dev venv stays as it is). Three steps, each of which
can fail the build:

1. **Freeze** with `packaging/atlas.spec` (PyInstaller, one folder, two executables).
2. **Ship the packs.** Every pack `packs.lock.json` pins is copied into `dist/ATLAS/packs`
   after its sha256 is checked against the lock. A mismatch fails the build: a release must
   ship the bytes the release audit was green against. `--allow-unpinned` ships whatever is
   baked locally instead, for a dev build, and says so in `packs/SHIPPED.json`.
3. **Smoke test** the frozen `atlas-cli.exe selftest`: the shipped fonts load, the shipped
   packs open, a world is built and three turns run on the mock provider with bots, and the
   game window opens offscreen. Nothing in it calls a model.

Exit 0 only if all three passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "atlas.spec"
LOCK = ROOT / "packs.lock.json"


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def _baked_packs_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Atlas" / "packs"


def draw_ico(build: Path) -> Path | None:
    """The Mac icon's mark, as a Windows .ico. None (and no icon) if Qt cannot write one."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_mac import draw_icon
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    draw_icon(build)
    image = QImage(str(build / "Atlas-1024.png")).scaled(
        256, 256, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    ico = build / "Atlas.ico"
    if not image.save(str(ico), "ICO"):
        print("[icon] Qt could not write an .ico; building without an icon", flush=True)
        return None
    print(f"[icon] {ico}", flush=True)
    return ico


def freeze(dist: Path, work: Path) -> None:
    print(f"[freeze] {SPEC.name} -> {dist}", flush=True)
    env = dict(os.environ)
    ico = draw_ico(work.parent)
    if ico is not None:
        env["ATLAS_ICO"] = str(ico)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(dist), "--workpath", str(work), str(SPEC)],
        cwd=ROOT, check=True, env=env)


def ship_packs(app: Path, *, allow_unpinned: bool, source: Path) -> list[dict]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    target = app / "packs"
    target.mkdir(parents=True, exist_ok=True)
    shipped: list[dict] = []
    problems: list[str] = []
    for record in lock["packs"]:
        name = record["name"]
        found = source / name
        if not found.exists():
            problems.append(f"{name}: not baked at {found}")
            continue
        digest = _sha256(found)
        pinned = digest == record["sha256"]
        if not pinned and not allow_unpinned:
            problems.append(f"{name}: sha256 {digest[:12]} but the lock pins "
                            f"{record['sha256'][:12]}; re-bake or pass --allow-unpinned")
            continue
        shutil.copy2(found, target / name)
        shipped.append({"name": name, "sha256": digest, "bytes": found.stat().st_size,
                        "pinned": pinned})
        print(f"[packs] {name}  {'pinned' if pinned else 'UNPINNED'}  "
              f"{found.stat().st_size / 1e6:.1f} MB", flush=True)
    if problems:
        raise SystemExit("[packs] refused:\n  " + "\n  ".join(problems))
    (target / "SHIPPED.json").write_text(json.dumps({
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "all_pinned": all(item["pinned"] for item in shipped),
        "packs": shipped,
    }, indent=2) + "\n", encoding="utf-8")
    return shipped


def smoke(app: Path, report: Path) -> dict:
    exe = app / "atlas-cli.exe"
    print(f"[smoke] {exe.name} selftest", flush=True)
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "ATLAS_BUNDLED_PACKS", "ATLAS_BOTS")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    started = time.monotonic()
    done = subprocess.run([str(exe), "selftest", "--report", str(report)],
                          env=env, cwd=app, capture_output=True, text=True, timeout=600)
    print(done.stdout[-4000:], flush=True)
    if done.returncode != 0:
        print(done.stderr[-4000:], file=sys.stderr, flush=True)
        raise SystemExit(f"[smoke] selftest exited {done.returncode}")
    result = json.loads(report.read_text(encoding="utf-8"))
    print(f"[smoke] passed in {time.monotonic() - started:.1f}s", flush=True)
    crash_check(exe, env, report.parent / "crash-appdata")
    return result


def crash_check(exe: Path, env: dict, appdata: Path) -> None:
    """A deliberate crash in the frozen exe must leave a report under the logs folder."""
    shutil.rmtree(appdata, ignore_errors=True)
    appdata.mkdir(parents=True)
    done = subprocess.run([str(exe), "selftest", "--crash"],
                          env={**env, "LOCALAPPDATA": str(appdata),
                               "ATLAS_NO_CRASH_DIALOG": "1"},
                          capture_output=True, text=True, timeout=120)
    reports = list((appdata / "Atlas" / "logs").glob("crash-*.txt"))
    if done.returncode == 0 or not reports or "deliberate crash" not in reports[0].read_text(
            encoding="utf-8"):
        raise SystemExit(f"[crash] no crash report from the frozen build "
                         f"(exit {done.returncode}): {done.stderr[-2000:]}")
    print(f"[crash] report written: {reports[0].name}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "pyinstaller")
    parser.add_argument("--packs", type=Path, default=None,
                        help="where the baked packs are (default %%LOCALAPPDATA%%/Atlas/packs)")
    parser.add_argument("--allow-unpinned", action="store_true")
    parser.add_argument("--skip-freeze", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--zip", action="store_true",
                        help="also archive the tested folder, for installing off Steam")
    args = parser.parse_args(argv)

    app = args.dist / "ATLAS"
    if not args.skip_freeze:
        freeze(args.dist, args.work)
    if not (app / "ATLAS.exe").exists():
        raise SystemExit(f"no frozen build at {app}")
    ship_packs(app, allow_unpinned=args.allow_unpinned,
               source=args.packs or _baked_packs_root())
    if not args.skip_smoke:
        smoke(app, args.work / "selftest.json")
    size = sum(path.stat().st_size for path in app.rglob("*") if path.is_file())
    print(f"[done] {app}  {size / 1e6:.0f} MB", flush=True)
    if args.zip:
        import tomllib

        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"]["version"]
        archive = shutil.make_archive(str(args.dist / f"ATLAS-{version}-win64"), "zip",
                                      root_dir=args.dist, base_dir="ATLAS")
        print(f"[zip] {archive}  {Path(archive).stat().st_size / 1e6:.0f} MB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
