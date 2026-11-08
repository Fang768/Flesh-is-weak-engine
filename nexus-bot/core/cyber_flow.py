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

from cyberpunk import cyber, dice

from core import easy_roster, roster

#: 触发安装的词（放在 `core/turn_flow.py` 的建卡/战斗分支之外，作为系统指令）
INSTALL_WORDS = ("安装", "装义体", "装赛博", "植入", "换上义体")

#: 明确说「想看看」的，不执行安装，只报价格与 HL
QUOTE_WORDS = ("查", "看", "多少钱", "价格", "多少人性")

_INSTALL_RE = re.compile(
    r"^(?:/?(?:安装|装义体|装赛博|植入|换上义体))\s*[：:]?\s*(.+)$"
)

#: 人性归零的口径（规则书没写死，标「约定」）
CYBERPSYCHOSIS_NOTE = "人性归零（赛博精神病风险，本引擎只提示不夺卡）"


def looks_like_install(text: str) -> bool:
    body = (text or "").strip()
    return bool(_INSTALL_RE.match(body))


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


def _cyber_list(card: dict) -> list[str]:
    return [str(x) for x in (card.get("state") or {}).get("cyber", [])]


def _remember(conn: sqlite3.Connection, group_id: str, user_id: str,
              card: dict, easy: bool, item: str, hum: int) -> None:
    state = dict(card.get("state") or {})
    items = list(state.get("cyber") or [])
    items.append(item)
    state["cyber"] = items[:24]
    state["hum"] = hum
    card["state"] = state
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
    name = m.group(1).strip().strip("。！!. ")
    if not name:
        return "要安装什么？比如「安装 赛博义眼」。"

    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡，先发「建卡」或「认领」拿一张。"

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
            pieces.append(f"安装设施：{entry['facility']}")
        return "；".join(pieces) + "。要装就发「安装 " + entry["zh"] + "」。"

    before = humanity_of(card, easy)
    loss = cyber.roll_hl(entry, roller)
    if loss <= 0:
        return (f"🦾 装上了{entry['zh']}：这件不造成人性损失（书内页 112 写 N/A），"
                f"人性还是 {before}。")

    after = max(0, before - loss)
    # 与规则引擎同一口径：EMP = max(1, hum // 10)（cyberpunk/characters.py:202-206）
    emp_after = max(1, after // 10)
    emp_before = _emp_of(card, easy)

    _write_humanity(conn, group_id, user_id, card, easy, after)
    _remember(conn, group_id, user_id, card, easy, entry["zh"], after)

    lines = [
        f"🦾 装上了{entry['zh']}"
        + (f"（{entry['section']}）" if entry.get("section") else "")
        + f"：人性 {before} → {after}"
        + (f"（投 {entry['hl_dice']} 得 {loss}）" if entry.get("hl_dice") else f"（固定 {loss}）"),
    ]
    if emp_after != emp_before:
        lines.append(f"  共情 EMP {emp_before} → {emp_after}（每掉满 10 点人性 −1，最低 1）。")
    else:
        lines.append(f"  共情 EMP 仍是 {emp_after}（还没掉满 10 点）。")
    if after <= 0:
        lines.append(f"  ⚠ {CYBERPSYCHOSIS_NOTE}。")
    elif after < 10:
        lines.append("  ⚠ 人性不到 10 点，EMP 已经压到 1 了。")
    return chr(10).join(lines)


def status_text(conn: sqlite3.Connection, group_id: str, user_id: str) -> str:
    """列出这个人装过的赛博组件与当前人性。"""
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    items = _cyber_list(card)
    hum = humanity_of(card, easy)
    emp = _emp_of(card, easy)
    head = f"🦾 {card.get('name', '你')}：人性 {hum}，共情 EMP {emp}"
    if not items:
        return head + "；还没装过赛博组件。"
    lines = [head] + [f"· {name}" for name in items]
    # 生效的加成一并展示（RED 义体 → 检定型加成）
    try:
        from cyberpunk import cyber_bonus
        bonus = cyber_bonus.describe(card)
        if bonus:
            lines.append("加成：" + "；".join(bonus))
    except Exception:  # noqa: BLE001  展示加成失败不阻断
        pass
    return chr(10).join(lines)
