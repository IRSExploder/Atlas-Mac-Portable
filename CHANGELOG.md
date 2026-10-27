# Changelog

What changed and why, at the level of a decision rather than a commit. The git log is the
full record; this is the shape of it.

## Dynamic borders

Three phases, taking a war from one dice roll to a campaign fought province by province,
with each important power deciding its own.

### Phase 1 — ground changes hands over turns, not on one roll

- **Pressure accumulates.** A contest is a standing push by one nation on one region,
  gaining ground turn by turn at a pace set mostly by the defender's army and terrain, and
  control changes only when it crosses a threshold. Several nations may press the same
  region at once: each accumulates independently and the first to arrive takes it, which is
  why this is a table rather than a pair of columns.
- **An unpressed front decays**, so an attacker cannot park a siege at 90% and come back
  twenty turns later.
- **Every column of it is engine-only.** A model that could write pressure could take
  ground by writing a number.
- **A forward-only, additive-only migration runner**, so a new column no longer costs the
  player the game they are in the middle of. Destructive statements are refused before they
  run.
- Rewind-safe: a campaign is diffed like any other state, and rewinding to turn N and
  replaying reproduces it exactly.

### Phase 2 — a power is places, not a place

- **Geometry is decoupled from regions.** A region references pack shapes by key and may be
  composed of several, so one region is no longer one pack entry. `PackSet` reads several
  packs behind one interface, addressed by the source in a geo key.
- **A territory file says which shapes compose which region**, generated from Natural Earth
  admin-1 and hand-corrected for borders that moved inside what is now one country: East
  Prussia, Silesia, the Polish Corridor, the Sudetenland, Alsace-Lorraine. Every power lands
  in a band of 7–13 regions, so no one is twenty times harder to conquer than a neighbour
  for reasons unrelated to its army.
- **The 1936 powers are promoted at world creation** when the province pack is present, and
  play at country granularity when it is not. A power can be provinces in Europe and one
  region per country elsewhere; region adjacency is derived from shape adjacency within each
  pack, so a promoted power still borders its country-level neighbours.
- **Minors are promoted too, not just powers.** A nation the territory file names that the
  power roster does not is drawn at province granularity by the same machinery — its province
  regions from the same groups, its defence spread across them by the same garrison path —
  while keeping a neutral minor's formulaic strength (promotion is geometry, not a stat buff).
  Ethiopia (a weak power, so promoted through the power path) and its neighbours Eritrea and
  Somalia are promoted, so the one starting war — the Abyssinian campaign — is fought province
  by province on both sides rather than decided in a single siege. The affordance is inert
  until the file names a non-power, so a scenario promotes one minor at a time.
- **A promoted minor can be a great power's colony.** A `COLONIAL_HOLDINGS` entry assigns a
  promoted minor's ground and its garrison to a colonial power instead of an independent
  nation, with the provinces beginning as that power's own core and a garrison stationed on the
  colony's own hub (the metropole is across the sea). Eritrea and Italian Somaliland are
  Italy's, so landlocked Ethiopia is reachable by land and the Abyssinian war is prosecutable —
  Italy meets it along a real frontier rather than only across an ocean. British Somaliland
  stays a separate independent minor. Reversible: emptying the one dict restores the minors.
- **Armies can move.** An order to redeploy inside your own territory now moves the troops,
  and a power at war marches its rear formations toward its frontier each turn — without
  either, a war was fought by whichever divisions happened to be standing in the right place
  in January, and the front stalled. The player's staff does the same, and any formation the
  player gave an order to is left where the order put it.
- **Pace re-tuned from a whole campaign** rather than one siege: France overruns Germany in
  about eleven monthly turns, against thirty at the country-level figure. A comparable
  defender costs two turns per region, a strong army dug into mountains eight.
- **Rendering follows.** A region draws every shape it is made of; zoomed out, each power's
  quiet ground dissolves into one country while contested and newly-changed ground stay
  drawn as places; a siege is hatched with whoever is pushing and its outline thickens with
  the pressure; geometry off screen is not resolved at all.

### Phase 3 — the powers decide their own wars

- **Military objectives.** A tracked power declares ground to push into, hold, or withdraw
  from. Intent and not a write path: the engine checks every name against the map, refuses
  what the power cannot reach with a reason it can act on, turns a push into a contest the
  resolver cannot tell from any other, and points that power's reserves at what it said it
  wanted. Standing until changed, and written as diffs so a rewind restores what each staff
  was trying to do.
- **A summarised front in the briefing**, to a hard budget of three enemies and five
  provinces each with the remainder stated as a count — an unbounded province list is what
  hollowed out these calls before.
- **Focus Mode tells a war from a border.** A power with live contests against the player
  outranks one at war on paper, which outranks one that merely shares a frontier.
- Minor powers keep the collective world-simulation channel, which is unchanged.

## Open-ended play

### The freeform channel

- **Orders no mechanic covers stop evaporating.** Anything the parser could not classify
  became `other`, which no handler read — so an absurd order was accepted, recorded as
  issued, and then nothing happened, indistinguishably from a bug. A new `freeform` role
  now narrates what the country made of it.
- **The model chooses a direction, never a number.** The response schema has no numeric
  field: a nudge is a field, a direction and a coarse magnitude from a closed enum, and
  engine code turns those into a value. Same discipline as evidence flags in a contest.
- **Three columns and no others** — `nation.stability`, `nation.legitimacy`,
  `relation.score` — allowlisted for the role and checked against that allowlist where
  each nudge becomes a diff. Territory, units, the treasury and the economy are absent by
  construction, so no wording reaches them.
- **Freeform proposes; the engine writes**, which is why it is not among the roles
  `diff_batch` accepts, alongside `advisor`. Four structural bounds: ≤4.0 per nudge, ≤3
  nudges per order, ≤2 orders per turn, and ≤6.0 per field per turn across all of them.
- **Refusal is a first-class answer.** An order that is really a hard mechanic is refused
  with the real order named, and changes nothing at all.

### Public works

- **The queue is the feature.** ATLAS skewed toward war because war was the only system
  with a *middle*: a contest accumulated over turns and a player could watch it move,
  while everything else resolved the instant it was ordered. Housing, rail, public health,
  schools and land reform are paid for when ordered, sit in the queue for four to eight
  turns, and deliver when they finish.
- **The same finite queue as a battleship**, deliberately: a country choosing between
  housing and a capital ship is the decision the system exists to pose, and a separate
  peaceful allowance would remove it.
- **Effects land on regions as well as nations.** Development and population are columns
  the map already draws, so a player who builds nothing military still watches their
  country change.
- **Land reform is a gamble**, drawn from the world's seeded stream against the nation's
  legitimacy, and it can set the countryside alight instead of settling it. A menu of
  small guaranteed gains is a shopping list; one real risk makes it a game — and gives the
  peaceful side the property that made the war side work.

### Industry

- **Capacity became a column instead of a formula.** It used to be `round(economy / 20)`,
  recomputed on the fly — a consequence of the economy and never a decision. `nation.industry`
  is something a country can put years into, and can lose with the province the works were
  in. Existing worlds are backfilled at exactly the ratio the old formula assumed, so no
  queue changes size.
- **Steelworks and shipyards** are public works like any other: same queue, same waiting,
  and they deliver industry rather than development. A steelworks costs the district some
  stability, because it is filthy and the district knows it.
- **Each works costs more than the last.** Factories that build factories grow
  exponentially; scaling the cost with the industry already held turns that into a curve.
  That is the whole anti-runaway guard, and it is one multiplication.
- Industry is engine-only: a power that could write it could put five years of steelworks
  into a sentence.

### Research, and building above history

- **Every effect is something shipped code already reads** — that is the test a technology
  has to pass to be in the catalogue. Welded hulls cut ship build times the yards consult;
  radar and carrier doctrine raise the base strength every delivered formation is stamped
  with; mass production and synthetic fuel raise the industry that sets the size of the
  queue. A tech unlocking something nothing consults is a line in a menu.
- **Prerequisites, and the same queue again.** Carrier doctrine needs welded hulls first,
  and a programme competes with a battleship and a housing estate for a slot — what a
  country does with the next seven years is one decision, not three.
- **Ahistorical divergence falls out.** Nothing in the engine references what actually
  happened; 1936 is a starting position. A power that spends years on shipbuilding
  research launches better ships faster than it historically could, and one that spends
  them on land reform does not.
- Knowledge and the templates it changes are written as diffs and folded into the
  fingerprint, so a rewind past a completed programme does not replay the turn with better
  ships than the turn actually had. Build times can never be driven below one turn.

### Secret programmes

- **No new hiding mechanism, and that is the design.** An atomic programme writes its
  events `secret` with only its owner in `event_visibility`; `known_events` has always
  filtered on exactly that, and rule INFO-002 has always guaranteed a diff is never more
  observable than its cause. A hidden programme is hidden by the three lines that hide a
  secret treaty, so there is nowhere for a second mechanism to be forgotten.
- INFO-002 proved it by refusing the first draft — the start diffs were still public while
  the event was secret, and "a covert cause cannot produce overt state" caught it.
- **Espionage stopped evaporating too.** It was in the parser's enum with no handler. It
  now grants sight of a secret event that already happened — a row in `event_visibility` —
  which is what intelligence actually is. The attempt is a seeded draw, so a rewind replays
  the same failure, and one service's success is not everybody's.

## Navies

- **A beaten garrison gets out, or does not.** A province that changed hands used to leave
  the defenders standing in it. They now withdraw to ground the same attacker is not
  already pressing, and are captured with the province when there is nowhere to go.
- **Water is on the map.** 113 named seas, straits and channels from Natural Earth's marine
  layer at scalerank 4 or better. A sea is an ordinary region owned by nobody, so sea
  control is the `controller_nation_id` an occupation already writes and a fleet action is
  a contest resolved by the same seeded draw as a battle for a province. Coasts are
  discovered from the adjacency rather than declared; sea-to-land borders are matched on a
  quarter-degree grid, because land and water were digitised separately and their
  coastlines never agree exactly.
- **Fleets, historically.** Battleships, carriers and transports, seeded as the fleets that
  existed on 1 January 1936 — fifteen British capital ships against Germany's three pocket
  battleships, six British carriers against nobody else's more than four. A carrier reaches
  a sea further than a battleship, which is the argument the 1930s naval staffs were having.
- **Armies and fleets move through different maps.** Every path that moves a formation
  learned that the map has two kinds of ground. Getting this wrong stalled a land campaign
  at four provinces without saying why.
- **Amphibious invasion.** Land formations never go to sea; an assault is an ordinary
  contest whose reachability is unusual. It needs one sea touching both shores, water no
  enemy controls *or has the stronger fleet in*, and transports to cap what goes ashore.
  Islands, exclaves and overseas departments stop being unwinnable. No new channel, column
  or write path: a power declares a push on a coastal region exactly as it does anywhere
  else.
- **Ships are built over time.** An order to build becomes a yard with something in it:
  paid for when ordered, delivered when finished, capped by an industrial capacity that
  follows the economy. The first instance of a production system rather than the whole of
  one.

## Nuclear weapons

A layer whose whole ambition is that almost nobody ever uses it. Nuclear content had been
prose — a sentence in a preset's hard constraints and two technology names that moved
`military_strength` — so nothing in the engine knew who was armed, nothing could be deterred,
and nothing could be fired.

- **An arsenal is state.** A new `arsenal` table (schema 10, additive and forward-only) holds
  how many warheads a nation has and how it would deliver them: `none`, `aircraft`, `missile`
  or `triad`. Both columns are engine-only — a model that could write its own count would have
  an instant-win button, and one that could write somebody else's could disarm them by
  narration. Modern Day's nine nuclear powers are armed at world creation; 1936's map has no
  rows and behaves exactly as it did before any of this existed.
- **Survivability is read off the map, not stored.** How much of an arsenal would still exist
  after being struck first is the delivery tier plus the submarines actually at sea — so
  sinking a rival's boats weakens its deterrent, and an anti-submarine campaign becomes a
  strategic act. Britain keeps seven warheads of eight through a first strike; North Korea
  keeps one of three.
- **Deterrence, without anybody launching.** A survivable arsenal is a term in the pressure
  calculation against the *homeland* it guards — the ground a nation is from, never ground it
  occupies, so it makes a country hard to invade without making any border it crossed
  permanent. Capped, because invading a nuclear power should be hard and not impossible: on
  the real 2026 map, a nuclear Ukraine loses three provinces in ten turns where the shipped
  one loses nine. This is where most of the value is, and it costs one query per contest.
- **A launch is an undertaking, adjudicated like any other.** `nuclear_launch` is a gambit
  kind: proposed by a staff, refused before the roll if the world does not permit it, charged
  a stake, decided by a seeded draw, and landed as diffs. It files under an event kind the
  closed enum already carried, so no enum was widened.
- **The proposer chooses where, and the engine chooses everything else.** Alone among the
  gambit kinds, a launch takes no effect verbs. A warhead craters the province's population and
  development, burns every formation standing in it whoever owns them, spreads fallout to
  three neighbours, shakes the struck government, disgraces the launcher, and collapses every
  other state's regard for it — all capped, all applied by the engine. That asymmetry is what
  makes it safe for a launch to reach columns a gambit may not write: no wording of any
  proposal selects, changes or omits any of it.
- **It still takes no ground.** A launch moves no border, raises no formation, opens no contest
  and starts no war. Those stay with the resolver and the declaration channel.
- **The reply is automatic.** If the target retains a survivable arsenal, the engine fires back
  in the same turn — no model call, no decision, no chance to reconsider. Proportional, one
  exchange deep, and it fires **whether or not the first strike got through**, because an
  intercepted warhead is still a warhead somebody fired at you. A first strike does destroy the
  share of an arsenal that was never dispersed, which is exactly why a triad answers and a
  small land-based force may not.
- **The stakes are disclosed rather than forbidden.** The briefing states the effect
  vocabulary, every cap, and the retaliation rule — then names the specific rivals who could
  answer and with how many warheads. Nowhere does it say that using one is unthinkable. A staff
  that can see it will be fired on regardless of whether its own strike worked can reach that
  conclusion itself, and a conclusion reached is worth more than an instruction given.
- **An exchange rewinds exactly.** Warheads spent, warheads caught on the ground, a ruined
  province, degraded formations, a collapsed reputation and the reply are all ordinary diffs.

## A third era: 1972 — Cold War

A third playable era beside 1936 and Modern Day, and the first position in which every
mechanism built since the second one is load-bearing at the same time.

- **The year is 1 January 1972, and the year is the design decision.** Not the early Cold
  War, where there is no deterrence to model — the Soviet arsenal in 1950 is about five
  devices with no way to deliver them, and neither bloc exists yet. Not 1962, where five
  thousand American strategic warheads against three hundred Soviet is American
  *superiority* rather than mutual assured destruction, and where the China seat on the
  Security Council is still held from Taipei. In January 1972 both superpowers hold a full
  triad at rough parity, SALT I is signed in May, the People's Republic took the China seat
  three months ago, and both blocs stand at full extent — so the five permanent members are
  exactly the five nuclear powers and nothing has to be fudged.
- **A starting position, not a script**, exactly as 1936 is one. Nixon lands in Beijing in
  February, the Easter Offensive opens in March, SALT is signed in May, and the Yom Kippur
  War is twenty-one months away. All four are visible from turn one and nothing in the
  engine makes any of them happen.
- **Twenty powers.** Two superpowers, NATO's European members, the Warsaw Pact including
  the one that refuses orders, China as a third nuclear pole, Japan, and four flashpoint
  pairs — the two Vietnams, the two Koreas, Israel and Egypt, and Cuba. Both Germanys and
  both Vietnams are powers rather than minors, for the reason Ukraine is one in 2026: a
  proxy war fought by a one-division generic minor resolves in a single contest and teaches
  nothing.
- **The blocs are commitments and the Council is a body**, which is one decision and not
  two. NATO and the Warsaw Pact are `defensive_pact` rows, so an attack on any member hands
  every other member a casus belli through the Article 5 the engine already reads. Seeding
  a bloc as a convening organisation as well would count the same attack twice. The
  Security Council is the one convening body, because it has what a pact does not: a veto,
  and jurisdiction over powers who are not its friends.
- **The Council is structurally unable to act against either superpower**, and that is not
  coded anywhere — it falls out of the five permanent members being the real five. A
  resolution against a power a permanent member is fond of is vetoed before the roll, the
  body loses standing for having been vetoed, and nothing is applied.
- **Deterrence is mutual for the first time.** Forty-six American warheads and thirty-eight
  Soviet, both a triad, both retaining more than thirty through a first strike, and both
  homelands measurably harder to invade before anybody launches anything. Britain is listed
  as a triad on four warheads because a submarine-only deterrent is maximally survivable,
  which is what that column asks. China is listed as aircraft on two — armed, and not
  survivable, which is the whole reason Beijing spent 1969-72 looking for a third position.
- **West Berlin is on the map as ground the United States owns.** Natural Earth has one
  Berlin shape and the city cannot be halved, so the scenario says so rather than inventing
  geometry and gives the shape to the Western garrison — the half that could be attacked,
  garrisoned on its own ground, with East Germany as its only land neighbour. A move on it
  is an attack on the United States, and the alliance is invoked by the shipped mechanism
  rather than by anything written for this scenario.
- **Ten countries are drawn province by province**: both Germanys, Poland, Czechoslovakia,
  both Koreas, both Vietnams split at the 17th parallel, Cuba, and Egypt less the two Sinai
  governorates, which Israel has held since 1967. The Soviet interior is deliberately not
  among them — the campaign of this era crosses the inner German border, not the Volga.
- **A 1972 fleet and a 1972 tree.** The battleship is thirty years gone; the carrier is the
  only way to put sustained air power over an ocean, and the guided-missile cruiser and the
  nuclear boat are the two answers a power without carriers can afford. Fifteen American
  carriers against two Soviet, and twelve Soviet boats against nine American, is Gorshkov's
  whole thesis in two lines of a table. The research tree is a missile argument at every
  level — surface-to-air belts, solid fuel, multiple warheads, reactors at sea — with the
  integrated circuit upstream of all of it, because in 1972 it had just become the thing
  every guidance package is built out of.
- **The governments are the real ones**, as 1936's are: Nixon, Brezhnev, Mao, Brandt,
  Honecker, Pompidou, Heath, Sato, Meir, Sadat and the rest, in the offices they held on
  that morning. 1972 is settled history, so there is nothing to invent.
- **Nothing about the machinery is new.** One data module, one territory file, one preset,
  one registry entry. No schema change, no migration, and no closed enum widened. The two
  shipped eras' seed and world fingerprints are unmoved, checked section by section.

## Trade: what a lane costs, and how much room it has left

Cargo used to be barter. It is now priced, both governments may walk away from the terms, and
this section is about the third thing that turned out to be needed: a player being able to
*see* a lane approaching its end.

### The price cliff was a legibility failure, not a pricing one

Binary consent creates a discrete suspension somewhere by construction, and moving a threshold
only moves where. So the question was not "where should the cliff be" but "why does it arrive
without warning", and that was answered by measurement rather than argument: every in-play
suspension across three eras, stressed and calm, classified by which named input actually
carried it (`scratchpad/CLIFF_DIAGNOSIS.md`).

- **212 suspensions. 95% are the seller withdrawing because the price *fell*.** 99% of those
  land on exactly `FLOOR_PRICE` — and since the squeeze and pressure terms are bounded below
  at 1.0 and distress at 0.55, **only the scarcity term can take a quote to the floor**. The
  cause is a transient collapse in the *seller's own* supply: a seller shorter of a good than
  its buyer is quoted less for it, and at the floor it would rather keep the cargo.
- **Zero were the case the engine was warning about.** The warning shipped one commit earlier
  watched the buyer's ceiling, closed by the buyer's regard for its supplier — one boundary,
  one input, and the alert list queried only routes the nation was *buying* on. It was
  structurally incapable of seeing 95% of what happens.
- **The cliff is where two constants meet.** `RESERVE_PRICE` *is* `FLOOR_PRICE`, so the least
  a cargo can be quoted at is the least a seller will ship for and there is no gap between
  them. That equality is deliberate and calibrated; the discontinuity is the setting working.

### Commercial headroom, in place of the regard warning

Per active route, whoever is at either end of it: what the contract stands at, **what it would
be if repriced today**, the buyer's maximum and the seller's minimum, which of the two is
nearer and by how much, **the turn and the date the terms next reopen**, the single input
currently eating the margin — named by counterfactual, "put this factor back to neutral and
how much room comes back" — and the lever on that input. On a suspension it states which of
three things happened: the price rose, the price fell, or a boundary moved.

- **The forecast is the actual fix.** A repricing is a step by construction, so a lane sits
  deep inside the band right up to the turn it does not. Both ends of that step and the date
  it lands on are computable now, which turns the one genuinely unforeseeable event in the
  layer into a scheduled one.
- **Two-sided, in each party's own words**, because the boundary that stops lanes is the
  seller's. A seller sees its own floor and *that* the buyer has a ceiling — never the number.
- **Quiet.** 0.5–2% of live lanes warn on an ordinary turn, rising to 23.5% on the turn before
  suspensions arrive; and the surface names `scarcity` on every stepping lane, reproducing the
  diagnosis without being told it.

### No mechanic was changed

Oscillation was measured because hysteresis was the obvious thing to reach for. It is not
warranted: of 70 close/reopen cycles the **median lane stayed shut 180 days — exactly one
repricing interval — and one cycle in 70 was shorter than the 60-day dwell already in the
engine**. Two eras produced no repeat-transition routes at all. The stagger already imposes a
floor on suspension length three times longer than a dwell would, so damping here would
suppress a correct signal to hide a legibility failure. The existing day-denominated clauses
are kept and pinned as durations by test.

**And none of it mutates.** Every number is derived when a player looks; nothing is stored,
stamped or cached. Checked rather than asserted — the same worlds played forward on this
branch's tip and on the working tree digest identically, turn for turn, which matters because
the `event` table *is* fingerprinted.

## The air situation: a layer the model knew and the player did not

The air war has been real, wired and load-bearing since it shipped. Air superiority is fought
for on a seeded roll every turn, persists in its own table, and reaches the rest of the world
by five independent paths — the odds of a land battle, the odds of a fleet action, the advance
after a battle won, the supply corridor underneath, and the decay that takes it all back off
whoever stops flying. Every one of those five is written into the model's briefing with its
actual number. **None of them had ever been said to the player**, who got a map layer, a
right-click menu and a headline.

Nothing here changes a combat mechanic or how air resolves. The whole surface is a read-only
derivation over the constants the engine already uses.

- **One coherent surface, per theatre.** `engine/airsituation.py` answers, for one province:
  who holds the sky and how decisively, when that hold will lapse if nobody flies, which
  formations put it there and from where, what it is doing to all five downstream systems,
  what is being interdicted and which corridor has actually gone, and what can be done —
  contest, strike, reinforce, or accept it. `ui/airsituation.py` draws it, off the region
  menu and off the region tab. The adjudicated state, never the roll: exact constants live on
  the tooltips and behind *How the air war works*.
- **The corridor is measured, not asserted.** An enemy-held sky costs two hops of a three-hop
  supply budget, which is among the strongest effects in the layer and was described to
  *nobody* — not the player's surfaces and not the model's air rules block either. The panel
  walks the supply graph twice, once as the quartermaster walks it and once with the skies
  clear, and reports the difference: which provinces have gone out of reach and how many
  formations are standing in them. The engine's walk is pinned against the counterfactual's,
  so the two cannot drift apart in silence.
- **`skies_taken` and `air_battles` reach a surface.** They were computed every turn since
  the layer shipped and read by nothing but a probe script. The post-turn report now carries
  the air campaign: skies that changed hands, written from the player's side of it, skies let
  go of, and — derived — **interdiction beginning**, which nothing anywhere had ever
  announced.
- **Alerts, filtered to whose air war it is.** A theatre reaches the alert list when the
  player holds the ground, an ally does, an enemy does, or the player has pinned it. Not every
  air contest: a campaign is one alert naming the ground, because a single turn of one war
  really does take five or six skies at once, and that is the flattening the attention
  substrate exists to prevent.
- **Two why entries.** What the sky was worth to a battle, and what supply costs under it —
  both re-derived through the calls the engine makes, both naming what would change them.
- **A confirmation that says what a raid will not do.** Keyed on the gambit kind rather than
  the action type, so sabotage and a summit stay quiet: an air strike names its theatre, says
  it is a public act against whoever holds the ground, and says outright that it takes no
  ground.
- **The rules exist, for the player.** There was no air entry in the reference, the advisor,
  the why panel or the alerts. `air_rules` generates the whole of it from the tables the
  engine reads — including the two things the model's own block never said, interdiction and
  the lapse — and it reaches the panel's disclosure and the council's briefing from the same
  call, so the two cannot come to disagree about a constant.

### Three technology descriptions that described the wrong mechanic

Treated as correctness defects. Every shipped effect is untouched; only what the player is
told changed, and no strategic-missile mechanic was invented to make the old prose true.

- **`solid_fuel_missiles` → re-described.** Its blurb was a silo — "a missile that can sit
  fuelled for years and fly on a minute's notice" — and what it buys is `+2.5` to a cruiser
  and `+2.0` to a submarine. Naval armament, not a deterrent, and there is no
  strategic-missile mechanic for the old prose to have been about. The blurb now says so.
  **Described rather than renamed, and the reason is a recorded prompt:** the research
  catalogue a tracked power is briefed on carries each programme's *name* and its effects and
  never its blurb, so the blurb is the player's alone and can be made honest for free —
  while renaming this one changes 50 lines of the active cassette and reddens the release
  gate until somebody re-records against live models. Solid fuel really is what put a
  ready-to-fire missile in a cruiser's launch tube; the name was never the lie.
- **`hypersonic_strike` and `mirv` → re-described.** Both are real and both were half-told:
  what they buy is `air.PENETRATION_TECHS`, which makes what you *send* survive interception —
  a bomber's raid exactly as much as a warhead, because the engine has one interception term
  and both are met by it — plus the hull bonuses a ship carrying them earns. The names were
  never the problem; the blurbs stopped before the mechanic.

### Finishing the surface: a date, a severity, and the room the panel was not using

The five effects reached the player in the section above. Three things they reached them
*as* were still wrong.

- **A lapse is a date, not a turn count.** The forecast said *in 3 turns*, which is a fact
  about how many times a button gets pressed. At monthly pacing that is June 1897 and at
  yearly pacing it is March 1900 — two readings that look almost identical and are three
  years apart, and a player planning a campaign is planning against a calendar. The panel
  now leads with the world date and keeps the count beside it, derived through
  `pacing.after_months` from the same span the decay itself is scaled by, so the two cannot
  come to disagree. A world whose clock will not parse gets the count alone and no invented
  date. Where both sides can still reach a theatre the date is stated as the counterfactual
  it is — *were both to stop flying* — rather than as a prediction about anybody's
  intentions.
- **A corridor has a severity, and it is a share rather than a count.** "Four provinces out
  of reach" is catastrophic to a power with six and a rounding error to one with sixty, so
  the panel was handing over the arithmetic instead of the finding. The two walks it already
  performs measure the answer — `1 - reach_now / reach_clear` — and four bands name it:
  `contained`, `constricted`, `severed`, `collapsed`. It reads as a tone-coloured verdict
  beside the section heading, so it survives a scan. `contained` rather than silence,
  because a corridor that has cost nothing *yet* is one province of enemy advance from
  costing everything, and an effect nobody is told about is one that reads as the engine
  being broken on the turn it finally bites.
- **The band the panel shows is the band the attention list ranks on.** A `severed` or
  `collapsed` corridor is *act now* rather than *decide this turn*, and the word is in the
  headline — two corridors filed under one salience ask the player to open the panel to find
  out which one they are looking at, which is what an attention list exists not to do. A
  floor and not a replacement: `ALERT_KINDS` still owns the kind's base salience and this can
  only raise it.

**And the panel now uses the screen it is given.** The base overlay caps a panel at the
window and otherwise lets it be as tall as its own size hint — right for a roster that ends,
wrong for a reading surface longer than any window. A scroll area's hint is a small constant,
so this panel asked for the same 680px at every size: at 1920×1080 that was 336 points of
empty canvas above and below a column still hiding 855px, with the corridor — the whole
reason the surface exists — under the fold on a screen with room for it twice over. The same
arithmetic ran the other way at 1280×720, where the panel overflowed its own cap by 24px and
the *outer* sheet scrolled, which is two nested scrollbars on the size the interface is drawn
for. Both are gone: the body asks for the room, measuring the chrome off the laid-out panel
rather than modelling it, since a wrapped verdict is a different height for a long nation's
name. The 360px body minimum turned out to *be* the panel's height rather than its floor and
is now the floor it was meant to be.

### Staged for the next live re-record, and switched off

Three of the corrections above have to be paid for in live-model quota, because they change
text a model reads: the active cassette is replayed against HEAD and a changed prompt is a
MISS by construction. That is an hour of somebody's subscription and it is worth paying once
for a batch rather than once per sentence — so `engine/rerecord.py` holds them written,
reviewed and tested, behind `ATLAS_AIR_REBRIEF`, defaulting **off**.

- **The interdiction bullet** — an enemy-held sky costs two hops of a three-hop supply
  budget. The strongest air effect in the engine and the only one the model's own air rules
  block never mentioned, which is exactly the failure that block was written to prevent one
  bullet further down for interception.
- **The lapse bullet** — an unflown sky loses a quarter of a point of margin a month and is
  let go of under the dead band. A power that thinks a sky won in 1947 is still its own in
  1950 is planning around a bonus that has already gone.
- **`solid_fuel_missiles` → `Solid-fuel naval missiles`.** The blurb was corrected outright
  in the section above because a blurb is the player's alone and is not on the tape. The
  *name* is, with its effects, and `Solid-fuel missiles … → cruiser base_strength +2.5`
  invites a strategic reading of a naval armament. One word fixes it and only a recording can
  land it. Applied in `research.catalogue_for`, the one place a catalogue is read from; the
  `technology` table stores `tech_id` and never a name, so this is render-time text that
  cannot reach a saved world, a fingerprint or a replay.

**Verified in both directions rather than asserted.** With the switch off the active tape is
*12 matched, 0 mismatched, 0 missed*. With it on, turn 1 MISSes on a `tracked_agent` call —
which is the evidence that the staged text really does reach a model prompt and genuinely
needs the recording, and not a claim that it might. Landing the batch is one boolean and one
`pt46_intel.py` run.
