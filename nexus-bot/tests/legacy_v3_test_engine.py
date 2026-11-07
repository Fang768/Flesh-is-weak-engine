"""规则引擎单元测试。

全部用 ScriptedRoller 注入骰子，不依赖随机数。
"""

from __future__ import annotations

import pytest

from cyberpunk import (
    Armor,
    Character,
    DamageType,
    DV,
    HitLocation,
    Modifier,
    RandomRoller,
    ScriptedRoller,
    apply_damage,
    death_save,
    derived,
    opposed_check,
    resolve_check,
)
from cyberpunk.stats import STAT_ORDER, validate_stats

SOLO = {
    "INT": 4,
    "REF": 7,
    "TECH": 3,
    "DEX": 7,
    "COOL": 5,
    "WILL": 4,
    "STR": 5,
    "CON": 4,
    "MOVE": 4,
    "BODY": 7,
}


# ---------- 派生属性 ----------


def test_derived_formulas_match_book():
    d = derived(SOLO)
    assert d.luck == 5  # (4+7)//2
    assert d.hum == 40  # WILL×10
    assert d.rec == 4  # (5+4)//2
    assert d.end == 8  # CON×2
    assert d.res == 12  # WILL×3
    assert d.run == 8  # MOVE×2
    assert d.spr == 12  # MOVE×3
    assert d.swim == 4
    assert d.leap == 4
    assert d.hits == 14  # BODY×2
    assert d.stun == 35  # BODY×5
    assert d.sd == 4  # CON


def test_derived_rounds_down():
    stats = dict(SOLO, INT=5, REF=4)  # (5+4)//2 = 4.5 → 4
    assert derived(stats).luck == 4


def test_stat_range_is_enforced():
    validate_stats(SOLO)
    with pytest.raises(ValueError):
        validate_stats(dict(SOLO, BODY=11))
    with pytest.raises(ValueError):
        validate_stats(dict(SOLO, BODY=1))


# ---------- 检定 ----------


def test_check_success_and_margin():
    r = ScriptedRoller([7])
    res = resolve_check(r, stat=7, skill=6, dv=DV.EVERYDAY)
    assert res.total == 20
    assert res.success is True
    assert res.margin == 6
    assert res.natural == 7


def test_tie_counts_as_success_in_v3():
    # 6 + 5 + 3 = 14 == DV14 → 平局算成功（与 RED Easy Mode 相反）
    res = resolve_check(ScriptedRoller([6]), stat=5, skill=3, dv=DV.EVERYDAY)
    assert res.total == 14
    assert res.success is True


def test_natural_ten_explodes_once():
    res = resolve_check(ScriptedRoller([10, 7]), stat=4, skill=2, dv=DV.HARD)
    assert res.rolls == [10, 7]
    assert res.total == 10 + 7 + 4 + 2
    assert res.open_ended is True


def test_natural_one_subtracts_and_can_fumble():
    # 1 − 6 + 4 + 2 = 1 ≥ 0 → 失败但不是 fumble
    res = resolve_check(ScriptedRoller([1, 6]), stat=4, skill=2, dv=DV.EVERYDAY)
    assert res.total == 1
    assert res.success is False
    assert res.fumble is False

    # 1 − 6 + 2 + 1 = −2 < 0 → fumble
    res = resolve_check(ScriptedRoller([1, 6]), stat=2, skill=1, dv=DV.EASY)
    assert res.total == -2
    assert res.fumble is True


def test_modifiers_are_summed():
    res = resolve_check(
        ScriptedRoller([8]),
        stat=5,
        skill=4,
        dv=DV.CHALLENGING,
        modifiers=[Modifier.COVER, Modifier.SMOKE],
    )
    assert res.total == 8 + 5 + 4 - 1 - 4
    assert res.success is False


def test_opposed_ties_go_to_defender():
    assert opposed_check(15, 14) is True
    assert opposed_check(15, 15) is False
    assert opposed_check(14, 15) is False


def test_describe_is_human_readable():
    res = resolve_check(ScriptedRoller([7]), stat=7, skill=6, purpose="开锁")
    text = res.describe()
    assert "开锁" in text and "DV14" in text and "成功" in text


# ---------- 伤害 ----------


def test_head_hit_triples_damage():
    result = apply_damage(5, hit=HitLocation.HEAD)
    assert result.total == 15
    assert result.penetrated == 15


def test_vitals_double_torso_single():
    assert apply_damage(5, hit=HitLocation.VITALS).total == 10
    assert apply_damage(5, hit=HitLocation.TORSO).total == 5
    assert apply_damage(5, hit=HitLocation.LIMB).total == 5


def test_armor_absorbs_and_wears_down():
    armor = Armor("Kevlar 轻夹克", sp=14)
    result = apply_damage(3, hit=HitLocation.TORSO, armor=armor)
    assert result.absorbed == 3
    assert result.penetrated == 0
    assert result.sp_after == 13  # 挡住一次就 SP−1


def test_ap_rounds_halve_sp():
    armor = Armor("Kevlar 轻夹克", sp=14)
    result = apply_damage(10, hit=HitLocation.TORSO, armor=armor, ap=True)
    assert result.absorbed == 7  # 14//2
    assert result.penetrated == 3


def test_melee_edged_also_halves_sp():
    armor = Armor("Flack 背心", sp=20)
    result = apply_damage(10, hit=HitLocation.TORSO, armor=armor, melee=True)
    assert result.absorbed == 10


def test_stun_damage_goes_to_stun_pool():
    hero = Character("测试", SOLO, armor=Armor("无", 0))
    before = hero.hits
    result = apply_damage(4, damage_type=DamageType.STUN)
    hero.take_damage(result)
    assert hero.hits == before
    assert hero.stun == hero.derived.stun - 4


def test_damage_never_goes_negative():
    hero = Character("脆皮", dict(SOLO, BODY=2))
    result = apply_damage(99, hit=HitLocation.HEAD)
    hero.take_damage(result)
    assert hero.hits == 0
    assert hero.is_dead is True


# ---------- 死亡豁免 ----------


def test_death_save_natural_ten_always_fails():
    alive, roll = death_save(ScriptedRoller([10]), sd=9)
    assert roll == 10 and alive is False


def test_death_save_compares_to_con_with_penalty():
    assert death_save(ScriptedRoller([4]), sd=5)[0] is True
    assert death_save(ScriptedRoller([5]), sd=5)[0] is True
    assert death_save(ScriptedRoller([5]), sd=5, penalty=1)[0] is False


# ---------- 角色卡 ----------


def test_character_starts_at_full_pools():
    hero = Character("黎明", SOLO)
    assert hero.hits == 14 and hero.stun == 35


def test_streetdeal_boosts_cool():
    hero = Character("门路", SOLO, perks={"streetdeal": 3})
    assert hero.effective_stat("COOL") == 8
    assert hero.effective_stat("REF") == 7


def test_beautiful_boosts_social_skills_per_level():
    hero = Character("美人", SOLO, skills={"说服": 4}, perks={"beautiful": 2})
    assert hero.effective_skill("说服") == 6
    assert hero.effective_skill("手枪") == 0


def test_character_check_uses_effective_values():
    hero = Character("美人", SOLO, skills={"说服": 4}, perks={"beautiful": 2})
    res = hero.check(ScriptedRoller([5]), stat="COOL", skill="说服", dv=DV.EVERYDAY)
    assert res.skill == 6
    assert res.stat == 5


def test_rest_recovers_rec_points_and_caps():
    hero = Character("伤者", SOLO)
    hero.hits = 5
    hero.rest()
    assert hero.hits == 9  # +REC(4)
    hero.rest(); hero.rest(); hero.rest()
    assert hero.hits == 14  # 不超过上限


def test_random_roller_is_seeded_and_in_range():
    a = RandomRoller(42)
    b = RandomRoller(42)
    seq_a = [a.roll(10) for _ in range(20)]
    seq_b = [b.roll(10) for _ in range(20)]
    assert seq_a == seq_b
    assert all(1 <= v <= 10 for v in seq_a)


def test_scripted_roller_exhaustion_is_loud():
    r = ScriptedRoller([3])
    r.roll(10)
    with pytest.raises(AssertionError):
        r.roll(10)
