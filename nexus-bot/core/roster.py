"""角色卡：模板套用、合法性校验、SQLite 存取。

规则上的硬约束都在这里，**不交给模型决定**：

* 属性 2–10（V3 上限 10）
* 技能 1–10
* 模板建卡只允许**最多三次属性互换**（书 p58）
* 派生属性（HITS/STUN/LUCK…）一律由 ``cyberpunk.stats.derived`` 现算
"""

from __future__ import annotations

import json
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cyberpunk import cyber_bonus, perks as perks_mod
from cyberpunk.characters import Character as RulesCharacter
from cyberpunk.names import normalize_template_skill, stats_en_to_zh, to_zh
from cyberpunk.stats import STAT_MAX, STAT_MIN, STAT_ORDER, derived

TEMPLATE_FILE = Path(__file__).resolve().parents[2] / "rules" / "red" / "data" / "templates.json"
MAX_SWAPS = 3
SKILL_MIN, SKILL_MAX = 1, 10

Character = dict[str, Any]


class RuleError(ValueError):
    """角色卡不合规。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- 模板 ----------


def load_templates() -> list[dict]:
    if not TEMPLATE_FILE.exists():
        return []
    return json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def template_by_name(name: str, templates: Iterable[dict] | None = None) -> dict:
    pool = list(templates if templates is not None else load_templates())
    want = (name or "").strip().upper()
    for tpl in pool:
        if tpl["name"].upper() == want:
            return tpl
    # 允许模糊：唯一包含关系
    hits = [t for t in pool if want and want in t["name"].upper()]
    if len(hits) == 1:
        return hits[0]
    raise RuleError(f"没有这个模板：{name}")


def template_menu() -> str:
    names = [t["name"] for t in load_templates()]
    return "、".join(names)


# RED 建卡要用点数买技能；机器人这边给一套「街头底子」保证一开始就能掷检定。
DEFAULT_SKILLS: dict[str, int] = {
    "觉察": 2,
    "教育通识": 2,
    "说服": 2,
    "搏击": 2,
    "闪避": 2,
    "潜行": 2,
}


# ---------- 建卡 ----------


def build(
    *,
    template: str,
    name: str,
    concept: str = "",
    stat_swaps: list[list[str]] | None = None,
    skill_overrides: dict[str, int] | None = None,
    perks: list[str] | None = None,
) -> Character:
    tpl = template_by_name(template)
    # RED 模板是「骰点 1–10 → 十项属性」的对照表，取第 1 行作默认值，
    # 技能不在模板里（RED 建卡时用点数买），认领时留空。
    base_stats = tpl.get("row1") or tpl.get("stats") or {}
    stats = {k: int(base_stats[k]) for k in STAT_ORDER if k in base_stats}
    skills: dict[str, int] = dict(DEFAULT_SKILLS)
    gear = [o.get("item", "") for o in tpl.get("outfit", []) if o.get("item")]
    picked_perks = [en for kind, en, item in (perks_mod.validate(perks or []) or [])]

    swaps = stat_swaps or []
    if len(swaps) > MAX_SWAPS:
        raise RuleError(f"属性最多互换 {MAX_SWAPS} 次")
    for pair in swaps:
        if len(pair) != 2:
            raise RuleError("互换格式应该是 [属性A, 属性B]")
        a, b = (str(x).strip().upper() for x in pair)
        if a not in stats or b not in stats:
            raise RuleError(f"没有这个属性：{a} / {b}")
        stats[a], stats[b] = stats[b], stats[a]

    character: Character = {
        "name": (name or tpl["name"]).strip()[:20],
        "template": tpl["name"],
        "concept": (concept or "").strip()[:120],
        "stats": stats,
        "skills": skills,
        "gear": gear[:6],
        "perks": picked_perks,
    }
    apply_skill_overrides(character, skill_overrides or {})
    validate(character)
    return character


def apply_skill_overrides(character: Character, overrides: dict[str, int]) -> None:
    for raw_name, raw_level in overrides.items():
        skill = normalize_template_skill(str(raw_name))
        if not skill:
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            raise RuleError(f"技能等级要是数字：{skill}")
        if not SKILL_MIN <= level <= SKILL_MAX:
            raise RuleError(f"技能等级 {level} 超出 {SKILL_MIN}–{SKILL_MAX}：{skill}")
        character["skills"][skill] = level


def validate(character: Character) -> None:
    stats = character.get("stats") or {}
    for key in STAT_ORDER:
        if key not in stats:
            raise RuleError(f"缺属性：{key}")
        value = int(stats[key])
        if not STAT_MIN <= value <= STAT_MAX:
            raise RuleError(f"{key}={value} 超出 {STAT_MIN}–{STAT_MAX}")
    if not character.get("skills"):
        raise RuleError("角色至少要有一项技能")
    for skill, level in character["skills"].items():
        if not SKILL_MIN <= int(level) <= SKILL_MAX:
            raise RuleError(f"技能 {skill} 等级 {level} 超出范围")


def apply_patch(character: Character, patch: dict) -> Character:
    """自然语言改卡落地：只接受白名单字段。"""
    updated = json.loads(json.dumps(character))  # 深拷贝

    name = patch.get("name")
    if isinstance(name, str) and name.strip():
        updated["name"] = name.strip()[:20]

    concept = patch.get("concept")
    if isinstance(concept, str) and concept.strip():
        updated["concept"] = concept.strip()[:120]

    stat_changes = patch.get("stat_changes") or {}
    if isinstance(stat_changes, dict):
        for raw_key, raw_value in stat_changes.items():
            key = str(raw_key).strip().upper()
            if key not in STAT_ORDER:
                raise RuleError(f"没有这个属性：{raw_key}")
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                raise RuleError(f"{key} 的值要是数字")
            if not STAT_MIN <= value <= STAT_MAX:
                raise RuleError(f"{key}={value} 超出 {STAT_MIN}–{STAT_MAX}")
            updated["stats"][key] = value

    skill_changes = patch.get("skill_changes") or {}
    if isinstance(skill_changes, dict):
        apply_skill_overrides(updated, skill_changes)

    validate(updated)
    return updated


# ---------- 展示 ----------


def derived_of(character: Character) -> dict[str, int]:
    d = derived(character["stats"])
    return {
        "HP": d.hp,
        "WOUND": d.wound,
        "SD": d.death_save,
        "HUM": d.hum,
        "LUCK": d.luck,
        "RUN": d.run,
        "LEAP": d.leap,
    }


def to_character(card: Character) -> "RulesCharacter":
    """存档里的 dict 卡 → 规则引擎的 Character（派生属性现算）。

    ``cyber_mod`` 由义体表达成（骨肉织绕 体格+2 之类），运行时生效、
    不写死 ``card["stats"]``（避免越出 ``validate_stats``）。
    """
    return RulesCharacter(
        name=card["name"],
        stats={k: int(v) for k, v in card["stats"].items()},
        skills={k: int(v) for k, v in card.get("skills", {}).items()},
        cyber_mod=cyber_bonus.attribute_bonus(card),
    )


def render_card(character: Character) -> str:
    stats = character["stats"]
    d = derived_of(character)
    skills = sorted(character["skills"].items(), key=lambda kv: -kv[1])[:8]
    lines = [
        f"🧬 {character['name']}（{character['template']}）",
    ]
    if character.get("concept"):
        lines.append(f"人设：{character['concept']}")
    zh = stats_en_to_zh()
    lines.append(
        "属性：" + " ".join(f"{zh.get(k, k)}{stats[k]}" for k in STAT_ORDER)
    )
    lines.append(
        f"派生：生命 {d['HP']} / 重伤 {d['WOUND']} / 死亡豁免 {d['SD']}"
        f" / 幸运 {d['LUCK']} / 人性 {d['HUM']}"
    )
    lines.append("技能：" + "、".join(f"{to_zh(k)} {v}" for k, v in skills))
    if character.get("gear"):
        lines.append("装备：" + "、".join(character["gear"][:4]))
    picked = perks_mod.summary(list(character.get("perks") or []))
    if picked:
        lines.append(picked)
    return "\n".join(lines)


# ---------- 存取 ----------


def save(conn, group_id: str, owner_qq: str, character: Character) -> None:
    # 一个玩家只留一张卡：建了核心书卡，简易版那张就不算数了
    try:
        conn.execute(
            "DELETE FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
            (str(group_id), str(owner_qq)),
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        INSERT INTO characters
            (group_id, owner_qq, name, template, concept, stats, skills, gear,
             perks, state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(group_id, owner_qq) DO UPDATE SET
            name=excluded.name, template=excluded.template, concept=excluded.concept,
            stats=excluded.stats, skills=excluded.skills, gear=excluded.gear,
            perks=excluded.perks, state=excluded.state,
            updated_at=excluded.updated_at
        """,
        (
            group_id,
            str(owner_qq),
            character["name"],
            character["template"],
            character.get("concept", ""),
            json.dumps(character["stats"], ensure_ascii=False),
            json.dumps(character["skills"], ensure_ascii=False),
            json.dumps(character.get("gear", []), ensure_ascii=False),
            json.dumps(character.get("perks", []), ensure_ascii=False),
            json.dumps(character.get("state", {}), ensure_ascii=False),
            _now(),
            _now(),
        ),
    )
    conn.commit()


def add_gear(conn, group_id: str, owner_qq: str, item: str) -> bool:
    """给角色补一件装备（战后拾取/GM 发放）；返回是否找到角色卡。"""
    card = get(conn, group_id, owner_qq)
    if card is None:
        return False
    name = (item or "").strip()[:40]
    if not name:
        return False
    gear = list(card.get("gear") or [])
    if name not in gear:
        gear.append(name)
    conn.execute(
        "UPDATE characters SET gear = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(gear[:12], ensure_ascii=False), _now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return True


def remove_gear(conn, group_id: str, owner_qq: str, item: str) -> bool:
    """用掉一件装备（例如手榴弹）；没卡或卡上没有这件就返回 False。"""
    card = get(conn, group_id, owner_qq)
    if card is None:
        return False
    name = (item or "").strip()
    gear = list(card.get("gear") or [])
    if not name or name not in gear:
        return False
    gear.remove(name)
    conn.execute(
        "UPDATE characters SET gear = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(gear[:12], ensure_ascii=False), _now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return True


def set_state(conn, group_id: str, owner_qq: str, st: dict) -> bool:
    """只更新 state（IP/EB/人情/HP），不碰卡面数值。"""
    cur = conn.execute(
        "UPDATE characters SET state = ?, updated_at = ?"
        " WHERE group_id = ? AND owner_qq = ?",
        (json.dumps(st, ensure_ascii=False), _now(), group_id, str(owner_qq)),
    )
    conn.commit()
    return cur.rowcount > 0


def get(conn, group_id: str, owner_qq: str) -> Character | None:
    row = conn.execute(
        "SELECT name, template, concept, stats, skills, gear, perks, state FROM characters"
        " WHERE group_id=? AND owner_qq=?",
        (group_id, str(owner_qq)),
    ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0],
        "template": row[1],
        "concept": row[2] or "",
        "stats": json.loads(row[3]),
        "skills": json.loads(row[4]),
        "gear": json.loads(row[5] or "[]"),
        "perks": json.loads(row[6] or "[]"),
        "state": json.loads(row[7] or "{}"),
    }


def members(conn, group_id: str) -> list[Character]:
    rows = conn.execute(
        "SELECT name, template, concept, stats, skills, gear, perks, state, owner_qq"
        " FROM characters WHERE group_id=? ORDER BY owner_qq",
        (group_id,),
    ).fetchall()
    return [
        {
            "name": r[0],
            "template": r[1],
            "concept": r[2] or "",
            "stats": json.loads(r[3]),
            "skills": json.loads(r[4]),
            "gear": json.loads(r[5] or "[]"),
            "perks": json.loads(r[6] or "[]"),
            "state": json.loads(r[7] or "{}"),
            "owner_qq": r[8],
        }
        for r in rows
    ]


# ---------- RED 技能表（用于校验模型给的技能名） ----------

SKILLS_FILE = Path(__file__).resolve().parents[2] / "rules/red/data/skills.json"


@lru_cache(maxsize=1)
def skill_names() -> tuple[str, ...]:
    try:
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        return tuple(str(x["zh"]) for x in data)
    except Exception:  # noqa: BLE001
        return ()


def resolve_skill(raw: str) -> str | None:
    """把模型写的技能名对到规则书里的那一个；对不上返回 None。"""
    text = str(raw or "").strip().replace(" ", "")
    if not text:
        return None
    pool = skill_names()
    if text in pool:
        return text
    tail = text.split("：")[-1].split(":")[-1].strip()
    if tail in pool:
        return tail
    hits = [x for x in pool if tail and (tail in x or x in tail)]
    return hits[0] if len(hits) == 1 else None
