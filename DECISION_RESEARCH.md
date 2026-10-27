# Decision: research is reachable, out of surplus, and nobody banks

**Status: MERGED.** Approved by Alex with two constraints, both of which ship as measured:
**surplus-only research** (`production._RESEARCH_SURPLUS = 1.0`) and **banking off**
(`production._BANK_ABOVE = inf`). No victory threshold, metric or scoring constant is
touched by any of it.

Every figure below was **re-measured on the fixed chooser**, after the zero-stability bug
was found. The pre-fix evidence is kept beside it as `research_measure_t*_*_PREFIX.json`
so the two can be diffed rather than trusted.

---

## 1. What was broken

**Nothing refused research; nothing ever reached it.** `order_research` has been correct
since it landed. The unattended chooser walked an ordered list of public works and took the
first thing it could afford, and a workshop is always cheaper than a programme — so across
every measured run in this project's history the `knowledge` score component sat at
**0.000 for every power in the world**. Measured on the tree as it stood: 30 turns, three
Modern Day seeds, **0 technologies and 0 researchers in all three**.

The seventh capability found working, tested and unreachable, after support, mediation,
reinforcement, garrison, war aims and cession.

## 2. What ships

**The engine half** is one order a turn, as it was. An unattended power may spend its one
order on a programme instead of a workshop, through the same `order_research` door the
player and the tracked agents use, charging the same treasury and taking the same queue
slot.

**Research is bought out of surplus, or not at all.** A power begins a programme only when
it could still have afforded the public work it would otherwise have laid down. The queue
takes one thing a turn either way, so the money is money that was going to sit idle — and
it is what makes "knowledge moved" and "development fell" separable rather than two
readings of the same spending.

**Saving is off.** Its own arm measured it at −4.0% of the world's development for nothing
at all: knowledge still 0.000 everywhere. A government that waits is a government that did
not build, and over sixty turns the compounding beats the better factory it was waiting
for. It ships as a mechanism that exists, is tested, and is one constant from being on,
because the number that was *measured* is 0.55 and the number nobody has measured is a bar
high enough that the wait is short.

**The briefing half** comes from the discoverability sweep's own findings: every
programme's line ends in what it buys; programmes behind an unmet prerequisite are named
with what opens them; the task block examples a programme; and a government holding a
committed strategy gets the list ordered by what that route is judged on.

## 3. The measured trade, on the fixed code

Three Modern Day seeds, the scripted oracle unchanged, both arms the same code with one
constant out of reach.

| | 60 turns | 100 turns |
|---|---|---|
| development, baseline → shipped | 11021.7 → 10835.1 | 22570.5 → 22085.2 |
| | **−1.69%** | **−2.15%** |
| technologies | 0 → **65** | 0 → **306** |
| powers researching | 0 → 13 | 0 → 41.3 |
| mean world score | −0.053% | **+0.256%** |
| score spread (sd) | 0.07356 → 0.07414 | 0.07985 → 0.08310 |

**Development does not recover — it is a real, sustained trade**, and slightly wider at 100
turns than at 60. A public work compounds: industry widens the queue, which builds more,
which raises the economy. A programme's effect is a one-off bump. Spending on science is
genuinely the worse *development* investment and the tree does not catch up.

**On the measure the world is actually scored by it is net positive, and it crosses over
between the two horizons.** At 60 turns the mean world score is −0.05%, a dead heat with
the programmes not yet delivered; at 100 it is **+0.26%**. Knowledge runs 0.000 → 0.0357 at
a weight of 0.08 against wealth 0.522 → 0.511 at a weight of 0.22. The trade is worth
making and it takes about seventy turns to become so.

**No degenerate convergence** — the condition this was staged on. The field's score spread
is *wider* with research on, not narrower, at both horizons. 102 technologies across about
42 powers per seed, where every previous measurement recorded zero, everywhere.

So the honest statement of the requirement is narrower than "development does not fall" and
stronger than it sounds: **knowledge moves, the spending buys more score than it costs, and
total development pays 2.15% for it.** Research is allowed to have an opportunity cost. It
does not have to beat a factory on every measure.

## 4. The contamination audit, and what it actually found

The figures above were re-measured because the chooser read stability through
`float(nation["stability"] or 100.0)`, and `0.0 or 100.0` is `100.0` — so a government at
exactly zero stability was scored as settled. Both new doors are gated on `not strained`, so
in principle a collapsed government could have opened a laboratory or banked its treasury,
and that would have inflated exactly the counters this dossier reports.

`scratchpad/zero_stability_probe.py` counts it directly. The `before` arm does not check out
an old commit: the two expressions differ on one input and only one, so substituting `100.0`
for a true stability of exactly `0.0` in front of the fixed function **is** the old function.

| horizon | chooser calls | at exactly 0.0 | → research | → banked | → restorative | technologies |
|---|---|---|---|---|---|---|
| 60 turns, before | 27,070 | **0** | 0 | 0 | 0 | 67 |
| 60 turns, after | 27,070 | **0** | 0 | 0 | 0 | 67 |
| 100 turns, before | 38,466 | 11 | **0** | **0** | 0 | 311 |
| 100 turns, after | 38,462 | 8 | **0** | **0** | 3 | 311 |

**No contamination in the measured runs.** Stated as the empirical finding it is: across
these six runs, no call at zero stability ever opened a programme or banked a treasury, and
the technology counts are identical between the arms. This is *not* a claim that the defect
could never affect a research measurement — `autonomous_investment` plays only powers that
are neither player nor tracked, which is why the count came out low here, but that is an
explanation of these runs rather than a guarantee about every government that might reach
the floor in future.

**What the fix did change** is worth recording, because it is the bug's real cost: under the
defect all eleven floor-calls chose **nothing at all** — a government that could afford a
land reform but not a workshop was being offered only the industrial list and built nothing.
Under the fix, three of them reach for something restorative.

## 5. What the rerun moved

| | pre-fix | post-fix |
|---|---|---|
| t60, both arms | — | **identical**, per seed and per fingerprint |
| t100 development | −2.17% | −2.15% |
| t100 mean score | +0.233% | +0.256% |
| t100 score sd | 0.07983 → 0.08310 | 0.07985 → 0.08310 |
| technologies, 60 / 100 | 65 / 306 | 65 / 306 |

At 60 turns every per-seed figure and every world fingerprint reproduces exactly. At 100
turns two baseline seeds and one shipped seed drift — by 1.1, 16.0 and 0.11 parts in about
22,800 — which is the three restorative choices above, propagating. **0.02 percentage points
on a 2.15-point effect.** The conclusions are materially unchanged, and the corrected
figures are the ones quoted throughout this document.

Note that the drift is larger in the *baseline* arm than in the shipped one, which is the
right way round: the baseline has no programme to spend on, so more treasuries sit idle and
more governments arrive at the floor with something restorative newly within reach.

## 6. What is not settled here

* Whether a world **should** pay 2.15% of its development for a knowledge component is a
  balance decision and is Alex's. This dossier reports the price; it does not argue it.
* **Saving** is off at a measured bar of 0.55 and untested at a high one. Whether a world
  should have governments that save is a design question rather than an arithmetic one.
* The knowledge component's **weight** (0.08) is untouched and was not measured against
  alternatives.
