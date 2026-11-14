"""引擎层：原子动作（ops）+ 复杂动作（action）。

验收三件事：
1. 原子动作各自只做一件事，失败抛 OpError
2. 复杂动作在事务里：任何一步失败全部回滚（钱不会白扣）
3. 「买票」和「进场」是两个动作——先付钱、待会进场是数据表达
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from engine import action as A
from engine import conditions as C
from engine import ops as O
from engine import save as SV
from engine import story as S


def _conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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
    """)
    conn.execute("INSERT INTO campaigns VALUES (1,'团','active','s1','t','x','x')")
    conn.execute("INSERT INTO group_sessions VALUES ('qq:1',1,'active','x')")
    conn.commit()
    return conn


def _card(conn, owner="u1", eb=500, hp=30, skills=None, gear=None):
    conn.execute(
        "INSERT INTO characters (group_id, owner_qq, name, template, stats,"
        " skills, gear, perks, state, created_at, updated_at)"
        " VALUES ('qq:1',?,'A','T',?,?,'[]','[]',?,'x','x')",
        (owner, json.dumps({"REF": 8, "INT": 7, "COOL": 6, "DEX": 8,
                            "TECH": 5, "WILL": 5, "LUCK": 5, "MOVE": 5,
                            "BODY": 6, "EMP": 5}),
         json.dumps(skills or {"潜行": 6, "交易": 4}),
         json.dumps({"eb": eb, "hp": hp, "max_hp": 40, "sp": 11})),
    )
    conn.commit()


STORY = {
    "id": "t",
    "acts": [{"id": "a1", "goal": "g", "scenes": [
        {"id": "s1", "name": "站台", "text": "海报钉在站牌上。",
         "npcs": [], "clues": [
             {"id": "poster", "text": "演出在明晚。", "skill": "觉察",
              "dv": 9, "keywords": ["海报"]}],
         "exits": [{"to": "s2", "label": "进场",
                    "when": {"item": "演出票"}}],
         "actions": [
             {"id": "buy", "label": "一口价买票", "steps": [
                 {"op": "charge", "eb": 50},
                 {"op": "grant", "item": "演出票"}]},
             {"id": "enter", "label": "检票进场",
              "when": {"item": "演出票"},
              "steps": [
                  {"op": "take", "item": "演出票"},
                  {"op": "move", "to": "s2"}]},
             {"id": "broke", "label": "乱花钱",
              "steps": [{"op": "charge", "eb": 999}]},
             {"id": "bad_move", "label": "先付钱再换错场",
              "steps": [
                  {"op": "charge", "eb": 100},
                  {"op": "move", "to": "s2"}]},
         ]},
        {"id": "s2", "name": "大厅", "text": "蜡烛。", "ending": "e1"},
    ]}],
    "endings": {"e1": "完"},
}


@pytest.fixture()
def env():
    conn = _conn()
    _card(conn)
    save = SV.Save(conn=conn, group_id="qq:1", campaign_id=1)
    story = S.compile_dict(STORY)
    ops = O.Ops(story, save)
    ex = A.Executor(story, save)
    yield conn, save, story, ops, ex
    conn.close()


# ---------- 原子：roll 是纯计算 ----------


def test_roll_does_not_change_state(env):
    conn, save, story, ops, ex = env
    before = save.card("u1")
    result = ops.roll("潜行", 13, card=before)
    assert isinstance(result, O.RollResult)
    after = save.card("u1")
    assert after.eb == before.eb and after.hp == before.hp
    assert after.gear == before.gear


def test_roll_uses_skill_and_stat(env):
    conn, save, story, ops, ex = env
    card = save.card("u1")
    result = ops.roll("潜行", 5, card=card)
    # REF 8 + 潜行 6 = +14，任何 1D10 都过 DV5
    assert result.success
    assert result.total >= 1 + 14


# ---------- 原子：charge 只接受增量，不够就炸 ----------


def test_charge_subtracts_and_adds(env):
    conn, save, story, ops, ex = env
    ops.charge("u1", eb=-50)
    assert save.card("u1").eb == 450
    ops.charge("u1", eb=25)
    assert save.card("u1").eb == 475


def test_charge_refuses_overspend_without_touching_money(env):
    conn, save, story, ops, ex = env
    with pytest.raises(O.OpError):
        ops.charge("u1", eb=-999)
    assert save.card("u1").eb == 500


def test_charge_never_goes_negative_hp(env):
    conn, save, story, ops, ex = env
    ops.charge("u1", hp=-100)
    assert save.card("u1").hp == 0


# ---------- 原子：grant / take ----------


def test_grant_then_take(env):
    conn, save, story, ops, ex = env
    ops.grant("u1", "演出票", kind="quest")
    card = save.card("u1")
    assert card.has_item("演出票")
    assert card.find_item("演出票").kind == "quest"
    ops.grant("u1", "演出票")                    # 幂等
    assert save.card("u1").item_names().count("演出票") == 1
    ops.take("u1", "演出票")
    assert not save.card("u1").has_item("演出票")
    with pytest.raises(O.OpError):
        ops.take("u1", "演出票")                 # 没有就失败


# ---------- 原子：move 只认本场出口 + 条件 ----------


def test_move_rejects_unknown_target(env):
    conn, save, story, ops, ex = env
    with pytest.raises(O.OpError) as exc:
        ops.move("s9")
    assert "不是本场能走的路" in str(exc.value)


def test_move_rejects_when_condition_unmet(env):
    conn, save, story, ops, ex = env
    with pytest.raises(O.OpError):
        ops.move("s2")                            # 还没票
    assert save.scene() == "s1"


def test_move_works_after_granting_ticket(env):
    conn, save, story, ops, ex = env
    ops.grant("u1", "演出票")
    ops.move("s2")
    assert save.scene() == "s2"


# ---------- 原子：mark 只认三种 + 校验线索属于本场 ----------


def test_mark_clue_checks_scene_membership(env):
    conn, save, story, ops, ex = env
    with pytest.raises(O.OpError) as exc:
        ops.mark("clue", "not_here")
    assert "本场没有这条线索" in str(exc.value)
    ops.mark("clue", "poster")
    assert "poster" in save.clues()


def test_mark_rejects_unknown_kind(env):
    conn, save, story, ops, ex = env
    with pytest.raises(O.OpError):
        ops.mark("teleport", "x")


def test_mark_talked_and_flag(env):
    conn, save, story, ops, ex = env
    ops.mark("talked", "student", topic="论文")
    ops.mark("flag", "got:ticket")
    world = save.world()
    assert world.talked_about("student", "论文")
    assert world.has_flag("got:ticket")


# ---------- 原子：reveal 只读 ----------


def test_reveal_returns_text(env):
    conn, save, story, ops, ex = env
    got = ops.reveal("clue:poster")
    assert "演出在明晚" in got.data["text"]
    assert ops.reveal("scene:s1").data["text"].startswith("站台")
    with pytest.raises(O.OpError):
        ops.reveal("nope:x")


# ---------- 复杂动作：买票与进场分开 ----------


def test_buy_and_enter_are_two_actions(env):
    """先付钱、待会进场——不需要 DM 临场拼。"""
    conn, save, story, ops, ex = env

    report = ex.perform("buy")
    assert report.ok
    assert save.card("u1").eb == 450
    assert save.card("u1").has_item("演出票")
    assert save.card("u1").find_item("演出票").kind == "quest"
    assert save.scene() == "s1", "买票不该换场"

    # 中间可以干别的
    ops.mark("clue", "poster")
    assert save.scene() == "s1"

    enter = ex.perform("enter")
    assert enter.ok
    assert save.scene() == "s2"
    assert "演出票" not in save.card("u1").gear, "进场要交票"


def test_enter_without_ticket_is_not_in_catalog(env):
    """没票时「进场」不出现在目录里——引擎知道，DM 不用记。"""
    conn, save, story, ops, ex = env
    ids = [a.id for a in ex.catalog()]
    assert "enter" not in ids
    assert "buy" in ids


def test_catalog_lists_action_after_ticket(env):
    conn, save, story, ops, ex = env
    ops.grant("u1", "演出票")
    ids = [a.id for a in ex.catalog()]
    assert "enter" in ids


# ---------- 事务：失败全部回滚 ----------


def test_failed_charge_rolls_back_everything(env):
    conn, save, story, ops, ex = env
    report = ex.perform("broke")
    assert not report.ok
    assert "EB 不够" in report.reason
    assert save.card("u1").eb == 500, "失败不该扣钱"


def test_failure_mid_action_rolls_back_earlier_steps(env):
    """先扣 100 再换场失败 → 100 必须退回。"""
    conn, save, story, ops, ex = env
    report = ex.perform("bad_move")
    assert not report.ok
    assert save.card("u1").eb == 500, "换场失败，前面扣的钱要退回来"
    assert save.scene() == "s1"


# ---------- 编译期：move 在 charge 之前要拦住 ----------


def test_move_before_charge_is_rejected():
    with pytest.raises(A.ActionError) as exc:
        A.parse_action({"id": "x", "steps": [
            {"op": "move", "to": "s2"},
            {"op": "charge", "eb": 10}]})
    assert "move 出现在 charge 之前" in str(exc.value)


def test_unknown_when_is_rejected():
    with pytest.raises(A.ActionError):
        A.parse_action({"id": "x", "steps": [
            {"op": "charge", "eb": 1, "when": "maybe"}]})


# ---------- 旧剧本自动编译成 Action ----------


def test_legacy_exits_become_move_actions():
    story = S.compile_story("opera")
    actions = {a.id: a for a in story.actions_of("a1.hook")}
    got = actions.get("go:a1.students")
    assert got is not None
    assert [s.op for s in got.steps] == ["move"]


def test_legacy_gate_becomes_roll_then_move():
    story = S.compile_story("opera")
    actions = {a.id: a for a in story.actions_of("a2.office")}
    gate = actions["gate:office_lock"]
    assert [s.op for s in gate.steps] == ["roll", "move"]
    assert gate.steps[0].args["skill"] == "撬锁"
    assert gate.steps[0].args["dv"] == 9
    assert gate.steps[1].when == "success"


def test_legacy_clue_becomes_roll_mark_reveal():
    story = S.compile_story("opera")
    actions = {a.id: a for a in story.actions_of("a2.office_in")}
    clue = actions["clue:office_photo"]
    assert [s.op for s in clue.steps] == ["roll", "mark", "reveal"]
    assert clue.steps[1].when == "success"


def test_legacy_encounter_becomes_battle_then_move():
    story = S.compile_story("opera")
    actions = {a.id: a for a in story.actions_of("a3.church")}
    fight = actions["battle:a3.church"]
    assert fight.steps[0].op == "battle"
    assert fight.steps[1].when == "battle_won"


# ---------- 真实剧本：买票 → 待会进场 ----------


def test_opera_ticket_flow_end_to_end():
    """剧本里没写「买票」，但可以补 Action；补齐后流程成立。"""
    raw = json.loads(pathlib.Path(
        "story/opera.json").read_text(encoding="utf-8"))
    for act in raw["acts"]:
        for scene in act["scenes"]:
            if scene["id"] == "a1.students":
                # 只给歌剧院的出口加「要有票」；去办公室那条保留，
                # 否则 a2.office 变成不可达（编译期会拦）。
                scene["exits"] = [
                    {"to": "a1.opera", "label": "检票进场",
                     "when": {"item": "演出票"}},
                    {"to": "a2.office", "label": "去社科楼四楼看看"},
                ]
                scene["actions"] = [
                    {"id": "buy_ticket", "label": "一口价买票",
                     "steps": [{"op": "charge", "eb": 50},
                               {"op": "grant", "item": "演出票"}]},
                    {"id": "enter_opera", "label": "检票进场",
                     "when": {"item": "演出票"},
                     "steps": [{"op": "take", "item": "演出票"},
                               {"op": "move", "to": "a1.opera"}]},
                ]
    conn = _conn()
    _card(conn)
    save = SV.Save(conn=conn, group_id="qq:1", campaign_id=1)
    save.set_scene("a1.students")
    story = S.compile_dict(raw)
    ex = A.Executor(story, save)

    assert "enter_opera" not in [a.id for a in ex.catalog()]

    assert ex.perform("buy_ticket").ok
    assert save.card("u1").eb == 450
    assert save.scene() == "a1.students"          # 买票不换场

    assert ex.perform("enter_opera").ok
    assert save.scene() == "a1.opera"
    assert not save.card("u1").has_item("演出票")
    conn.close()


# ---------- 红线 ----------


def test_ops_has_no_verb_specific_methods():
    """原子层不许出现「买票」「撬锁」这类方法——那些是数据。"""
    for name in ("buy", "buy_ticket", "pick_lock", "haggle", "sneak_in",
                 "search_wardrobe"):
        assert not hasattr(O.Ops, name), name


def test_ops_public_api_is_small():
    public = [n for n in dir(O.Ops) if not n.startswith("_")]
    assert len(public) < 15, public


def test_action_never_parses_player_text():
    """引擎不读玩家原话——那是 LLM 的事。

    判据：Action/Ops 里不出现正则匹配、子串查找、以及「玩家原话」这类
    参数。旧 turn_flow 的 45 个 if 正是这样一点点长出来的。
    """
    for module in (A, O):
        src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", "")
                assert name not in ("match", "fullmatch", "search"), (
                    module.__name__ + " 出现了文本匹配：" + name)
        # 函数签名里不许出现 player_text / user_text 这类入参
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                for bad in ("player_text", "user_text", "utterance"):
                    assert bad not in args, (
                        module.__name__ + "." + node.name + " 收了玩家原话")
