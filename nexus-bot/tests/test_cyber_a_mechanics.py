"""A 类义体机制（2026-11-10）：F 组合触发 / 痛觉编辑器 / 技能芯片。

规则出处 ``rules/red_core/raw/full.txt``：

* 化学皮肤 + 科技炫发 → 个人装扮（个人美容）+2（``:9726``、``:9753``）。
* 荧光纹身三个及以上 → 服饰&风格 +2（``:9737-9738``）。
* 痛觉编辑器「忽略严重伤势的负面影响」（``:27821-27825``）。
* 技能芯片：特定技能等级到达 3；已经超过 3 则无效（``:27830-27837``）。

「约定」：书里没写明按件数叠加的组合效果，一律不叠加（主人 2026-11-10 拍板）。
"""

from __future__ import annotations

import pytest

from core import combat as C
from cyberpunk import cyber_bonus

GID = "qq:aclass2"


def _model(*cyber: str) -> dict:
    return {"state": {"cyber": list(cyber)}, "skills": {"黑客": 1}}


# ── F 类组合触发 ──────────────────────────────────────────


def test_combo_web_and_hair_gives_grooming():
    card = _model("化学皮肤", "科技炫发")
    total, notes = cyber_bonus.check_bonus(card, skill="个人装扮")
    assert total == 2
    assert any("化学皮肤+科技炫发" in n for n in notes)


def test_combo_only_one_side_no_effect():
    total, _ = cyber_bonus.check_bonus(_model("化学皮肤"), skill="个人装扮")
    assert total == 0


def test_combo_web_and_hair_not_stacking_twice():
    # 化学皮肤 + 科技炫发各一件，书里是同一个 +2 的双向表述 → 只加一次
    card = _model("化学皮肤", "科技炫发")
    total, notes = cyber_bonus.check_bonus(card, skill="个人装扮")
    assert total == 2
    assert len([n for n in notes if "化学皮肤+科技炫发" in n]) == 1


def test_combo_tattoo_three_or_more():
    card = _model("荧光纹身", "荧光纹身", "荧光纹身")
    total, notes = cyber_bonus.check_bonus(card, skill="服饰&风格")
    assert total == 2
    assert any("荧光纹身" in n for n in notes)


def test_combo_tattoo_two_not_enough():
    card = _model("荧光纹身", "荧光纹身")
    total, _ = cyber_bonus.check_bonus(card, skill="服饰&风格")
    assert total == 0


def test_combo_tattoo_six_not_stacking():
    # 书里「三个甚至更多」→ 达到即止，不按 3→6 叠加
    card = _model(*(["荧光纹身"] * 6))
    total, _ = cyber_bonus.check_bonus(card, skill="服饰&风格")
    assert total == 2


def test_combo_wrong_skill_no_effect():
    card = _model("荧光纹身", "荧光纹身", "荧光纹身")
    total, _ = cyber_bonus.check_bonus(card, skill="枪械")
    assert total == 0


def test_combo_describe_lists_it():
    lines = cyber_bonus.describe(_model("化学皮肤", "科技炫发"))
    assert any("化学皮肤+科技炫发" in l for l in lines)


# ── 技能芯片 ─────────────────────────────────────────────


def test_skill_chip_lifts_to_three():
    card = _model("技能芯片")
    card["state"]["skill_chips"] = ["黑客"]
    total, notes = cyber_bonus.check_bonus(card, skill="黑客")
    assert total == 2  # 1 → 3
    assert any("技能芯片" in n for n in notes)


def test_skill_chip_no_effect_if_already_above_three():
    card = _model("技能芯片")
    card["state"]["skill_chips"] = ["黑客"]
    card["skills"]["黑客"] = 5
    total, _ = cyber_bonus.check_bonus(card, skill="黑客")
    assert total == 0


def test_skill_chip_at_exactly_three_no_effect():
    card = _model("技能芯片")
    card["state"]["skill_chips"] = ["黑客"]
    card["skills"]["黑客"] = 3
    total, _ = cyber_bonus.check_bonus(card, skill="黑客")
    assert total == 0


def test_skill_chip_only_bound_skill():
    card = _model("技能芯片")
    card["state"]["skill_chips"] = ["黑客"]
    total, _ = cyber_bonus.check_bonus(card, skill="觉察")
    assert total == 0


# ── 痛觉编辑器（Combatant 层） ───────────────────────────


def _pc(**kw):
    base = dict(group_id=GID, owner_qq="1001", name="阿飞", side="pc",
                initiative=20, hp=30, max_hp=30, sp=4, ref=7, dex=7, dodge=2)
    base.update(kw)
    return C.Combatant(**base)


def test_pain_editor_ignores_crit_penalties():
    c = _pc(crits=("eye_lost", "muscle_tear"), pain_editor=True)
    assert c.ranged_penalty == 0
    assert c.melee_penalty == 0
    assert c.move_penalty == 0
    assert c.action_penalty == 0


def test_without_pain_editor_penalties_apply():
    c = _pc(crits=("eye_lost", "muscle_tear"), pain_editor=False)
    assert c.ranged_penalty != 0 or c.melee_penalty != 0


def test_pain_editor_ignores_no_dodge_and_walk_damage():
    c = _pc(crits=("leg_lost", "rib_fracture"), pain_editor=True)
    assert c.no_dodge is False
    assert c.walk_damage is False


def test_pain_editor_keeps_crit_record():
    # 伤势还在（记录保留），只是感觉不到
    c = _pc(crits=("eye_lost",), pain_editor=True)
    assert c.crits == ("eye_lost",)


def test_pain_editor_json_roundtrip():
    c = _pc(pain_editor=True)
    raw = C._stat_json(c)
    assert '"pain_editor": true' in raw


# ── 装卡后接入（install → load → 生效） ──────────────────


@pytest.fixture()
def conn():
    from core import db
    return db.connect(":memory:")


def _give_card(conn, eb=20000):
    from core import growth, roster
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)
    return card


def test_pain_editor_install_and_load(conn):
    """先装神经链路→芯片插槽→痛觉编辑器，load 后 pain_editor=True。"""
    from core import cyber_flow
    from cyberpunk import dice
    _give_card(conn)
    for item in ("神经链路", "芯片插槽", "痛觉编辑器"):
        text = cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                                  roller=dice.RandomRoller())
        assert "装上了" in text, text
    # 起一场战斗，把卡接上
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    me = next(c for c in C.load(conn, GID).combatants if c.side == "pc")
    assert me.pain_editor is True


def test_skill_chip_install_binds_skill(conn):
    from core import cyber_flow
    from cyberpunk import dice
    _give_card(conn)
    for item in ("神经链路", "芯片插槽"):
        text = cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                                  roller=dice.RandomRoller())
        assert "装上了" in text, text
    text = cyber_flow.install(conn, GID, "u1", "安装 技能芯片 黑客",
                              roller=dice.RandomRoller())
    assert "装上了" in text
    from core import roster
    card = roster.get(conn, GID, "u1")
    assert card["state"]["skill_chips"] == ["黑客"]
    # 黑客不在卡上（0 级）→ 芯片把它抬到 3
    assert cyber_bonus.check_bonus(card, skill="黑客")[0] == 3

