"""NPC 态度：0–10 的隐藏值，只影响对话。

主人 2026-11-14 定的口径：

> 「态度作为一个隐藏的数值 0 到 10，5 为平淡，10 为友好，0 为厌恶，**只影响对话**，
> 由记录的人际记忆决定最终的好感度……但是需要锁范围，比如敌人无论怎么示好
> 都不会对你友善，顶多平淡，而有些人保持中立，怎么样也不会特别厌恶或者友善。」

所以：

* **不参与任何检定**（命中/伤害/DV 一律不看它）；
* 每次改变都夹进这个 NPC 的 ``bounds`` —— 这正是「锁范围」；
* 说出来的只有**委婉的旁白**（「眉毛舒展，似乎对这个话题很感兴趣」），
  且只在**跨越档位**时提，不每句都说。

``bounds`` 默认 ``[0, 10]``；剧本可写 ``"attitude_bounds": [0, 5]`` 覆写，
模型判错时作者能纠（决策：保留覆写能力）。
"""
from __future__ import annotations

import sqlite3

from core import memory

#: 档位边界：(下界, 名称)。值 ≤ 上界即落该档。
BANDS = (
    (2, "厌恶"),
    (4, "冷淡"),
    (5, "平淡"),
    (7, "友善"),
    (10, "亲近"),
)

#: 默认范围
DEFAULT_BOUNDS = (0, 10)


def band_of(attitude: int) -> str:
    """0–10 的值落在哪一档。"""
    value = max(0, min(10, int(attitude or 0)))
    for upper, name in BANDS:
        if value <= upper:
            return name
    return "亲近"


def clamp(attitude: int, bounds) -> int:
    """把态度夹进这个 NPC 能到的范围（「锁范围」）。"""
    lo, hi = _parse(bounds)
    return max(lo, min(hi, int(attitude or 0)))


def _parse(bounds) -> tuple[int, int]:
    """把 ``[lo, hi]`` 规整成合法区间；坏数据回退默认。"""
    lo, hi = DEFAULT_BOUNDS
    try:
        vals = list(bounds or [])
        if len(vals) == 2:
            lo, hi = int(vals[0]), int(vals[1])
    except (TypeError, ValueError):
        return DEFAULT_BOUNDS
    lo = max(0, min(10, lo))
    hi = max(0, min(10, hi))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def bounds_of(conn: sqlite3.Connection, campaign_id: int, npc_key: str) -> tuple:
    mem = memory.load_memory(conn, campaign_id, npc_key)
    return _parse(mem.get("bounds"))


def current(conn: sqlite3.Connection, campaign_id: int, npc_key: str) -> int:
    """当前态度（已夹进 bounds）。"""
    mem = memory.load_memory(conn, campaign_id, npc_key)
    return clamp(mem.get("attitude", 5), mem.get("bounds"))


def set_bounds(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
               bounds) -> tuple:
    """设定/覆写这个 NPC 的范围；态度会被立刻夹进去。"""
    lo, hi = _parse(bounds)
    mem = memory.load_memory(conn, campaign_id, npc_key)
    mem["bounds"] = [lo, hi]
    mem["attitude"] = clamp(mem.get("attitude", 5), [lo, hi])
    memory.save_memory(conn, campaign_id, npc_key, mem)
    return (lo, hi)


def apply_delta(conn: sqlite3.Connection, campaign_id: int, npc_key: str,
                delta: int) -> tuple[int, int, bool]:
    """加/减态度，夹进 bounds。

    返回 ``(旧值, 新值, 是否跨越了档位)``——只有跨越档位才值得旁白一句。
    """
    mem = memory.load_memory(conn, campaign_id, npc_key)
    old = clamp(mem.get("attitude", 5), mem.get("bounds"))
    new = clamp(old + int(delta or 0), mem.get("bounds"))
    crossed = band_of(old) != band_of(new)
    if new != old:
        mem["attitude"] = new
        memory.save_memory(conn, campaign_id, npc_key, mem)
    return old, new, crossed


def tone_hint(attitude: int, npc_name: str = "") -> str:
    """给提示词的一句话说明（模型据此调整语气）。"""
    band = band_of(attitude)
    who = npc_name or "这个人物"
    table = {
        "厌恶": f"极度反感玩家，说话带刺、逢迎无用，能不答就不答",
        "冷淡": f"对玩家没什么好感，回答敷衍、点到为止",
        "平淡": f"对玩家无感，公事公办",
        "友善": f"对玩家有好感，愿意多说两句、搭把手",
        "亲近": f"把玩家当自己人，主动帮忙、主动提醒",
    }
    return f"{who} 当前态度：{band}（{attitude}/10）。{table.get(band, '')}"


def describe_change(npc_name: str, old: int, new: int) -> str:
    """态度变化时给玩家的**委婉**旁白（主人给的例子：
    「雷菲尔德眉毛舒展，似乎对你的话题很感兴趣」）。

    只给兜底文案；更好的一句由模型写（见 ``core/nudge.compose_attitude``）。
    """
    if new > old:
        if band_of(new) in ("友善", "亲近"):
            return f"{npc_name}神色松动，似乎愿意再多说几句。"
        return f"{npc_name}的眉头松开了些。"
    if band_of(new) in ("厌恶", "冷淡"):
        return f"{npc_name}的脸色沉了下去。"
    return f"{npc_name}的表情冷了一分。"
