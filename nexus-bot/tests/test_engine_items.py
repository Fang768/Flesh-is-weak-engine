"""第 3-5 步：任务道具 + 战后动作 + 条件化奖励。

对照原文的四处缺口：

* **木桩**（:90）「来，拿去。带上这个木桩」——之前只在台词里
* **人头/伏特加**（:81）冰箱里的东西——之前只是线索文本
* **芭芭拉的人情**（:82）「她就欠了他们一个小人情」——之前没有动作能拿到
* **钉心脏/斩首**（:105）「如果他们记得刺穿…就赢得了青睐」——之前无条件发放
"""

from __future__ import annotations

import json

import pytest

from engine import action as A
from engine import conditions as C
from engine import save as SV
from engine import story as S


def _conn():
    import sqlite3

    from core import db as coredb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(coredb.SCHEMA)
    conn.execute(
        "INSERT INTO campaigns (id,title,status,scene,scenario,created_at,"
        "updated_at) VALUES (1,'t','active','a2.master','opera','x','x')")
    conn.execute("INSERT INTO group_sessions VALUES ('qq:1',1,'active','x')")
    conn.execute(
        "INSERT INTO characters (group_id,owner_qq,name,template,stats,skills,"
        " gear,perks,state,created_at,updated_at)"
        " VALUES ('qq:1','u1','肆拾','独狼',?,?, '[]','[]',?,'x','x')",
        (json.dumps({"REF": 8, "DEX": 8, "INT": 7, "COOL": 6, "TECH": 5,
                     "WILL": 5, "LUCK": 5, "MOVE": 5, "BODY": 6, "EMP": 5}),
         json.dumps({"觉察": 6, "潜行": 6, "运动": 5}),
         json.dumps({"eb": 500, "hp": 40, "max_hp": 40})),
    )
    conn.commit()
    return conn


@pytest.fixture()
def env():
    conn = _conn()
    save = SV.Save(conn=conn, group_id="qq:1", campaign_id=1)
    story = S.compile_story("opera")
    ex = A.Executor(story, save)
    yield conn, save, story, ex
    conn.close()


# ---------- 木桩 ----------


def test_master_hands_over_the_stake(env):
    """主人给木桩（原文 :90）——之前只有台词没有物品。"""
    conn, save, story, ex = env
    save.set_scene("a2.master")
    assert "take_stake" in [a.id for a in ex.catalog()]

    report = ex.perform("take_stake")
    assert report.ok
    card = save.card("u1")
    assert card.has_item("木桩")
    assert card.find_item("木桩").kind == "quest"


def test_stake_is_idempotent(env):
    conn, save, story, ex = env
    save.set_scene("a2.master")
    ex.perform("take_stake")
    ex.perform("take_stake")
    assert save.card("u1").item_names().count("木桩") == 1


# ---------- 冰箱里的东西 ----------


def test_fridge_loot_requires_knowing_whats_inside(env):
    """没认出人头之前，「装走」不该出现（先得查过 office_head）。"""
    conn, save, story, ex = env
    save.set_scene("a2.office_in")
    assert "loot_fridge" not in [a.id for a in ex.catalog()]

    save.mark_clue("office_head")
    assert "loot_fridge" in [a.id for a in ex.catalog()]


def test_fridge_loot_grants_all_five_items(env):
    """原文 :81 列了五件：伏特加 / 血腥玛丽 / 腌芦笋 / 磨砂玻璃 / 人头。"""
    conn, save, story, ex = env
    save.set_scene("a2.office_in")
    save.mark_clue("office_head")

    assert ex.perform("loot_fridge").ok
    names = save.card("u1").item_names()
    for item in ("人头", "伏特加", "血腥玛丽混合物", "腌芦笋", "磨砂玻璃"):
        assert item in names, item


# ---------- 芭芭拉的人情 ----------


def test_return_head_needs_the_head(env):
    conn, save, story, ex = env
    save.set_scene("a2.office_in")
    save.mark_clue("office_head")
    assert "return_head" not in [a.id for a in ex.catalog()]

    ex.perform("loot_fridge")
    assert "return_head" in [a.id for a in ex.catalog()]


def test_return_head_consumes_it_and_marks_favor(env):
    conn, save, story, ex = env
    save.set_scene("a2.office_in")
    save.mark_clue("office_head")
    ex.perform("loot_fridge")

    assert ex.perform("return_head").ok
    assert not save.card("u1").has_item("人头"), "交出去了就不在身上"
    assert save.has_flag("favor:barbara")


# ---------- 战后动作 ----------


def test_stake_heart_needs_both_battle_won_and_stake(env):
    conn, save, story, ex = env
    save.set_scene("a3.church")
    save.set_battle_result("won")

    # 有胜利但没有木桩：不能钉
    assert "stake_heart" not in [a.id for a in ex.catalog()]

    save.mark_flag("x")            # 触发一次写入，保证状态落库
    save.conn.execute(
        "INSERT INTO characters (group_id,owner_qq,name,template,stats,skills,"
        " gear,perks,state,created_at,updated_at)"
        " VALUES ('qq:1','u2','B','T','{}','{}',?,'[]','{}','x','x')",
        (json.dumps([{"name": "木桩", "kind": "quest"}]),),
    )
    save.conn.commit()
    assert "stake_heart" in [a.id for a in ex.catalog()]


def test_stake_heart_sets_the_flag(env):
    conn, save, story, ex = env
    save.set_scene("a3.church")
    save.set_battle_result("won")
    save.conn.execute(
        "UPDATE characters SET gear = ? WHERE group_id = 'qq:1' AND owner_qq = 'u1'",
        (json.dumps([{"name": "木桩", "kind": "quest"}]),),
    )
    save.conn.commit()

    assert ex.perform("stake_heart").ok
    assert save.has_flag("done:staked")


def test_behead_needs_only_a_win(env):
    conn, save, story, ex = env
    save.set_scene("a3.church")
    assert "behead" not in [a.id for a in ex.catalog()]
    save.set_battle_result("won")
    assert "behead" in [a.id for a in ex.catalog()]
    assert ex.perform("behead").ok
    assert save.has_flag("done:beheaded")


# ---------- 条件化奖励 ----------


def test_ending_favor_is_conditional(env):
    """原文 :105：钱照付，青睐要看「有没有做戏剧性的事」。"""
    conn, save, story, ex = env
    scene = story.scene("a3.ending")
    assert scene.favor_when is not None
    assert not scene.favor_when.matches(C.Context(world=save.world()))


def test_ending_favor_unlocks_after_staking(env):
    conn, save, story, ex = env
    scene = story.scene("a3.ending")
    save.mark_flag("done:staked")
    assert scene.favor_when.matches(C.Context(world=save.world()))


def test_settle_rewards_skips_favor_without_the_flag(env):
    conn, save, story, ex = env
    save.set_scene("a3.ending")
    result = ex.ops.settle_rewards("a3.ending")
    assert result.ok
    rewards = result.data.get("rewards") or {}
    assert "favor" not in rewards, "没钉心脏就不该有青睐"
    assert rewards.get("eb") == 1500, "钱照付"


def test_settle_rewards_includes_favor_after_staking(env):
    conn, save, story, ex = env
    save.set_scene("a3.ending")
    save.mark_flag("done:staked")
    result = ex.ops.settle_rewards("a3.ending")
    assert "favor" in (result.data.get("rewards") or {})


def test_settle_rewards_is_idempotent(env):
    conn, save, story, ex = env
    save.set_scene("a3.ending")
    first = ex.ops.settle_rewards("a3.ending")
    second = ex.ops.settle_rewards("a3.ending")
    assert first.ok and second.ok
    assert second.data.get("items") == [], "第二次不该再结算"
