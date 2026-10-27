# Release gate: check 5 — a committed conqueror must out-campaign the control

**Status.** Integrated, **check-5-pending as a standing gate**. The strategy layer is merged
(`ece103e`); this is the check it must keep passing, run per seed, before anything calls it
validated.

The layer was merged as an *integration* decision rather than a validation one. It has
passed this check once, at the revision below, and the point of writing the spec down is
that "passed once on somebody's machine" and "passes as a gate" are different claims — the
same distinction the geometry packs taught us the hard way.

---

## The claim being gated

> A government that **holds** a committed conquest objective campaigns more than one that
> re-picks its objective from current fitness every turn.

That is the whole premise of the persistent strategy layer. If it stops being true, the
layer is decoration and should be reverted or fixed immediately.

**It is deliberately not a claim that conquest *works*.** It does not: owned provinces never
change, every province taken is occupied, occupied ground erodes, and the committed arm's
share of world ground climbs and comes back down. That is the victory frame's problem and is
tracked separately. This gate is only about whether commitment produces sustained campaigning.

---

## Exact configuration

| | |
|---|---|
| revision first passed | `78ae4d4` — "The conquest trend measured its own denominator" |
| merged at | `ece103e` |
| harness | `scratchpad/check5_gate.py` (per seed) or `scratchpad/persistence_probe.py` (batch) |
| seeds | **2026, 7, 101** |
| turns | **40** monthly turns (≈ 3 years 4 months of world time) |
| tier | **great** — nation `chn` |
| role | **conquest** |
| arms | `held` (pre-committed plan, kept) vs `turn_to_turn` (re-picks every turn, no stored plan) |
| schema | 45 |

Only the great tier is a clean test. At mid and small tiers the annual review correctly talks
the specialist out of a plan its position cannot support — which is the layer working, and
not a measurement of persistence.

Command:

```bash
python scratchpad/check5_gate.py 40 2026
```

---

## Pass condition

For **each seed independently**:

1. `held` declares **more wars** than `turn_to_turn`; and
2. `held` ends holding **more provinces** than `turn_to_turn`.

The gate passes when every seed passes. A single seed failing is a finding, not noise —
report it and either fix or revert, per Alex's condition.

---

## The result it passed on (`78ae4d4`)

```
              held                    turn_to_turn
  seed 2026   2 wars, 19 provinces    0 wars, 14 provinces
  seed 7      1 war,  20 provinces    0 wars, 14 provinces
  seed 101    2 wars, 17 provinces    0 wars, 14 provinces
```

Secondary observations, recorded so a future run can be compared rather than merely judged:

* the committed arm's world-ground share peaks at 6.88% / 6.85% / 5.79% and decays to roughly
  where the control ends — expected, and the ground-does-not-stick finding;
* owned provinces are **14 in both arms in every seed** — nothing is ever annexed;
* objective switches: **0** in both arms.

---

## Why it runs per seed

The batch form timed out twice at ninety minutes and **discarded every seed's work both
times**, because one slow world took the whole run down with it. Two hours of compute bought
nothing. Per-seed runs with their own timeouts mean a slow seed costs that seed only, and
partial results are still results.

---

## Two hazards this harness has already had

**A global monkeypatch under concurrency.** The `turn_to_turn` arm suppresses
`strategy.review_world` by rebinding it on the module. Run concurrently with `held` worlds
under `asyncio.gather`, a held world could execute turns with the patch in place and have
its annual reviews silently swallowed. The arms run in separate phases now; a per-seed
runner keeps that property by construction.

**A specialist premise that leaked.** In `held` mode the engine's annual review still runs
and may override the pre-committed plan. That is correct behaviour and it is why this gate is
scoped to the great tier, where fitness and the specialist premise agree.
