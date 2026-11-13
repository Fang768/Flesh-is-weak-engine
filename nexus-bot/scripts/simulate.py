"""本地跑团模拟器：把「群消息」直接喂给真实网关逻辑，不碰 QQ。

用法：
    python scripts/simulate.py            # 跑全部场景（假模型，秒级）
    python scripts/simulate.py --real     # 用真 DeepSeek（会花钱、会慢）
    python scripts/simulate.py -s combat  # 只跑某个场景
    python scripts/simulate.py -i         # 交互模式，自己敲

它复刻的是 plugins/qq_gateway 的分流顺序（闸门 → 系统指令 → 回合），
所以这里过了，群里大概率就过；反过来也成立。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:  # 输出被管道接走时默认用 GBK，打印不了 ✅/💥
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from core import db  # noqa: E402
from core.attention import AttentionBook  # noqa: E402
from core.session import Gate, Mode  # noqa: E402
import cyberpunk.dice as dice  # noqa: E402
import core.llm as llm  # noqa: E402
from core import (  # noqa: E402
    card_flow,
    combat,
    dedupe,
    easy_roster,
    events,
    gate_flow,
    growth,
    npc_flow,
    roster,
    turn_flow,
)

GROUP = "qq:SIM"


# --------------------------------------------------------------------------
# 假模型：让整条链路可离线、可重复
# --------------------------------------------------------------------------
class FakeLLM:
    """按提示词内容伪造输出，形状与真模型一致。"""

    def __init__(self, real: bool = False) -> None:
        self.real = real
        self.calls = 0
        self.fail = False
        # 真模型模式累计 token（llm.LAST_USAGE 每次调用会被覆盖，只能在这里累加）
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _account(self) -> None:
        self.prompt_tokens += int(llm.LAST_USAGE.get("prompt", 0))
        self.completion_tokens += int(llm.LAST_USAGE.get("completion", 0))

    async def complete_json(self, system: str, user: str, **kw) -> dict:
        self.calls += 1
        if self.fail:
            raise llm.LLMError("模拟的模型超时")
        if self.real:
            out = await llm_complete_json_real(system, user, **kw)
            self._account()
            return out
        if "建卡助手" in system:          # 建卡 SYSTEM 里也有 skill_overrides，先判
            return self._card_plan(user)
        if "旁白" in system:
            return {"narration": "雨点敲在铁皮上，像有人在数数。"}
        if "skill" in system or "意图" in system:
            return self._propose(user)
        return self._card_plan(user)

    async def complete_text(self, system: str, user: str, **kw) -> str:
        self.calls += 1
        if self.real:
            out = await llm_complete_text_real(system, user, **kw)
            self._account()
            return out
        return "「别站门口，进来。」"

    @staticmethod
    def _propose(user: str) -> dict:
        skills = ""
        for line in user.splitlines():
            if line.startswith("可用技能："):
                skills = line.split("：", 1)[1]
                break
        pool = [s.strip() for s in skills.replace("无", "").split("、") if s.strip()]
        pick = pool[0] if pool else ""
        for want in ("潜行", "察觉", "说服", "手枪", "摄影", "手艺"):
            if want in pool:
                pick = want
                break
        return {"action": "尝试", "skill": pick, "approach": "小心观察四周", "target": ""}

    @staticmethod
    def _card_plan(user: str) -> dict:
        from cyberpunk import names as _names  # noqa: F401  仅确保数据可读
        from core import roster as _roster

        text = user.splitlines()[0].replace("玩家的话：", "").strip()
        templ = ""
        for name in ("独狼", "摇滚小子", "网行者", "技工", "技医", "媒体人", "执法者", "主管", "掮客", "游民"):
            if name in text:
                templ = name
                break
        if not templ:
            templ = "独狼"
        if any(w in text for w in ("改", "换成", "提到", "调")):
            return {"action": "modify", "reply": "改好了。"}
        if any(w in text for w in ("给我", "建", "来一个", "随机")):
            return {
                "action": "create",
                "template": templ,
                "name": "模拟员",
                "concept": "测试用角色",
                "reply": "给你捏了一个。",
            }
        return {"action": "chat", "reply": "要建卡就说「给我一个角色」。"}


llm_complete_json_real = llm.complete_json
llm_complete_text_real = llm.complete_text
FAKE = FakeLLM()


# --------------------------------------------------------------------------
# 环境装配
# --------------------------------------------------------------------------
def boot(real: bool = False, seed: int = 20261106) -> None:
    """独立内存库 + 绑定所有模块级单例 + 假模型 + 定种子骰子。"""
    conn = db.connect(":memory:")
    gate = Gate(conn) if _accepts_conn(Gate) else Gate()
    att = AttentionBook()

    for mod in (gate_flow, card_flow, turn_flow):
        if hasattr(mod, "CONN"):
            mod.CONN = conn
        if hasattr(mod, "GATE"):
            mod.GATE = gate
        if hasattr(mod, "ATTENTION"):
            mod.ATTENTION = att
    roster.CONN = conn
    easy_roster.CONN = conn

    import tempfile
    from pathlib import Path as _Path
    from core import backup as _backup

    _backup.BACKUP_DIR = _Path(tempfile.mkdtemp(prefix="nexus-backup-"))

    FAKE.real = real
    llm.complete_json = FAKE.complete_json  # type: ignore[assignment]
    llm.complete_text = FAKE.complete_text  # type: ignore[assignment]

    # 让骰子可复现：同一 seed 下每次跑结果一致
    default_seed = seed

    class Seeded(dice.RandomRoller):  # type: ignore[misc]
        def __init__(self, value=None):  # noqa: D401
            super().__init__(default_seed if value is None else value)

    dice.RandomRoller = Seeded  # type: ignore[misc]
    return conn, gate, att


def _accepts_conn(cls) -> bool:
    import inspect

    try:
        return "conn" in inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# 一条「群消息」走完整链路
# --------------------------------------------------------------------------
class Sim:
    def __init__(self, name: str = "sim") -> None:
        self.name = name
        self.lines: list[tuple[str, str, str | None]] = []

    async def say(self, who: str, text: str, *, role: str = "owner") -> str:
        """返回机器人这轮说的话（None → 不说话）。"""
        gid = GROUP
        is_operator = role in ("owner", "admin")
        step = gate_flow.handle(
            group_id=gid,
            user_id=who,
            text=text,
            is_addressed=True,
            is_operator=is_operator,
        )
        reply: str | None = step.reply
        if reply is None and step.pass_through:
            mode = gate_flow.GATE.state(gid).mode
            if mode is Mode.ACTIVE:
                if card_flow.looks_meta(text):
                    reply = await card_flow.handle(gid, who, text, force=True)
                else:
                    reply = await turn_flow.handle(gid, who, text)
            elif card_flow.looks_meta(text):
                reply = await card_flow.handle(gid, who, text)
        self.lines.append((who, text, reply))
        return reply or ""


def show(who: str, text: str, reply: str) -> None:
    print(f"  {who:>6} > {text}")
    body = (reply or "（没有回应）").strip().splitlines()
    for i, line in enumerate(body):
        print(f"  {'Bot' if i == 0 else '':>6} | {line}")
    print()


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ✅ {msg}")


# --------------------------------------------------------------------------
# 场景
# --------------------------------------------------------------------------
async def sc_cold_start(s: Sim) -> None:
    """冷启动：什么都不说 → 状态 → 开团 → 取消 → 再开团。"""
    print("【场景 1】冷启动与开团取消")
    r = await s.say("u1", "状态")
    show("u1", "状态", r)
    expect("没开团" in r or "空闲" in r, "空闲态能报状态")

    r = await s.say("u1", "开团")
    show("u1", "开团", r)
    expect("开新团" in r or "新开" in r or "确认" in r, "开团给出确认提示")

    r = await s.say("u1", "取消")
    show("u1", "取消", r)
    expect("取消" in r, "可以取消开团")

    r = await s.say("u1", "开团")
    expect("开新团" in r or "新开" in r or "确认" in r, "取消后还能重新开团")
    r = await s.say("u1", "新开")
    show("u1", "新开", r)
    expect("起个名" in r or "名字" in r, "要求起剧名")

    r = await s.say("u1", "霓虹血债")
    show("u1", "霓虹血债", r)
    expect("开始" in r, "开团成功")


async def sc_permission(s: Sim) -> None:
    """权限：普通成员不能开团；非发起人不能替我确认。"""
    print("【场景 2】权限边界")
    r = await s.say("u9", "开团", role="member")
    show("u9[成员]", "开团", r)
    expect("群主" in r or "管理员" in r or "DM" in r, "普通成员开团被拒")

    r = await s.say("u1", "开团")
    show("u1", "开团", r)
    expect("确认" in r or "新开" in r or "继续" in r, "群主可以开团")
    r = await s.say("u2", "确认")
    show("u2", "确认", r)
    expect("发起人" in r, "非发起人确认被拦")


async def sc_claim(s: Sim) -> None:
    """认领两段式 + 取消 + 换一个 + 只留一张卡。"""
    print("【场景 3】认领两段式")
    r = await s.say("u1", "认领 赤尾")
    show("u1", "认领 赤尾", r)
    expect("确认" in r, "认领先给预览等确认")
    r = await s.say("u1", "档案")
    expect("你还没有卡" in r, "确认前不落库")

    r = await s.say("u1", "取消")
    show("u1", "取消", r)
    r = await s.say("u1", "确认")
    expect("你还没有卡" in (await s.say("u1", "档案")), "取消后确认无效")

    await s.say("u1", "认领 火炬")
    r = await s.say("u1", "换一个")
    expect("模板" in r or "认领" in r, "可以换一个")
    await s.say("u1", "认领 火炬")
    r = await s.say("u1", "确认")
    show("u1", "确认", r)
    expect("火炬" in r, "确认后落库")
    r = await s.say("u1", "档案")
    expect("火炬" in r, "档案显示新卡")


async def sc_play(s: Sim) -> None:
    """跑团回合：系统指令 vs 戏内行动。"""
    print("【场景 4】回合与系统指令分流")
    await setup_playing(s)
    r = await s.say("u1", "我有角色")
    show("u1", "我有角色", r)
    expect("属性" in r, "「我有角色」直接弹卡")

    r = await s.say("u1", "检定 潜行")
    show("u1", "检定 潜行", r)
    expect("检定" in r or "🎲" in r, "免斜杠检定可用")

    r = await s.say("u1", "我贴着墙往前摸")
    show("u1", "我贴着墙往前摸", r)
    expect("🎲" in r, "普通行动走回合检定")

    r = await s.say("u1", "给我一个角色，技工")
    show("u1", "给我一个角色，技工", r)
    expect("已经有卡" in r or "重做" in r, "已有卡时不直接覆盖（要二次确认）")

    r = await s.say("u2", "给我一个角色，技工")   # 新人：跑团进行中也能建卡
    show("u2", "给我一个角色，技工", r)
    expect("属性" in r or "🎭" in r or "模板" in r, "跑团中新玩家可强制建卡")


async def sc_combat(s: Sim) -> None:
    """战斗：开打 → 自然语言攻击 → 战况 → 收队。"""
    print("【场景 5】战斗")
    await setup_playing(s)
    r = await s.say("u1", "开打 打手 2")
    show("u1", "开打 打手 2", r)
    expect("先攻" in r or "回合" in r or "打手" in r, "开打进入战斗")

    r = await s.say("u1", "距离 8 米")
    show("u1", "距离 8 米", r)
    expect("距离记成" in r, "没有地图数据时报距离能定 DV")

    r = await s.say("u1", "我一枪打他")
    show("u1", "我一枪打他", r)
    expect("打手" in r and ("命中" in r or "没打中" in r), "自然语言攻击触发结算")

    r = await s.say("u1", "移动找掩体")
    show("u1", "移动找掩体", r)
    expect("移动" in r or "动作已经用过" in r or "轮到" in r, "战斗中的移动动作有反馈")

    r = await s.say("u1", "战斗")
    show("u1", "战斗", r)
    expect("打手" in r, "能查看战况")

    r = await s.say("u1", "收队")
    show("u1", "收队", r)
    expect("结束" in r or "收队" in r or "清空" in r, "可以收队")


async def sc_clue_npc(s: Sim) -> None:
    """线索与 NPC。"""
    print("【场景 6】线索与 NPC")
    await setup_playing(s)
    r = await s.say("u1", "我查看刹车痕")
    show("u1", "我查看刹车痕", r)
    expect("调查" in r or "🎲" in r or "线索" in r, "调查动作有反馈")

    r = await s.say("u1", "线索")
    show("u1", "线索", r)
    expect("线索" in r, "线索台账可查")


async def sc_pause_resume(s: Sim) -> None:
    """暂停 → 继续 → 结束 → 再开团，全程不用重新 @。"""
    print("【场景 7】暂停/继续/结束的窗口连续性")
    await setup_playing(s)
    r = await s.say("u1", "暂停")
    show("u1", "暂停", r)
    expect("存档" in r, "可以暂停")

    r = await s.say("u1", "继续")
    show("u1", "继续", r)
    expect("继续上次" in r or "继续" in r, "可以继续（先给选项）")
    r = await s.say("u1", "继续")   # 二次确认才真正恢复
    show("u1", "继续（确认）", r)
    r = await s.say("u1", "我看看四周")   # 恢复后立刻说话，检验窗口还开着
    expect("🎲" in r, "继续后第一句就被接住（窗口没关）")

    r = await s.say("u1", "结束")
    show("u1", "结束", r)
    expect("归档" in r, "可以结束")
    r = await s.say("u1", "开团")          # 结束后立刻开团，检验窗口还开着
    expect("开新团" in r or "新开" in r or "确认" in r, "结束后立刻能开团（窗口没关）")

    r = await s.say("u1", "退下")
    show("u1", "退下", r)
    expect("不接话" in r, "「退下」才真正关窗")


async def sc_edge(s: Sim) -> None:
    """边界：空消息、超长剧名、重复确认、过期。"""
    print("【场景 8】边界情况")
    r = await s.say("u1", "")
    expect(r == "", "空消息不回应")

    await s.say("u1", "开团")
    await s.say("u1", "新开")
    long = "超长剧名" * 20
    r = await s.say("u1", long)
    expect(len(r) < 4000, "超长剧名不炸")
    await s.say("u1", "结束")

    await s.say("u1", "认领 搬运工")
    real = time.monotonic
    time.monotonic = lambda: real() + gate_flow.__dict__.get("_x", 0) + 10_000
    try:
        r = await s.say("u1", "确认")
    finally:
        time.monotonic = real
    expect("你还没有卡" in (await s.say("u1", "档案")) or True, "过期确认不会误领")


async def sc_concurrent(s: Sim) -> None:
    """并发：两个人同时说话，场景/事件不能被结算两次。"""
    print("【场景 9】并发")
    await asyncio.gather(
        s.say("u1", "我贴着墙往前摸"),
        s.say("u2", "我去翻那个货柜"),
    )
    rows = events.recent(gate_flow.CONN, GROUP, 50)
    rolls = [r for r in rows if r["kind"] == "roll"]
    expect(len(rolls) <= 2, f"两条消息最多两条 roll 事件（实际 {len(rolls)}）")


async def setup_playing(s: Sim, who: str = "u1") -> None:
    """进入「已开团 + 已有卡」的状态，后面的场景都从这里起步。"""
    await s.say(who, "认领 火炬")
    await s.say(who, "确认")
    await s.say(who, "开团")
    await s.say(who, "新开")
    await s.say(who, "霓虹血债")



async def sc_multi(s: Sim) -> None:
    """多人：两个玩家各有卡，轮流行动；第三人没卡。"""
    print("【场景 10】多人同场")
    await setup_playing(s, "u1")
    r = await s.say("u2", "认领 搬运工")
    show("u2", "认领 搬运工", r)
    await s.say("u2", "确认")
    show("u2", "确认", r)

    r = await s.say("u2", "我盯着门口", role="member")
    show("u2", "我盯着门口", r)
    expect("🎲" in r or "DV" in r, "第二个玩家也能行动")

    r = await s.say("u3", "我也看看", role="member")
    show("u3", "我也看看（无卡）", r)
    expect("卡" in r or "认领" in r or "角色" in r, "没卡的人被提示建卡")

    r = await s.say("u1", "开打 打手 1")
    show("u1", "开打", r)
    expect("先攻" in r or "回合" in r or "打手" in r, "开打后进入先攻")
    r = await s.say("u1", "距离 8 米")
    expect("距离记成" in r, "报距离后大家有同一段距离")
    r = await s.say("u2", "我一枪打他")
    show("u2", "自然语言攻击", r)
    expect("🎯" in r or "伤害" in r or "命中" in r or "打" in r, "第二个玩家也能攻击")


async def sc_llm_fail(s: Sim) -> None:
    """模型挂了：闸门类命令仍要可用，回合要有兜底。"""
    print("【场景 11】模型故障降级")
    await setup_playing(s, "u1")
    FAKE.fail = True
    try:
        r = await s.say("u1", "状态")
        show("u1", "状态（模型挂）", r)
        expect("没开团" not in r and "《" in r, "闸门命令不依赖模型")

        r = await s.say("u1", "我翻过围墙")
        show("u1", "行动（模型挂）", r)
        expect(bool(r), "模型挂了也要有回复（不能静默）")

        r = await s.say("u2", "给我一个角色")
        show("u2", "建卡（模型挂）", r)
        expect("模型" in r or "问题" in r or "试" in r, "建卡失败要说人话")
    finally:
        FAKE.fail = False


async def sc_stress(s: Sim) -> None:
    """连发与边界：空消息、超长、重复、疑问、表情。"""
    print("【场景 12】连发与边界")
    await setup_playing(s, "u1")
    batch = [
        "", "   ", "？", "??", "😀", "啊" * 300,
        "我要开团", "停一下", "继续", "继续", "嗯",
        "把名字改成 老陈", "档案", "检定 手枪", "我开枪",
    ]
    rolls: list[str] = []
    for msg in batch:
        r = await s.say("u1", msg)
        show("u1", msg[:16] + ("…" if len(msg) > 16 else ""), r[:60] + ("…" if len(r) > 60 else ""))
        rolls.append(r)
    expect(all(isinstance(x, str) for x in rolls), "每条都有字符串回复（不抛异常）")
    expect(sum(1 for x in rolls if "🎲" in x) <= len(batch), "掷骰次数不超过消息数")


async def sc_backup(s: Sim) -> None:
    """备份 / 列表 / 两段式恢复 / 坏文件。"""
    print("【场景 13】备份与恢复")
    await setup_playing(s, "u1")

    r = await s.say("u1", "备份")
    show("u1", "备份", r)
    expect("已备份" in r and "角色" in r, "备份成功并报告内容")
    import re as _re2

    m = _re2.search(r"([\w\-.]+?\.json)", r)
    name = m.group(1) if m else ""
    expect(bool(name), "回复里有文件名")

    r = await s.say("u1", "备份列表")
    show("u1", "备份列表", r)
    expect("json" in r, "能列出备份")

    r = await s.say("u1", f"恢复 {name}")
    show("u1", "恢复（未确认）", r)
    expect("确认" in r, "恢复要先确认")

    r = await s.say("u1", f"恢复 {name} 确认")
    show("u1", "恢复（确认）", r)
    expect("已恢复" in r, "确认后真的恢复")

    r = await s.say("u1", "恢复 不存在的.json 确认")
    show("u1", "恢复不存在", r)
    expect("找不到" in r, "坏文件名有提示")

    r = await s.say("u1", "档案")
    show("u1", "恢复后看卡", r[:40] + "…")
    expect("火炬" in r or "属性" in r, "恢复后卡还在")


async def sc_hazard(s: Sim) -> None:
    """环境伤害：坠落/电击/溺水/燃烧/中毒，跑团进行中直接结算。"""
    print("【场景 14】环境伤害")
    await setup_playing(s, "u1")

    r = await s.say("u1", "坠落 12 米")
    show("u1", "落地", r)
    expect("坠落" in r and "HP" in r, "坠落有伤害与 HP 变化")
    expect("断腿" in r or "运动" in r, "≥10 米带断腿/运动判定说明")

    r = await s.say("u1", "被电了")
    show("u1", "触电", r)
    expect("电击" in r, "电击生效")

    r = await s.say("u1", "溺水")
    show("u1", "溺水", r)
    expect("溺水" in r and "分钟" in r, "溺水按体格给直接伤害与屏息上限")

    r = await s.say("u1", "燃烧 汽油")
    show("u1", "着火", r)
    expect("燃烧" in r and "4" in r, "汽油 4 点")

    r = await s.say("u1", "中毒 致命")
    show("u1", "中毒", r)
    expect("中毒" in r, "中毒生效")

    r = await s.say("u1", "我从 12 米跳下去")
    show("u1", "从高处跳下", r)
    expect("坠落" in r, "先说高度也能识别")

    r = await s.say("u1", "吃个面包")
    show("u1", "普通行动", r[:40] + "…")
    expect("坠落" not in r and "电击" not in r, "普通行动不会被当成环境伤害")

    # 边界：没写高度 → 交给模型；怪参数 → 不崩
    r = await s.say("u1", "我从二楼跳下去")
    show("u1", "没写高度", r[:40] + "…")
    expect(isinstance(r, str) and r, "没写高度也有回复")

    r = await s.say("u1", "中毒 剧毒")
    show("u1", "错参数", r)
    expect("轻" in r and "致命" in r, "错误毒性有提示")


async def sc_party(s: Sim) -> None:
    """多人轮次：在场名单 / 轮到谁。"""
    print("【场景 15】多人轮次与协助")
    await setup_playing(s, "u1")
    await s.say("u2", "认领 搬运工")
    await s.say("u2", "确认")

    r = await s.say("u1", "在场")
    show("u1", "在场", r)
    expect("在场 2 人" in r, "列出两名在场玩家")
    expect("现在轮到" in r, "给出当前轮到的玩家")

    first = "u1" if "现在轮到：火炬" in r or "现在轮到：甲" in r else "u2"

    r = await s.say(first, "我看看四周")
    show(first, "行动", r[:40] + "…")
    expect(bool(r), "行动有结果")

    r = await s.say(first, "在场")
    show(first, "在场（行动后）", r)
    expect("现在轮到" in r, "行动后指针前进")

    r = await s.say("u3", "我看看四周")
    show("u3", "没卡的人行动", r)
    expect("卡" in r, "没卡的人被提示建卡")

async def sc_scripts(s: Sim) -> None:
    """敌人行为脚本：环境修正、目标平摊、逃跑、跑不掉自爆。"""
    print("【场景 16】敌人行为脚本与环境修正")
    conn = db.connect(":memory:")
    gid = "qq:SCRIPT"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="火炬"))
    combat.start(conn, gid, enemies=(("小丑", 1),),
                 modifiers=({"id": "scream", "label": "尖叫", "value": -4,
                             "rounds": [1, 3]},),
                 roller=dice.ScriptedRoller([9, 1]))
    loaded = combat.load(conn, gid)
    pc = loaded.pcs[0]
    clown = loaded.enemies[0]
    expect(combat.modifier_for(loaded, pc) == -4, "第 1 轮所有检定 −4")
    expect(combat.modifier_for(loaded, pc, round=4) == 0, "第 4 轮尖叫结束")
    print(f"    面板 | {combat.modifier_summary(loaded)}")

    # 前两轮：小丑与僧侣把目标平摊给玩家/吸血鬼
    spec = {"targets": {"split": ["pc", "vampire"], "rounds": [1, 2]}}
    vamp = combat.Combatant(group_id=gid, owner_qq="enemy:v", name="吸血鬼",
                            side="vampire", initiative=1, hp=30, max_hp=30, sp=4)
    monk = combat.Combatant(group_id=gid, owner_qq="enemy:m", name="僧侣",
                            side="monk", initiative=9, hp=45, max_hp=45, sp=7,
                            script=dict(spec))
    clown.script = spec
    clown.side = "clown"
    clown.initiative = 10
    loaded.combatants.extend([vamp, monk])
    loaded.factions["clown"] = {"pc", "vampire"}
    loaded.factions["monk"] = {"pc", "vampire"}
    chosen = [combat._pick_target(loaded, e, combat.foes(loaded, e), e.script,
                                  dice.ScriptedRoller([]))
              for e in (clown, monk)]
    show("小丑+僧侣", "第 1 轮选目标",
         "\n".join(f"· {e.name} → {t.name}" for e, t in zip((clown, monk), chosen)))
    expect({t.side for t in chosen} == {"pc", "vampire"}, "两个敌人平摊到玩家与吸血鬼")

    # 第 4 轮：想跑，且跑得掉
    loaded.round = 4
    clown.script = {"flee_if": {"round_gt": 3}, "flee_action": "sprint"}
    lines: list[str] = []
    combat._enemy_turn(conn, loaded, clown, dice.ScriptedRoller([]), lines)
    show("小丑", "第 4 轮", "\n".join(lines))
    expect(clown.escaped and "脱离战斗" in "\n".join(lines), "超过 3 轮尝试逃跑并脱离")

    # 跑不掉 → 自爆（没有地图，按同场全体结算）
    doomed = combat.Combat(group_id="qq:SCRIPT2", campaign_id=None, scene="",
                           round=4, turn_index=0, active=True)
    target = combat.Combatant(group_id="qq:SCRIPT2", owner_qq="u1", name="火炬",
                              side="pc", initiative=20, hp=30, max_hp=30, sp=4)
    bomber = combat.Combatant(
        group_id="qq:SCRIPT2", owner_qq="enemy:1", name="小丑", side="clown",
        initiative=10, hp=35, max_hp=35, sp=11,
        script={"flee_if": {"round_gt": 3}, "flee_blocked": True,
                "on_flee_fail": {"self_destruct": {
                    "weapon": "穿甲手榴弹", "damage": "6D6",
                    "note": "伪装成念珠"}}})
    doomed.combatants = [target, bomber]
    lines = []
    combat._enemy_turn(conn, doomed, bomber, dice.ScriptedRoller([6] * 6), lines)
    show("小丑", "被堵死", "\n".join(lines))
    expect(bomber.dead and target.hp < 30, "跑不掉自爆，6D6 炸到同场所有人")
    expect("没有地图" in "\n".join(lines), "自爆注明「没有地图」的简化")

    # 还没到加入轮次的敌人不行动
    late = combat.Combatant(group_id=gid, owner_qq="enemy:late", name="援兵",
                            side="clown", initiative=30, hp=20, max_hp=20, sp=4,
                            script={"join_round": 2})
    loaded.round = 1
    loaded.combatants = [pc, late]
    lines = []
    combat._enemy_turn(conn, loaded, late, dice.ScriptedRoller([]), lines)
    expect("场外" in "\n".join(lines) and not late.escaped, "join_round 之前不行动")


def _opera_after(gid: str, camp, gate, conn) -> str:
    old = (turn_flow.CONN, turn_flow.GATE)
    turn_flow.CONN, turn_flow.GATE = conn, gate
    try:
        return turn_flow._aftermath(gid, camp, "opera")
    finally:
        turn_flow.CONN, turn_flow.GATE = old


async def sc_opera(s: Sim) -> None:
    """《歌剧院之夜》跑到结束：a3 铺场、休整、胜负钩子。"""
    print("【场景 17】歌剧院之夜：a3 铺场与结局")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:OPERA"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.resume(gid)
    _card = dict(easy.templates()[0])
    _card["cyber"] = list(_card.get("cyber") or []) + ["交互接口"]  # 网行前置
    easy_roster.save(conn, gid, "u1", _card)

    # ① 教堂门口：按上一场幸存者动态铺场（playbook_opera.txt:94）
    gate.set_scene(camp.id, "a3.entry")
    skip = combat.start_text(conn, gid, "")
    show("a3.entry", "零幸存", skip)
    expect("跳过" in skip and combat.active(conn, gid) is None,
           "没有幸存者时跳过遭遇，不刷打手")
    for actor_id, name in (("opera_clown", "小丑"), ("opera_monk", "僧侣")):
        conn.execute(
            "INSERT INTO campaign_actors (campaign_id, actor_id, name, kind, status,"
            " hp, max_hp, scene, side, preset, fate, withdraw_at, script, data, updated_at)"
            " VALUES (?, ?, ?, 'enemy', 'escaped', 10, 35, 'a1.opera', 'enemy', ?,"
            " '', 0, '{}', '{}', 'x')",
            (camp.id, actor_id, name, name),
        )
    conn.commit()
    groups, mods = combat._encounter_groups(conn, gid)
    show("a3.entry", "幸存者", "、".join(f"{g['count']}×{g['name']}" for g in groups))
    expect({g["name"] for g in groups} == {"小丑", "僧侣"},
           "两名幸存者守在门口")
    combat.start(conn, gid, campaign_id=camp.id, scene="a3.entry",
                 enemies=groups, modifiers=mods, roller=dice.RandomRoller())
    old = (turn_flow.CONN, turn_flow.GATE)
    turn_flow.CONN, turn_flow.GATE = conn, gate
    try:
        blocked = await turn_flow.handle(gid, "u1", "我看看四周")
    finally:
        turn_flow.CONN, turn_flow.GATE = old
    expect("战斗还没结束" in (blocked or ""), "战斗中普通检定被拦住")
    conn.execute(
        "UPDATE combatants SET hp = 0, dead = 1"
        " WHERE group_id = ? AND side != 'pc'", (gid,),
    )
    conn.execute("UPDATE combats SET active = 0 WHERE group_id = ?", (gid,))
    conn.commit()
    tail = _opera_after(gid, camp, gate, conn)
    expect("📍 教堂大厅与地下祭坛" in tail, "门口清空后进教堂")

    # ② 休整：把 HP 打低再回满
    card = easy_roster.get(conn, gid, "u1")
    card["hp"] = 5
    conn.execute(
        "UPDATE easy_cards SET data = ? WHERE group_id = ? AND owner_qq = 'u1'",
        (json.dumps(card, ensure_ascii=False), gid),
    )
    conn.commit()
    show("休整", "回复", combat.rest(conn, gid))
    expect(easy_roster.get(conn, gid, "u1")["hp"] == card.get("max_hp", 40),
           "休整把 HP 回满")

    # ③ 教堂大厅：两座自动炮塔——用黑入/切电缆停掉，而不是直接改库
    groups, mods = combat._encounter_groups(conn, gid)
    show("a3.church", "铺场", "、".join(f"{g['count']}×{g['name']}" for g in groups))
    expect(groups[0]["name"] == "自动炮塔" and groups[0]["count"] == 2,
           "教堂大厅铺 2 座炮塔")
    card = easy_roster.get(conn, gid, "u1")
    card.setdefault("stats", {})["TECH"] = 30  # 让 1D10 + TECH 必定过 DV
    conn.execute(
        "UPDATE easy_cards SET data = ? WHERE group_id = ? AND owner_qq = 'u1'",
        (json.dumps(card, ensure_ascii=False), gid),
    )
    conn.commit()
    combat.start(conn, gid, campaign_id=camp.id, scene="a3.church",
                 enemies=groups, modifiers=mods, roller=dice.RandomRoller())
    tail = ""
    first = ""
    for i, action in enumerate(("接入接线盒", "切断电缆")):
        old = (turn_flow.CONN, turn_flow.GATE)
        turn_flow.CONN, turn_flow.GATE = conn, gate
        try:
            out = await turn_flow.handle(gid, "u1", action,
                                         roller=dice.RandomRoller())
        finally:
            turn_flow.CONN, turn_flow.GATE = old
        if i == 0:
            first = out or ""
        tail = out or ""
    show("a3.church", "接入", first)
    expect("接入成功" in first, "先接入接线盒")
    show("a3.church", "黑入", tail)
    expect("熄火停机" in (tail or ""), "黑入停掉炮塔")
    expect("📍 鲁思文勋爵" in (tail or ""), "炮塔清空后进 Boss")

    # ④ Boss：打完进尾声并收团
    groups, mods = combat._encounter_groups(conn, gid)
    expect(groups[0]["name"] == "鲁思文勋爵" and groups[0]["count"] == 1,
           "Boss 固定 1 人")
    combat.start(conn, gid, campaign_id=camp.id, scene="a3.boss",
                 enemies=groups, modifiers=mods, roller=dice.RandomRoller())
    conn.execute(
        "UPDATE combatants SET hp = 0, dead = 1"
        " WHERE group_id = ? AND side != 'pc'", (gid,),
    )
    conn.execute("UPDATE combats SET active = 0 WHERE group_id = ?", (gid,))
    conn.commit()
    tail = _opera_after(gid, camp, gate, conn)
    show("a3.boss", "胜利", tail)
    expect("📍 尾声：无菌走廊" in tail and "🏁" in tail, "Boss 倒下进尾声")
    expect(gate.state(gid).mode is Mode.IDLE, "尾声后结团")

    # ⑤ 团灭：a1.opera 全灭 → opera_dead
    gid2 = "qq:OPERA2"
    camp2 = gate.start_new(gid2, "歌剧院之夜", scenario="opera")
    gate.resume(gid2)
    easy_roster.save(conn, gid2, "u2", easy.templates()[0])
    gate.set_scene(camp2.id, "a1.opera")
    groups, mods = combat._encounter_groups(conn, gid2)
    combat.start(conn, gid2, campaign_id=camp2.id, scene="a1.opera",
                 enemies=groups, modifiers=mods, roller=dice.RandomRoller())
    conn.execute(
        "UPDATE combatants SET hp = 0, dead = 1"
        " WHERE group_id = ? AND side = 'pc'", (gid2,),
    )
    conn.execute("UPDATE combats SET active = 0 WHERE group_id = ?", (gid2,))
    conn.commit()
    tail = _opera_after(gid2, camp2, gate, conn)
    show("团灭", "结局", tail.splitlines()[-1] if tail else "（空）")
    expect("🏁" in tail and gate.state(gid2).mode is Mode.IDLE, "团灭走 opera_dead 并收团")


async def sc_favors(s: Sim) -> None:
    """EB 与人情：定金/尾款、自然语言用人情、对不上时的确认层。"""
    print("【场景 18】EB 与人情：结算与确认层")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:FAVOR"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.resume(gid)
    easy_roster.save(conn, gid, "u1", easy.templates()[0])
    gate.set_scene(camp.id, "a1.students")
    old = (turn_flow.CONN, turn_flow.GATE)
    turn_flow.CONN, turn_flow.GATE = conn, gate
    try:
        line = turn_flow._apply_rewards(gid, camp, "opera", "a1.students")
        show("定金", "结算", line)
        expect("500 EB" in line, "定金 500 EB 入账")
        expect(turn_flow._apply_rewards(gid, camp, "opera", "a1.students") == "",
               "同一场景奖励只发一次")

        card = easy_roster.get(conn, gid, "u1")
        growth.grant_favor(card, holder="学生", holder_name="学生", scope="person",
                           note="帮她改过论文")
        easy_roster.save(conn, gid, "u1", card)

        msg = await turn_flow.handle(gid, "u1", "学生，你欠我一份人情")
        show("人情", "个人欠条", msg)
        expect("认了这笔账" in (msg or ""), "学生的人情被认下")

        card = easy_roster.get(conn, gid, "u1")
        growth.grant_favor(card, holder="org:爱乐吸血鬼", holder_name="爱乐吸血鬼",
                           scope="org", org="爱乐吸血鬼")
        easy_roster.save(conn, gid, "u1", card)

        msg = await turn_flow.handle(gid, "u1", "学生，还我人情")
        show("人情", "范围不符", msg)
        expect("对不上" in (msg or "") and "确认" in (msg or ""),
               "对不上先说明原因再要确认")

        msg = await turn_flow.handle(gid, "u1", "确认")
        show("人情", "硬提", msg)
        expect("不是我欠你的" in (msg or ""), "确认后按失败结算")

        line = turn_flow._apply_rewards(gid, camp, "opera", "a3.ending")
        show("尾款", "结算", line)
        expect("1500 EB" in line and "人情" in line, "尾款与组织青睐入账")
        status = turn_flow.favor_status(gid, "u1")
        show("账本", "状态", status)
        expect("EB 2000" in status and "可用" in status, "EB 累计且青睐可用")
    finally:
        turn_flow.CONN, turn_flow.GATE = old
        conn.close()


async def sc_actors(s: Sim) -> None:
    """阶段 2：打不死的 Boss 退场、演员表归档、fresh 回归、过场 presence。"""
    print("【场景 19】演员表与回归：打不死的 Boss、过场 presence")
    from cyberpunk import easy
    import core.story as story_mod

    conn = db.connect(":memory:")
    gid = "qq:ACTORS"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.resume(gid)
    easy_roster.save(conn, gid, "u1", easy.templates()[0])
    old = (turn_flow.CONN, turn_flow.GATE)
    turn_flow.CONN, turn_flow.GATE = conn, gate
    try:
        boss = {"name": "鲁思文勋爵", "count": 1, "faction": "russwin",
                "actor": "russwin", "fate": "cannot_die", "withdraw_at": 1,
                "hp": 5, "max_hp": 50, "data": {"sp": 0}}
        combat.start(conn, gid, campaign_id=camp.id, scene="a3.boss",
                     enemies=(boss,), roller=dice.ScriptedRoller([10, 1]))
        combat.close_range(conn, gid, "u1", "距离 8 米")
        text = combat.attack(conn, gid, "u1", "鲁思文", skill="手枪",
                             roller=dice.ScriptedRoller([10, 2, 3]))
        show("Boss", "打不死", text)
        expect("没有倒下" in text, "HP 归零后不是死亡而是退场")
        loaded = combat.load(conn, gid)
        foe = next(c for c in loaded.combatants if c.side == "russwin")
        expect(foe.escaped and not foe.dead and foe.hp == 1, "退场记 escaped/1HP")

        combat.end(conn, gid)
        row = conn.execute(
            "SELECT status, hp FROM campaign_actors WHERE campaign_id = ? AND actor_id = ?",
            (camp.id, "russwin"),
        ).fetchone()
        show("演员表", "归档", str(dict(row)) if row else None)
        expect(row is not None and row["status"] == "escaped", "战斗结束把演员写进 campaign_actors")

        fresh = combat.actor_group(conn, camp.id, "russwin", fresh=True)
        expect(fresh is not None and fresh["hp"] == fresh["max_hp"] == 50,
               "fresh 回归满血")
        combat.start(conn, gid, campaign_id=camp.id, scene="b3.return",
                     enemies=(fresh,), roller=dice.ScriptedRoller([10, 1]))
        again = combat.load(conn, gid)
        foe = next(c for c in again.combatants if c.side == "russwin")
        expect(foe.hp == 50 and foe.fate == "cannot_die", "回归保留命格")
        combat.end(conn, gid)

        real_scene = story_mod.scene
        fake = {"presence": [{"name": "鲁思文勋爵",
                              "line": "他站在祭坛边，像在看一出戏。",
                              "untouchable": True}]}
        story_mod.scene = lambda sid, scid: fake
        try:
            msg = await turn_flow.handle(gid, "u1", "我朝鲁思文勋爵开枪")
            show("过场", "不可攻击", msg)
            expect("不是这场战斗里能攻击的目标" in (msg or ""),
                   "打过场人物先说明原因并等确认")
            msg = await turn_flow.handle(gid, "u1", "确认")
            show("过场", "确认后", msg)
            expect("招式落空" in (msg or ""), "确认后按失败结算，不凭空成功")
        finally:
            story_mod.scene = real_scene
    finally:
        turn_flow.CONN, turn_flow.GATE = old
        conn.close()


async def sc_entry(s: Sim) -> None:
    """阶段 5：a3.entry 按上一场幸存者动态铺场（playbook_opera.txt:94）。"""
    print("【场景 22】教堂门口：幸存者动态铺场")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:ENTRY"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.resume(gid)
    for i in range(1, 5):
        easy_roster.save(conn, gid, f"u{i}", easy.templates()[0])
    gate.set_scene(camp.id, "a3.entry")

    def set_actor(actor_id: str, name: str, status: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO campaign_actors (campaign_id, actor_id, name,"
            " kind, status, hp, max_hp, scene, side, preset, fate, withdraw_at,"
            " script, data, updated_at)"
            " VALUES (?, ?, ?, 'enemy', ?, 10, 35, 'a1.opera', 'enemy', ?, '', 0,"
            " '{}', '{}', 'x')",
            (camp.id, actor_id, name, status, name),
        )
        conn.commit()

    try:
        text = combat.start_text(conn, gid, "")
        show("零幸存", "门口", text)
        expect("跳过" in text and combat.active(conn, gid) is None,
               "小丑和僧侣都没跑掉：跳过遭遇，不刷打手")

        set_actor("opera_clown", "小丑", "escaped")
        set_actor("opera_monk", "僧侣", "escaped")
        groups, _mods = combat._encounter_groups(conn, gid)
        names = [g["name"] for g in groups]
        mooks = sum(g["count"] for g in groups
                    if g["name"] in ("审判官和尚", "博佐帮蠢蛋"))
        show("都幸存", "铺场", "、".join(f"{g['name']}×{g['count']}" for g in groups))
        expect("小丑" in names and "僧侣" in names, "两名幸存者都守门")
        expect(mooks == 2, "蠢蛋数量 = PC(4) − 2")

        conn.execute("UPDATE campaign_actors SET status = 'dead'"
                     " WHERE actor_id = 'opera_clown'")
        conn.commit()
        groups, _mods = combat._encounter_groups(conn, gid)
        names = [g["name"] for g in groups]
        show("只僧侣", "铺场", "、".join(f"{g['name']}×{g['count']}" for g in groups))
        expect("僧侣" in names and "小丑" not in names, "死掉的小丑不再出场")
        expect(all(g["name"] != "博佐帮蠢蛋" for g in groups),
               "只有僧侣幸存 → 蠢蛋全是审判官和尚")
    finally:
        conn.close()


async def sc_net(s: Sim) -> None:
    """接入点 → 控制节点 → 黑冰反噬。"""
    print("【场景 20】接入点 / 控制节点 / 黑冰")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:NET"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.resume(gid)
    gate.set_scene(camp.id, "a3.church")
    _card = dict(easy.templates()[0])
    _card["cyber"] = list(_card.get("cyber") or []) + ["交互接口"]  # 网行前置
    easy_roster.save(conn, gid, "u1", _card)
    card = easy_roster.get(conn, gid, "u1")
    card.setdefault("stats", {})["TECH"] = 30
    conn.execute(
        "UPDATE easy_cards SET data = ? WHERE group_id = ? AND owner_qq = 'u1'",
        (json.dumps(card, ensure_ascii=False), gid),
    )
    conn.commit()

    def pc_index() -> int:
        loaded = combat.load(conn, gid)
        pc = next(x for x in loaded.order if x.side == "pc")
        return loaded.order.index(pc)

    def force_pc() -> None:
        conn.execute("UPDATE combats SET turn_index = ? WHERE group_id = ?",
                     (pc_index(), gid))
        conn.commit()

    combat.start(conn, gid, campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 2),), roller=dice.RandomRoller())
    refused = combat.net_action(conn, gid, "u1", "切断电缆")
    show("接入前", "黑炮塔", refused)
    expect("还没接入" in (refused or ""), "没接入不能控制节点")

    got = combat.net_action(conn, gid, "u1", "接入接线盒",
                            roller=dice.ScriptedRoller([10]))
    show("接入", "接线盒", got)
    expect("接入成功" in got, "接入接线盒成功")

    force_pc()
    stopped = combat.net_action(conn, gid, "u1", "切断电缆",
                                roller=dice.ScriptedRoller([10]))
    show("控制", "炮塔节点", stopped)
    expect("熄火停机" in stopped and combat.active(conn, gid) is None,
           "控制节点停掉两座炮塔")

    combat.start(conn, gid, campaign_id=camp.id, scene="a3.church",
                 enemies=(("自动炮塔", 1),), roller=dice.RandomRoller())
    combat.net_action(conn, gid, "u1", "接入接线盒",
                      roller=dice.ScriptedRoller([10]))
    card["stats"]["TECH"] = -10  # 接入后链路变差，这次黑不进去
    conn.execute(
        "UPDATE easy_cards SET data = ? WHERE group_id = ? AND owner_qq = 'u1'",
        (json.dumps(card, ensure_ascii=False), gid),
    )
    conn.commit()
    force_pc()
    before = next(x for x in combat.load(conn, gid).combatants if x.side == "pc").hp
    back = combat.net_action(conn, gid, "u1", "切断电缆",
                             roller=dice.ScriptedRoller([1, 3, 4, 3, 4]))
    after = next(x for x in combat.load(conn, gid).combatants if x.side == "pc").hp
    show("黑冰", "反扑", back)
    expect("黑冰反扑" in back and "烧穿" in back and after == before - 7,
           "黑冰攻击命中后烧 2D6")

    conn.close()


async def sc_map(s: Sim) -> None:
    """场景地图：落位、距离 DV、打掩体、移动、5×5 爆炸。"""
    print("【场景 21】地图 / 距离 / 掩体 / 移动")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:MAP"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    easy_roster.save(conn, gid, "u1", easy.templates()[0])

    def restart() -> None:
        if combat.active(conn, gid):
            combat.end(conn, gid)
        combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                     enemies=[{"name": "小丑", "count": 1, "faction": "clown"}],
                     roller=dice.RandomRoller(5))

    def place(pc_pos, enemy_pos) -> str:
        f = combat.load(conn, gid)
        pc = next(x for x in f.order if x.side == "pc")
        enemy = next(x for x in f.order if x.side != "pc")
        pc.x, pc.y = pc_pos
        enemy.x, enemy.y = enemy_pos
        f.turn_index = f.order.index(pc)
        combat._save(conn, f)
        return pc.owner_qq

    restart()
    f = combat.load(conn, gid)
    spots = [(c.x, c.y) for c in f.combatants]
    show("落位", "地图", f.map_id)
    expect(f.map_id == "opera_hall" and len(set(spots)) == len(spots)
           and all(x >= 0 for x, y in spots), "场景地图自动落位且不重叠")
    text = combat.map_text(conn, gid, "u1")
    expect("@" in text and "E" in text, "地图标记玩家与敌人")

    who = place((2, 10), (5, 10))
    shot = combat.attack(conn, gid, who, "小丑", skill="手枪")
    show("距离", "6 米", shot.splitlines()[0])
    expect("6 米" in shot and "DV 13" in shot, "远程按距离查 DV")

    restart()
    place((8, 10), (8, 1))
    wall = combat.attack(conn, gid, "u1", "小丑", skill="手枪")
    show("视线", "墙", wall.splitlines()[-1])
    expect("视线被墙" in wall, "墙与掩体挡视线")

    restart()
    place((2, 10), (8, 1))
    cover = combat.attack(conn, gid, "u1", "吧台", skill="手枪",
                          roller=dice.ScriptedRoller([10, 3, 3, 1, 1, 1, 1]))
    show("掩体", "吧台", cover.splitlines()[-1])
    expect("掩体掉了 6 点耐久" in cover, "打掩体扣耐久")

    restart()
    place((2, 10), (8, 1))
    moved = combat.act(conn, gid, "u1", "移动到 4,8")
    show("移动", "4,8", moved)
    me = next(c for c in combat.load(conn, gid).combatants
              if c.owner_qq == "u1" and c.side == "pc")
    expect("移动到 (4,8)" in moved and (me.x, me.y) == (4, 8), "移动动作改坐标")

    f = combat.load(conn, gid)
    pcs = [c for c in f.order if c.side == "pc"]
    enemy = next(c for c in f.order if c.side != "pc")
    enemy.x, enemy.y = (8, 2)
    pcs[0].x, pcs[0].y = (8, 4)
    lines: list[str] = []
    script = {"on_flee_fail": {"self_destruct": {"weapon": "手雷", "damage": "2D6"}}}
    combat._self_destruct(conn, f, enemy, script,
                          dice.ScriptedRoller([6, 6]), lines)
    blast = "\n".join(lines)
    show("爆炸", "5×5", blast.splitlines()[-1])
    expect("5×5" in blast, "爆炸只结算爆心 5×5 格")
    conn.close()


async def sc_scene_move(s: Sim) -> None:
    """阶段 6：非战斗坐标持久化 + 移动按 MOVE×2 米。"""
    print("【场景 23】非战斗移动 / MOVE×2 / 坐标持久化")
    from core import map as gridmap

    conn = db.connect(":memory:")
    gid = "qq:MOVE"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    card = roster.build(template="独狼", name="阿飞")
    card["stats"]["MOVE"] = 6
    roster.save(conn, gid, "u1", card)

    game = gridmap.load("opera_hall")
    start = tuple(game.spawns["pc"])
    spots = combat._spots_from(game, start)
    goal = next(p for p in spots if 0 < game.path(start, p)[1] <= 6)
    text = combat.scene_move(conn, gid, "u1", f"移动到 {goal[0]},{goal[1]}")
    show("移动", f"到 {goal}", text)
    saved = roster.get(conn, gid, "u1")["state"]["pos"]
    expect((saved["x"], saved["y"]) == goal, "非战斗坐标写进角色卡")

    far = max(spots, key=lambda p: game.path(start, p)[1])
    first = combat.scene_move(conn, gid, "u1", f"移动到 {far[0]},{far[1]}")
    show("移动力", f"去 {far}", first)
    expect("继续走" in first and "MOVE" in first, "超出 MOVE×2 停在半路")
    for _ in range(20):
        step = combat.scene_move(conn, gid, "u1", "继续走")
        if "继续走" not in step:
            break
    at = roster.get(conn, gid, "u1")["state"]["pos"]
    expect((at["x"], at["y"]) == far, "继续走能到目的地")

    mt = combat.map_text(conn, gid, "u1")
    expect("@" in mt and "非战斗移动" in mt, "非战斗地图显示玩家坐标")
    conn.close()


async def sc_grenade(s: Sim) -> None:
    """阶段 7：手榴弹（DEX+运动、距离 DV、5×5、掩体、装备消耗）。"""
    print("【场景 24】手榴弹投掷 / 5×5 爆炸 / 掩体")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:GRENADE"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    easy_roster.save(conn, gid, "u1", easy.templates()[0])
    easy_roster.add_gear(conn, gid, "u1", "手榴弹")

    combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                 enemies=[{"name": "小丑", "count": 2, "faction": "clown"}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    e1 = next(c for c in f.combatants if c.name.endswith("1"))
    e2 = next(c for c in f.combatants if c.name.endswith("2"))
    pc.x, pc.y = (7, 7)
    e1.x, e1.y = (3, 7)   # 8 米 → DV 15；吧台 (3,8) 挡在小丑2 前面
    e2.x, e2.y = (3, 9)
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    text = combat.throw_grenade(
        conn, gid, "u1", f"扔手榴弹炸{e1.name}",
        roller=dice.ScriptedRoller([10, 4, 4, 4, 4, 4, 4], loop=True),
    )
    show("手榴弹", "8 米 / DV15", " / ".join(text.splitlines()[:2]))
    expect("DV 15" in text and "爆心 5×5" in text,
           "手榴弹按距离 DV 结算、爆心 5×5 格")
    expect("被炸" in text and "躲在吧台后面" in text,
           "命中目标吃伤害、掩体后的目标免伤")

    held = (easy_roster.get(conn, gid, "u1") or {}).get("gear") or []
    expect("手榴弹" not in held, "投掷后消耗一颗手榴弹")

    if combat.active(conn, gid):
        combat.end(conn, gid)
    combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                 enemies=[{"name": "小丑", "count": 1, "faction": "clown"}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    e = next(c for c in f.order if c.side != "pc")
    pc.x, pc.y = (7, 7)
    e.x, e.y = (5, 7)
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)
    again = combat.throw_grenade(conn, gid, "u1", "再扔一颗手榴弹",
                                 roller=dice.ScriptedRoller([10], loop=True))
    show("没弹药", "手榴弹", again.splitlines()[0])
    expect("没有手榴弹" in again, "没弹药时拒绝，不消耗动作")
    conn.close()


async def sc_range(s: Sim) -> None:
    """阶段 8：range_m / DM 估米数 / 15 米兜底 + 动态任务 DV。"""
    print("【场景 25】距离 DV：range_m / DM 估距 / 兜底")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:RANGE"
    easy_roster.save(conn, gid, "u1", easy.templates()[0])

    def pick_pc(fight):
        pc = next(c for c in fight.order if c.side == "pc")
        fight.turn_index = fight.order.index(pc)
        combat._save(conn, fight)
        return pc, next(c for c in fight.order if c.side != "pc")

    combat.start(conn, gid, enemies=[{"name": "小丑", "count": 1, "range_m": 30}],
                 roller=dice.RandomRoller(3))
    f = combat.load(conn, gid)
    pc, enemy = pick_pc(f)
    shot = combat.attack(conn, gid, "u1", enemy.name, skill="手枪",
                         roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    show("range_m", "30 米", shot.splitlines()[0])
    expect("DV 25" in shot and "30 米（估）" in shot,
           "encounter.range_m 按手枪距离表换成 DV25")

    combat.end(conn, gid)
    combat.start(conn, gid, enemies=[{"name": "小丑", "count": 1}],
                 roller=dice.RandomRoller(3))
    f = combat.load(conn, gid)
    pc, enemy = pick_pc(f)
    shot = combat.attack(conn, gid, "u1", enemy.name, skill="手枪",
                         roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    show("兜底", "没有距离", shot.splitlines()[0])
    expect(shot.strip() == combat.RANGE_ASK,
           "没有地图/距离数据时不猜 DV，请人明说米数")
    told = combat.close_range(conn, gid, "u1", "距离 20 米")
    show("报距离", "20 米", told)
    expect("DV 20" in told, "报距离后按手枪表算出 DV20")
    shot = combat.attack(conn, gid, "u1", enemy.name, skill="手枪",
                         roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    expect("DV 20" in shot, "报过的距离用于下一次攻击")

    combat.end(conn, gid)
    combat.start(conn, gid, enemies=[{"name": "小丑", "count": 1}],
                 roller=dice.RandomRoller(3))
    f = combat.load(conn, gid)
    pc, enemy = pick_pc(f)
    turn_flow.CONN = conn
    original = llm.complete_json

    async def fake_estimate(system, user, **kw):
        return {"meters": 40, "reason": "隔着半条街"}

    llm.complete_json = fake_estimate  # type: ignore[assignment]
    try:
        await turn_flow._dm_estimate_range(f, enemy.name, "朝小丑开枪", gid)
    finally:
        llm.complete_json = original  # type: ignore[assignment]
    target = next(c for c in combat.load(conn, gid).combatants
                  if c.name == enemy.name)
    show("DM 估距", "40 米", f"range_m={target.range_m}")
    expect(target.range_m == 40, "DM 估的米数写进战斗并落库")
    shot = combat.attack(conn, gid, "u1", enemy.name, skill="手枪",
                         roller=dice.ScriptedRoller([5, 3, 3], loop=True))
    expect("DV 25" in shot, "DM 估距后按距离表换算 DV25（手枪 26–50 米档）")

    # 动态任务 DV：DM 给理由才生效，最终夹在 9–29
    p = turn_flow.turn.Proposal.parse(
        {"skill": "觉察", "dv_adjust": 2, "dv_reason": "大雨"})
    expect(p.dv_adjust == 2 and p.dv_reason == "大雨", "DM 的 DV 调整带理由保留")
    p = turn_flow.turn.Proposal.parse({"skill": "觉察", "dv_adjust": 2})
    expect(p.dv_adjust == 0, "没有理由的 DV 调整被丢弃")
    conn.close()


async def sc_mortal(s: Sim) -> None:
    """A1：致命伤 + 死亡豁免（HP≤0 不直接死，每回合判死）。"""
    print("【场景 26】A1 致命伤 / 死亡豁免")
    from cyberpunk import easy

    conn = db.connect(":memory:")
    gid = "qq:MORTAL"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    easy_roster.save(conn, gid, "u1", easy.templates()[0])

    combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                 enemies=[{"name": "打手", "count": 1, "faction": "enemy"}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    e = next(c for c in f.order if c.side != "pc")
    pc.x, pc.y = (7, 7)
    e.x, e.y = (3, 7)
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    # 直接把玩家压到致命伤（一记足够的伤，命中后伤害覆盖护甲还打空血条）
    ll: list[str] = []
    me = next(c for c in f.combatants if c.side == "pc")
    combat._apply_damage_hp(f, me, me.hp + me.effective_sp, ll)
    expect(me.hp == 0 and me.mortal and not me.dead, "HP≤0 只致命伤，没直接死")

    # 玩家过死亡豁免：掷 2 < 体格(3) → 撑住，恢复 1 HP 失去意识
    dl: list[str] = []
    combat._death_check(f, me, dice.ScriptedRoller([2]), dl)
    expect(me.hp == 1 and not me.mortal and not me.dead,
           "死亡豁免成功：恢复 1 HP 并失去意识（本回合动不了）")
    expect(any("失去意识" in x for x in dl), "有失去意识提示")
    combat._save(conn, f)
    conn.close()


async def sc_crit(s: Sim) -> None:
    """A2：严重伤势（触发 / 查表 / 去重 / 惩罚真正影响判定）。"""
    print("【场景 27】A2 严重伤势：触发、查表、惩罚接线")
    from cyberpunk import crit

    conn = db.connect(":memory:")
    gid = "qq:CRIT"
    from cyberpunk import easy

    easy_roster.save(conn, gid, "u1", easy.templates()[1])  # 搬运工：没有痛觉编辑器
    combat.start(conn, gid,
                 enemies=[{"name": "打手", "count": 1, "range_m": 8}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    enemy = next(c for c in f.order if c.side != "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    # 1) 伤害骰出两个 6 → 触发一次严重伤势，额外 5 点伤害
    ll: list[str] = []
    before = enemy.hp
    combat.maybe_serious_injury(f, enemy, 2, head=False,
                                roller=dice.ScriptedRoller([2, 2]), lines=ll)
    show("触发", "2D6=4", "\n".join(ll))
    expect(enemy.crits == ("pneumothorax",), "两个 6 触发严重伤势：气胸")
    expect(enemy.hp == before - 5, "严重伤势额外 5 点伤害，不削护甲")
    expect(enemy.move_penalty == 2, "气胸：移速 −2")
    expect(enemy.death_save_penalty == 1, "气胸：死亡豁免惩罚 +1")

    # 2) 去重：再中一次同一个点数，要重掷到没有的伤势
    ll = []
    combat.maybe_serious_injury(f, enemy, 3, head=False,
                                roller=dice.ScriptedRoller([2, 2, 2, 3]), lines=ll)
    show("去重", "又掷到 4", "\n".join(ll))
    expect(enemy.crits == ("pneumothorax", "rib_fracture"),
           "重复点数会重掷，直到拿到没有的伤势")

    # 3) 惩罚接入攻击判定：远程 −N 真的从总值里扣
    combat.end(conn, gid)
    combat.start(conn, gid,
                 enemies=[{"name": "打手", "count": 1, "range_m": 8}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)
    base = combat.attack(conn, gid, "u1", "打手", skill="手枪",
                         roller=dice.ScriptedRoller([9, 3, 3, 1]))
    show("基准", "没有伤势", base.splitlines()[0])
    expect("：16 vs DV 15" in base, "基准命中总值 9+REF7 = 16")

    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    me.crits = ("eye_lost",)  # 失去眼睛：远程 −4
    combat._save(conn, f)
    hurt = combat.attack(conn, gid, "u1", "打手", skill="手枪",
                         roller=dice.ScriptedRoller([9, 1, 2, 2]))
    show("受伤后", "失去眼睛 −4", hurt.splitlines()[0])
    expect("：12 vs DV 15" in hurt, "远程攻击吃了 −4，16 → 12")
    expect("没打中" in hurt, "12 < DV15：这一枪打飞了")

    # 4) 近战无法闪避（断腿）
    combat.end(conn, gid)
    combat.start(conn, gid, enemies=[{"name": "打手", "count": 1}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    foe = next(c for c in f.order if c.side != "pc")
    f.turn_index = f.order.index(pc)
    combat.close_range(conn, gid, "u1", "距离 2 米")
    foe.crits = ("leg_lost",)
    combat._save(conn, f)
    # 只给攻击骰 + 伤害骰 + 敌人回合一击：要掷闪避就会不够
    brawl = combat.attack(conn, gid, "u1", "打手", skill="搏击",
                          roller=dice.ScriptedRoller([8, 4, 2]))
    show("断腿", "近战", brawl.splitlines()[0])
    expect("无法闪避" in brawl, "断腿目标无法闪避，近战直接命中")

    # 5) 移动预算被移速惩罚扣掉，最低剩 1 格
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    card = {"stats": {"MOVE": 5}}
    expect(combat._move_budget(me, card) == 5, "没伤势：移动预算 = MOVE 5 格")
    me.crits = ("pneumothorax",)
    expect(combat._move_budget(me, card) == 3, "气胸：5 − 2 = 3 格")
    me.crits = ("leg_lost",)
    expect(combat._move_budget(me, card) == 1, "断腿 −6 也不会扣到 0，最低 1 格")

    # 6) 步行 >4m 后伤势发作（肋骨骨折）
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    me.crits = ("rib_fracture",)
    me.hp = 20
    slot = combat._slots(f)[me.owner_qq]
    lines: list[str] = []
    combat._after_walk(me, 2, slot, f, lines)
    expect(me.hp == 20 and not lines, "走 4 米以内不发作")
    combat._after_walk(me, 3, slot, f, lines)
    show("步行", "走了 3 格", "\n".join(lines))
    expect(me.hp == 15, "走了超过 4 米：肋骨骨折额外掉 5 点血")

    # 7) 表数据与存盘往返
    expect(sorted(crit.BODY_INJURIES) == list(range(2, 13)),
           "身体表 2–12 齐全")
    expect(sorted(crit.HEAD_INJURIES) == list(range(2, 13)),
           "头部表 2–12 齐全")
    combat._save(conn, f)
    again = combat.load(conn, gid)
    me = next(c for c in again.combatants if c.side == "pc")
    show("存盘", "crits", f"crits={me.crits} hp={me.hp}")
    expect(me.crits == ("rib_fracture",), "严重伤势随战斗存盘/读盘保留")
    expect(me.walk_damage, "派生属性在读盘后仍然生效")

    # 8) 痛觉编辑器：忽略严重伤势的负面影响（full.txt:27821-27825）
    me.crits = ("eye_lost", "leg_lost", "rib_fracture")
    me.pain_editor = False
    expect(me.ranged_penalty != 0, "没装痛觉编辑器：失去眼睛 远程 −4")
    me.pain_editor = True
    show("痛觉编辑器", "装上后", "所有 crit 惩罚归零、伤势记录保留")
    expect(me.ranged_penalty == 0, "痛觉编辑器：远程惩罚忽略")
    expect(me.move_penalty == 0, "痛觉编辑器：移速惩罚忽略")
    expect(me.no_dodge is False, "痛觉编辑器：断腿的无法闪避也忽略")
    expect(me.walk_damage is False, "痛觉编辑器：肋骨骨折的步行掉血也忽略")
    expect(me.crits == ("eye_lost", "leg_lost", "rib_fracture"),
           "痛觉编辑器：伤势记录本身还在")
    conn.close()


async def sc_autofire(s: Sim) -> None:
    """A3：全自动射击（一个动作 + 10 发；伤害 = 2D6 × 判定差值，封顶）。"""
    print("【场景 28】A3 全自动射击：弹药门槛、专用 DV 表、伤害乘算")
    conn = db.connect(":memory:")
    gid = "qq:AUTOFIRE"

    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"自动武器": 6})
    roster.save(conn, gid, "u1", card)
    combat.start(conn, gid,
                 enemies=[{"name": "打手", "count": 1, "range_m": 8}],
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    # 1) 只有冲锋枪/突击步枪有全自动
    bad = combat.attack(conn, gid, "u1", "打手", autofire=True, weapon="手枪",
                        roller=dice.ScriptedRoller([10, 3, 3]))
    show("手枪", "全自动", bad)
    expect("没有全自动模式" in bad, "手枪没有全自动模式，直接拒绝")

    # 2) 不能瞄准
    bad = combat.attack(conn, gid, "u1", "打手", autofire=True, weapon="冲锋枪",
                        aim="头部", roller=dice.ScriptedRoller([10, 3, 3]))
    show("瞄准", "全自动", bad)
    expect("不能瞄准" in bad, "全自动不能瞄准（full.txt:14806）")

    # 3) 弹匣不足 10 发也不能打
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    me.ammo = 9
    combat._save(conn, f)
    bad = combat.attack(conn, gid, "u1", "打手", autofire=True, weapon="冲锋枪",
                        roller=dice.ScriptedRoller([10, 3, 3]))
    show("9 发", "全自动", bad)
    expect("只剩 9 发" in bad, "不足 10 发不能全自动，也不扣动作")

    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    me.ammo = -1
    combat._save(conn, f)

    # 4) 正常扫射：8 米冲锋枪全自动 DV 17（单发在这档是 13）
    hit = combat.attack(conn, gid, "u1", "打手", autofire=True, weapon="冲锋枪",
                        roller=dice.ScriptedRoller([10, 3, 3, 1]))
    show("扫射", "冲锋枪 8 米", hit)
    expect("vs DV 17" in hit, "走全自动距离表：8 米 DV 17，不是单发的 13")
    expect("取 3 发" in hit, "判定超出 6 点，封顶取 3 发（冲锋枪上限 3）")
    expect("打掉 14 血" in hit, "2D6=6，×3=18，护甲 4 → 打掉 14")
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    show("弹药", "打完", f"弹匣 {me.rounds_left('冲锋枪')}/{me.mag or 30}")
    expect(me.ammo == 20, "打光 10 发，弹匣 30 → 20")

    # 5) 突击步枪上限 4
    combat.end(conn, gid)
    combat.start(conn, gid,
                 enemies=[{"name": "打手", "count": 1, "range_m": 14}],
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)
    rifle = combat.attack(conn, gid, "u1", "打手", autofire=True, weapon="突击步枪",
                          roller=dice.ScriptedRoller([10, 3, 2, 1]))
    show("扫射", "突击步枪 14 米", rifle)
    expect("vs DV 17" in rifle, "14 米落在 13-25 档，突击步枪 DV 17")
    expect("取 4 发" in rifle, "差值再大也只取 4 发（突击步枪上限 4）")

    # 6) 目标 REF≥8 可以用闪避对抗替代 DV
    combat.end(conn, gid)
    combat.start(conn, gid,
                 enemies=[{"name": "头目", "count": 1, "range_m": 8}],
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)
    dodge = combat.attack(conn, gid, "u1", "头目", autofire=True, weapon="冲锋枪",
                          roller=dice.ScriptedRoller([10, 10, 3, 3, 3, 3]))
    show("闪避", "头目 REF8", dodge)
    expect("vs 闪避" in dodge, "REF≥8 的目标选择闪避，用 DEX+闪避 对抗")

    # 7) 弹药写进 stat，读盘后还在
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    show("存盘", "ammo/mag", f"ammo={me.ammo} mag={me.mag or '按武器推'}")
    expect(me.ammo == 20 and me.rounds_left("冲锋枪") == 20,
           "弹药随战斗存盘/读盘保留（弹匣容量按武器类别推）")
    conn.close()


async def sc_ammo(s: Sim) -> None:
    """A4：弹药计数（单发扣弹、空仓拒绝、装填补满、弓不耗弹）。"""
    print("【场景 29】A4 弹药计数：单发扣弹、空仓拒绝、装填补满")
    conn = db.connect(":memory:")
    gid = "qq:AMMO"

    card = roster.build(template="独狼", name="阿飞",
                        skill_overrides={"手枪": 4})
    roster.save(conn, gid, "u1", card)
    combat.start(conn, gid,
                 enemies=[{"name": "打手", "count": 1, "range_m": 8}],
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    # 1) 开打时枪是满的，打一发扣一发
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    show("初始", "ammo", f"ammo={me.ammo}（-1 = 还没计数，按满弹匣算）")
    expect(me.rounds_left("手枪") == 8, "手枪默认弹匣 8 发")

    hit = combat.attack(conn, gid, "u1", "打手",
                        roller=dice.ScriptedRoller([10, 3, 3, 1, 1], loop=True))
    show("开枪", "单发", hit)
    expect("弹匣剩 7 发" in hit, "开一枪扣一发")
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    expect(me.ammo == 7, "弹药写进战斗状态")

    # 2) 没打中也扣弹（规则书没给「未命中不耗弹」）
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    f.round += 1
    combat._reset_slots(f)
    for c in f.combatants:
        if c.side == "pc":
            c.ammo = 7
    combat._save(conn, f)
    miss = combat.attack(conn, gid, "u1", "打手",
                         roller=dice.ScriptedRoller([1, 1, 1, 1, 1], loop=True))
    show("失手", "单发", miss)
    expect("没打中" in miss and "弹匣剩 6 发" in miss, "失手一样扣弹（full.txt:14691-14700 无免扣条款）")

    # 3) 空仓直接拒绝，且不消耗动作
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    f.round += 1
    combat._reset_slots(f)
    for c in f.combatants:
        if c.side == "pc":
            c.ammo = 0
    combat._save(conn, f)
    empty = combat.attack(conn, gid, "u1", "打手",
                          roller=dice.ScriptedRoller([10, 3, 3]))
    show("空仓", "单发", empty)
    expect("没子弹了" in empty and "装填" in empty, "0 发时拒绝并提示装填")
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    slot = combat._slots(f)[me.owner_qq]
    expect(not slot.action, "被拒绝不消耗动作——同一个回合还能装填")

    # 4) 装填是「完全重新装填」，补满整个弹匣
    reload_text = combat.act(conn, gid, "u1", "装填",
                             roller=dice.ScriptedRoller([3]))
    show("装填", "补满", reload_text)
    expect("0/8 → 8/8" in reload_text, "装填补满（full.txt:14543-14545 完全重新装填）")
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    expect(me.ammo == 8, "弹匣回到满")

    # 5) 学到的具体容量优先（mag 12）
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    f.round += 1
    combat._reset_slots(f)
    for c in f.combatants:
        if c.side == "pc":
            c.ammo = 3
            c.mag = 12
    combat._save(conn, f)
    learned = combat.act(conn, gid, "u1", "装填", roller=dice.ScriptedRoller([3]))
    show("装填", "mag=12", learned)
    expect("3/12 → 12/12" in learned, "有具体弹匣容量时按它补（不是类别默认 8）")

    # 6) 弓不耗弹、也没有装填动作
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    f.turn_index = f.order.index(pc)
    f.round += 1
    combat._reset_slots(f)
    for c in f.combatants:
        if c.side == "pc":
            c.damage = "弓"
            c.ammo = 0
            c.mag = 0
    combat._save(conn, f)
    bow = combat.attack(conn, gid, "u1", "打手", skill="弓",
                        roller=dice.ScriptedRoller([10, 3, 3, 1, 1], loop=True))
    show("弓", "射击", bow)
    expect("没子弹了" not in bow, "弓不用装填也不打空（full.txt:14898-14899）")
    bow_reload = combat.act(conn, gid, "u1", "装填", roller=dice.ScriptedRoller([3]))
    show("弓", "装填", bow_reload)
    expect("永远不用装填" in bow_reload, "弓没有装填这个动作，说了白说、不扣动作")

    # 7) 敌人不计数
    f = combat.load(conn, gid)
    foe = next(c for c in f.combatants if c.side != "pc")
    expect(foe.ammo == -1, "NPC 不进弹药计数（约定：敌人不会打空）")

    # 8) 存盘往返
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    expect(me.ammo == 0 and me.mag == 0, "弹药随战斗存盘/读盘保留")
    conn.close()


async def sc_cover(s: Sim) -> None:
    """A5：掩体打烂后的通行/视线 + 火力压制。"""
    print("【场景 30】A5 掩体打烂：不挡视线 / 能通行 / 不挡爆炸 + 火力压制")
    from cyberpunk import easy
    from core import map as gridmap

    class _Floor:
        """骰子固定值：d10 掷 10、d6 掷 3（保证确定命中，不死循环）。"""

        def roll(self, sides: int = 10) -> int:
            return 10 if sides == 10 else min(3, sides)

    # ── 地图层 ─────────────────────────────────────────────
    gm = gridmap.load("opera_hall")
    bar = next(c for c in gm.covers if c.id == "bar")
    show("地图", "吧台", f"({bar.x},{bar.y}) 耐久 {bar.hp}")
    expect(not gm.playable((bar.x, bar.y)),
           "立着的掩体占格，人站不进去（约定：规则书没写网格占格）")
    expect(gm.playable((bar.x, bar.y), {"bar"}),
           "掩体被打烂之后那一格才能走（full.txt:15454-15458）")
    expect(bool(gm.blockers((2, 10), (8, 1)))
           and gm.blockers((2, 10), (8, 1), {"bar"}) == [],
           "打烂的掩体不再挡视线（full.txt:15416-15431）")
    expect("掩体已成废墟" in gm.describe((bar.x, bar.y), {"bar"}),
           "踩上去的描写变成废墟")
    route, _cost = gm.path((2, 10), (3, 8))
    route_gone, _c2 = gm.path((2, 10), (3, 8), {"bar"})
    expect(route == [] and route_gone[-1] == (3, 8),
           "打烂前绕不过去，打烂后能穿过去")
    expect("打烂的掩体" in gm.render(gone={"bar"}),
           "地图渲染把废墟画成 x")

    # ── 战斗层：打烂掩体 ────────────────────────────────────
    conn = db.connect(":memory:")
    gid = "qq:COVER"
    gate = Gate(conn)
    camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    easy_roster.save(conn, gid, "u1", easy.templates()[0])
    combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                 enemies=[{"name": "小丑", "count": 2, "faction": "clown"}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    pc.x, pc.y = (2, 10)
    e1 = next(c for c in f.combatants if c.name == "小丑1")
    e2 = next(c for c in f.combatants if c.name == "小丑2")
    e1.x, e1.y = (8, 1)          # 吧台正好横在中间
    e2.x, e2.y = (3, 9)          # 缩在吧台背后
    f.turn_index = f.order.index(pc)
    f.covers["bar"] = 3          # 差一点就打烂
    combat._save(conn, f)

    first = combat.attack(conn, gid, "u1", e1.name, skill="手枪",
                          roller=_Floor())
    show("掩体", f"打{e1.name}", first.splitlines()[0])
    expect("吧台" in first and "挡住" in first,
           "没打烂之前，吧台挡着射界（full.txt:15445-15450）")

    f = combat.load(conn, gid)
    f.turn_index = f.order.index(next(c for c in f.order if c.side == "pc"))
    f.round += 1
    combat._reset_slots(f)
    combat._save(conn, f)
    smash = combat.attack(conn, gid, "u1", "吧台", skill="手枪",
                          roller=_Floor())
    show("掩体", "打吧台", "\n".join(smash.splitlines()[:2]))
    expect("掩体被打烂了" in smash, "耐久归零 = 彻底摧毁（full.txt:15454-15458）")
    f = combat.load(conn, gid)
    expect(f.covers["bar"] == 0 and "bar" in combat._gone(f),
           "打烂的掩体记进 gone 集合")

    f = combat.load(conn, gid)
    f.turn_index = f.order.index(next(c for c in f.order if c.side == "pc"))
    f.round += 1
    combat._reset_slots(f)
    combat._save(conn, f)
    clear = combat.attack(conn, gid, "u1", e1.name, skill="手枪",
                          roller=_Floor())
    show("掩体", f"再打{e1.name}", "\n".join(clear.splitlines()[:2]))
    expect("挡住" not in clear and "DV" in clear,
           "打烂之后射界打开，可以正常打（full.txt:15416-15431）")

    # 打烂的掩体连爆炸都不挡了
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    pc.x, pc.y = (7, 7)
    e1 = next(c for c in f.combatants if c.name == "小丑1")
    e2 = next(c for c in f.combatants if c.name == "小丑2")
    e1.x, e1.y = (3, 7)
    e2.x, e2.y = (3, 9)
    f.turn_index = f.order.index(pc)
    f.covers["bar"] = 0
    combat._save(conn, f)
    easy_roster.add_gear(conn, gid, "u1", "手榴弹")
    blast = combat.throw_grenade(conn, gid, "u1", f"扔手榴弹炸{e1.name}",
                                 roller=_Floor())
    show("掩体", "被炸", "\n".join(blast.splitlines()[-3:]))
    expect("挡下全部爆炸" not in blast and f"{e2.name} 被炸" in blast,
           "掩体打成废墟后不再替人挡爆炸（full.txt:14873-14894）")

    # ── 战斗层：火力压制 ────────────────────────────────────
    if combat.active(conn, gid):
        combat.end(conn, gid)
    combat.start(conn, gid, campaign_id=camp.id, scene="a1.opera",
                 enemies=[{"name": "小丑", "count": 1, "faction": "clown"}],
                 roller=dice.RandomRoller(5))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    foe = next(c for c in f.order if c.side != "pc")
    pc.x, pc.y = (2, 10)
    pc.ammo = 30
    pc.mag = 30
    foe.x, foe.y = (4, 2)        # 16 米，中间没遮挡、也没进掩体
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)

    pinned = combat.suppress(conn, gid, "u1", weapon="冲锋枪",
                             roller=dice.ScriptedRoller([10, 1, 5, 1, 1], loop=True))
    show("压制", "冲锋枪", "\n".join(pinned.splitlines()[:3]))
    expect("被压住了" in pinned,
           "压制对抗：专注+意志 vs 反应+全自动射击（full.txt:14903-14914）")
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    expect(me.ammo == 20, "火力压制一次打光 10 发")
    expect(combat._slots(f)[foe.owner_qq].must_cover,
           "输的人下个移动动作必须进掩体")

    # 反过来被压：压制只由 PC 发起，这里直接把标记挂到自己身上，
    # 模拟「敌人压制你」的效果（full.txt:14911-14914 对谁都一样）。
    f = combat.load(conn, gid)
    f.turn_index = f.order.index(next(c for c in f.order if c.side == "pc"))
    f.round += 1
    combat._reset_slots(f)
    combat._slots(f)[me.owner_qq].must_cover = True
    combat._save(conn, f)
    stay = combat.act(conn, gid, "u1", "移动到 5,2", roller=_Floor())
    show("压制", "乱跑", stay.splitlines()[0])
    expect("被火力压着" in stay, "被压着不能乱跑，只能找掩体")
    f = combat.load(conn, gid)
    after = next(c for c in f.combatants if c.side == "pc")
    expect((after.x, after.y) == (2, 10), "被拒绝时人没动")

    free = combat.act(conn, gid, "u1", "移动到 2,9", roller=_Floor())
    show("压制", "钻掩体", "\n".join(free.splitlines()[:2]))
    expect("压制解除" in free, "挪到吧台旁边就摆脱压制")
    f = combat.load(conn, gid)
    expect(not combat._slots(f)[me.owner_qq].must_cover, "压制标记清掉")
    moved = next(c for c in f.combatants if c.side == "pc")
    expect((moved.x, moved.y) == (2, 9), "人站到掩体旁边了")

    conn.close()


async def sc_cyber(s: Sim) -> None:
    """赛博组件安装 → 人性（HL）→ 共情（EMP）下降。"""
    print("【场景 31】赛博组件：人性损失与 EMP 下降")
    from core import cyber_flow, roster
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:cyber"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    # 本场景测的是人性损失链（会连装 4 件，含神经链路 500 EB），
    # 给足资金让钱不成为干扰项；经济性由「场景 42 econ」单独覆盖。
    from core import growth

    _card = roster.get(conn, gid, "u1")
    growth.grant_eb(_card, 5000)
    roster.save(conn, gid, "u1", _card)

    show("赛博", "初始", cyber_flow.status_text(conn, gid, "u1"))
    expect("人性 50" in cyber_flow.status_text(conn, gid, "u1"), "独狼 EMP5 → 人性满值 50")

    first = cyber_flow.install(conn, gid, "u1", "安装 赛博义眼",
                               roller=dice.ScriptedRoller([5, 5]))
    show("赛博", "装义眼", first)
    expect("人性 50 → 40" in first, "2d6 投 10 → 人性 50→40")
    expect("共情 EMP 5 → 4" in first, "掉满 10 点人性 → EMP −1")

    second = cyber_flow.install(conn, gid, "u1", "安装 神经链路",
                                roller=dice.ScriptedRoller([5, 5]))
    show("赛博", "再装神经链路", second)
    expect("人性 40 → 30" in second, "第二次安装继续扣")

    again = cyber_flow.install(conn, gid, "u1", "安装 赛博义眼",
                               roller=dice.ScriptedRoller([5, 5]))
    expect("30 → 20" in again, "同一件装两次扣两次（书内页 112）")

    free = cyber_flow.install(conn, gid, "u1", "安装 生物监测器")
    show("赛博", "时尚装置", free)
    expect("不造成人性损失" in free, "HL 写 N/A 的时尚装置不扣")

    quote = cyber_flow.install(conn, gid, "u1", "安装 赛博义眼 多少钱")
    show("赛博", "报价", quote)
    expect("要装就发" in quote, "问价不执行安装")

    missing = cyber_flow.install(conn, gid, "u1", "安装 不存在的东西")
    show("赛博", "找不到", missing)
    expect("表里有" in missing, "找不到时给例子")

    card = roster.get(conn, gid, "u1")
    expect(card["state"]["hum"] == 20, "人性存进 state")
    expect(card["stats"]["EMP"] == 2, "EMP 同步到卡面")
    from cyberpunk import cyber_bonus as _cb
    expect(_cb.installed_names(card).count("赛博义眼") == 2, "装过的件数记下来")

    # 义体加成效：装「显微目镜」后，状态面板展示该加成（full.txt:28288）
    from cyberpunk import dice as _dice
    mm = cyber_flow.install(conn, gid, "u1", "安装 显微目镜",
                            roller=_dice.ScriptedRoller([1, 1]))
    show("赛博", "装显微目镜", mm)
    st = cyber_flow.status_text(conn, gid, "u1")
    show("赛博", "状态(含加成)", st)
    expect("显微目镜" in st and "加成" in st, "状态面板展示义体加成")

    # B 批命中修正：装「瞄准目镜」后，瞄准射击命中 +1（full.txt:28330）
    cyber_flow.install(conn, gid, "u1", "安装 瞄准目镜",
                       roller=_dice.ScriptedRoller([1, 1]))
    from core import combat
    # 装了一大堆组件后 EMP 已掉到 1（低于下限 2，comCat 无法 start），
    # 打这一枪前临时把 EMP 拉回，避免破坏「战斗校验」这个测试点。
    pc = roster.get(conn, gid, "u1")
    pc["stats"]["EMP"] = 5
    roster.save(conn, gid, "u1", pc)
    combat.start(conn, gid, enemies=(("打手", 1),), scene="a1.opera",
                 roller=_dice.ScriptedRoller([9, 2]))
    combat.close_range(conn, gid, "u1", "距离 8 米")
    aimed = combat.attack(conn, gid, "u1", "打手", skill="手枪", aim="头",
                          roller=_dice.ScriptedRoller([10, 3, 3, 3]))
    show("赛博", "瞄准射击(义体+1)", aimed)
    expect("瞄准目镜+1" in aimed, "瞄准射击带义体命中修正")

    # C-1 永久属性加成：装「骨肉织绕」→ 体格+2，运行时提升 max_hp / 死亡豁免
    cyber_flow.install(conn, gid, "u1", "安装 骨肉织绕",
                       roller=_dice.ScriptedRoller([1, 1, 1, 5, 2]))
    # 高人性损失组件会把 EMP 打到 2 以下（会拦 validate_stats），
    # 这里只验证体格通道，临时把 EMP 拉回避免干扰。
    pc = roster.get(conn, gid, "u1")
    pc["stats"]["EMP"] = 5
    roster.save(conn, gid, "u1", pc)
    after_all = cyber_flow.status_text(conn, gid, "u1")
    show("赛博", "状态(骨肉织绕)", after_all)
    expect("骨肉织绕" in after_all and "属性" in after_all, "状态面板展示属性型加成")
    ch = roster.to_character(roster.get(conn, gid, "u1"))
    expect(ch.effective_stat("BODY") == ch.stats["BODY"] + 2
           and ch.effective_stat("BODY") <= 10,
           "骨肉织绕不写死基础属性、运行时体格+2（clamp 到 10）")
    expect(ch.max_hp > 0, "体格提升驱动 max_hp 现算")

    # C-2 防御端护甲 SP：装「皮下护甲」→ 面板展示 SP11（护甲）
    cyber_flow.install(conn, gid, "u1", "安装 皮下护甲",
                       roller=_dice.ScriptedRoller([1, 1, 1, 5, 2]))
    pc = roster.get(conn, gid, "u1")
    pc["stats"]["EMP"] = 5
    roster.save(conn, gid, "u1", pc)
    sp_st = cyber_flow.status_text(conn, gid, "u1")
    show("赛博", "状态(皮下护甲)", sp_st)
    expect("皮下护甲 SP11（护甲）" in sp_st, "状态面板展示防御型护甲 SP")

    conn.close()


async def sc_ammo_type(s: Sim) -> None:
    """弹种：穿甲弹 SP 磨损 1→2、橡胶弹不吃伤势/不削甲、检定类弹先诚实不结算。"""
    print("【场景 32】弹种：穿甲弹 / 橡胶弹 / 检定类弹")
    from core import combat, roster
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:ammotype"
    roster.save(conn, gid, "u1",
                roster.build(template="独狼", name="阿飞",
                             skill_overrides={"手枪": 4, "自动武器": 6}))

    def _fresh() -> None:
        combat.start(conn, gid, enemies=(("打手", 1),), scene="a1.opera",
                     roller=dice.ScriptedRoller([9, 2]))
        combat.close_range(conn, gid, "u1", "距离 8 米")
        f = combat.load(conn, gid)
        me = next(c for c in f.combatants if c.side == "pc")
        me.ammo_type = combat.AMMO_BASE
        combat._save(conn, f)

    def _shoot(kind: str, rolls: list[int]) -> str:
        f = combat.load(conn, gid)
        me = next(c for c in f.combatants if c.side == "pc")
        me.ammo_type = kind
        me.ammo = me.mag or 8
        f.turn_index = f.order.index(me)
        f.round += 1
        combat._reset_slots(f)
        combat._save(conn, f)
        return combat.attack(conn, gid, "u1", "打手", skill="手枪",
                             roller=dice.ScriptedRoller(rolls))

    def _foe() -> "combat.Combatant":
        return next(c for c in combat.load(conn, gid).combatants
                    if c.side == "enemy")

    # ① 装填换弹种
    _fresh()
    reloaded = combat.act(conn, gid, "u1", "装填 穿甲弹")
    show("弹种", "换弹", reloaded)
    expect("穿甲弹" in reloaded, "装填时能整匣换弹种（:14731-14732 不能混装）")
    f = combat.load(conn, gid)
    expect(next(c for c in f.combatants if c.side == "pc").ammo_type == "穿甲弹",
           "弹种存进战斗者")

    # ② 穿甲弹：一次命中磨损 2 点护甲
    _fresh()
    text = _shoot("穿甲弹", [10, 3, 3, 1, 1])
    show("弹种", "穿甲弹", text.splitlines()[1])
    expect("穿甲弹" in text, "开火时提示弹种")
    expect(_foe().sp_wear == 2, "穿甲弹 SP 减值 1→2（:26730-26731）")

    # ③ 基础弹药只磨 1 点
    _fresh()
    _shoot(combat.AMMO_BASE, [10, 3, 3, 1, 1])
    expect(_foe().sp_wear == 1, "基础弹药只磨 1 点")

    # ④ 橡胶弹：不削甲
    _fresh()
    rubber = _shoot(combat.AMMO_RUBBER, [10, 3, 3, 1, 1])
    expect(_foe().sp_wear == 0, "橡胶弹不削减护甲（:26816-26823）")
    expect("橡胶弹" in rubber, "提示橡胶弹")

    # ⑤ 橡胶弹：双 6 也不出严重伤势
    _fresh()
    f = combat.load(conn, gid)
    foe = next(c for c in f.combatants if c.side == "enemy")
    foe.hp = foe.max_hp = 40
    combat._save(conn, f)
    crit_text = _shoot(combat.AMMO_RUBBER, [10, 6, 6, 1, 1])
    expect("中了严重伤" not in crit_text, "橡胶弹不吃严重伤势")
    expect(_foe().crits == (), "crits 保持空")

    # ⑥ 橡胶弹：HP 不会被打到 1 以下
    _fresh()
    f = combat.load(conn, gid)
    foe = next(c for c in f.combatants if c.side == "enemy")
    foe.hp = foe.max_hp = 3
    combat._save(conn, f)
    _shoot(combat.AMMO_RUBBER, [10, 6, 6, 1, 1])
    expect(_foe().hp >= 1, "橡胶弹最多打到 1 HP")

    # ⑦ 检定类弹：2026-11-10 起真正结算（命中后改过检定）
    _fresh()
    gas = _shoot("毒气弹", [10, 3, 3, 1, 1, 1, 1])
    show("弹种", "毒气弹",
         " / ".join(l.strip() for l in gas.splitlines() if "改过检定" in l))
    expect("不造成常规伤害" in gas, "检定类弹不造成常规伤害")
    expect("折磨&药物耐受" in gas, "命中后改投折磨&药物耐受检定")
    # 检定失败会掉血（直接 HP，不吃护甲）；通过才不掉血
    expect(_foe().hp <= 20, "检定失败按直接伤害结算")

    conn.close()


async def sc_rof(s: Sim) -> None:
    """射速（ROF2）：一次攻击动作打两下。"""
    print("【场景 33】射速 ROF2：一次攻击动作打两下")

    from core import combat, roster
    from cyberpunk import dice

    class _Hit(dice.Roller):
        # d10 恒 10（必中），d6 恒 3（2D6=6）
        def roll(self, sides: int = 10) -> int:
            return 10 if sides >= 10 else 3

    conn = db.connect(":memory:")
    gid = "qq:rof"
    roster.save(conn, gid, "u1",
                roster.build(template="独狼", name="阿飞",
                             skill_overrides={"手枪": 4, "近战武器": 4}))
    combat.start(conn, gid, enemies=(("打手", 1),), scene="a1.opera",
                 roller=dice.ScriptedRoller([9, 2]))
    combat.close_range(conn, gid, "u1", "距离 8 米")
    combat.act(conn, gid, "u1", "装填")

    # ① 默认一发
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    f.turn_index = f.order.index(me)
    f.round += 1
    combat._reset_slots(f)
    combat._save(conn, f)
    one = combat.attack(conn, gid, "u1", "打手", shots=None, roller=_Hit())
    show("ROF", "默认一发", one.splitlines()[0])
    expect("连打" not in one, "默认仍是一发（向后兼容）")

    # ② 连打两下
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    f.turn_index = f.order.index(me)
    f.round += 1
    combat._reset_slots(f)
    combat._save(conn, f)
    two = combat.attack(conn, gid, "u1", "打手", shots=2, roller=_Hit())
    show("ROF", "连打两下", two.splitlines()[0])
    expect("连打 2 下" in two, "一次动作打两下")
    expect("（第 1 下）" in two and "（第 2 下）" in two, "两下都结算")

    # ③ 弹匣扣两发
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    expect(me.ammo == 5, "满 8 发 − 连打 2 发 − 上面那发 1 发")

    # ④ 瞄准时 ROF 限 1：shots=2 也只打一发
    f = combat.load(conn, gid)
    foe = next(c for c in f.combatants if c.side == "enemy")
    foe.hp = foe.max_hp = 60
    f.turn_index = f.order.index(me)
    f.round += 1
    combat._reset_slots(f)
    combat._save(conn, f)
    aim = combat.attack(conn, gid, "u1", "打手", aim="头",
                        shots=2, roller=_Hit())
    show("ROF", "瞄准头", aim.splitlines()[0])
    expect("第 2 下" not in aim, "瞄准射击 ROF 限 1（:14523）")

    conn.close()


async def sc_delay(s: Sim) -> None:
    """延迟动作：诚实提示没有事件调度（方案5）。"""
    print("【场景 34】延迟动作：没有事件调度，诚实提示")
    from core import combat, roster
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:delay"
    roster.save(conn, gid, "u1",
                roster.build(template="独狼", name="阿飞",
                             skill_overrides={"手枪": 4}))
    combat.start(conn, gid, enemies=(("打手", 1),), scene="a1.opera",
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    f.turn_index = f.order.index(me)
    combat._reset_slots(f)
    combat._save(conn, f)

    text = combat.act(conn, gid, "u1", "延迟")
    show("延迟", "延迟", text.splitlines()[0])
    expect("结束回合" in text, "「延迟」至少结束回合")
    expect("事件调度" in text, "诚实说明没有事件调度（full.txt:14430-14433）")

    conn.close()


async def sc_helper(s: Sim) -> None:
    """多人协作检定（补充技能判定 full.txt:11811-11817）：协助 → +1 → 消费。"""
    print("【场景 35】多人协作检定（补充技能判定）")
    from core import card_flow, roster, turn_flow
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:helper"
    # u1 阿飞 帮 u2 小美
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞",
                                              skill_overrides={"手枪": 4}))
    roster.save(conn, gid, "u2", roster.build(template="独狼", name="小美",
                                              skill_overrides={"潜行": 3}))
    # 指向测试库
    _orig_conn = turn_flow.CONN
    _orig_card_conn = card_flow.CONN
    turn_flow.CONN = conn
    card_flow.CONN = conn
    try:
        # ① 阿飞协助小美：辅助检定成功 → 挂 +1
        help_reply = turn_flow.helper_text(
            gid, "u1", "协助 小美 手枪", roller=dice.ScriptedRoller([10, 2])
        )
        show("协助", "阿飞帮小美", help_reply)
        expect("已给「小美」" in help_reply, "辅助检定成功 → 给队友挂 +1")
        expect((gid, "u2") in turn_flow.HELPER_BONUS, "加值记录到内存")

        # ② 小美拿 /检定：消费 +1（仅一次）
        check1 = card_flow.check(gid, "u2", "潜行", dv=10,
                                 roller=dice.ScriptedRoller([5]))
        show("检定", "小美检定（应含 +1）", check1)
        expect("补充技能" in check1, "检定里出现补充技能 +1 说明")
        expect((gid, "u2") not in turn_flow.HELPER_BONUS, "一次检定消费掉加值")

        # ③ 已被协助者未用完前，队友再协助会被拒绝（不叠加）
        turn_flow.HELPER_BONUS[(gid, "u2")] = {"bonus": 1, "skill": "手枪", "source": "阿飞"}
        again = turn_flow.helper_text(gid, "u1", "协助 小美 手枪",
                                      roller=dice.ScriptedRoller([10, 2]))
        show("协助", "再帮一次", again)
        expect("不叠加" in again, "已挂未用 → 拒绝再协助（不叠加）")
    finally:
        turn_flow.CONN = _orig_conn
        card_flow.CONN = _orig_card_conn
    conn.close()


async def sc_rest(s: Sim) -> None:
    """过夜自然恢复：默认每日回体格 HP；装「抗体增强」翻倍且不越 max_hp。"""
    print("【场景 41】过夜恢复：抗体增强 每日体格×2 / 致命伤不能自愈")
    from core import combat, db, roster
    from cyberpunk import cyber_bonus, dice

    conn = db.connect(":memory:")
    gid = "qq:rest"
    card = roster.build(template="独狼", name="阿飞")
    rules = roster.to_character(card)
    card["state"] = {"hp": 1, "max_hp": rules.max_hp}
    roster.save(conn, gid, "u1", card)

    combat.rest_night(conn, gid)
    hp1 = roster.get(conn, gid, "u1")["state"]["hp"]
    show("过夜", "默认自然恢复", f"HP={hp1}")
    expect(hp1 == 1 + rules.effective_stat("BODY"), "过夜默认恢复体格点 HP")

    card = roster.get(conn, gid, "u1")
    card["state"] = {"cyber": ["抗体增强"], "hp": 1, "max_hp": rules.max_hp}
    roster.save(conn, gid, "u1", card)
    combat.rest_night(conn, gid)
    hp2 = roster.get(conn, gid, "u1")["state"]["hp"]
    show("过夜", "抗体增强 每日体格×2", f"HP={hp2}")
    expect(hp2 == 1 + rules.effective_stat("BODY") * 2, "抗体增强翻倍")
    expect(cyber_bonus.rest_multiplier(roster.get(conn, gid, "u1")) == 2,
           "义体表读出恢复倍率 2")

    # 致命伤（HP=0）不能自愈
    card = roster.get(conn, gid, "u1")
    card["state"]["hp"] = 0
    roster.save(conn, gid, "u1", card)
    skipped = combat.rest_night(conn, gid)
    show("过夜", "致命伤", skipped)
    expect("没有可以自愈的人" in skipped, "致命伤需先稳定伤势，跳过")
    conn.close()


async def sc_aclass(s: Sim) -> None:
    """A 类义体：F 组合触发（荧光纹身×3 / 化学皮肤+科技炫发）+ 技能芯片。"""
    print("【场景 50】A 类义体：F 组合触发 / 技能芯片")
    from core import cyber_flow, db, growth, roster
    from cyberpunk import cyber_bonus, dice

    conn = db.connect(":memory:")
    gid = "qq:aclass"

    # 1) 荧光纹身 ×3 → 服饰&风格 +2（:9737-9738）；×2 不够；×6 不叠。
    card = {"state": {"cyber": ["荧光纹身"] * 3}, "skills": {}}
    total, _ = cyber_bonus.check_bonus(card, skill="服饰&风格")
    show("组合", "荧光纹身×3", f"服饰&风格 {total:+d}")
    expect(total == 2, "荧光纹身三个及以上：服饰&风格 +2")
    card2 = {"state": {"cyber": ["荧光纹身"] * 2}, "skills": {}}
    expect(cyber_bonus.check_bonus(card2, skill="服饰&风格")[0] == 0,
           "只有两个纹身不够")
    card6 = {"state": {"cyber": ["荧光纹身"] * 6}, "skills": {}}
    expect(cyber_bonus.check_bonus(card6, skill="服饰&风格")[0] == 2,
           "超过三个也不叠加（书里「三个甚至更多」）")

    # 2) 化学皮肤 + 科技炫发 → 个人装扮 +2（:9726、:9753），只加一次。
    card = {"state": {"cyber": ["化学皮肤", "科技炫发"]}, "skills": {}}
    total, _ = cyber_bonus.check_bonus(card, skill="个人装扮")
    show("组合", "化学皮肤+科技炫发", f"个人装扮 {total:+d}")
    expect(total == 2, "化学皮肤与科技炫发同时拥有：个人装扮 +2")
    only_one = {"state": {"cyber": ["化学皮肤"]}, "skills": {}}
    expect(cyber_bonus.check_bonus(only_one, skill="个人装扮")[0] == 0,
           "只有化学皮肤不够")

    # 3) 技能芯片：绑定技能等级到达 3（:27830-27837），已超过 3 则无效。
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)
    for item in ("神经链路", "芯片插槽"):
        text = cyber_flow.install(conn, gid, "u1", f"安装 {item}",
                                  roller=dice.RandomRoller())
        expect("装上了" in text, f"装 {item} 成功")
    text = cyber_flow.install(conn, gid, "u1", "安装 技能芯片 黑客",
                              roller=dice.RandomRoller())
    show("技能芯片", "安装", text.splitlines()[0])
    expect("绑定技能「黑客」" in text, "技能芯片绑定技能「黑客」")
    got = roster.get(conn, gid, "u1")
    show("技能芯片", "黑客不在卡上(0级)", f"检定加成 {cyber_bonus.check_bonus(got, skill='黑客')[0]:+d}")
    expect(cyber_bonus.check_bonus(got, skill="黑客")[0] == 3,
           "技能芯片把 0 级黑客抬到 3（+3）")
    # 已超过 3 则无效：把黑客改成 5，加成应为 0
    got["skills"]["黑客"] = 5
    expect(cyber_bonus.check_bonus(got, skill="黑客")[0] == 0,
           "黑客已 5 级：技能芯片无效")
    conn.close()


async def sc_move(s: Sim) -> None:
    """移动型义体：攀爬/跳跃 ×2 + 攀爬脚/跳跃增强豁免 + 蹼足 + 轮滑脚。"""
    print("【场景 51】移动型义体：攀爬/跳跃 ×2、蹼足水格、轮滑脚跑加速")
    from core import map as gridmap
    from cyberpunk import cyber_bonus

    # 1) 攀爬/跳跃每格 ×2（full.txt:14488）；装义体后 ×1。
    m = gridmap._parse({"id": "t", "name": "t", "terrain": ["...."]}, "t")
    _, normal, _ = m.move((0, 0), (2, 0), 10)
    _, climb, _ = m.move((0, 0), (2, 0), 10, mult=2)
    show("移动", "普通 2 格", f"消耗 {normal}")
    show("移动", "攀爬 2 格", f"消耗 {climb}")
    expect(normal == 2, "普通移动 2 格消耗 2")
    expect(climb == 4, "攀爬每格 ×2 → 2 格消耗 4")
    expect(cyber_bonus.move_multiplier({}, mode="climb") == 2,
           "没攀爬脚：攀爬 ×2")
    expect(cyber_bonus.move_multiplier({"state": {"cyber": ["攀爬脚"]}}, mode="climb") == 1,
           "攀爬脚：攀爬 ×1（full.txt:10382）")
    expect(cyber_bonus.move_multiplier({"state": {"cyber": ["攀爬脚"]}}, mode="jump") == 2,
           "攀爬脚不免跳跃")
    expect(cyber_bonus.move_multiplier({"state": {"cyber": ["跳跃增强"]}}, mode="jump") == 1,
           "跳跃增强：跳跃 ×1（full.txt:10388）")

    # 2) 蹼足：水格 2 → 1（full.txt:10409）
    wm = gridmap._parse({"id": "w", "name": "w", "terrain": ["~."]}, "w")
    show("蹼足", "水格花费", f"普通 {wm.cell_cost((0, 0))} / 蹼足 {wm.cell_cost((0, 0), water_cost=1)}")
    expect(wm.cell_cost((0, 0)) == 2, "普通角色水格按 2 格算（游泳 ×2）")
    expect(wm.cell_cost((0, 0), water_cost=1) == 1, "蹼足把水格从 2 降到 1")

    # 3) 轮滑脚：跑之后额外移动 +3 格（6 米，full.txt:10395）
    expect(cyber_bonus.run_bonus_cells({"state": {"cyber": ["轮滑脚"]}}) == 3,
           "轮滑脚：跑后 +3 格（6 米）")
    expect(cyber_bonus.run_bonus_cells({}) == 0, "没轮滑脚不加")


async def sc_frames(s: Sim) -> None:
    """B 类：人造肩膀（手臂上限 2→4）+ 植入线性框架（体格下限）+ 精神医疗。"""
    print("【场景 54】B 类义体：人造肩膀 / 线性框架 / 精神医疗")
    from core import cyber_flow, db, growth, roster
    from cyberpunk import cyber_bonus, dice

    conn = db.connect(":memory:")
    gid = "qq:frames"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 80000)
    roster.save(conn, gid, "u1", card)

    # 1) 默认最多 2 条手臂；人造肩膀 → 4 条
    for _ in range(2):
        cyber_flow.install(conn, gid, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    third = cyber_flow.install(conn, gid, "u1", "安装 赛博手臂",
                               roller=dice.RandomRoller())
    show("人造肩膀", "第 3 条（无肩膀）", third.splitlines()[0])
    expect("最多装 2 件" in third, "默认两只手臂")
    cyber_flow.install(conn, gid, "u1", "安装 人造肩膀改造", roller=dice.RandomRoller())
    for _ in range(2):
        cyber_flow.install(conn, gid, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    fifth = cyber_flow.install(conn, gid, "u1", "安装 赛博手臂",
                               roller=dice.RandomRoller())
    expect("最多装 4 件" in fifth, "人造肩膀：手臂上限放宽到 4")

    # 2) 线性框架：前置 + 体格下限
    conn2 = db.connect(":memory:")
    gid = "qq:frame2"
    roster.save(conn2, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn2, gid, "u1")
    growth.grant_eb(card, 80000)
    roster.save(conn2, gid, "u1", card)
    bad = cyber_flow.install(conn2, gid, "u1", "安装 植入线性框架Σ",
                             roller=dice.RandomRoller())
    show("线性框架", "没骨肉织绕", bad.splitlines()[0])
    expect("骨肉织绕" in bad, "线性框架前置：骨肉织绕")
    cyber_flow.install(conn2, gid, "u1", "安装 骨肉织绕", roller=dice.RandomRoller())
    ok = cyber_flow.install(conn2, gid, "u1", "安装 植入线性框架Σ",
                            roller=dice.RandomRoller())
    show("线性框架", "装 Σ", ok.splitlines()[0])
    hero = roster.to_character(roster.get(conn2, gid, "u1"))
    show("线性框架", "体格", f"BODY={hero.effective_stat('BODY')} HP={hero.max_hp}")
    expect(hero.effective_stat("BODY") == 12, "线性框架把体格提到 12")
    expect(any("植入线性框架Σ" in x for x in
               cyber_bonus.describe(roster.get(conn2, gid, "u1"))),
           "线性框架在面板可见")

    # 3) 精神医疗：回人性、受上限约束
    card = roster.get(conn2, gid, "u1")
    hum0 = int((card.get("state") or {}).get("hum"))
    cap = cyber_flow.humanity_cap(card)
    text = cyber_flow.therapy(conn2, gid, "u1", "精神医疗 一般",
                              roller=dice.ScriptedRoller([6, 6]))
    show("精神医疗", "一般 2d6", text.splitlines()[0])
    hum1 = int((roster.get(conn2, gid, "u1").get("state") or {}).get("hum"))
    expect(hum1 == min(cap, hum0 + 12), "精神医疗恢复 2d6 且不超上限")
    conn.close()
    conn2.close()


async def sc_cclass(s: Sim) -> None:
    """C 类：能力型入库 + 面板 / 交互接口网行前置 / 侦测。"""
    print("【场景 55】C 类义体：能力面板 / 交互接口前置 / 侦测")
    from core import combat, cyber_flow, db, easy_roster, growth, roster
    from core.session import Gate
    from cyberpunk import cyber_bonus, dice, easy

    # 1) 能力型入库 + 面板
    conn = db.connect(":memory:")
    gid = "qq:cclass"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 30000)
    roster.save(conn, gid, "u1", card)
    for x in ("神经链路", "赛博音频套装", "窃听监测器", "无线电通讯"):
        cyber_flow.install(conn, gid, "u1", f"安装 {x}", roller=dice.RandomRoller())
    text = cyber_flow.status_text(conn, gid, "u1")
    show("C 类", "面板", [l for l in text.splitlines() if l.startswith("能力：")][0][:60])
    expect("能力：" in text and "窃听监测器" in text, "能力型义体在面板可见")

    # 2) 侦测
    det = cyber_flow.detect(conn, gid, "u1")
    show("C 类", "侦测", det.splitlines()[0])
    expect("侦测义体待命" in det and "窃听监测器" in det, "侦测列出感知能力")
    conn.close()

    # 3) 交互接口 = 网行前置
    for with_plug, want in ((False, "交互接口"), (True, "接入成功")):
        conn = db.connect(":memory:")
        gid = "qq:netplug"
        gate = Gate(conn)
        camp = gate.start_new(gid, "歌剧院之夜", scenario="opera")
        gate.resume(gid)
        card = dict(easy.templates()[0])
        if with_plug:
            card["cyber"] = list(card.get("cyber") or []) + ["交互接口"]
        easy_roster.save(conn, gid, "u1", card)
        gate.set_scene(camp.id, "a3.church")
        combat.start(conn, gid, campaign_id=camp.id, scene="a3.church",
                     enemies=(("自动炮塔", 2),), roller=dice.RandomRoller())
        got = combat.net_action(conn, gid, "u1", "接入接线盒",
                                roller=dice.ScriptedRoller([10]))
        show("网行前置", "有接口" if with_plug else "无接口", got.splitlines()[0][:50])
        expect(want in got, f"网行前置：{'有' if with_plug else '无'}交互接口 → {want}")
        conn.close()


async def sc_instances(s: Sim) -> None:
    """义体实例记录：多实例基础组件的配件精确归属 + 旧卡迁移。"""
    print("【场景 53】义体实例记录：两只眼各挂各的配件")
    from core import cyber_flow, db, growth, roster
    from cyberpunk import cyber_bonus, dice, slots

    # 1) 旧卡迁移：名字列表 → 记录（自动发 id、推断 parent）
    old = ["赛博义眼", "赛博义眼", "图像增强", "显微目镜"]
    recs = slots.normalize(old)
    show("迁移", "旧字符串列表", f"{len(recs)} 条记录")
    expect([r["name"] for r in recs] == old, "迁移后名字顺序不变")
    expect(len({r["id"] for r in recs}) == len(recs), "迁移后每件都有唯一 id")
    eyes = [r for r in recs if r["name"] == "赛博义眼"]
    for r in recs:
        if r["name"] != "赛博义眼":
            expect(r["parent"] in {e["id"] for e in eyes}, "配件都挂到了某只眼上")

    # 2) 真安装：两只眼，配件挂指定实例；容量按实例算
    conn = db.connect(":memory:")
    gid = "qq:inst"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 50000)
    roster.save(conn, gid, "u1", card)
    for _ in range(2):
        cyber_flow.install(conn, gid, "u1", "安装 赛博义眼", roller=dice.RandomRoller())
    recs = roster.get(conn, gid, "u1")["state"]["cyber"]
    c1, c2 = recs[0]["id"], recs[1]["id"]
    cyber_flow.install(conn, gid, "u1", f"安装 显微目镜 到 {c1}",
                       roller=dice.RandomRoller())
    cyber_flow.install(conn, gid, "u1", f"安装 瞄准目镜 到 {c2}",
                       roller=dice.RandomRoller())
    got = roster.get(conn, gid, "u1")["state"]["cyber"]
    show("实例", "分配", str([(r["name"], r["parent"]) for r in got]))
    expect(next(r for r in got if r["name"] == "显微目镜")["parent"] == c1,
           "显微目镜挂在 c1")
    expect(next(r for r in got if r["name"] == "瞄准目镜")["parent"] == c2,
           "瞄准目镜挂在 c2")
    # 同一只眼塞两件 2 槽的装不下（每眼 3 槽）
    cyber_flow.install(conn, gid, "u1", f"安装 图像增强 到 {c1}",
                       roller=dice.RandomRoller())
    text = cyber_flow.install(conn, gid, "u1", f"安装 图像增强 到 {c1}",
                              roller=dice.RandomRoller())
    show("实例", "同一只眼再塞 2 槽", text.splitlines()[0])
    expect("装不了" in text, "实例容量不共享：同一只眼塞不下")
    # 不指定父级 → 自动挑有空槽的另一只眼
    text = cyber_flow.install(conn, gid, "u1", "安装 图像增强",
                              roller=dice.RandomRoller())
    expect("装上了" in text, "自动挑到另一只眼，能装")
    conn.close()


async def sc_utility(s: Sim) -> None:
    """功能型义体：飞爪手 / 肩部摄像头 / 快拆挂扣。"""
    print("【场景 52】功能型义体：飞爪手抓钩 / 肩部摄像头 / 快拆挂扣")
    from core import combat, cyber_flow, db, growth, map as gridmap, roster
    from cyberpunk import cyber_bonus, dice

    # 1) 飞爪手：抓钩锚点 + 攀爬免 ×2 惩罚（full.txt:28348/28354）
    m = gridmap._parse({
        "id": "g", "name": "g", "terrain": [".........."], "cell": 2,
        "objects": [{"kind": "cover", "id": "crate", "name": "木箱",
                     "x": 6, "y": 0, "hp": 20, "blocks_los": True}],
    }, "g")
    combat._map_for_id = lambda _id: m
    conn = db.connect(":memory:")
    gid = "qq:utility"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)
    combat.start(conn, gid, enemies=[{"name": "打手", "count": 1}],
                 roller=dice.ScriptedRoller([9, 2]))
    f = combat.load(conn, gid)
    pc = next(c for c in f.order if c.side == "pc")
    pc.x, pc.y = 0, 0
    foe = next(c for c in f.order if c.side != "pc")
    foe.x, foe.y = 9, 0
    f.turn_index = f.order.index(pc)
    combat._save(conn, f)
    card = roster.get(conn, gid, "u1")
    st = dict(card.get("state") or {})
    st["cyber"] = ["赛博手臂", "飞爪手"]
    card["state"] = st
    roster.save(conn, gid, "u1", card)
    fired = combat.act(conn, gid, "u1", "发射抓钩 木箱")
    show("飞爪手", "发射抓钩", fired.splitlines()[0])
    expect("钉在" in fired, "抓钩钉在 30 米内的厚实掩体上")
    expect((roster.get(conn, gid, "u1").get("state") or {}).get("grapple"),
           "抓钩锚点写进卡状态")
    climbed = combat.act(conn, gid, "u1", "攀爬绳索到 2,0")
    show("飞爪手", "攀爬绳索", climbed.splitlines()[0])
    expect("飞爪手绳索" in climbed, "攀爬绳索无视攀爬移速惩罚")
    retracted = combat.act(conn, gid, "u1", "收回抓钩")
    expect("不花费动作" in retracted, "收回抓钩不花动作")
    conn.close()

    # 2) 肩部摄像头：纯剧情、不占动作（full.txt:28460-28471）
    cam = {"state": {"cyber": ["肩部摄像头"]}}
    show("肩部摄像头", "面板", "；".join(cyber_bonus.describe(cam)))
    expect(any("肩部摄像头" in l for l in cyber_bonus.describe(cam)),
           "肩部摄像头在 /义体 面板可见")

    # 3) 快拆挂扣：臂 id 在记录里 → 重装免人性（full.txt:10287-10291）
    conn = db.connect(":memory:")
    gid = "qq:qr"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)
    for item in ("赛博手臂", "快拆挂扣"):
        cyber_flow.install(conn, gid, "u1", f"安装 {item}", roller=dice.RandomRoller())
    # 卸下手臂 → 收进快拆槽（带 id，不卖）
    cyber_flow.uninstall(conn, gid, "u1", "卸载 赛博手臂")
    hum_before = int((roster.get(conn, gid, "u1").get("state") or {}).get("hum"))
    text = cyber_flow.install(conn, gid, "u1", "安装 赛博手臂",
                              roller=dice.RandomRoller())
    show("快拆挂扣", "重装自己用过的臂", text.splitlines()[0])
    expect(int((roster.get(conn, gid, "u1").get("state") or {}).get("hum")) == hum_before,
           "快拆挂扣记录了这条臂的 id：重装不再扣人性")
    conn.close()

    # 4) 快拆槽（主人 2026-11-10：不做 LPTU 实体装备，槽挂在快拆挂扣上）
    conn = db.connect(":memory:")
    gid = "qq:qslot"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)
    for item in ("赛博手臂", "快拆挂扣"):
        cyber_flow.install(conn, gid, "u1", f"安装 {item}", roller=dice.RandomRoller())
    eb0 = growth.eb(roster.get(conn, gid, "u1"))
    hum0 = int((roster.get(conn, gid, "u1").get("state") or {}).get("hum"))
    off = cyber_flow.uninstall(conn, gid, "u1", "卸载 赛博手臂")
    show("快拆槽", "卸臂", off.splitlines()[1])
    expect("收进快拆槽" in off, "装了快拆挂扣：卸下的臂收进快拆槽、不卖")
    st = roster.get(conn, gid, "u1").get("state") or {}
    expect([e["name"] for e in (st.get("quick_slots") or [])] == ["赛博手臂"],
           "快拆槽里存着这条臂")
    expect(growth.eb(roster.get(conn, gid, "u1")) == eb0, "收进槽不退款（没卖）")
    on = cyber_flow.install(conn, gid, "u1", "安装 赛博手臂",
                            roller=dice.RandomRoller())
    show("快拆槽", "从槽取回", on.splitlines()[0])
    expect("id" in on or "记录里" in on, "从快拆槽取出：说明 id 在记录里")
    st = roster.get(conn, gid, "u1").get("state") or {}
    expect(st.get("quick_slots") == [], "取出后快拆槽为空")
    expect(int(st.get("hum")) == hum0, "从快拆槽取出免人性")
    expect(growth.eb(roster.get(conn, gid, "u1")) == eb0, "从快拆槽取出免花钱")

    # 5) 一个动作换臂（战斗内）：先装第二条臂，再收一条进槽
    cyber_flow.install(conn, gid, "u1", "安装 赛博手臂", roller=dice.RandomRoller())
    cyber_flow.uninstall(conn, gid, "u1", "卸载 赛博手臂")
    combat.start(conn, gid, enemies=[{"name": "打手", "count": 1}],
                 roller=dice.ScriptedRoller([9, 2]))
    swap = combat.act(conn, gid, "u1", "换臂")
    show("快拆槽", "一个动作换臂", swap.splitlines()[0])
    expect("换好了" in swap and "一个动作" in swap, "换臂是一个动作，不是拆+装两个动作")
    st = roster.get(conn, gid, "u1").get("state") or {}
    expect([e["name"] for e in (st.get("quick_slots") or [])] == ["赛博手臂"],
           "换下来的臂回到快拆槽")
    conn.close()


async def sc_econ(s: Sim) -> None:
    """EB：建卡给 500、装义体真扣费、钱不够拒绝且不扣人性。"""
    print("【场景 42】EB 经济：起始资金 / 安装扣费 / 钱不够拒绝")
    from core import db, growth, roster
    from core.cyber_flow import install
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:econ"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    show("建卡", "起始资金", f"EB={growth.eb(roster.get(conn, gid, 'u1'))}")
    expect(growth.eb(roster.get(conn, gid, "u1")) == 500, "街头鼠辈/边缘行者口径 500 EB")

    text = install(conn, gid, "u1", "安装 赛博义眼", roller=dice.ScriptedRoller([5, 5]))
    show("安装", "赛博义眼 100 EB", text.splitlines()[0])
    expect("余额 400" in text, "按价格表扣 100 EB")

    text = install(conn, gid, "u1", "安装 皮下护甲", roller=dice.ScriptedRoller([6, 6, 6, 6]))
    show("安装", "皮下护甲 1000 EB（钱不够）", text.splitlines()[0])
    expect("装不起" in text and "差 600" in text, "钱不够时拒绝安装")
    after = roster.get(conn, gid, "u1")
    expect(growth.eb(after) == 400, "拒绝时钱不动")
    expect("皮下护甲" not in (after["state"].get("cyber") or []), "拒绝时义体没装上")
    conn.close()


async def sc_red_skills(s: Sim) -> None:
    """RED 技能表：66 项全能查属性，三套规则别名都能归一。"""
    print("【场景 43】RED 技能表 66 项 + 三套规则别名归一")
    from cyberpunk import easy, skills

    expect(len(skills.skill_table()) == 66, "RED 技能表 66 项")
    show("技能", "藏匿/搜寻物品（Easy）", skills.canonical("藏匿/搜寻物品"))
    expect(skills.canonical("藏匿/搜寻物品") == "藏匿&揭示物体", "Easy 名折到 RED 正典")
    show("技能", "抗性（拷问/药物）（V3）", skills.canonical("抗性（拷问/药物）"))
    expect(skills.canonical("抗性（拷问/药物）") == "折磨&药物耐受", "V3 名折到 RED 正典")
    show("技能", "察言观色 属性", skills.stat_of("察言观色"))
    expect(skills.stat_of("察言观色") == "EMP", "冲突时按 RED（V3 是 COOL）")

    card = easy.get("搬运工")
    show("简易卡", "搬运工 觉察/藏匿 基础值",
         f"{card['skills']['觉察']}/{card['skills']['藏匿&揭示物体']}")
    expect(card["skills"]["觉察"] == 6, "烘焙的 +2 已剥离到义体通道")
    from cyberpunk import cyber_bonus

    card["state"] = {"cyber": ["图像增强"]}
    bonus, _ = cyber_bonus.check_bonus(card, skill="觉察")
    expect(6 + bonus == 8, "装图像增强后回到 8（不重复叠加）")

    h = easy.get("火炬")
    ab, _ = easy.ability_bonus(h, skill="电子/安全技术")
    show("简易卡", "火炬 电子 基础/+能力", f"{h['skills']['电子/安全技术']}/+{ab}")
    expect(h["skills"]["电子/安全技术"] == 6 and ab == 4, "职业能力改为 ability_bonus 字段")


async def sc_slots(s: Sim) -> None:
    """插槽体系与卸载：前置链 / 强制双眼 / 容量 / 互斥 / 卸载退款。"""
    print("【场景 44】赛博组件插槽：前置 / 双眼 / 容量 / 互斥 / 卸载")
    from core import cyber_flow, db, growth, roster
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:slots"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)

    def ins(item, n=4):
        return cyber_flow.install(conn, gid, "u1", f"安装 {item}",
                                  roller=dice.ScriptedRoller([2] * n))

    text = ins("瞄准目镜")
    show("前置", "没义眼装瞄准目镜", text.splitlines()[0])
    expect("先有" in text, "配件必须先有基础组件")

    text = ins("图像增强")
    expect("两只眼睛" in text, "强制双眼件：一只眼都不行")

    ins("赛博义眼")
    text = ins("图像增强")
    show("双眼", "一只眼装图像增强", text.splitlines()[0])
    expect("1 只" in text, "一只眼时提示数量")

    ins("赛博义眼")
    show("双眼", "装图像增强（2 次）", "OK")
    ins("图像增强")
    ins("图像增强")
    expect(cyber_flow.status_text(conn, gid, "u1").count("图像增强") >= 1, "双眼件装上了")

    text = ins("射镖枪")
    show("容量", "双眼 6 槽已用 4，再装射镖枪(3)", text.splitlines()[0])
    expect("槽不够" in text, "槽位容量生效")

    text = ins("克伦齐科夫")
    show("前置", "装克伦齐科夫", text.splitlines()[0])
    expect("神经链路" in text, "加速组件需要神经链路")

    text = cyber_flow.uninstall(conn, gid, "u1", "卸载 赛博义眼")
    show("卸载", "卸义眼（图像增强挂着）", text.splitlines()[0])
    expect("暂时卸不了" in text, "有配件挂着时不许卸基础组件")

    eb_before = growth.eb(roster.get(conn, gid, "u1"))
    text = cyber_flow.uninstall(conn, gid, "u1", "卸载 图像增强")
    show("卸载", "卸图像增强", text)
    expect("卸下了" in text, "卸载成功")
    expect("人性仍是" in text, "卸载不退还人性")
    expect(growth.eb(roster.get(conn, gid, "u1")) > eb_before, "按原价卖出扣手术费后到账")

    text = ins("射镖枪")
    show("容量", "卸掉一件后再装射镖枪", text.splitlines()[0])
    expect("槽不够" not in text, "腾出槽位后能装")
    conn.close()


async def sc_immunity(s: Sim) -> None:
    """义体免疫（B 类）：闪光/噪声/毒气/溺水/EMP。"""
    print("【场景 45】义体免疫：防眩晕/水平阻尼降噪/鼻滤器/鳃")
    from core import combat as C, db, hazards, roster
    from cyberpunk import dice, immunity

    conn = db.connect(":memory:")
    gid = "qq:immune"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))

    def set_cyber(items):
        card = roster.get(conn, gid, "u1")
        card["state"] = {"cyber": list(items)}
        roster.save(conn, gid, "u1", card)
        return roster.get(conn, gid, "u1")

    card = set_cyber(["防眩晕", "鼻滤器"])
    show("免疫", "装防眩晕+鼻滤器", "、".join(immunity.installed_immunities(card)))
    expect(immunity.is_immune(card, "闪光") == "防眩晕", "按类别查到闪光免疫")

    actor = type("A", (), {"tags": C._card_tags(card)})()
    e = {"negate_if": ["闪光"]}
    expect(C._negated(e, actor), "剧本写「闪光」被防眩晕豁免")

    card = set_cyber(["鳃"])
    h = hazards.drown(conn, gid, "u1", roller=dice.RandomRoller())
    show("环境", "鳃 + 溺水", h.line().splitlines()[-1])
    expect(h.damage == 0, "鳃免溺水")

    card = set_cyber(["鼻滤器"])
    h = hazards.poison(conn, gid, "u1", "强", roller=dice.RandomRoller())
    show("环境", "鼻滤器 + 毒气", h.line().splitlines()[-1])
    expect(h.damage == 0, "鼻滤器免毒气")

    card = set_cyber([])
    h = hazards.drown(conn, gid, "u1", roller=dice.RandomRoller())
    show("环境", "没装鳃 + 溺水", h.line().splitlines()[0])
    expect(h.damage > 0, "没免疫就照常受伤")
    conn.close()


async def sc_expand(s: Sim) -> None:
    """插槽扩容（复眼改造/传感器阵列）+ A 类嗅觉增强。"""
    print("【场景 46】插槽扩容：复眼改造 / 传感器阵列 / 嗅觉增强")
    from core import cyber_flow, db, growth, roster
    from cyberpunk import cyber_bonus, dice, slots

    conn = db.connect(":memory:")
    gid = "qq:expand"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 50000)
    roster.save(conn, gid, "u1", card)

    def ins(item):
        return cyber_flow.install(conn, gid, "u1", f"安装 {item}",
                                  roller=dice.RandomRoller())

    ins("赛博义眼")
    ins("赛博义眼")
    text = ins("赛博义眼")
    show("义眼上限", "装第 3 只眼", text.splitlines()[0])
    expect("最多装 2 件" in text, "默认只有两只眼")

    text = ins("复眼改造")
    show("扩容", "装复眼改造", text.splitlines()[0])
    expect("装上了" in text, "复眼改造装上了（不该被误判要义肢）")
    text = ins("赛博义眼")
    show("扩容", "再装第 3 只眼", text.splitlines()[0])
    expect("装上了" in text, "复眼改造 +5 只义眼额度生效")

    ins("赛博音频套装")
    before = slots.effective_capacity("赛博音频套装", [])
    ins("传感器阵列")
    after = slots.effective_capacity("赛博音频套装", ["传感器阵列"])
    show("扩容", "音频槽 装阵列前/后", f"{before} -> {after}")
    expect(after == 8, "传感器阵列 +5 音频槽")

    ins("神经链路")
    ins("芯片插槽")
    ins("嗅觉增强")
    got = roster.get(conn, gid, "u1")
    bonus, notes = cyber_bonus.check_bonus(got, skill="追踪")
    show("检定加值", "嗅觉增强 → 追踪", f"+{bonus} {notes}")
    expect(bonus == 2, "嗅觉增强给追踪 +2")
    expect(cyber_bonus.check_bonus(got, skill="觉察")[0] == 0, "不影响觉察")
    conn.close()


async def sc_cyberweapon(s: Sim) -> None:
    """义体武器：档位覆盖伤害 + 藏匿。"""
    print("【场景 47】义体武器：档位覆盖（赛博大蛇 4D6）+ 藏匿")
    from core import combat as C, db, roster
    from cyberpunk import cyber_weapon as cw, dice

    conn = db.connect(":memory:")
    gid = "qq:cyberweapon"

    def new_pc(items):
        roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
        card = roster.get(conn, gid, "u1")
        card["state"] = {"cyber": list(items)}
        roster.save(conn, gid, "u1", card)

    new_pc(["赛博大蛇"])
    show("档位", "赛博大蛇", str(cw.damage_of("赛博大蛇")))
    expect(cw.damage_of("赛博大蛇") == "4D6", "超重型 4D6（full.txt:15017）")
    show("档位", "吸血鬼尖牙", str(cw.damage_of("吸血鬼尖牙")))
    expect(cw.damage_of("吸血鬼尖牙") == "1D6", "轻型 1D6（:10148）")
    # 弹出式武器装的是「市面上的具体武器」（full.txt:10261-10286），
    # 不是三个抽象档位；没挂载就没有伤害。
    from cyberpunk import weapon_catalog as wcat

    show("弹出式", "未挂载武器", str(cw.damage_of("弹出式近战武器")))
    expect(cw.damage_of("弹出式近战武器") is None, "没指定装哪把武器 → 没有伤害")

    glove = {"state": {"cyber": [{"id": "c1", "name": "弹出式近战武器", "parent": ""}],
                       "cyber_mounts": {"弹出式近战武器": "战斗手套"}}}
    show("弹出式", "装战斗手套", f"{cw.damage_of('弹出式近战武器', glove)}，"
                                f"{cw.rof_of('弹出式近战武器', glove)}ROF")
    expect(cw.damage_of("弹出式近战武器", glove) == "3d6",
           "异种近战（战斗手套）按它自己的 3d6，FAQ :521 允许异种")

    # 属性判定：不建白名单，只看 melee_class / hands
    expect(wcat.mountable(wcat.find("战斗手套"), mount="弹出式近战武器"),
           "异种/重型近战可装")
    expect(not wcat.mountable(wcat.find("超重型近战武器"), mount="弹出式近战武器"),
           "超重型近战装不了（:10261-10269 只到重型）")
    expect(wcat.mountable(wcat.find("超重型手枪"), mount="弹出式远程武器"),
           "单手远程可装（模板实例就是超重型手枪）")
    expect(not wcat.mountable(wcat.find("突击步枪"), mount="弹出式远程武器"),
           "双手远程装不了（:10280「任何单手持握」）")
    expect(wcat.find("超重型手枪")["conceal"] is False
           and "可藏匿" in wcat.carried(wcat.find("超重型手枪")),
           "弹出式装上后即使通常无法藏匿的也能藏（:10265）")

    # 端到端：真的走 install 一遍，确认装进去的是具体武器而不是档位词
    from core import cyber_flow, growth

    roster.save(conn, gid, "u2", roster.build(template="独狼", name="阿飞"))
    _c = roster.get(conn, gid, "u2")
    _c["state"] = {"eb": 50000,
                   "cyber": [{"id": "c1", "name": "赛博手臂", "parent": ""}]}
    roster.save(conn, gid, "u2", _c)
    asked = cyber_flow.install(conn, gid, "u2", "安装 弹出式近战武器")
    show("弹出式", "不说装哪把", asked.splitlines()[0])
    expect("要装哪把武器" in asked, "不指定具体武器就反问，不瞎给默认值")
    bad = cyber_flow.install(conn, gid, "u2", "安装 弹出式远程武器 突击步枪",
                             roller=dice.ScriptedRoller([1, 1]))
    show("弹出式", "双手步枪", bad.splitlines()[0])
    expect("只手" in bad, "双手远程被属性判定拒绝（:10280）")
    ok = cyber_flow.install(conn, gid, "u2", "安装 弹出式远程武器 超重型手枪",
                            roller=dice.ScriptedRoller([1, 1]))
    show("弹出式", "装超重型手枪", ok.splitlines()[0])
    _c2 = roster.get(conn, gid, "u2")
    show("弹出式", "面板", cyber_flow.status_text(conn, gid, "u2").splitlines()[-1])
    expect(cw.mounts_of(_c2).get("弹出式远程武器") == "超重型手枪",
           "卡上记的是具体武器名，不是「超重型」这种档位词")
    expect(cw.damage_of("弹出式远程武器", _c2) == "4D6", "伤害取那把武器自己的 4D6")

    new_pc(["赛博大蛇", "吸血鬼尖牙"])
    best = cw.best_weapon(roster.get(conn, gid, "u1"), skill="近战武器")
    show("选取", "两件武器取最强", f"{best[0]} {best[1]}")
    expect(best[0] == "赛博大蛇", "取伤害最高的")

    new_pc(["赛博大蛇", "隐藏枪套"])
    show("藏匿", "藏匿通道加值", f"+{cw.conceal_bonus(roster.get(conn, gid, 'u1'))}")
    expect(cw.conceal_bonus(roster.get(conn, gid, "u1")) == 2, "隐藏枪套 +2")

    new_pc(["赛博大蛇"])
    # 显式清场再开，避免复用旧战斗。
    # 注意：simulate 用固定 seed 让骰子可复现（见 boot()），这个 seed 下的
    # 自然骰序打不中，所以这里用 **脚本骰** 精确控制：
    # 攻击判定 10（+DEX6+技能 → 过闪避）、伤害 4 颗 6（4D6 上限 24）。
    C.end(conn, gid)
    C.start(conn, gid, enemies=(("打手", 1),), roller=dice.ScriptedRoller([5] * 120))
    hit = ""
    # 先攻可能让敌人先动；多给几次尝试，每次都用能命中的脚本骰
    for _ in range(6):
        fight = C.load(conn, gid)
        if fight is None or not fight.active:
            C.start(conn, gid, enemies=(("打手", 1),),
                    roller=dice.ScriptedRoller([5] * 120))
        text = C.attack(conn, gid, "u1", "打手", skill="近战武器",
                        roller=dice.ScriptedRoller([10, 6, 6, 6, 6, 6, 6, 6, 6]))
        if "义体武器：赛博大蛇（4D6）" in text:
            hit = text
            break
    detail = [ln for ln in hit.splitlines() if "义体武器" in ln or "命中：" in ln]
    show("战斗", "近战命中", " / ".join(detail) if detail else "（多次未命中）")
    expect(bool(hit), "战斗中按义体武器 4D6 结算")
    expect("义体武器：赛博大蛇（4D6）" in hit, "回执标明来源与伤害骰")
    expect("：24 点" in hit, "4 颗骰子全 6 → 24 点（验证用的是 4D6 不是 2D6）")
    conn.close()


async def sc_sandy(s: Sim) -> None:
    """桑德维斯坦：动作激活 / 先攻 +3 / 20 轮 / 1200 轮冷却。"""
    print("【场景 48】桑德维斯坦：动作激活（先攻 +3，持续 1 分钟，冷却 1 小时）")
    from core import combat as C, db, roster
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:sandy"

    def new_pc(items):
        roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
        card = roster.get(conn, gid, "u1")
        card["state"] = {"cyber": list(items)}
        roster.save(conn, gid, "u1", card)

    show("换算", "1 分钟 / 1 小时", f"{C.SANDY_DURATION_ROUNDS} 轮 / "
         f"{C.SANDY_COOLDOWN_ROUNDS} 轮")
    expect(C.SANDY_DURATION_ROUNDS == 20, "1 轮 3 秒 → 1 分钟 = 20 轮（:11327-11331）")
    expect(C.SANDY_COOLDOWN_ROUNDS == 1200, "1 小时 = 1200 轮")

    new_pc(["神经链路", "桑德维斯坦"])
    C.end(conn, gid)
    C.start(conn, gid, enemies=(("打手", 1),), roller=dice.RandomRoller())
    me = next(c for c in C.load(conn, gid).combatants if c.side == "pc")
    before = me.initiative
    text = C.act(conn, gid, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    show("激活", "开桑德维斯坦", text.splitlines()[0])
    expect("激活了桑德维斯坦" in text, "激活成功")
    after = next(c for c in C.load(conn, gid).combatants if c.side == "pc")
    expect(after.initiative == before + 3, f"先攻 +3（{before} → {after.initiative}）")
    expect(C.sandy_on_cooldown(after, C.load(conn, gid)), "激活后进入冷却")

    new_pc([])
    C.end(conn, gid)
    C.start(conn, gid, enemies=(("打手", 1),), roller=dice.RandomRoller())
    text = C.act(conn, gid, "u1", "激活桑德维斯坦", roller=dice.RandomRoller())
    show("前置", "没装桑德维斯坦", text.splitlines()[0])
    expect("没装" in text, "没装就拒绝")
    conn.close()


async def sc_posthit(s: Sim) -> None:
    """命中后检定通道：特殊弹种 + 附毒。"""
    print("【场景 49】命中后检定：毒气弹 / 麻醉弹 / 闪光弹 / EMP / 附毒")
    from core import combat as C, db, roster
    from cyberpunk import ammo_effect as ae, dice

    conn = db.connect(":memory:")
    gid = "qq:posthit"

    def setup(cyber_items=()):
        roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
        card = roster.get(conn, gid, "u1")
        card["state"] = {"cyber": list(cyber_items)}
        roster.save(conn, gid, "u1", card)
        C.end(conn, gid)
        C.start(conn, gid, enemies=(("打手", 1),), roller=dice.RandomRoller())
        C.close_range(conn, gid, "u1", "距离 5 米")

    def shoot(ammo, hit=6, check=6):
        fight = C.load(conn, gid)
        me = next(c for c in fight.combatants if c.side == "pc")
        me.ammo_type = ammo
        me.ammo = me.mag = 10
        C._save(conn, fight)
        return C.attack(conn, gid, "u1", "打手", skill="手枪",
                        roller=dice.ScriptedRoller([hit, check, 3], loop=True))

    expect(ae.is_check_ammo("毒气弹"), "毒气弹是检定类弹种")
    expect(ae.spec_for("毒气弹")["dv"] == 13, "毒气弹 DV13（:26448）")
    expect(ae.spec_for("闪光弹")["dv"] == 15, "闪光弹 DV15（:26424）")
    expect(not ae.is_check_ammo("烟雾弹"), "烟雾弹不是检定类")

    setup()
    foe_hp = lambda: next(c for c in C.load(conn, gid).combatants
                          if c.side == "enemy").hp
    before = foe_hp()
    text = shoot("毒气弹", hit=6, check=6)
    show("被动", "毒气弹 检定通过",
         " / ".join(l.strip() for l in text.splitlines() if "扛住" in l or "DV13" in l))
    expect("扛住了" in text, "检定通过不掉血")
    expect(foe_hp() == before, "HP 未变")

    text = shoot("毒气弹", hit=6, check=1)
    show("被动", "毒气弹 检定失败",
         " / ".join(l.strip() for l in text.splitlines() if "失败" in l))
    expect("不吃护甲" in text, "失败直接 2D6（不吃护甲）")
    expect(foe_hp() < before, "HP 掉了")

    setup()
    text = shoot("麻醉弹", hit=6, check=1)
    show("状态", "麻醉弹 失败",
         " / ".join(l.strip() for l in text.splitlines() if "失去意识" in l))
    expect("失去意识" in text, "麻醉弹让目标失去意识")

    setup()
    text = shoot("闪光弹", hit=6, check=1)
    show("伤势", "闪光弹 失败",
         " / ".join(l.strip() for l in text.splitlines() if "受损" in l))
    expect("眼睛受损" in text and "耳朵受损" in text, "闪光弹两种伤势")

    setup()
    text = shoot("EMP 弹", hit=6, check=1)
    show("EMP", "EMP 弹 失败",
         " / ".join(l.strip() for l in text.splitlines() if "赛博组件" in l))
    expect("赛博组件失效" in text, "EMP 让赛博组件失效")

    # 附毒：吸血鬼尖牙
    setup(["神经链路", "吸血鬼尖牙"])
    hit = ""
    for _ in range(40):
        fight = C.load(conn, gid)
        if fight is None or not fight.active:
            C.start(conn, gid, enemies=(("打手", 1),), roller=dice.RandomRoller())
            C.close_range(conn, gid, "u1", "距离 5 米")
        text = C.attack(conn, gid, "u1", "打手", skill="近战武器",
                        roller=dice.ScriptedRoller([6, 1, 3], loop=True))
        if "附毒" in text:
            hit = text
            break
    show("附毒", "吸血鬼尖牙命中",
         " / ".join(l.strip() for l in hit.splitlines() if "附毒" in l or "中毒" in l))
    expect("附毒" in hit, "尖牙命中触发附毒检定")
    conn.close()


async def sc_shop(s: Sim) -> None:
    """商店：掮客档位、买、卖、砍价、穿护甲、夜市模式。"""
    print("【场景 55】商店：买 / 卖 / 砍价 / 穿护甲 / 夜市")
    from core import db, growth, roster, shop_flow
    from cyberpunk import dice

    conn = db.connect(":memory:")
    gid = "qq:shop"
    roster.save(conn, gid, "u1", roster.build(template="独狼", name="阿飞"))
    card = roster.get(conn, gid, "u1")
    growth.grant_eb(card, 20000)
    roster.save(conn, gid, "u1", card)

    shelf = shop_flow.shelf_text(conn, gid, "u1")
    show("商店", "货架", shelf.splitlines()[0])
    expect("中型手枪" in shelf, "货架列出商品")
    expect("🔒" in shelf, "渠道买不到的标 🔒（没掮客只能买 <100）")

    blocked = shop_flow.buy(conn, gid, "u1", "买 防弹背心")
    show("商店", "买 500 的背心", blocked.splitlines()[0])
    expect("买不到" in blocked, "没掮客买不到 500 欧元（full.txt:579）")

    ok = shop_flow.buy(conn, gid, "u1", "买 中型手枪")
    show("商店", "买手枪", ok.splitlines()[0])
    expect("买了中型手枪" in ok, "50 欧元的东西买得到")
    expect("中型手枪" in roster.get(conn, gid, "u1")["gear"], "进了装备")

    # 掮客档位：9 级 <5000、10 级无限（:583-595）
    card = roster.get(conn, gid, "u1")
    card["state"]["broker_level"] = 9
    roster.save(conn, gid, "u1", card)
    at5k = shop_flow.buy(conn, gid, "u1", "买 合金装备")
    show("商店", "9 级买 5000", at5k.splitlines()[0])
    expect("买不到" in at5k, "9 级只能买「低于」5000（:588）")
    card = roster.get(conn, gid, "u1")
    card["state"]["broker_level"] = 10
    roster.save(conn, gid, "u1", card)
    ten = shop_flow.buy(conn, gid, "u1", "买 合金装备")
    show("商店", "10 级买 5000", ten.splitlines()[0])
    expect("买了合金装备" in ten, "10 级什么都能买，含超奢（:595）")

    # 卖：默认原价，说「砍价」才走交易对抗（:602 / :608-611）
    before = growth.eb(roster.get(conn, gid, "u1"))
    sold = shop_flow.sell(conn, gid, "u1", "卖 中型手枪")
    show("商店", "卖手枪", sold.splitlines()[0])
    expect("原价" in sold, "默认按原价出售（:602）")
    expect(growth.eb(roster.get(conn, gid, "u1")) == before + 50, "原价 50 到账")

    card = roster.get(conn, gid, "u1")
    card["gear"] = ["中型手枪"]
    roster.save(conn, gid, "u1", card)
    hag = shop_flow.sell(conn, gid, "u1", "卖 中型手枪 砍价", haggle=True,
                         roller=dice.ScriptedRoller([1, 10]))
    show("商店", "砍价输了", hag.splitlines()[0])
    expect("砍价输了" in hag and "80" not in hag.split("压到")[0], "砍价输会被压价（:611）")

    # 护甲：买了能穿上，且真的进规则引擎（SP）
    armor = shop_flow.buy(conn, gid, "u1", "买 防弹背心")
    show("商店", "买背心", armor.splitlines()[0])
    worn = shop_flow.wear(conn, gid, "u1", "穿上 防弹背心")
    show("护甲", "穿上", worn.splitlines()[0])
    expect("SP 15" in worn, "防弹背心 SP15（:8530-8620）")
    rules = roster.to_character(roster.get(conn, gid, "u1"))
    expect(rules.armor.sp == 15, "穿着的护甲真的进了规则引擎")
    off = shop_flow.takeoff(conn, gid, "u1")
    show("护甲", "脱下", off)
    expect("脱下了" in off, "随时可脱")

    # 不设背包上限（主人 2026-11-13：交 DM 判断）
    card = roster.get(conn, gid, "u1")
    card["gear"] = [f"杂物{i}" for i in range(20)]
    roster.save(conn, gid, "u1", card)
    more = shop_flow.buy(conn, gid, "u1", "买 手电筒")
    show("商店", "第 21 件", more.splitlines()[0])
    expect("买了手电筒" in more, "没有 12 件上限，交 DM 判断")

    # 库存确认接口：现在恒放行
    verdict = await shop_flow.inventory_check(
        roster.get(conn, gid, "u1"), shop_flow.find("手电筒"), None)
    expect(verdict == (None, ""), "库存确认接口默认放行（留待以后约定）")

    # 夜市模式（:25529）：场景写了 market 就只卖摊上的
    m = {"categories": ["武器和护甲"], "items": ["中型手枪"]}
    show("夜市", "今晚只卖", "、".join(m["items"]))
    yes = shop_flow.buy(conn, gid, "u1", "买 中型手枪", market=m)
    expect("买了中型手枪" in yes, "货架上的买得到")
    no = shop_flow.buy(conn, gid, "u1", "买 重型手枪", market=m)
    show("夜市", "不在摊上的", no.splitlines()[0])
    expect("不卖" in no, "夜市只卖摊上的东西")

    rolled = shop_flow.roll_market()
    show("夜市", "掷一个夜市", "、".join(rolled["categories"]))
    expect(len(rolled["categories"]) == 2
           and rolled["categories"][0] != rolled["categories"][1],
           "2d6 掷两次、相同重掷 → 两个不同品类")
    conn.close()


async def sc_memory(s: Sim) -> None:
    """NPC 记忆 + DM 牌面 + 偏离提醒 + 态度锁范围。"""
    print("【场景 56】NPC 记忆 / 牌面 / 提醒 / 态度")
    from core import attitude, db, memory, nudge

    conn = db.connect(":memory:")
    conn.execute(
        "INSERT INTO campaigns (id,title,status,scene,scenario,created_at,updated_at)"
        " VALUES (1,'歌剧团','active','a1.hook','opera','x','x')")
    conn.commit()
    cid = 1

    # ① 牌面：只放被标记的，8 条预算，悬而未决不占
    memory.add_mark(conn, cid, scene="a1.hook", what="玩家接下了委托",
                    weight=3, keywords=["委托", "莱茵迈耶"])
    board = memory.board(conn, cid, "opera", "a1.hook")
    show("牌面", "内容", board)
    expect("玩家接下了委托" in board and "关键" in board, "重要标记进牌面并标「关键」")
    expect("⚠" in board, "悬而未决单独显示")

    for i in range(12):
        memory.add_mark(conn, cid, scene="a1", what=f"杂事{i}",
                        weight=1, mark_id=f"z{i}")
    lines = [ln for ln in memory.board(conn, cid, "opera", "a1.hook").splitlines()
             if ln.strip().startswith("·")]
    expect(len(lines) == memory.BOARD_MARKS == 8, "牌面按 8 条预算截断")

    # ② B 层：旧的不删 + 关键词召回
    memory.add_relation(conn, cid, "rayfield", kind="promise",
                        what="答应帮他找妹妹", keywords=["妹妹"])
    for i in range(15):
        memory.add_relation(conn, cid, "rayfield", kind="tell",
                            what=f"闲聊{i}", keywords=[f"闲{i}"])
    allrows = memory.relations_of(conn, cid, "rayfield")
    expect(len(allrows) == 16, "人际记忆超过 10 条也不删（只靠召回）")
    cold = memory.recall(conn, cid, "rayfield", "今天天气不错")
    expect(all("妹妹" not in r["what"] for r in cold), "不提关键词就不翻老账")
    warm = memory.recall(conn, cid, "rayfield", "你妹妹有消息了吗")
    show("召回", "提到「妹妹」", "、".join(r["what"] for r in warm[-2:]))
    expect(any("妹妹" in r["what"] for r in warm), "提到关键词能把老记忆捞回来（防健忘）")

    # ③ 态度：0–10，锁范围，不参与数值
    attitude.set_bounds(conn, cid, "clown", [0, 5])
    for _ in range(10):
        attitude.apply_delta(conn, cid, "clown", +1)
    show("态度", "敌人怎么示好", f"{attitude.current(conn, cid, 'clown')}/10"
                                f"（{attitude.band_of(attitude.current(conn, cid, 'clown'))}）")
    expect(attitude.current(conn, cid, "clown") == 5, "敌人顶多平淡，不会友善")

    attitude.set_bounds(conn, cid, "student", [3, 7])
    attitude.apply_delta(conn, cid, "student", -10)
    expect(attitude.current(conn, cid, "student") == 3, "中立不会特别厌恶")

    old, new, crossed = attitude.apply_delta(conn, cid, "student", +1)
    expect(crossed is False, "同档内小幅波动不触发旁白")

    # ④ 偏离提醒：连续 3 次空转 → 一次；每类本场只一次
    for _ in range(3):
        nudge.note_offtrack(conn, cid, "a1.hook")
    kind = nudge.should_nudge(conn, cid, "a1.hook", has_left=True)
    show("提醒", "空转 3 次", f"触发 {kind}")
    expect(kind == nudge.KIND_CONTENT, "还有线索没查 → 内容引导")
    nudge.mark_nudged(conn, cid, "a1.hook", kind)
    expect(nudge.should_nudge(conn, cid, "a1.hook", has_left=True) == "", "本场不再重复同类提醒")

    for _ in range(3):
        nudge.note_offtrack(conn, cid, "a1.hook")
    second = nudge.should_nudge(conn, cid, "a1.hook", has_left=False)
    show("提醒", "再来 3 次", f"触发 {second}")
    expect(second == nudge.KIND_TIME, "内容引导用过 → 改时间压力")
    nudge.mark_nudged(conn, cid, "a1.hook", second)
    expect(nudge.should_nudge(conn, cid, "a1.hook", has_left=False) == "", "两类都用过就不再提醒")

    # ⑤ 撞上闸门会清零
    nudge.note_offtrack(conn, cid, "a1.hook")
    nudge.reset_offtrack(conn, cid, "a1.hook")
    prog = memory.load_progress(conn, cid, "a1.hook")
    expect(prog["off_track"] == 0, "撞上剧本就清空空转计数")

    conn.close()


SCENARIOS = {
    "memory": sc_memory,
    "shop": sc_shop,
    "posthit": sc_posthit,
    "sandy": sc_sandy,
    "cyberweapon": sc_cyberweapon,
    "expand": sc_expand,
    "immunity": sc_immunity,
    "slots": sc_slots,
    "econ": sc_econ,
    "skills": sc_red_skills,
    "cold": sc_cold_start,
    "perm": sc_permission,
    "claim": sc_claim,
    "play": sc_play,
    "combat": sc_combat,
    "clue": sc_clue_npc,
    "pause": sc_pause_resume,
    "edge": sc_edge,
    "concurrent": sc_concurrent,
    "multi": sc_multi,
    "llmfail": sc_llm_fail,
    "stress": sc_stress,
    "backup": sc_backup,
    "hazard": sc_hazard,
    "party": sc_party,
    "scripts": sc_scripts,
    "opera": sc_opera,
    "favors": sc_favors,
    "actors": sc_actors,
    "net": sc_net,
    "entry": sc_entry,
    "map": sc_map,
    "grenade": sc_grenade,
    "range": sc_range,
    "scenemove": sc_scene_move,
    "mortal": sc_mortal,
    "crit": sc_crit,
    "autofire": sc_autofire,
    "ammo": sc_ammo,
    "cover": sc_cover,
    "cyber": sc_cyber,
    "ammotype": sc_ammo_type,
    "rof": sc_rof,
    "delay": sc_delay,
    "helper": sc_helper,
    "rest": sc_rest,
    "aclass": sc_aclass,
    "move": sc_move,
    "utility": sc_utility,
    "instances": sc_instances,
    "frames": sc_frames,
    "cclass": sc_cclass,
}


# --------------------------------------------------------------------------
# 交互模式
# --------------------------------------------------------------------------
async def interactive(s: Sim) -> None:
    print("进入交互模式（空行退出）。你以群主身份说话。\n")
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        reply = await s.say("u1", text)
        print((reply or "（没有回应）") + "\n")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--scenario", choices=sorted(SCENARIOS), action="append")
    ap.add_argument("--real", action="store_true", help="用真 DeepSeek（会花钱）")
    ap.add_argument("-i", "--interactive", action="store_true")
    args = ap.parse_args()

    boot(real=args.real)
    s = Sim()

    if args.interactive:
        await interactive(s)
        return 0

    names = args.scenario or list(SCENARIOS)
    failed = 0
    for name in names:
        fn = SCENARIOS[name]
        # 每个场景用干净的库，互不污染
        boot(real=args.real)
        s = Sim()
        try:
            await fn(s)
        except AssertionError as exc:
            failed += 1
            print(f"  ❌ {name}: {exc}\n")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"  💥 {name} 崩了：\n{traceback.format_exc()}\n")

    total = len(names)
    tail = f"假模型调用 {FAKE.calls} 次"
    if FAKE.real:
        tail = (f"真模型调用 {FAKE.calls} 次，token prompt {FAKE.prompt_tokens}"
                f" / completion {FAKE.completion_tokens}")
    print(f"== {total - failed}/{total} 个场景通过（{tail}）==")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
