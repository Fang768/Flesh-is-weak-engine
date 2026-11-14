"""剧本编译：把 JSON 变成**带条件的场景图**，并在编译期校验。

为什么要有这一层（而不是继续用 core.story 的扁平化）：

* 旧 core.story 只做了 id -> dict 的索引，剧本写错（出口指向不存在的场景、
  死路、不可达）**不会报错**，玩家会在运行时静默卡死。
* 剧情状态机需要的是「从当前场景出发，满足什么条件就能去哪」，也就是**边**。
  旧代码没有边的概念，只有一堆临时拼的关键词匹配。

产物（全部是纯数据，不含数据库 / LLM）：

    Story
      .scenes   scene_id -> Scene
      .edges    from_id  -> list[Edge]
      .acts     [Act]（含 goal）
      .endings  key -> text
      .npcs     key -> dict

每条 Edge 都带一个 when 条件（engine.conditions），条件只读世界状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .conditions import (
    Always,
    Battle,
    Condition,
    ConditionError,
    Skill,
    parse_when,
)

STORY_DIR = Path(__file__).resolve().parent.parent / "story"


class StoryError(ValueError):
    """剧本结构不合法。编译期抛，不允许带病运行。"""


# -- 数据模型 ----------------------------------------------------------


@dataclass(frozen=True)
class Clue:
    """一条可查的线索：查得到，但不挡路。"""

    id: str
    text: str = ""
    skill: str = ""
    stat: str = ""
    dv: int = 0
    keywords: tuple = ()


@dataclass(frozen=True)
class Encounter:
    """一场遭遇。on_win / on_lose 是**战斗退出条件**——由剧情层持有。"""

    groups: tuple = ()
    on_win: str = ""
    on_lose: str = ""
    on_withdraw: str = ""
    note: str = ""
    range_m: int = 0
    modifiers: tuple = ()


@dataclass(frozen=True)
class Edge:
    """一条出边：满足 when 就能去 to。

    kind 的四种来源（剧本只有这些，不允许新增动词）：

    * exit    —— 玩家说去哪（when 默认 always）
    * gate    —— 技能闸门，可能带检定（check）
    * battle  —— 战斗结果驱动（when = battle:won / lost / withdraw）
    * ending  —— 收尾，to 是结局 key，不是场景 id
    """

    from_id: str
    to: str
    when: Condition
    kind: str = "exit"
    label: str = ""
    #: 走这条边要不要检定（None = 不检定，直接过去）
    check: dict | None = None
    #: 检定失败的落点（None = 留在原地）
    on_fail: str = ""
    #: 不检定时的固定代价（例如 {"eb": 50}）
    cost: dict | None = None
    #: 来源 id（gate id / encounter note / exit 提示），给 DM 看
    source: str = ""

    move = property(lambda self: self.to)

    def describe(self) -> str:
        head = self.from_id + " -[" + self.when.describe() + "]-> " + self.to
        if self.check:
            head += " (检定 " + str(self.check.get("skill")) + " DV" + str(
                self.check.get("dv")) + ")"
        if self.on_fail:
            head += " 失败->" + self.on_fail
        if self.kind == "ending":
            head += " (结局)"
        return head


@dataclass
class Scene:
    id: str
    name: str = ""
    text: str = ""
    act: str = ""
    act_title: str = ""
    npcs: tuple = ()            # ((key, inline_dict), ...)
    clues: tuple = ()
    encounter: Encounter | None = None
    ending: str = ""
    rewards: dict = field(default_factory=dict)
    #: 场景奖励的发放条件（None = 无条件）。原文里「只有钉了心脏才有青睐」
    #: 这种结构就落在这里——条件不满足时 rewards 一条都不结算。
    rewards_when: Condition | None = None
    #: 人情那一份的发放条件（None = 无条件）。原文：「如果他们记得刺穿
    #: 鲁思文勋爵的心脏…那他们就赢得了爱乐吸血鬼的青睐」——钱照付，
    #: 青睐要给条件。
    favor_when: Condition | None = None
    map_id: str = ""

    def npc_keys(self) -> list:
        return [key for key, _ in self.npcs]

    def npc(self, key: str) -> dict:
        for k, inline in self.npcs:
            if k == key:
                return inline
        return {}


@dataclass
class Act:
    id: str
    title: str = ""
    goal: str = ""
    scenes: tuple = ()


@dataclass
class Story:
    id: str
    title: str = ""
    premise: str = ""
    acts: tuple = ()
    scenes: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    npcs: dict = field(default_factory=dict)
    endings: dict = field(default_factory=dict)

    # -- 查询接口（剧情状态机只通过这些说话） --------------------------

    def scene(self, scene_id: str):
        return self.scenes.get(scene_id)

    def first_scene(self) -> str:
        for act in self.acts:
            if act.scenes:
                return act.scenes[0]
        raise StoryError("剧本里没有任何场景")

    def act_of(self, scene_id: str):
        sc = self.scenes.get(scene_id)
        if sc is None:
            return None
        for act in self.acts:
            if act.id == sc.act:
                return act
        return None

    def goal_of(self, scene_id: str) -> str:
        """这一节所属幕的目标（给 DM 提示词用）。"""
        act = self.act_of(scene_id)
        return act.goal if act else ""

    def outgoing(self, scene_id: str) -> list:
        return list(self.edges.get(scene_id, ()))

    def exits(self, scene_id: str) -> list:
        """本场「玩家说去哪」的出口。"""
        return [e for e in self.outgoing(scene_id) if e.kind == "exit"]

    def gates(self, scene_id: str) -> list:
        """本场的技能闸门（可能带检定）。"""
        return [e for e in self.outgoing(scene_id) if e.kind == "gate"]

    def ending_text(self, key: str) -> str:
        return self.endings.get(key, "")

    def actions_of(self, scene_id: str) -> list:
        """本场的复杂动作。**由旧格式编译出来**（延迟导入，避免环）。

        exits / gates / clues / encounter 各自编译成一份 Action，
        所以 opera.json 一行不用改就获得了「可执行」能力。
        """
        scene = self.scenes.get(scene_id)
        if scene is None:
            return []
        from .action import build_actions

        return build_actions(self, scene)

    def available(self, scene_id: str, ctx) -> list:
        """当前世界状态下**能走**的边。

        这是剧情状态机唯一的「去哪」入口：判定由条件做，副作用不在
        这里发生。战斗边不在此返回——战斗是否结束由战斗状态机告知。
        """
        out = []
        for edge in self.outgoing(scene_id):
            if edge.kind == "battle":
                continue
            if edge.when.matches(ctx):
                out.append(edge)
        return out

    def npc(self, key: str, scene_id: str = "") -> dict:
        """NPC 定义：场景内联优先，其次剧本顶层。"""
        if scene_id:
            sc = self.scenes.get(scene_id)
            if sc is not None and key in sc.npc_keys():
                inline = sc.npc(key)
                if inline:
                    return inline
        return dict(self.npcs.get(key) or {})

    # -- 诊断 ----------------------------------------------------------

    def graph(self) -> dict:
        """from_id -> [to, ...]，给测试/文档看整条剧情线。"""
        out = {}
        for sid in self.scenes:
            out[sid] = [e.to for e in self.outgoing(sid)]
        return out

    def reachable(self) -> set:
        """从第一场景出发能走到的场景（含结局 key）。"""
        start = self.first_scene()
        seen = {start}
        queue = [start]
        while queue:
            cur = queue.pop()
            for edge in self.outgoing(cur):
                if edge.to not in seen:
                    seen.add(edge.to)
                    if edge.to in self.scenes:
                        queue.append(edge.to)
        return seen


# -- 编译 --------------------------------------------------------------


def compile_story(story_id: str, *, story_dir=None):
    """读 JSON 并编译成 Story。任何结构问题都在这里抛 StoryError。"""
    base = Path(story_dir) if story_dir is not None else STORY_DIR
    path = base / (story_id + ".json")
    if not path.exists():
        raise StoryError("没有剧本 " + story_id + "（" + str(path) + "）")
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return compile_dict(raw, story_id=story_id)


def compile_dict(raw: dict, *, story_id: str = "") -> Story:
    """从已经读进来的 dict 编译（测试用，也方便将来从 DB 读剧本）。"""
    sid = str(raw.get("id") or story_id or "")
    if not sid:
        raise StoryError("剧本缺少 id")

    acts: list = []
    scenes: dict = {}
    for act_raw in raw.get("acts") or []:
        act_id = str(act_raw.get("id") or "")
        if not act_id:
            raise StoryError("幕缺少 id")
        scene_ids: list = []
        for sc_raw in act_raw.get("scenes") or []:
            scene = _build_scene(
                sc_raw,
                act_id=act_id,
                act_title=str(act_raw.get("title") or ""),
            )
            if scene.id in scenes:
                raise StoryError("场景 id 重复：" + scene.id)
            scenes[scene.id] = scene
            scene_ids.append(scene.id)
        acts.append(
            Act(
                id=act_id,
                title=str(act_raw.get("title") or ""),
                goal=str(act_raw.get("goal") or ""),
                scenes=tuple(scene_ids),
            )
        )

    if not scenes:
        raise StoryError("剧本里没有任何场景")

    edges: dict = {}
    for scene in scenes.values():
        out: list = []
        out.extend(_edges_from_exits(scene))
        out.extend(_edges_from_gates(scene))
        out.extend(_edges_from_encounter(scene))
        edges[scene.id] = out

    story = Story(
        id=sid,
        title=str(raw.get("title") or ""),
        premise=str(raw.get("premise") or ""),
        acts=tuple(acts),
        scenes=scenes,
        edges=edges,
        npcs={str(k): dict(v) for k, v in (raw.get("npcs") or {}).items()},
        endings={str(k): str(v) for k, v in (raw.get("endings") or {}).items()},
    )
    validate(story)
    return story


def _build_scene(raw: dict, *, act_id: str, act_title: str) -> Scene:
    sid = str(raw.get("id") or "")
    if not sid:
        raise StoryError("场景缺少 id")

    npcs: list = []
    seen_keys = set()
    for item in raw.get("npcs") or []:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("id") or "").strip()
            if not key:
                raise StoryError(sid + " 的内联 NPC 缺少 key")
            if key not in seen_keys:
                npcs.append((key, dict(item)))
                seen_keys.add(key)
        else:
            key = str(item or "").strip()
            if key and key not in seen_keys:
                npcs.append((key, {}))
                seen_keys.add(key)

    clues: list = []
    for c in raw.get("clues") or []:
        cid = str(c.get("id") or "")
        if not cid:
            raise StoryError(sid + " 的线索缺少 id")
        clues.append(
            Clue(
                id=cid,
                text=str(c.get("text") or ""),
                skill=str(c.get("skill") or ""),
                stat=str(c.get("stat") or ""),
                dv=int(c.get("dv") or 0),
                keywords=tuple(str(k) for k in (c.get("keywords") or [])),
            )
        )

    encounter = None
    enc = raw.get("encounter")
    if isinstance(enc, dict):
        encounter = Encounter(
            groups=tuple(enc.get("groups") or ()),
            on_win=str(enc.get("on_win") or ""),
            on_lose=str(enc.get("on_lose") or ""),
            on_withdraw=str(enc.get("on_withdraw") or ""),
            note=str(enc.get("note") or ""),
            range_m=int(enc.get("range_m") or 0),
            modifiers=tuple(enc.get("modifiers") or ()),
        )

    scene = Scene(
        id=sid,
        name=str(raw.get("name") or sid),
        text=str(raw.get("text") or ""),
        act=act_id,
        act_title=act_title,
        npcs=tuple(npcs),
        clues=tuple(clues),
        encounter=encounter,
        ending=str(raw.get("ending") or ""),
        rewards=dict(raw.get("rewards") or {}),
        map_id=str(raw.get("map") or ""),
    )
    # 原始 transitions 留在对象上，供边构建器读完就丢（不进对外接口）
    rewards_raw = raw.get("rewards")
    if isinstance(rewards_raw, dict):
        if rewards_raw.get("when") is not None:
            try:
                scene.rewards_when = parse_when(rewards_raw.get("when"))
            except ConditionError as exc:
                raise StoryError(f"{sid} 的 rewards.when 写错了：{exc}") from exc
        favor_raw = rewards_raw.get("favor")
        if isinstance(favor_raw, dict) and favor_raw.get("when") is not None:
            try:
                scene.favor_when = parse_when(favor_raw.get("when"))
            except ConditionError as exc:
                raise StoryError(f"{sid} 的 favor.when 写错了：{exc}") from exc
    scene._raw_exits = list(raw.get("exits") or [])       # noqa: SLF001
    scene._raw_gates = list(raw.get("gates") or [])       # noqa: SLF001
    scene._raw_actions = list(raw.get("actions") or [])   # noqa: SLF001
    return scene


def _edges_from_exits(scene: Scene) -> list:
    """出口：玩家说去哪就去哪。

    剧本两种写法都认：

    * `"exits": ["a2.noodles"]`                          —— 纯 id
    * `"exits": [{"to": "a2.office", "label": "去看看"}]` —— 带提示

    可选 `when` 把「玩家说去」收紧成「满足条件才能去」——这就是
    原文里「如果对此进行跟进，请前往支线」的表达方式。
    ``"exits": [{"to": "a2.office", "when": {"talked": {"npc": "student"}}}]``
    """
    out: list = []
    for item in getattr(scene, "_raw_exits", []) or []:
        if isinstance(item, dict):
            to = str(item.get("to") or "").strip()
            label = str(item.get("label") or "")
            when_spec = item.get("when")
            check = item.get("check")
            cost = item.get("cost")
        else:
            to = str(item or "").strip()
            label = ""
            when_spec = None
            check = None
            cost = None
        if not to:
            continue
        out.append(
            Edge(
                from_id=scene.id,
                to=to,
                when=parse_when(when_spec),
                kind="exit",
                label=label,
                check=dict(check) if isinstance(check, dict) else None,
                cost=dict(cost) if isinstance(cost, dict) else None,
            )
        )
    return out


def _edges_from_gates(scene: Scene) -> list:
    """技能闸门：成功走 next，失败走 fail（fail 可能指回自己）。"""
    out: list = []
    for gate in getattr(scene, "_raw_gates", []) or []:
        if not isinstance(gate, dict):
            continue
        skills = [str(s) for s in (gate.get("skills") or []) if str(s)]
        if not skills:
            one = str(gate.get("skill") or "")
            if one:
                skills = [one]
        when = (Skill(skills[0]) if len(skills) == 1
                else _any_skills(skills)) if skills else Always()
        check = {"skill": skills[0] if skills else "",
                 "dv": int(gate.get("dv") or 0)}
        source = str(gate.get("id") or "") or str(check["skill"])
        for target_key in ("next", "fail"):
            to = str(gate.get(target_key) or "").strip()
            if not to:
                continue
            out.append(
                Edge(
                    from_id=scene.id,
                    to=to,
                    when=when,
                    kind="gate",
                    label=str(gate.get("label") or ""),
                    check=dict(check),
                    on_fail=to if target_key == "fail" else "",
                    source=source,
                )
            )
    return out


def _edges_from_encounter(scene: Scene) -> list:
    """遭遇：战斗的退出条件由**剧情层**持有（战斗本身不推进剧情）。"""
    enc = scene.encounter
    if enc is None:
        return []
    out: list = []
    for result, target in (("won", enc.on_win), ("lost", enc.on_lose),
                           ("withdraw", enc.on_withdraw)):
        to = str(target or "").strip()
        if not to:
            continue
        kind, dest = ("ending", to.split(":", 1)[1].strip()) \
            if to.startswith("ending:") else ("battle", to)
        if not dest:
            continue
        out.append(
            Edge(
                from_id=scene.id,
                to=dest,
                when=Battle(result),
                kind=kind,
                label=enc.note,
                source="encounter:" + result,
            )
        )
    return out


def _any_skills(skills: list):
    from .conditions import AnyOf
    return AnyOf(tuple(Skill(s) for s in skills))


def validate(story: Story) -> None:
    """结构校验。**宁可在编译期炸，也不要运行时静默卡死。**"""
    problems: list = []

    known = set(story.scenes)
    ending_keys = set(story.endings)

    # 1) 边必须指向真实存在的场景或结局
    for sid, edges in story.edges.items():
        for edge in edges:
            if edge.kind == "ending":
                if edge.to not in ending_keys:
                    problems.append(
                        edge.describe() + " -> 结局不存在：" + edge.to)
                continue
            if edge.to not in known:
                problems.append(edge.describe() + " -> 场景不存在：" + edge.to)
            if edge.on_fail and edge.on_fail not in known:
                problems.append(
                    edge.describe() + " -> 失败落点不存在：" + edge.on_fail)

    # 2) 场景自己的 ending 字段也要存在
    for scene in story.scenes.values():
        if scene.ending and scene.ending not in ending_keys:
            problems.append(
                scene.id + " 的 ending 不存在：" + scene.ending)

    # 3) 死路：既没有出边、也不是结局场景
    for scene in story.scenes.values():
        if story.outgoing(scene.id):
            continue
        if scene.ending:
            continue
        problems.append(
            scene.id + " 是死路：没有 exits / gates / on_win / ending")

    # 4) 不可达
    reachable = story.reachable()
    for sid in story.scenes:
        if sid not in reachable:
            problems.append(sid + " 不可达：从第一场景走不到")

    if problems:
        raise StoryError("剧本 " + story.id + " 有问题：\n  - "
                         + "\n  - ".join(problems))
