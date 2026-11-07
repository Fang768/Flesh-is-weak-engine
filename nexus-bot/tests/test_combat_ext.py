"""方案 A（战斗增补）的测试。"""

from __future__ import annotations

from cyberpunk import combat_ext as c


def test_derived_matches_card_scale():
    d = c.derived({"INT": 5, "REF": 6, "BODY": 8, "MOVE": 7})
    assert d.hp == 40
    assert d.wound == 20
    assert d.death_save == 8
    assert d.luck == 5
    assert d.run == 14
    assert d.carry == 80


def test_hit_location_bands():
    assert c.hit_location(1) is c.Location.HEAD
    assert c.hit_location(2) is c.Location.VITALS
    assert c.hit_location(3) is c.Location.VITALS
    assert c.hit_location(4) is c.Location.TORSO
    assert c.hit_location(6) is c.Location.TORSO
    assert c.hit_location(7) is c.Location.LIMB
    assert c.hit_location(10) is c.Location.LIMB


def test_location_multiplier():
    assert c.damage_after_location(10, c.Location.HEAD) == 30
    assert c.damage_after_location(10, c.Location.VITALS) == 20
    assert c.damage_after_location(10, c.Location.TORSO) == 10
    assert c.limb_penalty(c.Location.LIMB) == -2
    assert c.limb_penalty(c.Location.HEAD) == 0


def test_autofire_hits_capped_by_rounds():
    assert c.autofire_hits(5, 30) == 5
    assert c.autofire_hits(40, 30) == 30
    assert c.autofire_hits(0, 30) == 0
    assert c.autofire_hits(-3, 30) == 0


def test_autofire_distance_penalty():
    # 30 发，远一档 → 每 10 发 −1 → 上限 27
    assert c.autofire_hits(40, 30, distance_band=1) == 27


def test_suppression_examples():
    assert c.suppression_save(64, 2) == 32
    assert c.suppression_save(64, 5) == 12
    assert c.suppression_save(10, 0) == 10


def test_effective_sp_halves_once():
    assert c.effective_sp(18) == 18
    assert c.effective_sp(18, ap=True) == 9
    assert c.effective_sp(18, melee=True) == 9
    assert c.effective_sp(18, ap=True, melee=True) == 9  # 不叠两次
    assert c.effective_sp(0, ap=True) == 0


def test_malfunction_by_reliability():
    assert c.is_malfunction(1, "VR") is False
    assert c.is_malfunction(1, "ST") is True
    assert c.is_malfunction(2, "ST") is False
    assert c.is_malfunction(2, "UR") is True
    assert c.is_malfunction(3, "UR") is False


def test_derived_accepts_card_hp():
    """简易版 HP 写在卡面：肆拾 BODY3→40、火炬 BODY6→35。"""
    rocker = c.derived({"BODY": 3, "INT": 5, "REF": 6, "MOVE": 7}, hp=40)
    assert (rocker.hp, rocker.wound, rocker.death_save) == (40, 20, 3)
    tech = c.derived({"BODY": 6, "INT": 8, "REF": 7, "MOVE": 6}, hp=35)
    assert (tech.hp, tech.wound, tech.death_save) == (35, 17, 6)
    fallback = c.derived({"BODY": 7, "INT": 7, "REF": 7, "MOVE": 7})
    assert fallback.hp == 35 and fallback.death_save == 7
