"""商店：买、卖、穿脱护甲、夜市。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:579`` 没掮客：「您可以购买 100 欧元以下的物品。」
* ``:583-595`` 掮客 3 级 <500、7 级 <1000、9 级 <5000、10 级任何东西
* ``:602``「只要它还能用，你就可以按照它的原价出售。」
* ``:608-611`` 砍价对抗：赢了拿底价，输了被砍到 80%–90%
* ``:25529`` 夜市生成器：2d6 品类 → 1d10 种类数 → d100 具体商品
* ``:8530-8620`` 护甲是一整套、SP 减伤

**约定（主人 2026-11-13）**：不设背包上限（交 DM 判断）；护甲随时可穿、
一次只穿一套；卖默认原价，说「砍价」才走对抗。
"""

from __future__ import annotations

import random

import pytest

from core import db, easy_roster, growth, roster, shop_flow
from cyberpunk import dice

GID = "qq:shop2"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, *, eb=50000, broker=0, qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    card["state"] = dict(card.get("state") or {})
    card["state"]["eb"] = eb
    card["state"]["broker_level"] = broker
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


# ---------- 目录：不建商店专属清单 ----------


def test_catalog_is_built_from_existing_files():
    """商品来自武器目录 + 装备目录，没有第三份清单。"""
    kinds = {shop_flow.kind_of(i) for i in shop_flow.items()}
    assert "护甲" in kinds and "通用装备" in kinds
    assert "远程武器" in kinds or "近战武器" in kinds


def test_weapons_are_not_duplicated_in_gear_catalog():
    """同一把武器只能有一个权威来源（武器在 weapons_catalog）。"""
    from cyberpunk import weapon_catalog

    weapon_names = {i["name"] for i in weapon_catalog.items()}
    gear_names = {i["name"] for i in shop_flow._gear()}
    assert not (weapon_names & gear_names), "武器不该同时出现在 gear_catalog"


def test_every_item_has_price_and_tier():
    tiers = shop_flow.tiers()
    for it in shop_flow.items():
        assert int(it["euro"]) > 0, it["name"]
        assert it["tier"] in tiers, f"{it['name']} 的档位 {it.get('tier')} 不在八档里"


def test_find_handles_punctuation_and_spaces():
    assert shop_flow.find("弓 & 十字弩")["name"] == "弓&十字弩"
    assert shop_flow.find("根本不存在的武器xyz") is None


# ---------- 渠道：掮客档位（:579-595） ----------


def test_brokerless_only_under_hundred(conn):
    _pc(conn, eb=50000)
    assert "买不到" in shop_flow.buy(conn, GID, "u1", "买 防弹背心")   # 500
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 中型手枪")     # 50


def test_broker_level_three_under_500(conn):
    _pc(conn, eb=50000, broker=3)
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 轻型装甲夹克")  # 100
    assert "买不到" in shop_flow.buy(conn, GID, "u1", "买 防弹背心")    # 500（不低于）


def test_broker_level_seven_under_1000(conn):
    _pc(conn, eb=50000, broker=7)
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 防弹背心")      # 500
    assert "买不到" in shop_flow.buy(conn, GID, "u1", "买 附甲式接入套装")  # 1000


def test_broker_level_nine_under_5000(conn):
    _pc(conn, eb=50000, broker=9)
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 附甲式接入套装")  # 1000
    # 正好 5000 的合金装备：书里是「低于 5000」
    assert "买不到" in shop_flow.buy(conn, GID, "u1", "买 合金装备")


def test_broker_level_ten_buys_anything(conn):
    """``:595``「您可以买到任何东西，包括超奢分类的商品」。"""
    _pc(conn, eb=50000, broker=10)
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 合金装备")      # 5000


# ---------- 买 ----------


def test_buy_deducts_and_adds_gear(conn):
    _pc(conn, eb=500)
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 中型手枪")
    card = roster.get(conn, GID, "u1")
    assert growth.eb(card) == 450
    assert "中型手枪" in card["gear"]


def test_no_gear_cap(conn):
    """主人 2026-11-13：不设背包上限，交 DM 判断（:约定）。"""
    _pc(conn, eb=50000)
    for _ in range(15):
        shop_flow.buy(conn, GID, "u1", "买 食物棒")
        # 同名的不会重复加，换成别的东西来撑数量
    card = roster.get(conn, GID, "u1")
    names = ["荧光棒", "照明棒", "记忆芯片", "即食餐（MRE）", "强力胶带",
             "手电筒", "旅行包", "开锁工具组", "个人卫生包", "绳子（60 米）",
             "荧光涂料", "防烟呼吸面罩", "充气床垫&睡袋", "中型手枪"]
    for n in names:
        shop_flow.buy(conn, GID, "u1", f"买 {n}")
    card = roster.get(conn, GID, "u1")
    assert len(card["gear"]) > 12, f"不该再有 12 件上限，实际 {len(card['gear'])}"


def test_buy_without_money(conn):
    _pc(conn, eb=10)
    assert "买不起" in shop_flow.buy(conn, GID, "u1", "买 中型手枪")


def test_buy_unknown_item(conn):
    _pc(conn)
    assert "货架上没有" in shop_flow.buy(conn, GID, "u1", "买 光剑")


def test_buy_empty(conn):
    _pc(conn)
    assert "要买什么" in shop_flow.buy(conn, GID, "u1", "买")


def test_buy_without_card(conn):
    assert "还没有角色卡" in shop_flow.buy(conn, GID, "nobody", "买 中型手枪")


def test_inventory_check_defaults_to_allow(conn):
    """留的接口现在恒放行；返回 False 时才拦。"""
    import asyncio

    _pc(conn, eb=500)
    card = roster.get(conn, GID, "u1")
    entry = shop_flow.find("中型手枪")
    verdict = asyncio.run(shop_flow.inventory_check(card, entry, None))
    assert verdict == (None, "")
    assert shop_flow.inventory_allows(verdict)

    # 显式传 False 时才拒绝，且不改状态
    before = growth.eb(roster.get(conn, GID, "u1"))
    reply = shop_flow.buy(conn, GID, "u1", "买 中型手枪", inventory_ok=False)
    assert "带不下" in reply
    assert growth.eb(roster.get(conn, GID, "u1")) == before


# ---------- 夜市模式（:25529） ----------


def test_market_for_reads_scene():
    assert shop_flow.market_for(None) is None
    assert shop_flow.market_for({}) is None
    m = {"items": ["中型手枪"]}
    assert shop_flow.market_for({"market": m}) == m


def test_market_limits_the_shelf(conn):
    _pc(conn, eb=50000)
    m = {"categories": ["武器和护甲"], "items": ["中型手枪"]}
    assert "买了" in shop_flow.buy(conn, GID, "u1", "买 中型手枪", market=m)
    assert "不卖" in shop_flow.buy(conn, GID, "u1", "买 重型手枪", market=m)


def test_roll_market_picks_two_categories():
    """2d6 掷两次、相同则重掷 → 恰好两个不同品类（:25529）。"""
    for seed in range(30):
        m = shop_flow.roll_market(random.Random(seed))
        cats = m["categories"]
        assert len(cats) == 2, m
        assert cats[0] != cats[1], cats


def test_roll_market_is_deterministic_per_seed():
    a = shop_flow.roll_market(random.Random(7))
    b = shop_flow.roll_market(random.Random(7))
    assert a == b


# ---------- 卖（:602 / :608-611） ----------


def test_sell_returns_full_price_by_default(conn):
    _pc(conn, eb=500)
    shop_flow.buy(conn, GID, "u1", "买 中型手枪")
    reply = shop_flow.sell(conn, GID, "u1", "卖 中型手枪")
    assert "卖掉" in reply and "原价" in reply
    card = roster.get(conn, GID, "u1")
    assert growth.eb(card) == 500
    assert "中型手枪" not in card["gear"]


def test_sell_haggle_win_gets_full_price(conn):
    _pc(conn)
    card = roster.get(conn, GID, "u1")
    before = growth.eb(card)
    card["gear"] = ["中型手枪"]
    roster.save(conn, GID, "u1", card)
    reply = shop_flow.sell(conn, GID, "u1", "卖 中型手枪 砍价", haggle=True,
                           roller=dice.ScriptedRoller([10, 1]))
    assert "砍价赢了" in reply
    assert growth.eb(roster.get(conn, GID, "u1")) == before + 50


def test_sell_haggle_lose_gets_80_to_90_percent(conn):
    _pc(conn)
    card = roster.get(conn, GID, "u1")
    card["gear"] = ["中型手枪"]
    roster.save(conn, GID, "u1", card)
    before = growth.eb(card)
    reply = shop_flow.sell(conn, GID, "u1", "卖 中型手枪 砍价", haggle=True,
                           roller=dice.ScriptedRoller([1, 10]))
    assert "砍价输了" in reply
    gained = growth.eb(roster.get(conn, GID, "u1")) - before
    # 50 欧元被压到 80%–90% → 40 或 45
    assert 40 <= gained <= 45, reply


def test_haggle_tail_is_stripped_from_item_name(conn):
    """「卖 X 砍价」不该把「砍价」当成物品名的一部分。"""
    _pc(conn)
    card = roster.get(conn, GID, "u1")
    card["gear"] = ["中型手枪"]
    roster.save(conn, GID, "u1", card)
    reply = shop_flow.sell(conn, GID, "u1", "卖 中型手枪 砍价", haggle=True,
                           roller=dice.ScriptedRoller([6, 3]))
    assert "身上没有" not in reply
    assert "卖掉「中型手枪」" in reply


def test_sell_not_held(conn):
    _pc(conn)
    assert "身上没有" in shop_flow.sell(conn, GID, "u1", "卖 合金装备")


def test_sell_unknown_price_item(conn):
    _pc(conn, eb=500)
    card = roster.get(conn, GID, "u1")
    card["gear"] = ["DM 给的纪念章"]
    roster.save(conn, GID, "u1", card)
    assert "没有进账" in shop_flow.sell(conn, GID, "u1", "卖 DM 给的纪念章")
    assert growth.eb(roster.get(conn, GID, "u1")) == 500


# ---------- 穿脱护甲（:8530-8620） ----------


def test_buy_armor_then_wear_it(conn):
    _pc(conn, eb=5000, broker=7)
    shop_flow.buy(conn, GID, "u1", "买 防弹背心")
    reply = shop_flow.wear(conn, GID, "u1", "穿上 防弹背心")
    assert "穿上了" in reply and "SP 15" in reply
    card = roster.get(conn, GID, "u1")
    assert card["state"]["armor"]["sp"] == 15


def test_worn_armor_reaches_rules_engine(conn):
    """买护甲若不影响 SP，买它就没意义——这条钉住接线。"""
    _pc(conn, eb=5000, broker=7)
    shop_flow.buy(conn, GID, "u1", "买 防弹背心")
    shop_flow.wear(conn, GID, "u1", "穿上 防弹背心")
    rules = roster.to_character(roster.get(conn, GID, "u1"))
    assert rules.armor.sp == 15


def test_no_armor_means_zero_sp(conn):
    _pc(conn)
    rules = roster.to_character(roster.get(conn, GID, "u1"))
    assert rules.armor.sp == 0


def test_wear_swaps_only_one_set(conn):
    """书里护甲是一整套，一次只穿一套。"""
    _pc(conn, eb=5000, broker=7)
    shop_flow.buy(conn, GID, "u1", "买 防弹背心")
    shop_flow.buy(conn, GID, "u1", "买 皮衣")
    shop_flow.wear(conn, GID, "u1", "穿上 皮衣")
    reply = shop_flow.wear(conn, GID, "u1", "穿上 防弹背心")
    assert "换下了「皮衣」" in reply
    card = roster.get(conn, GID, "u1")
    assert card["state"]["armor"]["name"] == "防弹背心"


def test_wear_something_not_armor(conn):
    _pc(conn, eb=500)
    shop_flow.buy(conn, GID, "u1", "买 中型手枪")
    assert "不是护甲" in shop_flow.wear(conn, GID, "u1", "穿上 中型手枪")


def test_wear_item_not_held(conn):
    _pc(conn)
    assert "身上没有" in shop_flow.wear(conn, GID, "u1", "穿上 合金装备")


def test_takeoff(conn):
    _pc(conn, eb=5000, broker=7)
    shop_flow.buy(conn, GID, "u1", "买 防弹背心")
    shop_flow.wear(conn, GID, "u1", "穿上 防弹背心")
    assert "脱下了" in shop_flow.takeoff(conn, GID, "u1")
    assert "armor" not in (roster.get(conn, GID, "u1")["state"] or {})
    assert "没穿护甲" in shop_flow.takeoff(conn, GID, "u1")


def test_selling_worn_armor_takes_it_off(conn):
    _pc(conn, eb=5000, broker=7)
    shop_flow.buy(conn, GID, "u1", "买 防弹背心")
    shop_flow.wear(conn, GID, "u1", "穿上 防弹背心")
    shop_flow.sell(conn, GID, "u1", "卖 防弹背心")
    card = roster.get(conn, GID, "u1")
    assert "armor" not in (card["state"] or {})
    assert roster.to_character(card).armor.sp == 0


# ---------- 路由：不吞戏内台词 ----------


def test_handles_only_real_shop_talk(conn):
    _pc(conn)
    assert shop_flow.handles(conn, GID, "u1", "买 中型手枪")
    assert not shop_flow.handles(conn, GID, "u1", "我去买杯酒，顺便打听消息")
    assert not shop_flow.handles(conn, GID, "u1", "买 光剑")


def test_handles_sell_needs_the_item(conn):
    _pc(conn)
    assert not shop_flow.handles(conn, GID, "u1", "卖 中型手枪")
    shop_flow.buy(conn, GID, "u1", "买 中型手枪")
    assert shop_flow.handles(conn, GID, "u1", "卖 中型手枪")


def test_handles_takeoff_needs_worn_armor(conn):
    _pc(conn)
    assert not shop_flow.handles(conn, GID, "u1", "脱下")
    card = roster.get(conn, GID, "u1")
    card["state"]["armor"] = {"name": "皮衣", "sp": 4, "ev": 0}
    roster.save(conn, GID, "u1", card)
    assert shop_flow.handles(conn, GID, "u1", "脱下")
