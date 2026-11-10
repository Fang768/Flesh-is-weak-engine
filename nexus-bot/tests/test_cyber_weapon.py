"""义体武器：档位伤害覆盖 + 藏匿。

档位表（``rules/red_core/raw/full.txt:14976-15027``）：

| 档位 | 伤害 | ROF | 能否藏匿 |
|---|---|---|---|
| 轻型 | 1D6 | 2 | 能 |
| 中型 | 2D6 | 2 | 不能 |
| 重型 | 3D6 | 2 | 不能 |
| 超重型 | 4D6 | 1 | 不能 |

**覆盖语义**（主人拍板）：装了义体武器，用它的档位骰，**不叠**技能默认值。
引擎里 `combat.WEAPON_DAMAGE` 是「近战武器 2D6」，装了赛博大蛇就打 4D6。
"""

from __future__ import annotations

import pytest

from core import combat as C
from core import db, roster
from cyberpunk import cyber, cyber_weapon as cw, dice

GID = "qq:cyberweapon"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, cyber_items=(), qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = {"cyber": list(cyber_items)}
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


# ---------- 数据自检 ----------


def test_tier_table_matches_the_book():
    assert cw.TIER_DAMAGE == {"轻型": "1D6", "中型": "2D6",
                              "重型": "3D6", "超重型": "4D6"}
    assert cw.TIER_ROF == {"轻型": 2, "中型": 2, "重型": 2, "超重型": 1}


def test_all_weapon_names_exist_in_hl_table():
    table = cyber.hl_table()
    for name in cw.items():
        assert name in table, f"武器表里的「{name}」不在 cyberware_hl"


def test_every_weapon_has_a_usable_damage():
    """每条要么给档位，要么给显式 damage，要么走手榴弹结算。

    非武器（盾牌/握把/枪套/口袋）与榴弹发射器（伤害走 6D6 手榴弹）不算。
    """
    non_weapons = {"弹出式盾牌", "皮下握把", "隐藏枪套", "皮下口袋"}
    for name in cw.items():
        if name in non_weapons:
            continue
        rule = cw.item_rule(name)
        if rule.get("grenade"):
            assert cw.damage_of(name) is None, f"{name} 不该按档位算"
            continue
        assert rule.get("tier") or rule.get("damage"), f"{name} 没有伤害来源"
        assert cw.damage_of(name), f"{name} 算不出伤害"


# ---------- 档位 → 伤害 ----------


@pytest.mark.parametrize("name,tier,damage", [
    ("赛博大蛇", "超重型", "4D6"),
    ("金刚狼爪", "重型", "3D6"),
    ("开膛手", "中型", "2D6"),
    ("大指虎", "中型", "2D6"),
    ("“切削通”", "中型", "2D6"),
    ("吸血鬼尖牙", "轻型", "1D6"),
    ("刮刮乐", "轻型", "1D6"),
    ("剃刀足", "轻型", "1D6"),
])
def test_melee_weapons_use_book_tiers(name, tier, damage):
    assert cw.item_rule(name)["tier"] == tier
    assert cw.damage_of(name) == damage


def test_popup_melee_defaults_to_medium():
    """弹出式近战武器不指定具体武器时按中型 2D6。"""
    assert cw.item_rule("弹出式近战武器")["tier"] == "依赖"
    assert cw.damage_of("弹出式近战武器") == "2D6"


def test_shield_and_smartgrip_are_not_weapons():
    for name in ("弹出式盾牌", "皮下握把", "隐藏枪套", "皮下口袋"):
        assert cw.damage_of(name) is None, name


# ---------- 选取逻辑 ----------


def test_no_weapon_means_no_override():
    assert cw.best_weapon({"state": {"cyber": []}}) is None
    assert cw.damage_override({"state": {"cyber": []}}) == (None, "")


def test_best_weapon_picks_highest_damage():
    card = {"state": {"cyber": ["吸血鬼尖牙", "赛博大蛇", "开膛手"]}}
    name, dmg = cw.best_weapon(card)
    assert name == "赛博大蛇" and dmg == "4D6"


def test_best_weapon_filters_by_skill():
    card = {"state": {"cyber": ["赛博大蛇", "弹出式远程武器"]}}
    melee = cw.best_weapon(card, skill="近战武器")
    ranged = cw.best_weapon(card, skill="手枪")
    assert melee[0] == "赛博大蛇"
    assert ranged[0] == "弹出式远程武器"


def test_explicit_weapon_wins():
    card = {"state": {"cyber": ["赛博大蛇", "吸血鬼尖牙"]}}
    dmg, note = cw.damage_override(card, skill="近战武器", weapon="吸血鬼尖牙")
    assert dmg == "1D6" and "吸血鬼尖牙" in note


def test_unknown_explicit_weapon_falls_back_to_none():
    card = {"state": {"cyber": ["赛博大蛇"]}}
    assert cw.damage_override(card, weapon="不存在的武器") == (None, "")


# ---------- 藏匿 ----------


def test_book_confirmed_concealable_weapons():
    for name in ("赛博大蛇", "吸血鬼尖牙", "金刚狼爪", "开膛手",
                 "大指虎", "刮刮乐", "剃刀足", "“切削通”", "射镖枪"):
        assert cw.item_rule(name)["conceal"] is True, name


def test_popup_weapons_are_concealable():
    """书里明写「即使是通常状态下无法藏匿的」（:10265、:10282）。"""
    for name in ("弹出式近战武器", "弹出式远程武器",
                 "弹出式榴弹发射器", "弹出式盾牌"):
        assert cw.item_rule(name)["conceal"] is True, name


def test_conceal_channel_bonus():
    assert cw.conceal_bonus({"state": {"cyber": ["隐藏枪套"]}}) == 2
    assert cw.conceal_bonus({"state": {"cyber": ["皮下口袋"]}}) == 1
    assert cw.conceal_bonus({"state": {"cyber": ["隐藏枪套", "皮下口袋"]}}) == 3


def test_concealed_weapons_lists_only_weapons():
    card = {"state": {"cyber": ["赛博大蛇", "隐藏枪套", "瞄准目镜"]}}
    assert cw.concealed_weapons(card) == ["赛博大蛇"]


# ---------- 战斗接线：覆盖而非叠加 ----------


def _fight(conn, cyber_items):
    _pc(conn, cyber_items)
    return C.start(conn, GID, enemies=(("打手", 1),), roller=dice.RandomRoller())


def _hit_once(conn, *, tries=40):
    """反复攻击直到命中，返回命中文本。"""
    for _ in range(tries):
        text = C.attack(conn, GID, "u1", "打手", skill="近战武器",
                        roller=dice.RandomRoller())
        if "义体武器" in text or "命中：" in text:
            return text
    return ""


def test_combat_uses_cyber_weapon_damage(conn):
    _fight(conn, ["赛博大蛇"])
    text = _hit_once(conn)
    assert "赛博大蛇（4D6）" in text, text


def test_combat_shows_cyber_weapon_source(conn):
    _fight(conn, ["赛博大蛇"])
    text = _hit_once(conn)
    assert "义体武器：" in text


def test_no_cyber_weapon_keeps_skill_damage(conn):
    _fight(conn, [])
    text = _hit_once(conn)
    assert "义体武器" not in text


def test_cyber_weapon_is_override_not_addition(conn):
    """4D6 覆盖 2D6，不是 6D6：单次伤害上限应是 24 而不是 36。"""
    _pc(conn, ["赛博大蛇"])
    best = 0
    seen = 0
    for _ in range(200):
        if C.load(conn, GID) is None or not C.load(conn, GID).active:
            C.start(conn, GID, enemies=(("打手", 1),), roller=dice.RandomRoller())
        try:
            text = C.attack(conn, GID, "u1", "打手", skill="近战武器",
                            roller=dice.RandomRoller())
        except C.CombatError:
            break
        if "义体武器：赛博大蛇（4D6）：" in text:
            raw = text.split("（4D6）：")[1].split(" 点")[0]
            best = max(best, int(raw))
            seen += 1
            if seen >= 8:
                break
    assert seen, "一次都没命中，测试没有说服力"
    assert best <= 24, f"出现 {best} 点，说明叠加了"


def test_ranged_skill_not_affected_by_melee_cyberweapon(conn):
    """近战义体武器不该改变远程伤害。"""
    _fight(conn, ["赛博大蛇"])
    text = C.attack(conn, GID, "u1", "打手", skill="手枪",
                    roller=dice.RandomRoller())
    assert "赛博大蛇" not in text


# ---------- 面板 ----------


def test_describe_lists_weapons():
    lines = cw.describe({"state": {"cyber": ["赛博大蛇", "隐藏枪套"]}})
    assert any("赛博大蛇" in x and "4D6" in x for x in lines)
    assert any("藏匿通道" in x for x in lines)


def test_describe_empty():
    assert cw.describe({"state": {"cyber": []}}) == []
