"""非战斗伤害（坠落/电击/溺水/燃烧/中毒）的测试。"""

from __future__ import annotations

import json

import pytest

from cyberpunk.dice import RandomRoller

from core import db, hazards, roster


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _card(conn, gid="qq:g1", qq="u1"):
    card = roster.build(template="独狼", name="测试员")
    roster.save(conn, gid, qq, card)
    return card


def _state(conn, gid="qq:g1", qq="u1"):
    row = conn.execute(
        "SELECT state FROM characters WHERE group_id = ? AND owner_qq = ?", (gid, qq)
    ).fetchone()
    return json.loads(row["state"] or "{}")


def test_fall_damage_floor_and_armor(conn):
    _card(conn)
    hz = hazards.fall(conn, "qq:g1", "u1", 12, roller=RandomRoller(seed=1))
    # 40 HP、无护甲 → 12 米 = 2D6
    assert 2 <= hz.damage <= 12
    assert hz.hp_before == 40
    assert hz.hp_after == 40 - hz.damage
    assert "断腿" in hz.note


def test_fall_saved_no_broken_leg(conn):
    _card(conn)
    hz = hazards.fall(conn, "qq:g1", "u1", 20, roller=RandomRoller(seed=2), saved=True)
    assert "没有断腿" in hz.note
    assert hz.damage > 0


def test_fall_below_ten_meters_still_hurts(conn):
    _card(conn)
    hz = hazards.fall(conn, "qq:g1", "u1", 5, roller=RandomRoller(seed=3))
    assert hz.damage >= 1
    assert "断腿" not in hz.note


def test_fall_from_skyscraper_kills(conn):
    _card(conn)
    hz = hazards.fall(conn, "qq:g1", "u1", 200, roller=RandomRoller(seed=4))
    assert hz.hp_after == 0
    assert "当场死亡" in hz.note


def test_fall_rejects_zero(conn):
    _card(conn)
    with pytest.raises(hazards.HazardError):
        hazards.fall(conn, "qq:g1", "u1", 0, roller=RandomRoller(seed=1))


def test_shock_is_six_d6(conn):
    _card(conn)
    hz = hazards.shock(conn, "qq:g1", "u1", roller=RandomRoller(seed=5))
    assert 6 <= hz.damage <= 36


def test_drown_uses_body_direct(conn):
    _card(conn)
    card = roster.build(template="独狼", name="力量型")
    body = card["stats"]["BODY"]
    hz = hazards.drown(conn, "qq:g1", "u1", roller=RandomRoller(seed=6))
    assert hz.damage == body  # 直接伤害 = BODY，不看护甲
    assert f"{body} 分钟" in hz.note


def test_burn_materials(conn):
    _card(conn)
    wood = hazards.burn(conn, "qq:g1", "u1", "木", roller=RandomRoller(seed=7))
    assert wood.damage == 2
    alu = hazards.burn(conn, "qq:g1", "u1", "铝热剂", roller=RandomRoller(seed=8))
    assert alu.damage == 6
    with pytest.raises(hazards.HazardError):
        hazards.burn(conn, "qq:g1", "u1", "纸", roller=RandomRoller(seed=9))


def test_poison_dice_by_grade(conn):
    _card(conn)
    light = hazards.poison(conn, "qq:g1", "u1", "轻", roller=RandomRoller(seed=10))
    assert 1 <= light.damage <= 6
    lethal = hazards.poison(conn, "qq:g1", "u1", "致命", roller=RandomRoller(seed=11))
    assert 3 <= lethal.damage <= 18
    with pytest.raises(hazards.HazardError):
        hazards.poison(conn, "qq:g1", "u1", "剧毒", roller=RandomRoller(seed=12))


def test_poison_resisted_no_damage(conn):
    _card(conn)
    hz = hazards.poison(
        conn, "qq:g1", "u1", "强", roller=RandomRoller(seed=13), resisted=True
    )
    assert hz.damage == 0
    assert "没有中毒" in hz.note


def test_hp_persists_across_calls(conn):
    _card(conn)
    hazards.poison(conn, "qq:g1", "u1", "轻", roller=RandomRoller(seed=14))
    after_first = _state(conn)["hp"]
    hazards.poison(conn, "qq:g1", "u1", "轻", roller=RandomRoller(seed=15))
    assert _state(conn)["hp"] < after_first


def test_easy_card_supported(conn):
    from core import easy_roster
    from cyberpunk import easy as easy_rules

    tpl = easy_rules.templates()[0]
    easy_roster.save(conn, "qq:g1", "u2", tpl)
    hz = hazards.shock(conn, "qq:g1", "u2", roller=RandomRoller(seed=16))
    row = conn.execute(
        "SELECT data FROM easy_cards WHERE group_id = 'qq:g1' AND owner_qq = 'u2'"
    ).fetchone()
    data = json.loads(row["data"])
    assert data["hp"] == hz.hp_after
    assert hz.hp_before == tpl["hp"]


def test_no_card_error(conn):
    with pytest.raises(hazards.HazardError):
        hazards.shock(conn, "qq:g1", "nobody", roller=RandomRoller(seed=17))


def test_match_and_apply_text(conn):
    _card(conn)
    for text, kind in (
        ("我从 12 米跳下去", "fall"),
        ("坠落 12 米", "fall"),
        ("被电了", "shock"),
        ("溺水", "drown"),
        ("着火了", "burn"),
        ("中毒 强", "poison"),
    ):
        assert hazards.match(text) == kind, text
    assert hazards.match("我看看控制台") is None
    assert hazards.match("我从二楼跳下去") is None  # 没写高度，交给模型当行动

    out = hazards.apply(conn, "qq:g1", "u1", "坠落 12 米", roller=RandomRoller(seed=18))
    assert "坠落" in out and "HP" in out
    out = hazards.apply(conn, "qq:g1", "u1", "吃个面包", roller=RandomRoller(seed=19))
    assert "没看懂" in out
