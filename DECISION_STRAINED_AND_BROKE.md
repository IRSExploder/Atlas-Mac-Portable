# Strained and too poor to fix it — diagnosis and four options. STAGED for Alex.

**Nothing here is committed and nothing is recommended.** No constant moved, no catalogue
entry was added, no balance number was touched. The three candidate fixes below were
measured as monkeypatched arms inside one process and put back.

Rig: `scratchpad/broke_probe.py`. Evidence: `broke_probe_t60_*.json`, `step_broke*.txt`.
Five arms × three seeds × 60 turns, ordinary Modern Day, **one world id per seed** so the
arms are the same world.

---

## 1. What was claimed, and what turned out to be true

The recovery measurement surfaced this: under strain a government takes a certain
restorative or nothing, housing costs 25 against `_INVEST_RESERVE` of 1.4, so it needs **35
treasury** — and on ordinary seed 7 Russia ended at 2.65 stability having placed 2 orders in
60 months, while the old policy's land-reform gamble happened to land and carried it back
over the strain line.

**The first half of that is right and the second half framed the question wrongly.** The
trap exists. It is not created by the new policy, and the new policy is the arm with the
*fewest* governments permanently stuck in it.

| totalled over three seeds | old | **abstain** (ships) | narrow | reserve1 | cheap_relief |
|---|---|---|---|---|---|
| chooser calls made under strain | 1076 | 1143 | 1132 | 982 | **970** |
| trapped, and built nothing | 235 | 525 | 508 | 348 | 239 |
| stuck runs ≥ 6 turns | 19 | 34 | 28 | 27 | 21 |
| **…that never escaped** | **12** | **3** | 9 | 6 | 4 |
| world mean stability | 55.91 | 56.54 | 56.42 | **56.57** | 56.40 |
| world development | 1992 | 2212 | 2214 | **2218** | 2169 |

**The old policy had four times as many governments permanently stuck.** It simply had them
spending money while stuck — 235 "built nothing" against `abstain`'s 525 — which reads as
activity and was not recovery. On every seed the old arm ends with lower world stability and
about 10% less world development.

## 2. Being momentarily short is common, and it resolves itself

Treasuries climb in nearly every stuck run: +0.3 to +2.2 a month, and most governments do
save up and buy the housing. Of `abstain`'s 34 stuck runs of six months or more, **31
ended**. Holding the money is usually patience rather than a trap, and the probe reports the
climb rate and the months-to-afford beside every run precisely so the two are not confused.

## 3. The genuinely stuck are two kinds, and only one of them is a problem

**Micro-states, and being stuck costs them nothing.**

```
m_grd  51 turns, treasury 16.2 -> 31.4 (+0.30/turn, short 3.6), stability 52.1 -> 52.0
```

Grenada is poor and *settled*. Its stability does not move across fifty-one months of being
unable to afford a national housing programme, because nothing is wrong with it. A country
of that size not being able to fund a national programme is a fact about its size.

**Countries at war, and here it bites.**

```
ukr    12 turns, treasury 15.3 -> 34.0 (+1.71/turn, short 1.0),  stability 42.0 -> 23.3
rus    21 turns, treasury  8.0 -> 27.3 (+0.96/turn, short 7.7),  stability 14.3 ->  6.8   [old arm]
```

Upkeep drains the treasury while the war drains stability, so the climb is real and too slow
to matter. **This is a war-economy problem wearing a build-menu costume**, and the strongest
evidence for that reading is that the *old* arm produced the worst of these cases: Russia
21 months stuck, stability 14.3 → 6.8, while it had the gamble available the whole time.

## 4. The options, each with what it buys and what it costs

**Nothing here is free, and none of them is recommended.**

**(a) Leave it. Stuck-broke is a legitimate failure state.**
Supported by the table: `abstain` already has the fewest permanently stuck governments of
any arm, the second-best world stability and development within noise of the best. A country
that cannot afford the cheapest national programme *is* in trouble, and an engine that
always hands it a way out has removed the trouble rather than modelled it.
*Costs:* the war cases above stay as they are. Ukraine loses ~19 stability across twelve
months of holding its money.

**(b) Spend the buffer under strain — `_INVEST_RESERVE` → 1.0 while strained** (housing
needs 25 instead of 35). Adds no content and moves no balance number; it changes how much of
its own money a government in trouble may commit, which is the same *kind* of decision as
the rest of the recovery policy.
*Buys:* the fewest months spent under strain of any arm bar one (982), the best world
stability (56.57) and development (2218).
*Costs:* **twice the permanent traps of `abstain` (6 against 3)** — and it produces the one
pathology none of the other arms do, a treasury that *falls* while stuck:
```
rus  20 turns, treasury 26.8 -> 20.6 (-0.33/turn, short 14.4, NEVER at this rate)
```
The buffer exists to stop a government spending itself into arrears against its own upkeep,
and removing it under strain removes it exactly when arrears are most likely.

**(c) A cheap emergency work.** Measured with a hypothetical `relief_works` — 12 treasury,
2 turns, +2 stability, deliberately worse per point than housing so it is relief rather than
a better housing programme. It reaches the stuck band because 12 × 1.4 = 16.8 against
treasuries sitting at 20–30.
*Buys:* the least time spent under strain of any arm (970), and it nearly eliminates the
build-nothing months (239 against 525).
*Costs:* **about 2% of world development** (2169 against 2212) — governments buy relief
instead of accumulating toward housing and industry — and it does **not** reduce permanent
traps (4 against 3). It makes the common case more comfortable and does nothing for the rare
case that motivated the question.
*And it is a catalogue addition*, which is a balance judgment and is why it is measured here
rather than proposed.

**(d) A small guaranteed stabilisation while holding.** **Not measured**, and the numbers say
why it would be hard to size. To matter for the war cases it would have to beat their drain
— Ukraine loses ~1.6 stability a month while stuck — and 1.6/month is enormous beside
housing's +4 over four months. A dividend large enough to fix the case that matters would be
worth more than building anything, and one small enough to be safe would not touch it. If
this is wanted, it wants its own measurement with a stated target, not a number picked to
look modest.

## 5. What the numbers do not settle

Which of these is right is a design question about what a country in trouble should be able
to do for itself, and it is Alex's. The measurement's contribution is narrower and it is
this: **the trap is real, it is mostly temporary, the shipped policy already minimises the
permanent cases, and the permanent cases that remain are wars rather than budgets.**
