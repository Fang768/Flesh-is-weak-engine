"""NPC 对话：跟屋里的人说话，不该被当成一次技能检定。

剧本里 ``npcs`` 是 ``{"mira": {"name": "米拉", "voice": "冷淡、短句、不解释"}}``，
场景用 ``npcs: ["mira"]`` 说明这场里有谁。

流程：

1. 玩家这句话里提到了在场 NPC 的名字 → 进这里；
2. 模型**只写台词**（1–3 句，保持 ``voice`` 的语气），
3. 硬约束交给提示词：不报数字、不宣布成败、不替玩家行动、不给任务奖励。
"""

from __future__ import annotations

from core import llm, story

SYSTEM = """你在扮演一个赛博朋克世界里的人物。

规则（必须遵守）：
- 只写这个人物说的话和他做的小动作，1–3 句，不超过 120 字。
- 保持给定的语气，不要变成旁白，不要替玩家角色说话或行动。
- 不要出现任何数字、骰子、难度值、伤害、奖励；不要宣布任何行动成功或失败。
- 不要编造剧本里没有的重大事实；不确定就用模糊的说法带过。
- 直接输出台词，不要加引号、不要加姓名前缀。"""


def _scene_npcs(story_id: str, scene_id: str) -> list[str]:
    """本场在场的 NPC key。

    剧本里两种写法都认：

    * ``"npcs": ["student"]``                       —— key 列表
    * ``"npcs": [{"key": "student", "name": ...}]`` —— 内联（官方模组转写用这种）

    早先只认第一种，遇到内联写法会把整个 dict 的 ``str()`` 当成 key，
    导致 ``match()`` 永远匹配不上、NPC 对话完全失效。
    """
    scene = story.scene(story_id, scene_id) or {}
    out: list[str] = []
    for item in scene.get("npcs") or []:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("id") or "").strip()
            if key:
                out.append(key)
        else:
            key = str(item or "").strip()
            if key:
                out.append(key)
    return out


def inline_npc(story_id: str, scene_id: str, key: str) -> dict | None:
    """场景内联写的 NPC 定义（有就用场景里的，没有回退到剧本顶层）。"""
    scene = story.scene(story_id, scene_id) or {}
    for item in scene.get("npcs") or []:
        if isinstance(item, dict) and str(item.get("key") or "") == key:
            return item
    return None


def match(story_id: str, scene_id: str, text: str) -> tuple[str, dict] | None:
    """这句话在跟谁说话？返回 ``(key, npc)`` 或 None。"""
    body = (text or "").strip()
    if not body:
        return None
    data = story.load(story_id)
    for key in _scene_npcs(story_id, scene_id):
        npc = inline_npc(story_id, scene_id, key) \
            or ((data.get("npcs") or {}).get(key) or {})
        name = str(npc.get("name") or "")
        if name and name in body:
            return key, npc
    return None


async def reply(
    *,
    npc: dict,
    text: str,
    card: dict | None = None,
    scene: dict | None = None,
    mem: dict | None = None,
    recalled: list[dict] | None = None,
    tone: str = "",
) -> str:
    """让 NPC 回一句。模型挂了就退回一句保底台词。

    ``mem`` 是 ``core/memory.load_memory`` 的记忆单位，``recalled`` 是这次
    该想起的人际记忆（最近 10 + 关键词补召回），``tone`` 是态度的一句话说明。
    三者都可缺省——老调用照旧能用。
    """
    name = str(npc.get("name") or "对方")
    voice = str(npc.get("voice") or "")
    who = str((card or {}).get("name") or "一个陌生人")
    where = str((scene or {}).get("name") or "")

    # 人设：voice 管语气，prompt 管动机（决策：NPC 可以带长期人设补充）
    persona = str(npc.get("prompt") or "")
    knows = [str(x) for x in (npc.get("knows") or [])]
    secret = [str(x) for x in (npc.get("secret") or [])]

    blocks = [f"人物：{name}", f"语气：{voice or '普通'}"]
    if persona:
        blocks.append(f"人设补充：{persona}")
    if tone:
        blocks.append(tone)
    if knows:
        blocks.append("他知道的事：" + "、".join(knows))
    if secret:
        blocks.append("他不愿说的事（被问到时含糊带过或露出破绽）："
                      + "、".join(secret))
    if recalled:
        blocks.append("他记得的事：" + "；".join(
            str(r.get("what")) for r in recalled if r.get("what")))
    if mem:
        recent = [e for e in (mem.get("exchanges") or [])][-3:]
        if recent:
            blocks.append("刚才的对话：" + "；".join(
                f"{e.get('who')}：{e.get('text')}" for e in recent))
    blocks.append(f"场景：{where}")
    blocks.append(f"对方（玩家）：{who}")
    blocks.append(f"对方说：{text}")
    blocks.append("只输出这个人物的台词。")

    user = "\n".join(blocks)
    try:
        line = await llm.complete_text(SYSTEM, user, max_tokens=220, temperature=0.85)
    except llm.LLMError:
        return f"{name}看了你一眼，没有接话。"
    line = (line or "").strip()
    if not line:
        return f"{name}看了你一眼，没有接话。"
    return f"🗣 {name}：{line}"


# ── 对话连续性 ───────────────────────────────────────────────
# 玩家跟某个 NPC 说上话之后，接下来几句就不必每句都点名——
# 否则「这货是谁的」会被当成一次普通行动去掷骰。
_ACTIVE: dict[tuple[str, str], tuple[str, float]] = {}
TTL = 600.0  # 10 分钟没说话就断开

#: 出现这些词就当成"动手"，立刻断开对话
ACTION_HINTS = (
    "我拿", "我掏", "我抓", "我推", "我拉", "我走", "我跑", "我去", "我冲",
    "我躲", "我爬", "我踹", "我翻", "我撞", "我开", "我举", "我拔", "我按",
    "攻击", "开枪", "打他", "打她", "动手", "踹", "砸",
    "潜行", "躲开", "翻过", "爬上去", "检查", "查看", "观察",
)


def remember(group_id: str, user_id: str, npc_key: str) -> None:
    import time

    _ACTIVE[(group_id, str(user_id))] = (npc_key, time.monotonic())


def forget(group_id: str, user_id: str) -> None:
    _ACTIVE.pop((group_id, str(user_id)), None)


def active_npc(group_id: str, user_id: str) -> str | None:
    """这个玩家上一句在跟谁说话？还没过期就返回 NPC key。"""
    import time

    hit = _ACTIVE.get((group_id, str(user_id)))
    if not hit:
        return None
    key, at = hit
    if time.monotonic() - at > TTL:
        _ACTIVE.pop((group_id, str(user_id)), None)
        return None
    return key


def looks_like_action(text: str) -> bool:
    body = (text or "").strip()
    return any(hint in body for hint in ACTION_HINTS)


def npc_by_key(story_id: str, key: str, scene_id: str = "") -> dict | None:
    """按 key 取 NPC 定义：场景内联的优先，其次剧本顶层。"""
    if scene_id:
        inline = inline_npc(story_id, scene_id, key)
        if inline is not None:
            return inline
    data = story.load(story_id)
    npc = (data.get("npcs") or {}).get(key)
    return npc if isinstance(npc, dict) else None
