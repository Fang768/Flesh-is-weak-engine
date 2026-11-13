"""回合：LLM 只提议，程序定数值，LLM 只负责把结果讲成人话。

三段式：
    1. :func:`propose`  —— 模型解析玩家意图，只准输出 action/skill/approach/target
    2. :func:`decide`   —— 程序查剧本闸门、定 DV、掷骰、算成败、决定下一场景
    3. :func:`narrate`  —— 模型把「已经发生的事实」写成 1~3 句旁白（禁止改数值）

任何要改动游戏状态的东西都不经过模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cyberpunk.checks import resolve_check
from cyberpunk.stats import STAT_ORDER
from core import llm, story

PROPOSE_SYSTEM = """你是赛博朋克跑团机器人的「意图解析器」。玩家说完一句话，你只判断他想做什么。

只输出一个 JSON 对象，字段固定为：
{"action": "act|talk|move", "skill": "技能名或空字符串", "approach": "一句话概括玩家要干什么", "target": "场景id或空字符串", "dv_adjust": 0, "dv_reason": ""}

硬规则：
1. skill 必须从「可用技能」列表里**原样**选一个；选不出就填空字符串。
2. 不许写数值、不许判定成败、不许编造场景 id（target 只能是「可去场景」里的）。
3. approach 不超过 40 字，用第二人称。
4. action：动手做事填 act，说话/交涉填 talk，单纯移动/观察填 move。
5. dv_adjust 只在环境明显有利/不利时给 -2..2 的整数，并在 dv_reason 里用一句话说明依据；
   拿不准就填 0 和空串。没有 dv_reason 的调整会被丢弃。
6. 武器的距离 DV 由程序按表硬算，dv_adjust 只用于非战斗任务的难度微调。
"""

NARRATE_SYSTEM = """你是赛博朋克跑团的旁白。给定「已经发生的事实」，写成 1–3 句中文旁白。

硬规则：
1. 不许改数字、不许改成败、不许新增奖励或代价。
2. 不出现骰点、DV、系统术语。
3. 不替玩家决定下一步；结尾留一个具体的感官细节。
4. 总长不超过 200 字。

只输出 JSON：{"narration": "旁白正文"}，不要额外字段。
"""

#: 兜底 DV（没有闸门匹配时）：日常偏难
FALLBACK_DV = 15


@dataclass(slots=True)
class Proposal:
    action: str = "act"
    skill: str = ""
    approach: str = ""
    target: str = ""
    #: 非战斗任务的 DV 微调（-2..2，只有 DM 给出理由才生效）
    dv_adjust: int = 0
    dv_reason: str = ""

    @classmethod
    def parse(cls, raw: dict) -> "Proposal":
        action = str(raw.get("action") or "act").strip().lower()
        if action not in ("act", "talk", "move"):
            action = "act"
        try:
            adjust = int(raw.get("dv_adjust") or 0)
        except (TypeError, ValueError):
            adjust = 0
        reason = str(raw.get("dv_reason") or "").strip()[:40]
        if not reason:
            adjust = 0          # 没理由就不调，避免模型随口改难度
        return cls(
            action=action,
            skill=str(raw.get("skill") or "").strip(),
            approach=str(raw.get("approach") or "").strip()[:60],
            target=str(raw.get("target") or "").strip(),
            dv_adjust=max(-2, min(2, adjust)),
            dv_reason=reason,
        )


@dataclass(slots=True)
class Outcome:
    skill: str = ""
    dv: int = FALLBACK_DV
    total: int = 0
    natural: int = 0
    success: bool = False
    fumble: bool = False
    margin: int = 0
    scene_from: str = ""
    scene_to: str = ""
    gate_id: str = ""
    note: str = ""
    #: DM 对任务 DV 的调整说明（有调整才非空）
    dm_note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def grade(self) -> str:
        if self.fumble:
            return "大失败"
        if self.success and self.natural == 10:
            return "大成功"
        if self.success:
            return "成功"
        return "失败"

    def program_line(self) -> str:
        parts = [f"🎲 {self.skill or '行动'} DV{self.dv}"]
        if self.skill:
            parts.append(f"掷出 {self.natural}，合计 {self.total}")
        parts.append(self.grade)
        if self.margin and self.success:
            parts.append(f"超出 {self.margin}")
        return " · ".join(parts)


def allowed_skills(card: dict, is_easy: bool) -> list[str]:
    from cyberpunk import easy, names, skills

    if is_easy:
        return sorted(card.get("skills", {}))
    table = {**names.skill_stat(), **skills.skill_table()}
    return sorted(s for s in card.get("skills", {}) if s in table)


def skill_stat_code(skill: str, card: dict, is_easy: bool) -> str | None:
    """技能 → 属性键。**RED 优先**（``rules/red_core/data/skills.json``）。"""
    from cyberpunk import easy, skills

    red = skills.stat_of(skill)
    if red:
        return red
    if is_easy:
        return easy.skill_stat(skill)
    return SKILL_STAT_FALLBACK.get(skill)


#: 少量别名（模板里的近战/子弹类叫法和术语表不完全一致）
SKILL_STAT_FALLBACK = {
    "手枪": "REF",
    "步枪": "REF",
    "冲锋枪": "REF",
    "霰弹枪": "REF",
    "近战武器": "DEX",
    "徒手搏斗": "DEX",
    "闪避": "DEX",
    "潜行": "DEX",
    "觉察": "INT",
    "说服": "COOL",
    "交谈": "EMP",
    "医疗": "TECH",
    "急救": "TECH",
    "电子/安防技术": "TECH",
    "审讯": "COOL",
    "交易": "COOL",
    "追踪": "INT",
    "推理": "INT",
    "本地专家": "INT",
    "察言观色": "EMP",
}


async def propose(text: str, *, card: dict, scene: dict, is_easy: bool,
                  board: str = "") -> Proposal:
    """把玩家的话翻译成一个提议动作。

    ``board`` 是 DM 牌面（``core/memory.board``）：只放**被标记的**剧情要点，
    不喂原始对话流水——跨回合不失忆，上下文又不膨胀。
    """
    skills = allowed_skills(card, is_easy)
    story_id = card.get("_story", story.DEFAULT_ID)
    exits = []
    for exit_ in story.exits_of(scene):
        target = story.scene(story_id, exit_["to"]) or {}
        name = str(target.get("name") or exit_["to"])
        label = exit_["label"]
        exits.append(f"{name}（{label}）" if label else name)
    room = f"" if not board else f"{board}\n"
    user = (
        f"地点：{scene.get('name')}——{scene.get('text')}\n"
        + room
        + f"可用技能：{'、'.join(skills) or '无'}\n"
        f"可去场景：{'、'.join(exits) or '无'}\n"
        f"玩家的话：{text}"
    )
    raw = await llm.complete_json(PROPOSE_SYSTEM, user, max_tokens=300, temperature=0.2)
    prop = Proposal.parse(raw)
    # 模型给了不在表里的技能 → 当作没给
    if prop.skill and prop.skill not in skills:
        prop.skill = ""
    return prop


def decide(
    proposal: Proposal,
    *,
    card: dict,
    story_id: str,
    scene_id: str,
    roller,
    is_easy: bool,
    mods: tuple[int, ...] = (),
) -> Outcome:
    from cyberpunk import easy

    scene = story.scene(story_id, scene_id) or {}
    gate = None
    if proposal.skill:
        # 只认玩家这次真的用的技能；对不上场景闸门就不推进场景
        gate = story.gate_for(story_id, scene_id, proposal.skill)
    else:
        # 模型没给技能 → 用卡上最高的三个技能试匹配闸门
        top = sorted(card.get("skills", {}), key=lambda k: -int(card["skills"][k]))[:3]
        gate = story.best_gate(story_id, scene_id, top)
    # 闸门认哪些技能：多技能写法（gate["skills"]）取模型给的那个（若在组内），
    # 否则取组内第一个。这样「说服 / 交谈」都算同一道门。
    gate_skill = ""
    if gate:
        allowed = story.gate_skills(gate)
        gate_skill = (proposal.skill if proposal.skill in allowed
                      else (allowed[0] if allowed else ""))
    skill = gate_skill or proposal.skill
    dv = int((gate or {}).get("dv") or FALLBACK_DV)
    dm_note = ""
    if gate and proposal.dv_adjust:
        base_dv = dv
        dv = max(9, min(29, base_dv + proposal.dv_adjust))
        if dv != base_dv:
            dm_note = f"任务 DV {base_dv} → {dv}：{proposal.dv_reason}"
        else:
            dm_note = (f"任务 DV {base_dv} 已到边界，DM 的 "
                       f"{proposal.dv_adjust:+d} 被夹住：{proposal.dv_reason}")

    stats = card.get("stats", {})
    stat_code = skill_stat_code(skill, card, is_easy) if skill else None
    if is_easy:
        stat_value = int(stats.get(stat_code, 0)) if stat_code else 0
        level = int(card.get("skills", {}).get(skill, 0)) if skill else 0
        result = resolve_check(
            roller,
            stat=stat_value,
            skill=level,
            dv=dv,
            modifiers=mods,
            purpose=f"{card.get('name', '你')}的{skill or '行动'}",
        )
    else:
        from cyberpunk.characters import Character

        hero = Character(
            name=str(card.get("name") or "你"),
            stats={k: int(stats.get(k, 5)) for k in STAT_ORDER},
            skills={str(k): int(v) for k, v in (card.get("skills") or {}).items()},
            perks={p: 1 for p in (card.get("perks") or [])},
        )
        result = hero.check(
            roller,
            stat=stat_code or "INT",
            skill=skill or "",
            dv=dv,
            modifiers=mods,
            purpose=f"{hero.name}的{skill or '行动'}",
        )

    # 判定号统一按 RED：静态 DV 检定「达到即成功」（两侧同一口径）
    success = result.success
    outcome = Outcome(
        skill=skill or proposal.skill or "行动",
        dv=dv,
        total=int(result.total),
        natural=int(result.natural),
        success=bool(success),
        fumble=bool(result.fumble),
        margin=int(result.total) - dv,
        scene_from=scene_id,
        gate_id=str((gate or {}).get("id") or ""),
        dm_note=dm_note,
    )
    step = story.next_scene(gate, success) if gate else None
    outcome.scene_to = step or scene_id
    if gate and not success:
        outcome.note = str(gate.get("fail_text") or "")
    elif not gate:
        outcome.note = ""
    return outcome


async def narrate(outcome: Outcome, *, proposal: Proposal, card: dict, story_id: str) -> str:
    from_scene = story.scene(story_id, outcome.scene_from) or {}
    to_scene = story.scene(story_id, outcome.scene_to) or {}
    moved = outcome.scene_to and outcome.scene_to != outcome.scene_from
    facts = [
        f"玩家：{card.get('name') or '你'}",
        f"玩家意图：{proposal.approach or '继续行动'}",
        f"结果：{outcome.grade}（{outcome.skill}，超出/落后 DV {outcome.margin}）",
    ]
    if moved:
        facts.append(f"位置：从「{from_scene.get('name')}」移动到「{to_scene.get('name')}」")
        facts.append(f"新场景：{to_scene.get('text')}")
    else:
        facts.append(f"仍在「{from_scene.get('name')}」：{from_scene.get('text')}")
    if outcome.note:
        facts.append(f"附带：{outcome.note}")
    user = "已发生的事实：" + "\n".join(facts)
    try:
        raw = await llm.complete_json(
            NARRATE_SYSTEM, user, max_tokens=320, temperature=0.7
        )
    except llm.LLMError:
        return ""
    return str(raw.get("narration") or raw.get("reply") or "").strip()[:220]
