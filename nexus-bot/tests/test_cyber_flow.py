"""赛博组件安装 → 人性损失（HL）→ 共情（EMP）下降。

规则依据（`rules/red_core/raw/full.txt`）：

* `:9834-9835`「HL（人性损失）: 这个装置对你的心理影响有多严重。在角色生成中，
  人性损失是预设的。在角色生成以后，人性点数损失由预设数字后括号中的骰子决定。」
* `:6970`「当你的角色正因为安装了赛博改造经历人性损失…他就失去了人性值。」
* `:9997`「（此情况需购买及安装两次，人性损失也是两次）。」

人性值 = EMP × 10（`cyberpunk/stats.py:92`）；每掉满 10 点人性 EMP −1（最低 1），
与 `cyberpunk/characters.py:202 Character.lose_humanity` 同一口径。
"""

from __future__ import annotations

from core import db, easy_roster, roster
from core.cyber_flow import (
    CYBERPSYCHOSIS_NOTE,
    humanity_of,
    install,
    looks_like_install,
    status_text,
)
from cyberpunk import cyber, dice, easy

GID = "qq:cyber1"


def conn_fixture():
    return db.connect(":memory:")


def _pc(conn, qq="u1", name="阿飞", template="独狼"):
    roster.save(conn, GID, qq, roster.build(template=template, name=name))
    return roster.get(conn, GID, qq)


def _roll(*values):
    return dice.ScriptedRoller(list(values))


# ---------- 数据表 ----------


def test_hl_table_is_loaded_from_the_rulebook():
    table = cyber.hl_table()
    assert table, "cyberware_hl.json 没加载到"
    # 书内页 112-115 的六个小节都抽到了
    sections = {meta["section"] for meta in table.values()}
    assert {"时尚装置", "神经组件", "赛博光学组件"} <= sections


def test_hl_lookup_accepts_bracketed_names():
    exact = cyber.hl_of("赛博义眼")
    assert exact is not None and exact["zh"] == "赛博义眼"
    # 「赛博义眼（单颗）」= 书里同一件（每颗眼球装一次，HL 也是两次）
    bracketed = cyber.hl_of("赛博义眼（单颗）")
    assert bracketed is not None and bracketed["hl"] == exact["hl"]


def test_hl_lookup_returns_none_for_unknown_items():
    assert cyber.hl_of("不存在的义体") is None
    assert cyber.hl_of("") is None


def test_fashionware_costs_no_humanity():
    entry = cyber.hl_of("生物监测器")
    assert entry is not None
    assert entry["hl"] == 0 and entry["hl_dice"] in (None, "N/A")
    assert cyber.roll_hl(entry) == 0


def test_rolled_hl_matches_the_dice():
    entry = cyber.hl_of("皮下口袋")          # 3（1d6）
    assert entry["hl_dice"] == "1d6"
    assert cyber.roll_hl(entry, _roll(6)) == 6


def test_halved_dice_round_up():
    entry = cyber.hl_of("防眩晕")            # 2（1d6/2 向上取整）
    assert entry["hl_dice"] == "1d6/2"
    assert cyber.roll_hl(entry, _roll(5)) == 3    # 5/2 = 2.5 → 3
    assert cyber.roll_hl(entry, _roll(4)) == 2    # 4/2 = 2


def test_fixed_hl_used_when_no_dice():
    entry = {"hl": 7, "hl_dice": None}
    assert cyber.roll_hl(entry) == 7
    assert cyber.roll_hl(None) == 0


# ---------- 人性 / EMP 结算 ----------


def test_refuses_without_a_card():
    conn = conn_fixture()
    assert "还没有角色卡" in install(conn, GID, "u9", "安装 赛博义眼")


def test_bracketed_syntax_variants():
    conn = conn_fixture()
    _pc(conn)
    for text in ("安装 赛博义眼", "/安装 赛博义眼", "植入 赛博义眼", "装义体 赛博义眼"):
        assert looks_like_install(text), text


def test_install_reports_unknown_item_with_suggestions():
    conn = conn_fixture()
    _pc(conn)
    text = install(conn, GID, "u1", "安装 不存在的义体")
    assert "没在赛博组件表里找到" in text
    assert "表里有" in text


def test_install_deducts_humanity_and_drops_emp():
    conn = conn_fixture()
    _pc(conn)                                  # 独狼 EMP5 → 人性 50
    assert humanity_of(roster.get(conn, GID, "u1"), False) == 50

    text = install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    assert "人性 50 → 40" in text
    assert "投 2d6 得 10" in text
    assert "共情 EMP 5 → 4" in text

    after = roster.get(conn, GID, "u1")
    assert after["stats"]["EMP"] == 4
    assert after["state"]["hum"] == 40


def test_humanity_persists_across_installs():
    conn = conn_fixture()
    _pc(conn)
    install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))       # 50 → 40
    install(conn, GID, "u1", "安装 神经链路", roller=_roll(5, 5))       # 40 → 30
    card = roster.get(conn, GID, "u1")
    assert card["state"]["hum"] == 30
    assert card["stats"]["EMP"] == 3


def test_installing_the_same_item_twice_costs_twice():
    """书内页 112（`:9997`）：「买及安装两次，人性损失也是两次」。"""
    conn = conn_fixture()
    _pc(conn)
    first = install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    second = install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    assert "50 → 40" in first
    assert "40 → 30" in second
    assert roster.get(conn, GID, "u1")["state"]["cyber"].count("赛博义眼") == 2


def test_emp_never_drops_below_one():
    conn = conn_fixture()
    _pc(conn)
    # 皮下护甲 14（4d6）；投满 6×4 = 24，远超初始 50
    install(conn, GID, "u1", "安装 皮下护甲", roller=_roll(6, 6, 6, 6))
    install(conn, GID, "u1", "安装 皮下护甲", roller=_roll(6, 6, 6, 6))
    card = roster.get(conn, GID, "u1")
    assert card["state"]["hum"] == 2
    assert card["stats"]["EMP"] == 1


def test_humanity_floors_at_zero():
    conn = conn_fixture()
    _pc(conn)
    for _ in range(3):
        install(conn, GID, "u1", "安装 皮下护甲", roller=_roll(6, 6, 6, 6))
    card = roster.get(conn, GID, "u1")
    assert card["state"]["hum"] == 0
    assert card["stats"]["EMP"] == 1
    text = install(conn, GID, "u1", "安装 皮下护甲", roller=_roll(6, 6, 6, 6))
    assert CYBERPSYCHOSIS_NOTE in text


def test_zero_hl_item_says_no_loss():
    conn = conn_fixture()
    _pc(conn)
    text = install(conn, GID, "u1", "安装 生物监测器")
    assert "不造成人性损失" in text
    assert humanity_of(roster.get(conn, GID, "u1"), False) == 50


def test_quote_does_not_install():
    conn = conn_fixture()
    _pc(conn)
    text = install(conn, GID, "u1", "安装 赛博义眼 多少钱")
    assert "要装就发" in text
    assert humanity_of(roster.get(conn, GID, "u1"), False) == 50


# ---------- 简易卡 ----------


def test_easy_card_also_loses_humanity():
    conn = conn_fixture()
    template = dict(easy.templates()[0])
    easy_roster.save(conn, GID, "e1", template, "肆拾")
    before = int(template["stats"]["EMP"]) * 10
    text = install(conn, GID, "e1", "安装 赛博义眼", roller=_roll(5, 5))
    assert f"人性 {before} → {before - 10}" in text
    card = easy_roster.get(conn, GID, "e1")
    assert card["state"]["hum"] == before - 10


# ---------- 清单 ----------


def test_status_lists_installed_items():
    conn = conn_fixture()
    _pc(conn)
    assert "还没装过赛博组件" in status_text(conn, GID, "u1")
    install(conn, GID, "u1", "安装 赛博义眼", roller=_roll(5, 5))
    install(conn, GID, "u1", "安装 皮下口袋", roller=_roll(4))
    text = status_text(conn, GID, "u1")
    assert "赛博义眼" in text and "皮下口袋" in text
    assert "人性 36" in text


def test_status_without_card():
    conn = conn_fixture()
    assert "还没有角色卡" in status_text(conn, GID, "u9")
