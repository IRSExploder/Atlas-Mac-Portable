# Domestic constituencies

**Status.** The layer is complete: foundation (`976542c`), **C4** (`ec40a03`),
**C1** (`f0396df`), the seven arguable mappings (`11d632a`), **C3** (`21977bc`),
**C2** (`839d6ae`) and **C5** (`2f73fdf`).

The one live decision for Alex is C2's **accountability proxy** — see below.

---

## 1. Scoping: what already exists, and what it can't say

### There is already a domestic model. It has two numbers.

`nation.stability` and `nation.legitimacy`, both 0–100, written from six places:

| writer | what it does |
| --- | --- |
| `engine/budget.py` | deficits and unpaid bills push stability down |
| `engine/leaders.py` | succession, illegitimate seizures, elections |
| `engine/treaties.py` | breaking an agreement costs legitimacy |
| `engine/unrest.py` | protest and repression |
| `engine/resolution.py` | lost provinces, occupation |
| `engine/organisation.py` | expulsion, condemnation |

`leaders.coup_risk` reads them: `_BASE_RISK = 0.005`, doubling below
`_DANGER_BELOW = 50`, `_RISK_FROM_INSTABILITY = 0.10`,
`_RISK_FROM_ILLEGITIMACY = 0.18`, `_COUP_TRAP = 1.9`.

### The gap this layer exists to close

Two scalars cannot hold a **disagreement**. Every domestic event in the game
today is either good for the country or bad for it, so there is never a decision
where the answer is "yes, and it will cost you somewhere else". Mobilisation
raises stability or lowers it; it cannot please the generals and frighten the
mothers at the same time. Neither can a peace that ends a war *and* signs away
two provinces — which is exactly the decision the war loop now puts in front of a
player every game.

So: not a replacement for stability and legitimacy. A layer **beside** them that
carries who is pleased, and by what.

### What the layer can attach to

- 14 capabilities and 8 civil projects (`housing`, `rail`, `health`, `schools`,
  `workshops`, `steelworks`, `shipyards`, `land_reform`) — each already writes a
  `construction` event. All eight are mapped.
- Every war-loop event from the last five batches: `war_declared`,
  `peace_signed`, `ceasefire`, `treaty_broken`, `territory_transfer`.
- `unit_raised` / `unit_disbanded` from production and losses.
- `coup`, `unrest`, `treaty_signed`.

Nothing new had to be emitted for the foundation.

---

## 2. The design

### The set: five, universal, argued

| kind | wants |
| --- | --- |
| **army** — the officer corps | arms, budgets, a war fought to a finish, never a settlement that wastes what was paid for it |
| **industry** — industry and finance | orders, factories, open trade routes, a treasury that pays its bills |
| **the people** | bread, housing, schools, their sons not conscripted for somebody else's frontier |
| **patriots** — the nationalists | ground, prestige, never a province signed away at a table |
| **establishment** | continuity, legality, a government that keeps what it signs |

Chosen because these five give **different answers to the same decision**. A set
where every group likes public works and dislikes defeat is decoration.

Rejected: a per-regime-type set (needs a regime taxonomy the schema doesn't
have); a per-era set (makes a world unreplayable across eras); "reformers" as a
sixth (a reforming impulse *is* the tension between the people and the
establishment — as its own group it is pleased by everything the other two split
on). What actually differs between a 1936 dictatorship and a 2026 democracy is
*which constituency can remove you* — a consequences question, answered in §4.

### Support: one number each, 0–100, five named bands

`hostile < 20 ≤ restive < 40 ≤ uneasy < 60 ≤ content < 80 ≤ devoted`. Names
because a briefing should say "restive", not "38.2".

Three dials:

- `RESTING = 50.0` — where support drifts with nothing happening. Deliberately
  not the midpoint of *content*: a government that does nothing is tolerated,
  not loved.
- `INERTIA = 0.06` — the fraction of the distance to resting that one month
  closes. Slow on purpose: a shock is still felt a year later, mostly gone in
  three. A grievance that fades in two turns is not a grievance you govern
  around.
- `PER_TURN_CAP = 12.0` — the most a **month's** reactions may move a group, scaled
  by the turn's span where it is applied. A country that raised six divisions in a
  month has done *one thing*, not six; a country that fought for a year, lost its
  capital and changed its government has not done one thing either.

**Seeded, not assigned.** A nation with no rows reads defaults derived from its
own `stability`, `legitimacy`, `military_strength`, `economy` and `treasury`: a
well-governed country starts with a contented establishment, a heavily armed one
with a pleased officer corps, a poor one with a restive population. Every era and
every authored scenario gets sensible starting politics for free. Rows are
written lazily, when something first moves.

### The model-proposes / engine-computes split

**There is deliberately no way in.** `constituency` is `ENGINE_ONLY`, and the
guard file that demands every engine-only table name its door records why: a
power that could write its own standing could *buy* the army's devotion instead
of earning it, and one that could write a rival's could talk that rival's
generals into a coup. Standing is computed from events already written — the
model's influence on it is the whole rest of the game.

Models **read** it: `views.py` gained `## Who you have to govern`, which ends
"not dials you can set".

---

## 3. Consequences

**C4 — the nationalists at the peace table (BUILT, `ec40a03`).** `acceptance`
reads the patriots. Mood × humiliation × voice, each factor a bound: an ordinary
country contributes nothing, an indemnity is not a nationalist question, and a
power annihilated past leverage −0.90 folds whatever it feels. Measured on the
canonical lost war — patriots at 72 sign both provinces away, at 88 the same
offer is refused.

**C1 — the coup channel (BUILT, `f0396df`).** A mutinous army is now the largest
single term in `coup_risk`; a disowned establishment a quieter one; a devoted
army takes 35% off the whole risk. Danger starts at *restive* rather than at
anything short of devoted, so on the three seeded maps only 1 / 6 / 6 nations
out of 74 / 148 / 242 are touched and the base rate is unchanged.

**C2 — who can remove you (BUILT, `839d6ae`). THE PROXY IS A GUESS TO OVERRULE.**

Nothing in the schema knows what kind of state a nation is: every government on all
three shipped maps reads `came_to_power = 'seeded'`, and 226 of 243 nations share one
stability and one legitimacy. What the scenarios *do* author is a **title**, so that
is the proxy — President / Prime Minister / Bundeskanzler on one side, Fuehrer /
Duce / General Secretary / Supreme Leader on the other. Two tuples in `leaders.py`,
no schema, one line to overrule. Coverage: **6, 12 and 13** accountable governments
per era; 63 of 74 in 1936 are titled "the government".

The fork that needed deciding was what happens to the untitled majority, and the
answer is a **third channel**. Two questions, three outcomes:

| the people are decisively angrier | there is a ballot box | outcome |
| --- | --- | --- |
| yes | yes | **election** — and the country gains legitimacy, the only path where a government falling leaves it better |
| yes | no | **revolution** — worse than a coup, because what was rejected is the order itself |
| no | either | **coup** — the default, not a fallback |

The street must clear a margin to beat the barracks, measured rather than picked:
the people's seeded standing derives from stability, and stability is what makes
governments fall, so without the margin the popular channel won nearly every
ambiguous case on one number counted twice.

**C3 — friction (BUILT, `21977bc`).** A hostile populace strikes — up to 30% of
revenue — and hostile industry drags its feet — yards down to 60% of a month —
both applied *inside* the revenue `nation_budget` returns and the days
`advance_construction` spends, so nothing new is stored and nothing extra rewinds.

Friction starts at the top of the *restive* band rather than the resting point, and
the threshold was measured: against the resting point, 232 of 242 modern-day nations
had slowed yards on the day the world was built. At the restive line the three
shipped worlds lose **0.02% / 0.00% / 0.00%** of world revenue at seed and no yard
anywhere is slowed. Both halves are named in the turn report, under their own
heading rather than among the arrears.

Built for the people and industry. The rest of the original table is unbuilt:

| group | at *restive* | at *hostile* |
| --- | --- | --- |
| army | slower mobilisation | units raise under strength |
| industry | construction costs more | a project stalls |
| people | unrest events more likely | strikes: production lost |
| patriots | peace acceptance harder | war aims cannot be dropped |
| establishment | treaties cost legitimacy to sign | a signature is repudiated |

This is where the layer earns its keep — a player who lets industry rot pays
every turn, not once at the end.

**C5 — the inspector (BUILT, `2f73fdf`).** `ui/homefront.py`, its own TopBar button
(the scales). Consequence first, mood second: each row leads with what the group is
doing to you, and the band and number sit underneath as evidence. Everything comes
through `constituencies.home_front`, which asks the same functions the engine acts
on, and the tests assert each figure equals its source. Screenshots in
`scratchpad/shots/homefront-*.png`, both themes, fingerprint unmoved by rendering.

### Order

`C4` → `C1` → the mappings → `C3` → `C2` → `C5`. All landed.

**Not in this layer:** delegation and standing instructions (Codex's later item) is
its own batch and has not been started.

---

## 4. The seven readings that were arguable (BUILT, `11d632a`)

Each was left unmapped in the foundation rather than guessed at. All seven now have
a reading, each a module-level `Reaction` in `constituencies.py` carrying the reading
it was given, so overruling any of them is a one-line edit.

> **Seven, and it has said six since the day it was written** — in this heading, in the
> status line above, in the commit title, and in the war-loop handoff. The table below has
> always had seven rows: `breaking your own truce` landed in the same commit and was never
> counted. Corrected on the doc-accuracy pass rather than quietly, because a count nobody
> checked for a fortnight is worth knowing about.

| action | reading taken | the reading not taken |
| --- | --- | --- |
| **austerity** | there is no austerity *policy* in this game; the thing that exists is **arrears** — army −2.5, industry −3.0, establishment −2.0, people −1.0 | "fiscal prudence pleases the establishment". An unpaid army is not prudence. |
| **money in** (`_invest`) | industry +2.5, army +1.0, establishment −1.0 | — |
| **sanctions received** | rally: patriots +2.0, industry −2.5, people −1.0 | hardship turns the people against the government — but `unrest` already charges that, and charging it twice is charging it twice |
| **a lost battle** | attacker −1.0 army; defender −1.2 army, −0.8 people, −0.6 patriots; winner +1.0 army, +0.8 patriots | mirrored win/loss numbers. A war of victories is what an army is *for*, so losing is worth more. |
| **an arms race with no war** | on top of `unit_raised`: patriots +1.5, people −1.0, establishment −1.0 | a peacetime army is politically cheaper. Right sign, wrong mechanism: what makes conscription bearable is a war. |
| **occupying / occupied** | per **month**, from the map and scaled by the turn's span: holding +1.0 patriots / +0.5 army; occupied −2.0 patriots, −1.5 people, −1.0 army, −1.0 establishment, ×min(3, provinces) | symmetric numbers. Losing is not the negative of taking. |
| **breaking your own truce** | people −3.0, establishment −2.0, patriots +1.5, on top of the generic `treaty_broken` charge | "the country tells itself the other side broke it" — real, but a narrative claim; this layer reads the ledger, which knows who moved. |

Two enabling decisions worth flagging:

- `economic_reform` is written for three unrelated things, so the mapping **reads
  the money, not the prose** — which way the economy moved, on the diffs the event
  itself caused. A sentence can be reworded; the ledger cannot.
- **Battle events carried no participants at all**, so who lost one was
  recoverable only by matching a nation's name inside a string — which "Germany"
  and "West Germany" break. They now name the defender and, as `beneficiary`, the
  side that prevailed. Air and naval actions still carry neither and so
  deliberately move nobody.

---

## 5. What is built

`src/atlas/engine/constituencies.py`, schema 29 (`constituency`, additive, saves
survive, fingerprinted), the turn hook, `report.at_home`, `_at_home_lines` in
`views.py`, and 21 tests in
`tests/test_a_leader_with_somebody_to_govern.py` — including rewind-exactness
and the engine-only guard.
