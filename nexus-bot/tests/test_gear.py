"""武器中文数据与检索的测试。"""

from __future__ import annotations

import json
from pathlib import Path

from cyberpunk import gear

DATA = Path(__file__).resolve().parents[2] / "rules" / "cp_v3" / "data"


def test_every_weapon_has_chinese_name():
    missing = [w["name"] for w in gear.weapons() if w["name_zh"] == w["name"]]
    assert missing == []


def test_weapon_count_matches_source():
    raw = json.loads((DATA / "weapons.json").read_text(encoding="utf-8"))
    assert len(gear.weapons()) == len(raw) == 81


def test_chinese_keys_align_with_english():
    zh = json.loads((DATA / "weapons_zh.json").read_text(encoding="utf-8"))["items"]
    en = {w["name"] for w in gear.weapons()}
    assert {gear._key(k) for k in zh} == {gear._key(n) for n in en}


def test_search_by_chinese_name():
    hits = gear.search("手枪", limit=3)
    assert hits, "类型词应该能命中"
    # 名字里带「手枪」的都算命中（气手枪是 EX 类），名称命中的排在类型命中前面
    assert all("手枪" in h["name_zh"] or h["type"] == "P" for h in hits)


def test_search_by_type_word():
    melee = gear.search("近战", limit=5)
    assert melee and all(h["type"] == "MELEE" for h in melee)


def test_get_is_accent_and_space_tolerant():
    assert gear.get("FN-FAL") is not None
    assert gear.get("fn fal") is not None
    assert gear.get("不存在的枪") is None


def test_format_card_is_chinese():
    entry = gear.get("FN-FAL")
    card = gear.format_card(entry)
    assert "FN FAL" in card
    assert "步枪" in card
    assert "伤害" in card
    assert "\\n" not in card  # 不能出现字面反斜杠
    assert len(card.splitlines()) >= 3


def test_cost_labels_are_localized():
    assert gear.format_cost("REF") == "价格由裁判定"
    assert gear.format_cost("GIRI") == "要用人情债买"
    assert gear.format_cost("$500") == "$500"
