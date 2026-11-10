"""RED 技能表与三套规则归一（``full.txt:6935-7139``）。

要点：
* RED 正典 66 项全部认得出属性；
* Easy Mode / V3 的叫法能折到 RED 正典名；
* 同名技能属性冲突时**按 RED 算**（察言观色 EMP、乐器演奏 TECH、驯兽 INT）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyberpunk import commands, easy, names, skills

RED_FILE = (Path(__file__).resolve().parents[2]
            / "rules" / "red_core" / "data" / "skills.json")


def _red_rows() -> list[dict]:
    return json.loads(RED_FILE.read_text(encoding="utf-8"))["skills"]


def test_red_table_has_66_skills():
    rows = _red_rows()
    assert len(rows) == 66
    # 书里九个技能分类都在
    cats = {r["cat"] for r in rows}
    assert {"感官", "体魄", "操控", "教育", "战斗", "表演", "远程武器", "社交", "技术"} == cats


def test_red_x2_skills_match_the_book():
    """书里标（×2）的 7 项：空中载具驾驶/武术/自动武器/重武器/爆破学/电子安全技术/强化急救。"""
    x2 = {r["zh"] for r in _red_rows() if r["x2"]}
    assert x2 == {"空中载具驾驶", "武术", "自动武器", "重武器", "爆破学",
                  "电子/安全技术", "强化急救"}


@pytest.mark.parametrize("name,stat", [
    (r["zh"], r["stat"]) for r in _red_rows()
])
def test_every_red_skill_resolves(name, stat):
    assert skills.stat_of(name) == stat


def test_skill_table_loaded():
    assert len(skills.skill_table()) == 66


# ---------- 三套规则的别名归一 ----------

@pytest.mark.parametrize("alias,canon", [
    # Easy Mode 写法
    ("藏匿/搜寻物品", "藏匿&揭示物体"),
    ("抵抗拷问/药物", "折磨&药物耐受"),
    ("抵肩枪械", "长枪射击"),
    ("衣着与风格", "服饰&风格"),
    ("电子/安防技术", "电子/安全技术"),
    ("徒手搏斗", "搏击"),
    ("医疗", "强化急救"),
    ("官僚世故", "官场世故"),
    # V3 写法
    ("藏匿与脱身", "藏匿&揭示物体"),
    ("抗性（拷问/药物）", "折磨&药物耐受"),
    ("读唇", "唇语"),
    ("跟踪", "追踪"),
    ("步枪", "长枪射击"),
    ("冲锋枪", "自动武器"),
    ("装备", None),  # 不是技能，保持原样
])
def test_aliases_fold_to_red(alias, canon):
    got = skills.canonical(alias)
    if canon is None:
        assert got == alias
    else:
        assert got == canon


def test_separator_spaces_are_cleaned():
    assert skills.canonical("藏匿& 揭示物体") == "藏匿&揭示物体"
    assert skills.canonical("电子/ 安全技术") == "电子/安全技术"


# ---------- 冲突时按 RED ----------

@pytest.mark.parametrize("skill,red_stat", [
    ("察言观色", "EMP"),   # V3 是 COOL
    ("乐器演奏", "TECH"),  # V3 是 INT
    ("驯兽", "INT"),       # V3 是 COOL
])
def test_red_wins_on_conflict(skill, red_stat):
    assert skills.stat_of(skill) == red_stat
    # 合并进 commands 之后也必须是 RED 口径
    assert commands.SKILL_STAT[skill] == red_stat


def test_commands_covers_all_red_skills():
    missing = [r["zh"] for r in _red_rows() if r["zh"] not in commands.SKILL_STAT]
    assert missing == []
    wrong = [(r["zh"], commands.SKILL_STAT[r["zh"]], r["stat"])
             for r in _red_rows()
             if commands.SKILL_STAT[r["zh"]] != r["stat"]]
    assert wrong == []


def test_commands_still_keeps_legacy_aliases():
    """老词条不能因为合并 RED 而丢（已有剧本/卡在用）。"""
    for old in ("手枪", "步枪", "重武器", "说服", "审讯", "冲锋枪"):
        assert old in commands.SKILL_STAT


def test_easy_skill_stat_prefers_red():
    assert easy.skill_stat("藏匿&揭示物体") == "INT"
    assert easy.skill_stat("藏匿/搜寻物品") == "INT"   # 别名也认
    assert easy.skill_stat("长枪射击") == "REF"


def test_category_and_x2_lookup():
    assert skills.category_of("手枪") == "远程武器"
    assert skills.is_x2("武术") is True
    assert skills.is_x2("手枪") is False


def test_unknown_skill_returns_none():
    assert skills.stat_of("不存在的技能") is None
    assert skills.canonical("不存在的技能") == "不存在的技能"
