# ATLAS — review handoff

> **This is the first of three, and it is the oldest.** It covers the tree up to `b73bf54`.
> The consolidation phase, `b73bf54..e11c330`, schema 15 → 16, is in
> [`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md). The **Cold War era** — a third
> playable era, the era-gate refactor and the air-reach fix it uncovered — is in
> [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md).
>
> Read this one first for the background it establishes; read the later two to review what
> is new.

**For:** a second reviewer (Codex), reviewing recent work on the engine.
**Prepared:** 2026-08-07. **HEAD:** `b73bf54` on `master`. **Schema:** 15. **Tests:** 1533
passing, 1 deselected (the `live` marker), ruff clean.

The purpose of this document is to help a reviewer find what we got wrong. Where I am
uncertain, I say so rather than smoothing it over.

---

## 0. Git topology — read this first

**There is exactly one branch, one worktree, no remote, and no stash.**

```
branches   : * master b73bf54          (no `main`, no others)
worktrees  : C:/Users/snejn/OneDrive/Desktop/Atlas Game  [master]
remotes    : (none)
status     : clean
stash      : empty
```

### On "Alex's own changes"

The brief for this handoff said Alex had made some changes himself. **I cannot see any such
changes from this repository, and I want to be precise about what that does and does not
mean.**

Every commit in this repo is *authored* `Alex <misterpick2011@gmail.com>` — that is the
local git identity, so authorship cannot distinguish his work from mine. The usable
discriminator is the `Co-Authored-By: Claude` trailer, which my commits carry and a
hand-made commit would not. Scanning all 183 commits for that trailer, exactly **one**
commit lacks it:

- `4863a053` (2026-08-01) *"Costed pricing model: measure the cacheable prefix, price the
  tiers"* — analysis only, two scratchpad tools, **no engine change**. It predates the
  entire arc below and touches none of it.

So one of the following is true, and I cannot tell which from here:

1. Alex's changes live in a **different clone or worktree elsewhere on disk** that this
   repository has no reference to (no remote, so nothing to fetch);
2. they exist only as uncommitted edits in that other location;
3. they have not been made yet, or were made and discarded.

**This divergence is itself a finding.** If Alex has a second working copy, then this
handoff describes only half the picture, and the two trees will need reconciling before
review conclusions mean anything. A reviewer should establish where that tree is and diff
it against `b73bf54` before trusting any "what changed" statement here — including this
one. I have not assumed his changes fix anything, because I have not seen them.

### Correction to the brief

The brief listed the **rewind bug** and the **observability gap** as outstanding. Both were
fixed after that brief was written:

- rewind bug → `f50af32`
- observability gap → `b73bf54`

I have moved them to §4 (*recently fixed — review the fix*) rather than §5 (*still open*).
Reviewing them as open bugs would waste the reviewer's time; reviewing the fixes is the
useful thing, and both are load-bearing.

---

## 1. The arc, with hashes

88 files, +12,276 / −259 across four batches.

### Batch A — feature depth (2026-08-04)

| Commit | What |
|---|---|
| `459d1d1` | Roadmap: depth items ordered by dependency |
| `b7f8e85` | **Real economic geography.** `population = min(120, area×0.4)` and `minor economy = 4 + area×0.02` were the last places map *shape* stood in for *substance*. The clamp was worse than the ratio: USA, Australia, Brazil, Canada and Antarctica all had identical populations and army caps. New `scenarios/demography.py`, normalised to preserve prior world totals. **Moves fingerprints (intentional, decision 96).** |
| `bb22711` | Three map overlays: development, unrest, air superiority |
| `b47e0a5` | Zoom stutter fix |
| `6d2e51d` | **Situation report.** Column 1 was the first 8 events *by row id* — insertion order. Added `JumpReport.significant()` gravity ranking. Also found `Role.ORGANISATION` had **no entry in `default_role_models()`**, so the first Security Council session in the shipped app raised `KeyError` and killed the turn. |
| `2d74fa2` | **Leaders** (schema 13): traits as multipliers on existing levers, coups, succession |
| `733b5bb` | Coup roll moved out of the round loop — 48 calls were 8 independent draws |
| `ed52709` | Modern leader names filled in; two dead 1936 keys removed |
| `cbd26ea` | **Partial apply.** A batch with 24 changes and 3 mistakes landed *nothing*. Severability is now "a violation names a diff", bounded by `SEVER_MINORITY`. |
| `fd8bce3` | Master-AI investment: build where a work is worth something, not alphabetically |
| `8a7adc1` / `5c5b1f7` | **Victory conditions.** Nothing stored; a victory is a fact in the event log. |
| `8deecec` / `b8e48a6` | **Logistics.** No schema at all — supply is a reading of the map. Seas are already regions, so blockade came free. |
| `1e25a35` / `6662b33` | **Trade & resources** (schema 14). Three goods. Shortage stops you *building*, not earning. |

### Batch B — playtest seam fixes (2026-08-07)

Found by a real-provider run by the parallel Codex agent.

| Commit | What |
|---|---|
| `f280099` | **Treaties now bind.** A negotiated pact wrote a `commitment` row and nothing else, while the invasion guard, gambit odds, router and four briefings all read `relation.treaty_status`. |
| `22819bc` | Parser couldn't order research or amphibious landings — the resolver knew things the parser was never told |
| `f882b23` | Objectives accept displayed names; Council decline leaves a record; domination requires a unique leader |
| `0c93b95` | Player undertaking channel (sabotage, air strike); word-boundary prose clipping |
| `5c602e5` | CORE-004 compares at the precision the model was shown, per field |
| `35b2af3` | **Provider tests were all failing** — see §3 |

### Batch C — the 8-item structural program (2026-08-07)

| # | Commit | Item |
|---|---|---|
| 1 | `123e56a` | **Capability manifest** — one catalogue; parser enum, council list, prompt menu generated |
| 2 | `9d5ad54` | **Reference boundary** — one resolver, five vocabularies, ambiguity as a distinct answer |
| 3 | `0682d8c` | **Treaty lifecycle + breach** (schema 15) |
| 4 | `cd31c0c` | **Player undertakings** generalised; nuclear deferred |
| 5 | `2cef2c5` | **Causal turn reports** — correlation ids, causal breakdowns |
| 6 | `0533e9a` | **Playtest laboratory** — ten suspicion detectors, portable bug bundle |
| 7 | `6995108` | **Scenario-scaled victory** |
| 8 | `58d634c` | **Snapshot-parallel focus calls** |

Sketches committed before builds: `cf35f4a`, `66ff4e7`.

### Batch D — PT31 and its consequences

| Commit | What |
|---|---|
| `13e7a46` | **PT31**: first live lab run. 16 turns Modern Day + 12 sandbox, 126 calls. Found 1 real bug and **3 defects in the lab itself**. |
| `f50af32` | **Rewind bug fixed** — see §4 |
| `b73bf54` | **Observability gap fixed** — see §4 |

---

## 2. Key design decisions

**Nuclear launch stays out of the player channel** (`cd31c0c`). Explicit and unambiguous
were achievable; *confirmed* was not. **There is no confirmation step anywhere in this
architecture** — an order runs text → parser → action → handler inside a single `jump()`.
Without one, "explicit" reduces to *the parser emitted the exact string*, and the parser is
a language model reading a typed sentence. **Named unblocker:** if a confirmation step is
ever built for anything, this becomes a five-line change.

**`treaty_status` as a materialised view of commitments** (`f280099`). One column, seven
readers. `commitment` is the source of truth; the column is its view, recomputed from
scratch so signing and breaking use one code path. Not every kind maps — a `ceasefire`
exists *because* of a war and would otherwise make marching in stop being an invasion.

**Isolation by filesystem identity, not spelling** (`35b2af3`). Deliberately *not* a
textual rewrite of the MSIX path, which would accept any other package's private store by
spelling alone.

**The capability manifest's closure runs both ways** (`123e56a`). Verified by breaking it
on purpose.

**Supply and scoring store nothing.** Both derived, so rewind-exactness is free.

---

## 3. The provider-test path fix (`35b2af3`)

**Every provider test had been failing on this machine** — 2 failures, 8 collection errors.
`assert_isolated` refused the directory `SandboxDir.create()` had just made: MSIX
virtualises writes under `%LOCALAPPDATA%`, and the redirect appears on *newly created*
entries but not on the ancestors they're created under. So the check compared a redirected
child against an unredirected parent.

**The failure was never silent — it became invisible when I excluded the module to report a
green number.** Two guards added: an end-to-end test through the real objects, and a
structural test that fails if any provider test acquires a `skip`/`xfail`.

---

## 4. Recently fixed — please review the fix, not the bug

### 4a. The rewind bug (`f50af32`) — was the gravest

Fingerprinted state written outside the diff pipeline did not rewind. A coup on turn 3
rewound to turn 2 **left the junta in the chair**; a pact signed on turn 3 was still active
with `treaty_status` still `non_aggression`.

`leader`, `commitment`, `commitment_nation` and `relation.treaty_status` now move through
diffs. `seat()` *requires* an `event_id` (an optional citation would mean an optional
rewind). Commitment ids are chosen at write time because an insert diff must *name* the row
it created.

**Two rules caught this before any test did.** INFO-002 refused every extraction — the
diffs were `PUBLIC` and a negotiation is `BILATERAL`. CORE-001 then refused the
reconciliation — the batch was built at `world.turn_number` while citing a later turn's
event.

**Reviewer focus:** (a) is the visibility inheritance right for *every* commitment path, or
only the ones I exercised? (b) `MAX(id)+1` for commitment ids — safe under the current
single-connection model, but I have not reasoned hard about concurrent writers. (c) The
`_logged_turn` / batch-turn interplay when a call happens outside a jump.

### 4b. The observability gap (`b73bf54`)

126 calls made, 81 logged. Logging moved into `_invoke`, the one door every call passes
through. Guard is behavioural: `len(routing_log) == report.calls`.

While writing that test I found **`MockProvider(always(...))` silently discards its
script** — `name` is the first field, `script` the second. Six scratchpad harnesses had been
running on schema defaults, **including `pt_report.py`, whose output I read and reported on
earlier in this session**. It also sent me chasing a phantom for three probes during the
rewind fix. `__post_init__` now rejects a non-`Provider` name.

**Reviewer focus:** any conclusion drawn from those six harnesses before this fix should be
treated as unverified.

---

## 5. Still open

### 5a. Deferred objectives are filed as rejections *(confirmed present)*

`turn_engine.py` ~line 1376:

```python
report.rejections.extend(
    f"{entry['name']}: {problem}"
    for problem in (*declared.refused, *declared.deferred)
)
```

A **deferred** objective is *accepted* — *"borders none of your territory yet; the objective
stands"*. Filing it under `rejections` makes an accepted objective look refused to
everything downstream, including the turn report and the lab's `impossible-objectives`
detector (which fired on it in PT31 until I excluded the phrase). Small, real, and the
detector's misfire was a symptom rather than the disease.

### 5b. PT31's lab defects — fixed, but the corrected lab has never run live

Three defects in the instrument (actor prefix read as evidence; substring matching; deferred
counted as impossible) plus a bundle-serialisation gap. All fixed and re-scored — **both
runs come back clean** — but that re-score was against *recorded* runs. The corrected
detectors have not been exercised against a fresh live world where coups and treaties
actually happen, which is exactly the case mocks cannot reach.

### 5c. Nation-id-where-a-region-is-expected

PT31 saw a tracked power write `blr` (a nation id) as a region objective. The refusal is
correct; whether the reference boundary *should* cross-resolve a nation to the regions it
holds is an open design question. One occurrence in 16 turns.

### 5d. Things I flagged and did not pursue

- `test_runtime.py::test_an_exception_becomes_a_failed_signal` is **load-flaky** — fails
  under full-suite load, passes 3/3 in isolation and on clean HEAD. Qt event-pump timing.
- Scenario-authored `treaty_status` and the commitments seeded beside it are **not**
  reconciled at seed. Deliberate (the scenario is authoritative), but it means the two can
  disagree from turn 0 and nothing checks it.

---

## 6. What to review — riskiest surfaces first

1. **`state/apply.py` + the diff pipeline** — partial apply (`cbd26ea`) and the new
   `PRIMARY_KEY` entries (`f50af32`). Everything else rests on this. Specifically: does
   `SEVER_MINORITY` interact correctly with insert/delete diffs? I only tested it on SETs.
2. **`engine/leaders.py`, `engine/diplomacy.py`, `engine/treaties.py`** — the rewind fix.
   The visibility-inheritance and batch-turn rules are new and subtle.
3. **`config/paths.py`** — `_contains`. Weakening this weakens the guarantee that model
   calls cannot run in a source tree. Ten cases pinned; look for an eleventh.
4. **`engine/capabilities.py` + `engine/reference.py`** — the closure tests are the thing
   stopping the seam bugs recurring. Are they actually exhaustive?
5. **`testing/laboratory.py`** — the detectors are the product. **Assume they are wrong
   until proven otherwise**; PT31's first outing produced three false positives and one true
   finding.
6. **`turn_engine.py::_run_tracked_agents`** — the parallel change. One snapshot for all
   agents is a *semantic* change: agents no longer see each other's moves within a turn.
7. **Fingerprints.** `2c90c4dc…` (1936) / `6f6e863a…` (2026), unmoved since decision 96.
   Any change moving them without a per-section proof is a bug.

### Where I am least confident

- Whether the **capability manifest's** availability predicates are right, as opposed to
  merely consistent. They are barely exercised.
- Whether **trade** endowments are balanced. Calibrated against demand ratios, never played
  through a long war.
- Whether the **supply range of 3** holds up on a map with finer provinces. Swept on the
  current maps only.
- The **breach detector's** attribution when a war has multiple declarations — I take the
  most recent, which I believe is right and have not proved.
