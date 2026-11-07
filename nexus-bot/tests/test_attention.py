"""注意力窗口：@ 之后免 @，参与者可多人，且只认参与者。"""

from __future__ import annotations

import time

from core.attention import AttentionBook


def test_open_then_owner_accepted():
    book = AttentionBook()
    book.open("g1", "111")
    assert book.accepts("g1", "111")
    assert not book.accepts("g1", "222")  # 没 @ 过的人机器人不接


def test_second_person_at_bot_joins_instead_of_taking_over():
    book = AttentionBook()
    book.open("g1", "111")
    book.open("g1", "222")  # 另一个人 @ 机器人 → 追加
    assert book.accepts("g1", "111")  # 第一个人还在
    assert book.accepts("g1", "222")
    assert book.get("g1").participants == {"111", "222"}


def test_initiator_is_first_opener():
    book = AttentionBook()
    book.open("g1", "111")
    book.open("g1", "222")
    assert book.get("g1").initiator == "111"


def test_other_group_unaffected():
    book = AttentionBook()
    book.open("g1", "111")
    assert not book.accepts("g2", "111")


def test_expired_window_closes():
    book = AttentionBook()
    win = book.open("g1", "111")
    win.last_active -= 601  # 10 分钟没动静
    assert book.get("g1") is None
    assert not book.accepts("g1", "111")


def test_touch_keeps_window_alive():
    book = AttentionBook()
    win = book.open("g1", "111")
    win.last_active -= 500
    win.touch()
    assert book.get("g1") is not None
    assert book.accepts("g1", "111")


def test_leave_removes_only_that_person():
    book = AttentionBook()
    book.open("g1", "111")
    book.open("g1", "222")
    book.leave("g1", "222")
    assert book.accepts("g1", "111")
    assert not book.accepts("g1", "222")


def test_leaving_last_person_closes_window():
    book = AttentionBook()
    book.open("g1", "111")
    book.leave("g1", "111")
    assert book.get("g1") is None


def test_close():
    book = AttentionBook()
    book.open("g1", "111")
    book.close("g1")
    assert not book.accepts("g1", "111")


def test_stage_switch():
    book = AttentionBook()
    book.open("g1", "111")
    book.set_stage("g1", "title")
    assert book.get("g1").stage == "title"


def test_default_ttl_is_ten_minutes():
    book = AttentionBook()
    win = book.open("g1", "111")
    assert 590 < win.ttl <= 600
    assert win.alive(now=time.time() + 100)


def test_runtime_holds_single_shared_instance():
    import core.runtime as first
    import core.runtime as second

    assert first.ATTENTION is second.ATTENTION
    assert first.GATE is second.GATE
    assert first.CONN is second.CONN
