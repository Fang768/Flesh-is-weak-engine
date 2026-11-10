"""非战斗伤害：坠落、电击、溺水/窒息、燃烧、中毒。

数值全部来自核心书（书内页 181–183 = PDF 199–200）：

- **坠落**：跌落高度 ≥10 米时，每 10 米 2d6，**可被护甲吸收**；未通过 DV15 运动
  判定则「断腿」（严重伤势）。40 米以上坠落在回合结束时结算，半空中可 DV15 运动自救。
- **电击**：立即 6d6，**护甲正常吸收**；未与电源分离则每回合再受一次。
- **溺水**：屏息时长 = 体格（BODY）分钟；开始溺水当回合受 **BODY 点直接 HP 伤害**。
- **燃烧**：木制品 2 点 / 汽油 4 点 / 铝热剂 6 点**直接 HP 伤害**。
- **中毒**：折磨与药物抵抗（WILL）判定，轻 DV11 1d6、强 DV13 2d6、致命 DV15 3d6，
  都是**直接 HP 伤害**（护甲不减伤）。

「直接 HP 伤害」= 不经过护甲减伤；「可被吸收」= 先减有效 SP 再进 HP。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from cyberpunk import immunity
from cyberpunk.dice import Roller
from cyberpunk.damage import roll_notation
from cyberpunk.stats import derived as derived_stats

FALL_PER_METERS = 10          # 每 10 米
FALL_DICE = "2D6"
FALL_SAVE_DV = 15
FALL_SERIOUS_METERS = 10      # 达到这个高度才可能断腿
FALL_DEATH_METERS = 120       # 常识性上限：这个高度直接死

SHOCK_DICE = "6D6"

BURNING = {
    "木": 2,
    "木制品": 2,
    "汽油": 4,
    "铝热剂": 6,
}

POISON = {
    "轻": (11, "1D6"),
    "强": (13, "2D6"),
    "致命": (15, "3D6"),
}


@dataclass(slots=True)
class Hazard:
    kind: str
    damage: int
    hp_before: int
    hp_after: int
    note: str = ""

    @property
    def dead(self) -> bool:
        return self.hp_after < 1

    def line(self) -> str:
        head = f"{self.kind}：{self.damage} 点伤害（HP {self.hp_before} → {self.hp_after}）"
        if self.dead:
            head += " —— 倒下了，需要死亡豁免"
        if self.note:
            head += "\n" + self.note
        return head


# ---------------------------------------------------------------- 卡与 HP


def _read(conn: sqlite3.Connection, group_id: str, owner_qq: str) -> tuple[str, dict] | None:
    """返回 (存法, 卡)；存法是 "red" 或 "easy"。"""
    row = conn.execute(
        "SELECT stats, state FROM characters WHERE group_id = ? AND owner_qq = ?",
        (str(group_id), str(owner_qq)),
    ).fetchone()
    if row is not None:
        card = {"stats": json.loads(row["stats"]), "state": json.loads(row["state"] or "{}")}
        return "red", card

    row = conn.execute(
        "SELECT data FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
        (str(group_id), str(owner_qq)),
    ).fetchone()
    if row is not None:
        return "easy", json.loads(row["data"])

    return None


def _hp(card: dict, *, easy: bool) -> int:
    """当前 HP；核心书卡没写过 state.hp 时按派生 HP 起算。"""
    if easy:
        return int(card.get("hp") or 0)
    state = card.get("state") or {}
    if state.get("hp") is None:
        return int(derived_stats(card.get("stats") or {}).hp)
    return int(state["hp"])


def _max_hp(card: dict, *, easy: bool) -> int:
    if easy:
        return int(card.get("hp") or 0)
    state = card.get("state") or {}
    return int(state.get("max_hp") or derived_stats(card.get("stats") or {}).hp)


def _body(card: dict) -> int:
    stats = card.get("stats") or {}
    return int(stats.get("BODY") or 0)


def _sp(card: dict) -> int:
    state = card.get("state") or {}
    armor = card.get("armor")
    armor_sp = armor.get("sp") if isinstance(armor, dict) else None
    return int(state.get("sp") or armor_sp or card.get("sp") or 0)


def _write(conn: sqlite3.Connection, group_id: str, owner_qq: str, kind: str, card: dict) -> None:
    if kind == "easy":
        conn.execute(
            "UPDATE easy_cards SET data = ?, updated_at = datetime('now')"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(card, ensure_ascii=False), str(group_id), str(owner_qq)),
        )
    else:
        conn.execute(
            "UPDATE characters SET state = ?, updated_at = datetime('now')"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(card.get("state") or {}, ensure_ascii=False), str(group_id), str(owner_qq)),
        )
    conn.commit()


def _settle(
    conn: sqlite3.Connection,
    group_id: str,
    owner_qq: str,
    *,
    kind_label: str,
    dice: str | None = None,
    flat: int | None = None,
    direct: bool,
    roller: Roller,
    note: str = "",
) -> Hazard:
    found = _read(conn, group_id, owner_qq)
    if found is None:
        raise HazardError("你还没有角色卡。先说「给我一个角色」。")
    kind, card = found
    easy = kind == "easy"
    hp = _hp(card, easy=easy)
    max_hp = _max_hp(card, easy=easy)

    raw = flat if flat is not None else roll_notation(roller, dice or "1D6")
    # 简易版没有 SP 概念时按 0 处理；核心书卡读 state.sp
    if direct:
        taken = raw
    else:
        taken = max(0, raw - _sp(card))

    after = max(0, hp - taken)
    if easy:
        card["hp"] = after
    else:
        state = card.setdefault("state", {})
        state["hp"] = after
        if not state.get("max_hp"):
            state["max_hp"] = hp or max_hp
    _write(conn, group_id, owner_qq, kind, card)
    return Hazard(kind=kind_label, damage=taken, hp_before=hp, hp_after=after, note=note)


class HazardError(Exception):
    """输入不对或没有卡。"""


# ---------------------------------------------------------------- 各类伤害


def fall(
    conn: sqlite3.Connection,
    group_id: str,
    owner_qq: str,
    meters: int,
    *,
    roller: Roller,
    saved: bool = False,
) -> Hazard:
    if meters <= 0:
        raise HazardError("高度要大于 0 米。")
    if meters >= FALL_DEATH_METERS:
        found = _read(conn, group_id, owner_qq)
        if found is None:
            raise HazardError("你还没有角色卡。")
        kind, card = found
        hp = _hp(card, easy=kind == "easy")
        hz = _settle_direct_death(conn, group_id, owner_qq, kind, card, hp)
        return hz

    if meters < FALL_SERIOUS_METERS:
        # 低于 10 米：书面规则只对 ≥10 米给伤害；这里按每 10 米 2d6 折算，最小 1d6
        steps = max(1, meters // FALL_PER_METERS)
        return _settle(
            conn, group_id, owner_qq,
            kind_label=f"坠落 {meters} 米",
            dice=f"{steps * 2}D6" if steps > 0 else FALL_DICE,
            direct=False,
            roller=roller,
        )

    steps = meters // FALL_PER_METERS
    note = ""
    if not saved:
        note = f"没有通过 DV{FALL_SAVE_DV} 的运动判定：断腿（严重伤势）。"
        note += "\n（赛博腿可以免疫这条；超过 30 米的话赛博腿也没用。）"
    else:
        note = "运动判定通过，没有断腿。"
    return _settle(
        conn, group_id, owner_qq,
        kind_label=f"坠落 {meters} 米",
        dice=f"{steps * 2}D6",
        direct=False,
        roller=roller,
        note=note,
    )


def _settle_direct_death(conn, group_id, owner_qq, kind, card, hp) -> Hazard:
    if kind == "easy":
        card["hp"] = 0
    else:
        card.setdefault("state", {})["hp"] = 0
    _write(conn, group_id, owner_qq, kind, card)
    return Hazard(kind="坠落", damage=hp, hp_before=hp, hp_after=0, note="这个高度不需要死亡豁免，当场死亡。")


def shock(conn, group_id, owner_qq, *, roller: Roller, turns: int = 1) -> Hazard:
    turns = max(1, int(turns))
    return _settle(
        conn, group_id, owner_qq,
        kind_label="电击" if turns == 1 else f"电击（{turns} 回合）",
        dice=f"{6 * turns}D6" if turns > 1 else SHOCK_DICE,
        direct=False,
        roller=roller,
        note="护甲照常吸收；只要没和电源分开，每回合都会重来一次。",
    )


def drown(conn, group_id, owner_qq, *, roller: Roller) -> Hazard:
    found = _read(conn, group_id, owner_qq)
    if found is None:
        raise HazardError("你还没有角色卡。")
    kind, card = found
    # 鳃（full.txt:10101-10105「能够在水下呼吸」）/ 独立供氧（:10112-10117）
    # 直接免溺水：先看免疫，再看屏息。
    source = immunity.is_immune(card, "溺水") or immunity.is_immune(card, "窒息")
    if source:
        return Hazard(
            kind="溺水/窒息",
            damage=0,
            hp_before=_hp_of(conn, group_id, owner_qq),
            hp_after=_hp_of(conn, group_id, owner_qq),
            note=f"{source}：在水下照常呼吸，不受溺水影响。",
        )
    body = _body(card) or 4
    return _settle(
        conn, group_id, owner_qq,
        kind_label="溺水/窒息",
        flat=body,
        direct=True,
        roller=roller,
        note=f"屏息上限 {body} 分钟；直接伤害不看护甲。",
    )


def burn(conn, group_id, owner_qq, material: str, *, roller: Roller) -> Hazard:
    key = (material or "").strip()
    if key not in BURNING:
        raise HazardError("可燃物要写：木 / 汽油 / 铝热剂。")
    return _settle(
        conn, group_id, owner_qq,
        kind_label=f"燃烧（{key}）",
        flat=BURNING[key],
        direct=True,
        roller=roller,
        note="直接 HP 伤害，不经过护甲。",
    )


def poison(conn, group_id, owner_qq, grade: str, *, roller: Roller, resisted: bool = False) -> Hazard:
    key = (grade or "").strip()
    if key not in POISON:
        raise HazardError("毒性要写：轻 / 强 / 致命。")
    dv, dice = POISON[key]
    # 鼻滤器（:10130-10135「对毒气、废气等类似危险效果免疫」）。
    # 「约定」：鼻滤器免的是**毒气/废气**这类环境效果；书本给「中毒」检定的是
    # 折磨&药物耐受，毒物中和植入（:10140-10145）才是那条 +2，不是免疫。
    # 所以只有毒气档（吸入性）走免疫，注射/口服中毒仍要检定。
    found = _read(conn, group_id, owner_qq)
    if found is not None:
        source = immunity.is_immune(found[1], "毒气")
        if source:
            return Hazard(
                kind=f"中毒（{key}）",
                damage=0,
                hp_before=_hp_of(conn, group_id, owner_qq),
                hp_after=_hp_of(conn, group_id, owner_qq),
                note=f"{source}：毒气/废气被过滤掉，不受影响。",
            )
    if resisted:
        return Hazard(
            kind=f"中毒（{key}）",
            damage=0,
            hp_before=_hp_of(conn, group_id, owner_qq),
            hp_after=_hp_of(conn, group_id, owner_qq),
            note=f"折磨与药物抵抗 DV{dv} 通过，没有中毒。",
        )
    return _settle(
        conn, group_id, owner_qq,
        kind_label=f"中毒（{key}）",
        dice=dice,
        direct=True,
        roller=roller,
        note=f"折磨与药物抵抗 DV{dv} 没过；直接 HP 伤害，护甲不减伤。",
    )


def _hp_of(conn, group_id, owner_qq) -> int:
    found = _read(conn, group_id, owner_qq)
    if found is None:
        raise HazardError("你还没有角色卡。")
    kind, card = found
    return _hp(card, easy=kind == "easy")


# ---------------------------------------------------------------- 文本入口

# 坠落必须写明高度（"坠落 12 米" / "从 12 米跳下去"）；只说「我跳下去」交给模型当行动处理
# 坠落要写明高度：先说动作（坠落 12 米）或先说高度（从 12 米跳下去）都认
_FALL = re.compile(
    r"(?:坠落|跳(?:下|楼|崖)去?|摔(?:下|下来)|掉下去)"
    r"\s*(\d{1,3})\s*米?\s*(自救|跳伞)?"
    r"|(\d{1,3})\s*米[^\n]{0,8}?(?:跳|摔|坠落|掉)"
    r"(?:\s*(自救|跳伞))?"
)

_SHOCK = re.compile(r"(?:电击|触电|被电)")
_DROWN = re.compile(r"(?:溺水|窒息|憋气|淹死|淹到)")
_BURN = re.compile(r"(?:燃烧|着火|身上起火)\s*([一-鿿]{1,4})?")
_POISON = re.compile(r"(?:中毒|下毒|毒物)\s*([一-鿿]{1,3})?")


def match(text: str) -> str | None:
    """这句话是不是环境伤害？返回类型名。"""
    body = (text or "").strip()
    for kind, pattern in (
        ("fall", _FALL),
        ("shock", _SHOCK),
        ("drown", _DROWN),
        ("burn", _BURN),
        ("poison", _POISON),
    ):
        if pattern.search(body):
            return kind
    return None


def apply(conn, group_id: str, owner_qq: str, text: str, *, roller: Roller) -> str:
    """把一句话解释成一次环境伤害并落库，返回给玩家看的文本。"""
    body = (text or "").strip()
    try:
        if m := _FALL.search(body):
            meters = int(m.group(1) or m.group(3))
            saved = bool(m.group(2) or m.group(4))
            return fall(conn, group_id, owner_qq, meters, roller=roller, saved=saved).line()
        if _SHOCK.match(body):
            return shock(conn, group_id, owner_qq, roller=roller).line()
        if _DROWN.match(body):
            return drown(conn, group_id, owner_qq, roller=roller).line()
        if m := _BURN.search(body):
            material = (m.group(1) or "木").strip()
            if material not in BURNING:
                return "可燃物只认：木 / 汽油 / 铝热剂。"
            return burn(conn, group_id, owner_qq, material, roller=roller).line()
        if m := _POISON.search(body):
            grade = (m.group(1) or "轻").strip()
            if grade not in POISON:
                return "毒性只认：轻 / 强 / 致命。"
            return poison(conn, group_id, owner_qq, grade, roller=roller).line()
    except HazardError as exc:
        return str(exc)
    return "没看懂这是什么伤害。可以写：坠落 12 米 / 电击 / 溺水 / 燃烧 汽油 / 中毒 强"
