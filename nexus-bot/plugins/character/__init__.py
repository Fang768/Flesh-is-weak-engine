"""建卡 / 改卡插件：在注意力窗口内用自然语言说，模型只负责出结构，数值由程序校验。

规则边界（写死在 `core/roster.py`）：属性 2–10、技能 1–10、模板建卡最多互换 3 次属性。
"""

from __future__ import annotations

import json
import sqlite3

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from core import easy_roster, events, growth, llm, roster
from core.runtime import ATTENTION, CONN, GATE
from cyberpunk import easy
from core.session import Mode

ACTIONS = {"create", "modify", "show", "chat"}

SYSTEM = """你是一个 Cyberpunk V3.0（Fuzion 系统）跑团机器人的建卡助手。
你**只输出 JSON**，由程序校验，不要自己编造规则数值。

可用模板（必须从这里挑）：
{templates}

规则要点：
- 十属性 INT/REF/TECH/DEX/COOL/WILL/STR/CON/MOVE/BODY，取值 2–10
- 模板建卡只允许最多 3 次属性互换（stat_swaps）
- 技能等级 1–10
- 玩家说"随便/你决定"时，你自己挑一个贴合其描述的模板

输出字段：
{{"action": "create|modify|show|chat",
  "name": "角色名", "template": "模板名", "concept": "一句话人设",
  "stat_swaps": [["REF","DEX"]],
  "skill_overrides": {{"Handgun": 6}},
  "stat_changes": {{"REF": 8}},
  "skill_changes": {{"Stealth": 4}},
  "reply": "对玩家说的话（纯文本，中文，2 句以内）"}}

- 玩家要一张新卡 → action=create
- 玩家要改现有卡 → action=modify，只填需要改的字段
- 玩家只是问卡面 → action=show
- 玩家在聊别的 → action=chat，reply 里自然回应，不要硬塞建卡
"""


def group_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.get_user_id()}"


def in_window_or_at() -> Rule:
    async def _rule(event: MessageEvent) -> bool:
        if not isinstance(event, MessageEvent):  # QQ 适配器事件不走这条旧路径
            return False
        text = event.get_plaintext().strip()
        if text.startswith("/"):
            return False
        group = group_key(event)
        if ATTENTION.accepts(group, str(event.get_user_id())):
            return True
        return isinstance(event, PrivateMessageEvent) or event.is_tome()

    return Rule(_rule)


player = on_message(rule=in_window_or_at(), priority=2, block=True)


def load_card(group: str, qq: str):
    return roster.get(CONN, group, qq)


@player.handle()
async def _(event: MessageEvent):
    group = group_key(event)
    qq = str(event.get_user_id())

    # 跑团进行中不接管，留给跑团玩法
    if GATE.state(group).mode is not Mode.IDLE:
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    card = load_card(group, qq)
    user_block = (
        f"玩家的话：{text}\n"
        f"玩家现有角色卡：{json.dumps(card, ensure_ascii=False) if card else '无'}"
    )

    try:
        plan = await llm.complete_json(
            SYSTEM.format(templates=roster.template_menu()), user_block
        )
    except llm.LLMError as exc:
        await player.finish(f"模型这边出了点问题：{exc}")

    action = str(plan.get("action", "chat")).lower()
    if action not in ACTIONS:
        action = "chat"

    reply = str(plan.get("reply") or "").strip()

    if action == "show":
        await player.finish(roster.render_card(card) if card else "你还没角色卡，说「给我一个角色」。")

    if action == "chat":
        await player.finish(reply or "我在。要建卡就说「给我一个角色」。")

    if action == "create":
        try:
            fresh = roster.build(
                template=str(plan.get("template") or ""),
                name=str(plan.get("name") or ""),
                concept=str(plan.get("concept") or ""),
                stat_swaps=plan.get("stat_swaps") or [],
                skill_overrides=plan.get("skill_overrides") or {},
                perks=plan.get("perks") or [],
            )
        except roster.RuleError as exc:
            await player.finish(f"这张卡不合规（{exc}）。换个说法我再试一次。")
        roster.save(CONN, group, qq, fresh)
        head = f"{reply}\n\n" if reply else ""
        await player.finish(
            head
            + roster.render_card(fresh)
            + "\n想改就说，例如「把手枪提到 8」「名字换成老猫」。"
        )

    # action == "modify"
    if card is None:
        await player.finish("你还没有角色卡，先说「给我一个角色」。")
    try:
        updated = roster.apply_patch(card, plan)
    except roster.RuleError as exc:
        await player.finish(f"这样改不行（{exc}）。")
    roster.save(CONN, group, qq, updated)
    await player.finish(roster.render_card(updated))

# ── 简易版预置角色（方案 A 的认领入口）──────────────────────────
_tpl = on_command("模板", aliases={"templates", "角色模板"}, priority=3, block=True)
_claim = on_command("认领", aliases={"claim"}, priority=3, block=True)


@_tpl.handle()
async def _(event: MessageEvent):
    await _tpl.finish(easy.format_list() + chr(10) + chr(10) + "用 /认领 名字 领走一张，比如 /认领 火炬")


@_claim.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    want = str(arg).strip()
    if not want:
        await _claim.finish("用法：/认领 火炬（先 /模板 看有哪些）")
    tpl = easy.get(want)
    if tpl is None:
        await _claim.finish(f"没有「{want}」这张卡。可用：{'、'.join(easy.names())}")
    card = easy_roster.save(
        CONN, group_key(event), event.get_user_id(), tpl, name=None
    )
    await _claim.finish(easy.format_card(card) + chr(10) + chr(10) + "想改名或调整就说「把名字改成 XX」。")


# ── 档案 / 记录 / 奖励 / 提升（存档与成长）──────────────────────
_record = on_command("记录", aliases={"事件", "log"}, priority=3, block=True)
_sheet = on_command("档案", aliases={"卡", "sheet"}, priority=3, block=True)
_award = on_command("奖励", aliases={"ip", "经验"}, priority=3, block=True)
_raise = on_command("提升", aliases={"升级", "raise"}, priority=3, block=True)


@_record.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    n = 5
    raw = str(arg).strip()
    if raw.isdigit():
        n = max(1, min(20, int(raw)))
    await _record.finish(events.render(events.recent(CONN, group_key(event), n)))


@_sheet.handle()
async def _(event: MessageEvent):
    card = roster.get(CONN, group_key(event), event.get_user_id())
    if card is None:
        easy_card = easy_roster.get(CONN, group_key(event), event.get_user_id())
        if easy_card is None:
            await _sheet.finish("你还没有卡。发「给我一个角色」，或 /认领 火炬。")
            return
        await _sheet.finish(easy.format_card(easy_card))
        return
    hp_max = roster.derived_of(card)["HP"]
    await _sheet.finish(roster.render_card(card) + chr(10) + growth.render_state(card, hp_max))


@_award.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    if not GATE.is_operator(event.get_user_id()):
        await _award.finish("只有 DM 能发 IP。")
        return
    raw = str(arg).strip()
    points = int(raw) if raw.lstrip("+-").isdigit() else growth.IP_PER_SESSION
    given = 0
    for card in roster.members(CONN, group_key(event)):
        growth.award(card, points)
        roster.save(CONN, group_key(event), card.get("owner_qq", ""), card) if card.get("owner_qq") else None
        given += 1
    if not given:
        await _award.finish("这一组还没有 V3 角色卡，没法发 IP。")
        return
    await _award.finish(f"⭐ 给 {given} 张卡各 +{points} IP。")


@_raise.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    raw = str(arg).strip()
    if not raw:
        await _raise.finish("用法：/提升 手枪（用 IP 换 1 级）")
        return
    card = roster.get(CONN, group_key(event), event.get_user_id())
    if card is None:
        await _raise.finish("V3 角色卡才有 IP 成长；简易版卡数值固定。")
        return
    try:
        level, spent = growth.raise_skill(card, raw)
    except growth.GrowthError as exc:
        await _raise.finish(f"❌ {exc}")
        return
    roster.save(CONN, group_key(event), event.get_user_id(), card)
    await _raise.finish(f"✅ {raw} 升到 {level}（花 {spent} IP），余 {growth.state(card)['ip']} 点。")
