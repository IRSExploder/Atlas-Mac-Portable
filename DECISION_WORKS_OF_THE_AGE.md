# Works of the Age — the six criteria, measured. STAGED for Alex.

**Status: STAGED. Nothing is wired into `scoring.assess`, no threshold is added, no schema
is migrated, no shipped catalogue is edited on disk.** The works are registered into the
project catalogue for the life of one process and put back in a `finally`, which is what
lets them travel the real `order_project`. This document recommends nothing.

Rig: `scratchpad/works_of_the_age.py`. Evidence: `works_positive_control.json`,
`works_2026_t100.json`, `step3_works_t100.txt`.

---

## 0. The positive control, which had to come first

**The previous run was INCONCLUSIVE and is discarded in full — including its "no passive
completion" result.** `orders.py:40` binds `CIVIL_PROJECTS` at import time, so the
membership test at `orders.py:736` never saw the registered works and **every work order
was silently refused**. Zero completions proves nothing about a design when orders cannot
execute; Alex's ruling, and it is right.

**The fix:** `registered` now rebinds *every* module that took its own import-time
reference — `projects`, `orders`, `production` — and asserts the rebinding took, so it
cannot fail quietly a second time. `reference.py:127` and `views.py:1265` import inside the
function and already saw the live tuple.

**The control: one work, end to end, through the real order path.**

```
work w_university for chn in r_chn_beijing, seed 2026
  GRANTED (fixture, not a finding): advanced_semiconductors, industry 55, treasury 5000
    [PASS] prerequisites satisfied
    [PASS] order ACCEPTED by the engine        (construction row, 240 days)
    [PASS] work COMPLETED                      (row gone by turn 9)
    [PASS] completion is PUBLIC                ("China completes National university in Beijing.")
    [PASS] effects APPLIED
  promised -> CHARGED BY THE COMPLETION ITSELF:
     region/development +5.0 -> +5.938 ;  nation/stability +3.0 -> +3.562
  (same fields over the whole run, growth and drift included: +7.126 / +2.562)
```

The applied figures are read off the `diff` rows carrying the completion event's own id,
not off the field deltas — nine turns of ordinary growth sit inside those, and the weaker
reading would have flattered the result. The ×1.1876 over the promise is China's leader
`development` modifier, which is how `_deliver_project` scales gains.

**The pipe is connected. Everything below is a measurement rather than an artefact.**

---

## 1. The six criteria — 100 turns (8.3 in-world years), seed 2026

| criterion | China (great) | Poland (mid) |
|---|---|---|
| 1 no seeded or passive completion | **PASS** — control finished 0; builder held 0 at turn 0 | **PASS** |
| 2 real opportunity cost vs control | **PASS** — +40 development, queue full 1× | pass, but vacuously |
| 3 prerequisites materially alter the build path | **FAIL** | **FAIL** |
| 4 plausible commitment-to-bid time | **FAIL** | **FAIL** |
| 5 detectable final bid | **PASS** — 3 public events for 3 completions | FAIL — nothing to see |
| 6 preparation retains value on failure | **PASS** — 2 works standing, still paying dev 9.0, stab 12.0 | FAIL |

```
chn  builder : 2 works standing across 2 of 5 families (institution, wellbeing)
               -- does not clear the bar of 3 families / 4 works
               ordered 5 works, 21 programmes; blocked: tech 3, industry 0, money 10, queue 1
     control : 0 works                       noprereq: 2 works across 2 families
pol  builder : 0 works
               ordered 0 works, 0 programmes; blocked: tech 112, industry 15, money 33, queue 3
```

## 2. What the numbers say that the PASS/FAIL grid does not

**Criterion 4 is the headline, and it is a design finding rather than a fixture fault.** No
bid was ever declared, by anybody, in 8.3 in-world years: China reached 2 of the 5 families
against a bar of 3, and nobody else reached any. The costs were calibrated **once**, against
the shipped catalogue's own scale (top shipped work 65 base; China industry 55 → ×1.92,
treasury 190) and against the 100-turn horizon. **They have not been lowered to make
anything pass, and per Alex's rule they will not be.** If the Works route is to be
completable inside a campaign, that is a decision about intended duration and intended
sacrifice, not a number to tune until the result turns green.

**Criterion 3 fails on outcome while succeeding on cost, and the distinction matters.** The
ungated arm reached the *same* 2 works in the *same* 2 families with **0 programmes
ordered**; the gated builder spent **21 programmes** getting to the identical place. So the
prerequisites are expensive and they are not *steering* — they delay the same build path
rather than changing which works are reachable. A gate that costs a decade and alters
nothing is decoration with a price tag. Whether the right answer is fewer, sharper
prerequisites or none is a design question and is left open.

**Poland fails upstream of construction entirely.** 112 tech-blocks, **0 programmes
ordered**, 0 works. It is not that a mid power cannot afford the stone; it cannot afford the
*laboratory* — treasury 40 against a programme's price, so the research chain never starts.
Costs scaling with industry was supposed to make the fraction-of-a-country comparable across
tiers; at the research gate it plainly does not. **On this evidence the Works route is
great-power-only, and it closes for mid powers before the first work is even orderable.**

**Criterion 5's second half is only half-answered, and is flagged rather than claimed.** The
lapse path is exercised whenever a bidder loses ground, but whether a rival would in
practice contest a bid is a question for a live opponent, not a scripted one.

## 3. What is not claimed

Nothing here says the route is good or bad. It says: works can be built and finish and pay
(control); they are not handed out (criterion 1); they cost something real (2); they keep
paying if the bid never comes (6); the prerequisites cost without steering (3); no campaign
of this length reaches a bid (4); and the mid tier is excluded at the research gate. The
decision is Alex's.
