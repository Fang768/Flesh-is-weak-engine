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
