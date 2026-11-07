"""骰子：所有随机性都从这里出来，方便测试时注入。

V3.0 只用 D10（D6 偶尔用于伤害/重伤表），所以接口保持极简。
"""

from __future__ import annotations

import random
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Roller(Protocol):
    """骰子源。"""

    def roll(self, sides: int = 10) -> int:  # pragma: no cover - protocol
        ...


class RandomRoller:
    """真随机（可给 seed 复现）。"""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def roll(self, sides: int = 10) -> int:
        return self._rng.randint(1, sides)


class ScriptedRoller:
    """脚本化骰子：按给定序列依次返回，用完之后循环或抛错。

    测试里的标准用法::

        r = ScriptedRoller([10, 7])   # 第一次掷出 10（自然大成功），第二次 7
    """

    def __init__(self, rolls: Sequence[int], *, loop: bool = False) -> None:
        self._rolls = list(rolls)
        self._loop = loop
        self._i = 0
        self.history: list[int] = []

    def roll(self, sides: int = 10) -> int:
        if self._i >= len(self._rolls):
            if not self._loop:
                raise AssertionError(
                    f"ScriptedRoller 用完了：需要第 {self._i + 1} 次掷骰，但只提供了 {len(self._rolls)} 个值"
                )
            self._i = 0
        value = self._rolls[self._i]
        self._i += 1
        if not 1 <= value <= sides:
            raise AssertionError(f"脚本骰 {value} 超出 1..{sides}")
        self.history.append(value)
        return value
