"""Recorded model text, played into the live window. A development tool, never a game mode.

**What it is for.** Under ``--mock`` the world-sim narrative is empty, the council speaks in
one canned line and every diplomatic reply is "We will consider your proposal." That is
enough to drive a menu and not enough to lay out a headline, size a dispatch panel, or
see what a page of real diplomacy does to a chat column. The cassettes already hold
several hundred real answers from frontier models; this provider lets the window show
them without spending a single call.

**Three modes, and the default is the one that works on any world.**

* :attr:`ReplayMode.TEXT` -- *prose only*. Each prose-bearing role (world-sim narrative
  and events, the council, diplomacy replies) is answered with the next recorded answer
  for that role, cycling when the tape runs out. Everything structured is stripped: a
  world-sim answer keeps its narrative and its events' text and loses its diffs,
  contests and declarations, and its events lose the actor, audience and causes that
  name rows in *another* world. So a 1972 tape can narrate a 1936 game -- the words will
  be about the wrong war, which is the point: they are layout copy, not mechanics. Every
  other role (parser, tracked agents, gambits, organisations, freeform) gets the mock's
  schema-shaped default, which is what ``--mock`` already gives them. Pair it with
  ``ATLAS_BOTS`` for a world that moves.
* :attr:`ReplayMode.LOOSE` -- every role replayed whole, matched by role in recorded order
  (``cassette.MatchMode.LOOSE``). For replaying a tape against the world it was recorded
  in, or deliberately stressing the gates with answers meant for a different state.
* :attr:`ReplayMode.EXACT` -- the cassette's own exact-digest match. Only useful against
  the very world the tape was built from.

**A miss is never fatal here,** unlike in :class:`~atlas.providers.cassette.ReplayProvider`
used as a regression fixture. A window is interactive: the player does things the tape
never saw, so in LOOSE and EXACT an unmatched call falls through to the mock's default
and is counted, never raised. The counts are on :attr:`DevReplayProvider.served` so a
Diagnostics line or a test can say how much of a session was real text.

**It is never constructed unless asked for** (``atlas play --replay``), and it never writes
anything: a cassette is read-only input here.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..state.models import Provider, Role
from .base import AuditVerdict, ModelAudit, ProviderRequest, ProviderResult
from .cassette import Entry, MatchMode, ReplayProvider, read_cassette
from .mock import MockProvider

__all__ = ["DevReplayProvider", "ReplayMode", "PROSE_ROLES", "prose_of"]


class ReplayMode(StrEnum):
    TEXT = "text"
    LOOSE = "loose"
    EXACT = "exact"


#: The roles whose recorded answer carries prose worth showing. Everything else is
#: structured orders, which mean nothing outside the world that produced them.
PROSE_ROLES: frozenset[Role] = frozenset({Role.WORLD_SIM, Role.ADVISOR, Role.DIPLOMACY})


def _decoded(entry: Entry) -> Any:
    """The recorded answer as an object, whichever of the two fields carried it."""
    if isinstance(entry.data, dict):
        return entry.data
    try:
        return json.loads(entry.text) if entry.text else None
    except ValueError:
        return None


def prose_of(entry: Entry, request: ProviderRequest) -> tuple[str, Any] | None:
    """A recorded answer reduced to its prose, shaped for *this* request, or ``None``.

    For a schema-less request (council, diplomacy reply) that is the recorded text. For a
    world-sim request it is a batch with no mechanics in it: the narrative, plus each
    event's kind and text as an anonymous public event -- and only if the request's own
    schema still asks for events, so a request built without them is not handed a field
    it would refuse.
    """
    if request.schema is None:
        if not entry.ok or not entry.text.strip():
            return None
        return entry.text, None
    if request.role is not Role.WORLD_SIM:
        return None
    answer = _decoded(entry)
    if not isinstance(answer, dict):
        return None
    properties = request.schema.get("properties", {})
    batch: dict[str, Any] = {"diffs": []}
    if "narrative" in properties:
        batch["narrative"] = str(answer.get("narrative") or "")
    if "events" in properties:
        allowed = set(properties["events"]["items"]["properties"]["kind"].get("enum", ()))
        events = []
        for raw in answer.get("events") or ():
            if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
                continue
            kind = raw.get("kind") if raw.get("kind") in allowed else "narrative"
            events.append({"kind": kind, "text": str(raw["text"]),
                           "actor_nation_id": None, "visibility": "public"})
        batch["events"] = events
    for key in ("contests", "declarations"):
        if key in properties:
            batch[key] = []
    if not batch.get("narrative") and not batch.get("events"):
        return None
    return json.dumps(batch), batch


@dataclass(slots=True)
class DevReplayProvider:
    """Recorded answers where there are some, the mock's defaults everywhere else."""

    entries: list[Entry]
    mode: ReplayMode = ReplayMode.TEXT
    name: Provider = Provider.MOCK
    fallback: MockProvider = field(default_factory=MockProvider)
    #: ``(role, "replayed" | "fallback")`` -> count. What share of the session was real text.
    served: Counter = field(default_factory=Counter)
    _by_role: dict[str, list[Entry]] = field(default_factory=dict, init=False, repr=False)
    _cursor: Counter = field(default_factory=Counter, init=False, repr=False)
    _replay: ReplayProvider | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.mode = ReplayMode(self.mode)
        if self.mode is ReplayMode.TEXT:
            by_role: dict[str, list[Entry]] = {}
            for entry in sorted(self.entries, key=lambda e: e.seq):
                if entry.ok:
                    by_role.setdefault(entry.role, []).append(entry)
            self._by_role = by_role
        else:
            self._replay = ReplayProvider(
                list(self.entries),
                mode=MatchMode.LOOSE if self.mode is ReplayMode.LOOSE else MatchMode.EXACT,
                strict=False,
            )

    @classmethod
    def from_file(cls, path: Path | str, mode: ReplayMode | str = ReplayMode.TEXT
                  ) -> DevReplayProvider:
        return cls(read_cassette(Path(path)), mode=ReplayMode(mode))

    def available(self) -> bool:
        return True

    def roles(self) -> dict[str, int]:
        """How many recorded answers each role has, for the startup line."""
        counts: Counter = Counter(entry.role for entry in self.entries if entry.ok)
        return dict(sorted(counts.items()))

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        if self.mode is ReplayMode.TEXT:
            result = self._prose(request)
        else:
            assert self._replay is not None
            missed = len(self._replay.missed)
            result = await self._replay.invoke(request)
            if len(self._replay.missed) > missed:
                result = None
        if result is None:
            self.served[(request.role.value, "fallback")] += 1
            return await self.fallback.invoke(request)
        self.served[(request.role.value, "replayed")] += 1
        return result

    def _prose(self, request: ProviderRequest) -> ProviderResult | None:
        if request.role not in PROSE_ROLES:
            return None
        pool = self._by_role.get(request.role.value) or []
        # Walk at most once round the pool: a tape whose every answer for this role is
        # unusable here falls through to the mock rather than spinning.
        for _ in range(len(pool)):
            entry = pool[self._cursor[request.role.value] % len(pool)]
            self._cursor[request.role.value] += 1
            shaped = prose_of(entry, request)
            if shaped is None:
                continue
            text, data = shaped
            return ProviderResult(
                provider=self.name, role=request.role, ok=True, text=text, data=data,
                audit=ModelAudit(request.model, request.model, AuditVerdict.MATCH),
            )
        return None
