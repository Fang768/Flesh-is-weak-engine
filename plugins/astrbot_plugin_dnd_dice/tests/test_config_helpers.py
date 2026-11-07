"""main.py 配置读取辅助函数单元测试：_safe_int / _safe_bool。

覆盖点：
  - _safe_int：正常转换、字符串数字、None/垃圾输入回退默认值、
    低于 min_val / 高于 max_val 回退默认值、恰好等于边界值不受影响。
  - _safe_bool：bool 原样返回、常见 false/true 字符串、None 回退默认值、
    兜底异常分支（F4）静默失效前先记录 logger.warning。
"""

from __future__ import annotations

import pytest
from astrbot_plugin_dnd_dice.main import _safe_bool, _safe_int


class TestSafeInt:
    def test_normal_int_passthrough(self) -> None:
        assert _safe_int(5, default=10) == 5

    def test_string_number_converted(self) -> None:
        assert _safe_int("42", default=10) == 42

    def test_none_returns_default(self) -> None:
        assert _safe_int(None, default=10) == 10

    def test_garbage_string_returns_default(self) -> None:
        assert _safe_int("not-a-number", default=10) == 10

    def test_below_min_val_returns_default(self) -> None:
        assert _safe_int(0, default=10, min_val=1) == 10

    def test_above_max_val_returns_default(self) -> None:
        assert _safe_int(2000, default=10, max_val=1000) == 10

    def test_boundary_equal_to_min_val_not_affected(self) -> None:
        assert _safe_int(1, default=999, min_val=1, max_val=1000) == 1

    def test_boundary_equal_to_max_val_not_affected(self) -> None:
        assert _safe_int(1000, default=999, min_val=1, max_val=1000) == 1000


class TestSafeBool:
    def test_bool_passthrough_true(self) -> None:
        assert _safe_bool(True, default=False) is True

    def test_bool_passthrough_false(self) -> None:
        assert _safe_bool(False, default=True) is False

    def test_false_like_strings(self) -> None:
        for value in ("false", "0", "off", ""):
            assert _safe_bool(value, default=True) is False, value

    def test_true_like_strings(self) -> None:
        for value in ("true", "1"):
            assert _safe_bool(value, default=False) is True, value

    def test_none_returns_default(self) -> None:
        assert _safe_bool(None, default=True) is True
        assert _safe_bool(None, default=False) is False


class _ExplodingBool:
    """__bool__ 主动抛异常的对象，用于触发 _safe_bool 的兜底 except 分支。"""

    def __bool__(self) -> bool:
        raise RuntimeError("boom")


class TestSafeBoolExceptionLogging:
    def test_exception_path_returns_default_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # F4：兜底分支不应静默吞掉异常，须记录 logger.warning（含值与异常信息）。
        with caplog.at_level("WARNING", logger="astrbot"):
            result = _safe_bool(_ExplodingBool(), default=True)

        assert result is True
        assert any(
            record.levelname == "WARNING" and "dnd_dice" in record.message
            for record in caplog.records
        )
