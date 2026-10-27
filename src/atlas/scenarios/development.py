"""What a province is economically worth, at world creation.

``region.development`` existed from the first commit and was seeded at exactly 1.0 for
every region of every scenario. Three things depended on it and none of them worked:

* **Revenue.** ``budget.nation_budget`` sums the development of the ground a nation holds.
  With every region at 1.0 that sum *is the province count*, so the "developed ground"
  term was paying nations for owning provinces rather than for owning valuable ones.
  Measured on the 1936 map: Italy, on an economy of 36, drew more of it (7.50) than
  Germany on 66 (2.50), because Italy holds Eritrea and Somalia. The term rewarded holding
  large amounts of poor land, which is the opposite of what it was written to do.
* **Map granularity leaked into the economy.** A power the scenario promoted into provinces
  earned more than an identical power it left whole. The United States is one region in
  both eras and drew 0.25 a turn from an economy of 96.
* **The public works.** Housing, rail, workshops, steelworks and land reform all raise
  ``development``, and every point of it was worth the same 0.25 whether it was laid in
  the Ruhr or in Turkmenistan. "Develop your land" was a strategy with no texture.

So development is seeded from economic geography here. Two inputs, both honest about what
they are:

**The holder's economy**, which every scenario already hand-authors per power, sets how
much there is to distribute. A nation's provinces total :data:`DEV_PER_ECONOMY` of its
economy -- so a Belgian province is worth more than an Ethiopian one before anybody looks
at a map, and *how many provinces a scenario cut a country into stops mattering to what it
earns*.

**A table of named heartlands** distributes it. This is hand-authored and unashamedly so:
there is no dataset in the repo that knows the Ruhr from the Masurian Lakes, and deriving
it from land area -- the one geographic quantity available -- would make Siberia the
richest ground in the Soviet Union. The multipliers below are relative to their own
nation's average province, so the table says "Silesia is worth three ordinary German
provinces", never "Silesia is worth 11", and it stays correct if a scenario's economy
figures are re-tuned.

Everything not named gets the average, floored. That is deliberate: a table that had to
name all 375 regions would be a table nobody could keep true.
"""

from __future__ import annotations

from dataclasses import replace

from ..state.bootstrap import NationSeed, RegionSeed

__all__ = [
    "CAPITAL_WEIGHT",
    "DEV_PER_ECONOMY",
    "HEARTLANDS",
    "assign_development",
    "region_weights",
]


#: What all of a nation's ground is worth, as a share of its economy.
#:
#: Sets the exchange rate between the two halves of revenue rather than the absolute
#: wealth of anybody: ``budget`` reads economy and development separately, and this is
#: what decides how much of a turn's income comes from *ground* and how much from the
#: abstract national economy. At 0.55, and with the revenue coefficients calibrated
#: alongside it, roughly a quarter of a great power's income is provincial -- enough that
#: losing the industrial half of your country is felt, and not so much that a bad early
#: war is unrecoverable.
DEV_PER_ECONOMY = 0.55

#: A capital that the heartland table does not name is still a capital.
CAPITAL_WEIGHT = 1.6

#: Ground a power holds that is not its own. Detected structurally rather than guessed:
#: a scenario names a power's own provinces ``r_{power}_{name}`` and a minor's
#: ``r_m_{code}_{name}``, so Italian Somaliland is nineteen regions whose ids say `som`
#: and whose owner says `ita`. Without this the default weight of 1.0 would have let
#: nineteen provinces of the Horn of Africa outvote metropolitan Italy for a share of the
#: Italian economy, which is how an empire ends up impoverishing its own capital.
#: Overrides in the table below still win, so genuinely valuable held ground (Baku, the
#: Ukrainian SSRs) can be named back up.
PERIPHERAL_WEIGHT = 0.25

#: The least developed a province can be seeded at, whatever the arithmetic says. A
#: province at zero is one no later mechanic can reason about: it cannot be bombed, cannot
#: be improved by a housing programme in any way the player would notice, and reads to a
#: model as ground that is not there.
FLOOR = 0.25


#: Multipliers against the holder's average province. Written per era in one table because
#: region ids are era-scoped and cannot collide.
#:
#: The rule for adding to this: a region belongs here when a player who knows the period
#: would be *surprised* to find it worth the same as its neighbours. Everything else is
#: better left average -- an over-specified table is one that quietly goes stale.
HEARTLANDS: dict[str, float] = {
    # ---------------------------------------------------------------- 1936
    # Germany. Silesian and Saar coal, and the capital. Upper Silesia is why the same
    # name appears twice in this table: Poland holds the other half of the coalfield.
    "r_ger_silesia": 2.6,
    "r_ger_berlin": 2.4,
    "r_ger_saarland": 2.2,
    "r_ger_baden_wurttemberg": 1.5,
    "r_ger_bremen": 1.3,
    "r_ger_east_prussia": 0.5,
    "r_ger_pomerania": 0.6,
    "r_ger_mecklenburg_vorpommern": 0.6,
    # Britain. London, and the Tyne and the Midlands that built the ships and the engines.
    "r_gbr_greater_london": 3.0,
    "r_gbr_north_east": 1.8,
    "r_gbr_east_midlands": 1.6,
    "r_gbr_north_eastern": 1.4,
    "r_gbr_west_wales_and_the_valleys": 0.9,
    "r_gbr_northern_ireland": 0.6,
    "r_irl": 0.4,
    # France. Paris, the northern coalfield, and the iron of Lorraine -- the three things
    # the 1914 and 1940 campaigns were both fought across.
    "r_fra_ile_de_france": 3.0,
    "r_fra_hauts_de_france": 2.0,
    "r_fra_alsace_lorraine": 1.8,
    "r_fra_normandie": 1.0,
    "r_fra_bretagne": 0.7,
    # ...and the overseas departments, which are not French industry.
    "r_fra_guadeloupe": 0.12,
    "r_fra_guyane_francaise": 0.08,
    "r_fra_mayotte": 0.08,
    "r_fra_reunion": 0.12,
    # Italy. The Milan-Turin-Genoa triangle is the country's industry; the south is not,
    # and the empire is emphatically not.
    "r_ita_lombardia": 3.2,
    "r_ita_liguria": 2.0,
    "r_ita_veneto": 1.4,
    "r_ita_toscana": 1.2,
    "r_ita_lazio": 1.4,
    "r_ita_calabria": 0.4,
    "r_ita_valle_d_aosta": 0.4,
    # The Soviet Union. Moscow, the Urals, the Donbass-adjacent Ukrainian industry -- and
    # the enormous emptiness that a land-area model would have made the richest ground on
    # the map.
    "r_sov_rus_central": 3.0,
    "r_sov_rus_urals": 2.2,
    "r_sov_ukr_kharkiv": 2.0,
    "r_sov_ukr_kiev_city": 1.6,
    "r_sov_blr_minsk": 1.0,
    "r_sov_rus_siberian": 0.35,
    "r_sov_rus_far_eastern": 0.3,
    "r_kaz": 0.3,
    "r_uzb": 0.3,
    "r_tkm": 0.2,
    "r_tjk": 0.15,
    "r_kgz": 0.15,
    "r_aze": 0.9,  # Baku, which is most of what the Caucasus is worth in 1936
    "r_geo": 0.4,
    "r_arm": 0.3,
    # Poland. The Polish half of Upper Silesia and the Lodz mills are the state's
    # industry; the eastern borderlands are not.
    "r_pol_silesian": 3.0,
    "r_pol_odz": 2.0,
    "r_pol_masovian": 1.8,
    "r_pol_eastern_borderlands": 0.3,
    "r_pol_podlachian": 0.4,
    "r_pol_polish_corridor": 1.1,
    # Czechoslovakia. Prague, and the Sudeten border industry that made 1938 worth doing.
    "r_cze_cze_prague": 2.6,
    "r_cze_sudetenland": 2.2,
    "r_cze_cze_jihomoravsky": 1.2,
    "r_cze_svk_kosicky": 0.5,
    "r_cze_svk_banskobystricky": 0.5,
    # Austria, Hungary, Spain: one hub each and a thin remainder.
    "r_aut_wien": 3.0,
    "r_aut_oberosterreich": 1.1,
    "r_aut_tirol": 0.5,
    "r_aut_vorarlberg": 0.5,
    "r_hun_central_hungary": 2.6,
    "r_esp_madrid": 2.4,
    "r_esp_valenciana": 1.4,
    "r_esp_asturias": 1.3,
    "r_esp_canary_is": 0.3,
    "r_esp_melilla": 0.15,
    # Italian East Africa, and Ethiopia. The point of the Abyssinian war was never money.
    "r_m_eri_maekel": 0.3,
    "r_m_som_banaadir": 0.3,
    "r_eth_addis_ababa": 2.0,
    "r_eth_somali": 0.3,
    "r_eth_afar": 0.3,
    "r_eth_gambela_peoples": 0.25,
    # Yugoslavia and Romania: a capital, an oilfield, and not much else.
    "r_yug_srb_grad_beograd": 2.6,
    "r_yug_svn_goriska": 1.2,
    "r_rou_rou_bucharest": 2.6,
    "r_rou_rou_gorj": 1.4,
    "r_rou_mda_leova": 0.4,
    "r_rou_mda_briceni": 0.4,
    "r_rou_mda_ocnita": 0.4,
    # Ground with no economy in it, in either era. These are here because a *minor's*
    # economy is still `4 + area * 0.02` -- the same land-area proxy this module exists to
    # get out of the provincial economy -- so Antarctica seeds an economy of 23.8 and
    # would otherwise be worth more developed ground than Bavaria. Correcting the minor
    # formula would move the `nation` fingerprint section, which this change is not
    # licensed to touch, so the four genuinely empty ones are named instead and the
    # formula is flagged in the changelog.
    "r_ata": 0.02,
    "r_grl": 0.15,
    "r_atf": 0.05,
    "r_sah": 0.4,

    # ---------------------------------------------------------------- 2026
    # China. The coastal and Yellow River manufacturing belt against the western plateau.
    "r_chn_beijing": 3.0,
    "r_chn_shandong": 2.4,
    "r_chn_liaoning": 1.8,
    "r_chn_henan": 1.8,
    "r_chn_fujian": 1.7,
    "r_chn_chongqing": 1.4,
    "r_chn_shanxi": 1.1,
    "r_chn_hainan": 0.6,
    "r_chn_guizhou": 0.6,
    "r_chn_ningxia": 0.4,
    "r_chn_xizang": 0.15,
    # Russia. Moscow is most of the Russian economy, and Kamchatka is not.
    #
    # **Regrouped when Crimea left.** These regions are clusters of oblasts named after one
    # member, and taking two shapes out of the Russian pool moved every cluster boundary --
    # so half the ids here changed even though nothing about Russia did. The weights follow
    # what the new clusters contain: Tatarstan is the Volga industrial belt, Khanty-Mansiy
    # is the oil and gas, Khakass reaches the Kuzbass, Orenburg the southern Urals.
    "r_rus_moskva": 3.4,
    "r_rus_tatarstan": 1.8,
    "r_rus_tula": 1.3,
    "r_rus_khanty_mansiy": 1.2,
    "r_rus_orenburg": 0.9,
    "r_rus_khakass": 0.7,
    "r_rus_penza": 0.7,
    "r_rus_pskov": 0.5,
    "r_rus_vologda": 0.5,
    "r_rus_kabardin_balkar": 0.3,
    "r_rus_amur": 0.25,
    "r_rus_kamchatka": 0.2,
    # India.
    "r_ind_delhi": 3.0,
    "r_ind_haryana": 1.8,
    "r_ind_andhra_pradesh": 1.5,
    "r_ind_kerala": 1.3,
    "r_ind_chhattisgarh": 0.9,
    "r_ind_himachal_pradesh": 0.4,
    "r_ind_sikkim": 0.15,
    "r_ind_nagaland": 0.2,
    "r_ind_manipur": 0.2,
    # South Korea: the Seoul capital region really is that lopsided.
    "r_kor_seoul": 3.2,
    "r_kor_incheon": 1.8,
    "r_kor_busan": 1.6,
    "r_kor_daegu": 1.2,
    "r_kor_gangwon": 0.5,
    "r_kor_jeju": 0.4,
    # Taiwan. Hsinchu is a science park that a fair part of the world economy runs on,
    # and a scenario where it is worth the same as Taitung is a scenario missing the point.
    "r_twn_taipei_city": 2.8,
    "r_twn_hsinchu_city": 2.6,
    "r_twn_kaohsiung_city": 1.8,
    "r_twn_taichung_city": 1.5,
    "r_twn_taitung": 0.3,
    "r_twn_kinmen": 0.2,
    "r_twn_nantou": 0.5,
    # Ukraine: the capital, and the industrial east that the war is being fought over --
    # which is now drawn oblast by oblast, because a line of control needs somewhere to be.
    # Donetsk and Luhansk are weighted for what the Donbas *was*: nine years of war have
    # taken most of it apart, and the engine has no way to say "worth this, and wrecked".
    "r_ukr_kiev_city": 2.8,
    "r_ukr_dnipropetrovs_k": 2.0,
    "r_ukr_kharkiv": 1.8,
    "r_ukr_donets_k": 1.6,
    "r_ukr_zaporizhzhya": 1.4,
    "r_ukr_luhans_k": 0.8,
    "r_ukr_ivano_frankivs_k": 0.6,
    "r_ukr_crimea": 0.5,
    "r_ukr_khmel_nyts_kyy": 0.5,
    "r_ukr_kherson": 0.4,
    # North Korea: a capital that is the state, and provinces that are not.
    "r_prk_p_yongyang": 3.4,
    "r_prk_p_yongan_namdo": 1.0,
    "r_prk_rason": 0.8,
    "r_prk_ryanggang": 0.2,
    "r_prk_chagang_do": 0.25,
    # Pakistan.
    "r_pak_punjab": 2.6,
    "r_pak_sind": 2.2,
    "r_pak_f_c_t": 1.4,
    "r_pak_baluchistan": 0.3,
    "r_pak_northern_areas": 0.15,
    "r_pak_azad_kashmir": 0.3,
    "r_pak_f_a_t_a": 0.3,
}


def _own_ground(region_id: str, owner: str) -> bool:
    """Whether this province is the owner's own, by the id convention scenarios use."""
    return region_id == f"r_{owner}" or region_id.startswith(f"r_{owner}_")


def region_weights(
    regions: list[RegionSeed], nations: list[NationSeed]
) -> tuple[dict[str, float], dict[str, float]]:
    """How much of its nation each province is worth, and each nation's total.

    Factored out because **population and economic weight are the same distribution**.
    Beijing, Shandong and Henan are both the rich provinces and the populous ones; Tibet
    and Kamchatka are neither. Sharing one table means the two can never disagree about
    where a country's substance is, and means a correction to the heartland table fixes
    both at once. Only the national total differs -- see :mod:`atlas.scenarios.demography`.
    """
    capitals = {
        nation.capital_region_id for nation in nations if nation.capital_region_id
    }
    weights: dict[str, float] = {}
    total_weight: dict[str, float] = {}
    for region in regions:
        if region.kind == "ocean" or not region.owner_nation_id:
            continue
        weight = HEARTLANDS.get(region.id)
        if weight is None:
            if region.id in capitals:
                weight = CAPITAL_WEIGHT
            elif _own_ground(region.id, region.owner_nation_id):
                weight = 1.0
            else:
                weight = PERIPHERAL_WEIGHT
        weights[region.id] = weight
        total_weight[region.owner_nation_id] = (
            total_weight.get(region.owner_nation_id, 0.0) + weight
        )
    return weights, total_weight


def assign_development(
    regions: list[RegionSeed], nations: list[NationSeed]
) -> list[RegionSeed]:
    """Distribute each nation's economic weight across the ground it holds.

    Normalised per nation, which is the property that makes this safe to change a scenario
    around: whatever the heartland table says, a nation's provinces total
    ``economy * DEV_PER_ECONOMY``, so adding a multiplier redistributes wealth inside a
    country and never invents any. It also means a power cut into twelve provinces and one
    left whole are worth the same, which they were not before.

    Ocean is skipped -- there is nothing to develop -- and a region whose owner is not a
    seeded nation keeps the default, since there is no economy to divide.
    """
    economy_of = {
        nation.id: float(nation.economy or 0.0) for nation in nations
    }
    weights, total_weight = region_weights(regions, nations)

    out: list[RegionSeed] = []
    for region in regions:
        weight = weights.get(region.id)
        owner = region.owner_nation_id
        if weight is None or owner is None:
            out.append(region)
            continue
        pool = economy_of.get(owner, 0.0) * DEV_PER_ECONOMY
        share = total_weight.get(owner) or 1.0
        out.append(
            replace(region, development=round(max(FLOOR, pool * weight / share), 2))
        )
    return out
