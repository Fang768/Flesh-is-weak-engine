"""阶段 4：场景地图、距离 DV、视线、掩体、移动与 5×5 爆炸。"""

import pytest

from core import combat as C
from core import map as gridmap
from core import roster, session
from core.db import connect
from cyberpunk import dice

GID = "qq:map1"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _camp(conn, scene):
    gate = session.Gate(conn)
    camp = gate.start_new(GID, "地图测试", "opera")
    gate.set_scene(camp.id, scene)
    return camp


def _fight(conn, scene="a1.opera", template="独狼", name="阿飞"):
    roster.save(conn, GID, "u1", roster.build(template=template, name=name))
    camp = _camp(conn, scene)
    C.start(
        conn,
        GID,
        campaign_id=camp.id,
        scene=scene,
        enemies=[{"name": "小丑", "count": 1, "faction": "clown", "label": "小丑"}],
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


def test_maps_load_with_spawns_and_covers():
    sizes = {
        "opera_hall": (18, 12),
        "church_gate": (16, 10),
        "church_nave": (18, 12),
        "boss_crypt": (16, 12),
    }
    for map_id, (w, h) in sizes.items():
        game = gridmap.load(map_id)
        assert (game.width, game.height) == (w, h)
        for spot in game.spawns.values():
            assert game.walkable(spot)
        for cover in game.covers:
            assert game.walkable((cover.x, cover.y))


def test_start_places_everyone_on_scene_map(conn):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    roster.save(conn, GID, "u2", roster.build(template="独狼", name="老猫"))
    camp = _camp(conn, "a1.opera")
    C.start_text(conn, GID, "", campaign_id=camp.id, scene="a1.opera")
    fight = C.load(conn, GID)
    assert fight.map_id == "opera_hall"
    spots = [(c.x, c.y) for c in fight.combatants]
    assert all(x >= 0 and y >= 0 for x, y in spots)
    assert len(set(spots)) == len(spots)
    text = C.map_text(conn, GID, "u1")
    assert "@" in text and "E" in text


def test_ranged_attack_uses_distance_dv(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (5, 10))
    text = C.attack(conn, GID, pc.owner_qq, "", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3, 1, 1, 1, 1]))
    assert "6 米" in text
    assert "DV 13" in text
    assert "命中" in text


def test_line_of_sight_blocked(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (8, 10), (8, 1))
    text = C.attack(conn, GID, pc.owner_qq, "", skill="手枪",
                    roller=dice.ScriptedRoller([10, 1, 1, 1, 1]))
    assert "视线被墙" in text


def test_melee_needs_adjacency(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (7, 10))
    text = C.attack(conn, GID, pc.owner_qq, "", skill="近战武器",
                    roller=dice.ScriptedRoller([10, 1, 1, 1, 1]))
    assert "够不着" in text


def test_cover_takes_damage(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    text = C.attack(conn, GID, pc.owner_qq, "吧台", skill="手枪",
                    roller=dice.ScriptedRoller([10, 3, 3]))
    assert "掩体掉了 6 点耐久" in text
    assert C.load(conn, GID).covers["bar"] == 24


def test_move_command_updates_position(conn):
    fight = _fight(conn)
    pc, enemy = _pc_enemy(fight)
    _stand(conn, fight, pc, enemy, (2, 10), (8, 1))
    text = C.act(conn, GID, pc.owner_qq, "移动到 4,8", roller=dice.RandomRoller(2))
    assert "移动到 (4,8)" in text
    loaded = C.load(conn, GID)
    me = next(c for c in loaded.combatants if c.owner_qq == pc.owner_qq)
    assert (me.x, me.y) == (4, 8)


def test_self_destruct_only_hits_5x5(conn):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    roster.save(conn, GID, "u2", roster.build(template="独狼", name="老猫"))
    camp = _camp(conn, "a1.opera")
    C.start(conn, GID, campaign_id=camp.id, scene="a1.opera",
            enemies=[{"name": "小丑", "count": 1, "faction": "clown"}],
            roller=dice.RandomRoller(3))
    fight = C.load(conn, GID)
    pcs = [c for c in fight.combatants if c.side == "pc"]
    enemy = next(c for c in fight.combatants if c.side != "pc")
    enemy.x, enemy.y = (8, 2)
    pcs[0].x, pcs[0].y = (8, 4)
    pcs[1].x, pcs[1].y = (2, 10)
    near_hp, far_hp = pcs[0].hp, pcs[1].hp
    lines: list[str] = []
    script = {"on_flee_fail": {"self_destruct": {"weapon": "手雷", "damage": "2D6"}}}
    assert C._self_destruct(conn, fight, enemy, script,
                            dice.ScriptedRoller([6, 6]), lines)
    assert any("5×5" in line for line in lines)
    assert pcs[0].hp < near_hp
    assert pcs[1].hp == far_hp
