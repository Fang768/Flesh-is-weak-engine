"""QQ 官方机器人接入层：把 QQ 群事件喂给平台无关的 core。

两条路：

1. **@ 或窗口内** → 走 `core.gate_flow`（纯规则、零模型调用）推进闸门；
2. **其它群消息** → 只记一行 ``chat`` 事件（留存 7 天 / 每群 200 条），
   **不进模型上下文**，也不回复。

以后接回合（`core.turn`）时，入口也是这里：`gate_flow` 返回 ``pass_through``
就把话交给玩法层。
"""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.qq import Bot, GroupMessageCreateEvent
from nonebot.log import logger
from nonebot.rule import Rule

from nonebot import on_command
from nonebot.params import CommandArg

from core import card_flow, dedupe, events, gate_flow, turn_flow
from core.locks import group_lock
from core.runtime import ATTENTION, CONN, GATE
from core.session import Mode

PREFIX = "qq:"
PRUNE_EVERY = 40
_summary_icon = {"owner": "👑", "admin": "🛡", "member": "💬"}


def group_key(event: GroupMessageCreateEvent) -> str:
    """和 OneBot 的数字群号分开，避免主键撞车。"""
    return f"{PREFIX}{event.group_openid}"


def addressed(event: GroupMessageCreateEvent) -> bool:
    if event.to_me:
        return True
    return ATTENTION.accepts(group_key(event), event.author.member_openid)


def operator(event: GroupMessageCreateEvent) -> bool:
    if getattr(event.author, "member_role", "") in ("owner", "admin"):
        return True
    return GATE.is_operator(event.author.member_openid)


async def _send(bot: Bot, event: GroupMessageCreateEvent, text: str) -> None:
    """被动回复：带上 msg_id，官方允许 5 分钟内最多回 5 条。"""
    try:
        await bot.send_to_group(
            group_openid=event.group_openid, message=text[:4000], msg_id=event.id
        )
    except Exception as exc:  # noqa: BLE001 - 不因为发不出去把整条链路拖死
        logger.warning("[gateway] send failed: {}: {}", type(exc).__name__, exc)


def _first_time(event: GroupMessageCreateEvent) -> bool:
    """同一条事件只处理一次（官方网关会重推）。"""
    return not dedupe.seen(CONN, f"qq:{event.id}")


async def _is_qq_group(event) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


def _is_addressed_qq() -> Rule:
    async def _rule(event: GroupMessageCreateEvent) -> bool:
        return addressed(event)

    return Rule(_rule)


gate = on_message(rule=_is_addressed_qq(), priority=1, block=False)


@gate.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    text = event.get_plaintext().strip()
    if text.startswith("/"):
        return  # 斜杠命令交给其它插件（/check、/r）
    if event.author.bot:
        return
    if not _first_time(event):
        return

    gid = group_key(event)
    uid = event.author.member_openid

    # 一个群一把锁：两条消息几乎同时到达时，回合/场景/事件只能结算一次
    async with group_lock(gid):
        step = gate_flow.handle(
            group_id=gid,
            user_id=uid,
            text=text,
            is_addressed=True,
            is_operator=operator(event),
        )
        if step.reply:
            await _send(bot, event, step.reply)
            return

        if not step.pass_through:
            return

        # 跑团进行中：系统操作（建卡/认领/档案/检定…）优先，其余才当成行动
        if GATE.state(gid).mode is Mode.ACTIVE:
            if card_flow.looks_meta(text, gid, uid):
                reply = await card_flow.handle(gid, uid, text, force=True)
            else:
                reply = await turn_flow.handle(gid, uid, text)
        elif card_flow.looks_meta(text, gid, uid):
            # 空闲态只接系统类请求（建卡/认领/档案…），
            # 闲聊不进模型——窗口还开着，别把每句话都喂给 LLM。
            reply = await card_flow.handle(gid, uid, text)
        else:
            reply = None
        if reply:
            await _send(bot, event, reply)


_counter = {"n": 0}

listen = on_message(priority=90, block=False)


@listen.handle()
async def _(event: GroupMessageCreateEvent):
    """没 @ 且不在窗口内的消息：只记账，不调模型、不回复。"""
    if event.author.bot or addressed(event):
        return
    plain = event.get_plaintext().strip()
    if not plain:
        return

    role = getattr(event.author, "member_role", "member")
    state = GATE.state(group_key(event))
    summary = f"{_summary_icon.get(role, '💬')} {plain[:120]}"
    events.record(
        CONN,
        group_id=group_key(event),
        kind=events.CHAT_KIND,
        campaign_id=state.campaign.id if state.campaign else None,
        summary=summary,
        actor=event.author.member_openid,
        data={"role": role, "to_me": bool(event.to_me)},
    )

    _counter["n"] += 1
    if _counter["n"] % PRUNE_EVERY == 0:
        try:
            removed = events.prune(CONN)
            if removed:
                logger.info("[gateway] pruned {} chat events", removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[gateway] prune failed: {}: {}", type(exc).__name__, exc)


# ── 命令：模板 / 认领 / 档案 / 记录 / 检定 / 骰 ────────────────────

_tpl = on_command("模板", aliases={"templates"}, priority=2, block=True)
_claim = on_command("认领", aliases={"claim"}, priority=2, block=True)
_sheet = on_command("档案", aliases={"卡", "sheet"}, priority=2, block=True)
_log = on_command("记录", aliases={"log"}, priority=2, block=True)
_check = on_command("检定", aliases={"check"}, priority=2, block=True)
_roll = on_command("r", aliases={"骰"}, priority=2, block=True)


@_tpl.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    await _send(bot, event, card_flow.templates_text())


@_claim.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    await _send(bot, event, card_flow.claim(group_key(event), event.author.member_openid, str(arg)))


@_sheet.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    await _send(bot, event, card_flow.sheet(group_key(event), event.author.member_openid))


@_log.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    raw = str(arg).strip()
    n = int(raw) if raw.isdigit() else 5
    await _send(bot, event, card_flow.record(group_key(event), n))


@_check.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    skill, dv, mods = card_flow.parse_check_arg(str(arg))
    await _send(
        bot,
        event,
        card_flow.check(group_key(event), event.author.member_openid, skill, dv=dv, mods=mods),
    )


_clue = on_command("线索", aliases={"clues"}, priority=2, block=True)


@_clue.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import clues

    await _send(bot, event, clues.text_of(CONN, group_key(event)))


@_roll.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from cyberpunk import commands, dice

    try:
        n, s, flat = commands.parse_notation(str(arg))
    except Exception as exc:  # noqa: BLE001
        await _send(bot, event, f"看不懂这个骰子（{exc}）。例如：/r 2d6+1")
        return
    await _send(bot, event, commands.roll_verbose(dice.RandomRoller(), n, s, flat))


# ── 命令：/op 管理 DM ─────────────────────────────────────────

_op = on_command("op", aliases={"dm"}, priority=2, block=True)


@_op.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    gid = group_key(event)
    who = event.author.member_openid
    raw = str(arg).strip().lower()
    is_dm = operator(event)
    if raw in ("del", "remove", "-", "取消", "删除"):
        if not is_dm:
            await _send(bot, event, "只有 DM 能取消自己的 DM 身份。")
            return
        GATE.remove_operator(who)
        await _send(bot, event, "已取消你的 DM 身份。")
        return
    if raw in ("list", "列表", ""):
        rows = GATE.operators()
        if not rows:
            await _send(bot, event, "还没有登记 DM。群主/管理员发「/op」就能把自己加成 DM。")
            return
        lines = [f"· {q[-6:]} {note}" if note else f"· {q[-6:]}" for q, note in rows]
        head = "DM 名单："
        await _send(bot, event, head + chr(10) + chr(10).join(lines))
        return
    if not is_dm:
        await _send(bot, event, "只有群主、管理员或已有 DM 才能加 DM。")
        return
    GATE.add_operator(who, note=str(arg)[:20])
    await _send(bot, event, "✅ 已经把你加成 DM，之后开团/暂停/发奖励都能用。")

# ── 命令：开打 / 攻击 / 战斗 / 收队 ───────────────────────────

_fight = on_command("开打", aliases={"fight"}, priority=2, block=True)
_hit = on_command("攻击", aliases={"attack"}, priority=2, block=True)
_battle = on_command("战斗", aliases={"battle"}, priority=2, block=True)
_map_cmd = on_command("地图", aliases={"map"}, priority=2, block=True)
_stop = on_command("收队", aliases={"stop"}, priority=2, block=True)


@_fight.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import turn_flow

    gid = group_key(event)
    state = GATE.state(gid)
    await _send(
        bot,
        event,
        # 走 encounter_text：遭遇被整段跳过时也会按 on_win 推进场景。
        turn_flow.encounter_text(
            gid,
            str(arg),
            campaign_id=state.campaign.id if state.campaign else None,
            scene=(state.campaign.scene or "") if state.campaign else "",
        ),
    )


@_hit.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import combat, turn_flow

    gid = group_key(event)
    target, skill = combat.parse_attack_arg(str(arg))
    try:
        text = combat.attack(
            CONN, gid, event.author.member_openid, target, skill=skill
        )
    except combat.CombatError as exc:
        await _send(bot, event, str(exc))
        return
    tail = turn_flow.aftermath(gid)
    await _send(bot, event, f"{text}\n\n{tail}" if tail else text)


@_battle.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import combat

    await _send(bot, event, combat.status_text(CONN, group_key(event)))


@_map_cmd.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import combat

    await _send(
        bot,
        event,
        combat.map_text(CONN, group_key(event), event.author.member_openid),
    )


@_stop.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import combat

    gid = group_key(event)
    combat.end(CONN, gid)
    await _send(bot, event, "⚑ 战斗结束了，血量按现在这个样子记在卡上。")


# ── 命令：人情 / 用人情 ───────────────────────────────────────

_favor = on_command("人情", aliases={"favors"}, priority=2, block=True)
_use_favor = on_command("用人情", aliases={"usefavor"}, priority=2, block=True)


@_favor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import turn_flow

    gid = group_key(event)
    await _send(bot, event, turn_flow.favor_status(gid, event.author.member_openid))


@_use_favor.handle()
async def _(bot: Bot, event: GroupMessageCreateEvent, arg=CommandArg()):
    if not addressed(event):
        return
    if not _first_time(event):
        return
    from core import turn_flow

    gid = group_key(event)
    await _send(
        bot,
        event,
        turn_flow.use_favor_command(gid, event.author.member_openid, str(arg).strip()),
    )
