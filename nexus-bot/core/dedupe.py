"""事件去重。

QQ 官方网关在网络抖动/超时时会**重推同一条事件**，而我们每条消息都可能掷骰、
扣血、推进场景——重推就会双倍结算。所以进来的每条消息先过一道登记：
``seen()`` 返回 ``True`` 表示“这条已经处理过”，调用方直接静默丢弃。
"""

from __future__ import annotations

from core.db import now

TTL_DAYS = 2


def seen(conn, key: str) -> bool:
    """登记一条事件；返回 True 表示之前已经见过（重复推送）。"""
    if not key:
        return False
    row = conn.execute("SELECT 1 FROM seen_events WHERE key = ?", (str(key),)).fetchone()
    if row is not None:
        return True
    conn.execute(
        "INSERT OR IGNORE INTO seen_events (key, created_at) VALUES (?, ?)",
        (str(key), now()),
    )
    conn.commit()
    return False


def prune(conn, *, days: int = TTL_DAYS) -> int:
    cur = conn.execute(
        "DELETE FROM seen_events WHERE created_at < datetime('now', 'localtime', ?)",
        (f"-{int(days)} days",),
    )
    conn.commit()
    return cur.rowcount or 0
