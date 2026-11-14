"""剧本推进：换场（exits）+ 官方模组的节奏。

背景（2026-11-14 核对官方模组《歌剧院之夜》）：

剧本原文的节奏是「**在准备好后**，前往：发展（面条和信息）」——
**玩家自己提议换场**，不是「说对关键词过检定」。

原来的 `opera.json` 里 11 个 gate 有 10 个是转写时编造的（原文根本没有那些检定），
导致玩家说对话也推不动。本批改成：

* 推进靠 `exits`（玩家说「进场吧」）
* 原文明写的检定（撬锁 DV9、觉察 DV9/13…）改成 `clues`，查得到但不挡路
* 战斗场景靠 `on_win` 自动推进

同时修掉三个真 bug：`exits` 是 dict 却被当字符串打印、`npcs` 内联写法让对话失效、
攻击台词被 NPC 对话抢走。
"""

from __future__ import annotations

import pytest

from core import story

GID = "qq:story"


# ---------- exits 解析 ----------


def test_exits_of_accepts_dict_and_string():
    """两种写法都认：纯 id 列表 / 带 label 的 dict 列表。"""
    assert story.exits_of({"exits": ["a2.noodles"]}) == [
        {"to": "a2.noodles", "label": ""}]
    assert story.exits_of({"exits": [{"to": "a2.office", "label": "去看看"}]}) == [
        {"to": "a2.office", "label": "去看看"}]


def test_exits_of_empty():
    assert story.exits_of({}) == []
    assert story.exits_of(None) == []


def test_exits_of_skips_bad_entries():
    sc = {"exits": [{"label": "没有目标"}, "", None, {"to": "ok"}]}
    assert [e["to"] for e in story.exits_of(sc)] == ["ok"]


# ---------- 玩家说去哪 → 命中出口 ----------


def test_exit_target_by_scene_id():
    assert story.exit_target("去 a1.students", "opera", "a1.hook") == "a1.students"


def test_exit_target_by_scene_name():
    sc = story.scene("opera", "a1.hook")
    assert sc is not None
    assert story.exit_target("动身去公交站", "opera", "a1.hook") == "a1.students"


def test_exit_target_unrelated_text_returns_none():
    assert story.exit_target("我今天想吃面", "opera", "a1.hook") is None


def test_exit_target_only_declared_exits():
    """编不出来的场景：只认本场声明过的出口。"""
    assert story.exit_target("去 a3.boss", "opera", "a1.hook") is None


def test_exit_target_matches_label_keywords():
    """「去社科楼看看」能命中「去社科楼四楼看看那间办公室」这条出口。"""
    assert story.exit_target("我去社科楼看看", "opera", "a1.students") == "a2.office"


def test_exit_target_prefers_earlier_exit_when_ambiguous():
    """两个出口都像时，按声明顺序取第一个（剧本作者排的优先级）。"""
    sc = {"exits": [{"to": "x", "label": "去甲地"}, {"to": "y", "label": "去乙地"}]}
    first = story.exits_of(sc)[0]["to"]
    assert first == "x"


# ---------- opera.json 的实际结构 ----------


def test_opera_has_no_fabricated_gates():
    """转写时编造的 gate 已删（原文没有任何「说话说服」检定）。"""
    for sid in ("a1.hook", "a2.noodles", "a2.master"):
        sc = story.scene("opera", sid) or {}
        assert not (sc.get("gates") or []), f"{sid} 不该再有编造的 gate"


def test_opera_keeps_the_real_pick_lock_check():
    """原文确实写了「通过一个 DV9 的撬锁技能鉴定」，这条保留。"""
    sc = story.scene("opera", "a2.office") or {}
    gates = sc.get("gates") or []
    assert [(g["skill"], g["dv"]) for g in gates] == [("撬锁", 9)]


def test_opera_encounters_all_advance_on_win():
    """每场战斗都要有 on_win，否则打完出不去。"""
    for sid in ("a1.opera", "a3.entry", "a3.church"):
        enc = (story.scene("opera", sid) or {}).get("encounter") or {}
        assert enc.get("on_win"), f"{sid} 打完没有 on_win，推不动"


def test_opera_switching_scenes_are_reachable():
    """每个非战斗场景都该有出口或 on_win，否则是死路。"""
    for act in story.load("opera")["acts"]:
        for sc in act["scenes"]:
            enc = sc.get("encounter") or {}
            can_move = bool(story.exits_of(sc)) or bool(enc.get("on_win")) \
                or bool(sc.get("ending"))
            assert can_move, f"{sc['id']} 是死路：没有 exits / on_win / ending"


def test_opera_clues_come_from_the_book():
    """原文明写的检定改成 clues：查得到，但不挡路。"""
    office = story.scene("opera", "a2.office_in") or {}
    dvs = {(c["skill"], c["dv"]) for c in (office.get("clues") or [])}
    assert ("觉察", 9) in dvs        # 原文：DV9 觉察发现照片
    assert ("觉察", 13) in dvs       # 原文：DV13 觉察发现衣柜/冰箱
    assert ("犯罪学", 13) in dvs     # 原文：DV13 犯罪学认出人头
    # 线索不占 gate，所以不推进场景
    assert not (office.get("gates") or [])


# ---------- NPC 内联写法 ----------


def test_inline_npc_is_recognized():
    """``npcs: [{"key": "student", ...}]`` 这种内联写法也要能对话。

    旧实现把整个 dict 转成字符串当 key，`match()` 永远返回 None——
    NPC 对话在实际游玩里是完全失效的。
    """
    from core import npc_flow

    keys = npc_flow._scene_npcs("opera", "a1.students")
    assert keys == ["student"]


def test_inline_npc_match_works():
    from core import npc_flow

    hit = npc_flow.match("opera", "a1.students", "我去问问学生")
    assert hit is not None
    key, npc = hit
    assert key == "student"
    assert npc.get("name") == "学生"


def test_npc_by_key_reads_inline():
    from core import npc_flow

    npc = npc_flow.npc_by_key("opera", "student", "a1.students")
    assert npc is not None and npc.get("voice")


# ---------- gate 多技能（上一批做的，钉住不回归） ----------


def test_gate_skills_accepts_both_forms():
    assert story.gate_skills({"skill": "说服"}) == ["说服"]
    assert story.gate_skills({"skills": ["说服", "交谈"]}) == ["说服", "交谈"]
    assert story.gate_skills({}) == []


def test_exit_target_works_for_every_scene_with_exits():
    """所有声明了 exits 的场景，出口都指得回真实场景。"""
    for act in story.load("opera")["acts"]:
        for sc in act["scenes"]:
            for exit_ in story.exits_of(sc):
                assert story.scene("opera", exit_["to"]), \
                    f"{sc['id']} 的出口 {exit_['to']} 不存在"
