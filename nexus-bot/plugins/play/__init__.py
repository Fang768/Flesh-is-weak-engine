"""跑团回合插件：开团之后，玩家说什么就推进一步。

流程：LLM 解析意图 → 程序定 DV/掷骰/判成败/换场景 → LLM 只写旁白。
所有数值由 :mod:`core.turn` 决定，模型改不了。
"""

from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from core import easy_roster, events, roster, story, turn
from core.runtime import ATTENTION, CONN, GATE
from core.session import Mode
from cyberpunk import RandomRoller
from cyberpunk import easy as easy_mod

ROLLER = RandomRoller()


def group_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.get_user_id()}"


def _in_play(event: MessageEvent) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return False
    group = group_key(event)
    if GATE.state(group).mode is not Mode.ACTIVE:
        return False
    qq = str(event.get_user_id())
    return (
        ATTENTION.accepts(group, qq)
        or event.is_tome()
        or isinstance(event, PrivateMessageEvent)
    )


play = on_message(rule=Rule(_in_play), priority=3, block=True)


@play.handle()
async def _(event: MessageEvent):
    group = group_key(event)
    qq = str(event.get_user_id())
    text = event.get_plaintext().strip()

    state = GATE.state(group)
    campaign = state.campaign
    if campaign is None:
        await play.finish("现在没有在跑的主线。")
        return

    story_id = getattr(campaign, "scenario", "demo") or "demo"
    scene_id = campaign.scene or story.first_scene(story_id)

    v3_card = roster.get(CONN, group, qq)
    easy_card = None if v3_card else easy_roster.get(CONN, group, qq)
    if v3_card is None and easy_card is None:
        await play.finish("你还没有角色卡。发「给我一个角色」，或 /模板 认领一张。")
        return
    card = dict(v3_card) if v3_card else dict(easy_card)
    card["_story"] = story_id
    is_easy = v3_card is None

    scene = story.scene(story_id, scene_id) or {}
    if not scene:
        await play.finish("剧本里找不到当前场景，先 /记录 看看发生了什么。")
        return

    try:
        proposal = await turn.propose(text, card=card, scene=scene, is_easy=is_easy)
    except Exception as exc:  # 模型侧任何问题都不该把回合吞掉
        await play.finish(f"模型没接上（{exc}）。再说一次？")
        return

    outcome = turn.decide(
        proposal,
        card=card,
        story_id=story_id,
        scene_id=scene_id,
        roller=ROLLER,
        is_easy=is_easy,
    )

    moved = outcome.scene_to and outcome.scene_to != scene_id
    if moved:
        GATE.set_scene(campaign.id, outcome.scene_to)
    events.record(
        CONN,
        group_id=group,
        kind="roll",
        campaign_id=campaign.id,
        summary=f"{card.get('name', '玩家')} {outcome.skill} DV{outcome.dv} → {outcome.grade}",
        actor=qq,
        data={"total": outcome.total, "natural": outcome.natural, "margin": outcome.margin},
    )
    if moved:
        events.record(
            CONN,
            group_id=group,
            kind="scene",
            campaign_id=campaign.id,
            summary=f"移动到「{story.scene_name(story_id, outcome.scene_to)}」",
            actor=qq,
        )

    narration = await turn.narrate(
        outcome, proposal=proposal, card=card, story_id=story_id
    )

    blocks = [outcome.program_line()]
    if narration:
        blocks.append(narration)
    target = story.scene(story_id, outcome.scene_to) if outcome.scene_to else None
    if moved and target:
        blocks.append(f"📍 {target['name']}：{target.get('text', '')}")
    elif outcome.note:
        blocks.append(outcome.note)

    ending_key = (target or {}).get("ending")
    if ending_key:
        text_end = story.ending_text(story_id, str(ending_key))
        if text_end:
            blocks.append(f"🎬 {text_end}")
            events.record(
                CONN,
                group_id=group,
                kind="note",
                campaign_id=campaign.id,
                summary=f"到达结局节点：{ending_key}",
                actor=qq,
            )

    await play.finish(chr(10) + chr(10).join(blocks))
