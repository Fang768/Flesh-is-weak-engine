"""十项属性与派生属性 —— **赛博朋克红（RED）核心规则**。

出处：``rules/red_core/raw/full.txt`` 书内页 72–80（PDF p90–98）。

关键公式（全部逐字核对过原书）：

* ``HP   = 10 + 5 × ⌈(BODY + WILL) / 2⌉``
* ``重伤 = ⌈HP / 2⌉``（生命值少于这个数就进入重伤，全检定 −2）
* ``死亡豁免 = 体格（BODY）``
* ``人性 HUM = 10 × 共情（EMP）``；人性每掉 10 点，共情 −1
* ``幸运值上限 = 运气（LUCK）``；每章开始补满
* 移动 = ``MOVE×2`` 米/回合

属性中文名采用 2077 简中口径（酷 = 镇定、体格 = BODY）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 书里属性表顺序：智力 反应 敏捷 技术 镇定 意志 幸运 移动 体格 共情
STAT_ORDER: tuple[str, ...] = (
    "INT",
    "REF",
    "DEX",
    "TECH",
    "COOL",
    "WILL",
    "LUCK",
    "MOVE",
    "BODY",
    "EMP",
)

STAT_ZH: dict[str, str] = {
    "INT": "智力",
    "REF": "反应",
    "DEX": "敏捷",
    "TECH": "技术",
    "COOL": "镇定",
    "WILL": "意志",
    "LUCK": "幸运",
    "MOVE": "移动",
    "BODY": "体格",
    "EMP": "共情",
}

# 书 p90 的四组
STAT_GROUPS: dict[str, tuple[str, ...]] = {
    "心智": ("INT", "WILL", "COOL", "EMP"),
    "战斗能力": ("TECH", "REF"),
    "运气组": ("LUCK",),
    "身体素质": ("BODY", "DEX", "MOVE"),
}

STAT_MIN = 2
STAT_MAX = 10


@dataclass(frozen=True, slots=True)
class DerivedStats:
    """派生属性。RED 只把 HP 和人性算作派生，其余是直接查表得到的常用值。"""

    hp: int  # 生命值 10 + 5×⌈(BODY+WILL)/2⌉
    wound: int  # 重伤阈值 ⌈HP/2⌉
    death_save: int  # 死亡豁免 = BODY
    hum: int  # 人性 = EMP×10
    luck: int  # 幸运值上限 = LUCK
    run: int  # 奔跑 MOVE×2（米/回合）
    leap: int  # 跳跃 MOVE（米）


def _ceil_half(value: int) -> int:
    return -(-value // 2)


def derived(stats: dict[str, int], *, hp: int | None = None) -> DerivedStats:
    """按书 p79–80 的公式算派生属性。

    ``hp`` 可显式覆盖（备用卡、NPC、模板卡面上写死的值）。
    """
    try:
        s = {k: int(stats[k]) for k in STAT_ORDER}
    except KeyError as exc:  # pragma: no cover - 参数错误
        raise KeyError(f"缺少属性 {exc.args[0]!r}，需要十项：{STAT_ORDER}") from exc

    real_hp = int(hp) if hp else 10 + 5 * _ceil_half(s["BODY"] + s["WILL"])
    return DerivedStats(
        hp=real_hp,
        wound=_ceil_half(real_hp),
        death_save=s["BODY"],
        hum=s["EMP"] * 10,
        luck=s["LUCK"],
        run=s["MOVE"] * 2,
        leap=s["MOVE"],
    )


def validate_stats(stats: dict[str, int]) -> None:
    """属性值域 2–10（人类正常范围 2–8，改造/极端可到 10）。"""
    for key in STAT_ORDER:
        if key not in stats:
            raise ValueError(f"缺少属性 {key}")
        value = stats[key]
        if not STAT_MIN <= value <= STAT_MAX:
            raise ValueError(f"{key}={value} 越界，应在 {STAT_MIN}–{STAT_MAX}")
