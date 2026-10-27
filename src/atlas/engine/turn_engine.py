"""The turn loop: read, act, negotiate, jump, resolve.

Two properties matter more than anything else here.

**A turn that partially succeeded is never discarded.** If the world simulation resolves
and then a tracked-nation call fails, the resolved work is kept and the turn is marked
``partial``. Throwing it away to keep the code simple would mean the player loses a jump --
and the expensive calls that produced it -- because of a failure in an optional extra.

**Batches are validated against fresh state, never resumed against stale state.** Each
batch is generated from a snapshot of the world. Once one applies, the snapshot the later
ones were built against is out of date, which is why every diff carries
``expected_prior``: a conflict is detected and the phase re-runs rather than silently
overwriting. Resuming from batch *k* after a failure would apply proposals reasoned about
under conditions that no longer hold.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta

from ..providers.attempts import RETRY_DELAYS, invoke_with_retries
from ..providers.base import ModelProvider, ProviderRequest, ProviderResult
from ..providers.cli_provider import WORLD_SIM_FALLBACK
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import (
    FORMATION_ORDER_LIMIT,
    FORMATIONS_PER_ORDER,
    OBJECTIVE_LIMIT,
    diff_batch_schema,
    parse_batch,
)
from ..state.models import (
    Difficulty,
    EventKind,
    EventOrigin,
    Role,
    TurnPhase,
    TurnStatus,
    Visibility,
)
from . import bots, refusals
from .answering import assess_unanswered_calls
from .armistice import assess_armistices
from .attribution import assess_attribution
from .budget import collect_revenue, erode_development
from .capitulation import assess_capitulation
from .causality import audience_of, breakdown_of
from .concert import award_leadership
from .constituencies import assess_constituencies
from .decolonisation import assess_independence, decolonise
from .digest import consolidate
from .doctrines import settle_shifts
from .embargo import MAX_PER_TURN as EMBARGO_LIMIT
from .embargo import describe as describe_embargo
from .embargo import impose as impose_embargo
from .embargo import lift as lift_embargo
from .entrench import drift_entrenchment
from .exposure import assess_exposure
from .focus import apply_focus, tracked_nations
from .freeform import (
    FREEFORM_SCHEMA,
    MAX_ORDERS_PER_TURN,
    PER_FIELD_TURN_CAP,
    apply_freeform,
    build_freeform_view,
    freeform_orders,
    freeform_overflow,
)
from .gambit import GAMBIT_SCHEMA, MAX_GAMBITS_PER_TURN, apply_gambit, build_gambit_view
from .intelligence import assess_networks
from .intent import MAX_STANDING as INTENT_LIMIT
from .intent import describe as describe_intent
from .intent import propose as propose_intent
from .intent import pursue as pursue_intents
from .interrupt import DEFAULT_WATCH, Trigger, triggers_in
from .leaders import assess_shocks, assess_successions, shock_assassination
from .logistics import assess_attrition
from .mediation import mediate_war
from .mobilisation import drift_mobilisation, set_level
from .morale import drift_morale
from .nuclear import drift_readiness
from .objectives import declare_objectives, objectives_of, prune_objectives
from .orders import apply_order_effects
from .organisation import (
    ORGANISATION_SCHEMA,
    apply_sanctions,
    build_session_view,
    convene,
    has_jurisdiction,
    organisations_of,
    triggers_this_turn,
)
from .pacing import rounds, span_of
from .peace import honour_peace_terms, offer_peace
from .production import advance_construction, autonomous_investment
from .progress import ProgressReporter, StageState
from .resolution import reconcile_self_sieges, resolve_turn
from .routing import (
    Band,
    Breaker,
    Router,
    RoutingDecision,
    jump_complexity,
    log_decision,
)
from .scoring import assess_victory
from .strategy import review_world as strategy_review
from .support import declare_support, move_funding
from .trade import assess_trade
from .treaties import (
    assess_breaches,
    expire_commitments,
    honour_terms,
    settle_fulfilled,
)
from .unrest import assess_partisans, assess_unrest
from .views import build_tracked_agent_view, build_world_sim_view
from .waraims import declare_war_aims, prune_war_aims
from .wareffort import assess_war_effort
from .warpath import may_declare

__all__ = ["JumpReport", "TurnEngine"]

#: **Balance number.** What every uninvolved power thinks of a country that has just opened a
#: war. Small, and paid to all of them at once: no single power minds very much, and a
#: government that keeps doing it finds the room colder every time.
_DECLARATION_REGARD = -1.5

#: Backoff between retries of a retryable provider failure. **Moved to
#: :mod:`atlas.providers.attempts` so there is one policy rather than one per caller**
#: -- EVE had no retry at all, which cost whole blocs their turns in live matches, and
#: a second copy of this tuple would have been a second thing to keep in step.
_RETRY_DELAYS = RETRY_DELAYS


@dataclass(slots=True)
class DeclarationOutcome:
    """What became of one power's request to open a war."""

    #: One line, already written for the turn report.
    line: str
    declared: bool = False
    target: str = ""
    problems: list[str] = field(default_factory=list)


def declare_for(
    conn: sqlite3.Connection, world_id: str, nation_id: str, wanted: dict, *,
    turn: int, date: str,
) -> DeclarationOutcome:
    """Let a power open a war, if `warpath` will have it.

    **Through `resolution._declare_war`, which is the same function the player's order and
    the simulator's declarations both use.** A second way to start a war is how one of them
    ends up skipping the defensive pacts, and the pacts are most of what makes a declaration
    frightening -- and, for the kitchen-sink study, most of what makes one a *betrayal*: a
    declaration on a treaty partner is what `treaties.assess_breaches` reads as broken.

    Shared by the turn engine's tracked powers and, when a match opts in, by EVE's blocs.
    The diplomatic cost is charged whatever the grievance.
    """
    from .naming import resolve_nation
    from .resolution import ResolutionReport, _declare_war

    target = resolve_nation(conn, world_id, wanted.get("target"))
    if not target:
        said_no = f"no power called {wanted.get('target')!r} to declare war on"
        # **A declaration refused is news to the staff that tried it.** `declare_war` is
        # the single most expensive field on the form and a power that keeps aiming it at a
        # country the map has never heard of spends its heaviest decision on nothing.
        refusals.record(
            conn, world_id, nation_id=nation_id, turn=turn,
            channel="declare_war", reason=said_no)
        return DeclarationOutcome(line=f"{nation_id}: {said_no}")

    verdict = may_declare(
        conn, world_id, aggressor=nation_id, target=target, turn=turn, date=date)
    if not verdict.allowed:
        refusals.record(
            conn, world_id, nation_id=nation_id, turn=turn,
            channel="declare_war",
            reason=f"you did not declare war on {target}: {verdict.because}")
        return DeclarationOutcome(
            line=f"{nation_id} did not declare war on {target}: {verdict.because}",
            target=target)

    said = ResolutionReport()
    _declare_war(
        conn, world_id, turn=turn, date=date, aggressor=nation_id,
        target_value=target, report=said,
        justification=(str(wanted.get("justification") or "").strip()
                       or verdict.grounds),
    )
    outcome = DeclarationOutcome(line="", target=target, problems=list(said.problems))
    if said.wars_declared:
        outcome.declared = True
        outcome.line = f"{nation_id} declared war on {target}: {verdict.grounds}"
        charge_for_declaring(conn, world_id, nation_id, target, turn=turn, date=date)
    conn.commit()
    return outcome


def charge_for_declaring(
    conn: sqlite3.Connection, world_id: str, aggressor: str, target: str, *,
    turn: int, date: str,
) -> None:
    """What opening a war costs a power with everybody who was not asked.

    Small per rival and paid to all of them at once, which is the shape the exposure term in
    `support` already uses: no single power minds very much, and a government that keeps
    doing it finds the room colder every time.
    """
    from ..state.apply import apply_batch
    from ..state.diff import Diff, DiffBatch
    from ..state.models import DiffOp, Role, Visibility

    event = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'war_declared'"
        "   AND actor_nation_id = ? ORDER BY id DESC LIMIT 1",
        (world_id, aggressor)).fetchone()
    if event is None:
        return
    others = [
        str(row["nation_b"]) for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b <> ? AND at_war = 0 ORDER BY nation_b",
            (world_id, aggressor, target))
    ]
    if not others:
        return
    diffs = tuple(
        Diff("relation", f"{other}|{aggressor}", "score", DiffOp.ADJUST,
             _DECLARATION_REGARD, reason_event_id=int(event["id"]),
             visibility_scope=Visibility.PUBLIC)
        for other in others
    )
    apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=diffs))


@dataclass(slots=True)
class _BotAnswer:
    """What a bot hands back in the place a `ProviderResult` would sit.

    Only the three attributes the call sites read. **Never failed**: a bot has no quota, no
    timeout and no transport, so the only way its answer fails is at the gates, which is
    where a model's answer fails too.
    """

    data: dict
    text: str = ""
    failed: bool = False
    failure: None = None


def _note_accepted(
    report: JumpReport, role: Role, *, answered: bool = False, failed: bool = False,
    batch=None, outcome=None,
) -> None:
    """Fold one call, or one batch, into the role's accepted-output tally.

    Called at both ends deliberately: a provider failure never reaches an `apply_batch`, so
    counting only batches would score a role that crashed every turn as having no rejected
    output at all -- which is precisely the confusion between *answered* and *accepted* this
    exists to prevent.
    """
    tally = report.accepted.setdefault(role.value, Accepted())
    if answered:
        tally.answered += 1
    if failed:
        tally.failed += 1
    if batch is None or outcome is None:
        return
    tally.batches += 1
    tally.diffs_proposed += len(batch.diffs)
    if outcome.applied:
        tally.batches_applied += 1
        tally.diffs_severed += len(getattr(outcome, "severed", ()) or ())
    else:
        tally.batches_refused += 1
        tally.diffs_severed += len(batch.diffs)


def _note_refusals(report: JumpReport, outcome, who: str) -> None:
    """Record the diffs a partly-applied batch lost.

    A partial apply reports ``applied=True``, so without this the refused writes vanish:
    the turn shows a successful batch and nothing about the three changes the rules threw
    out of it. Which rule caught each one is carried through verbatim, because "3 changes
    were dropped" is not something a player can act on and "[CORE-001] cited event 987654
    does not exist" is.
    """
    for _seq, rule_id, reason in getattr(outcome, "refusals", ()):
        report.refused_diffs.append(f"{who}: [{rule_id}] {reason}")


def _by_place(starved: list[str]) -> list[tuple[str, int]]:
    """Group starvation lines by the pocket they are about, keeping the engine's order.

    A line reads ``"Ukraine in Khmel'nyts'kyy is cut off and going short (2.3 -> 2.1)"``.
    The part before the numbers names *who is cut off where*, which is the fact; the numbers
    are one formation's share of it. Returns ``(first line, how many others)``.
    """
    order: list[str] = []
    seen: dict[str, list[str]] = {}
    for entry in starved:
        place = entry.split(" (")[0]
        if place not in seen:
            seen[place] = []
            order.append(place)
        seen[place].append(entry)
    return [(seen[place][0], len(seen[place]) - 1) for place in order]


def _air_lines(
    conn, world_id: str, resolution, *, viewer: str | None
) -> list[str]:
    """This turn's air war, as lines a player can act on. Read-only.

    **Three things reach a summary and the rest does not.** A sky changing hands, a sky
    being let go of, and interdiction *beginning* -- because those are the three moments the
    condition under which the next month is fought changes. How many contests were rolled is
    telemetry, and it is kept to one trailing line so a turn that fought an air war nowhere
    visible still says it did.

    Written from the viewer's side where there is one. "You took the sky over the Narrow Sea"
    and "Karthos took the sky over the Narrow Sea" are the same engine fact and completely
    different news, and a report that phrased both as the third person would make the player
    work out which of them applied to their own war.

    Nothing here writes; it reads the outcome the resolution phase already produced and the
    names behind the ids.
    """
    if not resolution.air_battles and not resolution.skies and not resolution.skies_lapsed:
        return []

    def name_of(table: str, key: str) -> str:
        row = conn.execute(
            f"SELECT name FROM {table} WHERE id = ?", (key,)).fetchone()  # noqa: S608
        return str(row["name"]) if row else key

    lines: list[str] = []
    mine, theirs = [], []
    for region_id, winner in resolution.skies:
        where = name_of("region", region_id)
        if viewer and winner == viewer:
            mine.append(f"you took the sky over {where}")
        else:
            theirs.append(f"{name_of('nation', winner)} took the sky over {where}")
    # The player's own first, and each side capped: a front where six skies changed hands is
    # one piece of news about a campaign, not six about six provinces. The same reasoning
    # `_by_place` already applies to pockets.
    lines.extend(mine[:2])
    lines.extend(theirs[:2])
    if len(mine) + len(theirs) > 4:
        lines.append(f"{len(mine) + len(theirs)} skies changed hands in all")

    if resolution.skies_lapsed:
        lines.append(
            f"{resolution.skies_lapsed} sky/skies were let go of — air superiority is "
            "maintained, not owned, and nobody flew over them")

    # **Interdiction beginning, which is the one effect nothing anywhere ever announced.**
    # Derived rather than reported by the air layer: a sky changing hands over ground the
    # player controls *is* an interdiction starting, and it costs two hops of a three-hop
    # supply budget from this turn on.
    if viewer:
        started = [
            name_of("region", region_id)
            for region_id, winner in resolution.skies
            if winner != viewer and _controls(conn, world_id, viewer, region_id)
        ]
        if started:
            lines.append(
                "your supply through " + ", ".join(started[:3])
                + " is now interdicted: two extra hops of a three-hop budget, each")

    if not lines and resolution.air_battles:
        lines.append(
            f"{resolution.air_battles} sky/skies were contested and none changed hands")
    return lines


def _controls(conn, world_id: str, nation_id: str, region_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM region WHERE world_id = ? AND id = ?"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ? AND kind <> 'ocean'",
        (world_id, region_id, nation_id)).fetchone()
    return row is not None


@dataclass(slots=True)
class Accepted:
    """How much of what one role proposed the engine actually took.

    **This is not provider success, and keeping the two apart is the whole point.** A
    provider that answers every call and proposes diffs the rules refuse is *worse* than one
    that fails loudly: the turn looks healthy, the call is paid for, and the world does not
    move. The comparison that first re-pinned the world simulation could not tell those
    apart, because the only number it had was latency.

    So `answered` and `failed` describe the *provider*, and everything below the line
    describes what the engine did with the answer. A role's accepted-output rate is the
    second, and it is the one Alex's bar is written against: revert unless agy's **output**
    is materially worse.

    `severed` is diffs the rules threw out of a batch that otherwise stood -- a partial
    apply reports as applied, so without counting them a batch that lost half its writes
    looks exactly like one that lost none.
    """

    #: Provider-side. A call that came back with something parseable at all.
    answered: int = 0
    #: Provider-side. A call that failed -- crash, timeout, empty body, quota.
    failed: int = 0
    #: Engine-side, from here down.
    batches: int = 0
    batches_applied: int = 0
    batches_refused: int = 0
    diffs_proposed: int = 0
    diffs_severed: int = 0

    @property
    def diffs_applied(self) -> int:
        return max(0, self.diffs_proposed - self.diffs_severed) if self.batches_applied else 0

    @property
    def accepted_rate(self) -> float | None:
        """Share of proposed diffs the engine kept. ``None`` when nothing was proposed.

        ``None`` rather than 1.0: a role that proposed nothing has no accepted-output rate,
        and scoring it perfect would let a silent provider outrank a productive one.
        """
        if not self.diffs_proposed:
            return None
        return self.diffs_applied / self.diffs_proposed

    def describe(self) -> str:
        rate = self.accepted_rate
        return (
            f"{self.answered} answered / {self.failed} failed; "
            f"{self.batches_applied}/{self.batches} batch(es) stood; "
            f"{self.diffs_applied}/{self.diffs_proposed} diff(s) accepted"
            + (f" ({rate:.0%})" if rate is not None else "")
        )

@dataclass(slots=True)
class JumpReport:
    """What happened during one time jump, including what did not."""

    turn: int
    status: TurnStatus
    days_advanced: int
    new_date: str
    applied_batches: int = 0
    rejected_batches: int = 0
    events_recorded: int = 0
    rejections: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    calls: int = 0
    #: Answers a deterministic bot gave instead of a model -- see `atlas.engine.bots`.
    #: Counted apart from `calls` so that `calls` stays what it always was: model calls.
    bot_decisions: int = 0
    orders_executed: int = 0
    contests: int = 0
    regions_occupied: int = 0
    wars_declared: int = 0
    wars_ended: int = 0
    #: Wars that ended because one side had no ground left to fight from. Its own list
    #: rather than a line among the peace headlines, because a capitulation is not a
    #: settlement: nobody agreed to it and there are no terms.
    capitulated: list[str] = field(default_factory=list)
    #: Nations that neither hold nor own any territory and have been marked not alive.
    eliminated: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    tracked_acted: list[str] = field(default_factory=list)
    #: Tracked powers that answered well and had nothing they wanted to do. Kept apart
    #: from failures: a quiet month is not an error, and reporting it as one marked every
    #: turn of a peaceful world partial.
    tracked_quiet: list[str] = field(default_factory=list)
    #: Tracked powers that set military objectives of their own this turn. Kept apart
    #: from ``tracked_acted`` because it is the sharper claim: not "this nation returned
    #: something" but "this nation decided what to do with its army".
    objectives_declared: list[str] = field(default_factory=list)
    #: Powers that began changing what they are built to do, and powers whose change
    #: landed. The slowest decision in the game, so it is reported at both ends.
    doctrine_shifts: list[str] = field(default_factory=list)
    doctrines_settled: list[str] = field(default_factory=list)
    #: Constituencies that changed their mind about their government this turn. A band
    #: change is news; four tenths of a point is not.
    at_home: list[str] = field(default_factory=list)
    #: Wars a power ended itself this turn, by offering terms the other side accepted.
    #: The sharpest claim in the whole loop: not "a war ended" but "a country decided to
    #: stop, and the engine agreed the other side would take it".
    peace_offers: list[str] = field(default_factory=list)
    #: Powers whose patience with a war crossed a band this turn -- resolute to straining,
    #: war-weary to exhausted. A band change is news; four tenths of a point is not.
    war_weariness: list[str] = field(default_factory=list)
    #: What each power now says it is fighting *for*, as one line per war restated. The
    #: sharper claim again: not "this nation is at war" but "this nation named terms that
    #: would end it".
    war_aims_declared: list[str] = field(default_factory=list)
    #: Tracked powers that laid down a public work or programme of their own this turn.
    #: The economic twin of ``objectives_declared``: proof the AI world is developing
    #: itself rather than standing frozen while only the human builds.
    tracked_built: list[str] = field(default_factory=list)
    #: Tracked powers that put new formations in the field this turn. The military twin of
    #: ``tracked_built``, and the answer to the question the Ukraine play-test asked: an AI
    #: army that only ever shrinks loses every long war to arithmetic, whoever is right.
    tracked_raised: list[str] = field(default_factory=list)
    #: Tracked powers that sent replacements to formations already in the line. Separate
    #: from ``tracked_raised`` because they answer different questions: that one says an
    #: army grew, this one says an army was *held together*, and a war of attrition is won
    #: or lost on the second.
    tracked_reinforced: list[str] = field(default_factory=list)
    #: Powers whose war footing changed this turn, one line each. Named rather than counted
    #: because a general mobilisation is news in a way a workshop is not.
    mobilised: list[str] = field(default_factory=list)
    #: Wars a tracked power opened on its own account this turn, one line each, and the
    #: refusals with them -- a declaration the engine turned down is news to the staff that
    #: tried it.
    declared_wars: list[str] = field(default_factory=list)
    #: Powers the world warmed toward this turn for leading it. Named rather than counted:
    #: who is leading the order is the whole substance of the concert victory.
    led_the_order: list[str] = field(default_factory=list)
    #: Which governments changed what they were playing for, and to what.
    strategy_changes: list[str] = field(default_factory=list)
    #: How many unattended powers (no human, no tracked agent) the master AI had invest in
    #: their own development this turn. A count, not a list: fifty minors laying down
    #: workshops is a healthy world, not fifty lines the player needs to read.
    world_investments: int = 0
    #: Ships and formations that came out of the yards this turn.
    commissioned: list[str] = field(default_factory=list)
    #: What the world made of orders no mechanic covers, in its own words.
    freeform: list[str] = field(default_factory=list)
    #: Undertakings a nation attempted this turn and how they went. Kept apart from
    #: ``freeform`` because these were *rolled for* and cost something.
    gambits: list[str] = field(default_factory=list)
    #: Nations that attempted nothing, and why -- their own judgement that this turn was
    #: not the turn, or the engine's refusal with the binding limit named. Deciding not to
    #: gamble is a *move*, and a report that shows only the attempts makes a cautious
    #: staff look like a broken subsystem. It is also the only place a refusal is visible
    #: from outside ``jump()``, which is what a run auditing the channel has to read.
    gambits_declined: list[str] = field(default_factory=list)
    #: What an international body resolved this turn, and what it cost its standing. Its
    #: own list because a Council being vetoed is news of a different kind from a summit.
    sessions: list[str] = field(default_factory=list)
    #: Powers paying for a standing measure this turn.
    sanctions: list[str] = field(default_factory=list)
    #: Powers a defensive pact handed a casus belli this turn, because somebody attacked
    #: an ally of theirs. Not a declaration -- each of them still chooses.
    pacts_invoked: list[str] = field(default_factory=list)
    #: Nuclear weapons used this turn, first strike and reply alike. Its own list and its
    #: own heading rather than a line among the undertakings, because a turn in which this
    #: is non-empty is not a turn like the others and the report should not read as if it
    #: were.
    nuclear: list[str] = field(default_factory=list)
    #: Powers that could not meet their upkeep this turn.
    in_arrears: list[str] = field(default_factory=list)
    #: Covert operations that came out this turn, with what the scandal was worth. Its own
    #: field rather than a line among the betrayals: an exposure is public by construction
    #: and carries a magnitude a reader can weigh, which is not true of anything else here.
    exposed: list[str] = field(default_factory=list)
    #: What a government's own country cost it this turn: strikes in the revenue, yards
    #: working to rule. **Its own field rather than a line among the arrears**, because the
    #: two have opposite answers -- a bankrupt government must find money and a struck one
    #: must find something to give the people, and a report that ran them together would
    #: send a player to fix the wrong thing.
    friction: list[str] = field(default_factory=list)
    #: Powers whose stability was dragged down this turn by the foreign ground they hold.
    #: The standing cost of conquest: proof that annexing a continent is not free.
    restive: list[str] = field(default_factory=list)
    #: Provinces that rose against their occupier this turn and reverted to home control.
    #: Where ``restive`` is unrest as a stability cost, this is unrest as a map cost.
    rebelled: list[str] = field(default_factory=list)
    #: Standing objectives the map overtook this turn, so a staff is told its order
    #: lapsed rather than silently discovering it next month.
    lapsed: list[str] = field(default_factory=list)
    #: What the fighting did, in the words resolution already wrote for it. Without this
    #: an offensive reads as a number of clashes and nothing else.
    front: list[str] = field(default_factory=list)
    #: **The war above the war.** The air campaign is fought every turn, unordered and
    #: undeclinable, and until now its per-turn outcome reached no surface at all:
    #: `ResolutionReport.skies_taken` and `air_battles` were computed and read by nothing.
    #: A sky changing hands is worth up to a tenth of every battle fought under it, more
    #: than twice that over water, and two extra hops to supply anything through it -- so a
    #: turn summary that omits it is a summary that leaves the player to infer the strongest
    #: circumstantial modifier in the game from its consequences.
    #:
    #: Its own list rather than lines in `front`, because these are not battles at the
    #: front: they are the condition the front is fought in.
    air: list[str] = field(default_factory=list)
    retreated: int = 0
    encircled: int = 0
    #: Governments removed this turn. Its own list rather than a line among the events,
    #: because a country changing hands at the top is news of a different kind from a
    #: country changing hands at the border.
    fell: list[str] = field(default_factory=list)
    #: Governments killed this turn by a shock the engine was told to fire, kept apart
    #: from ``fell`` on purpose: a fall is a country's own numbers catching up with it
    #: and a killing is not, so folding the two together would make an instrument's
    #: intervention indistinguishable from the world's own behaviour in every report
    #: and every measurement downstream.
    shocks: list[str] = field(default_factory=list)
    #: Individual changes refused inside a batch that otherwise stood. **Without this a
    #: partial apply is invisible**: the batch reports as applied, and the writes the
    #: rules threw out disappear from the turn entirely. Kept apart from ``rejections``,
    #: which is whole proposals refused -- one is a proposal that failed and the other is
    #: a proposal that mostly worked, and a report that conflates them is telling the
    #: player the wrong thing about their models.
    refused_diffs: list[str] = field(default_factory=list)
    #: Engine-accepted output, per role. **Distinct from provider success**, which is what
    #: `calls` and `failures` already carry -- see :class:`Accepted`. Keyed by role value so
    #: a recorded run can compare one provider's world simulation against another's without
    #: the comparison collapsing into "did the call come back".
    accepted: dict[str, Accepted] = field(default_factory=dict)
    #: Victory conditions met this turn, in the words the player reads. Meeting one does
    #: not end the game -- see :mod:`atlas.engine.scoring` for why -- so this is news the
    #: report carries rather than a terminal state the engine enters.
    victories: list[str] = field(default_factory=list)
    #: Formations that went short this turn for want of a supply line. Its own list because
    #: an army starving in a pocket is a different piece of news from an army losing a
    #: battle, and the front lines only ever report the second.
    starved: list[str] = field(default_factory=list)
    #: Trade routes that failed to deliver this turn, and why. Kept apart from `sanctions`,
    #: which is the measure being imposed: this is the measure *biting*, and a player needs
    #: to see the second to understand the first.
    trade_cut: list[str] = field(default_factory=list)
    #: Nations whose industry could not get what it needed this turn.
    short_of: list[str] = field(default_factory=list)
    #: Cargo that started or stopped moving because an agreement said so. Kept apart from
    #: ``trade_cut``, which is a standing route failing to deliver: this is a route coming
    #: into or going out of existence, which is a diplomatic fact rather than a military
    #: one -- and until agreements could open routes at all, it was a fact nothing in the
    #: engine could produce.
    trade_opened: list[str] = field(default_factory=list)
    #: Standing routes whose terms the engine re-settled this turn, each carrying the price
    #: *and the reason it moved* -- `atlas.engine.pricing.explain`, the same sentence the
    #: route itself would give. Its own list because a renegotiation is a thing that
    #: happened and not a failure: `trade_cut` is a lane that stopped delivering, and a
    #: player who saw "the oil got dearer" filed under that would read a price rise as a
    #: blockade. **The reasons were computed and discarded before this existed** -- pricing
    #: built the `because` strings every time it re-priced a route and nothing drained them,
    #: so the one part of the layer written to be read by a person never reached one.
    trade_repriced: list[str] = field(default_factory=list)
    #: Standing routes whose *seller* declined this turn's shipment. Its own list beside
    #: `trade_cut` and `trade_repriced` for the same reason those are apart from each
    #: other: a lane a fleet is stopping, a price that moved, and a supplier that would
    #: rather keep the cargo are three different pieces of news, and a player who read the
    #: third as the first would send a navy at a decision. Empty unless
    #: :mod:`atlas.engine.refusal` is switched on.
    trade_withdrawn: list[str] = field(default_factory=list)
    #: Standing routes whose *buyer* stopped taking the cargo, because the terms are above
    #: what it will pay. The other half of `trade_withdrawn`, and its own list for the same
    #: reason: a supplier that would rather keep the cargo and a customer that will not pay
    #: for it are two different decisions with two different remedies, and a government told
    #: only that "the oil stopped" cannot tell which of them to go and fix. Empty unless
    #: :mod:`atlas.engine.consent` is switched on.
    trade_refused: list[str] = field(default_factory=list)
    #: Routes that had stopped and are moving again. **Ranked with the news rather than
    #: filed under housekeeping**, because a world that only ever reports things getting
    #: worse is a world a player stops reading: somebody who was told the oil stopped has
    #: to be told when it starts.
    trade_resumed: list[str] = field(default_factory=list)
    #: What a government's purchasing policy did this turn: an intention adopted, and a
    #: route the engine opened chasing one -- see :mod:`atlas.engine.intent`. Kept apart
    #: from `trade_opened`, which is an agreement being honoured: one is a treaty taking
    #: effect and the other is a purchasing ministry doing its job, and only the first is a
    #: diplomatic fact.
    trade_arranged: list[str] = field(default_factory=list)
    #: Export embargoes imposed and lifted this turn, and the lanes one is stopping.
    #: **Its own list, ranked with the diplomatic news rather than with the shipping**,
    #: which is the whole distinction `atlas.engine.embargo` exists to draw: `trade_cut` is
    #: a lane a fleet or a price stopped, `trade_refused` is a bargain that stopped paying,
    #: and this is a government spending its own money to coerce another. A player who read
    #: the third under either of the first two would go looking for a market explanation of
    #: an ultimatum. Empty unless :mod:`atlas.engine.embargo` is switched on.
    trade_embargoed: list[str] = field(default_factory=list)
    #: Agreements torn up this turn, and by whom. Its own list and ranked high: a betrayal
    #: is the most consequential thing a diplomatic system produces, and burying it among
    #: the events would be the reporting equivalent of the bug that made pacts inert.
    betrayed: list[str] = field(default_factory=list)
    #: Agreements whose term simply ran out. **Kept apart from a betrayal on purpose** --
    #: showing them together teaches a player to distrust a power that did nothing wrong.
    lapsed_treaties: list[str] = field(default_factory=list)
    #: One line per patron that opened, raised or ended its backing of somebody.
    support_changed: list[str] = field(default_factory=list)
    #: ...and per dependency that became a sovereign state this turn.
    freed: list[str] = field(default_factory=list)
    #: What became of each order the player gave, tied to the words that produced it. The
    #: event log says what happened; this says which of the player's sentences it happened
    #: *to*, which is the difference between knowing something failed and knowing what to
    #: write instead.
    order_outcomes: list[str] = field(default_factory=list)
    #: Why the player's own numbers moved, one line per field. Derived from the diffs'
    #: cited events, so it cannot disagree with the log it summarises.
    why_it_moved: list[str] = field(default_factory=list)
    #: Objectives that were **accepted** and are waiting for the front to reach them --
    #: *"borders none of your territory yet; the objective stands"*. Kept apart from
    #: ``rejections``, where they used to be filed, and the distinction is not cosmetic: a
    #: deferred objective is the *accepted* path, and calling it a refusal told the turn
    #: report, the player and the laboratory that a staff had been turned down when it had
    #: been agreed with. PT31's `impossible-objectives` detector fired on exactly this and
    #: had to be taught to ignore the phrase, which treated the symptom.
    objectives_deferred: list[str] = field(default_factory=list)
    #: Tracked powers at war that described a month their orders do not back: they wrote
    #: an account of themselves and, by the state of the map, did nothing -- nothing
    #: declared to take, nothing deployed off their own ground, and no contest either way.
    #:
    #: **A power's prose and a power's orders are two different channels and only one of
    #: them binds.** Krell's staff wrote *"the fleet maintains the battle line in the
    #: Narrow Sea"* for three consecutive months of the showcase while its fleet sat in
    #: port, because the objective it kept declaring was on open water and was correctly
    #: refused every time -- so the account was published, quoted back in every later
    #: briefing, and nothing anywhere said the orders behind it had been turned down.
    #:
    #: The narrative is deliberately **not** constrained or rewritten. Narration is the
    #: model's job in this design and always has been; what was missing is the other half
    #: of the record. See ``_note_unbacked_narrative``.
    narrative_unbacked: list[str] = field(default_factory=list)
    #: The nation this report is read by -- the player -- or ``None`` for the referee.
    viewer: str | None = None
    #: **Lines only some nations may read**, each with the nations that may. A tracked
    #: power's covert backing and its covert undertakings are SECRET events, and they were
    #: copied into this report as plain strings and shown to the player; so were the
    #: refusals of what other powers *tried*. The report is still the referee's whole
    #: record -- the laboratory and the console read every field -- so a line is tagged
    #: with its audience where it is written rather than filed somewhere else, and the
    #: player's surfaces (:meth:`significant` and the turn overlay) go through
    #: :meth:`for_viewer`. A line absent from here is public, as every line used to be.
    restricted: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return self.status is TurnStatus.PARTIAL

    def restrict(self, lines, audience: frozenset[str] | None) -> None:
        """Tag ``lines`` as readable by ``audience`` only. ``None`` means public: no-op.

        Tagging the same line twice keeps the union, so a line two powers may each read is
        not hidden from either of them. Keyed by the text: every copy of one sentence shares
        one audience, which is why the lines tagged here are written with their author's
        name in front.
        """
        if audience is None:
            return
        for line in lines:
            self.restricted[line] = self.restricted.get(line, frozenset()) | audience

    def for_viewer(self, viewer: str | None) -> JumpReport:
        """This report as ``viewer`` may read it. ``None`` is the referee and reads it all.

        Every list of lines is filtered through :attr:`restricted`; the counts are left
        alone, because a count of "1 war declared" was already a public fact and the lines
        tagged here are never the ones a count is taken from.
        """
        def shown(entry) -> bool:
            if viewer is None or not isinstance(entry, str):
                return True
            audience = self.restricted.get(entry)
            return audience is None or viewer in audience

        return replace(self, restricted={}, **{
            spec.name: [entry for entry in value if shown(entry)]
            for spec in fields(self)
            if isinstance(value := getattr(self, spec.name), list)
        })

    def significant(self) -> list[str]:
        """What actually happened this turn, gravest first.

        **The situation report used to be the first eight events by row id**, so a nuclear
        exchange, a Security Council session and a minor laying down workshops competed on
        equal footing and were ordered by insertion. Meanwhile the report carried every one
        of those facts in its own structured field and showed none of them.

        Gravity is assigned here rather than in the interface, for the same reason a scene
        description is: how grave a turn's facts are is a property of the turn, not of the
        widget that draws it. `describe()` is deliberately left alone -- it is the console
        dump and its job is to be exhaustive in a fixed order, which is a different job.
        The numbers are ordinal only; nothing reads them except the sort.

        Long lists are truncated where they are summarised rather than being allowed to
        flood the report: fifty sanctioned minors is one fact, not fifty lines.

        Read as :attr:`viewer` may read it, so a secret line never reaches the ranking --
        filtered *before* the truncation, so it cannot take a slot from a visible one either.
        """
        if self.restricted:
            return self.for_viewer(self.viewer).significant()
        ranked: list[tuple[int, str]] = []

        for entry in self.nuclear:
            ranked.append((100, entry))
        for entry in self.victories:
            ranked.append((95, entry))
        for entry in self.betrayed:
            ranked.append((92, entry))
        if self.wars_declared:
            ranked.append((90, f"{self.wars_declared} war(s) declared"))
        if self.wars_ended:
            ranked.append((88, f"{self.wars_ended} war(s) ended"))
        # Above the count, because "Ukraine could not go on against Russia" is the news
        # and "1 war(s) ended" is the accounting.
        for line in self.capitulated:
            ranked.append((94, line))
        for line in self.eliminated:
            ranked.append((96, f"{line} has ceased to exist"))
        for entry in self.fell:
            ranked.append((85, entry))
        # Above an ordinary fall, because it is rarer and because a reader who sees
        # only one line about this country should see the one that says why.
        for entry in self.shocks:
            ranked.append((86, entry))
        # A colony becoming a country is the same order of fact as a government falling,
        # and it is the rarer of the two.
        for entry in self.freed:
            ranked.append((84, entry))
        for entry in self.rebelled:
            ranked.append((80, f"partisans took back {entry}"))
        for entry in self.pacts_invoked:
            ranked.append((78, f"a defensive pact freed {entry} to act"))
        for entry in self.sessions:
            ranked.append((75, entry))
        if self.regions_occupied:
            ranked.append((70, f"{self.regions_occupied} province(s) changed hands"))
        # **One pocket is one fact, however many formations are standing in it.** Three
        # divisions cut off in Khmel'nyts'kyy produced three near-identical lines, and PT34
        # showed what that costs: on eleven of sixteen turns the whole three-line summary
        # was the same province, three times, while everything else the turn contained fell
        # off the bottom. Exactly what the docstring above already says about fifty
        # sanctioned minors -- it just was not being applied here.
        for entry, others in _by_place(self.starved)[:4]:
            ranked.append((
                65,
                entry if not others
                else f"{entry} (and {others} other formation(s) cut off there)",
            ))
        # **Above a gambit and below a pocket.** A sky changing hands decides how the next
        # month's fighting goes rather than being a thing that happened -- and where it
        # starts interdicting a corridor it is the same order of news as a formation cut
        # off, which is the line above it.
        for entry in self.air[:4]:
            ranked.append((62, entry))
        for entry in self.gambits:
            ranked.append((60, entry))
        # Below a gambit: opening a supply line is quieter than trying something, and it is
        # a standing arrangement rather than an attempt that resolved this turn.
        for entry in self.support_changed[:4]:
            ranked.append((58, entry))
        for entry in self.sanctions[:4]:
            ranked.append((50, f"sanctions bite: {entry}"))
        if self.short_of:
            ranked.append((48, "cannot get what their industry needs: "
                               + ", ".join(self.short_of[:4])))
        for entry in self.trade_cut[:3]:
            ranked.append((46, f"trade cut: {entry}"))
        for entry in self.trade_opened[:3]:
            ranked.append((47, entry))
        # Below both: terms moving is news a player wants, and it is not a lane failing.
        for entry in self.trade_repriced[:3]:
            ranked.append((45, f"terms renegotiated: {entry}"))
        # And above a repricing, below a lane being cut: a supplier walking away is a
        # decision somebody made, which is louder than a number moving and quieter than a
        # blockade.
        for entry in self.trade_withdrawn[:3]:
            ranked.append((46, entry))
        # Beside a seller walking away, because it is the same size of event from the other
        # end of the lane.
        for entry in self.trade_refused[:3]:
            ranked.append((46, entry))
        # And a notch under a route opening: cargo moving again is good news, and good news
        # that outranked a blockade would be a report that reads like a brochure.
        for entry in self.trade_resumed[:3]:
            ranked.append((45, entry))
        for entry in self.trade_arranged[:3]:
            ranked.append((47, entry))
        # **Above every other trade line.** An embargo is a diplomatic act that happens to
        # be about cargo, and it outranks the market news for the same reason a broken
        # treaty outranks a renegotiated price: somebody decided it, and it is aimed.
        for entry in self.trade_embargoed[:3]:
            ranked.append((43, entry))
        if self.in_arrears:
            ranked.append((45, "cannot pay their establishments: "
                               + ", ".join(self.in_arrears[:4])))
        for entry in self.friction[:3]:
            ranked.append((44, entry))
        # High: a power caught red-handed is the kind of news a staff reorganises around.
        for entry in self.exposed[:3]:
            ranked.append((60, entry))
        for entry in self.front[:4]:
            ranked.append((40, entry))
        if self.commissioned:
            ranked.append((20, "commissioned: " + ", ".join(self.commissioned[:4])))
        for entry in self.order_outcomes[:4]:
            ranked.append((19, entry))
        if self.refused_diffs:
            ranked.append((18, f"{len(self.refused_diffs)} proposed change(s) refused"))
        for entry in self.objectives_deferred[:3]:
            ranked.append((17, f"standing, waiting for the front: {entry}"))
        # Above the deferred objectives and below the front, because a power describing a
        # war it is not fighting is worse news than an objective waiting its turn and less
        # than the fighting itself.
        for entry in self.narrative_unbacked[:3]:
            ranked.append((18, entry))
        if self.restive:
            ranked.append((15, "unrest drags on: " + ", ".join(self.restive[:4])))

        # A stable sort, so entries of equal gravity keep the order the engine produced
        # them in -- which is the order they happened.
        ranked.sort(key=lambda pair: -pair[0])
        return [text for _gravity, text in ranked]

    def describe(self) -> str:
        # **Unfiltered, deliberately.** This is the referee's console dump -- read by the
        # EVE harness and the tests, never by a player's screen -- and its job is to be
        # exhaustive. A player surface reads `for_viewer` or `significant`.
        lines = [
            f"turn {self.turn} -> {self.new_date} ({self.days_advanced}d), status {self.status.value}",
            f"  {self.applied_batches} batch(es) applied, {self.rejected_batches} rejected, "
            f"{self.events_recorded} events, {self.calls} model call(s)",
        ]
        if self.orders_executed:
            lines.append(f"  {self.orders_executed} of your orders were carried out")
        if self.contests or self.wars_declared or self.wars_ended:
            lines.append(
                f"  {self.contests} clash(es) resolved, {self.regions_occupied} region(s) "
                f"occupied, {self.wars_declared} war(s) declared, {self.wars_ended} ended"
            )
        if self.retreated or self.encircled:
            lines.append(
                f"  {self.retreated} formation(s) withdrew, {self.encircled} cut off"
            )
        lines.extend(f"  {entry}" for entry in self.air[:6])
        # What actually happened at the front, before the summary counts. A player needs
        # to know which province moved and how far, not how many rolls were made.
        lines.extend(f"  {entry}" for entry in self.front[:8])
        if self.tracked_acted:
            lines.append(f"  tracked nations acted: {', '.join(self.tracked_acted)}")
        if self.tracked_quiet:
            lines.append(
                f"  nothing to add this month: {', '.join(self.tracked_quiet)}"
            )
        if self.objectives_declared:
            lines.append(
                f"  military objectives set by: {', '.join(self.objectives_declared)}"
            )
        if self.objectives_deferred:
            lines.append("  objectives accepted, waiting for the front to reach them:")
            lines.extend(f"    {entry}" for entry in self.objectives_deferred[:6])
        if self.war_aims_declared:
            lines.append("  what the war is being fought for:")
            lines.extend(f"    {entry}" for entry in self.war_aims_declared[:6])
        if self.at_home:
            lines.append("  at home:")
            lines.extend(f"    {entry}" for entry in self.at_home[:6])
        if self.peace_offers:
            lines.append("  somebody stopped fighting:")
            lines.extend(f"    {entry}" for entry in self.peace_offers[:6])
        if self.war_weariness:
            lines.append("  the war told on somebody:")
            lines.extend(f"    {entry}" for entry in self.war_weariness[:6])
        if self.narrative_unbacked:
            lines.append("  described a month their orders do not back:")
            lines.extend(f"    {entry}" for entry in self.narrative_unbacked[:6])
        if self.commissioned:
            lines.append(f"  commissioned: {', '.join(self.commissioned)}")
        if self.in_arrears:
            lines.append(f"  cannot pay their establishments: {', '.join(self.in_arrears)}")
        if self.exposed:
            lines.append("  somebody was caught at it:")
            lines.extend(f"    {entry}" for entry in self.exposed[:6])
        if self.friction:
            lines.append("  their own country is not cooperating:")
            lines.extend(f"    {entry}" for entry in self.friction[:6])
        if self.restive:
            lines.append(
                f"  unrest in occupied ground drags on: {', '.join(self.restive[:6])}"
            )
        if self.sessions:
            lines.extend(f"  {entry}" for entry in self.sessions)
        if self.sanctions:
            lines.append(f"  sanctions bite: {', '.join(self.sanctions[:6])}")
        if self.pacts_invoked:
            lines.append(
                f"  a defensive pact was invoked; cause now runs to: "
                f"{', '.join(self.pacts_invoked)}"
            )
        if self.victories:
            lines.extend(f"  ** {entry} **" for entry in self.victories)
        if self.betrayed:
            lines.extend(f"  {entry}" for entry in self.betrayed)
        if self.lapsed_treaties:
            lines.extend(f"  {entry}" for entry in self.lapsed_treaties)
        if self.why_it_moved:
            lines.append("  why your numbers moved:")
            lines.extend(f"    {entry}" for entry in self.why_it_moved[:6])
        if self.order_outcomes:
            lines.extend(f"  {entry}" for entry in self.order_outcomes[:6])
        if self.short_of:
            lines.append(
                f"  short of what their industry needs: {', '.join(self.short_of[:6])}")
        if self.trade_opened:
            lines.extend(f"  {entry}" for entry in self.trade_opened[:4])
        if self.trade_cut:
            lines.extend(f"  trade cut: {entry}" for entry in self.trade_cut[:4])
        if self.trade_repriced:
            lines.extend(
                f"  terms renegotiated: {entry}" for entry in self.trade_repriced[:4])
        if self.trade_withdrawn:
            lines.extend(f"  {entry}" for entry in self.trade_withdrawn[:4])
        if self.trade_refused:
            lines.extend(f"  {entry}" for entry in self.trade_refused[:4])
        if self.trade_resumed:
            lines.extend(f"  {entry}" for entry in self.trade_resumed[:4])
        if self.trade_arranged:
            lines.extend(f"  {entry}" for entry in self.trade_arranged[:4])
        if self.trade_embargoed:
            lines.extend(f"  {entry}" for entry in self.trade_embargoed[:4])
        if self.starved:
            lines.extend(f"  {entry}" for entry in self.starved[:6])
        if self.fell:
            lines.extend(f"  {entry}" for entry in self.fell)
        if self.shocks:
            lines.extend(f"  {entry}" for entry in self.shocks)
        if self.refused_diffs:
            lines.append(
                f"  {len(self.refused_diffs)} change(s) refused inside batches that stood:"
            )
            lines.extend(f"    {entry}" for entry in self.refused_diffs[:6])
        if self.nuclear:
            lines.append("  ** NUCLEAR WEAPONS WERE USED THIS TURN **")
            lines.extend(f"    {entry}" for entry in self.nuclear)
        if self.gambits:
            lines.extend(f"  {entry}" for entry in self.gambits)
        if self.gambits_declined:
            lines.extend(f"  {entry}" for entry in self.gambits_declined)
        if self.rebelled:
            lines.append(
                f"  partisans rose and drove the occupier out of: "
                f"{', '.join(self.rebelled[:6])}"
            )
        if self.lapsed:
            lines.append(f"  orders overtaken by events: {', '.join(self.lapsed[:4])}")
        lines.extend(f"  {entry}" for entry in self.freeform)
        if self.promoted or self.demoted:
            lines.append(
                f"  focus: +{', '.join(self.promoted) or 'none'} "
                f"-{', '.join(self.demoted) or 'none'}"
            )
        lines.extend(f"  rejected: {reason}" for reason in self.rejections[:6])
        lines.extend(f"  failed:   {failure}" for failure in self.failures[:6])
        return "\n".join(lines)


@dataclass(slots=True)
class AdvanceReport:
    """Several turns run together, and why the run stopped when it did."""

    asked_for: int
    watching: frozenset[str] = field(default_factory=frozenset)
    jumps: list[JumpReport] = field(default_factory=list)
    turns_run: int = 0
    #: The turn the run stopped on, or ``None`` if it ran the whole way.
    halted_on: int | None = None
    triggers: tuple[Trigger, ...] = ()
    #: Set when the run stopped because a turn could not be completed, rather than because
    #: something worth seeing happened. A different sentence for the player entirely.
    aborted: bool = False
    #: Set when the run stopped because the player asked it to, between two turns.
    stopped: bool = False

    @property
    def halted(self) -> bool:
        return self.halted_on is not None

    @property
    def turn(self) -> int:
        """The turn the world is now on."""
        return self.jumps[-1].turn if self.jumps else 0

    def why(self) -> str:
        """One sentence a player can read, or empty if nothing stopped it."""
        if self.aborted:
            return "the turn could not be completed"
        if self.stopped:
            return "you asked it to stop"
        if not self.triggers:
            return ""
        return "; ".join(trigger.label for trigger in self.triggers)


@dataclass(slots=True)
class TurnEngine:
    """Drives one world through its turns."""

    conn: sqlite3.Connection
    world_id: str
    providers: dict[str, ModelProvider]
    router: Router
    difficulty: Difficulty = Difficulty.NORMAL
    #: How many turns of history to feed before switching to digests.
    raw_history_turns: int = 3
    #: How many nations may attempt a gambit this turn. A second model call on top of
    #: the agent call, so this is what a jump *costs* as much as what it can do: at the
    #: default a six-call turn becomes eight. Set to zero to run the turn loop with the
    #: channel off, which is what a test measuring the tracked-agent accounting wants.
    gambits_per_turn: int = MAX_GAMBITS_PER_TURN
    #: Reports each stage as it runs, so the player sees which call is slow rather than
    #: one spinner for six minutes. Optional: the engine works without a listener.
    progress: ProgressReporter | None = None
    #: Which turn  files a model call under. Set at the top of every jump.
    #: Defaults to 0 so a call made outside a jump -- an advisor question, a diplomacy
    #: session -- is still logged rather than raising, against turn zero, which is honest:
    #: it did not happen inside a turn.
    #: Consecutive world-simulation failures, and whether to stop asking agy. Held on the
    #: engine rather than passed in, because it is session state: a window of agy flakes is
    #: a fact about the next hour, not about one turn. See `atlas.engine.routing.Breaker`.
    world_sim_breaker: Breaker = field(default_factory=Breaker)
    #: **Nations a study is measuring that no bloc plays**, each with the model that plays
    #: it: ``{nation_id: (provider_key, model)}``. Empty in every world that did not ask.
    #:
    #: A study subject is asked **every turn**, on its own pin, whether or not Focus tracks
    #: it -- and it is never played by a bot (`atlas.engine.bots`) and never by the master
    #: AI (`production.autonomous_investment`). The kitchen-sink match of 2026-09-22 is why:
    #: its betrayal victims were left to Focus, and Malaysia got **no** model call in 15
    #: turns, Laos one and Czechoslovakia three -- the rest of the time a deterministic
    #: heuristic answered for the very nations the study was about. Forcing them onto the
    #: Focus roster instead would take three of its five slots and change which *other*
    #: neutrals the world pays attention to, which is a confound of its own.
    study_subjects: dict[str, tuple[str, str]] = field(default_factory=dict)
    _logged_turn: int = 0

    # -- world clock -------------------------------------------------------

    def _world_row(self) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT turn_number, world_date, rng_seed FROM world WHERE id = ?", (self.world_id,)
        ).fetchone()

    @property
    def turn(self) -> int:
        return int(self._world_row()["turn_number"])

    @property
    def world_date(self) -> str:
        return str(self._world_row()["world_date"])

    @property
    def seed(self) -> int:
        return int(self._world_row()["rng_seed"])

    def _player_nation(self) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_player = 1", (self.world_id,)
        ).fetchone()
        return row["id"] if row else None

    def _set_phase(self, phase: TurnPhase, status: TurnStatus | None = None,
                   *, turn: int | None = None) -> None:
        """Stamp one turn's row. **The turn is passed in, not read from the clock.**

        `world.turn_number` does not advance until the end of `jump()`, so reading it here
        addressed the *previous* turn for the whole of the turn in progress. On the seal
        path that is harmless -- the clock has moved by then, which is exactly why nothing
        noticed. On the two abort paths it was not: aborting turn T overwrote the sealed
        record of turn T-1 as abandoned, and left turn T's own row on the 'jump'/'open' it
        was inserted with, so the ledger disagreed with itself in both directions at once.
        On a fresh world whose first jump aborts -- a provider CLI that is not installed or
        not authenticated, which is this app's commonest real failure -- the row it
        rewrites is the one `bootstrap` sealed at turn 0.

        The same discipline `apply_focus` is given a few lines above the clock write, and
        for the same reason.
        """
        number = self.turn if turn is None else turn
        self.conn.execute(
            "UPDATE turn SET phase = ?" + (", status = ?" if status else "") +
            " WHERE world_id = ? AND number = ?",
            ((phase.value, status.value, self.world_id, number) if status
             else (phase.value, self.world_id, number)),
        )
        self.conn.commit()

    def _refused(
        self,
        report: JumpReport,
        *,
        nation_id: str,
        name: str,
        turn: int,
        channel: str,
        reasons,
    ) -> None:
        """One refusal, to the player's turn report **and** to the party that was refused.

        **The second half is what was missing, and it was missing everywhere.** Every
        refusal in this block was already worded as what to write instead and every one of
        them went only to `report.rejections`, which reaches `atlas.ui.overlays` and no view
        builder at all -- so a tracked power refused for a rule its briefing never stated
        proposed the identical thing the next turn, and the turn after. See
        :mod:`atlas.engine.refusals`.

        Kept as one helper rather than two calls at each of the eighteen sites, for the
        reason `_record_call` is one door: a thing every caller must remember to do is a
        thing some caller will forget, and the caller that forgets is invisible until
        somebody replays a tape and counts.

        Reasons are whitespace-normalised and empty ones are dropped, which is the only
        behavioural change to `report.rejections` this made: a blank line in a turn report
        was never useful and in a briefing it costs the same tokens as a real one.

        ``channel`` is the schema property the refusal is about, so the briefing can point
        the staff at the field it will fill in rather than at the turn in general.
        """
        for reason in reasons:
            text = " ".join(str(reason or "").split())
            if not text:
                continue
            report.rejections.append(f"{name}: {text}" if name else text)
            refusals.record(
                self.conn, self.world_id, nation_id=nation_id, turn=turn,
                channel=channel, reason=text,
            )

    def _record_call(
        self,
        request: ProviderRequest,
        result: ProviderResult,
        decision: RoutingDecision | None = None,
        nation_id: str = "",
    ) -> None:
        """Put one model call in the routing log, whoever made it.

        **PT31 measured 126 calls in a run and found 81 in the log.** Two of this file's
        six call sites logged; gambit, organisation, freeform and the digest did not, so 45
        calls a run carried no attestation, no served-model record and no duration --
        invisible to an audit and to the laboratory's own detectors.

        The logging lives here for the reason the capability manifest exists: a thing every
        caller must remember to do is a thing some caller will forget. `_invoke` is the one
        door every model call in a turn already passes through, so this cannot be skipped
        without skipping the call itself.

        `decision` is optional because it is **provably redundant for four of the six**.
        Gambit, organisation, freeform and the digest all route with `route(Role.X, 0.0)`,
        and `_band_for` returns `Band.LOW` for every role but WORLD_SIM and DIPLOMACY --
        so the reconstruction below is identical to the decision they hold, field for
        field. The result carries the provider key and role the call actually used, which
        is what makes reconstructing it honest rather than a guess.
        """
        log_decision(
            self.conn,
            self.world_id,
            self._logged_turn,
            decision or RoutingDecision(
                role=request.role,
                provider_key=result.provider or "",
                model=request.model,
                band=Band.LOW,
                score=0.0,
            ),
            nation_id=nation_id,
            served_model=result.audit.served if result.audit else None,
            audit_verdict=result.audit.verdict.value if result.audit else None,
            duration_ms=int(result.duration_s * 1000),
            outcome=(
                "ok" if result.ok
                else (result.failure.kind.value if result.failure else "failed")
            ),
        )
        self.conn.commit()

    async def _invoke(
        self, provider: ModelProvider, request: ProviderRequest, *,
        stage: str | None = None, decision: RoutingDecision | None = None,
        retries: int | None = None, nation_id: str = "",
    ) -> ProviderResult:
        """Call a provider, retrying the failures that are worth retrying.

        ``Failure.retryable`` has always distinguished a rate limit from a logged-out
        CLI, and nothing acted on it: one 429 from a burst of tracked-nation calls turned
        a paid, successful world simulation into a partial turn. Authentication and
        installation problems are still never retried -- retrying those wastes minutes
        and buries the message that would actually fix them.
        """
        # **`retries` is per call, and the world simulation asks for one.** Three attempts
        # is right for a rate limit, which clears; it is wrong for agy's empty-answer window,
        # which does not clear in fifteen seconds and costs the turn a minute of waiting to
        # find that out. One retry catches the genuine one-off; the breaker handles the rest.
        budget = _RETRY_DELAYS if retries is None else _RETRY_DELAYS[:max(0, retries)]

        def _announce() -> None:
            if self.progress is not None and stage is not None:
                # A *count*, not a sentence about rate limits and providers. What a second
                # attempt means is the view's business to say; see `atlas.ui.turning`.
                self.progress.retry(stage)

        result = await invoke_with_retries(
            provider, request, delays=budget, on_retry=_announce)
        # One log line per *call*, not per attempt: the duration is the last attempt's
        # and the outcome is what the turn actually got.
        self._record_call(request, result, decision, nation_id=nation_id)
        return result

    # -- the jump ----------------------------------------------------------

    def _withdraw_orders(self, issued: list[int], report: JumpReport) -> None:
        """Take back the `order_issued` events of a turn that never ran.

        Only ever called before the simulator's answer has been applied, so nothing can
        cite these yet -- the case `unrecord_events` exists for. Deleted rather than marked,
        because the briefing, the wire and the summary all read the event log as the record
        of what was done, and an order that was never attempted was not done.
        """
        if not issued:
            return
        unrecord_events(self.conn, issued)
        self.conn.commit()
        report.events_recorded -= len(issued)
        issued.clear()

    async def advance(
        self,
        *,
        turns: int,
        days: int = 30,
        actions: list[dict] | None = None,
        watch: frozenset[str] | set[str] | None = None,
        stop: threading.Event | None = None,
    ) -> AdvanceReport:
        """Run several turns, and stop early if one of them is worth stopping on.

        **`jump` is untouched and still means one turn.** This loops it. The distinction
        matters because a jump has always been a single turn covering however many days it
        was given -- a "year" was one turn of three hundred and sixty-five days, not twelve
        turns -- so an interruptible advance had nothing to interrupt until there was a
        loop to put the check inside.

        **Halting is an observation, not a mutation.** The stop conditions are read from
        the event and diff logs *after* a turn has fully resolved, and nothing about them
        reaches back into it -- so stopping after the third turn leaves a world identical,
        byte for byte, to having asked for three turns. That is the correctness property
        this whole feature stands on, and it is asserted directly against a fingerprint.

        Orders are carried by the **first** turn only. They were written against the world
        as it stood when the player wrote them, and replaying the same orders into a world
        three turns further on would be executing a decision nobody made.

        **`stop` is how the player ends the run, and it is only read between turns.** A
        `threading.Event` because it is set from the interface thread while this runs on
        the loop thread. A turn already under way always finishes: killing one mid-call is
        how a world ends up half-applied with nothing recording that it was interrupted.
        The halt it produces is the same observation as a trigger, so a stopped run leaves
        exactly the world that asking for that many turns would have.
        """
        report = AdvanceReport(asked_for=turns, watching=frozenset(
            DEFAULT_WATCH if watch is None else watch))
        carried = actions or []

        for index in range(max(1, turns)):
            jump = await self.jump(days=days, actions=carried if index == 0 else [])
            carried = []
            report.jumps.append(jump)
            report.turns_run += 1

            # **A turn that could not run stops the advance too.** Without this the loop
            # spends its whole budget re-failing: an aborted jump does not move the clock,
            # so the next iteration opens the same turn number, fails the same way, and
            # bills for it again. Ten turns of that is ten times the calls and no time
            # passing at all.
            if jump.status is TurnStatus.ABORTED:
                report.aborted = True
                report.halted_on = jump.turn
                break

            found = triggers_in(self.conn, self.world_id, jump.turn, watch=watch,
                                viewer=self._player_nation())
            if found:
                report.triggers = tuple(found)
                report.halted_on = jump.turn
                break

            if stop is not None and stop.is_set() and index + 1 < turns:
                report.stopped = True
                report.halted_on = jump.turn
                break

        return report

    async def jump(
        self, *, days: int = 30, actions: list[dict] | None = None,
        shocks: list[dict] | None = None,
    ) -> JumpReport:
        """Advance time and let the world simulate.

        The sequence is: open a turn, build the referee's view, call the world simulator,
        validate and apply what it proposes, advance the clock. Every failure short of a
        crash still produces a report the player can read.

        ``shocks`` is the engine's own channel for things that happen *to* a nation that
        its numbers did not predict: ``{"nation": id, "sponsor": id|None}`` for an
        assassination, and ``{"kind": "false_intel", "receivers": [ids], "accused": id,
        "region": id}`` for a report from a nation's own service that is not true -- see
        :mod:`atlas.engine.false_intel`. **It is deliberately not an `actions` entry.** `actions`
        carries orders, and an order type is one schema field away from every tracked
        agent in the game; a separate argument that only the CLI and the study harness
        fill in is what makes "no model can fire this" structural rather than checked.
        """
        turn = self.turn + 1
        # Every call this jump makes is logged against this turn. Held on the engine
        # because `_invoke` is the choke point that does the logging and it has no other
        # way to know which turn it is inside.
        self._logged_turn = turn
        actions = actions or []
        shocks = shocks or []
        start_date = self.world_date
        new_date = _advance(start_date, days)

        self.conn.execute(
            """
            INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)
            VALUES (?, ?, 'jump', 'open', ?)
            """,
            (self.world_id, turn, start_date),
        )
        self.conn.commit()

        report = JumpReport(
            turn=turn, status=TurnStatus.OPEN, days_advanced=days, new_date=new_date,
            viewer=self._player_nation(),
        )

        # **The shape of the whole turn, declared before any of it starts.** Moved above
        # the order-recording that used to precede it so the first stage has something to
        # report; nothing here reads or writes world state beyond the roster, and no
        # branch below depends on whether a listener is attached.
        #
        # The labels are deliberately flat -- a stage key and, for a tracked nation, that
        # nation's *name*, which is data rather than description. Everything the player
        # actually reads is written in `atlas.ui.turning`, which owns the single mapping
        # from these keys to words. Two places composing the same sentence is how a
        # screen ends up saying "calling the model for France".
        if self.progress is not None:
            planned = [
                ("orders", "Orders recorded", ""),
                ("world_sim", "World simulation", ""),
                ("validate", "Validation", ""),
                ("enact", "Order effects", ""),
                ("resolve", "Resolution", ""),
                ("month", "Engine month", ""),
            ]
            planned += [
                (f"tracked:{entry['id']}", str(entry["name"]), "")
                for entry in tracked_nations(self.conn, self.world_id)
            ]
            planned += [
                ("focus", "Focus roster", ""),
                ("digest", "Consolidation", ""),
                ("seal", "Turn sealed", ""),
            ]
            self.progress.plan(planned)
            self.progress.start("orders")

        # Player orders become engine-authored events, so any diff resulting from an
        # order has something legal to cite. Without this the mandatory-citation rule
        # would make player action unrepresentable.
        #
        # **Recorded before the simulator, and withdrawn if the turn never runs.** They
        # have to exist before the call -- they are what the simulator cites -- but an
        # aborted turn attempted none of them and does not move the clock, so a retry
        # opens the same turn number and records them again. Left in place, three failed
        # presses put three copies of every order into the next briefing, each reading as
        # issued. See `_withdraw_orders`.
        issued: list[int] = []
        for action in actions:
            issued.append(record_event(
                self.conn,
                world_id=self.world_id,
                turn=turn,
                date=start_date,
                kind=EventKind.ORDER_ISSUED,
                origin=EventOrigin.ENGINE,
                text=str(action.get("summary") or action.get("type") or "an order was issued"),
                actor_nation_id=self._player_nation(),
                visibility=Visibility.PUBLIC,
            ))
            report.events_recorded += 1
        self.conn.commit()
        if self.progress is not None:
            self.progress.finish("orders")

        score, factors = jump_complexity(
            self.conn, self.world_id, days_advanced=days, action_count=len(actions)
        )
        decision = self.world_sim_breaker.route(
            self.router.route(Role.WORLD_SIM, score, factors), WORLD_SIM_FALLBACK
        )

        if self.progress is not None:
            self.progress.start("world_sim")

        if bots.controls_role("world_sim"):
            # Narrative, and answers to the notes and treaty proposals among the orders --
            # the only part of the simulator's job the engine does not already do by rule.
            from .staff_bots import world_sim_batch

            result = _BotAnswer(world_sim_batch(
                self.conn, self.world_id, player=self._player_nation(), actions=actions,
                order_events=issued, days=days, turn=turn))
            report.bot_decisions += 1
            answered = True
        else:
            view = build_world_sim_view(
                self.conn, self.world_id, parsed_actions=actions, days=days
            )
            provider = self.providers.get(decision.provider_key)

            if provider is None or not provider.available():
                report.status = TurnStatus.ABORTED
                report.failures.append(
                    f"{decision.provider_key} is unavailable; the world simulator cannot run. "
                    "ATLAS does not substitute another provider for a configured role."
                )
                self._withdraw_orders(issued, report)
                self._set_phase(TurnPhase.RESOLVE, TurnStatus.ABORTED, turn=turn)
                return report

            request = ProviderRequest(
                role=Role.WORLD_SIM,
                prompt=view.text,
                model=decision.model,
                schema=diff_batch_schema(role=Role.WORLD_SIM),
                timeout_s=float(900 if decision.band.value == "high" else 420),
            )

            # The one role whose band and complexity are not constant, so it hands the real
            # decision over rather than letting `_record_call` reconstruct a `low` one.
            try:
                result: ProviderResult = await self._invoke(
                    provider, request, stage="world_sim", decision=decision, retries=1
                )
            except BaseException:
                # A crash or a cancellation in the call is the same non-event as a failed
                # answer: nothing was simulated, so nothing the orders would cite was written.
                self._withdraw_orders(issued, report)
                raise
            report.calls += 1
            answered = result.ok
            if self.world_sim_breaker.record(result.ok):
                report.failures.append(
                    f"{decision.provider_key} has failed the world simulation "
                    f"{self.world_sim_breaker.threshold} times running; falling back to "
                    f"{WORLD_SIM_FALLBACK[0]} for the rest of this session"
                )
        if self.progress is not None:
            self.progress.finish(
                "world_sim",
                state=StageState.DONE if answered else StageState.FAILED,
                detail="" if answered else (result.failure.kind.value if result.failure else "failed"),
            )


        _note_accepted(report, Role.WORLD_SIM,
                       answered=not result.failed, failed=result.failed)
        if result.failed:
            report.status = TurnStatus.ABORTED
            report.failures.append(result.failure.describe() if result.failure else "unknown failure")
            self._withdraw_orders(issued, report)
            self._set_phase(TurnPhase.RESOLVE, TurnStatus.ABORTED, turn=turn)
            return report

        # **An answer that cannot be read as a batch is a failed call, and ends the turn
        # the way one does.** The provider layer checks only the top-level keys and
        # leaves nested fields to each CLI's own schema enforcement, which agy -- the
        # world simulator's pin -- does not reliably do. A diff with no `target_id`, or
        # an `op` outside the enum, raised out of `jump` from here. Aborting rather than
        # applying part of it is the rule `result.failed` already follows: nothing is
        # written, and the turn can be played again.
        try:
            batch = parse_batch(
                result.data if result.data is not None else result.text,
                world_id=self.world_id,
                turn=turn,
                role=Role.WORLD_SIM,
                model=decision.model,
            )
        except (KeyError, TypeError, ValueError) as exc:
            report.status = TurnStatus.ABORTED
            report.failures.append(
                f"the world simulator's answer could not be read as a batch: {exc!r}"[:300])
            self._set_phase(TurnPhase.RESOLVE, TurnStatus.ABORTED, turn=turn)
            return report

        if self.progress is not None:
            self.progress.start("validate")
        # The turn's length reaches validation, so a rule that bounds a *rate* bounds
        # it over the right amount of time. Without this a year-long jump is held to a
        # month's economic movement -- and now that the caps are quoted in the briefing,
        # it would be held to a figure it had just been promised twelve times over.
        outcome = apply_batch(
            self.conn, batch, difficulty=self.difficulty, days=days
        )
        _note_accepted(report, Role.WORLD_SIM, batch=batch, outcome=outcome)
        if outcome.applied:
            report.applied_batches += 1
            report.events_recorded += len(batch.events)
            _note_refusals(report, outcome, "the world")
        else:
            report.rejected_batches += 1
            report.rejections.append(outcome.reason)
        if self.progress is not None:
            self.progress.finish(
                "validate",
                state=StageState.DONE if outcome.applied else StageState.FAILED,
                detail="applied" if outcome.applied else "refused by the rules",
            )

        if self.progress is not None:
            self.progress.start("enact")
        # An accepted order must move something by itself. Leaving every consequence to
        # the simulator meant "accelerate rearmament", accepted three quarters running,
        # moved military strength by one and a half points -- acceptance that meant nothing.
        effects = apply_order_effects(
            self.conn, self.world_id, turn=turn, date=new_date,
            actions=actions, nation_id=self._player_nation(), days=days,
        )
        report.orders_executed = effects.applied
        report.rejections.extend(effects.problems)
        # One line per order the player gave, whatever became of it -- including the ones
        # nothing executes, which used to leave no trace at all.
        report.order_outcomes.extend(
            outcome.rendered() for outcome in effects.outcomes
            if outcome.status != 'attempted'
        )

        # Orders no mechanic covers. They used to become `other` and evaporate: accepted
        # by the parser, recorded as issued, and then nothing -- which the player cannot
        # tell apart from a bug.
        # One ledger for the whole turn across *both* narrative channels, so freeform
        # and gambits share the per-field ceiling rather than each getting their own.
        ledger: dict[tuple[str, str], float] = {}
        await self._run_freeform(turn, new_date, actions, report, ledger)
        if self.progress is not None:
            self.progress.finish("enact")

        # Resolution: the phase that decides. Everything above this line observed and
        # proposed; nothing it returned could move a border or cost a division, because
        # those columns are engine-only. This is where an order to take ground becomes
        # something that happened.
        if self.progress is not None:
            self.progress.start("resolve")
        # **How much time this turn covers, and how it is spent.** One model decision buys
        # one jump; the engine then grinds out the months that decision covers. A year is
        # twelve ordinary monthly rounds rather than one enormous roll -- same total
        # advance, but the variance stays where it was tuned and the front tells a story
        # instead of teleporting. See :mod:`atlas.engine.pacing`.
        span = span_of(days)
        round_count, round_span = rounds(span)
        round_days = days / round_count
        # **And each of those months happened on its own day.** See `_round_dates`: the
        # last entry is `new_date` exactly, so a one-round jump -- every weekly and every
        # monthly turn -- is dated precisely as it was before.
        round_dates = _round_dates(start_date, days, round_count)
        resolution = resolve_turn(
            self.conn,
            self.world_id,
            turn=turn,
            date=new_date,
            seed=self.seed,
            contests=list(batch.contests),
            declarations=list(batch.declarations),
            actions=actions,
            difficulty=self.difficulty,
            player_nation=self._player_nation(),
            rounds=round_count,
            span=round_span,
        )
        report.contests = resolution.contests
        report.regions_occupied = resolution.regions_occupied
        report.wars_declared = resolution.wars_declared
        report.wars_ended = resolution.wars_ended
        report.pacts_invoked.extend(resolution.pacts_invoked)
        report.rejections.extend(resolution.problems)
        # The narration of what the fighting actually did. Resolution has always built
        # these -- "France pressed Germany in Saarland (60% of the way to taking it)" --
        # and the turn report threw every one of them away, so a player watching an
        # offensive saw "1 clash" three turns running and could not tell whether they were
        # winning, losing, or wasting a division a month on it.
        report.front.extend(resolution.headlines)
        report.front.extend(effects.headlines)
        report.air.extend(_air_lines(self.conn, self.world_id, resolution,
                                     viewer=self._player_nation()))
        report.retreated = resolution.retreated
        report.encircled = resolution.encircled
        if self.progress is not None:
            self.progress.finish(
                "resolve",
                detail=(
                    f"{resolution.contests} clash(es), "
                    f"{resolution.regions_occupied} region(s) occupied, "
                    f"{resolution.wars_declared + resolution.wars_ended} war(s) changed"
                    if resolution.acted else "nothing was contested"
                ),
            )

        if self.progress is not None:
            self.progress.start("month")
        # The rest of the engine's month, run once per round for the same reason the
        # fighting is: a year should collect a year of tax, drift a year of unrest and
        # deliver a year of shipbuilding. Each pass is told how long it covers, so the
        # flows multiply and the rates compound -- and because a rate compounded over
        # twelve one-month passes is exactly a rate compounded over one twelve-month span,
        # the two halves of the pacing model agree by construction.
        for _round in range(round_count):
            # **The day this round happened on**, and not the day the jump ends. Indexed
            # rather than iterated because `_round` is the loop's name for which round
            # this is, and two structural gates -- `test_every_per_round_call_is_given_a_round`
            # and `test_nothing_else_in_that_loop_takes_a_whole_turn` -- find this loop by
            # it in order to check that nothing inside takes the whole turn's length.
            round_date = round_dates[_round]

            # The month's money, before the yards spend it. Revenue first so a programme
            # ordered this turn can be paid for out of this turn's income rather than only
            # out of what was left over from the last one.
            budget = collect_revenue(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days
            )
            report.rejections.extend(budget.problems)
            for name in budget.in_arrears:
                if name not in report.in_arrears:
                    report.in_arrears.append(name)

            # **An arsenal nobody is paying for gets less reliable, and never smaller.**
            # Driven by how strained the budget is rather than by whether it broke: the
            # arrears flag fired zero times in five play-test games, so the version hung
            # off it was correct and dormant. Readiness moves slowly in both directions
            # and stops at a floor -- neglect is a real loss and never a confiscation,
            # which is the rule the launch stake had to learn.
            # **Not `report.nuclear`.** That list means *weapons were used this turn* --
            # its own heading precisely so a turn carrying it does not read like an
            # ordinary one -- and an arsenal quietly going unmaintained is the opposite of
            # that news. Filing it there made a conventional bloc war report as a nuclear
            # turn, which `test_a_bloc_war_leaves_the_deterrent_where_it_was` caught on the
            # first full run. It is friction, like the morale it sits beside.
            for line in drift_readiness(
                self.conn, self.world_id, turn=turn, date=round_date, span=round_span,
            ):
                if line not in report.friction:
                    report.friction.append(line)
            for line in budget.on_strike:
                if line not in report.friction:
                    report.friction.append(line)

            # Unrest as a map cost, before it is charged as a stability cost: badly-held
            # ground may rise this turn and revert to its home nation. Run first so the
            # holder is not then dragged for a province the partisans just took off its
            # books.
            # **`round_index`**: this is a draw, and a draw repeated on one turn's key is
            # the same draw (decision 104). Without it a year rolled one month's dice twelve
            # times over.
            partisans = assess_partisans(
                self.conn, self.world_id, turn=turn, date=round_date, seed=self.seed,
                days=round_days, round_index=_round,
            )
            report.rejections.extend(partisans.problems)
            report.rebelled.extend(partisans.flipped)

            # The standing cost of holding what is not yours. Assessed after the map has
            # settled -- conquests made this turn are already on the books, and ground
            # handed back at a peace is no longer charged.
            # **Before the unrest is charged**, so a movement that crossed its threshold
            # this turn is felt this turn rather than next -- the same ordering rule the
            # breach assessment follows against the fighting.
            colonies = assess_independence(
                self.conn, self.world_id, turn=turn, date=round_date,
                # **`round_span`, not `span`.** This runs once per round like everything
                # else in this loop, and it was being handed the whole *turn's* span every
                # time -- so a year-long jump charged twelve rounds of twelve months each,
                # a hundred and forty-eight months of momentum for twelve months of time.
                # Every neighbouring call takes `days=round_days`, which is the same
                # quantity by another name; this one took the only variable in scope that
                # was wrong. Exactly right at a monthly turn, where there is one round --
                # which is why no digest moved and no test noticed.
                seed=self.seed, span=round_span, round_index=_round,
            )
            report.rejections.extend(colonies.problems)
            report.betrayed.extend(colonies.stirred + colonies.ripe)
            unrest = assess_unrest(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days
            )
            report.rejections.extend(unrest.problems)
            for entry in unrest.restive:
                if entry not in report.restive:
                    report.restive.append(entry)

            # Cargoes move and the furnaces burn what arrives. Before the yards advance,
            # because what a country can build this round depends on what reached it this
            # round -- and after the fighting, because a lane cut this round is cut for it.
            # **`turn_days` as well as `days`.** Every flow here is scaled by the round;
            # how often a standing price is renegotiated is not a flow, it is capped at
            # once a turn, and it therefore has to know how much world time a turn is.
            # Without it a yearly game reconsidered its terms every six *years*.
            trade = assess_trade(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days,
                turn_days=days,
            )
            report.rejections.extend(trade.problems)
            for entry in trade.interrupted[:6]:
                if entry not in report.trade_cut:
                    report.trade_cut.append(entry)
            for entry in trade.short:
                if entry not in report.short_of:
                    report.short_of.append(entry)
            for entry in trade.repriced[:6]:
                if entry not in report.trade_repriced:
                    report.trade_repriced.append(entry)
            for entry in trade.withdrawn[:6]:
                if entry not in report.trade_withdrawn:
                    report.trade_withdrawn.append(entry)
            for entry in trade.refused[:6]:
                if entry not in report.trade_refused:
                    report.trade_refused.append(entry)
            for entry in trade.resumed[:6]:
                if entry not in report.trade_resumed:
                    report.trade_resumed.append(entry)
            for entry in trade.embargoed[:6]:
                if entry not in report.trade_embargoed:
                    report.trade_embargoed.append(entry)

            # What it cost to be beyond the reach of your own depots. **Inside the round
            # loop, unlike the succession roll below it**, and the distinction is the same
            # one: starvation is a flow, so six rounds of it is six months of it, while a
            # coup is a draw and six rounds of that is one draw redrawn. Placed after the
            # fighting so the supply walk reads the front as it now stands -- an army cut
            # off this round starves this round.
            supply = assess_attrition(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days
            )
            report.rejections.extend(supply.problems)
            for entry in supply.starved:
                if entry not in report.starved:
                    report.starved.append(entry)

            # **The recoverable half of the same news.** Supply charges attrition, which
            # is permanent; this moves morale, which is not. Run beside it and on the same
            # reading of the map, because an army out of supply is worse at fighting
            # before it is smaller -- and gets better again when the line reopens.
            spirit = drift_morale(
                self.conn, self.world_id, turn=turn, date=round_date, span=round_span,
            )
            report.rejections.extend(spirit.problems)
            for line in spirit.news:
                if line not in report.friction:
                    report.friction.append(line)

            # **After the fighting has moved whatever it is going to move.** Entrenchment
            # is earned by being somewhere at the end of a round rather than at the start
            # of one: a formation thrown out of a province this round must not be credited
            # with another month of digging in it, and one that has just taken a province
            # must start from nothing. Running this before resolution would do both.
            spades = drift_entrenchment(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days,
            )
            report.rejections.extend(spades.problems)

            # What standing mobilised costs the country, charged every month it stands. The
            # manpower a footing buys arrives when it is declared; the bill arrives forever.
            callup = drift_mobilisation(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days,
            )
            report.rejections.extend(callup.problems)

            # **After the fighting and the call-up, because both are inputs to it.** A power
            # that declared a war this turn should be judged on having declared it, not on
            # the record it had before the turn began.
            # **What every government is playing for, reviewed on its own clock.** Run
            # before the order is awarded and after the world has moved, so a review reads
            # the turn that just happened. Most turns this does nothing at all: a plan is
            # reviewed about once a year, and holding one between reviews is the point.
            # **And on this round's own date, which is what lets a shock review happen in
            # March.** `replan_trigger` was fully implemented and structurally unable to
            # fire mid-jump: every round of a yearly turn asked the calendar the same
            # question on the same day, so a government overrun in March was re-evaluated
            # in December. `round_date`, not the bare `date` -- this module imports `date`
            # from datetime, so that name here is the class. It reached the diff pipeline
            # as a value and failed to serialise, which is the good way for that mistake
            # to end.
            for line in strategy_review(
                    self.conn, self.world_id, turn=turn, date=round_date):
                report.strategy_changes.append(line)

            order = award_leadership(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days,
            )
            report.rejections.extend(order.problems)
            for who in order.led:
                if who not in report.led_the_order:
                    report.led_the_order.append(who)

            # ...and what that costs the *ground* rather than the government. Ordered
            # after unrest so the two read the same map, and after the partisans so a
            # province that just rose is no longer being degraded by its former holder.
            erosion = erode_development(
                self.conn, self.world_id, turn=turn, date=round_date, days=round_days
            )
            report.rejections.extend(erosion.problems)
            report.front.extend(erosion.headlines)

            # The yards move whether or not anybody fought. A programme laid down before
            # the war is what the war is fought with, so this runs every round regardless.
            production = advance_construction(
                self.conn, self.world_id, turn=turn, date=round_date, seed=self.seed,
                days=round_days,
            )
            report.rejections.extend(production.problems)
            report.commissioned.extend(production.delivered)
            for nation_id in production.dragging:
                named = self.conn.execute(
                    "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
                line = (f"{named['name'] if named else nation_id}: industry is dragging "
                        "its feet on the yards")
                if line not in report.friction:
                    report.friction.append(line)
            # **And the other reason a yard is slow, which has a different answer.**
            # `dragging` is settled with your own industrialists; this one is settled by
            # opening a route or breaking a blockade, and a government told only "the yards
            # are slow" cannot tell which of the two it has. Reported separately for the
            # same reason `ProductionReport` keeps the two lists apart -- and reported at
            # all because friction a player cannot see is a bug they cannot diagnose, which
            # is the argument that put `dragging` here in the first place.
            for nation_id in production.starved:
                named = self.conn.execute(
                    "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
                line = (f"{named['name'] if named else nation_id}: the yards are short of "
                        "the materials they need, and building slowly for it")
                if line not in report.friction:
                    report.friction.append(line)

            # Every other power develops too. The player and the tracked agents drive
            # their own economies; the master AI plays everyone else's, so the whole world
            # industrialises rather than freezing while a builder walks away from it.
            # Inside the loop so a yard that empties in March is refilled in April rather
            # than standing idle until next year.
            # **`round_date`, not `self.world_date`.** The clock on the engine still
            # reads the date the jump *started* -- `_advance` produced the jump's dates and
            # nothing writes one back until the turn is sealed -- so every event this wrote
            # was filed under last turn's date. A briefing orders its news by date, the
            # causality layer reads spans off it, and an investment made in April was
            # recorded as having happened in March. It is now recorded in April even when
            # April is the fourth round of a jump that ends in December.
            investment = autonomous_investment(
                self.conn, self.world_id, turn=turn, date=round_date,
                exclude=frozenset(self.study_subjects),
            )
            report.world_investments += len(investment.invested)

        # Whether each government survives what its country has been through. **Outside
        # the round loop, unlike every flow above it, and that is not a style choice.**
        # The stream is keyed on `(world, turn, nation)` -- deliberately, so call order
        # cannot shift it -- which means running it six times inside one turn re-derives
        # the *same* stream and redraws the *same* number six times. A mock run caught it:
        # 242 nations assessed 48 times over four years produced zero changes of
        # government, because 48 calls were 8 independent draws. Run once per turn with
        # the turn's whole span, `rounds()` inside it does the several months honestly,
        # and it reads the stability the fighting and the budget just left behind.
        successions = assess_successions(
            self.conn, self.world_id, turn=turn, date=new_date, seed=self.seed,
            days=days,
        )
        report.rejections.extend(successions.problems)
        report.fell.extend(successions.fell)

        # **After the successions, not before, so the killing is the last word on who
        # governs this turn.** Before them, a nation could be assassinated and then have
        # its brand-new government fall to the same turn's coup roll -- two removals in
        # one month, of which the second was rolled against the stability the first had
        # just destroyed. The damage still reaches next turn's roll, which is the spiral
        # working as designed.
        for shock in shocks:
            if shock.get("kind") == "false_intel":
                self._plant(shock, turn=turn, date=new_date, report=report)
                continue
            nation_id = str(shock.get("nation") or "").strip()
            if not nation_id:
                continue
            struck = shock_assassination(
                self.conn, self.world_id, nation_id=nation_id, turn=turn, date=new_date,
                sponsor=str(shock.get("sponsor") or "").strip() or None,
            )
            report.rejections.extend(struck.problems)
            if struck.happened:
                report.shocks.append(struck.text)

        # And whatever the era itself asked for, which is nothing at all unless the
        # scenario set a rate -- see `assess_shocks` for why that has to mean no draw
        # rather than a draw that fails.
        for drawn in assess_shocks(
            self.conn, self.world_id, turn=turn, date=new_date, seed=self.seed,
            days=days,
        ):
            report.rejections.extend(drawn.problems)
            if drawn.happened:
                report.shocks.append(drawn.text)

        # Which agreements the fighting has made a lie of. After the war is resolved, so
        # a declaration this turn is a breach this turn, and before the score is taken, so
        # the standing a betrayal costs is reflected in it.
        breaches = assess_breaches(
            self.conn, self.world_id, turn=turn, date=new_date
        )
        report.rejections.extend(breaches.problems)
        report.betrayed.extend(breaches.breached)
        report.lapsed_treaties.extend(
            expire_commitments(self.conn, self.world_id, turn=turn, date=new_date)
        )
        # **Reorganisations that have run their course.** Beside the expiries because it is
        # the same kind of thing -- something that was set to end on a date, ending -- and
        # by date rather than by a countdown, so a year-long jump lands every shift that
        # came due inside it and a monthly one lands only what actually matured.
        report.doctrines_settled.extend(
            settle_shifts(self.conn, self.world_id, turn=turn, date=new_date)
        )
        # **A promise that has been kept stops being an obligation.** Beside the expiries
        # rather than with the breaches: a treaty carried out in full is the opposite of a
        # betrayal, and a report that filed them together would teach a player to distrust
        # a power that did exactly what it said.
        report.lapsed_treaties.extend(
            settle_fulfilled(self.conn, self.world_id, turn=turn, date=new_date)
        )
        # ...and what the agreements still standing say the world should be doing. **After
        # the breaches and the expiries**, so a route whose agreement was torn up this turn
        # closes this turn rather than shipping one last cargo under a treaty that no longer
        # exists.
        # What a patron owes its client, which is a standing obligation of the same shape
        # as a trade route and is settled beside one.
        report.trade_opened.extend(
            move_funding(self.conn, self.world_id, turn=turn, date=new_date)
        )
        terms = honour_terms(self.conn, self.world_id, turn=turn, date=new_date)
        report.trade_opened.extend(terms.honoured)
        # And what the governments themselves asked their delegations to arrange. **After
        # the agreements and once per turn, not per round**: an intention is a standing
        # policy rather than a flow, and a route opened twelve times inside one long turn
        # would be twelve routes. It does nothing at all with `atlas.engine.pricing` down.
        arranged = pursue_intents(
            self.conn, self.world_id, turn=turn, date=new_date)
        report.trade_arranged.extend(arranged.opened)
        report.rejections.extend(arranged.problems)
        # A route that could not be opened used to be reported to nobody and re-attempted
        # silently on every later turn -- the same invisible loop the breach path had.
        report.rejections.extend(terms.problems)

        # What became of the player's own orders, and why their numbers moved. Last,
        # because both are readings of a finished turn.
        player = self._player_nation()
        if player:
            report.why_it_moved.extend(
                breakdown_of(self.conn, self.world_id, player, turn, viewer=player).lines()
            )

        # ...and whether anybody has now won. Last of the per-turn assessments, because
        # a score is a reading of the finished world and taking it earlier would score the
        # turn against a map still being fought over. Nothing is stored: see
        # :mod:`atlas.engine.scoring`.
        victory = assess_victory(
            self.conn, self.world_id, turn=turn, date=new_date
        )
        report.victories.extend(victory.headlines)

        # An objective the map has overtaken -- a `hold` on ground just lost, a `push`
        # into ground just taken -- lapses here, before anybody is briefed on it. Left
        # standing it is shown back to the staff, restated, and refused every turn for
        # the rest of the war. Run after the fighting, when the map has settled.
        report.lapsed.extend(
            prune_objectives(self.conn, self.world_id, turn=turn, date=new_date)
        )
        # And an aim outlives its war by exactly as long as it takes to notice. A power
        # that made peace this turn is not still fighting for anything, so the aims of the
        # settled war lapse here, after the settlements have been applied.
        report.lapsed.extend(
            prune_war_aims(self.conn, self.world_id, turn=turn, date=new_date)
        )

        # **What every war cost this month, and what peace mended.** After the fighting and
        # after the settlements, so a war that ended this turn is already at peace when its
        # ledger is read -- which is what makes the first month of peace recover rather than
        # exhaust. Nothing here ends a war; it is what a country feels about one.
        report.war_weariness.extend(
            assess_war_effort(
                self.conn, self.world_id, turn=turn, date=new_date, days=days)
        )

        # What the peaces that ended earlier wars are still costing: this month's
        # reparations, and a demilitarised zone somebody has marched back into -- which is
        # a broken agreement and charged as one.
        report.war_weariness.extend(
            honour_peace_terms(self.conn, self.world_id, turn=turn, date=new_date)
        )

        # **And who answered the call, and who did not.** After the fighting resolves, so
        # an ally that entered this war *this* turn counts as having come -- which is the
        # generous reading and the correct one. Article 5 stays prompted rather than
        # automatic; what this adds is that ignoring it is no longer free.
        answers = assess_unanswered_calls(
            self.conn, self.world_id, turn=turn, date=new_date)
        report.betrayed.extend(answers.unanswered)
        report.war_weariness.extend(answers.answered)
        report.rejections.extend(answers.problems)

        # And which truces are about to run out. The lapse itself is
        # `treaties.expire_commitments`, which retires any agreement whose term has run --
        # an armistice is not special there and a second expiry path would be two ways for
        # the same thing to happen.
        report.war_weariness.extend(
            assess_armistices(self.conn, self.world_id, turn=turn, date=new_date)
        )

        # **And what the country made of all of it.** Last, so it reads a settled turn:
        # the fighting, the settlements and the agreements are all written by here, and a
        # constituency reacting to half a turn would be reacting to something that had not
        # finished happening.
        report.at_home.extend(
            assess_constituencies(
                self.conn, self.world_id, turn=turn, date=new_date, days=days)
        )

        # And what each service could still see at the end of it. Networks drift toward
        # what the relationship alone sustains, so one raised for a war that has ended
        # settles back rather than standing forever. Nothing is reported to the player:
        # what your own service can see is not news, and what somebody else's can see is
        # not yours to know.
        assess_networks(
            self.conn, self.world_id, turn=turn, date=new_date, days=days)

        # And what each victim has worked out about who has been acting against it. After
        # the networks, because the tier is computed from one against the other, and a
        # turn's investigation should read the services as they stand at the end of it.
        report.rejections.extend(
            assess_attribution(
                self.conn, self.world_id, turn=turn, date=new_date, days=days).problems)

        # And whether any of it came out. After attribution, because the two are
        # independent and reading them in a fixed order is what keeps a turn reproducible.
        blown = assess_exposure(
            self.conn, self.world_id, turn=turn, date=new_date, seed=self.seed,
            days=days)
        report.rejections.extend(blown.problems)
        report.exposed.extend(blown.blown)

        # **The gambit decision runs before the build decision, and the order is the
        # whole fix.** It used to run after, and PT30 showed what that costs: a staff
        # commits its purse to works and research, and is then asked to gamble with the
        # residue -- 16 of 20 slots declined for lack of cash, with treasuries at 0.6 to
        # 4.4 against a minor stake of 6. Disclosure had already been tried (decision 34's
        # purse lines) and was not enough.
        #
        # The asymmetry is what makes this the right order rather than an arbitrary one: a
        # stake is 6-34 and a public work is 20-130, so **the small claim can be absorbed
        # by the large decision and the large claim cannot be absorbed by the small one.**
        # A staff that spends everything on a steelworks and cannot then bribe anybody is
        # making a real trade-off; a staff that is never offered the choice is not.
        # The build view reads live treasury, so it sees exactly what the gambit left.
        await self._run_gambits(turn, new_date, report, ledger, span=span)
        if self.progress is not None:
            self.progress.finish("month")

        # Tracked nations act on their own account, after the world has moved. Each is a
        # separate call, and a failure in one is contained: the world simulation has
        # already been committed, so a tracked agent going wrong costs its own
        # contribution rather than the whole turn.
        await self._run_tracked_agents(turn, report, date=new_date, days=days)

        # **A war that has been won stops -- but only after everyone has had their turn to
        # end it themselves.** Every other way a war could end goes through somebody
        # proposing terms, so a war nobody negotiated ran forever: five play-test games
        # finished with Ukraine at zero provinces, alive, and still listed at war.
        #
        # **Deliberately last, and that ordering is the whole of what makes it safe.** Run
        # inside the resolution rounds -- where it was first put -- it fired the instant a
        # power lost its final province, which pre-empted the most dramatic peace in the
        # game: a beaten government suing for terms could never be heard, because the war
        # was already over by the time it spoke. Here a power that offers terms gets the
        # settlement it negotiated, and only one that says nothing is recorded as having
        # simply run out of ground.
        #
        # Once per turn rather than once per round, for the same reason: a nation is not
        # beaten because it was momentarily landless in the middle of a season's fighting.
        beaten = assess_capitulation(
            self.conn, self.world_id, turn=turn, date=new_date,
        )
        report.rejections.extend(beaten.problems)
        report.capitulated.extend(beaten.capitulated)
        report.eliminated.extend(beaten.eliminated)
        report.wars_ended += beaten.wars_ended

        # **And the last look at the map, after everything that could have moved it.**
        #
        # Four passes move a province and three of them are above this line: partisans
        # putting a flag back up, a peace term sending an occupied province home, and a
        # tracked nation letting a colony go on its own turn. Any of them can leave a
        # country besieging ground it now holds -- a contest that can never end, because
        # `captured` requires that the attacker does *not* hold it -- and reconciling
        # inside the resolution cannot see a transfer that happens after the resolution.
        # South Vietnam at Quang Tri, in five recorded runs.
        report.rejections.extend(
            reconcile_self_sieges(
                self.conn, self.world_id, turn=turn, date=new_date)
        )

        # The world's institutions, last of all: a body reacts to the turn that has just
        # happened, so everything it might take an interest in is already on the record.
        report.sanctions.extend(
            apply_sanctions(
                self.conn, self.world_id, turn=turn, date=new_date, days=days
            )
        )
        await self._run_sessions(turn, new_date, report)
        self.conn.commit()

        # Recompute the roster for next turn, now that this turn's wars and agreements
        # are on the record.
        if self.progress is not None:
            self.progress.start("focus")
        # **The turn is passed in, not read from the clock.** The roster is recomputed
        # before `world.turn_number` advances a few lines below, so a batch built from the
        # world row would be filed under the outgoing turn -- outside the range a rewind to
        # that turn deletes, which is precisely the hole this write was moved into diffs to
        # close.
        report.promoted, report.demoted = apply_focus(
            self.conn, self.world_id, player_nation=self._player_nation(),
            turn=turn, date=new_date,
        )
        if self.progress is not None:
            self.progress.finish(
                "focus",
                detail=f"+{len(report.promoted)} / -{len(report.demoted)}",
            )

        # The clock advances whether or not every proposal survived validation. A turn
        # where the simulator overreached and was refused still happened -- time passed,
        # the orders were issued, and the rejections are on the record.
        self.conn.execute(
            "UPDATE world SET turn_number = ?, world_date = ? WHERE id = ?",
            (turn, new_date, self.world_id),
        )
        self.conn.execute(
            "UPDATE turn SET end_date = ? WHERE world_id = ? AND number = ?",
            (new_date, self.world_id, turn),
        )

        # Fold anything newly old enough into digests. Not an optimisation -- without it
        # the briefing grows without bound and turn 200 is unreachable.
        if self.progress is not None:
            self.progress.start("digest")
        try:
            # Routed through `_invoke` like every other model call in the turn. It used to
            # call `provider.invoke` directly, which meant no retries on a rate limit and
            # -- worse -- no entry in `report.calls`: a turn that made 486 calls reported
            # eight. The count is the only instrument anybody has on what a jump costs.
            async def _digest_invoke(provider, request):
                result = await self._invoke(provider, request, stage="digest")
                report.calls += 1
                return result

            written = await consolidate(
                self.conn, self.world_id, providers=self.providers, router=self.router,
                invoke=_digest_invoke,
            )
            if self.progress is not None:
                self.progress.finish(
                    "digest",
                    detail=f"{len(written)} digest(s)" if written else "nothing old enough yet",
                )
        except Exception as exc:  # noqa: BLE001 - a digest must never cost a resolved turn
            report.failures.append(f"history could not be consolidated: {exc}")
            if self.progress is not None:
                self.progress.finish("digest", state=StageState.FAILED)

        # **Partial means the turn lost work, not that anything was refused.**
        #
        # `rejections` is the readable list of everything the engine said no to, and most
        # of what lands in it is the engine working: "you already hold that province",
        # "that programme is already under way", "your forces cannot reach there". None of
        # those costs the turn anything -- the model asked, the engine answered.
        #
        # Judging status on that list made PT20 report five turns out of five as partial,
        # including one that applied six batches out of six and was marked down because
        # China was told a workshop was already being built. With five tracked agents
        # nearly every turn contains a refusal of some kind, so the word stopped
        # distinguishing the case it exists for: a provider dying mid-turn with the
        # resolved work kept. `rejected_batches` counts exactly that -- a whole proposal
        # thrown away by validation -- which is the thing a player needs told.
        #
        # The same argument, one level down, is why a tracked agent with nothing to say is
        # `tracked_quiet` rather than a failure. See `_run_tracked_agents`.
        if self.progress is not None:
            self.progress.start("seal")
        report.status = (
            TurnStatus.SEALED if not report.rejected_batches and not report.failures
            else TurnStatus.PARTIAL
        )
        self._set_phase(TurnPhase.SEALED, report.status, turn=turn)
        if self.progress is not None:
            self.progress.finish(
                "seal",
                state=(StageState.DONE if report.status is TurnStatus.SEALED
                       else StageState.FAILED),
            )
        return report


    async def _run_sessions(
        self, turn: int, date: str, report: JumpReport
    ) -> None:
        """Let an international body sit, if something happened that is its business.

        **At most one session a turn, world-wide**, and only about the gravest thing that
        happened. A session is a model call; triggers are rare by construction, but "rare
        by construction" is not a budget, and the cap is.

        A failure here costs the session and nothing else. The turn is already resolved.
        """
        bodies = [org for org in organisations_of(self.conn, self.world_id) if org.credible]
        if not bodies:
            return
        triggers = triggers_this_turn(self.conn, self.world_id, turn)
        if not triggers:
            return
        trigger = triggers[0]

        # A body does not sit in judgement on itself, and a universal body has
        # jurisdiction over everybody else.
        body = next(
            (
                org for org in bodies
                if org.nation_id != trigger.accused
                and has_jurisdiction(self.conn, org.nation_id, org.kind, trigger.accused)
            ),
            None,
        )
        if body is None:
            return

        if bots.controls_role("organisation"):
            result = _BotAnswer(bots.organisation_decision(
                self.conn, self.world_id, str(trigger.accused), date=date))
            report.bot_decisions += 1
        else:
            decision = self.router.route(Role.ORGANISATION, 0.0)
            provider = self.providers.get(decision.provider_key)
            if provider is None or not provider.available():
                return
            result = await self._invoke(
                provider,
                ProviderRequest(
                    role=Role.ORGANISATION,
                    prompt=build_session_view(
                        self.conn, self.world_id, org=body, trigger=trigger
                    ),
                    model=decision.model,
                    schema=ORGANISATION_SCHEMA,
                    timeout_s=120.0,
                ),
                stage="session",
            )
            report.calls += 1
        if result.failed:
            report.failures.append(
                f"{body.name} did not sit: "
                f"{result.failure.message if result.failure else 'unknown failure'}"
            )
            return

        outcome = convene(
            self.conn, self.world_id, org=body, trigger=trigger,
            payload=result.data if result.data is not None else result.text,
            turn=turn, date=date, seed=self.seed,
        )
        self.conn.commit()
        report.rejections.extend(outcome.problems)
        if outcome.happened:
            report.sessions.append(
                f"{body.name}: {outcome.narrative} "
                f"(odds {outcome.odds:.0%}, standing "
                f"{outcome.authority_before:.0f} -> {outcome.authority_after:.0f})"
            )
        elif outcome.refused:
            report.sessions.append(f"{body.name} resolved nothing: {outcome.refused}")

    async def _run_gambits(
        self, turn: int, date: str, report: JumpReport,
        ledger: dict[tuple[str, str], float], span: float = 1.0,
    ) -> None:
        """Let a tracked nation attempt something whose outcome is not certain.

        Tracked nations only, and at most :data:`MAX_GAMBITS_PER_TURN` of them, because
        this is a *second* call per nation on top of the agent call and the Focus cap is
        the only thing standing between the turn loop and an unbounded bill. Two is
        enough for the world to be up to something and few enough that a jump does not
        quietly double in cost.

        The ledger is the turn's, shared with freeform, so the two channels cannot be
        combined to walk one column further than either allows alone.

        A failure here costs the attempt and nothing else. The turn is already resolved.
        """
        pool = [
            entry for entry in tracked_nations(self.conn, self.world_id)
            if entry['id'] != self._player_nation()
        ]
        take = max(0, self.gambits_per_turn)
        if not pool or not take:
            return
        # **Rotated by turn, not sliced off the front.** `tracked_nations` is ORDER BY
        # name, so a bare `[:2]` asked the same two alphabetically-first powers every turn
        # for the life of the game. PT23 is the proof: once Focus promoted China and
        # Germany, they took both slots from turn 2 onward and **Russia and Ukraine were
        # never offered a gambit again** -- the two belligerents, the two nations on the
        # map with the most to scheme about, locked out by an ORDER BY name. It also
        # skewed the balance investigation, because the two powers the channel did ask
        # happened to be mid-development and short of cash, which read as "nobody can
        # afford a gambit" when the world at large was solvent throughout.
        #
        # The offset is the turn number, so the choice is deterministic, depends on
        # nothing but the clock, and rewinds exactly -- replaying turn 7 asks whoever
        # turn 7 asked. Over a game every tracked power comes up.
        start = (turn * take) % len(pool)
        candidates = [pool[(start + offset) % len(pool)] for offset in range(min(take, len(pool)))]

        botted = bots.controls_role("gambit")
        decision = self.router.route(Role.GAMBIT, 0.0)
        provider = self.providers.get(decision.provider_key)
        # A botted channel still needs its provider if a protected nation is in the pool:
        # a study subject's gambit is never a bot's.
        if (not botted or any(str(e["id"]) in self._protected() for e in candidates)) and (
                provider is None or not provider.available()):
            return

        for index, entry in enumerate(candidates):
            nation_id = str(entry['id'])
            if botted and nation_id not in self._protected():
                result = _BotAnswer(bots.gambit_decision())
                report.bot_decisions += 1
            else:
                result = await self._invoke(
                    provider,
                    ProviderRequest(
                        role=Role.GAMBIT,
                        prompt=build_gambit_view(
                            self.conn, self.world_id, nation_id=nation_id, span=span
                        ),
                        model=decision.model,
                        schema=GAMBIT_SCHEMA,
                        timeout_s=120.0,
                    ),
                )
                report.calls += 1
            if result.failed:
                report.failures.append(
                    f"{entry['name']} attempted nothing: "
                    f"{result.failure.message if result.failure else 'unknown failure'}"
                )
                continue

            outcome = apply_gambit(
                self.conn, self.world_id, nation_id=nation_id,
                payload=result.data if result.data is not None else result.text,
                turn=turn, date=date, seed=self.seed, ledger=ledger, index=index,
                span=span,
                # **The ceiling scales with the stake, or the stake is a swindle.** The
                # per-field cap is a flat 6.0 a turn, so a year-long turn charging 73 for
                # a `minor` stake would have delivered a magnitude of 18.25 trimmed to
                # 6.0 -- four times the price for the same movement. That is precisely the
                # trap a bare cost multiply falls into, and it is why the cost, the
                # magnitude *and* the ceiling all have to be flows together.
                per_field_cap=PER_FIELD_TURN_CAP * span,
            )
            self.conn.commit()
            # Nothing evaporates: a refusal and a trimmed effect are both reported. **But
            # what a power tried is its own business**: reported to the referee and to
            # itself, named, and to nobody else -- a refused covert undertaking read out to
            # its intended victim is the undertaking announced.
            own = frozenset({nation_id})
            tried = [f"{entry['name']}: {line}"
                     for line in (*outcome.problems, *outcome.trimmed)]
            report.rejections.extend(tried)
            report.restrict(tried, own)
            if outcome.truce_broken:
                # A strike through a ceasefire is a broken agreement, reported with the
                # other broken agreements rather than as a line among the undertakings.
                report.betrayed.append(outcome.truce_broken)
            if outcome.launch is not None and outcome.launch.happened:
                # Reported apart from the ordinary undertakings, and both halves of it:
                # a strike whose reply is buried in a list of summits is a report that
                # has understated the turn.
                strike = outcome.launch
                report.nuclear.append(
                    f"{entry['name']} fired {strike.warheads} warhead(s) at "
                    f"{strike.region_id}: {strike.result} "
                    f"(odds of getting through {strike.odds:.0%})"
                )
                if strike.retaliation is not None:
                    report.nuclear.append(
                        f"the engine fired back for {strike.retaliation.launcher}: "
                        f"{strike.retaliation.warheads} warhead(s) at "
                        f"{strike.retaliation.region_id}, "
                        f"{strike.retaliation.result}"
                    )
                else:
                    report.nuclear.append(
                        f"nothing of {strike.target}'s arsenal survived to answer"
                    )
                report.nuclear.extend(strike.notes)
            elif outcome.strike is not None and outcome.strike.result != "refused":
                raid = outcome.strike
                report.gambits.append(
                    f"{entry['name']} struck {raid.region_name}: {raid.result} "
                    f"(odds of getting through {raid.odds:.0%})"
                    + (" -- " + "; ".join(raid.effects) if raid.effects else "")
                )
            elif outcome.happened:
                # **At the scope of the event it describes.** A covert undertaking that
                # worked is a SECRET event its victim cannot read; a failed one is public.
                # An outcome that names no event is the actor's alone -- a line whose
                # provenance cannot be checked is not thereby public.
                done = (f"{entry['name']}: {outcome.narrative} "
                        f"(odds {outcome.odds:.0%}, cost {outcome.cost:.0f})")
                report.gambits.append(done)
                seen_by = audience_of(self.conn, self.world_id, outcome.event_id)
                report.restrict([done], None if seen_by is None else seen_by | own)
            elif outcome.refused:
                declined = f"{entry['name']} attempted nothing: {outcome.refused}"
                report.gambits_declined.append(declined)
                report.restrict([declined], own)

    async def _run_freeform(
        self, turn: int, date: str, actions: list[dict], report: JumpReport,
        ledger: dict[tuple[str, str], float],
    ) -> None:
        """Answer the player's orders that match no mechanic.

        Player-only for now, and deliberately: a tracked agent already has three channels
        and a hard character budget, and a fourth invites it to spend its turn on colour
        instead of decisions -- which is the failure mode that hollowed these calls out
        before. Five agents narrating at the same three fields every turn would also have
        to share the per-turn ceiling, which is fiddly for something nobody has asked for.

        A failure here costs the order and nothing else. The turn is already resolved.
        """
        nation_id = self._player_nation()
        orders = freeform_orders(actions) if nation_id else []
        # **What the rate cut, said out loud.** The slice has always been right and has
        # never been reported: a player who wrote three orders no mechanic covers had one
        # disappear with no line anywhere. Raised before the early return, so a turn whose
        # *only* freeform orders were the ones over the rate still says so -- which cannot
        # happen today (the rate is 2 and the first 2 are always kept) and is exactly the
        # kind of thing a later change to `freeform_orders` would break silently.
        for cut in freeform_overflow(actions) if nation_id else []:
            said = str(cut.get("summary") or "").strip()
            report.rejections.append(
                f"only {MAX_ORDERS_PER_TURN} orders that no mechanic covers are answered in "
                "a turn; this one was not"
                + (f': "{said[:120]}"' if said else "")
            )
        if not orders:
            return

        botted = bots.controls_role("freeform")
        if not botted:
            decision = self.router.route(Role.FREEFORM, 0.0)
            provider = self.providers.get(decision.provider_key)
            if provider is None or not provider.available():
                report.failures.append(
                    f"a freeform order went unanswered: {decision.provider_key} is unavailable"
                )
                return

        for action in orders:
            text = str(action.get("summary") or action.get("target_nation") or "").strip()
            if not text:
                continue
            if botted:
                from .staff_bots import freeform_answer

                result = _BotAnswer(freeform_answer(
                    self.conn, self.world_id, nation_id=nation_id, order_text=text))
                report.bot_decisions += 1
            else:
                result = await self._invoke(
                    provider,
                    ProviderRequest(
                        role=Role.FREEFORM,
                        prompt=build_freeform_view(
                            self.conn, self.world_id, nation_id=nation_id, order_text=text
                        ),
                        model=decision.model,
                        schema=FREEFORM_SCHEMA,
                        timeout_s=120.0,
                    ),
                )
                report.calls += 1
            if result.failed:
                report.failures.append(
                    "a freeform order went unanswered: "
                    f"{result.failure.message if result.failure else 'unknown failure'}"
                )
                continue

            outcome = apply_freeform(
                self.conn, self.world_id, nation_id=nation_id, order_text=text,
                payload=result.data if result.data is not None else result.text,
                turn=turn, date=date, ledger=ledger,
            )
            self.conn.commit()
            report.rejections.extend(outcome.problems)
            if outcome.refused:
                report.freeform.append(f"refused: {outcome.refused}")
            elif outcome.narrative:
                report.freeform.append(outcome.narrative)
            # Nothing evaporates. A consequence the caps or the allowlist would not carry
            # is reported rather than dropped, which is the entire point of this channel.
            report.rejections.extend(outcome.trimmed)

    def _protected(self) -> frozenset[str]:
        """Who a bot may never play: every bloc nation and every study subject.

        Asked of the database each time, not cached, because blocs are assigned after the
        engine is built and a bloc nation must be protected from the moment it is one.
        """
        in_blocs = {str(row["id"]) for row in self.conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND bloc_id IS NOT NULL",
            (self.world_id,))}
        return frozenset(in_blocs | set(self.study_subjects))

    def _plant(self, shock: dict, *, turn: int, date: str, report: JumpReport) -> None:
        """One false report, planted through the same channel as an assassination."""
        from .false_intel import plant_false_report

        planted = plant_false_report(
            self.conn, self.world_id,
            receivers=tuple(str(n) for n in shock.get("receivers") or ()),
            accused=str(shock.get("accused") or ""), region=str(shock.get("region") or ""),
            turn=turn, date=date)
        report.rejections.extend(planted.problems)
        from .views import fresh_reports_enabled

        if planted.happened and not fresh_reports_enabled():
            # **Planted is not delivered.** Without the section the report competes for the
            # thirty-event window at salience two and loses to any war month's battles.
            report.rejections.append(
                "a false report was planted with ATLAS_FRESH_REPORTS off, so the briefing "
                "may rank it out and its receivers may never read it")
        # Named in the turn report, which only the harness and the player read; the
        # deceived nations see the report and nothing that says it was planted.
        if planted.happened:
            report.shocks.append(
                f"planted a false report in {', '.join(planted.event_ids)}: "
                f"{shock.get('accused')} massing in {shock.get('region')} "
                f"(true: {planted.true_formations} {shock.get('accused')} land formations "
                f"there, held by {planted.true_holder or 'nobody'})")

    def _declare_for(
        self, nation_id: str, wanted: dict, *, turn: int, date: str, report: JumpReport
    ) -> None:
        """Let a tracked power open a war, if `warpath` will have it.

        A thin wrapper over :func:`declare_for`, which EVE calls too -- see there. Moved out
        of the class so that one door serves both, rather than a copy that would drift.
        """
        outcome = declare_for(
            self.conn, self.world_id, nation_id, wanted, turn=turn, date=date)
        report.declared_wars.append(outcome.line)
        report.rejections.extend(outcome.problems)

    def _charge_for_declaring(
        self, aggressor: str, target: str, *, turn: int, date: str
    ) -> None:
        charge_for_declaring(
            self.conn, self.world_id, aggressor, target, turn=turn, date=date)

    async def _run_tracked_agents(
        self, turn: int, report: JumpReport, *, date: str, days: int = 30
    ) -> None:
        """Give each tracked nation its own call.

        Failures here are deliberately *not* fatal. The world simulation is already
        committed by this point, so a tracked agent that times out costs its own
        contribution and nothing else -- the turn is marked partial rather than discarded.
        Losing an expensive, successful world simulation because an optional extra failed
        would be the worst possible trade.

        **Three phases, and the split is what makes the turn faster without making it less
        deterministic.**

        *Read.* Every briefing is built first, sequentially, from the state as it stands
        now. One snapshot for all of them -- so no tracked power sees another's moves
        inside the same turn, which also removes the first-mover advantage the old
        sequential loop handed to whoever sorted first by name.

        *Call.* All of them at once. These are subprocesses and the wait is entirely I/O,
        so five calls of four minutes become one wait of four minutes. **No database
        access happens here**, which is what keeps a single sqlite3 connection safe: the
        views are already built and nothing is applied yet.

        *Apply.* Sequentially, in **nation id order** -- not the order the calls returned,
        which is a race, and not the roster order, which is `ORDER BY name` and would
        reshuffle if somebody renamed a country. Two agents that reasoned from the same
        snapshot may propose writes to the same field; the first lands and the second is
        caught by CORE-004's compare-and-swap and severed, which is exactly the machinery
        decision 107 built. Nothing here weakens a rule to make the parallelism work.
        """
        # **A nation a bloc plays is not also a tracked agent.** Focus never promotes a
        # bloc nation, but one already tracked when the blocs were assigned stayed on the
        # roster until the end of the next jump, and was asked twice in the same turn --
        # once by its bloc's model and once by the role pin. `bloc_id` is NULL in every
        # world that is not an EVE match, so this changes nothing anywhere else.
        in_blocs = {
            str(row["id"]) for row in self.conn.execute(
                "SELECT id FROM nation WHERE world_id = ? AND bloc_id IS NOT NULL",
                (self.world_id,))
        }
        roster = [entry for entry in tracked_nations(self.conn, self.world_id)
                  if entry["id"] not in in_blocs]
        # Study subjects join the roster whether or not Focus tracks them, once each, and
        # never a bloc nation -- a bloc nation is the bloc's, and asking it twice was the
        # bug the `in_blocs` filter above exists for.
        listed = {str(entry["id"]) for entry in roster}
        names = {str(r["id"]): str(r["name"]) for r in self.conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ? AND is_alive = 1",
            (self.world_id,))}
        roster += [{"id": n, "name": names[n], "since_turn": None, "reason": "study subject"}
                   for n in sorted(self.study_subjects)
                   if n not in listed and n not in in_blocs and n in names]
        protected = self._protected()
        prepared: list[tuple[dict, object, ProviderRequest]] = []
        # **A bot answers in the read phase, from the same snapshot every model reads.**
        # Its answer joins the apply phase below exactly where a model's would, so it goes
        # through every gate a model's does; what it skips is only the call.
        answered: dict[str, _BotAnswer] = {}
        for entry in roster:
            nation_id = entry["id"]
            if bots.controls_nation(str(nation_id)) and str(nation_id) not in protected:
                answered[str(nation_id)] = _BotAnswer(bots.nation_decision(
                    self.conn, self.world_id, str(nation_id), turn=turn))
                prepared.append((entry, None, None))
                continue
            pinned = self.study_subjects.get(str(nation_id))
            decision = (RoutingDecision(
                role=Role.TRACKED_AGENT, provider_key=pinned[0], model=pinned[1],
                band=Band.LOW, score=0.0, factors=("study subject: pinned",))
                if pinned else self.router.route(Role.TRACKED_AGENT, 0.0))
            provider = self.providers.get(decision.provider_key)
            if provider is None or not provider.available():
                report.failures.append(
                    f"{entry['name']} could not act: {decision.provider_key} is unavailable"
                )
                continue
            if self.progress is not None:
                self.progress.start(f"tracked:{nation_id}")
            view = build_tracked_agent_view(
                self.conn, self.world_id, nation_id=nation_id, days=days
            )
            prepared.append((
                entry,
                decision,
                ProviderRequest(
                    role=Role.TRACKED_AGENT,
                    prompt=view.text,
                    model=decision.model,
                    schema=diff_batch_schema(role=Role.TRACKED_AGENT),
                    timeout_s=300.0,
                ),
            ))

        if not prepared:
            return

        called = await asyncio.gather(*(
            # The government this call is for travels with it, so the routing log can say
            # who a model was playing and not merely that it answered.
            self._invoke(self.providers[decision.provider_key], request,
                         nation_id=str(entry["id"]))
            for entry, decision, request in prepared if request is not None
        ))
        from_models = iter(called)
        results = [answered[str(entry["id"])] if request is None else next(from_models)
                   for entry, _decision, request in prepared]

        # **Sorted by nation id, not by which call came back first.** Arrival order is a
        # race and would make the same world replay differently.
        for (entry, decision, _request), result in sorted(
            zip(prepared, results, strict=True), key=lambda pair: str(pair[0][0]["id"])
        ):
            nation_id = entry["id"]
            if isinstance(result, _BotAnswer):
                report.bot_decisions += 1
            else:
                report.calls += 1
                _note_accepted(report, Role.TRACKED_AGENT,
                               answered=not result.failed, failed=result.failed)

            if result.failed:
                report.failures.append(
                    f"{entry['name']} could not act: "
                    f"{result.failure.message if result.failure else 'unknown failure'}"
                )
                if self.progress is not None:
                    self.progress.finish(f"tracked:{nation_id}", state=StageState.FAILED)
                continue

            # **Everything refused below is what this power tried**, and it is reported to
            # the referee and to the power itself -- never to the player, who used to read
            # every tracked staff's refused orders verbatim, covert ones included. Tagged
            # once at the end of the body rather than at each of the twenty-odd sites that
            # file a refusal, so a site added later cannot forget to.
            refused_from, severed_from = len(report.rejections), len(report.refused_diffs)

            # One power's unreadable answer is that power failing to act, not the turn
            # failing: see the world simulator's parse above for why it can happen.
            try:
                batch = parse_batch(
                    result.data if result.data is not None else result.text,
                    world_id=self.world_id,
                    turn=turn,
                    role=Role.TRACKED_AGENT,
                    model=bots.BOT_MODEL if decision is None else decision.model,
                    # The subject of the information-asymmetry rule: a tracked agent
                    # speaks for one nation and is held to what that nation could know.
                    nation_id=nation_id,
                )
            except (KeyError, TypeError, ValueError) as exc:
                report.failures.append(
                    f"{entry['name']} could not act: its answer could not be read as a "
                    f"batch: {exc!r}"[:300])
                if self.progress is not None:
                    self.progress.finish(f"tracked:{nation_id}", state=StageState.FAILED)
                continue
            outcome = apply_batch(
                self.conn, batch, difficulty=self.difficulty, days=days
            )

            # **A war of its own, before anything else it does this turn.** A power that
            # opens a war should be spending the rest of the turn on the war it just
            # opened, and the objectives and build orders below are read against the world
            # this leaves behind. It sat below them once, and a power that declared war
            # and pushed into its new enemy in the same breath had the push refused for a
            # war it had already opened.
            if batch.declare_war:
                self._declare_for(nation_id, batch.declare_war, turn=turn, date=date,
                                  report=report)

            # Objectives are recorded separately from the batch and after it, because
            # they are not diffs the nation proposed -- they are intent the engine checks
            # against the map and then writes itself. A batch refused for some unrelated
            # overreach must not cost this power its say in its own war.
            declared = declare_objectives(
                self.conn,
                self.world_id,
                nation_id=nation_id,
                declared=batch.military_objectives,
                turn=turn,
                # The turn's own date, for the reason given at `autonomous_investment`:
                # the engine's clock still reads the date the jump started.
                date=date,
            )
            # **Refused and deferred are different answers and go to different places.**
            # A refusal is *no*; a deferral is *yes, and it waits* -- the objective is
            # recorded, stands, and is pressed the moment the front reaches it. Filing
            # both under `rejections` made an accepted objective read as a turned-down one
            # everywhere downstream.
            self._refused(
                report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                channel="military_objectives", reasons=declared.refused,
            )
            report.objectives_deferred.extend(
                f"{entry['name']}: {problem}" for problem in declared.deferred
            )
            if declared.total:
                report.objectives_declared.append(entry["name"])

            # **And what the country itself is to be.** Recorded here for the reason the
            # objectives and the aims are: it is intent the engine checks, prices and then
            # writes itself, so a batch refused for some unrelated overreach must not cost
            # a nation the one decision that takes half a year to matter.
            if batch.doctrine_shift:
                from .doctrines import adopt, axis_of_stance

                axis = axis_of_stance(batch.doctrine_shift)
                if axis is None:
                    self._refused(
                        report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                        channel="doctrine_shift",
                        reasons=[f"{batch.doctrine_shift!r} is not a doctrine"],
                    )
                else:
                    shift = adopt(
                        self.conn, self.world_id, nation_id=nation_id, axis=axis.id,
                        stance=batch.doctrine_shift, turn=turn, date=date)
                    if shift.ok:
                        report.doctrine_shifts.append(shift.described)
                    else:
                        self._refused(
                            report, nation_id=nation_id, name=str(entry["name"]),
                            turn=turn, channel="doctrine_shift",
                            reasons=[shift.rendered()],
                        )

            # **What the war is for, on the same footing as what the army is doing.**
            # Recorded here rather than inside the batch for the reason the objectives
            # are: it is intent the engine checks against the world and then writes
            # itself, and a batch refused for some unrelated overreach must not cost a
            # belligerent its terms. Nothing on this path can reach `relation.at_war`.
            aims = declare_war_aims(
                self.conn,
                self.world_id,
                nation_id=nation_id,
                declared=batch.war_aims,
                turn=turn,
                date=date,
            )
            self._refused(
                report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                channel="war_aims", reasons=aims.refused,
            )
            for enemy_id in aims.wars_restated:
                report.war_aims_declared.append(
                    f"{entry['name']} against {enemy_id}: "
                    + ", ".join(
                        aim.kind.replace("_", " ") + (f" {aim.subject_id}" if aim.subject_id else "")
                        for aim in aims.accepted if aim.enemy_id == enemy_id
                    )
                )

            # **And what it will stop for.** The last of the intent channels, and the one
            # a beaten power had no way to use: it could be shown its exhaustion, its
            # casualties and the engine's own reading that it was beaten, and had no
            # sentence available for *I will stop on these terms*. Routed here rather than
            # inside the batch for the reason the others are -- an unrelated overreach must
            # not cost a losing country its peace -- and the engine still decides whether
            # the other side accepts.
            offered = offer_peace(
                self.conn,
                self.world_id,
                nation_id=nation_id,
                declared=batch.peace_offer,
                turn=turn,
                date=date,
            )
            self._refused(
                report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                channel="peace_offer", reasons=offered.refused,
            )
            if offered.signed:
                # **One list, because `JumpReport` has no `headlines`.** The first version
                # of this appended the minted facts to `report.headlines`, which is a field
                # of `ResolutionReport` and not of this one -- so the line raised the moment
                # a tracked power's offer was actually *signed*, which no test reached: the
                # unit tests call `offer_peace` directly and the wiring test greps the
                # source. Found by the live run, on turn one.
                report.peace_offers.append(
                    f"{entry['name']} settled with {offered.commitment_id}: "
                    + "; ".join([*offered.applied, *offered.facts][:5])
                    if offered.applied or offered.facts
                    else f"{entry['name']} settled")

            # **And what it is putting to a war it is not in.** Routed here for the same
            # reason the peace offer is: an unrelated overreach in the batch must not cost
            # a war the settlement somebody outside it had finally arranged. The engine
            # decides whether either belligerent signs; nothing on this path can end a war
            # that both sides will not end.
            brokered = mediate_war(
                self.conn,
                self.world_id,
                nation_id=nation_id,
                declared=batch.mediation,
                turn=turn,
                date=date,
            )
            self._refused(
                report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                channel="mediation", reasons=brokered.refused,
            )
            if brokered.signed:
                report.peace_offers.append(
                    f"{entry['name']} brokered a peace: "
                    + ("; ".join(brokered.applied[:5]) or "an end to the fighting"))

            # **Who this power is backing, on the same footing as what it is trying to
            # take.** Recorded here rather than inside the batch for the reason the
            # objectives are: it is intent the engine checks against the world and then
            # writes itself, and a batch refused for some unrelated overreach must not cost
            # a patron its clients. Nothing on this path can reach `relation.at_war`.
            backed = declare_support(
                self.conn,
                self.world_id,
                patron_id=nation_id,
                declared=batch.support,
                turn=turn,
                date=date,
            )
            # And the same again for anything it is sending quietly. A second call rather
            # than a flag threaded through the first: the two answers are separate
            # decisions in the schema and stay separate here, so a refusal on one cannot
            # cost the other.
            if batch.covert_support:
                quiet = declare_support(
                    self.conn,
                    self.world_id,
                    patron_id=nation_id,
                    declared=batch.covert_support,
                    turn=turn,
                    date=date,
                    covert=True,
                )
                backed.refused.extend(quiet.refused)
                backed.opened.extend(quiet.opened)
                backed.raised.extend(quiet.raised)
                backed.withdrawn.extend(quiet.withdrawn)
                backed.sources.update(quiet.sources)
            self._refused(
                report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                channel="support", reasons=backed.refused,
            )
            for line in backed.opened + backed.raised + backed.withdrawn:
                said = f"{entry['name']}: {line}"
                report.support_changed.append(said)
                # Covert backing is read by patron and client only, exactly as the event
                # it was written as is; open backing stays public.
                report.restrict([said], audience_of(
                    self.conn, self.world_id, backed.sources.get(line)))

            # ...and what it is letting go of. The diplomatic end of decolonisation: the
            # momentum accrues on its own and this is the only thing a holder can *do*
            # about it short of holding the ground harder.
            for region_id in batch.grant_independence[:OBJECTIVE_LIMIT]:
                new_state, why = decolonise(
                    self.conn, self.world_id, region_id=str(region_id),
                    granted_by=nation_id, turn=turn, date=date,
                )
                if new_state is None:
                    self._refused(
                        report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                        channel="grant_independence", reasons=[why],
                    )
                else:
                    report.freed.append(
                        f"{entry['name']} recognised the independence of {new_state}")
            _note_unbacked_narrative(
                self.conn, self.world_id, report, batch=batch,
                nation_id=nation_id, name=str(entry["name"]),
                turn=turn, date=date, applied=outcome.applied,
            )
            self.conn.commit()

            # What this power wants its trade delegations to go and arrange. Kept out of
            # the diff batch for the reason build intent is: a refused steelworks must not
            # cost this power its purchasing policy, and the two compete for nothing.
            # `atlas.engine.intent.propose` does every check -- the good, the counterparty,
            # the reach, the cap -- and writes the row itself, so nothing a model wrote
            # reaches the table unexamined.
            for wanted in batch.trade_intents[:INTENT_LIMIT]:
                adopted, why = propose_intent(
                    self.conn, self.world_id, nation_id=nation_id,
                    kind=str(wanted.get("kind") or ""),
                    good=str(wanted.get("good") or ""),
                    partner_id=str(wanted.get("partner") or ""),
                    turn=turn, date=date,
                )
                if adopted is None:
                    self._refused(
                        report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                        channel="trade_intents", reasons=[why],
                    )
                else:
                    report.trade_arranged.append(
                        f"{entry['name']} sets out to "
                        + describe_intent(self.conn, adopted))
                self.conn.commit()

            # ...and what it is refusing to sell, and to whom. **Kept out of the diff batch
            # for the same reason the intentions above are, and with a sharper edge**: an
            # embargo is the one decision in this layer that costs the government making it,
            # and losing it because an unrelated steelworks was refused would be the engine
            # quietly declining to let a power spend its own money. `embargo.impose` does
            # every check -- the target, the good, the cooldown, and above all whether this
            # power is actually exporting to them -- charges the diplomatic cost, and writes
            # the row itself.
            for asked in batch.trade_embargoes[:EMBARGO_LIMIT]:
                action = str(asked.get("action") or "").strip().lower()
                target = str(asked.get("target") or "").strip()
                good = str(asked.get("good") or "").strip().lower()
                if action == "lift":
                    lifted, why = lift_embargo(
                        self.conn, self.world_id, proposer_id=nation_id,
                        target_id=target, good=good, turn=turn, date=date,
                    )
                    if lifted:
                        report.trade_embargoed.append(
                            f"{entry['name']} lifts its embargo on "
                            f"{good or 'all trade'} to {target}")
                    else:
                        self._refused(
                            report, nation_id=nation_id, name=str(entry["name"]),
                            turn=turn, channel="trade_embargoes", reasons=[why],
                        )
                elif action == "impose":
                    imposed, why = impose_embargo(
                        self.conn, self.world_id, proposer_id=nation_id,
                        target_id=target, good=good, turn=turn, date=date,
                    )
                    if imposed is None:
                        self._refused(
                            report, nation_id=nation_id, name=str(entry["name"]),
                            turn=turn, channel="trade_embargoes", reasons=[why],
                        )
                    else:
                        report.trade_embargoed.append(
                            describe_embargo(self.conn, imposed))
                else:
                    self._refused(
                        report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                        channel="trade_embargoes",
                        reasons=[f"an embargo is imposed or lifted; {action!r} is neither"],
                    )
                self.conn.commit()

            # Build intent runs through the same order handler the player's orders do, so
            # a tracked power's steelworks is charged and queued exactly as the human's is.
            # Kept out of the batch for the same reason objectives are: an unrelated diff
            # being refused must not cost this power its industrial plan.
            orders = _build_orders_to_actions(batch.build_orders)
            if orders:
                built = apply_order_effects(
                    self.conn, self.world_id, turn=turn, date=date,
                    actions=orders, nation_id=nation_id, days=days,
                )
                if built.applied:
                    report.tracked_built.append(entry["name"])
                self._refused(
                    report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                    channel="build_orders", reasons=built.problems,
                )
                self.conn.commit()

            # Reinforcement, kept out of the build batch for the same reason build intent
            # is kept out of the diff batch: a refused steelworks must not cost this power
            # the divisions it also asked for. They compete for the treasury, which is the
            # trade that should decide it, and for nothing else.
            recruits = _formation_orders_to_actions(batch.raise_formations)
            if recruits:
                raised = apply_order_effects(
                    self.conn, self.world_id, turn=turn, date=date,
                    actions=recruits, nation_id=nation_id, days=days,
                )
                if raised.applied:
                    report.tracked_raised.append(entry["name"])
                self._refused(
                    report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                    channel="raise_formations", reasons=raised.problems,
                )
                self.conn.commit()

            # Replacements, again on their own. A power that cannot afford a new division
            # can very often afford to make good the one it has, and that is the whole
            # point of the channel -- so it must not be refused as a side effect of an
            # unaffordable steelworks proposed in the same breath.
            # The call-up, before the reinforcement it is for: a footing asked for this
            # turn should be the footing the replacements are drawn against, or the
            # decision reads as doing nothing for a month.
            if batch.mobilisation is not None:
                _level, said = set_level(
                    self.conn, self.world_id, nation_id=nation_id,
                    wanted=int(batch.mobilisation), turn=turn, date=date,
                )
                if said:
                    report.mobilised.append(said)
                self.conn.commit()

            if batch.reinforce:
                topped_up = apply_order_effects(
                    self.conn, self.world_id, turn=turn, date=date,
                    actions=[{"type": "reinforce", "summary": "send up replacements"}],
                    nation_id=nation_id, days=days,
                )
                if topped_up.applied:
                    report.tracked_reinforced.append(entry["name"])
                self._refused(
                    report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                    channel="reinforce", reasons=topped_up.problems,
                )
                self.conn.commit()

            # An empty batch applies perfectly well -- there is nothing in it to refuse.
            # Reporting that as "acted" is how five nations doing nothing, every turn,
            # read as five nations taking their turn. A nation that proposed nothing did
            # not act, and the report should say so -- under `tracked_quiet`, because
            # saying so under `failures` marked every turn of a peaceful world partial.
            _note_accepted(report, Role.TRACKED_AGENT, batch=batch, outcome=outcome)
            proposed = len(batch.diffs) + len(batch.events) + declared.total
            if outcome.applied and proposed:
                report.applied_batches += 1
                report.tracked_acted.append(entry["name"])
                _note_refusals(report, outcome, str(entry["name"]))
            elif outcome.applied:
                # A well-formed answer with nothing in it is a nation with nothing to do
                # this month, not a failure -- and calling it one marked every turn of a
                # quiet world PARTIAL and buried the real failures under five lines of
                # noise a turn. The signal is kept, because five powers saying nothing
                # every turn for twenty turns is worth seeing; it is just not an error.
                report.tracked_quiet.append(entry["name"])
            else:
                report.rejected_batches += 1
                self._refused(
                    report, nation_id=nation_id, name=str(entry["name"]), turn=turn,
                    channel="diffs", reasons=[outcome.reason],
                )
            if self.progress is not None:
                self.progress.finish(
                    f"tracked:{nation_id}",
                    # Applied-and-empty is DONE, not FAILED: the call worked and the
                    # nation had nothing to do. Only a refused proposal is a failure.
                    state=StageState.DONE if outcome.applied else StageState.FAILED,
                    detail=(
                        "acted" if outcome.applied and proposed
                        else "nothing to add" if outcome.applied
                        else "proposal refused"
                    ),
                )
            report.restrict(
                report.rejections[refused_from:] + report.refused_diffs[severed_from:],
                frozenset({str(nation_id)}))


def _note_unbacked_narrative(  # noqa: PLR0913 - one call site, every argument load-bearing
    conn: sqlite3.Connection,
    world_id: str,
    report: JumpReport,
    *,
    batch,
    nation_id: str,
    name: str,
    turn: int,
    date: str,
    applied: bool,
) -> None:
    """Say what a power's orders were, when its account of the month is all there is.

    **A power's prose and a power's orders are two different channels, and only one of
    them binds.** Krell's staff wrote *"the fleet maintains the battle line in the Narrow
    Sea"* for three consecutive months of the showcase while its fleet sat in port: the
    objective behind it was a `push` on open water, correctly refused every time by the
    guard at `ce40ab2`, and the refusal went into the turn's `rejections` while the prose
    went into `event` -- public, durable, and quoted back in every briefing afterwards. A
    reader taking the account at face value was reading a war that was not being fought.

    **The narrative is deliberately not constrained, rewritten, or checked against its own
    words.** Narration is the model's job in this design and always has been; matching
    prose against orders would mean keyword-matching free text, which is the instrument
    manufacturing its own findings. What was missing is the other half of the record, and
    that half is structural -- it needs no reading of the prose at all.

    **The first cut of this check asked only whether *anything* stood, and that was too
    narrow to catch the case it was written for.** Krell's account of contesting the
    Narrow Sea ran for three months against a standing objective of *defend home* -- a
    ``hold`` on ground it already had. One objective was enough to satisfy "something
    stands", so the check went quiet on the exact turn it existed to speak on. Absence was
    never the point; **doing nothing was**, and a defensive objective is a way of doing
    nothing that the first predicate could not see.

    So the question is now *is this power acting at all*, answered from the map in three
    ways, any one of which is enough to make the account backed and keep the engine quiet:

    * **something to take** -- a standing ``push``. ``hold`` and ``withdraw_from`` are
      orders to stay put and to give ground; neither is an intent to contest anything.
    * **something deployed** -- a formation standing anywhere this power does not hold.
      This is *the fleet sat in port* as a fact about the map rather than a reading of the
      prose: a squadron contesting a sea is **in** that sea, which nobody holds, and one
      in harbour is on its own coast, which it does. It separates the two without knowing
      what a sea is.
    * **something being fought over** -- an open ``contest`` with this power on either
      side. A power under invasion is manifestly at war whatever its orders say, and its
      account is backed by the fighting on its own soil. Without this clause every
      defender in the game would collect one of these every month.

    All three false, plus at war, plus it published an account, is a power whose month
    happened entirely in prose.

    The engine writes an event of its own rather than only a report line, because the
    briefing is where the misleading happens: the report is read once and the event is
    read for the rest of the game. It states only what the engine knows and makes no
    claim about the account beside it -- and it says *which* of the two shapes it found,
    because "no orders at all" and "orders that commit to nothing" are different facts
    about a government and a reader should not have to guess which one this was.
    """
    if not applied:
        return
    narrated = bool(getattr(batch, "events", ()) or str(getattr(batch, "narrative", "")))
    if not narrated:
        return
    at_war = conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1 LIMIT 1",
        (world_id, nation_id),
    ).fetchone()
    if at_war is None:
        return

    standing = objectives_of(conn, world_id, nation_id)
    if standing.get("push"):
        return
    # `IS NOT` rather than `<>`, and the difference is the whole of the naval case: a sea
    # nobody has won has a NULL holder, and `NULL <> 'krell'` is NULL rather than true --
    # so a fleet at sea would have read as a fleet at home and the check would have gone
    # quiet on precisely the deployment it exists to notice.
    if conn.execute(
        """
        SELECT 1 FROM unit u JOIN region r ON r.id = u.region_id
         WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) IS NOT ?
         LIMIT 1
        """,
        (world_id, nation_id, nation_id),
    ).fetchone():
        return
    if conn.execute(
        "SELECT 1 FROM contest WHERE world_id = ?"
        "   AND (attacker_id = ? OR defender_id = ?) LIMIT 1",
        (world_id, nation_id, nation_id),
    ).fetchone():
        return

    told = (
        f"{name} is at war and has no standing military objective; its forces hold "
        "where they are."
        if not standing
        else (
            f"{name} is at war; its standing orders name no ground to take, and every "
            "formation it has is inside its own territory."
        )
    )
    record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=told, visibility=Visibility.PUBLIC,
    )
    report.narrative_unbacked.append(told)


def _build_orders_to_actions(build_orders: tuple[dict, ...]) -> list[dict]:
    """Turn a tracked power's build intent into the same order shape the player uses.

    The point of routing through ``apply_order_effects`` rather than a bespoke path is that
    a tracked steelworks is then charged, queued and delivered by exactly the code that
    handles the human's -- one order handler, one set of rules, no second way to build that
    could drift from the first.
    """
    actions: list[dict] = []
    for order in build_orders:
        project = str(order.get("project") or "").strip()
        if not project:
            continue
        action = {"type": "build_project", "project": project,
                  "summary": f"begin {project}"}
        if order.get("region"):
            action["region"] = str(order["region"])
        actions.append(action)
    return actions


def _formation_orders_to_actions(raise_formations: tuple[dict, ...]) -> list[dict]:
    """Turn a tracked power's reinforcement intent into the player's own raising order.

    Same argument as ``_build_orders_to_actions``, and it matters more here: `_raise_units`
    is where a formation is priced in treasury *and* manpower, stamped at the same strength
    a player's is, and refused when neither will stretch. A bespoke path would be a second
    way to put an army on the map, and the second way is always the one that forgets to
    charge for it.

    The kind is passed through **unresolved on purpose**. Naming a kind the scenario does
    not define is refused by the handler with a reason the power is told, and the
    alternative -- falling back to infantry -- is the exact bug that docstring warns about:
    an order for armour that quietly becomes something else.
    """
    actions: list[dict] = []
    for order in raise_formations[:FORMATION_ORDER_LIMIT]:
        kind = str(order.get("unit_kind") or "").strip().lower()
        if not kind:
            continue
        try:
            quantity = int(order.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        quantity = max(1, min(FORMATIONS_PER_ORDER, quantity))
        action = {"type": "raise_units", "unit_kind": kind, "quantity": quantity,
                  "summary": f"raise {quantity} {kind}"}
        if order.get("region"):
            action["to_region"] = str(order["region"])
        actions.append(action)
    return actions


def _advance(iso_date: str, days: int) -> str:
    """Advance an in-world date.

    Falls back to leaving the date untouched rather than raising: a scenario with an
    unparseable date should still be playable, and a broken clock is a cosmetic problem
    next to a turn that cannot run at all.
    """
    try:
        parsed = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date
    return (parsed + timedelta(days=days)).isoformat()


def _round_dates(iso_date: str, days: int, count: int) -> tuple[str, ...]:
    """The world date each round of a jump lands on, one per round, in order.

    **A jump is one decision and several months, and each of those months happened on its
    own day.** `jump` computes the jump's *end* date once, and the round loop used to hand
    that one date to all twelve rounds of a yearly turn: a year of revenue, unrest,
    attrition, partisans and shipbuilding was all written down as having happened on the
    26th of December. Measured, that is 201 events on 1 distinct date where a monthly game
    of the same year records 219 on 12 -- and everything that reads a *gap* between
    recorded dates saw a world in which every war declared that year was simultaneous with
    every peace signed that year: `concert`'s restraint window, `alerts`, `answering`,
    `exposure`'s cold-network check, the causality spans. It is also why
    `strategy.replan_trigger` -- fully implemented, and the mechanism that is supposed to
    give a government a second look when something happens -- could not fire in March on a
    yearly turn: there was no March for it to fire on.

    Round *k* ends ``days * (k + 1) / count`` days after the start, to the nearest whole
    day, which makes the rounds evenly spaced across exactly the span the jump covers.

    **The last entry is the jump's end date exactly, by construction rather than by
    arithmetic.** A one-round jump -- every weekly and every monthly turn, which is what
    the recorded tapes are made of -- therefore gets the single date it always got, so
    nothing that was measured at those cadences moves.

    An unreadable start date degrades the way `_advance` does: every round gets it back
    unchanged, and a scenario with a broken clock is still playable.
    """
    count = max(1, int(count))
    ending = _advance(iso_date, days)
    if count == 1:
        return (ending,)
    # `+ 0.5` rather than `round`, which is banker's rounding and can hand two adjacent
    # rounds days that run backwards at a tie. A sequence of dates that goes backwards is
    # worse than one that is a day out.
    return tuple(
        _advance(iso_date, int(days * (index + 1) / count + 0.5))
        for index in range(count - 1)
    ) + (ending,)
