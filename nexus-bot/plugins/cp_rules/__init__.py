"""跑团规则插件：掷骰 + V3.0 技能检定。

命令：
    /r 2d6+1          —— 自由骰
    /dv               —— 查难度阶梯
    /check 手枪 dv=14 —— 掷一次技能检定（有角色卡就用卡，没有就用演示角色）
    /check 说服 dv=18 mod=-3

优先级约定：有卡的玩家用卡上的属性和技能等级；没卡的先给一张演示 SOLO 卡兜底。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from core import easy_roster, roster
from core.runtime import CONN
from cyberpunk import easy, perks
from cyberpunk import (
    DV,
    SKILL_STAT,
    Character,
    RandomRoller,
    parse_check_args,
    parse_notation,
    roll_verbose,
)
from cyberpunk.names import resolve_skill

# SOLO 模板属性（书 p60）
DEMO_STATS = {
    "INT": 4,
    "REF": 7,
    "TECH": 3,
    "DEX": 7,
    "COOL": 5,
    "WILL": 4,
    "STR": 5,
    "CON": 4,
    "MOVE": 4,
    "BODY": 7,
}

ROLLER = RandomRoller()
DEMO_SKILL_LEVEL = 6  # 演示用：让检定看得出成功率，接存档后删掉


def group_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.get_user_id()}"


def easy_check(event: MessageEvent, skill: str, dv: int, mods) -> str | None:
    """玩家有简易版卡时走这条路：1d10 + 属性 + 技能 + 修正 > DV（平局算守方赢）。"""
    card = easy_roster.get(CONN, group_key(event), event.get_user_id())
    if not card:
        return None
    stats = card.get("stats", {})
    skills = card.get("skills", {})
    name, level = easy.resolve_skill(skill, skills)
    if name is None:
        return f"❌ 简易版卡里没有「{skill}」。可用：{'、'.join(skills)}"
    stat = easy.skill_stat(name)
    if stat is None or stat not in stats:
        return f"❌ 「{name}」在简易版里没有关联属性，暂时算不了。"
    d10 = ROLLER.roll(10)
    mod_sum = sum(mods)
    total = d10 + int(stats[stat]) + int(level) + mod_sum
    ok = total > dv
    detail = f"1d10={d10} + {stat}{stats[stat]} + {name}{level}"
    if mod_sum:
        detail += f" {mod_sum:+d}"
    hp = card.get("hp", 0)
    head = f"{'✅' if ok else '❌'} {card['name']} 的{name}检定"
    line2 = f"{detail} = {total} vs DV{dv}（简易版：要 > DV 才算成功）"
    line3 = f"生命 {hp} / 重伤 {int(hp) // 2}"
    return chr(10).join([head, line2, line3])

_dv = on_command("dv", aliases={"难度"}, priority=5, block=True)
_gun = on_command("枪", aliases={"weapon", "武器"}, priority=5, block=True)
_cyber = on_command("义体", aliases={"cyber", "赛博"}, priority=5, block=True)
_perk = on_command("专长", aliases={"perk", "天赋", "talent"}, priority=5, block=True)


def build_demo(skill: str) -> Character:
    return Character("演示角色", dict(DEMO_STATS), skills={skill: DEMO_SKILL_LEVEL})


def load_hero(event: MessageEvent, skill: str) -> tuple[Character, bool]:
    """优先用玩家自己的角色卡；没有卡就退回演示角色。返回 (角色, 是否真卡)。"""
    group_id = str(getattr(event, "group_id", "") or "")
    if group_id:
        try:
            card = roster.get(CONN, group_id, event.get_user_id())
        except Exception:  # 存档读失败不该让检定挂掉
            card = None
        if card:
            return roster.to_character(card), True
    return build_demo(skill), False


@_dv.handle()
async def _(event: MessageEvent):
    table = " / ".join(f"{d.name}={d.value}" for d in DV)
    await _dv.finish(f"难度阶梯：{table}\n判定 ≥ DV 即成功，平局算成功。")


@_gun.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    from cyberpunk import gear

    keyword = str(arg).strip()
    if not keyword:
        await _gun.finish("用法：/枪 关键词（比如 /枪 手枪、/枪 马洛里安、/枪 步枪）")
        return
    hits = gear.search(keyword, limit=3)
    if not hits:
        await _gun.finish(f"没找到「{keyword}」。试试 /枪 手枪、/枪 步枪、/枪 近战、/枪 异型")
        return
    sep = chr(10) + chr(10)
    await _gun.finish(sep.join(gear.format_card(e) for e in hits))

@_cyber.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    from cyberpunk import cyber

    keyword = str(arg).strip()
    if not keyword:
        await _cyber.finish("用法：/义体 关键词（比如 /义体 接驳器、/义体 强化、/义体 智能枪）")
        return
    hits = cyber.search(keyword, limit=6)
    if not hits:
        await _cyber.finish(f"没找到「{keyword}」。试试 /义体 接驳器、/义体 强化、/义体 套装")
        return
    sep = chr(10)
    await _cyber.finish(sep.join(cyber.format_entry(e) for e in hits))


@_perk.handle()
async def _(event: MessageEvent, arg=CommandArg()):
    kw = str(arg).strip()
    if not kw:
        names = "、".join(item["zh"] for item in perks.perks().values())
        tnames = "、".join(item["zh"] for item in perks.talents().values())
        head = "🏅 专长（Perk）：" + names
        body = "✨ 天赋（Talent）：" + tnames
        await _perk.finish(chr(10).join([head, body, "", "查详情：/专长 权威、/专长 俊美"]))
        return
    hits = perks.search(kw)
    if not hits:
        await _perk.finish(f"没找到「{kw}」。试试 /专长 人脉、/专长 夜视、/专长 战斗直觉")
        return
    sep = chr(10) + chr(10)
    await _perk.finish(sep.join(perks.format_entry(k, en, item) for k, en, item in hits))
