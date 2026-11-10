"""弹种与穿甲弹。

规则依据（`rules/red_core/raw/full.txt`）：

* `:14731-14732`「你可以花费一个其他动作来用一种子弹种类完全重新装填武器/
  更换弹匣。你不能在一个弹匣里混合多种子弹种类。」→ 弹种跟着弹匣走。
* `:26688-26715` 弹药种类表（基础/穿甲/生物毒素/EMP/膨胀/闪光/燃烧/毒气/
  橡胶/麻醉/智能/烟雾/催泪）。
* `:26726-26731` **穿甲弹**：「当使用这种弹药时，目标的护甲被穿透时，
  SP 减值从 1 增加为 2。」——不是护甲减半。
* `:26816-26823` **橡胶弹**：「使用这种弹药造成的伤害不会造成严重伤势。
  此外，用这种弹药进行的攻击不能削减护甲。如果这种弹药造成的损害将使
  HP 大于 1 的目标降低到小于 0HP，目标 HP 将停留在 1HP。」
"""

from __future__ import annotations

from core import combat as C
from core import db, roster
from cyberpunk import dice

GID = "qq:ammo-type"


def _conn():
    return db.connect(":memory:")


def _fight(conn, *, skill=4, enemy_hp=None, distance="距离 8 米"):
    roster.save(conn, GID, "1001",
                roster.build(template="独狼", name="阿飞",
                             skill_overrides={"手枪": skill, "自动武器": 6}))
    C.start(conn, GID, enemies=(("打手", 1),), scene="a1.opera",
            roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", distance)
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    foe = next(c for c in fight.combatants if c.side == "enemy")
    if enemy_hp is not None:
        foe.hp = foe.max_hp = enemy_hp
    C._save(conn, fight)
    return fight, me, foe


def _me(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "pc")


def _foe(conn):
    return next(c for c in C.load(conn, GID).combatants if c.side == "enemy")


def _reload(conn, text="装填"):
    return C.act(conn, GID, "1001", text)


# ---------- 弹种表 / 文本识别 ----------


def test_ammo_types_cover_the_rulebook_table():
    assert set(C.AMMO_TYPES) >= {
        C.AMMO_BASE, C.AMMO_AP, C.AMMO_RUBBER,
        "生物毒素弹", "EMP 弹", "膨胀弹", "闪光弹", "燃烧弹",
        "毒气弹", "麻醉弹", "智能弹药", "烟雾弹", "催泪弹",
    }


def test_ammo_type_from_text():
    assert C.ammo_type_from_text("装填 穿甲弹") == C.AMMO_AP
    assert C.ammo_type_from_text("换上 AP 弹") == C.AMMO_AP
    assert C.ammo_type_from_text("换橡胶弹") == C.AMMO_RUBBER
    assert C.ammo_type_from_text("装填 催泪弹") == "催泪弹"
    assert C.ammo_type_from_text("装填") is None


def test_default_ammo_is_base():
    conn = _conn()
    _, me, _ = _fight(conn)
    assert me.ammo_type == C.AMMO_BASE


# ---------- 装填换弹种 ----------


def test_reload_can_switch_ammo_type():
    conn = _conn()
    _fight(conn)
    text = _reload(conn, "装填 穿甲弹")
    assert "穿甲弹" in text
    assert "弹种 基础弹药 → 穿甲弹" in text
    assert _me(conn).ammo_type == C.AMMO_AP


def test_reload_without_a_type_keeps_the_current_one():
    conn = _conn()
    _fight(conn)
    _reload(conn, "装填 穿甲弹")
    text = _reload(conn, "装填")
    assert "弹种" not in text
    assert _me(conn).ammo_type == C.AMMO_AP


def test_ammo_type_survives_save_and_load():
    conn = _conn()
    _fight(conn)
    _reload(conn, "装填 穿甲弹")
    assert _me(conn).ammo_type == C.AMMO_AP


# ---------- 穿甲弹：SP 减值 1 → 2 ----------


def _shoot(conn, *, ammo_type, rolls):
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    me.ammo_type = ammo_type
    me.ammo = me.mag or 8
    C._save(conn, fight)
    return C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller(rolls))


def test_ap_rounds_take_two_armor_points_per_hit():
    conn = _conn()
    _, _, foe = _fight(conn)
    assert foe.sp_wear == 0
    # 10+REF7+手枪4 = 21 ≥ DV15；伤害 2D6=3+3=6，护甲 4 挡掉，打掉 2
    _shoot(conn, ammo_type=C.AMMO_AP, rolls=[10, 3, 3, 1, 1])
    assert _foe(conn).sp_wear == 2


def test_base_rounds_take_one_armor_point_per_hit():
    conn = _conn()
    _fight(conn)
    _shoot(conn, ammo_type=C.AMMO_BASE, rolls=[10, 3, 3, 1, 1])
    assert _foe(conn).sp_wear == 1


def test_ap_does_not_change_the_damage_itself():
    """穿甲弹改的是护甲磨损，不是当次伤害（书里没写减伤翻倍）。"""
    conn = _conn()
    _, _, foe = _fight(conn)
    before = foe.hp
    _shoot(conn, ammo_type=C.AMMO_AP, rolls=[10, 3, 3, 1, 1])
    # 2D6=6 − 护甲4 = 2
    assert _foe(conn).hp == before - 2


def test_ap_also_applies_to_autofire():
    conn = _conn()
    _fight(conn)
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    me.ammo_type = C.AMMO_AP
    me.ammo = me.mag = 30
    C._save(conn, fight)
    # 冲锋枪 DV17：10+REF7+自动武器6 = 23，超出 6 → 取 3 发；2D6=3+3=6 → 18 − 护甲4
    C.attack(conn, GID, "1001", "打手", autofire=True, weapon="冲锋枪",
             roller=dice.ScriptedRoller([10, 3, 3, 1, 1]))
    assert _foe(conn).sp_wear == 2


def test_melee_never_uses_ammo_type():
    conn = _conn()
    _fight(conn, distance="距离 1 米")
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    me.ammo_type = C.AMMO_AP
    C._save(conn, fight)
    foe = _foe(conn)
    before_wear = foe.sp_wear
    C.attack(conn, GID, "1001", "打手", skill="近战武器",
             roller=dice.ScriptedRoller([10, 3, 3, 3, 1, 1]))
    assert _foe(conn).sp_wear == before_wear + 1


# ---------- 橡胶弹 ----------


def test_rubber_rounds_never_ablate_armor():
    conn = _conn()
    _fight(conn)
    _shoot(conn, ammo_type=C.AMMO_RUBBER, rolls=[10, 3, 3, 1, 1])
    assert _foe(conn).sp_wear == 0


def test_rubber_rounds_never_cause_critical_injuries():
    conn = _conn()
    _fight(conn, enemy_hp=40)
    text = _shoot(conn, ammo_type=C.AMMO_RUBBER, rolls=[10, 6, 6, 1, 1])
    # 两个 6 本来该触发严重伤势，橡胶弹免除（文案里的「不造成严重伤势」是弹种说明）
    assert "中了严重伤" not in text
    assert _foe(conn).crits == ()


def test_rubber_rounds_stop_at_one_hp():
    conn = _conn()
    # 敌人只有 3 HP：橡胶弹最多打到 1
    _fight(conn, enemy_hp=3)
    _shoot(conn, ammo_type=C.AMMO_RUBBER, rolls=[10, 6, 6, 1, 1])
    foe = _foe(conn)
    assert foe.hp >= 1
    assert not foe.dead


def test_rubber_notes_in_the_reply():
    conn = _conn()
    _fight(conn)
    text = _shoot(conn, ammo_type=C.AMMO_RUBBER, rolls=[10, 3, 3, 1, 1])
    assert "橡胶弹" in text


# ---------- 「命中后检定」类弹种：2026-11-10 起真正结算 ----------
#
# 书 `:26442-26450`：攻击不造成常规伤害，改过 DV13 折磨&药物耐受，失败直接 2d6。
# 早期版本只提示不结算，现在由 cyberpunk/ammo_effect.py 落地。


def test_check_only_ammo_does_no_ordinary_damage():
    """检定类弹种不按「伤害减护甲」结算常规伤害。"""
    conn = _conn()
    _fight(conn)
    text = _shoot(conn, ammo_type="毒气弹", rolls=[10, 3, 3, 1, 1])
    assert "不造成常规伤害" in text
    assert "命中后改过检定" in text or "改过检定" in text


def test_check_only_ammo_resolves_the_check():
    """命中后要真的过检定，并把结果写进回执。"""
    conn = _conn()
    _fight(conn)
    text = _shoot(conn, ammo_type="毒气弹", rolls=[10, 3, 3, 1, 1])
    assert "折磨&药物耐受" in text and "DV13" in text


def test_check_only_ammo_no_damage_on_success():
    """检定通过 → 不掉血。"""
    conn = _conn()
    _, _, foe = _fight(conn)
    before = _foe(conn).hp
    # 命中骰 10，然后目标检定骰 10（通过）
    text = _shoot(conn, ammo_type="毒气弹", rolls=[10, 10, 1, 1])
    assert "扛住了" in text
    assert _foe(conn).hp == before


def test_check_only_ammo_damages_on_failure():
    """检定失败 → 直接伤害（不吃护甲）。"""
    conn = _conn()
    _, _, foe = _fight(conn)
    before = _foe(conn).hp
    text = _shoot(conn, ammo_type="毒气弹", rolls=[10, 1, 1, 1, 1])
    assert "失败" in text
    assert _foe(conn).hp < before


def test_check_only_ammo_still_spends_the_round():
    conn = _conn()
    _fight(conn)
    fight = C.load(conn, GID)
    me = next(c for c in fight.combatants if c.side == "pc")
    me.ammo_type = "毒气弹"
    me.ammo = me.mag = 8
    C._save(conn, fight)
    C.attack(conn, GID, "1001", "打手", skill="手枪",
             roller=dice.ScriptedRoller([10, 3, 3, 1, 1]))
    assert _me(conn).ammo == 7


def test_ammo_note_appears_for_special_types():
    conn = _conn()
    _fight(conn)
    text = _shoot(conn, ammo_type=C.AMMO_AP, rolls=[10, 3, 3, 1, 1])
    assert "穿甲弹" in text
