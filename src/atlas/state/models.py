"""Typed mirrors of the world-state tables, plus the enums the engine switches on.

These are plain frozen dataclasses rather than an ORM. The engine reads through
purpose-built queries (see :mod:`atlas.state.visibility`) and writes only through the
diff pipeline, so there is no unit of work to manage and nothing an ORM would buy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Action",
    "Bloc",
    "City",
    "Commitment",
    "CommitmentKind",
    "CommitmentStatus",
    "Controller",
    "Difficulty",
    "DiffOp",
    "DiffStatus",
    "Event",
    "EventKind",
    "EventOrigin",
    "GATING_EVENT_KINDS",
    "Nation",
    "Provider",
    "Region",
    "RegionKind",
    "Relation",
    "Role",
    "TreatyStatus",
    "TurnPhase",
    "TurnStatus",
    "Unit",
    "UnitTemplate",
    "Visibility",
    "World",
    "WorldMode",
]


class Difficulty(StrEnum):
    VERY_EASY = "very_easy"
    EASY = "easy"
    NORMAL = "normal"
    HARD = "hard"
    IMPOSSIBLE = "impossible"


class WorldMode(StrEnum):
    PVE = "pve"
    EVE = "eve"


class TurnPhase(StrEnum):
    READ = "read"
    CONSULT = "consult"
    ACT = "act"
    NEGOTIATE = "negotiate"
    JUMP = "jump"
    RESOLVE = "resolve"
    SEALED = "sealed"


class TurnStatus(StrEnum):
    OPEN = "open"
    SEALED = "sealed"
    #: A later call failed but earlier work resolved cleanly. Never discard a turn that
    #: partially succeeded -- a partial turn is strictly better than a lost one.
    PARTIAL = "partial"
    ABORTED = "aborted"


class Provider(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    ENGINE = "engine"
    MOCK = "mock"


class Role(StrEnum):
    WORLD_SIM = "world_sim"
    DIPLOMACY = "diplomacy"
    PARSER = "parser"
    TRACKED_AGENT = "tracked_agent"
    #: The player's own council. Advisory only: it reads the player's view and answers
    #: in prose, and it is the one model-facing role that cannot write anything at all,
    #: enforced by an empty allowlist rather than by asking it not to.
    ADVISOR = "advisor"
    #: Orders the engine has no mechanic for. It narrates what happened and may nudge how
    #: the country feels about it, within caps -- and it may reach nothing else at all,
    #: which is what lets a player order something absurd without the world becoming
    #: arbitrary. See :mod:`atlas.engine.freeform`.
    FREEFORM = "freeform"
    #: An undertaking with an uncertain outcome -- a summit, a sting, a bribe --
    #: proposed by a model, rolled for by the engine, and paid for whether or not it
    #: works. Like FREEFORM it never appears as a `diff_batch.proposer_role`: it
    #: proposes and the engine writes, which is why it needed no schema change.
    #: See :mod:`atlas.engine.gambit`.
    GAMBIT = "gambit"
    #: An international body deciding what to do about something that just happened.
    #: Like FREEFORM and GAMBIT it never appears as a `diff_batch.proposer_role` -- it
    #: proposes a resolution and the engine applies the consequences, which is why the
    #: whole layer needed no change to that closed enum.
    #: See :mod:`atlas.engine.organisation`.
    ORGANISATION = "organisation"
    ENGINE = "engine"
    ARBITER = "arbiter"


class Controller(StrEnum):
    PLAYER = "player"
    ENGINE = "engine"
    CLAUDE = "claude"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"


class RegionKind(StrEnum):
    LAND = "land"
    COASTAL = "coastal"
    OCEAN = "ocean"
    STRAIT = "strait"


class Visibility(StrEnum):
    PUBLIC = "public"
    BILATERAL = "bilateral"
    SECRET = "secret"


class EventOrigin(StrEnum):
    #: Written by the applier only. Every privileged rule requires this.
    ENGINE = "ENGINE"
    #: Proposed by a model. Never sufficient to justify a gated mutation.
    MODEL = "MODEL"


class EventKind(StrEnum):
    GENESIS = "genesis"
    ORDER_ISSUED = "order_issued"
    ORDER_REJECTED = "order_rejected"
    MOVE = "move"
    BATTLE = "battle"
    SIEGE = "siege"
    TERRITORY_TRANSFER = "territory_transfer"
    CASUS_BELLI = "casus_belli"
    WAR_DECLARED = "war_declared"
    PEACE_SIGNED = "peace_signed"
    CEASEFIRE = "ceasefire"
    ALLIANCE_FORMED = "alliance_formed"
    ALLIANCE_BROKEN = "alliance_broken"
    TREATY_SIGNED = "treaty_signed"
    TREATY_BROKEN = "treaty_broken"
    TRADE_AGREEMENT = "trade_agreement"
    DIPLOMATIC_MESSAGE = "diplomatic_message"
    ULTIMATUM = "ultimatum"
    ENVOY_DISPATCHED = "envoy_dispatched"
    COMMITMENT_MADE = "commitment_made"
    COMMITMENT_BROKEN = "commitment_broken"
    COMMITMENT_FULFILLED = "commitment_fulfilled"
    ECONOMIC_REFORM = "economic_reform"
    CONSTRUCTION = "construction"
    UNIT_RAISED = "unit_raised"
    UNIT_DISBANDED = "unit_disbanded"
    UNREST = "unrest"
    REVOLUTION = "revolution"
    COUP = "coup"
    ELECTION = "election"
    LEADER_CHANGE = "leader_change"
    FAMINE = "famine"
    PLAGUE = "plague"
    DISASTER = "disaster"
    DISCOVERY = "discovery"
    ESPIONAGE = "espionage"
    SABOTAGE = "sabotage"
    NARRATIVE = "narrative"


#: Event kinds that unlock a privileged mutation and therefore must carry
#: ``origin='ENGINE'`` to count as justification. If a model could mint these itself, it
#: would emit one on turn N, cite it on turn N+1, and every gated rule would be
#: bypassable in two turns.
GATING_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.CASUS_BELLI,
        EventKind.TERRITORY_TRANSFER,
        EventKind.BATTLE,
        EventKind.SIEGE,
        EventKind.WAR_DECLARED,
        EventKind.PEACE_SIGNED,
        EventKind.ECONOMIC_REFORM,
        EventKind.UNIT_RAISED,
        EventKind.UNIT_DISBANDED,
        EventKind.REVOLUTION,
        EventKind.COUP,
    }
)


class TreatyStatus(StrEnum):
    NONE = "none"
    NON_AGGRESSION = "non_aggression"
    TRADE = "trade"
    DEFENSIVE = "defensive"
    ALLIANCE = "alliance"
    VASSAL = "vassal"


class CommitmentKind(StrEnum):
    NON_AGGRESSION_PACT = "non_aggression_pact"
    ALLIANCE = "alliance"
    TRADE_AGREEMENT = "trade_agreement"
    TERRITORY_CESSION = "territory_cession"
    MILITARY_ACCESS = "military_access"
    TRIBUTE = "tribute"
    CEASEFIRE = "ceasefire"
    PEACE_TREATY = "peace_treaty"
    DEFENSIVE_PACT = "defensive_pact"


class CommitmentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    BROKEN = "broken"
    FULFILLED = "fulfilled"
    EXPIRED = "expired"


class DiffOp(StrEnum):
    SET = "set"
    ADJUST = "adjust"
    INSERT = "insert"
    DELETE = "delete"


class DiffStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Row mirrors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class World:
    id: str
    name: str
    world_date: str
    turn_number: int
    rng_seed: int
    difficulty: Difficulty
    mode: WorldMode = WorldMode.PVE
    preset_id: str | None = None
    map_pack_id: str | None = None
    schema_version: int = 1
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class Bloc:
    id: str
    world_id: str
    name: str
    provider: Provider
    model_pin: str
    colour: str


@dataclass(frozen=True, slots=True)
class Nation:
    id: str
    world_id: str
    name: str
    colour: str
    is_player: bool = False
    controller: Controller = Controller.ENGINE
    bloc_id: str | None = None
    capital_region_id: str | None = None
    personality_blurb: str = ""
    standing_goals: tuple[str, ...] = ()
    military_strength: float = 0.0
    economy: float = 0.0
    stability: float = 50.0
    legitimacy: float = 50.0
    treasury: float = 0.0
    manpower: float = 0.0
    is_tracked: bool = False
    tracked_since_turn: int | None = None
    tracked_reason: str | None = None
    is_alive: bool = True


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    world_id: str
    name: str
    #: The region's primary shape, used for labels and as the back-compatible single
    #: reference. A region composed of several pack entries names one of them here.
    geo_key: str
    kind: RegionKind = RegionKind.LAND
    owner_nation_id: str | None = None
    controller_nation_id: str | None = None
    terrain: str = "plains"
    population: float = 0.0
    development: float = 0.0
    #: Every shape this region is drawn from. Empty means "just ``geo_key``".
    #:
    #: Natural Earth mixes administrative levels between countries -- Germany's 16 are
    #: states, Slovenia's 193 are municipalities -- so one pack entry per region would
    #: make Yugoslavia 392 regions and Germany 16, and conquering Yugoslavia would take
    #: twenty-four times longer for reasons that have nothing to do with either army.
    #: Grouping entries into regions is how a scenario picks a sane count per power.
    parts: tuple[str, ...] = ()

    @property
    def geo_keys(self) -> tuple[str, ...]:
        return self.parts or (self.geo_key,)


@dataclass(frozen=True, slots=True)
class UnitTemplate:
    world_id: str
    kind: str
    domain: str
    cost_treasury: float = 0.0
    cost_manpower: float = 0.0
    base_strength: float = 1.0
    move_range: int = 1


@dataclass(frozen=True, slots=True)
class Unit:
    id: str
    world_id: str
    nation_id: str
    region_id: str
    kind: str
    strength: float = 1.0
    morale: float = 50.0


@dataclass(frozen=True, slots=True)
class City:
    id: str
    world_id: str
    region_id: str
    name: str
    tier: int = 1


@dataclass(frozen=True, slots=True)
class Relation:
    world_id: str
    nation_a: str
    nation_b: str
    score: float = 0.0
    treaty_status: TreatyStatus = TreatyStatus.NONE
    at_war: bool = False
    since_event_id: int | None = None


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    world_id: str
    turn: int
    date: str
    kind: EventKind
    origin: EventOrigin
    actor_nation_id: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    text: str = ""
    causes: tuple[int, ...] = ()
    visible_to: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Commitment:
    id: int
    world_id: str
    kind: CommitmentKind
    status: CommitmentStatus
    created_turn: int
    condition_text: str = ""
    source_event_id: int | None = None
    resolved_turn: int | None = None
    nations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Action:
    id: int
    world_id: str
    turn: int
    nation_id: str
    raw_text: str
    parsed_json: str | None = None
    validation_status: str = "pending"
    rejection_reason: str | None = None
    event_id: int | None = None


@dataclass(frozen=True, slots=True)
class DifficultyProfile:
    """The numeric half of difficulty.

    Difficulty has to move both the prompt and the dice. Prompt-only difficulty is a
    polite suggestion a model can ignore; numbers-only difficulty produces the same
    narrative with different outcomes. Both halves are applied, always together.
    """

    difficulty: Difficulty
    #: Multiplies the player's odds in contested resolution.
    player_fortune: float = 1.0
    #: How fast relations drift toward hostility when unattended.
    relation_drift: float = 1.0
    #: Threshold above which an AI nation will act on an aggressive opportunity.
    aggression_threshold: float = 0.55
    #: Weight on "look for a plausible way this backfires" in the world-sim prompt.
    backfire_bias: float = 0.0
    #: Whether rivals actively form counter-coalitions against a leading player.
    counter_coalitions: bool = False
    prompt_clause: str = ""


DIFFICULTY_PROFILES: dict[Difficulty, DifficultyProfile] = {
    Difficulty.VERY_EASY: DifficultyProfile(
        Difficulty.VERY_EASY,
        player_fortune=1.6,
        relation_drift=0.4,
        aggression_threshold=0.80,
        backfire_bias=0.0,
        counter_coalitions=False,
        prompt_clause=(
            "Rival nations act reactively and forgivingly. Player actions succeed unless "
            "clearly implausible. Setbacks are rare, mild, and recoverable."
        ),
    ),
    Difficulty.EASY: DifficultyProfile(
        Difficulty.EASY,
        player_fortune=1.3,
        relation_drift=0.7,
        aggression_threshold=0.70,
        backfire_bias=0.1,
        counter_coalitions=False,
        prompt_clause=(
            "Rival nations are slow to take offence and quick to forgive. Player actions "
            "usually succeed. Setbacks are recoverable."
        ),
    ),
    Difficulty.NORMAL: DifficultyProfile(
        Difficulty.NORMAL,
        player_fortune=1.0,
        relation_drift=1.0,
        aggression_threshold=0.55,
        backfire_bias=0.25,
        counter_coalitions=False,
        prompt_clause=(
            "Rival nations pursue their own self-interest without special hostility. "
            "Outcomes follow realistic probability, including partial success and "
            "unintended consequences."
        ),
    ),
    Difficulty.HARD: DifficultyProfile(
        Difficulty.HARD,
        player_fortune=0.8,
        relation_drift=1.3,
        aggression_threshold=0.42,
        backfire_bias=0.55,
        counter_coalitions=True,
        prompt_clause=(
            "Rival nations actively pursue their strategic interests, form counter-"
            "coalitions against the strongest power, and exploit the player's mistakes. "
            "Look for plausible ways the player's actions backfire rather than defaulting "
            "to success."
        ),
    ),
    Difficulty.IMPOSSIBLE: DifficultyProfile(
        Difficulty.IMPOSSIBLE,
        player_fortune=0.6,
        relation_drift=1.6,
        aggression_threshold=0.30,
        backfire_bias=0.8,
        counter_coalitions=True,
        prompt_clause=(
            "Rival nations are ruthless, coordinated, and opportunistic. They form "
            "counter-coalitions early and punish every overextension. Actively seek the "
            "most plausible way each player action backfires; success should be earned, "
            "never assumed."
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class Digest:
    id: int
    world_id: str
    turn_from: int
    turn_to: int
    level: int
    summary_text: str
    scope_nation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """One recorded routing choice, so a silent misroute is detectable after the fact."""

    role: Role
    provider: Provider
    requested_model: str
    band: str
    complexity: float
    served_model: str | None = None
    audit_verdict: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
