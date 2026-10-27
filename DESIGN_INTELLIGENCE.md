# Active intelligence and covert operations

**Status.** **All seven layers are built and committed**, plus arsenals-estimable, which
Alex settled after the six. What survives here is the design reasoning for each, kept
because the arguments are worth re-reading and the seams are worth remembering; the
unbuilt items are named as such in section 5.

**The rule everything here is shaped around:** intelligence **reveals real state
imperfectly**; a model **never invents what is secretly true**. The engine owns the
truth and owns what each nation may believe about it. Every sub-piece below states
its side of that split explicitly.

---

## 1. Scoping: what is already here

### Secrecy is structural, and it already works

There is no "hidden flag" anywhere. A thing is secret because the **event** that
caused it is, and rule **INFO-002** refuses any diff more observable than its cause.
So a hidden atomic programme needs no hiding mechanism of its own: its events are
`secret`, and everything that follows from them inherits that scope.

| piece | where | what it does |
| --- | --- | --- |
| `event.visibility` | schema | `public` / `bilateral` / `secret` |
| `event_visibility` | schema | per-nation grants — the only way to see a non-public event |
| INFO-002 | rules | a covert cause cannot produce overt state |
| `state/visibility.py` | reads | **every function takes a viewer**; there is deliberately no `get_all_events` |
| `state/referee.py` | reads | omniscient, quarantined, every function named `all_*`, and a test asserts nothing but the world-sim view imports it |
| `research.Technology.secret` | catalogue | records that a programme was *meant* to be hidden; the event visibility is what hides it |

**The omniscient reader discipline is real and I confirmed it holds.** Referee reads
are in their own module with an import test guarding them; the estimate machinery
does not import `referee`, and takes the true value as a parameter from a
viewer-scoped caller rather than reaching for it.

### What a nation currently "knows"

Assembled in `views.py` from `visibility.*`: `nation_self` (its own row, exact),
`other_nations` (a per-rival brief), `own_regions`, `standing_commitments`,
`fronts_of`, and `known_events` (public + own + explicitly granted, ranked by
salience). `_powers_worth_naming` then trims the roster to the states a nation
would actually have a view about — on the 2026 map that is the difference between
a briefing and ten thousand characters of `Aland: regard +0, far weaker than you`.

### The hole the foundation closes

`visibility._band` took a rival's **true** `military_strength`, divided by the
viewer's own, and named the ratio. Coarse — five buckets — but **exact, identical
for every observer, and free**. A landlocked minor with no attachés abroad read
Berlin exactly as well as Britain did. There was no such thing as *being better
informed*.

### How espionage works today

One order (`orders._espionage`), a flat **45%** roll on the seeded stream, and on
success a single `event_visibility` grant for the target's oldest unseen secret
event. No capability, no build-up, no per-target notion of reach — and the odds are
the same for a great power and a city-state. The comment on it is careful about
keying (`espionage:{actor}:{target}:{report_id}`, so a second attempt at the same
report is honestly futile rather than secretly certain).

**That is exactly where networks hook in**: the flat 0.45 becomes a function of the
network, and the network is what a nation invests in.

---

## 2. Built: the foundation

`src/atlas/engine/intelligence.py`, schema 30 (`intel_network`).

**Networks are per (viewer, target)** — intelligence is not a stat a country has, it
is a relationship it has with another country. Britain reading Berlin well and Tokyo
badly is the ordinary case and a single number on `nation` could not say it. Five
bands: `blind / patchy / workable / good / excellent`. Rows are written lazily and a
pair with none reads a **seeded default derived from the relationship** — you watch
your neighbours (+14), your enemies harder (+18), an ally lets you in a little (+8),
and a rich state can afford attachés everywhere (up to +16 on the economy ratio).
243 × 243 is fifty-nine thousand rows of nobody watching anybody.

**Estimates.** `estimate_of` takes the true figure and returns a band. Error runs
from `MAX_ERROR = 0.34` at blind to `MIN_ERROR = 0.05` at excellent — never zero,
because a top end that hands over the exact number spends a whole subsystem
re-deriving the leak it was built to close.

**The band is not centred on the truth**, and this is the subtle thing the layer
would otherwise have shipped: a symmetric interval hands the real number to anybody
who averages the ends. The centre is displaced by a seeded bias *first*, and the
interval opened around the displacement. Ends are rounded to a coarse step so the
pair cannot be solved back.

Seeded on `(world, turn, viewer, target, subject)` and never on call order — so the
same question twice gets the same answer, two viewers get different answers, a
rewind reproduces the same wrong guess, and two different questions are wrong in
unrelated ways.

**`Estimate` has no `true_value` field.** Not because a caller could not be trusted,
but because its existence is the leak — the same argument `visibility` makes about
an unscoped reader.

**Where it surfaces.** `visibility.other_nations` now bands rivals on the *midpoint
of the viewer's own estimate* rather than on the row, so the oldest sentence in the
briefing became viewer-dependent. And the tracked-agent view gained
`## What your services can tell you`: ranges for army, economy and industry against
up to six powers, with the network band that produced them, and a paragraph saying
in as many words that the true number is not there and cannot be worked out.

**Per-turn**, `assess_networks` lets networks drift toward what the relationship
alone sustains — a service raised for a war that has ended settles back. Drift only:
nothing in this brick builds one up or tears one down.

---

## 3. The rest of the system — all built

Each with its reveal-imperfectly / never-invent split stated. This section was written as
a proposal and is kept as one, with the outcome marked per layer, so the design that was
argued for and the thing that shipped can be read against each other.

### I1 — Discovery: seeing what was meant to be hidden — **BUILT**

Re-point `orders._espionage` at the network: the flat 0.45 becomes a function of
network strength, and what comes back is **graded**. A patchy service learns *that*
a programme exists; a good one learns what it is; an excellent one learns how far
along. Mechanically this is still an `event_visibility` grant — the machinery that
already makes every ATLAS secret work — plus, for partial reveals, a **redacted**
event text the engine authors from the real one.

*Reveals:* real events, really written, some of them partially.
*Never invents:* the model asks its service to look; the engine decides what was
found and writes the words. A model cannot author a discovery.

**Settled:** per-kind redaction templates, no schema. Three sentences per event
kind — existence, nature, detail — regenerated from the real event every time, so
they cannot drift out of step with what they describe.

**And one thing the design did not anticipate.** Only the *top* tier writes an
`event_visibility` grant; below it the service files a report and the hidden event
stays hidden. That is what keeps a partial reveal partial — otherwise it would be a
full reveal with a shorter sentence. It also meant a partial success left the secret
still unseen, so repeat orders in one turn re-found the same one; the attempt is now
offset by how many reports the actor has already filed that turn. Not a link to the
secret, because `causality` follows `event_cause` in both directions without a
viewer, which is precisely the leak the constituency layer was caught by.

### I2 — Counterintelligence — **BUILT**

A second per-pair number, or a sign on the same one: what a target's own services do
to a viewer's network in its territory. Degrades strength, and at high levels can
feed the estimate machinery a **deliberately displaced** centre rather than merely a
wide one — which is the interesting mechanic, because the victim cannot tell a
confident wrong answer from a confident right one.

*Reveals:* nothing new.
*Never invents:* both sides' capability is engine state; neither writes the other's.

**Settled: the column, keyed the other way.** The row is "A's network in B" and the
column is B's work against it, so one row holds both ends of one contest — which is
why this needed no table and no second migration.

Built at schema 32, model-reachable through a `counterintelligence` order beside
`espionage`. Three regimes rather than two: below the threshold it *blinds* (band
widens, the rival's own confidence falls with it, so it knows it knows less); past
the threshold it *handles* (band comes back tight, centre moved — a confident answer
that is wrong, with no tell anywhere in the rival's view).

Two things the build settled:

- **Tradecraft became effective rather than raw.** What a sweep takes away is the
  ability to work, not the names on a ledger — so counterintelligence tells on an
  operator twice, degrading what it can do *and* putting more police on the case.
  That is what closes I3's gap: an elite operator (tradecraft 100) is unprovable at
  zero investment and proven at forty.
- **`ALTER TABLE ADD COLUMN` was not re-runnable.** Every other additive statement in
  the migration set takes `IF NOT EXISTS`; SQLite offers no such clause for a column,
  so any world built from the current `schema.sql` and stamped at an earlier version
  could not be opened at all. The runner now tolerates exactly that one error.

### I3 — Attribution tiers: suspected / likely / proven — **BUILT**

A covert act has an author, and today that is binary — you either see the event or
you do not. Three tiers, computed from the acting party's tradecraft against the
victim's counterintelligence, and **the tier gates the diplomatic consequence**:
suspected moves regard and nothing else; likely justifies a demand; proven mints a
casus belli and a persistent fact.

*Reveals:* who really did it, at a confidence.
*Never invents:* the tier is engine-computed. A model may *accuse* anybody it likes
— that is the diplomacy channel and always has been — but the accusation carries no
mechanical weight the attribution tier has not given it.

**Built at schema 31.** Confidence accumulates per turn at a rate set by how far the
victim's counterintelligence outmatches the actor's tradecraft, so patience is
literal: a clumsy operation against a watchful state is proven in about eight turns
and a superb one against a careless state is suspected for ever and proven never.

Two things the build settled that the design had not:

- **`none` is a stored tier.** An investigation has to persist while it is still
  going nowhere, or the evidence it accumulates is thrown away every turn and can
  never reach the first threshold at all.
- **Counterintelligence tops out around 66 while tradecraft reaches 100.** So
  *passively*, only a mediocre operator is ever proven — catching a great power's
  service is exactly what I2's active half is for. That is the seam working as
  intended rather than a calibration to fix now, but it is worth knowing before I2
  picks its numbers.

Covert gambits also had to start naming their victim: the undertaking knew its target
and the event did not record it, the same gap the land battle had.

### I4 — Exposure events — **BUILT**

A blown operation becomes public: regard, legitimacy, a casus belli for the victim,
and a `world_fact` so the world *remembers* it. Hooks straight into the facts
machinery from the war-loop batch and the breach machinery in `treaties.breach`.

*Reveals:* the real operation, publicly.
*Never invents:* exposure is rolled by the engine against tradecraft vs
counterintelligence.

**Built at schema 33, to the dynamic design.** The magnitude is a product of
importance x guardedness x mismatch, scaling regard and legitimacy continuously; a
fact is always minted; the casus belli falls out of the shape rather than a second
rule. Measured before the constants locked, and the first measurement was wrong
twice:

- **Mismatch was the same number as guardedness.** Both were driven by
  `counter_intel`, so the "product of three factors" was closer to one factor
  squared. Mismatch now measures against the operator's *raw* service — how far out
  of its depth it was — while guardedness measures the defender's investment.
- **A bribe bought a war.** At magnitude 55 against a well-guarded nuclear power,
  which is exactly the flat-gate behaviour the design exists to avoid. Hence
  `CASUS_BELLI_NEEDS_IMPORTANCE = 0.70`: the espionage family tops out at 0.65 and is
  structurally excluded, however embarrassing.

**Flagged as mis-scaled and not fixed:** importance is read from the event's *kind*
and target, because the sharper signal — the `gambit.STAKES` grade the actor actually
committed, minor/serious/grave — is charged to the treasury and never recorded on the
event. Recording it would make importance much sharper for one line of change.

### I5 — Covert proxy support — **BUILT** (`e94ec44`)

`support.py` already prices `advisors` and `intelligence` as **the deniable ones**,
and its comment already reasons about "at some point the other side stops
pretending" — but every shipment it writes is `Visibility.PUBLIC`, with a comment
saying a shipment is deniable in the newspapers and not in the field. So deniability
is currently *flavour*. Wiring it to attribution tiers makes it mechanical: a
deniable channel starts at *suspected* and climbs as the war goes on.

**The seam:** `support` writes public events today. Making covert support genuinely
covert means those events become `secret` with grants — which is an INFO-002-shaped
change to a shipped subsystem, not a new module. Worth doing deliberately and not as
a side effect of building I3.

**Built as option (i).** `declare_support(..., covert=True)` writes a SECRET event shaped
like an operation — patron as actor, client as beneficiary, the client's enemies as
targets — which is the triple `attribution._open_acts` already recognises, so the tier
climbs while the backing is sustained without widening any kind list. Only `DENIABLE_KINDS`
may go quietly; every diff takes the event's scope, so INFO-002 holds. Nothing on the path
can reach `relation.at_war`. Model-reachable through a `covert_support` property beside
`support`, deliberately separate: choosing to run a war quietly is a different decision
from choosing to run one. No schema. 14 tests.

### I6 — Intelligence-sharing clauses — **BUILT** (`baaddae`)

A treaty clause under which one power's network in a third country is readable by
another. Natural home is `peace.TERM_KINDS`-style structured clauses on a
`commitment` — but note the constraint the peace layer already hit:
**`commitment_term.clause` is a closed CHECK with three values and SQLite cannot
widen a CHECK without rebuilding the table**, which the migration guard refuses. The
peace layer's answer was a new `peace_term` table hanging off the same commitment.
Intelligence sharing should either reuse `peace_term` with a new kind (cheap, but
the table is named for peace) or take its own `intel_term` table (clean, one more
migration). **My recommendation: rename nothing, add `intel_term`** — a shared
"structured clause" table would be a refactor of two shipped subsystems.

*Reveals:* the ally's estimates, not the ally's truth — a shared estimate is still
an estimate, which is a nice property to keep.
*Never invents:* the clause is enforced by the engine like any other.

**Built at schema 34, with `intel_term` as recommended.** What crosses is the sharer's
*belief*, recomputed by asking `estimate_of` with the ally as viewer, so a superb service
hands over a narrow band and a bad one a wide band — and better means *narrower*, so an
ally who knows less than you tells you nothing. Reachable at the negotiating table through
the diplomacy extraction (`intel_sharing` / `intel_about`), bounded twice like a cession:
only a defensive pact or an alliance may carry a clause, and an unresolvable subject is
refused rather than quietly widened into everything each side sees. Hanging off the
`commitment` is what makes a clause expire, lapse and breach through machinery that already
existed. 19 tests.

### Arsenals, estimable — **BUILT** (`f535db5`)

Not in the original six; settled by Alex afterwards. Warhead counts join the estimate
machinery at the widest fog in the module — `ARSENAL_MIN_ERROR 0.18` / `ARSENAL_MAX_ERROR
1.10` against `0.05`/`0.34` — and come back in *words* below `workable`, because `0-380` is
a pair of numbers a staff will average into a plan. Counterintelligence lies about warheads
as it lies about armies.

**The split:** `nuclear_balance_lines` and `nuclear_rules_block` reason on
`estimated_retaliation`, so a nation can be deterred by warheads that are not there;
`launch`, `second_strike` and `survivability` read the `arsenal` row, because the warheads
that really exist are the ones that fly. 29 tests.

### Settled since, by Alex

- **"Proven" is independent of exposure.** Patient counterintelligence can prove
  authorship quietly, without the operation being blown. I3 is not downstream of I4.
- **Exposure fallout is dynamic, not a flat casus-belli gate.** A computed magnitude
  from *importance of the exposed operation or target* × *the victim's invested
  secrecy and counterintelligence around it* × *tradecraft mismatch*, scaling regard
  and legitimacy continuously and crossing into casus-belli territory only when the
  exposed thing is both important **and** heavily guarded. A persistent fact is
  always minted. **The casus-belli threshold is to be measured before it locks.**
- **Arsenals will probably become estimable**, at the widest fog and pinnable only by
  an excellent network. Probable, not final; deliberately not built into I1.

### Build sequence I would propose

`I1` (discovery — closes the espionage loop the foundation half-rewrote) →
`I3` (attribution — most gameplay per unit of work) → `I2` (counterintelligence —
best after attribution, since it is what the tiers are computed against) →
`I4` (exposure) → `I5` + `I6` (the two seams, together, after their dependencies).

---

## 4. What is built

The foundation: `src/atlas/engine/intelligence.py`; schema 30 `intel_network` (additive,
saves survive, fingerprinted); `visibility.other_nations` re-pointed at the estimate; the
`## What your services can tell you` briefing section; the `assess_networks` turn hook; and
`tests/test_a_service_that_reports_what_it_believes.py` — including the load-bearing test,
which renders a whole briefing and asserts the rival's true figures are nowhere in it.

And, in order, each committed and reported separately:

| layer | commit | schema |
| --- | --- | --- |
| foundation | `d35b4dd` | 30 |
| I1 discovery | `8588ffb` | — |
| I3 attribution | `5528464` | 31 |
| I2 counterintelligence | `808ccb0` | 32 |
| I4 exposure | `983c3bb` | 33 |
| I5 covert proxy support | `e94ec44` | — |
| I6 sharing clauses | `baaddae` | 34 |
| arsenals estimable | `f535db5` | — |

## 5. Still not built

- **The STAKES sharpening of exposure importance.** `importance_of` reads the event's kind
  and target; the sharper signal — the stake the actor actually committed — is charged to
  the treasury and never recorded on the event. One line to record it. Deferred by Alex,
  who approved the current curve as-is.
- **A live proof.** Every layer is tested and demonstrated against staged worlds; none has
  been driven by real models over a multi-turn run the way PT42 drove the peace loop.
