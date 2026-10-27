# ATLAS — review handoff: making the depth visible

**For:** Codex, reading this against the UI proposal (#1–#8).
**Prepared:** 2026-08-13. **HEAD:** `be9c068` on `master`.
**Tests:** 2802 passing, 1 deselected (`live`), ruff clean over `src`, `tests`, `scripts`.
**Schema:** 24. **`atlas release-audit`: PASS**, 8/8 gates.

> **Fifth of five.** [`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) covers everything before the
> consolidation; [`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md) the consolidation;
> [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md) the 1972 era;
> [`REVIEW_HANDOFF_ROADMAP.md`](REVIEW_HANDOFF_ROADMAP.md) the engine batches. This one is
> organised around **your UI list** so it can be read against the proposal item by item.

> **⚠ Three things to know before anything else.**
> 1. **Your assessment was right, and understated.** Every one of these eight items was
>    surfacing something the engine already computed correctly and had never shown. Almost
>    no new engine behaviour was needed — the exceptions are named below and each is small.
> 2. **No seed fingerprint moved for any of this work.** These are views over existing
>    state. The current era digests are unchanged from before the phase began:
>    `59a71733…` / `ba0ae422…` / `bf6a46d9…`.
> 3. **Schema went 23 → 24, additively.** Migration 19 and 20 remain the only two that
>    invalidate saves. 24 is `invalidates_saves=False`.

---

## 1. The eight items

### #1 Strategy / victory screen — `d2249b4`

Rank and standing, the five weighted score components with what each contributes, the table
around you, and all three victory conditions with every clause as have/need. **17 tests.**

- **Needed one addition:** `assess_victory` answers *achieved or not*, which is right for a
  permanent log and useless for a player asking how far off they are. `victory_progress()`
  reads the *same* constants, the same `_tuning` and the same sustained-turn counts, and
  reports one `Requirement` per clause. A test asserts the two can never disagree about a
  condition being met.
- Progress is the **weakest link, not the average** — 90% of the ground share and 10% of
  the margin is not "half way".
- **Finding:** `knowledge` scores **0.000 for every nation in every era at turn 0** —
  nothing is researched at seed. Shown at zero rather than hidden.
- The sandbox disables `domination`; the card says so rather than showing an unreachable bar.

### #2 Causal event inspector — `7903c9d`

Click an event: what caused it, what it changed (grouped), where, and who wrote it. Causes
and consequences are clickable both ways. **18 tests.**

- **Map-centering made it in** — four lines, not a fast follow. Selection routes through the
  existing region-click path; the scene is in projected degrees so a label point projects
  straight to a `centerOn` coordinate.
- **Regions are derived from the diffs, not `event_participant`.** That table carries a
  region on **3 of 716** events in the PT39 world; an inspector trusting it would report
  that almost nothing happened anywhere. A region diff names its province; a *unit* diff
  resolves through the unit's province, which is what makes a battle show where it was fought.
- `inspect_event` lives beside `breakdown_of` in `engine/causality.py` — the same join with
  the opposite pivot, so one module knows how a change ties to its reason.
- Engine and model authorship are distinguished: the PT39 peace reads *"applied as engine,
  on gpt-5.6-terra's proposal"*.

### #3 Orders workbench — `4a07e24`

Parsed orders as cards before the turn: target, action, cost, prerequisites, expected
outcome, and the refusal reason with the legal alternative. **21 tests.**

- **The verdict is the engine's own call.** Gambit refusals come from
  `gambit._requirements_met` — the same function `apply_gambit` calls before it rolls; cost
  and prerequisites from the `CAPABILITIES` table. The load-bearing test sweeps every player
  undertaking × every target × three region cases (72 combinations) and requires the card's
  verdict, reason, alternative and failed requirement to equal the engine's.
- **Only `gambit` is fully pre-validated**, and `PREVALIDATED` says so on the screen. Every
  other kind is structurally checked (required fields, capability availability) and then
  says *"No verdict was available before the turn"* rather than guessing. A test asserts
  that list contains only kinds with a real predicate behind them.
- *Inert* is kept distinct from *refused*: `handled_by == "nothing"` is a real column, and
  telling a player the first was the second sends them to fix a sentence that was fine.
- **Finding:** the richest refusals in the engine are in `objectives` — `push` onto an ally's
  ground names the pact and offers `hold` instead — and **objectives are a tracked-agent-only
  channel.** No player order type produces one, so the workbench has nothing to preview.
  Making them player-orderable is a design decision, not an interface one.

### #4 Interruptible time jumps — engine `739c642`, control `3b67e73`

**22 + 15 tests.**

- **Premise correction.** A jump was *always one turn*. `jump(days=N)` opens exactly one
  turn row and advances the clock once; the loop inside it is **combat rounds**, not turns —
  a 365-day jump is one turn resolved in 365 rounds. The four spans (Week/Month/Season/Year)
  were all single turns. There was nothing to interrupt because there was no sequence.
- `advance(turns=N, watch=…)` adds the sequence and loops `jump`. **`jump` itself is
  untouched**, so nothing that ran before runs differently.
- **Eight stop conditions** in `engine/interrupt.py`, six watched by default: war,
  ally-attacked (the Article 5 signal), nuclear, capital, treaty, independence — with
  `project` and `order_refused` askable-for but off, because stopping for either means the
  advance never gets anywhere.
- **State over prose** wherever there's a choice: a nuclear use is warheads *falling* in an
  arsenal, not the word in a headline; a new sovereign state is a `nation` row appearing.
- **Determinism proof.** Halting is an observation, so an advance of ten that stops on turn
  three leaves a world byte-identical to one asked for three:
  ```
  asked for 10, ran 3, halted on turn 3 — a war began
  halted-at-3  c6660f50bae290662529d9a3224b5159
  explicit-3   c6660f50bae290662529d9a3224b5159   IDENTICAL
  ```
  Asserted both directions, and again at the UI's own entry point.
- **The spans were deliberately NOT redefined.** Making "Year" twelve turns is a 12×
  model-call cost and a gameplay decision; the control sits beside them with its cap on its
  face. A test asserts `_SPANS` is untouched.
- **Defect found:** an aborted turn doesn't move the clock, so a loop of ten opened turn one
  ten times and billed for it. An advance now stops on a turn that could not run.

### #5 Timeline branching — `1552e6e`

Fork a world at a turn; the original is only ever read. **16 tests.**

- **Fork = `export_world` + `rewind_to(T)` on the copy.** Both are existing machinery; a
  branch is exactly as sound as a saved game.
- **Lineage lives in its own `world_lineage` table, deliberately outside
  `_FINGERPRINT_TABLES`.** The first attempt put two columns on `world` — which *is*
  fingerprinted — so every fork differed from its own origin by exactly the fields saying it
  was one. The test asserting they were identical is what caught it. A fork at turn T now
  fingerprints identically to the original rewound to turn T.
- A branch is a **directory**, not a new `world_id`: that id appears in 30 of 37 tables.
- **Schema 24 is additive and non-save-invalidating** — one new table, no column touched.
- Two existing guards had to be answered rather than routed around:
  `test_every_table_is_either_diffed_or_argued_for` needed a real argument for
  `world_lineage` (lineage doesn't stop being true because a line was rewound), and the
  migration is `CREATE TABLE IF NOT EXISTS` because a fresh build makes it from `schema.sql`
  and then runs migrations over the same database.

### #6 Contextual map actions — `8c56993`

Right-click a province for the actions legal there, refused ones greyed with the reason.
**18 tests.**

- **Every line goes through `review_orders`** — the workbench's own call — so the menu and
  the workbench cannot disagree. A test asserts it for every action on every region.
- **One pipeline:** a chosen action appends an ordinary action dict to the same
  `_pending_orders` the parser fills, so it reaches the preview and the turn by the route a
  typed order takes. Free text stays the advanced path.
- Refused lines carry the reason **on the line**, not in a tooltip; enabled lines come first.
- A move is offered pointing at the clicked province with `needs="from_region"`; the next
  click supplies the source and completes the order instead of selecting.
- **Had to touch one thing:** `mousePressEvent` emitted `region_clicked` for *any* button, so
  right-click already selected as a side effect of opening nothing. Left-click behaviour is
  unchanged; right-click carries the menu and does not move the selection.
- **Deferred:** inspect-supply (no supply view exists to open), contact-owner as a live chat
  hop (offered as a `diplomatic_message` order instead), strike-origin, `build_project`
  (its capability isn't region-targeted).

### #7 Strategic alerts — `d036f85`

What the player should act on, banded gravest-first. **22 tests.**

- **Ranked by the newspaper's own `SALIENCE`**, not a second scale — four bands presented as
  *Act now / Decide this turn / Worth knowing / Routine*. A test asserts the alert bands are
  a subset of `SALIENCE`'s and that `capital_threatened == war_declared == 3`. The
  nuclear/capital grave-word bump comes along free.
- **Two kinds of signal, and the distinction is load-bearing.** Some alerts are *events* and
  rank off `salience_of`; others are *standing conditions* nothing announces — nobody writes
  "your treasury is short" every turn, and a capital beside an enemy for six turns needs
  saying on the sixth as much as the first.
- **Honest empty state:** it names the seven things it watches rather than saying "all
  clear". Scoped to the player — somebody else's war is news, a war you're in is an alert.
- **Three signals thinner than expected, flagged not faked:**
  - `front_unsupported` — implemented and querying real state, but didn't fire in 1936
    staging; it needs your ground beside an at-war neighbour *with no unit in it*, rarer
    than it sounds. Wants a live run before the threshold is trusted.
  - `arrears` — correct, but didn't fire on peacetime Britain (revenue exceeds upkeep). It
    fires once there's a genuine shortfall, proven in test by raising forty divisions.
  - `order_refused` — reads `diff_batch.status='rejected'` scoped to `proposer_nation_id`,
    so a refusal the *engine* hit on your behalf won't show. Narrower than the proposal
    implies.

### #8 Scenario workshop — substrate `d25b230`, panel `be9c068`

Clone a scenario, edit it as data, get a coherence verdict, play it. **17 + 24 tests.**

- **Scoping answer:** two of the three artefacts were already data. `Preset` has had
  save/load/list/delete and a `builtin` flag since it was written; territory files are JSON.
  Only the seed module is code, and its *contents* are primitives — 110 relations, six
  arsenals, 17 treaties.
- **The one gap was `scenario_for`**, which knew three hard-coded module ids. Authored
  scenarios are now consulted first, so a clone resolves to its edited `Scenario` everywhere
  a shipped one resolves to its module — which is why the seeder, auditor, turn engine and
  map all take it without knowing.
- **Clone gets its own identity and fingerprint**, base untouched:
  ```
  base   04fd0a0af137c00cf5a3478e55c4be73    clone  661a7d105b5745ce7f7bd38989d635ea
  ```
  **One correction to how that reads:** the `world` row is fingerprinted and it records
  which scenario built the world, so a clone digests differently *from the moment it is
  cloned*, edits or none. That difference is identity, not evidence the edits landed. The
  evidence is two audits of the same clone either side of one changed number, which is
  what the panel's test now asserts — and, separately, reading the edited value back out
  of the built world.
- **The auditor is the differentiator.** `check()` runs the same seed-phase invariants the
  release audit runs against shipped eras, *and* builds twice: "coherent and reproducible"
  vs the specific findings. A scenario that doesn't reproduce is a broken save waiting to
  happen.
- **No schema change, nothing new in a save.** The overlay is a file; the world is ordinary.
- **Editable in v1:** relations, arsenals, deterrent boats, seed — values with no referential
  tail. Powers, sovereignty and treaties are *not* offered: each can point at something
  absent, and they need a validator of their own.
- **Two defects found by using it:** a clone has no territory file, so it built at country
  granularity while its sovereignty named provinces — colliding on `region.geo_key` and
  refusing to build (territory now falls back to the base, in the loader so it holds for
  every pipeline); and an overlay naming a nonexistent base resolved to nothing, whereupon
  the seeder built a *generic* world and the check called it coherent.
- **The panel — `be9c068`, 24 tests.** It lives on the **setup screen**, not the TopBar, and
  that placement is what makes Play real: a clone is an ordinary preset, so playing one is
  selecting it in the list that was already there and pressing Create world. No window is
  rebuilt around a world that does not exist yet — the lifecycle hop branch switching still
  owes is not owed here.
- **Relations: 110 rows, no wall.** The list opens on the edited, the wars and anything held
  at ±60 or beyond; a search box finds any pair by nation name or three-letter id. Rows are
  **directed**, because the data is. Editing a pair that has since scrolled out of view is
  still an edit — harvest merges rather than replaces, and a value put back to the base's own
  stops being an edit at all.
- **The bad edit is the point.** Setting one side of a war and not the other is what a player
  will actually do, and the panel does not quietly fix it: `war-is-symmetric` — the engine's
  own seed invariant — names the pair and says what each direction claims. An out-of-range
  score is likewise left to the schema: the box accepts 900, and `CHECK (score BETWEEN -100
  AND 100)` is what refuses it. A screen with its own copy of a bound is a second place that
  bound is written down.
- **Arsenals** are warheads, a tier picked from `DELIVERY_TIERS` itself, and boats at sea —
  for powers that have an arsenal. Gathering Storm has none and says so rather than showing
  an empty section. Creating an arsenal for a power without one is a fast-follow (§3).
- **The ceiling is drawn.** `NOT_EDITABLE` is new beside `EDITABLE` — powers, sovereignty and
  treaties listed with counts and the reason each is withheld. It is the only addition to the
  substrate this panel needed.
- **The verdict gets the bottom of the panel**: "Coherent and reproducible — 148 nations, 451
  provinces" with the fingerprint, or the findings verbatim in red, and Play is disabled and
  says why until the answer is yes.
- **Screenshots:** `scratchpad/shots/workshop-{1..5}` — picked, edited, coherent, caught, and
  the coherent verdict in light. The harness re-derives the base's own digest before and
  after and prints them: `04fd0a0a…` both times.

---

## 2. Cross-cutting principles

The eight screens are more alike than the list suggests, and this is the part worth reviewing
as a whole.

**Read-only, and asserted.** Every screen asserts the world's fingerprint is unchanged after
rendering — in tests and in the screenshot harnesses. A read-only view that mutates a save
would corrupt it by being looked at, and it is the one defect a screenshot cannot show. The
two screens that *do* change the world (advance, fork) assert the change is exactly the one
the equivalent direct operation would make.

**Verdicts reuse the engine's own predicates — no second opinion.** The workbench calls
`gambit._requirements_met`; the map menu calls the workbench; the strategy screen's progress
reads the same constants `assess_victory` does; alerts rank by the newspaper's `SALIENCE`.
Every one of these has a test asserting the two cannot disagree. A screen with its own copy
of a rule is one that eventually tells the player something the turn will contradict, and
they learn to distrust all of them.

**Honest empty and limit states.** An alerts panel that said "all clear" would be claiming
more than seven signals support. `PREVALIDATED` names exactly which order kinds get a real
verdict. A disabled scenario condition says so instead of showing an unreachable bar. A
category with no live signal is *absent*, never faked.

**Screenshots as the verification method.** Invariants and fingerprints cannot tell you a
panel laid out, that numbers landed in the right columns, or that a bar filled. Rendering
offscreen and looking caught things tests did not: a widget-stacking leak on reload
(`deleteLater` defers, so three opens drew three copies), a layout that pushed *where* and
*what followed* below the fold, and a sentence reading "Written by the model, no model
involved".

**Additive, no engine behaviour change.** Excepting the four small, named additions —
`victory_progress`, `advance`, `world_lineage`, the authored-scenario hook — nothing in this
phase changes what the engine does.

---

## 3. Open fast-follows

Honestly ordered by how much is left in them.

1. **In-app branch switching** — `AtlasWindow` takes `conn` in its constructor and binds a
   `TurnEngine` to it, so switching means rebuilding the window. A half-swapped window
   holding one world's map and another's turn engine is worse than not having the button.
2. **A supply view** — needed before the map menu can offer "inspect supply" honestly.
3. **Branch tree view** — the list is flat; the lineage data supports a tree.
4. **Naming a fork** — v1 derives the name; a text field is small.
5. **Widening the workshop edit set** to powers / sovereignty / treaties — needs a
   referential validator first.
6. **Giving a power an arsenal it does not have** — `_apply` only edits arsenals the base
   already declares, so the panel offers rows only for those. A nation with no arsenal is
   still a nation that exists, so this is a small widening rather than a referential one;
   it was left out to keep the panel and the substrate saying the same thing.
7. **Making objectives player-orderable** — would give the workbench and the map menu the
   engine's richest refusals. A design decision (§#3).
8. **Watch-set toggles for the advance control** are *done*; what remains is persisting them.
   The workshop has the same shape of gap: an unaudited clone is remembered on disk, but
   which clone you had open is not.

---

## 4. Not in this phase — the other tier of your list

Everything above is **surfacing**: the data existed and the work was showing it. The rest of
your broader list is a different kind of work — new engine subsystems, with new state, new
rules and new balance questions:

- war aims, war exhaustion, peace conferences
- structured multilateral diplomacy (blocs negotiating as blocs)
- domestic politics as a system rather than two numbers
- deeper intelligence (what you know vs what is true)
- wars of independence — currently flagged in `decolonisation.py` as deliberately unmodelled
- economic crises and cycles
- strategic doctrines

Plus **state-derived map overlays** (supply, threat, unrest painted onto the map), which sits
between the tiers: the state largely exists, but the rendering is real work rather than a
panel.

These should be scoped like engine batches, not like screens. None of them is blocked by
anything in this phase.

---

## 5. Build state

| | |
|---|---|
| **HEAD** | `be9c068` |
| **Tests** | 2802 passing, 1 deselected (`live`) |
| **Ruff** | clean over `src`, `tests`, `scripts` |
| **Schema** | 24 |
| **Save-invalidating migrations** | **19 and 20 only** — still the only two, both deliberate. 21, 22, 23, 24 are additive. |
| **Seed fingerprints** | unmoved by this phase: `59a71733…` / `ba0ae422…` / `bf6a46d9…` |
| **`atlas release-audit`** | **PASS**, 8/8 gates |

Test growth through the phase, per commit: 2629 → 2647 → 2668 → 2690 → 2705 → 2721 → 2743 →
2761 → 2778 → 2802.

The TopBar now carries **six** new screens — alerts, branches, advance, workbench, strategy,
inspector — alongside rewind, export and the theme toggle. The seventh new interaction is the
contextual region menu, a right-click on the map rather than a TopBar screen; the eighth is
the scenario workshop, which opens from the setup screen because that is where a scenario
that has no world yet belongs.
