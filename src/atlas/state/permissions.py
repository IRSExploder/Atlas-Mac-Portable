"""Which role may write which column. The structural half of "the model never decides".

Magnitude caps alone are not enough. A rule that says "economy may not move more than
15% per turn" still lets a Diplomacy call flip ``region.owner_nation_id`` or set
``unit.strength`` directly -- it just does it slowly. Conquest by small increments is
still the model deciding the outcome.

So the columns that *constitute* an outcome are ENGINE-only. Models propose intents and
narrate; :mod:`atlas.sim.resolve` computes what actually happened using the seeded
stream and emits the resulting diffs itself, under the ENGINE role. A model diff naming
an ENGINE-only column is rejected by rule CORE-003 regardless of how small the change is.

``ENGINE`` here means the applier acting on the engine's own resolution, never a model
wearing the engine's hat: the role is stamped by the orchestrator when it constructs the
batch, and no provider output can set it.
"""

from __future__ import annotations

from .models import Role

__all__ = [
    "ENGINE_ONLY",
    "ROLE_WRITABLE",
    "is_engine_only",
    "may_write",
    "writable_columns",
]

#: Columns only the engine may ever write, with the reason each one is reserved.
#: Keyed ``(table, column)``.
ENGINE_ONLY: dict[tuple[str, str], str] = {
    ("doctrine_stance", "stance"): "a doctrine is adopted through a priced shift, never asserted",
    ("doctrine_stance", "shifting_to"): "what a country is changing to is checked before it is written",
    ("doctrine_stance", "shift_ends_on"): "how long a reorganisation takes is the engine's to say",
    ("doctrine_stance", "adopted_on"): "when a doctrine took effect is a record, not a claim",
    ("doctrine_stance", "adopted_turn"): "when a doctrine took effect is a record, not a claim",
    ("doctrine_stance", "axis"): "which question a row answers is fixed when it is created",
    ("region", "owner_nation_id"): "territory changes hands only through resolved conflict or a ratified cession",
    ("region", "controller_nation_id"): "occupation is an outcome of resolution, not a claim",
    ("region", "geo_key"): "geometry is immutable; the map is never redrawn by a model",
    ("unit", "strength"): "combat strength is an outcome of resolution",
    ("unit", "morale"): "morale is an outcome of resolution",
    ("unit", "entrenchment"): "digging in is earned by holding ground, not declared",
    ("arsenal", "tactical"): "what an arsenal is made of follows from what was built, "
                             "never from a claim about it",
    ("nation", "mobilisation"): "a footing is asked for through `mobilisation` and moved "
                                "one step at a time by the engine, never set outright",
    ("unit", "entrenched_at"): "where a formation dug in is a fact about where it is",
    ("relation", "at_war"): "war state follows from a declaration event the engine wrote",
    # **A materialised view of the commitments, so nothing else may write it.** Decision 120
    # made this column the derived form of what `commitment` says, recomputed from scratch by
    # `sync_treaty_status` whenever an agreement is signed, broken or expires -- and then left
    # it writable by three model roles, so a model could set the column and leave it saying
    # something the agreements do not. Seven consumers read it: the invasion guard, the gambit
    # odds, the router and four briefings. The invariant checker already reports a column that
    # hides a commitment; this stops the drift instead of noticing it afterwards.
    ("relation", "treaty_status"):
        "the standing treaty is derived from the commitments; sign or break one instead",
    # **And the commitments themselves, for the same reason one level down.** Decision 120
    # closed the derived column and left the thing it is derived *from* writable by three
    # model roles -- so a power could retire an agreement it no longer liked by writing one
    # word, and every consequence of tearing up a signature was skipped: no `treaty_broken`
    # event, no 35 regard with each injured party, no legitimacy charge, no casus belli, and
    # no `breached_by_nation_id` to say who had done it. `breach()` is the whole of that
    # cost, and it was optional.
    #
    # **Three live instances in three consecutive runs**, which is what settled it. Germany
    # wrote Locarno from `pending` to `broken` on turn 1 of 1936 -- the Rhineland
    # remilitarised for nothing, against three guarantors. India did the same to its pact
    # with Pakistan on turn 1. A third model wrote a standing supply agreement to
    # `fulfilled` after one month and silently cut its own trade route.
    #
    # Every legitimate writer was already the engine: `accept_commitment` activates (and
    # DIP-004 checks it), `expire_commitments` retires, `breach` breaks. This entry only
    # removes the fourth route, which was the one with no consequences attached.
    ("commitment", "status"):
        "an agreement's standing follows from what its parties do; break the promise and "
        "the engine records the breach and charges for it",
    # The other half of the same record. A model that could write when an agreement was
    # resolved could date a betrayal out of the window the breach detector reads.
    ("commitment", "resolved_turn"):
        "when an agreement ended is a fact the engine records, not a claim",
    ("world", "turn_number"): "the turn counter belongs to the turn engine",
    ("world", "rng_seed"): "reseeding would let a model reroll an unfavourable outcome",
    ("world", "difficulty"): "difficulty is the player's setting, not the world's to change",
    ("world", "mode"): "game mode is configuration",
    ("nation", "is_tracked"): "Focus Mode promotion is ranked by the engine under a hard cap",
    ("nation", "is_player"): "who the player controls is configuration",
    ("nation", "controller"): "provider assignment is configuration",
    ("nation", "bloc_id"): "EVE bloc membership is configuration",
    ("event", "origin"): "a model cannot label its own event as engine-authored",
    ("region", "home_nation_id"): "where a region is from is a fact about the world, not a claim",
    # The whole contest row. A model that could write pressure could take ground by
    # writing a number, which is conquest-by-increment wearing a different hat.
    ("contest", "pressure"): "how far a campaign has got is an outcome of resolution",
    ("contest", "attacker_id"): "who is besieging what follows from resolution",
    ("contest", "defender_id"): "who is holding a region follows from resolution",
    ("contest", "region_id"): "a contest is identified by where it is",
    ("contest", "opened_turn"): "when a campaign began is a fact, not a claim",
    ("contest", "last_turn"): "when a front was last pressed is a fact, not a claim",
    # Objectives are declared through their own channel, where the engine checks them
    # against the map before writing anything. A diff straight into this table would let
    # a nation set an objective on ground it cannot reach, or on somebody else's behalf.
    ("military_objective", "kind"): "objectives are declared through the objectives channel, not written",
    ("military_objective", "nation_id"): "a power declares its own objectives and nobody else's",
    ("military_objective", "region_id"): "an objective is identified by the ground it names",
    ("military_objective", "declared_turn"): "when an objective was set is a fact, not a claim",
    # War aims are declared through their own channel, where the engine checks each one
    # against the war and the map. A diff straight into this table would let a power
    # declare an aim in somebody else's war, or against a country it is at peace with --
    # and, once aims bear on a settlement, write itself terms it never fought for.
    ("war_aim", "kind"): "war aims are declared through the war-aims channel, not written",
    ("war_aim", "nation_id"): "a power declares its own war aims and nobody else's",
    ("war_aim", "enemy_id"): "an aim belongs to a war the engine can see you are in",
    ("war_aim", "subject_id"): "an aim names ground the map already knows",
    ("war_aim", "declared_turn"): "when an aim was declared is a fact, not a claim",
    # The war's ledger. A power that could write its own exhaustion could fight for a
    # decade at no political cost, and one that could write somebody else's could talk a
    # rival into a surrender it had no reason to accept -- which is the whole of the
    # leverage layer, decided by narration.
    ("war_effort", "nation_id"): "a ledger belongs to the war it is a ledger of",
    ("war_effort", "enemy_id"): "a ledger belongs to the war it is a ledger of",
    ("war_effort", "opened_turn"): "when a war began is a fact, not a claim",
    ("war_effort", "last_turn"): "when it was last counted is a fact, not a claim",
    ("war_effort", "casualties"): "what a war has cost is counted from the fighting",
    ("war_effort", "charged_casualties"): "the engine's own bookkeeping",
    ("war_effort", "exhaustion"): "how tired of a war a country is, is derived from the war",
    # How the country feels about its government. A power that could write its own support
    # would govern by declaring itself popular -- the same exploit as writing its own
    # exhaustion, and with the coup machinery on the other end of it.
    ("constituency", "kind"): "a constituency is one of the five every state has",
    ("constituency", "nation_id"): "a country answers to its own people and nobody else's",
    ("constituency", "support"): "support follows from what the government did",
    ("constituency", "last_turn"): "when they last moved is a fact, not a claim",
    ("constituency", "because"): "why they moved is the engine's account of it",
    # **The whole point of the intelligence layer, enforced here.** A power that could
    # write its own network would award itself perfect sight of a rival, and one that could
    # write another's would blind it -- which is intelligence decided by narration, and the
    # exact thing "reveal real state imperfectly, never invent it" forbids.
    ("intel_network", "nation_id"): "a service belongs to the country that runs it",
    ("intel_network", "target_nation_id"): "who it is aimed at is not a claim to make",
    ("intel_network", "strength"): "what a service can see follows from what it has done",
    ("intel_network", "opened_turn"): "when it was opened is a fact, not a claim",
    ("intel_network", "last_turn"): "when it last moved is a fact, not a claim",
    ("intel_network", "because"): "why it moved is the engine's account of it",
    ("intel_network", "counter_intel"): "defending against a service is done, not declared",
    # **The guarantee of the whole attribution layer, enforced here.** A model may accuse
    # anybody it likes through the diplomacy channel and always could; what it must never be
    # able to do is *write the finding*, because a power that could set its own attribution
    # tier would manufacture a casus belli by asserting guilt.
    ("attribution", "event_id"): "which act is being attributed is a fact, not a claim",
    ("attribution", "actor_nation_id"): "who did it is the engine's to work out",
    ("attribution", "victim_nation_id"): "whose knowledge this is follows from the act",
    ("attribution", "confidence"): "certainty is earned by counterintelligence, not stated",
    ("attribution", "tier"): "the tier is computed; asserting it would forge a casus belli",
    ("attribution", "opened_turn"): "when the act happened is a fact, not a claim",
    ("attribution", "last_turn"): "when it was last assessed is a fact, not a claim",
    # A model cannot fake an exposure, avoid one by protesting innocence, or manufacture the
    # casus belli a large enough one carries. Every figure here is arithmetic over state.
    ("exposure", "event_id"): "which operation was blown is a fact, not a claim",
    ("exposure", "actor_nation_id"): "whose operation it was is the engine's to say",
    ("exposure", "victim_nation_id"): "who it was done to follows from the act",
    ("exposure", "importance"): "how big a thing it was is computed, not asserted",
    ("exposure", "guardedness"): "what the victim had invested is what it invested",
    ("exposure", "mismatch"): "how outclassed the operation was is measured",
    ("exposure", "magnitude"): "the scandal is the product of the three, not a number",
    ("exposure", "casus_belli"): "a war is earned by the magnitude, never declared into",
    ("exposure", "turn"): "when it broke is a fact, not a claim",
    # The clause is negotiated; what it *does* is the engine's. A power that could write
    # these rows would grant itself an ally's eyes without an ally.
    ("intel_term", "sharer_id"): "who shares is what the treaty says, not what you say",
    ("intel_term", "recipient_id"): "and who receives is the other half of that",
    ("intel_term", "subject_id"): "what a clause covers is what was agreed",
    # What a peace promised. A model that could write these could award itself a province
    # at the table it never took in the field, which is conquest by paperwork.
    ("peace_term", "kind"): "a term is written by the settlement that agreed it",
    ("peace_term", "obliged_id"): "who owes what follows from the settlement",
    ("peace_term", "beneficiary_id"): "who is owed follows from the settlement",
    ("peace_term", "subject_id"): "a term names ground the map already knows",
    ("peace_term", "value"): "what a term is worth is checked before it is written",
    ("peace_term", "outstanding"): "what is left to pay is counted, not claimed",
    ("peace_term", "due_turn"): "when a promise falls due is part of the promise",
    # What the world remembers is minted from an event the engine wrote. A model that could
    # write here could give the world a memory of something that never happened -- and,
    # because every briefing reads these, have every power reason from it forever.
    ("world_fact", "predicate"): "the world remembers what happened, not what it is told",
    ("world_fact", "subject_id"): "a fact is about something the map already knows",
    ("world_fact", "object_id"): "a fact is about something the map already knows",
    ("world_fact", "value"): "what a fact says is minted from the event that caused it",
    ("world_fact", "start_turn"): "when the world learned something is a fact, not a claim",
    ("world_fact", "end_turn"): "when the world stopped believing something is a fact too",
    ("world_fact", "source_event_id"): "a memory with no cause is a rumour",
    ("world_fact", "visibility"): "who knows a thing follows from how they learned it",
    ("world_fact", "confidence"): "recognition is counted, not asserted",
    # The yards. A nation that could write these could finish a battleship by editing a
    # number, which is unit creation wearing a shipwright's hat.
    ("construction", "kind"): "what is on the slipway follows from the order that laid it down",
    ("construction", "nation_id"): "a power builds its own ships and nobody else's",
    ("construction", "region_id"): "where a hull is being built is a fact, not a claim",
    ("construction", "days_remaining"): "how far along a build is, is time passing, not a claim",
    ("construction", "started_turn"): "when a keel was laid is a fact, not a claim",
    ("unit_template", "build_days"): "how long a kind takes to build is a rule of the world",
    # Industry is built, not narrated. A power that could write it could put five years of
    # steelworks into a sentence, which is the same exploit as writing siege pressure.
    ("nation", "industry"): "industrial capacity is built over years, through the queue",
    # What a nation knows how to do follows from a programme it finished, and what that
    # changed about its ships is a rule of the world rather than a claim about it.
    ("technology", "tech_id"): "knowledge is the outcome of a programme, not a declaration",
    ("technology", "completed_turn"): "when a programme finished is a fact, not a claim",
    ("technology", "secret"): "whether a programme was hidden is decided when it is begun",
    # The arsenal. A model that could write its own warhead count has an instant-win
    # button, and one that could write somebody else's could disarm a rival by narration --
    # which would delete the deterrent this whole layer exists to create. Warheads are
    # granted by a completed programme and spent by a launch, both engine-authored.
    # An international body's standing follows from whether anybody listened to it. A
    # model that could write its own authority could vote itself credibility, and one
    # that could write somebody else's membership could expel its accuser mid-session.
    ("organisation", "authority"): "a body's standing follows from whether its resolutions were honoured",
    ("organisation", "kind"): "what kind of body this is, is a fact about the world",
    ("organisation", "convened_turn"): "when a body last sat is a fact, not a claim",
    ("organisation_member", "is_permanent"): "who holds a veto is a fact about the charter",
    ("organisation_member", "nation_id"): "membership changes through a resolution, not a diff",
    # A sanction is the outcome of a vote. A nation that could write one could sanction
    # its rival by narration, and one that could delete one could lift its own.
    ("sanction", "target_nation_id"): "who a measure is against follows from the resolution that passed",
    ("sanction", "severity"): "how hard a measure bites is set by the vote that carried it",
    ("sanction", "until_turn"): "when a measure lapses is set when it is imposed",
    # Backing a side is a declaration the engine records, not a number a nation may assert
    # about itself. A model that could write its own `support.level` could arm itself.
    # How restive a colony is follows from how it is held, not from what its holder says
    # about it. A model that could write this could declare its empire content.
    ("independence", "momentum"): "how far a colony has got toward independence is derived from how it is held, not claimed",
    ("support", "level"): "who is backing whom is recorded by the engine from a declaration, not claimed",
    ("arsenal", "warheads"): "how many warheads a nation holds follows from its programmes and what it has fired",
    ("arsenal", "delivery"): "how a nation would deliver them is the outcome of what it built, not a claim",
    ("arsenal", "readiness"): "how ready an arsenal is follows from whether its upkeep was paid, and a power that could write it would maintain itself by saying so",
    ("unit_template", "base_strength"): "what a formation is worth follows from research",
    ("unit_template", "cost_treasury"): "what a formation costs is a rule of the world",
    ("unit_template", "domain"): "where a formation can stand is a rule of the world",
    # What a cargo moves and what it costs. Both follow from what one country digs up and
    # the other cannot get enough of, and a model that could write either could sign itself
    # a war's worth of oil for nothing. The volume was already unreachable -- `trade_route`
    # is in no role's allowlist -- and naming both here says *why* rather than leaving it to
    # an absence, and gives the refusal something to print.
    ("trade_route", "volume"): "what a route moves follows from one side's surplus and the other's shortfall, not from whoever drafted the deal",
    ("trade_route", "unit_price"): "what a cargo costs is settled by the engine from supply, demand and leverage; a power that could name its own price could buy a war for nothing",
    ("trade_route", "priced_turn"): "when the terms were last settled is a fact about the world, not a claim",
    # **The commercial stoppage, closed for the same reason the price is.** A power that
    # could write this column could stop somebody else's shipments by saying so -- an
    # embargo with none of an embargo's cost, and one the injured party could not appeal
    # against because no price had moved. Whether a cargo is worth taking is settled by
    # `atlas.engine.consent` from the two sides' own numbers.
    ("trade_route", "suspended_turn"): "whether a cargo is still worth taking is settled by the engine from both sides' terms, never declared",
    ("trade_route", "suspended_by"): "which side stopped a shipment is an outcome of the terms, not a claim about them",
    ("trade_route", "suspended_date"): "when a shipment stopped is recorded by the engine that stopped it, never declared",
    # **An intention is proposed, not written.** `atlas.engine.intent.propose` is the one
    # door: it checks the good, the counterparty and the reach, caps how many a power may
    # hold, and writes the row itself. A model that could write these columns could mark
    # its own intention `met` without a route ever opening, or plant one on somebody else.
    ("trade_intent", "nation_id"): "an intention belongs to the government that proposed it; propose your own instead",
    ("trade_intent", "kind"): "what an intention asks for is fixed when it is proposed",
    ("trade_intent", "good"): "what an intention is about is fixed when it is proposed",
    ("trade_intent", "partner_id"): "who an intention names is checked against the map before it is written",
    ("trade_intent", "opened_turn"): "when an intention was adopted is a record, not a claim",
    ("trade_intent", "status"): "whether an intention has been met follows from a route actually opening",
    ("trade_intent", "note"): "what stopped an intention being met is the engine's account of it",
    # **An embargo is proposed, not written, and every column of it is a consequence.**
    # `atlas.engine.embargo.impose` is the one door: it checks the proposer is actually
    # exporting to the target, checks the cooldown, charges the diplomatic cost and
    # writes the row itself. A model that could write these columns could stop somebody
    # else's exports by saying so, lift the embargo standing against it, or claim a
    # sacrifice it never made -- and the sacrifice is the only thing that makes this a
    # decision rather than a free weapon.
    ("trade_embargo", "proposer_id"): "an embargo belongs to the power that imposed it; impose your own instead",
    ("trade_embargo", "target_id"): "who an embargo is aimed at is fixed when it is imposed, and checked against the routes you actually run",
    ("trade_embargo", "good"): "what an embargo covers is fixed when it is imposed",
    ("trade_embargo", "opened_turn"): "when an embargo began is a record, not a claim",
    ("trade_embargo", "lifted_turn"): "when an embargo was lifted is a record, not a claim",
    ("trade_embargo", "opened_on"): "the date an embargo began is a record, not a claim",
    ("trade_embargo", "lifted_on"): "the date an embargo was lifted is a record, not a claim",
    ("trade_embargo", "status"): "whether an embargo still stands follows from the government that imposed it lifting it, and no sooner than the minimum it committed to",
    ("trade_embargo", "revenue_forgone"): "what an embargo has cost its proposer is computed from the cargoes it stopped and the prices the engine would have charged; a power that could name its own sacrifice could claim one it never made",
    ("trade_embargo", "episodes"): "how many times an embargo has been imposed is a record, not a claim",
    ("trade_embargo", "note"): "the engine's account of an embargo is the engine's",
    ("resource", "produced"): "what a country digs up follows from its ground, not from what it says about it",
    ("resource", "stockpile"): "what a country has in store follows from what it produced, shipped and burned",
}

#: Columns each model-facing role may write. Anything absent is refused.
#: The parser is deliberately empty: it emits *actions*, which the engine materialises
#: as events after validation. It never mutates world state directly.
ROLE_WRITABLE: dict[Role, frozenset[tuple[str, str]]] = {
    Role.WORLD_SIM: frozenset(
        {
            ("nation", "stability"),
            ("nation", "legitimacy"),
            ("nation", "economy"),
            ("nation", "military_strength"),
            ("nation", "treasury"),
            ("nation", "manpower"),
            ("nation", "personality_blurb"),
            ("nation", "standing_goals_json"),
            ("nation", "is_alive"),
            ("region", "population"),
            ("region", "development"),
            ("region", "terrain"),
            ("relation", "score"),
            ("event", "*"),
        }
    ),
    Role.DIPLOMACY: frozenset(
        {
            ("relation", "score"),
            ("commitment", "condition_text"),
            ("event", "*"),
        }
    ),
    Role.TRACKED_AGENT: frozenset(
        {
            ("nation", "standing_goals_json"),
            ("relation", "score"),
            ("event", "*"),
        }
    ),
    # Three columns and no others, which is the whole safety argument for letting a model
    # answer an order the engine has no mechanic for. Territory, units, the treasury and
    # the economy are absent by construction rather than by instruction, so an order to
    # "annex Belgium by decree" cannot annex anything however the narration is worded --
    # and the caps in FREE-001 bound even what it *can* touch.
    Role.FREEFORM: frozenset(
        {
            ("nation", "stability"),
            ("nation", "legitimacy"),
            ("relation", "score"),
            ("event", "*"),
        }
    ),
    # An adjudicated attempt reaches one column more than freeform -- the treasury --
    # and, unlike freeform, may reach *somebody else's* row. That is the whole of what
    # a gambit adds in reach, and it is why it has to be rolled for and paid for.
    # Territory, units, contests and war status are absent here exactly as they are
    # everywhere else a model can propose.
    Role.GAMBIT: frozenset(
        {
            ("nation", "stability"),
            ("nation", "legitimacy"),
            ("nation", "treasury"),
            ("relation", "score"),
            ("event", "*"),
        }
    ),
    # A body proposes a resolution; the engine decides what one *does*. So this allowlist
    # exists to be consulted at the point a resolution becomes a diff, and it stops at
    # the three soft columns a condemnation and a sanction reach. Territory, units,
    # contests and war status are absent here exactly as everywhere else -- the whole
    # point is that a Council cannot resolve that somebody has lost.
    Role.ORGANISATION: frozenset(
        {
            ("nation", "economy"),
            ("nation", "treasury"),
            ("relation", "score"),
            ("event", "*"),
        }
    ),
    Role.ARBITER: frozenset(),
    Role.PARSER: frozenset(),
    # The advisor speaks to the player and to nobody else. It is given no schema and no
    # writable column, so "advisory only" is a property of the wiring rather than an
    # instruction it could be talked out of.
    Role.ADVISOR: frozenset(),
}


def is_engine_only(table: str, column: str) -> bool:
    return (table, column) in ENGINE_ONLY


def writable_columns(role: Role) -> frozenset[tuple[str, str]]:
    return ROLE_WRITABLE.get(role, frozenset())


def may_write(role: Role, table: str, column: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)``. ``reason`` is player-facing on refusal.

    The engine role bypasses the allowlist but is still the only role that can reach an
    ENGINE-only column, which is the whole point of the split.
    """
    if role is Role.ENGINE:
        return True, ""
    if (reason := ENGINE_ONLY.get((table, column))) is not None:
        return False, f"{table}.{column} is engine-only: {reason}"
    allowed = writable_columns(role)
    if (table, "*") in allowed or (table, column) in allowed:
        return True, ""
    return False, f"role {role.value} may not write {table}.{column}"
