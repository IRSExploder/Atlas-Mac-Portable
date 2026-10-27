# ATLAS

An AI-driven alternate-history grand strategy sandbox. Pick a scenario and a nation, issue
free-text orders, negotiate with AI-controlled powers in chat, then jump time forward — the
world simulates, the map redraws, and history diverges.

Local, single-user, and driven entirely by CLI subprocesses under existing flat-rate
subscriptions. **No API keys, ever.**

## The idea

Each role goes to the provider that suits it, and none of them talk to each other:

| Role | Provider | Why |
|---|---|---|
| World simulation | Antigravity (Gemini) | Long-horizon reasoning, largest practical context |
| Per-country agents | Codex | Structured proposals, reliably — see Focus Mode below |
| Diplomacy | Claude | Holds a persona under pressure; resists flattery |
| Your council | Claude | Reading a position and holding one are the same skill |
| Order parsing | Codex | Bounded, structured, cheap — and a real read-only sandbox |

**The important powers each get their own agent.** This is the part that makes the world
feel inhabited rather than narrated. Up to five nations are promoted into Focus Mode and
each gets its *own* call every turn, with its own goals, its own briefing built from what
that nation alone can see, and the right to propose anything its role permits. Everyone
else is moved collectively by the single world-simulation call. So Germany decides to
remilitarise the Rhineland because Germany decided it — not because one narrator decided
it on Germany's behalf.

That extends to its army. A tracked power is shown where its fronts are — the provinces
either side of each border with an enemy, and how far along any siege on them has got —
and declares **military objectives**: ground to push into, ground to hold, ground to give
up. Intent, not a write path: no territory column is reachable by any model, the engine
refuses objectives over ground a power cannot reach, and outcomes are still drawn from
the seeded stream. Two tracked powers can want the same border in opposite directions and
each is asked separately, so the war between them is two decisions rather than one
narrator's summary of both.

**Borders move province by province.** A great power is ten or twelve regions rather than
one, so a war is a campaign with a front in it: pressure accumulates on contested ground
turn by turn, at a pace set mostly by how strong the defender is, and control changes only
when it crosses the threshold. France overruns Germany in about fifteen monthly turns, and
you can watch which province falls next. A province that falls does not leave its garrison
standing under occupation: they withdraw, or they are captured with the ground. Zoomed out
the map dissolves each power's quiet ground into a country and leaves the fighting drawn as
places, so a continent stays readable while a front stays visible.

**You can build instead of fight, and it takes years.** Housing, rail, public health,
schools, land reform, steelworks, shipyards — ordered, paid for, and sat in a queue for
four to nine turns before they deliver. The same finite queue a battleship waits in, so
choosing between them is a real decision, and its size is your industry, which is itself
something you spend years building. Research runs in the same queue and changes what your
yards can do, which is how a country out-builds the one history gave it. Land reform can
go wrong.

**Some programmes nobody else can see.** A secret project writes secret events, which the
visibility system has always hidden and which rule INFO-002 has always stopped leaking
through their consequences. Espionage buys you sight of one event that already happened.

**And you can order things the engine has no rule for.** An order matching no mechanic
goes to a model that narrates what your country made of it and may move how people feel
about your government — within hard caps, reaching three columns and no others. Authorise
the citizens to fight the stray cats and you will get militias in three towns, a general
staff asking in writing whether this is a joke, and a point and a half off your standing.
You will not get territory.

**There is water, and there are fleets on it.** The named seas are ordinary regions nobody
owns, so sea control is the same occupation a province gets and a fleet action is the same
seeded contest. The 1936 fleets are the ones that existed — fifteen British capital ships
against Germany's three. An army crosses water it does not own by amphibious assault:
one sea, no enemy controlling it or holding the stronger fleet in it, and transports to
carry them, which is what finally makes islands and exclaves winnable. Ships are built over
time in yards whose number follows your economy, so a navy is a decision you make a year
before you need it.

**State is the message bus.** Every call is stateless: it receives a role-specific rendered
view of world state and returns structured diffs, which the engine validates and writes
back to SQLite. No model remembers anything between calls. The database remembers
everything — which is why turn 200 is reachable and why the world cannot quietly drift out
of sync with what a model happens to still have in context.

The engine, not the model, decides contested outcomes. Models emit enumerated evidence
flags; Python computes the odds from real state and draws from a seeded stream. Same seed
plus same inputs always gives the same battle, which is what makes rewind trustworthy.

## Setup

**On macOS, or without the CLIs:** run `./setup_mac.sh`, then `atlas start --api` (Claude API
plus rule-based bots) or `atlas start --no-ai` (bots only). See `docs/PLAY_MODES.md`.

```bash
py -m venv %LOCALAPPDATA%\atlas-venv
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m pip install -e ".[dev,bake]"
```

There is no bare `python` on this machine — use `py` or the absolute venv path.

Check what is installed and how well contained it is:

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli doctor
```

Bake the map once (downloads public-domain Natural Earth data):

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli map bake --source admin0-50m
```

And once more for provinces, which is what makes the 1936 powers fight province by
province. It is a heavier bake — ~4,600 shapes, 25 MB — and optional: a scenario opened
without it plays at country granularity exactly as it did before.

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli map bake --source admin1-10m
```

And once for the water, which is what makes a navy possible. Small and fast — 113 named
seas — and also optional: without it the world is all land, as it was before there were
ships.

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli map bake --source marine-10m
```

Then start a game. `new` opens the scenario and power picker and creates the world for you:

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli new
```

To seed a world from the command line and open it directly instead:

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli map world --player "admin0-50m:FRA"
```

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m atlas.cli play --nation n_fra
```

Add `--mock` to `new` or `play` to run the whole game with no real model calls.

The interface has a light theme as well as a dark one. The sun/moon button at the top
right switches between them at runtime, map included — there is no second stylesheet and
no restart.

## Your council

Top of the right-hand rail, above the powers you negotiate with, is **Consult your
council** — a private conversation with your own government about the position you are
in. It reads the same briefing you do: your exact numbers, your standing commitments,
rivals in bands rather than exact figures, and the list of what you can actually order
this turn. Ask it whether to mobilise and it will tell you what it would do and what that
costs, argue with you when it disagrees, and say it does not know rather than inventing a
rival's order of battle.

It is deliberately the one model-facing role that cannot change anything. There is no
"conclude" step, no commitment is written, and its write allowlist is empty — so advice
is free to ask for and only orders *you* issue will move the world. It sits outside the
Diplomacy list for exactly that reason: talking to your ministers is not negotiation.

## Commands

| Command | What it does |
|---|---|
| `new` | Pick a scenario and a power, then play |
| `play` | The game: map, events, orders, diplomacy, time |
| `doctor` | Installed CLIs, versions, containment, model pins |
| `probe` | One small real call per provider, end to end |
| `map bake` / `map verify` | Build and check the geometry pack |
| `map world` | Seed a world from a baked pack |
| `eve` | Engine-vs-Engine: three providers, three blocs |
| `stats` | Behavioural statistics for a world |
| `rewind <turn>` | Put a world back exactly as it stood |
| `export <path>` | Single-file copy, safe to sync |
| `presets` | List or inspect scenarios |
| `rules` | The validation rules the models are held to |
| `newgame` | Exercise the diff pipeline end to end |

## How cheating is prevented

Five mechanisms, all engine-side, none of them a prompt instruction:

1. **Numeric hidden state gates outcomes.** Alliances need the relation score to clear a
   threshold; war needs a casus belli or costs legitimacy.
2. **Every diff must cite a cause.** A change with no traceable `reason_event_id` is
   rejected — and *recorded* as rejected, so the attempt stays on the record.
3. **A per-role write matrix.** The columns that constitute an outcome are engine-only, so
   conquest-by-small-increments is impossible, not merely slow. Territory, unit strength,
   accumulated siege pressure and declared objectives are all on that list — a power says
   what it is *trying* to do through a channel the engine checks against the map, and
   never writes the result.
4. **Seeded RNG resolves contests.** The model narrates; it never decides.
5. **Information asymmetry is structural.** A nation's briefing is *built* without other
   nations' secrets, never told to ignore them. Secrets are tainted values and the
   serializer raises rather than leaking.

Gating events must carry `origin='ENGINE'`, so a nation cannot mint its own casus belli on
one turn and cite it the next.

## Testing

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m pytest
```

888 tests, fully hermetic — no subprocesses, no network, no quota. A 25-turn soak game runs
against a scripted provider, and a France–Germany campaign is fought across the real
province map to check that overrunning a comparable power still takes months rather than
years. The handful of tests that need the province pack skip cleanly without it.

```bash
%LOCALAPPDATA%\atlas-venv\Scripts\python.exe -m pytest -m live
```

Opt-in tests that hit the real CLIs, to catch flag drift. These spend quota.

## Things worth knowing

- **agy ignores the subprocess working directory** and runs in its own scratch folder, so
  per-call temp-dir isolation does not contain it. `--add-dir` is the real boundary, and
  Diagnostics says so rather than implying a guarantee that does not exist.
- **agy attests nothing** about which model served a call. Claude and Codex do. EVE can be
  told to refuse an unattested bloc.
- **Codex needs strict JSON Schema** (every property in `required`); Claude and agy accept
  ordinary schemas. One schema is written to the strictest dialect and used everywhere.
- **Migrations are additive only, and that is enforced.** There is no general migration
  framework: `CREATE TABLE`, `ADD COLUMN` and backfills are allowed, and `DROP`, `DELETE`,
  `RENAME` and a `NOT NULL` column with no default are refused before they run. A change
  that needs any of those invalidates saved worlds instead, and `newgame` is cheap by
  design. Forward only — opening a world written by a newer ATLAS is an error, not a
  rollback.
- Mutable state lives in `%LOCALAPPDATA%\Atlas`, never in this folder — SQLite in a synced
  tree corrupts through its `-wal` sidecars. `export` writes a quiescent single file that
  *is* safe to sync.
