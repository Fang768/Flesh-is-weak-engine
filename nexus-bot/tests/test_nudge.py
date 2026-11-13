"""偏离提醒 + 闸门副作用。

设计（主人 2026-11-14）：

* **偏离提醒**：玩家做的事跟剧本无关时委婉拨回来。连续 3 次空转触发，
  本场每类一次（内容引导 / 时间压力），不剧透。
* **闸门副作用**：``on_success`` / ``on_fail`` **只做两件事**——
  写剧情标记、调 NPC 态度。不做策反、不改战斗数值。
"""

from __future__ import annotations

import pytest

from core import attitude, db, memory, nudge, turn

CID = 1


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    c.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at,"
        " updated_at) VALUES (?, '测试团', 'active', 'a1.hook', 'opera', 'x', 'x')",
        (CID,),
    )
    c.commit()
    return c


# ---------- 空转计数 ----------


def test_note_offtrack_accumulates(conn):
    assert nudge.note_offtrack(conn, CID, "a1.hook") == 1
    assert nudge.note_offtrack(conn, CID, "a1.hook") == 2
    assert nudge.note_offtrack(conn, CID, "a1.hook") == 3


def test_reset_zeroes(conn):
    nudge.note_offtrack(conn, CID, "a1.hook")
    nudge.reset_offtrack(conn, CID, "a1.hook")
    assert memory.load_progress(conn, CID, "a1.hook")["off_track"] == 0


def test_no_nudge_below_threshold(conn):
    for _ in range(nudge.OFFTRACK_LIMIT - 1):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a1.hook") == ""


def test_nudge_fires_at_threshold(conn):
    for _ in range(nudge.OFFTRACK_LIMIT):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a1.hook", has_left=True) == nudge.KIND_CONTENT


def test_content_first_then_time(conn):
    """还有线索没查 → 先内容引导；用过 → 改时间压力。"""
    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a1.hook", has_left=True) == nudge.KIND_CONTENT
    nudge.mark_nudged(conn, CID, "a1.hook", nudge.KIND_CONTENT)

    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a1.hook", has_left=False) == nudge.KIND_TIME


def test_each_kind_only_once_per_scene(conn):
    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    nudge.mark_nudged(conn, CID, "a1.hook", nudge.KIND_CONTENT)
    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    nudge.mark_nudged(conn, CID, "a1.hook", nudge.KIND_TIME)
    for _ in range(5):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a1.hook", has_left=True) == ""


def test_mark_nudged_resets_counter(conn):
    """提醒过后计数清零，避免连环催。"""
    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    nudge.mark_nudged(conn, CID, "a1.hook", nudge.KIND_CONTENT)
    assert memory.load_progress(conn, CID, "a1.hook")["off_track"] == 0


def test_scene_isolation(conn):
    """一个场景的空转不污染另一个场景。"""
    for _ in range(3):
        nudge.note_offtrack(conn, CID, "a1.hook")
    assert nudge.should_nudge(conn, CID, "a2.noodles") == ""


# ---------- 文案（不剧透） ----------


def test_fallback_texts_exist():
    assert nudge.FALLBACK[nudge.KIND_CONTENT]
    assert nudge.FALLBACK[nudge.KIND_TIME]


def test_fallback_has_no_spoiler_words():
    for text in nudge.FALLBACK.values():
        for word in ("凶手", "真相", "答案", "线索", "剧情", "任务"):
            assert word not in text, f"兜底文案不该出现「{word}」"


def test_nudge_system_forbids_spoilers():
    sys_prompt = nudge.NUDGE_SYSTEM
    assert "不剧透" in sys_prompt
    assert "不" in sys_prompt


def test_attitude_system_forbids_numbers():
    assert "不报数字" in nudge.ATTITUDE_SYSTEM or "数字" in nudge.ATTITUDE_SYSTEM


# ---------- 闸门副作用 ----------


def _outcome(grade: str):
    """``grade`` 是只读属性，由 ``success`` / ``fumble`` 决定。"""
    return turn.Outcome(
        skill="说服", natural=8, total=16, dv=15, margin=1,
        success=(grade == "成功"),
        scene_from="a1.hook", scene_to="a1.students",
    )


@pytest.fixture()
def wired(conn, monkeypatch):
    """把 turn_flow 的模块级 CONN 指到测试库（_apply_gate_effects 用它）。"""
    from core import turn_flow

    monkeypatch.setattr(turn_flow, "CONN", conn, raising=False)
    return conn


def test_gate_on_success_writes_mark(wired):
    from core import turn_flow

    gate = {"id": "g1", "skill": "说服", "dv": 15,
            "on_success": {"mark": {"what": "玩家接下了委托", "weight": 3,
                                    "keywords": ["委托"]}}}
    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", gate, _outcome("成功"))
    marks = memory.plot_marks(wired, CID)
    assert any(m["what"] == "玩家接下了委托" and m["weight"] == 3 for m in marks)


def test_gate_on_fail_uses_its_own_spec(wired):
    from core import turn_flow

    gate = {"id": "g1", "skill": "说服", "dv": 15,
            "on_success": {"mark": {"what": "成功的事"}},
            "on_fail": {"mark": {"what": "被拒绝了"}}}
    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", gate, _outcome("失败"))
    whats = [m["what"] for m in memory.plot_marks(wired, CID)]
    assert "被拒绝了" in whats and "成功的事" not in whats


def test_gate_mark_accepts_plain_string(wired):
    from core import turn_flow

    gate = {"id": "g1", "on_success": {"mark": "简短标记"}}
    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", gate, _outcome("成功"))
    assert any(m["what"] == "简短标记" for m in memory.plot_marks(wired, CID))


def test_gate_shifts_attitude(wired):
    from core import turn_flow

    gate = {"id": "g1", "on_success": {"npc_change": {"rayfield": 1}}}
    before = attitude.current(wired, CID, "rayfield")
    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", gate, _outcome("成功"))
    assert attitude.current(wired, CID, "rayfield") == before + 1


def test_gate_effects_never_touch_combat_numbers(conn):
    """态度只影响对话——引擎里没有任何抽查定去用态度的地方。"""
    import inspect

    from core import turn_flow

    src = inspect.getsource(turn_flow._apply_gate_effects)
    for forbidden in ("hp", "damage", "dv", "sp", "攻击", "命中"):
        # dv 只可能出现在注释里；实际赋值语句不该有
        assert f"{forbidden} =" not in src, f"闸门副作用不该改 {forbidden}"


def test_unknown_gate_is_noop(wired):
    from core import turn_flow

    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", None, _outcome("成功"))
    assert memory.mark_count(wired, CID) == 0


def test_bad_npc_change_ignored(wired):
    from core import turn_flow

    gate = {"id": "g1", "on_success": {"npc_change": {"x": "不是数字"}}}
    turn_flow._apply_gate_effects(CID, "opera", "a1.hook", gate, _outcome("成功"))
    # 不炸就行
