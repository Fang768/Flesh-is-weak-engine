"""简易卡按 RED 口径重做的回归测试。

背景：简易书 p37–46 的 5 张预设卡 + 1 张自制卡，原先把职业能力/义体加成
**烘焙**进了技能等级（搬运工觉察 8 = 6+2、火炬电子 10 = 6+4），导致
「简易卡不吃义体加成」看起来像 bug——其实吃过，只是吃在了卡面。

本轮改成：卡面存**基础值**，加成走 ``ability_bonus`` 字段与 ``cyber_bonus``
运行时加，数值结果不变，但加成来源可算、可查、不会重复叠加。

规则依据：
* HP 公式 ``10 + 5×⌈(BODY+WILL)/2⌉``（``full.txt:79-80`` 页的派生属性）
* 属性 2–8（``full.txt:6525``「一个属性不能大于8 或者小于2」）
* 技能不高于 6（``full.txt:2861``「都不能超过6 级」）
* 职业能力见简易书 p37–46
"""

from __future__ import annotations

import pytest

from cyberpunk import easy, skills

ALL = {t["name"]: t for t in easy.templates()}


def test_six_templates_still_present():
    assert set(ALL) == {"肆拾", "搬运工", "火炬", "赤尾", "24/7", "清道夫"}


# ---------- HP 与属性合规 ----------


@pytest.mark.parametrize("name", list(ALL))
def test_hp_matches_red_formula(name):
    card = ALL[name]
    body = card["stats"]["BODY"]
    will = card["stats"]["WILL"]
    expected = 10 + 5 * (-(-(body + will) // 2))
    assert card["hp"] == expected, f"{name} 的 HP 与 RED 公式不符"


@pytest.mark.parametrize("name", list(ALL))
def test_attributes_within_starting_range(name):
    for stat, value in ALL[name]["stats"].items():
        assert 2 <= value <= 8, f"{name} 的 {stat}={value} 越界"


@pytest.mark.parametrize("name", list(ALL))
def test_no_skill_exceeds_six(name):
    """剥离烘焙之后，卡面基础值必须全部 ≤6（书：技能不能超过 6 级）。"""
    over = {k: v for k, v in ALL[name]["skills"].items() if v > 6}
    assert over == {}, f"{name} 还有超 6 的技能：{over}"


# ---------- 技能名统一到 RED ----------


@pytest.mark.parametrize("name", list(ALL))
def test_all_skills_are_red_canonical(name):
    for skill in ALL[name]["skills"]:
        assert skills.stat_of(skill) is not None, f"{name} 的技能「{skill}」在 RED 表里查不到"
        assert skills.canonical(skill) == skill, f"{name} 的技能「{skill}」不是 RED 正典名"


# ---------- 义体名统一到 HL 表 ----------


@pytest.mark.parametrize("name", list(ALL))
def test_all_cyberware_names_are_known(name):
    from cyberpunk import cyber

    for item in ALL[name]["cyber"]:
        assert cyber.hl_of(item) is not None, f"{name} 的义体「{item}」不在 HL 表里"


# ---------- ability_bonus 字段 ----------


def test_banyungong_bakes_nothing_and_gets_initiative_bonus():
    card = ALL["搬运工"]
    # 图像增强 +2 已从觉察/藏匿里剥离
    assert card["skills"]["觉察"] == 6
    assert card["skills"]["藏匿&揭示物体"] == 6
    assert card["ability_bonus"] == {"先攻": 4}
    bonus, notes = easy.ability_bonus(card, initiative=True)
    assert bonus == 4 and notes


def test_huoju_bakes_nothing_and_gets_skill_bonus():
    card = ALL["火炬"]
    assert card["skills"]["电子/安全技术"] == 6      # 10 → 6
    assert card["ability_bonus"] == {"电子/安全技术": 4}
    bonus, notes = easy.ability_bonus(card, skill="电子/安防技术")  # 别名也认
    assert bonus == 4


def test_ability_bonus_does_not_leak_to_other_skills():
    card = ALL["火炬"]
    assert easy.ability_bonus(card, skill="手枪") == (0, [])


def test_moves_without_ability_bonus():
    assert ALL["肆拾"]["ability_bonus"] == {}
    assert easy.ability_bonus(ALL["肆拾"], skill="手枪") == (0, [])


# ---------- 剥离后的净效果：装义体仍能拿回 +2 ----------


def test_banyungong_awareness_recovers_via_cyberware():
    """搬运工装了「图像增强」（模板自带的 2 次），觉察回到 8（6 + 2）。

    关键：``full.txt:9698-9699``「除非另有说明，否则多次安装相同赛博组件的
    增益不会叠加」——所以强制双眼装两次的**加成只算一次**（+2），
    不是 +4。双眼装两次是为了「两只眼都改造才能用」，不是为了双倍加成。
    """
    from cyberpunk import cyber_bonus

    card = dict(ALL["搬运工"])
    assert card["cyber"].count("图像增强") == 2
    bonus, notes = cyber_bonus.check_bonus(card, skill="觉察")
    assert bonus == 2, f"同件义体不叠加，应 +2，实得 {bonus}"
    assert card["skills"]["觉察"] + bonus == 8, "卡面 6 + 2 = 8，与旧卡一致"


def test_banyungong_duo_does_not_double_count():
    """顶层与 state 两处都有时不会算成 2 份（顶层优先）。"""
    from cyberpunk import cyber_bonus

    card = dict(ALL["搬运工"])
    card["state"] = {"cyber": ["图像增强"]}   # 故意再塞一份到 state
    base = card["skills"]["觉察"]
    for _ in range(3):
        bonus, _ = cyber_bonus.check_bonus(card, skill="觉察")
        assert bonus == 2, f"应恒为 +2，实得 {bonus}"
        assert base + bonus == 8


def test_huoju_skill_plus_cyberware_are_separate_sources():
    """火炬的电子是「职业能力 +4」，义体加成不参与；两者互不重复。"""
    from cyberpunk import cyber_bonus

    card = dict(ALL["火炬"])
    card["state"] = {"cyber": []}
    ability, _ = easy.ability_bonus(card, skill="电子/安全技术")
    cyber, _ = cyber_bonus.check_bonus(card, skill="电子/安全技术")
    assert ability == 4
    assert cyber == 0
    assert card["skills"]["电子/安全技术"] + ability == 10
