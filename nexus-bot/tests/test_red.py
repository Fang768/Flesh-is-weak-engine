"""RED（赛博朋克红核心规则）引擎测试。

数值全部取自核心书：
* HP 表 p79（书内页）
* 伤势三档 p220
* 严重伤势 p190（伤害骰两个 6 → 追加 5 点）
* 死亡豁免 p222
"""

from __future__ import annotations

import pytest

from cyberpunk import Armor, Character, DV, HitLocation, ScriptedRoller, apply_damage
from cyberpunk.stats import STAT_ORDER, derived, validate_stats

BASE = {
    "INT": 6,
    "REF": 7,
    "DEX": 7,
    "TECH": 6,
    "COOL": 5,
    "WILL": 6,
    "LUCK": 7,
    "MOVE": 6,
    "BODY": 6,
    "EMP": 5,
}


def test_stat_order_is_red():
    assert STAT_ORDER == ("INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP")
    validate_stats(BASE)
    with pytest.raises(ValueError):
        validate_stats({**{k: v for k, v in BASE.items() if k != "EMP"}, "STR": 5})


@pytest.mark.parametrize(
    "body,will,hp",
    [
        (2, 2, 20),   # 10 + 5×2
        (3, 4, 30),   # 平均 3.5 → ⌈3.5⌉=4
        (6, 6, 40),   # 10 + 5×6
        (7, 7, 45),
        (10, 10, 60),
        (2, 15, 55),  # 平均 8.5 → 9
    ],
)
def test_hp_formula_matches_book_table(body, will, hp):
    assert derived({**BASE, "BODY": body, "WILL": will}).hp == hp


def test_derived_values():
    d = derived(BASE)
    assert d.hp == 40
    assert d.wound == 20  # ⌈40/2⌉
    assert d.death_save == 6  # = BODY
    assert d.hum == 50  # EMP×10
    assert d.luck == 7  # = LUCK
    assert d.run == 12  # MOVE×2
    assert d.leap == 6


def test_hp_override_for_cards_and_npcs():
    d = derived({**BASE, "BODY": 3}, hp=40)
    assert d.hp == 40 and d.wound == 20


def test_wound_states_and_penalties():
    hero = Character("阿七", BASE)
    assert hero.wound_state == "轻伤" and hero.wound_penalty == 0

    hero.hp = 19  # < 重伤阈值 20
    assert hero.wound_state == "重伤" and hero.wound_penalty == -2

    hero.hp = 0  # < 1 → 致命伤
    assert hero.wound_state == "致命伤" and hero.wound_penalty == -4
    assert hero.move_now == 6  # 12 − 6


def test_wound_penalty_enters_check():
    hero = Character("阿七", BASE, skills={"觉察": 5})
    hero.hp = 10
    res = hero.check(ScriptedRoller([5]), stat="INT", skill="觉察", dv=DV.EVERYDAY)
    assert res.total == 6 + 5 + 5 - 2  # 属性+技能+骰−重伤 2


def test_damage_subtracts_armor_before_location_multiplier():
    armor = Armor("凯夫拉", sp=7)
    res = apply_damage(10, hit=HitLocation.HEAD, armor=armor)
    assert res.absorbed == 7
    assert res.penetrated == (10 - 7) * 2  # 减甲之后 ×2
    assert armor.sp == 6  # 挡一次 −1


def test_armor_halved_for_ap_and_melee():
    assert apply_damage(10, armor=Armor("甲", sp=8), ap=True).penetrated == 6
    assert apply_damage(10, armor=Armor("甲", sp=8), melee=True).penetrated == 6


def test_critical_injury_extra_damage_ignores_armor():
    armor = Armor("甲", sp=6)
    res = apply_damage(4, hit=HitLocation.TORSO, armor=armor, critical_extra=True)
    assert res.penetrated == 5  # 4 被甲吃光，追加 5 点照打


def test_death_save_uses_body_and_penalty():
    hero = Character("阿七", BASE)  # BODY 6
    ok, roll = hero.death_save(ScriptedRoller([5]))
    assert (ok, roll) == (True, 5)
    assert hero.death_penalty == 1
    ok, _ = hero.death_save(ScriptedRoller([5]))  # 有效体格 6−1=5，5 < 5 不成立
    assert ok is False
    assert hero.death_penalty == 2


def test_natural_ten_always_fails():
    hero = Character("铁人", {**BASE, "BODY": 10})
    ok, roll = hero.death_save(ScriptedRoller([10]))
    assert (ok, roll) == (False, 10)


def test_humanity_loss_lowers_emp():
    hero = Character("阿七", BASE)
    assert hero.humanity == 50
    hero.lose_humanity(12)
    assert hero.humanity == 38
    assert hero.stats["EMP"] == 3  # 38 → 3


def test_rest_recovers_body_hp():
    hero = Character("阿七", BASE)
    hero.hp = 10
    hero.rest()
    assert hero.hp == 10 + 6


def test_heal_caps_at_max():
    hero = Character("阿七", BASE)
    hero.heal(999)
    assert hero.hp == hero.max_hp == 40
