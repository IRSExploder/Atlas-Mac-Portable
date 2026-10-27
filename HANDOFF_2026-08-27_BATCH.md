# Session handoff — 2026-08-27: the briefing batch, the conquest experiments, the builder prototype

**Read this first.** Everything below is either *merged and green* or *staged and
recommending nothing*. No victory threshold, metric, scoring constant or unrest constant
was changed anywhere in this session. The only engine changes that merged are two
correctness fixes and one feature Alex had already approved.

---

## 1. What merged to master

| commit | what | gate |
|---|---|---|
| `6e297fe` | **Zero-stability fix.** `float(nation["stability"] or 100.0)` read the falsy `0.0` as missing, so a government at exactly zero stability was scored as *settled*. Explicit `None` check; regression test at 0.0 / 0.01 / 54 plus the `None` complement. | suite 4292 pass |
| `3c46ead` | **Discoverability audit + 3 fixes** — the capability menu says what an act costs including "no direct cost"; a patron is told it may ship deniably (`covert_support`, named in 0 of 96 briefings); mediation joins the visibility guard. | — |
| `a832d7e` | **Research reachable**, surplus-only, banking off. | — |
| `4ea21f5` | **Gate 1 evidence** + `DECISION_RESEARCH.md`. | — |
| `2910ee5` | Merge of the briefing batch, with tape `pt73-cold-war`. | **audit 8/8** |
| `0141533` | **Conquest harness correction** (scratchpad + dossier banner only). | — |
| `1de0022` | Staged instruments for steps 4 and 5. | — |
| `6baf12e` | **`suggested_terms` correctness fix** — see §4. | suite 4315 pass |

**Release audit on the merged tree (through `1de0022`): 8/8 PASS**, suite 4312 passed /
1 skipped / 0 failed, ruff clean, `pt73-cold-war` replays 12 matched / 0 mismatched / 0
missed. **That is the last green audit, and it is the state master is in.**

**The `suggested_terms` fix (`6baf12e`) merged after one detour.** Its first audit returned
7/8 with the suite red, and the failure turned out to be the documentation guard
`test_every_test_count_a_doc_quotes_is_the_count`: two review handoffs promise that
`test_a_peace_that_says_what_it_costs.py` holds 35 tests, and the new invariant made it 38.
Nothing to do with the fix's logic. Corrected, suite re-run **4315 passed / 1 skipped / 0
failed**, ruff clean. The guard earned its place — a stale promise nobody would otherwise
have noticed for months.

**A full `atlas release-audit` has NOT been re-run since that merge.** The suite gate is
green and the other seven gates were green on the immediately preceding tree, but the
audit itself should be re-run at the start of the next session for a clean 8/8 on record.

### The tape

`pt73-cold-war` replaces `pt72`. 12 turns, **106 calls, 9 of them failures** — all nine
`codex exited with code 1` on `tracked_agent`, a transient CLI fault. Eight retried
successfully; **one key failed all three attempts, so one tracked power was silent for one
turn.** Nothing suspicious across twelve turns, rewind to turn 10 matches. That failure
count is in `INDEX.json` rather than averaged away, so the next re-record has something to
be measured against.

### Gate 1 — the research contamination audit

Alex's condition: the research figures were taken on the buggy chooser, and both new doors
(research, banking) are gated on `not strained`, so a collapsed government could in
principle have opened a laboratory.

| horizon | chooser calls | at exactly 0.0 | → research | → banked | → restorative |
|---|---|---|---|---|---|
| 60t before / after | 27,070 | **0 / 0** | 0 | 0 | 0 |
| 100t before | 38,466 | 11 | **0** | **0** | 0 |
| 100t after | 38,462 | 8 | **0** | **0** | 3 |

**No contamination in the measured runs.** Stated as the empirical finding: across these
six runs no call at zero stability opened a programme or banked a treasury, and technology
counts are identical between arms. **This is not a claim that the defect could never affect
such a measurement** — `autonomous_investment` plays only powers that are neither player
nor tracked, which explains why the count came out low here, but that is an account of
these runs and not a guarantee about every government that reaches the floor later.

**The rerun:** t60 reproduces *exactly* (per-seed development, technologies, researchers,
and every world fingerprint). t100 moved by 0.02 points on a 2.15-point effect — development
−2.17% → **−2.15%**, mean score +0.233% → **+0.256%**, technologies 306 → 306. Conclusions
materially unchanged; `DECISION_RESEARCH.md` quotes the corrected figures.

**What the fix did change** is the bug's real cost: under the defect all eleven floor-calls
chose *nothing at all* — a government that could afford a land reform but not a workshop was
offered only the industrial list. Under the fix, three reach for something restorative.

---

## 2. The conquest harness, corrected (staged)

The first Domination study measured *the rig's policy* in four places.
`DECISION_DOMINATION.md` now opens with a banner marking its figures provisional.

1. **The strategy review was suppressed for the whole world** — `S.review_world = lambda: []`
   froze all 24 governments, so the ceiling was measured against a world that could not
   respond. Now only the specialist's review clock advances; verified at 16 other
   governments still adopting plans on the same pass.
2. **Mobilisation was pinned at general call-up for 60 months**, war and peace alike — about
   42 stability points, most of the collapse it then reported as the cost of conquest.
3. **Targets were chosen alphabetically.** Now reachable → weaker → larger prize.
4. **"Great vs mid" was one nation each.** Now `chn`/`ind`/`rus` and `tur`/`pol`/`egy`.

A fifth, found while fixing the fourth: the attribution matched refusals on *"regard"* and
*"grounds"*, and the gate says **"you have no grievance the world would recognise"** — so
every grievance refusal fell through to `other` and named nothing.

---

## 3. The Domination experiments (staged — `DECISION_DOMINATION_EXPERIMENTS.md`)

### (a) Fixed territorial weights — **Alex: APPROVED as the accounting model, kept staged**

| | owned | live `ground_share` | frozen |
|---|---|---|---|
| chn | 14 → 17 | 5.59% → 4.71% (**0.84×**) | 5.59% → 5.82% (**1.04×**) |
| ind | 10 → 11 | 3.34% → 2.92% (0.87×) | 3.34% → 3.73% (1.12×) |
| rus | 12 → 14 | 2.80% → 1.43% (**0.51×**) | 2.80% → 2.82% (1.01×) |

Freezing fixes the direction — under the live metric every conqueror *loses share while
gaining ownership*. **But it brings 25% no closer and makes the 2× runner-up clause
harder**: China goes from ahead of the ground runner-up (4.71 vs 4.03) to behind it (5.82 vs
6.06), because freezing strips out its domestic-development advantage. Freezing makes
conquest **legible**, not **attainable**.

*Not to be merged alone while 25% is unreachable — it is decided together with the threshold
and the runner-up margin.*

### (b) Cooldown — **Alex: DO NOT CHANGE**

Two seeds, same-world arms. On **seed 2026 it never binds** — one war opened in sixty
turns, and 730 / 548 / 365 are byte-identical. On **seed 7 it binds and SATURATES**:

| seed 7 | wars | owned | stability |
|---|---|---|---|
| 730d | 2 | 14 → 16 | 22.8 |
| 548d | 3 | 14 → **17** | 19.6 |
| 365d | 3 | 14 → 17 | 19.6 |

730 → 548 buys one more war and **one more province** for about 3 stability; 548 → 365 buys
**nothing at all**. Below roughly 548 days something else is the constraint —
`MAX_CONCURRENT_WARS = 2`, and a conqueror converting occupation spends its turns proposing
peace rather than declaring. Across both seeds, halving the cooldown is worth **at most one
province in five years**. Contradicts the first study's "cooldown-bound" headline.

**Caveat on the record:** the oracle asks for held ground before opening anything new, so
the declaration path runs on a handful of turns. The honest claim is *"not what is capping
expansion under this conqueror's ask-before-declare policy"*, not *"the cooldown doesn't
matter"*.

*Process note:* the first cooldown run was **confounded** — the world id contained the
cooldown value, so the three arms were three different worlds. Caught before reporting;
`step4_cooldown_CONFOUNDED.txt` is kept.

### (c) Brokered cessions — **Alex: KEEP as the mid-power path**

**(b) Eligibility** — a qualifying broker existed in **100%** of the months a mid power held
ground (14–17 powers at once, best good offices 0.25). Brokers are not scarce.

**(a) Conversion** — Poland **1/16 (6%) bilateral → 15/16 (94%) brokered**, and the
committing arm **signed 2 real cessions**. But Poland ends owning 4 in *both* arms: the
treaties changed *how* it got there, not *how much* — the marginal gain in that seed was
zero.

**Three mid powers fail at three different stages, and only one is leverage:**

* **Poland — leverage.** Mediation solves it.
* **Turkey — target availability.** Its one permitted target is Israel, which owns one
  province, so `_last_province` refuses: *"a state is not dissolved at the table."*
* **Egypt — grievance.** Zero turn-wars in sixty.

*Alex: Turkey's and Egypt's constraints are legitimate strategy, not defects. Do not "fix".*

---

## 4. `suggested_terms` — a correctness defect, fixed on its own

**The contract was false.** The docstring promised terms the loser *"would actually sign"*;
**8 of 9 offers were then refused by the engine's own `acceptance`.**

**Root cause.** `acceptance` decides on `net = demanded + shortfall`, where
`shortfall = max(0, position) × EXPECTATION_FROM_LEVERAGE` — *the loser's own expectation of
concessions*. A power giving up ground in a war it is otherwise **winning** expects to be
given something. `suggested_terms` compared only what the terms *cost*, against a tolerance
it re-derived by hand, and never accounted for the shortfall.

> `rus is at +0.10 and 37/100 war-weary, so it will bear about 0.09; these terms cost it
> 0.14 — it fights on.`

**The fix** grows the package one province at a time and keeps it only while the whole
package still passes the same `acceptance` call a settlement would make, so the contract is
a property of the construction rather than a hope.

**The invariant**, parametrized over two arms:
> With unchanged state, terms returned by `suggested_terms` must be accepted when
> immediately offered by the named winner to the named loser.

*Test-design note:* the first version of this test **passed on the buggy code**. In the
two-province sandbox one cession costs 0.5 against a tolerance of 0.08, so both
implementations decline and never disagree — the defect only lives where a cession is
*cheap*. The fixture now gives the ceding power 13 provinces (1/13 = 0.077, just under the
0.08 floor), leaving exactly the room the shortfall overflows. Verified to fail on the old
implementation and pass on the new.

---

## 5. Works of the Age (staged prototype)

Option B with Alex's five changes: families rather than a globally-exclusive shelf, breadth
across them, real technology prerequisites, effects shipped code reads so completion is
useful without victory, and a public bid whose contest window resolves for nobody when
matched. Nothing exhaustible: a rival contests by **matching**, never by taking something
off a shelf.

Registered into `projects.CIVIL_PROJECTS` for the life of the process only, so the works
travel the real `order_project` — real queue, real treasury, real public event, real effects.
Three arms: the specialist, a **control** on the ordinary oracle, and an **ungated** arm with
prerequisites removed.

### STATUS: NO VALID MEASUREMENT WAS OBTAINED. Three fixture faults, all mine.

**Nothing in this section is a design finding.** Per Alex's rule, an invalid fixture is not
economic infeasibility, and all three faults below are the former. **Do not read "zero works
completed" as evidence about the route.**

**Fault 3, and the one still open — the works never reach the engine.**
`orders.py:40` does `from .projects import CIVIL_PROJECTS` **at import time**, binding the
original tuple into the `orders` module namespace. The `registered` context manager rebinds
`projects.CIVIL_PROJECTS`, which `orders` never re-reads — so the membership test at
`orders.py:736` never sees the works and **every order is silently refused**. Proved by
tracing the construction queue: after twenty turns China's queue holds `uncrewed_systems`
(a research programme, which resolves fine) and `steelworks` (from the base oracle), and
**not one Work of the Age**, against 21 order intents in the 100-turn run.

`reference.py:127` imports *inside* the function and would have seen them; `orders.py` is
the early binder. **The fix is one line: `registered` must patch `orders.CIVIL_PROJECTS`
as well** (and re-check any other import-time binding — `production.py:42` binds early too,
but `project_by_id` reads the live global, so `order_project` itself is fine).

Until that is done there is **no evidence at all** about criteria 3–6. What the runs did
establish is only criterion 1, and weakly criterion 2:

| criterion | verdict | evidence |
|---|---|---|
| 1 no seeded/passive completion | **HOLDS** | control finished 0 works in every arm; no builder held anything at turn 0 |
| 2 real opportunity cost vs control | **partial** | China's builder ends 7 development *below* its control and hit a full queue twice — but that cost was paid on **research programmes and refused orders**, not on works, so it is not yet the intended measurement |
| 3 prerequisites materially alter the path | **no evidence** | ungated arm ordered the same 21 and completed the same 0 |
| 4 plausible time to bid | **no evidence** | no work ever completed, so no bid could form |
| 5 bid detectable / disruptable | **no evidence** | 0 public completions because there were 0 completions |
| 6 preparation retains value | **no evidence** | nothing standing to retain |

**The first full run was INVALID and is reported as such.** All six arms produced zero
completions — and the tell was that the *ungated* arm produced zero too. Two faults:

* **prerequisites named the wrong era's catalogue** (interwar `mass_production`/`welding`/
  `radar`; Modern Day ships `uncrewed_systems`, `advanced_semiconductors`,
  `integrated_air_defence`, `modular_shipbuilding`, `hypersonic_strike`, …), so they were
  unsatisfiable by construction — 203 tech-blocks and zero programmes ordered;
* **the works were priced against nothing** — the shipped catalogue tops out at 65 base, I
  had written 95–170, and with industry scaling China would pay 182–326 against a treasury
  of 190.

Recalibrated (bases 38–84, floors 3–28, era-correct prerequisites including one two-step
chain) and re-run. **Only criterion 1 was established by the invalid run** — the control
finished zero works and no builder held anything at turn 0.

Two engine facts the prototype had to discover, both worth knowing if this is ever built:

* **there is no `done` column on `construction`** — a finished work *deletes* its row and
  writes an event naming the project and region by *display* name, which a condition asking
  "does this power still hold that work" cannot use. The prototype keeps its own ledger by
  diffing the table each turn.
* `technology` is keyed `tech_id`, not `id`.

---

## 5b. Two epistemic rules Alex set — carry these forward

### On the Works prototype: an INVALID FIXTURE is not ECONOMIC INFEASIBILITY

The first run was an **invalid fixture** — prerequisites named the interwar catalogue in a
Modern Day world, so they were unsatisfiable by construction. That is a broken instrument
and is correctly discarded.

**"Too expensive to finish in a campaign" is a different thing, and it may be the finding.**
The rule Alex set: calibrate cost and prerequisites to the **intended campaign duration**
and the **intended sacrifice** — what a builder should have to give up — and then *stop*. Do
**not** keep lowering costs until something completes. If a sensibly-calibrated Works still
cannot be finished inside a campaign, **preserve and report that** as a design finding about
the route, not as a fixture to be tuned until it passes.

The recalibration performed here was made against the shipped catalogue's own scale (top
work 65 base, China industry 55 → ×1.92, treasury 190; Poland industry 13 → ×1.22, treasury
40) and against the 100-turn horizon — i.e. to intended duration and sacrifice, once. Any
further reduction would need that justification, not a desire for a green result.

### On the conversion rerun: three numbers, never one

The broker conversion rates in §3(c) were measured on the **buggy** `suggested_terms` and
must be re-measured. Three distinctions to keep apart:

1. **A VALID suggestion is not a COMPLETE one.** An empty `suggested_terms` means *"no
   acceptable package found by this cession-only search"* — **not** *"no negotiated
   settlement is possible."* The function searches cessions of held ground and nothing else:
   no reparations, no recognition, no restoration, no mixed package. Empties must never be
   reported as "no deal exists".
2. **Do NOT gate mediation on bilateral suggestions.** A package refused bilaterally may be
   exactly the one a broker unlocks. Broker-aware packages must be searched
   **independently** of whatever bilateral `suggested_terms` returns — otherwise the
   mediation arm inherits the bilateral arm's blind spots and measures them twice.
3. **Report three separate numbers: OPPORTUNITIES, VALID PACKAGES FOUND, ACTUAL SIGNED
   CESSIONS.** This is the critical one. **A higher acceptance rate after the fix could
   simply mean the generator stopped proposing anything difficult.** The fix makes
   `suggested_terms` return only packages that pass `acceptance` — so its acceptance rate
   trivially approaches 100% while the *number of packages it finds at all* may fall. "94%
   accepted" is meaningless without "of how many opportunities, and how many valid packages
   were found." The §3(c) figures in this handoff are stated as ratios over ground-holding
   months precisely so the denominator travels with them, but the post-fix rerun needs all
   three explicitly.

## 6. Open threads for Alex

1. **The Domination decision itself** — frozen metric **+** 25% threshold **+** 2× runner-up
   margin, made together. The evidence is in §3. Nothing is recommended.
2. **`suggested_terms` propose-then-refuse** — fixed (§4), but note it is a plausible partial
   explanation for the first study's low conversion, *independent of mediation*. **The broker
   conversion rates in §3(c) were measured on the buggy code and want re-measuring.**
3. **Russia's stability collapse** — 54 → 9 with zero wars and never mobilised. A reconciling
   ledger now exists (`scratchpad/stability_ledger.py`): every write with its amount,
   subsystem, reason event and text, summing exactly to the fall. Early signal on the
   ordinary world: **one failed land reform cost −8.00 in a single write** against −5.20 of
   accumulated drift. Note the link to §1 — `_RESTORATIVE` leads with `land_reform`, which is
   a **risky** project, so a strained government reaching for the restorative list can lose 8
   stability when it fails. **No unrest tuning until the full attribution is in.**
4. **Mid-tier expansion shape** — Poland can annex with a broker; Turkey and Egypt are
   blocked upstream by design. Whether that distribution is the intended one is a design
   question.

## 7. Where things live

* `DECISION_RESEARCH.md` — the research decision, post-fix figures, contamination audit.
* `DECISION_DOMINATION.md` — the first study, banner-flagged provisional.
* `DECISION_DOMINATION_EXPERIMENTS.md` — the corrected experiments (a)/(b)/(c).
* `scratchpad/domination_measure.py` — the corrected conquest rig.
* `scratchpad/frozen_measure.py` — frozen weights, cooldown, brokered cessions.
* `scratchpad/works_of_the_age.py` — the builder prototype.
* `scratchpad/zero_stability_probe.py` — the contamination counter.
* `scratchpad/stability_ledger.py` — the attribution ledger.
* `scratchpad/*_PREFIX.json` — pre-fix research evidence, kept for diffing.
