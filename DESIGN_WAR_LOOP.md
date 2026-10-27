# ATLAS — design: completing the war loop

**For:** Alex, to shape before the meaty layers are built.
**Prepared:** 2026-08-13. **Status:** layer 1 of 6 built; layers 2–6 are proposals.
**Scope:** war aims → exhaustion → leverage → structured settlement → separate peace →
domestic consequences.

> **The one-sentence finding.** A war in this engine is two `relation` rows and nothing
> else: no war entity, no war id, no start turn, no participant list, no accumulator of any
> kind — and `_settle_war` is 110 lines that clear a flag and move some occupied provinces.
> Everything below follows from that.

---

## 1. What exists today

### 1.1 A war is a pair of rows

`relation` (`schema.sql:488`) is directed: `(a→b)` and `(b→a)` are separate rows, keyed
`(nation_a, nation_b)`, each with `at_war`. `at_war` is ENGINE-only
(`permissions.py:38`). There is **no war table, no war id, no war participants, no start
turn and no per-war accumulator anywhere in the schema.**

Started by `_declare_war` (`resolution.py:1006`) — the only writer. It resolves the target,
refuses international bodies, writes an engine `war_declared` event with `actor`/`target`
participants, sets both rows, and charges `_UNPROVOKED_WAR_COST = 8.0` legitimacy when
there is no casus belli. Reached from exactly two places: the player's `declare_war` action
(`resolution.py:196`) and the WORLD_SIM `declarations` channel (`resolution.py:202`).

**WAR-001** (`rules/diplomacy.py:145`) requires a casus belli or a legitimacy charge in the
same batch, skips the victim's mirror row, and is deliberately **joint** — it names no
`diff.seq`, because severing half a declaration leaves a war half-started.

**You do not need a war to invade.** `_contests_from_actions` (`resolution.py:910`) checks
resolvability, reachability and friendliness — never `at_war`. An undeclared invasion of a
non-treaty-bound neighbour produces battles, occupation and casualties with `at_war = 0`.

### 1.2 A peace is a flag and some provinces

`_settle_war` (`resolution.py:1285`) is the whole peace engine. It writes one event
(`peace_signed` or `ceasefire`), two `at_war = 0` diffs, and — for a peace, not a ceasefire
— cessions. `_cession_diffs` (`resolution.py:1398`) is the safety line: a province may
change hands only if **its owner and its holder are the two powers at the table**, so a
peace can only ratify an occupation the fighting actually reached; a capital is relocated in
the same batch; a state cannot be dissolved at the table.

What `_settle_war` does **not** do: no commitment row, no reparations, no disarmament, no
withdrawal, no guarantee, no relation-score change, no war-guilt, no contest cleanup. A
ceasefire leaves every occupation in place and every siege open.

Three related gaps worth naming:

- **`sue_for_peace` is advertised and executed by nothing.** It is a full `Capability`
  (`capabilities.py:179`) with aliases and an example, the advisor actively recommends it
  (`views.py:1402`), and `orders.py::_HANDLERS` has no entry — so the player's order comes
  back `not_attempted: "nothing in the engine executes a 'sue_for_peace' order"`. Only
  WORLD_SIM can end a war today.
- **`CommitmentKind.PEACE_TREATY` is never written.** Dead enum surface since it was added.
- **WAR-002** (`rules/diplomacy.py:219`) is named "peace requires a recorded agreement" and
  requires no agreement — only that the clearing diff cites a `peace_signed`/`ceasefire`
  event, and unlike WAR-001 it does **not** check `origin = 'ENGINE'`.

### 1.3 What is already tracked, and would feed the rest

| Thing | Where | Shape |
|---|---|---|
| Occupation | `region.controller_nation_id`, `owner_nation_id`, `home_nation_id` | set on capture (`resolution.py:1816`) and cession; cleared only by recapture and partisan revolt (`unrest.py:265`) |
| Fronts | `contest` (pressure, opened_turn, last_turn) | id derived from `(region, attacker)`, diffed, decays when idle |
| Casualties | `_loss_diffs` (`resolution.py:2369`) | per-unit `strength` diffs; **no cumulative total anywhere** |
| Occupation cost | `unrest.py` | `_UNREST_PER_PROVINCE = 0.8`, garrison relief, stability drag capped at `_MAX_DRAG = 5.0`, partisan revolt |
| Coalitions | `defensive_pact_allies` (`resolution.py:1149`) | Article 5 is **prompted, not automatic**: a casus belli plus `_PACT_BETRAYAL = -30` regard |
| Agreements | `commitment` / `commitment_nation` / `commitment_term` | 9 kinds; **3 clauses only** (`no_war`, `open_trade`, `cede_region`); breach detection, casus belli, `honour_terms` all exist and work |
| War weariness | — | **nothing.** Zero hits for weariness/exhaustion in the tree |

### 1.4 Two constraints that shape everything below

- **`commitment_term.clause` is a closed CHECK with three values.** SQLite cannot widen a
  CHECK without rebuilding the table, and the migration guard refuses non-additive
  statements — migrations 19 and 20 are the only two ever marked `invalidates_saves`.
  **Structured peace terms must therefore go in a new additive table, not in
  `commitment_term`** (§2.4), unless we are willing to spend the third save-invalidating
  migration.
- **`event.kind` is a closed CHECK too**, so there is no `war_aim_declared` or
  `terms_offered` event kind without the same rebuild. Layer 1 uses `order_issued`, which is
  what the objectives channel does.

---

## 2. The design, layer by layer

Each layer states the **model-proposes / engine-enforces** split explicitly, because that is
the line the whole project is built on: a model may say what it wants and never what
happens.

### 2.1 War aims — **built** (§3)

| | |
|---|---|
| **Model proposes** | `war_aims: [{against, kind, region?}]` on the tracked-agent channel |
| **Engine enforces** | you are at war with `against`; the kind exists; ground is named where the kind needs it and absent where it does not; the region is on the map; the enemy **owns** it; `recover_cores` is checked against `home_nation_id`; a cap of 5 per war |
| **Engine decides alone** | nothing is achieved, conceded or settled here |

Kinds: `defend`, `recover_cores`, `annex`, `regime_change`, `independence`, `demilitarise`.
Each was chosen so a settlement can later *contain* it as a term — a kind with no possible
term would be a war aim that can never be satisfied or refused.

### 2.2 War exhaustion — **proposed**

**Where it lives.** A new additive table, keyed the same way aims are:

```sql
war_effort (world_id, nation_id, enemy_id,
            opened_turn, last_turn,
            casualties REAL,     -- cumulative strength lost in this war
            exhaustion REAL)     -- 0..100
PRIMARY KEY (world_id, nation_id, enemy_id)
```

This doubles as **the war's ledger** — the thing that does not exist today — and it is what
gives a war a start turn, a duration and a cost without inventing a war entity (§5, open
decision 1).

**Where the numbers come from.** Casualties need a new accumulator: `_loss_diffs`
(`resolution.py:2369`) already knows the contest, hence attacker and defender, hence the
pair. It writes one extra ADJUST diff per side per contest. Everything else is derivable
from state already present.

**Proposed formula shape — every constant a named, tunable dial:**

> **This is the proposal, and the built version differs.** Kept as written because it is the
> record of what was argued for; read `engine/wareffort.py` for what shipped. Two of the
> terms below changed during the build, both deliberately:
>
> * **The casualty denominator is no longer `manpower_ceiling`.** It is the army that took
>   the losses -- what it had is what survives plus what it has just lost -- because that
>   reads the same in every era, and the weight went to 60.
> * **Occupation drifts toward a ceiling instead of adding a share of one.** A half-held
>   country settles at half of `OCCUPIED_CEILING` and stays there, which is what stops a
>   stabilised front collapsing a country by itself. It compounds over the turn's span
>   rather than multiplying by it, which is what makes the plateau a plateau on a long jump
>   as well as a monthly one.

```
per turn, per war, scaled by the turn's span in months:

  exhaustion += BASE_PER_MONTH                              # 0.6 — time alone
             +  CASUALTY_WEIGHT * (losses_this_turn / manpower_ceiling)   # 35
             +  OCCUPIED_HOME    * (home provinces the enemy holds / your home provinces)  # 25
             +  SHORTAGE         * (1 if in arrears or a good is out, else 0)  # 3.0
  exhaustion -= RECOVERY_PER_MONTH                          # 1.2 — only while not at war
  exhaustion *= (1 + FRAGILITY * (1 - stability/100))       # 0.5 — a shaky state tires faster
  clamp 0..100
```

Bands, so it reads to a player and a model rather than being a number:
`0–20 resolute · 20–45 straining · 45–70 war-weary · 70–90 exhausted · 90+ collapsing`.

**Effects (deliberately small at first):** a stability drag above 60, reusing
`unrest.assess_unrest`'s clamping discipline and `bounds.absorbable`; visibility in
briefings; and input to leverage. **No auto-surrender** — see open decision 3.

| | |
|---|---|
| **Model proposes** | nothing. Exhaustion is derived |
| **Engine enforces** | the whole thing, from state it already owns |

### 2.3 Leverage — **proposed**

Pure derivation, **no new state**, in the shape of `scoring.victory_progress`: a function
returning a signed position and the reasons for it.

```
leverage(A vs B) ∈ [-1, +1] =
    GROUND   * (A-held home provinces of B − B-held home provinces of A) / (their homelands)
  + FORCE    * tanh(A.military_strength / B.military_strength − 1)
  + WEARINESS* (B.exhaustion − A.exhaustion) / 100
  + AIMS     * (A's aims achieved − B's aims achieved)      # reads §2.1 directly
```

With a sentence attached: *"You hold four of their home provinces and none of yours are
held; they are war-weary and you are straining. They will concede ground; they will not
concede their government."*

**This is what makes the settlement layer honest**, because it is what decides which terms a
losing side will *accept* — the engine, not the winner's model.

| | |
|---|---|
| **Model proposes** | nothing |
| **Engine enforces** | the computation; it is read-only and asserted fingerprint-neutral |

### 2.4 Structured peace terms — **proposed** (the big one)

**Vocabulary**, each mapped to a mechanism that already exists:

| Term | Means | Enforced by |
|---|---|---|
| `cede_region` | ownership moves | the existing `_cession_diffs` validation |
| `restore_region` | occupied ground goes back to its owner | clears `controller_nation_id` |
| `withdraw` | all forces leave named ground | the eviction path `_evict_diffs` already has |
| `reparations` | treasury transfer over N turns | a standing term, checked by `honour_terms` |
| `demilitarise` | no formations in named ground for N turns | a standing term; a rule refuses moves in |
| `guarantee` | the winner underwrites a third party | writes a `defensive_pact` commitment |
| `recognise` | the loser accepts an independence or annexation | writes the sovereignty change once |

**Where they are stored.** A new additive `peace_term` table hanging off `commitment`, *not*
a widened `commitment_term` — see §1.4. The peace itself becomes a real `peace_treaty`
commitment (currently never written), so a broken peace runs through the breach machinery
that already exists and produces the casus belli, the regard and the legitimacy charge for
free.

| | |
|---|---|
| **Model proposes** | a term set, in the offer: "here is what I will stop for" |
| **Engine enforces** | *enforceability*: you cannot cede what neither party holds; reparations bounded by treasury and by leverage; demilitarisation only over ground the obliged party owns; withdrawal only where an occupation exists; guarantees only naming nations that exist |
| **Engine decides alone** | **acceptance.** Whether the loser takes the deal is a function of leverage and exhaustion, not of either model's say-so. This is the line that keeps "the model never decides outcomes" true for peace as it is for battle |

### 2.5 Separate peace and armistice — **proposed**

**Separate peace falls out of the pair-keying for free**: settling (A,B) does not touch
(A,C), and each belligerent's aims are already its own rows. What it needs is the
*consequence*: abandoning a co-belligerent costs regard with them (reuse `_PACT_BETRAYAL`)
and, if a defensive pact was live, is a breach with everything that already implies.

**An armistice that can fail** means writing the `ceasefire` commitment `_settle_war` does
not currently write, with an `expires_turn`. Then breaking it is an ordinary breach: the
detector, the casus belli and the charges all exist. This also closes the WAR-002 hole
(§1.2) — a ceasefire that exists as a row can be *required* rather than merely cited.

Also in this layer: **wire `sue_for_peace`**, which is advertised to the player today and
executed by nothing.

### 2.6 Domestic consequences — **seam only, not built here**

Exhaustion above a band, a humiliating settlement (aims unmet plus ground given up), or a
hopeless war continued should cost legitimacy and stability, and should eventually be
*whose* patience ran out rather than a scalar. That "whose" is Codex's batch #2 —
domestic constituencies — and this layer must not pre-empt its data model. What layer 6 does
here is call a named hook with the settlement's shape and let #2 fill it in.

---

## 3. What has landed: layer 1

War aims, per belligerent per enemy: `war_aim` table (schema 25, additive, **not**
save-invalidating), `engine/waraims.py`, the `war_aims` channel on the tracked-agent
schema, a briefing section on both sides of every war, a turn-report line, and pruning when
the war ends.

**Cost, stated plainly:**

- **Schema 25**, additive. `invalidates_saves=False`. A world written before it opens fine
  and has no aims, which is the true reading of a war nobody wrote a point for.
- **Every built-world digest moved by the width of one table name**, because `war_aim` joins
  `_FINGERPRINT_TABLES` and the digest covers table names whether or not there are rows.
  Same as schemas 16, 21 and 23. New era digests:
  `gathering-storm b3458b26…`, `cold-war 1a039244…`, `modern-day 9646ed8c…`.
- **The tracked-agent request payload gained a channel**, so the two active cassettes
  (`pt37-cold-war`, `pt38-cold-war`) will miss on a *full replay* with
  "the schema, the options or the system prompt have changed". The release-audit cassette
  gate does not check this (it checks key well-formedness), so the audit stays green — but
  they should be re-recorded on the next live run or reclassified historical.

---

## 4. Build sequence

Each step is its own commit(s), with tests, and each is shippable on its own.

| # | Piece | Depends on | New state | Rough size |
|---|---|---|---|---|
| **1** | **War aims** ✅ | — | `war_aim` (schema 25) | landed, 25 tests |
| 2 | Exhaustion + the casualty ledger | 1 | `war_effort` (schema 26) | medium; the dials are the work |
| 3 | Leverage | 2 | none — pure derivation | small |
| 4 | Structured terms + enforceability | 2, 3 | `peace_term` (schema 27), real `peace_treaty` commitments | **large**; the acceptance rule is the design |
| 5 | Separate peace, armistices that fail, `sue_for_peace` | 4 | none | medium |
| 6 | Domestic hooks | 2, 4 | none of its own | small, then hands off to batch #2 |

**Recommended pause points:** after 2 (do the dials feel right in a live run?) and after 4
(is the acceptance rule producing settlements that read as history?).

---

## 5. Open decisions — this is what needs shaping

1. **A real `war` entity, or keep pair-keying?** Everything so far is keyed
   `(nation, enemy)`, which is what a war *is* today and costs no migration. A `war` table
   with an id would give a war a name, a coalition, and one place to end it — and would
   require re-keying aims, the ledger and the terms, plus a story for wars already in
   progress in a save. **Recommendation: stay pair-keyed through layer 4**, and revisit only
   if settling a coalition as a bloc turns out to need it.
2. **Structured terms in a new table (additive) or a widened `commitment_term` (the third
   save-invalidating migration ever)?** **Recommendation: new table.** The cost is two
   places to look for a promise; the alternative eats every existing save.
3. **Does exhaustion ever force a hand?** Options: (a) never — it only informs the model and
   feeds leverage; (b) at 90+, the engine *offers* terms the model may refuse; (c) at 100,
   an engine-forced capitulation. **Recommendation: (b)**, because (a) risks a model
   fighting to the last division forever — the exact failure `settleable_wars` was added to
   nudge — and (c) takes the decision away from the player as well as the model.
4. **Undeclared wars.** Invading without declaring is legal today, and aims, exhaustion and
   settlement all key off `at_war`. Do we (a) leave undeclared fighting outside the war loop,
   (b) have the engine declare a war once sustained fighting is observed, or (c) refuse
   contests between powers not at war? **Recommendation: (b)**, engine-declared with a
   casus-belli-free legitimacy charge, but it is a behaviour change worth a decision.
5. **Reparations cap.** Bounded by treasury alone, or by leverage too? A winner with total
   leverage extracting 90% of a treasury is historically right and mechanically brutal.
6. **Cassettes.** Re-record `pt37`/`pt38` on the next live run, or mark them historical now?

---

## 6. The eventual proof

Codex's #10 — *a model negotiating an unfavourable peace* — is not a separate feature; it is
the **validation of this batch**, and it becomes runnable the moment layer 4 lands. The
shape: a scripted or live run in which one side's exhaustion is high and its leverage
negative, and the measurement is whether it accepts terms that cost it ground rather than
fighting to annihilation — and whether the terms it accepts are *worse* the further leverage
has moved against it. Until then there is nothing to negotiate over: today a peace is a flag
and some provinces, and any model's answer to it is a coin flip.
