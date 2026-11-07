"""《歌剧院之夜》跑到结束：a3 三场铺场、胜负钩子、休整/装备命令、战斗拦检定。"""

import asyncio
import json

import pytest

from core import combat, db, easy_roster, story, turn_flow
from core.session import Gate, Mode
from cyberpunk import dice, easy


@pytest.fixture()
def opera(monkeypatch):
    c = db.connect(":memory:")
    gate = Gate(c)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.resume("g1")
    easy_roster.save(c, "g1", "u1", easy.templates()[0])
    monkeypatch.setattr(turn_flow, "CONN", c)
    monkeypatch.setattr(turn_flow, "GATE", gate)
    yield c, gate, camp
    c.close()


def _end_combat(c, *, win: bool):
    if win:
        c.execute(
            "UPDATE combatants SET hp = 0, dead = 1 WHERE group_id = 'g1' AND side != 'pc'"
        )
    else:
        c.execute(
            "UPDATE combatants SET hp = 0, dead = 1 WHERE group_id = 'g1' AND side = 'pc'"
        )
    c.execute("UPDATE combats SET active = 0 WHERE group_id = 'g1'")
    c.commit()


def test_a3_encounters_fixed(opera):
    data = story.load("opera")
    entry = data["scenes"]["a3.entry"]["encounter"]
    assert entry["on_win"] == "a3.church"
    assert entry["on_lose"] == "ending:opera_dead"
    assert [g.get("ref") for g in entry["groups"] if g.get("ref")] == [
        "opera_clown", "opera_monk"]
    assert any(str(g.get("count", "")).startswith("=pc-2") for g in entry["groups"])

    expected = {
        "a3.church": ("自动炮塔", 2, "a3.boss"),
        "a3.boss": ("鲁思文勋爵", 1, "a3.ending"),
    }
    for sid, (enemy, count, on_win) in expected.items():
        enc = data["scenes"][sid]["encounter"]
        assert enc["groups"][0]["enemy"] == enemy
        assert enc["groups"][0]["count"] == count
        assert enc["on_win"] == on_win
        assert enc["on_lose"] == "ending:opera_dead"
    assert data["scenes"]["a1.opera"]["encounter"]["on_lose"] == "ending:opera_dead"


def test_win_moves_scene_and_ends(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a3.boss")
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a3.boss",
        enemies=(("鲁思文勋爵", 1),), roller=dice.RandomRoller(),
    )
    _end_combat(c, win=True)
    tail = turn_flow._aftermath("g1", camp, "opera")
    assert "📍 尾声：无菌走廊" in tail
    assert "🏁" in tail
    assert gate.state("g1").mode is Mode.IDLE


def test_lose_ends_campaign(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a3.entry")
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a3.entry",
        enemies=(("博佐帮蠢蛋", 2),), roller=dice.RandomRoller(),
    )
    _end_combat(c, win=False)
    tail = turn_flow._aftermath("g1", camp, "opera")
    assert "🏁" in tail
    assert gate.state("g1").mode is Mode.IDLE


def test_rest_heals_easy_card(opera):
    c, gate, camp = opera
    data = easy_roster.get(c, "g1", "u1")
    max_hp = data["hp"]
    data["hp"] = 3
    c.execute(
        "UPDATE easy_cards SET data = ? WHERE group_id = 'g1' AND owner_qq = 'u1'",
        (json.dumps(data, ensure_ascii=False),),
    )
    c.commit()
    msg = asyncio.run(turn_flow.handle("g1", "u1", "休整"))
    assert "休整完毕" in msg
    assert easy_roster.get(c, "g1", "u1")["hp"] == max_hp


def test_rest_blocked_in_combat(opera):
    c, gate, camp = opera
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a1.hook",
        enemies=(("打手", 1),), roller=dice.RandomRoller(),
    )
    msg = asyncio.run(turn_flow.handle("g1", "u1", "治疗"))
    assert "战斗还没结束" in msg


def test_equip_adds_gear(opera):
    c, gate, camp = opera
    msg = asyncio.run(turn_flow.handle("g1", "u1", "装备 水平阻尼降噪"))
    assert "🎒" in msg
    assert "水平阻尼降噪" in easy_roster.get(c, "g1", "u1")["gear"]


def test_combat_blocks_plain_checks(opera):
    c, gate, camp = opera
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a1.hook",
        enemies=(("打手", 1),), roller=dice.RandomRoller(),
    )
    msg = asyncio.run(turn_flow.handle("g1", "u1", "我看看四周"))
    assert "战斗还没结束" in msg


def test_net_access_then_control_stops_turrets(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a3.church")
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a3.church",
        enemies=(("自动炮塔", 2),), roller=dice.RandomRoller(),
    )
    first = combat.net_action(c, "g1", "u1", "接入接线盒",
                              roller=dice.ScriptedRoller([10]))
    assert "接入成功" in first
    connected = combat.load(c, "g1").net["u1"]
    assert connected["connected"] is True

    second = combat.net_action(c, "g1", "u1", "切断电缆",
                               roller=dice.ScriptedRoller([10]))
    assert "熄火停机" in second
    assert combat.active(c, "g1") is None
    # 网关 /攻击 走的是同一个公开入口
    tail = turn_flow.aftermath("g1")
    assert "📍 鲁思文勋爵" in tail


def test_hack_ignores_other_text(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a3.church")
    combat.start(
        c, "g1", campaign_id=camp.id, scene="a3.church",
        enemies=(("自动炮塔", 1),), roller=dice.RandomRoller(),
    )
    assert combat.net_action(c, "g1", "u1", "我看看四周") is None
