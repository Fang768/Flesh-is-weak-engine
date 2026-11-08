"""赛博改造（义体）条目：中文名与检索。

数据文件 ``rules/cp_v3/data/cyberware_zh.json``：
``{"_meta": ..., "items": {"Audio Splice": {"zh": "听觉接驳器", "note": "..."}}}``
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "rules" / "cp_v3" / "data"
CYBERWARE_FILE = DATA_DIR / "cyberware_zh.json"
#: 人性损失（HL）表，从规则书赛博组件表抽出来的：``rules/red_core/data/cyberware_hl.json``
HL_FILE = DATA_DIR.parents[1] / "red_core" / "data" / "cyberware_hl.json"

# 分类只用于展示分组，键是条目名的前缀/关键词
GROUPS = (
    ("接驳器 Splice", ("Splice", "Chipware Port", "Chipware port")),
    ("链接 Link", ("Link", "AutoFAC", "Radio", "Smartgun", "Vehicle", "Targeting", "Subvocal", "Mechapresence")),
    ("强化 Amp", ("Amped", "Analyzer", "Anti-dazzle", "Digital Recorder", "Infrared Vision",
                 "Level Damper", "Low Light", "Microvision", "Pain Editor", "Radiation Vision",
                 "Scent Tracking", "Smell Damper", "Sonar", "Sound Editing", "Synthesizer",
                 "Targeting Scope", "Telescopic Vision", "Thermographic Vision", "Ultraviolet Vision",
                 "Subvision")),
    ("套装 Package", ("PACKAGE",)),
)


@lru_cache(maxsize=1)
def items() -> dict[str, dict]:
    if not CYBERWARE_FILE.exists():
        return {}
    return dict(json.loads(CYBERWARE_FILE.read_text(encoding="utf-8")).get("items", {}))


def all_entries() -> list[dict]:
    return [{"en": en, "zh": meta.get("zh", en), "note": meta.get("note", "")}
            for en, meta in items().items()]


def get(name: str) -> dict | None:
    want = (name or "").strip().lower()
    if not want:
        return None
    for entry in all_entries():
        if entry["en"].lower() == want or entry["zh"].lower() == want:
            return entry
    return None


def search(keyword: str, limit: int = 8) -> list[dict]:
    key = (keyword or "").strip().lower()
    if not key:
        return []
    hits = []
    for entry in all_entries():
        haystack = f"{entry['en']} {entry['zh']} {entry['note']}".lower()
        if key in haystack:
            hits.append(entry)
    hits.sort(key=lambda e: (0 if key in e["zh"].lower() else 1, e["en"]))
    return hits[:limit]


def format_entry(entry: dict) -> str:
    line = f"🦾 {entry['zh']}（{entry['en']}）"
    return line if not entry["note"] else f"{line} — {entry['note']}"


def markdown_table() -> str:
    rows = ["| 中文名 | 英文名 | 说明 |", "| --- | --- | --- |"]
    for entry in sorted(all_entries(), key=lambda e: e["en"]):
        rows.append(f"| {entry['zh']} | {entry['en']} | {entry['note']} |")
    return chr(10).join(rows)


# ---------- 人性损失（HL） ----------
#
# 书内页 112（``rules/red_core/raw/full.txt:9834-9835``）：
#   「HL（人性损失）: 这个装置对你的心理影响有多严重。在角色生成中，人性损失是预设的。
#     在角色生成以后，人性点数损失由预设数字后括号中的骰子决定。」
# 也就是说：建卡时直接扣 ``hl`` 点；游戏里临时加装则改投 ``hl_dice``，扣骰出来的点数。


@lru_cache(maxsize=1)
def hl_table() -> dict[str, dict]:
    """加载 HL 表；文件缺失时返回空表（不抛错，和本模块其它函数的风格一致）。"""
    if not HL_FILE.exists():
        return {}
    return dict(json.loads(HL_FILE.read_text(encoding="utf-8")).get("items", {}))


def hl_of(name: str) -> dict | None:
    """按中文名查 HL。先精确，再退一步做包含匹配（「赛博义眼（单颗）」→「赛博义眼」）。"""
    table = hl_table()
    want = (name or "").strip()
    if not want:
        return None
    if want in table:
        return {**table[want], "zh": want}
    # 去掉括注后再试一次：「赛博义眼（单颗）」→「赛博义眼」
    bare = want.split("（")[0].split("(")[0].strip()
    if bare and bare in table:
        return {**table[bare], "zh": bare}
    hits = [k for k in table if want in k or k in want]
    if len(hits) == 1:
        return {**table[hits[0]], "zh": hits[0]}
    return None


def roll_hl(entry: dict, roller=None) -> int:
    """算出这次安装该扣多少人性。

    * 没有 ``hl_dice``（书里写 ``N/A``）→ 扣 ``hl`` 点（本来就是 0 的时尚装置就是 0）。
    * 有 ``hl_dice`` → 按骰子表达式投，返回骰出来的点数（书内页 112 的「游戏后安装用法」）。
    """
    from . import dice as _dice  # 延迟导入，避免循环

    if entry is None:
        return 0
    spec = entry.get("hl_dice")
    if not spec:
        return int(entry.get("hl") or 0)
    r = roller or _dice.RandomRoller()
    total = 0
    for part in str(spec).split("+"):
        part = part.strip().lower()
        if "d" not in part:
            total += int(part or 0)
            continue
        head, _, tail = part.partition("d")
        count = int(head or 1)
        divisor = 1
        if "/" in tail:
            tail, _, div = tail.partition("/")
            divisor = max(1, int(div or 1))
        sides = int(tail)
        subtotal = sum(r.roll(sides) for _ in range(max(1, count)))
        total += -(-subtotal // divisor)  # 向上取整（书内页 112 的表头写着「向上取整」）
    return total
