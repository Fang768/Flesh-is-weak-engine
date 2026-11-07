"""从武器页的 text block 里取「名称 + 英文描述」。

版式事实（书 p177–181）：
* 每个武器 = 一个 text block，块内顺序是 名称行 / 射程 / 价格 / 类型 / 精度 / 隐蔽 / 可得+伤害 / 弹数 / 射速 / 可靠性 / 描述。
* 字母间距排版，名称要先去空格。
* 段首武器块的名称行前面会粘上小标题（"Light Autopistols"），要剔掉。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

PDF = r"D:\Code\Synthetic_Dawn\Cyberpunk v3 - CP4110 Core Rulebook.pdf"
OUT = Path(__file__).with_name("data") / "weapon_desc_en.json"

TYPE_CODES = {"P", "SMG", "SHG", "SHT", "RIF", "HVY", "EX", "MELEE", "BFG"}
RELIABILITY = {"VR", "ST", "UR"}

SECTION_TITLES = {
    "LIGHT AUTOPISTOLS",
    "MEDIUM AUTOPISTOLS",
    "HEAVY AUTOPISTOLS",
    "VERY HEAVY AUTOPISTOLS",
    "LIGHT SUBMACHINE GUNS",
    "MEDIUM SUBMACHINE GUNS",
    "HEAVY SUBMACHINE GUNS",
    "ASSAULT RIFLES",
    "SHOTGUNS",
    "HEAVY WEAPONS",
    "MELEE WEAPONS",
    "EXOTIC WEAPONS",
}


def despace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def join_despace(text: str) -> str:
    """按词还原间距：单字符 token 连成一个词，多字符 token 之间留空格。"""
    out: list[str] = []
    buf = ""
    for tok in text.split():
        if len(tok) == 1 and tok.isalnum():
            buf += tok
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(tok)
    if buf:
        out.append(buf)
    return " ".join(out)


def fix_hyphen(text: str) -> str:
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", text)


def main() -> None:
    doc = pymupdf.open(PDF)
    result: dict[str, str] = {}
    for page_no in range(178, 183):
        page = doc.load_page(page_no - 1)
        for block in page.get_text("blocks"):
            lines = [l.strip() for l in block[4].splitlines() if l.strip()]
            if len(lines) < 4:
                continue
            rel_idx = None
            for i, line in enumerate(lines):
                if despace(line).upper() in RELIABILITY:
                    rel_idx = i
            if rel_idx is None:
                continue
            type_idx = None
            for i, line in enumerate(lines[:rel_idx]):
                if despace(line).upper() in TYPE_CODES:
                    type_idx = i
                    break
            if type_idx is None or type_idx == 0:
                continue
            name = ""
            for line in lines[:type_idx]:
                fixed = join_despace(line).upper()
                if fixed in SECTION_TITLES or not fixed:
                    continue
                name = fixed  # 名称只占一行；射程/价格是后面的行
                break
            if not name:
                continue
            desc = fix_hyphen(" ".join(lines[rel_idx + 1:])).strip()
            if name and desc:
                result[name] = desc
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("weapons with desc:", len(result))


if __name__ == "__main__":
    main()
