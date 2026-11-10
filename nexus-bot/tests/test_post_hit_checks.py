"""命中后检定通道：特殊弹种 + 附毒。

规则依据（``rules/red_core/raw/full.txt``）：

| 弹种 | 检定 | DV | 失败 | 原文 |
|---|---|---|---|---|
| 生物毒素弹 | 折磨&药物耐受 | 15 | 直接 3D6 | `:26372-26380` |
| 毒气弹 | 折磨&药物耐受 | 13 | 直接 2D6 | `:26442-26450` |
| 麻醉弹 | 折磨&药物耐受 | 13 | 倒地失意识 1 分钟 | `:26463-26472` |
| 催泪弹 | 折磨&药物耐受 | 13 | 眼部受损 1 分钟 | `:26503-26509` |
| 闪光弹 | 折磨&药物耐受 | 15 | 眼部+耳朵受损 1 分钟 | `:26419-26428` |
| EMP 弹 | 赛博技术 | 15 | 2 件赛博组件失效 1 分钟 | `:26383-26393` |

关键口径：

* 「攻击不造成任何伤害」——但**命中判定照常做**（书里说「被你的攻击打到」）。
* 伤害是**直接 HP**：「护甲不会被削减，因为它不会与之产生交互」。
* 「打到肉的人」（FAQ `:698-700`）：无人机与完全赛博格不算肉。
* 1 分钟 = 20 轮（`:11327-11331` 一回合 3 秒）。
* 吸血鬼尖牙附毒走毒素表 `:15358-15367`（轻 11 / 强 13 / 致命 15）。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import db, growth, roster
from cyberpunk import ammo_effect as ae, dice

GID = "qq:posthit"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _setup(conn, cyber_items=(), ammo="", qq="u1", enemy="打手"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = {"cyber": list(cyber_items)}
    roster.save(conn, GID, qq, card)
    combat = C.start(conn, GID, enemies=((enemy, 1),),
                     roller=dice.RandomRoller())
    if ammo:
        me = next(c for c in combat.combatants if c.side == "pc")
        me.ammo_type = ammo
        me.ammo = me.mag = 10
        C._save(conn, combat)
    # 没地图时要先给距离，否则会返回「距离不明」
    C.close_range(conn, GID, qq, "距离 5 米")
    return C.load(conn, GID)


def _get(conn, side):
    return next(c for c in C.load(conn, GID).combatants if c.side == side)


def _shoot(conn, ammo, *, hit=6, check=6, target="打手", skill="手枪"):
    """开一枪。

    骰子只给 1–6 的值（``loop=True``）：因为 d10 命中骰和 d6 伤害骰混在
    同一个序列里，给 10 会落到 d6 上炸掉。命中与否用 ``hit``（1–6）配合
    5 米距离的 DV13 控制——独狼 REF7，掷 6 就是 13，刚好达到 DV（>= 即成功）。
    """
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    me.ammo_type = ammo
    me.ammo = me.mag = 10
    C._save(conn, fight)
    return C.attack(conn, GID, "u1", target, skill=skill,
                    roller=dice.ScriptedRoller([hit, check, 3], loop=True))


# ---------- 数据表 ----------


def test_six_check_ammo_types_registered():
    for name in ("生物毒素弹", "毒气弹", "麻醉弹", "催泪弹", "闪光弹", "EMP 弹"):
        assert ae.is_check_ammo(name), name


def test_smoke_is_not_a_check_ammo():
    assert not ae.is_check_ammo("烟雾弹")


def test_dvs_match_the_book():
    assert ae.spec_for("生物毒素弹")["dv"] == 15
    assert ae.spec_for("毒气弹")["dv"] == 13
    assert ae.spec_for("麻醉弹")["dv"] == 13
    assert ae.spec_for("催泪弹")["dv"] == 13
    assert ae.spec_for("闪光弹")["dv"] == 15
    assert ae.spec_for("EMP 弹")["dv"] == 15


def test_check_skills_match_the_book():
    assert ae.spec_for("生物毒素弹")["check"] == "折磨&药物耐受"
    assert ae.spec_for("EMP 弹")["check"] == "赛博技术"


def test_rounds_per_minute_is_twenty():
    assert ae.ROUNDS_PER_MINUTE == 20


def test_fail_effects():
    assert ae.spec_for("生物毒素弹")["on_fail"]["damage"] == "3D6"
    assert ae.spec_for("毒气弹")["on_fail"]["damage"] == "2D6"
    assert ae.spec_for("麻醉弹")["on_fail"]["status"] == "unconscious"
    assert "eye_damage" in ae.spec_for("催泪弹")["on_fail"]["injury"]
    assert set(ae.spec_for("闪光弹")["on_fail"]["injury"]) == {"eye_damage", "ear_damage"}
    assert ae.spec_for("EMP 弹")["on_fail"]["disable_cyberware"] == 2


def test_injury_keys_exist_in_crit_table():
    from cyberpunk import crit

    for name in ("催泪弹", "闪光弹"):
        for key in ae.spec_for(name)["on_fail"]["injury"]:
            assert key in crit.BY_KEY, f"{name} 的伤势 {key} 不存在"


# ---------- 命中判定照常 ----------


def test_miss_deals_nothing(conn):
    _setup(conn)
    before = _get(conn, "enemy").hp
    text = _shoot(conn, "毒气弹", hit=1)
    assert "没打中" in text
    assert _get(conn, "enemy").hp == before


def test_hit_does_no_ordinary_damage(conn):
    """命中后不走「伤害减护甲」的常规流程。"""
    _setup(conn)
    text = _shoot(conn, "毒气弹", hit=6, check=1)
    assert "不造成常规伤害" in text


# ---------- 检定通过 / 失败 ----------


def test_check_success_means_no_effect(conn):
    _setup(conn)
    before = _get(conn, "enemy").hp
    text = _shoot(conn, "毒气弹", hit=6, check=6)
    assert "扛住了" in text
    assert _get(conn, "enemy").hp == before


def test_check_failure_deals_direct_damage(conn):
    _setup(conn)
    before = _get(conn, "enemy").hp
    text = _shoot(conn, "毒气弹", hit=6, check=1)
    assert "失败" in text and "不吃护甲" in text
    assert _get(conn, "enemy").hp < before


def test_direct_damage_ignores_armor(conn):
    """书里：「护甲不会被削减，因为它不会与之产生交互」。"""
    _setup(conn)
    before_sp = _get(conn, "enemy").effective_sp
    _shoot(conn, "毒气弹", hit=6, check=1)
    assert _get(conn, "enemy").effective_sp == before_sp


def test_biological_toxin_uses_3d6(conn):
    _setup(conn)
    text = _shoot(conn, "生物毒素弹", hit=6, check=1)
    assert "3D6" in text


# ---------- 状态类效果 ----------


def test_anesthetic_knocks_unconscious(conn):
    _setup(conn)
    text = _shoot(conn, "麻醉弹", hit=6, check=1)
    assert "失去意识" in text
    assert _get(conn, "enemy").unconscious_until > 0


def test_tear_gas_inflicts_eye_damage(conn):
    _setup(conn)
    text = _shoot(conn, "催泪弹", hit=6, check=1)
    assert "眼睛受损" in text


def test_flashbang_inflicts_both_injuries(conn):
    _setup(conn)
    text = _shoot(conn, "闪光弹", hit=6, check=1)
    assert "眼睛受损" in text and "耳朵受损" in text


def test_emp_disables_cyberware(conn):
    _setup(conn)
    text = _shoot(conn, "EMP 弹", hit=6, check=1)
    assert "赛博组件失效" in text


# ---------- 「打到肉」与免疫 ----------


def test_mechanical_targets_are_immune(conn):
    """FAQ `:698-700`：无人机与完全赛博格不视为肉类。"""
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    C.start(conn, GID, enemies=(("自动炮塔", 1),), roller=dice.RandomRoller())
    C.close_range(conn, GID, "u1", "距离 5 米")
    text = _shoot(conn, "毒气弹", hit=6, check=1, target="自动炮塔")
    assert "不是血肉之躯" in text


def test_nasal_filter_blocks_tear_gas_immunity(conn):
    """鼻滤器免毒气（immunity.json）；催泪弹按毒气档处理。"""
    from cyberpunk import immunity

    card = {"state": {"cyber": ["鼻滤器"]}}
    assert immunity.is_immune(card, "毒气") == "鼻滤器"


def test_immunity_category_mapping():
    assert ae.immunity_for("毒气弹") == "毒气"
    assert ae.immunity_for("闪光弹") == "闪光"
    assert ae.immunity_for("EMP 弹") == "EMP"


# ---------- 吸血鬼尖牙附毒 ----------


def test_fangs_carry_poison_flag():
    from cyberpunk import cyber_weapon

    assert cyber_weapon.item_rule("吸血鬼尖牙")["poison"] is True


def test_poison_of_returns_grade():
    from cyberpunk import cyber_weapon

    assert cyber_weapon.poison_of({"state": {"cyber": ["吸血鬼尖牙"]}}) == "强"


def test_poison_of_empty_without_fangs():
    from cyberpunk import cyber_weapon

    assert cyber_weapon.poison_of({"state": {"cyber": ["赛博大蛇"]}}) == ""


def test_fangs_hit_applies_poison(conn):
    _setup(conn, cyber_items=["神经链路", "吸血鬼尖牙"])
    before = _get(conn, "enemy").hp
    # 攻击命中 + 伤害骰 + 毒素检定失败 + 毒素伤害骰
    for _ in range(40):
        fight = C.load(conn, GID)
        if fight is None or not fight.active:
            C.start(conn, GID, enemies=(("打手", 1),), roller=dice.RandomRoller())
        text = C.attack(conn, GID, "u1", "打手", skill="近战武器",
                        roller=dice.ScriptedRoller([6, 1, 3], loop=True))
        if "附毒" in text:
            assert "中毒！" in text or "抗住了" in text
            return
    pytest.fail("多次尝试都没触发附毒")


def test_poison_does_not_apply_without_fangs(conn):
    _setup(conn, cyber_items=["赛博大蛇"])
    for _ in range(20):
        fight = C.load(conn, GID)
        if fight is None or not fight.active:
            C.start(conn, GID, enemies=(("打手", 1),), roller=dice.RandomRoller())
        text = C.attack(conn, GID, "u1", "打手", skill="近战武器",
                        roller=dice.ScriptedRoller([6, 3], loop=True))
        assert "附毒" not in text


# ---------- 文案 ----------


def test_on_fail_descriptions_are_readable():
    assert "3D6" in ae.on_fail_description("生物毒素弹")
    assert "失去意识" in ae.on_fail_description("麻醉弹")
    assert "眼睛受损" in ae.on_fail_description("催泪弹")
    assert "赛博组件失效" in ae.on_fail_description("EMP 弹")
