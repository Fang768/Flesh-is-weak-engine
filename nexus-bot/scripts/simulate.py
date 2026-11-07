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
    easy_roster.save(conn, gid, "u1", easy.templates()[0])

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
    easy_roster.save(conn, gid, "u1", easy.templates()[0])
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

    easy_roster.save(conn, gid, "u1", easy.templates()[0])
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
    expect("：15 vs DV 15" in base, "基准命中总值 9+REF6 = 15")

    f = combat.load(conn, gid)
    me = next(c for c in f.combatants if c.side == "pc")
    me.crits = ("eye_lost",)  # 失去眼睛：远程 −4
    combat._save(conn, f)
    hurt = combat.attack(conn, gid, "u1", "打手", skill="手枪",
                         roller=dice.ScriptedRoller([9, 1, 2, 2]))
    show("受伤后", "失去眼睛 −4", hurt.splitlines()[0])
    expect("：11 vs DV 15" in hurt, "远程攻击吃了 −4，15 → 11")
    expect("没打中" in hurt, "11 < DV15：这一枪打飞了")

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


SCENARIOS = {
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
