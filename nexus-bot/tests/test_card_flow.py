import pytest

from core import card_flow, db
from cyberpunk.dice import ScriptedRoller


@pytest.fixture()
def conn(monkeypatch):
    c = db.connect(":memory:")
    monkeypatch.setattr(card_flow, "CONN", c)
    yield c
    c.close()


def test_parse_check_plain():
    assert card_flow.parse_check_arg("潜行") == ("潜行", 15, ())


def test_parse_check_with_dv():
    assert card_flow.parse_check_arg("潜行 难度18") == ("潜行", 18, ())
    assert card_flow.parse_check_arg("潜行 dv=21") == ("潜行", 21, ())


def test_parse_check_with_mods():
    skill, dv, mods = card_flow.parse_check_arg("手枪 难度17 修正-3")
    assert (skill, dv) == ("手枪", 17)
    assert mods == (-3,)


def test_parse_check_empty():
    assert card_flow.parse_check_arg("") == ("", 15, ())


def test_check_requires_skill(conn):
    assert "用法" in card_flow.check("g", "u", "")


def test_check_without_card(conn):
    assert "还没有卡" in card_flow.check("g", "u", "潜行")


def test_easy_check_strictly_greater(conn):
    """简易版：总数等于 DV 判失败（Interlock 平局守方胜）。"""
    from core import easy_roster
    from cyberpunk import easy

    tpl = easy.templates()[0]
    easy_roster.save(conn, "g1", "u1", tpl)
    card = easy_roster.get(conn, "g1", "u1")
    skill = next(iter(card["skills"]))
    stat = easy.skill_stat(skill) or "INT"
    dv = int(card["stats"][stat]) + int(card["skills"][skill]) + 5  # natural=5 → 平局

    assert card_flow.check("g1", "u1", skill, dv=dv, roller=ScriptedRoller([5])).startswith("❌")
    assert card_flow.check("g1", "u1", skill, dv=dv, roller=ScriptedRoller([6])).startswith("✅")


def test_claim_needs_confirmation(conn):
    """认领是两段式：先看卡，说「确认」才落库。"""
    from cyberpunk import easy

    name = easy.names()[0]
    preview = card_flow.claim("g2", "u2", name)
    assert "确认" in preview
    assert "你还没有卡" in card_flow.sheet("g2", "u2")

    done = card_flow.confirm_claim("g2", "u2")
    assert done and "生命" in done
    assert "生命" in card_flow.sheet("g2", "u2")


def test_claim_can_be_cancelled(conn):
    from cyberpunk import easy

    card_flow.claim("g2", "u3", easy.names()[0])
    assert card_flow.cancel_claim("g2", "u3") is not None
    assert card_flow.confirm_claim("g2", "u3") is None
    assert "你还没有卡" in card_flow.sheet("g2", "u3")


def test_claim_pending_expires(conn, monkeypatch):
    import time

    from cyberpunk import easy

    card_flow.claim("g2", "u4", easy.names()[0])
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + card_flow.CLAIM_TTL + 1)
    assert card_flow.pending_claim("g2", "u4") is None


def test_claim_replaces_red_card(conn):
    """领了简易版卡之后，「档案」必须显示新卡，不能还挂着旧的核心书卡。"""
    from cyberpunk import easy
    from core import roster

    roster.save(conn, "g2", "u5", roster.build(template="独狼", name="老卡"))
    assert "独狼" in card_flow.sheet("g2", "u5")

    name = easy.names()[0]
    card_flow.claim("g2", "u5", name)
    card_flow.confirm_claim("g2", "u5")
    shown = card_flow.sheet("g2", "u5")
    assert "老卡" not in shown
    assert "生命" in shown


def test_record_render(conn):
    from core import events

    events.record(conn, group_id="g3", kind="start", summary="开团《霓虹血债》")
    assert "霓虹血债" in card_flow.record("g3")


def test_looks_meta_detects_system_talk():
    for text in (
        "给我一个角色",
        "帮我建一张卡",
        "认领 火炬",
        "我的卡呢",
        "档案",
        "检定 潜行",
        "/r 2d6+1",
        "提升 手枪",
        "记录",
        "模板有哪些",
    ):
        assert card_flow.looks_meta(text), text


def test_looks_meta_ignores_in_fiction_actions():
    for text in (
        "我摸到仓库后门去",
        "我一枪打爆他的轮胎",
        "你们谁去跟摊主搭话",
        "我蹲下看看刹车痕",
        "",
    ):
        assert not card_flow.looks_meta(text), text


def test_looks_meta_is_anchored_at_sentence_start():
    # 句中带「记录 / 档案」是戏内行动，不能当系统操作
    assert not card_flow.looks_meta("看下控制台上的记录")
    assert not card_flow.looks_meta("我去翻他的档案柜")
    assert card_flow.looks_meta("记录")
    assert card_flow.looks_meta("记录 5")
    assert card_flow.looks_meta("档案")


def test_plain_have_card(conn):
    """「我有角色」不该惊动模型，直接弹卡。"""
    from cyberpunk import easy

    card_flow.claim("g4", "u9", easy.names()[0])
    card_flow.confirm_claim("g4", "u9")
    for text in ("我有角色", "我的卡", "档案"):
        assert "生命" in (card_flow._plain_command("g4", "u9", text) or ""), text


def test_start_keeps_window():
    """开团完成后窗口要留着，否则下一句得重新 @。"""
    import inspect

    from core import gate_flow

    src = inspect.getsource(gate_flow)
    block = src[src.index("campaign = GATE.start_new"):]
    block = block[: block.index("def _handle_intent")]
    assert "ATTENTION.set_stage" in block
    assert "ATTENTION.close" not in block


def test_window_only_closed_on_explicit_exit():
    """不变量：注意力窗口只能被用户显式退出关掉，状态转移一律不关。

    背景：开团/继续/暂停/结束都曾错误地调用 ATTENTION.close，
    结果是机器人刚说完「把角色卡发我」，下一句就得重新 @ 才听得见。
    """
    import inspect

    from core import gate_flow

    src = inspect.getsource(gate_flow)
    assert src.count("ATTENTION.close") == 1, "只允许 EXIT 分支关窗"

    exit_at = src.index("if intent is Intent.EXIT:")
    close_at = src.index("ATTENTION.close")
    assert close_at > exit_at, "关窗必须写在 EXIT 分支里"
    # 且 EXIT 分支内部就是它，不留别的关窗点
    assert "close_window" not in src, "close_window 字段已废弃，别再写"
