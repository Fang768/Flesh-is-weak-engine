"""命令层纯逻辑：参数解析与骰点格式化（不依赖 NoneBot，方便单测）。"""

from __future__ import annotations

import re

from .checks import DV
from .dice import Roller

# 技能 → 关联属性（V3 无 EMP，社交类走 COOL）
SKILL_STAT: dict[str, str] = {
    # REF —— 远程
    "手枪": "REF",
    "抵肩枪械": "REF",
    "冲锋枪": "REF",
    "步枪": "REF",
    "重武器": "REF",
    "驾驶地面载具": "REF",
    # DEX —— 战斗与身法
    "近战武器": "DEX",
    "徒手搏斗": "DEX",
    "闪避": "DEX",
    "潜行": "DEX",
    "运动": "DEX",
    # TECH —— 手艺
    "技术": "TECH",
    "急救": "TECH",
    "医疗": "TECH",
    "电子/安防技术": "TECH",
    "撬锁": "TECH",
    "伪造": "TECH",
    # INT —— 脑子
    "觉察": "INT",
    "图书馆检索": "INT",
    "本地专家": "INT",
    "推理": "INT",
    "犯罪学": "INT",
    "教育": "INT",
    "战术": "INT",
    # COOL —— 嘴与人
    "说服": "COOL",
    "审讯": "COOL",
    "交易": "COOL",
    "街头智慧": "COOL",
    "表演": "COOL",
    "衣着与风格": "COOL",
}

# V3.0 全量技能（96 项，来自 glossary.json）叠加在上面那批旧别名之后；
# 旧别名优先，避免破坏已经写过的手感。
from .names import skill_stat as _v3_skill_stat  # noqa: E402

SKILL_STAT = {**_v3_skill_stat(), **SKILL_STAT}

NOTATION_RE = re.compile(r"^\s*(\d{1,2})\s*[dD]\s*(\d{1,3})\s*(?:([+-])\s*(\d{1,3}))?\s*$")
DV_RE = re.compile(r"dv\s*=\s*(\d{1,2})", re.I)
MOD_RE = re.compile(r"mod\s*=\s*([+-]?\d{1,2})", re.I)

ALLOWED_SIDES = (4, 6, 8, 10, 12, 20, 100)


def parse_notation(text: str) -> tuple[int, int, int]:
    """``2d6+1`` → (数量, 面数, 修正)。上限 20 颗，防刷屏。"""
    m = NOTATION_RE.match(text or "")
    if not m:
        raise ValueError("格式应为 2d6+1 / 1d10 / 3d6-2")
    count, sides, sign, flat = m.groups()
    n, s = int(count), int(sides)
    if not 1 <= n <= 20:
        raise ValueError("骰子数量要在 1–20")
    if s not in ALLOWED_SIDES:
        raise ValueError("面数只支持 d4/d6/d8/d10/d12/d20/d100")
    flat_v = 0
    if flat:
        flat_v = int(flat) if sign == "+" else -int(flat)
    return n, s, flat_v


def roll_verbose(roller: Roller, n: int, s: int, flat: int) -> str:
    """掷骰并给出可读的明细。"""
    rolls = [roller.roll(s) for _ in range(n)]
    total = sum(rolls) + flat
    detail = " + ".join(str(x) for x in rolls)
    tail = f" {flat:+d}" if flat else ""
    return f"🎲 {n}d{s}{tail}: {detail}{tail} = **{total}**"


def parse_check_args(text: str) -> tuple[str, int, list[int]]:
    """``手枪 dv=14 mod=-3`` → ("手枪", 14, [-3])。"""
    raw = text or ""
    dv = int(DV.EVERYDAY)
    dv_m = DV_RE.search(raw)
    if dv_m:
        dv = int(dv_m.group(1))
    mods = [int(m.group(1)) for m in MOD_RE.finditer(raw)]
    name = MOD_RE.sub("", DV_RE.sub("", raw)).strip()
    return name, dv, mods
