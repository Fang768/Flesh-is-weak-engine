"""把 V3.0（Fuzion）的装备数值折算进 RED Easy Mode（Interlock）。

Easy Mode 的核心不动：`1d10 + 属性 + 技能 vs DV`、平局守方胜、单一 HP 池、
DV 五档 13/15/17/21/24。这里只做**装备量纲的桥接**，让 V3 的 81 把枪、
18 件护甲、122 条义体能直接在这套规则里用。

每条换算都只依赖一个函数，方便在文档里逐条解释，也方便以后调平衡。
"""

from __future__ import annotations

import re

# ── 难度桥（Easy Mode 五档）─────────────────────────────
# V3 的可得性代码直接映射到 Easy Mode 的 DV 阶梯，买不买得到就是一次检定。
AVAILABILITY_DV = {"E": 13, "C": 15, "P": 17, "R": 21, "U": 24}
# 隐蔽性 → 被别人"看出来你带了枪"的觉察 DV
CONCEAL_DV = {"P": 13, "J": 15, "L": 17, "N": 21}
# 可靠性 → 卡壳（自然骰出这些点数就卡壳）
MALFUNCTION_ON = {"VR": (), "ST": (1,), "UR": (1, 2)}
# EV（累赘）→ 反射类检定罚值
EV_PENALTY = ((0, 0), (2, -1), (4, -2), (99, -3))
# Easy Mode 护甲 SP 上限（重武器会超过，封顶后靠"半甲/AP"体现差异）
SP_CAP = 18
SP_SCALE = 0.7

_DICE = re.compile(r"^\s*(\d+)\s*[dD]\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$")


def parse_damage(text: str) -> tuple[int, int]:
    """'6D6+2' → (6, 2)；'Stun'/'Drugs' 这类非骰点文本返回 (0, 0)。"""
    m = _DICE.match(text or "")
    if not m:
        return (0, 0)
    dice, _sides, sign, flat = m.groups()
    mod = int(flat or 0)
    return (int(dice), -mod if sign == "-" else mod)


def damage_to_easy(text: str) -> str:
    """伤害骰折算：≤5D6 原样保留，更重的压缩到 5D6–8D6 区间。

    例：2D6+1 → 2D6+1；6D6+2 → 5D6+1；10D6 → 7D6；13D6 → 8D6。
    """
    dice, mod = parse_damage(text)
    if not dice:
        return text  # Stun / Drugs / Varies
    if dice <= 5:
        return f"{dice}D6" + (f"{mod:+d}" if mod else "")
    easy = min(8, dice // 2 + 2)
    mod_easy = mod // 2
    return f"{easy}D6" + (f"{mod_easy:+d}" if mod_easy else "")


def _int(value, default: int = 0) -> int:
    """数据文件里的数字常是字符串（'21'），统一转一下。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def is_autofire(v3_rof) -> bool:
    """V3 的 ROF ≥ 15 视作全自动（可直接套用 V3 的连发命中算法）。"""
    return _int(v3_rof) >= 15


def shots_per_turn(v3_rof) -> int:
    """Easy Mode 里"每回合能打几发"就是 V3 的 ROF 本身。"""
    return max(1, _int(v3_rof, 1))


def hit_modifier(accuracy: str) -> int:
    """V3 的精度（WA）直接当命中修正：'−1' → -1；'0' → 0。"""
    try:
        return int(str(accuracy).replace("−", "-").replace("+", "").strip())
    except (TypeError, ValueError):
        return 0


def availability_dv(code: str) -> int:
    return AVAILABILITY_DV.get((code or "").strip().upper(), 15)


def conceal_dv(code: str) -> int:
    return CONCEAL_DV.get((code or "").strip().upper(), 17)


def malfunction_on(code: str) -> tuple[int, ...]:
    return MALFUNCTION_ON.get((code or "").strip().upper(), (1,))


def ev_penalty(ev: int) -> int:
    ev = max(0, int(ev or 0))
    for limit, penalty in EV_PENALTY:
        if ev <= limit:
            return penalty
    return -3


def sp_to_easy(sp: int) -> int:
    """V3 的 SP 按 0.7 缩放并封顶 18（Easy Mode 的猎人皮衣是 11）。"""
    return min(SP_CAP, max(0, round(int(sp or 0) * SP_SCALE)))


def armor_row(name: str, sp: int, ev: int) -> dict:
    return {
        "name": name,
        "sp": sp_to_easy(sp),
        "sp_v3": sp,
        "ev": ev,
        "ref_penalty": ev_penalty(ev),
    }


# ── 义体：只保留与 Easy Mode 数值相容的六件 ──────────────────
CYBERWARE_EASY = (
    ("智能枪链接 Smartgun", "枪械命中 +2（改造费 = 枪价 ×2）"),
    ("强化反射（克伦兹科夫）", "反应 +1（影响先攻与所有反应类检定）"),
    ("痛觉编辑器 Pain Editor", "意志类检定 +4，无视疼痛惩罚"),
    ("芯片接口 + 技能芯片", "选定技能 +2（最多叠到 +3），每级 $300"),
    ("护腕 Bracer（四肢）", "该肢体额外护甲 SP 10；不叠加身上护甲，取高"),
    ("抗眩光 Anti-dazzle", "免疫闪光/频闪/闪光弹造成的致盲与罚值"),
)

# ── 成长：Easy Mode 没有 IP，用简化版 ─────────────────────
IP_PER_SESSION = 4          # 每场固定 4 点，省得记表
IP_COST_MULTIPLIER = 2      # 技能 +N → +N+1 花费 N × 2 点


def ip_cost(current_level: int) -> int:
    """把技能从 current_level 升一级需要的 IP。"""
    return max(1, int(current_level or 0) * IP_COST_MULTIPLIER)
