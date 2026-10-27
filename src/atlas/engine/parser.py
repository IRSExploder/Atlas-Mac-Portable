"""Turning free-text orders into validated mechanical actions.

The gate between prose and mechanics. "Mobilise the northern army and warn Karthos" has to
become something the engine can act on -- and, just as importantly, an order that cannot be
carried out has to come back with a reason the player can act on, *before* they spend a
jump on it.

**It validates, it does not translate.** Does this nation actually hold those units? Is the
destination reachable? Is the target contactable? A parser that faithfully converts an
impossible order into a well-formed action has done the harder half of its job and skipped
the useful half.

**It never decides outcomes.** The schema can express "attempt to move these units there",
never "and they arrive" or "and they win". Whether an attempt succeeds is resolved later,
by the engine, from the seeded stream.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..providers.base import ModelProvider, ProviderRequest
from ..state.jsonschema import make_strict
from ..state.models import Role
from .capabilities import action_types
from .routing import Router
from .views import build_parser_view

__all__ = ["ACTION_SCHEMA", "ParsedOrders", "parse_orders"]

def _peace_term_schema() -> dict[str, Any]:
    """One clause of a settlement, in the shape every other door uses.

    Imported rather than restated: the tracked-agent channel, the simulator's declarations
    and this all describe a term the same way, so a peace offered by a model, by the
    simulator or by a human is the same object by the time the engine reviews it.
    """
    from .peace import term_schema

    return term_schema()


#: What the parser may return. Note what is absent: no outcome, no success flag, no
#: resulting state. An action is an *attempt*.
_ACTION_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions", "rejected"],
    "properties": {
        "actions": {
            "type": "array",
            "description": "Orders that can actually be attempted.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "summary"],
                "properties": {
                    "type": {
                        "type": "string",
                        # **Generated from `atlas.engine.capabilities`, never listed here.**
                        # This enum, the council's list and the orders dispatch table were
                        # three hand-kept copies of one fact, and a mechanic could exist in
                        # the engine while being absent from the vocabulary the parser was
                        # given -- which is how research became unorderable.
                        "enum": list(action_types()),
                    },
                    "summary": {
                        "type": "string",
                        "description": "One clause describing the attempt, in plain language.",
                    },
                    "target_nation": {"type": ["string", "null"]},
                    "unit_kind": {
                        "type": ["string", "null"],
                        "description": (
                            "For raise_units and disband_units: which kind of formation, "
                            "e.g. infantry, armour, battleship, carrier, transport."
                        ),
                    },
                    "project": {
                        "type": ["string", "null"],
                        "description": (
                            "For build_project: which public work or research programme "
                            "to begin. Use an id from the menu in the prompt -- it lists "
                            "both, and research ids (welding, radio, radar) are as valid "
                            "here as civil ones (housing, rail, health, schools, "
                            "land_reform)."
                        ),
                    },
                    "gambit_kind": {
                        "type": ["string", "null"],
                        "description": (
                            "For gambit: which undertaking. One of sabotage (wreck "
                            "something of theirs), air_strike (bomb a region), sting, "
                            "bribe, incite, expose, summit, envoy. Null otherwise."
                        ),
                    },
                    "ceasefire": {
                        "type": ["boolean", "null"],
                        "description": (
                            "For sue_for_peace: true to ask for a temporary ceasefire "
                            "rather than a settlement. A ceasefire stops the fighting for "
                            "a few months, settles nothing, and can be broken."
                        ),
                    },
                    "terms": {
                        "type": "array",
                        "maxItems": 8,
                        "description": (
                            "For sue_for_peace: what the settlement says. Each term names "
                            "a kind, which side carries it out, and the province or amount "
                            "it is about. Empty means an unconditional end to the fighting."
                        ),
                        "items": _peace_term_schema(),
                    },
                    "doctrine": {
                        "type": ["string", "null"],
                        "description": (
                            "For adopt_doctrine: which stance to change to. One word, "
                            "because a stance names its own question -- 'mass' or "
                            "'professional' for how the army is raised, 'retaliation' or "
                            "'minimum' for what the warheads are for, 'collective_security' "
                            "or 'neutrality' for who the country stands with."
                        ),
                    },
                    "from_region": {"type": ["string", "null"]},
                    "to_region": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    # No null member: agy rejects any enum containing null. The model
                    # picks a tone explicitly, which is better than a silent default.
                    "tone": {
                        "type": "string",
                        "enum": ["friendly", "neutral", "firm", "threatening"],
                    },
                },
            },
        },
        "rejected": {
            "type": "array",
            "description": "Orders that cannot be carried out, each with a reason the player can act on.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["order", "reason"],
                "properties": {
                    "order": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "description": "Why this is impossible, in one sentence. Be specific: name the missing units or the unreachable region.",
                    },
                },
            },
        },
    },
}

#: Codex uses OpenAI strict structured output, which demands every property appear in
#: `required`. A schema written the natural way works for Claude and agy and is rejected
#: by Codex mid-turn, after the call is already paid for. See atlas.state.jsonschema.
ACTION_SCHEMA: dict[str, Any] = make_strict(_ACTION_SCHEMA_RAW)


@dataclass(frozen=True, slots=True)
class ParsedOrders:
    actions: tuple[dict, ...] = ()
    rejected: tuple[dict, ...] = ()
    #: Set when the parser itself could not be reached, as opposed to rejecting an order.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def summaries(self) -> list[str]:
        return [str(action.get("summary", "")) for action in self.actions]

    @property
    def rejection_lines(self) -> list[str]:
        return [f"{r.get('order', '')} - {r.get('reason', '')}" for r in self.rejected]


async def parse_orders(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    raw_text: str,
    providers: dict[str, ModelProvider],
    router: Router,
) -> ParsedOrders:
    """Parse and validate a player's orders. Cheap, bounded, and schema-enforced."""
    if not raw_text.strip():
        return ParsedOrders()

    from . import bots

    if bots.controls_role("parser"):
        # The same answer shape, so the audit row and every gate downstream are unchanged.
        from .staff_bots import parse_orders as bot_parse

        data = bot_parse(conn, world_id, nation_id=nation_id, raw_text=raw_text)
    else:
        decision = router.route(Role.PARSER, 0.0)
        provider = providers.get(decision.provider_key)
        if provider is None or not provider.available():
            return ParsedOrders(
                error=f"{decision.provider_key} is unavailable, so orders cannot be checked."
            )

        view = build_parser_view(conn, world_id, nation_id=nation_id, raw_text=raw_text)
        result = await provider.invoke(
            ProviderRequest(
                role=Role.PARSER,
                prompt=view.text,
                model=decision.model,
                schema=ACTION_SCHEMA,
                timeout_s=120.0,
                # A bounded translation task. The default effort for this CLI is xhigh, which
                # would cost more than the world simulation it feeds.
                options={"reasoning_effort": "low"},
            )
        )

        if result.failed:
            return ParsedOrders(
                error=result.failure.message if result.failure else "your staff could not be reached"
            )
        if not isinstance(result.data, dict):
            return ParsedOrders(
                error="your staff sent back something that was not a list of orders")
        data = result.data

    # Persist the attempt so the audit trail shows what the player asked for and what the
    # parser made of it, separately from what the world then did.
    turn = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)
    ).fetchone()["turn_number"]
    actions = tuple(data.get("actions") or ())
    rejected = tuple(data.get("rejected") or ())

    import json

    conn.execute(
        """
        INSERT INTO action (world_id, turn, nation_id, raw_text, parsed_json,
                            validation_status, rejection_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            world_id,
            turn,
            nation_id,
            raw_text,
            json.dumps({"actions": actions, "rejected": rejected}),
            "accepted" if actions else "rejected",
            "; ".join(str(r.get("reason", "")) for r in rejected)[:1000] or None,
        ),
    )
    conn.commit()

    return ParsedOrders(actions=actions, rejected=rejected)
