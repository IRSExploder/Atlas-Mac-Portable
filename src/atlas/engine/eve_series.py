"""EVE Phase B: several matches, one statement per provider.

A single EVE match is an anecdote, and :mod:`atlas.engine.statistics` labels it one. The
only way past that label is repetition, and repetition only means something if it is
designed. This module is the design; the statistics module is the arithmetic.

**Three decisions, each of which a naive loop gets wrong.** All three are Alex's to
revisit (2026-09-18); the reasoning is here so that he can.

1. **The seats rotate.** On a curated era the three blocs are not equal: the Western Bloc
   opens with the United States, the Third Force with China, France and North Vietnam.
   Pin Claude to the West for ten matches and "Claude" is measured together with the
   West's opening, and no number of matches separates the two. So the pins are dealt as a
   Latin square -- match *i* gives seat *k* the pin *(k + i) mod 3* -- and after every
   multiple of three matches each provider has played each seat exactly once as often as
   any other. :func:`atlas.engine.statistics.summarise` flags any provider for which that
   is not true. *Alternative:* a fixed seating, which measures bloc-plus-provider and is
   the right design only when the question is about one bloc.

2. **The dice are the same in every match.** In-play randomness is
   :func:`atlas.sim.rng.derive` of the world's seed, its *id*, the turn and a call key, so
   every match here is built with the same seed and the same world id, in its own
   database. Two matches then differ only where the models' choices differ -- which is
   the premise the statistics module's docstring already states and nothing enforced.
   *Alternative:* a fresh world id per match, which also samples the dice; that answers
   "how does this provider do across fortunes" rather than "what does this provider
   choose", and needs far more matches to separate the two.

3. **A match is a fresh world.** Nothing carries from one match to the next: each is
   built by the caller's ``open_world`` and closed here. A series that continued one
   world would make every match after the first depend on the one before.

Nothing here talks to a model directly -- the providers are handed in -- so the tests run
it against :class:`atlas.providers.mock.MockProvider` with different scripted behaviour
per provider, and ``atlas eve --matches N --mock`` does the same from the command line.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from ..providers.base import ModelProvider
from .eve import Bloc, BlocConflict, EveEngine, EveRound, assign_blocs
from .routing import Router
from .statistics import MatchStats, collect_match_stats, summarise
from .turn_engine import TurnEngine

__all__ = ["SeriesResult", "rotated_pins", "run_series"]

#: A pin table as :data:`atlas.providers.cli_provider.EVE_BLOC_MODELS` holds it: seat
#: label to ``(provider key, model)``.
Pins = dict[str, tuple[str, str]]


def rotated_pins(pins: Pins, match: int) -> Pins:
    """The pin table for match ``match``: seat ``k`` gets pin ``(k + match) mod n``.

    Labels stay where they are -- a label is a seat -- and the pins move under them, so
    over ``n`` consecutive matches every pin sits in every seat once.
    """
    labels = list(pins)
    values = list(pins.values())
    n = len(values)
    return {label: values[(index + match) % n] for index, label in enumerate(labels)}


@dataclass(slots=True)
class SeriesResult:
    runs: list[MatchStats] = field(default_factory=list)
    #: Per match, who sat where: ``(seat name, provider key, model)`` in bloc order.
    seatings: list[tuple[tuple[str, str, str], ...]] = field(default_factory=list)
    #: Matches that could not be set up, with the reason, rather than silently dropped.
    skipped: list[str] = field(default_factory=list)

    @property
    def summary(self) -> MatchStats:
        return summarise(self.runs)

    def describe(self) -> str:
        lines = [f"EVE series: {len(self.runs)} match(es)"
                 + (f", {len(self.skipped)} skipped" if self.skipped else "")]
        for index, seating in enumerate(self.seatings):
            lines.append(f"  match {index + 1}: " + "; ".join(
                f"{seat} = {model} ({provider})" for seat, provider, model in seating))
        lines.extend(f"  skipped: {reason}" for reason in self.skipped)
        lines.append("")
        lines.append(self.summary.describe())
        return "\n".join(lines)


async def run_series(  # noqa: PLR0913 - a series is configured by all of these
    open_world: Callable[[int], sqlite3.Connection],
    world_id: str,
    providers: dict[str, ModelProvider],
    pins: Pins,
    blocs_for: Callable[[sqlite3.Connection, str, Pins], list[Bloc] | None],
    *,
    matches: int,
    rounds: int,
    days: int,
    router: Router,
    on_round: Callable[[int, EveRound], None] | None = None,
    require_attestation: bool = False,
) -> SeriesResult:
    """Play ``matches`` matches of ``rounds`` rounds each and collect their statistics.

    ``open_world(i)`` returns a fresh connection to match *i*'s own world, built with the
    same seed and world id as every other match (see the module docstring); the series
    closes it. ``blocs_for`` is the roster rule -- ``atlas.cli._eve_blocs`` from the
    command line -- given the rotated pins for that match.
    """
    result = SeriesResult()
    for match in range(matches):
        conn = open_world(match)
        try:
            try:
                blocs = blocs_for(conn, world_id, rotated_pins(pins, match))
                if blocs is None:
                    result.skipped.append(
                        f"match {match + 1}: not enough nations for three blocs")
                    continue
                assign_blocs(conn, world_id, blocs)
            except BlocConflict as exc:
                # Reported, and the series goes on: losing every finished match to one
                # refused seating would throw away the only thing a series is for.
                result.skipped.append(f"match {match + 1}: seating refused: {exc}")
                continue
            result.seatings.append(
                tuple((bloc.name, bloc.provider_key, bloc.model) for bloc in blocs))
            eve = EveEngine(conn=conn, world_id=world_id, providers=providers, router=router,
                            require_attestation=require_attestation, days=days)
            turns = TurnEngine(conn=conn, world_id=world_id, providers=providers,
                               router=router)
            for _round in range(rounds):
                report = await eve.run_round()
                if on_round is not None:
                    on_round(match, report)
                await turns.jump(days=days)
            result.runs.append(collect_match_stats(conn, world_id))
        finally:
            conn.close()
    return result
