"""存量卡前置/存储的回归测试。

这三个都是**跑现有卡时发现的真 bug**，不是假想：

1. **简易卡的义体在顶层 `cyber`**，而 `_cyber_list` 只读 `state.cyber`
   → 赤尾装了 5 件，`/义体` 面板却显示「还没装过赛博组件」。
2. **赛博义肢配件没有前置校验**：`category_base("赛博义肢")` 返回空，
   导致「工具手」不装赛博手臂也能装。
3. **6 张预设卡的义体不合 RED 前置链**：简易版书只写效果，没写基础组件，
   直接照搬会被新加的插槽校验全部拦住。
"""

from __future__ import annotations

import pytest

from core import cyber_flow, db, easy_roster, roster
from cyberpunk import cyber, easy, slots

GID = "qq:prereq"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


# ---------- bug 1：简易卡的义体存在顶层 ----------


def _easy(conn, name="赤尾", qq="e1"):
    easy_roster.save(conn, GID, qq, dict(easy.get(name)), name)
    return easy_roster.get(conn, GID, qq)


def test_cyber_list_reads_top_level(conn):
    card = _easy(conn)
    assert cyber_flow._cyber_list(card), "简易卡顶层 cyber 没被读到"


def test_status_shows_easy_card_cyberware(conn):
    _easy(conn)
    text = cyber_flow.status_text(conn, GID, "e1")
    assert "还没装过" not in text
    assert "工具手" in text


def test_remember_writes_top_level_for_easy_card(conn):
    _easy(conn)
    card = easy_roster.get(conn, GID, "e1")
    before = len(card["cyber"])
    cyber_flow._remember(conn, GID, "e1", card, True, "生物监测器", 40)
    after = easy_roster.get(conn, GID, "e1")
    assert len(after["cyber"]) == before + 1
    from cyberpunk import cyber_bonus
    assert "生物监测器" in cyber_bonus.installed_names(after)
    # 不该被塞进 state
    assert "生物监测器" not in (after.get("state") or {}).get("cyber", [])


def test_remember_writes_state_for_red_card(conn):
    roster.save(conn, GID, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, "u1")
    cyber_flow._remember(conn, GID, "u1", card, False, "赛博义眼", 40)
    after = roster.get(conn, GID, "u1")
    from cyberpunk import cyber_bonus
    assert "赛博义眼" in cyber_bonus.installed_names(after)


def test_uninstall_works_on_easy_card(conn):
    _easy(conn)
    text = cyber_flow.uninstall(conn, GID, "e1", "卸载 工具手")
    assert "卸下了" in text
    after = easy_roster.get(conn, GID, "e1")
    from cyberpunk import cyber_bonus
    assert "工具手" not in cyber_bonus.installed_names(after)


# ---------- bug 2：赛博义肢配件必须有义肢 ----------


def test_cyberlimb_accessory_needs_a_limb():
    msg = slots.check_install([], "工具手")
    assert "赛博手臂" in msg


def test_cyberlimb_accessory_ok_with_arm():
    assert slots.check_install(["赛博手臂"], "工具手") == ""


def test_cyberlimb_accessory_rejects_wrong_limb():
    # 手臂件不能挂到腿上（旧版会误挂，现已按 requires_any 认父级）
    assert "赛博手臂" in slots.check_install(["赛博腿"], "工具手")


@pytest.mark.parametrize("item", ["开膛手", "金刚狼爪", "大指虎", "刮刮乐"])
def test_arm_weapons_need_an_arm(item):
    assert "赛博手臂" in slots.check_install([], item)


def test_razor_feet_need_legs():
    assert "赛博腿" in slots.check_install([], "剃刀足")


def test_cyberlimb_category_has_no_single_base():
    """赛博义肢有两个 base（手臂/腿），不能靠 category_base 取单一值。"""
    assert slots.category_base("赛博义肢") == ""
    assert slots.categories()["赛博义肢"]["bases"] == ["赛博手臂", "赛博腿"]


# ---------- bug 3：6 张预设卡必须过得了前置与槽位校验 ----------


@pytest.mark.parametrize("name", ["肆拾", "搬运工", "火炬", "赤尾", "24/7", "清道夫"])
def test_template_cyberware_passes_slot_check(name):
    """按模板顺序安装，每一件都必须能装上。"""
    tpl = easy.get(name)
    installed: list[str] = []
    for raw in tpl["cyber"]:
        zh = (cyber.hl_of(raw) or {}).get("zh")
        assert zh, f"{name} 的义体「{raw}」不在 HL 表"
        problem = slots.check_install(installed, zh)
        assert not problem, f"{name} 装「{zh}」被拦：{problem}"
        installed.append(zh)


@pytest.mark.parametrize("name", ["肆拾", "搬运工", "火炬", "赤尾", "24/7", "清道夫"])
def test_templates_include_base_components(name):
    """每张卡的义体链里要有对应分类的基础组件。"""
    tpl = easy.get(name)
    names = [(cyber.hl_of(x) or {}).get("zh") for x in tpl["cyber"]]
    names = [x for x in names if x]
    for accessory in names:
        parent = slots._parent_base(accessory, names)
        if parent:
            assert parent in names, f"{name} 的「{accessory}」缺基础组件「{parent}」"


@pytest.mark.parametrize("name", ["搬运工", "24/7"])
def test_image_enhancement_is_installed_twice(name):
    """图像增强强制双眼，每只眼各一次（:9902、:9997）。"""
    tpl = easy.get(name)
    assert tpl["cyber"].count("图像增强") == 2


def test_both_eyes_cards_have_two_cybereyes():
    for name in ("搬运工", "24/7"):
        tpl = easy.get(name)
        assert tpl["cyber"].count("赛博义眼") == 2, f"{name} 只有一只眼"
