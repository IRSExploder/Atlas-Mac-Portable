# ATLAS — review handoff: the Cold War era (1972)

**For:** a second reviewer (Codex), reviewing the third playable era.
**Prepared:** 2026-08-09, revised three times: after Codex's first review, after the
nuclear-engine batch, and after Codex's deep verification pass. **HEAD:** `64b6601` on
`master`. **Tests:** 2347 passing, 1 deselected (`live`), ruff clean.

> **⚠ Schema 19 and 20 both invalidate existing saves.** They are the only two migrations
> in the project that do, both were authorised deliberately, and a world written before
> them cannot be opened after them. 19 widened `arsenal.delivery` for the `sea` tier; 20
> gave `commitment.breached_by_nation_id` the foreign key `ALTER TABLE ADD COLUMN` could
> not attach. See §5h and §5i.

> ## ⚠ Read this before running anything
>
> **Every number in this document assumes the province pack is baked.** The Cold War era is
> drawn at `admin1-10m` granularity, and without that pack most of its coverage does not run
> at all — Codex measured **2124 passed / 142 skipped** on a tree without it, against 2310
> passed here. Specifically, without `admin1-10m.pack` (and `marine-10m.pack`):
>
> * **38 of the 45 Cold War tests skip**, including every fingerprint, promotion and
>   flashpoint assertion — they are `pytest.skip("geometry pack not baked")`, not failures;
> * **`scratchpad/seed_fingerprint.py` skips all three eras**, so none of the digests in §6
>   can be reproduced;
> * **PT36 replay exits `province pack not baked`.**
>
> **All three packs are needed**, not the two this box first listed: the country
> pack draws every nation the province files do not, and a partial install makes
> tests fail on a country that is simply absent rather than skip.
>
> ```bash
> atlas map bake --source admin0-50m
> atlas map bake --source admin1-10m
> atlas map bake --source marine-10m
> ```
>
> A green run without the pack is not evidence that this era works; it is evidence that it
> was not tested. That is a real weakness of the era's test design and it is Codex's finding,
> not mine.

> **Codex reviewed this document against the repo, the tests, the cassette and the
> historical record, and found six things wrong. All six are fixed; the corrections are
> marked ✅ below and the factual errors in this document have been corrected in place.**

The first two handoffs cover everything before this:
[`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md) (through `b73bf54`) and
[`REVIEW_HANDOFF_PHASE2.md`](REVIEW_HANDOFF_PHASE2.md) (the consolidation phase). Read them
for the background they establish; read this one for the era.

The purpose is the same as theirs: **help a reviewer find what we got wrong.** Where I am
uncertain I say so, and §8 is the part I would read first if I were you.

---

## 0. The commits

| Commit | What |
|---|---|
| `eb87781` | **Sketch first.** The anchor year argued, and both departures declared, before any code. |
| `b35efde` | **The era gate**: `era_1936: bool` → an `Era` enum, plus the three 1972 tables. |
| `256993e` | **The scenario**: `cold_war.py`, `territory/cold-war.json`, the preset, the registry entry, a three-line generator fix. |
| `efe53cf` | **Tests**: `test_cold_war.py` (33) and `test_a_bipolar_world_plays.py` (9). |
| `f0e394d` | **Docs**: the design note answered, `CHANGELOG.md`, overview Part VI, CLI help. |
| `c1af793` | **The air-across-oceans fix** — found by the live playtest, an engine bug in every era. |
| `396c7ea` | ✅ **The 1972 sovereignty map** — 229 nations down to 147. Codex #1. |
| `958a64b` | ✅ **Colombo, not Andreotti.** Codex #2. |
| `fab2e5c` | ✅ **Three treaties `pending`, not `active`.** Codex #3. |
| `65de203` | ✅ **Britain `missile`, not `triad`**, plus two engine issues flagged. Codex #4. |
| `565e8a5` | ✅ **The harness's acceptance semantics.** Codex #5. |
| `f7e8aaa` | ✅ **This document's own factual errors**, and the pack dependency. Codex #6. |
| `6080452` | ✅ **The SSBN/SSN split** — an attack boat is no longer a deterrent. |
| `e20cddf` | ✅ **A `sea` delivery tier** — schema 19, and it invalidates saves. |
| `ea12b52` | ✅ **Delivery range** — a bomber can no longer strike the other hemisphere. |
| `c646c22` | ✅ **Nuclear failure atomicity** — a refused strike is retracted, not reported. |
| `518e5fc` | ✅ **Boatless deterrent** collapses instead of keeping its tier. |
| `3b0dc0b` | ✅ **Morale floored** — a warhead cannot make a unit fight for the enemy. |
| `4cf2abf` | ✅ **Schema parity**, and the check that compares contracts rather than labels. |
| `24ef42d` | ✅ **Dependencies are not homeland** — Hong Kong is no longer nuclear-protected. |
| `111e82d` | ✅ **Schema 20**: the foreign key `ADD COLUMN` could not attach. |
| `df9bea2` | ✅ **Full bloc membership** — NATO 15, Warsaw Pact 7. |
| `9469e15` | ✅ **Bake verifies**, plus the agy tab-parse and three doc fixes. |
| `64b6601` | ✅ **Council of fifteen**, and `organisation.kind` is finally read. |

Everything before `c1af793` is additive data plus one shared-machinery refactor.
`c1af793` is the only change to combat behaviour, and it is not Cold War specific.

---

## 1. What was built, in the order it matters

### 1a. Three artefacts, the established pattern

* **`src/atlas/scenarios/presets.py`** — a `cold-war` `Preset`: id, name, `start_date`
  `1972-01-01`, lore, and five `simulation_rules`. **MAD lives in `simulation_rules`, not
  in `lore_text`**, because that is the field injected as a prohibition rather than a
  preference — first use is the taboo the whole period is organised around.
* **`src/atlas/scenarios/territory/cold-war.json`** — ten modern country codes partitioned
  into twelve nations, **106 promoted regions** (this document first said 119; Codex counted
  it and was right).
* **`src/atlas/scenarios/cold_war.py`** — pure data: twenty powers, the 1972 sovereignty
  map, ship classes, fleets, arsenals, one organisation, relations, treaties, history, unit
  templates, a research catalogue. *(A line count was quoted here and was wrong by eleven;
  it has been removed rather than corrected, because it goes stale on every edit.)*
* **`src/atlas/scenarios/registry.py`** — one line. Several existing suites parametrize
  over `SCENARIOS` and picked the era up without being told to, which is the point of it.

### 1b. The era gate: a boolean became an enum

**This is the one change to shared machinery and the thing to review hardest after the air
fix.** `era_1936 = scenario.start_date < "1950"` selected three tables that are keyed by
*nation* rather than by scenario, so they cannot live on the scenario object: who governs a
country (`leaders.py`), what it digs out of the ground (`resources.py`), how many people
live in it (`demography.py`).

A boolean names two eras. A 1972 world falling through to the modern branch would have been
governed by the politicians of the 2020s, and `sov` is in no modern table at all — the
Soviet Union would have taken `DEFAULT_POPULATION = 3.0`, the figure meant for an unnamed
minor, and been handed an army smaller than Belgium's.

So: `atlas/scenarios/era.py` carries `Era(INTERWAR | COLD_WAR | MODERN)`, `Scenario.era` is
a **required** field, and both shipped eras state theirs explicitly. New tables:
`LEADERS_1972`, `ENDOWMENT_1972`, `POPULATION_1972`.

**Stated, never inferred.** The boundary between eras is a judgement, not a fact about a
number: 1946 and 1989 are both "after 1950" and neither belongs in the same table as 2026.

`rescale_minor_economies` lost a parameter it never read.

**What to check:** that the branch each shipped era takes is the one it took before. I
claim byte-identical and prove it with fingerprints (§7), not by assertion.

### 1c. Era units, ships, tech

Per the era-gate discipline, 1972 supplies its own. Ships: `carrier`, `cruiser`,
`submarine`, `transport` — the battleship is thirty years gone and the missile is the
argument. Two names are load-bearing rather than flavour: `naval.sea_lift` finds amphibious
shipping by the name `transport`, and `nuclear.survivability` counts boats at sea by the
name `submarine`, so a Soviet second strike genuinely weakens when its boats are sunk.

Seven programmes, every effect on a lever shipped code already reads. Two of them —
`sam_network` and `mirv` — buy nothing at completion and everything at resolution time, via
new keys in `air.INTERCEPT_TECHS` / `air.PENETRATION_TECHS`. Those are plain dicts keyed by
technology id, not CHECK enums; `test_research_eras.py` already treats a runtime tech with
no `TechEffect` as legitimate and checks the closure in both directions.

---

## 2. The design decisions, and why

### 2a. The anchor year: 1 January 1972

The brief offered ~1947-50 or 1962. I argued a third answer, and **if this is wrong most of
the rest is wrong**, so it is the first thing to attack.

* **Not 1947-50** — there is no deterrence to model. American monopoly to 1949; the Soviet
  arsenal in 1950 is about five devices with no way to deliver them against North America.
  The nuclear layer's subject is *what survives a first strike*, and there is no second
  party for it to be about. NATO does not exist until 1949, the Warsaw Pact until 1955, so
  neither bloc can be seeded as a commitment at all.
* **Not 1962** — roughly 5,000 American strategic warheads against perhaps 300 Soviet is
  American *superiority*, not mutual assured destruction, and seeding it as MAD would be
  seeding a lie. And the China seat on the Security Council is held from Taipei until
  October 1971, so a 1962 P5 must either give a veto to a power not on the map as a great
  power, or hand it to Beijing nine years early.
* **1972** — both superpowers hold a full triad at rough parity and SALT I / the ABM Treaty
  are signed in May, so mutual vulnerability is a treaty obligation rather than a
  description. The P5 is exactly the historical five *and* exactly the five **declared**
  nuclear powers — the NPT's weapon states — for the first year ever. **The scenario also
  seeds Israel, undeclared, so six powers hold warheads and five hold vetoes**; an earlier
  draft of this document said "exactly the five nuclear powers" without that qualifier and
  contradicted its own arsenal table. Both blocs at full extent. Every named flashpoint live
  at once. And Gorshkov's navy exists, so a sea contest has two sides.

It also keeps the discipline both shipped eras hold to — **a starting position, not a
script.** Beijing in February, SALT in May, the Easter Offensive in March, Yom Kippur
twenty-one months out: all visible from turn one, none of them made to happen by anything
in the engine.

### 2b. Twenty powers

Two superpowers; NATO's `gbr fra wger ita tur`; the Warsaw Pact's `eger pol cze rou`; `chn`
as a third nuclear pole; `jpn`; and the flashpoint pairs `dvn`/`rvn`, `prk`/`kor`,
`isr`/`egy`, `cub`.

**Both Germanys and both Vietnams are powers rather than minors**, for the reason Ukraine
is one in Modern Day: a proxy war fought by a one-division generic minor resolves in a
single contest and teaches nothing. Romania is in because a Warsaw Pact with a member that
refuses orders is a different object from one without. India and Pakistan are out — the
1971 war is three weeks past and it is a genuine loss, but South Asia is a sideshow to a
bipolar showcase and twenty is already above both shipped eras.

`economy` is **mobilisation capacity, not product**, exactly as Modern Day states it.

### 2c. Blocs are commitments; the Council is an organisation

Modern Day's decision, restated because this era needed both halves. **A bloc is a
`defensive_pact` commitment and not a convening body.** Seeding NATO twice would put two
mechanisms on one treaty: an attack on a member would hand every other member a casus belli
*and* convene a body whose strongest action is to hand every member a casus belli — the
same event counted twice, the second pass adding nothing.

So NATO and the Warsaw Pact are `defensive_pact` rows the shipped Article 5 reads, and the
Security Council is the one `universal` organisation, because it has what a pact does not:
a veto, and jurisdiction over powers who are not its friends.

**Membership is who was actually in the UN in 1972** — neither Germany until September
1973, neither Korea nor either Vietnam until later. That is free accuracy and it makes the
Council's reach into three of the four flashpoints indirect, which is what it was.

What falls out with no code: **the Council is structurally unable to act against either
superpower**, because each vetoes a resolution against itself.

### 2d. Arsenals

Scaled as Modern Day scales them, ~1 per 50 real warheads. `usa` 46 triad, `sov` 38 triad,
`gbr` 4 **triad** (submarine-only is maximally survivable, which is what that column asks),
`fra` 3 missile (the first boat was three weeks old on this date), `chn` 2 **aircraft** —
armed and *not* survivable, which is the whole reason Beijing spent 1969-72 looking for a
third position. `isr` 1, undeclared.

1972's superpower arsenals come out **larger** than 2026's. That is correct: the peak is
1986, and a player moving between eras should feel it.

### 2e. Province-promoted flashpoints

Only the ground a campaign crosses. Ten country codes: both Germanys (five eastern Länder
overridden to `eger`), Poland, Czechoslovakia, both Koreas, both Vietnams split at the 17th
parallel, Cuba, and Egypt less the two Sinai governorates — which `isr` holds, because it
did on this date.

**The Soviet interior is deliberately country-level.** The campaign of this era crosses the
inner German border, not the Volga.

**West Berlin.** Natural Earth has one Berlin shape, so the city cannot be halved. The
scenario says so rather than inventing geometry and gives the shape to `m_wbe`, held as a
**colonial holding of the United States** through the shipped `colonies` path — occupied
ground garrisoned by the Western powers and constitutionally closed to the Bundeswehr. Its
only land neighbour is East Germany, so a move on it is an attack on ground the United
States owns and Article 5 fires through the mechanism rather than a special case.

### 2f. One generator change, and it is a bug fix

`scripts/gen_territory.py` resolved a capital against the *mechanical* owner of a
province's modern country, so it could not express two nations sharing one country with two
capitals — precisely what a divided Germany and a divided Vietnam are. Three lines: resolve
the effective owner first, check the capital against that. Plus an orphan guard so a nation
an override creates without a capital fails loudly instead of `KeyError`-ing.

**Both shipped territory files regenerate with every generated nation byte-identical.** The
only delta is the three hand-added 1936 nations a fresh output path cannot carry, which is
the writer's documented behaviour.

---

## 3. Judgement calls — flagged, not hidden

Each of these is a decision I made that a reviewer could reasonably reverse.

1. **Israel is seeded armed** (1 warhead, `aircraft`, undeclared). Modern Day already makes
   this call; this is the same one. A scenario that left it out would be asserting the
   opposite just as firmly.
2. **The Soviet Union is country-level**, so it holds fifteen large regions where West
   Germany holds ten small ones. Deliberate under the promotion rule, and it means a
   hypothetical NATO drive into Russia is coarse in a way the central front is not.
3. **The Golan and the West Bank are not modelled**, though Israel holds both on this date.
   An override can only move a province between powers the province map covers, and Syria
   and Palestine are country-level here. Sinai is modelled because Egypt is promoted.
4. **The West Berlin garrison's unit ids read `u_usa_germany_infN`.** The colony path takes
   its slug from the country-level name of the modern country the provinces belong to.
   Cosmetic; the alternative — slugging from the minor id — would move 1936's unit ids and
   with them its seed fingerprint, which is a much worse trade.
5. **`MINOR_ECONOMY` stays era-neutral.** It has no 1936 variant either, so 1972 inherits an
   approximation 1936 has lived with since it was written. Fixing it means re-authoring the
   table per era, which is a balance change to a shipped era wearing this one's clothes.
6. **The Sino-Soviet Treaty of Friendship (1950) is deliberately absent** from the
   commitments. It does not expire until 1980 and by 1972 is a dead letter with a million
   troops on the frontier it covers; seeding it would raise `treaty_status` to `alliance`
   between two powers that shot at each other three years ago. It is in the history
   instead, which is where a document nobody intends to honour belongs.
7. **The Four Power Agreement on Berlin is `military_access`**, not a pact. Reading it as
   one would put the United States and the Soviet Union in a non-aggression relationship
   and make the central antagonism of the era unfightable. Same kind, same reasoning, that
   1936 uses for Locarno.
8. **Which Vietnam history event carries `war_declared` is mechanical, not editorial.**
   `organisation.triggers_this_turn` names the *actor* of a turn-0 declaration as the power
   a body sits about, so filing the American withdrawal under that kind would have put the
   United States in front of the Security Council for a war it is leaving. The declaration
   is Hanoi's, and the reason is written at the entry.
9. **Two `pending` commitments were seeded by the PT36 harness, not by the scenario** —
   SALT and the *Grundlagenvertrag*. Both were genuinely under negotiation on this date, and
   they exist because with no player `rank_candidates` ranked both superpowers below Cuba
   on a tiebreak. **They are harness state, not shipped scenario content**, and if the
   Focus ranking is the right lever to reach for here is a fair question to put to me.

### One real bug the build found

**`svn` is Slovenia's country code.** South Vietnam as `svn` gave Slovenia Nguyễn Văn
Thiệu, nineteen million people and South Vietnamese rice, because all three era tables key
on the id with `m_` stripped. The Vietnams are now `dvn` and `rvn`. The guard is structural
and parametrized over every registered scenario: a power id may share the code of a country
it *holds* and may not share the code of one it does not. Found by building the world and
printing the leader table, not by reasoning — and the three per-era hygiene tests all passed
at the time, because none had been extended to the new era yet. They have been now.

---

## 4. What the live playtest actually showed (PT36)

Ten monthly turns, 1 Jan – 27 Oct 1972, **82 real model calls, 1 failure**, 825 s. Tracked
by Focus: `usa`, `sov`, `dvn`, `rvn`, `wger`. Seven turns sealed, three partial, none
aborted. In-run rewind to turn 8 matched; **the tape replays 10/10 byte-identical**.

* **MAD held, in the strongest form available.** Zero applied diffs against `arsenal` in
  ten turns. `nuclear_launch` was on the menu in **all 20** gambit calls, and the American
  briefing said in each: *"No power you are currently at war with holds warheads that would
  survive to answer."* The engine disclosed a **free** strike on North Vietnam, four times,
  and the model declined every time — twice preferring a conventional strike on the same
  province. Nobody launched, so **automatic second-strike never fired**: unproven live.
* **Proxy, not direct.** No new wars. The USSR's answer to an American strike on its client
  was to resupply Hanoi's air defences and press harder on SALT: *"Vietnam is not a theatre
  to be inherited; Soviet aid will continue, but no Soviet move will turn it into a direct
  war."* Its one aggressive act against the United States in ten months was a subversion
  gambit funding the anti-war movement.
* **Vietnam hot, Korea and Germany frozen.** Ten ground battles at Quang Tri (it changed
  hands repeatedly), 7 at Ha Tinh, 7 at Hue. **Zero battles and zero contests in Korea or
  Germany**, which is the correct 1972 answer. American ground troops ended the run
  occupying Ha Tinh, inside North Vietnam, and then declared a withdrawal from it.
* **The Council deadlocked itself.** One session, turn 1, off a real trigger. It did not sit
  and get vetoed — it read the veto arithmetic in its own briefing and declined to bring a
  resolution: *"with China and the Soviet Union pledged to veto anything against North
  Vietnam ... a vetoed resolution weakens this body rather than restraining Hanoi."*
  Authority 50 → 46; it never convened again.
* **Blocs cohered and were never tested.** `casus_belli` events after turn 0: **0**. No bloc
  member was ever attacked, so Article 5 never had occasion to fire. Membership shaped
  everything around it — `wger→eger` swung −55 → +6.2 on four back-channel gambits — but
  **collective defence is proven only by the mock test, not live.**
* **Détente is measurable:** `usa→sov` −70 → −39, `sov→usa` −68 → −22, `sov→chn` −80 → −40,
  `usa→chn` −20 → +5. Both superpowers sent envoys to Beijing in the same month, unprompted.

**Health.** codex 50/50 with attestation MATCH; claude 21/21 MATCH; agy 11 calls, 1
`empty_response` retried successfully, attestation UNVERIFIED as always. The breaker never
tripped and the Sonnet fallback was never used.

**The tape is at `scratchpad/cassettes/pt36-cold-war.jsonl`** (FORMAT 2, 82 entries) with
its fingerprints sidecar, replayable with `scratchpad/pt36_replay.py`. Both are
**untracked** — deliberately, per the brief for that run.

---

## 5. Known issues for you to scrutinise

### 5a. Air across oceans — ✅ **FIXED in `c1af793`, and it was never a Cold War problem**

PT36 found the United States holding the sky over four North Korean provinces at margin
0.96-0.97, **at peace, with all six of its air wings sitting in `r_usa`**. `air._within` was
a plain BFS over `region_adjacency` and an ocean is one region however wide it is.

Measured before the fix, from aircraft that had not moved: the **1936** United States
projected over 134 foreign land provinces, 2026's over 115, 1972's over 125.

The fix mirrors `logistics.supplied_at_front`: `_OCEAN_HOP = 2` (the same figure and reason
as `_SEA_COST`) and `_MAX_WATER_CROSSINGS = 1`, with the walk reshaped like `supply_map` and
its **state changed from `region` to `(region, crossings)`**. `naval.crossable` is
deliberately *not* consulted — that gates things moving *through* water, and an enemy fleet
does not stop an aeroplane.

**What to attack:** (a) is one crossing the right limit, or does it want to be two for a
long-ranged bomber? (b) The `deck` exemption keys on the unit's region *being water* rather
than on the hull's kind — a carrier alongside in harbour is a coastal airfield. I think that
is right; the first draft keyed on kind and the bug survived. (c) **1936 Britain now reaches
no continental ground at all** (41 foreign provinces before), because its two seeded wings
are inland in Greater London. I judged that a scenario-placement consequence rather than an
engine flaw — a reviewer could reasonably call it an over-correction. (d) A residual I did
*not* fix: a country-level power's single region touches every ocean its coastline touches,
so `r_usa` still reaches one crossing plus one hop at 37% strength. That is a granularity
artefact and the honest fix is promoting the United States to provinces.

### 5h. ✅ The three nuclear-engine issues — all fixed, and one of them costs a save

Flagged in the first pass, greenlit by Alex, and done. **Two departures from my own
recommendations, both deliberate:**

* **The SSBN/SSN split is not a template flag** (`6080452`). I recommended one and it
  cannot work: a flag on `unit_template` is per *era*, there is one `submarine` kind in
  1972, and the thing that differs is the *nation* — Britain's boats carry missiles and
  China's do not, out of the same template. The gate is the arsenal's own delivery tier
  (`SEA_LEG_TIERS`), which is the per-nation fact. No schema change; attack boats keep
  every naval property.
* **Delivery range was implemented, not left** (`ea12b52`). I recommended leaving it; Alex
  overruled that and was right. Only `aircraft` is limited — six hops from ground the
  launcher *holds*, so forward basing counts — because a ballistic missile in a silo or a
  boat genuinely is intercontinental and gating it would model something untrue.

**`sea`, at schema 19** (`e20cddf`), survival 0.65 / penetration 0.85 / deterrence 0.06,
between `missile` and `triad` on all three: a boat on patrol cannot be caught, and a force
that is *only* boats has no second basket when they are found.

**What to attack.** The `invalidates_saves` escape from the additive migration guard is the
riskiest thing here: it is narrow, it refuses a migration that gives no reason, and a test
pins the set allowed to carry it at exactly `{19}` — but it is still an escape from the
guard that stops a migration eating somebody's world, and a second one should be argued
for. Also worth checking: whether six hops is the right bomber radius when a hop is a
province on one map and a country on another, which is a coarseness the fix does not remove
and says so.

**Reclassified, deliberately, so two built-world digests moved:** cold-war `gbr` and Modern
Day `gbr` and `fra` are all `sea` now. France in *1972* stays `missile` and means it — the
Albion silos and the Mirage IVs were both in service, and the first boat was three weeks
old. Modern Day's `pak`, `isr` and `prk` lost their boat bonus to the SSBN gate, which is
the honest reading of Modern Day's own comment that those sea legs are "real and
undeclared".

### 5i. ✅ Codex's deep verification pass — ten findings, all closed, two flagged

Everything above the line in Alex's repair order is fixed and has a regression test that
fails without it. Three things a reviewer should look at hardest:

* **`invalidates_saves` is now used twice** (19 and 20). It is still narrow — the guard is
  skipped only for flagged migrations, a flagged migration with no reason is refused, and a
  test pins the set at exactly `{19, 20}` — but a third use should be argued for, not
  assumed.
* **The strengthened parity test found a bug nobody had reported**, which is the argument
  for it: comparing contracts rather than labels surfaced the `commitment` foreign-key gap
  the moment it could see constraints. `KNOWN_SCHEMA_GAPS` is now empty.
* **`home_nation_id` reseeding moved 1936's built world**, legitimately — it has Eritrea and
  Italian Somaliland. Modern Day has no dependencies and did not move. No seed digest moved.

**Two items are flagged rather than done**, both design calls with my recommendation in the
changelog: **boat-level SSBN capability** (unsayable without a second unit kind or a schema
change; I would use a per-nation `deterrent_boats` count, which cannot model hunting a
*particular* boat) and **finite missile range** (a sixth tier means a third save-invalidating
migration, and hops are the wrong unit for continental range).

### 5j. ✅ Boat-level SSBN capability — done, and the flagged design call resolved

`arsenal.deterrent_boats` (schema 21, additive and nullable). A count on the nation, not a
kind on the unit: there is one `submarine` kind per era, so nothing on a unit or its
template distinguishes Britain's boats from China's out of the same template in the same
year. `survivability` takes `min(afloat, declared)`, so losses eat the attack boats first.

**Still true, and still the honest way to be wrong:** an anti-submarine campaign that
deliberately hunted the missile boats cannot be modelled. What is modelled is that sinking a
navy eventually sinks the deterrent. NULL means "the scenario did not say" and counts every
boat, so 1936 and Modern Day are unchanged and no save is invalidated.

1972: usa 4/9, sov 5/12, gbr 2/3, fra 1/2, **chn 0/4**, isr 0/1.

### 5k. 🔍 The scenario auditor — and the two gaps it immediately found

`atlas audit <era>` (`src/atlas/testing/audit.py`, manifests in `testing/manifests/*.json`)
checks a built world against an **independently written** record of its year: alliance
membership, organisation membership and permanent seats, dependencies/cores/occupation,
leaders, treaty status against ratification dates, arsenals in both directions, required
geometry packs, and the six-hop delivery boundary walked on the world's own adjacency.

**The manifests are JSON and import nothing**, which is the whole point — an auditor reading
`SCENARIO.organisations` would have passed on the day the Council had fourteen members. All
three eras pass: 62 facts for 1972, 36 for 2026, 26 for 1936.

It found two things on its first run, both **declared rather than asserted** so the audit
passes on a defect it exists to record:

* ~~**1936 has no era sovereignty map.**~~ ✅ **Fixed.** Israel, Pakistan, Bangladesh,
  Zimbabwe, Namibia, Ghana, Indonesia, Malaysia, Singapore, Algeria, Libya, Burma, Zaire and
  a hundred and thirty more were sovereign states on the 1936 map. 1936 now has the largest
  sovereignty table of the three eras — **202 minors became 57** — and its manifest
  asserts the thirty anachronisms rather than declaring them out of scope. The 1936 seed
  digest moved legitimately; the Cold War's did not move at all.
* ~~**Modern Day models no occupation.**~~ ✅ **Fixed.** `Scenario.occupied` writes
  `controller_nation_id` over a built world: Crimea, Luhansk and Donetsk are Ukrainian ground
  under Russian control, with the core left Ukrainian so `deterrence` does not extend a
  Russian guarantee over ground Ukraine is retaking. Crimea used to be Russian *ground*,
  because Natural Earth files it under RUS and the generator took that mechanically.
  Zaporizhzhia and Kherson stay Ukrainian: Russia holds most of both by area and neither
  oblast capital, and one controller per region admits no half.

Both smaller findings are fixed too: **Ireland** is now `m_irl`, the Irish Free State, a
self-governing Dominion rather than British home soil with a British core; and
**Antarctica** is ungoverned in all three eras, which is the answer 1972 already had.

**Every finding the auditor raised is now closed.** What remains are four declared
silences across the three eras, and each is a fact rather than a gap: 1936 does not model
the League of Nations (a known omission, argued in the manifest), no era begins mid-
occupation except Modern Day, 1936 had no arsenals, and Modern Day seats no dependency and
has nothing that postdates it. Audit coverage is **74 facts for 1936, 62 for 1972, 43 for
2026**.

Also fixed in passing: `pyproject.toml`'s package-data glob never covered
`scenarios/territory/*.json`, so an installed wheel would have played every province-drawn
era at country granularity and said nothing.

### 5b. Two mechanisms this era exists to showcase are unproven against real models

**Collective defence** and **automatic second-strike**. Both have mock tests that fire them
through `jump()`; neither happened in PT36, because no bloc member was attacked and nobody
launched. A run that produced either would be worth more than another ten quiet turns.

### 5c. The nuclear disclosure is only in the gambit prompt

The **tracked-agent** briefing — the call that decides strategy — never mentions arsenals at
all. The taboo held anyway, but the staffs planning the war were reasoning about a nuclear
standoff they were never shown. Whether that is a gap or a mercy is a design question.

### 5d. A patron cannot declare an objective over its client's ground

West Germany declared `hold: [r_m_wbe_west_berlin]` on **eight of ten turns** and was
refused every time — correctly, because the United States owns it. The United States hit the
identical wall trying to hold Quang Tri and Hue for Saigon. The refusals are right and the
concept behind them is missing: there is no way to say *"I will defend your ground"*, so a
staff burns an objective slot every turn on the most obvious sentence a Cold War government
could utter and is never told why. **This is the sharpest design finding of the run.**

### 5e. A transient invariant violation, and the checker's first live catch

`nobody-besieges-their-own-ground` fired on turns 5-7: partisans retook Quang Tri for Saigon
while Saigon's own siege of it was still live, so `rvn` was besieging ground it now held. It
**decayed out by turn 8**, so it is a transient window rather than a stuck contest — but the
checker is right that such a contest can never *end*, and the partisan path clearly does not
clear contests over the province it flips. Small, real, unfixed.

### 5g. ✅ What Codex's review changed, and what it left open

Six findings, all fixed. Three are worth a second reviewer's attention because the fix
involved a judgement:

* **The sovereignty map** (`396c7ea`). The era shipped with 209 minors, including states
  that did not exist in 1972 and an Antarctic government, in violation of its own hard
  constraints. `Scenario.sovereignty` and `Scenario.absent` fix it — 147 nations. **A
  dependency is deliberately not garrisoned**: the first draft charged Britain thirty
  formations of upkeep for Pitcairn and the Cayman Islands and bankrupted it in eleven
  turns. Rhodesia is treated as a state despite being de jure a colony in rebellion.
* **Britain's tier** (`65de203`). `triad` → `missile`, so its three boats are what its
  survivability rests on rather than being irrelevant to it. **Two engine issues underneath
  are flagged and not fixed**, both Codex's: `nuclear.survivability` counts every submarine
  whatever it carries (China's four *attack* boats raise its deterrent, and the PRC had no
  SSBN until 1981), and delivery has no geographic range. My recommendation is in the
  changelog; both are Alex's call.
* **The eastern treaties** (`fab2e5c`). Moscow, Warsaw and the Four Power Agreement were
  `active` and were signed-not-in-force until 3 June 1972. Setting them `pending` exposed a
  second half the first pass missed: the *relation* rows still asserted `NON_AGGRESSION`,
  and `_reconcile_seeded_treaties` only ever raises, so the column disagreed with the
  paperwork. Both are now `NONE`.

**And the PT36 tape no longer replays against HEAD.** Not because of the acceptance fix —
because the sovereignty map removed 82 nations from every briefing, so the world simulator
is asked a different question and the digest misses, loudly, on turn 1. That is the cassette
doing exactly its job. The tape remains an accurate record of the run and the narrative in
§4 stands; it is no longer a regression fixture for this build.

### 5f. Smaller, all flagged rather than fixed

* **agy 1.1.11 changed `agy models` to `slug<TAB>Display Name`**, and
  `agy_adapter.parse_models` still reads the whole line — so `normalise_model` produces
  `gemini-3.6-flash-high-gemini-3.6-flash-high` and **every agy pin fails validation while
  working perfectly**. `atlas doctor` currently reports two false problems. One-line fix,
  not taken this session because it is unrelated to either task.
* The Soviet treasury ended PT36 at 0.1 — not structural (it opens at +1.0/turn) but spent
  flat on public works. Worth asking whether the AI investment path over-commits.
* `rank_candidates` with no player ranks on so few terms that both superpowers fell below
  Cuba on a tiebreak by nation id. That is a Focus-layer observation, not a scenario one.

---

## 6. Fingerprints

**The two shipped eras must not move, and they have not** — checked per section after every
commit that touched shared machinery, which is every commit in the era-gate piece.

| era | seed (no player) | built world |
|---|---|---|
| gathering-storm | `57af03900d347d615d241442f8b02e5a` | `30dd1d940ef98d6015b8bdbe0760e910` |
| **cold-war** | **`de872c31640a87daee6b7029440620fe`** | **`913b57ddffef344886d2670c53e3f6b3`** |
| modern-day | `bf7810a54fd7af5bcc9c020322c5d479` | **`ef457bef4f9314d48066485dcbda1537`** |

**Modern Day's *built world* moved for the first time**, because `gbr` and `fra` were
reclassified to the `sea` tier; its *seed* is unmoved, and 1936 is unmoved in both. The
Cold War world moved again for the same reason (`gbr` → `sea`). Arsenals are written by
`apply_politics` over a created world, which is why a reclassification moves one digest and
not the other.

**The Cold War pair also moved twice during Codex's review and both moves were intentional**: the
sovereignty map changed the seed (82 fewer nations), and the treaty and relation corrections
changed the built world. 1936 and Modern Day are unmoved throughout, measured after each
commit. **All of this requires the province pack** — see the warning at the top.

All twelve section digests for the shipped eras are unmoved.
`scratchpad/seed_fingerprint.py` builds each era **twice** and compares, so the new pair is
proved reproducible rather than written down.

**One asymmetry worth knowing:** `apply_politics` writes relations, treaties, arsenals,
organisations, leaders and history *after* `create_world`, so editing any of those moves the
**world** digest and leaves the **seed** digest still. Cold War's world digest moved once
during the build, when the Vietnam history entry changed; its seed digest did not. That is
the distinction working, not a bug.

The air fix moves **neither**: reach is a read, `projection` stores nothing, and the
`air_control` rows a turn writes still go through `apply_batch` as diffs.

---

## 7. Riskiest surfaces, ranked

1. **`engine/air.py::_within` and the two constants** (`c1af793`). The only combat-behaviour
   change in this work, it touches every era, and it is new. Specifically: the
   `(region, crossings)` state, the `deck` predicate, and whether one crossing is right.
   2256 tests passed unchanged, which means **no existing test covered this**, which is
   exactly why it survived so long.
2. **`scenarios/seeder.py`'s minor loop + `cold_war.SOVEREIGNTY`.** New, it decides who
   exists on the map, and it is the largest single table in the era. The mechanism is inert
   for a scenario that declares nothing and that is tested, but the *data* is sixty
   historical claims and a wrong one is silent. Check the seat rule (first code listed, not
   first walked) and the decision not to garrison dependencies.
3. **`scenarios/seeder.py` + `era.py` — the era gate.** A required field on a frozen
   dataclass and three table lookups rerouted. If the branch selection is wrong for a
   shipped era, that era's governments, oil and population all change at once. The
   fingerprints say it is not, and the fingerprints are the whole argument.
4. **`territory/cold-war.json` and the generator fix.** 119 promoted regions, 42 hand-written
   overrides, two divided countries. An override that matches nothing fails loudly, but an
   override that matches the *wrong* province is silent. The Vietnamese names carry two
   visually identical code points (U+0110 and U+00D0) and were generated rather than typed
   for exactly that reason.
5. **`cold_war.py`'s `TREATIES` and `ORGANISATIONS`.** These are what the bloc and Council
   showcases rest on. Check the `military_access` choice for the Four Power Agreement (§2c,
   §3.7) and the absent Sino-Soviet treaty (§3.6) — both are deliberate and both change what
   the engine can do.
6. **The arsenal table and the delivery tiers.** `gbr` as `triad` on four warheads and `chn`
   as `aircraft` on two are the two entries doing the most work, and both are judgements
   about what the `delivery` column is asking rather than facts about airframes.
7. **The colony path for West Berlin.** It reuses shipped machinery unchanged, but it is the
   first time a *promoted minor drawn from a covered country* has been used as a colonial
   holding, and it produces the cosmetic unit-id wart in §3.4.
8. **`POPULATION_1972` / `ENDOWMENT_1972` / `LEADERS_1972`.** Data, checked by the three
   hygiene tests — which now cover this era, and which did **not** at the moment the `svn`
   collision was introduced.
9. **Whether twenty powers is too many.** Both shipped eras have fewer. Twenty tracked-agent
   candidates and twenty sets of relations is more surface than either, and I have not
   measured the prompt-size or turn-cost consequence on a long game.

---

## 8. Where I am least confident

* **Whether 1972 is the right anchor.** Everything rests on it. The argument is in §2a and
  in the design note at `ENGINE_CHANGELOG.md`; if the answer is 1962 after all, the P5 and
  the arsenal table both change.
* **Whether the air fix over-corrects.** 1936 Britain reaching no continental ground is a
  real regression in the era where Britain matters most, and I chose to call it scenario
  placement. I would not bet heavily on that call.
* **Whether the blocs actually work under real models.** They cohered in PT36 and were never
  tested. A mock proves the mechanism; only a live run proves the models reach for it.
* **Whether the figures are balanced.** Calibrated against the budget the way both shipped
  eras were, and played through exactly ten turns. Four client states run deliberate
  deficits; the United States runs −0.01. None of that has seen a long war.
* **Whether the promotion set is right.** Ten countries, chosen by where I thought campaigns
  would be fought. PT36 fought exactly one of them. That is either good targeting or
  evidence that six of the ten are ground nobody will ever cross.
