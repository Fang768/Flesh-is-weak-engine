"""自然语言意图识别（**纯规则，不调 LLM**）。

IDLE 状态下机器人不加载任何跑团进程，所以意图识别必须在本地完成：
关键词 + 优先级，够用且零成本。认不出来就让玩家按提示重说一句。
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    START = "start"  # 想开团（开新的 or 继续，由闸门再问）
    RESUME = "resume"  # 明确要从断点继续
    PAUSE = "pause"
    END = "end"
    STATUS = "status"
    HELP = "help"
    EXIT = "exit"  # 用户明确让机器人别听了
    UNKNOWN = "unknown"


# 优先级从上到下：一句话里同时出现多个词时，靠前的赢。
# 例如「继续开团」→ RESUME；「不跑了，结束」→ END。
_RULES: list[tuple[Intent, tuple[str, ...]]] = [
    (
        Intent.EXIT,
        ("别听了", "退下", "别理我", "不用你了", "结束对话", "收声", "闭嘴"),
    ),
    (
        Intent.RESUME,
        ("继续", "接着", "断点", "上次", "恢复", "续上", "接着上次", "接着跑"),
    ),
    (
        Intent.END,
        ("结束", "收工", "归档", "不跑", "关团", "散会", "结束跑团"),
    ),
    (
        Intent.PAUSE,
        ("暂停", "存档", "存个档", "先停", "歇一会", "休息一下", "停一下"),
    ),
    (
        Intent.STATUS,
        ("状态", "进度", "跑到哪", "什么情况", "在跑吗", "开没开"),
    ),
    (
        Intent.HELP,
        ("帮助", "指令", "命令", "怎么用", "说明书", "能做什么"),
    ),
    (
        Intent.START,
        (
            "开团",
            "开个团",
            "开始跑",
            "跑团",
            "开跑",
            "新剧本",
            "新开",
            "开新",
            "开局",
            "来一局",
            "开始",
            "带团",
        ),
    ),
]

_STRIP = re.compile(r"[\s，。！？、,.!?~～@]+")


def normalize(text: str) -> str:
    """去掉空白与标点、转小写，减小匹配误差。"""
    return _STRIP.sub("", (text or "").lower())


def classify(text: str) -> Intent:
    """一句话 → 意图。"""
    s = normalize(text)
    if not s:
        return Intent.UNKNOWN
    for intent, words in _RULES:
        if any(w in s for w in words):
            return intent
    return Intent.UNKNOWN


# ---------- 确认环节 ----------

_YES_RESUME = ("继续", "接着", "上次", "断点", "恢复", "续上", "1", "一", "第一个")
_YES_NEW = ("新", "重新", "开新", "新剧本", "2", "二", "第二个")
_YES_PLAIN = ("是", "对", "好", "行", "可以", "确认", "嗯", "开", "来", "ok", "yes")
_NO = ("不", "算了", "取消", "别", "免了", "先不", "不用", "no")
# 犹豫不决：既不算同意也不算拒绝，让机器人再问一次
_HEDGE = ("想想", "再说", "考虑", "等一下", "等等", "让我", "稍等")


def classify_confirm(text: str, *, has_paused: bool) -> str | None:
    """解析确认回复。

    返回 ``"resume"`` / ``"new"`` / ``"cancel"`` / ``None``（没听懂，需要再问）。
    """
    s = normalize(text)
    if not s:
        return None
    if any(w in s for w in _NO):
        return "cancel"
    if any(w in s for w in _HEDGE):
        return None
    if any(w in s for w in _YES_NEW):
        return "new"
    if has_paused and any(w in s for w in _YES_RESUME):
        return "resume"
    if any(w in s for w in _YES_PLAIN):
        # 只有一条可选路时才把「好」当成它
        return "resume" if has_paused else "new"
    return None
