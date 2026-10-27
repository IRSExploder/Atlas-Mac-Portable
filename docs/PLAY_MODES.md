# Play modes: API, bots, CLIs

ATLAS was built to drive three subscription CLIs (claude, codex, agy). Two more ways to play
need none of them. All three work with `atlas start`, `atlas new` and `atlas play`.

| Mode | Command | Who plays what | Cost |
|---|---|---|---|
| **API (hybrid)** | `atlas start --api` | Claude over the Anthropic API: the world simulation and turn narrative, the history digest, diplomacy (replies, and what talks agreed), your council, and orders no rule covers. Rule-based bots: parsing your typed orders, the minor powers, the covert channel, the Security Council. | Metered. Haiku 5.5 ≈ $0.11 per turn for the world simulation (list price), and a fraction of a cent per message in talks. |
| **No AI** | `atlas start --no-ai` | Rule-based bots in every seat. Free and offline, but the narrative is mechanical. | Free |
| **CLIs** (original) | `atlas start` | claude / codex / agy, pinned per role (see `atlas doctor`). | Your subscriptions |

## On a Mac

**The app.** Download `Atlas-macOS-arm64.dmg` from the rolling `atlas-mac` release and drag
Atlas to Applications. Python, Qt and the maps are inside; nothing else is installed. It is
not signed with a Developer ID, so the first launch needs System Settings > Privacy &
Security > Open Anyway. Opened with no key it plays with bots; with a key saved it starts
in API mode (reopen after saving the key). Saves go to `~/Library/Application Support/Atlas`.
Every push that touches the game rebuilds it: `.github/workflows/mac-app.yml` runs
`scripts/build_mac.py` on an Apple-silicon runner, smoke-tests the app and replaces the
release asset. Apple silicon only; an Intel Mac uses the source route below.

**From a checkout.** Double-click **`Atlas.command`**. The first run installs the game into `~/.atlas-venv` and
bakes the maps (a few minutes, needs internet); later runs open straight away, and a newer
build in the folder is installed automatically. It starts in API mode. **`Atlas (no
AI).command`** starts with bots only. If macOS says the file is from an unidentified
developer, right-click it, choose **Open**, then **Open** again (once).

## API mode

**The key:** open **Settings > AI providers**, paste it, press **Save key**, then **Test**. It
is stored in your Mac's Keychain (or the system keychain elsewhere; a private file only
where there is none) and takes effect on the next call, with no restart. **Remove** deletes
it.

Alternatively set `ATLAS_ANTHROPIC_API_KEY`, which wins over a saved key. Do not use
`ANTHROPIC_API_KEY`: ATLAS strips that variable from every CLI it launches, so a stray key
cannot switch a subscription CLI to metered billing, and `atlas doctor` warns when it sees
it. The separate name keeps that guard meaningful.

```bash
atlas probe --provider api                # one call per seat, each with its real schema
atlas start --api
```

`probe` sends four tiny requests, one of each shape the hybrid uses. It is the cheap way to
find a refused key, an empty account or a schema the API will not compile, before a turn
depends on it.

**Models and effort.** Every seat defaults to `claude-haiku-5-5`. Override all of them, or
one role:

| Variable | Example | Effect |
|---|---|---|
| `ATLAS_API_MODEL` | `claude-sonnet-5-5` | every API seat |
| `ATLAS_API_MODEL_WORLD_SIM` | `claude-sonnet-5-5` | only the world simulation (the most demanding seat) |
| `ATLAS_API_EFFORT_<ROLE>` | `ATLAS_API_EFFORT_DIPLOMACY=medium` | thinking effort: `low`, `medium`, `high`, `xhigh`, `max` |

Default effort: world simulation `high`, council `medium`, diplomacy, freeform and the
extraction/digest calls `low`.

**If turn reports feel thin**, move only the world simulation up a model:
`ATLAS_API_MODEL_WORLD_SIM=claude-sonnet-5-5` (≈ $0.43 per turn at list price). It produces
the large structured batch the rules then check, so it gains the most from a larger model.

**What can go wrong, and what you see:**

- *Key refused, no credit, rate limited, API overloaded*: the turn reports it in plain words
  and nothing is written. Press Advance again once it is fixed.
- *A safety refusal*: Haiku has no automatic fallback model, so a declined world simulation
  fails that turn. Pressing Advance again usually goes through.
- *Schema refused as too complex*: the call is retried once asking for bare JSON. Either
  way the answer goes through the same rule gates as any model's.

The model that served each call is checked against the one pinned for the role. If the world
simulator's circuit breaker ever asks for a different model, the call is still served by the
configured one and the routing log records a `MISMATCH`, so a fallback cannot quietly move the
game onto a more expensive model.

## Conquering a country

1. **Declare war and invade**: "Declare war on Iran and invade it". Your forces must border it.
2. **Take every province it holds**: "Capture Iran" (or "invade", "seize", "take"). When it
   holds no ground at all it capitulates and the war ends.
3. **Annex it**: "Capture Iran" again, or "Annex Iran". The land you occupy becomes yours;
   a country left with nothing ceases to exist. Expect the Security Council to object.

A country that still holds ground is taken from at the peace table instead ("sue for peace
with Iran, ceding ...").

## Reading the screen

By default the interface hides the engine's working: odds and before -> after numbers in
headlines, other powers' routine administration in the feed, debug map layers and the save
path. The turn summary leads with what happened to you, then your orders and whether each
worked. Land you have not scouted is drawn in its owner's colour, muted; armies there stay
hidden. **Settings > Show technical details** brings all of it back.

## No-AI mode: what the bots do

All of them write the same forms a model writes and go through the same gates.
`engine/bots.py` plays the scenery, `engine/staff_bots.py` everything you talk to.

- **Orders** are matched against the capability catalogue's own vocabulary, plus everyday
  verbs ("sound out", "ask", "spy on", "send a note"). Names resolve through the engine's
  resolver, including demonyms ("the Cubans", "the Soviet railways"). An order the parser
  cannot place is refused with what to write instead. Irregular forms ("the Chinese") are
  not recognised: name the country.
- **Diplomacy** answers from regard, war and relative strength. It agrees to a treaty only
  within the thresholds rule DIP-001 enforces (alliance 55, defensive pact 35, both ways), so
  a "yes" is never then refused. Peace and ceasefires go through the `sue for peace` order,
  which the engine judges on how the war is going.
- **The council** summarises your position and recommends orders, written so the parser
  accepts them as they stand.
- **The world simulation** narrates the span, and answers notes and treaty proposals sent as
  orders. Everything mechanical already runs on the engine.
- **Freeform orders** are recorded and narrated, and never move a number.

`--bots tracked,gambit,organisation` (or `--bots all`) still means the scenery bots only, so
the `--replay` dev profile in `DEV_OFFLINE.md` keeps its recorded narrative.
