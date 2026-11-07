"""成长：IP（Improvement Points）与消耗品状态。

规则来源（V3.0 书 p171 + 简化版 doc 11）：
    * 升 N → N+1 需要 ``N × 倍率`` 点 IP（方案 A 统一把倍率取 2）
    * 每场 session 每人 4 点 IP（原书实战 1–9 点，取中位）
    * 实战 IP、扮演奖励由 GM 给；这里只负责记账与校验

状态放在角色卡的 ``state`` 字段里（``ip`` / ``favor_used`` / ``hp`` / ``notes``），
跟卡面数值分开，避免玩家改卡时把成长记录冲掉。
"""

from __future__ import annotations

IP_PER_SESSION = 4
IP_COST_MULTIPLIER = 2
SKILL_MAX = 10
SKILL_MIN = 1


class GrowthError(ValueError):
    """IP 不够、技能越界之类。"""


def blank_state() -> dict:
    return {
        "ip": 0,
        "favor_used": [],
        "hp": None,
        "notes": "",
        "eb": 0,
        "favors": [],
        "settled": [],
    }


def state(card: dict) -> dict:
    """取（必要时补全）卡上的 state 字段。"""
    st = dict(card.get("state") or {})
    for key, value in blank_state().items():
        st.setdefault(key, value)
    card["state"] = st
    return st


def ip_cost(level: int) -> int:
    """把技能从 ``level`` 升到 ``level+1`` 需要的 IP。"""
    return max(1, int(level)) * IP_COST_MULTIPLIER


def award(card: dict, points: int = IP_PER_SESSION) -> int:
    """给卡加 IP，返回加完后的余额。"""
    if points < 0:
        raise GrowthError("IP 不能为负")
    st = state(card)
    st["ip"] = int(st.get("ip") or 0) + int(points)
    return st["ip"]


def raise_skill(card: dict, skill: str, times: int = 1) -> tuple[int, int]:
    """把技能升 ``times`` 级，返回 (新等级, 花掉的 IP)。IP 不够就抛 GrowthError。"""
    st = state(card)
    skills = card.setdefault("skills", {})
    level = int(skills.get(skill, 0)) or int(card.get("skills", {}).get(skill, 0))
    if level <= 0:
        raise GrowthError(f"卡上没有「{skill}」这个技能")
    spent = 0
    for _ in range(max(1, int(times))):
        if level >= SKILL_MAX:
            raise GrowthError(f"「{skill}」已经到 {SKILL_MAX} 级上限")
        cost = ip_cost(level)
        if int(st.get("ip") or 0) < cost:
            raise GrowthError(
                f"IP 不够：升「{skill}」{level}→{level + 1} 要 {cost} 点，你只剩 {st['ip']}"
            )
        st["ip"] = int(st["ip"]) - cost
        spent += cost
        level += 1
    skills[skill] = level
    return level, spent


def spend_favor(card: dict, name: str = "") -> bool:
    """人情是一次性的：用掉就记进 favor_used，避免下次还能白嫖。"""
    st = state(card)
    key = (name or "人情").strip()
    if key in st["favor_used"]:
        raise GrowthError(f"「{key}」这个人情已经用过了")
    st["favor_used"].append(key)
    return True


def favors_left(card: dict) -> list[str]:
    st = state(card)
    owned = [
        item.get("zh", key)
        for key, item in _perk_items(card)
        if key == "Favor" or item.get("zh") == "人情"
    ]
    return [f for f in owned if f not in st["favor_used"]]


def _perk_items(card: dict):
    from cyberpunk import perks as perks_mod

    for key in card.get("perks") or []:
        hit = perks_mod.find(key)
        if hit:
            yield hit[1], hit[2]


# ---------- EB 与人情账本 ----------
#
# 人情是「谁欠你」的叙事欠条：个人人情只对本人，组织人情在组织内可用。
# 引擎只做显式匹配与记账，认不认、要不要加价由 DM 判断；这里不替 DM 编故事。


def eb(card: dict) -> int:
    return int(state(card).get("eb") or 0)


def grant_eb(card: dict, amount: int, *, reason: str = "") -> int:
    if amount < 0:
        raise GrowthError("EB 不能为负")
    st = state(card)
    st["eb"] = int(st.get("eb") or 0) + int(amount)
    return st["eb"]


def favors(card: dict) -> list[dict]:
    """卡上所有人情（含已用），按记录顺序。"""
    return [dict(f) for f in state(card).get("favors") or [] if isinstance(f, dict)]


def grant_favor(
    card: dict,
    *,
    holder: str,
    holder_name: str = "",
    scope: str = "person",
    org: str = "",
    weight: int = 1,
    source_scene: str = "",
    note: str = "",
) -> dict:
    """记一笔欠条；返回记下的条目。"""
    st = state(card)
    entry = {
        "id": f"f-{len(st['favors']) + 1:03d}",
        "holder": str(holder),
        "holder_name": str(holder_name or holder),
        "scope": "org" if scope == "org" else "person",
        "org": str(org),
        "weight": max(1, int(weight)),
        "source_scene": str(source_scene),
        "note": str(note),
        "used": False,
    }
    st["favors"].append(entry)
    return entry


def _target_names(target: dict) -> set[str]:
    names = {
        str(target.get("key") or ""),
        str(target.get("name") or ""),
        str(target.get("holder") or ""),
    }
    return {n for n in names if n}


def favors_for(card: dict, target: dict) -> tuple[list[dict], list[dict]]:
    """返回 (能用的, 名字对得上但范围不匹配的)。"""
    names = _target_names(target)
    orgs = {str(a) for a in target.get("affiliations") or [] if str(a)}
    if str(target.get("name") or ""):
        orgs.add(str(target["name"]))
    usable: list[dict] = []
    mismatched: list[dict] = []
    for entry in favors(card):
        if entry.get("used"):
            continue
        holder = str(entry.get("holder") or "")
        scope = entry.get("scope")
        if scope == "org":
            want = {str(entry.get("org") or ""), holder.replace("org:", ""),
                    str(entry.get("holder_name") or "")}
            if want & orgs:
                usable.append(entry)
            elif str(entry.get("holder_name") or "") in names or holder in names:
                mismatched.append(entry)
            continue
        if holder in names or str(entry.get("holder_name") or "") in names:
            usable.append(entry)
        else:
            mismatched.append(entry)
    return usable, mismatched


def use_favor(card: dict, favor_id: str) -> dict:
    """把某笔人情标记为已用；重复用会抛 GrowthError。"""
    st = state(card)
    for entry in st["favors"]:
        if not isinstance(entry, dict) or entry.get("id") != favor_id:
            continue
        if entry.get("used"):
            raise GrowthError(f"「{entry.get('holder_name') or entry.get('holder')}」这份人情已经用过了")
        entry["used"] = True
        name = str(entry.get("holder_name") or entry.get("holder") or "人情")
        st["favor_used"].append(name)
        return dict(entry)
    raise GrowthError("账上没有这笔人情")


def settle(card: dict, key: str, rewards: dict) -> list[str]:
    """场景奖励结算；同一 ``key`` 只结一次，返回本次结算的说明行。"""
    if not rewards:
        return []
    st = state(card)
    if key in st["settled"]:
        return []
    lines: list[str] = []
    amount = int(rewards.get("eb") or 0)
    if amount:
        grant_eb(card, amount)
        lines.append(f"+{amount} EB")
    points = int(rewards.get("ip") or 0)
    if points:
        award(card, points)
        lines.append(f"+{points} IP")
    favor = rewards.get("favor")
    if isinstance(favor, dict) and favor.get("holder"):
        entry = grant_favor(
            card,
            holder=str(favor["holder"]),
            holder_name=str(favor.get("holder_name") or ""),
            scope=str(favor.get("scope") or "person"),
            org=str(favor.get("org") or ""),
            weight=int(favor.get("weight") or 1),
            source_scene=str(favor.get("source_scene") or ""),
            note=str(favor.get("note") or ""),
        )
        lines.append(f"{entry['holder_name']}欠你一份人情")
    st["settled"].append(key)
    return lines



def render_state(card: dict, hp_max: int | None = None) -> str:
    """一行状态：IP / EB / 人情 / 生命。"""
    st = state(card)
    bits = [f"IP {st['ip']}", f"EB {int(st.get('eb') or 0)}"]
    hp = st.get("hp")
    if hp is not None and hp_max:
        bits.append(f"生命 {hp}/{hp_max}")
    names = [
        str(f.get("holder_name") or f.get("holder") or "人情")
        for f in st.get("favors") or []
        if isinstance(f, dict) and not f.get("used")
    ]
    names.extend(favors_left(card))
    left = list(dict.fromkeys(names))
    if left:
        bits.append("可用人情 " + "、".join(left))
    return " · ".join(bits)
