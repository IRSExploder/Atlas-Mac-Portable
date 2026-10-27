# ATLAS — review handoff: the three gameplay batches

**For:** Codex, reading this against gameplay proposals #1 (the war loop), #2 (domestic
constituencies) and #3 (active intelligence).
**Prepared:** 2026-08-14. **HEAD:** `f7bdff1` on `master`.
**Tests:** 3353 passing, 1 skipped, 1 deselected (`live`), ruff clean over `src` and `tests`.
**Schema:** 34. **`atlas release-audit`: PASS**, 8/8 gates.

> **Sixth of six, and the one that supersedes the middle of the set.**
> [`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) covers everything before the consolidation;
> [`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md) the consolidation;
> [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md) the 1972 era;
> [`REVIEW_HANDOFF_ROADMAP.md`](REVIEW_HANDOFF_ROADMAP.md) the engine batches;
> [`REVIEW_HANDOFF_UI.md`](REVIEW_HANDOFF_UI.md) the eight screens.
> [`REVIEW_HANDOFF_WARLOOP_CONSTITUENCIES.md`](REVIEW_HANDOFF_WARLOOP_CONSTITUENCIES.md)
> was written at schema 30 and covered batches #1 and #2 plus one night's work; **this
> document extends it through the whole of #3 and everything since, and where the two
> disagree this one is current.**

> **Three things to know before anything else.**
> 1. **All three batches are built.** #1 and #2 are complete against what was scoped; #3 is
>    complete including the layers that were designed-but-unbuilt at the last handoff.
>    What is *not* built is listed honestly in §7 and is mostly the deliberately-deferred
>    half of #2's friction table plus post-war occupation regimes.
> 2. **Migrations 19 and 20 remain the only two that invalidate saves.** Schema went
>    24 → 34 in this stretch, every step additive.
> 3. **The one thing worth arguing with is in §4.** Whether a displaced-centre estimate is
>    really unrecoverable is the assumption the whole intelligence subsystem rests on.

---

## 1. Persistent world facts — `d340644`

**What it does.** A `world_fact` table the engine mints from events: who was invaded, who
broke a signature, who was caught running an operation. Facts carry a subject, a predicate
from a closed vocabulary, an object, the turn, and the event that minted them. They are
readable in briefings and citable by later layers.

**The load-bearing distinction, and it is the whole design.** A *fact* is something the
world remembers. An *effect* is something the engine computes. Germany invaded Poland in
1939 is a fact; what that does to Germany's economy, borders, units or unrest is not stored
next to it — it is recomputed, every turn, by the layers that own those numbers.

Concretely: **nothing on the fact path can reach the economy, the borders or the units.**
A fact cannot be a diff against `nation.economy`, cannot move a region's owner, cannot
raise or destroy a unit. It is inert with respect to mechanics by construction, and that is
what makes it safe to mint liberally — a wrong fact is a wrong sentence in a briefing, not a
wrong border. `attribution`, `exposure` and the peace layer **mint** facts alongside their
mechanical state — they call `record_fact` and compute their consequences themselves, from
their own tables — and it is briefings and naming that **consume** them, through
`visible_facts` and `names_for` in `engine/views.py`. Nothing reads a fact back as evidence
for a mechanic.

**`world_fact` is `ENGINE_ONLY` and has no model channel.** Recorded as such in the
engine-only door test rather than left implicit. The "anything funnel" — a described action
becoming a remembered fact — is designed and unbuilt; see
[`DESIGN_WORLD_FACTS.md`](DESIGN_WORLD_FACTS.md).

**Tests:** `test_the_world_remembers_what_was_done_to_it.py` (26).

---

## 2. The war loop — Codex gameplay #1

Six layers, each additive and each rewindable.

| layer | what it does | commit |
| --- | --- | --- |
| war aims | structured per-belligerent aims; six kinds; `annex` is tested against the **owner**, not the holder | `821f4d2` |
| exhaustion + ledger | casualties charged from the diffs actually written, not from a narrative; five named bands | `ab3893d` |
| the ledger has no door | `war_effort` made engine-only, so a power cannot declare itself un-tired | `89d6bce` |
| leverage | ground / weariness / force / aims, antisymmetric by construction | `f7a11de` |
| tuning | three dials measured against a real war rather than argued in the abstract | `d650a29` |
| terms + acceptance | seven term kinds, an enforceability review, engine-only acceptance | `32459f0` |
| reachability | `sue_for_peace` made model-reachable, and the free-white-peace fix | `895c213`, `a1128d4` |
| armistices | ceasefires that can be broken, through the existing breach machinery | `a4684e6` |
| dissolution | a country is not dissolved one province at a time | `e9d488f` |

### The split that matters

**The model proposes a term set; the engine refuses what cannot be carried out
(`review_terms`); and the engine alone decides acceptance (`acceptance`).** Neither model
has a vote on whether a peace is signed, for exactly the reason neither decides a battle. A
model may demand a province it does not border and has never occupied — `review_terms`
refuses it with a reason, and **the whole offer falls with it** (`peace.py:617`). Dropping
the bad clause and signing the rest would hand the proposer a different treaty from the one
it made, and in the worst case a free white peace: every demand refused, the peace signed
anyway. The refusals go back with their reasons and offering again is cheap.

### Live proof

**PT42, Cold War era, real models.** `gpt-5.6-terra` playing West Germany read its own
briefing — leverage against it, exhaustion band, the aims declared on it — concluded it was
beaten, and **sued for peace, ceding six provinces rather than fight on.** The Soviet
Union's counter-demand overreached and was refused by the engine. Three separate wars
produced three separate treaties; the separate peace held. A rewind reproduced turn 4
exactly.

The wiring that made this possible is `895c213` — before it, `sue_for_peace` existed and no
model could reach it. `a1128d4` is the line no test executed, found by the first live turn:
a white peace was free.

### Tuning, measured rather than argued — `d650a29`

Three dials moved against a real war: the **occupation ceiling** (holding ground stopped
being an unbounded leverage source), **leverage now feels exhaustion** (a winning side that
is exhausted cannot demand as though it were fresh), and **air fighting entered the
casualty ledger** (an air campaign had cost nobody anything).

**Tests:** `test_a_war_can_have_a_point.py` (25),
`test_a_war_costs_something_to_go_on_with.py` (21),
`test_a_peace_that_says_what_it_costs.py` (38),
`test_a_truce_that_can_be_broken.py` (24).

---

## 3. Domestic constituencies — Codex gameplay #2

Five groups per nation — **army, industry, people, patriots, establishment** — chosen
because they give *different answers to the same decision*. Universal rather than
per-regime: a regime taxonomy does not exist in the schema, and a per-era set makes a world
unreplayable across eras.

| piece | what it does | commit |
| --- | --- | --- |
| scoping | the argument for the parts deliberately not built | `bf96777` |
| foundation | the store, lazy rows, **seeded from each nation's own stats** | `976542c` |
| C4 | the nationalists gate peace acceptance | `ec40a03` |
| C1 | coup risk reads the **army and the establishment specifically**, not stability | `f0396df` |
| mappings | the six readings that were arguable, decided and written down | `11d632a` |
| C3 | strikes cost revenue; hostile industry slows the yards | `21977bc` |
| C2 | the removal channel: election / revolution / coup | `839d6ae` |
| C5 | the constituency inspector screen | `2f73fdf` |
| doc | what was decided and what is still Alex's | `11744c4`, `623c336` |

**Everything is engine-derived.** `constituency` is `ENGINE_ONLY`: a power that could write
its own standing could buy the army's devotion instead of earning it. Rows are lazy and
seeded from the nation's own numbers, so a world that has never had a turn carries no rows
and answers identically to one that has.

**Numbers worth checking.** C4: at the canonical lost war, patriots at 72 sign both
provinces away and at 88 the same offer is refused. C1: on the three seeded maps only
1 / 6 / 6 nations out of 74 / 148 / 242 are touched, and the base rate is unchanged. C3:
`FRICTION_FROM` was moved to 40 after measuring that at 50 it slowed the yards of 232 of 242
modern-day nations *at the resting point* — friction that applies to everybody is not
friction.

### The unmapped-action mappings — `11d632a`

The foundation mapped only what was not in doubt and left six actions unmapped rather than
guess. Each now has a reading, gathered in one block with **what was chosen and what the
other reading would have been**, so overruling one is a one-line edit:

- **Austerity → arrears.** A government has no austerity lever in this game, so the thing
  that exists is mapped instead. Everyone owed is angry and nobody is pleased.
- **Sanctions → rally.** The nationalists consolidate; industry, which actually pays, is not
  consoled. *Flagged:* the hardship reading is what `unrest` already charges, and charging
  it twice is charging it twice.
- **A lost battle** stings the army, more when it was fought on ground you were holding.
- **Peacetime rearmament** splits a country differently from mobilisation — no enemy at the
  gate to justify the cost.
- **Occupation** is read from the map every turn rather than from an event, both directions,
  heavier when it is your own ground: losing is not the negative of taking.
- **Tearing up your own ceasefire** pleases the people who wanted the war back and costs you
  everyone who wanted it over.

All six are **flagged for Alex's veto** and each is a single line. The economic three
(investment / sanctions / arrears) all arrive as `economic_reform`, so the mapping reads the
*money* — which way the economy moved, on the diffs the event itself caused — rather than
the prose. A sentence can be reworded; the ledger cannot.

### The C2 accountability proxy — still the one decision waiting

**Nothing in the schema knows what kind of state a nation is.** Every government on all
three shipped maps reads `came_to_power = 'seeded'`, and 226 of 243 nations share one
stability and one legitimacy. Rather than build a regime system, the proxy is the **leader's
title** — President / Prime Minister / Bundeskanzler on one side, Fuehrer / Duce / General
Secretary / Supreme Leader on the other — two tuples in `leaders.py`, no schema, one line to
overrule. It names 6 / 12 / 13 accountable governments per era; 63 of 74 in 1936 are titled
"the government" and default to force.

**Tests:** `test_a_leader_with_somebody_to_govern.py` (21),
`test_the_day_the_constituencies_start_to_bite.py` (49),
`test_the_screen_that_shows_who_you_have_to_govern.py` (13).

---

## 4. Active intelligence — Codex gameplay #3

Seven layers. The foundation was built first by instruction, then the rest in order, each
committed and each reported before the next began.

| layer | what it does | commit |
| --- | --- | --- |
| foundation | networks per (viewer, target); estimates with a confidence band | `d35b4dd` |
| I1 discovery | graded reveals — what a service brings back depends on how good it is | `8588ffb` |
| I3 attribution | tiers: none / suspected / likely / proven | `5528464` |
| I2 counterintelligence | degrade a rival's service, then **feed it a confident lie** | `808ccb0` |
| I4 exposure | operations that get blown, with computed fallout | `983c3bb` |
| I5 covert proxy support | backing a side without saying so | `e94ec44` |
| I6 sharing clauses | treaties that pool what each side believes | `baaddae` |
| arsenals | warhead counts estimable at the widest fog | `f535db5` |

### Foundation — `d35b4dd`

**The hole it closes.** `visibility._band` took a rival's *true* `military_strength`,
divided by the viewer's own and named the ratio: coarse, but exact, identical for every
observer, and free. A landlocked minor read Berlin exactly as well as Britain did.

**The guarantee.** `Estimate` has no `true_value` field, and the band is deliberately **not
centred on the truth** — a symmetric interval hands the real number to anyone who averages
the ends. The centre is displaced by a seeded bias first and the interval opened around
*that*. Networks are per (viewer, target), because intelligence is a relationship a country
has with another country, not a stat it has.

### I1 discovery — `8588ffb`

Espionage was a flat 45% coin and an all-or-nothing answer. Now both the chance and the
**resolution** come off the network: `nothing / existence / nature / detail / full`, and only
an `excellent` service buys the real thing. At every other tier the service returns an
engine-authored sentence about a hidden event it could not read, and the event stays hidden.

### I3 attribution — `5528464`

Four tiers, accumulated from evidence over time. **`proven` is independent of exposure** —
patient counterintelligence can reach certainty *quietly*, and a state can be certain who
burned its shipyard and never able to say so. Both directions are tested. A model cannot
write a tier: a power that could set its own attribution would manufacture a casus belli by
asserting guilt, which is the one claim the whole subsystem exists to refuse.

### I2 counterintelligence — `808ccb0`

Two regimes, and the second is the interesting one. Below `DECEPTION_AT = 55` a sweep
**widens** a rival's band and its reported confidence falls with it — it knows less and knows
that it knows less. Above it, the defender has enough control of what leaves the country to
**choose what the rival believes**: the band comes back tight and the *centre* is moved.
A fed answer and an earned one are indistinguishable from inside. Model-reachable —
`invest_counterintel` is in the answer schema and pre-flighted.

### I4 exposure — `983c3bb`, with the measurement

**The fallout is computed, not gated.** A switch — exposed, therefore war — prices a bribe
exactly like a plot against a head of state and makes every covert act a coin flip between
nothing and a catastrophe. The magnitude is a **product**: `importance × secrecy × mismatch`
— what was done, how hard the victim was working to prevent it, and how far out of its depth
the operation was. It scales regard and legitimacy continuously, always mints a fact, and
the casus belli falls out of the shape rather than out of a second rule.

**Measured before the constants locked, and the first measurement was wrong twice.**

1. Mismatch was computed against *effective* tradecraft — raw minus the sweep — so the sweep
   drove both it and guardedness, and the "product of three factors" was one factor squared.
   It now measures against the **raw** service, which asks a different question: not how hard
   they were trying to stop you, but how far out of your depth you were.
2. **A bribe bought a war.** Magnitude 55 against a well-guarded nuclear power — precisely
   the flat-gate behaviour the design exists to avoid. `CASUS_BELLI_NEEDS_IMPORTANCE = 0.70`
   excludes the whole espionage family structurally: buying ministers and stealing secrets
   top out at 0.65.

**The curve after both corrections** (`CASUS_BELLI_AT = 42`): a bribe nobody was guarding
costs 3 and a shrug; sabotage nobody was guarding costs 7; sabotage on a nuclear power that
had invested nothing is still not a war; the same act against a victim with 60 in
counterintelligence **is**. Exposure chance runs 1%–35% a month and a trail goes cold after
twelve turns, so tradecraft is worth something and the layer is not a clock. **Alex approved
this curve as-is.**

### I5 covert proxy support — `e94ec44`

`support.py` already priced advisors and intelligence as "the deniable ones" and wrote every
shipment `PUBLIC`, so there was nothing to deny. `declare_support(..., covert=True)` writes
a SECRET event shaped like an operation — patron as actor, client as beneficiary, the
client's enemies as targets — which is the triple `attribution._open_acts` already
recognises, so the tier climbs while the backing is sustained. **Deniability buys time, not
immunity**, and never buys belligerence: nothing on this path can reach `relation.at_war`.

### I6 intelligence-sharing clauses — `baaddae`

A new `intel_term` table hanging off `commitment`, because `commitment_term.clause` is a
closed CHECK that SQLite cannot widen without a table rebuild — the same constraint the peace
layer hit, answered the same way. **What crosses a clause is the ally's *estimate*,
recomputed from the ally's own network.** There is no path by which a treaty transfers the
true figure, because nobody on either side of it has the true figure to transfer. Better
means narrower; an ally who knows less than you tells you nothing. Reachable at the
negotiating table through the diplomacy extraction, bounded twice like a cession.

### Arsenals estimable — `f535db5`, and the MAD split

Warhead counts join the estimate machinery **at the widest fog on the map**, on the
importance × secrecy logic: `ARSENAL_MIN_ERROR 0.18` / `ARSENAL_MAX_ERROR 1.10` against
`0.05`/`0.34` for army, economy and industry. Below `workable` the answer comes back in
**words** — *a handful to hundreds* — because `0-380` is a pair of numbers a staff will
average into a plan and a phrase is not. The subject appears for every country or for none,
gated on whether the nuclear age has arrived *anywhere*, because a line that showed up
exactly when the target was armed would hand over the programme for free.

**The split, and it is the point of the layer.** `nuclear_balance_lines` and
`nuclear_rules_block` reason on `estimated_retaliation` — your own count you know, theirs you
estimate — so a nation can be **deterred by warheads that are not there**. Measured on the
cold-war map, USA at war with the USSR and North Vietnam, throwing about 43 of its own:

```
  sov counter_intel = 0    dvn 0-5 + sov 26-46  →  "absorb something like 38"
  sov counter_intel = 95   dvn 0-5 + sov 36-66  →  "absorb something like 54"
                           (the truth is 36 either way)
```

**The quoted total is a sum of midpoints over every power that could answer**, not the
midpoint of one band — an earlier draft of this document showed a single rival's band beside
the total and the two did not reconcile, which read as an arithmetic error in the engine and
was an error in the example. The engine's own figures are above.

The deceived briefing flips its own verdict, from *you would come out with more left* to
*there is no order of operations here that leaves your capital standing*.

`launch`, `second_strike` and `survivability` are untouched and read the `arsenal` row. The
warheads that really exist are the ones that fly. Guarded by **reading** the launch path as
well as testing it, because a resolution that reached for a band would still return a number.

**Tests:** `test_a_service_that_reports_what_it_believes.py` (22),
`test_what_a_service_brings_back_depends_on_how_good_it_is.py` (13),
`test_who_the_victim_believes_did_it.py` (17),
`test_a_service_that_can_be_defended_against.py` (21),
`test_an_operation_that_was_blown.py` (20),
`test_backing_a_side_without_saying_so.py` (14),
`test_an_agreement_to_pool_what_you_believe.py` (19),
`test_nobody_knows_how_many_warheads_you_have.py` (29).

---

## 5. Cross-cutting principles

These hold across all three batches and are worth reading as one thing.

**Model proposes, engine owns truth.** Every layer here has the same shape. A model may
declare war aims, sue for peace, run an operation, invest in counterintelligence, back a
client quietly, or agree to share what it sees. No model may write what a war has cost, what
its own constituencies think of it, what a rival's arsenal is, who is guilty of what, or
whether a peace was accepted. The tables that carry those are in `ENGINE_ONLY`.

**"Built but unreachable" is a shipped defect, and there is now a pre-flight for it.** It
happened twice before it was caught as a pattern: **proxy war** was built and no model could
declare support; **the peace loop** was built and no model could sue for peace (`895c213`).
Both were found before a live run was wasted on them. Every layer since is pre-flighted —
the answer schema is inspected for the channel, and a test asserts the turn engine actually
*reads* it. `test_a_mechanism_a_model_cannot_reach_is_not_shipped.py` carries a `DOORS` entry
per engine-only table saying how a model reaches it **or why nothing should**, and fails on
any new engine-only table with no entry.

**Determinism and exact rewind, through one pipeline.** Everything is a `Diff` under
`apply_batch`: record → apply → validate → keep or roll back. Randomness is `derive(seed,
world_id, turn, call_key)` where the key identifies the *decision*, never call order — so the
same question asked twice in a turn gets the same answer and a rewind reproduces the same
wrong guess. That is the difference between fog and a coin flip. A property test
(`6dfe2af`) drives twelve seeded random histories over the tables added since the war loop,
rewinds each to four earlier turns, and compares digests.

**It did not cover all of them until `006baff`, and that is worth reading as the pattern
rather than the incident.** `attribution`, `exposure` and `intel_term` arrived after the
file was written; each got an example-based rewind test and none joined the randomized list,
so three layers were covered for the sequences somebody thought of and not for the ones
nobody did. The list is now derived from the migrations and fails until a new fingerprinted
table is fuzzed too.

**The auditor is the safety rail.** `atlas release-audit` runs eight gates — packs exist,
packs verify, schema parity, determinism (each era built twice), 13 invariants × 3 eras,
scenario audit, cassettes, and the full suite with ruff. Every layer in this document landed
green.

**Reuse, not re-derivation.** The persistent-facts machinery is minted by the war loop and
read by exposure and by peace acceptance. Attribution's `_open_acts` recognises covert proxy
backing without widening any kind list. Exposure prices a blown backing on the same scale it
prices a blown operation. The nuclear map layer and the MAD briefing read the *same*
`estimated_retaliation`, so a screen and an adjudicator cannot drift apart —
`within_delivery_range` was refactored onto `deliverable_regions` for the same reason.

---

## 6. Hardening and polish since

### The overnight bug scout — `b97596b`, `dd76b2b`

| severity | finding |
| --- | --- |
| HIGH | **A covert operation announced itself through the politics.** A successful covert gambit writes a SECRET `unrest` event; `unrest` is a mapped constituency reaction; the layer wrote the movement as a PUBLIC diff citing its own PUBLIC narrative event. INFO-002 could not catch it — the cited cause really *was* public — so the secret was laundered through one hop. Movements now take the narrowest scope of any cause. |
| MEDIUM | **A siege of your own ground**, caught live by PT43. Opening one was already refused; nothing closed one that *became* self-besieging, and `captured` requires `held != attacker`, so it could never end. |
| MEDIUM | **A latent determinism hole** — the `because` written to a fingerprinted column was chosen by whichever reaction arrived first, over three `GROUP BY` queries with no `ORDER BY`. |
| MEDIUM | **Air, naval and nuclear battles named nobody** (`dd76b2b`), so a whole air campaign moved no politics and could not be attributed. |
| LOW | **Having nothing was perfectly observable** — multiplicative noise on a true zero gives a band of 0-0, so disarmament was the only perfectly visible act in the game. |

Probed and found sound: exhaustion bounds under absurd casualties, leverage antisymmetry at
the extremes, a broken armistice really resuming the fighting, every advertised capability
having a handler, no dead enum members.

### The 110-turn determinism campaign — `d3266b5`, fixes in `484f2c8`

**Determinism passed: 110 turns reproduced exactly, twice over.** Two things did not stop,
and both were one-line changes with map-wide consequences, so neither was guessed at — they
were measured before and after with the same campaign.

1. **Economic runaway.** Economy 9.0× and industry 12.1× over nine game-years. The drift
   toward the ceiling was always a converging pull and was never the problem; the *ceiling*
   is linear in industry and development, both bought with a treasury the economy feeds, and
   nothing damped the loop. `supportable_economy` now saturates above a knee — and the knee
   (120) sits above every nation on every shipped map, so the transform is the **identity**
   for all of them on the day the world is built. The early game is not merely materially
   unchanged, it is bit-identical. After: France 1.09× and moving −0.2% in the last stretch.
2. **A century of peaceful building was a century of rising coup risk.** Founders' stability
   56.5 → 40.5 over eighty turns with no wars and nobody bankrupt. Every applied diff was a
   construction charge: `autonomous_investment` develops every unattended nation every turn
   and picked from exactly the three projects whose stability effect is negative — housing,
   health and land reform were not reachable by an unattended power at all. It now prefers
   the restorative three under strain (prepending, so nothing that could be built before
   cannot be built now), and `collect_revenue` heals: one-directional, toward a resting
   point, gated on genuinely being at peace. A country at war heals nothing.
   **57.8 → 37.2 became 57.8 → 52.9**; mean coup risk **0.58% → 3.88% became 0.46% → 0.80%**
   against a base rate of 0.5%.

   Two thresholds moved during the measurement and the reason is worth keeping: at a resting
   point of 50 — the danger threshold itself — peace converged on 48.4 and *tripled* coup
   risk, because the equilibrium landed inside the band where risk starts. At a strain
   trigger of 50 the two mechanisms made a country oscillate across the line rather than
   above it. Both sit at 55. All four constants are flagged TUNABLE; the tests pin the
   *shape*, not the numbers.

### Other hardening

- **Tapes** — PT44 re-recorded, replays 12/12 exact (`2ba8cbc`). Finding worth knowing: the
  world a tape was recorded against is *part of the tape*, and replaying against the wrong
  one mismatches on turn 1 with no misses, which reads exactly like an engine divergence.
- **A standing guard instead of a cleanup diff** (`a2381d9`) — nothing was provably dead, so
  every closed CHECK value must now be written or listed as reserved with a reason.

### Map overlays

| overlay | commit |
| --- | --- |
| supply reach, and where it stops | `eaf7bc1` |
| **nuclear reach & retaliation risk**, drawn from the *estimate* | `ece5fee` |
| diplomatic attitude toward you | `8a1ed29` |
| war aims and the fronts they imply | `27f2691` |

The nuclear overlay is the one to look at. A risk map read off the `arsenal` table would have
counted every warhead on earth exactly, on a screen, for free — the leak the whole
intelligence subsystem exists to close. It reads `estimated_retaliation`, so it is as good or
as deceived as the service behind it, and the reading is a **share of the exchange** against
your own throw weight rather than a league table: scaling against the biggest arsenal on the
map would have been blind to deception, since the rival stays biggest either way.
The war-aims overlay needed a new engine derivation, `waraims.implied_regions` — three of the
six aim kinds name a province and three do not, and the three that do not are still about
ground.

### UX

| item | commit |
| --- | --- |
| search across provinces, powers, events, agreements (Ctrl+F) | `19a6624` |
| pinnable watchlist, and `state/workspace.py` | `eb108aa` |
| draft-order persistence between sessions | `9363761` |
| unit-type composition in the marker tooltip | `f7bdff1` |

Search obeys the same visibility rules every other view does: events filtered by exactly the
predicate `state.visibility` uses, provinces by `map.query.known_regions` — made public so
the fog has one definition rather than two. Pins and unspent orders live in a JSON sidecar
**beside** the save: there is no code path from a preference to a digest because they are not
in the same file.

**Overlays and screens are read-only, and every one asserts it** — the fingerprint is
unchanged by rendering.

---

## 7. Open and flagged

Honest list. None of these is blocking; each is a decision rather than a defect.

### Waiting on a decision

- **The STAKES exposure-importance tweak — deferred, deliberately.** `importance_of` reads
  the event's *kind and target*, because the sharper signal — the minor/serious/grave stake
  the actor actually committed — is charged to the treasury and never recorded on the event.
  One line of change to record it. Alex approved the current curve and asked for this left
  flagged rather than built.
- **The colonial-empire stability bleed — a real question, not a bug.** After `484f2c8` the
  founders recover to 52.9 over 110 turns, but *excluding the colonial holders* they recover
  to 55.6. The gap is entirely empire. **Is idle empire meant to be an active-management
  drain?** If yes, this is the mechanic working and wants a name and a lever. If no, it wants
  damping. Nobody has decided, so nothing was changed.
- **`SHARING_KINDS` is a balance call.** Only `defensive_pact` and `alliance` may carry an
  intelligence-sharing clause, on the argument that a non-aggression pact is a promise not to
  fight and a trade agreement is about cargo. Widening it is one line.
- **The C2 accountability proxy** (§3) — the leader's title, standing in for a regime type
  the schema does not have.
- **The six constituency mappings** (§3) — each decided with its alternative written down.

### Working as designed, but worth knowing

- **Observer-scoped overlays are sparse on a fresh world.** `known_region_ids` is
  presence-based — what you hold, what you occupy, what borders it, where your forces stand —
  so at turn 0 the USA knows 29 of 451 regions and the attitude and nuclear layers are
  correspondingly thin. That is the fog working. It was kept rather than special-cased
  because the alternative leaks: colouring unseen ground by its holder's attitude announces
  who holds it. If contact should start wider, that is a seeding decision, not a layer one.
- **`world_fact` has no model channel** (§1), and the anything-funnel is unbuilt.
- **The PT37/PT38 control pair is gone.** PT37 was the control half of the support-cost pair;
  re-recording it would have destroyed what made it a control, so the pair no longer has a
  live half.

### Designed and not built

Each has a design doc with the reasoning, so none of this needs re-deriving:

- **C3's other three halves** — the army (slower mobilisation, understrength units),
  patriots (war aims that cannot be dropped) and establishment (repudiated signatures)
  friction. [`DESIGN_CONSTITUENCIES.md`](DESIGN_CONSTITUENCIES.md).
- **Post-war consequences beyond terms** — occupation regimes, reconstruction, revanchism as
  a standing state. The facts layer is the seam they hang off.
  [`DESIGN_WAR_LOOP.md`](DESIGN_WAR_LOOP.md).
- **Intelligence has nothing designed-and-unbuilt left** — all seven layers shipped, plus
  arsenals. [`DESIGN_INTELLIGENCE.md`](DESIGN_INTELLIGENCE.md) is kept as the proposal it
  was, with the outcome marked per layer and a commit table, so what was argued for and what
  shipped can be read against each other. Its §5 lists the two remaining items (the STAKES
  sharpening, and a live proof).
- **The fact funnel.** [`DESIGN_WORLD_FACTS.md`](DESIGN_WORLD_FACTS.md).
- **Delegation and standing instructions** (your later item) — untouched.
- **A live intel proof.** Every intelligence layer is tested and demonstrated against staged
  worlds; none has yet been driven by real models over a multi-turn run the way PT42 drove
  the peace loop. That is the next thing worth spending a live run on.

---

## 8. Build state

**Tests:** 3353 passing, 1 skipped, 1 deselected (`live`). Ruff clean over `src` and `tests`.

**Schema: 34.** Migrations 24 → 34, all additive:

| # | what it added |
| --- | --- |
| 24 | a what-if should not cost you the line you were playing |
| 25 | war aims |
| 26 | world facts |
| 27 | the war-effort ledger |
| 28 | peace terms |
| 29 | constituencies |
| 30 | intelligence networks |
| 31 | attribution |
| 32 | the counter-intelligence column |
| 33 | exposure |
| 34 | intelligence-sharing clauses |

**Save invalidation: still only 19 and 20.** Nothing in this stretch invalidates a save.

**Fingerprints.** The seed digest covers the *name* of every fingerprinted table whether or
not it has rows, so each migration that registered a new table moved every built world's
digest by the width of one table name — 25, 26, 27, 28, 29, 30, 31, 33 and 34 each did, on
tables that are **empty in all three shipped eras**. This is harmless for the reason recorded
in `state/bootstrap.py`: digests are only ever compared within a schema version. Schema 32
added a *column* to a table empty in every shipped era and moved nothing. Current era
digests at schema 34:

```
  gathering-storm  08de695447dc80725a6a81faae039805
  cold-war         1039a4d398a6b60f81110f2bf7d67616
  modern-day       1be2fb1c23262d506aad5aa3e33dbf74
```

The `484f2c8` balance fix is the one change in this stretch that could have moved a digest
for a *material* reason and does not: `supportable_economy`'s knee sits above every nation on
every shipped map, so the transform is the identity at seed and the built worlds are
bit-identical.

**`atlas release-audit`: PASS** — 8/8 gates green at `f7bdff1`.

---

## 9. Where to look first

If you have time for four things:

1. `engine/intelligence.py` — `estimate_of`. Whether the displaced-centre construction is
   really unrecoverable is the assumption the whole subsystem rests on.
2. `engine/exposure.py` — `importance_of` and the constants around `CASUS_BELLI_AT`. The
   shape is the design; the flagged mis-scaling is named in the docstring.
3. `engine/constituencies.py` — the argued mappings block. Six judgements, each with the
   reading not taken. Overruling one is a one-line edit.
4. `engine/peace.py` — `acceptance` and `patriot_stiffening`. The bounds are the design, and
   this is where batches #1 and #2 actually meet.
