"""战斗扩展：环境修正（§3）与敌人行为脚本（§4）。"""

import pytest

from core import combat as C
from core import roster, session
from core.db import connect
from cyberpunk import dice

GID = "qq:g1"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _card(conn, qq="1001", name="阿飞"):
    card = roster.build(template="独狼", name=name)
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _combat(round_=1):
    return C.Combat(group_id=GID, campaign_id=None, scene="", round=round_,
                    turn_index=0, active=True)


def _pc(**kw):
    base = dict(group_id=GID, owner_qq="1001", name="阿飞", side="pc",
                initiative=20, hp=30, max_hp=30, sp=4, ref=7, dex=7, dodge=2)
    base.update(kw)
    return C.Combatant(**base)


# ── 环境修正（§3）────────────────────────────────────────────


def test_rounds_spec_variants():
    assert C._rounds_hit(None, 5)
    assert C._rounds_hit([1, 3], 2)
    assert not C._rounds_hit([1, 3], 4)
    assert C._rounds_hit("1-3", 3)
    assert not C._rounds_hit("1-3", 4)
    assert C._rounds_hit("2,4", 4)
    assert not C._rounds_hit("2,4", 3)
    assert C._rounds_hit(2, 2)
    assert not C._rounds_hit(2, 1)


def test_modifier_for_rounds_faction_and_noise_immunity():
    c = _combat()
    c.modifiers = [
        {"id": "scream", "label": "尖叫", "value": -4, "rounds": [1, 3],
         "applies_to": "all", "negate_if": ["水平阻尼降噪"]},
        {"id": "pc_only", "value": 1, "applies_to": "pc"},
    ]
    pc = _pc()
    enemy = C.Combatant(group_id=GID, owner_qq="enemy:1", name="打手", side="enemy",
                        initiative=10, hp=20, max_hp=20, tags=())
    assert C.modifier_for(c, pc, round=1) == -3       # 尖叫 −4 + pc_only +1
    assert C.modifier_for(c, pc, round=4) == 1        # 尖叫过期，只剩阵营修正
    assert C.modifier_for(c, enemy) == -4             # 敌人不吃 pc_only

    pc.tags = ("装备：水平阻尼降噪耳机",)
    assert C.modifier_for(c, pc, round=1) == 1        # 尖叫被免疫
    assert "尖叫" not in C.modifier_text(c, pc, round=1)


def test_attack_reports_scream_and_misses(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),),
            modifiers=({"id": "scream", "label": "尖叫", "value": -4,
                        "rounds": [1, 3]},),
            roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    text = C.attack(conn, GID, "1001", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3]))
    assert "尖叫 −4" in text
    assert "没打中" in text               # 17 − 4 = 13 < DV 15


def test_attack_hits_without_scream_after_round_three(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),),
            modifiers=({"id": "scream", "label": "尖叫", "value": -4,
                        "rounds": [1, 3]},),
            roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    conn.execute("UPDATE combats SET round = 4 WHERE group_id = ?", (GID,))
    conn.commit()
    text = C.attack(conn, GID, "1001", skill="手枪",
                    roller=dice.ScriptedRoller([10, 4, 4, 3]))
    assert "尖叫" not in text
    assert "命中" in text                 # 10 + 7 = 17 ≥ DV 15


def test_status_text_shows_modifier_summary(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),),
            modifiers=({"id": "scream", "label": "尖叫", "value": -4,
                        "rounds": [1, 3]},),
            roller=dice.ScriptedRoller([9, 2]))
    assert "环境修正：尖叫 −4" in C.status_text(conn, GID)


def test_encounter_loads_modifiers_and_scripts():
    conn = connect(":memory:")
    _card(conn)
    gate = session.Gate(conn)
    camp = gate.start_new(GID, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    C.start_text(conn, GID, "")
    combat = C.load(conn, GID)
    assert combat.modifiers and combat.modifiers[0]["id"] == "scream"
    clown = next(c for c in combat.combatants if c.name == "小丑")
    assert clown.script["flee_if"]["round_gt"] == 3
    assert clown.script["on_flee_fail"]["self_destruct"]["damage"] == "6D6"
    assert "水平阻尼降噪" in combat.modifiers[0]["negate_if"]


# ── 目标平摊（§4 targets）────────────────────────────────────


def test_targets_split_between_pc_and_vampire():
    c = _combat()
    pc = _pc()
    vamp = C.Combatant(group_id=GID, owner_qq="enemy:9", name="吸血鬼",
                       side="vampire", initiative=15, hp=30, max_hp=30, sp=4)
    spec = {"targets": {"split": ["pc", "vampire"], "rounds": [1, 2]}}
    clown = C.Combatant(group_id=GID, owner_qq="enemy:1", name="小丑", side="clown",
                        initiative=18, hp=35, max_hp=35, sp=11, script=spec)
    monk = C.Combatant(group_id=GID, owner_qq="enemy:2", name="僧侣", side="monk",
                       initiative=16, hp=45, max_hp=45, sp=7, script=dict(spec))
    c.combatants = [pc, vamp, clown, monk]
    assert C._pick_target(c, clown, C.foes(c, clown), clown.script,
                          dice.ScriptedRoller([])).side == "pc"
    assert C._pick_target(c, monk, C.foes(c, monk), monk.script,
                          dice.ScriptedRoller([])).side == "vampire"


# ── 逃跑与自爆（§4 flee_if / on_flee_fail）────────────────────


def test_flee_when_round_gt():
    conn = connect(":memory:")
    c = _combat(round_=4)
    pc = _pc()
    enemy = C.Combatant(group_id=GID, owner_qq="enemy:1", name="小丑", side="clown",
                        initiative=10, hp=35, max_hp=35, sp=11,
                        script={"flee_if": {"round_gt": 3}})
    c.combatants = [pc, enemy]
    lines: list[str] = []
    C._enemy_turn(conn, c, enemy, dice.ScriptedRoller([]), lines)
    assert enemy.escaped and not enemy.alive
    assert any("脱离战斗" in x for x in lines)


def test_flee_when_ally_is_serious():
    c = _combat(round_=1)
    clown = C.Combatant(group_id=GID, owner_qq="enemy:1", name="小丑", side="clown",
                        initiative=18, hp=35, max_hp=35, wound=18, sp=11,
                        script={"flee_if": {"any_serious": True},
                                "allies": ["clown", "monk"]})
    monk = C.Combatant(group_id=GID, owner_qq="enemy:2", name="僧侣", side="monk",
                       initiative=16, hp=20, max_hp=45, wound=23, sp=7)
    c.combatants = [clown, monk]
    assert C._should_flee(c, clown, clown.script)


def test_flee_blocked_self_destruct_hits_everyone():
    conn = connect(":memory:")
    c = _combat(round_=4)
    pc = _pc()
    enemy = C.Combatant(
        group_id=GID, owner_qq="enemy:1", name="小丑", side="clown",
        initiative=10, hp=35, max_hp=35, sp=11,
        script={"flee_if": {"round_gt": 3}, "flee_blocked": True,
                "on_flee_fail": {"self_destruct": {
                    "weapon": "穿甲手榴弹", "damage": "6D6",
                    "note": "伪装成念珠"}}})
    c.combatants = [pc, enemy]
    lines: list[str] = []
    C._enemy_turn(conn, c, enemy, dice.ScriptedRoller([6] * 6), lines)
    assert enemy.dead and not enemy.alive
    assert "穿甲手榴弹" in "\n".join(lines)
    assert "6D6" in "\n".join(lines)
    assert any("没有地图" in x for x in lines)
    # 6×6=36 − 护甲 4 = 32 → 30 血的玩家吃满：A1 起 HP≤0 只算致命伤，还没死
    assert pc.hp == 0 and pc.mortal and not pc.dead


def test_join_round_skips_until_its_round():
    conn = connect(":memory:")
    c = _combat(round_=1)
    pc = _pc()
    enemy = C.Combatant(group_id=GID, owner_qq="enemy:1", name="援兵", side="clown",
                        initiative=10, hp=20, max_hp=20, sp=4,
                        script={"join_round": 2})
    c.combatants = [pc, enemy]
    lines: list[str] = []
    C._enemy_turn(conn, c, enemy, dice.ScriptedRoller([]), lines)
    assert any("场外" in x for x in lines)
    assert pc.hp == 30 and not enemy.escaped


# ── A1 死亡豁免 / 致命伤（full.txt:15730-15745、15943-15949）─────────────


def test_pc_goes_mortal_not_dead():
    conn = connect(":memory:")
    c = _combat()
    pc = _pc(body=5)
    c.combatants = [pc]
    lines: list[str] = []
    C._apply_damage_hp(c, pc, 40, lines)
    assert pc.hp == 0
    assert pc.mortal and not pc.dead
    assert any("致命伤" in x for x in lines)


def test_death_save_success_recovers_one_hp_and_loses_action():
    conn = connect(":memory:")
    c = _combat()
    pc = _pc(body=5, mortal=True, damage="2D6")
    c.combatants = [pc]
    lines: list[str] = []
    # 掷出 3 < 5 → 撑住
    C._death_check(c, pc, dice.ScriptedRoller([3]), lines)
    assert not pc.mortal and not pc.dead
    assert pc.hp == 1
    assert pc.death_save_penalty == 0
    slot = C._slots(c)[pc.owner_qq]
    assert slot.action is True
    assert any("失去意识" in x for x in lines)


def test_death_save_roll_ten_is_auto_death():
    conn = connect(":memory:")
    c = _combat()
    pc = _pc(body=5, mortal=True)
    c.combatants = [pc]
    lines: list[str] = []
    C._death_check(c, pc, dice.ScriptedRoller([10]), lines)
    assert pc.dead and not pc.mortal


def test_death_save_roll_at_threshold_fails():
    conn = connect(":memory:")
    c = _combat()
    pc = _pc(body=5, mortal=True)
    c.combatants = [pc]
    lines: list[str] = []
    # 掷出 5：需 < 5，达不到 → 死
    C._death_check(c, pc, dice.ScriptedRoller([5]), lines)
    assert pc.dead and not pc.mortal


def test_mortal_taking_more_damage_increases_death_penalty():
    conn = connect(":memory:")
    c = _combat()
    # 致命伤角色 HP 已是 0，再挨打 -> 死亡豁免惩罚 +1
    pc = _pc(body=5, mortal=True, hp=0)
    c.combatants = [pc]
    lines: list[str] = []
    C._apply_damage_hp(c, pc, 5, lines)
    assert pc.mortal and not pc.dead
    assert pc.death_save_penalty == 1
    assert any("惩罚 +1" in x for x in lines)


def test_enemy_dies_at_zero_hp():
    conn = connect(":memory:")
    c = _combat()
    enemy = C.Combatant(group_id=GID, owner_qq="enemy:1", name="打手", side="enemy",
                        initiative=10, hp=20, max_hp=20)
    c.combatants = [enemy]
    lines: list[str] = []
    C._apply_damage_hp(c, enemy, 25, lines)
    assert enemy.dead and not enemy.mortal
