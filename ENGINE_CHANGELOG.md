# Overnight session log

---

## HANDOFF — read this first

Written at the end of an autonomous session that hit its context limit. Everything below
is committed; the tree is clean and the suite is green.

### State

- **1096 tests pass**, 1 deselected (`live`). `ruff check .` clean. Decisions through **69**,
  playtests through **PT25 — the nuclear layer on the real 2026 map, mock**. The most recent
  real-model run is still **PT23 — the gambit channel at a seasonal span, run twice**.
  **Schema is at version 10.**
- **The nuclear layer is built** (decisions 64–69, PT25), on top of the gambit frame exactly as
  the design note sequenced it. Nuclear weapons were prose — a sentence in a preset and two
  technology names — and are now an `arsenal` table, a deterrence term in `compute_pressure`,
  a `nuclear_launch` gambit kind with an engine-fixed effect vocabulary, and **an automatic,
  proportional, one-exchange-deep second strike that fires whether or not the first strike got
  through**. `src/atlas/engine/nuclear.py` is the whole of it; read its module docstring first.
  Measured: a nuclear Ukraine loses 3 provinces in ten turns where the shipped one loses 9.
- **Schema 9 was the first bump in the previous block** of work, and the first migration that
  carried data rather than only adding a column.
  **This session built a SECOND ERA — Modern Day (2026) — through all five phases of the
  scoping plan (0-4), and on the way found and fixed a pacing flaw that predated all of
  it (see the DESIGN NOTE).** ATLAS is no longer a game with one scenario and a
  scenario-shaped hole; it is a game with two eras and a data path for a third.
- Nothing else touched the schema, migrations,
  the write path or the diff pipeline. Old saves are untouched and rewind-safe.
- **The gambit channel is built** (decisions 55–58, PT22) — the bespoke-events design from the
  three design notes, now real. A tracked nation's staff may propose *one attempt at something*
  per turn from a closed vocabulary of seven kinds, stake treasury on it, and have the engine
  adjudicate it with the same seeded RNG and evidence weighting the battle resolver uses.
  **No schema change and no migration** — every kind reuses an event kind the closed enum already
  carried, and the outcome lands as ordinary diffs under `Role.ENGINE`. **This is the frame a
  nuclear launch and an air/missile strike get built on later, as content rather than as engine.**

### What this session did, in commit order

1. **Phase 0 — the live bug.** Selecting Modern Day routed to the generic `earth_seed`, which
   makes a nation of every entry in the pack it is handed — and the pack a scenario opens carries
   the marine layer. Reproduced exactly as scoped: 355 "nations", **113 of them open water** (the
   Arctic Ocean with a government, a capital city and a standing army), **zero sea regions** and
   therefore no navy, and `preset_id` written as the *pack* id, which loads no preset — so the
   era's lore and hard constraints never reached the referee. Fixed: water routes to the same
   unowned `kind='ocean'` regions 1936 builds, coasts are discovered, seagoing templates ship, and
   the scenario's id/name/date reach the world row. (Decision 37.)
2. **Phase 1 — the refactor.** ~550 lines of era-agnostic seeding lifted out of
   `gathering_storm.py` (**1269 → 545 lines**) into `scenarios/seeder.py`, driven by a `Scenario`
   dataclass; three hardcoded 1936 *facts* inside the arithmetic (armour, air, fleets) became
   fields. `scenarios/registry.py` replaced **four** hardcoded `preset.id == "gathering-storm"`
   branches across `cli.py` and `ui/setup.py`. **Guard held: the pre/post gathering-storm
   `WorldSeed` fingerprints were byte-identical across three player choices.** (Decision 39.)
3. **A live fix Modern Day found.** Coast discovery ran against the *simplified* outline, which
   had straightened Israel's Mediterranean shore out of existence along with three other
   countries' and 288 provinces' — and a country the game thinks is landlocked silently loses its
   whole navy at world creation. Now LOD 0. Committed on its own because it changes 1936
   (+16 sea-to-coast borders, nothing else). (Decision 38.)
4. **Phase 2 — the scenario.** `scenarios/modern_day.py`: sixteen designed powers, 90 directed
   relations, 10 standing commitments, 12 engine-authored events of prior history, modern unit
   templates (destroyer/carrier/submarine/transport as data), real fleet orders of battle.
   (Decisions 35, 36, 40, 41.)
5. **Phase 3 — the flashpoints.** `territory/modern-day.json` promotes eight countries across
   four arguments; the generator became per-era and two of its assumptions turned out to be
   1936-only. A country-level Ukraine fell in two turns; it now fights a twelve-turn campaign.
   (Decisions 42, 43.)
6. **Elapsed-time pacing.** Alex's question, and the largest single correction in the block:
   the four jump spans meant nothing to the simulation, so the same war took 0.2 years of game
   time weekly and 14.0 yearly. Now `span = days/30`, flows multiply, rates and probabilities
   compound, the fighting runs in monthly rounds, and build times are in days.
   **Schema 9.** (Decisions 44-47, PT19.)

### The three judgement calls worth Alex's attention

- **Sixteen powers, not the planned fourteen** — Ukraine and Germany added, and **Russia–Ukraine
  starts at war**. A 2026 that pretends the largest land war in Europe since 1945 is not happening
  is not "the present"; Ukraine as a generic one-division minor would fall in a single contest.
  Every other quarrel on the map is deliberately cold. (Decision 40.)
- **`economy` reads as mobilisation capacity, not nominal GDP**, and the figures were calibrated
  against the budget twice — the first pass put **all sixteen powers into structural deficit**
  (North Korea bankrupt turn 1, Russia turn 6). Fifteen are now solvent at peace; Pakistan runs
  −0.4/turn with ~37 turns of reserves, deliberately, as the exact analogue of 1936's Japan.
  (Decision 41.)
- **Nuclear weapons stay latency** — nine nuclear powers, no warhead in any order of battle. The
  preset's hard constraints carry the taboo and the referee reads them every turn. **Open for
  Alex:** whether a played Modern Day feels wrong without a deterrence layer. (Decision 35.)

### Where to pick up

**The pacing model is now complete: every per-turn system reads the turn's length, including
validation** (decision 54, the last of it). Nothing from PT20 or PT21 is outstanding.

**A real-model run at a span other than a month has still never happened**, and it is now the
sharpest gap. Every live run to date is monthly — the one span where all of this arithmetic is
the identity — so *none* of the pacing work has been observed live except `build_days` ticking
(PT21). A short seasonal or yearly run would be the first live test of rounds, of the
compounded rates, and of the freshly-scaled validation caps. It is cheap: a yearly turn is
still one model call, so seven turns of a year costs what seven turns of a month costs.

**Two of the four discoverability fixes remain unproven live** (decisions 51 and 52). They only
do anything when something is refused or an objective goes stale, and PT21 refused nothing.
They will be exercised by any run that goes badly enough — a "wait for it", not a "force it".

**Of the three mechanics in the DESIGN NOTE below, two are built and one is not.** Part 1
(the gambit channel) landed in the previous block; **Part 2 (nuclear weapons) landed in this
one**, out of the note's recommended order — it was taken before Part 3 because Alex asked for
it, and it turned out not to need Part 3 after all: interception reads the air layer *as it
honestly is* (air-domain unit strength plus `integrated_air_defence`) rather than waiting for
the air layer to exist. **Part 3 — air and missile warfare — is the only unbuilt piece**, and
the audit in that note still stands for it: `domain='air'` reaches only the upkeep table, the
build time and a CHECK constraint, so an air wing is summed into a land battle exactly like an
infantry division. `nuclear.penetration_odds` is the socket it plugs into.

**Exercised live and off this list:** a partisan revolt (PT21, Donets'k, naturally caused by an
over-extended occupier). Decision 18's deferred case remains: a wholly-conquered home nation
spawns no partisans, and no run has yet wholly conquered anybody.

**Provider note:** antigravity's eligibility check flakes on a Google profile-picture fetch —
fatal to PT20 on turn 6, absent in PT21, cleared by a re-probe both times. Probe first, and
re-probe on that specific crash rather than treating it as an outage.

### Tools this session added or kept

- `scratchpad/seed_fingerprint.py` — hashes a scenario's `WorldSeed` (not a created database: the
  world row carries `created_at`, so a DB digest churns every run and proves nothing). **This is
  the refactor guard. Run it before and after any change to `seeder.py`.** **It now covers
  both eras** — Modern Day was outside it while every seed change happened to be 1936's, and
  the development pass touches the regions of every scenario.

  Current values, after decision 82 moved the `regions` section of both:

  | era | no player | second | third |
  |---|---|---|---|
  | gathering-storm | `2c90c4dc…` | `0f4b764a…` (Germany) | `5ddc4e7d…` (France) |
  | modern-day | `6f6e863a…` | `1f0e03a4…` (USA) | `66b21229…` (China) |

  | era | nations | regions | adjacency | cities | unit_templates | units |
  |---|---|---|---|---|---|---|
  | gathering-storm | `e9ad4211` | `96b14595` | `251b3bf6` | `e392d55f` | `c4b42a8e` | `8ac6cdf0` |
  | modern-day | `57d68ad6` | `e6b4040f` | `14f4956f` | `967d1b4c` | `834e2a8c` | `993e2ff9` |

  **Decision 96 moved `nations` and `regions` in both eras, and only those.** `nations`
  because minor economies stopped being land area; `regions` because population did, and
  because development is derived from those economies. Before it they were `738e07d8` /
  `a1646035` (1936) and `606a46a2` / `b7c8a1c7` (2026). Geometry, cities, unit templates
  and units are byte-identical across the change, proved the same way as decision 82: both
  sides computed in one process with the new step swapped for the identity function.

  The 1936 `regions` hash was `8162323b` and Modern Day's was `d3a09271` before decision 82.
  **Every other section of both eras is byte-identical across that change**, verified by
  computing both sides in one process with `assign_development` swapped for the identity
  function rather than by trusting that it should be. That is what the per-section guard is
  for: the whole-seed digest says "something moved", these say what.
  **The whole-seed values were mis-recorded here until the nuclear work re-checked them** —
  the `2ef8caea…` family predates decision 46's `build_days` change, which moved them and
  said so.
- `scratchpad/pt_nuclear.py` — the nuclear playtest (PT25), kept. Six sections: the arsenals
  are on the map, deterrence without a launch, the same campaign with and without a deterrent,
  a staff that reads its briefing and declines, a launch resolving with its reply, and rewind.
  The third section is the one worth re-reading: the mock staff *parses its own briefing* to
  decide, so it tests whether the MAD disclosure is legible rather than whether it is present.
- `scratchpad/pt_modern.py` — the Modern Day playtest, kept rather than thrown away like the
  land-war harness. Six sections: it builds, there is water, the era rules reach the referee, the
  fleets are real and can reach a shore, nobody starts bankrupt, a war can be fought. The next era
  wants the same six questions asked of it.
- `scratchpad/rmpt_landwar.py` — the real-model land-war harness from the previous session,
  unchanged.

### What exists now, in build order

Dynamic borders (provinces, pressure, promotion, LOD rendering) → navies (sea regions, fleets,
amphibious invasion, ship construction) → per-country military objectives → the open-ended layer
(freeform channel, public works, industry, research, secret programmes) → a per-turn budget → the
peaceful game *progresses in a way you can feel* → the AI world develops too → a war can be
*settled* by a negotiated territorial peace → holding ceded ground costs stability, can be eased by
garrisoning, and can be lost to partisans → **this block: a scenario is data. The era-agnostic
seeder takes a `Scenario` and builds a world from it; a registry joins presets to scenarios in one
place; there are now two eras, 1936 and 2026, sharing every line of machinery, each with its own
map granularity, fleet, unit templates and research catalogue; and a turn's effects finally
depend on how long the turn was** → a nation's staff can propose *one uncertain undertaking* a
turn and have the engine adjudicate it → **and this block: nuclear weapons are real. Who is armed
is state, a survivable arsenal makes a homeland measurably harder to invade before anybody
launches, a launch is a gambit kind whose consequences the engine owns outright, and the reply is
automatic — which is what makes a first strike almost never the right move.**

`CHANGELOG.md` has the durable feature record. This file has the decisions and the playtest log.

### Things worth knowing before touching anything

- **Run the suite through the venv**: `%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m pytest -q`.
  There is no bare `python` on this machine, and the Windows Store aliases are stubs. `uv run
  --extra dev pytest` *works* but resolves a different PySide6 under which `tests/test_runtime.py`
  fails 1–2 Qt tests spuriously; the same commit is fully green in the project venv. `uv run` also
  drops an untracked `uv.lock` in the repo root — delete it, it is not part of the project.
- **A live world cannot be written under a sync root.** `SyncedPathError` fires on any path
  containing `onedrive`, including the session scratchpad. Use `%LOCALAPPDATA%\Atlas\worlds` or
  `:memory:`.
- **Three packs are baked**: `admin0-50m`, `admin1-10m`, `marine-10m`. Province and marine packs
  are optional — tests needing them skip cleanly.
- **Adding an era is now: one data module + one registry line + one preset.** The module exports
  `SCENARIO`; `registry.SCENARIOS` maps the preset id to it, lazily by name so the setup screen
  does not parse every era's order of battle to list who is playable. The preset id, the territory
  file name and the registry key are **the same string on purpose** — three ids that can drift
  apart is three ways to ship a world whose era rules never load, and a test pins it.
- **`transport` is a reserved kind name in every era.** `engine/naval.py::sea_lift` looks it up by
  name, so an era that renames its amphibious ships keeps every other naval mechanic and silently
  loses the ability to land an army anywhere.
- **`diff_batch.proposer_role` and `event.kind` are closed SQL CHECK enums.** They cannot be
  widened by an additive migration. If a new role needs to *write*, that is a save-invalidating
  change; consider instead having the engine write on its behalf, which is what `freeform` does.
- **New state must be a diff** to survive rewind, and tables written by diffs need an entry in
  `apply.PRIMARY_KEY` and usually in `bootstrap._FINGERPRINT_TABLES`.

### Open decisions for Alex

1. ~~**The nuclear question (decision 35, the plan's Phase 5).**~~ **Closed — built.** See
   decisions 64–69 and PT25. What remains of it is a *balance* question that only a real-model
   run can answer: whether 0.18 of pressure is the right price for a deterrent, and whether a
   staff that is not a regex still declines.
2. **A live run with the nuclear layer in it.** PT25 is mock, and the whole design rests on a
   model reading the MAD disclosure and drawing the conclusion. A scripted staff did; that is
   evidence the page is *legible*, not that a model is *persuaded*. This is now the sharpest
   gap on the list, ahead of the seasonal-span one.
3. **A Modern Day save in progress will migrate to a world with no arsenals** (decision 64).
   If Alex has one running and wants its warheads, it needs restarting. This is the one
   user-visible consequence of the schema change and it is not recoverable in code.
4. **Whether to spend a real-model run on Modern Day.** It has had none.
5. **Part 3 of the design note — air and missile warfare — is now the only unbuilt piece**, and
   the nuclear layer left a socket for it: `nuclear.penetration_odds` is the interception term,
   currently reading air-domain unit strength and `integrated_air_defence` because that is what
   honestly exists. When air is built, it takes that term over.

Autonomous session run while Alex was asleep. Dispatch stood in for decisions.

This file is the working log: what was played, what was found, what was decided and why.
`CHANGELOG.md` is the durable record of *what the engine does*; this is the record of
*how it got there tonight*.

---


---

## DESIGN NOTE — turn length is scalable and nothing scales with it

Raised by Alex. **BUILT — decisions 45-47, PT19.** Alex took the clean option on the one
open sub-decision (`build_days`, with the schema change), and the whole recommendation
below landed in the order it proposes. Kept as written because the *reasoning* is the
durable part -- particularly the three classes of arithmetic, which is what makes this a
correctness problem rather than a tuning one. See PT19 for the after picture.

### What the spans actually are

**Four, not three** (`ui/rail_right.py::_SPANS`): **Week 7, Month 30, Season 90, Year 365**
days. The rail defaults to Month. `atlas eve --days` defaults to 90 and `atlas play` uses
the rail. `TurnEngine.jump(days=30)` is the only entry point and takes any integer.

### Does elapsed-time scaling exist? No. Not partially — not at all.

`days` reaches exactly three places, and none of them is the simulation:

1. `_advance(start_date, days)` — the calendar.
2. `jump_complexity(..., days_advanced=days)` — **model routing only**: a longer span
   scores higher and buys a bigger model (`routing.py:77`, capped at 365).
3. The report line and two UI strings.

Every simulation system is called with `(turn, date)` and ticks exactly once per jump:
`collect_revenue`, `assess_partisans`, `assess_unrest`, `advance_construction`,
`autonomous_investment`, and the whole resolution phase. A 365-day turn fights one round,
collects one month's revenue, ticks one build step and rolls one partisan check.

### The measurement

`scratchpad/pt_pacing.py` runs the identical Russia–Ukraine campaign at each span:

| span | days/turn | turns to conquer | **elapsed** | carrier delivered | economy gained |
|---|---|---|---|---|---|
| Week | 7 | 9 | **0.2 y** | 0.0 y | +1.5 |
| Month | 30 | 8 | **0.7 y** | 0.1 y | +1.4 |
| Season | 90 | 10 | **2.5 y** | 0.2 y | +1.7 |
| Year | 365 | 14 | **14.0 y** | 1.0 y | +2.1 |

An **81× spread** on the same war. A carrier takes eleven weeks or ten years depending only
on how the player chose to pass the time. The economy grows the same amount over fourteen
years as over ten weeks. And the *turn counts* barely move (9/8/10/14), which states the
problem precisely: **the simulation is measured in turns and the calendar is decoration.**

This also means the existing balance work — every constant in `budget.py`, `unrest.py`,
`production.py` and `resolve.py` — was calibrated at 30-day turns and is only correct
there. "Eleven turns to overrun a comparable power" (`resolve.py`) means eleven *months*
and nobody wrote that down.

### The model: one scale factor, three kinds of arithmetic

Define `span = days / 30.0`. **The month is the reference unit because every constant was
tuned there**, so the change is a no-op at the default and no existing playtest is
invalidated. Then each per-turn system falls into one of three classes, and getting the
class wrong is where this goes quietly wrong.

**1. Flows — multiply by `span`.** Quantities that are "X per unit time" and accumulate
linearly. Revenue and upkeep (`budget`), the unrest stability drag and its `_MAX_DRAG`
cap, contest `IDLE_DECAY`, arrears cost, autonomous investment. A year collects twelve
months of tax and pays twelve months of upkeep.

**2. Rates — compound, do not multiply.** Anything expressed as "close *r* of the gap each
turn": economy growth toward its ceiling (`_ECONOMY_GROWTH_RATE = 0.06`), manpower
recovery (0.04), legitimacy drift (0.03), unrest mitigation. The correct scaling is
`1 - (1 - r)**span`, not `r * span`. Over a year, 0.06 compounds to **0.52, not 0.72** —
multiplying overstates a year's growth by 38% and *exceeds 1.0* for any rate above 0.084,
which would overshoot the ceiling.

**3. Probabilities — compound too, for a different reason.** The partisan check is a
per-turn chance (`_PARTISAN_CHANCE_PER_LOAD`, capped at `_PARTISAN_MAX_CHANCE = 0.25`).
Twelve months at 0.25 is `1 - 0.75**12 = 0.968`, not 3.0. Multiplying is not merely wrong,
it is not a probability.

**4. War is its own case, and the interesting one.** Pressure accumulates by
`outcome.advance` per turn, capped at `CAPTURE_THRESHOLD * 0.75` — the cap that guarantees
"a region takes at least two turns however lopsided the fight". Scaling the advance alone
would need the cap raised too, and then a yearly turn resolves an entire campaign on **one
dice roll**: correct in expectation, absurd in variance, and illegible ("the war happened").

The recommendation is **rounds**: `rounds = max(1, round(span))`, each advancing by
`span / rounds`. One model call per jump, `rounds` engine-resolved rounds inside it.

| span | rounds | advance per round |
|---|---|---|
| Week | 1 | 0.23 |
| Month | 1 | 1.00 |
| Season | 3 | 1.00 |
| Year | 12 | 1.00 |

Total advance per elapsed month is constant, variance stays where it was tuned, and the
front produces a *narrative* — "through Sumy in March, Kharkiv in June" — instead of one
opaque leap. The model still decides once per jump: **it sets policy for the span and the
engine grinds the span out**, which is also the honest reading of what a year-long turn
means. Costs no extra quota; the only cost is engine CPU and diff volume.

### Where this is subtle

- **`construction.turns_remaining` is `INTEGER`.** Decrementing by a fractional `span` needs
  either a `REAL` column (a migration, and the first schema change in this whole block of
  work) or the count-down expressed in days (`build_days`, a bigger and better change:
  `BUILD_TURNS` currently means months and does not say so). Cleanest is to keep the
  integer column and let the war-style *rounds* drive it — a year runs twelve construction
  ticks, a week runs one. That makes a weekly jump advance builds too fast; the honest fix
  there is `build_days`. **Flag: this is the one place duration-awareness is not pure
  arithmetic.**
- **RNG determinism.** `sim/rng.derive` must take the round index, or all twelve rounds of a
  yearly turn roll identically and the year resolves as one round twelve times over.
- **Rewind stays exact, and this is worth stating.** `replay.rewind_to` is *inverse-diff*
  (undo `old_json`, newest first), not re-simulation. Nothing about scaling changes it, as
  long as every scaled effect still flows through a diff — which they all do. Sub-rounds
  share the turn number, so rewinding to turn N−1 undoes the whole span atomically.
  Granularity stays per-turn, which is what the UI offers anyway.
- **Turn duration is already recorded.** `turn.start_date` and `turn.end_date` are both
  written, so a past turn's span is recoverable without a schema change — useful for the
  briefings ("last quarter") and for any later re-derivation.
- **Not everything should scale.** The relation swing cap (±25) is an anti-abuse guard on
  what a *model* may write in one call, not a physical rate; a longer jump does not entitle
  a model to a bigger swing, though it may deserve a modest widening. Focus recomputation,
  digest consolidation and routing are per-*call*, not per-unit-time, and already correct.
- **The two-turn capture floor becomes a two-*month* floor**, which is what it always meant.
  A yearly turn can and should take a province — over twelve internal rounds, not one.
- **A week is `span = 0.23`,** so every formula must be right for `span < 1` as well as
  `span > 1`. The compounding forms are (`1-(1-r)**0.23` is well defined); naive
  multiplication is also fine below 1. The rounds formula floors at 1, so a weekly jump
  gets one round at 0.23 advance — correct.

### Recommendation, in build order

1. **Land `span` and the flow/rate/probability scaling** (`budget`, `unrest`,
   `IDLE_DECAY`). Pure arithmetic, no schema, no-op at 30 days — so the guard is that every
   existing test stays green *unchanged*, and a new test asserts twelve monthly jumps ≈ one
   yearly jump on economy, treasury and stability.
2. **Rounds for the resolution phase.** The behavioural core. Same guard: monthly is
   unchanged; `pt_pacing.py`'s ELAPSED column should flatten from 81× to roughly 1×.
3. **Decide `build_turns` → `build_days`** (Alex's call: it is a schema change and a save
   migration, against leaving weekly jumps building slightly too fast).
4. **Re-read the balance notes.** Several docstrings quote figures in "turns" that will
   then mean months; they should say months.

**Entanglement with the scenario work: none.** Phase 3 is a data file and Phase 4 (era-gating
the tech tree) touches a catalogue. This is engine arithmetic. They can land in either
order, and Phase 3 landed first.


---

## DESIGN NOTE — three mechanics, mapped not built

Alex's brief, on paper for reaction before anything is written. Each part opens with an
honest audit of what exists today, because two of the three turned out to have less behind
them than their names suggest.

### Audit first: what is actually there

**Bespoke events: three-quarters of the machinery already exists, in three separate
places, and none of them is the one we want.**

- `sim/resolve.py` has the *adjudication* pattern: a `Contest`, a closed set of
  `EvidenceFlag` circumstances with weights in readable code, a seeded draw from
  `rng.derive`, and an outcome the engine turns into diffs. The model reports
  circumstances; the engine decides what they are worth.
- `engine/freeform.py` has the *expressiveness* pattern: an order no mechanic covers gets
  taken seriously, a model narrates, and the country's feelings move a little. Its
  discipline is the important part — the model picks a direction and a word from a closed
  enum, never a number; it reaches exactly three columns; it proposes and the engine
  writes, under `Role.ENGINE`.
- The `event` table is the *causal spine*: 40 closed kinds, `origin` engine-or-model, a
  `event_cause` edge list, and the rule that every diff must cite a cause.

What is missing is the diagonal: **freeform's expressiveness with resolution's
adjudication.** Freeform is deterministic and reaches three columns. Resolution is
adjudicated but only ever asks one question — who holds this ground.

**Nukes: nothing exists. Not "partly" — nothing.** `grep -i nuclear|nuke|atomic|warhead`
across `src/` returns preset prose, power blurbs, two technology names, and one comment.
`atomic_programme` (1936) and `warhead_modernisation` (2026) move `nation.military_strength`
and `nation.legitimacy`, which is what a shipyard programme would do. There is no warhead
state, no delivery, no targeting, no interception, no fallout, and no retaliation. The
entire nuclear content of ATLAS is the preset's hard-constraint sentence telling the referee
that use is close to unthinkable, plus whatever the models make of it. That is not nothing —
PT20 and PT21 both ran seven months of a war between nuclear powers without anybody reaching
for one — but it is prose, not mechanism.

**Air: it is data.** This is the sharpest finding of the three. `domain = 'air'` appears in
exactly three places in the whole engine: the upkeep table (0.35/turn), `BUILD_DAYS`, and
the schema's CHECK constraint. `_strength()` splits the world into sea and not-sea
(`domain <> 'sea'`), so **an air wing is summed into a land battle exactly like an infantry
division** — same adjacency discount, same morale multiplier, no range, no strike, no
interception, no air superiority. The only domain-aware combat behaviour anywhere is
`_CARRIER_REACH`, and that is about carriers as *ships*.

The consequence worth stating plainly: **three technologies across two eras describe
capabilities the engine cannot express.** 1936's `radar` ("you see them before they see
you") and Modern Day's `integrated_air_defence` and `hypersonic_strike` all resolve to
`base_strength` on a template — which makes air wings better at standing in a field. Nothing
intercepts anything. This is not a bug; every one of those effects lands on a lever shipped
code reads, which is the bar the catalogue sets. But the names promise a layer that is not
there.

---

### Part 1 — Bespoke events (the "gambit" channel)

**The shape.** A model proposes a *gambit*: a specific undertaking with an uncertain
outcome, adjudicated by a seeded roll. Not a new numeric lever and not free narration — a
structured attempt at something, which may fail.

    {
      "kind":       one of a closed set,        # summit, sting, sabotage, defection,
                                                # bribe, incite, expose, mediate, ...
      "actor":      nation id,
      "target":     nation id | region id | null,
      "intent":     one sentence, in world,     # narrative only, never parsed
      "stakes":     "minor" | "serious" | "grave",
      "evidence":   [closed flags],             # circumstances, weighted in code
      "on_success": [closed effect verbs],
      "on_failure": [closed effect verbs],
      "visibility": "public" | "bilateral" | "secret"
    }

**The discipline is freeform's, inherited wholesale.** No numeric field anywhere. `stakes`
and `evidence` are words; Python turns them into magnitudes. `on_success` and `on_failure`
are drawn from a **closed vocabulary of effect verbs**, each mapping to a bounded diff on an
allowlisted column:

| verb | reaches | bounded by |
|---|---|---|
| `warm` / `cool` (target) | `relation.score` | stakes, and the existing ±25 swing cap |
| `shake` / `steady` (target) | `nation.stability` | stakes, and ECON-001's scaled cap |
| `discredit` / `bolster` (target) | `nation.legitimacy` | same |
| `drain` (target) | `nation.treasury` | same |
| `delay_programme` (target) | `construction.days_remaining` | stakes, capped per programme |
| `expose` (event id) | `event.visibility` secret → public | must be an event the actor could see |
| `propose_commitment` | `commitment` row, status `pending` | the other party still has to accept |

**Nothing in the vocabulary moves territory, units, contests or war state.** Those stay in
`ENGINE_ONLY` where they are. A gambit can shape the world; it can never conquer through
narration, and the reason is structural rather than instructional — the verbs that would do
it do not exist.

**Adjudication** mirrors `resolve_contest` exactly:

    odds = base(kind)
         + Σ evidence weights                  # closed set, weights in readable code
         + capability(actor, kind)             # e.g. economy for a bribe, secrecy for a sting
         − resistance(target, kind)            # stability for incitement, legitimacy for expose
    odds = clamp(odds, 0.05, 0.95)
    roll = derive(seed, world_id, turn, f"gambit:{kind}:{actor}:{target}:{index}").random()

Three outcomes rather than two, taking the `swing` idea from resolution: a clear success, a
partial (half magnitude, and the attempt becomes known), and a failure that applies
`on_failure`. **A gambit always costs something** — the stake is charged whether or not it
works, which is what stops it being a free lottery ticket.

**Composition with what exists:**

- *With engine events*: the gambit writes an engine-authored event using an existing kind —
  `espionage`, `sabotage`, `diplomatic_message`, `discovery`, `narrative` are all already in
  the closed enum. **No new event kind is needed**, which matters because `event.kind` is a
  CHECK enum and widening it is save-invalidating. The event becomes a legal cause that later
  diffs can cite, so a gambit is a first-class root in the causal graph.
- *With freeform*: they divide cleanly. Freeform is "no mechanic exists, and nothing
  uncertain is being attempted" — flavour, deterministic, three columns. A gambit is "an
  uncertain attempt at something specific". The rule of thumb: **if it wants a roll, it is a
  gambit.** They should share one per-turn ledger so the two channels cannot be combined to
  walk a column further than either allows alone.
- *With the parser*: an order that currently parses to `other` and falls to freeform should
  be offered to the gambit vocabulary first, and fall through to freeform if it names
  nothing. That makes "send agents to discredit their foreign minister" a real attempt
  rather than a stability nudge.

**Rewind and fingerprint.** Nothing new is required. Rewind is inverse-diff plus
`DELETE FROM event WHERE turn > n`, so a gambit that landed is undone exactly like a battle,
provided every effect flows through a diff — which the vocabulary guarantees by
construction. World-creation code is untouched, so the seed fingerprints are unaffected by
definition. The roll is re-derivable from `(seed, world_id, turn, key)`, so a fresh replay
of the same inputs produces the same outcome; the key must include the gambit's index within
the turn or two identical gambits resolve identically.

**What this genuinely unlocks:** a counterintelligence sting that can fail and expose you; a
summit that produces a real pending commitment or a public humiliation; bribing a minister
to delay a rival's programme; exposing a secret nuclear programme; inciting unrest in an
occupied province. All adjudicated, all cited, all rewindable.

**What it still cannot express, honestly:**

- **Anything needing new state.** "Build a spy network that persists and pays off later" has
  nowhere to live. The vocabulary is stateless effects on existing columns. A later
  `standing_effect` table would fix it and is a schema change.
- **Anything moving territory or units.** Deliberate, permanent.
- **New *rules*.** "From now on my alliance auto-joins my wars" is a rule change, not an
  event. A gambit cannot alter how the engine works.
- **Genuinely unanticipated actions.** The vocabulary is finite. This does not reach Pax
  Historia's "literally anything"; it reaches *"anything expressible as a bounded, adjudicated
  attempt to shift existing state"*. That is a large step up from freeform's three columns
  and a deterministic nudge, and it is not the same thing as open narration. Claiming
  otherwise would be the "trust me bro" this design exists to avoid.

---

### Part 2 — Nuclear weapons

**BUILT — decisions 64–69, PT25.** Landed close to as written below, with four departures worth
naming: the arsenal is a **table** rather than columns on `nation` (absent is the answer for ~210
of 219 nations, and it keeps the row every view reads from growing nuclear columns); **the
proposer does not choose the effects**, which the note left open and which turned out to be the
whole safety argument for reaching engine-only columns; **survivability is derived from the
delivery tier plus boats on the map** rather than stored, so sinking submarines weakens a
deterrent; and **interception did not have to wait for Part 3** — it reads the air layer as it
honestly is today. Kept as written because the reasoning is the durable part, particularly the
paragraph on retaliation, which is the rule the whole thing turns on.

Building on nothing, so the whole thing is new. The design goal is that a nuclear power is
*harder to fight* without anybody ever launching, and that launching is available, terrible,
and almost never correct.

**Arsenal as engine-only state.** A `nation.warheads INTEGER` column plus a `delivery` tier
(none / aircraft / missile / triad), both additive with defaults of zero — so every existing
save migrates to a non-nuclear world, which is correct for 1936 and needs seeding for 2026.
Both go in `ENGINE_ONLY`: a model that could write its own warhead count has an instant-win
button. Research grants them — `atomic_programme` and `warhead_modernisation` stop being
generic stat bumps and start meaning what they say.

**Deterrence is the part that matters, and it costs almost nothing.** A survivable arsenal
is a term in `compute_pressure` against the *homeland* of a nuclear power: invading one is
much harder, before anybody launches. This is where most of the value is, it needs no new
event, and it makes the 2026 map read correctly — Russia and Ukraine fight over Ukraine, and
nobody marches on Moscow.

**Launch is a gambit kind** (composing with Part 1), gated by: at war with the target,
`warheads > 0`, a delivery tier that reaches, and the preset's hard constraints still in the
briefing. Adjudication is two-stage — **interception first** (Part 3's air-defence strength
against the delivery tier), then effect on what gets through.

**Effects, applied by the engine:**

- the target region's `population` and `development` crushed; a large, persistent unrest
  load added — reusing the existing unrest machinery rather than inventing fallout state;
- the target's `stability` and the *launcher's* `legitimacy` both fall hard;
- every third party's relation with the launcher collapses, which is the taboo made
  mechanical rather than instructional.

**Retaliation is engine-authored and automatic.** If the target has a surviving arsenal, the
engine launches back — no model call, no decision, no chance to reconsider. **This is the
single most important rule in the design**, because it is what stops a first strike being a
winning move: the attacker does not get to find out whether the defender would have dared.
Survivability is where `submarine` earns its place — an arsenal survives in proportion to
the boats at sea, so a triad power cannot be disarmed by a first strike and a land-based one
can. That reuses a unit kind Modern Day already fields.

**MAD as discoverability, not as a lecture.** The retaliation rule goes in the briefings
verbatim, the way the economic caps now are. A model that knows a second strike is automatic
and unavoidable does not need to be told nuclear use is unthinkable; it can work it out.

**The failure mode to design against** is a model that launches for narrative reasons on
turn three. Three guards: the stake is charged up front, the legitimacy collapse is
immediate and visible in the next briefing, and the automatic retaliation means the
launcher's own homeland is in the report before it has decided anything else.

---

### Part 3 — Air and missile warfare

From near-zero, but the smallest of the three and the one that makes existing content
honest.

**Air superiority as a separate contest over the same ground.** Before the ground contest in
a region resolves, air units on both sides contest the air above it — same seeded machinery,
different participants. The winner carries a modifier into the ground contest. That single
change makes an air wing something other than expensive infantry.

**Range is what makes air different.** `unit_template.move_range` already exists and already
says 3 for an air wing against 1 for infantry, and nothing reads it for reach. Air should
contest and strike out to `move_range` regions, not just adjacent ones. Nothing else in the
engine reaches past adjacency, so this is the mechanical identity of the arm.

**Strike missions.** The natural home is a fourth objective kind, `strike` — but
`military_objective.kind` is a CHECK enum (`push`, `hold`, `withdraw_from`) and widening it
is save-invalidating. **Two options, and Alex should pick**: (a) accept a save-invalidating
change, or (b) express strikes as a gambit kind from Part 1, which needs no schema change at
all and gets seeded adjudication for free. **(b) is the cheaper and better answer**, and it
is the strongest argument for sequencing Part 1 first.

**What a strike does — and does not.** It degrades: `construction.days_remaining` pushed
back (bomb the yards), region `development`, and the `strength`/`morale` of units in the
region. **Air alone never takes ground.** That keeps the existing occupation rules intact and
keeps air a supporting arm rather than a second way to conquer.

**Interception is where the technologies become real.** The defender's air strength plus any
`integrated_air_defence` effect reduces what gets through. `radar` in 1936 and
`integrated_air_defence` in 2026 stop moving `base_strength` and start moving an interception
term. `hypersonic_strike` reduces the interception the attacker suffers — which is what
hypersonics actually are for, and currently is not what the tech does.

---

### Sequencing: 1, then 3, then 2

**Part 1 first.** It needs no schema change and no migration, so it is the cheapest to get
wrong and re-do. It is testable in mock the day it exists. And it is *load-bearing for the
other two*: strikes and launches both want a bounded, adjudicated, model-proposed attempt,
and building that channel once is much better than building nuclear plumbing and air
plumbing separately and discovering they are the same shape.

**Part 3 second.** It is the smallest, it closes the honesty gap where three technologies
describe a layer that does not exist, and it is a prerequisite for the interception stage of
a launch. With Part 1 in hand, strikes cost one gambit kind rather than a save-invalidating
enum change.

**Part 2 last.** It depends on both — interception from 3, the launch decision from 1 — and
it is the one with the most ways to be an unfun game. It also benefits most from having a
played Modern Day behind it: the deterrence term is a balance question, and balance
questions want evidence.

**The one thing I would consider doing out of order** is the deterrence term from Part 2. It
is perhaps thirty lines — a survivability term in `compute_pressure` — it needs no launch
mechanic at all, and it would make the 2026 map read correctly immediately. If Alex wants a
quick win before the big channel, that is the one.

## OVERNIGHT DECISIONS

Every call made without Alex, and the reasoning.

| # | Decision | Why |
|---|---|---|
| 143 | **Every model call reaches the routing log**, because logging moved into `_invoke` rather than living at the call sites. | PT31 measured **126 calls in a run and 81 in the log**. Two of the turn engine's six call sites logged; gambit, organisation, freeform and the digest did not, so 45 calls a run carried no attestation, no served model and no duration — invisible to an audit and to the laboratory's own detectors. Logging now lives at the one door every model call already passes through, for the same reason the capability manifest exists: **a thing every caller must remember to do is a thing some caller will forget.** The `RoutingDecision` is optional and **provably redundant for four of the six** — gambit, organisation, freeform and the digest all route with `route(Role.X, 0.0)` and `_band_for` returns `LOW` for every role but WORLD_SIM and DIPLOMACY, so the reconstruction is identical field for field; world_sim, whose band and complexity actually vary, passes its own. One line per *call*, not per retry, so the log's count and the turn's count are the same number. **The guard is behavioural, not structural**: a test that grepped each call site for `log_decision` would pass the day somebody adds a seventh site, so the test asserts `len(routing_log) == report.calls` — there is no way to add an unlogged call and keep that true. Verified live: 9 reported, 9 logged, with a `quota_exhausted` failure now visible that would previously have been silent. |
| 144 | **`MockProvider(always(...))` silently discarded its script**, and six harnesses had been running on schema defaults. | Found while writing the test above. `name` is the dataclass's first field and `script` its second, so a positional call binds the script to `name`: the mock then answers every request with a schema-shaped default and reports the script *function* as its provider — which is what surfaced here, as `type 'function' is not supported` binding a log row. It fails silently and convincingly. Six scratchpad harnesses were affected, including `pt_report.py`, **whose output I read and reported on earlier in this session**; it is also what sent me chasing a phantom for three probes while debugging the rewind fix, where I misread a schema default as evidence the loop was not running. `__post_init__` now refuses a `name` that is not a `Provider`, which turns a silent wrong answer into an immediate error, and all six call sites are fixed. |
| 141 | **Governments and agreements rewind** — `leader`, `commitment`, `commitment_nation` and `relation.treaty_status` all move through the diff pipeline. | PT31's laboratory found the mismatch and the isolated cases were stark: a coup on turn 3 rewound to turn 2 left the junta in the chair, and a pact signed on turn 3 was still standing with `treaty_status` still reading `non_aggression`. Both tables are in `_FINGERPRINT_TABLES` *because* they are state a rewind has to put back, so the digest moved and the world did not. Decision 101 named the risk exactly — *a rewind past a coup would replay the turn under a different administration* — and then wrote the chair with direct SQL, reasoning that "the rewind replaying that same turn" would restore it. **A rewind does not replay.** `seat()` now requires an `event_id` rather than taking one optionally, because CORE-001 refuses a diff that cites nothing and an optional citation would mean an optional rewind. Commitment ids are chosen at write time rather than left to AUTOINCREMENT, because an insert diff has to *name* the row it created — that name is what a rewind deletes by. Proved by repeating PT31's experiment: both now MATCH, the chair holds who it held, and the commitment count returns to zero. |
| 142 | **Two rules caught the change before any test did, and both were right.** | **INFO-002**: the commitment diffs were marked `PUBLIC` and a negotiation is `BILATERAL`, so *a covert cause cannot produce overt state* refused every extraction. The scope now comes from the cited event rather than from what the writer assumed — the identical mistake the gambit layer made with a `SECRET` cause, and the identical fix. **CORE-001**: `sync_treaty_status` built its batch at `world.turn_number` while citing a later turn's event, so *a cause cannot postdate its effect* refused the reconciliation and left the column standing behind the agreement it follows. The batch's turn now comes from the event it cites. Worth recording because both are the validation layer doing precisely its job on engine-authored writes: neither was worked around, and each one named a real error in how the write was framed. |
| 140 | **Focus calls run concurrently from one snapshot**, and land in nation-id order. | Five tracked powers cost five sequential model calls of up to four minutes each. They are subprocesses and the wait is entirely I/O, so there was never a reason for it to be serial. Three phases: **read** every briefing first, sequentially, from the state as it stands; **call** them all at once; **apply** sequentially in nation-id order. **No database access happens during the concurrent phase**, which is what keeps one sqlite3 connection safe — the views are already built and nothing is applied yet. Measured with a recording provider: three powers finish in the time of one, linearly. Two properties are deliberate rather than incidental. **One snapshot for every briefing** means no tracked power sees another's moves inside the same turn, which also removes the first-mover advantage the old loop handed to whoever sorted first by name — a fairness improvement that fell out of the change rather than motivating it. **Nation-id order for applying**, not arrival order (a race, and it would make the same world replay differently) and not the roster's `ORDER BY name` (which a rename would reshuffle). Two agents reasoning from one snapshot can now propose writes to the same field; the first lands and the second is caught by CORE-004 and severed, which is exactly the machinery decision 107 built — **nothing was weakened to make the parallelism work**, and a test asserts the rule is still severable. The overlap is proved by recording each call's start and end and requiring the windows to intersect, because *written as if parallel* and *parallel* are different things. |
| 138 | **Victory conditions scale to the scenario**: a share, a margin, an absolute floor, a duration, and a per-scenario override. | Decision 124 required a *gap* rather than a share, which fixed the sandbox's simultaneous winners and left a hole: **a gap is a ratio, and a ratio has no scale.** Hold three provinces of four and you are three times the runner-up having conquered nothing anybody would call a campaign. So domination now needs all four: a quarter of world ground value, twice the runner-up, **at least eight provinces actually held**, and **two consecutive turns** — because ground changes hands every turn of a war, and a threshold crossed by one province taken in March and lost in April was the front moving rather than a conquest. On a real map the absolute floor is inert (25% of the 2026 map is well over a hundred provinces) and on a toy one it is the whole guard, which is the right division of labour. **And the sandbox switches domination off entirely** rather than retuning it: there is no honest threshold on a three-nation map where two powers hold 44.4% each, and a tiny sandbox is not a small world but a different kind of object. The override is keyed on the world's *preset* rather than its size, because which scenario this is was somebody's choice and how many provinces it has is a consequence — a rule keyed on the consequence would change behaviour silently the first time a designer subdivided a country. Both real maps still report *nothing won at seed*, 4.2× short of domination. |
| 139 | **Growing a test fixture exposed two tests that had been passing on a saturated component.** | The scoring fixture went from two provinces to eleven so it could clear the new absolute floor, and two unrelated tests broke: nine provinces at development 40 **saturate the ground ceiling at 1.0**, and a component pinned at its maximum cannot demonstrate either that it moves or that occupation discounts it. Both now build a small world of their own and say why in a comment. Worth recording because neither test was wrong and neither was testing what its name claimed — they were reading a clamp. |
| 137 | **The playtest laboratory** (`atlas.testing.laboratory`): eight suspicion detectors and a portable bug bundle. | **Every integration seam this project has shipped was found by a person reading a transcript and thinking *that is not right*** — research vanishing into `other`, an active pact not stopping an invasion, the Council resolving nothing turn after turn, two nations dominating at once. None of them had a test, and the reason is structural: a test asks whether one thing behaves, and a seam is two things disagreeing. So this is the reading, automated. **The detectors are the product; the harness is twenty lines.** Each one is a sentence a reviewer actually said about a real bug: an accepted order that moved nothing (the tracked agents returning empty), a name the world contains refused as unknown (the objectives seam), an active pact between powers at war (the treaty seam), an advertised capability refused three turns running (research), two simultaneous winners (domination), a model call with no trace either way, events recorded against an unmoved world, and something built that nobody ordered (transport becoming infantry). **Each is proved twice** — it fires on its own bug and stays quiet on an uneventful run — because a detector that fires on healthy turns is one people learn to ignore, and an ignored detector is worse than none. A broken detector is reported as a finding against itself rather than ending the sweep, so seven working ones cannot disappear with it and hand back a clean run. The bundle copies the world through SQLite's own backup rather than by copying the file, because a live connection holds pages in its WAL and a file copy would produce a bundle that is subtly *not* the world the findings came from. |
| 135 | **Causal turn reports** (`atlas.engine.causality`): why a number moved, and what became of each order. | The event log has always been complete and has never been an *explanation* -- a player reading `economy 28.2 -> 31.3` had to work out which of revenue, development, wartime disruption and a finished public work was responsible, from a list in the order the engine happened to write it. **The breakdown cost nothing to build because `diff.reason_event_id` already exists**: rule CORE-001 has required every change to cite the event that caused it since schema 1, so grouping a turn's diffs by their cited event turns the audit trail into a sentence without a single new column, and it is derived so it cannot disagree with the log it summarises. Orders get a **correlation id** assigned before any layer can refuse them, because an order is a sentence and what happens to it is spread across the parser, the handler and the resolver — a refusal three layers down now reports against the words the player typed. Three statuses, not two: an order **nothing executes** is *not attempted*, which is a different thing from *refused*, and telling a player the first was the second sends them to fix a sentence that was fine. Refusals carry a suggestion keyed on a phrase the message already contains rather than on an invented error code, because the refusals are prose and a code vocabulary would be a second thing to keep in step with the first. |
| 136 | **The `LEFT` join is about deleted events, not missing citations — and a test found that out by being refused.** | The breakdown was written to count changes citing no event, under a plain label, so the parts would sum to the whole. The test for it could not be written: **CORE-001 refuses an applied diff with no `reason_event_id`**, so that case cannot occur and the code was defending against nothing. The case that *does* occur is the reverse — a rewind deletes the events of the turns it undoes while the diff rows survive, so a breakdown of a rewound turn finds citations pointing at nothing. Same fallback, entirely different reason, and the comment now says the true one. Worth recording because the code was correct and its stated justification was not, which is the kind of thing only an attempt to test it catches. |
| 134 | **The whole undertaking catalogue is orderable by a player**, and `nuclear_launch` is deliberately not. | Decision 125 opened the channel with `sabotage` and `air_strike`; this is the rest — summit, envoy, bribe, sting, incite, expose — which needed no new mechanism now the manifest and the boundary exist. Targets resolve through `reference`, so an ambiguous nation is told apart from an absent one. Inference phrases are chosen by a stricter test than before: not *could this mean X* but **could this mean anything but X**, which is why `summit` does not claim "meet" and `bribe` does not claim "pay". A sentence claimed by two kinds is refused rather than resolved to whichever is first in the table — a wrong guess spends the player's money on something they did not order. **On nuclear release, the decision is to leave it out, and the reason is the third condition rather than the first two.** Explicit and unambiguous were achievable: an exact kind id, no alias inference, refusing anything guessed. *Confirmed* was not — **there is no confirmation step anywhere in this architecture.** An order runs text → parser → action → handler inside a single `jump()`, and there is no point at which the engine can stop, show the player *you are about to launch*, and wait; building one is a new interaction mode in the turn loop, the UI and the CLI, and would be the only place in ATLAS where a turn blocks on a human. Without it, "explicit" reduces to *the parser emitted the exact string*, and the parser is a language model reading a sentence somebody typed. A test pins all three locks: not in the offered set, no inference phrase, and an exact `gambit_kind` of `nuclear_launch` still falls through to the refusal. **A deferral with a named unblocker**: if a confirmation step is ever built for anything, this becomes a five-line change and should be revisited. |
| 132 | **Agreements can now end badly** (schema **15**; `atlas.engine.treaties`). | Decision 120 made an active commitment bind. It could still never be *violated*: nothing in the engine had ever written `status = 'broken'` — the value has sat in the CHECK constraint since schema 1 with no code path reaching it — so a pact could be signed, honoured, and betrayed by nobody. A treaty you cannot break is set dressing. A breach now does five things, and it is five or it is not modelled: **marks the agreement and records who** (two columns, because *this ended badly* and *Karthos ended it* are different facts and only the second supports a grievance); **costs regard with every injured party and legitimacy at home**; **hands the injured party a casus belli**, authored per party because the war guard keys grievances on the *aggrieved* nation and one event naming the violator would arm the wrong side; **takes `treaty_status` back down**, which decision 120's recompute does for free; and **reaches the turn report ranked just under a nuclear use**. **Detection is a query, not a claim** — a pair at war with an active pact between them *is* a breach — so no model can assert a betrayal that did not happen or conceal one that did, and attribution comes from the engine-authored war declaration rather than from anybody's account of it. A war nobody is recorded as starting leaves the pact standing and says so, because an agreement cannot be broken by nobody. **A `ceasefire` or `peace_treaty` is not breachable**: those exist *because* of the war, and reading them as violated by it would mean every peace betrays itself the moment it is signed. Expiry is a separate path and a separate report line, because showing it beside a betrayal teaches a player to distrust a power that did nothing wrong. |
| 133 | **SQL comments moved out of `CREATE TABLE commitment`.** | Not style. SQLite re-parses a table's stored SQL on `ALTER TABLE … DROP COLUMN`, and a comment left dangling by the removed text fails the re-parse with *incomplete input* — which broke twelve migration tests the moment schema 15's columns were added, because the downgrade scaffolding drops them to simulate an older world. The explanation now sits above the table, where dropping a column cannot strand it. Worth recording because the failure mode is invisible until something drops a column, and the thing that drops columns is the test that proves the migration works. |
| 130 | **The reference boundary** (`atlas.engine.reference`): one resolver, five vocabularies, and *ambiguous* as an answer distinct from *unknown*. | The engine is strict about ids and shows people names, and every naming bug this project has had lives in that gap. `naming` already had a good tiered matcher and keeps doing the matching; two things were missing. **It could not say why it failed** -- `None` meant both *no such place* and *seven places match*, so no caller could write a message worth reading, and ambiguity is the useful answer because it means the vocabulary was right and the phrase was not specific enough. Objectives now name the candidates instead of telling a staff the place it was just shown does not exist. **And it only knew the two vocabularies that are tables** -- technologies, civil projects and unit kinds are catalogues, and every caller that wanted one wrote its own matching, which is why a `transport` order became infantry and why *All-welded construction* matched nothing. Five vocabularies, one algorithm, and `Capability.targets` names the same five so the manifest and the resolver cannot disagree about what a target is. **The engine is no less strict**: resolution happens at the boundary and everything behind it still deals only in ids. |
| 131 | **Resolving a field and resolving a sentence are different questions, and collapsing them cost a capability I did not notice removing.** | Wiring `build_project` through the boundary broke two tests, and they were right to break: `entry.id in haystack` had been doing real work -- finding *radar* inside *begin work on radar*, which is what a parser that left the field empty actually produces. The substring test was still wrong (it matched `rail` inside *derailed* and inside any sentence containing the letters), so the answer was not to restore it but to make the second question explicit: `mentioned_in` scans prose for **whole-word** occurrences of an id or display name, and returns *ambiguous* when a sentence names two things rather than taking the first. `_named_or_mentioned` asks the field question of the field and the prose question of the summary. Worth recording because the failing tests were the only thing that caught it: the refactor looked clean and had quietly deleted a path players use. |
| 129 | **The capability manifest** (`atlas.engine.capabilities`): one catalogue, and the parser enum, the council's list and the prompt menu are generated from it. | An action type was declared in **three** places that had to agree and nothing made them -- the parser's schema enum, `views.ADVISABLE_ACTIONS`, and `orders._HANDLERS`. A parity test held the first two together (it is what caught `gambit`), and nothing tied either to a handler, an availability rule, or a sentence explaining the act. That is the shape of every seam bug found: *research exists in the engine and the parser has never heard of it*. **What makes it structural is that exhaustiveness runs both ways.** A capability that never reaches a prompt fails; a parser enum value with no capability fails; a capability claiming `orders` handles it when it does not fails; one naming a module that does not import fails; one requiring a field the schema cannot emit fails; a worked example naming an action type outside the enum fails. **Verified by breaking it on purpose** rather than by assertion: adding a `blockade_port` capability with nothing behind it turns the suite red. Two things it deliberately is not: it is **not a dispatch framework** -- handlers keep their signatures and the manifest records *where* execution lives, because a catalogue that also runs things is one people are afraid to edit -- and availability **fails open on a database error and does not catch a programming error at all**, because a world that cannot answer must not silently lose a mechanic, while a bug in a predicate hiding behind a menu one line short is the exact failure this module exists to prevent. |
| 127 | **Every provider test had been failing on this machine, and containment is now decided by filesystem identity rather than by spelling** (`paths._contains`). | Ten tests -- two failures and eight collection errors -- because `assert_isolated` refused the directory `SandboxDir.create()` had *just made*. **The diagnosis, measured rather than assumed:** this process runs inside a Windows packaged-app (MSIX) container, which virtualizes writes beneath `%LOCALAPPDATA%` into the package's private store. The asymmetry is the bug: `runs_root()` predates the redirect and `Path.resolve()` returns it unchanged, while a *newly created* child under it resolves to `…\Packages\<pkg>\LocalCache\Local\Atlas
uns\…`. So the check compared a redirected child against an unredirected parent, `is_relative_to` said no, and **no sandbox this environment can produce could ever pass.** Fixed by asking the filesystem instead of the string: if plain containment fails, walk up from the target and ask `os.path.samefile` -- volume serial and file index, not text -- whether any ancestor *is* the runs root. That accepts whatever redirection the OS is actually performing without this module knowing MSIX's layout. **Explicitly not done by rewriting `Packages\<pkg>\LocalCache\Local` back to `Local` textually**, which would accept any *other* package's private store -- a different physical directory -- as equivalent to ours, by spelling alone. That is the exact class of hole an allowlist exists to close, and a parametrised test pins it: a path *shaped* like the virtual store is still refused, because no such directory exists to have an identity. Nine other locations -- the repo, home, the temp dir, `C:\Windows`, the worlds root, a sibling of runs -- are pinned refused too. |
| 128 | **The failure was never silent; it became invisible when I excluded the module to get a green number.** Two guards. | Worth recording because the mechanism matters more than the bug. `tests/test_providers.py` reported eight errors and two failures every run; what hid them was `--ignore=tests/test_providers.py`, which I used to report a suite as passing while a module was excluded. So: an end-to-end test asserts `assert_isolated` accepts a sandbox `SandboxDir.create()` just made -- through the real objects, no hardcoded shape, so it holds on any machine and fails on any machine where the two disagree again -- and a structural test fails if any provider test acquires a `skip` or `xfail`, which is the way a broken environment gets "fixed" into permanent silence. The `live` mark stays the one legitimate exclusion, because those tests spend real quota. **Result: 1416 passing with nothing ignored**, against 1328 with the module excluded. |
| 125 | **A player could not order a sabotage or an air strike at all** — new `gambit` action type routing a named undertaking into the existing channel. | *"Sabotage Karthian railways"* was parsed as `espionage`, and the engine reported that the agents had learned nothing: true of espionage, and not remotely what was ordered. The catalogue has had a `sabotage` kind and an `air_strike` kind the whole time — **there was simply no path from a player to any of them**, because the channel only ever offered gambits to tracked nations. The handler assembles the same payload the model-facing channel produces and hands it to the same `apply_gambit`, so the odds, the stake, the ledger cap and the roll are identical whoever asked; the player chooses the undertaking and the target and nothing else. **`nuclear_launch` is deliberately excluded**: a channel where a parser's reading of a sentence can put a warhead in the air is not a channel that should exist. When the parser files a gambit without naming which, the kind is inferred from a short literal vocabulary rather than guessed across the catalogue — a wrong guess spends the player's money on something they did not order — and an unguessable one is refused with the options named. The advisor's list moved with the parser's enum, because a standing parity test fails if they ever diverge. |
| 126 | **Refusal prose was cut mid-word** (`untrusted.clip`). | A bare `[:400]` ends a refusal at *"the staff judged the moment unfa"*, which reads as a bug in the engine rather than as a long sentence — and this is prose shown to the player verbatim, so it is the first thing they notice. Backs up to a word boundary and ellipsizes, with two limits worth stating: it will not back up past half the budget (three surviving words tell a reader less than a hard cut does), and a single word longer than the whole budget is still cut, because there is no boundary to find. |
| 122 | **A staff was told the ground it had just been shown is not on the map.** Objective declarations resolve region references through the existing resolver. | Briefings render ground as `Northmark [r_north]`; live agents write back `Northmark`; `objectives` did an exact id lookup and refused it. The order pipeline already had `naming.resolve_region` for precisely this, so this is one call rather than a new mechanism — the same class of fault as the PT30 naming issue and the same fix. A place that resolves to nothing is still refused, **and refused by the spelling the staff actually wrote** rather than by a resolver's best guess at it, because the message is the only thing that tells them what to write instead. |
| 123 | **A Security Council that declined to act left no record — and was therefore asked again every turn, forever.** | Codex's pointer was that a decline is invisible to history. It is worse than that: **the trigger dedupe keys on an organisation-authored event citing the cause**, which is what stops a body convening about the same war twice, and the early-return path wrote no event at all. My own mock run had already shown the symptom without my recognising it — *the United Nations Security Council resolved nothing*, on turn after turn after turn. That is the loop, not a body with nothing to say. The decline path now records a session exactly as a resolution does: an event citing the trigger, `convened_turn` moved so the column means *last sat* rather than *last agreed*, and authority priced at `AUTH_FAILED` — the same as a vote that failed, because from outside the room it is the same event: the body was asked and did not act. |
| 124 | **Domination measured size where it meant dominance** (`_DOMINATION_MARGIN`, twice the runner-up). | Decision 119 made the condition pick a single leader, which was necessary and not sufficient: the three-nation sandbox seeds **two powers at 44.4% of world ground each**, so whichever of them a rounding-level development shift put ahead was announced as dominating the world without having conquered anything. **Raising the share cannot fix that** — at 44.4% each, any threshold below it crowns somebody and anything above it is unreachable on a real map — which is the useful thing the small scenario exposed. What was missing is that domination is a claim about the *gap*, not the size. Twice the runner-up: on the 2026 map the leader stands at 1.09x the second and the sandbox pair are level at 1.0x, so neither qualifies, while a Karthos that actually swallowed Aurelia stands at 8x and plainly should. |
| 121 | **The parser could not order research, and could not order a landing** -- two gaps of the same shape: the resolver knew something the parser was never told. | **Research.** Public works and research share one handler, but the parser view described `project` as five civil ids and listed no technologies at all. A real-provider run ordered *All-welded construction*, the parser had nothing to map it to, filed it as `other`, and freeform then refused it as anachronistic for 1897. The player's own briefing already had the menu; the parser now gets the same helper, and the schema field says research ids belong there too. **And the deeper half, which the pointer did not name:** the menu listed *display names only* while `build_project` takes an *id*, so even shown the list a parser had to guess `welding` from *All-welded construction*. Every entry now carries its handle in the `Name [`id`]` form the region and unit lists already use -- naming a thing and naming the handle you act on it by are different jobs and this list has to do both. **Landings.** `naval.amphibious_reach` already worked out which far shores could be assaulted, through a sea the nation can cross with transports to carry the troops, and the resolver honoured the route -- but the parser saw only land adjacency and refused the invasion before resolution could ever use it. It now reads the same function, so the two cannot disagree about what is reachable, and a nation with no ships is told nothing rather than offered a crossing it cannot make. The test builds a strait rather than borrowing the sandbox, because the sandbox offers no route and the first version of it *skipped* -- a test that verified nothing about the feature it was named for. |
| 120 | **A negotiated pact did not stop an invasion.** `relation.treaty_status` is now the materialised view of active commitments, recomputed on every change (`diplomacy.sync_treaty_status`). | Found by a real-provider run, and the worst of the integration seams because both halves worked. `extract_commitments` wrote a `commitment` row and nothing else; **every consumer in the engine reads `relation.treaty_status`** -- the invasion guard `resolution._is_friendly`, the gambit odds, the router, and four separate briefing lines. So an active non-aggression pact appeared in the history a model was shown, appeared in its own briefing, and did not stop the engine treating a march across that border as an invasion. Two ways to close it: teach seven readers about commitments, or make the one column say what the commitments say. The second, because there is one column and seven readers **and because the column is already what the player is shown** -- `commitment` stays the source of truth and this is its view. **Recomputed from scratch rather than patched**, so one function handles signing and breaking alike: drop a commitment to `broken` and the next sync takes the status back down, which no amount of incremental patching gets right for free. **Not every kind maps** -- a `ceasefire` or `peace_treaty` ends a war, which `at_war` already says, and reading either as a standing pact would make marching in stop being an invasion; `military_access`, `territory_cession` and `tribute` are obligations rather than pacts. Both directed rows always move together, because a pact one side honours and the other does not is not a pact and would make the guard answer differently depending on who was marching. Seeding is deliberately **not** synced: a scenario writes both columns itself and is authoritative there, and the fingerprints confirm it -- `2c90c4dc…` / `6f6e863a…` unmoved. |
| 118 | **CORE-004 compares at the precision the model was actually shown, per field** (`_SHOWN_PRECISION`; regard at 0 decimals, everything else at 1). | A batch was being refused for citing exactly the snapshot it was handed: engine state accumulates fractional flows at full SQLite precision while briefings show a rounded figure, so a model told `economy 20.6` and writing `expected_prior=20.6` against an actual `20.58` lost the whole proposal to a difference it had no way to see. **The audit that was asked for, in full.** `expected_prior` is set by the parser and by nothing else — its default is `None` and no engine module assigns it — so the blast radius is exactly the model-writable set: **18 columns, 11 numeric.** Two are integers (`nation.is_alive`, `commitment.resolved_turn`) where a sub-0.1 change cannot exist. Of the nine reals, six are `round(x, 1)` in `visibility.nation_self` (`economy`, `stability`, `legitimacy`, `treasury`, `manpower`, `military_strength`) and two in `visibility.own_regions` (`region.development`, `region.population`). **Nothing anywhere is rendered at two or more decimals, so the risk of rounding away a meaningful change does not exist in this set** — no rate, probability or multiplier is CAS-guarded. But the audit found the *opposite* fault, which the blanket policy did not fix: `relation.score` is rendered `{:+.0f}`, and it genuinely holds fractional values (the organisation layer writes regard to two decimals, the nuclear layer to three), so a model shown `+42` against an actual `42.37` was still refused. Per-field precision fixes both directions at once. The guard test renders both fields through the *real* view builders and fails if the table and the prompts ever disagree — a written-down belief about precision is the thing that goes stale. |
| 119 | **Four playtest fixes**: a parser that rejected the player's own country, unavailable units silently becoming infantry, two nations declared dominant at once, and a stray domestic target. | **(a)** The parser view never said which country it was parsing for, so "invest in Aurelia's economy" played *as* Aurelia was refused for naming a nation absent from the contact list. **(b)** `_unit_kind` skipped any template with no build time, so an order for a `transport` fell through to the generic path and **turned into infantry without telling the player** — now an explicitly named kind the scenario does not define is refused by name, and a kind that does exist keeps its own cost, strength and kind rather than the generic constants. **(c)** Domination tested each nation independently, so on a small map several could each hold 25% and all were congratulated — which says the opposite of what the condition means; it now requires the ground leader, singular, strictly ahead of the runner-up. **(d)** A domestic action still carried the `target_nation` the parser had recognised. Every handler ignores it for domestic kinds so nothing misbehaved, and it is blanked so the *next* handler cannot read it and conclude the order was aimed at somebody — on a copy, so the parser's own answer survives as the record of what the model said. |
| 114 | **Strategic resources and the trade that moves them** (schema **14**: `resource`, `trade_route`; `atlas.engine.trade`, `atlas.scenarios.resources`). | Everything a country needed was one abstract number. Three goods -- `oil`, `steel`, `rare` -- and the smallness is the design decision: **every resource is a number a model has to reason about inside a prompt**, and a dozen would buy spreadsheet depth and pay for it in the thing that makes ATLAS work. A shortage does not make a country poorer, it makes it **unable to build**: the shortfall scales `industrial_capacity`, a door that already existed, so a blockaded country still collects its taxes and cannot lay down a battleship. Resources feeding `economy` was rejected because economy has four inputs already. **The three disruption levers are three existing systems being asked a question they could already answer** -- sanctions the organisation layer already writes, a sea lane gated by the same `naval._sea_is_ours` that decides landings and supply, and war -- so nothing needed a line of code saying *embargo*. Both CHECK constraints are **authored on new tables rather than widened**, and an old save has no rows, so the layer is inert in it exactly as air and leaders are. Measured on both maps and the shape is recognisable: in 1936 the USA and USSR are self-sufficient while Germany, Japan, Italy and France all import oil; in 2026 the swing suppliers the seed derives are Saudi Arabia and Russia for oil and China for steel and rare earths. Japan at war with its oil seller is at the 35% capacity floor within two years. |
| 115 | **My own sketch predicted the fingerprints would move. They did not, and that is worth writing down rather than quietly not mentioning.** | The sketch said this would move the `nations` section of both eras. It moves neither: resources live in their own tables, not as nation columns, and the seed digest hashes `WorldSeed` -- so `2c90c4dc…` and `6f6e863a…` are **unchanged**, no baseline needed updating, and the endowment tables can be edited without touching one. What did change is the *world* fingerprint, which gained `resource` and `trade_route` sections, for the reason `arsenal` has one: a rewind past a blockade that restored the fleet but left the stockpile it drained would replay the turn with a full magazine. That digest is not a checked-in golden value, so adding sections costs nothing. The prediction was wrong because it was made about a design that had not been written yet, which is the risk a sketch-first rule runs and a cheap one. |
| 116 | **Three calibrations the real maps overturned, and one dead-key mistake my own new guard caught.** | **(a) The lane walk reused `logistics.supply_map` and cut all 648 seeded routes on turn one.** Military reach and commercial reach are not the same thing: supply is bounded at three hops because an army is fed from depots behind it, and a merchant ship is not. Saudi oil could not reach Japan because Japan is more than three provinces from Riyadh -- true, and nothing to do with trade. The lane walk is now unbounded and crosses anybody's land freely; only water a hostile fleet commands stops it. **(b) The starting store was months of a country's own *production*, so a country producing no oil started with none** -- putting Japan and Germany at the capacity floor on turn one before a shot was fired. A world does not begin mid-crisis; the store is months of *demand*. **(c) A bare world seeded a flat 0.4 a month whatever a nation's size, so a country that doubled its industry built fewer things** -- a unit mismatch rather than a supply crisis, caught by an existing production test. An unscripted world now produces exactly what it burns, with a test guarding the two coefficients against drifting apart. And the endowment table shipped with the same dead `deu`/`rus` duplicates decision 106 had just removed from the leader table -- **caught immediately by the per-era guard that decision added**, which is the best evidence that guard was worth writing. |
| 117 | **35 land regions on the 2026 map have no adjacency at all**, so trade to them read as permanently blockaded. Handled openly. | Aruba, Anguilla, American Samoa and thirty-odd others are single-province islands the packs never joined to anything. Without a rule they sit at the starved-capacity floor forever and appear in every turn report as short — which is punishing a country for a hole in the geometry. **Nobody blockaded Aruba**: a nation nothing can reach also cannot be *cut off* by anyone, so `is_unreachable` exempts them and the seed opens no routes into them. If the packs ever gain those edges this becomes dead code in the honest way — returning False for everybody rather than quietly still firing. |
| 112 | **Supply lines** — `atlas.engine.logistics`, and like the victory layer it needed **no schema at all**. | An army could march to the far side of the world and fight exactly as well there as at home, and a province taken behind a front changed nothing about the front. Supply is a *reading of the map* rather than a fact about a unit — who controls what, which seas are held, where the sky is contested, all of it changing several times a turn — so a stored supply state would be a cache invalidated by every conquest, landing and air battle, and one a rewind would have to unwind. Derived, it cannot be stale; the only thing written is what being cut off *cost*, as diffs on `unit.strength`. The trace is a weighted multi-source walk over the adjacency graph the air layer already builds, and each rule answers something specific: **depots are your own core, not merely ground you hold** (a supply base needs an administration behind it, which is what makes a deep invasion a corridor rather than a spreading stain); **ground you do not control does not pass supply** (so encirclement is a consequence of where the line is rather than a rule anybody wrote); **enemy air control costs range rather than blocking it**. And the one that cost nothing at all: **seas are already regions**, 113 of them with 587 crossings, so supply walks through water and `naval._sea_is_ours` — the same function that decides whether a landing happens — decides whether the walk passes. Measured: blockading Britain's seven seas takes it from 8 supplied regions to 1, and no line of code says *blockade*. Being cut off costs both attrition and effectiveness, through the same `compute_pressure` / `compute_advance` doors the air and leader layers use, so all three compose. |
| 113 | **The supply range was three times too generous, and the first hook made every attack in the world unsupplied.** | Two errors, both caught by measurement rather than by reasoning, and the second was worse. **(a)** `supply_multiplier` tested whether the attacker was in supply *at the province it was attacking* — which an attacker by definition does not control, so every attack anywhere read as cut off and a 16-turn conquest became 34. The honest test is whether the thread reaches the front: the region or any province beside it. A pocket still fails it, which is the property that must not be given away to fix the other. **(b)** `_SUPPLY_RANGE` was 6 and could not bite on any campaign these maps can produce: ATLAS's world is 429 regions across 242 nations, so most countries are a *single province*, and a Russia holding thirty provinces from Vladivostok to the Rhine never needs supply to travel more than **three** hops. Swept on the real map: at 2 India cannot supply its own country (90%), at 5 a fifteen-country conquest is fed entirely, and **3 is the only value that feeds every power at home while making an overextended conquest go short** (91%). The table is in the docstring. Consequence, and it is the layer working rather than a test being loosened: France overrunning Germany went from 16 turns to 22, because at the start France can supply an attack on **2 of Germany's 10 provinces** and after taking five can supply 3 of the remaining 5 — the campaign proceeds in bites the length of the thread. The bound moved to 24 with that measurement written next to it. My own fixtures were written against range 6 and silently stopped testing what they claimed; they are now derived from the constant. |
| 110 | **The game can now be won** — `atlas.engine.scoring`: a five-component score, three victory conditions, **and no schema change at all**. | ATLAS could be played forever without ever being told whether you were doing well. Chosen over the fog layer on which was cleaner to do *well*: fog is mostly built, so its real increment is active intelligence threaded through every view builder, which is wide and risky — and the roadmap's one stated reason for deferring scoring (*an economy that is still land-area-shaped would bake the flaw into the win condition*) was removed by decisions 82 and 96. Three design choices, sketched in the changelog before any code existed. **(a) Nothing is stored** — `score_of` is a pure read, so a score cannot go stale, cannot be farmed, cannot be set by a model, and **rewind-exactness is free rather than defended**: there is no scoreboard to unwind. A test asserts no table anywhere holds one. **(b) A victory is a fact in the event log** — `rewind_to` deletes events past its target, so an achievement unwinds with the turn it belonged to, and it lands everywhere events already go including the model's own briefing. The compromise is stated rather than hidden: `event.kind` is a closed CHECK, so a `'victory'` kind would rebuild the table and invalidate every save; these are `narrative` carrying a marker the module owns. **(c) Winning does not end the game** — a sandbox whose window closes when a threshold trips punishes the thing people play a sandbox for. Measured on both maps the table is recognisable — 1936 reads USA, UK, Germany, France, USSR, and Germany, the USSR, Japan and Italy are the four the engine says are *strongest at arms* — and nothing is won at seed, which is the right answer. Conquest scores at exactly 40% of annexation, because the score reads through `ground_value` rather than counting provinces: taking France moves Germany's ground 0.093 → 0.122, and keeping it at the peace table moves it to 0.165. |
| 111 | **Two calibrations the seed measurement overturned, both of which read fine on paper.** | **Domination was 40% of world ground and would never once have fired**: the largest power on the 2026 map holds **5.9%**, and the top eight together hold 27%. A 243-nation world spreads its ground very thin, and a threshold picked by imagining a board game with six players is a threshold nothing reaches. At 25% it is four times the United States — a genuine hegemon, and 4.2× further than anyone currently is. **And `leading_on` was measuring the ceilings rather than the world**: comparing raw components made almost every power come back "standing", because standing runs 0.5–0.65 for everybody while ground runs 0.07–0.2 for everybody. That is a fact about `_FULL`, not about Japan. Measured against the *field* it discriminates immediately and along historically recognisable lines. Also fixed while writing the test for it: a sustained condition has to be marked on every turn it holds so the run can be counted, but an event reading *and has for years* in the year it started is **the engine writing a falsehood into the permanent record** — the log now states what was true that turn and the report announces what was achieved. |
| 109 | **The master AI builds where a work is worth something** (`_least_busy_region` → `_best_investment_region`). | The chooser was *fewest works under way, then alphabetically*, which was exactly right while every province was worth the same and stopped being right the moment decision 82 and decision 96 made development vary. The load-bearing reason is not the tiebreak: **ground you hold but do not own yields 40% of its development, and it yields that discount twice** — once into revenue and once into the ceiling the economy grows toward — so a steelworks on conquered ground earns two-fifths of the same steelworks at home, for the same money and the same years. The old chooser could not see the difference, and an occupier's provinces sort early in a good many alphabets. Second: restive ground is being *drained* by `erode_development` while a work fills it. So the order is own core, then quiet ground, then spread the works out (which is what the old chooser was for, and is kept), then development. It is a preference and not a filter — a power driven out of its own country keeps building. **Measured at seed the change is small and the honest number is worth writing down: 7 of 242 nations pick differently**, because most nations are single-province minors and nobody occupies anything on turn 0. The two that matter are legible — Russia was building in Adygey (development 0.62) and now builds in Moskva (7.04); India was in Andhra Pradesh (4.34) and is now in Delhi (8.68) — and the occupied-ground half only comes into play mid-war, which is when it is worth having. The development tiebreak is **honestly a tiebreak**: project effects are flat, so marginal revenue is the same in a rich province and a poor one. What it buys is a master AI whose choices a player can follow, which is most of how a player learns to trust it. |
| 107 | **A batch may lose the diffs the rules refuse and keep the rest** — severability is now *a violation names a diff*, bounded by a new `SEVER_MINORITY`, and re-validated in a loop rather than once. | A batch was all-or-nothing except for one whitelisted rule, so **a proposal with 24 changes and 3 mistakes landed nothing** — the turn's whole narrative discarded, and everything spent generating it discarded with it. Reproduced exactly in the playtest: 21 of 24 now stand. The old whitelist was defending something real — a batch reaching for an engine-only column may have misunderstood the contract, and applying the rest of *that* is applying a half-understood change — but it was expressing it as a per-rule veto, which is the wrong axis: **the question is how much of a batch is refused, not which rule caught it.** So the argument moves to where the size of a batch is actually known. Three bounds, each answering a different failure: a violation naming no diff ends it immediately (those are the joint ones — units conjured from nothing, an aggregate over its cap, a broken mutual reference — and no subset of the batch answers them); severing may not take a majority, so a batch must be more right than wrong to stand; and at most four passes, because severing moves the aggregates and each pass costs a full re-apply and rule sweep. **The rules already drew the line this needed** — of the 27 places a violation is raised, 16 name a diff and 11 do not, and the 11 are exactly the joint ones — so this is the apply layer believing `diff_seq` rather than inventing a taxonomy. A stale compare-and-swap keeps its exemption from the majority bound, for the rule-specific reason already written down. **Rewind stays exact**, and that is the first test in the file: replay reads `diff.status`, per-diff status was already recorded, and a partly-applied batch unwinds to a byte-identical world. |
| 108 | **The turn report shows what a partial batch lost** (`JumpReport.refused_diffs`, and the overlay's refusal column). | A partial apply returns `applied=True`, so decision 107 on its own would have made refused writes **invisible**: the turn shows a successful batch and says nothing about the three changes thrown out of it. Kept apart from `rejections`, which is whole proposals refused — one is a proposal that failed and the other is a proposal that mostly worked, and conflating them tells the player the wrong thing about their models. Each refusal carries the rule that caught it and that rule's own words, because *3 changes were dropped* is not something anyone can act on and *[CORE-001] cited event 987654 does not exist* is — and because if refusals are ever fed back to the model that wrote them, this is the text that would go. |
| 105 | **The modern leader table has names, researched 2026-08-04** (all 17 entries); and `LEADERS_1936` loses two dead keys. | Decision 101 shipped the modern table deliberately blank because who holds an office changes between the build and the play and naming the wrong head of government is worse than saying *the government of Japan*. That condition is now met: the names were checked against current sources by somebody who could, and **the date they were checked is written next to the table** so the next reader knows how stale it might be rather than having to guess. Nothing mechanical moved — the engine runs entirely off `traits`, so this is display and briefing text, and the seed fingerprints confirm it: `2c90c4dc…` and `6f6e863a…`, unchanged. Two entries came with a note that they are recent changes of office (the United Kingdom in July 2026 and Iran in March 2026) and are the two most likely to need revisiting first; both are one-line edits by construction. **Which office an entry names stays a design choice** — China is the General Secretary and Iran the Supreme Leader because those are whose agendas the entries carry — so the docstring now says so, to stop a future correction quietly changing what the game models. |
| 106 | **The leader-table guard checks each era against its own map**, not against both at once. | `LEADERS_1936` carried a `deu` and a `rus` entry duplicating `ger` and `sov`. The 1936 map has neither id, so **those two rows had never once been read** — and the old guard passed them because `deu` and `rus` exist in the *modern* map. A cross-era alibi is exactly what a table guard should not accept: the whole purpose of the check is that a key nothing resolves is a key doing nothing, and 'it resolves somewhere else' does not answer that. A second test now also fails any modern entry that loses its name, so the state decision 101 shipped on purpose cannot creep back in as a bug. |
| 104 | **The succession roll runs once per turn, not once per round** — the one stage in the turn loop that is a *draw* rather than a *flow*. | Decision 101 put it inside the per-round loop beside unrest and the budget, where it looked right and was not. The stream is keyed on `(world, turn, nation)` deliberately, so that adding a call elsewhere in a turn cannot shift anybody's luck — and the price of that guarantee is that two calls on the same turn **are the same call**: the second redraws the first's numbers. A flow repeated six times is six months of flow; a draw repeated six times is one draw. A mock run found it rather than reasoning did: **242 nations assessed 48 times over four years produced zero changes of government**, because 48 calls were 8 independent draws. Moved out of the loop with the turn's whole span, so `rounds()` does the several months honestly inside it, and it reads the stability the fighting and the budget just left behind rather than the stability they started from. After: five governments in four years across 242 nations, which is the rate decision 102 tuned for. The test pins the *property* — two calls on one turn draw identically, consecutive turns do not — because the property is what makes the placement necessary, and a test on the placement would pass while the reason for it was forgotten. |
| 101 | **Nations get somebody to be: leaders, traits and coups** (schema **13**, new `leader` table; `atlas.engine.leaders` and `atlas.scenarios.leaders`; new `leader` fingerprint section, **every existing section byte-identical**). | `stability` and `legitimacy` moved every turn with nobody's name attached to them, so a coup was a number going down and the tracked-agent briefing spoke in the voice of a colour on a map. Four things make it a subsystem rather than flavour. **(a) No trait invents a mechanic** — every one is a multiplier on a number some module was already computing, and `modifier()` is the single door they come through, so a trait cannot mean one thing in combat and another in the treasury. Five levers, wired to the real functions: `advance` in `sim.resolve`, `development` on what a completed public work is worth, `unrest` on the drag held foreign ground puts on stability, `gambit` on the odds, `coup_risk` on survival. **(b) Nothing is free** — a test walks the table and fails any trait that is only good; measured end to end, a bellicose/ideologue/hardliner government is +23% on advance and −15% on works, and a technocrat/cautious/dealmaker one is the mirror. **(c) Nobody is invented** — 1936 carries real names because it is settled history, and **the modern table carries none at all on purpose**: who holds an office changes between the build and the play, and a game that confidently names the wrong head of government is worse than one that says *the government of Japan*. The modern entries carry the part that does not go stale — real title, distinct traits, an agenda. Filling in names is a one-line edit per country and needs somebody who can check today's answer. **(d) An old save is untouched** — no leader row means every multiplier is exactly 1.0 and no succession is rolled, so a pre-13 world resolves on the numbers it always did; that is the whole reason this could be a migration rather than a save-invalidating change, and it is the first test in the file. Also: the seed fingerprints did **not** move — leaders live outside `WorldSeed`, and all twelve section digests match decision 96's baselines exactly. **Needs Alex's visual QA** for the one interface change: the top bar's second line now reads the government instead of the word `playing`. |
| 102 | **The coup curve is quadratic below 50, not linear below 100** (`_DANGER_BELOW`; base rate 0.5%/year). | The first tuning charged for every point below 100 and the playtest killed it: **408 governments fell in ten years across 243 nations, and the ones that fell averaged 57.0 against the survivors' 57.4** — not a simulation of instability, a lottery with extra steps. The cause was measurable and was not the constants: **215 of the 243 nations on the 2026 seed sit at exactly 60/55**, so any curve that charges them at all charges the whole map identically and the dice decide everything. Two fixes, both from the diagnosis rather than from taste: only the bottom half of the scale is dangerous, and within it risk is quadratic in the shortfall — which is both the shape the historical record has and the shape that gives a player time to notice. The base rate is what a settled country carries and is now set at the observed real-world figure. After: 12 falls a decade world-wide; Iran 3.1%, North Korea 3.4%, Chad 2.8% and Pakistan 2.0% per year against 0.45–0.72% for the undistinguished map. **The playtest's own verdict line was wrong too** and was rewritten: comparing mean stability of fallen against survivors is a true measurement of the wrong thing when 88% of the sample is identical by construction. |
| 103 | **A coup on a collapsing state was refused and reported as having happened.** The stability and legitimacy hit is now clamped against what the country actually has. | A flat −8/−12 takes a state at stability 4 to −4; rule ECON-003 refuses the whole batch; the government falls in the turn report and stays in power in the database. **The worst-off states are exactly the ones the roll fires on**, so this was the common case rather than an edge one — it appeared on the first seed the test searched. Floored at 1 rather than 0, because a state pinned at zero has nothing left for the next shock to bite on and the point of a spiral is that each turn of it still costs something. |
| 100 | **The Security Council was unreachable in the shipped app** — `Role.ORGANISATION` had no entry in `default_role_models()`, and `Router.route` reads `self.pins[role]` with no fallback. Pinned to `claude-sonnet-5`; `test_every_model_role_has_a_pin` now walks the `Role` enum. | Found by pointing the new situation-report playtest at a real map through the app's own pin table, which no prior harness did — every UN playtest passed pins of its own, so the whole layer looked healthy while being dead the moment a real player reached it. It did not degrade quietly either: the first time a body convened, `jump()` raised `KeyError` mid-turn and the turn was lost. The guard walks the enum rather than listing roles, so the next role to be added cannot ship the same way; `ENGINE` is excluded because it is what the engine writes as itself, not a job given to a model. |
| 99 | **The situation report leads with what mattered, not with what was written first** (`JumpReport.significant()`, and a rewritten cost column). | The overlay's first column was *the first eight events on the turn by row id* — and row id is insertion order, an artefact of which engine stage happens to run earliest. Measured on a real map: a turn in which the Security Council convened opened with five identical lines about air fighting over Bulgaria, Belarus, Czechia, Germany and Denmark, and the session did not appear at all. Every one of those facts already had its own structured field on the report — `nuclear`, `sessions`, `pacts_invoked`, `rebelled`, `sanctions`, `in_arrears`, `gambits` — and **none of them reached the overlay**. Gravity lives on the report rather than in the widget because how grave a turn's facts are is a property of the turn; the numbers are ordinal and nothing reads them but the sort. `describe()` is deliberately untouched: it is the console dump and being exhaustive in a fixed order is a different job. The second column changed too — it reported model calls, batches and events, which is engine telemetry rather than what the turn cost the world; those counts survive as one line at the bottom, because a turn that made eight calls and applied nothing is still worth being able to see. Long lists are summarised, so fifty sanctioned minors is one line rather than fifty. **Needs Alex's visual QA** — the ranking is unit-tested and playtested, but how it *reads* in the overlay cannot be checked from here. |
| 97 | **Three measured map overlays: wealth, unrest and the sky** (`MapLayer.DEVELOPMENT` / `UNREST` / `AIR`, plus `unrest` and `air_control` on `MapViewState`). | Three subsystems built this session were invisible on the map that exists to show the world. The layer machinery was already right -- a layer is a pure function of the same state, so switching one is an ordinary re-derive -- and wealth needed **no data plumbing at all**, because `RegionState.development` was already carried and simply never drawn. Unrest reads through `unrest.held_foreign_provinces` rather than recomputing, so the map and the stability charge can never quote different numbers for the same ground; it is the residual *after* garrison relief, so a province with troops on it reads quiet, which is the true picture. Three decisions worth naming: a choropleth **repaints the fill and leaves the outline and the hatching alone**, so it never hides the political map underneath; **no reading is not a reading of zero** -- ground nobody has flown over is left blank rather than coloured "uncontested", because the alternative claims a fact the world does not have; and the air layer is coloured by *whose* sky it is deepened by the margin, since it is the one measured layer whose interesting fact is an owner. Fog still wins over every overlay. The development ramp is scaled against the richest province on the map rather than a constant, so it still spreads across the whole scale in a poor era. |
| 96 | **Population and minor economies come from real demography, not land area** (new `scenarios/demography.py`; **`nations` and `regions` fingerprint sections move in both eras**). Both tables are normalised so the world totals are exactly what they were. | The last two places the map's *shape* stood in for its *substance*, and decision 86 flagged them. `population = min(120, area * 0.4)` gave Tibet 112 against Henan's 16 — Tibet has 3.6 million people and Henan 99 — but the **clamp** was worse than the ratio: every large country saturated at 120, so the United States, Australia, Brazil, Canada and *Antarctica* had identical populations and identical army caps, and Russia's manpower ceiling was 809 against India's 204. `minor economy = 4 + area * 0.02` made **Antarctica the richest minor on the map in both eras** and left Taiwan on 4.9, which is why Hsinchu seeded at 0.43 development however large a multiplier the heartland table gave it. **This is a combat change, not only an economic one:** manpower is capped at `population * 2` and manpower is what units cost, so population decides how big an army a country can field at all. Normalising to the previous totals is what makes that safe — aggregate manpower capacity and aggregate minor wealth are unchanged and only their distribution moves, the same redistribute-never-conjure discipline decision 82 used. Within a country, population is spread by the *same weights as development*, because the rich provinces and the populous ones are overwhelmingly the same provinces; one table, so the two cannot disagree. Measured after: India and China top the population table in 2026 and China, India and the USSR in 1936; Antarctica's economy is 23.8 → 0.1; Henan is 71 against Tibet's 5.9. A test validates every key in all three tables against both real maps — it caught three typos (`lts`, `ssd`, and three Antarctic codes no era has) on its first run. |
| 95 | **Flagged, not fixed: freeform's own nudges are still flat per turn.** `PER_FIELD_TURN_CAP` is 6.0 however long the turn is, and freeform's per-nudge magnitudes do not scale either. | Same class of bug as decision 93, in the channel next door: a year-long turn allows exactly as much freeform movement as a weekly one, so the same 52x swing applies to nudges. The gambit call now passes a span-scaled cap for its own effects, which is what its scaled stakes require; the shared ledger means freeform benefits from the raised ceiling at long spans without its own magnitudes growing to use it. That is strictly better than before and still not right. Left alone because scaling freeform is its own calibration with its own playtest, and this change was licensed for gambits. |
| 94 | **The undertaking is decided *before* the build orders, not after** (`turn_engine.jump`). | The measured cause of PT30's declines, and the half that a cost change alone could not fix. The two are separate model calls; the build call came first and spent the purse, and the gambit call was then offered the residue — treasuries of 0.6 to 4.4 against a minor stake of 6. Decision 34 had already tried disclosure (the purse lines in the build view) and PT30 showed it was not enough. **The asymmetry is what makes this the right order rather than an arbitrary one:** a stake is 6–34 and a public work 20–130, so the small claim can be absorbed by the large decision and the large claim cannot be absorbed by the small one. A staff that spends everything on a steelworks and cannot then bribe anybody is making a real trade-off; a staff that is never offered the choice is not. No reserve, no floor and no paternalism: the build view reads live treasury, so it sees exactly what the undertaking left. Its purse text — which used to promise the gambit came *afterwards* — was rewritten with the order, and a test pins the sequence against the source. |
| 93 | **A gambit's stake is a flow: cost, magnitude and per-field ceiling all scale with the turn's span, with the month as the identity point** (`gambit.py`, `turn_engine.py`). | It was a flat fee per attempt, and with at most two attempts a turn that made the money a power could commit to schemes **51.4 a month on weekly turns against 0.99 on yearly ones — a 52x swing in how much intrigue a game contains, decided purely by how the turns were sliced.** Exactly what the pacing model exists to prevent, and the last flat magnitude in the engine. Three things scale together and the third is the trap: scaling the *price* alone would have made a season's gambit three times worse value than a month's, and scaling price and magnitude while leaving the per-field cap at a flat 6.0 would have charged 73 at a yearly span for a magnitude of 18.25 trimmed to 6.0 — four times the price for the same movement. Measured after: 12.00 a month at every span and an exchange rate of 0.250 at every span. The briefing quotes the scaled figure and says why, because a briefing that says 6 while the treasury is debited 18 is decision 50's exact failure. |
| 92 | **(Alex's call on decision 91) The Council convenes about inherited wars as well as new ones**, once per war, deduped exactly through `event_cause` (`organisation.py::triggers_this_turn`). | Decision 91 flagged that the body was structurally deaf to every scenario flashpoint, because its jurisdiction read `war_declared` events *from this turn* and Russia--Ukraine, Italy--Abyssinia and Japan--Manchuria are all seeded as `relation.at_war = 1`. Alex's answer was both arms. **The implementation needed no heuristic and no new state, because the engine already records who started every war**: the seeded history carries turn-0 `war_declared` events naming the aggressor — *"Russia invaded Ukraine in February 2022"*, *"Italy invaded Abyssinia (October 1935)"* — so the new arm reads the same events at `turn < now` instead of `turn = now`, filtered to wars still live. That matters: at turn 1 of either era **nobody occupies anybody**, so the obvious who-holds-whose-ground heuristic had nothing to go on and could as easily have accused the victim. The dedupe is per-war rather than per-aggressor or a cooldown: a session cites its trigger, so `NOT EXISTS (a session citing this declaration)` identifies the war exactly and it can never come back however long it runs. Escalation inside it returns through the annexation and nuclear arms, which is the intended second bite. The authority floor, the veto and the one-session-a-turn cap are untouched — and the floor matters more now, since an inherited-war trigger is true on every turn until it is acted on. |
| 91 | **Flagged, not fixed: the Council cannot convene about a war that was already running when the game started.** | `triggers_this_turn` reads `war_declared` events, and every scenario flashpoint in both eras — Russia–Ukraine in 2026, Italy–Ethiopia and Japan–China in 1936 — is seeded as `relation.at_war = 1` with no declaration event. So in PT30 the Council sat out a two-and-a-half-year war it existed to have an opinion about, and would have done so in every scenario's headline conflict. **This is a design question, not a bug**: a body that convenes on turn 1 about every pre-existing war would open every game with a session, and one that never does is deaf to the wars that matter most. The options are a one-off "inherited conflicts" pass at world creation, a trigger on *escalation* within an existing war (a province taken, a capital threatened), or leaving it. Alex's call. |
| 90 | **Air superiority lapses when nobody flies for it** (`air._lapse_uncontested`, 0.25 of margin a turn). | PT30: Russia won the sky over 41 provinces during the Ukrainian war, the war ended at turn 6, and Russia still held all 41 at turn 10 — four turns of peace later — because nothing ever cleared the rows. Any later war in those provinces would have opened with a bonus won in a campaign two years dead. **Air superiority is maintained, not owned**, and the first cut of the layer simply did not say so. The decay converges and a row at the floor stops being written, so a world at peace pays nothing for it. Reachable on its own as `lapse_air_control`, because `resolve_air_war` returns early when there is no air war — which is exactly the state a world enters the moment a war ends, and exactly when the lapse is needed. |
| 89 | **A covert gambit's stake is scoped like its cause, not hard-coded public** (`gambit.py`, one word). | The stake diff was `Visibility.PUBLIC` while a covert success files a `SECRET` event, so rule INFO-002 — "a covert cause cannot produce overt state" — refused the entire batch every time a covert gambit *worked*. **Sabotage, bribery, stings and incitement landed only when they failed**, deterministically, and four playtests had not noticed because a mock run never rolled a covert success with a real payload. PT30 caught it on Ukraine's one successful sabotage. Paying for a covert operation is itself covert, so the correct scope was never public — and the rule that caught it was doing precisely its job. |
| 88 | **Annexation at a peace table now files a `territory_transfer` event** (`resolution.py::_make_peace`). | The Security Council's annexation trigger reads that event kind and **nothing had ever written one** — the cession path recorded a single `peace_signed` event and put the annexation in a report headline. The trigger was dead code from the day it was added. PT30 found it the hard way: Russia annexed Kiev, Donets'k, Mykolayiv and Sumy in one settlement, the most Council-worthy act of the run, and no session convened. Recovering your own core is not annexation and files nothing, which is the same distinction `report.headlines` already drew between "recovered" and "annexed". |
| 87 | **`sanitize()` takes one argument** (`gambit.py::apply_gambit`), and the `air_strike` path now has tests that go through `apply_gambit` rather than calling `air.strike` directly. | The live run's *first* air strike crashed the turn: `TypeError: sanitize() takes 1 positional argument but 2 were given`. The gambit wrapper for the kind had never once been executed — 38 air tests, a mock playtest and a full suite all called the engine function directly, and only a model ever chose the kind. **A path only a model takes is a path only a live run tests**, which is the argument for these runs in one line. The length bound moved to a slice; the aim is validated against the closed `STRIKE_AIMS` vocabulary inside `air.strike` anyway. |
| 86 | **Flagged, not fixed: a *nation's* economy is still land area.** `minor_power` seeds every minor at `economy = 4 + area * 0.02`, and `population` is `area * 0.4` for every region in both eras. | The same proxy this pass removed from provincial value is still the source of national value for 190-odd minors, and it is visibly wrong in both directions: Antarctica seeds an economy of 23.8 (named down in the heartland table, since it would otherwise be worth more developed ground than Bavaria), and **Hsinchu City — where a serious share of the world's semiconductors is made — seeds at 0.43, because Taiwan's whole national economy is 4.9**. No heartland multiplier can fix that: the table redistributes inside a country and the country is the problem. Population is worse — Tibet reads 112 against Henan's 16, and population feeds the manpower ceiling and the unrest load. **Both are left alone deliberately**: fixing either moves the `nation` section of the seed fingerprint, and this change was licensed to move `regions`. They are the obvious next economic pass. |
| 85 | **The public works were left as flat adjustments, and that turns out to be the feature.** Rail is +3.0 development wherever it is laid. | Measured on the real map: rail in Silesia is 6.89 → 9.89 (+44%); the same 45 treasury in East Prussia is 1.32 → 4.32 (**+227%**). A flat gain is proportionally worth four times more to poor ground, which is exactly the answer to "underdeveloped conquered land should be worth investing in" — and it needed no new mechanic, only a development number that was not the same everywhere. Noted rather than tuned: over a very long game, building everywhere flattens the geography back out. That wants a played game to judge, not a guess. |
| 84 | **Development goes down as well as up** (`budget.erode_development`), driven by unrest, sanctions and — from the air layer — bombing. | Development was a ratchet: five public works raised it and nothing ever lowered it, so "develop your land" was a strategy with no downside to weigh. The erosion reads `unrest.held_foreign_provinces`, which is the *residual after garrison relief* — so a garrison on restive ground protects its value as well as its holder's stability, and that incentive needed no second mechanic to express. It is deliberately the *slow* half: bombing writes its diffs at the moment the raid lands. Sanctions are worth a fifth of what unrest is, because the same measure already bites economy and treasury directly and a third large term on one cause is one punishment counted three times. Measured: a French-held Saarland goes 5.65 → 4.89 over six ungarrisoned turns and does not move at all over the six after a division arrives. |
| 83 | **Ground you hold but do not own yields 40%, and the economy's ceiling reads ground as well as industry** (`budget.ground_value`, `_OCCUPIED_YIELD`, `_ECONOMY_FROM_DEVELOPMENT`). | Two gaps that were really one: conquest was instantly and fully profitable, and the two halves of the economic game were strangers — building factories compounded through the economy ceiling and developing provinces did not, so a player who developed land got cash and no growth. Now both feed it, and the pair is calibrated to reproduce the old seed equilibrium exactly (`0.6 × 1.4 + 0.55 × 0.43` = the 1.08 that `0.6 × 1.8` gave), so nothing about turn one moved and everything about what a turn *can do* did. The consequence worth naming: **losing provinces now shrinks your economy**, not just your income — losing the Ruhr is a country that is smaller in two years. One function computes ground value for both callers, because two copies of "what is this worth" drift. |
| 82 | **`region.development` is seeded from economic geography** (new `scenarios/development.py`; schema unchanged; **the `regions` fingerprint section of both eras moves, and only that section**). A nation's provinces total 0.55 of its economy, distributed by a hand-authored table of named heartlands. | It was 1.0 for every province of every scenario since the first commit, which made three things quietly untrue. The ground half of revenue *was the province count*: measured, Italy on an economy of 36 drew 7.50 of it and Germany on 66 drew 2.50, because Italy holds Eritrea and Somalia — the term rewarded holding large amounts of poor land, the exact opposite of its own docstring. Map granularity leaked into the economy: the United States is one region in both eras and drew 0.25 a turn. And every public work bought the same revenue in the Ruhr as in Turkmenistan. The table is hand-authored without apology — no dataset in the repo knows the Ruhr from the Masurian Lakes, and land area, the one geographic quantity available, would have made Siberia the richest ground in the Soviet Union. Multipliers are **relative to the holder's average province**, so the table says "Silesia is worth three ordinary German provinces" and stays true if a scenario re-tunes its economy figures; normalising per nation means it redistributes wealth inside a country and never invents any. Colonial ground is detected structurally from the `r_m_{code}_` id convention rather than guessed — without that, nineteen provinces of the Horn of Africa outvote metropolitan Italy for a share of the Italian economy, which is how conquering a desert makes Milan poorer. A test validates every id in the table against both real maps, because a table of ids nobody checks goes stale silently. |
| 81 | **Three calibrations the playtest forced, all of them measured rather than guessed.** Strike weight divided by 12 rather than 25; the development floor lowered from 1.0 to 0.5; a bombed formation floored at 0.1 strength. | Each was a number that looked reasonable and was wrong against the real maps. (a) Measured across every nation that owns aircraft, projected air strength runs median 4.5 in 1936 and 9.3 in 2026; at a divisor of 25 six French raids on Baden-Wurttemberg took **0.3 strength off a 23.6 garrison**, which is not a bombing campaign, it is weather. (b) **Every region on both maps is seeded at development exactly 1.0**, so a floor of 1.0 made the `infrastructure` aim a guaranteed no-op on every province in both eras until somebody built there -- the aim now bites a little on virgin ground and still cannot erase a province. (c) Halving a garrison approaches zero without reaching it in arithmetic, but twelve raids on a 1.3-strength French division rounded it to **exactly 0.0**: a unit still on the map, still counted as a formation, worth nothing. Air power is not allowed to remove a division from the order of battle. All three pinned by tests. |
| 80 | **One interception term in the engine, and the three air technologies now read it at resolution time instead of being paid out at completion** (`air.air_defence_over`, `INTERCEPT_TECHS`, `PENETRATION_TECHS`). `integrated_air_defence` loses *all* of its `TechEffect`s. | `nuclear.penetration_odds` had its own copy, written with a note saying the air layer should take it over. Taking it over corrected three things at once: air defence one province away was worth nothing (interceptors were the only thing in the game with no range), a wing with no morale left defended as well as a fresh one, and `radar` counted for nothing in 1936 so an early-era warhead met only the defence the 2026 catalogue names. The technologies themselves were worse than inert, they were *backwards*: `radar` and `integrated_air_defence` were `base_strength` bumps on an air wing, i.e. they helped you **attack**, and `hypersonic_strike` -- "too fast to intercept" -- was read by nothing at all and bought a destroyer a better gun. IAD now carries no template effect whatever and is purely an interception term, which required teaching the era-catalogue guard that a technology may buy a runtime effect (`RUNTIME_TECHS`) rather than weakening it. |
| 79 | **A strike is a bespoke gambit kind, not a fourth `military_objective.kind`** (`air_strike`, `_BESPOKE`), with a closed two-word aim vocabulary and every magnitude engine-chosen. | The design note flagged this fork and recommended this arm of it: `military_objective.kind` is a closed CHECK enum and widening it is save-invalidating, while the gambit channel needs no schema change and brings seeded adjudication, affordability, refusal-before-the-roll and the per-turn cap with it. The `_BESPOKE` dispatch was built for one entry with a comment naming an air strike as the second; this is that. The proposer picks a region and one of `forces` / `infrastructure`; it does not pick a magnitude, and the caps are in `air.STRIKE_CAPS`. Role.GAMBIT cannot write `unit.strength` or `construction.days_remaining` at all, which is exactly why this had to be engine-applied rather than verb-driven. |
| 78 | **Reach: `unit_template.move_range` is finally read, and air is the only thing in the engine that goes further than one hop or ignores what it is flying over.** | The column has said 3 for an air wing against 1 for infantry since the first commit and there was not a single `SELECT` of it anywhere in `src/`. Every other reach in the engine is exactly one hop, with two hand-written naval exceptions. So this is the arm's whole mechanical identity: a breadth-first walk to `move_range`, with strength falling off 0.72 per hop, and `region_adjacency.crossing` deliberately not consulted -- an aeroplane does not care whether the border below it is a river, a coastline or a front line, and that indifference is the reason the arm is worth modelling separately. Measured consequence on the 1936 map: Britain projects air over provinces it does not hold and could not otherwise reach without transports. Carriers project air too, at 0.6, because the most expensive hull in either era exists to carry aircraft and a layer that ignored that would leave it contributing nothing to the one thing it is for. |
| 77 | **Aircraft no longer count toward the strength of a land or naval battle** (`sim/resolve.py::_strength`, `= 'land'` where it was `<> 'sea'`), and are excluded from the three gatherers that charge losses and walk onto captured ground. | The single line that made air data rather than a mechanic. An air wing was summed into a land battle at full weight, with no range, no way to lose that did not involve being overrun, and nothing air-like about it: it was the cheapest infantry in the game. This is the trade the whole layer rests on -- an air force stops padding a total and starts deciding how well the total does. Bounded by measurement rather than hope: air is 2.3% of all military strength in 1936 and 5.8% in 2026, so removing it perturbs existing balance slightly and the superiority term gives it back with interest to whoever wins the sky. It did move one existing pacing test (France over Germany went from 14 turns to 25 before decision 82's margin fix and back inside the documented 4-16 band after), which is the intended kind of change. |
| 76 | **Air superiority is a separate seeded contest over the same ground, resolved before anything on it moves, and its outcome is persistent state** (new `air_control` table, schema 11 -> 12, additive, no backfill). Margin is taken from the **force ratio, not the roll**. | The nuclear and organisation layers both bought their spine by reusing a `nation` row; this one could not -- a sky is not an actor, it is a property of a province -- so it is the first genuinely new state in a while, and it is one additive table keyed `(world_id, region_id)`. **Engine-driven and unprompted**, like the front line marching: an air campaign is what having an air force *means*, not a decision a staff makes each month, and routing it through an intent channel would have made it something only the player and five tracked nations ever did. Fought once per turn rather than once per round, because a season of fighting should not change hands in the air four times. The margin rebasing is the interesting correction: taken from the roll it measured *luck*, so against a 3.5-to-1 Luftwaffe every French roll was far from the odds and the margin pinned at 0.96 -- the modifier was effectively binary. Taken from the ratio it is graded, and it gains a property worth having: **a weaker air force that wins the day gets no control at all**, having contested the sky rather than seized it, which is what an upset in the air actually is. |
| 75 | **A sanction's drag is a share of an economy, not a fixed subtraction off it** (`organisation.py`, 3%/month of economy and 6% of treasury per severity, floored at 1.0 economy). | The first cut subtracted a flat 0.9 a month, and the Modern Day playtest showed what that means: Belarus went 4.3 -> the floor in six turns, a 77% collapse, while the identical resolution against the United States would have moved 100.0 to 94.6. An absolute drag is regressive, which is both the wrong simulation and the wrong game -- it makes the Council devastating against precisely the small powers a great-power veto already leaves it free to punish, and harmless against everyone else. Rates fix it: measured, the same resolution now takes 17% off both. Pinned by `test_a_sanction_weighs_the_same_on_a_small_power_as_on_a_large_one`. |
| 74 | **Seeded the UN Security Council only -- NATO stays a `defensive_pact` and is not also seeded as a convening body**, though the `kind='alliance'` arm of the schema is kept for a bloc that wants it. | Alex approved modelling NATO both ways but asked for judgement if it produced redundancy, and it does. Decision 71 already makes an attack on a NATO member hand every other member a `casus_belli` automatically. A NATO organisation's strongest available action is `authorise_force`, which hands every member a `casus_belli`. So one attack would fire the same consequence twice -- once for free and once through a model call and a vote that can only add nothing or, worse, *fail*, leaving a treaty that just worked looking like it had not. Two mechanisms on one treaty is also two things to keep in step. The UNSC is the body worth having because it does something no pact does: it judges a war nobody signed a treaty about. |
| 73 | **The Council's jurisdiction is read from the diff log and the causal spine, not threaded through the call sites** (`organisation.py::triggers_this_turn`). | A nuclear use is detected as an applied negative adjust on `arsenal.warheads` -- warheads only ever leave an arsenal by being fired, so the diff log is an unspoofable record of a launch that no narration can fake and no caller can forget to report. An unprovoked war reuses `_has_casus_belli`, the same test `_declare_war` already used to decide whether to charge the aggressor's legitimacy, so the body's jurisdiction and the engine's own judgement of provocation cannot drift apart. The same trick answers "who was defied?" six turns later: the session cites its trigger through `event_cause`, and the trigger's actor is the accused, so the authority loop needed no new column. |
| 72 | **An international organisation is a `nation` row it cannot use, plus three additive tables (schema 10 -> 11)**, and the engine decides what a resolution does -- the body only picks from a closed vocabulary. | The same representation choice the nuclear layer made, for the same reason: a `nation` row buys `event.actor_nation_id`, a directed relation to all 209 powers, diffs, visibility and exact rewind without building any of it, and the cost is paid in explicit guards rather than in a parallel spine. It is excluded from the roster, from Focus candidacy, from the master AI's investment pass and from war declaration; it holds no ground, no units and no capital. The vocabulary is `condemn` / `sanction` / `demand_ceasefire` / `authorise_force`, and every magnitude is engine-chosen -- the nuclear-launch discipline, not the gambit-verb one, because a resolution's consequences should not be negotiable by whoever drafts it. Nothing in it reaches territory, units, contests or war status: a Council can make a war expensive, lonely and legitimate to oppose; it cannot end one by saying so. |
| 71 | **Article 5 is prompted, not automatic: attacking a member of an active `defensive_pact` hands every other member an engine-authored `casus_belli` and collapses their regard for the aggressor** (`resolution.py::_invoke_defensive_pacts`). Nobody is declared into a war. | The enum member `CommitmentKind.DEFENSIVE_PACT` appeared **exactly once in the codebase -- its own definition**. Modern Day had seeded NATO with Article 5's text in `condition_text` since the era was written, and attacking Germany drew in nobody. The choice was auto-join or casus belli, and casus belli is both truer and stronger than it looks: `_has_casus_belli` exempts its holder from `_UNPROVOKED_WAR_COST` for five turns and rule DIP-003 reads the same record, so **an ally that enters the war pays nothing while the aggressor has already paid 8 legitimacy**. That asymmetry is the deterrent, and it leaves the decision where every other decision to fight lives. A hard auto-join would drag five powers into a war none of them chose, which is not what a defensive pact is and not what happened the one time Article 5 was actually invoked -- members decided, and decided differently. Reuses an event kind the closed enum already carries: **no schema change, nothing save-invalidating.** Verified in PT26 on the real map: Russia attacks Germany, four allies are handed cause, allied regard falls 30 apiece (or clamps at -100), nobody is conscripted, and an ally that then joins pays zero legitimacy. It also composes symmetrically without being told to -- when the United States joined, the DPRK-Russia pact fired back the other way. |
| 70 | **A nation adjacent to a war it is not in is ranked for Focus** (`focus.py::wars_on_the_frontier`, +30 x standing plus 5 per front, capped at +10). | Every other term in `rank_candidates` asks what a nation is doing **to you** or to a tracked power. A country watching an invasion arrive at its frontier did none of those things, so it stayed in the referee's bulk pass -- where it can still act (world_sim may declare its wars and move its relations) but never in its own voice, with its own goals, against its own reading of events. That is precisely the nation with the most to say. Keyed on the **`contest` table, not `relation.at_war`**: a war fought somewhere else is not a war on your border, and what makes a country nervous is ground changing hands within sight. The cost is one turn of lag between a declaration and the first contest, which is the honest reading. Weighted above "borders your territory" (25) and far below "at war with you" (100) so a spectator can never outrank a belligerent, and scaled by `standing` like the other proximity terms so a microstate beside a great-power war does not get a dedicated agent for adjoining something it cannot affect. Computed once per ranking rather than per candidate -- the per-nation form is 240 adjacency joins on a full map. |
| 69 | **Survivability is derived from the delivery tier plus the boats actually on the map, not stored** (`nuclear.py::survivability`; `arsenal` carries two columns and no more). | The temptation was a third column — `survivable REAL` — and it would have been a number that stops being true the moment anything happens. Reading the map instead means **sinking a rival's submarines weakens its deterrent**, which turns an anti-submarine campaign into a strategic act rather than a naval one, and it is the only place `submarine` earns something no other hull earns. It also keeps the table honest about what it is: an arsenal is *how many* and *what kind*, and everything else about it is a fact about the world today. Measured on the real map: Britain retains 7 of 8 warheads through a first strike, North Korea 1 of 3 — the same distinction the real ones were built to settle, falling out of two columns and a `COUNT(*)`. |
| 68 | **Arsenals are seeded through `apply_politics`, not through the `WorldSeed`** (`Scenario.arsenals`). | Mechanically either would work; the guard decides it. `scratchpad/seed_fingerprint.py` hashes the `WorldSeed` dataclass *field by field*, so adding a field to it moves the whole-seed digest for **every** era — including 1936, which has no arsenals at all and in which nothing whatsoever would have changed. A guard that fires on a change it was built to prove innocent is a guard that gets ignored. Nuclear weapons are also political state on their merits: they sit with the relations, the treaties and the prior history, which is what `apply_politics` is for. Result: all three gathering-storm digests and all six per-section hashes are **byte-identical** to the commit before this work. (Noted while checking: the values quoted in the old handoff — `2ef8caea…`, `57a31c89…`, `296a0c76…` — were **stale**, predating decision 46's `build_days` change, which said in as many words that it moved them. The true current values are in the handoff above.) |
| 67 | **Deterrence keys on `home_nation_id`, is capped at 0.18 of pressure, and does not follow a nuclear power onto ground it occupies** (`nuclear.py::deterrence`, subtracted in `sim/resolve.py::compute_pressure`). | This is the part of the layer worth having even if nobody ever launches, and both restrictions are load-bearing in opposite directions. **The cap**, because the scenario that motivated all of this has a nuclear power invading a neighbour and being fought to a standstill by conventional means — a term with no ceiling would have quietly declared that the war on the map cannot happen. **Homeland only**, because keying it on who is *standing* there would have made every border a nuclear power ever crossed permanent, which is a far larger change to the game than "hard to invade" and nobody asked for it. Measured over ten monthly turns of the real Russia–Ukraine campaign: 9 Ukrainian provinces fall as the scenario ships, **3** against an otherwise identical Ukraine with a triad. Hard, not impossible, which is the whole target. |
| 66 | **The second strike is engine-authored, automatic, proportional, one exchange deep, and it fires whether or not the first strike got through** (`nuclear.py::second_strike`). | **The single most important rule in the design.** A first strike is only a winning move if the attacker gets to find out whether the defender would have dared; making the reply mechanical takes that question off the board entirely. Four details, each chosen against a plausible alternative: *automatic* rather than a model call, because a call is a chance to flinch and the deterrent is the certainty; *proportional* (never more warheads than were thrown), because a reply that escalates makes the arithmetic about the reply rather than about the decision; *one exchange deep*, because two nuclear powers trading until one runs out is a loop rather than a mechanic; and **fired even when the strike was intercepted**, because an intercepted warhead is still a warhead somebody fired at you, and the alternative turns a launch into a cheap roll that only bites when it works. A first strike does take with it the share of the target's arsenal that was never dispersed — which is exactly why a triad answers and a small land-based force may not. |
| 65 | **A nuclear launch is a gambit *kind*, and the one kind whose effects the engine chooses rather than the proposer** (`gambit.py::_BESPOKE`, `nuclear.py::NUCLEAR_CAPS`). | The frame was built for this and it fits: proposed by a model, refused before the roll if the world does not permit it, charged a stake, decided by a seeded draw, landed as diffs under `Role.ENGINE`. What changes is the effects stage, and it has to. **A launch reaches `region.population`, `region.development`, `unit.strength` and `unit.morale` — every one of which `ROLE_WRITABLE` refuses to `Role.GAMBIT`** — and the thing that makes that safe is not a cap, it is that *no wording of any proposal selects, changes or omits them*. A staff decides whether to fire and where; a warhead decides the rest. Decision 57's line survives intact and was worth re-testing at the sharpest kind on the list: a launch takes no ground, moves no formation, opens no contest and starts no war. **No new event kind** — it files under `battle`, which the closed CHECK enum already carries, so the enum was never widened. |
| 64 | **Nuclear weapons become state: schema 9 → 10, an additive `arsenal` table** (`migrations.py`, `schema.sql`, `engine/nuclear.py`). Backfilled from completed nuclear programmes; everybody else migrates to nothing. | Alex approved the schema change; this records the shape and its one honest limit. Two columns — `warheads` and a `delivery` tier — both `ENGINE_ONLY`, because a model that could write its own count has an instant-win button and one that could write *somebody else's* could disarm them by narration, which is the quieter and more total exploit. A table rather than columns on `nation` because the answer is "absent" for ~210 of 219 nations on any map. It is in `apply.PRIMARY_KEY` and `bootstrap._FINGERPRINT_TABLES` for the ordinary reason: warheads are spent, destroyed on the ground and fired back, and a rewind that restored the city but left the missile expended would have disarmed somebody retroactively. **The limit, stated because it will bite somebody:** a save written before schema 10 has no record of who was armed — Modern Day's nine nuclear powers were sentences in their character blurbs — so the backfill reads the only evidence that exists, a finished `atomic_programme` (3/aircraft) or `warhead_modernisation` (8/missile), and **an in-progress Modern Day save therefore migrates to a world where nobody is armed until somebody researches it.** The alternative is a migration that hardcodes one scenario's roster, and a migration that knows what era it is, is worse than a save that has to be restarted to get its warheads. |
| 63 | **The tracked-agent briefing states that an undertaking is paid from the purse the build orders are about to spend** (`views.py::_gambit_purse_lines`), quoting the nation's treasury and what it currently affords. | PT24. The build orders and the gambit are **separate model calls and the build one comes first**, so a staff commits its treasury to works and is then asked to gamble with the remainder, having had no way to know the question was coming. The existing "Undertakings you could attempt instead" section listed the costs but read as a footnote about a different subject; it never said *when* the choice arrives or that it competes for the same money. **A reserve was deliberately not imposed on `build_orders`** — the auto-builder's 1.4× rule was right there and extending it to agents was the obvious fix and the wrong one, because a power that would rather have the steelworks than the option is making a real decision and the engine has no business overruling it. What it lacked was an *informed* one. Decision 34's precedent exactly: the gauge, not the rebalance. |
| 62 | **The gambit channel rotates through the tracked powers by turn instead of slicing the first two off a name-ordered list** (`turn_engine.py::_run_gambits`). | `tracked_nations` is `ORDER BY name` and the channel took `[:2]`, so it asked **the same two alphabetically-first powers every turn for the life of the game**. PT23 is the proof: once Focus promoted China and Germany they held both slots from turn 2 onward and **Russia and Ukraine were never offered a gambit again** — the two belligerents, the two nations with the most to scheme about. **This is decision 59's bug in a different place**: that one sliced a name-ordered roster and offered Russia Andorra, this one slices a name-ordered roster and asks Germany forever, and both were invisible because a name-ordered list reads as data rather than as a choice. It also **manufactured the balance question PT24 was opened to answer** — the two powers it kept asking were mid-development and short of cash, which read as "nobody can afford a gambit" while the world at large was solvent throughout. The offset is the turn number, so the choice is deterministic, depends on nothing but the clock, and **rewinds exactly**: replaying turn 7 asks whoever turn 7 asked. Coverage over 8 turns went 2 powers → 6; watched treasuries went `3, 12, 3, 13, 6, 7` → `37, 53, 35, 78, 54, 78`. |
| 61 | **A refusal or a decline is reported out of the turn, not just counted** (`JumpReport.gambits_declined`, surfaced in `describe()`). | Written *before* PT23 because the run could not otherwise answer its own question. `report.gambits` held what happened, and `outcome.refused` — both the model's own "not this turn" and the engine's "you cannot afford it" — reached nothing outside `apply_gambit`. So a turn where both staffs judged the moment wrong was indistinguishable from a turn where the channel never ran, which is precisely the shape of the bug that hid a dead tracked-agent subsystem behind "applied" for a whole game (decision 5). It also made the channel's headline finding unmeasurable: **restraint is the behaviour under test**, and an instrument that records only attempts cannot see it. |
| 60 | **The briefing states which kinds need a war** (`gambit_rules_block`, generated from `GambitKind.requires`). | PT23: **two of the three proposals a real staff ever made** were spent aiming a war-only kind at a nation nobody was at war with — Ukraine's `incite` at Belarus, China's at India. The rules block listed every kind's blurb and never mentioned that `sabotage` and `incite` are closed in peacetime, so both models proposed in good faith and were refused. The engine's refusal message was already correct and already named the real limit; that is not worth much once the proposal is spent. Generated from `requires` rather than written out beside the table, for the decision-50 reason: a briefing that quietly disagrees with the engine is worse than one that says nothing. **Decision 11's pattern, for the fourth time** — and the cheapest possible instance of it, one clause. |
| 59 | **The gambit roster is ranked by who bears on your affairs, and quotes a real regard** (`engine/gambit.py::_prominence`; `other.relation_score` in place of `getattr(other, "score", 0)`). | Two bugs on one line, and together they made the channel unusable — **PT23 run 1 fired zero gambits in six turns and every model explained why.** `other_nations` returns the living world `ORDER BY n.name`, so `others[:12]` offered every nation Afghanistan-through-Armenia on every call, and **Russia, at war with Ukraine, was never offered Ukraine**; six declines named Andorra and Antarctica outright. The second is the more instructive: `NationBrief` has no `score` attribute, so the `getattr` default silently served a regard of **0.0 for every nation on every call**, and several staffs reasoned from it aloud ("no established relationship worth leveraging"). **Nothing raised, and 22 unit tests plus a full mock playtest missed it, because every one of them asserted on the rules block rather than the roster** — a defaulted wrong number is worse than a crash, so the fix uses direct attribute access to make a rename break loudly. Ranked war → border → treaty → `abs(regard)`, with the reason shown beside each name; `abs` because somebody you loathe is exactly as worth a gambit as somebody you court. |
| 56 | **One magnitude, one ceiling — a gambit's effect verbs are not individually scaled** (`engine/gambit.py`; the deleted `_FIELD_SCALE`). | Caught by a test that asked the obvious question — does a partial move less than a success — and got `6.0 == 6.0`. The first cut gave each column a scale factor (`score` 4x, because a point of relation is a smaller thing than a point of stability), which is defensible in isolation and wrong in composition: at 4x even a `minor` stake produced 1.5 x 4 = 6.0, exactly the per-turn per-field ceiling, so **every stake saturated the cap and the stake stopped meaning anything**. Paying 34 for `grave` bought precisely what 6 bought for `minor`. The fix is to delete the scaling rather than retune it: one magnitude, one ceiling, every column read the same way. A second knob whose only effect is to hide the first knob is not a knob. |
| 55 | **A new *channel*, not a new proposer role: the gambit is adjudicated, and the engine writes its outcome** (`engine/gambit.py`, `Role.GAMBIT`, `permissions.py`). Batches land under `Role.ENGINE`; `Role.GAMBIT` exists only to name a view builder and a write allowlist, and never appears as `diff_batch.proposer_role`. | The whole point of the channel is that a model proposes an *attempt* and does not author its result — so the thing writing to the world is the adjudicator, which is the engine, exactly as with battle resolution. Making `gambit` a proposer role would have meant widening a closed CHECK enum, which is **save-invalidating** and which Alex's brief said to stop and flag before doing. It also would have been a lie about who decided the outcome. So the role is a permissions and routing identity, `ROLE_WRITABLE` bounds it to four columns (`nation.stability`, `nation.legitimacy`, `nation.treasury`, `relation.score`), and **no schema change and no migration were needed** — schema stays at 9. Every one of the seven kinds maps onto an event kind the enum already carries (`DIPLOMATIC_MESSAGE`, `ENVOY_DISPATCHED`, `ESPIONAGE`, `SABOTAGE`, `UNREST`). |
| 57 | **Nothing a gambit does reaches ground, forces, or war status** — the allowlist stops at four soft columns, and a playtest asserts region ownership, unit strength and `at_war` are untouched across forty resolved attempts. | This is the line that keeps the channel from becoming the button Alex named: narrate a superpower into existence. A gambit can chill a relationship, shake a government, or drain a treasury; it cannot move a border, raise a division, or start a war — those stay with the resolver and the declaration channel, where a model proposes intent and dice decide. It is also what makes the frame safe to build *content* on later: a nuclear launch or a missile strike would be a new `GambitKind` with a stake, evidence, seeded odds and a bounded effect list, reusing the adjudication wholesale — the reason to add one is content, and it would still not get to write the map directly. |
| 58 | **The stake is charged win, lose or draw; only a refusal is free** (`apply_gambit` charges before it rolls). Refusals name the real limit and the real figure ("costs 34 and you have 4"). | Freeform's discipline, applied to a channel with dice in it. If failure were free the correct play is to attempt something every turn forever, and the channel becomes noise generation; charging up front makes attempting a *decision* with a cost, which is the only thing that makes choosing not to attempt a real move. The per-turn ledger is the **same dict** freeform uses, so the two channels cannot be stacked to double a nation's allowance in one turn — an audit of eight `grave` attempts in one turn moved the relation 6.0 total, the single-turn ceiling. And the refusal quotes the binding number for the decision-11 discoverability reason: a bound a model cannot see is a bound it burns proposals discovering. |
| 54 | **The validation caps scale with the turn's length, linearly rather than compounded** (`RuleContext.days`/`.span`, threaded through `validate` and `apply_batch`; `views.economy_write_rules(days)`). | PT20 finding 3, and the last piece of the pacing work: validation was the one place the span did not reach. `state/rules/economy.py` promised this in its opening docstring *in the initial commit* and no rule ever read `days`, so a week and a year shared one 20% bound and the year-jumper was held to a twelfth of the movement the month-jumper could have. Decision 50 sharpened it: once the caps are quoted to models, a year-long turn was stating a bound twelve times stricter than the one it would enforce — so the briefing became a function of the turn length too, and a test asserts the two agree at every span. **Linear is a deliberate choice against the obvious one.** Compounding is what the *quantity* does (twelve months of 50% treasury growth is 139x) and applying it to the *cap* gave a year-long bound of 13,801%, which is not a bound: a guardrail that switches itself off exactly when one decision covers the most time is worse than one that is slightly tight, because this rule exists to stop one proposal narrating a superpower into being, not to budget growth. Linear gives 243% military strength over a year against the 53% the engine's own budget produces. Twelve monthly turns therefore still permit more in total than one yearly turn, and that is not the asymmetry this work set out to remove — a staff making twelve decisions against twelve turns of feedback *should* achieve more than one making a single decision. That is what finer control is. |
| 53 | **Answer a misheard build order rather than asking again for what was already said** (`engine/orders.py::_closest`). Three failures told apart: nothing named, several possible, nothing this world builds — the last two quote the request back. | PT20 finding 6. China's order was refused with "was not told which public work or programme to begin"; China *had* been told, and the message diagnosed a silent order when the real fault was an unmatched one, handing back a bare list of ids with no clue which had nearly been named. The near-miss is deliberately conservative — shared whole words plus a five-character prefix, nothing fuzzier — because a confident wrong suggestion is worse than none: at edit distance "housing" and "shipyards" start looking related. It reaches what a staff writing prose actually produces ("semiconductor fabs", "hypersonics", "air defence", "welded hulls") and declines "build ships". Suggestions come from the *era's* catalogue, so 1936 is offered radar and 2026 is not. |
| 52 | **An objective the map overtook lapses, and the turn report says so** (`engine/objectives.py::prune_objectives`, run after resolution). | PT20 finding 5. Ukraine retook Sumy and the month after, both sides were refused over it — Russia's `hold` on ground it no longer held, Ukraine's `push` into ground it now owned. Neither was a model's mistake: both were declared against a map that then changed, and because the stale row stayed in `military_objective` the *briefing* kept showing it back and the staff kept faithfully restating it. Pruning at the source stops the loop; reporting it stops a staff discovering the lapse in next month's rejection list. Written as diffs so a rewind restores the orders — pinned. It drops the moot and nothing else, which is the whole risk: this file already carries a scar from the version that turned a bad declaration into a stand-down order. |
| 51 | **`partial` means the turn lost work, not that anything was refused** (`engine/turn_engine.py`: status keys on `rejected_batches`, not on the `rejections` list). | PT20 finding 4. Five turns out of five reported partial, one of them after applying six batches out of six — marked down because China was told a workshop was already under way. Most of what lands in `rejections` is the engine *working*: you already hold that, that is already under way, your forces cannot reach there. With five tracked agents nearly every turn contains one, so the word stopped distinguishing the case it exists for — a provider dying mid-turn with the resolved work kept. `rejected_batches` already counted exactly that. The same argument one level down is why a quiet tracked agent is not a failure. |
| 50 | **State the economic caps in the briefings, generated from the rule rather than restated beside it** (`views.ECONOMY_WRITE_RULES`, reading `SOFT_CAPS` and `REFORM_MULTIPLIER`). | PT20 finding 2, and the exact shape decision 21/22 fixed for relation writes (rejections 6 → 0). Turn 1 lost an entire world-sim batch to an undisclosed 20%/turn cap: the simulator roughly doubled both belligerents' `military_strength` in a month, ECON-001 refused it, and everything else in the batch went with it. The guardrail was right and the model had no way to know it existed. Generated from the constants so the briefing cannot drift from what is enforced — a briefing that quietly disagrees with the engine is worse than none, because the model then trusts it — and it names the escape hatch (`economic_reform`, which triples the cap), which a bare number would leave out. |
| 49 | **Report a recapture as a recapture** (`engine/resolution.py`): the occupation headline read `owner_nation_id` instead of the beaten defender, and a nation retaking its own province is now told it has retaken it. | PT20 found it and PT20 was the first run in six that could have. The headline said "Ukraine now occupies Sumy, which Russia still owns" — Sumy is Ukrainian core and Russia never owned it. Holder and owner are the same nation the first time ground changes hands, so five earlier real runs read correctly; they differ the moment a *defender* counter-attacks, which no previous run had ever done. The state was right throughout (occupation moves control only, as the comment three lines above says), so this was a reporting fault — but it is the report the player and every model read, and it told the liberator a foreign power owned its own ground. Fixed with a regression test checked against the old code, where it fails with the exact sentence from the run. |
| 48 | **Each era carries its own research catalogue on `Scenario.technologies`, and an effect that cannot land is an error rather than a no-op** (`engine/research.py::catalogue_for`, `engine/production.py`, `scenarios/modern_day.py::TECHNOLOGIES`). | The anachronism was the obvious half -- a 2026 player offered radio direction finding. The silent half was worse: two interwar effects name `battleship`, which Modern Day does not field, and delivery skipped a missing subject with a bare `continue`, so a completed programme could hand over two thirds of itself and report success. Radar in particular would have become an air-only technology, which is not a reduced version of gunnery radar but a different thing wearing its name. Gated in three places rather than one, because a catalogue split alone would have left the `continue` waiting for the next era: ordering out of era is refused *by name*, a missing subject at delivery is reported, and a parametrised test walks every registered era checking each effect names a lever shipped code reads and a kind that era builds. The test is the load-bearing one -- the interwar set fails it against Modern Day's templates and Modern Day's fails against 1936's, six effects to two, and that symmetry is the argument for a gate over a patch. Modern Day gains seven programmes on the same four levers, with semiconductors upstream of the tree because the scenario's own history says the export controls are about exactly that. No migration -- a catalogue is code, and `technology.tech_id` was already free text. |
| 47 | **Keep a push objective on enemy ground the front has not reached, instead of refusing it** (`engine/objectives.py`, new `ObjectiveReport.deferred`). | Fell out of the pacing work and is a pacing bug in disguise. Reachability is re-checked every round by `pushes_as_contests` anyway, so refusing at *declaration* time made a long jump strictly worse at prosecuting a war than the twelve short ones it replaces: a staff that said "take the country" once a year got the border provinces and then stood still, because everything behind them had been thrown away and there was no second call in which to name it. It is still *reported* -- in a third list rather than as a refusal -- because a nation that believes its army is already there is the confusion those messages exist to prevent. Ground held by nobody this power is fighting is refused exactly as before. |
| 46 | **`build_turns` becomes `build_days`, with schema 9 and the first data-carrying migration in this block** (Alex's call, taken as the clean option over driving builds from the round loop). | A turn is a week or a year; a day is a day. The conversion is thirty days a turn because every balance constant was tuned at monthly turns, so a save mid-build arrives at exactly the delivery date it was already heading for. `build_turns` and `turns_remaining` survive as dead columns and that is not an oversight -- the migration runner refuses DROP by design, and the schema-parity test requires a fresh world to match a migrated one column for column. Both are commented as superseded. **This is the one change in the whole block that moves the 1936 fingerprint**, so the fingerprint script now reports per *section*: nations, regions, adjacency, cities and all 442 units are byte-identical and only the six unit-template rows differ, which is the change and nothing else. |
| 45 | **Scale per-turn effects by `span = days/30`, with flows multiplying, rates and probabilities compounding, and the fighting split into monthly rounds** (`engine/pacing.py`). | The fix for the 81x spread decision 44 measured. The month is the reference unit because every constant was calibrated there, so the whole change is the identity at the default turn. Three classes, because getting the class wrong is silent: a rate multiplied instead of compounded overstates a year's growth by 38% and goes over 1.0 for any rate above 0.084; a probability multiplied stops being a probability. War got *rounds* rather than a scaled single step -- one model call buys one decision and the engine grinds out the months it covers -- because a year resolved on one dice roll is right in expectation, absurd in variance and illegible in the report. Two constants turned out to be per-turn quantities nobody had labelled as such: the "ignore drifts under 0.05" floor (which silently deleted a weekly player's economic growth -- 0.5 gained over two years where a monthly player gained 2.8) and the per-turn cap on a single round's advance. |
| 44 | **Do not scale anything with the jump length yet; measure it, write it down and ask.** (`scratchpad/pt_pacing.py`, and the DESIGN NOTE above.) | Alex raised it and it is real: the rail offers four spans and *no* simulation system reads `days`, so the same Russia-Ukraine war takes 0.2 years on weekly turns and 14.0 on yearly ones. It would have been easy to start multiplying constants by `days/30` -- and wrong three times over: compounding rates would overshoot (0.06 a month is 0.52 a year, not 0.72), per-turn *probabilities* would exceed 1.0, and scaling the war's advance without adding rounds would resolve a whole campaign on one dice roll. It also touches `construction.turns_remaining`, an INTEGER column, which is a schema change and a save migration -- the first in this whole block of work. That is Alex's call, not a call to make quietly at two in the morning. |
| 43 | **Chunk provinces into regions by proximity, not by a west-to-east sweep -- and only for the modern era** (`scripts/gen_territory.py`, `Era.clustering`). Merged regions are also named after their most central member rather than the seed. | The sweep orders on longitude alone, which is safe only on a country wider than it is tall. India is neither: it produced a region holding Jammu and Kashmir *and* Kerala, and Russia one holding Murmansk and Crimea. Neither is ground any front can cross, which is the same failure the 1936 note describes for France and Guyane -- the sweep just hides it better on a wide country. Per-era rather than global because 1936 sweeps over *native* groups that are already coherent, and changing it there would move the committed file for no gain. Naming followed: southern Russia was coming out as "Sevastopol" and southern India as "Lakshadweep", an island and an enclave standing for the mainland behind them, and region ids are what orders are written in. |
| 42 | **Promote eight countries in Modern Day, and make the territory generator take an `Era`** (`territory/modern-day.json`, `scripts/gen_territory.py`). Native region grouping is off for 2026. | PT17's finding: a country-level Ukraine falls in two turns, one region and one contest. Eight countries, four arguments, same rule as 1936 -- promote the ground a campaign crosses and leave the rest of the world one region per country. The generator's own docstring had already asked for this refactor. Native grouping had to become per-era because Natural Earth's `region` column is only usable where it is *finer* than the band we want: it is for 1936 (Romania's 42 counties) and the opposite for 2026 (China's 32 provinces collapse to six), and the merge can only coarsen. While there: regenerating no longer silently deletes the three hand-added East African nations, which the previous commit on that file was a warning comment about. |
| 41 | **Calibrate Modern Day's economies and fleets against the budget rather than against the news, and let Pakistan run a deficit** (`scenarios/modern_day.py`). `economy` reads as *mobilisation capacity*, not nominal GDP; escort and submarine counts sit at roughly a tenth of real hull numbers while carriers stay near-real. | The first pass used nominal GDP and near-real hull counts, looked entirely reasonable, and put **all sixteen powers into structural deficit** — North Korea bankrupt on turn 1, Russia on turn 6, Pakistan on turn 5. Cause: a modern hull costs 0.6/turn against 0.18 for a division, and a *country-level* power draws development revenue from one region where a promoted 1936 power draws it from twelve. The budget module's own note says it was calibrated twice against 1936 for exactly this failure ("you can never build anything and it is your fault"); Modern Day needed the same pass. Two changes, each defensible on its own terms: `economy` is what a state can mobilise (what the number *does* in this game is pay for divisions and fill yards, which is a purchasing-power question, not an exchange-rate one), and fleets came down because ninety American destroyers would be most of the units on the map. Carriers stayed near-real because there are only ~20 in the world and each one changes what its owner can attempt. Result: every power solvent at peace except **Pakistan at −0.4/turn with ~37 turns of reserves** — deliberately left, as the exact analogue of 1936's Japan (decision 34): a true fact about the country, escapable by industrialising, and now visible in the budget gauge. |
| 40 | **Sixteen powers in Modern Day, not the planned fourteen: Ukraine and Germany added, and Russia–Ukraine starts at war** (`scenarios/modern_day.py`). | Ukraine was not on the scoping list. A 2026 that has to pretend the largest land war in Europe since 1945 is not happening is not "the present with all its unresolved arguments", and the alternative — Ukraine as a generic one-division minor — would have Russia through it in a single contest, which teaches nothing and misrepresents the hardest-fighting army in Europe. Germany was added for the opposite reason: it is the largest European economy and the clearest example of the era's central tension (a rich state that chose not to be a military power, now reconsidering), and leaving it out would have made Europe read as Britain and France. Every other quarrel on the map is deliberately cold — competition nobody has declared is the era's whole character, and one live war is enough to make the war spine reachable from turn 1. |
| 39 | **A `Scenario` dataclass plus a `SCENARIOS` registry, replacing four hardcoded `preset.id == "gathering-storm"` branches** (`scenarios/seeder.py`, `scenarios/registry.py`). ~550 lines of era-agnostic seeding lifted out of `gathering_storm.py` (1269 → 545 lines), which becomes data plus a `SCENARIO` object plus two thin wrappers. | The scoping assessment's one real code cost. Four branches across `cli.py` and `ui/setup.py` decided how a world was built, who was playable and what a power's disposition said; each would have needed a new arm per era, and the Phase 0 bug was precisely what happens when one falls through to the generic path. Three things that were hardcoded 1936 *facts* inside the arithmetic — the armour table, the air table, the fleet order of battle — became fields. **Guard: the pre- and post-lift gathering-storm `WorldSeed` fingerprints were byte-identical across three player choices** (`scratchpad/seed_fingerprint.py`, kept), proving the refactor changed nothing. A preset with no scenario is now a first-class case rather than a fallthrough: it builds generically but keeps its own id, name and start date, so its era rules still reach the referee. |
| 38 | **Discover coasts on the full-detail outline (LOD 0) rather than the simplified one (LOD 1)** (`scenarios/earth.py::derive_coast_adjacency`). Costs 0.06s on the country pack and 0.43s on the province pack, once, at world creation. | Found by Modern Day, not by looking: Israel declared five hulls and received none, silently, with nothing in the seed to suggest anything was missing. Cause: a simplification that moves a coastline a few kilometres is invisible on screen and decisive here — LOD 1 straightened Israel's hundred-mile Mediterranean shore out of existence, along with three other countries' and **288 provinces'**, and a country the game thinks is landlocked cannot base a fleet. Committed on its own because it changes 1936: sixteen more sea-to-coast borders (1264 → 1280), same 219 nations / 488 regions / 442 units, strictly additive, no fleet moved. **The gathering-storm fingerprints moved deliberately here for the first time** — a821e221→2ef8caea, f7d2ce41→57a31c89, 0dd5bd0e→296a0c76. |
| 37 | **Marine shapes never become nations; `earth_seed` takes the scenario's id, name and start date** (`scenarios/earth.py`). | A live bug, not a refactor. Selecting Modern Day routed to `earth_seed`, which makes a nation of every entry in the pack it is handed — and the pack a scenario opens carries the marine layer. Reproduced exactly: 355 "nations", **113 of them open water** (the Arctic Ocean with a government, a capital city and a standing army), zero sea regions and therefore no navy at all, and `preset_id` written as the *pack* id (`admin0-50m+marine-10m`), which loads no preset — so the era's lore and its hard constraints ("nuclear weapons exist and their use is close to unthinkable") never reached the referee. Modern Day was unplayable in three ways at once. Water now routes to the same unowned `kind='ocean'` regions 1936 builds. |
| 36 | **`transport` stays the name of amphibious lift in every era, however the era names its other ships** (`scenarios/seeder.py::ShipClass`). | Modern Day fields destroyers and submarines and no battleships, which needed fleet composition to become data (`ShipClass`) rather than three hardcoded kinds. But `engine/naval.py::sea_lift` looks up `kind = 'transport'` by name, so an era that renamed its amphibious ships would keep every other naval mechanic and *silently lose the ability to land an army anywhere* — the same class of invisible failure as the missing coast. Rather than generalise the engine for one caller, the constraint is stated where a scenario author will read it (the `ShipClass` docstring) and pinned by a test. 1936 keeps the classic three unchanged. |
| 35 | **Nuclear weapons stay latency in Modern Day: nine nuclear powers, no warhead in any order of battle** (`scenarios/modern_day.py`). Deferred, flagged for Alex. | The scoping plan's Phase 5, and the answer for now is "not yet". The preset's hard constraints already tell the referee that use is close to unthinkable and that any move toward it is a catastrophic escalation, which is a real constraint the simulation reads every turn. A deterrence/MAD layer is a substantial mechanic — escalation ladders, second-strike, signalling — and building it before a playtest has shown the era *needs* it would be inventing a requirement. **Open for Alex:** whether a Modern Day playthrough feels wrong without it. |
| 34 | **Show the per-turn budget (revenue/upkeep/net) in the advisor and tracked-agent briefings** (`engine/views.py::_budget_line`, one line in each "Your position" block, reusing `budget.nation_budget`). A negative net carries a plain-English warning naming the two levers (build the economy, or stand down forces). | **PT15 finding, the general case of the sharp one:** the briefings showed the treasury as a bare number but never whether it was *rising or falling*, so a player could not tell they were bleeding until it drained. The sharp case: **Japan starts structurally underwater** (net −2.3/turn — its 18-ship fleet's upkeep against a low economy of 42), the *only* great power not "six to eight a turn ahead" as the budget's own calibration note claims — and drifted into arrears by turn ~18 in the do-nothing run with nothing in the briefing to say so. A follow-up smart-builder run confirmed the deficit is **escapable, not a trap**: one early workshop lifts the industry→economy ceiling, the economy drifts up passively (42→51), revenue climbs 11.0→13.4 and net reaches +0.1 by turn 24 without ever hitting arrears — so the right fix is the *gauge that makes the exit legible*, not a rebalance. Reuses the one budget helper (quoted net can't drift from what's charged); view-only, additive. |
| 33 | **Name what is already under way in the build menu** (`engine/views.py::_buildable_lines`: an "Already under way" list of each in-progress work with its region and turns-remaining, shown above the options and even when every slot is full). | **PT15 finding:** the menu headed "1 of 2 slots working" but never said *what* occupied the slot or when it freed up, so a player watching a build had no gauge of it. Shared with the tracked-agent view, so an AI can see its own queue and avoid re-ordering a duplicate (which the "one work per region per kind" rule would refuse anyway). Additive, view-only. |
| 32 | **Thin routine construction out of every briefing** (`engine/views.py::_thin_construction` + `_construction_footnote`, applied to all four event-rendering views — world-sim, diplomacy, tracked-agent, advisor). The viewer's *own* construction and every non-construction event are kept; other nations' builds collapse into a single count line. | **PT15 finding, and a real-run cost, not just polish:** the master AI (`production.autonomous_investment`) develops every unattended nation each turn, so on a full map a raw event feed is mostly near-identical "<nation> begins/completes <public work>" lines — dozens a turn. In the Japan mock the player's "what your ministry knows" was ~25 workshop lines burying its own two actions; worse, the referee's `all_events` window is **capped at 120**, so on a busy turn real wars and treaties can be crowded clean off the end. The events are untouched in the DB and in `visible_event_ids` — still recorded, still citable by id — so this thins the *briefing*, not the history: a legibility win for the player and a token/crowding win for every real-run briefing. |
| 31 | **Fix "comparable than you" in the rival-strength briefings** (`engine/views.py::_strength_clause`). The strength bands from `visibility._band` are single words tuned to follow "… than you", but "comparable" and "unknown" don't take that suffix. | **PT15 finding:** a rival who is a close match read as "comparable than you" (and an ungauged one "unknown than you") in the advisor and tracked-agent briefings — plain ungrammatical output in the player's face. The helper renders each band as a full clause; the four that read naturally keep the suffix, the two that didn't carry their own phrasing ("a close match for you", "of strength your ministry cannot gauge"). View-only. |
| 30 | **Show each event's visibility scope in the model briefings** (`state/visibility.py::known_events` now returns `visibility`; `engine/views.py` renders it as `(public/bilateral/secret)` on every event line in the tracked-agent *and* diplomacy views — the world-sim view already showed it via `referee.all_events`). | **PT14's commonest tracked-agent rejection** was INFO-002: a `relation.score` change marked more widely seen than the event it cited as its cause. Decision 21 already *told* models the rule ("a change may not be more widely seen than its cause"), but the briefing listed each event's id and text without its **scope**, so a model citing a bilateral event it could see had no way to know it was bilateral and guessed `public`. This closes the loop — the decision-11/21/24/29 discoverability pattern applied to INFO-002's missing input. The player-facing advisor is left unchanged (scope labels are model plumbing, noise for a human). View-side only; no schema/engine/write-path change; mock-verified (the tracked-agent view now shows `(public)` on the genesis event). Whether it cuts the churn needs the next real run to confirm, like decision 21 did for the bounds. |
| 29 | **Tell the models the valid `treaty_status` set** (`engine/views.py::RELATION_WRITE_RULES`, derived from the `TreatyStatus` enum so it cannot drift from the CHECK). The world-sim and tracked-agent briefings now name the closed set `none/non_aggression/trade/defensive/alliance/vassal` and warn that anything else takes the whole batch down. | **PT13 real-model finding:** a tracked agent proposed a `relation.treaty_status` outside the closed enum, failing the DB CHECK and losing its *entire atomic batch* — the same class of wasted output decisions 21 (relation bounds) and 24 (event ids) fixed, applied to the last unstated closed set a diplomacy write touches. The briefings showed *current* treaty values but never the *legal* ones, so a model reaching for "peace treaty" or "mutual defence" invented a value that could not apply. View-side only; no schema/engine/write-path change; mock-verified (both briefings name the set). Whether it cuts the churn needs the next real run to confirm, like decision 21 did for the bounds. |
| 28 | **Italy owns its East-African colonies** (`gathering_storm.py`: `COLONIAL_HOLDINGS = {m_eri: (ita,5), m_som: (ita,3)}`, a colony branch in the promoted-minor loop, and `_colonial_garrison`). A promoted minor named here has its ground *and garrison* assigned to the colonial power instead of an independent nation; each colony province begins as the owner's own core (`home_nation_id = ita`), and the garrison is stationed on the colony's own hub (the metropole is across the sea, so `_garrisoned`/`_order_of_battle` from Rome would never reach it). Eritrea (5 divisions) and Italian Somaliland (3) are Italian; British Somaliland stays the separate independent `m_sol`. | The scenario half of the minor-promotion work Alex green-lit: promotion made the ground province-granular, but the flagship war stayed unfightable because Italy — landlocked Ethiopia's only historical enemy — had no land border with it. This gives Italy that border (11 land-province pairs, both the Eritrean and Ogaden fronts) so the Abyssinian war is prosecutable by advance. Chose the **seed-level flip that keeps the two minors' province geometry** (the reversible option I recommended at the last handoff) over a territory-file recut: `COLONIAL_HOLDINGS` is one dict, trivially emptied to restore independent minors (a test pins that path). Colony provinces as Italian *core* (not occupation) is right for a colony held since 1890 — no start-of-game unrest — while later Ethiopian conquests are still foreign core and do drag. Validated in mock: Italy overruns Ethiopia's 11 provinces in sequence over ~6 turns through the real `resolve_turn`, and stays solvent (colonies add revenue that offsets the colonial army's upkeep, net +9.5/turn; no nation hits arrears). Seed-only; no schema/migration; old saves load unchanged (only new worlds get colonies); rewind-safe. |
| 27 | **Minor-nation province promotion** (`gathering_storm.py`: `_minor_power` / `_minor_nation_seed` + a promoted-minor loop; `territory/gathering-storm.json`: `m_eri`, `m_som`, and `ERI`/`SOM` added to `countries`). Promotion was power-only; the seeder now reads a territory nation the POWERS roster does not know as a *minor* to promote, drawing it at province granularity through the identical machinery — province regions from the same groups, defence spread by the same `_order_of_battle` / `_garrisoned` path — while keeping a neutral minor's formulaic strength (colour, military, economy unchanged from the country-level minor: promotion is geometry, not a stat buff). Eritrea (6 provinces) and Somalia (13) join Ethiopia; both border it province-to-province. | **Roadmap item #5**, and it closes the live-run gap that a single-region minor can only be taken in one siege. Chose **eager promotion of the scenario-relevant East-African cluster** over lazy-on-first-contact: no new per-turn machinery, and the ground a war is actually fought across is the ground worth the region count. Reuses the PackSet / territory-file / derive-adjacency machinery wholesale — an extension, not new architecture. Coastal minors touch only ocean and their real land neighbours (no cross-ocean stitching — the bug hit before). `home_nation_id` is seeded per region, so the occupation/unrest/partisan spine already works against a promoted minor. Country-level and promoted minors share one `_minor_power` definition, so the two can never drift on what a minor is. No schema/migration change; additive; old saves untouched; the affordance is inert until the file names a non-power. Validated in mock end-to-end: Eritrea's six provinces fall to a determined invader **in sequence** through the real `resolve_turn` (a turn exists where the attacker holds some and not the rest) — the campaign, not just the geometry. Region counts stay in band (Somalia 13, at the sov ceiling; Eritrea 6). |
| 26 | **Promote Ethiopia to provinces** (`territory/gathering-storm.json`: `eth` + `ETH` in `countries`). Ethiopia is a *power* on the 1936 map (a weak one, military 8), and the scenario opens with Italy at war with it — but as one region that war could only be decided in a single siege. Adding `eth` to the territory file promotes it through the *existing* power-promotion path with **zero code change**: 11 connected provinces, capital Addis Ababa, bordering its country-level neighbours (Eritrea, Somalia, Kenya, Sudan, Djibouti, ...) through the shared province pack. | The smallest safe first increment of roadmap #5: Ethiopia's fightability is unblocked by a pure *data* addition, before any code generalisation (decision 27 built that on top). Landlocked (no fleet), units on its own soil, region count 11 (in the 7–13 power band). The country-level pack alone still builds the old one-region Ethiopia, so the change is decided entirely by the geometry available — nothing else on the map moved. |
| 25 | **War-progress panel for the player** (`engine/views.py::_war_progress_lines`, added to `build_advisor_view`). The player's advisor now shows, for each war, the contested provinces with how far each siege has come (pressure %), and attacks across water called out as landings — the same front briefing a tracked agent gets. With the occupation block and the sue-for-peace section already in the advisor, the three together are the war-progress panel content, player-facing. | The oldest standing finding, open since **PT1**: *"no feedback on why an offensive is stalling."* The pressure data existed (the `contest` table, `visibility.fronts_of`) and every tracked agent was briefed on its fronts, but the human's council showed only *who* it was at war with — never the pressure on any province — so a player watching an attack go nowhere had no gauge. This is the gauge. View-only, additive, reuses the shared `fronts_of`; empty at peace. The roadmap's "war-progress UI panel" as its testable data/text core in the player's information surface — the PySide6 GUI widget (a 873-line custom-widget rail, unrunnable/untestable from here) can consume the same helper and is left as a follow-up rather than wired blind. |
| 24 | **reason_event_id discoverability: give models a citable event of last resort** (`engine/views.py::_founding_event_id`, injected into the world-sim and tracked-agent citation instructions). Each briefing now names the world's genesis event id as an always-valid fallback and warns that an invented id is rejected and takes its whole diff down; the world-sim instruction also stops implying a model can cite an event it is creating this turn (it cannot know the id — the exact fabrication being fixed). | PT10's separate real-model stumble: models cite **nonexistent `reason_event_id`s** (~5–6 rejections/run), because CORE-001 requires every diff to cite an existing event and a change with no clean recent cause (growing your own economy, a routine adjustment) has nothing obvious to point at — so the model invents an id and loses the diff. The decision-11/21 discoverability pattern applied to CORE-001: surface a true, general cause (genesis is engine-authored, public, dated turn 0, so it passes CORE-001 and INFO-001/002 for *any* proposer and *any* change) rather than let the model fabricate a specific one. View-only; no schema/engine/write-path change. Mock-verified (both briefings name the id and warn; a batch citing it applies cleanly — a guard against advertising an id the rules would then reject). Whether it actually cuts the churn needs a real run to confirm, like decision 21 did for the relation bounds. |
| 23 | **Sue-for-peace nudge: surface a settled occupation to whoever can end the war** (`engine/resolution.py::settleable_wars`, injected into the world-sim and advisor briefings in `engine/views.py`). A new read-only helper finds every province held by an occupier still *at war* with its owner — the exact condition a cession may ratify — and the referee briefing is told to bring such a war to a `peace` that cedes the held ground, while the advisor tells the player it can `sue_for_peace` to annex what it holds. | The clearest behavioural gap the real runs found (PT10): the peace-cession lever exists and is safe, but *nothing made a model reach for it* — Italy held Ethiopia's capital occupied for three turns and neither side ever proposed peace, so the war just ground on. The mechanism was invisible to whoever decides. This is the legibility fix (the decision-11/19 discoverability pattern) for the peace layer: name the ground already held and prompt a settlement. The **world-sim is the sole peace-author**, and its briefing lists *all* settleable wars world-wide, so it covers a tracked occupier's war too (a tracked agent has no peace channel and is deliberately not told to write one — that would only generate the rejection-churn PT9 warned of). View + one helper; no schema, engine, or write-path change; rewind-safe (pure read of current state). Validated end-to-end in **PT11**: a scripted occupier that reads the nudge now reaches the peace table through the real `TurnEngine` and annexes the ground. |
| 22 | **Clamp `relation.score` to its hard bound instead of rejecting the batch** (`state/apply.py::_BOUNDED_COLUMNS`, applied in `_apply_one`). A value past ±100 is clamped to the edge on write; only genuine hard numeric bounds are in the map, semantic caps (swing, visibility) stay rules. | The guidance (decision 21) cut the swing/visibility churn, but PT10 still showed CHECK-range overshoots: a model proposes +120 regard, the DB CHECK fails, and the whole *atomic* batch is dropped — taking the agent's other valid diffs with it. A hard bound is a value to clamp (+120 means "as friendly as it gets"), not a rule to reject. Mock-verifiable (clamp at both edges; in-range untouched); additive; reversible (one dict). |
| 21 | **Tell the models the relation-write bounds** (`engine/views.py::RELATION_WRITE_RULES`, injected into the world-sim and tracked-agent briefings): the −100..+100 range, the 25-per-turn swing cap and its dramatic-event exception, and the visibility-scope rule. | PT9's commonest wasted output by far was `relation.score` diffs the guardrails rejected (28 across three rules), because the briefings never stated the bounds — and an atomic batch loses its other diffs with the rejected one. The decision-11 discoverability pattern. **Validated in PT10:** the swing-cap (DIP-002) rejections went **6 → 0** and the visibility-scope (INFO-002) rejections **16 → ~6** versus PT9 — the guidance measurably cut the churn it targeted. View-side only, no engine/schema change. (Remaining: a few range-CHECK violations and a new "cited event does not exist" pattern, both separate follow-ups.) |
| 20 | **Tell overseas belligerents how to press their war** (`engine/views.py`). The tracked-agent briefing now shows the `military_objectives` lever to any nation *at war* (not only one with a land front), spells out that a `push` may be a landing across water, and surfaces the nation's own standing offensives ("offensives across the water") with the region id and pressure. | Found by the first real-model run (PT9): tracked belligerents never declared military objectives and their wars stalled. Root cause was a `if fronts:` gate on the army-orders instruction — a "front" is ground either side of a *border* with an enemy, so a war across water (Italy vs an Ethiopia it does not touch) produced no front and the agent was never shown it could attack. The world-sim's opening push then just decayed. View-side only, no engine/schema change, mock-verifiable (regression test: an at-war overseas belligerent sees the lever + its offensive; a nation at peace does not). Whether it actually makes AI wars progress needs the *next* real-model run to confirm — the affordance being visible at all is the fix. |
| 19 | **Surface occupied ground in the briefings** (`engine/unrest.py::held_foreign_provinces`, `engine/views.py::_occupation_lines`, injected into the advisor and tracked-agent views). Each restive province a nation holds is listed with its residual unrest and whether a garrison is holding it down. | The unrest layer (16–18) was mechanically complete but *invisible* to the decision-maker: a player only discovered the drag when stability had already cratered, and was never told a garrison answers it — a feedback loop with no gauge. This is the gauge. Empty for a nation on its own soil (most of them), and for an occupier a legible at-a-glance list of what is held-down, lightly-held, and at-risk-of-revolt. Given to the AI's own briefing too, so an AI occupier can choose to garrison rather than bleed. The math lives in one `unrest.py` helper so the briefing and the assessment can never quote different numbers. Pure additive view code; no schema, no engine change. |
| 18 | **Partisan rebellion: badly-held ground rises and reverts to its home nation** (`engine/unrest.py::assess_partisans`, wired into `turn_engine` before the drag). Each turn every restive province carries a *residual* load (base unrest less garrison less what stability suppresses), and that residual is a per-turn seeded chance the population rises; on success **control** (not ownership) reverts to the home nation. Chance caps at 0.25; only *living* home nations rise. | The increment that turns unrest from a *stat* cost into a *map* cost — the roadmap's second unrest increment. Chose **single-shot probabilistic over a pressure accumulator** (the `pushes_as_contests` model the handoff suggested): an accumulator needs persistent per-province state, which means either a schema bump or sharing the `contest` table — and sharing it collides with `_decay_unpressed`'s ordering. Single-shot needs *no new state*, rewinds as ordinary region diffs, and reads well thematically (uprisings are sporadic). Control-not-ownership keeps the engine-wide occupation/annexation split, so a peace still decides ownership. Living-home-only because reviving a wholly-conquered state is conquest's business, not a partisan's — flagged as a future increment. Validated PT8: bare occupation lost Veyran Plateau to a turn-6 uprising; a garrison stopped every rising. |
| 17 | **Garrison eases unrest: troops stationed on restive ground hold it down** (`engine/unrest.py`). Each point of the holder's unit strength in a restive province takes `0.3` off that province's load, floored at zero (a garrison neutralises unrest, never inverts it into a bonus). | The roadmap's first unrest increment and the guns-on-the-ground half of occupation: before this, the *only* way to hold ground cheaply was to be stable, so an occupier had no active lever. Now stationing an army holds a province directly. Calibrated so one land division (~4–7 strength) roughly neutralises one province — and garrison is a *finite* lever (you cannot station a division on every province of a continent), so the tension is where to put the troops you have. Per-province now, via a LEFT JOIN onto summed holder unit strength; additive, rewind-safe, no schema change. Validated PT8. |
| 16 | **Holding foreign ground costs stability** (`engine/unrest.py`), a per-turn engine drag scaling with how much foreign core a nation holds and eased — never fully — by its own stability. | Alex's steer for the layer after peace, and the counterweight it needs: the peace mechanic made annexing a continent *possible*, and without a cost it would be *free*, turning the map into a land-grab. A province whose holder is not its `home_nation_id` (occupied or annexed) is restive and drags. Calibrated on the real map so 3 provinces cost a great power ~7 stability over 20 turns and 15 cost ~42 (accelerating as stability falls — imperial overstretch), while a nation on its own ground pays nothing. Engine-authored, rewind-safe, additive; `home_nation_id` is what the peace work seeded to make this legible. **This is increment 1 (the drag) of a larger layer** — garrison-eases-unrest and partisan-contests-to-rebel are the natural next increments, left for the next session. |
| 15 | **Peace can transfer provinces** (`engine/resolution.py`, `state/diff.py`, `state/bootstrap.py`), closing the missing loop: a war could start and grind but never end with a territorial outcome. A `peace` now carries `cessions`; the engine transfers `owner`+`controller` together in the war-ending batch, relocates a ceded capital (CORE-005), and refuses to dissolve a nation outright. | Alex's steer for the next grand-strategy layer. **The safety line — a model must never decide a military outcome — is preserved by construction:** a cession may only *ratify an occupation the battlefield already produced* (province occupied now, both parties the two belligerents, going to one of them). A model cannot cede peaceful land, so it formalises a dice-drawn result rather than inventing one. `home_nation_id` (now seeded at world creation, not just backfilled by migration) is left untouched, so recovering your own core reads differently from annexing foreign ground. Propose-only through the existing `declarations` channel; no new write role; rewind-safe. Validated end-to-end (Playthrough 6). |
| 1 | **Added a per-turn budget** (revenue and upkeep) rather than raising starting treasuries. | Playtesting France showed the treasury was a one-shot budget: 80 at the start, 25 by turn 3, and nothing affordable ever again. Every economic decision in the game happened before turn four. A bigger starting pile delays that; income removes it. |
| 2 | **Upkeep is charged per formation, with ships costing ~3× a division.** | Income alone is free money. The tension the peaceful layer needs is "an army you are not using is an army you are paying for instead of building something". Naval upkeep being punitive is also just true of the 1930s. |
| 3 | **Recalibrated revenue/upkeep after the first pass bankrupted France.** First numbers gave her 9/turn against 15 of upkeep — permanent bankruptcy from turn 5. Now a great power at peace nets +6 to +8. | Replacing "you can never build anything" with "you can never build anything and it is your fault" is not an improvement. Calibrated against the real 1936 orders of battle: Britain's 27 warships leave her barely in the black, which is the position the Royal Navy actually put the Treasury in. |
| 4 | **A country that cannot pay stops at zero and loses standing**, rather than going into debt. | A negative treasury is a number nobody can act on. Arrears is a legible failure state, and it keeps the `UNIT-001` overdrawn check meaning what it says. |
| 6 | **A refusal now names the alternatives** rather than only the reason. | "The two do not border" is unanswerable when the player has never been shown what their provinces neighbour. The map knows; the message did not say. |
| 7 | **Carried resolution's headlines into the turn report** rather than inventing a new pressure summary. | The narration already existed and was thrown away. Building a second one would have been two things to keep in step. |
| 5 | **A quiet tracked nation is reported under its own heading, not as a failure.** Kept the signal, dropped the error. | Five powers with nothing to say produced five failure lines a turn and marked every turn of a peaceful world `PARTIAL`. The original diagnostic mattered — a dead tracked-agent subsystem hid behind "applied" for a whole game — so the report still says who was silent. It just is not an error, and it must not bury the real failures. |
| 11 | **The world-sim roster now prints each power's id** beside its name (`engine/views.py`), instead of matching on short codes. | `declare_war` on "gre" resolved to nothing (Greece is `m_grc`), and the name matcher is intentionally strict — the one time it was loosened it read "East Africa" as "East Timor" and invaded there. So the fix is discoverability, not fuzziness: a model that can see `[m_grc]` writes the exact id, which resolves on the first tier. Model-facing only; the human advisor's prose stays clean. Closes handoff finding 3. |
| 12 | **(Dispatch A) Halved the passive legitimacy drift to 0.03/turn** (`engine/budget.py`). | Alex's call on my flagged tuning question: the 0.05 rate closed ~70% of the gap to stability in two game-years, making legitimacy a laggy copy of stability. Gentle background now; the government's own legitimacy choices (health, schools, land reform, a freeform appeal) are meant to be the lever. Player/agent choices mattering more than autopilot is the better design. |
| 13 | **(Dispatch B, part 1) Tracked agents can now build**, via a `build_orders` intent channel (`state/diff.py`, `engine/turn_engine.py`, `engine/views.py`). | Alex's call: a world where only the human develops is a broken simulation. A per-country agent could direct its army (`military_objectives`) but not its economy, so it never built. `build_orders` is the economic twin — intent, not a write path; the engine runs each order through the same `order_project` the player uses, charging and queuing it. Ties into Atlas's per-country-agent identity: agents that build, not just fight. Propose-only, engine-charged, rewind-safe; no new write role (so the closed-CHECK-enum constraint is untouched). |
| 14 | **(Dispatch B, part 2) A master AI develops every unattended power** (`engine/production.py::autonomous_investment`). | The compounding loop needs a country to *build* to have anything to compound, and a minor has no model call. So the engine plays their treasuries: deterministic, cheap (SQL only, no model calls), and routed through the same `order_project` — steelworks for an industrial power, workshops to bootstrap a poor one, rail when cash-rich but industry-poor, always keeping a 1.4× reserve so nobody is spent into arrears. This is the "master AI for minors at whatever fidelity is affordable" Alex sanctioned; engine-driven is that affordable fidelity for 200 nations. |
| 10 | **Added a starter industry project, `workshops`** (20 treasury, 4 turns, +5 industry, scales with industry). | A full peaceful playthrough showed the economy-growth loop from decision 8 never *ignited* for a poor power: every industry-building project was priced for a great power (steelworks 68+ vs Italy's +8/turn income), so industry stayed frozen and the economy had nothing to compound. Workshops is the missing bottom rung — cheap enough that a poor power can start building capacity, and industry-scaled so it does not stay the great powers' tool. It turns "economy inert" into "economy climbs" (see Playthrough 4). Additive: one catalogue entry. |
| 9 | **The advisor now shows the full build menu with real costs** (`engine/views.py`), rather than naming five of seven works with no numbers. | Acting on the standing finding that nothing prompts a player with money and a free slot. The catalogue and tech tree already held every number; they were simply never put in front of the person deciding. Costs come from one `project_cost` helper shared with the order path, so the quoted price and the charged price cannot drift. No engine or schema change — a view addition. |
| 8 | **Economy and legitimacy now move every turn, driven by what the player built** (`engine/budget.py`). Economy climbs toward `industry × 1.8` at 6%/turn; legitimacy drifts toward stability at 5%/turn. | Acting on the standing Dispatch decision that the peaceful game should progress in a way the player can *feel*. Before this, economy/industry/legitimacy were step-functions that only fired when a project finished — twenty Italian turns moved none of them. A step-function reward you see once is not a feedback loop. Making economy compound off the industrial base turns a steelworks from a one-line bump into years of visible growth, and is the mechanism that makes "build a factory" pay. Playtest (3 profiles, 20 turns): a hoarder gains +2.6 economy / +6.4 legitimacy; one steelworks yields +17 economy over the run against its +12 industry; three yield +35. Meaningful, felt, and it rewards building without giving it away. **Industry itself was deliberately left investment-only** — economy is the compounding downstream, but factories still have to be chosen and built, or the core decision evaporates. Reversible: two constants and one loop body. |

---

## PLAYTEST LOG

### Playthrough 1 — France 1936, 16 turns, full stack

Player orders across the run: housing, welding research, a battleship, a steelworks,
a freeform order, public health, war on Germany at turn 8, then an offensive into the Saar.

**Findings, in order of severity:**

1. **No income anywhere in the engine.** `treasury` only ever went down. France spent to 25
   by turn 3 and could afford nothing for the remaining 13 turns. *Fixed — `engine/budget.py`.*
2. **Costs were calibrated against a treasury that never refilled**, so a battleship at 60
   against a starting 80 was most of the game's money. *Fixed by the same change; costs now
   read as 3–8 turns of saving.*
3. **"X returned nothing to act on" reported as a failure every single turn** for every
   tracked nation that had nothing to say. Real noise in the turn report, and it will spam
   the live game too. *Fixed — `tracked_quiet` on the report.*
4. **No feedback about why an offensive is failing.** The player attacked the Saar three
   turns running with one division and got "1 clash" each time and no explanation. Pressure
   is invisible from the turn report. *Fixed — the turn report was **discarding** every
   headline resolution wrote. They were there all along.*
5. **A redeploy order was refused for a border the player had no way to know about**
   ("your forces in Ile-de-France cannot reach Alsace-Lorraine"). Correct, but the player is
   never shown what their regions border. *Fixed — the refusal now names where they can go.*
6. `manpower` never moved in 16 turns. *Fixed — it now recovers toward what the population
   sustains.*

**After the fix:** France runs +7.7/turn, completes housing, welded construction and public
health, and ends turn 16 with 118 in the treasury and stability up from 42 to 50.5.

### Playthrough 2 — Italy 1936, 20 turns, a poorer power

Orders: steelworks, welding research, a freeform rally, housing, two transports, war on
Greece, carrier doctrine. Italy nets about +6/turn.

**Findings:**

1. **Partial fulfilment was silent.** Two transports ordered, one laid down, nothing said
   why. *Fixed — a short order now reports the shortfall, a refusal for money reports what
   it costs against what you have, and a full queue reports how full.*
2. **`declare_war` on "gre" matched no nation.** Greece is a minor, so its id is `m_grc`,
   and a three-letter code is too ambiguous for the fuzzy matcher. Full names resolve.
   *Open — low severity, but minor-nation ids are not discoverable.*
3. **Legitimacy, economy and industry never moved in 20 turns.** For a peaceful player
   only stability, treasury and manpower are live. Economy moves through
   `economic_investment` and two of the seven public works; legitimacy through almost
   nothing. *Open — the clearest remaining balance gap.*
4. **Costs bite small powers hard.** A steelworks at 68 is eleven turns of Italian saving
   for a twelve-turn payback. The industry cost scaling is doing its job at the top end;
   at the bottom it may want a floor. *Open.*
5. World solvency check across all 221 nations after 20 turns: **nobody stuck at zero.**
   The budget calibration holds for minors as well as great powers.

### Playthrough 3 — the peaceful-drift fix, isolated

Not a full game: an Italy-profiled nation (economy 54, industry 32, stability 42,
legitimacy 32) run through the budget pass alone for 20 turns, to calibrate the new
economy/legitimacy movement without other systems muddying it. Numbers in decision 8
above. Finding 3 from Playthrough 2 (economy/legitimacy/industry never moved) is now
addressed for economy and legitimacy; **industry** is still project/research-only by
design. The open question a full replay should answer: does the compounding make great
powers run away from minors over a long game, or does the budget's upkeep keep it in check?

### Playthrough 4 — peaceful Italy, 16 turns, the real turn loop

Not isolated this time: `gathering_storm_seed` on the country pack, Italy the player, world
sim proposing nothing, a builder's programme of works and research driven through
`TurnEngine.jump`. This is the game validating decisions 8–10 together.

**Before the workshops fix**, the run exposed the flaw decision 10 addresses: legitimacy
moved wonderfully (28 → 53.6, drifting up behind the stability that housing and schools
raised), but **economy crept 36 → 40.4 and industry never moved at all**. Every project
that builds industry — steelworks (68, refused twice for money), research (refused) — was
out of a poor power's reach on +8/turn, so the compounding loop had no fuel.

**After adding `workshops`**, the same sixteen turns run industry 21.6 → 36.6, economy
36 → 46.6, legitimacy 28 → 47.3. All three peaceful stats are now live and visibly rising
for calm builder play — which is exactly what the standing Dispatch decision asked for.
The steelworks is still a great-power tool (76 and climbing by turn 9); the poor power's
path to industry is the workshops rung, built repeatedly, and it works.

**Two smaller things seen, not yet fixed:** manpower sat flat at 80 the whole run (the
recovery ceiling is population×2 and Italy is already at it, so nothing recovers — fine
here, but a war that spent manpower might not climb back as intended for a small power);
and Italy's queue is only ~2 slots wide at this industry level, so a second same-turn order
is often refused as "already under way", which is correct but could read as a dead end
without the menu now showing free-slot count.

### Playthrough 5 — the AI world develops too (Decision B)

Two harnesses, both through the real turn loop.

*Tracked power builds (B1):* a scripted tracked nation returning `build_orders: [workshops]`
each turn raises **its own** industry 40 → 55 and economy 40 → 65 over fourteen turns — an
AI power developing itself, charged and queued through the player's own order handler.

*The whole world develops (B2):* player Italy building focused workshops, everyone else on
the master AI. Over sixteen turns the non-tracked great powers industrialise **hard** — USA
industry 57.6 → 96.6 (+39), UK 43.2 → 80.2 (+37), Germany +27, USSR +30, Japan +25. The
player's own +20 (focused workshops) no longer walks away from a frozen world; if anything
the unconstrained industrial AIs now out-build a cautious human.

*Long game (40 turns):* top industry braked at ~141 (USA) — the industry-scaled cost is a
real ceiling (a steelworks there costs ~167 treasury), not a runaway. **Zero nations at zero
treasury and zero arrears across all 221 over 40 turns**: the 1.4× investment reserve holds
world-wide. A *passive* human (Italy, building nothing) ends up *out-developed* by the AI
world — the exact inverse of the original problem, and the right one.

**Understand before touching:** in these mock runs the five focus-tracked nations (France
was one) build **nothing**, because a tracked power develops through its own `build_orders`
(B1) and the `MockProvider` returns none. That is a harness artifact — a real model would
build — not a master-AI gap; the master AI deliberately skips tracked nations (they get real
model attention). **The genuine open question for real-model playtests:** does a tracked
agent, juggling war and diplomacy in one call, reliably remember to develop its economy? If
weaker models neglect it, tracked powers could fall behind the master-AI-driven ones, and
the fix is a master-AI *fallback* for a tracked nation that returned no build order and has
idle slots. Left out for now on the small-increment principle — add it only if playtests
show the neglect.

### Playthrough 6 — war to a negotiated peace (peace-as-province-transfers)

The arc the settlement layer exists to make possible, driven through the real `TurnEngine`
loop with a scripted simulator. Aurelia (player) at war with Karthos, a strong army next to
Karthos's border province:

- **t1** Aurelia presses Karthos Basin (75% of the way to taking it).
- **t2** Aurelia *occupies* it — controller Aurelia, owner still Karthos.
- **t3–4** pressure holds at 100%; the war has reached the state real wars stall in: ground
  held, not owned, and no mechanism to convert one into the other.
- **t5** the simulator sues for peace with a cession of the occupied province. The engine
  ends the war and, in the same batch, transfers owner+controller to Aurelia and relocates
  Karthos's capital. Turn report: *"Aurelia and Karthos stopped fighting. Aurelia annexed
  Karthos Basin."*

**Final:** Karthos Basin is Aurelia's, the war is over. Before this the same war would have
ended (if at all) with the occupation frozen and the province still de jure Karthos's — a
ceasefire wearing a peace's name. Now a war can actually be *settled*.

Nine unit tests pin the mechanic: annex, recover-your-own-core, refuse a bystander's land,
refuse un-occupied ground (the safety line), a ceasefire that moves nothing, and capital
relocation. **No live-model question raised by this layer** — the cession is engine-
validated, so a real simulator can only propose terms the engine will already accept or
reject for stated reasons.

### Playthrough 7 — the cost of empire (unrest increment 1)

Not a played game — a calibration sweep of the new drag on the real map, one great power
(Germany, stability 74) made to occupy foreign provinces, run 20 turns with everything else
idle:

- **3 provinces occupied:** stability 74 → 66.7 (−7.3 over 20 turns). A modest conquest is a
  modest, manageable friction.
- **15 provinces occupied:** stability 74 → 31.9 (−42.1). Holding a continent without
  actively countering it nearly halves your stability in five years, and *accelerates* as
  stability falls (lower stability mitigates less), which is imperial overstretch behaving
  as it should. A conqueror can still hold it — but only by pouring stability projects
  (housing, land reform, schools, a freeform rally) into the occupation, which is exactly
  the occupy-vs-digest decision the layer exists to pose.
- **Own ground:** costs nothing — every nation at world start, since `home_nation_id` is
  seeded to the owner.

So the peace layer's annexations now carry a standing price, and blitzing the whole map is
no longer free. **Still to build (next session):** garrison presence easing a province's
unrest, and partisan contests that let badly-held ground actually rebel (turning the drag
from a stat cost into a map cost). No live-model question — the drag is pure engine
assessment.

### Playthrough 8 — occupation with and without a garrison (unrest increments 2 & 3)

Two occupations of the same three provinces (Aurelia holding Karthos's and Veyra's ground,
sandbox, stability 55), run 30 turns side by side — one bare, one garrisoned at 6 strength
per province — to validate garrison-easing and partisan rebellion together:

- **Bare occupation:** load 3.65, stability **55 → 43.3** (−11.7 over 30 turns), and on
  **turn 6 partisans rose in Veyran Plateau and drove Aurelia out** — control reverted to
  Veyra, the province off Aurelia's books (held-foreign dropped 3 → 2, load 3.65 → 2.60).
  The remaining two bled stability for the rest of the run. Unrest is now both a stat cost
  *and* a map cost: hold ground badly and you lose it.
- **Garrisoned occupation:** the 6-strength garrison floors each province's load to **0.00**,
  so **no drag (stability flat at 55) and no uprising in 30 turns** — all three provinces
  held indefinitely. Guns on the ground hold what stability alone could not.

The two levers behave as designed and compose: garrison *or* stability can hold restive
ground, and the cost of neither is where the occupy-vs-digest decision lives. Garrison is
finite (a 6-strength division per province is cheap on three provinces, punishing across a
continent's worth), which is the intended tension. Pure engine assessment + seeded rolls —
no live-model question. **The unrest layer is now complete** (drag → garrison → rebellion).

### Playthrough 9 — France 1936, **REAL MODELS**, 8 turns (the first live integration run)

The one bounded real-model run Alex authorised: France (passive player), standard mode, real
CLIs (world-sim on antigravity/Gemini, tracked agents on codex/GPT), 8 engine turns. The
Italy–Ethiopia war starts live in 1936, so occupation/unrest/peace could be exercised without
scripting. ~48 model-driven diff batches across the run. Purpose: confirm the integrated
system behaves under real models and catch real-model-only bugs the mock never shows.

**Operational finding — agy lazy auth.** The very first world-sim call returned
`not_authenticated` (agy signs in on first invocation); the engine aborted that turn, and the
*next* jump succeeded and ran normally for the rest of the game. `atlas doctor` reports agy
`[OK]` (install check, no call), so it cannot see this — **run `atlas probe` before a real
game.** The engine's abort-and-retry-next-turn handled it cleanly (good resilience: a
world-sim failure does not corrupt the turn; focus, master-AI investment and unrest all still
ran).

**Q1 — do tracked agents develop their economy while also fighting/negotiating? YES.**
Decisively. Across the run Spain, the UK, Germany (×5), Italy (×2), Brazil and Ethiopia all
laid down real `build_orders`; the construction queue grew 9 → 34; Germany reached economy 72
/ industry 50 and the UK 77 / 53 by turn 8. The mock's "every tracked nation looks frozen" is
confirmed a pure harness artifact. **This closes the long-standing open item.** No neglect
fallback is needed.

**Q3 — do AI occupiers garrison / do partisans revolt? UNTESTED, because the war never
reached occupation — and *that* was the run's key bug.** The world-sim opened an Italy→Ethiopia
push (75%) on turn 1, but once Italy was promoted to a tracked agent its offensive *decayed
untouched* every turn after (`objectives_declared=[]` for all nations, all 8 turns).
Root cause, found and fixed (decision 20): the tracked-agent briefing only explained the
`military_objectives` lever when the nation had a land **front**, and Italy–Ethiopia is a war
*across water* with no shared border — so Italy was never told it could order an amphibious
push, and never did. Garrison/partisan therefore still await a run where an occupation
actually happens.

**Q2 — does the simulator sue for peace? UNANSWERABLE this run** — there was never an occupied
stalemate to end (see Q3). Still open.

**Q4 — real-model-only bugs.** One behavioural bug (Q3's front-gated war lever, fixed) and one
strong *negative* result: **the engine's validation held perfectly against ~48 batches of
adversarial real-model output.** Every malformed proposal was rejected cleanly, never
crashing a turn: relation-score writes were the models' most common stumble (28 rejections
across four rules — out of `[-100,100]` range, above the 25/turn swing cap, and visibility-scope
mismatches), and the world-sim emitted an empty-`region_id` clash roughly once a turn (×10,
rejected as "region '' does not exist"). None of these are engine defects — they are the
guardrails doing their job — but they are **legibility gaps**: the models are never told the
relation-write rules, so they keep proposing changes that get thrown away, and their
diplomatic intent is lost with the whole batch. Fixing that (telling the models the caps and
the range, the decision-11 discoverability pattern) is the clearest follow-up, though whether
it changes behaviour needs another real run to confirm.

**Bottom line:** the integrated system is *robust* under real models (no crashes, validation
airtight, the AI economy genuinely develops), and the first live run immediately earned its
cost by surfacing the front-gated war-lever bug that a mock could never show. The war/peace
half of the standing questions still needs a run where an occupation actually lands — most
likely now that overseas belligerents are told how to attack.

### Playthrough 10 — France 1936, **REAL MODELS**, 8 turns (the second live run)

The second authorised run, after two fixes shipped from PT9: the overseas-belligerent war
lever (decision 20) and the relation-write bounds (decision 21). Fresh world, so PT9→PT10 is a
clean before/after. `atlas probe` first (per the agy-lazy-signin note) — claude and agy live;
**codex crashed once, then a single re-probe served cleanly** (transient CLI flakiness worth
noting), so all three were confirmed before spending the game. Ran clean start to finish, no
auth abort this time.

**The occupation → unrest chain fired under real models — the headline result.** The world-sim
drove the Italy–Ethiopia contest over the threshold on turn ~5 (`Italy now occupies Ethiopia,
which Ethiopia still owns`), and the unrest layer immediately engaged: **`restive=['Italy']`
for the three following turns**, charging Italy's stability for the foreign ground it now held
(final state: `r_eth` controller=`ita`, owner=`eth`, home=`eth` — control taken, ownership not,
exactly the occupation/annexation split). **First live validation that peace-layer occupation
and the unrest drag work end-to-end in a real game**, not just in isolation.

**Decision 20 (war lever) — half-confirmed.** The fix worked at the level it operated: Italy,
now shown the offensive briefing, **did declare a `push` on Ethiopia** — visible as the refusal
`Italy: r_eth borders none of your territory, so you cannot push into it`. But the push was
*correctly* refused: this map does **not give Italy its historical East-African colonies**
(Eritrea `m_eri` and Somaliland `m_som` are independent minors), and Ethiopia is landlocked, so
Italy genuinely cannot reach it by land or by its 2 transports. So the tracked-agent war path is
un-gated now, but this particular war is unreachable through it; the occupation came via the
world-sim channel instead (which resolves off abstract force ratios, not unit reach). **Scenario
finding:** to actually test tracked-agent war *prosecution*, the next run needs land-adjacent
belligerents — either give Italy its colonies, or script a France/Germany-style land war.

### The morning run — a LAND-WAR harness is built and waiting (do NOT run without Alex)

The scenario the whole untested remainder needs — a land war between *adjacent* powers with a
**tracked** occupier on conquered ground — is prepared and mock-validated. Harness at
`…/d8f7f7fd-…/scratchpad/rmpt_landwar.py` (this session's scratchpad; full path in the
land-war-harness note below). It seeds **Germany (`ger`) vs Poland (`pol`), 1936**, player =
**Poland (passive)** so the occupier is at war with the player and therefore scores +100 in focus
and is **promoted to a tracked agent** — a real-model call driving Germany. Germany starts
occupying two Polish border provinces (`r_pol_polish_corridor`, garrisoned; `r_pol_greater_poland`,
bare), owner+home still Poland (the occupation/annexation split), with a German garrison on the
corridor and on the adjacent home province `r_ger_east_prussia` (a real front, which OWN-001 needs
for any later cession).

**Mock dry-run confirmed** (`RMPT_MOCK=1`, free): Germany is tracked from turn 1, `settleable=[(ger,
pol,2)]` so decision 23's sue-for-peace nudge fires in the world-sim briefing, and the garrison is
on the ground. This exercises, in one run: **tracked war prosecution** (real Germany), the
**sue-for-peace nudge** (world-sim shown the settlement), and **garrison-easing** (units flooring a
province's unrest) — all reliably. The **drag/partisan** half is the one caveat: the bare province
does not necessarily stay bare — **the engine walks German units onto controlled border ground
during the turn, re-garrisoning it and zeroing the drag** (seen in the mock dry-run; a realistic
"an army sits on what it holds" dynamic, but it means partisan revolt bites only under
*over-extension*, which the real prosecuting Germany may create by pushing deeper than it can cover).
To run: `atlas probe` first (PT10's codex-crash note), then `<venv-python> rmpt_landwar.py`
(env `RMPT_TURNS` default 8). It STOPS on the first hard failure, no retries.

**Decision 21 (relation bounds) — confirmed working.** Versus PT9's 28 relation rejections: the
25/turn swing-cap violations (DIP-002) went **6 → 0** and the visibility-scope violations
(INFO-002) **16 → ~6**. The guidance measurably cut the churn on exactly the rules it stated.

**Q2 — does the simulator sue for peace? First real data point: NO.** Italy held Ethiopia's
capital province occupied for three turns and **neither side ever proposed a `peace`** (0
peace/ceasefire events all run). The mechanism exists and is safe, but nothing makes a model
reach for it — confirming the standing concern. The lever now wants a *nudge*: a stronger
prompt when a nation holds enemy ground, or an engine-proposed settlement after a front has been
static and one-sided for N turns.

**Garrison / partisan — still unexercised.** The world-sim's abstract occupation stationed no
Italian units in Ethiopia, so there was no garrison to ease unrest, and three turns is too few
for a partisan roll to land. These await a longer occupation or a land war where the occupier
actually marches units onto the ground.

**New Q4 finding:** models cite **nonexistent `reason_event_id`s** (~5–6 "cited event does not
exist" rejections) — a citation-discipline stumble separate from the relation writes, rejected
cleanly. A candidate for the same discoverability treatment later.

**Bottom line:** two live runs in, the engine is proven robust and the peace→occupation→unrest
spine now has a real-model confirmation. Both PT9 fixes did what they were built to do. What
still has *no* live confirmation: an AI *garrisoning* held ground, a *partisan* revolt, an
AI *prosecuting* a war through its own objectives (blocked here by map geography), and an AI
*suing for peace* (observed once — it didn't). All four point the same way: **the next real
run needs a land war between adjacent powers**, where units actually move onto conquered
ground — that is the one scenario that would exercise the whole untested remainder at once.

### Playthrough 11 — the sue-for-peace nudge, mock end-to-end (decision 23)

Not a real-model run — the roadmap's mock validation that a scripted occupier now *reaches
the peace table* once it is told it can. Setup: Aurelia occupies Karthos Basin (controller
Aurelia, owner still Karthos), the two are at war, and Aurelia holds adjacent home ground —
the settled-occupation stalemate PT10 found no one ever ends. Driven through the **real
`TurnEngine.jump`** with a `MockProvider` world-sim scripted to act on the nudge only when it
actually appears in its briefing:

- **Before the nudge existed**, the same referee saw an occupation and a war and did nothing
  — exactly PT10's live result (Italy held Ethiopia's capital three turns, 0 peace events).
- **With the nudge**, the world-sim briefing now carries a *"Wars that could be settled now"*
  section naming Aurelia's hold on `r_south` and prompting a `peace` with a `cession`. The
  scripted referee reads it, proposes the peace, and the engine settles it: **owner
  karthos→aurelia, controller aurelia, at_war True→False** — the war is over and the ground
  annexed. (One setup catch, not a bug: OWN-001 still requires the occupier to hold ground
  *adjacent* to the ceded province — a real front — so the harness inserts the r_north↔r_south
  adjacency a real march would have created; without it the cession is correctly refused as
  unratified.)

Seven repo tests pin it without the harness: `settleable_wars` lists an occupied stalemate,
ignores a peacetime holding and an unoccupied war; the world-sim view carries the nudge (and
omits it when nothing is settleable); the advisor prompts the player to `sue_for_peace` (and
stays silent otherwise). **944 tests green.** What this does *not* prove — and PT10's actual
gap — is whether a *real* model, shown the nudge, chooses to end the war. That is queued for
the next authorised real-model run (see the land-war setup below): the affordance is now
visible and acted-upon in mock; whether it changes live behaviour needs the real run.

### Playthrough 12 — minor-nation promotion, the Abyssinian campaign in mock (decisions 26–27)

Not a real-model run — the roadmap's mock validation that a *promoted minor is fightable
province by province* (roadmap item #5). After promoting Ethiopia (a weak power) and its
neighbours Eritrea and Somalia to province granularity, a scripted campaign drives Ethiopia
into Eritrea through the **real `resolve_turn`**, pressing every Eritrean province it can reach
each turn (Ethiopia handed a real army first — its historical strength of 8 could not overrun
anyone):

- Eritrea's **six provinces fall in sequence**, not on one roll: over five turns the count Ethiopia
  holds climbs 0 → 2 → 2 → 5 → 6, with the front width changing as the border moves (4, 4, 4, 4, 1).
  There is a turn where Ethiopia holds *some* of Eritrea and not the rest — the state a single-region
  minor can never show, and the whole point of promotion.

Structural checks alongside it: each promoted minor is one connected landmass (Eritrea 6/6, Somalia
13/13, Ethiopia 11/11 regions internally reachable), meets only its real land neighbours and open
water (no cross-ocean stitching), garrisons only its own soil, governs from a region it owns
(CORE-005), and keeps its country-level strength unchanged (promotion is geometry, not a buff). No
country-level region is left behind for a promoted nation (the same-ground-owned-twice failure).
`home_nation_id` is seeded per region, so the occupation/unrest/partisan spine already applies to a
conquered minor. **954 tests green.** What this does *not* settle is the scenario choice of whether
**Italy owns** Eritrea/Somaliland — the ownership flip that would let *Italy* (not just Ethiopia's
neighbours) fight the Abyssinian war from a real land front. That is queued for Alex as the
land-war real-run setup; the geometry it needs is now in place (see "The morning run" above).

### Playthrough 13 — Germany vs Poland, **REAL MODELS**, tracked-agent land war (the reserved run)

The run the whole land-war track was built for, and it exercised **the entire untested chain at
once.** Setup (harness at `scratchpad/rmpt_landwar.py`, rebuilt this session): a promoted
gathering-storm world, no human player, Germany and Poland set at war and both tracked from turn 1,
one German `push` seeded onto the Polish Corridor (the proximate cause) so the front is live
immediately. World-sim on antigravity/gemini, tracked agents on codex/gpt, 8 turns; `atlas probe`
clean first. It ran to a natural end in **7 turns** and every reserved question came back answered
**yes**:

- **A tracked belligerent prosecutes its own war.** Germany's agent did not just hold the seeded
  push — each turn it *advanced the objective*: Polish Corridor → Greater Poland → Masovian (toward
  Warsaw), setting `hold` on each conquest as it took it. This is decision 20's affordance
  (belligerents told how to press) working under a real model, and the exact behaviour PT9 found
  missing. Poland's agent answered defensively — `hold` on its threatened provinces, then a `push`
  counter-attack to retake Greater Poland.
- **The front is set by the defender.** Germany took the Corridor and Greater Poland but Poland
  **threw it back at Masovian** three turns running — the war stalled at two provinces because the
  defender fought, not because a timer ran out. Poland *withdrew* from Greater Poland to Łódź when
  beaten (the retreat mechanic), and in the parallel Abyssinian war Ethiopia's cut-off forces were
  **captured with Afar** (encirclement). All live.
- **Garrisoning holds occupied ground.** Germany set `hold` on its two conquered Polish provinces
  and they **never revolted** — German stability actually *rose* while it occupied. Decision 17
  (garrison eases unrest) confirmed live. *(This is also why no partisan revolt was observed: both
  occupiers garrisoned well. To see a partisan rising live still needs an occupier that does not —
  the mechanic is validated in mock, PT8, but not yet under a real model that neglects its hold.)*
- **THE big one — an occupier sued for peace.** Turn 7: *"Germany and Poland stopped fighting.
  Germany annexed Greater Poland. Germany annexed Polish Corridor."* The world-sim, shown the
  decision-23 nudge over a settled two-province occupation, **reached the peace table and ceded the
  held ground** — ending the war and transferring owner+controller to Germany. This is the precise
  gap PT10 found (Italy held Ethiopia's capital three turns, nobody ever proposed peace) and
  decision 23 was built to close; it now works under real models, with sane terms (it ceded exactly
  what was held). The single most valuable result of the night.
- **Bonus: the colony flip (decision 28) validated live in the same run.** Italy, tracked, prosecuted
  Ethiopia *from its new colonies* province by province — Afar occupied, then Amhara, Ethiopia
  withdrawing inland — so tasks "give Italy its colonies" and "run the Abyssinian war" both got real
  confirmation here without a separate run. Solvency held throughout: Germany dipped to 1.8 treasury
  at the annexation turn but never went to arrears; Poland bottomed at 2.1 and recovered.

**Findings (all guardrail behaviour, no crashes across 7 turns / ~40 real calls):**
1. **Region-name-for-id churn (new, low severity).** Models occasionally name a region by its display
   name ("Greater Poland", "Masovian", "Alsace-Lorraine") where an id (`r_pol_greater_poland`) is
   required, and the objective/contest is refused ("X is not a region on this map"). The ids *are*
   already surfaced (front and offensive briefings print `` `r_...` `` next to each name — the
   decision-11 pattern), and Germany used them correctly most turns, so this is residual model noise
   the guardrail rejects cleanly, not a missing-information bug — and objectives are recorded apart
   from the batch, so a refused name does not cost the power its standing war. Watch; do not
   over-engineer on one run.
2. **Invalid `treaty_status` value kills a batch (new, worth a fix).** A tracked agent proposed a
   `relation.treaty_status` outside the closed set `none|non_aggression|trade|defensive|alliance|
   vassal`, failing the CHECK and taking its whole atomic batch down. The valid set is never stated
   in the briefings — the same discoverability gap decisions 21/24 fixed for relation bounds and
   event ids. **Fixed this session (decision 29).**
3. **The known PT9/PT10 churn persists, guardrails holding:** DIP-002 relation swing-cap overshoots
   (bounds are told since decision 21; models still occasionally exceed), INFO-002 covert-cause/overt-
   state, CORE-001 fabricated event ids (decision 24's fallback is offered; still seen), and the
   world-sim's ~once-a-turn empty-`region_id` clash. None fatal; each costs only its own diff.

### Playthrough 14 — Italy vs Ethiopia, **REAL MODELS**, the Abyssinian war from the colonies

The flagship war, run for real, to validate the minor-promotion + colony-flip work (decisions 26–28)
end to end. Same harness (`scratchpad/rmpt_landwar.py`), parameterised: `RMPT_ATTACKER=ita
RMPT_DEFENDER=eth RMPT_PUSH=r_eth_tigray` (Tigray borders Italian Eritrea). Italy tracked, one push
seeded from Eritrea, 8 turns; it ended naturally in **7**. The result is a near-perfect historical
echo — the Second Italo-Ethiopian War ended in May 1936 with Italy annexing Ethiopia; this run ended
turn 7 (dated 1936-06) with Italy annexing its conquests.

- **A colonial power prosecutes from its colonies.** Italy's agent occupied Tigray from Eritrea, then
  drove inland turn after turn — Tigray → Amhara → Afar → Benshangul-Gumaz — while setting `hold` not
  only on each conquest but on *its own colony provinces* (`r_m_eri_debub`, `r_m_som_bakool`): a
  tracked power defending its colonies and advancing out of them. This is decision 28 (the colony
  flip) confirmed live and deliberate: landlocked Ethiopia was fought province by province from
  Italian East Africa, the exact thing impossible before this session.
- **The defender fought and was ground down.** Ethiopia held the interior (Amhara defended for two
  turns, a counter-attack to retake it), withdrew when beaten (Afar → Oromiya, cut-off forces
  captured with the province — encirclement), and was bled toward bankruptcy (treasury 8 → 0.6)
  without ever collapsing outright.
- **The war ended in a negotiated annexation.** After Italy consolidated four occupied provinces and
  paused (all `hold`, no new `push`), the world-sim — shown the settled occupation via the decision-23
  nudge — sued for peace: *"Italy and Ethiopia stopped fighting. Italy annexed Afar, Amhara,
  Benshangul-Gumaz, Tigray."* Sane terms (it ceded exactly the four held), a rump Ethiopia surviving
  on its remaining seven provinces. **Both real runs this night (PT13, PT14) ended on turn 7 with the
  world-sim suing for peace and annexing the held ground** — decision 23 is robust across two very
  different theatres (adjacent great powers; a colonial war on a landlocked minor).
- **Solvency held:** Italy dipped to 7.5 treasury mobilising, recovered to 40; no nation hit arrears.

Findings: the same churn set as PT13 (**INFO-002 visibility scope the commonest here — addressed by
decision 30**, which now shows each event's scope so a cited cause can be matched; CORE-001 fabricated
event ids; one region-name-for-id refusal "Tigray is not a region on this map"), all guardrail
behaviour, no crashes across 7 turns. **Not triggered:** a partisan revolt (Italy garrisoned
its conquests) and the decision-18 deferred case (Ethiopia was never *wholly* conquered — it kept
seven provinces — so the "a dead home nation spawns no partisans" question is still unexercised
live). **955 tests green; both harness runs clean.**

### Playthrough 15 — Japan 1936, a peaceful builder, ~24 turns (MOCK)

The first playtest of a nation never driven before, chosen to stress a profile the earlier runs
never did: a **naval great power on a poor economy** (Japan — economy 42, an 18-ship fleet). Mock
world-sim (peaceful), the player driving an adaptive builder off the advisor's own build menu, 24
turns. It was a **legibility playtest** — the full advisor view was dumped every few turns and read
as a player would read it — and it found four things, all now fixed (decisions 31–34):

1. **"comparable than you"** — a rival who is a close match rendered ungrammatically in the briefing
   (also "unknown than you"). Decision 31.
2. **The event feed was a wall of workshops.** Japan's "what your ministry knows has happened" was
   ~25 near-identical "<nation> begins/completes Workshops" lines from the master AI developing every
   unattended nation, burying Japan's own two actions. The referee's `all_events` window is capped at
   120, so on a busy turn the real wars and treaties can be crowded off the end entirely. Decision 32
   thins it to the viewer's own builds plus a count; the feed dropped from ~25 lines to 3.
3. **The build menu never named what was under way** — "1 of 2 slots working" with no idea what
   occupied the slot or when it freed. Decision 33.
4. **No income gauge anywhere.** The treasury was a bare number; nothing said it was falling. This
   surfaced the **sharp balance finding**: Japan is the *only* great power that starts in structural
   deficit (net −2.3/turn, fleet upkeep 13.3 against revenue 11.0), against the budget's own claim
   that a great power at peace is "six to eight a turn ahead". In the do-nothing run it drifted into
   arrears by turn ~18. Decision 34 adds the gauge (and a deficit warning naming the levers).

**The balance question the gauge raised, then answered (PT16, a follow-up mock).** Re-run with a
*smart* builder (industry first): Japan's deficit is **escapable, not a trap**. One early workshop
lifts the industry→economy ceiling; the economy then drifts up passively 42→51, revenue climbs
11.0→13.4, and net reaches **+0.1 by turn 24 without ever hitting arrears**. So the deficit is
historical pressure with a discoverable exit (industrialise), and the right response was the gauge
that makes the exit legible — **not** a rebalance of Japan's fleet or economy. A second PT16 profile,
**Poland** (a poorer small power, economy 26, tiny army), confirmed the gauge reads sanely at the
other extreme: net **+8.2/turn**, a healthy surplus, treasury climbing — and reconfirmed the standing
"single-region powers hoard cash they can't convert to growth" item (open item 5), not a new bug.

**958 → 960 tests green** across the four fixes (each committed separately with its own regression
test). All view-only and additive; no schema, engine, write-path, or balance-constant change — the
budget numbers are unchanged, only now *shown*. The mock harness (`pt15_builder.py`, parameterised by
`PT_PLAYER`) is a throwaway in the session scratchpad per convention.


### Playthrough 17 — Modern Day 2026, the first playthrough of a second era (MOCK)

`scratchpad/pt_modern.py`, kept (like the land-war harness, not a throwaway — the next era wants
the same questions asked of it). Six sections, all green on the final run.

**Section 1–2, the world.** 242 nations, 355 regions, 865 borders, 673 units. 113 seas, 160 coastal
regions. Nothing standing in open water; no nation with a capital at sea. Before Phase 0 this same
build produced 355 "nations" of which **113 were open water with armies**.

**Section 3, the era reaches the referee.** The world-sim brief carries the preset's lore
("Great-power competition has returned without anyone declaring it") *and* its hard constraints
("Nuclear weapons exist and their use is close to unthinkable…", "Economic coercion is usually the
first instrument, not force"), and does **not** carry 1936's ("No jet aircraft"). This is the check
that would have failed silently before the `preset_id` fix, with no symptom except a simulation
quietly playing by no era's rules at all.

**Section 4, the fleets.** 132 hulls across 15 powers: usa 27, chn 17, jpn 12, rus 12, gbr 9, ind 9,
fra 8, kor 7, tur 6, prk 4… Every declared fleet reached the water — the check that **caught the
simplified-coastline bug** (decision 38), where Israel declared five hulls and silently received
none. An American fleet finds **45 shores it could put an army ashore on**, so the amphibious layer
is live on a modern map.

**Section 4b, the budget.** Added to the harness after the first pass put all sixteen powers
underwater (see decision 41). After recalibration: fifteen solvent at peace, Pakistan at âˆ’0.4/turn
with ~37 turns of reserves. No power bankrupt inside ten turns.

**Section 5, a war.** Russia and Ukraine start at war *from the scenario data* — no harness setup.
A seeded Russian `push` on Ukraine was accepted across a real border; four turns resolved with no
aborts. Turn 1 Ukraine held (Russia to 75% pressure); turn 2 Russia took it and Ukraine's remaining
forces in the province were cut off with it.

**The finding, and it is the Phase 3 argument.** At country granularity **Ukraine falls in two
turns** — one region, one contest, done. Nothing is wrong with the engine; the map is simply too
coarse for the war it is being asked to hold. This is precisely the case the territory file exists
for, and it is why Russia/Ukraine heads the Phase 3 promotion list. Until then, Modern Day showcases
the naval, economic and diplomatic layers well and the *land-war* layer only shallowly.

**Second finding, minor:** the 1936-flavoured example list in the parser's `unit_kind` hint ("e.g.
infantry, armour, battleship, carrier, transport") is now wrong for a modern player, who has no
battleships and does have destroyers and submarines. Harmless — `order_construction` validates
against the world's own `unit_template` rows, so "build two destroyers" works — but it is a
legibility wart of exactly the kind PT15 was about. Left for the next session; noted in open items.


### Playthrough 18 — Modern Day, the flashpoints promoted (MOCK)

Same harness as PT17 (`scratchpad/pt_modern.py`), re-run against the promoted map, plus a
new section. All green.

**The war, which is what Phase 3 was for.** The harness now discovers the front from the
map rather than hardcoding it: **three of Ukraine's nine provinces border Russia**
(Donets'k, Sumy, Zaporizhzhya), and it presses whatever is reachable each turn, the way a
tracked agent would. **The campaign ran twelve turns instead of two.** Ukraine withdrew
from Khmel'nyts'kyy to Ivano-Frankivs'k rather than evaporating; Ivano-Frankivs'k was later
taken with its garrison cut off inside it; Kiev City held out through three separate
contests before falling on turn 11; and at turn 12 Ukraine still held one province and was
*winning* the contest for it. That is a campaign with a shape, which is exactly what a
single country-level contest could not produce.

**The rest of the world got bigger without getting slower.** 429 regions and 1094 borders,
up from 355 and 865 — the eight promoted countries cost 74 regions between them and
everything else is untouched. 202 coastal regions (up from 156), because province-level
coasts are finer than country-level ones.

**A balance change arrived through the data file, and it is a good one.** A promoted power
draws development revenue from a dozen regions rather than one, which pushed **Pakistan out
of its −0.4/turn deficit**: every power is now solvent at peace. Worth naming because it is
the kind of change that arrives silently — a territory edit is not obviously a budget edit.
Pinned by a test.

**Taiwan is amphibious-or-nothing.** Its eleven provinces have no land neighbour at all;
every adjacency out of Taiwan is to ocean. The Chinese invasion has to come across the
strait with the four amphibious ships China owns, which is the scenario working as intended
and the first use a second era has made of the 1936 minor-promotion machinery.

**Crimea needed no override.** Natural Earth already files Crimea and Sevastopol under
Russia, which is the post-2014 position the scenario's history records, so the mechanical
pass produced it. Pinned, because a Natural Earth refresh that moved them back would change
Russia's start position with no other symptom.


### Playthrough 19 — the same war at four turn lengths (MOCK)

`scratchpad/pt_pacing.py`, rewritten around the right question. "Turns to conquer" is
meaningless when a turn can be a week or a year, and "elapsed time to conquer" is quantised
to the jump -- a province that falls in the second month of a year-long turn still reads as
a year, because there is no date between the two ends of a jump. So: give every span the
**same budget of game time** and ask what it achieved.

**Before** (the measurement that started this, PT18-era code):

| span | turns | elapsed to conquer | carrier |
|---|---|---|---|
| Week | 9 | **0.2 y** | 0.0 y |
| Month | 8 | **0.7 y** | 0.1 y |
| Season | 10 | **2.5 y** | 0.2 y |
| Year | 14 | **14.0 y** | 1.0 y |

**After**, 24 months of game time at each span, Russia pressing Ukraine throughout and a
120-day destroyer laid down before the first jump:

| span | days/turn | months | provinces | ship | economy | treasury | manpower |
|---|---|---|---|---|---|---|---|
| Week | 7 | 24.0 | 8/9 | 4.2mo | +2.6 | +117.1 | +739.2 |
| Month | 30 | 24.0 | 8/9 | 4.0mo | +2.8 | +117.4 | +739.2 |
| Season | 90 | 24.0 | 9/9 | 6.0mo | +2.8 | +116.9 | +739.2 |
| Year | 365 | 24.3 | 8/9 | 12.2mo | +2.7 | +110.7 | +739.1 |

**The 81x spread is gone.** The same two years buys the same war (8-9 of Ukraine's nine
provinces), the same economy, the same treasury and the same manpower, whichever button the
player pressed.

**The ship column still spreads, and it is not a bug.** Delivery is only *observable* at a
jump boundary: a hull finished in the fourth month of a year-long turn cannot be seen until
the turn ends. Reading the yard directly removes the quantisation -- after ~90 days of game
time on a 120-day ship, Week/Month/Season have **29 / 30 / 30** days left. The clock
underneath is right; the reporting is as fine as the jump the player chose, which is the
control/speed trade the four buttons exist to offer.

**Three findings the harness produced by being wrong first**, each worth more than the run:

- **It ordered a carrier Russia could not afford**, had the order refused for want of ninety
  in the treasury, and then reported the ship "finished" on turn one because the yard was
  empty. A measurement of nothing, printed as a number. It now asserts the order started.
- **It declared nine objectives against a limit of six**, so the engine truncated
  alphabetically and the harness spent two runs measuring its own sort order. It now orders
  them the way a staff would -- what the front can reach first.
- **Declaring only today's front** measured how often the harness got to re-declare rather
  than how fast the war moved, which is what surfaced decision 47.


### Era gating, at a glance

What is per-era after Phase 4, and what is still shared. Worth keeping current: the answer
to "where does a third era hang its X" is this list.

| Per era (on `Scenario`) | Shared (engine) |
|---|---|
| `powers`, `relations`, `treaties`, `history` | the diff pipeline and every validation rule |
| `unit_templates` and `ship_classes` | the *kind names* `transport` (sea lift) and the land/air/sea domains |
| `armour`, `air`, `fleets`, `colonies` | `order_of_battle`, `fleet`, `garrisoned` -- the arithmetic |
| `technologies` | `TechEffect`'s four levers, and the delivery path that applies them |
| the territory file, via `Scenario.id` | promotion, merging, adjacency, coast discovery |
| the preset's lore and hard constraints | the pacing model (`span`, rounds, `build_days`) |

The one rule that binds them: **an era may rename its ships but not its amphibious lift**.
`engine/naval.py::sea_lift` looks up `kind = 'transport'` by name, so an era that renames
it keeps every other naval mechanic and silently loses the ability to land an army
anywhere. Pinned by a test in `test_modern_day.py`.


### Playthrough 20 — Modern Day, **REAL MODELS**, Russia vs Ukraine, monthly turns

The first real-model run of a second era, and it tested **three never-run-live systems at
once**: Modern Day itself, variable turn length (monthly, the calibrated span), and the
era-gated research catalogue. Harness at `scratchpad/rmpt_modern.py`, kept. `atlas probe`
first: claude and codex clean, antigravity failed on a transient Google profile-picture
timeout in its own eligibility check and came back clean on a re-probe.

**Five clean turns, then a provider crash.** Turn 6 aborted on the *same* antigravity
eligibility error that had failed the first probe — so that flakiness is recurring, not a
one-off. The harness did the right thing: reported it and stopped, keeping five turns of
resolved work. Ninety-six percent of what was proposed applied.

**What came back green:**

- **The era gate works live, end to end, across three independent agents.** Russia laid
  down `uncrewed_systems` on turn 1, the United States the same on turn 2, China
  `modular_shipbuilding` on turn 4 — three nations, two distinct modern programmes, all
  chosen from the era catalogue in their own build menus. **Russia completed
  `uncrewed_systems` on turn 5**: offered, chosen by a live model, built through the yards,
  delivered. Not one model asked for an interwar programme; `era-gate refusals seen: none`.
- **A war the *scenario* declared.** Unlike PT13/PT14, nothing seeded the war — Russia and
  Ukraine came out of `preset_politics` already at war, and the front was live on turn 1.
- **Both belligerents played their side properly.** Russia took Donets'k, Sumy and
  Zaporizhzhya, setting `hold` on each conquest *and* on two of its own border provinces,
  then dropped every `push` to consolidate. Ukraine withdrew province by province rather
  than evaporating, set `hold` on what it still had — **and counter-attacked**.
- **A defender retook occupied ground, for the first time in any run.** Ukraine recaptured
  Sumy on turn 4; Russia's forces withdrew and the remainder were cut off with it. By turn
  5 the front had frozen with neither side pushing, which is an uncomfortably good likeness
  of the real 2026 position and nobody designed it.
- **The peaceful layer ran underneath the war.** Workshops commissioned in Germany, France,
  Britain and India; five powers promoted to tracked agents by focus.

**Six findings, one fixed, five logged.**

1. **The liberation headline named the beaten occupier as the owner.** Ukraine retaking its
   own province was reported as "Ukraine now occupies Sumy, which Russia still owns." The
   *state* was right throughout — occupation moves control only. **Fixed this session**
   (a recapture now reads "has retaken"), with a regression test verified against the old
   code. This is the single clearest argument for real-model runs: the bug needed a
   counter-attack to appear, and five runs never produced one.
2. **ECON-001's caps are never disclosed, and cost a whole batch on turn 1.** The world-sim
   tried to roughly double both belligerents' `military_strength` in one month; the cap held
   and took the rest of the batch with it. Exactly the failure decision 21/22 fixed for
   relation writes, where stating the bounds in the briefing cut rejections 6 → 0.
   `views.RELATION_WRITE_RULES` is the pattern; there is no `ECON_WRITE_RULES`.
3. **Validation is the one place the pacing model does not reach** — and
   `state/rules/economy.py` has claimed otherwise since the initial commit ("The caps scale
   with the length of the jump"). No rule reads `days` or `span`. Harmless at monthly, wrong
   at a year. `RuleContext` has `conn` and `batch.turn`, and `turn.start_date`/`end_date`
   are both written, so the span is recoverable without a schema change.
4. **`partial` no longer means anything.** `status = SEALED if not rejections and not
   failures else PARTIAL`, so *any* refusal marks the turn partial — turn 3 applied 6 of 6
   batches and still read partial because China was told a workshop was already under way.
   With five tracked agents nearly every turn will contain one, which buries the signal that
   distinguishes a dead provider from a double-ordered workshop. The codebase already fixed
   this one level down: see the `tracked_quiet` comment in `turn_engine.py`.
5. **Standing objectives are not pruned when they go moot.** After Ukraine retook Sumy,
   Russia's `hold` on it and Ukraine's `push` on it were both refused — correctly, and
   through no fault of either model, which had declared them a month earlier against a map
   that then changed. Re-refused every turn, and each refusal marks the turn partial (4).
6. **The build-order matcher, not the catalogue.** China's build order was refused with
   "was not told which public work or programme to begin". The refusal listed exactly the
   seven modern programmes and no interwar ones, so Phase 4 feeds the error path correctly;
   what failed was `orders.py`'s substring match against whatever China actually asked for.

Findings 2, 4, 5 and 6 are all the same shape and worth taking together: **the engine knows
the answer and is not telling the model in a form it can act on.** That is the decision-11
discoverability pattern, and it has paid off every time it has been applied.


### Playthrough 21 — Modern Day again, **REAL MODELS**, the four fixes under test

A deliberate repeat of PT20: same harness, same world, same opening position (Russia 12
provinces, Ukraine 9, the same three border provinces pushed), same seven modern
programmes. `atlas probe` clean on all three first time — antigravity answered in 12.6s
against 179.6s in PT20, same transient-network family, the good side of it.

**Seven turns of seven sealed. Thirty-nine batches proposed, thirty-nine applied. Zero
rejections, zero failures, no abort.** No ATLAS real-model run had done that before.

| | PT20 | PT21 |
|---|---|---|
| turns completed | 5 of 7, then aborted | **7 of 7** |
| status | 4 partial, 1 sealed | **7 sealed** |
| batches | 26 of 27 applied | **39 of 39** |
| rejections | 5 | **0** |

Every one of PT20's five rejections was in the class one of the four fixes targets, and
none recurred. Turn 1 is the sharpest single comparison: PT20 lost the whole world-sim
batch to the undisclosed ECON-001 cap (2 of 3 applied); PT21 applied all three.

**What this run does and does not prove.** It is one sample of a stochastic process, and
the honest reading of "no failure occurred" is weaker than "the fix worked". Specifically:

- **Decision 50 (economic caps stated)**: the PT20 failure did not recur and the world-sim
  moved economic aggregates freely all run without tripping the cap. Consistent with the
  fix; not proof it caused the absence.
- **Decision 53 (build orders answered)**: no build order failed to match, where PT20 had
  one. Same caveat.
- **Decision 51 (`partial` semantics)**: **not exercised.** It only changes what happens
  *when* something is refused, and nothing was. Still unproven live.
- **Decision 52 (objectives lapse)**: **not exercised.** No standing objective went stale —
  both agents kept theirs current, and the one province that changed hands unexpectedly
  (Donets'k) was not under a standing `hold` at the time. Still unproven live.

Two of the four remain untested by this run, and saying so is the point: a green run that
did not reach the code is not evidence about the code.

**The headline finding is something else entirely: a partisan revolt, under a real model,
for the first time.** On turn 2 Donets'k rose against Russia and reverted to Ukraine —
Russia 15 → 14 provinces, occupied 3 → 2. The mechanic has been validated in mock since
PT8 and listed as unexercised-live in every handoff since, because PT13 and PT14 both had
occupiers that garrisoned their conquests. The earlier handoffs proposed *scripting* a bare
occupation to force it.

It needed no scripting. Russia took three provinces on turn 1, set `hold` on two of them,
and pushed on toward Kiev and Mykolayiv — leaving Donets'k bare. A real model over-extended
and the unrest layer charged it for exactly that, which is far better evidence than a
scripted setup would have been. Russia then ran `restive` for four consecutive turns
afterwards, so the stability drag is sustained and legible too.

**The rest, briefly:**

- **The era gate held across four nations and two programmes.** Russia and Germany both
  completed `uncrewed_systems`; China ran `uncrewed_systems` *and* `advanced_semiconductors`;
  the United States ran `advanced_semiconductors`. `era-gate refusals seen: none`. Nobody
  asked for anything interwar in either run.
- **`build_days` ticks correctly under real models.** China's and America's 240-day
  semiconductor programmes counted down 240 → 210 → 180 → 150 → 120 → 90 across six monthly
  turns: exactly thirty days a turn. The pacing work, observed live rather than in mock.
- **The war stayed a war.** Ukraine threw Russia back in Mykolayiv, counter-pressed Sumy to
  43%, and ended holding seven provinces with four `hold` objectives. Nobody sued for peace
  in seven months, which is a reasonable outcome for this war and not obviously a fault.

### Playthrough 22 — the gambit channel, six audits (MOCK)

`scratchpad/pt_gambit.py`. The unit tests (22 of them) drive `apply_gambit` directly; this
drives the *engine* — a scripted model proposes attempts, the turn loop routes them, and the
world is inspected afterwards. All six sections green.

**All three outcomes occur, and the stake is charged whatever happens.** Sweeping seeds until
each result appeared, at `serious` stakes with one favourable evidence flag (60% odds):

| result | charged | relation | stability |
|---|---|---|---|
| success | 16.0 | +3.0 | +3.0 |
| partial | 16.0 | +1.5 | +1.5 |
| failure | 16.0 | −1.5 | +0.0 |

A partial moves exactly half a success (`PARTIAL_SHARE`), and a failure moves the relation the
*other* way — the `on_failure` list is a real consequence, not a null branch. This table is
what decision 56 was checking when it found every stake saturating the cap.

**Refusals cost nothing and name the binding number.** A `grave` summit against a treasury of 4:
`A summit at 'grave' stakes costs 34 and you have 4`. Sabotage in peacetime:
`Sabotage is only open against somebody you are at war with`. Treasury unchanged after both.

**Nothing reached ground, forces or war.** Forty resolved attempts using the most destructive
verbs available (`shake`, `discredit`, `drain`): region ownership and control identical, unit
strengths identical, every `at_war` flag identical. Decision 57, asserted rather than asserted-to.

**The ledger is shared and holds.** Eight `grave` attempts in a single turn — an absurd volume,
attempted deliberately — moved the relation **6.0 total**, the one-turn ceiling. Stacking does
not buy anything.

**Rewind is exact.** `rewound turn 2 -> 1: 3 change(s) undone, 1 event(s) and 1 batch(es)
removed`, fingerprint back to its mark, zero surviving events. This section failed twice before
it passed, both times in the *harness*: the first version marked at turn 1 and rewound to 0, so
the turn counter doing its job read as a rewind fault; the second rewound to 1 without advancing
the clock and tripped `can_rewind`'s "only goes backwards" guard, so nothing was undone at all
and it failed identically. **The check now asserts `report.ok` before it compares fingerprints**
— a rewind that silently declines is the one failure a fingerprint comparison cannot tell apart
from a rewind that worked, which is worth remembering the next time one of these is written.

**Through the real turn loop.** Two tracked nations each got one attempt in an ordinary
`jump(days=30)` — Karthos a failure, Veyra a partial, both charged 16 — and the turn still
sealed clean with zero rejections. Same seed, same call key, same result on re-run.

**Status:** 1051 tests pass, 1 deselected (`live`). `ruff check .` clean. **Schema still 9, no
migration.** All three gathering-storm fingerprints unchanged (`39ae6fe5…`, `01e34d10…`,
`8401ab82…`) and all six per-section hashes identical — gambits are play-time, not seed-time.

### Playthrough 23 — Modern Day, **REAL MODELS**, seasonal turns, the gambit channel live

`scratchpad/rmpt_gambit.py`. Two firsts in one run: the gambit channel against real models,
and **the first live run at a span other than 30 days**. Russia vs Ukraine, 91-day turns
(span 3.03), 6 turns, both belligerents tracked — PT20/PT21's setup with exactly two
variables changed, so the findings read against those runs.

**It was run twice. The first run fired zero gambits, and that was the point of running it.**

#### Run 1 — twelve declines out of twelve chances, and two reasons why

Not one gambit fired in six turns. The models were not at fault; the *view* was.

**The target roster was alphabetical.** `build_gambit_view` did `others[:12]` against
`other_nations`, which returns the whole living world `ORDER BY n.name`. So every nation, on
every call, was offered Afghanistan, Aland, Albania, Algeria, American Samoa, Andorra, Angola,
Anguilla, Antarctica, Antigua and Barbuda, Argentina, Armenia — and **Russia, at war with
Ukraine, was never once offered Ukraine.** Six of the twelve declines named the list outright:

> *Russia:* "spending it chasing Andorra or American Samoa would be theatre, not strategy"
> *Ukraine:* "none supply weapons, sit on the front, or hold sway over the peace terms"
> *China:* "Andorra, Antarctica, Armenia, and the like — bear on the matters that concern Beijing"

**And the regard beside each name was a defaulted zero.** The line read
`getattr(other, "score", 0)`; the attribute on a `NationBrief` is `relation_score`. Nothing
raised, nothing logged — the default just served every model a flat 0 for every nation, and
several reasoned from it out loud ("regard sits at zero across the board, meaning no
established relationship worth leveraging"). **A wrong number that never raises is worse than
a crash**, and this one survived 22 unit tests and a full mock playtest because every one of
them asserted on the *rules block*, not on the roster.

Both fixed (decision 59): ranked by war, then border, then treaty, then `abs(regard)`, with
the reason shown beside each name and direct attribute access so a rename breaks loudly.
Russia's roster afterwards opens
`- **Ukraine** [ukr] (at war with you, your neighbour): your regard for them is -95.0`.

#### Run 2 — the channel works, and restraint is real

| | |
|---|---|
| chances (6 turns × 2) | 12 |
| proposed an attempt | 3 |
| — resolved | 1 |
| — turned away by the engine | 2 |
| **chose to attempt nothing** | **9 (75%)** |
| invented a kind / stake / evidence flag | **0 / 0 / 0** |

**Models do not spam the channel.** 75% of chances passed up *by choice*, with reasons that
read like a staff and not a dice-roller — China: "this is the kind of impatience Beijing does
not reward". Across both runs, **15 proposals and not one hallucinated kind, stake or evidence
flag**; the closed vocabulary held completely.

**The one gambit that resolved is the best evidence in the run.** Russia proposed `sabotage`
against Ukraine at `serious` stakes — a war-only kind, used correctly, the moment the roster
finally showed it an enemy — and cited `hostile_ground` among its evidence. **That flag is
worth −0.12.** The model reported a circumstance *against its own odds* rather than claiming
all seven, which is exactly what "report what is true — claiming all of them does not help"
was written to produce. It failed at 45%, and paid the 16 anyway.

**The cost discipline lands without being enforced.** Six declines quote the treasury figure
against the stake table before proposing anything — Germany at 0.1 and 0.6, China at 5.1,
India at 2.0: *"Treasury stands at 2.0, less than even a minor undertaking's cost of 6."* Not
one of those became a wasted proposal. That is decision 11's pattern working as designed —
the cost table is in the briefing, so the model checks its purse itself.

#### Finding: a third undisclosed bound, fixed but not yet re-observed

**Two of the three proposals ever made died on a rule the briefing never stated.** `sabotage`
and `incite` are war-only; the rules block listed their blurbs and said nothing about it. So
Ukraine aimed `incite` at Belarus and China aimed it at India, both in good faith, both
refused with *"Incite unrest is only open against somebody you are at war with"*. The refusal
message reads correctly — it names the real limit — but by then the proposal is spent. **67%
of all attempts were lost to a bound that costs one generated clause to disclose.** Fixed in
decision 60; the constraint is now generated from `requires`, so it cannot drift. **Not
verified live** — that would need a third run, and the finding is already established.

#### Pacing, at a non-monthly span for the first time against real models

- **War ran in rounds.** `span 3.03 -> 3 rounds of 1.01 months`, producing 5–9 contests a
  turn against the 2–4 a monthly turn produces. The front narrated through the season rather
  than leaping: Donets'k contested and occupied on turn 1, Sumy taken by turn 6.
- **The validation caps scaled, and the briefing said so.** Quoted at 91 days:
  `economy 45%, manpower 76%, military_strength 61%, treasury 152%` against the monthly
  `15% / 25% / 20% / 50%` — with the text "Across this turn (91 days, so 3 months' worth)".
  Decision 54 observed live at last.
- **Rates compounded rather than multiplied.** Russia's economy climbed
  +0.61, +0.51, +0.42, +0.35 across the first four turns — *decelerating* as it approached its
  industrial ceiling, which is compounding; a linear reading would have produced four equal
  steps. It then re-accelerated (+1.34, +1.60) once Russia's completed programme raised the
  ceiling, which is the loop working end to end.
- **`build_days` ticked at the season.** Russia's `uncrewed_systems` ran 120 days remaining
  after one 91-day turn, from 211.

**Health:** 12 turns across two runs, **zero aborts**. Run 1: 6 sealed, 0 rejections. Run 2:
4 sealed, 2 partial, 2 rejected batches — both ordinary world-sim overreach (a DIP-002 swing
cap, an ECON-001 move above the *scaled* 45% cap), neither gambit-related. No proposal was
refused or trimmed by the gambit caps in either run.

**Status:** 1055 tests pass (4 new), `ruff check .` clean, **schema still 9, no migration**,
all three gathering-storm fingerprints and all six per-section hashes unchanged.

### Playthrough 24 — the gambit affordability investigation (MOCK)

PT23 found real staffs declining gambits at 0.1–5.1 treasury against a `minor` stake of 6, and
two readings fit: Modern Day's economy is too thin, or the stakes are overpriced. They imply
opposite fixes, so this measured instead of guessing. `scratchpad/balance_gambit.py`,
`balance_builder.py`, `balance_confirm.py`.

**It was neither.** The answer is a third thing, and the hunch — including mine — was wrong.

#### The economy is not thin

All 16 Modern Day powers, 8 turns, silent models so every treasury movement is the engine's:

| span | in structural deficit | mean treasury below the 6 stake | lowest treasury seen |
|---|---|---|---|
| monthly (30d) | **0/16** | **0/16** | 8.9 (North Korea) |
| seasonal (91d) | **0/16** | **0/16** | 10.3 (Iran) |

Mean treasuries run 13 (Israel) to 130 (China); nets are positive everywhere, +0.58 to +11.29
monthly. **16/16 can afford a `minor` on their average treasury, 15–16/16 a `serious`, 8/16 a
`grave`** — which is the spread the stake table was designed for: everyone can try something
small, half the world can try the big one. Decision 41's solvency calibration holds at the
seasonal span too, which had never been checked.

#### The stakes are not overpriced

With gambits attempted every turn, **77% of attempts are affordable** even for an aggressive
builder. The turns a developing power cannot reach for a gamble are the guns-butter-schemes
tension working, not a wall.

#### It was the sampler

`_run_gambits` did `tracked_nations(...)[:2]`, and `tracked_nations` is `ORDER BY name`. So the
channel asked **the same two alphabetically-first powers every turn for the life of the game.**
In PT23 run 2, once Focus promoted China and Germany they took both slots from turn 2 onward and
**Russia and Ukraine were never offered a gambit again** — the two belligerents, the two nations
on the map with the most to scheme about, locked out by an `ORDER BY name`.

**This is the same bug as decision 59, in a different place, found three weeks of work apart.**
That one sliced a name-ordered roster and offered Russia Andorra; this one slices a name-ordered
roster and asks Germany forever. Both were invisible because a name-ordered list looks like data
rather than like a choice.

It also **manufactured the balance question this investigation was opened to answer**: the two
powers the channel kept asking happened to be mid-development and short of cash, so their
treasuries read as "nobody can afford a gambit" while the world at large was solvent throughout.
The evidence for a thin economy was a sampling artifact.

Rotating by turn (decision 62): lowest treasury across the watched powers went from
`46, 51, 3, 12, 3, 13, 6, 7` to `46, 66, 37, 53, 35, 78, 54, 78`, and the number of distinct
powers the channel reaches over 8 turns went **2 → 6**.

#### What did change, and what deliberately did not

**Changed:** the tracked-agent briefing now states that an undertaking is paid out of the same
treasury the build orders are about to spend, and that it is asked for *after* those orders are
charged — quoting the nation's actual treasury and what it currently affords (decision 63). The
two are separate model calls, the build call comes first, and nothing in it mentioned the second.

**Deliberately not changed:** no reserve is imposed on `build_orders`. `_choose_development`
keeps a 1.4× reserve for the auto-builder, and extending it to agents was the obvious fix and the
wrong one — a power that would rather have the steelworks than the option is making a real
decision, and the engine has no business overruling it. What it lacked was an *informed* one.
Decision 34 took the same view: the gauge, not the rebalance.

**Also not changed:** the Modern Day seed figures and `budget.py`. Nothing in the measurement
justified touching them, and the earlier solvency calibration is intact.

**Status:** 1058 tests pass (4 new, one of them verified to fail against the old slice), `ruff
check .` clean, **schema still 9, no migration**, all three gathering-storm fingerprints and all
six per-section hashes unchanged.

---

### Playthrough 25 — the nuclear layer, on the real 2026 map (MOCK)

`scratchpad/pt_nuclear.py`, kept. Six sections against a freshly built Modern Day world, and
the three the brief asked for are 3, 4 and 1–2 respectively: **deterrence holding fire, a
launch resolving, and the automatic reply firing back.** Mock only; spends no quota.

#### 0 — the arsenals are on the map

Nine powers armed, and nobody else:

```
chn 18/triad  fra 12/triad  gbr  8/triad  ind  9/triad  isr 5/missile
pak  8/missile prk 3/missile rus 40/triad  usa 38/triad
```

What each could still fire *after* absorbing a first strike — the number the whole design
turns on — comes out of the delivery tier plus the boats each actually has at sea:

```
chn 17  fra 11  gbr 7  ind 8  isr 2  pak 4  prk 1  rus 38  usa 36
```

Britain keeps **7 of 8**; North Korea keeps **1 of 3**. Neither number is written down
anywhere: they fall out of `survivability` reading `unit WHERE kind='submarine'`, which is why
sinking boats is now a strategic act.

#### 1–2 — deterrence, and what it is worth in turns

The pressure penalty for invading a homeland, measured on real provinces:

| ground | penalty |
|---|---|
| Adygey (Russian core) | **0.180** — the ceiling |
| Donets'k (Ukrainian core) | **0.000** |

Which is the 2026 map reading correctly on its own terms: the live war is a nuclear power
invading a non-nuclear one, and the deterrent is on the side that is not being invaded.

Then the same Russia–Ukraine campaign, ten monthly turns, identical in every respect except
one — the front re-derived each turn, the same pushes declared, the same mock providers:

| Ukraine | provinces taken in 10 turns | pressure left standing |
|---|---|---|
| as the scenario ships | **9** | 0.00 |
| with a triad (24 warheads) | **3** | 2.38 |

Three provinces still fell. **Hard, not impossible** is the target and this is what it looks
like; a term with no ceiling would have said the war on the map cannot happen.

#### 3 — a staff declines to launch, and says why

The interesting one, and it is deliberately not scripted to the answer. The mock staff
*parses its own briefing* — the sentence beginning "Who could answer you today, and with how
many warheads after absorbing your first strike" — and declines when that list is non-empty.
Nothing in the script knows what MAD is.

Russia, tracked, at war with a nuclear Britain, offered eight strikeable regions:

> Russia attempted nothing: the briefing says the reply is automatic and that United Kingdom
> (7) would still be able to make it. There is no version of this where we are better off
> afterwards.

`report.nuclear` empty, Russia's forty warheads untouched. **The restraint came from a
sentence the engine put on the page, not from an instruction anybody gave** — and the fact
that a regex could act on it is the actual test: a discoverability fix nobody can parse is a
comment in a prompt. This is decision 11's pattern at its most expensive instance yet.

#### 4 — a launch resolving, and the reply

Seeds searched rather than assumed, because the point is that it is *rolled*. Russia fires two
warheads at Donets'k with a triad, 88% chance of getting through:

```
population   2.72 -> 1.09      development 1.00 -> 0.40      garrison 5.2 -> 1.6
Ukrainian stability   42.0 -> 32.0
Russian legitimacy    44.0 -> 32.0
average regard for Russia, world-wide   -0.6 -> -20.5
```

256 diffs in one batch — the province, its neighbours' fallout, every formation standing in
it, the two governments, and **every living nation's regard for the launcher**, which is the
taboo expressed as something the engine does rather than something a briefing asks for. The
caps held: population floored above 30% of what it was, nothing erased outright.

Then, in the same turn, with no model call:

```
note: 2 of ukr's warheads destroyed on the ground
ukr -> r_rus_moskva, 1 warhead(s), struck
```

Ukraine was at `aircraft` tier with six warheads — a first strike caught two of them on the
ground and **one still got to Moscow.** That is the asymmetry the tiers exist to express, and
it is the answer to "was a first strike worth it": Russia spent two warheads, lost twelve
points of legitimacy, turned every state on the map against it by twenty, and took a warhead
on its capital. Against a *triad* opponent it would have been worse.

#### 5 — rewind is exact

An exchange at turn 3 of a real Modern Day world: 499 diffs across 2 batches. Fingerprint
before ≠ after; fingerprint after rewinding to turn 2 is **identical** to before. Nothing new
was needed for this and that is the point of asserting it — every effect flows through a diff,
so a nuclear exchange undoes exactly like a battle.

#### Status

**1096 tests pass** (35 new in `tests/test_nuclear.py`, 3 new in `tests/test_migrations.py`),
`ruff check .` clean, `scratchpad/pt_modern.py` still all-pass. **Schema 10**, migration
additive and forward-only, fresh and migrated worlds match column-for-column. All three
gathering-storm seed fingerprints and all six per-section hashes **byte-identical** to the
commit before this work.

#### Two things left open, and one worth watching

- **An in-progress Modern Day save migrates to a world with no arsenals** (decision 64). A
  fresh one seeds the nine. If Alex has a Modern Day game running, it will need restarting to
  get its warheads.
- **The interception term reads the air layer, and the air layer is still data.**
  `penetration_odds` uses the defender's air-domain strength over the struck region plus
  `integrated_air_defence` — which is honest about what exists, and finally makes that
  technology do what its name says. When Part 3 of the design note gets built, this is the
  term it takes over.
- **A launch writes ~250 diffs on a full map**, because world-wide outrage is not capped. That
  was chosen over a cap: a partial application would be the mechanic quietly not working, and
  the social cost *is* the deterrent. Worth watching if diff volume ever becomes a problem.

---

### Playthrough 26 — the nervous neighbour, and NATO waking up (MOCK)

`scratchpad/pt_alliance.py`, kept. Two mechanics that were missing from the same place: a
map where nothing outside the five tracked powers could react in its own voice, and a NATO
whose Article 5 text was seeded into `commitment` and read by nothing.

#### A — a bystander promoted by a war next door

Russia opens three fronts in Ukraine (`r_ukr_donets_k`, `r_ukr_sumy`,
`r_ukr_zaporizhzhya`), discovered from the map rather than scripted. **Belarus** is the one
power bordering that fighting and not in it, and Focus promotes it with the reason shown
verbatim to the player: *"a war it is not part of on 1 of its borders"*. Neither belligerent
is scored as a bystander.

**One observation worth recording, because it is not what the term does and looks like it
might be.** Belarus scored 37 against Russia's 34 on that pass. That is not the new term
misbehaving: `rank_candidates` is **player-relative**, the player is the United States, and
the USA is not in this war — so Russia's `+100 at war with you` and `+80 fighting you` never
fire and it is scored on recent activity alone. The pre-existing `war_with_tracked` term
(+60) corrects it on the very next pass: **Russia 102 vs Belarus 45**. Both belligerents end
up tracked. Nothing here was introduced by this change, and the playtest asserts the
invariant that is actually true rather than the one that reads well.

#### B — attacking a NATO member invokes the pact

Russia declares on Germany. In the same resolution:

```
Russia declared war with no provocation; the world noticed.       (-8 legitimacy)
A defensive pact binds France, United Kingdom, Turkey, United States
  to Germany; each now has cause against Russia.
```

Four engine-authored `casus_belli` events, one per ally. Allied regard for Russia:
`fra -62 -> -92`, `gbr -82 -> -100`, `tur +20 -> -10`, `usa -78 -> -100` — thirty apiece,
clamped at the floor where it was already near it. **Nobody is at war who was not before.**

Then the half that makes it a mechanic rather than a mood: the United States chooses to
enter, and **pays no legitimacy for it** (60.0 -> 60.0) because it holds a casus belli,
while Russia has already paid (44.0 -> 36.0). That eight-point asymmetry is the entire
deterrent, and it is the reason a casus belli beats an auto-join here.

It also **composes symmetrically without being told to**: the American declaration fired
the DPRK–Russia pact in the opposite direction, handing North Korea cause against the
United States.

#### C — disclosed before the fact

The referee's briefing now lists every active pact, generated from the rows the engine
reads: NATO (5 members, Article 5 text), US–Japan, US–ROK, DPRK–Russia, China–DPRK. With the
consequence stated in the same breath — attack a member and every other member may declare
on you for five turns *without paying the legitimacy an unprovoked war costs*. A tracked
member is additionally told it is bound by one.

#### Status

**1127 tests pass** (14 new), `ruff check .` clean, **no schema change** — Article 5 reuses
`EventKind.CASUS_BELLI`, which the closed CHECK enum already carried. All three
gathering-storm fingerprints and all six per-section hashes byte-identical. Rewind is
untouched: every effect is an ordinary diff under `Role.ENGINE`.

#### What this does not do

- **A pact does not make anyone fight.** If no model acts on the casus belli, the pact's
  whole mechanical effect is the relation collapse. That is deliberate, and it is the thing
  a real-model run should be watched for: whether a staff reads the disclosure and acts.
- **The bystander term needs a contest, not a declaration.** A war declared and not
  prosecuted promotes nobody until the first province is contested.
- **`focus.rank_candidates` remains player-relative.** A war between two AI powers on the
  far side of the map still ranks its belligerents only through `war_with_tracked` and
  recent activity. That predates this work and is the next thing to look at if AI-vs-AI
  wars feel unattended.

---

### Playthrough 27 — the Security Council convenes, and is often ignored (MOCK)

`scratchpad/pt_unsc.py`, on the real 2026 map. Six sections, 23 checks, all passing. Mock
only — spends no quota. The whole layer exists for section C.

**A. the body is on the map and is not a country.** The Modern Day seed carries one
organisation: the United Nations Security Council, `unsc`, 16 members, standing 50, with
China, France, the United Kingdom, Russia and the United States permanent. It holds no
region, fields no unit, appears in no power's roster, takes no Focus slot — and holds a
directed relation to all 209 powers, which is the entire point of representing it as a
`nation` row. Every guard the design note listed was checked, not assumed.

**B. jurisdiction.** Germany declares war on Poland with no casus belli: one trigger,
`unprovoked_war`, severity 2. The same declaration in a world where the engine had already
minted Germany a `casus_belli` produces *no* trigger at all. The body's jurisdiction is
`_has_casus_belli` — the engine's own judgement of provocation — so the two cannot drift.

**The veto, which on this map is not hypothetical.** Russia is a permanent member. A
session against Russia is vetoed by China and Russia, does nothing, and costs the Council
6 standing for having tried (50 → 44). Nobody wrote that; it falls out of the seeded
regard matrix.

**C. a patron is the difference between a body that can act and one that cannot.** The
first cut of this playtest accused Germany and could never carry anything, through 200
seeds. That was not a bug worth fixing — it was the mechanic being sharper than the
playtest: France (+70), Britain (+56) and the United States (+68) all hold vetoes and all
like Germany, so a NATO member is structurally beyond the Council's reach. Iran and North
Korea are likewise protected, by China and Russia. Belarus is protected by nobody. So the
demonstration uses Belarus, and the contrast is now an explicit check, because it is the
truest thing the layer expresses.

Against Belarus a condemnation carries at 95%, standing goes 50 → 53, and average member
regard for Belarus lands at −15. **The authority loop, in both directions:** a first
resolution watered down costs 2 (50 → 48); the same offender back in front of the body
three turns later, having ignored it, costs 10 (50 → 40). Being defied costs more than
passing gains, so a body that keeps sitting and being ignored declines — and the decline
is *measurable*, since standing feeds the odds: 93% at standing 40 against 95% at 95.

**D. what a resolution does.** `authorise_force` passed at 53% and handed all 15 other
members an engine-authored `casus_belli` — the same primitive as Article 5 — while putting
nobody at war and exempting the accused from being authorised against itself. A `sanction`
passed at 75% and dragged Belarus's economy 4.3 → 3.6 and treasury 15.0 → 7.0 over six
turns, then lapsed on its own with no sweep.

**The one thing this playtest found that was actually wrong** (decision 75): the sanction
drag was a flat subtraction, which took Belarus from 4.3 to the floor — a 77% collapse —
while the identical resolution against the United States would have been a rounding error.
Now a rate: the same resolution takes 17% off both, measured side by side in the run.

**E. rewind is exact.** The session and six turns of sanctions wrote 18 diffs; rewinding
past them restores the world byte-for-byte.

**F. the prompt states the stakes.** The session view names every permanent member with
its regard for the accused, states outright *“France, United Kingdom, United States will
veto anything against Germany”*, and explains what standing does and how it is lost —
generated from the tables the engine reads, per the discoverability pattern.


### Playthrough 28 — the air war, on both real maps (MOCK)

`scratchpad/pt_air.py`. Six sections, 22 checks, all passing. Mock only — spends no quota.
The first genuinely new subsystem since the contest model itself, and unlike the nuclear
and organisation layers it is not gambit content: it changes how combat resolves in both
eras.

**A. reach.** French, German and British aircraft all reach 3 regions; Britain can put
weight over 71 provinces and **41 of them are provinces it does not hold**, including
Belgium, Brazil and Canada. Nothing else in the engine crosses water without transports.
`unit_template.move_range` had said 3 for an air wing since the first commit and had never
once been read.

**B. superiority, unprompted.** France and Germany can both reach ten provinces. Ten air
battles are fought before anything on the ground moves, ordered by nobody:

```
  Tirol              France 1.75   Germany 6.07
  Alsace-Lorraine    France 2.43   Germany 6.07
  Baden-Wurttemberg  France 1.75   Germany 8.42
  ...
  Germany holds 9 skies, average margin 0.55;  nobody holds 1;  France holds 0
```

Nobody wrote "the Luftwaffe is stronger." It falls out of two air wings against one.

**C. what the sky is worth.** Over Tirol, held by Germany at margin 0.55: France attacking
into it takes −0.055 to the odds, Germany attacking under it takes +0.055, and the
resulting pressures are 0.244 against 0.756. Bounded at ±0.10 — about what favourable
terrain is worth, and less than surprise. Air decides how well a ground attack goes, never
whether it happens.

**D. strikes.** The comparison that makes the point is two raids rather than one:

```
  Germany strikes Alsace-Lorraine:   6.1 air strength projected, odds 72%, struck
    garrison 5.4 -> 1.3   (4.1 off)
  France strikes Baden-Wurttemberg:  1.7 air strength projected, odds 61%, struck
    garrison 23.6 -> 22.9 (0.7 off)
```

The size of a raid is the size of the air force flying it. Then the line that must not
move: Germany bombs one French province **twelve times** — 4 hits, 3 partial, 5 broken up.
The garrison is degraded, and the province does not change hands, no formation is deleted,
no formation reaches zero strength, no contest opens and no war starts. Aimed at the works
instead, it adds 6 days to a French armour build and takes development 1.0 → 0.6.

**E. interception.** The United States strikes Heilongjiang, 60 raids each way:

```
  Chinese air defence over it: 0.350 on fighters alone -> 0.500 with the programme
  no programme:      struck  6   degraded 11   intercepted 43
  programme done:    struck  5   degraded  3   intercepted 52
```

`integrated_air_defence` previously bought +2.0 strength on an air wing and +1.5 on a
destroyer — that is, it helped you attack. It now buys nothing at all except this. And the
same term defends against a warhead: a missile's odds of getting through Chinese air
defence are 0.320 against a bare tier value of 0.82.

**F. air is no longer infantry.** Parking a 30-strength air wing on Tirol moves the ground
strength of the battle there from 9.91 to 9.91.

**Rewind.** A full turn through `resolve_turn`: 10 air battles, 9 skies taken, 10 rows of
air control written. Rewinding restores every aircraft exactly and leaves no sky owned by
anybody.

**Three things the playtest found that were wrong**, all now decisions 76 and 81: margin
measured luck rather than force ratio; the strike weight divisor was more than twice too
large; and twelve raids rounded a 1.3-strength division to exactly 0.0.


### Playthrough 29 — provincial economics, on both real maps (MOCK)

`scratchpad/pt_economy.py`. Five sections, 19 checks, all passing. Mock only.

**A. a province is worth what a province is worth.** 101 distinct values across 375 land
provinces in 1936, from 0.25 to 52.8:

```
  Germany   richest: Silesia 6.89, Berlin 6.36, Saarland 5.83
            poorest: Pomerania 1.59, East Prussia 1.32
  Britain   richest: Greater London 9.35, North East 5.61, East Midlands 4.99
  the USSR  richest: RUS Central 5.0, RUS Urals 3.66, UKR Kharkiv 3.33
            poorest: Tajikistan 0.25, Kyrgyzstan 0.25
  Italy     richest: Lombardia 3.36, Liguria 2.1  poorest: Shabeellaha 0.26
```

Land area — the only geographic quantity in the repo — would have said the opposite of
all three of Silesia/East Prussia, Lombardy/Somaliland and Moscow/Siberia. 2026 the same:
Beijing 9.52 against Tibet 0.48, Moscow 7.04 against Kamchatka 0.41.

**B. map granularity is out of the economy.** The ground half of revenue was `0.25 ×
province count`:

```
  nation           econ  regions   old ground    now
  United States      96        1         0.25   6.34
  Italy              36       30         7.50   2.37
  Germany            66       10         2.50   4.35
  Soviet Union       58       21         5.25   3.83
```

Italy was drawing three times Germany's ground revenue on half its economy. Total revenue
for every great power in both eras lands within a point of where it was — the change is
entirely in *where* it comes from — and exactly one power of 219 runs a deficit at seed
(Japan, which already did, and by less than before).

**C. losing the right provinces hurts.** France takes Silesia, the Saar and Berlin — three
of Germany's ten provinces but 53% of its industrial ground. German revenue falls 18.84 →
14.83, **21% of income for 30% of the provinces**, and over eight turns the economy drifts
66.0 → 64.8 because the ceiling reads ground too. France, holding but not owning, collects
40% of what it took.

*This section is why the coefficients were re-split.* At the first calibration ground was a
quarter of revenue and the same conquest cost Germany 12% — a conquest that removes half a
country's industry and reads as a rounding error is one the economy did not notice.

**D. the public works, and why flat is right.**

```
  rail and roads in Silesia:      6.89 -> 9.89  (+44%)
  rail and roads in East Prussia: 1.32 -> 4.32  (+227%)
```

The same 45 treasury buys four times the proportional gain on poor ground. "Underdeveloped
conquered land is worth investing in" fell out of the numbers rather than needing a
mechanic.

**E. and it goes down.** A French infrastructure strike takes the Saarland 5.83 → 5.65 —
the aim that was a *guaranteed no-op* two commits ago, because its floor was the value
every province in the game was seeded at. Under French occupation it goes 5.65 → 4.89 over
six ungarrisoned turns, and **does not move at all** over the six after a division arrives.
Sanctions take 4.7% of German development over eight turns. Bombed to 0.3 and left
occupied, it bottoms out at 0.25 and stays there through 38 more turns.

**Rewind** restores every province exactly.

**One finding reported rather than dressed up as a pass** (decision 86): Hsinchu City seeds
at 0.43 — a fair share of the world's semiconductors — because Taiwan is a *minor* and a
minor's national economy is still `4 + area × 0.02`. The heartland table cannot fix it; it
redistributes inside a country and the country is the problem.


### Playthrough 30 — everything at once, **REAL MODELS**, Modern Day, seasonal turns

`scratchpad/rmpt_grand.py`. Ten turns × 90 days = 2.5 game-years, Modern Day, no human
player, five tracked powers (`rus`, `ukr`, `usa`, `chn`, `prk`). All three providers probed
green first time — claude `claude-sonnet-5` [MATCH], codex `gpt-5.6-terra` [MATCH], agy
`gemini-3.6-flash-medium` [UNVERIFIED, as always]. **The first live run at a span other
than the monthly one every constant was calibrated at.**

**80 model calls, 56 applied batches, 0 rejected batches, 0 failures, 0 arrears, 0 aborted
turns.** Twenty rejections, all of them legible refusals rather than errors.

#### The run

Russia opened with a seeded `push` and took Donets'k and Sumy in the first season. The
front moved to five occupied provinces by turn 3, Ukraine clawed one back at turn 4, and at
**turn 6 the war ended at a negotiated peace in which Russia annexed Kiev, Donets'k,
Mykolayiv and Sumy** — the settlement layer producing a partial victory rather than a
conquest or a stalemate. Turns 7–10 were an armed peace: China and the DPRK negotiating
supply arrangements with Russia, the United States and Ukraine coordinating support.

#### Three defects, all found by live play and all fixed

1. **`sanitize()` crash on the first air strike anybody proposed** (decision 87). Ukraine
   proposed an `air_strike` on turn 3 of the first attempt and the engine raised
   `TypeError: sanitize() takes 1 positional argument but 2 were given`. The gambit wrapper
   for the kind had **never once been executed** — every test called `air.strike` directly,
   and the mock playtest did too, so only a model had ever taken that path. Three tests now
   go through `apply_gambit`.

2. **The Council's annexation trigger was dead code** (decision 88). `triggers_this_turn`
   reads `territory_transfer` events; the peace path wrote one `peace_signed` event and put
   the annexation in a *report headline*. So Russia annexing four Ukrainian provinces in one
   settlement — the single most Council-worthy act of the run — convened nothing. The
   trigger had never fired since the day it was written.

3. **Every covert gambit that worked was silently refused** (decision 89). The stake diff
   was hard-coded `PUBLIC` while a covert success files a `SECRET` event, so rule INFO-002
   ("a covert cause cannot produce overt state") correctly refused the whole batch. Sabotage,
   bribery, stings and incitement landed **only when they failed**. Caught on Ukraine's one
   successful sabotage of the game. Deterministic, and nothing had noticed across four
   playtests, because mock runs never rolled a covert success with a real payload.

#### AIR — the system that behaved best, and the one that revealed a fourth flaw

152 air battles over the run, entirely unprompted. Russia's air arm is ~8× Ukraine's at the
front (45.7 projected against 5.8), and the map said so without anybody writing it down:

```
  turn 1   16 battles   rus holds 13 skies (margin 0.63), 3 contested
  turn 3   30 battles   rus holds 32 (margin 0.97), 4 contested
  turn 5   36 battles   rus holds 41 (margin 0.84), 1 contested
  turn 6+   0 battles   rus holds 41 ...
```

That last line is the flaw (decision 90): **the war ended and the skies never lapsed.**
Russia held air superiority over 41 provinces for four turns of peace, and any later war
fought there would have opened with a bonus won in a campaign two years dead. Air
superiority is now something you maintain — an uncontested sky loses 0.25 of margin a turn
and is nobody's after four quiet ones, then stops being written at all.

**No strikes were flown in the successful run** and no interception was exercised live: the
models declined nearly every gambit on treasury grounds (below). The strike path is now
covered by tests through `apply_gambit`, but its *live* behaviour remains untested.

#### NUKES — MAD held, and the briefing is not why

Nine armed powers, 141 warheads, **nobody fired, nobody threatened to fire.** The DPRK,
tracked and nuclear, referred to its deterrent seven times and always as a defensive
possession — *"its nuclear deterrent is non-negotiable"*, *"its deterrent is defensive"* —
and Russia explicitly bracketed it out of a trade deal: *"nuclear matters remain outside
the arrangement."* That is exactly the behaviour the layer wants.

**But it is not evidence the briefing worked.** No power was ever losing badly enough to be
tempted, the one war ended in a negotiated peace favourable to the nuclear side, and every
staff that declined a gambit cited *treasury*, not deterrence. The honest reading: **MAD was
never put under pressure.** A run that tests it needs a nuclear power facing collapse.

#### UN — never convened, for two separate reasons

Standing stayed at 50 for ten turns and no session was held.

- The **annexation** trigger was defect 2 above, so the one act that should have convened it
  could not.
- The **unprovoked-war** trigger reads `war_declared` events, and the Russia–Ukraine war was
  *seeded* rather than declared in-run. **The trigger set cannot fire on a war that was
  already running when the game started** — which is the shape of every scenario flashpoint
  in both eras. Flagged as decision 91, not fixed: it needs a design decision about whether
  a body convenes about an ongoing war it has never sat about, and that is Alex's call.

So the veto and authority dynamics are still only mock-verified (PT27).

#### ECONOMY — the added test

**Do staffs target valuable ground? Yes — unanimously, and slightly too well.** Every one of
the 32 tracked-power builds went into that nation's *most valuable* province: China → Beijing
(the highest-development Chinese region), Russia → Moskva, Ukraine → Kiev City, the DPRK →
P'yongyang, the USA → its single region. Not one build went anywhere else.

**But nobody said "because this province is worth more."** The stated reasoning is national,
not provincial: *"State planners prioritize workshops and light industry to deepen domestic
productive capacity"*. They pick correctly and explain it in aggregate terms — so the
behaviour is right and the *legibility* of provincial value is unproven.

**Does anyone develop land over guns? Yes, extensively.** Housing, schools, health, rail,
land reform, workshops, shipyards and steelworks were all laid down. The sharpest evidence is
Ukraine declining: *"The government defers new public works: the treasury cannot yet meet
the cost of the available programmes without sacrificing immediate wartime flexibility."*
A staff weighing public works against a war it is losing is precisely the tension the
economy layer was built for.

**Does the 40% occupation yield read as a pull to invest, or a tax? A tax.** Russia held four
to five Ukrainian provinces for five turns and built *nothing* in any of them — every rouble
went to Moskva. The proportional-gain incentive (decision 85) is real in the arithmetic and
invisible in play.

**Does development erode? Yes,** and visibly: Donets'k went 1.42 → 1.21 in the first season
alone under occupation (once per round of the seasonal jump), and Ukraine's total ground
value fell 4.5 → 2.5 by turn 3 before recovering to 5.1 after the peace.

**Concentration, not flattening.** Decision 85 worried that flat public works would flatten
the geography over a long game. The opposite happened: Beijing climbed 9.52 → **16.02**
because every Chinese build went there. The risk is capital-pumping, not levelling.

**Italy's correction was not tested** — Italy is a 1936 concern and this was a 2026 run.

#### Where the numbers now look off against real play

| what | measured | reading |
|---|---|---|
| gambit cost vs seasonal treasury | 16 of 20 gambit slots declined, **almost all citing treasury**: Russia 4.1, Ukraine 4.4, DPRK 1.9, China 0.6 | A gambit costs a flat 6 **whatever the span**, while upkeep is a flow and triples at 90 days. At seasonal pace the sharper tools price themselves out for everyone but the very rich. The strongest candidate for a pacing fix. |
| poor powers cannot climb | DPRK: economy 18.2 → 18.7, industry 10.8 → 10.8, ground 10.0 → 10.0 over 2.5 years | It never built anything: net +0.9 a turn against a 20-treasury entry price. The workshops rung (decision 10) is still too high for the poorest. |
| rich powers compound hard | USA 101 → 134, China 93 → 134; industry 60 → 92 and 55 → 93 | The build loop works, arguably too well: China nearly caught the USA in ten turns. |
| Antarctica builds workshops | development 13.09 → 16.09 | Decision 86's flagged land-area national economy, now visible in play. |

#### Health

| | |
|---|---|
| turns | 10/10 sealed, none aborted |
| model calls | 80 (8/turn, flat) |
| batches | 56 applied, **0 rejected** |
| failures | 0 |
| arrears | 0 of 243 powers, all ten turns |
| gambits | 4 attempted, 16 declined — **sparing, and every decline was a reasoned refusal** |
| partisan risings | 2 (Kiev City twice) |
| rejections | 20, every one legible |

**The rejections worth reading.** Two read wrong:

- `Ukraine: you are not at war with whoever holds r_ukr_sumy` ×3 — Ukraine trying to press
  into *its own* province. Technically true ("you are not at war with yourself") and useless
  as guidance.
- `Russia: Donets'k is not a region on this map` ×2, plus Kiev, Mykolayiv and Sumy — Russia
  naming provinces by **name** where the parser wants ids. Decision 11 fixed exactly this for
  the world-sim roster by printing ids; the tracked-agent objective view evidently still
  does not, and a staff spent four objectives a turn finding out.

The rest read correctly, including the best refusal of the run:
`Germany asked to begin 'uncrewed', which is not a thing this world builds. The closest is
'uncrewed_systems' -- name it exactly and it will be laid down.`

#### Seasonal pacing

Ten 90-day turns produced no arithmetic pathology: no arrears anywhere, no runaway growth,
fronts that moved at a plausible rate, and erosion/decay applying once per round rather than
once per turn. The one span-sensitive thing that *is* wrong is the flat gambit cost above.


### Playthrough 31 — the Council convenes about the wars it was born into (MOCK)

`scratchpad/pt_unsc_inherited.py`, on both real maps. 11 checks, all passing. Mock only.
The fix for what PT30 found.

**A. the wars it was born into.** Turn 1, no prompting:

```
  modern-day       seeded wars [(Russia, Ukraine)]     -> ongoing_war / Russia   (severity 2)
  gathering-storm  seeded wars [(Ethiopia, Italy)]     -> ongoing_war / Italy    (severity 2)
```

and the record each is read from is the engine's own turn-0 history: *"Russia invaded
Ukraine in February 2022, expecting Kyiv to fall in days…"*, *"Italy invaded Abyssinia
(October 1935). The League voted sanctions, omitted oil, and the campaign continues."*
**Nobody occupies anybody at turn 1 of either era**, so the occupier heuristic this could
have used would have had nothing to go on — and could as easily have accused Ethiopia.

**The sharpest thing the 2026 map does with it.** The Council's first act of the game is a
resolution about Russia, and Russia holds a veto:

```
  -> vetoed; vetoed by ['China', 'Russia']      standing 50 -> 44
```

It convenes, it is blocked by the accused, and it pays six standing for having tried.
Nobody wrote that; it falls out of the seeded regard matrix meeting the new trigger.

**B. once, not every turn.** Turns 2–12 raise nothing at all. The dedupe is exact rather
than a cooldown — a session cites its trigger through `event_cause`, so the war it sat
about is identified, not approximated.

**C. escalation still bites.** Russia annexes a province on turn 6 and the Council is back:
`[('annexation', 'Russia')]`.

**D. and new wars still trigger.** Belarus declares war on Lithuania on turn 4 of a fresh
world: `[('unprovoked_war', 'Belarus'), ('ongoing_war', 'Russia')]` — both arms coexisting,
gravest first, one session a turn, so the new war and the old are heard in order rather than
at once. A war declared *this* turn is never also counted as inherited: the turn boundary is
what separates the two queries.

**Rewind** is exact: 2 diffs undone, 1 event removed, fingerprint restored.

*Two scaffolding slips of mine, both in the rewind section and neither in the engine:* the
baseline was first taken at turn 2 and compared against a world correctly rewound to turn 1,
and then taken from a freshly created world, which says turn **0** until something sets it.
`world.turn_number` and `world.world_date` are in the fingerprint and a rewind legitimately
moves them. The rewind was exact all three times.


### Playthrough 32 — why nobody could afford a gambit (MOCK, diagnostic)

`scratchpad/diag_gambit_cost.py`. Not a playthrough so much as a measurement: PT30 declined
16 of 20 gambit slots at a seasonal span, almost all citing treasury, and there were four
candidate causes with four different fixes. Non-tracked powers are driven by
`autonomous_investment`, which is deterministic and needs no provider, so PT30's spending
behaviour reproduces exactly at four spans without spending a token.

**The two hypotheses that were wrong.**

*Upkeep is not the drain.* Revenue and upkeep are both flows and scale together, so net
income **rises** with span for every solvent power. Ruled out by arithmetic.

*Powers do not structurally reinvest to zero.* Measured over ten turns at each span, the
engine-driven powers end every turn holding 20–70:

```
                     purse at gambit time   minor stake affordable
  week                        16.4 – 43.0            50/50
  month                       21.1 – 50.2            50/50
  season                      20.2 – 35.8            50/50
  year                        34.2 – 70.7            50/50
```

That is `autonomous_investment` keeping a 1.4× reserve (`_INVEST_RESERVE`) so it never
spends a nation into arrears. **The master AI already solves this problem for the powers it
drives.** The model-driven path has no equivalent — which is the finding.

**The two that were right, and they are different problems.**

*Reachability is an ordering problem* (decision 94). The build call came first and spent the
purse; the gambit call was offered the residue. Not a shortage — a sequence.

*Time-consistency is a flatness problem* (decision 93). At most two attempts a turn at a
flat 6:

```
  span      cost   attempts/turn   scheming per elapsed month
  week       6.0         2                    51.43
  month      6.0         2                    12.00
  season     6.0         2                     4.00
  year       6.0         2                     0.99
```

**A 52x swing decided purely by how the turns are sliced.** Note the direction: scaling the
cost *up* with span is the time-consistent move and would have made PT30's affordability
strictly worse — which is why the two fixes had to land together.

**After.** Cost, magnitude and per-field ceiling all scale, month as identity:

```
  span      cost   magnitude   per unit spent   scheming per month
  week       1.4      0.35          0.250            12.00
  month      6.0      1.50          0.250            12.00
  season    18.0      4.50          0.250            12.00
  year      73.0     18.25          0.250            12.00
```

and measured at the moment the decision is now taken — before the build call, against a
tracked power the master AI never touches:

```
  span      need    purse at decision        affordable
  week       1.4     81.6 (min  15.3)          30/30
  month      6.0     93.7 (min  16.1)          30/30
  season    18.0    126.2 (min  18.3)          30/30
  year      73.0    279.0 (min  28.3)          26/30
```

The four year-span misses are all **one power: Poland, whose economy is 4.5**, and even
Poland affords it from its fourth turn. Russia, China, India and South Korea afford it 6/6
at every span. Poland's economy is decision 86's land-area minor-economy bug, and a power
that size failing to fund a year-long intrigue campaign is the right answer anyway.


## PT31 — the laboratory's first live outing

Two real-provider runs: **Modern Day, 16 turns, 126 calls, 1202s**, with Russia and Germany
carrying scripted dispositions; and **the sandbox, 12 turns, 830s**. All three CLIs probed
live first (claude MATCH 3.4s, codex MATCH 5.3s, antigravity UNVERIFIED 12.4s — agy does not
attest, which is expected and long-standing).

### Health

Clean. 16 turns produced 14 `sealed` and 2 `partial`, no aborts. 80 batches applied against
3 rejected; 791 events; 13 problems in total. Of the 81 calls that reach the routing log,
**codex attested MATCH 67/67** and antigravity `UNVERIFIED` 14/14, with **zero failed
outcomes**. Median durations: tracked agent 13.1s, world sim 23.0s. The war ran: provinces
changed hands on six separate turns, Moldova lost its government, supply attrition bit
repeatedly on both sides of the Dnieper, and trade lanes were cut and reported.

### One real bug, and it is the gravest class

**Fingerprinted state written outside the diff pipeline does not rewind.** The
`rewind-fingerprint-mismatch` detector fired on turn 14 — recorded `72539cc3…`, replayed
`b3b396ba…`. Reproduced deliberately, in isolation, away from provider randomness:

* a **coup on turn 3, rewound to turn 2, leaves the junta in the chair**;
* a **commitment signed on turn 3, rewound to turn 2, is still active** and
  `relation.treaty_status` still reads `non_aggression`.

Both are mine. Decision 101 put `leader` in `_FINGERPRINT_TABLES` with the argument that *a
rewind past a coup would replay the turn under a different administration* — correctly
identifying the risk — and then wrote the chair with direct SQL, reasoning that "the rewind
replaying that same turn" would restore it. **A rewind does not replay.** Decision 120
documented the commitment half as a known gap and argued the important property was that
the two stay consistent with each other; that is true and does not stop the digest moving.

A mock run rewinds byte-identically, which is why no test caught it: the mocks return empty
batches, so no coup and no treaty ever happen.

### Three defects in the instrument, all now fixed

The run was as much a test of the laboratory as of the engine, and the laboratory came off
worse.

**The actor prefix was being read as evidence.** Objectives refusals read `Belarus: blr is
not a region` — the nation making the request is named at the front — so
`known-name-rejected` matched *that* and concluded the world contained the refused place.
**Two of the three modern-day findings were this, and the sandbox's only finding was too.**
The body is now searched, not the whole line.

**Substring matching.** `aurelian_straits` is an invented place, and "aurelia" appears
inside it. Whole-word now, the same rule the reference boundary uses and for the same
reason.

**A deferred objective was counted as an impossible one.** *"borders none of your territory
yet; the objective stands"* is the **accepted** path. It reads like a refusal because
`turn_engine` files deferred objectives under `report.rejections` — which is worth fixing in
the engine and was not the detector's to guess at.

Re-scored after the fixes: **Modern Day 0 findings, sandbox 0 findings** — and the rewind
mismatch reappears the moment the replayed fingerprint is supplied, which the old bundle did
not serialise (a fourth, smaller defect, also fixed).

A false positive here is worse than a miss. It is the instrument manufacturing the finding
it exists to detect, in a run where the engine behaved.

### What the detectors did not catch

Nothing false-negative was found by hand, but the run exposed a gap worth naming: **126
calls were reported and 81 reached the routing log.** The gambit, organisation and freeform
roles do not call `log_decision`, so 45 model calls left no attestation trace — invisible to
`call-with-no-trace`, which reads batches rather than the log.


## Engine-accepted output, kept separate from provider success

Decision 1 reverted the world-simulation pin on latency and crash rate and set the bar for
keeping it on something else entirely: *revert unless agy's **output** is materially worse.*
Nothing measured output. This does.

**The distinction is the whole point.** A provider that answers every call and proposes diffs
the rules refuse is *worse* than one that fails loudly: the turn looks healthy, the call is
paid for, and the world does not move. The comparison that produced the original re-pin could
not have seen that, because the only number it had was latency.

So `Accepted` splits them and never lets them blur. `answered` and `failed` are provider
facts; `batches`, `batches_applied`, `diffs_proposed` and `diffs_severed` are engine facts,
and the accepted-output rate is built only from the second group. Two decisions in it are
worth stating:

* **a role that proposed nothing has no rate, rather than a perfect one.** `None`, not 1.0 --
  scoring silence as perfect would let a provider that says nothing outrank one doing the
  work, which is the failure mode agy's empty-answer window produces.
* **severed diffs are counted.** A partial apply reports as *applied*, so without them a
  batch that lost half its writes looks exactly like one that lost none.

Tallied per role, because one provider serving several roles is the normal case and a single
number would say nothing about which of them is producing usable output. Recorded on
`TurnRecord`, so a tape can be compared against another provider's afterwards -- which is
precisely what the five recorded claude runs on the shelf are for.


## "Fulfilled" means the promise is discharged, and most promises here cannot be

A non-aggression pact is not *completed* by a year of peace; a supply arrangement is not
*finished* by a month of cargo. Those end by being broken, by expiring, or by being
terminated. A status saying otherwise **deletes a standing obligation while announcing that
it was honoured** -- and everything downstream believes it, because `sync_treaty_status` and
`honour_terms` both read `status` and neither asks what kind of promise it was. PT34d is the
case: a model marked a standing supply agreement `fulfilled` one month in, the engine cut the
trade route, and two other powers sent envoys asking why.

The classification is semantic rather than a whitelist of kinds:

* **standing clauses** -- `no_war`, `open_trade` -- are promises you go on keeping. Nothing
  discharges them.
* **one-off clauses** -- `cede_region` -- are done once, and `cede_region` is done when the
  ground has moved, read with the same query `_violation` uses to decide the opposite. One
  fact, read once, rather than two notions of "handed over" that can disagree.
* **standing kinds** cover what the terms cannot say. A `trade_agreement` with no recorded
  terms implies nothing deliberately, and a `military_access` grant is inert by design, so
  neither has a term to read -- and both are permissions that *continue*.
* **an unclassified clause blocks.** A clause in the schema's CHECK and in neither set is one
  nobody has decided about, and deciding by default is how a standing obligation gets closed.

**A mixed agreement stays active while any standing term remains**, however many one-off
terms are complete -- the case a naive version gets wrong, since a treaty that cedes a
province *and* promises peace is not finished when the province moves.

**DIP-005 governs the engine, because it is the only writer left.** `commitment.status` is
engine-only now, so no model can reach it, and nothing in the engine sets `fulfilled` yet
either. So this is the *specification* of a fulfillment derivation that has not been written,
made enforceable before it is written rather than after it goes wrong -- with a test that
fails the day something does start recording agreements fulfilled, so its author reads this
rule first. That is the intended order of events.


## The world simulator goes back to agy, measured over 391 calls instead of four

It was moved to claude after PT33 on the strength of **four calls** -- 22s and 37s against
agy's 16s and 22s on one Modern Day prompt -- and that entry conceded at the time that *"it
is not enough to characterise the distribution."* A night of recorded runs put **391**
world-sim calls through claude across five playtests, and the distribution it characterises
does not support the swap:

* **roughly six times agy's latency** on the same call, not the 1.5x four calls suggested;
* **and no better reliability.** PT34g died at turn 6 to twenty-seven consecutive
  `claude exited with code 1` -- about 5% of that run's calls -- which is the same *shape*
  of failure agy was moved off for, on the same every-turn critical path.

A provider that buys neither speed nor reliability does not justify the swap, so the pin is
reverted. What replaces "hope it does not flake" is explicit:

**One retry per call.** Three attempts is right for a rate limit, which clears; it is wrong
for agy's empty-answer window, which does not clear in fifteen seconds and costs the turn a
minute of waiting to discover that. `_invoke` takes a per-call retry budget and the world
simulation asks for one.

**A breaker, and not a per-call fallback.** agy's failure is *episodic*: exit 0,
`"status":"SUCCESS"`, no answer, for a window of minutes, and then it is the fastest provider
on the table again. A per-call switch answers each of those by quietly using somebody else --
which from outside is indistinguishable from the provider working, so the window never
reaches a report and the pin can never be judged again. `Breaker` takes three consecutive
failures to trip, says so **once** in the turn report, and stays tripped for the session
rather than flapping back on the next lucky call.

**The attestation guard changed sides rather than being deleted.** The old test required the
world simulation to be on a provider that reports which model served the call, and said a
future re-pin "should have to argue with this". It did, and lost on the numbers. The role is
back on a provider that cannot attest and that is now a recorded cost -- with the guard
rewritten to require that the **fallback** attests, so a session that trips the breaker
becomes verifiable exactly when reliability is in question.

**What is measured from here is engine-accepted output, not provider success.** A provider
that answers every call and proposes diffs the rules refuse is worse than one that fails
loudly, and the comparison that produced the original re-pin could not have told them apart.
Alex's bar: revert unless agy's *output* is materially worse; broadly comparable means the
revert is correct.


## An agreement is broken by breaking it, not by writing down that it is broken

Decision 120 made `relation.treaty_status` engine-only because a model could set the derived
column to say something the agreements did not. **It left the thing the column is derived
from writable by three model roles**, and that was the larger hole: a power could retire an
agreement it no longer liked by writing one word, and every consequence of tearing up a
signature went with it -- no `treaty_broken` event, no 35 regard with each injured party, no
legitimacy charge, no casus belli, no `breached_by_nation_id` to say who had done it.
`breach()` is the whole of that cost, and it was optional.

**Three independent live instances in three consecutive runs**, which is what settled it:

    PT34g  turn 1, 1936   ger  commitment 8 (Locarno)  status  "pending" -> "broken"
    PT34e  turn 1         ind  non-aggression pact     status  "active"  -> "broken"
    PT34d  turn 6         ind  trade agreement         status  "active"  -> "fulfilled"

The first is the sharpest: **Locarno**, guaranteed by Britain and Italy, with France and
Italy as co-parties -- the Rhineland remilitarised on turn 1 against three guarantors, for
nothing. It is also the *right instinct for the era*, which is what makes the hole
load-bearing rather than theoretical: the first thing a real model does with 1936 is reach
for it.

`commitment.status` and `commitment.resolved_turn` are now ENGINE-only. **Every legitimate
writer was already the engine**, which is why the suite went green on the permission change
alone: `accept_commitment` activates and DIP-004 checks it, `expire_commitments` retires,
`breach` breaks. This removes exactly one route -- the fourth, with no consequences attached.

**What a model does instead is what it should always have done: break the promise.** The
breach path is derived from state, not reported -- go to war with somebody you have a
`no_war` term with and `assess_breaches` finds it, charges it, hands the injured party a
casus belli and puts a line in the turn report. That path was proved end to end through
`jump()` earlier in this session. The refusal message names it, the way decision 120's does.

**DIP-004 changes job rather than retiring.** It was written to stop one signatory activating
an agreement on its own, which was a guard on *models*; with the column closed it is a guard
on the **engine's own** activation path, which is where `accept_commitment` writes ACTIVE. Its
three tests now run as `Role.ENGINE` for that reason.

*Twenty-two tests: every model role against every status value, the refusal naming the route,
prose still writable by diplomacy, activation and expiry untouched, a real breach landing all
five consequences -- and the two that matter most, that
`a-broken-agreement-names-its-breaker` cannot be reached by a model any more and that the
engine's own route to `broken` writes the attribution in the same batch as the status, so
there is no ordering in which one lands without the other.*


## PT34g: 1936's first recorded run, and Germany tore up Locarno on turn 1

The era with 488 regions and 219 powers had never had a live run of any kind -- PT31, PT33
and PT34a-f are all Modern Day and the sandbox. It has one now, and it is a *partial*: five
turns sealed, then the provider layer went down.

    5 sealed turns, then 7 consecutive aborted attempts at turn 6
    64 recorded calls, 28 of them failures: 27 x "claude exited with code 1", 1 x codex
    quota exhausted
    replay: 12/12 attempts reproduced the recorded fingerprint, 0 unused, 145.9s

**The abort was quota, not the engine**, and the degradation path behaved exactly as
designed: the world simulation failed, the turn was retried, each attempt was marked
`aborted`, nothing was written, and the world stayed at turn 5. A provider outage cost the
turn and not the world. **The tape replays anyway, aborted attempts included** -- so even a
run killed by exhaustion is a faithful regression fixture.

### Germany remilitarised the Rhineland by writing one column

    turn 1  tracked_agent ger  commitment 8  status  "pending" -> "broken"  [applied]

    commitment 8: military_access, parties fra/gbr/ger/ita
    "Locarno (1925): the Rhineland is to remain demilitarised, guaranteed by
     Britain and Italy. Untested."

No `treaty_broken` event, no 35 regard with each of three guarantors, no legitimacy charge,
no casus belli for France, Britain or Italy, and nothing recording that Germany did it.
`breach` was never involved.

This is the **third** independent live instance of the same hole in three consecutive runs --
after India tearing up its pact with Pakistan (PT34e) and a model writing a standing supply
agreement to `fulfilled` (PT34d). It is also the most pointed: the first thing a real model
does with the 1936 scenario is tear up Locarno, which is the correct instinct for the era and
which the engine grants for free. **The hole is load-bearing, not theoretical.**

*The invariant added hours earlier caught it on turn 1 and on every turn after, which is what
it is for.* The rule change itself stays parked -- see the log.

### And the supply finding, confirmed live

Italy's East African garrisons -- Maekel, Debub, Debubawi Keyih Bahri, later Galguduud -- are
cut off and going short from **turn 1**, exactly as the static measurement predicted. Amhara
was retaken by partisans on turn 3. The 1936 map behaves as the sweep said it would.


## PT34f: the first non-monthly run, and the pacing arithmetic confirmed live

Every live run this project has ever made was monthly -- the one span where all of the
pacing arithmetic is the identity function -- so none of decisions 44-47 had ever been
observed against a real model. PT34f is eight turns of **ninety days**: two years of game
time, recorded, and it replays.

    8 turns of 90 days, 66 recorded calls, 0 failures, 1140s
    rewind to turn 6: recorded 402492bbec6c / replayed 402492bbec6c  MATCH
    replay: 8/8 turns reproduced the recorded fingerprint, 0 unused, 37.6s
    detectors: nothing suspicious    invariants: nothing reported

**And it produced a number that can be checked against the monthly runs.** Saudi Arabia is
the first power whose industry cannot get what it needs:

    pt34d  monthly (30d)   first short at turn 12   = 12 months elapsed
    pt34e  monthly (30d)   first short at turn 12   = 12 months elapsed
    pt34f  seasonal (90d)  first short at turn  4   = 12 months elapsed

**Identical in elapsed time across a threefold change in turn length**, in two independent
monthly runs and one seasonal one, on a mechanic nobody tuned for the comparison. That is
the trade layer's flows -- production, shipment and consumption are each `over(x, span)` --
doing exactly what the pacing layer promises.

It is also the distinction the sanction bug fixed earlier tonight turns on, now visible from
both sides: **a flow scales linearly with the span and a rate compounds.** Trade is flows and
was right; the sanction drag was a rate multiplied like a flow, and at a yearly turn it went
negative.

**A balance observation, parked.** Two years in, the shortage is no longer one Gulf state:
by month 18 France, India and Japan are short, and by month 21 China and Germany are too --
oil and rare in almost every case. The handoff's least-confident list says trade endowments
were *"calibrated against demand ratios, never played through a long war"*. They have now
been played through two years of one, and the world drifts steadily toward shortage. Whether
that is the intended pressure or an endowment that does not keep up is Alex's call, and the
tape is on the shelf to re-measure against.


## `atlas check`: the invariant checker could only be pointed at a laboratory

Thirteen things that must never be true of a world, and **nothing outside
`scratchpad/lab_run.py` ever asked them.** The checker is inside the shipped package, the
detectors call it, the tests call it -- and a game somebody actually played was the one world
it was never run against.

    atlas check --world-id modern-day --explain

    modern-day at turn 10: 13 invariant(s) checked at rest
      [a-broken-agreement-names-its-breaker] commitment 11 (non_aggression_pact) is broken
          and nothing records who broke it
      [nobody-besieges-their-own-ground] rus holds Donets'k and is besieging it (55%);
          that contest can never end

Read-only by construction -- every invariant is a query and the checker never writes -- which
is what makes it safe to point at a live save rather than at a copy, and is pinned by a test
that compares the fingerprint either side of a run.

Exit codes are for a shell: **0** sound, **2** something to look at, **1** no such world.
*Found something* and *could not look* being different answers is the part worth having a
test for, because a command that conflated them would report every missing save as sound.

`--phase` exists because the checker is phase-aware and a save on disk is always between
turns; `rest` is the default and the strictest.


## Why no war started: the escalation ladder has no top rung

Three runs, thirty-six turns, two powers at **-98 regard** with dispositions written to want a
border settled, and no new war. The obvious reading is that models are war-averse. **It is
the wrong reading, and the right one is worth knowing.**

`declarations` -- the field a war is declared through -- is offered to **`Role.WORLD_SIM`
and to nobody else** (`diff._batch_schema`). A tracked agent's schema carries
`military_objectives` and `build_orders`. Its briefing says so plainly: *"You cannot seize
territory or declare war directly, and you never decide who wins a battle; the engine
resolves all of that."* So this is disclosed design rather than an oversight -- and a `push`
objective cannot substitute, because `pushes_as_contests` drops a push whose defender is not
already an enemy. **A tracked power that decides on war has no way to have one.**

What India did instead, in order, is the whole ladder it was given: a counterintelligence
sting, three `expose` undertakings, an ultimatum on turn 9 demanding talks on the line of
control, Pakistan's refusal on turn 10 -- and then nothing, because there was no next rung.

**And the one role that could declare did not, with everything it needed to.** The
world-sim prompt for that turn contains India's disposition verbatim (*"has decided the line
of control question must be settled in this decade"*), the -98 regard, the ultimatum and the
refusal. It is 76,791 characters of exactly this situation, and it declared nothing.

That is the observation to weigh: **the sole author of wars is the one actor with no stake in
one.** The world simulation is a referee describing everybody; a grievance belongs to a
power. Whether that is right -- or whether a tracked power should be able to declare war
against the same legitimacy charge everyone else pays -- is a design decision with real
consequences either way, and it is Alex's.

*Left as an observation with its evidence rather than acted on. Nothing here is broken; the
engine did exactly what it says it does.*


## PT34e: nobody went to war, and a treaty was torn up for free

Two nuclear-armed powers, a seeded non-aggression pact between them, dispositions on both
sides written to want the line of control settled, and twelve turns to do something about
it. **Neither attacked.**

What they did instead is the interesting part. India ran a counterintelligence sting and
three separate `expose` undertakings -- leaked evidence of Pakistani corruption, then of army
support for militants. Pakistan sent an envoy to Beijing to firm up Chinese backing. Three
negotiations, all of which Pakistan declined on the substance (*"New Delhi's message is vague
-- 'revised in our favour' will not do"*). Regard between them ended at **-98 and -95**, and
neither fired a shot. That is a run where deterrence, covert action and diplomacy did what
they are for, and it is the third consecutive run in which **no model chose to start a new
war** -- every war in every run tonight is one a scenario seeded. Worth knowing before the
nuclear layer is judged for having never been used: nothing has yet got close enough to use
it.

**And on turn 1, India tore the pact up for nothing.**

    turn 1  tracked_agent ind   commitment 11  status  "active" -> "broken"   [applied]
            event: "India declares the non-aggression pact with Pakistan exhausted
                    and no longer binding"

`breach` is what makes tearing up a signature cost something: the `treaty_broken` event, 35
regard, 6 legitimacy, a casus belli for the injured party, and `breached_by_nation_id` so the
record says who did it. **All of it is skipped if the status simply changes**, and
`commitment.status` is writable by three model roles. Pakistan got no grievance; nothing
recorded that India had done anything; the agreement was gone.

This is the *same question* PT34d parked from the other side, where a model wrote a standing
supply agreement to `fulfilled` after one month and silently cut its own trade route. Two
independent live instances, two different statuses, one hole: **`commitment.status` is the
column the whole treaty lifecycle hangs off, and a model may write it to anything.** Decision
120 made `relation.treaty_status` engine-only for exactly this reason, and schema 18 stopped
a single signatory activating a pending agreement -- the *breaking* direction was never
closed.

**Parked for Alex, with both instances**, because it is a rule change about what a model may
write rather than a defect in what the engine does with it.

**Detected now, though.** A new invariant, `a-broken-agreement-names-its-breaker`: a treaty
recorded as broken by nobody went through no breach and cost nobody anything.
`a-breach-names-a-party` does not catch it -- that asks whether a *named* breacher is a
signatory and says nothing about a breach that names nobody. Run against the two bundled
worlds, it reports PT34e's pact and stays quiet on PT34d.


## The world-sim re-pin, measured over 391 calls instead of four

Phase-2 handoff 7.2 named this itself: *the world-sim re-pin's latency claim rests on four
calls. 22s and 37s on claude against 16s and 22s on agy. That is enough to overturn the
65-83s figure that looked disqualifying; it is not enough to characterise the distribution.*

Tonight's four recorded runs are 391 calls, and the tapes carry a duration per call. On the
**same prompt** -- Modern Day's world simulation, 76-77k characters -- read off the tapes:

| tape | provider | n | median | max | ok |
|---|---|---|---|---|---|
| pt33/modern-day | antigravity, gemini-3.6-flash-medium | 12 | **19.5s** | 30.0s | 12/12 |
| pt34-modern-day | claude, claude-sonnet-5 | 17 | **109.1s** | 179.8s | 16/17 |
| pt34d-modern-day | claude, claude-sonnet-5 | 13 | **131.4s** | 214.7s | 12/13 |
| pt34e-modern-day | claude, claude-sonnet-5 | 11 | **127.3s** | 184.2s | 11/11 |

**The re-pin made the one call every turn depends on roughly six times slower**, and the
four-call sample that justified it (22s, 37s) is not within sight of the 41-call reality.
It is also not free of the failure mode it was meant to avoid: 2 of those 41 world-sim calls
crashed outright (`claude exited with code 1`), so claude is ~95% here against agy's 12/12.

For scale, the rest of the engine is fast and was never the problem: over the same 391 calls,
`tracked_agent` on codex is 12.7s median across 210 calls at 100%, `parser` 3.6s, `diplomacy`
6.3s, `gambit` 14.0s. **The world simulation is about two thirds of a turn's wall clock**,
because the five tracked agents run in parallel and it does not.

**This is Alex's call and it is parked, not acted on.** The re-pin's narrow ground was that
*an unpredictable provider on the one call every turn depends on is a worse risk than a slow
one*, and that argument survives the measurement -- what does not survive is the belief that
the slow one is only slightly slower. The honest statement of the trade is now: **agy at ~20s
with episodic flake, against claude at ~120s with a ~5% crash rate.** The caveat is real and
stated: these are different days under different load, and the agy figures come from one
earlier session's tape.


## PT34d: open_trade opens a route, and a detector finds a nine-turn seam

Two results, and the second is the one worth reading.

### The positive case, closed

Phase-2 handoff 5b: *five negotiations ran live and all five counterparts declined, so
`open_trade` has never opened a route against a real model.* Germany and India, given
dispositions with a reason to sign:

    turn 4  deu -> ind; ind: "This is exactly the arrangement India wants"
            recorded trade_agreement/pending [open_trade]  -- ind ships rare, 0.185/month
    turn 6  India begins shipping rare to Germany under their agreement   <- route opened
    turn 7  the rare shipments from India stop: the agreement behind them no longer stands

Negotiated, extracted, termed, activated, **opened**, and closed again when the agreement
stopped standing. The whole path, live, against real models.

**And the models noticed the ending before we did.** India's own staff wrote the agreement's
status to `fulfilled` on turn 6, one month into a *standing* supply arrangement -- which the
engine correctly reads as no longer active, so it cut the route. Germany and the United
States both dispatched envoys the next turn asking why the shipments had stopped. `fulfilled`
means *the promise is discharged*, which is right for a cession or a tribute and meaningless
for a standing obligation, and nothing refuses it. **Parked for Alex** rather than fixed:
narrowing which statuses a model may write to which commitment kinds is a rule change, and
the run that found it is on the shelf to test it against.

### The seam: a siege of ground you are standing on

The laboratory reported `impossible-objectives` -- *Russia named ground it cannot act on 6
times*. Triaged, and it is not the instrument being wrong. Russia's briefing said, every turn
for nine turns:

    Donets'k [r_ukr_donets_k] -- you hold it, you are 55% of the way to taking it

Russia controlled Donets'k and a contest was open with Russia as the attacker. It could never
end: `captured = pressure >= THRESHOLD and held != attacker`, so once the attacker holds the
ground the capture branch is unreachable and the row accumulates pressure for the rest of the
game. The staff read the line, ordered a push, and the objectives channel refused it with
*there is nothing there to push into* -- six times. **Two parts of the engine describing one
province in contradictory terms**, which is the definition of a seam this laboratory exists
to find, and it took a detector to notice.

The cause is an asymmetry between the two paths into the resolver.
`objectives.pushes_as_contests` drops a push whose defender is the pushing power itself; a
clash **reported by the world simulation** was never checked. It is now, and refused with a
reason.

Also a new invariant, `nobody-besieges-their-own-ground`, because the resolver refusing to
*open* one is a claim about a proposal and the invariant is the same claim about the world --
which is what catches a path that writes one some other way.


## A twelfth detector: the engine refusing its own batch

Three times now this project has found the same bug one site at a time -- `breach` charging
a violator more legitimacy than it had, the arrears charge taking a bankrupt minor below
zero, the occupation drag doing the same to a spent occupier. Every one was refused by
ECON-003, which names no diff and therefore condemns the **whole batch**, and two of those
batches covered the entire world.

Every one of them was also *visible in a live run* and nothing was looking. So there is now
a detector for the class rather than three tests for its instances.

**A model batch being refused is the system working** -- the rules exist to refuse models,
and a run with several is a healthy run. An **engine** batch being refused is the engine
proposing something it had already decided was true, and there is no reading of that which
is not a defect. The detector reads `diff_batch` for `proposer_role = 'engine'` and
`status = 'rejected'`: ground truth the applier wrote, which a phrasing change cannot hide.

*The closure test added earlier tonight caught it immediately for having no demonstration,
which is what it is for.* It has one now, plus the half that decides whether anybody reads
it: three refused **model** batches must not make it fire.


## A year of sanctions was harsher than the twelve months it replaced

`apply_sanctions` scaled its rate **linearly** in the span -- `1 - rate * severity * span`
-- where every other per-turn rate in the engine goes through `pacing.compounded`. The two
agree at a month and diverge as the turn grows:

    yearly turn, severity 2:  treasury multiplier  -0.46 linear vs 0.21 compounded
    yearly turn, severity 3:  economy  multiplier  -0.10 linear vs 0.32 compounded

A negative multiplier meets `max(0.0, ...)` and `max(FLOOR, ...)`, so **a single yearly turn
emptied a sanctioned power's treasury outright**, and at severity 3 took its economy to the
floor -- where twelve monthly charges leave 21% and 32%. That is precisely the bug
`atlas.engine.pacing` exists to prevent, and the function that prevents it was one import
away.

Monthly behaviour is unchanged, which is what the layer was tuned at and what the third test
pins.

**And the docstring's calibration was quoted as absolutes** -- *"about a point of economy and
two and a half of treasury a month"* -- for rates that are proportional. True of a power with
an economy near 33 and misleading for anybody larger. Restated as what it is: 3% of the
economy and 6% of the treasury a month at severity 1.0, which over the eight turns a measure
stands is -21% and -39%.


## The engine authored two more penalties its own rules refuse

Group 2 found `breach` charging -6.0 legitimacy against a violator that had 2.0 left, and
ECON-003 -- which holds both scores inside 0-100 and **names no diff, so it condemns the
whole batch** -- threw the entire breach away. That fix was applied where it was found. It
was never swept for, and there were two more.

**The arrears charge is the worse of them, because its batch is the world.**
`collect_revenue` writes every nation's revenue, upkeep and legitimacy drift in one batch.
An unclamped 1.5 stability charge against one bankrupt minor sitting at 0.5 takes it to
-1.0, ECON-003 refuses the batch, and **not one treasury in the world moves that turn**.
Reproduced. In a 242-nation world running for many turns this is not an edge case, it is a
matter of time.

**The occupation drag is the same shape.** `assess_unrest` charges every occupier at once,
so an occupier down to 0.2 taking a 0.5 drag cost every *other* occupier its drag as well,
and the only thing that happened was a problem line. Reproduced.

Both now charge what there is, which is also the truer reading and the one `breach` and the
coup path already use: a government at zero stability cannot be shaken further by missing
one more payroll. The penalty is unchanged for every government that can pay it, which is
what the second test of each pair pins.

*The class is now three sites deep and each was found separately. What would find a fourth
is a rule that engine-authored adjustments to a bounded column must be clamped at the point
of authorship -- worth considering, and not built tonight.*


## PT34 -- Modern Day, sixteen turns, and the first tape that replays

**The regression fixture the phase-2 phase was built around is no longer empty.** Section 5c
of that handoff said it plainly: `403943b` changed model-facing prompts, so both PT33 tapes
miss on replay, and *there is currently no live tape that replays*. There is now.

    16 turns, 135 recorded calls, 1 failure, 2703s
    rewind to turn 14: recorded 3d7882d339f5 / replayed 3d7882d339f5  MATCH
    replay: 16/16 turns reproduced the recorded fingerprint, 0 calls unused, 40.8s

Sixteen for sixteen, no leftovers, no misses, in forty seconds and no quota. That also
re-proves the digest change earlier tonight from the other side: a tape recorded under the
sorted-column fingerprint replays under it exactly.

**What the run exercised, live, that mocks cannot reach.** A Security Council session
convened on turn 1 and **declined to table a resolution** -- the first live outing of the
organisation layer's *refusal* path. A government fell on turn 1 (Curaçao). Russia proposed
and ran a sabotage undertaking on turn 3. Partisans took Zaporizhzhya back on turn 14. Five
provinces changed hands. Every detector came back clean and the invariant checker reported
nothing on any of the sixteen turns.

**Four negotiations, four refusals -- again.** `rivals` dispositions produce declines that
are correct for the dispositions, which is exactly what section 5b said and why it says the
positive case needs two powers with a reason to agree. That is what the `concord` profile
added tonight is for.

**Two balance observations, neither chased.** Turns 6-16 lead with *Ukraine in
Khmel'nyts'kyy is cut off and going short*, three lines a turn, because `significant()`
ranks supply attrition above everything a quiet turn contains -- true, and repetitive.
And `rus -> m_maf (oil): the lane is cut` recurs for four consecutive turns: a route reads
as cut every turn it *is* cut, which is a state readout rather than an event, and one
Ukrainian fleet on a chokepoint appears to cut a great power's seaborne trade with everyone.
Both are readings of a report rather than defects in a mechanic.


## The eleventh case: the sandbox root was a sandbox, and the source tree was a spelling

The phase-2 handoff put `config/paths.py::_contains` third on its riskiest-surfaces list --
*ten cases pinned; look for an eleventh.* There are two, and both are the check being right
by accident rather than for a reason.

**The runs root itself passed both guards.** `_contains` accepts identity, so
`assert_isolated(runs_root())` and `assert_grantable(runs_root())` returned it. A call
working in the root can read **every other sandbox in it** -- another nation's briefing,
another role's prompt, another turn's answer -- and the central architectural claim of this
project is that models never see each other's output. The grant version is worse: `agy`
ignores `cwd`, so `--add-dir` is the only containment it has, and one argument would hand
it the whole run. Nothing does this today, because `SandboxDir.create` always makes a child.
A guard that only holds because nobody has called it wrong is not a guard.

**And ATLAS's own tree was forbidden by spelling.** `_default_forbidden()` named it
`~/OneDrive/Desktop/Atlas Game` -- the absolute path it happens to sit at -- so moving the
checkout, or cloning it to review a branch, would silently stop the source tree being a
forbidden root. `project_root()`, which computes it correctly, was exported and used by
nothing. That is the same mistake this module refuses for the MSIX redirect, pointing the
other way: containment decided by how a path is written rather than by which directory it
is. It is now found rather than spelled, and deduplicated, because on this machine the two
are the same place and a list naming it twice reads like two rules.

*The allowlist was doing the real work throughout -- both are second-layer failures -- which
is the argument for having two layers.*


## An act cannot break an agreement that did not exist when it happened

`_aggressor` took the most recent engine-authored `war_declared` between the parties and
never asked when the agreement was made. So a pact signed **during** a war was contradicted
the moment it became active, and the breach was charged to whoever had declared that war
several turns earlier.

**And it is farmable.** A power under invasion can grind its enemy down by repeatedly
offering pacts it signs: each one lands, each one is instantly "broken" by the original
declaration, and each costs the aggressor 35 regard and 6 legitimacy. Reproduced -- three
signatures floor the regard at -100 and take 18 legitimacy, for one declaration on turn 1.

The floor is the signature and it is `>=`, because declaring war on the day you signed is a
betrayal and the sharpest one there is. Going to war after signing is unchanged, which is
the half that had to survive.

An agreement between powers already at war is now **left standing and reported**, every turn
it recurs -- the same correction the failed-breach path got, for the same reason. The message
distinguishes the two cases that used to read alike: a war with no author, and an agreement
signed by two powers who were already fighting. The check was also widened past `BREACHABLE`,
because a `trade_agreement` carrying an explicit `open_trade` term is breachable through its
terms and is not in that set -- so this case was silent for exactly the agreements a
negotiation makes.

*Both seeded eras re-checked: nine active agreements in Modern Day and six in 1936, no
breaches and no new problem lines, and the invariant checker still returns nothing at SEED.*

**Parked for Alex:** whether the engine should refuse to *activate* an agreement whose terms
the world already contradicts, rather than accepting it and reporting the oddity. That is a
rule about what a negotiation may produce, and it would change what a model is allowed to
settle.


## A route that could not be opened was reported to nobody, and told a story anyway

Phase-2 handoff, section 5e, one line: *`honour_terms` returns silently when its batch is
refused; nothing is reported.* It is the same defect the breach path had, and it turned out
to have the same second half.

**Silent.** `honour_terms` returned `[]` on a refused batch -- the identical answer it gives
when there was nothing to do. So a route that could not be opened was reported to nobody,
and because the function is idempotent and re-derives what the world *should* look like
every turn, it was re-attempted and swallowed again on every turn afterwards: an invisible
loop that could run for the rest of the game. It now returns a `TermReport`, and
`turn_engine` extends the problems into the turn's rejections where the player reads them.
Retrying is kept, deliberately -- the reason can clear -- exactly as it was for breaches.

**And it left the narrative behind.** The event is written before the batch because every
diff must cite one, so a refusal left *"Karthos begins shipping oil to Aurelia under their
agreement"* in the history with no route under it. Every later briefing quotes the history
back and the digest carries it forward, so both staffs would go on planning around a route
that does not exist. `_unrecord` was already in this module, written for this shape of
failure on the breach path; it is now used on this one.


## An order given twice in a turn was one decision reported twice

`atlas.sim.rng` states the rule everything deterministic here rests on: a `call_key` must
identify **the decision**, never the order it occurred in. Three places in the player's own
order channel broke the other half of that rule -- two genuinely different decisions
sharing one key -- and because a shared key hands back the *same number*, the symptom was
not randomness in the wrong place. It was the total absence of it.

**Espionage was 45% once a turn, not 45% an attempt.** `espionage:{actor}:{target}` is the
same string for every attempt, so a second order against the same rival inherited the
first's answer: a network that got in once emptied the whole safe for free, and one that
failed could not have got in whatever else it did that turn. Reproduced over twelve seeds
-- four orders returned exactly four secrets or exactly none, never one, two or three. The
key now names **the report being gone after**, which is what the decision actually is. A
single order still succeeds 45% of the time; only which seeds land where has moved. Keying
on an attempt *ordinal* instead would have made every repeat an independent free roll, and
espionage costs nothing, so that would have made spamming the order the way to play it.

**A player paid three stakes for one roll.** `orders._gambit` passed no `index`, so three
sabotage orders drew `gambit:sabotage:a->k#0` three times: three successes or three
failures, never a mix, each charged 6.0. The dispatch loop now counts how many identical
orders came before -- over the order's **own identity**, not its position in the list, so
an unrelated order typed first cannot move anybody's roll, which would be the ordinal trap
one level up.

**And the per-turn magnitude ceiling was per-order.** `apply_gambit` bounds how far one
field may move in a turn by accumulating into the ledger it is handed, and this path handed
each order a fresh one. Three undertakings therefore got three whole ceilings. One ledger
per call to `apply_order_effects`, which is one per turn, is the fix and is what the
tracked-agent path already did.

*Two things checked and found sound while looking: `#r{round}` is in a contest's key, so a
yearly turn is not one month repeated twelve times; and the coup roll is drawn once per
nation per turn however long the turn is.* **Parked for Alex:** the `#{index}` on the
tracked-agent gambit, nuclear and strike keys is that agent's *position in the roster*. It
is redundant -- the actor is already in the key and a nation proposes one undertaking a
turn -- and it quietly couples one power's roll to whether another power was promoted.
Harmless today because a rewind restores the roster exactly, so a replay still reproduces;
worth removing, and removing it moves every future gambit roll, which is a change to make
deliberately rather than at 3am.


## The digest depended on how the file got here, not on what is in it

`fingerprint()` selected columns in `PRAGMA table_info` order, which is the order the table
was **built**. A column added by `ALTER TABLE ... ADD COLUMN` lands at the end of a
carried-forward database, and `schema.sql` declares three of them in the middle:
`nation.industry`, `region.home_nation_id` and `construction.project_kind` -- all three
fingerprinted. So an existing save and a world built fresh from the same seed, holding
byte-identical state, hashed differently.

`schema.sql` states the requirement itself, beside `unit_template.build_turns`: *a fresh
world has to match a migrated one column for column*. It was not met. Nothing noticed
because `test_migrations` compares schemas as an unordered **set** of (table, column), and
the engine only ever compares a world against itself -- but `lab_replay.py` compares digests
**across databases**, replaying a recorded run into a world it builds itself, and the first
carried-forward save it met would have reported a regression that is not one.

Sorted, which makes the property structural instead of a coincidence of declaration order.
Every `fingerprint()` value moves; the two recorded sidecars were already historical, since
`403943b` changed the prompts and both tapes miss on replay regardless. **Seed fingerprints
are unaffected and verified unmoved** -- `seed_fingerprint.py` hashes the `WorldSeed`
dataclass, not a database.

Three attacks that did not land, now tests rather than beliefs: row insertion order (every
`_FINGERPRINT_TABLES` entry orders by something that, with the `world_id` its scope fixes,
covers the primary key); separator injection, since `repr` escapes  and ; and type
confusion, since None, 'None', '7', 7 and 7.0 are five digests.


## Group 2 verified against its guarantees, and two gaps closed

Alex asked for each Group 2 guarantee to be confirmed with evidence. Two of the three held;
breach atomicity had **two real gaps** behind the part that was fixed.

**A failed breach was reported to nobody, and retried silently for ever.** `breach` returned
`None`, `assess_breaches` appended nothing, and because the commitment stays `active` and the
war goes on, the same failure was attempted and swallowed again on every subsequent turn — an
invisible loop that could run for the rest of the game. It now reports through
`BreachReport.problems`, which `turn_engine` extends into `JumpReport.rejections` and the
player reads. Retrying is kept deliberately, because the reason can clear; retrying *silently*
was the defect.

**And the root cause was the engine authoring a penalty its own rules refuse.** ECON-003 holds
legitimacy inside 0–100, so a violator with 2.0 left could not be charged 6.0 and the whole
breach was thrown away over the overshoot. It charges what there is now — which is also the
truer reading: a government at zero legitimacy cannot be disgraced further by tearing up one
more signature. The reproduction that used to leave a false betrayal on the record now simply
breaches the treaty and takes the last 2.0.

**The cassette's last-line rule was one notch too generous.** An interrupted write leaves JSON
that stops mid-token; a line that *parses* and is missing a required field was written in full
and is wrong wherever it sits. Tolerating that at the end would let a genuinely damaged final
entry pass as an interrupted one. Only an unparseable final line is excused now.

**And "unused entries fail a replay" lived only in a scratchpad harness**, which is a rule with
no test. `ReplayProvider.unused()` and `.exhausted()` are library behaviour now, named by role
so the message says *which* call went missing rather than how many.


## Six smaller corrections from the phase-2 review (schema 18)

**`relation.treaty_status` is engine-only now.** Decision 120 made it the derived form of what
`commitment` says and then left it writable by three model roles — so a model could set the
column to say something the agreements did not, and seven consumers read the column rather
than the agreements. The invariant checker reports the drift; this stops it.

**An agreement takes effect when every party has accepted it.** `commitment.status` was
writable, so one signatory could flip a `pending` agreement to `active` on its own and
`sync_treaty_status` would hand both sides a standing treaty the others had never agreed to.
"Pending" meant nothing enforceable. Acceptance is now per party
(`commitment_nation.accepted_turn`, schema 18) and rule **DIP-004** refuses activation while
any party's is missing — unless *nobody* has accepted, which is every agreement in every world
written before this and is left alone.

**A name and an id that disagree are refused.** `Germany [fra]` is not a reference with a typo
in it; it is a writer who has lost track of which country they mean, and taking the id because
ids are authoritative would act on France while the sentence says Germany. It comes back
*ambiguous* naming both, because "these are two different things" is the useful message.

**The gazetteer sorts before it truncates.** It kept an arbitrary eight of nine and could drop
a different one after an unrelated insert.

**Two capability descriptions were untrue, and one handler was worse than its description.**
`fortify` advertised a treasury cost and spends none. `disband_units` claimed to refund
nothing while returning half the manpower — and advertised `region` and `unit_kind` targets
its handler **ignored**, so the manifest's own worked example, *disband the garrison in the
north*, stood down the weakest formations anywhere in the country. The handler honours both
now; the two cost lines say what actually happens.

*The list-agreement tests could never have caught these: they check that every capability
reaches the parser, the council and a handler, which is a different question from whether the
description is true. The new tests compare each claim against what the handler does.*


## DESIGN NOTE — who an event happened *to* — **BUILT (schema 17)**

*Built as sketched, at schema 17. Twelve tests, and the reproduction is the one that matters:
a non-aggression pact charged to the wrong country because an unrelated war was the more
recent declaration. `_aggressor` now asks for a declaration whose **target is also a party**,
and a casus belli is a specific complaint about a specific country rather than a five-turn
licence against anybody.*

*Adopted at every site the sketch named: war declarations, all three casus-belli writers, the
Council's session event, cessions (beneficiary and ground), and the gazetteer, which now
prefers places the events **say** they are about over places their prose happens to name.*

*The compatibility rule held everywhere and is the reason nothing moved: an event with no
participants falls back to the behaviour it had. Seed fingerprints unmoved — `2c90c4dc…`,
`6f6e863a…`.*



**Nothing below is built at the time of writing.** From the Codex review of phase 2, and the
one architectural item in it: several separate bugs are the same missing fact.

### The hole

`event.actor_nation_id` records who *did* something. Nothing records who it was done **to**.
Every consumer that needs the other end guesses, and the guesses are wrong in ways that are
individually small and collectively serious.

**Reproduced.** Karthos and Aurelia hold a non-aggression pact. Aurelia declares war on
Karthos on turn 1 — Aurelia has broken the pact. On turn 2 Karthos declares war on *Veyra*,
which has nothing to do with it. `_aggressor` takes the most recent `war_declared` authored by
*any party to the pact*, so it finds Karthos's declaration against Veyra and **blames Karthos
for the pact Aurelia broke**: Karthos loses 35 regard and 6 legitimacy, and Aurelia — the
actual violator — is handed a casus belli against it.

The same shape, elsewhere:

* **a casus belli records its holder and not its target** (`resolution.py:1402`), so
  `_has_casus_belli` counts any grievance within five turns and a grievance against Veyra
  licenses a war against Aurelia;
* a **Council ceasefire** names its belligerents only in prose;
* a **cession** records the ground and not who ends up with it;
* the **gazetteer** scans prose for place names because the events do not say which places
  they were about.

### The table

Additive, schema 17, authored CHECK enums — nothing closed is widened.

```sql
CREATE TABLE event_participant (
    event_id   INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('actor','target','defender',
                                             'beneficiary','aggrieved')),
    seq        INTEGER NOT NULL DEFAULT 0,
    nation_id  TEXT REFERENCES nation(id) ON DELETE CASCADE,
    region_id  TEXT REFERENCES region(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, role, seq),
    CHECK ((nation_id IS NULL) <> (region_id IS NULL))
);
```

**Two typed columns rather than one polymorphic `entity_id`.** The obvious shape is
`(entity_type, entity_id)`, and it costs the foreign keys — which are the thing that catches
a writer naming a region that does not exist, the same argument that made `PRAGMA
foreign_keys = ON` worth having. The `CHECK` makes exactly one of the two present, so the
pair is a tagged union the database understands.

**`seq` because a role can repeat.** A cession has several beneficiaries and a war has one
target, and a primary key over the nullable columns would not hold: SQLite treats NULLs in a
non-integer primary key as distinct, so uniqueness would silently stop meaning anything.

**No diff pipeline, and this is the one place that is right.** Participants attach to the
event being written *now*, and a rewind deletes events past its target turn — so they cascade
away with the event that owns them. That is exactly the argument `event_visibility` fails,
because an espionage grant points *backwards* at an older event and outlives the rewind. It is
fingerprinted regardless, so the cascade is provable rather than assumed.

### Which call sites adopt it, and what each one stops guessing

| site | writes | reads |
|---|---|---|
| `resolution._declare_war` | `actor` = aggressor, `target` = victim | — |
| `treaties._aggressor` | — | which party declared war **on another party** |
| casus belli, everywhere it is written | `actor` = holder, `target` = whom against | |
| `resolution._has_casus_belli` | — | a grievance **against this nation**, not any grievance |
| `organisation.demand_ceasefire` | `target` = both belligerents | |
| cession / `territory_transfer` | `beneficiary` = who gains, `region` = what | |
| `views._places_named_above` | — | places the events *say* they are about |

**Backward compatibility is the same trick the treaty terms used**: an event with no
participants falls back to today's behaviour. Every event written before schema 17 has none,
so an existing save behaves exactly as it does now and only newly written events are precise.
That is what keeps this additive in behaviour as well as in schema.

### What this deliberately does not do

It does not become a general event-annotation table. Five roles, closed, each because a
specific consumer needs it — a sixth needs a consumer first, or it becomes somewhere to put
things nobody reads.


## Three correctness fixes from the phase-2 review

**A breach happens completely or not at all.** The five things a breach does are one act, and
the events have to be written first because every diff must cite one. So a refused batch left
the *narrative* of a betrayal behind with none of its consequences: the commitment stayed
`active`, nobody was charged anything, and the record said the treaty had been torn up.
Reproduced by giving the violator 2.0 legitimacy, where the −6.0 charge takes it below its
floor.

The casus belli is the half that matters. It is a standing grant of permission to go to war,
keyed on the aggrieved nation and read by the war guard for five turns — so a failed breach
handed out a real licence for a betrayal that never happened. A refused batch now takes its
events with it: our own writes from moments ago, uncited, with the batch that would have
cited them rolled back.

**The war-symmetry invariant could only find rows it had.** An inner join compares pairs that
both exist, so it caught `at_war = 1` against `at_war = 0` and was blind to `at_war = 1`
against *nothing at all* — the worse of the two, since a consumer reading the absent direction
gets no row and reads the pair as at peace. A LEFT join, and both directions examined so a
missing row is reported from whichever side survives.

**A cassette tolerated damage anywhere.** Only an incomplete *final* line is an interrupted
write; a malformed line in the middle is a damaged tape, and skipping it replayed the run as
though that call had never been made. Interrupted and damaged are different words, and the
first was being used to excuse the second.

Two more, on the instruments around it. **Unused entries now fail a replay** — a miss says the
engine asked something the tape lacks, and leftovers say the opposite: a call the recorded run
made and this one did not, which is exactly as much of a regression and was being printed as a
number nobody read. And **the bundle carries the tape**, which is what makes it a bug bundle:
a world and a list of findings say what happened, and only the cassette says why the engine
did it.

**The REPLAY.md commands were both wrong** — `rewind` takes the turn as a positional and the
world as `--world-id`, and there has never been an `atlas jump` command at all. `build_parser`
is split out of `main` so the recipe is now checked against the parser itself rather than
against a remembered spelling: a test parses every command the bundle tells a reader to type.


## Three tables the digest could not see, and the leak hiding behind them

From the Codex review of phase 2, and the two findings turn out to be one: a stolen secret
survived a rewind, and the reason nobody noticed is that the digest did not cover the table
it lived in.

**The leak.** Espionage grants sight of an *older* event — by construction, since that is
what there is to find out. The grant was written straight to `event_visibility`, so a rewind
deleted the espionage action and the espionage event, both belonging to the turn being
undone, and left the grant standing. **The spy kept, permanently, a secret it learned in a
turn that no longer happened.** The grant now rides in a batch citing the espionage event,
scoped `SECRET` because its cause is — an intelligence coup that announced itself would not
be one.

**Why nothing caught it.** `commitment_nation`, `air_control` and `event_visibility` were all
diffable and none was in `_FINGERPRINT_TABLES`. Each could be changed experimentally with no
digest move at all, which makes every rewind-exactness claim ever made about them vacuous. A
digest that omits state is worse than no digest: it is an assurance that does not hold.

All three are in now. Two needed a scope other than `WHERE world_id = ?`, which they do not
have — they are reached through the row that owns them, which is the relationship their
foreign key already declares.

**The structural guard is the general rule behind the three**: every table a diff can address
must be in the digest. A table a diff can address is state the engine changes, and state the
engine changes is what a rewind has to put back — if it is not digested, nothing can tell
whether it did. The set difference is now empty and a test keeps it so.

**What moved, proved per section.** `scratchpad/probe_fingerprint_sections.py` computes the
digest over the old table list and the new one in one process, on the same world — decision
96's discipline. The old-list value is unchanged, so the whole difference is the three
additions:

```
gathering-storm   commitment_nation 381a6da1e271f695 (19 rows)   air_control / event_visibility empty
modern-day        commitment_nation 9358364b9f30b3a9 (23 rows)   air_control / event_visibility empty
```

**The seed fingerprints do not move** — `2c90c4dc…` and `6f6e863a…`, unchanged — and the
reason is worth stating precisely, because it is easy to expect otherwise: commitments and
their parties are laid over a *created* world by `apply_politics`, not carried in `WorldSeed`,
and the recorded baselines are `WorldSeed` digests. The world digest is deliberately not a
checked-in value, so there is no baseline to update; what does go stale is the per-turn
fingerprints in the PT33 cassette sidecars, and those tapes were already historical.


## A diff must land on the row it names

From the Codex review of phase 2, and the gravest of the three it found in the core
pipeline, because rewind addresses rows by `target_id` and nothing else. Two ops, the same
mistake: **the row a change landed on could differ from the row the audit trail named.**

**An update to a row that is not there reported applied.** `UPDATE ... WHERE` on a missing
row changes nothing and raises nothing, so the batch stood, the diff was recorded with
`old_value = None`, a rule computing `new_value - old_value` saw a swing out of nowhere, and
a rewind would try to restore a value into a row that does not exist. A *delete* has refused
a missing row since it was written; this was the one op without the rule.

Existence is checked rather than the value, because `_read_current` returning `None` cannot
tell a legitimately NULL column from an absent row — and treating the first as the second
would refuse every first write to a nullable field.

**An insert could land under a different id than it named.** `setdefault` let a payload
carrying its own primary key win over `target_id`. Reproduced on `construction`: the diff
said `c_named`, the payload said `c_actually`, the row appeared as `c_actually`, the batch
reported applied — and a rewind, deleting by `target_id`, found nothing and left the row
behind forever. It applied to every primary-key column, not only to `id`. Absent is still
fine and is the normal case; conflicting is refused rather than reconciled, because nothing
can tell which of the two the writer meant.

**Two existing tests were proving less than they claimed**, which the check surfaced rather
than broke: `test_core003_blocks_engine_only_columns` asked CORE-003 about a unit id the
sandbox does not have, and an anticheat test refused a relation to a nation that does not
exist. Both now use rows that are really there, so they test the rule rather than the
absence.


## The cassette could replay silently where it should have missed — cassette format 2

Found writing the phase-2 review handoff, disclosed there as §7.1 rather than quietly
patched, and fixed here.

`ReplayProvider._take` tried the request digest and then fell back to a weaker key —
`asked(role, model, canonical prompt)`, everything an `Entry` stores, which is strictly less
than the digest because **an entry records neither the schema nor the options**. The fallback
existed to rescue the PT33 tapes, whose keys were computed before the untrusted-block nonce
was normalised away and cannot be recomputed from an entry.

It was consulted unconditionally. So a change to a response *schema* — the engine asking for
a different shape of answer — missed the digest and then quietly hit the weak key:

```
record with schema A, replay with schema B  ->  SILENTLY REPLAYED {'v': 1}
```

That is the exact class of change a cassette exists to catch, replayed as though nothing had
happened. Options went through the same hole.

**The fix is a gate, not a removal.** `FORMAT` is 2; new tapes carry canonical keys and are
indexed only by digest. Format-1 entries — and only those — are additionally indexed under
`asked`, and the format travels on the entry rather than on the file, so a tape concatenated
from two recordings still gates line by line. The rescue now dies with the recordings it was
written for. Both PT33 tapes still load, still read as format 1, and still keep their rescue.

The miss message also told the wrong story here, saying the *prompt* did not match when the
prompt matched exactly. There are three causes with three different fixes — asked more often
than recorded, a prompt that changed, and a prompt that did not change while the schema or
options did — and it now distinguishes them by scanning what is left on the miss path.

Seven tests, including the demonstration verbatim, the same-schema replay that must still
hit, a format-1 entry with a nonce-bearing prompt that must still be rescued, and the same
entry at format 2 that must not be.


## The world simulator moves to claude

PT33's sharpest operational finding, acted on — and the measurement changed the reasoning
twice on the way, which is why the numbers are all here rather than a conclusion.

**What the tape says.** Across both runs, by provider: agy 36 calls / **17 failed** / 291k
prompt tokens; codex 76 calls / 0 failed / 267k; claude 43 calls / 0 failed / 44k. Every
failure was agy on `world_sim`. But agy answered **12/12** on Modern Day, whose prompts are
seven times larger, and 7/24 on the sandbox an hour later — and a fresh probe of the exact
failed prompt answers **3/3, fastest of the three**. So this is the documented agy flake in a
bad window, not a size limit. That makes it worse for this role, not better: the world sim is
the one call every turn depends on, and three of its failures aborted a turn outright.

**Why claude and not codex**, each point measured rather than assumed:

| | agy | claude | codex |
|---|---|---|---|
| failures, PT33 | 17 / 36 | 0 / 43 | 0 / 76 |
| real 17k-token world-sim prompt | 16s, 22s | **22s, 37s** | 22s, 15s |
| small sandbox prompt | 12-17s | 65-83s | 13-19s |
| attests served model | never | yes | yes |
| prompt tokens carried | 291k | 44k | 267k |

* **Diversity is a stated design property.** The world sim and the tracked agents must come
  from different judgment sources; codex holds the tracked agents. Moving there would
  collapse it and put ~93% of prompt volume on one provider.
* **Latency was the objection and did not survive measurement.** Claude's 65-83s on the
  *small* prompt looked disqualifying. On the prompt that actually runs every turn it is
  22-37s, inside PT31's 23.0s median. The slow figure was a window, not scale.
* **The largest role becomes attestable for the first time.** agy never reports which model
  served a call, so the model refereeing the entire world was permanently `UNVERIFIED`.

**What it costs, plainly.** The world sim is the biggest single line in the token budget:
**291k of 602k prompt tokens across both runs, 48% of everything**, ~17k every turn on Modern
Day. All three CLIs are subscription-authenticated subprocesses, so this is quota consumption
rather than metered billing — but it is real, and it moves from Google's quota to Anthropic's.
Claude was the least loaded by volume at 7%, so the balance lands at roughly 335k claude /
267k codex, against 558k on codex for the alternative.

The **arbiter stays on agy**, deliberately: it is EVE-only and rare, and EVE's premise is
three genuinely different judgment sources. Removing agy from the pin table entirely would
undermine that quietly rather than by decision.

`ATTESTING` is now a named fact about the CLIs rather than a remark in an adapter, and a test
asserts the world simulator sits on a provider that says who answered — so a future re-pin has
to argue with the reason rather than only with the destination.


## The naming bug PT33 found, diagnosed from the tape

**The diagnosis in the PT33 entry above was wrong, and the cassette is what disproved it.**
The obvious reading — that some view composes a region's `kind` and `name` into *Coastal
Reaches* — is checkable now that the prompts are on disk, and it is false. Grepping every
recorded briefing for *Reaches*:

* the phrase appears **only inside event text**, never in a structured line;
* the first occurrence is turn 1, in narrative prose a model wrote;
* `r_coast` appears in **no tracked-agent prompt of the entire run**.

So the chain was: a model was shown the region's name *with no id beside it*, embellished it
in prose as "the Coastal Reaches", the engine filed that prose as history, every later
briefing quoted the history back, and both staffs read it and wrote the invented name. The
resolver refused it ten times, correctly. `The Reaches` and `Reaches` both resolve to
`r_coast`; `Coastal Reaches` does not, and should not.

**Two halves, and the first does not fix the bug on its own.**

*One renderer, because there were four.* A region reached a model as ``Name (`id`)`` in the
front lines, `Name [id]` in the parser view, and as a bare `Name` **twice in the world
simulator's own briefing** — and the bare ones are the interesting failure, because the world
simulator writes the prose everything else quotes. `reference.label` is now the only renderer,
`Name [id]` everywhere, matching the form nations already used. `resolve` gained a tier that
reads a displayed label directly, so the round trip holds *by construction* rather than by the
fuzzy prefix tier coping — pinned over all 488 regions of 1936 and all 429 of Modern Day, plus
every other vocabulary, plus a guard that fails if any view goes back to the old form.

*And a gazetteer, because a display fix cannot reach a place that is never displayed.* The
Reaches was Aurelia's province, not on Karthos's front and not in its holdings, so it was in
no structured line of Karthos's briefing — while Karthos's own stated goal was *"you want the
Reaches and you intend to take them"*. The briefing now scans its own prose — the nation's
character, its standing goals, the history it is showing — for places the world really has,
and lists each with the name the engine will accept. Capped at eight, skipping anything
already shown. **It discloses nothing new: the prose is the input, so a place a staff cannot
see is a place this cannot name.**

With PT33's exact scripted disposition, Karthos's briefing now carries `The Reaches
[r_coast]`.

*This changes the model-facing prompts, so the recorded cassettes will now miss on replay —
loudly, with the message saying the engine is asking a different question. That is the
designed response to an intentional prompt change, and the tapes remain what they were: the
record of what the engine asked and what the models answered on 2026-08-08.*


## PT33 — the first run that never has to be paid for twice

Two live runs, both recorded. **Modern Day, 12 turns, 100 calls, 875s** with Russia and
Germany carrying scripted dispositions; **the sandbox, 10 turns, 55 calls, 870s** with
Karthos and Aurelia. All three CLIs probed green first (claude `claude-sonnet-5` MATCH 2.9s,
codex `gpt-5.6-terra` MATCH 3.8s, antigravity `gemini-3.6-flash-medium` UNVERIFIED 10.8s).

### The headline: both runs replay offline, exactly

| | live | replayed | |
|---|---|---|---|
| Modern Day | 875s, 100 calls | **24.2s** | every call matched, none unused |
| sandbox | 870s, 55 calls | **96.2s** | every call matched, none unused, **every turn's fingerprint reproduced** |

The sandbox tape is the stronger proof of the two, because it contains **17 real provider
failures** and reproduces them: the engine replays the failures, replays the retries, and
lands on the same digest. That is the half of a live run a mock has never been able to reach.

Modern Day's tape predates the `created_at` record (below) so its digests are not comparable,
but every one of its 100 prompts matched byte for byte — and a prompt carries event ids,
treasuries and the turn's own history, so an identical question on turn 12 means the worlds
were identical through turn 11. The digest check is a cheaper restatement of that.

### What the recording found, in the harness rather than the engine

**Three of the four defects this run surfaced were mine, and all three were in the
instrument.** The engine came off well; the tooling did not, which is the same result PT31
had and is worth expecting from any new instrument's first outing.

1. **The untrusted-block nonce made negotiations unreplayable.** `wrap_untrusted` fences
   quarantined text with a fresh `secrets.token_hex(8)` per call — deliberately, because a
   fixed delimiter lets a nation type the closing token and address the model directly. So
   two calls asking the identical question in identical worlds hash differently, and the
   replay missed on turn 4. Normalised out of the digest rather than made deterministic:
   the nonce is a security property of the *call*, not part of the question, and weakening
   it to make a harness easier would trade a real defence for a convenience.
2. **A tape recorded before that normalisation cannot be re-keyed**, because an `Entry` does
   not store the schema and options the digest also covers. Rather than discard an
   875-second recording, the replay falls back to a weaker index over what an entry *does*
   hold — role, model, canonical prompt.
3. **`world.created_at` is wall-clock and sits inside the fingerprint**, so a replay building
   its own world differs on every turn for a reason that has nothing to do with the engine.
   Now recorded beside the tape and aligned on replay.
4. **The sidecar keyed digests by turn number, and an aborted turn is re-attempted** — so a
   turn number appears twice and a dict keeps only the last. The replay compared the
   *aborted* attempt against the *sealed* attempt's digest and reported three mismatches on
   a run that had reproduced perfectly. A harness that cries regression is a harness nobody
   reads.

### And two in the engine, both found by the corrected detectors

* **`a clash named region '', which does not exist`** — every turn of the sandbox from turn
  1. The world simulator emits a clash with an empty region id and the engine refuses it.
  Not fatal and not new, and nothing had ever reported it.
* **`Coastal Reaches is not a region on this map`** — *six times by Karthos and four by
  Veyra*, every turn for six turns. The region is `r_coast`, named **The Reaches**, of kind
  `coastal`. Two independent models, playing different powers, converge on the same wrong
  name every single turn — so this is the briefing showing a name that is not the name, not
  a staff being careless. **This is `impossible-objectives` working**: the same detector
  that produced three false positives in PT31 has now produced two true ones, on the body of
  the message rather than the actor prefix, which is exactly what the correction was for.

### The invariant checker

**Zero violations, on every turn of both runs.** It is the first instrument this project has
shipped that came back empty on its first live outing, which is either good news or the
thing to be suspicious of next.

### The treaty clauses were exercised, and declined five times out of five

The harness had never opened a negotiation — `DiplomacySession` is constructed in exactly one
place in the tree, the UI — so every headless run this project has ever made tested the
negotiation layer and the commitment extraction behind it **zero times end to end**. This one
does, three times in Modern Day and twice in the sandbox.

Germany refused a Russian energy deal all three times (*"Berlin declines"*, *"Moscow, no"*,
*"We decline. A formal energy-supply arrangement…"*), and Aurelia refused Karthos twice. Every
one was a reasoned refusal from a power with a good reason to refuse — Germany is scripted as
the reluctant anchor of Europe and Russia is invading Ukraine.

So: **the extraction ran live, correctly recorded nothing, and wrote no terms.** The negative
case is verified end to end. **Route-opening has still never run against a real model**, and
that is the sharpest remaining gap — it needs two powers with a reason to agree, which the
current dispositions deliberately do not provide.

### Health

| | Modern Day | sandbox |
|---|---|---|
| turns | 12: 10 sealed, 2 partial | 10 attempts: 7 sealed, 3 **aborted** |
| calls | 100, **0 failures** | 55, **17 failures** |
| rewind check | MATCH | MATCH |
| detector findings | **none** | 5, all true positives |
| invariant violations | none | none |

**Every one of the sandbox's 17 failures was `antigravity`/`gemini-3.6-flash-medium` on the
world-sim role**, and three of them exhausted their retries and aborted the turn. **The
cassette is what makes that attributable rather than anecdotal.**

> **Correction, written when the re-pin was done.** Two sentences that stood here were
> wrong, and the tape is what disproved them. I wrote that the world sim is the largest
> prompt and agy the provider least able to answer it, and that Modern Day routed the role
> elsewhere. Both runs used the same pins, so **Modern Day's world sim was also agy — and it
> answered 12/12 on prompts seven times larger** (75.7k chars against the sandbox's 10.7k).
> Re-probed afterwards with the exact prompt it had failed, agy answered 3/3, fastest of the
> three providers. So the failures are **episodic, not a capability limit** — the
> long-standing agy flake, in a bad window an hour after a clean run. The recommendation
> stands and the reasoning behind it does not: an unpredictable provider on the one call
> every turn depends on is a worse risk than a slow one, not a weaker one.


## DESIGN NOTE — the consolidation phase, sketched before building

Four items, agreed by two reviewers independently. Written before any code exists so the
shape can be argued with rather than inferred from what got built. **Nothing below is built
at the time of writing.** Two of the four turned out to be different from what was asked
for once the tree was read, and both departures are argued here rather than made quietly.

### 1. The replay cassette — **BUILT**

*Built as sketched, at the provider seam. `providers/cassette.py`: a `RecordingProvider`
that decorates any provider and appends a line of JSON per call, and a `ReplayProvider` that
answers from the tape. Fifteen tests; the headline one records three turns of a two-tracked-
power world and replays them into a fresh world of the same seed to the same fingerprint.*

*Two things the tests changed. The digest covers the schema and the options, which a test
found by rebuilding a request from a recorded prompt without them and missing — correctly.
And a truncated line is now skipped while a whole cassette from another format is refused:
the two look similar and mean opposite things, one being an interrupted run and the other a
tape whose fields no longer mean what this build thinks they mean.*

*`lab_run.py` records every run to `scratchpad/cassettes/<preset>.jsonl`, with each turn's
fingerprint in a sidecar rather than in the tape — a cassette is what the models said, and
what the world became is the engine's answer to that, and a tape carrying its own expected
result could not falsify anything. `lab_replay.py` plays a run back and exits non-zero if
any turn's fingerprint has moved.*


**The problem.** A live run is the most expensive thing this project produces and it is
spent once. PT31 cost 2032 seconds and two providers' quota, found one real bug and three
defects in its own instrument, and is now unreproducible: the world is in a bundle, the
model's answers are not. Every conclusion in the playtest log rests on a run nobody can
re-derive. When the observability fix landed (`b73bf54`) it came with the discovery that six
scratchpad harnesses had been running scriptless — and the honest consequence, stated in the
handoff, is that *any conclusion drawn from them should be treated as unverified*. There is
no way to check, because there is no record of what was asked or answered.

**The shape.** Record every provider call as a line of JSON; replay it later with no CLI, no
network and no quota. A recorded run becomes a regression fixture that exercises the engine
against *real model output* — including the malformed, the empty and the merely strange,
which is the half mocks cannot reach. This is what makes "hundreds of cheap campaigns" a
proposal rather than a wish, and it is why it goes first: the final live run of this phase
should be the first one that never has to be paid for twice.

**Departure from the brief: it records at the provider seam, not at `_invoke`.** The brief
said `TurnEngine._invoke`, on the strength of `b73bf54` calling it *the one door every call
passes through*. That is true of every call **inside a jump** and not true in general. Six
call sites reach a provider without going through it:

```
engine/advisor.py:89     engine/diplomacy.py:157   engine/diplomacy.py:277
engine/eve.py:219        engine/parser.py:165      providers/diagnostics.py:190
```

`parse_orders` is on that list, and it runs for every order a human types. A cassette
recorded at `_invoke` would be missing the player's own half of the game. So the recorder is
a `ModelProvider` that decorates another one — which is also why it needs no engine change
at all, and why the recorder and the player are the same shape.

**It records attempts, not calls.** `_invoke` retries a rate limit and logs one line per
call; a cassette must reproduce the *sequence the provider saw*, so a failure that was
retried is recorded and replayed as a failure that is retried. Recording the post-retry
answer only would desynchronise a replay the first time a live run hit a 429.

**Matching is by request digest, not by order of arrival.** Tracked agents run concurrently
through `asyncio.gather`, so arrival order is a race — the same reason their results are
sorted by nation id before use. The key is a hash of (role, model, prompt, schema, options),
which is order-independent and reproducible. Repeats of the same key are consumed in
recorded order, which is what makes retries replay correctly.

A missing key is **loud by default**. A prompt that has changed means the engine is now
asking a different question, and that is the finding, not an inconvenience to be smoothed
over with a nearest-match. There is a `loose` mode keyed on role and ordinal for the case
where the prompt has legitimately changed and the question is whether the new engine handles
last week's answers — but it is opt-in, and a run that used it says so.

**No schema, no fingerprint, no rewind impact.** A cassette is a file beside the world, not
a table in it.

### 2. The treaty-clause seam — **BUILT (schema 16)**

*`commitment_term` at schema 16: additive, a new table so its CHECK constraint is authored
rather than widened, no save invalidated. Eighteen tests. **Both eras' seed fingerprints are
byte-identical across the change** — `2c90c4dc…` and `6f6e863a…`, every section hash
unmoved — which is the point of the "terms are created in play or not at all" rule.*

*Three clauses shipped, as sketched: `no_war`, `open_trade`, `cede_region`. The compatibility
story turned out to be the load-bearing design decision: **an agreement with no recorded
terms is read as carrying the terms its kind implies**, which for the three pacts is exactly
what `BREACHABLE` hardcoded — so every existing world and every seeded agreement behaves
precisely as it did, and only agreements made in play carry explicit terms. A seeded trade
agreement therefore still opens nothing, which is what keeps the fingerprints still.*

*`honour_terms` is idempotent and runs every turn rather than firing on a transition, because
there is no single moment an agreement becomes active — a model may write `commitment.status`
in any batch. That also means it repairs itself: an agreement whose activating batch was
refused is honoured the next turn instead of never. Route ids derive from the term that
opened them, so a rewind and a re-run produce the same id and the same digest, which is what
a replayed cassette has to reproduce.*

*The loop closes end to end and is derived at every step: war → breach → the commitment goes
`broken` → the route it opened closes. Nothing anywhere says "embargo".*

*`military_access` and `tribute` are still inert, and the honest reason is in the sketch:
neither has a test against state that exists today, and writing one that fires on something
adjacent would be the instrument manufacturing its own findings. The mechanism is there for
the moment either gets something to check.*


**The bug.** `trade_route` is written in exactly one place in the tree —
`scenarios/seeder.py:983`. Nothing opens a route in play. Routes can be *cut* by war,
blockade and sanction, because `route_is_open` is derived and asks three subsystems a
question they already knew the answer to — but nothing can ever open one. Trade is a one-way
ratchet toward autarky, and a negotiated `trade_agreement` moves `relation.treaty_status`
and not one barrel of oil.

Three more kinds are worse than inert. `territory_cession`, `military_access` and `tribute`
are values in a closed CHECK enum that **no engine code reads at all**. They can be signed
and nothing anywhere is different afterwards.

**Why this is the treaty lifecycle's completion rather than a new feature.**
`treaties.BREACHABLE` covers three kinds of nine. The other six cannot be broken because
nothing knows what they promised — `condition_text` is prose. A term table is the missing
half: it makes a promise *checkable*, which is the only thing standing between the six inert
kinds and the same derived-detection discipline the three pacts already get.

**`commitment_term`, and the discipline it keeps.** One row per clause, additive, a new
table so its CHECK constraint is authored rather than widened and no save is invalidated.
**Detection stays a query, not a claim**: a term records *what was promised*, and whether it
is being kept is read from state every turn. No model can assert a betrayal that did not
happen and none can conceal one that did.

Three clauses, and the count is a decision:

| clause | on kind | the engine *does* | breach test, derived |
|---|---|---|---|
| `no_war` | the three pacts | — | the parties are at war |
| `open_trade` | `trade_agreement` | **opens a `trade_route`** | the obliged party is at war with the buyer |
| `cede_region` | `territory_cession` | — | the obliged party still holds the region at the due turn |

**`military_access` and `tribute` stay inert, deliberately, and that is worth saying plainly
rather than papering over.** Neither has an honest test against state that exists today. A
grant of access is a permission, not an obligation, so nothing about the world says whether
it was honoured; a tribute needs a per-turn transfer, which is a balance surface nothing in
either era would exercise and which no test could reach through play. Writing a test that
fires on something adjacent would be the instrument manufacturing its own findings — the
exact failure PT31 already had three of. The mechanism now exists for both the moment a
scenario or a peace settlement gives either of them something to check.

**Terms are engine-derived, not model-authored, except one.** `no_war` and `open_trade`
follow from the commitment's kind and the parties' own state — the same discipline that lets
a nuclear launch take no effect verbs from its proposer. `cede_region` is the exception and
has to be: a promise to hand over a named province is exactly what a negotiation produces,
and nothing but the transcript knows which province. So the extractor may name one, and the
engine refuses it unless the region resolves through the reference boundary and is held by
the party promising it. **The model may record a promise; only the engine may move ground.**

**Seeded agreements do not open routes.** The seeded worlds already carry their routes, and
opening more at seed would move both eras' fingerprints for no gain. A term is created in
play or not at all, and a test pins the seed digests across the change.

### 3. The phase-aware invariant checker — **BUILT**

*`testing/invariants.py`, ten invariants, folded into the laboratory: same `Finding` type,
same `suspicious()` sweep, same bundle, and `TurnRecord.invariants` so a violation names the
turn it started on rather than only the end of the run. Twenty tests, and the ones that
matter are the ones proving a check does **not** fire while the engine is behaving.*

**It found its target on the first run, and the number split in two.** Seeded worlds carried
4 reconciliation violations in 1936 and **56 in Modern Day** — and they are two different
problems that would have been one number:

* **an agreement the column does not admit to.** 1936 seeds an active Anglo-German trade
  agreement and leaves `relation.treaty_status` reading `none`, so an agreement that exists
  in the commitment table is invisible to the seven consumers that read the column. That is
  unambiguously a bug. Fixed at the seam: `apply_politics` now raises a status the seeded
  commitments justify, **upward only**, and the seed fingerprints do not move because
  relations and commitments are laid over a created world rather than carried in `WorldSeed`.
  Both eras now seed clean.
* **a pact the column asserts with no agreement behind it.** Modern Day does this 52 times
  — NATO is an `alliance` in `relation` with no commitment row. Recomputing the column from
  the commitments would demote all 52 to `none` and delete most of the map's diplomacy, so
  this is *not* an invariant violation and is not checked. **It is a real gap and it is
  Alex's call**: an alliance with nothing behind it cannot be betrayed, because there is
  nothing to break. Turning those 52 into commitments would make them breachable and
  term-bearing, and that is a scenario change with balance consequences rather than a
  reconciliation.

*One more thing fell out, in the cassette's own headline test rather than the engine:
**`world.created_at` is wall-clock and sits inside the fingerprint**, so comparing two
separately created worlds was flaky in the worst way — green in isolation and in almost every
full-suite run, red when the two `create_world` calls straddled a second. The digest is for
comparing a world against *itself* at another time, which is why `seed_fingerprint.py` hashes
a `WorldSeed` and not a database. The test now aligns that one column and says why.*


**Folded into the laboratory, not beside it.** Same `Finding` type, same `suspicious()`
sweep, same bundle. A detector says *this pattern is the shape of a bug we have had*; an
invariant says *this must never be true at rest*. They are the same instrument pointed at
two different kinds of evidence, and giving them two reporting paths would mean a run whose
verdict has to be read twice.

**Phase-awareness is the whole design, not a refinement.** An active pact between two powers
at war is a bug at the end of a turn and the *correct* intermediate state in the middle of
one — `assess_breaches` runs after the fighting resolves, deliberately, so a war declared
this turn is a breach this turn. An invariant that does not know which phase it is in would
fire on the engine behaving exactly as designed. PT31's three false positives were the
instrument manufacturing findings, and a false positive here is worse than a miss for the
same reason: it discredits the eight checks that were right.

So an invariant declares the phases it holds in, and the checker is given the phase it is
running at. Two entry points: at seed (a fresh world must satisfy the seed-time set) and at
rest after each turn.

**The first invariant is the one with a known violation waiting.** Scenario-authored
`relation.treaty_status` and the commitments seeded beside it are never reconciled — the
handoff flags it, the scenario is authoritative by design, and nothing checks that the two
agree from turn 0. Whether the answer is to fix the data or to reconcile at seed depends on
how far apart they are, which nobody has measured. Measuring it is the first thing this
checker is for.

### 4. The chores, and one that turned out to be wrong — **BUILT**

*Built as described. `apply_focus` now takes the turn rather than reading the clock, because
the roster is recomputed before the clock advances and a batch filed under the outgoing turn
sits outside the range a rewind to that turn deletes — the same hole, one line lower. Four
new tests, of which the structural one is the one that matters: it fails on any new direct
write to a fingerprinted table outside `state/`, with a four-entry allowlist that each carry
the reason a rewind still restores them. A second test fails if an allowlist entry stops
describing anything, because a stale exemption eventually covers a write nobody argued for.*


**Deferred objectives get their own list.** A deferred objective is *accepted* — *"borders
none of your territory yet; the objective stands"* — and `turn_engine.py:1374` files it
under `report.rejections`, where it reads as a refusal to the turn report, to the player and
to the laboratory. `JumpReport` already keeps eleven things apart from each other for
exactly this kind of reason; this is the twelfth.

**Departure from the brief: `MAX(id)+1` stays, and replacing it would break something real.**
The brief asks for commitment ids safe across a rewind, on the grounds that ids are reused —
turn 3 signs commitment 7, a rewind deletes it, and the next turn 3 signs a different
agreement that is also 7. The reuse is real. The conclusion is backwards.

`MAX(id)+1` is **what makes a replay exact**. A rewind deletes the row, so re-running the
turn allocates the same id, and the world that comes back is byte-identical to the world that
was there before — which is precisely the property the cassette exists to test and the
laboratory's gravest detector exists to check. A monotonic allocator that survived a rewind
would hand the second run id 8, move the digest, and turn every replay of a turn containing
a treaty into a mismatch. It would trade a cosmetic problem — a bug bundle citing an integer
that now means a different agreement — for a permanent false positive on the one check that
says the audit trail is sound.

So the id stays, the reasoning gets written down where the next reader will find it, and a
test pins the property: rewind, re-run, same ids, same fingerprint.

**What the chore turns into instead.** Auditing that path found two writers of fingerprinted
state that do not go through the diff pipeline — the same class as PT31's gravest finding,
in two places `f50af32` did not reach:

* **`focus.py::reconcile_roster`** writes `nation.is_tracked`, `tracked_since_turn` and
  `tracked_reason` with direct SQL. All three are columns of a fingerprinted table, and
  `fingerprint()` hashes every column. A power promoted on turn 5 and rewound to turn 4 stays
  tracked, and the digest moves. This happens on ordinary turns of an ordinary game.
* **`organisation.py::demand_ceasefire`** writes a `commitment` row and its
  `commitment_nation` rows with direct SQL. A Council resolution demanding a ceasefire does
  not unwind.

Both are the bug decision 101 wrote down and decision 132 fixed in two other places. Neither
is a new mistake; both are the same one, in files nobody grepped. That is the argument for
the invariant checker having a *structural* member: a test that asserts no module outside
`state/` writes a fingerprinted table directly is worth more than any individual fix.


## DESIGN NOTE — the treaty lifecycle, and the player's undertaking channel

### 3. Agreements that can be broken

Decision 120 made an active commitment *bind*: `relation.treaty_status` became its
materialised view, so the invasion guard, the gambit odds and four briefings all read the
same fact. What it did not do is let an agreement **end badly**. Nothing in the engine has
ever written `status = 'broken'` -- the value has existed in the CHECK constraint since
schema 1 and no code path reaches it -- so a pact could be signed, honoured, and never
violated. A treaty you cannot betray is set dressing.

**The lifecycle, and where each transition comes from.** `pending` when the extraction
records something one side offered; `active` when both accepted; then either `expired`
(a term ran out) or `broken` (somebody violated it). `fulfilled` stays for the one-shot
kinds -- a cession happens once and is done.

**What a breach has to do**, and all five or it is not modelled:

* mark the commitment `broken` and **record who broke it** -- which needs two additive
  columns, because "this agreement ended badly" and "Karthos ended it" are different facts
  and only the second supports a grievance;
* cost the violator **regard with every other party**, and **legitimacy** at home, because
  a state that tears up its own signature is worth less as a signatory to everyone;
* hand the injured party a **casus belli** -- the engine already gates war declarations on
  one, and being attacked by a co-signatory is the least controversial grievance there is;
* take `treaty_status` back down, which decision 120's recompute already does for free;
* **appear in the turn report**, high, because it is the single most consequential thing a
  diplomatic system can produce.

**Detection is derived, not trusted.** The breach that matters is *being at war with a
power you have an active pact with*, and that is a query, not a claim -- so no model can
assert a betrayal that did not happen, and none can conceal one that did. Checked each turn
after the fighting resolves, so a war declared this turn is a breach this turn.

The one judgement call: a `ceasefire` or `peace_treaty` between two nations who are at war
is **not** a breach, because those are the agreements that exist *because* of the war. Only
the three that promise not to fight -- non-aggression, defensive, alliance -- can be
violated by fighting.

### 4. The player's undertaking channel

Decision 125 gave the player `sabotage` and `air_strike` through the gambit engine. The
generalisation is the rest of the catalogue -- summit, envoy, bribe, expose, sting, incite --
which needs no new mechanism at all now that the manifest and the reference boundary exist:
the kinds become a vocabulary, the aliases live on the capability, and targets resolve
through the boundary like every other reference.

**On `nuclear_launch`, the decision is: it stays out of the player undertaking channel.**

The condition offered was that it could go in behind an explicit, unambiguous, *confirmed*
player intent. The first two are achievable -- an exact kind id, no alias inference, refusing
anything the parser had to guess at. The third is not, and it is the one that matters.
**There is no confirmation step in this architecture.** An order goes text → parser → action →
handler inside a single `jump()`; there is no point at which the engine can stop, show the
player *you are about to launch*, and wait. Building one is not a small addition to this
item -- it is a new interaction mode in the turn loop, the UI and the CLI, and it would be
the only place in ATLAS where a turn blocks on a human.

Without it, "explicit and unambiguous" reduces to *the parser emitted the exact string* --
and the parser is a language model reading a sentence a human typed. That is precisely the
thing decision 125 declined to put a warhead behind, and nothing in the two foundations
since has changed it. The nuclear path stays where it is: a deliberate one, reached
through the arsenal layer's own machinery rather than through order parsing.

This is a *deferral with a named unblocker*, not a refusal: if a confirmation step is ever
built for anything, this becomes a five-line change and should be revisited.


## DESIGN NOTE — the capability manifest and the reference boundary, sketched

Two foundations, sketched together because they are the same idea applied to two different
kinds of vocabulary: **the engine should not be able to know something it has not told
anybody.** Every integration-seam bug found so far is an instance of it -- research existed
and the parser had never heard of it; an amphibious route existed and the parser had never
heard of it; a pact existed and the guard had never heard of it; a region's *name* was on
the screen and the objective validator had never heard of it.

### 1. The capability manifest

Today an action type is declared in **three** places that have to agree and nothing makes
them: the parser's schema enum, `views.ADVISABLE_ACTIONS`, and `orders._HANDLERS`. A parity
test exists between the first two -- it caught the `gambit` addition -- and there is nothing
tying either to a handler, nothing tying any of them to an availability rule, and nothing at
all that would notice a capability the engine can execute but never advertises. That is
precisely the shape of *research exists in the engine but vanished from the parser*.

So: one `Capability` record per mechanic, in one catalogue, carrying the action type, the
words a player might use for it, the fields it needs, what it may target, what it costs, an
availability predicate, who executes it, and a sentence the player reads. Everything else is
**generated**: the parser enum and its prompt menu, the council's list, validation messages,
model examples.

**The test that makes it structural** is not that the catalogue exists but that it is
*exhaustive in both directions*: every capability appears in the parser enum and the
advisor list and has somewhere to be executed, **and** every value in the parser enum is a
capability. A mechanic added to the engine and not to the manifest fails; a manifest entry
that never reaches a prompt fails. Neither can be waved through by remembering to update a
second file, because there is no second file.

What this deliberately does *not* do: it does not become a dispatch framework. Handlers stay
where they are and keep their signatures; the manifest records *where* execution lives
rather than owning it. A catalogue that also runs things is a catalogue people are afraid to
edit.

### 2. The reference boundary

`naming._resolve` is already a good tiered matcher -- exact id, exact name, alias, whole-word
prefix, close match, with a scar in the comments about the tier that read *Western Front* as
*Western Sahara* and invaded it. Two things are missing.

**It cannot say why it failed.** It returns `None` for *no such place* and for *seven places
match*, so no caller can produce the message the model needs. "Ambiguous" is the answer that
actually helps: it means the vocabulary was right and the phrase was not specific enough.

**It only knows the two vocabularies that live in tables.** Technologies, civil projects and
unit kinds are catalogues, and each caller that wanted one wrote its own matching -- which is
why `_unit_kind` had a build-time filter that silently turned a transport into infantry, and
why `build_project` needed the ids put in front of the parser by hand.

So: one boundary, one tiered algorithm, five vocabularies, and a structured answer -- the id,
or *unknown*, or *ambiguous with these candidates*. **The engine stays exactly as strict as
it is after normalisation**; what changes is that the strictness is applied to what the model
*meant* rather than to what it typed. The objectives fix from decision 122 was this for one
vocabulary at one call site; this is that generalised.

The ordering rule between the two: the manifest is first, because the reference boundary is
what the manifest's `targets` field will name, and building them the other way round means
writing the target vocabulary twice.


## DESIGN NOTE — trade and resources, sketched before building

The last subsystem in the queue, and the only one so far that **moves the seed**. Everything
below is written before any code exists so the shape can be argued with rather than inferred
from what got built.

### Three strategic goods, and why so few

`oil`, `steel`, `rare` — fuel, metal, and the small handful of things a modern industry
cannot substitute for. Three rather than a dozen, and the reason is not simplicity for its
own sake: **every resource is a number a model has to reason about in a prompt**, and the
tracked-agent briefing is already the largest object in the engine. A dozen goods would buy
spreadsheet depth and pay for it in the thing that actually makes ATLAS work, which is that
a model can hold the whole position in its head. Three is enough for the shape that matters:
a country that has what it needs, a country that must buy it, and a country that can be cut
off from it.

The era decides what they mean, not the table: `oil` is what an army runs on in both 1936
and 2026, `rare` is tungsten and rubber in one and semiconductors and lithium in the other.
One set of ids, two sets of endowments.

### What they do

**Production, not revenue.** A shortage does not make a country poorer — it makes it unable
to *build*. `industrial_capacity` already answers "how many things can this nation have
under construction at once" from `industry`, and a resource shortfall scales it. That is one
existing door, not a new mechanic, and it keeps the failure mode legible: a blockaded
country still collects its taxes and cannot lay down a battleship.

The alternative — resources feeding `economy` — was rejected because economy already has
four inputs and a fifth would make the number mean nothing at all.

### Endowment moves the fingerprint, deliberately

Every nation seeds with a per-good endowment, from a table keyed the same way
:mod:`atlas.scenarios.demography` and :mod:`atlas.scenarios.leaders` are. **This moves the
`nations` section of both eras' fingerprints and nothing else**, and the proof is the same
one decision 96 used: both sides computed in one process with the new step swapped for the
identity function. Baselines updated in the same commit as the change that moves them.

Unlike demography, there is **no total to preserve** here, because there was no previous
total — this is new state rather than a redistribution of existing state. That makes it the
first seed change in the project that cannot lean on the redistribute-never-conjure
discipline, so the guard has to be different: endowments are *relative* and consumed against
a demand derived from `industry`, so what matters is the ratio between what a country has
and what it needs, and both sides of that come from the same table.

### Trade is a standing agreement, and embargo is its absence

A `trade_route` between two nations moves one good, and it exists because somebody agreed to
it. Three ways it stops, and **all three already exist**:

* **sanction** — the organisation layer already writes `sanction` rows, and a sanctioned
  nation loses its routes with the sanctioning body's members;
* **blockade** — a route is carried by the same sea walk supply uses, so a fleet across the
  lane cuts it, and `naval._sea_is_ours` decides. The same function again;
* **war** — belligerents do not trade.

So the disruption levers are not new code, they are three existing systems being *asked* a
question they could already answer. That is the same trick the logistics layer got its
blockade from, and it is the strongest argument that this design fits the engine rather than
being bolted to it.

### Schema 14, additive

`resource` (nation, good, produced, stockpile) and `trade_route` (from, to, good, volume,
opened_turn). Both new tables, so their CHECK constraints are **authored rather than
widened** — no closed enum anywhere is touched, and no existing save is invalidated. A world
written before 14 has no rows, and a nation with no resource row has no shortfall: the layer
is inert in an old save exactly as air and leaders are.

### What this deliberately does not do

No prices, no market, no arbitrage. A route moves a good at an agreed volume and that is
all. Prices are the part of a trade system that is genuinely hard to make interesting
without becoming a second game, and every strategy title that has tried has ended up with
either a spreadsheet or a decoration. The lever worth having first is **whether the goods
arrive at all**.


## DESIGN NOTE — logistics and trade, sketched; and why logistics goes first

Two subsystems, and the order between them is a decision rather than the order they were
listed in. **Trade moves the seed** — a resources layer is state every nation starts with,
so it moves the fingerprints and every balance judgement taken afterwards is taken against
it. **Logistics moves nothing at seed**: supply is a reading of the map, so it can be built,
measured and tuned against baselines that have not shifted underneath it. The roadmap's own
ordering rule says seed-data changes go first, but that rule exists so later *measurements*
are stable — and here the seed-moving one is also the one that benefits from being measured
in a world where supply already works. So: logistics, then trade.

---

## DESIGN NOTE — logistics, sketched before building

### The claim: this needs no schema at all

Supply is not a fact about a unit. It is a **reading of the map** — of who controls what
this instant, of which seas are held, of where the sky is contested. Storing it would mean
maintaining a cache that every conquest, every landing and every air battle invalidates, and
that a rewind would have to unwind. Derived, it cannot be stale and cannot be wrong, and
rewind-exactness comes free. The same argument the scoring layer just made, and it holds
here for the same reason.

The one thing that *is* stored is what being out of supply **cost** — attrition, written as
ordinary diffs on `unit.strength` citing one event. That is state, it rewinds, and it is the
only part that should.

### How supply is traced

A multi-source breadth-first walk from every depot the nation has, over the adjacency graph
the air layer already built, through provinces it controls. Three rules, and each is the
answer to a specific thing that should be possible:

**Depots are your own core, not merely ground you hold.** A supply base needs an
administration behind it, and occupied ground has somebody else's. This is what makes a deep
invasion a *corridor* rather than a spreading stain: an army in Belgium is supplied from
Germany through the ground between, and the length of that thread is the campaign's real
constraint. It also pairs with the unrest layer without either knowing about the other —
ground that is restive is ground that is not supplying anybody.

**A province you do not control does not pass supply.** This is the lever Alex asked for and
it needs no new mechanic at all: take one province behind the front and everything beyond it
is cut this turn. Encirclement stops being a special case in the resolver and becomes a
consequence of where the line is.

**Seas are regions, so supply crosses them exactly the way armies do.** The adjacency graph
already contains 113 ocean regions as real nodes with 587 sea crossings between them, so an
overseas theatre is supplied by *walking through the sea*, and `naval._sea_is_ours` — the
same function that decides whether a landing can happen — decides whether the walk passes. A
blockade therefore strangles supply without anything being written to make it do so. That is
the naval tie-in, and it is free.

**Enemy air control costs range rather than blocking it.** An interdicted province is
crossed at a penalty, so interdiction *shortens the thread* and can snap it at the far end.
Blocking outright would make air control decisive on its own, which is a bigger claim about
air power than the air layer itself makes.

### What being out of supply does

Two things, deliberately, because one alone is either invisible or absolute:

* **attrition** — a formation out of supply loses strength every turn, so a pocket dies
  even if nobody attacks it;
* **effectiveness** — an unsupplied attacker advances less and fights worse, through the
  same `compute_pressure` / `compute_advance` doors the air and leader layers use, so all
  three compose and none of them can secretly mean something different from the others.

Not annihilation, and not instant. A cut-off army should be a problem its owner has several
turns to answer — by breaking out, by relieving it, or by giving the ground back.

### What this deliberately does not do

No supply *throughput* — a depot has range, not tonnage. No separate rail or convoy layer.
No per-unit supply consumption. All three are the kind of depth that is only worth having
once the reach model has been played against, and reach is what makes cutting a line matter.


## DESIGN NOTE — victory conditions, sketched before building

Chosen over the intelligence/fog layer, and the reason is which one is cleaner to do
*well* rather than which is more exciting. Fog is mostly built already — `known_region_ids`,
per-event visibility, `SECRET` scoping enforced by INFO-002, a fog map layer — so the real
increment there is **active** intelligence, which means new model-facing verbs threaded
through every view builder. That is a wide, risky change. Scoring is narrow, and its one
stated blocker is gone: the roadmap deferred it because *"scoring an economy that is still
land-area-shaped would bake the flaw into the win condition"*, and decisions 82 and 96
removed the flaw.

It is also the gap a player feels first. You can currently play ATLAS forever and never be
told whether you are winning.

### The three decisions worth arguing with

**1. Nothing is stored. The score is derived on read.** The same discipline `_deltas_for`
already uses: the state and the audit trail know everything the score is made of, and a
cached copy is a copy that can disagree with them after a rewind. `score_of()` is a pure
read over the same tables the map draws, so **rewind-exactness is free rather than
defended** — there is no scoreboard to unwind.

**2. A victory is a fact in the event log, not a row in a new table.** `rewind_to` deletes
events past its target turn, so an achievement recorded as an ENGINE event unwinds with
everything else and needs **no schema change at all**. It also means the achievement is
visible everywhere events already are: the turn report, the overlay, the briefing a model
reads. A nation being told *you are being watched as the leading power* is a strictly
better prompt than a hidden number.

This costs one compromise, stated plainly: `event.kind` is a closed CHECK enum, so adding
`'victory'` would rebuild the table and invalidate every save. The events are written as
`narrative` with a marker the module owns and reads back. That is uglier than a kind of its
own and much cheaper than a migration nobody's save survives.

**3. Winning does not end the game.** The engine records that a condition was met and the
report says so; the player decides whether that was the end. A grand-strategy sandbox whose
window closes when a threshold trips is a sandbox that punishes the thing people play it
for — and it fits the standing rule that the player is never railroaded.

### What is scored

Five components, weighted, each read from something the engine already simulates, so that
playing the game well is what scores well and nothing has to be farmed:

* **ground** — `budget.ground_value`, which already discounts held-but-not-owned at 40%, so
  an occupier scores an occupation for what it is worth rather than for its area;
* **wealth** — economy and industry together, because a rich country with no industry is a
  different thing from a poor one with a lot;
* **arms** — military strength, and the arsenal, which nothing else scores;
* **standing** — legitimacy, the regard of other powers, and a seat at whatever bodies
  exist. The only component a peaceful nation can lead on;
* **knowledge** — technologies held.

Weights are one table in one module, so the argument about what a grand-strategy game is
*for* happens in one place a reader can find and disagree with.

### The conditions

Instantaneous where possible, because a condition needing history needs somewhere to keep
it and decision 1 says there is nowhere. The one that genuinely needs time —
**hegemony**, being twice the runner-up — is derived by asking whether the event was written
in each of the last N turns, which the event log answers without storing anything new.

* **domination** — a share of the world's ground value;
* **hegemony** — a multiple of the runner-up's total score, held for several turns;
* **survival** — a nation that was written off and is still there at the end date;
* **concert** — leading on standing rather than on ground or arms, which is how a small
  power is meant to be able to win.

### What this deliberately does not do

No difficulty-scaled targets, no per-scenario victory scripting, no end-of-game screen. All
three are reasonable and all three are decisions about a *game* rather than an *engine*,
and they are much easier to make once there is a score to look at.


## DESIGN NOTE — the depth roadmap, ordered

Everything Alex named, sequenced by value and by how much else depends on it. Tagged
**real build** vs **cheap tweak**, and **sim-depth** vs **UX**. Written before starting so
the ordering can be argued with rather than inferred from what got done.

The ordering rule: anything that changes *seed data* goes first, because every later
measurement and balance judgement is taken against it. Anything Alex can see goes next,
because it is the difference between a system existing and a system being felt. New
subsystems go last, because they are the ones that can be cut without stranding the rest.

### Tier 1 — foundational, and it moves everything downstream

**1. Real economic geography.** *Real build · sim-depth · moves fingerprints.*
`population = land_area * 0.4` and `minor economy = 4 + area * 0.02` are the last two
places the map's *shape* stands in for its *substance*. Measured consequences today: Tibet
carries 112 population against Henan's 16, Antarctica seeds an economy of 23.8, Taiwan's
is 4.9 — so Hsinchu, which a serious share of the world's semiconductors comes from, is
worth 0.43 development. Population feeds the manpower ceiling, so this is not only an
economic fix: **it rebalances what every army is made of.** Flagged twice (decision 86 and
Alex's own list), foundational to any balance work after it, and the reason it goes first.

### Tier 2 — high visibility, concrete, and Alex pointed straight at them

**2. Map overlays.** *Cheap tweak · UX.* The layer machinery already exists — `MapLayer`,
the chrome chips, and a `build_scene` that is a pure function of the same state — with
four layers built (political, focus, fronts, fog). What is missing is exactly what was
asked for: **development, unrest, air superiority**. Cheap because the hard part is done;
valuable because three subsystems built this session are currently invisible on the map
that is meant to show the world.

**3. Map performance, round three.** *Cheap tweak · UX.* Two known costs remain after the
settle timer, the cached world read and the applier skip: `build_scene` rebuilds all 429
region visuals on every click (5.8 ms) to change one selection, and crossing a
level-of-detail boundary mid-gesture spikes to ~130 ms. Both are bounded and measurable.

**4. Situation report.** *Cheap tweak · UX.* The overlay is three columns — what happened,
what it cost, what was refused — and the refusal column is the right instinct. What it
does not do is *lead* with the systems built since it was written: an air campaign, a
Security Council session, a nuclear use and provincial development moving all land in a
generic list. A turn where the Council convened should not read like a turn where a
minor laid down workshops.

### Tier 3 — the biggest new subsystem worth doing well

**5. Named leaders, traits, agendas, succession and coups.** *Real build · sim-depth ·
additive schema.* Weighted above the rest of the depth list because it is the one Alex
keeps returning to, and because it is the cheapest way to make a nation feel like somebody
rather than a colour. It also has somewhere to attach: `nation.stability` and
`legitimacy` already move every turn, `coup` and `leader_change` are **already event
kinds**, and the organisation layer showed the pattern for a first-class actor that is not
a country. A leader with an agenda gives the tracked-agent briefing a *voice* and gives
the engine a reason to reject a proposal that contradicts it.

### Tier 4 — deferred, and why

**6. Intelligence / fog of war.** Deferred because *most of it already exists* and would be
deepening rather than building: `known_region_ids`, per-event visibility, `SECRET` scoping
enforced by rule INFO-002, a fog map layer, and a secret-research path. The honest gap is
**active** intelligence — spying to *reduce* fog rather than fog as a passive consequence.
Worth doing after leaders, because the best thing to spy on is somebody's agenda.

**7. Logistics and supply.** Real build, and genuinely good, but it lands on the same
adjacency-and-reach machinery the air layer just took over, and doing both in one session
risks two half-built reach models. Also the most likely to make the game *less* fun if
mis-tuned.

**8. Domestic politics and factions.** Overlaps leaders substantially — factions are the
thing a leader survives or does not. Doing it before leaders means building the
constituency before the person.

**9. Victory conditions and scoring.** Cheap to build, and deliberately last: it should
score the world the other items produce. Scoring an economy that is still land-area-shaped
would bake the flaw into the win condition.

### What this sequence deliberately does not do

It does not start a subsystem that cannot be finished cleanly in one pass. Every Tier 1-3
item is committable on its own with tests, a changelog entry and — where it means anything
— a mock playtest. The UI items cannot be visually verified from here and are flagged for
Alex's eyes.


## DESIGN NOTE — an international organisation as a first-class actor

Alex's brief, on paper for sign-off before anything is written. **Nothing below is built.**
It opens with an audit, because two of the pieces this needs turn out to already exist and
one of the obvious approaches turns out to be closed.

### Audit first: what is actually there

**There is no supranational anything.** `bloc` is not it — it is EVE plumbing that assigns
nations to a *provider* for engine-vs-engine runs, with columns `provider`, `model_pin`,
`colour`. It has no political meaning and reading it as one would be a category error.

**A "nation" is the only actor the engine has.** Every acting entity is a `nation` row:
`event.actor_nation_id` is a foreign key to it, `diff.target_id` addresses it, `relation`
is a directed pair of them, `commitment_nation` joins to it, `event_visibility` scopes by
it. Anything that acts and can be reacted to has to be reachable through those.

**Three things this needs already exist and are load-bearing.**

1. *A trigger surface.* The engine already computes, at a specific line, every condition an
   organisation would care about: an unprovoked war (`_has_casus_belli` false, and the
   aggressor is already charged 8 legitimacy for it), an attack on a pact member
   (`_invoke_defensive_pacts`, decision 71), a nuclear launch (`nuclear.launch`), a
   province changing hands (`_cession_diffs`, `_evict_diffs`). **The organisation does not
   need to re-derive anything.** It subscribes to conditions the engine already found.
2. *A grant-a-free-hand primitive.* Decision 71 just built it: an engine-authored
   `casus_belli` exempts its holder from `_UNPROVOKED_WAR_COST` for five turns and
   satisfies DIP-003. "The Security Council authorises force" **is that primitive**, aimed
   at a different trigger.
3. *An adjudicated-proposal channel.* `engine/gambit.py` is a model proposing a bounded
   attempt from a closed vocabulary, refused before the roll if the world does not permit
   it, decided by `rng.derive`, landed as diffs under `Role.ENGINE`. A resolution before a
   council is the same shape with a different vote-counting function.

**And one approach is closed.** `CommitmentKind` is a closed CHECK enum
(`non_aggression_pact`, `alliance`, `trade_agreement`, `territory_cession`,
`military_access`, `tribute`, `ceasefire`, `peace_treaty`, `defensive_pact`) and so is
`relation.treaty_status`. **Membership of an organisation cannot be expressed as a
commitment or a treaty status without widening one of them, which is save-invalidating.**
That is what forces a table.

---

### The representation: a nation row it cannot use, plus additive state

**The organisation is a `nation` row with no territory, no units and no capital, carrying
an additive `organisation` row keyed on its id.** This is exactly the shape the nuclear
layer took — `nation` plus an additive `arsenal` keyed on `nation_id` — and it is chosen
for the same reason: it buys the entire existing spine for free.

What the nation row buys, none of which has to be built:

- `event.actor_nation_id` works, so the body can author events and they are legal causes
  later diffs can cite. Without this it cannot appear in the causal graph at all.
- `relation` rows to every nation already exist — world creation writes every ordered pair
  — so **"the world's regard for the Assembly" and "the Assembly's regard for you" are
  already state**, with no new column. That is precisely the diplomatic-standing lever such
  a body runs on.
- `commitment_nation`, `event_visibility`, `diff` addressing, digests and rewind all work
  unchanged.

What it must be prevented from doing, and each of these is a small guard rather than a
design problem:

| risk | why it is not serious |
|---|---|
| ranked by Focus | scores ~0 (no wars, no borders); still excluded explicitly |
| the master AI builds it a steelworks | `autonomous_investment` already skips it — `_least_busy_region` returns `None` |
| CORE-005 capital check | only checks capitals that are `NOT NULL`; a null capital passes today |
| revenue/upkeep | `collect_revenue` COALESCEs population to 0 and it owns no units; **verify, do not assume** |
| somebody declares war on it | no regions, so no contest can ever open; refuse the declaration outright |
| it clutters every nation roster | one `NOT EXISTS (SELECT 1 FROM organisation …)` in `visibility.other_nations` |

Deriving "is an organisation" from the presence of an `organisation` row is preferred over
an `is_organisation` column on `nation`: the nation table is read on every view and does
not need another flag, and the join is against a table with single-digit rows.

### The state it holds

An organisation has no army and no ground. What it has is **standing**, and making that a
real number is what turns a UN that everybody ignores from a complaint into a mechanic.

    organisation(nation_id PK, kind, charter_text, authority REAL, convened_turn)
    organisation_member(organisation_id, nation_id, is_permanent, joined_turn)
    sanction(id, organisation_id, target_nation_id, kind, until_turn, opened_turn)

- **`authority`** (0–100) is the body's own credibility. It falls when a resolution is
  vetoed, or passes and is ignored; it rises when a member complies. It feeds the odds of
  the next resolution passing. **This is the loop worth having:** a Security Council that is
  defied twice becomes a Security Council nobody fears, and that emerges rather than being
  written.
- **`kind`** separates a universal body (everyone is a member; the trigger set is atrocity
  and aggression) from an alliance body (members only; the trigger is an attack on one).
  NATO can therefore be *both* the `defensive_pact` commitment it already is **and** an
  organisation that convenes — the pact is the automatic half, the body is the deliberate
  half.
- **`is_permanent`** is the veto. It is the one part of this that should be deterministic
  rather than rolled: a permanent member opposed defeats the resolution, full stop.
- **`sanction`** is the only genuinely new *standing* state. It is also the thing the gambit
  design note flagged as missing ("anything needing new state has nowhere to live"), so it
  is worth building as a narrow, honest table rather than a general `standing_effect` — a
  general one invites everything to become a standing effect.

### How it convenes, and how it is adjudicated

**Triggered, not scheduled.** A session opens when the engine has just recorded one of a
closed set of conditions, all of which it already detects:

| trigger | detected today at |
|---|---|
| a war declared with no casus belli | `_declare_war`, where the 8-legitimacy charge is applied |
| a member of a defensive pact attacked | `_invoke_defensive_pacts` (decision 71) |
| a nuclear weapon used | `nuclear.launch` |
| a province annexed at a peace, or a capital occupied | `_cession_diffs` / `_evict_diffs` |

Convening is **capped at one session per turn** and requires `authority` above a floor, so a
discredited body stops being able to command attention — which is both the correct
simulation and the cost control.

The session itself is **one model call, gambit-shaped**. The body's view is: what happened,
who its members are, who is permanent, its own authority, and the accused's standing. The
response is a closed schema with no numeric field — a `resolution` naming one action from a
closed vocabulary, a target, and a one-sentence in-world justification.

Adjudication mirrors `resolve_contest` and `gambit.adjudicate`, with the vote as the
pressure term and **every input read from ground truth**:

    support = Σ over members of f(relation[member -> accused], member weight)
    veto    = any permanent member whose regard for the accused is high   (deterministic)
    odds    = base(action) + authority/200 + support − severity_resistance(accused)
    odds    = clamp(odds, 0.05, 0.95)
    roll    = derive(seed, world_id, turn, f"session:{org}:{action}:{accused}:{index}")

Three outcomes, as everywhere else: **passed**, **watered down** (half magnitude, and the
body loses a little authority), **failed or vetoed** (no effect on the accused, and the body
loses more). A resolution that fails is not nothing — it is the most interesting result on
the board, and it must cost the body something or there is no reason not to convene forever.

### The effect vocabulary, closed and bounded

Every verb reaches a column the engine already tracks, and every one is applied by the
engine rather than chosen freely — the nuclear-launch discipline, not the gambit-verb one,
because a resolution's consequences should not be negotiable by whoever drafts it.

| action | reaches | bound |
|---|---|---|
| `condemn` | every member's `relation.score` toward the accused | −15 each, floored at −100 |
| `sanction` | a `sanction` row; a per-turn drag on the accused's `economy` and `treasury` | expires; capped per turn; scaled by how many members comply |
| `authorise_force` | an engine-authored `casus_belli` for every member | five turns, exactly as Article 5 |
| `demand_ceasefire` | a `pending` commitment of kind `ceasefire` between the belligerents | the parties still have to accept |
| `admit` / `expel` | `organisation_member` | membership only |

**Nothing in the vocabulary moves territory, units, contests or war status.** Same line as
decision 57, for the same reason, and it is what stops "the UN resolves that you have lost"
from being expressible. The body can make a war expensive, lonely and legitimate to oppose.
It cannot end one by saying so.

Event kinds: `diplomatic_message` for the session, `ultimatum` for a condemnation,
`casus_belli` for authorisation, `economic_reform` for sanctions, `commitment_made` for a
ceasefire demand. **All already in the closed enum — no event kind needs widening.**

### Do members react?

**Prompted, not automatic** — consistent with Article 5, and for the same reason. A
resolution does not declare anybody's war. What it does is put a casus belli in members'
hands, move their relations, and state the consequence in their briefing. Compliance is then
visible and measurable: a member that acts on an authorisation raises the body's authority,
one that ignores it lowers it. **That feedback is the whole design** — it makes the
organisation's power a function of whether anyone has been listening, which is what an
international body's power actually is.

### How it stays inside the guarantees

- **Ground truth.** Support is computed from `relation` rows, veto from
  `organisation_member`, severity from what the engine already charged. Nothing in the odds
  comes from a model.
- **Seeded adjudication.** One `rng.derive` draw keyed on the session, so a replay of the
  same inputs produces the same resolution.
- **Exact rewind.** Every effect is a diff under `Role.ENGINE`. `sanction` needs an entry in
  `apply.PRIMARY_KEY` and `bootstrap._FINGERPRINT_TABLES`, exactly as `arsenal` did.
- **No new proposer role.** `Role.ORGANISATION` would be a permissions and routing identity
  only, never appearing in `diff_batch.proposer_role` — decision 55's arrangement, which is
  what keeps this out of a save-invalidating enum change.
- **Disclosed.** The trigger list, the vocabulary, the caps and the veto go in the briefings
  generated from the tables, so a staff can price an unprovoked war before starting one.
- **Cost.** One extra model call on a triggered turn, gambit-sized (~2k tokens in). At the
  measured mixed-pin rate that is roughly $0.01–0.02 a session — negligible next to the
  eight calls a turn already makes.

### Schema change: yes — flagged, not taken

**This needs schema 10 → 11**: three new tables (`organisation`, `organisation_member`,
`sanction`). All are `CREATE TABLE`, so the migration is additive and passes the runner's
guard unchanged; **no closed CHECK enum is widened**, so no save is invalidated. 1936 has no
organisations and would be untouched; Modern Day gains a UN and, optionally, NATO as a body
alongside the pact it already has. Per Alex's standing rule this is flagged and awaiting
sign-off rather than done.

### What it will still not do, honestly

- **It cannot make anyone comply.** If every model ignores it, the mechanical residue is a
  relation hit and a sanction drag. That is the correct simulation and it will read as
  underwhelming in a run where nobody engages — worth knowing before it is built.
- **No General Assembly floor, no committees, no procedure.** One body, one session, one
  resolution. Modelling the procedure would be modelling the wrong thing.
- **Sanctions are a drag, not a trade model.** There is no trade network to cut; `economy`
  and `treasury` are what exist to push on.
- **It cannot be attacked or dissolved by force.** Ignoring it into irrelevance via
  `authority` is the only way to destroy it, which is arguably the truer mechanic anyway.

### Recommended sequencing

1. **The tables and the seeding, with no session at all.** A UN that exists, has members and
   a charter, and appears in briefings. Proves the nation-row-with-no-territory
   representation against every guard above before any behaviour depends on it.
2. **Triggered convening + `condemn` only.** One action, one effect, the authority loop
   live. This is the smallest thing that is recognisably the mechanic.
3. **`authorise_force` and `demand_ceasefire`**, which reuse decision 71's primitive and the
   existing commitment channel and add no new state.
4. **`sanction`**, last, because it is the only piece that introduces standing state and a
   per-turn pass, and it should be added when there is a played game to calibrate the drag
   against rather than before.


## A rival's weapons programme was filed as a routine public work

Found by auditing `_thin_construction`, confirmed against three recorded runs.

The briefing collapses *other* nations' `CONSTRUCTION` events into one footnote —
*"(and N routine public works begun or finished by other nations, not listed)"* — and it has
to. `production.autonomous_investment` develops every unattended nation every turn, so a
Modern Day feed is otherwise 130-odd near-identical "<nation> begins a river port" lines
burying the wars, and on the referee's capped window they crowd the real events out entirely.

**But it thins by event kind, and `CONSTRUCTION` was carrying two different things.**
Technology programmes were recorded under it, so a rival's completed weapons programme went
into that count, described to everyone as routine. It is in the tapes:

    pt34d: "Russia completes Uncrewed systems at scale."
           "United States completes Integrated air and missile defence."
    pt34e: "Russia completes Uncrewed systems at scale."
           "United States completes Uncrewed systems at scale."

All four public, all four reaching every rival's briefing, all four thinned. Two to four per
ten-turn run — rare, and precisely the news a grand-strategy briefing exists to carry.

**Fix: a programme is a `DISCOVERY`, not a `CONSTRUCTION`.** The research track, not the
public-works one. `discovery` was already in the closed event enum and nothing gates on
`construction` outside `production.py` and `views.py`, so this is a re-filing rather than a
schema change — no migration, no CHECK edit, fingerprints unmoved.

**Secrecy was never the kind and the tests pin that.** A secret programme is hidden by
`visibility='secret'` and a one-entry `event_visibility`; moving the kind neither leaks one
nor starts hiding a public one. `tests/test_programmes_are_not_routine.py`, seven tests, two
of which fail if the fix is reverted.

Not changed, for want of evidence: laying down warships is also `CONSTRUCTION`, and "United
States lays down 3 destroyers" is not routine either — but **zero** such events occur in any
of the three recorded runs, so there is nothing to measure and no case for changing it blind.

### Also this item

- `causality.py` audited by running it over every nation and turn of a recorded run: 2430
  breakdowns, 5772 field-turns, **0** causes with no event text behind them, **0** fields that
  moved without producing a sentence, and **0** sentences whose stated span disagrees with the
  sum of its own causes. Clean. One docstring is wrong — `breakdown_of` justifies its `LEFT
  JOIN` by saying diff rows survive a rewind, and they do not (`diff_batch` is deleted and
  `diff.batch_id` cascades). The join is still right, for a different reason:
  `reason_event_id` is deliberately not a foreign key, so `_unrecord` can leave a citation
  dangling.
- `scratchpad/accepted_from_bundle.py` reconstructs a pre-counter run's engine-accepted rate
  from its recorded database, since `diff_batch.proposer_role` and the per-diff `status` make
  it a recorded fact rather than an inference. Offered counts come off the tape, because a
  wholly-refused batch writes no diff rows and counting survivors would score it perfect.


## PT35a's rewind mismatch was the harness, and that was worth finding anyway

PT35a reported, against the hardest guarantee ATLAS makes:

    rewind to turn 8: recorded a8d4065e8833 / replayed a6be1abe9742 MISMATCH

**Cause: the baseline was taken in the middle of the turn.** `lab_run` recorded each turn's
fingerprint the instant `jump()` returned, and the negotiation it holds every fourth turn runs
*after* that — `DiplomacySession` writes `diplomatic_message` events at the turn still
current, and `event` is a fingerprinted table. Turn 8 was a negotiation turn and the rewind
target (`run[-3]`) happened to land on one. Both digests were correct; they described
different moments of the same turn, and the check compared across them.

**The engine's rewind is exact and is now proven so rather than assumed.**
`tests/test_rewind_across_a_negotiation.py` pins the half the harness could not see: a
session's writes are ordinary state, a rewind *past* the talks restores the world
byte-identically, and a rewind *to* their own turn keeps them — swept across three landing
turns, because the harness's target moves with the run length and this only showed when it
landed on a negotiation.

**Why a false alarm here is not a footnote.** Every fourth turn was a coin toss the check
could not win, so a *genuine* rewind failure landing on one would have read exactly like this
and been dismissed the same way. An alarm that cannot be trusted on the guarantee it guards is
worse than no alarm.

Fixed by re-stamping the turn's fingerprint after the negotiations in `lab_run`, and moving
the comparison to the same point in `lab_replay` so recorder and replayer describe the same
moment. No engine change — there was nothing wrong with it.

### And a second harness-fidelity gap, found beside it

`lab_replay` never called `arrange()`, which `lab_run` calls when building the world. For a
`flashpoint` run that seeds a standing non-aggression pact — a `commitment`, a
`commitment_nation` and a synced treaty status, **all three fingerprinted** — so a replay
started from a different world and would mismatch on turn 1 and every turn after, for a
reason that has nothing to do with whether the engine reproduced the run. Latent only because
no flashpoint tape has been replayed yet, and a no-op for every other profile, which is
exactly why it went unnoticed.


## The briefing spent its event window on events it then deleted

The window is capped — `known_events(..., limit=25)`, ordered by `turn DESC, id DESC` — and
`_thin_construction` dropped *other* nations' public works **after** the cap. In that order
the cap is not a cap on the briefing at all. `production.autonomous_investment` develops every
unattended nation every turn, those events are written late and so hold the high ids, and the
window is ordered by recency: the cap filled with construction, the thinning deleted it, and
the briefing showed whatever few real events had squeezed in. The wars were outside the
window and no amount of thinning could reach them.

Measured on the recorded runs:

| | before | after |
|---|---|---|
| pt34e — briefings filling the cap yet showing less | **243/243** | 0/243 |
| pt34e — events shown per briefing (cap 25) | **11.1** | **25.0** |
| pt34g — events shown per briefing (cap 25) | **4.1** | **25.0** |
| pt34g — window slots spent then discarded | **4573** | 0 |

**1314 displacements of in-run news** on pt34g alone, including `commitment_broken` —
*"Germany repudiates the Rhineland demilitarisation provisions of Locarno"* — pushed out of
Austria's briefing in a 1936 game. After the fix pt34e carries 12.9 pieces of genuine news per
briefing.

**Fix: thin in the window, not after it.** `known_events` grows
`without_routine_construction`, off by default so no other caller changes, and
`routine_construction` counts what was left out over the span the window reaches — one SQL
predicate, `_ROUTINE_CONSTRUCTION`, written once and used both ways so the two can never come
to disagree about what "routine" means. `_briefing_events` in `views.py` is the single place
the three nation-facing views get their window.

**`visible_event_ids` is deliberately not narrowed.** It answers *what may this nation cite*,
which is a wider question than *what is worth printing*, and shrinking it would have turned a
display fix into diffs refused by CORE-001. It is the union of the thinned window and the
plain one, so it is a superset of what it held before: 25 per briefing became 38.9 (pt34e) and
45.9 (pt34g). A test asserts the old set is a subset of the new one rather than trusting the
construction.

`tests/test_briefing_window_is_not_wasted.py`, six tests; four fail if the cap-then-thin order
is restored.


## The same bug at the fourth call site, which the first fix had left behind

The briefing-window fix corrected the three nation-facing views and stopped there. The
referee's window had it too: `all_events(limit=120)` then `_thin_construction(viewer=None)`,
and the referee has no nation, so **every** nation's construction was elsewhere to it and was
fetched only to be discarded. The original helper's docstring even named this as the worst
case — *"on the referee's capped window they can even crowd the real events out entirely"* —
and the thinning it recommended ran after the cap, so it never could have helped.

32 of 120 slots wasted on pt34e and 40 on pt34g, with the cap binding exactly. After: 120/120
and 93/120 of real events.

**`_thin_construction` is deleted rather than left dead.** It is the footgun itself — a filter
whose whole shape assumes it runs after the window — and leaving it in place with no callers
is an invitation to use it that way again. Its tests were rewritten to drive the real
paths, which is a better test than the one they replaced: they now exercise the predicate the
briefing actually uses instead of a list helper standing in for it.

**The two predicates are deliberately not shared.** `visibility._ROUTINE_CONSTRUCTION` asks
"construction belonging to somebody other than the viewer" and needs `actor_nation_id <> ?`;
passing `NULL` for a referee with no nation of its own inverts it *silently*, because
`'usa' <> NULL` is NULL rather than true. The referee's `_A_NATIONS_CONSTRUCTION` asks
"construction belonging to any nation", which the actorless `N project(s) advance in the
yards` aggregate is not — and a test pins that it survives.


## SKETCH — the two naval gameplay-depth changes, designed before they are built

Two items out of the Narrow Sea showcase are **not bug fixes**. They change how the game
plays and they have balance consequences, so they are argued here first and the numbers
are named as choices rather than discovered in a diff. Alex decides the magnitudes; this
section says what each one is worth and what it was measured against.

### SKETCH 1 — air superiority over a sea

**The brief's premise is wrong and the complaint behind it is right.** Air control *is*
already summed into a naval surface fight: `compute_pressure` calls `air.superiority_term`
for every contest whatever the region's kind, and `compute_advance` calls
`air.advance_multiplier` the same way. Measured, evenly matched fleet action on `r_sea`:

    no sky              pressure 0.500   advance 1.62
    aurelia owns sky    pressure 0.600   advance 2.27
    karthos owns sky    pressure 0.400   advance 1.62

So a carrier fleet that owns the sky is not getting nothing. It is getting the **land**
figure — `_SUPERIORITY_MAX = 0.10`, whose own comment sets it at *"about as much as
favourable terrain and less than surprise"*, on the reasoning that *"air decides how well a
ground attack goes, never whether it happens"*. That reasoning is right on land and is the
opposite of the naval case: at sea in the carrier era, who owns the sky very often decides
whether the surface action happens at all. Over two exchanges ±0.10 is invisible next to
the roll, which is what the showcase saw.

**The change: one number, and only at sea.** `superiority_term` learns the region's kind
and uses `_SUPERIORITY_MAX_AT_SEA` over water.

> **BALANCE CHOICE — `_SUPERIORITY_MAX_AT_SEA = 0.22`, against 0.10 on land.**
>
> Sized against the terms it sits beside rather than guessed. The force ratio spans about
> ±0.20 through `0.5 + (base - 0.5) * 1.4`; the largest evidence flag is ±0.18
> (`ATTACKER_OVEREXTENDED`); the worst terrain is 0.15. At 0.22 a fleet with *total* air
> superiority fighting at material parity wins 72% of exchanges instead of 60% — the
> largest single circumstantial modifier in the game, which is the historical claim, and
> still beaten outright by a 2:1 material advantage. It cannot auto-win: the loser of the
> air war still takes 28% of exchanges at parity, and `margin` scales the whole term, so a
> narrowly-held sky is worth a fraction of it.
>
> **Turn it down to 0.15** if a carrier power feels unbeatable; **up to 0.30** if the arm
> still feels ornamental. One constant, one line.

**Deliberately not changed: the advance.** `_ADVANCE_BONUS` stays at the land figure. The
showcase's complaint was about who won the exchanges, not about how fast the sea fell, and
a second lever moving at the same time makes neither measurable. Named here so that
holding it back is a decision rather than an omission.

**Two real holes found beside it, reported and NOT fixed in that item.** Both are why *"the
carrier gained nothing"* is a fair description of what was watched, and both are bigger than
the item that found them:

1. **An uncontested sky is never recorded at all.** `resolve_air_war` builds its fight list
   from `set(reach[a]) & set(reach[b])`, so a region is only contested if *both*
   belligerents can put something over it. A carrier group whose enemy has no aircraft in
   range of the water writes no `air_control` row, and `superiority_term` returns exactly
   0.0. Verified: the same fleet with the enemy's air removed gives `control_of(r_sea) ==
   (None, 0.0)` and a straight gun duel. This is true on land too, and changing it would
   hand out free superiority over every region a power reaches unopposed — a balance
   surface of its own and not something to slip into a naval commit.

2. **A strike into open water lands and does nothing.** `strike` takes its victim from the
   region's holder, `COALESCE(controller_nation_id, owner_nation_id)`, and open sea has
   none. Verified: an Aurelian raid on a Karthosi battleship in `r_sea` returns
   `result='struck'`, `victim=None`, `effects=[]`, and the battleship's strength is
   unchanged. Carrier air cannot touch a ship in water nobody has taken, which is every sea
   until somebody wins it. This one is a plain defect with a clear correct answer — take
   the victim from whose ships are actually there — and is worth its own commit.

### SKETCH 2 — losses that can sink a ship

**The finding.** `_loss_diffs` divides a side's losses by the total strength engaged and
charges every formation the same fraction:

    share = min(1.0, losses / total)
    for unit in units:
        lost = round(float(unit["strength"]) * share, 2)

A fleet of four hulls losing 8% of its strength loses 8% of *every* hull. No hull can reach
zero before the whole fleet does, nothing anywhere removes a unit at zero, and seven turns
of the showcase's war sank nothing. A *"ship goes down"* moment is not rare in this model;
it is arithmetically unreachable.

**The change: the same total, allocated by a seeded draw instead of spread flat.**

* **The total is untouched.** `attacker_losses` and `defender_losses` still come from
  `resolve_contest` exactly as they do now, so the attrition feedback loop the
  `_LOSS_DECISIVE` / `_LOSS_CLOSE` comment describes — losses shift the force ratio, which
  shifts the advance — keeps the tuning it was given. Only the *distribution* changes.
* **Salvos, aimed uniformly.** The total is cut into a few equal salvos and each is aimed
  at one of the engaged formations by a `pick` draw. Aimed *uniformly*, not weighted by
  strength: strength-weighting spares a cripple, which is exactly backwards for the thing
  being built. A hull that takes two salvos in one exchange loses far more than the flat
  share; two hulls come through untouched.
* **A hull that cannot absorb its salvo is sunk** — deleted, with its remaining strength
  counted as the loss and the surplus spilling to the next target, so the side never loses
  more than the arithmetic said. The precedent is `_evict_diffs`, which already deletes an
  encircled formation; it is a diff, so a rewind puts the ship back.
* **Seeded by the decision, never by call order.** `derive(seed, world_id, turn,
  "losses:<contest key>:<side>")` — the contest key already carries kind, attacker,
  defender, region and round index.

> **BALANCE CHOICE — how many salvos.** `max(1, (engaged + 1) // 2)`: half the engaged
> formations, rounded up, so at most half a fleet is damaged in any one exchange and the
> concentration is roughly two-to-one against the flat model. **Fewer salvos concentrates
> harder** (one salvo = the whole loss on one hull, which sinks ships fast and reads as a
> lucky magazine hit every month); **more approaches the flat model** and at one salvo per
> formation is barely distinguishable from it. This is the one knob.

> **SCOPE CHOICE — naval only, and here is why.** The mechanism is shared with the land
> war and is deliberately not applied there.
>
> * A ship is a **hull**: it floats or it does not, and that discreteness is the whole
>   reason concentration is right at sea.
> * A land formation is a **pool** — a division's strength stands for men and materiel,
>   and uniform erosion is the honest model for it. Divisions are not destroyed by
>   attrition in this engine; they are ground down and then encircled, which
>   `_evict_diffs` already does and which is where losing a formation is *supposed* to
>   come from.
> * The codebase already holds this line explicitly: `air._UNIT_FLOOR` exists so that
>   *"Air power is not allowed to remove a division from the order of battle; that is what
>   the ground war is for."*
> * And the practical argument: changing land loss distribution moves the pace of every
>   campaign in both eras, against a `BASELINE_ADVANCE` tuned by measuring whole
>   conquests. That is a large unrequested balance change riding in on a naval fix.
>
> Applied by region kind (`ocean`), which is equivalent to unit domain here because the
> `forbidden` filter already means only sea units are engaged in a sea contest. **To extend
> it to land later, flip one argument at the call site** — the mechanism is general and only
> the caller is opinionated.


## The instrument every exactness claim rests on had a wall-clock in it

`world.created_at` is an ISO timestamp written when the file is made, and `fingerprint()`
hashed it like state — it has since the initial commit. Two worlds built from the same seed
a second apart therefore digested differently, and
`test_fingerprint_canonical.py::test_the_digest_does_not_depend_on_how_the_database_reached_its_schema`
— the one test in the suite that builds *two* worlds and compares them — failed on about
**one run of the suite in four hundred**. Found by reproducing it in a loop: of every
fingerprinted table, the only row that ever differed was `world`, on that one column.

**The rewind guarantee was never at risk**, which is why this survived so long. A rewind
compares a world against *itself* at another moment, and `created_at` does not move within
one world. What it broke is the weaker and still useful claim — that two worlds holding the
same state digest the same — and with it any hope of writing down a reproducible
fingerprint for a freshly built era.

Excluded through `bootstrap._UNHASHED_COLUMNS`, a table rather than a single test so the
next column of this kind has an obvious home. **Nothing may be added there that a rewind
would have to restore**, and a canary test pins the list at exactly `{"world":
{"created_at"}}` so widening it has to be argued for in a commit that also edits the test.

**Only the `world` section moved, proved rather than asserted.** Both rules — every column,
and every column but this one — computed side by side in one process against one database,
which is how this project has proved every other "and nothing else changed" claim. Every
other fingerprinted table is byte-identical, so no state stopped being fingerprinted. The
column is still stored; it stopped being hashed, not written.

`tests/test_a_fingerprint_is_not_a_clock.py`, 9 tests. Five fail with the exclusion removed,
including the two-worlds-a-second-apart repro, which is forced with explicit timestamps
rather than waited for. The 94 rewind and fingerprint tests are green.

### The canonical baselines, reproducible for the first time

The **seed** digest hashes the `WorldSeed` dataclass and has always been reproducible — a
dataclass has no clock in it. The **world** digest hashes the created database along the
path `newgame` takes (`preset_seed` → `create_world` → `preset_politics`), and could not
be written down until now. `scratchpad/seed_fingerprint.py` builds each era **twice** and
compares, so it proves its own output is reproducible rather than claiming it.

| era | built world (no player) |
|---|---|
| gathering-storm | `30dd1d940ef98d6015b8bdbe0760e910` |
| modern-day | `130b2c3d90703e1316bfd2f24b6d225c` |

And the seed digests as they actually stand:

| era | no player | second | third |
|---|---|---|---|
| gathering-storm | `57af0390…` | `b7d88128…` (Germany) | `613bd7f6…` (France) |
| modern-day | `bf7810a5…` | `a1f974cf…` (USA) | `e8145d02…` (China) |

| era | nations | regions | adjacency | cities | unit_templates | units |
|---|---|---|---|---|---|---|
| gathering-storm | `e9ad4211` | `f909b48d` | `67b60b6b` | `e392d55f` | `c4b42a8e` | `8ac6cdf0` |
| modern-day | `57d68ad6` | `7b99b1d7` | `ad105963` | `967d1b4c` | `834e2a8c` | `993e2ff9` |

**The `2c90c4dc…` / `6f6e863a…` gap has a boring explanation, and it is not this bug.** The
seed digest never touched the database and was never affected by `created_at` — the script's
own docstring said as much. Four of the six sections match the documented table exactly:
`nations`, `cities`, `unit_templates` and `units` are unchanged. Exactly `regions` and
`adjacency` moved, in both eras, which is what the coastal and sea-adjacency work of the
preceding batch does to seed geometry — and nobody re-recorded the whole-seed digests
afterwards, so the headline numbers went stale while the sections quietly said why.

Confirmed by building a worktree at `e62f292` and running the script there: **every seed
digest is identical to HEAD's**, so the drift predates this session and none of its commits
moved seed data, exactly as each of them claimed.


## SKETCH — a third era: the Cold War, designed before it is built — **BUILT**

Two eras have shipped and a great deal has been built since the second one. The nuclear
layer, the Security Council, Article 5, trade and embargo, and the naval and air fixes are
all mechanisms **1936 cannot reach and 2026 exercises only partly** — 2026 has nine nuclear
powers and no bipolar deterrence, a Council whose veto is real and rarely decisive, and two
blocs of which only one is a commitment. A Cold War era is the position in which every one
of those is load-bearing at once, which is why it is worth building rather than a fourth
map.

Sketched first, in the discipline of `0e379e1` and `9aa20a3`: the decisions that are
arguable are argued **here**, before any code, so a reviewer attacks the reasoning and not
the diff.

### The anchor year: **1 January 1972**

The brief offered an early-Cold-War dawn (~1947-50) or a peak-tension year (1962) and asked
for the most *playable*. I want a third answer, and the argument is that the mechanisms this
era exists to showcase are all at their historical maximum on this date and on no other.

**Why not 1947-50.** There is no deterrence to model. The United States holds a monopoly
until 1949 and a usable margin for years after; the Soviet arsenal in 1950 is about five
devices with no means of delivering them against North America. The nuclear layer's whole
subject — *what survives a first strike* — has no second party to be about. NATO does not
exist until 1949 and the Warsaw Pact until 1955, so neither bloc can be seeded as a
commitment. The Council is real, but so is the Soviet boycott.

**Why not 1962.** Iconic, and it fails two of the five showcases. The exchange ratio is
roughly 5,000 American strategic warheads against perhaps 300 Soviet, with a Soviet ICBM
force in the dozens: that is American superiority, not mutual assured destruction, and
seeding it as MAD would be seeding a lie. And the China seat on the Security Council is held
by the Republic of China until October 1971 — so a 1962 scenario must either give the P5
veto to a power that is not on the map as a great power, or hand it to Beijing nine years
early. Both are worse than moving the year.

**Why 1972.**

1. **MAD is doctrine, not asymmetry.** Both superpowers hold a full triad at rough
   parity — Minuteman/Polaris/B-52 against SS-9 and SS-11, Yankee-class boats at sea since
   1968, and Tu-95s. SALT I and the ABM Treaty are *signed in May of this year*, which is
   the moment mutual vulnerability stopped being a description and became a treaty
   obligation. The engine's survivability model — `delivery` tier plus the boats actually at
   sea — is at its most meaningful here.
2. **The P5 is exactly the historical five, and they are exactly the five nuclear powers.**
   The People's Republic took the China seat in October 1971 and tested in 1964. Any earlier
   anchor forces a fudge; this one needs none.
3. **Both blocs exist as real collective-defence commitments,** at full extent, and Article 5
   is a mechanism the engine already reads. It is *also* the year the bipolar frame is
   visibly breaking: the Sino-Soviet split is total after Zhenbao, and Nixon lands in Beijing
   in February. Three nuclear poles, not two, is what makes a veto and a pact interesting
   rather than symmetrical.
4. **Every flashpoint the brief names is live at once.** Divided Germany with the Four Power
   Agreement on Berlin just signed and Ostpolitik in progress; Korea frozen on the DMZ;
   Indochina about to see the Easter Offensive and Linebacker, the largest air campaign since
   Korea; Cuba a Soviet client under an American embargo; and the Middle East one year from
   the Yom Kippur War, with the Soviet advisors about to be expelled from Egypt.
5. **The naval fixes have two sides to be about.** Gorshkov's blue-water navy exists by 1972,
   so a sea contest is a contest. In 1950 or 1962 the Soviet navy is coastal defence plus
   submarines and every fleet action is one-sided.

And the discipline the other two eras hold to: **a starting position, not a script.** 1936
opens with the Rhineland, Spain and the Anti-Comintern Pact all near and none inevitable.
1 January 1972 opens with Beijing, SALT and the Easter Offensive in exactly the same
relation to the player — visible, weeks away, and nothing in the engine that makes any of
them happen.

Preset id `cold-war`, name `1972 - Cold War`, `rng_seed=1972`, `default_world_id="cold1972"`.

### The powers — twenty

`economy` is **mobilisation capacity, not GDP**, exactly as Modern Day states it. That
matters more here than in either shipped era: Soviet GDP in 1972 is somewhere near half the
American figure and Soviet *military output* is close to parity, because a command economy
pointed at the army converts a smaller product into more divisions. Japan is the mirror
image — the second-largest economy in the world behind a self-defence force that is
constitutionally forbidden to be one.

| bloc | powers |
|---|---|
| superpowers | `usa` United States, `sov` Soviet Union |
| NATO | `gbr`, `fra`, `wger` West Germany, `ita`, `tur` |
| Warsaw Pact | `eger` East Germany, `pol`, `cze` Czechoslovakia, `rou` Romania |
| third pole | `chn` China |
| aligned, not in either bloc | `jpn` Japan |
| flashpoint pairs | `nvn`/`svn` Vietnam, `prk`/`kor` Korea, `isr`/`egy`, `cub` Cuba |

Four judgement calls worth stating. **Romania is in** because the Warsaw Pact with a member
that refuses orders is a different object from one without; it costs one power and buys the
bloc an internal argument. **India and Pakistan are out** — the 1971 war is three weeks past
and it is a genuine loss, but South Asia is a sideshow to a bipolar showcase and twenty
powers is already above both shipped eras. **Canada and the Benelux are out**; NATO is
represented by the members whose decisions move other members' decisions. **Both Vietnams and
both Germanys are powers**, not minors, because a proxy war fought by a one-division generic
minor resolves in a single contest and teaches nothing — the same argument that made Ukraine
a power in Modern Day.

### The blocs, as commitments — and the Council, as an organisation

Modern Day's decision holds and is worth restating because the brief asks for both: **a bloc
is a `defensive_pact` commitment and not a convening organisation.** Seeding NATO twice
would put two mechanisms on one treaty — an attack on a member would hand every other member
a casus belli *and* convene a body whose strongest action is to hand every member a casus
belli. So:

* **NATO** — one `defensive_pact`, parties `usa gbr fra wger ita tur`. France is a party:
  it left the *integrated command* in 1966 and never left the treaty, which is a distinction
  the scenario carries in the condition text and in a lower regard figure.
* **The Warsaw Pact** — one `defensive_pact`, parties `sov eger pol cze rou`.
* **The Security Council** — one `universal` organisation, members the powers that are UN
  members in 1972, permanent members `usa sov gbr fra chn`. Neither Germany is a UN member
  until September 1973 and neither Vietnam nor Korea until later; leaving them out is free
  accuracy and it makes the Council's jurisdiction over the flashpoints indirect, which is
  what it was.

`organisation.kind` stays the two values the CHECK already carries. No enum is widened
anywhere in this work; `defensive_pact`, `alliance`, `non_aggression_pact` and
`trade_agreement` are all existing `CommitmentKind` members.

What this buys immediately and without a line of code: **the Council is structurally unable
to act against either superpower**, because both hold a veto — which is the single most
important true thing about it and falls out of the data.

### Nuclear arsenals

Scaled the way Modern Day scales them, roughly one per fifty real warheads with the
deployed/stockpiled distinction folded in. Note that **1972's superpower arsenals come out
larger than 2026's**, which is correct: the peak is 1986, and a player moving between eras
should feel that.

| power | warheads | delivery | why |
|---|---|---|---|
| `usa` | 46 | `triad` | Minuteman, Polaris, B-52 |
| `sov` | 38 | `triad` | SS-9/SS-11, Yankee boats since 1968, Tu-95 |
| `gbr` | 4 | `triad` | Polaris boats only — not a triad in the textbook sense and *maximally* survivable, which is what the column asks |
| `fra` | 3 | `missile` | Mirage IV and the Plateau d'Albion silos; the first boat commissioned three weeks ago and is not yet a deterrent |
| `chn` | 2 | `aircraft` | tested in 1964, delivered by H-6; **armed and not survivable**, which is the whole reason Beijing spent 1969-72 looking for a third position |
| `isr` | 1 | `aircraft` | undeclared then and now. Modern Day already seeds Israel armed; seeding it here is the same call, flagged |

Everyone else is latent or threshold and gets no row. India's first test is May 1974 — two
years out, and a scenario should not pre-empt it.

**MAD is carried in the preset's `simulation_rules` as a hard constraint**, not in lore:
first use is the taboo the whole period is organised around, and the rules block is the field
that is injected as a prohibition rather than a preference.

### Flashpoints, province-promoted

The promotion rule is *only the ground a campaign crosses*. Seven modern country codes, and
each earns its place by being unfightable at country granularity:

* **DEU**, split into **West Germany** and **East Germany** by overriding the five eastern
  states to `eger`. This is the same class of hand correction the 1936 file makes for East
  Prussia and the Sudetenland — a border that moved inside what is now one country.
* **Berlin.** Natural Earth has one Berlin shape, so the city cannot be divided. It becomes
  **`m_wbe`, West Berlin, held as a colonial holding of the United States** with a garrison
  on its own ground — which is what it was: an occupied enclave garrisoned by the Western
  powers and constitutionally off-limits to the Bundeswehr. It uses the shipped `colonies`
  path unchanged, and it means an East German move on West Berlin is an attack on ground the
  United States owns, which invokes Article 5 through the mechanism rather than through a
  special case. The GDR is then governed from the region around it, named for what it holds.
* **VNM**, split at the 17th parallel into `nvn` and `svn`.
* **KOR** and **PRK** — the same ground Modern Day already promotes.
* **CUB**, so an invasion of Cuba is a campaign and not one roll.
* **ISR** and **EGY**, so Sinai is ground.

Everything else on the map stays one region per country, as in both shipped eras.

**One generator change, and it is a bug fix.** `scripts/gen_territory.py` resolves a
capital against the *mechanical* owner of a province's modern country, so it cannot express
two nations sharing one country with two capitals — which is precisely what a divided Germany
and a divided Vietnam are. The fix is three lines: resolve the effective owner first
(override if there is one, mechanical otherwise) and check the capital against that. **It
cannot change either shipped era's output** — no 1936 or 2026 override names a capital
province — and both files are regenerated and diffed to prove it rather than to claim it.

### Era units, ships and the research catalogue

Per the era gate: this era supplies its own templates, its own ship classes and its own
catalogue.

**Ships** — `carrier`, `cruiser` (guided-missile), `submarine` (nuclear attack), `transport`.
The battleship is gone and the missile is the argument: 1972's naval question is whether a
carrier group can survive a saturation attack from a cruiser or a boat, which is Gorshkov's
whole thesis. `transport` keeps the engine's name because `naval.sea_lift` looks for it, and
`submarine` keeps its name because `nuclear.survivability` counts boats at sea — a Soviet
second strike that weakens when its submarines are sunk is the era working.

**Land and air** — `infantry` (motor rifle), `armour` (main battle tank), `air_wing` (jet).

**The catalogue** — seven programmes, every effect on a lever shipped code already reads:

| programme | moves |
|---|---|
| `sam_network` | interception at resolution time (`air.INTERCEPT_TECHS`), no completion effect — the same shape as `integrated_air_defence`, and the thing Linebacker was flown against |
| `mirv` | penetration (`air.PENETRATION_TECHS`) plus `nation.military_strength` |
| `solid_fuel_missiles` | `base_strength` on cruiser and submarine |
| `nuclear_propulsion` | `build_days` and `base_strength` on submarine, `build_days` on carrier |
| `main_battle_tank` | `base_strength` on armour and infantry |
| `integrated_circuits` | `nation.industry` and `nation.economy` — 1972 is the year of the microprocessor, and it sits upstream of the two above it |
| `thermonuclear_warheads` | secret, requires two of the above, `military_strength` and `legitimacy` |

Two new keys in `air.INTERCEPT_TECHS` / `PENETRATION_TECHS`. Those are plain dicts keyed by
technology id, not CHECK enums; adding a key is the sanctioned way to reach the lever, and
`test_research_eras.py` already treats a runtime tech with no `TechEffect` as legitimate.

### Leaders — real names, and the era gate they force

1972 is settled history in exactly the sense 1936 is, so the table carries **real people**:
Nixon, Brezhnev, Mao, Brandt, Honecker, Pompidou, Heath, Gierek, Husák, Ceaușescu, Satō,
Andreotti, Lê Duẩn, Thiệu, Kim Il Sung, Park, Castro, Meir, Sadat. Traits stay mechanical and
neutral — they are levers in `engine.leaders.TRAITS`, never characterisations.

**This forces one refactor, and it is the era gate extended rather than a new mechanism.**
Three tables are currently selected by `era_1936 = scenario.start_date < "1950"`: the leader
table, the resource endowments and the population overrides. A boolean cannot name a third
era, and a 1972 world falling through to the modern branch would be governed by the
politicians of 2026 and populated with 2026 census figures — `sov` is not in the modern
population table at all and would take the three-million default. So `era_1936: bool` becomes
an `Era` enum (`INTERWAR`, `COLD_WAR`, `MODERN`) carried explicitly on `Scenario`, with new
`LEADERS_1972`, `ENDOWMENT_1972` and `POPULATION_1972` tables beside the existing ones.
Both shipped eras get their value set explicitly, and **the branch each of them takes is
byte-for-byte the one it takes today** — proved by the fingerprints, not asserted.

`MINOR_ECONOMY` stays era-neutral. It has no 1936 variant either, so 1972 inherits an
approximation 1936 has lived with since it was written; flagged rather than fixed, because
fixing it is a balance change to a shipped era wearing this one's clothes.

### Guards

* **Additive.** New data modules, one new territory file, one registry entry, one preset.
  No CHECK enum widened; no schema change; no migration.
* **The two shipped fingerprints must not move.** Baseline, re-measured on clean HEAD before
  any edit: gathering-storm seed `57af0390…`, built world `30dd1d94…`; modern-day seed
  `bf7810a5…`, built world `130b2c3d…`; all twelve section digests as recorded. Checked
  **per section** after every commit that touches shared machinery, which is every commit in
  the era-gate piece.
* **Cold War gets its own fingerprint**, recorded in the changelog the way the other two are,
  and `scratchpad/seed_fingerprint.py` gains a third entry so it is proved reproducible by
  being built twice rather than by being written down.
* **Tests.** A `test_cold_war.py` pinning that the era builds, that its powers hold ground,
  that the blocs are commitments the engine's `defensive_pact_allies` actually returns, that
  the Council's permanent members are the historical five, and that both superpowers retain
  a second strike. A mock playtest exercising the bipolar dynamics end to end: a proxy
  flashpoint, a bloc invoked, the Council convened and vetoed, and deterrence holding.
  Several existing suites parametrize over `SCENARIOS` and pick the era up for free — which
  is the point of the registry.
* Ruff clean, full suite green, commits in logical pieces.

### What this deliberately does not do

* **No new mechanism.** If a Cold War want cannot be expressed with the pieces that exist,
  it is cut rather than built — this is a scenario, and the value of it is that it is the
  first thing to load every recently built mechanism at once. The one exception is the
  three-line generator fix, which is a bug the era merely uncovers.
* **No Berlin airlift, no missile crisis, no scripted event chain.** Same rule as both
  shipped eras: history is the causal root, not a timetable.
* **No divided city.** The map has one Berlin shape and the scenario says so plainly rather
  than inventing geometry.


## The Cold War era, built — what the sketch got right, and the four things it did not

The sketch above is `eb87781`; this is what came back from building it. **Built across
`b35efde` (the era gate), `256993e` (the scenario) and `efe53cf` (the tests).** 2256 tests
pass, ruff clean, and both shipped eras' seed and world fingerprints are unmoved section by
section.

### Held

The anchor year, the twenty powers, the bloc-versus-body split, the arsenal table, the ten
promoted countries, the ship classes and the research tree all landed as sketched. So did
the two things the sketch predicted would be needed and argued for in advance: the era gate
becoming an enum, and the three-line capital fix in `gen_territory.py`. Both shipped
territory files regenerate byte-identically — every generated nation matches, and the only
delta is the three hand-added 1936 nations a fresh output path cannot carry, which is the
writer's documented behaviour rather than a change.

### 1. `svn` is Slovenia

**The one real bug, and it is a whole class rather than an instance.** South Vietnam was
`svn`. `leaders`, `resources` and `demography` are all keyed by nation id *with any `m_`
prefix stripped* — which is the feature that lets one entry serve a country whether a
scenario promoted it to a power or left it a minor. It also means a power whose id happens
to be some other country's code silently takes that country's entry in all three tables: on
the first built world, Slovenia was governed by Nguyen Van Thieu, held nineteen million
people and produced South Vietnamese rice.

The Vietnams are now `dvn` and `rvn`, which are what the two states actually called
themselves. The guard is structural and parametrized over every registered scenario: **a
power id may share the code of a country it holds — `usa` is the United States — and may
not share the code of one it does not.**

Worth noting how it was found: by building the world and printing the leader table, not by
reasoning. The three per-era hygiene tests all passed, because none of them had been
extended to the new era yet. They have been now.

### 2. `defensive_pact_allies` does the more interesting thing

The test written first asserted that when one pact member attacks another, the pact returns
nobody. It returns *the remaining members*, and excludes the aggressor. That is the
documented rule — "when NATO attacks NATO the treaty has already failed, and the honest
answer is that the remaining members are freed to act, not that they owe the attacker
anything" — and the Warsaw Pact is the first place in any era where it can be put under
load, because it is the only bloc that has ever done it. The test now asserts 1968.

### 3. Which Vietnam event carries `war_declared` is a mechanical decision

`organisation.triggers_this_turn` finds wars that were already running by reading turn-0
`war_declared` events and naming their **actor** as the power a body would sit about. The
first draft filed the American withdrawal under that kind, which put the United States in
front of the Security Council for a war it is leaving — a claim the scenario should not make
by accident. The declaration is Hanoi's and the withdrawal is a `diplomatic_message`, with
the reason written at the entry so the next person to edit it knows what the kind does.

The Council then sits about North Vietnam and is vetoed by two permanent members who are
fond of it, which is the structural fact of the era and is now a test.

### 4. Nine powers were bankrupt on the first pass

Calibrated against the budget exactly as both shipped eras were, and exactly as
unsuccessfully on the first attempt. Everybody is now solvent or within a rounding error of
it except the four client states whose wars somebody else pays for — both Vietnams, Israel
and Egypt — which run small deficits with fifty to a hundred turns of reserve. That is true
about them and is the same allowance Modern Day makes for Pakistan. The United States runs
-0.01 a turn, which is twenty thousand turns of reserve and, in January 1972, thematically
about right.

### The fingerprints

| era | seed (no player) | built world |
|---|---|---|
| gathering-storm | `57af0390…` | `30dd1d94…` |
| **cold-war** | **`c63bd000fd3d95455d633015ecec2b07`** | **`62520184c47a01843ce184f722ffcd0d`** |
| modern-day | `bf7810a5…` | `130b2c3d…` |

`scratchpad/seed_fingerprint.py` builds each era twice and compares, so the new pair is
proved reproducible rather than written down. The cold-war *world* digest moved once during
the build, when the Vietnam history entry changed; its *seed* digest did not, because
history is laid over a created world by `apply_politics` and is not in the `WorldSeed` the
seed digest hashes. That is the distinction working.

### Still open, and flagged rather than fixed

* **`MINOR_ECONOMY` is era-neutral.** It has no 1936 variant either, so 1972's minors take
  modern shares of a preserved total. Fixing it means re-authoring the table per era, which
  moves a shipped era's balance — a different change wearing this one's clothes.
* **The Golan and the West Bank are not modelled**, though Israel holds both on this date.
  An override can only move a province between powers the province map covers, and Syria and
  Palestine are country-level here. Sinai is modelled because Egypt is promoted.
* **The West Berlin garrison's unit ids read `u_usa_germany_infN`.** The colony path takes
  its slug from the *country-level* name of the modern country the provinces belong to, so a
  holding drawn from German provinces is slugged "germany" whatever it is called. Cosmetic,
  and the alternative — slugging from the minor id — would move 1936's unit ids and with them
  its seed fingerprint, which is a much worse trade.
* **The Soviet Union is country-level**, so it holds fifteen large regions where West
  Germany holds ten small ones. Deliberate under the promotion rule, and it does mean a
  hypothetical NATO drive into Russia would be coarse in a way the central front is not.
* **Everything here is against a mock provider.** Nine tests run real turns on the real map,
  and none of them is evidence about how a *model* behaves in this era. The first live run
  is the next thing this era needs.


## An ocean is one region however wide it is, and air reach walked it in one hop

**Found by PT36**, the Cold War era's first live playtest, and it was never a Cold War
problem. The United States held the sky over four North Korean provinces --
Hamgyong-bukto, P'yongan-bukto, Rason and Ryanggang, at margin 0.96-0.97 -- while `usa`
and `prk` were at peace and **all six American air wings were sitting in `r_usa`**. The
walk was continental United States, North Pacific, Sea of Japan, Korea: three hops of
`move_range`, two of them across an entire ocean.

`_within` was a plain breadth-first search over `region_adjacency` and an ocean is one
region however wide it is, so crossing the Pacific cost exactly what crossing a county
costs. **It is the same shape as the free hop across open water that
`logistics.supplied_at_front` was fixed for**, one layer along, and the fix mirrors that
one.

### How much of the map this was

Measured on the seeded worlds, from aircraft that had not moved a province:

| era | regions projected over | of which **foreign land** |
|---|---|---|
| 1936 United States | 216 → **79** | 134 → **50** |
| 2026 United States | 200 → **77** | 115 → **48** |
| 1972 United States | 214 → **83** | 125 → **54** |

A 1936 United States with one air wing in Washington was contesting the sky over a hundred
and thirty-four foreign provinces. This has been true since the air layer was built and no
test noticed, because every air test is about two powers who share a border.

### The rule

Two constants, and the first is deliberately the same figure and the same reasoning as
`logistics._SEA_COST`:

* **`_OCEAN_HOP = 2`.** A sea is longer than the map makes it look and there is no field
  underneath it.
* **`_MAX_WATER_CROSSINGS = 1`.** A land-based wing may cross one stretch of water. The
  Channel, the Sea of Japan from Japan, the Taiwan Strait are all real capabilities and
  all one crossing from a real airfield. Two chained oceans is transoceanic reach, and the
  only thing that buys that is a deck.

`_within` is now shaped like `supply_map` rather than like a plain BFS -- once a step can
cost two, the first path to a region is no longer the shortest -- and **its state is
`(region, crossings)` rather than `region`**, because a path that has spent its crossing
and one that has not are not interchangeable at the same province. Collapsing them lets a
wing launder a crossing through a cheap overland route.

**The exemption is being at sea, not being a carrier.** `deck=True` is set when the unit's
own region *is* water, so a carrier on station projects across the ocean it floats on and
the same hull alongside in its home port is a coastal airfield. Keying it on the hull's
kind -- the first draft -- handed a fleet that never left harbour the reach of one on
station, which is how the United States kept its Korean sky through the first attempt.

### What it deliberately does not do

**`naval.crossable` is not consulted**, though supply, trade and landings all gate their
water on it. Those move things *through* the water; this passes *over* it, and an enemy
battle fleet does not stop an aeroplane -- the Luftwaffe flew the Channel while the Royal
Navy owned it. `_neighbours` already states the principle: air ignores what kind of border
it is crossing. What water costs an aircraft is distance, and distance is what is charged.

### It moves no combat-test expectations

**2256 tests pass unchanged.** Nothing in the suite depended on transoceanic air, because
every air test is a border war. Ten new tests in
`tests/test_air_does_not_cross_an_ocean_for_free.py`; **six of them fail with the two
constants relaxed**, including the reported case reproduced exactly -- wings home, at
peace, `r_prk_rason` covered.

Seed and built-world fingerprints are unmoved for all three eras: air reach is a *read*,
`projection` stores nothing, and the `air_control` rows a turn writes still go through
`apply_batch` as diffs, so rewind exactness is untouched. The walk has no randomness and
no dict-ordering dependence, so a recorded air war still replays.

### One behaviour change with a balance consequence, stated rather than buried

**A land-based wing must now be based within reach of the water it wants to cross**, and
1936 Britain's two seeded wings are in Greater London, which is inland. The RAF therefore
now covers its own island and the seas around it and **reaches no continental ground at
all**, where before it reached forty-one foreign provinces. Germany from Berlin still
covers nineteen (Austria, Bohemia, Alsace-Lorraine, the Corridor, Denmark, the
Netherlands) and France from Île-de-France twenty-three, so the layer is working -- this
is specifically the cost of an island power basing its air force in its capital.

That is a *scenario placement* consequence rather than an engine one and it is fixable by
moving a unit, but it is a real change to how 1936 plays and it is not being smuggled in.

**A residual, named:** a country-level power's single region touches every ocean its
coastline touches, so `r_usa` still reaches one crossing plus one hop -- France, Britain,
Japan, Norway, Iceland, Morocco -- at 0.72³ = 37% strength. That is a *granularity*
artefact, the same one the territory file exists to remove, and the honest fix for it is
promoting the United States to provinces rather than charging air more.


## The 1972 map had Bosnia, Micronesia and an Antarctic government

**Codex found it reviewing the era.** `build_world` makes an independent minor of every
modern map entry no power claims -- right for a scenario set today, wrong for every other
one. The Cold War world shipped with **209 minors**, among them Bosnia, Croatia, Slovenia,
Eritrea, East Timor, Micronesia, Namibia, Zimbabwe and Belize, none of them a state on
1 January 1972, and **Antarctica with a treasury, a government and a border guard**. The
preset's own hard constraints say *do not invent nations*; the scenario was inventing sixty
of them. It also inflates the Focus pool, because `rank_candidates` scores every living
nation on the map.

### The sketch, and it is 1936's trick applied to minors

1936 already composes Czechoslovakia, Yugoslavia and the Soviet Union out of successor
regions -- for *powers*, through `Power.regions`. There was no way to do it for a minor,
and no way at all to say a code was somebody's colony or nobody's ground.

`Scenario.sovereignty` is `(holder, display name, modern codes)` and covers three cases
with one mechanism:

* **successors merged into their parent** -- Yugoslavia's seven republics, Eritrea into
  Ethiopia, Somaliland into Somalia, Northern Cyprus into Cyprus;
* **dependencies given to whoever held them** -- Portuguese Angola, Mozambique, Guinea and
  Timor; British Hong Kong, Belize and the Caribbean; the American Pacific Trust Territory;
  French Djibouti and the Comoros; South-West Africa to South Africa; Spanish Sahara;
  **and the West Bank and Gaza to Israel**, which closes a gap the era shipped with;
* **renames**, where the modern name is a different polity -- Rhodesia, Zaire, Burma,
  Ceylon, Dahomey, Upper Volta, the Khmer Republic, the Republic of China.

`Scenario.absent` is the fourth answer: ground that is drawn and owned by nobody. Antarctica
is the only member, because the Antarctic Treaty of 1961 suspends every claim on it.

**147 nations, down from 229.** Deliberately left independent: Bangladesh (three weeks old),
Bahrain, Qatar and the Emirates (all 1971), Fiji, Tonga, Nauru, Mauritius, Malta.

### Two decisions inside it

**The seat is the first code the table lists, not the first one walked.** Codes are visited
alphabetically, so taking the first arrival governs Ethiopia from Eritrea and Yugoslavia
from Bosnia.

**A dependency is not separately garrisoned.** The border guard exists because an
independent minor has nobody else; a dependency has a metropole, its defence is that
power's army, and taking it is an act against that power -- which is most of the reason for
assigning it. The first draft did garrison them and **charged Britain thirty formations of
upkeep for Pitcairn, Montserrat and the Cayman Islands, bankrupting it in eleven turns on
an economy of thirty.** Colonies were lightly held; that is why they changed hands so
easily.

### Guards

**The mechanism is inert unless an era uses it**, and that is tested rather than asserted:
1936 and Modern Day declare no sovereignty and no absences, so they take the identical
path they took before, down to the unit ids -- a minor still gets `u_m_xxx_inf1` on its own
ground. Both eras' seed *and* built-world fingerprints are unmoved, measured with the
mechanism in the tree.

**The Cold War seed fingerprint moves, intentionally**, and is re-baselined below.

| era | seed (no player) | built world |
|---|---|---|
| gathering-storm | `57af0390…` **unmoved** | `30dd1d94…` **unmoved** |
| **cold-war** | **`de872c31640a87daee6b7029440620fe`** | **`0f21f34dc337a55b3d6922d8886535af`** |
| modern-day | `bf7810a5…` **unmoved** | `130b2c3d…` **unmoved** |

14 new tests in `tests/test_the_world_outside_the_powers_is_of_its_era.py`, including a
guard that every code the table names exists on the map -- an entry matching nothing would
silently leave the ground with whoever holds it today, which is the failure every Polish
override in the 1936 territory file had until its own guard was written.

### Flagged rather than resolved

Rhodesia is treated as a state because it had an army, a government and eleven years of
UDI, though de jure it was a colony in rebellion -- the alternative hands Salisbury's
brigades to London, which is worse. The New Hebrides were an Anglo-French condominium and
go to Britain because the engine has no shared sovereignty. Rhodesia, Namibia and the
Portuguese territories all had live independence wars in 1972 that none of this models:
they are ground held, not ground quiet.


## Britain was a triad with no aircraft and no land-based missiles

Codex, reviewing the era. `gbr` was seeded `triad` and the comment beside it said the
deterrent was submarine-only -- which is true, and is not what `triad` means.
`nuclear.DELIVERY_TIERS` spells it "aircraft, land-based missiles and boats at sea" and the
gambit briefing reads that string to the player, so the scenario was describing a force
Britain did not have in the one place a model would read it.

**The mechanical half is the better argument.** `survivability` is *tier plus boats*. At
`triad` Britain starts at 0.80 and reaches the 0.95 ceiling whether or not it owns a navy,
so **its deterrent is insensitive to its own submarines** -- the exact opposite of what a
submarine-only force should be. At `missile` it is 0.45 plus its three boats: 0.60, with
the boats doing the work and an anti-submarine campaign against them actually weakening it.
That is the right mechanism in the vocabulary that exists.

### Two engine issues found underneath it, flagged rather than fixed

Both are Codex's, both are real, and both are a bigger change than a scenario correction.

**1. `survivability` counts every submarine, whatever it carries.** There is one
`submarine` unit kind, so China's four *attack* boats raise its nuclear survivability from
0.20 to 0.40 -- and the People's Republic had no ballistic-missile submarine in 1972; the
first went to sea in 1981. The scenario cannot fix this from data. Pinned by a test that
asserts the current behaviour so a fix has to come past it deliberately.

**2. Delivery has no geographic range.** An `aircraft` arsenal can strike any target the
world is at war with, so a regional force reaches another hemisphere.

**Recommended, in order.** A fifth tier -- `sea` -- would fix Britain honestly and is the
smallest change, but it widens a closed CHECK enum and invalidates saves, so it wants to
ride with the next schema bump rather than alone. Distinguishing an SSBN from an attack
boat is the deeper fix and needs either a unit kind or a column; **I would do it as a
template flag rather than a kind**, because `naval` and `nuclear` both look up `submarine`
by name and a second kind would have to be taught to both. Delivery range I would leave:
it is a real gap, and the alternative is a range model for warheads that nothing else in
the engine has an equivalent of. **All three are Alex's call, not mine.**


## An attack submarine was a deterrent, because there is only one kind of submarine

Codex's finding, greenlit for fixing. `survivability` counted every hull with
`kind = 'submarine'`, so China's four *attack* boats raised its nuclear survivability from
0.20 to 0.40 -- in a year when the People's Republic had no ballistic-missile submarine at
all. The first Chinese SSBN went to sea in 1981.

### The template flag was the obvious fix and it cannot work

I recommended a flag on `unit_template` rather than a second unit kind, because `naval` and
`nuclear` both look `submarine` up by name and a second kind would have to be taught to
both. That reasoning still holds and the conclusion was still wrong: **a template flag is
per *era*.** There is one `submarine` kind in 1972, so a flag makes every boat that year a
deterrent or none of them -- and the thing that actually differs is the *nation*. Britain's
boats carry missiles and China's do not, in the same year, out of the same template.

**What already knows that, per nation, is the arsenal's own delivery tier.** A power whose
deterrent has no sea leg has no missile submarines by construction, so its hulls are a navy
and nothing more. `SEA_LEG_TIERS` is the gate; a boat counts toward survivability only
where there is something for it to be carrying.

No schema change, no new column, no new kind. **Attack submarines keep every naval property
they had** -- this changes what a boat means to `nuclear` and nothing about what it means to
`naval`, which is asserted rather than assumed.

### What moves

China: 0.40 → 0.20. Britain, currently `missile`, drops from 0.60 to 0.45 and gets its
boats back in the next commit when it becomes `sea`. The United States and the Soviet Union
are `triad` and unaffected. Modern Day's `pak`, `isr` and `prk` are all `missile` with boats
on the map, so their survivability falls to their tier -- **a deliberate reclassification of
what those arsenals mean, and the honest one**: none of the three had a declared sea-based
deterrent, and Modern Day's own comment says the Israeli and Pakistani sea legs are
"real and undeclared" and that a scenario should not resolve an argument the world has not.

No fingerprint moves: `survivability` is a read, and arsenals are written by
`apply_politics` over a created world.

The test that pinned the old behaviour has been come past deliberately, which is what it
was written for.


## A deterrent carried by boats is its own thing — schema 19, and it invalidates saves

**The first save-invalidating migration in the project, and Alex authorised it explicitly.**

Britain's 1972 deterrent is four Resolution boats and nothing else, and there was no honest
way to say that. `triad` claimed an air leg and a land leg it did not have and read them to
the player in those words. `missile` claimed a land-based force it never had — and once
boats were gated on the tier in the previous commit, it left the deterrent resting on
nothing at all, which is the opposite of what a submarine-only force is.

`sea` is the fifth tier. **Survival 0.65, above `missile` and below `triad`**: a boat on
patrol cannot be caught, which is the whole point of one, but a force that is *only* boats
has no second basket — find them and the deterrent goes with them, where a triad still has
silos. **Penetration 0.85, between them for the same reason**: a submarine-launched missile
flies a shorter, flatter path from an unpredictable bearing, and the tier that has every
option still has the best of it. Deterrence bonus 0.06 against `triad`'s 0.07.

### Why this costs a save, and what was done about it

`arsenal.delivery` carries a closed CHECK, and SQLite cannot widen one without rebuilding
the table — which means DROP and RENAME, which the additive migration guard refuses, and
correctly so: that guard is what stops a future migration eating somebody's world.

So the escape is **explicit, narrow and tested** rather than smuggled. `Migration` gains
`invalidates_saves` and `reason`; `check_migration` skips the additive rule for those and
for nothing else, and **refuses one that gives no reason**. A test pins the set of
migrations allowed to carry the flag at exactly `{19}`, so a second one is a decision
somebody has to make on purpose.

**Worlds written before schema 19 cannot be opened after it.**

### The CHECK lives in two places and the first fix only found one

`schema.sql` builds a fresh world and `migrations.py` upgrades an old one. Widening only
the migration left a fresh world *refusing* what a migrated one accepted, and every Modern
Day test died on `CHECK constraint failed` the moment an arsenal was seeded. Pinned now by
a test that inserts `sea` into a freshly created world and checks the enum is still closed
against a value nobody defined.

### Reclassifications, and the fingerprints they move

| era | power | was | now | why |
|---|---|---|---|---|
| cold-war | `gbr` | `missile` | `sea` | four Resolution boats, no land-based missile, ever |
| modern-day | `gbr` | `triad` | `sea` | submarine-only, which its own comment already said |
| modern-day | `fra` | `triad` | `sea` | the land-based silos on the Plateau d'Albion went in 1996 |

France in **1972** stays `missile` and means it: Mirage IVs and the Albion silos were both
in service, and the first boat was three weeks old.

Both are deliberate, so both built-world digests move; **neither seed digest does**, because
arsenals are written by `apply_politics` over a created world, and **1936 does not move at
all** because it has no arsenals.

| era | seed | built world |
|---|---|---|
| gathering-storm | `57af0390…` unmoved | `30dd1d94…` unmoved |
| cold-war | `de872c31…` unmoved | **`913b57ddffef344886d2670c53e3f6b3`** |
| modern-day | `bf7810a5…` unmoved | **`ef457bef4f9314d48066485dcbda1537`** |


## A handful of gravity bombs could reach the far side of the planet

Delivery had no geography at all. `launch` checked that the region existed, that it was not
open water, and that the launcher had warheads — and then let a nation with medium bombers
strike any province on earth it was at war with. Codex flagged it; I recommended leaving it
and Alex overruled that, correctly.

**Only `aircraft` is limited, and that restraint is the design rather than a shortcut.** A
ballistic missile, in a silo or in a launch tube, *is* intercontinental — that is what the
word means and what the tier is for — and a submarine goes where the sea goes. Gating
either on a hop radius would model something that is not true. What is true is that a
bomber has a combat radius: it flies from a field, over ground, and comes back.

`_TIER_REACH` gives `aircraft` six hops and everything else `None`, meaning global.
`within_delivery_range` walks `region_adjacency` outward from **every region the launcher
holds** — not from its capital — so forward basing counts, which is what forward basing is.
Six hops is generous on both maps and meant to be: it says *your own theatre and its
neighbours*, not *the other hemisphere*.

The common case costs one dict lookup and no query, because every intercontinental tier
returns before touching the database.

**Rewind-safe by construction rather than by plumbing.** It decides a *refusal*, before the
roll and before any diff is written, so a launch beyond reach spends no warhead and leaves
no row — asserted rather than assumed. No randomness and no dict-ordering dependence, so a
recorded exchange still replays.

### What it does not do, stated

Reach is in **hops**, not kilometres, and a hop is a province on one map and a country on
another — the same coarseness every range in this engine has. A six-hop bomber crossing
1936's provinces goes a great deal less far than one crossing Modern Day's countries. That
is a map-granularity artefact and the honest fix is not a different radius but a distance
model the rest of the engine does not have.


## A refused nuclear strike was still in the newspapers

Codex's second pass. The outcome event is written *before* the diff batch, because the
batch's diffs cite it -- the shape every engine act that can be refused has. `launch` then
reported a refused batch into `problems` and returned, leaving the event standing and
`happened` true, because `happened` was `bool(self.result)` and `result` is set by the
interception roll rather than by anything landing.

So a refused batch produced a turn report announcing a nuclear strike with **no warhead
spent, no crater, no reputational collapse and no reply**, and an event every later
briefing would quote. Worse: `organisation.triggers_this_turn` detects a nuclear use from
the *diff log* rather than from event text, so the Security Council would not convene about
an explosion the whole world could nonetheless read about.

**Exactly the class already fixed for refused battles and refused war declarations**, and
fixed the same way. `unrecord_events` retracts the news -- the same function `air.py` calls
for the same reason -- and `happened` becomes a claim about what was *applied* rather than
about what was rolled.

Five tests; two fail without the fix. The refusal in them is a database trigger rather than
a monkeypatched `apply_batch`, so `launch` sees exactly what a real rejected batch looks
like rather than a mock's idea of one.


## A submarine deterrent with no submarines still rode out a first strike

`sea` kept its 0.65 floor with every hull on the bottom, so a power whose entire second
strike had been sunk still survived a first strike with two thirds of its arsenal, kept six
warheads of ten, and fired back. Codex found it by reading the number rather than the
assertion — **the test beside it codified the residual**, asserting that a sunk force falls
back to its *tier*, which is what the code did and is nonsense.

Two changes. A hull only counts **at strength above zero**, because sinking is how the
naval layer expresses a loss and a wreck is not on patrol. And a `sea` arsenal with no
boats left collapses to `_SEA_WITHOUT_BOATS` = 0.10 rather than to its tier.

**Not zero, and `triad` is untouched.** The warheads still exist and a boat alongside is
still a boat; what is gone is anything on patrol or hidden, so a first strike finds almost
all of it. A `triad` in the same position keeps its tier, because a triad's floor *is* the
two legs that do not sink — and that difference is the entire reason the two tiers are
separate rather than one tier with a boat count.

The test that codified the bug now asserts the collapse, and three more were added: that a
sunk force answers with at most one warhead where an intact one answers with eight, that a
hull at zero strength is not on patrol, and that a triad keeps its silos.


## A formation at negative morale fought for the other side

`incinerate` subtracted a flat `_MORALE_HIT * share` from every unit in the struck province
with no floor, so a garrison already at morale 10 came out at **-15**. `sim.resolve`
multiplies strength by `morale / 50`, so a negative-morale unit *subtracts* from the
strength of the army it stands in: a warhead on a shaken enemy garrison made that defence
arithmetically **easier to hold**, and one on a province the launcher's own troops were
contesting helped the defender.

The air layer has clamped the drop to what the unit actually has since it was written;
`_charge_attrition` does exactly this. This is that floor, in the one place that did not
have it. Codex found it.

Three tests, one of which fails without the fix — the one that puts a unit of the
*launcher's own* in the struck province, because a warhead does not check the flag on the
vehicles.


## Two paths to schema 19, three disagreements, and the check that could see none of them

Codex compared a **freshly created** schema-19 world against a **migrated** one and found
they promise different things about `arsenal`:

| | fresh (`schema.sql`) | migrated (migration 19) |
|---|---|---|
| primary key | `(nation_id)` | `(world_id, nation_id)` |
| negative warheads | permitted | refused |
| `ix_arsenal_world` | present | **gone** |

The parity test compared table names and column names, which matched throughout.

**Migration 19 had the right contract and the fresh schema did not.** The composite key is
the important one: every other table here is keyed by world first because one file can hold
several worlds, and keyed on `nation_id` alone **two worlds in one file could not both give
the United States an arsenal**. `CHECK (warheads >= 0)` should be unreachable — a launch
clamps what it commits to what is held — which is exactly when a constraint is worth
having. The index went with the table migration 19 dropped and is recreated after the
rename.

### The parity test now compares the contract

Per table: every column's type, nullability, default and key position; the primary key in
order; every index and the columns it covers; every foreign key with its delete rule; and
every CHECK, normalised. It found a **third** divergence nobody had named:

**`commitment.breached_by_nation_id` has no foreign key in a migrated world.** Migration 15
added it with `ALTER TABLE ADD COLUMN`, and SQLite cannot attach a foreign key that way; a
fresh world has `REFERENCES nation(id) ON DELETE SET NULL` and a migrated one has nothing,
so deleting a nation leaves a migrated world pointing at it.

**Not fixed here, and named rather than hidden.** Repairing it means rebuilding
`commitment`, which is a second save-invalidating migration, and that is a decision for
Alex rather than something to fold into a repair about arsenals. It sits in
`KNOWN_SCHEMA_GAPS` with its reason, the parity test skips exactly that one aspect and
checks everything else, and a second test fails if the exemption ever stops describing
something real — the same discipline `test_rewind_holes` puts on its own allowlist.

No fingerprint move: `fingerprint()` hashes rows, not DDL.


## Hong Kong was British homeland, so it was nuclear-protected

`create_world` seeds every region with `home_nation_id = owner_nation_id`. That is right
for a country's own ground and wrong for everything a power merely *holds* --
and `nuclear.deterrence` applies only to the ground a power is **from**. So the Cold War
map made Hong Kong British core at 0.105, Djibouti French, the West Bank Israeli and West
Berlin American: **a nuclear power's colonies were nuclear-protected**, the exact inverse
of the rule the term was written to obey. The same column is read by conquest, logistics,
unrest and settlement. Codex found it.

A dependency's home is **nobody**. There is no nation for Hong Kong in 1972, and inventing
one would be inventing a state -- the thing the sovereignty map exists to stop. `NULL` says
what is true: held, and not anybody's core.

**Written in `apply_politics`, not in the seed**, for the reason the arsenals are: a
`RegionSeed` field would change the dataclass every era's seed digest is computed from,
moving three fingerprints to express a fact about two. Both kinds of held ground come from
the scenario's own tables -- a **dependency** is a code the sovereignty map gave to somebody
other than itself, a **colony** is what `Scenario.colonies` names.

**Two tests codified the bug and are corrected.** 1936's asserted "a colony is Italian
core"; the Cold War deterrence test picked its "homeland" by lowest region id, which is
West Berlin -- the very ground that must not be deterred.

**1936 legitimately moves**, because it has Eritrea and Italian Somaliland. Modern Day has
no dependencies and does not move. Every seed digest is unmoved.

| era | seed | built world |
|---|---|---|
| gathering-storm | `57af0390…` unmoved | **`1183520f7f60c10fc3050a61f644dd87`** |
| cold-war | `de872c31…` unmoved | **`608a8f901dd69e46a15c54ae9f972bc3`** |
| modern-day | `bf7810a5…` unmoved | `ef457bef…` **unmoved** |


## The foreign key `ADD COLUMN` could not attach — schema 20

The gap the strengthened parity test named the moment it could see constraints. Migration
15 added `commitment.breached_by_nation_id` with `ALTER TABLE ADD COLUMN`, and SQLite
cannot attach a foreign key that way — so a **fresh** world has
`REFERENCES nation(id) ON DELETE SET NULL` on that column and a **migrated** one has
nothing. Delete a nation from a migrated world and every agreement it broke goes on naming
it.

The same 12-step rebuild schema 19 used on `arsenal`, the same `invalidates_saves` flag
with its own stated reason, and the index recreated after the rename. `KNOWN_SCHEMA_GAPS`
is now **empty**, which is what it was for: it held this entry for exactly as long as it
took to authorise the fix.

Tested as behaviour rather than as DDL text — delete the nation, watch the column go NULL.
The parity test now passes on every aspect of every table with no exemptions.

**Schema 19 → 20. Both invalidate saves, deliberately.**


## NATO had six members and the Warsaw Pact five

Codex counted them. NATO had **fifteen** members in 1972 and the Warsaw Pact **seven**; the
scenario seeded the powers it had designed and forgot everybody who was a minor on the map.
`defensive_pact_allies` reads `commitment_nation` and does not care whether a party is a
designed power or a one-region minor — so **Belgium could be attacked and the alliance owed
it nothing**, and the same for Hungary.

Added: Belgium, Canada, Denmark, Greece, Iceland, Luxembourg, the Netherlands, Norway and
Portugal to the North Atlantic Treaty; Bulgaria and Hungary to the Warsaw Treaty and to
Comecon, which they were founding members of. **Albania is deliberately not a party** — it
stopped participating over the invasion of Czechoslovakia and left in 1968.

Three earlier tests asserted exact equality against the incomplete membership and are
corrected to assert containment plus the specific small members, which is the claim that
survives somebody adding a sixteenth.

Cold War built world moves to `08777cfb573893aae72e0824c358db92`; its seed and both other
eras are unmoved.


## A bake that failed its own verifier reported success, and three smaller things

**The bake never ran the verifier.** `atlas map verify` existed, returned 2 on a bad pack,
and nothing called it — so a freshly baked `marine-10m` printed its statistics, exited 0,
and shipped. `atlas map bake` now verifies what it wrote and returns 2 when that fails,
which is the only moment anybody is looking.

**The two LOD borders, documented rather than papered over.** A fresh `marine-10m` fails at
`North Atlantic Ocean / Strait of Gibraltar` (LOD 1) and `Southern Ocean / Drake Passage`
(LOD 2): simplification at those tolerances pulls the smaller region's edge off the larger
one and a gap opens. **It is a rendering defect and not a gameplay one**, and that is
checkable rather than hopeful: `earth.derive_adjacency` and `derive_coast_adjacency` both
take `lod: int = 0` and the seeder never passes anything else, so every border the engine
reasons about is computed at full detail. The gap is visible only when zoomed out. Fixing
it properly means a shared-border floor in the simplifier, of the same shape as the
existing "kept finer geometry for N region-LODs that would have vanished" rule — a real
change to how every pack is built, and not one to make while fixing a report.

**Three smaller things, all Codex's.**

`agy models` in 1.1.11 lists `slug<TAB>Display Name`, and `parse_models` took the whole
line as one name — so `normalise_model` produced `gemini-3.6-flash-high-gemini-3.6-flash-high`
and `atlas doctor` reported **both Antigravity pins as models the CLI does not offer**,
while the CLI answered on them perfectly. Third naming scheme in three releases, which is
exactly why this is parsed rather than hardcoded. **`validate_pins` now reports no
problems.**

`pt36_replay` printed *THE WORLD AT THE END OF OCTOBER 1972* over the untouched starting
position after the replay had correctly missed on turn 1 — the most misleading moment it
could have said it. The heading now says which world it is showing.

`test_a_landlocked_country_has_no_coast` filtered its pack list to the ones that exist and
then asked only whether the list was non-empty, so a **partial** install passed the
precondition and the test failed on San Marino not existing — reporting a missing download
as a bug in the map. It now skips naming the packs it wants. The handoff's bake command was
missing `admin0-50m` for the same reason and now lists all three.


## The Security Council had fourteen members and no idea what kind of body it was

Two of Codex's scenario-correctness items, both about the organisation layer.

**The Council is fifteen seats: five permanent and ten elected.** It was seeded with
fourteen members chosen by which powers the scenario happened to have designed, so Turkey,
Poland, Czechoslovakia, Romania, Cuba, Israel and Egypt each had a vote they did not have
in 1972. `_support` scores *every* member, so the room deciding whether a resolution
carried was wrong by seven. The ten elected are the real cohorts — Argentina, Belgium,
Italy, Japan and Somalia serving 1971-72; Guinea, India, Panama, Sudan and Yugoslavia
serving 1972-73. Pure data; no mechanic changed.

**`organisation.kind` was stored and never read.** `turn_engine` picked the first credible
body that was not itself the accused, so an `alliance` — a body whose charter runs to its
own members — would convene on a power that had never joined it and never accepted its
authority, exactly as a `universal` body does. The column exists to draw that distinction
and nothing drew it. `has_jurisdiction` now does: universal reaches everybody, which is the
whole reason the Council can act on a power that is not its friend; an alliance reaches its
own members and nobody else.

**Inert for both shipped eras**, which seed only `universal` bodies — so this is a
correctness fix ahead of the first scenario that seeds a bloc as a convening body, and the
`kind='alliance'` arm of the schema stops being decoration.

No fingerprint move: `organisation_member` is not in `_FINGERPRINT_TABLES`, which is worth
knowing on its own — the Council's *membership* is outside the digest, in the same way
`commitment_nation` is.


## Two of Codex's items are design calls, not repairs — flagged with recommendations

Both were "do it if clean, flag if bigger". Both are bigger, and here is why and what I
would do.

### Boat-level SSBN capability

For a `sea` or `triad` power every generic submarine still counts as a missile boat.
Britain's three are all deterrent; in reality a navy runs SSBNs and attack boats together.

**Why it is not clean.** There is one `submarine` kind per era and the fleet is declared as
counts per `ShipClass`, so "two SSBN and one SSN" is unsayable without either a second unit
kind — which `naval.sea_lift` and `nuclear.survivability` both look up by name, and which is
the change I argued against and still would — or a per-unit column, which is a schema
change plus a seeder decision about which hull is which.

**What I would do:** `Scenario.deterrent_boats: dict[str, int]`, a per-nation count of how
many of that power's submarines carry missiles, with `survivability` counting
`min(afloat, declared)`. Data only, no schema, no new kind, and it degrades sensibly —
losses eat the attack boats first and the deterrent last, which is optimistic for the
defender and the right way to be wrong. **What it does not do** is let a *particular* boat
be sunk: the engine would still not know which hull was which, so an anti-submarine
campaign that deliberately hunted the SSBNs could not be modelled. That limitation is why
this wants a decision rather than a patch.

### Finite missile range, MRBM against ICBM

`missile` is globally unlimited, and France's 1972 S2 reached about 3,500 km — a
medium-range weapon that could hit Moscow from the Plateau d'Albion and nothing in the
western hemisphere. The tier is doing duty for two different weapons.

**Why it is not clean.** `_TIER_REACH` already supports a finite hop count, so the
*mechanism* is free; what is not free is the *tier*. A sixth entry means widening the closed
CHECK on `arsenal.delivery` again — a **third** save-invalidating migration — and the hop
counts would then be doing continental-scale work on a map where a hop is a province in one
era and a country in another.

**What I would do:** nothing yet, and if anything, not a tier. The honest version is a range
in kilometres against real geometry, which the engine has nowhere else and should not grow
for one column. If a distinction is wanted sooner, the cheap and defensible one is to give
`aircraft` and a hypothetical `missile_regional` the same treatment through `_TIER_REACH`
and accept hops as the unit — but that is a scenario-fidelity gain bought with a save
invalidation, and it is Alex's trade to make rather than mine.


## The morale clamp was local; the guarantee is global

`3b0dc0b` floored morale at the nuclear call site. That fixes the path that was known to
break it and says nothing about the next one. **`morale-is-a-percentage` is the same claim
about the world**: no unit, at any phase, has morale outside 0-100.

It matters because `resolve` multiplies strength by `morale / 50`, so a unit below zero
does not merely fight badly — it **subtracts from the strength of its own army**. A warhead
on a shaken garrison made that garrison's own side weaker, which is the wrong sign, not a
wrong magnitude.

**`_ALWAYS`, and the phase set is the claim.** There is no moment in a turn when a unit
legitimately has impossible morale: every writer goes through a diff and a diff applies a
value that was already clamped, so a violation is a bug at whichever call site produced it
rather than the engine being partway through making the world consistent.

**The upper bound is checked although nothing writes it.** `orders` fortifies to a ceiling
of 95, and `unit.morale` is `REAL NOT NULL DEFAULT 50` with no CHECK behind it — so 140 is
storable, nothing would notice, and the cost of asking is one scan.

Twelve tests. Seven fail with the invariant removed, including the closure test that
requires every registered invariant to have a way to break it. Both ends of the range are
pinned as legal: a formation with nothing left is at zero, not below it, and both the
nuclear and air clamps land exactly there.

No behaviour change — this is an instrument, not a rule.


## Which boats are the deterrent — schema 21, additive

The SSBN/SSN split was half done. The tier gate stopped a power with *no* sea leg counting
its boats; for a `sea` or `triad` power **every hull still counted as a missile boat**, so
Britain's three submarines were three deterrents and the United States' nine were nine.

`arsenal.deterrent_boats` is the missing fact, and it is a **count on the nation** rather
than a flag on the unit — because there is one `submarine` kind per era and nothing on a
unit or its template can distinguish Britain's boats from China's, out of the same template
in the same year. A second kind would have to be taught to `naval` and `nuclear`, both of
which look `submarine` up by name.

`survivability` takes **`min(afloat, declared)`**, so losses eat the attack boats first and
the deterrent last. **Optimistic for the defender, and the honest way to be wrong**: the
engine does not know which hull was which, so an anti-submarine campaign that deliberately
hunted the missile boats cannot be modelled. What it does model is that sinking a navy
eventually sinks the deterrent, and that a power with no missile boats never had one.

**Additive and nullable.** `ALTER TABLE arsenal ADD COLUMN deterrent_boats INTEGER` — not
save-invalidating, and NULL means "the scenario did not say", which counts every boat
exactly as the column did before it existed. So an existing world and both undeclared eras
behave as they did. Added to `schema.sql` **and** the migration, which the parity test then
checks — the lesson from schema 19, where widening only one of the two left a fresh world
refusing what a migrated one accepted.

The 1972 counts: `usa` 4 of 9, `sov` 5 of 12, `gbr` 2 of 3, `fra` 1 of 2, **`chn` 0 of 4**
and `isr` 0 of 1. China's nought is written down rather than left implicit in its tier,
because the People's Republic had no ballistic-missile submarine until 1981 and a fact that
lives only in a tier is lost the moment somebody edits the tier.

Twelve tests, including the case Alex named: a power *with* SSBNs that loses all of them
lands on **0.10**, exactly where a power that never had any lands.

**All three built-world digests move**, because `arsenal` is in `_FINGERPRINT_TABLES` and it
has a new column — the schema-16 situation exactly, and harmless for the same reason:
digests are only ever compared within a schema version. **No seed digest moves.**

| era | seed | built world |
|---|---|---|
| gathering-storm | `57af0390…` unmoved | **`1183520f…` unmoved** |
| cold-war | `de872c31…` unmoved | **`d6eb2f08f4054406422d398f3ad08f28`** |
| modern-day | `bf7810a5…` unmoved | **`312d78af299184f157c22adbbb1d2112`** |


## The scenario auditor — `atlas audit <era>`

An invariant says *this cannot be true of a world that is not broken*. It is about the
engine and holds in every era. Nothing said the other thing: **the world this scenario
builds is the world it claims to be describing.** Article 5 is a fact about the engine;
that fifteen named states were parties to the North Atlantic Treaty on 1 January 1972 is a
fact about 1972, and no invariant can know it.

Nothing was checking it. Each era's test file pins the handful of facts whoever wrote it
happened to think of, and every fact nobody thought of is unguarded — which is how South
Vietnam shipped as `svn` and quietly took Slovenia's population, and how the Security
Council shipped with fourteen members. Both were found by a person reading, and a person
reading is not a mechanism.

**The manifest is JSON and imports nothing, and that is the entire design.** The obvious
implementation is worthless: an auditor that reads `SCENARIO.organisations` and checks the
database matches proves only that `apply_politics` can copy a tuple, and it would have
passed on the day the Council had fourteen members — the scenario said fourteen and the
world had fourteen. So the manifest is a *separately written record of what the period
was*, kept out of Python where it cannot reach the scenario module even by accident. The
audit compares two independent statements about the same year. Where they disagree, the
manifest is not automatically right; the disagreement is the finding, and somebody has to
decide which of the two was wrong.

Ten checks: alliance membership by exact party set; organisation membership **and**
permanent seats, which fail independently because a Council with the right fifteen and the
wrong five is deadlocked by the wrong powers and counts perfectly; dependencies, cores,
occupation, ungoverned ground and **who does not exist yet**; leaders in office on the
start date; treaty status against ratification dates, because signed is not in force;
arsenals **in both directions**, since an arsenal the era did not have is invisible until
somebody uses it; every required geometry pack; and the delivery boundary.

**The boundary is walked, not asserted.** Checking `_TIER_REACH["aircraft"] == 6` proves a
dict has not been edited. The check breadth-first walks the world's own adjacency out of
the launcher's held ground, takes a region first reached at exactly six steps and one at
exactly seven, and requires `within_delivery_range` to accept the first and refuse the
second. Both failure directions, on the map a warhead actually crosses.

**Silence is a finding.** The cheapest route to a green audit is an empty manifest, so an
empty section must be *declared* in `silent_on` with a reason — "there were none" and
"nobody wrote it down" are identical in an empty list and opposite in meaning. The CLI
prints the fact count and the declared silences on every run, including a clean one. A test
pins a floor on both.

All three eras pass: **62 facts for 1972, 36 for 2026, 26 for 1936.** Thirty-one tests,
including nine that break a manifest on purpose to prove each check can fail.

### What it found on its first run

Both declared rather than asserted, so the audit passes on defects it exists to record:

* **1936 has no era sovereignty map.** Israel, Pakistan, Bangladesh, Zimbabwe, Namibia,
  East Timor, Ghana, Indonesia, Malaysia, Singapore, Algeria, Libya, Burma and Zaire are
  all sovereign states on the 1936 map. Exactly the class of bug the 1972 `sovereignty`
  table fixed; 1936 has never had the same pass. The largest scenario-accuracy gap in the
  repo, and it belongs to the oldest era rather than the newest.
* **Modern Day models no occupation at all** — Russian-held Ukrainian ground is a relation
  and a set of strengths, never a controlled province.

Smaller: 1936 owns Ireland as British home ground, and Antarctica is ungoverned in 1972 and
owned in 2026 — the two eras disagree and one of them is wrong.

And one packaging bug fixed in passing: `package-data`'s `scenarios/*.json` never covered
`scenarios/territory/*.json`, because a glob does not recurse. An installed wheel would have
played every province-drawn era at country granularity and said nothing about it — which is
the same silent degradation the auditor's `packs` check now reports.

No schema change, no fingerprint movement: all three seed digests and all three built-world
digests are byte-identical to the previous commit. It only reads.


## 1936 was the 2020s map with older powers, and now it is not

The pass the oldest era never got. Every modern map entry no power claimed became an
independent state, so **1936 shipped with Israel, Pakistan, Bangladesh, India, Burma,
Indonesia, Malaysia, Singapore, Algeria, Libya, Ghana, Nigeria, Kenya, Zimbabwe, Namibia,
Zaire, Angola, Mozambique, Vietnam, Syria, Lebanon, Transjordan, Cyprus, Malta, the
Philippines, Taiwan, Korea and the Sudan as sovereign nations** — a hundred and forty-five
anachronisms, in a scenario whose own hard constraints say *do not invent nations*. The
scenario auditor found it on its first run. This is the same treatment the 1972
`SOVEREIGNTY` table gave the Cold War, using the same machinery: no new mechanism.

**1936 is the hardest year on the map for this**, and that is why it matters most here. It
is the high-water mark of European empire — roughly a third of the world's land was
somebody's dependency, and the modern pack draws every one of those dependencies as a
country. **202 minors become 57**, and every one of the 57 is a state that actually existed
on 1 January 1936.

A dependency is **ground, not a nation**: the holder owns the region and pays no separate
garrison, which is right twice over — a colony's defence *was* the metropole's army, and
giving each of Britain's ninety holdings a border guard would have charged London ninety
formations of upkeep, the bug that bankrupted it in eleven turns when the 1972 table first
shipped. A test pins it.

**What stays independent, because it was.** The Dominions — Canada, Australia, New Zealand,
South Africa and the Irish Free State — are self-governing states under a shared crown and
the Statute of Westminster was five years old. Iraq (1932) and Egypt (1922) are independent
with British treaties over them. Latin America, Iran, Afghanistan, Saudi Arabia, Yemen,
Nepal, Bhutan, Muscat and Oman, Liberia, Siam and every European neutral are their own.

Judgement calls, flagged rather than buried: **Southern Rhodesia** is a British dependency
(a self-governing colony, but London held the reserved powers, and the alternative invents
a nation); **Cameroon and Togo** were split French/British mandates and France held the
larger share of each; **the New Hebrides** were an Anglo-French condominium and Britain gets
them, as in 1972; **Anglo-Egyptian Sudan** was also a condominium and is British, which is
what it was in practice; **Iceland** stays independent, a sovereign kingdom in personal
union with Denmark; **Mandatory Palestine is two codes**, `PSX` and `ISR`, and both are
British.

### Ireland was a British county, and it was a Dominion

The second 1936 finding. `gbr.regions` was `("GBR", "IRL")`, so the Irish Free State was
British ground **and a British core** — home soil, not even a colony. It had its own army,
its own constitution and the Statute of Westminster. It is now `m_irl`, "Irish Free State",
governing itself from Dublin.

### Antarctica: the two eras disagreed and only one could be right

1972 left the continent ungoverned; 2026 gave it a government, an army and a treasury. The
auditor found the disagreement. **All three eras now agree**: five overlapping claims, no
permanent inhabitants and no government is ownerless ground, not a nation. `ABSENT` gains
`ATA` in 1936 and in Modern Day.

### The tables that followed

`ENDOWMENT_1936` endowed India, Malaya and the Indies, which are no longer nations. Folded
into their holders rather than deleted — the tin was real: Britain's line is now the
Empire's, and the Netherlands gets the Indies' oil, which is most of why Japan looked south.
`MINOR_ECONOMY` loses `ata`, and the two demography tests that used Antarctica as their
example of empty ground now use the French Southern and Antarctic Lands, which is the same
argument with a nation still attached.

### The manifests now assert it

The 1936 `not_yet` and `ungoverned` sections move **out of `silent_on` and into asserted
facts** — thirty states that must not exist and one continent that must have no government.
The audit went from 26 facts to **74**, and every one of these anachronisms would now be a
failing test rather than a declared silence. Modern Day's `ungoverned` silence is likewise
gone.

### Fingerprints

**The 1936 seed digest moves, legitimately and by design** — this is a change to who exists,
which is exactly what a seed digest is for. Cold War is untouched in both.

| era | seed | built world |
|---|---|---|
| gathering-storm | `57af0390…` → **`d89e1c8d9b3d2a29cfae8f279c4f5416`** | `1183520f…` → **`9cebbf8aaeaf0ba1ce4e9cddf75e4ea3`** |
| cold-war | `de872c31…` unmoved | `d6eb2f08…` unmoved |
| modern-day | `bf7810a5…` → **`3e316236b0eaa2948eed194a241877bc`** | `312d78af…` → **`29f03aa0c2f97218fa3e243c8c3b3f6d`** |

1936: **219 nations → 74**, **442 units → 297** (145 minors, each with a border guard, are
now somebody's ground). Regions and adjacency are **unchanged at 488 and 1279** — the same
map, differently owned, which is what a sovereignty table should do and a useful check that
it did not quietly redraw anything. Modern Day: **242 → 241 nations, 579 → 578 units**,
Antarctica and its garrison, and nothing else.

2401 passed, 1 deselected, ruff clean. No schema change.


## Russia was at war over ground it did not hold

The last finding the scenario auditor was still reporting. Modern Day modelled the war in
Ukraine as a relation and a set of national strengths and nothing else — **no region
anywhere on the 2026 map had a controller that differed from its owner.** The engine had
the columns to say otherwise and no era had ever used them.

`Scenario.occupied` — `region id -> controller`, written through `apply_politics` for the
reason the arsenals and the dependencies are. **Three columns, three different facts:**

* **`owner_nation_id` stays Ukraine.** A settlement has something to hand back, and the map
  still says whose it is.
* **`controller_nation_id` is Russia.** This is what `COALESCE(controller, owner)` picks up
  throughout the engine — supply, delivery range, sea lift, who a front is against.
* **`home_nation_id` stays Ukraine**, which is the one a scenario would most easily get
  wrong. Were it Russian, `deterrence` would extend a Russian nuclear guarantee over ground
  Ukraine is trying to retake — the exact inverse of the rule, and the same defect the 1972
  dependencies had before `24ef42d`.

That is also why occupation needs its own table rather than a sovereignty entry: a
**dependency** is held ground that is nobody's core, and an **occupation** is held ground
that *is* somebody's core, and somebody who is still fighting for it.

### Crimea was Russian ground, and it is not

`_MODERN_OVERRIDES` used to be empty on the argument that a 2026 scenario drawn on a 2020s
map has no moved border to put back. That was wrong about one thing: **Natural Earth files
Crimea and Sevastopol under `RUS`**, the generator took it mechanically, and the two shapes
ended up buried inside a Russian region called Adygey. Taking a map's filing decision as a
political fact turned an annexation into sovereignty. Crimea is now a region of its own,
Ukrainian and Russian-held. The test that pinned the old behaviour **codified the bug** and
has been corrected rather than deleted.

### Where the map is coarser than the war

A region is held by one nation or the other; there is no half, and four of the five oblasts
here are divided in reality. The rule applied throughout is **whoever holds the oblast
capital**, and each entry is flagged in the module for what that costs:

* **Crimea** — exact. Russian-held entire since 2014, the only entry that is not an
  approximation at all.
* **Luhansk** — very nearly exact.
* **Donetsk** — rounds *toward* Russia: about four fifths including the city, against a
  Ukrainian-held northern corner around Kramatorsk and Sloviansk this map cannot draw.
* **Zaporizhzhia and Kherson round toward Ukraine, and this under-credits Russia.** Russia
  holds most of both by area — Melitopol, Berdiansk, the nuclear plant, the east bank of the
  Dnipro — and Ukraine holds both oblast capitals: Zaporizhzhia city was never entered, and
  Kherson city was retaken in November 2022. Marking either Russian would hand Moscow a city
  it has never held, which is the worse of the two errors.

**What genuinely cannot be expressed, stated rather than forced:** a province contested by
both. The engine has one controller per region and the map has one shape per oblast, so "the
front runs through here" has nowhere to live at seed time. Splitting Zaporizhzhia and Kherson
along the Dnipro would need geometry Natural Earth does not carry at admin-1.

### The regrouping, and its blast radius

Six overrides give Crimea and the five oblasts the war is about regions of their own — plus
Kharkiv and Dnipropetrovsk, which the mechanical clustering had swept into a region named
after Sumy. **Ukraine goes from 9 regions to 12.**

Taking two shapes out of the Russian pool moved every Russian cluster boundary, so half of
Russia's region ids changed although nothing about Russia did — the clusters are named after
one member and the member changed. `HEARTLANDS` was remapped to what the new clusters
actually contain: Tatarstan is the Volga industrial belt, Khanty-Mansiy the oil and gas,
Khakass reaches the Kuzbass. Ukraine's weights were rewritten oblast by oblast, and Donetsk
and Luhansk are weighted for what the Donbas *was* — nine years of war have taken most of it
apart and the engine has no way to say "worth this, and wrecked".

**An invented occupation is refused loudly**: a region id matching nothing, or a controller
that is the region's own sovereign, raises rather than updating zero rows and reporting
success.

### The auditor asserts it now

Modern Day's `occupied` and `cores` sections move out of `silent_on` into asserted facts —
three occupation entries with owner *and* controller, and three core entries pinning that
occupied ground is not the occupier's homeland. **43 facts, up from 37.** The era's two
remaining silences are both honest: it seats no dependency, and nothing on a 2026 map
postdates 2026.

### Fingerprints

Both Modern Day digests move: the **seed** because the territory file regrouped, which is a
real change to the map, and the **built world** because of that plus three controller
columns. 1936 and the Cold War are untouched — the other two era files regenerate
byte-identically.

| era | seed | built world |
|---|---|---|
| gathering-storm | `d89e1c8d…` unmoved | `9cebbf8a…` unmoved |
| cold-war | `de872c31…` unmoved | `d6eb2f08…` unmoved |
| modern-day | `3e316236…` → **`ceb1f190acd7f592159a66d0310f1dcb`** | `29f03aa0…` → **`16441fc2ccc93336843fa0e66846cf68`** |

Modern Day: **429 regions → 432**, **1093 adjacency → 1096**, nations and units unchanged at
241 and 578. Three more Ukrainian provinces and the borders between them, and nothing else.

2403 passed, 1 deselected, ruff clean. No schema change.


## A patron had no sentence for "reinforce my ally"

`push` onto an ally's ground is refused — you are not at war with them. `hold` over it was
refused too, because you do not hold it. So **the one thing a patron most wants to say about
a client under attack was the one thing the objective channel could not express**, and the
orders went into refusals turn after turn. Worse, the refusal it got was *"you are not at war
with whoever holds r_south; declare war before attacking it"* — true, useless, and readable
by a model as an invitation to declare war on its own ally.

**No new objective kind and no widened CHECK.** `hold` already means *this is where my
reserves march*; all that was missing was letting it mean that about an ally's border.

* `hold` over ground held by an **active** defensive-pact partner is accepted. `pending` is
  not a guarantee — the distinction the 1972 Ostpolitik treaties exist to carry.
* Allied ground is `COALESCE(controller, owner)`, so an ally's **dependencies** count.
  1936's British Empire is ninety regions Britain holds without owning, and an ally marching
  into one of them is not a subtlety.
* `push` onto it is refused **with the legal equivalent named**: declare `hold` instead.
* `_distance_to_objectives` treats a hold over ground you do not hold exactly as it treats a
  push target — the army masses on its own side of the border. That is what a relief force
  is; nobody teleports a corps into Poland.
* The briefing says which verb an ally's ground takes, but only to a nation that actually
  has a pact partner — an unusable instruction costs tokens and teaches nothing.

Seven tests. No schema change, no fingerprint movement.


## A balance you only see at the point of firing is a countdown, not a deterrent

The MAD picture — own arsenal, surviving warheads, who answers and with how much — lived in
`nuclear_rules_block`, which is built for the **gambit prompt**. So it reached a leader
exactly when that leader was already contemplating a strike, and never while it was deciding
whether to invade, escalate, sign or back down — which is where deterrence actually does its
work.

`nuclear_balance_lines` puts the same picture in the regular strategic briefing: what you
have and how it is delivered, how much survives being hit first, its reach, who answers and
with how many, and the one-line read of whether any order of operations leaves your capital
standing. **It recomputes nothing** — `arsenal_of`, `survivability` and `surviving_warheads`
are the same three the launch path uses, so the number quoted and the number fired cannot
drift apart. Empty for a nation with no arsenal, which is almost all of them.

**Who counts as the adversary took two attempts, and the first was wrong in a way 1972
caught.** "Whoever you are at war with, else the largest arsenal" went silent for the United
States, because the United States in 1972 is at war with North Vietnam — which has no
warheads — and the arsenal shaping every decision it makes is the one it is *not* fighting.
The rule is now **"whoever you are fighting who could answer you, and otherwise the largest
arsenal on the map"**, which is the Cold War's actual shape: a proxy war under a balance
held somewhere else. The line says which of the two it is showing.

Ten tests. No schema change, no fingerprint movement.


## A coup and six finished workshops, in whatever order the engine wrote them

The news feed was ordered by recency, and recency is the wrong axis when
`autonomous_investment` develops every unattended nation every turn — construction is the
most numerous kind on the map by a wide margin. The window's cap already had to exclude
*other* nations' works to stop them crowding out the wars; that was a blunt version of this,
and it did nothing about a staff's own building programme or about the order things printed
in.

`SALIENCE` ranks the event kinds in four bands, and the boundaries are about what a staff
must not miss rather than about drama. **Three** is somebody using force or changing what a
state is — a launch, a coup, a declaration of war, ground changing hands. **Two** is a
commitment made or broken, which decides who fights beside whom next turn. **One** is
everything else that happened to somebody. **Zero** is the tick: a workshop finished, a
reform passed.

Two things use it, and the second is the one that is not cosmetic:

* **The briefing prints gravest first.** Turn numbers are on every line, so it still reads
  as a chronology.
* **The window itself is now spent by weight.** Ordering what survives the cap is no use if
  the cap threw the coup away first — thirty of a nation's own works, written after it, hold
  the high ids and win a window ordered by recency. A test reproduces exactly that.

Two details worth the space. **A nuclear launch is a `narrative` event**, because the event
enum is closed and widening it invalidates saves — so kind alone cannot rank the gravest
thing on the map, and `salience_of` also reads the words the engine itself writes. And an
**unranked kind defaults to one, not zero**: a kind nobody has ranked is a kind somebody
added, and a default of zero files every future addition below a completed workshop on the
day it lands.

**Nothing is dropped.** A routine event still prints, still counts against the cap, still
appears in the construction footnote, and is still citable. The ranking decides order and
what a truncated window keeps — never what exists.

Nine tests. No schema change, no fingerprint movement.


## A refusal that only says no is a refusal the staff will make again next turn

The recorded runs are full of it. An air strike aimed past its combat radius came back turn
after turn against *"nothing you own can reach that region"* — a message that named nothing
that could, so the proposer had every reason to read it as "try harder". A patron told
*"you are not at war with whoever holds r_south"* went looking for a war to declare on its
own ally.

`causality.Refusal` is three fields. **`requirement`** is the gate that failed, in the
engine's own vocabulary, so two refusals can be told apart without parsing prose.
**`because`** is the sentence. **`instead`** is the legal equivalent *where one exists* —
and it is empty rather than invented where it does not, because a suggestion the engine
would also refuse sends a staff round the loop a second time and costs it another turn. It
stringifies to its rendered line, so every caller that already treated a refusal as text
kept working unchanged.

Every gambit and objective refusal is now one. The ones that gained a real alternative:

* **out of range** — names the radius *and* the nearest region the target actually holds
  that is inside it. `projection` is already computed one line above for the range test, so
  the suggestion costs a dictionary lookup rather than a second walk of the map.
* **no aircraft at all** — told as that, rather than as "you strike within 0 regions", which
  is a sentence about an air force that does not exist and sends a staff looking for a
  basing problem it does not have.
* **wrong holder** — names who actually holds it, which is also a legal target.
* **a fleet that is somebody else's** — names whose.
* **push onto an ally's ground** — declare `hold`, the defend objective.
* **push into ground you already hold** — declare `hold`.
* **hold over enemy ground** — declare `push`, but **only when you are at war with the
  holder**. Against a neutral there is no suggestion, because inventing "declare war, then
  push" would be the engine talking a staff into a war it did not propose.
* **an objective over open water** — says where sea control actually comes from.

Fourteen tests. No schema change, no fingerprint movement.


## A treaty that was carried out stayed open for the rest of the game

`fulfillment_blockers` has specified this since the standing-agreement work, and rule
DIP-005 has enforced it against a model's writes — but **nothing anywhere had ever written
`status = 'fulfilled'`.** The value has been in the CHECK since schema 1 with no code path
reaching it, and the function's own docstring said so: *"what this governs is the engine's
own fulfillment derivation, which does not exist yet."*

So a treaty that ceded a province handed the province over and then went on being a live
obligation: listed in every briefing, re-read by `assess_breaches` every turn, counted
against its parties as something still owed.

`settle_fulfilled` is that derivation. **It calls `fulfillment_blockers` rather than
re-deriving the rule** — the check that refuses a model's write and the check that drives
the engine's own must be one check, or one will eventually permit what the other forbids.
Idempotent and derived, like `honour_terms`, because there is no single moment a promise
becomes kept.

Three lives, and the contrast is the point:

* **A one-off closes when it is done.** A cession is discharged when the ground has moved —
  read through `_one_off_is_done`, which is the same query `_violation` asks to decide the
  opposite.
* **A standing agreement never closes.** Every kind in `STANDING_KINDS` is skipped before
  any term is read. A defensive pact survives every settlement pass there is; it ends by
  being broken, by expiring, or by being terminated.
* **A mixed agreement stays open while any standing term remains.** A treaty that cedes a
  province *and* promises peace is not finished when the province moves.

Two guards worth naming. **An agreement with no terms is left alone** — "we know of no
obligation" is not "every obligation is met", and reading it as the second would have closed
agreements wholesale on the turn this shipped. And **`pending` is untouched**: the 1972
Ostpolitik treaties are signed and unratified, so they are not standing obligations, cannot
be breached, and must not be swept up as discharged either. They reach `active` the way
every negotiated agreement does, when the last party accepts.

Filed with the expiries in the turn report rather than with the breaches: a treaty carried
out in full is the opposite of a betrayal, and a report that put them together would teach a
player to distrust a power that did exactly what it said. Breaking a standing pact was
already an event; a test now pins that it lands in a news band above the workshops.

Eighteen tests. **No schema change** — `fulfilled` and `resolved_turn` have been in the
schema since the beginning. No fingerprint movement.


## A patron had exactly two settings: neutral, or belligerent — schema 22, additive

The commonest thing a great power does in the twentieth century, and the engine could not
express it. The Soviet Union armed Hanoi for a decade and the United States armed Saigon,
and in the database each of those was either **nothing at all or a declaration of war on the
other superpower**. PT36 showed the cost directly: both superpowers reasoned correctly that
attacking each other was unthinkable, and then had nothing to *do* about a proxy war except
watch it. What was missing was not restraint; it was a lever.

**A new table, not a new commitment kind.** The commitment kinds are a closed CHECK and
widening one is save-invalidating — schema 19 did that once, for the `sea` delivery tier, and
it cost every save. `support` is additive and costs nothing.

**Nothing in it writes `relation.at_war`, and that is the feature.** Backing a side does not
make a patron a belligerent, does not put it at war with its client's enemy, and is not read
by `defensive_pact_allies`, so it triggers no Article 5. Three tests come at that from three
directions, including one that reads the `at_war` column directly so a later change that
starts writing it fails here rather than in a playtest.

Five kinds, each with an effect no other kind has — the rule the objective kinds are held to:

| kind | what it does |
|---|---|
| `equipment` | the largest contest-pressure term; the one that is actually a war-winning quantity |
| `advisors` | a smaller one — what a training mission buys is a unit that does not come apart |
| `intelligence` | the smallest; a multiplier on competence, not a substitute for it |
| `air_defence` | added through `air_defence_over`, the engine's **single** interception term, so a client whose patron mans its SAMs is protected against a bomb and a warhead by the same state |
| `funding` | treasury moved patron→client every turn — the one kind with a price the patron sees in its own numbers |

Levels are 0–3 per patron per kind, **summed across patrons** because two patrons arming one
client is more than one, and bounded by how many powers will be seen doing it. Level 0
deletes the row rather than storing a nought, so "backed at nothing" and "not backed" cannot
disagree — the same reason a dependency's home is NULL rather than itself. Raising replaces
rather than stacks. Opening, changing and ending are INSERT, SET and DELETE **diffs**, so a
rewind past the decision undoes it; a test proves that.

### The balance numbers, flagged

`COMBAT_WEIGHT` totals **0.09 at full level across all three combat kinds** — between
favourable terrain and air superiority in the same arithmetic. Chosen so a fully-backed
client fights like a power one tier above itself and never like its patron: support decides
whether a client *holds*, never whether it wins outright. `AIR_DEFENCE_WEIGHT` is 0.06 a
level, so three levels is 0.18 — half again what the engine's own ceiling on fighters gives.
`FUNDING_PER_LEVEL` is 2.0 a turn. **If play shows a proxy beating a great power on shipments
alone, `COMBAT_WEIGHT` is the dial.** All three are marked as balance numbers in the module.

Support is public: a shipment is deniable in the newspapers and not in the field. **Secret
support would be a second mechanic — flagged, not built.**

### ⚠ Fingerprints: all three built-world digests move

`support` is in `_FINGERPRINT_TABLES`, and the digest mixes in each table's *name*, so adding
it moves every built-world digest **even though the table is empty in all three shipped
eras**. This is the schema-16 and schema-21 situation and it is harmless for the same reason:
digests are only ever compared within a schema version. It is in the list rather than left
out because a rewind that restored the front and not the shipments would replay the battle at
the wrong odds, which is exactly what that list exists to catch. **No seed digest moves.**

| era | seed | built world |
|---|---|---|
| gathering-storm | `d89e1c8d…` unmoved | `9cebbf8a…` → **`509c6fce6b0a695278b0fe6e28f71d1c`** |
| cold-war | `de872c31…` unmoved | `d6eb2f08…` → **`95403df0066ab3184fd08c0f7d3fab42`** |
| modern-day | `ceb1f190…` unmoved | `16441fc2…` → **`0371f092042f10a8075f03a56ac74d9f`** |

Schema 21 → 22, **additive, not save-invalidating**. Twenty-two tests.


## An empire was free ground with no politics in it — schema 23, additive

The unrest layer charges a holder for foreign *core*: `held_foreign_provinces` requires
`home_nation_id IS NOT NULL`, and a dependency's home is nobody — deliberately, because
there is no nation for British India and inventing one would be inventing a state. So **the
ninety regions Britain holds in 1936 and the thirty-three France holds cost their holders
exactly nothing and could never want anything.** That is the largest silent hole the
sovereignty map opened, and this closes it.

**Momentum, not a die roll per turn.** A movement accumulates: it grows from how developed
the territory is, is slowed by the garrison sitting on it, and is spent by nothing except
independence. A per-turn chance would make decolonisation a surprise, and it was not a
surprise to anybody — it was visible for decades and arrived anyway. Momentum is also
*legible*: a holder reads the number, watches it rise, and decides.

**A garrison slows and never reverses.** A mechanic where enough troops make a colony want
to *stay* gets the century backwards. Troops buy years; that is all they ever bought.

Deterministic through the pipeline: the good-year/bad-year draw uses `derive` with a
`call_key` naming the **region**, never an ordinal, so adding a colony elsewhere on the map
cannot shift this one's history.

### The transition, and the rule it needed

Independence is a set of diffs — the nation appears by INSERT, the region's three identity
columns are SET, the movement row is deleted. **All three columns land where the
sovereign/controller/core split says they should**, and each would be a different bug:
`owner_nation_id` becomes the new state; `home_nation_id` becomes the new state, which is
the column that says *this is somebody's country* and was NULL precisely because it was
nobody's; `controller_nation_id` is cleared, because nobody is occupying it.

**The rule engine caught two things and both were right.** `CORE-005` refused freeing a
holder's seat of government — now refused earlier, in a sentence rather than as a rule code.
And **`OWN-001` refused every decolonisation**: a new state holds one region, is adjacent to
nothing of its own by construction, and has no cession because nobody ceded anything.

That was the genuine design fork, and the least-invasive defensible answer is a **third
escape in `OWN-001`**: a state coming into existence on its own ground is neither conquest
nor cession, and is not a teleport either — the ground does not move, the sovereignty does.
It fires only when the same batch *creates* the new owner and names that very region as its
capital, so the only ground it can justify is the one province a new state is born on. A
nation INSERT is engine-only, so no model reaches it.

### The other ending, flagged rather than hidden

A **war of independence is deliberately not modelled** as a separate mechanic. A movement
past the threshold makes the ground restive and drags the holder's stability through the
existing unrest path, and if the holder will not let go, that drag is the whole cost.
Building a rebellion that raises its own armies would need a nation to raise them for, which
is the thing this module exists not to invent until the moment of independence.

### The balance numbers, all named

`MOMENTUM_PER_TURN` 0.9, `MOMENTUM_PER_DEVELOPMENT` 0.35, `GARRISON_RELIEF` 0.55,
`MOVEMENT_THRESHOLD` 25, `INDEPENDENCE_THRESHOLD` 70, `UNREST_PER_MOVEMENT` 1.2, variance
±25%. Set so an ungarrisoned developed colony reaches a movement in roughly two to three
years of turns and independence in six to eight, and a heavily garrisoned one takes two to
three times as long. **If play shows empires dissolving in 1938, `MOMENTUM_PER_TURN` is the
dial.** The drag is in the unrest layer's own unit, so an empire in trouble and an occupation
in trouble cost the same kind of thing.

### Fingerprints and the auditor

All three built-world digests move again, for the same reason `support` moved them: the
digest mixes in each fingerprinted table's name and `independence` is now one, **though it is
empty in all three shipped eras**. No seed digest moves.

| era | seed | built world |
|---|---|---|
| gathering-storm | `d89e1c8d…` unmoved | `509c6fce…` → **`c95cfe86e5829918df8a04bc30776a69`** |
| cold-war | `de872c31…` unmoved | `95403df0…` → **`86f63a1efc5154ee65b4fde480fc533b`** |
| modern-day | `ceb1f190…` unmoved | `0371f092…` → **`8cbde8eabe3e2e8475dbfb07071bdc97`** |

**`atlas audit` passes on all three eras unchanged** — 74 facts for 1936, 62 for 1972, 43 for
2026. It runs against a freshly built world at turn 0, where nothing has decolonised, so the
thirty `not_yet` assertions are undisturbed. A world that plays forward *will* eventually mint
some of those states, which is the feature working.

Schema 22 → 23, **additive, not save-invalidating**. Eighteen tests.


## A mechanism a model cannot reach is not shipped

The proxy-war layer went in complete: a table, a term in every contest, an air-defence
contribution, a funding transfer, briefing lines, rewind support, and twenty-two tests. And
**no model could ever open a support relationship.** `support.level` is engine-only —
correctly — and nothing in any answer schema named it, so the runtime consumed support
everywhere and nothing could create it.

Every one of those twenty-two tests called `declare_support` directly. That is exactly the
shape of test that proves a mechanism works and never asks whether anybody can get to it.
Found in pre-flight for the live Cold War run, by checking all four possible doors —
answer schema, gambit kinds, direct diff write, turn engine — and finding every one shut.
Decolonisation had the same hole in its diplomatic half: momentum accrued every turn and
`decolonise` had no caller at all.

**Two channels, both mirroring `military_objectives`.** `support` is `{client: {kind:
level}}`; `grant_independence` is a list of region ids. Both are *intent*: the nation names
who and how much, the engine checks it against the world and writes it itself. Neither can
reach `relation.at_war`, which is the whole point of the proxy layer, and neither can be
written as a diff.

Recorded **beside** `declare_objectives` and outside the batch, for the reason the
objectives are: a batch refused for some unrelated overreach must not cost a patron its
clients. `grant_independence` is an engine-checked intent rather than a gambit deliberately
— a gambit is something you attempt and may fail at, and letting a colony go is a decision
that is yours alone and cannot fail on a die roll. What it *can* do is be refused for
naming ground that is not a dependency, which it is, with a reason.

**And the briefing now offers the lever.** `support_lines` rendered what a nation already
had, which is no use to one that has none — and it is precisely the power backing nobody
that needs telling. The offer is made only where there is somebody plausible to back: a war
this power is not in, involving somebody not hostile to it. A belligerent is not told it
could send equipment instead.

Two new report fields, ranked: a colony becoming a country sits just under a government
falling, and opening a supply line sits just under a gambit — a standing arrangement is
quieter than an attempt that resolved this turn.

### The test the other forty were missing

`test_a_mechanism_a_model_cannot_reach_is_not_shipped.py` tests the **door**, not the room:
it starts from the JSON a provider returns and follows it to the world changing — an answer
carrying `support` that ends in a measurably harder fight for the client and a patron still
at war with nobody, and an answer carrying `grant_independence` that ends in a sovereign on
the map with all three identity columns right.

Plus the cheap generalisation: every engine-only table must name, in one place, either how
a model reaches it or why it is right that none can. It found four unlisted tables on its
first run. That is not proof, but it asks the question out loud at the moment somebody adds
a table, which is the only moment it is cheap to answer.

Eleven tests. **No schema change, no save-invalidation, no fingerprint movement** — 2528
passed.


## Support was a free ratchet, so it had one setting

PT37 ran twelve live turns in which **both superpowers reached the ceiling on every kind by
turn nine and never came down**. Four of the five kinds cost nothing but standing, so
maximum was always correct and no decision was ever posed. The one kind with a visible price
behaved differently — Washington dropped `funding` on turn 10, the only de-escalation in the
whole run. That was the tell.

`COMBAT_WEIGHT` is untouched: the magnitude was right, the front never moved and no client
became a great power. What was missing was a reason to say no.

**Three prices, each the one that kind actually carried.** `funding` moves treasury to the
client, as before. **`equipment` and `air_defence` are materiel** and draw treasury *and*
industry every turn — a tank sent to Hanoi is a tank not kept, and the industry term is what
stops a rich but unindustrialised power arming three clients at once. **`advisors` and
`intelligence` are the deniable ones**, so their price is being seen: standing with the
*client's enemies*, every turn, accumulating toward the point where the other side stops
treating the patron as a neutral. Charged against the enemy's regard for the patron, and
reaching nobody else — backing somebody is only a provocation to the people being shot at.

**What cannot be paid for lapses**, loudly, rather than going into debt or being quietly
discounted. That is the design goal made mechanical: a patron at its limits has to choose
which client and which kind to sustain, and if it will not choose, the shipments stop and it
is told so where it can act on it next turn. Kinds are walked in a stable
`(patron, client, kind)` order so a rewind and replay lapse the same ones.

Two more things make it a decision rather than a surprise. **An unaffordable opening level
is refused at the door**, with the price and the level that *would* work — a patron told the
number can pick one it can sustain; one told nothing ships crates it has not got and finds
out next month. And **the briefing shows the bill against the purse**, because a tradeoff a
patron cannot see is not one it can weigh. `upkeep_of` is the number quoted and the number
spent, so the two cannot drift.

Withdrawing is never refused for being unaffordable: level 0 costs nothing, and a broke
patron must always be able to stop.

### Whether the numbers bite

Against PT37's own endgame: the Soviet package (equipment 3, air defence 3, advisors 3,
intelligence 3) costs **9.6 treasury and 3 industry a turn**, the American one 4.8 and 1.5.

**Measured against the wrong number when this shipped, and the correction matters.** The
first version of this paragraph set that against the scenario's *starting* treasury -- 130
for the Soviet Union, 200 for the United States -- and concluded Moscow's maximum was about
a year's reserve. PT38 measured the real figure: what a patron actually has spare, after the
rest of its economy has taken its cut, is about **22 for Moscow on turn 1 and falling**, not
130. So the full package is roughly **four turns of reserve, not twenty-seven**, and this
cost is far more binding than the arithmetic above suggested. Read it off *disposable*
treasury; the starting figure is not money anybody gets to spend on clients.

Seventeen tests. **No schema change, no save-invalidation, no fingerprint movement** —
2545 passed.

### The verdict, for whoever tunes this next

PT38 settled it: **the cost poses a real decision, and nothing here should be retuned now.**
Twelve live turns in which Moscow opened below maximum, suspended everything twice when it
ran dry, substituted `advisors` and `intelligence` -- the kinds that cost standing rather
than money -- when broke, and said so in its own words: *"with the treasury exhausted, aid to
Hanoi is suspended deliberately rather than allowed to fail by arrears."* Washington cut
three times and lapsed once. Nine de-escalations against PT37's one.

**If a future run shows a superpower unable to sustain any package at all**, the dial is
:data:`MATERIEL_TREASURY_PER_LEVEL` (1.6) and it goes **down**, not up. On PT38's evidence it
sits close to the edge -- but on the right side of it, and `COMBAT_WEIGHT` is not the thing
to touch either way.


## `atlas release-audit` — one command, one verdict

Every gate here already existed and was already exercised: as a test, as another subcommand,
as a scratchpad script. What did not exist was **a single answer to "is this tree fit to
ship"** — so a release was green if somebody remembered to run six things and read six
outputs, which is a process that works right up until the day somebody is in a hurry.

Eight gates, cheap to expensive, **and it does not stop at the first failure**: a missing
pack and a failing invariant are different problems and finding one must not hide the other.

| gate | calls | on this tree |
|---|---|---|
| `packs-exist` | `territory.pack_paths` for every scenario | 3/3 packs |
| `packs-verify` | `map.verify.verify_pack` | **2 problems in `marine-10m`** |
| `schema-parity` | `state.parity.schema_parity_problems` | schema 23 |
| `determinism` | builds each era **twice**, compares `WorldSeed` by value and `bootstrap.fingerprint` | 3 eras |
| `invariants` | `testing.invariants.violations` at `Phase.SEED` | 13 × 3 |
| `scenario-audit` | `testing.audit.audit` + `coverage` | 74 / 62 / 43 facts |
| `cassettes` | the tape index; **only `active` tapes gate** | 2 active, 3 historical |
| `suite` | pytest and ruff as subprocesses | skippable |

**Nothing is reimplemented.** One piece was reachable only as a test, so it was *extracted*
rather than copied: `state/parity.py` now owns "what does this table promise", and
`test_migrations.py` imports it instead of defining it. A second implementation of that
comparison would have been exactly the divergence it exists to catch.

Three design points worth stating. **A gate that cannot run is a failure, not a pass** —
"we did not check" and "we checked and it was fine" must never share an exit status; a gate
that raises is caught, reported with the exception, and the remaining gates still run.
**Historical tapes do not gate**: a recording made before a change that legitimately altered
a prompt *should* miss, and an audit that reddened on it would teach everybody to ignore the
audit. **Side-effect free**: every world is in memory or under a temp directory.

Exit 0 only if every gate is green; 2 otherwise, with the failing gates named in the verdict.

Seventeen tests, and most of them are fault injection — a release audit that has never been
seen to fail is a green light nobody has tested. One fault per gate, asserting *that* gate
reddens, the verdict goes non-zero, and **no other gate reddens**, because a gate that fails
for somebody else's reason is worse than no gate at all.

**No schema change, no fingerprint movement** — 2562 passed.


## `atlas map verify` was already right, and nothing was checking

**A retraction first.** The release-audit work reported that `atlas map verify` printed
PROBLEMS and exited 0. It does not, and never did — the handler has always ended
`return 0 if report.ok else 2`. The reading came from `... | tail -12; echo "exit=$?"`,
which reports **tail's** status, not the command's. Measured without the pipe it returns 2
on `marine-10m` and 0 on `admin0-50m`, which is correct.

What was real is the *absence underneath* it: **nothing anywhere asserted that exit code.**
`9469e15` gave the bake a test when it taught the bake to verify; `verify` itself never got
one, so the behaviour was correct and unguarded — which is exactly how it came to be
misread. A command whose exit status nothing checks is a command whose exit status can
change by accident, and the next person to read it will not have a pipe to blame.

Three tests, mirroring the pair the bake already has: problems exit 2, a clean pack exits 0,
and **warnings alone exit 0**. The last is the one with teeth. Warnings are expected
simplification effects rather than defects — a border that moves within tolerance is what
simplification *does* — and a verify that failed on those would be a verify nobody could
ever get green. Only `report.problems` gates, which is the same definition
`atlas release-audit` uses.

No behaviour changed. 2565 passed, no schema change, no fingerprint movement.


## The bounding box of a scatter is not the size of anything

`atlas release-audit` failed `packs-verify` on `marine-10m`, and the diagnostic said the
excuse rule was measuring the wrong object.

**The Drake Passage is seven scraps of water strewn across eight degrees of ocean, none of
them wider than 0.064°.** Its bounding box is therefore 8.428°, so the rule called it a
large region and failed it for losing a border whose entire geometry was **fifty times
below the 0.35° tolerance that erased it**. The rule read `region.bbox`; for a multipart
region that describes the *scatter*, not the geometry.

The question is now asked of the parts that actually vanished and actually carried the
border — `_largest_vanished_part`. Two consequences, both measured before the change:

* **The slack goes with the proxy.** The old rule needed `tolerance * 2` because a bounding
  box overstates a diagonal coastline. Measuring the real part needs no allowance: Hong
  Kong's vanished part is **0.269° against the 0.35° that erased it**, comfortably inside,
  where its bbox said 0.370° and had to be forgiven.
* **It does not over-correct.** Gibraltar's dropped part is **0.124° against a 0.08°
  tolerance** and stays a problem, which is the honest answer — that one is a bake defect,
  not an expected effect.

Verified across all three packs before and after: `admin1-10m` loses no borders at all;
`admin0-50m`'s single Hong Kong warning stays a warning; `marine-10m` goes from 2 problems
and 1 warning to **1 problem and 2 warnings**. The remaining problem is real and is the
next commit's business.

Three tests, built on synthetic packs where the parts genuinely share the mainland's edge —
a scatter of sub-tolerance parts over a huge bbox is excused, a part above tolerance is not
however small its siblings are, and the warning quotes the number it judged on so a reader
can check the judgement rather than take it.

No schema change, no fingerprint movement.


## A part above the tolerance is not allowed to vanish

The remaining `packs-verify` problem, and it was a bake defect rather than a verifier one.

**The Strait of Gibraltar lost a 0.124° part to a 0.08° simplification while two smaller
parts survived**, and the border it shared with the North Atlantic went with it. A thin
sliver collapses to a line under Douglas–Peucker, shapely drops the empty polygon, and
nothing downstream can tell it from a part that was never there.

**A floor for exactly this already existed and could not reach it.** `_is_degenerate` asks
whether the *whole region* collapsed — the Monaco and Andorra case — and falls back to the
finer geometry if so. A region that keeps two of its five parts is not degenerate, so the
three that went were lost silently.

`_restore_lost_parts` is the same argument one level down: a part whose own smaller
dimension exceeds the tolerance is restored from the finer LOD — the geometry the
region-level floor already falls back to, and already in hand. A part **at or below** the
tolerance still vanishes, which is what simplification is for and what the verifier now
excuses by measuring the part rather than the region. Matching is by bounding box within one
tolerance of slack, because the simplifier's whole contract is that no vertex moves further
than that.

### What it actually moved

Measured by re-baking all three packs into a clean tree:

| pack | bytes | delta | parts restored | problems after |
|---|---|---|---|---|
| `marine-10m` | 1,025,753 | **+136** | 1 | **0** |
| `admin0-50m` | 2,269,023 | **+0** | 0 | 0 |
| `admin1-10m` | 24,456,932 | **+672** | 2 | 0 |

**Three parts across roughly five thousand regions**, +808 bytes in total. The bake reports
the count so it can never be a silent behaviour again.

**No engine fingerprint moved.** Checked rather than assumed: seed and built-world digests
for all three eras are byte-identical before and after re-baking the real packs — adjacency
is derived from LOD 0, which simplification never touches. Pack bytes changed; world state
did not.

Four tests on the helper directly: a part above tolerance is restored, one below it still
vanishes, a part that merely *moved* is recognised rather than duplicated beside itself, and
the bake actually calls it and reports the number.

`atlas release-audit` is now green. No schema change.

---

## Trade settlement was creating money

`554ffea` merged the pricing layer to master default-on, which put a defect in
`assess_trade`'s settlement on the default path. It is fixed on its own branch, measured
before and after, and merged as a revertible `--no-ff` commit.

### What was wrong

Each cargo's `bill` was computed once and then applied to the two sides **separately**.
Every nation's netted position was rounded to 2dp on its own, and a nation whose whole
netted position came to less than a cent was dropped from the batch outright. So:

* the two halves of one payment were rounded by different amounts, and stopped cancelling;
* a cargo too small to bill in cents **shipped free**;
* and because dividing a call divides the bill, a quarter of a payment could fall on the
  far side of the cent threshold that the whole payment cleared -- the length of a turn
  had become a price.

None of this is visible on one route between two countries, which is why
`test_a_cargo_has_a_price::test_money_and_goods_both_move_on_settlement` was green
throughout: it asserts one buyer's payment against one seller's receipt to 2dp, and that
assertion is *true of the defect*. All of it scales with route count, and the shipped
modern-day map carries 488 lanes a round.

### What it was costing

Six long games -- three eras, two world ids each, 36 monthly turns -- with every power
driven by a provider that proposes nothing, so what is measured is the engine's own
arithmetic. Drift read off the diff log as `new_json - old_json` on trade settlement's own
event, because an `adjust` row records the value the change *left* and not the change.
`scratchpad/settlement_drift.py`.

| era | routes | world | settlements | leaked | traded | **created** | share |
|---|---|---|---|---|---|---|---|
| modern-day | 488 | sd1 | 36 | 36 | 423.74 | **+10.66** | 2.52% |
| modern-day | 488 | sd2 | 36 | 36 | 420.33 | **+10.93** | 2.60% |
| cold-war | 368 | sd1 | 36 | 36 | 351.58 | **+5.97** | 1.70% |
| cold-war | 368 | sd2 | 36 | 36 | 351.88 | **+5.97** | 1.70% |
| gathering-storm | 187 | sd1 | 36 | 35 | 299.96 | **+1.94** | 0.99% |
| gathering-storm | 187 | sd2 | 36 | 35 | 297.41 | **+1.93** | 1.00% |
| **total** | | | **216** | **214** | **2144.88** | **+37.40** | **1.74%** |

**214 of 216 settlements leaked**, always in the same direction -- money created, never
destroyed -- and it scales with route count exactly as the diagnosis predicts: 488, 368 and
187 lanes give 2.5%, 1.7% and 1.0%. It compounds into the money supply permanently, because
nothing ever takes it back out.

### The fix

A bill is denominated in whole `SETTLEMENT_UNITS` of `2**-20` -- **one integer, computed
once, handed to the payer as its negation and the payee as itself.** Integers cancel
exactly; two independently rounded floats do not.

The grain is a power of two so the division back to a float is exact, which makes every
settled amount an exact multiple of one power of two and their sum exactly `0.0` rather
than `1e-17`. A decimal grain would be exact in the integers and inexact the moment it was
divided, which is the same class of bug one layer down. `2**-20` is about a millionth: four
orders finer than the cent the interface displays, two orders finer than the smallest bill
the engine can produce (`_shipment` floors a cargo at 0.001, `pricing.FLOOR_PRICE` at
0.027, so the smallest possible bill is 28 units).

**Nothing is repaired after the fact.** No party is credited to make the books balance and
no cargo is made free: a shipment too small to bill does not move. That branch cannot fire
at this grain and is written down anyway, because the only alternative to refusing such a
cargo is giving it away, which is the defect this replaces.

### What it moved

The same six games, same config:

| | settlements | leaked | traded | created | worst settlement |
|---|---|---|---|---|---|
| before | 216 | **214** | 2144.88 | **+37.40** | 0.40 |
| after | 216 | **0** | 2152.89 | **0.000000000** | **0.0** |

Not "within tolerance": `drift_abs_total` is `0.0`, and the worst single settlement in
216 is `0.0`. The database-level `new - old` treasury deltas cancel exactly too, not merely
the emitted amounts, because the adjustments are exact multiples of `2**-20` and the
treasuries stay on that lattice.

Turnover rose 0.37% (2144.88 to 2152.89), which is the sub-cent cargo that used to ship
free now being billed and drawing down its buyer's import budget honestly.

### What is pinned

`tests/test_a_settlement_creates_no_money.py`, on a sixteen-nation fixture of three lane
sizes -- sub-half-cent, middling, and bulk -- because the two halves of the defect live in
different size regimes and a one-tier fixture misses one of them. The fixture asserts it is
*in* the regime it claims, from the prices the engine actually settled, so a change in the
pricing layer breaks the file loudly rather than quietly making it a test of nothing.

**Five of its six tests are red on master**: the batch nets to `+0.030000`, a sub-half-cent
cargo ships free, four weekly calls settle 8% more than one 28-day call, and the same on
the default path with `refusal` and `consent` at their shipped settings. The sixth -- the
rewind -- was green before and after, and is kept because it is the invariant most easily
broken by a later change.

### The tape

The settlement change moves the fingerprinted tape, so `pt76-cold-war` was re-recorded as
`pt77-cold-war`. Characterised before spending a call: the gate is **green on master's
`trade.py` and red on the fix**, so the miss is caused by this change and nothing else, and
`scratchpad/characterize_miss.py` puts the whole prompt difference at **one line** --
`treasury 95.0` becoming `treasury 95.1`, at similarity 1.0000. A settlement value, which
is the only thing that should have moved. The briefing renders treasury to one decimal and
that nation sat on a rounding boundary, so the underlying change is far smaller than the
displayed 0.1.

No schema change. Old saves are untouched.
