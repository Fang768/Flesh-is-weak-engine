"""存量卡起始资金迁移（幂等）。

背景：RED 起始资金 ``full.txt:8631``（街头鼠辈/边缘行者额外 500 EB）在
2026-11-10 才接进引擎，之前建的卡 ``state.eb`` 是空的，等于一件义体都装不起。

迁移必须**幂等**且不能刷钱，判据是 ``state.eb_granted`` 标记而不是当前余额：
玩家把钱花光后余额也是 0，用余额判断会每次启动都补 500。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core import db, easy_roster, growth, roster, runtime
from cyberpunk import dice, easy

GID = "qq:migrate"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _old_card(conn, qq="u1", name="老卡", state=None):
    """模拟旧库：直接写 state，绕过建卡时的 init_starting_eb。"""
    card = roster.build(template="独狼", name=name)
    card["state"] = state if state is not None else {}
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


# ---------- 纯函数层 ----------


def test_migrate_grants_once():
    card = {"name": "旧", "state": {}}
    assert growth.migrate_starting_eb([card]) == ["旧"]
    assert card["state"]["eb"] == growth.STARTING_EB
    assert card["state"]["eb_granted"] == growth.STARTING_EB


def test_migrate_is_idempotent():
    card = {"name": "旧", "state": {}}
    growth.migrate_starting_eb([card])
    assert growth.migrate_starting_eb([card]) == []
    assert card["state"]["eb"] == growth.STARTING_EB


def test_migrate_does_not_refund_spent_money():
    """关键：花光了钱（eb=0）但标记在 → 不再补发。"""
    card = {"name": "花光", "state": {"eb": 0, "eb_granted": 500}}
    assert growth.migrate_starting_eb([card]) == []
    assert card["state"]["eb"] == 0


def test_migrate_keeps_earned_money():
    """自己挣过钱的卡：只打标记，不覆盖、不加钱。"""
    card = {"name": "自挣", "state": {"eb": 800}}
    assert growth.migrate_starting_eb([card]) == []
    assert card["state"]["eb"] == 800
    assert card["state"]["eb_granted"] == 800


def test_migrate_skips_empty_list():
    assert growth.migrate_starting_eb([]) == []


# ---------- 建卡路径同样幂等 ----------


def test_init_starting_eb_does_not_double_grant():
    card = {"name": "新", "state": {}}
    assert growth.init_starting_eb(card) == 500
    assert growth.init_starting_eb(card) == 500
    card["state"]["eb"] = 0          # 花光
    assert growth.init_starting_eb(card) == 0   # 不再补


# ---------- 落库层 ----------


def test_runtime_migrates_red_card(conn):
    _old_card(conn)
    names = runtime.migrate_starting_eb(conn)
    assert names == ["老卡"]
    after = roster.get(conn, GID, "u1")
    assert growth.eb(after) == 500
    assert after["state"]["eb_granted"] == 500


def test_runtime_migration_is_idempotent(conn):
    _old_card(conn)
    runtime.migrate_starting_eb(conn)
    assert runtime.migrate_starting_eb(conn) == []
    assert growth.eb(roster.get(conn, GID, "u1")) == 500


def test_runtime_migrates_easy_card(conn):
    tpl = dict(easy.templates()[0])
    tpl.pop("state", None)           # 旧库没有 state
    easy_roster.save(conn, GID, "e1", tpl, "肆拾")
    names = runtime.migrate_starting_eb(conn)
    assert "肆拾" in names
    assert growth.eb(easy_roster.get(conn, GID, "e1")) == 500


def test_migrated_card_can_actually_buy():
    """迁移的最终目的：老卡装得起义体了。"""
    conn = db.connect(":memory:")
    _old_card(conn)
    runtime.migrate_starting_eb(conn)
    from core.cyber_flow import install

    text = install(conn, GID, "u1", "安装 赛博义眼", roller=dice.ScriptedRoller([5, 5]))
    assert "余额 400" in text
    conn.close()


def test_run_startup_tasks_never_raises_on_bad_db():
    """迁移失败不能挡住启动。"""
    bad = sqlite3.connect(":memory:")   # 没有表
    runtime.run_startup_tasks(bad)       # 不抛异常即通过
    bad.close()


def test_eb_granted_is_in_blank_state():
    assert "eb_granted" in growth.blank_state()
    assert growth.blank_state()["eb_granted"] == 0
