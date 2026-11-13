"""商店：买、卖、穿脱护甲、夜市。

数据源（**不建商店专属清单**，全部复用现有目录，加东西只加数据）：

* 武器 → ``rules/red_core/data/weapons_catalog.json``（按属性判定，弹出式也用）
* 护甲/通用装备/弹药 → ``rules/red_core/data/gear_catalog.json``
* 赛博组件 → ``rules/red_core/data/cyberware_hl.json``（已有 cost，走「安装」）

规则依据（``rules/red_core/raw/full.txt``）：

* ``:579`` 没掮客：「您可以购买 100 欧元以下的物品。」
* ``:583-595`` 掮客 3 级 <500、7 级 <1000、9 级 <5000、10 级任何东西
* ``:602``「只要它还能用，你就可以按照它的原价出售。」
* ``:608-611`` 实际成交看交易对抗：`酷+交易+掮客等级` 对抗对方，
  输了被砍到 **80%–90%**；掮客靠这个赚 10%–40% 差价
* ``:25529`` 夜市生成器：2d6 品类 → 1d10 种类数 → d100 具体商品
  （「您可以买到 GM 在夜市上出售的任何东西」）

**约定（主人 2026-11-13 定）**：

* **不设背包上限**。重量体积视情况而定，交 DM 判断；买的时候过一次
  「库存确认」（``inventory_check``），现在默认放行，留接口等以后约定。
* **穿护甲随时可穿**，非战斗状态无需检定；一次只穿一套（书里是一整套）。
* 卖东西默认原价；玩家说「砍价」才走交易对抗（折中口径）。
* 夜市模式：剧本场景写 ``market`` 时只卖那几样，否则用全货架。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from core import easy_roster, growth, roster

DATA_DIR = Path(__file__).resolve().parents[2] / "rules" / "red_core" / "data"
WEAPONS_FILE = DATA_DIR / "weapons_catalog.json"
GEAR_FILE = DATA_DIR / "gear_catalog.json"

_BUY_RE = re.compile(r"^(?:/?(?:购买|买|入手|买下|采购))\s*[：:]?\s*(.+)$")
_SELL_RE = re.compile(r"^(?:/?(?:出售|卖掉|卖出|卖))\s*[：:]?\s*(.+)$")
_HAGGLE_RE = re.compile(r"砍价|讨价还价|讲价|还价")
_WEAR_RE = re.compile(r"^(?:/?穿上|/?套上)\s*[：:]?\s*(.+)$")
_TAKEOFF_RE = re.compile(r"^(?:/?脱下|/?脱掉|/?卸甲)\s*[：:]?\s*(.*)$")

#: 「砍价」这类尾巴要从物品名里剥掉（「卖 合金装备 砍价」）
_HAGGLE_TAIL = re.compile(r"[\s，,]*(?:砍价|讨价还价|讲价|还价)[\s。！!.，,]*$")

#: 行情用语（问价/看货架，不成交）
_QUOTE_WORDS = ("查", "看", "多少钱", "价格", "有什么", "卖什么", "货架", "商店")

#: 交易对抗失败时的压价区间（``:611``「砍价到底价的 90%-80%」）
HAGGLE_LOW, HAGGLE_HIGH = 0.80, 0.90


# ---------- 目录 ----------


@lru_cache(maxsize=1)
def _weapons() -> list[dict]:
    try:
        return json.loads(WEAPONS_FILE.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return []


@lru_cache(maxsize=1)
def _gear() -> list[dict]:
    try:
        data = json.loads(GEAR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("items") or []


def tiers() -> dict[str, int]:
    try:
        data = json.loads(GEAR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict((data.get("_meta") or {}).get("tiers") or {})


def items() -> list[dict]:
    """所有能买的东西（武器 + 护甲 + 装备 + 弹药）。"""
    return list(_weapons()) + list(_gear())


def _key(name: str) -> str:
    return re.sub(r"[\s·・,，、/（）()\-—–]", "", (name or "")).lower()


def find(name: str) -> dict | None:
    want = _key(name)
    if not want:
        return None
    pool = items()
    for it in pool:
        if _key(it["name"]) == want:
            return it
    hits = [it for it in pool if want in _key(it["name"])]
    return hits[0] if len(hits) == 1 else None


def search(keyword: str, limit: int = 6) -> list[dict]:
    want = _key(keyword)
    if not want:
        return []
    exact = [it for it in items() if _key(it["name"]) == want]
    loose = [it for it in items() if want in _key(it["name"]) and it not in exact]
    return (exact + loose)[:limit]


def tier_of(euro: int) -> str:
    table = sorted(tiers().items(), key=lambda kv: kv[1])
    name = table[0][0] if table else "廉价"
    for tier, value in table:
        if int(euro) >= value:
            name = tier
    return name


def kind_of(entry: dict) -> str:
    return str(entry.get("kind") or entry.get("category") or "")


def is_armor(entry: dict) -> bool:
    return kind_of(entry) == "护甲"


# ---------- 渠道 ----------


def broker_level(card: dict) -> int:
    return max(0, int((card.get("state") or {}).get("broker_level") or 0))


def cap_text(level: int) -> str:
    cap = growth.broker_cap(level)
    if cap is None:
        return f"掮客等级 {level}：什么都能买（含超奢）"
    if level:
        return f"掮客等级 {level}：只能买低于 {cap} 欧元的商品"
    return f"没掮客：只能买低于 {cap} 欧元的商品（full.txt:579）"


# ---------- 夜市模式（:25529 生成器） ----------


def market_for(scene: dict | None) -> dict | None:
    """场景里写了 ``market`` 才有夜市限制；否则 None = 全货架。"""
    if not scene:
        return None
    m = scene.get("market")
    return m if isinstance(m, dict) else None


def roll_market(rng: random.Random | None = None) -> dict:
    """掷一个夜市（``:25529``）：2d6 品类 → 1d10 种类数 → d100 具体商品。

    返回 ``{"categories": [...], "items": [名字...]}``。引擎不编商品，
    只从现有目录里挑。
    """
    rng = rng or random.Random()
    cats = ["食物和药物", "个人电子产品", "武器和护甲", "赛博组件",
            "服装和时尚装置", "求生装备"]
    first = rng.randint(1, 6)
    picks = [cats[(first - 1) % len(cats)]]
    for _ in range(10):          # 重掷直到和第一颗不同（:25531）
        nxt = rng.randint(1, 6)
        if (nxt - 1) % len(cats) != (first - 1) % len(cats):
            picks.append(cats[(nxt - 1) % len(cats)])
            break
    pool = {
        "武器和护甲": [i for i in items()
                       if kind_of(i) in ("远程武器", "近战武器", "异种武器", "护甲")],
        "个人电子产品": [i for i in items() if kind_of(i) == "通用装备"],
        "求生装备": [i for i in items() if kind_of(i) == "通用装备"],
    }
    picked: list[str] = []
    for cat in picks:
        cand = pool.get(cat) or []
        if not cand:
            continue
        for _ in range(rng.randint(1, 10)):
            it = cand[rng.randrange(len(cand))]
            if it["name"] not in picked:
                picked.append(it["name"])
    return {"categories": picks, "items": picked}


def on_shelf(entry: dict, market: dict | None) -> bool:
    """这件东西现在买不买得到（夜市模式下受货架限制）。"""
    if market is None:
        return True
    allow = market.get("items")
    return not allow or entry["name"] in allow


# ---------- 库存确认（留接口） ----------


async def inventory_check(card: dict, entry: dict,
                          scene: dict | None) -> tuple[bool | None, str]:
    """DM 判断这件东西带不带得下（主人 2026-11-13：先留接口）。

    不设背包上限——重量、体积视情况而定，由 DM（LLM）判断。
    返回 ``(True, 理由)`` 带得下 / ``(False, 理由)`` 带不下 / ``(None, "")`` 未知。

    **现在恒返回 ``(None, "")``，即默认放行**：模型不可用时不能让玩家买不了
    东西（与回合层 ``degraded`` 同一口径）。以后约定好了在这里填判定，
    调用点不用改。
    """
    return (None, "")


def inventory_allows(result: tuple[bool | None, str]) -> bool:
    """把 ``inventory_check`` 的三态折成「能不能继续」。"""
    ok, _reason = result
    return ok is not False          # True 或 None 都放行


# ---------- 卡 ----------


def _card_of(conn: sqlite3.Connection, group_id: str, user_id: str):
    """返回 (卡, 是不是简易卡)；没卡返回 (None, False)。优先 RED 卡。"""
    card = roster.get(conn, group_id, user_id)
    if card is not None:
        return card, False
    return easy_roster.get(conn, group_id, user_id), True


def _persist(conn, group_id: str, user_id: str, card: dict, easy: bool) -> None:
    if easy:
        easy_roster.save(conn, group_id, user_id, card, card.get("name"))
    else:
        roster.save(conn, group_id, user_id, card)


def _gear_list(card: dict) -> list[str]:
    return [str(x) for x in (card.get("gear") or [])]


def _add_gear(card: dict, name: str) -> None:
    """加一件装备。**不设上限**（主人 2026-11-13：交 DM 判断带不带得下）。"""
    gear = _gear_list(card)
    if name not in gear:
        gear.append(name)
    card["gear"] = gear


# ---------- 路由判定 ----------


def looks_like_buy(text: str) -> bool:
    return bool(_BUY_RE.match((text or "").strip()))


def looks_like_sell(text: str) -> bool:
    return bool(_SELL_RE.match((text or "").strip()))


def looks_like_wear(text: str) -> bool:
    return bool(_WEAR_RE.match((text or "").strip()))


def looks_like_takeoff(text: str) -> bool:
    return bool(_TAKEOFF_RE.match((text or "").strip()))


def wants_haggle(text: str) -> bool:
    return bool(_HAGGLE_RE.search(text or ""))


def handles(conn: sqlite3.Connection, group_id: str, user_id: str,
            text: str, *, market: dict | None = None) -> bool:
    """这句话是不是真的在跟商店打交道（而不是戏里「买一杯酒」那种扮演）。

    买：货架上真有这件；卖/穿：身上真有这件；脱：真穿着。其余交回合层。
    """
    body = (text or "").strip()
    m = _BUY_RE.match(body)
    if m:
        raw = m.group(1).strip().strip("。！!. ")
        if not raw:
            return True
        entry = find(raw)
        return entry is not None and on_shelf(entry, market)
    card, _easy = _card_of(conn, group_id, user_id)
    if card is None:
        return False
    m = _SELL_RE.match(body)
    if m:
        want = _HAGGLE_TAIL.sub("", m.group(1)).strip("。！!. ")
        return any(_key(want) and _key(want) in _key(g) for g in _gear_list(card))
    m = _WEAR_RE.match(body)
    if m:
        want = m.group(1).strip("。！!. ")
        return any(_key(want) and _key(want) in _key(g) for g in _gear_list(card))
    if _TAKEOFF_RE.match(body):
        return bool((card.get("state") or {}).get("armor"))
    return False


# ---------- 货架 ----------


def shelf_text(conn=None, group_id: str = "", user_id: str = "",
               *, market: dict | None = None) -> str:
    """货架：按档位分组列出可买的东西，并标出你这档买不到哪些。"""
    level = 0
    if conn is not None and group_id and user_id:
        card, _easy = _card_of(conn, group_id, user_id)
        level = broker_level(card) if card else 0
    cap = growth.broker_cap(level)
    lines = [f"🏪 夜市（{cap_text(level)}）"]
    if market:
        cats = "、".join(market.get("categories") or [])
        lines.append(f"  今晚的夜市：{cats or '说不准'}（只卖摊上的东西）")
    by_tier: dict[str, list[str]] = {}
    for it in items():
        if not on_shelf(it, market):
            continue
        locked = "" if (cap is None or it["euro"] < cap) else "🔒"
        by_tier.setdefault(it["tier"], []).append(f"{it['name']}（{it['euro']}）{locked}")
    for tier, value in sorted(tiers().items(), key=lambda kv: kv[1]):
        names = by_tier.get(tier)
        if names:
            lines.append(f"  {tier} {value}：{'、'.join(names)}")
    lines.append("🔒 = 渠道买不到（要更高等级的掮客）。"
                 "「买 X」下单、「卖 X」出手、「穿 X」穿上护甲。")
    return chr(10).join(lines)


# ---------- 买 ----------


def buy(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
        market: dict | None = None, inventory_ok: bool | None = None) -> str:
    """买一件东西：查目录 → 渠道 → 钱 → 库存确认 → 入 gear。

    ``inventory_ok`` 是上层跑过 ``inventory_check`` 后折出来的结论
    （``None`` = 没跑，等同放行）。
    """
    m = _BUY_RE.match((text or "").strip())
    if not m or not m.group(1).strip():
        return "要买什么？发「商店」看货架，比如「买 中型手枪」。"
    raw = m.group(1).strip().strip("。！!. ")
    if any(w in raw for w in _QUOTE_WORDS):
        return shelf_text(conn, group_id, user_id, market=market)

    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡，先发「建卡」或「认领」拿一张。"

    entry = find(raw)
    if entry is None:
        picks = search(raw, limit=4)
        hint = ("；你要买的是：" + "、".join(f"{e['name']}（{e['euro']}）" for e in picks)
                ) if picks else "；发「商店」看货架。"
        return f"🏪 货架上没有「{raw}」{hint}"

    if not on_shelf(entry, market):
        return (f"🏪 今晚的夜市不卖「{entry['name']}」——摊上就那几样"
                f"（full.txt:25529 的夜市生成器）。等下一个夜市，或者找掮客。")

    price = int(entry["euro"])
    level = broker_level(card)
    if not growth.can_buy(price, broker_level=level):
        return (f"🏪 {entry['name']} 要 {price} 欧元（{entry['tier']}），"
                f"但{cap_text(level)}——你买不到。找个掮客，或者去夜市。"
                f"（full.txt:579-595）")

    have = growth.eb(card)
    if have < price:
        return (f"💸 买不起{entry['name']}：要 {price} 欧元，你只有 {have}"
                f"（差 {price - have}）。")

    if inventory_ok is False:
        return (f"🎒 带不下{entry['name']}：DM 说这件东西带不走。"
                f"（重量/体积由 DM 判断，本引擎不设背包上限）")

    growth.spend_eb(card, price, reason=f"购买{entry['name']}")
    _add_gear(card, entry["name"])
    _persist(conn, group_id, user_id, card, easy)

    left = growth.eb(card)
    extra = ""
    if entry.get("damage"):
        extra = f"（{entry['damage']}，{entry.get('skill', '')}）"
    elif entry.get("sp"):
        extra = f"（SP{entry['sp']}）"
    tail = "。穿上护甲就说「穿 <名字>」。" if is_armor(entry) else "。"
    return (f"🛒 买了{entry['name']}{extra}：−{price} 欧元，余额 {left}。"
            f"已放进装备{tail}")


# ---------- 卖 ----------


def _haggle(card: dict, price: int, roller) -> tuple[int, str]:
    """交易对抗（``:608-611``）：酷+交易+掮客等级 vs 对方。

    赢 → 原价；输 → 砍到 80%–90%。引擎不知道对方是谁，用「同等水平的
    掮客」近似（``:611``「他们会砍价到底价的 90%-80%」）。
    """
    from cyberpunk import dice

    roller = roller or dice.RandomRoller()
    stats = card.get("stats") or {}
    skills = card.get("skills") or {}
    cool = int(stats.get("COOL") or 0)
    trade = int(skills.get("交易") or 0)
    level = broker_level(card)
    mine = roller.roll(10) + cool + trade + level
    theirs = roller.roll(10) + cool + trade + level     # 对家同等水平（约定）
    if mine > theirs:
        return price, "（砍价赢了，拿到底价，full.txt:609）"
    span = HAGGLE_HIGH - HAGGLE_LOW
    gap = min(0.5, (theirs - mine) / 40.0)          # 差得越多压得越狠
    ratio = HAGGLE_LOW + span * (0.5 - gap)          # 钳在 80%–90%
    got = max(1, int(price * ratio))
    return got, f"（砍价输了，被压到 {int(ratio * 100)}%，full.txt:611）"


def sell(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
         haggle: bool = False, roller=None) -> str:
    """卖一件东西：从 gear 去掉 → 按原价结算（说「砍价」才走交易对抗）。"""
    m = _SELL_RE.match((text or "").strip())
    if not m:
        return "用法：卖 <东西名>（比如「卖 中型手枪」）。"
    raw = _HAGGLE_TAIL.sub("", m.group(1)).strip().strip("。！!. ")
    if not raw:
        return "要卖什么？发「档案」看装备。"

    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡，先发「建卡」或「认领」拿一张。"

    gear = _gear_list(card)
    held = next((g for g in gear if _key(g) == _key(raw)), None)
    if held is None:
        picks = [g for g in gear if _key(raw) and _key(raw) in _key(g)]
        if len(picks) == 1:
            held = picks[0]
        elif picks:
            return f"🛒 你说的是哪件？{'、'.join(picks)}"
        else:
            return (f"🎒 你身上没有「{raw}」。"
                    + (f"现有装备：{'、'.join(gear)}" if gear else "装备是空的。"))

    state = dict(card.get("state") or {})
    if (state.get("armor") or {}).get("name") == held:
        state.pop("armor", None)         # 卖了就不再穿着
        card["state"] = state

    card["gear"] = [g for g in gear if g is not held]
    _persist(conn, group_id, user_id, card, easy)

    entry = find(held)
    if entry is None:
        return (f"🛒 卖掉了「{held}」，但目录里没有它的标价"
                f"（多半是剧情道具，价格由 DM 定），没有进账。")
    price = int(entry["euro"])
    note = ""
    if haggle:
        price, note = _haggle(card, price, roller)

    growth.grant_eb(card, price, reason=f"出售{held}")
    _persist(conn, group_id, user_id, card, easy)
    tail = note or "（按原价，full.txt:602）"
    return (f"🛒 卖掉「{held}」：+{price} 欧元{tail}，余额 {growth.eb(card)}。")


# ---------- 穿 / 脱 护甲 ----------


def _armor_of(entry: dict) -> dict:
    """目录条目 → 卡上存的护甲 ``{name, sp, ev}``。"""
    return {
        "name": entry["name"],
        "sp": int(entry.get("sp") or 0),
        "ev": -int(entry.get("ref_penalty") or 0),
    }


def wear(conn: sqlite3.Connection, group_id: str, user_id: str, text: str) -> str:
    """穿上护甲：只认身上有的、且是护甲的那件。随时可穿（主人 2026-11-13）。

    书里护甲是一整套、不叠加（``full.txt:8530-8620``），所以一次只穿一套。
    """
    m = _WEAR_RE.match((text or "").strip())
    raw = (m.group(1).strip("。！!. ") if m else "")
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    if not raw:
        return "要穿哪件护甲？发「档案」看装备。"

    gear = _gear_list(card)
    held = next((g for g in gear if _key(g) == _key(raw)), None)
    if held is None:
        picks = [g for g in gear if _key(raw) and _key(raw) in _key(g)]
        held = picks[0] if len(picks) == 1 else None
    if held is None:
        return f"🎒 你身上没有「{raw}」。先「买 {raw}」。"

    entry = find(held)
    if entry is None or not is_armor(entry):
        return f"🥼 「{held}」不是护甲。"

    state = dict(card.get("state") or {})
    old = (state.get("armor") or {}).get("name")
    armor = _armor_of(entry)
    state["armor"] = armor
    card["state"] = state
    _persist(conn, group_id, user_id, card, easy)
    switched = f"（换下了「{old}」）" if old and old != armor["name"] else ""
    return (f"🥼 穿上了{armor['name']}：SP {armor['sp']}{switched}。"
            f"一次只穿一套（书里护甲是一整套，full.txt:8530-8620）。")


def takeoff(conn: sqlite3.Connection, group_id: str, user_id: str) -> str:
    """脱下护甲：随时可脱，不花时间。"""
    card, easy = _card_of(conn, group_id, user_id)
    if card is None:
        return "你还没有角色卡。"
    state = dict(card.get("state") or {})
    worn = state.pop("armor", None)
    if not worn:
        return "🥼 你现在没穿护甲。"
    card["state"] = state
    _persist(conn, group_id, user_id, card, easy)
    return f"🥼 脱下了{worn.get('name') or '护甲'}。"


# ---------- 统一入口 ----------


def reply(conn: sqlite3.Connection, group_id: str, user_id: str, text: str, *,
          market: dict | None = None, roller=None) -> str:
    """把「买/卖/穿/脱」分派到对应处理（同步路径，库存确认走默认放行）。

    需要 DM 判库存时用 ``reply_async``；两者最终落到同一批函数。
    """
    body = (text or "").strip()
    if looks_like_wear(body):
        return wear(conn, group_id, user_id, body)
    if looks_like_takeoff(body):
        return takeoff(conn, group_id, user_id)
    if looks_like_sell(body):
        return sell(conn, group_id, user_id, body, haggle=wants_haggle(body),
                    roller=roller)
    return buy(conn, group_id, user_id, body, market=market)


async def reply_async(conn: sqlite3.Connection, group_id: str, user_id: str,
                      text: str, *, market: dict | None = None,
                      scene: dict | None = None, roller=None) -> str:
    """异步版：买之前先问 DM「带不带得下」（``inventory_check``）。

    现在 ``inventory_check`` 恒返回 ``(None, "")``，所以等价于放行；
    等以后定了判定口径，这里自动生效，调用点不用改。
    """
    body = (text or "").strip()
    if not looks_like_buy(body):
        return reply(conn, group_id, user_id, body, market=market, roller=roller)

    card, easy = _card_of(conn, group_id, user_id)
    entry = None
    m = _BUY_RE.match(body)
    if card is not None and m:
        raw = m.group(1).strip().strip("。！!. ")
        if raw and not any(w in raw for w in _QUOTE_WORDS):
            entry = find(raw)
    if entry is None:
        return buy(conn, group_id, user_id, body, market=market)

    verdict = await inventory_check(card, entry, scene)
    return buy(conn, group_id, user_id, body, market=market,
               inventory_ok=(None if inventory_allows(verdict) else False))
