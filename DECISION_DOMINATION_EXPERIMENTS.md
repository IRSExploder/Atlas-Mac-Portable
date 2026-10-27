# Domination: the corrected experiments — STAGED for Alex

**Nothing here is committed.** No threshold, metric, constant or scoring rule is changed by
any of it. `_DOMINATION_SHARE` (0.25), `_DOMINATION_MARGIN` (2.0) and
`_DOMINATION_PROVINCES` (8) are read-only throughout, and this document **recommends no
committed metric**. It reports what the corrected instrument measured.

Run through `scratchpad/frozen_measure.py`, which depends on the corrected
`scratchpad/domination_measure.py` — per-nation review suppression, war-driven
mobilisation, ranked target selection, three nations per tier. The first study's figures
remain provisional; see the banner on `DECISION_DOMINATION.md`.

---

## (a) Fixed territorial weights — it fixes the direction and moves nothing else

Each province's value frozen at world creation, **numerator and denominator both**,
computed beside `standings` and never instead of it. Seed 2026, great tier, 60 turns.

| | owned | live `ground_share` | `frozen_share` | `frozen_of_map` |
|---|---|---|---|---|
| chn | 14 → 17 | 5.59% → 4.71% (**0.84×**) | 5.59% → 5.82% (**1.04×**) | 1.04× |
| ind | 10 → 11 | 3.34% → 2.92% (**0.87×**) | 3.34% → 3.73% (**1.12×**) | 1.11× |
| rus | 12 → 14 | 2.80% → 1.43% (**0.51×**) | 2.80% → 2.82% (**1.01×**) | 1.01× |

**The direction problem is real and freezing fixes it.** Under the live metric every one of
the three conquerors *loses share while gaining ownership* — Russia's halves while it takes
two provinces. Under frozen weights all three rise. `budget.ground_value` sums
`r.development`, development compounds everywhere at once, and the denominator outruns the
numerator; freezing removes exactly that.

**But it brings the victory no closer, and it makes one clause worse.** This is the finding
that was not expected and is the reason the full condition is evaluated rather than the
share alone:

| | share vs ground runner-up, live | frozen |
|---|---|---|
| chn | 4.71% vs 4.03% — **ahead** | 5.82% vs 6.06% — **behind** |
| ind | 2.92% vs 5.34% | 3.73% vs 6.08% |
| rus | 1.43% vs 4.56% | 2.82% vs 6.05% |

Freezing strips out the conqueror's **domestic development advantage**. A power that builds
at home gains live share without taking anything; measured on frozen territory it gains
nothing, so it is scored on ground alone — and there a power that began with more valuable
provinces leads. China goes from ahead of the ground runner-up to behind it. **The 2×
margin gets harder under the frozen metric, not easier.**

And 25% remains about four-fold away on every reading: the best frozen share any of the
three reaches in five years is 5.82%.

**So the honest summary is narrower than "freeze the weights and conquest works".** Freezing
makes conquest *legible* — the number moves the way the act does — and it does not make
Domination *attainable*. Those are two different problems and only the first is a metric
problem. Recommending nothing, per the brief.

### The 2× clause, which the first study did not evaluate

`scoring.assess` requires share ≥ 25% **and** share ≥ 2× the ground runner-up **and** ≥ 8
provinces held. All three are now reported at every checkpoint under all three metrics. The
eight-province clause is satisfied by every great power in every run at turn zero — it is
not what is binding, and it never was. The binding clauses are the share and the margin,
and the margin behaves *worse* under the proposed fix.

## What the corrected harness changed about the story

Worth stating beside the metric result, because it revises the first study's most quoted
conclusion. The original reported forced arms ending at **zero stability** and read that as
the cost of conquest. With mobilisation following the wars rather than pinned at general
call-up for sixty months:

| | months mobilised | stability |
|---|---|---|
| chn | 8 / 60 | 66 → 57 |
| ind | 10 / 60 | 56 → 27 |
| rus | 0 / 60 | 54 → **9** |

China conquers three provinces and ends comfortable. **Russia is the anomaly worth
flagging: it opened no wars at all, stood mobilised in no month, gained two provinces, and
still fell from 54 to 9.** Whatever is draining it is not conquest and not the call-up.
That is a loose thread, not a conclusion, and it is recorded here rather than explained.

---

## (c) Brokered cessions — RE-MEASURED on the fixed `suggested_terms`

> **The figures in this section were re-taken on 2026-08-27** after the
> `suggested_terms` correctness fix (`6baf12e`). The pre-fix numbers are kept below under
> *What the first measurement said*, because the difference between them is the point.
>
> Two of Alex's rules govern the rerun and both changed what the instrument does:
>
> 1. **Mediation is not gated on the bilateral suggestion.** The first probe asked brokers
>    about `suggested or minimal` — so whenever the bilateral search found something, the
>    brokered channel was handed the bilateral channel's package and measured its blind
>    spots twice. The one-province demand is now constructed and put to the brokers **every
>    month there is ground to cede**, independently of what `suggested_terms` returned.
> 2. **Three numbers, never one.** The fix makes `suggested_terms` return only packages
>    that pass `acceptance`, so its acceptance *rate* trivially approaches 100% while the
>    number of packages it finds at all may fall. A bare percentage is meaningless.

Seed 2026, mid tier, 60 turns. `acceptance` and `may_broker` are pure reads; the
committing arm additionally calls the shipped `mediate`, which necessarily changes the run
it measures (a signed peace ends the war), which is why its denominators are smaller.

### THE THREE NUMBERS

**Read-only arm** — nothing is signed, so column 3 is 0 by construction:

| | 1. OPPORTUNITIES<br>(months holding ground to cede) | 2. VALID PACKAGES FOUND<br>(cession-only bilateral search) | 3. ACTUAL SIGNED CESSIONS |
|---|---|---|---|
| tur | 4 | 0/4 (0%) | — (read-only) |
| **pol** | **15** | **0/15 (0%)** | — (read-only) |
| egy | 0 | — | — |

**Committing arm** — the same rig with `mediate` allowed to write:

| | 1. OPPORTUNITIES | 2. VALID PACKAGES FOUND | 3. **ACTUAL SIGNED CESSIONS** |
|---|---|---|---|
| tur | 2 | 0/2 (0%) | **0** |
| **pol** | **3** | **0/3 (0%)** | **2 provinces** |
| egy | 0 | — | **0** |

**Poland's two provinces changed owner at a brokered table in months where the
cession-only search found nothing at all** — signed at turn 5 and turn 57 against Russia,
China brokering both, on the independently constructed one-province package.

That is the whole argument for keeping the three numbers apart, demonstrated rather than
asserted: **column 2 was zero and column 3 was not.** An empty `suggested_terms` means *no
acceptable package was found by this cession-only search* — it searches cessions of held
ground and nothing else, no reparations, no recognition, no restoration, no mixed package.
It has never meant *no negotiated settlement exists*.

### Where the leverage actually is

| Poland, read-only arm | months | accepted bilaterally | would sign under mediation |
|---|---|---|---|
| the package `suggested_terms` proposed | 0 | — | — |
| the **independently constructed** one province | 15 | 0/15 (0%) | **15/15 (100%)** |

Bilaterally Poland can convert **none** of its fifteen ground-holding months. Under
mediation it could convert **all fifteen**. The mechanism is already built and shipped;
what the first study measured was the absence of it.

Turkey converts **0 of 4** on both channels, and that is not the same failure. Its one
permitted target owns a single province, so `_last_province` refuses — *"a state is not
dissolved at the table."* The rerun makes that visible as a refusal of the minimal demand
rather than as an absent one. Egypt has **0 turn-wars in sixty months**: it fails at
grievance, upstream of everything here. *Alex: both are legitimate strategy, not defects.*

### (b) ELIGIBILITY — brokers are not scarce

| | turn-wars | holding enemy ground | eligible, any turn-war | eligible when holding ground |
|---|---|---|---|---|
| tur | 26 | 4 | 4/26 (15%) | **4/4 (100%)** |
| pol | 22 | 15 | 15/22 (68%) | **15/15 (100%)** |
| egy | 0 | 0 | — | — |

In **every** month a mid power held ground worth ceding, a qualifying third party existed —
usually nine to seventeen at once. Best good offices seen: 0.25, the ceiling. Turkey's 15%
is the ratio of months it held ground at all, not a shortage of brokers.

### What the first measurement said, and why it moved

| Poland | pre-fix (buggy `suggested_terms`) | post-fix |
|---|---|---|
| opportunities | 16 | 15 |
| valid packages found | 9/16 | **0/15** |
| accepted bilaterally | 1/16 (6%) | 0/15 (0%) |
| would sign brokered | 15/16 (94%) | **15/15 (100%)** |

**The conversion story survives the fix and the diagnosis of it changes.** Pre-fix the
generator proposed nine packages and the engine then refused eight of them — the
propose-then-refuse defect. Post-fix it proposes none, because none of the packages it can
build out of held ground alone clears `acceptance` bilaterally. Either way the brokered
channel signs, and it signs on a package the bilateral search never produced.

The opportunity counts differ by one because these runs were taken on the `safe-recovery`
tree, where the recovery-policy fix changes what unattended governments build and therefore
the world Poland fights in. A pre-patch copy of the read-only arm is kept as
`cession_mid_2026_MASTER.json` for diffing.

### The defect found on the way — SINCE FIXED, and this is what it was

*Kept because the rerun above cannot be read without it.*

`suggested_terms` documented itself as building an offer *"stopping at what the loser's
position says it will bear — so the offer is by construction one that would be accepted if
made."* It was not. Measured at turn 11, Poland against Russia, one cession proposed:

> `rus is at +0.10 and 37/100 war-weary, so it will bear about 0.09; these terms cost it
> 0.14 — it fights on. Its nationalists are devoted and took 45% off what it could accept.`

The walk pre-computed its budget as `acceptance(terms=()).tolerance × (1 −
patriot_stiffening(humiliation=1.0))` and then filled to it; the real `acceptance` re-derives
the stiffening from the *actual* terms and took 45% off, which is more than the
pre-computation allowed for. **The engine proposed settlements its own acceptance model then
refused, in 8 of the 9 cases measured here.**

**Fixed on master in `6baf12e`.** The package now grows one province at a time and is kept
only while the whole package still passes the same `acceptance` call a settlement would
make, so the contract is a property of the construction rather than a hope. That is why
column 2 of the rerun reads 0 rather than 9: the generator stopped proposing what the
engine would refuse. **A higher acceptance rate after that fix would have meant only that
the generator stopped proposing anything difficult** — which is exactly why the rerun
reports opportunities and packages-found beside it instead of a percentage.

### And the finding that reframes the tier

**The three mid powers fail at three different stages, and only one of them is leverage.**

* **Poland — leverage.** Mediation solves it. Post-fix: the cession-only bilateral
  search finds **0** acceptable packages in 15 ground-holding months, mediation would
  sign in **15/15**, and the committing arm **actually moved 2 provinces**.
* **Turkey — target availability, not leverage.** Its refusals are
  *"none of these terms can be carried out"*, which is `review_terms` rejecting the term
  structurally rather than the loser refusing on tolerance. `may_declare` permitted Turkey
  exactly one target, **Israel, which owns one province**, so `_last_province` fires:
  *"a state is not dissolved at the table."* No quantity of good offices touches that.
* **Egypt — grievance.** Zero turn-wars in sixty. It never reached a war at all; every
  candidate refused at *"you have no grievance the world would recognise"*.

The first study collapsed all three into "mid powers gained nothing in six of six runs",
and it was measuring Turkey. Broadening the tier is what separated them.

**So: does mediation unlock mid-power annexation?** For a mid power that can get into a war
and take ground from a state large enough to survive losing a province — **yes, decisively.**
It does not manufacture a target, and it does not manufacture a grievance. Whether that is
the right shape is Alex's call; nothing is recommended here.

---

## (b) Cooldown, varied alone — a small, saturating effect

`warpath.COOLDOWN_DAYS` patched in-process and restored in a `finally`. **Nothing else
touched**, and deliberately not run alongside any unrest change: two knobs turned at once
produce a number attributable to neither. Both seeds, China, 60 turns, all three arms on the
*same world*.

| seed | cooldown | wars | owned | stability | cooldown refusals |
|---|---|---|---|---|---|
| 2026 | 730 / 548 / 365 | 1 / 1 / 1 | 15 / 15 / 15 | 22 / 22 / 22 | 2 / 2 / 2 |
| 7 | 730 | 2 | 16 | 22.8 | 3 |
| 7 | 548 | 3 | **17** | 19.6 | 7 |
| 7 | 365 | 3 | 17 | 19.6 | 7 |

**On seed 2026 the cooldown never binds** — the conqueror opened one war in sixty turns, so
shortening it changed nothing and all three arms are byte-identical.

**On seed 7 it binds, and the effect saturates.** 730 → 548 buys one more war and **one more
province**, costing about 3 stability. 548 → 365 buys **nothing at all**: the same three
wars, the same seventeen provinces, the same 19.6. Below roughly 548 days something else is
the constraint — `MAX_CONCURRENT_WARS = 2`, and the fact that a conqueror converting
occupation spends its turns proposing peace rather than declaring.

So across two seeds, halving the cooldown is worth **at most one province in five years**,
and the second halving is worth nothing. That is not a justification for changing it.

**The caveat that belongs with the number.** The oracle asks for the ground it already holds
before it opens anything new, so the declaration path runs on only a handful of turns. The
honest claim is *"the cooldown is not what is capping expansion under this conqueror's
ask-before-declare policy"* — not *"the cooldown does not matter"*. A rig whose conqueror
declared whenever permitted would consult it far more often, and that is a different
measurement rather than a better one.

*Process note.* The first cooldown run was **confounded**: the world id contained the
cooldown value, so the three arms were three different worlds and the experiment mixed the
constant under test with the map it was tested on — the same defect `zero_stability_probe`
records catching in itself. Caught before anything was concluded from it;
`scratchpad/step4_cooldown_CONFOUNDED.txt` is kept rather than deleted.
