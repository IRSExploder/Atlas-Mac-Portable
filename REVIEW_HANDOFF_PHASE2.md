# ATLAS — review handoff, phase 2 (the consolidation phase)

> **Second of three.** [`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) covers everything before
> this; [`REVIEW_HANDOFF_COLDWAR.md`](REVIEW_HANDOFF_COLDWAR.md) covers the Cold War era,
> the era-gate refactor and the air-reach fix its playtest uncovered.

**For:** the parallel Codex agent, reviewing the consolidation phase.
**Prepared:** 2026-08-08. **HEAD:** `45406a3` on `master`. **Schema:** 16.
**Tests:** 1620 passing, 1 deselected (`live`), ruff clean. Seed fingerprints unmoved.

> **`e11c330` was HEAD when this was written; `55804ac` fixes the defect disclosed in §7.1
> and is the tree to review.**

The first handoff (`REVIEW_HANDOFF.md`, HEAD `b73bf54`) covers everything before this. This
one covers `b73bf54..45406a3`.

The purpose is the same: help a reviewer find what we got wrong. Where I am uncertain I say
so, and §7 is the part I would read first if I were you — it is where I think the mistakes
are, including one I found while writing this document, disclosed unfixed, and have since fixed.

---

## 0. Git topology — re-checked, and it is unchanged

```
branches   : * master 45406a3     (no main, no others)
worktrees  : C:/Users/snejn/OneDrive/Desktop/Atlas Game  [master]
remotes    : (none)
status     : clean
stash      : empty
commits    : 192
```

**On Alex's own changes: still exactly one commit in the whole history lacks the
`Co-Authored-By: Claude` trailer**, and it is the same one the last handoff found —
`4863a053` (2026-08-01), *"Costed pricing model"*, analysis only, no engine change. Nothing
has appeared since. So either Alex's edits live in a clone this repository has no reference
to, or they have not been made. **Nothing in this tree is hand-made**, and a reviewer should
still establish whether a second working copy exists before trusting any "what changed"
statement — including this one.

---

## 1. What landed

| Commit | Item |
|---|---|
| `0e379e1` | **Sketch first.** All four items designed before any code, with the two departures argued in advance rather than discovered in the diff. |
| `92a1f4f` | **The chores sweep** — and the audit inside it found more than the chore did. |
| `12cd0bd` | **The replay cassette.** `providers/cassette.py`, 458 lines, 15 tests. |
| `62c0102` | **The treaty-clause seam**, schema 16. `commitment_term`, 18 tests. |
| `6e7a716` | **The phase-aware invariant checker.** `testing/invariants.py`, ten invariants, 20 tests. |
| `29c346a` | **PT33** — two live runs, both recorded, both replay. |
| `403943b` | **One region renderer + a gazetteer.** The naming bug, correctly diagnosed the second time. |
| `e11c330` | **`world_sim` re-pinned** off antigravity onto claude. |
| `55804ac` | **Cassette format 2** — the §7.1 defect, gated so the weak-key rescue applies only to format-1 tapes. |

New modules: `providers/cassette.py`, `testing/invariants.py`. New tests:
`test_cassette.py`, `test_invariants.py`, `test_treaty_terms.py`, `test_rewind_holes.py`,
`test_region_labels.py`.

---

## 2. Design departures from the brief, and why

Three, all argued in `0e379e1` before building. **A reviewer should attack these first,
because if any is wrong the thing built on it is wrong.**

### 2a. The cassette records at the provider seam, not `TurnEngine._invoke`

The brief said `_invoke`, on the strength of `b73bf54` calling it *the one door every call
passes through*. That is true of every call **inside a jump** and false in general. Six call
sites reach a provider without it:

```
engine/advisor.py:89   engine/diplomacy.py:157   engine/diplomacy.py:277
engine/eve.py:219      engine/parser.py:165      providers/diagnostics.py:190
```

`parse_orders` is on that list and runs for every order a human types. A recorder decorating
a `ModelProvider` sees all of them and needs no engine change.

**Consequence worth checking:** the routing-log guard `len(routing_log) == report.calls`
still only covers calls inside a jump. The cassette and the routing log therefore disagree
about what "every call" means, and neither says so.

### 2b. `MAX(id)+1` for commitment ids stays

The brief asked for ids safe across a rewind because ids are reused. **The reuse is real and
the conclusion is backwards.** A rewind deletes the row, so re-running the turn allocates the
same id and the world comes back byte-identical — which is what `rewind-fingerprint-mismatch`
checks and what a replayed cassette must reproduce. A monotonic allocator would hand the
second run id 8, move the digest, and turn every replay of a turn containing a treaty into a
permanent mismatch.

Written down in `diplomacy.next_commitment_id`, now the single allocator for both writers.

**If you disagree, the counter-argument to make is:** an external artifact (a bug bundle, a
`run.json`) citing commitment 7 points at a different agreement after a rewind. I judged that
cosmetic against a false positive on the gravest detector. That is a judgement, not a proof.

### 2c. `military_access` and `tribute` stay inert

Neither has an honest test against state that exists today. A grant of access is a permission,
not an obligation — nothing about the world says whether it was honoured. A tribute needs a
per-turn transfer, which is a balance surface no scenario would exercise and no test could
reach through play. Writing a check that fires on something adjacent would be the instrument
manufacturing its own findings, which is the failure PT31 had three of.

So the *mechanism* is general and the *coverage* is three clauses of nine kinds. Say so if
you think that is the wrong call.

---

## 3. What the id audit found — two more rewind bugs

Auditing the `MAX(id)+1` path found **two live instances of PT31's gravest bug class**, in
files decision 132's fix did not reach. Both wrote fingerprinted state with direct SQL:

* **`focus.py::reconcile_roster`** wrote `nation.is_tracked`, `tracked_since_turn` and
  `tracked_reason`. `fingerprint()` hashes every column of every fingerprinted table, so a
  power promoted on turn 5 and rewound to turn 4 stayed tracked and the digest moved. **This
  happens on ordinary turns of an ordinary game.**
* **`organisation.py::demand_ceasefire`** wrote a `commitment` row and its parties, so a
  Council resolution demanding a ceasefire did not unwind.

Both now write diffs. `apply_focus` takes the turn as an argument because the roster is
recomputed *before* the clock advances, and a batch filed under the outgoing turn sits outside
the range a rewind to that turn deletes — the same hole one line lower.

**The guard is structural** (`tests/test_rewind_holes.py`): a test that fails on any new
direct write to a fingerprinted table outside `state/`, with a four-entry allowlist that each
carry the reason a rewind restores them anyway, plus a second test that fails when an
exemption stops describing anything real.

**Reviewer focus:** (a) is the allowlist right? `world` is exempt because `rewind_to` restores
the clock explicitly; `eve.py` because it runs before turn 1; `diplomacy.py` because it is the
seed path with no event to cite. (b) The regex sweep is textual — a write assembled from a
variable table name would slip past it.

---

## 4. Self-corrections the tape enabled

**Both are cases where I reported something confidently and the recording proved me wrong.**
That is the strongest argument for the cassette and the strongest reason to distrust the rest
of my reporting.

### 4a. "Modern Day routes `world_sim` elsewhere, so it had zero failures" — false

Both PT33 runs used the same pins. **Modern Day's world sim was also antigravity, and it
answered 12/12 on prompts seven times larger** (75.7k chars against the sandbox's 10.7k).
Re-probed with the exact prompt it had failed 17 times, agy answers **3/3, fastest of the
three providers**.

So the failures are **episodic** — the long-documented agy flake, in a bad window an hour
after a clean run — not a size limit and not incapacity. The re-pin still stands, on the
narrower ground that an unpredictable provider on the one call every turn depends on is a
worse risk than a slow one. The correction is written into the PT33 entry in the changelog
rather than quietly amended.

### 4b. "The briefing composes kind + name into *Coastal Reaches*" — false

No view does. Grepping every recorded briefing: the phrase appears **only inside event text**,
first at turn 1 in prose a model wrote, and `r_coast` appears in **no tracked-agent prompt of
the entire run**. The chain was: a model was shown the region's name *with no id beside it*,
embellished it in prose, the engine filed that prose as history, every later briefing quoted
it back, and both staffs read it and wrote the invented name. `The Reaches` and `Reaches` both
resolve; `Coastal Reaches` does not, and should not.

The fix therefore has two halves, and the first does not reach the bug: one renderer
(`reference.label`, `Name [id]`, with a `resolve` tier that reads a displayed label directly)
**and** a gazetteer, because a display fix cannot reach a place that is never displayed.

---

## 5. Outstanding — for review or for Alex

### 5a. 52 phantom alliances *(Alex's call, not a bug)*

Modern Day asserts 52 relationships in `relation.treaty_status` — NATO among them — with **no
commitment row behind them**. They satisfy every reader of the column and **cannot be
breached, because there is nothing to break**. The invariant checker deliberately does not
report them: a scenario is authoritative about the world it describes, and recomputing the
column from commitments would demote all 52 to `none` and delete most of the map's diplomacy.

Turning them into commitments would make them breachable and term-bearing. That is a scenario
change with balance consequences — every war between allies would then cost −35 regard and −6
legitimacy on top of the war itself.

The invariant *does* report the other direction (an agreement the column hides), which found
and fixed 4 real cases in 1936 and 0 remaining in either era.

### 5b. Route-opening is proven only in its negative case

Five negotiations ran live — three in Modern Day, two in the sandbox — and **all five
counterparts declined**, with reasons that were correct for their dispositions. The extraction
ran, recorded nothing, and wrote no terms. So the path is verified end to end *for the case
where nothing is agreed*. **`open_trade` has never opened a route against a real model.**
Proving the positive needs two powers with a reason to agree, which the current dispositions
deliberately do not provide.

### 5c. Both cassettes are now historical

`403943b` changed model-facing prompts, so the two recorded tapes will now **miss on replay**
— loudly, with the message saying the engine is asking a different question. That is the
designed response to an intentional prompt change, and the tapes remain accurate records of
what was asked and answered on 2026-08-08. But **there is currently no live tape that
replays**, so the regression fixture the phase was built around is empty until the next
recorded run.

### 5d. `_reconcile_seeded_treaties` only runs at world creation

An existing save carries its inconsistency forever. Deliberate — a migration that rewrote a
running game's diplomacy would be deciding something the scenario decided — but it means the
invariant will fire on old saves and the fix will not reach them.

### 5e. Smaller, all flagged rather than fixed

* `open_trade`'s breach test and `no_war`'s both reduce to "at war", so a pair holding **both**
  a non-aggression pact and a trade agreement takes **two** breaches for one war: −70 regard,
  −12 legitimacy. Floored at −100 and never measured in play.
* `honour_terms` returns silently when its batch is refused; nothing is reported.
* The gazetteer adds up to eight lines to the tracked-agent briefing. **The prompt-size delta
  on the 2026 map is unmeasured** — the 8-line cap was chosen, not calibrated.
* `commitment_nation` is in `apply.PRIMARY_KEY` but **not** in `_FINGERPRINT_TABLES`. Parties
  to an agreement are therefore diffed but not fingerprinted. Pre-existing, noticed here, not
  changed.

---

## 6. What to review first — riskiest surfaces, ranked

1. **`providers/cassette.py::_take` and the format-2 gate on the `asked()` fallback.** See
   §7.1 — disclosed unfixed, since fixed. What to check is the gate itself: that a format-2
   entry is genuinely unreachable by the weak key, that the format-1 rescue still finds a
   nonce-bearing entry, and that `_forget` still consumes from every index an entry sits in
   (a repeated question handing out the same answer twice would be the next bug in this
   shape).
2. **`engine/focus.py::apply_focus`** — the roster now goes through the diff pipeline, and
   **the interaction between partial apply and the `trg_focus_cap` database trigger is
   untested.** Demotions are ordered before promotions inside one batch so the trigger sees a
   free seat; if a rule severs a demotion while keeping a promotion, the trigger aborts the
   transaction. `SEVER_MINORITY` was only ever tested on SETs, which the first handoff also
   flagged, and this adds a case where severance has a *database-level* consequence.
3. **`engine/treaties.py::terms_of` and the implied-terms fallback.** The whole compatibility
   story is "an agreement with no terms carries the terms its kind implies". If that fallback
   is wrong, every existing world and both seeded eras change behaviour silently. Check
   especially that a `trade_agreement` with no terms implies **nothing** — that is what keeps
   the seed fingerprints still.
4. **`engine/reference.py::resolve`'s new `_LABELLED` tier.** It reads an id out of brackets
   and returns it before any name matching. The regex accepts `[A-Za-z0-9_.:-]+` inside either
   `[...]` or `` (`...`) ``. **Is there a real region name that ends in a bracketed token?** If
   so, that name now resolves to something else. I checked all 917 regions across both eras
   round-trip, which is not the same as checking no name is misread.
5. **`engine/views.py::_places_named_above`** — the gazetteer. Two questions: does it disclose
   anything? (I argue not: the prose is the input, so a place a staff cannot see cannot be
   named — but it does confirm the *canonical name and id* of a place an event merely
   mentioned.) And is it worth its tokens on a map where a busy turn names twenty provinces?
6. **`testing/invariants.py`'s `holds_in` column.** The phase assignments are the product. An
   invariant that claims to hold in `SEED` when it does not will fire on a correctly seeded
   world; one that claims `MID_TURN` when it does not will fire on the engine working. I
   assigned all ten by reasoning, and only `no-standing-pact-between-belligerents` has a test
   proving the distinction matters.
7. **Schema 16 / `commitment_term`.** Additive, new table, no enum widened, migration test
   passes both directions. But it is now in `_FINGERPRINT_TABLES`, which changes the world
   digest for every world — harmless because digests are only compared within a version, and
   worth confirming you agree.
8. **Fingerprints.** `2c90c4dc…` (1936) / `6f6e863a…` (2026), unmoved, every section hash
   unmoved. Re-verified after schema 16 and after the seed reconciliation.

---

## 7. Where I am least confident

### 7.1 The cassette could replay silently when it should miss loudly — **FIXED in `55804ac`**

> **Update.** This was disclosed here unfixed and has since been fixed, at cassette format 2:
> new tapes carry canonical keys and are indexed by digest alone, while format-1 entries —
> and only those — keep the weak-key rescue, with the format travelling on the entry rather
> than the file. The demonstration below is now a regression test, alongside one that the
> same-schema replay still hits, one that a format-1 nonce-bearing entry is still rescued,
> and one that the same entry at format 2 is not. The miss message also distinguished the
> wrong cause and now tells the three apart. **Still worth reviewing** — the description
> below is what it did, and the gate is the thing to check.

I found this writing this document and am disclosing rather than quietly patching it.

`ReplayProvider._take` tries the request digest and then falls back to a weaker key,
`asked(role, model, canonical_prompt)`, which **does not cover the schema or the options**.
The fallback exists to rescue the PT33 tapes, whose keys were computed before the untrusted
nonce was normalised out and cannot be recomputed from an `Entry`.

But the fallback is consulted **unconditionally**, so a change to a response *schema* — the
engine asking for a different shape of answer — misses the digest and then silently hits the
weak key. Demonstrated:

```
record with schema A, replay with schema B  ->  SILENTLY REPLAYED {'v': 1}
```

That defeats "a miss is loud" for exactly the class of change the cassette exists to catch.
**The fix I would make:** bump `FORMAT` to 2, write canonical keys on new tapes, and consult
`_by_asked` only for `FORMAT`-1 entries — so the shim dies with the tapes it was written for.
I have not done it because this turn was scoped to the handoff.

### 7.2 The rest, in order of how much it would cost to be wrong

* **Whether `open_trade` should breach at all.** I argued a state that goes to war with its
  customer has stopped supplying it. A reviewer could reasonably say a trade agreement is not
  a promise not to fight and the route closing is punishment enough.
* **The `label` round-trip is proved over vocabularies, not over prompts.** The tests assert
  every label resolves and that no view uses the old form. They do not assert that everything
  a model is shown *is* a label — a new renderer could still print a bare name and pass.
* **Whether the invariant checker returning zero on its first live outing is good news.** Ten
  checks, two full runs, nothing. Either the engine is sound in those ten respects or the
  checks are weaker than they look. I lean toward the first and would not bet heavily.
* **The world-sim re-pin's latency claim rests on four calls.** 22s and 37s on claude against
  16s and 22s on agy, on one Modern Day prompt. That is enough to overturn the 65–83s figure
  that looked disqualifying; it is not enough to characterise the distribution.
* **`all_mentioned` and `label_of` are both linear scans of the region vocabulary**, called
  per briefing and per hit. Up to ~3800 comparisons per tracked-agent view on the 2026 map.
  Correct, and wasteful in the place the engine spends most of its time.

### 7.3 Unchanged from the first handoff, still open

`test_runtime.py::test_an_exception_becomes_a_failed_signal` is still load-flaky (Qt event-pump
timing). The nuclear channel's *named unblocker* — a confirmation step — remains unbuilt. The
capability manifest's availability predicates are still only two, and still barely exercised.
