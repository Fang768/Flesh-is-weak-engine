"""角色卡：模板套用 + 校验 + 存取。"""

from __future__ import annotations

import pytest

from core import db, roster


def test_build_solo_matches_template():
    card = roster.build(template="独狼", name="老猫")
    assert card["stats"]["REF"] == 7
    assert card["stats"]["BODY"] == 6
    d = roster.derived_of(card)
    assert d["HP"] == 40  # 10 + 5×⌈(6+6)/2⌉
    assert d["WOUND"] == 20  # ⌈HP/2⌉
    assert d["SD"] == 6  # = BODY
    assert d["HUM"] == 50  # EMP×10


def test_build_swaps_two_stats():
    card = roster.build(template="独狼", name="A", stat_swaps=[["REF", "DEX"]])
    assert card["stats"]["REF"] == 7 and card["stats"]["DEX"] == 7  # 7/7 互换看不出
    card2 = roster.build(template="独狼", name="B", stat_swaps=[["REF", "INT"]])
    assert card2["stats"]["REF"] == 6 and card2["stats"]["INT"] == 7


def test_more_than_three_swaps_rejected():
    with pytest.raises(roster.RuleError):
        roster.build(
            template="独狼",
            name="A",
            stat_swaps=[["REF", "INT"], ["DEX", "LUCK"], ["COOL", "WILL"], ["MOVE", "BODY"]],
        )


def test_unknown_template_rejected():
    with pytest.raises(roster.RuleError):
        roster.build(template="JEDI", name="A")


def test_skill_level_out_of_range_rejected():
    with pytest.raises(roster.RuleError):
        roster.build(template="独狼", name="A", skill_overrides={"Handgun": 11})


def test_patch_changes_skill():
    card = roster.build(template="独狼", name="老猫")
    updated = roster.apply_patch(card, {"skill_changes": {"Handgun": 8}})
    # 技能键统一成术语表中文名，英文/中文输入都归一
    assert updated["skills"]["手枪"] == 8
    assert "手枪" not in card["skills"]  # 原卡没被改


def test_patch_stat_out_of_range_rejected():
    card = roster.build(template="独狼", name="老猫")
    with pytest.raises(roster.RuleError):
        roster.apply_patch(card, {"stat_changes": {"BODY": 11}})


def test_patch_unknown_stat_rejected():
    card = roster.build(template="独狼", name="老猫")
    with pytest.raises(roster.RuleError):
        roster.apply_patch(card, {"stat_changes": {"STR": 5}})


def test_patch_rename_and_concept():
    card = roster.build(template="独狼", name="老猫")
    updated = roster.apply_patch(card, {"name": "夜莺", "concept": "沉默的枪手"})
    assert updated["name"] == "夜莺"
    assert updated["concept"] == "沉默的枪手"


def test_save_and_get_roundtrip():
    conn = db.connect(":memory:")
    card = roster.build(template="媒体人", name="记者")
    roster.save(conn, "g1", "111", card)
    loaded = roster.get(conn, "g1", "111")
    assert loaded["name"] == "记者"
    assert loaded["stats"] == card["stats"]
    assert roster.get(conn, "g1", "222") is None


def test_save_twice_updates_same_row():
    conn = db.connect(":memory:")
    card = roster.build(template="媒体人", name="记者")
    roster.save(conn, "g1", "111", card)
    roster.apply_patch(card, {"name": "老记"})
    card["name"] = "老记"
    roster.save(conn, "g1", "111", card)
    rows = conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
    assert rows == 1
    assert roster.get(conn, "g1", "111")["name"] == "老记"


def test_render_card_contains_key_lines():
    card = roster.build(template="独狼", name="老猫")
    text = roster.render_card(card)
    assert "老猫" in text and "独狼" in text
    assert "生命 40" in text and "重伤 20" in text


def test_templates_loaded():
    templates = roster.load_templates()
    assert len(templates) == 10
    names = {t["name"] for t in templates}
    assert {"独狼", "媒体人", "技工", "网行者"} <= names
