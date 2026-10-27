"""Run a world for many turns and notice the things a human would have to be watching for.

Every integration seam found so far was found by a person reading a transcript and thinking
*that is not right*: research vanishing into `other`, an active pact not stopping an
invasion, the Council resolving nothing turn after turn, two nations dominating the world at
once. Each was visible in the output and none was visible in a *test*, because a test asks
whether one thing behaves and a seam is two things disagreeing.

So this is the reading, automated. It plays a world, records everything, and applies a set
of **suspicion detectors** -- each of which is a sentence a reviewer would have said out
loud. A detector never decides a run is wrong; it says *this pattern is the shape of a bug
we have had before*, and points at the turn.

**Detectors are the product here, not the harness.** The loop is twenty lines; the value is
entirely in the list of things worth being suspicious about, and every one of them is a bug
this project actually shipped.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DETECTORS",
    "Finding",
    "TurnRecord",
    "bundle",
    "inspect_run",
    "suspicious",
]


@dataclass(slots=True)
class TurnRecord:
    """Everything one turn produced, kept so a detector can compare across turns."""

    turn: int
    fingerprint: str = ""
    report_lines: list[str] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    order_outcomes: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    applied_batches: int = 0
    rejected_batches: int = 0
    calls: int = 0
    problems: list[str] = field(default_factory=list)
    #: What a rewind to this turn reproduced, when the harness checked. Empty when it did
    #: not -- a rewind is expensive and a run may only sample it.
    replayed_fingerprint: str = ""
    status: str = ""
    #: Engine-accepted output per role: ``{role: {answered, failed, batches, ...}}``.
    #: **Not provider success**, which the `calls` field above already carries. A provider
    #: that answers every call and proposes diffs the rules refuse is worse than one that
    #: fails loudly, and a run recorded without this cannot tell the two apart afterwards --
    #: which is exactly the comparison the world-sim re-pin needed and did not have.
    accepted: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Invariants that did not hold at the end of this turn. Recorded per turn rather than
    #: only at the end of the run because *when* a world stopped being consistent is most
    #: of the information: a violation present on turn 12 and absent on turn 11 names the
    #: turn to look at, and one present at the end says only that something, once, went
    #: wrong. See :mod:`atlas.testing.invariants`.
    invariants: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Finding:
    """One suspicion, and where to look."""

    detector: str
    turn: int
    detail: str

    def rendered(self) -> str:
        return f"turn {self.turn}: [{self.detector}] {self.detail}"


Detector = Callable[[sqlite3.Connection, str, Sequence[TurnRecord]], list[Finding]]


# --------------------------------------------------------------------- the detectors


def _accepted_order_with_no_consequence(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A turn that accepted orders, applied batches, and moved nothing.

    The shape of *the tracked agents return empty*: a model call succeeds, a batch is
    recorded, and the world is byte-identical afterwards.
    """
    out: list[Finding] = []
    for index, record in enumerate(run):
        if index == 0 or not record.orders or not record.applied_batches:
            continue
        if record.fingerprint and record.fingerprint == run[index - 1].fingerprint:
            out.append(Finding(
                "accepted-order-no-consequence", record.turn,
                f"{len(record.orders)} order(s) and {record.applied_batches} batch(es) "
                "applied, and the world is unchanged",
            ))
    return out


def _known_name_rejected_as_unknown(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A refusal saying a place does not exist, when the world contains it.

    The shape of the objectives naming bug: the briefing showed *Northmark*, the staff
    wrote *Northmark*, and the engine said there is no such region.
    """
    names = {
        str(row["name"]).lower()
        for row in conn.execute(
            "SELECT name FROM region WHERE world_id = ?", (world_id,))
    } | {
        str(row["name"]).lower()
        for row in conn.execute(
            "SELECT name FROM nation WHERE world_id = ?", (world_id,))
    }
    out: list[Finding] = []
    for record in run:
        for problem in record.problems:
            lowered = problem.lower()
            if "no such" not in lowered and "not a region" not in lowered:
                continue
            # **The actor prefix is stripped first, and that is the load-bearing half.**
            # PT31 found this detector firing on almost every objectives refusal, because
            # those read `Belarus: blr is not a region` -- the nation making the request is
            # named at the front, and the detector was matching *that* and concluding the
            # world contained the refused place. Two of its three findings on the modern
            # map were this, and the sandbox's only one was too.
            #
            # Whole words as well, so `aurelian_straits` is not evidence for `Aurelia`.
            # A false positive here is worse than a miss: it is the instrument
            # manufacturing the finding it exists to detect.
            body = lowered.split(":", 1)[1] if ":" in lowered else lowered
            named = [
                name for name in names
                if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", body)
            ]
            if named:
                out.append(Finding(
                    "known-name-rejected", record.turn,
                    f"refused {named[0]!r} as unknown, and the world has it: {problem}",
                ))
    return out


def _active_treaty_not_honoured(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """An active pact between two powers who are at war and nobody recorded a breach.

    The shape of the treaty seam. After decision 132 a breach is written, so a surviving
    case means either the breach path did not run or the pact was not readable to it.
    """
    rows = conn.execute(
        """
        SELECT c.id, c.kind FROM commitment c
         WHERE c.world_id = ? AND c.status = 'active'
           AND c.kind IN ('non_aggression_pact', 'defensive_pact', 'alliance')
        """,
        (world_id,),
    ).fetchall()
    out: list[Finding] = []
    for row in rows:
        parties = [
            str(p["nation_id"]) for p in conn.execute(
                "SELECT nation_id FROM commitment_nation WHERE commitment_id = ?",
                (row["id"],))
        ]
        if len(parties) < 2:
            continue
        marks = ",".join("?" * len(parties))
        at_war = conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
            f"   AND nation_a IN ({marks}) AND nation_b IN ({marks}) LIMIT 1",  # noqa: S608
            (world_id, *parties, *parties),
        ).fetchone()
        if at_war:
            out.append(Finding(
                "active-treaty-not-honoured", run[-1].turn if run else 0,
                f"commitment {row['id']} ({row['kind']}) is active between parties at war",
            ))
    return out


def _repeated_refusal_of_an_advertised_capability(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """The same advertised action refused turn after turn.

    The shape of *research exists and the parser cannot order it*: the menu offers it, the
    player asks for it, and it is refused every time.
    """
    from ..engine.capabilities import action_types

    counts: dict[str, int] = {}
    for record in run:
        for outcome in record.order_outcomes:
            for action_type in action_types():
                if action_type in outcome and (
                    "refused" in outcome or "not_attempted" in outcome
                ):
                    counts[action_type] = counts.get(action_type, 0) + 1
    return [
        Finding("repeatedly-refused-capability", run[-1].turn if run else 0,
                f"{action_type} was advertised and refused {count} times")
        for action_type, count in sorted(counts.items())
        if count >= 3
    ]


def _several_simultaneous_winners(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """Two nations told they dominate the world on the same turn."""
    out: list[Finding] = []
    for record in run:
        claims = [line for line in record.report_lines if "holds more of the world" in line]
        if len(claims) > 1:
            out.append(Finding(
                "several-winners", record.turn,
                f"{len(claims)} powers were announced as dominant at once",
            ))
    return out


def _model_call_with_no_trace(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """Calls were made, nothing was applied, and nothing was refused either.

    A refusal is a result. Silence is not, and it is what a provider returning an empty
    answer looks like from the outside.
    """
    return [
        Finding("call-with-no-trace", record.turn,
                f"{record.calls} model call(s), no batch applied and none rejected")
        for record in run
        if record.calls and not record.applied_batches and not record.rejected_batches
    ]


def _fingerprint_did_not_move_for_a_busy_turn(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A turn that recorded events and left the world byte-identical."""
    out: list[Finding] = []
    for index, record in enumerate(run):
        if index == 0 or not record.events:
            continue
        if record.fingerprint and record.fingerprint == run[index - 1].fingerprint:
            out.append(Finding(
                "events-without-state", record.turn,
                f"{len(record.events)} event(s) recorded and no state moved",
            ))
    return out


def _construction_silently_substituted(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """Something was built that nobody named.

    The shape of the transport-becomes-infantry bug: an order for one kind produced a
    different kind and said nothing.
    """
    out: list[Finding] = []
    for record in run:
        asked = {
            str(order.get("unit_kind") or "").lower()
            for order in record.orders if order.get("unit_kind")
        }
        if not asked:
            continue
        for line in record.report_lines:
            if "raised" not in line.lower():
                continue
            if not any(kind and kind in line.lower() for kind in asked):
                out.append(Finding(
                    "construction-substituted", record.turn,
                    f"ordered {sorted(asked)} and the report says {line!r}",
                ))
    return out


def _rewind_fingerprint_mismatch(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A turn whose recorded fingerprint does not match what a rewind reproduces.

    Only meaningful when a run recorded a `replayed_fingerprint` -- the harness rewinds to
    a turn and re-reads the digest, and a mismatch means the world does not unwind to where
    it said it was. The gravest thing this file can find: every other detector describes a
    mechanic behaving oddly, and this one says the audit trail is wrong.
    """
    return [
        Finding("rewind-fingerprint-mismatch", record.turn,
                f"recorded {record.fingerprint[:12]} and a rewind reproduced "
                f"{record.replayed_fingerprint[:12]}")
        for record in run
        if record.replayed_fingerprint
        and record.fingerprint
        and record.replayed_fingerprint != record.fingerprint
    ]


def _impossible_objectives(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A power naming ground it cannot act on, repeatedly.

    One refused objective is a staff being ambitious. The same power refused three turns
    running is a staff that has not understood what it was shown -- which is a briefing
    problem rather than a strategy problem, and the two look identical from inside a turn.
    """
    counts: dict[str, int] = {}
    for record in run:
        for problem in record.problems:
            lowered = problem.lower()
            # **"borders none of your territory" is deliberately absent.** PT31 counted it
            # and should not have: that message is the *deferred* path, where the objective
            # is **accepted** and waits for the front to reach it. It reads like a refusal
            # only because the turn engine files deferred objectives under
            # `report.rejections` -- which is worth fixing in the engine, and is not this
            # detector's business to guess at.
            if not any(
                phrase in lowered
                for phrase in ("is not a region", "there is no region",
                               "nothing there to push", "declare war before")
            ):
                continue
            who = problem.split(":", 1)[0].strip() if ":" in problem else "somebody"
            counts[who] = counts.get(who, 0) + 1
    return [
        Finding("impossible-objectives", run[-1].turn if run else 0,
                f"{who} named ground it cannot act on {count} times")
        for who, count in sorted(counts.items())
        if count >= 3
    ]


def _engine_refused_its_own_batch(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """A batch the engine wrote, refused by the engine's own rules.

    **The bug class this project keeps finding one site at a time.** `breach` charged a
    violator more legitimacy than it had; the arrears charge took a bankrupt minor below
    zero; the occupation drag did the same to a spent occupier. Every one was refused by
    ECON-003, which names no diff and therefore condemns the *whole batch* -- and two of
    those batches covered the entire world, so one exhausted country cost every other
    country its turn.

    A model batch being refused is the system working: the rules exist to refuse models.
    An **engine** batch being refused is the engine proposing something it had already
    decided was true, and there is no reading of that which is not a defect.

    Read from `diff_batch` rather than from the report's prose, because the role and the
    status are ground truth the applier wrote and a phrasing change cannot hide them.
    """
    rows = conn.execute(
        """
        SELECT turn, COUNT(*) AS n, MIN(rejection_reason) AS why
          FROM diff_batch
         WHERE world_id = ? AND proposer_role = 'engine' AND status = 'rejected'
         GROUP BY turn ORDER BY turn
        """,
        (world_id,),
    ).fetchall()
    return [
        Finding(
            "engine-batch-refused", int(row["turn"]),
            f"{row['n']} batch(es) the engine wrote were refused by its own rules: "
            f"{str(row['why'] or 'no reason recorded')[:160]}",
        )
        for row in rows
    ]


def _an_invariant_did_not_hold(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """Things that must never be true of a world, and were.

    **Folded in here rather than reported beside the detectors**, because they are the same
    instrument pointed at two kinds of evidence: a detector says *this is the shape of a bug
    we have had*, and an invariant says *this cannot be true of a world that is not broken*.
    A run whose verdict has to be read in two places is a run whose verdict gets read once.

    Two sources, and the difference matters. Anything the harness recorded per turn names
    the turn it first went wrong, which is most of the information. The final sweep catches
    a world that is inconsistent now even though nobody was watching each turn -- which is
    every run of the harness written before this existed.
    """
    from .invariants import Phase, violations

    out = [
        Finding("invariant-violated", record.turn, line)
        for record in run
        for line in record.invariants
    ]
    if out:
        return out
    # Only when nothing was recorded per turn: otherwise the same violation is reported
    # twice, once against the turn it started on and once against the end of the run, and
    # the second copy is strictly less useful.
    return [
        Finding("invariant-violated", run[-1].turn if run else 0, line)
        for line in violations(conn, world_id, Phase.REST)
    ]


#: Every detector, and the name it reports under. **This list is the product.** Each entry
#: is a sentence a reviewer said out loud about a real bug; adding one is how a seam found
#: by hand stops needing to be found by hand again.
DETECTORS: tuple[tuple[str, Detector], ...] = (
    ("accepted-order-no-consequence", _accepted_order_with_no_consequence),
    ("known-name-rejected", _known_name_rejected_as_unknown),
    ("active-treaty-not-honoured", _active_treaty_not_honoured),
    ("repeatedly-refused-capability", _repeated_refusal_of_an_advertised_capability),
    ("several-winners", _several_simultaneous_winners),
    ("call-with-no-trace", _model_call_with_no_trace),
    ("events-without-state", _fingerprint_did_not_move_for_a_busy_turn),
    ("construction-substituted", _construction_silently_substituted),
    ("rewind-fingerprint-mismatch", _rewind_fingerprint_mismatch),
    ("impossible-objectives", _impossible_objectives),
    ("engine-batch-refused", _engine_refused_its_own_batch),
    ("invariant-violated", _an_invariant_did_not_hold),
)


def suspicious(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> list[Finding]:
    """Every detector, over one recorded run. Ordered by turn, then by detector.

    A detector that raises is reported as a finding against itself rather than allowed to
    end the sweep -- a broken detector must not hide the seven working ones.
    """
    found: list[Finding] = []
    for name, detector in DETECTORS:
        try:
            found.extend(detector(conn, world_id, run))
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            found.append(Finding(
                "detector-failed", run[-1].turn if run else 0,
                f"{name} could not run: {type(exc).__name__}: {exc}",
            ))
    return sorted(found, key=lambda f: (f.turn, f.detector, f.detail))


def inspect_run(
    conn: sqlite3.Connection, world_id: str, run: Sequence[TurnRecord]
) -> str:
    """A readable verdict for a recorded run."""
    findings = suspicious(conn, world_id, run)
    if not findings:
        return f"{len(run)} turn(s) inspected, nothing suspicious."
    lines = [f"{len(run)} turn(s) inspected, {len(findings)} thing(s) worth a look:"]
    lines.extend(f"  {finding.rendered()}" for finding in findings)
    return "\n".join(lines)


def bundle(
    conn: sqlite3.Connection,
    world_id: str,
    run: Sequence[TurnRecord],
    findings: Sequence[Finding],
    into: Path,
    cassette: Path | None = None,
) -> Path:
    """Write a portable bug bundle: the world, the run, the findings, and how to replay.

    Everything needed to reproduce without the machine that found it. The database is
    copied through SQLite's own backup rather than by copying the file, because a live
    connection has pages in its WAL that a file copy would miss -- which would produce a
    bundle that is subtly not the world the findings came from.

    **The cassette comes too, when there is one, and it is the piece that makes the bundle
    mean what its name says.** A world and a list of findings say what happened; only the
    tape says *why the engine did it*, because the answers that drove the run are on it and
    nowhere else. Without it a bundle can be inspected and not re-run, which is the same
    position PT31 left us in -- and the reason the cassette exists at all.
    """
    into.mkdir(parents=True, exist_ok=True)

    copy = sqlite3.connect(into / "world.sqlite3")
    with copy:
        conn.backup(copy)
    copy.close()

    (into / "run.json").write_text(
        json.dumps(
            [
                {
                    "turn": record.turn,
                    "status": record.status,
                    "fingerprint": record.fingerprint,
                    "replayed_fingerprint": record.replayed_fingerprint,
                    "orders": record.orders,
                    "order_outcomes": record.order_outcomes,
                    "report": record.report_lines,
                    "events": record.events[:50],
                    "applied_batches": record.applied_batches,
                    "rejected_batches": record.rejected_batches,
                    "calls": record.calls,
                    "problems": record.problems,
                    "invariants": record.invariants,
                }
                for record in run
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (into / "findings.txt").write_text(
        inspect_run(conn, world_id, run) + "\n", encoding="utf-8"
    )
    carried: list[str] = []
    if cassette is not None and Path(cassette).exists():
        tape = Path(cassette)
        (into / tape.name).write_bytes(tape.read_bytes())
        carried.append(tape.name)
        sidecar = tape.with_suffix(".fingerprints.json")
        if sidecar.exists():
            (into / sidecar.name).write_bytes(sidecar.read_bytes())
            carried.append(sidecar.name)

    first = min((f.turn for f in findings), default=0)
    # **The commands were both wrong**, which the Codex review of phase 2 caught: `rewind`
    # takes the turn as a positional and the world as `--world-id`, not `--world`/`--to`,
    # and there has never been an `atlas jump` command at all. A reproduction recipe that
    # does not run is worse than none, because it costs a reader the time to find that out.
    lines = [
        "# Replaying this",
        "",
        f"World `{world_id}`, {len(run)} turn(s) recorded, first finding on turn {first}.",
        "",
        "## What is here",
        "",
        "- `world.sqlite3` -- the world as it stood when the findings were taken.",
        "- `run.json` -- every turn's orders, outcomes, fingerprints and invariants.",
        "- `findings.txt` -- what the detectors and invariants made of it.",
    ]
    if carried:
        lines += [
            f"- `{carried[0]}` -- **every model call of the run**: what was asked and what "
            "came back.",
            *([f"- `{carried[1]}` -- each turn's fingerprint, for checking a replay "
               "reproduces it."] if len(carried) > 1 else []),
            "",
            "## Re-running it, with no CLI and no quota",
            "",
            "The tape is the run. Point the replay harness at it:",
            "",
            "```bash",
            f"cp {carried[0]} scratchpad/cassettes/",
            *([f"cp {carried[1]} scratchpad/cassettes/"] if len(carried) > 1 else []),
            f"python scratchpad/lab_replay.py {Path(carried[0]).stem}",
            "```",
            "",
            "It exits non-zero if the engine no longer reproduces the run: 2 for a call the "
            "tape has no answer for, 3 for a fingerprint that moved, 4 for a recorded call "
            "the engine never made.",
        ]
    else:
        lines += [
            "",
            "**No cassette was recorded for this run**, so it can be inspected and not "
            "re-run. Record one (`lab_run.py` does it by default) if this needs to be "
            "reproduced rather than read.",
        ]
    lines += [
        "",
        "## Looking at the world directly",
        "",
        "```bash",
        f"atlas rewind {max(0, first - 1)} --world-id {world_id} --path world.sqlite3",
        "```",
        "",
        "That puts the copy back to the turn before the first finding, so the state the "
        "run entered that turn with can be read.",
        "",
    ]
    (into / "REPLAY.md").write_text("\n".join(lines), encoding="utf-8")
    return into
