"""简易版（RED Easy Mode）模板与认领的测试。"""

from __future__ import annotations

import sqlite3

import pytest

from cyberpunk import easy
from core import db, easy_roster

TEMPLATES = easy.templates()


def test_all_templates_have_required_fields():
    assert len(TEMPLATES) >= 6
    for card in TEMPLATES:
        assert card["name"] and card["role"]
        assert set(card["stats"]) == set(easy.STAT_ORDER)
        assert 30 <= card["hp"] <= 50
        assert card["wound"] == card["hp"] // 2
        assert card["death_save"] == card["stats"]["BODY"]
        assert card["skills"], "每张卡都要有技能"
        assert card["weapons"], "每张卡都要有武器"


def test_every_skill_has_a_stat():
    for card in TEMPLATES:
        for skill in card["skills"]:
            assert easy.skill_stat(skill), f"{skill} 没有关联属性"


def test_skill_stat_matches_book():
    assert easy.skill_stat("手枪") == "REF"
    assert easy.skill_stat("潜行") == "DEX"
    assert easy.skill_stat("说服") == "COOL"
    assert easy.skill_stat("觉察") == "INT"
    assert easy.skill_stat("医疗") == "TECH"
    assert easy.skill_stat("专注") == "WILL"
    assert easy.skill_stat("交谈") == "EMP"


def test_resolve_skill_prefers_exact_then_fragment():
    skills = {"手枪": 6, "抵肩枪械": 6, "潜行": 2}
    assert easy.resolve_skill("手枪", skills) == ("手枪", 6)
    assert easy.resolve_skill("枪械", skills) == ("抵肩枪械", 6)
    assert easy.resolve_skill("不存在的技能", skills) == (None, 0)


def test_get_matches_name_role_and_fragment():
    assert easy.get("火炬")["role"] == "技工"
    assert easy.get("技工")["name"] == "火炬"
    assert easy.get("没有这张") is None


def test_format_card_has_chinese_lines():
    text = easy.format_card(easy.get("搬运工"))
    assert "佣兵" in text
    assert "生命 45" in text
    assert "重伤 22" in text
    assert "先攻" in text  # 职业能力里提到


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    yield c
    c.close()


def test_claim_writes_one_card_per_player(conn):
    tpl = easy.get("火炬")
    first = easy_roster.save(conn, "g1", "u1", tpl)
    again = easy_roster.save(conn, "g1", "u1", easy.get("赤尾"))
    assert again["name"] == "赤尾"
    assert easy_roster.get(conn, "g1", "u1")["name"] == "赤尾"
    assert len(easy_roster.members(conn, "g1")) == 1


def test_cards_are_scoped_per_group(conn):
    easy_roster.save(conn, "g1", "u1", easy.get("火炬"))
    assert easy_roster.get(conn, "g2", "u1") is None
