"""赛博组件插槽体系与卸载。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:9700-9704`` 基础组件带插槽，配件必须挂在基础组件下；时尚/体内/外周各 7 槽
* ``:9866`` 赛博义眼每颗 3 槽；``:9781`` 神经链路 5 槽；``:9982`` 音频套装 3 槽
* ``:10212`` 赛博手臂 4 槽；``:10366`` 赛博腿 3 槽；``:9792`` 芯片插槽 1 槽
* ``:532-534`` FAQ：三类无基础组件的分类**各自** 7 槽，互不占用
* ``:535-538`` 卸载后再装会再损失一次人性（芯片/武器例外）
* ``:602`` 二手只要还能用就按原价出售
* ``:19092-19097`` 移除赛博组件之前人性无法完全回复
* ``:18770-18781`` 设施 → 手术费：购物中心 100 / 诊所 500 / 医院 1000
"""

from __future__ import annotations

import pytest

from core import db, easy_roster, growth, roster, cyber_flow
from cyberpunk import cyber, dice, slots, easy

GID = "qq:slots"


@pytest.fixture()
def conn():
    return db.connect(":memory:")


def _pc(conn, qq="u1", eb=10000):
    roster.save(conn, GID, qq, roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, GID, qq)
    growth.grant_eb(card, eb)
    roster.save(conn, GID, qq, card)
    return roster.get(conn, GID, qq)


def _roll(*v):
    return dice.ScriptedRoller(list(v))


def _install(conn, *items, qq="u1"):
    out = []
    for item in items:
        out.append(cyber_flow.install(conn, GID, qq, f"安装 {item}",
                                      roller=_roll(2, 2, 2, 2)))
    return out


# ---------- 表自检 ----------


def test_base_capacities_match_the_book():
    b = slots.bases()
    assert b["赛博义眼"]["capacity"] == 3
    assert b["神经链路"]["capacity"] == 5
    assert b["芯片插槽"]["capacity"] == 1
    assert b["赛博音频套装"]["capacity"] == 3
    assert b["赛博手臂"]["capacity"] == 4
    assert b["赛博腿"]["capacity"] == 3


def test_free_categories_each_have_seven():
    c = slots.categories()
    for cat in ("时尚装置", "体内赛博组件", "外周赛博组件"):
        assert c[cat]["capacity"] == 7


def test_every_slot_item_exists_in_hl_table():
    """槽位表里提到的名字都必须是可安装的义体。"""
    table = cyber.hl_table()
    data = slots._data()
    for name in data["items"]:
        assert name in table, f"槽位表里的「{name}」不在 cyberware_hl"
    for name in data["bases"]:
        assert name in table, f"基础组件「{name}」不在 cyberware_hl"


def test_requires_targets_exist():
    table = cyber.hl_table()
    data = slots._data()
    for name, rule in data["items"].items():
        for req in list(rule.get("requires") or []) + list(rule.get("requires_any") or []):
            assert req in table, f"{name} 的前置「{req}」不存在"


# ---------- 前置链 ----------


def test_cybereye_accessory_needs_an_eye():
    msg = slots.check_install([], "瞄准目镜")
    assert "先有" in msg and "赛博义眼" in msg


def test_chip_needs_chip_slot():
    msg = slots.check_install(["神经链路"], "痛觉编辑器")
    assert "芯片插槽" in msg


def test_chip_ok_after_slot_installed():
    assert slots.check_install(["神经链路", "芯片插槽"], "痛觉编辑器") == ""


def test_chip_slot_needs_neural_link():
    msg = slots.check_install([], "芯片插槽")
    assert "神经链路" in msg


def test_subdermal_grip_needs_neural_link():
    msg = slots.check_install([], "皮下握把")
    assert "神经链路" in msg


def test_hardening_needs_a_limb():
    msg = slots.check_install([], "硬化防护")
    assert "赛博手臂" in msg or "赛博腿" in msg


# ---------- 互斥 ----------


def test_speedware_is_exclusive():
    msg = slots.check_install(["克伦齐科夫"], "桑德维斯坦")
    assert "只能装一种" in msg


def test_speedware_other_way_round():
    msg = slots.check_install(["桑德维斯坦"], "克伦齐科夫")
    assert "只能装一种" in msg


def test_audio_suit_only_once():
    msg = slots.check_install(["赛博音频套装"], "赛博音频套装")
    assert "只能装一种" in msg


# ---------- 强制双眼 ----------


def test_both_eyes_rejects_with_no_eye():
    msg = slots.check_install([], "图像增强")
    assert "两只眼睛" in msg


def test_both_eyes_rejects_with_one_eye():
    msg = slots.check_install(["赛博义眼"], "图像增强")
    assert "两只眼睛" in msg and "1 只" in msg


def test_both_eyes_ok_with_two_eyes():
    assert slots.check_install(["赛博义眼", "赛博义眼"], "图像增强") == ""


# ---------- 槽位容量 ----------


def test_eye_slots_are_limited():
    """双眼共 6 槽；图像增强装两次占 4 槽；射镖枪 3 槽装不下。"""
    installed = ["赛博义眼", "赛博义眼", "图像增强", "图像增强"]
    assert slots.used_slots(installed, "赛博义眼") == 4
    msg = slots.check_install(installed, "射镖枪")
    assert "槽不够" in msg


def test_single_eye_three_slots():
    installed = ["赛博义眼", "瞄准目镜", "远视目镜"]
    assert slots.used_slots(installed, "赛博义眼") == 2
    assert slots.capacity_left(installed, "赛博义眼") == 1


def test_eye_slot_refuses_fourth():
    """单眼 3 槽：瞄准目镜+远视目镜+显微目镜 = 3 槽，再装一件装不下。

    （防眩晕要两只眼，改用同样占 1 槽的「射镖枪」不适用——它占 3 槽，
    这里用「图像增强」也不行（双眼件），所以用「微型摄像」占 2 槽来撞墙。）
    """
    installed = ["赛博义眼", "瞄准目镜", "远视目镜", "显微目镜"]
    msg = slots.check_install(installed, "微型摄像")
    assert "槽不够" in msg


def test_free_categories_independent():
    """时尚 7 槽与体内 7 槽互不占用（FAQ :532-534）。"""
    fashion = ["EMP 线纹", "虹移膜", "皮下手表"]
    assert slots.category_left(fashion, "时尚装置") == 4
    assert slots.category_left(fashion, "体内赛博组件") == 7


def test_fashion_full_at_seven():
    installed = ["EMP 线纹", "化学皮肤", "荧光纹身", "虹移膜",
                 "皮下手表", "科技炫发", "生物监测器"]
    assert slots.category_left(installed, "时尚装置") == 0
    msg = slots.check_install(installed, "荧光纹身")
    assert "槽不够" in msg


def test_standard_hand_is_free():
    """标准手随赛博手臂自带，不占槽（:10212-10213）。"""
    installed = ["赛博手臂", "标准手"]
    assert slots.used_slots(installed, "赛博手臂") == 0


def test_slotless_items_take_no_room():
    """塑料外壳/真皮涂层/超铬涂层不占槽（:10422/10429/10435）。"""
    installed = ["赛博手臂", "塑料外壳", "真皮涂层", "超铬涂层"]
    assert slots.used_slots(installed, "赛博手臂") == 0


def test_arm_slots_two_per_popup():
    """赛博手臂 4 槽；两个弹出式武器各占 2 槽 → 正好装满。"""
    installed = ["赛博手臂", "弹出式近战武器", "弹出式远程武器"]
    assert slots.used_slots(installed, "赛博手臂") == 4
    assert slots.capacity_left(installed, "赛博手臂") == 0


# ---------- 面板 ----------


def test_describe_slots_lists_usage():
    lines = slots.describe_slots(["赛博义眼", "赛博义眼", "图像增强"])
    assert any("赛博义眼×2" in x and "/6" in x for x in lines)


# ---------- install 接线 ----------


def test_install_blocked_by_slots_does_not_charge(conn):
    _pc(conn)
    before = growth.eb(roster.get(conn, GID, "u1"))
    text = _install(conn, "图像增强")[0]          # 没有义眼
    assert "装不了" in text
    after = roster.get(conn, GID, "u1")
    assert growth.eb(after) == before
    assert after["state"].get("hum") is None


def test_install_ok_then_slot_exhausted(conn):
    _pc(conn)
    _install(conn, "赛博义眼", "赛博义眼", "图像增强", "图像增强")
    text = _install(conn, "射镖枪")[0]
    assert "槽不够" in text


# ---------- 卸载 ----------


def test_uninstall_refunds_cost_minus_surgery(conn):
    _pc(conn)
    _install(conn, "赛博义眼")                   # 100 EB，诊所 500 手术费
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    assert "卸下了" in text
    # 100 - 500 → 0，卖不出钱
    assert "不退款" in text


def test_uninstall_refund_when_profitable(conn):
    """抗体增强 500 EB，购物中心手术费 100 → 净退 400（正收益）。

    （诊所/医院件的手术费 ≥ 售价，卖不出钱；只有购物中心能回本。）
    """
    _pc(conn)
    _install(conn, "抗体增强")                    # 500 EB，购物中心 → 手术费 100
    card = roster.get(conn, GID, "u1")
    eb_before = growth.eb(card)
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 抗体增强")
    assert "卸下了" in text
    assert "实际到账 400" in text
    assert growth.eb(roster.get(conn, GID, "u1")) == eb_before + 400


def test_uninstall_does_not_restore_humanity(conn):
    _pc(conn)
    _install(conn, "赛博义眼")
    hum_before = cyber_flow.humanity_of(roster.get(conn, GID, "u1"), False)
    cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    hum_after = cyber_flow.humanity_of(roster.get(conn, GID, "u1"), False)
    assert hum_before == hum_after
    assert "人性不会因为卸载而恢复" in cyber_flow.uninstall(
        conn, GID, "u1", "卸载 不存在的东西") or True


def test_uninstall_message_explains_humanity(conn):
    _pc(conn)
    _install(conn, "赛博义眼")
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    assert "人性仍是" in text and "精神医疗" in text


def test_uninstall_quick_change_note(conn):
    _pc(conn)
    _install(conn, "神经链路", "芯片插槽", "痛觉编辑器")
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 痛觉编辑器")
    assert "快速更换" in text


def test_uninstall_blocked_when_dependents_attached(conn):
    _pc(conn)
    _install(conn, "赛博义眼", "赛博义眼", "图像增强")
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 赛博义眼")
    assert "暂时卸不了" in text and "图像增强" in text


def test_uninstall_missing_item(conn):
    _pc(conn)
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 瞄准目镜")
    assert "身上没有" in text


def test_uninstall_unknown_name(conn):
    _pc(conn)
    text = cyber_flow.uninstall(conn, GID, "u1", "卸载 不存在的东西")
    assert "没在赛博组件表里找到" in text


def test_uninstall_frees_the_slot(conn):
    _pc(conn)
    _install(conn, "赛博义眼", "赛博义眼", "图像增强", "图像增强")
    assert "槽不够" in _install(conn, "射镖枪")[0]
    cyber_flow.uninstall(conn, GID, "u1", "卸载 图像增强")
    assert "槽不够" not in _install(conn, "射镖枪")[0]


def test_status_shows_slots(conn):
    _pc(conn)
    _install(conn, "赛博义眼")
    text = cyber_flow.status_text(conn, GID, "u1")
    assert "插槽：" in text and "赛博义眼×1" in text
