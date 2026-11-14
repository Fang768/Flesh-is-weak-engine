"""数据层：存档（engine/save.py）。

测的是「一次游玩的状态能不能被正确地读写出来」，以及**读写是否对称**
（能写的 mark_* 正好就是 conditions 能读的谓词）。

跨层验收在文件末尾：编译出的剧情图 + 存档 → 回答「现在能走哪些边」。
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from engine import conditions as C
from engine import save as SV
from engine import story as S


# ---------- 夹具 ----------


def _conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # 只建这一层用到的表（不依赖 core.db，保持第 1 层独立）
    conn.executescript(
        """
        CREATE TABLE campaigns (id INTEGER PRIMARY KEY, title TEXT, status TEXT,
            scene TEXT, scenario TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE group_sessions (group_id TEXT PRIMARY KEY,
            campaign_id INTEGER, mode TEXT, updated_at TEXT);
        CREATE TABLE clues_found (group_id TEXT, campaign_id INTEGER,
            clue_id TEXT, found_by TEXT, created_at TEXT,
            PRIMARY KEY (group_id, clue_id));
        CREATE TABLE scene_progress (campaign_id INTEGER, scene TEXT,
            clues_found TEXT DEFAULT '[]', npcs_talked TEXT DEFAULT '[]',
            gates_passed TEXT DEFAULT '[]', off_track INTEGER DEFAULT 0,
            nudged TEXT DEFAULT '[]', battle_result TEXT DEFAULT '',
            updated_at TEXT, PRIMARY KEY (campaign_id, scene));
        CREATE TABLE campaign_flags (campaign_id INTEGER, name TEXT,
            value TEXT DEFAULT '', created_at TEXT,
            PRIMARY KEY (campaign_id, name));
        CREATE TABLE characters (id INTEGER PRIMARY KEY, group_id TEXT,
            owner_qq TEXT, name TEXT, template TEXT, concept TEXT, stats TEXT,
            skills TEXT, gear TEXT, perks TEXT, state TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE easy_cards (group_id TEXT, owner_qq TEXT, name TEXT,
            template TEXT, data TEXT, created_at TEXT, updated_at TEXT,
            PRIMARY KEY (group_id, owner_qq));
        CREATE TABLE npc_memory (campaign_id INTEGER, npc_key TEXT,
            met INTEGER DEFAULT 0, attitude INTEGER DEFAULT 5,
            bounds TEXT DEFAULT '[0,10]', relations TEXT DEFAULT '[]',
            exchanges TEXT DEFAULT '[]', impression TEXT DEFAULT '[]',
            updated_at TEXT, PRIMARY KEY (campaign_id, npc_key));
        CREATE TABLE plot_marks (campaign_id INTEGER, mark_id TEXT, scene TEXT,
            what TEXT, weight INTEGER DEFAULT 1, keywords TEXT DEFAULT '[]',
            created_at TEXT, PRIMARY KEY (campaign_id, mark_id));
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER, group_id TEXT, kind TEXT, actor TEXT,
            summary TEXT, data TEXT DEFAULT '{}', created_at TEXT);
        """
    )
    conn.execute(
        "INSERT INTO campaigns VALUES (1,'团','active','a1.hook','opera','x','x')")
    conn.execute(
        "INSERT INTO group_sessions VALUES ('qq:1',1,'active','x')")
    conn.commit()
    return conn


@pytest.fixture()
def sv():
    conn = _conn()
    yield SV.Save(conn=conn, group_id="qq:1", campaign_id=1)
    conn.close()


# ---------- 进度 ----------


def test_scene_read_write(sv):
    assert sv.scene() == "a1.hook"
    sv.set_scene("a2.office")
    assert sv.scene() == "a2.office"


def test_missing_campaign_does_not_crash():
    conn = _conn()
    save = SV.Save(conn=conn, group_id="qq:1", campaign_id=999)
    assert save.scene() == ""
    assert save.world().clues == set()
    conn.close()


def test_open_save_finds_active_campaign():
    conn = _conn()
    got = SV.open_save(conn, "qq:1")
    assert got is not None and got.campaign_id == 1
    assert SV.open_save(conn, "qq:none") is None
    conn.close()


def test_open_save_ignores_ended_campaign():
    conn = _conn()
    conn.execute("UPDATE campaigns SET status='ended'")
    conn.commit()
    assert SV.open_save(conn, "qq:1") is None
    conn.close()


# ---------- 三个通用记录：幂等 + 读写对称 ----------


def test_mark_clue_is_idempotent(sv):
    assert sv.mark_clue("office_photo", by="u1") is True
    assert sv.mark_clue("office_photo") is False        # 第二次 no-op
    assert sv.clues() == ["office_photo"]
    assert sv.mark_clue("") is False                    # 空 id 不写


def test_mark_talked_with_and_without_topic(sv):
    assert sv.mark_talked("student") is True
    assert sv.mark_talked("student", "论文") is True
    assert sv.mark_talked("student", "论文") is False   # 重复
    got = sv.talked()
    assert "student" in got
    assert "论文" in got["student"]
    # 只记「聊过」也存在于 talked 里（空 topic 那条）
    assert sv.talked()["student"]


def test_mark_flag_is_idempotent_and_readable(sv):
    assert sv.mark_flag("task:office_searched") is True
    assert sv.mark_flag("task:office_searched") is True   # upsert，不报错
    assert sv.has_flag("task:office_searched") is True
    assert sv.has_flag("nope") is False
    assert "task:office_searched" in sv.flags()


def test_battle_result_roundtrip(sv):
    assert sv.battle_result() == ""
    sv.set_battle_result("won")
    assert sv.battle_result() == "won"
    sv.set_battle_result("")
    assert sv.battle_result() == ""


def test_battle_result_rejects_garbage(sv):
    with pytest.raises(SV.SaveError):
        sv.set_battle_result("大胜")


# ---------- world() 与条件对称 ----------


def test_world_reflects_every_mark(sv):
    sv.mark_clue("office_photo")
    sv.mark_talked("student", "论文")
    sv.mark_flag("got:ticket")
    sv.set_battle_result("won")

    ctx = C.Context(world=sv.world())
    assert C.Clue("office_photo").matches(ctx)
    assert C.Talked("student", "论文").matches(ctx)
    assert C.Talked("student").matches(ctx)
    assert C.Flag("got:ticket").matches(ctx)
    assert C.Battle("won").matches(ctx)
    assert not C.Clue("nope").matches(ctx)
    assert not C.Talked("student", "演出").matches(ctx)


def test_world_is_a_snapshot(sv):
    world = sv.world()
    sv.mark_clue("late")
    assert not world.has_clue("late"), "world() 应是快照，不是活视图"
    assert sv.world().has_clue("late")


def test_visited_tracks_scenes(sv):
    sv.mark_talked("student")          # 只为确保当前场景有 progress 行
    assert "a1.hook" in sv.visited()
    sv.set_scene("a2.office")
    sv.mark_talked("prof")
    assert set(sv.visited()) == {"a1.hook", "a2.office"}


# ---------- 玩家状态：两套卡归一化 ----------


def test_card_view_normalizes_red_card(sv):
    sv.conn.execute(
        "INSERT INTO characters (group_id, owner_qq, name, template, stats,"
        " skills, gear, perks, state, created_at, updated_at)"
        " VALUES ('qq:1','u1','阿飞','独狼',?,?,?,?,?,'x','x')",
        (json.dumps({"INT": 7, "REF": 8}), json.dumps({"潜行": 6}),
         json.dumps(["手枪"]), "[]",
         json.dumps({"hp": 30, "max_hp": 40, "sp": 11, "eb": 500,
                     "cyber": ["交互接口"]})),
    )
    sv.conn.commit()
    card = sv.card("u1")
    assert card.kind == "red"
    assert card.hp == 30 and card.max_hp == 40
    assert card.eb == 500
    assert card.skill("潜行") == 6
    assert card.stat("REF") == 8
    assert card.cyber == ["交互接口"]
    assert card.alive() is True


def test_card_view_normalizes_easy_card(sv):
    sv.conn.execute(
        "INSERT INTO easy_cards VALUES ('qq:1','u2','肆拾','游民',?, 'x','x')",
        (json.dumps({"hp": 34, "sp": 8, "stats": {"REF": 6},
                     "skills": {"手枪": 5}, "gear": ["中型手枪"],
                     "state": {"eb": 200}}),),
    )
    sv.conn.commit()
    card = sv.card("u2")
    assert card.kind == "easy"
    assert card.hp == 34 and card.max_hp == 34
    assert card.eb == 200
    assert card.stat("REF") == 6


def test_cards_lists_both_kinds(sv):
    sv.conn.execute(
        "INSERT INTO characters (group_id, owner_qq, name, template, stats,"
        " skills, gear, perks, state, created_at, updated_at)"
        " VALUES ('qq:1','u1','A','T','{}','{}','[]','[]','{}','x','x')")
    sv.conn.execute(
        "INSERT INTO easy_cards VALUES ('qq:1','u2','B','T',?, 'x','x')",
        (json.dumps({"hp": 10}),))
    sv.conn.commit()
    kinds = sorted(c.kind for c in sv.cards())
    assert kinds == ["easy", "red"]


def test_save_never_writes_player_state(sv):
    """这一版玩家状态**只读**：防止和 roster/easy_roster 抢写。"""
    public = [n for n in dir(SV.Save) if not n.startswith("_")]
    for name in public:
        assert "set_hp" != name and "set_eb" != name
    src = inspect.getsource(SV)
    for forbidden in ("UPDATE characters", "UPDATE easy_cards",
                      "INSERT INTO characters", "INSERT INTO easy_cards"):
        assert forbidden not in src, "存档层不该写玩家状态：" + forbidden


# ---------- 物品：装备 / 剧情道具 / 消耗品 ----------


def test_item_roundtrip_plain_gear_stays_string():
    """普通装备落库仍是裸字符串——旧数据零迁移。"""
    item = SV.Item(name="中型手枪")
    assert item.to_json() == "中型手枪"


def test_item_roundtrip_quest_keeps_kind():
    item = SV.Item(name="木桩", kind="quest", source="story")
    payload = item.to_json()
    assert payload == {"name": "木桩", "kind": "quest", "source": "story"}
    assert SV.as_item(payload) == item


def test_as_item_accepts_strings_and_items():
    assert SV.as_item("皮衣") == SV.Item(name="皮衣")
    assert SV.as_item({"name": "人头", "kind": "quest"}) == \
        SV.Item(name="人头", kind="quest")
    assert SV.as_item({"name": "  "}).name == ""


def test_card_gear_normalizes_mixed_rows(sv):
    """库里可能同时有旧字符串和新的 dict——读出来要统一成 Item。"""
    sv.conn.execute(
        "INSERT INTO easy_cards VALUES ('qq:1','u9','X','T',?, 'x','x')",
        (json.dumps({"hp": 20, "gear": [
            "皮衣",
            {"name": "木桩", "kind": "quest", "source": "story"},
            {"name": "演出票", "kind": "quest"},
        ]}),),
    )
    sv.conn.commit()
    card = sv.card("u9")
    assert card.item_names() == ["皮衣", "木桩", "演出票"]
    assert card.find_item("木桩").kind == "quest"
    assert card.find_item("皮衣").kind == "gear"
    assert card.has_item("演出票")


def test_world_items_include_quest_items(sv):
    sv.conn.execute(
        "INSERT INTO easy_cards VALUES ('qq:1','u9','X','T',?, 'x','x')",
        (json.dumps({"hp": 20, "gear": [
            {"name": "木桩", "kind": "quest"}]}),),
    )
    sv.conn.commit()
    assert C.Item("木桩").matches(C.Context(world=sv.world()))


# ---------- 记忆与牌面 ----------


def test_board_only_has_marks(sv):
    assert sv.board() == ""
    sv.conn.execute(
        "INSERT INTO plot_marks VALUES (1,'m1','a1.hook','玩家接下了委托',3,'[]','x')")
    sv.conn.execute(
        "INSERT INTO plot_marks VALUES (1,'m2','a1.hook','随手的杂事',1,'[]','x')")
    sv.conn.commit()
    board = sv.board()
    assert "玩家接下了委托" in board and "关键" in board
    assert board.index("玩家接下了委托") < board.index("随手的杂事")


def test_board_respects_limit(sv):
    for i in range(12):
        sv.conn.execute(
            "INSERT INTO plot_marks VALUES (1,?,'s',?,1,'[]','x')",
            ("m" + str(i), "事" + str(i)))
    sv.conn.commit()
    lines = [ln for ln in sv.board().splitlines() if ln.strip().startswith("·")]
    assert len(lines) == 8


def test_memory_defaults_when_absent(sv):
    mem = sv.memory("student")
    assert mem["met"] is False
    assert mem["attitude"] == 5
    assert mem["bounds"] == [0, 10]


def test_memory_reads_stored_row(sv):
    sv.conn.execute(
        "INSERT INTO npc_memory VALUES (1,'student',1,7,'[3,9]',?,?,'[]','x')",
        (json.dumps([{"what": "答应帮他找妹妹"}]), "[]"))
    sv.conn.commit()
    mem = sv.memory("student")
    assert mem["met"] is True and mem["attitude"] == 7
    assert mem["bounds"] == [3, 9]
    assert mem["relations"][0]["what"] == "答应帮他找妹妹"


# ---------- 事件 ----------


def test_record_and_read_events(sv):
    eid = sv.record("scene", "转到 办公室", data={"to": "a2.office"}, actor="u1")
    assert eid > 0
    got = sv.events(5)
    assert got[0]["kind"] == "scene"
    assert json.loads(got[0]["data"])["to"] == "a2.office"
    assert sv.events(5, kinds=("roll",)) == []


# ---------- 分层红线 ----------


def test_save_does_not_import_core():
    """第 1 层不许依赖旧 core/——否则以后删旧层会被拖着。"""
    src = pathlib.Path(SV.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("core"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("core"), alias.name


def test_save_public_api_stays_small():
    public = [n for n in dir(SV.Save) if not n.startswith("_")]
    assert len(public) < 30, "公开 API 太多了：" + str(public)


# ---------- 跨层验收：剧情图 + 存档 = 现在能去哪 ----------


def test_story_plus_save_answers_where_can_i_go(sv):
    """第 1 步的图 + 第 2 步的存档，拼出「当前能走哪些边」。"""
    story = S.compile_story("opera")
    sv.set_scene("a2.office")

    # 没撬锁技能：只有出口可走（闸门要 skill 匹配）
    cold = C.Context(world=sv.world())
    assert {e.to for e in story.available("a2.office", cold)} == {"a2.master"}

    # 说有撬锁技能：闸门也亮起来
    warm = C.Context(world=sv.world(), skill="撬锁")
    assert {e.to for e in story.available("a2.office", warm)} == {
        "a2.master", "a2.office_in", "a2.office"}


def test_conditional_exit_unlocks_after_mark(sv):
    """条件出口：聊过话题之前走不了，聊过之后就亮。"""
    raw = {
        "id": "t",
        "acts": [{"id": "a1", "scenes": [
            {"id": "s1", "name": "站台", "text": "", "exits": [
                {"to": "s2", "when": {"talked": {"npc": "student",
                                                 "topic": "论文"}}},
                {"to": "s2", "label": "直接进"},
            ]},
            {"id": "s2", "name": "办公室", "text": "", "ending": "e"},
        ]}],
        "endings": {"e": "完"},
    }
    story = S.compile_dict(raw)
    before = story.available("s1", C.Context(world=sv.world()))
    assert len(before) == 1 and before[0].label == "直接进"

    sv.mark_talked("student", "论文")
    after = story.available("s1", C.Context(world=sv.world()))
    assert len(after) == 2
