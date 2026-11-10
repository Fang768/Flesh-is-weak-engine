"""赛博组件插槽与前置：容量、占槽、前置链、互斥。

数据源 ``rules/red_core/data/cyberware_slots.json``（``full.txt:9698-10500``）。

规则要点（逐条对过原文）：

* **基础组件才有插槽**（``:9700-9702``）：赛博义眼 3 槽（``:9866``）、
  神经链路 5 槽（``:9781``）、赛博音频套装 3 槽（``:9982``）、
  赛博手臂 4 槽（``:10212``）、赛博腿 3 槽（``:10366``）、芯片插槽 1 槽（``:9792``）。
* **时尚装置 / 体内 / 外周各 7 槽，且三类互相独立**（``:9703-9704`` +
  FAQ ``:532-534``「你可以有七个时尚装置和七个体内殖装，其他同理」）。
* **配件必须挂到对应基础组件下**（``:9701-9702``）：没有赛博义眼就装不了
  图像增强；没有神经链路就装不了芯片插槽。
* **加速组件互斥**（``:9804-9805``、``:9811``）：克伦齐科夫/桑德维斯坦只能装一种。
* **强制双眼安装**（``:9873``、``:9902``、``:9926``、``:9967``、``:9997``）：
  要装两次，各占每只眼的槽，人性损失也是两次。

「约定」（规则书没写死，本仓库口径）：

* 玩家只有**一只**赛博义眼时（``state.cyber`` 里「赛博义眼」出现 1 次），
  强制双眼的配件直接拒绝，提示「要两只眼都赛博化」——比按两只眼记账简单，
  也不会出现「一只眼装了紫外」这种半吊子状态。
* ``state.cyber`` 是扁平的名字列表，所以同名义体装两次就是两项。
  「赛博义眼」装两次 = 两只眼；基础组件按出现次数算容量。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = (Path(__file__).resolve().parents[2]
             / "rules" / "red_core" / "data" / "cyberware_slots.json")

DEFAULT_SLOTS = 1


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA_FILE.exists():
        return {"bases": {}, "categories": {}, "items": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def bases() -> dict[str, dict]:
    return dict(_data().get("bases", {}))


def categories() -> dict[str, dict]:
    return dict(_data().get("categories", {}))


def item_rule(name: str) -> dict:
    """某件义体的槽位/前置规则（没有就空字典 = 占 1 槽、无前置）。"""
    return dict(_data().get("items", {}).get(str(name), {}))


def slots_of(name: str) -> int:
    """这件义体占几个槽（缺省 1）。"""
    return int(item_rule(name).get("slots", DEFAULT_SLOTS))


def is_base(name: str) -> bool:
    return bool(item_rule(name).get("base")) or str(name) in bases()


def base_capacity(name: str) -> int:
    """基础组件有几槽；不是基础组件返回 0。"""
    return int(bases().get(str(name), {}).get("capacity", 0))


def category_of(name: str, section: str = "") -> str:
    """义体属于哪个分类（优先用 HL 表的 section，兜底从槽位表反查）。"""
    if section:
        return str(section)
    for cat, meta in categories().items():
        if str(name) in (meta.get("includes") or ()):
            return cat
    return ""


def category_capacity(category: str) -> int:
    return int(categories().get(category, {}).get("capacity", 0))


def category_base(category: str) -> str:
    return str(categories().get(category, {}).get("base") or "")


def requires(name: str) -> list[str]:
    """前置组件（必须全部具备）。"""
    raw = item_rule(name).get("requires") or []
    return [str(x) for x in raw]


def requires_any(name: str) -> list[str]:
    """前置组件（具备任意一个即可）。"""
    raw = item_rule(name).get("requires_any") or []
    return [str(x) for x in raw]


def exclusive_group(name: str) -> str:
    return str(item_rule(name).get("exclusive_group") or "")


def needs_both_eyes(name: str) -> bool:
    return bool(item_rule(name).get("both_eyes"))


def free_with(name: str) -> str:
    """随基础组件自带、不占槽（标准手/标准脚）。"""
    return str(item_rule(name).get("free_with") or "")


def capacity_bonus(name: str) -> dict:
    """扩容型义体：这件装上后给哪个基础组件加容量。

    返回 ``{"base": "赛博义眼", "eyes": 5}`` 或 ``{"base": "...", "slots": 5}``。
    非扩容件返回空字典。

    * **复眼改造** ``:10473-10478``：「最多能够再加装 5 个额外的赛博义眼」
      → 给的是**能装几只眼**的额度（``eyes``），不是每只眼的槽。
    * **传感器阵列** ``:10479-10485``：「最多再获得 5 个额外的赛博音频插槽，
      此改造不占用它原有的插槽」→ 给的是**插槽数**（``slots``）。
    """
    raw = item_rule(name).get("capacity_bonus")
    return dict(raw) if isinstance(raw, dict) else {}


def _capacity_table(installed) -> tuple[dict[str, int], dict[str, int]]:
    """算上扩容后的 (每件容量, 最多能装几件)。

    默认每件容量取 ``bases`` 表的 ``capacity``；
    「最多能装几件」只对**成对**的基础组件（赛博义眼默认 2 只）有意义，
    复眼改造 +5 后放宽到 7 只。``installed`` 可以是名字列表或记录列表。
    """
    caps = {base: int(meta.get("capacity") or 0) for base, meta in bases().items()}
    # 成对/成组的基础组件默认件数：义眼 2 只、手臂 2 条、腿 2 条。
    max_count = {"赛博义眼": 2, "赛博手臂": 2, "赛博腿": 2}
    for name in names_of(installed):
        bonus = capacity_bonus(name)
        base = str(bonus.get("base") or "")
        if not base:
            continue
        if "eyes" in bonus:
            max_count[base] = max_count.get(base, 2) + int(bonus["eyes"])
        if "counts" in bonus:
            # 人造肩膀改造：能在原双臂之下再装 2 条赛博手臂（:28621-28626）
            max_count[base] = max_count.get(base, 2) + int(bonus["counts"])
        if "slots" in bonus:
            caps[base] = caps.get(base, 0) + int(bonus["slots"])
    return caps, max_count


def max_instances(base: str, installed) -> int:
    """这个基础组件最多能装几件（义眼默认 2 只，复眼改造放宽到 7）。"""
    if base not in bases():
        return 99
    _, max_count = _capacity_table(installed)
    return int(max_count.get(base, 99))


def effective_capacity(base: str, installed) -> int:
    """某个基础组件的有效**单件**容量（含扩容，如传感器阵列 +5 音频槽）。"""
    if base not in bases():
        return 0
    caps, _ = _capacity_table(installed)
    return int(caps.get(base, base_capacity(base)))


# ── 记账 ──────────────────────────────────────────────────────


def _count(items: list[str], name: str) -> int:
    return sum(1 for x in items if x == name)


# ── 实例记录（instance model）─────────────────────────────────
#
# 旧版把义体存成扁平名字列表 ``["赛博义眼","赛博义眼","图像增强"]``，
# 分不清配件挂在哪只眼/哪条臂上，手腿还会互相误挂。现在改成**实例记录**：
#
#     {"id": "c1", "name": "赛博义眼", "parent": ""}
#     {"id": "c2", "name": "赛博义眼", "parent": ""}
#     {"id": "c3", "name": "图像增强", "parent": "c1"}
#     {"id": "c4", "name": "显微目镜", "parent": "c2"}
#
# ``parent`` 指向基础组件实例的 ``id``；基础组件自己 parent 为空串。
# 容量按**每个基础实例**单独算。读旧格式（字符串列表）时自动迁移。


def names_of(records) -> list[str]:
    """记录列表 → 名字列表（所有旧接口靠它兼容）。"""
    out: list[str] = []
    for r in records or []:
        if isinstance(r, dict):
            nm = str(r.get("name") or "")
            if nm:
                out.append(nm)
        elif r:
            out.append(str(r))
    return out


def new_id(records) -> str:
    """给新装的一件发一个不冲突的实例 id。"""
    used = {str(r.get("id") or "") for r in records if isinstance(r, dict)}
    seq = 0
    while True:
        seq += 1
        cand = f"c{seq}"
        if cand not in used:
            return cand


def by_id(records, rid: str) -> dict | None:
    return next((r for r in records if isinstance(r, dict)
                 and r.get("id") == rid), None)


def _used_on(records: list[dict], base_id: str) -> int:
    """挂在某个基础**实例**下的已占槽（随基础自带的标准手/脚不计）。"""
    total = 0
    for r in records:
        if r.get("parent") != base_id:
            continue
        if item_rule(str(r.get("name") or "")).get("free_with"):
            continue
        total += slots_of(str(r.get("name") or ""))
    return total


def instance_free(records: list[dict], base_id: str) -> int:
    """某个基础实例还能装几槽。"""
    rec = by_id(records, base_id)
    if rec is None:
        return 0
    base = str(rec.get("name") or "")
    return max(0, effective_capacity(base, names_of(records))
               - _used_on(records, base_id))


def normalize(installed) -> list[dict]:
    """把「字符串列表」或「记录列表」统一成带 id 与 parent 的记录。

    旧卡（纯名字）会在这里被迁移：先发 id，再按分类规则给配件找父实例。
    """
    raw = list(installed or [])
    out: list[dict] = []
    for x in raw:
        if isinstance(x, dict):
            out.append({"id": str(x.get("id") or ""),
                        "name": str(x.get("name") or ""),
                        "parent": str(x.get("parent") or "")})
        elif x:
            out.append({"id": "", "name": str(x), "parent": ""})
    used_ids: set[str] = set()
    seq = 0
    for rec in out:
        if not rec["id"] or rec["id"] in used_ids:
            while True:
                seq += 1
                cand = f"c{seq}"
                if cand not in used_ids:
                    rec["id"] = cand
                    break
        used_ids.add(rec["id"])
    # 推断 parent：只给「需要基础组件」的配件找父实例
    for rec in out:
        if rec["parent"]:
            continue
        name = rec["name"]
        if name in bases() or item_rule(name).get("base"):
            continue                      # 基础组件自己
        if capacity_bonus(name) or item_rule(name).get("free_with"):
            continue                      # 扩容件 / 随基础自带
        parent_type = _parent_base(name, names_of(out))
        if not parent_type:
            continue
        need = slots_of(name)
        pick = ""
        for r in out:
            if r is rec or r["name"] != parent_type:
                continue
            if instance_free(out, r["id"]) >= need:
                pick = r["id"]
                break
        if not pick:
            pick = next((r["id"] for r in out
                         if r is not rec and r["name"] == parent_type), "")
        rec["parent"] = pick
    return out


def base_instances(installed) -> dict[str, int]:
    """已装的基础组件 → 个数（赛博义眼装两次 = 2 只）。"""
    out: dict[str, int] = {}
    for name in names_of(normalize(installed)):
        if name in bases():
            out[name] = out.get(name, 0) + 1
    return out


def used_slots(installed, base: str) -> int:
    """挂在某个**基础类型**下的已占槽合计（各实例相加）。"""
    records = normalize(installed)
    total = 0
    for r in records:
        pid = r.get("parent")
        if not pid:
            continue
        parent = by_id(records, pid)
        if parent is None or parent.get("name") != base:
            continue
        if item_rule(str(r.get("name") or "")).get("free_with"):
            continue
        total += slots_of(str(r.get("name") or ""))
    return total


def capacity_left(installed, base: str) -> int:
    """某个基础组件**类型**还剩几槽（各实例合计）。

    单件容量走 ``effective_capacity``，这样传感器阵列（+5 音频槽）能生效。
    需要精确到具体某个实例时用 ``instance_free(records, base_id)``。
    """
    records = normalize(installed)
    names = names_of(records)
    count = _count(names, base)
    if count <= 0:
        return 0
    total = effective_capacity(base, names) * count
    return max(0, total - used_slots(records, base))


def category_left(installed, category: str) -> int:
    """无基础组件的分类（时尚/体内/外周）还剩几槽。"""
    cap = category_capacity(category)
    if not cap:
        return 0
    names = names_of(normalize(installed))
    used = sum(slots_of(x) for x in names
               if category_of_installed(x) == category)
    return max(0, cap - used)


#: 分类查表（由 HL 表提供 section，这里缓存一次）
@lru_cache(maxsize=1)
def _section_map() -> dict[str, str]:
    from . import cyber

    return {name: str(meta.get("section") or "")
            for name, meta in cyber.hl_table().items()}


def category_of_installed(name: str) -> str:
    """已装义体所属分类（按 HL 表 section）。"""
    return _section_map().get(str(name), "")


# ── 校验 ──────────────────────────────────────────────────────


class SlotError(ValueError):
    """槽位/前置不满足。消息直接给玩家看。"""


def plan_install(installed, name: str, *, parent_id: str = "") -> tuple[str, str]:
    """装 ``name`` 的完整校验 + 选父实例。

    返回 ``(错误消息, 父实例 id)``：错误为空表示可装；``父实例 id`` 是这件
    配件应挂到的那个**基础实例**（基础组件自己/无父级的返回空串）。
    ``parent_id`` 可显式指定（多只眼/多条臂时挑具体那一件）。
    """
    records = normalize(installed)
    names = names_of(records)
    section = category_of_installed(name)
    rule = item_rule(name)

    # ① 已经是基础组件 → 先查它自己的前置与互斥，再放行
    if rule.get("base") or name in bases():
        missing = [req for req in requires(name) if _count(names, req) <= 0]
        if missing:
            return (f"装 {name} 之前要先有「{missing[0]}」"
                    f"（规则书：这是它的先决条件）。"), ""
        group = exclusive_group(name)
        if group and _has_group(names, group):
            return (f"{name} 和 {_first_in_group(names, group)} "
                    f"属于同一类，身上只能装一种。"), ""
        # ①b 件数上限：义眼默认 2 只、手臂/腿默认 2 条
        limit = max_instances(name, names)
        have = _count(names, name)
        if have >= limit:
            hint = ""
            if name == "赛博义眼":
                hint = "（复眼改造可以再放宽 5 只义眼，:10473-10478）"
            elif name == "赛博手臂":
                hint = "（人造肩膀改造可以再放宽 2 条手臂，:28621-28626）"
            return (f"{name} 最多装 {limit} 件，你已经装了 {have} 件{hint}。"), ""
        return "", ""

    # ② 前置组件（必须全部具备）
    missing = [req for req in requires(name) if _count(names, req) <= 0]
    if missing:
        return (f"装 {name} 之前要先有「{missing[0]}」"
                f"（规则书：这是它的先决条件）。"), ""

    # ③ 前置组件（任意一个即可）
    any_list = requires_any(name)
    if any_list and not any(_count(names, x) > 0 for x in any_list):
        return (f"装 {name} 需要先有「{' 或 '.join(any_list)}」中的至少一件。"), ""

    # ④ 互斥
    group = exclusive_group(name)
    if group and _has_group(names, group, exclude=name):
        return (f"{name} 和 {_first_in_group(names, group)} "
                f"属于同一类加速组件，身上只能装一种。"), ""

    # ⑤ 强制双眼
    if needs_both_eyes(name):
        eyes = _count(names, "赛博义眼")
        if eyes < 2:
            return (f"{name} 必须两只眼睛都赛博化才能装"
                    f"（规则书：双眼都必须安装）。你目前有 {eyes} 只。"), ""

    # ⑥ 槽位。父级基础组件没装时，先报「缺基础组件」而不是「0/0 槽」。
    #
    # 扩容件（复眼改造/传感器阵列）例外：它们**本身就是给基础组件扩容的**，
    # 书里只要求「先有义眼/音频套装」，不吃分类父级的槽位限制。
    if capacity_bonus(name):
        return "", ""

    parent_type = _parent_base(name, names)
    if parent_type:
        candidates = [r for r in records if r["name"] == parent_type]
        if parent_id:
            chosen = by_id(records, parent_id)
            if chosen is None or chosen.get("name") != parent_type:
                return (f"指定的那件不是「{parent_type}」，装不了 {name}。"), ""
            candidates = [chosen]
        if not candidates:
            return (f"装 {name} 之前要先有「{parent_type}」"
                    f"（规则书：这是它的先决条件）。"), ""
        need = slots_of(name)
        for r in candidates:
            if instance_free(records, r["id"]) >= need:
                return "", str(r["id"])
        left = max((instance_free(records, r["id"]) for r in candidates), default=0)
        have = effective_capacity(parent_type, names)
        return (f"{parent_type}的插槽不够：{name} 要 {need} 槽，"
                f"当前单件最多剩 {left} 槽（每件共 {have} 槽）。"
                f"先卸掉一件再装。"), ""

    # ⑦ 无基础组件的三类：各自 7 槽
    cap = category_capacity(section)
    if cap:
        left = category_left(records, section)
        need = slots_of(name)
        if need > left:
            return (f"{section}插槽不够：{name} 要 {need} 槽，当前剩 {left} 槽"
                    f"（共 {cap} 槽）。先卸掉一件再装。"), ""
    return "", ""


def check_install(installed, name: str, *, parent_id: str = "") -> str:
    """兼容旧接口：只返回错误消息（空串 = 可装）。"""
    return plan_install(installed, name, parent_id=parent_id)[0]


def _has_group(installed: list[str], group: str, *, exclude: str = "") -> bool:
    for name in installed:
        if name == exclude:
            continue
        if exclusive_group(name) == group:
            return True
    return False


def _first_in_group(installed: list[str], group: str) -> str:
    for name in installed:
        if exclusive_group(name) == group:
            return name
    return ""


def _parent_base(name: str, installed: list[str]) -> str:
    """这条配件该挂到哪个基础组件下；没有则返回空串。

    优先级：
    1. ``requires`` 里显式写了基础组件（芯片插槽/皮下握把 → 神经链路）；
    2. 分类只有**唯一** base（赛博光学 → 赛博义眼，音频 → 赛博音频套装）；
    3. 分类有**多个** base（赛博义肢 = 手臂 或 腿）→ 取已装的那个；
    4. ``requires_any`` 里已装的基础组件。
    """
    for req in requires(name):
        if req in bases():
            return req
    # 明确「不挂任何基础组件」的（线性框架这类骨架件）
    if item_rule(name).get("no_parent"):
        return ""
    # 随基础自带的标准手/脚：按 free_with 认父，别被多 base 兜底挂错
    fw = free_with(name)
    if fw:
        return fw
    section = category_of_installed(name)
    base = category_base(section)
    if base:
        return base
    cat = categories().get(section) or {}
    multi = list(cat.get("bases") or ())
    # ③a ``requires_any`` 里点名了基础类型（手臂件/腿件）→ 优先按它认，
    # 否则「只有腿」时工具手会被错挂到腿上（旧 bug）。
    any_bases = [x for x in requires_any(name) if x in bases()]
    if any_bases:
        installed_any = [x for x in any_bases if _count(installed, x) > 0]
        if installed_any:
            return installed_any[0]
        return any_bases[0]
    for candidate in multi:
        if _count(installed, candidate) > 0:
            return candidate
    if multi:
        # 一件都没装：报第一件作为「该装什么」，别让配件凭空装上
        return multi[0]
    return ""


def describe_slots(installed) -> list[str]:
    """给 /义体 面板：列出各基础组件**实例**与三类独立槽的占用情况。"""
    records = normalize(installed)
    names = names_of(records)
    lines: list[str] = []
    for base in bases():
        insts = [r for r in records if r["name"] == base]
        if not insts:
            continue
        cap = effective_capacity(base, names)      # 含扩容
        if len(insts) == 1:
            used = _used_on(records, insts[0]["id"])
            lines.append(f"{base}×1：{used}/{cap} 槽")
        else:
            # 同类型多实例：逐件列出，方便分辨「哪只眼/哪条臂」
            for i, r in enumerate(insts, 1):
                used = _used_on(records, r["id"])
                lines.append(f"{base}#{i}：{used}/{cap} 槽")
            lines.append(f"{base}×{len(insts)}：合计 "
                         f"{_used_on_total(records, insts)}/{cap * len(insts)} 槽")
    # 扩容件单独提一句，否则面板看不到「为什么槽变多了」
    for name in names:
        bonus = capacity_bonus(name)
        if not bonus:
            continue
        if "eyes" in bonus:
            lines.append(f"{name}：{bonus.get('base')} 可装 "
                         f"{max_instances(str(bonus['base']), names)} 只")
    for cat, meta in categories().items():
        cap = category_capacity(cat)
        if not cap:
            continue
        used = sum(slots_of(x) for x in names if category_of_installed(x) == cat)
        if used:
            lines.append(f"{cat}：{used}/{cap} 槽")
    return lines


def _used_on_total(records: list[dict], insts: list[dict]) -> int:
    return sum(_used_on(records, r["id"]) for r in insts)


def uninstall_blockers(installed, name: str) -> str:
    """卸 ``name`` 之前检查有没有别的义体以它为前置。返回理由或空串。"""
    records = normalize(installed)
    names = names_of(records)
    dependents = [x for x in names
                  if x != name and name in requires(x)]
    if dependents:
        return (f"还有 {('、'.join(dict.fromkeys(dependents)))} 挂在 {name} 上，"
                f"先把它们卸掉。")
    # 基础组件被卸 → 挂在它**这个实例**上的配件都失去依托
    # （快拆挂扣是「插座」性质，拆臂时它留在身上，不拦）
    for rec in records:
        if rec["name"] != name:
            continue
        orphans = [r["name"] for r in records
                   if r.get("parent") == rec["id"] and r["name"] != "快拆挂扣"]
        if orphans:
            return (f"还有 {('、'.join(dict.fromkeys(orphans)))} 装在 {name} 上，"
                    f"先把它们卸掉。")
    return ""
