# Backlog

Work that is real, scoped enough to act on, and deliberately not being done yet. An item
here is a decision to defer, not a note that something might be nice.

---

## Discoverability: every action should trace to a briefing that offers it

**Status:** open. Not scheduled. Do **not** fold into a feature branch — this is its own
piece of work.

**The problem is a contract, not a run of bad luck.** Six times now a capability has been
found that existed, worked, was tested, and was invisible to the governments that could have
used it:

| # | capability | how it was found |
|---|---|---|
| 1 | nine channels surfaced by the first discoverability audit | audit |
| 2 | `doctrine_shift` — a schema channel no briefing mentioned | audit |
| 3 | the victory conditions themselves — 150 turns, zero victories, because *nobody was ever told what they were playing for* | play-test |
| 4 | the support/patronage channel | audit |
| 5 | mediation's gate — a briefing that listed nothing taught staffs the channel was dead | build |
| 6 | `cede_region` — annexation has always worked, and only the **loser** was ever shown it, and only above 90 exhaustion | victory-frame scoping |

Number 3 is the one that should have settled it. A whole scoring layer read as a balance
problem and was nothing of the kind. Number 6 is the same shape and cost a full round of
"Domination's mechanics are broken" analysis before the mechanism turned out to be present
and merely unreachable.

**The invariant to enforce:** every action a government may legally take should trace to at
least one briefing condition that tells an eligible government the action exists, and why it
is or is not available to it right now.

**Shape of the work, roughly:**

* enumerate the legal action surface — the `DiffBatch` answer properties, plus the engine
  entry points a tracked power can reach through them;
* enumerate what the briefing renders, per block, with the condition that gates each;
* a test that fails when an action has no offering condition, in the shape of
  `test_every_bounded_column_write_in_the_engine_is_clamped_by_its_writer` — a structural
  sweep with a recorded exemption list, so the next gap fails in CI rather than in a
  play-test six months later;
* and the "why not" half, which matters as much: a block that goes silent when the answer
  is no teaches a staff that the channel does not work, rather than that the moment is
  wrong. Mediation and the new cession block both learned this.

**Why it is deferred:** it touches every briefing block and would collide with the
victory-frame work in flight. It also wants doing once, properly, rather than as a rider on
whatever feature happens to be open.

---

## The builder-victory gap

**Status:** open, explicit, and now the largest known hole in the victory frame. Not to be
solved by forcing a ratio -- that was tried and abandoned.

**The finding.** Development has no *earned* ending. Measured across three seeds and three
tiers: a specialist that genuinely out-developed its control in **every single arm** -- the
only power in any run to research anything, knowledge 0.214 / 0.143 / 0.071 against 0.000
everywhere else -- **lost on ratio in every tier that mattered**. The decomposition shows
why: `d log(ratio)` went negative for the superpower in both arms while its own score rose,
because the runner-up rose faster. The field converges toward the same component clamps, so
the ratio is mean-reverting; it does not merely fail to reward building, it punishes it.

Hegemony was the only condition that might have rewarded development, and it rewards
relative position in a converging field, which is a different thing. It has been reclassified
as a termination condition rather than tuned down, because tuning the bar would have made a
bad measure cheaper rather than making development pay.

**What is owed.** Concert, territorial expansion and development should each eventually give
a genuinely earned ending. Two of the three now do something: concert is reachable and has
been won, conquest converts and holds ground since Domination Option A. Development has
nothing. **One live route is a temporary state, not the long-term frame.**

**Explicitly out of scope for a quick fix:** no ratio tuning, no unclamping components (that
would move Concert, which is the working control), no folding it into the Domination
threshold work. It wants a real mechanism and its own design pass.

---

## The default AI never researches

**Status:** open, small, discoverability class -- see the item above about the interface
contract.

Research works. Modern-day offers seven programmes (`uncrewed_systems`,
`advanced_semiconductors`, `integrated_air_defence`, `hypersonic_strike`,
`modular_shipbuilding`, `autonomous_undersea`, `warhead_modernisation`), cold-war and
gathering-storm their own, and a nation that orders them learns five in twenty-four turns and
carries its knowledge component from 0.000 to 0.357.

Nobody orders them. Across every run measured, in every era, the knowledge component sits at
**0.000** for every nation -- 8% of the score untouched because no briefing ever puts a
research programme in front of a government as something worth buying.

**Recorded against a mistake worth not repeating:** this was first reported as "modern-day
has no research", from reading a refusal that listed *civil works* as though it enumerated
the research menu. It does not; they are separate catalogues. The gap is that nobody buys
them, not that they do not exist.

---

## Check 5 as a standing release gate

**Status:** spec recorded in `RELEASE_GATE_CHECK5.md`, runner at
`scratchpad/check5_gate.py`, seeds being swept. See that file rather than duplicating it
here.
