"""main.py LLM 函数工具辅助函数单元测试：_compose_tool_expr。

覆盖点：
  - 常规：expression + label 合成 '#' 分隔的完整表达式。
  - 空标签：无标签时原样返回 expression。
  - 空表达式：LLM 省略 expression 时退化为 f"d{default_sides}"。
  - dc 合成：dc 不为 None 时并入 label 尾部（"标签 DC" 或纯数字 "DC"）。
  - dc 异常值（NaN/inf）：忽略 dc 且不抛异常，label 保持原样。
  - 组合结果可被 dice_parser.parse() 正确解析并提取 label/dc 字段。
"""

from __future__ import annotations

import math

import pytest
from astrbot_plugin_dnd_dice.dice_parser import parse
from astrbot_plugin_dnd_dice.main import _compose_tool_expr


class TestComposeToolExprBasic:
    def test_normal_expression_and_label(self) -> None:
        assert _compose_tool_expr("1d20+5", "攻击", None, 20) == "1d20+5#攻击"

    def test_empty_label_returns_expression_only(self) -> None:
        assert _compose_tool_expr("d20", "", None, 20) == "d20"


class TestComposeToolExprEmptyExpression:
    def test_empty_expression_falls_back_to_default_sides(self) -> None:
        assert _compose_tool_expr("", "", None, 6) == "d6"

    def test_whitespace_only_expression_with_dc_falls_back(self) -> None:
        assert _compose_tool_expr("  ", "感知", 15, 20) == "d20#感知 15"


class TestComposeToolExprDcComposition:
    def test_dc_appended_after_label(self) -> None:
        assert _compose_tool_expr("d20", "感知", 15.0, 20) == "d20#感知 15"

    def test_dc_alone_without_label(self) -> None:
        assert _compose_tool_expr("d20", "", 15.0, 20) == "d20#15"


class TestComposeToolExprDcInvalidValues:
    def test_nan_dc_ignored_without_raising(self) -> None:
        assert _compose_tool_expr("d20", "感知", float("nan"), 20) == "d20#感知"

    def test_inf_dc_ignored_without_raising(self) -> None:
        assert _compose_tool_expr("d20", "感知", float("inf"), 20) == "d20#感知"

    def test_nan_dc_without_label_keeps_expression_only(self) -> None:
        assert _compose_tool_expr("d20", "", float("nan"), 20) == "d20"

    def test_math_nan_via_isnan_sanity(self) -> None:
        # 确认测试用例中的 NaN 确实是 NaN（防止用例本身书写错误）。
        assert math.isnan(float("nan"))


class TestComposeToolExprParseRoundTrip:
    def test_composed_expr_with_label_and_dc_parses_correctly(self) -> None:
        full_expr = _compose_tool_expr("d20", "感知", 15.0, 20)
        parsed = parse(full_expr)
        assert parsed.label == "感知"
        assert parsed.dc == 15

    def test_composed_expr_with_dc_only_parses_as_dc_no_label(self) -> None:
        full_expr = _compose_tool_expr("d20", "", 15.0, 20)
        parsed = parse(full_expr)
        assert parsed.label == ""
        assert parsed.dc == 15
