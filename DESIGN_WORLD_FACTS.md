# ATLAS — design: persistent world facts

**For:** Alex, alongside `DESIGN_WAR_LOOP.md`, while the larger sequencing is decided.
**Prepared:** 2026-08-13. **Status:** the fact store is built (schema 26); initiatives,
the anything-funnel and the effect vocabulary are **not started**.

> **The central distinction, stated once.** A **historical fact** is something the world
> remembers and reasons from. A **mechanical effect** is something the engine computes. They
> are separate, they are stored separately, they are shown separately, and **a fact never
> implies an effect**. This is enforced structurally, not by convention: the module that
> mints facts addresses exactly one table, and a test asserts it.

---

## 1. Scoping: how the world currently remembers anything

### 1.1 Events are the memory, and they scroll

`event` (`schema.sql:563`) is the causal spine: turn, date, actor, a **closed** `kind` CHECK,
`origin` (`ENGINE`/`MODEL`), `visibility` (`public`/`bilateral`/`secret`), text. Two edge
tables hang off it — `event_cause` (the DAG) and `event_participant` (`actor`/`target`/
`defender`/`beneficiary`/`aggrieved`) — plus `event_visibility` (who else may see a
non-public one).

**The problem this layer exists for:** an event is *news*, not *state*. A briefing shows a
window of 25–30 events (`views.py::_briefing_events`), ranked by salience since a coup was
being pushed out by six completed workshops. Anything said only in prose is forgotten four
turns later. "Danzig is now called Gdansk" written as an event is true for a month.

### 1.2 How a prompt is assembled today

| View | Scoping | Assembled from |
|---|---|---|
| `build_tracked_agent_view` | one nation's knowledge | `visibility.nation_self` / `other_nations` / `own_regions` / `standing_commitments` / `fronts_of`, `_briefing_events`, then per-topic sections (occupation, objectives, war aims) |
| `build_diplomacy_view` | the same, plus the bilateral pair | as above, plus `relations_of`, shared commitments, the conversation so far |
| `build_world_sim_view` | **omniscient** | `state.referee` — `all_nations`, `all_events`, … in its own module *so that an unscoped read is visible in review* |

That last line is the house rule: `state/visibility.py`'s docstring says there is deliberately
no `get_all_events`, because "once an unscoped reader exists, some future call site uses it
'just for a moment'".

### 1.3 How visibility actually works

One predicate, in `visibility._rows`:

```sql
e.visibility = 'public'
OR e.actor_nation_id = ?
OR EXISTS (SELECT 1 FROM event_visibility v WHERE v.event_id = e.id AND v.nation_id = ?)
```

and one rule enforcing the other direction — **INFO-002**: a change may not be marked more
widely visible than the event it cites. *A covert cause cannot produce overt state.*

### 1.4 How rewind works

`rewind_to` (`state/replay.py:81`) undoes applied diffs newest-first, then
`DELETE FROM event WHERE turn > ?`. Foreign keys are ON (`db.py:88`), so everything hanging
off a deleted event cascades — which is exactly why `event_participant` and `event_cause` are
listed as `_NOT_DIFFED` in the rewind-holes test with the note "cascades with the event that
owns it".

**So a facts layer keyed to its source event gets a clean rewind for free**, and needs the
diff pipeline only for the one thing a cascade cannot do: putting back a fact that was
*retired* on an undone turn.

---

## 2. The model

```
world_fact(id, world_id,
           subject_kind, subject_id,        -- nation | region | organisation | world
           predicate,                        -- open text against a Python registry
           object_kind, object_id, value,    -- a reference, or a phrase
           start_turn, end_turn,             -- end_turn NULL = still believed
           source_event_id,                  -- ON DELETE CASCADE. the causal link
           visibility, confidence)
```

**Design calls worth arguing with:**

1. **`predicate` is open text, not a CHECK.** The whole value of a facts layer is that a
   world can remember things nobody enumerated in advance, and every widening of a SQLite
   CHECK is a table rebuild (the same constraint that pushes structured peace terms into
   their own table). Meaning lives in `engine/facts.PREDICATES`, an unregistered predicate is
   refused at the write path, and adding one is a dictionary entry rather than a migration.
2. **A fact is retired, never deleted.** `end_turn` means "Danzig was called Gdansk between
   1945 and 1990" stays askable — and gives a rewind something to put back.
3. **`confidence` is recognition, not certainty.** A state four powers accept and three do
   not is four facts, not one fact with a probability. Recognition is per believer.
4. **`single_valued` is per predicate.** A new name retires the old one; a new pledge does
   not retire a different pledge. That is a fact about the predicate, so it lives in the
   registry.
5. **Soft references.** `subject_id` cannot be a foreign key because it may name a nation, a
   region, a body or the world; it is validated at the write path instead.

### The vocabulary shipped

`named` · `recognises` · `disputes` · `claims` · `pledges` · `doctrine` ·
`official_language` · `commemorates`

Covering every example in the proposal: a city renamed, a disputed state recognised, a
doctrine announced, an official language set, a no-first-use pledge, a holiday created.

### Fact vs effect, in practice

A rename **writes no column**. `region.name` stays what the scenario laid down; `display_name`
is what the world calls it now, and the briefing renderer uses that. Nothing about the
ground, its owner, its population or its worth moves — which is the line the whole module is
drawn along. A test snapshots `nation`, `region`, `unit` and `relation` around a fact write
and asserts nothing moved.

When a described policy *should* cost something, that cost belongs to the initiative and
effect layers: a separate row, separately shown, with the engine owning every number.

### How facts reach a prompt

- **Tracked-agent briefing** — a *What the world remembers* section, scoped by
  `visible_facts`, which tells the reader in words that none of it moves a number.
- **Diplomacy view** — *What is remembered about the two of you*, filtered to the pair.
- **World sim** — `every_fact` exists, is named to make an unscoped read obvious, and is not
  yet wired into the referee view (deliberately: one context was the brief, two shipped).

Both sections are **conditional on there being facts**, so a world with none produces a
byte-identical prompt to before — which is why this brick costs the cassettes nothing.

---

## 3. What landed

`world_fact` (schema 26, additive, `invalidates_saves=False`), `engine/facts.py` (registry,
write path, four read paths, `display_name`), briefing + diplomacy surfacing, and 26 tests.

**Costs:**

- **Digests moved by one table name's width**, as with schemas 16/21/23/25:
  `gathering-storm 719aedd4…`, `cold-war 2ca6c5a7…`, `modern-day 4cafeb1b…`.
- **No schema channel, so no cassette impact at all** — unlike the war-aims channel, nothing
  in a model's request payload changed.
- One thing the rules taught us mid-build: INFO-002 refused the first secret fact, because
  the diff was marked public while its cause was secret. The scope of a fact's write is now
  the scope of the event that caused it. The rule was right.

---

## 4. The seam — why this serves both threads

**Facts are where the war loop's paper goes.** A peace treaty is a commitment (enforceable,
breachable) *and* a set of facts (this border is recognised; this state exists; this power
guaranteed that one). Recognition, guarantees and renamed provinces after a settlement are
all `world_fact` rows with the peace event as their cause — which is why layer 4 of the war
loop should mint facts rather than grow its own memory.

**And a persistent fact is one of the funnel's six outcomes.** When free text becomes
"the world now believes X", `record_fact` is already the destination, on the same terms the
engine uses today.

So the ordering question in front of you is *not* "war loop or funnel" for this brick — it is
built and both need it. What is genuinely exclusive is the next step: **war-loop layer 2
(exhaustion + the casualty ledger)** or **funnel step 2 (universal initiatives and the effect
vocabulary)**. My reading, offered and not acted on:

- The war loop is **half a mechanism** right now — aims with nothing to settle them. Finishing
  layers 2–4 closes a loop the player can feel, and gives #10 something to prove.
- The funnel is **wider but shallower first**: the anything-funnel without the effect
  vocabulary mostly produces facts (built) and refusals (built). Its real lever is
  initiatives, and initiatives want the effect vocabulary, which is the largest single design
  in either thread.

Either way, nothing built here has to be revisited.
