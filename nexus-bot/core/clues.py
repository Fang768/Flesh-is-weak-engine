"""线索：把剧本里的 ``clues`` 真正跑起来。

剧本里每条线索长这样（``story/demo.json``）::

    {"id": "brake", "text": "刹车痕指向仓库方向", "skill": "觉察",
     "stat": "INT", "dv": 14, "keywords": ["刹车", "轮胎", "痕"]}

规则：

* 玩家这句话里出现任一 ``keywords`` → 触发这条线索的检定；
* 检定 = ``1D10 + 属性 + 技能``，RED 用 ``≥ DV``，简易版用 ``> DV``；
* 成功就把线索写进 ``clues_found`` 并给出原文，失败只说“没看出名堂”，
  **不消耗线索**（可以换个技能/换个人再查）；
* 已经查到的线索不再重复掷骰，直接回“这条你已经查过了”。
"""

from __future__ import annotations

from core.db import now
from core import story
from cyberpunk import dice

MIN_SKILL = 0


def of_scene(story_id: str, scene_id: str) -> list[dict]:
    scene = story.scene(story_id, scene_id) or {}
    return list(scene.get("clues") or [])


def match(story_id: str, scene_id: str, text: str) -> dict | None:
    """这句话在查哪条线索？没有就返回 None。"""
    body = (text or "").strip()
    if not body:
        return None
    for clue in of_scene(story_id, scene_id):
        for kw in clue.get("keywords") or []:
            # 关键词至少两个字：单字（货/血/记）会误伤正常对话
            if len(kw) >= 2 and kw in body:
                return clue
    return None


def seen(conn, group_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT clue_id FROM clues_found WHERE group_id = ?", (group_id,)
    ).fetchall()
    return {r["clue_id"] for r in rows}


def _record(conn, group_id: str, campaign_id: int | None, clue_id: str, by: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO clues_found"
        " (group_id, campaign_id, clue_id, found_by, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (group_id, campaign_id, clue_id, str(by), now()),
    )
    conn.commit()


def attempt(
    conn,
    *,
    group_id: str,
    campaign_id: int | None,
    card: dict,
    clue: dict,
    is_easy: bool = False,
    roller: dice.Roller | None = None,
) -> str:
    """查一次线索，返回可直接发群里的文本。"""
    roller = roller or dice.RandomRoller()
    clue_id = str(clue.get("id", ""))
    if clue_id and clue_id in seen(conn, group_id):
        return f"🔎 这条你已经查过了：{clue.get('text', '')}"

    skill = str(clue.get("skill") or "")
    stat = str(clue.get("stat") or "INT")
    dv = int(clue.get("dv") or 15)
    level = int((card.get("skills") or {}).get(skill, 0))
    stat_value = int((card.get("stats") or {}).get(stat, 2))

    natural = roller.roll(10)
    total = natural + stat_value + level
    ok = total > dv if is_easy else total >= dv

    head = (
        f"{'✅' if ok else '❌'} 调查「{skill}」 DV{dv}"
        f" · 掷出 {natural}，合计 {total}"
    )
    if ok:
        if clue_id:
            _record(conn, group_id, campaign_id, clue_id, card.get("owner_qq", ""))
        return f"{head}\n🔎 {clue.get('text', '')}"
    if natural == 1:
        return f"{head}\n手一抖，反倒把动静弄大了——什么都没看出来。"
    return f"{head}\n这里看不出更多名堂（可以换个人或换个查法）。"


def text_of(conn, group_id: str) -> str:
    """已经查到的线索清单。"""
    rows = conn.execute(
        "SELECT clue_id FROM clues_found WHERE group_id = ? ORDER BY created_at",
        (group_id,),
    ).fetchall()
    if not rows:
        return "还没有查到任何线索。"
    ids = [r["clue_id"] for r in rows]
    lines = ["🔎 已知线索："]
    for cid in ids:
        lines.append(f"· {_text_of_id(cid, group_id)}")
    return "\n".join(lines)


def _text_of_id(clue_id: str, group_id: str) -> str:
    """回查剧本拿线索原文（跨场景扫一遍，找不到就只报 id）。"""
    for st in story.list_stories():
        data = story.load(st)
        for scene in data.get("scenes", {}).values():
            for clue in scene.get("clues") or []:
                if str(clue.get("id")) == clue_id:
                    return str(clue.get("text") or clue_id)
    return clue_id
