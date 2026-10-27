# Track A — progress on `track-a-shell`

*Branch `track-a-shell`, cut from `integration/complete-2026-09-28` (`0c95441a`), not from
`master`: master is 121 commits behind and those commits touch the same files (cli.py,
pyproject, map/, main_window). Nothing is merged anywhere. Every item was built and tested
with the model stubbed (`MockProvider`, the deterministic bots, recorded cassettes) — no live
model call, no GPU.*

## Done

| # | Item | Where | Tested with |
|---|---|---|---|
| A1 | `atlas play/new --replay TAPE [--replay-mode text\|loose\|exact] --bots SCOPES` | `providers/devreplay.py`, `docs/DEV_OFFLINE.md` | a real jump narrated from the 1972 tape on the sandbox world |
| A2 | Fonts + OFL licences in package-data; every tracked data file held to a glob | `pyproject.toml` | a real wheel build |
| A3 | Packs ship beside the exe; shipped wins over a baked copy; "reinstall", never "bake", in an install | `config/paths.py: pack_file` | path-resolution tests; the frozen build |
| A4 | Frozen Windows build: `ATLAS.exe` + `atlas-cli.exe`, packs checked against the lock, frozen self-check, crash check, zip | `packaging/atlas.spec`, `scripts/build_windows.py`, `atlas selftest` | the frozen exe itself |
| A5 | Crash handler: rotating log, faulthandler, crash reports from any thread, crash dialog | `runtime/crash.py` | unit + a real crashing process + `selftest --crash` on the frozen exe |
| A6 | Newer / synced / unmigratable / damaged saves are a sentence on every surface: menu, setup, every subcommand (exit 4), export, fork, rewind | `state/problems.py` | each surface |
| A7 | `ATLAS_DATA_DIR`; `LAYOUT` classifies every data folder cloud vs local; the developer's forbidden folders moved to `config/dev_forbidden_roots.txt` (checkout only) | `config/paths.py` | path tests |
| A8 | Main menu: Continue / New / Load / Settings / Quit, provider-status slot, back to menu on close | `ui/main_menu.py`, `ui/launcher.py`, `state/catalog.py`, `atlas start` | full offscreen loop |
| A9 | Settings sheet for the four preferences, app-level defaults, in-game Ctrl+, | `ui/settings.py`, `ui/preferences.py` | offscreen |
| A10 | Campaign end: victory / superseded / 150-turn limit, "end here / play on", score breakdown | `engine/campaign.py`, `ui/endscreen.py` | seeded states + the real window |
| A11 | Defeat: eliminated or holding no ground | same | the real window on the modern-day map |

**Packs re-baked to the lock.** shapely 2.1.2, topojson 1.10, numpy 2.5.3, Natural Earth
`ca96624a` reproduce `packs.lock.json` byte for byte. They were baked into
`%LOCALAPPDATA%\atlas-rebake` (the build venv holds the `[bake]` stack); the shared
`%LOCALAPPDATA%\Atlas\packs` was left alone because other worktrees and the play folder read
it. The release build uses them: `python scripts/build_windows.py --packs
%LOCALAPPDATA%\atlas-rebake --zip` → `all_pinned: true`, self-check green, crash report written.

## Full test suite (2026-10-18/19, normal priority, offscreen)

| Run | Tree | Packs | Result |
|---|---|---|---|
| 1 | A1-A4, A8-A11 (`a5ed69b1`) | dev packs in app data | 6368 passed, **3 failed**: the known pt79 MISS (x2) and `CAMPAIGN_TURNS` missing from the clock registry -- fixed in `b5790f5d` |
| 2 | HEAD (A1-A11) | dev packs in app data | 6386 passed, **2 failed** (the known pt79 MISS, both release-audit tests), 249 skipped |
| 3 | HEAD | re-baked, lock-matching packs, fresh `LOCALAPPDATA` | 6386 passed, **2 failed** -- identical; the pinned packs change nothing and are not the cause of the pt79 MISS |

The pt79 `cassettes` gate is the integration branch's own pre-existing red (2 failed there
too); it needs a live re-record (track C9). Pointing a whole suite at other packs with
`ATLAS_BUNDLED_PACKS` or `ATLAS_DATA_DIR` is the wrong instrument: 31 fixtures assert packs
live in `packs_root()` and some tests isolate via `LOCALAPPDATA`, which `ATLAS_DATA_DIR`
overrides. Use a temp `LOCALAPPDATA` with `Atlas/packs` instead (run 3).

## Build

```powershell
# separate build venv, so the dev venv stays as it is
C:\Users\snejn\AppData\Local\atlas-build-venv\Scripts\python.exe scripts\build_windows.py --packs C:\Users\snejn\AppData\Local\atlas-rebake --zip
```

## Decisions taken without asking (easy to change)

- Branch base: the integration branch, as above.
- Campaign length 150 turns (`engine/campaign.py: CAMPAIGN_TURNS`, per-world override in the
  workspace, 0 = open-ended). Victory offers "play on" rather than ending.
- `atlas` exits **4** when a world file is the problem (1–3 already mean other things).

## Not done / blocked

- **Installer:** Inno Setup is not installed on this machine; skipped as instructed. Steam
  ships the folder; the zip covers off-Steam testing.
- **Clean-VM CI** for the frozen build: not set up.
- **Release audit `cassettes` gate:** still red — `pt79-cold-war` MISS at turn 2, the known
  pre-existing failure (fix E2). Needs a live re-record (track C9).
- Remaining track A: A12–A24 (map labels, "what can I order", DPI, Steamworks, Steam Cloud
  config from `LAYOUT`, SteamPipe, art, notices, "report this text", SQLite audit, …).
