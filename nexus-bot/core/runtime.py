"""进程内共享的运行时对象。

为什么要单独一个模块：``plugins/gate`` 和 ``plugins/character`` 各自 import 会拿到
**两份不同的** ``AttentionBook`` / ``Gate``，窗口状态就对不上了（曾经真的这样错过）。
所有插件都从这里取同一份实例。
"""

from __future__ import annotations

import sys

from core import db, easy_roster, growth, roster
from core.attention import AttentionBook
from core.session import Gate

GATE = Gate()
ATTENTION = AttentionBook()
CONN = db.connect()


def migrate_starting_eb(conn=None) -> list[str]:
    """给存量卡补发起始资金（幂等）。返回被补发的卡名。

    RED ``full.txt:8631``：街头鼠辈/边缘行者额外 500 EB。本引擎在 2026-11-10 之前
    建的卡都没有这笔钱，不补的话老玩家一件义体都装不起。

    幂等靠 ``state.eb_granted`` 标记（见 ``growth.migrate_starting_eb``）：
    花光了钱的卡不会被再补发。
    """
    conn = conn or CONN
    touched: list[str] = []

    for group_id, in conn.execute("SELECT DISTINCT group_id FROM characters"):
        cards = roster.members(conn, group_id)
        names = growth.migrate_starting_eb(cards)
        if not names:
            continue
        for card in cards:
            if str(card.get("name")) in names:
                roster.save(conn, group_id, card["owner_qq"], card)
        touched.extend(names)

    for group_id, in conn.execute("SELECT DISTINCT group_id FROM easy_cards"):
        cards = easy_roster.members(conn, group_id)
        names = growth.migrate_starting_eb(cards)
        if not names:
            continue
        for card in cards:
            owner = str(card.get("owner_qq", ""))
            if owner and str(card.get("name")) in names:
                easy_roster.save(conn, group_id, owner, card, card.get("name"))
        touched.extend(names)

    return touched


def run_startup_tasks(conn=None) -> None:
    """进程启动时跑一次的一次性任务（全部幂等）。"""
    try:
        names = migrate_starting_eb(conn)
    except Exception as exc:  # noqa: BLE001  迁移失败不能挡住启动
        print(f"[runtime] 起始资金迁移失败：{exc}", file=sys.stderr)
        return
    if names:
        print(f"[runtime] 已为 {len(names)} 张存量卡补发起始资金 "
              f"{growth.STARTING_EB} EB：{'、'.join(names)}")
