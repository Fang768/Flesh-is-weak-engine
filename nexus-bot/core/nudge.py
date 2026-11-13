"""DM 的“扶一把”：偏离提醒 + 态度旁白。

两个职能都只**写文案**，不碰任何数值：

1. **偏离提醒**（决策 D）：玩家做的事跟剧本完全无关时，委婉把注意力拨回来。
   主人给的两个范式：
   * 内容引导：「你注意到六名学生聚集在该地区等待公共汽车……说不定这帮书呆子会知道些什么」
   * 时间压力：「时间已经不早了，如果再耽搁下去，演出都快要结束了」

   约束：**不剧透**——只指向「东西在哪」，不说「答案是什么」。

2. **态度旁白**（决策 4）：态度跨越档位时，委婉提一句。
   范式：「雷菲尔德眉毛舒展，似乎对你的话题很感兴趣」

频率控制（决策 G）：
* 空转**连续 3 次**触发；
* 本场每类**最多 1 次**（内容引导 / 时间压力各一）；
* 触发后计数清零，避免连环催。
"""
from __future__ import annotations

import sqlite3

from core import llm, memory

#: 连续空转多少次触发提醒
OFFTRACK_LIMIT = 3
#: 提醒类型（每类每场一次）
KIND_CONTENT = "content"
KIND_TIME = "time"
#: 换场提示：本场已做完该做的、玩家还没走
KIND_EXIT = "exit"

#: 兜底文案（模型挂了也要能用）
FALLBACK = {
    KIND_CONTENT: "你环顾四周，似乎还有没注意到的东西。",
    KIND_TIME: "时间已经不早了，再耽搁下去怕是要错过什么。",
    KIND_EXIT: "时候不早了，该动身了。",
}

NUDGE_SYSTEM = """你在跑团里当主持人（DM）。玩家刚才做的事跟这一节剧本无关，
你要委婉地把注意力拨回来。

硬规矩：
- 只说 1 句，不超过 50 字，像旁白不像提示。
- **不剧透**：只指向「场上有什么」，绝不说出答案、结论、凶手、真相。
- 不提数字、不提名「线索」「剧情」「任务」这类游戏术语。
- 不要命令玩家做什么，用观察或氛围暗示。
"""


# ── 偏离计数 ────────────────────────────────────────────────


def note_offtrack(conn: sqlite3.Connection, campaign_id: int, scene: str) -> int:
    """记一次空转（没撞 gate / 没中线索 / 没跟 NPC 说话），返回新计数。"""
    prog = memory.load_progress(conn, campaign_id, scene)
    prog["off_track"] = int(prog.get("off_track") or 0) + 1
    memory.save_progress(conn, campaign_id, scene, prog)
    return prog["off_track"]


def reset_offtrack(conn: sqlite3.Connection, campaign_id: int, scene: str) -> None:
    """撞上任何一样（gate/线索/NPC）就清零。"""
    prog = memory.load_progress(conn, campaign_id, scene)
    if prog.get("off_track"):
        prog["off_track"] = 0
        memory.save_progress(conn, campaign_id, scene, prog)


def should_nudge(conn: sqlite3.Connection, campaign_id: int,
                 scene: str, *, has_left: bool = True,
                 can_leave: bool = False) -> str:
    """该不该提醒、提醒哪一类。返回 ``""`` 表示不提醒。"""
    prog = memory.load_progress(conn, campaign_id, scene)
    done = set(prog.get("nudged") or [])
    # 本场**没有**待查线索、但有出口可走 → 提示换场（模组的节奏）
    if can_leave and not has_left and KIND_EXIT not in done:
        return KIND_EXIT
    if int(prog.get("off_track") or 0) < OFFTRACK_LIMIT:
        return ""
    # 还有没查的线索 → 先做内容引导；否则用时间压力
    if has_left and KIND_CONTENT not in done:
        return KIND_CONTENT
    if KIND_TIME not in done:
        return KIND_TIME
    return ""


def mark_nudged(conn: sqlite3.Connection, campaign_id: int, scene: str,
                kind: str) -> None:
    """记下已经提醒过哪一类（本场不再重复）。并清零空转计数。"""
    prog = memory.load_progress(conn, campaign_id, scene)
    done = list(prog.get("nudged") or [])
    if kind and kind not in done:
        done.append(kind)
    prog["nudged"] = done
    prog["off_track"] = 0
    memory.save_progress(conn, campaign_id, scene, prog)


# ── 文案 ────────────────────────────────────────────────────


async def compose(conn: sqlite3.Connection, campaign_id: int, scene: str, *,
                  kind: str, scene_def: dict | None = None,
                  npc_names: list[str] | None = None,
                  clue_topics: list[str] | None = None) -> str:
    """让模型写一句提醒；失败或空返回用兜底。

    **只喂存在性信息**（场上有谁、还有什么没看过），不喂答案——
    这正是「不剧透」的实现方式。
    """
    scene_def = scene_def or {}
    facts = [f"地点：{scene_def.get('name') or '这里'}"]
    if npc_names:
        facts.append("在场的人：" + "、".join(npc_names))
    if clue_topics:
        facts.append("这附近还有些值得留意的东西（但别说是什么）："
                     + "、".join(clue_topics))
    if kind == KIND_TIME:
        facts.append("玩家已经在这里耽搁了一阵，时间在流逝。")
    elif kind == KIND_EXIT:
        facts.append("这一节该做的都做完了，该去下一个地方了"
                     "（但**不要说**下一站在哪，由玩家自己提出动身）。")
    else:
        facts.append("玩家还没注意到这附近可查的东西。")
    try:
        line = await llm.complete_text(
            NUDGE_SYSTEM, "\n".join(facts), max_tokens=90, temperature=0.8)
    except llm.LLMError:
        return FALLBACK.get(kind, FALLBACK[KIND_CONTENT])
    line = (line or "").strip().strip("「」“”\"'")
    return line or FALLBACK.get(kind, FALLBACK[KIND_CONTENT])


ATTITUDE_SYSTEM = """你在写跑团小说的一个细节。玩家刚跟某人聊完，
这个人的态度跨了一个档位。写一句**很轻**的表情或动作描写。

硬规矩：
- 只写这个人的神态/小动作，1 句，不超过 30 字。
- 不说「态度变了」「好感度」这类词，不报数字。
- 不写对话，不替玩家行动。
"""


async def compose_attitude(npc_name: str, before: str, after: str,
                           *, voice: str = "") -> str:
    """态度跨档位时的委婉旁白（「眉毛舒展，似乎对你的话题很感兴趣」）。"""
    from core import attitude

    user = (
        f"人物：{npc_name}\n语气：{voice or '普通'}\n"
        f"态度从「{before}」变成了「{after}」\n只写一句神态描写。"
    )
    try:
        line = await llm.complete_text(
            ATTITUDE_SYSTEM, user, max_tokens=60, temperature=0.9)
    except llm.LLMError:
        return attitude.describe_change(npc_name, 0, 1) if after != before else ""
    line = (line or "").strip().strip("「」“”\"'")
    return line or attitude.describe_change(npc_name, 0, 1)
