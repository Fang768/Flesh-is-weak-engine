"""注意力窗口：@机器人 打开一段对话，窗口内**参与者**说话不用再 @。

规则（按主人要求）：

* ``@机器人`` 打开窗口，把这个人加进参与者名单
* 窗口内，**任何参与者**发言都会被处理，不需要再 @
* 另一个人 @ 机器人 → **追加**为参与者（不是顶替），两人一起对话
* 窗口 10 分钟无交互自动关闭
* 开团成功窗口关闭，进入「全员可发言」的跑团模式
* LLM 只在窗口打开期间可能被调用；没开窗 = 零 LLM
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

DEFAULT_TTL = 600.0  # 10 分钟


@dataclass
class Window:
    group_id: str
    participants: set[str] = field(default_factory=set)
    initiator: str = ""  # 第一个打开窗口的人（确认流程只认他）
    stage: str = "chat"  # chat | confirm | title | character
    opened_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    ttl: float = DEFAULT_TTL
    notes: dict[str, str] = field(default_factory=dict)

    def alive(self, *, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_active <= self.ttl

    def touch(self, *, now: float | None = None) -> None:
        self.last_active = now or time.time()

    def joined(self, qq: str) -> bool:
        return str(qq) in self.participants

    def roster(self) -> str:
        return "、".join(sorted(self.participants))


class AttentionBook:
    """一个群一个窗口；新的人 @ 机器人会被**追加**进参与者。"""

    def __init__(self) -> None:
        self._windows: dict[str, Window] = {}

    def open(self, group_id: str, owner: str, *, stage: str = "chat") -> Window:
        window = self.get(group_id)
        if window is None:
            window = Window(group_id=group_id, stage=stage)
            self._windows[group_id] = window
        window.participants.add(str(owner))
        if not window.initiator:
            window.initiator = str(owner)
        if stage:
            window.stage = stage
        window.touch()
        return window

    def get(self, group_id: str) -> Window | None:
        window = self._windows.get(group_id)
        if window is None:
            return None
        if not window.alive():
            self._windows.pop(group_id, None)
            return None
        return window

    def accepts(self, group_id: str, user_id: str) -> bool:
        """这条消息该不该被机器人接：窗口里且发送者是参与者。"""
        window = self.get(group_id)
        return window is not None and window.joined(user_id)

    def leave(self, group_id: str, user_id: str) -> None:
        window = self.get(group_id)
        if window is None:
            return
        window.participants.discard(str(user_id))
        if not window.participants:
            self.close(group_id)

    def close(self, group_id: str) -> None:
        self._windows.pop(group_id, None)

    def set_stage(self, group_id: str, stage: str) -> None:
        window = self.get(group_id)
        if window:
            window.stage = stage
            window.touch()
