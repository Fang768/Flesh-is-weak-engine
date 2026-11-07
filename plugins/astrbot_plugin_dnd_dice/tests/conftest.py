"""pytest 共享夹具。

职责：
  1. 把 data/plugins 目录插入 sys.path，使骰子模块能以命名空间包方式导入
     （astrbot_plugin_dnd_dice 目录无 __init__.py，也不依赖 astrbot 包）。
  2. 提供可注入的确定性 RNG 夹具，替换 dice_roller._rng，
     使掷骰结果可精确断言，并统计 randint/choice 调用次数。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

# 必须在导入被测模块之前完成路径注入。
_PLUGINS_DIR = str(Path(__file__).resolve().parent.parent.parent)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

# 路径注入必须先于被测模块导入，故此处的模块级导入无法置顶。
import pytest  # noqa: E402
from astrbot_plugin_dnd_dice import dice_roller  # noqa: E402


class SeqRNG:
    """按预置序列依次返回值的确定性 RNG。

    - randint(a, b)：弹出序列下一个值；序列耗尽后返回 fallback。
    - choice(seq)：弹出序列下一个值（FATE 骰直接在序列中写 -1/0/1）。
    两类调用共享同一序列（掷骰引擎对普通骰只用 randint，对 FATE 只用 choice，
    实际不会交叉），并分别统计调用次数以便断言掷骰数量。
    """

    def __init__(self, values: Sequence[int], fallback: int = 1) -> None:
        self._values: list[int] = list(values)
        self._idx: int = 0
        self._fallback: int = fallback
        self.randint_calls: int = 0
        self.choice_calls: int = 0

    def _next(self) -> int:
        if self._idx < len(self._values):
            value = self._values[self._idx]
            self._idx += 1
            return value
        return self._fallback

    def randint(self, a: int, b: int) -> int:
        self.randint_calls += 1
        return self._next()

    def choice(self, seq: Sequence[int]) -> int:
        self.choice_calls += 1
        return self._next()


class MaxRNG:
    """永远返回最大值的 RNG，用于强制触发爆炸链与预算上限。"""

    def __init__(self) -> None:
        self.randint_calls: int = 0
        self.choice_calls: int = 0

    def randint(self, a: int, b: int) -> int:
        self.randint_calls += 1
        return b

    def choice(self, seq: Sequence[int]) -> int:
        self.choice_calls += 1
        return seq[-1]


@pytest.fixture
def make_rng(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., SeqRNG]:
    """工厂夹具：make_rng([6, 1], fallback=1) 注入确定性 RNG 并返回其实例。"""

    def _make(values: Sequence[int], fallback: int = 1) -> SeqRNG:
        rng = SeqRNG(values, fallback=fallback)
        monkeypatch.setattr(dice_roller, "_rng", rng)
        return rng

    return _make


@pytest.fixture
def max_rng(monkeypatch: pytest.MonkeyPatch) -> MaxRNG:
    """注入 always-max RNG 并返回其实例（用于爆炸预算测试）。"""
    rng = MaxRNG()
    monkeypatch.setattr(dice_roller, "_rng", rng)
    return rng
