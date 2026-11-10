"""RED 技能表：66 项技能的名字 → 关联属性 / 分类 / 是否 ×2 双倍点数。

数据源 ``rules/red_core/data/skills.json``（``full.txt:6935-7139``，书内页 82–85）。

三套规则的名字不一样，这里做**统一归一**：

* **RED 正典**（本模块，66 项）：``藏匿&揭示物体`` / ``折磨&药物耐受`` /
  ``长枪射击`` / ``个人装扮`` / ``服饰&风格`` / ``赛博技术``
* **Easy Mode**（``easy.SKILL_STAT``，42 项）：``藏匿/搜寻物品`` /
  ``抵抗拷问/药物`` / ``抵肩枪械`` / ``衣着与风格``
* **Cyberpunk V3**（``names.skill_stat()``，95 项）：``藏匿与脱身`` /
  ``抗性（拷问/药物）`` / ``步枪``

``canonical()`` 把任意一种写法折成 RED 正典名，``stat_of()`` 再查属性。

「约定」（规则书没写，本仓库口径）：

* 同名技能在三套规则里属性不同时，**RED 优先**（本模块先查）：
  ``察言观色`` RED=EMP（V3 是 COOL）、``乐器演奏`` RED=TECH（V3 是 INT）、
  ``驯兽`` RED=INT（V3 是 COOL）。这是「按 RED 规则算」的落地口径。
* 属性键统一用引擎内部的十项（INT/REF/DEX/TECH/COOL/WILL/LUCK/MOVE/BODY/EMP）；
  V3 里的 ``CON``/``STR`` 是 Fuzion 独有，RED 没有，遇到就落到 INT。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_FILE = (Path(__file__).resolve().parents[2]
             / "rules" / "red_core" / "data" / "skills.json")

#: 三套规则的写法 → RED 正典名。只放**不通名**的别名；同名同义的不用列。
ALIASES: dict[str, str] = {
    # Easy Mode 写法
    "藏匿/搜寻物品": "藏匿&揭示物体",
    "抵抗拷问/药物": "折磨&药物耐受",
    "抵肩枪械": "长枪射击",
    "衣着与风格": "服饰&风格",
    "电子/安防技术": "电子/安全技术",
    "徒手搏斗": "搏击",
    # Easy Mode 的「医疗」= RED 的「强化急救 Paramedic」（full.txt:7124-7127
    # 「治疗所有不需要手术的严重伤势」；Easy 书 p14「用于治疗所有不需要外科手术的重伤」）
    "医疗": "强化急救",
    # Easy 书里「外科手术 DV17」用来处理断肢这类重伤，而 RED 的门槛技能就是
    # 强化急救（Paramedic，`full.txt:7124-7127`「治疗所有不需要手术的严重伤势」）。
    # RED 里「外科手术」是技医职业能力「医疗圣手」下的子项（`:2552-2557`），
    # 不是独立技能，所以折到强化急救。
    "外科手术": "强化急救",
    "官僚世故": "官场世故",
    "商业": "商学",
    "会计": "会计学",
    "创作": "写作",
    "教育": "教育通识",
    "本地专家": "地方专家",
    # V3 写法
    "藏匿与脱身": "藏匿&揭示物体",
    "抗性（拷问/药物）": "折磨&药物耐受",
    "读唇": "唇语",
    "跟踪": "追踪",
    "监视": "觉察",
    "闪避与脱身": "闪避",
    "游泳": "运动",
    "耐力": "忍耐",
    "步枪": "长枪射击",
    "冲锋枪": "自动武器",
    "重型武器": "重武器",
    "重炮操作": "重武器",
    "伪装": "扮演",
    "表演": "扮演",
    "仪容打理": "个人装扮",
    "社交礼仪": "个人装扮",
    "访谈套话": "交谈",
    "统御": "说服",
    "诱惑": "说服",
    "演说": "说服",
    "威吓": "审讯",
    "说服与花言巧语": "说服",
    "电子学": "电子/安全技术",
    "电子安防": "电子/安全技术",
    "义体技术": "赛博技术",
    "武器匠": "武器技术",
    "基础技术": "基础技术",
    "爆破": "爆破学",
    "撬锁": "开锁",
    "绘画": "绘画/速写/雕刻",
    "摄影与电影": "摄影/摄像",
    "医疗技术": "强化急救",
    "驾驶（汽车）": "地面载具驾驶",
    "驾驶（摩托）": "地面载具驾驶",
    "驾驶（旋翼机）": "空中载具驾驶",
    "驾驶（固定翼）": "空中载具驾驶",
    "驾驶（飞艇）": "空中载具驾驶",
    "驾驶（矢量推力）": "空中载具驾驶",
    "驾驶（潜水器）": "海上载具驾驶",
    "航空技术": "空中载具技术",
    "飞行器技术": "空中载具技术",
    "旋翼机技术": "空中载具技术",
    "通识教育": "教育通识",
    "专家知识": "地方专家",
    "市场交易": "交易",
}

#: 归一前的机械清洗：去掉分隔符周围的空格（``藏匿& 揭示物体`` → ``藏匿&揭示物体``）
_PAIRS = (("& ", "&"), (" &", "&"), ("/ ", "/"), (" /", "/"))


@lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    if not DATA_FILE.exists():
        return ()
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(data.get("skills", ()))


@lru_cache(maxsize=1)
def skill_table() -> dict[str, str]:
    """RED 正典技能名 → 关联属性。"""
    return {str(r["zh"]): str(r["stat"]) for r in _rows()}


@lru_cache(maxsize=1)
def skill_category() -> dict[str, str]:
    """RED 正典技能名 → 九大类。"""
    return {str(r["zh"]): str(r.get("cat") or "") for r in _rows()}


@lru_cache(maxsize=1)
def skill_x2() -> dict[str, bool]:
    """RED 正典技能名 → 是否 ×2（升级要双倍点数，``full.txt:6881-6887``）。"""
    return {str(r["zh"]): bool(r.get("x2")) for r in _rows()}


def _clean(text: str) -> str:
    out = (text or "").strip()
    for a, b in _PAIRS:
        out = out.replace(a, b)
    return out


@lru_cache(maxsize=512)
def canonical(name: str) -> str:
    """把任意写法的技能名折成 RED 正典名；认不出就原样返回清洗后的文本。"""
    text = _clean(name)
    if not text:
        return ""
    table = skill_table()
    if text in table:
        return text
    if text in ALIASES:
        return ALIASES[text]
    # 再试一层：别名表的键也做清洗（比如玩家手打「藏匿& 揭示物体」）
    for key, value in ALIASES.items():
        if _clean(key) == text:
            return value
    # 最后按包含关系找唯一命中（「手枪」vs「手枪射击」这类）
    hits = [k for k in table if text in k or k in text]
    if len(hits) == 1:
        return hits[0]
    return text


def stat_of(name: str) -> str | None:
    """技能名（任意写法）→ 关联属性；认不出返回 None。"""
    return skill_table().get(canonical(name))


def category_of(name: str) -> str | None:
    return skill_category().get(canonical(name))


def is_x2(name: str) -> bool:
    return bool(skill_x2().get(canonical(name)))
