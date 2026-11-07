"""dice_parser 单元测试：修复项 F5/F6/F7/F9 及解析回归用例。"""

from __future__ import annotations

import pytest
from astrbot_plugin_dnd_dice.dice_parser import DiceParseError, parse

# ---------------------------------------------------------------------------
# F5：修饰符顺序不再硬编码
# ---------------------------------------------------------------------------


class TestModifierOrderF5:
    def test_reroll_before_keep(self) -> None:
        g = parse("4d6r<2kh3").groups[0]
        assert (g.count, g.sides) == (4, 6)
        assert (g.keep_mode, g.keep_n) == ("kh", 3)
        assert len(g.reroll_conditions) == 1
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, False)

    def test_sort_before_exploding(self) -> None:
        g = parse("8d6s!").groups[0]
        assert g.sort_order == "asc"
        assert g.exploding
        assert g.explode_mode == "standard"

    def test_canonical_order_still_works(self) -> None:
        g = parse("4d6kh3r<2").groups[0]
        assert (g.keep_mode, g.keep_n) == ("kh", 3)
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, False)

    def test_unknown_modifier_still_rejected(self) -> None:
        with pytest.raises(DiceParseError):
            parse("4d6xyz3")


# ---------------------------------------------------------------------------
# F6：比较符后无数字须整体回退
# ---------------------------------------------------------------------------


class TestComparePointBacktrackF6:
    def test_bare_equals_after_explode_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("d6!=")

    def test_explode_with_value_still_works(self) -> None:
        g = parse("d6!=3").groups[0]
        assert g.exploding
        assert (g.explode_compare, g.explode_value) == ("=", 3)


# ---------------------------------------------------------------------------
# F7：adv/dis 显式骰数 >2 时报错
# ---------------------------------------------------------------------------


class TestAdvDisF7:
    def test_adv_with_more_than_two_dice_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("5d20adv")

    def test_dis_with_more_than_two_dice_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("3d20dis")

    @pytest.mark.parametrize("raw", ["d20adv", "1d20adv", "2d20adv"])
    def test_adv_valid_counts(self, raw: str) -> None:
        g = parse(raw).groups[0]
        assert (g.count, g.keep_mode, g.keep_n) == (2, "kh", 1)

    def test_dis_valid(self) -> None:
        g = parse("d20dis").groups[0]
        assert (g.count, g.keep_mode, g.keep_n) == (2, "kl", 1)


# ---------------------------------------------------------------------------
# F9：DC 仅在空格分隔（或纯数字标签）时提取——与实现对齐的行为固定用例
# ---------------------------------------------------------------------------


class TestDcExtractionF9:
    def test_dc_with_space(self) -> None:
        expr = parse("d20 感知 15")
        assert expr.label == "感知"
        assert expr.dc == 15

    def test_no_space_suffix_is_not_dc(self) -> None:
        expr = parse("d20 房间2")
        assert expr.label == "房间2"
        assert expr.dc is None

    def test_pure_number_label_is_dc(self) -> None:
        expr = parse("d20 15")
        assert expr.label == ""
        assert expr.dc == 15


# ---------------------------------------------------------------------------
# 解析回归用例
# ---------------------------------------------------------------------------


class TestParserRegression:
    def test_keep_low(self) -> None:
        g = parse("2d20kl1").groups[0]
        assert (g.count, g.sides, g.keep_mode, g.keep_n) == (2, 20, "kl", 1)

    def test_drop_shorthand(self) -> None:
        g = parse("8d6d3").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dl", 3)

    def test_drop_low_explicit(self) -> None:
        g = parse("8d6dl3").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dl", 3)

    def test_drop_high(self) -> None:
        g = parse("8d6dh2").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dh", 2)

    def test_success_failure(self) -> None:
        g = parse("3d6>3f1").groups[0]
        assert (g.success_compare, g.success_value) == (">", 3)
        assert (g.failure_compare, g.failure_value) == ("=", 1)

    def test_fate(self) -> None:
        g = parse("4dF").groups[0]
        assert g.fate
        assert g.count == 4

    def test_reroll_once(self) -> None:
        g = parse("2d6ro<2").groups[0]
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, True)

    def test_reroll_default(self) -> None:
        g = parse("8d6r").groups[0]
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("=", 1, False)

    def test_sort_asc_and_desc(self) -> None:
        assert parse("8d6s").groups[0].sort_order == "asc"
        assert parse("8d6sd").groups[0].sort_order == "desc"

    def test_multi_group_with_flat_modifier(self) -> None:
        expr = parse("2d6+1d4+3")
        assert len(expr.groups) == 2
        assert (expr.groups[0].count, expr.groups[0].sides) == (2, 6)
        assert (expr.groups[1].count, expr.groups[1].sides) == (1, 4)
        assert expr.flat_modifier == 3

    def test_negative_group(self) -> None:
        expr = parse("1d8-1d6")
        assert expr.groups[0].modifier == 0
        assert expr.groups[1].modifier == -1

    def test_fullwidth_plus_normalized(self) -> None:
        expr = parse("d20＋5")
        assert len(expr.groups) == 1
        assert expr.groups[0].sides == 20
        assert expr.flat_modifier == 5
