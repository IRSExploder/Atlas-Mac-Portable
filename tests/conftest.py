"""Shared fixtures.

Worlds are built in memory. The isolation policy forbids a live database inside the
project tree, and building on disk would be slower for no benefit -- these tests are
about logic, not about SQLite's file handling.

**Saved presets and workshop overlays are the exception, and they are redirected below.**
Those two are real files by design -- the point of a clone is that it survives the app
being closed -- so the tests that exercise them wrote into the user's actual
``%LOCALAPPDATA%\\Atlas`` tree. See :func:`_saved_scenarios_are_per_run`.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.scenarios import presets as presets_module
from atlas.scenarios import workshop as workshop_module
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, Role, Visibility


@pytest.fixture(scope="session", autouse=True)
def _saved_scenarios_are_per_run(tmp_path_factory):
    r"""Give this run its own presets and overlay directories.

    **Two suites could not run on one machine at the same time.** A saved preset and its
    overlay are two real files under ``%LOCALAPPDATA%\Atlas``, named for the preset id, and
    the tests that exercise them use fixed ids -- ``test-workshop-panel``, ``test-detente``,
    ``test-world``. One process's teardown therefore deleted another process's clone
    mid-test, and the victim failed on an edit it had definitely written:

        assert (4, 'sea') == (40, 'triad')

    which reads exactly like a real bug in the workshop panel and is not one. That is two
    worktrees running `pytest`, or -- more often, because it takes half an hour and nobody
    watches it -- one worktree running `atlas release-audit` while another runs anything at
    all. It cost three separate false 7/8 verdicts and two forty-minute blind re-runs
    before anybody looked at the process list.

    `test_endgame` already redirected `presets_root` per test and was the only file that
    could not be hurt, which is the shape of the answer: the redirect belongs here, where
    it covers every test rather than the one whose author happened to think of it.

    Session-scoped because the collision is *between* processes -- one directory per run is
    enough, and a directory per test would be five thousand of them. Within a run the tests
    still clean up after themselves, as they always did.

    `presets_root` and `workshop_root` are the only doors to either location, so patching
    the two of them covers every reader and writer in the package. Anything that imports
    either *by name* keeps its own binding and escapes this -- there is one such import, in
    `test_a_scenario_can_be_cloned_and_edited_as_data`, and it is written to go through the
    module for that reason.
    """
    root = tmp_path_factory.mktemp("saved-scenarios")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(presets_module, "presets_root", lambda: root / "presets")
        patch.setattr(workshop_module, "workshop_root", lambda: root / "workshop")
        yield root


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    yield connection
    connection.close()


@pytest.fixture
def genesis(conn: sqlite3.Connection) -> int:
    """The root event every turn-0 diff can legally cite."""
    return conn.execute(
        "SELECT id FROM event WHERE world_id = 'sandbox' AND kind = 'genesis'"
    ).fetchone()["id"]


@pytest.fixture
def batch_factory(genesis: int):
    """Build a batch with sensible defaults so each test states only what it is testing."""

    def make(
        *diffs: Diff,
        role: Role = Role.WORLD_SIM,
        nation: str | None = None,
        turn: int = 0,
        events: tuple[dict, ...] = (),
    ) -> DiffBatch:
        resolved = tuple(
            d if d.reason_event_id is not None or d.field == "__nocite__" else
            Diff(
                target_table=d.target_table,
                target_id=d.target_id,
                field=d.field,
                op=d.op,
                new_value=d.new_value,
                expected_prior=d.expected_prior,
                reason_event_id=genesis,
                visibility_scope=d.visibility_scope,
            )
            for d in diffs
        )
        return DiffBatch(
            world_id="sandbox",
            turn=turn,
            proposer_role=role,
            proposer_nation_id=nation,
            proposer_model="mock",
            diffs=resolved,
            events=events,
        )

    return make


def diff(
    table: str,
    target: str,
    field: str,
    value,
    *,
    op: DiffOp = DiffOp.SET,
    cite: int | None = None,
    prior=None,
    scope: Visibility = Visibility.PUBLIC,
) -> Diff:
    """Terse diff constructor for tests."""
    return Diff(
        target_table=table,
        target_id=target,
        field=field,
        op=op,
        new_value=value,
        expected_prior=prior,
        reason_event_id=cite,
        visibility_scope=scope,
    )


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch):
    """Retry backoff is real seconds; the tests are about the count, not the wait.

    Without this, one scripted timeout in a tracked-agent test spends thirty seconds
    sleeping to prove that ``asyncio.sleep`` sleeps.
    """
    monkeypatch.setattr("atlas.engine.turn_engine._RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr("atlas.providers.attempts.RETRY_DELAYS", (0.0, 0.0))
