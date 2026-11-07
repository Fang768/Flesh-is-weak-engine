"""事件流：把「这局发生了什么」记成一行一行的账。

用途：
    * 暂停/恢复时能说出「上次你们停在码头，正被两个打手盯着」
    * 复盘、查账、给 LLM 当上下文（只喂最近 N 条，不喂全库）
"""

from __future__ import annotations

from datetime import datetime, timezone

KINDS = (
    "start",
    "pause",
    "resume",
    "end",
    "scene",
    "roll",
    "damage",
    "award",
    "note",
    "chat",
    "clue",
    "npc",
)

#: 只是「听见了」的闲聊记录：留存，但**绝不进模型上下文**
CHAT_KIND = "chat"
CONTEXT_KINDS = tuple(k for k in KINDS if k != CHAT_KIND)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    conn,
    *,
    group_id: str,
    kind: str,
    summary: str,
    campaign_id: int | None = None,
    actor: str = "",
    data: dict | None = None,
) -> int:
    """写一条事件，返回 id。``kind`` 不在 KINDS 里也不拒（留扩展余地）。"""
    import json

    cur = conn.execute(
        "INSERT INTO events (campaign_id, group_id, kind, actor, summary, data, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            str(group_id),
            kind,
            str(actor or ""),
            summary.strip()[:500],
            json.dumps(data or {}, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def recent(conn, group_id: str, limit: int = 5, *, kinds=None) -> list[dict]:
    """最近事件。``kinds`` 为空时**排除闲聊**（闲聊不进上下文）。"""
    kinds = tuple(kinds) if kinds else CONTEXT_KINDS
    marks = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"SELECT kind, actor, summary, created_at FROM events"
        f" WHERE group_id=? AND kind IN ({marks}) ORDER BY id DESC LIMIT ?",
        (str(group_id), *kinds, int(limit)),
    ).fetchall()
    return [
        {"kind": r[0], "actor": r[1], "summary": r[2], "at": r[3]} for r in reversed(rows)
    ]


def last_summary(conn, group_id: str, *, kinds=None) -> str | None:
    kinds = tuple(kinds) if kinds else CONTEXT_KINDS
    marks = ",".join("?" * len(kinds))
    row = conn.execute(
        f"SELECT summary FROM events WHERE group_id=? AND kind IN ({marks})"
        f" ORDER BY id DESC LIMIT 1",
        (str(group_id), *kinds),
    ).fetchone()
    return row[0] if row else None


def prune(conn, *, per_group: int = 200, keep_days: int = 7) -> int:
    """清理闲聊流水：每个群只留最近 ``per_group`` 条、且不早于 ``keep_days`` 天。

    返回删除条数。跑团事件（start/scene/roll…）一律不动。
    """
    deleted = 0
    cur = conn.execute(
        "DELETE FROM events WHERE kind=?"
        " AND created_at < datetime('now', ?)",
        (CHAT_KIND, f"-{int(keep_days)} days"),
    )
    deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    cur = conn.execute(
        "DELETE FROM events WHERE kind=? AND id NOT IN ("
        "  SELECT id FROM events WHERE kind=? ORDER BY id DESC LIMIT ?",
        (CHAT_KIND, CHAT_KIND, int(per_group)),
    )
    deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return deleted


def render(events: list[dict]) -> str:
    if not events:
        return "还没有记录。"
    icon = {
        "start": "🚩",
        "pause": "⏸",
        "resume": "▶️",
        "end": "🏁",
        "scene": "📍",
        "roll": "🎲",
        "damage": "🩸",
        "award": "⭐",
        "note": "📝",
    }
    lines = []
    for ev in events:
        stamp = (ev.get("at") or "")[5:16].replace("T", " ")
        mark = icon.get(ev["kind"], "•")
        lines.append(f"{mark} {stamp} {ev['summary']}")
    return "\n".join(lines)
