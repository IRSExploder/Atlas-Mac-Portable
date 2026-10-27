# Session handoff — 2026-08-28: the recovery merge, and what is parked

**Read this first.** One correctness fix merged to master with a clean 8/8 and a fresh
tape. Everything else in this session is **staged, recommending nothing**, or **parked
deliberately**. No victory threshold, metric, cooldown, Concert, Hegemony or
strategy-fitness constant was changed anywhere.

---

## 1. What is on master

| commit | what |
|---|---|
| `7d8c1fb` | **Merge `safe-recovery`** — the recovery-policy correctness fix, its tests, the Works and cession rigs and evidence, three staged decision documents. |
| `1a3880e` | The pt74 tape's **fingerprints file**, which `7d8c1fb` missed. See §4. |

**Release audit: 8/8 PASS**, suite 4326 passed / 1 skipped / 1 deselected, ruff clean,
`pt74-cold-war` replays 12 matched / 0 mismatched / 0 missed.

Taken on `c21ac7c` (the `safe-recovery` tip). `git diff c21ac7c 7d8c1fb` is **empty** — the
audited tree and the merged tree are byte-identical, which is why it was not re-run after
the merge. `1a3880e` adds one cassette artifact and touches no code.

### The fix itself

`_RESTORATIVE` led with `land_reform`, the catalogue's only `risk_from_legitimacy` gamble:
**+9 stability** when it holds, **−8 stability and −3 legitimacy** when it does not — and
legitimacy is the number the *next* attempt's odds are read off. The government with the
least legitimacy, and so the worst odds, took the gamble ahead of two certain answers.

Now: under strain a government takes a **certain** restorative (housing +4, health +3), or a
gamble whose expectation is genuinely positive, or **nothing at all** — holding its money and
reporting that as a `bank`, so a deliberate save is distinguishable from a chooser that found
nothing. `land_reform` is **preserved**, gated on `expected_stability` being positive.
`success_chance` is one definition with two callers, so the policy that weighs the bet and
the dice that settle it cannot read different odds.

**The arithmetic is from the per-attempt probability, never a realized failure count.**
Break-even is `p = 8 / (8 + 9·worth)`; Russia is `hardliner` (0.95 on development) so it needs
**0.4834** and starts Modern Day at legitimacy 44. Across six paired runs the old policy's
mean odds ran **0.199 to 0.435** — every gamble it took was negative expectation, including
the two it won.

**Conqueror arm** — the configuration the collapse was found in. `leaders._DANGER_BELOW` is
50.0; the old policy fell to **40.9 / 22.5 / 11.1** on the three seeds and the new one never
went below **50.9**. On seeds 7 and 101 it ends +27.5 and +46.4 ahead.

**Reported as mixed where it is mixed.** Ordinary seed 7 is a real regression (2.65 against
34.65): the old policy's reform landed at p = 0.435 and its +8.55 lifted Russia back over
`_STRAINED_BELOW` into settled play where revenue compounded; housing's +3.80 did not clear
the band. Coups were **zero in every arm of every run** — what the study shows is the *risk*
collapsing, not a coup averted, and the ledger's original coup was in a world with a
different `world_id` and different dice.

### The tape

`pt74-cold-war` replaces `pt73`. 12 turns, **97 calls, 0 failures** — worth stating beside
pt73's 106 calls and 9 `codex exited with code 1` faults on `tracked_agent`, since that count
is in `INDEX.json` precisely so the next record has something to be measured against. It was
a transient CLI fault and this run did not see it. Rewind to turn 10 matches both ways,
nothing suspicious, no new wars, no ground changed hands. pt73 is retired to `historical`
with the reason written into its entry.

A re-record was unavoidable: the recovery policy changes what unattended governments build,
so the world diverges at turn 1 and the briefings at turn 2. pt73 replayed against the fixed
tree read `turn 1 sealed MISMATCH` and then a `tracked_agent` MISS — the tape asking a
question the engine no longer poses, which is what an active tape is for.

---

## 2. Staged for Alex — decided by nobody

* **`DECISION_RECOVERY_POLICY.md`** — the six-run before/after above, with the seed-7
  regression stated rather than buried, and why `abstain` shipped over `narrow` (they are
  indistinguishable for Russia on every seed; `abstain` is what the instruction described).
* **`DECISION_WORKS_OF_THE_AGE.md`** — the six criteria, on a fixture that now proves itself
  first. **1, 2, 5, 6 hold for China; 3 and 4 fail.** No bid was declared by anybody in 8.3
  in-world years, and **costs were not lowered to make anything pass**. The ungated arm
  reached the *same* two works with **0 programmes** against the builder's **21** — the
  prerequisites cost a decade and steer nothing. Poland: **112 tech-blocks, 0 programmes, 0
  works** — the mid tier closes at the *research* gate, before a work is orderable.
* **`DECISION_DOMINATION_EXPERIMENTS.md` §(c), rewritten** — the conversion rerun as three
  numbers. Poland read-only: **15 opportunities · 0/15 valid packages · brokered 15/15**.
  Committing arm: **3 opportunities · 0/3 valid packages · 2 ACTUAL SIGNED CESSIONS**.
  Column 2 was zero and column 3 was not, which is the demonstration that an empty
  `suggested_terms` means *no acceptable package found by this cession-only search* and never
  *no deal exists*.
* **`DECISION_STRAINED_AND_BROKE.md`** — see §3.

---

## 3. PARKED, deliberately — not blocked, just not tonight

### (a) The strained-and-too-poor defect — **diagnosed, four options measured, none picked**

Complete and committed as a document; nothing further is needed to *decide* it, only the
decision. Five arms × three seeds × 60 turns, one world id per seed.

The framing needed correcting: the trap is real, but **the old policy left 12 governments
permanently stuck against the shipped policy's 3** — it simply had them spending money while
stuck, which reads as activity and was not recovery. Most stuck runs end (treasuries climb
+0.3 to +2.2 a month; 31 of 34 escape). The permanent cases are **micro-states**, for whom it
costs nothing (Grenada: 51 months stuck, stability flat at 52), and **countries at war**,
which is a war economy wearing a build-menu costume — and the worst such case in the study is
in the *old* arm.

Four options, each with what it buys and costs, in the document: leave it; spend the buffer
under strain (best aggregates, but double the permanent traps and the only *falling*-treasury
pathology); a cheap relief work (least time under strain, −2% world development, does not
touch the permanent cases); or a stabilisation dividend (unmeasured, with its sizing problem
stated).

### (b) Discoverability — the matrix is clean; the guard is merged; one item staged

The audit's list was four merges stale. Re-measured: the research findings that were **0/24**
are now **24/24**, and the channel matrix has nothing dark left — what remains is correctly
conditional (`mediation`, `grant_independence`) or the one deliberate silence Alex holds
(`declare_war`). So there is **no next batch of safe surfacing fixes**.

The guard that keeps it that way is **merged**, but see §4 — it is on the wrong branch.
The one candidate not taken: `task_block_examples_a_programme` is 0/24 (the task block says
`{"project": "<programme id>"}` while public works get named examples). Naming a concrete
programme steers which one agents pick, so it is a judgment call, not a visibility fix.

### (c) Hardening / bug-scout pass — **not started.**

---

## 4. Two things the next session must know

### The working tree is shared, and it moved under me

A separate session is doing demo-critical UI-performance work in this same directory. The
reflog is unambiguous: `checkout: moving from master to ui-perf`, two commits, then
`checkout: moving from ui-perf to discoverability-batch2`.

**Consequence:** when I ran `git checkout -b discoverability-batch2`, HEAD was on `ui-perf`
at `4fcc0ea`, **not on master**. So despite the standing "branch off master" rule, that
branch was cut from their work. My structural-guard commit **`4f2ec9a` is correct and intact
but sits on the wrong base**, sandwiched between their commits, with more of theirs on top.

**It is not on master and it is not lost.** Nothing was rewritten. To land it, cherry-pick
`4f2ec9a` onto a fresh branch off master once the UI work has settled — do not rebase
`discoverability-batch2`, which another process is building on.

`1a3880e` was committed from a **separate `git worktree`** for exactly this reason: the
shared working tree was never touched. If a stale worktree is registered, `git worktree
prune` clears it.

### The fingerprints miss, and what it teaches

`7d8c1fb` added `pt74-cold-war.jsonl` by explicit path and **missed the
`.fingerprints.json` beside it**. The audit passed 8/8 anyway because the file was in my
working tree — **a fresh clone would have failed the cassettes gate on its first run.**
Reproduced in a clean worktree: without it `tape_replay.py` dies on `FileNotFoundError`.
Fixed in `1a3880e`. A cassette is two files; add the directory, not the tape.

---

## 5. Where things live

* `scratchpad/recovery_policy_measure.py` — old / abstain / narrow arms, ordinary and
  conqueror, with the odds recorded off `uncertain` and filtered to one nation's own
  `construction` ids.
* `scratchpad/broke_probe.py` — the five-arm trap probe, with climb rate and months-to-afford
  beside every stuck run so "rising" is never mistaken for "will get there".
* `scratchpad/works_of_the_age.py` — the builder prototype, `--control` for the positive
  control that must pass before any criterion is measured.
* `scratchpad/frozen_measure.py` — brokered cessions, mediation probed independently of the
  bilateral suggestion.
* `tests/test_no_channel_is_offered_nowhere.py` — the structural guard (on
  `discoverability-batch2`, see §4).
