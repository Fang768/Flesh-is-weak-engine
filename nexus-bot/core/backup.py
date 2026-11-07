"""存档导出 / 恢复。

一个群的跑团状态 = 主线 + 角色卡（核心书 + 简易版）+ 已发现的线索。
事件日志不进备份（它只是流水，恢复后仍可继续追加）。

导出格式带 ``schema`` 版本号，恢复时先校验再写，避免把坏文件灌进库。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = 1
BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "backups"


class BackupError(Exception):
    """备份/恢复失败。"""


def _rows(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def export_group(conn: sqlite3.Connection, group_id: str) -> dict:
    """把一个群的跑团状态打包成 dict。"""
    campaign = _rows(
        conn,
        "SELECT * FROM campaigns WHERE id = ("
        "  SELECT campaign_id FROM group_sessions WHERE group_id = ?"
        ")",
        (group_id,),
    )
    return {
        "schema": SCHEMA,
        "group_id": group_id,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "campaign": campaign[0] if campaign else None,
        "characters": _rows(
            conn, "SELECT * FROM characters WHERE group_id = ?", (group_id,)
        ),
        "easy_cards": _rows(
            conn, "SELECT * FROM easy_cards WHERE group_id = ?", (group_id,)
        ),
        "clues_found": _rows(
            conn, "SELECT * FROM clues_found WHERE group_id = ?", (group_id,)
        ),
    }


def counts(data: dict) -> str:
    """一句话描述备份里有什么（给玩家看的）。"""
    chars = len(data.get("characters") or []) + len(data.get("easy_cards") or [])
    clues = len(data.get("clues_found") or [])
    campaign = data.get("campaign") or {}
    title = campaign.get("title") or "（没有主线）"
    return f"主线《{title}》· 角色 {chars} 张 · 线索 {clues} 条"


def save(conn: sqlite3.Connection, group_id: str, *, out_dir: Path | None = None) -> Path:
    """导出并落盘，返回文件路径。"""
    data = export_group(conn, group_id)
    target = Path(out_dir) if out_dir else BACKUP_DIR
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_gid = "".join(ch for ch in group_id if ch.isalnum() or ch in "-_") or "group"
    path = target / f"{safe_gid}-{stamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(path: Path | str) -> dict:
    p = Path(path)
    if not p.exists():
        raise BackupError(f"找不到备份文件：{p.name}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackupError(f"备份文件不是合法 JSON（{exc.msg}）") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise BackupError("这份备份的格式版本对不上，不能直接恢复")
    return data


def list_backups(out_dir: Path | None = None, limit: int = 5) -> list[Path]:
    target = Path(out_dir) if out_dir else BACKUP_DIR
    if not target.exists():
        return []
    files = sorted(target.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def restore(
    conn: sqlite3.Connection,
    data: dict,
    *,
    group_id: str | None = None,
    replace: bool = True,
) -> str:
    """把备份写回库；``replace=True`` 先清空该群现有数据。"""
    gid = group_id or str(data.get("group_id") or "")
    if not gid:
        raise BackupError("备份里没有群标识，也没指定群")
    try:
        if replace:
            conn.execute("DELETE FROM characters WHERE group_id = ?", (gid,))
            conn.execute("DELETE FROM easy_cards WHERE group_id = ?", (gid,))
            conn.execute("DELETE FROM clues_found WHERE group_id = ?", (gid,))
            conn.execute("DELETE FROM combatants WHERE group_id = ?", (gid,))
            conn.execute("DELETE FROM combats WHERE group_id = ?", (gid,))
            conn.execute("DELETE FROM group_sessions WHERE group_id = ?", (gid,))

        campaign = data.get("campaign")
        if campaign:
            conn.execute(
                "INSERT OR REPLACE INTO campaigns"
                " (id, title, status, scene, scenario, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign["id"],
                    campaign["title"],
                    campaign.get("status", "active"),
                    campaign.get("scene", ""),
                    campaign.get("scenario", "demo"),
                    campaign.get("created_at", ""),
                    campaign.get("updated_at", ""),
                ),
            )
            mode = "active" if campaign.get("status") == "active" else "paused"
            conn.execute(
                "INSERT OR REPLACE INTO group_sessions"
                " (group_id, campaign_id, mode, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (gid, campaign["id"], mode, campaign.get("updated_at", "")),
            )

        for row in data.get("characters") or []:
            conn.execute(
                "INSERT OR REPLACE INTO characters"
                " (id, group_id, owner_qq, name, template, concept, stats, skills,"
                "  gear, perks, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.get("id"),
                    gid,
                    row["owner_qq"],
                    row["name"],
                    row.get("template", ""),
                    row.get("concept", ""),
                    row.get("stats", "{}"),
                    row.get("skills", "{}"),
                    row.get("gear", "[]"),
                    row.get("perks", "[]"),
                    row.get("state", "{}"),
                    row.get("created_at", ""),
                    row.get("updated_at", ""),
                ),
            )

        for row in data.get("easy_cards") or []:
            conn.execute(
                "INSERT OR REPLACE INTO easy_cards"
                " (group_id, owner_qq, name, template, data, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    gid,
                    row["owner_qq"],
                    row.get("name", "无名"),
                    row.get("template", ""),
                    row.get("data", "{}"),
                    row.get("created_at", ""),
                    row.get("updated_at", ""),
                ),
            )

        for row in data.get("clues_found") or []:
            conn.execute(
                "INSERT OR REPLACE INTO clues_found"
                " (group_id, campaign_id, clue_id, found_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    gid,
                    row.get("campaign_id"),
                    row["clue_id"],
                    row.get("found_by", ""),
                    row.get("created_at", ""),
                ),
            )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise BackupError(f"写库失败，已回滚：{exc}") from exc
    return gid
