"""建卡 / 认领 / 检定（平台无关）：把「一句话」变成「一条回复」。

* 和 ``gate_flow`` 一样：不发消息、不认识适配器；
* 模型只出 JSON 结构，数值由 ``core.roster`` 的硬规则校验；
* 首次建卡走 RED 规则（属性 2–10、模板 3 次互换），简易版卡走认领。
"""

from __future__ import annotations

import json
import re as _re

from core import backup, easy_roster, events, growth, hazards, llm, party, roster
from core.runtime import CONN, GATE
from core.session import Mode
from cyberpunk import checks, commands, cyber_bonus, dice, easy
from cyberpunk.characters import Character

ACTIONS = {"create", "modify", "show", "chat"}

SYSTEM = """你是 Cyberpunk RED 跑团机器人的建卡助手。
你**只输出 JSON**，由程序校验，不要自己编造规则数值。

可用模板（必须从这里挑）：
{templates}

可用技能：{skills}

规则要点：
- 十属性 INT/REF/DEX/TECH/COOL/WILL/LUCK/MOVE/BODY/EMP，取值 2–10
- 模板建卡只允许最多 3 次属性互换（stat_swaps），交换双方属性值对调
- 技能等级 1–10；只能从「可用技能」里挑，写错会被丢弃；默认技能：觉察、教育通识、说服、搏击、闪避、潜行（各 2 级）
- 玩家说「随便/你决定」时，你自己挑一个贴合其描述的模板

输出字段：
{{"action": "create|modify|show|chat",
  "name": "角色名", "template": "模板名", "concept": "一句话人设",
  "stat_swaps": [["REF","DEX"]],
  "skill_overrides": {{"手枪": 6}},
  "stat_changes": {{"REF": 8}},
  "skill_changes": {{"潜行": 4}},
  "perks": ["俊美"],
  "reply": "对玩家说的话（纯文本，中文，2 句以内）"}}

- 玩家要一张新卡 → action=create
- 玩家要改现有卡 → action=modify，只填需要改的字段
- 玩家只是问卡面 → action=show
- 玩家在聊别的 → action=chat，reply 里自然回应，不要硬塞建卡
"""

DEFAULT_DV = 15

#: 「在跟机器人说话」而不是「在戏里行动」的措辞。
#: 跑团进行中命中这些，就不该被当成一次行动去掷骰。
WANTS_CARD = _re.compile(r"(给我(?:一个|个)?角色|建卡|做张卡)")

META_PATTERNS = (
    # 全部锚定在句首：句中出现「记录」「档案」不算系统操作。
    # 例：「看下控制台上的记录」是戏内行动，不该弹角色卡。
    r"^给我.{0,4}角色",
    r"^(?:帮我|麻烦|请)?.{0,3}(?:建|做|来|随机).{0,3}卡",
    r"^认领",
    r"^我的卡",
    r"^我(?:有|的)(?:角色|人物|卡)",
    r"^(?:用|拿|发)(?:我)?的?卡",
    r"^(?:我的)?(?:档案|卡面|角色卡)",
    r"^(?:检定|掷骰|投骰)",
    r"^/?(?:r|roll)\s",
    r"^奖励",
    r"^(?:记录|事件)(?:\s|$)",
    r"^(?:模板|有哪些模板)",
    r"^提升",
    r"^(?:开打|攻击|战斗|战况|收队|撤退|线索|fight|attack|battle|stop|clues)",
    r"^/?(?:备份|导出|备份列表|备份清单|恢复|还原|backup|restore)",
    r"^(?:坠落|跳(?:下|楼|崖)|摔(?:下|下来)|电击|触电|被电|溺水|窒息|憋气|淹|燃烧|着火|中毒|下毒|毒物)",
    r"^/?(?:在场|谁在|轮到谁|队伍|小队|party)",
    r"^(?:确认|确定|领取|就它|换一个|换张|取消|算了|不要)",
)


def looks_meta(text: str) -> bool:
    """这句话是在跟机器人说系统操作，而不是在戏里行动？"""
    import re

    t = (text or "").strip()
    if not t:
        return False
    return any(re.search(pat, t) for pat in META_PATTERNS)


def templates_text() -> str:
    return (
        roster.template_menu()
        + "\n\n直接说「给我一个角色，独狼」就按模板建；说「模板」可看两套清单。"
        + "\n\n"
        + easy.format_list()
        + "\n\n简易版卡比核心书少一半规则，适合随便玩一局；领取说「认领 火炬」。"
    )


# 认领要先确认：模板名 → 待提交，5 分钟没确认就作废
_CLAIM_PENDING = {}
CLAIM_TTL = 300.0
CONFIRM_WORDS = ("确认", "确定", "领取", "就它", "就要这个", "好")
CANCEL_WORDS = ("取消", "算了", "不要", "不领", "先不要")
SWAP_WORDS = ("换一个", "换张", "换个", "别的", "其他")


def pending_claim(group_id, user_id):
    """有没有还没确认的认领？过期就清掉。"""
    import time

    key = (str(group_id), str(user_id))
    item = _CLAIM_PENDING.get(key)
    if not item:
        return None
    name, ts = item
    if time.monotonic() - ts > CLAIM_TTL:
        _CLAIM_PENDING.pop(key, None)
        return None
    return name


def _set_pending(group_id, user_id, name):
    import time

    _CLAIM_PENDING[(str(group_id), str(user_id))] = (name, time.monotonic())


def _drop_pending(group_id, user_id):
    _CLAIM_PENDING.pop((str(group_id), str(user_id)), None)




# ---------- 备份 / 恢复 ----------


def _backup_commands(group_id: str, user_id: str, text: str) -> str | None:
    m = _re.match(r"^/?(?:备份|导出|backup)\s*$", text)
    if m:
        try:
            path = backup.save(CONN, group_id)
        except Exception as exc:  # noqa: BLE001
            return f"备份失败：{exc}"
        data = backup.export_group(CONN, group_id)
        return f"💾 已备份：{path.name}" + chr(10) + backup.counts(data)

    if _re.fullmatch(r"/?(?:备份列表|备份清单|backups?)", text):
        files = backup.list_backups()
        if not files:
            return "还没有备份。发「备份」生成一份。"
        lines = [f"· {p.name}" for p in files]
        return "最近的备份：" + chr(10) + chr(10).join(lines)

    m = _re.match(r"^/?(?:恢复|还原|restore)\s+(\S+)\s*(.*)$", text)
    if m:
        name, tail = m.group(1), (m.group(2) or "").strip()
        try:
            data = backup.load(backup.BACKUP_DIR / name)
        except backup.BackupError as exc:
            return str(exc)
        if tail not in ("确认", "确定", "confirm"):
            return (
                f"要恢复 {name}（{backup.counts(data)}）会覆盖当前进度。"
                + chr(10)
                + f"确定就发：恢复 {name} 确认"
            )
        backup.restore(CONN, data, group_id=group_id)
        return f"♻️ 已恢复 {name}（{backup.counts(data)}）"

    return None


def _plain_command(group_id: str, user_id: str, text: str) -> str | None:
    """不带斜杠的系统指令。命中就返回回复文本，否则 None。"""
    import re as _re

    body = text.strip()
    if body.startswith("/"):
        body = body[1:].strip()
    if not body:
        return None
    if body in CONFIRM_WORDS:
        hit = confirm_claim(group_id, user_id)
        if hit is not None:
            return hit
    if body in CANCEL_WORDS:
        hit = cancel_claim(group_id, user_id)
        if hit is not None:
            return hit
    if body in SWAP_WORDS:
        hit = swap_claim(group_id, user_id)
        if hit is not None:
            return hit


    m = _re.match(r"^(?:开打|fight)\s*(.*)$", body)
    if m:
        from cyberpunk import names as _n  # noqa: F401
        from core import combat as _cbt

        st = GATE.state(group_id)
        camp = st.campaign
        return _cbt.start_text(
            CONN,
            group_id,
            m.group(1),
            campaign_id=camp.id if camp else None,
            scene=(camp.scene or "") if camp else "",
        )
    m = _re.match(r"^(?:攻击|attack)\s*(.*)$", body)
    if m:
        from core import combat as _cbt

        return _cbt.attack_text(CONN, group_id, user_id, m.group(1))
    if _re.fullmatch(r"(?:战斗|battle|战况)", body):
        from core import combat as _cbt

        return _cbt.status_text(CONN, group_id)
    if _re.fullmatch(r"(?:收队|stop|撤退)", body):
        from core import combat as _cbt

        return _cbt.stop_text(CONN, group_id)
    if _re.fullmatch(r"/?(?:在场|谁在|轮到谁|队伍|小队|party)", body):
        return party.status_text(CONN, group_id)

    if hazards.match(body):
        from cyberpunk import dice as _dice

        return hazards.apply(CONN, group_id, user_id, body, roller=_dice.RandomRoller())

    _back = _backup_commands(group_id, user_id, body)
    if _back is not None:
        return _back
    if _re.fullmatch(r"/?(?:义体清单|赛博组件|我的义体|改造|人性|cyberware)", body):
        from core import cyber_flow as _cy

        return _cy.status_text(CONN, group_id, user_id)
    if _re.fullmatch(r"(?:线索|clues)", body):
        from core import clues as _cl

        return _cl.text_of(CONN, group_id)
    if _re.fullmatch(r"(模板|模板列表|templates)", body):
        return templates_text()
    m = _re.match(r"^认领\s*(.*)$", body)
    if m:
        return claim(group_id, user_id, m.group(1))
    if _re.fullmatch(r"(?:我(?:有|的)(?:角色|人物|卡)?|用(?:我)?的?卡|发(?:我)?的?卡)", body):
        return sheet(group_id, user_id)
    if _re.fullmatch(r"(我的卡|档案|卡面|角色卡|sheet)", body):
        return sheet(group_id, user_id)
    m = _re.match(r"^(?:记录|log)(?:\s+(\d+))?$", body)
    if m:
        return record(group_id, int(m.group(1)) if m.group(1) else 5)
    m = _re.match(r"^(?:检定|check)\s+(.+)$", body)
    if m:
        skill, dv, mods = parse_check_arg(m.group(1))
        return check(group_id, user_id, skill, dv=dv, mods=mods)
    return None


def _clean_skills(raw: dict) -> tuple[dict, list[str]]:
    """只保留规则书里存在的技能名；返回 (可用, 被丢掉的原名)。"""
    clean: dict[str, int] = {}
    dropped: list[str] = []
    for key, value in (raw or {}).items():
        hit = roster.resolve_skill(key)
        if hit:
            clean[hit] = int(value)
        else:
            dropped.append(str(key))
    return clean, dropped


async def handle(
    group_id: str, user_id: str, text: str, *, force: bool = False
) -> str | None:
    """自然语言建卡。

    默认只在 ``IDLE`` 时接管；跑团进行中传 ``force=True``（由上层判断
    这句话是不是系统操作），否则返回 None 交给回合层。
    """
    if not force and GATE.state(group_id).mode is not Mode.IDLE:
        return None
    text = (text or "").strip()
    if not text:
        return None

    # 斜杠可有可无：群里没人愿意敲 /模板，直接说「模板」也得认。
    if WANTS_CARD.search(text) and (
        roster.get(CONN, group_id, user_id)
        or easy_roster.get(CONN, group_id, user_id)
    ):
        return (
            "你已经有卡了。要重做就说「重做角色」，想留着就接着用；"
            "看卡面发「档案」。"
        )

    hit = _plain_command(group_id, user_id, text)
    if hit is not None:
        return hit

    card = roster.get(CONN, group_id, user_id)
    user_block = (
        f"玩家的话：{text}\n"
        f"玩家现有角色卡：{json.dumps(card, ensure_ascii=False) if card else '无'}"
    )
    try:
        plan = await llm.complete_json(SYSTEM.format(templates=roster.template_menu(), skills="、".join(roster.skill_names())), user_block)
    except llm.LLMError as exc:
        return f"模型这边出了点问题：{exc}"

    action = str(plan.get("action", "chat")).lower()
    if action not in ACTIONS:
        action = "chat"
    reply = str(plan.get("reply") or "").strip()

    if action == "show":
        return roster.render_card(card) if card else "你还没角色卡，说「给我一个角色」。"
    if action == "chat":
        return reply or "我在。要建卡就说「给我一个角色」。"

    if action == "create":
        try:
            fresh = roster.build(
                template=str(plan.get("template") or ""),
                name=str(plan.get("name") or ""),
                concept=str(plan.get("concept") or ""),
                stat_swaps=plan.get("stat_swaps") or [],
                skill_overrides=_clean_skills(plan.get("skill_overrides") or {})[0],
                perks=plan.get("perks") or [],
            )
        except roster.RuleError as exc:
            return f"这张卡不合规（{exc}）。换个说法我再试一次。"
        roster.save(CONN, group_id, user_id, fresh)
        head = f"{reply}\n\n" if reply else ""
        return (
            head
            + roster.render_card(fresh)
            + "\n想改就说，例如「把手枪提到 8」「名字换成老猫」。"
        )

    if card is None:
        return "你还没有角色卡，先说「给我一个角色」。"
    try:
        updated = roster.apply_patch(card, plan)
    except roster.RuleError as exc:
        return f"这样改不行（{exc}）。"
    roster.save(CONN, group_id, user_id, updated)
    return roster.render_card(updated)


# ---------- 认领 / 档案 / 记录 ----------


def claim(group_id, user_id, want):
    """第一步：先给看卡，等「确认」再落库。"""
    want = (want or "").strip()
    if not want:
        return "用法：认领 火炬（先看「模板」）"
    tpl = easy.get(want)
    if tpl is None:
        return "没有「" + want + "」这张卡。可用：" + "、".join(easy.names())
    _set_pending(group_id, user_id, tpl["name"])
    return easy.format_card(tpl) + '\n\n' + "要这张的话回「确认」；不满意就「换一个」，或者「取消」。"


def confirm_claim(group_id, user_id):
    """第二步：「确认」才真正落库。"""
    name = pending_claim(group_id, user_id)
    if not name:
        return None
    tpl = easy.get(name)
    if tpl is None:
        _drop_pending(group_id, user_id)
        return "「" + name + "」这张模板找不到了，重新说「模板」看看。"
    _drop_pending(group_id, user_id)
    card = easy_roster.save(CONN, group_id, user_id, tpl, name=None)
    return easy.format_card(card) + '\n\n' + "想改名就说「把名字改成 XX」；「档案」随时看卡。"


def cancel_claim(group_id, user_id):
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return "好，不领了。想要就说「模板」再看一遍。"


def swap_claim(group_id, user_id):
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return templates_text()



def sheet(group_id: str, user_id: str) -> str:
    card = roster.get(CONN, group_id, user_id)
    if card is not None:
        hp_max = roster.derived_of(card)["HP"]
        return roster.render_card(card) + "\n" + growth.render_state(card, hp_max)
    easy_card = easy_roster.get(CONN, group_id, user_id)
    if easy_card is not None:
        return easy.format_card(easy_card)
    return "你还没有卡。说「给我一个角色」，或「认领 火炬」。"


def record(group_id: str, n: int = 5) -> str:
    return events.render(events.recent(CONN, group_id, max(1, min(20, n))))


# ---------- 检定 ----------


def check(
    group_id: str,
    user_id: str,
    skill: str,
    *,
    dv: int = DEFAULT_DV,
    mods: tuple[int, ...] = (),
    roller=None,
) -> str:
    """掷一次技能检定：简易版卡 `> DV`，RED 卡 `≥ DV`。"""
    roller = roller or dice.RandomRoller()
    skill = (skill or "").strip()
    if not skill:
        return "用法：检定 潜行（可以加「难度 18」）"

    # 多人协作检定：若挂着队友的补充技能 +1（full.txt:11811-11817），这次检定就消费掉。
    # 读走即删，仅一次、不叠加。辅助者本人帮人时这里通常为空，不会误消费。
    helper_note = ""
    try:
        from core.turn_flow import pop_helper_bonus
        helper = pop_helper_bonus(group_id, user_id)
        if helper is not None:
            mods = mods + (int(helper["bonus"]),)
            helper_note = ("\n（补充技能：" + str(helper["source"]) + " 协助，"
                           + str(helper["skill"] or "相关技能") + " +1，full.txt:11811）")
    except Exception:  # noqa: BLE001  消费失败时不阻断正常检定
        helper_note = ""

    # 义体加成：已装的赛博组件（RED 义体）给这次检定加修正（规则书依据见
    # docs/待办缺口.md 复核节）。easy / RED 两分支共用；一个玩家只有一张卡。
    cyber_note = ""
    cc = easy_roster.get(CONN, group_id, user_id)
    if cc is None:
        cc = roster.get(CONN, group_id, user_id)
    if cc is not None:
        cb, c_notes = cyber_bonus.check_bonus(cc, skill=(skill or "").strip())
        if cb:
            mods = mods + (cb,)
            cyber_note = "（义体：" + "+".join(c_notes) + "）"

    easy_card = easy_roster.get(CONN, group_id, user_id)
    if easy_card is not None:
        name, level = easy.resolve_skill(skill, easy_card.get("skills", {}))
        if level is None:
            return f"简易版卡上没有「{skill}」这项技能。"
        stat = easy.skill_stat(name) or "INT"
        stat_value = int(easy_card["stats"].get(stat, 0))
        natural = roller.roll(10)
        total = natural + stat_value + int(level) + sum(mods)
        ok = total > dv  # Interlock：严格大于
        head = f"{'✅' if ok else '❌'} {easy_card['name']} 的「{name}」检定"
        detail = f"1d10={natural} + {stat} {stat_value} + 技能 {level} = {total} vs DV{dv}"
        hp = easy_card.get("hp", "?")
        return f"{head}\n{detail}\n生命 {hp} / 重伤 {easy_card.get('wound', '?')}" + helper_note + cyber_note

    card = roster.get(CONN, group_id, user_id)
    if card is None:
        return "你还没有卡。先「给我一个角色」或「认领 火炬」，再检定。"

    stat = commands.SKILL_STAT.get(skill)
    if stat is None:
        found = [k for k in commands.SKILL_STAT if skill in k or k in skill]
        if len(found) != 1:
            return f"我不确定「{skill}」算哪项属性。可以说得更具体些，比如「检定 手枪」。"
        skill = found[0]
        stat = commands.SKILL_STAT[skill]

    hero = Character(
        name=card.get("name", "无名"),
        stats=card["stats"],
        skills={k: int(v) for k, v in card.get("skills", {}).items()},
    )
    result = hero.check(
        roller, stat=stat, skill=skill, dv=dv, modifiers=mods, purpose=f"{skill}检定"
    )
    if result.fumble:
        tail = "（大失败：要出事了）"
    elif result.natural == 10:
        tail = "（大成功：再掷一次加值）"
    else:
        tail = ""
    return f"{'✅' if result.success else '❌'} {hero.name} 的「{skill}」检定\n{result.describe()}{tail}" + helper_note + cyber_note


def parse_check_arg(raw: str) -> tuple[str, int, tuple[int, ...]]:
    """``潜行`` / ``潜行 18`` / ``潜行 难度18 修正-3`` 都认。"""
    import re

    text = (raw or "").strip()
    dv = DEFAULT_DV
    mods: list[int] = []
    m = re.search(r"(?:难度|dv)\s*[=:：]?\s*(\d+)", text, re.I)
    if m:
        dv = int(m.group(1))
    for mm in re.finditer(r"(?:修正|mod)\s*[=:：]?\s*([+-]?\d+)", text, re.I):
        mods.append(int(mm.group(1)))
    stripped = re.sub(r"(?:难度|dv|修正|mod)\s*[=:：]?\s*[+-]?\d+", " ", text, flags=re.I)
    words = stripped.split()
    skill = words[0] if words else ""
    return skill, dv, tuple(mods)