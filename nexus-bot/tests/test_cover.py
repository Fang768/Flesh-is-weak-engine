"""A5：掩体打烂后的通行/视线 + 火力压制。

规则依据（rules/red_core/raw/full.txt）：
  :15454-15458 耐久归零时掩体被彻底摧毁；溢出的伤害不伤到后面的人。
  :15416-15431 打穿路障之后射手就能看见后面的人。
  :15445-15450 掩体三定律：敌人看得到你就不算躲进掩体。
  :14873-14894 爆炸物：足够抵挡的掩体挡下全部伤害，掩体被打烂则吃满伤害。
  :14903-14914 火力压制：一个动作 + 10 发，25 米内未进掩体者对抗专注+意志。

「约定」（规则书没写网格占格）：掩体占一格，人站不进去，
所以「躲进掩体」用「紧贴一个还立着的掩体（八向相邻）」表达。
"""

import pytest

from core import combat as C
from core import map as gridmap
from core import roster, session
from core.db import connect
from cyberpunk import dice

GID = "qq:cover1"


@pytest.fixture()
def conn():
    return connect(":memory:")


# ── 地图层：gone / playable / render / describe ─────────────────


def test_cover_cell_is_not_playable_until_destroyed():
    gm = gridmap.load("opera_hall")
    bar = next(c for c in gm.covers if c.id == "bar")
    assert gm.walkable((bar.x, bar.y)) is True       # 地形本身能走
    assert gm.playable((bar.x, bar.y)) is False      # 但立着掩体，站不进去
    assert gm.playable((bar.x, bar.y), {"bar"}) is True  # 打烂了就能站


def test_destroyed_cover_stops_blocking_the_shot():
    gm = gridmap.load("opera_hall")
    assert gm.blockers((2, 10), (8, 1))            # 吧台横在中间
    assert gm.blockers((2, 10), (8, 1), {"bar"}) == []
    assert gm.visible((2, 10), (8, 1)) is False
    assert gm.visible((2, 10), (8, 1), {"bar"}) is True


def test_destroyed_cover_does_not_protect_anyone():
    gm = gridmap.load("opera_hall")
    assert gm.cover_on_line((2, 8), (8, 8)) is not None
    assert gm.cover_on_line((2, 8), (8, 8), {"bar"}) is None


def test_path_goes_through_rubble_after_cover_is_destroyed():
    gm = gridmap.load("opera_hall")
    route, _ = gm.path((2, 10), (3, 8))
    assert route == []                              # 掩体挡着，过不去
    route, cost = gm.path((2, 10), (3, 8), {"bar"})
    assert route[-1] == (3, 8) and cost >= 2


def test_move_into_rubble_cell_after_cover_is_destroyed():
    gm = gridmap.load("opera_hall")
    route, _cost, reached = gm.move((2, 10), (3, 8), 6, {"bar"})
    assert reached is True and route[-1] == (3, 8)


def test_render_marks_destroyed_cover_with_x():
    gm = gridmap.load("opera_hall")
    text = gm.render(gone={"bar"})
    assert "x" in text
    assert "打烂的掩体" in text
    bar = next(c for c in gm.covers if c.id == "bar")
    # 第 0 行是标题、第 1 行是列号，之后每行是 "行号 + 格子"
    row = text.splitlines()[2 + bar.y]
    assert row[2 + bar.x] == "x"


def test_describe_says_rubble():
    gm = gridmap.load("opera_hall")
    bar = next(c for c in gm.covers if c.id == "bar")
    assert "掩体已成废墟" in gm.describe((bar.x, bar.y), {"bar"})


def test_low_cover_never_blocks_line_of_sight():
    gm = gridmap.load("opera_hall")
    seats = next(c for c in gm.covers if c.id == "seats-a")
    assert seats.blocks_los is False
    assert gm.blockers((1, seats.y), (gm.width - 2, seats.y)) == []


# ── 战斗层：打烂掩体 ───────────────────────────────────────────


def _camp(conn, scene):
    gate = session.Gate(conn)
    camp = gate.start_new(GID, "掩体测试", "opera")
    gate.set_scene(camp.id, scene)
    return camp


def _fight(conn, scene="a1.opera", enemies=None):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    camp = _camp(conn, scene)
    C.start(
        conn,
        GID,
        campaign_id=camp.id,
        scene=scene,
        enemies=enemies or [{"name": "小丑", "count": 1,
                             "faction": "clown", "label": "小丑"}],
        roller=dice.RandomRoller(1),
    )
    return C.load(conn, GID)


def _pc_enemy(fight):
    pc = next(c for c in fight.order if c.side == "pc")
    enemy = next(c for c in fight.order if c.side != "pc")
    return pc, enemy


def _stand(conn, fight, pc, enemy, pc_pos, enemy_pos):
    pc.x, pc.y = pc_pos
    enemy.x, enemy.y = enemy_pos
    fight.turn_index = fight.order.index(pc)
    C._save(conn, fight)


def _wreck_bar(conn, fight):
    fight.covers["bar"] = 0
    C._save(conn, fight)


def test_destroying_a_cover_marks_it_gone(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    fight.covers["bar"] = 3                     # 差一点就打烂
    C._save(conn, fight)
    text = C.attack(conn, GID, pc.owner_qq, "吧台", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert "掩体被打烂了" in text
    loaded = C.load(conn, GID)
    assert loaded.covers["bar"] == 0
    assert "bar" in C._gone(loaded)


def test_destroyed_cover_no_longer_blocks_the_shot(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    blocked = C.attack(conn, GID, pc.owner_qq, "", skill="手枪",
                       roller=dice.RandomRoller(1))
    assert "吧台" in blocked and "挡住" in blocked

    _wreck_bar(conn, fight)
    fight = C.load(conn, GID)                  # 重新载入，拿回这一回合的行动
    pc, enemy = _pc_enemy(fight)
    fight.turn_index = fight.order.index(pc)
    C._slots(fight)[pc.owner_qq].action = False
    C._save(conn, fight)
    text = C.attack(conn, GID, pc.owner_qq, "", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 1, 1, 1, 1]))
    assert "挡住" not in text
    assert "DV 20" in text                      # 10 格 = 20 米 → 手枪 20
    assert "命中" in text or "没打中" in text


def test_cannot_walk_onto_a_standing_cover(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    text = C.act(conn, GID, pc.owner_qq, "移动到 3,8", roller=dice.RandomRoller(1))
    assert "立着掩体" in text
    me = next(c for c in C.load(conn, GID).combatants if c.owner_qq == pc.owner_qq)
    assert (me.x, me.y) == (2, 10)


def test_can_walk_into_the_cell_after_the_cover_is_wrecked(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    _wreck_bar(conn, fight)
    text = C.act(conn, GID, pc.owner_qq, "移动到 3,8", roller=dice.RandomRoller(1))
    assert "移动到 (3,8)" in text
    me = next(c for c in C.load(conn, GID).combatants if c.owner_qq == pc.owner_qq)
    assert (me.x, me.y) == (3, 8)


def test_grenade_ignores_a_wrecked_cover(conn):
    """吧台替躲在它后面的人挡爆炸；吧台被打烂后那人就得吃满伤害。"""
    fight = _fight(conn, enemies=[
        {"name": "小丑", "count": 2, "faction": "clown", "label": "小丑"},
    ])
    pc = next(c for c in fight.order if c.side == "pc")
    pc.x, pc.y = (7, 7)
    one = next(c for c in fight.combatants if c.name == "小丑1")
    two = next(c for c in fight.combatants if c.name == "小丑2")
    one.x, one.y = (3, 7)      # 炸点
    two.x, two.y = (3, 9)      # 缩在吧台（3,8）后面
    fight.turn_index = fight.order.index(pc)
    C._save(conn, fight)
    roster.add_gear(conn, GID, pc.owner_qq, "手榴弹")

    def blast(roller):
        fight = C.load(conn, GID)
        pc = next(c for c in fight.order if c.side == "pc")
        fight.turn_index = fight.order.index(pc)
        C._slots(fight)[pc.owner_qq].action = False
        C._save(conn, fight)
        return C.throw_grenade(conn, GID, pc.owner_qq, "扔向小丑1", roller=roller)

    shielded = blast(dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4], loop=True))
    assert "小丑2 躲在吧台后面" in shielded
    assert "挡下全部爆炸" in shielded

    fight = C.load(conn, GID)
    _wreck_bar(conn, fight)
    roster.add_gear(conn, GID, pc.owner_qq, "手榴弹")
    exposed = blast(dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4], loop=True))
    assert "挡下全部爆炸" not in exposed
    assert "小丑2 被炸" in exposed


# ── 战斗层：火力压制 ───────────────────────────────────────────


def test_suppression_wording():
    assert C.looks_like_suppression("火力压制他们")
    assert C.looks_like_suppression("压制射击")
    assert not C.looks_like_suppression("全自动扫射")
    # 压制是另一个动作，不能被当成普通攻击
    assert C.looks_like_attack("全自动扫射")
    assert not C.looks_like_attack("火力压制")


def test_suppression_pins_an_exposed_target(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    pc.ammo = 30
    pc.mag = 30
    C._save(conn, fight)

    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.ScriptedRoller([10, 1]), weapon="冲锋枪")
    assert "压制" in text
    assert "被压住了" in text
    loaded = C.load(conn, GID)
    assert C._slots(loaded)[enemy.owner_qq].must_cover is True
    me = next(c for c in loaded.combatants if c.owner_qq == pc.owner_qq)
    assert me.ammo == 20                        # 打光 10 发


def test_suppression_spares_someone_already_in_cover(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (2, 9))   # 紧贴吧台 = 躲进掩体
    pc.ammo = 30
    pc.mag = 30
    C._save(conn, fight)
    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.ScriptedRoller([10, 1]), weapon="冲锋枪")
    assert "没人被压住" in text
    assert C._slots(C.load(conn, GID))[enemy.owner_qq].must_cover is False


def test_suppression_spares_target_behind_a_blocker(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (5, 6))   # 吧台挡在中间
    pc.ammo = 30
    pc.mag = 30
    C._save(conn, fight)
    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.ScriptedRoller([10, 1]), weapon="冲锋枪")
    assert "没人被压住" in text


def test_suppression_ignores_targets_out_of_range(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (1, 10), (16, 1))  # 远超 25 米
    pc.ammo = 30
    pc.mag = 30
    C._save(conn, fight)
    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.ScriptedRoller([10, 1]), weapon="冲锋枪")
    assert "没人被压住" in text


def test_suppression_needs_an_automatic_weapon(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    C._save(conn, fight)
    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.RandomRoller(1), weapon="手枪")
    assert "没有全自动模式" in text
    loaded = C.load(conn, GID)
    assert C._slots(loaded)[pc.owner_qq].action is False   # 没浪费动作


def test_suppression_needs_ten_rounds(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    pc.ammo = 9
    C._save(conn, fight)
    text = C.suppress(conn, GID, pc.owner_qq,
                      roller=dice.RandomRoller(1), weapon="冲锋枪")
    assert "只剩 9 发" in text
    loaded = C.load(conn, GID)
    assert C._slots(loaded)[pc.owner_qq].action is False


def test_pinned_target_must_move_into_cover(conn):
    """被压住的人这一动只能往掩体里钻（full.txt:14911-14914）。"""
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    C._slots(fight)[pc.owner_qq].must_cover = True
    C._save(conn, fight)
    text = C.act(conn, GID, pc.owner_qq, "移动到 5,2", roller=dice.RandomRoller(1))
    assert text is not None
    assert "被火力压着" in text
    me = next(c for c in C.load(conn, GID).combatants if c.owner_qq == pc.owner_qq)
    assert (me.x, me.y) == (2, 10)               # 没动成


def test_pinned_target_can_break_free_by_reaching_cover(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    C._slots(fight)[pc.owner_qq].must_cover = True
    C._save(conn, fight)
    text = C.act(conn, GID, pc.owner_qq, "移动到 2,9", roller=dice.RandomRoller(1))
    assert "压制解除" in text
    loaded = C.load(conn, GID)
    assert C._slots(loaded)[pc.owner_qq].must_cover is False
    me = next(c for c in loaded.combatants if c.owner_qq == pc.owner_qq)
    assert (me.x, me.y) == (2, 9)                # 挪到吧台旁边


def test_suppression_target_keeps_flag_across_turns(conn):
    """压制标记不随回合清零：被打住的人下一轮还得先找掩体。"""
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (4, 2))
    sl = C._slots(fight)[enemy.owner_qq]
    sl.must_cover = True
    sl.reset()                                  # 新一轮只清 move/action/extra_move
    assert sl.must_cover is True
