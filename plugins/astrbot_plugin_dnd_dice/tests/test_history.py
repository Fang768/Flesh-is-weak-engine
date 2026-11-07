"""history.py 单元测试：RollHistoryManager 的读写行为与并发安全。

覆盖点：
  - add() 写入后 get_all() 能读回，且字段已经过 _sanitize 清洗。
  - 超过 max_count 时截断，仅保留最新记录。
  - 失败投掷（以 ROLL_ERROR_PREFIXES 开头）不写入历史。
  - get_by_sender 按发送者过滤。
  - clear() 返回被删除条数并清空存储。
  - enabled=False 时 add() 静默跳过，不产生任何 KV 写入。
  - 并发回归（F1/F2）：多个 add() 与一个 clear() 并发执行时，
    KV 操作序列中每个"读"都紧跟着自己的"写/删"，不会与其他协程的操作交错。
    这只有在 add()/clear() 共享同一把锁、并发时严格串行化的前提下才成立。
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_dnd_dice.history import ROLL_ERROR_PREFIXES, RollHistoryManager


class FakeEvent:
    """最小化的假 AstrMessageEvent，仅提供 RollHistoryManager 依赖的三个成员。"""

    def __init__(self, origin: str, sender_id: str, sender_name: str) -> None:
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name


class FakeStar:
    """内存字典实现的假 Star，模拟 get_kv_data/put_kv_data/delete_kv_data（均为 async）。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._data[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._data.pop(key, None)


class InstrumentedFakeStar:
    """记录 KV 操作顺序的假 Star，用于并发交错回归测试。

    每次调用先记录操作类型，再 `await asyncio.sleep(0)` 主动让出控制权，
    制造并发交错的窗口——如果调用方（RollHistoryManager）没有用同一把锁
    串行化整个"读-改-写"临界区，事件循环就有机会在此处切换到另一个协程，
    使操作序列出现交错。
    """

    def __init__(self) -> None:
        self._data: dict[str, object] = {}
        self.ops: list[tuple[str, str]] = []

    async def get_kv_data(self, key: str, default: object = None) -> object:
        self.ops.append(("get", key))
        await asyncio.sleep(0)
        return self._data.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self.ops.append(("put", key))
        await asyncio.sleep(0)
        self._data[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self.ops.append(("delete", key))
        await asyncio.sleep(0)
        self._data.pop(key, None)


# ---------------------------------------------------------------------------
# 基础读写
# ---------------------------------------------------------------------------


class TestAddAndGetAll:
    def test_add_then_get_all_roundtrip(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
            event = FakeEvent("group:1", "u1", "Alice")

            await mgr.add(event, "1d20+5", "掷骰结果: 18")
            entries = await mgr.get_all(event)

            assert len(entries) == 1
            assert entries[0].expr == "1d20+5"
            assert entries[0].result == "掷骰结果: 18"
            assert entries[0].sender_id == "u1"
            assert entries[0].sender_name == "Alice"
            assert entries[0].ts  # 时间戳已填充

        asyncio.run(_run())

    def test_add_sanitizes_control_characters(self) -> None:
        # expr / sender_name 中的换行等控制字符不应原样写入存储，
        # 防止伪造多行历史记录（_sanitize 清洗）。
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
            event = FakeEvent("group:1", "u1", "Al\nice\t评论")

            await mgr.add(event, "1d20+5\n注入行", "结果: 10")
            entries = await mgr.get_all(event)

            assert len(entries) == 1
            entry = entries[0]
            assert "\n" not in entry.expr
            assert "\n" not in entry.sender_name
            assert "\t" not in entry.sender_name

        asyncio.run(_run())

    def test_max_count_truncates_keep_latest(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=3, enabled=True)
            event = FakeEvent("group:1", "u1", "Alice")

            for i in range(5):
                await mgr.add(event, f"expr{i}", f"result{i}")

            entries = await mgr.get_all(event)
            assert [e.expr for e in entries] == ["expr2", "expr3", "expr4"]

        asyncio.run(_run())

    def test_failed_result_not_recorded(self) -> None:
        async def _run() -> None:
            for prefix in ROLL_ERROR_PREFIXES:
                star = FakeStar()
                mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
                event = FakeEvent("group:1", "u1", "Alice")

                await mgr.add(event, "bad", f"{prefix}详情")
                entries = await mgr.get_all(event)
                assert entries == []

        asyncio.run(_run())

    def test_add_noop_when_disabled(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=False)
            event = FakeEvent("group:1", "u1", "Alice")

            await mgr.add(event, "1d20", "结果: 10")

            assert star._data == {}
            entries = await mgr.get_all(event)
            assert entries == []

        asyncio.run(_run())


class TestGetBySender:
    def test_get_by_sender_filters(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
            event_a = FakeEvent("group:1", "u1", "Alice")
            event_b = FakeEvent("group:1", "u2", "Bob")

            await mgr.add(event_a, "1d20", "r1")
            await mgr.add(event_b, "1d6", "r2")
            await mgr.add(event_a, "1d8", "r3")

            alice_entries = await mgr.get_by_sender(event_a, "u1")
            assert [e.expr for e in alice_entries] == ["1d20", "1d8"]

            bob_entries = await mgr.get_by_sender(event_b, "u2")
            assert [e.expr for e in bob_entries] == ["1d6"]

        asyncio.run(_run())


class TestClear:
    def test_clear_returns_count_and_empties(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
            event = FakeEvent("group:1", "u1", "Alice")

            for i in range(4):
                await mgr.add(event, f"expr{i}", f"result{i}")

            count = await mgr.clear(event)
            assert count == 4

            entries = await mgr.get_all(event)
            assert entries == []

        asyncio.run(_run())

    def test_clear_on_empty_history_returns_zero(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = RollHistoryManager(star=star, max_count=50, enabled=True)
            event = FakeEvent("group:1", "u1", "Alice")

            count = await mgr.clear(event)
            assert count == 0

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 并发回归（F1/F2）
# ---------------------------------------------------------------------------


class TestConcurrencyRegression:
    def test_concurrent_add_and_clear_do_not_interleave(self) -> None:
        """add() 的"读→写"与 clear() 的"读→删"在并发下必须各自保持原子、不交错。

        单把管理器级锁下，任意时刻只有一个协程能进入临界区，因此完整的 KV
        操作序列必然是若干个连续的 (get, put) 或 (get, delete) 对首尾相接，
        不会出现 get 之后被别的协程的操作插队的情况。
        """

        async def _run() -> list[tuple[str, str]]:
            star = InstrumentedFakeStar()
            mgr = RollHistoryManager(star=star, max_count=100, enabled=True)
            event = FakeEvent("group:1", "u1", "Alice")

            tasks = [mgr.add(event, f"expr{i}", f"result{i}") for i in range(5)]
            tasks.append(mgr.clear(event))
            await asyncio.gather(*tasks)
            return star.ops

        ops = asyncio.run(_run())

        # 5 次 add（各 1 get + 1 put）+ 1 次 clear（1 get + 1 delete）= 12 次操作。
        assert len(ops) == 12
        for i in range(0, len(ops), 2):
            first_kind, first_key = ops[i]
            second_kind, second_key = ops[i + 1]
            assert first_kind == "get", f"第 {i} 个操作应为 get，实际为 {ops[i]}"
            assert second_kind in ("put", "delete"), (
                f"第 {i + 1} 个操作应紧跟在对应 get 之后，实际为 {ops[i + 1]}"
            )
            assert first_key == second_key == "history:group:1"
