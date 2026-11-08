"""义体检定型加成（cyber_bonus）的测试。

数据表 ``rules/red_core/data/cyberware_bonus.json`` 以 RED 义体中文名（HL 表键）
为 key，``card["state"]["cyber"]`` 存的就是这个名字（安装时由 cyber_flow 写入）。
"""

from __future__ import annotations

from cyberpunk import cyber_bonus


def _mk(*cyber: str) -> dict:
    return {"state": {"cyber": list(cyber)}}


def test_microscope_gives_awareness():
    total, notes = cyber_bonus.check_bonus(_mk("显微目镜"), skill="觉察")
    assert total == 2
    assert notes[0].startswith("显微目镜")


def test_microscope_not_for_gun():
    total, _ = cyber_bonus.check_bonus(_mk("显微目镜"), skill="手枪")
    assert total == 0


def test_audio_hearing_gives_awareness():
    total, notes = cyber_bonus.check_bonus(_mk("听觉放大"), skill="觉察")
    assert total == 2
    assert notes[0].startswith("听觉放大")


def test_tone_analysis_gives_empathy_skills():
    for sk in ("察言观色", "审讯"):
        total, notes = cyber_bonus.check_bonus(_mk("语气分析"), skill=sk)
        assert total == 2
        assert notes[0].startswith("语气分析")
    # 不是察言观色/审讯的技能不生效
    total, _ = cyber_bonus.check_bonus(_mk("语气分析"), skill="觉察")
    assert total == 0


def test_stack_two_awareness():
    total, notes = cyber_bonus.check_bonus(_mk("显微目镜", "听觉放大"), skill="觉察")
    assert total == 4
    assert len(notes) == 2


def test_empty_no_bonus():
    assert cyber_bonus.check_bonus(_mk(), skill="觉察") == (0, [])


def test_unknown_cyber_ignored():
    # 赛博义眼（基础件）不在加成表里 → 静默，不报错
    total, _ = cyber_bonus.check_bonus(_mk("赛博义眼"), skill="觉察")
    assert total == 0


def test_hit_modifier_only_not_applied_in_check_batch():
    # 瞄准目镜这类"命中修正"义体，A 批检定制不生效（留给战斗加成批）
    total, _ = cyber_bonus.check_bonus(_mk("瞄准目镜"), skill="手枪")
    assert total == 0


def test_describe_lists_effected():
    lines = cyber_bonus.describe(_mk("显微目镜", "语气分析", "瞄准目镜"))
    assert any("显微目镜" in l and "+2" in l for l in lines)
    assert any("语气分析" in l and "+2" in l for l in lines)


def test_card_without_state_key():
    total, _ = cyber_bonus.check_bonus({"name": "x"}, skill="觉察")
    assert total == 0


# ── B 批：战斗命中修正（瞄准/远视目镜）────────────────────────


def test_hit_bonus_target_scope_aimed():
    # 瞄准目镜：瞄准射击（aim）时命中 +1（full.txt:28330-28331）
    total, notes = cyber_bonus.hit_bonus(_mk("瞄准目镜"), aiming=True, meters=8)
    assert total == 1 and notes[0].startswith("瞄准目镜")
    # 不瞄准 → 不加
    total, _ = cyber_bonus.hit_bonus(_mk("瞄准目镜"), aiming=False, meters=8)
    assert total == 0


def test_hit_bonus_target_scope_far():
    # 远视目镜：目标 ≥51 米才 +1（full.txt:28339）
    total, notes = cyber_bonus.hit_bonus(_mk("远视目镜"), aiming=False, meters=60)
    assert total == 1 and notes[0].startswith("远视目镜")
    total, _ = cyber_bonus.hit_bonus(_mk("远视目镜"), aiming=False, meters=30)
    assert total == 0
    # 无可靠距离 → 不加
    total, _ = cyber_bonus.hit_bonus(_mk("远视目镜"), aiming=False, meters=None)
    assert total == 0


def test_hit_bonus_no_given_cyber():
    total, _ = cyber_bonus.hit_bonus(_mk(), aiming=True, meters=99)
    assert total == 0


# ── C-1 批：永久属性加成（骨肉织绕 体格+2）──────────────────────


def test_attribute_bonus_bone():
    assert cyber_bonus.attribute_bonus(_mk("骨肉织绕")) == {"BODY": 2}


def test_attribute_bonus_no_cyber():
    assert cyber_bonus.attribute_bonus(_mk()) == {}
    # 非属性型义体不进属性加成
    assert cyber_bonus.attribute_bonus(_mk("显微目镜")) == {}


def test_character_effective_stat_applies_cyber_mod():
    from cyberpunk.characters import Character
    ch = Character(
        name="甲",
        stats={"REF": 5, "BODY": 5, "DEX": 5, "TECH": 5, "COOL": 5,
               "WILL": 4, "LUCK": 5, "MOVE": 5, "INT": 5, "EMP": 5},
        cyber_mod={"BODY": 2},
    )
    assert ch.stats["BODY"] == 5            # 不写死基础属性
    assert ch.effective_stat("BODY") == 7   # 运行时加成
    # 体格提升驱动 max_hp：10 + 5×⌈(7+4)/2⌉ = 10+30 = 40
    assert ch.max_hp == 40


def test_character_cyber_mod_clamped_to_max():
    from cyberpunk.characters import Character
    ch = Character(
        name="乙",
        stats={"REF": 5, "BODY": 10, "DEX": 5, "TECH": 5, "COOL": 5,
               "WILL": 4, "LUCK": 5, "MOVE": 5, "INT": 5, "EMP": 5},
        cyber_mod={"BODY": 2},
    )
    # 义体加成也 clamp 到 2–10，不越过 validate_stats 上限
    assert ch.effective_stat("BODY") == 10


# ── C-2 批：防御端护甲 SP（编织皮肤 SP7 / 皮下护甲 SP11）────────


def test_armor_sp_subdermal():
    assert cyber_bonus.armor_sp(_mk("皮下护甲")) == 11
    assert cyber_bonus.armor_sp(_mk("编织皮肤")) == 7


def test_armor_sp_takes_highest():
    # 不叠加：两件装一起取最高（RED 说无法与穿着护甲叠加）
    assert cyber_bonus.armor_sp(_mk("编织皮肤", "皮下护甲")) == 11


def test_armor_sp_none():
    assert cyber_bonus.armor_sp(_mk()) == 0
    # 非护甲型义体不进护甲通道
    assert cyber_bonus.armor_sp(_mk("显微目镜", "瞄准目镜")) == 0


# ── C-3 批：恢复型（抗体增强 每日恢复体格×2）─────────────────


def test_rest_multiplier_default_and_anti():
    # 默认休整恢复倍率 1；装「抗体增强」翻倍为 2（full.txt:10220-10223）
    assert cyber_bonus.rest_multiplier(_mk()) == 1
    assert cyber_bonus.rest_multiplier(_mk("抗体增强")) == 2
    # 非恢复型义体不改变倍率
    assert cyber_bonus.rest_multiplier(_mk("显微目镜", "骨肉织绕")) == 1


def test_rest_multiplier_takes_highest():
    assert cyber_bonus.rest_multiplier(_mk("抗体增强", "皮下护甲")) == 2