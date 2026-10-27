# ATLAS — review handoff: the roadmap batches, and what came out of them

**For:** Codex, reading this against the roadmap he proposed (Batch 1 / Batch 2 / Batch 3 /
gameplay / verification).
**Prepared:** 2026-08-10. **HEAD:** `79a9458` on `master`.
**Tests:** 2545 passing, 1 deselected (`live`), ruff clean over `src`, `tests`, `scripts`.
**Schema:** 23.

> **Fourth of four.** [`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) covers everything before the
> consolidation; [`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md) the consolidation
> phase; [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md) the 1972 era and the
> air-reach fix its playtest uncovered. This one is organised around **your** batches so it
> can be read against the proposal line by line.

> **⚠ Two things to know before anything else.**
> 1. **Schema 19 and 20 invalidate existing saves.** They remain the only two that do, both
>    deliberate. Everything since — 21, 22, 23 — is additive.
> 2. **Two seed fingerprints moved, deliberately.** Not "none", which is what a summary
>    would like to say. See §9.

---

## 1. Batch 1 — correctness

| item | commit | what it fixed | tests |
|---|---|---|---|
| Transactional nuclear launches | `c646c22` | A refused strike had already written its event: the newspapers carried a launch that never happened. `unrecord_events` retracts engine-authored news when the batch is refused. | 2330 |
| The three nuclear findings closed | `71e4fab` | Two of your findings landed; two places I was wrong are written down rather than quietly fixed. | — |
| SSBN-dependent sea deterrence | `518e5fc` | A `sea` power with no submarines still rode out a first strike at the full tier value. `_SEA_WITHOUT_BOATS = 0.10` floors it at the boatless value. | 2333 |
| Morale floor | `3b0dc0b` | A formation at negative morale fought for the other side. Clamped at the write. | 2336 |
| Global 0–100 morale invariant | `63dd52a` | The clamp was local; the guarantee is now global and phase-aware, registered in `testing/invariants.py` with a test that fails without it. | 2359 |
| Fresh == migrated schema | `4cf2abf` | A fresh world and a migrated one disagreed about what an `arsenal` is. The parity test now compares **contracts** — columns, PK, indexes, FKs, CHECKs — not labels. It immediately found an unreported `commitment` FK gap, closed by `111e82d` (schema 20). `KNOWN_SCHEMA_GAPS` is now empty. | 2340 |
| Map-bake auto-verify | `9469e15` | A bake that failed its own verifier reported success. `atlas map bake` now runs `verify_pack` and exits 2 on failure. Three smaller things in the same commit. | 2345 |

**Note on the parity test.** It is the item on this list that paid for itself twice: it was
asked for as a consistency check and behaved as a bug detector on its first run.

---

## 2. Batch 2 — world modelling

| item | commit | what changed | tests |
|---|---|---|---|
| Sovereign / controller / home split | `24ef42d` | `create_world` seeded `home_nation_id = owner_nation_id` for everything, so **Hong Kong was British homeland and nuclear-protected at 0.105** — a nuclear power's colonies were covered by its deterrent, the inverse of the rule. A dependency's home is now nobody. | 2341 |
| Explicit nuclear legs (`sea` tier) | `e20cddf` | A deterrent carried by boats became its own delivery tier. **Schema 19, save-invalidating**, authorised deliberately; `Migration.invalidates_saves` + `reason` is the narrow escape from the additive guard. | — |
| Attack boat ≠ missile boat | `6080452` | One `submarine` kind per era meant every hull counted as a deterrent. | — |
| Boat-level SSBN counts | `c9f07d7` | `arsenal.deterrent_boats` — a count on the **nation**, not a kind on the unit, because nothing on a unit distinguishes Britain's boats from China's out of the same template in the same year. `survivability` takes `min(afloat, declared)`. Schema 20 → 21, additive. 1972: usa 4/9, sov 5/12, gbr 2/3, fra 1/2, **chn 0/4**, isr 0/1. | 2365 |
| Delivery range | `ea12b52` | A handful of gravity bombs reached the far side of the planet. `aircraft` is now bounded at six hops; missile/sea/triad stay intercontinental, which is what those words mean. | — |
| NATO / Warsaw membership | `df9bea2` | **NATO had six members and the Warsaw Pact five.** Now fifteen and seven, minors included as parties — an alliance that forgets its small members is not an alliance. | 2343 |
| Organisation jurisdiction + 15-seat Council | `64b6601` | The Security Council had fourteen members and no idea what kind of body it was. `has_jurisdiction(kind, accused)`; `universal` vs `alliance`. | 2347 |

---

## 3. Batch 3 — the scenario auditor

**`9168187`** · 31 tests · `atlas audit <era>` · 2396 passing.

An invariant says *this cannot be true of a world that is not broken* — it is about the
engine and holds in every era. Nothing said the other thing: **the world this scenario
builds is the world it claims to describe.** That fifteen states were parties to the North
Atlantic Treaty on 1 January 1972 is a fact about 1972, and no invariant can know it.

**The manifests are JSON and import nothing.** That is the whole design. An auditor reading
`SCENARIO.organisations` and comparing it to the database would prove only that
`apply_politics` can copy a tuple — and would have passed on the day the Council had
fourteen members, because the scenario said fourteen and the world had fourteen.

**Ten checks:** alliance membership by exact party set · organisation membership **and**
permanent seats (they fail independently) · dependencies, cores, occupation, ungoverned
ground, and who does not exist yet · leaders in office on the start date · treaty status
against ratification dates · arsenals **in both directions** · required geometry packs ·
the delivery boundary, **walked** on the world's own adjacency rather than read off the
constant · and a meta-check that an empty section is *declared*, not merely empty.

**Fact counts today: 1936 → 74 · 1972 → 62 · 2026 → 43.** Exit 0 on all three.

### The two real gaps it found on its first run

Both were declared in `silent_on` rather than asserted, so the audit passed on defects it
existed to record — and both were then closed:

| gap found | closed by | what it was |
|---|---|---|
| **1936 had no era sovereignty map** | `bd4f5da` | Israel, Pakistan, Bangladesh, India, Burma, Indonesia, Malaysia, Singapore, Algeria, Libya, Zaire and ~130 more were sovereign states on the 1936 map. **202 minors → 57.** A dependency is ground, not a nation: no separate garrison. Ireland was British *home soil with a core* → now `m_irl`, a Dominion. Antarctica reconciled to ungoverned in all three eras. |
| **Modern Day modelled no occupation** | `da28531` | No region anywhere in 2026 had a controller differing from its owner. Crimea was Russian *ground* because Natural Earth files it under `RUS` and the generator took that mechanically. Now: Crimea, Luhansk, Donetsk are Ukrainian-owned, Russian-controlled, Ukrainian-core. |

**Where the map cannot express reality, it says so.** Zaporizhzhia and Kherson stay
Ukrainian — Russia holds most of both by area and neither oblast capital, and one controller
per region admits no half. A province contested by both is stated as unrepresentable rather
than forced.

Also fixed in passing (`9168187`): `pyproject.toml`'s package-data glob never covered
`scenarios/territory/*.json` — a glob does not recurse — so an installed wheel would have
played every province-drawn era at country granularity, silently.

---

## 4. Gameplay improvements

| item | commit | one line | tests |
|---|---|---|---|
| Defend-ally objective | `516b645` | A patron had no sentence for "reinforce my ally": `push` refused (not at war), `hold` refused (don't hold it). `hold` over an **active** pact partner's ground is now accepted; no new kind, no widened CHECK. | 2410 |
| Nuclear balance in briefings | `1104bf2` | MAD reached a leader only inside a gambit prompt — i.e. only while already contemplating a strike. Now in the strategic briefing, reusing the same three functions the launch path uses. | 2420 |
| News salience | `50ef91b` | Four bands. The **window** is now spent by weight too, not just the display order — ordering what survives the cap is no use if the cap threw the coup away first. | 2429 |
| Explain refusals | `b0d4379` | `causality.Refusal(requirement, because, instead)`. Out-of-range strikes now name the nearest reachable target. `instead` is empty rather than invented where no legal equivalent exists. | 2443 |
| Treaty lifecycle by kind | `8a8d3f4` | `settle_fulfilled` — the derivation `fulfillment_blockers` had *specified* since the standing-agreement work while nothing ever wrote `status='fulfilled'`. A cession closes when the ground moves; a standing pact never closes. `pending` untouched. | 2461 |
| Colonial unrest → independence | `b0784bc` | 1936's ninety British regions cost their holder nothing and could never want anything. Momentum, not a die roll. Schema 22 → 23, additive. Needed a third escape in `OWN-001`: a state coming into existence on its own ground is neither conquest nor cession. | 2508 |
| Proxy-war support | `4f4b71f` | A patron had two settings, neutral or belligerent. Five kinds, none of which touches `at_war`. Schema 21 → 22, additive. | 2488 |

### Against your gameplay list

**Done:** defend-ally objective · nuclear balance in briefings · news salience over
construction spam · explain-refusals · treaty lifecycle by kind · colonial unrest →
independence → negotiated decolonisation · proxy-war support.

**Still open** (see §8): war-of-independence as a modelled path · secret/deniable support ·
finite missile range on real geometry · contested provinces.

---

## 5. Tooling and robustness that emerged along the way

| item | commit | what |
|---|---|---|
| Map overlay integration | `5118d17` | Your per-region unit overlays had never run on a tree with the proxy-war and decolonisation work. Reconciled: **zero file overlap**, no conflict possible. Measured by stashing: engine-only 2500, combined 2508 — the overlay contributes **8** tests, not the 177 the file selection suggested. |
| Flaky Qt test, root cause | `61dbf89` | Not a timeout bump. `submit` starts the coroutine before returning; the caller connects after; **Qt resolves receivers at emit time**, so a coroutine with no `await` emitted into an empty list and the signal was *gone*. The two flaky tests were exactly the two with no `await`. Reproduced deterministically by sleeping 50 ms between submit and connect. |
| The production race it exposed | `c8ab464` | The same pattern at **five `main_window.py` sites**. A provider failing before its first `await` would drop its `failed` signal and leave the UI waiting forever. `TaskHandle` now remembers its terminal outcome and replays it to late subscribers, deferred through the handle's own thread, exactly once — guarded by a lock held across record-and-emit. | 

Verification for the Qt work: 50/50 isolated runs, 30/30 under eight busy CPU workers.

---

## 6. Proxy-war validation arc

This is the part of the roadmap that produced the most instructive failure, so it gets its
own section.

**`4f4b71f`** shipped the support layer complete: table, contest term, air-defence term,
funding transfer, briefing lines, rewind support, **22 tests**.

**And no model could reach it.** Found in pre-flight for the first live run by checking all
four possible doors — answer schema, gambit kinds, direct diff write, turn engine — and
finding every one shut. All 22 tests called `declare_support` directly, which is the shape
of test that proves a mechanism works and never asks whether anybody can get to it.
Decolonisation had the same hole in its diplomatic half.

**`21dbf39`** wired it: a `support` property and a `grant_independence` property on the
tracked-agent schema, both engine-checked intent recorded beside `declare_objectives` and
outside the batch. Plus the test the other forty were missing — **from the JSON a provider
returns to the world changing** — and a cheap generalisation: every engine-only table must
name, in one place, how a model reaches it or why none should. It found four unlisted tables
on its first run.

### The two live runs

| | **PT37** `5bfafb3` (uncosted) | **PT38** `79a9458` (costed) |
|---|---|---|
| turns / calls | 12 / 97, 0 failures | 12 / 98, 1 provider `empty_response` |
| Moscow's opening | equipment 2, air-def 2 → ratcheted | **below max**: advisors 2, air-def 1, equipment 2 |
| escalations to max | both patrons, all kinds, by turn 9 | never held |
| de-escalations | **one** | **nine** |
| ending | 7 kinds all at 3 | sov: advisors 1, equipment 3 · usa: intelligence 2 |

**PT37 proved the mechanism and diagnosed the flaw:** four of five kinds cost nothing but
standing, so maximum was always correct and no decision was ever posed.

**`3b00d92`** added the cost. `COMBAT_WEIGHT` untouched — the magnitude was never the
problem. Materiel (`equipment`, `air_defence`) draws treasury **and** industry; the deniable
kinds (`advisors`, `intelligence`) cost standing with the client's enemies; what cannot be
paid for **lapses loudly** rather than going into arrears.

**PT38 proved the cost works.** Moscow suspended everything twice when it ran dry and, on
turn 9 with 3.6 treasury, re-opened with **advisors and intelligence — precisely the two
kinds priced in standing rather than money.** It substituted along the cost axis. The
door-refusal fired four times with the affordable level named; one shipment lapsed live.
Moscow, in its own words:

> *"With the treasury exhausted, aid to Hanoi is suspended **deliberately rather than
> allowed to fail by arrears**."*

**`660a91b`** corrected the number I had got wrong. The cost was set against the scenario's
*starting* treasury (130 for the USSR); PT38 measured **disposable** treasury — what remains
after the rest of the economy takes its cut — at about **22 on turn 1 and falling**. So a
full package is roughly **four turns of reserve, not twenty-seven**. The verdict is recorded
beside the constants: leave them alone; if a future run shows a superpower unable to sustain
any package, `MATERIEL_TREASURY_PER_LEVEL` (1.6) goes **down**, not up.

---

## 7. Verification improvement — NOT done

**The release-audit command you proposed is still open, and it is the right next capstone.**

Everything to build it now exists and is exercised separately: `atlas audit` (three eras,
74/62/43 facts), `atlas check` (the invariant sweep against a save), `atlas map verify`,
the schema-parity contract test, the seed/built-world fingerprint script, and the cassette
replay path. What is missing is the one command that runs them together and returns a single
verdict a release can be gated on.

Recommended shape: `atlas release-audit` → audit all shipped eras · invariants at SEED phase
on a freshly built world of each · pack verification for every required source · schema
parity fresh-vs-migrated · fingerprint reproducibility (build twice, compare) · replay the
ACTIVE cassettes and assert they still hit. Exit 0/2/1 on the established convention.

---

## 8. Open threads and deliberate design calls

| thread | status |
|---|---|
| **Decolonisation's rich-before-poor proxy** | **A real bug, medium severity, deliberately left.** `MOMENTUM_PER_DEVELOPMENT` makes the *richest* dependencies stir first — PT37/PT38 both put **Guam, Puerto Rico and the US Virgin Islands ahead of Angola and Mozambique at 12.0**. That is the century backwards: the 1972 independence wars were in the poorest territories. Development alone is the wrong proxy; population and metropole distance belong in it. Needs a design call, not a tweak. |
| **Secret / deniable support** | Not built. All support is public — a shipment is deniable in the newspapers and not in the field. A covert tier would be a second mechanic. |
| **Finite missile range** | Not built. A sixth delivery tier means a third save-invalidating migration, and hops are the wrong unit for continental range. Only `aircraft` is bounded today. |
| **War of independence** | Deliberately not modelled. A movement past the threshold drags the holder's stability and that is the whole cost of refusing. Rebels raising armies needs a nation to raise them *for* — the thing the module exists not to invent until independence. |
| **Contested provinces** | Unrepresentable: one controller per region, one shape per oblast. Stated in the Modern Day module rather than forced. |
| **`invalidates_saves` used twice** | 19 and 20 only, pinned by a test. **A third use should be argued for, not assumed.** |
| **Collective defence / automatic second strike vs real models** | Still unproven live — neither fired in PT37 or PT38 (no pact was ever broken, no warhead spent). |
| **1936 decolonisation run** | Not run. 145 dependencies makes it the better stress test than 1972's 83, though 1972 exercised the mechanism adequately (three movements crossed on turn 12). |

---

## 9. Current build state

- **Tests:** 2545 passing, 1 deselected (`live`). Ruff clean over `src`, `tests`, `scripts`.
- **Schema:** **23**. Save-invalidating: **19 and 20 only** — 21 (`deterrent_boats`),
  22 (`support`), 23 (`independence`) are all additive.
- **Fingerprints — and this is the part a summary gets wrong.** Seed digests are **not**
  all unmoved:

  | era | seed | moved by |
  |---|---|---|
  | gathering-storm | `d89e1c8d9b3d2a29cfae8f279c4f5416` | `bd4f5da` — the 1936 sovereignty map. A real change to who exists, which is exactly what a seed digest is for. |
  | cold-war | `de872c31640a87daee6b7029440620fe` | **never moved** since the era shipped |
  | modern-day | `ceb1f190acd7f592159a66d0310f1dcb` | `bd4f5da` (Antarctica absent), then `da28531` (occupation regroup) |

  Built-world digests today: `c95cfe86…` / `86f63a1e…` / `8cbde8ea…`. These moved several
  more times — `support` and `independence` joining `_FINGERPRINT_TABLES` moves every one by
  the width of a table name even while empty. Harmless: digests are only ever compared
  within a schema version.
- **Cassette labels:** **PT37 and PT38 are ACTIVE** — both recorded against HEAD, and the
  pair is the before/after of the support cost, so PT37 is the control rather than a stale
  tape. **PT36 and older are HISTORICAL**: PT36 predates the air-across-oceans fix, the
  sovereignty maps and schemas 19–23, and misses against HEAD, correctly.

### Where I am least confident

1. **The decolonisation proxy** (§8) — a known-wrong model shipped with a flag on it.
2. **The support cost constants** — one live run of evidence. PT38 says they pose a real
   decision; PT38 is also n=1.
3. **`grant_independence` has never fired live.** The channel is wired and unit-tested end
   to end; no model has used it in a run, because 1972 gives momentum ~12 turns and the
   threshold is 70.
