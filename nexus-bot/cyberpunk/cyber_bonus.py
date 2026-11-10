"""义体加成效：从角色卡已安装的赛博组件（RED 义体），算出检定型加成。

数据表 ``rules/red_core/data/cyberware_bonus.json``：key = ``cyberware_hl.json``
（RED 义体表）的中文名，即 ``core/cyber_flow`` 安装后写进 ``state.cyber`` 的名字。
每条 ``{bonus:N, skills:[技能名]|scope:"远程", note}``。

角色卡 ``state.cyber`` 存的就是这个中文名（``core/cyber_flow._remember`` 用
``entry["zh"]``，entry 来自 ``cyber.hl_of(name)``）。所以这里**直接用
``state.cyber`` 的名字查表，不需要再反查 V3 义体**。

「约定」：
* ``check_bonus`` 只做**检定型**加成（加到检定结果上），不改卡面属性。
* ``hit_bonus`` 做**战斗命中修正**（瞄准/远视目镜）。
* ``attribute_bonus`` 做**永久属性加成**（如骨肉织绕 体格+2），运行时生效、
  不写死基础 stats；具体合成进 ``character.py`` 的 ``effective_stat``。
* ``armor_sp`` 做**防御端护甲 SP**（编织皮肤 SP7 / 皮下护甲 SP11），不叠加、取高。
* ``rest_multiplier`` 做**休整恢复倍率**（抗体增强 每日恢复体格×2）。
* ``scope:"远程"`` = 所有远程武器技能。``miss_modes`` 是额外限定（如"瞄准"）。
* 查不到对应加成 → 静默忽略（不阻断检定）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "rules" / "red_core" / "data"
BONUS_FILE = DATA_DIR / "cyberware_bonus.json"
ABILITY_FILE = DATA_DIR / "cyberware_ability.json"

# 远程武器技能（与 core/combat 的 _WEAPON_TABLE 对齐；scope:"远程" 会展开成这些）
RANGED_SKILLS = ("手枪", "冲锋枪", "霰弹枪", "突击步枪", "狙击步枪", "重武器",
                 "自动武器")


@lru_cache(maxsize=1)
def bonus_table() -> dict[str, dict]:
    if not BONUS_FILE.exists():
        return {}
    data = json.loads(BONUS_FILE.read_text(encoding="utf-8"))
    return dict(data.get("items", {}))


@lru_cache(maxsize=1)
def combo_table() -> list[dict]:
    """F 类组合触发（``full.txt:9726``/``:9753``/``:9737``）。

    组合加成与单件加成不同：它**依赖多件义体的共存关系**，所以单独一张表。
    每条：``{id, requires_all|requires_count, bonus, skills, note}``。
    """
    if not BONUS_FILE.exists():
        return []
    data = json.loads(BONUS_FILE.read_text(encoding="utf-8"))
    combos = data.get("combos")
    return [dict(c) for c in combos] if isinstance(combos, list) else []


def _raw_cyber(card: dict) -> list:
    """卡上义体原始列表（RED 卡在 ``state.cyber``，简易卡在顶层 ``cyber``）。

    两处都有时**只用顶层的**——简易卡的顶层是权威来源，``state.cyber`` 可能是
    历史残留；合并会导致同一件义体被算两次（加成翻倍）。
    """
    top = card.get("cyber")
    if isinstance(top, (list, tuple)) and top:
        return list(top)
    state = card.get("state")
    if isinstance(state, dict):
        return list(state.get("cyber") or [])
    return []


def installed_names(card: dict) -> list[str]:
    """卡上已装的义体名（兼容旧「字符串列表」与新「实例记录」）。"""
    from . import slots
    return slots.names_of(_raw_cyber(card))


def installed_records(card: dict) -> list[dict]:
    """卡上已装的义体**实例记录**（字符串会就地迁移成记录）。"""
    from . import slots
    return slots.normalize(_raw_cyber(card))


@lru_cache(maxsize=1)
def ability_table() -> dict[str, dict]:
    """C 类「能力型」义体说明（无自动数值，交 DM）。"""
    if not ABILITY_FILE.exists():
        return {}
    data = json.loads(ABILITY_FILE.read_text(encoding="utf-8"))
    return dict(data.get("items", {}))


def abilities(card: dict) -> list[str]:
    """已装的「能力型」义体 → 可读说明（给 /义体 面板）。"""
    table = ability_table()
    out: list[str] = []
    for zh in installed_names(card):
        spec = table.get(zh)
        if spec:
            out.append(f"{zh}（{spec.get('ability', '')}）")
    return out


def has_cyberware(card: dict, name: str) -> bool:
    """这张卡装没装某件义体（两种存法都认）。"""
    return str(name) in installed_names(card)


def installed_with_rule(card: dict) -> list[tuple[str, dict]]:
    """卡片上装了的义体名，配上规则表里的加成条目（可能无）。

    返回 ``[(名字, rule)]``；``rule`` 在表里没有就是 ``None``。
    """
    table = bonus_table()
    return [(zh, table.get(zh)) for zh in installed_names(card)]


def _name_counts(card: dict) -> dict[str, int]:
    """已装义体按「名字 → 份数」聚合（荧光纹身 ×3 这种组合要用到）。"""
    counts: dict[str, int] = {}
    for zh in installed_names(card):
        counts[zh] = counts.get(zh, 0) + 1
    return counts


def active_combos(card: dict) -> list[dict]:
    """当前卡满足的 F 类组合（一条组合只算一次，不随件数叠加）。

    * ``requires_all``：列出的义体**每件至少 1 份**。
    * ``requires_count``：``{名字: n}``，该义体至少 n 份。

    规则书对同一效果重复触发只写一次（``:9726``/``:9753`` 是同一 +2 的双向表述；
    ``:9737`` 只说「三个甚至更多纹身」→ 达到即可，不按超过 3 的数量叠）。
    """
    counts = _name_counts(card)
    hits: list[dict] = []
    for combo in combo_table():
        need_all = [str(x) for x in (combo.get("requires_all") or [])]
        need_count = {str(k): int(v)
                      for k, v in (combo.get("requires_count") or {}).items()}
        if need_all and all(counts.get(n, 0) >= 1 for n in need_all):
            hits.append(combo)
        elif need_count and all(counts.get(n, 0) >= c
                                for n, c in need_count.items()):
            hits.append(combo)
    return hits


def combo_check_bonus(card: dict, *, skill: str) -> tuple[int, list[str]]:
    """组合触发带来的检定型加成（与单件加成合并展示用）。"""
    sk = (skill or "").strip()
    total = 0
    notes: list[str] = []
    for combo in active_combos(card):
        if sk not in [str(s) for s in (combo.get("skills") or [])]:
            continue
        bonus = int(combo.get("bonus") or 0)
        if bonus:
            total += bonus
            notes.append(str(combo.get("id") or "组合"))
    return total, notes


def _card_skill_level(card: dict, skill: str) -> int:
    """卡上该项技能的原始等级（RED 卡 / 简易卡都是 ``card["skills"]``）。"""
    skills = card.get("skills") or {}
    try:
        return int(skills.get(skill, 0))
    except (TypeError, ValueError):
        return 0


def skill_chip_bonus(card: dict, *, skill: str) -> tuple[int, list[str]]:
    """技能芯片：把绑定的那项技能**等级下限抬到 3**（``full.txt:27830-27837``）。

    原文：「技能芯片会让使用者在特定的技能等级上到达+3，除非使用者的技能等级
    已经超过+3，在这种情况下，它不会起效。」→ 等级达到 3 即止；原本 >3 无效，
    所以对检定表现为 ``max(0, 3 − 当前等级)`` 的加值（等价于下限 3，不叠加）。
    """
    sk = (skill or "").strip()
    if not sk:
        return 0, []
    state = card.get("state") or {}
    bound = state.get("skill_chips") or []
    if sk not in [str(x) for x in bound]:
        return 0, []
    base = _card_skill_level(card, sk)
    if base >= 3 or base < 0:
        return 0, []
    delta = 3 - base
    return delta, [f"技能芯片{delta:+d}"]


# ── 移动型义体（full.txt:10382 / :10388 / :10409 / :10395） ──────────
#
# 规则书 `:14488`「游泳，攀爬或者助跑跳，每经过 1m 都要花费 2m 的最大移动
# 距离」——攀爬/跳跃是**移动方式**倍率（×2），不是地形。四条义体各自豁免。
#
# * 攀爬脚 ``:10382``「攀爬时无视移速惩罚」→ 攀爬倍率 1。
# * 跳跃增强 ``:10388``「当跳跃时无视移速惩罚」→ 跳跃倍率 1。
# * 蹼足 ``:10409``「当游泳时无视移速惩罚」→ 水格花费 1（默认 2）。
# * 轮滑脚 ``:10395``「当使用奔跑动作时移速会增加 6 米」→ 跑后额外移动 +3 格。

#: 轮滑脚在「跑」之后的额外移动格数（书里 6 米；本仓库 1 格 = 2 米 → 3 格）。
RUN_SKATES_CELLS = 3


def climb_free(card: dict) -> bool:
    """攀爬脚：攀爬时不受 ×2 移速惩罚。"""
    return has_cyberware(card, "攀爬脚")


def jump_free(card: dict) -> bool:
    """跳跃增强：跳跃时不受 ×2 移速惩罚。"""
    return has_cyberware(card, "跳跃增强")


def swim_free(card: dict) -> bool:
    """蹼足：游泳时不受 ×2 移速惩罚（水格按 1 格算）。"""
    return has_cyberware(card, "蹼足")


def run_bonus_cells(card: dict) -> int:
    """轮滑脚：奔跑动作之后的额外移动格数（不装 = 0）。"""
    return RUN_SKATES_CELLS if has_cyberware(card, "轮滑脚") else 0


def move_multiplier(card: dict, *, mode: str) -> int:
    """这次移动方式的花费倍率（1 或 2）。

    ``mode`` ∈ ``{"normal", "climb", "jump"}``。攀爬/跳跃按规则书 ×2；
    装了对应义体（攀爬脚/跳跃增强）则降回 1。
    """
    if mode == "climb":
        return 1 if climb_free(card) else 2
    if mode == "jump":
        return 1 if jump_free(card) else 2
    return 1


def water_cost(card: dict) -> int:
    """水格花费：默认 2（游泳 ×2），装蹼足后 1。"""
    return 1 if swim_free(card) else 2


# ── 功能型义体（full.txt:28348 / :28460 / :10287） ──────────────────

#: 飞爪手抓钩射程（`:28350`「最远 30 米/码内的任何厚实的掩体」）。
GRAPPLE_RANGE_M = 30


def has_grapple(card: dict) -> bool:
    """飞爪手（`:28348-28360`）：30 米抓钩、攀绳免移速惩罚。"""
    return has_cyberware(card, "飞爪手")


def has_shoulder_cam(card: dict) -> bool:
    """肩部摄像头（`:28460-28471`）：可隐藏无需检定、拔出/收纳不花动作。"""
    return has_cyberware(card, "肩部摄像头")


def has_quick_release(card: dict) -> bool:
    """快拆挂扣（`:10287-10291`）：1 个动作拆/装赛博手臂；重装用过的臂不扣人性。

    注意：快拆挂扣本身是「赛博臂改造选项」，卸手臂时会跟着卸掉。为了让
    「先拆再装」这条流程走得通，这里认「当前装着**或**曾经装过」
    （``used_items`` 里有它也算）。
    """
    if has_cyberware(card, "快拆挂扣"):
        return True
    return "快拆挂扣" in used_arms(card)


def used_arms(card: dict) -> list[str]:
    """以前卸下过的赛博组件名字（``state["used_items"]``）。"""
    state = card.get("state") or {}
    return [str(x) for x in (state.get("used_items") or [])]


def quick_reinstall_allowed(card: dict, name: str) -> bool:
    """这次安装能不能走快拆挂扣免人性（`:10287-10291`）。

    「使用快拆挂扣重新安装你之前已经使用过的赛博臂则不会有这种情况
    （人性损失）」——所以要求：装了快拆挂扣 + 这条臂以前卸下过。
    """
    if not has_quick_release(card):
        return False
    return str(name) in used_arms(card)


def check_bonus(card: dict, *, skill: str = "", stat: str = "") -> tuple[int, list[str]]:
    """这次检定（按技能名）能拿到的义体加成合计。

    规则命中逻辑：
    * 规则给了 ``skills`` → 只有检定技能名精确匹配才生效。
    * 规则给了 ``scope:"远程"``（且带 ``miss_modes`` 的同义体若无适用场景不硬加）→
      检定技能属于远程武器技能就生效。
    * 其它（仅 stat 说明）A 批不生效——检定型只认 skills/scope。

    返回 ``(加成合计, 来源说明列表)``，没命中就是 ``(0, [])``。
    """
    sk = (skill or "").strip()
    total = 0
    notes: list[str] = []
    seen: set[str] = set()          # 同一件义体只算一次
    for zh, rule in installed_with_rule(card):
        if not rule or zh in seen:
            continue
        bonus = int(rule.get("bonus") or 0)
        if not bonus:
            continue
        # 只认 skills 或 scope；命中类（miss_modes）A 批不做，避免误加。
        if rule.get("miss_modes") and not rule.get("skills"):
            continue
        if rule.get("skills"):
            if sk in [str(s) for s in rule["skills"]]:
                total += bonus
                notes.append(f"{zh}{bonus:+d}")
                seen.add(zh)
        elif rule.get("scope") == "远程":
            if sk in RANGED_SKILLS:
                total += bonus
                notes.append(f"{zh}{bonus:+d}")
                seen.add(zh)
    # F 类组合触发（荧光纹身×3 / 化学皮肤+科技炫发），与单件加成合并。
    c_total, c_notes = combo_check_bonus(card, skill=sk)
    total += c_total
    notes.extend(c_notes)
    # 技能芯片：绑定技能的等级下限 3（full.txt:27830-27837）。
    chip_total, chip_notes = skill_chip_bonus(card, skill=sk)
    total += chip_total
    notes.extend(chip_notes)
    return total, notes


def describe(card: dict) -> list[str]:
    """列出这张卡上**生效**的义体加成（给 /义体 展示）。"""
    lines: list[str] = []
    for zh, rule in installed_with_rule(card):
        if not rule:
            # 常数表里的（不写在 cyberware_bonus.json）也要展示，否则装了
            # 克伦齐科夫却看不到任何说明。
            spec = INITIATIVE_BONUS.get(zh)
            if spec:
                lines.append(f"{zh} 先攻{int(spec):+d}")
            continue
        if int(rule.get("bonus") or 0):
            where = (
                "、".join(str(s) for s in rule.get("skills") or [])
                or (rule.get("scope") == "远程" and "远程攻击" or "")
            )
            if not where and rule.get("stat"):
                where = rule.get("stat", "") + "属性"
            if not where:
                where = rule.get("miss_modes") and "命中修正" or ""
            lines.append(f"{zh} {int(rule['bonus']):+d}"
                         + (f"（{where}）" if where else ""))
        elif rule.get("sp"):
            lines.append(f"{zh} SP{int(rule['sp'])}（护甲）")
        elif rule.get("rest_mult"):
            lines.append(f"{zh}（休整恢复×{int(rule['rest_mult'])}）")
        elif isinstance(rule.get("set_stat"), dict):
            spec = "、".join(f"{k}→{int(v)}" for k, v in rule["set_stat"].items())
            lines.append(f"{zh}（{spec}，属性下限）")
        elif zh in INITIATIVE_BONUS:
            lines.append(f"{zh} 先攻{int(INITIATIVE_BONUS[zh]):+d}")
    # F 类组合触发（荧光纹身×3 / 化学皮肤+科技炫发）
    for combo in active_combos(card):
        sk = "、".join(str(s) for s in (combo.get("skills") or []))
        lines.append(f"{combo.get('id', '组合')} {int(combo.get('bonus') or 0):+d}"
                     + (f"（{sk}）" if sk else ""))
    # 技能芯片：绑定技能等级下限 3（full.txt:27830-27837）
    bound = [str(x) for x in ((card.get("state") or {}).get("skill_chips") or [])]
    for sk in bound:
        base = _card_skill_level(card, sk)
        if base < 3:
            lines.append(f"技能芯片（{sk} 等级{base}→3，{3 - base:+d}）")
        else:
            lines.append(f"技能芯片（{sk} 已 {base} 级，无效）")
    # 移动型义体（full.txt:10382/10388/10409/10395）
    if climb_free(card):
        lines.append("攀爬脚（攀爬移速惩罚免除）")
    if jump_free(card):
        lines.append("跳跃增强（跳跃移速惩罚免除）")
    if swim_free(card):
        lines.append("蹼足（游泳移速惩罚免除，水格按 1 格）")
    run_bonus = run_bonus_cells(card)
    if run_bonus:
        lines.append(f"轮滑脚（奔跑时移动 +{run_bonus * 2} 米）")
    # 功能型（full.txt:28348 / :28460 / :10287）
    if has_grapple(card):
        lines.append(f"飞爪手（{GRAPPLE_RANGE_M} 米抓钩；攀绳免移速惩罚）")
    if has_shoulder_cam(card):
        lines.append("肩部摄像头（可隐藏无需检定，收纳/放出不占动作；纯剧情）")
    if has_quick_release(card):
        lines.append("快拆挂扣（1 动作拆装赛博臂；重装用过的臂不扣人性）")
    return lines


def attribute_bonus(card: dict) -> dict[str, int]:
    """永久**属性型**义体加成：按已装义体返回 ``{属性键: 加值}``。

    例：骨肉织绕 体格+2 → ``{"BODY": 2}``。运行时合成进
    ``cyberpunk.characters.Character.effective_stat``，驱动 HP/重伤阈值/死亡豁免，
    但**不写死** ``state`` 里的基础 ``stats``（避免 ``validate_stats`` 越界）。

    同一件义体只算一次（``full.txt:9698-9699``「多次安装相同赛博组件的增益
    不会叠加」）；但**骨肉织绕自己写了「重复改造将叠加此效果」**（``:10109-10110``），
    属于「另有说明」，所以它在表里带 ``stacks: true``，允许按份数累加。
    """
    out: dict[str, int] = {}
    seen: set[str] = set()
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        k = rule.get("stat")
        if not (k and int(rule.get("bonus") or 0)):
            continue
        if zh in seen and not rule.get("stacks"):
            continue
        out[k] = out.get(k, 0) + int(rule["bonus"])
        if not rule.get("stacks"):
            seen.add(zh)
    return out


def stat_floor(card: dict) -> dict[str, int]:
    """**属性下限**型义体：把某属性**抬到**指定值（如线性框架 体格→12）。

    与 ``attribute_bonus``（加值）不同：这是「至少到 X」，运行时取
    ``max(基础+加成, 下限)``。线性框架允许 BODY 超过常规上限 10（12/14）。
    """
    out: dict[str, int] = {}
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        spec = rule.get("set_stat")
        if not isinstance(spec, dict):
            continue
        for k, v in spec.items():
            out[str(k)] = max(out.get(str(k), 0), int(v))
    return out


def armor_sp(card: dict) -> int:
    """防御端**护甲 SP** 义体：返回装了的义体带来的护甲值（取最高）。

    RED：编织皮肤 躯干+头部 SP7（`:10298-10302`）、皮下护甲 躯干+头部 SP11
    （`:10305-10308`），均**不与穿着护甲叠加**、可被削减。本引擎只跟踪一档全局
    SP（不分部位），故按「与穿着护甲**取高**」处理，见 combat.start。标「约定」。
    """
    best = 0
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        sp = int(rule.get("sp") or 0)
        if sp > best:
            best = sp
    return best


def rest_multiplier(card: dict) -> int:
    """休整恢复倍率：装「抗体增强」则每休息日恢复体格×2（`full.txt:10220-10223`）。

    默认（无恢复型义体）每日恢复 体格 点 HP；装抗体增强翻倍为 体格×2。
    """
    mult = 1
    for zh, rule in installed_with_rule(card):
        if not rule:
            continue
        m = int(rule.get("rest_mult") or 0)
        if m > mult:
            mult = m
    return mult


#: 命中修正型义体（B 批）：装了就按条件在战斗命中里加值。
#: RED 规则书（rules/red_core/raw/full.txt）：
#: * 瞄准目镜 `:28330-28331`：瞄准射击时命中 +1，需赛博义眼。
#: * 远视目镜 `:28337-28340`：单发或瞄准射击、目标 ≥51 米时命中 +1，不叠加狙击瞄准镜。
HIT_BONUS = {
    "瞄准目镜": {"aim_only": True, "min_m": None, "note": "瞄准+1（:28330）"},
    "远视目镜": {"aim_only": False, "min_m": 51, "note": "远距≥51米+1（:28339）"},
}

#: 先攻修正型义体（D 批）：装了就永久加先攻。
#: * 克伦齐科夫 `:9802-9805`：加速组件，先攻 +2；**只能在身上装其中一种加速组件**。
#: * 桑德维斯坦 `:9807-9812`：要花一个动作激活、先攻 +3 持续 1 分钟、冷却 1 小时，
#:   属于「动作激活」型，不在这里常数加值（见 combat.activate_cyberware）。
#: 「约定」：两件加速组件互斥，同时装了只取克伦齐科夫那 +2（书里不让两件都装）。
INITIATIVE_BONUS = {"克伦齐科夫": 2}


def initiative_bonus(card: dict) -> int:
    """先攻常数加值（克伦齐科夫 +2）。桑德维斯坦的 +3 要走激活通道，不算进来。"""
    zhs = set(installed_names(card))
    total = 0
    for zh, value in INITIATIVE_BONUS.items():
        if zh in zhs:
            total += value
    return total


def hit_bonus(card: dict, *, aiming: bool = False,
              meters: int | None = None) -> tuple[int, list[str]]:
    """战斗命中修正：按已装义体 + 本次攻击条件（是否瞄准 / 目标距离）。

    命中检定里加进去（combat.py 攻击）。返回 ``(加成合计, 来源说明)``。
    """
    zhs = set(installed_names(card))
    total = 0
    notes: list[str] = []
    for zh, spec in HIT_BONUS.items():
        if zh not in zhs:
            continue
        if spec["aim_only"]:
            if not aiming:
                continue
        if spec["min_m"] is not None:
            if meters is None or meters < spec["min_m"]:
                continue
        total += 1
        notes.append(f"{zh}+1")
    return total, notes