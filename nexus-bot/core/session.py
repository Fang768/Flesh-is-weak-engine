"""跑团闸门：一个群一条主线，IDLE / ACTIVE / PAUSED 三态。

设计要点（见与主人的讨论）：

* 闸门状态**持久化**，重启后仍能「继续上次」
* 一个群同时只有一条活动主线（``group_sessions.group_id`` 是主键）
* IDLE 下不创建任何跑团运行时对象，只允许读库（``/状态``）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from .db import connect, now


class Mode(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


@dataclass(slots=True)
class Campaign:
    id: int
    title: str
    status: str
    scene: str
    created_at: str
    updated_at: str
    scenario: str = "demo"


@dataclass(slots=True)
class SessionState:
    group_id: str
    mode: Mode
    campaign: Campaign | None

    @property
    def is_active(self) -> bool:
        return self.mode is Mode.ACTIVE


class Gate:
    """闸门的状态机 + 存储。"""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn or connect()

    # ---------- 读 ----------

    def state(self, group_id: str) -> SessionState:
        row = self.conn.execute(
            "SELECT campaign_id, mode FROM group_sessions WHERE group_id = ?", (group_id,)
        ).fetchone()
        if row is None:
            return SessionState(group_id, Mode.IDLE, None)
        campaign = self._campaign(row["campaign_id"]) if row["campaign_id"] else None
        return SessionState(group_id, Mode(row["mode"]), campaign)

    def paused_campaign(self, group_id: str) -> Campaign | None:
        """本群最近一条已暂停的主线（用于「是否从上次暂停点继续」）。"""
        state = self.state(group_id)
        if state.campaign and state.campaign.status == CampaignStatus.PAUSED.value:
            return state.campaign
        row = self.conn.execute(
            """SELECT * FROM campaigns WHERE status = 'paused'
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()
        return _row_to_campaign(row) if row else None

    def last_paused_any(self) -> Campaign | None:
        row = self.conn.execute(
            "SELECT * FROM campaigns WHERE status = 'paused' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_campaign(row) if row else None

    # ---------- 写 ----------

    def start_new(self, group_id: str, title: str, scenario: str = "demo") -> Campaign:
        """新开一条主线并立即进入 ACTIVE，场景落在剧本第一幕。"""
        from core import story

        stamp = now()
        try:
            first = story.first_scene(scenario)
        except story.StoryError:
            scenario, first = "demo", story.first_scene("demo")
        cur = self.conn.execute(
            """INSERT INTO campaigns (title, status, scene, scenario, created_at, updated_at)
               VALUES (?, 'active', ?, ?, ?, ?)""",
            (title, first, scenario, stamp, stamp),
        )
        campaign_id = int(cur.lastrowid)
        self._set_session(group_id, campaign_id, Mode.ACTIVE)
        self.conn.commit()
        return self._campaign(campaign_id)  # type: ignore[return-value]

    def resume(self, group_id: str, campaign_id: int | None = None) -> Campaign | None:
        """从暂停点继续。没有可继续的主线就返回 ``None``。"""
        target = self._campaign(campaign_id) if campaign_id else self.paused_campaign(group_id)
        if target is None:
            return None
        self.conn.execute(
            "UPDATE campaigns SET status = 'active', updated_at = ? WHERE id = ?",
            (now(), target.id),
        )
        self._set_session(group_id, target.id, Mode.ACTIVE)
        self.conn.commit()
        return self._campaign(target.id)

    def pause(self, group_id: str) -> Campaign | None:
        state = self.state(group_id)
        if state.campaign is None:
            return None
        self.conn.execute(
            "UPDATE campaigns SET status = 'paused', updated_at = ? WHERE id = ?",
            (now(), state.campaign.id),
        )
        self._set_session(group_id, state.campaign.id, Mode.PAUSED)
        self.conn.commit()
        return self._campaign(state.campaign.id)

    def end(self, group_id: str) -> Campaign | None:
        state = self.state(group_id)
        if state.campaign is None:
            self._set_session(group_id, None, Mode.IDLE)
            self.conn.commit()
            return None
        self.conn.execute(
            "UPDATE campaigns SET status = 'ended', updated_at = ? WHERE id = ?",
            (now(), state.campaign.id),
        )
        self._set_session(group_id, None, Mode.IDLE)
        self.conn.commit()
        return self._campaign(state.campaign.id)

    def set_scene(self, campaign_id: int, scene: str) -> None:
        self.conn.execute(
            "UPDATE campaigns SET scene = ?, updated_at = ? WHERE id = ?",
            (scene, now(), campaign_id),
        )
        self.conn.commit()

    # ---------- 权限 ----------

    def is_operator(self, qq: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM operators WHERE qq = ?", (str(qq),)).fetchone()
        return row is not None

    def remove_operator(self, qq: str) -> bool:
        cur = self.conn.execute("DELETE FROM operators WHERE qq = ?", (str(qq),))
        self.conn.commit()
        return cur.rowcount > 0

    def operators(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT qq, note FROM operators ORDER BY added_at"
        ).fetchall()
        return [(str(r[0]), str(r[1] or "")) for r in rows]

    def add_operator(self, qq: str, note: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO operators (qq, note, added_at) VALUES (?, ?, ?)",
            (str(qq), note, now()),
        )
        self.conn.commit()

    # ---------- 内部 ----------

    def _campaign(self, campaign_id: int | None) -> Campaign | None:
        if campaign_id is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (int(campaign_id),)
        ).fetchone()
        return _row_to_campaign(row) if row else None

    def _set_session(self, group_id: str, campaign_id: int | None, mode: Mode) -> None:
        self.conn.execute(
            """INSERT INTO group_sessions (group_id, campaign_id, mode, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(group_id) DO UPDATE SET
                 campaign_id = excluded.campaign_id,
                 mode = excluded.mode,
                 updated_at = excluded.updated_at""",
            (str(group_id), campaign_id, mode.value, now()),
        )


def _row_to_campaign(row: sqlite3.Row | None) -> Campaign | None:
    if row is None:
        return None
    return Campaign(
        id=int(row["id"]),
        title=row["title"],
        status=row["status"],
        scene=row["scene"],
        scenario=(row["scenario"] if "scenario" in row.keys() else "demo"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
