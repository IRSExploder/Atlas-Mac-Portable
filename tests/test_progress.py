"""Stage reporting: the engine saying what it is doing while it does it.

The reporter is deliberately a plain callback rather than a Qt signal -- the engine runs
on the asyncio thread and must not import Qt -- so it is testable without a display, and
these tests run entirely headless.
"""

from __future__ import annotations

import pytest

from atlas.engine.progress import ProgressReporter, Stage, StageState, StageUpdate


def test_plan_declares_the_shape_of_the_turn():
    seen: list[list[StageUpdate]] = []
    reporter = ProgressReporter(on_update=seen.append)
    reporter.plan([("world_sim", "The world moves", "referee"), ("validate", "Checking", "")])

    assert [stage.key for stage in seen[-1]] == ["world_sim", "validate"]
    assert all(stage.state is StageState.PENDING for stage in seen[-1])


def test_the_whole_list_is_sent_on_every_change():
    """The modal renders all stages at once; sending deltas invites disagreement."""
    seen: list[list[StageUpdate]] = []
    reporter = ProgressReporter(on_update=seen.append)
    reporter.plan([("a", "A", ""), ("b", "B", "")])
    reporter.start("a")
    reporter.finish("a")

    assert all(len(update) == 2 for update in seen)


def test_a_finished_stage_carries_its_timing():
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    reporter.start("a")
    reporter.finish("a")

    stage = reporter.snapshot()[0]
    assert stage.state is StageState.DONE
    assert stage.duration_s is not None and stage.duration_s >= 0
    assert stage.timing.endswith("s")


def test_a_running_stage_reports_elapsed_time():
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    reporter.start("a")
    assert reporter.snapshot()[0].duration_s is not None


def test_a_pending_stage_has_no_timing():
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    assert reporter.snapshot()[0].timing == ""


def test_stages_discovered_mid_turn_can_be_added():
    """One tracked nation per call, and how many there are is decided during the turn."""
    reporter = ProgressReporter()
    reporter.plan([("world_sim", "The world moves", "")])
    reporter.add("tracked:ita", "Italy acts")
    reporter.add("tracked:ita", "Italy acts")  # idempotent

    assert [stage.key for stage in reporter.snapshot()] == ["world_sim", "tracked:ita"]


def test_failure_is_recorded_with_a_reason():
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    reporter.start("a")
    reporter.finish("a", state=StageState.FAILED, detail="provider timed out")

    stage = reporter.snapshot()[0]
    assert stage.state is StageState.FAILED
    assert stage.detail == "provider timed out"


def test_unknown_keys_are_ignored_rather_than_raising():
    """Progress is decoration; a mismatched key must never take a turn down."""
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    reporter.start("nope")
    reporter.finish("nope")
    assert reporter.snapshot()[0].state is StageState.PENDING


def test_a_listener_that_raises_does_not_break_the_turn():
    def explode(_updates):
        raise RuntimeError("the modal fell over")

    reporter = ProgressReporter(on_update=explode)
    reporter.plan([("a", "A", "")])  # must not raise
    reporter.start("a")
    assert reporter.snapshot()[0].state is StageState.RUNNING


def test_reporter_without_a_listener_is_inert():
    reporter = ProgressReporter()
    reporter.plan([("a", "A", "")])
    reporter.start("a")
    reporter.finish("a")
    assert reporter.snapshot()[0].state is StageState.DONE


def test_stage_snapshot_is_a_value():
    stage = Stage(key="a", label="A")
    first = stage.snapshot()
    stage.state = StageState.DONE
    assert first.state is StageState.PENDING
    with pytest.raises(AttributeError):
        first.state = StageState.DONE  # type: ignore[misc]
