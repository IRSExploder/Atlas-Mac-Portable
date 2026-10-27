# ATLAS — the design map: Codex's ten gameplay recommendations, and where each one stands

**For:** Alex and Codex, together. **Prepared:** 2026-08-15, and **revised the same day**
after the overnight run.

* **The map as first written** was a snapshot at `718f9f0` on `master` — 3536 passing, 1
  skipped, 1 deselected (`live`). Every commit hash attributed to a row below is from that
  snapshot or earlier unless the row says otherwise.
* **This revision** is at `d662771` on `master` — 3916 passing, 1 skipped, 1 deselected
  (`live`); ruff clean over `src` and `tests`. Four rows moved:
  **#3** is no longer unknown and is now **DONE**; the *compare two timelines* half of **#8**
  is **DONE**; **#9**'s two structural gaps are closed rather than worked around; and the
  advisor, the action list and the alternatives all read the standing policy that #3 added.

This is a map, not a proposal. Each of the ten is marked **DONE**, **PARTIAL** or
**NEEDS DESIGN**, with one line on the machinery it stands on, and — for the ones that are
held — the questions that have to be answered before anybody writes code. The held ones are
held because they are *design*, not because they are hard: every one of them has a plausible
implementation that would be wrong in a way only Alex can adjudicate.

> **The gap that was here is closed.** Recommendation **#3** was not recoverable from my
> working record when this map was written — the numbered list reached me by number rather
> than by text. Alex supplied it (*national strategy / standing directives*) and it is now
> built. The row below is the real one.

---

## The map

| # | recommendation | status | where it lives |
|---|---|---|---|
| 1 | intent-to-plan / "describe anything" orders | **NEEDS DESIGN** | — (held) |
| 2 | partial-success alternatives on a refusal | **DONE** | `engine/alternatives.py` (`642f265`) |
| 3 | national strategy / standing directives | **DONE** | `engine/directives.py`, `ui/directives.py` (`df0a361`) |
| 4 | a "why is this true" inspector | **DONE**, bounded to two values | `engine/why.py` (`5299097`) |
| 5 | operations as multi-turn projects | **NEEDS DESIGN** | — (held) |
| 6 | playable domestic politics | **PARTIAL** | `engine/constituencies.py`, `engine/leaders.py`, `ui/homefront.py` |
| 7 | crisis mode | **NEEDS DESIGN** | — (held) |
| 8 | branching fast-follows | **PARTIAL**, one of the two held halves now done | `state/chronicle.py`, `state/branch.py` (`030ee0c`), `state/compare.py` |
| 9 | "what can I do about *that*" from the search box | **DONE** | `engine/against.py` (`718f9f0`) |
| 10 | a scenario editor | **PARTIAL** | `scenarios/workshop.py` (`d25b230`, `be9c068`) |

---

## 1. Intent-to-plan / "describe anything" orders — NEEDS DESIGN

**What exists that it would build on.** The parser already turns a sentence into *one*
action against a closed catalogue (`engine/capabilities.py` generates the schema enum, the
prompt menu and the advisor's list from a single declaration), and `engine/freeform.py` is
the existing answer to "the player asked for something the catalogue does not name".

**Why it is held.** Everything above turns a sentence into **one** act that the rules already
know. Intent-to-plan turns a sentence into a *sequence*, and a sequence is a thing the engine
has no representation for: nothing in the schema can hold "and then, if that works". Building
it means deciding what a plan *is* before deciding how to parse one.

**What Alex has to decide.**

* **Is a plan a first-class object or a convenience?** If the engine stores plans, they are
  state — they rewind, they fingerprint, they need a permission (whose? the player's own, or
  engine-only like `objectives`?), and a save from before them is a migration. If they are a
  convenience, the model expands a sentence into orders *once*, the player edits the result,
  and nothing is stored — which is much cheaper and gives up "the plan adapts".
* **What happens when step two becomes illegal?** A plan whose second step is refused can
  stop, skip, or ask. Each is a different game.
* **Does the player see the expansion before it runs?** "Describe anything" is a promise about
  *authorship*; a plan that executes unseen is the model playing for you.
* **Where is the line against the advisor?** `engine/advisor.py` already answers "what should
  I do"; if a plan is generated *and* run, the advisor and the order pipeline have merged and
  the player's role has changed.

**What was built instead, deliberately.** #9 below — a target, a filtered catalogue, and a
prefilled order. Its signature takes an id and not a sentence, and a test holds it to that,
because the moment it takes a sentence it is this item and this item is Alex's.

---

## 2. Partial-success alternatives — DONE

`engine/alternatives.py` (`642f265`). `causality.Refusal` grew an `instead`, and the refusal
sites across orders, gambits, war aims, objectives and peace terms fill it from the gate's own
data: the provinces actually occupied, the enemies actually at war, the regions actually
reachable, the undertakings that actually pass `gambit._requirements_met`.

**The discipline that makes it worth having:** an alternative is only ever offered when the
rules can back it. Nothing is invented, and `phrase()` returns an empty string rather than a
sentence when there is nothing legal to name — a refusal that suggests something also refused
is worse than one that suggests nothing.

**Not done, and not needed:** ranking the alternatives by how good they are. That is advice,
and advice has an owner (`engine/advisor.py`).

---

## 3. National strategy / standing directives — DONE

`engine/directives.py`, `ui/directives.py` (`df0a361`). A player had orders and never a
policy. Every turn began from nothing: the advisor argued each question on its merits, the
action list offered every legal act in catalogue order, and a refusal suggested whatever the
rules could back. None of it knew this government had spent six years avoiding a continental
war.

Six directives — avoid a continental war, contain a power, restore the old borders, keep the
seas, protect a client, industrialise. Two of them name a power and say nothing about anywhere
else. Three decisions carry the whole design, and each was Alex's:

* **It is the player's, not the world's.** A directive changes nothing about the world, so it
  lives in the **workspace** beside the watchlist and the draft orders — no schema, no
  fingerprint, and a rewind does not touch it. A note somebody wrote to themselves must not be
  undone by unwinding a turn. (`state/workspace.py`, the same sidecar the pins use.)
* **It advises and never acts.** Nothing writes a diff, orders anything, or removes a legal
  option. A government whose standing policy is to avoid a continental war may still declare
  one — it will be further down the list. The action list is the **same length** with a policy
  and without, and every offer carries the weight policy put on it (`Doable.policy`) so the
  ordering can be read rather than guessed. `test_nothing_here_can_reach_a_diff` holds the
  line at the source level: the module contains no `Diff(`, no `apply_batch`, no
  `record_event`.
* **It is about this government only.** What a *tracked nation* is trying to do is a
  different, engine-owned feature. Conflating them would put a player's private note into the
  world's state. **NPC doctrines are explicitly not this**, and remain unbuilt.

Where it is read: `engine/views.py` tells the advisor what the government is trying to do
(naming powers, not ids), `engine/against.py` orders the action list by it, and
`engine/alternatives.py` ranks a refusal's suggestions by it **before** the limit is applied,
so the cut keeps what serves the policy rather than what the catalogue happened to list first.
Two directives pulling opposite ways cancel, because the honest answer to "what does policy
say" is then nothing.

---

## 4. The "why is this true" inspector — DONE, bounded to two values

`engine/why.py` (`5299097`), reachable as `atlas why <nation>`. Two values, chosen because
they are the two a player most often disbelieves: **why a coup is likely** and **why a peace
will not be signed**.

**Nothing new is tracked.** Every figure is re-derived by calling the function the engine
calls, with the same constants; the only thing added is naming the terms. The load-bearing
property is that **the terms sum to the number the engine uses** — which took reporting each
multiplication as the difference it made rather than listing only the additive half. The first
version was out by 38% on a troubled government and looked entirely plausible.

**The obvious extension, and it is bounded too:** any other value computed by one function
from named inputs — war-aim satisfaction, trade viability, an alliance's regard. Each is
another `Why` with the same summing test. It stops being bounded the moment somebody wants
"why did the *world* do this", which is a causal history rather than an arithmetic breakdown.

---

## 5. Operations as multi-turn projects — NEEDS DESIGN

**What exists that it would build on.** More than it looks. `engine/projects.py` is already a
finite queue of things ordered now and delivered later, competing for the same slots as a
battleship; `engine/objectives.py` is already a tracked power saying what it wants done with
its army over time; `engine/waraims.py` already holds a war's purpose across turns; and
`engine/interrupt.py` already stops a long advance when something worth seeing happens.

**Why it is held.** Those are four different answers to "a thing that takes time", and an
operations system is either a fifth or a unification of the four. Which one it is *is* the
design.

**What Alex has to decide.**

* **Is an operation a new system or a generalisation of `projects`?** The queue, the slot
  competition, the pay-now-deliver-later shape and the rewind story are all built. A military
  operation that is "a project with a target region" is cheap; one with its own lifecycle is a
  subsystem.
* **Can an operation fail *partway*?** A project cannot — it delivers or it is cancelled. An
  offensive that stalls in month three is the interesting case, and it needs a
  representation for "in progress and going badly" that nothing currently has.
* **Who can order one — the player, tracked nations, or the world simulator?** `objectives`
  is engine-only by design and tracked nations reach it through a narrow channel. Operations
  ordered by the world simulator would be the first long-lived model-authored plan in the
  state.
* **What does it cost to abandon?** Without a sunk cost, an operation is a suggestion.

---

## 6. Playable domestic politics — PARTIAL

**What exists.** Three constituencies per nation (army, establishment, people) with support
that moves on what the government does and settles back toward a resting point;
`engine/leaders.py` with legitimacy, how a government came to power, and a coup risk that
reads all of it; `engine/unrest.py` for the cost of holding ground that is not yours; and
`ui/homefront.py`, where a player can see all of it. As of `5299097` a player can also ask
**why** the coup number is what it is, and what would move each term.

**What is held: the *playable* half.** Everything above is a consequence system — the player
acts abroad and the mood at home responds. Making politics playable means giving the player
acts aimed *at* it, and that is a game-shape decision.

**What Alex has to decide.**

* **Does the player govern, or manage?** A budget line for housing is management; a coalition
  that can defect is governing. The constituencies support either and imply neither.
* **Are factions people or numbers?** Three named blocs with support values are a mood.
  Factions with leaders, demands and memory are characters, and characters need a model call
  per turn — which is a cost per nation per turn, and the tracked-nation budget is already
  the scarcest thing in a jump.
* **Can domestic politics *stop* a foreign order?** A parliament that can refuse a war is the
  single highest-impact version of this and the one that most changes what the player is
  doing. It is also a hard "no" from the current shape: nothing can currently veto a player
  order except the rules.
* **What is the failure state?** A coup exists and removes a leader. Whether losing the
  domestic game ends the campaign, changes who you play, or merely costs you is unanswered.

---

## 7. Crisis mode — NEEDS DESIGN

**What exists that it would build on.** `engine/alerts.py` already ranks what a player should
worry about into four bands, with band three reserved for force or a state changing shape;
`engine/interrupt.py` already halts a multi-turn advance on a condition and does so *by
observation*, so stopping leaves the world byte-identical to not having asked for the extra
turns. Those two are, between them, "the engine already knows when something has become
urgent, and can already stop for it".

**Why it is held.** What is missing is not detection. It is what a crisis *is* as a mode: a
different clock, a different set of legal acts, a different information regime, or all three.

**What Alex has to decide.**

* **Does time change?** Monthly turns during a missile crisis are the wrong grain, and
  `engine/pacing.py` is built for exactly this (`span_of(days)`, and every rate compounds
  correctly since `c1eeb18`). But a variable clock means the player's other business also
  runs at the crisis grain, which is either the point or a tax.
* **Does the catalogue change?** An ultimatum, a mobilisation, a back channel — acts that only
  exist under pressure — are new capabilities with new gates, and a capability that exists
  only sometimes is a first for `capabilities.py`.
* **Is it mandatory?** A crisis you can ignore is an alert. A crisis you cannot is a modal
  interruption, and the engine currently has no notion of a turn the player must answer.
* **Who else is in it?** A two-power crisis and an eight-power crisis are different games, and
  the second one needs every participant to be a tracked nation — which is a real cost.

---

## 8. Branching fast-follows — PARTIAL

**Done** (`030ee0c`): the flat branch list is now a **tree**, indented by the lineage that was
stored all along; a fork can be **named by hand** (`state/chronicle.py:rename_branch`); and a
branch can be exported as a **clean campaign chronicle** — a readable turn-by-turn record,
optionally scoped to what one seat was entitled to see, with the fingerprint unchanged by
rendering it. A Windows connection leak in `branch._describe` was found and fixed on the way,
which was a real bug: the probe connection outlived its `with` block and held the file.

**Held, and flagged rather than guessed:**

* ~~**Compare two timelines.**~~ **Now done** (`state/compare.py`, `ui/branches.py`, and
  `atlas compare <left> <right>`). Alex answered the design question the way the held note
  posed it: a **curated headline**, never a diff. Nine questions a person actually asks —
  territory, wars, treaties, governments, economy, armed forces, nuclear balance, major facts,
  score — and anything absent is absent on purpose. Two further decisions fell out of
  building it. **The same world date**: two lines drift apart in turn number the moment their
  pacing differs, so turn 30 against turn 25 holds two different moments beside each other and
  calls the gap a consequence; the comparison happens on the last day *both* lines reached and
  says which. **Read-only, by copy**: reading a line as it stood earlier means unwinding it,
  so the file is copied to a temporary one, the *copy* is rewound, and it is deleted — the
  player's saves are opened `mode=ro` and come back byte for byte. A branch whose file is
  gone is a refusal with a reason on it rather than a comparison with one side silently blank,
  which would read as "this line has no wars".
* **In-app branch switching.** Not a design fork — a **window-lifecycle** one. Every overlay,
  the map applier and the pending orders hold a connection and a world id; switching means
  tearing all of that down and rebuilding it while the user watches. Doable, unglamorous, and
  worth its own session rather than an hour at the end of one.

---

## 9. "What can I do about *that*" — DONE

`engine/against.py` (`718f9f0`), in the search box and as `atlas against <nation> <target>`.
Finding a province or a power was where the box stopped; the question a player has next is
what they can do about it.

**Nothing is offered that the engine would refuse.** The catalogue's own availability test and
the undertakings' own requirement gate decide the list. The two gaps this section once
described as ones `Capability.available` *structurally could not* close — a declaration
against a power you are already fighting, and an offer of peace to one you are not — are no
longer worked around in `against.py`: `Capability.available` now takes an **optional target**
(`8405707`), the special-case table is deleted, and the catalogue answers the question itself.
What naming a target cannot supply is reported rather than guessed — a message still
needs its message, says so, and stays unclickable — and only whole orders can be taken, into
the same pipeline every other order uses.

**The line it does not cross:** the signature takes an id, not a sentence. That is #1, and #1
is held.

---

## 10. A scenario editor — PARTIAL

**What exists.** `scenarios/workshop.py` (`d25b230`, `be9c068`): a scenario can be **cloned
and changed without writing Python**, because an authored scenario is a base plus an overlay
of data edits, reconstructed with `dataclasses.replace`. The clone is auditable — the same
`atlas audit` that holds the shipped eras to the record of their period runs against it — and
the workshop screen shows the auditor catching a bad edit (a war set on one side only) rather
than merely claiming it would.

**What is held: authoring a scenario from nothing.** The overlay model edits *a base*. A new
era with its own map, its own powers and its own year has no base, and the artefacts it needs
(a territory file, a seed, a preset, a manifest of the period's record for the auditor to
check against) are four different kinds of thing.

**What Alex has to decide.**

* **Does an authored scenario have to be auditable?** Every shipped era is checked against a
  manifest of what actually happened. An invented 1840 has no record to be checked against, so
  either the auditor becomes optional — and the guarantee stops being a guarantee — or an
  author has to write a manifest, which is most of the work.
* **Map or no map?** Editing powers on an existing map is bounded. A new map is a bake, a
  geometry digest and a pack, and that is a tools project rather than a game feature.
* **Who is it for?** A designer changing 1962 to see what happens is served by the workshop
  today. A player building a fictional world is a different product with a different surface.

---

## How to read the held ones

Five remain held: **#1** (intent-to-plan), **#5** (projects), **#7** (crisis mode), the deeper
half of **#6**, and **#10**'s authoring-from-nothing half. None of them is blocked on
engineering. Each is blocked on a question that
would be answered *by* implementing it if nobody asked first — which is the failure mode this
map exists to prevent. The machinery lines above are there to make the cost visible: #5 and #7
are much closer to existing systems than they look, #1 and #6 change what the player's role
*is*, and #10 is two features wearing one name.
