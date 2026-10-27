# SESSION CLOSE — 2026-08-08, 07:15 → 12:20 (+0400)

**16 further commits. Tests 1843 → 1939. Cumulative for the whole session: 1678 → 1939
(+261).** ruff clean, seed fingerprints unmoved (`2c90c4dc…` / `6f6e863a…`), tree clean at
`7a30e57`. Still a hardening session: no new gameplay subsystem was built, and the one
gameplay-visible change (briefings) is a bug fix rather than a feature.

---

## The headline: both of Alex's decisions are in, and the revert is confirmed on his bar

Decision 1 asked for the world-simulation pin to go back to Antigravity **and** for the
measure to change: *revert unless agy's **output** is materially worse.* Nothing measured
output — the original re-pin was made on latency and crash rate, which are provider facts.
`Accepted` (`684a039`) separates the two and never lets them blur: `answered`/`failed`
describe the provider, everything else describes what the engine did with the answer, and a
role that proposed nothing scores `None` rather than `1.0`, because scoring silence as perfect
would let a provider that says nothing outrank one doing the work.

Then the comparison itself. The five claude runs predate the counters, so their rate was
**reconstructed** from `diff_batch.proposer_role` and the per-diff `status` — a recorded fact,
not an inference — with offered counts taken off the tape, because a wholly-refused batch
writes no diff rows and counting survivors would score it perfect.

| run | provider (world_sim) | **engine-accepted (world_sim)** |
|---|---|---|
| pt34d — claude | 12 ok / 1 fail (92%) | 4/10 batches, **9/46 diffs (20%)** |
| pt34e — claude | 12 ok / 0 fail (100%) | 7/10 batches, **22/45 diffs (49%)** |
| pt34g — claude | 5 ok / 21 fail (19%) | 3/5 batches, **13/29 diffs (45%)** |
| **pt35a — agy** | **10 ok / 1 fail (91%)** | **6/8 batches, 26/40 diffs (65%)** |

**Verdict: agy's output is not materially worse — it is better than all three claude runs, on
every measure here**, at 80s/turn. The caveat runs *against* agy: claude's figures are the
record-time engine's verdicts and the engine has got stricter since, so agy faced the harsher
bar and still scored higher. tracked_agent (codex, unchanged in both) sits at 78–100%
throughout — the control showing the engine is not uniformly harsh.

**Honest limit: n=1 for agy against n=3 for claude.** The direction is consistent and the bias
conservative, so the revert is confirmed rather than merely un-refuted — but a second agy run
would firm the sample up, and that is the one piece of this Alex might still want.

The separate finding underneath it: **claude answered ~96% of calls and the engine kept
20–49% of what it said.** The measure the original re-pin was decided on was nearly blind to
the thing the bar was written against.

Decision 2 (`bf84aa7`, `44b0252`) made `fulfilled` a semantic rule rather than a treaty-kind
whitelist: standing obligations can never be discharged, one-off ones can when the map says
so, mixed treaties stay active while any standing term remains, and an unclassified clause
blocks rather than being assumed harmless. DIP-005 is a *specification* of a fulfilment
derivation nobody has written yet — a test pins that nothing in the engine writes `fulfilled`,
and it fails the day someone adds one, which is the intended order of events.

## Bugs found and fixed in this stretch — 5

| # | What | Commit |
|---|---|---|
| 10 | **`commitment.status` was model-writable**, so a nation tore up a pact by writing down that it had. Three live instances, including Germany ripping up Locarno on turn 1. Now engine-derived through the breach machinery, with consequences — or refused. | `cd47d2c` |
| 11 | **A victory was a fact in the event log, and rewind prunes the event log.** | `9e9b2c6` |
| 12 | **A rival's weapons programme was filed as a routine public work.** Technology programmes shared `CONSTRUCTION` with public works, and the briefing collapses other nations' construction into *"routine public works, not listed"*. Four instances across two runs, including *"United States completes Integrated air and missile defence"*. A programme is a `DISCOVERY` now — already in the closed enum, so no migration. | `ca01d56` |
| 13 | **The briefing spent its event window on events it then deleted.** The cap is by recency and the thinning ran after it, so the cap filled with construction the thinning then removed while the wars sat outside the window entirely. **243/243 and 219/219 briefings filled the cap and showed less than it** — 11.1 and **4.1** events of an allowed 25 — with **1314 displacements of in-run news**, including *"Germany repudiates the Rhineland demilitarisation provisions of Locarno"* falling out of Austria's briefing in a 1936 game. Both now show 25.0. | `ca983b8` |
| 14 | **The same bug at the fourth call site**, which fix 13 had left behind: the referee's window, 32 of 120 slots wasted on pt34e and 40 on pt34g. The now-dead `_thin_construction` was deleted rather than left as an invitation to re-introduce the pattern. | `7a30e57` |

## The rewind mismatch — the engine was exonerated by proof

PT35a reported `rewind to turn 8: recorded a8d4065e8833 / replayed a6be1abe9742 MISMATCH`,
against the hardest guarantee ATLAS makes. **It was the harness.** `lab_run` recorded each
turn's fingerprint the instant `jump()` returned; the negotiation it holds every fourth turn
runs *after* that and writes `diplomatic_message` events at the turn still current, into the
fingerprinted `event` table. Turn 8 was a negotiation turn and the rewind target happened to
land on one, so the baseline described the middle of the turn while the rewind restored its
end. Both digests were right about different moments.

**It was still worth fixing properly, because a false alarm here is worse than noise:** every
fourth turn a *genuine* rewind failure would have read identically and been dismissed the same
way. Fixed in `lab_run` and `lab_replay` (`f2a82ea`), and the engine half is now pinned rather
than assumed — `tests/test_rewind_across_a_negotiation.py`, 8 tests, swept across three
landing turns. A second harness-fidelity gap turned up beside it: `lab_replay` never called
`arrange()`, so a `flashpoint` replay would have started from a different world and mismatched
on every turn (`00293eb`).

## Audits that came back clean

- **`engine/causality.py`** — run over every nation and turn of a recorded run rather than
  read: 2430 breakdowns, 5772 field-turns, **0** causes with no event text behind them, **0**
  fields that moved without producing a sentence, **0** sentences whose stated span disagrees
  with the sum of its own causes. One docstring is wrong about *why* its `LEFT JOIN` is needed
  (it says diff rows survive a rewind; `diff_batch` is deleted and `diff.batch_id` cascades).
  The join is right for a different reason: `reason_event_id` is deliberately not a foreign
  key.

## Standing totals

**13 phase-aware invariants, 12 suspicion detectors.** Three invariants were added earlier in
the session (`nobody-besieges-their-own-ground`, `a-route-has-an-agreement-behind-it`,
`a-broken-agreement-names-its-breaker`), one detector (`engine-batch-refused`), and one
guard-style regression suite for rewind-across-a-negotiation in this stretch.

## Still parked for Alex — six, unchanged

Untouched by design: each is a balance or design judgement rather than a correctness question.

1. **Only `world_sim` may declare war** — explains why no new war started in 44 turns of play.
2. **Italy loses 16% of its military strength in year one of every 1936 game.** The failing
   constant is `_DEPOT_DEVELOPMENT`, not `_SUPPLY_RANGE`.
3. **One war against several signatures** costs regard that floors and legitimacy that does not.
4. **The gazetteer's 8-place cap binds in 43% of briefings.**
5. **~6% of provider calls never reach the routing log** — the diplomacy layer.
6. **Trade endowments drift the world toward shortage** over two years.

## What a next session might pick up

- A second agy world_sim run, to firm up n=1 on the revert verdict.
- **The pt34 tapes no longer replay strictly.** The `DISCOVERY` re-filing and the
  briefing-window fix both changed prompts on purpose, so the misses are correct — which is
  the whole point of strict matching. They need re-recording before they are regression
  fixtures again.

---

# MORNING SUMMARY — 2026-08-08, 03:34 → 07:15 (+0400)

**46 commits. Tests 1678 → 1843 (+165). ruff clean. Seed fingerprints unmoved
(`2c90c4dc…` / `6f6e863a…`), re-verified three times.** A hardening night: no new gameplay
subsystem was built.

---

## Bugs found and fixed — 9

| # | What | Commit |
|---|---|---|
| 1 | **`fingerprint()` was not canonical.** It read columns in `PRAGMA` order, so a carried-forward save and a fresh world with byte-identical state hashed differently. `schema.sql` asserts the opposite requirement in a comment. | `3d8a833` |
| 2 | **Espionage was 45% *once a turn*, not per attempt.** Four orders returned exactly four secrets or exactly none across twelve seeds — one success emptied a target's whole safe for free. | `28ab21c` |
| 3 | **A player paid three stakes for one roll.** Three undertakings drew the identical `gambit:…#0` key; three successes or three failures, never a mix. Same commit: the per-turn magnitude ledger was per-*order*. | `28ab21c` |
| 4 | **`honour_terms` failed silently *and* lied.** A refused batch was reported to nobody, retried forever, and left *"India begins shipping rare to Germany"* in the history with no route under it. | `422273f` |
| 5 | **A breach could be charged for a war that predated the agreement — and it was farmable.** Three pacts signed during a war took −105 regard and −18 legitimacy off the aggressor for one declaration. | `b24dbfe` |
| 6 | **Two more engine penalties its own rules refuse** (arrears, occupation drag). ECON-003 names no diff, so it condemns the *whole batch* — and one bankrupt minor meant **not one treasury in the world moved**. | `3064529` |
| 7 | **A year of sanctions was harsher than the twelve months it replaced.** Linear span scaling gave a *negative* multiplier: one yearly turn emptied a sanctioned treasury outright. | `050c162` |
| 8 | **A siege of ground you are standing on.** A contest whose attacker already holds the region can never resolve; Russia's briefing read *"you hold it, you are 55% of the way to taking it"* for nine turns. | `5d54fa8` |
| 9 | **The sandbox root was a sandbox**, and the ATLAS source tree was forbidden by *spelling* rather than identity. Both second-layer failures. | `7bffc49` |

Two instrument fixes on top: one supply pocket is one report line (`c66f77d`), and
`lab_replay.py` silently succeeding for a preset it never attempted (`199f373`).

## Surfaces attacked and found sound

Diff apply / inverse-diff byte-exactness over every op and key shape; severance × the
`trg_focus_cap` trigger; severance over insert/delete; the INFO boundary and
`event_participant` inheritance; visibility inheritance on **every** commitment path (6 kinds
× 3 scopes); RNG first-draw bias; the label round-trip over **1421 names in both eras**; the
parallel snapshot's semantic claim; the breach and nuclear paths end-to-end through `jump()`.

## Invariants: 10 → 13, and every one now has a demonstration

`nobody-besieges-their-own-ground`, `a-route-has-an-agreement-behind-it`,
`a-broken-agreement-names-its-breaker`. Two of the ten pre-existing ones **had never been
shown to fire**. A closure test now fails when an invariant arrives without a demonstration,
and each has a no-crosstalk case. A **twelfth detector** (`engine-batch-refused`) covers the
class §6 kept finding one site at a time. `atlas check` (`08b3ed7`) makes all of it runnable
against a real save — it could previously only be pointed at a laboratory.

## Playtests — 6 recorded runs, ~3h of live models

| run | what | outcome |
|---|---|---|
| **PT34a** | Modern Day, 16 monthly turns | **replays 16/16.** Closed handoff §5c — the first tape that replays. UN session, coup, sabotage, partisans |
| **PT34c** | sandbox, `concord` | **A real model accepted** a trade agreement — first ever. And proved the sandbox *cannot* open a route (self-sufficient by construction) |
| **PT34d** | Modern Day, `concord` (deu↔ind) | **`open_trade` opened a route. §5b closed.** Also found the nine-turn Donets'k seam |
| **PT34e** | Modern Day, `flashpoint` (nuclear ind↔pak) | No war. India tore up its pact for free |
| **PT34f** | Modern Day, **8 × 90 days** | **replays 8/8.** First non-monthly run ever; pacing arithmetic confirmed live |
| **PT34g** | **1936, first ever recorded** | Partial — killed by quota at turn 6. **replays 12/12.** Germany tore up Locarno on turn 1 |

Three tapes replay perfectly and are permanent regression fixtures. **Balance measured, not
guessed:** coup rates, deterrence (saturates at 0.18 — the tenth warhead buys nothing, a
triad buys everything), sanction severity, supply coverage on both maps, trade endowments
over two years, the gazetteer's prompt cost, and what the routing log does not count (6%).

---

## PARKED FOR ALEX — decisions with evidence

### 1. ~~`commitment.status` is writable by models~~ — **RESOLVED, Alex approved, commit `cd47d2c`**
**Three independent live instances in three consecutive runs.** Germany tore up **Locarno**
on turn 1 of 1936 (`pending → broken`); India tore up its non-aggression pact with Pakistan
on turn 1 (`active → broken`); a model wrote a standing supply agreement to `fulfilled` after
one month and silently cut its own trade route. In every case `breach()` was never involved —
no charge, no grievance, no record of who did it. Decision 120 made `relation.treaty_status`
engine-only for exactly this reason and schema 18 stopped one signatory *activating* a pending
agreement; the **breaking** direction was never closed. *Detected now by
`a-broken-agreement-names-its-breaker`.*

> **Closed.** `commitment.status` and `commitment.resolved_turn` are ENGINE-only as of
> `cd47d2c`. Every legitimate writer was already the engine — `accept_commitment` activates,
> `expire_commitments` retires, `breach` breaks — so the change removed exactly one route:
> the fourth, with no consequences attached. **The suite went green on the permission change
> alone**, which is the evidence that nothing legitimate depended on it. A model now breaks
> an agreement by breaking the promise, and `assess_breaches` charges it. Twenty-two tests,
> including the two that matter: the invariant is unreachable by a model, and the engine's
> route to `broken` writes the attribution in the same batch as the status.

### 2. The world-sim re-pin was decided on four calls and is overturned by 391
Measured over the night's runs rather than the one prompt the original decision rested on.
See `22d1163` for the numbers.

### 3. Only `world_sim` can declare a war
Disclosed to tracked agents, and it explains why **no new war started in 44 turns** of
Modern Day. India climbed its whole ladder — sting, three exposes, an ultimatum, a refusal —
and stopped, because there was no next rung. The sole author of wars is the one actor with no
stake in one. *Nothing is broken; the engine does what it says.*

### 4. Italy loses 16% of its military strength in the first peaceful year of every 1936 game
Eight formations in Eritrea, out of supply from turn 1, confirmed live in PT34g. The failing
constant is `_DEPOT_DEVELOPMENT` (an absolute bar on a quantity divided among however many
provinces a country is drawn with), **not** `_SUPPLY_RANGE`. Three readings in the log.

### 5. Smaller, each with numbers
One war against several signatures costs regard that floors and legitimacy that does not
(−24 for four agreements); the gazetteer's 8-place cap **binds in 43%** of briefings for ~1%
more prompt; ~**6%** of provider calls never reach the routing log, systematically, and it is
the diplomacy layer; trade endowments drift the whole world toward shortage over two years.

---

*Full detail for every item is in the dated sections below, and in `ENGINE_CHANGELOG.md`.*

---

# Overnight hardening log — 2026-08-08 → 2026-08-09

A verification night, not a feature night. Every entry below is one item: what was
attacked, what was found, what changed, and the commit.

Starting point: `b657337`, schema 18, **1678 tests passing**, ruff clean, seed
fingerprints `2c90c4dc…` (1936) / `6f6e863a…` (2026).

> Items marked **PARKED** are judgement calls left for Alex, with the evidence gathered
> so the decision can be made from data rather than from taste.

---

## 1. Deep-bug audit of the core surfaces

### 1a / 1c. Diff apply, inverse-diff, and severance with a database-level consequence

**Attacked:** whether undoing an insert, an update and a delete each restore the world
*byte for byte* — including JSON columns, NULLs, float representation and multi-column
primary keys — and whether a partially severed batch still rewinds cleanly when severance
makes the remainder illegal to SQLite rather than to a rule.

**Method.** A full dump of every table in the database rendered through `repr`, which is
strictly stronger than `fingerprint()` on two counts: it covers tables the digest was
never told about, and it distinguishes `7` from `7.0`. A world was then mutated every way
a diff can — SET, ADJUST, INSERT, DELETE, over single and composite keys, over floats,
ints, text, JSON text and NULLs; a field written three times in one turn; a row deleted
and re-created under the same id in one turn; a row created on turn 2 and changed on turn
3; and an espionage-shaped `event_visibility` grant pointing *backwards* at a turn-1 event
— then rewound and compared.

**Found: no defect.** All of it inverts byte-exactly. Two facts worth having on the
record because they were assumed rather than known:

* SQLite's REAL affinity turns a diffed `7` into `7.0` on write, and the `json.dumps` /
  `json.loads` round trip through `diff.old_json` carries that through without turning it
  back into an int. The digest would not have noticed either way; the byte dump does.
* The backwards-pointing visibility grant is undone by its inverse diff, not by the event
  cascade — the event it hangs off survives the rewind, which is exactly why the table had
  to be diffable.

**The severance interaction (phase-2 handoff §6.2) holds, and it holds by a route worth
naming.** A reshuffle whose demotion is condemned by CORE-004 — the one rule that may
sever past `SEVER_MINORITY` — is re-applied without it, the promotion then meets a roster
still holding five, and `trg_focus_cap` aborts the statement. `_sever_and_stand` catches
it, rolls back to the savepoint and returns `None`, so the **whole** reshuffle is refused:
the roster does not gain a sixth power, the fingerprint does not move, the savepoint is
released, and the next batch of the game applies normally. That is the right answer — a
roster that gained a seat without losing one is not the reshuffle that was computed.

**Also checked and found sound, so it is not a gap:**

* Every `ORDER BY` in `_FINGERPRINT_TABLES` is over a unique key, so the digest is
  row-order independent for all 23 tables.
* No `DELETE` diff in the engine addresses a table with a diffable `ON DELETE CASCADE`
  child. The five tables the engine deletes (`unit`, `contest`, `construction`,
  `military_objective`, `trade_route`) have no diffable children, so no cascade can
  silently destroy state a rewind then fails to restore.

**Changed:** `tests/test_diff_inversion.py`, six tests. No engine change.
**Commit:** `cecacca`. 1684 tests, ruff clean.

### 1b. Fingerprint canonicality — **one real defect, fixed**

**Attacked:** whether two identical worlds can hash differently, and whether two different
worlds can hash the same.

**Found — the digest depended on how the database reached its schema, not on what is in
it.** `fingerprint()` selected columns in `PRAGMA table_info` order, which is the order the
table was *built*. A column added by `ALTER TABLE … ADD COLUMN` lands at the end of a
carried-forward database, and `schema.sql` declares three of them in the middle:

| table | column | fresh position | carried-forward position |
|---|---|---|---|
| `nation` | `industry` | 13 of 21 | last |
| `region` | `home_nation_id` | 8 of 11 | last |
| `construction` | `project_kind` | 5 of 9 | last |

All three are fingerprinted. So an existing save and a world built fresh from the same seed,
holding byte-identical state, produced **different digests**. `schema.sql` states the
requirement itself, next to `unit_template.build_turns`: *a fresh world has to match a
migrated one column for column*. It was not met, and nothing noticed because
`test_migrations` compares schemas as an unordered **set** of `(table, column)`.

Not a live bug today — the engine only ever compares a world against itself — but
`scratchpad/lab_replay.py` compares digests **across databases**, replaying a recorded run
into a world it builds itself. That works only while both sides are freshly created. The
first time a carried-forward save is compared, it reports a regression that is not one.

**Fixed** by sorting the column list in `fingerprint()`: one line, and it makes the property
structural rather than a coincidence of where the columns happen to be declared.

**Attacked and held:** row insertion order (every `_FINGERPRINT_TABLES` entry orders by a
key that, with the `world_id` its scope fixes, covers the table's primary key — now a
structural test); separator injection, since `repr` escapes both `\x1f` and `\x1e`; and
type confusion, since `None`, `"None"`, `"7"`, `7` and `7.0` are five distinct digests.

**Consequence to know about:** every `fingerprint()` value moves, including the two recorded
sidecars in `scratchpad/cassettes/*.fingerprints.json`. Those tapes were already historical
— phase-2 handoff §5c: `403943b` changed model-facing prompts, so both miss on replay
regardless — and tonight's runs record fresh ones. **Seed fingerprints are unaffected and
verified unmoved**: `scratchpad/seed_fingerprint.py` hashes the `WorldSeed` dataclass, not a
database. `2c90c4dc…` / `6f6e863a…`, every section hash unmoved.

**Changed:** `src/atlas/state/bootstrap.py` (the sort),
`tests/test_fingerprint_canonical.py`, eight tests.
**Commit:** `3d8a833`. 1692 tests, ruff clean, seed fingerprints verified unmoved.

### 1d. Seeded-RNG determinism and `call_key` uniqueness — **two real defects, fixed**

**Attacked:** whether two decisions in one turn can collide on a `call_key`, and whether
any roll is order-dependent such that adding a call shifts a later one.

**Method.** Enumerated all nine `derive` sites and all three `uncertain` sites; instrumented
`derive` across every module that imported it and ran full jumps at 30 and 365 days,
recording every `(turn, call_key)`; then attacked the sites the enumeration made suspicious.

**Found — a repeated player order was one decision reported twice.** Three instances of one
mistake: the order channel treated each order as if it were the only one of the turn.

1. **Espionage was 45% *once a turn*, not 45% *an attempt*.** `espionage:{actor}:{target}`
   is the same string every time, so a second order against the same rival inherited the
   first's answer. Reproduced over twelve seeds: four orders returned exactly four secrets
   or exactly none — never one, two or three. A network that got in once emptied the safe
   for free. **Fixed** by naming the report being gone after in the key, which is what the
   decision actually is. A single order's odds are untouched; only which seeds land where
   has moved. *An attempt ordinal was considered and rejected: espionage costs nothing, so
   independent repeat rolls would make spamming the order the dominant way to play it.*
2. **A player paid three stakes for one roll.** `orders._gambit` passed no `index`, so
   three sabotage orders drew `gambit:sabotage:a->k#0` three times — three successes or
   three failures, never a mix, each charged 6.0. **Fixed:** the dispatch loop counts
   identical earlier orders, over the order's *own identity* rather than its position, so
   an unrelated order typed first cannot move anybody's roll.
3. **The per-turn magnitude ceiling was per-order.** The same path handed each undertaking
   a fresh `ledger`, so three of them got three whole ceilings. **Fixed:** one ledger per
   call to `apply_order_effects`, which is what the tracked-agent path already did.

**Attacked and found sound:** `#r{round}` is in a contest's stream key, so a yearly turn is
not one month resolved twelve times (the code says so and it is true); the coup roll is
drawn once per nation per turn however long the turn is; air battles resolve once per turn,
outside the round loop; and no `(turn, call_key)` repeats in an instrumented jump.

**PARKED for Alex — the `#{index}` on the tracked-agent keys.** `gambit:…#{index}`,
`nuclear:…#{index}` and `strike:…#{index}` take `index` from `enumerate(candidates)` in
`_run_tracked_agents` — it is *that power's position in the focus roster*. It is redundant
(the actor is already in the key, and a nation proposes one undertaking a turn) and it
couples one power's roll to whether another power was promoted. Harmless today, because a
rewind restores the roster exactly and a replay therefore reproduces — but it is precisely
the ordinal coupling `atlas.sim.rng`'s docstring exists to forbid. Removing it moves every
future gambit roll in every world, which is a deliberate change rather than a 3am one.

**Also noticed, not chased:** `sim.resolve.morale_shift` has no callers anywhere.

**Changed:** `src/atlas/engine/orders.py`, `tests/test_repeated_orders.py`, eight tests.
**Commit:** `28ab21c`. 1700 tests, ruff clean.

### 1e. The visibility / INFO boundary — **no defect; the participant question answered**

**Attacked:** whether a model proposal can leak or widen visibility beyond its cause, and
whether `event_participant` rows inherit their event's secrecy.

**The participant question, answered properly.** A participant row has **no visibility
column** — it says who an event happened to and nothing about who may know. So the property
lives entirely in *where the table is read*. Enumerated every reader:

| reader | model-facing? | why it is safe |
|---|---|---|
| `views._places_named_above` | **yes** | handed the events the briefing is *already showing*, so it can only read participants of events the viewer can see |
| `treaties._aggressor`, `treaties`' breach query | no | engine, omniscient by design |
| `resolution._has_casus_belli` | no | engine |
| `organisation.demand_ceasefire` | no | engine |

Only one model-facing reader, and it inherits by construction. Pinned behaviourally in both
directions: a secret operation's target region does **not** appear in an outsider's
gazetteer, and *does* appear in the author's — the second half matters, because without it
the first would pass for the wrong reason.

**INFO-002 now has all seven visibility combinations under test**, not the two that happened
to be exercised. Two sharper attacks also pinned: a tracked agent writing itself an
`event_visibility` row (refused by CORE-003 — row creation is engine-only for every table,
which is the general rule covering this very specific case), and a nation citing somebody
else's secret as the cause of a change (INFO-001).

**Checked and found closed, so they are not gaps:**

* `record_event` will attach a `cause` edge to any event id that exists, with no visibility
  check — INFO-001 covers a diff's citation, not an event's. It is not a leak: `event_cause`
  has exactly two readers, both in `organisation.py`, and both require the citing event to
  be `origin = 'ENGINE'` with an *organisation* as its actor. A model-authored event is
  neither, so a tracked agent cannot forge a cause link to suppress a Council trigger or to
  fake having been defied.
* `known_events` filters on public / own-actor / explicit grant, and `standing_commitments`
  joins through the viewer's own party row.

**Changed:** `tests/test_visibility_boundary.py`, thirteen tests. No engine change.
**Commit:** `95fd4c1`. 1713 tests, ruff clean.

---

## 4 (taken early). The rest of the capability manifest, compared against behaviour

The phase-2 review found `fortify` and `disband_units` describing things they do not do,
and those two were fixed at schema 18. **The other capabilities that carry a `cost` line,
or claim to read a field, had never been compared against their handler either** — the
existing tests check the catalogue agrees with *itself*, which cannot notice a description
that is simply untrue.

Seven more claims checked. **All seven hold**, and now say so in a test:

| claim | verdict |
|---|---|
| `raise_units`: *treasury and manpower per formation* | holds, and *per formation* is exact — three cost three times one |
| `raise_units`: *anything with a build time enters the yards* | holds — two templates differing only in `build_days` split correctly |
| `economic_investment`: *treasury, and the economy moves* | holds |
| `gambit`: *a stake from the treasury, charged win or lose* | holds — six attempts, every one paid for |
| `move_units`: *no direct cost* | holds — neither treasury nor manpower moves |
| availability predicates gate the *handler*, not only the menu | holds for all three unit orders |
| a region target advertised is a region target refused when wrong | already held |

**One thing found while writing them, kept as a test rather than a fix:** a sea kind
ordered by a power whose home region has no coast is refused *by name and with the reason*
— `a battleship needs a coast; r_south has none` — rather than falling through to a land
formation. That is the right behaviour and it was undefended.

**On the availability predicates** (handoff §"least confident"): they are right in the
sense that matters — the menu and the handler agree. A power with no units is not offered
`move_units`, `disband_units` or `fortify`, all three are refused if ordered anyway, and one
formation unlocks all three. They remain *weakly discriminating* by design: `_has_neighbours`
is false only in a one-nation world, which is what its docstring says.

**Changed:** `tests/test_capabilities.py`, seven tests.
**Commit:** `993bd82`. 1720 tests, ruff clean.

### 5 (first loop). Every invariant, shown to actually fire

**Attacked:** the instrument, on the phase-2 handoff's own terms — *"whether the invariant
checker returning zero on its first live outing is good news. Either the engine is sound in
those ten respects or the checks are weaker than they look."*

**Found — two of the ten had never been shown to fire on anything.**
`a-term-belongs-to-an-agreement` and `a-route-has-two-living-ends` had no test at all: they
were four lines of SQL somebody believed in. Both, it turns out, work.

Replaced ten-tests-by-hand with a **table plus a closure test**: every entry in `INVARIANTS`
must have a way to break it, so adding an invariant without a demonstration now fails the
suite. Same argument the capability manifest's closure test makes, pointed at the
instrument instead of at the engine.

And the half PT31 says matters more, which nothing had: **breaking one thing must not report
a violation of something else.** Each wound is checked against a named list of honest
consequences — killing a country to strand a trade route also leaves it holding ground, so
that pair is allowed and everything else is not. Nothing unexplained fired.

So the answer to the handoff's question is: the ten checks are real, they were returning
zero because the engine was sound in those respects, and two of them were untested rather
than weak.

**Changed:** `tests/test_invariants.py`, 21 further tests (10 fire + 10 no-crosstalk + the
closure).
**Commit:** `d1b9256`. 1741 tests, ruff clean.

### 4b. `honour_terms` failed silently — **real defect, fixed, and it had a second half**

Phase-2 handoff §5e, one line: *`honour_terms` returns silently when its batch is refused;
nothing is reported.* True, and the same shape as the breach defect Group 2 fixed — with the
same second half nobody had looked for.

* **Silent.** It returned `[]` on refusal, which is the identical answer it gives when there
  was nothing to do. Because the function is idempotent and re-derives the world's shape
  every turn, the failure was re-attempted and swallowed again on every later turn — an
  invisible loop that could run for the rest of the game. Now returns a `TermReport`, whose
  problems `turn_engine` extends into the turn's rejections. Retrying is kept deliberately;
  retrying *silently* was the defect.
* **And it left the story behind.** The event is written before the batch because every diff
  must cite one, so a refusal left *"Karthos begins shipping oil to Aurelia under their
  agreement"* in the event log with no route under it — quoted back by every later briefing
  and carried forward by the digest, so both staffs plan around a route that does not exist.
  `_unrecord` was already in this module, written for exactly this on the breach path.

**Changed:** `src/atlas/engine/treaties.py`, `src/atlas/engine/turn_engine.py`,
`tests/test_treaty_terms.py` (three tests + call-site updates).
**Commit:** `422273f`. 1744 tests, ruff clean.

### 4c. Breach attribution when a war predates the agreement — **real defect, farmable, fixed**

**Attacked:** the handoff's *"least confident"* line — *the breach detector's attribution
when a war has multiple declarations; I take the most recent, which I believe is right and
have not proved."*

**The multiple-declarations worry is unfounded**, and now known rather than believed:
`_declare_war` returns without writing anything if the pair is already at war, so there is
at most one `war_declared` per pair per war. Successive wars across a game are ordered by
id and the most recent is the one running. Correct.

**But the same query had a worse hole next to it.** `_aggressor` never asked *when* the
agreement was made, so a pact signed **during** a war was contradicted the moment it became
active and charged to whoever declared that war turns earlier.

**And it is farmable.** Reproduced: Karthos declares war on turn 1; Aurelia signs a pact on
turns 3, 4 and 5; each is instantly "broken" and each costs Karthos −35 regard and −6
legitimacy. Three signatures floor the regard at −100 and take 18 legitimacy, for one
declaration. A power under invasion can grind its enemy's standing down by offering
agreements.

**Fixed:** the floor is the signature, and it is `>=` — declaring war on the day you signed
is a betrayal and the sharpest one there is. Going to war *after* signing is unchanged,
which is the half that had to survive.

An agreement between powers already at war is now left standing and **reported every turn it
recurs**, with a message that distinguishes the two cases that used to read alike (a war with
no author; an agreement signed between belligerents). The check was widened past `BREACHABLE`
because a `trade_agreement` with an explicit `open_trade` term is breachable through its
terms and is not in that set — so the case was silent for exactly the agreements a
negotiation makes.

*Both seeded eras re-checked: nine active agreements in Modern Day, six in 1936, no breaches,
no new problem lines, invariant checker still silent at SEED.*

**PARKED for Alex:** whether the engine should *refuse to activate* an agreement whose terms
the world already contradicts, rather than accepting it and reporting the oddity. That is a
rule about what a negotiation may produce and it would change what a model is allowed to
settle.

**Changed:** `src/atlas/engine/treaties.py`, `tests/test_treaty_terms.py`, four tests.
**Commit:** `b24dbfe`. 1748 tests, ruff clean.

### 5 (second loop). The isolation policy — **the eleventh case, and it was two**

**Attacked:** handoff §6.3 — *`config/paths.py::_contains`. Ten cases pinned; look for an
eleventh.*

**Found two, both second-layer failures** (the allowlist was doing the real work throughout,
which is the argument for having two layers):

1. **The sandbox root itself passed both guards.** `_contains` accepts identity, so
   `assert_isolated(runs_root())` and `assert_grantable(runs_root())` returned it. A call
   working in the root can read *every other sandbox in it* — another nation's briefing,
   another role's prompt, another turn's answer — against the central architectural claim
   that models never see each other's output. The grant version is worse: `agy` ignores
   `cwd`, so `--add-dir` is its only containment and one argument would hand it the whole
   run. Unreachable today (`SandboxDir.create` always makes a child) — but a guard that
   holds only because nobody has called it wrong is not a guard.
2. **ATLAS's own tree was forbidden by spelling.** `_default_forbidden()` named it by the
   absolute path it happens to sit at, so moving the checkout — or cloning it to review a
   branch — would silently stop the source tree being a forbidden root. `project_root()`,
   which computes it correctly, was exported and used by **nothing**. Same mistake the
   module refuses for the MSIX redirect, pointing the other way.

**Changed:** `src/atlas/config/paths.py`, `tests/test_paths.py`, five tests.

Also taught `lab_replay.py` the run tag, and to read the span and disposition profile out of
the tape's sidecar — a replay that assumed a monthly turn would ask a different question of a
run recorded at a season, and the miss would read as a changed prompt rather than as the
harness guessing.
**Commit:** `7bffc49`. 1753 tests, ruff clean.

### 5 (third loop). Can a real name be read as a label?

**Attacked:** handoff §6.4 — *`_LABELLED` reads an id out of brackets and returns it before
any name matching. Is there a real region name that ends in a bracketed token? I checked all
917 regions round-trip, which is not the same as checking no name is misread.*

**Swept exhaustively.** Every entry of every vocabulary in both eras — regions, nations,
projects, technologies, unit kinds; **1421 entries** — run through the regex directly.
**Not one matches the label shape at all**, and every label round-trips to the id it names.
The concern is answered: no real name is misread.

**One improvement while there.** A place genuinely called `Sector [r_east]` is
indistinguishable, as written, from a label for the Veyran Plateau. The resolver already
refused it — correctly — but named only the id in its ambiguity, not the region actually
called that. It now names both readings. Unreachable in either era today, which is the
reason to get the message right before it is not.

**Changed:** `src/atlas/engine/reference.py` (the ambiguity now sees both readings),
`tests/test_reference.py`, four tests.
**Commit:** `4d3d3df`. 1757 tests, ruff clean.

### 5 (fourth loop). The parallel snapshot's *semantic* claim

**Attacked:** handoff §6.6 — *`turn_engine::_run_tracked_agents`. One snapshot for all agents
is a **semantic** change: agents no longer see each other's moves within a turn.*

The three existing tests pin the *mechanics* (the calls overlap; every briefing is built
before any call; the apply order is nation id). Nothing checked the *claim*, which is the
one a future refactor would break without touching any of them.

**Now pinned from outside the engine.** Aurelia announces something unmistakable; no other
tracked power's briefing carries it that turn, and every one of them carries it the next —
the second half being what stops the test passing because the marker never landed. No
defect: the claim holds.

**Changed:** `tests/test_focus_parallel.py`, one test.

### PARKED (upgraded with the mechanism) — the `#{index}` roster coupling

Traced to the exact source, which makes this an easy call rather than a vague worry.
`_run_tracked_agents` enumerates `candidates`, which is a turn-rotation over
`tracked_nations` — and `tracked_nations` is **`ORDER BY name`**. So the `index` in
`gambit:…#{index}`, `nuclear:…#{index}` and `strike:…#{index}` is a power's position in an
alphabetically-sorted roster, offset by the turn. A power's roll therefore depends on

* which other powers are tracked,
* how many there are,
* and **their names** — renaming a country moves another country's gambit outcome.

It is redundant: the actor is already in the key and a nation proposes one undertaking a
turn, so removing `#{index}` cannot create a collision. It is also harmless today, because a
rewind restores the roster exactly and a replay reproduces.

**Not changed tonight, deliberately.** Removing it moves every future roll, which would
invalidate the fingerprint sidecars of the tapes being recorded tonight — and recording
replayable tapes is the other half of this night's work. It is a one-line change to make
with a clean tape shelf.

---

## 3. Balance calibration, from measurement rather than taste

### 3a. Coup rates — **measured; the numbers are defensible, and the pacing arithmetic is exact**

`coup_risk`'s constants were chosen and never measured. Now they are. **Per-year chance a
government falls**, at the stability/legitimacy pairs a played world visits:

| stability \ legitimacy | 5 | 15 | 25 | 35 | 50 | 70 |
|---|---|---|---|---|---|---|
| **5** | 0.167 | 0.126 | 0.095 | 0.074 | 0.062 | 0.062 |
| **15** | 0.144 | 0.103 | 0.072 | 0.051 | 0.039 | 0.039 |
| **25** | 0.127 | 0.085 | 0.054 | 0.033 | 0.022 | 0.022 |
| **35** | 0.115 | 0.074 | 0.043 | 0.022 | 0.010 | 0.010 |
| **50** | 0.109 | 0.067 | 0.036 | 0.015 | 0.004 | 0.004 |
| **70** | 0.109 | 0.067 | 0.036 | 0.015 | 0.004 | 0.004 |

Three things worth having on the record:

* **A healthy country is essentially safe** (0.4% a year at 50/50) and a collapsing one
  faces about one chance in six a year. That is a mounting risk a player can answer, which
  is what the design note asked for.
* **`_MAX_RISK = 0.30` never binds for a first government** — the worst cell is 0.167. It
  binds only through the coup trap: 0.167 × 1.9 = 0.317, capped. So the cap is doing exactly
  the one job it was written for, and nothing else.
* **Legitimacy matters more than stability** — the range across a legitimacy row (0.109 →
  0.004) is wider than down a stability column (0.167 → 0.109). Worth knowing; probably
  right, since the mechanic is about a government rather than a state.

**And the pacing arithmetic is exact at a span other than a month, which no run had ever
tested.** Over three years of elapsed time, at stability 5 / legitimacy 5, with 1200
government-lives per span:

| span | first coup within 3 years | theory |
|---|---|---|
| 30-day | 0.397 ± 0.028 | 0.423 |
| 90-day | 0.416 ± 0.028 | 0.423 |
| 365-day | 0.436 ± 0.028 | 0.423 |

All three inside the interval, monthly and yearly 1.9 standard errors apart — sampling, not
a scale-down bug. Chased anyway, because the direction was monotone in every row, and the
chase found something worth keeping:

**The first draw of a freshly derived stream is unbiased**, checked directly. Nearly every
call site in the engine derives a stream and takes exactly *one* number from it, so a biased
first output would bias every one-draw decision in the game — and nothing would ever show
it, because each stream is used once and there is no sequence to look wrong. It is now a
test rather than an assumption.

**Changed:** `tests/test_rng.py`, one test. No engine change.

### 3b. Supply on a finer map — **the worry was pointed at the wrong constant, and there is a real balance finding behind it**

**Attacked:** handoff *"least confident"* — *whether the supply range of 3 holds up on a map
with finer provinces. Swept on the current maps only.*

**Measured on both eras.** Own-country coverage at range 3:

| era | owned land regions in supply at home |
|---|---|
| modern-day | **316 / 316 (100%)** |
| gathering-storm | **352 / 375 (93.9%)** |

The 23 that are not fed are almost all Italy's: **Italy feeds 11 of its 30**, France 8 of 12.

**The range is not the problem.** Sensitivity sweep, 1936:

| setting | Italy | France | whole era |
|---|---|---|---|
| range 3, depot 0.8 *(now)* | 11/30 | 8/12 | 352/375 |
| range 4, depot 0.8 | 11/30 | 8/12 | 352/375 |
| range 5, depot 0.8 | 11/30 | 9/12 | 353/375 |
| range 3, depot **0.5** | 11/30 | 8/12 | 352/375 |
| range 3, depot **0.3** | **28/30** | **10/12** | **371/375** |

**The constant that does not survive a finer map is `_DEPOT_DEVELOPMENT`, not
`_SUPPLY_RANGE`** — an *absolute* development bar applied to a quantity that is divided
among however many provinces a country happens to be drawn with. Italy has nine provinces
over 0.8 and twenty-one under. Modern Day never showed it because its provincialised powers
(China, Russia, India) are large economies whose every province clears the bar easily.

**And the note on `_SUPPLY_RANGE` was untrue.** It claims *"3 is the only value that feeds
every power's own country"*, with a sweep table — and that sweep was **Modern Day only**.
Corrected in place with the measurement, since a justification that is false is worse than
none.

**PARKED for Alex — a real, quantified balance consequence.** Eight Italian formations sit
in Eritrea, out of supply on **turn 1 of every 1936 game, at peace**, and grind at 8% a
month. Measured: **Italy loses 16% of its total military strength over the first peaceful
year.** World-wide it is those eight formations out of 442, so this is Italy alone and it is
every game. Three readings, and the choice is a scenario decision rather than a bug fix:

1. *It is correct.* Italian East Africa genuinely could not be fed from Rome, and a supply
   layer that says so is doing its job. Then Italy's order of battle should not garrison it.
2. *The depot bar should scale with how finely a country is drawn* — a share of the
   country's development rather than an absolute 0.8. This is the fix that generalises, and
   it changes the 1936 map's supply everywhere.
3. *A colony should supply itself.* A depot rule that reads "development ≥ 0.8 **or** the
   most developed province of a disconnected holding" would feed Eritrea and nothing else.

Nothing changed but the comment: the constant carries balance weight in a shipped scenario.

### 3c. One war, several signatures — **measured; and the cost depends on packaging**

**Attacked:** handoff §5e — *`open_trade`'s breach test and `no_war`'s both reduce to "at
war", so a pair holding both takes two breaches for one war: −70 regard, −12 legitimacy.
Floored at −100 and never measured in play.*

**Measured.** One war, one aggressor, varying how many agreements the pair held:

| agreements held | breaches | regard | legitimacy |
|---|---|---|---|
| 1 | 1 | 0 → −35 | 50 → 44 |
| 2 | 2 | 0 → −70 | 50 → 38 |
| 3 | 3 | 0 → **−100** (floored) | 50 → 32 |
| 4 | 4 | 0 → −100 (floored) | 50 → **26** |

Two things the handoff's line did not say:

* **Regard floors and legitimacy does not.** Past three agreements the penalty changes
  shape: the diplomatic cost stops growing and only the domestic one continues. Four
  agreements is −24 legitimacy in a single turn — half a starting government's stock, on
  top of the −8 an unprovoked war already charges.
* **The cost depends on how the same promises were packaged.** `assess_breaches` already
  refuses to charge twice for *two clauses of one treaty* — "charging it twice would say a
  power that signed a longer document behaved worse" — and then charges exactly that across
  *two treaties of one clause each*. One agreement carrying `no_war` and `open_trade` costs
  −35; two agreements carrying one clause each cost −70, for identical promises identically
  broken.

**PARKED for Alex.** The choice is a real one and it is not obvious:

1. *As it stands.* Breaking four separate signatures is four betrayals and should cost four
   times. Defensible, and it makes a dense treaty network genuinely expensive to betray.
2. *One war is one betrayal.* Charge once per pair per turn, whatever was signed — the same
   argument the per-agreement dedup already makes one level down, applied one level up.
3. *Floor the legitimacy charge too*, so the shape does not change at the third agreement.

Nothing changed: this rebalances how expensive aggression is in a scenario with a treaty
network, which is exactly the kind of decision left for Alex.

---

## 2. Recorded live playtests

### PT34a — Modern Day, 16 monthly turns, `rivals` — **and the first tape that replays**

```
16 turns, 135 recorded calls, 1 failure, 2703s
rewind to turn 14: recorded 3d7882d339f5 / replayed 3d7882d339f5   MATCH
replay:  16/16 turns reproduced the recorded fingerprint, 0 calls unused, 40.8s
detectors: nothing suspicious on any turn
invariants: nothing reported on any turn
```

**Phase-2 handoff §5c is closed.** *"There is currently no live tape that replays, so the
regression fixture the phase was built around is empty until the next recorded run."* Both
PT33 tapes miss because `403943b` changed the prompts; this one hits, sixteen for sixteen,
in forty seconds and no quota. It also re-proves tonight's digest change from the other
side: a tape recorded under the sorted-column `fingerprint()` replays under it exactly.

**Live coverage mocks cannot reach:**

* a **Security Council session convened on turn 1 and declined to table a resolution** —
  the organisation layer's *refusal* path, live for the first time;
* a **government fell on turn 1** (Curaçao) — the coup path;
* **Russia proposed and ran a sabotage undertaking** on turn 3 — the player/agent gambit
  channel end to end against a real model;
* **partisans took Zaporizhzhya back** on turn 14; five provinces changed hands.

**Four negotiations, four refusals — again**, all correct for the dispositions. Exactly what
§5b predicted, and the reason the `concord` profile exists.

**Two balance observations, neither chased (report readings, not mechanic defects):**

1. Turns 6–16 lead with *Ukraine in Khmel'nyts'kyy is cut off and going short*, three lines
   a turn. `significant()` ranks supply attrition above everything a quiet turn contains.
   True, and repetitive.
2. `rus -> m_maf (oil): the lane is cut` recurs on four consecutive turns. A route reads as
   cut every turn it *is* cut — a state readout rather than an event — and it appears **one
   Ukrainian fleet on a chokepoint cuts a great power's seaborne trade with everybody**.
   Worth a look when trade is next tuned.

**A harness bug the first tagged run found:** `build()` ignored the world id it was handed
for the sandbox preset, so any run not tagged with the default built a world called
`sandbox` and then asked the engine about a world that did not exist. Fixed.

**Instrument fix from PT34a's triage.** `significant()`'s own docstring states the rule —
*"fifty sanctioned minors is one fact, not fifty lines"* — and it was not applied to
starvation. Three formations cut off in one province produced three near-identical lines, so
on **eleven of PT34a's sixteen turns the entire three-line summary was Khmel'nyts'kyy, three
times**, and everything else the turn contained fell off the bottom. One pocket is now one
line, annotated with how many other formations are in it. Two tests.

### 5 (fifth loop). Every detector, shown to fire — the closure that only looked like one

`test_every_detector_has_a_name_and_a_test` searched two test files for the detector's name
**as a substring**. Its own docstring warns against exactly that — *"duplicating a token of
it here to satisfy a substring search would be the test being satisfied rather than the
detector being proved"* — and that is what it did: the name appearing anywhere, including in
a comment, satisfied it.

Replaced with the same table-and-closure shape the invariants got: `detector -> (what makes
the world wrong, the run that shows it)`, every entry asserted to actually fire, and a
closure test that fails when a detector arrives without one. **All eleven fire.** Two had no
direct firing test before — `rewind-fingerprint-mismatch` and `impossible-objectives` were
covered elsewhere in the file, and `invariant-violated` was covered only by proxy.

This matters more than it looks: a detector nobody has shown to work is trusted anyway, and
a clean run is read as good news.

**Changed:** `tests/test_laboratory.py`, twelve tests replacing one.

### PT34c — sandbox, 12 turns, `concord` — **the acceptance half proved, and the instrument caught out**

```
12 turns, 65 recorded calls, 0 failures, 1607s
rewind to turn 10: recorded 1d7d69990a0e / replayed 1d7d69990a0e   MATCH
detectors: nothing suspicious      invariants: nothing reported
```

**Two acceptances, and §5b is half closed.** Five live negotiations across PT33 all
declined; the `concord` dispositions produced the opposite. Turn 4: *"Veyra accepts. This
proposal formalizes what our summits have already…"*. Turn 12: *"Veyra accepts. We have said
plainly, to you and…"*. Both recorded a real `trade_agreement/active` commitment. **The
negotiation → commitment path is now proved for acceptance**, which had never happened.

**And it recorded `[no terms]`, 0 routes — which is correct behaviour in the wrong world.**
`bootstrap._default_production` makes every nation in an undesigned world produce *exactly*
what its industry burns, deliberately, so the trade layer is inert in a bare world. Checked
directly: `worth_selling` returns `None` for **all six ordered pairs** in the sandbox. So

> **no trade agreement made in the sandbox can ever open a route, however willing the models
> are** — and PT33's five declines and tonight's two acceptances say nothing whatever about
> `open_trade`'s positive path.

That is an instrument misfire of the PT31 kind: a harness that cannot produce the evidence
it was built to look for. Written into `lab_run.py` beside the profile so it is not repeated,
and the run that *can* answer it — Modern Day, `deu` ↔ `ind`, where resources are designed
and Germany genuinely has no oil — is running.

**Also live:** the gambit channel ran on nearly every turn (23 undertakings — summits,
envoys, a counterintelligence sting, buying a minister), and partial applies happened twice
(2 and 4 changes refused), exercising the sever path against real model output.

### 5 (sixth loop). The Group-2 bug class, swept — **two more sites, both fixed**

Group 2 fixed `breach` authoring a penalty ECON-003 refuses. **That fix was applied where it
was found and never swept for.** Swept now: every engine-authored `ADJUST` on a bounded
column. Two more, both unclamped, and **both ride in batches that cover the whole world**
— because ECON-003 names no diff, it condemns the entire batch rather than one write.

| site | batch | reproduced consequence |
|---|---|---|
| `budget.collect_revenue` arrears charge | every nation's revenue, upkeep and drift | one bankrupt minor at stability 0.5 → **not one treasury in the world moved** |
| `unrest.assess_unrest` occupation drag | every occupier at once | an occupier at 0.2 → **every other occupier lost its drag too** |

In a 242-nation world running for many turns, the first is not an edge case; it is a matter
of time. Both now charge what there is — the same fix and the same reading `breach` and the
coup path already use. Four tests: two that the world survives, two that the penalty is
**unchanged** for a government that can pay it.

**Noted, not built:** the class is now three sites deep and each was found separately. What
would find a fourth is a rule that engine-authored adjustments to a bounded column must be
clamped at the point of authorship.

**And the general guard, so a fourth site fails on the day it is written.** The three
per-site fixes are pinned individually; `tests/test_engine_penalties.py` asks the question
instead — *drive the world to the floor and run the engine over it*. Every nation at zero
stability, zero legitimacy, zero treasury, in arrears, occupying, at war, holding a pact
that war has broken. Four passes individually, all of them together through `jump()`, and a
year-long turn (which scales every charge by twelve — a clamp written against the *unscaled*
constant would hold at a month and fail at a year). **Verified to catch a regression**: with
one clamp reverted, two of the six fail with the ECON-003 message.

### 3d. What a deterrent and a won sky are worth — **measured on the real map**

Both are on the plan's list as chosen-not-measured. `resolve_contest` compares one number
against a roll in `[0,1)`, so a term only means something next to that: **0.10 is ten points
of odds.**

**Deterrence** (subtracted from an attacker's pressure), every nuclear power in Modern Day:

| power | warheads | delivery | survivability | deterrence |
|---|---|---|---|---|
| Russia | 40 | triad | 0.95 | **0.180** |
| United States | 38 | triad | 0.95 | **0.180** |
| China | 18 | triad | 0.95 | 0.180 |
| France | 12 | triad | 0.95 | 0.180 |
| India | 9 | triad | 0.90 | 0.180 |
| United Kingdom | 8 | triad | 0.95 | 0.175 |
| Pakistan | 8 | missile | 0.55 | **0.100** |
| Israel | 5 | missile | 0.55 | 0.070 |
| North Korea | 3 | missile | 0.60 | 0.055 |
| *(any non-nuclear power)* | — | — | — | 0.000 |

Two things worth having on the record:

* **The term saturates.** Russia's forty warheads and the United Kingdom's eight are worth
  the same 0.18. Above roughly eight, arsenal *size* stops mattering and only **delivery**
  does — triad 0.18, missile 0.055–0.10. That is arguably the correct simulation (MAD is
  a threshold, not a quantity) and it is worth Alex knowing it is the behaviour, because it
  means building the tenth warhead buys nothing and building a triad buys everything.
* **The India/Pakistan asymmetry is 0.18 against 0.10** — attacking India costs eighteen
  points of odds, attacking Pakistan ten, entirely because of delivery. That is the pair the
  `flashpoint` run puts under pressure, so the run has a prediction to be measured against.

**A won sky** is bounded at `_SUPERIORITY_MAX = 0.10` and scaled by margin, with a dead band
below 0.08 — so at its absolute ceiling air superiority is worth **less than half** what a
triad deterrent is worth, and its stated intent ("about as much as favourable terrain, less
than surprise") is consistent with that. Nothing to park.

### 5 (seventh loop). Severance over insert and delete — the first handoff's own question

**Attacked:** handoff §6.1 — *does `SEVER_MINORITY` interact correctly with insert/delete
diffs? I only tested it on SETs.* It stayed that way because creating and destroying rows is
engine-only (CORE-003), so reaching the case needs `Role.ENGINE`.

**Worth reaching.** A severed SET leaves a column at the value it already had — a small
wrong answer. A severed INSERT that landed anyway leaves **a row nothing accounts for**, and
a severed DELETE that went through anyway destroys one — and in both cases the audit trail
says it did not happen, so a rewind would not undo it.

**No defect.** Four cases pinned: a severed insert leaves no row; a severed delete leaves
the row alone (and takes the *right* one when two are proposed); a batch that severs a row
change still rewinds byte-identically; and `SEVER_MINORITY` is about how much of a batch was
wrong rather than which op it used, so a batch mostly of refused inserts is refused whole.

**Changed:** `tests/test_partial_apply.py`, four tests.

### 5 (eighth loop). Visibility inheritance on **every** commitment path

**Attacked:** the first handoff's reviewer note on the rewind fix — *is the visibility
inheritance right for every commitment path, or only the ones I exercised?*

INFO-002 is what makes this matter: it **names no diff**, so a mis-scoped commitment does
not lose one write, it loses the agreement, its parties and its terms together — and the
failure reads as the negotiation having settled nothing.

**Two writers, both now checked. No defect.**

* `diplomacy.extract_commitments` reads the scope off the event it cites. Pinned over
  **six commitment kinds × three visibilities = eighteen cases**, plus a secret
  `territory_cession`, which is the batch with the most writes in it (agreement, two
  parties, and a term naming ground). Every one lands at the talks' own scope.
* `organisation.demand_ceasefire` **hardcodes `PUBLIC`** rather than reading its cause —
  safe only because every event that module writes is public. That was a coupling between
  two files with nothing holding it. Now held: a structural test fails if any event in
  `organisation.py` stops being public, because that is the day the Council's ceasefire
  starts being refused whole and the failure looks like the Council doing nothing.

**Changed:** `tests/test_commitment_visibility.py`, twenty tests.

### 3e. Sanction severity — **measured, and the measurement found a pacing bug**

The last of the named chosen-not-measured numbers. At severity 1.0 a measure takes 3% of the
economy and 6% of the treasury a month; over the eight turns a measure stands that is **−21%
economy and −39% treasury** — a headwind, as intended. Severity sums across concurrent
measures, so three at once is −44% and −91% over the same eight turns.

**The docstring quoted those as absolutes** — *"about a point of economy and two and a half
of treasury a month"* — for rates that are proportional. True of a power with an economy near
33, misleading for anybody larger. Restated.

**And the measurement found a real bug.** `apply_sanctions` scaled linearly in span —
`1 - rate * severity * span` — where every other per-turn rate uses `pacing.compounded`:

| yearly turn | linear | compounded |
|---|---|---|
| severity 2, treasury kept | **−0.46** | 0.21 |
| severity 3, economy kept | **−0.10** | 0.32 |

A negative multiplier meets `max(0.0, …)`, so **one yearly turn emptied a sanctioned power's
treasury outright**, and at severity 3 took its economy to the floor. Exactly the bug
`atlas.engine.pacing` exists to prevent, with the function that prevents it one import away.
Fixed; monthly behaviour unchanged, which is what the layer was tuned at and what the third
test pins.

**Changed:** `src/atlas/engine/organisation.py`, `tests/test_organisation.py`, three tests.

### 5 (ninth loop). A twelfth detector — the engine refusing its own batch

Three times this project has found the same bug one site at a time (`breach`, the arrears
charge, the occupation drag). **Every one was visible in a live run and nothing was looking.**
So there is now a detector for the *class* rather than three tests for its instances.

`engine-batch-refused` reads `diff_batch` for `proposer_role = 'engine'` and
`status = 'rejected'` — ground truth the applier wrote, which no phrasing change can hide. A
model batch being refused is the system working; an engine batch being refused is the engine
proposing something it had already decided was true.

*The closure test added earlier tonight caught the new detector immediately for having no
demonstration* — which is exactly what it is for. It has one now, plus the half that decides
whether anybody reads it: three refused **model** batches must not make it fire.

**Changed:** `src/atlas/testing/laboratory.py`, `tests/test_laboratory.py`, two tests.

### PT34d — Modern Day, 12 turns, `concord` (deu ↔ ind) — **§5b closed, and a nine-turn seam found**

```
12 turns, 101 recorded calls, 1 failure, 2505s
rewind to turn 10: recorded 2887a057bda2 / replayed 2887a057bda2   MATCH
detectors: 1 finding (triaged below)      invariants: nothing reported
```

**`open_trade` opened a route against a real model. Handoff §5b is closed.**

| turn | what happened |
|---|---|
| 4 | `deu → ind`; India: *"This is exactly the arrangement India wants"* → `trade_agreement/pending [open_trade]`, India ships `rare`, 0.185/month |
| 6 | **India begins shipping rare to Germany under their agreement** ← the route opened |
| 7 | the rare shipments stop: the agreement behind them no longer stands |

Negotiated → extracted → termed → activated → **opened** → closed. The whole path, live.

**And the models noticed the ending before we did.** India's own staff wrote the agreement's
status to `fulfilled` on turn 6 — one month into a *standing* supply arrangement — which the
engine correctly reads as no longer active, so it cut the route. Germany and the United
States both dispatched envoys the next turn asking why shipments had stopped.

> **PARKED for Alex.** `fulfilled` means *the promise is discharged* — right for a cession or
> a tribute, meaningless for a standing obligation — and nothing refuses it. Narrowing which
> statuses a model may write to which commitment kinds is a rule change; the recorded run is
> on the shelf to test it against.

**The detector finding was real, and it was a seam.** `impossible-objectives` reported
*Russia named ground it cannot act on 6 times*. Triaged rather than assumed: Russia's briefing
said, every turn for nine turns —

> `Donets'k [r_ukr_donets_k] -- you hold it, you are 55% of the way to taking it`

Russia **controlled** Donets'k and a contest was open with Russia as the attacker. It could
never end: `captured = pressure >= THRESHOLD and held != attacker`, so once the attacker holds
the ground the capture branch is unreachable and the row accumulates pressure forever. The
staff read that line, ordered a push, and the objectives channel refused it with *there is
nothing there to push into* — six times. **Two parts of the engine describing one province in
contradictory terms.**

Cause: an asymmetry between the two paths into the resolver.
`objectives.pushes_as_contests` drops a push whose defender is the pushing power itself; a
clash **reported by the world simulation** was never checked. Fixed, with a reason reported.
Plus a new invariant — `nobody-besieges-their-own-ground` — because refusing to *open* one is
a claim about a proposal and the invariant is the same claim about the world.

*My first reading of this finding was that it was a detector misfire. Checking the briefing
is what showed it was not.*

**Changed:** `src/atlas/engine/resolution.py`, `src/atlas/testing/invariants.py`,
`tests/test_resolution.py`, `tests/test_invariants.py`. Four tests.

### 5 (tenth loop). Two more invariants, one of them validated against the world that had the bug

Each real bug tonight has been turned into a test; two of them are properties of a *world*
rather than of a call, which is what an invariant is for.

**`nobody-besieges-their-own-ground`** (added with the PT34d fix) was run against the
bundled PT34d world afterwards, and it fires:

> `[nobody-besieges-their-own-ground] rus holds Donets'k and is besieging it (55%); that
> contest can never end`

**Had it existed, the run would have reported that on turn 3** — at rest, by name, with the
number — instead of a detector counting refusals until turn 12. That is the difference
between an invariant and a detector, on a real example.

**`a-route-has-an-agreement-behind-it`** is new here. `honour_terms` is idempotent and
derived, so it repairs a turn whose *opening* batch was refused — but the failure it cannot
repair is its own batch being refused on the **closing** side: the agreement lapses, the
close is thrown out, and because the route now exists with no active term behind it nothing
else will ever remove it. The seller ships forever under a treaty that no longer exists.
Route ids are derived from the term that opened them, so the question is answerable from the
id; seeded routes carry a scenario's own ids and are deliberately none of its business.

*Checked against the PT34d world: it reports nothing there, so it is not firing on the
route that legitimately opened and closed.*

**Also checked and deliberately not made an invariant:** a peace leaves open contests
between the ex-belligerents. They are invisible (a briefing's fronts are scoped to wars the
viewer is in) and `decay_idle_contests` removes them over a few turns, so an invariant would
fire on the legitimate decay window. Noted as an observation: a war re-declared inside that
window inherits the old siege pressure.

**Changed:** `src/atlas/testing/invariants.py`, `tests/test_invariants.py`. Twelve invariants
now, every one with a demonstration.

### 5 (eleventh loop). The rule layer's own fail-safe, which nothing exercised

`validate`'s docstring states the most important fail-safe in the layer — *a rule that
raises is treated as a violation rather than propagating; a buggy rule should fail closed* —
and **nothing tested it**. The laboratory has the equivalent test for a detector that raises,
written because a broken detector taking seven working ones with it makes a run come back
clean. The stakes here are higher: a rule failing *open* would not report a clean run, it
would let an illegal change into the world with nothing saying so.

**No defect** — it fails closed, correctly, and three properties are now pinned:

* a raising rule refuses the batch and names itself and the exception;
* it does **not** stop the other rules running — a real violation behind it is still caught,
  so the batch is refused for the right reason as well as the wrong one;
* it condemns the **whole** batch rather than one diff, because a rule that broke decided
  nothing about any particular write. Severing around it would apply most of a batch nothing
  had checked.

**Changed:** `tests/test_rules.py`, three tests.

### 3f. PARKED — the world-sim re-pin, measured over 391 calls instead of four

Phase-2 handoff §7.2 named this itself: *the world-sim re-pin's latency claim rests on four
calls… not enough to characterise the distribution.* Tonight's four recorded runs are **391
calls**, and every tape carries a duration. On the **same prompt** (Modern Day's world
simulation, 76–77k characters):

| tape | provider | n | median | max | ok |
|---|---|---|---|---|---|
| pt33/modern-day | antigravity, gemini-3.6-flash-medium | 12 | **19.5s** | 30.0s | 12/12 |
| pt34-modern-day | claude, claude-sonnet-5 | 17 | **109.1s** | 179.8s | 16/17 |
| pt34d-modern-day | claude, claude-sonnet-5 | 13 | **131.4s** | 214.7s | 12/13 |
| pt34e-modern-day | claude, claude-sonnet-5 | 11 | **127.3s** | 184.2s | 11/11 |

**The re-pin made the one call every turn depends on roughly six times slower**, and the
four-call sample that justified it (22s, 37s) is not within sight of the 41-call reality. It
also did not buy immunity from the failure mode it was meant to avoid: **2 of those 41
world-sim calls crashed outright** (`claude exited with code 1`) — ~95%, against agy's 12/12.

The rest of the engine was never the problem. Over the same 391 calls: `tracked_agent` on
codex **12.7s median across 210 calls at 100%**, `parser` 3.6s, `diplomacy` 6.3s, `gambit`
14.0s, all 100%. **The world simulation is about two thirds of a turn's wall clock**, because
the five tracked agents run in parallel and it does not.

**Alex's call, parked with the data.** The re-pin's narrow ground was that *an unpredictable
provider on the one call every turn depends on is a worse risk than a slow one*, and that
argument survives — what does not survive is the belief that the slow one is only slightly
slower. The honest statement of the trade is now:

> **agy at ~20s with episodic flake, against claude at ~120s with a ~5% crash rate.**

Caveat stated rather than buried: these are different days under different load, and the agy
figures come from one earlier session's tape.

### PT34e — Modern Day, 12 turns, `flashpoint` (ind ↔ pak, nuclear, seeded pact)

```
12 turns, 100 recorded calls, 0 failures, 2206s
rewind to turn 10: recorded dc84550808ac / replayed dc84550808ac   MATCH
detectors: 1 finding — the same Donets'k seam PT34d found (this run predates the fix)
```

**Nobody went to war.** Two nuclear-armed powers, a pact between them, dispositions on both
sides written to want the line of control settled, twelve turns — and neither attacked.
India ran a counterintelligence sting and three `expose` undertakings; Pakistan sent an envoy
to Beijing for Chinese backing; three negotiations, all declined by Pakistan on the substance.
Regard ended at **−98 / −95** with no shot fired.

That is the profile working as written (*"if neither attacks, that is a finding about the
deterrence disclosure rather than a failed run"*), and it is the **third consecutive run in
which no model chose to start a new war** — every war in every run tonight is one a scenario
seeded. Worth knowing before the nuclear layer is judged for never having been used: nothing
has yet come close enough to use it.

**So the breach and launch paths still have no live coverage**, and that is now an
evidence-backed statement rather than an omission: three runs, 36 turns, real dispositions
designed to provoke, no war.

**But India tore the pact up on turn 1, for nothing:**

```
turn 1  tracked_agent ind   commitment 11  status  "active" -> "broken"   [applied]
        event: "India declares the non-aggression pact with Pakistan exhausted
                and no longer binding"
```

`breach` is what makes tearing up a signature cost something — the `treaty_broken` event,
−35 regard, −6 legitimacy, a casus belli for the injured party, and `breached_by_nation_id`
so the record says who did it. **All of it is skipped if the status simply changes.**
Pakistan got no grievance; nothing recorded that India had done anything.

> **PARKED for Alex — and it is the same question PT34d parked from the other side.** There,
> a model wrote a standing supply agreement to `fulfilled` and silently cut its own trade
> route. Two independent live instances, two different statuses, one hole: **`commitment.status`
> is the column the whole treaty lifecycle hangs off, and a model may write it to anything.**
> Decision 120 made `relation.treaty_status` engine-only for exactly this reason, and schema
> 18 stopped one signatory activating a pending agreement — the *breaking* direction was
> never closed. A rule change about what a model may write, so it is Alex's.

**Detected now, though.** New invariant `a-broken-agreement-names-its-breaker`: a treaty
recorded as broken by nobody went through no breach and cost nobody anything.
`a-breach-names-a-party` does not catch it — that asks whether a *named* breacher is a
signatory, and says nothing about a breach that names nobody. Verified against both bundled
worlds: it reports PT34e's pact and stays quiet on PT34d.

**Changed:** `src/atlas/testing/invariants.py`, `tests/test_invariants.py`. Thirteen
invariants now, every one with a demonstration and a no-crosstalk case.

### PARKED — why no war started, and a correction to my own earlier note

I recorded above that *"no model chose to start a new war"* across three runs. **That was
imprecise in a way that mattered, and the accurate version is a finding.**

`declarations` — the field a war is declared through — is offered to **`Role.WORLD_SIM` and
to nobody else** (`diff._batch_schema`). A tracked agent's schema carries
`military_objectives` and `build_orders`. Its briefing says so plainly:

> *"You cannot seize territory or declare war directly, and you never decide who wins a
> battle; the engine resolves all of that."*

Disclosed design, not an oversight. And a `push` objective cannot substitute, because
`pushes_as_contests` drops a push whose defender is not already an enemy. **So two of the
three powers I described as "choosing" not to attack could not have attacked.**

What India did instead is the entire ladder it was given: a counterintelligence sting, three
`expose` undertakings, an ultimatum on turn 9 demanding talks on the line of control,
Pakistan's refusal on turn 10 — and then nothing, because there was no next rung.

**The one role that *could* declare had everything it needed and did not.** The world-sim
prompt for that turn is 76,791 characters containing India's disposition verbatim (*"has
decided the line of control question must be settled in this decade"*), the −98 regard, the
ultimatum, and the refusal.

> **The observation to weigh:** the sole author of wars is the one actor with no stake in
> one. The world simulation is a referee describing everybody; a grievance belongs to a
> power. Whether a tracked power should be able to declare war — against the same legitimacy
> charge everyone else pays — is a design decision with consequences either way. **Alex's.**

*Nothing here is broken: the engine does exactly what it says it does. Recorded as an
observation with its evidence, and it also explains why the breach and launch paths have no
live coverage after 36 turns of trying.*

**Pinned, since the briefing makes a promise about it.** A tracked agent's task block states
*"You cannot seize territory or declare war directly"* — true because `declarations` reaches
`Role.WORLD_SIM` and no other role. If that field is ever added elsewhere, the sentence
becomes a lie a staff is reading while it acts. Two tests: `declarations` is present for
`WORLD_SIM` and **absent for every other role**, with the briefing line checked in the same
test; and a `push` objective is not a way around the channel — it is dropped while the
defender is not already an enemy, and fought the moment it is. The design question stays
open; the code and the briefing agreeing about it does not.

### 5 (twelfth loop). `atlas check` — the checker could only be pointed at a laboratory

Thirteen invariants, and **nothing outside `scratchpad/lab_run.py` ever ran them.** The
checker ships inside the package; the detectors call it; the tests call it — and a game
somebody actually played was the one world it was never run against.

```
atlas check --world-id modern-day --explain

modern-day at turn 10: 13 invariant(s) checked at rest
  [a-broken-agreement-names-its-breaker] commitment 11 (non_aggression_pact) is broken and
      nothing records who broke it
  [nobody-besieges-their-own-ground] rus holds Donets'k and is besieging it (55%); that
      contest can never end
```

That output is real — run against PT34e's bundled world, where it found both of tonight's
live findings unaided.

Read-only by construction (every invariant is a query), pinned by a test comparing the
fingerprint either side of a run. Exit codes for a shell: **0** sound, **2** something to
look at, **1** no such world — *found something* and *could not look* being different
answers is the part worth a test, since conflating them would report every missing save as
sound.

**Changed:** `src/atlas/cli.py`, `tests/test_cli_check.py`, four tests.

### PT34f — Modern Day, 8 turns of **90 days** — the first non-monthly run ever recorded

```
8 turns of 90 days = 2 years of game time, 66 calls, 0 failures, 1140s
rewind to turn 6: recorded 402492bbec6c / replayed 402492bbec6c   MATCH
replay:  8/8 turns reproduced the recorded fingerprint, 0 unused, 37.6s
detectors: nothing suspicious      invariants: nothing reported
```

**Every live run this project has made was monthly** — the one span where all the pacing
arithmetic is the identity — so none of decisions 44–47 had ever been observed against a
real model. This one was, and it produced a number checkable against the monthly runs.

Saudi Arabia is the first power whose industry cannot get what it needs:

| run | span | first short | elapsed |
|---|---|---|---|
| pt34d | 30 days | turn 12 | **12 months** |
| pt34e | 30 days | turn 12 | **12 months** |
| pt34f | 90 days | turn 4 | **12 months** |

**Identical in elapsed time across a threefold change in turn length**, in two independent
monthly runs and one seasonal one, on a mechanic nobody tuned for the comparison.

It is also the distinction tonight's sanction bug turns on, now visible from both sides:
**a flow scales linearly with the span and a rate compounds.** Trade is flows
(`over(x, span)` for production, shipment and consumption) and was right; the sanction drag
was a rate multiplied like a flow, and at a yearly turn it went negative.

> **PARKED — trade endowments over a long war.** The handoff's least-confident list says they
> were *"calibrated against demand ratios, never played through a long war"*. They have now
> been played through two years of one. By month 18 France, India and Japan are short; by
> month 21 China and Germany are too — oil and rare in almost every case. The world drifts
> steadily toward shortage. Whether that is the intended pressure or an endowment that does
> not keep up is Alex's call, and the tape is on the shelf to re-measure against.

### 3g. The gazetteer's prompt cost — **measured; the cap is cheap and it binds often**

Phase-2 handoff §5e: *"The gazetteer adds up to eight lines to the tracked-agent briefing.
The prompt-size delta on the 2026 map is unmeasured — the 8-line cap was chosen, not
calibrated."* Tonight's tapes hold every prompt verbatim. Over **252 tracked-agent prompts**
across four runs:

| | |
|---|---|
| median tracked-agent prompt | **14.8k chars** |
| prompts carrying a gazetteer | 181 / 252 (**72%**) |
| median gazetteer | 389 chars — **2.63% of the prompt** |
| largest gazetteer | 474 chars |
| places listed | median 6, max 8 |
| **gazetteers at the 8-place cap** | **77 / 181 (43%)** |

**The cost is not the story; the truncation is.** 2.6% of a briefing is cheap for the thing
that stops a staff inventing *Coastal Reaches*. But the cap binds in **43%** of the
gazetteers that appear — so nearly half the time a place the briefing mentions is being
dropped without a canonical name, which is precisely the failure the gazetteer exists to
prevent.

> **PARKED for Alex.** Raising the cap from 8 to 12 would cost roughly another 1% of the
> prompt on this evidence. Whether that is worth it is a prompt-budget judgement, and it is
> now a judgement with numbers under it rather than a chosen constant. *(For scale: the
> world-sim prompt on the same map is 76.8k chars — five times a tracked agent's — so the
> tracked-agent budget is not where the pressure is.)*

### 3h. PARKED — what the routing log counts, measured

Phase-2 handoff §2a listed this as a *"consequence worth checking"*: the routing-log guard
`len(routing_log) == report.calls` covers calls **inside a jump**, and the cassette records
calls at the provider seam — *"the two therefore disagree about what 'every call' means, and
neither says so."* Measured from tonight's bundles:

| run | `report.calls` summed (what the guard covers) | calls on the tape | outside every jump |
|---|---|---|---|
| pt34d | 94 | 101 | **7** |
| pt34e | 94 | 100 | **6** |

**The gap is not noise — it is exactly the negotiation channel.** Three `diplomacy` replies
and three `parser` extractions per run, one pair per `DiplomacySession`, none of which runs
inside `jump()`. So **~6% of every run's provider calls never reach the observability record**,
systematically, and the guard that exists because of *"126 calls made, 81 logged"* is silent
about them.

Six call sites reach a provider without `_invoke` (`advisor.py:89`, `diplomacy.py:157`,
`diplomacy.py:277`, `eve.py:219`, `parser.py:165`, `providers/diagnostics.py:190`), and
`parse_orders` is among them — it runs for every order a human types.

> **Alex's call, parked rather than half-fixed.** Closing it means routing every provider
> call through one logged door, which is a change across six call sites in five modules, not
> a patch. `routing_log` is outside the fingerprint and is pruned by rewind, so the change
> would be digest-neutral and rewind-safe — but it is a change to the observability
> contract, and the honest interim position is that the number is now known: **6%, and it is
> the diplomacy layer.**

### 5 (thirteenth loop). Thirty turns against mocks, with the invariants checked every turn

**Every bug this night found was an accumulation bug.** A contest that could never resolve
sat for nine turns; a pact torn up on turn 1 went unnoticed until the world was read at turn
12; a route outlived its treaty. None is visible in a one-turn test, and a live run costs
forty minutes and real quota to reach turn 12 once.

A mock provider reaches turn 30 in under a second. It cannot say what a *model* would do —
that is what the recorded runs are for — but it drives the same engine through the same
phases, and the invariants are claims about the **world** rather than about anybody's
judgement. So this is the cheap half of the same question, and it now runs in the suite:

* thirty monthly turns of a world with a war, armies, yards and a treaty — every invariant
  after **every** turn, and a failure names the turn it first appeared on;
* eight **year-long** turns of the same, where everything that scales with the span gets
  twelve times the movement per turn — which is exactly where tonight's sanction bug lived;
* a guard on the guard: if `holds_in` were ever narrowed, the file would keep passing while
  checking less, so it asserts all thirteen invariants are checked at rest.

**No defect — but the sweep caught the fixture on its first run.** My own `busy` world
created a `trade_agreement` with a direct INSERT and never synced the derived
`relation.treaty_status`, and `treaty-status-hides-a-commitment` reported it on turn 1. The
instrument catching the test rather than the engine is a fair advertisement for it, and the
fixture now asserts it starts sound.

**Changed:** `tests/test_long_game_invariants.py`, three tests.

### 5 (fourteenth loop). The breach path, end to end through `jump()`

**No live run has ever produced a treaty breach**, and after tonight's playtests the reason
is known rather than guessed: only `Role.WORLD_SIM` can declare a war, and across **44 turns
of Modern Day it declared none**. So the path that charges a power for tearing up its
signature was exercised only by tests calling `assess_breaches` directly — which proves the
function and says nothing about the wiring between a model's answer and the number a player
reads.

Driven through the front door now: a world simulation reports a declaration in its
`declarations`, `jump()` resolves it, the breach pass reads the world, and **all five things
a breach does** must be true at the end of the same turn — the war, the agreement broken and
attributed to the aggressor, the legitimacy charge, the injured party's casus belli, and the
line in `report.betrayed` the player reads. Plus:

* `relation.treaty_status` stops saying `non_aggression` in **both** directions — seven
  consumers read that column rather than the agreements, and a breach that left it standing
  would leave the invasion guard still protecting the victim;
* the world the breach leaves behind passes the **whole invariant sweep** at rest, including
  `a-broken-agreement-names-its-breaker` — which is what says the breach went through
  `breach()` rather than through a status write, the exact thing PT34e caught India doing;
* and a war between powers holding **no** pact breaks nothing and is charged once, not twice.

**No defect.** The wiring holds; it had simply never been asserted from end to end.

**Changed:** `tests/test_breach_end_to_end.py`, four tests.

### 5 (fifteenth loop). The nuclear layer, end to end through `jump()`

`test_nuclear.py` is twenty-eight tests and they prove the **layer**: interception odds,
survivability, the deterrence cap, the proportional reply, the refusals, the disclosure, the
rewind. Every one calls `launch` or `apply_gambit` directly.

**Nothing drove one through `jump()`** — and tonight's playtests are why that matters. No
live run has ever fired a warhead: three runs, thirty-six turns, two nuclear powers at −98
regard, and no war to fire it in. So the wiring between a tracked agent's answer and the turn
report was the part with **no evidence behind it at all**, and a layer that works but is not
plugged in looks exactly like a layer that works.

Deliberately not a second copy of the odds tests. Five tests assert only what the front door
adds: the gambit channel carries the launch and warheads leave the arsenal; **the engine's
reply happens inside the same turn without anybody choosing it**; the player is told; the
world the exchange leaves behind passes **all thirteen invariants**; the whole turn rewinds
byte-identically; and an unarmed power proposing a launch costs the turn nothing.

**No defect — and the fixture found a real fact about the channel.** The first version armed
Aurelia and was never offered a gambit at all: `_offer_gambits` excludes the **player's own**
nation from the pool, and the sandbox seeds Aurelia as the player. Correct behaviour (a human
decides their own undertakings), and worth knowing when writing any test of that channel.

**Changed:** `tests/test_nuclear_end_to_end.py`, five tests.

### PT34g — gathering-storm, 1936's first recorded run ever — **partial, killed by quota**

```
5 turns sealed, then 7 consecutive aborted attempts at turn 6
64 recorded calls, 28 failures: 27 x "claude exited with code 1", 1 x codex quota exhausted
replay: 12/12 attempts reproduced the recorded fingerprint, 0 unused, 145.9s
```

**The abort was the subscription, not the engine.** The degradation path did exactly what it
promises: the world simulation failed, the turn was retried, each attempt was marked
`aborted`, nothing was written, and the world stayed at turn 5. A provider outage cost the
turn and not the world. **The tape replays anyway — aborted attempts included** — so even a
run killed by exhaustion is a faithful regression fixture.

**Germany remilitarised the Rhineland on turn 1 by writing one column.**

```
turn 1  tracked_agent ger  commitment 8  status  "pending" -> "broken"  [applied]

commitment 8: military_access, parties fra/gbr/ger/ita
"Locarno (1925): the Rhineland is to remain demilitarised, guaranteed by
 Britain and Italy. Untested."
```

No `treaty_broken` event, no −35 regard with each of three guarantors, no legitimacy charge,
no casus belli for France, Britain or Italy, nothing recording that Germany did it.

**This is the third independent live instance of the same hole, in three consecutive runs** —
after India's pact (PT34e) and the `fulfilled` trade agreement (PT34d). It is the most
pointed of the three: the first thing a real model does with the 1936 scenario is tear up
Locarno, which is the *right instinct for the era*, and the engine grants it for free. The
hole is load-bearing rather than theoretical. **The invariant added hours earlier caught it
on turn 1 and every turn after** — which is what it is for.

**And the supply finding, confirmed live.** Italy's East African garrisons — Maekel, Debub,
Debubawi Keyih Bahri, later Galguduud — are cut off and going short from **turn 1**, exactly
as the static sweep predicted. Partisans retook Amhara on turn 3.

**A harness bug this found:** `lab_replay.py` had `gathering-storm` in neither its preset list
nor an error path, so asking it to replay this tape **printed nothing and exited 0** — a
harness reporting success for a run it never attempted, which is the failure mode this
project distrusts most. Fixed, with an explicit refusal for an unknown preset.

---

## MORNING SESSION — 2026-08-08, after the limit reset

### The `commitment.status` hole, closed

**Approved by Alex and fixed.** Full detail in `ENGINE_CHANGELOG.md`; the parked item above
is marked resolved. Tests **1843 → 1865 (+22)**, ruff clean, seed fingerprints unmoved.

* `commitment.status` and `commitment.resolved_turn` → ENGINE-only, the same principle
  decision 120 applied to `relation.treaty_status`, one level down.
* **The suite went green on the permission change alone** — nothing legitimate depended on
  the model route. All three writers were already `Role.ENGINE`.
* **DIP-004 changes job rather than retiring**: written to stop one signatory activating an
  agreement on its own (a guard on models), it now guards the *engine's own* activation
  path, where `accept_commitment` writes ACTIVE. Its three tests moved to `Role.ENGINE`.
* Rewind is untouched — the status change already flowed through diffs and still does.
* **The invariant `a-broken-agreement-names-its-breaker` is now unreachable in normal play**:
  the only route to `broken` is `breach()`, which writes `breached_by_nation_id` in the same
  batch, so there is no ordering in which one lands without the other. Both halves are tests.
* Verified to catch the regression: with the permission restored, **17 of the 22 fail**.

*The three recorded worlds that contain the old behaviour (PT34d/e/g bundles) still report
the violation under `atlas check`, correctly — they are records of what happened, and the
tapes still replay.*

### Victory and scoring — rewind-exactness, claimed since it was built and never tested

`scoring`'s central claim: *nothing is stored, so rewind-exactness is free rather than
defended.* **Free is not the same as tested**, and one condition does real work — **hegemony
is a streak**, announced only when the mark appears on each of the last few turns, counted by
reading the event log back. A rewind deletes events past its target, so it does not merely
un-announce a victory: it **shortens the streak**, and the world afterwards has to behave
like one that never got that far.

Ten tests, checking not that the events vanish but that the derived machinery reading them
gives the same answers as a world that took the shorter path: a rewind into the middle of a
run leaves a genuinely shorter streak rather than one turn from announcing; a marked turn the
rewind did not reach still counts; the score returns with the world; assessing a turn twice
congratulates once; a rewound world re-announces exactly what it earned and the digest
agrees; and for **each of the three conditions** the fingerprint returns — which is what
would notice if any ever started writing a row somewhere. Plus that reading the standings
never writes.

**No defect.** The claim holds, and now says so. Tests 1865 → 1875.

### The label round-trip, over prompts rather than vocabularies

Handoff §7.2's own least-confident note: *"the tests do not assert that everything a model is
shown **is** a label — a new renderer could still print a bare name and pass."* That is the
gap where the failure actually happened: PT33's two staffs wrote *Coastal Reaches* for six
turns because a model had been shown **The Reaches** with no id beside it.

The scan is over the **prompts**: build all four model-facing views and assert no region is
named in any of them without its id. **It holds in all four** — the worry is unfounded, and
now unfounded on the record rather than by luck.

Event prose is excluded deliberately (a model's own words are the gazetteer's *input*, not a
renderer's output), both label shapes are accepted including bold markers, and there are
three guards on the guard — a bare name injected into a renderer's own section is caught, the
same name inside quoted history is not, and the injection point is asserted to still exist,
because a scan that stopped reaching the text would pass by looking away.

**No defect.** Tests 1875 → 1882.

### DECISION 1 — world_sim reverted to Antigravity, with resilience *(Alex's call)*

**The 391-call measurement supersedes the 4-call sample.** claude is ~6× slower on this call
and no more reliable — PT34g died to 27 consecutive `claude exited with code 1`, ~5% of that
run's calls, the same *shape* of failure agy was moved off for. Neither latency nor
reliability justified the swap on the every-turn critical path.

* **agy primary** for `world_sim`; `WORLD_SIM_FALLBACK` is claude.
* **One retry per call** — `_invoke` takes a per-call budget. Three attempts suits a rate
  limit, which clears; not an empty-answer window, which costs a minute to discover.
* **A breaker, not a per-call switch** — three *consecutive* failures to trip, announced
  once, stays tripped for the session. A per-call fallback would make the episodic window
  invisible, which is how it stayed hard to judge the first time.
* **The attestation guard changed sides rather than being deleted.** It required an attesting
  provider and said a re-pin "should have to argue with this"; it argued and lost. Now it
  requires the **fallback** to attest, so a tripped breaker is verifiable exactly when
  reliability is in question.

Tests 1882 → 1891 (+9, one old guard rewritten).

### DECISION 2 — `fulfilled` as a semantic rule *(Alex's call)* — **complete**

Two commits. `bf84aa7` the rule, `44b0252` the PT34d replay regression.

**The classification is semantic, not a whitelist of kinds:** standing clauses (`no_war`,
`open_trade`) are promises you go on keeping and nothing discharges them; one-off clauses
(`cede_region`) are done once, and done is read with **the same query `_violation` uses to
decide the opposite** — one fact read once, rather than two notions of "handed over" that can
disagree. Standing *kinds* cover what terms cannot say (a term-less `trade_agreement` implies
nothing deliberately; `military_access` is inert by design; both are permissions that
*continue*). An **unclassified clause blocks**, because deciding by default is how a standing
obligation gets closed.

**DIP-005 governs the engine, which is the only writer left.** `commitment.status` is
engine-only, and nothing in the engine sets `fulfilled` yet — so this is the *specification*
of a derivation not yet written, enforceable before it is written rather than after it goes
wrong, with a test that fails the day something starts recording agreements fulfilled so its
author reads the rule first.

All five required properties: (1) the recorded answer is refused, (2) the commitment stays
active, (3) the route stays open — **and still open on turn 7**, which is where PT34d
actually failed — (4) a legitimate one-off cession still fulfils, (5) a mixed agreement does
not close when only its finite terms complete.

Tests 1891 → 1909 (+18).

---

## HANDOFF — what is left

Everything below is *not* done. The tree is clean and green at `44b0252`.

### 1. `world_sim_accepted` — engine-accepted output rate *(Alex explicitly wants this)*

Decision 1 reverted the pin on latency and crash-rate evidence, and set the bar as **output**
quality: *revert unless agy's output is materially worse.* The counters that would answer that
are **not built**. What is needed:

* On `JumpReport`: proposed / applied / refused diff counts for the **world-sim batch
  specifically**, plus whether its batch stood. The numbers exist at the call site in
  `turn_engine.jump` — `batch.diffs`, the `ApplyOutcome`, and `_note_refusals` already sees
  the severed ones — they are simply not aggregated per role.
* On `TurnRecord` in `testing/laboratory.py`, so a recorded run carries them and
  `lab_run.py` prints them.
* Then a run on agy compared against the five recorded claude runs already on the shelf
  (PT34a/c/d/e/f), which is the comparison the original re-pin could not make.

**Careful:** *provider success* and *engine-accepted output* are different measures, and
conflating them is the mistake this is meant to correct. A provider answering every call with
diffs the rules refuse is worse than one that fails loudly.

### 2. Remaining pure-hardening candidates, unstarted

* **`engine/causality.py`** — correlation ids and causal breakdowns; never audited this
  session.
* **`_thin_construction`** in `views.py` — the briefing-thinning logic that drops other
  nations' routine public-works events; never audited.

### 3. Still parked for Alex (unchanged, balance/design judgement)

Only `world_sim` may declare war (explains no new war in 44 turns); Italy losing 16% of its
military strength in year one of every 1936 game (the failing constant is
`_DEPOT_DEVELOPMENT`, not `_SUPPLY_RANGE`); one war against several signatures costing regard
that floors and legitimacy that does not; the gazetteer's 8-place cap binding in 43% of
briefings; ~6% of provider calls never reaching the routing log (the diplomacy layer); trade
endowments drifting the world toward shortage over two years.
