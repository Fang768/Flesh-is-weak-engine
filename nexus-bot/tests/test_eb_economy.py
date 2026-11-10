"""EB 经济：建卡起始资金 + 装义体真扣费。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:8631``「街鼠和边缘行者角色还可以额外获得 500 欧元用于购买其他物品或攒下来」
* ``:9388`` 面面俱到 2550 EB（本仓库位卡模板不走这条路，只作参照）
* ``:30352-30369`` 八档价格类别：廉价10 / 平价20 / 略贵50 / 高价100 /
  昂贵500 / 特贵1000 / 奢侈5000 / 超奢10000+
* ``:30347``「没有掮客或夜市的帮助，你只能购买到高价级别以下的商品」

「约定」：钱不够直接拒绝安装（书里没有赊账条款），不扣人性、不落任何状态。
"""

from __future__ import annotations

import pytest

from core import db, easy_roster, growth, roster
from core.cyber_flow import install

GID = "qq:eb1"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, qq="u1"):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    return roster.get(conn, GID, qq)


def _roll(*values):
    from cyberpunk import dice

    return dice.ScriptedRoller(list(values))


# ---------- 起始资金 ----------


def test_new_character_gets_starting_eb(conn):
    card = _pc(conn)
    assert growth.eb(card) == growth.STARTING_EB == 500


def test_starting_eb_does_not_overwrite_existing(conn):
    card = _pc(conn)
    growth.grant_eb(card, 700)          # 500 + 700 = 1200
    assert growth.init_starting_eb(card) == 1200
    assert growth.eb(card) == 1200


def test_starting_eb_is_idempotent_on_rebuild(conn):
    """建卡流程重跑不该刷钱。"""
    _pc(conn)
    again = roster.build(template="独狼", name="阿飞")
    assert growth.eb(again) == 500


# ---------- spend_eb ----------


def test_spend_eb_deducts(conn):
    card = _pc(conn)
    assert growth.spend_eb(card, 100) == 400
    assert growth.eb(card) == 400


def test_spend_eb_rejects_when_short(conn):
    card = _pc(conn)
    with pytest.raises(growth.GrowthError) as exc:
        growth.spend_eb(card, 600)
    assert "不够" in str(exc.value)
    assert growth.eb(card) == 500        # 没扣


def test_spend_eb_zero_is_free(conn):
    card = _pc(conn)
    assert growth.spend_eb(card, 0) == 500


def test_spend_eb_negative_rejected(conn):
    card = _pc(conn)
    with pytest.raises(growth.GrowthError):
        growth.spend_eb(card, -10)


# ---------- 安装扣费 ----------


def test_install_charges_the_listed_price(conn):
    _pc(conn)
    # 赛博义眼 100 EB（full.txt:9869「100 欧元（高价）」）
    text = install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    assert "100" in text and "余额 400" in text
    assert growth.eb(roster.get(conn, GID, "u1")) == 400


def test_install_refuses_when_broke(conn):
    card = _pc(conn)
    # 皮下护甲 1000 EB，只有 500 → 装不起
    text = install(conn, GID, "u1", "安装 皮下护甲", roller=_roll(6, 6, 6, 6))
    assert "装不起" in text and "差 500" in text
    after = roster.get(conn, GID, "u1")
    assert growth.eb(after) == 500                       # 钱没动
    assert after["state"].get("hum") in (None, 50)       # 人性没扣
    assert "皮下护甲" not in (after["state"].get("cyber") or [])


def test_install_zero_hl_item_still_costs_money(conn):
    """生物监测器 HL N/A 但还是要 100 EB（full.txt:9721）。"""
    _pc(conn)
    text = install(conn, GID, "u1", "安装 生物监测器")
    assert "不造成人性损失" in text
    assert growth.eb(roster.get(conn, GID, "u1")) == 400


def test_install_charges_each_time(conn):
    """装两次扣两次钱（书内页 112：买及安装两次）。"""
    _pc(conn)
    install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    assert growth.eb(roster.get(conn, GID, "u1")) == 300


def test_install_easy_card_pays_too(conn):
    from cyberpunk import easy

    tpl = dict(easy.templates()[0])
    tpl.setdefault("state", {})["eb"] = 1000
    easy_roster.save(conn, GID, "e1", tpl, "肆拾")
    install(conn, GID, "e1", "安装 赛博义眼", roller=_roll(5, 5))
    assert growth.eb(easy_roster.get(conn, GID, "e1")) == 900


# ---------- 价格类别对照（数据自检） ----------


def test_price_categories_match_the_book():
    table = {
        "廉价": 10, "平价": 20, "略贵": 50, "高价": 100,
        "昂贵": 500, "特贵": 1000, "奢侈": 5000, "超奢": 10000,
    }
    from cyberpunk import cyber

    for name, entry in cyber.hl_table().items():
        cost = entry.get("cost")
        if cost is None:
            continue
        assert cost in table.values(), f"{name} 的价格 {cost} 不在书的八档里"


def test_brokerless_cap_is_high_price_tier(conn):
    """没有掮客只能买「高价」（100）以下：这条规则留给 /购买 用，先记常量。"""
    assert growth.BROKERLESS_CAP == 100


def test_broker_tier_caps_follow_profession_level():
    # full.txt:584-585：掮客 3 级 <500、7 级 <1000
    assert growth.broker_cap(1) == growth.BROKERLESS_CAP
    assert growth.broker_cap(3) == 500
    assert growth.broker_cap(5) == 500
    assert growth.broker_cap(7) == 1000
    assert growth.broker_cap(9) == 10000
