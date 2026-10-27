# The recovery policy — measured old against new. MERGE CANDIDATE.

**What changed:** the policy an unattended government follows when it is under domestic
strain. **What did not change:** unrest, land reform's own success and failure effects, any
victory threshold, metric, cooldown or scoring constant. Alex's holds are intact.

Rig: `scratchpad/recovery_policy_measure.py`. Evidence: `recovery_policy_t60.json`
(ordinary), `recovery_policy_t60_conq.json` (conqueror), `step2_recovery*.txt`.

---

## 1. The defect

`_RESTORATIVE` led with `land_reform` — the catalogue's **only** `risk_from_legitimacy`
gamble. It pays **+9 stability** when it holds and costs **−8 stability and −3 legitimacy**
when it does not, and *legitimacy is the number the next attempt's odds are read off*. So:

* the government that reached for it first was, by construction, the one with the least
  legitimacy and therefore the **worst** odds;
* every failure took three more points of the thing that sets the next roll;
* and it sat *ahead* of housing (+4, certain) and health (+3, certain) in the list.

## 2. The per-attempt probability — stated, and not derived from outcomes

The engine rolls `success_chance(legitimacy) = min(0.9, max(0.15, legitimacy / 100))`. The
expectation is `p × 9 × worth − (1 − p) × 8`, with gains scaled by the leader's
`development` modifier and the failure not scaled, because that is exactly how
`_deliver_project` applies them. **Break-even is `p = 8 / (8 + 9·worth)`** — 0.4706 at
worth 1.0, and **0.4834 for Russia**, whose government is `hardliner` (0.95 on
development). Russia starts Modern Day at legitimacy 44, so its opening attempt is
`0.44 × 9 × 0.95 − 0.56 × 8 = −0.72` stability.

**These are the odds the engine actually used**, recorded by wrapping `uncertain` and
filtered to Russia's own `construction` ids. The realized failure count is a *sample* and is
never used as the probability.

| run | Russia's own attempts | mean p | **E[stability] per attempt** |
|---|---|---|---|
| ordinary, seed 2026 | 5 (4 failed) | 0.225 | **−4.17** |
| ordinary, seed 7 | 1 (0 failed) | 0.435 | **−0.61** |
| ordinary, seed 101 | 7 (1 failed) | 0.337 | **−2.27** |
| conqueror, seed 2026 | 10 (4 failed) | 0.315 | **−2.65** |
| conqueror, seed 7 | 11 (8 failed) | 0.199 | **−4.61** |
| conqueror, seed 101 | 11 (8 failed) | 0.316 | **−2.62** |

**Every gamble the old policy took, in all six runs, was negative expectation.** Its mean
odds never once reached the 0.4834 break-even. Where the old policy wins below, it wins a
bet it should not have taken.

*(A first pass reported 112–115 attempts and a positive expectation. That instrument was
wrong: it matched every `project:` key and was counting the whole world's land reforms. The
figures above are Russia's alone.)*

## 3. The result, in the configuration the doom loop was found in

**Conqueror arm** — the corrected conquest harness's `forced` mode, per-nation review
suppression, the configuration `stability_ledger` read the 54 → 9 collapse in. Both arms of
each seed share one `world_id`, because `uncertain` takes `world_id` and arms in separate
worlds are separate worlds — the confound that invalidated the first cooldown study.

| conqueror | stability end | legitimacy end | **lowest stability** | what it built |
|---|---|---|---|---|
| 2026 old | **65.6** | 39.8 | 40.9 | land reform ×10, workshops ×5 |
| 2026 new | 58.6 | **46.1** | **51.0** | housing ×4, workshops ×8 |
| 7 old | 30.5 | 11.8 | 22.5 | land reform ×14, workshops ×2 |
| 7 new | **58.0** | **46.7** | **52.5** | housing ×3, workshops ×9 |
| 101 old | 11.1 | 13.5 | 11.1 | land reform ×14, workshops ×2 |
| 101 new | **57.5** | **41.4** | **50.9** | housing ×4, workshops ×7 |

**The clearest single fact in the study: `leaders._DANGER_BELOW` is 50.0, and in the
conqueror configuration the new policy's stability never went below 50.9 on any seed, while
the old policy's fell to 40.9, 22.5 and 11.1.** The old policy spent the study inside the
band where coup risk starts; the new one never entered it. On seeds 7 and 101 the new policy
ends **+27.5** and **+46.4** stability ahead.

**Coups: zero in every arm of every run, old and new.** The original ledger's coup (turn 41)
was in a world with a different `world_id` and therefore different dice; this study cannot
reproduce that individual event and does not claim to. What it shows is the *risk* — time
spent below the danger line — collapsing, not a coup being averted.

## 4. Where it is worse, stated plainly

**Ordinary arm, seed 7 is a real regression: 54 → 2.65 under the new policy against 54 →
34.65 under the old.** The mechanism is legible and is not the gamble being good. Russia's
one land reform there landed at p = 0.435; the **+8.55** lifted it back over
`_STRAINED_BELOW` into settled play, where a richer revenue base compounded. Housing's
**+3.80** did not clear the band, so Russia stayed strained and too poor to order anything —
2 orders in 60 months. The old policy won that seed on a coin flip it had no business
taking, and the new policy has no answer to a government that is simply broke.

Across all six paired runs: final stability favours the new policy in 3, the old in 3.
**Minimum stability — the quantity coup risk is computed from — favours the new policy in
5 of 6.** Final legitimacy favours it in 4 of 6.

**Honest read: the patch helps, decisively in the configuration that produced the doom loop,
and it is not uniformly better.** It removes a systematically negative-expectation reflex and
keeps a strained government out of the danger band; it also removes the lucky escape that
reflex occasionally bought, and one seed shows what that costs. The remaining failure —
a strained government too poor to afford the cheapest certain restorative — is a *separate*
defect and is not addressed here.

## 5. The two variants, and why `abstain` ships

Alex's instruction admits two readings, and both were measured as arms:

* **`abstain`** *(what ships)* — under strain: a certain restorative, or a gamble whose
  expectation is positive, or **nothing at all**, holding the money and reporting it as a
  `bank`. Alex's words: *"ABSTAINING (build nothing that turn) can be better than worsening
  the crisis."*
* **`narrow`** — drop only the gamble; keep the industrial fall-through exactly as it was.

**They are indistinguishable for Russia.** Identical builds and identical stability on all
three conqueror seeds (58.6/58.6, 58.0/57.1, 57.5/57.7) and all three ordinary ones. The
only consistent difference is legitimacy, where `abstain` is higher in **3 of 3** conqueror
seeds (46.1 vs 41.1, 46.7 vs 37.0, 41.4 vs 34.1). Since the evidence does not separate them
on the quantity in question and does mildly favour `abstain` on legitimacy, the variant that
matches the instruction ships.

The abstention band is narrow by construction: housing costs 25 and the reserve is 1.4, so
a strained power holds its money only below 35 treasury — and below 28 the old policy could
not have afforded anything either.

## 6. What is preserved

`land_reform` is **not removed**. `_RESTORATIVE_GAMBLES` keeps it, gated on
`expected_stability` being positive, so a government the country believes in still breaks up
the great estates — the player may always order it, and an unattended one takes it when the
odds are genuinely on its side. `success_chance` is now one definition with two callers, so
the policy that weighs the bet and the dice that settle it cannot read different odds.
