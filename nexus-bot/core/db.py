"""SQLite 存档层（第 1 步：只放闸门需要的表）。

后面会往上加 characters / campaign_members / events 等，所以这里把连接与
建表集中管理，其他模块只拿 :func:`connect`。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nexus.db"

SCHEMA = """

CREATE TABLE IF NOT EXISTS seen_events (
    key        TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clues_found (
    group_id    TEXT NOT NULL,
    campaign_id INTEGER,
    clue_id     TEXT NOT NULL,
    found_by    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, clue_id)
);

CREATE TABLE IF NOT EXISTS combats (
    group_id    TEXT PRIMARY KEY,
    campaign_id INTEGER,
    scene       TEXT NOT NULL DEFAULT '',
    round       INTEGER NOT NULL DEFAULT 1,
    turn_index  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    factions    TEXT NOT NULL DEFAULT '{}',
    labels      TEXT NOT NULL DEFAULT '{}',
    modifiers   TEXT NOT NULL DEFAULT '[]',
    net         TEXT NOT NULL DEFAULT '{}',
    map         TEXT NOT NULL DEFAULT '',
    covers      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS combatants (
    group_id   TEXT NOT NULL,
    owner_qq   TEXT NOT NULL,
    name       TEXT NOT NULL,
    side       TEXT NOT NULL DEFAULT 'pc',
    initiative INTEGER NOT NULL DEFAULT 0,
    hp         INTEGER NOT NULL DEFAULT 0,
    max_hp     INTEGER NOT NULL DEFAULT 0,
    sp         INTEGER NOT NULL DEFAULT 0,
    sp_wear    INTEGER NOT NULL DEFAULT 0,
    damage     TEXT NOT NULL DEFAULT '',
    dead       INTEGER NOT NULL DEFAULT 0,
    script     TEXT NOT NULL DEFAULT '{}',
    stat       TEXT NOT NULL DEFAULT '',
    escaped    INTEGER NOT NULL DEFAULT 0,
    actor_id   TEXT NOT NULL DEFAULT '',
    fate       TEXT NOT NULL DEFAULT '',
    withdraw_at INTEGER NOT NULL DEFAULT 0,
    x          INTEGER NOT NULL DEFAULT -1,
    y          INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (group_id, owner_qq)
);

-- campaign 级演员表：战斗里被打死/打跑的具名角色留档，下一场 encounter 可按 ref 拉回来。
CREATE TABLE IF NOT EXISTS campaign_actors (
    campaign_id INTEGER NOT NULL,
    actor_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'enemy',
    status      TEXT NOT NULL DEFAULT 'active',
    hp          INTEGER NOT NULL DEFAULT 0,
    max_hp      INTEGER NOT NULL DEFAULT 0,
    scene       TEXT NOT NULL DEFAULT '',
    side        TEXT NOT NULL DEFAULT 'enemy',
    preset      TEXT NOT NULL DEFAULT '',
    fate        TEXT NOT NULL DEFAULT '',
    withdraw_at INTEGER NOT NULL DEFAULT 0,
    script      TEXT NOT NULL DEFAULT '{}',
    data        TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, actor_id)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER REFERENCES campaigns(id),
    group_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_group ON events(group_id, id);

CREATE TABLE IF NOT EXISTS campaigns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('active', 'paused', 'ended')),
    scene      TEXT NOT NULL DEFAULT '',
    scenario   TEXT NOT NULL DEFAULT 'demo',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_sessions (
    group_id    TEXT PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaigns(id),
    mode        TEXT NOT NULL DEFAULT 'idle' CHECK (mode IN ('idle', 'active', 'paused')),
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operators (
    qq       TEXT PRIMARY KEY,
    note     TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   TEXT NOT NULL,
    owner_qq   TEXT NOT NULL,
    name       TEXT NOT NULL,
    template   TEXT NOT NULL,
    concept    TEXT NOT NULL DEFAULT '',
    stats      TEXT NOT NULL,
    skills     TEXT NOT NULL,
    gear       TEXT NOT NULL DEFAULT '[]',
    perks      TEXT NOT NULL DEFAULT '[]',
    state      TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (group_id, owner_qq)
);

-- 简易版（RED Easy Mode）的角色卡：字段结构与 V3 不同（有 LUCK/EMP，没有 STR/CON），
-- 所以单独一张表；包里存原始模板数据 + 玩家的改名，展示与判定都由 cyberpunk/easy.py 负责。
CREATE TABLE IF NOT EXISTS party_turns (
    group_id    TEXT PRIMARY KEY,
    order_json  TEXT NOT NULL DEFAULT '[]',
    idx         INTEGER NOT NULL DEFAULT 0,
    round       INTEGER NOT NULL DEFAULT 1,
    assist_json TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS easy_cards (
    group_id   TEXT NOT NULL,
    owner_qq   TEXT NOT NULL,
    name       TEXT NOT NULL,
    template   TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, owner_qq)
);

-- ── 记忆与牌面（2026-11-14） ─────────────────────────────────────
-- 三层记忆（主人定）：
--   plot_marks  剧情标记，跨 NPC、属 campaign，进 DM 牌面
--   relations   NPC 的人际记忆（favor/promise/grudge/tell…），只在跟该 NPC 有关时读
--   exchanges   NPC 的对话流水，滚动 5 条
-- 旧的不删：靠 keywords 做硬检索补召回（见 core/memory.py）。

CREATE TABLE IF NOT EXISTS plot_marks (
    campaign_id INTEGER NOT NULL,
    mark_id     TEXT NOT NULL,
    scene       TEXT NOT NULL DEFAULT '',
    what        TEXT NOT NULL,
    weight      INTEGER NOT NULL DEFAULT 1,
    keywords    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, mark_id)
);

CREATE TABLE IF NOT EXISTS npc_memory (
    campaign_id INTEGER NOT NULL,
    npc_key     TEXT NOT NULL,
    met         INTEGER NOT NULL DEFAULT 0,
    -- 态度 0–10（5 平淡 / 10 友好 / 0 厌恶）；只影响对话，不参与任何检定
    attitude    INTEGER NOT NULL DEFAULT 5,
    -- 这个 NPC 能到的最恨与最亲，如 "[0,5]"（敌人）/"[3,7]"（中立）/"[5,10]"
    bounds      TEXT NOT NULL DEFAULT '[0,10]',
    relations   TEXT NOT NULL DEFAULT '[]',
    exchanges   TEXT NOT NULL DEFAULT '[]',
    impression  TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, npc_key)
);

-- 场景进度：判断玩家有没有偏离剧本（决策 D），以及提醒去重（决策 G）
CREATE TABLE IF NOT EXISTS scene_progress (
    campaign_id INTEGER NOT NULL,
    scene       TEXT NOT NULL,
    clues_found TEXT NOT NULL DEFAULT '[]',
    npcs_talked TEXT NOT NULL DEFAULT '[]',
    gates_passed TEXT NOT NULL DEFAULT '[]',
    off_track   INTEGER NOT NULL DEFAULT 0,
    nudged      TEXT NOT NULL DEFAULT '[]',
    -- 上一场战斗的结果：''（没打过）| won | lost | withdraw
    -- 剧情条件 battle:won 读它（engine/conditions.py 的 Battle）
    battle_result TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, scene)
);

-- ── 引擎层（2026-11-14 起） ──────────────────────────────────────
-- 通用标记：剧本自己起名的旗标（task:office_searched / got:ticket …）。
-- 刻意**不复用** scene_progress.gates_passed——那张表的列语义是「通过的闸门 id」，
-- 挪用语义正是旧结构漏风的根源。条件谓词 flag:X 读这张表。
CREATE TABLE IF NOT EXISTS campaign_flags (
    campaign_id INTEGER NOT NULL,
    name        TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, name)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """老库升级：characters 表补 perks / state 两列（SQLite 没有 ADD COLUMN IF NOT EXISTS）。"""
    camp = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
    if "scenario" not in camp:
        conn.execute("ALTER TABLE campaigns ADD COLUMN scenario TEXT NOT NULL DEFAULT 'demo'")

    have_c = {row[1] for row in conn.execute("PRAGMA table_info(combats)").fetchall()}
    for name, ddl in (("factions", "TEXT NOT NULL DEFAULT '{}'"),
                      ("labels", "TEXT NOT NULL DEFAULT '{}'"),
                      ("modifiers", "TEXT NOT NULL DEFAULT '[]'"),
                      ("net", "TEXT NOT NULL DEFAULT '{}'"),
                      ("map", "TEXT NOT NULL DEFAULT ''"),
                      ("covers", "TEXT NOT NULL DEFAULT '{}'")):
        if name not in have_c:
            conn.execute(f"ALTER TABLE combats ADD COLUMN {name} {ddl}")

    have_cm = {row[1] for row in conn.execute("PRAGMA table_info(combatants)").fetchall()}
    for name, ddl in (("script", "TEXT NOT NULL DEFAULT '{}'"),
                      ("stat", "TEXT NOT NULL DEFAULT ''"),
                      ("escaped", "INTEGER NOT NULL DEFAULT 0"),
                      ("actor_id", "TEXT NOT NULL DEFAULT ''"),
                      ("fate", "TEXT NOT NULL DEFAULT ''"),
                      ("withdraw_at", "INTEGER NOT NULL DEFAULT 0"),
                      ("x", "INTEGER NOT NULL DEFAULT -1"),
                      ("y", "INTEGER NOT NULL DEFAULT -1")):
        if name not in have_cm:
            conn.execute(f"ALTER TABLE combatants ADD COLUMN {name} {ddl}")

    have = {row[1] for row in conn.execute("PRAGMA table_info(characters)").fetchall()}
    for name, ddl in (("perks", "TEXT NOT NULL DEFAULT '[]'"), ("state", "TEXT NOT NULL DEFAULT '{}'")):
        if name not in have:
            conn.execute(f"ALTER TABLE characters ADD COLUMN {name} {ddl}")

    # 场景进度补 battle_result（引擎层的 battle:won/lost 条件要读它）
    have_sp = {row[1] for row in conn.execute("PRAGMA table_info(scene_progress)").fetchall()}
    if "battle_result" not in have_sp:
        conn.execute(
            "ALTER TABLE scene_progress ADD COLUMN battle_result TEXT NOT NULL DEFAULT ''"
        )
    conn.commit()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开连接并确保表存在。``:memory:`` 可传字符串用于测试。"""
    path = db_path if db_path is not None else DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    conn.commit()
    return conn
