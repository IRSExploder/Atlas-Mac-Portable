# ATLAS — NPC strategic doctrines: scoping and design proposal

**For:** Alex. **Prepared:** 2026-08-15, at `72ff619` on `master`; **revised the same day**
after Alex decided FORK A.

**Status.** Steps 1 and 2 are the scoping and the proposal, as first written, and the forks
in them are kept as they were asked and answered. **Step 3 is the decided design, and all
three of its axes are now BUILT**:

* **mobilisation** — the foundation and the first axis, `a9f62bc`;
* **nuclear posture** — `3f09544`, with the arsenal upkeep charge it needed, retuned in
  `dbdeac3`;
* **alignment** — `bdf6a67`, with the unanswered-call rule it needed, plus the pact upkeep
  in `971583d`.

The axis catalogue was opened at `b82bf21` (schema 38) so the four axes still scoped in §1.1
— forward defence, carrier versus surface fleet, autarky versus trade, decolonisation versus
integration — are a code entry rather than a migration each.

The premise, restated so the rest can be checked against it: **directives are the player's
workspace preferences and only reorder advice; doctrines are engine-owned world state that
changes what a nation actually does.** Two nations with identical stats should fight, spend
and prioritise differently. Doctrines are persistent, seeded per nation, slow to change, and
costly to switch.

---

# Step 1 — Scoping

## 1.1 Where each axis would hook

The table first, then the detail. "Clean" means every number the stance would move is already
a named constant or a single function with one or two call sites.

| axis | hook | verdict |
|---|---|---|
| mass mobilisation vs professional | `wareffort.manpower_ceiling`, `budget._MANPOWER_RECOVERY`, `budget._UPKEEP`, `unit_template.base_strength` | **clean** |
| nuclear retaliation vs minimum deterrence | `nuclear.deterrence`, `nuclear.survivability`, `nuclear.second_strike`, `WARHEADS_BY_STAKE` | **clean** |
| autarky vs trade dependence | `trade.demand_of`, `trade.shortfall_of`, `budget._ECONOMY_GROWTH_RATE` | **clean** |
| forward defence vs defence in depth | `sim.resolve.defender_resilience`, `compute_pressure`, `IDLE_DECAY` | **one new query** |
| carrier vs surface fleet | `sim.resolve._CARRIER_REACH`, `naval.sea_lift`, `air.superiority_term`, `unit_template` costs | **needs a ship-role map** |
| decolonisation vs imperial integration | `decolonisation.GARRISON_RELIEF`, `MOVEMENT_THRESHOLD`, `INDEPENDENCE_THRESHOLD`, `unrest_load` | **half clean, half a new verb** |
| collective security vs neutrality | `resolution._invoke_defensive_pacts`, `treaties` breach detection | **needs new machinery** |

### Mass mobilisation vs professional forces — clean

Four levers, all of them already load-bearing:

* `wareffort.manpower_ceiling(population)` returns `population * 2.0`, floored at 10. Its own
  docstring says it is kept because it is "the honest measure of what a country *could* put in
  the field, which the reports and any later mobilisation layer will want" — the code has been
  waiting for this.
* `budget._MANPOWER_RECOVERY` is 0.04 per month, compounded toward the ceiling.
* `budget._UPKEEP` is `{"land": 0.18, "air": 0.35, "sea": 0.6}` per formation per month.
* `unit_template.base_strength` is stamped onto every unit the yards deliver, and
  `engine/research.py` already moves it — so a doctrine moving it is doing a thing the engine
  does today rather than inventing a mechanism.

Mass mobilisation raises the ceiling and recovery and lowers per-formation strength; a
professional force inverts both and costs more per head. **This is the axis with no new
machinery at all.**

### Nuclear retaliation vs minimum deterrence — clean

`nuclear.deterrence(conn, defender, region_id)` is one function with one call site — a
subtraction inside `sim.resolve.compute_pressure`, imported inside the function on purpose so
that content layers stay on top of the adjudicator. `survivability` reads `DELIVERY_TIERS` and
the boat count. `second_strike` is engine-authored and automatic; `WARHEADS_BY_STAKE` maps a
stake to a warhead count, capped by `MAX_WARHEADS_PER_LAUNCH`.

Minimum deterrence buys the same *per-region* deterrent from a smaller arsenal and spends less;
massive retaliation buys a larger second strike and pays for it. The axis is exactly zero on any
map with no arsenal rows, which is every 1936 world — the same "absent is the answer for almost
every nation" property the `arsenal` table already has.

### Autarky vs trade dependence — clean

`trade.demand_of` is `industry * _DEMAND_PER_INDUSTRY` (0.11), floored. `trade.shortfall_of`
takes the **worst** of the three goods and floors it at `_STARVED_CAPACITY` (0.35), and that
result scales `production.industrial_capacity` — an existing door, explicitly described as such.
`budget._ECONOMY_GROWTH_RATE` (0.06) is where the price goes.

Autarky lowers demand and raises the starved floor — a blockade hurts less — and pays for it in
economic growth. Trade dependence is the reverse. Both halves already exist as constants.

### Forward defence vs defence in depth — one new query

`sim.resolve.defender_resilience(conn, contest)` is the right hook and it is a single function:
terrain modifier times a morale term, floored at 1.0, and it decides *how much ground* an
advance takes rather than who wins the day. `compute_pressure` is where an odds term would go,
and it already carries five layered terms (`_SUPPLY_SWING` 0.12, nuclear, air, support,
difficulty) added by exactly the pattern a doctrine term would use.

What is missing is the notion of **depth**: how far this region is from the frontier. The
ingredients exist — `region.home_nation_id` distinguishes core from held ground, and
`logistics.supply_multiplier` already walks the adjacency graph from a nation's own core — but
"is this the border or the interior" is not a question anything asks today. One derived query,
not a new subsystem.

### Carrier vs surface fleet — needs a ship-role map

The single number this axis is *about* already exists and is documented as being about exactly
this argument: `sim.resolve._CARRIER_REACH` is 1.0, against `_ADJACENT_WEIGHT` 0.6 for anything
else nearby, and its comment says giving carrier aircraft reach "is the one place the model says
something specific about the argument the 1930s naval staffs were having." `naval.sea_lift`,
`naval.crossable` and `air.superiority_term` are the other three doors.

**The wrinkle is that ship kinds are era-specific.** Cold War ships are `carrier`, `cruiser`,
`submarine`, `transport`; 1936 has a different set. A doctrine that names "carrier" is naming a
string that does not exist in every era. Either the axis is expressed against a *role* (a new
per-era mapping from `unit_template.kind` to a role) or it is expressed only as modifiers on
build cost by domain plus `_CARRIER_REACH`, which is weaker but needs nothing new. **This is a
real fork, called out below.**

### Decolonisation vs imperial integration — half clean, half a new verb

The decolonisation half is clean. `decolonisation` is entirely constants and one assessor:
`GARRISON_RELIEF` is 0.06 per point of garrison, `MOVEMENT_THRESHOLD` is 25.0,
`INDEPENDENCE_THRESHOLD` is 70.0, `UNREST_PER_MOVEMENT` is 1.2, and `decolonise()` /
`_settle_the_inevitable()` are the two endings. A doctrine of managed withdrawal would lower the
unrest load and let a holder leave early on better terms.

**Integration is a genuinely new verb.** Making a dependency into core territory means writing
`region.home_nation_id`, which is engine-only and which nothing writes today. That is the
mechanic — an empire that spends years and stability turning a colony into part of the
metropole, and thereby stops paying the colonial unrest but starts paying the foreign-core
unrest that `unrest.held_foreign_provinces` already charges. Worth building; not free.

### Collective security vs neutrality — needs new machinery, and it is the deliberate kind

`resolution._invoke_defensive_pacts` is emphatic that Article 5 is **prompted, not automatic**:
attacking a pact member writes an engine-authored `casus_belli` for each ally (which exempts the
holder from the unprovoked-war legitimacy cost) and collapses the allies' regard for the
aggressor. *The engine does not declare anybody's war*, because "a hard auto-join would make one
declaration drag five powers into a war nobody chose".

That is the right design and it means **there is currently no mechanical cost to an ally that
does not come**. A collective-security doctrine has nothing to bite on until there is one. So
this axis needs a new rule — failing to honour a pact you hold costs legitimacy and the other
parties' regard — before the doctrine has any mechanical half at all. Its prompt half would work
immediately; its mechanical half is a new rule with its own design question about how long an
ally has to decide. **The most expensive of the seven, and the one I would build last.**

## 1.2 How NPCs are driven today

Three distinct channels, and a doctrine touches all three differently.

**Tracked nations get a model call.** `engine/focus.py` caps the roster at five
(`FOCUS_CAP`), ranks deterministically with no model call, and writes `is_tracked` **as diffs**
because it is fingerprinted state — its docstring records that writing it with direct SQL meant
a power promoted on turn 5 stayed promoted through a rewind, "the gravest class this project
has, found twice before in `leader` and `commitment`". Any doctrine table inherits that lesson
directly.

The call is built by `views.build_tracked_agent_view`, which already lays out
`## Your government` (the leader's agenda and traits, from `leader`) and `## Your standing
objectives` (from `nation.standing_goals_json`). **A doctrine line slots in between them with no
structural change** — and the view's own comment explains why the government's aim sits above
the objectives: "the same country pursues 'secure the frontier' very differently under a
dealmaker and under a hardliner." A doctrine is a third lens of the same kind.

**Everyone else is resolved in bulk** by the world simulator plus engine code. An untracked
nation's economy, manpower, production, colonies, war exhaustion, air war and supply are all
computed by `budget`, `production`, `decolonisation`, `wareffort`, `air` and `logistics` with no
model involved at all.

**This is the strongest single argument for doctrines being state rather than prompt text.** A
prompt-only doctrine reaches five nations out of a hundred and forty-seven. A doctrine that
moves `manpower_ceiling` reaches all of them.

**The precedent for a tracked agent proposing intent the engine prices** is `military_objective`
(`push` / `hold` / `withdraw_from`) and `war_aim`: real tables, ENGINE-only, ids derived from
what identifies them so they survive a rewind and replay, and the model names the ground while
the engine decides everything that follows. A doctrine shift is the same shape.

**One caveat worth having in front of you.** Per the project record, tracked-agent calls came
back empty under `agy` and are pinned to `codex` for that reason; under the **mock** provider
tracked nations still issue nothing. So in any test, screenshot or offline run, the *prompt*
half of a doctrine is invisible. **The mechanical half has to stand alone and be independently
testable**, or the feature will look built and do nothing in exactly the conditions the suite
runs under.

## 1.3 The seam with directives

Clean, and the separation is already written down. `engine/directives.py` says of itself: "Not
doctrines for anybody else. What a *tracked nation* is trying to do is a different,
engine-owned feature."

| | directives (`df0a361`) | doctrines (proposed) |
|---|---|---|
| storage | workspace sidecar beside the save | a fingerprinted table |
| schema | none | additive `CREATE TABLE` |
| rewind | untouched — it is a note to yourself | undone, like every other state |
| who has one | the player only | every **eligible** nation (fork below) — a nation with no row for an axis has no stance on it, and that is the common case |
| what it does | reorders advice; never acts | changes the arithmetic |
| who writes it | the player, freely, instantly | model proposes, engine prices |
| test that holds the line | `test_nothing_here_can_reach_a_diff` | the mirror: doctrine effects must be *only* diffs |

**One shared-vocabulary hazard, and it is worth fixing before anything is built.** Three of the
six directives read like doctrine axes: `naval_supremacy` against carrier-vs-surface,
`industrialise` against autarky, `avoid_continental_war` against forward-vs-depth. A player
reading "your standing policy is naval supremacy" beside "your doctrine is a surface fleet"
will reasonably assume one causes the other, and it does not.

Recommendation: **reserve the word *policy* for directives and the word *doctrine* for
doctrines, everywhere in the UI, the briefings and the prose** — and never let a doctrine reuse
a directive's id. The advisor block added by `views._policy_block` is titled "What this
government is trying to do"; a doctrine block should be titled "How this country fights",
"How it arms", and so on — a statement about method, not about aims.

## 1.4 Determinism, schema and rewind

Doctrines are fingerprinted state, so all of the following is mandatory rather than optional:

* **An additive `CREATE TABLE`**, which passes the additive-only guard in `state/migrations.py`
  without needing `invalidates_saves`. A world written before it simply has no rows, and a
  nation with no row behaves exactly as every nation does today — the `arsenal` property, and
  the thing that keeps the three shipped eras from moving except where they are seeded.
* **A composite primary key `(world_id, nation_id, axis)`.** `arsenal` records the lesson in its
  own comment: keyed by nation alone, "two worlds in one file could not both give the United
  States an arsenal", and the fresh and migrated schemas disagreed about it.
* **Registration in `_FINGERPRINT_TABLES`** (`state/bootstrap.py`) and in `PRIMARY_KEY`
  (`state/apply.py`). **This moves every built-world digest by the width of one table name**,
  which is the documented, expected consequence — `war_aim` and four other tables carry the same
  note. It also moves the cassettes. **Do it once, in the foundation commit, not once per axis.**
* **Every column ENGINE-only** in `state/permissions.ENGINE_ONLY`. A model that could write its
  own doctrine would be granting itself the bonus, which is the `arsenal` argument verbatim.
* **Written as diffs, never as direct SQL** — `focus.py`'s scar.
* **Seeded through `apply_politics`**, the path `arsenal`, the treaties and the organisations
  already take, from a per-scenario dict on the scenario dataclass.
* **A `doctrines` manifest section** in `testing/audit.py`, so the seeding is checked against a
  separately written record of the period rather than against the scenario module that wrote it.
  The auditor's own docstring is explicit that checking the scenario against itself "proves only
  that `apply_politics` can copy a tuple".
* **Every per-turn quantity through `engine/pacing.py`.** A doctrine that drifts anything per
  turn is a rate or a flow and must say which, or it will break the pacing-invariance gate built
  in item 1 of the last batch.

---

# Step 2 — Design proposal

## FORK A — NPC-only, or universal?

**Recommendation: universal.** The player has a doctrine too, and it is a real commitment with a
price, entirely distinct from their advice-only directives.

Three reasons, in order of weight:

1. **The hooks do not know who is playing.** `manpower_ceiling`, `defender_resilience`,
   `deterrence`, `shortfall_of` are engine functions with no concept of a player. NPC-only means
   a "unless this is the player" branch in every one of them — which is precisely the shape that
   grew `against.py`'s private table of exceptions, deleted last week for being a second
   catalogue. Building seven of them deliberately would be a mistake we have already made once.
2. **It is a better decision for the player than directives are.** A directive is free and
   instant; a doctrine costs money, takes months, and dips your effectiveness while it changes.
   That is a genuinely different kind of choice, and the two are complementary rather than
   redundant: *what I want* against *how my country is built to get it*.
3. **An asymmetry would be visible and would read as cheating.** A player who notices that
   Germany gets a mobilisation bonus they cannot have is a player who has stopped trusting the
   simulation.

**What you give up by choosing universal:** UI work (a doctrine screen, not just a briefing
line), and the awkward moment where a player wants to switch doctrine mid-war and cannot afford
to. I think the second is a feature.

## FORK B — Independent axes, or one doctrine per domain?

**Recommendation: independent axes, one row per `(nation, axis)`, and only where the axis
applies.**

A landlocked power has no naval doctrine row, exactly as a non-nuclear power has no `arsenal`
row, and a nation with no colonies has no imperial row. **Absence is the default and absence
means "behave exactly as the engine does today."** That is what keeps the blast radius small: an
axis that has not been built yet, or does not apply, is not a special case — it is simply no
rows.

The alternative — one doctrine per domain, chosen from a list of named packages — is more
flavourful and much harder to balance, because every package is a bundle of effects that has to
be priced as a bundle. Independent axes can each be balanced against their own downside.

Proposed shape:

```
CREATE TABLE doctrine (
    world_id       TEXT NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    nation_id      TEXT NOT NULL REFERENCES nation(id) ON DELETE CASCADE,
    axis           TEXT NOT NULL CHECK (axis IN (...)),   -- closed enum, widening invalidates
    stance         TEXT NOT NULL,                          -- one of the axis's two poles
    adopted_turn   INTEGER NOT NULL DEFAULT 0,
    adopted_on     TEXT NOT NULL DEFAULT '',               -- world date, per item 1's rule
    -- A shift in progress. Empty when settled; while set, NEITHER stance's bonus applies.
    shifting_to    TEXT NOT NULL DEFAULT '',
    shift_ends_on  TEXT NOT NULL DEFAULT '',               -- world date, not a turn count
    PRIMARY KEY (world_id, nation_id, axis)
);
```

`adopted_on` and `shift_ends_on` are **world dates, not turn counts** — the rule settled in item
1 of the last batch. A six-month doctrine shift is six months of elapsed history, not six
player decisions.

## FORK C — How big may an effect be?

**Recommendation: adopt a stated rule and hold it with a test.** Every stance must move **at
least two numbers in opposite directions**, and **no single number by more than 15%**. No stance
may be strictly better than its opposite in every situation.

Without a rule of this shape the axes will drift into a tech tree, where one pole is simply the
good one and picking it is not a decision. The rule is testable: a table of (axis, stance,
number, direction) that a test walks, asserting each stance has at least one `+` and one `−`.

### The seven axes, with their downsides

| axis | stance | gains | pays |
|---|---|---|---|
| mobilisation | mass | higher `manpower_ceiling`, faster recovery | lower `base_strength` per formation, higher total upkeep |
| | professional | higher `base_strength`, lower upkeep per head | much lower ceiling — losses are not replaceable |
| nuclear | massive retaliation | larger second strike, higher `deterrence` term | arsenal upkeep, and worse standing after any launch |
| | minimum deterrence | most of the deterrent for a fraction of the arsenal | a first strike can disarm you if your tier has no sea leg |
| economy | autarky | higher `_STARVED_CAPACITY` floor, lower `demand_of` — blockade hurts less | slower `_ECONOMY_GROWTH_RATE` |
| | trade dependence | faster growth, more industry | a cut route is a production collapse |
| defence | forward | higher `defender_resilience` on your own border provinces | a broken front is a rout: less resilience behind it |
| | depth | ground trades for time — advances slow the further they go | you lose provinces you would have held |
| navy | carrier | `_CARRIER_REACH` and air projection over water | hulls cost more; a lost carrier is a lost campaign |
| | surface | cheaper hulls, better sea denial (`naval.crossable`) | no reach beyond your own coasts |
| empire | integration | colonial unrest falls as ground becomes core | foreign-core unrest rises; costs years and stability |
| | managed withdrawal | lower `UNREST_PER_MOVEMENT`, better exit terms | you lose the ground and the revenue from it |
| alliances | collective security | allies' regard, and honouring costs you nothing | **needs the new rule**: not honouring costs legitimacy |
| | neutrality | no pact obligations, trade with everybody | no `casus_belli` shelter, no ally comes for you |

The last row is the one that does not work yet. See §1.1.

## FORK D — Transition: how slow, and how expensive?

**Recommendation: the effectiveness dip is the primary cost; treasury second; stability only
where it is politically charged.**

* **Six months of world time** to shift an axis, during which **neither** stance's modifier
  applies. That is the whole cost mechanism and it is self-balancing: a nation that keeps
  changing its mind never gets a bonus at all.
* **A treasury charge proportional to what the axis governs** — the size of the army for
  mobilisation, the fleet for naval, the arsenal for nuclear. A doctrine shift for a small power
  is cheap and for a great power is a budget.
* **A stability charge only on the two political axes** (empire, alliances). Reorganising your
  army is expensive; announcing that you are abandoning your allies is a domestic event.
* **One axis at a time.** A nation already shifting cannot start another. This is the
  `Capability.available` predicate doing its job, and it needs the optional target that landed
  in `8405707`.

**FORK D-1, unresolved:** may a nation shift doctrine *while at war*? Arguments both ways — it
is exactly when you would want to, and it is exactly when the dip would be fatal. **My
recommendation: yes, but the dip is doubled**, so it is a desperate measure rather than a
forbidden one. This is a judgement about the feel of the game and it is yours.

## FORK E — Who proposes a shift?

**Recommendation: model proposes, engine prices — the `military_objective` pattern exactly.**

A new capability `adopt_doctrine` in `engine/capabilities.py`, which generates the parser enum,
the prompt menu and the advisor's list from one declaration. Its `available` predicate refuses a
shift already in progress, an unaffordable one, and an axis that does not apply to this nation.
Its handler writes the `doctrine` row as an ordinary diff citing an ordinary event.

That gives, for free: the player can order it in the same sentence-shaped way they order
anything; a tracked nation can propose it in its own turn; refusals carry a `Refusal` with an
`instead`; and `engine/against.py` will offer it in the action list. **No new channel.**

## FORK F — Seeding, and the prompt half

**Seeding.** A `doctrines: dict[str, dict[str, str]]` on the scenario dataclass — nation to axis
to stance — written through `apply_politics` alongside the arsenals. Historically per era:
France 1936 is *defence in depth* and *professional*; the Soviet Union 1936 is *mass
mobilisation*; Britain 1936 is *surface fleet* and *imperial integration*; the United States 1972
is *carrier* and *massive retaliation*; China 1972 is *minimum deterrence* and *autarky*; India
1972 is *neutrality*. Every one of those is a claim about the period, which is exactly what the
manifest auditor exists to hold a second opinion on.

**A `doctrines` manifest section** with its own `Check`, so the seeding is compared against a
separately written record rather than against the scenario module. `_sections_are_spoken_for`
will then also report an era that declares no doctrines at all, which is the right default for
the sandbox and the wrong one for 1936.

**The prompt half.** One block in `views.build_tracked_agent_view`, between `## Your government`
and `## Your standing objectives`, saying what this country's doctrine *is* and — the part that
does the work — **what it implies about what to do**: a mass-mobilisation power is told it can
absorb losses and should not husband its army; a minimum-deterrence power is told its warheads
are for deterring and not for winning. The same block belongs in the world-simulator briefing so
untracked nations are narrated consistently with the arithmetic they are actually running under.

## Build sequence

**Foundation first, once.** The table, the fingerprint and `PRIMARY_KEY` registration, the
`ENGINE_ONLY` entries, the seeding path, the `adopt_doctrine` capability with its price and its
transition, the briefing block, and the rewind test. This is the commit that moves every digest
and every cassette; doing it once is the whole reason to do it first.

**Then three axes, in this order, chosen purely by how clean the hooks are:**

1. **Mass mobilisation vs professional forces.** Every lever is an existing named constant,
   `research.py` is the precedent for a system moving `unit_template` numbers, and it applies in
   all three eras. If the foundation is wrong, this is where it shows.
2. **Nuclear retaliation vs minimum deterrence.** `deterrence` is one function with one call
   site, `second_strike` is already engine-authored, and it is exactly zero on every 1936 world —
   so a mistake here cannot damage the era with the most content.
3. **Autarky vs trade dependence.** `demand_of` and `shortfall_of` are single functions and
   `industrial_capacity` is already the door they push on.

**Then, in increasing order of new machinery:** forward vs depth (one new frontier-distance
query); carrier vs surface (needs the ship-role decision in FORK B-1 below); decolonisation vs
integration (needs the integration verb); collective security vs neutrality (needs a cost for
not honouring a pact, which is its own design question).

**FORK B-1, unresolved:** the naval axis needs either a per-era map from `unit_template.kind` to
a role, or a weaker version expressed only through `_CARRIER_REACH` and build costs by domain.
**My recommendation: the per-era role map**, because the same problem will recur for every
future system that wants to say something about a *kind* of ship rather than a named one — but
it is a scenario-data change across three eras and it should be a deliberate decision rather
than something smuggled in under a doctrine.

---

## What I would want you to decide before anything is built

1. **FORK A — universal or NPC-only.** Everything else depends on it. I recommend universal.
2. **The starting set.** I recommend the three clean axes above; you may want a different three
   for reasons about how the game *feels* that the hook analysis cannot see.
3. **FORK D-1 — shifting doctrine during a war.** I recommend allowing it at double cost.
4. **FORK B-1 — the ship-role map.** I recommend building it, as its own change, before the
   naval axis.

Not proposed, and deliberately: doctrines for organisations, doctrine *trees* or prerequisites,
and any doctrine that changes what a nation can build rather than how well. Each is a larger
feature wearing this one's name.

---

# Step 3 — The decided design, concretely

**Alex decided FORK A: universal.** Every nation has doctrines, the player included, as real
engine-owned commitments distinct from their advice-only directives. What follows is the
concrete model for the three starting axes, and the shape everything else hangs on.

**One change from Alex's list, flagged rather than assumed.** He named *collective security vs
neutrality* as a starting axis; §1.1 found it is the one of the seven with **no mechanical half
yet**, because Article 5 is deliberately prompted rather than automatic and there is no cost to
an ally who does not come. It is designed below in full, including the new rule it needs. It was designed third and built last of the three, for that reason, and the rule it needs
is built with it (`bdf6a67`).

## 3.1 The state shape

> **Superseded in one respect.** As decided, `axis` carried a closed `CHECK` and a
> fourth axis would have cost a table rebuild. Schema 38 replaced this table with
> `doctrine_stance`, identical but for the constraint, so the four axes still scoped in
> §1.1 are a code entry in `engine.doctrines.AXES` and need no migration at all. The
> shape below is kept as the decision that was taken; the live one is in `schema.sql`.

```
CREATE TABLE doctrine (
    id             TEXT PRIMARY KEY,          -- derived: d_<nation>_<axis>
    world_id       TEXT NOT NULL,
    nation_id      TEXT NOT NULL,
    axis           TEXT NOT NULL,             -- see the note below: schema 37 closed this, 38 opened it
    stance         TEXT NOT NULL,             -- '' while a shift is in flight
    adopted_on     TEXT NOT NULL DEFAULT '',  -- world date
    adopted_turn   INTEGER NOT NULL DEFAULT 0,
    shifting_to    TEXT NOT NULL DEFAULT '',  -- '' when settled
    shift_ends_on  TEXT NOT NULL DEFAULT '',  -- world date
    UNIQUE (world_id, nation_id, axis)
);
```

**A derived id rather than a composite key**, matching `military_objective`, `war_aim` and
`war_effort`: `nation.id` is a global primary key in this schema, so `d_<nation>_<axis>` is
unique across worlds in one file, and a derived id is what lets a row keep its identity across a
rewind and replay. It also means one line in `state/apply.PRIMARY_KEY` and ordinary
`INSERT`/`SET` diffs, with no new applier machinery.

**Absence is the default and means today's behaviour.** A nation with no row for an axis reads
as no stance, and every modifier below is exactly 1.0. That is the `arsenal` property: a world
written before this migrates to one with no doctrines and behaves precisely as it did.

**`stance` is empty while a shift is in flight**, which is how the transition dip is expressed:
there is no third stance and no flag to forget, because the *absence* of a stance already means
"no modifiers", and that is the dip.

## 3.2 Transition: time to bite, and what it costs

* **`SHIFT_MONTHS` is 6.0 of world time**, not six turns. A doctrine shift is half a year of
  elapsed history whether the player is watching it a month or a year at a time — the rule
  settled for armistices and sanctions in the last batch.
* **Doubled at war.** `SHIFT_AT_WAR` is 2.0, so a nation that reorganises mid-war is without
  either stance for a year. Allowed, because it is exactly when a country would want to, and
  ruinous, because it is exactly when it can least afford to.
* **`SHIFT_TREASURY_SHARE` is 0.15** of the treasury, charged on the day the shift starts.
* **`SHIFT_STABILITY` is 2.0** points, on the politically-charged axes only — `alignment` today.
  Reorganising your army is expensive; announcing you are abandoning your allies is a domestic
  event.
* **One axis at a time.** A nation already shifting is refused another until it settles.

**All five are tunable balance numbers and are marked as such in the source.** What is
load-bearing rather than tunable is the *shape*: the dip is the primary cost, because it is
self-balancing — a nation that keeps changing its mind never holds a bonus at all.

**FORK D-1 is implemented at my recommendation** (shifting during a war is allowed at double the
dip) because implementing the recommended default beats blocking on it. Say the word and it
becomes a refusal instead; it is one predicate.

## 3.3 Axis 1 — mass mobilisation vs professional forces

Five levers, every one of them an existing named constant or an existing write:

| | mass | professional | where |
|---|---|---|---|
| manpower ceiling | ×1.30 | ×0.75 | `wareffort.manpower_ceiling` |
| manpower recovery | ×1.50 | ×0.80 | `budget._MANPOWER_RECOVERY` |
| land upkeep per formation | ×0.85 | ×1.25 | `budget._UPKEEP` |
| strength of a land unit when raised | ×0.90 | ×1.12 | the delivery stamp in `production` and `orders` |
| war exhaustion per casualty | ×1.25 | ×0.85 | the `bleeding` term in `wareffort._charge` |

Read across a row and each stance gains two things and pays for two. Mass mobilisation fields a
bigger army faster and more cheaply, and that army is individually worse and its losses hurt the
country more. A professional force is better and cheaper to sustain per head, and **its losses
are not replaceable** — the 0.75 ceiling is the whole downside and it is severe on purpose.

**The quality multiplier is stamped at delivery, not applied at combat time.** A unit raised
under mass mobilisation is permanently a conscript formation; switching doctrine does not
retrain the army you already have, it changes the army you raise next. That is free realism, it
is a second reason a shift takes time to matter, and it costs one multiplication at the point
where `base_strength` is already being read.

**Why these numbers, and the rule they follow.** In §2 I proposed a flat 15% cap on any single
number. Picking real values showed that to be wrong for *stocks and flows*: a 15% manpower
ceiling is not a doctrine, it is a rounding error. The rule I would hold instead, and which the
table above obeys:

> **±15% on anything that feeds combat odds directly** — unit quality, exhaustion — because
> those compound through every contest. **±30% on stocks and flows the player can see and plan
> around** — the manpower pool, its recovery, upkeep. And **every stance moves at least two
> numbers in opposite directions.**

## 3.4 Axis 2 — nuclear retaliation vs minimum deterrence

Applies only to a nation with an `arsenal` row, so it is exactly nothing on every 1936 world.

| | massive retaliation | minimum deterrence | where |
|---|---|---|---|
| deterrence term against your homeland | ×1.15 | ×0.90 | `nuclear.deterrence` |
| warheads committed per launch | ×1.5, rounded down | ×0.5, floor 1 | `nuclear.WARHEADS_BY_STAKE` |
| second-strike share that survives | unchanged | ×1.20 | `nuclear.survivability` |
| arsenal upkeep | a new charge, per warhead | none | `budget.nation_budget` |
| standing after any launch | worse | unchanged | the launch's reputation effect |

Massive retaliation buys a deterrent that is harder to discount and a second strike that ends
the other country, and pays for it in money every month and in reputation the day it is used.
Minimum deterrence keeps most of the deterrent value for a fraction of the arsenal and accepts
that a first strike may disarm it — which bites hard on a nation whose delivery tier has no sea
leg, and not at all on a triad power, exactly as `survivability` already models.

**The one new number is the arsenal upkeep charge**, and it is new machinery: nothing currently
charges anything for holding warheads. Small, but it is a new line in the budget rather than a
multiplier on an old one, and it should be flagged as such.

## 3.5 Axis 3 — collective security vs neutrality

**This axis needs a rule that does not exist, and that is the finding.** `_invoke_defensive_pacts`
writes a casus belli for each ally and collapses the allies' regard *for the aggressor*. There is
no consequence at all for an ally that holds a pact and stays home. So:

**The new rule: an unanswered call.** When a pact is invoked and an ally has not joined the war
within a stated span of world time, that ally pays legitimacy at home and regard with the victim,
and the victim gains the right to treat the pact as dead. This is derivable from state — the
pact, the invocation event and `relation.at_war` are all already recorded — so **no model can
claim an ally came when it did not, or conceal one that did not come**, which is the same
discipline `treaties.py` uses to detect a breach.

Only once that exists does the axis have anything to modify:

| | collective security | neutrality | where |
|---|---|---|---|
| cost of an unanswered call | ×1.5 | ×0.5 | the new rule |
| regard gained by honouring one | ×1.5 | unchanged | `resolution._invoke_defensive_pacts` |
| may sign a defensive pact or alliance | yes | refused | `Capability.available` |
| trade with belligerents | unchanged | stays open to both sides | `trade.route_is_open` |
| legitimacy while at peace | unchanged | a small upward drift | `budget._LEGITIMACY_DRIFT_RATE` |

A neutral is trusted by nobody and cut off by nobody; it keeps selling to both sides of a war and
its own people are content, and when somebody comes for it, nobody comes for it.

**Two genuinely new pieces here**, which is why it was designed first and built last. The
unanswered-call rule is built (`bdf6a67`). Letting `route_is_open` know about neutrality is
**not** built: neutrality's cheapness is the pact upkeep instead (`971583d`), which is a
budget line rather than a trade rule and was the better answer.

## 3.6 The propose-a-shift channel

Two doors onto one function, exactly as `military_objective` has:

* **The player** orders it through a new `Capability`, `adopt_doctrine`, which — because
  capabilities are declared once and generate the parser enum, the prompt menu, the advisor's
  list and the action-search entry from that declaration — is reachable the moment it is
  declared. Its `available` predicate refuses a shift already in flight, and uses the optional
  target that landed in `8405707` to refuse an axis this nation has no business on.
* **A tracked nation** proposes it through a new `doctrine_shift` field on `DiffBatch`, present
  in the schema only for `Role.TRACKED_AGENT`, consumed in `turn_engine` beside
  `declare_objectives` and for the same stated reason: it is intent the engine checks and then
  writes itself, so a batch refused for some unrelated overreach must not cost a nation its say.

**Reachability is five links and every one of them gets a test**, because a channel that exists
and is never offered is the failure mode this project keeps finding: the `DiffBatch` field, the
JSON-schema property, its registration under `TRACKED_AGENT`, `parse_batch` reading it, and the
briefing telling the agent it exists.

## 3.7 Prompting

One block in `views.build_tracked_agent_view`, between "Your government" and "Your standing
objectives" — the same slot and the same argument the view already makes about a leader's agenda
being the lens the objectives are read through. It states the doctrine **and what it implies**: a
mass-mobilisation power is told it can absorb losses and should not husband its army; a
minimum-deterrence power is told its warheads are for deterring and not for winning. The same
block goes to the player's advisor view, so a council is told what its country is built to do.

## 3.8 Seeding, and how it is checked

A `doctrines` dict on the scenario dataclass, written through `apply_politics` beside the
arsenals, plus a `doctrines` section in each era's manifest and a `Check` in `testing/audit.py`.
The manifest is a **separately written record of the period**, so the audit compares two
independent statements rather than proving `apply_politics` can copy a tuple.
