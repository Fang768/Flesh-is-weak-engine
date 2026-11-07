"""地图模块测试：加载、距离 DV、视线、寻路、渲染。"""

from __future__ import annotations

import pytest

from core import map as m


def test_ids_and_load():
    assert "warehouse" in m.ids()
    gm = m.load("warehouse")
    assert gm.width == 16 and gm.height == 9
    assert gm.cell == 2


def test_meters_and_bands():
    gm = m.load("warehouse")
    assert gm.meters((0, 0), (3, 0)) == 6
    assert gm.meters((0, 0), (3, 3)) == 6  # 对角线按格算
    assert gm.band(6) == 0
    assert gm.band(7) == 1
    assert gm.band(25) == 2
    assert gm.band(801) is None


def test_single_shot_dv():
    gm = m.load("dock")
    # 手枪：0-6 米 13 / 13-25 米 20
    assert gm.dv((1, 1), (2, 1), "手枪") == 13
    assert gm.dv((1, 1), (9, 1), "手枪") == 20  # 16 米
    assert gm.dv((1, 1), (2, 1), "火箭筒") == 17


def test_out_of_range_is_none():
    gm = m.load("dock")
    far = gm.dv((1, 10), (22, 1), "手枪")  # 42 米，手枪 26-50 还是 25
    assert far == 25
    # 100 米外手枪 N/A：直接查表
    assert m.SINGLE_DV["手枪"][6] is None


def test_autofire_dv():
    gm = m.load("dock")
    assert gm.dv((1, 1), (4, 1), "突击步枪", autofire=True) == 22  # 6 米 → 档 0
    assert gm.dv((1, 1), (6, 1), "突击步枪", autofire=True) == 20  # 10 米 → 档 1
    # 全自动表只到 100 米：造一张每格 20 米的图来测超距
    wide = m._parse(
        {"id": "wide", "name": "旷野", "terrain": ["." * 50, "." * 50, "." * 50], "cell": 20},
        "t.json",
    )
    assert wide.meters((1, 1), (40, 1)) == 780
    assert wide.dv((1, 1), (40, 1), "突击步枪", autofire=True) is None


def test_unknown_weapon_raises():
    gm = m.load("dock")
    with pytest.raises(m.MapError):
        gm.dv((1, 1), (2, 1), "激光剑")


def test_wall_blocks_los():
    gm = m.load("dock")
    assert gm.visible((14, 2), (20, 2)) is False
    assert gm.visible((14, 2), (14, 6)) is True


def test_cover_blocks_or_not():
    gm = m.load("dock")
    assert gm.visible((8, 1), (8, 5)) is False  # 油桶组挡视线
    wh = m.load("warehouse")
    assert wh.visible((8, 1), (8, 3)) is True  # 托盘不挡视线
    assert wh.cover_at((8, 2)).name == "低矮托盘"


def test_cover_destroyed_stops_blocking():
    gm = m.load("dock")
    barrel = gm.covers[0]
    gm.covers = [m.Cover(id=barrel.id, name=barrel.name, x=barrel.x, y=barrel.y, hp=0)]
    try:
        assert gm.visible((8, 1), (8, 5)) is True  # 打烂了就不挡了
        assert "已打烂" in gm.covers[0].line()
    finally:
        gm.covers = [barrel]  # load 有缓存，别污染其它用例


def test_path_avoids_water():
    gm = m.load("warehouse")
    route, cost = gm.path((4, 5), (7, 5))
    assert route[0] == (4, 5) and route[-1] == (7, 5)
    # 绕开积水（每格 2），对角走 3 步即可
    assert cost == 3
    assert not any(gm.at(p) == m.WATER for p in route)


def test_water_costs_double():
    gm = m.load("warehouse")
    assert gm.cost((5, 5)) == 2  # 积水
    assert gm.cost((8, 2)) == 1  # 地面（掩体不改地形）


def test_path_blocked_by_wall():
    gm = m.load("warehouse")
    route, cost = gm.path((1, 1), (1, 1))
    assert route == [(1, 1)] and cost == 0
    route, cost = gm.path((1, 1), (20, 1))  # 越界
    assert route == [] and cost == 0


def test_move_budget_insufficient():
    gm = m.load("dock")
    route, cost, reached = gm.move((2, 10), (21, 1), 3)
    assert reached is False
    assert cost <= 3
    assert route[0] == (2, 10)


def test_move_reaches_when_enough():
    gm = m.load("warehouse")
    route, cost, reached = gm.move((2, 7), (4, 7), 6)
    assert reached is True and cost == 2 and route[-1] == (4, 7)


def test_render_overlay():
    gm = m.load("warehouse")
    text = gm.render({(2, 7): "@", (13, 2): "E"})
    assert "三号仓库" in text
    assert "@" in text and "E" in text
    assert text.count(chr(10)) == gm.height + 2


def test_bearing():
    gm = m.load("warehouse")
    assert gm.bearing((2, 7), (2, 7)) == "就在跟前"
    assert "东" in gm.bearing((2, 7), (12, 7))
    assert "北" in gm.bearing((2, 7), (2, 1))


def test_parse_rejects_bad_map():
    with pytest.raises(m.MapError):
        m._parse({"id": "x", "name": "x"}, "t.json")
    with pytest.raises(m.MapError):
        m._parse({"id": "x", "name": "x", "terrain": ["##", "#"]}, "t.json")
    with pytest.raises(m.MapError):
        m._parse({"id": "x", "name": "x", "terrain": ["#Z#"]}, "t.json")


def test_load_missing_file():
    with pytest.raises(m.MapError):
        m.load("no-such-map")


def test_dv_table_text():
    assert "手枪" not in m.dv_table("手枪")  # 只列档位
    assert "0-6m:13" in m.dv_table("手枪")
    assert "没有" in m.dv_table("不存在")
