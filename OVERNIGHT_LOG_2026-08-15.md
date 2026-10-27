# OVERNIGHT — 2026-08-15. The queue, and what came out of it

**All fourteen items are done.** Release audit **8/8** at five checkpoints across the night,
the last of them after item 14. **3439 -> 3788 tests**, ruff clean, tree clean.

Nothing on the HOLD list was built. Everything flagged rather than guessed at is written up
below rather than left in a commit message.

| # | item | commit | tests after |
|---|---|---|---|
| 1 | bug-scout the newest code | `c1eeb18`, `492f00e` | 3460 · **8/8** |
| 2 | partial-success alternatives (Codex #2) | `642f265` | 3479 · **8/8** |
| 3 | branching fast-follows (Codex #8, bounded) | `030ee0c` | green |
| 4 | the "why" inspector (Codex #4) | `5299097` | 3499 |
| 5 | "what can I do about *that*" (Codex #9) | `718f9f0` | 3536 |
| 6 | the design map | `b7da4f5` | **8/8** |
| 7 | deeper bug-scout | `9396cf6` | 3597 · **8/8** |
| 8 | 200 turns twice, and the older tables fuzzed | `ba88f2e` | 3630 |
| 9 | trade, obligations and independence overlays | `c180ecd` | 3659 |
| 10 | accessibility and small UX | `d791f7f` | 3692 |
| 11 | confirmation before the irreversible | `0a80f4d` | 3707 · **8/8** |
| 12 | broader alternatives, three more "why" values | `6a4f163` | 3732 |
| 13 | doc-accuracy audit | `2ed40ad` | 3751 |
| 14 | coverage gap-fill | `3ad1eb7` | 3788 · **8/8** |

**Ten real bugs found and fixed**, six of them the same shape: a quantity tuned per month and
charged per turn. The largest came *after* the queue, from a campaign run at yearly pacing --
see §15, and the rule it broke is a gate now rather than a thing to watch for. Three more bugs
were mine, each caught by running something twice. The rest of this document is what each one
was.

---

## 1. Bug-scout — one real bug, and it was silent

**MEDIUM — converging drift was multiplied by the span, so a long turn overshot the thing it
was converging to.** Both places that settle a value toward a resting point wrote
`value + (rest - value) * rate * span`. That is correct at span 1 and wrong for any span past
`1/rate`: an intel network at 70 with a floor of 30 came out at **26** over three years, and a
constituency at 90 settling toward 50 came out at **2.4**.

**The part that made it worth hunting for rather than reading about:** the out-of-bounds value
was refused by the bounds rule, and ECON-003 condemns the *whole batch*. So a long jump applied
**no drift at all and no counter-intelligence lapse**, and reported nothing. The bug did not
look like a wrong number; it looked like a quiet turn.

Fixed by using `pacing.compounded(rate, span)` — `1 - (1-rate)**span`, which is what "the same
proportion each month" actually means — in `engine/intelligence.py` and
`engine/constituencies.py` (`c1eeb18`).

**Two things I checked before believing my own fix.** Shipped era digests are unmoved. The
PT48 tape *mismatched with zero misses* — the world changed, not the prompt — because about
0.2% of values land differently at the third decimal after the change; re-recorded as PT49
(`492f00e`). And my claim that the two formulas are identical at span 1 was **wrong in
floating point**: 128 of 100001 sampled values round differently. Measured rather than
asserted, then confirmed it was float ordering and not behaviour.

Everything else on the scout list — the six intelligence layers, arsenals-as-estimable, the
new espionage/counterintelligence gambit kinds, the per-actor reachability guard, the
deception-leak fix, the counterintel tuning, the self-siege ordering fix — held up under
adversarial testing. No determinism holes, no rewind gaps, no visibility leaks found.

## 2. Partial-success alternatives — `642f265`

`engine/alternatives.py`. `causality.Refusal` grew an `instead`, filled at the refusal sites
across orders, gambits, war aims, objectives and peace terms **from the gate's own data**: the
provinces actually occupied, the enemies actually at war, the regions actually reachable, the
undertakings that actually pass `gambit._requirements_met`.

Only what the rules can back is ever suggested; `phrase()` returns an empty string rather than
a sentence when there is nothing legal to name. One existing test asserted a refusal had *no*
"Instead" — I changed the claim rather than the behaviour, with the reasoning in its docstring,
because naming a legal alternative is the thing that was asked for.

## 3. Branching fast-follows — `030ee0c`

The flat branch list is a **tree** now, indented by lineage that was stored all along; a fork
can be **named by hand**; a branch exports as a **clean campaign chronicle**, optionally scoped
to one seat's entitlement, with the fingerprint unchanged by rendering it (`atlas chronicle`).

**A real bug on the way:** `branch._describe` opened its probe connection with
`with sqlite3.connect(...)`, which scopes the *transaction* and not the handle — so the file
stayed open and renaming a branch raised `PermissionError` on Windows. Fixed with an explicit
close. Three of my own tests had the same mistake.

Compare-two-timelines and in-app switching were **held and flagged** — see below.

## 4. The "why" inspector — `5299097`

`engine/why.py`, and `atlas why <nation>`. Why a coup is likely, and why a peace will not be
signed. Nothing new is tracked: every figure is re-derived by calling the function the engine
calls, and the only addition is naming the terms.

**The property the module rests on is that the terms sum to the number the engine uses.** The
first version did not: it listed the additive half of `coup_risk` and skipped three
multiplications, and was out by 38% on a troubled government while looking entirely plausible.
Each scaling is now reported as the difference it made, and five parametrised cases hold it
exact. A screen whose parts do not add up to its own total is worse than no screen.

## 5. "What can I do about *that*" — `718f9f0`

`engine/against.py`, in the search box and as `atlas against`. Nothing is offered that the
engine would refuse: the catalogue's availability test and the undertakings' requirement gate
decide the list.

**Two gaps `Capability.available` structurally cannot close**, because it takes a nation and no
target, were closed there: a declaration against a power you are already fighting (which
`_declare_war` silently returns from) and an offer of peace to one you are not (which `settle`
refuses in as many words). Each line names the engine test it stands in for.

What naming a target cannot supply is **reported rather than guessed** — a message still needs
its message, says so, and stays unclickable. Only whole orders can be taken, through the same
pipeline every other order uses.

It stayed clear of the held item: **the signature takes an id, not a sentence**, and a test
holds it to that.

## 6. The design map — `b7da4f5`

[`DESIGN_MAP_GAMEPLAY.md`](DESIGN_MAP_GAMEPLAY.md). Each of the ten marked DONE / PARTIAL /
NEEDS DESIGN, with the machinery it stands on and, for the held five, the questions that have
to be answered first.

**One gap, recorded as a blank rather than guessed at:** recommendation **#3** never reached me
as text — the list came through by number, and #3 is the one number that never came up. Paste
it and it can be placed like the rest.

---

## 7. Deeper bug-scout — four bugs, and they were all the same bug

**One shape, four places.** The resolver has two kinds of system: those that run **per round**
inside a jump (`budget`, `trade`, `unrest`, `supply`, `partisans`, `independence`, development
erosion, construction) and those that run **once per turn** with the whole span handed to them
(`wareffort`, `constituencies`, `intelligence`, `attribution`, `exposure`, sanctions, orders).
The per-round half is span-correct by construction — it is called twelve times for a year.
Every bug found tonight was in the once-per-turn half, and every one of them was a quantity
tuned per month being charged per *turn*.

That is worth stating as a rule, because it is where the next one will be too.

### MEDIUM — war weariness walked past its own plateau (`c1eeb18`… fixed here)

`invaded = gap * OCCUPIED_DRIFT * span` is a converging rate written linearly. It passes the
ceiling it converges toward once the span exceeds `1 / 0.25` — four months. A quarter-occupied
country over a one-year jump took **+75 from a term whose entire ceiling is 25**.

**The clamp is what kept it quiet.** Exhaustion is clamped to 0–100 at the write, so the value
stayed in range, nothing was refused and nothing was reported. The country simply arrived at
total exhaustion from a stabilised front — and the module's own docstring says the plateau
"is what stops a stabilised front collapsing a country by itself".

### MEDIUM — domestic politics were muted by jumping (`constituencies`)

`assess_constituencies` was **half span-aware**. The pull back toward the resting point
compounded correctly; everything that *causes* a mood to move was charged exactly once
however long the turn was. Two halves of one mistake:

* **A condition is a flow.** The function draws the distinction itself — "an occupation does
  not occur, it continues" — and that is precisely what should be charged by the month.
  Charged once, a year of occupation angered a population as much as a month of it.
* **The cap is monthly.** Its reasoning says so: *a country that raised six divisions in a
  month has done one thing, not six.* A country that fought for a year, lost its capital and
  changed its government has not done one thing either.

Together they made the settling term — which does compound — the dominant force in any long
jump. A year of catastrophe moved a constituency 12 points while the resting pull dragged it
a third of the way back to 50. **A player could skip past the anger of a long war by choosing
a longer turn.**

### MEDIUM — the player's schemes were the last thing priced by the turn (`orders._gambit`)

`apply_gambit` takes a `span` and defaults it to 1.0. The model-facing channel passes the real
one; the player's path never did — under a docstring promising "the odds, the stake, the
ledger cap and the roll are identical whoever asked". On a year-long turn a tracked nation
paid a year's stake to move a year's worth and the player paid a month's to move a month's.

The span now rides on `OrderEffects`, where the per-turn gambit ledger already lives, for the
same reason: it is a fact about the *turn*, and fifteen handlers share a signature that does
not need it.

### MEDIUM — secrets kept better the faster you played (`exposure`)

`exposure_chance` says of itself that it is the chance in one *month*; `assess_exposure` rolled
it once per turn. An operation running through a year-long jump got a single month's chance of
surfacing. Compounded now — which is what `pacing` names for probabilities specifically,
because multiplying one does not merely overstate it, it stops being a probability.

### What was checked and found correct

Worth recording, so the next scout does not re-tread it: the per-round systems above; the
accrual terms in `attribution` and `decolonisation` (flows, correctly linear); every `derive`
call key in the engine (canonical, state-derived, not order-dependent); `leverage`'s force
ratio (guarded at 0.1, so no division by zero when an army is destroyed); the nuclear
estimate/truth split (`estimated_retaliation` fogs the *surviving* count under the same
subject key as the stockpile, and `launch` still fires the true one); treaty expiry sweeps
(`<=`, so a long jump cannot step over one).

### Flagged for Alex, not fixed: durations are turn counts wearing month names

`ARMISTICE_MONTHS = 3`, `MAX_MONTHS = 24`, `COLD_AFTER = 12`, `commitment.expires_turn`,
`commitment_term.due_turn`. Every one is named or reasoned about in months and stored as a
**turn count**, so a three-month ceasefire lasts three years on yearly turns and twenty-four
months is twenty-four years.

**Why this one is not a bug fix.** It is systematic rather than a slip — the whole commitment
layer, the alerts that warn on expiry and the armistice lapse check all agree that duration is
turns — so resolving it means choosing, and the choice has two real answers:

* **Convert at the current span** (`turn + months / span`, never below one turn). Local-ish,
  no schema change, and honest about turn granularity — but wrong if the player changes pace
  mid-truce, and needs the span plumbed through three call chains that do not carry it.
* **Store an expiry date** and compare against the world calendar. Correct under any pacing,
  and it is an additive column plus every reader of `expires_turn` and `due_turn`.

The first is cheap and approximate; the second is right and is a schema decision. Guessing
between them is exactly what the hard rule says not to do.

**And the question is narrower than "convert everything", which is worth knowing before
answering it.** `treaties.CESSION_GRACE_TURNS = 3` was judged *honestly* turn-shaped at the time — **and that judgement was wrong**; Codex overturned it and it is `CESSION_GRACE_MONTHS` on the world clock now (`e7a09b9`). What follows is the reasoning as it stood that night: its reasoning is
"long enough that a government has to mean it and short enough that a player watching for it
does not lose interest" -- an argument about the player's attention, which is measured in
turns and not in months. So the decision is not "durations should be dates"; it is **which
durations are about the world's clock and which are about the player's**. A three-month
ceasefire is the first kind. A grace period somebody is watching for is the second.

**The most consequential member of the family is the victory condition**, found on a later
scout of `scoring.py`. `_DOMINATION_TURNS = 2` and `_HEGEMONY_TURNS = 3` are consecutive
*turns*, and the reasoning beside them names months in as many words: *"a threshold crossed by
one province taken in March and lost again in April was never a domination -- it was the front
moving."* So at yearly pacing a player must hold domination for two **years** and hegemony for
three, against two and three months at the default. **The pace control decides who wins and
when**, which makes this the one member of the family worth answering first.

---

## 8. Two hundred turns, twice, on every era — and the fuzz gap was in the old code

### Determinism: identical at every checkpoint, on all three eras

`scratchpad/long_haul.py` builds an era through the audit's own builder, runs 200 monthly
turns with every provider answering an empty batch, and does it twice. **Engine-only on
purpose**: a divergence here is the resolver's, not a provider's.

Checked every 20 turns rather than only at the end, because a world that diverges on turn
140 and reconverges by 200 is still broken and an end-state comparison would call it fine.
The invariants are re-run at each of those ten points too, so the turn a promise stops
holding would be known rather than inferred.

| era | turns | determinism | invariants |
|---|---|---|---|
| cold-war | 200 x 2 | identical at all 10 checkpoints | held at all 10 |
| gathering-storm | 200 x 2 | identical at all 10 checkpoints | held at all 10 |
| modern-day | 200 x 2 | identical at all 10 checkpoints | held at all 10 |

### The curves, and which half of them is about the engine

**Accumulation, not runaway.** Treasury climbs 52x and economy 13x over sixteen Cold War
years. Nothing in an engine-only run *spends* — no player, no builds, no model — and units,
strength, wars and provinces held are all flat. The economy target is a Michaelis-Menten
ceiling rather than a compounding one, so this is a pile of unspent money and not a
divergence. Reported rather than acted on, because the run cannot tell the two apart on its
own and pretending otherwise would be the finding.

**Decolonisation works, and works hard.** `alive` rises 148 -> 160 in the Cold War and
74 -> 119 in the Gathering Storm. Momentum accrues unopposed when nobody garrisons, which is
precisely what an engine-only run never does, so the rate is not evidence of anything. That
forty-five new nations appear at all is evidence the layer is live across a century.

### PARKED for Alex — war exhaustion is a one-way ratchet

All three eras pin exhaustion at **100.0** and stay there: Cold War by turn 80, modern day by
120, Gathering Storm by 140. The telling part is the company it keeps — **zero provinces
occupied, unit strength flat**. These are wars in which nothing whatsoever happens, and they
still reach maximum weariness in about a decade, because the flat monthly cost of *being* at
war accumulates with no ceiling short of 100 and the recovery term only applies once the war
is over.

So a phoney war and a bloodbath converge to the same number, and past that point exhaustion
stops telling them apart — which matters, because it is an input to whether a peace gets
signed. Whether a stalemate should saturate is a balance judgement with scenario
consequences, so it is parked rather than tuned.

### The fuzz gap was in the code old enough that nobody re-read it

`test_the_new_tables_rewind_whatever_you_do_to_them.py` fuzzes the fingerprinted tables added
since migration 19, and is guarded so the next one cannot be forgotten. **Nothing covered the
tables that were already there.** Eleven of them now are, in a sibling file: `contest`,
`construction`, `technology`, `support`, `independence`, `arsenal`, `sanction`,
`military_objective`, `air_control`, `trade_route`, `resource`.

**Composite keys are the substance.** Every table the older file fuzzes is keyed on a single
`id`; six of these are keyed on two, three or four columns and addressed as `"a|b|c"`.
Splitting that id, checking the insert payload agrees with it, and finding the row again on
the way back is exactly the path a rewind uses to undo an insert, and it had no randomized
coverage at all.

It passed on all twelve seeds first time, which is worth saying plainly: **no bug there**, and
it is now a property rather than an assumption.

Two things it caught about *itself* and not about the engine, both fixed in the fuzz: it
opened a contest against ground the attacker already held — a siege of yourself, which an
invariant correctly refuses — and the "did every table actually get written" check missed the
tables the world already seeds, because those are only ever written *to*, never inserted.

**The exhaustiveness is the part that lasts.** Every fingerprinted, diffable table must be in
one of the two fuzz lists or in `NOT_FUZZED` with a written reason. There is no fourth
category, so the next table added cannot quietly land in one.

**And one flake of my own, caught by running the suite twice.** The coverage check seeded its
generator from the builtin `hash()`, which Python randomises per process -- so it drew a
different sequence on every run, passed on its own, and failed on the next full suite. A
seeded fuzz whose seed is not seeded is not reproducible, which is the one property it exists
to have. Now `zlib.crc32`, verified across three `PYTHONHASHSEED` values.

---

## 9. Three more layers, over state the map already held

Trade, obligations and independence momentum. Every figure was already in the database and
already reachable; none of it was on the map, so a player deciding whether to break with a
supplier, honour a pact or garrison a colony was reading three screens and holding the map in
their head.

**The scope rule is the design, and the three are deliberately not the same.**

* **Trade and obligations are the observer's own.** A route is a standing agreement to sell
  and a pact is a thing you signed: both parties know, and what two other powers arrange
  between themselves is intelligence rather than a fact this map may draw. The same rule
  `supply` and `nuclear` already run on.
* **Independence momentum is everybody's.** A colony on its way out holds meetings and stops
  paying its taxes, which is the visible restiveness `unrest` already reads for the whole
  map. Scoping it to the observer would blank the layer for every player without an empire --
  not a more honest answer, only a less useful one.

**Obligations are weighted by what honouring one would cost, not by how friendly it reads.**
A defensive pact can put your army in the field and a trade agreement cannot, whatever the two
governments feel about each other. Only `active` agreements: a pending one has not been
accepted by everybody, and a broken one is the opposite of an obligation.

The independence bands (*stirring* / *organised* / *ripe*) read `decolonisation`'s own two
thresholds rather than restating them, so the map and the engine cannot come to disagree about
when a colony is ripe.

### What the screenshots show, and one honest limitation

`scratchpad/shot_layers.py`, four frames, world fingerprint unchanged on every render. The
momentum in them is the engine's -- two years of `assess_independence` on the shipped 1936
world -- rather than numbers I picked, because which provinces stir is the question the layer
exists to answer and a hand-seeded answer would make the picture about my fixture.

**A measured layer is only drawn on ground the observer knows**, and knowledge comes from
presence: what you hold, occupy, border, or have forces standing in. In a *fresh* world that
is the observer's own neighbourhood, so Washington's obligations layer in 1972 lights up
Canada and little else until the world opens up. That is the fog working rather than the layer
failing -- painting a province the observer has never been near would be a hole in the fog
with a colour ramp in front of it -- but it does mean these two layers are least informative
early, which is worth knowing before reading a screenshot of them.

Independence is shown from London in 1936 for exactly that reason: it is the seat with an
empire to lose, so the fog and the layer overlap. 99 of the 158 restive provinces on that map
are ones Britain can see.

---

## 10. The interface bends to the person using it

Six things, and the shape of four of them was decided by what the interface already had
rather than by what an accessibility checklist lists.

**Text scale.** Every font in the program comes from one type scale and passes through one
function, so the setting is a multiplier inside `styled_font` -- all twenty-five sizes move
together, no widget is told about it, and none holds a preferences object. Five steps rather
than a slider, because the type scale is a designed set of sizes with relationships between
them and a continuous factor invites a setting where the heading is a hair larger than the
body and the hierarchy is gone. A test holds every entry in `TYPE` to scaling by the same
factor, so a style that quietly did not would fail rather than come apart at Large.

**A palette that survives colour blindness.** The map ramp is already cold-blue to
warm-amber and safe; **the risk was the semantic pair**. `good` is a green and `danger` a
red, and between them they colour every delta, every meter and every warning in the program.
The safe palette replaces the green with a blue of the same weight -- separable by hue under
deuteranopia and protanopia and by lightness under tritanopia, which is more than red and
green manage under any of the three. Nothing else moves, because repainting what is already
safe is change for its own sake. It is a property of the person rather than of the theme, so
it survives a light/dark switch instead of being undone by one.

**Reduced motion, and honesty about how little there is to reduce.** This interface has no
animations, no transitions and no easing curves. The only thing that moves by itself is the
map dropping to a coarser rendering while the hand is on it and snapping back a beat after it
stops, and that is what a person sensitive to motion would actually notice. So that is what
the setting turns off, and `MOTION_SURFACES` says as much in the source -- **a preference
that silently does nothing is worse than one that is honestly small**, and if an animation is
ever added its name belongs in that tuple.

**Remappable shortcuts.** Three of them, declared once with their defaults and what they do,
so a settings screen can be generated from the list and a binding cannot exist that nothing
offers to change. Three live on widgets that never see the window, so they read a
module-level current preferences rather than having one threaded through twenty constructors
to reach three -- the same argument as the text scale, and the same reason.

All four are kept in the **workspace**, beside the save with the pins and the draft orders,
because a rewind must not be able to change how big somebody's text is.

### And two small things the turn summary should always have done

**A headline you can click.** A summary line names a province and the map is behind the
overlay; making the reader close it and type the name into the search box is a small tax that
adds up. `naming.region_named_in` derives the link -- the engine's own prose already contains
the database's own names -- and is **strict in three ways, because centring the wrong province
is worse than not being clickable**: exactly one match or none, fogged through the same
`known_regions` the search box is held to, and no fuzzy matching (the generosity
`resolve_region` needs for a model writing "Persia" would only buy false positives here). The
clickable line is coloured *and* cursored, because colour alone is a claim a person who cannot
see it never receives -- which is the same argument the palette one screen over is built on.

**A wait you can predict.** `routing_log` has recorded `duration_ms` and the turn for every
provider call since the log existed, so the estimate is the **median of what recent turns
actually cost in this world with these models** -- no opinion about how many calls a turn
makes or which models are pinned, both of which change and both of which would be a second
thing to keep in step with the router. The median rather than the mean, so one turn where a
provider timed out and retried does not double every estimate after it. A world with nothing
to go on says nothing, because a number invented for a progress bar is still a number the
player will believe. And it gets deliberately vaguer as it grows: past a few minutes the
honest answer is roughly that.

---

## 11. A last look at what the turn will not give back

Codex flagged that Atlas has no confirmation step before an irreversible act, and named
four: a nuclear launch, a surrender, dissolving a country, abandoning an alliance.

**Asked of the catalogue rather than taken on trust, three of the four are not
player-reachable at all** -- and that is the finding, held as tests rather than as a claim
here:

* **`nuclear_launch` is deliberately absent from `orders.PLAYER_GAMBITS`**, because a channel
  where a parser's reading of a sentence can put a warhead in the air is not a channel that
  should exist. It is proposed by the world simulator and by tracked nations; a player cannot
  order one however the sentence is worded, and `_undertaking_kind` refuses an exact
  `gambit_kind` of it.
* **Dissolving a country is no single act.** `peace.settle` already refuses a term set that
  would leave a power owning nothing, in as many words: *a state is not dissolved at the
  table, however many provinces it is asked for one at a time.*
* **Abandoning an alliance is not an order either.** A breach is *derived* from behaviour by
  the treaty layer -- you break a pact by doing the thing it forbade, and there is no button
  that says so first.

A confirmation for any of the three would guard a path nobody can walk. `test_the_three_
codex_named_that_are_not_reachable_still_are_not` fails the day one of them becomes
orderable, which is when its confirmation should be written.

### What is covered, and where the declaration lives

Every grave act the catalogue actually offers: **declaring war**, **settling a peace**, and
**standing formations down**. The declaration is `Capability.grave` -- and it is the
*sentence*, not a boolean, because a dialog that says "are you sure?" asks nothing and the
only kind worth interrupting somebody for says what the click will not give back. A boolean
would let an act be marked grave without anybody writing that sentence.

One line per grave order rather than one for the batch: "three of these cannot be undone" is
not something a person can act on, and *which* three is. Targets go through the same resolver
the order does, so what is confirmed is the row the engine would act on rather than the
string the player typed.

**The check sits at the jump**, because that is the moment the orders stop being a draft.
Everything before it is editable and costs nothing, and a confirmation about a draft is a
confirmation about a thing that has not happened.

### The line it deliberately does not cross

**Nothing here refuses an order.** Making acknowledgement a *requirement* would mean an order
proposed by a tracked nation or the world simulator had to carry a field invented for a
dialog -- the interface leaking into the rules about the world. A test holds the engine to
still accepting a grave order with no dialog anywhere in sight.

---

## 12. Three more numbers that account for themselves, and a guarantee about refusals

### The "why" inspector, extended

Three more values, chosen because each is a number a player acts on and cannot see inside.
Same discipline throughout: **nothing is computed for the occasion**, every figure is
re-derived by calling the function the engine calls, and the terms sum to it.

* **A bargaining position** was the one value that already carried its own breakdown.
  `leverage_between` has always returned four weighted components, each with a sentence
  saying what it is about, and every caller kept the total and threw the four away. So this
  is a translation rather than a derivation, and it is the shortest function in the module
  for that reason. One caveat, stated in the test rather than hidden behind a loose
  tolerance: `position` is the sum *rounded to four places*, so the terms sum to what the
  engine rounded and differ from the headline by under 5e-5.
* **War exhaustion**, term by term, in the order `_charge` applies them -- and the
  multiplication (fragility, what a shaky country's war costs it over a settled one's) is
  reported as the difference it made, for the reason `why_a_coup` reports its three that way.
* **An attribution tier**, which is a threshold on a contest between two services. Both
  halves are named -- what your counterintelligence can do against what their tradecraft can
  hide -- plus **how far the next tier is**, which is the question the tier itself cannot
  answer: a player looking at *suspected* has no way to tell whether that is one turn from
  *likely* or twenty.

`atlas why <nation> --against <enemy>` prints the pair that only means anything about two
powers, because a position and the exhaustion under it are read together or not at all.

### Partial-success alternatives, made structural

The first pass covered "the main refusal sites", which is a claim somebody has to keep true
by remembering. **It is now checked mechanically**: an AST scan over the whole engine fails
if any `Refusal` is constructed without an `instead` argument.

The rule is that the author *considered* it, not that there is always something to say.
`phrase` returns an empty string for an empty list on purpose -- a refusal that suggests
something also refused is worse than one that suggests nothing -- so an explicitly empty
alternative is a legitimate answer, and never having asked is not.

Forty-three sites had one. **One did not**: *you cannot back yourself*, which now names the
powers you could back instead.

That last one is worth being precise about, because it had been **deliberately excepted** on
the first pass -- the sweep listed it as "the one honest blank", on the argument that the
refusal already implies its own answer: anybody but you. Closed on this pass because
*implies* is work the reader does, and the branch directly below it already produces exactly
the sentence needed for a client that does not exist. The exception list is now empty, which
is a stronger guarantee than a list with a justification in it.

---

## 13. Doc-accuracy audit

Thirteen documents, checked the way Codex checked the gameplay handoff. Split deliberately
into the half a machine can be sure about and the half only reading finds.

### The checkable half: 278 claims, zero stale values

Every doc line of the form `` `NAME` is 0.05 `` is now matched against the constant that name
resolves to anywhere in the package, and **not one was wrong** -- including the four constants
I retuned earlier tonight. That is a better result than expected and it is worth stating
plainly rather than dressing up.

It is now a test rather than a script, so a tuning that moves a constant a doc quotes fails
on the day it moves. **Values only.** An earlier version also resolved every backticked
identifier and reported 268 unresolved names, nearly all of them right -- private methods,
dataclass fields, column names, event kinds, action types, unit templates, world ids quoted
from a real save. A checker with that many false positives finds nothing, because nobody
reads its output twice.

One stated hole: names repeat across layers. `RESTING` is 50 for a constituency and 22 for an
intelligence network, so the check accepts either value for either doc. Dropping repeated
names would stop checking exactly the constants the docs quote most; picking a module would
be inventing a claim about which was meant.

### The reading half: three real findings

**1. A source comment pointing at something that never existed.** `constituencies.py` said
"see `_ARGUED` below, which is a list of decisions rather than of numbers". There is no
`_ARGUED` and there never was -- the readings are module-level `Reaction` constants. The
war-loop handoff repeated the name. Both fixed.

**2. The count has been wrong since the day it was written.** The seven arguable readings
have been called **six** in four places: the design doc's heading, its status line, the
commit title (`11d632a`) and the handoff. The table has always had seven rows --
`breaking your own truce` landed in the same commit as the other six and was never counted.
Corrected in the doc rather than quietly, with a note saying so, because a count nobody
checked for a fortnight is worth knowing about.

**3. Two claims my own work tonight made stale.** `DESIGN_CONSTITUENCIES` described
`PER_TURN_CAP` as "the most one *turn's* reactions may move a group" and the occupation
mapping as "per turn, from the map". Both are per **month** and scaled by the span now. Fixed
where the doc claims to describe what is.

### And one place a doc is right where a check would disagree

`DESIGN_WAR_LOOP` is marked *"layer 1 of 6 built; layers 2-6 are proposals"*, and its
exhaustion formula is explicitly a **proposed** shape. Two of its terms differ from what
shipped: the casualty denominator is no longer `manpower_ceiling` (it is the army that took
the losses, weight 60), and occupation drifts toward a ceiling rather than adding a share of
one.

The proposal is left as written, because it is the record of what was argued for. A note
beside it now says what changed during the build and points at `engine/wareffort.py` for what
runs -- which is the honest fix for a document that is accurate about the past and misleading
about the present.

---

## 14. Coverage gap-fill, and the defect it turned up

Branch coverage over the engine, then the gaps filled where a gap was a *behaviour* nobody
had exercised rather than a line nobody had coloured.

**`waraims` was the weakest module in the war loop at 88%**, against 92-95% for its
neighbours, and the whole gap was in its **refusal paths** -- which are exactly the branches a
live model exercises. A world simulator writing aims produces malformed ones: a string where a
list belongs, a name the map does not carry, the same demand twice, six aims where five are
kept. The module states the standard itself -- *a power told nothing believes it is fighting
for something it is not, and will keep believing it for the rest of the war* -- so those
branches are a promise, and none of them had a test. **88% -> 96%.**

### The defect: a function kept to prevent drift, which nothing used

`wareffort.manpower_ceiling` says of itself that it is *"identical to the ceiling `budget`
refills toward... **one copy, so the two cannot drift**"*.

There were **two copies**. `budget.py` computed `max(10.0, population * 2.0)` inline and never
called it. Same arithmetic, so nothing was wrong today and nothing held them together for
tomorrow -- and the docstring asserted the opposite. Found by covering a function nothing
called, which is the argument for covering functions nothing calls. Budget calls it now.

### Three things I got wrong on the way, and what they were

Worth recording, because each was a test that would have passed while proving nothing:

* **Two tests claiming to cover ambiguity that covered "not found".** `"the north"` resolves
  to *nothing on this map*, which is a different branch from *could be either of these two*.
  Renamed to say what they test, and a real ambiguity fixture added -- two provinces named
  Westfeld and Westfell, which is realistic rather than contrived: a period map has both, and
  a simulator writing prose will sooner or later write neither exactly.
* **A limit test that passed when nothing hit the limit.** It asserted "*if* exactly five were
  accepted, a refusal exists". It now builds its aim list from what the engine will actually
  take, one at a time, because which aims a map allows depends on who holds what -- a
  hand-written list tests the fixture rather than the limit.
* **A claim about empty state that was backwards.** `standing_of` for a nation with no rows
  returns five standings at the resting point, not an empty list. That is the seeded-on-demand
  behaviour which gives every era and every authored scenario sensible starting politics for
  free, and I had asserted the opposite.

---

## 15. After the queue: the largest span bug of the night, found by reading a curve

The queue was done, so the fallback began with the pass my own work most warranted: **a long
campaign at yearly turns.** Everything tonight was verified at 30-day turns, and four of the
bugs fixed were specifically about long spans -- so the changed code had never been run at
scale in the shape it was changed for. 60 yearly turns, twice, on two eras.

It found this.

`assess_independence` runs inside the resolver's round loop like everything around it. Every
neighbouring call takes `days=round_days`. **That one call was handed the whole turn's span,
on every round.**

| turn length | rounds | momentum charged | should be |
|---|---|---|---|
| 30 days | 1 | 1.00 months | 1.00 |
| 90 days | 3 | 9.00 months | 3.00 |
| 365 days | 12 | **146.00 months** | 12.17 |

A year-long turn charged **twelve years of independence momentum for one year of time**.
Exactly right at a monthly turn, where `rounds()` returns one -- which is why no seed digest
moved, no cassette drifted, and nothing in 3788 tests noticed.

**Found by reading the curve rather than the code.** The Gathering Storm went from 74 living
powers to 238 in ten years, which is not a colonial system dissolving; it is a clock running
twelve times fast. After the fix the same run reaches 130 at twenty years and 237 at sixty --
and the monthly campaign's 74 to 119 over sixteen years now sits on the same line, which is
the pacing promise actually holding across pacings rather than being asserted.

The sharpest version of the shape, too: the correct value (`round_span`) is computed two lines
above the call and passed correctly to `resolve_turn` on the very next line. The call took the
one variable in scope that was wrong.

Determinism and invariants held at every checkpoint in both eras, before and after. The bug
was in *how much*, never in *whether it reproduces* -- which is exactly why a determinism gate
could never have caught it and a curve did.

### And the rule is checked now instead of watched

Reading a curve worked once and is not a method. The curve only showed this because
decolonisation is *visible in an aggregate*; a per-round call overcharging something quiet --
a stockpile, a technology, an intel network -- would have produced no curve to read.

So there is a gate: **every call inside the resolver's round loop that takes a length must
take a per-round one.** The loop's own variables say which is which, and it is parsed rather
than grepped, because `span=span` and `days=days` are both substrings of the right answer.

**It runs both directions**, and both are demonstrated against a real mistake rather than
assumed:

* inside the loop given a whole turn -> `assess_independence(span=span) at line 1139`, which
  is the actual bug;
* outside the loop given one round -> `assess_networks(days=round_days) at line 1348`, which
  is a once-per-turn system that would run twelve times *slow*.

The second direction is the one nobody would ever see. Overcharging shows up as a runaway
curve; undercharging just makes a subsystem quietly stop mattering, which is indistinguishable
from a balance choice. One stated exception, `resolve_turn`, which is handed the round count
and grinds the rounds out itself -- and a test that the reason in the exception list is true.

**One bug of my own on the way, and it is a classic.** The first version found the loop and
its enclosing function with two separate parses and then compared node identity between them.
Two parses of one file give objects equal in every way and identical in none, so the gate
reported every correct call as an error. One parse now, with the reason written where the next
person will look.

---

## 16. The pacing promise, measured at three pacings

The fix and the gates are worth only as much as the behaviour they produce, so the campaign
was run at a third pacing -- 90-day turns, where `rounds()` returns three and the old bug
overcharged by 3x rather than 12x.

| pacing | run | determinism | invariants | new powers per game-year |
|---|---|---|---|---|
| monthly (30d) | 200 turns x 2, three eras | identical | held | 0.72 |
| seasonal (90d) | 80 turns x 2, cold war | identical | held | 0.75 |
| yearly (365d) | 60 turns x 2, two eras | identical | held | in line, see below |

**That row is the promise.** Decolonisation now runs at effectively the same rate per year of
game time whichever way the player chooses to spend their turns -- 0.72 new powers a year at
monthly pacing against 0.75 at seasonal. Before the fix the yearly run produced 164 new powers
in ten years on a map that yields about eleven in fifteen at monthly pacing.

`resolve_turn` was checked on the same lens and is clean: it takes the round count and the
per-round span, loops the count, and passes the per-round span down. The one function that
legitimately holds both holds them correctly.

---

## Wanting Alex

Three things, none of them blocking anything that shipped.

1. **Compare two timelines** (Codex #8, the held half). What *is* a comparison? Two worlds at
   the same turn differ in thousands of rows and a full diff is unreadable. Which facts are
   "the campaign" — ground, wars, scoring, something else — is a design choice, not an
   implementation one.
2. **In-app branch switching** (Codex #8, the other held half). Not a design fork: a
   window-lifecycle one. Every overlay, the map applier and the pending orders hold a
   connection and a world id, and switching means tearing all of that down and rebuilding it
   live. Worth its own session.
3. **Recommendation #3.** See above.
4. **War exhaustion is a one-way ratchet** (item 8). A war in which nothing happens still
   reaches maximum weariness in a decade, and a phoney war and a bloodbath end up at the
   same number. Balance judgement, with scenario consequences.
5. **Durations are turn counts wearing month names** (item 7, sharpened in §15-16). Not
   "convert everything": **which durations belong to the world's clock and which to the
   player's**. A three-month ceasefire is the first; a grace period somebody is watching for
   is honestly the second. **Answer the victory condition first** -- `_DOMINATION_TURNS = 2`
   and `_HEGEMONY_TURNS = 3` are consecutive turns with monthly reasoning beside them, so at
   yearly pacing a player must hold domination for two years rather than two months. The pace
   control currently decides who wins and when.

And one small thing I noticed and did **not** change, because it is a catalogue question
rather than a bug: `Capability.available` takes a nation and no target, so any future
capability whose legality depends on the *pair* will have the same gap `against.py` had to
paper over for `declare_war` and `sue_for_peace`. Two is a coincidence; a third would mean
`available` should take the target.

---

# Alex's decided work (the second brief)

Everything under "Wanting Alex" that was a design question has been answered and built. The
five items below were built in the order given, each committed on its own.

## D1. World clock vs player clock — `4ad16cf`, `ce75b13`, `0b0f1f1`, `333b888`

Victory is held for a **stretch of calendar time** (28 days for domination, 59 for hegemony —
the shortest real February and the shortest real two months, so a short month cannot make a
condition unreachable). Armistices, treaty deadlines, sanctions and intel cooldowns now expire
on **world dates** (migrations 35 and 36, both additive; the old turn columns stay because a
pre-35 save has no date to convert *from*). UI grace periods stay in turns, and each remaining
turn count is honestly about the player's attention. War exhaustion was retuned: a quiet war
drifts to a **plateau near 32** and stays there, while casualties, occupation, mobilisation,
bombing, economic disruption and domestic opposition are what carry it to 100. A structural
gate holds the invariance: at a world date every pacing actually observes, the answer agrees.

## D2. `Capability.available` takes an optional target — `8405707`

The gap flagged at the bottom of the first brief, closed rather than papered over. The
predicate now takes `(conn, world_id, nation_id, target)`; `against.py`'s special-case table
for "already at war" and "not at war" is **deleted**, and the catalogue answers for itself.

## D3. National strategy / standing directives — `df0a361`

Six directives, kept in the **workspace** beside the watchlist and the draft orders: no
schema, no fingerprint, no rewind. They order the action list, the advisor's briefing and a
refusal's alternatives, and they **never act** — the list is the same length with a policy and
without, and `test_nothing_here_can_reach_a_diff` holds that at the source level. NPC
doctrines are explicitly *not* this and remain unbuilt.

## D4. Timeline comparison — `state/compare.py`, `ui/branches.py`, `atlas compare`

A **curated headline**, never a diff: territory, wars, treaties, governments, economy, armed
forces, nuclear balance, major facts, score. Read at the last day **both** lines reached,
because turn 30 against turn 25 holds two different moments beside each other and calls the
gap a consequence. **Read-only by copy** — a line that has to be read as it stood earlier is
copied to a temporary file and the *copy* is rewound; the player's saves are opened `mode=ro`
and come back byte for byte.

Three things the implementation turned up, all fixed:

* **Wars printed database keys.** `dvn vs usa` where a player is owed *North Vietnam vs
  United States*.
* **One missing table killed the whole comparison.** There are worlds on disk older than
  `leader`, and the first real two-world run crashed on one. Each heading now fails alone and
  says "not recorded in this save" — and `_score_line` no longer swallows a database error
  into "unscored", which would have claimed nobody had any points.
* **A scrolled section painted itself grey.** `QScrollArea.setWidget` turns
  `autoFillBackground` back on, so the section filled with the desktop palette's colour
  rather than showing the panel — a grey slab in one theme and a dark one in the other.
* **The worst of the four: a plain file copy could not see a world that was open.** An open
  world — and the line a player compares is the one they are playing — keeps its recent turns
  in the write-ahead log, not in the `.db` file. `shutil.copy2` of a live world gave a
  database with **no tables at all**, so every heading would have come back "not recorded"
  and the screen would have reported a campaign with no wars and no armies. It uses
  `export_world` now — the same `VACUUM INTO` `fork_world` already uses, which reads through
  the log and is legal from a read-only handle because it writes only to the destination. It
  passed every test before the fix, because a world a test has closed has already
  checkpointed its log; running it against two real worlds from the command line is what
  caught it.

## D5. Doc cleanup — `DESIGN_MAP_GAMEPLAY.md`

Recommendation #3 written up properly (it was a blank in the first draft), the compare half of
#8 moved to done, #9's two "structural" gaps rewritten now that D2 closed them, and the header
labelled: the map as first written was a snapshot at `718f9f0`, and the revision says where it
now stands.

## Still wanting Alex

Only **in-app branch switching** remains from the list above, and it was never a design fork —
it is a window-lifecycle one, worth its own session. The five held gameplay recommendations
(#1 intent-to-plan, #5 projects, #7 crisis mode, the deeper half of #6, #10's
authoring-from-nothing half) are untouched and still flagged, as instructed.
