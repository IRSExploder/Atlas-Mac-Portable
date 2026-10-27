# Playing ATLAS with no model behind it

Track-A work (menus, packaging, endings, layout) has to be testable without spending a model
call or touching a GPU. Three switches do that, and they combine.

| Switch | What it gives you |
|---|---|
| `--mock` | Every role answers instantly with an empty, schema-valid default. The world-sim narrative is blank, the council says one canned line, diplomacy replies "We will consider your proposal." Free-text orders do nothing ("Nothing actionable in that"). **Map-menu orders work** — they skip the parser. |
| `--replay TAPE` | Recorded model answers from a cassette (`scratchpad/cassettes/*.jsonl`) instead. Implies no real calls. See the modes below. |
| `--bots SCOPES` | Deterministic controllers answer for tracked neutrals (`tracked`), the covert channel (`gambit`) and the Security Council (`organisation`) — or `all`. Same gates as a model's answer. This is what makes a mock world *move*: wars, mobilisation, peace offers, construction, council votes. Equivalent to `ATLAS_BOTS=...` but set at the command line. |

All three work on both window commands, `atlas new` and `atlas play`.

## Replay modes

- `--replay-mode text` (**default**): prose only. World-sim narrative and event text, council
  answers and diplomacy replies come from the tape, cycling when it runs out. Diffs, contests,
  declarations and every reference to a nation or event of the recorded world are stripped, so
  **any tape narrates any world** — a 1972 tape will happily narrate 1936, about the wrong war.
  That is the point: it is layout copy, not mechanics. Every other role gets the mock default.
- `--replay-mode loose`: whole recorded answers, matched by role in recorded order. For replaying
  against the world the tape was recorded in, or deliberately feeding the gates mismatched
  answers.
- `--replay-mode exact`: exact prompt-digest match, as the regression harness uses. Only useful
  against the exact world that recorded the tape.

In every mode a call the tape cannot answer falls through to the mock default — it never aborts
the turn, unlike the headless regression replay where a miss is the finding.

Which tapes have what: the cold-war tapes (`pt36`–`pt79`) carry world-sim, tracked-agent, gambit
and organisation answers but no diplomacy or council. `pt34*-modern-day` and
`pt34c-sandbox` also carry real diplomacy replies. None carries council (advisor) answers yet.

## The dev profile

```powershell
# Real narrative text, a moving world, zero model calls:
atlas play --world-id earth --replay scratchpad\cassettes\pt34-modern-day.jsonl --bots all

# Fresh world from the setup screen, same profile:
atlas new --replay scratchpad\cassettes\pt79-cold-war.jsonl --bots all

# Slow-turn / failure testing stays on the scripted MockProvider (delay_s, failure_kind) in tests.
```

On this machine, keep dev runs off the P-cores while a game is running:

```powershell
$p = Get-Process -Id $PID; $p.ProcessorAffinity = 0xFFF0000; $p.PriorityClass = 'BelowNormal'
atlas play ...   # children inherit the mask
```

## What still needs the real model (track C)

Free-text orders (the parser), real diplomacy *negotiation* outcomes, and anything whose layout
must match the shipping model's own prose length and tone. Tape text is frontier-model text; do
not put it in marketing (DEV_TRACKS C10).
