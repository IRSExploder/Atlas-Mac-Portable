# ATLAS — review handoff: pacing, the world clock, and national doctrines

**For:** Codex, reading this against your audit of the last handoff and against gameplay
recommendations #2, #3, #4, #8 and #9.
**Prepared:** 2026-08-16. **HEAD:** `16b9d97` on `master`.
**Tests:** 4021 passing, 1 skipped, 1 deselected (`live`), ruff clean over `src` and `tests`.
**Schema:** 37. **`atlas release-audit`: PASS**, 8/8 gates.

> **Seventh of seven.** [`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) covers everything before
> the consolidation; [`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md) the
> consolidation; [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md) the 1972 era;
> [`REVIEW_HANDOFF_ROADMAP.md`](REVIEW_HANDOFF_ROADMAP.md) the engine batches;
> [`REVIEW_HANDOFF_UI.md`](REVIEW_HANDOFF_UI.md) the eight screens;
> [`REVIEW_HANDOFF_WARLOOP_CONSTITUENCIES.md`](REVIEW_HANDOFF_WARLOOP_CONSTITUENCIES.md)
> batches #1 and #2; and [`REVIEW_HANDOFF_GAMEPLAY.md`](REVIEW_HANDOFF_GAMEPLAY.md) the
> three gameplay batches, at `9b64d1d`. **This document covers everything since that
> commit — 56 commits — and where the two disagree this one is current.**

> **Four things to know before anything else.**
>
> 1. **Your audit of the last handoff found four claims that were not true of the code, and
>    all four are fixed** (§1). The worst was the cassette gate: it had never actually
>    replayed anything. It replays now and reddens on a miss.
> 2. **A whole bug *class* was found and closed** (§2): a quantity tuned per month and
>    charged per turn is invisible at the monthly turn and wrong at every larger jump. Ten
>    instances. Then Alex directed the deeper fix — durations of elapsed history are world
>    dates now, and a structural gate makes the pace control unable to decide outcomes.
> 3. **National doctrines are built and universal** (§5): engine-owned per-nation state, the
>    player included, seeded historically, with three axes live. This is the largest single
>    addition since the intelligence layer.
> 4. **Migrations 19 and 20 remain the only two that invalidate saves.** Schema went
>    34 → 37 in this stretch, every step additive.

---

## 1. The tooling hardening your audit drove — `4933a92`, `1a4e336`, `3718cb4`, `006baff`, `996ff46`, `c194145`, `925e43d`

You read the last handoff against the code and found things it asserted that the code did
not do. `c194145` is the commit that answers four of them; the rest are the individual
fixes. Taken together this closed the class where **an instrument reports green because it
never ran**.

**The cassette gate was a green light with no sensor behind it — `1a4e336`.** The release
audit's `cassettes` gate counted files in the index and checked their status strings. It had
never replayed a tape. A recorded run whose prompts HEAD no longer produces would have
passed it silently, which is the exact failure the gate exists to catch. It now replays
every ACTIVE tape through `scratchpad/tape_replay.py` and reddens on a miss or a mismatch.
`925e43d` then recorded a fixture the strict gate could actually pass, because the tape that
was active when the sensor was installed could not.

**A script that always says fine is a script that cannot fail — `3718cb4`.** The replay and
verification scripts printed their findings and exited zero regardless. Anything that shells
out to them — a hook, a CI step, a person in a hurry — read success. They return nonzero on
a miss now.

**A pack that predates the fix says so — `4933a92`.** Map packs carried no version, so a
pack baked before a geometry correction was indistinguishable from a pack that was simply
wrong. Two very different findings with one symptom. Packs are versioned and the verifier
says which it is looking at.

**The door test never asked who could walk through — `996ff46`.** This is the important one.
The "no mechanism a model cannot reach" test had recurred as a defect *three times*, and the
reason was that it only asked whether a door existed — not whether the actor who needs it
can open it. A channel present for the player and absent for a tracked nation passed. The
guard is now **per-actor**, and it is what caught `1bf62ab` (a nation nobody is playing
could not run its own intelligence). Every channel added since — the doctrine-shift channel
in §5 among them — is pre-flighted against it.

**Three tables the randomized history had never touched — `006baff`.** The rewind fuzz had
a hand-kept list of tables and three fingerprinted, diffable ones were missing from it. The
list is now checked against `_FINGERPRINT_TABLES` ∩ `PRIMARY_KEY`, so a new table cannot be
omitted quietly — it fails until it is fuzzed or argued for in writing. This gate fired on
`doctrine` the day it was added (§5) and demanded a fuzz branch before it would go green.

---

## 2. Pacing: the bug class, then the world clock

### 2a. The class — `9396cf6`, `6bc9b10`, `c1eeb18`, `ba88f2e`, `2b29658`, `6aea603`, `33556d0`

**The shape.** The resolver has two halves. The per-round half is span-correct by
construction, because a year-long jump calls it twelve times. The **once-per-turn half is
handed the whole span and has to do the arithmetic itself** — and every bug found was there.
A quantity tuned per month and charged per turn is *exactly right* at the default monthly
turn and wrong at every larger one, so none of these was visible in ordinary play.

**Six of this shape, out of ten findings in the scout.** The other four were ordinary
bugs the same sweep turned up and are not about pacing at all; conflating the two numbers
overstated the class, and Codex was right to say so. The three of the six worth naming:

* **War weariness walked past its own plateau.** `gap * DRIFT * span` is a converging rate
  written linearly, and it passes what it converges to after four months. A quarter-occupied
  country took +75 over a year from a term whose entire ceiling is 25.
* **A year of independence charged twelve years of it** (`6bc9b10`). `assess_independence`
  was called with the whole turn's span *inside the round loop*, so a year-long turn charged
  146 months of colonial momentum. Found by reading a curve — living powers went 74 → 238
  over ten yearly turns — not by reading code.
* **Intelligence exposure and counter-intelligence** were rates applied linearly, so a
  player who watched a year at a time was caught at a different rate than one who watched a
  month at a time.

**The distinction that fixes them, and it is now doctrine** (`engine/pacing.py`): a per-turn
quantity is a **flow** (`over(amount, span)` — multiply), a **rate** (`compounded(rate,
span)` — close a fraction of the gap), or **war** (resolved in `rounds(span)` rounds).
`6aea603` shows the rule failing in both directions before it passes; `2b29658` replaced
"somebody watched the round loop" with an AST gate that checks every per-round call is
given a round; `ba88f2e` ran 200 turns twice at two pacings over the tables nobody re-read.

### 2b. The world clock — `4c85ec2`, `68e0b1b`, `4ad16cf`, `ce75b13`, `0b0f1f1`, `333b888`

Alex directed the deeper fix after `4c85ec2` demonstrated the consequence: **the pace
control decided who wins.** `_DOMINATION_TURNS = 2` meant holding domination for two months
at monthly pacing and two *years* at yearly, from the same world.

* **Victory is calendar time — `68e0b1b`.** 28 days for domination, 59 for hegemony: the
  shortest real February and the shortest real two months, chosen so a short month cannot
  make a condition unreachable.
* **Agreements end on dates — `4ad16cf`.** Armistices, treaty deadlines, sanctions and the
  intel cooldown now carry world dates. **Migrations 35 and 36**, both additive; the old
  turn columns stay, because a save written before them has no date to convert *from*.
* **Which clock each duration is on — `ce75b13`.** Every time-shaped constant in the package
  is classified as WORLD_CLOCK, PLAYER_CLOCK or NOT_A_DURATION with a written reason, and an
  unclassified one fails. UI grace periods stayed turn-based deliberately: those are honestly
  about the player's attention, not about elapsed history.
* **War exhaustion retuned — `0b0f1f1`.** A quiet war now approaches a moderate plateau near
  32 from time alone; casualties, occupation, mobilisation, bombing, economic disruption and
  domestic opposition are what carry it to 100. Before this a phoney war and a bloodbath
  arrived at the same number.
* **The gate that makes it permanent — `333b888`.** The honest form of the invariant is *at a
  world date every pacing actually observes, the answer agrees* — not "twelve monthly turns
  equal one yearly turn", which is arithmetic rather than a promise. An earlier draft
  asserted the algebraic identity and had to be replaced.

**Worth arguing with:** the plateau constant (`TIME_CEILING = 32`) is a balance judgement,
and §9 lists three exhaustion drivers that are designed and unbuilt.

---

## 3. Standing directives — `df0a361`

**Recommendation #3, and the answer to "what is this government trying to do".**

Six standing policies — avoid a continental war, contain a power, restore the old borders,
keep the seas, protect a client, industrialise. Three properties carry the whole design:

* **It is the player's, not the world's.** A directive changes nothing about the world, so it
  lives in the **workspace sidecar** beside the watchlist and the draft orders. No schema, no
  fingerprint, and **a rewind does not touch it** — a note somebody wrote to themselves must
  not be undone by unwinding a turn.
* **It advises and never acts.** Nothing writes a diff, orders anything, or removes a legal
  option. The action list is the **same length** with a policy and without, and every offer
  carries the weight policy put on it so the ordering can be read rather than guessed.
  `test_nothing_here_can_reach_a_diff` holds this at the source level: the module contains no
  `Diff(`, no `apply_batch`, no `record_event`.
* **It is about this government only.** What a *tracked nation* is trying to do is the
  engine-owned feature in §5, and conflating them would put a player's private note into the
  world's state.

Read by `engine/views.py` (the advisor's briefing), `engine/against.py` (the action list) and
`engine/alternatives.py` — which ranks a refusal's suggestions **before** the limit is
applied, so the cut keeps what serves the policy rather than what the catalogue listed first.

**Tests:** `test_a_government_that_is_trying_to_do_something.py` (23).

---

## 4. Two smaller pieces — `8405707`, `d662771`, `72ff619`

**`Capability.available` takes an optional target — `8405707`.** The last handoff flagged
this: the predicate took a nation and no target, so `engine/against.py` had grown a private
table of two exceptions (a declaration against a power you are already fighting, an offer of
peace to one you are not), each naming the engine rule it stood in for. Two is a
coincidence; a third would have been a second catalogue. The predicate now takes
`(conn, world_id, nation_id, target)`, the exception table is **deleted**, and the catalogue
answers for itself. Used immediately afterwards by `adopt_doctrine` and by the neutrality
pact gate in §5.

**Timeline comparison — `d662771`** (recommendation #8's held half). Two lines held against
each other as a **curated headline**, never a diff: territory, wars, treaties, governments,
economy, armed forces, nuclear balance, major facts, score. Read at **the last day both lines
reached**, because turn 30 against turn 25 holds two different moments beside each other and
calls the gap a consequence. Read-only, and the copy is how: a line that must be read as it
stood earlier is copied and the *copy* is rewound.

> **The bug it flushed out, and it is worth your attention.** `shutil.copy2` of a world that
> is **open** — and the line a player compares is the one they are playing — produced a
> database with **no tables at all**, because the recent turns live in the write-ahead log.
> It uses `export_world`'s `VACUUM INTO` now, which reads through the log. **Every test
> passed before the fix**, because a world a test has closed has already checkpointed. Only
> running it against two real saves from the command line caught it.

`72ff619` closes a follow-on: a marked line whose directory disappears between refreshes.

**Tests:** `test_two_lines_you_played_held_against_each_other.py` (38),
`test_whether_you_can_do_it_can_depend_on_who_to.py`.

---

## 5. National strategic doctrines — `9653f45`, `a9f62bc`, `3f09544`, `bdf6a67`

**The largest addition in this stretch, and the engine-owned counterpart to §3.** Design in
[`DESIGN_NPC_DOCTRINES.md`](DESIGN_NPC_DOCTRINES.md), which contains the scoping of all seven
axes and the forks Alex answered. `9653f45` is the scoping; `a9f62bc` the foundation plus the
first axis; `3f09544` and `bdf6a67` the other two.

**Universal, at Alex's decision.** Every nation is *eligible* for a doctrine, the player's
seat included — and most have none. Modern Day seeds 14 of its 242 nations, because a
doctrine is a claim about a country that somebody has to be able to defend, and absence is
both the default and the honest answer for the rest. What "universal" rules out is a
mechanic that applies to the AI and not to you.
The alternative — NPC-only — would have meant an "unless this is the player" branch in every
hook, which is precisely the shape that grew `against.py`'s exception table.

| | directives (§3) | doctrines |
|---|---|---|
| stored in | the workspace sidecar | a fingerprinted table |
| rewind | untouched | undone like any other state |
| who has one | the player | **every nation** |
| what it does | reorders advice | changes the arithmetic |
| who writes it | the player, free and instant | proposed, then priced |

**Absence is the default and means today's behaviour.** No row, no stance, every multiplier
exactly 1.0 — so no system asking the question needs a branch for a world without doctrines.
That is the `arsenal` property and it is what let this ship without moving any world that
does not seed it. **Schema 37**, additive.

**The transition is the cost.** Six months of world time (twelve at war), plus 15% of the
treasury on the day. **The stance is emptied while shifting**, so a country reorganising
holds neither stance's advantages — self-balancing in a way a flat charge is not, and there
is no in-transition flag anybody can forget to check, because an absent stance already means
no modifiers. One axis at a time.

**Two doors onto one priced function:** the player's `adopt_doctrine` capability, and a
`doctrine_shift` field present in the schema only for `Role.TRACKED_AGENT`. Both go through
`doctrines.adopt`, so they cannot come to disagree about what a shift costs. Five
reachability links, each with a test — the §1 guard applies.

**Seeded historically in all three eras and checked against a manifest written separately
from the scenario.** It earned itself immediately: India is `m_ind` in 1972, the seed said
`ind`, and the doctrine was dropped in silence by the `living` filter. The manifest disagreed
and was right.

### The three built axes

**Mass mobilisation vs a professional army.** Five levers, all pre-existing constants:
manpower ceiling (×1.30 / ×0.75), its recovery, land upkeep per formation, the strength of a
land unit **when it is raised**, and war exhaustion per casualty. The 0.75 ceiling is the
whole downside and is severe on purpose: a professional army that is destroyed is not
rebuilt. The quality multiplier is **stamped at delivery and never re-applied**, so changing
doctrine changes the army you raise next, not the one you have — free realism, and a second
reason the axis takes years to fully bite.

**Nuclear posture.** Massive retaliation buys a deterrent that is harder to discount and
harder to disarm and answers in force; it is paid for every month and remembered the day it
is used. Minimum deterrence keeps most of the deterrent for a fraction of the money and
accepts that a first strike might find it. Required one new mechanic Alex approved — the
**arsenal upkeep charge** (§6), because nothing had ever charged anything for holding
warheads.

**Collective security vs armed neutrality.** Required the other new rule Alex approved:
**an unanswered call costs the no-show**. Article 5 was prompted rather than automatic — which
is right — but *nothing at all happened to an ally that held a pact and stayed home*, so a
pact was a benefit with no obligation and collective security had nothing to be reliable
about. Three months of world time to decide; then −18 regard with the injured party and −3
legitimacy at home, or +12 regard for coming. **Derivable, never asserted** — the call names
the ally, the aggressor and the victim, and whether the ally came is `relation.at_war`, so no
model can claim it answered when it did not. Charged exactly once with **no schema**: the
settling event cites the call.

Neutrality is **the one doctrine that removes an option rather than scaling one** — a neutral
will not sign a defensive pact or an alliance at all. It may still talk, trade and go to war.

### The discipline, and what it caught

**No free lunch, held as a rule rather than as numbers somebody checked.** Every stance must
both help and hurt; at most ±15% on anything reaching `compute_pressure`, at most ±30% on
stocks, flows and accumulators. It caught its author three times:

* manpower recovery was written at 1.50 and is 1.30;
* `casualty_grief` was classified as a combat-odds lever when war exhaustion is an
  accumulator a player watches, not a term in anybody's odds;
* the rule itself was originally "one number up and one down", which silently assumed higher
  is better — and massive retaliation raises five numbers of which two are bills. Levers now
  declare whether they are a cost.

> **Two levers were wired and silently did nothing, and both were caught by printing them
> rather than by reading them.** Survivability went into the land-only branch of
> `nuclear.survivability`, so it was inert for any triad or boat force — every arsenal the
> axis is about. And deterrence multiplied *before* its ceiling: `_DETERRENCE_CEILING` is
> 0.18 and a warhead is worth 0.015, so every arsenal above a dozen survivors was already
> clamped and the posture moved nothing at all for the United States, the Soviet Union,
> Russia or China. **This is the failure mode §1 exists for, arriving in a new disguise:**
> not a channel with no door, but an effect with no reach.

**Tests:** `test_what_a_country_is_built_to_do.py` (76),
`test_an_ally_that_does_not_come.py` (36).

---

## 6. The two economic follow-ups — `dbdeac3`, `971583d`

Both are budget charges Alex approved after seeing the doctrine numbers, and both were
**measured before they were tuned**, which changed what the job turned out to be.

**Britain's nuclear deficit — `dbdeac3`.** Britain ran −0.31/month under the arsenal charge
and the sea tier was the named dial. Measuring first showed the dial was only part of it:
**Britain's deficit is its fleet.** It runs −0.137 with no arsenal charge at all — ten ships
at 0.60/month is 6.00 of an 8.72 upkeep against 8.59 of revenue. **No arsenal dial reaches
balance, because 44% of the deficit is the Royal Navy and always was.** Two changes: the sea
tier 0.22 → 0.18, and a deterrent's programme now **scales with how much of it there is** —
Britain's four warheads and America's forty-six were paying the same programme, so a squadron
cost what an establishment did.

**Pact upkeep — `971583d`.** A per-month charge per standing agreement that promises force,
so "less entangled" is a budget line rather than a multiplier on somebody else's constant.
**No schema** — derived from the commitment rows, so a pact that lapses stops costing in the
turn it stops binding. Bounded by construction: the most-allied power on any shipped map
holds four.

> **It needed the superpower to be able to afford it.** The 1972 United States has 0.720 of
> margin, holds the most pacts on any map and has the largest arsenal — and the arsenal
> charge was taking 0.694 of the 0.720. The per-warhead cost was why, and it contradicted its
> own stated design: at 46 warheads the count came to 0.184 against a 0.35 triad programme,
> more than half, on a charge whose premise is that the money is in the silos and not the
> warhead on top.

**Every nuclear power, both charges** (1936 has none armed and pays nothing, nation by nation):

| era | nation | arsenal | pacts | before both | after both |
|---|---|---|---|---|---|
| 1972 | United States | 0.480 | 0.150 | +0.720 | **+0.090** |
| 1972 | Soviet Union | 0.464 | 0.113 | +1.697 | +1.120 |
| 1972 | United Kingdom | 0.055 | 0.037 | −0.137 | **−0.230** |
| 1972 | France | 0.042 | 0.030 | +3.142 | +3.070 |
| 1972 | China | 0.021 | 0.060 | +1.621 | +1.540 |
| 1972 | Israel | 0.018 | 0.000 | −0.182 | −0.200 |
| 2026 | Russia | 0.468 | 0.037 | +2.286 | +1.780 |
| 2026 | United States | 0.464 | 0.150 | +5.424 | +4.810 |
| 2026 | China | 0.203 | 0.060 | +11.603 | +11.340 |
| 2026 | France | 0.098 | 0.037 | +1.955 | +1.820 |
| 2026 | India | 0.130 | 0.000 | +6.730 | +6.600 |
| 2026 | United Kingdom | 0.076 | 0.075 | +1.642 | +1.490 |
| 2026 | Pakistan | 0.087 | 0.030 | −0.003 | −0.120 |
| 2026 | Israel | 0.051 | 0.000 | +0.961 | +0.910 |
| 2026 | North Korea | 0.042 | 0.060 | +1.032 | +0.930 |

**The guarantee, and a test walks all three eras asserting it: nobody who was in the black
before either charge is in the red after both.** The five negatives above were all negative
or within a rounding error of zero before either charge existed.

---

## 7. The Quang Tri self-siege — `ada23ed`

A contest where South Vietnam held Quang Tri **and** besieged it, appearing in five recorded
live runs. `4496caf` had aimed at it and it recurred anyway. This is the actual cause.

**The earlier fix moved the reconciliation to the end of the RESOLUTION and its own docstring
called that "the end of the turn". It is not.** Three passes move a province after the
fighting resolves:

* partisans rising and putting the flag back up (`unrest.assess_partisans`),
* a peace term sending an occupied province home (`peace.honour_peace_terms`),
* a colony becoming a country on a tracked nation's own turn (`decolonisation.decolonise`),
  which is the last thing that happens in a jump.

The live shape is the first and it is ordinary: a country fights to retake its own occupied
core, partisans rise there in the same turn, and the flag goes up *behind* the
reconciliation. **Reproduced deterministically in about a second** — which is what the first
fix lacked; it was reasoned from the recordings rather than reproduced.

Why it matters is not a stale row: `captured = pressure >= THRESHOLD and held != attacker`
means such a contest can never end, and the briefing renders it as *"you hold it, you are 55%
of the way to taking it"* — a sentence a staff acts on, and did.

`reconcile_self_sieges` runs once, after everything in the turn that can move a province. A
test asserts the ordering against the three late passes **by name**, and another asserts the
four modules that write a region's holder are the four we think they are — so a fifth cannot
reopen it quietly.

**Confirmed live, twice.** PT52 and PT53 both report *nothing suspicious* across all twelve
turns, from the same era, seed and setup that produced the finding five times running — and
PT53 is under a materially different economy (§6), so the fix is not load-bearing on the
numbers it was found under.

---

## 8. Cross-cutting principles

These are the rules the work above is checked against. Each has a test that fails first.

* **Model proposes, engine owns the truth.** Every new channel in this stretch is *intent*:
  the doctrine shift, the pact-answering detection, the comparison. The columns are
  ENGINE-only, the engine checks the proposal against state and writes the row itself.
  Nothing added here can assert an outcome.
* **Built-but-unreachable, pre-flighted and per-actor** (§1). The guard now asks *who* can
  open a door, not merely whether one exists — and the doctrine layer produced a new variant
  of the same class (an effect with no reach, §5) that the guard does not catch, which is
  worth thinking about.
* **Determinism and rewind are not optional.** Every fingerprinted table is in the randomized
  rewind fuzz or has a written argument for why not; the list is checked against the schema
  rather than kept by hand. `doctrine` was fuzzed the day it was added because the gate
  refused to pass otherwise.
* **`atlas release-audit` is the rail.** Eight gates: packs-exist, packs-verify,
  schema-parity, determinism, invariants, scenario-audit, cassettes, suite. It was run at
  every checkpoint in this stretch and is 8/8 at HEAD.
* **Measure before tuning.** This reframed the job three times: the pacing class was found by
  reading a curve rather than code; Britain's deficit turned out to be its navy; and the two
  silently-dead nuclear levers were found by printing them. **A constant chosen by argument
  and a constant chosen by measurement look identical in a diff.**

---

## 9. Open, flagged, and deliberately not built

Nothing here is a surprise; each was flagged when it was met.

**Balance dials, not defects.**
* **Britain's residual −0.137/month** is the Royal Navy and predates every charge in §6.
  Reaching balance needs a naval-upkeep or revenue dial, which is a scenario decision.
* **`TIME_CEILING = 32`** — the war-exhaustion plateau. A judgement.

**The four unbuilt doctrine axes**, scoped in
[`DESIGN_NPC_DOCTRINES.md`](DESIGN_NPC_DOCTRINES.md) §1.1 with the hook analysis:
* **forward defence vs defence in depth** — needs one new frontier-distance query;
* **carrier vs surface fleet** — needs a per-era map from `unit_template.kind` to a role,
  because "carrier" is not a string that exists in 1936;
* **autarky vs trade dependence** — clean hooks, nothing new needed;
* **decolonisation vs imperial integration** — the withdrawal half is clean; *integration* is
  a genuinely new verb (writing `region.home_nation_id`, which nothing does today).

**War exhaustion:** three drivers are designed and unbuilt — mobilisation level, strategic
bombing and economic disruption — and there is an open question about whether
`constituencies` and exhaustion should feed each other, which is a loop nobody has priced.

**Deliberately deferred, design-laden** (each has written questions in
[`DESIGN_MAP_GAMEPLAY.md`](DESIGN_MAP_GAMEPLAY.md)): intent-to-plan / "describe anything"
orders (#1), operations as multi-turn projects (#5), crisis mode (#7), the deeper half of
playable domestic politics (#6), and authoring a scenario from nothing (#10).

**Not a design fork, just unbuilt:** **in-app branch switching** (#8's other half). Every
overlay, the map applier and the pending orders hold a connection and a world id; switching
means tearing all of that down and rebuilding it live. Worth its own session.

**One judgement call worth arguing with.** When a save is too old to answer a comparison
heading, the timeline comparison reports *that heading* as "not recorded in this save" and
still gives the other eight. The alternative is refusing the whole comparison. I chose the
former because the player with the longest campaign is exactly the one whose saves predate a
table — and is exactly the player who most wants to compare.

**Now closed:** the pact-upkeep-as-its-own-budget-line note was flagged as designed-and-not-
built in `bdf6a67` and is built in `971583d`.

---

## 10. Build state

* **HEAD:** `16b9d97` on `master`. Working tree clean.
* **Tests:** 4021 passing, 1 skipped, 1 deselected (`live`), across 215 test files. Ruff
  clean over `src` and `tests`.
* **Schema:** 37. Migrations added in this stretch: **35** (agreements that end on a date),
  **36** (a standing measure that runs out on a date), **37** (`doctrine`). All additive.
* **Save-invalidating migrations: still only 19 and 20.** Unchanged since the last handoff.
* **Digest moves.** Three built-world digests moved in this stretch and each for a stated
  reason. 35 was the first time a *column* addition moved one, because `commitment` and
  `commitment_term` have rows in the shipped eras. **37 moved them for a third reason: a new
  fingerprinted table that the shipped eras actually fill** — 28 rows in Modern Day — rather
  than a table name on an empty table. Digests are only ever compared within a schema
  version, which is why this is bookkeeping and not a break.
* **Cassette lineage.** PT50 → PT51 → PT52 → **PT53 (active)**. Each supersession is recorded
  in `scratchpad/cassettes/INDEX.json` with which kind of drift caused it: a *miss* is a
  prompt change, a *mismatch with zero misses* is a world change. PT53 replays 12 matched, 0
  mismatched, 0 missed, and its live run reports nothing suspicious.
* **`atlas release-audit`: PASS, 8/8** — packs-exist, packs-verify, schema-parity 37,
  determinism (3 eras built twice), invariants (13 × 3 eras), scenario-audit
  (cold:85 gathering:88 modern:71 facts), cassettes (1/1 active replayed, 15 historical),
  suite.

**Design documents worth reading alongside this:**
[`DESIGN_NPC_DOCTRINES.md`](DESIGN_NPC_DOCTRINES.md) (the seven-axis scoping and the answered
forks), [`DESIGN_MAP_GAMEPLAY.md`](DESIGN_MAP_GAMEPLAY.md) (all ten recommendations and where
each stands), [`DESIGN_WAR_LOOP.md`](DESIGN_WAR_LOOP.md),
[`DESIGN_CONSTITUENCIES.md`](DESIGN_CONSTITUENCIES.md),
[`DESIGN_INTELLIGENCE.md`](DESIGN_INTELLIGENCE.md).
