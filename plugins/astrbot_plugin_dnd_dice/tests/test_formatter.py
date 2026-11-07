"""formatter 集成测试：F8 展示要求及关键展示路径回归。"""

from __future__ import annotations

from astrbot_plugin_dnd_dice.dice_parser import parse
from astrbot_plugin_dnd_dice.dice_roller import roll
from astrbot_plugin_dnd_dice.formatter import format_result

# ---------------------------------------------------------------------------
# F8：爆炸追加骰被重骰时原始值须以 ~N~ 展示
# ---------------------------------------------------------------------------


class TestExplodedRerollDisplayF8:
    def test_rerolled_original_of_exploded_die_shown(self, make_rng) -> None:
        # 基础骰 6 → 爆炸掷出 1 → r1 重骰成 6：原始 1 的 ~1~ 标注不可丢失。
        make_rng([6, 1, 6])
        out = format_result(roll(parse("1d6!r1")))
        assert "~1~" in out
        assert "[6, ~1~, 6!]" in out


# ---------------------------------------------------------------------------
# 展示回归
# ---------------------------------------------------------------------------


class TestDisplayRegression:
    def test_dropped_dice_in_parentheses(self, make_rng) -> None:
        make_rng([1, 2, 3, 4])
        out = format_result(roll(parse("4d6kh3")))
        assert "(1)" in out
        assert "= 9" in out

    def test_success_failure_marks(self, make_rng) -> None:
        make_rng([1, 4, 5])
        out = format_result(roll(parse("3d6>3f1")))
        assert "1x" in out
        assert "4*" in out
        assert "5*" in out
        assert "2成功 1失败" in out

    def test_fate_faces(self, make_rng) -> None:
        make_rng([-1, 0, 1, 1])
        out = format_result(roll(parse("4dF")))
        assert "[-, 0, +, +]" in out

    def test_natural_20_annotation_with_advantage(self, make_rng) -> None:
        make_rng([20, 5])
        out = format_result(roll(parse("d20adv")))
        assert "大成功" in out

    def test_dc_judgement(self, make_rng) -> None:
        make_rng([10])
        out = format_result(roll(parse("d20 感知 15")))
        assert "10 / 15 失败" in out

    def test_reroll_depth_exhausted_question_mark(self, make_rng) -> None:
        make_rng([], fallback=1)
        out = format_result(roll(parse("1d6r<2")))
        assert "1?" in out

    def test_show_detail_false(self, make_rng) -> None:
        make_rng([4])
        out = format_result(roll(parse("1d6+2")), show_detail=False)
        assert out == "d6+2 = 6"
