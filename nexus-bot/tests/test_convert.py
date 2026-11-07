"""V3 → Easy Mode 装备换算的测试。"""

from __future__ import annotations

from cyberpunk import convert


def test_parse_damage():
    assert convert.parse_damage("2D6+1") == (2, 1)
    assert convert.parse_damage("13D6") == (13, 0)
    assert convert.parse_damage("Stun") == (0, 0)


def test_light_damage_kept():
    assert convert.damage_to_easy("2D6+1") == "2D6+1"
    assert convert.damage_to_easy("4D6") == "4D6"
    assert convert.damage_to_easy("5D6+2") == "5D6+2"


def test_heavy_damage_compressed():
    assert convert.damage_to_easy("6D6+2") == "5D6+1"
    assert convert.damage_to_easy("10D6") == "7D6"
    assert convert.damage_to_easy("13D6") == "8D6"


def test_non_dice_text_passthrough():
    assert convert.damage_to_easy("Stun") == "Stun"
    assert convert.damage_to_easy("Drugs") == "Drugs"


def test_autofire_threshold():
    assert convert.is_autofire(30) is True
    assert convert.is_autofire(2) is False


def test_availability_and_conceal_map_to_dv():
    assert convert.availability_dv("E") == 13
    assert convert.availability_dv("U") == 24
    assert convert.conceal_dv("N") == 21


def test_malfunction_table():
    assert convert.malfunction_on("VR") == ()
    assert convert.malfunction_on("ST") == (1,)
    assert convert.malfunction_on("UR") == (1, 2)


def test_ev_penalty_steps():
    assert convert.ev_penalty(0) == 0
    assert convert.ev_penalty(2) == -1
    assert convert.ev_penalty(3) == -2
    assert convert.ev_penalty(9) == -3


def test_sp_scaled_and_capped():
    assert convert.sp_to_easy(10) == 7
    assert convert.sp_to_easy(25) == 18
    assert convert.sp_to_easy(0) == 0


def test_ip_cost():
    assert convert.ip_cost(4) == 8
    assert convert.ip_cost(0) == 1
