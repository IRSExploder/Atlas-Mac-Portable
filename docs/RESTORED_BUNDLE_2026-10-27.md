# Restored bundle check — `atlas_project_restored.zip` (2026-10-27)

## Verdict

**Playable.** This is a real project tree. It has every source file the game needs, installs
cleanly, bakes its maps, runs turns, and opens the full game window. It is an **older,
self-consistent snapshot**, not the `track-a-shell` tip, and parts of the test suite and
the dev scratchpad are missing.

## What was checked

| Check | Result |
|---|---|
| Zip CRCs | OK, 382 files |
| `src/atlas/` vs git index (1,454 tracked) | **252 / 252 present** |
| Root docs, `docs/`, `packaging/`, `scripts/` | all present |
| `tests/` | 88 / 352 present |
| `scratchpad/` (dev tools, recorded tapes) | 0 / 807 present |
| `pip install -e ".[dev,bake]"` (Python 3.13, PySide6 6.12) | OK |
| `atlas newgame` | world created; legal diff applied; illegal diff rejected by all 6 rules; rollback leaves fingerprint unchanged |
| `atlas map bake` (admin0-50m, marine-10m, admin1-10m) | all three bake, all shared-border checks pass |
| `atlas eve --era gathering-storm --mock --rounds 4` | 4 turns simulated (1936-01 → 1936-12); blocs silent as designed under the mock |
| Game window (offscreen, France, 1936) | opens and renders map, events, council, diplomacy, orders and Advance |
| `./setup_mac.sh` from a clean venv | OK in ~1 min, including the map bakes |
| Test suite | see below |

## Test suite

Full run with all three packs baked (`pytest --continue-on-collection-errors`, 12.5 min):

**1,243 passed, 5 failed, 54 skipped, 1 xfailed, 412 errors.**

Everything that does not pass is explained by files missing from the bundle or by the
version mix. None of it points at the game being broken:

- **`tests/conftest.py` is missing** (5,772 B in the index) and is not in either zip. ~380
  tests error with `fixture 'conn' not found`, and 4 test modules (`test_apply`,
  `test_partial_apply`, `test_rules`, `test_a_bounded_column_costs_only_its_own_write`)
  cannot even be imported. Plain `pytest` stops at collection, so run it with
  `--continue-on-collection-errors` until conftest is back.
- **`tests/test_the_older_tables_rewind_whatever_you_do_to_them.py` is missing**, and 26
  tests import helpers from it.
- **`scratchpad/cassettes/` is missing** (102 files), so the PT34d regression cannot replay
  its recorded answer, and two `test_release_audit` gates fail on the absent
  `cassettes/INDEX.json` (3 failures).
- **Version mix (2 failures).** A few tests are newer than the source:
  `test_every_duration_is_on_a_clock_somebody_named` expects the later `CAMPAIGN_TURNS`
  clock change, and `test_the_window_reviews_the_orders_a_jump_would_carry` expects the
  later `_pending_orders` draft-orders work in `main_window.py`.

## Why not swap in the newer files from the first bundle

The first zip carries `track-a-shell` versions of `cli.py`, `config/paths.py`,
`ui/main_window.py` and `ui/branches.py`. Dropping them in breaks the import graph:
`main_window.py` then needs `save_app_preferences` from a newer `ui/preferences.py`,
which neither zip has. Keeping the restored source as one consistent snapshot is
better than a half-upgraded one.

## Fixes applied on top of the zip (each one hash-verified against the git index)

- `CHANGELOG.md` in the zip was a byte-for-byte copy of `ENGINE_CHANGELOG.md` (597 KB). It
  has been replaced with the real 38 KB `CHANGELOG.md` from the first bundle.
- `tests/__init__.py` restored (it is an empty file).
- `config/dev_forbidden_roots.txt` restored from the first bundle.
- File modes normalised to 644. Every file in the zip was marked executable.

## Still differing from the index (older versions; left as they are)

`ENGINE_CHANGELOG.md`, `REVIEW_HANDOFF_COLDWAR.md`, `REVIEW_HANDOFF_GAMEPLAY.md`,
`REVIEW_HANDOFF_PACING_AND_DOCTRINES.md`, `REVIEW_HANDOFF_WARLOOP_CONSTITUENCIES.md`,
`packs.lock.json`, `pyproject.toml`, `src/atlas/{cli.py, config/paths.py,
scenarios/territory.py, testing/audit.py, ui/branches.py, ui/main_window.py,
ui/preferences.py}` and 11 test files.

## Map packs vs `packs.lock.json`

Freshly baked packs differ from the pinned hashes by a few hundred bytes each (e.g.
admin0-50m 2,269,241 B vs 2,269,036 B pinned). That is expected when shapely/GEOS differ from
the Windows bake. Every pack passes its own border verification. Run `atlas map pin` after
baking on the Mac if you want the lock to match your machine.

## macOS notes

- Worlds and packs go to `$LOCALAPPDATA/Atlas`, which on a Mac falls back to
  `~/AppData/Local/Atlas`. It looks odd but it is harmless and keeps the Windows layout.
- `atlas doctor` reports the claude / codex / agy CLIs as missing until they are installed
  and on `PATH`. `--mock` plays without any of them.
- `atlas.exe` and `packaging/atlas.spec` are the Windows build. Nothing Mac-specific is
  packaged. Launch with `~/.atlas-venv/bin/atlas new`.

## To get to a complete tree

From the Windows machine, `git bundle create atlas-full.bundle --all` (or `git push` to this
repository) would bring the missing tests, `conftest.py`, the scratchpad tapes and the
`track-a-shell` tip in one go.

## Update: `atlas_missing_fixes.zip` applied, plus one macOS fix

All 14 files were hash-identical to the git index. All 252 files in `src/atlas/` now match the
`track-a-shell` tip, and `tests/conftest.py` and the active `pt79-cold-war` tape are back.

**macOS fix in `providers/resolver.py`.** `claude` and `codex` found on `PATH` were refused
unless they had a file extension, which is a Windows-only rule (a bare file there is npm's shell
script beside the `.cmd`). On a Mac every real executable is bare, so a normal install of either
CLI was invisible and real-model play could never start. `agy` already used
`bare_ok=os.name != "nt"`; claude and codex now do too. Homebrew's Apple Silicon npm root
(`/opt/homebrew/lib/node_modules`) was also added to the search list. Verified with stand-in
executables: before the fix claude/codex were NOT FOUND; after it, `atlas probe` runs a claude
call through the per-call sandbox and the model attestation reads `MATCH`.

**Functionality still affected by missing or older files:**
- `pyproject.toml` (older) leaves `ui/fonts/*` out of package-data. That is harmless for the
  editable install `setup_mac.sh` does, but a plain `pip install .` would lose the bundled fonts.
- `packs.lock.json` (older) is only read by `atlas map check`, `map pin` and the release audit.
- Everything else missing is tests or developer scratchpad. `src/` only touches `scratchpad/`
  from the release audit and the lab tooling, never from gameplay.

**Full suite after the fixes:** 1,813 passed, 5 failed, 54 skipped, 0 errors (12 min, all packs
baked; run before the resolver fix, which touches no tested path except `test_providers`, 125/125
green after it). The 5 failures are all missing test or developer files, not game behaviour:
- `test_release_audit` x2: `scratchpad/tape_replay.py` is missing, so the active tape cannot be
  replayed.
- `test_the_older_tables_rewind...` x2: imports `tests/test_the_new_tables_rewind_whatever_you_do_to_them.py`,
  which is missing.
- `test_the_window_reviews_the_orders_a_jump_would_carry`: this test file is the older version and
  does not match the current `main_window.py`.
