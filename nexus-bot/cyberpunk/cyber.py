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
