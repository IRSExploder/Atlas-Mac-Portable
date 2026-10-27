"""The gravest bug class this project has, and a structural guard against a fourth one.

**Fingerprinted state written outside the diff pipeline does not rewind.** PT31 found it in
`leader` and `commitment`; decision 132 fixed those two and did not find the other two,
because the fix was reasoned about per-module rather than swept for. This file exists so the
next instance is caught by a test rather than by a live run.

The behavioural tests below pin the two that were found. The structural one is the more
valuable of the three: it fails on a *new* direct write to a fingerprinted table anywhere in
the engine, which is the only way this class stops recurring.
"""

from __future__ import annotations

import re
from pathlib import Path

from atlas.engine.focus import apply_focus
from atlas.state.bootstrap import _FINGERPRINT_TABLES, fingerprint
from atlas.state.replay import rewind_to

_SOURCE = Path(__file__).resolve().parent.parent / "src" / "atlas"

#: Where a direct write to fingerprinted state is correct, and why. **Every entry is a
#: claim that a rewind puts this back by some other route**, and none of them may be added
#: to without that being true.
_ALLOWED: dict[tuple[str, str], str] = {
    ("engine/turn_engine.py", "world"):
        "the clock. `rewind_to` restores turn_number and world_date explicitly, which is "
        "why it is the one piece of fingerprinted state that may move outside a diff.",
    ("engine/eve.py", "world"):
        "engine-vs-engine setup, before turn 1 exists. Not reachable in a played game.",
    ("engine/eve.py", "nation"):
        "the same: assigning nations to blocs when an EVE run is configured.",
    ("engine/doctrines.py", "doctrine_stance"):
        "`seed_doctrines`, which runs inside `apply_politics` at world creation -- there "
        "is no turn behind turn zero for a rewind to restore. Every doctrine written after "
        "that goes through `adopt`, which is diffed, and a test holds that.",
    ("engine/diplomacy.py", "relation"):
        "`sync_treaty_status` with no event to cite -- the seeding path, which runs "
        "before any turn exists and so has nothing for a rewind to undo.",
}

_STATEMENTS = ("INSERT INTO", "INSERT OR IGNORE INTO", "INSERT OR REPLACE INTO",
               "UPDATE", "DELETE FROM")


def _direct_writes() -> dict[tuple[str, str], list[int]]:
    """Every ``(module, fingerprinted table)`` written with raw SQL outside ``state/``."""
    tables = {table for table, _order, _scope in _FINGERPRINT_TABLES}
    # `event` is written by `record_event`, which is the engine's own front door for it and
    # is deleted wholesale by a rewind rather than undone diff by diff.
    tables.discard("event")

    pattern = re.compile(
        r"\b(?:" + "|".join(s.replace(" ", r"\s+") for s in _STATEMENTS) + r")\s+"
        r"(" + "|".join(sorted(tables)) + r")\b",
        re.IGNORECASE,
    )
    found: dict[tuple[str, str], list[int]] = {}
    for path in sorted(_SOURCE.rglob("*.py")):
        relative = path.relative_to(_SOURCE).as_posix()
        # `state/` *is* the pipeline; `scenarios/` builds a world before any turn exists.
        if relative.startswith(("state/", "scenarios/")):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                found.setdefault((relative, match.group(1).lower()), []).append(number)
    return found


def test_no_new_module_writes_fingerprinted_state_outside_the_diff_pipeline():
    """The guard. A new entry here is a rewind bug that has not been noticed yet.

    If this fails on a write you believe is correct, the fix is to add it to `_ALLOWED`
    **with the reason a rewind still restores it** -- not to widen the pattern.
    """
    unexpected = {
        key: lines for key, lines in _direct_writes().items() if key not in _ALLOWED
    }
    assert not unexpected, (
        "fingerprinted state written outside the diff pipeline:\n"
        + "\n".join(
            f"  {module}: {table} at line(s) {', '.join(map(str, lines))}"
            for (module, table), lines in sorted(unexpected.items())
        )
    )


def test_everything_a_diff_can_address_is_in_the_fingerprint():
    """**The guard that would have caught all three at once.**

    `commitment_nation`, `air_control` and `event_visibility` were all diffable and none was
    digested, so each could be changed experimentally with no fingerprint move -- which
    makes every rewind-exactness claim about them vacuous. Found by the Codex review of
    phase 2, one table at a time; this is the rule behind them.

    A table a diff can address is state the engine changes, and state the engine changes is
    what a rewind has to put back. If it is not in the digest, nothing can tell whether it
    did.
    """
    from atlas.state.apply import PRIMARY_KEY

    digested = {table for table, _order, _scope in _FINGERPRINT_TABLES}
    missing = sorted(set(PRIMARY_KEY) - digested)
    assert not missing, (
        f"these tables are diffable and not fingerprinted, so a rewind cannot be proved "
        f"to have restored them: {missing}"
    )


def test_the_allowlist_has_not_gone_stale():
    """An exemption for a write that no longer exists is an exemption that will one day
    cover a write nobody argued for."""
    writes = _direct_writes()
    assert not [key for key in _ALLOWED if key not in writes], (
        f"these exemptions no longer describe anything: "
        f"{sorted(key for key in _ALLOWED if key not in writes)}"
    )


def test_a_focus_promotion_unwinds(conn):
    """A power promoted on turn 2 and rewound to turn 1 was still tracked.

    `is_tracked`, `tracked_since_turn` and `tracked_reason` are columns of `nation`, and
    `fingerprint()` hashes every column of every fingerprinted table -- so this moved the
    digest on ordinary turns of an ordinary game, which is the same signature PT31 caught
    on turn 14 and attributed entirely to the coup.
    """
    conn.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = 'sandbox'"
        "   AND ((nation_a = 'karthos' AND nation_b = 'aurelia')"
        "     OR (nation_a = 'aurelia' AND nation_b = 'karthos'))"
    )
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = 'sandbox'")
    conn.commit()
    before = fingerprint(conn, "sandbox")
    tracked_before = _tracked(conn)

    promoted, _demoted = apply_focus(
        conn, "sandbox", player_nation="aurelia", turn=2, date="1897-05-01"
    )
    assert promoted, "this test is worthless if nobody was promoted"
    assert _tracked(conn) != tracked_before

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = 'sandbox'")
    conn.commit()
    report = rewind_to(conn, "sandbox", 1)

    assert report.ok, report.problems
    assert _tracked(conn) == tracked_before, "the roster outlived the turn that set it"
    assert fingerprint(conn, "sandbox") == before


def test_a_focus_demotion_unwinds(conn):
    """The other direction, which is the one that loses information: a demotion clears
    `tracked_since_turn` and `tracked_reason`, and a rewind has to put both back."""
    conn.execute(
        "UPDATE nation SET is_tracked = 1, tracked_since_turn = 0,"
        " tracked_reason = 'seeded' WHERE id = 'veyra'"
    )
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = 'sandbox'")
    conn.commit()
    before = fingerprint(conn, "sandbox")

    # Veyra borders the player and so earns its place in an ordinary reweighing. A roster
    # with no seats is the shortest way to make the demotion happen and is a real call:
    # `cap` is a parameter precisely so the ceiling can be argued with.
    _promoted, demoted = apply_focus(
        conn, "sandbox", player_nation="aurelia", cap=0, turn=2, date="1897-05-01"
    )
    assert demoted == ["veyra"], "this test is worthless if nobody was demoted"

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = 'sandbox'")
    conn.commit()
    assert rewind_to(conn, "sandbox", 1).ok
    assert conn.execute(
        "SELECT is_tracked, tracked_since_turn, tracked_reason FROM nation"
        " WHERE id = 'veyra'"
    ).fetchone()[:] == (1, 0, "seeded")
    assert fingerprint(conn, "sandbox") == before


def _tracked(conn) -> list[tuple]:
    return [tuple(row) for row in conn.execute(
        "SELECT id, is_tracked, tracked_since_turn, tracked_reason FROM nation"
        " WHERE world_id = 'sandbox' ORDER BY id"
    )]


# ------------------------------------------------- and the classification behind them all
#
# `test_everything_a_diff_can_address_is_in_the_fingerprint` closes one direction: a table
# a diff can write must be one the digest can see. This closes the other, and it is the
# guard that would have caught `commitment_nation`, `air_control` and `event_visibility`
# *before* the Codev review found them one at a time -- because it forces every table in
# the schema to be classified, and the only classification that needs no argument is
# "diffed and digested".

#: Every table that is **not** carried by the diff pipeline, and the reason a rewind
#: restores it anyway. A new table lands in none of these buckets and fails the test, which
#: is the point: the decision gets made when the table is added rather than when a live run
#: catches its absence.
_NOT_DIFFED: dict[str, str] = {
    "doctrine": "superseded by `doctrine_stance` at schema 38 and read by nothing. It is "
                "kept rather than dropped because dropping it is the destructive statement "
                "this whole guard exists to refuse, and because a save that still carries "
                "rows in it had them copied across -- so there is nothing in it a rewind "
                "could need to restore.",
    # Restored because a rewind deletes events past its target turn outright, and these
    # hang off an event by a cascading foreign key.
    # What a government asked for and did not get, so the next briefing can say so. Not a
    # diff on purpose: a refusal is the record of a change that did *not* happen, and
    # routing it through the pipeline would put it in `diff_batch`, make it citable as a
    # `reason_event_id`, and hand a model a way to point at its own rejection as the cause
    # of something. Truncated by turn in `rewind_to` alongside `routing_log`, and it must
    # be -- a rewound world that opened by telling a staff it was refused something it
    # never proposed would be the layer lying about the one thing it exists to report.
    # It is also HISTORY the engine reads back, so `REGISTRY` flags it `gates_play` and a
    # paired run compares it row for row -- which makes the truncation load-bearing twice
    # over: rows outliving a rewind would make one world compare as two.
    "refusal": "deleted past the target turn by `rewind_to`, like every HISTORY row",
    "event": "deleted wholesale by `rewind_to`, not undone diff by diff",
    "event_participant": "cascades with the event that owns it; digested so that is provable",
    "event_cause": "cascades with the event that owns it",
    # The audit trail. A rewound world is *meant* to remember the path not taken.
    "diff": "the audit trail; a rewind prunes it by turn and it is not world state",
    "diff_batch": "the audit trail",
    "routing_log": "the audit trail",
    "digest": "a cached reading of history, pruned by turn",
    "action": "what the player typed, pruned by turn",
    # The clock, restored explicitly.
    "turn": "the clock; `rewind_to` deletes past its target and restores world_date from it",
    # **Not world state at all: a fact about the file.** Where a line was forked from does
    # not stop being true because the line was rewound, and a fork rewound to before its
    # own fork point is still a fork. Written once when the branch is made, outside the
    # diff pipeline on purpose -- and kept out of the fingerprint for the same reason, so
    # a fork is byte-identical to the turn it was taken from.
    "world_lineage": "lineage of the save file, not of the world; survives a rewind by design",
    # Written once, at world creation, and never again.
    "region_adjacency": "geometry; written at seed and immutable thereafter",
    "region_geometry": "geometry; written at seed and immutable thereafter",
    "bloc": "EVE configuration, set before turn 1 and not played",
    # **The one that is a decision rather than a fact.** Membership is state, and the
    # organisation design note gives `admit` and `expel` a vocabulary -- neither of which is
    # built. While nothing writes it after seeding there is nothing for a rewind to undo;
    # the first handler that writes it has to make this table diffable and fingerprinted,
    # and this entry is what will fail when somebody does.
    "organisation_member": (
        "written only by the seeder today. `admit`/`expel` are designed and unbuilt: the "
        "handler that builds them must add this table to PRIMARY_KEY and _FINGERPRINT_TABLES"
    ),
}


def _tables(conn) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            "   AND name NOT LIKE 'sqlite_%'"
        )
    }


def test_every_table_is_either_diffed_or_argued_for(conn):
    """A table that is neither carried by the diff pipeline nor listed above is state
    nobody has decided about, and a rewind will not put it back."""
    from atlas.state.apply import PRIMARY_KEY

    unclassified = sorted(_tables(conn) - set(PRIMARY_KEY) - set(_NOT_DIFFED))
    assert not unclassified, (
        "these tables are not written through diffs and carry no argument for why a "
        f"rewind restores them anyway: {unclassified}"
    )


def test_the_exemptions_still_describe_real_tables(conn):
    """An argument for a table that no longer exists is an argument that will one day be
    read as covering a different one."""
    stale = sorted(set(_NOT_DIFFED) - _tables(conn))
    assert not stale, f"these exemptions no longer describe anything: {stale}"


def test_nothing_is_both_diffed_and_exempted(conn):
    """The two lists are a partition, or the reason recorded for a table is not the reason
    it is actually safe."""
    from atlas.state.apply import PRIMARY_KEY

    both = sorted(set(PRIMARY_KEY) & set(_NOT_DIFFED))
    assert not both, both


def test_organisation_member_really_is_written_only_at_seed():
    """The exemption above is a claim about the code, so it is checked against the code.

    `admit` and `expel` are in the organisation design note's effect vocabulary and neither
    is built. The day one is, membership starts moving during play and this table has to
    join the diff pipeline -- and this test is what says so at the time rather than after a
    coup that a rewind failed to undo.
    """
    writes = re.compile(
        r"\b(?:INSERT\s+INTO|INSERT\s+OR\s+\w+\s+INTO|UPDATE|DELETE\s+FROM)\s+"
        r"organisation_member\b", re.IGNORECASE)
    culprits = [
        path.relative_to(_SOURCE).as_posix()
        for path in sorted(_SOURCE.rglob("*.py"))
        if writes.search(path.read_text(encoding="utf-8"))
    ]
    assert culprits == ["scenarios/seeder.py"], (
        f"membership is written outside the seeder now, so it is state a rewind has to "
        f"restore: {culprits}"
    )
