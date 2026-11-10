"""网络战：先接入、再黑入节点；动作槽、黑冰反噬。"""

import pytest

from core import combat, db, easy_roster, story, turn_flow
from core.session import Gate
from cyberpunk import dice, easy


@pytest.fixture()
def net(monkeypatch):
    c = db.connect(":memory:")
    gate = Gate(c)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.resume("g1")
    _card = dict(easy.templates()[0])
    _card["cyber"] = list(_card.get("cyber") or []) + ["交互接口"]  # 网行前置
    easy_roster.save(c, "g1", "u1", _card)
    monkeypatch.setattr(turn_flow, "CONN", c)
    monkeypatch.setattr(turn_flow, "GATE", gate)
    gate.set_scene(camp.id, "a3.church")
    yield c, gate, camp
    c.close()


def _force_pc_turn(c):
    loaded = combat.load(c, "g1")
    pc = next(x for x in loaded.order if x.side == "pc")
    c.execute("UPDATE combats SET turn_index = ? WHERE group_id = 'g1'",
              (loaded.order.index(pc),))
    c.commit()


def _net_spec():
    return story.load("opera")["scenes"]["a3.church"]["encounter"]["net"]


def test_net_spec_has_access_and_node(net):
    spec = _net_spec()
    assert spec["access"]["dv"] == 8
    node = spec["nodes"][0]
    assert node["effect"] == {"type": "stop_turrets", "target": "自动炮塔", "count": 2}
    assert node["ice"]["damage"] == "2D6"


def test_control_before_access_is_refused(net):
    c, gate, camp = net
    combat.start(c, "g1", campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 2),), roller=dice.RandomRoller())
    msg = combat.net_action(c, "g1", "u1", "切断电缆")
    assert "还没接入" in msg
    assert combat.active(c, "g1") is not None


def test_access_persists_then_control_stops_turrets(net):
    c, gate, camp = net
    combat.start(c, "g1", campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 2),), roller=dice.RandomRoller())

    first = combat.net_action(c, "g1", "u1", "接入接线盒",
                              roller=dice.ScriptedRoller([10]))
    assert "接入成功" in first
    assert combat.load(c, "g1").net["u1"]["connected"] is True

    _force_pc_turn(c)
    second = combat.net_action(c, "g1", "u1", "切断电缆",
                               roller=dice.ScriptedRoller([10]))
    assert "熄火停机" in second
    assert combat.active(c, "g1") is None


def test_ice_attack_hit_deals_2d6_after_failed_control(net):
    c, gate, camp = net
    combat.start(c, "g1", campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 1),), roller=dice.RandomRoller())
    combat.net_action(c, "g1", "u1", "接入接线盒",
                      roller=dice.ScriptedRoller([10]))
    before = next(x for x in combat.load(c, "g1").combatants if x.side == "pc").hp

    _force_pc_turn(c)
    # 控制失败(1) → 黑冰攻击 ATK6+3=9 vs 交互界面 4 → 命中 → 2D6=3+4
    msg = combat.net_action(c, "g1", "u1", "切断电缆",
                            roller=dice.ScriptedRoller([1, 3, 4, 3, 4]))
    assert "ATK 9 vs 你的交互界面 4" in msg
    assert "烧穿" in msg and "2D6 = 7" in msg
    after = next(x for x in combat.load(c, "g1").combatants if x.side == "pc").hp
    assert after == before - 7


def test_ice_attack_miss_leaves_hp_untouched(net):
    c, gate, camp = net
    combat.start(c, "g1", campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 1),), roller=dice.RandomRoller())
    combat.net_action(c, "g1", "u1", "接入接线盒",
                      roller=dice.ScriptedRoller([10]))
    before = next(x for x in combat.load(c, "g1").combatants if x.side == "pc").hp

    _force_pc_turn(c)
    msg = combat.net_action(c, "g1", "u1", "切断电缆",
                            roller=dice.ScriptedRoller([1, 1, 10]))
    assert "没咬到" in msg
    after = next(x for x in combat.load(c, "g1").combatants if x.side == "pc").hp
    assert after == before


def test_scene_without_net_ignores_network_text(net):
    c, gate, camp = net
    gate.set_scene(camp.id, "a3.boss")
    combat.start(c, "g1", campaign_id=camp.id, scene="a3.boss",
                 enemies=(("鲁思文勋爵", 1),), roller=dice.RandomRoller())
    assert combat.net_action(c, "g1", "u1", "接入接线盒") is None
