"""The diff -- the only way anything ever changes in an ATLAS world.

Models do not mutate state. They return a :class:`DiffBatch`, which is recorded, then
validated, then either applied or rejected. That single choke point buys four things at
once: a complete audit trail, free rewind, a debuggable answer to "why on earth did
Russia do that", and a place to enforce every anti-cheating rule.

This module is also the single source of truth for the JSON Schema handed to the
providers. All three CLIs enforce a schema natively (``claude --json-schema``,
``codex --output-schema``, ``agy --json-schema``), so shape errors are caught before the
bytes ever reach Python and there is no hand-written parser to drift out of sync.

Two field choices are load-bearing:

``reason_event_id``
    Mandatory. A diff that cannot name the event which caused it is rejected. This is
    what stops ``germany-russia relations +30`` appearing from nowhere, and it is what
    makes the audit log readable when an outcome feels wrong.

``expected_prior``
    Compare-and-swap. A batch is generated against a snapshot of the world; by the time
    it applies, an earlier batch in the same turn may have moved the same field. Without
    a precondition the second write silently clobbers the first. With one, the conflict
    is detected and the phase re-runs against fresh state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .jsonschema import make_strict
from .models import DiffOp, DiffStatus, Role, Visibility

__all__ = ["Diff", "DiffBatch", "diff_batch_schema", "parse_batch"]


@dataclass(frozen=True, slots=True)
class Diff:
    """One proposed field change."""

    target_table: str
    target_id: str
    field: str
    op: DiffOp
    new_value: Any = None
    expected_prior: Any = None
    reason_event_id: int | None = None
    visibility_scope: Visibility = Visibility.PUBLIC
    seq: int = 0
    old_value: Any = None
    status: DiffStatus = DiffStatus.PROPOSED
    rejected_by_rule: str | None = None
    rejection_reason: str | None = None

    def rejected(self, rule_id: str, reason: str) -> Diff:
        from dataclasses import replace

        return replace(
            self, status=DiffStatus.REJECTED, rejected_by_rule=rule_id, rejection_reason=reason
        )


@dataclass(frozen=True, slots=True)
class DiffBatch:
    """A set of diffs proposed by one call, applied all-or-nothing."""

    world_id: str
    turn: int
    proposer_role: Role
    diffs: tuple[Diff, ...] = ()
    proposer_model: str | None = None
    #: The nation this batch speaks for. ``None`` for ``world_sim``, which is a
    #: deliberate omniscient referee rather than a participant -- the information
    #: asymmetry rule skips it for exactly that reason.
    proposer_nation_id: str | None = None
    narrative: str = ""
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: Clashes the simulator observed but did not decide. Each carries evidence flags and
    #: nothing else; :mod:`atlas.sim.resolve` draws the result. This is the only channel
    #: by which territory can change hands, and it exists so that "the model never decides
    #: outcomes" stays true while wars still actually happen.
    contests: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: Wars starting and ending. ``relation.at_war`` is engine-only, so without this the
    #: player was the only power on the map that could enter a state of war, and no war
    #: could ever end.
    declarations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: What a tracked power's staff is trying to do with its army, as
    #: ``{"push": [...], "hold": [...], "withdraw_from": [...]}`` of region ids. Intent,
    #: not a write path: every territory column stays engine-only, the engine checks each
    #: named region against the map and ignores what cannot be reached, and outcomes are
    #: still drawn in :mod:`atlas.sim.resolve`.
    military_objectives: dict[str, Any] = field(default_factory=dict)
    #: What a tracked power wants to *build* this turn, as ``[{"project": id, "region": id?},
    #: ...]``. Intent, not a write path, exactly like ``military_objectives``: the engine
    #: runs each through the same order handler the player uses, which charges the treasury,
    #: occupies a queue slot and delivers on completion, or refuses it for a reason the
    #: power is told. This is what makes a per-country agent build its economy, not just
    #: fight -- without it, only the human ever developed and the AI world stood still.
    build_orders: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: Formations this power wants to raise this turn, as ``[{"unit_kind": kind,
    #: "quantity": n, "region": id?}, ...]``. The military twin of ``build_orders`` and
    #: intent in exactly the same sense: it routes to the `raise_units` handler the player
    #: uses, which charges treasury *and* manpower and refuses what neither will stretch
    #: to. **Without it a tracked power could not replace a single casualty**, so every AI
    #: war was an annihilation race the larger army won by arithmetic -- measured on the
    #: Ukraine front, where the defender's strength fell from 72 to 1.7 without one
    #: replacement while it was still taking ground back.
    raise_formations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: Whether this power is sending replacements to the formations it already has. A
    #: plain yes, because the interesting decision is whether to spend on holding the line
    #: at all -- which division gets the men is a question the engine answers better than a
    #: staff can, from supply and from who is worst off. Bounded by
    #: `REINFORCE_BUDGET_SHARE` so saying yes can never spend a country's last coin.
    reinforce: bool = False
    #: How deep this power wants to dig for soldiers, 0 peacetime to 3 total war. Intent
    #: like every other channel here: the engine moves the footing one step a turn toward
    #: what was asked, charges the revenue and the stability, and writes the column itself.
    mobilisation: int | None = None
    #: Who this power is declaring war on, as ``{"target": id, "justification": str}``.
    #: Intent like every other channel here, and the most heavily gated of them: the engine
    #: checks reach, grounds, capacity and a cooldown before anything happens, and writes
    #: `relation.at_war` itself through the same `_declare_war` the simulator uses.
    declare_war: dict[str, Any] = field(default_factory=dict)
    #: What this power wants its trade delegations to arrange, as ``[{"kind": ..., "good":
    #: ..., "partner": id?}, ...]``. Intent, not a write path, in exactly the sense
    #: ``build_orders`` is: every numeric column on ``trade_route`` stays engine-only, and
    #: :func:`atlas.engine.intent.propose` checks the good, the counterparty and the reach
    #: before a row exists. Parsed always and *offered* only where
    #: :func:`atlas.engine.pricing.enabled` -- a barter world has nothing to buy a cargo
    #: with.
    trade_intents: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: What this power is refusing to sell, and to whom, as ``[{"action": "impose"|"lift",
    #: "target": id, "good": str?}, ...]``. **The one deliberate act in the trade layer**:
    #: everything else a government says here is a thing it wants arranged, and this is a
    #: thing it is doing at its own expense. Intent, not a write path, exactly like
    #: ``trade_intents``: every column of ``trade_embargo`` is engine-only, and
    #: :func:`atlas.engine.embargo.impose` checks that the power is actually exporting to
    #: the target, computes what it is giving up, charges the diplomatic cost and writes the
    #: row. Parsed always and *offered* only where :func:`atlas.engine.embargo.enabled` --
    #: a field in every recorded prompt would move every cassette for a default-off feature.
    trade_embargoes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: Who this power is backing in somebody else's war, as ``{client: {kind: level}}``.
    #: Intent, not a write path, exactly like the two above -- ``support.level`` is
    #: engine-only, the engine checks the client is real and reachable, and **nothing here
    #: can make the patron a belligerent**. That last part is the whole feature: a patron
    #: with no channel had two settings, neutral or at war, and the twentieth century is
    #: mostly the third one.
    support: dict[str, Any] = field(default_factory=dict)
    #: The same, sent without saying so. Only the deniable kinds; see `_covert_support_property`.
    covert_support: dict[str, Any] = field(default_factory=dict)
    #: What this power wants its country to *be*, as a single stance id -- "professional",
    #: "minimum", "neutrality". Intent, not a write path, exactly like the objectives: the
    #: engine checks whether the shift is legal, prices it, charges for it and writes the
    #: row itself, and every column of `doctrine` is engine-only. A stance names its own
    #: axis, so this is one word rather than two.
    doctrine_shift: str = ""
    #: Dependencies this power is granting independence to, as region ids. Intent again:
    #: the engine refuses ground that is not a dependency, refuses a seat of government,
    #: and mints the new state itself.
    grant_independence: tuple[str, ...] = field(default_factory=tuple)
    #: What this power says it is fighting *for*, as ``[{"against": nation, "kind": kind,
    #: "region": region?}, ...]``. Intent in the strongest sense: an aim is a statement of
    #: what would end the war, and it decides nothing at all on its own. The engine refuses
    #: an aim in a war this power is not in, and ground the enemy does not hold.
    war_aims: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: What this power will stop the war for, as ``{"against": nation, "terms": [...]}``.
    #: Intent again, and the strongest case for the pattern: the engine reviews every term
    #: against the map, decides acceptance from the war rather than from the offer, and
    #: writes the treaty itself. Nothing here can end a war the other side will not end.
    peace_offer: dict[str, Any] = field(default_factory=dict)
    #: Terms this power is putting to somebody *else's* war, as ``{"between": [a, b],
    #: "terms": [...]}``. Intent in the same sense as a peace offer and held to a higher
    #: bar: both belligerents run the ordinary acceptance test, and a broker that is a
    #: party to the fight is refused outright. See :mod:`atlas.engine.mediation`.
    mediation: dict[str, Any] = field(default_factory=dict)

    def with_seq(self) -> DiffBatch:
        """Return a copy with ``seq`` renumbered densely from zero."""
        from dataclasses import replace

        return replace(
            self,
            diffs=tuple(replace(d, seq=i) for i, d in enumerate(self.diffs)),
        )


# ---------------------------------------------------------------------------
# JSON Schema handed to the CLIs
# ---------------------------------------------------------------------------

_SCALAR = {"type": ["string", "number", "boolean", "null"]}


def writable_fields_for(role: Role) -> dict[str, list[str]]:
    """The columns ``role`` may write, grouped by table.

    Used to build the response schema, so a model is *shown* the legal field names rather
    than left to guess them from a human-readable briefing. That guessing was a real
    failure mode: a view saying "regard: +12" and "military 32.0" led models to propose
    ``field: "regard"`` and ``field: "military"``, neither of which is a column, and the
    whole batch was refused. Naming the columns in the schema turns an entire class of
    rejection into something the CLI simply will not emit.
    """
    from .permissions import ROLE_WRITABLE

    grouped: dict[str, list[str]] = {}
    for table, column in sorted(ROLE_WRITABLE.get(role, frozenset())):
        if column == "*":
            continue
        grouped.setdefault(table, []).append(column)
    return grouped


def diff_batch_schema(*, allow_events: bool = True, role: Role | None = None) -> dict[str, Any]:
    """The canonical schema for a model's response.

    ``additionalProperties: false`` throughout is deliberate. A model that invents a
    field is telling us it misunderstood the contract, and we would rather see that as a
    hard schema failure than silently drop the field and apply a half-understood change.

    Note what is *absent*: there is no ``outcome``, ``result``, ``winner`` or
    ``pressure`` field anywhere. A model cannot report who won a battle, because it does
    not decide. It reports evidence; :mod:`atlas.sim.resolve` draws the result.
    """
    writable = writable_fields_for(role) if role is not None else {}
    if writable:
        table_property: dict[str, Any] = {"type": "string", "enum": sorted(writable)}
        field_property: dict[str, Any] = {
            "type": "string",
            "enum": sorted({column for columns in writable.values() for column in columns}),
            "description": (
                "The exact database column to change. Use these names literally -- they are "
                "not the labels used in your briefing. "
                + "; ".join(f"{table}: {', '.join(columns)}" for table, columns in sorted(writable.items()))
            ),
        }
    else:
        table_property = {
            "type": "string",
            "enum": ["nation", "region", "relation", "unit", "city", "commitment"],
        }
        field_property = {"type": "string"}

    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["diffs"],
        "properties": {
            "narrative": {
                "type": "string",
                "description": "In-world prose describing what happened. Flavour only; never parsed for mechanics.",
            },
            "diffs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["target_table", "target_id", "field", "op", "reason_event_id"],
                    "properties": {
                        "target_table": table_property,
                        "target_id": {
                            "type": "string",
                            "description": "The row's id. For a relation, write it as '<nation_a>|<nation_b>'.",
                        },
                        "field": field_property,
                        "op": {"type": "string", "enum": ["set", "adjust"]},
                        "new_value": _SCALAR,
                        "expected_prior": dict(
                            _SCALAR,
                            description="The value you believe this field currently holds. The change is refused if it has moved.",
                        ),
                        "reason_event_id": {
                            "type": "integer",
                            "description": "REQUIRED. The id of an existing event that causes this change. A change you cannot attribute to a specific prior event will be rejected.",
                        },
                        "visibility_scope": {
                            "type": "string",
                            "enum": ["public", "bilateral", "secret"],
                            "description": "Who can observe this change. Must not be broader than the visibility of the event you cited.",
                        },
                    },
                },
            },
        },
    }

    if allow_events:
        schema["properties"]["events"] = {
            "type": "array",
            "description": "New events to record. These are narrative and diplomatic occurrences only.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "text"],
                "properties": {
                    # Gating kinds are absent from this enum on purpose: a model cannot
                    # mint its own casus belli and cite it next turn.
                    "kind": {
                        "type": "string",
                        "enum": [
                            "diplomatic_message",
                            "ultimatum",
                            "envoy_dispatched",
                            "commitment_made",
                            "commitment_broken",
                            "commitment_fulfilled",
                            "treaty_signed",
                            "treaty_broken",
                            "trade_agreement",
                            "alliance_formed",
                            "alliance_broken",
                            "unrest",
                            "election",
                            "leader_change",
                            "discovery",
                            "espionage",
                            "sabotage",
                            "narrative",
                        ],
                    },
                    "actor_nation_id": {"type": ["string", "null"]},
                    "text": {"type": "string"},
                    "visibility": {
                        "type": "string",
                        "enum": ["public", "bilateral", "secret"],
                    },
                    "visible_to": {"type": "array", "items": {"type": "string"}},
                    "causes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Ids of earlier events that led to this one.",
                    },
                },
            },
        }
    if role is Role.WORLD_SIM:
        schema["properties"]["contests"] = _contests_property()
        schema["properties"]["declarations"] = _declarations_property()
    if role is Role.TRACKED_AGENT:
        schema["properties"]["military_objectives"] = _military_objectives_property()
        schema["properties"]["build_orders"] = _build_orders_property()
        schema["properties"]["raise_formations"] = _raise_formations_property()
        schema["properties"]["reinforce"] = _reinforce_property()
        schema["properties"]["mobilisation"] = _mobilisation_property()
        schema["properties"]["declare_war"] = _declare_war_property()
        schema["properties"]["support"] = _support_property()
        schema["properties"]["covert_support"] = _covert_support_property()
        schema["properties"]["grant_independence"] = _grant_independence_property()
        schema["properties"]["doctrine_shift"] = _doctrine_shift_property()
        schema["properties"]["war_aims"] = _war_aims_property()
        schema["properties"]["peace_offer"] = _peace_offer_property()
        schema["properties"]["mediation"] = _mediation_property()
        # **Behind the pricing flag, and that is a schema-level decision rather than a
        # runtime one.** A world that barters cannot pay for a cargo, so asking a staff what
        # it would like to buy would be offering an order the engine would refuse. Read
        # through the function rather than captured at import, because a test moves the flag
        # inside one process -- and because the flag now defaults *on*, so the branch that
        # has to keep working is the one a barter world takes.
        from ..engine import pricing

        if pricing.enabled():
            schema["properties"]["trade_intents"] = _trade_intents_property()
        # And the deliberate half of the same layer, behind its own flag. Read through the
        # function for the same reason -- `embargo.enabled` is conjoined with pricing, so
        # this is never offered in a world that cannot pay for a cargo and therefore cannot
        # sacrifice the payment.
        from ..engine import embargo

        if embargo.enabled():
            schema["properties"]["trade_embargoes"] = _trade_embargoes_property()

    # Written to the strictest dialect any provider requires, so one schema serves
    # all three. See atlas.state.jsonschema.
    return make_strict(schema)


def _declaration_term_schema() -> dict[str, Any]:
    """A peace term as the simulator writes one: the sides are named, not relative.

    The simulator speaks for everybody, so "me" and "them" mean nothing to it -- it says
    `actor` or `target`, which are the two powers it has already named on the declaration.
    """
    from ..engine.peace import term_schema

    schema = term_schema()
    schema["properties"] = dict(schema["properties"])
    schema["properties"]["from"] = {
        "type": "string",
        "enum": ["actor", "target"],
        "description": "Which of the two powers named above carries this term out.",
    }
    return schema


def _declarations_property() -> dict[str, Any]:
    """How a war starts or ends when the player is not the one doing it.

    ``relation.at_war`` is engine-only, so before this existed the only power on the map
    that could enter a state of war was the player, and no war could ever end -- every
    world drifted toward permanent, undeclared fighting. The simulator now reports the
    *intent*; the engine writes the event, charges the legitimacy an unprovoked war costs,
    and sets the flag in both directions.

    Still absent: any outcome. Declaring war does not say who wins it, and offering peace
    does not say it was accepted -- the engine decides whether the conditions hold.
    """
    return {
        "type": "array",
        "description": (
            "Changes in the state of war between nations this turn. Report a declaration "
            "when a power commits to war, and a settlement when fighting formally ends. "
            "You are reporting what the powers decided, not what it achieves."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "actor", "target", "reason_event_id"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["declare_war", "ceasefire", "peace"],
                },
                "actor": {"type": "string", "description": "Nation id of the side acting."},
                "target": {"type": "string", "description": "Nation id of the other side."},
                "justification": {
                    "type": "string",
                    "description": (
                        "One sentence, in world. A declaration without a standing casus "
                        "belli costs legitimacy however well it is argued."
                    ),
                },
                "months": {
                    "type": "integer",
                    "description": (
                        "For a `ceasefire`: how long the truce runs before it lapses. "
                        "Defaults to three months. A ceasefire stops the fighting and "
                        "settles nothing -- the war goes on and either side can break it."
                    ),
                },
                "terms": {
                    "type": "array",
                    "maxItems": 8,
                    "items": _declaration_term_schema(),
                    "description": (
                        "Only on a `peace`: what the settlement says. Reviewed against the "
                        "map term by term, and accepted or refused according to how the war "
                        "has actually gone. Richer than `cessions`, which it supersedes: "
                        "restoration, withdrawal, reparations, a demilitarised zone, a "
                        "guarantee and recognition are all sayable here."
                    ),
                },
                "cessions": {
                    "type": "array",
                    "description": (
                        "Only on a `peace`: provinces that change hands as its terms. Each "
                        "names a province id and which of the two powers gets it. A province "
                        "may only be ceded if it is held between exactly these two -- "
                        "typically ground one side has occupied and the other still owns. "
                        "The winner keeping what it took is an annexation; a province handed "
                        "back to whoever it belongs to is a recovery. A `ceasefire` carries "
                        "no cessions."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["region", "to"],
                        "properties": {
                            "region": {
                                "type": "string",
                                "description": "Province id changing hands.",
                            },
                            "to": {
                                "type": "string",
                                "description": "Nation id it is ceded to (one of the two powers).",
                            },
                        },
                    },
                },
                "reason_event_id": {
                    "type": "integer",
                    "description": "REQUIRED. The id of the event that led to this.",
                },
            },
        },
    }


#: How many regions a power may name in one objective list. A tracked agent is asked to
#: decide a war, not to write out an order of battle, and an unbounded list is what
#: hollowed out the tracked-agent calls before: a nation handed a hundred province ids
#: spends its whole answer restating them. Anything past this is dropped, and the nation
#: is told so in its briefing rather than silently ignored.
OBJECTIVE_LIMIT = 6


def _military_objectives_property() -> dict[str, Any]:
    """What a tracked power's staff is trying to do with its army.

    The channel exists because the alternative shapes are both wrong. Letting a nation
    write ``region.controller_nation_id`` would hand it the map; giving it nothing means
    a power with its own agent, its own goals and its own read of the war has no way to
    say "I am going for the Saar" -- so its army is directed by an engine heuristic and
    its agency stops at the diplomatic.

    Note what a nation still cannot say here: not who wins, not how fast, not how much it
    costs, and not what anyone else does. It names ground. The engine checks each name
    against the map, drops what this power cannot reach, and resolves the fighting the
    same way it resolves everyone else's.
    """
    region_list = {
        "type": "array",
        "maxItems": OBJECTIVE_LIMIT,
        "items": {"type": "string"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "What you are trying to do with your army, as region ids from your briefing. "
            "Standing intent: it holds until you declare otherwise. You are not deciding "
            "any outcome -- ground you cannot reach is ignored, and the engine resolves "
            "the fighting."
        ),
        "properties": {
            "push": {
                **region_list,
                "description": (
                    "Ground held by someone else that you are attacking. Must border "
                    f"territory you hold. At most {OBJECTIVE_LIMIT}."
                ),
            },
            "hold": {
                **region_list,
                "description": (
                    "Ground you hold and intend to defend. Your reserves move toward it."
                ),
            },
            "withdraw_from": {
                **region_list,
                "description": (
                    "Ground you hold and are giving up on. Your formations there leave "
                    "and any attack you were making from it stops."
                ),
            },
            "reason_event_id": {
                "type": "integer",
                "description": "REQUIRED. The id of the event these objectives answer to.",
            },
        },
        "required": ["reason_event_id"],
    }


def _support_property() -> dict[str, Any]:
    """Backing a side in somebody else's war without joining it.

    **The channel that was missing.** The `support` table, its effect on a contest, its
    air defence, its funding transfer, its briefing lines and its rewind were all built
    and tested -- and no model could ever open a support relationship, because
    ``support.level`` is engine-only and nothing in any answer schema named it. The
    mechanism was complete and unreachable, which is the one defect a unit test that calls
    the function directly can never catch.

    Same shape as ``military_objectives`` and for the same reasons: a nation names *who*
    and *how much*, and the engine decides whether that is possible. It cannot say what
    the support achieves, cannot aim it at itself, and above all **cannot make itself a
    belligerent** -- there is no path from here to ``relation.at_war``.
    """
    from ..engine.support import MAX_LEVEL, SUPPORT_KINDS

    return {
        "type": "object",
        "description": (
            "Who you are backing in a war you are not fighting, as "
            "{nation id: {kind: level}}. Levels run 0 to "
            f"{MAX_LEVEL:.0f}; 0 withdraws. Kinds: "
            + ", ".join(f"`{kind}` ({what})" for kind, what in SUPPORT_KINDS.items())
            + ". Standing intent: it holds until you change it. This does NOT put you at "
            "war with your client's enemy and does not commit you to anything."
        ),
        "additionalProperties": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
    }


def _covert_support_property() -> dict[str, Any]:
    """The same backing, sent without saying so.

    **Deniability was flavour until it had a channel.** `support` already priced advisors
    and intelligence as "the deniable ones" and reasoned in its own comments about the point
    where the other side stops pretending -- and every shipment it wrote was public, so
    there was nothing to deny and nothing to work out.

    Its own property rather than a flag inside `support`, for two reasons. The existing
    channel is ``{nation: {kind: number}}`` and a boolean in there would break the shape a
    provider is handed; and choosing to run a war quietly is a different decision from
    choosing to run one, which is worth a model having to say separately.

    Only the deniable kinds may be sent this way. The engine refuses the rest with a reason:
    a tank is a tank the other side can photograph.
    """
    from ..engine.support import DENIABLE_KINDS, MAX_LEVEL

    return {
        "type": "object",
        "description": (
            "The same as `support`, sent quietly, as {nation id: {kind: level}}. Only "
            + ", ".join(f"`{kind}`" for kind in sorted(DENIABLE_KINDS))
            + f" can be sent this way; levels run 0 to {MAX_LEVEL:.0f}. The shipment is "
            "not public, so your client's enemies do not read about it -- but they work "
            "at it, and what they can prove about you grows the longer you keep it up. "
            "This does NOT put you at war with anybody."
        ),
        "additionalProperties": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
    }


def _grant_independence_property() -> dict[str, Any]:
    """Letting a dependency go, by agreement.

    The diplomatic end of :mod:`atlas.engine.decolonisation`. Momentum accrues on its own
    every turn and the holder is shown it; this is the only thing the holder can *do* about
    it short of garrisoning the ground harder.

    An engine-checked intent rather than a gambit, deliberately. A gambit is something you
    attempt against somebody and may fail at; granting independence is a decision that is
    yours alone and cannot fail on a die roll. What it can do is be refused for naming
    ground that is not a dependency -- which the engine says, with a reason.
    """
    return {
        "type": "array",
        "maxItems": OBJECTIVE_LIMIT,
        "items": {"type": "string"},
        "description": (
            "Region ids of dependencies you are recognising as independent states. Only "
            "ground you hold that is nobody's homeland, and never your own seat of "
            "government. This is final and it is not reversible by you."
        ),
    }


def _doctrine_shift_property() -> dict[str, Any]:
    """Changing what this country is built to do.

    The slowest thing a nation can decide and the only one that changes how its own
    arithmetic works. Deliberately one word: a stance names its own question, so a staff
    cannot pair "minimum" with "how the army is raised" and be refused for a mistake the
    schema could have prevented.

    An engine-checked intent rather than a diff, for the reason `military_objectives` is
    one: the columns are engine-only, and what a nation may write is what it *wants*.
    """
    from ..engine.doctrines import AXES

    lines = []
    for axis in AXES:
        for stance in axis.stances:
            lines.append(f"{stance.id} ({axis.label.lower()}: {stance.blurb})")
    return {
        "type": "string",
        "description": (
            "Leave empty unless you are changing what your country is built to do. One "
            "stance id: " + "; ".join(lines) + ". A change costs a share of your treasury "
            "and takes half a year -- twice that at war -- during which you have the "
            "advantages of neither the old doctrine nor the new one. Only one at a time."
        ),
    }


def _war_aims_property() -> dict[str, Any]:
    """What this power says it is fighting for, per war.

    **A war here has never had a point.** It is two ``relation`` rows, and between the
    declaration and the settlement there was nowhere to record what either side wanted out
    of it -- so a peace could only ever be *stop shooting, and here is some ground*.

    Note what a power still cannot say here: not that its aims are met, not what the enemy
    must accept, and not what it will settle for. It states what it wants; the engine
    refuses an aim in a war it is not in and ground the enemy does not hold, and everything
    a settlement does with these is decided above this channel.
    """
    from ..engine.waraims import AIM_KINDS, WAR_AIM_LIMIT

    return {
        "type": "array",
        # Room for a few aims in each of a few wars. A power fighting more wars than this
        # has a bigger problem than the cap.
        "maxItems": WAR_AIM_LIMIT * 3,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "against": {
                    "type": "string",
                    "description": "The nation id you are fighting. You must be at war.",
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(AIM_KINDS),
                    "description": "; ".join(
                        f"{kind}: {why}" for kind, why in sorted(AIM_KINDS.items())),
                },
                "region": {
                    "type": "string",
                    "description": (
                        "The region id the aim is about. Required for annex, "
                        "independence and demilitarise; not allowed for the others."
                    ),
                },
            },
            "required": ["against", "kind"],
        },
        "description": (
            "What you are fighting for, per enemy. Standing until you restate it for that "
            f"enemy; at most {WAR_AIM_LIMIT} aims against any one of them. Naming aims "
            "against one enemy leaves your aims in your other wars alone."
        ),
    }


def _mediation_property() -> dict[str, Any]:
    """Terms this power is putting to a war it is not in.

    **The marquee diplomatic action, and the layer shipped without it.** Every way a war
    could end went through one of its own belligerents: a power outside it could send
    equipment, take a side, or watch, and had no sentence that meant *stop, and here are the
    terms*.

    What a power still cannot say here: not that either side accepts. Both run the ordinary
    acceptance test, in both directions -- a higher bar than a bilateral offer, where only
    the side being offered to has to agree.
    """
    from ..engine.peace import term_schema

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["between"],
        "properties": {
            "between": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "string"},
                "description": (
                    "The two nation ids at war whose peace you are brokering. Neither may "
                    "be you, and you may not be at war with either."
                ),
            },
            "terms": {
                "type": "array",
                "maxItems": 8,
                "items": term_schema(),
                "description": (
                    "What you are proposing they settle on. Leave empty to put a plain end "
                    "to the fighting, which a side that is winning will refuse."
                ),
            },
        },
        "description": (
            "An offer to broker somebody else's war. Your standing in the international "
            "order decides how much weight your good offices carry at the table, and both "
            "belligerents must accept -- you persuade, you do not sign for anybody. "
            "Succeeding is worth more to your standing than anything else you can do in a "
            "single turn."
        ),
    }


def _peace_offer_property() -> dict[str, Any]:
    """What this power will stop the war for.

    **The sentence a beaten power did not have.** It could be shown its exhaustion, its
    casualties and the engine's own reading that it was beaten, and there was no answer it
    could return that meant *I will stop on these terms* -- so the only strategy available
    to a losing model was to fight on. Everything the settlement layer does was reachable
    from engine code and from a human's typed orders, and from nowhere a model could get to.

    What a power still cannot say here: not that the other side accepts. Acceptance is
    computed from the war -- see :func:`atlas.engine.peace.acceptance` -- because a winner
    who could talk a loser into surrender would be deciding the war by narration.
    """
    from ..engine.armistice import MAX_MONTHS as ARMISTICE_MAX_MONTHS
    from ..engine.peace import term_schema

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["against"],
        "properties": {
            "against": {
                "type": "string",
                "description": "The nation id you are offering to make peace with.",
            },
            "kind": {
                "type": "string",
                "enum": ["peace", "ceasefire"],
                "description": (
                    "`peace` settles the war: terms are carried out and it is over. "
                    "`ceasefire` only stops the fighting for a while -- no terms, nothing "
                    "settled, the war goes on, and either side can break it. Defaults to "
                    "peace."
                ),
            },
            # **Bounded here as well as clamped there.** `armistice.offer_ceasefire` has
            # always held this inside 1..MAX_MONTHS and did it silently, so a staff that
            # asked for a five-year truce got two years and was told nothing. The engine
            # still clamps -- a schema is a request, not a guarantee -- but a bound the
            # model is handed is a bound it can respect, which is the whole of decision 50.
            "months": {
                "type": "integer",
                "minimum": 1,
                "maximum": ARMISTICE_MAX_MONTHS,
                "description": (
                    "For a ceasefire: how long it runs, from 1 to "
                    f"{ARMISTICE_MAX_MONTHS} months. Defaults to three. Longer than "
                    f"{ARMISTICE_MAX_MONTHS} is shortened to it -- a truce that long is a "
                    "peace treaty that has not admitted what it is, so offer `peace`."
                ),
            },
            "terms": {
                "type": "array",
                "maxItems": 8,
                "items": term_schema(),
                "description": (
                    "What the settlement says. Leave empty to offer an unconditional end "
                    "to the fighting -- which a power that is winning will refuse."
                ),
            },
        },
        "description": (
            "An offer to end one of your wars. The engine checks every term against the "
            "map, and the other side accepts or refuses according to how the war has "
            "actually gone -- not according to how you put it."
        ),
    }


#: How many things a power may lay down in one turn. Its queue is only a few slots wide, so
#: anything past this is refused for lack of a slot anyway; the cap just keeps the answer
#: from becoming a wish-list. Named separately from OBJECTIVE_LIMIT because the constraint
#: is different (queue width, not answer focus).
BUILD_ORDER_LIMIT = 3

#: How many separate raising orders a tracked power may give in one turn, and how many
#: formations one order may ask for.
#:
#: **These are a rate, and the rate is the only hard limit on rebuilding.** Everything else
#: is priced: treasury and manpower are charged per formation and a power that cannot pay
#: is refused, which is the brake that should bind in an ordinary game. The rate exists for
#: the case where it does not -- a rich power at war with a small one could otherwise turn
#: its whole treasury into divisions in a single month, which is not a month.
FORMATION_ORDER_LIMIT = 2
FORMATIONS_PER_ORDER = 3


def _build_orders_property() -> dict[str, Any]:
    """What a tracked power is trying to build this turn.

    The economic twin of ``military_objectives``. A power with its own agent, its own
    economy and its own read of where it is behind had no way to say "I am building a
    steelworks" -- so it never built anything, and over a long game only the human's
    country developed while the AI world stood frozen. This names the intent; the engine
    runs it through the same order handler the player uses, which is the one that charges
    the treasury and occupies the queue. The power decides *what* to build; it decides
    nothing about whether it can afford it or when it finishes.
    """
    return {
        "type": "array",
        "maxItems": BUILD_ORDER_LIMIT,
        "description": (
            "Public works and research programmes you want to begin this turn, by id from "
            "the build menu in your briefing (for example a steelworks or workshops to grow "
            "your industry, or rail to grow your economy). Each takes a queue slot and is "
            "paid for when it starts. The engine charges you and refuses what you cannot "
            f"afford or have no slot for. At most {BUILD_ORDER_LIMIT}."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["project"],
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "The id of a public work or research programme from your briefing, "
                        "e.g. 'workshops', 'steelworks', 'rail', 'welding'."
                    ),
                },
                "region": {
                    "type": "string",
                    "description": (
                        "Optional. A region id you hold to build it in; the engine picks "
                        "one of yours if you do not name it."
                    ),
                },
            },
        },
    }


def _raise_formations_property() -> dict[str, Any]:
    """What a tracked power is putting in the field this turn.

    ``build_orders`` gave an AI power an economy; this gives it an army that does not only
    shrink. Between them a power could develop industry for thirty turns and still be
    ground to nothing, because the one thing it could never do was replace a loss.

    **The brake is the price, not a rule.** A formation costs treasury and manpower from
    the same purse the factories come out of, and ``_raise_units`` already refuses what
    neither will stretch to -- so a power that spends its war rebuilding its army is a
    power that is not building its economy, which is the trade the decision should be. The
    only hard bound here is the rate: ``FORMATION_ORDER_LIMIT`` orders a turn, so a month
    is a month's recruiting and not a mobilisation.
    """
    return {
        "type": "array",
        "maxItems": FORMATION_ORDER_LIMIT,
        "description": (
            "New formations you want to raise this turn, by unit kind from the forces menu "
            "in your briefing. This is how you replace losses: an army that is fighting is "
            "an army that is shrinking, and nothing else on this form puts a division back "
            "in the line. Each is paid for in treasury AND manpower out of the same purse "
            "your building comes from, so raising an army you do not need is industry you "
            "do not get. The engine charges you and refuses what you cannot afford. At most "
            f"{FORMATION_ORDER_LIMIT} orders."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["unit_kind"],
            "properties": {
                "unit_kind": {
                    "type": "string",
                    "description": (
                        "The kind of formation, from the forces menu in your briefing, "
                        "e.g. 'infantry' or 'armour'. A kind this scenario does not define "
                        "is refused rather than quietly turned into infantry."
                    ),
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": FORMATIONS_PER_ORDER,
                    "description": (
                        "How many to raise, at most "
                        f"{FORMATIONS_PER_ORDER}. The engine raises as many as the treasury "
                        "and manpower cover and no more."
                    ),
                },
                "region": {
                    "type": "string",
                    "description": (
                        "Optional. A region id you hold to raise them in; the engine picks "
                        "one of yours if you do not name it."
                    ),
                },
            },
        },
    }


def _reinforce_property() -> dict[str, Any]:
    """Whether to make good this turn's losses in the formations already in the line.

    Deliberately a boolean and not a list of divisions. **The decision worth modelling is
    guns or butter, not which battalion gets the drafts** -- and a staff asked to name
    formations by id would spend its turn naming them, get one wrong, and have the order
    refused. The engine spends the allowance most-worn-first among what is in supply, which
    is both what a real staff would do and reproducible in a way a model's ordering is not.
    """
    return {
        "type": "boolean",
        "description": (
            "Say true to send replacements to formations you already have. This is how a "
            "worn army is held together: it restores strength to under-strength formations "
            "that are in supply, most damaged first, at a quarter of what the same strength "
            "would cost in a newly raised formation -- but at full manpower, because the "
            "men have to come from somewhere. It never spends more than half your treasury, "
            "so you can reinforce and keep building in the same turn. Formations out of "
            "supply cannot be reinforced at any price."
        ),
    }


def _mobilisation_property() -> dict[str, Any]:
    """How hard this country is digging for soldiers.

    **The channel that turns the manpower floor into a decision.** Replacement is paid in
    men, and how many men a country can reach was a fact about its population that nothing
    a government did could change -- so a war of attrition had exactly one answer and it was
    demography. This is the lever against that, and it is deliberately expensive.
    """
    from ..engine.mobilisation import LEVELS, MAX_LEVEL

    return {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_LEVEL,
        "description": (
            "The war footing you want your country on: "
            + "; ".join(f"{f.level} {f.label}" for f in LEVELS)
            + ". A higher footing reaches more men and refills them faster, and costs you a "
              "share of your revenue and stability every month you stay on it. The engine "
              "moves you **one step a turn** toward what you ask for, so this is a "
              "commitment rather than a switch. Your briefing prices each rung."
        ),
    }


#: Words a model writes into a required field when it means *nobody*. A strict schema makes
#: every field mandatory, so "no target" arrives spelled -- as a blank, and also as a word:
#: Gemini, playing North Vietnam in betrayal-only run 2 (2026-09-22), sent
#: ``{"target": "none"}`` and was refused "no power called 'none'". Compared case-blind and
#: stripped. **No nation is called any of these** in any shipped era; a map that ever adds
#: one would need this list revisited, and `tests/test_a_blank_declaration_is_no_declaration`
#: checks the shipped eras for exactly that.
PLACEHOLDER_NAMES: frozenset[str] = frozenset({
    "none", "null", "nil", "n/a", "na", "nobody", "no one", "noone", "-", "--",
})


def _names_somebody(value: Any) -> bool:
    """Whether ``value`` is a name at all, rather than a blank or a placeholder word."""
    text = str(value or "").strip().lower()
    return bool(text) and text not in PLACEHOLDER_NAMES


def _declaration(raw: Any) -> dict[str, Any]:
    """A `declare_war` with no target is no declaration at all.

    **A strict schema makes a model fill every field, and some fill this one with a blank.**
    In the kitchen-sink match of 2026-09-21 Gemini sent ``{"target": ""}`` for China and
    North Vietnam at turn 3 -- a placeholder, not a request -- and the engine answered it as
    a refused declaration, "no power called '' to declare war on", which went onto both
    powers' refusal ledgers and into their next briefings. Nobody asked; nothing was refused.
    """
    wanted = dict(raw or {})
    return wanted if _names_somebody(wanted.get("target")) else {}


def _peace_offer(raw: Any) -> dict[str, Any]:
    """A `peace_offer` against nobody is no offer -- the same placeholder as `_declaration`.

    Seen live in the betrayal-only match of 2026-09-22: Gemini, playing Czechoslovakia,
    sent ``{"against": ""}`` at turns 4, 10 and 13, and each came back as "'' is nobody you
    can make peace with" on its ledger and in its next briefing. A strict schema asks for the
    field; a model with nothing to offer fills it with a blank.
    """
    offer = dict(raw or {})
    return offer if _names_somebody(offer.get("against")) else {}


def _mediation(raw: Any) -> dict[str, Any]:
    """A `mediation` that names nobody at all is no mediation.

    **Every name blank, not any.** ``["", ""]`` is two entries, passes the length check, and
    was refused as "a power is not at war with itself"; ``[]`` was refused as naming the
    wrong number of powers. Both are placeholders. One real name beside a blank is a genuine
    attempt written badly, and still reaches the engine and its refusal -- that one the
    staff should hear about.
    """
    offer = dict(raw or {})
    named = offer.get("between") or []
    if isinstance(named, str):
        named = [named]  # one name written bare is still one name -- or one blank
    if not isinstance(named, (list, tuple)):
        return offer  # malformed, not blank: the engine says what is wrong with it
    return offer if any(_names_somebody(n) for n in named) else {}


def _declare_war_property() -> dict[str, Any]:
    """Opening a war of your own.

    **The channel that was missing, and the reason the victory layer never activated.**
    Ground is the largest component of the engine's own score and it moves by conquest; a
    government that could not choose to fight could not choose to pursue it, so the pack
    never separated and no game was ever won. `declarations` belonged to the world simulator
    alone.

    Gated hard, because the simulator is trusted to declare sensibly -- it sees the whole
    board and has no side -- and a government has a side. `atlas.engine.warpath` checks that
    the war could be prosecuted at all, that there is a grievance the world would recognise,
    that this power is not already fighting more than it can, and that it has not declared
    one recently. Every refusal names the gate it failed.
    """
    from ..engine.warpath import COOLDOWN_DAYS, MAX_CONCURRENT_WARS

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["target"],
        "description": (
            "Declare war on another power. This is the heaviest thing on this form and the "
            "engine refuses most of them: you must be able to reach them, you must have a "
            "grievance the world would recognise -- they hold ground of yours, an ally you "
            "are sworn to is already fighting them, or relations are already very bad -- you "
            f"must not already be in {MAX_CONCURRENT_WARS} wars, and you must not have "
            f"declared one in the last {COOLDOWN_DAYS / 365:.0f} years. It costs you "
            "standing with "
            "everybody, whatever the grievance. Leave it out unless you mean it."
        ),
        "properties": {
            "target": {
                "type": "string",
                "description": "The id of the power you are declaring war on.",
            },
            "justification": {
                "type": "string",
                "description": (
                    "What you are telling the world. Recorded on the declaration; it does "
                    "not decide whether the declaration is accepted."
                ),
            },
        },
    }


def _contests_property() -> dict[str, Any]:
    """How a clash is reported: circumstances only, never a result.

    This is the single most important shape in the schema. Without it the simulator has
    no way to say "these two are fighting over this region", so no battle can ever be
    resolved and no territory can ever change hands -- the map freezes on turn one and
    every order to take ground is quietly ignored.

    With it, note what a model still cannot say: there is no winner, no probability, no
    casualty count, no territory transfer. Only *who*, *where*, and a closed list of
    observations. :func:`atlas.sim.resolve.resolve_contest` turns those into a result by
    weighing real force ratios and drawing from the world's seeded stream.
    """
    from ..sim.resolve import ContestKind, EvidenceFlag

    return {
        "type": "array",
        "description": (
            "Armed clashes happening this turn. Report one for every attempt to take or "
            "hold ground by force, including the player's. You do NOT decide who wins: "
            "report the circumstances and the engine resolves it."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "attacker", "defender", "region_id", "reason_event_id"],
            "properties": {
                "kind": {"type": "string", "enum": [k.value for k in ContestKind]},
                "attacker": {"type": "string", "description": "Nation id of the side acting."},
                "defender": {"type": "string", "description": "Nation id of the side resisting."},
                "region_id": {"type": "string", "description": "Region id being contested."},
                "flags": {
                    "type": "array",
                    "description": (
                        "Observations about the situation, from this closed list. Each one "
                        "shifts the odds by an amount fixed in engine code, not by you."
                    ),
                    "items": {"type": "string", "enum": [f.value for f in EvidenceFlag]},
                },
                "reason_event_id": {
                    "type": "integer",
                    "description": "REQUIRED. The id of the event that caused this clash.",
                },
            },
        },
    }


def parse_batch(
    payload: str | dict[str, Any],
    *,
    world_id: str,
    turn: int,
    role: Role,
    model: str | None = None,
    nation_id: str | None = None,
) -> DiffBatch:
    """Build a :class:`DiffBatch` from a provider's schema-validated response.

    The CLI has already enforced the schema, so this is a mapping step rather than a
    parser. It still tolerates a JSON string, because ``claude --output-format json``
    returns the structured answer as a *string* inside its ``result`` field.
    """
    data: dict[str, Any] = json.loads(payload) if isinstance(payload, str) else payload

    diffs: list[Diff] = []
    for index, raw in enumerate(data.get("diffs") or ()):
        diffs.append(
            Diff(
                target_table=str(raw["target_table"]),
                target_id=str(raw["target_id"]),
                field=str(raw["field"]),
                op=DiffOp(raw["op"]),
                new_value=raw.get("new_value"),
                expected_prior=raw.get("expected_prior"),
                reason_event_id=raw.get("reason_event_id"),
                visibility_scope=Visibility(raw.get("visibility_scope", "public")),
                seq=index,
            )
        )

    return DiffBatch(
        world_id=world_id,
        turn=turn,
        proposer_role=role,
        proposer_model=model,
        proposer_nation_id=nation_id,
        diffs=tuple(diffs),
        contests=tuple(data.get("contests") or ()),
        declarations=tuple(data.get("declarations") or ()),
        military_objectives=dict(data.get("military_objectives") or {}),
        build_orders=tuple(data.get("build_orders") or ()),
        raise_formations=tuple(data.get("raise_formations") or ()),
        reinforce=bool(data.get("reinforce") or False),
        mobilisation=(int(data["mobilisation"])
                      if data.get("mobilisation") is not None else None),
        declare_war=_declaration(data.get("declare_war")),
        trade_intents=tuple(data.get("trade_intents") or ()),
        trade_embargoes=tuple(data.get("trade_embargoes") or ()),
        support=dict(data.get("support") or {}),
        covert_support=dict(data.get("covert_support") or {}),
        doctrine_shift=str(data.get("doctrine_shift") or ""),
        grant_independence=tuple(
            str(region) for region in (data.get("grant_independence") or ())),
        war_aims=tuple(data.get("war_aims") or ()),
        peace_offer=_peace_offer(data.get("peace_offer")),
        mediation=_mediation(data.get("mediation")),
        narrative=str(data.get("narrative", "")),
        events=tuple(data.get("events") or ()),
    )


def _trade_intents_property() -> dict[str, Any]:
    """What this power wants its trade delegations to go and arrange.

    **Intent, not a write path, in exactly the sense ``build_orders`` and
    ``military_objectives`` are** -- and the reason is the same one the whole trade layer is
    built on. A power may say *seek oil*, *sell steel to Germany*, *diversify rare earths
    away from China*. It may not say how much, may not say what it pays, and may not open a
    route to somebody it cannot reach: every column of ``trade_route`` that carries a number
    is engine-only, and `atlas.engine.intent.propose` checks the good, the counterparty and
    the reach before a row exists at all. A staff that could name the volume could write
    itself a hundred barrels a month; a staff that could name the price could buy a war.

    **Absent from a barter world's schema entirely.** The property is added only when
    `atlas.engine.pricing` is switched on -- see :func:`schema_for` -- because a world that
    cannot pay for a cargo has no business being asked what it would like to buy.
    """
    from ..engine.intent import KINDS, MAX_STANDING
    from ..engine.trade import GOODS

    return {
        "type": "array",
        "maxItems": MAX_STANDING,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "good"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": (
                        "`seek` to find a supply of the good, `sell` to find an outlet for "
                        "it, `diversify` to gain a supplier other than the one you name."
                    ),
                },
                "good": {"type": "string", "enum": list(GOODS)},
                "partner": {
                    "type": ["string", "null"],
                    "description": (
                        "The nation id you want to trade with, or -- for `diversify` -- the "
                        "one you want to depend on less. Required for `diversify`. Leave it "
                        "out of a `seek` or a `sell` and the engine finds whoever is in "
                        "reach and cheapest."
                    ),
                },
            },
        },
        "description": (
            "Standing trade intentions. The engine finds the counterparty, settles the "
            "volume and settles the price; you say what you are after. An intention it "
            "cannot act on this turn stays standing and is retried, and your briefing says "
            "what stopped it."
        ),
    }


def _trade_embargoes_property() -> dict[str, Any]:
    """What this power will stop selling, and to whom.

    **The one thing in this schema that costs the power proposing it**, and the description
    says so in as many words, because a model that does not know an action has a price
    cannot be said to have chosen it. Every other trade property asks the engine to arrange
    something; this one asks it to stop something the government is currently being paid
    for.

    Intent, not a write path. A power names a target and at most a good. It may not name
    what the embargo costs, may not name what it does to the relationship, may not embargo
    a power it does not actually supply, and may not lift one before it has stood
    `atlas.engine.embargo.MINIMUM_MONTHS`. Every column of ``trade_embargo`` is engine-only
    and `atlas.engine.embargo.impose` checks all of it before a row exists.

    **Absent from the schema entirely unless the mechanic is on** -- see
    :func:`diff_batch_schema`. `atlas.engine.embargo.enabled` is conjoined with pricing, so
    a barter world is never offered a lever whose whole content is a sum of money.
    """
    from ..engine.embargo import MAX_PER_TURN
    from ..engine.trade import GOODS

    return {
        "type": "array",
        "maxItems": MAX_PER_TURN,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "target"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["impose", "lift"],
                    "description": (
                        "`impose` to stop selling to them, `lift` to start again."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "The nation id you will stop selling to. You must actually be "
                        "exporting to them; an embargo of a trade that is not happening is "
                        "refused."
                    ),
                },
                # **Not an enum, and `atlas.state.jsonschema` is why.** An optional enum
                # is made *required* by the strict transform, with no null member, because
                # no nullable-enum spelling exists that both Codex and Antigravity accept
                # -- so an enum here would force every embargo to name one good and remove
                # "stop everything", which is the wider and more consequential of the two
                # acts. The goods are listed in the description instead, and
                # `atlas.engine.embargo.impose` refuses anything that is not one of them.
                "good": {
                    "type": ["string", "null"],
                    "description": (
                        "One of: " + ", ".join(GOODS) + ". Leave it out or pass null to "
                        "stop everything you sell them."
                    ),
                },
            },
        },
        "description": (
            "Export embargoes: trade you are choosing to stop in order to pressure "
            "somebody, at your own expense. This is not a bargain going bad -- the engine "
            "already handles that. It is a deliberate sacrifice: you lose the revenue "
            "those cargoes earn you, every month, for as long as it stands, and the power "
            "you aim it at will think worse of you for it. Where the trade you stop was "
            "guaranteed by an agreement you signed, stopping it breaks the agreement and "
            "is treated as one. Your briefing prints, for each of your customers, exactly "
            "what an embargo would cost you and how much of their consumption you cover. "
            "An embargo must stand some months before you may lift it."
        ),
    }
