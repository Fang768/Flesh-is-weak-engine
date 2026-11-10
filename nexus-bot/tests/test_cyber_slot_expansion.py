"""插槽扩容：复眼改造 / 传感器阵列。

规则依据（``rules/red_core/raw/full.txt``）：

* **复眼改造** ``:10473-10478``：「使用者最多能够再**加装 5 个额外的赛博义眼**」
  → 这是「能装几只眼」的额度，不是每只眼的槽。
* **传感器阵列** ``:10479-10485``：「最多能够再获得 **5 个额外的赛博音频插槽**，
  需要赛博音频套装，此改造不占用它原有的插槽」→ 加的是插槽数。

两者的 ``slots`` 都是 0：扩容件本身不占原有插槽。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, db, growth, roster
from cyberpunk import dice, slots

GID = "qq:expand"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, eb=50000):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    growth.grant_eb(card, eb)
    roster.save(conn, GID, "u1", card)
    return roster.get(conn, GID, "u1")


def _roll(*v):
    return dice.ScriptedRoller(list(v))


# ---------- 数据 ----------


def test_expansion_items_are_declared():
    assert slots.capacity_bonus("复眼改造") == {"base": "赛博义眼", "eyes": 5}
    assert slots.capacity_bonus("传感器阵列") == {"base": "赛博音频套装", "slots": 5}


def test_expansion_items_take_no_slots():
    """扩容件本身不占原有插槽。"""
    assert slots.slots_of("复眼改造") == 0
    assert slots.slots_of("传感器阵列") == 0


def test_normal_items_have_no_capacity_bonus():
    assert slots.capacity_bonus("瞄准目镜") == {}
    assert slots.capacity_bonus("赛博义眼") == {}


# ---------- 义眼件数上限 ----------


def test_two_eyes_by_default():
    assert slots.max_instances("赛博义眼", []) == 2


def test_third_eye_refused_by_default():
    msg = slots.check_install(["赛博义眼", "赛博义眼"], "赛博义眼")
    assert "最多装 2 件" in msg


def test_third_eye_allowed_with_retinal_upgrade():
    installed = ["赛博义眼", "赛博义眼", "复眼改造"]
    assert slots.max_instances("赛博义眼", installed) == 7
    assert slots.check_install(installed, "赛博义眼") == ""


def test_seven_eyes_is_the_hard_cap():
    installed = ["复眼改造"] + ["赛博义眼"] * 7
    msg = slots.check_install(installed, "赛博义眼")
    assert "最多装 7 件" in msg


def test_retinal_upgrade_needs_an_eye_first():
    msg = slots.check_install([], "复眼改造")
    assert "赛博义眼" in msg


def test_no_limit_for_non_paired_bases():
    """神经链路不像眼球那样成对，不该有 2 件上限。"""
    assert slots.max_instances("神经链路", []) == 99


# ---------- 音频插槽扩容 ----------


def test_audio_capacity_default_is_three():
    assert slots.effective_capacity("赛博音频套装", []) == 3


def test_sensor_array_adds_five_audio_slots():
    assert slots.effective_capacity("赛博音频套装", ["传感器阵列"]) == 8


def test_sensor_array_needs_audio_suit_first():
    msg = slots.check_install([], "传感器阵列")
    assert "赛博音频套装" in msg


def test_audio_slots_actually_usable_after_expansion():
    """装满 3 个音频件后本来装不下，加了阵列就能再装。"""
    installed = ["赛博音频套装", "听觉放大", "语气分析", "内置代理"]
    assert slots.capacity_left(installed, "赛博音频套装") == 0
    # 没阵列时第 4 件装不下
    assert "槽不够" in slots.check_install(installed, "录音机")
    # 装了阵列就够
    with_array = installed + ["传感器阵列"]
    assert slots.capacity_left(with_array, "赛博音频套装") == 5
    assert slots.check_install(with_array, "录音机") == ""


# ---------- 接线：真实安装 ----------


def test_install_retinal_upgrade_then_third_eye(conn):
    _pc(conn)
    for _ in range(2):
        assert "装上了" in cyber_flow.install(
            conn, GID, "u1", "安装 赛博义眼", roller=_roll(2, 2))
    text = cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(2, 2))
    assert "最多装 2 件" in text

    assert "装上了" in cyber_flow.install(
        conn, GID, "u1", "安装 复眼改造", roller=_roll(2, 2, 2, 2))
    text = cyber_flow.install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(2, 2))
    assert "装上了" in text


def test_install_sensor_array_widens_audio(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 赛博音频套装", roller=_roll(2, 2))
    text = cyber_flow.install(conn, GID, "u1", "安装 传感器阵列",
                              roller=_roll(2, 2, 2, 2))
    assert "装上了" in text
    card = roster.get(conn, GID, "u1")
    assert slots.effective_capacity("赛博音频套装", card["state"]["cyber"]) == 8


def test_describe_slots_reflects_expansion(conn):
    _pc(conn)
    lines = slots.describe_slots(["赛博音频套装", "传感器阵列", "听觉放大"])
    assert any("赛博音频套装" in x and "/8" in x for x in lines), lines
