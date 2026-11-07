"""EB 与人情账本：结算幂等、自然语言确认层、组织/个人范围、/人情。"""

import asyncio

import pytest

from core import db, easy_roster, growth, story, turn_flow
from core.session import Gate
from cyberpunk import easy


@pytest.fixture()
def opera(monkeypatch):
    c = db.connect(":memory:")
    gate = Gate(c)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.resume("g1")
    easy_roster.save(c, "g1", "u1", easy.templates()[0])
    monkeypatch.setattr(turn_flow, "CONN", c)
    monkeypatch.setattr(turn_flow, "GATE", gate)
    turn_flow._PENDING.clear()
    yield c, gate, camp
    turn_flow._PENDING.clear()
    c.close()


def _card(c):
    return easy_roster.get(c, "g1", "u1")


def _save(c, card):
    easy_roster.save(c, "g1", "u1", card)


def test_ledger_grant_and_scope():
    card = {"name": "测试"}
    growth.grant_eb(card, 120)
    entry = growth.grant_favor(
        card, holder="org:爱乐吸血鬼", holder_name="爱乐吸血鬼",
        scope="org", org="爱乐吸血鬼", note="救了露西",
    )
    assert growth.eb(card) == 120
    assert entry["used"] is False

    usable, mismatched = growth.favors_for(
        card, {"name": "雷菲尔德", "affiliations": ["爱乐吸血鬼"]}
    )
    assert [f["id"] for f in usable] == [entry["id"]]
    assert mismatched == []

    growth.use_favor(card, entry["id"])
    assert growth.favors(card)[0]["used"] is True
    with pytest.raises(growth.GrowthError):
        growth.use_favor(card, entry["id"])


def test_settle_is_idempotent():
    card = {"name": "测试"}
    rewards = {"eb": 500, "ip": 2}
    first = growth.settle(card, "c:scene", rewards)
    assert "+500 EB" in first and "+2 IP" in first
    assert growth.settle(card, "c:scene", rewards) == []
    assert growth.eb(card) == 500 and growth.state(card)["ip"] == 2


def test_scene_reward_only_once(opera):
    c, gate, camp = opera
    line = turn_flow._apply_rewards("g1", camp, "opera", "a1.students")
    assert "500 EB" in line
    assert turn_flow._apply_rewards("g1", camp, "opera", "a1.students") == ""
    assert growth.eb(_card(c)) == 500


def test_ending_rewards_and_org_favor(opera):
    c, gate, camp = opera
    line = turn_flow._apply_rewards("g1", camp, "opera", "a3.ending")
    assert "1500 EB" in line and "4 IP" in line and "人情" in line
    card = _card(c)
    assert growth.eb(card) == 1500
    usable, _ = growth.favors_for(
        card, {"name": "雷菲尔德", "affiliations": ["爱乐吸血鬼"]}
    )
    assert len(usable) == 1


def test_natural_language_uses_person_favor(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")
    card = _card(c)
    growth.grant_favor(card, holder="学生", holder_name="学生", scope="person",
                       note="帮她改过论文")
    _save(c, card)

    msg = asyncio.run(turn_flow.handle("g1", "u1", "学生，你欠我一份人情"))
    assert "认了这笔账" in msg
    assert _card(c)["state"]["favors"][0]["used"] is True


def test_scope_mismatch_needs_confirm(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")
    card = _card(c)
    growth.grant_favor(card, holder="org:爱乐吸血鬼", holder_name="爱乐吸血鬼",
                       scope="org", org="爱乐吸血鬼")
    _save(c, card)

    msg = asyncio.run(turn_flow.handle("g1", "u1", "学生，还我的人情"))
    assert "对不上" in msg and "确认" in msg
    assert growth.favors(_card(c))[0]["used"] is False

    confirm = asyncio.run(turn_flow.handle("g1", "u1", "确认"))
    assert "不是我欠你的" in confirm
    assert growth.favors(_card(c))[0]["used"] is False


def test_missing_favor_confirm_and_cancel(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")

    msg = asyncio.run(turn_flow.handle("g1", "u1", "学生，你欠我人情吧"))
    assert "账上没有这笔人情" in msg
    assert "确认" in msg

    assert "取消了" in asyncio.run(turn_flow.handle("g1", "u1", "算了"))
    assert "g1" not in turn_flow._PENDING

    again = asyncio.run(turn_flow.handle("g1", "u1", "学生，人情"))
    assert "账上没有" in again
    done = asyncio.run(turn_flow.handle("g1", "u1", "确认"))
    assert "什么人情" in done


def test_favor_status_and_slash_command(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")
    card = _card(c)
    growth.grant_eb(card, 77)
    growth.grant_favor(card, holder="学生", holder_name="学生", scope="person")
    _save(c, card)

    status = turn_flow.favor_status("g1", "u1")
    assert "EB 77" in status and "学生" in status and "可用" in status

    used = turn_flow.use_favor_command("g1", "u1", "学生")
    assert "认了这笔账" in used
    assert "已用" in turn_flow.favor_status("g1", "u1")


def test_favor_forbidden_in_combat(opera):
    c, gate, camp = opera
    gate.set_scene(camp.id, "a1.students")
    from core import combat

    combat.start(c, "g1", campaign_id=camp.id, scene="a1.students",
                 enemies=(("打手", 1),))
    msg = asyncio.run(turn_flow.handle("g1", "u1", "学生，人情"))
    assert "战斗里没时间" in msg
