# Review handoff — the war loop, domestic constituencies, and what followed

For Codex. Everything below is on `master`, hashes are real, and the done/open split is
honest. Two batches are covered plus the overnight work that came after them.

**Tree state at handoff:** schema 30, `atlas release-audit` green on all 8 gates,
**3097 passing / 1 skipped**, ruff clean.

---

## 1. The war loop (Codex gameplay #1)

Built in six layers, each additive and each rewindable.

| layer | what it does | commit |
| --- | --- | --- |
| aims | structured per-belligerent war aims; `annex` tested against the **owner**, not the holder | `821f4d2` |
| ledger + exhaustion | casualties charged from the diffs actually written; five bands | `ab3893d` |
| leverage | ground / weariness / force / aims, antisymmetric by construction | `f7a11de` |
| tuning | three dials measured against a real war rather than argued | `d650a29` |
| terms + acceptance | seven term kinds, enforceability review, engine-only acceptance | `32459f0` |
| wiring | `sue_for_peace` reachable by a model; the free-white-peace fix | `895c213`, `a1128d4` |
| armistices | ceasefires that can be broken, through the existing breach machinery | `a4684e6` |
| facts | what the world remembers, minted from events | `d340644` |

**The split that matters.** The model proposes a term set; the engine refuses what cannot
be carried out (`review_terms`), and the engine alone decides acceptance (`acceptance`).
Neither model has a vote on whether a peace is signed, for the same reason neither decides
a battle.

**Live proof.** PT42, Cold War, real models: `gpt-5.6-terra` playing West Germany sued for
peace and ceded six provinces; the Soviet Union's overreach was refused by the engine;
separate peace held (three treaties for three wars); rewind reproduced turn 4 exactly.

**Tests:** `test_a_war_can_have_a_point.py` (25), `test_a_war_costs_something_to_go_on_with.py`
(21), `test_a_peace_that_says_what_it_costs.py` (38), `test_a_truce_that_can_be_broken.py` (24).

### Open in this batch

- **Layer 5's domestic half** folded into the constituencies batch and is done there.
- **Post-war consequences beyond terms** — occupation regimes, reconstruction, revanchism as
  a standing state — are not built. The facts layer is the seam they would hang off.
- `world_fact` has **no model channel**. It is engine-minted only, and the "anything funnel"
  that would let a described action become a remembered fact is unbuilt. Recorded as such in
  the engine-only door test rather than left implicit.

---

## 2. Domestic constituencies (Codex gameplay #2)

Five groups per nation — army, industry, people, patriots, establishment — chosen because
they give *different answers to the same decision*. Universal rather than per-regime: a
regime taxonomy does not exist in the schema, and a per-era set makes a world unreplayable
across eras.

| piece | what it does | commit |
| --- | --- | --- |
| foundation | the store, lazy rows, seeded from the nation's own numbers | `976542c` |
| C4 | the nationalists move peace acceptance | `ec40a03` |
| C1 | coup risk reads the army and the establishment specifically | `f0396df` |
| mappings | the six readings that were arguable, decided and written down | `11d632a` |
| C3 | strikes cost revenue; hostile industry slows the yards | `21977bc` |
| C2 | the removal channel: election / revolution / coup | `839d6ae` |
| C5 | the inspector screen | `2f73fdf` |

**Everything is engine-derived.** `constituency` is `ENGINE_ONLY`: a power that could write
its own standing could buy the army's devotion instead of earning it.

**Numbers worth checking.** C4: at the canonical lost war, patriots at 72 sign both
provinces away and at 88 the same offer is refused. C1: on the three seeded maps only
1 / 6 / 6 nations out of 74 / 148 / 242 are touched, and the base rate is unchanged. C3:
aggregate drag at seed is 0.02% / 0.00% / 0.00% of world revenue with no yard slowed.

**Tests:** `test_a_leader_with_somebody_to_govern.py` (21),
`test_the_day_the_constituencies_start_to_bite.py` (49),
`test_the_screen_that_shows_who_you_have_to_govern.py` (13).

### The one decision waiting on Alex

**The C2 accountability proxy is a guess.** Nothing in the schema knows what kind of state a
nation is: every government on all three shipped maps reads `came_to_power = 'seeded'`, and
226 of 243 nations share one stability and one legitimacy. The proxy is the **leader's
title** — President / Prime Minister / Bundeskanzler on one side, Fuehrer / Duce / General
Secretary / Supreme Leader on the other — two tuples in `leaders.py`, no schema, one line to
overrule. It names 6 / 12 / 13 accountable governments per era; 63 of 74 in 1936 are titled
"the government" and default to force.

### Open in this batch

- **C3 is built for the people and industry only.** The army (slower mobilisation,
  understrength units), patriots (war aims that cannot be dropped) and establishment
  (repudiated signatures) halves of the original table are unbuilt and recorded in
  `DESIGN_CONSTITUENCIES.md`.
- **Delegation / standing instructions** (Codex's later item) is untouched.

---

## 3. Intelligence foundation (Codex gameplay #3) — `d35b4dd`

Foundation only, by instruction. Networks per (viewer, target), estimates computed from real
state with noise scaled by the network.

**The hole it closes.** `visibility._band` took a rival's *true* `military_strength`, divided
by the viewer's own and named the ratio: coarse, but exact, identical for every observer, and
free. A landlocked minor read Berlin exactly as well as Britain did.

**The guarantee.** `Estimate` has no `true_value` field, and the band is deliberately **not
centred on the truth** — a symmetric interval hands the real number to anyone who averages
the ends. The load-bearing test renders a whole briefing and asserts the rival's true figures
are nowhere in it. **Tests:** `test_a_service_that_reports_what_it_believes.py` (22).

**Open:** discovery, counterintelligence, attribution tiers, exposure events, covert proxy
support and intelligence-sharing clauses are all designed and unbuilt — `DESIGN_INTELLIGENCE.md`,
with the two seams flagged (`support.py` writes deniable shipments as PUBLIC today;
`commitment_term.clause` is a closed CHECK that cannot be widened without a table rebuild).

---

## 4. What the overnight pass found

### Fixed

| severity | finding | commit |
| --- | --- | --- |
| HIGH | **A covert operation announced itself through the politics.** A successful covert gambit writes a SECRET `unrest` event; `unrest` is a mapped constituency reaction; the layer wrote the movement as a PUBLIC diff citing its own PUBLIC narrative event. INFO-002 could not catch it — the cited cause really was public — so the secret was laundered through one hop. Movements now take the narrowest scope of any cause. | `b97596b` |
| MEDIUM | **A siege of your own ground**, caught live by PT43: South Vietnam held Quang Tri and besieged it at 70% then 55%. Opening one was already refused; nothing closed one that *became* self-besieging, and `captured` requires `held != attacker`, so it could never end. | `b97596b` |
| MEDIUM | **A latent determinism hole**: the `because` written to a fingerprinted column was chosen by whichever reaction arrived first, and three feeding queries were `GROUP BY` with no `ORDER BY`. | `b97596b` |
| MEDIUM | **Air, naval and nuclear battles named nobody**, so a whole air campaign moved no politics and could not be attributed. All four two-sided battle events now carry actor / defender / beneficiary. | `dd76b2b` |
| LOW | **Having nothing was perfectly observable**: multiplicative noise on a true zero gives a band of 0-0, so disarmament was the only perfectly visible act in the game. | `b97596b` |

### Probed and sound

Exhaustion bounds under absurd casualties; leverage antisymmetry at the extremes; a broken
armistice really resuming the fighting; every advertised capability having a handler; no
dead enum members anywhere.

### Not fixed — needs a decision

**The 110-turn determinism campaign** (`d3266b5`, harness `scratchpad/century.py`).
Determinism passed: **110 turns reproduced exactly, twice over**. Two things did not stop:

1. **Economy 9.0x and industry 12.1x over nine game-years** (~27% compound annual growth).
   The economy *drift* is a converging pull toward a ceiling and is correct; what compounds
   is the ceiling itself, `industry * 1.4 + development * 0.43`, both bought with a treasury
   the economy feeds. No damping term anywhere in that loop.
2. **Founders' stability 56.5 → 40.5 over eighty turns** with no wars, no occupied provinces
   and nobody bankrupt. Traced on the United States, 58.00 → 46.04: every applied diff is a
   construction charge. `autonomous_investment` develops every unattended nation every turn
   and `_choose_development` picks from exactly three projects — steelworks, workshops, rail
   — which are the three whose stability effect is negative. Housing +4, health +3 and land
   reform +9 are never picked, and **nothing anywhere restores stability**. Since C1 makes
   coup risk climb below 50, a century of peaceful building is a century of rising coup risk
   caused by the building.

Both are one-line changes with map-wide consequences, which is why neither was guessed at.

### Also landed

- **PT44** re-recorded against HEAD, replays **12/12 exact, no misses** (`2ba8cbc`).
  PT37/PT38 retired as historical: the war-aims and peace channels legitimately changed the
  world-sim schema. **PT37 was the control half of the support-cost pair and re-recording it
  would have destroyed what made it a control — the pair no longer has a live half.**
- `scratchpad/tape_replay.py`, generalised from `pt36_replay.py`. Finding worth knowing: the
  world a tape was recorded against is *part of the tape* — every recorder carries its own
  `build`/`arrange`/`dress`, PT38's differs from PT36's by one hashed column
  (`nation.tracked_reason`), and replaying against the wrong one mismatches on turn 1 with no
  misses, which reads exactly like an engine divergence.
- **Property test over the diff→rewind pipeline** for all six new tables (`6dfe2af`):
  twelve seeded random histories, rewound to four earlier turns each, digests compared.
- **A standing guard instead of a cleanup diff** (`a2381d9`): nothing was provably dead, so
  every closed CHECK value must now be written or listed as reserved with a reason.
- **The supply map layer** (`eaf7bc1`): supply already decided fights and could not be looked
  at.

---

## 5. Where to look first

If you have time for three things:

1. `engine/peace.py` — `acceptance` and `patriot_stiffening`. The bounds are the design.
2. `engine/constituencies.py` — the seven arguable readings, each a module-level
   `Reaction` carrying the reading it was given. (It said "the `_ARGUED` block, six
   judgements"; there is no such block, and there are seven. See
   `DESIGN_CONSTITUENCIES.md` §4.) Overruling one is a one-line edit.
3. `engine/intelligence.py` — `estimate_of`. Whether the displaced-centre construction
   really is unrecoverable is the question the whole layer rests on.

## 6. Not built, and known

- Discovery / counterintel / attribution / exposure / covert proxy / sharing clauses.
- C3's army, patriots and establishment friction.
- Post-war occupation regimes and reconstruction.
- Delegation and standing instructions.
- Map overlays beyond supply: nuclear reach, diplomatic attitude, war aims and planned
  fronts were queued and are unstarted.
- UX: cross-entity search, watchlist, draft-order persistence, unit-composition tooltips.
