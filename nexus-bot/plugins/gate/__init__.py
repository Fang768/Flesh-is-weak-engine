"""跑团闸门插件：@机器人打开注意力窗口，之后只跟当事人对话。

交互模型（与主人讨论确认）：

* 群里 ``@机器人 开团`` → 打开**注意力窗口**（10 分钟，绑定发起人）
* 窗口内**不需要再 @**，只有当事人的发言会被处理；别人说话机器人当没听见
* 开团两段式：先问「继续上次 / 新开」，当事人自然语言确认 → 新开的话再问剧本名
* 开团成功窗口关闭，进入跑团模式
* 没开窗时**零 LLM 调用**：这里只有规则匹配 + 读库
"""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from core.attention import Window
from core.intents import Intent, classify, classify_confirm
from core.runtime import ATTENTION, GATE
from core.session import Mode

STAGE_CHAT = "chat"
STAGE_CONFIRM = "confirm"
STAGE_TITLE = "title"

TITLE_MAX = 30

HELP_TEXT = (
    "我是跑团机器人。@我 说一句就能开始，之后 10 分钟内不用再 @：\n"
    "· 「开团」——开一局（会问你继续上次还是新开）\n"
    "· 「继续」——从上次断点接着跑\n"
    "· 「暂停」——存档退出\n"
    "· 「结束」——归档这条主线\n"
    "· 「状态」——现在跑到哪了\n"
    "开团需要群主、管理员，或登记过的 DM。"
)


def group_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.get_user_id()}"


def addressed_or_in_window() -> Rule:
    """群里：@机器人，或窗口内当事人发言；私聊一律算找机器人。"""

    async def _rule(event: MessageEvent) -> bool:
        if isinstance(event, PrivateMessageEvent):
            return True
        if event.is_tome():
            return True
        return ATTENTION.accepts(group_key(event), str(event.get_user_id()))

    return Rule(_rule)


gate_matcher = on_message(rule=addressed_or_in_window(), priority=1, block=False)


def sender_is_operator(event: MessageEvent) -> bool:
    """群主 / 管理员 / 白名单里的 DM / 超管。"""
    qq = str(event.get_user_id())
    if GATE.is_operator(qq):
        return True
    if isinstance(event, GroupMessageEvent) and getattr(event.sender, "role", None) in (
        "owner",
        "admin",
    ):
        return True
    try:
        from nonebot import get_driver

        if qq in {str(x) for x in get_driver().config.superusers}:
            return True
    except Exception:  # pragma: no cover - 配置缺失时不致命
        pass
    return False


# ---------- 文案 ----------


def state_text(group_id: str) -> str:
    state = GATE.state(group_id)
    if state.mode is Mode.IDLE:
        paused = GATE.paused_campaign(group_id)
        if paused:
            return f"当前没开团。上次的《{paused.title}》还停在断点上，@我 说「继续」就能接着跑。"
        return "当前没开团。@我 说「开团」就能开始一局。"
    campaign = state.campaign
    title = campaign.title if campaign else "（未命名）"
    if state.mode is Mode.ACTIVE:
        scene = f"，当前场景：{campaign.scene}" if campaign and campaign.scene else ""
        return f"正在跑《{title}》{scene}。"
    return f"《{title}》已暂停，@我 说「继续」可以从断点接着跑。"


def choose_text(has_paused: bool, paused_title: str) -> str:
    if has_paused:
        return (
            f"检测到上次的《{paused_title}》停在断点上。\n"
            "1）继续上次\n"
            "2）开一个新剧本\n"
            "直接回一句「继续」或「开新的」就行。"
        )
    return "好，开新团。确认一下：说「确认」我就开始，说「算了」就取消。"


# ---------- 主流程 ----------


@gate_matcher.handle()
async def _(event: MessageEvent):
    text = event.get_plaintext().strip()
    if text.startswith("/"):
        return  # 斜杠命令交给别的插件

    group_id = group_key(event)
    user_id = str(event.get_user_id())

    window = ATTENTION.get(group_id)
    if isinstance(event, PrivateMessageEvent) or event.is_tome() or window is None:
        # @我（或私聊）→ 打开/接管窗口，保留当前阶段
        window = ATTENTION.open(
            group_id, user_id, stage=window.stage if window else STAGE_CHAT
        )
    else:
        window.touch()

    if window.stage == STAGE_CONFIRM:
        await _handle_confirm(event, group_id, window, text)
        return
    if window.stage == STAGE_TITLE:
        await _handle_title(event, group_id, text)
        return
    await _handle_intent(event, group_id, text)


async def _handle_confirm(event: MessageEvent, group_id: str, window: Window, text: str):
    if window.initiator and str(event.get_user_id()) != window.initiator:
        await gate_matcher.finish("等发起人确认一下，你先别急。")

    has_paused = window.notes.get("has_paused") == "1"
    paused_title = window.notes.get("paused_title", "")
    choice = classify_confirm(text, has_paused=has_paused)

    if choice is None:  # 犹豫 / 没听懂 → 再问一次
        await gate_matcher.finish(choose_text(has_paused, paused_title))
        return
    if choice == "cancel":
        ATTENTION.close(group_id)
        await gate_matcher.finish("好，取消。")
        return
    if choice == "resume":
        campaign = GATE.resume(group_id)
        ATTENTION.close(group_id)
        if campaign is not None:
            events.record(
                CONN,
                group_id=group_id,
                kind="resume",
                campaign_id=campaign.id,
                summary=f"接着跑《{campaign.title}》" + (f"，上次停在{campaign.scene}" if campaign.scene else ""),
                actor=str(event.get_user_id()),
            )
        if campaign is None:
            await gate_matcher.finish("没找到可以继续的主线，说「开团」开个新的吧。")
            return
        await gate_matcher.finish(
            f"▶️ 接着跑《{campaign.title}》。\n"
            + (f"上次停在：{campaign.scene}\n" if campaign.scene else "")
            + "先说一下你们现在要做什么。"
        )
        return

    # choice == "new" → 问剧本名
    ATTENTION.set_stage(group_id, STAGE_TITLE)
    await gate_matcher.finish("新剧本叫什么名字？直接回一句就行（例如：霓虹血债）。")


async def _handle_title(event: MessageEvent, group_id: str, text: str):
    window = ATTENTION.get(group_id)
    if window and window.initiator and str(event.get_user_id()) != window.initiator:
        await gate_matcher.finish("让发起人给剧本起个名吧，一句话就行。")

    title = text.strip()[:TITLE_MAX] or "未命名主线"
    campaign = GATE.start_new(group_id, title)
    events.record(
        CONN,
        group_id=group_id,
        kind="start",
        campaign_id=campaign.id,
        summary=f"开团《{campaign.title}》",
        actor=str(event.get_user_id()),
    )
    ATTENTION.close(group_id)
    await gate_matcher.finish(
        f"🎬 《{campaign.title}》开始。\n"
        "把你们的角色卡发我，或者说「我有角色」；没有的话说「给我一个角色」。"
    )


async def _handle_intent(event: MessageEvent, group_id: str, text: str):
    state = GATE.state(group_id)
    intent = classify(text)

    if intent is Intent.HELP:
        await gate_matcher.finish(HELP_TEXT)
        return
    if intent is Intent.STATUS:
        await gate_matcher.finish(state_text(group_id))
        return

    if intent in (Intent.START, Intent.RESUME, Intent.PAUSE, Intent.END):
        if not sender_is_operator(event):
            await gate_matcher.finish("开团、暂停这类操作需要群主、管理员或登记过的 DM。")
            return

    if intent is Intent.PAUSE:
        campaign = GATE.pause(group_id)
        ATTENTION.close(group_id)
        if campaign is not None:
            events.record(
                CONN,
                group_id=group_id,
                kind="pause",
                campaign_id=campaign.id,
                summary=f"暂停《{campaign.title}》" + (f"，停在{campaign.scene}" if campaign.scene else ""),
                actor=str(event.get_user_id()),
            )
        await gate_matcher.finish(
            f"⏸ 《{campaign.title}》已存档。" if campaign else "现在没有在跑的主线。"
        )
        return

    if intent is Intent.END:
        campaign = GATE.end(group_id)
        ATTENTION.close(group_id)
        if campaign is not None:
            events.record(
                CONN, group_id=group_id, kind="end", campaign_id=campaign.id,
                summary=f"结束《{campaign.title}》", actor=str(event.get_user_id()),
            )
        await gate_matcher.finish(
            f"🏁 《{campaign.title}》已归档。" if campaign else "现在没有在跑的主线。"
        )
        return

    if state.mode is Mode.ACTIVE:
        await gate_matcher.finish(
            f"已经在跑《{state.campaign.title if state.campaign else ''}》了。说「暂停」或「结束」。"
        )
        return

    if intent not in (Intent.START, Intent.RESUME):
        # 不认识 → 不抢话，交给后面的插件（建卡 / 玩法）
        return
        return

    paused = GATE.paused_campaign(group_id)
    if intent is Intent.RESUME and paused is None:
        await gate_matcher.finish("没找到断点。说「开团」开个新的吧。")
        return

    window = ATTENTION.get(group_id) or ATTENTION.open(
        group_id, str(event.get_user_id())
    )
    window.notes["has_paused"] = "1" if paused else "0"
    window.notes["paused_title"] = paused.title if paused else ""
    ATTENTION.set_stage(group_id, STAGE_CONFIRM)
    await gate_matcher.finish(choose_text(paused is not None, paused.title if paused else ""))
