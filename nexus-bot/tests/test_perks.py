"""专长（Perk）与天赋（Talent）的测试。"""

from __future__ import annotations

import pytest

from cyberpunk import perks
from cyberpunk.characters import Character


def test_data_counts():
    assert len(perks.perks()) == 10
    assert len(perks.talents()) == 24


def test_every_entry_is_localised():
    for kind, table in (("perk", perks.perks()), ("talent", perks.talents())):
        for en, item in table.items():
            assert item["zh"], f"{en} 没有中文名"
            assert item["note"] and item["effect"], f"{item['zh']} 缺说明"
            assert not item["zh"].isascii(), f"{item['zh']} 像是没翻译"


def test_find_by_chinese_english_and_fragment():
    assert perks.find("权威")[1] == "Authority"
    assert perks.find("Authority")[1] == "Authority"
    assert perks.find("俊美")[0] == "talent"
    assert perks.find("夜视")[2]["zh"] == "夜视"
    assert perks.find("不存在的东西") is None


def test_perk_cost_follows_impact_multiplier():
    assert perks.perk_cost(1, "moderate") == 2
    assert perks.perk_cost(1, "high") == 3
    assert perks.perk_cost(1, "very high") == 4
    assert perks.perk_cost(1, "little") == 1
    assert perks.perk_cost(5, "very high") == 20.0  # 财富是最贵的专长
    assert perks.perk_cost(0.5, "moderate") == 1.0  # 人情最便宜


def test_validate_rejects_unknown():
    with pytest.raises(ValueError):
        perks.validate(["权威", "根本没这个"])


def test_summary_splits_perks_and_talents():
    text = perks.summary(["Authority", "Beautiful or Handsome", "Night Vision"])
    assert text == "专长 权威；天赋 俊美、夜视"


def test_beautiful_raises_social_skills():
    stats = {k: 5 for k in ("INT", "REF", "TECH", "DEX", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP")}
    plain = Character("a", dict(stats), skills={"说服": 3}, perks={})
    pretty = Character("b", dict(stats), skills={"说服": 3}, perks={"Beautiful or Handsome": 2})
    assert pretty.effective_skill("说服") == plain.effective_skill("说服") + 2


def test_rapid_healing_is_documented():
    """Rapid Healing 的效果写在数据里（每天多回 3 点），方便 GM 与日志引用。"""
    assert "3" in perks.talents()["Rapid Healing"]["effect"]
