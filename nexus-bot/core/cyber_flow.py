"""赛博组件安装：扣人性（HL）→ 共情（EMP）下降。

规则依据（``rules/red_core/raw/full.txt``）：

* ``:9834-9835``「HL（人性损失）: 这个装置对你的心理影响有多严重。在角色生成中，
  人性损失是预设的。在角色生成以后，人性点数损失由预设数字后括号中的骰子决定。」
  → 建卡时扣固定值，**游戏里加装就投那颗骰子**。
* ``:6970``「当你的角色正因为安装了赛博改造经历人性损失…他就失去了人性值。」
* ``:9997``「（此情况需购买及安装两次，人性损失也是两次）」→ 同一件装两次就扣两次。
* ``:9995-9997`` 赛博义眼「每颗人工眼球拥有 3 个插槽」→ 一件 = 一次安装，不做插槽记账
  （「约定」：本仓库不跟踪插槽，见 docs/进度.md）。

人性值 = EMP × 10（``cyberpunk/stats.py:92``）；每掉满 10 点人性，EMP −1（最低 1），
由 ``cyberpunk/characters.py:202 Character.lose_humanity`` 实现——这里复用它，
所以两套卡（核心书卡 / 简易卡）的结算口径完全一致。
"""

from __future__ import annotations

import re
import sqlite3

from cyberpunk import cyber, dice, slots, weapon_catalog

from core import easy_roster, growth, roster

#: 触发安装的词（放在 `core/turn_flow.py` 的建卡/战斗分支之外，作为系统指令）
INSTALL_WORDS = ("安装", "装义体", "装赛博", "植入", "换上义体")

#: 明确说「想看看」的，不执行安装，只报价格与 HL
QUOTE_WORDS = ("查", "看", "多少钱", "价格", "多少人性")

_INSTALL_RE = re.compile(
    r"^(?:/?(?:安装|装义体|装赛博|植入|换上义体))\s*[：:]?\s*(.+)$"
)

#: 卸载：规则书 :535-538 把「卸除」当独立流程讲，所以做成单独入口
_UNINSTALL_RE = re.compile(
    r"^(?:/?(?:卸载|卸下|拆下|拆除|取出))\s*[：:]?\s*(.+)$"
)

#: 人性归零的口径（规则书没写死，标「约定」）
CYBERPSYCHOSIS_NOTE = "人性归零（赛博精神病风险，本引擎只提示不夺卡）"


def looks_like_install(text: str) -> bool:
    body = (text or "").strip()
    return bool(_INSTALL_RE.match(body))


def looks_like_uninstall(text: str) -> bool:
    body = (text or "").strip()
    return bool(_UNINSTALL_RE.match(body))


def _card_of(conn: sqlite3.Connection, group_id: str, user_id: str):
    """返回 (卡, 是不是简易卡)；没卡返回 (None, False)。"""
    card = easy_roster.get(conn, group_id, user_id)
    if card is not None:
        return card, True
    return roster.get(conn, group_id, user_id), False


def _emp_of(card: dict, easy: bool) -> int:
    if easy:
        return int((card.get("stats") or {}).get("EMP") or 0)
    return int((card.get("stats") or {}).get("EMP") or 0)


def humanity_of(card: dict, easy: bool) -> int:
    """当前人性。

    ``state["hum"]`` 有值就用它（已经扣过改造损失）；否则按规则取 ``EMP × 10``
    作为「还没装过」的满值（``cyberpunk/stats.py:92``）。
    """
    stored = (card.get("state") or {}).get("hum")
    if stored is not None:
        return max(0, int(stored))
    return max(0, _emp_of(card, easy) * 10)


def _write_humanity(conn: sqlite3.Connection, group_id: str, user_id: str,
                    card: dict, easy: bool, hum: int) -> bool:
    """把新人性写回卡：state["hum"] + 必要时同步 EMP。返回是否写成功。"""
    emp = max(1, hum // 10)
    stats = dict(card.get("stats") or {})
    stats["EMP"] = emp
    state = dict(card.get("state") or {})
    state["hum"] = hum

    if easy:
        card["stats"] = stats
        card["state"] = state
        return easy_roster.save(conn, group_id, user_id, card, card.get("name"))
    # 核心书卡：stats 要单独回写（roster.save 会整卡覆盖，所以先把 stats 改好）
    card["stats"] = stats
    card["state"] = state
    roster.save(conn, group_id, user_id, card)
    return True


def _cyber_list(card: dict) -> list[dict]:
    """卡上已装的义体**实例记录**（旧字符串列表会自动迁移成记录）。

    RED 卡存在 ``state.cyber``，简易卡（预设模板）存在**顶层** ``cyber``——
    两处都要读，否则简易卡的义体列表永远是空的（曾经真的这样错过：
    赤尾装了 5 件，``/义体`` 面板却显示「还没装过」）。
    """
    from cyberpunk import cyber_bonus

    return cyber_bonus.installed_records(card)


def _write_cyber(card: dict, records: list[dict]) -> None:
    """把义体**实例记录**写回卡上**正确的存储位置**。

    简易卡（预设模板）把义体放**顶层** ``cyber``，RED 卡放 ``state.cyber``。
    两处都写会读重复，所以按卡的类型择一：
    顶层已有 ``cyber`` 键（简易卡）→ 写顶层；否则（RED 卡）→ 写 ``state.cyber``。
    """
    clean = [{"id": str(r.get("id") or ""),
              "name": str(r.get("name") or ""),
              "parent": str(r.get("parent") or "")}
             for r in records]
    if "cyber" in card and card.get("cyber") is not None:
        card["cyber"] = clean
        return
    state = dict(card.get("state") or {})
    state["cyber"] = clean
    card["state"] = state


def _remember(conn: sqlite3.Connection, group_id: str, user_id: str,
              card: dict, easy: bool, item: str, hum: int,
              *, parent_id: str = "", record_id: str = "") -> str:
    """把新装的一件记进卡，返回它的实例 id。

    ``record_id`` 用于「从快拆槽取回自己的臂」——复用原来的实例 id，
    这样快拆挂扣的 ID 记录才能认出「还是同一条臂」。
    """
    records = _cyber_list(card)
    rid = str(record_id or "") or slots.new_id(records)
    records.append({"id": rid, "name": item,
                    "parent": str(parent_id or "")})
    _write_cyber(card, records[:24])
    state = dict(card.get("state") or {})
    state["hum"] = hum
    card["state"] = state
    if easy:
        easy_roster.save(conn, group_id, user_id, card, card.get("name"))
    else:
        roster.save(conn, group_id, user_id, card)
    return rid


def _mark_used(card: dict, name: str) -> None:
    """记「这件以前装过」（重装豁免用：芯片/武器快速更换、快拆挂扣重装臂）。"""
    state = dict(card.get("state") or {})
    used = list(state.get("used_items") or [])
    if str(name) not in [str(x) for x in used]:
        used.append(str(name))
    state["used_items"] = used
    card["state"] = state


def _qr_entries(card: dict) -> list[dict]:
    """快拆槽条目统一成 ``{name, id}``。

    ``id`` 是这条臂的**实例 id**（与 ``state.cyber`` 里的记录 id 一致），
    换臂时靠它判断「这条臂以前上过身没有」。旧格式（纯字符串 / 无 id）
    保留为空 id，按「用过的」处理（旧卡迁移）。
    """
    raw = (card.get("state") or {}).get("quick_slots") or []
    out: list[dict] = []
    for x in raw:
        if isinstance(x, dict):
            out.append({"name": str(x.get("name") or ""),
                        "id": str(x.get("id") or "")})
        elif x:
            out.append({"name": str(x), "id": ""})
    return out


def _quick_ids(card: dict) -> list[str]:
    """快拆挂扣记录过的「上过身的臂 id」清单（主人要的 ID 机制）。"""
    return [str(x) for x in ((card.get("state") or {}).get("quick_ids") or [])]


def _mark_quick_id(card: dict, arm_id: str) -> None:
    """把一条臂的 id 记进快拆挂扣的清单（之后重装它就不扣人性）。"""
    if not arm_id:
        return
    state = dict(card.get("state") or {})
    ids = list(state.get("quick_ids") or [])
    if str(arm_id) not in [str(x) for x in ids]:
        ids.append(str(arm_id))
    state["quick_ids"] = ids
    card["state"] = state


def _bind_skill_chip(card: dict, skill: str) -> None:
    """技能芯片绑定一项技能（``full.txt:27830-27837``「特定的技能」）。

    存 ``state["skill_chips"] = [技能名, ...]``：多枚芯片可绑多项技能。
    """
    state = dict(card.get("state") or {})
    bound = list(state.get("skill_chips") or [])
    sk = str(skill).strip()
    if sk and sk not in bound:
        bound.append(sk)
    state["skill_chips"] = bound
    card["state"] = state


def _persist(conn: sqlite3.Connection, group_id: str, user_id: str,
             card: dict, easy: bool) -> None:
    """只落盘当前卡（不改 state 语义），给「扣了钱但没扣人性」的路径用。"""
    if easy:
        easy_roster.save(conn, group_id, user_id, card, card.get("name"))
    else:
        roster.save(conn, group_id, user_id, card)


def _similar(name: str) -> str:
    hits = cyber.hl_table()
    if not hits:
        return ""
    picks = cyber.search(name, limit=4)
    if picks:
        return "；比如：" + "、".join(e["zh"] for e in picks)
    sample = list(hits)[:4]
    return "；表里有：" + "、".join(sample)


def install(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
            roller: dice.Roller | None = None) -> str:
    """安装一件赛博组件：投 HL → 扣人性 → EMP 可能下降。"""
    m = _INSTALL_RE.match((text or "").strip())
    if not m:
        return "用法：安装 <赛博组件名>（比如「安装 赛博义眼」）。"
    raw = m.group(1).strip().strip("。！!. ")
    if not raw:
        return "要安装什么？比如「安装 赛博义眼」。"
    # 技能芯片要绑定一项技能：「安装 技能芯片 黑客」/「安装 技能芯片：黑客」。
    chip_skill = ""
    name = raw
    if raw.startswith("技能芯片"):
        rest = raw[len("技能芯片"):].strip(" ：:，,")
        if rest:
            chip_skill = rest
            name = "技能芯片"
    # 多实例基础组件（两只义眼/两条义体）可指定挂到哪一个实例：
    # 「安装 图像增强 到 c2」/「…到 左眼」。不指定就自动挑有空槽的。
    parent_hint = ""
    m_inst = re.search(r"(?:到|装到|挂到|装在)\s*([cC]\d+)\s*$", name)
    if m_inst:
        parent_hint = m_inst.group(1).lower()
        name = name[:m_inst.start()].strip()
    name = name.strip()

    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡，先发「建卡」或「认领」拿一张。"

    # 弹出式武器装的是**市面上的具体武器**（full.txt:10261-10286），
    # 不是「弹出式专用武器」，也不套档位表。这里把「弹出式近战武器 武士刀」
    # 拆成 (安装位, 武器名)，能不能装交给 weapon_catalog 按属性判定。
    mount_name = ""
    mounted_weapon = None
    for slot_name in ("弹出式近战武器", "弹出式远程武器"):
        if name.startswith(slot_name):
            rest = name[len(slot_name):].strip(" ：:，,")
            mount_name = slot_name
            if not rest:
                sample = weapon_catalog.list_for(slot_name)
                hint = (f"\n比如「安装 {slot_name} {sample[0]['name']}」。") if sample else ""
                return (f"要装哪把武器？{slot_name}装的是市面上的具体武器"
                        f"（full.txt:10261-10286）。可选："
                        f"{weapon_catalog.summary(slot_name)}{hint}")
            mounted_weapon = weapon_catalog.find(rest)
            if mounted_weapon is None:
                picks = weapon_catalog.search(rest, limit=5)
                hint = ("；你要装的是：" + "、".join(p["name"] for p in picks)
                        if picks else f"；可选：{weapon_catalog.summary(slot_name)}")
                return f"🦾 武器目录里没有「{rest}」{hint}"
            blocked = weapon_catalog.mount_problem(mounted_weapon, mount=slot_name)
            if blocked:
                return (f"🦾 装不了{mounted_weapon['name']}：{blocked}"
                        f"\n可选：{weapon_catalog.summary(slot_name)}")
            name = slot_name
            break

    entry = cyber.hl_of(name)
    if entry is None:
        return f"没在赛博组件表里找到「{name}」{_similar(name)}"

    if any(w in (text or "") for w in QUOTE_WORDS) and "安装" not in name:
        dice_spec = entry.get("hl_dice")
        price = entry.get("cost")
        pieces = [f"🦾 {entry['zh']}（{entry.get('section', '')}）"]
        if price is not None:
            pieces.append(f"花费 {price} 欧元")
        pieces.append(
            f"人性损失 {entry['hl']}（建卡时按这个算）"
            + (f"，游戏里安装投 {dice_spec}" if dice_spec else "，没有骰子、固定扣这些")
        )
        if entry.get("facility"):
            facility = str(entry["facility"])
            dv = SURGERY_DV.get(facility)
            pieces.append(f"安装设施：{facility}"
                          + (f"（技医 DV{dv}，full.txt:18770-18781）" if dv else ""))
        return "；".join(pieces) + "。要装就发「安装 " + entry["zh"] + "」。"

    before = humanity_of(card, easy)

    # 前置 + 槽位校验（规则书 :9700-9704 的插槽体系）：不通过就**什么都不做**，
    # 不扣钱不扣人性，也不消耗骰子。``plan_install`` 还会选出挂到哪件**基础实例**。
    installed = _cyber_list(card)
    problem, parent_id = slots.plan_install(installed, entry["zh"],
                                            parent_id=parent_hint)
    if problem:
        return f"🦾 装不了{entry['zh']}：{problem}"

    # 植入线性框架的体格前置（书 :28628-28655）：
    # Σ 需体格≥6 + 1 个骨肉织绕；β 需体格≥8 + 2 个骨肉织绕。
    frame_req = {"植入线性框架Σ": (6, 1), "植入线性框架β": (8, 2)}.get(entry["zh"])
    if frame_req:
        need_body, need_hug = frame_req
        hugs = sum(1 for r in installed if r["name"] == "骨肉织绕")
        base_body = int((card.get("stats") or {}).get("BODY", 0))
        eff_body = min(10, base_body + 2 * hugs)   # 骨肉织绕 +2/份、clamp 10
        if eff_body < need_body:
            return (f"🦾 装不了{entry['zh']}：需要体格 ≥ {need_body}"
                    f"（你现在 {eff_body}）。")
        if hugs < need_hug:
            return (f"🦾 装不了{entry['zh']}：需要 {need_hug} 个骨肉织绕"
                    f"（你现在 {hugs} 个）。")

    # 快拆挂扣自带的「快拆槽」（主人 2026-11-10 定：不做 LPTU 实体装备）：
    # 装上快拆挂扣 → 2 个臂槽。槽里每条臂带**实例 id**；快拆挂扣另记一份
    # ``quick_ids``（上过身的臂 id）。
    #   * 换臂时：槽里这条臂的 id 在 quick_ids 里 → 免人性；
    #   * 不在 → 它是全新的臂，照常扣人性，并把 id 记进 quick_ids。
    qr_slots = _qr_entries(card)
    slot_idx = next((i for i, e in enumerate(qr_slots)
                     if e["name"] == entry["zh"]), None)
    from_qr = entry["zh"] == "赛博手臂" and slot_idx is not None
    slot_id = qr_slots[slot_idx]["id"] if from_qr else ""
    known_arm = bool(slot_id) and slot_id in _quick_ids(card)
    if from_qr:
        # 从槽里取出来：一件换一件，槽位 -1（稍后随 _remember 一起落盘）
        left = list(qr_slots)
        left.pop(slot_idx)
        st = dict(card.get("state") or {})
        st["quick_slots"] = left
        card["state"] = st
    if entry["zh"] == "快拆挂扣":
        st = dict(card.get("state") or {})
        st.setdefault("quick_slots", [])
        card["state"] = st

    # 从槽取「快拆挂扣记录过 id」的臂才免人性；全新（没记录）照常扣。
    loss = 0 if (from_qr and known_arm) else cyber.roll_hl(entry, roller)

    # 重装豁免（`full.txt:535-538` FAQ + `:10287-10291` 快拆挂扣）：
    # * 芯片/武器这类「本身就可以快速更换的」→ 卸下再装不再扣人性。
    # * 赛博手臂 + 快拆挂扣 + 以前卸下过 → 不再扣人性（「重新安装你用过的臂」）。
    # 其余一律照常扣（FAQ：「卸除后很快重新安装…是的，会再损失一次」）。
    quick_note = ""
    if from_qr and known_arm:
        quick_note = ("（快拆挂扣的记录里有这条臂的 id：免人性、免花钱，"
                      "full.txt:10287-10291）")
    elif from_qr:
        quick_note = ("（快拆槽里这条**没有 id 记录**：全新的臂第一次上身，"
                      "照常扣人性并记下 id，full.txt:10287）")
    from cyberpunk import cyber_bonus  # 局部导入，避免循环
    used = [str(x) for x in
            ((card.get("state") or {}).get("used_items") or [])]
    zh_name = entry["zh"]
    if not from_qr and zh_name in used:
        if zh_name in QUICK_CHANGE:
            loss = 0
            quick_note = "（芯片/武器类可快速更换，重装不再扣人性，full.txt:536）"
    # 注意：赛博手臂不再用「used_items 名字」判豁免，改由快拆挂扣的
    # **臂 id 记录**（quick_ids）判定，见上面的 from_qr / known_arm。

    # 臂的实例 id：从槽取回就复用原 id；新臂发一个新 id，并记进快拆挂扣的 ID 清单
    arm_rid = ""
    if entry["zh"] == "赛博手臂":
        arm_rid = slot_id or slots.new_id(installed)
        _mark_quick_id(card, arm_rid)

    # 花钱：赛博组件不是白来的。价格取 cyberware_hl 的 cost（书里标的是市场价，
    # `full.txt:30352-30369` 的八档价格类别已折成欧元）。钱不够就**不安装**、
    # 不扣人性（规则书没有赊账条款，标「约定」）。从快拆槽取出 = 自己已有的臂，不花钱。
    price = 0 if from_qr else int(entry.get("cost") or 0)
    eb_before = growth.eb(card)
    if price:
        try:
            growth.spend_eb(card, price, reason=f"安装{entry['zh']}")
        except growth.GrowthError as exc:
            return (f"💸 装不起{entry['zh']}：要 {price} 欧元，你只有 {eb_before} 欧元"
                    f"（差 {price - eb_before}）。先去挣点钱，或者换件便宜的。")
    eb_after = growth.eb(card)
    money_note = f"，付了 {price} 欧元（余额 {eb_after}）" if price else ""

    # 弹出式武器：记下装的是哪把市面武器（数值用它自己的）。
    if mounted_weapon is not None:
        from cyberpunk import cyber_weapon as _cw
        _cw.set_mount(card, entry["zh"], mounted_weapon["name"])

    if loss <= 0:
        if chip_skill:
            _bind_skill_chip(card, chip_skill)
        # 以前这里只 _persist、忘了把义体记进列表——HL=0 的件（书内页 112
        # 写 N/A 的）会「装上了却不在身上」。现在统一走 _remember。
        _remember(conn, group_id, user_id, card, easy, entry["zh"], before,
                  parent_id=parent_id, record_id=arm_rid)
        extra = f"，绑定技能「{chip_skill}」" if chip_skill else ""
        if quick_note:
            return (f"🦾 装上了{entry['zh']}{extra}{money_note}：{quick_note}"
                    f"人性还是 {before}。")
        return (f"🦾 装上了{entry['zh']}{extra}{money_note}：这件不造成人性损失"
                f"（书内页 112 写 N/A），人性还是 {before}。")

    after = max(0, before - loss)
    # 与规则引擎同一口径：EMP = max(1, hum // 10)（cyberpunk/characters.py:202-206）
    emp_after = max(1, after // 10)
    emp_before = _emp_of(card, easy)

    _write_humanity(conn, group_id, user_id, card, easy, after)
    if chip_skill:
        _bind_skill_chip(card, chip_skill)
    _remember(conn, group_id, user_id, card, easy, entry["zh"], after,
              parent_id=parent_id, record_id=arm_rid)

    lines = [
        f"🦾 装上了{entry['zh']}"
        + (f"（{entry['section']}）" if entry.get("section") else "")
        + f"：人性 {before} → {after}"
        + (f"（投 {entry['hl_dice']} 得 {loss}）" if entry.get("hl_dice") else f"（固定 {loss}）"),
    ]
    if chip_skill:
        lines.append(f"  绑定技能「{chip_skill}」：该技能等级至少按 3 算"
                     f"（已超过 3 则不生效，full.txt:27830-27837）。")
    if money_note:
        lines.append(f"  花了 {price} 欧元（余额 {eb_after}）。")
    if emp_after != emp_before:
        lines.append(f"  共情 EMP {emp_before} → {emp_after}（每掉满 10 点人性 −1，最低 1）。")
    else:
        lines.append(f"  共情 EMP 仍是 {emp_after}（还没掉满 10 点）。")
    if after <= 0:
        lines.append(f"  ⚠ {CYBERPSYCHOSIS_NOTE}。")
    elif after < 10:
        lines.append("  ⚠ 人性不到 10 点，EMP 已经压到 1 了。")
    return chr(10).join(lines)


#: 卸载时**不会**再扣人性的义体（书 :536「有些殖装并非如此，譬如那些本身就可以
#: 快速更换的芯片和武器」）。芯片类 = 插入式芯片（需要芯片插槽的那批），
#: 武器类 = 弹出式武器/义肢武器（它们本身就是可换的部件）。
QUICK_CHANGE = {
    "化学物质分析器", "记忆芯片", "嗅觉增强", "痛觉编辑器", "技能芯片", "触觉增强",
    "弹出式榴弹发射器", "弹出式近战武器", "弹出式远程武器", "弹出式盾牌",
    "射镖枪", "隐藏枪套", "皮下口袋", "快拆挂扣",
}

#: 快拆挂扣自带的「快拆槽」容量（主人 2026-11-10：不做 LPTU 实体装备，
#: 两个臂槽直接挂在快拆挂扣上）。存进去的是**你用过的**赛博臂，重装免人性。
QUICK_SLOTS = 2

#: 卸载手术费：按安装设施不同（full.txt:18770-18781 的设施→费用表）
UNINSTALL_FEE = {"购物中心": 100, "诊所": 500, "医院": 1000}

#: 卸载且不接受恢复人性治疗时的默认口径（书 :19092-19097：移除前人性无法恢复）
HUMANITY_NOT_RESTORED = "人性不会因为卸载而恢复（规则书：移除赛博组件之前人性无法完全回复）"


def uninstall(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
              roller: dice.Roller | None = None) -> str:
    """卸载一件赛博组件。

    规则口径（``rules/red_core/raw/full.txt``）：

    * ``:535-538`` 卸除后**再安装会再损失一次人性**——所以卸载本身**不退还人性**；
      只有精神医疗疗程才能回人性（``:19092-19097``，本引擎未做该疗程）。
    * ``:536`` 例外：芯片与武器这类「可以快速更换的」不重复扣（见 ``QUICK_CHANGE``）。
    * ``:602`` 二手只要还能用就按原价出售 → 卸载后按 **cost 全额**退还 EB。
    * ``:18770-18781`` 卸载手术按设施收费（购物中心 100 / 诊所 500 / 医院 1000）。
    * ``slots.uninstall_blockers`` 拦住「还有配件挂在这件上」的情况。
    """
    m = _UNINSTALL_RE.match((text or "").strip())
    if not m:
        return "用法：卸载 <赛博组件名>（比如「卸载 图像增强」）。"
    name = m.group(1).strip().strip("。！!. ")
    if not name:
        return "要卸载什么？比如「卸载 图像增强」。"

    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"

    entry = cyber.hl_of(name)
    if entry is None:
        return f"没在赛博组件表里找到「{name}」{_similar(name)}"
    zh = entry["zh"]

    installed = _cyber_list(card)
    names = [r["name"] for r in installed]
    if zh not in names:
        return f"你身上没有「{zh}」。当前装着：{'、'.join(names) or '（空）'}"

    blocked = slots.uninstall_blockers(installed, zh)
    if blocked:
        return f"🦾 暂时卸不了{zh}：{blocked}"

    # ① 从卡上移除一件（按实例 id 移除，多件同名时只卸一件）
    target = next((r for r in installed if r["name"] == zh), None)
    items = [r for r in installed if r is not target]
    # 快拆挂扣是「插座」：拆的是装它的那条臂时，它留在身上，
    # 优先重新挂到另一条臂，没有就悬空（parent 置空）。
    if target is not None:
        other_arm = next((r for r in items if r["name"] == "赛博手臂"), None)
        for r in items:
            if r.get("name") == "快拆挂扣" and r.get("parent") == target["id"]:
                r["parent"] = other_arm["id"] if other_arm else ""
    _write_cyber(card, items)
    # 记「卸下过的」：重装豁免要用（芯片/武器快速更换、快拆挂扣重装赛博臂）。
    _mark_used(card, zh)

    # ② 人性：不退还（再装会再扣一次）。只提示，不改数值。
    hum = humanity_of(card, easy)

    # ③ 赛博手臂 + 快拆挂扣：卸下的臂可以收进「快拆槽」（最多 2 条），不卖。
    #    （主人 2026-11-10：不做 LPTU 实体装备，槽直接挂在快拆挂扣上。）
    stored = False
    qr_note = ""
    if zh == "赛博手臂" and any(r["name"] == "快拆挂扣" for r in installed):
        st = dict(card.get("state") or {})
        slots_list = _qr_entries(card)
        if len(slots_list) < QUICK_SLOTS:
            # 存进去时带上这条臂的实例 id：之后换回它，快拆挂扣认得出
            slots_list.append({"name": "赛博手臂",
                               "id": str(target["id"] if target else "")})
            st["quick_slots"] = slots_list
            card["state"] = st
            stored = True
            qr_note = (f"收进快拆槽（{len(slots_list)}/{QUICK_SLOTS}），不卖。"
                       f"下次换回它不会扣人性（id 已在快拆挂扣记录里）。")

    # ④ 退钱：原价卖出，扣掉卸载手术费（收进快拆槽的不卖）
    price = int(entry.get("cost") or 0)
    fee = UNINSTALL_FEE.get(str(entry.get("facility") or ""), 0)
    refund = 0 if stored else max(0, price - fee)
    if refund:
        growth.grant_eb(card, refund, reason=f"出售{zh}")

    _persist(conn, group_id, user_id, card, easy)

    lines = [f"🦾 卸下了{zh}。"]
    if stored:
        lines.append(f"  {qr_note}")
    elif refund:
        lines.append(f"  按原价卖出得 {price} 欧元，扣卸载手术费 {fee}，"
                     f"实际到账 {refund}（余额 {growth.eb(card)}）。")
    elif price:
        lines.append(f"  这件卖不出钱（{price} ≤ 手术费 {fee}），不退款。")
    if zh in QUICK_CHANGE:
        lines.append("  芯片/武器类可以快速更换，下次再装不会再扣人性。")
    else:
        lines.append(f"  人性仍是 {hum}。{HUMANITY_NOT_RESTORED}；"
                     f"想回人性要做精神医疗疗程（本引擎未做）。")
    left = slots.describe_slots(items)
    if left:
        lines.append("  插槽：" + "；".join(left))
    return chr(10).join(lines)


def swap_arm(conn: sqlite3.Connection, group_id: str, user_id: str, *,
             roller: dice.Roller | None = None) -> str:
    """**一个动作**换臂（快拆挂扣，`full.txt:10287-10291`）。

    取下当前赛博手臂 + 从快拆槽取出另一条装上——**合并成一个动作**，不是两个。
    * 槽里是「用过的」臂 → 免人性、不花钱（它本来就是你身体的一部分）；
    * 槽里是「全新的」臂 → 第一次上身**照常扣人性**（`:10287`）。
    """
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    installed = _cyber_list(card)
    if not any(r["name"] == "快拆挂扣" for r in installed):
        return f"🦾 你身上没装快拆挂扣，换臂要拆了再装（两个动作）。"
    current = next((r for r in installed if r["name"] == "赛博手臂"), None)
    if current is None:
        return f"🦾 你身上没有赛博手臂可换。"
    state = dict(card.get("state") or {})
    qr = _qr_entries(card)
    idx = next((i for i, e in enumerate(qr) if e["name"] == "赛博手臂"), None)
    if idx is None:
        return (f"🦾 快拆槽里没有备用的赛博手臂（{len(qr)}/{QUICK_SLOTS}）。"
                f"先用「卸载 赛博手臂」把一条收进槽。")

    # ① 当前臂取下（进槽）；② 槽里那条取出来装上。净槽数不变。
    #    快拆挂扣记的是**臂 id**：槽里那条的 id 在记录里 → 免人性，
    #    不在 → 全新的臂，第一次上身扣人性并把 id 记进去。
    spare = qr.pop(idx)
    known = bool(spare.get("id")) and str(spare["id"]) in _quick_ids(card)
    qr.append({"name": "赛博手臂", "id": str(current["id"])})
    items = [r for r in installed if r is not current]
    new_id = str(spare.get("id") or "") or slots.new_id(items)
    _mark_quick_id(card, new_id)
    items.append({"id": new_id, "name": "赛博手臂", "parent": ""})
    # 快拆挂扣跟着挂到新臂上
    for r in items:
        if r["name"] == "快拆挂扣" and r.get("parent") in ("", current["id"]):
            r["parent"] = new_id
    _write_cyber(card, items)
    st = dict(card.get("state") or {})
    st["quick_slots"] = qr
    card["state"] = st
    if known:
        _persist(conn, group_id, user_id, card, easy)
        return (f"🦾 快拆挂扣：一个动作换好了手臂"
                f"（快拆槽 {len(qr)}/{QUICK_SLOTS}，这臂 id 在记录里——"
                f"免人性、不花钱）。")
    # 全新的臂（id 不在记录里）第一次上身：照常扣人性（`:10287`）
    entry = cyber.hl_of("赛博手臂")
    loss = cyber.roll_hl(entry, roller) if entry else 0
    hum = humanity_of(card, easy)
    after = max(0, hum - loss)
    _write_humanity(conn, group_id, user_id, card, easy, after)
    _persist(conn, group_id, user_id, card, easy)
    return (f"🦾 快拆挂扣：一个动作换好了手臂（快拆槽 {len(qr)}/{QUICK_SLOTS}）。"
            f"⚠ 换上的是**全新的臂**（id 不在记录里），第一次上身："
            f"人性 {hum} → {after}（投 {entry.get('hl_dice')} 得 {loss}，"
            f"full.txt:10287）；已把它的 id 记进快拆挂扣。")


def status_text(conn: sqlite3.Connection, group_id: str, user_id: str) -> str:
    """列出这个人装过的赛博组件、当前人性与插槽占用。"""
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    items = _cyber_list(card)
    hum = humanity_of(card, easy)
    emp = _emp_of(card, easy)
    head = f"🦾 {card.get('name', '你')}：人性 {hum}，共情 EMP {emp}"
    if not items:
        return head + "；还没装过赛博组件。"
    names = [r["name"] for r in items]
    lines = [head] + [f"· {name}" for name in names]
    # 插槽占用
    try:
        used = slots.describe_slots(items)
        if used:
            lines.append("插槽：" + "；".join(used))
    except Exception:  # noqa: BLE001  展示失败不阻断
        pass
    # 快拆槽（快拆挂扣自带的 2 个臂槽）
    try:
        qr = _qr_entries(card)
        if qr or any(r["name"] == "快拆挂扣" for r in items):
            desc = "、".join(e["name"] for e in qr) if qr else "空"
            lines.append(f"快拆槽：{desc}（{len(qr)}/{QUICK_SLOTS}）")
    except Exception:  # noqa: BLE001
        pass
    # 生效的加成一并展示（RED 义体 → 检定型加成）
    try:
        from cyberpunk import cyber_bonus
        bonus = cyber_bonus.describe(card)
        if bonus:
            lines.append("加成：" + "；".join(bonus))
    except Exception:  # noqa: BLE001  展示加成失败不阻断
        pass
    # 免疫项（防眩晕/鼻滤器/鳃 这类）
    try:
        from cyberpunk import immunity
        immune = immunity.describe(card)
        if immune:
            lines.append("免疫：" + "；".join(immune))
    except Exception:  # noqa: BLE001
        pass
    # 能力项（C 类：交互接口/监测器/通讯等，无自动数值，交 DM）
    try:
        from cyberpunk import cyber_bonus
        ability = cyber_bonus.abilities(card)
        if ability:
            lines.append("能力：" + "；".join(ability))
    except Exception:  # noqa: BLE001
        pass
    # 义体武器（赛博大蛇 4D6 这类）
    try:
        from cyberpunk import cyber_weapon
        arms = cyber_weapon.describe(card)
        if arms:
            lines.append("义体武器：" + "；".join(arms))
    except Exception:  # noqa: BLE001
        pass
    return chr(10).join(lines)


#: 安装手术难度（`full.txt:18770-18781`）：设施 → (技医 DV, 手术费已含在售价里)
SURGERY_DV = {"购物中心": 13, "诊所": 15, "医院": 17}

#: 精神医疗疗程（`full.txt:19081-19118`）
#: 一般：500 EB + 100 耗材，恢复 2d6 人性，DV15；极端：1000 + 500，恢复 4d6，DV17。
THERAPY = {
    "一般": {"cost": 500, "materials": 100, "dice": 2, "dv": 15},
    "极端": {"cost": 1000, "materials": 500, "dice": 4, "dv": 17},
}


def humanity_cap(card: dict) -> int:
    """人性**上限**（`:19081-19118`）：每件赛博组件 −2，每件赛博格组件再 −2（合计 −4）。

    「在安装时造成 0 人性损失的赛博组件不会降低人性上限」→ ``hl<=0`` 的不计。
    本引擎以 100 为满值（EMP×10 的上限），标「约定」。
    """
    records = _cyber_list(card)
    n = 0
    n_body = 0
    for r in records:
        entry = cyber.hl_of(r["name"])
        if not entry:
            continue
        if int(entry.get("hl") or 0) <= 0:
            continue
        n += 1
        if str(entry.get("section") or "") == "赛博格组件":
            n_body += 1
    return max(0, 100 - 2 * n - 2 * n_body)


def therapy(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
            roller: dice.Roller | None = None) -> str:
    """精神医疗疗程：花 EB 回人性（`:19081-19118`）。

    * 一般：500 EB + 100 耗材 → 恢复 2d6 人性（DV15）
    * 极端：1000 EB + 500 耗材 → 恢复 4d6 人性（DV17）

    恢复量受「人性上限」限制（每件义体 −2）；卸载义体之后才能完全回复。
    DV 由设施/技医掷，本引擎不掷（「约定」）。
    """
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    tier = "极端" if ("极端" in (text or "") or "4d6" in (text or "")) else "一般"
    spec = THERAPY[tier]
    total = spec["cost"] + spec["materials"]
    before = humanity_of(card, easy)
    try:
        growth.spend_eb(card, total, reason=f"精神医疗（{tier}）")
    except growth.GrowthError as exc:
        return (f"💸 做不起{tier}疗程：要 {total} 欧元（{spec['cost']} 疗程 + "
                f"{spec['materials']} 耗材），你只有 {growth.eb(card)}。")
    roller = roller or dice.RandomRoller()
    gain = sum(roller.roll(6) for _ in range(spec["dice"]))
    cap = humanity_cap(card)
    after = min(cap, before + gain)
    _write_humanity(conn, group_id, user_id, card, easy, after)
    _persist(conn, group_id, user_id, card, easy)
    lines = [
        f"🧠 {card.get('name', '你')} 做了一次{tier}精神医疗疗程："
        f"恢复 {gain} 点人性，人性 {before} → {after}（上限 {cap}）。",
        f"  花了 {total} 欧元（余额 {growth.eb(card)}）。",
    ]
    if after >= cap:
        lines.append("  已经到当前人性上限了——还有义体在身上，想再高只能先卸掉几件。")
    return chr(10).join(lines)


#: 侦测型义体 → 能感知什么（C 类，无自动数值；有没有信号由 DM 裁定）
SENSORS = {
    "辐射监测器": "100 米内的辐射源（视野里泛蓝光）",
    "雷达监测器": "100 米内工作中的雷达设备（会哔哔响）",
    "窃听监测器": "2 米内的窃听器（会哔哔响）",
    "追踪器": "1 公里内配套的追踪器",
    "无线电通讯": "1 公里内的无线电通讯",
    "微型摄像": "可录下眼前的音频与视频",
}


def detect(conn: sqlite3.Connection, group_id: str, user_id: str) -> str:
    """「侦测」：列出身上装了的感知型义体能察觉到什么。

    不掷骰、不判真伪——世界里有没有对应信号由 DM 决定，引擎只把
    「你有这个传感器」变成可发现性提示。
    """
    card, _easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    names = {r["name"] for r in _cyber_list(card)}
    hits = [f"· {n}：可感知{s}" for n, s in SENSORS.items() if n in names]
    if not hits:
        return ("🔍 你身上没有可用的侦测义体（辐射/雷达/窃听监测器、追踪器、"
                "无线电通讯之类）。")
    return ("🔍 侦测义体待命：\n" + "\n".join(hits)
            + "\n（周围实际有没有对应信号，由 DM 裁定。）")
