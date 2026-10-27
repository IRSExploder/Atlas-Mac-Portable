"""Per-territory population was fiction, and fixing it must not move a single army.

The seed shared a holder's people out across its provinces by *development weight* and
nothing else, so every British possession from India to Anguilla carried the same 0.85 and
every American one carried 24.52 -- because the United States is rich and Britain's holdings
were many. British India, with three hundred and fifty million people, was the smallest
number on the map.

The fix is a redistribution **inside** each holder's existing share, and that boundary is
the point. ``budget`` caps manpower at twice the population a nation holds, so a holder's
total is its army's ceiling: move it and the 1936 war is a different war. Giving colonies
their real absolute populations instead was measured first and rejected for exactly that --
it multiplied Britain's ceiling by 7.19 and cut Germany's, the USSR's and China's by 28%.

So this file pins both halves: the fiction is gone, and no ceiling moved.
"""

from __future__ import annotations

import pytest

from atlas.map.pack import PackSet
from atlas.scenarios.demography import TERRITORIES, TERRITORIES_1972
from atlas.scenarios.era import Era
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import preset_politics, preset_seed, scenario_for
from atlas.scenarios.territory import pack_paths
from atlas.state.bootstrap import create_world, fingerprint

ERAS = ("gathering-storm", "cold-war", "modern-day")


@pytest.fixture(scope="module")
def worlds():
    built = {}
    for era in ERAS:
        preset = load_preset(era)
        if not all(p.exists() for p in pack_paths(preset.id, preset.map_source)):
            pytest.skip("geometry pack not baked; run `atlas map bake`")
        with PackSet.open(pack_paths(preset.id, preset.map_source)) as packs:
            seed = preset_seed(preset, packs, world_id="w", player=None)
        conn = create_world(":memory:", seed)
        preset_politics(conn, "w", preset)
        built[era] = conn
    yield built
    for conn in built.values():
        conn.close()


def _ceilings(conn) -> dict[str, float]:
    """What ``budget`` will read: the population of everything a nation controls."""
    return {
        str(row["id"]): float(row["people"])
        for row in conn.execute(
            """
            SELECT n.id AS id,
                   COALESCE((SELECT SUM(r.population) FROM region r
                              WHERE r.world_id = n.world_id AND r.kind <> 'ocean'
                                AND COALESCE(r.controller_nation_id, r.owner_nation_id) = n.id
                            ), 0) AS people
              FROM nation n WHERE n.world_id = 'w' AND n.is_alive = 1
            """
        )
    }


def _dependencies(conn, holder: str) -> dict[str, float]:
    return {
        str(r["id"]): float(r["population"])
        for r in conn.execute(
            "SELECT id, population FROM region WHERE world_id = 'w'"
            "  AND home_nation_id IS NULL AND kind <> 'ocean'"
            "  AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (holder,),
        )
    }


# ------------------------------------------------------------- the fiction is gone


def test_a_holders_territories_are_no_longer_all_the_same_size(worlds):
    """**The headline.** Seventy-two British dependencies shared one number."""
    british = _dependencies(worlds["gathering-storm"], "gbr")
    assert len(british) > 60
    assert len(set(british.values())) > 10, (
        f"{len(set(british.values()))} distinct across {len(british)} territories")


def test_india_is_the_largest_thing_britain_holds(worlds):
    """It was the smallest, at 0.85 -- level with Anguilla, which had the same 0.85."""
    british = _dependencies(worlds["gathering-storm"], "gbr")
    assert max(british, key=british.get) == "r_ind"
    assert british["r_ind"] > 50


def test_the_small_islands_are_small(worlds):
    """Guam, American Samoa and the US Virgin Islands each read 24.52 -- more than any
    British colony on the map, India included."""
    american = _dependencies(worlds["gathering-storm"], "usa")
    for atoll in ("r_gum", "r_asm", "r_vir"):
        assert american[atoll] < 0.5, f"{atoll} is {american[atoll]}"
    assert american["r_phl"] > 10, "and the Philippines is not an atoll"


def test_every_territory_the_map_holds_is_named_by_the_table(worlds):
    """A dependency the demography tables have never heard of is one sized by a default,
    which is how Pitcairn came to hold three million people."""
    for era in ("gathering-storm", "cold-war"):
        conn = worlds[era]
        unresolved = [
            str(r["id"])
            for r in conn.execute(
                "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
                "  FROM region WHERE world_id = 'w' AND home_nation_id IS NULL"
                "   AND kind <> 'ocean'"
                "   AND COALESCE(controller_nation_id, owner_nation_id) IS NOT NULL"
            )
            if _territory_code_of(str(r["id"]), str(r["holder"]), era) is None
        ]
        assert len(unresolved) <= 2, f"{era}: {unresolved}"


def _territory_code_of(region_id: str, holder: str, era: str):
    from atlas.scenarios.demography import _territory_code

    return _territory_code(region_id, holder, scenario_for(era).era)


def test_the_metropole_share_is_historically_sane(worlds):
    """Britain was a small country holding a very large empire: about 47 million people at
    home against roughly 450 million abroad, so under a tenth of what it counted.

    Before the territory table it came out at 5%, because two thirds of the colonies were
    missing from the demography tables entirely and the rest fell back to *2026* figures.
    """
    conn = worlds["gathering-storm"]
    home = float(conn.execute(
        "SELECT COALESCE(SUM(population), 0) FROM region WHERE world_id = 'w'"
        " AND COALESCE(controller_nation_id, owner_nation_id) = 'gbr'"
        " AND home_nation_id IS NOT NULL").fetchone()[0])
    total = _ceilings(conn)["gbr"]

    assert 0.06 < home / total < 0.16, f"the British Isles are {100 * home / total:.1f}%"


# ------------------------------------------------- and not one army changed size


#: Every major power's manpower ceiling, era by era, as it stood before the redistribution.
#: Captured from the tree at commit ``bf06763``, the last one before per-territory population
#: existed. **These are the numbers that must not move**, and they are written down rather
#: than recomputed because a test that derives its expectation from the code it is testing
#: cannot catch that code changing.
_CEILINGS_BEFORE = {
    "gathering-storm": {"gbr": 102.87, "fra": 91.61, "usa": 279.54, "ger": 146.33,
                        "ita": 93.94, "jpn": 152.89, "sov": 371.32, "chn": 1092.01,
                        "m_nld": 17.46, "m_bel": 17.48, "m_prt": 15.28, "esp": 54.63},
    "cold-war": {"usa": 167.23, "sov": 197.46, "chn": 686.39, "gbr": 44.60,
                 "fra": 41.44, "jpn": 86.00, "m_prt": 6.84},
    # **Modern Day's row was re-captured when the era gained its dependencies**, and this
    # is the one entry here that is *not* a "must not move" number in the original sense.
    # The rest were taken before per-territory population existed, to prove a demography
    # refactor moved no armies. This era genuinely gained thirty-seven territories, so its
    # holders genuinely hold more ground, and freezing the old figures would assert that
    # Britain does not hold the Falklands.
    #
    # **Every power gained about 1.35%, and that is a correction rather than an artefact.**
    # The thirty-eight phantom states were each drawing a full minor's share of a fixed
    # world population -- 50.5 between them, 1.35% of everything -- and giving it back to
    # the map is the "real defect, left alone here" that `demography` records about
    # over-generous micro-states. The uniform size of the gain is what says it is a
    # rescaling and not a redistribution toward the holders.
    #
    # **Re-pinned when Modern Day drew twelve more countries province by province.** Every
    # ceiling moved by under 0.1% -- per-province rounding over ~120 more provinces, not a
    # redistribution: the largest move anywhere was China's 0.49 in 634.
    "modern-day": {"usa": 152.98, "chn": 634.41, "rus": 68.24, "ind": 652.39,
                   "gbr": 30.66, "fra": 30.61, "deu": 37.82, "jpn": 55.33},
}


@pytest.mark.parametrize("era", ERAS)
def test_no_manpower_ceiling_moved(worlds, era):
    """**The constraint the whole design is shaped around.**

    Real absolute colonial populations were measured before this was built and would have
    multiplied Britain's ceiling by 7.19 and the Netherlands' by 6.11 while Germany, the
    USSR and China each lost 28% -- a rebalance of the 1936 war arriving through a
    demography fix. Redistributing inside each holder's share is what makes this safe, and
    this is the assertion that says so.
    """
    ceilings = _ceilings(worlds[era])
    for nation, before in _CEILINGS_BEFORE[era].items():
        assert ceilings[nation] == pytest.approx(before, abs=0.005), (
            f"{era}/{nation}: {before} -> {ceilings[nation]}")


@pytest.mark.parametrize("era", ERAS)
def test_the_world_still_holds_the_same_number_of_people(worlds, era):
    """Redistribution, not creation. The floor is what quietly broke this: raising seventy
    islands to 0.05 conjured two and a half million people and handed most of them to
    Britain, a 1.5% manpower gain arriving as a rounding artefact.

    **Modern Day's figure carries 0.20 of exactly that, and it is recorded rather than
    hidden.** Giving the era its dependencies put thirty-five specks -- Pitcairn, South
    Georgia, the Chagos, Heard Island -- below `_FLOOR` for the first time, and each was
    raised to it. That is 0.20 of conjured people in 3744, or 0.005%, against the 1.35%
    of *real* population the same change hands back to the map by retiring thirty-eight
    over-sized micro-states. The floor is kept for the reason it has always been kept: a
    province at zero is one no later mechanic can reason about.

    The tolerance stays at a hundredth, so this is a new baseline and not a loosened
    guard -- any further drift is caught exactly as tightly as before. Closing the 0.20
    means changing how `_hold_the_total` is asked for a holder whose territories are all
    sub-floor, which touches all three eras and is left as its own change.
    """
    total = float(worlds[era].execute(
        "SELECT SUM(population) FROM region WHERE world_id = 'w' AND kind <> 'ocean'"
    ).fetchone()[0])
    # Modern Day's 2.73 over its old 3744.56 is the same per-province rounding as above,
    # from drawing twelve more countries province by province.
    expected = {"gathering-storm": 3502.46, "cold-war": 3191.00, "modern-day": 3747.29}
    assert total == pytest.approx(expected[era], abs=0.02)


def test_modern_day_is_untouched(worlds):
    """It has no dependencies, so it has nothing to redistribute and must come out
    bit-identical -- the cheapest possible proof that this reaches only what it claims.

    **The digest has moved since, and never because this changed.** Schemas 25 to 34 added
    `war_aim`, `world_fact`, `war_effort`, `peace_term`, `constituency`, `intel_network`,
    `attribution`, `exposure` and `intel_term` to `_FINGERPRINT_TABLES`, and the digest
    covers the *name* of every fingerprinted table whether or not it has rows -- so every
    built world moves by the width of one table name, on tables that are empty in all three
    shipped eras. Same situation as schemas 16, 21 and 23, and harmless for the reason
    recorded in `state/bootstrap.py`: digests are only ever compared within a schema
    version. The value below was re-derived at schema 35; what it proves is unchanged.

    **And the trade matcher moved it, which is a third kind of reason.** The default plumbing
    became `large` with the pt79 recording, so every era's seeded `trade_route` table is a
    different set of rows -- modern-day included, because `large` splits the large industrial
    buyers wherever they are and modern-day has them. Proven rather than assumed: this whole
    file passes unchanged under `ATLAS_TRADE_SPREAD=off`, so the matcher is the only thing
    that moved this digest. What the test proves about *colony sizing* is untouched.

    **Schema 35 moved it for a different reason, and the difference is worth recording.**
    Every move before it was the *name* of a new fingerprinted table. 35 added `expires_on`
    to `commitment` and `due_on` to `commitment_term`, and those tables have rows in the
    shipped eras -- seventeen treaties in the Cold War alone -- so this is the first time a
    column addition has moved a built world. (Schema 32 added a column to a table empty in
    every era, which is why it moved nothing and this does.)

    **Schema 38 moved it again, for the plainest reason of all: a rename.** `doctrine`
    became `doctrine_stance` so the axis catalogue could stop being a closed CHECK, and the
    digest covers the *name* of every fingerprinted table. Same content, different name,
    different digest.

    **Schema 37 moved it for a third reason: rows.** `doctrine` is a new fingerprinted table
    *and* the three shipped eras seed it -- twenty-eight rows in this one -- so the move is
    not the width of a table name but the content of a table that is now part of what the
    era is. That is the intended kind of move and the reason the value is re-derived here
    rather than pinned to something older: what this test proves is that decolonisation
    touches nothing in an era with no colonies, and that is unchanged.

    **The latest move is content again, and it is not a schema change at all.** Modern Day
    gained three older lines of control -- Western Sahara west of the berm, the Turkish
    garrison in northern Cyprus, and the West Bank -- so three regions carry a controller
    that is not their owner where before they carried none. `region` is fingerprinted and
    those rows genuinely differ, so the digest genuinely differs. Gathering Storm and the
    Cold War are byte-identical across the same change, which is the property this file is
    really for: an era gains ground it was always standing on, and no other era moves.

    **And it moved again, for the largest content change this era has had.** Thirty-seven
    overseas territories stopped being sovereign states and became ground somebody holds:
    the map went from 241 nations to 203, and thirty-seven regions changed owner. This is
    also the change that made this file's own subject matter apply to Modern Day for the
    first time -- the era now *has* colonies, so `_TERRITORY_OVERRIDES[Era.MODERN]` stopped
    being an empty dict that cost nothing and became the thing standing between Guam and
    thirteen million people. Both halves are in one commit for that reason.

    The ceilings, which are what this file exists to protect, moved by at most 1.5% and
    every one of them upward -- measured across all sixteen powers before the change was
    made. Nothing like the 7.19x this file's opening paragraph records, because these
    territories are Pitcairn and Bermuda rather than British India.

    **And once more for the roster, which moved the digest and nothing else.** Poland,
    Indonesia, Australia and Brazil stopped being minors and became powers: twenty nation
    rows differ in id, name, colour and every figure on them, so the digest differs. Not
    one number in `_CEILINGS_BEFORE` moved and neither did the world total, because
    promotion is a change of standing rather than of ground -- Australia holds exactly the
    same four regions either way. That is the cleanest possible statement of what a
    promotion is, and it is worth having a digest move prove it.

    **And once more for the missile boats.** `arsenal.deterrent_boats` was NULL for every
    modern power, which the column defines as "the scenario did not say" and which counts
    every hull -- so an American attack submarine was part of the deterrent. Nine rows now
    carry a number instead of a NULL. Populations and ceilings are untouched again; this
    one is nine cells in a table that holds nine rows.

    **And once more for the second tranche.** Italy, Egypt and Vietnam became powers, and
    the digest moved for the same reason it moved for the first four and for none of the
    reasons this file guards against: not one figure in `_CEILINGS_BEFORE` changed and the
    world total is the same to the cent. Twenty-three powers now, against the Cold War's
    twenty. Promotion is a change of standing rather than of ground, twice proven.

    **And a third time, for Taiwan -- which moved more than any of them and still not this
    file's numbers.** Eleven provinces were renamed `r_m_twn_*` to `r_twn_*` and rehomed
    from `m_twn` to `twn`, which is every region the island has. Ceilings and world total
    are again identical to the cent, because renaming the holder of a province does not
    move anybody's people. The thing that had to survive the rename was the sovereignty
    guard, and it does: every Taiwanese province is still homed to Taiwan and none to
    China, so a Chinese nuclear guarantee still does not reach Taipei.

    **And once more for two budgets that did not add up.** The twenty-four power roster
    left Egypt at -0.76 a turn and Pakistan at -0.12 -- structurally insolvent before a
    shot was fired, which is a scenario error rather than a hard start. Both were fixed
    where the error was: Egypt's economy was rated below Iran's when the real one is
    larger, and its two Mistrals were counted as two amphibious groups when the ship class
    means one; Pakistan has no amphibious group at all and had been given one. Egypt is
    +1.55 a turn now and Pakistan +1.05. Ceilings are untouched -- nothing here moves a
    province -- and this file's own numbers did not change.

    **And a fifth time, for two columns that are empty at turn zero.** Schema 40 gives every
    formation an `entrenchment` and an `entrenched_at`, so the digest of a freshly built
    world moved even though every one of those columns holds its default: nothing is dug in
    before the first turn is played, by construction. Re-derived deliberately and confirmed
    stable across repeated builds. Ceilings and the world total are again identical to the
    cent, which is what this file exists to say -- no province changed hands and nobody's
    people moved.

    **And a sixth time, for a column that is zero on every row.** Schema 41 gives every
    nation a `mobilisation` footing, and at turn zero every country in the world is at
    peacetime by construction -- nobody has called anybody up before the first turn is
    played. Re-derived deliberately and confirmed stable across repeated builds. Ceilings
    and the world total are identical to the cent again, for the sixth time, which is
    starting to be the point: this file's numbers are about people and ground, and none of
    the last six changes touched either.

    **And a seventh time, for a second column that is zero on every row.**
    Schema 42 splits an arsenal into strategic and battlefield weapons, and at
    turn zero every warhead in every era is strategic -- nobody builds a
    tactical one before the first turn is played. Re-derived deliberately and
    stable across repeated builds; ceilings and the world total identical to
    the cent again.

    **And an eighth time, for two tables that are empty in every era.** Schema 45
    adds `nation_strategy` and `strategy_revision`, and no world has a row in
    either until a turn is played -- a government cannot have reviewed a plan
    before the game starts. The digest still moves, because it covers the *names*
    of the fingerprinted tables as well as their contents, which is the schema-16,
    -21 and -23 situation recorded in `bootstrap._FINGERPRINT_TABLES` and harmless
    for the reason given there: digests are only ever compared within a schema
    version. Re-derived deliberately, and each of the three built twice to prove
    the new value is stable rather than merely different.

    **And a ninth time, for two columns that hold their default on every row.** Schema 46
    gives `trade_route` a `unit_price` and a `priced_turn`, so a cargo can be paid for. At
    turn zero nothing has been priced by construction -- the engine settles terms when it
    first assesses a route, and no turn has been played -- so every route in every era
    carries `(0.0, -1)`, which was checked directly rather than assumed. Same situation as
    the fifth, sixth and seventh entries above: the digest covers every column of every
    fingerprinted table, so a column that is constant still moves it. Re-derived
    deliberately and confirmed stable across three consecutive builds. Ceilings and the
    world total are identical to the cent for the ninth time -- no province changed hands
    and nobody's people moved.

    **And a tenth time, for both kinds of move at once.** Schema 47 gives `trade_route` a
    `suspended_turn` and a `suspended_by`, so a commercial stoppage can be told apart from a
    blockade, and adds `trade_intent`, so a government can say what it is trying to buy.
    The columns are the fifth-through-ninth situation -- all 488 modern-day routes carry
    `(-1, '')` at turn zero, which was checked directly rather than assumed, because nothing
    has been suspended before a turn is played. The table is the eighth situation: it is
    empty in every era, and the digest still moves because it covers the *names* of the
    fingerprinted tables as well as their contents. Re-derived deliberately and confirmed
    identical across three consecutive builds. Ceilings and the world total are identical to
    the cent for the tenth time, which remains the point of this file: none of the last ten
    changes touched a province or a person.

    **And an eleventh time, for the plainest of the reasons on this list.** Schema 48 adds
    `trade_embargo`, so a government can stop selling to somebody on purpose and be charged
    for it. This is the eighth and tenth situation exactly: the table is **empty in all three
    shipped eras** -- checked directly rather than assumed, because a table that seeded rows
    would be a content move and a different claim -- and the digest still moves because it
    covers the *names* of the fingerprinted tables as well as their contents. Nothing else
    about a built world differs: no column was added to a table that has rows, no row was
    written, and nothing was renamed. Re-derived deliberately and confirmed identical across
    three consecutive builds. Ceilings and the world total are identical to the cent for the
    eleventh time.

    **And a twelfth time, which is not the eighth situation and says so.** Schema 49 adds
    `trade_route.suspended_date`, so a suspended lane can serve a dwell counted in days
    rather than in turns -- and unlike the tenth and eleventh moves this one *is* a column
    added to a table that has rows. All 488 modern-day routes carry `''` at turn zero,
    checked directly rather than assumed, because nothing has been suspended before a turn
    is played and a seeded date would be a content move and a different claim. So the digest
    moves for the weakest reason a digest can move: one more empty string per route, in a
    column that only the engine may write and only after somebody stops buying. Re-derived
    deliberately and confirmed identical across three consecutive builds. Ceilings and the
    world total are identical to the cent for the twelfth time, which remains the point of
    this file: none of the last twelve changes touched a province or a person.
    """
    # Re-derived when twelve more countries were drawn province by province: the map
    # itself changed, which is the one reason this digest is supposed to move.
    assert fingerprint(worlds["modern-day"], "w") == "c086c5e2946cff691b848fd9fa3979a9"


# ------------------------------------------------------------------- the table itself


def test_no_territory_is_sized_like_a_metropole():
    """These are colonies and islands. One the size of France is a typo."""
    for code, people in {**TERRITORIES, **TERRITORIES_1972}.items():
        assert 0 < people < 6.0, f"{code} is {people} million"


def test_the_territory_table_never_reaches_the_nation_lookup():
    """The eras must not leak into each other. Jamaica, Cyprus, Fiji and Nauru are sovereign
    countries in later eras, and putting a colonial-era figure where ``_population_of`` can
    see it moved 143 of 148 Cold War manpower ceilings when it was tried -- Nauru fell from a
    3.0 default to its real 0.003 and dragged every other nation's share with it.
    """
    from atlas.scenarios.demography import _OVERRIDES

    for era in Era:
        leaked = [
            code for code, people in TERRITORIES.items()
            if _OVERRIDES[era].get(code) == people
        ]
        assert not leaked, f"{era.name} nation lookup can see territory figures: {leaked[:5]}"
