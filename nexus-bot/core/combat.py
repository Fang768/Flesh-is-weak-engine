"""战斗：按核心书（书内页 167–182 / PDF 185–200）实现的先攻队列与回合。

书里写死的规则（不复述、直接按它做）：

* 先攻 = 反应 + 1D10，从大到小排成先攻队列；同值重掷，高者在前（书内页 168）。
* 每回合约 3 秒，每人 **1 次移动动作 + 1 次其他动作**；队列走完从队首重来 = 新轮次。
* 远程攻击 = `反应 + 武器技能 + 1D10` vs **距离决定的 DV**（书内页 175）。
  世界始终有物理场景，所以距离也必须来自场景：有地图就按格算；没有地图数据就用
  encounter/scene 的 `range_m`，或者 DM 现估的米数（`combatants.stat.range_m`）。
  两者都没有时**不猜 DV**，直接请 DM 给一个距离（或玩家说「距离 20 米」）。
* 近战攻击 = `敏捷 + 近战武器 + 1D10` vs `敏捷 + 闪避 + 1D10`（书内页 175 示例）。
  近战伤害**无视一半护甲**（向上取整）。
* 瞄准射击：花掉本回合全部其他动作、ROF 限 1、判定 **−8**；打头「穿透护甲后伤害翻倍」，
  打腿「断腿（严重伤势）」，打手中物「东西脱手」（书内页 174）。
* 装填 = 一个其他动作，**完全重新装填**（补满整个弹匣），不能混装弹种（书内页 174 / :14543-14545）。
  单发攻击扣 1 发、全自动扣 10 发，命中与否都扣；弓/十字弩永不装填也不耗弹（:14898-14899）。

没有建模的：**延迟动作的事件调度**（书内页 170 = `full.txt:14430-14433` 要求「指定一个
触发事件 + 预设动作和目标」，本引擎没有事件调度器，玩家说「延迟」只等于结束回合，
并诚实提示这一限制）、**地形被炸毁后的状态**（`full.txt:15067` 只说爆炸伤及地形，没给
破坏后的地形规则；掩体耐久是另一条、已实现）。

中毒不在这里：它走 `core/hazards.py` 的 `poison()`（折磨与药物抵抗 DV，直接 HP 伤害）。
"""

from __future__ import annotations

import json
import re
from collections import deque
from functools import lru_cache
from pathlib import Path
import sqlite3
from dataclasses import dataclass, field

from cyberpunk import ammo_effect, cyber_bonus, cyber_weapon, dice
from cyberpunk import easy as easy_rules
from cyberpunk.crit import SERIOUS_EXTRA_DAMAGE, SERIOUS_TRIGGER_SIXES
from cyberpunk.damage import roll_dice_full as _roll_dice_full
from core import easy_roster, roster
from core import map as gridmap
from core.db import now

# ── 敌人速览（数值是我们给的 NPC 面板，不是书的规则）────────────


@dataclass(frozen=True)
class EnemyPreset:
    name: str
    hp: int
    ref: int
    dex: int
    dodge: int
    skill: int
    sp: int
    damage: str
    #: 体格（死亡豁免定位目标，full.txt:15943）
    body: int = 5
    #: 重伤线（enemies.json 的 wound）；0 = 用 max_hp // 2
    wound: int = 0
    #: 用于语义匹配的标签（改造/护甲/武器/备注），如「水平阻尼降噪」
    tags: tuple[str, ...] = ()


ENEMY_PRESETS: dict[str, EnemyPreset] = {
    "打手": EnemyPreset("打手", hp=20, ref=6, dex=6, dodge=4, skill=4, sp=4, damage="2D6"),
    "精英": EnemyPreset("精英", hp=35, ref=7, dex=7, dodge=6, skill=6, sp=7, damage="3D6"),
    "头目": EnemyPreset("头目", hp=50, ref=8, dex=8, dodge=8, skill=8, sp=9, damage="3D6+1"),
}
DEFAULT_ENEMY = "打手"


ENEMIES_FILE = Path(__file__).resolve().parents[2] / "rules/red/data/enemies.json"


@lru_cache(maxsize=1)
def _enemy_data() -> dict:
    """读 `rules/red/data/enemies.json`；读不到就当空表（回退内置三档）。"""
    if not ENEMIES_FILE.exists():
        return {}
    try:
        raw = json.loads(ENEMIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


@lru_cache(maxsize=128)
def _preset_from_json(name: str) -> EnemyPreset | None:
    data = _enemy_data().get(name)
    if not data:
        return None
    stats = data.get("stats") or {}
    skills = data.get("skills") or {}
    weapons = data.get("weapons") or []
    attack = max((int(skills.get(str(w.get("skill", "")), 0)) for w in weapons), default=0)
    tags = [name, str(data.get("note") or ""), str(data.get("armor") or "")]
    cyber = data.get("cyber") or []
    if isinstance(cyber, str):
        tags.append(cyber)
    else:
        tags.extend(str(x) for x in cyber)
    for w in weapons:
        tags.extend([str(w.get("name") or ""), str(w.get("skill") or "")])
    return EnemyPreset(
        name,
        hp=int(data.get("hp", 20)),
        ref=int(stats.get("REF", 5)),
        dex=int(stats.get("DEX", 5)),
        dodge=int(skills.get("闪避", 4)),
        skill=attack,
        sp=int(data.get("sp", 4)),
        damage=str(weapons[0].get("damage", "2D6")) if weapons else "2D6",
        body=int(stats.get("BODY", 5)),
        wound=int(data.get("wound") or 0),
        tags=tuple(t for t in tags if t),
    )


def enemy_preset(name: str) -> EnemyPreset | None:
    """先查剧本数据（enemies.json），再查内置三档。"""
    return _preset_from_json(name) or ENEMY_PRESETS.get(name)


def enemy_names() -> list[str]:
    return list(dict.fromkeys(list(_enemy_data()) + list(ENEMY_PRESETS)))

#: 技能 → 伤害（书里的武器表还有具体枪械，这里先用按技能的粗粒度）
WEAPON_DAMAGE = {
    "手枪": "2D6",
    "抵肩枪械": "2D6",
    "自动武器": "2D6",
    "近战武器": "2D6",
    "重武器": "3D6",
    "武术": "2D6",
    "搏击": "1D6",
}
MELEE_SKILLS = {"近战武器", "武术", "搏击"}

# ── 弹种（full.txt:26688-26900） ──────────────────────────────
#
# 书里「装填 = 用一种子弹种类完全重新装填武器」，且「你不能在一个弹匣里混合
# 多种子弹种类」（:14731-14732）→ 弹种跟着**弹匣**走，所以记在战斗者身上。
#
# 这一轮只把「穿甲弹」的战斗效果接进结算，其余弹种按原文落库、只做提示：
#   · 穿甲弹（:26726-26731）：护甲被穿透时 **SP 减值 1 → 2**（不是护甲减半）
#   · 橡胶弹（:26816-26823）：不造成严重伤势、不削减护甲、HP 不会被打到 1 以下
#   · 其余（生物毒素/EMP/膨胀/闪光/燃烧/毒气/麻醉/智能/烟雾/催泪）多为
#     「攻击不造成伤害，改成过一次 DV 检定」，还没有通用的「检定效果」通道，
#     先只记录弹种、在战斗里给出提示（「约定」，见 docs/进度.md）。
AMMO_BASE = "基础弹药"
AMMO_AP = "穿甲弹"
AMMO_RUBBER = "橡胶弹"

#: 弹种 → 额外说明（不知道的弹种按基础弹药算）
AMMO_TYPES = {
    AMMO_BASE: "标准弹药，没有特殊效果。",
    AMMO_AP: "穿透护甲时 SP 减值从 1 增加到 2（full.txt:26730-26731）。",
    "生物毒素弹": "仅箭矢与榴弹；攻击不造成伤害，改过 DV15 折磨&药物耐受，失败 3d6 直接伤害。",
    "EMP 弹": "仅榴弹；不过伤害，改过 DV15 赛博技术，失败 2 件赛博组件失效 1 分钟。",
    "膨胀弹": "仅箭矢/子弹/独头弹；头部与体内异物伤势重投 1d10，且不再吃那 5 点额外伤害。",
    "闪光弹": "仅榴弹；不过伤害，改过 DV15 折磨&药物耐受，失败吃眼部受损+耳朵受损。",
    "燃烧弹": "穿透护甲就点燃；每回合结束 2 点直接 HP，不可叠加。",
    "毒气弹": "仅箭矢与榴弹；不过伤害，改过 DV13 折磨&药物耐受，失败 2d6。",
    AMMO_RUBBER: "不造成严重伤势、不削减护甲；HP 不会被打到 1 以下。",
    "麻醉弹": "仅箭矢与榴弹；不过伤害，改过 DV13，失败昏倒 1 分钟。",
    "智能弹药": "需要赛博组件「瞄准目镜」，没装就不开火；单发打偏 ≤4 时用 1d10+10 再投一次。",
    "烟雾弹": "仅榴弹；10×10 米烟雾 1 分钟，被烟雾遮挡 −4。",
    "催泪弹": "仅榴弹；不过伤害，改过 DV13，失败吃眼部受损。",
}

#: 历史常量：这批弹种书里写「攻击不造成任何伤害，改成过一次 DV 检定」。
#: **2026-11-10 起已由 `cyberpunk/ammo_effect.py` 真正结算**（命中后改投检定），
#: 此集合仅保留给老测试/外部引用，内部不再使用。
AMMO_CHECK_ONLY = {
    "生物毒素弹", "EMP 弹", "闪光弹", "毒气弹", "麻醉弹", "催泪弹",
}

#: 书里逐条写了「可用弹药类型」，这里只记类别限制（不逐枪存）
AMMO_ALLOWED_HINT = {
    AMMO_AP: "霰弹除外",
    "生物毒素弹": "仅箭矢与榴弹",
    "EMP 弹": "仅榴弹",
    "膨胀弹": "仅箭矢/子弹/独头弹",
    "闪光弹": "仅榴弹",
    "燃烧弹": "仅箭矢/子弹/榴弹/霰弹",
    "毒气弹": "仅箭矢与榴弹",
    AMMO_RUBBER: "仅箭矢/子弹/独头弹",
    "麻醉弹": "仅箭矢与榴弹",
    "智能弹药": "仅箭矢/子弹/火箭弹",
    "烟雾弹": "仅榴弹",
    "催泪弹": "仅榴弹",
}

#: 全自动射击一次要打出去的子弹（full.txt:14802「一个动作 + 10 发子弹」）
AUTOFIRE_ROUNDS = 10

#: 各类武器的常见弹匣容量（weapons.json 里 SMG 20–50、步枪 5–35 的中位值）。
#: 「约定」：玩家没写具体枪型时按类别取默认容量。
_MAG_DEFAULT = {"手枪": 8, "冲锋枪": 30, "霰弹枪": 8, "突击步枪": 30,
                "狙击步枪": 5, "弓": 1, "榴弹发射器": 6, "火箭筒": 1}


def _mag_of(weapon: str | None) -> int:
    """这件武器的默认弹匣容量。"""
    return _MAG_DEFAULT.get(weapon or "", 8)


#: 不需要装填的武器：弓/十字弩「拈弓搭箭是攻击动作的一部分」，永远不用重装填
#: （full.txt:14898-14899）。所以它们的弹药不消耗、也不会打空。
NO_RELOAD_WEAPONS = {"弓"}


def _spend_round(me: "Combatant", weapon: str | None) -> int:
    """打掉一发子弹，返回扣完还剩几发。

    还没开始计数（`ammo == -1`）就先按满弹匣起算，这样「开打时枪是满的」是免费的。
    弓不扣弹（full.txt:14898-14899）。
    """
    if weapon in NO_RELOAD_WEAPONS:
        return me.rounds_left(weapon)
    if me.ammo < 0:
        me.ammo = me.mag or _mag_of(weapon)
    me.ammo = max(0, me.ammo - 1)
    return me.ammo


def _out_of_ammo(me: "Combatant", weapon: str | None) -> str | None:
    """弹匣空了就返回一句拒绝文案（不消耗动作），否则 None。"""
    if weapon in NO_RELOAD_WEAPONS:
        return None
    if me.rounds_left(weapon) > 0:
        return None
    return (f"{me.name} 的{weapon or '武器'}没子弹了（0 发）。"
            "先花一个其他动作装填（full.txt:14543-14545）。")


def _has_cyberarm(me: "Combatant") -> bool:
    """有没有赛博臂/义肢（让体格低于 10 的人也能砸钢制掩体，书内页 183）。"""
    parts = " ".join(me.tags)
    return "赛博臂" in parts or "义肢" in parts


def _armor_step(me: "Combatant", target: "Combatant", *, melee: bool) -> int:
    """这一击把目标护甲磨掉几点。

    默认 1 点。**穿甲弹**把「护甲被穿透时 SP 减值从 1 增加为 2」
    （full.txt:26726-26731）→ 远程 + 穿甲弹时返回 2。
    """
    if not melee and me.ammo_type == AMMO_AP:
        return 2
    return 1


#: 射速（ROF）：一次攻击动作里这件武器能打几次（full.txt:14557「一次攻击动作中
#: 该武器能射击的最大次数」）。
#:
#: 「约定」：书里是**逐枪**的表（中型手枪 ROF2、霰弹枪 ROF1、突击步枪 ROF1…），
#: 本仓库按武器类别取一个代表值（见 weapons.json 的 `rof` 字段口径）。
_ROF_DEFAULT = {"手枪": 2, "冲锋枪": 1, "霰弹枪": 1, "突击步枪": 1,
                "狙击步枪": 1, "弓": 1, "榴弹发射器": 1, "火箭筒": 1,
                "近战武器": 2, "武术": 2, "搏击": 2}

#: 书里「无论你持有多少武器…都不能进行超过两次攻击判定」（full.txt:14676-14685）
ROF_CAP = 2


def _rof_of(weapon: str | None, *, melee: bool) -> int:
    """这件武器一次攻击动作能打几次（1 或 2）。"""
    name = weapon or ""
    if melee:
        # 近战表：轻型/中型/重型都是 2（full.txt:14985-15010）
        return min(ROF_CAP, _ROF_DEFAULT.get(name, 2))
    return min(ROF_CAP, max(1, _ROF_DEFAULT.get(name, 1)))


def _ammo_note(me: "Combatant", *, melee: bool) -> str:
    """开火前把特殊弹种的提示拼出来（空串 = 基础弹药，没什么可说）。"""
    if melee:
        return ""
    kind = me.ammo_type or AMMO_BASE
    if kind == AMMO_BASE:
        return ""
    text = AMMO_TYPES.get(kind)
    if not text:
        return ""
    return f"（{kind}：{text}）"


def _is_mechanical(target: "Combatant") -> bool:
    """纯机械目标不吃「打到肉」的效果。

    书里原文（``full.txt:698-700`` FAQ）：
      「我们用这个指代非无机物，**无人机和完全化赛博格不视为肉类**。
        当我们提到『肉』这个词时针对某些部位时（例如催泪瓦斯影响肉眼），
        我们就要看看这个部位是否进行了赛博改造。」

    所以判定分两层：
    1. 明显机械的敌人（炮塔/无人机/机器人）→ 直接免疫；
    2. 完全赛博化的目标（全身义体）→ 也免疫。
    「约定」：第 2 层用「装了外壳/义肢类义体且无肉手臂」近似，本引擎没有
    完整的「全身改造度」字段，所以只对明确标了机械标签的生效。
    """
    words = ("机械", "炮塔", "无人机", "机器人", "自动炮塔", "turret", "drone")
    tags = " ".join(str(t) for t in (target.tags or ())) + " " + str(target.name)
    return any(w in tags for w in words)


def _resolve_poison_on_hit(conn, combat: "Combat", target: "Combatant",
                           grade: str, roller) -> str:
    """吸血鬼尖牙附毒的结算（``full.txt:10148-10150`` + 毒素表 ``:15358-15367``）。

    毒素档位与 DV（``:15359-15367``）：
    轻 DV11 / 强 DV13 / 致命 DV15，失败都是**直接 HP 伤害**（护甲不削减）。
    「约定」：书里没写尖牙附哪一档，默认「强」（DV13，2D6）。
    """
    POISON_TABLE = {"轻": (11, "1D6"), "强": (13, "2D6"), "致命": (15, "3D6")}
    dv, dice_spec = POISON_TABLE.get(str(grade), POISON_TABLE["强"])
    if _is_mechanical(target):
        return f"  {target.name}不是血肉之躯，毒素无效。"
    stat_value = max(1, int(target.body))
    level = max(0, int(target.body) - 2)
    natural = roller.roll(10)
    total = natural + stat_value + level
    head = (f"  ☠ 附毒（{grade}）：{target.name} 过 DV{dv} 折磨&药物耐受 "
            f"1d10={natural} + WILL{stat_value} + 技能{level} = {total}")
    if total >= dv:
        return head + " → 抗住了。"
    damage, _sixes = _roll_dice_full(roller, dice_spec)
    _apply_damage_hp(combat, target, damage, [])
    return head + f" → 中毒！直接 {dice_spec}（不吃护甲）：{damage} 点。"


def _target_skill(c: "Combatant", skill: str) -> int:
    """目标在某个技能上的等价等级（NPC 没有完整技能表，用近似值）。"""
    name = str(skill or "")
    if "折磨" in name or "药物" in name:
        # 书里没给 NPC 的耐受等级；「约定」用 WILL 属性当技能值
        return max(0, int(c.body) - 2)
    if "赛博技术" in name:
        return max(0, int(c.dex) - 2)
    return max(0, int(c.dodge) // 2)


def _target_stat(c: "Combatant", skill: str) -> tuple[str, int]:
    """检定用的属性与值。"""
    name = str(skill or "")
    if "折磨" in name or "药物" in name:
        return "WILL", max(1, int(c.body))
    if "赛博技术" in name:
        return "TECH", max(1, int(c.dex))
    return "WILL", max(1, int(c.body))


def _resolve_post_hit(combat: "Combat", target: "Combatant", ammo_type: str,
                      roller, *, conn=None) -> str:
    """命中后的检定结算（``full.txt:26362-26510``）。

    返回给玩家看的多行文本。不扣护甲、不走严重伤势的连带伤害。
    """
    spec = ammo_effect.spec_for(ammo_type)
    if not spec:
        return "  （这个弹种没有配置效果）"
    lines: list[str] = []

    # ① 免疫：鼻滤器免毒气、防眩晕免闪光、硬化防护免 EMP
    category = ammo_effect.immunity_for(ammo_type)
    if category and target.side == "pc" and conn is not None:
        tcard = (roster.get(conn, combat.group_id, target.owner_qq)
                 or easy_roster.get(conn, combat.group_id, target.owner_qq))
        if tcard:
            from cyberpunk import immunity

            source = immunity.is_immune(tcard, category)
            if source:
                return f"  {source}：免疫{category}，{ammo_type}无效。"

    # ② 「打到肉的人」：纯机械目标不吃生物类效果
    if spec.get("requires_flesh") and _is_mechanical(target):
        return f"  {target.name}不是血肉之躯，{ammo_type}对它无效。"

    # ③ 检定
    skill = str(spec.get("check") or "")
    dv = int(spec.get("dv") or 13)
    stat, stat_value = _target_stat(target, skill)
    level = _target_skill(target, skill)
    natural = roller.roll(10)
    total = natural + stat_value + level
    ok = total >= dv      # 静态 DV 检定：达到即成功（与全引擎一致）
    lines.append(f"  {target.name} 过「{skill}」DV{dv}："
                 f"1d10={natural} + {stat}{stat_value} + 技能{level} = {total}"
                 f" → {'通过' if ok else '失败'}")

    if ok:
        lines.append("  扛住了，没有额外效果。")
        return "\n".join(lines)

    fail = spec.get("on_fail") or {}
    rounds = int(fail.get("rounds") or ammo_effect.ROUNDS_PER_MINUTE)

    # ⑤ 直接伤害（护甲不削减：书里「不会与之产生交互」）
    if fail.get("damage"):
        damage, _sixes = _roll_dice_full(roller, str(fail["damage"]))
        lines.append(f"  失败！直接 {fail['damage']}（不吃护甲）：{damage} 点。")
        _apply_damage_hp(combat, target, damage, lines)

    # ⑥ 状态：倒地失去意识
    if fail.get("status") == "unconscious":
        target.unconscious_until = max(
            int(getattr(target, "unconscious_until", 0) or 0),
            int(combat.round) + rounds - 1)
        lines.append(f"  失败！倒地并失去意识（约 {rounds} 轮 / 1 分钟）。")

    # ⑦ 严重伤势（书里明确「不会因严重伤势受到额外伤害」）
    for key in fail.get("injury") or ():
        if key in target.crits:
            continue
        target.crits = tuple(target.crits) + (str(key),)
        from cyberpunk import crit

        injury = crit.BY_KEY.get(key)
        label = (getattr(injury, "zh", None) or getattr(injury, "label", None)
                 or str(key))
        lines.append(f"  失败！获得严重伤势「{label}」（约 {rounds} 轮后自愈）。")

    # ⑧ EMP：赛博组件失效
    if fail.get("disable_cyberware"):
        count = int(fail["disable_cyberware"])
        lines.append(f"  失败！GM 选 {count} 件赛博组件失效 1 分钟"
                     f"（硬化防护可免疫）。")

    return "\n".join(lines)



#: 全自动命中数上限（书内页 173）：冲锋枪 3、突击步枪 4。表在地图模块里维护
AUTOFIRE_MAX_HITS = gridmap.AUTOFIRE_MAX_HITS
FALLBACK_SKILL = "手枪"
DEFAULT_SP = 4

#: 约定：认不出武器名时按手枪的距离表算 DV（书内页 173 的表是按武器分的，
#: 敌人 preset 只记了伤害骰；手枪 2D6 正好对上大多数杂兵的伤害串）
DEFAULT_WEAPON = "手枪"
#: 没有地图也没有场景距离时，不猜 DV，让 DM / 玩家给一个距离
RANGE_ASK = ("距离不明：这里没有地图数据，也没有场景距离。"
             "说一句「距离 20 米」告诉我在多远，或者让 DM 按场景估一下"
             "（这句话不算动作）。")
#: 瞄准的判定修正（书内页 174）
AIM_PENALTY = -8
#: 瞄准部位
AIM_PARTS = {"头": "head", "头部": "head", "腿": "leg", "腿部": "leg",
             "手": "hand", "手中物": "hand", "手里的东西": "hand"}


class CombatError(Exception):
    """战斗流程走不下去（没开打 / 不是你的回合 / 目标不存在…）。"""


@dataclass(slots=True)
class Combatant:
    group_id: str
    owner_qq: str
    name: str
    side: str          # "pc" | "enemy"
    initiative: int
    hp: int
    max_hp: int
    sp: int = 0
    sp_wear: int = 0
    damage: str = ""
    dead: bool = False
    #: 致命伤（HP≤0 还没死透）：仅 PC 侧用，每回合开始要过死亡豁免，失败才 dead
    #: （rules/red_core/raw/full.txt:15730-15745、15943-15949）
    mortal: bool = False
    #: 死亡豁免目标 = 体格 BODY；敌人取 enemies.json 的 stats.BODY，缺省 5
    body: int = 5
    #: 死亡豁免累积惩罚：每次判死 +1，稳定伤势恢复到 1 HP 后清零（full.txt:15943-15948）
    death_save_penalty: int = 0
    #: 严重伤势列表（CriticalInjury.key，如 "arm_lost"）。严重伤不可重复获得，
    #: 伤害骰出 ≥2 个 6 时触发，投 2D6 查身体/头部伤势表（full.txt:15743-15748）。
    crits: tuple[str, ...] = ()
    # 敌人面板 / 玩家从卡上读
    ref: int = 5
    dex: int = 5
    dodge: int = 5
    skill: int = 0
    #: 敌人的行为脚本（targets / flee_if / on_flee_fail / join_round）
    script: dict = field(default_factory=dict)
    #: 语义标签（改造、护甲、武器、备注），给 negate_if 之类做匹配
    tags: tuple[str, ...] = ()
    #: 重伤线；0 = 用 max_hp // 2
    wound: int = 0
    #: 已脱离战斗（逃跑成功），既不算活人也不算倒下
    escaped: bool = False
    #: campaign 演员表 id；空 = 杂兵，战斗结束不留档
    actor_id: str = ""
    #: 命格："" 普通；"cannot_die" 打不死，HP 归零强制退场
    fate: str = ""
    #: 掉到这个 HP 以下就抽身退场（0 = 不触发）
    withdraw_at: int = 0
    #: 网格位置（-1 = 没有地图 / 未落位）
    x: int = -1
    y: int = -1
    #: 没有地图时的距离（米）：encounter 的 range_m 或 DM 估的米数，0 = 没给
    range_m: int = 0
    #: 弹匣里的余弹。-1 = 还没开始计数（按满弹匣算）；全自动射击要 10 发，
    #: 所以 A3 先引入这个字段做门槛（full.txt:14802），单发计数留给 A4。
    ammo: int = -1
    #: 弹匣容量；0 = 按武器类别的常见值推（_MAG_DEFAULT）
    mag: int = 0
    #: 这一匣装的是什么弹（``AMMO_TYPES`` 的键，如「穿甲弹」）。
    #: 书里「你不能在一个弹匣里混合多种子弹种类」（full.txt:14731-14732），
    #: 所以弹种跟着弹匣走、记在人身上，装填时整匣换。
    ammo_type: str = AMMO_BASE
    #: 开打时的落位提示（不落库；只有 start 用）
    spawn: tuple[int, int] | None = None
    #: 先攻加值（职业能力「战斗意识 +4」/ 义体「克伦齐科夫 +2」这类），
    #: 在 start() 掷完先攻后加上。存成独立字段是为了让「先攻」可重复计算、
    #: 也方便后续做桑德维斯坦的「激活 +3」临时值。
    init_bonus: int = 0
    #: 桑德维斯坦激活到哪一轮为止（``combat.round <= cyber_active_until`` 就是生效中）。
    #: 书 `:9809-9810`「使用行动激活其之后，增加 3 点先攻，并持续 1 分钟」，
    #: 而 1 轮 ≈ 3 秒（`:11331`）→ 1 分钟 = 20 轮。
    cyber_active_until: int = 0
    #: 桑德维斯坦冷却到哪一轮才可再激活（`:9810`「拥有 1 小时冷却」→ 1200 轮）。
    cyber_cooldown_until: int = 0
    #: 麻醉弹等造成的「失去意识」到哪一轮为止（`:26470` 失去意识 1 分钟 = 20 轮）。
    unconscious_until: int = 0
    #: 痛觉编辑器：忽略严重伤势的负面影响（``full.txt:27821-27825``）。
    #: ``True`` 时所有由 ``crits`` 派生的惩罚/限制一律按 0/False 处理，
    #: 但 ``crits`` 列表本身仍保留（伤势还在，只是感觉不到）。
    pain_editor: bool = False

    @property
    def alive(self) -> bool:
        # 致命伤角色（HP≤0 但还没判死）仍留在先攻队列里，好让他们自己过死亡豁免
        return not self.dead and not self.escaped and (self.hp > 0 or self.mortal)

    @property
    def effective_sp(self) -> int:
        return max(0, self.sp - self.sp_wear)

    @property
    def crits_list(self) -> list["CriticalInjury"]:
        """严重伤势对象列表（按现有 crits 顺序）。

        痛觉编辑器（``:27821-27825``）把严重伤势的负面影响全部忽略，
        因此装了就返回空列表——所有派生惩罚/限制自动归零。
        """
        if self.pain_editor:
            return []
        from cyberpunk.crit import BY_KEY
        return [BY_KEY[key] for key in self.crits if key in BY_KEY]

    @property
    def action_penalty(self) -> int:
        """严重伤势「所有动作判定」累计惩罚（手指粉碎/脑损伤/断臂手类）。"""
        return sum(inj.action_penalty for inj in self.crits_list)

    @property
    def ranged_penalty(self) -> int:
        """严重伤势「远程攻击判定」累计惩罚（失去眼睛/眼睛受损）。"""
        return sum(inj.ranged_penalty for inj in self.crits_list)

    @property
    def melee_penalty(self) -> int:
        """严重伤势「近战攻击判定」累计惩罚（肌肉撕裂）。"""
        return sum(inj.melee_penalty for inj in self.crits_list)

    @property
    def move_penalty(self) -> int:
        """严重伤势累计移速惩罚（最低把 MOVE 扣到剩 1 格）。"""
        return sum(inj.move_penalty for inj in self.crits_list)

    @property
    def no_dodge(self) -> bool:
        """断腿：无法闪避（full.txt:15876）。"""
        return any(inj.no_dodge for inj in self.crits_list)

    @property
    def no_speak(self) -> bool:
        """气管破裂：无法说话（full.txt:15910 附近）。"""
        return any(inj.no_speak for inj in self.crits_list)

    @property
    def head_mult(self) -> int:
        """瞄准头部的伤害倍率：默认 2，颅骨骨裂后按伤势写成 3（full.txt:15904）。"""
        return max((inj.head_mult for inj in self.crits_list), default=0)

    @property
    def walk_damage(self) -> bool:
        """肋骨骨折/体内异物：徒步行进 >4m 的回合要再吃一次额外伤害。"""
        return any(inj.walk_damage for inj in self.crits_list)

    @property
    def no_move_next(self) -> bool:
        """耳朵受损：步行 >4m 之后的下个回合不能移动（full.txt:15912-15920）。"""
        return any(inj.no_move_next for inj in self.crits_list)

    def rounds_left(self, weapon: str | None = None) -> int:
        """弹匣里还剩几发。没开始计数（-1）按满弹匣算。"""
        cap = self.mag or _mag_of(weapon)
        return cap if self.ammo < 0 else max(0, min(self.ammo, cap))

    @property
    def wound_penalty(self) -> int:
        """HP 伤势 + 严重伤势「所有动作」判定的总合惩罚。"""
        base = 0
        if self.hp < 1:
            base = -4
        elif self.max_hp and self.hp * 2 <= self.max_hp:
            base = -2
        return base + self.action_penalty

    def line(self) -> str:
        sp = f" SP {self.effective_sp}" if self.effective_sp else ""
        return f"{self.name} {max(0, self.hp)}/{self.max_hp} HP{sp}"


FACTION_LABELS = {"pc": "玩家", "enemy": "敌人"}


def faction_label(combat: "Combat", side: str) -> str:
    """阵营显示名：card 自己指定的 label > 内置 > 原样。"""
    return combat.labels.get(side) or FACTION_LABELS.get(side) or side



def present(combat: "Combat", c: "Combatant") -> bool:
    """这个敌人此刻在不在场上（增援还没到 = 不在）。

    join_round：还没到轮次 = 场外。
    join_when：条件没满足 = 场外。例如「炮塔全毁时鲁思文才出来」
    （playbook_opera.txt:102「一旦炮塔被处理完毕…立即被…打断」）。
    """
    if c.side == "pc":
        return True
    script = c.script or {}
    try:
        join = int(script.get("join_round") or 1)
    except (TypeError, ValueError):
        join = 1
    if combat.round < join:
        return False
    return join_when_ok(combat, c)


def join_when_ok(combat: "Combat", c: "Combatant") -> bool:
    """join_when 条件是否已经满足（不满足 = 还在场外）。

    支持：{"all_dead": "自动炮塔"} —— 名字以它开头的敌人全部倒下/逃走后入场。
    没写 join_when 就一直满足。
    """
    spec = (c.script or {}).get("join_when")
    if not isinstance(spec, dict):
        return True
    target = str(spec.get("all_dead") or "").strip()
    if not target:
        return True
    guards = [x for x in combat.combatants
              if x is not c and x.side != "pc"
              and x.name.startswith(target)]
    if not guards:
        return True          # 压根没这一批：条件视为已满足
    return all((not x.alive) or x.escaped for x in guards)


def pending_join(combat: "Combat") -> list:
    """场外等着的增援（活着但还没入场）。"""
    return [c for c in combat.combatants
            if c.side != "pc" and c.alive and not present(combat, c)]

def foes(combat: "Combat", actor: "Combatant") -> list["Combatant"]:
    """actor 能打谁：按阵营关系表算，未声明的阵营默认敌视所有非本阵营。

    场外的增援不算——鲁思文没出场之前，玩家打不到他。
    """
    allowed = combat.factions.get(actor.side)
    out = []
    for c in combat.order:
        if c is actor or not c.alive or c.side == actor.side:
            continue
        if c.side != "pc" and not present(combat, c):
            continue
        if allowed is None or c.side in allowed:
            out.append(c)
    return out


def provoke(combat: "Combat", a: str, b: str) -> None:
    """一方动手就不再中立：双向登记敌意。"""
    if not a or not b or a == b:
        return
    combat.factions.setdefault(a, set()).add(b)
    combat.factions.setdefault(b, set()).add(a)


@dataclass(slots=True)
class Slot:
    """这一轮用掉的动作用量（书内页 168：1 移动 + 1 其他）。"""

    move: bool = False
    action: bool = False
    extra_move: bool = False
    #: 严重伤势「下个回合不能用其他动作」（脊柱受伤，full.txt:15856）：写回合号。
    #: 用回合号而不是布尔，是因为 reset() 每轮都会跑，布尔会被立刻清掉。
    no_action_round: int = 0
    #: 严重伤势「步行超过 4m 后下个回合无法移动」（耳朵受损，full.txt:15912）。
    no_move_round: int = 0
    #: 火力压制的受害者：下一个移动动作必须用来进掩体（full.txt:14911-14914）。
    #: 这个标记不随回合清零——书里说的是「下一个移动动作」，跨轮也要算，
    #: 直到他真的把移动动作用在进掩体上为止。
    must_cover: bool = False

    def reset(self) -> None:
        self.move = self.action = self.extra_move = False


@dataclass(slots=True)
class Combat:
    group_id: str
    campaign_id: int | None
    scene: str
    round: int
    turn_index: int
    active: bool
    combatants: list[Combatant] = field(default_factory=list)
    # 阵营关系：side(faction id) -> 它主动攻击的阵营集合；未声明的阵营 = 打所有非本阵营
    factions: dict[str, set[str]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    #: 环境/剧本修正（设计 §3），形如 {id,value,rounds,applies_to,negate_if,label,note}
    modifiers: list[dict] = field(default_factory=list)
    #: 网络战状态（阶段 3）：owner_qq -> {connected, controlled:[node_id]}，另存 flags
    net: dict = field(default_factory=dict)
    #: 场景地图 id；空 = 没有地图数据（距离改问 range_m / DM / 玩家）
    map_id: str = ""
    #: 掩体耐久：cover id -> 当前 hp；空 = 用地图文件里的初始值
    covers: dict = field(default_factory=dict)

    @property
    def order(self) -> list[Combatant]:
        """先攻队列（活人，含敌人），从大到小。"""
        return sorted([c for c in self.combatants if c.alive],
                      key=lambda c: (-c.initiative, c.owner_qq))

    @property
    def enemies(self) -> list[Combatant]:
        return [c for c in self.order if c.side != "pc"]

    @property
    def pcs(self) -> list[Combatant]:
        return [c for c in self.order if c.side == "pc"]

    @property
    def current(self) -> Combatant | None:
        order = self.order
        if not order:
            return None
        return order[self.turn_index % len(order)]


# 动作用量放内存（重启即重置；回合与血量在库里）
_SLOTS: dict[str, dict[str, Slot]] = {}


def _slots(combat: Combat) -> dict[str, Slot]:
    book = _SLOTS.setdefault(combat.group_id, {})
    for c in combat.combatants:
        book.setdefault(c.owner_qq, Slot())
    return book


def _reset_slots(combat: Combat) -> None:
    for slot in _slots(combat).values():
        slot.reset()


# ── 存取 ──────────────────────────────────────────────────────


def load(conn, group_id: str) -> Combat | None:
    row = conn.execute(
        "SELECT campaign_id, scene, round, turn_index, active, factions, labels, modifiers,"
        " net, map, covers FROM combats WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    combat = Combat(
        group_id=group_id,
        campaign_id=row["campaign_id"],
        scene=row["scene"] or "",
        round=int(row["round"]),
        turn_index=int(row["turn_index"]),
        active=bool(row["active"]),
    )
    try:
        raw_f = json.loads(row["factions"] or "{}")
        combat.factions = {str(k): set(v) for k, v in raw_f.items()}
        combat.labels = {str(k): str(v) for k, v in
                         (json.loads(row["labels"] or "{}") or {}).items()}
    except (ValueError, TypeError, KeyError):
        pass
    try:
        raw_m = json.loads(row["modifiers"] or "[]")
        combat.modifiers = [e for e in raw_m if isinstance(e, dict)]
    except (ValueError, TypeError):
        pass
    try:
        raw_n = json.loads(row["net"] or "{}")
        combat.net = raw_n if isinstance(raw_n, dict) else {}
    except (ValueError, TypeError):
        pass
    combat.map_id = str(row["map"] or "")
    try:
        raw_cov = json.loads(row["covers"] or "{}")
        combat.covers = {str(k): int(v) for k, v in raw_cov.items()} if isinstance(
            raw_cov, dict) else {}
    except (ValueError, TypeError):
        pass
    for crow in conn.execute(
        "SELECT owner_qq, name, side, initiative, hp, max_hp, sp, sp_wear, damage, dead,"
        " script, stat, escaped, actor_id, fate, withdraw_at, x, y FROM combatants"
        " WHERE group_id = ? ORDER BY initiative DESC, owner_qq",
        (group_id,),
    ).fetchall():
        base = str(crow["name"])
        preset = enemy_preset(base.rstrip("0123456789"))
        side = str(crow["side"] or "pc")
        try:
            raw_s = json.loads(crow["script"] or "{}")
            script = raw_s if isinstance(raw_s, dict) else {}
        except (ValueError, TypeError):
            script = {}
        c = (
            Combatant(
                group_id=group_id,
                owner_qq=str(crow["owner_qq"]),
                name=base,
                side=side,
                initiative=int(crow["initiative"]),
                hp=int(crow["hp"]),
                max_hp=int(crow["max_hp"]),
                sp=int(crow["sp"]),
                sp_wear=int(crow["sp_wear"]),
                damage=str(crow["damage"] or ""),
                dead=bool(crow["dead"]),
                ref=preset.ref if preset else 5,
                dex=preset.dex if preset else 5,
                dodge=preset.dodge if preset else 5,
                skill=preset.skill if preset else 5,
                body=preset.body if preset else 5,
                script=script,
                escaped=bool(crow["escaped"]),
                actor_id=str(crow["actor_id"] or ""),
                fate=str(crow["fate"] or ""),
                withdraw_at=int(crow["withdraw_at"] or 0),
                x=int(crow["x"] if crow["x"] is not None else -1),
                y=int(crow["y"] if crow["y"] is not None else -1),
                wound=preset.wound if preset else 0,
                tags=preset.tags if preset else (),
            )
        )
        try:
            raw_stat = json.loads(crow["stat"] or "{}")
        except (ValueError, TypeError):
            raw_stat = {}
        if isinstance(raw_stat, dict) and raw_stat:
            c.ref = int(raw_stat.get("ref", c.ref))
            c.dex = int(raw_stat.get("dex", c.dex))
            c.dodge = int(raw_stat.get("dodge", c.dodge))
            c.skill = int(raw_stat.get("skill", c.skill))
            c.wound = int(raw_stat.get("wound", c.wound))
            c.range_m = int(raw_stat.get("range_m", c.range_m))
            c.mortal = bool(raw_stat.get("mortal", c.mortal))
            c.body = int(raw_stat.get("body", c.body))
            c.death_save_penalty = int(raw_stat.get("death_save_penalty", c.death_save_penalty))
            tags = raw_stat.get("tags")
            if isinstance(tags, list):
                c.tags = tuple(str(t) for t in tags)
            crits = raw_stat.get("crits")
            if isinstance(crits, list):
                c.crits = tuple(str(t) for t in crits)
            c.ammo = int(raw_stat.get("ammo", c.ammo))
            c.mag = int(raw_stat.get("mag", c.mag))
            c.ammo_type = str(raw_stat.get("ammo_type") or c.ammo_type)
            c.init_bonus = int(raw_stat.get("init_bonus", c.init_bonus))
            c.cyber_active_until = int(
                raw_stat.get("cyber_active_until", c.cyber_active_until))
            c.cyber_cooldown_until = int(
                raw_stat.get("cyber_cooldown_until", c.cyber_cooldown_until))
            c.unconscious_until = int(
                raw_stat.get("unconscious_until", c.unconscious_until))
            c.pain_editor = bool(raw_stat.get("pain_editor", c.pain_editor))
        if c.side == "pc":
            card = roster.get(conn, group_id, c.owner_qq) or easy_roster.get(
                conn, group_id, c.owner_qq
            )
            if card:
                st = card.get("stats") or {}
                c.ref = int(st.get("REF", c.ref))
                c.dex = int(st.get("DEX", c.dex))
                c.dodge = int((card.get("skills") or {}).get("闪避", c.dodge))
                c.tags = _card_tags(card)
                c.pain_editor = cyber_bonus.has_cyberware(card, "痛觉编辑器")
        combat.combatants.append(c)
    return combat


def _stat_json(c: Combatant) -> str:
    """战斗员的运行时属性（gdata 覆盖过的，不能只靠 preset 还原）。"""
    return json.dumps({
        "ref": c.ref, "dex": c.dex, "dodge": c.dodge, "skill": c.skill,
        "wound": c.wound, "range_m": c.range_m, "tags": list(c.tags),
        "mortal": c.mortal, "body": c.body, "death_save_penalty": c.death_save_penalty,
        "crits": list(c.crits), "ammo": c.ammo, "mag": c.mag,
        "ammo_type": c.ammo_type, "init_bonus": c.init_bonus,
        "cyber_active_until": c.cyber_active_until,
        "cyber_cooldown_until": c.cyber_cooldown_until,
        "unconscious_until": c.unconscious_until,
        "pain_editor": c.pain_editor,
    }, ensure_ascii=False)


def _save(conn, combat: Combat) -> None:
    conn.execute(
        "UPDATE combats SET round = ?, turn_index = ?, active = ?, factions = ?,"
        " labels = ?, modifiers = ?, net = ?, map = ?, covers = ?, updated_at = ?"
        " WHERE group_id = ?",
        (combat.round, combat.turn_index, int(combat.active),
         json.dumps({k: sorted(v) for k, v in combat.factions.items()},
                    ensure_ascii=False),
         json.dumps(combat.labels, ensure_ascii=False),
         json.dumps(combat.modifiers, ensure_ascii=False),
         json.dumps(combat.net, ensure_ascii=False), combat.map_id,
         json.dumps(combat.covers, ensure_ascii=False), now(), combat.group_id),
    )
    for c in combat.combatants:
        conn.execute(
            "UPDATE combatants SET hp = ?, max_hp = ?, sp = ?, sp_wear = ?, dead = ?,"
            " initiative = ?, script = ?, damage = ?, stat = ?, escaped = ?, actor_id = ?,"
            " fate = ?, withdraw_at = ?, x = ?, y = ? WHERE group_id = ? AND owner_qq = ?",
            (c.hp, c.max_hp, c.sp, c.sp_wear, int(c.dead), c.initiative,
             json.dumps(c.script, ensure_ascii=False), c.damage, _stat_json(c),
             int(c.escaped),
             c.actor_id, c.fate, c.withdraw_at, c.x, c.y,
             combat.group_id, c.owner_qq),
        )
    conn.commit()


def _insert(conn, combat: Combat) -> None:
    conn.execute("DELETE FROM combatants WHERE group_id = ?", (combat.group_id,))
    conn.execute("DELETE FROM combats WHERE group_id = ?", (combat.group_id,))
    conn.execute(
        "INSERT INTO combats (group_id, campaign_id, scene, round, turn_index, active,"
        " factions, labels, modifiers, net, map, covers, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (combat.group_id, combat.campaign_id, combat.scene, combat.round,
         combat.turn_index, int(combat.active),
         json.dumps({k: sorted(v) for k, v in combat.factions.items()},
                    ensure_ascii=False),
         json.dumps(combat.labels, ensure_ascii=False),
         json.dumps(combat.modifiers, ensure_ascii=False),
         json.dumps(combat.net, ensure_ascii=False), combat.map_id,
         json.dumps(combat.covers, ensure_ascii=False), now(), now()),
    )
    for c in combat.combatants:
        conn.execute(
            "INSERT INTO combatants (group_id, owner_qq, name, side, initiative, hp,"
            " max_hp, sp, sp_wear, damage, dead, script, stat, escaped, actor_id, fate,"
            " withdraw_at, x, y)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (combat.group_id, c.owner_qq, c.name, c.side, c.initiative, c.hp,
             c.max_hp, c.sp, c.sp_wear, c.damage, int(c.dead),
             json.dumps(c.script, ensure_ascii=False), _stat_json(c),
             int(c.escaped),
             c.actor_id, c.fate, c.withdraw_at, c.x, c.y),
        )
    conn.commit()


# ── 地图 / 场景 ───────────────────────────────────────────────


def _scene_data(conn, group_id: str) -> dict:
    """当前场景的 JSON 段（没有就返回 {}）。"""
    row = conn.execute(
        "SELECT c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return {}
    scene = data.get("scenes", {}).get(str(row["scene"] or "")) or {}
    return scene if isinstance(scene, dict) else {}


def _map_for_id(map_id: str):
    if not map_id:
        return None
    try:
        return gridmap.load(str(map_id))
    except Exception:
        return None


def _map_for(combat: "Combat"):
    return _map_for_id(combat.map_id)


def _cover_hp(combat: "Combat", cover) -> int:
    return int(combat.covers.get(cover.id, cover.hp))


def _gone(combat: "Combat") -> set[str]:
    """这一场里已经打烂的掩体 id（耐久归零）。

    地图是静态的、还带 lru_cache，掩体耐久却按场次记在 `combat.covers` 里，
    所以视线/通行/遮挡都要用这个集合把「已经没了」的掩体排掉。
    规则依据：full.txt:15454-15458「耐久度归零的时候掩体就被彻底摧毁了」。
    """
    game = _map_for(combat)
    if game is None:
        return set()
    return {c.id for c in game.covers
            if int(combat.covers.get(c.id, c.hp)) <= 0}


_WEAPON_TABLE = ("手枪", "冲锋枪", "霰弹枪", "突击步枪", "狙击步枪", "弓", "榴弹发射器", "火箭筒")


def _weapon_of(c: "Combatant", skill_name: str = "") -> str | None:
    """从技能名/伤害串里认出武器表里的武器。"""
    if skill_name in _WEAPON_TABLE:
        return skill_name
    text = c.damage or ""
    for name in _WEAPON_TABLE:
        if name in text or any(name in str(tag) for tag in c.tags):
            return name
    return None


def _shot_dv(combat: "Combat", me: "Combatant", target: "Combatant", *,
             melee: bool, weapon: str | None,
             autofire: bool = False) -> tuple[int | None, str]:
    """有地图时按格算 DV / 视线 / 近战相邻；没地图返回 (None, "")。"""
    game = _map_for(combat)
    if game is None or me.x < 0 or me.y < 0 or target.x < 0 or target.y < 0:
        return None, ""
    here, there = (me.x, me.y), (target.x, target.y)
    if melee:
        if game.meters(here, there) > 2:
            return None, f"够不着：{me.name}和{target.name}隔着 {game.meters(here, there)} 米。"
        return None, ""
    blockers = game.blockers(here, there, _gone(combat))
    if blockers:
        return None, f"视线被{blockers[0]}挡住，这一枪打不出去。"
    dv = game.dv(here, there, weapon, autofire=autofire) if weapon else None
    if dv is None:
        return None, f"超出{weapon or '武器'}的射程（{game.meters(here, there)} 米），打不到。"
    return dv, ""


def _target_meters(combat: "Combat", me: "Combatant",
                   target: "Combatant") -> int | None:
    """这次攻击目标离射手多少米；没有可靠距离返回 None（书内页 173 只按距离算 DV）。"""
    game = _map_for(combat)
    if game is not None and me.x >= 0 and me.y >= 0 and target.x >= 0 and target.y >= 0:
        return game.meters((me.x, me.y), (target.x, target.y))
    if target.range_m > 0:
        return int(target.range_m)
    return None


def _range_dv(combat: "Combat", me: "Combatant", target: "Combatant", *,
              melee: bool, weapon: str | None,
              autofire: bool = False) -> tuple[int | None, str, str]:
    """这一枪的距离 DV。返回 (dv, 距离说明, 打不了的原因)。

    世界始终有场景，所以距离按优先级取：① 地图格子 ② 场景/遭遇写的
    `range_m` 或 DM 估的米数 ③ 都没有就**不猜**，返回 (None, "", RANGE_ASK)
    让 DM 或玩家先给距离（书内页 173 的 DV 只看距离，没有「中距默认值」）。

    `autofire=True` 时查的是全自动距离表（书内页 173 有独立一张表）。
    """
    game = _map_for(combat)
    if game is not None and me.x >= 0 and me.y >= 0 and target.x >= 0 and target.y >= 0:
        dv, problem = _shot_dv(combat, me, target, melee=melee, weapon=weapon,
                               autofire=autofire)
        meters = game.meters((me.x, me.y), (target.x, target.y))
        return dv, f"，{meters} 米", problem
    if melee:
        return None, "", ""
    if target.range_m > 0:
        name = weapon or DEFAULT_WEAPON
        dv = gridmap.dv_for_meters(name, target.range_m, autofire=autofire)
        if dv is None:
            return None, "", (f"超出{name}的射程（{target.range_m} 米），打不到。"
                              "换个目标，或者先靠近。")
        return dv, f"，{target.range_m} 米（估）", ""
    return None, "", RANGE_ASK


def close_range(conn, group_id: str, actor_qq: str, text: str) -> str | None:
    """玩家/ DM 用一句话给出距离（「距离 20 米」「大概三十米」）。

    世界始终有场景，所以没有地图数据时距离要么来自剧本的 `range_m`，
    要么由人给一个数。这里把数字写进所有还没定距离的参战者身上，
    返回一句确认；这句话不像在报距离就返回 None（交给别的分支处理）。
    """
    body = (text or "").strip()
    if not body:
        return None
    m = re.search(r"(?:距离|大概|大约|约|有)\s*(\d+)\s*(?:米|m|码)?", body)
    if m is None:
        m = re.search(r"(\d+)\s*(?:米|m)\b", body)
    if m is None:
        return None
    meters = int(m.group(1))
    if meters <= 0:
        return None
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    meters = min(meters, 800)
    for c in combat.combatants:
        c.range_m = meters
    _save(conn, combat)
    weapon = _weapon_of(next((c for c in combat.combatants
                             if c.owner_qq == actor_qq and c.side == "pc"), None)
                       or combat.combatants[0]) or DEFAULT_WEAPON
    dv = gridmap.dv_for_meters(weapon, meters)
    tail = f"，按{weapon}的距离表 DV {dv}" if dv is not None else \
        f"，{weapon}这个距离够不着"
    return f"📏 距离记成 {meters} 米{tail} 。"


def _spots_from(game, start: tuple[int, int] | None) -> list[tuple[int, int]]:
    """从 start 开始 BFS 所有可走格；start 无效则从第一个可走格开始。

    掩体格不算可走（`playable()`）；落位发生在开打瞬间，还没有掩体被打烂。
    """
    if start is None or not game.playable(start):
        start = None
        for y, row in enumerate(game.grid):
            for x, _ch in enumerate(row):
                if game.playable((x, y)):
                    start = (x, y)
                    break
            if start:
                break
    if start is None:
        return []
    seen = {start}
    queue = deque([start])
    out: list[tuple[int, int]] = []
    while queue:
        pos = queue.popleft()
        out.append(pos)
        for nxt in game.neighbors(pos):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return out


def _place_combatants(combat: "Combat", game) -> None:
    """按 spawns 给所有人落位；同阵营依次占最近的空格。"""
    taken: set[tuple[int, int]] = set()
    pcs = [c for c in combat.combatants if c.side == "pc"]
    enemies = [c for c in combat.combatants if c.side != "pc"]
    for c in pcs:
        _assign_spot(c, game.spawns.get("pc"), game, taken)
    for c in enemies:
        spot = c.spawn or game.spawns.get(c.side) or game.spawns.get("enemy")
        _assign_spot(c, spot, game, taken)


def _assign_spot(c: Combatant, start, game, taken: set[tuple[int, int]]) -> None:
    for pos in _spots_from(game, start):
        if pos not in taken:
            c.x, c.y = pos
            taken.add(pos)
            return
    c.x = c.y = -1


# ── 开打 ──────────────────────────────────────────────────────


def start(conn, group_id: str, *, campaign_id: int | None = None, scene: str = "",
          enemies: tuple[tuple[str, int], ...] = ((DEFAULT_ENEMY, 1),),
          modifiers: tuple[dict, ...] = (),
          roller: dice.Roller | None = None) -> Combat:
    roller = roller or dice.RandomRoller()
    cards = roster.members(conn, group_id)
    easy = easy_roster.members(conn, group_id)
    if not cards and not easy:
        raise CombatError("群里还没有角色卡，先让玩家认领/建卡。")

    combat = Combat(group_id=group_id, campaign_id=campaign_id, scene=scene,
                    round=1, turn_index=0, active=True,
                    modifiers=[dict(m) for m in modifiers if isinstance(m, dict)])

    for card in cards:
        rules = roster.to_character(card)
        stats = card.get("stats") or {}
        stored = (card.get("state") or {}).get("hp")
        hp = int(stored) if stored is not None else rules.max_hp
        skills = card.get("skills") or {}
        combat.combatants.append(Combatant(
            group_id=group_id,
            owner_qq=str(card.get("owner_qq", "")),
            name=str(card.get("name", "无名")),
            side="pc",
            initiative=0,
            hp=max(0, hp),
            # rules.max_hp / effective_stat 已含义体永久加成（骨肉织绕 BODY+2 等）
            max_hp=rules.max_hp,
            # 护甲 SP：穿着护甲 与 义体护甲（编织皮肤/皮下护甲）取高——RED 不叠加
            sp=max(rules.armor.sp or DEFAULT_SP, cyber_bonus.armor_sp(card)),
            ref=rules.effective_stat("REF"),
            dex=rules.effective_stat("DEX"),
            dodge=int(skills.get("闪避", 0)),
            body=rules.effective_stat("BODY"),
            tags=_card_tags(card),
        ))

    for card in easy:
        stats = card.get("stats") or {}
        hp = int(card.get("hp") or 30)
        skills = card.get("skills") or {}
        combat.combatants.append(Combatant(
            group_id=group_id,
            owner_qq=str(card.get("owner_qq", "")),
            name=str(card.get("name", "无名")),
            side="pc",
            initiative=0,
            hp=hp,
            max_hp=hp,
            sp=int(card.get("sp") or DEFAULT_SP),
            ref=int(stats.get("REF", 5)),
            dex=int(stats.get("DEX", 5)),
            dodge=int(skills.get("闪避", 0)),
            body=int(stats.get("BODY", 5)),
            tags=_card_tags(card),
        ))

    index = 0
    for group in enemies:
        if isinstance(group, dict):
            preset_name = str(group.get("name") or "")
            count = int(group.get("count") or 1)
            faction = str(group.get("faction") or "enemy")
            label = str(group.get("label") or "")
            hostile_to = tuple(str(x) for x in (group.get("hostile_to") or ()))
            script = dict(group.get("script") or {}) if isinstance(
                group.get("script"), dict) else {}
            actor = str(group.get("actor") or "")
            fate = str(group.get("fate") or "")
            withdraw_at = int(group.get("withdraw_at") or 0)
            gdata = group.get("data") if isinstance(group.get("data"), dict) else {}
            hp_override = group.get("hp")
            max_hp_override = int(group.get("max_hp") or 0)
            range_m = int(group.get("range_m") or 0)
            raw_spawn = group.get("spawn")
            spawn = (int(raw_spawn[0]), int(raw_spawn[1])) if (
                isinstance(raw_spawn, (list, tuple)) and len(raw_spawn) == 2) else None
        else:
            preset_name, count = str(group[0]), int(group[1])
            faction, label, hostile_to, script = "enemy", "", (), {}
            actor, fate, withdraw_at = "", "", 0
            gdata, hp_override, max_hp_override = {}, None, 0
            range_m = 0
            spawn = None
        if faction != "pc":
            combat.labels.setdefault(faction, label or faction)
            combat.factions.setdefault(faction, set()).update(hostile_to)
            for other in hostile_to:
                combat.factions.setdefault(other, set()).add(faction)
        preset = enemy_preset(preset_name)
        if preset is None:
            raise CombatError(
                f"没有这种敌人：{preset_name}（可选：{'、'.join(enemy_names())}）")
        for _ in range(max(1, count)):
            index += 1
            hp = preset.hp if hp_override is None else max(0, int(hp_override))
            combat.combatants.append(Combatant(
                group_id=group_id,
                owner_qq=f"enemy:{index}",
                name=f"{preset.name}{index}" if count > 1 else preset.name,
                side=faction,
                initiative=0,
                hp=hp,
                max_hp=max_hp_override or preset.hp,
                sp=int(gdata.get("sp", preset.sp)),
                damage=str(gdata.get("damage") or preset.damage),
                ref=int(gdata.get("ref", preset.ref)),
                dex=int(gdata.get("dex", preset.dex)),
                dodge=int(gdata.get("dodge", preset.dodge)),
                skill=int(gdata.get("skill", preset.skill)),
                body=int(gdata.get("body", preset.body)),
                script=dict(script),
                wound=preset.wound,
                tags=tuple(gdata.get("tags") or preset.tags),
                actor_id=(f"{actor}:{index}" if actor and count > 1 else actor),
                fate=fate,
                withdraw_at=withdraw_at,
                range_m=range_m,
                spawn=spawn,
            ))

    # 场景有地图就落位；没有就看 range_m（场景默认，敌人组可覆盖）
    scene_data = _scene_data(conn, group_id)
    enc_data = scene_data.get("encounter") if isinstance(scene_data.get("encounter"), dict) else {}
    scene_range = int(scene_data.get("range_m") or enc_data.get("range_m") or 0)
    if scene_range:
        for c in combat.combatants:
            if c.range_m <= 0:
                c.range_m = scene_range
    map_id = str(scene_data.get("map") or "")
    if map_id:
        game = _map_for_id(map_id)
        if game is not None:
            combat.map_id = map_id
            _place_combatants(combat, game)

    # 先攻 = 反应 + 1D10 + 加值；同值重掷（书内页 168）
    # 加值来源：简易卡职业能力「战斗意识 +4」（easy.ability_bonus(initiative=True)）
    # 与义体「克伦齐科夫 先攻 +2」（full.txt:9802-9805）。
    for c in combat.combatants:
        if c.side == "pc":
            bonus = 0
            pc_card = (easy_roster.get(conn, group_id, c.owner_qq)
                       or roster.get(conn, group_id, c.owner_qq))
            if pc_card:
                bonus, _ = easy_rules.ability_bonus(pc_card, initiative=True)
                bonus += cyber_bonus.initiative_bonus(pc_card)
            c.init_bonus = bonus
        c.initiative = roller.roll(10) + c.ref + c.init_bonus
    for _ in range(20):
        dup = _duplicates(combat.combatants)
        if not dup:
            break
        for c in dup:
            c.initiative = roller.roll(10) + c.ref + c.init_bonus

    _SLOTS.pop(group_id, None)
    combat.turn_index = 0
    _reset_slots(combat)
    _settle_head(conn, combat, roller)
    _insert(conn, combat)
    return combat


def _settle_head(conn, combat: "Combat", roller) -> None:
    """开局时队列头部可能是敌人：先把它们自动结算掉，直到轮到玩家。"""
    for _ in range(32):
        cur = combat.current
        if cur is None or not combat.active or cur.side == "pc":
            return
        _enemy_turn(conn, combat, cur, roller, [])
        _check_over(combat, [])
        if not combat.active:
            return
        order = combat.order
        if not order:
            return
        combat.turn_index += 1
        if combat.turn_index % len(order) == 0:
            combat.round += 1
            _reset_slots(combat)


def _duplicates(people: list[Combatant]) -> list[Combatant]:
    """返回所有参与并列的人（书：同值就再次掷骰，更高者在前）。"""
    counts: dict[int, int] = {}
    for c in people:
        counts[c.initiative] = counts.get(c.initiative, 0) + 1
    return [c for c in people if counts[c.initiative] > 1]


def _archive(conn, combat: Combat) -> None:
    """战斗结束前，把具名演员的结局写进 campaign_actors（杂兵不记）。"""
    if combat.campaign_id is None:
        return
    for c in combat.combatants:
        if c.side == "pc" or not c.actor_id:
            continue
        status = "dead" if c.dead else ("escaped" if c.escaped else "active")
        data = {"ref": c.ref, "dex": c.dex, "dodge": c.dodge, "skill": c.skill,
                "sp": c.sp, "damage": c.damage, "tags": list(c.tags),
                "max_hp": c.max_hp, "wound": c.wound}
        preset = c.name.rstrip("0123456789")
        conn.execute(
            "INSERT INTO campaign_actors (campaign_id, actor_id, name, kind, status,"
            " hp, max_hp, scene, side, preset, fate, withdraw_at, script, data,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, actor_id) DO UPDATE SET"
            " name=excluded.name, status=excluded.status, hp=excluded.hp,"
            " max_hp=excluded.max_hp, scene=excluded.scene, side=excluded.side,"
            " preset=excluded.preset, fate=excluded.fate,"
            " withdraw_at=excluded.withdraw_at, script=excluded.script,"
            " data=excluded.data, updated_at=excluded.updated_at",
            (combat.campaign_id, c.actor_id, c.name, "enemy", status, c.hp,
             c.max_hp, combat.scene, c.side, preset, c.fate, c.withdraw_at,
             json.dumps(c.script, ensure_ascii=False),
             json.dumps(data, ensure_ascii=False), now()),
        )


def actor_group(conn, campaign_id: int, actor_id: str, *,
                fresh: bool = False) -> dict | None:
    """把演员表里的一条记录变回 start() 能吃的敌人组。"""
    row = conn.execute(
        "SELECT * FROM campaign_actors WHERE campaign_id = ? AND actor_id = ?",
        (int(campaign_id), str(actor_id)),
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"] or "{}")
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    max_hp = int(row["max_hp"] or data.get("max_hp") or 1)
    hp = max_hp if fresh else int(row["hp"])
    try:
        script = json.loads(row["script"] or "{}")
    except (ValueError, TypeError):
        script = {}
    return {
        "name": str(row["preset"] or row["name"]),
        "count": 1,
        "faction": str(row["side"] or "enemy"),
        "label": str(row["name"]),
        "script": script if isinstance(script, dict) else {},
        "actor": str(row["actor_id"]),
        "fate": str(row["fate"] or ""),
        "withdraw_at": int(row["withdraw_at"] or 0),
        "hp": hp,
        "max_hp": max_hp,
        "data": data,
        "status": str(row["status"] or ""),
    }


def end(conn, group_id: str) -> None:
    fight = load(conn, group_id)
    if fight is not None:
        _archive(conn, fight)
    conn.execute("DELETE FROM combatants WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM combats WHERE group_id = ?", (group_id,))
    conn.commit()
    _SLOTS.pop(group_id, None)


def rest(conn, group_id: str) -> str:
    """战后休整：所有活着的 PC 回满 HP（规则书「接受医理并恢复状态」）。"""
    if active(conn, group_id) is not None:
        raise CombatError("战斗还没结束，先打完再休整。")
    healed: list[str] = []
    for card in roster.members(conn, group_id):
        rules = roster.to_character(card)
        state = dict(card.get("state") or {})
        hp = state.get("hp")
        if hp is not None and int(hp) <= 0:
            continue
        state["hp"] = rules.max_hp
        state["max_hp"] = rules.max_hp
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), now(), group_id,
             str(card.get("owner_qq", ""))),
        )
        healed.append(str(card.get("name") or "无名"))
    for card in easy_roster.members(conn, group_id):
        owner = str(card.get("owner_qq", ""))
        data = easy_roster.get(conn, group_id, owner) or {}
        hp = int(data.get("hp") or 0)
        if hp <= 0:
            continue
        max_hp = int(data.get("max_hp") or 0)
        if max_hp <= 0:
            tpl = easy_rules.get(str(card.get("template") or "")) or {}
            max_hp = int(tpl.get("hp") or hp or 30)
        data["hp"] = max_hp
        data["max_hp"] = max_hp
        conn.execute(
            "UPDATE easy_cards SET data = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(data, ensure_ascii=False), now(), group_id, owner),
        )
        healed.append(str(data.get("name") or card.get("name") or "无名"))
    conn.commit()
    if not healed:
        return "🩹 没有需要休整的人（或者大家都倒下了）。"
    return "🩹 休整完毕：" + "、".join(healed) + " 的 HP 已回满。"


def rest_night(conn, group_id: str) -> str:
    """过夜短休：按自然恢复回复一部分 HP（非「接受医理」的瞬时全回复）。

    RED 自然痊愈：稳定伤势成功后，休息一整天恢复 **体格** 点 HP
    （characters.py 条目 / full.txt 书 p222）。装「抗体增强」翻倍为 体格×2
    （full.txt:10220-10223）。致命伤（HP≤0，还没稳定）不能自愈，跳过。
    """
    if active(conn, group_id) is not None:
        raise CombatError("战斗还没结束，先打完再过夜。")
    healed: list[str] = []
    for card in roster.members(conn, group_id):
        rules = roster.to_character(card)
        state = dict(card.get("state") or {})
        hp = state.get("hp")
        if hp is None or int(hp) <= 0:
            continue  # 致命伤未稳定，不能自愈
        cur = int(hp)
        amount = rules.effective_stat("BODY") * cyber_bonus.rest_multiplier(card)
        new_hp = min(rules.max_hp, cur + amount)
        if new_hp == cur:
            continue
        state["hp"] = new_hp
        state["max_hp"] = rules.max_hp
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), now(), group_id,
             str(card.get("owner_qq", ""))),
        )
        healed.append(f"{card.get('name') or '无名'}+{new_hp - cur}")
    conn.commit()
    if not healed:
        return "🌙 过夜后没有可以自愈的人（致命伤需要先稳定伤势或就医）。"
    return "🌙 过了一夜（自然恢复）：" + "、".join(healed) + "。"


# ── 文本 ──────────────────────────────────────────────────────


def status_text(conn, group_id: str) -> str:
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return "现在没有在打。用「开打 打手 2」开始一场。"
    order = combat.order
    if not order:
        return "场上没有活人了。用「收队」清场。"
    cur = combat.current
    lines = [f"⚔ 第 {combat.round} 轮 · 先攻队列"]
    summary = modifier_summary(combat)
    if summary:
        lines.append(summary)
    for i, c in enumerate(order):
        mark = "▶" if cur is not None and c.owner_qq == cur.owner_qq else "·"
        who = faction_label(combat, c.side)
        lines.append(f"{mark} {i + 1}. {c.line()}〔{who}〕")
    if cur is not None:
        if cur.side == "pc":
            slot = _slots(combat).get(cur.owner_qq, Slot())
            acts = ("移动✅" if slot.move else "移动") + "/" + (
                "动作✅" if slot.action else "动作")
            lines.append(f"现在轮到：{cur.name}（{acts}）")
        else:
            lines.append(f"现在轮到：{cur.name}"
                         f"（{faction_label(combat, cur.side)}回合自动结算）")
    return "\n".join(lines)


def _move_cells(card) -> int:
    """一次移动动作的格数：MOVE×2 米，1 格 2 米 → 等于 MOVE（书内页 168）。"""
    stats = (card or {}).get("stats") or {}
    try:
        return max(1, int(stats.get("MOVE") or stats.get("移动") or 6))
    except (TypeError, ValueError):
        return 6


def _move_budget(me: "Combatant", card) -> int:
    """移动预算 = MOVE 格数 − 严重伤势的移速惩罚，最低剩 1 格。

    规则书：气胸移速 −2（full.txt:15770）、腿骨折 −4（:15840）、断腿 −6
    （:15876），三处都写明「最低剩余 1」。
    """
    return max(1, _move_cells(card) - me.move_penalty)


def _scene_cards(conn, group_id: str):
    """(owner_qq, card, is_easy)；同一人两张卡时以简易卡为准。"""
    out = []
    seen = set()
    for card in easy_roster.members(conn, group_id):
        owner = str(card.get("owner_qq") or "")
        seen.add(owner)
        out.append((owner, card, True))
    for card in roster.members(conn, group_id):
        owner = str(card.get("owner_qq") or "")
        if owner not in seen:
            out.append((owner, card, False))
    return out


def _saved_pos(card, map_id: str):
    state = card.get("state") or {}
    pos = state.get("pos") if isinstance(state, dict) else None
    if not isinstance(pos, dict) or str(pos.get("map") or "") != map_id:
        return None
    try:
        goal = pos.get("goal")
        return int(pos["x"]), int(pos["y"]), goal
    except (KeyError, TypeError, ValueError):
        return None


def _noncombat_positions(conn, group_id: str, game, map_id: str) -> dict[str, tuple[int, int]]:
    """非战斗时的 PC 坐标：卡里存过就用，否则从 spawns["pc"] 排最近的空格。"""
    cards = _scene_cards(conn, group_id)
    occupied: set[tuple[int, int]] = set()
    positions: dict[str, tuple[int, int]] = {}
    for owner, card, _ in cards:
        saved = _saved_pos(card, map_id)
        if saved is None:
            continue
        pos = (saved[0], saved[1])
        if game.playable(pos) and pos not in occupied:
            positions[owner] = pos
            occupied.add(pos)
    spawn = tuple(game.spawns.get("pc") or (0, 0))
    spots = _spots_from(game, spawn)
    for owner, card, _ in cards:
        if owner in positions:
            continue
        pick = next((p for p in spots if p not in occupied), None)
        if pick is None:
            continue
        positions[owner] = pick
        occupied.add(pick)
    return positions


def _persist_card(conn, group_id: str, user_id: str, card, is_easy: bool) -> None:
    """把卡写回（简易卡 / RED 卡各走各的存档）。"""
    if is_easy:
        easy_roster.save(conn, group_id, user_id, card, card.get("name"))
    else:
        roster.save(conn, group_id, user_id, card)


def _save_pos(conn, group_id: str, owner: str, card, is_easy: bool,
              map_id: str, pos: tuple[int, int],
              goal: tuple[int, int] | None) -> None:
    state = dict(card.get("state") or {})
    state["pos"] = {"map": map_id, "x": pos[0], "y": pos[1]}
    if goal is not None:
        state["pos"]["goal"] = [goal[0], goal[1]]
    if is_easy:
        easy_roster.set_state(conn, group_id, owner, state)
    else:
        roster.set_state(conn, group_id, owner, state)


def scene_move(conn, group_id: str, user_id: str, text: str) -> str | None:
    """非战斗移动：场景有地图时按 MOVE×2 米走；不是明确移动就返回 None。"""
    body = (text or "").strip()
    if not body:
        return None
    fight = load(conn, group_id)
    if fight is not None and fight.active:
        return None
    scene = _scene_data(conn, group_id)
    map_id = str(scene.get("map") or "")
    game = _map_for_id(map_id)
    if game is None:
        return None
    cards = _scene_cards(conn, group_id)
    mine = next(((card, easy) for owner, card, easy in cards
                 if owner == user_id), None)
    if mine is None:
        return None
    card, is_easy = mine
    positions = _noncombat_positions(conn, group_id, game, map_id)
    start = positions.get(user_id)
    if start is None:
        return None
    occupied = {p for owner, p in positions.items() if owner != user_id}

    # 飞爪手（非战斗，`:28348-28360`）：发射/收回抓钩；攀爬绳索免 ×2 惩罚。
    if "抓钩" in body or "飞爪" in body:
        if not cyber_bonus.has_grapple(card):
            return f"{card.get('name') or '你'} 身上没装飞爪手（赛博臂改造）。"
        gstate = dict(card.get("state") or {})
        if any(w in body for w in ("收回", "收起", "松开", "收绳")):
            if not gstate.get("grapple"):
                return "现在没有射出的抓钩。"
            gstate.pop("grapple", None)
            card["state"] = gstate
            _persist_card(conn, group_id, user_id, card, is_easy)
            return "🪝 收回抓钩（不花费动作，full.txt:28355-28356）。"
        target = None
        found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
        if found:
            target = (int(found.group(1)), int(found.group(2)))
        else:
            cover = next((c for c in game.covers
                          if (c.name in body or c.id in body) and c.alive), None)
            if cover is not None:
                target = (cover.x, cover.y)
        if target is None:
            names = "、".join(c.name for c in game.covers if c.alive)
            return (f"抓钩钉在哪？说掩体名（{names}）或坐标。"
                    f"只能在 {cyber_bonus.GRAPPLE_RANGE_M} 米内的厚实掩体上。")
        meters = game.meters(start, target)
        if meters > cyber_bonus.GRAPPLE_RANGE_M:
            return (f"{meters} 米太远了：飞爪手最远 "
                    f"{cyber_bonus.GRAPPLE_RANGE_M} 米（full.txt:28350）。")
        cover_here = game.cover_at(target)
        if cover_here is None or not cover_here.blocks_los:
            return "飞爪手只能固定在**厚实掩体**上（低矮的不行，full.txt:28350）。"
        gstate["grapple"] = {"map": map_id, "x": target[0], "y": target[1]}
        card["state"] = gstate
        _persist_card(conn, group_id, user_id, card, is_easy)
        return (f"🪝 发射抓钩，钉在 ({target[0]},{target[1]}) 的{cover_here.name}上。"
                f"之后「攀爬绳索」不受移速惩罚（full.txt:28354）。")

    goal: tuple[int, int] | None = None
    if "继续走" in body:
        saved = _saved_pos(card, map_id)
        if saved is not None and saved[2]:
            try:
                goal = (int(saved[2][0]), int(saved[2][1]))
            except (TypeError, ValueError, IndexError):
                goal = None
    if goal is None:
        found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
        if found:
            goal = (int(found.group(1)), int(found.group(2)))
        else:
            cover = next((c for c in game.covers
                          if c.name in body or c.id in body), None)
            if cover is not None:
                near = [p for p in game.neighbors((cover.x, cover.y))
                        if game.playable(p) and p not in occupied]
                goal = near[0] if near else None
    if goal is None:
        return None
    if not game.playable(goal):
        if game.walkable(goal):
            return (f"({goal[0]},{goal[1]}) 那格放着掩体，站不进去；"
                    "绕到旁边一格，或者先把掩体打烂。")
        return f"({goal[0]},{goal[1]}) 走不过去（{game.terrain_name(goal)}）。"

    budget = _move_cells(card)
    # 飞爪手（`:28354`）：有抓钩锚点又是在攀爬 → 无视攀爬移速惩罚（×1）。
    mode = ("climb" if any(w in body for w in _CLIMB_WORDS)
            else "jump" if any(w in body for w in _JUMP_WORDS)
            else "normal")
    mult = cyber_bonus.move_multiplier(card, mode=mode)
    if mode == "climb" and (card.get("state") or {}).get("grapple"):
        mult = 1
    water = cyber_bonus.water_cost(card)
    route, used, arrived = game.move(start, goal, budget, mult=mult, water_cost=water)
    if not route:
        return "找不到通往那里的路。"
    pos = route[-1]
    _save_pos(conn, group_id, user_id, card, is_easy, map_id, pos,
              None if arrived else goal)
    name = str(card.get("name") or "你")
    desc = game.describe(pos)
    if arrived:
        return (f"👟 {name} 走到 ({pos[0]},{pos[1]})，走了 {used} 格"
                f"（MOVE {budget} → 一次最多 {budget} 格 / {budget * 2} 米）。{desc}")
    return (f"👟 {name} 走到半路 ({pos[0]},{pos[1]})，走了 {used} 格；"
            f"一次移动最多 {budget} 格（MOVE×2 米）。说「继续走」接着走。")


def map_text(conn, group_id: str, user_qq: str = "") -> str:
    """当前场景/战斗的地图；没有地图就直说。"""
    combat = load(conn, group_id)
    if combat is not None and not combat.active:
        combat = None
    game = None
    scene = None
    if combat is not None and combat.map_id:
        game = _map_for(combat)
    if game is None:
        scene = _scene_data(conn, group_id)
        game = _map_for_id(str(scene.get("map") or ""))
    if game is None:
        return "这个场景没有地图，位置由 GM 叙述。"
    tokens: dict[tuple[int, int], str] = {}
    if combat is not None:
        for c in combat.combatants:
            if c.x < 0 or c.y < 0 or c.escaped:
                continue
            if c.side != "pc":
                tokens[(c.x, c.y)] = "E"
            elif user_qq and c.owner_qq == user_qq:
                tokens[(c.x, c.y)] = "@"
            else:
                tokens[(c.x, c.y)] = "A"
    else:
        map_id = str(scene.get("map") or "")
        for owner, pos in _noncombat_positions(conn, group_id, game, map_id).items():
            tokens[pos] = "@" if user_qq and owner == user_qq else "A"
    text = game.render(tokens, _gone(combat) if combat is not None else ())
    if combat is not None:
        text += "\n移动：说「移动到 5,6」或「躲到<掩体名>后面」；打掩体：「攻击 <掩体名>」。"
    else:
        text += "\n非战斗移动：说「移动到 5,6」或「躲到<掩体名>后面」；一次走 MOVE×2 米。"
    return text



def _roll_damage(roller, notation: str) -> int:
    from cyberpunk.damage import roll_notation

    return roll_notation(roller, (notation or "2D6").upper())


def _best_weapon_skill(card) -> str:
    if not card:
        return FALLBACK_SKILL
    skills = card.get("skills") or {}
    owned = [s for s in WEAPON_DAMAGE if s in skills]
    if not owned:
        return FALLBACK_SKILL
    return max(owned, key=lambda s: int(skills.get(s, 0)))


def _flatten(value) -> list[str]:
    """把卡上的一个字段摊成字符串列表（字符串 / 字典 / 列表都认）。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get("name") or "")]
    return [str(item.get("name") or "") if isinstance(item, dict) else str(item)
            for item in value]


def _card_tags(card) -> tuple[str, ...]:
    """把卡上的装备/改造/特长摊成标签，给修正的 negate_if 做语义匹配。

    RED 卡（``core/roster.get``）把义体存在 ``state["cyber"]``，**顶层没有
    ``cyber`` 字段**；简易卡则存在顶层 ``cyber``。两边都要摊进来，否则
    「装了水平阻尼降噪却不免疫尖叫」这种修正对 RED 卡永远失效。
    """
    if not card:
        return ()
    parts: list[str] = [str(card.get("name") or ""), str(card.get("template") or "")]
    for key in ("gear", "cyber", "weapons", "armor", "perks"):
        parts.extend(_flatten(card.get(key)))
    # RED 卡：义体在 state 里（安装后写的是 cyberware_hl 的中文名）
    state = card.get("state")
    if isinstance(state, dict):
        parts.extend(_flatten(state.get("cyber")))
        parts.extend(_flatten(state.get("gear")))
    return tuple(p for p in parts if p)


# ── 环境修正（战斗扩展设计 §3）────────────────────────────────


def _rounds_hit(spec, rnd: int) -> bool:
    """rounds：缺省/all = 每轮；int、[起, 止]、"1-3"、"1,2,3"。"""
    if spec is None or spec == "" or spec == "all":
        return True
    if isinstance(spec, bool):
        return bool(spec)
    if isinstance(spec, (int, float)):
        return int(spec) == rnd
    if isinstance(spec, str):
        text = spec.strip().replace("～", "-").replace("–", "-").replace("—", "-")
        if "," in text or "，" in text:
            nums = [int(x) for x in text.replace("，", ",").split(",")
                    if x.strip().lstrip("-").isdigit()]
            return rnd in nums
        if "-" in text:
            left, _, right = text.partition("-")
            if left.strip().isdigit() and right.strip().isdigit():
                return int(left) <= rnd <= int(right)
            return True
        return text.isdigit() and int(text) == rnd
    if isinstance(spec, (list, tuple)):
        nums = [int(x) for x in spec if str(x).strip().lstrip("-").isdigit()]
        if len(nums) == 2 and len(spec) == 2:
            return min(nums) <= rnd <= max(nums)
        return rnd in nums
    return True


def _applies(entry: dict, actor: Combatant | None) -> bool:
    scope = entry.get("applies_to", "all")
    if actor is None or scope in (None, "", "all", "*"):
        return True
    if isinstance(scope, str):
        return scope == actor.side
    return actor.side in {str(x) for x in scope}


def _scope_all(entry: dict) -> bool:
    return entry.get("applies_to", "all") in (None, "", "all", "*")


#: 同一件义体的不同写法：数据里叫「声音抑制器」（Sound Damper），剧本写「水平阻尼降噪」
NEGATE_ALIASES: dict[str, tuple[str, ...]] = {
    "水平阻尼降噪": ("水平阻尼降噪", "水平阻尼", "阻尼降噪", "声音抑制器"),
}


def _negate_candidates(token: str) -> tuple[str, ...]:
    """一个 ``negate_if`` 词元能对应的所有义体写法。

    两种写法都认（``cyberpunk/immunity.py``）：

    * 写**义体名**（``"水平阻尼降噪"``）→ 展开成它的全部别名；
    * 写**免疫类别**（``"闪光"``、``"毒气"``）→ 展开成所有提供该免疫的义体名。

    这样剧本作者不用记义体全名，写类别就够。
    """
    from cyberpunk import immunity

    name = str(token).strip()
    if not name:
        return ()
    # 别名表优先（历史数据用的就是这些写法）
    if name in NEGATE_ALIASES:
        return tuple(NEGATE_ALIASES[name])
    table_aliases = immunity.aliases()
    if name in table_aliases:
        return tuple([name, *table_aliases[name]])
    # 当类别用：找出所有免疫这个词的义体
    providers = [zh for zh, meta in immunity.items().items()
                 if name in (meta.get("immunes") or [])]
    if providers:
        extra: list[str] = []
        for zh in providers:
            extra.extend(table_aliases.get(zh, ()))
        return tuple([name, *providers, *extra])
    return (name,)


def _negated(entry: dict, actor: Combatant | None) -> bool:
    if actor is None:
        return False
    tokens = entry.get("negate_if") or []
    if isinstance(tokens, str):
        tokens = [tokens]
    tags = " ".join(actor.tags)
    for token in tokens:
        if any(alias in tags for alias in _negate_candidates(token)):
            return True
    return False


def modifiers_for(combat: Combat, actor: Combatant | None = None,
                  round: int | None = None) -> list[dict]:
    """当轮对 actor 生效的修正（已过回合区间、阵营范围与免疫判定）。"""
    rnd = combat.round if round is None else int(round)
    out: list[dict] = []
    for entry in combat.modifiers or []:
        if not isinstance(entry, dict):
            continue
        if not _rounds_hit(entry.get("rounds"), rnd):
            continue
        if not _applies(entry, actor):
            continue
        if _negated(entry, actor):
            continue
        out.append(entry)
    return out


def modifier_for(combat: Combat, actor: Combatant | None = None,
                 round: int | None = None) -> int:
    return sum(int(e.get("value") or 0) for e in modifiers_for(combat, actor, round))


def _fmt_mod(value: int) -> str:
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"−{abs(value)}"
    return "±0"


def _entry_label(entry: dict) -> str:
    name = str(entry.get("label") or entry.get("name") or entry.get("id") or "修正")
    return f"{name} {_fmt_mod(int(entry.get('value') or 0))}"


def modifier_text(combat: Combat, actor: Combatant | None = None,
                  round: int | None = None) -> str:
    entries = modifiers_for(combat, actor, round)
    if not entries:
        return ""
    return "（" + "、".join(_entry_label(e) for e in entries) + "）"


def modifier_summary(combat: Combat) -> str:
    """状态面板用：只列对所有人生效的修正。"""
    entries = [e for e in modifiers_for(combat) if _scope_all(e)]
    if not entries:
        return ""
    return "环境修正：" + "、".join(_entry_label(e) for e in entries)


def _find_target(combat: Combat, hint: str,
                 actor: Combatant | None = None) -> Combatant:
    pool = foes(combat, actor) if actor is not None else combat.enemies
    if not pool:
        raise CombatError("场上没有你能打的目标了。")
    hint = (hint or "").strip()
    if hint:
        for c in pool:
            if hint in c.name:
                return c
    return pool[0]


def _sync_pc_hp(conn, combat: Combat, c: Combatant) -> None:
    if c.side != "pc":
        return
    row = conn.execute(
        "SELECT state FROM characters WHERE group_id = ? AND owner_qq = ?",
        (combat.group_id, c.owner_qq),
    ).fetchone()
    if row is not None:
        state = json.loads(row["state"] or "{}")
        state["hp"] = c.hp
        state.setdefault("max_hp", c.max_hp)
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), now(), combat.group_id, c.owner_qq),
        )
    else:
        row = conn.execute(
            "SELECT data FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
            (combat.group_id, c.owner_qq),
        ).fetchone()
        if row is not None:
            data = json.loads(row["data"] or "{}")
            data["max_hp"] = int(data.get("max_hp") or c.max_hp)
            data["hp"] = c.hp
            conn.execute(
                "UPDATE easy_cards SET data = ?, updated_at = ?"
                " WHERE group_id = ? AND owner_qq = ?",
                (json.dumps(data, ensure_ascii=False), now(), combat.group_id,
                 c.owner_qq),
            )
    conn.commit()


# ── 回合推进 ──────────────────────────────────────────────────


def _death_check(combat: Combat, c: Combatant, roller, lines: list[str]) -> None:
    """致命伤角色轮到自己时先判死（full.txt:15943-15949）。

    - 掷 D10 == 10：自动失败，即死。
    - 投出 < 体格 - 已累积死亡豁免惩罚：撑住，恢复 1 HP 并失去意识，清零惩罚。
    - 否则：判死。
    死亡豁免只在轮到自己时投；敌人不留致命伤状态（直接 dead）。
    """
    if not c.mortal:
        return
    roll = roller.roll(10)
    lines.append(f"  ☠ {c.name} 每回合开始要过死亡豁免（D10 < {c.body}"
                 f"{'' if c.death_save_penalty==0 else ' -'+str(c.death_save_penalty)}）…掷出 {roll}。")
    if roll == 10 or roll >= c.body - c.death_save_penalty:
        c.dead = True
        c.mortal = False
        lines.append(f"  {c.name} 没撑住，死了。")
    else:
        c.mortal = False
        c.death_save_penalty = 0
        c.hp = 1
        lines.append(f"  {c.name} 硬撑了过来：恢复 1 HP，但失去意识，这一回合动不了。")
        # 失去意识 -> 本轮消耗掉动作，避免立刻又被放回行动
        slot = _slots(combat).get(c.owner_qq)
        if slot is not None:
            slot.action = True


def _announce_joins(combat: Combat, lines: list[str]) -> None:
    """刚刚入场的增援报一句——炮塔倒下、鲁思文走出来的那一刻。"""
    for c in combat.combatants:
        if c.side == "pc" or not c.alive:
            continue
        script = c.script or {}
        if script.get("_joined"):
            continue
        if not present(combat, c):
            continue
        # 只有**靠条件/轮次延迟入场**的才播报，开战就在场的不报
        if not (script.get("join_when") or script.get("join_round")):
            continue
        script["_joined"] = True
        c.script = script
        lines.append(f"⚑ {c.name} 登场了。")


def _check_over(combat: Combat, lines: list[str]) -> None:
    _announce_joins(combat, lines)
    # 敌人全灭，但还有增援在场外等着（join_round 没到 / 条件没满足）
    # → 不结束。原文里炮塔刚被处理完鲁思文就出现，中间没有重新部署。
    if not combat.enemies and pending_join(combat):
        lines.append("⚑ 场面安静了一瞬，但还没结束。")
        return
    if not combat.enemies:
        combat.active = False
        if any(c.escaped for c in combat.combatants):
            lines.append("⚑ 敌人跑光了，战斗结束。用「收队」清场。")
        else:
            lines.append("⚑ 敌人全倒下了，战斗结束。用「收队」清场。")
    elif not combat.pcs:
        combat.active = False
        lines.append("☠ 队伍全倒下了，战斗结束。")


def _advance(conn, combat: Combat, roller, lines: list[str]) -> None:
    """指针后移；走到敌人就自动结算，直到轮到活着的玩家或战斗结束。"""
    for _ in range(32):
        order = combat.order
        if not order or not combat.active:
            return
        combat.turn_index += 1
        if combat.turn_index % len(order) == 0:
            combat.round += 1
            _reset_slots(combat)
        order = combat.order
        if not order:
            return
        cur = order[combat.turn_index % len(order)]
        if cur.side == "pc":
            # 致命伤角色轮到自己：先判死（成功会恢复 1 HP 并消耗掉本回合动作）
            if cur.mortal:
                _death_check(combat, cur, roller, lines)
                if not combat.active:
                    return
            # 队列里有人倒下会让取模错位：如果又轮到一个动作已用掉的 PC，
            # 说明这一圈已经转完，另起一轮，别把上一轮的动作扣到新回合。
            slot = _slots(combat)[cur.owner_qq]
            if slot.action:
                combat.round += 1
                _reset_slots(combat)
            lines.append(f"▶ 轮到 {cur.name}。")
            return
        _enemy_turn(conn, combat, cur, roller, lines)
        _check_over(combat, lines)
        if not combat.active:
            return


# ── 敌人行为脚本（战斗扩展设计 §4）────────────────────────────


def maybe_serious_injury(combat: Combat, c: Combatant, sixes: int, *,
                         head: bool, roller, lines: list[str],
                         force: bool = False) -> None:
    """伤害骰出了两个及以上 6 → 造成一次严重伤势（full.txt:15743-15748）。

    - 投 2D6 查表（瞄准头部用头部表，否则身体表），直到得到一个目标目前没有的伤势。
    - 严重伤势额外造成 5 点伤害，不削护甲、不受部位倍率，穿甲与否都生效。
    - 已有该伤势就再掷，避免重复。
    - 严重伤势自带的死亡豁免惩罚 +1 并入 ``death_save_penalty``。
    - ``force=True``：致命伤角色再被近战/远程攻击打中时，**必**获得一个严重伤势
      （full.txt:15741-15742），不管骰子有没有出俩 6。
    """
    if not force and sixes < SERIOUS_TRIGGER_SIXES:
        return
    from cyberpunk import crit
    table = crit.table_for(head)
    # 排除已有伤势，避免重复获得（full.txt:15746-15748）
    have = set(c.crits)
    roll = roller.roll(6) + roller.roll(6)
    guard = 0
    inj = table.get(roll)
    while (inj is None or inj.key in have) and guard < 24:
        roll = roller.roll(6) + roller.roll(6)
        inj = table.get(roll)
        guard += 1
    if inj is None:
        return
    c.crits = c.crits + (inj.key,)
    # 痛觉编辑器（full.txt:27821-27825）「忽略严重伤势的负面影响」：
    # 伤势照记、5 点即时伤害照吃（那是受伤本身），但**持续惩罚与限制**全部跳过。
    if not c.pain_editor and inj.death_penalty:
        c.death_save_penalty += inj.death_penalty
    # 状态类伤势：写成「回合号」，因为 Slot.reset() 每轮清零布尔
    slot = _slots(combat)[c.owner_qq]
    if not c.pain_editor:
        if inj.no_action_next:
            slot.no_action_round = combat.round + 1
        if inj.no_move_next:
            slot.no_move_round = combat.round + 1
    # 5 点额外伤害：不削护甲、不受部位倍率
    extra = crit.SERIOUS_EXTRA_DAMAGE
    c.hp = max(0, c.hp - extra)
    lines.append(
        f"  💥 {c.name} 中了严重伤：{inj.zh}"
        f"（{inj.sk}）——额外 5 点伤害"
        + (f"，死亡豁免惩罚 +{inj.death_penalty}" if inj.death_penalty else "")
        + "。")
    lines.append(f"  {c.name}：{c.line()}")


def _apply_damage_hp(combat: Combat, c: Combatant, taken: int,
                     lines: list[str]) -> None:
    """扣血，并按命格决定「倒下 / 退场 / 打不死」。

    - 普通：HP 归零 → dead
    - withdraw_at：HP 掉到阈值以内 → escaped（不算赢，也不是尸体）
    - cannot_die：HP 归零也不死，改为退场，留着下次回来
    """
    c.hp = max(0, c.hp - max(0, int(taken)))
    if c.hp < 1:
        if c.fate == "cannot_die":
            c.hp = 1
            c.escaped = True
            lines.append(f"  {c.name} 没有倒下——他从包围里退了出去。")
        elif c.side == "pc":
            # 致命伤（full.txt:15730-15745）：HP≤0 只算致命伤，还没死。
            # 已致命再挨打 -> 死亡豁免更困难（惩罚 +1，严重伤势留给 A2）
            if c.mortal:
                c.death_save_penalty += 1
                lines.append(
                    f"  {c.name} 已经致命了，又挨了一下：死亡豁免惩罚 +1"
                    f"（当前 {c.death_save_penalty}）。")
            else:
                c.mortal = True
                lines.append(f"  {c.name} 受了致命伤：每回合开始要先过死亡豁免，不然就死了。")
        else:
            c.dead = True
    elif c.withdraw_at and c.hp <= c.withdraw_at:
        c.escaped = True
        lines.append(f"  {c.name} 伤到这个程度，抽身退出了战斗。")


def _kin(combat: Combat, enemy: Combatant, script: dict) -> list[Combatant]:
    """「其中一人」的范围：自己 + allies 里点名的阵营；没写就只算自己一派。

    含已倒下的同伴（any_critical 要能看见「有人被打死」），但不含已逃跑的。
    """
    sides = {str(x) for x in (script.get("allies") or [])} or {enemy.side}
    return [c for c in combat.combatants if not c.escaped and c.side in sides]


def _serious(c: Combatant) -> bool:
    if not c.alive or c.hp < 1:
        return False
    return c.hp <= (c.wound or max(1, c.max_hp // 2))


def _critical(c: Combatant) -> bool:
    return c.dead or c.hp < 1


def _should_flee(combat: Combat, enemy: Combatant, script: dict) -> bool:
    cond = script.get("flee_if") or {}
    if not isinstance(cond, dict) or not cond:
        return False
    if "round_gt" in cond and combat.round > int(cond["round_gt"]):
        return True
    if "round_gte" in cond and combat.round >= int(cond["round_gte"]):
        return True
    kin = _kin(combat, enemy, script)
    if cond.get("any_serious") and any(_serious(c) for c in kin):
        return True
    if cond.get("any_critical") and any(_critical(c) for c in kin):
        return True
    return False


def _flee(conn, combat: Combat, enemy: Combatant, script: dict, roller,
          lines: list[str]) -> bool:
    """完整回合 + 冲刺。跑不掉返回 False（交给 on_flee_fail）。"""
    lines.append(f"🏃 {enemy.name} 转身就跑（完整回合 + 冲刺）。")
    if script.get("flee_blocked"):
        lines.append("  但出口被堵死，冲不出去。")
        return False
    enemy.escaped = True
    lines.append(f"  {enemy.name} 脱离战斗。")
    return True


def _self_destruct(conn, combat: Combat, enemy: Combatant, script: dict, roller,
                   lines: list[str]) -> bool:
    """on_flee_fail：跑不掉就自爆。没有格子/地图，按同场全体结算。"""
    spec = (script.get("on_flee_fail") or {}).get("self_destruct")
    if not spec:
        return False
    if isinstance(spec, str):
        spec = {"weapon": spec, "damage": "6D6"}
    weapon = str(spec.get("weapon") or "手榴弹")
    notation = str(spec.get("damage") or "6D6").upper()
    note = str(spec.get("note") or "")
    damage = _roll_damage(roller, notation)
    tip = f"（{note}）" if note else ""
    lines.append(f"💥 {enemy.name} 拉响{weapon}{tip}：{notation} = {damage}。")
    enemy.hp = 0
    enemy.dead = True
    game = _map_for(combat)
    if game is not None and enemy.x >= 0 and enemy.y >= 0:
        victims = [c for c in combat.order
                   if c is not enemy and c.x >= 0 and c.y >= 0
                   and max(abs(c.x - enemy.x), abs(c.y - enemy.y)) <= 2]
        lines.append("  （手榴弹按中心 5×5 格结算，站在爆心 2 格内才会被炸到。）")
    else:
        victims = [x for x in combat.order if x is not enemy]
        lines.append("  （没有地图，按同场全体结算爆炸伤害。）")
    for c in victims:
        armor = c.effective_sp
        taken = max(0, damage - armor)
        if taken:
            c.sp_wear += 1
        _apply_damage_hp(combat, c, taken, lines)
        _sync_pc_hp(conn, combat, c)
        lines.append(f"  {c.name} 被炸：护甲挡 {min(damage, armor)}，掉 {taken} 血"
                     f"（{max(0, c.hp)}/{c.max_hp} HP）。")
    return True


def _pick_target(combat: Combat, enemy: Combatant, targets: list[Combatant],
                 script: dict, roller) -> Combatant:
    """targets：在 rounds 内按「同脚本敌人」的序号平摊阵营，之后回到默认。"""
    spec = script.get("targets") or {}
    if isinstance(spec, dict) and spec and _rounds_hit(
            spec.get("rounds"), combat.round):
        split = spec.get("split") or []
        if isinstance(split, str):
            split = [split]
        split = [str(x) for x in split]
        usable = [s for s in split if any(t.side == s for t in targets)]
        if usable:
            peers = [c for c in combat.order
                     if c.side != "pc" and c.script.get("targets") == spec]
            index = peers.index(enemy) if enemy in peers else 0
            side = usable[index % len(usable)]
            same = [t for t in targets if t.side == side]
            if same:
                return same[0] if len(same) == 1 else same[roller.roll(len(same)) - 1]
    game = _map_for(combat)
    if game is not None and enemy.x >= 0 and enemy.y >= 0:
        near = [t for t in targets if t.x >= 0 and t.y >= 0]
        if near:
            return min(near, key=lambda t: game.meters((enemy.x, enemy.y), (t.x, t.y)))
    return targets[0] if len(targets) == 1 else targets[roller.roll(len(targets)) - 1]


def _enemy_turn(conn, combat: Combat, enemy: Combatant, roller,
                lines: list[str]) -> None:
    script = enemy.script or {}
    if not present(combat, enemy):
        join = int(script.get("join_round") or 1)
        why = f"第 {join} 轮才加入" if combat.round < join else "还在等时机"
        lines.append(f"· {enemy.name} 还在场外（{why}）。")
        return
    if _should_flee(combat, enemy, script):
        if _flee(conn, combat, enemy, script, roller, lines):
            return
        if _self_destruct(conn, combat, enemy, script, roller, lines):
            return
        lines.append(f"· {enemy.name} 没跑掉，只能继续硬撑。")
    targets = foes(combat, enemy)
    if not targets:
        lines.append(f"· {enemy.name} 没有可打的目标。")
        return
    target = _pick_target(combat, enemy, targets, script, roller)
    game = _map_for(combat)
    dv: int | None = None
    distance = ""
    if game is not None and enemy.x >= 0 and target.x >= 0:
        here, there = (enemy.x, enemy.y), (target.x, target.y)
        weapon = _weapon_of(enemy) or DEFAULT_WEAPON
        shot = game.dv(here, there, weapon)
        gone = _gone(combat)
        blocked = game.blockers(here, there, gone)
        if blocked or shot is None:
            if "炮塔" in enemy.name:
                lines.append(f"· {enemy.name} 固定在原地，打不到 {target.name}。")
                return
            occupied = {(c.x, c.y) for c in combat.order if c is not enemy and c.x >= 0}
            spots = [p for p in game.neighbors(there, gone)
                     if game.playable(p, gone) and p not in occupied]
            route = []
            for spot in spots:
                candidate, _used, _arrived = game.move(here, spot, 6, gone)

                if candidate:
                    route = candidate
                    break
            if route:
                enemy.x, enemy.y = route[-1]
                why = f"视线被{blocked[0]}挡住" if blocked else "距离太远"
                lines.append(f"· {enemy.name} 移动接近 {target.name}（{why}，走了 {len(route) - 1} 格）。")
            else:
                lines.append(f"· {enemy.name} 找不到接近 {target.name} 的路。")
            return
        dv = shot
        distance = f"，{game.meters(here, there)} 米"
    else:
        # 没有地图数据也得有距离：场景 range_m / DM 估的米数，谁先给了就用谁的
        meters = enemy.range_m or target.range_m
        weapon = _weapon_of(enemy) or DEFAULT_WEAPON
        if meters > 0:
            dv = gridmap.dv_for_meters(weapon, meters)
            distance = f"，{meters} 米（估）"
            if dv is None:
                lines.append(f"· {enemy.name} 手里的{weapon}够不到 {target.name}"
                             f"（{meters} 米），先往前压。")
                return
        else:
            lines.append(f"· {enemy.name} 朝 {target.name} 压了过来"
                         "（距离还没定：DM 说一句米数，或者玩家说「距离 20 米」）。")
            return
    mod = modifier_for(combat, enemy)
    total = roller.roll(10) + enemy.ref + enemy.skill + mod + enemy.ranged_penalty
    lines.append(f"· {enemy.name} 行动：{total} vs DV {dv}{distance}"
                 f"{modifier_text(combat, enemy)}")
    if total < dv:
        lines.append(f"  没打中 {target.name}。")
        return
    damage, sixes = _roll_dice_full(roller, enemy.damage or "2D6")
    taken = max(0, damage - target.effective_sp)
    was_mortal = target.mortal and target.hp >= 1
    if taken:
        target.sp_wear += 1
    _apply_damage_hp(combat, target, taken, lines)
    lines.append(f"  命中 {target.name}：伤害 {damage}，护甲挡 {damage - taken}，"
                 f"掉 {taken} 血。")
    if sixes >= SERIOUS_TRIGGER_SIXES or (was_mortal and taken):
        maybe_serious_injury(combat, target, sixes, head=False,
                             roller=roller, lines=lines,
                             force=(was_mortal and taken))
    _sync_pc_hp(conn, combat, target)


# ── 攻击 ──────────────────────────────────────────────────────


def _autofire(conn, combat: "Combat", me: "Combatant", target: "Combatant", card,
              aim_part: str | None, weapon: str | None,
              roller: dice.Roller, slot) -> str:
    """全自动射击：一个动作 + 10 发子弹（full.txt:14800-14813）。

    判定用「自动武器」技能、查**全自动距离表**（不是普通距离表）；不能瞄准；
    目标 REF≥8 时可以选择闪避（用 DEX+闪避 对抗替代 DV，:14691-14700）。
    命中后先投 2D6 得伤害，再乘以「判定值 − DV」的差值，差值上限是武器标注
    的数字（冲锋枪 3、突击步枪 4，map.AUTOFIRE_MAX_HITS）；护甲照常减一次。
    两个伤害骰都是 6 时额外造成一次严重伤势（:14810）。
    """
    skill_name = "自动武器"
    weapon = weapon or _weapon_of(me, skill_name) or DEFAULT_WEAPON
    lines: list[str] = []

    # ① 前置门槛：都不消耗动作（子弹没打出去）
    if weapon not in AUTOFIRE_MAX_HITS:
        return (f"{weapon}没有全自动模式。能扫射的只有冲锋枪和突击步枪"
                "（full.txt:14800、:14816-14840）。")
    if aim_part:
        return "全自动射击不能瞄准（full.txt:14806）。想打准就用单发。"
    left = me.rounds_left(weapon)
    if left < AUTOFIRE_ROUNDS:
        return (f"弹匣里只剩 {left} 发，全自动一次要打光 {AUTOFIRE_ROUNDS} 发"
                "（full.txt:14800）。先装填，或者改用单发。")

    dv, distance, problem = _range_dv(combat, me, target, melee=False,
                                      weapon=weapon, autofire=True)
    if problem:
        if problem == RANGE_ASK:
            return problem
        lines.append(problem)
        slot.action = True
        _sync_pc_hp(conn, combat, me)
        _advance(conn, combat, roller, lines)
        _save(conn, combat)
        return "\n".join(lines)

    level = int((card.get("skills") or {}).get(skill_name, 0)) if card else 0
    total = roller.roll(10) + me.ref + level
    total += me.wound_penalty + me.ranged_penalty + modifier_for(combat, me)
    mod_note = modifier_text(combat, me)

    # ② 目标 REF≥8 可选闪避：用 DEX+闪避 对抗替代 DV
    if target.ref >= 8:
        threshold = roller.roll(10) + target.dex + target.dodge
        lines.append(f"{me.name} 用{weapon}扫射 {target.name}：{total} vs 闪避 "
                     f"{threshold}{distance}{mod_note}")
    else:
        threshold = dv
        lines.append(f"{me.name} 用{weapon}扫射 {target.name}：{total} vs DV "
                     f"{dv}{distance}{mod_note}")

    slot.action = True
    me.ammo = left - AUTOFIRE_ROUNDS
    lines.append(f"打光 {AUTOFIRE_ROUNDS} 发，弹匣剩 {me.ammo} 发。")

    # 平局守方胜（full.txt:14697），差值 0 也就没有伤害
    if total <= threshold:
        lines.append("没打中。")
        _sync_pc_hp(conn, combat, me)
        _advance(conn, combat, roller, lines)
        _save(conn, combat)
        return "\n".join(lines)

    margin = total - threshold
    hits = min(margin, AUTOFIRE_MAX_HITS[weapon])
    damage, sixes = _roll_dice_full(roller, WEAPON_DAMAGE.get(skill_name, "2D6"))
    raw = damage * hits
    sp = target.effective_sp
    taken = max(0, raw - sp)
    was_mortal = target.mortal and target.hp >= 1
    if taken:
        # 全自动也是远程射击，穿甲弹同样 1 → 2（full.txt:26730-26731）
        target.sp_wear += _armor_step(me, target, melee=False)

    _apply_damage_hp(combat, target, taken, lines)
    lines.append(f"命中：判定超出 {margin}，取 {hits} 发（{weapon}上限 "
                 f"{AUTOFIRE_MAX_HITS[weapon]}）→ {damage}×{hits}={raw}，"
                 f"护甲挡 {min(raw, sp)}，打掉 {taken} 血。")
    if sixes >= SERIOUS_TRIGGER_SIXES or (was_mortal and taken):
        maybe_serious_injury(combat, target, sixes, head=False, roller=roller,
                             lines=lines, force=(was_mortal and taken))
    lines.append(f"{target.name}：{target.line()}")

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 火力压制（full.txt:14903-14914）────────────────────────────
#
# 规则依据（rules/red_core/raw/full.txt:14903-14914）：
#   「当你进行火力压制时，需要花费一个动作和10 发子弹。如果你的弹匣中没有10
#    发子弹你将不能使用火力压制。任何一个站着的，未处于掩体内，距离你25 米/
#    码之内而且和你之间没有东西遮挡的人都必须投 1D10+ 专注力（技能）+ 意志力
#    （属性）与你的 1D10+ 反应+ 全自动射击进行对抗。对抗失败的人的下一个移动
#    动作必须用来转移到掩体内。如果移动动作不足以让Ta 躲进掩体，Ta 必须使用
#    跑这个其他动作来溜到掩体后面或者尽可能接近掩体。」
#
# 「约定」：
#   - 书里技能叫「全自动射击」，本仓库技能表叫「自动武器」（同 A3）。
#   - 「站着」按 alive 且没有 no_move 类伤势处理；「未处于掩体内」按掩体占格
#     语义判断（站在掩体格上才算躲进去了，见 map.GameMap.playable 的约定）。
#   - 「和你之间没有东西遮挡」用 map.blockers（墙 / 还立着的掩体）。
SUPPRESS_RANGE_M = 25


def _in_cover(game, x: int, y: int, gone=()) -> bool:
    """这个人算不算「在掩体里」。

    「约定」：本项目掩体占一格、人和子弹都穿不进去（见 `map.GameMap.playable`），
    所以站在掩体格上是非法状态，只能用「紧贴一个还立着的掩体（八向相邻）」来
    表达「躲进掩体」（full.txt:14909「未处于掩体内」、:15445-15450 掩体三定律）。
    """
    if game is None or x < 0 or y < 0:
        return False
    for c in game.covers:
        if c.id in (gone or ()) or not c.alive or not c.blocks_los:
            continue
        if max(abs(x - c.x), abs(y - c.y)) == 1:
            return True
    return False


def _card_of(conn, group_id: str, c: "Combatant") -> dict:
    """取这个人的角色卡（简易卡优先，再退回正式卡）；敌人没有卡就返回空 dict。"""
    if c.side != "pc":
        return {}
    return (easy_roster.get(conn, group_id, c.owner_qq)
            or roster.get(conn, group_id, c.owner_qq)
            or {})


def _skill_level(conn, group_id: str, c: "Combatant", skill: str) -> int:
    """角色卡上的技能等级；没卡/没这项技能就是 0。"""
    return int(((_card_of(conn, group_id, c).get("skills") or {}).get(skill) or 0))


def _stat_value(conn, group_id: str, c: "Combatant", stat: str) -> int:
    """角色卡上的属性值；没卡时用 `Combatant` 自己带的近似值兜底。"""
    card = _card_of(conn, group_id, c)
    stats = card.get("stats") or {}
    if stat in stats:
        return int(stats[stat] or 0)
    if card:
        return 0
    # 敌人：用战斗数据里最接近的字段（反应→ref、敏捷→dex、意志→skill 的常见做法）
    return {"REF": c.ref, "DEX": c.dex, "WILL": c.skill,
            "BODY": c.body}.get(stat, 0)


def suppress(conn, group_id: str, actor_qq: str, *,
             roller: dice.Roller | None = None,
             weapon: str | None = None) -> str:
    """火力压制：一个动作 + 10 发，逼 25 米内暴露的人下一个移动动作进掩体。"""
    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    card = (easy_roster.get(conn, group_id, actor_qq)
            or roster.get(conn, group_id, actor_qq))
    weapon = weapon or _weapon_of(me, "自动武器") or DEFAULT_WEAPON
    if weapon not in AUTOFIRE_MAX_HITS:
        return (f"{weapon}没有全自动模式，压制不了。能压制的只有冲锋枪和"
                "突击步枪（full.txt:14903-14914）。")
    left = me.rounds_left(weapon)
    if left < AUTOFIRE_ROUNDS:
        return (f"弹匣里只剩 {left} 发，火力压制一次要打光 {AUTOFIRE_ROUNDS} 发"
                "（full.txt:14903-14906）。先装填。")

    level = int(((card or {}).get("skills") or {}).get("自动武器", 0))
    total = roller.roll(10) + me.ref + level
    total += me.wound_penalty + me.ranged_penalty + modifier_for(combat, me)

    slot.action = True
    me.ammo = left - AUTOFIRE_ROUNDS
    lines = [f"🔫 {me.name} 用{weapon}压制火力：{total} vs 各人的 "
             f"专注力+意志力（full.txt:14903-14914）{modifier_text(combat, me)}"]
    lines.append(f"打光 {AUTOFIRE_ROUNDS} 发，弹匣剩 {me.ammo} 发。")

    game = _map_for(combat)
    gone = _gone(combat)
    caught = 0
    for c in combat.order:
        if c is me or not c.alive or c.side == me.side:
            continue
        # ① 距离 25 米内
        if game is not None and me.x >= 0 and c.x >= 0:
            meters = game.meters((me.x, me.y), (c.x, c.y))
            if meters > SUPPRESS_RANGE_M:
                continue
            # ② 中间没有东西遮挡
            blockers = game.blockers((me.x, me.y), (c.x, c.y), gone)
            if blockers:
                continue
            # ③ 未处于掩体内：本项目里掩体占格、人站不进去，所以「进了掩体」
            #    按「紧贴一个还立着的掩体」算（见 map.playable 的约定）
            if _in_cover(game, c.x, c.y, gone):
                continue
        else:
            meters = me.range_m or c.range_m
            if meters > SUPPRESS_RANGE_M:
                continue
        # ④ 对抗：1D10 + 专注 + 意志
        save = roller.roll(10) + _skill_level(conn, group_id, c, "专注") \
            + _stat_value(conn, group_id, c, "WILL")
        if save > total:
            lines.append(f"  {c.name} 顶住了压制（{save} vs {total}）。")
            continue
        caught += 1
        sl = _slots(combat)[c.owner_qq]
        sl.must_cover = True
        lines.append(f"  {c.name} 被压住了（{save} vs {total}）："
                     "下一个移动动作必须用来进掩体。")
    if not caught:
        lines.append("没人被压住。")

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


def attack(conn, group_id: str, actor_qq: str, target_hint: str = "", *,
           skill: str | None = None, aim: str | None = None,
           autofire: bool = False, weapon: str | None = None,
           shots: int | None = None,
           roller: dice.Roller | None = None) -> str:
    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")

    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")

    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")

    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    # 打掩体：有地图、点了掩体名字、又没点具体敌人。
    game = _map_for(combat)
    if game is not None and target_hint:
        cover = next(
            (c for c in game.covers
             if _cover_hp(combat, c) > 0 and (c.name in target_hint or c.id in target_hint)),
            None,
        )
        enemy_hit = any(c.name in target_hint for c in foes(combat, me))
        if cover is not None and not enemy_hit:
            lines = [f"🧱 {me.name} 打{cover.name}（{game.describe((cover.x, cover.y))}）。"]
            if me.x < 0 or me.y < 0:
                lines.append("你还没站到地图上。")
                return "\n".join(lines)
            cover_weapon = _weapon_of(me) or DEFAULT_WEAPON
            dv = game.dv((me.x, me.y), (cover.x, cover.y), cover_weapon)
            if dv is None:
                return (f"超出{cover_weapon}的射程：离{cover.name}有 "
                        f"{game.meters((me.x, me.y), (cover.x, cover.y))} 米，打不到。")
            # 弹药：打掩体也是开枪，一样要扣一发、一样会被空仓挡住（full.txt:14691-14700）
            empty = _out_of_ammo(me, cover_weapon)
            if empty:
                return empty
            _spend_round(me, cover_weapon)
            total = roller.roll(10) + me.ref + me.wound_penalty + modifier_for(combat, me)
            lines.append(f"判定：{total} vs DV {dv}{modifier_text(combat, me)}")
            lines.append(f"弹匣剩 {me.rounds_left(cover_weapon)} 发。")
            slot.action = True
            if total < dv:
                lines.append("没打中，子弹嵌进了旁边的墙。")
            else:
                # 掩体材质：钢制掩体不能被体格低于 10 或没有赛博臂的角色的
                # 武术/搏击攻击所损伤（full.txt:15462-15464，书内页 183）
                cover_skill = skill or _best_weapon_skill(roster.get(conn, group_id, actor_qq))
                if (cover.material == "钢"
                        and cover_skill in ("武术", "搏击")
                        and me.body < 10 and not _has_cyberarm(me)):
                    lines.append(f"  {cover.name}是钢制的，武术/搏击伤不了它"
                                 "（体格 ≥10 或要赛博臂，full.txt:15462-15464）。")
                else:
                    dmg = _roll_damage(roller, WEAPON_DAMAGE.get("手枪", "2D6"))
                    hp = max(0, _cover_hp(combat, cover) - dmg)
                    combat.covers[cover.id] = hp
                    lines.append(f"掩体掉了 {dmg} 点耐久，还剩 {hp}。"
                                 + ("掩体被打烂了。" if hp <= 0 else ""))
            _sync_pc_hp(conn, combat, me)
            _advance(conn, combat, roller, lines)
            _save(conn, combat)
            return "\n".join(lines)

    target = _find_target(combat, target_hint, me)
    provoke(combat, me.side, target.side)
    card = roster.get(conn, group_id, actor_qq)
    skill_name = skill or _best_weapon_skill(card)
    level = int((card.get("skills") or {}).get(skill_name, 0)) if card else 0
    melee = skill_name in MELEE_SKILLS
    aim_part = AIM_PARTS.get((aim or "").strip()) if aim else None
    penalty = AIM_PENALTY if aim_part else 0
    # 义体命中修正（B 批）：瞄准/远视目镜按「是否瞄准 + 目标距离」给命中加值，
    # 只对远程生效（近战不吃义眼改造）。combat_hit_note 用于回执说明。
    cyber_hit = 0
    cyber_hit_note = ""
    if card is not None and not melee:
        cyber_hit, c_notes = cyber_bonus.hit_bonus(
            card, aiming=bool(aim_part), meters=_target_meters(combat, me, target))
        if cyber_hit:
            cyber_hit_note = "（义体命中：" + "+".join(c_notes) + "）"

    # 全自动射击走独立结算（不同技能、不同 DV 表、伤害乘算），见 _autofire
    if autofire:
        return _autofire(conn, combat, me, target, card, aim_part,
                         weapon or _weapon_of(me, "自动武器"), roller, slot)

    lines: list[str] = []
    if aim_part:
        lines.append(f"🎯 {me.name} 瞄准{aim}：判定 −8、ROF 限 1。")

    dv, distance, problem = _range_dv(combat, me, target, melee=melee,
                                      weapon=_weapon_of(me, skill_name))
    if problem:
        if problem == RANGE_ASK:
            # 只是请 DM / 玩家先给个距离：不算动作，也不推进回合
            return problem
        lines.append(problem)
        slot.action = True
        _sync_pc_hp(conn, combat, me)
        _advance(conn, combat, roller, lines)
        _save(conn, combat)
        return "\n".join(lines)

    # 弹药：按一次攻击动作打一发算（full.txt:14691-14700 未给「未命中不耗弹」，
    # 所以命中与否都扣）。近战不耗弹；弓不用装填、也不耗弹（full.txt:14898-14899）。
    shoot_weapon = None
    if not melee:
        shoot_weapon = _weapon_of(me, skill_name) or DEFAULT_WEAPON
        empty = _out_of_ammo(me, shoot_weapon)
        if empty:
            return empty

    mod_note = modifier_text(combat, me)
    #: 特殊弹种的提示（基础弹药为空串）
    ammo_note = _ammo_note(me, melee=melee)
    # 「攻击不造成伤害、命中后改过检定」的弹种（full.txt:26362-26510）。
    # 注意：**命中判定照常做**——书里说的是「被你的攻击打到肉的人」要通过检定，
    # 所以先确认打中，再改走检定流程（早期版本直接跳过命中判定，是错的）。
    check_ammo = (not melee) and ammo_effect.is_check_ammo(me.ammo_type)
    if ammo_note and not check_ammo:
        lines.append(f"{me.name} 用的是{me.ammo_type}{ammo_note}")

    # 射速（ROF）：一次攻击动作可以打两下（full.txt:14477-14499）。
    # 「约定」：引擎默认仍只结算一发（`shots` 不给就是 1），玩家明确要
    # 「连打/双发/两下」时才按 ROF 连打——否则现有对话里每一枪都会变两下，
    # 老玩家的节奏全被打乱。瞄准射击把 ROF 限成 1（:14523）。
    rof = 1 if aim_part else _rof_of(shoot_weapon if not melee else skill_name,
                                     melee=melee)
    burst = 1
    if shots is not None:
        burst = max(1, min(int(shots), rof, ROF_CAP))
    if burst > 1:
        lines.append(f"🔫 {me.name} 用{skill_name}连打 {burst} 下"
                     f"（{skill_name}射速 {rof}，full.txt:14477-14499）。")

    slot.action = True
    hit_any = False
    for shot in range(burst):
        if shot:
            # 第二下要重新扣弹（一次判定一发，:14691-14700）
            if not melee:
                empty = _out_of_ammo(me, shoot_weapon)
                if empty:
                    lines.append(empty)
                    break
        if not melee:
            _spend_round(me, shoot_weapon)

        total = roller.roll(10) + (me.dex if melee else me.ref) + level + penalty
        # 严重伤势：近战/远程各有独立惩罚（肌肉撕裂 melee、失去眼睛 ranged），
        # 叠加在「所有动作」的 wound_penalty 之上（full.txt:15785-15930）
        total += me.wound_penalty + modifier_for(combat, me)
        total += me.melee_penalty if melee else me.ranged_penalty
        total += cyber_hit  # 义体命中修正（瞄准/远视目镜），B 批

        tag = f"（第 {shot + 1} 下）" if burst > 1 else ""
        if melee:
            # 断腿：无法闪避攻击（full.txt:15876）——防守方不再掷闪避骰，近战直接命中
            if target.no_dodge:
                defend = 0
                hit = True
                lines.append(f"  {target.name} 无法闪避（严重伤势：断腿），这一下躲不开。")
            else:
                defend = roller.roll(10) + target.dex + target.dodge
                hit = total > defend
            lines.append(f"{me.name} 用{skill_name}打 {target.name}{tag}："
                         f"{total} vs 闪避 {defend}{mod_note}")
        else:
            hit = total >= dv
            lines.append(f"{me.name} 用{skill_name}打 {target.name}{tag}："
                         f"{total} vs DV {dv}{distance}{mod_note}{cyber_hit_note}")

        if not hit:
            lines.append("没打中。")
            if shoot_weapon and shoot_weapon not in NO_RELOAD_WEAPONS:
                lines.append(f"弹匣剩 {me.rounds_left(shoot_weapon)} 发。")
            continue

        hit_any = True
        # 检定类弹种：命中后**不结算伤害**，改投一次检定（full.txt:26362-26510）。
        # 书里：「你的攻击不会造成任何伤害。任何被你的攻击打到肉的人必须通过
        # 一个 DVxx 的折磨&药物耐受检定」——所以这一步在命中之后、伤害之前。
        if check_ammo:
            lines.append(f"  {me.ammo_type}命中：不造成常规伤害，改过检定。")
            lines.append(_resolve_post_hit(combat, target, me.ammo_type, roller,
                                           conn=conn))
            if shoot_weapon and shoot_weapon not in NO_RELOAD_WEAPONS:
                lines.append(f"弹匣剩 {me.rounds_left(shoot_weapon)} 发。")
            continue
        # 义体武器覆盖：装了义体武器就用它的档位骰，不叠技能默认值
        # （档位见 full.txt:14976-15027；弹出式武器按中型 2D6 兜底）。
        cyber_dmg, cyber_weapon_note = (
            cyber_weapon.damage_override(card, skill=skill_name, weapon=weapon)
            if (card is not None and melee) else (None, "")
        )
        damage, sixes = _roll_dice_full(
            roller, cyber_dmg or WEAPON_DAMAGE.get(skill_name, "2D6"))
        if cyber_weapon_note:
            lines.append(f"  {cyber_weapon_note}：{damage} 点。")
        # 附毒：吸血鬼尖牙「可以对其附加毒素以及生物毒素」（full.txt:10148-10150）。
        # 走毒素表（:15359-15367）：轻 DV11 / 强 DV13 / 致命 DV15，直接 HP 伤害。
        poison_grade = (cyber_weapon.poison_of(card, weapon=weapon)
                        if (card is not None and melee) else "")
        poison_line = ""
        if poison_grade:
            poison_line = _resolve_poison_on_hit(
                conn, combat, target, poison_grade, roller)
        sp = (target.effective_sp + 1) // 2 if melee else target.effective_sp
        taken = max(0, damage - sp)
        was_mortal = target.mortal and target.hp >= 1
        # 橡胶弹：不造成严重伤势、不削减护甲、HP 不会被打到 1 以下（full.txt:26816-26823）
        rubber = (not melee) and me.ammo_type == AMMO_RUBBER
        if taken and not rubber:
            # 穿甲弹：护甲被穿透时 SP 减值 1 → 2（full.txt:26730-26731）
            target.sp_wear += _armor_step(me, target, melee=melee)
        # 附毒结算：用攻击后追加一段（和主伤害分开算，书里没说要替代伤害）
        if poison_line:
            lines.append(poison_line)
        if rubber and target.hp > 1:
            taken = min(taken, target.hp - 1)
        note = ""
        if aim_part == "head" and taken:
            # 默认头部瞄准 ×2；目标已有「颅骨骨裂」则倍数改成 3（full.txt:15904）
            mult = target.head_mult or 2
            taken *= mult
            note = f"打中头部：穿透护甲后伤害翻倍（×{mult}）。" if mult == 2 else \
                f"打中头部：颅骨骨裂，伤害×{mult}。"
        if aim_part == "leg" and taken:
            note = (note + " " if note else "") + "腿被打穿：断腿（严重伤势）。"
        if aim_part == "hand" and taken:
            note = (note + " " if note else "") + "手中的东西被打落。"

        _apply_damage_hp(combat, target, taken, lines)
        lines.append(f"命中：伤害 {damage}，护甲挡 {min(damage, sp)}，打掉 {taken} 血。")
        if note:
            lines.append(note)
        # 严重伤势：伤害骰出 ≥2 个 6，毫无例外（穿甲与否都触发）
        # 致命伤角色再挨打：不看出俩 6，强制受伤势（full.txt:15741-15742）
        # 橡胶弹不造成严重伤势（full.txt:26820「使用这种弹药造成的伤害不会造成严重伤势」）
        if not rubber and (sixes >= SERIOUS_TRIGGER_SIXES or (was_mortal and taken)):
            maybe_serious_injury(combat, target, sixes,
                                 head=(aim_part == "head"), roller=roller, lines=lines,
                                 force=(was_mortal and taken))
        lines.append(f"{target.name}：{target.line()}")
        if rubber:
            lines.append("（橡胶弹：不削减护甲、不会把人打到 1 HP 以下。）")
        if shoot_weapon and shoot_weapon not in NO_RELOAD_WEAPONS:
            lines.append(f"弹匣剩 {me.rounds_left(shoot_weapon)} 发。")
        if not target.alive:
            break

    if not hit_any and burst == 1:
        pass  # 单发失手：文案已在上面给过

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


_HACK_WORDS = ("黑入", "接管", "切断电缆", "断开电缆", "黑炮塔", "关掉炮塔")


def _scene_encounter(conn, group_id: str) -> dict:
    """当前场景的 encounter 段（没有就返回 {}）。"""
    row = conn.execute(
        "SELECT c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return {}
    scene = data.get("scenes", {}).get(str(row["scene"] or "")) or {}
    enc = scene.get("encounter")
    return enc if isinstance(enc, dict) else {}


def hack(conn, group_id: str, actor_qq: str, text: str, *,
         roller: dice.Roller | None = None) -> str | None:
    """场景 encounter.hack：花一个动作黑入/切断设备。不是黑入指令就返回 None。"""
    enc = _scene_encounter(conn, group_id)
    spec = enc.get("hack") if isinstance(enc, dict) else None
    if not isinstance(spec, dict):
        return None
    words = tuple(str(w) for w in (spec.get("keywords") or _HACK_WORDS))
    if not any(w and w in (text or "") for w in words):
        return None

    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    skill_name = str(spec.get("skill") or "电子/安全技术")
    dv = int(spec.get("dv") or 13)
    count = max(1, int(spec.get("count") or 1))
    target_word = str(spec.get("target") or "").strip()
    card = roster.get(conn, group_id, actor_qq) or {}
    easy_card = easy_roster.get(conn, group_id, actor_qq) or {}
    stats = dict(easy_card.get("stats") or card.get("stats") or {})
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    total = (roller.roll(10) + int(stats.get("TECH") or 5)
             + int(skills.get(skill_name) or 0)
             + me.wound_penalty + modifier_for(combat, me))
    slot.action = True
    lines = [f"🔌 {me.name} 黑入{(' ' + target_word) if target_word else ''}："
             f"{total} vs DV {dv}（{skill_name}）{modifier_text(combat, me)}"]
    if total < dv:
        lines.append("没接进去，端口把你这只手弹了出来。")
    else:
        targets = [c for c in combat.enemies
                   if target_word in c.name]
        targets.sort(key=lambda c: -c.initiative)
        stopped = 0
        for tgt in targets[:count]:
            tgt.hp = 0
            tgt.dead = True
            stopped += 1
            lines.append(f"链路被切断：{tgt.name} 熄火停机。")
        if not stopped:
            lines.append("端口是空的，没有能接管的设备。")
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 网络战：接入点 / 控制节点 / 黑冰（阶段 3）──────────────────

_NET_ACCESS_WORDS = ("接入", "接线", "插上", "找接入点", "连上", "接进网络")


def _net_validate(combat: Combat, actor_qq: str) -> Combatant:
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    if _slots(combat)[me.owner_qq].action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")
    return me


def _net_total(conn, group_id: str, combat: Combat, me: Combatant,
               skill_name: str, roller) -> int:
    card = roster.get(conn, group_id, me.owner_qq) or {}
    easy_card = easy_roster.get(conn, group_id, me.owner_qq) or {}
    stats = dict(easy_card.get("stats") or card.get("stats") or {})
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    return (roller.roll(10) + int(stats.get("TECH") or 5)
            + int(skills.get(skill_name) or 0)
            + me.wound_penalty + modifier_for(combat, me))


def _net_defense(conn, group_id: str, me: Combatant,
                 skill_name: str, roller) -> int:
    """黑冰攻击时的防守方：用交互界面代理（本引擎里是电子/安全技术等级）。"""
    card = roster.get(conn, group_id, me.owner_qq) or {}
    easy_card = easy_roster.get(conn, group_id, me.owner_qq) or {}
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    return roller.roll(10) + int(skills.get(skill_name) or 0)


def _net_effect(combat: Combat, effect: dict, lines: list[str]) -> None:
    kind = str((effect or {}).get("type") or "")
    if kind == "stop_turrets":
        target = str(effect.get("target") or "")
        count = max(1, int(effect.get("count") or 1))
        targets = [c for c in combat.enemies if target and target in c.name]
        targets.sort(key=lambda c: -c.initiative)
        if not targets:
            lines.append("节点是通的，但场上没有能被它停下的设备。")
        for tgt in targets[:count]:
            tgt.hp = 0
            tgt.dead = True
            lines.append(f"链路被切断：{tgt.name} 熄火停机。")
    elif kind in ("open_door", "flag"):
        flag = str(effect.get("flag") or ("door_open" if kind == "open_door" else "flag"))
        combat.net.setdefault("flags", {})[flag] = True
        lines.append(str(effect.get("text") or
                         ("门锁弹开，通往里面的路开了。" if kind == "open_door"
                          else "你把标记写进了网络。")))
    else:
        lines.append("节点响应了，但你看不出它能控制什么。")


def net_action(conn, group_id: str, actor_qq: str, text: str, *,
               roller: dice.Roller | None = None) -> str | None:
    """scene.encounter.net：先接入，再控制节点；不是网络动作返回 None。"""
    enc = _scene_encounter(conn, group_id)
    spec = enc.get("net") if isinstance(enc, dict) else None
    if not isinstance(spec, dict):
        return None
    body = (text or "").strip()
    if not body:
        return None

    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    me = _net_validate(combat, actor_qq)
    roller = roller or dice.RandomRoller()
    slot = _slots(combat)[me.owner_qq]
    access = spec.get("access") if isinstance(spec.get("access"), dict) else {}
    nodes = [n for n in (spec.get("nodes") or []) if isinstance(n, dict)]
    st = combat.net.setdefault(me.owner_qq, {"connected": False, "controlled": []})
    if not isinstance(st.get("controlled"), list):
        st["controlled"] = []

    acc_words = tuple(str(w) for w in (access.get("keywords") or _NET_ACCESS_WORDS))
    node_words = tuple(w for n in nodes for w in (n.get("keywords") or ()))
    if not st.get("connected"):
        wants_access = any(w and w in body for w in acc_words)
        wants_node = any(w and w in body for w in node_words) or "黑入" in body
        if not wants_access:
            if wants_node:
                return "你还没接入——先找接入点，说「接入」或「找接入点」。"
            return None
        skill_name = str(access.get("skill") or "电子/安全技术")
        dv = int(access.get("dv") or 8)
        # 网行必须靠交互接口（full.txt:871「插上交互接口」、:2173「用交互接口
        # 进入网络建筑」）。没装就插不进去——这是 C 类里唯一有硬规则的。
        net_card = (roster.get(conn, group_id, me.owner_qq)
                    or easy_roster.get(conn, group_id, me.owner_qq))
        if net_card is not None and not cyber_bonus.has_cyberware(net_card, "交互接口"):
            return (f"🔌 {me.name} 没有交互接口（Interface Plug），插不进网络——"
                    f"先装一个（full.txt:871、:2173）。")
        total = _net_total(conn, group_id, combat, me, skill_name, roller)
        slot.action = True
        name = str(access.get("name") or "接入点")
        lines = [f"🔌 {me.name} 接入 {name}：{total} vs DV {dv}（{skill_name}）"
                 f"{modifier_text(combat, me)}"]
        if total >= dv:
            st["connected"] = True
            lines.append("接入成功，控制节点在视野里亮了起来。")
        else:
            lines.append("端口把你这只手弹了出来，这次没接进去。")
    else:
        if any(w and w in body for w in acc_words):
            return "已经接入了，直接说「黑入 <节点>」。"
        node = next((n for n in nodes
                     if str(n.get("name") or "") in body or
                     any(w and w in body for w in (n.get("keywords") or ()))), None)
        if node is None:
            return None
        nid = str(node.get("id") or node.get("name") or "node")
        if nid in st["controlled"]:
            return f"「{node.get('name') or nid}」已经在你的控制下了，看还有没有别的节点。"
        skill_name = str(node.get("skill") or "电子/安全技术")
        dv = int(node.get("dv") or 8)
        total = _net_total(conn, group_id, combat, me, skill_name, roller)
        slot.action = True
        lines = [f"💻 {me.name} 黑入 {node.get('name') or nid}：{total} vs DV {dv}"
                 f"（{skill_name}）{modifier_text(combat, me)}"]
        if total >= dv:
            st["controlled"].append(nid)
            _net_effect(combat, node.get("effect") or {}, lines)
        else:
            ice = node.get("ice") if isinstance(node.get("ice"), dict) else {}
            if ice:
                ice_name = str(ice.get("name") or "黑冰")
                dmg_spec = str(ice.get("damage") or "2D6")
                atk = int(ice.get("atk") or 0)
                if atk > 0:
                    atk_total = roller.roll(10) + atk
                    def_total = _net_defense(conn, group_id, me, skill_name, roller)
                    lines.append(f"⚡ {ice_name}反扑：ATK {atk_total} vs 你的交互界面 {def_total}。")
                    if atk_total > def_total:
                        damage = _roll_damage(roller, dmg_spec)
                        _apply_damage_hp(combat, me, damage, lines)
                        lines.append(f"你被烧穿：{dmg_spec} = {damage} 点直接伤害（护甲不减）。")
                    else:
                        lines.append("你侧身让过链路，黑冰这一下没咬到你。")
                else:  # 旧数据没写 ATK：保留直接反噬
                    damage = _roll_damage(roller, dmg_spec)
                    _apply_damage_hp(combat, me, damage, lines)
                    lines.append(f"{ice_name}反噬：{dmg_spec} = {damage} 点直接伤害（护甲不减）。")
            else:
                lines.append("节点拒绝了你，链路断开，这次白费了动作。")

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 其他动作（书内页 168 的动作简表）──────────────────────────

_MOVE_WORDS = ("移动", "走位", "找掩体", "换位置")
#: 攀爬 / 跳跃是「移动方式」，每格花费 ×2（full.txt:14488）。
#: 装了攀爬脚/跳跃增强则各自降回 ×1（:10382 / :10388）。
_CLIMB_WORDS = ("攀爬", "翻越", "翻墙", "爬墙", "爬上去", "爬上", "攀上")
_JUMP_WORDS = ("跳跃", "跳到", "跳过去", "跳上", "跳下", "跳远", "助跑跳")
_RELOAD_WORDS = ("装填", "换弹", "换弹匣", "上弹")
_GETUP_WORDS = ("起身", "爬起来")
_DELAY_WORDS = ("延迟", "等一等", "过", "跳过", "结束回合")
_RUN_WORDS = ("跑", "冲刺")
#: 桑德维斯坦激活（full.txt:9807-9812）
_ACTIVATE_WORDS = ("激活桑德维斯坦", "开桑德维斯坦", "启动桑德维斯坦",
                   "启用桑德维斯坦", "激活加速", "开加速")


# ── 桑德维斯坦：动作激活的加速组件 ────────────────────────────
#
# 规则原文 ``full.txt:9807-9812``：
#   「加速组件。当使用行动激活其之后，增加 3 点先攻，并持续 1 分钟。
#     拥有 1 小时冷却。加速组件只能在身上安装其中一种。需要神经链路。」
#
# 时间换算 ``full.txt:11327-11331``：「每一个 TURNS（回合）大概有 3 秒钟…
# 一个 ROUND（轮次）也是差不多 3 秒钟」→ 1 分钟 = 20 轮，1 小时 = 1200 轮。
SANDY_INITIATIVE = 3
SANDY_DURATION_ROUNDS = 20      # 1 分钟 ÷ 3 秒
SANDY_COOLDOWN_ROUNDS = 1200    # 1 小时 ÷ 3 秒
SANDY_NAME = "桑德维斯坦"


def sandy_active(c: "Combatant", combat: "Combat") -> bool:
    """桑德维斯坦此刻是否生效。"""
    return int(c.cyber_active_until or 0) >= int(combat.round or 0) > 0


def sandy_on_cooldown(c: "Combatant", combat: "Combat") -> bool:
    return int(c.cyber_cooldown_until or 0) > int(combat.round or 0)


def has_sandy(conn, group_id: str, owner_qq: str) -> bool:
    """这张卡装没装桑德维斯坦。"""
    from cyberpunk import cyber_bonus

    card = roster.get(conn, group_id, owner_qq) or easy_roster.get(
        conn, group_id, owner_qq)
    return cyber_bonus.has_cyberware(card, SANDY_NAME) if card else False


def looks_like_activate(body: str) -> bool:
    text = (body or "").strip()
    return any(w in text for w in _ACTIVATE_WORDS)


def activate_sandevistan(combat: "Combat", me: "Combatant",
                         slot: "Slot") -> str:
    """花一个动作激活桑德维斯坦：先攻 +3、持续 20 轮、冷却 1200 轮。"""
    if sandy_active(me, combat):
        left = int(me.cyber_active_until) - int(combat.round) + 1
        return (f"{me.name} 的{SANDY_NAME}已经开着"
                f"（还剩约 {left} 轮，约 {left * 3} 秒）。")
    if sandy_on_cooldown(me, combat):
        left = int(me.cyber_cooldown_until) - int(combat.round)
        return (f"{me.name} 的{SANDY_NAME}还在冷却，"
                f"还要约 {left} 轮（约 {left * 3 // 60} 分钟）才能再开。")
    if slot.action:
        return "这回合的动作已经用过了。"

    slot.action = True
    me.cyber_active_until = int(combat.round) + SANDY_DURATION_ROUNDS - 1
    me.cyber_cooldown_until = int(combat.round) + SANDY_COOLDOWN_ROUNDS
    # 先攻是队列排序依据，立刻加，让本轮之后的排位都变
    me.initiative += SANDY_INITIATIVE
    me.init_bonus += SANDY_INITIATIVE
    return (f"⚡ {me.name} 激活了{SANDY_NAME}：先攻 +{SANDY_INITIATIVE}"
            f"（现为 {me.initiative}），持续 1 分钟（{SANDY_DURATION_ROUNDS} 轮），"
            f"冷却 1 小时。")


def _after_walk(me: "Combatant", used: int, slot: "Slot", combat: Combat,
                lines: list[str]) -> None:
    """徒步走完一段之后的伤势结算（两条都是「步行超过 4m」触发）。

    - 肋骨骨折/体内异物：每个徒步行进超过 4m 的回合，重新受到这个严重伤势的
      额外伤害，直接从生命值扣除（full.txt:15774、15816）。
    - 耳朵受损：步行超过 4m 后的下个回合无法进行移动动作（full.txt:15912-15920）。
    1 格 = 2 米，故超过 4 米即 used > 2 格。
    """
    if used <= 2:
        return
    if me.walk_damage:
        from cyberpunk.crit import SERIOUS_EXTRA_DAMAGE
        me.hp = max(0, me.hp - SERIOUS_EXTRA_DAMAGE)
        lines.append(f"  🩸 {me.name} 走了 {used} 格（{used * 2} 米）："
                     f"伤势发作，额外掉 {SERIOUS_EXTRA_DAMAGE} 点血。")
    if me.no_move_next:
        slot.no_move_round = combat.round + 1


def act(conn, group_id: str, user_id: str, text: str, *,
        roller: dice.Roller | None = None) -> str | None:
    """非攻击动作；这句话不是战斗动作就返回 None。"""
    body = (text or "").strip()
    if not body:
        return None
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    me = next((c for c in combat.combatants
               if c.owner_qq == user_id and c.side == "pc"), None)
    if me is None or not me.alive:
        return None
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        return None
    roller = roller or dice.RandomRoller()
    slot = _slots(combat)[me.owner_qq]
    lines: list[str] = []
    #: 「跑」拿到额外移动时要保留回合（不立刻推进），见 _RUN_WORDS 分支。
    keep_turn = False

    # 脊柱受伤：下个回合只能用移动动作（full.txt:15856）
    moving = (any(w in body for w in _MOVE_WORDS)
              or any(w in body for w in _CLIMB_WORDS)
              or any(w in body for w in _JUMP_WORDS)
              or (any(w in body for w in _RUN_WORDS) and body.startswith("跑")))
    if not moving and slot.no_action_round == combat.round:
        return f"{me.name} 脊柱受伤：这一回合用不了其他动作，只能移动。"

    # 桑德维斯坦激活（full.txt:9807-9812）：一个动作，先攻 +3 / 20 轮 / 冷却 1200 轮
    if looks_like_activate(body):
        if not has_sandy(conn, group_id, me.owner_qq):
            return (f"{me.name} 身上没装{SANDY_NAME}。"
                    f"（它需要神经链路，而且和克伦齐科夫互斥，只能装一种。）")
        line = activate_sandevistan(combat, me, slot)
        # 激活是「一个动作」——和其他动作一样要推进回合，
        # 否则激活完还能接着再动一次（早期版本漏了这句）。
        if slot.action and "已经开着" not in line and "冷却" not in line:
            _advance(conn, combat, dice.RandomRoller(), [])
        _save(conn, combat)
        return line

    if any(w in body for w in _RELOAD_WORDS):
        if slot.action:
            return "这回合的动作已经用过了。"
        # 先看手里到底是什么武器（damage/tags），没有再看技能对应的武器类别
        reload_weapon = (_weapon_of(me)
                         or _weapon_of(me, _best_weapon_skill(
                             roster.get(conn, group_id, user_id)))
                         or DEFAULT_WEAPON)
        if reload_weapon in NO_RELOAD_WEAPONS:
            return (f"{me.name} 用的是{reload_weapon}：拈弓搭箭就是攻击动作的一部分，"
                    "永远不用装填（full.txt:14898-14899）。")
        slot.action = True
        # 「用一种子弹种类完全重新装填」——补满，不是补几发（full.txt:14543-14545）。
        # 顺带可以换弹种：「装填 穿甲弹」整匣换成穿甲弹，不能混装（:14731-14732）。
        cap = me.mag or _mag_of(reload_weapon)
        before = me.rounds_left(reload_weapon)
        me.ammo = cap
        switched = ammo_type_from_text(body)
        if switched:
            before_kind = me.ammo_type
            me.ammo_type = switched
            lines.append(f"🔄 {me.name} 花一个动作把{reload_weapon}换成{switched}："
                         f"{before}/{cap} → {cap}/{cap}"
                         f"（弹种 {before_kind} → {switched}）。")
        else:
            lines.append(f"🔄 {me.name} 花一个动作装填{reload_weapon}："
                         f"{before}/{cap} → {cap}/{cap}。")
    elif any(w in body for w in _GETUP_WORDS):
        if slot.action:
            return "这回合的动作已经用过了。"
        slot.action = True
        lines.append(f"🧍 {me.name} 起身。")
    elif any(w in body for w in _DELAY_WORDS):
        slot.action = True
        # 延迟动作（full.txt:14430-14433）：把动作延迟到先攻队列更靠后位置，
        # 要选一件具体事件当触发时机（或指定先攻位置），并预先声明动作和目标。
        # 本引擎没有事件调度器，无法在「目标进入射程/敌人起身」那一刻自动触发，
        # 所以只做到「结束回合」，并诚实说明这一限制（见 docs/进度.md 的约定）。
        lines.append(f"⏸ {me.name} 结束回合，等下一个时机再出手。")
        lines.append("  ⚠️ 书里的「延迟动作」要指定触发事件+预告动作和目标"
                     "（full.txt:14430-14433），本引擎还没有事件调度，"
                     "只能靠你在下个时机自己再发指令。")
    elif any(w in body for w in _RUN_WORDS) and body.startswith("跑"):
        if not slot.move:
            return "要先用移动动作，才能「跑」再获得一次移动（书内页 168）。"
        if slot.extra_move:
            return "这回合已经跑过了。"
        slot.action = True
        slot.extra_move = True
        run_card = easy_roster.get(conn, group_id, me.owner_qq) or roster.get(
            conn, group_id, me.owner_qq)
        skates = cyber_bonus.run_bonus_cells(run_card) if run_card else 0
        lines.append(f"🏃 {me.name} 跑起来：本轮多一次移动。")
        if skates:
            lines.append(f"  轮滑脚：这次奔跑的移动 +{skates * 2} 米（+{skates} 格，"
                         "full.txt:10395）。")
        # 「跑」是给一次**额外移动**的动作（书内页 168）：动作已用，但回合
        # 不能马上推进，否则下一次移动会被算进下一轮、额外移动被 reset 清掉。
        # 这里先保留回合，等玩家用掉额外移动（移动分支结尾会 _advance）。
        keep_turn = True
    elif any(w in body for w in ("换臂", "换手臂", "换条手臂", "交换手臂")):
        # 快拆挂扣：一个动作换臂（取下当前 + 从快拆槽取另一条，合并成一个动作）
        if slot.action:
            return "这回合的动作已经用过了。"
        from core import cyber_flow
        reply = cyber_flow.swap_arm(conn, group_id, user_id, roller=roller)
        if "换好了" not in reply:
            return reply              # 失败不消耗动作
        slot.action = True
        lines.append(reply)
    elif "抓钩" in body or "飞爪" in body:
        card_now = (easy_roster.get(conn, group_id, me.owner_qq)
                    or roster.get(conn, group_id, me.owner_qq))
        if not (card_now and cyber_bonus.has_grapple(card_now)):
            return f"{me.name} 身上没装飞爪手（赛博臂改造，full.txt:28348）。"
        state_now = dict((card_now.get("state") or {}))
        if any(w in body for w in ("收回", "收起", "松开", "收绳")):
            # 收回绳索不花费动作（`:28355-28356`）。
            if not state_now.get("grapple"):
                return f"{me.name} 现在没有射出的抓钩。"
            state_now.pop("grapple", None)
            card_now["state"] = state_now
            if easy_roster.get(conn, group_id, me.owner_qq):
                easy_roster.save(conn, group_id, me.owner_qq, card_now, card_now.get("name"))
            else:
                roster.save(conn, group_id, me.owner_qq, card_now)
            lines.append(f"🪝 {me.name} 收回抓钩（不花费动作）。")
        else:
            # 发射抓钩 = 一个动作（`:28349`）。
            if slot.action:
                return "这回合的动作已经用过了。"
            game = _map_for(combat)
            if game is None:
                return f"{me.name} 这里没有网格地图，抓钩只能靠 GM 叙事判定。"
            target = None
            found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
            if found:
                target = (int(found.group(1)), int(found.group(2)))
            else:
                cover = next((c for c in game.covers
                              if (c.name in body or c.id in body)
                              and _cover_hp(combat, c) > 0), None)
                if cover is not None:
                    target = (cover.x, cover.y)
            if target is None:
                names = "、".join(c.name for c in game.covers
                                 if _cover_hp(combat, c) > 0)
                return (f"抓钩钉在哪？说掩体名（{names}）或坐标。"
                        f"只能在 {cyber_bonus.GRAPPLE_RANGE_M} 米内的厚实掩体上。")
            meters = game.meters((me.x, me.y), target)
            if meters > cyber_bonus.GRAPPLE_RANGE_M:
                return (f"{meters} 米太远了：飞爪手最远 "
                        f"{cyber_bonus.GRAPPLE_RANGE_M} 米（full.txt:28350）。")
            cover_here = game.cover_at(target, _gone(combat))
            if cover_here is None or not cover_here.blocks_los:
                return "飞爪手只能固定在**厚实掩体**上（低矮/已打烂的不行，full.txt:28350）。"
            slot.action = True
            state_now["grapple"] = {"map": combat.map_id, "x": target[0], "y": target[1]}
            card_now["state"] = state_now
            if easy_roster.get(conn, group_id, me.owner_qq):
                easy_roster.save(conn, group_id, me.owner_qq, card_now, card_now.get("name"))
            else:
                roster.save(conn, group_id, me.owner_qq, card_now)
            lines.append(f"🪝 {me.name} 花一个动作射出抓钩，钉在 ({target[0]},{target[1]})"
                         f" 的{cover_here.name}上。之后「攀爬绳索」不受移速惩罚"
                         f"（full.txt:28354）。")
            lines.append(f"  抓钩那侧手现在不能拿东西，也不能用它擒拿（full.txt:28359）。")
    elif any(w in body for w in _MOVE_WORDS + _CLIMB_WORDS + _JUMP_WORDS):
        # 「跑」给的额外移动允许第二次移动（书内页 168：1 移动 + 1 动作）
        if slot.move and not slot.extra_move:
            return "这回合的移动动作已经用过了。"
        # 耳朵受损：上个回合步行超过 4m → 这回合不能移动（full.txt:15912-15920）
        if slot.no_move_round == combat.round:
            return f"{me.name} 头晕目眩：这一回合做不了移动动作。"
        game = _map_for(combat)
        if game is None or me.x < 0 or me.y < 0:
            slot.move = True
            lines.append(f"👟 {me.name} 移动（没有地图，位置由 GM 叙述）。")
        else:
            goal: tuple[int, int] | None = None
            gone = _gone(combat)  # 已经打烂的掩体不再挡路（full.txt:15454-15458）
            found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
            if found:
                goal = (int(found.group(1)), int(found.group(2)))
            else:
                cover = next((c for c in game.covers
                              if _cover_hp(combat, c) > 0
                              and (c.name in body or c.id in body)), None)
                if cover is not None:
                    occupied = {(c.x, c.y) for c in combat.order if c is not me}
                    near = [p for p in game.neighbors((cover.x, cover.y), gone)
                            if game.playable(p, gone) and p not in occupied]
                    goal = near[0] if near else None
            if goal is None:
                names = "、".join(c.name for c in game.covers if _cover_hp(combat, c) > 0)
                return (f"移动到哪里？可以说坐标（例如「移动 5,6」），或躲到掩体旁边（{names}）。")
            # 被火力压制：下一个移动动作必须用来进掩体（full.txt:14911-14914）。
            # 「进掩体」在本项目 = 站到掩体旁边（掩体占格，站不进去，见 map.playable 约定）
            if slot.must_cover:
                # 低矮掩体挡不住子弹，也不算躲进掩体（full.txt:15530「0 = 不算掩体」）
                nearby = [c for c in game.covers
                          if _cover_hp(combat, c) > 0 and c.blocks_los
                          and max(abs(goal[0] - c.x), abs(goal[1] - c.y)) == 1]
                if not nearby:
                    names = "、".join(c.name for c in game.covers
                                      if _cover_hp(combat, c) > 0 and c.blocks_los)
                    return (f"{me.name} 被火力压着，这一个移动动作只能往掩体里钻"
                            f"（{names or '附近没有还能用的掩体'}）。"
                            "走不到就用「跑」（full.txt:14911-14914）。")
            if not game.playable(goal, gone):
                if game.walkable(goal):
                    return (f"({goal[0]},{goal[1]}) 那格立着掩体，人站不进去；"
                            "躲到它旁边，或者先把掩体打烂。")
                return f"({goal[0]},{goal[1]}) 走不过去（{game.terrain_name(goal)}）。"
            budget = _move_budget(me, easy_roster.get(conn, group_id, me.owner_qq)
                                 or roster.get(conn, group_id, me.owner_qq))
            move_card = easy_roster.get(conn, group_id, me.owner_qq) or roster.get(
                conn, group_id, me.owner_qq)
            # 移动方式（full.txt:14488）：攀爬 / 跳跃每格花费 ×2；
            # 攀爬脚（:10382）/ 跳跃增强（:10388）各自豁免回 ×1。
            mode = ("climb" if any(w in body for w in _CLIMB_WORDS)
                    else "jump" if any(w in body for w in _JUMP_WORDS)
                    else "normal")
            mult = cyber_bonus.move_multiplier(move_card or {}, mode=mode)
            # 飞爪手（`:28354`）：攀爬抓钩绳索时无视攀爬移速惩罚 → ×1。
            grapple_note = ""
            if (mode == "climb" and move_card is not None
                    and (move_card.get("state") or {}).get("grapple")):
                mult = 1
                grapple_note = "（飞爪手绳索：攀爬免移速惩罚）"
            # 蹼足（:10409）：水格按 1 格算，否则默认 2（游泳 ×2）。
            water = cyber_bonus.water_cost(move_card or {})
            # 轮滑脚（:10395）：跑之后的额外移动 +3 格（6 米）。
            if slot.extra_move and move_card is not None:
                budget += cyber_bonus.run_bonus_cells(move_card)
            route, used, arrived = game.move((me.x, me.y), goal, budget, gone,
                                             mult=mult, water_cost=water)
            if not route:
                return "找不到通往那里的路。"
            me.x, me.y = route[-1]
            slot.move = True
            walked = len(route) - 1
            tail = "" if arrived else "（移动力用完，只走到半路）"
            mode_note = {"climb": "〔攀爬〕", "jump": "〔跳跃〕"}.get(mode, "")
            cost_note = f"（攀爬/跳跃按 ×{mult} 计，消耗 {used}）" if mult > 1 else ""
            lines.append(f"👟 {me.name}{mode_note} 移动到 ({me.x},{me.y})，走了 {walked} 格{tail}{cost_note}。"
                         f"{game.describe((me.x, me.y), gone)}（MOVE {budget} → 一次最多 {budget * 2} 米）")
            if grapple_note:
                lines.append(f"  {grapple_note}")
            if slot.must_cover:
                slot.must_cover = False
                lines.append("  掩体挡住了射界，压制解除（full.txt:14911-14914）。")
            _after_walk(me, walked, slot, combat, lines)
    else:
        return None

    if slot.action and not keep_turn:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 投掷爆炸物（手榴弹；书内页 151「投掷」、153「爆炸物」）────────
#
# 规则依据（rules/red_core/raw/full.txt）：
#   :15074-15082 投掷物体＝「其他动作」做远程攻击，DEX+运动+1D10，射程 25 米，
#                距离 DV 等同榴弹发射器；目标 REF≥8 可闪避；伤害由 GM 定。
#   :14873-14892 高爆武器对 10×10 米（5×5 格）内所有目标结算，伤害骰只投一次；
#                判定不足 DV 时落点由 GM 在 10×10 米内偏移；足够抵挡的掩体
#                后面的目标不受伤害，掩体被打烂则吃满伤害。
#   :14760-14766 榴弹发射器距离 DV：16/15/15/17/20/22/25。
#   :14667-14670 手榴弹伤害 6D6。
GRENADE_WORDS = ("手榴弹", "手雷")
GRENADE_RANGE_M = 25
GRENADE_DAMAGE = "6D6"
GRENADE_WEAPON = "榴弹发射器"
BLAST_CELLS = 2  # 5×5 格
_SCATTER_DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1),
                 (-1, 0), (-1, -1), (0, -1), (1, -1))


def looks_like_grenade(text: str) -> bool:
    return any(w in (text or "") for w in GRENADE_WORDS)


def grenades(card) -> list[str]:
    """卡上带着的爆炸物（名称含「手榴弹 / 手雷」）。"""
    gear = list((card or {}).get("gear") or [])
    return [g for g in gear if "手榴弹" in g or "手雷" in g]


def _spend_gear(conn, group_id: str, owner_qq: str, item: str) -> bool:
    return (easy_roster.remove_gear(conn, group_id, owner_qq, item)
            or roster.remove_gear(conn, group_id, owner_qq, item))


def throw_grenade(conn, group_id: str, actor_qq: str, text: str = "", *,
                  roller: dice.Roller | None = None) -> str:
    """扔手榴弹：DEX+运动 对抗距离 DV，爆心 5×5 格、伤害骰只投一次。"""
    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    card = (easy_roster.get(conn, group_id, actor_qq)
            or roster.get(conn, group_id, actor_qq))
    held = grenades(card)
    if not held:
        return (f"🧨 {me.name} 身上没有手榴弹（装备里没有「手榴弹 / 手雷」）。"
                "可以先用「装备 手榴弹」补上。")
    item = held[0]

    target = _find_target(combat, target_from_text(combat, text, me), me)
    game = _map_for(combat)
    on_map = game is not None and me.x >= 0 and me.y >= 0
    here = (me.x, me.y) if on_map else None
    there = (target.x, target.y) if target.x >= 0 and target.y >= 0 else None

    lines = [f"🧨 {me.name} 朝 {target.name} 扔出{item}："
             f"敏捷(DEX)+运动+1D10，射程 {GRENADE_RANGE_M} 米。"]
    dv: int | None = None
    if on_map and here and there:
        meters = game.meters(here, there)
        if meters > GRENADE_RANGE_M:
            return (f"太远了：{me.name}到{target.name}有 {meters} 米，"
                    f"手榴弹最多扔 {GRENADE_RANGE_M} 米。")
        walls = [b for b in game.blockers(here, there) if b.startswith("墙")]
        if walls:
            return f"{walls[0]}挡在中间，手榴弹扔不过去。"
        dv = game.dv(here, there, GRENADE_WEAPON)
        if dv is None:
            return (f"{meters} 米太远了，{GRENADE_WEAPON}在 "
                    f"{GRENADE_RANGE_M} 米内才扔得准。")
        lines.append(f"距离 {meters} 米，DV {dv}。")
    else:
        meters = me.range_m or target.range_m
        if meters > GRENADE_RANGE_M:
            return (f"太远了：到{target.name}有 {meters} 米，"
                    f"手榴弹最多扔 {GRENADE_RANGE_M} 米。")
        dv = gridmap.dv_for_meters(GRENADE_WEAPON, meters) if meters > 0 else None
        if dv is None:
            return RANGE_ASK
        lines.append(f"距离 {meters} 米（估），DV {dv}；没有地图数据，爆炸按同场全体处理。")

    level = int(((card or {}).get("skills") or {}).get("运动", 0))
    total = (roller.roll(10) + me.dex + level + me.wound_penalty
             + me.ranged_penalty + modifier_for(combat, me))
    lines.append(f"投掷判定：{total} vs DV {dv}{modifier_text(combat, me)}")
    hit = total >= dv
    center = there
    if hit:
        lines.append("落点就在目标脚下。")
    elif on_map and there:
        step = _SCATTER_DIRS[roller.roll(8) - 1]
        far = roller.roll(2)
        spot = (there[0] + step[0] * far, there[1] + step[1] * far)
        if game.inside(spot):
            center = spot
            lines.append(f"没扔准：落点偏到 ({spot[0]},{spot[1]})，照样爆炸。")
        else:
            lines.append("没扔准，手榴弹砸在目标附近的墙上弹了回来，照样爆炸。")
    else:
        lines.append("没扔准，但爆炸还是盖住了目标所在的位置。")

    damage = _roll_damage(roller, GRENADE_DAMAGE)
    lines.append(f"爆炸伤害 {GRENADE_DAMAGE} = {damage}（伤害骰只掷一次，对所有目标生效）。")

    if on_map and center:
        victims = [c for c in combat.order
                   if c.alive and c.x >= 0 and c.y >= 0
                   and max(abs(c.x - center[0]), abs(c.y - center[1])) <= BLAST_CELLS]
        lines.append(f"爆心 5×5 格（{center[0]},{center[1]}）：{len(victims)} 个目标。")
    else:
        victims = [c for c in combat.order if c.alive]
        lines.append(f"没有地图：同场 {len(victims)} 个目标全部吃爆炸。")

    dodged: set[int] = set()
    for c in victims:
        if c is me or not c.alive or c.ref < 8:
            continue
        if c.no_dodge:  # 断腿：连爆炸都躲不开（full.txt:15876）
            continue
        dodge = roller.roll(10) + c.ref
        if dodge > total:
            dodged.add(id(c))

    touched: set[str] = set()
    for c in victims:
        if id(c) in dodged:
            lines.append(f"  {c.name} 反应过人，闪出了爆炸范围。")
            continue
        cover = None
        if on_map and center and c.x >= 0 and c.y >= 0:
            # 已经打烂的掩体不再挡爆炸（full.txt:14873-14894、:15454-15458）
            found = game.cover_on_line(center, (c.x, c.y), _gone(combat))
            if found is not None and _cover_hp(combat, found) > 0:
                cover = found
        if cover is not None and _cover_hp(combat, cover) >= damage:
            left = _cover_hp(combat, cover) - damage
            combat.covers[cover.id] = left
            touched.add(cover.id)
            lines.append(f"  {c.name} 躲在{cover.name}后面，掩体挡下全部爆炸（耐久剩 {left}）。")
            continue
        if cover is not None:
            combat.covers[cover.id] = 0
            touched.add(cover.id)
            lines.append(f"  {cover.name} 被炸烂了，{c.name} 吃满伤害。")
        armor = c.effective_sp
        taken = max(0, damage - armor)
        if taken:
            c.sp_wear += 1
        _apply_damage_hp(combat, c, taken, lines)
        _sync_pc_hp(conn, combat, c)
        lines.append(f"  {c.name} 被炸：护甲挡 {min(damage, armor)}，掉 {taken} 血"
                     f"（{max(0, c.hp)}/{c.max_hp} HP）。")

    if on_map and center:
        for cover in game.covers:
            if cover.id in touched or _cover_hp(combat, cover) <= 0:
                continue
            if max(abs(cover.x - center[0]), abs(cover.y - center[1])) > BLAST_CELLS:
                continue
            left = max(0, _cover_hp(combat, cover) - damage)
            combat.covers[cover.id] = left
            lines.append(f"  {cover.name} 被爆炸削掉 {damage} 点耐久（剩 {left}）。")

    _spend_gear(conn, group_id, actor_qq, item)
    slot.action = True
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 文本识别 ──────────────────────────────────────────────────

ATTACK_HINTS = ("打他", "打它", "打那个", "开枪", "射击", "开火", "砍他", "捅他",
                "爆头", "扫射", "招呼他", "朝他", "一拳")
SKILL_HINTS = {
    "手枪": "手枪", "步枪": "抵肩枪械", "霰弹": "抵肩枪械", "狙击": "抵肩枪械",
    "弓": "抵肩枪械", "冲锋": "自动武器", "机枪": "自动武器", "自动": "自动武器",
    "近战": "近战武器", "刀": "近战武器", "棍": "近战武器", "剑": "近战武器",
    "武术": "武术", "拳": "搏击", "重武器": "重武器", "火箭": "重武器",
}


#: 全自动射击的说法（「约定」：规则书只写「全自动射击」，这些是玩家口语）
AUTOFIRE_HINTS = ("全自动", "连射", "扫射", "连发", "突突", "哒哒")

#: 火力压制的说法（full.txt:14903-14914 只写「火力压制」/「压制」）
SUPPRESS_HINTS = ("火力压制", "压制射击", "压制火力", "压制")


def looks_like_suppression(text: str) -> bool:
    """这句话是不是在要求火力压制（另一个动作，同样 10 发，full.txt:14903）。"""
    return any(h in (text or "") for h in SUPPRESS_HINTS)


def looks_like_autofire(text: str) -> bool:
    """这句话是不是在要求全自动射击（一个动作 + 10 发，full.txt:14800）。"""
    return any(h in (text or "") for h in AUTOFIRE_HINTS)


def looks_like_attack(text: str) -> bool:
    # 扫射/全自动本身就是攻击动作，不必再说「打他」；
    # 火力压制是**另一个**动作（full.txt:14903），不算攻击。
    if looks_like_suppression(text):
        return False
    return (any(h in (text or "") for h in ATTACK_HINTS)
            or looks_like_autofire(text))


def autofire_weapon_from_text(text: str) -> str | None:
    """从话里认出全自动的枪型；认不出返回 None（由角色卡上的武器兜底）。"""
    body = text or ""
    if "突击" in body or "步枪" in body:
        return "突击步枪"
    if "冲锋" in body or "机枪" in body or "smg" in body.lower():
        return "冲锋枪"
    return None


def skill_from_text(text: str) -> str | None:
    body = text or ""
    for key, skill in SKILL_HINTS.items():
        if key in body:
            return skill
    return "手枪" if "枪" in body else None


#: 弹种的口语说法 → ``AMMO_TYPES`` 的键（full.txt:26688-26715）
AMMO_HINTS = (
    ("穿甲", AMMO_AP),
    ("ap 弹", AMMO_AP),
    ("ap弹", AMMO_AP),
    ("橡胶", AMMO_RUBBER),
    ("生物毒素", "生物毒素弹"),
    ("emp", "EMP 弹"),
    ("膨胀", "膨胀弹"),
    ("闪光", "闪光弹"),
    ("燃烧", "燃烧弹"),
    ("毒气", "毒气弹"),
    ("麻醉", "麻醉弹"),
    ("智能弹", "智能弹药"),
    ("烟雾", "烟雾弹"),
    ("催泪", "催泪弹"),
    ("基础弹", AMMO_BASE),
)


def ammo_type_from_text(text: str) -> str | None:
    """从话里认出弹种；认不出返回 None（保持当前弹种）。"""
    body = (text or "").lower()
    for key, kind in AMMO_HINTS:
        if key in body:
            return kind
    return None


#: 「一次打两下」的口语（full.txt:14477-14499 分段移动与开火速率）
BURST_HINTS = ("连打", "两下", "双发", "连开两枪", "打两枪", "快速射击")


def shots_from_text(text: str) -> int | None:
    """从话里认出「一次打两下」；没提就返回 None（默认按 1 发算）。"""
    body = text or ""
    return 2 if any(h in body for h in BURST_HINTS) else None



def target_from_text(combat: Combat, text: str,
                     actor: Combatant | None = None) -> str:
    body = text or ""
    pool = foes(combat, actor) if actor is not None else combat.enemies
    for c in pool:
        if c.name in body:
            return c.name
    return pool[0].name if pool else ""


def active(conn, group_id: str) -> Combat | None:
    combat = load(conn, group_id)
    return combat if combat is not None and combat.active else None


# ── 文本入口（给网关与 core 用）───────────────────────────────


def parse_enemy_arg(raw: str) -> tuple[str, int]:
    parts = (raw or "").strip().split()
    if not parts:
        return DEFAULT_ENEMY, 1
    name = parts[0]
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    return name, max(1, count)


def parse_attack_arg(raw: str) -> tuple[str, str | None]:
    parts = (raw or "").strip().split()
    if not parts:
        return "", None
    return parts[0], (parts[1] if len(parts) > 1 else None)


_COUNT_EXPR = re.compile(r"^[0-9pc+\-*/() ]+$")


def _count_value(raw, pc: int) -> int:
    """把 encounter 的 count 转成人数：整数、=pc、=pc-2、=(pc-2)//2 等。"""
    if isinstance(raw, int):
        return max(0, raw)
    text = str(raw or "").strip()
    if not text:
        return 1
    if text.startswith("="):
        expr = text[1:]
        if not _COUNT_EXPR.match(expr):
            return 1
        try:
            value = eval(expr, {"__builtins__": {}}, {"pc": pc})  # noqa: S307
        except Exception:
            return 1
        return max(0, int(value))
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return 1


def _actor_status(conn, campaign_id) -> dict[str, str]:
    if campaign_id is None:
        return {}
    rows = conn.execute(
        "SELECT actor_id, status FROM campaign_actors WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    return {str(r["actor_id"]): str(r["status"] or "") for r in rows}


def _condition_ok(cond, status: dict[str, str]) -> bool:
    """条件组：{"escaped": [...], "not_escaped": [...]}。"""
    if not isinstance(cond, dict):
        return True
    escaped = [str(x) for x in (cond.get("escaped") or [])]
    not_escaped = [str(x) for x in (cond.get("not_escaped") or [])]
    if any(status.get(a) != "escaped" for a in escaped):
        return False
    if any(status.get(a) == "escaped" for a in not_escaped):
        return False
    return True


def _encounter_groups(
    conn, group_id: str
) -> tuple[tuple[dict, ...], tuple[dict, ...]] | None:
    """场景里写了 encounter 就自动铺场（只在玩家没指定敌人时用）。

    返回 (敌人组, 环境修正)；敌人组里可以带 ``script``（脚本名或内联 dict）。
    """
    row = conn.execute(
        "SELECT c.id AS campaign_id, c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return None
    campaign_id = int(row["campaign_id"]) if row["campaign_id"] is not None else None
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return None
    scene_id = str(row["scene"] or "")
    enc = (data.get("scenes", {}).get(scene_id) or {}).get("encounter")
    if not enc:
        return None
    pc = max(1, len(roster.members(conn, group_id)) + len(easy_roster.members(conn, group_id)))
    statuses = _actor_status(conn, campaign_id)
    script_bank = {str(k): v for k, v in (data.get("scripts") or {}).items()}
    script_bank.update({str(k): v for k, v in (enc.get("scripts") or {}).items()})
    enc_range = int(enc.get("range_m") or 0)
    groups: list[dict] = []
    for g in enc.get("groups") or []:
        ref = str(g.get("ref") or "").strip()
        if ref:
            if campaign_id is None:
                continue
            grp = actor_group(conn, campaign_id, ref,
                              fresh=bool(g.get("fresh")))
            if grp is None:
                continue
            cond = g.get("if")
            if isinstance(cond, dict):
                if not _condition_ok(cond, statuses):
                    continue
            elif cond:
                if grp.get("status") != str(cond).strip():
                    continue
            elif grp.get("status") == "dead":
                continue
            grp["faction"] = str(g.get("faction") or grp["faction"])
            grp["label"] = str(g.get("label") or grp.get("label") or "")
            grp["hostile_to"] = list(g.get("hostile_to") or [])
            grp["range_m"] = int(g.get("range_m") or enc_range or grp.get("range_m") or 0)
            if g.get("script") is not None:
                script = g.get("script")
                if isinstance(script, str):
                    script = script_bank.get(script) or {}
                grp["script"] = dict(script) if isinstance(script, dict) else {}
            groups.append(grp)
            continue
        name = str(g.get("enemy") or g.get("name") or "").strip()
        if not name or enemy_preset(name) is None:
            continue
        if not _condition_ok(g.get("if"), statuses):
            continue
        raw = g.get("count", 1)
        count = _count_value(raw, pc)
        if count <= 0:
            continue
        script = g.get("script")
        if isinstance(script, str):
            script = script_bank.get(script) or {}
        groups.append({
            "name": name,
            "count": count,
            "actor": str(g.get("actor") or "").strip(),
            "faction": str(g.get("faction") or "enemy"),
            "label": str(g.get("label") or ""),
            "hostile_to": list(g.get("hostile_to") or []),
            "script": dict(script) if isinstance(script, dict) else {},
            "spawn": g.get("spawn"),
            "range_m": int(g.get("range_m") or enc_range or 0),
        })
    _encounter_meta[group_id] = {"scene": scene_id, "note": str(enc.get("note") or "")}
    if not groups:
        return (), ()
    modifiers = tuple(e for e in (enc.get("modifiers") or ()) if isinstance(e, dict))
    return tuple(groups), modifiers


_encounter_meta: dict[str, dict] = {}


def start_text(conn, group_id: str, arg: str, *, campaign_id=None,
               scene: str = "") -> str:
    groups = None
    modifiers: tuple[dict, ...] = ()
    if not (arg or "").strip():
        found = _encounter_groups(conn, group_id)
        if found is not None:
            groups, modifiers = found
            if not groups:
                meta = _encounter_meta.get(group_id) or {}
                note = str(meta.get("note") or "")
                tip = f"（{note}）" if note else ""
                return f"🚪 这次遭遇没有人登场，直接跳过{tip}。"
    if groups is None:
        # 手动「开打 打手 2」：清掉上一场遭遇留下的 note，否则炮塔的
        # 说明会挂到随便刷出来的打手头上（旧 bug）。
        _encounter_meta.pop(group_id, None)
        name, count = parse_enemy_arg(arg)
        groups = ({"name": name, "count": count, "faction": "enemy"},)
    groups = [
        g if isinstance(g, dict) else {"name": str(g[0]), "count": int(g[1]),
                                       "faction": "enemy"}
        for g in groups
    ]
    start(conn, group_id, campaign_id=campaign_id, scene=scene, enemies=groups,
          modifiers=modifiers)
    label = "、".join(
        f"{g['name']}" + (f"×{g['count']}" if g["count"] > 1 else "")
        for g in groups)
    meta = _encounter_meta.get(group_id) or {}
    tip = f"（{meta.get('note')}）" if meta.get("note") else ""
    return f"⚔ 开打：{label}{tip}（先攻 = 反应 + 1D10）" + chr(10) + status_text(conn, group_id)


def attack_text(conn, group_id: str, user_id: str, arg: str) -> str:
    target, skill = parse_attack_arg(arg)
    return attack(conn, group_id, user_id, target, skill=skill)


def stop_text(conn, group_id: str) -> str:
    end(conn, group_id)
    return "🏁 战斗结束，场地清空。"
