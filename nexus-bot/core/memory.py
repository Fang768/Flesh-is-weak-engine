"""NPC 记忆与 DM 牌面。

主人 2026-11-14 定下的分层（**按「何时被读」分区，不按「重不重要」**）：

| 层 | 表/字段 | 何时读 | 条数 |
|---|---|---|---|
| A 剧情标记 | ``plot_marks`` | **每次**调模型（进牌面） | 8（预算） |
| B 人际记忆 | ``npc_memory.relations`` | 只跟这个 NPC 有关时 | 最近 10 + 关键词补 5 |
| C 对话流水 | ``npc_memory.exchanges`` | 跟这个 NPC 说话 | 5（滚动丢） |

**为什么分两层而不是一层**（主人的原话）：

> 「玩家不是无情的推剧情工具，有一些问答的记忆是为了获取情报，可以滚动丢弃，
> 有些需要 NPC 作为一个真正的人类该记住的事情需要留下，尽管可能跟剧情无关，
> 这部分一般不作为上下文，只在玩家跟该 NPC 对话或者对话中涉及该 NPC 的时候调用。」

所以 B 层**不进牌面**——它再多也不会撑爆上下文。

**旧的不删**（决策 2）：B 层超过 10 条时不删，靠 ``keywords`` 硬检索补召回。
关键词要求 ≥2 字，沿用 ``core/clues.py`` 的经验（单字会误伤正常对话）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.db import now

#: 牌面预算（决策 1：暂定 8，测试后再调）
BOARD_MARKS = 8
#: B 层每次必给的最近条数（决策 2）
RECENT_RELATIONS = 10
#: 关键词补召回的额外条数（决策 2）
RECALL_EXTRA = 5
#: C 层对话流水保留条数（决策 A）
EXCHANGES_KEPT = 5
#: 关键词最短长度：单字容易误伤正常对话（同 core/clues.py:39）
MIN_KEYWORD = 2


# ── 存取 ────────────────────────────────────────────────────


def _rows(conn: sqlite3.Connection, sql: str, args: tuple) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def _load_json(raw: Any, default):
    try:
        val = json.loads(raw or "")
        return val if isinstance(val, type(default)) else default
    except (TypeError, ValueError):
        return default


# ── 剧情标记（A 层） ────────────────────────────────────────


def add_mark(conn: sqlite3.Connection, campaign_id: int, *, scene: str,
             what: str, weight: int = 1, keywords=(), mark_id: str = "") -> str:
    """写一条剧情标记（进牌面）。同一 ``mark_id`` 重复写不会重复计。"""
    text = str(what or "").strip()
    if not text:
        return ""
    m_id = str(mark_id or f"m:{scene}:{len(text)}:{text[:24]}")
    kw = [str(k) for k in keywords if str(k)]
    conn.execute(
        "INSERT OR REPLACE INTO plot_marks"
        " (campaign_id, mark_id, scene, what, weight, keywords, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(campaign_id), m_id, str(scene or ""), text[:160],
         max(1, int(weight or 1)), json.dumps(kw, ensure_ascii=False), now()),
    )
    conn.commit()
    return m_id


def plot_marks(conn: sqlite3.Connection, campaign_id: int,
               limit: int = BOARD_MARKS) -> list[dict]:
    """进牌面的剧情标记：按 weight 降序、同权按新旧，取前 ``limit`` 条。"""
    rows = _rows(
        conn,
        "SELECT mark_id, scene, what, weight, keywords, created_at"
        " FROM plot_marks WHERE campaign_id = ?"
        " ORDER BY weight DESC, rowid ASC",
        (int(campaign_id),),
    )
    out = []
    for row in rows[:max(0, int(limit))]:
        out.append({
            "id": row["mark_id"], "scene": row["scene"], "what": row["what"],
            "weight": int(row["weight"] or 1),
            "keywords": _load_json(row["keywords"], []),
        })
    return out


def mark_count(conn: sqlite3.Connection, campaign_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM plot_marks WHERE campaign_id = ?",
                       (int(campaign_id),)).fetchone()
    return int(row[0] if row else 0)


# ── NPC 记忆（B / C 层） ────────────────────────────────────


def blank_memory() -> dict:
    return {
        "met": 0,
        "attitude": 5,          # 0=厌恶 / 5=平淡 / 10=友好
        "bounds": [0, 10],
        "relations": [],
        "exchanges": [],
        "impression": [],       # 决策 3：留空位，先不写
    }


def load_memory(conn: sqlite3.Connection, campaign_id: int, npc_key: str) -> dict:
    row = conn.execute(
        "SELECT met, attitude, bounds, relations, exchanges, impression"
        " FROM npc_memory WHERE campaign_id = ? AND npc_key = ?",
        (int(campaign_id), str(npc_key)),
    ).fetchone()
    mem = blank_memory()
    if row is None:
        return mem
    mem["met"] = int(row["met"] or 0)
    mem["attitude"] = int(row["attitude"] or 0)
    mem["bounds"] = _load_json(row["bounds"], [0, 10])
    mem["relations"] = _load_json(row["relations"], [])
    mem["exchanges"] = _load_json(row["exchanges"], [])
    mem["impression"] = _load_json(row["impression"], [])
    return mem


def save_memory(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
                mem: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO npc_memory"
        " (campaign_id, npc_key, met, attitude, bounds, relations, exchanges,"
        "  impression, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            int(campaign_id), str(npc_key), int(mem.get("met") or 0),
            int(mem.get("attitude") if mem.get("attitude") is not None else 5),
            json.dumps(mem.get("bounds") or [0, 10], ensure_ascii=False),
            json.dumps(mem.get("relations") or [], ensure_ascii=False),
            json.dumps(mem.get("exchanges") or [], ensure_ascii=False),
            json.dumps(mem.get("impression") or [], ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()


def meet(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
         *, bounds=None) -> dict:
    """见一面：``met`` 置 1；``bounds`` 只在第一次确定时可写（决策 E）。"""
    mem = load_memory(conn, campaign_id, npc_key)
    mem["met"] = 1
    if bounds and mem.get("bounds") == [0, 10]:
        mem["bounds"] = list(bounds)
    save_memory(conn, campaign_id, npc_key, mem)
    return mem


def add_relation(conn: sqlite3.Connection, campaign_id: int, npc_key: str, *,
                 kind: str, what: str, delta: int = 0,
                 keywords=(), scene: str = "") -> dict:
    """写一条人际记忆（B 层）。**旧的不删**（决策 2）。

    ``delta`` 同时作用到态度上（由 ``core.attitude` 负责夹取与落库）。
    """
    text = str(what or "").strip()
    if not text:
        return load_memory(conn, campaign_id, npc_key)
    mem = load_memory(conn, campaign_id, npc_key)
    mem["relations"].append({
        "kind": str(kind or "tell"),
        "what": text[:160],
        "delta": int(delta or 0),
        "keywords": [str(k) for k in keywords if str(k)],
        "scene": str(scene or ""),
    })
    save_memory(conn, campaign_id, npc_key, mem)
    return mem


def add_exchange(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
                 speaker: str, text: str) -> list[dict]:
    """记一句对话流水（C 层），只保留最近 ``EXCHANGES_KEPT`` 条。"""
    mem = load_memory(conn, campaign_id, npc_key)
    rows = list(mem.get("exchanges") or [])
    rows.append({"who": str(speaker or ""), "text": str(text or "")[:200]})
    mem["exchanges"] = rows[-EXCHANGES_KEPT:]
    save_memory(conn, campaign_id, npc_key, mem)
    return mem["exchanges"]


def _hits(text: str, keywords) -> bool:
    body = str(text or "")
    if not body:
        return False
    for kw in keywords or []:
        word = str(kw)
        if len(word) >= MIN_KEYWORD and word in body:
            return True
    return False


def recall(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
           text: str) -> list[dict]:
    """这个 NPC 此刻该想起哪些人际记忆。

    最近 ``RECENT_RELATIONS`` 条必给；**更老的靠 keywords 硬检索补召回**
    （最多 ``RECALL_EXTRA`` 条），这正是「防止 NPC 健忘」的机制。
    """
    mem = load_memory(conn, campaign_id, npc_key)
    allrows = list(mem.get("relations") or [])
    recent = allrows[-RECENT_RELATIONS:]
    older = allrows[:-RECENT_RELATIONS]
    extra = [r for r in older if _hits(text, r.get("keywords"))]
    return recent + extra[-RECALL_EXTRA:]


def relations_of(conn: sqlite3.Connection, campaign_id: int,
                 npc_key: str) -> list[dict]:
    """这个 NPC 的全部人际记忆（不截断，供面板/测试用）。"""
    return list((load_memory(conn, campaign_id, npc_key).get("relations") or []))


# ── 场景进度（决策 D / G） ──────────────────────────────────


def blank_progress() -> dict:
    return {"clues_found": [], "npcs_talked": [], "gates_passed": [],
            "off_track": 0, "nudged": []}


def load_progress(conn: sqlite3.Connection, campaign_id: int,
                  scene: str) -> dict:
    row = conn.execute(
        "SELECT clues_found, npcs_talked, gates_passed, off_track, nudged"
        " FROM scene_progress WHERE campaign_id = ? AND scene = ?",
        (int(campaign_id), str(scene or "")),
    ).fetchone()
    prog = blank_progress()
    if row is None:
        return prog
    prog["clues_found"] = _load_json(row["clues_found"], [])
    prog["npcs_talked"] = _load_json(row["npcs_talked"], [])
    prog["gates_passed"] = _load_json(row["gates_passed"], [])
    prog["off_track"] = int(row["off_track"] or 0)
    prog["nudged"] = _load_json(row["nudged"], [])
    return prog


def save_progress(conn: sqlite3.Connection, campaign_id: int, scene: str,
                  prog: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO scene_progress"
        " (campaign_id, scene, clues_found, npcs_talked, gates_passed,"
        "  off_track, nudged, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            int(campaign_id), str(scene or ""),
            json.dumps(prog.get("clues_found") or [], ensure_ascii=False),
            json.dumps(prog.get("npcs_talked") or [], ensure_ascii=False),
            json.dumps(prog.get("gates_passed") or [], ensure_ascii=False),
            int(prog.get("off_track") or 0),
            json.dumps(prog.get("nudged") or [], ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()


def remember_in_scene(conn: sqlite3.Connection, campaign_id: int, scene: str,
                      field: str, value: str) -> dict:
    """把「这场里做过的某事」记进进度（clue / npc / gate）。"""
    prog = load_progress(conn, campaign_id, scene)
    bucket = list(prog.get(field) or [])
    if value and value not in bucket:
        bucket.append(value)
    prog[field] = bucket
    save_progress(conn, campaign_id, scene, prog)
    return prog


# ── 牌面组装（决策 1：8 条预算，悬而未决不占） ──────────────


def board(conn: sqlite3.Connection, campaign_id: int, story_id: str,
          scene_id: str, *, limit: int = BOARD_MARKS) -> str:
    """DM 牌面：喂给模型的跨回合上下文。

    只放**被标记的**剧情要点，不喂原始对话流水——这就是「防止上下文过多」。

    「悬而未决」（⚠）是状态不是记忆，**不占** 8 条预算（主人 2026-11-14 定）。
    """
    from core import story

    marks = plot_marks(conn, campaign_id, limit=limit)
    pending = unresolved(conn, campaign_id, story_id, scene_id)
    if not marks and not pending:
        return ""

    lines = ["[牌面]"]
    for m in marks:
        tag = "关键" if m["weight"] >= 3 else ""
        where = m["scene"] or "—"
        tail = f"（{where} · {tag}）" if tag else f"（{where}）"
        lines.append(f"  · {m['what']}{tail}")
    for item in pending:
        lines.append(f"  ⚠ {item}")
    return chr(10).join(lines)


def unresolved(conn: sqlite3.Connection, campaign_id: int, story_id: str,
               scene_id: str) -> list[str]:
    """还没体验到的剧本内容（只给**存在性**提示，不给答案）。

    判据：本场还没查到的线索（``clues`` 有定义但没查到）。
    """
    from core import story

    sc = story.scene(story_id, scene_id) or {}
    defined = [str(c.get("id")) for c in (sc.get("clues") or []) if c.get("id")]
    if not defined:
        return []
    prog = load_progress(conn, campaign_id, scene_id)
    found = set(prog.get("clues_found") or [])
    left = [c for c in defined if c not in found]
    if not left:
        return []
    return [f"本场还有 {len(left)} 条线索没查"]


# ── 牌面里的一句话摘要（给 prompt 用） ──────────────────────


def board_lines(conn: sqlite3.Connection, campaign_id: int, story_id: str,
                scene_id: str, *, limit: int = BOARD_MARKS) -> list[str]:
    """``board()`` 的行版本，方便测试与拼 prompt。"""
    text = board(conn, campaign_id, story_id, scene_id, limit=limit)
    return text.splitlines() if text else []
