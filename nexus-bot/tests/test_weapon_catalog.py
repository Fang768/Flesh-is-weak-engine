"""弹出式武器：装的是**市面上的具体武器**，按属性判定能不能装。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:10261-10269`` 弹出式近战武器：「**任何轻型，中型，或重型武器**都能被安装于
  一条赛博手臂中。此武器能够被藏匿，**即使是通常状态下无法藏匿的**。」
* ``:10278-10286`` 弹出式远程武器：「安装于一条赛博手臂中的**任何单手持握的
  远程武器**。」
* ``:521`` FAQ：「弹出式近战武器殖装允许你高效地按照轻型，中型，重型，
  **甚至是异种近战武器**的规格将它们装在自己的手臂上。」

**设计要点（主人 2026-11-13 定）**：不建弹出式专用白名单。
判定只看武器自己的属性（``melee_class`` / ``hands``），所以以后加武器只要往
``rules/red_core/data/weapons_catalog.json`` 加一行，判定自动生效。
"""

from __future__ import annotations

import pytest

from cyberpunk import weapon_catalog as wc

GID = "qq:popup"


# ---------- 目录本身 ----------


def test_catalog_loads_with_the_three_book_categories():
    cats = {it["category"] for it in wc.items()}
    assert cats <= {"远程武器", "近战武器", "异种武器"}
    assert "近战武器" in cats and "远程武器" in cats and "异种武器" in cats


def test_every_catalog_item_has_the_attributes_we_judge_on():
    """判定全靠这些字段，缺了就无法筛选（以后加武器也会被这条拦住）。"""
    for it in wc.items():
        assert it.get("name"), it
        assert it.get("damage"), f"{it['name']} 缺 damage"
        assert "hands" in it, f"{it['name']} 缺 hands"
        assert it.get("category") in ("远程武器", "近战武器", "异种武器"), it["name"]
        if it["category"] == "近战武器" or it.get("melee_class"):
            assert it["melee_class"] in ("轻型", "中型", "重型", "超重型"), it["name"]


def test_find_and_search_are_punctuation_insensitive():
    assert wc.find("弓 & 十字弩")["name"] == "弓&十字弩"
    assert wc.find("金田一 03 式单分子武士刀")["name"] == "金田一03式单分子武士刀"
    assert wc.find("不存在的武器") is None
    assert wc.search("手枪")


# ---------- 近战：只允许轻/中/重（:10261-10269） ----------


@pytest.mark.parametrize("name", ["轻型近战武器", "中型近战武器", "重型近战武器"])
def test_melee_light_medium_heavy_can_mount(name):
    assert wc.mountable(wc.find(name), mount="弹出式近战武器")


def test_superheavy_melee_cannot_mount():
    """书里只列到重型——超重型不在「任何轻型，中型，或重型武器」里。"""
    weapon = wc.find("超重型近战武器")
    problem = wc.mount_problem(weapon, mount="弹出式近战武器")
    assert "超重型" in problem and "10261-10269" in problem


def test_exotic_melee_can_mount_if_class_is_allowed():
    """FAQ ``:521`` 明确说异种近战武器也行——只看类别，不看是不是异种。"""
    glove = wc.find("战斗手套")       # 异种 / 重型
    stick = wc.find("电击棍")         # 异种 / 中型
    assert glove["category"] == "异种武器" and glove["melee_class"] == "重型"
    assert wc.mountable(glove, mount="弹出式近战武器")
    assert wc.mountable(stick, mount="弹出式近战武器")


def test_exotic_superheavy_katana_cannot_mount():
    """金田一是超重型双手刀 → 两个理由都装不进。"""
    katana = wc.find("金田一03式单分子武士刀")
    assert katana["melee_class"] == "超重型"
    assert not wc.mountable(katana, mount="弹出式近战武器")


def test_ranged_weapon_cannot_go_into_melee_mount():
    problem = wc.mount_problem(wc.find("中型手枪"), mount="弹出式近战武器")
    assert "不是近战武器" in problem


# ---------- 远程：必须单手持握（:10278-10286） ----------


@pytest.mark.parametrize("name", [
    "中型手枪", "重型手枪", "超重型手枪", "冲锋枪", "重型冲锋枪",
    "气手枪", "镖枪", "马洛里安军火3516型手枪", "微波发射器", "高音发射器", "电击枪",
])
def test_one_handed_ranged_can_mount(name):
    weapon = wc.find(name)
    assert weapon["hands"] == 1, f"{name} 应该是单手持握"
    assert wc.mountable(weapon, mount="弹出式远程武器")


@pytest.mark.parametrize("name", [
    "霰弹枪", "突击步枪", "狙击步枪", "弓&十字弩", "榴弹发射器", "火箭筒",
    "火焰喷射器", "宪法军火飓风突击武器", "军科牛仔U-56型榴弹发射器",
    "莱茵金属EMG-86型电磁炮", "浪啸军火螺旋枪",
])
def test_two_handed_ranged_cannot_mount(name):
    weapon = wc.find(name)
    assert weapon["hands"] == 2
    problem = wc.mount_problem(weapon, mount="弹出式远程武器")
    assert "只手" in problem and "10280" in problem


def test_melee_weapon_cannot_go_into_ranged_mount():
    problem = wc.mount_problem(wc.find("棒球棍" if wc.find("棒球棍") else "中型近战武器"),
                               mount="弹出式远程武器")
    assert "近战武器" in problem


# ---------- 解耦：加一行数据就生效，不用改代码 ----------


def test_adding_a_weapon_needs_no_code_change(monkeypatch):
    """解耦验证：往目录里塞一条新武器，判定与筛选自动认它，不改任何代码。"""
    extra = {
        "name": "测试用轻型刀", "category": "近战武器", "melee_class": "轻型",
        "skill": "近战武器", "damage": "1d6", "rof": 2, "hands": 1,
        "conceal": False, "euro": 50, "tier": "略贵", "line": 0,
    }
    original = wc._data()
    patched = {"items": list(original["items"]) + [extra]}
    monkeypatch.setattr(wc, "_data", lambda: patched)
    try:
        found = wc.find("测试用轻型刀")
        assert found is not None and found["damage"] == "1d6"
        # 新增的武器自动出现在可装清单里，没有改任何判定代码
        assert "测试用轻型刀" in [it["name"] for it in wc.list_for("弹出式近战武器")]
        assert wc.mountable(found, mount="弹出式近战武器")
    finally:
        monkeypatch.undo()


def test_mount_problem_is_pure_attribute_check():
    """判定只看属性：同一条记录改 hands 就能翻转结论，不涉及任何名单。"""
    one_hand = {"name": "X", "hands": 1, "damage": "2D6"}
    two_hand = {"name": "X", "hands": 2, "damage": "2D6"}
    assert wc.mount_problem(one_hand, mount="弹出式远程武器") == ""
    assert wc.mount_problem(two_hand, mount="弹出式远程武器") != ""


def test_list_for_matches_the_two_rules():
    melee = wc.list_for("弹出式近战武器")
    assert melee and all(it["melee_class"] in ("轻型", "中型", "重型") for it in melee)
    ranged = wc.list_for("弹出式远程武器")
    assert ranged and all(it["hands"] == 1 for it in ranged)


def test_unknown_mount_is_reported():
    assert "不认识的安装位" in wc.mount_problem(wc.find("中型手枪"), mount="插嘴里")


# ---------- 藏匿语义（弹出式真正的价值） ----------


def test_carried_text_states_the_popup_conceal_rule():
    """``:10265``/``:10282``「即使是通常状态下无法藏匿的」。"""
    assert "可藏匿" in wc.carried(wc.find("超重型手枪"))
    assert "通常无法藏匿" in wc.carried(wc.find("超重型手枪"))
    # 超重型手枪本身在核心表里 conceal=False，弹出式装上去就能藏
    assert wc.find("超重型手枪")["conceal"] is False
