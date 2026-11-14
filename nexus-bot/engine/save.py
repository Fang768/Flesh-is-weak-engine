"""存档：一次游玩的全部状态，包成一个句柄。

对应你划分的第一层第二项——**存档**：

    * 目前进行的进度（在哪一节）
    * 剧本中哪些内容触发了（查到的线索 / 聊过的话题 / 通用旗标）
    * 玩家状态（血量/义体/金钱/人情……）
    * NPC 记忆

设计约束：

1. **不做业务判断**。这里只读写,不知道「买票」是什么,也不知道该不该推进。
   判断在 engine/drama.py,规则在 cyberpunk/。
2. **不 import core/**。连接由调用方传进来,表结构复用 core/db.py 的 schema。
   这样新旧两层不会互相拖拽(旧层以后整块删掉,这一层不受影响)。
3. **读写对称**。能写的状态(`mark_clue` / `mark_talked` / `mark_flag`)
   就是 `conditions.py` 能读的谓词,一一对应;不开放"随便塞个 key"。
4. 玩家状态这一版**只读**。战斗结算 / 义体安装 / 商店扣钱仍在写这些字段,
   这时再开一个写入口会变成两个写者。等 drama 层接管后再收归。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .conditions import World


# -- 玩家状态（归一化视图） --------------------------------------------


@dataclass(frozen=True)
class Item:
    """身上的一件东西。

    `kind` 分三类——这是旧结构缺的那一层：以前 `gear` 只是一串名字，
    分不清「买来的装备」和「剧本给的任务道具」。

    * `gear`       买来/捡来的普通装备（枪、护甲、杂物）
    * `quest`      剧情道具（演出票、木桩、人头）——用掉之后**仍在身上**
    * `consumable` 消耗品（弹药、汽水）——用一次就没
    """

    name: str
    kind: str = "gear"
    #: 从哪来：shop / story / loot / ""（不知道）
    source: str = ""
    #: quest 道具「已经用过了」但没消耗掉（例如木桩钉过心脏）
    used: bool = False

    def to_json(self):
        """落库形状：普通装备仍是裸字符串，保持旧数据兼容。"""
        if self.kind == "gear" and not self.source and not self.used:
            return self.name
        out = {"name": self.name, "kind": self.kind}
        if self.source:
            out["source"] = self.source
        if self.used:
            out["used"] = True
        return out


def as_item(raw) -> Item:
    """把库里的一行（字符串或 dict）归一化成 Item。"""
    if isinstance(raw, Item):
        return raw
    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        return Item(
            name=name,
            kind=str(raw.get("kind") or "gear"),
            source=str(raw.get("source") or ""),
            used=bool(raw.get("used")),
        )
    return Item(name=str(raw or "").strip())


@dataclass(frozen=True)
class CardView:
    """一张角色卡的只读视图,吸收 RED 卡与简易卡的字段差异。

    两种卡在库里是两张表、两套字段(`characters.state` vs `easy_cards.data`),
    剧情层不该关心这个区别,所以在这里归一化。
    """

    owner_qq: str
    name: str
    kind: str = "red"          # "red" | "easy"
    hp: int = 0
    max_hp: int = 0
    sp: int = 0
    eb: int = 0
    stats: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    #: 身上的东西（Item）。读的时候统一归一化，写的时候见 Item.to_json
    gear: list = field(default_factory=list)
    cyber: list = field(default_factory=list)
    favors: list = field(default_factory=list)
    #: 原始 blob,冷门字段从这里兜底读(不建议长期依赖)
    state: dict = field(default_factory=dict)

    def item_names(self) -> list:
        return [i.name for i in self.gear if i.name]

    def has_item(self, name: str) -> bool:
        return bool(name) and name in self.item_names()

    def find_item(self, name: str):
        for item in self.gear:
            if item.name == name:
                return item
        return None

    def skill(self, name: str, default: int = 0) -> int:
        try:
            return int(self.skills.get(name, default))
        except (TypeError, ValueError):
            return default

    def stat(self, code: str, default: int = 0) -> int:
        try:
            return int(self.stats.get(code, default))
        except (TypeError, ValueError):
            return default

    def alive(self) -> bool:
        return self.hp > 0


# -- 存档句柄 ----------------------------------------------------------


class SaveError(ValueError):
    """存档不存在 / 数据坏了。"""


@dataclass
class Save:
    """一次游玩的存档。

    用法::

        save = Save(conn, group_id="qq:123", campaign_id=1)
        save.mark_clue("office_photo", by="u1")
        ctx = Context(world=save.world())
    """

    conn: sqlite3.Connection
    group_id: str
    campaign_id: int

    # -- 进度 ----------------------------------------------------------

    def scene(self) -> str:
        row = self.conn.execute(
            "SELECT scene FROM campaigns WHERE id = ?", (int(self.campaign_id),)
        ).fetchone()
        if row is None:
            return ""
        return str(row["scene"] or "")

    def set_scene(self, scene_id: str) -> None:
        self.conn.execute(
            "UPDATE campaigns SET scene = ?, updated_at = ? WHERE id = ?",
            (str(scene_id or ""), _now(), int(self.campaign_id)),
        )
        self.conn.commit()

    def status(self) -> str:
        row = self.conn.execute(
            "SELECT status FROM campaigns WHERE id = ?", (int(self.campaign_id),)
        ).fetchone()
        return str(row["status"] or "") if row else ""

    def end(self) -> None:
        self.conn.execute(
            "UPDATE campaigns SET status = 'ended', updated_at = ? WHERE id = ?",
            (_now(), int(self.campaign_id)),
        )
        self.conn.commit()

    # -- 世界状态快照（给 conditions 用） -------------------------------

    def world(self) -> World:
        """只读快照。每次调用重查——不存在脏读,也不用管失效。"""
        items: set = set()
        for card in self.cards():
            items.update(card.item_names())
        return World(
            clues=set(self.clues()),
            talked=self.talked(),
            flags=set(self.flags()),
            battle=self.battle_result(),
            visited=set(self.visited()),
            items=items,
        )

    def clues(self) -> list:
        rows = self.conn.execute(
            "SELECT clue_id FROM clues_found WHERE group_id = ?",
            (self.group_id,),
        ).fetchall()
        return [str(r["clue_id"]) for r in rows]

    def talked(self) -> dict:
        """{npc_key: {topic, ...}}；空集合 = 聊过但没记话题。"""
        out: dict = {}
        for row in self.conn.execute(
            "SELECT npcs_talked FROM scene_progress WHERE campaign_id = ?",
            (int(self.campaign_id),),
        ).fetchall():
            for item in _load_json(row["npcs_talked"], []):
                if isinstance(item, dict):
                    key = str(item.get("npc") or "")
                    topic = str(item.get("topic") or "")
                else:
                    key, topic = str(item or ""), ""
                if not key:
                    continue
                out.setdefault(key, set())
                if topic:
                    out[key].add(topic)
        return out

    def flags(self) -> list:
        rows = self.conn.execute(
            "SELECT name FROM campaign_flags WHERE campaign_id = ?",
            (int(self.campaign_id),),
        ).fetchall()
        return [str(r["name"]) for r in rows]

    def battle_result(self) -> str:
        row = self.conn.execute(
            "SELECT battle_result FROM scene_progress"
            " WHERE campaign_id = ? AND scene = ?",
            (int(self.campaign_id), self.scene()),
        ).fetchone()
        return str(row["battle_result"] or "") if row else ""

    def visited(self) -> list:
        rows = self.conn.execute(
            "SELECT scene FROM scene_progress WHERE campaign_id = ?",
            (int(self.campaign_id),),
        ).fetchall()
        return [str(r["scene"]) for r in rows if r["scene"]]

    # -- 三个通用记录（与 conditions 的三个谓词一一对应） ---------------

    def mark_clue(self, clue_id: str, by: str = "") -> bool:
        """记一条查到的线索。幂等：同一条再写一次是 no-op。"""
        cid = str(clue_id or "").strip()
        if not cid:
            return False
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO clues_found"
            " (group_id, campaign_id, clue_id, found_by, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (self.group_id, int(self.campaign_id), cid, str(by or ""), _now()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_talked(self, npc_key: str, topic: str = "") -> bool:
        """记「跟某人聊过（某个话题）」。

        写进**当前场景**的 scene_progress——同一 NPC 在不同场景可以有不同
        进度；条件读的时候按 campaign 汇总（见 `talked()`）。
        """
        key = str(npc_key or "").strip()
        if not key:
            return False
        scene = self.scene()
        prog = self._progress(scene)
        items = list(prog.get("npcs_talked") or [])
        entry = {"npc": key, "topic": str(topic or "").strip()}
        if entry in items:
            return False
        items.append(entry)
        self._save_progress(scene, {"npcs_talked": items})
        return True

    def mark_flag(self, name: str, value: str = "") -> bool:
        """记一个通用旗标（任务完成、东西拿到手……）。幂等。"""
        flag = str(name or "").strip()
        if not flag:
            return False
        cur = self.conn.execute(
            "INSERT INTO campaign_flags (campaign_id, name, value, created_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, name) DO UPDATE SET"
            " value = excluded.value",
            (int(self.campaign_id), flag, str(value or ""), _now()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def has_flag(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM campaign_flags WHERE campaign_id = ? AND name = ?",
            (int(self.campaign_id), str(name or "")),
        ).fetchone()
        return row is not None

    def set_battle_result(self, result: str) -> None:
        """记上一场战斗的结果：'' / won / lost / withdraw。"""
        value = str(result or "").strip()
        if value not in ("", "won", "lost", "withdraw"):
            raise SaveError("战斗结果只能是 ''/won/lost/withdraw：" + repr(result))
        scene = self.scene()
        self._progress(scene)                       # 确保行存在
        self.conn.execute(
            "UPDATE scene_progress SET battle_result = ?, updated_at = ?"
            " WHERE campaign_id = ? AND scene = ?",
            (value, _now(), int(self.campaign_id), scene),
        )
        self.conn.commit()

    # -- 场景进度（内部的读写基础设施） --------------------------------

    def _progress(self, scene: str) -> dict:
        row = self.conn.execute(
            "SELECT clues_found, npcs_talked, gates_passed, off_track, nudged"
            " FROM scene_progress WHERE campaign_id = ? AND scene = ?",
            (int(self.campaign_id), str(scene or "")),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT OR IGNORE INTO scene_progress"
                " (campaign_id, scene, clues_found, npcs_talked, gates_passed,"
                "  off_track, nudged, battle_result, updated_at)"
                " VALUES (?, ?, '[]', '[]', '[]', 0, '[]', '', ?)",
                (int(self.campaign_id), str(scene or ""), _now()),
            )
            self.conn.commit()
            return {"clues_found": [], "npcs_talked": [], "gates_passed": [],
                    "off_track": 0, "nudged": []}
        return {
            "clues_found": _load_json(row["clues_found"], []),
            "npcs_talked": _load_json(row["npcs_talked"], []),
            "gates_passed": _load_json(row["gates_passed"], []),
            "off_track": int(row["off_track"] or 0),
            "nudged": _load_json(row["nudged"], []),
        }

    def _save_progress(self, scene: str, patch: dict) -> None:
        prog = self._progress(scene)
        prog.update(patch)
        self.conn.execute(
            "UPDATE scene_progress SET clues_found = ?, npcs_talked = ?,"
            " gates_passed = ?, off_track = ?, nudged = ?, updated_at = ?"
            " WHERE campaign_id = ? AND scene = ?",
            (
                json.dumps(prog.get("clues_found") or [], ensure_ascii=False),
                json.dumps(prog.get("npcs_talked") or [], ensure_ascii=False),
                json.dumps(prog.get("gates_passed") or [], ensure_ascii=False),
                int(prog.get("off_track") or 0),
                json.dumps(prog.get("nudged") or [], ensure_ascii=False),
                _now(),
                int(self.campaign_id),
                str(scene or ""),
            ),
        )
        self.conn.commit()

    # -- 玩家状态（只读） ----------------------------------------------

    def cards(self) -> list:
        out: list = []
        for row in self.conn.execute(
            "SELECT owner_qq, name, stats, skills, gear, perks, state"
            " FROM characters WHERE group_id = ? ORDER BY id",
            (self.group_id,),
        ).fetchall():
            state = _load_json(row["state"], {})
            out.append(
                CardView(
                    owner_qq=str(row["owner_qq"]),
                    name=str(row["name"]),
                    kind="red",
                    hp=int(state.get("hp") or 0),
                    max_hp=int(state.get("max_hp") or 0),
                    sp=int(state.get("sp") or 0),
                    eb=int(state.get("eb") or 0),
                    stats=_load_json(row["stats"], {}),
                    skills=_load_json(row["skills"], {}),
                    gear=_items(_load_json(row["gear"], [])),
                    cyber=_load_json(state.get("cyber"), []),
                    favors=_load_json(state.get("favors"), []),
                    state=state,
                )
            )
        for row in self.conn.execute(
            "SELECT owner_qq, name, data FROM easy_cards WHERE group_id = ?"
            " ORDER BY rowid",
            (self.group_id,),
        ).fetchall():
            data = _load_json(row["data"], {})
            out.append(
                CardView(
                    owner_qq=str(row["owner_qq"]),
                    name=str(row["name"]),
                    kind="easy",
                    hp=int(data.get("hp") or 0),
                    max_hp=int(data.get("max_hp") or data.get("hp") or 0),
                    sp=int(data.get("sp") or 0),
                    eb=int((data.get("state") or {}).get("eb") or 0),
                    stats=dict(data.get("stats") or {}),
                    skills=dict(data.get("skills") or {}),
                    gear=_items(data.get("gear") or []),
                    cyber=list(data.get("cyber") or []),
                    favors=list((data.get("state") or {}).get("favors") or []),
                    state=dict(data.get("state") or {}),
                )
            )
        return out

    def card(self, owner_qq: str):
        for item in self.cards():
            if item.owner_qq == str(owner_qq):
                return item
        return None

    # -- NPC 记忆与牌面 ------------------------------------------------

    def memory(self, npc_key: str) -> dict:
        row = self.conn.execute(
            "SELECT met, attitude, bounds, relations, exchanges, impression"
            " FROM npc_memory WHERE campaign_id = ? AND npc_key = ?",
            (int(self.campaign_id), str(npc_key or "")),
        ).fetchone()
        if row is None:
            return {"met": False, "attitude": 5, "bounds": [0, 10],
                    "relations": [], "exchanges": [], "impression": []}
        bounds = _load_json(row["bounds"], [0, 10])
        if not isinstance(bounds, list) or len(bounds) != 2:
            bounds = [0, 10]
        return {
            "met": bool(row["met"]),
            "attitude": int(row["attitude"] or 5),
            "bounds": [int(bounds[0]), int(bounds[1])],
            "relations": _load_json(row["relations"], []),
            "exchanges": _load_json(row["exchanges"], []),
            "impression": _load_json(row["impression"], []),
        }

    def board(self, limit: int = 8) -> str:
        """DM 牌面：只放被标记的剧情要点,按 weight 降序取前 limit 条。"""
        rows = self.conn.execute(
            "SELECT scene, what, weight FROM plot_marks"
            " WHERE campaign_id = ? ORDER BY weight DESC, rowid ASC LIMIT ?",
            (int(self.campaign_id), max(0, int(limit))),
        ).fetchall()
        if not rows:
            return ""
        lines = ["[牌面]"]
        for row in rows:
            tag = "关键" if int(row["weight"] or 1) >= 3 else ""
            where = str(row["scene"] or "—")
            tail = "（" + where + (" · " + tag if tag else "") + "）"
            lines.append("  · " + str(row["what"]) + tail)
        return "\n".join(lines)

    # -- 奖励结算（记账，不解释含义） ----------------------------------

    def settle_rewards(self, scene_id: str, rewards: dict) -> list:
        """把一次场景奖励记下来（幂等）。返回本次真正结算的条目说明。

        这一层只负责「记过没记过」，不懂 EB/IP/人情的语义——
        具体怎么加到卡上是第 2 层 ops 的事（见 ops.settle_rewards）。
        """
        key = "reward:" + str(scene_id)
        if self.has_flag(key):
            return []
        self.mark_flag(key, json.dumps(rewards, ensure_ascii=False))
        return [k for k in ("eb", "ip", "favor") if rewards.get(k)]

    def rewards_done(self, scene_id: str) -> bool:
        return self.has_flag("reward:" + str(scene_id))

    # -- 事件流水 ------------------------------------------------------

    def record(self, kind: str, summary: str, data: dict | None = None,
               actor: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO events (campaign_id, group_id, kind, actor, summary,"
            " data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(self.campaign_id), self.group_id, str(kind or ""),
             str(actor or ""), str(summary or "").strip()[:500],
             json.dumps(data or {}, ensure_ascii=False), _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def events(self, limit: int = 10, kinds=None) -> list:
        sql = ("SELECT kind, actor, summary, data, created_at FROM events"
               " WHERE group_id = ?")
        args: list = [self.group_id]
        if kinds:
            marks = ",".join("?" * len(kinds))
            sql += " AND kind IN (" + marks + ")"
            args.extend(str(k) for k in kinds)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(0, int(limit)))
        return [dict(row) for row in self.conn.execute(sql, tuple(args)).fetchall()]


# -- 工具 --------------------------------------------------------------


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _items(raw) -> list:
    """把库里的 gear 列归一化成 [Item, ...]，丢掉空名字。"""
    out = []
    for entry in raw or ():
        item = as_item(entry)
        if item.name:
            out.append(item)
    return out


def _load_json(raw, default):
    """读 JSON 字段；**已经是目标类型的直接返回**。

    后者很关键：`state` 这一列本身就是 JSON，从里面再取 `state["cyber"]`
    拿到的是已解析的 list——旧写法会把 list 丢进 json.loads 抛 TypeError，
    然后静默变成默认值，义体列表就永远是空的。
    """
    if isinstance(raw, type(default)):
        return raw
    if not isinstance(raw, str):
        return default
    try:
        value = json.loads(raw or "")
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError):
        return default


def open_save(conn: sqlite3.Connection, group_id: str) -> Save | None:
    """按群号取当前存档；没开团返回 None。"""
    row = conn.execute(
        "SELECT s.campaign_id FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id"
        " WHERE s.group_id = ? AND c.status = 'active'",
        (str(group_id),),
    ).fetchone()
    if row is None or row["campaign_id"] is None:
        return None
    return Save(conn=conn, group_id=str(group_id),
                campaign_id=int(row["campaign_id"]))
