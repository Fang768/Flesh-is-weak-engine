"""Local SQLite storage for the Neon Ashes campaign.

No AstrBot imports. All state transitions occur in a BEGIN IMMEDIATE SQLite
transaction and use a strict expected_revision check (optimistic locking).
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SaveError(ValueError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(x: Any, max_len: int = 250) -> bool:
    return isinstance(x, str) and 0 < len(x.strip()) <= max_len and "\x00" not in x


def _str_or_none(x: Any, max_len: int = 300) -> bool:
    return x is None or _text(x, max_len)


def _nat_or_none(x: Any, maximum: int = 99999999) -> bool:
    return x is None or (type(x) is int and 0 <= x <= maximum)


def _bounded_int(x: Any, lo: int, hi: int) -> bool:
    return type(x) is int and lo <= x <= hi


def _list(x: Any, max_len: int = 100) -> bool:
    return isinstance(x, list) and len(x) <= max_len and len(canonical(x)) <= 32000


def _dict(x: Any) -> bool:
    return isinstance(x, dict) and len(x) <= 200 and len(canonical(x)) <= 32000


def _bool(x: Any) -> bool:
    return type(x) is bool


WORLD_FIELDS = {
    "act": lambda v: _bounded_int(v, 1, 99),
    "scene_status": lambda v: v in ("not_started", "active", "paused", "complete"),
    "game_date": _str_or_none,
    "weekday": _str_or_none,
    "location": _str_or_none,
    "scene": _str_or_none,
    "main_quest": _dict,
    "side_quests": _list,
    "current_task_id": _str_or_none,
    "task_sequence": _nat_or_none,
    "season": _nat_or_none,
    "npc_attitudes": _dict,
    "faction_relations": _dict,
    "alert_level": lambda v: _bounded_int(v, 0, 100),
    "team_cash": _nat_or_none,
    "team_assets": _list,
    "base_facilities": _dict,
    "asset_ledger": _list,
    "clocks": _dict,
    "clues": _list,
    "confirmed_facts": _list,
    "rumors": _list,
    "npcs": _list,
    "recent_roll": lambda v: v is None or _dict(v),
    "action_window": _str_or_none,
    "scene_entry": _str_or_none,
    "present_characters": _list,
    "absent_characters": _list,
    "pending_actions": _list,
    "recap": lambda v: isinstance(v, str) and len(v) <= 300,
    "mission_offer": _dict,
}

ROLE_FIELDS = {
    "nickname": lambda v: _text(v, 80),
    "status": lambda v: v in ("pending", "active", "away", "retired"),
    "job": _str_or_none,
    "position": _str_or_none,
    "hp": _nat_or_none,
    "max_hp": _nat_or_none,
    "ram": _nat_or_none,
    "max_ram": _nat_or_none,
    "stress": lambda v: _nat_or_none(v, 100),
    "cash": _nat_or_none,
    "xp": _nat_or_none,
    "cumulative_xp": _nat_or_none,
    "equipment": _list,
    "injuries": _list,
    "cyberware_cooldowns": _dict,
    "attributes": _dict,
    "skills": _dict,
    "quest_participation": _list,
    "reward_option": _str_or_none,
    "reward_uses": lambda v: _nat_or_none(v, 999),
    "dossier_stage": lambda v: v in ("unrevealed", "ready", "omen", "consented", "revealed"),
    "dossier_category_id": lambda v: v is None or _bounded_int(v, 1, 10),
}


def initial_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "title": "霓虹余烬",
        "rules_version": "3.2",
        "world": {
            "act": 1, "scene_status": "not_started", "game_date": None,
            "weekday": None, "location": None, "scene": None,
            "main_quest": {"status": "not_started", "task_id": None},
            "side_quests": [], "current_task_id": None, "task_sequence": None,
            "season": None, "npc_attitudes": {}, "faction_relations": {},
            "alert_level": 0, "team_cash": None, "team_assets": [],
            "base_facilities": {}, "asset_ledger": [],
            "clocks": {"corporate": None, "community": None, "black_market": None},
            "clues": [], "confirmed_facts": [], "rumors": [], "npcs": [],
            "recent_roll": None, "action_window": None, "scene_entry": None,
            "present_characters": [], "absent_characters": [],
            "pending_actions": [], "recap": "第一幕尚未开始，当前没有已登记角色；委托报价不代表预付款已到账。",
            "mission_offer": {
                "fixer": "米拉·克罗", "location": "沃森·歌舞伎",
                "objective": "取回死去快递员储物柜中的芯片",
                "reward_per_player": 600, "advance_per_player": 100,
                "status": "not_offered", "advance_paid": False,
            },
        },
        "roles": {},
    }


def new_role(user_id: str, character_id: str, nickname: str) -> dict[str, Any]:
    return {
        "qq_user_id": user_id, "character_id": character_id, "nickname": nickname,
        "joined_at": utcnow(), "status": "pending", "job": None,
        "position": None, "hp": None, "max_hp": None,
        "ram": None, "max_ram": None, "stress": None,
        "cash": None, "xp": None, "cumulative_xp": None,
        "equipment": [], "injuries": [], "cyberware_cooldowns": {},
        "attributes": {}, "skills": {}, "quest_participation": [],
        "dossier_progress": 0, "dossier_completed_tasks": [],
        "dossier_stage": "unrevealed", "dossier_category_id": None,
        "dossier_content": "尚未生成", "dossier_public_evidence": None,
        "reward_option": None, "reward_uses": 0,
    }


def validate_snapshot(s: Any) -> None:
    if not isinstance(s, dict) or s.get("schema_version") != 1 or s.get("rules_version") != "3.2":
        raise SaveError("存档格式或规则版本不匹配")
    if set(s) != {"schema_version", "title", "rules_version", "world", "roles"}:
        raise SaveError("存档顶层字段异常")
    if s["title"] != "霓虹余烬":
        raise SaveError("游戏名称不匹配")
    w, roles = s["world"], s["roles"]
    if not isinstance(w, dict) or set(w) != set(WORLD_FIELDS):
        raise SaveError("世界字段缺失或包含未知键")
    for field, checker in WORLD_FIELDS.items():
        if not checker(w[field]):
            raise SaveError(f"世界字段不合法：{field}")
    if not isinstance(roles, dict) or len(roles) > 200:
        raise SaveError("历史角色记录超过200条安全上限")
    if sum(r.get("status") != "retired" for r in roles.values() if isinstance(r, dict)) > 10:
        raise SaveError("在册非退团角色超过10名")
    seen_users = set()
    required = set(new_role("u", "01", "n"))
    for cid, r in roles.items():
        if not isinstance(r, dict) or set(r) != required or r["character_id"] != cid:
            raise SaveError("角色字段结构或ID不匹配")
        if not re.fullmatch(r"[\w-]{1,24}", cid, flags=re.ASCII):
            raise SaveError("角色ID格式不合法")
        if not _text(r["qq_user_id"], 128) or r["qq_user_id"] in seen_users:
            raise SaveError("QQ用户ID缺失或重复")
        seen_users.add(r["qq_user_id"])
        if not _text(r["joined_at"], 60):
            raise SaveError("加入时间不合法")
        for field, checker in ROLE_FIELDS.items():
            if not checker(r[field]):
                raise SaveError(f"角色{cid}字段不合法：{field}")
        for a, b in (("hp", "max_hp"), ("ram", "max_ram")):
            if r[a] is not None and r[b] is not None and r[a] > r[b]:
                raise SaveError(f"{cid}的{a}不能超过{b}")
        p = r["dossier_progress"]
        if not _bounded_int(p, 0, 3) or not _list(r["dossier_completed_tasks"], 3):
            raise SaveError("未知档案进度无效")
        if p != len(r["dossier_completed_tasks"]) or len(set(r["dossier_completed_tasks"])) != p:
            raise SaveError("未知档案任务进度不一致")
        if any(not _text(i, 100) for i in r["dossier_completed_tasks"]):
            raise SaveError("未知档案任务ID无效")
        stage = r["dossier_stage"]
        if p < 3 and stage != "unrevealed":
            raise SaveError("未满3次不能进入档案揭晓流程")
        if p == 3 and stage == "unrevealed":
            raise SaveError("满3次应处于ready阶段")
        if stage != "revealed" and (r["dossier_category_id"] is not None or r["dossier_content"] != "尚未生成"
                                  or r["dossier_public_evidence"] is not None or r["reward_option"] is not None
                                  or r["reward_uses"] != 0):
            raise SaveError("尚未揭晓的档案不能预存秘密或奖励")
        if stage == "revealed" and (r["dossier_category_id"] is None or not _text(r["dossier_content"], 1000)
                                    or not _text(r["dossier_public_evidence"], 200)):
            raise SaveError("揭晓时必须有类别、经玩家确认的事实及公开证据引用")
    if len(canonical(s)) > 500000:
        raise SaveError("存档大小超过500KB限制")


class NeonStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "neon_saves.sqlite3"
        self.export_root = self.data_dir / "exports"
        self.export_root.mkdir(exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS saves (
                    group_key TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_group_rev ON audit(group_key,revision);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.db_path), timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _key(self, key: str) -> str:
        if not _text(key, 260) or ":" not in key:
            raise SaveError("非法群组标识")
        return key

    def _row(self, db, key: str):
        row = db.execute("SELECT * FROM saves WHERE group_key=?", (self._key(key),)).fetchone()
        if row is None:
            raise SaveError("该群尚未初始化，请管理员执行 /neoninit")
        return row

    def read(self, key: str) -> dict:
        with self._db() as db:
            row = self._row(db, key)
            return {"revision": row["revision"], "updated_at": row["updated_at"],
                    "updated_by": row["updated_by"], "snapshot": json.loads(row["snapshot"])}

    def init(self, key: str, actor: str) -> int:
        key = self._key(key)
        snapshot = initial_snapshot()
        validate_snapshot(snapshot)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM saves WHERE group_key=?", (key,)).fetchone():
                raise SaveError("该群已经有存档，禁止重复初始化覆盖")
            now = utcnow()
            payload = canonical(snapshot)
            db.execute("INSERT INTO saves VALUES (?,?,?,?,?)", (key, 1, payload, now, actor))
            db.execute("INSERT INTO audit(group_key,revision,actor,action,reason,before_json,after_json,timestamp) VALUES (?,?,?,?,?,?,?,?)",
                       (key, 1, actor, "initialize", "管理员初始化", None, payload, now))
        return 1

    def _mutate(self, key: str, actor: str, expected_revision: int, action: str, reason: str, fn) -> int:
        if type(expected_revision) is not int or expected_revision < 1:
            raise SaveError("必须提供有效的当前修订号")
        if not _text(reason, 250):
            raise SaveError("必须填写可审计的变更原因")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, key)
            if row["revision"] != expected_revision:
                raise SaveError(f"版本冲突：现有修订号为{row['revision']}，本次请求为{expected_revision}。请重新查询后再试")
            old = json.loads(row["snapshot"])
            new = json.loads(row["snapshot"])
            fn(new)
            validate_snapshot(new)
            if canonical(new) == canonical(old):
                raise SaveError("内容没有变化，未写入")
            rev = expected_revision + 1
            now = utcnow()
            payload = canonical(new)
            changed = db.execute("UPDATE saves SET revision=?,snapshot=?,updated_at=?,updated_by=? WHERE group_key=? AND revision=?",
                                 (rev, payload, now, actor, key, expected_revision)).rowcount
            if changed != 1:
                raise SaveError("保存失败：版本冲突")
            db.execute("INSERT INTO audit(group_key,revision,actor,action,reason,before_json,after_json,timestamp) VALUES (?,?,?,?,?,?,?,?)",
                       (key, rev, actor, action, reason, canonical(old), payload, now))
        return rev

    def join(self, key: str, actor: str, expected_revision: int, user_id: str, char_id: str, nickname: str):
        if not _text(user_id, 128) or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", char_id) or not _text(nickname, 80):
            raise SaveError("用户ID、角色号或昵称格式不合法（昵称不含空格更便于指令使用）")
        def change(s):
            if char_id in s["roles"]:
                raise SaveError("角色号已经被认领")
            if any(r["qq_user_id"] == user_id for r in s["roles"].values()):
                raise SaveError("同一QQ用户不能重复占用角色")
            if sum(r["status"] != "retired" for r in s["roles"].values()) >= 10:
                raise SaveError("已有10名非退团角色，请进入等候名单或由GM处理扩席")
            s["roles"][char_id] = new_role(user_id, char_id, nickname)
        return self._mutate(key, actor, expected_revision, "join", f"登记QQ用户{user_id}角色{char_id}", change)

    @staticmethod
    def _single_change(s: dict, target: str, field: str, value: Any):
        if target == "world":
            if field not in WORLD_FIELDS:
                raise SaveError("禁止修改未列入白名单的世界字段")
            if not WORLD_FIELDS[field](value):
                raise SaveError(f"字段{field}的数值或类型不合法")
            s["world"][field] = value
        elif target.startswith("role:"):
            char_id = target[5:]
            if char_id not in s["roles"]:
                raise SaveError("角色不存在")
            role = s["roles"][char_id]
            if field not in ROLE_FIELDS or field == "dossier_category_id":
                raise SaveError("该字段只读或需要专用操作")
            if not ROLE_FIELDS[field](value):
                raise SaveError(f"角色字段{field}类型不合法")
            if field == "dossier_stage":
                transitions = {"unrevealed": [], "ready": ["omen"], "omen": ["consented", "ready"],
                               "consented": ["revealed", "ready"], "revealed": []}
                old = role["dossier_stage"]
                if value not in transitions.get(old, []):
                    raise SaveError(f"档案阶段不允许从{old}改为{value}")
                if value == "revealed" and role["dossier_category_id"] is None:
                    raise SaveError("必须先登记经过公开掷骰并确认的类别ID")
            if field == "dossier_category_id" and (role["dossier_stage"] != "consented" or value is None):
                raise SaveError("玩家同意继续揭晓之前不能记录类别")
            role[field] = value
        else:
            raise SaveError("target必须是world或role:角色号")

    def patch_many(self, key: str, actor: str, expected_revision: int, changes: list, reason: str):
        if not isinstance(changes, list) or not 1 <= len(changes) <= 25:
            raise SaveError("changes必须是1-25条修改")
        def change(s):
            for op in changes:
                if not isinstance(op, dict) or set(op) != {"target", "field", "value"}:
                    raise SaveError("修改记录必须含target、field、value且不得有其他字段")
                self._single_change(s, op["target"], op["field"], op["value"])
        return self._mutate(key, actor, expected_revision, "patch", reason, change)

    def dossier_task(self, key: str, actor: str, expected_revision: int, character_id: str, task_id: str):
        if not _text(task_id, 100):
            raise SaveError("必须填写已完成任务的唯一ID")
        def change(s):
            r = s["roles"].get(character_id)
            if r is None:
                raise SaveError("角色不存在")
            if r["status"] == "retired":
                raise SaveError("退团角色不可新增档案进度")
            if task_id in r["dossier_completed_tasks"]:
                raise SaveError("该角色本任务已经计过一次进度")
            if r["dossier_progress"] >= 3:
                raise SaveError("档案已达到3/3")
            r["dossier_completed_tasks"].append(task_id)
            r["dossier_progress"] += 1
            if r["dossier_progress"] == 3:
                r["dossier_stage"] = "ready"
        return self._mutate(key, actor, expected_revision, "dossier_task", f"管理员确认{character_id}确有参与并完成任务{task_id}", change)

    def reveal(self, key: str, actor: str, expected_revision: int, char_id: str, category: int, fact: str, reward: str, evidence: str):
        if not _bounded_int(category, 1, 10) or not _text(fact, 1000) or not _text(reward, 300) or not _text(evidence, 200):
            raise SaveError("类别1-10、经玩家确认的事实及奖励均必填")
        def change(s):
            r = s["roles"].get(char_id)
            if r is None or r["dossier_stage"] != "consented" or r["dossier_progress"] != 3:
                raise SaveError("档案尚未经玩家确认进入揭晓环节")
            r["dossier_category_id"] = category
            r["dossier_content"] = fact
            r["dossier_public_evidence"] = evidence
            r["reward_option"] = reward
            r["dossier_stage"] = "revealed"
        return self._mutate(key, actor, expected_revision, "reveal", f"管理员确认玩家已同意且公开骰点：类别{category}，证据引用：{evidence}", change)

    def history(self, key: str, limit: int = 10) -> list[dict]:
        limit = min(max(limit, 1), 30)
        with self._db() as db:
            rows = db.execute("SELECT revision,actor,action,reason,timestamp FROM audit WHERE group_key=? ORDER BY id DESC LIMIT ?",
                              (self._key(key), limit)).fetchall()
            return [dict(r) for r in rows]

    def _group_folder(self, key: str):
        folder = self.export_root / hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def export(self, key: str, actor: str) -> tuple[str, Path]:
        data = self.read(key)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"{stamp}-{secrets.token_hex(4)}"
        payload = {"format": "neon_save_export_v1", "group_key": key, "backup_id": backup_id,
                   "source_revision": data["revision"], "exported_by": actor,
                   "exported_at": utcnow(), "snapshot": data["snapshot"],
                   "sha256": hashlib.sha256(canonical(data["snapshot"]).encode()).hexdigest()}
        path = self._group_folder(key) / (backup_id + ".json")
        with path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.flush()
            import os
            os.fsync(file.fileno())
        return backup_id, path

    def restore(self, key: str, actor: str, expected_revision: int, backup_id: str):
        if not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8}", backup_id):
            raise SaveError("备份ID不合法")
        path = self._group_folder(key) / (backup_id + ".json")
        if not path.is_file() or path.stat().st_size > 1200000:
            raise SaveError("备份不存在或文件过大")
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("format") != "neon_save_export_v1" or payload.get("group_key") != key or payload.get("backup_id") != backup_id:
            raise SaveError("拒绝跨群或格式不正确的备份")
        new_snapshot = payload.get("snapshot")
        validate_snapshot(new_snapshot)
        expected_hash = hashlib.sha256(canonical(new_snapshot).encode()).hexdigest()
        if payload.get("sha256") != expected_hash:
            raise SaveError("备份校验和不一致，拒绝恢复")
        def change(s):
            s.clear()
            s.update(new_snapshot)
        return self._mutate(key, actor, expected_revision, "restore", f"从备份{backup_id}恢复，保留审计历史", change)
