"""剧情条件：把「什么时候能走这条边」变成可以判定的数据。

设计约束（2026-11-14 定）：

* 条件必须是**一小组通用谓词** —— 不允许为某个具体动作（例如「买票」）
  写专门的判断。剧本里每一条边都由这些谓词组合而成。
* 条件只**读**世界状态和本次意图，不写任何东西；判定与副作用彻底分开。
* 条件要能序列化成一句人话（describe），因为剧本作者和 DM 都要看。

这一层不认识数据库、不认识 LLM、不认识 QQ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# -- 世界状态（第二层填入，这里只定义形状） ----------------------------


@dataclass
class World:
    """一次游玩的世界状态快照 —— **只读**。

    真正的持久化在存档层（engine.save），这里只规定剧情状态机需要看什么。
    任何条件都只能读这里 + 本次意图。
    """

    #: 已经查到的线索 id
    clues: set = field(default_factory=set)
    #: 跟某个 NPC 聊过的话题：{npc_key: {topic, ...}}；空集合 = 聊过但没记话题
    talked: dict = field(default_factory=dict)
    #: 通用标记（任务完成、旗标解锁……），名字由剧本自己起
    flags: set = field(default_factory=set)
    #: 上一场战斗的结果：""（没打过）| "won" | "lost" | "withdraw"
    battle: str = ""
    #: 去过的场景 id
    visited: set = field(default_factory=set)
    #: 全队身上的东西（gear 并集）。条件 item:X 读它。
    items: set = field(default_factory=set)

    def has_clue(self, clue_id: str) -> bool:
        return bool(clue_id) and clue_id in self.clues

    def talked_about(self, npc_key: str, topic: str = "") -> bool:
        topics = self.talked.get(npc_key)
        if topics is None:
            return False
        return True if not topic else topic in topics

    def has_flag(self, name: str) -> bool:
        return bool(name) and name in self.flags

    def has_item(self, name: str) -> bool:
        return bool(name) and name in self.items


@dataclass
class Context:
    """一次判定的输入：世界状态 + 玩家这一句的意图。"""

    world: World
    #: 这一句要用的技能（没有就是空串）
    skill: str = ""
    #: 玩家原话
    text: str = ""


# -- 条件 --------------------------------------------------------------


class ConditionError(ValueError):
    """剧本里的条件写法不认识。**编译期就该炸**，不许静默降级。"""


class Condition:
    """条件基类。子类只实现 matches 和 tag。"""

    tag: str = ""

    def matches(self, ctx: Context) -> bool:  # pragma: no cover - 抽象
        raise NotImplementedError

    def describe(self) -> str:
        return self.tag


@dataclass(frozen=True)
class Always(Condition):
    """无条件：玩家说去就去，或者遭遇必然触发。"""

    tag: str = "always"

    def matches(self, ctx: Context) -> bool:
        return True


@dataclass(frozen=True)
class Never(Condition):
    """占位：这一版还没接通的条件。"""

    tag: str = "never"

    def matches(self, ctx: Context) -> bool:
        return False


@dataclass(frozen=True)
class Skill(Condition):
    """玩家这一句恰好用到了这个技能。"""

    skill: str = ""
    tag: str = "skill"

    def matches(self, ctx: Context) -> bool:
        return bool(self.skill) and ctx.skill == self.skill

    def describe(self) -> str:
        return "skill:" + str(self.skill)


@dataclass(frozen=True)
class Battle(Condition):
    """上一场战斗的结果。"""

    result: str = ""
    tag: str = "battle"

    def matches(self, ctx: Context) -> bool:
        return bool(self.result) and ctx.world.battle == self.result

    def describe(self) -> str:
        return "battle:" + str(self.result)


@dataclass(frozen=True)
class Clue(Condition):
    """查到了某条线索。"""

    clue_id: str = ""
    tag: str = "clue"

    def matches(self, ctx: Context) -> bool:
        return ctx.world.has_clue(str(self.clue_id))

    def describe(self) -> str:
        return "clue:" + str(self.clue_id)


@dataclass(frozen=True)
class Talked(Condition):
    """跟某个 NPC 聊过（可限定话题）。"""

    npc: str = ""
    topic: str = ""
    tag: str = "talked"

    def matches(self, ctx: Context) -> bool:
        return bool(self.npc) and ctx.world.talked_about(str(self.npc),
                                                         str(self.topic))

    def describe(self) -> str:
        text = "talked:" + str(self.npc)
        return text + ("/" + self.topic if self.topic else "")


@dataclass(frozen=True)
class Flag(Condition):
    """通用标记：任务完成、东西拿到手……"""

    name: str = ""
    tag: str = "flag"

    def matches(self, ctx: Context) -> bool:
        return ctx.world.has_flag(str(self.name))

    def describe(self) -> str:
        return "flag:" + str(self.name)


@dataclass(frozen=True)
class Item(Condition):
    """队里有人身上带着这件东西（演出票、木桩……）。"""

    name: str = ""
    tag: str = "item"

    def matches(self, ctx: Context) -> bool:
        return ctx.world.has_item(str(self.name))

    def describe(self) -> str:
        return "item:" + str(self.name)


@dataclass(frozen=True)
class All(Condition):
    """全部满足。"""

    parts: tuple = ()
    tag: str = "all"

    def matches(self, ctx: Context) -> bool:
        return all(p.matches(ctx) for p in self.parts)

    def describe(self) -> str:
        return "all(" + " & ".join(p.describe() for p in self.parts) + ")"


@dataclass(frozen=True)
class AnyOf(Condition):
    """任意满足。"""

    parts: tuple = ()
    tag: str = "any"

    def matches(self, ctx: Context) -> bool:
        return any(p.matches(ctx) for p in self.parts)

    def describe(self) -> str:
        return "any(" + " | ".join(p.describe() for p in self.parts) + ")"


@dataclass(frozen=True)
class Not(Condition):
    """取反。"""

    inner: Condition = field(default_factory=Always)
    tag: str = "not"

    def matches(self, ctx: Context) -> bool:
        return not self.inner.matches(ctx)

    def describe(self) -> str:
        return "not(" + self.inner.describe() + ")"


def parse_when(spec) -> Condition:
    """把剧本里的 when 解析成条件对象。

    认这几种写法::

        "always"                                  -> Always()
        {"battle": "won"}                          -> Battle("won")
        {"skill": "撬锁"}                           -> Skill("撬锁")
        {"clue": "office_photo"}                    -> Clue("office_photo")
        {"talked": {"npc": "student"}}              -> Talked("student")
        {"flag": "task:office"}                     -> Flag("task:office")
        {"all": [...]} / {"any": [...]}             -> All / AnyOf
        {"not": ...}                                -> Not
    """
    if spec is None:
        return Always()
    if isinstance(spec, Condition):
        return spec
    if isinstance(spec, str):
        text = spec.strip()
        if text == "always":
            return Always()
        if text == "never":
            return Never()
        raise ConditionError("认不出条件：" + repr(spec))
    if isinstance(spec, dict):
        if len(spec) != 1:
            raise ConditionError("一个条件只能有一个键：" + repr(spec))
        items = list(spec.items())
        key, value = str(items[0][0]), items[0][1]
        if key == "always":
            return Always()
        if key == "never":
            return Never()
        if key == "battle":
            return Battle(str(value))
        if key == "skill":
            return Skill(str(value))
        if key == "clue":
            return Clue(str(value))
        if key == "flag":
            return Flag(str(value))
        if key == "item":
            return Item(str(value))
        if key == "talked":
            if isinstance(value, dict):
                return Talked(str(value.get("npc") or ""),
                              str(value.get("topic") or ""))
            return Talked(str(value))
        if key == "all":
            return All(tuple(parse_when(v) for v in _as_list(value)))
        if key == "any":
            return AnyOf(tuple(parse_when(v) for v in _as_list(value)))
        if key == "not":
            return Not(parse_when(value))
    raise ConditionError("认不出条件：" + repr(spec))


def _as_list(value) -> Iterable:
    if isinstance(value, (list, tuple)):
        return value
    return (value,)
