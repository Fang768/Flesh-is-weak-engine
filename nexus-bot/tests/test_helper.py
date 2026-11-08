"""多人协作检定（补充技能判定 full.txt:11811-11817）测试。

覆盖：
- helper_text 成功给队友挂 +1（辅助者自己先打互补检定）
- 队友名找不到 / 协助自己 → 错误提示
- 已挂 +1 未用再协助 → 不叠加
- card_flow.check（/检定 命令）消费 +1，且只消费一次
- turn_flow.handle 主路径消费 +1（普通行动）
"""

import asyncio

import pytest

from core import card_flow, roster, turn_flow
from core.db import connect
from core.session import Campaign, Mode
from cyberpunk import dice

GID = "qq:g1"
HELPER = "1001"   # 助人者
DONEE = "2002"    # 被协助者


@pytest.fixture()
def conn():
    return connect(":memory:")


def _hero(conn, qq, name, skill="手枪", level=4):
    card = roster.build(template="独狼", name=name,
                        skill_overrides={skill: level})
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def test_helper_hangs_bonus_on_donee_success(conn, monkeypatch):
    """辅助者打一次补充检定成功 → 给队友挂 +1。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美")
    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(card_flow, "CONN", conn)
    reply = turn_flow.helper_text(
        GID, HELPER, "协助 小美 手枪", roller=dice.ScriptedRoller([10, 2])
    )
    assert reply and "已给「小美」" in reply
    # 挂上加值：bonus=1
    key = (GID, DONEE)
    assert key in turn_flow.HELPER_BONUS
    assert turn_flow.HELPER_BONUS[key]["bonus"] == 1


def test_helper_missing_donee_or_self(conn, monkeypatch):
    """找不到队友 / 协助自己 → 提示，不挂加值。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美")
    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(card_flow, "CONN", conn)
    # 找不到队友
    reply = turn_flow.helper_text(GID, HELPER, "协助 路人甲 手枪",
                                  roller=dice.ScriptedRoller([10, 2]))
    assert reply and "没找到" in reply
    # 协助自己
    reply = turn_flow.helper_text(GID, HELPER, "协助 阿飞 手枪",
                                  roller=dice.ScriptedRoller([10, 2]))
    assert reply and ("自己" in reply or "没找到" in reply)


def test_helper_no_overlap(conn, monkeypatch):
    """已挂 +1 未用 → 再协助被拒绝（不叠加）。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美")
    monkeypatch.setattr(turn_flow, "CONN", conn)
    monkeypatch.setattr(card_flow, "CONN", conn)
    turn_flow.HELPER_BONUS[(GID, DONEE)] = {"bonus": 1, "skill": "手枪", "source": "阿飞"}
    reply = turn_flow.helper_text(GID, HELPER, "协助 小美 手枪",
                                  roller=dice.ScriptedRoller([10, 2]))
    assert reply and "不叠加" in reply
    assert turn_flow.HELPER_BONUS[(GID, DONEE)]["bonus"] == 1


def test_check_consumes_helper_bonus(conn, monkeypatch):
    """挂 +1 后拿 /检定 → +1 计入总计，且一次用完。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美", skill="觉察", level=1)
    monkeypatch.setattr(card_flow, "CONN", conn)
    monkeypatch.setattr(turn_flow, "CONN", conn)
    from core import turn_flow as tf
    turn_flow.HELPER_BONUS[(GID, DONEE)] = {"bonus": 1, "skill": "手枪", "source": "阿飞"}
    # 直接消费：第一次检定 total = natural + stat + level + bonus
    reply = card_flow.check(GID, DONEE, "觉察", dv=10,
                            roller=dice.ScriptedRoller([5]))
    assert "+1" in reply or "补充技能" in reply
    # 加值已消费：HELPER_BONUS 空了
    assert (GID, DONEE) not in tf.HELPER_BONUS


def test_handle_main_path_consumes_helper(conn, monkeypatch):
    """turn_flow 主路径：挂着 +1 的玩家普通行动也消费掉。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美", skill="觉察", level=1)

    conn.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (1, '测试', 'active', 'act1.dock', 'demo', '', '')"
    )
    conn.commit()
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

    turn_flow.HELPER_BONUS[(GID, DONEE)] = {"bonus": 1, "skill": "觉察", "source": "阿飞"}

    # 玩家做一次普通行动（调查）→ 应消费 +1
    text = asyncio.run(
        turn_flow.handle(GID, DONEE, "我小心环视四周，看看有没有埋伏",
                         roller=dice.ScriptedRoller([8, 1]))
    )
    assert text
    # 这走 turn.decide 链路，天然会消费；验证加值被取走
    assert (GID, DONEE) not in turn_flow.HELPER_BONUS


def test_in_turn_mentions_help_is_not_intercepted(conn, monkeypatch):
    """戏内台词中段提到「支援/帮忙」不应被当成协助命令拦截。"""
    _hero(conn, HELPER, "阿飞")
    _hero(conn, DONEE, "小美", skill="觉察", level=1)
    monkeypatch.setattr(turn_flow, "CONN", conn)

    # 中段提到「支援」：helper_text 返回 None → 交给后面正常处理
    reply = turn_flow.helper_text(GID, DONEE, "我需要你们的支援！",
                                  roller=dice.ScriptedRoller([8, 1]))
    assert reply is None
    # 句首没说要协助的「你们来支援我」也不拦截
    assert turn_flow.helper_text(GID, DONEE, "你们来支援我",
                                 roller=dice.ScriptedRoller([8, 1])) is None
    # 句首明确的「协助」才给提示
    assert turn_flow.helper_text(GID, HELPER, "协助",
                                 roller=dice.ScriptedRoller([8, 1])) is not None