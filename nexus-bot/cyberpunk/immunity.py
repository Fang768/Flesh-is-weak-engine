"""义体免疫：哪些义体让人对哪类负面效果免疫。

数据源 ``rules/red_core/data/cyberware_immunity.json``，规则全部对过原文
（``rules/red_core/raw/full.txt``）：

* **防眩晕** ``:9871-9874``：「使用者免疫闪光造成的负面效果。需要赛博义眼基础，
  并且需要在两只眼睛都安装。」
* **水平阻尼降噪** ``:10019-10022``：「使用者对巨大噪声引发的负面作用免疫。」
* **鼻滤器** ``:10130-10135``：「对毒气，废气等类似的危险效果免疫。」
* **鳃** ``:10101-10105``：「使用者能够在水下呼吸。」
* **紫外（微光/红外/紫外）** ``:9922-9929``：「无视黑暗，烟雾，雾气等造成的惩罚。
  需要双眼赛博化，强制双眼安装。」
* **独立供氧** ``:10112-10117``：「提供维生 30 分钟的氧气。需要 1 小时吸气补充。」
* **硬化防护** ``:10412-10418``：「赛博义肢及其中安装的附件无法被 EMP 冲击或
  非黑冰程序无效化。需要安装赛博义肢。」
* **雷达/声呐植入** ``:10136-10139``：「扫描 50m 以内的地形。扫描无法穿透掩体。」

设计要点：

* 免疫是**按类别**匹配的（``闪光``/``噪声``/``毒气``/``火焰``…），不是按具体物件名。
  剧本写 ``"negate_if": ["闪光"]`` 就能被「防眩晕」豁免，不用记义体全名。
* 单向：义体给免疫，修正条目声明它属于哪个类别（``category`` 或 ``negate_if``）。
* 三条有「双眼」门槛（防眩晕/紫外），用 ``slots.needs_both_eyes`` 判定；本模块只
  在**已装**的前提下判定，双眼校验交给 install 阶段（``slots.check_install``）。
* 「约定」：免疫范围按书里的措辞取**最宽合乎原文**的解释，不额外扩展。例如
  鼻滤器免毒气/废气，但不免毒物中和那类「中毒检定」——
  ``:10140-10145`` 的毒物中和植入给的是技能 +2，不是免疫。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = (Path(__file__).resolve().parents[2]
             / "rules" / "red_core" / "data" / "cyberware_immunity.json")


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA_FILE.exists():
        return {"items": {}, "aliases": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def items() -> dict[str, dict]:
    return dict(_data().get("items", {}))


def aliases() -> dict[str, list[str]]:
    """写法别名：剧本/数据里可能用别的叫法指同一件义体。"""
    return {k: [str(x) for x in v] for k, v in _data().get("aliases", {}).items()}


def categories_of(cyberware: str) -> list[str]:
    """这件义体免疫哪些类别（按名字查，认别名）。"""
    meta = items().get(str(cyberware))
    if meta is None:
        for canon, names in aliases().items():
            if str(cyberware) in names:
                meta = items().get(canon)
                break
    if not meta:
        return []
    return [str(x) for x in meta.get("immunes") or []]


def requires_both_eyes(cyberware: str) -> bool:
    meta = items().get(str(cyberware)) or {}
    return bool(meta.get("both_eyes"))


def installed_immunities(card: dict) -> dict[str, str]:
    """这张卡装上后免疫哪些类别 → 来源义体名。

    已装义体从 ``state.cyber``（RED 卡）与顶层 ``cyber``（简易卡）两处取，
    与 ``combat._card_tags`` 的口径一致。
    """
    from . import cyber_bonus

    names = cyber_bonus.installed_names(card)
    out: dict[str, str] = {}
    for name in names:
        for cat in categories_of(name):
            out.setdefault(cat, name)
    return out


def is_immune(card: dict, category: str) -> str:
    """这张卡是否免疫 ``category``；返回提供免疫的义体名（不免疫返回空串）。"""
    return installed_immunities(card).get(str(category), "")


def negate_tokens(card: dict) -> list[str]:
    """给 ``combat._negated`` 用的豁免 token 列表。

    返回该卡能用来豁免修正条目的所有写法：义体名本身 + 其免疫类别。
    这样剧本写 ``negate_if: ["水平阻尼降噪"]`` 或 ``["噪声"]`` 都能生效。
    """
    tokens: list[str] = []
    for cat, source in installed_immunities(card).items():
        tokens.append(cat)
        tokens.append(source)
    return tokens


def describe(card: dict) -> list[str]:
    """给 ``/义体`` 面板：列出免疫项。"""
    out: list[str] = []
    for cat, source in installed_immunities(card).items():
        out.append(f"{source}：免疫{cat}")
    return out
