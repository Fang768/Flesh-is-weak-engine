"""战斗：按核心书（书内页 167–182 / PDF 185–200）实现的先攻队列与回合。

书里写死的规则（不复述、直接按它做）：

* 先攻 = 反应 + 1D10，从大到小排成先攻队列；同值重掷，高者在前（书内页 168）。
* 每回合约 3 秒，每人 **1 次移动动作 + 1 次其他动作**；队列走完从队首重来 = 新轮次。
* 远程攻击 = `反应 + 武器技能 + 1D10` vs **距离决定的 DV**（书内页 175）。
  世界始终有物理场景，所以距离也必须来自场景：有地图就按格算；没有地图数据就用
  encounter/scene 的 `range_m`，或者 DM 现估的米数（`combatants.stat.range_m`）。
  两者都没有时**不猜 DV**，直接请 DM 给一个距离（或玩家说「距离 20 米」）。
* 近战攻击 = `敏捷 + 近战武器 + 1D10` vs `敏捷 + 闪避 + 1D10`（书内页 175 示例）。
  近战伤害**无视一半护甲**（向上取整）。
* 瞄准射击：花掉本回合全部其他动作、ROF 限 1、判定 **−8**；打头「穿透护甲后伤害翻倍」，
  打腿「断腿（严重伤势）」，打手中物「东西脱手」（书内页 174）。
* 装填 = 一个其他动作（书内页 174）。

没有建模的：弹药计数、延迟动作的具体时机、中毒与疾病的细化流程、爆炸对地形的破坏。
"""

from __future__ import annotations

import json
import re
from collections import deque
from functools import lru_cache
from pathlib import Path
import sqlite3
from dataclasses import dataclass, field

from cyberpunk import dice, easy
from cyberpunk.crit import SERIOUS_EXTRA_DAMAGE, SERIOUS_TRIGGER_SIXES
from cyberpunk.damage import roll_dice_full as _roll_dice_full
from core import easy_roster, roster
from core import map as gridmap
from core.db import now

# ── 敌人速览（数值是我们给的 NPC 面板，不是书的规则）────────────


@dataclass(frozen=True)
class EnemyPreset:
    name: str
    hp: int
    ref: int
    dex: int
    dodge: int
    skill: int
    sp: int
    damage: str
    #: 体格（死亡豁免定位目标，full.txt:15943）
    body: int = 5
    #: 重伤线（enemies.json 的 wound）；0 = 用 max_hp // 2
    wound: int = 0
    #: 用于语义匹配的标签（改造/护甲/武器/备注），如「水平阻尼降噪」
    tags: tuple[str, ...] = ()


ENEMY_PRESETS: dict[str, EnemyPreset] = {
    "打手": EnemyPreset("打手", hp=20, ref=6, dex=6, dodge=4, skill=4, sp=4, damage="2D6"),
    "精英": EnemyPreset("精英", hp=35, ref=7, dex=7, dodge=6, skill=6, sp=7, damage="3D6"),
    "头目": EnemyPreset("头目", hp=50, ref=8, dex=8, dodge=8, skill=8, sp=9, damage="3D6+1"),
}
DEFAULT_ENEMY = "打手"


ENEMIES_FILE = Path(__file__).resolve().parents[2] / "rules/red/data/enemies.json"


@lru_cache(maxsize=1)
def _enemy_data() -> dict:
    """读 `rules/red/data/enemies.json`；读不到就当空表（回退内置三档）。"""
    if not ENEMIES_FILE.exists():
        return {}
    try:
        raw = json.loads(ENEMIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


@lru_cache(maxsize=128)
def _preset_from_json(name: str) -> EnemyPreset | None:
    data = _enemy_data().get(name)
    if not data:
        return None
    stats = data.get("stats") or {}
    skills = data.get("skills") or {}
    weapons = data.get("weapons") or []
    attack = max((int(skills.get(str(w.get("skill", "")), 0)) for w in weapons), default=0)
    tags = [name, str(data.get("note") or ""), str(data.get("armor") or "")]
    cyber = data.get("cyber") or []
    if isinstance(cyber, str):
        tags.append(cyber)
    else:
        tags.extend(str(x) for x in cyber)
    for w in weapons:
        tags.extend([str(w.get("name") or ""), str(w.get("skill") or "")])
    return EnemyPreset(
        name,
        hp=int(data.get("hp", 20)),
        ref=int(stats.get("REF", 5)),
        dex=int(stats.get("DEX", 5)),
        dodge=int(skills.get("闪避", 4)),
        skill=attack,
        sp=int(data.get("sp", 4)),
        damage=str(weapons[0].get("damage", "2D6")) if weapons else "2D6",
        body=int(stats.get("BODY", 5)),
        wound=int(data.get("wound") or 0),
        tags=tuple(t for t in tags if t),
    )


def enemy_preset(name: str) -> EnemyPreset | None:
    """先查剧本数据（enemies.json），再查内置三档。"""
    return _preset_from_json(name) or ENEMY_PRESETS.get(name)


def enemy_names() -> list[str]:
    return list(dict.fromkeys(list(_enemy_data()) + list(ENEMY_PRESETS)))

#: 技能 → 伤害（书里的武器表还有具体枪械，这里先用按技能的粗粒度）
WEAPON_DAMAGE = {
    "手枪": "2D6",
    "抵肩枪械": "2D6",
    "自动武器": "2D6",
    "近战武器": "2D6",
    "重武器": "3D6",
    "武术": "2D6",
    "搏击": "1D6",
}
MELEE_SKILLS = {"近战武器", "武术", "搏击"}
FALLBACK_SKILL = "手枪"
DEFAULT_SP = 4

#: 约定：认不出武器名时按手枪的距离表算 DV（书内页 173 的表是按武器分的，
#: 敌人 preset 只记了伤害骰；手枪 2D6 正好对上大多数杂兵的伤害串）
DEFAULT_WEAPON = "手枪"
#: 没有地图也没有场景距离时，不猜 DV，让 DM / 玩家给一个距离
RANGE_ASK = ("距离不明：这里没有地图数据，也没有场景距离。"
             "说一句「距离 20 米」告诉我在多远，或者让 DM 按场景估一下"
             "（这句话不算动作）。")
#: 瞄准的判定修正（书内页 174）
AIM_PENALTY = -8
#: 瞄准部位
AIM_PARTS = {"头": "head", "头部": "head", "腿": "leg", "腿部": "leg",
             "手": "hand", "手中物": "hand", "手里的东西": "hand"}


class CombatError(Exception):
    """战斗流程走不下去（没开打 / 不是你的回合 / 目标不存在…）。"""


@dataclass(slots=True)
class Combatant:
    group_id: str
    owner_qq: str
    name: str
    side: str          # "pc" | "enemy"
    initiative: int
    hp: int
    max_hp: int
    sp: int = 0
    sp_wear: int = 0
    damage: str = ""
    dead: bool = False
    #: 致命伤（HP≤0 还没死透）：仅 PC 侧用，每回合开始要过死亡豁免，失败才 dead
    #: （rules/red_core/raw/full.txt:15730-15745、15943-15949）
    mortal: bool = False
    #: 死亡豁免目标 = 体格 BODY；敌人取 enemies.json 的 stats.BODY，缺省 5
    body: int = 5
    #: 死亡豁免累积惩罚：每次判死 +1，稳定伤势恢复到 1 HP 后清零（full.txt:15943-15948）
    death_save_penalty: int = 0
    #: 严重伤势列表（CriticalInjury.key，如 "arm_lost"）。严重伤不可重复获得，
    #: 伤害骰出 ≥2 个 6 时触发，投 2D6 查身体/头部伤势表（full.txt:15743-15748）。
    crits: tuple[str, ...] = ()
    # 敌人面板 / 玩家从卡上读
    ref: int = 5
    dex: int = 5
    dodge: int = 5
    skill: int = 0
    #: 敌人的行为脚本（targets / flee_if / on_flee_fail / join_round）
    script: dict = field(default_factory=dict)
    #: 语义标签（改造、护甲、武器、备注），给 negate_if 之类做匹配
    tags: tuple[str, ...] = ()
    #: 重伤线；0 = 用 max_hp // 2
    wound: int = 0
    #: 已脱离战斗（逃跑成功），既不算活人也不算倒下
    escaped: bool = False
    #: campaign 演员表 id；空 = 杂兵，战斗结束不留档
    actor_id: str = ""
    #: 命格："" 普通；"cannot_die" 打不死，HP 归零强制退场
    fate: str = ""
    #: 掉到这个 HP 以下就抽身退场（0 = 不触发）
    withdraw_at: int = 0
    #: 网格位置（-1 = 没有地图 / 未落位）
    x: int = -1
    y: int = -1
    #: 没有地图时的距离（米）：encounter 的 range_m 或 DM 估的米数，0 = 没给
    range_m: int = 0
    #: 开打时的落位提示（不落库；只有 start 用）
    spawn: tuple[int, int] | None = None

    @property
    def alive(self) -> bool:
        # 致命伤角色（HP≤0 但还没判死）仍留在先攻队列里，好让他们自己过死亡豁免
        return not self.dead and not self.escaped and (self.hp > 0 or self.mortal)

    @property
    def effective_sp(self) -> int:
        return max(0, self.sp - self.sp_wear)

    @property
    def crits_list(self) -> list["CriticalInjury"]:
        """严重伤势对象列表（按现有 crits 顺序）。"""
        from cyberpunk.crit import BY_KEY
        return [BY_KEY[key] for key in self.crits if key in BY_KEY]

    @property
    def action_penalty(self) -> int:
        """严重伤势「所有动作判定」累计惩罚（手指粉碎/脑损伤/断臂手类）。"""
        return sum(inj.action_penalty for inj in self.crits_list)

    @property
    def ranged_penalty(self) -> int:
        """严重伤势「远程攻击判定」累计惩罚（失去眼睛/眼睛受损）。"""
        return sum(inj.ranged_penalty for inj in self.crits_list)

    @property
    def melee_penalty(self) -> int:
        """严重伤势「近战攻击判定」累计惩罚（肌肉撕裂）。"""
        return sum(inj.melee_penalty for inj in self.crits_list)

    @property
    def move_penalty(self) -> int:
        """严重伤势累计移速惩罚（最低把 MOVE 扣到剩 1 格）。"""
        return sum(inj.move_penalty for inj in self.crits_list)

    @property
    def no_dodge(self) -> bool:
        """断腿：无法闪避（full.txt:15876）。"""
        return any(inj.no_dodge for inj in self.crits_list)

    @property
    def no_speak(self) -> bool:
        """气管破裂：无法说话（full.txt:15910 附近）。"""
        return any(inj.no_speak for inj in self.crits_list)

    @property
    def head_mult(self) -> int:
        """瞄准头部的伤害倍率：默认 2，颅骨骨裂后按伤势写成 3（full.txt:15904）。"""
        return max((inj.head_mult for inj in self.crits_list), default=0)

    @property
    def walk_damage(self) -> bool:
        """肋骨骨折/体内异物：徒步行进 >4m 的回合要再吃一次额外伤害。"""
        return any(inj.walk_damage for inj in self.crits_list)

    @property
    def no_move_next(self) -> bool:
        """耳朵受损：步行 >4m 之后的下个回合不能移动（full.txt:15912-15920）。"""
        return any(inj.no_move_next for inj in self.crits_list)

    @property
    def wound_penalty(self) -> int:
        """HP 伤势 + 严重伤势「所有动作」判定的总合惩罚。"""
        base = 0
        if self.hp < 1:
            base = -4
        elif self.max_hp and self.hp * 2 <= self.max_hp:
            base = -2
        return base + self.action_penalty

    def line(self) -> str:
        sp = f" SP {self.effective_sp}" if self.effective_sp else ""
        return f"{self.name} {max(0, self.hp)}/{self.max_hp} HP{sp}"


FACTION_LABELS = {"pc": "玩家", "enemy": "敌人"}


def faction_label(combat: "Combat", side: str) -> str:
    """阵营显示名：card 自己指定的 label > 内置 > 原样。"""
    return combat.labels.get(side) or FACTION_LABELS.get(side) or side


def foes(combat: "Combat", actor: "Combatant") -> list["Combatant"]:
    """actor 能打谁：按阵营关系表算，未声明的阵营默认敌视所有非本阵营。"""
    allowed = combat.factions.get(actor.side)
    out = []
    for c in combat.order:
        if c is actor or not c.alive or c.side == actor.side:
            continue
        if allowed is None or c.side in allowed:
            out.append(c)
    return out


def provoke(combat: "Combat", a: str, b: str) -> None:
    """一方动手就不再中立：双向登记敌意。"""
    if not a or not b or a == b:
        return
    combat.factions.setdefault(a, set()).add(b)
    combat.factions.setdefault(b, set()).add(a)


@dataclass(slots=True)
class Slot:
    """这一轮用掉的动作用量（书内页 168：1 移动 + 1 其他）。"""

    move: bool = False
    action: bool = False
    extra_move: bool = False
    #: 严重伤势「下个回合不能用其他动作」（脊柱受伤，full.txt:15856）：写回合号。
    #: 用回合号而不是布尔，是因为 reset() 每轮都会跑，布尔会被立刻清掉。
    no_action_round: int = 0
    #: 严重伤势「步行超过 4m 后下个回合无法移动」（耳朵受损，full.txt:15912）。
    no_move_round: int = 0

    def reset(self) -> None:
        self.move = self.action = self.extra_move = False


@dataclass(slots=True)
class Combat:
    group_id: str
    campaign_id: int | None
    scene: str
    round: int
    turn_index: int
    active: bool
    combatants: list[Combatant] = field(default_factory=list)
    # 阵营关系：side(faction id) -> 它主动攻击的阵营集合；未声明的阵营 = 打所有非本阵营
    factions: dict[str, set[str]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    #: 环境/剧本修正（设计 §3），形如 {id,value,rounds,applies_to,negate_if,label,note}
    modifiers: list[dict] = field(default_factory=list)
    #: 网络战状态（阶段 3）：owner_qq -> {connected, controlled:[node_id]}，另存 flags
    net: dict = field(default_factory=dict)
    #: 场景地图 id；空 = 没有地图数据（距离改问 range_m / DM / 玩家）
    map_id: str = ""
    #: 掩体耐久：cover id -> 当前 hp；空 = 用地图文件里的初始值
    covers: dict = field(default_factory=dict)

    @property
    def order(self) -> list[Combatant]:
        """先攻队列（活人，含敌人），从大到小。"""
        return sorted([c for c in self.combatants if c.alive],
                      key=lambda c: (-c.initiative, c.owner_qq))

    @property
    def enemies(self) -> list[Combatant]:
        return [c for c in self.order if c.side != "pc"]

    @property
    def pcs(self) -> list[Combatant]:
        return [c for c in self.order if c.side == "pc"]

    @property
    def current(self) -> Combatant | None:
        order = self.order
        if not order:
            return None
        return order[self.turn_index % len(order)]


# 动作用量放内存（重启即重置；回合与血量在库里）
_SLOTS: dict[str, dict[str, Slot]] = {}


def _slots(combat: Combat) -> dict[str, Slot]:
    book = _SLOTS.setdefault(combat.group_id, {})
    for c in combat.combatants:
        book.setdefault(c.owner_qq, Slot())
    return book


def _reset_slots(combat: Combat) -> None:
    for slot in _slots(combat).values():
        slot.reset()


# ── 存取 ──────────────────────────────────────────────────────


def load(conn, group_id: str) -> Combat | None:
    row = conn.execute(
        "SELECT campaign_id, scene, round, turn_index, active, factions, labels, modifiers,"
        " net, map, covers FROM combats WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        return None
    combat = Combat(
        group_id=group_id,
        campaign_id=row["campaign_id"],
        scene=row["scene"] or "",
        round=int(row["round"]),
        turn_index=int(row["turn_index"]),
        active=bool(row["active"]),
    )
    try:
        raw_f = json.loads(row["factions"] or "{}")
        combat.factions = {str(k): set(v) for k, v in raw_f.items()}
        combat.labels = {str(k): str(v) for k, v in
                         (json.loads(row["labels"] or "{}") or {}).items()}
    except (ValueError, TypeError, KeyError):
        pass
    try:
        raw_m = json.loads(row["modifiers"] or "[]")
        combat.modifiers = [e for e in raw_m if isinstance(e, dict)]
    except (ValueError, TypeError):
        pass
    try:
        raw_n = json.loads(row["net"] or "{}")
        combat.net = raw_n if isinstance(raw_n, dict) else {}
    except (ValueError, TypeError):
        pass
    combat.map_id = str(row["map"] or "")
    try:
        raw_cov = json.loads(row["covers"] or "{}")
        combat.covers = {str(k): int(v) for k, v in raw_cov.items()} if isinstance(
            raw_cov, dict) else {}
    except (ValueError, TypeError):
        pass
    for crow in conn.execute(
        "SELECT owner_qq, name, side, initiative, hp, max_hp, sp, sp_wear, damage, dead,"
        " script, stat, escaped, actor_id, fate, withdraw_at, x, y FROM combatants"
        " WHERE group_id = ? ORDER BY initiative DESC, owner_qq",
        (group_id,),
    ).fetchall():
        base = str(crow["name"])
        preset = enemy_preset(base.rstrip("0123456789"))
        side = str(crow["side"] or "pc")
        try:
            raw_s = json.loads(crow["script"] or "{}")
            script = raw_s if isinstance(raw_s, dict) else {}
        except (ValueError, TypeError):
            script = {}
        c = (
            Combatant(
                group_id=group_id,
                owner_qq=str(crow["owner_qq"]),
                name=base,
                side=side,
                initiative=int(crow["initiative"]),
                hp=int(crow["hp"]),
                max_hp=int(crow["max_hp"]),
                sp=int(crow["sp"]),
                sp_wear=int(crow["sp_wear"]),
                damage=str(crow["damage"] or ""),
                dead=bool(crow["dead"]),
                ref=preset.ref if preset else 5,
                dex=preset.dex if preset else 5,
                dodge=preset.dodge if preset else 5,
                skill=preset.skill if preset else 5,
                body=preset.body if preset else 5,
                script=script,
                escaped=bool(crow["escaped"]),
                actor_id=str(crow["actor_id"] or ""),
                fate=str(crow["fate"] or ""),
                withdraw_at=int(crow["withdraw_at"] or 0),
                x=int(crow["x"] if crow["x"] is not None else -1),
                y=int(crow["y"] if crow["y"] is not None else -1),
                wound=preset.wound if preset else 0,
                tags=preset.tags if preset else (),
            )
        )
        try:
            raw_stat = json.loads(crow["stat"] or "{}")
        except (ValueError, TypeError):
            raw_stat = {}
        if isinstance(raw_stat, dict) and raw_stat:
            c.ref = int(raw_stat.get("ref", c.ref))
            c.dex = int(raw_stat.get("dex", c.dex))
            c.dodge = int(raw_stat.get("dodge", c.dodge))
            c.skill = int(raw_stat.get("skill", c.skill))
            c.wound = int(raw_stat.get("wound", c.wound))
            c.range_m = int(raw_stat.get("range_m", c.range_m))
            c.mortal = bool(raw_stat.get("mortal", c.mortal))
            c.body = int(raw_stat.get("body", c.body))
            c.death_save_penalty = int(raw_stat.get("death_save_penalty", c.death_save_penalty))
            tags = raw_stat.get("tags")
            if isinstance(tags, list):
                c.tags = tuple(str(t) for t in tags)
            crits = raw_stat.get("crits")
            if isinstance(crits, list):
                c.crits = tuple(str(t) for t in crits)
        if c.side == "pc":
            card = roster.get(conn, group_id, c.owner_qq) or easy_roster.get(
                conn, group_id, c.owner_qq
            )
            if card:
                st = card.get("stats") or {}
                c.ref = int(st.get("REF", c.ref))
                c.dex = int(st.get("DEX", c.dex))
                c.dodge = int((card.get("skills") or {}).get("闪避", c.dodge))
                c.tags = _card_tags(card)
        combat.combatants.append(c)
    return combat


def _stat_json(c: Combatant) -> str:
    """战斗员的运行时属性（gdata 覆盖过的，不能只靠 preset 还原）。"""
    return json.dumps({
        "ref": c.ref, "dex": c.dex, "dodge": c.dodge, "skill": c.skill,
        "wound": c.wound, "range_m": c.range_m, "tags": list(c.tags),
        "mortal": c.mortal, "body": c.body, "death_save_penalty": c.death_save_penalty,
        "crits": list(c.crits),
    }, ensure_ascii=False)


def _save(conn, combat: Combat) -> None:
    conn.execute(
        "UPDATE combats SET round = ?, turn_index = ?, active = ?, factions = ?,"
        " labels = ?, modifiers = ?, net = ?, map = ?, covers = ?, updated_at = ?"
        " WHERE group_id = ?",
        (combat.round, combat.turn_index, int(combat.active),
         json.dumps({k: sorted(v) for k, v in combat.factions.items()},
                    ensure_ascii=False),
         json.dumps(combat.labels, ensure_ascii=False),
         json.dumps(combat.modifiers, ensure_ascii=False),
         json.dumps(combat.net, ensure_ascii=False), combat.map_id,
         json.dumps(combat.covers, ensure_ascii=False), now(), combat.group_id),
    )
    for c in combat.combatants:
        conn.execute(
            "UPDATE combatants SET hp = ?, max_hp = ?, sp = ?, sp_wear = ?, dead = ?,"
            " initiative = ?, script = ?, stat = ?, escaped = ?, actor_id = ?, fate = ?,"
            " withdraw_at = ?, x = ?, y = ? WHERE group_id = ? AND owner_qq = ?",
            (c.hp, c.max_hp, c.sp, c.sp_wear, int(c.dead), c.initiative,
             json.dumps(c.script, ensure_ascii=False), _stat_json(c),
             int(c.escaped),
             c.actor_id, c.fate, c.withdraw_at, c.x, c.y,
             combat.group_id, c.owner_qq),
        )
    conn.commit()


def _insert(conn, combat: Combat) -> None:
    conn.execute("DELETE FROM combatants WHERE group_id = ?", (combat.group_id,))
    conn.execute("DELETE FROM combats WHERE group_id = ?", (combat.group_id,))
    conn.execute(
        "INSERT INTO combats (group_id, campaign_id, scene, round, turn_index, active,"
        " factions, labels, modifiers, net, map, covers, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (combat.group_id, combat.campaign_id, combat.scene, combat.round,
         combat.turn_index, int(combat.active),
         json.dumps({k: sorted(v) for k, v in combat.factions.items()},
                    ensure_ascii=False),
         json.dumps(combat.labels, ensure_ascii=False),
         json.dumps(combat.modifiers, ensure_ascii=False),
         json.dumps(combat.net, ensure_ascii=False), combat.map_id,
         json.dumps(combat.covers, ensure_ascii=False), now(), now()),
    )
    for c in combat.combatants:
        conn.execute(
            "INSERT INTO combatants (group_id, owner_qq, name, side, initiative, hp,"
            " max_hp, sp, sp_wear, damage, dead, script, stat, escaped, actor_id, fate,"
            " withdraw_at, x, y)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (combat.group_id, c.owner_qq, c.name, c.side, c.initiative, c.hp,
             c.max_hp, c.sp, c.sp_wear, c.damage, int(c.dead),
             json.dumps(c.script, ensure_ascii=False), _stat_json(c),
             int(c.escaped),
             c.actor_id, c.fate, c.withdraw_at, c.x, c.y),
        )
    conn.commit()


# ── 地图 / 场景 ───────────────────────────────────────────────


def _scene_data(conn, group_id: str) -> dict:
    """当前场景的 JSON 段（没有就返回 {}）。"""
    row = conn.execute(
        "SELECT c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return {}
    scene = data.get("scenes", {}).get(str(row["scene"] or "")) or {}
    return scene if isinstance(scene, dict) else {}


def _map_for_id(map_id: str):
    if not map_id:
        return None
    try:
        return gridmap.load(str(map_id))
    except Exception:
        return None


def _map_for(combat: "Combat"):
    return _map_for_id(combat.map_id)


def _cover_hp(combat: "Combat", cover) -> int:
    return int(combat.covers.get(cover.id, cover.hp))


_WEAPON_TABLE = ("手枪", "冲锋枪", "霰弹枪", "突击步枪", "狙击步枪", "弓", "榴弹发射器", "火箭筒")


def _weapon_of(c: "Combatant", skill_name: str = "") -> str | None:
    """从技能名/伤害串里认出武器表里的武器。"""
    if skill_name in _WEAPON_TABLE:
        return skill_name
    text = c.damage or ""
    for name in _WEAPON_TABLE:
        if name in text or any(name in str(tag) for tag in c.tags):
            return name
    return None


def _shot_dv(combat: "Combat", me: "Combatant", target: "Combatant", *,
             melee: bool, weapon: str | None) -> tuple[int | None, str]:
    """有地图时按格算 DV / 视线 / 近战相邻；没地图返回 (None, "")。"""
    game = _map_for(combat)
    if game is None or me.x < 0 or me.y < 0 or target.x < 0 or target.y < 0:
        return None, ""
    here, there = (me.x, me.y), (target.x, target.y)
    if melee:
        if game.meters(here, there) > 2:
            return None, f"够不着：{me.name}和{target.name}隔着 {game.meters(here, there)} 米。"
        return None, ""
    blockers = game.blockers(here, there)
    if blockers:
        return None, f"视线被{blockers[0]}挡住，这一枪打不出去。"
    dv = game.dv(here, there, weapon) if weapon else None
    if dv is None:
        return None, f"超出{weapon or '武器'}的射程（{game.meters(here, there)} 米），打不到。"
    return dv, ""


def _range_dv(combat: "Combat", me: "Combatant", target: "Combatant", *,
              melee: bool, weapon: str | None) -> tuple[int | None, str, str]:
    """这一枪的距离 DV。返回 (dv, 距离说明, 打不了的原因)。

    世界始终有场景，所以距离按优先级取：① 地图格子 ② 场景/遭遇写的
    `range_m` 或 DM 估的米数 ③ 都没有就**不猜**，返回 (None, "", RANGE_ASK)
    让 DM 或玩家先给距离（书内页 173 的 DV 只看距离，没有「中距默认值」）。
    """
    game = _map_for(combat)
    if game is not None and me.x >= 0 and me.y >= 0 and target.x >= 0 and target.y >= 0:
        dv, problem = _shot_dv(combat, me, target, melee=melee, weapon=weapon)
        meters = game.meters((me.x, me.y), (target.x, target.y))
        return dv, f"，{meters} 米", problem
    if melee:
        return None, "", ""
    if target.range_m > 0:
        name = weapon or DEFAULT_WEAPON
        dv = gridmap.dv_for_meters(name, target.range_m)
        if dv is None:
            return None, "", (f"超出{name}的射程（{target.range_m} 米），打不到。"
                              "换个目标，或者先靠近。")
        return dv, f"，{target.range_m} 米（估）", ""
    return None, "", RANGE_ASK


def close_range(conn, group_id: str, actor_qq: str, text: str) -> str | None:
    """玩家/ DM 用一句话给出距离（「距离 20 米」「大概三十米」）。

    世界始终有场景，所以没有地图数据时距离要么来自剧本的 `range_m`，
    要么由人给一个数。这里把数字写进所有还没定距离的参战者身上，
    返回一句确认；这句话不像在报距离就返回 None（交给别的分支处理）。
    """
    body = (text or "").strip()
    if not body:
        return None
    m = re.search(r"(?:距离|大概|大约|约|有)\s*(\d+)\s*(?:米|m|码)?", body)
    if m is None:
        m = re.search(r"(\d+)\s*(?:米|m)\b", body)
    if m is None:
        return None
    meters = int(m.group(1))
    if meters <= 0:
        return None
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    meters = min(meters, 800)
    for c in combat.combatants:
        c.range_m = meters
    _save(conn, combat)
    weapon = _weapon_of(next((c for c in combat.combatants
                             if c.owner_qq == actor_qq and c.side == "pc"), None)
                       or combat.combatants[0]) or DEFAULT_WEAPON
    dv = gridmap.dv_for_meters(weapon, meters)
    tail = f"，按{weapon}的距离表 DV {dv}" if dv is not None else \
        f"，{weapon}这个距离够不着"
    return f"📏 距离记成 {meters} 米{tail} 。"


def _spots_from(game, start: tuple[int, int] | None) -> list[tuple[int, int]]:
    """从 start 开始 BFS 所有可走格；start 无效则从第一个可走格开始。"""
    if start is None or not game.walkable(start):
        start = None
        for y, row in enumerate(game.grid):
            for x, _ch in enumerate(row):
                if game.walkable((x, y)):
                    start = (x, y)
                    break
            if start:
                break
    if start is None:
        return []
    seen = {start}
    queue = deque([start])
    out: list[tuple[int, int]] = []
    while queue:
        pos = queue.popleft()
        out.append(pos)
        for nxt in game.neighbors(pos):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return out


def _place_combatants(combat: "Combat", game) -> None:
    """按 spawns 给所有人落位；同阵营依次占最近的空格。"""
    taken: set[tuple[int, int]] = set()
    pcs = [c for c in combat.combatants if c.side == "pc"]
    enemies = [c for c in combat.combatants if c.side != "pc"]
    for c in pcs:
        _assign_spot(c, game.spawns.get("pc"), game, taken)
    for c in enemies:
        spot = c.spawn or game.spawns.get(c.side) or game.spawns.get("enemy")
        _assign_spot(c, spot, game, taken)


def _assign_spot(c: Combatant, start, game, taken: set[tuple[int, int]]) -> None:
    for pos in _spots_from(game, start):
        if pos not in taken:
            c.x, c.y = pos
            taken.add(pos)
            return
    c.x = c.y = -1


# ── 开打 ──────────────────────────────────────────────────────


def start(conn, group_id: str, *, campaign_id: int | None = None, scene: str = "",
          enemies: tuple[tuple[str, int], ...] = ((DEFAULT_ENEMY, 1),),
          modifiers: tuple[dict, ...] = (),
          roller: dice.Roller | None = None) -> Combat:
    roller = roller or dice.RandomRoller()
    cards = roster.members(conn, group_id)
    easy = easy_roster.members(conn, group_id)
    if not cards and not easy:
        raise CombatError("群里还没有角色卡，先让玩家认领/建卡。")

    combat = Combat(group_id=group_id, campaign_id=campaign_id, scene=scene,
                    round=1, turn_index=0, active=True,
                    modifiers=[dict(m) for m in modifiers if isinstance(m, dict)])

    for card in cards:
        rules = roster.to_character(card)
        stats = card.get("stats") or {}
        stored = (card.get("state") or {}).get("hp")
        hp = int(stored) if stored is not None else rules.max_hp
        skills = card.get("skills") or {}
        combat.combatants.append(Combatant(
            group_id=group_id,
            owner_qq=str(card.get("owner_qq", "")),
            name=str(card.get("name", "无名")),
            side="pc",
            initiative=0,
            hp=max(0, hp),
            max_hp=rules.max_hp,
            sp=rules.armor.sp or DEFAULT_SP,
            ref=int(stats.get("REF", 5)),
            dex=int(stats.get("DEX", 5)),
            dodge=int(skills.get("闪避", 0)),
            body=int(stats.get("BODY", 5)),
            tags=_card_tags(card),
        ))

    for card in easy:
        stats = card.get("stats") or {}
        hp = int(card.get("hp") or 30)
        skills = card.get("skills") or {}
        combat.combatants.append(Combatant(
            group_id=group_id,
            owner_qq=str(card.get("owner_qq", "")),
            name=str(card.get("name", "无名")),
            side="pc",
            initiative=0,
            hp=hp,
            max_hp=hp,
            sp=int(card.get("sp") or DEFAULT_SP),
            ref=int(stats.get("REF", 5)),
            dex=int(stats.get("DEX", 5)),
            dodge=int(skills.get("闪避", 0)),
            body=int(stats.get("BODY", 5)),
            tags=_card_tags(card),
        ))

    index = 0
    for group in enemies:
        if isinstance(group, dict):
            preset_name = str(group.get("name") or "")
            count = int(group.get("count") or 1)
            faction = str(group.get("faction") or "enemy")
            label = str(group.get("label") or "")
            hostile_to = tuple(str(x) for x in (group.get("hostile_to") or ()))
            script = dict(group.get("script") or {}) if isinstance(
                group.get("script"), dict) else {}
            actor = str(group.get("actor") or "")
            fate = str(group.get("fate") or "")
            withdraw_at = int(group.get("withdraw_at") or 0)
            gdata = group.get("data") if isinstance(group.get("data"), dict) else {}
            hp_override = group.get("hp")
            max_hp_override = int(group.get("max_hp") or 0)
            range_m = int(group.get("range_m") or 0)
            raw_spawn = group.get("spawn")
            spawn = (int(raw_spawn[0]), int(raw_spawn[1])) if (
                isinstance(raw_spawn, (list, tuple)) and len(raw_spawn) == 2) else None
        else:
            preset_name, count = str(group[0]), int(group[1])
            faction, label, hostile_to, script = "enemy", "", (), {}
            actor, fate, withdraw_at = "", "", 0
            gdata, hp_override, max_hp_override = {}, None, 0
            range_m = 0
            spawn = None
        if faction != "pc":
            combat.labels.setdefault(faction, label or faction)
            combat.factions.setdefault(faction, set()).update(hostile_to)
            for other in hostile_to:
                combat.factions.setdefault(other, set()).add(faction)
        preset = enemy_preset(preset_name)
        if preset is None:
            raise CombatError(
                f"没有这种敌人：{preset_name}（可选：{'、'.join(enemy_names())}）")
        for _ in range(max(1, count)):
            index += 1
            hp = preset.hp if hp_override is None else max(0, int(hp_override))
            combat.combatants.append(Combatant(
                group_id=group_id,
                owner_qq=f"enemy:{index}",
                name=f"{preset.name}{index}" if count > 1 else preset.name,
                side=faction,
                initiative=0,
                hp=hp,
                max_hp=max_hp_override or preset.hp,
                sp=int(gdata.get("sp", preset.sp)),
                damage=str(gdata.get("damage") or preset.damage),
                ref=int(gdata.get("ref", preset.ref)),
                dex=int(gdata.get("dex", preset.dex)),
                dodge=int(gdata.get("dodge", preset.dodge)),
                skill=int(gdata.get("skill", preset.skill)),
                body=int(gdata.get("body", preset.body)),
                script=dict(script),
                wound=preset.wound,
                tags=tuple(gdata.get("tags") or preset.tags),
                actor_id=(f"{actor}:{index}" if actor and count > 1 else actor),
                fate=fate,
                withdraw_at=withdraw_at,
                range_m=range_m,
                spawn=spawn,
            ))

    # 场景有地图就落位；没有就看 range_m（场景默认，敌人组可覆盖）
    scene_data = _scene_data(conn, group_id)
    enc_data = scene_data.get("encounter") if isinstance(scene_data.get("encounter"), dict) else {}
    scene_range = int(scene_data.get("range_m") or enc_data.get("range_m") or 0)
    if scene_range:
        for c in combat.combatants:
            if c.range_m <= 0:
                c.range_m = scene_range
    map_id = str(scene_data.get("map") or "")
    if map_id:
        game = _map_for_id(map_id)
        if game is not None:
            combat.map_id = map_id
            _place_combatants(combat, game)

    # 先攻 = 反应 + 1D10；同值重掷（书内页 168）
    for c in combat.combatants:
        c.initiative = roller.roll(10) + c.ref
    for _ in range(20):
        dup = _duplicates(combat.combatants)
        if not dup:
            break
        for c in dup:
            c.initiative = roller.roll(10) + c.ref

    _SLOTS.pop(group_id, None)
    combat.turn_index = 0
    _reset_slots(combat)
    _settle_head(conn, combat, roller)
    _insert(conn, combat)
    return combat


def _settle_head(conn, combat: "Combat", roller) -> None:
    """开局时队列头部可能是敌人：先把它们自动结算掉，直到轮到玩家。"""
    for _ in range(32):
        cur = combat.current
        if cur is None or not combat.active or cur.side == "pc":
            return
        _enemy_turn(conn, combat, cur, roller, [])
        _check_over(combat, [])
        if not combat.active:
            return
        order = combat.order
        if not order:
            return
        combat.turn_index += 1
        if combat.turn_index % len(order) == 0:
            combat.round += 1
            _reset_slots(combat)


def _duplicates(people: list[Combatant]) -> list[Combatant]:
    """返回所有参与并列的人（书：同值就再次掷骰，更高者在前）。"""
    counts: dict[int, int] = {}
    for c in people:
        counts[c.initiative] = counts.get(c.initiative, 0) + 1
    return [c for c in people if counts[c.initiative] > 1]


def _archive(conn, combat: Combat) -> None:
    """战斗结束前，把具名演员的结局写进 campaign_actors（杂兵不记）。"""
    if combat.campaign_id is None:
        return
    for c in combat.combatants:
        if c.side == "pc" or not c.actor_id:
            continue
        status = "dead" if c.dead else ("escaped" if c.escaped else "active")
        data = {"ref": c.ref, "dex": c.dex, "dodge": c.dodge, "skill": c.skill,
                "sp": c.sp, "damage": c.damage, "tags": list(c.tags),
                "max_hp": c.max_hp, "wound": c.wound}
        preset = c.name.rstrip("0123456789")
        conn.execute(
            "INSERT INTO campaign_actors (campaign_id, actor_id, name, kind, status,"
            " hp, max_hp, scene, side, preset, fate, withdraw_at, script, data,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, actor_id) DO UPDATE SET"
            " name=excluded.name, status=excluded.status, hp=excluded.hp,"
            " max_hp=excluded.max_hp, scene=excluded.scene, side=excluded.side,"
            " preset=excluded.preset, fate=excluded.fate,"
            " withdraw_at=excluded.withdraw_at, script=excluded.script,"
            " data=excluded.data, updated_at=excluded.updated_at",
            (combat.campaign_id, c.actor_id, c.name, "enemy", status, c.hp,
             c.max_hp, combat.scene, c.side, preset, c.fate, c.withdraw_at,
             json.dumps(c.script, ensure_ascii=False),
             json.dumps(data, ensure_ascii=False), now()),
        )


def actor_group(conn, campaign_id: int, actor_id: str, *,
                fresh: bool = False) -> dict | None:
    """把演员表里的一条记录变回 start() 能吃的敌人组。"""
    row = conn.execute(
        "SELECT * FROM campaign_actors WHERE campaign_id = ? AND actor_id = ?",
        (int(campaign_id), str(actor_id)),
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"] or "{}")
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    max_hp = int(row["max_hp"] or data.get("max_hp") or 1)
    hp = max_hp if fresh else int(row["hp"])
    try:
        script = json.loads(row["script"] or "{}")
    except (ValueError, TypeError):
        script = {}
    return {
        "name": str(row["preset"] or row["name"]),
        "count": 1,
        "faction": str(row["side"] or "enemy"),
        "label": str(row["name"]),
        "script": script if isinstance(script, dict) else {},
        "actor": str(row["actor_id"]),
        "fate": str(row["fate"] or ""),
        "withdraw_at": int(row["withdraw_at"] or 0),
        "hp": hp,
        "max_hp": max_hp,
        "data": data,
        "status": str(row["status"] or ""),
    }


def end(conn, group_id: str) -> None:
    fight = load(conn, group_id)
    if fight is not None:
        _archive(conn, fight)
    conn.execute("DELETE FROM combatants WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM combats WHERE group_id = ?", (group_id,))
    conn.commit()
    _SLOTS.pop(group_id, None)


def rest(conn, group_id: str) -> str:
    """战后休整：所有活着的 PC 回满 HP（规则书「接受医理并恢复状态」）。"""
    if active(conn, group_id) is not None:
        raise CombatError("战斗还没结束，先打完再休整。")
    healed: list[str] = []
    for card in roster.members(conn, group_id):
        rules = roster.to_character(card)
        state = dict(card.get("state") or {})
        hp = state.get("hp")
        if hp is not None and int(hp) <= 0:
            continue
        state["hp"] = rules.max_hp
        state["max_hp"] = rules.max_hp
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), now(), group_id,
             str(card.get("owner_qq", ""))),
        )
        healed.append(str(card.get("name") or "无名"))
    for card in easy_roster.members(conn, group_id):
        owner = str(card.get("owner_qq", ""))
        data = easy_roster.get(conn, group_id, owner) or {}
        hp = int(data.get("hp") or 0)
        if hp <= 0:
            continue
        max_hp = int(data.get("max_hp") or 0)
        if max_hp <= 0:
            tpl = easy.get(str(card.get("template") or "")) or {}
            max_hp = int(tpl.get("hp") or hp or 30)
        data["hp"] = max_hp
        data["max_hp"] = max_hp
        conn.execute(
            "UPDATE easy_cards SET data = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(data, ensure_ascii=False), now(), group_id, owner),
        )
        healed.append(str(data.get("name") or card.get("name") or "无名"))
    conn.commit()
    if not healed:
        return "🩹 没有需要休整的人（或者大家都倒下了）。"
    return "🩹 休整完毕：" + "、".join(healed) + " 的 HP 已回满。"


# ── 文本 ──────────────────────────────────────────────────────


def status_text(conn, group_id: str) -> str:
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return "现在没有在打。用「开打 打手 2」开始一场。"
    order = combat.order
    if not order:
        return "场上没有活人了。用「收队」清场。"
    cur = combat.current
    lines = [f"⚔ 第 {combat.round} 轮 · 先攻队列"]
    summary = modifier_summary(combat)
    if summary:
        lines.append(summary)
    for i, c in enumerate(order):
        mark = "▶" if cur is not None and c.owner_qq == cur.owner_qq else "·"
        who = faction_label(combat, c.side)
        lines.append(f"{mark} {i + 1}. {c.line()}〔{who}〕")
    if cur is not None:
        if cur.side == "pc":
            slot = _slots(combat).get(cur.owner_qq, Slot())
            acts = ("移动✅" if slot.move else "移动") + "/" + (
                "动作✅" if slot.action else "动作")
            lines.append(f"现在轮到：{cur.name}（{acts}）")
        else:
            lines.append(f"现在轮到：{cur.name}"
                         f"（{faction_label(combat, cur.side)}回合自动结算）")
    return "\n".join(lines)


def _move_cells(card) -> int:
    """一次移动动作的格数：MOVE×2 米，1 格 2 米 → 等于 MOVE（书内页 168）。"""
    stats = (card or {}).get("stats") or {}
    try:
        return max(1, int(stats.get("MOVE") or stats.get("移动") or 6))
    except (TypeError, ValueError):
        return 6


def _move_budget(me: "Combatant", card) -> int:
    """移动预算 = MOVE 格数 − 严重伤势的移速惩罚，最低剩 1 格。

    规则书：气胸移速 −2（full.txt:15770）、腿骨折 −4（:15840）、断腿 −6
    （:15876），三处都写明「最低剩余 1」。
    """
    return max(1, _move_cells(card) - me.move_penalty)


def _scene_cards(conn, group_id: str):
    """(owner_qq, card, is_easy)；同一人两张卡时以简易卡为准。"""
    out = []
    seen = set()
    for card in easy_roster.members(conn, group_id):
        owner = str(card.get("owner_qq") or "")
        seen.add(owner)
        out.append((owner, card, True))
    for card in roster.members(conn, group_id):
        owner = str(card.get("owner_qq") or "")
        if owner not in seen:
            out.append((owner, card, False))
    return out


def _saved_pos(card, map_id: str):
    state = card.get("state") or {}
    pos = state.get("pos") if isinstance(state, dict) else None
    if not isinstance(pos, dict) or str(pos.get("map") or "") != map_id:
        return None
    try:
        goal = pos.get("goal")
        return int(pos["x"]), int(pos["y"]), goal
    except (KeyError, TypeError, ValueError):
        return None


def _noncombat_positions(conn, group_id: str, game, map_id: str) -> dict[str, tuple[int, int]]:
    """非战斗时的 PC 坐标：卡里存过就用，否则从 spawns["pc"] 排最近的空格。"""
    cards = _scene_cards(conn, group_id)
    occupied: set[tuple[int, int]] = set()
    positions: dict[str, tuple[int, int]] = {}
    for owner, card, _ in cards:
        saved = _saved_pos(card, map_id)
        if saved is None:
            continue
        pos = (saved[0], saved[1])
        if game.walkable(pos) and pos not in occupied:
            positions[owner] = pos
            occupied.add(pos)
    spawn = tuple(game.spawns.get("pc") or (0, 0))
    spots = _spots_from(game, spawn)
    for owner, card, _ in cards:
        if owner in positions:
            continue
        pick = next((p for p in spots if p not in occupied), None)
        if pick is None:
            continue
        positions[owner] = pick
        occupied.add(pick)
    return positions


def _save_pos(conn, group_id: str, owner: str, card, is_easy: bool,
              map_id: str, pos: tuple[int, int],
              goal: tuple[int, int] | None) -> None:
    state = dict(card.get("state") or {})
    state["pos"] = {"map": map_id, "x": pos[0], "y": pos[1]}
    if goal is not None:
        state["pos"]["goal"] = [goal[0], goal[1]]
    if is_easy:
        easy_roster.set_state(conn, group_id, owner, state)
    else:
        roster.set_state(conn, group_id, owner, state)


def scene_move(conn, group_id: str, user_id: str, text: str) -> str | None:
    """非战斗移动：场景有地图时按 MOVE×2 米走；不是明确移动就返回 None。"""
    body = (text or "").strip()
    if not body:
        return None
    fight = load(conn, group_id)
    if fight is not None and fight.active:
        return None
    scene = _scene_data(conn, group_id)
    map_id = str(scene.get("map") or "")
    game = _map_for_id(map_id)
    if game is None:
        return None
    cards = _scene_cards(conn, group_id)
    mine = next(((card, easy) for owner, card, easy in cards
                 if owner == user_id), None)
    if mine is None:
        return None
    card, is_easy = mine
    positions = _noncombat_positions(conn, group_id, game, map_id)
    start = positions.get(user_id)
    if start is None:
        return None
    occupied = {p for owner, p in positions.items() if owner != user_id}

    goal: tuple[int, int] | None = None
    if "继续走" in body:
        saved = _saved_pos(card, map_id)
        if saved is not None and saved[2]:
            try:
                goal = (int(saved[2][0]), int(saved[2][1]))
            except (TypeError, ValueError, IndexError):
                goal = None
    if goal is None:
        found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
        if found:
            goal = (int(found.group(1)), int(found.group(2)))
        else:
            cover = next((c for c in game.covers
                          if c.name in body or c.id in body), None)
            if cover is not None:
                near = [p for p in game.neighbors((cover.x, cover.y))
                        if game.walkable(p) and p not in occupied]
                goal = near[0] if near else None
    if goal is None:
        return None
    if not game.walkable(goal):
        return f"({goal[0]},{goal[1]}) 走不过去（{game.terrain_name(goal)}）。"

    budget = _move_cells(card)
    route, used, arrived = game.move(start, goal, budget)
    if not route:
        return "找不到通往那里的路。"
    pos = route[-1]
    _save_pos(conn, group_id, user_id, card, is_easy, map_id, pos,
              None if arrived else goal)
    name = str(card.get("name") or "你")
    desc = game.describe(pos)
    if arrived:
        return (f"👟 {name} 走到 ({pos[0]},{pos[1]})，走了 {used} 格"
                f"（MOVE {budget} → 一次最多 {budget} 格 / {budget * 2} 米）。{desc}")
    return (f"👟 {name} 走到半路 ({pos[0]},{pos[1]})，走了 {used} 格；"
            f"一次移动最多 {budget} 格（MOVE×2 米）。说「继续走」接着走。")


def map_text(conn, group_id: str, user_qq: str = "") -> str:
    """当前场景/战斗的地图；没有地图就直说。"""
    combat = load(conn, group_id)
    if combat is not None and not combat.active:
        combat = None
    game = None
    scene = None
    if combat is not None and combat.map_id:
        game = _map_for(combat)
    if game is None:
        scene = _scene_data(conn, group_id)
        game = _map_for_id(str(scene.get("map") or ""))
    if game is None:
        return "这个场景没有地图，位置由 GM 叙述。"
    tokens: dict[tuple[int, int], str] = {}
    if combat is not None:
        for c in combat.combatants:
            if c.x < 0 or c.y < 0 or c.escaped:
                continue
            if c.side != "pc":
                tokens[(c.x, c.y)] = "E"
            elif user_qq and c.owner_qq == user_qq:
                tokens[(c.x, c.y)] = "@"
            else:
                tokens[(c.x, c.y)] = "A"
    else:
        map_id = str(scene.get("map") or "")
        for owner, pos in _noncombat_positions(conn, group_id, game, map_id).items():
            tokens[pos] = "@" if user_qq and owner == user_qq else "A"
    text = game.render(tokens)
    if combat is not None:
        text += "\n移动：说「移动到 5,6」或「躲到<掩体名>后面」；打掩体：「攻击 <掩体名>」。"
    else:
        text += "\n非战斗移动：说「移动到 5,6」或「躲到<掩体名>后面」；一次走 MOVE×2 米。"
    return text



def _roll_damage(roller, notation: str) -> int:
    from cyberpunk.damage import roll_notation

    return roll_notation(roller, (notation or "2D6").upper())


def _best_weapon_skill(card) -> str:
    if not card:
        return FALLBACK_SKILL
    skills = card.get("skills") or {}
    owned = [s for s in WEAPON_DAMAGE if s in skills]
    if not owned:
        return FALLBACK_SKILL
    return max(owned, key=lambda s: int(skills.get(s, 0)))


def _card_tags(card) -> tuple[str, ...]:
    """把卡上的装备/改造/特长摊成标签，给修正的 negate_if 做语义匹配。"""
    if not card:
        return ()
    parts: list[str] = [str(card.get("name") or ""), str(card.get("template") or "")]
    for key in ("gear", "cyber", "weapons", "armor", "perks"):
        val = card.get(key) or []
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, dict):
            parts.append(str(val.get("name") or ""))
        else:
            for item in val:
                parts.append(str(item.get("name") or "") if isinstance(item, dict)
                             else str(item))
    return tuple(p for p in parts if p)


# ── 环境修正（战斗扩展设计 §3）────────────────────────────────


def _rounds_hit(spec, rnd: int) -> bool:
    """rounds：缺省/all = 每轮；int、[起, 止]、"1-3"、"1,2,3"。"""
    if spec is None or spec == "" or spec == "all":
        return True
    if isinstance(spec, bool):
        return bool(spec)
    if isinstance(spec, (int, float)):
        return int(spec) == rnd
    if isinstance(spec, str):
        text = spec.strip().replace("～", "-").replace("–", "-").replace("—", "-")
        if "," in text or "，" in text:
            nums = [int(x) for x in text.replace("，", ",").split(",")
                    if x.strip().lstrip("-").isdigit()]
            return rnd in nums
        if "-" in text:
            left, _, right = text.partition("-")
            if left.strip().isdigit() and right.strip().isdigit():
                return int(left) <= rnd <= int(right)
            return True
        return text.isdigit() and int(text) == rnd
    if isinstance(spec, (list, tuple)):
        nums = [int(x) for x in spec if str(x).strip().lstrip("-").isdigit()]
        if len(nums) == 2 and len(spec) == 2:
            return min(nums) <= rnd <= max(nums)
        return rnd in nums
    return True


def _applies(entry: dict, actor: Combatant | None) -> bool:
    scope = entry.get("applies_to", "all")
    if actor is None or scope in (None, "", "all", "*"):
        return True
    if isinstance(scope, str):
        return scope == actor.side
    return actor.side in {str(x) for x in scope}


def _scope_all(entry: dict) -> bool:
    return entry.get("applies_to", "all") in (None, "", "all", "*")


#: 同一件义体的不同写法：数据里叫「声音抑制器」（Sound Damper），剧本写「水平阻尼降噪」
NEGATE_ALIASES: dict[str, tuple[str, ...]] = {
    "水平阻尼降噪": ("水平阻尼降噪", "水平阻尼", "阻尼降噪", "声音抑制器"),
}


def _negated(entry: dict, actor: Combatant | None) -> bool:
    if actor is None:
        return False
    tokens = entry.get("negate_if") or []
    if isinstance(tokens, str):
        tokens = [tokens]
    tags = " ".join(actor.tags)
    for token in tokens:
        name = str(token).strip()
        if not name:
            continue
        if any(alias in tags for alias in NEGATE_ALIASES.get(name, (name,))):
            return True
    return False


def modifiers_for(combat: Combat, actor: Combatant | None = None,
                  round: int | None = None) -> list[dict]:
    """当轮对 actor 生效的修正（已过回合区间、阵营范围与免疫判定）。"""
    rnd = combat.round if round is None else int(round)
    out: list[dict] = []
    for entry in combat.modifiers or []:
        if not isinstance(entry, dict):
            continue
        if not _rounds_hit(entry.get("rounds"), rnd):
            continue
        if not _applies(entry, actor):
            continue
        if _negated(entry, actor):
            continue
        out.append(entry)
    return out


def modifier_for(combat: Combat, actor: Combatant | None = None,
                 round: int | None = None) -> int:
    return sum(int(e.get("value") or 0) for e in modifiers_for(combat, actor, round))


def _fmt_mod(value: int) -> str:
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"−{abs(value)}"
    return "±0"


def _entry_label(entry: dict) -> str:
    name = str(entry.get("label") or entry.get("name") or entry.get("id") or "修正")
    return f"{name} {_fmt_mod(int(entry.get('value') or 0))}"


def modifier_text(combat: Combat, actor: Combatant | None = None,
                  round: int | None = None) -> str:
    entries = modifiers_for(combat, actor, round)
    if not entries:
        return ""
    return "（" + "、".join(_entry_label(e) for e in entries) + "）"


def modifier_summary(combat: Combat) -> str:
    """状态面板用：只列对所有人生效的修正。"""
    entries = [e for e in modifiers_for(combat) if _scope_all(e)]
    if not entries:
        return ""
    return "环境修正：" + "、".join(_entry_label(e) for e in entries)


def _find_target(combat: Combat, hint: str,
                 actor: Combatant | None = None) -> Combatant:
    pool = foes(combat, actor) if actor is not None else combat.enemies
    if not pool:
        raise CombatError("场上没有你能打的目标了。")
    hint = (hint or "").strip()
    if hint:
        for c in pool:
            if hint in c.name:
                return c
    return pool[0]


def _sync_pc_hp(conn, combat: Combat, c: Combatant) -> None:
    if c.side != "pc":
        return
    row = conn.execute(
        "SELECT state FROM characters WHERE group_id = ? AND owner_qq = ?",
        (combat.group_id, c.owner_qq),
    ).fetchone()
    if row is not None:
        state = json.loads(row["state"] or "{}")
        state["hp"] = c.hp
        state.setdefault("max_hp", c.max_hp)
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), now(), combat.group_id, c.owner_qq),
        )
    else:
        row = conn.execute(
            "SELECT data FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
            (combat.group_id, c.owner_qq),
        ).fetchone()
        if row is not None:
            data = json.loads(row["data"] or "{}")
            data["max_hp"] = int(data.get("max_hp") or c.max_hp)
            data["hp"] = c.hp
            conn.execute(
                "UPDATE easy_cards SET data = ?, updated_at = ?"
                " WHERE group_id = ? AND owner_qq = ?",
                (json.dumps(data, ensure_ascii=False), now(), combat.group_id,
                 c.owner_qq),
            )
    conn.commit()


# ── 回合推进 ──────────────────────────────────────────────────


def _death_check(combat: Combat, c: Combatant, roller, lines: list[str]) -> None:
    """致命伤角色轮到自己时先判死（full.txt:15943-15949）。

    - 掷 D10 == 10：自动失败，即死。
    - 投出 < 体格 - 已累积死亡豁免惩罚：撑住，恢复 1 HP 并失去意识，清零惩罚。
    - 否则：判死。
    死亡豁免只在轮到自己时投；敌人不留致命伤状态（直接 dead）。
    """
    if not c.mortal:
        return
    roll = roller.roll(10)
    lines.append(f"  ☠ {c.name} 每回合开始要过死亡豁免（D10 < {c.body}"
                 f"{'' if c.death_save_penalty==0 else ' -'+str(c.death_save_penalty)}）…掷出 {roll}。")
    if roll == 10 or roll >= c.body - c.death_save_penalty:
        c.dead = True
        c.mortal = False
        lines.append(f"  {c.name} 没撑住，死了。")
    else:
        c.mortal = False
        c.death_save_penalty = 0
        c.hp = 1
        lines.append(f"  {c.name} 硬撑了过来：恢复 1 HP，但失去意识，这一回合动不了。")
        # 失去意识 -> 本轮消耗掉动作，避免立刻又被放回行动
        slot = _slots(combat).get(c.owner_qq)
        if slot is not None:
            slot.action = True


def _check_over(combat: Combat, lines: list[str]) -> None:
    if not combat.enemies:
        combat.active = False
        if any(c.escaped for c in combat.combatants):
            lines.append("⚑ 敌人跑光了，战斗结束。用「收队」清场。")
        else:
            lines.append("⚑ 敌人全倒下了，战斗结束。用「收队」清场。")
    elif not combat.pcs:
        combat.active = False
        lines.append("☠ 队伍全倒下了，战斗结束。")


def _advance(conn, combat: Combat, roller, lines: list[str]) -> None:
    """指针后移；走到敌人就自动结算，直到轮到活着的玩家或战斗结束。"""
    for _ in range(32):
        order = combat.order
        if not order or not combat.active:
            return
        combat.turn_index += 1
        if combat.turn_index % len(order) == 0:
            combat.round += 1
            _reset_slots(combat)
        order = combat.order
        if not order:
            return
        cur = order[combat.turn_index % len(order)]
        if cur.side == "pc":
            # 致命伤角色轮到自己：先判死（成功会恢复 1 HP 并消耗掉本回合动作）
            if cur.mortal:
                _death_check(combat, cur, roller, lines)
                if not combat.active:
                    return
            # 队列里有人倒下会让取模错位：如果又轮到一个动作已用掉的 PC，
            # 说明这一圈已经转完，另起一轮，别把上一轮的动作扣到新回合。
            slot = _slots(combat)[cur.owner_qq]
            if slot.action:
                combat.round += 1
                _reset_slots(combat)
            lines.append(f"▶ 轮到 {cur.name}。")
            return
        _enemy_turn(conn, combat, cur, roller, lines)
        _check_over(combat, lines)
        if not combat.active:
            return


# ── 敌人行为脚本（战斗扩展设计 §4）────────────────────────────


def maybe_serious_injury(combat: Combat, c: Combatant, sixes: int, *,
                         head: bool, roller, lines: list[str],
                         force: bool = False) -> None:
    """伤害骰出了两个及以上 6 → 造成一次严重伤势（full.txt:15743-15748）。

    - 投 2D6 查表（瞄准头部用头部表，否则身体表），直到得到一个目标目前没有的伤势。
    - 严重伤势额外造成 5 点伤害，不削护甲、不受部位倍率，穿甲与否都生效。
    - 已有该伤势就再掷，避免重复。
    - 严重伤势自带的死亡豁免惩罚 +1 并入 ``death_save_penalty``。
    - ``force=True``：致命伤角色再被近战/远程攻击打中时，**必**获得一个严重伤势
      （full.txt:15741-15742），不管骰子有没有出俩 6。
    """
    if not force and sixes < SERIOUS_TRIGGER_SIXES:
        return
    from cyberpunk import crit
    table = crit.table_for(head)
    # 排除已有伤势，避免重复获得（full.txt:15746-15748）
    have = set(c.crits)
    roll = roller.roll(6) + roller.roll(6)
    guard = 0
    inj = table.get(roll)
    while (inj is None or inj.key in have) and guard < 24:
        roll = roller.roll(6) + roller.roll(6)
        inj = table.get(roll)
        guard += 1
    if inj is None:
        return
    c.crits = c.crits + (inj.key,)
    if inj.death_penalty:
        c.death_save_penalty += inj.death_penalty
    # 状态类伤势：写成「回合号」，因为 Slot.reset() 每轮清零布尔
    slot = _slots(combat)[c.owner_qq]
    if inj.no_action_next:
        slot.no_action_round = combat.round + 1
    if inj.no_move_next:
        slot.no_move_round = combat.round + 1
    # 5 点额外伤害：不削护甲、不受部位倍率
    extra = crit.SERIOUS_EXTRA_DAMAGE
    c.hp = max(0, c.hp - extra)
    lines.append(
        f"  💥 {c.name} 中了严重伤：{inj.zh}"
        f"（{inj.sk}）——额外 5 点伤害"
        + (f"，死亡豁免惩罚 +{inj.death_penalty}" if inj.death_penalty else "")
        + "。")
    lines.append(f"  {c.name}：{c.line()}")


def _apply_damage_hp(combat: Combat, c: Combatant, taken: int,
                     lines: list[str]) -> None:
    """扣血，并按命格决定「倒下 / 退场 / 打不死」。

    - 普通：HP 归零 → dead
    - withdraw_at：HP 掉到阈值以内 → escaped（不算赢，也不是尸体）
    - cannot_die：HP 归零也不死，改为退场，留着下次回来
    """
    c.hp = max(0, c.hp - max(0, int(taken)))
    if c.hp < 1:
        if c.fate == "cannot_die":
            c.hp = 1
            c.escaped = True
            lines.append(f"  {c.name} 没有倒下——他从包围里退了出去。")
        elif c.side == "pc":
            # 致命伤（full.txt:15730-15745）：HP≤0 只算致命伤，还没死。
            # 已致命再挨打 -> 死亡豁免更困难（惩罚 +1，严重伤势留给 A2）
            if c.mortal:
                c.death_save_penalty += 1
                lines.append(
                    f"  {c.name} 已经致命了，又挨了一下：死亡豁免惩罚 +1"
                    f"（当前 {c.death_save_penalty}）。")
            else:
                c.mortal = True
                lines.append(f"  {c.name} 受了致命伤：每回合开始要先过死亡豁免，不然就死了。")
        else:
            c.dead = True
    elif c.withdraw_at and c.hp <= c.withdraw_at:
        c.escaped = True
        lines.append(f"  {c.name} 伤到这个程度，抽身退出了战斗。")


def _kin(combat: Combat, enemy: Combatant, script: dict) -> list[Combatant]:
    """「其中一人」的范围：自己 + allies 里点名的阵营；没写就只算自己一派。

    含已倒下的同伴（any_critical 要能看见「有人被打死」），但不含已逃跑的。
    """
    sides = {str(x) for x in (script.get("allies") or [])} or {enemy.side}
    return [c for c in combat.combatants if not c.escaped and c.side in sides]


def _serious(c: Combatant) -> bool:
    if not c.alive or c.hp < 1:
        return False
    return c.hp <= (c.wound or max(1, c.max_hp // 2))


def _critical(c: Combatant) -> bool:
    return c.dead or c.hp < 1


def _should_flee(combat: Combat, enemy: Combatant, script: dict) -> bool:
    cond = script.get("flee_if") or {}
    if not isinstance(cond, dict) or not cond:
        return False
    if "round_gt" in cond and combat.round > int(cond["round_gt"]):
        return True
    if "round_gte" in cond and combat.round >= int(cond["round_gte"]):
        return True
    kin = _kin(combat, enemy, script)
    if cond.get("any_serious") and any(_serious(c) for c in kin):
        return True
    if cond.get("any_critical") and any(_critical(c) for c in kin):
        return True
    return False


def _flee(conn, combat: Combat, enemy: Combatant, script: dict, roller,
          lines: list[str]) -> bool:
    """完整回合 + 冲刺。跑不掉返回 False（交给 on_flee_fail）。"""
    lines.append(f"🏃 {enemy.name} 转身就跑（完整回合 + 冲刺）。")
    if script.get("flee_blocked"):
        lines.append("  但出口被堵死，冲不出去。")
        return False
    enemy.escaped = True
    lines.append(f"  {enemy.name} 脱离战斗。")
    return True


def _self_destruct(conn, combat: Combat, enemy: Combatant, script: dict, roller,
                   lines: list[str]) -> bool:
    """on_flee_fail：跑不掉就自爆。没有格子/地图，按同场全体结算。"""
    spec = (script.get("on_flee_fail") or {}).get("self_destruct")
    if not spec:
        return False
    if isinstance(spec, str):
        spec = {"weapon": spec, "damage": "6D6"}
    weapon = str(spec.get("weapon") or "手榴弹")
    notation = str(spec.get("damage") or "6D6").upper()
    note = str(spec.get("note") or "")
    damage = _roll_damage(roller, notation)
    tip = f"（{note}）" if note else ""
    lines.append(f"💥 {enemy.name} 拉响{weapon}{tip}：{notation} = {damage}。")
    enemy.hp = 0
    enemy.dead = True
    game = _map_for(combat)
    if game is not None and enemy.x >= 0 and enemy.y >= 0:
        victims = [c for c in combat.order
                   if c is not enemy and c.x >= 0 and c.y >= 0
                   and max(abs(c.x - enemy.x), abs(c.y - enemy.y)) <= 2]
        lines.append("  （手榴弹按中心 5×5 格结算，站在爆心 2 格内才会被炸到。）")
    else:
        victims = [x for x in combat.order if x is not enemy]
        lines.append("  （没有地图，按同场全体结算爆炸伤害。）")
    for c in victims:
        armor = c.effective_sp
        taken = max(0, damage - armor)
        if taken:
            c.sp_wear += 1
        _apply_damage_hp(combat, c, taken, lines)
        _sync_pc_hp(conn, combat, c)
        lines.append(f"  {c.name} 被炸：护甲挡 {min(damage, armor)}，掉 {taken} 血"
                     f"（{max(0, c.hp)}/{c.max_hp} HP）。")
    return True


def _pick_target(combat: Combat, enemy: Combatant, targets: list[Combatant],
                 script: dict, roller) -> Combatant:
    """targets：在 rounds 内按「同脚本敌人」的序号平摊阵营，之后回到默认。"""
    spec = script.get("targets") or {}
    if isinstance(spec, dict) and spec and _rounds_hit(
            spec.get("rounds"), combat.round):
        split = spec.get("split") or []
        if isinstance(split, str):
            split = [split]
        split = [str(x) for x in split]
        usable = [s for s in split if any(t.side == s for t in targets)]
        if usable:
            peers = [c for c in combat.order
                     if c.side != "pc" and c.script.get("targets") == spec]
            index = peers.index(enemy) if enemy in peers else 0
            side = usable[index % len(usable)]
            same = [t for t in targets if t.side == side]
            if same:
                return same[0] if len(same) == 1 else same[roller.roll(len(same)) - 1]
    game = _map_for(combat)
    if game is not None and enemy.x >= 0 and enemy.y >= 0:
        near = [t for t in targets if t.x >= 0 and t.y >= 0]
        if near:
            return min(near, key=lambda t: game.meters((enemy.x, enemy.y), (t.x, t.y)))
    return targets[0] if len(targets) == 1 else targets[roller.roll(len(targets)) - 1]


def _enemy_turn(conn, combat: Combat, enemy: Combatant, roller,
                lines: list[str]) -> None:
    script = enemy.script or {}
    join = int(script.get("join_round") or 1)
    if combat.round < join:
        lines.append(f"· {enemy.name} 还在场外（第 {join} 轮才加入）。")
        return
    if _should_flee(combat, enemy, script):
        if _flee(conn, combat, enemy, script, roller, lines):
            return
        if _self_destruct(conn, combat, enemy, script, roller, lines):
            return
        lines.append(f"· {enemy.name} 没跑掉，只能继续硬撑。")
    targets = foes(combat, enemy)
    if not targets:
        lines.append(f"· {enemy.name} 没有可打的目标。")
        return
    target = _pick_target(combat, enemy, targets, script, roller)
    game = _map_for(combat)
    dv: int | None = None
    distance = ""
    if game is not None and enemy.x >= 0 and target.x >= 0:
        here, there = (enemy.x, enemy.y), (target.x, target.y)
        weapon = _weapon_of(enemy) or DEFAULT_WEAPON
        shot = game.dv(here, there, weapon)
        blocked = game.blockers(here, there)
        if blocked or shot is None:
            if "炮塔" in enemy.name:
                lines.append(f"· {enemy.name} 固定在原地，打不到 {target.name}。")
                return
            occupied = {(c.x, c.y) for c in combat.order if c is not enemy and c.x >= 0}
            spots = [p for p in game.neighbors(there)
                     if game.walkable(p) and p not in occupied]
            route = []
            for spot in spots:
                candidate, _used, _arrived = game.move(here, spot, 6)
                if candidate:
                    route = candidate
                    break
            if route:
                enemy.x, enemy.y = route[-1]
                why = f"视线被{blocked[0]}挡住" if blocked else "距离太远"
                lines.append(f"· {enemy.name} 移动接近 {target.name}（{why}，走了 {len(route) - 1} 格）。")
            else:
                lines.append(f"· {enemy.name} 找不到接近 {target.name} 的路。")
            return
        dv = shot
        distance = f"，{game.meters(here, there)} 米"
    else:
        # 没有地图数据也得有距离：场景 range_m / DM 估的米数，谁先给了就用谁的
        meters = enemy.range_m or target.range_m
        weapon = _weapon_of(enemy) or DEFAULT_WEAPON
        if meters > 0:
            dv = gridmap.dv_for_meters(weapon, meters)
            distance = f"，{meters} 米（估）"
            if dv is None:
                lines.append(f"· {enemy.name} 手里的{weapon}够不到 {target.name}"
                             f"（{meters} 米），先往前压。")
                return
        else:
            lines.append(f"· {enemy.name} 朝 {target.name} 压了过来"
                         "（距离还没定：DM 说一句米数，或者玩家说「距离 20 米」）。")
            return
    mod = modifier_for(combat, enemy)
    total = roller.roll(10) + enemy.ref + enemy.skill + mod + enemy.ranged_penalty
    lines.append(f"· {enemy.name} 行动：{total} vs DV {dv}{distance}"
                 f"{modifier_text(combat, enemy)}")
    if total < dv:
        lines.append(f"  没打中 {target.name}。")
        return
    damage, sixes = _roll_dice_full(roller, enemy.damage or "2D6")
    taken = max(0, damage - target.effective_sp)
    was_mortal = target.mortal and target.hp >= 1
    if taken:
        target.sp_wear += 1
    _apply_damage_hp(combat, target, taken, lines)
    lines.append(f"  命中 {target.name}：伤害 {damage}，护甲挡 {damage - taken}，"
                 f"掉 {taken} 血。")
    if sixes >= SERIOUS_TRIGGER_SIXES or (was_mortal and taken):
        maybe_serious_injury(combat, target, sixes, head=False,
                             roller=roller, lines=lines,
                             force=(was_mortal and taken))
    _sync_pc_hp(conn, combat, target)


# ── 攻击 ──────────────────────────────────────────────────────


def attack(conn, group_id: str, actor_qq: str, target_hint: str = "", *,
           skill: str | None = None, aim: str | None = None,
           roller: dice.Roller | None = None) -> str:
    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")

    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")

    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")

    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    # 打掩体：有地图、点了掩体名字、又没点具体敌人。
    game = _map_for(combat)
    if game is not None and target_hint:
        cover = next(
            (c for c in game.covers
             if _cover_hp(combat, c) > 0 and (c.name in target_hint or c.id in target_hint)),
            None,
        )
        enemy_hit = any(c.name in target_hint for c in foes(combat, me))
        if cover is not None and not enemy_hit:
            lines = [f"🧱 {me.name} 打{cover.name}（{game.describe((cover.x, cover.y))}）。"]
            if me.x < 0 or me.y < 0:
                lines.append("你还没站到地图上。")
                return "\n".join(lines)
            weapon = _weapon_of(me) or DEFAULT_WEAPON
            dv = game.dv((me.x, me.y), (cover.x, cover.y), weapon)
            if dv is None:
                return (f"超出{weapon}的射程：离{cover.name}有 "
                        f"{game.meters((me.x, me.y), (cover.x, cover.y))} 米，打不到。")
            total = roller.roll(10) + me.ref + me.wound_penalty + modifier_for(combat, me)
            lines.append(f"判定：{total} vs DV {dv}{modifier_text(combat, me)}")
            slot.action = True
            if total < dv:
                lines.append("没打中，子弹嵌进了旁边的墙。")
            else:
                dmg = _roll_damage(roller, WEAPON_DAMAGE.get("手枪", "2D6"))
                hp = max(0, _cover_hp(combat, cover) - dmg)
                combat.covers[cover.id] = hp
                lines.append(f"掩体掉了 {dmg} 点耐久，还剩 {hp}。"
                             + ("掩体被打烂了。" if hp <= 0 else ""))
            _sync_pc_hp(conn, combat, me)
            _advance(conn, combat, roller, lines)
            _save(conn, combat)
            return "\n".join(lines)

    target = _find_target(combat, target_hint, me)
    provoke(combat, me.side, target.side)
    card = roster.get(conn, group_id, actor_qq)
    skill_name = skill or _best_weapon_skill(card)
    level = int((card.get("skills") or {}).get(skill_name, 0)) if card else 0
    melee = skill_name in MELEE_SKILLS
    aim_part = AIM_PARTS.get((aim or "").strip()) if aim else None
    penalty = AIM_PENALTY if aim_part else 0

    lines: list[str] = []
    if aim_part:
        lines.append(f"🎯 {me.name} 瞄准{aim}：判定 −8、ROF 限 1。")

    dv, distance, problem = _range_dv(combat, me, target, melee=melee,
                                      weapon=_weapon_of(me, skill_name))
    if problem:
        if problem == RANGE_ASK:
            # 只是请 DM / 玩家先给个距离：不算动作，也不推进回合
            return problem
        lines.append(problem)
        slot.action = True
        _sync_pc_hp(conn, combat, me)
        _advance(conn, combat, roller, lines)
        _save(conn, combat)
        return "\n".join(lines)

    mod_note = modifier_text(combat, me)
    total = roller.roll(10) + (me.dex if melee else me.ref) + level + penalty
    # 严重伤势：近战/远程各有独立惩罚（肌肉撕裂 melee、失去眼睛 ranged），
    # 叠加在「所有动作」的 wound_penalty 之上（full.txt:15785-15930）
    total += me.wound_penalty + modifier_for(combat, me)
    total += me.melee_penalty if melee else me.ranged_penalty

    if melee:
        # 断腿：无法闪避攻击（full.txt:15876）——防守方不再掷闪避骰，近战直接命中
        if target.no_dodge:
            defend = 0
            hit = True
            lines.append(f"  {target.name} 无法闪避（严重伤势：断腿），这一下躲不开。")
        else:
            defend = roller.roll(10) + target.dex + target.dodge
            hit = total > defend
        lines.append(f"{me.name} 用{skill_name}打 {target.name}：{total} vs 闪避 {defend}"
                     f"{mod_note}")
    else:
        hit = total >= dv
        lines.append(f"{me.name} 用{skill_name}打 {target.name}：{total} vs DV {dv}"
                     f"{distance}{mod_note}")


    slot.action = True

    if not hit:
        lines.append("没打中。")
        _sync_pc_hp(conn, combat, me)
        _advance(conn, combat, roller, lines)
        _save(conn, combat)
        return "\n".join(lines)

    damage, sixes = _roll_dice_full(
        roller, WEAPON_DAMAGE.get(skill_name, "2D6"))
    sp = (target.effective_sp + 1) // 2 if melee else target.effective_sp
    taken = max(0, damage - sp)
    was_mortal = target.mortal and target.hp >= 1
    if taken:
        target.sp_wear += 1
    note = ""
    if aim_part == "head" and taken:
        # 默认头部瞄准 ×2；目标已有「颅骨骨裂」则倍数改成 3（full.txt:15904）
        mult = target.head_mult or 2
        taken *= mult
        note = f"打中头部：穿透护甲后伤害翻倍（×{mult}）。" if mult == 2 else \
            f"打中头部：颅骨骨裂，伤害×{mult}。"
    if aim_part == "leg" and taken:
        note = (note + " " if note else "") + "腿被打穿：断腿（严重伤势）。"
    if aim_part == "hand" and taken:
        note = (note + " " if note else "") + "手中的东西被打落。"

    _apply_damage_hp(combat, target, taken, lines)
    lines.append(f"命中：伤害 {damage}，护甲挡 {min(damage, sp)}，打掉 {taken} 血。")
    if note:
        lines.append(note)
    # 严重伤势：伤害骰出 ≥2 个 6，毫无例外（穿甲与否都触发）
    # 致命伤角色再挨打：不看出俩 6，强制受伤势（full.txt:15741-15742）
    if sixes >= SERIOUS_TRIGGER_SIXES or (was_mortal and taken):
        maybe_serious_injury(combat, target, sixes,
                             head=(aim_part == "head"), roller=roller, lines=lines,
                             force=(was_mortal and taken))
    lines.append(f"{target.name}：{target.line()}")

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


_HACK_WORDS = ("黑入", "接管", "切断电缆", "断开电缆", "黑炮塔", "关掉炮塔")


def _scene_encounter(conn, group_id: str) -> dict:
    """当前场景的 encounter 段（没有就返回 {}）。"""
    row = conn.execute(
        "SELECT c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return {}
    scene = data.get("scenes", {}).get(str(row["scene"] or "")) or {}
    enc = scene.get("encounter")
    return enc if isinstance(enc, dict) else {}


def hack(conn, group_id: str, actor_qq: str, text: str, *,
         roller: dice.Roller | None = None) -> str | None:
    """场景 encounter.hack：花一个动作黑入/切断设备。不是黑入指令就返回 None。"""
    enc = _scene_encounter(conn, group_id)
    spec = enc.get("hack") if isinstance(enc, dict) else None
    if not isinstance(spec, dict):
        return None
    words = tuple(str(w) for w in (spec.get("keywords") or _HACK_WORDS))
    if not any(w and w in (text or "") for w in words):
        return None

    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    skill_name = str(spec.get("skill") or "电子/安全技术")
    dv = int(spec.get("dv") or 13)
    count = max(1, int(spec.get("count") or 1))
    target_word = str(spec.get("target") or "").strip()
    card = roster.get(conn, group_id, actor_qq) or {}
    easy_card = easy_roster.get(conn, group_id, actor_qq) or {}
    stats = dict(easy_card.get("stats") or card.get("stats") or {})
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    total = (roller.roll(10) + int(stats.get("TECH") or 5)
             + int(skills.get(skill_name) or 0)
             + me.wound_penalty + modifier_for(combat, me))
    slot.action = True
    lines = [f"🔌 {me.name} 黑入{(' ' + target_word) if target_word else ''}："
             f"{total} vs DV {dv}（{skill_name}）{modifier_text(combat, me)}"]
    if total < dv:
        lines.append("没接进去，端口把你这只手弹了出来。")
    else:
        targets = [c for c in combat.enemies
                   if target_word in c.name]
        targets.sort(key=lambda c: -c.initiative)
        stopped = 0
        for tgt in targets[:count]:
            tgt.hp = 0
            tgt.dead = True
            stopped += 1
            lines.append(f"链路被切断：{tgt.name} 熄火停机。")
        if not stopped:
            lines.append("端口是空的，没有能接管的设备。")
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 网络战：接入点 / 控制节点 / 黑冰（阶段 3）──────────────────

_NET_ACCESS_WORDS = ("接入", "接线", "插上", "找接入点", "连上", "接进网络")


def _net_validate(combat: Combat, actor_qq: str) -> Combatant:
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    if _slots(combat)[me.owner_qq].action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")
    return me


def _net_total(conn, group_id: str, combat: Combat, me: Combatant,
               skill_name: str, roller) -> int:
    card = roster.get(conn, group_id, me.owner_qq) or {}
    easy_card = easy_roster.get(conn, group_id, me.owner_qq) or {}
    stats = dict(easy_card.get("stats") or card.get("stats") or {})
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    return (roller.roll(10) + int(stats.get("TECH") or 5)
            + int(skills.get(skill_name) or 0)
            + me.wound_penalty + modifier_for(combat, me))


def _net_defense(conn, group_id: str, me: Combatant,
                 skill_name: str, roller) -> int:
    """黑冰攻击时的防守方：用交互界面代理（本引擎里是电子/安全技术等级）。"""
    card = roster.get(conn, group_id, me.owner_qq) or {}
    easy_card = easy_roster.get(conn, group_id, me.owner_qq) or {}
    skills = dict(easy_card.get("skills") or card.get("skills") or {})
    return roller.roll(10) + int(skills.get(skill_name) or 0)


def _net_effect(combat: Combat, effect: dict, lines: list[str]) -> None:
    kind = str((effect or {}).get("type") or "")
    if kind == "stop_turrets":
        target = str(effect.get("target") or "")
        count = max(1, int(effect.get("count") or 1))
        targets = [c for c in combat.enemies if target and target in c.name]
        targets.sort(key=lambda c: -c.initiative)
        if not targets:
            lines.append("节点是通的，但场上没有能被它停下的设备。")
        for tgt in targets[:count]:
            tgt.hp = 0
            tgt.dead = True
            lines.append(f"链路被切断：{tgt.name} 熄火停机。")
    elif kind in ("open_door", "flag"):
        flag = str(effect.get("flag") or ("door_open" if kind == "open_door" else "flag"))
        combat.net.setdefault("flags", {})[flag] = True
        lines.append(str(effect.get("text") or
                         ("门锁弹开，通往里面的路开了。" if kind == "open_door"
                          else "你把标记写进了网络。")))
    else:
        lines.append("节点响应了，但你看不出它能控制什么。")


def net_action(conn, group_id: str, actor_qq: str, text: str, *,
               roller: dice.Roller | None = None) -> str | None:
    """scene.encounter.net：先接入，再控制节点；不是网络动作返回 None。"""
    enc = _scene_encounter(conn, group_id)
    spec = enc.get("net") if isinstance(enc, dict) else None
    if not isinstance(spec, dict):
        return None
    body = (text or "").strip()
    if not body:
        return None

    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    me = _net_validate(combat, actor_qq)
    roller = roller or dice.RandomRoller()
    slot = _slots(combat)[me.owner_qq]
    access = spec.get("access") if isinstance(spec.get("access"), dict) else {}
    nodes = [n for n in (spec.get("nodes") or []) if isinstance(n, dict)]
    st = combat.net.setdefault(me.owner_qq, {"connected": False, "controlled": []})
    if not isinstance(st.get("controlled"), list):
        st["controlled"] = []

    acc_words = tuple(str(w) for w in (access.get("keywords") or _NET_ACCESS_WORDS))
    node_words = tuple(w for n in nodes for w in (n.get("keywords") or ()))
    if not st.get("connected"):
        wants_access = any(w and w in body for w in acc_words)
        wants_node = any(w and w in body for w in node_words) or "黑入" in body
        if not wants_access:
            if wants_node:
                return "你还没接入——先找接入点，说「接入」或「找接入点」。"
            return None
        skill_name = str(access.get("skill") or "电子/安全技术")
        dv = int(access.get("dv") or 8)
        total = _net_total(conn, group_id, combat, me, skill_name, roller)
        slot.action = True
        name = str(access.get("name") or "接入点")
        lines = [f"🔌 {me.name} 接入 {name}：{total} vs DV {dv}（{skill_name}）"
                 f"{modifier_text(combat, me)}"]
        if total >= dv:
            st["connected"] = True
            lines.append("接入成功，控制节点在视野里亮了起来。")
        else:
            lines.append("端口把你这只手弹了出来，这次没接进去。")
    else:
        if any(w and w in body for w in acc_words):
            return "已经接入了，直接说「黑入 <节点>」。"
        node = next((n for n in nodes
                     if str(n.get("name") or "") in body or
                     any(w and w in body for w in (n.get("keywords") or ()))), None)
        if node is None:
            return None
        nid = str(node.get("id") or node.get("name") or "node")
        if nid in st["controlled"]:
            return f"「{node.get('name') or nid}」已经在你的控制下了，看还有没有别的节点。"
        skill_name = str(node.get("skill") or "电子/安全技术")
        dv = int(node.get("dv") or 8)
        total = _net_total(conn, group_id, combat, me, skill_name, roller)
        slot.action = True
        lines = [f"💻 {me.name} 黑入 {node.get('name') or nid}：{total} vs DV {dv}"
                 f"（{skill_name}）{modifier_text(combat, me)}"]
        if total >= dv:
            st["controlled"].append(nid)
            _net_effect(combat, node.get("effect") or {}, lines)
        else:
            ice = node.get("ice") if isinstance(node.get("ice"), dict) else {}
            if ice:
                ice_name = str(ice.get("name") or "黑冰")
                dmg_spec = str(ice.get("damage") or "2D6")
                atk = int(ice.get("atk") or 0)
                if atk > 0:
                    atk_total = roller.roll(10) + atk
                    def_total = _net_defense(conn, group_id, me, skill_name, roller)
                    lines.append(f"⚡ {ice_name}反扑：ATK {atk_total} vs 你的交互界面 {def_total}。")
                    if atk_total > def_total:
                        damage = _roll_damage(roller, dmg_spec)
                        _apply_damage_hp(combat, me, damage, lines)
                        lines.append(f"你被烧穿：{dmg_spec} = {damage} 点直接伤害（护甲不减）。")
                    else:
                        lines.append("你侧身让过链路，黑冰这一下没咬到你。")
                else:  # 旧数据没写 ATK：保留直接反噬
                    damage = _roll_damage(roller, dmg_spec)
                    _apply_damage_hp(combat, me, damage, lines)
                    lines.append(f"{ice_name}反噬：{dmg_spec} = {damage} 点直接伤害（护甲不减）。")
            else:
                lines.append("节点拒绝了你，链路断开，这次白费了动作。")

    _sync_pc_hp(conn, combat, me)
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 其他动作（书内页 168 的动作简表）──────────────────────────

_MOVE_WORDS = ("移动", "走位", "找掩体", "换位置")
_RELOAD_WORDS = ("装填", "换弹", "换弹匣", "上弹")
_GETUP_WORDS = ("起身", "爬起来")
_DELAY_WORDS = ("延迟", "等一等", "过", "跳过", "结束回合")
_RUN_WORDS = ("跑", "冲刺")


def _after_walk(me: "Combatant", used: int, slot: "Slot", combat: Combat,
                lines: list[str]) -> None:
    """徒步走完一段之后的伤势结算（两条都是「步行超过 4m」触发）。

    - 肋骨骨折/体内异物：每个徒步行进超过 4m 的回合，重新受到这个严重伤势的
      额外伤害，直接从生命值扣除（full.txt:15774、15816）。
    - 耳朵受损：步行超过 4m 后的下个回合无法进行移动动作（full.txt:15912-15920）。
    1 格 = 2 米，故超过 4 米即 used > 2 格。
    """
    if used <= 2:
        return
    if me.walk_damage:
        from cyberpunk.crit import SERIOUS_EXTRA_DAMAGE
        me.hp = max(0, me.hp - SERIOUS_EXTRA_DAMAGE)
        lines.append(f"  🩸 {me.name} 走了 {used} 格（{used * 2} 米）："
                     f"伤势发作，额外掉 {SERIOUS_EXTRA_DAMAGE} 点血。")
    if me.no_move_next:
        slot.no_move_round = combat.round + 1


def act(conn, group_id: str, user_id: str, text: str, *,
        roller: dice.Roller | None = None) -> str | None:
    """非攻击动作；这句话不是战斗动作就返回 None。"""
    body = (text or "").strip()
    if not body:
        return None
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        return None
    me = next((c for c in combat.combatants
               if c.owner_qq == user_id and c.side == "pc"), None)
    if me is None or not me.alive:
        return None
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        return None
    roller = roller or dice.RandomRoller()
    slot = _slots(combat)[me.owner_qq]
    lines: list[str] = []

    # 脊柱受伤：下个回合只能用移动动作（full.txt:15856）
    moving = (any(w in body for w in _MOVE_WORDS)
              or (any(w in body for w in _RUN_WORDS) and body.startswith("跑")))
    if not moving and slot.no_action_round == combat.round:
        return f"{me.name} 脊柱受伤：这一回合用不了其他动作，只能移动。"

    if any(w in body for w in _RELOAD_WORDS):
        if slot.action:
            return "这回合的动作已经用过了。"
        slot.action = True
        lines.append(f"🔄 {me.name} 花一个动作装填完毕（弹药不计数）。")
    elif any(w in body for w in _GETUP_WORDS):
        if slot.action:
            return "这回合的动作已经用过了。"
        slot.action = True
        lines.append(f"🧍 {me.name} 起身。")
    elif any(w in body for w in _DELAY_WORDS):
        slot.action = True
        lines.append(f"⏸ {me.name} 结束回合。")
    elif any(w in body for w in _RUN_WORDS) and body.startswith("跑"):
        if not slot.move:
            return "要先用移动动作，才能「跑」再获得一次移动（书内页 168）。"
        if slot.extra_move:
            return "这回合已经跑过了。"
        slot.action = True
        slot.extra_move = True
        lines.append(f"🏃 {me.name} 跑起来：本轮多一次移动。")
    elif any(w in body for w in _MOVE_WORDS):
        # 「跑」给的额外移动允许第二次移动（书内页 168：1 移动 + 1 动作）
        if slot.move and not slot.extra_move:
            return "这回合的移动动作已经用过了。"
        # 耳朵受损：上个回合步行超过 4m → 这回合不能移动（full.txt:15912-15920）
        if slot.no_move_round == combat.round:
            return f"{me.name} 头晕目眩：这一回合做不了移动动作。"
        game = _map_for(combat)
        if game is None or me.x < 0 or me.y < 0:
            slot.move = True
            lines.append(f"👟 {me.name} 移动（没有地图，位置由 GM 叙述）。")
        else:
            goal: tuple[int, int] | None = None
            found = re.search(r"(\d+)\s*[,， ]\s*(\d+)", body)
            if found:
                goal = (int(found.group(1)), int(found.group(2)))
            else:
                cover = next((c for c in game.covers
                              if _cover_hp(combat, c) > 0
                              and (c.name in body or c.id in body)), None)
                if cover is not None:
                    occupied = {(c.x, c.y) for c in combat.order if c is not me}
                    near = [p for p in game.neighbors((cover.x, cover.y))
                            if game.walkable(p) and p not in occupied]
                    goal = near[0] if near else None
            if goal is None:
                names = "、".join(c.name for c in game.covers if _cover_hp(combat, c) > 0)
                return (f"移动到哪里？可以说坐标（例如「移动 5,6」），或躲到掩体旁边（{names}）。")
            if not game.walkable(goal):
                return f"({goal[0]},{goal[1]}) 走不过去（{game.terrain_name(goal)}）。"
            budget = _move_budget(me, easy_roster.get(conn, group_id, me.owner_qq)
                                 or roster.get(conn, group_id, me.owner_qq))
            route, used, arrived = game.move((me.x, me.y), goal, budget)
            if not route:
                return "找不到通往那里的路。"
            me.x, me.y = route[-1]
            slot.move = True
            tail = "" if arrived else "（移动力用完，只走到半路）"
            lines.append(f"👟 {me.name} 移动到 ({me.x},{me.y})，走了 {used} 格{tail}。"
                         f"{game.describe((me.x, me.y))}（MOVE {budget} → 一次最多 {budget * 2} 米）")
            _after_walk(me, used, slot, combat, lines)
    else:
        return None

    if slot.action:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 投掷爆炸物（手榴弹；书内页 151「投掷」、153「爆炸物」）────────
#
# 规则依据（rules/red_core/raw/full.txt）：
#   :15074-15082 投掷物体＝「其他动作」做远程攻击，DEX+运动+1D10，射程 25 米，
#                距离 DV 等同榴弹发射器；目标 REF≥8 可闪避；伤害由 GM 定。
#   :14873-14892 高爆武器对 10×10 米（5×5 格）内所有目标结算，伤害骰只投一次；
#                判定不足 DV 时落点由 GM 在 10×10 米内偏移；足够抵挡的掩体
#                后面的目标不受伤害，掩体被打烂则吃满伤害。
#   :14760-14766 榴弹发射器距离 DV：16/15/15/17/20/22/25。
#   :14667-14670 手榴弹伤害 6D6。
GRENADE_WORDS = ("手榴弹", "手雷")
GRENADE_RANGE_M = 25
GRENADE_DAMAGE = "6D6"
GRENADE_WEAPON = "榴弹发射器"
BLAST_CELLS = 2  # 5×5 格
_SCATTER_DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1),
                 (-1, 0), (-1, -1), (0, -1), (1, -1))


def looks_like_grenade(text: str) -> bool:
    return any(w in (text or "") for w in GRENADE_WORDS)


def grenades(card) -> list[str]:
    """卡上带着的爆炸物（名称含「手榴弹 / 手雷」）。"""
    gear = list((card or {}).get("gear") or [])
    return [g for g in gear if "手榴弹" in g or "手雷" in g]


def _spend_gear(conn, group_id: str, owner_qq: str, item: str) -> bool:
    return (easy_roster.remove_gear(conn, group_id, owner_qq, item)
            or roster.remove_gear(conn, group_id, owner_qq, item))


def throw_grenade(conn, group_id: str, actor_qq: str, text: str = "", *,
                  roller: dice.Roller | None = None) -> str:
    """扔手榴弹：DEX+运动 对抗距离 DV，爆心 5×5 格、伤害骰只投一次。"""
    roller = roller or dice.RandomRoller()
    combat = load(conn, group_id)
    if combat is None or not combat.active:
        raise CombatError("现在没有在打。")
    me = next((c for c in combat.combatants
               if c.owner_qq == actor_qq and c.side == "pc"), None)
    if me is None:
        raise CombatError("你还没有参战的角色卡。")
    if not me.alive:
        raise CombatError("你已经倒下了。")
    cur = combat.current
    if cur is not None and cur.owner_qq != me.owner_qq:
        raise CombatError(f"现在轮到 {cur.name}，还没到你。")
    slot = _slots(combat)[me.owner_qq]
    if slot.action:
        raise CombatError("你这回合的动作已经用过了，等下一轮。")

    card = (easy_roster.get(conn, group_id, actor_qq)
            or roster.get(conn, group_id, actor_qq))
    held = grenades(card)
    if not held:
        return (f"🧨 {me.name} 身上没有手榴弹（装备里没有「手榴弹 / 手雷」）。"
                "可以先用「装备 手榴弹」补上。")
    item = held[0]

    target = _find_target(combat, target_from_text(combat, text, me), me)
    game = _map_for(combat)
    on_map = game is not None and me.x >= 0 and me.y >= 0
    here = (me.x, me.y) if on_map else None
    there = (target.x, target.y) if target.x >= 0 and target.y >= 0 else None

    lines = [f"🧨 {me.name} 朝 {target.name} 扔出{item}："
             f"敏捷(DEX)+运动+1D10，射程 {GRENADE_RANGE_M} 米。"]
    dv: int | None = None
    if on_map and here and there:
        meters = game.meters(here, there)
        if meters > GRENADE_RANGE_M:
            return (f"太远了：{me.name}到{target.name}有 {meters} 米，"
                    f"手榴弹最多扔 {GRENADE_RANGE_M} 米。")
        walls = [b for b in game.blockers(here, there) if b.startswith("墙")]
        if walls:
            return f"{walls[0]}挡在中间，手榴弹扔不过去。"
        dv = game.dv(here, there, GRENADE_WEAPON)
        if dv is None:
            return (f"{meters} 米太远了，{GRENADE_WEAPON}在 "
                    f"{GRENADE_RANGE_M} 米内才扔得准。")
        lines.append(f"距离 {meters} 米，DV {dv}。")
    else:
        meters = me.range_m or target.range_m
        if meters > GRENADE_RANGE_M:
            return (f"太远了：到{target.name}有 {meters} 米，"
                    f"手榴弹最多扔 {GRENADE_RANGE_M} 米。")
        dv = gridmap.dv_for_meters(GRENADE_WEAPON, meters) if meters > 0 else None
        if dv is None:
            return RANGE_ASK
        lines.append(f"距离 {meters} 米（估），DV {dv}；没有地图数据，爆炸按同场全体处理。")

    level = int(((card or {}).get("skills") or {}).get("运动", 0))
    total = (roller.roll(10) + me.dex + level + me.wound_penalty
             + me.ranged_penalty + modifier_for(combat, me))
    lines.append(f"投掷判定：{total} vs DV {dv}{modifier_text(combat, me)}")
    hit = total >= dv
    center = there
    if hit:
        lines.append("落点就在目标脚下。")
    elif on_map and there:
        step = _SCATTER_DIRS[roller.roll(8) - 1]
        far = roller.roll(2)
        spot = (there[0] + step[0] * far, there[1] + step[1] * far)
        if game.inside(spot):
            center = spot
            lines.append(f"没扔准：落点偏到 ({spot[0]},{spot[1]})，照样爆炸。")
        else:
            lines.append("没扔准，手榴弹砸在目标附近的墙上弹了回来，照样爆炸。")
    else:
        lines.append("没扔准，但爆炸还是盖住了目标所在的位置。")

    damage = _roll_damage(roller, GRENADE_DAMAGE)
    lines.append(f"爆炸伤害 {GRENADE_DAMAGE} = {damage}（伤害骰只掷一次，对所有目标生效）。")

    if on_map and center:
        victims = [c for c in combat.order
                   if c.alive and c.x >= 0 and c.y >= 0
                   and max(abs(c.x - center[0]), abs(c.y - center[1])) <= BLAST_CELLS]
        lines.append(f"爆心 5×5 格（{center[0]},{center[1]}）：{len(victims)} 个目标。")
    else:
        victims = [c for c in combat.order if c.alive]
        lines.append(f"没有地图：同场 {len(victims)} 个目标全部吃爆炸。")

    dodged: set[int] = set()
    for c in victims:
        if c is me or not c.alive or c.ref < 8:
            continue
        if c.no_dodge:  # 断腿：连爆炸都躲不开（full.txt:15876）
            continue
        dodge = roller.roll(10) + c.ref
        if dodge > total:
            dodged.add(id(c))

    touched: set[str] = set()
    for c in victims:
        if id(c) in dodged:
            lines.append(f"  {c.name} 反应过人，闪出了爆炸范围。")
            continue
        cover = None
        if on_map and center and c.x >= 0 and c.y >= 0:
            found = game.cover_on_line(center, (c.x, c.y))
            if found is not None and _cover_hp(combat, found) > 0:
                cover = found
        if cover is not None and _cover_hp(combat, cover) >= damage:
            left = _cover_hp(combat, cover) - damage
            combat.covers[cover.id] = left
            touched.add(cover.id)
            lines.append(f"  {c.name} 躲在{cover.name}后面，掩体挡下全部爆炸（耐久剩 {left}）。")
            continue
        if cover is not None:
            combat.covers[cover.id] = 0
            touched.add(cover.id)
            lines.append(f"  {cover.name} 被炸烂了，{c.name} 吃满伤害。")
        armor = c.effective_sp
        taken = max(0, damage - armor)
        if taken:
            c.sp_wear += 1
        _apply_damage_hp(combat, c, taken, lines)
        _sync_pc_hp(conn, combat, c)
        lines.append(f"  {c.name} 被炸：护甲挡 {min(damage, armor)}，掉 {taken} 血"
                     f"（{max(0, c.hp)}/{c.max_hp} HP）。")

    if on_map and center:
        for cover in game.covers:
            if cover.id in touched or _cover_hp(combat, cover) <= 0:
                continue
            if max(abs(cover.x - center[0]), abs(cover.y - center[1])) > BLAST_CELLS:
                continue
            left = max(0, _cover_hp(combat, cover) - damage)
            combat.covers[cover.id] = left
            lines.append(f"  {cover.name} 被爆炸削掉 {damage} 点耐久（剩 {left}）。")

    _spend_gear(conn, group_id, actor_qq, item)
    slot.action = True
    _check_over(combat, lines)
    if combat.active:
        _advance(conn, combat, roller, lines)
    _save(conn, combat)
    return "\n".join(lines)


# ── 文本识别 ──────────────────────────────────────────────────

ATTACK_HINTS = ("打他", "打它", "打那个", "开枪", "射击", "开火", "砍他", "捅他",
                "爆头", "扫射", "招呼他", "朝他", "一拳")
SKILL_HINTS = {
    "手枪": "手枪", "步枪": "抵肩枪械", "霰弹": "抵肩枪械", "狙击": "抵肩枪械",
    "弓": "抵肩枪械", "冲锋": "自动武器", "机枪": "自动武器", "自动": "自动武器",
    "近战": "近战武器", "刀": "近战武器", "棍": "近战武器", "剑": "近战武器",
    "武术": "武术", "拳": "搏击", "重武器": "重武器", "火箭": "重武器",
}


def looks_like_attack(text: str) -> bool:
    return any(h in (text or "") for h in ATTACK_HINTS)


def skill_from_text(text: str) -> str | None:
    body = text or ""
    for key, skill in SKILL_HINTS.items():
        if key in body:
            return skill
    return "手枪" if "枪" in body else None


def target_from_text(combat: Combat, text: str,
                     actor: Combatant | None = None) -> str:
    body = text or ""
    pool = foes(combat, actor) if actor is not None else combat.enemies
    for c in pool:
        if c.name in body:
            return c.name
    return pool[0].name if pool else ""


def active(conn, group_id: str) -> Combat | None:
    combat = load(conn, group_id)
    return combat if combat is not None and combat.active else None


# ── 文本入口（给网关与 core 用）───────────────────────────────


def parse_enemy_arg(raw: str) -> tuple[str, int]:
    parts = (raw or "").strip().split()
    if not parts:
        return DEFAULT_ENEMY, 1
    name = parts[0]
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    return name, max(1, count)


def parse_attack_arg(raw: str) -> tuple[str, str | None]:
    parts = (raw or "").strip().split()
    if not parts:
        return "", None
    return parts[0], (parts[1] if len(parts) > 1 else None)


_COUNT_EXPR = re.compile(r"^[0-9pc+\-*/() ]+$")


def _count_value(raw, pc: int) -> int:
    """把 encounter 的 count 转成人数：整数、=pc、=pc-2、=(pc-2)//2 等。"""
    if isinstance(raw, int):
        return max(0, raw)
    text = str(raw or "").strip()
    if not text:
        return 1
    if text.startswith("="):
        expr = text[1:]
        if not _COUNT_EXPR.match(expr):
            return 1
        try:
            value = eval(expr, {"__builtins__": {}}, {"pc": pc})  # noqa: S307
        except Exception:
            return 1
        return max(0, int(value))
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return 1


def _actor_status(conn, campaign_id) -> dict[str, str]:
    if campaign_id is None:
        return {}
    rows = conn.execute(
        "SELECT actor_id, status FROM campaign_actors WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    return {str(r["actor_id"]): str(r["status"] or "") for r in rows}


def _condition_ok(cond, status: dict[str, str]) -> bool:
    """条件组：{"escaped": [...], "not_escaped": [...]}。"""
    if not isinstance(cond, dict):
        return True
    escaped = [str(x) for x in (cond.get("escaped") or [])]
    not_escaped = [str(x) for x in (cond.get("not_escaped") or [])]
    if any(status.get(a) != "escaped" for a in escaped):
        return False
    if any(status.get(a) == "escaped" for a in not_escaped):
        return False
    return True


def _encounter_groups(
    conn, group_id: str
) -> tuple[tuple[dict, ...], tuple[dict, ...]] | None:
    """场景里写了 encounter 就自动铺场（只在玩家没指定敌人时用）。

    返回 (敌人组, 环境修正)；敌人组里可以带 ``script``（脚本名或内联 dict）。
    """
    row = conn.execute(
        "SELECT c.id AS campaign_id, c.scenario, c.scene FROM group_sessions s"
        " JOIN campaigns c ON c.id = s.campaign_id WHERE s.group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        return None
    campaign_id = int(row["campaign_id"]) if row["campaign_id"] is not None else None
    try:
        from core import story as story_mod
        data = story_mod.load(str(row["scenario"] or story_mod.DEFAULT_ID))
    except Exception:
        return None
    scene_id = str(row["scene"] or "")
    enc = (data.get("scenes", {}).get(scene_id) or {}).get("encounter")
    if not enc:
        return None
    pc = max(1, len(roster.members(conn, group_id)) + len(easy_roster.members(conn, group_id)))
    statuses = _actor_status(conn, campaign_id)
    script_bank = {str(k): v for k, v in (data.get("scripts") or {}).items()}
    script_bank.update({str(k): v for k, v in (enc.get("scripts") or {}).items()})
    enc_range = int(enc.get("range_m") or 0)
    groups: list[dict] = []
    for g in enc.get("groups") or []:
        ref = str(g.get("ref") or "").strip()
        if ref:
            if campaign_id is None:
                continue
            grp = actor_group(conn, campaign_id, ref,
                              fresh=bool(g.get("fresh")))
            if grp is None:
                continue
            cond = g.get("if")
            if isinstance(cond, dict):
                if not _condition_ok(cond, statuses):
                    continue
            elif cond:
                if grp.get("status") != str(cond).strip():
                    continue
            elif grp.get("status") == "dead":
                continue
            grp["faction"] = str(g.get("faction") or grp["faction"])
            grp["label"] = str(g.get("label") or grp.get("label") or "")
            grp["hostile_to"] = list(g.get("hostile_to") or [])
            grp["range_m"] = int(g.get("range_m") or enc_range or grp.get("range_m") or 0)
            if g.get("script") is not None:
                script = g.get("script")
                if isinstance(script, str):
                    script = script_bank.get(script) or {}
                grp["script"] = dict(script) if isinstance(script, dict) else {}
            groups.append(grp)
            continue
        name = str(g.get("enemy") or g.get("name") or "").strip()
        if not name or enemy_preset(name) is None:
            continue
        if not _condition_ok(g.get("if"), statuses):
            continue
        raw = g.get("count", 1)
        count = _count_value(raw, pc)
        if count <= 0:
            continue
        script = g.get("script")
        if isinstance(script, str):
            script = script_bank.get(script) or {}
        groups.append({
            "name": name,
            "count": count,
            "actor": str(g.get("actor") or "").strip(),
            "faction": str(g.get("faction") or "enemy"),
            "label": str(g.get("label") or ""),
            "hostile_to": list(g.get("hostile_to") or []),
            "script": dict(script) if isinstance(script, dict) else {},
            "spawn": g.get("spawn"),
            "range_m": int(g.get("range_m") or enc_range or 0),
        })
    _encounter_meta[group_id] = {"scene": scene_id, "note": str(enc.get("note") or "")}
    if not groups:
        return (), ()
    modifiers = tuple(e for e in (enc.get("modifiers") or ()) if isinstance(e, dict))
    return tuple(groups), modifiers


_encounter_meta: dict[str, dict] = {}


def start_text(conn, group_id: str, arg: str, *, campaign_id=None,
               scene: str = "") -> str:
    groups = None
    modifiers: tuple[dict, ...] = ()
    if not (arg or "").strip():
        found = _encounter_groups(conn, group_id)
        if found is not None:
            groups, modifiers = found
            if not groups:
                meta = _encounter_meta.get(group_id) or {}
                note = str(meta.get("note") or "")
                tip = f"（{note}）" if note else ""
                return f"🚪 这次遭遇没有人登场，直接跳过{tip}。"
    if groups is None:
        name, count = parse_enemy_arg(arg)
        groups = ({"name": name, "count": count, "faction": "enemy"},)
    groups = [
        g if isinstance(g, dict) else {"name": str(g[0]), "count": int(g[1]),
                                       "faction": "enemy"}
        for g in groups
    ]
    start(conn, group_id, campaign_id=campaign_id, scene=scene, enemies=groups,
          modifiers=modifiers)
    label = "、".join(
        f"{g['name']}" + (f"×{g['count']}" if g["count"] > 1 else "")
        for g in groups)
    meta = _encounter_meta.get(group_id) or {}
    tip = f"（{meta.get('note')}）" if meta.get("note") else ""
    return f"⚔ 开打：{label}{tip}（先攻 = 反应 + 1D10）" + chr(10) + status_text(conn, group_id)


def attack_text(conn, group_id: str, user_id: str, arg: str) -> str:
    target, skill = parse_attack_arg(arg)
    return attack(conn, group_id, user_id, target, skill=skill)


def stop_text(conn, group_id: str) -> str:
    end(conn, group_id)
    return "🏁 战斗结束，场地清空。"
