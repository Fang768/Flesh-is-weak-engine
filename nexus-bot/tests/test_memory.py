"""NPC 记忆与 DM 牌面。

主人 2026-11-14 定的设计：

* **三层记忆**（按「何时被读」分区，不按「重不重要」）：
  A 剧情标记进牌面、B 人际记忆只在跟该 NPC 有关时读、C 对话流水滚动 5 条。
* **旧的不删**：B 层超过 10 条时靠 keywords 硬检索补召回（最多 5 条）。
* **牌面 8 条预算**；「悬而未决」是状态不是记忆，**不占**预算。
* **态度 0–10**，5 平淡；只影响对话，且锁在 ``bounds`` 内。
"""

from __future__ import annotations

import pytest

from core import attitude, db, memory

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


# ---------- 表结构 ----------


def test_new_tables_exist(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"plot_marks", "npc_memory", "scene_progress"} <= names


def test_blank_memory_defaults(conn):
    mem = memory.blank_memory()
    assert mem["attitude"] == 5          # 平淡
    assert mem["bounds"] == [0, 10]
    assert mem["relations"] == [] and mem["exchanges"] == []


# ---------- A 层：剧情标记 ----------


def test_mark_roundtrip(conn):
    memory.add_mark(conn, CID, scene="a1.hook", what="玩家接下了委托",
                    weight=3, keywords=["委托", "莱茵迈耶"])
    marks = memory.plot_marks(conn, CID)
    assert len(marks) == 1
    assert marks[0]["what"] == "玩家接下了委托"
    assert marks[0]["weight"] == 3
    assert marks[0]["keywords"] == ["委托", "莱茵迈耶"]


def test_mark_dedupe_by_id(conn):
    """同一 mark_id 重复写不重复计数（重跑同一句话不该刷牌面）。"""
    for _ in range(3):
        memory.add_mark(conn, CID, scene="a1", what="拿到定金",
                        mark_id="m:pay")
    assert memory.mark_count(conn, CID) == 1


def test_empty_mark_ignored(conn):
    assert memory.add_mark(conn, CID, scene="a1", what="   ") == ""
    assert memory.mark_count(conn, CID) == 0


def test_marks_sorted_by_weight(conn):
    memory.add_mark(conn, CID, scene="a1", what="小事", weight=1, mark_id="a")
    memory.add_mark(conn, CID, scene="a1", what="关键转折", weight=3, mark_id="b")
    memory.add_mark(conn, CID, scene="a1", what="中等", weight=2, mark_id="c")
    got = [m["what"] for m in memory.plot_marks(conn, CID)]
    assert got == ["关键转折", "中等", "小事"]


def test_board_respects_eight_mark_budget(conn):
    """牌面 8 条预算（决策 1：暂定，测试后再调）。"""
    for i in range(12):
        memory.add_mark(conn, CID, scene="a1", what=f"第{i}件事",
                        weight=2, mark_id=f"m{i}")
    text = memory.board(conn, CID, "opera", "a1.hook")
    assert sum(1 for ln in text.splitlines() if ln.strip().startswith("·")) == 8


def test_board_empty_when_nothing(conn):
    """既没标记、也没未查线索 → 牌面为空（a1.hook 有线索定义，所以换一场）。"""
    assert memory.board(conn, CID, "opera", "act1.dock") == ""


# ---------- B 层：人际记忆 + 关键词召回 ----------


def test_relation_is_append_only(conn):
    """决策 2：超过 10 条**不删**。"""
    for i in range(20):
        memory.add_relation(conn, CID, "rayfield", kind="tell",
                            what=f"聊到第{i}件事", keywords=[f"事{i}"])
    assert len(memory.relations_of(conn, CID, "rayfield")) == 20


def test_recall_returns_recent_ten_by_default(conn):
    for i in range(20):
        memory.add_relation(conn, CID, "rayfield", kind="tell",
                            what=f"聊到第{i}件事", keywords=[f"关键词{i}"])
    got = memory.recall(conn, CID, "rayfield", "今天天气不错")
    assert len(got) == memory.RECENT_RELATIONS == 10
    assert got[-1]["what"] == "聊到第19件事"


def test_recall_brings_back_old_by_keyword(conn):
    """这正是「防止 NPC 健忘」的机制：老记忆靠关键词捞回来。"""
    memory.add_relation(conn, CID, "rayfield", kind="promise",
                        what="答应帮他找妹妹", keywords=["妹妹", "找人"])
    for i in range(15):        # 把它挤出最近 10 条
        memory.add_relation(conn, CID, "rayfield", kind="tell",
                            what=f"闲聊{i}", keywords=[f"闲{i}"])
    cold = memory.recall(conn, CID, "rayfield", "今天天气不错")
    assert all("妹妹" not in r["what"] for r in cold), "不提关键词就不该翻出来"

    warm = memory.recall(conn, CID, "rayfield", "你妹妹找到了吗")
    assert any("妹妹" in r["what"] for r in warm), "提到关键词要能回忆起来"


def test_recall_caps_extra(conn):
    """补召回最多 5 条（决策 2）。关键词要 ≥2 字才认。"""
    for i in range(10):
        memory.add_relation(conn, CID, "rayfield", kind="tell",
                            what=f"旧账{i}", keywords=["旧账"])
    for i in range(memory.RECENT_RELATIONS):
        memory.add_relation(conn, CID, "rayfield", kind="tell",
                            what=f"新事{i}", keywords=["新事"])
    got = memory.recall(conn, CID, "rayfield", "我们来清清旧账吧")
    assert len(got) == memory.RECENT_RELATIONS + memory.RECALL_EXTRA


def test_keyword_needs_two_chars(conn):
    """单字关键词会误伤正常对话（同 clues.match 的经验）。"""
    memory.add_relation(conn, CID, "n1", kind="tell", what="提到了血",
                        keywords=["血"])
    for i in range(11):
        memory.add_relation(conn, CID, "n1", kind="tell", what=f"x{i}",
                            keywords=["xx"])
    got = memory.recall(conn, CID, "n1", "他流血了")
    assert all(r["what"] != "提到了血" for r in got)


# ---------- C 层：对话流水 ----------


def test_exchanges_roll_to_five(conn):
    for i in range(8):
        memory.add_exchange(conn, CID, "rayfield", "玩家", f"第{i}句")
    mem = memory.load_memory(conn, CID, "rayfield")
    assert len(mem["exchanges"]) == memory.EXCHANGES_KEPT == 5
    assert mem["exchanges"][-1]["text"] == "第7句"


# ---------- 存盘往返 ----------


def test_memory_persists(conn):
    memory.meet(conn, CID, "russwin", bounds=[0, 5])
    memory.add_relation(conn, CID, "russwin", kind="grudge",
                        what="被玩家打伤", delta=-2, keywords=["打伤"])
    memory.add_exchange(conn, CID, "russwin", "玩家", "你好")

    mem = memory.load_memory(conn, CID, "russwin")
    assert mem["met"] == 1
    assert mem["bounds"] == [0, 5]
    assert len(mem["relations"]) == 1
    assert mem["relations"][0]["delta"] == -2
    assert len(mem["exchanges"]) == 1


def test_meet_keeps_first_bounds(conn):
    """bounds 只在第一次确定（决策 E：由剧本/模型判一次）。"""
    memory.meet(conn, CID, "clown", bounds=[0, 5])
    memory.meet(conn, CID, "clown", bounds=[0, 10])
    assert memory.load_memory(conn, CID, "clown")["bounds"] == [0, 5]


def test_impression_field_reserved(conn):
    """决策 3：有趣记忆先不写，但字段留好。"""
    mem = memory.load_memory(conn, CID, "nobody")
    assert mem["impression"] == []
    mem["impression"] = [{"what": "假的，占位"}]
    memory.save_memory(conn, CID, "nobody", mem)
    assert memory.load_memory(conn, CID, "nobody")["impression"]


# ---------- 场景进度 ----------


def test_progress_roundtrip(conn):
    memory.remember_in_scene(conn, CID, "a1.hook", "clues_found", "hook_missing")
    prog = memory.load_progress(conn, CID, "a1.hook")
    assert prog["clues_found"] == ["hook_missing"]
    assert prog["off_track"] == 0


def test_unresolved_counts_remaining_clues(conn):
    """悬而未决只给**存在性**提示，不给答案。"""
    memory.add_mark(conn, CID, scene="a1.hook", what="接委托", weight=3)
    text = memory.board(conn, CID, "opera", "a1.hook")
    assert "⚠" in text
    assert "线索" in text


def test_unresolved_not_in_mark_budget(conn):
    """决策：悬而未决不占那 8 条预算。"""
    for i in range(8):
        memory.add_mark(conn, CID, scene="a1.hook", what=f"事{i}",
                        weight=3, mark_id=f"m{i}")
    text = memory.board(conn, CID, "opera", "a1.hook")
    dots = [ln for ln in text.splitlines() if ln.strip().startswith("·")]
    warns = [ln for ln in text.splitlines() if ln.strip().startswith("⚠")]
    assert len(dots) == 8
    assert warns, "即使预算满了，悬而未决仍要显示"


# ---------- 态度（决策 E） ----------


def test_attitude_bands():
    assert attitude.band_of(0) == "厌恶"
    assert attitude.band_of(5) == "平淡"
    assert attitude.band_of(10) == "亲近"
    assert attitude.band_of(6) == "友善"


def test_enemy_cannot_become_friendly(conn):
    """「敌人无论怎么示好都不会对你友善，顶多平淡」。"""
    attitude.set_bounds(conn, CID, "clown", [0, 5])
    for _ in range(10):
        attitude.apply_delta(conn, CID, "clown", +1)
    assert attitude.current(conn, CID, "clown") == 5
    assert attitude.band_of(attitude.current(conn, CID, "clown")) == "平淡"


def test_neutral_stays_in_the_middle(conn):
    """「有些人保持中立，怎么样也不会特别厌恶或者友善」。"""
    attitude.set_bounds(conn, CID, "student", [3, 7])
    for _ in range(10):
        attitude.apply_delta(conn, CID, "student", +1)
    assert attitude.current(conn, CID, "student") == 7
    for _ in range(20):
        attitude.apply_delta(conn, CID, "student", -1)
    assert attitude.current(conn, CID, "student") == 3


def test_crossing_band_is_reported(conn):
    """只有跨越档位才值得旁白一句。"""
    attitude.set_bounds(conn, CID, "rayfield", [0, 10])
    old, new, crossed = attitude.apply_delta(conn, CID, "rayfield", +1)
    assert (old, new, crossed) == (5, 6, True)      # 平淡 → 友善
    old, new, crossed = attitude.apply_delta(conn, CID, "rayfield", +1)
    assert crossed is False, "6 → 7 仍是「友善」，不重复提示"


def test_apply_delta_clamps(conn):
    attitude.set_bounds(conn, CID, "rayfield", [0, 10])
    attitude.apply_delta(conn, CID, "rayfield", +10)
    assert attitude.current(conn, CID, "rayfield") == 10
    attitude.apply_delta(conn, CID, "rayfield", -20)
    assert attitude.current(conn, CID, "rayfield") == 0


def test_bad_bounds_fall_back(conn):
    attitude.set_bounds(conn, CID, "x", ["奶", "茶"])
    assert attitude.bounds_of(conn, CID, "x") == (0, 10)


def test_reversed_bounds_are_fixed(conn):
    attitude.set_bounds(conn, CID, "x", [8, 2])
    assert attitude.bounds_of(conn, CID, "x") == (2, 8)


def test_tone_hint_mentions_band(conn):
    hint = attitude.tone_hint(9, "雷菲尔德")
    assert "雷菲尔德" in hint and "亲近" in hint


def test_describe_change_is_subtle():
    """委婉：不报数字、不说「态度 +1」。"""
    up = attitude.describe_change("雷菲尔德", 5, 6)
    assert "雷菲尔德" in up
    assert "+1" not in up and "6" not in up
    down = attitude.describe_change("雷菲尔德", 5, 4)
    assert "雷菲尔德" in down
