"""One command that decides whether this tree is fit to ship.

Every gate here already existed and was already exercised -- as a test, as a CLI subcommand,
as a scratchpad script. What did not exist was **a single verdict**. A release was green if
somebody remembered to run six things and read six outputs, which is a process that works
right up until the day somebody is in a hurry.

**Nothing is reimplemented.** Each gate calls the machinery that already owns the question:
:func:`atlas.map.verify.verify_pack`, :func:`atlas.state.parity.schema_parity_problems`,
:func:`atlas.testing.invariants.violations`, :func:`atlas.testing.audit.audit`,
:func:`atlas.state.bootstrap.fingerprint`, and pytest and ruff as subprocesses. Where a piece
was only reachable as a test it was extracted rather than copied -- see
:mod:`atlas.state.parity`, which the migration test now imports rather than defines.

**Ordered cheap-to-expensive, and it does not stop at the first failure.** A reviewer wants
the whole picture, not the first thing that broke: a missing pack and a failing invariant are
different problems and finding one should not hide the other. The suite gate is the slow one
and can be skipped for a fast structural pass.

**Side-effect free.** Every world it builds is in memory or under a temporary directory;
nothing it does touches a tracked fixture, a baked pack, or a save.

**Historical tapes do not gate.** A recording made before a change that legitimately altered
a prompt *should* miss, and a release audit that reddened on that would teach everybody to
ignore it. Only tapes marked ``active`` in ``scratchpad/cassettes/INDEX.json`` are checked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Gate", "GATES", "ReleaseReport", "release_audit"]


@dataclass(slots=True)
class Gate:
    """One question, its verdict, and the number a reader will want."""

    name: str
    #: What it is for, in one line, printed with a failure.
    because: str
    ok: bool = False
    #: The headline figure -- "3 eras", "2545 passed". Shown whether it passed or failed.
    number: str = ""
    #: Why it failed. Empty on success.
    problems: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass(slots=True)
class ReleaseReport:
    gates: list[Gate] = field(default_factory=list)

    @property
    def failed(self) -> list[Gate]:
        return [g for g in self.gates if not g.ok and not g.skipped]

    @property
    def ok(self) -> bool:
        return not self.failed

    def rendered(self) -> str:
        """The summary table and the verdict, as the CLI prints it."""
        width = max((len(g.name) for g in self.gates), default=10)
        lines = [
            "",
            f"  {'GATE'.ljust(width)}  RESULT   DETAIL",
            f"  {'-' * width}  -------  {'-' * 46}",
        ]
        for gate in self.gates:
            mark = "SKIP" if gate.skipped else ("PASS" if gate.ok else "FAIL")
            lines.append(f"  {gate.name.ljust(width)}  {mark:<7}  {gate.number}")
        lines.append("")
        if self.ok:
            lines.append("  VERDICT: PASS -- every gate is green.")
        else:
            lines.append(
                f"  VERDICT: FAIL -- {len(self.failed)} gate(s) red: "
                + ", ".join(g.name for g in self.failed)
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- the gates


def _scenario_packs() -> dict[str, list[Path]]:
    from ..scenarios.presets import load_preset
    from ..scenarios.registry import SCENARIOS
    from ..scenarios.territory import pack_paths

    out: dict[str, list[Path]] = {}
    for preset_id in sorted(SCENARIOS):
        preset = load_preset(preset_id)
        if preset is not None:
            out[preset_id] = list(pack_paths(preset.id, preset.map_source))
    return out


def _gate_packs_exist(gate: Gate) -> None:
    """Every pack every shipped scenario declares it needs is on disk, and is the one this
    release was verified against.

    First because it is the cheapest and because every gate after it depends on the answer:
    a scenario whose finer pack is missing still *builds*, silently, at country granularity.

    **And pinned, because "eight of eight green" was not portable.** The packs are derived
    artefacts living outside the repository, and until `packs.lock.json` existed nothing
    recorded which build of them a green audit had run against -- so one machine could pass
    every gate while another failed three of them, both readings true. Every problem raised
    here carries the command that fixes it: a check that can fail and cannot be satisfied is
    a check people learn to skip.
    """
    from ..map.manifest import compare, repair_plan

    declared = _scenario_packs()
    wanted = {path for paths in declared.values() for path in paths}
    missing = sorted(str(p) for p in wanted if not p.exists())
    problems = [f"not baked: {p}" for p in missing]

    drift = compare(sorted(wanted))
    problems += [f"{p.name}: {p.kind} -- {p.detail}" for p in drift]
    if plan := repair_plan(drift):
        problems.append("to put this machine right: " + " && ".join(plan))

    gate.problems = problems
    gate.number = f"{len(wanted) - len(missing)}/{len(wanted)} packs"
    gate.ok = not problems


def _gate_packs_verify(gate: Gate) -> None:
    """...and each one passes its own verifier.

    The same `verify_pack` the bake now runs and exits 2 on. Bijection, ring closure,
    orientation and shared-border preservation -- a pack that fails these draws a map with
    holes in it.
    """
    from ..map.pack import GeometryPack
    from ..map.verify import verify_pack

    wanted = sorted({p for paths in _scenario_packs().values() for p in paths})
    checked, problems = 0, []
    for path in wanted:
        if not path.exists():
            continue
        try:
            with GeometryPack(path) as pack:
                report = verify_pack(pack)
            checked += 1
            problems += [f"{path.name}: {line}" for line in report.problems[:3]]
        except Exception as exc:  # noqa: BLE001 - a broken pack must not end the audit
            problems.append(f"{path.name}: could not be opened: {exc}")
    gate.problems = problems
    gate.number = f"{checked} pack(s) verified"
    gate.ok = not problems


def _gate_suite(gate: Gate, *, root: Path) -> None:
    """The packed test suite and ruff, as subprocesses.

    Subprocesses rather than in-process pytest: running the suite inside the interpreter
    that is running the audit would share module state with it, and a gate that can be
    influenced by what it is auditing is not a gate.
    """
    suite = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", "-q", str(root / "tests")],
        capture_output=True, text=True, cwd=str(root), check=False,
    )
    tail = [ln for ln in suite.stdout.splitlines() if "passed" in ln or "failed" in ln]
    lint = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
        capture_output=True, text=True, cwd=str(root), check=False,
    )
    gate.number = (tail[-1].strip() if tail else "no pytest summary") + (
        ", ruff clean" if lint.returncode == 0 else ", ruff FAILED")
    gate.problems = []
    if suite.returncode != 0:
        gate.problems.append(f"pytest exited {suite.returncode}: {tail[-1] if tail else ''}")
    if lint.returncode != 0:
        gate.problems.append(f"ruff exited {lint.returncode}: {lint.stdout.strip()[:400]}")
    gate.ok = not gate.problems


def _gate_schema_parity(gate: Gate) -> None:
    """A fresh world and a migrated one promise the same thing.

    Calls `schema_parity_problems`, which the migration test also calls -- one comparison,
    not two, which is the whole reason it was extracted.
    """
    from ..state.db import connect, create_world_db
    from ..state.migrations import current_version
    from ..state.parity import KNOWN_SCHEMA_GAPS, build_oldest_world, schema_parity_problems

    work = Path(tempfile.mkdtemp(prefix="atlas-release-"))
    try:
        fresh = create_world_db(":memory:")
        path = work / "old.db"
        # Down to schema 1 by removing everything any migration adds, so the whole chain
        # runs. Stripping only the newest migrations let every older ADD COLUMN arrive
        # with `schema.sql`'s constraints already on it, which hid three real gaps (S4).
        build_oldest_world(path)

        migrated = connect(path)
        try:
            gate.number = f"schema {current_version(migrated)}"
            gate.problems = schema_parity_problems(
                fresh, migrated, known_gaps=KNOWN_SCHEMA_GAPS)[:6]
            gate.ok = not gate.problems
        finally:
            migrated.close()
            fresh.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _build(preset_id: str, world_id: str = "audit"):
    """One era, built through the registry exactly as the game does."""
    from ..map.pack import PackSet
    from ..scenarios.presets import load_preset
    from ..scenarios.registry import preset_politics, preset_seed
    from ..scenarios.territory import pack_paths
    from ..state.bootstrap import create_world

    preset = load_preset(preset_id)
    with PackSet.open(pack_paths(preset.id, preset.map_source)) as packs:
        seed = preset_seed(preset, packs, world_id=world_id, player=None)
    conn = create_world(":memory:", seed)
    preset_politics(conn, world_id, preset)
    return preset, seed, conn


def _gate_determinism(gate: Gate) -> None:
    """Every era builds twice to the same seed digest and the same world digest.

    Two builds in one process, so a difference is the builder's and not the machine's. This
    is the gate that catches a wall-clock or a set-iteration order creeping back in -- which
    has happened before, and is invisible in a single build.
    """
    from ..scenarios.registry import SCENARIOS
    from ..state.bootstrap import fingerprint

    problems, seen = [], []
    for preset_id in sorted(SCENARIOS):
        built = []
        for _attempt in (1, 2):
            _preset, seed, conn = _build(preset_id)
            try:
                built.append((seed, fingerprint(conn, "audit")))
            finally:
                conn.close()
        # **The seed is compared by value, not by digest.** `WorldSeed` and everything in it
        # are frozen dataclasses, so equality is the strongest form of this check and needs
        # no hash function that could itself disagree between two callers.
        if built[0][0] != built[1][0]:
            problems.append(f"{preset_id}: two builds produced different WorldSeeds")
        if built[0][1] != built[1][1]:
            problems.append(
                f"{preset_id}: two builds produced different worlds -- "
                f"{built[0][1][:12]} then {built[1][1][:12]}")
        seen.append(preset_id)
    gate.problems = problems
    gate.number = f"{len(seen)} era(s) built twice"
    gate.ok = not problems


def _gate_invariants(gate: Gate) -> None:
    """Every invariant that holds at SEED, against each era's opening world.

    The phase matters: a world that has taken no turns is allowed things a played one is
    not, and the checker is told which phase it is looking at rather than guessing.
    """
    from ..scenarios.registry import SCENARIOS
    from ..testing.invariants import INVARIANTS, Phase, violations

    problems, checked = [], 0
    for preset_id in sorted(SCENARIOS):
        _preset, _seed, conn = _build(preset_id)
        try:
            found = violations(conn, "audit", Phase.SEED)
            problems += [f"{preset_id}: {line}" for line in found[:4]]
            checked += 1
        finally:
            conn.close()
    holding = sum(1 for i in INVARIANTS if Phase.SEED in i.holds_in)
    gate.problems = problems
    gate.number = f"{holding} invariant(s) x {checked} era(s)"
    gate.ok = not problems


def _gate_scenario_audit(gate: Gate) -> None:
    """`atlas audit` for every shipped era, in process.

    Reports the fact counts, because a green audit against a manifest that asserts nothing
    is the failure mode the auditor is built around and the number is what exposes it.
    """
    from ..scenarios.registry import SCENARIOS
    from ..testing.audit import audit, coverage, load_manifest

    problems, counts = [], []
    for preset_id in sorted(SCENARIOS):
        manifest = load_manifest(preset_id)
        if manifest is None:
            problems.append(f"{preset_id}: no manifest, so nothing audits it")
            continue
        _preset, _seed, conn = _build(preset_id)
        try:
            found = audit(conn, "audit", manifest)
        finally:
            conn.close()
        asserted = sum(n for n in coverage(manifest).values() if n > 0)
        counts.append(f"{preset_id.split('-')[0]}:{asserted}")
        problems += [f"{preset_id}: {line}" for line in found[:4]]
    gate.problems = problems
    gate.number = " ".join(counts) + " facts"
    gate.ok = not problems


def _gate_active_cassettes(gate: Gate, *, root: Path) -> None:
    """Every ACTIVE tape is **actually replayed**, and must not miss.

    **This gate used to lie.** It read each active tape, checked the entries carried
    canonical keys, counted them into a variable called `replayed`, and reported "N active"
    -- while the audit's own one-line reason said *every active fixture still answers the
    questions HEAD asks*. Nothing replayed anything. A tape could drift the moment a prompt
    changed and the gate would stay green, which is worse than not having the gate: it is a
    green light that was never wired to a sensor. Found by Codex reading the gate against
    what it claimed.

    So each active tape is now run through `scratchpad/tape_replay.py`, offline, and a miss
    or a mismatch reddens the release. The structural checks are kept in front of it because
    they are instant and they produce a better message for a corrupt tape than a replay
    traceback would.

    **A historical tape that misses is not a failure**, and this is the only gate that has to
    know the difference. `scratchpad/cassettes/INDEX.json` declares which are which, and a
    tape nobody has classified is treated as active -- an unclassified recording is more
    likely to be a new one somebody forgot to label than an old one they meant to retire.

    A subprocess rather than an import, for the reason `_gate_suite` gives: the replay
    harness carries the recorder that built each world, those live in `scratchpad`, and a
    gate that shared module state with what it audits is not a gate.
    """
    from ..providers.cassette import read_cassette

    index = root / "scratchpad" / "cassettes" / "INDEX.json"
    if not index.exists():
        gate.number = "no cassette index"
        gate.problems = [f"{index} is missing, so no tape can be classified"]
        gate.ok = False
        return

    harness = root / "scratchpad" / "tape_replay.py"
    declared = json.loads(index.read_text(encoding="utf-8"))
    active = [t for t in declared.get("tapes", []) if t.get("status") == "active"]
    problems, replayed = [], 0

    if not active:
        # **Not a pass.** A release with no live fixture has nothing checking that its
        # prompts still match anything ever recorded, and reporting that as green is the
        # same defect this gate was just fixed for.
        gate.problems = [
            "no tape is declared active, so nothing checks that HEAD's prompts still match "
            "any recording; re-record one against HEAD and mark it active"]
        gate.number = f"0 active, {len(declared.get('tapes', []))} historical"
        gate.ok = False
        return
    for tape in active:
        tag = tape["tag"]
        path = root / "scratchpad" / "cassettes" / f"{tag}.jsonl"
        if not path.exists():
            problems.append(f"{tag}: declared active and not on disk")
            continue
        try:
            entries = read_cassette(path)
        except Exception as exc:  # noqa: BLE001 - a corrupt tape is a finding, not a crash
            problems.append(f"{tag}: unreadable: {exc}")
            continue
        if not entries:
            problems.append(f"{tag}: no entries")
            continue
        # **By format, not by key.** Every entry carries a key -- `Entry.from_json` refuses
        # a line without one -- so testing for an empty key could never fire. What makes a
        # format-1 entry weak is that its key was hashed over the raw prompt, nonce and
        # all, and only the format it was read from says so.
        weak = [e for e in entries if e.format < 2]
        if weak:
            problems.append(
                f"{tag}: {len(weak)} entry/entries carry no canonical key, so it is "
                "a format-1 tape and cannot gate anything")
            continue

        if not harness.exists():
            # Checked here rather than up front so a tape that is simply missing says so,
            # which is the more specific finding and the more useful one.
            problems.append(f"{harness} is missing, so {tag} cannot be replayed")
            continue
        run = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(harness), tag],
            check=False, capture_output=True, text=True, cwd=str(root), timeout=1800,
        )
        if run.returncode != 0:
            problems.append(f"{tag}: replay failed: {_last_finding(run.stdout)}")
            continue
        replayed += 1

    historical = len(declared.get("tapes", [])) - len(active)
    gate.problems = problems
    gate.number = f"{replayed}/{len(active)} active replayed, {historical} historical"
    gate.ok = not problems


def _last_finding(output: str) -> str:
    """The line of a replay that says what went wrong, or the tail if none stands out.

    The harness prints a per-turn verdict and a summary; the useful line is the MISS or
    MISMATCH, and quoting the whole transcript into a gate summary would bury it.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if "MISS" in line or "MISMATCH" in line:
            return line[:200]
    return (lines[-1][:200] if lines else "no output")


#: Ordered cheap to expensive. The names are what the summary table prints.
GATES: tuple[tuple[str, str], ...] = (
    ("packs-exist", "a scenario whose finer pack is missing degrades to country granularity, silently"),
    ("packs-verify", "a pack that fails its own verifier draws a map with holes in it"),
    ("schema-parity", "a fresh world and a migrated one must promise the same thing"),
    ("determinism", "two builds of one era must agree, or a fingerprint proves nothing"),
    ("invariants", "things that cannot be true of a world that is not broken"),
    ("scenario-audit", "each era must be the year it says it is"),
    ("cassettes", "every active fixture is replayed and must still answer HEAD's questions"),
    ("suite", "the packed test suite and ruff"),
)


def release_audit(
    *,
    root: Path | None = None,
    skip_suite: bool = False,
    only: tuple[str, ...] | None = None,
) -> ReleaseReport:
    """Run every gate and return the verdict. Never raises for a failing gate.

    A gate that blows up is reported as a failure with the exception, rather than ending the
    run -- the same rule the invariant sweep and the scenario auditor both follow, and for
    the same reason: a broken check must not hide the seven that work.
    """
    here = root or Path(__file__).resolve().parents[3]
    report = ReleaseReport()
    runners = {
        "packs-exist": _gate_packs_exist,
        "packs-verify": _gate_packs_verify,
        "schema-parity": _gate_schema_parity,
        "determinism": _gate_determinism,
        "invariants": _gate_invariants,
        "scenario-audit": _gate_scenario_audit,
        "cassettes": lambda g: _gate_active_cassettes(g, root=here),
        "suite": lambda g: _gate_suite(g, root=here),
    }
    for name, because in GATES:
        # `only` runs a subset. Meant for a fast structural check and for tests that need to
        # prove one gate reddens without rebuilding six worlds to do it -- every gate that
        # is not named is simply absent, not silently passed.
        if only is not None and name not in only:
            continue
        gate = Gate(name=name, because=because)
        if name == "suite" and skip_suite:
            gate.skipped = True
            gate.number = "skipped (--skip-suite)"
        else:
            try:
                runners[name](gate)
            except Exception as exc:  # noqa: BLE001 - a broken gate is a finding
                gate.ok = False
                gate.number = "could not run"
                gate.problems = [f"{type(exc).__name__}: {exc}"]
        report.gates.append(gate)
    return report
