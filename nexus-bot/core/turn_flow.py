"""回合推进（平台无关）：一句话 → 一条完整回复。

三段式（和 `plugins/play` 同一套东西，只是抽出来给 QQ 用）：

1. 模型**只提议**：把玩家的话翻译成 `{action, skill, approach, target}`；
2. 程序**定数值**：掷骰、比 DV、决定成败与换不换场景；
3. 模型**只旁白**：把已经定死的事实写成 1–3 句中文。

输出压成**一条**消息：官方 Bot 的被动回复每条消息最多 5 次，不能一句一段。
"""

from __future__ import annotations

import re

from core import (
    clues,
    combat,
    easy_roster,
    events,
    growth,
    hazards,
    llm,
    npc_flow,
    party,
    roster,
    story,
    turn,
)
from core.runtime import CONN, GATE
from core.session import Mode
from cyberpunk import dice


def _card_of(group_id: str, user_id: str) -> tuple[dict | None, bool]:
    card = easy_roster.get(CONN, group_id, user_id)
    if card is not None:
        return card, True
    return roster.get(CONN, group_id, user_id), False


#: 没有地图数据时让 DM 按场景估距离（不进判定，只补 range_m）
RANGE_SYSTEM = (
    "你是赛博朋克 RED 的 DM，正在帮系统估计交战距离。"
    "根据玩家这句话和敌方/场景描述，给出玩家到目标的直线距离（米）。"
    "只输出 JSON：{\"meters\": 整数, \"reason\": \"不超过 20 字的依据\"}。"
    "巷战/室内一般 3–30 米，开阔地 30–200 米；拿不准就给保守值。"
)


async def _dm_estimate_range(fight, target_name: str, text: str,
                             group_id: str) -> str:
    """没有地图数据时让 DM 估一次米数：给所有还没定距离的人填上同一段距离。

    失败返回空串——此时不猜 DV，而是让玩家/ DM 明说距离（见 combat.RANGE_ASK）。
    """
    if combat._map_for(fight) is not None:
        return ""
    unknown = [c for c in fight.combatants if c.alive and c.range_m <= 0]
    if not unknown:
        return ""
    names = "、".join(c.name for c in unknown[:4])
    prompt = (f"玩家行动：{text[:200]}\n目标：{target_name or names}\n"
              f"场景：{fight.scene}\n在场：{names}")
    try:
        raw = await llm.complete_json(
            RANGE_SYSTEM, prompt, timeout=15.0, max_tokens=120, temperature=0.1
        )
    except Exception:
        return ""
    try:
        meters = int(raw.get("meters"))  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return ""
    meters = max(1, min(800, meters))
    reason = str(raw.get("reason") or "").strip()[:40]  # type: ignore[union-attr]
    for c in unknown:
        c.range_m = meters
    combat._save(CONN, fight)
    events.record(
        CONN,
        group_id=group_id,
        kind="note",
        summary=f"DM 估距：{meters} 米（{reason or '按场景估算'}）",
        data={"range_m": meters, "reason": reason},
    )
    return ""


def _aftermath(group_id: str, campaign, story_id: str) -> str:
    """战斗结束后按 encounter.on_win/on_lose 推进场景或收团。"""
    fight = combat.load(CONN, group_id)
    if fight is None or fight.active:
        return ""
    scene = story.scene(story_id, fight.scene) or {}
    enc = scene.get("encounter") or {}
    if not fight.pcs:
        target = str(enc.get("on_lose") or "").strip()
    elif not fight.enemies:
        foes = [c for c in fight.combatants if c.side != "pc"]
        all_dead = bool(foes) and all(c.dead for c in foes)
        if all_dead:
            target = str(enc.get("on_win") or "").strip()
        elif any(c.escaped for c in foes):
            target = str(enc.get("on_withdraw") or enc.get("on_win") or "").strip()
        else:
            target = str(enc.get("on_win") or "").strip()
    else:
        return ""
    if not target:
        combat.end(CONN, group_id)
        return ""
    combat.end(CONN, group_id)
    if target.startswith("ending:"):
        ending = target.split(":", 1)[1].strip()
        GATE.end(group_id)
        text = story.ending_text(story_id, ending)
        return f"🏁 {text}" if text else "🏁 故事到这儿落幕了。"
    GATE.set_scene(campaign.id, target)
    moved = story.scene(story_id, target) or {}
    lines: list[str] = []
    if moved.get("name"):
        lines.append(f"📍 {moved['name']}")
    if moved.get("text"):
        lines.append(str(moved["text"]))
    lines.extend(_presence_lines(story_id, target))
    reward_line = _apply_rewards(group_id, campaign, story_id, target)
    if reward_line:
        lines.append(reward_line)
    if moved.get("ending"):
        GATE.end(group_id)
        end = story.ending_text(story_id, moved["ending"])
        if end:
            lines.append(f"🏁 {end}")
    return "\n\n".join(lines)


def aftermath(group_id: str) -> str:
    """给网关命令用：战斗刚结束就按 encounter.on_win/on_lose 推进。"""
    state = GATE.state(group_id)
    if state.campaign is None:
        return ""
    return _aftermath(group_id, state.campaign,
                      state.campaign.scenario or story.DEFAULT_ID)


# ---------- 自由输入的统一确认层（战斗内外共用） ----------
#
# 玩家说“我躲到吧台后面”“小丑欠我人情”这类自由输入时，先判断可行性：
# 可行→执行；有代价/不匹配→说明原因并等「确认」；完全不成立→说明 + 给替代。
# 这里先服务“用人情”，后面的地图/黑冰复用同一个 _PENDING 机制。

_PENDING: dict[str, dict] = {}
_CONFIRM = re.compile(r"^(?:确认|继续|是|好|对|执行|试试|坚持)(?:吧|啊|了)?$")
_CANCEL = re.compile(r"^(?:算了|取消|换|不|放弃|不了|不用)(?:吧|了|啊)?$")


def _all_cards(group_id: str):
    """(owner_qq, card, is_easy)；同一人两张卡时以简易卡为准。"""
    out = []
    seen = set()
    for card in easy_roster.members(CONN, group_id):
        owner = str(card.get("owner_qq") or "")
        seen.add(owner)
        out.append((owner, card, True))
    for card in roster.members(CONN, group_id):
        owner = str(card.get("owner_qq") or "")
        if owner not in seen:
            out.append((owner, card, False))
    return out


def _save_state_for(group_id: str, owner: str, card: dict, is_easy: bool) -> None:
    st = growth.state(card)
    if is_easy:
        easy_roster.set_state(CONN, group_id, owner, st)
    else:
        roster.set_state(CONN, group_id, owner, st)


def _presences(story_id: str, scene_id: str) -> list[dict]:
    """场景里「不参战但站在那里」的人：纯文本，或带 untouchable 的互动位。"""
    sc = story.scene(story_id, scene_id) or {}
    out: list[dict] = []
    for item in sc.get("presence") or []:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, dict):
            out.append(item)
    return out


def _presence_lines(story_id: str, scene_id: str) -> list[str]:
    lines = []
    for p in _presences(story_id, scene_id):
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        line = str(p.get("line") or p.get("text") or "").strip()
        lines.append(f"👤 {name}" + (f"：{line}" if line else ""))
    return lines


_ATTACK_WORDS = ("攻击", "开枪", "砍", "杀", "捅", "射", "冲上去", "动手")


def _presence_guard(group_id: str, story_id: str, scene_id: str,
                    text: str) -> str | None:
    """对不可触碰的过场角色动手：先说明原因，确认后再演“落空”。"""
    for p in _presences(story_id, scene_id):
        if not p.get("untouchable"):
            continue
        name = str(p.get("name") or "").strip()
        if not name or name not in text:
            continue
        if not any(w in text for w in _ATTACK_WORDS):
            continue
        _PENDING[group_id] = {"kind": "presence_attack", "npc": {"name": name}}
        return (
            f"{name}不是这场战斗里能攻击的目标——他站在那儿，像在别处。\n"
            "A. 先听他说完 B. 找别的目标 C. 还是要试一下。\n"
            f"真要动手，回「确认」。"
        )
    return None


def _scene_npcs(story_id: str, scene: dict) -> list[dict]:
    pool = story.load(story_id).get("npcs") or {}
    out: list[dict] = []
    for item in scene.get("npcs") or []:
        if isinstance(item, str):
            key, name = item, str((pool.get(item) or {}).get("name") or item)
            extra = {}
        else:
            key = str(item.get("key") or item.get("name") or "")
            name = str(item.get("name") or (pool.get(key) or {}).get("name") or key)
            extra = item
        npc = dict(pool.get(key) or {})
        npc.update({k: v for k, v in extra.items() if k in ("key", "name", "voice", "affiliations")})
        npc.setdefault("key", key)
        npc.setdefault("name", name)
        out.append(npc)
    return out


def _find_npc(story_id: str, scene: dict, text: str) -> dict | None:
    hits = [n for n in _scene_npcs(story_id, scene)
            if n.get("name") and str(n["name"]) in text]
    if not hits:
        return None
    return max(hits, key=lambda n: len(str(n.get("name") or "")))


def _apply_rewards(group_id: str, campaign, story_id: str, scene_id: str) -> str:
    """场景奖励（EB/IP/人情）按 campaign:scene 幂等结算，每个角色各记一份。"""
    sc = story.scene(story_id, scene_id) or {}
    rewards = sc.get("rewards")
    if not rewards:
        return ""
    key = f"{campaign.id}:{scene_id}"
    got: list[str] = []
    for owner, card, is_easy in _all_cards(group_id):
        lines = growth.settle(card, key, rewards)
        if lines:
            _save_state_for(group_id, owner, card, is_easy)
            got.extend(lines)
    if not got:
        return ""
    return "💰 结算：" + "；".join(dict.fromkeys(got))


def _favor_status(group_id: str, user_id: str) -> str:
    card, _ = _card_of(group_id, user_id)
    if card is None:
        return "你还没有角色卡。说「给我一个角色」或「认领 火炬」，再来看账。"
    lines = [f"💰 EB {growth.eb(card)}"]
    entries = growth.favors(card)
    if not entries:
        lines.append("人情账：空。")
    for entry in entries:
        mark = "已用" if entry.get("used") else "可用"
        scope = "组织" if entry.get("scope") == "org" else "个人"
        note = f"（{entry['note']}）" if entry.get("note") else ""
        lines.append(f"· {entry.get('holder_name') or entry.get('holder')}（{scope}）— {mark}{note}")
    legacy = growth.favors_left(card)
    if legacy:
        lines.append("perk 自带人情：" + "、".join(legacy))
    return "\n".join(lines)


def favor_status(group_id: str, user_id: str) -> str:
    return _favor_status(group_id, user_id)


def _use_favor_now(group_id: str, user_id: str, card: dict, is_easy: bool,
                   npc: dict, campaign, story_id: str, scene: dict, text: str) -> str:
    usable, mismatched = growth.favors_for(card, npc)
    name = str(npc.get("name") or npc.get("key") or "对方")
    if usable:
        entry = usable[0]
        try:
            growth.use_favor(card, str(entry.get("id") or ""))
        except growth.GrowthError as exc:
            return str(exc)
        _save_state_for(group_id, user_id, card, is_easy)
        events.record(
            CONN,
            group_id=group_id,
            kind="note",
            campaign_id=campaign.id,
            summary=f"用人情：{entry.get('holder_name') or name}",
            actor=user_id,
        )
        return (
            f"🤝 你提起{entry.get('holder_name') or name}那笔人情"
            f"（{entry.get('note') or '旧账'}）。\n"
            f"{name}沉默片刻，认了这笔账，答应帮忙。\n（这份人情已用掉。）"
        )
    if mismatched:
        entry = mismatched[0]
        kind = "组织" if entry.get("scope") == "org" else "个人"
        return (
            f"{name}把话头挡了回来：那是{entry.get('holder_name') or entry.get('holder')}"
            f"的{kind}人情，跟{name}对不上。\n账上这笔没动。"
        )
    unused = [f for f in growth.favors(card) if not f.get("used")]
    if unused:
        entry = unused[0]
        return (
            f"{name}把话头挡了回来：你账上那笔是{entry.get('holder_name') or entry.get('holder')}"
            f"的，跟{name}对不上。\n账上这笔没动。"
        )
    used = [f for f in growth.favors(card) if f.get("used")
            and str(f.get("holder_name") or "") == name]
    if used:
        return f"{name}摊手：那笔人情你已经用过了。\n（账上这笔没动。）"
    return _favor_missing_text(name)


def _favor_missing_text(name: str) -> str:
    return (
        f"{name}一脸茫然：账上没有这笔人情。\n"
        "你能做的：A. 当场许个新愿（欠对方一份人情） B. 换个人 C. 先算了。\n"
        "如果还是要开口，回「确认」。"
    )


def _begin_favor(group_id: str, user_id: str, card: dict, is_easy: bool,
                 campaign, story_id: str, scene_id: str, scene: dict, text: str) -> str:
    fight = combat.active(CONN, group_id)
    npc = _find_npc(story_id, scene, text)
    if npc is None:
        names = "、".join(str(n.get("name")) for n in _scene_npcs(story_id, scene) if n.get("name"))
        return f"这场景里没有你说的那个人（在场：{names or '没有别人'}）。要先找对人，或者换件事做。"
    name = str(npc.get("name") or npc.get("key") or "对方")
    if fight is not None:
        return f"⚔ 战斗里没时间跟{name}慢慢算人情：先打完这一轮。"
    usable, mismatched = growth.favors_for(card, npc)
    if usable:
        return _use_favor_now(group_id, user_id, card, is_easy, npc, campaign,
                              story_id, scene, text)
    if mismatched:
        entry = mismatched[0]
        kind = "组织" if entry.get("scope") == "org" else "个人"
        _PENDING[group_id] = {"kind": "favor_scope", "npc": npc, "user": user_id}
        return (
            f"{name}跟这笔账对不上：{entry.get('holder_name') or entry.get('holder')}"
            f"欠你的是{kind}人情，不是{name}。\n"
            "A. 找对人 B. 当场许个新愿 C. 算了。\n"
            "还是要硬提吗？回「确认」我就当你说出口了。"
        )
    unused = [f for f in growth.favors(card) if not f.get("used")]
    if unused:
        entry = unused[0]
        _PENDING[group_id] = {"kind": "favor_scope", "npc": npc, "user": user_id}
        return (
            f"{name}跟这笔账对不上：你账上那笔是"
            f"{entry.get('holder_name') or entry.get('holder')}的，不是{name}的。\n"
            "A. 找对人 B. 当场许个新愿 C. 算了。\n"
            "还是要硬提吗？回「确认」我就当你说出口了。"
        )
    if growth.favors(card):
        _PENDING[group_id] = {"kind": "favor_used", "npc": npc, "user": user_id}
        return (
            f"{name}提醒你：那笔人情已经用掉了。\n"
            "A. 当场欠新的人情 B. 换个人 C. 算了。\n"
            "还要再提一嘴吗？回「确认」我就当你说出口了。"
        )
    _PENDING[group_id] = {"kind": "favor_missing", "npc": npc, "user": user_id}
    return _favor_missing_text(name)


def _resolve_pending(group_id: str) -> str:
    item = _PENDING.pop(group_id, None)
    if not item:
        return ""
    name = str((item.get("npc") or {}).get("name") or "对方")
    kind = item.get("kind")
    if kind == "favor_scope":
        return f"你硬把话提了出来，{name}还是那句：“这不是我欠你的。”\n（账上没动。）"
    if kind == "favor_used":
        return f"{name}笑了笑：“那笔早用过了。”\n（账上没动。）"
    if kind == "presence_attack":
        return (f"你朝{name}出手，招式落空——他连衣角都没动。\n"
                "（这一下没伤到谁，也没改变局面。）")
    return f"你开了口，{name}一脸茫然：“什么人情？”\n（账上没有这笔，什么也没发生。）"


def use_favor_command(group_id: str, user_id: str, target_text: str) -> str:
    """「/用人情 <对象>」：显式使用，不再走确认；找不到或对不上就说明原因。"""
    state = GATE.state(group_id)
    if state.mode is not Mode.ACTIVE or state.campaign is None:
        return "现在不在跑团里。先「/开始」或「继续」。"
    card, is_easy = _card_of(group_id, user_id)
    if card is None:
        return "你还没有角色卡。说「给我一个角色」或「认领 火炬」，再来看账。"
    if combat.active(CONN, group_id) is not None:
        return "⚔ 战斗里没时间慢慢算人情：先打完这一轮。"
    campaign = state.campaign
    story_id = campaign.scenario or story.DEFAULT_ID
    scene_id = campaign.scene or story.first_scene(story_id)
    scene = story.scene(story_id, scene_id) or {}
    npc = _find_npc(story_id, scene, target_text)
    if npc is None:
        names = "、".join(str(n.get("name")) for n in _scene_npcs(story_id, scene) if n.get("name"))
        return f"这里没有「{target_text}」这个人（在场：{names or '没有别人'}）。"
    return _use_favor_now(group_id, user_id, card, is_easy, npc, campaign,
                          story_id, scene, target_text)


async def handle(
    group_id: str,
    user_id: str,
    text: str,
    *,
    roller=None,
) -> str | None:
    """跑团进行中：推进一次回合。未开团或玩家没卡时返回提示。"""
    state = GATE.state(group_id)
    if state.mode is not Mode.ACTIVE or state.campaign is None:
        return None

    card, is_easy = _card_of(group_id, user_id)
    if card is None:
        return "你还没有角色卡。说「给我一个角色」或「认领 火炬」，再行动。"

    campaign = state.campaign
    story_id = campaign.scenario or story.DEFAULT_ID
    scene_id = campaign.scene or story.first_scene(story_id)
    scene = story.scene(story_id, scene_id) or {}

    text = (text or "").strip()
    if not text:
        return None

    # 确认层的待办：只有「确认/取消」会作用到它；发别的命令视作放弃
    if group_id in _PENDING:
        if _CONFIRM.match(text):
            return _resolve_pending(group_id)
        if _CANCEL.match(text):
            _PENDING.pop(group_id, None)
            return "好，取消了。"
        _PENDING.pop(group_id, None)

    # 自由输入里提到「人情」→ 先走账本与确认，不交给模型编
    if "人情" in text and not text.startswith("/"):
        return _begin_favor(group_id, user_id, card, is_easy, campaign,
                            story_id, scene_id, scene, text)

    # ⓪ 休整 / 拾取装备：不进模型，直接结算
    if re.match(r"^(?:休整|治疗|包扎|休息)\s*(?:一下)?$", text):
        try:
            return combat.rest(CONN, group_id)
        except combat.CombatError as exc:
            return str(exc)
    picked = re.match(r"^(?:装备|带上|拾取)\s*(\S.*)$", text)
    if picked is not None:
        if combat.active(CONN, group_id) is not None:
            return "⚔ 战斗还没结束，先打完再装备。"
        item = picked.group(1).strip()
        add = easy_roster.add_gear if is_easy else roster.add_gear
        if not add(CONN, group_id, user_id, item):
            return "没找到你的角色卡。"
        events.record(
            CONN,
            group_id=group_id,
            kind="note",
            campaign_id=campaign.id,
            summary=f"装备 {item[:16]}",
            actor=user_id,
        )
        return f"🎒 已装备「{item}」，下一场战斗生效。"

    # ⓪b 非战斗移动：场景有地图时按 MOVE×2 米走，不交给模型编位置
    moved = combat.scene_move(CONN, group_id, user_id, text)
    if moved:
        events.record(
            CONN,
            group_id=group_id,
            kind="note",
            campaign_id=campaign.id,
            summary=f"移动：{text[:16]}",
            actor=user_id,
        )
        return moved

    # ① 点名了在场 NPC → 对话（最高优先：跟人说话不该掷骰）
    talked = npc_flow.match(story_id, scene_id, text)
    if talked is None:
        # ② 没点名，但刚跟某人说着话、这句也不像动手 → 继续对话
        last = npc_flow.active_npc(group_id, user_id)
        if last and not npc_flow.looks_like_action(text):
            npc = npc_flow.npc_by_key(story_id, last)
            if npc is not None:
                talked = (last, npc)

    if talked is not None:
        key, npc = talked
        npc_flow.remember(group_id, user_id, key)
        answer = await npc_flow.reply(npc=npc, text=text, card=card, scene=scene)
        events.record(
            CONN,
            group_id=group_id,
            kind="npc",
            campaign_id=campaign.id,
            summary=f"与{str(npc.get('name') or 'NPC')}交谈",
            actor=user_id,
        )
        return answer

    npc_flow.forget(group_id, user_id)

    # ②' 战斗中的非攻击动作（移动/装填/跑/起身/延迟，书内页 168）
    fight = combat.active(CONN, group_id)
    if fight is None:
        guard = _presence_guard(group_id, story_id, scene_id, text)
        if guard is not None:
            return guard

    # ②'0 报距离：玩家/DM 用一句话给米数（「距离 20 米」），不消耗动作
    if fight is not None and not combat.looks_like_attack(text) \
            and not combat.looks_like_grenade(text):
        told = combat.close_range(CONN, group_id, user_id, text)
        if told:
            events.record(
                CONN,
                group_id=group_id,
                kind="note",
                campaign_id=campaign.id,
                summary=f"报距离：{text[:12]}",
                actor=user_id,
            )
            return told
    # ②'a 战斗中的投掷爆炸物（手榴弹，书内页 151/153）
    if fight is not None and combat.looks_like_grenade(text):
        try:
            thrown = combat.throw_grenade(
                CONN, group_id, user_id, text, roller=roller or dice.RandomRoller()
            )
        except combat.CombatError as exc:
            return str(exc)
        events.record(
            CONN,
            group_id=group_id,
            kind="roll",
            campaign_id=campaign.id,
            summary=f"投掷手榴弹：{text[:12]}",
            actor=user_id,
        )
        tail = _aftermath(group_id, campaign, story_id)
        return f"{thrown}\n\n{tail}" if tail else thrown

    if fight is not None:
        acted = combat.act(
            CONN, group_id, user_id, text, roller=roller or dice.RandomRoller()
        )
        if acted:
            events.record(
                CONN,
                group_id=group_id,
                kind="roll",
                campaign_id=campaign.id,
                summary=f"战斗动作：{text[:12]}",
                actor=user_id,
            )
            tail = _aftermath(group_id, campaign, story_id)
            return f"{acted}\n\n{tail}" if tail else acted

    # ②'' 战斗中的「打他」→ 直接走战斗结算（不再当成普通检定）
    if fight is not None and combat.looks_like_attack(text):
        skill = combat.skill_from_text(text)
        target = combat.target_from_text(fight, text)
        # 全自动射击：换技能、换 DV 表、伤害乘算（full.txt:14800-14813）
        autofire = combat.looks_like_autofire(text)
        if autofire:
            skill = "自动武器"
        # 距离必须来自场景：没地图数据时让 DM 估一次；估不出来就不猜 DV，
        # attack() 会请玩家明说米数（返回 RANGE_ASK，不消耗动作）。
        if combat._map_for(fight) is None:
            await _dm_estimate_range(fight, target, text, group_id)
            fight = combat.load(CONN, group_id) or fight
        try:
            answer = combat.attack(
                CONN,
                group_id,
                user_id,
                target,
                skill=skill,
                autofire=autofire,
                weapon=combat.autofire_weapon_from_text(text) if autofire else None,
                roller=roller,
            )
        except combat.CombatError as exc:
            return str(exc)
        events.record(
            CONN,
            group_id=group_id,
            kind="roll",
            campaign_id=campaign.id,
            summary=("全自动射击 " if autofire else "攻击 ") + (target or "…"),
            actor=user_id,
        )
        tail = _aftermath(group_id, campaign, story_id)
        return f"{answer}\n\n{tail}" if tail else answer

    # ②''' 黑入/切断设备（encounter.hack，花一个动作）
    if fight is not None:
        try:
            hacked = combat.net_action(CONN, group_id, user_id, text, roller=roller)
            if hacked is None:
                hacked = combat.hack(CONN, group_id, user_id, text, roller=roller)
        except combat.CombatError as exc:
            return str(exc)
        if hacked is not None:
            events.record(
                CONN,
                group_id=group_id,
                kind="roll",
                campaign_id=campaign.id,
                summary=f"黑入：{text[:12]}",
                actor=user_id,
            )
            tail = _aftermath(group_id, campaign, story_id)
            return f"{hacked}\n\n{tail}" if tail else hacked

    # ②'' 环境伤害：坠落/电击/溺水/燃烧/中毒（自己受）
    hazard = hazards.match(text)
    if hazard is not None:
        answer = hazards.apply(
            CONN, group_id, user_id, text, roller=roller or dice.RandomRoller()
        )
        events.record(
            CONN,
            group_id=group_id,
            kind="roll",
            campaign_id=campaign.id,
            summary=f"环境伤害：{hazard}",
            actor=user_id,
        )
        return answer

    # ②''' 战斗还没打完：普通检定不能换场（胜负由 _aftermath 推进）
    if fight is not None:
        return "⚔ 战斗还没结束：先攻击、移动、装填、跑，或者「收队」。"

    # ③ 查线索
    clue = clues.match(story_id, scene_id, text)
    if clue is not None:
        answer = clues.attempt(
            CONN,
            group_id=group_id,
            campaign_id=campaign.id,
            card=card,
            clue=clue,
            is_easy=is_easy,
            roller=roller or dice.RandomRoller(),
        )
        events.record(
            CONN,
            group_id=group_id,
            kind="clue",
            campaign_id=campaign.id,
            summary=f"调查线索 {clue.get('id')}",
            actor=user_id,
        )
        return answer

    degraded = False
    try:
        proposal = await turn.propose(text, card=card, scene=scene, is_easy=is_easy)
    except llm.LLMError:
        # 降级：模型挂了也要把这一回合走完（照常掷骰），只是没有旁白。
        degraded = True
        fallback_skill = "觉察" if "觉察" in (card.get("skills") or {}) else ""
        proposal = turn.Proposal(
            action="尝试", skill=fallback_skill, approach="", target=""
        )

    # 战斗中的技能检定要带环境修正（设计 §3：如歌剧院尖叫 −4）
    mods: tuple[int, ...] = ()
    mod_note = ""
    if fight is not None:
        me = next((c for c in fight.combatants
                   if c.owner_qq == user_id and c.side == "pc"), None)
        if me is not None:
            value = combat.modifier_for(fight, me)
            if value:
                mods = (value,)
            mod_note = combat.modifier_text(fight, me)

    outcome = turn.decide(
        proposal,
        card=card,
        story_id=story_id,
        scene_id=scene_id,
        roller=roller or dice.RandomRoller(),
        is_easy=is_easy,
        mods=mods,
    )
    party.advance(CONN, group_id, user_id)

    moved = bool(outcome.scene_to and outcome.scene_to != scene_id)
    if moved:
        GATE.set_scene(campaign.id, outcome.scene_to)
        events.record(
            CONN,
            group_id=group_id,
            kind="scene",
            campaign_id=campaign.id,
            summary=f"转到 {story.scene_name(story_id, outcome.scene_to)}",
            actor=user_id,
        )
    events.record(
        CONN,
        group_id=group_id,
        kind="roll",
        campaign_id=campaign.id,
        summary=f"{outcome.skill or '行动'} {outcome.grade}（合计 {outcome.total} vs DV{outcome.dv}）",
        actor=user_id,
        data={"natural": outcome.natural, "total": outcome.total,
              "dv": outcome.dv, "dv_note": outcome.dm_note},
    )

    lines = [outcome.program_line()]
    if outcome.dm_note:
        lines.append(f"（DM 判定：{outcome.dm_note}）")

    narration = ""
    if not degraded:
        try:
            narration = await turn.narrate(
                outcome, proposal=proposal, card=card, story_id=story_id
            )
        except Exception:  # noqa: BLE001  旁白只是锦上添花
            narration = ""
    if narration:
        lines.append(narration)
    if degraded:
        lines.append("（模型没接上，这一回合只结算了数值）")
    if outcome.note:
        lines.append(outcome.note)
    if mod_note:
        lines.append(mod_note)

    if moved:
        target = story.scene(story_id, outcome.scene_to) or {}
        if target.get("name"):
            lines.append(f"📍 {target['name']}")
        if target.get("text"):
            lines.append(target["text"])
        lines.extend(_presence_lines(story_id, outcome.scene_to))
        reward_line = _apply_rewards(group_id, campaign, story_id, outcome.scene_to)
        if reward_line:
            lines.append(reward_line)
        if target.get("ending"):
            GATE.end(group_id)
            end = story.ending_text(story_id, target["ending"])
            if end:
                lines.append(f"🏁 {end}")

    return "\n\n".join(lines)
