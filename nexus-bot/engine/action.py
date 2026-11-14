"""复杂动作（Action）：由**剧本数据**定义的原子动作组合。

一个 Action 就是一串 steps，每步调用一个原子动作：

    {"id": "buy_ticket_fixed", "label": "一口价买票", "steps": [
        {"op": "charge", "eb": 50},
        {"op": "grant",  "item": "演出票"}]}

关键设计：

1. **购买和进场分开**。买票只改「我有票了」，进场是另一个 Action
   （带 when: {item: 演出票}，并在进场时 take 掉）。这样
   「先付钱、待会进场」是数据表达，不需要 DM 临场拼。
2. **整个 Action 在一个事务里**。任何一步失败 -> 全部回滚，
   不会出现「钱扣了但没换成场」。
3. when 只有四种取值：always（默认）/ success / fail / first。
   它决定「这一步要不要执行」，不是跳转。
4. 旧剧本（exits/gates/clues/encounter）在 story.py 里自动编译成 Action，
   所以 opera.json 一行不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .conditions import Condition, Context, parse_when
from .ops import OpError, Ops, RollResult, StepResult
from .save import Save
from .story import Story


class ActionError(ValueError):
    """Action 定义有问题（编译期或运行时）。"""


#: step 的 when 只认这四种
WHEN_ALWAYS = "always"
WHEN_SUCCESS = "success"
WHEN_FAIL = "fail"
WHEN_FIRST = "first"          # 整队只有第一个人做（例如「掏钱」）
#: 战斗后推进：读存档里的 battle_result（won/lost/withdraw）
WHEN_BATTLE_WON = "battle_won"
WHEN_BATTLE_LOST = "battle_lost"
WHENS = (WHEN_ALWAYS, WHEN_SUCCESS, WHEN_FAIL, WHEN_FIRST,
         WHEN_BATTLE_WON, WHEN_BATTLE_LOST)


@dataclass(frozen=True)
class Step:
    """一个原子动作调用。"""

    op: str
    args: dict = field(default_factory=dict)
    when: str = WHEN_ALWAYS

    def describe(self) -> str:
        bits = "、".join(str(k) + "=" + str(v) for k, v in self.args.items())
        text = self.op + "(" + bits + ")"
        return text + (" [" + self.when + "]" if self.when != WHEN_ALWAYS
                       else "")


@dataclass(frozen=True)
class Action:
    """一个复杂动作：有序的原子步骤 + 触发条件。"""

    id: str
    label: str = ""
    steps: tuple = ()
    #: 整个动作能不能做（条件不满足 -> catalog 里不出现）
    when: Condition | None = None
    #: 给 DM 参考的触发词，引擎**不用**它做匹配
    trigger: tuple = ()
    #: 来源：exit / gate / clue / encounter / script
    kind: str = "script"

    def describe(self) -> str:
        head = self.label or self.id
        body = " -> ".join(s.describe() for s in self.steps)
        return head + ": " + body


@dataclass
class Report:
    """一次 perform 的回执：给 DM 叙述用，不含任何提示词。"""

    action: str
    ok: bool
    steps: list = field(default_factory=list)
    reason: str = ""
    rolled: list = field(default_factory=list)
    moved_to: str = ""
    texts: list = field(default_factory=list)

    def describe(self) -> str:
        if not self.ok:
            return "做不了「" + self.action + "」：" + self.reason
        bits = [s.detail for s in self.steps if s.detail]
        return "；".join(bits)


# -- 解析 --------------------------------------------------------------


def parse_step(spec: dict) -> Step:
    if not isinstance(spec, dict):
        raise ActionError("step 必须是对象：" + repr(spec))
    op = str(spec.get("op") or "").strip()
    if not op:
        raise ActionError("step 缺少 op：" + repr(spec))
    when = str(spec.get("when") or WHEN_ALWAYS).strip()
    if when not in WHENS:
        raise ActionError("when 只认 " + "/".join(WHENS) + "：" + when)
    args = {k: v for k, v in spec.items() if k not in ("op", "when")}
    return Step(op=op, args=args, when=when)


def parse_action(spec: dict) -> Action:
    if not isinstance(spec, dict):
        raise ActionError("Action 必须是对象：" + repr(spec))
    aid = str(spec.get("id") or "").strip()
    if not aid:
        raise ActionError("Action 缺少 id")
    steps = tuple(parse_step(s) for s in (spec.get("steps") or ()))
    if not steps:
        raise ActionError("Action " + aid + " 没有任何步骤")
    trigger = tuple(str(t) for t in (spec.get("trigger") or ()))
    when = parse_when(spec.get("when")) if spec.get("when") else None
    _check_order(aid, steps)
    return Action(id=aid, label=str(spec.get("label") or aid), steps=steps,
                  when=when, trigger=trigger,
                  kind=str(spec.get("kind") or "script"))


def _check_order(aid: str, steps: tuple) -> None:
    """编译期拦截：先换场再扣钱 = 玩家进去了却没付钱。"""
    moved = False
    for step in steps:
        if step.op == "move":
            moved = True
        elif moved and step.op in ("charge", "take"):
            raise ActionError(
                "Action " + aid + " 里 move 出现在 " + step.op
                + " 之前：玩家会先到位、再扣代价，失败时人会卡在新场景。")

# -- 执行器 ------------------------------------------------------------


class Executor:
    """按数据执行 Action。**不认识任何具体动作**。"""

    def __init__(self, story: Story, save: Save, *, roller=None,
                 actions=None) -> None:
        self.story = story
        self.save = save
        self.ops = Ops(story, save, roller=roller)
        self._extra = list(actions or ())
        self._ctx = None

    # -- 目录 ----------------------------------------------------------

    def catalog(self) -> list:
        """本场现在能做什么（条件不满足的不出现）。"""
        ctx = Context(world=self.save.world())
        out = []
        for action in self.actions():
            if action.when is not None and not action.when.matches(ctx):
                continue
            out.append(action)
        return out

    def actions(self) -> list:
        """本场全部 Action：剧本声明的 + 从旧格式编译出来的。"""
        current = self.save.scene()
        out = list(self._extra)
        out.extend(self.story.actions_of(current))
        return out

    def find(self, action_id: str):
        for action in self.actions():
            if action.id == action_id:
                return action
        return None

    # -- 执行 ----------------------------------------------------------

    def perform(self, action_id: str, *, owner_qq: str = "") -> Report:
        """跑一个复杂动作。事务内：任何一步失败全部回滚。"""
        action = self.find(str(action_id or "").strip())
        if action is None:
            return Report(action=str(action_id), ok=False,
                          reason="本场没有这个动作")
        cards = self.save.cards()
        if not cards:
            return Report(action=action.id, ok=False, reason="没有角色卡")
        actors = [c.owner_qq for c in cards]
        runner = str(owner_qq or "").strip()
        if runner and runner in actors:
            actors = [runner]

        report = Report(action=action.id, ok=True)
        last_roll = None
        first_done = False
        # 资格快照：在**开始执行前**拍一次世界状态。
        # 换场条件按这个快照判——否则「进场」这种
        # 「先交票、再换场」的动作会自己把条件弄失效。
        self._ctx = Context(world=self.save.world())
        try:
            with self.ops.atomically():
                for step in action.steps:
                    if not self._should_run(step, last_roll, first_done):
                        continue
                    targets = (actors[:1] if step.when == WHEN_FIRST
                               else actors)
                    for owner in targets:
                        result = self._run_step(step, owner, report)
                        if step.op == "roll" and result is not None:
                            last_roll = result
                    if step.when == WHEN_FIRST:
                        first_done = True
        except OpError as exc:
            return Report(action=action.id, ok=False, reason=str(exc))
        return report

    def _should_run(self, step: Step, last_roll, first_done: bool) -> bool:
        if step.when == WHEN_ALWAYS:
            return True
        if step.when == WHEN_FIRST:
            return not first_done
        if step.when == WHEN_SUCCESS:
            return bool(last_roll and last_roll.success)
        if step.when == WHEN_FAIL:
            return bool(last_roll and not last_roll.success)
        if step.when == WHEN_BATTLE_WON:
            return self.save.battle_result() == "won"
        if step.when == WHEN_BATTLE_LOST:
            return self.save.battle_result() == "lost"
        return False

    def _run_step(self, step: Step, owner: str, report: Report):
        op = step.op
        args = dict(step.args)
        if op == "roll":
            # 有 gate 的 Action 只有 roll，换场由 play() 按结果补
            card = self.save.card(owner)
            result = self.ops.roll(str(args.get("skill") or ""),
                                   int(args.get("dv") or 0), card=card)
            report.rolled.append(result)
            report.steps.append(StepResult(
                "roll", result.success, result.describe()))
            return result
        if op == "charge":
            # 剧本里写的是**代价**（正数 = 花掉/扣掉）；
            # 原子 charge 收的是**增量**（正 = 加），这里取反。
            result = self.ops.charge(owner, eb=-int(args.get("eb") or 0),
                                     hp=-int(args.get("hp") or 0),
                                     sp=-int(args.get("sp") or 0))
        elif op == "grant":
            # 剧本里的 grant 默认是**剧情道具**（票、木桩、人头）——
            # 用掉之后仍在身上，和商店买的 gear 区分开。
            result = self.ops.grant(owner, str(args.get("item") or ""),
                                    kind=str(args.get("kind") or "quest"),
                                    source=str(args.get("source") or "story"))
        elif op == "take":
            result = self.ops.take(owner, str(args.get("item") or ""))
        elif op == "move":
            result = self.ops.move(str(args.get("to") or ""),
                                   ctx=getattr(self, "_ctx", None))
            report.moved_to = str(result.data.get("to") or "")
        elif op == "mark":
            result = self.ops.mark(str(args.get("kind") or ""),
                                   str(args.get("id") or ""),
                                   topic=str(args.get("topic") or ""))
        elif op == "reveal":
            result = self.ops.reveal(str(args.get("ref") or ""))
            report.texts.append(str(result.data.get("text") or ""))
        elif op == "record":
            result = self.ops.record(str(args.get("kind") or "note"),
                                     str(args.get("text") or ""))
        elif op == "battle":
            result = self.ops.battle(str(args.get("action") or "enter"),
                                     result=str(args.get("result") or ""))
        else:
            raise OpError("不认识的步骤：" + op, code="bad_op")
        report.steps.append(result)
        return result

    # -- 临场动作：DM 直接调原子 --------------------------------------

    def ops_handle(self) -> Ops:
        """临场发挥用。**不走 Action**，DM 自己组合原子并叙述后果。

        和真人跑团一致：玩家灵机一动，DM 给一个检定 + 简单后果。
        """
        return self.ops


# -- 从旧剧本格式编译 Action ------------------------------------------


def build_actions(story, scene) -> list:
    """把一个场景的 exits / gates / clues / encounter 编译成 Action。

    这样旧剧本（opera.json / demo.json）**一行不用改**就获得了
    「可执行」能力；而新的复杂动作（一口价买票、翻衣柜）直接写在
    剧本的 actions 段里，和这些并排出现。
    """
    out: list = []
    out.extend(_from_exits(story, scene))
    out.extend(_from_gates(story, scene))
    out.extend(_from_clues(story, scene))
    out.extend(_from_encounter(story, scene))
    out.extend(_from_script(story, scene))
    return out


def _from_exits(story, scene) -> list:
    """出口 -> move。条件原样搬到 Action.when 上。"""
    out = []
    for edge in story.exits(scene.id):
        steps = [{"op": "move", "to": edge.to}]
        out.append(Action(
            id="go:" + edge.to,
            label=edge.label or ("前往 " + _scene_name(story, edge.to)),
            steps=tuple(parse_step(s) for s in steps),
            when=edge.when,
            kind="exit",
        ))
    return out


def _from_gates(story, scene) -> list:
    """闸门 -> roll + move(next)[success] + move(fail)[fail]。"""
    out = []
    for gate in getattr(scene, "_raw_gates", None) or []:
        if not isinstance(gate, dict):
            continue
        skills = [str(s) for s in (gate.get("skills") or []) if str(s)]
        if not skills:
            one = str(gate.get("skill") or "")
            if one:
                skills = [one]
        if not skills:
            continue
        dv = int(gate.get("dv") or 0)
        steps = [{"op": "roll", "skill": skills[0], "dv": dv}]
        nxt = str(gate.get("next") or "").strip()
        fail = str(gate.get("fail") or "").strip()
        if nxt and nxt != scene.id:
            steps.append({"op": "move", "to": nxt, "when": "success"})
        if fail and fail != scene.id:
            steps.append({"op": "move", "to": fail, "when": "fail"})
        out.append(Action(
            id="gate:" + str(gate.get("id") or skills[0]),
            label=str(gate.get("label") or "")
            or (skills[0] + " DV" + str(dv)),
            steps=tuple(parse_step(s) for s in steps),
            kind="gate",
        ))
    return out


def _from_clues(story, scene) -> list:
    """线索 -> roll + mark(clue)[success] + reveal(clue)[success]。"""
    out = []
    for clue in scene.clues:
        steps = []
        if clue.skill:
            steps.append({"op": "roll", "skill": clue.skill,
                          "dv": clue.dv or 10})
        steps.append({"op": "mark", "kind": "clue", "id": clue.id,
                      "when": "success" if clue.skill else "always"})
        steps.append({"op": "reveal", "ref": "clue:" + clue.id,
                      "when": "success" if clue.skill else "always"})
        out.append(Action(
            id="clue:" + clue.id,
            label="调查：" + (clue.skill or clue.id),
            steps=tuple(parse_step(s) for s in steps),
            kind="clue",
        ))
    return out


def _from_encounter(story, scene) -> list:
    """遭遇 -> battle(enter) + move(on_win)[battle:won]。

    战斗本身由战斗状态机跑；这里只声明「打完往哪走」，
    且换场是剧情层做的（符合「战斗不推进剧情」的分工）。
    """
    enc = scene.encounter
    if enc is None:
        return []
    steps = [{"op": "battle", "action": "enter"}]
    if enc.on_win:
        target = enc.on_win
        if target.startswith("ending:"):
            steps.append({"op": "reveal",
                          "ref": "ending:" + target.split(":", 1)[1]})
        else:
            steps.append({"op": "move", "to": target, "when": "battle_won"})
    return [Action(
        id="battle:" + scene.id,
        label="开战（" + (enc.note or scene.id) + "）",
        steps=tuple(parse_step(s) for s in steps),
        kind="encounter",
    )]


def _from_script(story, scene) -> list:
    """剧本里手写的复杂动作（「一口价买票」「翻衣柜」这类）。"""
    raw = getattr(scene, "_raw_actions", None) or []
    out = []
    for spec in raw:
        try:
            out.append(parse_action(spec))
        except ActionError:
            raise
    return out


def _scene_name(story, scene_id: str) -> str:
    scene = story.scene(scene_id)
    return scene.name if scene is not None else scene_id
