"""把认领相关测试改成两步确认，并补互斥/取消用例。"""
import io

NL = chr(10)
P = "tests/test_card_flow.py"
src = io.open(P, encoding="utf-8").read()

old = '''def test_claim_and_sheet(conn):
    from cyberpunk import easy

    name = easy.names()[0]
    msg = card_flow.claim("g2", "u2", name)
    assert "生命" in msg or "技能" in msg
    assert "生命" in card_flow.sheet("g2", "u2")'''

new = '''def test_claim_needs_confirmation(conn):
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
    assert "生命" in shown'''

if old not in src:
    raise SystemExit("测试原文没匹配上")
src = src.replace(old, new, 1)
io.open(P, "w", encoding="utf-8", newline=NL).write(src)
print("tests patched")
