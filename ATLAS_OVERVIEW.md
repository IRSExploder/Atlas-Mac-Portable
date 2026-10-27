# ATLAS — what it is and how it works

An AI-driven alternate-history grand strategy sandbox. Python, PySide6, SQLite, no server,
no network at play time. You pick a scenario and a power, you write orders in plain English,
and a set of large language models plays the rest of the world against you.

That description fits a dozen projects. What makes ATLAS a particular thing is the answer to
one question — *how do you let a language model run a world without it simply making things
up?* — and almost every design decision in the codebase follows from it.

This document covers the whole engine as it stands: 1096 tests, schema version 10, two
playable eras, twenty-five logged playthroughs of which six used real models.

---

## Part I — The three ideas everything else hangs off

### 1. State is the message bus

A model never sees the database. It never sees another model's output. It carries nothing
between calls. Each call receives a **rendered view** — Markdown prose, assembled field by
field from an allowlist — and returns **diffs**, which are proposed changes to named rows
and columns.

Everything a model appears to remember is something the view builder put back in front of
it. There is no conversation, no chat history, no shared scratchpad. If a fact matters next
turn, it is in the database or it is gone.

This has a consequence that shapes the whole codebase: views are built from an **allowlist**,
never by serialising a row and deleting what should not be there. An allowlist that misses a
field produces an obviously incomplete briefing. A denylist that misses a field leaks it,
silently, forever.

### 2. The model narrates; Python decides

A model proposes; the engine writes. This is enforced structurally rather than by
instruction, in three layers:

- **Engine-only columns.** `region.owner_nation_id`, `region.controller_nation_id`,
  `unit.strength`, `unit.morale`, `relation.at_war`, the whole `contest` row, `world.rng_seed`
  and about a dozen more are in an `ENGINE_ONLY` table with a one-line reason each. No model
  of any role can write them. Territory changes hands through resolved conflict or a ratified
  cession, and through nothing else.
- **Per-role allowlists.** `ROLE_WRITABLE` maps each role to the exact `(table, column)`
  pairs it may touch. The world simulator can move six nation stats and relations. A tracked
  agent can move its own goals and its own relations. The freeform channel reaches three
  columns and no others. The advisor writes nothing at all.
- **Evidence, not outcomes.** Where a model's judgement genuinely matters, it reports
  *circumstances* from a closed vocabulary and the engine decides what they are worth. A
  battle report can say `defender_fortified` or `attacker_overextended`; the weights
  (`-0.15`, `-0.18`) live in readable Python. A model cannot describe a battle enthusiastically
  enough to win it.

The rule of thumb throughout: **the model picks a direction and a word; Python picks the
number.**

### 3. Every roll is seeded and derived, never ambient

All randomness comes from `atlas.sim.rng.derive(seed, world_id, turn, call_key)`, which
returns an independent stream for one specific decision point. The project bans
`random.random`, `random.randint`, `random.choice` and `secrets` at the lint level, with the
message *"ambient entropy breaks replay."*

`call_key` must identify the *decision*, not its order — `"battle:r14:jpn-vs-usa"`, never
`"battle:3"` — so that adding a call elsewhere in the turn cannot shift every subsequent
roll. The same world, seed and inputs produce the same outcomes, which is what makes a
replay meaningful and a bug reproducible.

---

## Part II — The state layer

### The database

One SQLite file per world, in `%LOCALAPPDATA%\Atlas\worlds`, never inside a synced folder
(the engine refuses to open a live world under a sync root — SQLite's `-wal` sidecars corrupt
through OneDrive). Twenty-six tables: `world`, `turn`, `nation`, `region`, `region_adjacency`,
`region_geometry`, `unit`, `unit_template`, `construction`, `technology`, `arsenal`, `city`,
`contest`, `military_objective`, `relation`, `commitment`, `commitment_nation`, `event`,
`event_cause`, `event_visibility`, `action`, `diff_batch`, `diff`, `digest`, `bloc`,
`routing_log`.

### Diffs and batches

Nothing changes except through a **diff**: a `(table, id, field, op, new_value)` record with
`expected_prior` for optimistic concurrency, a mandatory `reason_event_id`, and a
`visibility_scope`. Diffs arrive in **batches** attributed to a proposing role and model.

Applying a batch is: record the proposal, open a savepoint, apply every diff, run every
validation rule against the *post-apply* state, and either keep it or roll the whole thing
back. A refused batch is an ordinary outcome the player is shown, not an exception.

There is a subtlety worth knowing: if a batch fails only because some diffs were reasoned
about against state that has since moved, the engine severs those diffs and retries the
remainder. A batch is never *resumed* against stale state — it is re-derived or reduced.

### The validation rules

A registry of rules with stable ids, each a generator yielding violations. Families:

- **CORE** — every diff must cite a cause (`reason_event_id`), and the cited event must exist
  and predate the effect.
- **UNIT** — units cannot be created without a raise event and the means to pay; they must
  respect their domain (no navies inland, no armies at sea); they must belong to a living
  nation.
- **ECON** — economic aggregates may not jump more than a per-turn cap (economy 15%,
  military strength 20%, manpower 25%, treasury 50%, all scaled by the turn's length); a
  reform event that unlocks a larger change must be engine-authored; stability and legitimacy
  stay within 0–100.
- **INFO** — a change may not be more widely visible than its cause. A secret event cannot
  produce publicly observable state.
- **OWN** — territory transfers require adjacency and a legal cause.

A rule that raises is treated as a violation rather than propagating: a buggy rule **fails
closed**, because refusing a legal change is visible and recoverable while letting an illegal
one through corrupts the world silently.

### Events: the causal spine

Every diff must point at an event. Events have a **closed CHECK enum of 40 kinds**
(`battle`, `treaty_signed`, `casus_belli`, `espionage`, `narrative`, `coup`, `famine`…), an
`origin` of `ENGINE` or `MODEL`, a visibility scope, and an `event_cause` edge list so the
history is a directed graph rather than a list.

The closed enum is load-bearing: a model cannot mint a new event kind, and in particular
cannot invent a *gating* kind that a privileged rule would then honour. Every privileged rule
requires `origin='ENGINE'` on the event it cites, so a nation cannot forge itself a grievance
and act on it next turn.

This is why every scenario ships a **HISTORY block** of engine-authored events. A world that
began with no past would have nothing legal to point at, and the first turn would be
unplayable.

### Visibility

Three scopes — `public`, `bilateral`, `secret` — with an `event_visibility` table naming who
may see a restricted event. The viewer-scoped readers are used everywhere except the
referee's own briefing, which deliberately uses a separate omniscient module
(`state/referee.py`) in exactly one place.

---

## Part III — Time, turns and pacing

### The turn loop

One **jump** advances the clock by a chosen number of days and runs: record player orders as
engine events → build the referee's view → call the world simulator → validate and apply →
resolve contests → run the engine's month (budget, partisans, unrest, construction,
autonomous investment) → prune stale objectives → call each tracked nation's agent →
recompute Focus Mode → consolidate history → seal.

A turn that partially succeeded is **never discarded**. If the world simulation resolves and
a tracked-nation call then fails, the resolved work is kept and the turn is marked `partial`
— because throwing it away would cost the player a jump *and* the expensive calls that
produced it.

`partial` means the turn *lost work*: a whole batch thrown away by validation, or a provider
failure. An ordinary refused order — "you already hold that province" — is the engine
working, and does not mark the turn down.

### Turn length is scalable, and everything scales with it

The player chooses how much time a turn covers: **Week (7), Month (30), Season (90), Year
(365)**. For a long time nothing in the simulation knew. The same Russia–Ukraine war took 0.2
years of game time on weekly turns and 14.0 on yearly ones — an **81× spread** on a choice
made for pacing reasons.

The fix defines `span = days / 30`, with the month as the reference unit because every balance
constant in the engine was tuned there. Then each per-turn quantity is classified, and getting
the class wrong is silent — which is why they live in one module:

- **Flows multiply.** Revenue, upkeep, the unrest stability drag, contest decay. A year
  collects twelve months of tax and pays twelve of upkeep.
- **Rates compound.** Economy growth toward its industrial ceiling, manpower recovery,
  legitimacy drift — "close this fraction of the gap each turn". `1 - (1-r)^span`. A monthly
  0.06 is 0.52 over a year, not 0.72; and naive multiplication of any rate above 0.084 by
  twelve exceeds 1.0 and overshoots the ceiling it approaches.
- **Probabilities compound too**, for a different reason. A 0.25 monthly chance of a partisan
  rising is 0.97 over a year, not 3.0 — multiplying stops it being a probability.
- **War runs in rounds.** An advance is a flow, but resolving a year of it as one step would
  settle a campaign on a single dice roll. So a long jump is split into monthly rounds:
  `rounds = max(1, round(span))`, each advancing `span/rounds`. A year is twelve ordinary
  months of fighting inside one model call. Total advance is the same; the variance stays
  where it was tuned; and the front produces a narrative rather than one opaque leap.
- **Build times are days, not turns.** `unit_template.build_days` and
  `construction.days_remaining` (schema 9, the first migration carrying data). A carrier is
  300 days whether you watch it in weeks or years.
- **Validation caps scale too** — the last piece. `RuleContext` carries the turn's length and
  ECON-001 scales with it, *linearly* rather than compounding. That is deliberate:
  compounding is what the quantity does (twelve months of 50% treasury growth is 139×) and
  applying it to the cap produced a year-long bound of 13,801%, which is not a bound. A
  guardrail that switches itself off exactly when a decision covers the most time is worse
  than one slightly tight.

The two mechanisms agree by construction, since a rate compounded over twelve one-month
passes is exactly that rate compounded over one twelve-month span.

### Rewind

`rewind_to(turn)` puts the world back exactly. It is **inverse-diff**, not re-simulation:
every applied diff since the target turn is undone newest-first from its stored `old_json`,
and events after that turn are deleted outright. A "cancelled" event still sitting in the
table would poison a history models read to decide what happens.

Because it is inverse-diff, rewind is exact regardless of how the simulation works — the only
requirement is that **everything the engine changes flows through a diff**. That constraint is
why several systems that could have been direct SQL writes are not.

---

## Part IV — The world: geography and borders

### Geometry packs

Map geometry comes from Natural Earth (public domain), baked **offline** into a flat,
memory-mapped `.pack` file: magic bytes, a blake2b body digest verified on open, a JSON
index, and geometry blocks at several levels of detail. Deliberately *not* SQLite — the pack
is written once and thereafter only read, and SQLite's `immutable=1` is a promise the bytes
never change, which OneDrive can break, and its response to a broken promise is stale-page
reads rather than an honest error.

Three packs are used: `admin0-50m` (countries), `admin1-10m` (provinces, a heavy optional
bake), `marine-10m` (seas, straits and channels).

### Adjacency is derived, never hand-authored

Two regions border each other if their outlines **share at least two coordinates** — a single
shared vertex is a corner touch, not a border. Derived by indexing points to regions in one
pass rather than comparing every pair.

Sea-to-land cannot work that way: land and water come from separately digitised layers, so a
coastline appears twice with coordinates that are close and never equal. That side is matched
on a **0.25° grid** instead — about 25 km, fine enough that the North Sea does not reach
Bavaria and coarse enough to bridge two datasets that disagree to the metre.

Both are computed at **full detail**. A simplification that moves a coastline a few kilometres
is invisible on screen and decisive here: at the simplified level it erased Israel's entire
Mediterranean shore, along with three other countries' and 288 provinces' — and a country the
game thinks is landlocked silently loses its whole navy at world creation.

### Dynamic borders and province promotion

A region is not one shape. `region_geometry` maps many pack shapes to one region, because
Natural Earth mixes administrative levels between countries — Germany's 16 shapes are federal
states, Slovenia's 193 are municipalities — so one region per shape would give Yugoslavia 392
regions and Germany 16, and overrunning Yugoslavia would take twenty-four times longer for
reasons unrelated to either army.

A **territory file** (`scenarios/territory/<scenario>.json`) groups shapes into named regions
per nation. The powers it describes are **promoted on the spot**: a promoted Germany is not
one region called Germany, it is Berlin, Silesia, East Prussia, Bayern and the rest, and a
Franco-German war is fought province by province because there are provinces to fight over.

Promotion is **all or nothing** and decided by what the pack can draw. A machine without the
province bake plays the same scenario at country granularity, which is what every scenario did
before provinces existed. The optional download stays optional.

The rule for what gets promoted: **only the ground a campaign actually crosses.** Everything
else stays one region per country, which is what keeps a world-scale map affordable.

---

## Part V — The scenario system

### Presets, scenarios, registry

A **preset** is the description of a world — name, start date, `lore_text`, `simulation_rules`,
map source, playable powers. Lore and rules are separate fields on purpose: lore is grounding
a simulation should stay consistent with, rules are hard constraints phrased as prohibitions
and injected into every world-sim call. Merging them means treating flavour as inviolable or
a hard constraint as a suggestion.

A **scenario** is the content — who exists, how strong, who resents whom. `Scenario` is a
pure-data dataclass: powers, armour/air tables, fleets, colonies, ship classes, unit
templates, technologies, relations, treaties, history.

The **registry** joins them: preset id → the module holding its `SCENARIO`, imported lazily by
name. `preset_seed()` and `preset_politics()` are the whole decision, in one place. Before it
existed the join was four hardcoded `if preset.id == "gathering-storm"` branches across two
files, each of which would have needed a new arm per era.

The preset id, the territory file's name and the registry key are **the same string on
purpose** — three ids that can drift apart is three ways to ship a world whose era rules never
load. A test pins it.

### The seeder

`scenarios/seeder.py` holds everything era-agnostic: sea regions from the water layer, naval
and land adjacency, coast discovery, order of battle, fleet placement, colonial garrisons,
march reachability, minor-nation construction, and the 250-line assembly loop. It takes a
`Scenario` and returns a `WorldSeed`.

The line between data and machinery is stated explicitly: **anything a period historian would
argue about is data** (who exists, how many battleships, how many divisions garrisoned a
colony); **anything that would be the same argument in any century is machinery** (a fleet
needs a port, an army cannot march across an ocean, a capital is garrisoned first).

### Minors and colonies

Ground not held by a listed power becomes a **minor nation** with its own region, figures and
a single border guard — because a neutral with no army is ground any power can walk into
against no resistance, and the map stops meaning anything.

A minor the territory file names but the power list does not is **promoted just like a power**,
using the same machinery. A **colony** is the same geometry with different ownership: its
ground belongs to the colonial power and is garrisoned from its own hub, because the metropole
is across water and the march-reachability rule would otherwise leave it undefended.

---

## Part VI — The three eras

### 1936 — Gathering Storm

The political state of the world on 1 January 1936. Eleven promoted powers plus minors.
Germany rearming, France haunted by 1914, Britain hoping concession buys time, Italy bogged
down in Abyssinia, the Soviet Union industrialising and shooting its own officers. 73 directed
relations, 8 treaties, 8 engine-authored historical events (Versailles, Locarno, Manchuria,
the Anglo-German Naval Agreement).

The map is modern and the politics are not: Natural Earth has no Czechoslovakia, Yugoslavia or
USSR, so those are composed from successor regions. It is honest about what it is — 1936
borders approximated by 1990s ones. The territory file's **override table** is the hand-corrected
half, moving provinces the mechanical pass puts in the wrong country: East Prussia, the Polish
Corridor, Alsace-Lorraine, the Sudetenland, the Kresy.

Fleets are the real 1936 orders of battle. The Royal Navy's fifteen capital ships against
Germany's three is the point, not a bug.

### 1972 — Cold War

Twenty powers on 1 January 1972, and the era chosen because every mechanism built after
Modern Day is at its historical maximum on that date rather than at a fudge. Both
superpowers hold a full triad at rough parity and SALT I is signed in May, so mutual
assured destruction is a treaty obligation rather than a description. The People's Republic
took the China seat in October 1971 and tested in 1964, so the five permanent members of the
Security Council are exactly the five nuclear powers — which no earlier anchor can manage
without seating a veto in Taipei or handing one to Beijing years early.

Four things it is the first era to put under load:

- **Two blocs as real collective-defence commitments.** NATO and the Warsaw Pact are
  `defensive_pact` rows, so an attack on a member hands every other member a casus belli
  through the shipped Article 5 and nothing written for this scenario. A bloc is
  deliberately *not* also a convening organisation — that would count one attack twice.
- **A Security Council that cannot act against a superpower**, because both hold a veto.
  That falls out of the membership data rather than being coded.
- **Mutual deterrence.** 46 American warheads and 38 Soviet, both `triad`, both retaining
  more than thirty through a first strike. Britain is `triad` on four because a
  submarine-only deterrent is maximally survivable; China is `aircraft` on two, which is
  armed and not survivable, and is the whole reason Beijing spent 1969-72 looking for a
  third position.
- **A proxy war that is a campaign.** Both Vietnams and both Germanys are powers rather than
  minors, for the reason Ukraine is one in 2026.

**West Berlin is the sharpest thing on the map.** Natural Earth has one Berlin shape and the
city cannot be halved, so the scenario says so and gives the shape to the Western garrison —
ground the **United States** owns, garrisoned on itself through the `colonies` path, whose
only land neighbour is East Germany. A move on it is therefore an attack on the United
States, and Article 5 fires through the mechanism.

Ten countries are province-promoted: both Germanys, Poland, Czechoslovakia, both Koreas,
both Vietnams split at the 17th parallel, Cuba, and Egypt less the two Sinai governorates,
which Israel holds. The Soviet interior is deliberately country-level — the campaign of this
era crosses the inner German border, not the Volga.

One id rule the era paid for: **a power id must not equal a modern country code the power
does not hold.** South Vietnam as `svn` is Slovenia, and the leader, endowment and population
tables all key on the id with `m_` stripped, so Slovenia was handed Nguyen Van Thieu. The
Vietnams are `dvn` and `rvn`, and a parametrised test now guards every era against the
general case.

### Modern Day — 2026

Sixteen powers: the two that can project force anywhere, the regional powers, and four
flashpoint pairs. 90 directed relations, 10 standing commitments, 12 historical events
(Crimea, the 2022 invasion, AUKUS, the Taiwan question, semiconductor export controls).

Three judgement calls worth naming:

- **Ukraine is a power and the war is already on.** A 2026 that pretends the largest land war
  in Europe since 1945 is not happening is not the present, and Ukraine as a generic
  one-division minor would fall in a single contest.
- **`economy` is mobilisation capacity, not nominal GDP** — closer to purchasing power,
  because what the number does in this game is pay for divisions and fill shipyards.
- **The figures were calibrated against the budget, twice.** The first pass put all sixteen
  powers into structural deficit — North Korea bankrupt on turn one — because a modern hull
  costs 0.6/turn and a country-level power draws revenue from one region.

Eight countries are province-promoted: Russia/Ukraine, China/Taiwan, the two Koreas,
India/Pakistan. Taiwan's only neighbours are ocean, so the strait is amphibious-or-nothing.
Crimea and Sevastopol come out Russian with no override, because Natural Earth already files
them that way.

Nuclear weapons are **latency, not a unit**: nine of these powers have them, none has a warhead
in the order of battle, and the preset's hard constraints carry the taboo to the referee.

### The era gate

Each era supplies its own **unit templates** (1936 fields battleships; 1972 fields
carriers, guided-missile cruisers and nuclear boats; 2026 fields destroyers, carriers
and submarines and no battleships), its own **ship classes**, and its own **research
catalogue**.

It also supplies its **era**, as an enum on the scenario rather than a comparison
against the start date. Three tables are keyed by *nation* rather than by scenario and
so cannot live on the scenario object: who governs a country, what it digs out of the
ground, and how many people live in it. A boolean `start_date < 1950` was exactly enough
for two eras; a third would have been governed by the politicians of the 2020s and given
the Soviet Union the three-million default meant for an unnamed minor.

The gate is explicit in three places rather than one, because a catalogue split alone would
have left the real bug in place: ordering out of era is refused *by name*; an effect that
reaches delivery naming a unit kind this world does not build is **reported** rather than
silently dropped; and a parametrised test walks every registered era asserting each effect
names a lever shipped code reads and a kind that era fields.

That test is load-bearing. Before it, 1936's `welding` and `radar` each carried a `battleship`
effect that vanished silently in Modern Day — so a player could complete "Radio direction
finding", be told it succeeded, and receive an air-only technology.

**One rule binds every era: an era may rename its ships but not its amphibious lift.**
`engine/naval.py` looks up `kind = 'transport'` by name, so an era that renames it keeps every
other naval mechanic and silently loses the ability to land an army anywhere.

---

## Part VII — Warfare

### Land: pressure, not a single roll

Ground does not change hands on one roll. A contest accumulates **pressure**; the region falls
when it crosses the threshold. The day's fighting moves a front, so overrunning a country is a
campaign whose length is set by how hard its army is to shift.

Advance is `BASELINE_ADVANCE × advantage / defender_resilience × swing`, capped per round so
any single region takes at least two rounds however lopsided the fight. The `swing` is the
same roll that decided the engagement — a narrow win inches forward, a rout covers ground, a
bad enough day gives it back. No second draw is taken, which keeps the advance exactly as
reproducible as the outcome that produced it.

That smoothness was learned the hard way: gating advance on *winning* produced a cliff rather
than a curve, where every force ratio below ~0.6 stalled for eighteen turns and everything
above it fell in two.

Strength counts units in the region plus units in bordering regions at a discount — an
invader's army is by definition *next to* the region it is invading — plus a tenth of national
military strength, so an undefended region is not an automatic loss.

Also modelled: **withdrawal** (a beaten defender falls back to an adjacent friendly region),
**encirclement** (forces with nowhere to withdraw to are taken with the province), **march to
the front** (units move toward standing objectives), and **contest decay** (a front nobody
stood on this turn is let go).

### Naval

Water is a region like any other — which is most of why a navy cost so little new machinery. A
fleet is units standing in a sea; sea control is the same `controller_nation_id` an occupation
writes; a fleet action is the same seeded contest as a land battle.

At sea **only ships count and there is no national reserve**, or a landlocked power could
contest the North Sea. Carriers count at full weight from a neighbouring sea because their
aircraft are already there.

**Amphibious invasion** is the interesting piece. Land formations never go to sea — that
invariant is what keeps rule UNIT-002 free of exceptions. Instead an amphibious assault is an
ordinary contest whose *reachability* is unusual. A power may attack ground it does not border
when three things hold: there is water touching both, that water is not controlled *or
militarily denied* by an enemy (a fleet in being closes a sea even unclaimed), and there are
transports on it or in a port on it.

Ship construction runs through the same queue as everything else, with build times steep
enough that a navy cannot be conjured in a turn.

### Air — honestly, it is data

`domain = 'air'` reaches exactly three places in the engine: the upkeep table (0.35/turn),
`BUILD_DAYS`, and a schema CHECK. `_strength()` splits the world into sea and not-sea, so **an
air wing is summed into a land battle exactly like an infantry division** — no range, no
strike, no interception, no air superiority.

Consequently three technologies across two eras — 1936's `radar`, Modern Day's
`integrated_air_defence` and `hypersonic_strike` — describe a layer the engine cannot express.
Each lands on a lever shipped code reads (`base_strength`), which is the bar the catalogue
sets, but the names promise something absent. A design for a real air/missile layer exists and
is not built.

### Nuclear weapons — a deterrent, not a button

Who is armed is state: an `arsenal` row per nuclear power, holding a warhead count and a
delivery tier (`aircraft` / `missile` / `triad`), both engine-only. How much of an arsenal
survives a first strike is *derived* — the tier plus the submarines actually at sea — so
sinking boats weakens a deterrent.

Three pieces, and only the first involves a weapon going off:

- **Deterrence.** A survivable arsenal is a term in `compute_pressure` against the *homeland*
  it guards (keyed on `home_nation_id`, so it never follows a nuclear power onto ground it
  occupies), capped at 0.18. On the real 2026 map a nuclear Ukraine loses three provinces in
  ten turns where the shipped one loses nine: hard to invade, not impossible. Exactly zero on
  a map with no arsenals, which is why 1936 is untouched.
- **A launch is a gambit kind.** Proposed, refused before the roll if the world does not
  permit it, staked, and decided by a seeded interception draw. **Alone among the kinds it
  takes no effect verbs**: a warhead craters the province, burns every formation standing in
  it, spreads fallout to three neighbours, shakes the struck government, disgraces the
  launcher and collapses every other state's regard for it — all capped, all engine-applied.
  That asymmetry is what makes it safe for a launch to reach columns a gambit may not write.
  It still takes no ground, raises no formation and starts no war.
- **The reply is automatic.** If the target retains a survivable arsenal the engine fires back
  in the same turn: no model call, proportional, one exchange deep, and it fires whether or not
  the first strike got through. This is the rule the whole design turns on — a first strike is
  only a winning move if the attacker gets to find out whether the defender would have dared.

The briefing states the vocabulary, every cap and the retaliation rule, then names the specific
rivals who could answer and with how many warheads. It never says that use is unthinkable; a
staff that can see it will be fired on regardless can reach that itself.

### Peace as province transfers

A war can be *settled*. A peace declaration may carry `cessions`: the ground that actually
changes hands. A grinding war where one side has occupied the other's provinces ends with
those provinces ceded, not with the border snapping back. Owner *and* controller transfer, and
recovery is told from annexation.

The engine nudges toward this — when a war has reached a settleable state, the referee's
briefing says so. Before that nudge existed, a real-model run had Italy hold Ethiopia's capital
for three turns with nobody proposing peace.

---

## Part VIII — The peaceful game

### Budget

Every turn: revenue from economy and developed ground, upkeep per unit by domain (land 0.18,
air 0.35, sea 0.6), manpower recovering toward what the population sustains, economy climbing
toward what the industrial base supports, legitimacy drifting toward stability.

The economy target is the compounding heart of the peaceful game. A steelworks is not a one-off
bump — it raises the ceiling the economy then climbs toward for years, so a builder watches
their country grow every quarter and a hoarder watches it sit still.

A government that cannot pay its establishments goes into **arrears**, which costs standing.
The per-turn budget (revenue, upkeep, net) is shown in the briefings, with a warning naming the
two levers, because a player could otherwise not tell they were bleeding until the treasury
drained.

### Industry, construction and public works

`nation.industry` sets how many simultaneous projects a nation can run. Everything shares one
queue — formations, ships, public works, research — because what a country does with the next
seven years is one decision rather than three. Paid for when ordered and delivered when
finished, so a half-built battleship is money already spent, which is what makes a building
programme a commitment rather than a shopping list.

Public works: housing, rail, health, schools, workshops, steelworks, shipyards, land reform.
Some are gambles whose odds scale with legitimacy.

### Research

A programme ends in a country knowing how to do something it could not before. The bar for
being in a catalogue at all: **every effect must move something shipped code already reads** —
`unit_template.build_days`, `unit_template.base_strength`, `nation.industry`, `nation.economy`.
A technology unlocking a capability nothing consults is a line in a menu, and a game full of
those is one where research is a tax you pay to make numbers go up.

Programmes may be **secret**. What actually hides one is the visibility of the events it
writes, which the engine already enforces everywhere — not a bespoke hiding mechanism.

This is where a country outgrows its history: nothing in the engine references what actually
happened, so a power that spends fifteen turns on shipbuilding launches better ships faster
than it historically could.

### Unrest, garrisons and partisans

Holding what is not yours costs. Foreign core — ground held but not your `home_nation_id` —
generates unrest that **drags on stability** every turn, eased by **garrisoning** troops on it
and by a steady hand at home. Badly-held ground can **rise in partisan revolt** and revert to
its home nation.

That turns unrest from a stat cost into a *map* cost: annexing a continent is possible, no
longer free, and no longer even guaranteed to stay yours. The briefings list each occupied
province with its unrest and garrison state, so the risk is shown to whoever decides rather
than discovered when stability craters.

One case is deliberately deferred: a **wholly-conquered** home nation spawns no partisans,
because only living home nations rise. Occupation of a fully annexed nation is currently
partisan-proof, which is arguably too safe.

---

## Part IX — The AI architecture

### Roles

Seven, each with its own view builder, response schema and model pin:

| role | what it does | writes |
|---|---|---|
| `world_sim` | the referee: runs the whole world for a turn | six nation stats, relations, commitments, events |
| `tracked_agent` | one nation acting on its own account | its own goals, its relations, events |
| `diplomacy` | negotiation between powers | relations, commitments, events |
| `advisor` | answers the player's questions | **nothing** |
| `parser` | turns prose orders into validated actions | nothing directly |
| `freeform` | answers orders no mechanic covers | three columns, via the engine |
| `gambit` | proposes one adjudicated *attempt* per turn | four columns, via the engine |
| `arbiter` | adjudication | — |

`gambit` and `freeform` are permissions-and-routing identities rather than proposer roles:
neither ever appears in `diff_batch.proposer_role`, because in both the model proposes and the
*engine* writes. That is what let both channels land without widening a closed CHECK enum.

### Focus Mode

Running a per-nation agent for 242 nations is impossible. **`FOCUS_CAP = 5`** nations are
"tracked" at any time — enforced by a database trigger as well as in code. Candidates are
ranked each turn by how much is happening to them: at war, bordering the player, recently
active, high stakes. The rest are played in bulk by the world simulator.

The **master AI** plays the treasury of every power no human and no tracked agent is driving,
so the whole world industrialises rather than freezing while one builder walks away from it. A
minor's steelworks is charged and queued through exactly the same `order_project` the player
uses.

### Model routing

Three CLI providers — Claude, Codex, Antigravity — driven as subprocesses, sandboxed, with
per-call timeouts. Roles are pinned to providers by default (`world_sim` → Antigravity,
`tracked_agent` → Codex, `advisor`/`diplomacy`/`freeform` → Claude) and a **complexity score**
per jump picks a band within a role: how much time is being advanced, how many actions, how
much is at war. A quiet month gets a cheap model; a jump with three wars gets an expensive one.

Every call is written to `routing_log`: role, provider, requested model, **served model**,
audit verdict (`MATCH` / `MISMATCH` / `UNVERIFIED`), complexity, band, duration, outcome.

**Model attestation** matters and is not uniform. Claude and Codex report which model actually
served a call and the engine verifies it against what was requested. Antigravity does not
attest at all, so its calls are logged `UNVERIFIED` — and in Engine-vs-Engine mode a bloc whose
provider cannot prove which model answered can be refused outright.

Cost control is structural rather than a budget counter: the Focus cap bounds calls per turn,
routing bands bound the model tier, `atlas probe` spends one small real call per provider to
check health, and the mock provider runs the entire game with no calls at all. A jump costs
one world-sim call plus up to five tracked calls.

### The freeform channel

Every order is parsed into a closed list of action types, and anything fitting none becomes
`other` — for which there was once no handler at all. So "authorise the citizens to fight the
stray cats" did not fail and did not happen: it *evaporated*, which is the worst of the three
possible answers because the player cannot tell it from a bug.

Freeform is the third answer. A model narrates what the country made of the order, and the
country's feelings may move a little. **No numeric field exists anywhere in its schema**: a
nudge is a field, a direction, and a coarse magnitude from a closed enum (`slight` 1.5,
`moderate` 4.0). Four structural bounds: ≤3 nudges per order, ≤2 orders per turn, a per-field
per-turn ledger cap of 6.0, and three reachable columns.

**Refusal is the point.** A channel that always says yes teaches the player nothing has
consequences. An order that is really an attempt at a hard mechanic ("annex Belgium by decree")
is refused with the real order named.

### Discoverability — the pattern that keeps paying

Repeatedly the same lesson: **a guardrail the model is never told about is a guardrail that
costs a whole batch.** Real-model runs kept losing entire proposals to bounds nobody had
disclosed. Each time, stating the rule in the briefing fixed it:

- Relation write rules (range, the 25-point swing cap, visibility scoping, the exact
  `treaty_status` set) — rejections went from 28 across four rules to zero.
- The economic caps, generated from the rule's own constants so the briefing cannot drift from
  what is enforced, and stated **for the actual turn length**.
- A citable-of-last-resort: the genesis event, so a model with no obvious cause for a routine
  change has a true general one instead of a fabricated specific one.
- Objectives that the map has overtaken now **lapse** and are reported, rather than being shown
  back to the staff, restated, and refused every turn.
- A build order that cannot be matched is answered with what was asked and the closest thing
  this era builds, instead of "you were not told which programme to begin" — which was a lie,
  since the staff had been told.

---

## Part X — The guarantees

These are the properties the project defends, and most of the design decisions above exist to
maintain one of them.

1. **Ground truth is the database.** No model output is authoritative. Every claim becomes a
   validated diff or it does not exist.
2. **Determinism.** Seeded, derived randomness with keys identifying decision points. Same
   inputs, same world.
3. **Exact rewind.** Inverse-diff, not replay. Requires that everything the engine changes
   flows through a diff.
4. **Additive-only migrations, enforced.** `CREATE TABLE`, `CREATE INDEX`, `ALTER TABLE ADD
   COLUMN` and backfills are allowed; `DROP`, `DELETE`, `RENAME` and `ADD COLUMN NOT NULL`
   without a default are *refused by the migration runner itself*. One transaction per
   version. Forward only — opening a world from a newer ATLAS is an error, not an attempted
   downgrade. Currently at **schema version 10**.
5. **Closed CHECK enums** on `event.kind`, `diff_batch.proposer_role`, `military_objective.kind`
   and others. These cannot be widened additively, so a change needing a new one is
   save-invalidating and must be a deliberate decision.
6. **Propose-only.** Engine-only columns, per-role allowlists, evidence vocabularies instead
   of numbers.
7. **Fingerprint guards.** A scenario's `WorldSeed` is hashed whole *and per section*
   (nations, regions, adjacency, cities, unit templates, units), so a refactor that should
   change nothing can be proved to have changed nothing — and a change that *should* be
   confined to one part can be shown to be. The whole-seed digest says "something moved"; the
   sections say what.
8. **Fail closed.** A validation rule that raises is a violation.
9. **No network at play time.** Geometry is baked offline; the game never fetches anything.

---

## Part XI — Where it stands

**1096 tests**, one deselected (`live`, which spends real quota). `ruff` clean. Schema version
10. Two playable eras sharing every line of machinery. Twenty-five logged playthroughs.

**Proven under real models:** a tracked belligerent prosecuting its own war by setting and
advancing objectives; garrisoning conquered ground; the world simulator suing for peace with
sane territorial terms; a colonial power fighting from its colonies; the peace →
occupation → unrest spine end to end; a second era with its own map granularity, fleet and
research catalogue; the era gate (four nations, two modern programmes, one delivered); a
defender counter-attacking and retaking occupied ground; a **partisan revolt**, arising
naturally from an over-extended occupier rather than a scripted setup; and `build_days`
counting down exactly thirty days a monthly turn.

**Proven in mock only:** most of the pacing model. Every live run so far has used monthly
turns — the one span where all of that arithmetic is the identity — so rounds, the compounded
rates and the newly scaled validation caps have never been observed against a real model. A
seasonal or yearly run would test all three and costs the same as a monthly one, since a
yearly turn is still a single model call.

**Not built, designed:** an air/missile layer — air superiority contested over the same
ground, range read off `move_range`, strike missions, and interception as a real term. It is
the last of three mechanics designed together; the other two (the gambit channel and the
nuclear layer) are built, and both landed close to as the design note predicted, which is the
argument for building the frame before the content.

**Known gaps:** air is data, and the nuclear layer's interception term reads it as it honestly
is (air-domain unit strength plus `integrated_air_defence`) pending a real air layer; the
nuclear layer has never been in front of a real model, so a *scripted* staff declining to
launch is evidence the MAD disclosure is legible rather than that a model is persuaded; a
wholly-conquered nation spawns no partisans; a Modern Day save written before schema 10
migrates to a world with no arsenals; and the Antigravity CLI's eligibility check flakes on a
transient network fetch that has twice ended or delayed a real-model run and cleared on a
re-probe both times.

---

## A note on how this codebase is written

Two conventions a reader will notice immediately.

**Comments explain *why*, and specifically why the obvious alternative was rejected.** Most
non-trivial constants carry the story of the run that set them: the advance baseline records
that 0.65 made France take thirty turns to overrun Germany; the loss intensity records the
feedback loop that made casualties compound; the coast-matching cell size records that Natural
Earth's two layers do not agree to the metre. The intent is that someone changing a number
finds out what it cost to learn it.

**Every mechanic has a playtest behind it.** The engine changelog logs twenty-one
playthroughs and fifty-four numbered decisions, each recording what was found, what was
changed, and what was deliberately *not* changed. Several entries are records of a fix that
was considered and rejected — the structural deficit that turned out to be escapable and
therefore wanted a gauge rather than a rebalance, or the compounding cap that would have made
a guardrail vacuous. The log is as much a record of restraint as of work.
