# Overnight session — 2026-08-27

Continuation of the previous session's queue. Four milestones, one merged to master and
three staged — plus a fifth taken afterwards, also merged. Master is at `9766c52`, clean,
and every release gate is green on it.

**A simulated turn now costs 8.420s against the 13.769s this queue started at — 39% off,
in two independently proved steps.**

**Nothing was touched that was frozen:** no victory threshold, no scoring weight, no
Concert constant, Hegemony's classification unchanged, `strategy.fitness_of` unchanged. No
protectorates, no new victory condition, no Concert rebalancing.

---

## Where things stand

| branch | head | state |
|---|---|---|
| **master** | `9766c52` | merged; full release audit **PASS**, all eight gates |
| `discoverability-audit` | `fd9c9b0` | **staged** — audit + three small fixes; cassette gate red, wants a re-record |
| `research-discoverable` | `ca7ed19` | **staged** — measured; cassette gate red, wants a re-record |
| `builder-victory-dossier` | `e9503ab` | **staged** — analysis only, no code |
| `domination-remeasure`, `victory-frame` | unchanged | left alone, as instructed |

---

## 1 — MERGED: the adjacency graph is built once per world

`3fe93e9` (merge), `2368d11` (change), `9e18ecd` (baseline).

`air._neighbours` read the whole adjacency table into a graph **10,867 times in twelve
turns**, because supply, trade and the air pass each rebuilt it on every one of their own
inner calls — `trade.route_is_open` alone runs 5,700 times and built the entire map each
time to ask one question about one lane. The map has not moved since the world was seeded
and nothing in the engine can move it: `region_adjacency` is written once, by
`create_world`, and is not a diff target, so a rewind cannot touch it either.

| | baseline | with the cache |
|---|---|---|
| sim | 158.88s ± 2.98 | **109.10s ± 3.31** — a **31.3%** cut |
| total | 185.01s ± 3.27 | 135.41s ± 3.66 |
| a turn | 13.240s | 9.092s |
| peak memory | 313.9 MB | 313.9 MB |
| fingerprint | `51fe0d42bfd1` ×3 | `51fe0d42bfd1` ×3 |

Determinism is proved rather than assumed: the same digest on all six repeats, before and
after, and every repeat fell against its counterpart (163.1/156.7/156.8 → 113.8/106.8/106.7).

**Why it beat the profile's predicted 15%:** the sqlite execute count barely moved
(2,676,472 → 2,665,606, exactly the 10,867 queries removed). One query returning 1,280 rows
is cheap; fourteen million rows of Python is not.

**The risk was the key, not the caching.** Every sandbox world in the suite is called
`"sandbox"` and dozens exist at once in separate in-memory databases, so a cache keyed on
world id alone would have answered one with another's map — silently, as supply reaching
provinces it should not. Three independent guards:

* the connection's identity is part of the key;
* **the entry holds that connection by strong reference**, which is what makes keying on
  `id()` sound at all — a freed connection's address can be handed to a new object, and an
  entry that keeps its connection alive can never be looked up by an impostor;
* **seeding a world forgets that world on every connection.** `create_world` says so
  itself, beside the only `INSERT INTO region_adjacency` there is.

The cache is bounded at eight (which is what keeps the strong references honest) and hands
out a read-only proxy over tuples. Six tests; the two that matter are the collision ones.
`nuclear.deliverable_regions` had a third copy of the same query and now shares this one.

**Gate on the branch:** packs-exist, packs-verify, schema-parity, determinism, invariants,
scenario-audit, cassettes (1/1 replayed, no re-record needed), suite 4253 passed / 1 skipped,
ruff clean. **VERDICT: PASS.** The suite itself came in at 1407s against master's 1469s.

**Next target, same shape, not taken:** the ocean set. `air._oceans`, logistics and trade
each rebuild "every stretch of water on this map" per call, and `region.kind` does not move
either.

---

## 2 — STAGED: the discoverability matrix (audit only)

`5186189` (audit), `fd9c9b0` (three fixes). Artifact:
<https://claude.ai/code/artifact/39fe21f6-394c-42a5-a7ed-1b9e8e761d3c> · repo copy
`AUDIT_DISCOVERABILITY.md` · raw output `scratchpad/discoverability_matrix.json`,
`scratchpad/discoverability_probe.json`.

**Method.** Every top-level channel of the tracked-agent schema and every entry of the
player capability catalogue, rendered against real briefings in real worlds: three eras ×
the eight largest powers × **four states** (seeded, at war, occupying, ally losing) — 96
briefings, 24 player menus. Each state applied inside a transaction and rolled back, so all
four are measured against the world the scenario actually seeded. The first version of the
harness cleaned up with `at_war = 0` and silently destroyed the seeded wars; from the second
government onward it would have been measuring universal peace and calling it "seeded".

**Findings.**

1. **`covert_support` is named in 0 of 96 briefings.** In the schema, has an engine, has
   tests through `parse_batch`, and no government has ever been told it exists. **The audit
   began with a waiver claiming it was "named beside `support`" and the rendering refuted
   the waiver** — which is the whole argument for rendering text rather than reasoning about
   it.
2. **Research is priced and never justified.** The build menu names this era's programmes
   with id, cost and duration in 24 of 24 briefings and says what one *does* in **0 of 24**,
   directly under public works that each print `-> industry +8, stability -1`.
3. **Programmes behind unmet prerequisites are named nowhere** — three of seven in
   modern-day and cold-war, three of six in gathering-storm.
4. **`declare_war` is silent for 27 of 96 governments, deliberately.** `_warpath_lines`
   chose "silent when the answer is nobody" over "nobody, and here is why" — the same
   silence five other blocks were repaired for. **Left as a design call.**
5. **Mediation's discovery path had no test at all.** The mechanism is tested thoroughly and
   so is the answer channel; nothing rendered a briefing and asked whether the broker is
   told.

The player's half is structurally sound in a way the model's is not — one catalogue
generates the parser enum, the prompt menu, the council's list and the examples — with one
exact exception: `Capability.cost` documents `""` as *"nothing directly, which is itself
worth saying"*, and `prompt_menu` omitted the clause. Six acts showed a bare verb, one of
them `declare_war`. **On cost, the AI's briefing was better than the player's menu.**

Nine waivers are recorded by hand, so the next audit argues with a decision rather than
rediscovering a silence.

**Three fixes staged:** the menu says what an act costs (including "no direct cost"); a
patron is told it could ship quietly; mediation joins `test_every_lever_is_visible.py` with
both halves. The mediation test took two attempts — setting `at_war` alone leaves all three
sandbox powers at an identical leadership index, so the gate correctly refuses and the
assertion never sees the block; the declarations have to be recorded as *events*, because
that is what `concert._restraint` reads.

---

## 3 — STAGED: research is reachable, and the measurement says what it costs

`9fbf4c3`, `ca7ed19`. Harness `scratchpad/research_measure.py`; arms recorded as
`scratchpad/research_measure_t{30,60,100}_{arm}.json`.

**Nothing refused research; nothing ever reached it.** The unattended chooser walked an
ordered list of public works and took the first thing it could afford, and a workshop is
always cheaper than a programme. Measured on the tree as it stood: 30 turns, three Modern
Day seeds, **0 technologies and 0 researchers in all three**. The seventh capability found
working, tested and unreachable.

**What it does.** Briefings state what each programme buys (a unit effect names its hull),
name the programmes behind a prerequisite and what opens them, example a programme in the
task block, and order the list by the government's committed strategy — a reordering only:
no extra action, no price changed, nothing opened that was closed, and it never consults a
victory threshold. The engine half is one order a turn as before, through `order_research`,
the same door the player and tracked agents use.

**Research is bought out of surplus, or not at all**: a power begins a programme only when
it could still have afforded the public work it would otherwise have laid down. Both gates
are constants precisely so that the control arm is *the same code with one number out of
reach*.

**60 turns, three seeds, oracle unchanged:**

| arm | surplus | bank | development | Δ | technologies | score sd |
|---|---|---|---|---|---|---|
| baseline | ∞ | ∞ | 11021.7 | — | 0 | 0.0736 |
| **shipped** | 1.0 | ∞ | **10835.1** | **−1.7%** | 65 | 0.0742 |
| banknear | 1.0 | 1.2 | 10795.0 | −2.1% | 73 | 0.0735 |
| both on | 1.0 | 0.55 | 10540.5 | −4.4% | 78 | 0.0698 |
| bank only | ∞ | 0.55 | 10586.8 | −4.0% | **0** | 0.0679 |

**Saving is off, because its own arm says it costs 4.0% of world development and buys
nothing.** The argument for it is good — reordering to something cheaper is a treadmill,
the workshop keeps the treasury permanently just below the steelworks — and it is wrong
here: a government that waits is a government that did not build. It ships as a mechanism
that exists, is tested with the bar put back where it was measured, and is one constant from
being on. At a short-wait bar (1.2) it is roughly a wash: −2.1% for eight more technologies.
**Which of those a world should have is your call.**

**100 turns changed the verdict, and for the better:**

| 100 turns | development | mean world score | score sd | knowledge | wealth |
|---|---|---|---|---|---|
| baseline | 22576.2 | 0.21501 | 0.07983 | 0.00000 | 0.52208 |
| shipped | 22085.2 | 0.21551 | 0.08310 | 0.03571 | 0.51100 |
| | **−2.17%** | **+0.231%** | | | |

Development does **not** recover — it is a real, sustained trade, slightly wider at 100 than
at 60. A public work compounds (industry widens the queue, which builds more); a programme's
effect is a one-off bump. **But on the measure the world is actually scored by it is net
positive, and it crosses over between the two horizons:** −0.05% at 60 turns, **+0.23% at
100**. Knowledge 0.000 → 0.0357 at weight 0.08 against wealth 0.522 → 0.511 at weight 0.22.

So the honest statement is narrower than "development does not fall" and stronger than it
sounds: **knowledge moves, the spending is not wasteful — it buys more score than it costs —
and total development pays 2.2% for it.**

**The convergence condition for staging this is answered.** Score spread 0.0798 → 0.0831:
the field is *less* converged with research on. 102 technologies across ~42 powers per seed,
where every previous measurement recorded zero everywhere.

**Correctness check:** with both constants out of reach, 30 turns of all three seeds return
`7ce49411b18a / 424a4323b52c / 4ab9d51329f4` — the same fingerprints the tree produced
before any of this. The refactor is behaviour-identical where it is switched off, which is
the only thing that makes the comparison mean anything.

---

## 5 — MERGED: two pure string helpers, memoised

`9766c52` (merge), `850dd6c` (change), `3f64ea7` (measurement). Taken after the queue was
finished, under the same discipline and the same bar.

`naming.normalise` casefolds, strips accents and punctuation and drops a leading article,
and is a function of its argument and nothing else. `reference.all_mentioned` calls it on
every id and every display name in a vocabulary on every call — two hundred nations and as
many regions, each twice — and then builds a whole-word regex around each result. About
**four hundred distinct answers, recomputed tens of thousands of times**: 47,077 regex
compilations and 141,216 parser calls in a twelve-turn run.

| | baseline | memoised |
|---|---|---|
| sim | 108.13s ± 3.18 | **101.04s ± 2.20** — a **6.6%** cut |
| a turn | 9.011s | 8.420s |
| function calls in a profiled run | 50.2M | 35.8M |
| fingerprint | `51fe0d42bfd1` ×3 | `51fe0d42bfd1` ×3 |

**The measurement half is what found it.** The harness now prints sixty rows and a second
table sorted by tottime; none of these lines appear on the cumulative table at all, because
a pure helper called from twenty places has a large tottime and a cumulative spread too
thin to rank.

Deliberately memoisation and **not** an alternation over the vocabulary, which would be
faster and is not the same test — an alternation matches at most once at any position, so a
shorter name shadows a longer one and two entries that both appear become one. Thirty-five
tests compare each helper against the expression it replaced.

**Gate:** all eight PASS, cassettes replayed with no re-record needed, suite 4288 passed,
ruff clean.

**Where the time now is, and it is not this.** 2.7 million sqlite executes, 44.4s, **51% of
the profiled run**, and 2.33 million `fetchone` calls — so most of them are single-row
lookups. The three largest callers: `naval._sea_is_ours` (540,699 calls),
`resolve._strength` (242,058) and `budget.ground_value` (167,845). All three read state
that moves inside a turn, so they need a scoped cache with an argued invalidation contract
rather than a memo. That is the next piece of work and it is a larger one.

---

## 4 — STAGED: the builder-victory dossier (analysis, nothing selected)

`e9503ab`, `DECISION_BUILDER_VICTORY.md`.

**The frame first, because it disqualifies a family of designs rather than two constants.**
Hegemony and Domination both measured a power against the field, and the decisive evidence
is not that their thresholds were wrong: a specialist that genuinely out-developed its
control in every arm — the only power in any run to research anything — **lost on ratio
anyway**, its own score rising while the runner-up rose faster. A relative measure cannot
reward building, because everybody builds.

**But absolute alone is not enough either**: an absolute bar on a *level* is a bar the
scenario may already have handed somebody on turn one. Four tests run through the document —
not relative, not seeded, costly, counterable — with a fifth asked of each: does it work for
a power that is not a great power.

**What Concert teaches and the half that does not transfer.** It became winnable by building
a mechanic, not by lowering a number. It also stayed comparative and got away with it because
its award is rivalrous *by construction* — only three powers are paid. Development has no
such pump and cannot honestly be given one, so the comparative form is closed here in a way
it was not closed for Concert.

Three options, each with what advances it, what is given up, how a rival answers, how it
avoids a seeded win, how wealth and knowledge contribute, what evidence would count, and
whether a minor can play it:

* **A — the Development Compact**: an index against the country's *own* turn-zero baseline.
  Passes the seeded test cleanly, the only one that works for minors, smallest engine change.
  Risk: becomes a clock rather than a route.
* **B — Works of the Age**: a finite catalogue of enormous projects, each completable once,
  by one power. Richest counter-play (race it, bomb it, sanction it, invade it) and the only
  one where knowledge is structurally load-bearing. Largest build.
* **C — the Economic and Scientific Order**: win by being what the world's development runs
  through. Best sacrifice in the document (you enrich your rivals and cannot blockade whom
  you feed) and least ready — Modern Day seeds 648 trade routes so it fails the seeded test
  as stated, and the technology transfer it rests on does not exist.

Each has one measurement that decides whether it is alive, all three cheap and headless.
Doing nothing stays a live option and the document says so.

---

## The one thing blocking both briefing branches

**The active cassette misses, and it should.**

```
pt72-cold-war: turn 1: MISS -- the cassette has no record of this tracked_agent call.
Its prompt does not match any of the 59 unused tracked_agent call(s) recorded, so the
engine is asking a different question than it asked when this was recorded.
```

Both `discoverability-audit` and `research-discoverable` change rendered prompt text, so
both red the cassette gate (and with it the five neighbouring `test_release_audit.py` tests
that assert only their own gate is red — 6 failed, everything else green, on each branch).

It was **not** worked around. There is a `MatchMode.LOOSE` that would make it green and
using it would make the gate decorative.

**Re-recording is a live-model run** — ~99 provider calls over 12 turns, with agy pinned to
`world_sim` at roughly 4 minutes a turn — so it was deliberately not spent unattended on a
branch in flight. It should be **batched**: decide which staged work you are taking, merge
that combination, and re-record once against the tree that will actually ship. A tape
recorded against either branch alone would be wrong the moment the other landed.

---

## Where we left off

* **Master is green and 39% faster a turn** (13.769s → 8.420s across the two merged
  optimisations). Nothing there needs attention.
* **Three decisions are yours**, in rough order of how ready they are:
  1. the three small discoverability fixes — take, amend or drop;
  2. research: take as shipped, or turn saving on at 0.55 or 1.2 (numbers above), or drop —
     and whether a 2.2% development cost for a net-positive world score is a trade this
     world should make;
  3. the builder-victory options — select one, ask for the deciding measurement on one, or
     decline all three.
* **Then one batched tape re-record**, against whatever you take.
* **Left untouched as instructed:** `domination-remeasure`, `victory-frame`.
* **Next perf target**, and it is now the only large one left: **2.7M sqlite executes,
  51% of a profiled run**, almost all single-row. Not a memo — those callers read state
  that moves inside a turn, so it wants a per-pass cache with an invalidation contract
  written down and argued. The ocean set (`air._oceans` and its two copies) is the small,
  provably-safe leftover of the adjacency shape and was left in favour of the larger win.
