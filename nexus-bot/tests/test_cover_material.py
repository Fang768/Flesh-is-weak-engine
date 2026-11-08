"""掩体材质（方案4）：钢制掩体不能被体格 <10 且无赛博臂的武术/搏击攻击损伤。

规则依据（``rules/red_core/raw/full.txt``）：
- :15462-15464「厚的掩体对于体格属性小于 10 点的角色来说太难搞了，除非这个角色
  有特殊装备。钢制掩体不能被体格低于 10 点或者没有赛博臂的角色的武术或者斗殴
  攻击所损伤。」（书内页 183）
- 掩体种类表 :15473-15480（钢制厚 50HP/薄 25HP、石制 40/20、防弹玻璃 30/15、
  混凝土 25/10、木制 20/5、石灰泡沫塑料 15/0HP 不算掩体）。

「约定」：
- 材质写在地图 objects 的 `material` 字段（钢/石/混凝土/木…），缺省空串按普通掩体算。
- 只有武术/搏击（近战徒手/小武器）受这条限制；拿枪或攻城锤等硬打不在此限制内，
  由 DM 现场判断，本引擎只拦「徒手砸钢」这种明确写死的组合。
"""

import json

from core import combat as C
from core import map as M
from core import roster
from core.db import connect
from cyberpunk import dice

GID = "qq:covermat"

import pytest

# 手工塞一张地图，掩体是钢制的；`attack` 用 `_map_for(combat)` → `_map_for_id(map_id)`
# 从文件加载，测不了内联 json，所以用 autouse fixture patch `_map_for_id`（只对本模块生效）。
_TEST_MAP = M._parse({
    "id": "t", "name": "巷战",
    "terrain": ["....", "...."],
    "legend": {'.': '地'},
    "spawns": {"pc": (0, 0), "enemy": (3, 0)},
    "objects": [
        {"id": "steel", "kind": "cover", "name": "钢墩",
         "x": 1, "y": 0, "hp": 50, "material": "钢"},
    ],
}, "test")


@pytest.fixture(autouse=True)
def _map_patch(monkeypatch):
    """让 `attack` 里的 `_map_for_id` 返回本方的手工地图（每个测试自动启停）。"""
    import core.combat as cc
    monkeypatch.setattr(cc, "_map_for_id", lambda _map_id: _TEST_MAP)


def _connect():
    return connect(":memory:")


def _make_combat(conn, *, body=8, cyberarm=False, skill="搏击"):
    roster.save(conn, GID, "1001",
                roster.build(template="独狼", name="猛男",
                             skill_overrides={skill: 5}))
    if cyberarm:
        # 卡上要真带赛博臂，`attack` 加载时才会从卡重算 tags（卡片 schema 没有
        # 独立的 cyber 列，赛博件和装备一样放 gear）。
        card = roster.get(conn, GID, "1001")
        card["gear"] = (card.get("gear") or []) + [{"name": "赛博臂", "type": "赛博义肢"}]
        roster.save(conn, GID, "1001", card)
    C.start(conn, GID, enemies=(("打手", 1),), scene="a1.opera",
            roller=dice.ScriptedRoller([9, 2]))
    f = C.load(conn, GID)
    me = next(c for c in f.combatants if c.side == "pc")
    me.body = body
    me.x, me.y = 0, 0  # 站到地图上（场景无 map，否则打掩体分支会「你还没站到地图上」）
    C._save(conn, f)
    return f, me


def _hit_cover(conn, body=8, cyberarm=False, skill="搏击"):
    f, me = _make_combat(conn, body=body, cyberarm=cyberarm, skill=skill)
    f.turn_index = f.order.index(me)
    f.round += 1
    C._reset_slots(f)
    C._save(conn, f)
    return C.attack(conn, GID, "1001", "钢墩", skill=skill,
                    roller=dice.ScriptedRoller([10, 3, 3]))


def _cover_hp(conn):
    f = C.load(conn, GID)
    return f.covers.get("steel", 50)


def test_cover_default_material_unknown():
    """材质缺省 = 普通掩体，不触发钢制限制。"""
    conn = _connect()
    roster.save(conn, GID, "1001", roster.build(template="独狼", name="x"))
    game = M._parse({
        "id": "t", "name": "t",
        "terrain": ["...."], "legend": {".": "地"},
        "spawns": {"pc": (0, 0), "enemy": (3, 0)},
        "objects": [{"id": "c", "kind": "cover", "name": "木板",
                     "x": 1, "y": 0, "hp": 20}],
    }, "test")
    assert game.covers[0].material == ""


def test_cover_material_parsed():
    conn = _connect()
    roster.save(conn, GID, "1001", roster.build(template="独狼", name="x"))
    game = M._parse({
        "id": "t", "name": "t",
        "terrain": ["...."], "legend": {".": "地"},
        "spawns": {"pc": (0, 0), "enemy": (3, 0)},
        "objects": [{"id": "s", "kind": "cover", "name": "钢墩",
                     "x": 1, "y": 0, "hp": 50, "material": "钢"}],
    }, "test")
    assert game.covers[0].material == "钢"


def test_brawler_low_body_cannot_damage_steel():
    """体格 8、无赛博臂的搏击手打不穿钢制掩体。"""
    conn = _connect()
    text = _hit_cover(conn, body=8, cyberarm=False, skill="搏击")
    assert "伤不了它" in text
    assert _cover_hp(conn) == 50  # 耐久没掉


def test_brawler_body10_can_damage_steel():
    """体格 ≥10 的搏击手能打钢制掩体。"""
    conn = _connect()
    # 搏击命中 → 伤害 2D6=6，掩体 50 → 44
    text = _hit_cover(conn, body=10, cyberarm=False, skill="搏击")
    assert "伤不了它" not in text
    assert _cover_hp(conn) == 44


def test_brawler_cyberarm_can_damage_steel():
    """有赛博臂、体格 <10 的搏击手也能打钢。"""
    conn = _connect()
    text = _hit_cover(conn, body=8, cyberarm=True, skill="搏击")
    assert "伤不了它" not in text
    assert _cover_hp(conn) == 44


def test_melee_weapon_not_brawl_ignores_material_limit():
    """武术以外的近战技能（如近战武器）不受「钢制不可徒手砸」限制。"""
    conn = _connect()
    text = _hit_cover(conn, body=8, cyberarm=False, skill="近战武器")
    assert "伤不了它" not in text


def test_ranged_gun_damages_steel():
    """用枪打钢制掩体不受限制。"""
    conn = _connect()
    text = _hit_cover(conn, body=8, cyberarm=False, skill="手枪")
    assert "伤不了它" not in text
    assert _cover_hp(conn) == 44


def test_has_cyberarm_via_tags():
    import core.combat as cc
    f, _ = _make_combat(_connect(), body=8, cyberarm=True)
    assert cc._has_cyberarm(f.combatants[0]) is True
    f2, _ = _make_combat(_connect(), body=8, cyberarm=False)
    assert cc._has_cyberarm(f2.combatants[0]) is False