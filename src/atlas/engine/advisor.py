"""The player's council: advice, and nothing else.

The one model-facing role that cannot change the world. That is enforced three times
over rather than asked for: the role's write allowlist is empty
(:mod:`atlas.state.permissions`), no schema is ever attached to the call so there is no
shape in which a diff could be returned, and this module has no writer -- there is
deliberately no ``close()`` here, no extraction step, and no counterpart to
:func:`atlas.engine.diplomacy.extract_commitments`. A conversation with your own
ministers leaves no trace on the world, which is what makes it free to have.

**It is not a per-country agent.** Those act on their own account, hold their own goals,
and propose diffs through the validation layer; the advisor holds the *player's* goals
and proposes sentences. Same provider as diplomacy -- reading a strategic position and
holding one under pressure are the same skill -- but a different role, a different view,
and no write path at all.

**A conversation is not memory.** As everywhere else, each reply is a fresh stateless
call carrying the transcript back in as data. The council remembers nothing between
calls; the transcript is the memory, and it lives in the session rather than in a
model's context window.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..providers.base import ModelProvider, ProviderRequest
from ..state.models import Role
from .diplomacy import ChatTurn
from .routing import Router, log_decision
from .views import build_advisor_view

__all__ = ["AdvisorSession"]


@dataclass(slots=True)
class AdvisorSession:
    """One private conversation between the player and their own government."""

    conn: sqlite3.Connection
    world_id: str
    player_nation: str
    providers: dict[str, ModelProvider]
    router: Router
    transcript: list[ChatTurn] = field(default_factory=list)

    #: What the council is called in the transcript and in the UI.
    speaker: str = "Your council"

    def _turn(self) -> int:
        row = self.conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (self.world_id,)
        ).fetchone()
        return int(row["turn_number"]) if row else 0

    async def ask(self, question: str) -> ChatTurn:
        """Put a question to the council and get its answer.

        Returns a turn rather than raising on failure, for the same reason the rest of
        the engine does: an unreachable provider should show up in the conversation as
        an unanswered question, not as a traceback in the middle of a game.
        """
        self.transcript.append(ChatTurn(self.player_nation, question, is_player=True))

        from . import bots

        if bots.controls_role("advisor"):
            from .staff_bots import advise

            reply = ChatTurn(self.speaker, advise(
                self.conn, self.world_id, nation_id=self.player_nation, question=question))
            self.transcript.append(reply)
            return reply

        decision = self.router.route(Role.ADVISOR, 0.0)
        provider = self.providers.get(decision.provider_key)
        if provider is None or not provider.available():
            reply = ChatTurn(
                self.speaker,
                f"[Your council cannot be reached: {decision.provider_key} is unavailable.]",
            )
            self.transcript.append(reply)
            return reply

        # Built from the player's own visibility. An advisor briefed from the referee's
        # view would launder rivals' exact strength and secret intent into a
        # conversation the player is allowed to have for free.
        view = build_advisor_view(
            self.conn,
            self.world_id,
            nation_id=self.player_nation,
            question=question,
            # The question is appended to the view by the builder, so the transcript
            # passed here is the history *before* it.
            conversation=[(t.speaker, t.text) for t in self.transcript[:-1]],
        )

        result = await provider.invoke(
            ProviderRequest(
                role=Role.ADVISOR,
                prompt=view.text,
                model=decision.model,
                # No schema, deliberately. There is no structured answer to ask for,
                # and attaching one would create a shape in which a diff could arrive.
                timeout_s=180.0,
            )
        )

        # Logged like every other call, so the advisor shows up in the routing audit and
        # in usage accounting rather than being invisible spend.
        log_decision(
            self.conn,
            self.world_id,
            self._turn(),
            decision,
            served_model=result.audit.served if result.audit else None,
            audit_verdict=result.audit.verdict.value if result.audit else None,
            duration_ms=int(result.duration_s * 1000),
            outcome="ok" if result.ok else "failed",
        )
        self.conn.commit()

        text = (
            result.text.strip()
            if result.ok
            else f"[no answer: {result.failure.message if result.failure else 'unknown error'}]"
        )
        reply = ChatTurn(self.speaker, text)
        self.transcript.append(reply)
        return reply
