# The discoverability matrix

**Audit only.** Nothing in the engine was redesigned to produce this and nothing was broadly
surfaced. Two small, clearly-isolated fixes and one missing test are proposed at the end and
are **staged for review, not merged**; everything else is a finding with a verdict and, where
the silence is deliberate, a waiver that says so out loud.

Produced by `scratchpad/discoverability_matrix.py` and `scratchpad/discoverability_probe.py`,
both read-only, no provider, no quota. Raw output in `scratchpad/discoverability_matrix.json`
and `scratchpad/discoverability_probe.json`.

**Method.** Every top-level channel of the tracked-agent answer schema and every entry of the
player capability catalogue, rendered against **real briefings in real worlds**: three eras
(modern-day, cold-war, gathering-storm) × the eight largest powers × **four states**, for 96
tracked-agent briefings and 24 player menus.

The four states are the load-bearing part of the method. A channel that renders only for a
power already using it measures as *present* in a single peacetime snapshot and is invisible
in play — which is exactly how the support layer went unused for a hundred and fifty turns.
So each government is rendered as seeded, at war, at war and occupying, and warm with a third
party that is losing a war of its own. Each state is applied inside a transaction and rolled
back, so all four are measured against the world the scenario actually seeded rather than
against the wreckage of the previous state.

---

## 1. The headline

**Three findings that are defects, and one that is a design question.**

| # | finding | evidence |
|---|---|---|
| 1 | **`covert_support` is named in 0 of 96 briefings.** The deniable twin of the support channel is in the schema, has an engine, has tests through `parse_batch` — and no government has ever been told it exists. | probe: `covert_support_named 0/24`; matrix: `0/24` in every one of the four states |
| 2 | **Research is listed without ever saying what it is for.** The build menu names this era's programmes with id, cost and duration in 24 of 24 briefings — and states a benefit in **0 of 24**. Public works on the very next line print `-> industry +12`. | probe: `menu_states_a_programme_effect 0/24` |
| 3 | **Programmes behind unmet prerequisites are not mentioned at all.** Three of modern-day's and cold-war's seven, three of gathering-storm's six. The menu simply goes quiet, which teaches a staff the catalogue is four items long. | probe: `menu_names_a_locked_programme 0/24`; `available_technologies` returns 4, 4 and 3 of 7, 7 and 6 |
| 4 | **`declare_war` is named in 69 of 96 briefings and silent in the other 27 — on purpose.** `_warpath_lines` documents the choice: "Silent when the answer is nobody, which is the common case and should read as one." That is the same silence five other blocks were repaired for. **This is a design call, not a defect, and it is not touched here.** | matrix: 17–18 of 24 per state |

And one gap in the guard rather than in the engine:

| 5 | **Mediation's discovery path has no test.** `test_a_power_can_broker_somebody_elses_war.py` tests the mechanism thoroughly and the answer channel too, and nothing anywhere renders a briefing and asserts that an eligible broker is *told*. `test_every_lever_is_visible.py` — the file that exists for precisely this — does not list it. |

---

## 2. The matrix: tracked-agent channels

96 briefings: 3 eras × 8 powers × 4 states (24 per state).

| channel | seeded | at war | occupying | ally losing | eligibility | briefing states cost / effect | reference AI ever emits it | discovery test | verdict |
|---|---|---|---|---|---|---|---|---|---|
| `build_orders` | 24/24 | 24/24 | 24/24 | 24/24 | always | yes / **works only** | yes (`workshops`, always) | yes | **named — and see §3** |
| `mobilisation` | 24/24 | 24/24 | 24/24 | 24/24 | always | yes / yes | yes | yes | named |
| `raise_formations` | 24/24 | 24/24 | 24/24 | 24/24 | always | yes / yes | no | yes | named |
| `reinforce` | 24/24 | 24/24 | 24/24 | 24/24 | always | yes / yes | yes | yes | named |
| `doctrine_shift` | 24/24 | 24/24 | 24/24 | 24/24 | always | yes / yes | no | yes | named |
| `support` | 22/24 | 22/24 | 22/24 | 24/24 | a war you are not in | yes / yes | yes | yes | named |
| `declare_war` | 18/24 | 17/24 | 17/24 | 17/24 | a target the war-path gate would pass | yes / yes | no | yes | **waived — see §1.4** |
| `military_objectives` | 3/24 | 24/24 | 24/24 | 3/24 | at war | — / yes | yes | yes | correctly conditional |
| `war_aims` | 3/24 | 24/24 | 24/24 | 3/24 | at war | — / yes | no | yes | correctly conditional |
| `peace_offer` | 3/24 | 24/24 | 24/24 | 3/24 | at war | yes / yes | no | yes | correctly conditional |
| `grant_independence` | 11/24 | 11/24 | 11/24 | 11/24 | holds a dependency | — / yes | no | yes, **including a negative test** | correctly conditional |
| `mediation` | 7/24 | 7/24 | 7/24 | 10/24 | a war between two others | yes / yes | no | **none** | conditional; **guard missing** |
| `covert_support` | **0/24** | **0/24** | **0/24** | **0/24** | a war you are not in | — / — | no | schema only | **INVISIBLE** |
| `diffs` | — | — | — | — | always | n/a | yes | n/a | waived: the generic write path; the whole briefing is an account of what may be written |
| `events` | — | — | — | — | always | n/a | yes | n/a | waived: every briefing explains citing events at length |
| `narrative` | — | — | — | — | always | n/a | n/a | n/a | waived: prose, not a mechanic |

**The `covert_support` waiver was written into the audit and the audit refuted it.** The
sweep began with an exemption reading "the deniable twin of `support`; named beside it
wherever support is". That is what the code *ought* to do and it is not what it does: the
support block prints one worked example, for `support`, and the covert channel appears in no
briefing in any era in any state. A waiver that has never been checked against rendered text
is a guess, which is the whole argument for rendering the text.

**World-sim-only channels.** `contests` and `declarations` are offered to `Role.WORLD_SIM`
and to nothing else, which is correct — the world simulator moves everybody at once and a
declaration a power makes about *itself* belongs to the tracked-agent role, where
`declare_war` already is. Not audited against the world-sim view here; that view has a
different reader and wants its own pass.

**The writable-diff surface**, for completeness: the world simulator may write 9 columns of
`nation`, 3 of `region` and 1 of `relation`; a tracked agent may write 1 of `nation` and 1 of
`relation`. Both are enumerated in the schema's own `field` description, which is generated
from `writable_fields_for` — so this half cannot drift out of sight, and it is the one part of
the surface that is structurally safe.

---

## 3. Research: the seventh invisible mechanic

The channel-level sweep says `build_orders` is named in 96 of 96 briefings, and that is true
and useless. Research is one of the things `build_orders` takes; no power in any measured run
has ever researched anything. The finer probe says why.

| measure | result |
|---|---|
| briefings with a "Research you could begin" section | **24/24** |
| briefings listing at least one programme with its id | **24/24** |
| briefings stating what a programme *does* | **0/24** |
| briefings naming a programme behind an unmet prerequisite | **0/24** |
| task block ever exampling a programme, as it examples works | **0/24** |
| parser view listing programmes | 24/24 |
| reference oracle ever ordering one | **never** — it orders `workshops` and nothing else |

So the reader is given a name, a price and a duration, and no reason. Immediately above it,
every public work prints `Name [`id`]: 40 treasury, 3 turns -> industry +8, stability -1` —
the effect summary that makes a price mean something. `research.py` opens by insisting every
programme's effect is something shipped code already reads; the briefing then declines to say
what any of them are.

Three of seven programmes in modern-day and cold-war, and three of six in gathering-storm,
sit behind `requires` and are named nowhere. A staff reading the menu has no way to learn
that `carrier_doctrine` exists, still less that `welding` is what opens it — so the one part
of the catalogue with a *plan* in it is the part nobody can see.

And the task block, which examples `workshops`, `steelworks`, `rail` and `schools` by name
and says what each raises, mentions research once, generically, with no example and no
benefit.

**This is the subject of its own contained piece of work and is not fixed here.**

---

## 4. The player's half: the capability catalogue

17 capabilities, 24 nation-menus. The player's surface is **structurally sound in a way the
model's is not**: `capabilities.CAPABILITIES` generates the parser enum, the prompt menu, the
council's advisable list and the worked examples, and a capability that reaches no prompt
fails an existing test. Every capability offered by its availability rule appears in the
parser view, in every era, for every power — 24/24 wherever the rule allows it.

| capability | offered | eligibility rule | cost stated in the menu |
|---|---|---|---|
| `move_units` | 24/24 | has units | yes |
| `raise_units` | 24/24 | always | yes |
| `reinforce` | 24/24 | always | yes |
| `disband_units` | 24/24 | has units | yes |
| `adopt_doctrine` | 24/24 | has a doctrine to adopt | yes |
| `economic_investment` | 24/24 | always | yes |
| `build_project` | 24/24 | always | yes |
| `gambit` | 24/24 | always | yes |
| `sue_for_peace` | 3/24 | at war | yes |
| `propose_treaty` | 22/24 | may sign | **no** |
| `declare_war` | 21/24 | somebody to declare on | **no** |
| `fortify` | 24/24 | has units | **no** |
| `diplomatic_message` | 24/24 | has neighbours | **no** |
| `espionage` | 24/24 | has neighbours | **no** |
| `counterintelligence` | 24/24 | has neighbours | **no** |
| `freeform` | 24/24 | always | no — correctly, it costs nothing directly |
| `other` | 24/24 | always | no — correctly, it is the parser saying it found no mechanic |

**The finding here is small and exact.** `Capability.cost` documents `""` as *"nothing
directly" — which is itself worth saying, because a player asked to choose between acts needs
to know which ones are free*. `prompt_menu` then does `if capability.cost:` and omits the
clause entirely, so the thing the catalogue says is worth saying is never said. Six acts show
a bare verb, and one of them is `declare_war` — which the *tracked agent's* briefing calls
"the heaviest thing on this form" and prices in standing, in wars-in-progress and in years of
cooldown, while the player's own menu says only "`declare_war` -- declare war; needs
`target_nation`".

That asymmetry is worth stating plainly: **on cost, the AI's briefing is better than the
player's menu.**

---

## 5. Waivers, recorded

An explicit waiver for everything not surfaced, so the next audit argues with a recorded
decision rather than rediscovering a silence.

| # | not surfaced | waiver |
|---|---|---|
| W1 | `diffs` | The generic write path. The whole briefing is an account of what may be written and by whom; naming the property would add nothing. |
| W2 | `events` | Every briefing explains event citation at length, in the section on `reason_event_id`. |
| W3 | `narrative` | Prose. Not a mechanic, never parsed for one. |
| W4 | `contests`, `declarations` | World-simulator channels only. A tracked power's own declaration goes through `declare_war`; contests are the simulator moving everybody at once. |
| W5 | `military_objectives`, `war_aims`, `peace_offer` at peace | A power at peace has no aims to state and no war to end. The blocks are correctly silent, and `test_every_lever_is_visible.py` has a matching negative test for the neighbouring case. |
| W6 | `grant_independence` without a dependency | Correctly gated, and it has an explicit negative test: a power with no territories is not offered decolonisation. |
| W7 | `mediation` without a war between others | Correctly gated. **The waiver covers the gate, not the missing test** — see F5. |
| W8 | `declare_war` where no declaration would pass the gate | **Deliberate, and the one waiver that should be argued with rather than accepted.** `_warpath_lines` chose silence over "nobody, and here is why", and the project has repaired that exact silence in five other blocks. Left as a design question for Alex. |
| W9 | engine-only tables with no model door | Already covered, table by table, by `test_a_mechanism_a_model_cannot_reach_is_not_shipped.py`. Not re-derived here. |

---

## 6. What is staged, and what is not

**Staged for review on `discoverability-audit` — small, isolated, and each one line of
argument long:**

1. **`prompt_menu` says "no direct cost" instead of nothing** when a capability records none.
   Six acts gain a clause; the catalogue already says this is worth saying. Touches one
   function, no engine behaviour, no state.
2. **`covert_support` is named beside `support`** in the block that already renders for every
   eligible patron. One sentence in `_patronage_lines`; the waiver that claimed this was
   already true is deleted.
3. **`mediation` joins `test_every_lever_is_visible.py`** — an eligible broker is told, an
   ineligible one is not. A test only.

Both briefing changes move rendered prompt text, so they will want a batched cassette
re-record. That is why they are staged rather than merged.

**Deliberately not done:**

* Research (§3) — its own contained piece of work, with a measurement attached.
* `declare_war`'s silence (W8) — a design call.
* Anything touching victory thresholds, Concert, Hegemony's classification, or the strategy
  layer's fitness.
* Any broad resurfacing pass. The point of this document is that the next gap should fail in
  CI, and the structural guard for that — a test that fails when a channel has no offering
  condition — is the piece of work this audit makes possible rather than the piece it does.

---

## 7. What the next audit should add

* **A world-sim view pass.** `contests` and `declarations` were waived on the tracked-agent
  view without being rendered against the view they belong to.
* **The advisor view.** The council reads `ADVISABLE_ACTIONS` from the same catalogue, so it
  is structurally covered, but it was not rendered here.
* **The negative half, measured rather than asserted.** For every conditional channel, does
  the briefing say *why not* when the answer is no? Mediation and the cession block both had
  to learn this and neither is checked by a test.
* **A structural guard**, in the shape of `test_every_bounded_column_write_in_the_engine_is_clamped_by_its_writer`:
  a sweep that fails when a schema channel has no offering condition anywhere, with a recorded
  exemption list — this document's §5 is that list, written by hand for the first time.
