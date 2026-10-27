"""Reporting what a turn is doing while it does it.

A year-long jump with five tracked nations is six provider calls and several minutes. A
single spinner for all of it tells the player nothing about whether it is working, which
call is slow, or what they lose by cancelling. The design shows each stage with its own
state and timing, so the engine has to say what it is doing rather than only what it did.

Deliberately a plain callback rather than a Qt signal: the engine runs on the asyncio
thread and must not import Qt. The UI adapts it at the boundary.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["ProgressReporter", "Stage", "StageState", "StageUpdate"]


class StageState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageUpdate:
    """One stage of a turn, as it currently stands."""

    key: str
    label: str
    state: StageState
    detail: str = ""
    duration_s: float | None = None
    #: Which attempt at this stage is currently running. 1 for every stage that worked
    #: first time. A *number* rather than a message because the view that renders it has
    #: to say so in its own words -- see :mod:`atlas.ui.turning`.
    attempt: int = 1

    @property
    def timing(self) -> str:
        if self.duration_s is None:
            return ""
        return f"{self.duration_s:.1f}s"


@dataclass(slots=True)
class Stage:
    """A stage being tracked, with its own clock."""

    key: str
    label: str
    detail: str = ""
    state: StageState = StageState.PENDING
    started: float | None = None
    duration_s: float | None = None
    attempt: int = 1

    def snapshot(self) -> StageUpdate:
        elapsed = self.duration_s
        if elapsed is None and self.started is not None and self.state is StageState.RUNNING:
            elapsed = time.monotonic() - self.started
        return StageUpdate(
            key=self.key, label=self.label, state=self.state,
            detail=self.detail, duration_s=elapsed, attempt=self.attempt,
        )


@dataclass(slots=True)
class ProgressReporter:
    """Collects stages and pushes the whole list on every change.

    The whole list rather than a delta: the modal renders all stages at once, and sending
    the complete picture removes any chance of the display and the engine disagreeing
    about what has finished.
    """

    on_update: Callable[[list[StageUpdate]], None] | None = None
    stages: list[Stage] = field(default_factory=list)

    def plan(self, entries: list[tuple[str, str, str]]) -> None:
        """Declare the stages up front, so the player sees the shape of the whole turn."""
        self.stages = [Stage(key=key, label=label, detail=detail) for key, label, detail in entries]
        self._emit()

    def _find(self, key: str) -> Stage | None:
        return next((stage for stage in self.stages if stage.key == key), None)

    def add(self, key: str, label: str, detail: str = "") -> None:
        """Append a stage discovered mid-turn, such as one tracked nation per call."""
        if self._find(key) is None:
            self.stages.append(Stage(key=key, label=label, detail=detail))
            self._emit()

    def start(self, key: str, detail: str | None = None) -> None:
        stage = self._find(key)
        if stage is None:
            return
        stage.state = StageState.RUNNING
        stage.started = time.monotonic()
        stage.attempt = 1
        if detail is not None:
            stage.detail = detail
        self._emit()

    def retry(self, key: str) -> None:
        """This stage is being attempted again.

        A count rather than a sentence. The reason a stage is on its second attempt is a
        fact about the machinery, and the machinery is precisely what the view over this
        must not put in front of the player -- so the reporter says *how many times*, and
        the view says what that means in the world.
        """
        stage = self._find(key)
        if stage is None:
            return
        stage.attempt += 1
        # The clock is not reset: the player is waiting on the stage, not on the attempt,
        # and restarting it would make a stage that has taken four minutes look fresh.
        self._emit()

    def finish(
        self, key: str, *, state: StageState = StageState.DONE, detail: str | None = None
    ) -> None:
        stage = self._find(key)
        if stage is None:
            return
        stage.state = state
        if stage.started is not None:
            stage.duration_s = time.monotonic() - stage.started
        if detail is not None:
            stage.detail = detail
        self._emit()

    def snapshot(self) -> list[StageUpdate]:
        return [stage.snapshot() for stage in self.stages]

    def _emit(self) -> None:
        if self.on_update is None:
            return
        # A listener that raises must never take the turn down with it: progress is
        # decoration, and the simulation running is not.
        with contextlib.suppress(Exception):
            self.on_update(self.snapshot())


# ---------------------------------------------------------------------------
# How long the next one will take
# ---------------------------------------------------------------------------

#: How many recent turns the estimate is taken over. Enough that one slow turn does not
#: dominate, few enough that a provider change last week is not still being averaged in.
RECENT_TURNS = 8


def observed_turn_seconds(
    conn, world_id: str, *, recent: int = RECENT_TURNS
) -> float | None:
    """The typical wall-clock cost of one turn in **this** world, or ``None`` if unknown.

    **Measured, not modelled.** `routing_log` already records `duration_ms` for every
    provider call with the turn it was made on, so the honest estimate is the median of what
    the last few turns actually cost -- which needs no opinion about how many calls a turn
    makes, how many nations are tracked, or which models are pinned. All three change, and
    a model of them would be a second thing to keep in step with the router.

    The **median** rather than the mean: one turn where a provider timed out and retried is
    a real turn, and it should not double the estimate shown before every subsequent one.

    ``None`` where there is nothing to go on -- a fresh world, or one played entirely
    offline -- because a number invented for a progress bar is still a number the player
    will believe.
    """
    rows = conn.execute(
        "SELECT turn, SUM(duration_ms) AS spent FROM routing_log"
        " WHERE world_id = ? AND duration_ms IS NOT NULL"
        " GROUP BY turn ORDER BY turn DESC LIMIT ?",
        (world_id, max(1, int(recent))),
    ).fetchall()
    spent = sorted(float(row["spent"] or 0.0) for row in rows if row["spent"])
    if not spent:
        return None
    middle = len(spent) // 2
    median = (spent[middle] if len(spent) % 2
              else (spent[middle - 1] + spent[middle]) / 2.0)
    return median / 1000.0


def estimated_advance(conn, world_id: str, *, turns: int) -> str:
    """What to tell the player before a jump of ``turns``, in words rather than a bar.

    Deliberately vague at the top end. Beyond a couple of minutes the honest thing to say
    is "a few minutes", because the spread between a quiet turn and one with six nations
    thinking is wider than any single figure would admit -- and a precise estimate that is
    wrong by a factor of two is worse than an approximate one that is not.
    """
    each = observed_turn_seconds(conn, world_id)
    if each is None:
        return ""
    total = each * max(1, int(turns))
    if total < 20:
        return "about a quarter of a minute"
    if total < 90:
        return f"about {round(total / 15) * 15:.0f} seconds"
    minutes = total / 60.0
    if minutes < 10:
        return f"about {minutes:.0f} minute{'s' if round(minutes) != 1 else ''}"
    return f"upwards of {int(minutes // 5) * 5} minutes"
