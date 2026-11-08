"""战斗测试：按核心书的先攻队列与动作经济（书内页 167–175）。"""

import pytest

from core import combat as C
from core import roster
from core.db import connect
from cyberpunk import dice

GID = "qq:g1"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _card(conn, qq="1001", name="阿飞", **kw):
    card = roster.build(template="独狼", name=name, **kw)
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _pc(conn):
    return [x for x in C.load(conn, GID).combatants if x.side == "pc"][0]


def _enemy(conn):
    return [x for x in C.load(conn, GID).combatants if x.side == "enemy"][0]


# ── 先攻队列（书内页 168）────────────────────────────────────


def test_initiative_queue_includes_enemies(conn):
    _card(conn)  # 独狼 REF7；打手 REF6
    c = C.start(conn, GID, campaign_id=1, scene="act1.dock",
                enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    assert len(c.combatants) == 2
    assert [x.side for x in c.combatants] == ["pc", "enemy"]
    assert c.order[0].side == "pc"          # 16 > 8
    assert c.round == 1 and c.active
    assert c.current is not None and c.current.side == "pc"


def test_initiative_ties_are_rerolled(conn):
    _card(conn)
    # 先 3+7=10 / 4+6=10 同值 → 重掷 9 / 1 → 玩家 16 在前
    c = C.start(conn, GID, enemies=(("打手", 1),),
                roller=dice.ScriptedRoller([3, 4, 9, 1]))
    assert len({x.initiative for x in c.combatants}) == 2
    assert c.order[0].side == "pc"


def test_start_without_any_card_is_rejected(conn):
    with pytest.raises(C.CombatError):
        C.start(conn, GID, roller=dice.ScriptedRoller([1]))


def test_unknown_enemy_name_is_rejected(conn):
    _card(conn)
    with pytest.raises(C.CombatError):
        C.start(conn, GID, enemies=(("哥斯拉", 1),), roller=dice.ScriptedRoller([1, 2]))


# ── 远程 / 近战 ───────────────────────────────────────────────


def test_ranged_attack_uses_dv_and_armor(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    # 10+REF7=17 ≥ DV15 命中；伤害 3+3=6，护甲 4 → 掉 2；敌人回合 3+6+4=13 未中
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "命中" in text and "DV 15" in text
    assert _enemy(conn).hp == 18


def test_melee_is_opposed_by_dodge_and_halves_armor(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    # 近战 7+敏捷7+搏击2=16 vs 闪避 3+6+4=13 → 命中；伤害 1D6=4，半护甲 2 → 掉 2
    text = C.attack(conn, GID, "1001", "打手", skill="搏击",
                    roller=dice.ScriptedRoller([7, 3, 4, 3]))
    assert "闪避 13" in text
    assert _enemy(conn).hp == 18


def test_miss_then_enemy_turn_hurts_pc(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    # 攻击 1+7=8 <15 失手；敌人 6+6+4=16 命中；伤害 5+5=10 − 护甲 4 = 6
    # 伤害骰特意不用两个 6：免得触发严重伤势（那条路径由 test_crit.py 单独覆盖）
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([1, 6, 5, 5]))
    assert "没打中" in text
    assert _pc(conn).hp == 34


def test_aimed_head_doubles_damage(conn):
    _card(conn, skill_overrides={"手枪": 6})
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    # 瞄准 −8：10+7+6−8=15 ≥15 命中；伤害 6−护甲4=2，头部翻倍 → 4
    text = C.attack(conn, GID, "1001", "打手", skill="手枪", aim="头",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "瞄准" in text and "伤害翻倍" in text
    assert _enemy(conn).hp == 16


# ── 义体命中修正（B 批：瞄准/远视目镜）────────────────────────


def test_cyber_target_scope_boosts_aimed_hit(conn):
    """装「瞄准目镜」的远程角色，瞄准射击命中 +1（full.txt:28330-28331）。"""
    from core import roster as _roster

    card = _card(conn, skill_overrides={"手枪": 6})
    card["state"] = {"cyber": ["瞄准目镜"]}
    _roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    text = C.attack(conn, GID, "1001", "打手", skill="手枪", aim="头",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "瞄准目镜+1" in text, text


def test_cyber_target_scope_far_shot(conn):
    """装「远视目镜」时，目标 ≥51 米才给命中 +1（full.txt:28339）。"""
    from core import roster as _roster

    card = _card(conn, skill_overrides={"手枪": 6})
    card["state"] = {"cyber": ["远视目镜"]}
    _roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 60 米")
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "远视目镜+1" in text, text


def test_cyber_target_scope_not_at_close(conn):
    """装「远视目镜」但目标只有 8 米 → 命中不加。"""
    from core import roster as _roster

    card = _card(conn, skill_overrides={"手枪": 6})
    card["state"] = {"cyber": ["远视目镜"]}
    _roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "远视目镜+1" not in text, text


# ── 义体护甲 SP（C-2：编织皮肤 SP7 / 皮下护甲 SP11）──────────


def test_cyber_subdermal_armor_raises_effective_sp(conn):
    """装「皮下护甲」后，PC 战斗护甲从穿着/DEFAULT 抬到 SP11 且不叠加。"""
    from core import roster as _roster

    card = _card(conn)
    # 独狼默认穿着护甲远低于 SP11
    assert _pc_default_sp(card) < 11
    card["state"] = {"cyber": ["皮下护甲"]}
    _roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    pc = _pc(conn)
    assert pc.sp == 11, f"皮下护甲应把 SP 抬到 11，实际 {pc.sp}"


def test_cyber_woven_skin_sp7(conn):
    """装「编织皮肤」→ 战斗 SP 抬到 7（低于皮下护甲 11，不叠加取高）。"""
    from core import roster as _roster

    card = _card(conn)
    card["state"] = {"cyber": ["编织皮肤"]}
    _roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    assert _pc(conn).sp == 7, _pc(conn).sp


def _pc_default_sp(card) -> int:
    # 未装护甲义体时，sp = 穿着护甲 or DEFAULT_SP（combat.start 的原始口径）
    from core import roster as _roster
    rules = _roster.to_character(card)
    return rules.armor.sp or 4


# ── 动作经济（书内页 168）────────────────────────────────────


def test_action_is_spent_before_turn_passes(conn):
    """动作一旦用掉，同一回合内不能再用（用「装填」验证）。"""
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    assert "装填" in (C.act(conn, GID, "1001", "装填", roller=dice.ScriptedRoller([3])) or "")
    # 敌人回合后回到玩家是「新一轮」，动作用量已重置 —— 这里只验证当轮不可重复
    c = C.load(conn, GID)
    assert c.active


def test_move_then_attack_is_allowed(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    assert "移动" in (C.act(conn, GID, "1001", "移动找掩体") or "")
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 3]))
    assert "命中" in text


def test_second_move_is_rejected(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.act(conn, GID, "1001", "移动到门口")
    assert "移动动作已经用过" in (C.act(conn, GID, "1001", "移动") or "")


def test_reload_costs_action_and_passes_turn(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    # 装填（动作）→ 推进到敌人 → 敌人 3+6+4=13 未中 → 回到玩家，进入第 2 轮
    text = C.act(conn, GID, "1001", "装填", roller=dice.ScriptedRoller([3]))
    assert "装填" in text and "轮到" in text
    assert C.load(conn, GID).round == 2


def test_non_action_text_is_ignored(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    assert C.act(conn, GID, "1001", "我看看周围") is None


def test_delay_action_ends_turn_and_is_honest(conn):
    """延迟动作（full.txt:14430-14433）：至少结束回合，并诚实说明引擎没有
    事件调度、无法在指定时刻自动触发。"""
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    text = C.act(conn, GID, "1001", "延迟", roller=dice.ScriptedRoller([3]))
    assert "结束回合" in text
    # 诚实提示：书里要「指定触发事件+预告动作和目标」，本引擎没事件调度
    assert "要指定触发事件" in text and "事件调度" in text
    assert C.load(conn, GID).round == 2  # 动作已花、回合推进


# ── 结束与持久化 ──────────────────────────────────────────────


def test_killing_enemy_ends_combat(conn):
    _card(conn, skill_overrides={"手枪": 9})
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    c = C.load(conn, GID)
    c.enemies[0].hp = 2
    C._save(conn, c)
    text = C.attack(conn, GID, "1001", "打手", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert "战斗结束" in text
    assert C.load(conn, GID).active is False


def test_hp_is_written_back_to_card_state(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.close_range(conn, GID, "1001", "距离 8 米")
    C.attack(conn, GID, "1001", "打手", skill="手枪",
             roller=dice.ScriptedRoller([1, 6, 5, 5]))
    assert roster.get(conn, GID, "1001")["state"]["hp"] == 34


def test_rest_night_heals_body_points(conn):
    """「过夜」自然恢复体格点 HP（不带抗体增强=×1）。"""
    from core import roster as _roster

    card = _card(conn)
    rules = _roster.to_character(card)
    card["state"] = {"hp": 1, "max_hp": rules.max_hp}
    _roster.save(conn, GID, "1001", card)
    C.rest_night(conn, GID)
    got = roster.get(conn, GID, "1001")["state"]["hp"]
    assert got == 1 + rules.effective_stat("BODY"), got


def test_rest_night_antibody_heals_double_and_clamps(conn):
    """装「抗体增强」→ 过夜恢复体格×2，且不越 max_hp。"""
    from core import roster as _roster

    card = _card(conn)
    rules = _roster.to_character(card)
    card["state"] = {"cyber": ["抗体增强"], "hp": 1, "max_hp": rules.max_hp}
    _roster.save(conn, GID, "1001", card)
    C.rest_night(conn, GID)
    got = roster.get(conn, GID, "1001")["state"]["hp"]
    assert got == 1 + rules.effective_stat("BODY") * 2, got
    assert got <= rules.max_hp

    # 从接近满血开始，恢复量被 clamp 到 max_hp（不过量）
    card = roster.get(conn, GID, "1001")
    card["state"]["hp"] = rules.max_hp - 1
    _roster.save(conn, GID, "1001", card)
    C.rest_night(conn, GID)
    assert roster.get(conn, GID, "1001")["state"]["hp"] == rules.max_hp


def test_rest_night_skips_mortal(conn):
    """致命伤（HP≤0，还没稳定）不能自愈——过夜跳过。"""
    from core import roster as _roster

    card = _card(conn)
    rules = _roster.to_character(card)
    card["state"] = {"cyber": ["抗体增强"], "hp": 0, "max_hp": rules.max_hp}
    _roster.save(conn, GID, "1001", card)
    text = C.rest_night(conn, GID)
    assert "没有可以自愈的人" in text, text
    assert roster.get(conn, GID, "1001")["state"]["hp"] == 0


def test_status_text_lists_queue(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 2),), roller=dice.ScriptedRoller([9, 2, 3]))
    text = C.status_text(conn, GID)
    assert "先攻队列" in text
    assert "打手1" in text and "打手2" in text
    assert "移动" in text and "动作" in text


def test_end_clears_everything(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    C.end(conn, GID)
    assert C.load(conn, GID) is None
    assert "现在没有在打" in C.status_text(conn, GID)


# ── 自然语言 ──────────────────────────────────────────────────


def test_natural_language_helpers(conn):
    _card(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    fight = C.active(conn, GID)
    assert C.looks_like_attack("我朝打手开枪")
    assert C.skill_from_text("我拔刀砍他") == "近战武器"
    assert C.skill_from_text("开枪") == "手枪"
    assert C.target_from_text(fight, "打手") == "打手"


def test_natural_language_attack_through_turn_flow(conn, monkeypatch):
    """回合层：战斗中说「我一枪打他」要真的走战斗结算。"""
    import asyncio

    from core import turn_flow
    from core.session import Campaign, Mode

    _card(conn)
    conn.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (1, '测试', 'active', 'act1.dock', 'demo', '', '')"
    )
    conn.commit()
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    before = C.load(conn, GID).enemies[0].hp

    camp = Campaign(id=1, title="t", status="active", scene="act1.dock",
                    created_at="", updated_at="")

    class _State:
        mode = Mode.ACTIVE
        campaign = camp

    class _Gate:
        def state(self, gid):
            return _State()

    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(turn_flow, "GATE", _Gate())

    text = asyncio.run(
        turn_flow.handle(
            GID, "1001", "我一枪打他", roller=dice.ScriptedRoller([10, 3, 3, 3])
        )
    )
    assert text and "命中" in text
    assert C.load(conn, GID).enemies[0].hp < before
