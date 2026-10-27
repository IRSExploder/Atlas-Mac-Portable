"""Command line entry point.

Every phase adds a subcommand that makes that phase's work independently exercisable
without the GUI. Phase 1 ships ``newgame``, which creates a world, pushes a legal batch
and an illegal one through the diff pipeline, and prints the fingerprint before and
after -- enough to see the whole mutation core work end to end.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
from pathlib import Path

from .map.sources import SOURCES
from .scenarios.presets import builtin_presets, has_map
from .scenarios.sandbox import sandbox_seed
from .state.apply import apply_batch
from .state.bootstrap import create_world, fingerprint
from .state.db import world_db_path
from .state.diff import Diff, DiffBatch
from .state.models import Difficulty, DiffOp, Role, Visibility

__all__ = ["main"]


def _packs_for_world(db_path: Path, world_id: str, map_source: str) -> list[Path]:
    """Which packs an existing world has to be read through, finest first.

    Read from the world rather than from ``--source``, because whether a world is drawn
    at province granularity is a fact about how it was seeded, not about the flag someone
    passed today.
    """
    import sqlite3

    from .scenarios.territory import pack_paths
    from .state.db import read_only_uri

    preset = None
    try:
        with sqlite3.connect(read_only_uri(db_path), uri=True) as probe:
            row = probe.execute(
                "SELECT preset_id FROM world WHERE id = ?", (world_id,)
            ).fetchone()
            preset = row[0] if row else None
    except sqlite3.Error:
        preset = None
    return pack_paths(preset or "", map_source)


#: What SQLite may leave beside a database file. A stale ``-journal`` next to a new file of
#: the same name is replayed into it as a hot journal, so these go with the database.
_SIDECARS = ("-wal", "-shm", "-journal")


def _is_the_worlds_own_directory(db_path: Path, world_id: str) -> bool:
    """Whether ``db_path`` is the managed default *and* its directory is provably the world's.

    Comparing against :func:`world_db_path` alone is not enough: ``--world-id ..`` makes that
    ``worlds_root()/../world.db``, whose directory is the whole ATLAS data folder. So the
    directory has to sit directly under :func:`worlds_root`. That is asked of the filesystem
    with ``samefile`` rather than of the spelling, for the MSIX redirect ``config.paths``
    describes; any doubt answers no, which only costs the narrower delete.
    """
    import os

    from .config.paths import worlds_root

    try:
        target = db_path.resolve()
        if target != world_db_path(world_id).resolve():
            return False
        return os.path.samefile(target.parent.parent, worlds_root())
    except OSError:
        return False


def _discard_world(db_path: Path, world_id: str) -> None:
    """Remove an existing world so a new one can be created at ``db_path``.

    **Only the managed default loses its directory.** ``worlds_root()/<id>/`` holds that
    world and nothing else -- forks are sibling directories, packs live under
    ``packs_root()`` -- so clearing it, as it always did, also clears stale sidecars and any
    pre-migration copy. A ``--path`` is a file in a folder the user chose, and the folder is
    theirs: ``rmtree(path.parent)`` there deleted Documents, or the checkout it was run from.
    So a user path loses the database and its SQLite sidecars and nothing else.
    """
    if _is_the_worlds_own_directory(db_path, world_id):
        shutil.rmtree(db_path.parent, ignore_errors=True)
        return
    for doomed in (db_path, *(db_path.with_name(db_path.name + s) for s in _SIDECARS)):
        doomed.unlink(missing_ok=True)


def _cmd_newgame(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else world_db_path(args.world_id)
    if path.exists():
        if not args.force:
            print(f"{path} already exists; pass --force to replace it", file=sys.stderr)
            return 1
        _discard_world(path, args.world_id)

    seed = sandbox_seed(
        world_id=args.world_id, seed=args.seed, difficulty=Difficulty(args.difficulty)
    )
    conn = create_world(path, seed)
    print(f"created world {seed.id!r} ({seed.name}) at {path}")
    print(f"  seed={seed.rng_seed}  difficulty={seed.difficulty.value}  date={seed.start_date}")

    genesis = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis'", (seed.id,)
    ).fetchone()["id"]
    before = fingerprint(conn, seed.id)
    print(f"\nfingerprint (fresh)    {before}")

    # A legal batch: a modest economic shift, properly attributed to the genesis event.
    legal = DiffBatch(
        world_id=seed.id,
        turn=0,
        proposer_role=Role.WORLD_SIM,
        proposer_model="mock",
        diffs=(
            Diff(
                target_table="nation",
                target_id="karthos",
                field="economy",
                op=DiffOp.ADJUST,
                new_value=2.0,
                reason_event_id=genesis,
                visibility_scope=Visibility.PUBLIC,
            ),
        ),
    )
    outcome = apply_batch(conn, legal, difficulty=seed.difficulty)
    print(f"\nlegal batch    -> applied={outcome.applied}")
    after_legal = fingerprint(conn, seed.id)
    print(f"fingerprint (applied)  {after_legal}")

    # An illegal batch, three ways at once: no citation, an engine-only column, and a
    # magnitude far past the per-turn cap. Each should be caught by its own rule.
    illegal = DiffBatch(
        world_id=seed.id,
        turn=0,
        proposer_role=Role.DIPLOMACY,
        proposer_model="mock",
        proposer_nation_id="karthos",
        diffs=(
            Diff("nation", "karthos", "economy", DiffOp.SET, 9999.0, reason_event_id=None),
            Diff("region", "r_north", "owner_nation_id", DiffOp.SET, "karthos",
                 reason_event_id=genesis),
        ),
    )
    outcome2 = apply_batch(conn, illegal, difficulty=seed.difficulty)
    print(f"\nillegal batch  -> applied={outcome2.applied}")
    for violation in outcome2.validation.violations:
        print(f"  [{violation.rule_id}] {violation.reason}")

    after_illegal = fingerprint(conn, seed.id)
    print(f"\nfingerprint (rolled back)  {after_illegal}")
    if after_illegal == after_legal:
        print("  -> unchanged: the rejected batch left no trace in world state")
    else:
        print("  -> MISMATCH: a rejected batch mutated the world", file=sys.stderr)
        return 2

    kept = conn.execute(
        "SELECT status, COUNT(*) AS n FROM diff_batch WHERE world_id = ? GROUP BY status",
        (seed.id,),
    ).fetchall()
    print("\naudit trail:")
    for row in kept:
        print(f"  {row['status']:<10} {row['n']} batch(es)")
    print("  (the rejected proposal is still on the record, which is the point)")

    conn.close()
    return 0


def _cmd_rules(_args: argparse.Namespace) -> int:
    from .state.validate import all_rules

    for rule in all_rules():
        print(f"{rule.id:<10} {rule.description}")
    return 0


def _add_offline_flags(parser: argparse.ArgumentParser) -> None:
    """The flags that let the game window run with no model behind it at all.

    `--mock` alone gives empty narrative; `--replay` adds real recorded text; `--bots`
    makes the untracked-by-the-player world move. Together they are the dev profile in
    `docs/DEV_OFFLINE.md`.
    """
    parser.add_argument("--mock", action="store_true", help="use the mock provider, no real calls")
    parser.add_argument(
        "--replay", metavar="TAPE",
        help="answer from a recorded cassette (.jsonl) instead of a model; implies no real calls",
    )
    parser.add_argument(
        "--replay-mode", choices=("text", "loose", "exact"), default="text",
        help="text: recorded prose only, works on any world (default); loose/exact: whole "
             "recorded answers, for the world the tape was made in",
    )
    parser.add_argument(
        "--bots", metavar="SCOPES",
        help="comma-separated bot scopes (tracked,gambit,organisation) or 'all'; "
             "deterministic controllers answer instead of a model",
    )
    parser.add_argument(
        "--api", action="store_true",
        help="Claude over the Anthropic API (key in ATLAS_ANTHROPIC_API_KEY) writes the world, "
             "talks, the council and odd orders; rule-based bots parse orders and play the "
             "minor powers, covert channel and Security Council",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="rule-based bots play every AI seat -- other powers, your orders, talks, "
             "the council, the world narrative -- and no model is ever called",
    )


def _offline_providers(args: argparse.Namespace) -> dict | None:
    """The providers the offline flags ask for, or ``None`` to use the real CLIs.

    Also moves the bot switches, because they have to be set before the first turn and
    this is the one place both window commands pass through.
    """
    from .engine import bots
    from .providers.mock import MockProvider

    if getattr(args, "api", False):
        from .providers.api_provider import API_KEY_ENV, ApiProvider, api_role_models
        from .providers.cli_provider import use_api_pins

        api = ApiProvider()
        if not api.available():
            # Not fatal: the window opens, and the key can be added under Settings > AI
            # providers. The provider reads it on every call, so no restart is needed.
            print(f"api: no key yet; add it in Settings > AI providers, or set {API_KEY_ENV}")
        # The hybrid: a model where prose and judgement show, a rule where they do not.
        bots.set_bots(roles=bots.SCOPES | {"parser"})
        pins = api_role_models()
        use_api_pins(pins)
        print("api: " + ", ".join(f"{role.value} on {pins[role][1]}" for role in (
            Role.WORLD_SIM, Role.DIPLOMACY, Role.ADVISOR, Role.FREEFORM))
            + "; bots play the parser, minor powers, covert channel and council")
        return {key: api for key in ("claude", "codex", "antigravity")}

    if getattr(args, "no_ai", False):
        # Every seat a model would fill, played by rule; the mock is only the backstop for
        # anything a later change routes to a provider without a bot in front of it.
        bots.set_bots(roles=bots.SCOPES | bots.STAFF_SCOPES)
        print("no AI: rule-based bots answer for every role; no model calls will be made")
        shared = MockProvider()
        return {key: shared for key in ("claude", "codex", "antigravity")}

    scopes = getattr(args, "bots", None)
    if scopes:
        wanted = bots.SCOPES if scopes.strip() == "all" else {
            scope.strip() for scope in scopes.split(",") if scope.strip()}
        bots.set_bots(roles=frozenset(wanted))
        print(f"bots answer for: {', '.join(sorted(wanted))}")

    tape = getattr(args, "replay", None)
    if tape:
        from .providers.devreplay import DevReplayProvider

        shared = DevReplayProvider.from_file(tape, mode=args.replay_mode)
        roles = ", ".join(f"{role} x{count}" for role, count in shared.roles().items())
        print(f"replaying {Path(tape).name} ({args.replay_mode}): {roles}; "
              "no real model calls will be made")
    elif args.mock:
        shared = MockProvider()
        print("running with the mock provider; no real model calls will be made")
    else:
        return None
    return {key: shared for key in ("claude", "codex", "antigravity")}


def _guard_window() -> None:
    """Crash logging and the crash dialog, for every command that opens a window (A5)."""
    from .runtime.crash import install

    install()


def _cmd_new(args: argparse.Namespace) -> int:
    """Open the new-world screen, then the world it creates.

    Creation is synchronous and takes a few seconds: it derives every border from real
    polygon geometry, which is a few hundred thousand coordinate comparisons. The screen
    says so rather than pretending to be instant.
    """
    _guard_window()
    from .config.paths import missing_pack_advice
    from .engine.routing import Router
    from .map.pack import PackSet
    from .providers.cli_provider import default_role_models, make_provider
    from .providers.sandbox import sweep_orphans
    from .runtime.loop_thread import AsyncRuntime
    from .scenarios.registry import preset_politics, preset_seed
    from .scenarios.territory import pack_paths
    from .state.bootstrap import create_world
    from .state.db import connect, world_db_path
    from .ui.app import ensure_app
    from .ui.main_window import AtlasWindow
    from .ui.setup import SetupScreen
    from .ui.theme import DARK, load_fonts

    offline = _offline_providers(args)
    sweep_orphans()
    app = ensure_app()
    load_fonts()

    screen = SetupScreen(DARK)
    screen.setWindowTitle("ATLAS")
    screen.resize(1600, 950)

    runtime = AsyncRuntime()
    runtime.start()
    # Held here for the same reason every Qt window is: a top-level widget with no
    # Python reference is collected, and shiboken destroys the C++ side under it.
    opened: list = []

    def build(request) -> None:
        paths = pack_paths(request.preset.id, request.preset.map_source)
        missing = [path for path in paths if not path.exists()]
        if missing:
            screen.show_problem(missing_pack_advice(missing[0]))
            return

        db_path = world_db_path(request.world_id)
        if db_path.exists():
            screen.show_problem(
                f"a world called {request.world_id!r} already exists; choose another name"
            )
            return

        screen.set_busy(True)
        try:
            with PackSet.open(paths) as pack:
                seed = preset_seed(
                    request.preset, pack, world_id=request.world_id, player=request.player,
                    difficulty=request.difficulty, seed=request.seed,
                )
                conn = create_world(db_path, seed)
                preset_politics(conn, request.world_id, request.preset)
            conn.close()
        except Exception as exc:  # noqa: BLE001 -- shown on the screen, not as a traceback
            from .state.problems import describe

            screen.set_busy(False)
            screen.show_problem(describe(exc, what="The new world"))
            return

        providers = offline or {
            key: make_provider(key) for key in ("claude", "codex", "antigravity")
        }

        window = AtlasWindow(
            connect(db_path),
            PackSet.open(paths),
            request.world_id,
            providers=providers,
            router=Router(pins=default_role_models()),
            runtime=runtime,
            player_nation=request.player,
        )
        opened.append(window)
        window.show()
        screen.set_busy(False)
        screen.close()

    screen.create.connect(build)
    screen.cancelled.connect(screen.close)
    screen.show()
    try:
        return int(app.exec())
    finally:
        runtime.stop()


def _cmd_start(args: argparse.Namespace) -> int:
    """The game as a player meets it: the main menu, then setup or a save, then play."""
    _guard_window()
    from .providers.cli_provider import make_provider
    from .providers.sandbox import sweep_orphans
    from .runtime.loop_thread import AsyncRuntime
    from .ui.app import ensure_app
    from .ui.launcher import Launcher
    from .ui.preferences import load_app_preferences
    from .ui.theme import DARK, load_fonts
    from .ui.widgets import set_text_scale

    offline = _offline_providers(args)
    if getattr(args, "api", False):
        status = ("Claude API mode (metered): Claude writes the world, talks, the council and "
                  "odd orders; rule-based bots play the rest. Your key is set below.")
    elif getattr(args, "no_ai", False):
        status = "No AI: rule-based bots play every seat; no model is called."
    elif offline is not None:
        status = "Offline: no AI model is being called (mock or recorded answers)."
    else:
        status = "AI providers: the installed command-line models (see `atlas doctor`)."
    sweep_orphans()
    app = ensure_app()
    app.setQuitOnLastWindowClosed(False)
    load_fonts()
    set_text_scale(load_app_preferences().text_scale)
    runtime = AsyncRuntime()
    runtime.start()
    launcher = Launcher(
        app, DARK, runtime=runtime, provider_status=status,
        providers=lambda: offline or {
            key: make_provider(key) for key in ("claude", "codex", "antigravity")},
    )
    launcher.show_menu()
    try:
        return int(app.exec())
    finally:
        runtime.stop()


def _cmd_selftest(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    if args.crash:
        # Proves the crash handler works in *this* build: the build script runs it against
        # the frozen exe and checks a report appears in the logs folder.
        _guard_window()
        raise RuntimeError("selftest --crash: a deliberate crash, to check reporting")

    return run_selftest(preset_id=args.preset, player=args.nation, turns=args.turns,
                        report_path=Path(args.report) if args.report else None,
                        open_window=not args.no_window)


def _cmd_play(args: argparse.Namespace) -> int:
    """Open the full application: map, events, orders, diplomacy, time."""
    _guard_window()
    from .engine.routing import Router
    from .map.pack import PackSet
    from .providers.cli_provider import default_role_models, make_provider
    from .providers.sandbox import sweep_orphans
    from .runtime.loop_thread import AsyncRuntime
    from .state.db import connect, world_db_path
    from .ui.app import ensure_app
    from .ui.main_window import AtlasWindow

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(
            f"missing {db_path}; run `atlas map world --source {args.source} "
            f"--world-id {args.world_id}`",
            file=sys.stderr,
        )
        return 1

    # The world knows which scenario it was built from, and that is what decides whether
    # it needs the province pack open as well. Guessing from --source alone would open a
    # promoted world through country geometry and draw a dozen provinces as one blank.
    paths = _packs_for_world(db_path, args.world_id, args.source)
    for pack_path in paths:
        if not pack_path.exists():
            from .config.paths import missing_pack_advice

            print(f"missing {pack_path}: {missing_pack_advice(pack_path)}", file=sys.stderr)
            return 1

    sweep_orphans()

    providers = _offline_providers(args) or {
        key: make_provider(key) for key in ("claude", "codex", "antigravity")
    }

    app = ensure_app()
    runtime = AsyncRuntime()
    runtime.start()

    window = AtlasWindow(
        connect(db_path),
        PackSet.open(paths),
        args.world_id,
        providers=providers,
        router=Router(pins=default_role_models()),
        runtime=runtime,
        player_nation=args.nation,
    )
    window.show()
    try:
        return int(app.exec())
    finally:
        runtime.stop()


#: The three poles of 1972, and why these nine.
#:
#: A cold-war match wants sides that a reader recognises as sides. The West and the Eastern
#: bloc are the obvious two; the third is the interesting one, and it is not invented --
#: by 1972 the Sino-Soviet split was a decade old and France had been out of NATO's
#: integrated command since 1966, so a Paris-Peking pole is the year's own third answer
#: rather than a balancing fudge. It also makes the match competitive: roughly 348 / 270 /
#: 214 on combined military and economy, against 348 / 270 / 139 if the third bloc were
#: China and its neighbours alone.
#:
#: Falls back to strength-ranked assignment on any world that does not have these powers,
#: so `atlas eve --era` still works on the other presets.
_COLD_WAR_BLOCS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Western Bloc", ("usa", "wger", "gbr")),
    ("Eastern Bloc", ("sov", "eger", "pol")),
    ("Third Force", ("chn", "fra", "dvn")),
)


def _eve_blocs(conn, world_id: str, pins: dict):
    """Three blocs, one per model family, over the most significant powers available.

    ``None`` when three blocs cannot be formed -- fewer than three pins or fewer than three
    living nations -- which the caller reports rather than crashing on. The pins are indexed
    three times below, so a short pin table used to raise ``IndexError`` from the fallback
    instead of taking this exit.
    """
    from .engine.eve import Bloc

    if len(pins) < 3:
        return None
    alive = {row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1", (world_id,))}
    colours = ("#7FB3FF", "#F2C14E", "#8FD18F")
    labels = list(pins.items())

    historical = [members for _name, members in _COLD_WAR_BLOCS]
    if all(all(nation in alive for nation in members) for members in historical):
        rosters = historical
        names = [name for name, _members in _COLD_WAR_BLOCS]
    else:
        ranked = [row["id"] for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 "
            "ORDER BY (military_strength + economy) DESC, id LIMIT 9", (world_id,))]
        if len(ranked) < 3:
            return None
        rosters = [tuple(ranked[index::3]) for index in range(3)]
        names = [f"Bloc {label}" for label, _pin in labels]

    return [
        Bloc(id=f"bloc_{labels[index][0].lower()}", name=names[index],
             provider_key=labels[index][1][0], model=labels[index][1][1],
             colour=colours[index % 3], nations=tuple(rosters[index]))
        for index in range(3)
    ]


def _cmd_eve(args: argparse.Namespace) -> int:
    """Run an Engine-vs-Engine match headlessly and report behaviour.

    Exit codes are meant for a shell or a scheduled batch of matches:

    - ``0`` every round ran clean and every jump sealed;
    - ``1`` the match could not start (no pack, too few nations);
    - ``2`` the bloc configuration was refused;
    - ``3`` the match ran but is not a clean result: a round recorded a failure (a bloc's
      provider was unavailable, a call failed, a bloc returned nothing) or a jump ended
      ABORTED or PARTIAL.

    **Three is not zero.** A match in which every provider was missing used to print its
    statistics and exit 0, so a batch of them read as a batch of results. The statistics
    are still printed -- a partial match is worth reading -- but the code says it is not
    one to count.
    """
    import asyncio

    from .engine.eve import BlocConflict, EveEngine, assign_blocs
    from .engine.routing import Router
    from .engine.statistics import collect_match_stats
    from .engine.turn_engine import TurnEngine
    from .map.pack import PackSet
    from .providers.cli_provider import EVE_BLOC_MODELS, default_role_models, make_provider
    from .providers.mock import MockProvider
    from .scenarios.presets import load_preset
    from .scenarios.registry import preset_politics, preset_seed
    from .scenarios.territory import pack_paths
    from .state.bootstrap import create_world
    from .state.db import connect, world_db_path

    def build(db_path: Path) -> bool:
        # **A curated era, not a raw geographic world.** `earth_seed` gives every country
        # on the map with no armies, no politics and no history, so the blocs were being
        # handed Antarctica and the match had nothing to be about. A preset seeds the
        # powers, their forces and the standing quarrels of its year -- which is what makes
        # a cross-model contrast legible as strategy rather than as noise.
        preset = load_preset(args.era)
        paths = pack_paths(preset.id, preset.map_source)
        missing = [path for path in paths if not path.exists()]
        if missing:
            print(f"missing {missing[0]}; run `atlas map bake`", file=sys.stderr)
            return False
        with PackSet.open(paths) as pack:
            create_world(
                db_path, preset_seed(preset, pack, world_id=args.world_id, seed=args.seed)
            ).close()
        staging = connect(db_path)
        preset_politics(staging, args.world_id, preset)
        staging.commit()
        staging.close()
        # **No human seat, and nothing here has to arrange that.** `preset_seed` marks the
        # player by comparing each power against the seat it was given, so seeding without
        # one leaves every government unseated already. Clearing the flag by hand was both
        # redundant and wrong in kind -- `nation` is fingerprinted state, and
        # `test_rewind_holes` refuses fingerprinted writes outside the diff pipeline,
        # because a raw write is one a rewind cannot put back.
        return True

    if args.mock:
        shared = MockProvider()
        providers = {key: shared for key in ("claude", "codex", "antigravity")}
    else:
        providers = {key: make_provider(key) for key in ("claude", "codex", "antigravity")}

    if args.matches > 1:
        return _eve_series(args, build, providers)

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if args.fresh and db_path.exists():
        _discard_world(db_path, args.world_id)
    if not db_path.exists() and not build(db_path):
        return 1

    conn = connect(db_path)

    blocs = _eve_blocs(conn, args.world_id, EVE_BLOC_MODELS)
    if blocs is None:
        print("not enough nations in this world for three blocs", file=sys.stderr)
        return 1

    try:
        assign_blocs(conn, args.world_id, blocs)
    except BlocConflict as exc:
        print(f"EVE configuration refused: {exc}", file=sys.stderr)
        return 2

    print(f"EVE match in {args.world_id}:")
    for bloc in blocs:
        print(f"  {bloc.name:<14} {bloc.provider_key:<12} {bloc.model:<24} "
              f"{len(bloc.nations)} nation(s)  [{bloc.family}]")
    print()

    router = Router(pins=default_role_models())
    eve = EveEngine(conn=conn, world_id=args.world_id, providers=providers, router=router,
                    require_attestation=args.require_attestation, days=args.days)
    turn_engine = TurnEngine(conn=conn, world_id=args.world_id, providers=providers, router=router)

    from .state.models import TurnStatus

    failed_rounds: list[int] = []
    unsealed: list[str] = []

    async def run() -> None:
        for _round in range(args.rounds):
            report = await eve.run_round()
            print(report.describe())
            if report.failures:
                failed_rounds.append(report.turn)
            # **Who played what, one line each.** A round tally says three blocs acted; it
            # cannot say the Gemini bloc's China escalated while its France sat still, and
            # that contrast is the reason to run three families at all.
            for move in report.moves:
                print(f"    {move.describe()}")
            jump = await turn_engine.jump(days=args.days)
            print(f"  {jump.describe().splitlines()[0]}")
            if jump.status in (TurnStatus.ABORTED, TurnStatus.PARTIAL):
                unsealed.append(f"turn {jump.turn} {jump.status.value}")
            for line in _eve_headlines(conn, args.world_id, report.turn):
                print(f"    . {line}")
            print()

    asyncio.run(run())

    print()
    print(collect_match_stats(conn, args.world_id).describe())
    conn.close()
    if failed_rounds or unsealed:
        problems = []
        if failed_rounds:
            problems.append(f"{len(failed_rounds)} of {args.rounds} round(s) had failures")
        if unsealed:
            problems.append(", ".join(unsealed))
        print(f"\nEVE match incomplete: {'; '.join(problems)}", file=sys.stderr)
        return 3
    return 0


def _eve_series(args: argparse.Namespace, build, providers) -> int:
    """``atlas eve --matches N``: a rotated series, one statement per provider.

    Every match is a fresh world under ``<worlds>/<world-id>-series/mNN``, built with the
    same seed and the *same world id*, because in-play randomness is keyed off the id and
    the point is that two matches differ only where the models chose differently. See
    :mod:`atlas.engine.eve_series` for that and for why the seats rotate.
    """
    import asyncio

    from .config.paths import worlds_root
    from .engine.eve_series import run_series
    from .engine.routing import Router
    from .providers.cli_provider import EVE_BLOC_MODELS, default_role_models
    from .state.db import connect

    if args.path:
        print("--path names one world; a series builds its own under the worlds root",
              file=sys.stderr)
        return 1
    # Beside the single match's directory, not inside it: `--fresh` on a single match
    # removes that whole directory, and would take every series match with it.
    root = worlds_root() / f"{args.world_id}-series"

    def open_world(match: int):
        db_path = root / f"m{match + 1:02d}" / "world.db"
        # A series is always fresh: a match that continued an old world would not be
        # the same starting conditions as its siblings.
        if db_path.exists():
            shutil.rmtree(db_path.parent, ignore_errors=True)
        if not build(db_path):
            raise SystemExit(1)
        return connect(db_path)

    def on_round(match: int, report) -> None:
        print(f"  match {match + 1}  {report.describe().splitlines()[0]}")

    print(f"EVE series in {args.world_id}: {args.matches} matches of {args.rounds} round(s),"
          " seats rotating\n")
    result = asyncio.run(run_series(
        open_world, args.world_id, providers, EVE_BLOC_MODELS, _eve_blocs,
        matches=args.matches, rounds=args.rounds, days=args.days,
        router=Router(pins=default_role_models()), on_round=on_round,
        require_attestation=args.require_attestation))
    print()
    print(result.describe())
    return 0 if result.runs else 1


def _eve_headlines(conn, world_id: str, since_turn: int, limit: int = 6) -> list[str]:
    """What the round just did, in the engine's own sentences.

    **Nothing here is composed.** Every line is an `event.text` exactly as the engine wrote
    it, which is the rule `atlas chronicle` follows and for the same reason: a match report
    a reader cannot check against the world is a story about the world rather than a record
    of it. The full history is still `atlas chronicle`; this is the watchable slice.

    **The window starts at the blocs' own turn, and it has to.** A round asks the blocs
    while turn N is open, so `apply_batch` writes their events at N; the jump that follows
    plays N+1 and only then advances `world.turn_number`. Reading the current turn number
    therefore selected the jump's turn and nothing else, and every event the models
    authored -- the one thing a spectator came to see -- was silently dropped from the
    match report. `since_turn` is the round's turn, so the window is the blocs' moves plus
    the world's answer to them.

    **The models' news is drawn first.** A jump can write dozens of public events, so
    taking the last `limit` of the window by id alone would let a talkative world simulator
    crowd the blocs back out -- the same defect by a slower route. Half the lines are held
    for `origin = 'MODEL'`; whatever a round does not use goes back to the engine's. The
    lines are printed in the order they happened either way, and a bloc's own line comes
    before another power's: see the ranking below.

    **The window has a ceiling as well as a floor.** ``turn <= since_turn + 1`` is exactly
    the round and the jump that answers it. Without it, re-reading an older round -- a
    replay, a later reader, anything that is not the live loop -- returned the *latest*
    turn's news under an earlier round's heading, and looked right because during a match
    the later turns do not exist yet.
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT e.id, e.origin, e.text, n.bloc_id AS bloc
          FROM event e LEFT JOIN nation n ON n.id = e.actor_nation_id
         WHERE e.world_id = ? AND e.turn >= ? AND e.turn <= ?
           AND e.visibility = 'public'
         ORDER BY e.id
        """,
        (world_id, since_turn, since_turn + 1)).fetchall()

    def lines(predicate) -> list[tuple[int, str]]:
        return [(int(row["id"]), str(row["text"])) for row in rows if predicate(row)]

    blocs = lines(lambda row: row["origin"] == "MODEL" and row["bloc"])
    others = lines(lambda row: row["origin"] == "MODEL" and not row["bloc"])
    engine = lines(lambda row: row["origin"] != "MODEL")

    # Half the lines are the models', and within that half a bloc comes first. A jump
    # plays every *other* tracked power on the role pin, and those write `origin =
    # 'MODEL'` too -- so the reserved half filled up with powers no bloc was playing, and
    # a round of a three-model match showed one bloc line out of three. Whatever the blocs
    # do not use goes to the other model-written news, and then to the engine's.
    reserved = (limit + 1) // 2
    chosen = blocs[-reserved:]
    chosen = others[-(reserved - len(chosen)):] + chosen if len(chosen) < reserved else chosen
    spare = limit - len(chosen)
    if spare > 0:
        chosen = chosen + engine[-spare:]
    return [text for _id, text in sorted(chosen)]


def _cmd_rewind(args: argparse.Namespace) -> int:
    """Put a world back to an earlier turn, exactly."""
    from .state.bootstrap import fingerprint
    from .state.db import connect, world_db_path
    from .state.replay import rewind_to

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    before = fingerprint(conn, args.world_id)
    report = rewind_to(conn, args.world_id, args.to_turn)
    print(report.describe())
    if report.ok:
        print(f"  fingerprint {before} -> {fingerprint(conn, args.world_id)}")
    conn.close()
    return 0 if report.ok else 2


def _cmd_export(args: argparse.Namespace) -> int:
    """Write a quiescent single-file copy, safe to keep in a synced folder."""
    from .state.db import connect, export_world, world_db_path

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    destination = export_world(conn, Path(args.destination))
    conn.close()
    size = destination.stat().st_size / 1_000_000
    print(f"exported {args.world_id} to {destination} ({size:.1f} MB)")
    print("This is a single quiescent file with no sidecars, so it is safe to sync.")
    return 0


def _cmd_chronicle(args: argparse.Namespace) -> int:
    """Write one line's history out as something a person would read.

    **Not an export.** `export` writes the world; this writes what happened in it, in the
    engine's own sentences, turn by turn. Nothing is composed -- every line is an
    `event.text` exactly as recorded, so any of it can be checked against the event that
    produced it.
    """
    from .state.branch import lineage_of
    from .state.chronicle import chronicle, render_chronicle
    from .state.db import connect, world_db_path

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        came_from, at_turn = lineage_of(conn, args.world_id)
        entries = chronicle(conn, args.world_id, viewer=args.seat)
        written = render_chronicle(
            entries,
            title=args.title or db_path.parent.name,
            lineage=(f"forked from {came_from} at turn {at_turn}" if came_from
                     else "an original line")
            + (f", written from {args.seat}'s seat" if args.seat else ""),
        )
    finally:
        conn.close()

    destination = Path(args.destination)
    destination.write_text(written, encoding="utf-8")
    print(f"wrote {len(entries)} turn(s) to {destination}")
    if args.seat:
        print(f"Scoped to what {args.seat} was entitled to see.")
    return 0


def _cmd_why(args: argparse.Namespace) -> int:
    """Ask a number to account for itself.

    Nothing here is computed for the occasion: every term is re-derived by calling the same
    function the engine calls, and the terms sum to the number the engine uses.
    """
    from .engine.why import (
        why_a_bargaining_position,
        why_a_coup,
        why_supply_stops_here,
        why_the_sky_changed_a_battle,
        why_this_war_is_exhausting,
    )
    from .state.db import connect, world_db_path

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        answers = [why_a_coup(conn, args.world_id, args.nation)]
        if args.against:
            # The two that only mean anything about a pair. Asked together because a
            # position and the exhaustion under it are read together or not at all.
            answers.append(why_a_bargaining_position(
                conn, args.world_id, nation_id=args.nation, enemy_id=args.against))
            answers.append(why_this_war_is_exhausting(
                conn, args.world_id, nation_id=args.nation, enemy_id=args.against))
        if args.over:
            from .engine.reference import resolve as resolve_reference

            found = resolve_reference(conn, args.world_id, "region", args.over)
            if not found.ok:
                print(found.problem, file=sys.stderr)
                return 1
            answers.append(why_supply_stops_here(
                conn, args.world_id, nation_id=args.nation, region_id=str(found.value)))
            if args.against:
                answers.append(why_the_sky_changed_a_battle(
                    conn, args.world_id, region_id=str(found.value),
                    attacker=args.nation, defender=args.against))
        for index, answer in enumerate(answers):
            if index:
                print()
            for line in answer.rendered():
                print(line)
    finally:
        conn.close()
    return 0


def _cmd_against(args: argparse.Namespace) -> int:
    """What this nation could actually order against one target, right now.

    Nothing is offered that the engine would refuse: the catalogue's own availability test
    and the undertakings' own requirement gate decide the list.
    """
    from .engine.against import what_can_i_do_about
    from .state.db import connect, world_db_path

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    try:
        found = what_can_i_do_about(conn, args.world_id, nation_id=args.nation,
                                    target=args.target)
    finally:
        conn.close()

    if not found:
        print(f"nothing {args.nation} can do about {args.target}")
        return 0
    print(f"{args.nation}, about {args.target}:")
    for doable in found:
        mark = "~" if doable.is_gamble else " "
        line = f"  {mark} {doable.action_type:<22} {doable.explain}"
        if doable.cost:
            line += f"  [{doable.cost}]"
        print(line)
        if doable.still_needs:
            print(f"      still needs: {', '.join(doable.still_needs)}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Hold two lines you have played against each other on the same day.

    A curated headline rather than a diff of two databases: nine questions a person asks
    about a campaign. Read-only -- each line is opened read-only, and where one has to be
    read as it stood earlier it is copied to a temporary file and the copy is rewound.
    """
    from .state.branch import branches
    from .state.compare import compare, render_comparison

    found = {branch.name: branch for branch in branches()}
    missing = [name for name in (args.left, args.right) if name not in found]
    if missing:
        have = ", ".join(sorted(found)) or "none"
        print(f"no line called {missing[0]!r}; have {have}", file=sys.stderr)
        return 1

    result = compare(found[args.left], found[args.right],
                     viewer=args.seat or "", on=args.on or "")
    for line in render_comparison(result):
        print(line)
    return 1 if result.refused else 0


def _cmd_presets(args: argparse.Namespace) -> int:
    from .scenarios.presets import list_presets, load_preset

    if args.show:
        preset = load_preset(args.show)
        if preset is None:
            print(f"no preset {args.show!r}", file=sys.stderr)
            return 1
        print(f"{preset.name}  ({preset.id})")
        print(f"  starts {preset.start_date} on map {preset.map_source}")
        print(f"  {preset.description}\n")
        print(preset.lore_block() or "(no lore)")
        print()
        print(preset.rules_block() or "(no hard constraints)")
        return 0

    for preset in list_presets():
        mark = "built-in" if preset.builtin else "custom  "
        # Listed rather than hidden, so a preset that will not start says why.
        unplayable = "" if has_map(preset) else f"  (no map: {preset.map_source!r} cannot be baked)"
        print(f"  [{mark}] {preset.id:<18} {preset.name:<26} {preset.start_date}  "
              f"{len(preset.simulation_rules)} rule(s){unplayable}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    from .engine.statistics import collect_match_stats
    from .state.db import connect, world_db_path

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1
    conn = connect(db_path)
    print(collect_match_stats(conn, args.world_id).describe())
    conn.close()
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Run the invariant checker over a real save.

    **The instrument existed and could only be pointed at a laboratory run.** Thirteen
    things that must never be true of a world, and nothing outside
    `scratchpad/lab_run.py` ever asked them -- so a game Alex actually played was the one
    world they were never checked against.

    Read-only by construction: every invariant is a query and the checker never writes,
    which is what makes it safe to point at a live save rather than a copy.

    Exit codes are meant for a shell: 0 sound, 2 something to look at, 1 no such world.
    """
    from .state.db import connect, world_db_path
    from .testing.invariants import INVARIANTS, Phase, violations

    db_path = Path(args.path) if args.path else world_db_path(args.world_id)
    if not db_path.exists():
        print(f"no world at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    row = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (args.world_id,)
    ).fetchone()
    if row is None:
        print(f"no world {args.world_id!r} in {db_path}", file=sys.stderr)
        conn.close()
        return 1

    # A sealed turn is the phase where everything the engine promises is true, and a save
    # on disk is always between turns. `--phase seed` is for a world that has taken none.
    phase = Phase(args.phase)
    found = violations(conn, args.world_id, phase)
    checked = [i.name for i in INVARIANTS if phase in i.holds_in]
    print(f"{args.world_id} at turn {row['turn_number']}: "
          f"{len(checked)} invariant(s) checked at {phase.value}")
    if not found:
        print("  nothing to report")
        conn.close()
        return 0
    for line in found:
        print(f"  {line}")
    if args.explain:
        print()
        for invariant in INVARIANTS:
            if any(line.startswith(f"[{invariant.name}]") for line in found):
                print(f"  [{invariant.name}] {invariant.because}")
    conn.close()
    return 2


def _cmd_audit(args: argparse.Namespace) -> int:
    """Build an era from its preset and check it against the record of its own year.

    `atlas check` points the invariant sweep at a save and asks *is this world broken*.
    This asks the different question: *is this world the year it says it is* -- fifteen
    parties to the North Atlantic Treaty, five permanent seats, Colombo and not Andreotti.
    No invariant can know any of that, because it is a fact about 1972 rather than about
    the engine.

    Builds a throwaway in-memory world rather than reading a save, deliberately: what is
    being audited is the *scenario*, and a save has had turns taken against it.

    Exit codes for a shell: 0 sound, 2 findings, 1 no such era or no manifest.
    """
    from .map.pack import PackSet
    from .scenarios.presets import load_preset
    from .scenarios.registry import SCENARIOS, preset_politics, preset_seed
    from .scenarios.territory import pack_paths
    from .state.bootstrap import create_world
    from .testing.audit import CHECKS, audit, coverage, load_manifest, manifest_ids

    if args.era in (None, "all"):
        eras = sorted(set(SCENARIOS) & set(manifest_ids()))
    elif args.era in SCENARIOS:
        eras = [args.era]
    else:
        print(f"no scenario {args.era!r}; try one of {', '.join(sorted(SCENARIOS))}",
              file=sys.stderr)
        return 1

    worst = 0
    for era in eras:
        manifest = load_manifest(era)
        if manifest is None:
            print(f"{era}: no manifest, so nothing audits it", file=sys.stderr)
            worst = max(worst, 1)
            continue

        preset = load_preset(era)
        paths = pack_paths(preset.id, preset.map_source)
        missing = [p for p in paths if not p.exists()]
        if missing:
            # Not a silent skip: a missing pack is itself one of the things this reports.
            print(f"{era}: geometry not baked ({', '.join(p.name for p in missing)}); "
                  "run `atlas map bake`", file=sys.stderr)
            worst = max(worst, 2)
            continue

        with PackSet.open(paths) as packs:
            seed = preset_seed(preset, packs, world_id="audit", player=None)
        conn = create_world(":memory:", seed)
        preset_politics(conn, "audit", preset)
        found = audit(conn, "audit", manifest)
        conn.close()

        counts = coverage(manifest)
        asserted = sum(n for n in counts.values() if n > 0)
        silent = sorted(name for name, n in counts.items() if n < 0)
        print(f"{era} ({manifest.start_date}): {asserted} fact(s) checked across "
              f"{len(CHECKS)} check(s)")
        if silent:
            # Printed every time, not only on failure. A green audit against a manifest
            # that declares half the era out of scope is not the same as a green audit.
            print(f"  silent on: {', '.join(silent)}")
        if not found:
            print("  nothing to report")
        for line in found:
            print(f"  {line}")
            worst = 2
        if args.explain:
            print()
            for check in CHECKS:
                print(f"  [{check.name}] {check.because}")
        if args.why and silent:
            print()
            for name in silent:
                print(f"  silent on {name}: {manifest.silent_on[name]}")
        print()
    return worst


def _cmd_release_audit(args: argparse.Namespace) -> int:
    """Every release gate, one verdict.

    The pieces all existed and were all exercised separately -- as a test, as another
    subcommand, as a scratchpad script. What did not exist was a single answer to *is this
    tree fit to ship*, so a release was green if somebody remembered to run six things and
    read six outputs.

    Exit codes for a shell: 0 every gate green, 2 something is red. There is no third code:
    a gate that could not run is a gate that failed, because "we did not check" and "we
    checked and it was fine" must never be the same exit status.
    """
    from .testing.release import release_audit

    report = release_audit(skip_suite=args.skip_suite)
    for gate in report.gates:
        if gate.problems:
            print(f"[{gate.name}] {gate.because}")
            for line in gate.problems[:6]:
                print(f"    {line}")
            if len(gate.problems) > 6:
                print(f"    ...and {len(gate.problems) - 6} more")
            print()
    print(report.rendered())
    return 0 if report.ok else 2


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report what is installed and how well contained it is. Spends no model calls."""
    from .providers.diagnostics import collect
    from .providers.sandbox import sweep_orphans

    swept = sweep_orphans()
    if swept:
        print(f"cleaned up {swept} stale sandbox(es) from a previous run\n")

    diagnostics = collect()
    for diagnostic in diagnostics:
        print(diagnostic.describe())
        print()

    if args.models:
        for diagnostic in diagnostics:
            if diagnostic.models:
                print(f"{diagnostic.key} accepts these --model strings:")
                for name in diagnostic.models:
                    print(f"  {name}")
                print()

    from .providers.cli_provider import (
        EVE_BLOC_MODELS,
        default_role_models,
        eve_family_conflict,
        validate_pins,
    )

    pins = default_role_models()
    available = {d.key: d.models for d in diagnostics}
    print("role pins:")
    for role, (provider_key, model) in pins.items():
        print(f"  {role.value:<15} {provider_key:<12} {model}")

    problems = validate_pins(pins, available)
    conflict = eve_family_conflict(EVE_BLOC_MODELS)
    if conflict:
        problems.append(f"EVE: {conflict}")

    if problems:
        print("\npin problems:")
        for problem in problems:
            print(f"  ! {problem}")
    else:
        print("\nall pins validate against the installed CLIs")

    missing = [d.key for d in diagnostics if not d.installed]
    if missing:
        print(f"\nunavailable: {', '.join(missing)}")
        print("Roles assigned to a missing provider are disabled; ATLAS never substitutes another.")
    return 2 if problems else 0


def _cmd_probe(args: argparse.Namespace) -> int:
    """Spend one small real call per provider to prove the path works end to end."""
    import asyncio

    if args.provider == "api":
        return asyncio.run(_probe_api(args.timeout))

    from .providers.diagnostics import probe_live
    from .providers.resolver import resolve_all

    keys = list(resolve_all()) if args.provider == "all" else [args.provider]

    async def run() -> int:
        failures = 0
        for key in keys:
            print(f"--- {key} ---")
            result = await probe_live(key, model=args.model, timeout_s=args.timeout)
            if result is None:
                print("  not installed; skipped\n")
                failures += 1
                continue

            if result.ok:
                audit = result.audit
                verdict = audit.verdict.value if audit else "?"
                served = (audit.served if audit else None) or "unattested"
                print(f"  OK        {result.duration_s:.1f}s")
                print(f"  reply     {result.text.strip()[:120]!r}")
                print(f"  model     requested={audit.requested if audit else '?'} served={served} [{verdict}]")
                if audit and audit.side_models:
                    print(f"  also used {', '.join(audit.side_models)}")
                if audit and audit.note:
                    print(f"  note      {audit.note}")
            else:
                failures += 1
                print(f"  FAILED    {result.failure.kind.value}")
                print(f"  {result.failure.message}")
                if result.failure.remedy:
                    print(f"  -> {result.failure.remedy}")
                if args.verbose and result.raw_stderr:
                    print(f"  stderr    {result.raw_stderr[:600]}")
            print()
        return 1 if failures else 0

    return asyncio.run(run())


async def _probe_api(timeout_s: float) -> int:
    """The ``--api`` seats, each asked one tiny question with the schema it really uses.

    Schemas first-compile on the API side and some may be refused as too complex; this is
    the cheap place to find that out, rather than mid-turn.
    """
    from .engine.diplomacy import COMMITMENT_SCHEMA
    from .engine.freeform import FREEFORM_SCHEMA
    from .providers.api_provider import API_KEY_ENV, ApiProvider, api_role_models
    from .providers.base import ProviderRequest
    from .state.diff import diff_batch_schema

    provider = ApiProvider()
    if not provider.available():
        print(f"{API_KEY_ENV} is not set; nothing to probe")
        return 1
    pins = api_role_models()
    checks = [
        (Role.ADVISOR, "Reply with the single word: ready.", None),
        (Role.WORLD_SIM, "Nothing happens this turn. Return no diffs, no events, no contests "
         "and no declarations, and a one-sentence narrative.",
         diff_batch_schema(role=Role.WORLD_SIM)),
        (Role.FREEFORM, "The order was: hold a minute's silence. Narrate it in one sentence, "
         "with no nudges.", FREEFORM_SCHEMA),
        (Role.PARSER, "Two powers exchanged greetings and agreed nothing. Record the "
         "commitments.", COMMITMENT_SCHEMA),
    ]
    failures = 0
    for role, prompt, schema in checks:
        result = await provider.invoke(ProviderRequest(
            role=role, prompt=prompt, model=pins[role][1], schema=schema,
            timeout_s=timeout_s))
        shape = "structured" if schema else "text"
        if result.ok:
            served = result.audit.served if result.audit else "?"
            print(f"  OK      {role.value:<10} {shape:<10} {served}  {result.duration_s:.1f}s")
        else:
            failures += 1
            print(f"  FAILED  {role.value:<10} {shape:<10} {result.failure.kind.value}: "
                  f"{result.failure.message}")
            if result.failure.detail:
                print(f"          {result.failure.detail[:300]}")
    return 1 if failures else 0


def _cmd_map(args: argparse.Namespace) -> int:
    from .config.paths import packs_root
    from .map.sources import SOURCES

    if args.map_command == "sources":
        for source in SOURCES.values():
            print(f"{source.id:<14} ~{source.approx_regions:>5} regions  {source.description}")
        return 0

    if args.map_command == "pin":
        # **Recording what a green run was green against.** Run after baking, and commit the
        # result: `packs.lock.json` is what makes "eight of eight" mean the same thing on
        # two machines, and until it existed nothing did.
        from .map.manifest import record_current, write
        from .testing.release import _scenario_packs

        paths = sorted({path for group in _scenario_packs().values() for path in group})
        records = record_current(paths)
        if not records:
            print("no packs on this machine to pin; run `atlas map bake` first",
                  file=sys.stderr)
            return 1
        where = write(records)
        for record in records:
            print(f"{record.name:<18} baked_by={record.baked_by} "
                  f"sha256={record.sha256[:12]} {record.bytes:,} bytes")
        print(f"\nwritten to {where}")
        return 0

    if args.map_command == "check":
        # The same comparison the release audit makes, on its own, so somebody can ask the
        # question without running twelve minutes of tests.
        from .map.manifest import ManifestError, compare, load, repair_plan
        from .testing.release import _scenario_packs

        paths = sorted({path for group in _scenario_packs().values() for path in group})
        try:
            pinned = load()
        except ManifestError:
            pinned = None  # `compare` reports it, with the fix, like any other problem
        if pinned == {}:
            print("no packs.lock.json in this checkout; nothing is pinned")
            return 0
        problems = compare(paths)
        if not problems:
            print(f"{len(paths)} pack(s) match the manifest this release was verified "
                  "against")
            return 0
        for problem in problems:
            print(f"{problem.name}: {problem.kind} -- {problem.detail}", file=sys.stderr)
        print("\nto put this machine right:", file=sys.stderr)
        for step in repair_plan(problems):
            print(f"  {step}", file=sys.stderr)
        return 2

    if args.map_command == "bake":
        # Imported here so the bake extras (shapely, topojson, requests) are needed only
        # when actually baking. The runtime reads packs with the standard library alone.
        try:
            from .map.bake import bake
        except ImportError as exc:
            print(
                f"baking needs the geometry extras: pip install -e \".[bake]\"\n  ({exc})",
                file=sys.stderr,
            )
            return 1
        report = bake(args.source, refresh=args.refresh)
        print(report.describe())

        # **A bake that fails its own verifier used to report success.** `atlas map verify`
        # existed, returned 2 on a bad pack, and nothing ran it -- so a freshly baked
        # `marine-10m` printed its statistics, exited 0, and shipped with two LOD borders
        # that had come apart. Verifying here makes the bake the thing that catches it,
        # which is the only moment anybody is looking.
        from .map.pack import GeometryPack, PackCorruptError
        from .map.verify import verify_pack

        try:
            with GeometryPack(report.pack_path) as baked:
                verified = verify_pack(baked)
        except PackCorruptError as exc:
            print(f"the pack this bake wrote is corrupt: {exc}", file=sys.stderr)
            return 2
        print()
        print(verified.describe())
        if not verified.ok:
            print(
                f"bake FAILED verification: {len(verified.problems)} problem(s) above. "
                "The pack was written and should not be trusted.",
                file=sys.stderr,
            )
            return 2
        return 0

    if args.map_command == "world":
        from .map.pack import PackSet
        from .scenarios.presets import load_preset
        from .scenarios.registry import preset_politics, preset_seed
        from .scenarios.territory import pack_paths
        from .state.bootstrap import create_world
        from .state.db import world_db_path

        paths = pack_paths(args.preset, args.source)
        for pack_path in paths:
            if not pack_path.exists():
                print(
                    f"no pack at {pack_path}; run `atlas map bake --source {pack_path.stem}`",
                    file=sys.stderr,
                )
                return 1

        db_path = Path(args.path) if args.path else world_db_path(args.world_id)
        if db_path.exists():
            if not args.force:
                print(f"{db_path} already exists; pass --force to replace it", file=sys.stderr)
                return 1
            _discard_world(db_path, args.world_id)

        # `--preset earth` is the bare pack with no scenario behind it, and loads as
        # None. Anything else is a real preset, whose id, name and date have to reach
        # the world row or the referee never sees that era's rules.
        preset = load_preset(args.preset)
        with PackSet.open(paths) as pack:
            seed = preset_seed(preset, pack, world_id=args.world_id, player=args.player)
            conn = create_world(db_path, seed)
            politics = preset_politics(conn, args.world_id, preset)

        print(f"created world {seed.id!r} ({seed.name}) from "
              f"{', '.join(path.name for path in paths)}")
        print(f"  {len(seed.nations)} nations, {len(seed.regions)} regions, "
              f"{len(seed.adjacency)} borders derived from geometry")
        if politics:
            print(f"  {politics['relations']} historical relations, "
                  f"{politics['treaties']} treaties in force, "
                  f"{politics['events']} events of prior history")
        print(f"  {db_path}")
        conn.close()
        return 0

    if args.map_command == "show":
        from .map.pack import PackSet
        from .state.db import connect, world_db_path
        from .ui.app import ensure_app
        from .ui.map_window import MapWindow

        db_path = Path(args.path) if args.path else world_db_path(args.world_id)
        if not db_path.exists():
            print(
                f"missing {db_path}; run `atlas map world --source {args.source}`",
                file=sys.stderr,
            )
            return 1
        paths = _packs_for_world(db_path, args.world_id, args.source)
        for pack_path in paths:
            if not pack_path.exists():
                print(
                    f"missing {pack_path}; run `atlas map bake --source {pack_path.stem}`",
                    file=sys.stderr,
                )
                return 1

        app = ensure_app()
        pack = PackSet.open(paths)
        conn = connect(db_path)
        window = MapWindow(conn, pack, args.world_id, observer_nation_id=args.observer)
        window.show()
        return int(app.exec())

    if args.map_command == "verify":
        from .map.pack import GeometryPack, PackCorruptError
        from .map.verify import verify_pack

        path = Path(args.path) if args.path else packs_root() / f"{args.source}.pack"
        if not path.exists():
            print(f"no pack at {path}; run `atlas map bake --source {args.source}`", file=sys.stderr)
            return 1
        try:
            with GeometryPack(path) as pack:
                report = verify_pack(pack)
        except PackCorruptError as exc:
            print(f"pack is corrupt: {exc}", file=sys.stderr)
            return 2
        print(report.describe())
        return 0 if report.ok else 2

    return 1


def build_parser() -> argparse.ArgumentParser:
    """Every command this CLI accepts.

    Split out of :func:`main` so the surface can be *asserted against* rather than
    remembered. The laboratory writes a REPLAY.md telling a reader how to reproduce a bug
    bundle, and both of its commands were wrong -- one had invented flags and the other named
    a subcommand that has never existed. A recipe that does not run costs a reader the time
    to find that out, so a test now parses what the bundle tells them to type.
    """
    parser = argparse.ArgumentParser(prog="atlas", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("newgame", help="create a world and exercise the diff pipeline")
    new.add_argument("--world-id", default="sandbox")
    new.add_argument("--seed", type=int, default=42)
    new.add_argument(
        "--difficulty",
        default=Difficulty.NORMAL.value,
        choices=[d.value for d in Difficulty],
    )
    new.add_argument("--path", help="explicit database path (default: %%LOCALAPPDATA%%\\Atlas)")
    new.add_argument("--force", action="store_true", help="replace an existing world")
    new.set_defaults(func=_cmd_newgame)

    rules = sub.add_parser("rules", help="list the validation rules")
    rules.set_defaults(func=_cmd_rules)

    new_world = sub.add_parser("new", help="pick a scenario and a power, then play")
    _add_offline_flags(new_world)
    new_world.set_defaults(func=_cmd_new)

    start = sub.add_parser("start", help="open the game (what double-clicking ATLAS does)")
    _add_offline_flags(start)
    start.set_defaults(func=_cmd_start)

    selftest = sub.add_parser(
        "selftest", help="check this installation works, with no model calls")
    selftest.add_argument("--preset", default="modern-day")
    selftest.add_argument("--nation", default="rus")
    selftest.add_argument("--turns", type=int, default=3)
    selftest.add_argument("--report", help="write the JSON report here")
    selftest.add_argument("--no-window", action="store_true", help="skip opening the window")
    selftest.add_argument("--crash", action="store_true",
                          help="crash on purpose, to check a crash report is written")
    selftest.set_defaults(func=_cmd_selftest)

    play = sub.add_parser("play", help="open the full game window")
    play.add_argument("--source", default="admin0-50m")
    play.add_argument("--world-id", default="earth")
    play.add_argument("--nation", help="nation id to play (defaults to the world's player)")
    play.add_argument("--path", help="explicit database path")
    _add_offline_flags(play)
    play.set_defaults(func=_cmd_play)

    eve = sub.add_parser(
        "eve", help="run an Engine-vs-Engine match and report behaviour",
        epilog="exit codes: 0 clean match; 1 could not start; 2 bloc configuration "
               "refused; 3 a round had failures or a jump was ABORTED or PARTIAL",
    )
    eve.add_argument("--source", default="admin0-50m",
                     help="ignored when --era names a preset, which brings its own map")
    eve.add_argument("--era", default="cold-war",
                     help="which curated era to play; the powers and politics come with it")
    eve.add_argument("--world-id", default="eve-match")
    eve.add_argument("--rounds", type=int, default=3)
    eve.add_argument("--days", type=int, default=90, help="time advanced between rounds")
    eve.add_argument("--seed", type=int, default=1936)
    eve.add_argument("--fresh", action="store_true", help="discard any existing match world")
    eve.add_argument("--mock", action="store_true", help="use the mock provider, no real calls")
    eve.add_argument(
        "--require-attestation",
        action="store_true",
        help="refuse a bloc's moves when its provider cannot prove which model answered",
    )
    eve.add_argument("--path", help="explicit database path")
    eve.add_argument(
        "--matches", type=int, default=1,
        help="play a series of fresh matches with the providers rotated through the seats,"
             " and report one line per provider across all of them",
    )
    eve.set_defaults(func=_cmd_eve)

    rewind = sub.add_parser("rewind", help="put a world back to an earlier turn")
    rewind.add_argument("to_turn", type=int)
    rewind.add_argument("--world-id", default="earth")
    rewind.add_argument("--path", help="explicit database path")
    rewind.set_defaults(func=_cmd_rewind)

    export = sub.add_parser("export", help="write a single-file copy, safe to sync")
    export.add_argument("destination")
    export.add_argument("--world-id", default="earth")
    export.add_argument("--path", help="explicit source database path")
    export.set_defaults(func=_cmd_export)

    chron = sub.add_parser(
        "chronicle", help="write a branch's history out as a readable campaign log")
    chron.add_argument("destination")
    chron.add_argument("--world-id", default="earth")
    chron.add_argument("--path", help="explicit source database path")
    chron.add_argument("--seat", help="write it from one nation's point of view")
    chron.add_argument("--title", help="what to call it")
    chron.set_defaults(func=_cmd_chronicle)

    why = sub.add_parser("why", help="what is driving a number, and what would change it")
    why.add_argument("nation", help="whose government to explain")
    why.add_argument("--against", help="also explain the war with this power: the "
                                       "bargaining position and what it is costing")
    # **The air layer's two entries, because it was the largest thing in the engine with
    # none.** A sky is held over a *place*, so both take a province; the battle one also has
    # to know which way round the fight was, and takes that from `--against`.
    why.add_argument("--over", help="also explain the sky over this province: what supply "
                                    "costs under it, and (with --against) what it was "
                                    "worth to a battle there")
    why.add_argument("--world-id", default="earth")
    why.add_argument("--path", help="explicit source database path")
    why.set_defaults(func=_cmd_why)

    against = sub.add_parser(
        "against", help="what you could actually order against one province or power")
    against.add_argument("nation", help="who is asking")
    against.add_argument("target", help="the province or power asked about")
    against.add_argument("--world-id", default="earth")
    against.add_argument("--path", help="explicit source database path")
    against.set_defaults(func=_cmd_against)

    compare_cmd = sub.add_parser(
        "compare", help="hold two lines you have played against each other")
    compare_cmd.add_argument("left", help="one branch, by name")
    compare_cmd.add_argument("right", help="the other")
    compare_cmd.add_argument("--seat", help="whose campaign to read it as")
    compare_cmd.add_argument("--on", help="the world date to compare at; "
                                          "default is the latest day both have reached")
    compare_cmd.set_defaults(func=_cmd_compare)

    presets = sub.add_parser("presets", help="list or inspect scenario presets")
    presets.add_argument("--show", help="print one preset's lore and rules")
    presets.set_defaults(func=_cmd_presets)

    stats = sub.add_parser("stats", help="behavioural statistics for a world")
    stats.add_argument("--world-id", default="eve-match")
    stats.add_argument("--path", help="explicit database path")
    stats.set_defaults(func=_cmd_stats)

    check = sub.add_parser("check", help="run the invariant checker over a saved world")
    check.add_argument("--world-id", default="atlas")
    check.add_argument("--path", help="explicit database path")
    check.add_argument("--phase", default="rest", choices=["seed", "mid_turn", "rest"],
                       help="which promises to hold it to; a save on disk is at rest")
    check.add_argument("--explain", action="store_true",
                       help="print why each reported invariant matters")
    check.set_defaults(func=_cmd_check)

    audit_cmd = sub.add_parser("audit", help="check a scenario against the record of its own era")
    audit_cmd.add_argument("era", nargs="?", default="all",
                           help="which era, or 'all' for every one that has a manifest")
    audit_cmd.add_argument("--explain", action="store_true",
                           help="print what each check is for")
    audit_cmd.add_argument("--why", action="store_true",
                           help="print the reason behind each declared silence")
    audit_cmd.set_defaults(func=_cmd_audit)

    release = sub.add_parser(
        "release-audit", help="every release gate, one verdict: is this tree fit to ship")
    release.add_argument(
        "--skip-suite", action="store_true",
        help="skip the pytest and ruff gate for a fast structural pass")
    release.set_defaults(func=_cmd_release_audit)

    doctor = sub.add_parser("doctor", help="report installed CLIs and their containment")
    doctor.add_argument("--models", action="store_true", help="list every accepted model string")
    doctor.set_defaults(func=_cmd_doctor)

    probe = sub.add_parser("probe", help="spend one small real call per provider")
    probe.add_argument("--provider", default="all",
                       choices=["all", "claude", "codex", "antigravity", "api"])
    probe.add_argument("--model", help="override the pinned model")
    probe.add_argument("--timeout", type=float, default=180.0)
    probe.add_argument("--verbose", action="store_true")
    probe.set_defaults(func=_cmd_probe)

    game_map = sub.add_parser("map", help="bake and verify map geometry")
    map_sub = game_map.add_subparsers(dest="map_command", required=True)

    map_sub.add_parser("sources", help="list available Natural Earth sources")
    map_sub.add_parser(
        "pin", help="record the packs this release is verified against, into packs.lock.json")
    map_sub.add_parser(
        "check", help="compare the packs on this machine against packs.lock.json")

    bake_cmd = map_sub.add_parser("bake", help="download and bake a geometry pack (offline, one-time)")
    bake_cmd.add_argument("--source", default="admin0-50m", choices=sorted(SOURCES))
    bake_cmd.add_argument("--refresh", action="store_true", help="re-download even if cached")

    verify_cmd = map_sub.add_parser("verify", help="check a pack's integrity and topology")
    verify_cmd.add_argument("--source", default="admin0-50m")
    verify_cmd.add_argument("--path", help="explicit pack path")

    world_cmd = map_sub.add_parser("world", help="seed a world from a baked pack")
    world_cmd.add_argument("--source", default="admin0-50m")
    world_cmd.add_argument("--world-id", default="earth")
    world_cmd.add_argument(
        "--preset",
        default="earth",
        # Every built-in scenario with a bakeable map, plus 'earth' -- the bare pack with
        # no scenario behind it. Listed rather than hardcoded so a preset added to the
        # catalogue is reachable from the command line the day it exists.
        choices=["earth", *sorted(p.id for p in builtin_presets() if has_map(p))],
        help="a designed era seeds its real political position ('gathering-storm' 1936, "
             "'cold-war' 1972, 'modern-day' 2026); 'earth' is the raw pack with no politics",
    )
    world_cmd.add_argument(
        "--player",
        help="who to play. For a designed era a power id (ger, fra, gbr, usa, sov...); "
             "otherwise a geo_key such as admin0-50m:FRA",
    )
    world_cmd.add_argument("--path", help="explicit database path")
    world_cmd.add_argument("--force", action="store_true")

    show_cmd = map_sub.add_parser("show", help="open the map window")
    show_cmd.add_argument("--source", default="admin0-50m")
    show_cmd.add_argument("--world-id", default="earth")
    show_cmd.add_argument("--observer", help="nation id to view as (omit for omniscient)")
    show_cmd.add_argument("--path", help="explicit database path")

    game_map.set_defaults(func=_cmd_map)

    return parser


def _use_utf8_output() -> None:
    """Print what the world actually says, on a console that defaults to cp1252.

    **A live EVE match died here.** Windows gives a redirected stdout the ANSI code page,
    and a model wrote a Vietnamese name into an event it authored; `print` raised
    ``UnicodeEncodeError: 'charmap' codec can't encode character 'ệ'`` out of the
    match loop after two rounds had already been played and paid for. The world was
    safe -- every round is committed as it happens -- but the run was over, and the cause
    was the terminal's encoding rather than anything in the game.

    A game about the whole world writes Vietnamese, Russian and Chinese names in the
    ordinary course of play, so this is not an edge case. Every harness in `scratchpad/`
    has done this for months; the shipped command never did.

    ``errors="replace"`` rather than ``strict``: a character the console genuinely cannot
    draw should cost a glyph, not the run. Guarded, because a captured or wrapped stream
    need not support reconfiguring at all.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


#: Exit status when the world file itself is the problem: newer, synced, unmigratable, damaged.
WORLD_FILE_PROBLEM = 4


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)
    from .state.problems import WORLD_ERRORS, describe

    try:
        return int(args.func(args))
    except WORLD_ERRORS as exc:
        # A save that is newer, synced, unmigratable or damaged is the file's problem, not
        # a bug: one sentence and exit status 4, never a traceback (A6). 4 because 1-3
        # already mean things (no such world; `check` findings; an EVE round failed).
        # Anything else still propagates -- to the crash handler, in a window command.
        print(f"atlas: {describe(exc, what='That world')}", file=sys.stderr)
        return WORLD_FILE_PROBLEM


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
