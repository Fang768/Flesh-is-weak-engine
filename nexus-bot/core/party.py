"""多人同场：在场名单、轮流顺序、协助加成。

* **在场名单**：这群里所有还留着角色卡的人（核心书卡 + 简易版卡），按入团顺序排。
* **轮流顺序**：每有人完成一次行动（检定/攻击/线索/环境伤害），指针前进一格；
  只是提示「现在轮到谁」，不阻止别人说话。
数据落库（`party_turns` 表），重启后顺序不丢。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


class PartyError(Exception):
    """没卡、没团之类。"""


@dataclass(slots=True)
class Member:
    qq: str
    name: str
    kind: str        # "red" | "easy"
    hp: int | None = None
    max_hp: int | None = None

    def line(self, *, current: bool = False) -> str:
        mark = "🎯" if current else "·"
        hp = f"{self.hp}/{self.max_hp}" if self.hp is not None else "?"
        return f"{mark} {self.name}（{hp} HP）"


def _rows(conn: sqlite3.Connection, group_id: str) -> list[Member]:
    out: list[Member] = []
    for row in conn.execute(
        "SELECT owner_qq, name, state FROM characters WHERE group_id = ?"
        " ORDER BY created_at, id",
        (str(group_id),),
    ).fetchall():
        state = json.loads(row["state"] or "{}")
        out.append(
            Member(
                qq=str(row["owner_qq"]),
                name=str(row["name"]),
                kind="red",
                hp=_opt(state.get("hp")),
                max_hp=_opt(state.get("max_hp")),
            )
        )
    for row in conn.execute(
        "SELECT owner_qq, name, data FROM easy_cards WHERE group_id = ?"
        " ORDER BY created_at, rowid",
        (str(group_id),),
    ).fetchall():
        data = json.loads(row["data"] or "{}")
        hp = _opt(data.get("hp"))
        out.append(
            Member(qq=str(row["owner_qq"]), name=str(row["name"]), kind="easy", hp=hp, max_hp=hp)
        )
    return out


def _opt(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def members(conn: sqlite3.Connection, group_id: str) -> list[Member]:
    return _rows(conn, group_id)


def _order(conn: sqlite3.Connection, group_id: str) -> list[str]:
    """当前顺序：库里存的顺序 ∩ 现在还在的人；新人追加到末尾。"""
    now = [m.qq for m in _rows(conn, group_id)]
    row = conn.execute(
        "SELECT order_json FROM party_turns WHERE group_id = ?", (str(group_id),)
    ).fetchone()
    saved: list[str] = json.loads(row["order_json"]) if row else []
    merged = [qq for qq in saved if qq in now] + [qq for qq in now if qq not in saved]
    if merged != saved:
        _save(conn, group_id, merged, 0, 1)
    return merged


def _save(conn: sqlite3.Connection, group_id: str, order: list[str], idx: int, rnd: int) -> None:
    conn.execute(
        "INSERT INTO party_turns (group_id, order_json, idx, round, updated_at)"
        " VALUES (?, ?, ?, ?, datetime('now'))"
        " ON CONFLICT (group_id) DO UPDATE SET"
        " order_json = excluded.order_json, idx = excluded.idx,"
        " round = excluded.round, updated_at = excluded.updated_at",
        (str(group_id), json.dumps(order, ensure_ascii=False), int(idx), int(rnd)),
    )
    conn.commit()


def _load(conn: sqlite3.Connection, group_id: str) -> tuple[list[str], int, int]:
    order = _order(conn, group_id)
    row = conn.execute(
        "SELECT idx, round FROM party_turns WHERE group_id = ?", (str(group_id),)
    ).fetchone()
    idx = int(row["idx"]) if row else 0
    rnd = int(row["round"]) if row else 1
    if order:
        idx %= len(order)
    else:
        idx = 0
    return order, idx, rnd


def current(conn: sqlite3.Connection, group_id: str) -> Member | None:
    order, idx, _ = _load(conn, group_id)
    if not order:
        return None
    qq = order[idx]
    for m in _rows(conn, group_id):
        if m.qq == qq:
            return m
    return None


def advance(conn: sqlite3.Connection, group_id: str, actor_qq: str) -> Member | None:
    """行动结束后指针前进一格；返回新的当前玩家。"""
    order, idx, rnd = _load(conn, group_id)
    if not order:
        return None
    if str(actor_qq) in order:
        idx = (order.index(str(actor_qq)) + 1) % len(order)
        if idx == 0:
            rnd += 1
    else:
        idx = (idx + 1) % len(order)
    _save(conn, group_id, order, idx, rnd)
    return current(conn, group_id)


def status_text(conn: sqlite3.Connection, group_id: str) -> str:
    people = _rows(conn, group_id)
    if not people:
        return "群里还没有角色卡。先「认领 火炬」或「给我一个角色」。"
    order, idx, rnd = _load(conn, group_id)
    cur = order[idx] if order else None
    lines = [f"👥 在场 {len(people)} 人（第 {rnd} 轮）"]
    for m in people:
        lines.append(m.line(current=m.qq == cur))
    if cur:
        name = next((m.name for m in people if m.qq == cur), cur)
        lines.append(f"现在轮到：{name}（其他人也可以说，只是顺序提示）")
    return "\n".join(lines)


def reset(conn: sqlite3.Connection, group_id: str) -> None:
    conn.execute("DELETE FROM party_turns WHERE group_id = ?", (str(group_id),))
    conn.commit()
