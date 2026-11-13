"""安装弹出式武器：装的是**市面上的具体武器**，数值用那把武器自己的。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:10261-10269`` 近战：「任何轻型，中型，或重型武器都能被安装于一条赛博手臂中」
* ``:10278-10286`` 远程：「任何**单手持握**的远程武器」
* ``:521`` FAQ：连**异种近战武器**的规格也能装

关键点：以前按「轻型/中型/重型」三个抽象档位取 1D6/2D6/3D6 是错的——
书里的机制是「指定具体武器，用那把武器的数值」。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, db, growth, roster
from cyberpunk import cyber_weapon as cw, dice, weapon_catalog as wc

GID = "qq:popup-install"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, *, arms=1, eb=50000, qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = dict(card.get("state") or {})
    card["state"]["eb"] = eb
    card["state"]["cyber"] = [
        {"id": f"c{i}", "name": "赛博手臂", "parent": ""} for i in range(1, arms + 1)]
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _mounts(conn, qq="u1"):
    return cw.mounts_of(roster.get(conn, GID, qq))


# ---------- 需要手臂 ----------


def test_popup_needs_a_cyber_arm(conn):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    card["state"]["eb"] = 50000
    roster.save(conn, GID, "u1", card)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 战斗手套",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "需要先有" in reply and "赛博手臂" in reply


# ---------- 必须说清是哪把武器 ----------


def test_install_without_weapon_name_asks(conn):
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器")
    assert "要装哪把武器" in reply
    assert "轻型近战武器" in reply          # 给出该位置的可选清单


def test_install_with_unknown_weapon_lists_candidates(conn):
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 光剑")
    assert "目录里没有" in reply


def test_install_rejects_superheavy_melee(conn):
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 超重型近战武器",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "超重型" in reply and "10261-10269" in reply
    assert _mounts(conn) == {}


def test_install_rejects_two_handed_ranged(conn):
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式远程武器 突击步枪",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "只手" in reply and "10280" in reply
    assert _mounts(conn) == {}


def test_install_rejects_wrong_kind(conn):
    """把手枪塞进近战位。"""
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 中型手枪",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "不是近战武器" in reply


# ---------- 正常路径：装进去的是具体武器 ----------


def test_install_exotic_melee_uses_its_own_stats(conn):
    """战斗手套（异种/重型）→ 3d6、2ROF，不是「重型档 3D6」的巧合。"""
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 战斗手套",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "装上了" in reply
    card = roster.get(conn, GID, "u1")
    assert _mounts(conn)["弹出式近战武器"] == "战斗手套"
    assert cw.damage_of("弹出式近战武器", card) == "3d6"
    assert cw.rof_of("弹出式近战武器", card) == 2


def test_install_one_handed_ranged_uses_its_own_stats(conn):
    """超重型手枪 → 4D6 / 1ROF（模板里就是「弹出式远程武器（超重型手枪）」）。"""
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式远程武器 超重型手枪",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "装上了" in reply
    card = roster.get(conn, GID, "u1")
    assert _mounts(conn)["弹出式远程武器"] == "超重型手枪"
    assert cw.damage_of("弹出式远程武器", card) == "4D6"
    assert cw.rof_of("弹出式远程武器", card) == 1


def test_two_different_ranged_weapons_give_different_damage(conn):
    """同样的安装位、装不同武器 → 伤害不同。这正是旧「档位」做不到的。"""
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 弹出式远程武器 中型手枪",
                       roller=dice.ScriptedRoller([1, 1]))
    card = roster.get(conn, GID, "u1")
    assert cw.damage_of("弹出式远程武器", card) == "2D6"

    card["state"]["cyber_mounts"] = {"弹出式远程武器": "马洛里安军火3516型手枪"}
    assert cw.damage_of("弹出式远程武器", card) == "5D6"


def test_install_accepts_names_with_extra_spaces(conn):
    _pc(conn)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 金田一 03 式单分子武士刀",
                               roller=dice.ScriptedRoller([1, 1]))
    # 金田一是超重型 → 应当被拒（但认得出这把武器，不是「目录里没有」）
    assert "超重型" in reply


def test_install_still_pays_and_costs_humanity(conn):
    _pc(conn, eb=1000)
    before = cyber_flow.humanity_of(roster.get(conn, GID, "u1"), False)
    cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 电击棍",
                       roller=dice.ScriptedRoller([1, 1]))
    card = roster.get(conn, GID, "u1")
    assert growth.eb(card) == 500          # 弹出式固定 500 EB
    assert cyber_flow.humanity_of(card, False) < before


def test_install_refused_when_broke(conn):
    _pc(conn, eb=10)
    reply = cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 电击棍",
                               roller=dice.ScriptedRoller([1, 1]))
    assert "装不起" in reply
    assert _mounts(conn) == {}


# ---------- 面板 ----------


def test_panel_shows_the_actual_weapon(conn):
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 弹出式近战武器 战斗手套",
                       roller=dice.ScriptedRoller([1, 1]))
    text = cyber_flow.status_text(conn, GID, "u1")
    assert "战斗手套" in text
    assert "3d6" in text
    assert "可藏匿" in text


def test_popup_weapon_is_concealable_even_if_usually_not(conn):
    """超重型手枪在核心表里 conceal=False，但弹出式装上去就能藏（:10265）。"""
    _pc(conn)
    cyber_flow.install(conn, GID, "u1", "安装 弹出式远程武器 超重型手枪",
                       roller=dice.ScriptedRoller([1, 1]))
    card = roster.get(conn, GID, "u1")
    assert wc.find("超重型手枪")["conceal"] is False
    assert "弹出式远程武器" in cw.concealed_weapons(card)
