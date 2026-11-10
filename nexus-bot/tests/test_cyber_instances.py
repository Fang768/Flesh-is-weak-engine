"""义体实例记录（instance model）：多实例基础组件的配件精确归属。

旧模型 ``state.cyber = ["赛博义眼","赛博义眼","图像增强"]`` 是扁平名字列表，
分不清配件挂在哪只眼/哪条臂上。新模型用**实例记录**：

    {"id": "c1", "name": "赛博义眼", "parent": ""}
    {"id": "c2", "name": "赛博义眼", "parent": ""}
    {"id": "c3", "name": "图像增强", "parent": "c1"}

容量按**每个基础实例**单独算；旧卡（纯字符串）读入时自动迁移。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import cyber_flow, growth, roster
from core.db import connect
from cyberpunk import cyber_bonus, dice, slots

GID = "qq:inst"


@pytest.fixture()
def conn():
    return connect(":memory:")


def _pc(conn, eb=50000):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)


def _records(conn):
    return roster.get(conn, GID, "u1")["state"]["cyber"]


# ── 迁移 ────────────────────────────────────────────────


def test_normalize_migrates_strings_and_assigns_parents():
    recs = slots.normalize(["赛博义眼", "赛博义眼", "图像增强", "显微目镜"])
    assert [r["name"] for r in recs] == ["赛博义眼", "赛博义眼", "图像增强", "显微目镜"]
    assert len({r["id"] for r in recs}) == 4
    eyes = [r for r in recs if r["name"] == "赛博义眼"]
    assert all(r["parent"] == "" for r in eyes)
    for r in recs:
        if r["name"] != "赛博义眼":
            assert r["parent"] in {e["id"] for e in eyes}


def test_normalize_keeps_existing_records():
    recs = [{"id": "a", "name": "赛博义眼", "parent": ""},
            {"id": "b", "name": "图像增强", "parent": "a"}]
    assert slots.normalize(recs) == recs


# ── 实例级容量 ──────────────────────────────────────────


def test_per_instance_capacity_not_shared(conn):
    """每只眼 3 槽：往同一只眼塞两件 2 槽的装不下，但另一只眼可以。"""
    _pc(conn)
    for _ in range(2):
        assert "装上了" in cyber_flow.install(conn, GID, "u1", "安装 赛博义眼",
                                              roller=dice.RandomRoller())
    recs = _records(conn)
    c1 = recs[0]["id"]
    # 第一件图像增强明确挂 c1
    assert "装上了" in cyber_flow.install(conn, GID, "u1", f"安装 图像增强 到 {c1}",
                                          roller=dice.RandomRoller())
    # 第二件还挂 c1：c1 只剩 1 槽，装不下
    text = cyber_flow.install(conn, GID, "u1", f"安装 图像增强 到 {c1}",
                              roller=dice.RandomRoller())
    assert "装不了" in text and "槽" in text, text
    # 不指定父级：自动挑另一只眼，能装
    text = cyber_flow.install(conn, GID, "u1", "安装 图像增强",
                              roller=dice.RandomRoller())
    assert "装上了" in text, text


def test_install_to_specific_parent(conn):
    _pc(conn)
    for _ in range(2):
        cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=dice.RandomRoller())
    recs = _records(conn)
    c2 = recs[1]["id"]
    cyber_flow.install(conn, GID, "u1", f"安装 显微目镜 到 {c2}",
                       roller=dice.RandomRoller())
    got = next(r for r in _records(conn) if r["name"] == "显微目镜")
    assert got["parent"] == c2


def test_install_bad_parent_rejected(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=dice.RandomRoller())
    # 拿手臂的 id 当义眼的父级 → 拒绝
    cyber_flow.install(conn, GID, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    arm = next(r for r in _records(conn) if r["name"] == "赛博手臂")
    text = cyber_flow.install(conn, GID, "u1", f"安装 显微目镜 到 {arm['id']}",
                              roller=dice.RandomRoller())
    assert "装不了" in text, text


# ── 卸载按实例 ──────────────────────────────────────────


def test_uninstall_blocked_by_attached_child(conn):
    _pc(conn)
    for _ in range(2):
        cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=dice.RandomRoller())
    cyber_flow.install(conn, GID, "u1", "安装 图像增强", roller=dice.RandomRoller())
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    # 只有挂着图像增强的那只眼被拦；这只眼恰好是卸载时命中的第一只
    assert "暂时卸不了" in text and "图像增强" in text


def test_uninstall_removes_one_instance_only(conn):
    _pc(conn)
    for _ in range(2):
        cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=dice.RandomRoller())
    assert len([r for r in _records(conn) if r["name"] == "赛博义眼"]) == 2
    cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    assert len([r for r in _records(conn) if r["name"] == "赛博义眼"]) == 1


# ── 面板 ────────────────────────────────────────────────


def test_describe_shows_per_instance():
    recs = [{"id": "c1", "name": "赛博义眼", "parent": ""},
            {"id": "c2", "name": "赛博义眼", "parent": ""},
            {"id": "c3", "name": "图像增强", "parent": "c1"}]
    lines = slots.describe_slots(recs)
    assert any("赛博义眼#1" in l for l in lines)
    assert any("赛博义眼#2" in l for l in lines)
    assert any("合计" in l for l in lines)


def test_installed_names_still_works_on_records():
    card = {"state": {"cyber": [{"id": "c1", "name": "赛博义眼", "parent": ""}]}}
    assert cyber_bonus.installed_names(card) == ["赛博义眼"]
    assert cyber_bonus.has_cyberware(card, "赛博义眼")
