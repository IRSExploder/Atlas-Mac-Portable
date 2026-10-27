"""The gate that decides whether the tree ships, and whether it can actually say no.

A release audit that has never been seen to fail is a green light nobody has tested. So the
bulk of this file is fault injection: break one thing, assert **that** gate reddens, assert
the verdict is non-zero, and assert the other gates are undisturbed -- because a gate that
fails for somebody else's reason is worse than no gate at all.

The clean-tree test is deliberately not "everything is green". It asserts every gate *runs*,
that no gate fails silently, and that the gates which cannot depend on local build artefacts
are green. Whether the locally baked packs verify is a fact about this machine's `atlas map
bake`, not about the source tree, and a test that asserted otherwise would fail on a fresh
clone for the wrong reason.
"""

from __future__ import annotations

import json

import pytest

from atlas.testing.release import GATES, Gate, ReleaseReport, _scenario_packs, release_audit

#: The gates that read only the source tree, so their verdict is a property of the commit
#: rather than of whatever is baked into this machine's cache.
SOURCE_ONLY = ("schema-parity", "determinism", "invariants", "scenario-audit", "cassettes")

#: Two gates that build no worlds, used as the control in the fault-injection tests: if a
#: fault reddens one of these it has leaked out of the gate it was aimed at.
CHEAP = ("packs-exist", "cassettes")

#: The gates that build worlds read them from the geometry packs, so on a machine that has
#: not run `atlas map bake` they fail on a missing file rather than on anything under test.
_needs_packs = pytest.mark.skipif(
    not all(p.exists() for paths in _scenario_packs().values() for p in paths),
    reason="geometry packs not baked; run `atlas map bake`")


@pytest.fixture(scope="module")
def already_red():
    """The control gates that are red on this machine before any fault is injected.

    `packs-exist` also holds the baked packs to `packs.lock.json`, and a bake on another
    machine can differ from the pinned hashes. That is a fact about this machine, so it says
    nothing about where an injected fault went. Taken before the test's own patches apply.
    """
    return {g.name for g in release_audit(skip_suite=True, only=CHEAP).failed}


@pytest.fixture(scope="module")
def clean():
    """One real audit, shared: it builds six worlds and is the slow part of this file."""
    return release_audit(skip_suite=True)


# --------------------------------------------------------------------- shape and honesty


#: A real newline, named so the fixtures below read as data rather than escaping.
NEWLINE = "\n"


def test_every_declared_gate_runs(clean):
    assert [g.name for g in clean.gates] == [name for name, _why in GATES]
    assert all(g.number for g in clean.gates), "a gate with no number tells a reader nothing"


def test_no_gate_fails_without_saying_why(clean):
    """The property that makes the verdict trustworthy: a red gate always carries detail."""
    for gate in clean.gates:
        if not gate.ok and not gate.skipped:
            assert gate.problems, f"{gate.name} failed and said nothing"


def test_every_gate_explains_what_it_is_for():
    for _name, because in GATES:
        assert len(because) > 25


@_needs_packs
def test_the_source_only_gates_are_green_on_this_tree(clean):
    """Schema parity, determinism, invariants, the scenario audit and the cassette index --
    all of them read the commit and nothing else, so they must be green here."""
    red = [g.name for g in clean.gates if g.name in SOURCE_ONLY and not g.ok]
    detail = {g.name: g.problems[:2] for g in clean.gates if g.name in SOURCE_ONLY and not g.ok}
    assert not red, f"source-only gate(s) failed: {detail}"


@_needs_packs
def test_the_headline_numbers_are_the_ones_we_publish(clean):
    """If these move, the handoffs are stale and somebody should notice here first.

    **Moved at schema 37**, from 74/62/43, when the manifests gained a `doctrines` section:
    fourteen claims about 1936, twenty-three about 1972 and twenty-eight about now, each of
    them a statement about the period that the auditor holds the seeding to. A number that
    goes *up* here is coverage; one that goes down is a check that stopped running.

    **Modern Day moved 71 -> 91**, and every one of the twenty is coverage in that sense.
    Six treaties the scenario already shipped were simply unguarded -- Article 5 among them.
    Three occupations were added to the world and pinned with it. Seven cores were pinned,
    four of which guard ground *nobody* occupies: Taiwan, Kinmen, Kosovo and Somaliland are
    governed by the people who live in them, and the pin is there so a claim never quietly
    becomes a `home_nation_id`. Four leaders were pinned, one of which -- Pakistan -- had
    nobody in office at all. This era is now the best-guarded of the three, which is the
    right way round for the one that had a live scenario and the thinnest record of it.

    **Then 91 -> 129, when the era stopped being silent about dependencies.** That section
    was a declared silence whose own reason admitted it was a simplification rather than a
    fact -- Greenland, Hong Kong, Guam and the Falklands were sovereign states on the 2026
    map. Thirty-eight are now recorded with their holder, and Antarctica is recorded as
    governed by nobody. A silence that had to be argued for is worth more than an empty
    list, and a silence that turns out to be wrong is worth exactly this much.

    **129 -> 139 with the roster.** Four leaders, six doctrines, and two alliances that
    gained a member: Poland is a NATO power now, and Australia is finally the A in AUKUS --
    an entry that had been carrying "Australia is not a power in this era" as a caveat.
    That is what a roster gap leaves behind when nobody fills it.

    **139 -> 149 with the second tranche.** Italy, Egypt and Vietnam bring three leaders
    and six doctrines, Article 5 gains a seventh party, and the Egypt-Israel Treaty of
    Peace enters the table at all -- it could not be recorded while one of its two parties
    was a minor. A roster gap does not only leave powers out; it leaves the things they
    are party to unrecordable.

    **149 -> 151 with Taiwan**, which is the smallest increment of the three and the one
    that took the most care: a leader and a doctrine, plus two `cores` entries that were
    re-pointed rather than added, because the guard they carry predates the promotion.
    Seating a government is not a ruling on sovereignty, and those two lines are what keep
    the questions apart.

    **A fourth era, 2026-09-18: the Fulda Gap (`hot:87`), and `3 era(s)` became `4`.** An
    alternate 1972 in which the Pact crossed the inner-German border. Its 87 facts are the
    real 1972 record -- cold-war's 85 plus the two occupied front provinces, which are the
    only counterfactual entries and are labelled as such in its manifest. Auditing an
    alternate history against a real record is what proves it diverges only where it says
    it does; the numbers of the other three eras did not move.
    """
    audit = next(g for g in clean.gates if g.name == "scenario-audit")
    assert "gathering:88" in audit.number
    assert "cold:85" in audit.number
    assert "hot:87" in audit.number
    assert "modern:151" in audit.number

    determinism = next(g for g in clean.gates if g.name == "determinism")
    assert "4 era(s)" in determinism.number


def test_skipping_the_suite_is_reported_as_a_skip_not_a_pass(clean):
    suite = next(g for g in clean.gates if g.name == "suite")
    assert suite.skipped and not suite.ok
    assert "SKIP" in clean.rendered()
    # ...and a skip must not be counted as a failure either, or --skip-suite would always red.
    assert suite not in clean.failed


# ------------------------------------------------------------------- the verdict itself


def test_the_verdict_is_pass_only_when_nothing_is_red():
    passing = ReleaseReport(gates=[Gate("a", "why", ok=True), Gate("b", "why", ok=True)])
    assert passing.ok and "VERDICT: PASS" in passing.rendered()

    failing = ReleaseReport(gates=[Gate("a", "why", ok=True), Gate("b", "why", ok=False)])
    assert not failing.ok
    assert "VERDICT: FAIL" in failing.rendered()
    assert "b" in failing.rendered().rsplit("VERDICT", 1)[1], "the verdict must name the gate"


def test_a_gate_that_cannot_run_is_a_failure_not_a_pass(monkeypatch):
    """"We did not check" and "we checked and it was fine" must never be the same answer."""
    def explode(_gate):
        raise RuntimeError("the checker itself is broken")

    monkeypatch.setattr("atlas.testing.release._gate_invariants", explode)
    report = release_audit(skip_suite=True, only=CHEAP + ("invariants",))

    broken = next(g for g in report.gates if g.name == "invariants")
    assert not broken.ok and not broken.skipped
    assert "could not run" in broken.number
    assert any("the checker itself is broken" in p for p in broken.problems)
    assert not report.ok


@_needs_packs
def test_one_broken_gate_does_not_stop_the_others(monkeypatch):
    """A reviewer wants the whole picture, not the first thing that broke."""
    monkeypatch.setattr(
        "atlas.testing.release._gate_schema_parity",
        lambda g: (_ for _ in ()).throw(RuntimeError("boom")))
    # A subset with the broken gate first and a real one after it: the property is ordering,
    # not coverage, and rebuilding six worlds to prove it would be waste.
    report = release_audit(skip_suite=True, only=("schema-parity", "scenario-audit"))

    assert len(report.gates) == 2
    assert next(g for g in report.gates if g.name == "scenario-audit").ok, (
        "a later gate stopped running because an earlier one broke")


# --------------------------------------------------- one fault per gate, one red per fault


def _only_red(report, name: str, already_red: set[str]) -> None:
    """The fault reddened its own gate and nobody else's."""
    red = {g.name for g in report.failed}
    assert name in red, f"{name} should have failed; red were {red or 'none'}"
    unrelated = red - {name, "packs-verify"} - already_red
    assert not unrelated, f"the fault also reddened {unrelated}, which it should not have"


def test_a_missing_pack_reddens_packs_exist(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setattr(
        "atlas.testing.release._scenario_packs",
        lambda: {"cold-war": [Path(tmp_path / "not-baked.pack")]})
    report = release_audit(skip_suite=True, only=("packs-exist",))

    gate = next(g for g in report.gates if g.name == "packs-exist")
    assert not gate.ok and "not baked" in gate.problems[0]
    assert not report.ok


@_needs_packs
def test_a_schema_divergence_reddens_schema_parity(monkeypatch, already_red):
    monkeypatch.setattr(
        "atlas.state.parity.schema_parity_problems",
        lambda fresh, migrated, **kw: ["arsenal.checks means different things"])
    report = release_audit(skip_suite=True, only=CHEAP + ("schema-parity",))

    _only_red(report, "schema-parity", already_red)
    assert "arsenal.checks" in next(
        g for g in report.gates if g.name == "schema-parity").problems[0]


@_needs_packs
def test_a_non_deterministic_build_reddens_determinism(monkeypatch, already_red):
    """The gate that catches a wall clock creeping back into a seed -- which has happened."""
    real = None
    seen: list[int] = []

    def wobble(conn, world_id):
        seen.append(1)
        return f"{real(conn, world_id)}-{len(seen)}"

    import atlas.state.bootstrap as bootstrap
    real = bootstrap.fingerprint
    monkeypatch.setattr(bootstrap, "fingerprint", wobble)
    report = release_audit(skip_suite=True, only=CHEAP + ("determinism",))

    _only_red(report, "determinism", already_red)
    assert "different worlds" in " ".join(
        next(g for g in report.gates if g.name == "determinism").problems)


@_needs_packs
def test_a_violated_invariant_reddens_invariants(monkeypatch, already_red):
    monkeypatch.setattr(
        "atlas.testing.invariants.violations",
        lambda conn, world_id, phase: ["[made-up] the world is on fire"])
    report = release_audit(skip_suite=True, only=CHEAP + ("invariants",))

    _only_red(report, "invariants", already_red)
    assert "on fire" in " ".join(
        next(g for g in report.gates if g.name == "invariants").problems)


@_needs_packs
def test_a_scenario_that_is_not_its_era_reddens_the_scenario_audit(monkeypatch, already_red):
    monkeypatch.setattr(
        "atlas.testing.audit.audit",
        lambda conn, world_id, manifest: ["[alliances] NATO is missing m_prt"])
    report = release_audit(skip_suite=True, only=CHEAP + ("scenario-audit",))

    _only_red(report, "scenario-audit", already_red)
    assert "m_prt" in " ".join(
        next(g for g in report.gates if g.name == "scenario-audit").problems)


def test_a_missing_active_tape_reddens_cassettes(monkeypatch, tmp_path):
    """An ACTIVE fixture that is not on disk is a broken promise."""
    (tmp_path / "scratchpad" / "cassettes").mkdir(parents=True)
    (tmp_path / "scratchpad" / "cassettes" / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "ghost", "status": "active"}]}), encoding="utf-8")

    report = release_audit(root=tmp_path, skip_suite=True)
    gate = next(g for g in report.gates if g.name == "cassettes")
    assert not gate.ok
    assert "declared active and not on disk" in gate.problems[0]


def _tape(cassettes, tag: str, *, fmt: int = 2) -> None:
    """One well-formed entry (format 2 unless asked), which is all the structural half of
    the gate reads."""
    cassettes.joinpath(f"{tag}.jsonl").write_text(
        json.dumps({"format": fmt, "key": "k", "seq": 1, "role": "world_sim",
                    "provider": "p", "model": "m", "prompt": "q", "ok": True,
                    "text": "{}", "data": {}}) + NEWLINE, encoding="utf-8")


def _harness(root, *, exit_code: int, says: str = "") -> None:
    """A stand-in replay harness, so these tests exercise the gate rather than a 12-turn
    Cold War run. What the gate promises is that it *runs* one and believes the answer."""
    root.joinpath("scratchpad", "tape_replay.py").write_text(
        f"print({says!r})" + NEWLINE + f"raise SystemExit({exit_code})", encoding="utf-8")


def test_a_historical_tape_that_is_missing_does_not_redden_anything(tmp_path):
    """**The distinction the gate exists to make.** A tape recorded before a change that
    legitimately altered a prompt should miss, and an audit that reddened on it would teach
    everybody to ignore the audit.

    The fixture carries a live tape as well, because *no* active tape is now a finding in
    its own right -- see below. Two historical tapes that are not on disk must be invisible
    to the gate whether or not anything else is going on.
    """
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [
            {"tag": "alive", "status": "active"},
            {"tag": "long-gone", "status": "historical"},
            {"tag": "also-gone", "status": "historical"},
        ]}), encoding="utf-8")
    _tape(cassettes, "alive")
    _harness(tmp_path, exit_code=0, says="turn 1 ok MATCH")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert gate.ok, gate.problems
    assert "2 historical" in gate.number


def test_the_gate_actually_replays_and_fails_on_a_miss(tmp_path):
    """**The hole this closes.** The gate used to read each tape, check its keys, count it
    into a variable called `replayed`, and report "N active" -- while the audit's own
    one-line reason said *every active fixture still answers the questions HEAD asks*.
    Nothing replayed anything, so a tape could drift the moment a prompt changed and the
    gate would stay green: a green light that was never wired to a sensor. Found by Codex
    reading the gate against what it claimed.
    """
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "drifted", "status": "active"}]}), encoding="utf-8")
    _tape(cassettes, "drifted")
    _harness(tmp_path, exit_code=1, says="  turn 1: MISS")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert not gate.ok, "a tape that misses must redden the release"
    assert "replay failed" in gate.problems[0]
    assert "MISS" in gate.problems[0], "the finding itself, not only the exit code"


def test_a_replay_that_passes_is_counted_as_replayed(tmp_path):
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "good", "status": "active"}]}), encoding="utf-8")
    _tape(cassettes, "good")
    _harness(tmp_path, exit_code=0, says="  turn 1 ok MATCH")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert gate.ok, gate.problems
    assert gate.number.startswith("1/1 active replayed")


def test_an_active_format_1_tape_is_refused_even_when_it_replays(tmp_path):
    """**The guard that could never fire.** It tested for an empty key, and every entry has
    one -- `Entry.from_json` refuses a line without it -- so a format-1 tape, whose keys
    were hashed over the raw nonce-bearing prompt and which replays only through the
    weak-key rescue, sailed through to the replay and was counted as a gate. The harness
    here passes, so the only thing that can redden the gate is the format check.
    """
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "old", "status": "active"}]}), encoding="utf-8")
    _tape(cassettes, "old", fmt=1)
    _harness(tmp_path, exit_code=0, says="  turn 1 ok MATCH")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert not gate.ok, "a format-1 tape cannot gate anything"
    assert "format-1 tape" in gate.problems[0]


def test_an_index_with_no_live_tape_is_not_a_pass(tmp_path):
    """**A release whose every tape is retired has stopped checking anything**, and
    reporting that as all clear is the same defect the gate was just fixed for."""
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "long-gone", "status": "historical"}]}),
        encoding="utf-8")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert not gate.ok
    assert "no tape is declared active" in gate.problems[0]


def test_a_tape_that_cannot_be_replayed_at_all_is_a_finding(tmp_path):
    """No harness on disk is not an excuse to pass. It is the gate having no way to keep
    its promise, which is exactly what it must say."""
    cassettes = tmp_path / "scratchpad" / "cassettes"
    cassettes.mkdir(parents=True)
    (cassettes / "INDEX.json").write_text(
        json.dumps({"tapes": [{"tag": "orphan", "status": "active"}]}), encoding="utf-8")
    _tape(cassettes, "orphan")

    gate = next(g for g in release_audit(root=tmp_path, skip_suite=True).gates
                if g.name == "cassettes")
    assert not gate.ok
    assert "cannot be replayed" in gate.problems[0]


@_needs_packs
def test_the_real_index_keeps_a_live_fixture(clean):
    """**At least one tape must still be gating**, whichever one it is.

    Pinning the count at two, and naming PT37 and PT38, was a test about the state of the
    cupboard rather than about the guarantee. Both of those retired the day the war-aims and
    peace channels changed the world-sim schema -- legitimately, which is what `historical`
    means -- and PT44 replaced them. The claim worth keeping is that the cupboard is not
    empty: a release audit with no active tape has stopped checking that HEAD still asks the
    questions somebody recorded answers to.
    """
    gate = next(g for g in clean.gates if g.name == "cassettes")
    assert gate.ok, gate.problems
    assert not gate.number.startswith("0/"), (
        "no tape gates any more, so nothing would notice HEAD changing a question")
    replayed, of = gate.number.split(" ")[0].split("/")
    assert replayed == of, "an active tape that does not replay is a fixture in name only"
