"""闸门与意图识别的测试（不启动 NoneBot）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect  # noqa: E402
from core.intents import Intent, classify, classify_confirm  # noqa: E402
from core.session import Gate, Mode  # noqa: E402

GID = "1121562905"


@pytest.fixture()
def gate() -> Gate:
    return Gate(connect(":memory:"))


# ---------- 意图 ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我们开个团吧", Intent.START),
        ("跑团跑团", Intent.START),
        ("开新剧本", Intent.START),
        ("从上次继续", Intent.RESUME),
        ("接着上次跑", Intent.RESUME),
        ("先暂停一下", Intent.PAUSE),
        ("存个档", Intent.PAUSE),
        ("结束吧，收工", Intent.END),
        ("现在什么进度", Intent.STATUS),
        ("怎么用啊", Intent.HELP),
        ("今天天气不错", Intent.UNKNOWN),
        ("", Intent.UNKNOWN),
    ],
)
def test_classify_intents(text, expected):
    assert classify(text) is expected


def test_resume_wins_over_start_when_both_present():
    # 一句话里既有「继续」又有「开团」→ 按断点继续
    assert classify("从上次继续开团") is Intent.RESUME


def test_end_wins_over_pause():
    assert classify("不跑了，结束吧") is Intent.END


# ---------- 确认 ----------


def test_confirm_resume_and_new():
    assert classify_confirm("继续", has_paused=True) == "resume"
    assert classify_confirm("接着上次", has_paused=True) == "resume"
    assert classify_confirm("开新的", has_paused=True) == "new"
    assert classify_confirm("重新开一个", has_paused=True) == "new"


def test_confirm_plain_yes_follows_available_choice():
    assert classify_confirm("好", has_paused=True) == "resume"
    assert classify_confirm("好", has_paused=False) == "new"


def test_confirm_cancel_and_unknown():
    assert classify_confirm("算了", has_paused=True) == "cancel"
    assert classify_confirm("取消吧", has_paused=False) == "cancel"
    assert classify_confirm("嗯……让我想想", has_paused=True) is None


def test_confirm_resume_only_when_paused_exists():
    # 没有断点时「继续」不该被当成 resume
    assert classify_confirm("继续", has_paused=False) != "resume"


# ---------- 状态机 ----------


def test_idle_by_default(gate):
    state = gate.state(GID)
    assert state.mode is Mode.IDLE
    assert state.campaign is None
    assert gate.paused_campaign(GID) is None


def test_start_new_activates(gate):
    campaign = gate.start_new(GID, "霓虹血债")
    assert campaign.title == "霓虹血债"
    state = gate.state(GID)
    assert state.mode is Mode.ACTIVE
    assert state.campaign.id == campaign.id


def test_pause_then_resume_keeps_same_campaign(gate):
    campaign = gate.start_new(GID, "霓虹血债")
    gate.set_scene(campaign.id, "第二幕：码头")

    paused = gate.pause(GID)
    assert paused.id == campaign.id
    assert gate.state(GID).mode is Mode.PAUSED
    assert gate.paused_campaign(GID).id == campaign.id

    resumed = gate.resume(GID)
    assert resumed.id == campaign.id
    assert gate.state(GID).mode is Mode.ACTIVE
    assert gate.state(GID).campaign.scene == "第二幕：码头"


def test_resume_without_paused_returns_none(gate):
    assert gate.resume(GID) is None


def test_end_returns_to_idle_and_keeps_record(gate):
    campaign = gate.start_new(GID, "霓虹血债")
    ended = gate.end(GID)
    assert ended.id == campaign.id
    state = gate.state(GID)
    assert state.mode is Mode.IDLE
    assert state.campaign is None
    assert gate.paused_campaign(GID) is None  # 已归档的不再提示续跑


def test_second_group_is_independent(gate):
    gate.start_new(GID, "A 群的主线")
    assert gate.state("999").mode is Mode.IDLE
    gate.start_new("999", "B 群的主线")
    assert gate.state(GID).campaign.title == "A 群的主线"
    assert gate.state("999").campaign.title == "B 群的主线"


def test_operators_whitelist(gate):
    assert gate.is_operator("123456") is False
    gate.add_operator("123456", "DM")
    assert gate.is_operator("123456") is True


def test_state_survives_reconnect(gate, tmp_path):
    """真文件库：关掉再开，断点还在。"""
    db = tmp_path / "nexus.db"
    g1 = Gate(connect(db))
    campaign = g1.start_new(GID, "持久化测试")
    g1.pause(GID)
    g1.conn.close()

    g2 = Gate(connect(db))
    state = g2.state(GID)
    assert state.mode is Mode.PAUSED
    assert g2.paused_campaign(GID).title == "持久化测试"
    assert g2.resume(GID).id == campaign.id
