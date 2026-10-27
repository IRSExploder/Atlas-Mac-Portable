# Domination re-measurement — staged for Alex

> ### ⚠ THE NUMBERS BELOW WERE TAKEN WITH A FLAWED RIG — 2026-08-27
>
> A review by Alex and Codex found four places where this instrument measured **its own
> policy** rather than the engine's ceiling. The rig is corrected in
> `scratchpad/domination_measure.py`; **the study has not been re-run**, so every figure in
> this file is provisional and the conclusions drawn from them are not yet earned.
>
> 1. **The strategy review was suppressed for the whole world**, not for the conqueror.
>    `forced` replaced `strategy.review_world` with a stub, so twenty-three bystanders were
>    frozen on their turn-zero plans too. The ceiling was measured against a world that could
>    not respond to being conquered. *Corrected:* only the specialist's review clock is
>    advanced; verified at 16 other governments still reviewing on the same pass.
> 2. **Mobilisation was pinned at general call-up for all sixty months**, war and peace
>    alike, at 0.70 stability per reference month — about 42 points over the study. That is
>    most of why the forced arm ended at zero stability, and it was the rig's doing rather
>    than the cost of conquest. **"Sustainable expansion" cannot be estimated from a country
>    that was never allowed to stand down.** *Corrected:* the footing follows the wars, and
>    `months_mobilised` is now reported so the difference is visible rather than assumed.
> 3. **Targets were selected alphabetically.** The shape of every campaign was a fact about
>    the alphabet. *Corrected:* reachable first, then weaker, then larger prize — with
>    `may_declare` still the only authority on whether a war is permitted.
> 4. **"Great vs mid" was China vs Turkey** — two nations carrying a claim about two tiers.
>    *Corrected:* three of each (`chn`/`ind`/`rus`, `tur`/`pol`/`egy`), chosen to vary the
>    shape of the problem and not only its size. A 12-turn smoke already shows Poland taking
>    a province, which a Turkey-only mid tier reports as "mid powers gain nothing".
>
> A fifth defect was found while correcting the fourth: the per-stage attribution matched
> refusals on *"regard"* and *"grounds"*, and the gate says **"you have no grievance the
> world would recognise"** — so every grievance refusal fell through to `other` and named
> nothing. Egypt's campaign fails at that gate and nowhere else, and the rig could not say
> so. It can now.
>
> **The corrected instrument is ready for the corrected experiments.** Those are step 4's,
> together with the fixed-territorial-weights and cooldown arms, and no threshold, metric or
> constant is touched by any of it.

**Status: STAGED. Nothing committed.** No threshold, metric, or constant has been changed.
`_DOMINATION_SHARE` (0.25), `_DOMINATION_PROVINCES` (8) and `_DOMINATION_MARGIN` (2.0) are
read-only throughout. The decision is Alex's.

Rig: `scratchpad/domination_measure.py`. Artifacts: `scratchpad/dom_{tier}_{seed}.json`.
**12 runs** — 2 tiers × 3 seeds × 2 arms, 60 turns each = 5.0 in-world years, per seed with
its own clock.

---

## The two arms

**`forced`** — committed to conquest and never allowed to reconsider. The mechanical ceiling.
**`chosen`** — adopts conquest through the strategy layer and may abandon it at review.

| tier | seed | mode | gained | /yr | revs | share start → end | world growth |
|---|---|---|---|---|---|---|---|
| great | 2026 | forced | 3 | 0.60 | 0 | 5.59% → 3.66% | 1.86× |
| great | 2026 | chosen | 2 | 0.40 | 0 | 5.59% → 3.45% | 1.85× |
| great | 7 | forced | 3 | 0.60 | 0 | 5.59% → 3.53% | 1.92× |
| great | 7 | chosen | 3 | 0.60 | 0 | 5.59% → 3.80% | 1.79× |
| great | 101 | forced | 3 | 0.60 | 0 | 5.59% → 3.37% | 2.01× |
| great | 101 | chosen | 3 | 0.60 | 0 | 5.59% → 4.25% | 1.60× |
| mid | all 6 | both | **0** | 0.00 | 0 | 1.76% → ~0.9% | ~2.0× |

**Zero reversals in all twelve runs.** Occupied ground read 0 at every great-tier checkpoint,
because everything taken was converted to ownership by treaty rather than held. Domination
Option A works exactly as designed.

---

## Per-stage failure attribution

**Great tier — no stage in the chain fails.** Campaigning, occupation, proposal, acceptance,
cession and retention all worked: two asks, two signatures, three provinces, nothing lost.
The caps sit downstream of conquest:

* **war cooldown** blocked declarations on **8–23 of 60 turns** (median ~11). A third of the
  campaign spent legally unable to open anything.
* **unrest**: five of six runs ended below stability 10; four reached zero.

**Mid tier — a different and more interesting failure.** `proposal: held ground and never
asked (2–5× nothing bearable)`. Turkey *did* occupy 1–3 provinces every run, and
`suggested_terms` returned nothing every single time: it never had the leverage for any
cession to be bearable. **A mid power can take ground and can never convert it.** The
conversion path is gated on decisive victory, which a mid power does not achieve.

---

## The chosen-arm question: appropriately sensitive, not stubborn

Seed 101 is the clean case. **Both arms gained the same 3 provinces**, but `chosen`
recovered — stability 66 → 32 → 47 → 55.7 → **70.8** → 56.4 — while `forced` sat at 0.0. It
was cooldown-blocked 23× against forced's 10×: it paced itself, and got the same conquest
with an intact government.

Seeds 2026 and 7 show partial easing (10 vs 21 blocks) without full recovery. So the layer
is **responsive but seed-dependent**, and it never abandoned conquest outright. Verdict: not
insufficiently sensitive to domestic cost, and worth revisiting only if a future run shows it
holding a plan through a collapse it could have avoided.

---

## Decision output 1 — is 25% attainable? **No, and not at any horizon.**

**Horizon assumed: 10 in-world years (120 turns)** — double the longest run measured. *Alex's
to confirm.*

Sizing from the live map: 319 land provinces, world ground **value** 906. China holds 50.6
across 14 provinces = 3.62 per province. 25% of world value = 227 ≈ **63 provinces**, so
**+49** from a standing start. At 0.60/year that is **~82 in-world years**.

**But the horizon is not the problem, and this is the finding that matters.** The world's
total ground value grows **1.84× every five years** — about **13% a year, compounding** — as
every nation develops. A conqueror gaining 0.6 provinces a year grows its own holdings by
about **3.9% a year**.

> **The denominator outruns the numerator by roughly nine points a year.** Share falls while
> ownership rises, in **twelve runs out of twelve**. 25% is not distant; it is *receding*.

No threshold value fixes this. A 10% bar recedes the same way — it is the *metric* that is
broken, not the number on it.

---

## Decision output 2 — the sustainable envelope

**Great tier: 0.567 owned provinces per in-world year, σ = 0.075** (five runs at 0.60, one at
0.40). Zero reversals: what is taken is kept.

**Mid tier: 0.00/year, structurally** — six of six, blocked at the proposal stage.

The rate is **cooldown-bound, not conquest-bound**: 730 days between declarations permits
~2 wars per 5 years, and each yields ~1.5 provinces.

Time-to-threshold at the measured rate, *ignoring* denominator growth (i.e. best case):

| target | provinces needed | years |
|---|---|---|
| 10% | +11 | ~18 |
| 15% | +24 | ~40 |
| 25% | +49 | ~82 |

With denominator growth included, **none of the three is ever reached.**

---

## Decision output 3 — recommendation: move the metric off world-share

Share is the same structural trap as Hegemony's ratio, and for the same reason: it is a
comparison against a quantity that grows faster than the thing being measured. Hegemony was
reclassified rather than retuned once that was understood; the same reasoning applies here.

**Recommended: measure conquest against the nation's own starting position.**

> **Domination = owned ground grown to N× the nation's holdings at world creation, held for
> the sustained period.**

Grounded in the measurement: 14 → 17 in five years is **1.21×**. Extrapolating the envelope,
a ten-year campaign reaches ~1.4× and a twenty-year ~1.9×. So **2.0× owned ground over
roughly 10–15 years** is demanding, reachable, and cannot be manufactured by the rest of the
world getting poorer — or denied by it getting richer.

**Alternative if a world-relative flavour is wanted:** share of world ground **as measured at
world creation** — a fixed denominator. Same anti-mean-reversion property.

**Recommended against for now:** strategically-weighted regions. Larger build, and the
measurement says the defect is the denominator, not the weighting.

**This recommendation is incomplete in one respect, and it should not be adopted without
resolving it:** at 0.567/year, 2.0× takes ~25 years for a great power and is *unreachable at
any multiple* for a mid power, which converts nothing at all. A self-relative metric fixes
the mean-reversion but not the rate, and the rate is set by the two caps below.

---

## Decision output 4 — the 8-province floor: keep, unchanged

Assessed on its own terms, as asked. It solves a **small-map degeneracy**: on a three-province
map two provinces satisfy any ratio, which is why `_two_powers()` in the tests was grown to
nine regions. On the shipped 319-province map it binds on nobody.

It is a floor against absurd worlds, not a conquest target. **If the metric moves to a
self-relative multiple it becomes *more* necessary**, since 1 → 2 provinces is 2.0× and
plainly not a domination. Recommend keeping 8 and revisiting only if a future map makes it
non-binding.

---

## Two caps that belong in the conversation (flagged, not changed)

**Is Domination winnable at all under the current regime?** On the current metric, no. On a
self-relative metric, only if these are discussed:

1. **The 730-day war-declaration cooldown** blocks a third of turns and is the binding
   constraint on the entire envelope. It, not combat, sets 0.567/year.
2. **Unrest** collapsed four of six great-tier governments to zero stability inside five
   years. A conqueror that keeps winning loses the country.

Seed 101's `chosen` arm shows both are survivable *together* when a nation paces itself —
same gains, stability recovered to 56 — but that is one seed in three.

**And the mid-tier result deserves its own line:** conquest is currently a great-power-only
route. A mid power can fight, occupy, and never convert. Whether that is intended is a design
question, not a measurement one.
