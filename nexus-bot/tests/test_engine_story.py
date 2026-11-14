"""数据层：剧本编译成场景图 + 剧情条件。

这一层不碰数据库、不碰 LLM。测的是「剧本能不能被正确抽象成一条剧情线」。

关键约束（2026-11-14 定）：**引擎不认识具体动作**。
「买票」「撬锁」「炸炮塔」都不是方法，它们是数据里的边 + 条件。
本测试文件同时是这条红线的看门人：出现 `def buy_ticket` 这类东西，
test_no_verb_specific_api 会失败。
"""

from __future__ import annotations

import inspect

import pytest

from engine import conditions as C
from engine import story as S


# ---------- 条件：解析 ----------


def test_parse_when_always_and_never():
    assert isinstance(C.parse_when(None), C.Always)
    assert isinstance(C.parse_when("always"), C.Always)
    assert isinstance(C.parse_when("never"), C.Never)


def test_parse_when_simple_forms():
    assert C.parse_when({"battle": "won"}) == C.Battle("won")
    assert C.parse_when({"skill": "撬锁"}) == C.Skill("撬锁")
    assert C.parse_when({"clue": "office_photo"}) == C.Clue("office_photo")
    assert C.parse_when({"flag": "task:office"}) == C.Flag("task:office")
    assert C.parse_when({"talked": "student"}) == C.Talked("student")
    assert C.parse_when({"talked": {"npc": "student", "topic": "论文"}}) == \
        C.Talked("student", "论文")


def test_parse_when_combinators():
    got = C.parse_when({"all": [{"clue": "a"}, {"battle": "won"}]})
    assert isinstance(got, C.All) and len(got.parts) == 2
    got = C.parse_when({"any": [{"clue": "a"}, {"flag": "b"}]})
    assert isinstance(got, C.AnyOf)
    got = C.parse_when({"not": {"battle": "lost"}})
    assert isinstance(got, C.Not)


def test_parse_when_rejects_unknown():
    """认不出的条件必须炸——不许静默降级成「永远走不通」。"""
    with pytest.raises(C.ConditionError):
        C.parse_when({"teleport": "moon"})
    with pytest.raises(C.ConditionError):
        C.parse_when("todo")
    with pytest.raises(C.ConditionError):
        C.parse_when({"clue": "a", "flag": "b"})


# ---------- 条件：判定 ----------


def _ctx(**kw):
    world = C.World(**{k: v for k, v in kw.items() if k in
                       ("clues", "talked", "flags", "battle", "visited")})
    return C.Context(world=world, skill=kw.get("skill", ""),
                     text=kw.get("text", ""))


def test_always_matches_and_never_does_not():
    assert C.Always().matches(_ctx())
    assert not C.Never().matches(_ctx())


def test_skill_matches_only_that_skill():
    assert C.Skill("撬锁").matches(_ctx(skill="撬锁"))
    assert not C.Skill("撬锁").matches(_ctx(skill="交易"))
    assert not C.Skill("撬锁").matches(_ctx())


def test_clue_flag_battle_read_world():
    assert C.Clue("x").matches(_ctx(clues={"x"}))
    assert not C.Clue("x").matches(_ctx(clues={"y"}))
    assert C.Flag("won_game").matches(_ctx(flags={"won_game"}))
    assert C.Battle("won").matches(_ctx(battle="won"))
    assert not C.Battle("won").matches(_ctx(battle=""))


def test_talked_with_and_without_topic():
    world = _ctx(talked={"student": set()})
    assert C.Talked("student").matches(world)
    assert not C.Talked("student", "论文").matches(world)   # 没记话题
    noted = _ctx(talked={"student": {"论文"}})
    assert C.Talked("student", "论文").matches(noted)
    assert not C.Talked("student", "演出").matches(noted)


def test_combinators():
    ctx = _ctx(clues={"a"}, battle="won")
    assert C.All((C.Clue("a"), C.Battle("won"))).matches(ctx)
    assert not C.All((C.Clue("a"), C.Battle("lost"))).matches(ctx)
    assert C.AnyOf((C.Clue("zzz"), C.Battle("won"))).matches(ctx)
    assert not C.Not(C.Battle("won")).matches(ctx)


def test_describe_is_human_readable():
    assert C.Clue("office_photo").describe() == "clue:office_photo"
    assert C.Talked("student", "论文").describe() == "talked:student/论文"
    text = C.All((C.Clue("a"), C.Battle("won"))).describe()
    assert "clue:a" in text and "battle:won" in text


# ---------- 剧本编译 ----------


def test_opera_compiles():
    story = S.compile_story("opera")
    assert story.id == "opera"
    assert story.first_scene() == "a1.hook"
    # 炮塔与鲁思文是同一场战斗（原文 :102），所以 a3.boss 已并入 a3.church
    assert len(story.scenes) == 10
    assert [a.id for a in story.acts] == ["act1", "act2", "act3"]


def test_opera_graph_matches_the_hand_computed_line():
    """整条剧情线：这一条是本次重构的验收标准。

    a1.hook -> a1.students -> a1.opera -> a2.noodles -> a2.master
            -> a3.entry -> a3.church -> a3.ending
                        ↘ a2.office -> a2.office_in ↘

    注意 a3.church 一场打完直接进结局——鲁思文是这一场的第二波
    （join_when: all_dead 炮塔），不是另一个房间。
    """
    story = S.compile_story("opera")
    graph = story.graph()
    assert graph["a1.hook"] == ["a1.students"]
    assert set(graph["a1.students"]) == {"a1.opera", "a2.office"}
    assert "a2.noodles" in graph["a1.opera"]
    # 拉面车也能去办公室（原文 :69/:71「如果对此进行跟进」）
    assert set(graph["a2.noodles"]) == {"a2.master", "a2.office"}
    assert set(graph["a2.office"]) == {"a2.master", "a2.office_in", "a2.office"}
    assert graph["a2.office_in"] == ["a2.master"]
    assert graph["a2.master"] == ["a3.entry"]
    assert "a3.church" in graph["a3.entry"]
    assert "a3.ending" in graph["a3.church"]
    assert "a3.boss" not in graph


def test_battle_edges_carry_their_result_condition():
    """战斗退出条件归剧情层：不是「打赢了就一定推进」，而是带 battle:won 的边。"""
    story = S.compile_story("opera")
    edges = {e.to: e for e in story.outgoing("a3.church") if e.kind != "ending"}
    assert "a3.ending" in edges
    assert edges["a3.ending"].when == C.Battle("won")
    lose = [e for e in story.outgoing("a3.church") if e.kind == "ending"]
    assert lose and lose[0].to == "opera_dead"
    assert lose[0].when == C.Battle("lost")


def test_gate_edges_carry_skill_and_check():
    """技能闸门带 check；失败落点也在边上。"""
    story = S.compile_story("opera")
    gates = [e for e in story.outgoing("a2.office") if e.kind == "gate"]
    assert {g.to for g in gates} == {"a2.office_in", "a2.office"}
    success = next(g for g in gates if g.to == "a2.office_in")
    assert success.check["skill"] == "撬锁"
    assert success.check["dv"] == 9
    assert success.when == C.Skill("撬锁")


def test_demo_gates_become_edges_with_multiple_skills():
    """demo 的一节有多道门（潜行/交谈/手枪）——它们都是边，不是 if。"""
    story = S.compile_story("demo")
    gates = [e for e in story.outgoing("act1.dock") if e.kind == "gate"]
    assert {e.check["skill"] for e in gates} == {"潜行", "交谈", "手枪"}
    assert all(e.to == "act1.warehouse" or e.on_fail for e in gates)


def test_goal_is_available_for_the_dm():
    """每个场景都能问出「这一节要干什么」——DM 需要它才不会瞎演。"""
    story = S.compile_story("opera")
    assert "委托" in story.goal_of("a1.hook")
    assert "邀请" in story.goal_of("a2.noodles")
    assert "露西" in story.goal_of("a3.ending")


def test_npcs_are_visible_with_inline_override():
    story = S.compile_story("opera")
    students = story.scene("a1.students")
    assert students.npc_keys() == ["student"]
    assert story.npc("student", "a1.students")["name"] == "学生"
    # 顶层定义也能取到
    assert story.npc("rayfield")["name"] == "雷菲尔德"


def test_clues_are_structured_not_text():
    story = S.compile_story("opera")
    office = story.scene("a2.office_in")
    by_id = {c.id: c for c in office.clues}
    assert by_id["office_photo"].skill == "觉察"
    assert by_id["office_photo"].dv == 9
    assert "芭芭拉" in by_id["office_photo"].text


# ---------- 编译期校验（写错要炸） ----------


def _base(**over):
    raw = {
        "id": "t",
        "title": "测试",
        "acts": [{"id": "a1", "title": "一", "goal": "g",
                  "scenes": [{"id": "s1", "name": "起点", "text": "",
                              "exits": ["s2"]},
                             {"id": "s2", "name": "终点", "text": "",
                              "ending": "e1"}]}],
        "endings": {"e1": "完"},
    }
    raw.update(over)
    return raw


def test_compile_minimal_story():
    story = S.compile_dict(_base())
    assert story.graph() == {"s1": ["s2"], "s2": []}


def test_dangling_exit_is_rejected():
    raw = _base()
    raw["acts"][0]["scenes"][0]["exits"] = ["ghost"]
    with pytest.raises(S.StoryError) as exc:
        S.compile_dict(raw)
    assert "场景不存在" in str(exc.value)
    assert "ghost" in str(exc.value)


def test_dangling_ending_is_rejected():
    raw = _base()
    raw["acts"][0]["scenes"][1]["ending"] = "nope"
    with pytest.raises(S.StoryError) as exc:
        S.compile_dict(raw)
    assert "ending 不存在" in str(exc.value)


def test_dead_end_is_rejected():
    """没有出边、也不是结局的场景 = 玩家会卡死。编译期就要拦。"""
    raw = _base()
    raw["acts"][0]["scenes"][1].pop("ending")
    with pytest.raises(S.StoryError) as exc:
        S.compile_dict(raw)
    assert "死路" in str(exc.value)


def test_unreachable_scene_is_rejected():
    raw = _base()
    raw["acts"][0]["scenes"].append({"id": "island", "name": "孤岛", "text": "",
                                     "ending": "e1"})
    with pytest.raises(S.StoryError) as exc:
        S.compile_dict(raw)
    assert "不可达" in str(exc.value)


def test_duplicate_scene_id_is_rejected():
    raw = _base()
    raw["acts"][0]["scenes"].append({"id": "s1", "name": "重复", "text": "",
                                     "ending": "e1"})
    with pytest.raises(S.StoryError) as exc:
        S.compile_dict(raw)
    assert "重复" in str(exc.value)


def test_conditional_exit_is_supported():
    """「如果跟进就前往支线」——条件写在边上，不是隐藏 if。"""
    raw = _base()
    raw["acts"][0]["scenes"][0]["exits"] = [
        {"to": "s2", "when": {"talked": {"npc": "student", "topic": "论文"}}}]
    story = S.compile_dict(raw)
    edge = story.outgoing("s1")[0]
    assert edge.when == C.Talked("student", "论文")


def test_non_check_exit_has_no_check():
    """Gate 跟掷骰不绑定：不写 check 的边就是「直接过去」。"""
    story = S.compile_story("opera")
    exits = [e for e in story.outgoing("a1.students") if e.kind == "exit"]
    assert exits and all(e.check is None for e in exits)


# ---------- 当前能走哪些边 ----------


def test_available_filters_by_condition():
    """条件没满足的边不可走；战斗边不在这里返回（由战斗层告知）。"""
    story = S.compile_story("opera")

    # 没聊过学生、也没票：两条出口都不满足条件
    cold = C.Context(world=C.World())
    assert story.available("a1.students", cold) == []

    # 有票 → 能进场；聊过论文 → 能去办公室
    warm = C.Context(world=C.World(items={"演出票"},
                                   talked={"student": {"论文"}}))
    assert {e.to for e in story.available("a1.students", warm)} == {
        "a1.opera", "a2.office"}

    # 加了条件之后，条件不满足就不可走
    raw = _base()
    raw["acts"][0]["scenes"][0]["exits"] = [
        {"to": "s2", "when": {"talked": {"npc": "student"}}},
        {"to": "s2", "when": "always", "label": "备用"},
    ]
    story2 = S.compile_dict(raw)
    got = story2.available("s1", C.Context(world=C.World()))
    assert len(got) == 1 and got[0].label == "备用"
    warm = C.Context(world=C.World(talked={"student": set()}))
    assert len(story2.available("s1", warm)) == 2


def test_available_excludes_battle_edges():
    story = S.compile_story("opera")
    ctx = C.Context(world=C.World(battle="won"))
    got = story.available("a3.church", ctx)
    assert all(e.kind != "battle" for e in got)


# ---------- 红线：不许为具体动作写方法 ----------


def test_no_verb_specific_api():
    """引擎里不许出现「买票 / 撬锁 / 炸炮塔」这类专用方法。

    这些都是数据（边 + 条件），不是代码。一旦有人加 `def buy_ticket`，
    这条路就会越走越窄，最后回到 45 个 if 的老路。
    """
    forbidden = ("buy_ticket", "pick_lock", "blow_up", "haggle", "sneak_in")
    for module in (S, C):
        names = dir(module)
        for name in forbidden:
            assert name not in names, (module.__name__ + " 出现了专用方法 " + name)
    # 公开 API 里也不许有「动词 + 名词」形状的一堆函数
    public = [n for n in dir(S) if not n.startswith("_")]
    assert len(public) < 40, "公开 API 太多了，说明又开始往里堆功能了"
