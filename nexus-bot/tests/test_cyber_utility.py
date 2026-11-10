"""功能型义体（2026-11-10）：飞爪手 / 肩部摄像头 / 快拆挂扣。

规则出处 ``rules/red_core/raw/full.txt``：

* 飞爪手 ``:28348-28360``：一个动作发射抓钩钉在 30 米内的厚实掩体上；攀爬这条
  绳索时无视攀爬移速惩罚；收回不花动作。
* 肩部摄像头 ``:28460-28471``：可隐藏无需检定；拔出/收纳不花费动作；纯叙事。
* 快拆挂扣 ``:10287-10291``：1 个动作拆/装赛博手臂；**重装你以前用过的臂不再扣
  人性**（首次装新臂无论有没有快拆挂扣都要扣）。

「约定」：引擎按**通用名**记账（`state.used_items`），没有唯一实例 ID；所以
「卸下过同类臂再装」会被免人性，DM 可对「买一条全新同名臂」的人工纠正。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import cyber_flow, growth, map as gridmap, roster
from core.db import connect
from cyberpunk import cyber_bonus, dice

GID = "qq:utility"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _pc(conn, eb=20000):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)
    return card


def _cyber(conn) -> list[str]:
    return cyber_bonus.installed_names(roster.get(conn, GID, "u1"))


def _hum(conn) -> int:
    card = roster.get(conn, GID, "u1")
    return int((card.get("state") or {}).get("hum")
               or card["stats"]["EMP"] * 10)


# ── 快拆挂扣 ─────────────────────────────────────────────


def test_quick_release_reinstall_does_not_cost_humanity(conn):
    """快拆挂扣记录过这条臂的 id → 重装免人性（full.txt:10287-10291）。"""
    _pc(conn)
    for item in ("赛博手臂", "快拆挂扣"):
        assert "装上了" in cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                                              roller=dice.RandomRoller())
    # 卸下手臂 → 收进快拆槽（带 id），不卖
    cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博手臂")
    hum_before = _hum(conn)
    text = cyber_flow.install(conn, GID, "u1", "安装 赛博手臂",
                              roller=dice.RandomRoller())
    assert "装上了" in text
    assert _hum(conn) == hum_before, text
    assert "id" in text or "快拆" in text, text


def test_quick_slots_store_and_reinstall_free(conn):
    """快拆挂扣自带 2 个「快拆槽」：卸下的臂收进去，重装免人性、免花钱。"""
    _pc(conn)
    for item in ("赛博手臂", "快拆挂扣"):
        assert "装上了" in cyber_flow.install(conn, GID, "u1", f"安装 {item}",
                                              roller=dice.RandomRoller())
    eb_before = growth.eb(roster.get(conn, GID, "u1"))
    hum_before = _hum(conn)
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博手臂")
    assert "收进快拆槽" in text, text
    card = roster.get(conn, GID, "u1")
    assert [e["name"] for e in card["state"]["quick_slots"]] == ["赛博手臂"]
    # 收进槽 = 不卖，余额不变
    assert growth.eb(card) == eb_before
    # 重装：从槽里取，免人性、免花钱
    text = cyber_flow.install(conn, GID, "u1", "安装 赛博手臂",
                              roller=dice.RandomRoller())
    assert "快拆槽" in text or "免人性" in text, text
    card = roster.get(conn, GID, "u1")
    assert card["state"]["quick_slots"] == []
    assert _hum(conn) == hum_before
    assert growth.eb(card) == eb_before


def test_without_quick_release_uninstall_sells(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博手臂")
    assert "收进快拆槽" not in text
    card = roster.get(conn, GID, "u1")
    assert not (card.get("state") or {}).get("quick_slots")


def test_reinstall_arm_without_quick_release_costs_again(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博手臂")
    hum_before = _hum(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    assert _hum(conn) < hum_before   # 没快拆挂扣：照常再扣


def test_quick_change_chip_reinstall_free(conn):
    """芯片类「本身就可以快速更换」（:536）→ 卸下再装不再扣人性。"""
    _pc(conn)
    for item in ("神经链路", "芯片插槽", "痛觉编辑器"):
        cyber_flow.install(conn, GID, "u1", f"安装 {item}", roller=dice.RandomRoller())
    cyber_flow.uninstall(conn, GID, "u1", "卸载 痛觉编辑器")
    hum_before = _hum(conn)
    text = cyber_flow.install(conn, GID, "u1", "安装 痛觉编辑器",
                              roller=dice.RandomRoller())
    assert "装上了" in text
    assert _hum(conn) == hum_before, text


# ── 肩部摄像头 ───────────────────────────────────────────


def test_shoulder_cam_in_panel():
    card = {"state": {"cyber": ["肩部摄像头"]}}
    lines = cyber_bonus.describe(card)
    assert any("肩部摄像头" in l for l in lines)


def test_shoulder_cam_helper():
    assert cyber_bonus.has_shoulder_cam({"state": {"cyber": ["肩部摄像头"]}})
    assert not cyber_bonus.has_shoulder_cam({})


# ── 飞爪手 ───────────────────────────────────────────────


def _grapple_map():
    return gridmap._parse({
        "id": "t", "name": "t", "terrain": [".........."],
        "cell": 2,
        "objects": [{"kind": "cover", "id": "crate", "name": "木箱",
                     "x": 6, "y": 0, "hp": 20, "blocks_los": True}],
    }, "t")


def _fight(conn, monkeypatch):
    test_map = _grapple_map()
    monkeypatch.setattr(C, "_map_for_id", lambda _id: test_map)
    _pc(conn)
    C.start(conn, GID, enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
    fight = C.load(conn, GID)
    pc = next(c for c in fight.order if c.side == "pc")
    pc.x, pc.y = 0, 0
    foe = next(c for c in fight.order if c.side != "pc")
    foe.x, foe.y = 9, 0
    fight.turn_index = fight.order.index(pc)
    C._save(conn, fight)
    return fight, pc


def _set_cyber(conn, *items):
    from cyberpunk import slots
    card = roster.get(conn, GID, "u1")
    state = dict(card.get("state") or {})
    recs = slots.normalize(list(items))
    state["cyber"] = recs
    # 这些臂当作「上过身」，id 记进快拆挂扣（真实安装也会这样记）
    state["quick_ids"] = [r["id"] for r in recs if r["name"] == "赛博手臂"]
    card["state"] = state
    roster.save(conn, GID, "u1", card)


def test_grapple_fire_sets_anchor(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "飞爪手")
    text = C.act(conn, GID, "u1", "发射抓钩 木箱")
    assert "抓钩" in text and "钉在" in text, text
    card = roster.get(conn, GID, "u1")
    assert (card.get("state") or {}).get("grapple"), card.get("state")


def test_grapple_fire_out_of_range(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "飞爪手")
    # 12 列 × 2 米 = 22 米内；把目标放到图外远处用坐标校验距离
    text = C.act(conn, GID, "u1", "发射抓钩 40,0")
    assert "太远" in text, text


def test_grapple_retract_is_free(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "飞爪手")
    C.act(conn, GID, "u1", "发射抓钩 木箱")
    text = C.act(conn, GID, "u1", "收回抓钩")
    assert "收回" in text and "不花费动作" in text, text
    card = roster.get(conn, GID, "u1")
    assert not (card.get("state") or {}).get("grapple")


def test_grapple_requires_cyberarm_and_hand(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn)  # 没装飞爪手
    text = C.act(conn, GID, "u1", "发射抓钩 木箱")
    assert "没装飞爪手" in text, text


# ── 快拆挂扣：一个动作换臂 ──────────────────────────────


def test_swap_arm_is_one_action(conn, monkeypatch):
    """有快拆挂扣 + 槽里有臂 →「换臂」一个动作完成，不扣人性。"""
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "快拆挂扣", "赛博手臂")
    hum0 = _hum(conn)
    # 把一条臂收进槽
    cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博手臂")
    text = C.act(conn, GID, "u1", "换臂")
    assert "换好了" in text and "一个动作" in text, text
    card = roster.get(conn, GID, "u1")
    assert [e["name"] for e in card["state"]["quick_slots"]] == ["赛博手臂"]
    assert _hum(conn) == hum0


def test_swap_arm_without_quick_release_rejected(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂")
    text = C.act(conn, GID, "u1", "换臂")
    assert "没装快拆挂扣" in text, text


def test_swap_arm_empty_slot_rejected(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "快拆挂扣")
    text = C.act(conn, GID, "u1", "换臂")
    assert "快拆槽里没有" in text, text


def test_swap_to_new_arm_costs_humanity(conn, monkeypatch):
    """槽里是全新臂（id 不在记录里）：换上去第一次上身要扣人性（full.txt:10287）。"""
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "快拆挂扣", "赛博手臂")
    card = roster.get(conn, GID, "u1")
    st = dict(card.get("state") or {})
    st["quick_slots"] = [{"name": "赛博手臂", "id": "brand-new-999"}]
    card["state"] = st
    roster.save(conn, GID, "u1", card)
    hum0 = _hum(conn)
    text = C.act(conn, GID, "u1", "换臂")
    assert "全新的臂" in text, text
    assert _hum(conn) < hum0
    # 换过之后这个 id 被记进快拆挂扣
    key = (roster.get(conn, GID, "u1").get("state") or {}).get("quick_ids") or []
    assert "brand-new-999" in key


def test_swap_to_used_arm_is_free(conn, monkeypatch):
    """槽里这条臂的 id 已在快拆挂扣记录里：换上去不再扣人性。"""
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "快拆挂扣", "赛博手臂")
    card = roster.get(conn, GID, "u1")
    arm = next(r for r in card["state"]["cyber"] if r["name"] == "赛博手臂")
    st = dict(card.get("state") or {})
    st["quick_slots"] = [{"name": "赛博手臂", "id": arm["id"]}]
    card["state"] = st
    roster.save(conn, GID, "u1", card)
    hum0 = _hum(conn)
    text = C.act(conn, GID, "u1", "换臂")
    assert "id 在记录里" in text, text
    assert _hum(conn) == hum0


def test_grapple_rope_removes_climb_penalty(conn, monkeypatch):
    _fight(conn, monkeypatch)
    _set_cyber(conn, "赛博手臂", "飞爪手")
    C.act(conn, GID, "u1", "发射抓钩 木箱")
    text = C.act(conn, GID, "u1", "攀爬绳索到 2,0")
    assert "〔攀爬〕" in text
    assert "飞爪手绳索" in text or "免移速惩罚" in text, text
