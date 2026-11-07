"""闸门流程（平台无关）：把「一句话」翻译成「一条回复」。

设计约定：
* 这里**只管文案与状态迁移**，不发消息、不认识任何适配器；
* 平台插件（OneBot / QQ）负责把群号、用户号、是否 @ 传进来，再把返回值发出去；
* 不认识的意图返回 ``None``，让上层继续往下传（建卡 / 玩法）。
"""

from __future__ import annotations
import re

from dataclasses import dataclass

from core import events, story
from core.attention import Window
from core.intents import Intent, classify, classify_confirm
from core.runtime import ATTENTION, CONN, GATE
from core.session import Mode

STAGE_CHAT = "chat"
STAGE_CONFIRM = "confirm"
STAGE_TITLE = "title"

TITLE_MAX = 30

ADMIN_CMD = re.compile(r"^/?(?:备份|导出|备份列表|备份清单|恢复|还原|backup|restore)")

HELP_TEXT = (
    "我是跑团机器人。@我 说一句就能开始，之后 10 分钟内不用再 @：\n"
    "· 「开团」——开一局（会问你继续上次还是新开）\n"
    "· 「继续」——从上次断点接着跑\n"
    "· 「暂停」——存档退出\n"
    "· 「结束」——归档这条主线\n"
    "· 「状态」——现在跑到哪了\n"
    "开团需要群主、管理员，或登记过的 DM。"
)


@dataclass(slots=True)
class Step:
    """一次处理的结果。"""

    reply: str | None = None
    # 让上层继续处理（例如交给建卡/玩法），而不是把这条消息当成闸门指令
    pass_through: bool = False


def state_text(group_id: str) -> str:
    state = GATE.state(group_id)
    if state.mode is Mode.IDLE:
        paused = GATE.paused_campaign(group_id)
        if paused:
            return (
                f"当前没开团。上次的《{paused.title}》还停在断点上，"
                "@我 说「继续」就能接着跑。"
            )
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


def handle(
    *,
    group_id: str,
    user_id: str,
    text: str,
    is_addressed: bool,
    is_operator: bool,
) -> Step:
    """处理一条群消息。**不调模型**，纯规则。"""
    text = (text or "").strip()
    if not text:
        return Step()

    window = ATTENTION.get(group_id)
    if is_addressed or window is None:
        window = ATTENTION.open(
            group_id, user_id, stage=window.stage if window else STAGE_CHAT
        )
    else:
        window.touch()

    # 「退下/别听了」在任何阶段都算数，否则卡在确认态时闭不上嘴
    if classify(text) is Intent.EXIT:
        return _handle_intent(group_id, user_id, text, is_operator=is_operator)

    if window.stage == STAGE_CONFIRM:
        return _handle_confirm(group_id, user_id, window, text)
    if window.stage == STAGE_TITLE:
        return _handle_title(group_id, user_id, text)
    return _handle_intent(group_id, user_id, text, is_operator=is_operator)


# ---------- 三个阶段的处理 ----------


def _handle_confirm(group_id: str, user_id: str, window: Window, text: str) -> Step:
    if window.initiator and user_id != window.initiator:
        return Step(reply="等发起人确认一下，你先别急。")

    has_paused = window.notes.get("has_paused") == "1"
    paused_title = window.notes.get("paused_title", "")
    choice = classify_confirm(text, has_paused=has_paused)

    if choice is None:
        return Step(reply=choose_text(has_paused, paused_title))
    if choice == "cancel":
        # 「取消」只是放弃这次开团，窗口留着，接着说话不用重新 @
        ATTENTION.set_stage(group_id, STAGE_CHAT)
        return Step(reply="好，取消。想开团随时说一声。")
    if choice == "resume":
        campaign = GATE.resume(group_id)
        ATTENTION.set_stage(group_id, STAGE_CHAT)
        if campaign is None:
            return Step(
                reply="没找到可以继续的主线，说「开团」开个新的吧。"
            )
        events.record(
            CONN,
            group_id=group_id,
            kind="resume",
            campaign_id=campaign.id,
            summary=f"接着跑《{campaign.title}》"
            + (f"，上次停在{campaign.scene}" if campaign.scene else ""),
            actor=user_id,
        )
        return Step(
            reply=(
                f"▶️ 接着跑《{campaign.title}》。\n"
                + (f"上次停在：{campaign.scene}\n" if campaign.scene else "")
                + "先说一下你们现在要做什么。"
            ),
        )

    ATTENTION.set_stage(group_id, STAGE_TITLE)
    return Step(reply="新剧本叫什么名字？直接回一句就行（例如：霓虹血债）。")


def _handle_title(group_id: str, user_id: str, text: str) -> Step:
    window = ATTENTION.get(group_id)
    if window and window.initiator and user_id != window.initiator:
        return Step(reply="让发起人给剧本起个名吧，一句话就行。")

    title = text.strip()[:TITLE_MAX] or "未命名主线"
    scenario = story.match_scenario(title)
    if scenario:
        title = story.load(scenario).get("title", title)
    campaign = GATE.start_new(group_id, title, scenario=scenario or story.DEFAULT_ID)
    events.record(
        CONN,
        group_id=group_id,
        kind="start",
        campaign_id=campaign.id,
        summary=f"开团《{campaign.title}》",
        actor=user_id,
    )
    # 开团后窗口继续留着：紧接着说「我有角色」「给我一个角色」不该重新 @
    ATTENTION.set_stage(group_id, STAGE_CHAT)
    return Step(
        reply=(
            f"🎬 《{campaign.title}》开始。\n"
            "把你们的角色卡发我，或者说「我有角色」；没有的话说「给我一个角色」。"
        ),
    )


# 设计约束：状态转移（开团/继续/暂停/结束/取消）绝不关窗，只 set_stage；
# 关窗只允许发生在 Intent.EXIT 分支。原因见 docs/设计约束.md 第 1 条。

def _handle_intent(
    group_id: str, user_id: str, text: str, *, is_operator: bool
) -> Step:
    state = GATE.state(group_id)
    intent = classify(text)

    if intent is Intent.HELP:
        return Step(reply=HELP_TEXT)
    if intent is Intent.EXIT:
        ATTENTION.close(group_id)
        return Step(reply="好，我先不接话了。有事 @ 我。")

    if intent is Intent.STATUS:
        return Step(reply=state_text(group_id))

    if intent in (Intent.START, Intent.RESUME, Intent.PAUSE, Intent.END):
        if not is_operator:
            return Step(reply="开团、暂停这类操作需要群主、管理员或登记过的 DM。")

    if intent is Intent.PAUSE:
        campaign = GATE.pause(group_id)
        # 窗口不关：否则说「继续」时还得重新 @ 一次
        ATTENTION.set_stage(group_id, STAGE_CHAT)
        if campaign is not None:
            events.record(
                CONN,
                group_id=group_id,
                kind="pause",
                campaign_id=campaign.id,
                summary=f"暂停《{campaign.title}》"
                + (f"，停在{campaign.scene}" if campaign.scene else ""),
                actor=user_id,
            )
            return Step(reply=f"⏸ 《{campaign.title}》已存档。")
        return Step(reply="现在没有在跑的主线。")

    if intent is Intent.END:
        campaign = GATE.end(group_id)
        # 窗口不关：结束后想「再开一局」不该重新 @
        ATTENTION.set_stage(group_id, STAGE_CHAT)
        if campaign is not None:
            events.record(
                CONN,
                group_id=group_id,
                kind="end",
                campaign_id=campaign.id,
                summary=f"结束《{campaign.title}》",
                actor=user_id,
            )
            return Step(reply=f"🏁 《{campaign.title}》已归档。")
        return Step(reply="现在没有在跑的主线。")

    if ADMIN_CMD.match(text):
        # 备份/恢复属于运维指令，不该被「已经在跑」挡下来
        return Step(pass_through=True)

    if state.mode is Mode.ACTIVE:
        if intent in (Intent.START, Intent.RESUME):
            title = state.campaign.title if state.campaign else ""
            return Step(reply=f"已经在跑《{title}》了。说「暂停」或「结束」。")
        # 跑团进行中的普通发言 → 交给回合层（turn_flow）
        return Step(pass_through=True)

    if intent not in (Intent.START, Intent.RESUME):
        # 不认识的意图 → 交给建卡/玩法插件
        return Step(pass_through=True)

    paused = GATE.paused_campaign(group_id)
    if intent is Intent.RESUME and paused is None:
        return Step(reply="没找到断点。说「开团」开个新的吧。")

    window = ATTENTION.get(group_id) or ATTENTION.open(group_id, user_id)
    window.notes["has_paused"] = "1" if paused else "0"
    window.notes["paused_title"] = paused.title if paused else ""
    ATTENTION.set_stage(group_id, STAGE_CONFIRM)
    return Step(reply=choose_text(paused is not None, paused.title if paused else ""))
