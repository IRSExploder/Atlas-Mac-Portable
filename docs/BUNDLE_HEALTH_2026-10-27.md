# Bundle health check — `atlas_files_0902_to_0946.zip` (2026-10-27)

Re-run any time with:

```bash
python3 -I tools/check_bundle.py <bundle.zip> --missing-out docs/MISSING_FILES.tsv
```

## Verdict

**The files are healthy, but the game is not in the bundle.** It cannot be set up or run
from this zip: 245 of the 252 source files under `src/atlas/` are missing (97% of the source
by size), including the whole engine, simulation, SQL schema, scenarios, map and providers.

The zip is a "files viewed between 09:02 and 09:46" export from Google Drive, not a project
export. It holds docs, tests, git metadata and Qt library assets that happened to be opened,
not the repository.

## Integrity — all good

| Check | Result |
|---|---|
| Zip CRCs | OK |
| Files vs `MANIFEST.md` | 322/322 present, 0 size mismatches |
| PNG (105) | every chunk CRC valid, image data inflates |
| Python (46) | all parse |
| JSON / TOML / CSV | load cleanly; CSVs rectangular |
| Git loose objects (40) | every one hashes back to its own name |
| Git indexes (2) | checksums valid |
| `atlas_app.log` | 0 bytes (empty, not corrupt) |

## Completeness — what the repo has vs what the zip has

Matched by git blob hash against the bundled git index (1,454 tracked files):

| Area | In zip | Tracked |
|---|---:|---:|
| `src/` (the game) | **7** | 252 |
| `tests/` | 40 | 352 |
| root docs / config | 27 | 35 |
| `docs/` | 2 | 3 |
| `config/` | 1 | 1 |
| `packaging/`, `scripts/` | 0 | 4 |
| `scratchpad/` | 5 | 807 |

The 7 source files that survive (recovered from loose git objects): `cli.py`,
`config/paths.py`, `state/catalog.py`, `state/problems.py`, `ui/branches.py`,
`ui/launcher.py`, `ui/main_window.py`. Together they import 100 `atlas.*` modules, 94 of
which are absent. `python -c "import atlas.cli"` fails on its first line of imports
(`atlas.map`). Full list of the 1,372 missing paths: `MISSING_FILES_2026-10-27.tsv`.

Critical gaps for a first boot: `src/atlas/__init__.py`, `state/schema.sql`, `state/db.py`,
`engine/*` (97 files), `scenarios/*` (21), `map/*` (12), `providers/*` (17), `ui/app.py`,
and the current `pyproject.toml`.

## Other things worth knowing

- **Mixed snapshots.** The bundle has two git indexes and two HEAD logs (tip
  `b5790f5d` → `506c93fe` on `track-a-shell`). `pyproject.toml`, `packs.lock.json`,
  `ENGINE_CHANGELOG.md`, four `REVIEW_HANDOFF_*.md` and 11 test files are *older versions*
  than the ones the index tracks (e.g. the zip's `pyproject.toml` is 2,625 B, the tracked one
  5,634 B).
- **Not Atlas at all:** the 40 `.qml` files, 105 `.png` files and 2 `.obj` files are Qt /
  PySide6's own dialog and style assets (QtQuick Dialogs, macOS/iOS spinbox and text field
  skins). Eight loose git objects (six files, two trees) are React/TypeScript from an unrelated project.
- **`atlas.exe` is not the game.** It is a 46 KB `uv` console-script launcher hard-wired to
  `C:\Users\snejn\OneDrive\Desktop\Atlas Game\.venv\Scripts\python.exe`. Useless on a Mac.
- **`.git` is a pointer**, not a repository: `gitdir: C:/Users/snejn/OneDrive/Desktop/Atlas Game/.git/worktrees/gracious-poitras-5c0608`.
- **Map packs** (`admin0-50m`, `admin1-10m`, `marine-10m`) are never in the repo by design;
  they are baked with `atlas map bake` after install.
- The README's setup steps are Windows-only (`py`, `%LOCALAPPDATA%`). On macOS the
  equivalent is `python3.12 -m venv ~/atlas-venv && ~/atlas-venv/bin/pip install -e ".[dev,bake]"`
  (requires Python ≥ 3.12 and PySide6 ≥ 6.9).

## How to get a bundle that will actually run

On the Windows machine, from `C:\Users\snejn\OneDrive\Desktop\Atlas Game`, either:

```bash
git bundle create atlas-full.bundle --all     # whole history, one file, verifiable
```

or push the branch to this GitHub repo:

```bash
git remote add mac https://github.com/IRSExploder/Atlas-Mac-Portable.git
git push mac track-a-shell
```

Either one carries every tracked file with its hash, so nothing can go missing silently.
Zipping the folder also works, as long as it is the folder and not a Drive "recent files"
export.
