"""简易版角色卡的存取（表 ``easy_cards``）。

V3 的角色卡走 ``core.roster``（十属性含 STR/CON，没有 LUCK/EMP）；
简易版的属性是 INT/REF/DEX/TECH/COOL/WILL/LUCK/MOVE/BODY/EMP，两套不通用，
所以单独一张表，卡里的 ``data`` 直接存模板 JSON（含改名）。

玩法判定用 Easy Mode 的量纲：
    1d10 + 属性 + 技能 > DV（13/15/17/21/24），平局算守方赢
"""

from __future__ import annotations

import json
import sqlite3

from core.db import now
from cyberpunk import easy

CLAIM_COOLDOWN = 0  # 占位：以后要限制换卡频率就改这里


def save(conn: sqlite3.Connection, group_id: str, owner_qq: str,
         template: dict, name: str | None = None) -> dict:
    """把模板存成这个人的卡（同一人重复认领会覆盖）。"""
    # 一个玩家只留一张卡：领了简易版，核心书那张就不算数了
    conn.execute(
        "DELETE FROM characters WHERE group_id = ? AND owner_qq = ?",
        (str(group_id), str(owner_qq)),
    )
    data = dict(template)
    if name:
        data["name"] = name
    payload = json.dumps(data, ensure_ascii=False)
    stamp = now()
    conn.execute(
        """INSERT INTO easy_cards
               (group_id, owner_qq, name, template, data, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (group_id, owner_qq) DO UPDATE SET
               name = excluded.name, template = excluded.template,
               data = excluded.data, updated_at = excluded.updated_at""",
        (group_id, owner_qq, data["name"], data["role"], payload, stamp, stamp),
    )
    conn.commit()
    return data


def add_gear(conn: sqlite3.Connection, group_id: str, owner_qq: str,
             item: str) -> bool:
    """给简易卡补一件装备（战后拾取/GM 发放）；返回是否找到角色卡。"""
    data = get(conn, group_id, owner_qq)
    if data is None:
        return False
    name = (item or "").strip()[:40]
    if not name:
        return False
    gear = list(data.get("gear") or [])
    if name not in gear:
        gear.append(name)
    data["gear"] = gear
    conn.execute(
        "UPDATE easy_cards SET data = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(data, ensure_ascii=False), now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return True


def remove_gear(conn: sqlite3.Connection, group_id: str, owner_qq: str,
                item: str) -> bool:
    """用掉一件装备（例如手榴弹）；没卡或卡上没有这件就返回 False。"""
    data = get(conn, group_id, owner_qq)
    if data is None:
        return False
    name = (item or "").strip()
    gear = list(data.get("gear") or [])
    if not name or name not in gear:
        return False
    gear.remove(name)
    data["gear"] = gear
    conn.execute(
        "UPDATE easy_cards SET data = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(data, ensure_ascii=False), now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return True


def set_state(conn: sqlite3.Connection, group_id: str, owner_qq: str,
              st: dict) -> bool:
    """只更新 data 里的 state（IP/EB/人情），不碰 HP/装备等卡面字段。"""
    data = get(conn, group_id, owner_qq)
    if data is None:
        return False
    data["state"] = dict(st)
    conn.execute(
        "UPDATE easy_cards SET data = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(data, ensure_ascii=False), now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return True


def get(conn: sqlite3.Connection, group_id: str, owner_qq: str) -> dict | None:
    row = conn.execute(
        "SELECT data FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
        (group_id, owner_qq),
    ).fetchone()
    return json.loads(row[0]) if row else None


def members(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT owner_qq, name, template, data FROM easy_cards WHERE group_id = ?",
        (group_id,),
    ).fetchall()
    out = []
    for qq, name, template, data in rows:
        card = json.loads(data)
        card["owner_qq"] = qq
        card["template"] = template
        out.append(card)
    return out


def release(conn: sqlite3.Connection, group_id: str, owner_qq: str) -> bool:
    cur = conn.execute(
        "DELETE FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
        (group_id, owner_qq),
    )
    conn.commit()
    return cur.rowcount > 0


def render(card: dict) -> str:
    """用 easy.format_card 渲染（card 里已有 wound/death_save 就重算一遍）。"""
    view = dict(card)
    view.setdefault("wound", int(view.get("hp", 0)) // 2)
    view.setdefault("death_save", int(view.get("stats", {}).get("BODY", 0)))
    view.setdefault("skills", {})
    view.setdefault("weapons", [])
    view.setdefault("cyber", [])
    view.setdefault("armor", "无")
    view.setdefault("sp", 0)
    view.setdefault("ability", "")
    view.setdefault("hook", "")
    return easy.format_card(view)
