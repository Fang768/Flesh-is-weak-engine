"""原子动作：引擎层的**最小可执行单元**。

每个原子只做一件事，不可再分：

    roll    掷一次骰（纯计算，不改状态）
    charge  扣资源（EB/HP/SP），不够则失败
    grant   给东西
    take    收走东西
    move    校验边 + 换场（**不带代价**，代价永远写在 Action 的步骤里）
    mark    记线索 / 话题 / 旗标
    reveal  取一段剧情文本（只读）
    record  写事件流水
    battle  战斗进出（交给战斗状态机）

复杂动作（engine/action.py）由这些原子按剧本数据组合而成。
**没有针对具体动作（买票/撬锁/翻衣柜）的方法**——那些是数据。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field

from .conditions import Context
from .save import Item, Save
from .story import Story


class OpError(RuntimeError):
    """原子动作失败。执行器捕获它 -> 整个 Action 回滚。"""

    def __init__(self, message: str, *, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


# -- 回执 --------------------------------------------------------------


@dataclass(frozen=True)
class RollResult:
    skill: str
    dv: int
    natural: int
    total: int
    success: bool
    fumble: bool = False
    card: str = ""

    @property
    def margin(self) -> int:
        return self.total - self.dv

    def describe(self) -> str:
        verdict = "成功" if self.success else "失败"
        if self.fumble:
            verdict += "（大失败）"
        return (self.skill + " DV" + str(self.dv) + "：掷出 "
                + str(self.natural) + "，合计 " + str(self.total)
                + " · " + verdict)


@dataclass
class StepResult:
    """一个原子动作的执行回执。"""

    op: str
    ok: bool
    detail: str = ""
    data: dict = field(default_factory=dict)


# -- 原子动作 ----------------------------------------------------------


class Ops:
    """原子动作集合。写操作都在事务里（见 atomically）。"""

    def __init__(self, story: Story, save: Save, *, roller=None) -> None:
        self.story = story
        self.save = save
        self._roller = roller
        self._depth = 0

    # -- 事务 ----------------------------------------------------------

    @contextmanager
    def atomically(self):
        """把写操作攒起来，退出时统一提交；异常则回滚。

        嵌套调用是安全的（只最外层真正提交）。
        """
        self._depth += 1
        try:
            yield self
            self._depth -= 1
            if self._depth == 0:
                self._commit()
        except BaseException:
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._rollback()
            raise

    def _commit(self) -> None:
        try:
            self.save.conn.commit()
        except Exception:  # noqa: BLE001  提交失败不能静默
            self.save.conn.rollback()
            raise

    def _rollback(self) -> None:
        try:
            self.save.conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    # -- 掷骰（纯计算） -------------------------------------------------

    def roll(self, skill: str, dv: int, card=None) -> RollResult:
        """掷一次骰。**不改任何状态**——记线索要另外调 mark。"""
        target = card or (self.save.cards()[0] if self.save.cards() else None)
        if target is None:
            raise OpError("没有角色卡，没法掷骰", code="no_card")
        stat_code, stat_value, level = self._skill_values(target, skill)
        natural = self._roll_d10()
        total = natural + stat_value + level
        return RollResult(
            skill=str(skill or "行动"),
            dv=int(dv or 0),
            natural=natural,
            total=total,
            success=total >= int(dv or 0),
            fumble=natural == 1,
            card=target.owner_qq,
        )

    def _roll_d10(self) -> int:
        if self._roller is not None:
            value = (self._roller.roll(10)
                     if hasattr(self._roller, "roll") else self._roller())
            return int(value)
        import random

        return random.randint(1, 10)

    def _skill_values(self, card, skill: str):
        """技能 -> (属性码, 属性值, 技能等级)。规则表在 cyberpunk 层。"""
        from cyberpunk import skills as skills_mod

        code = skills_mod.stat_of(skill) or ""
        stat_value = card.stat(code) if code else 0
        return code, stat_value, card.skill(skill)

    # -- 资源 ----------------------------------------------------------

    def charge(self, owner_qq: str, *, eb: int = 0, hp: int = 0,
               sp: int = 0) -> StepResult:
        """加减资源。**只接受增量**，不接受「设为某值」。

        减钱不够 -> 抛 OpError，整个 Action 回滚
        （不会出现「钱扣了但没换成场」）。
        """
        card = self.save.card(owner_qq)
        if card is None:
            raise OpError("找不到角色卡：" + str(owner_qq), code="no_card")
        if eb == 0 and hp == 0 and sp == 0:
            return StepResult("charge", ok=True, detail="没有代价")
        if eb and card.eb + int(eb) < 0:
            raise OpError(
                "EB 不够：要 " + str(abs(int(eb))) + "，你只有 " + str(card.eb),
                code="insufficient_eb")
        state = dict(card.state or {})
        state["eb"] = int(state.get("eb") or 0) + int(eb)
        if hp:
            state["hp"] = max(0, int(state.get("hp") or 0) + int(hp))
        if sp:
            state["sp"] = max(0, int(state.get("sp") or 0) + int(sp))
        self._write_state(owner_qq, card.kind, state)
        bits = []
        if eb:
            bits.append("EB " + ("+" if eb > 0 else "") + str(int(eb)))
        if hp:
            bits.append("HP " + ("+" if hp > 0 else "") + str(int(hp)))
        if sp:
            bits.append("SP " + ("+" if sp > 0 else "") + str(int(sp)))
        return StepResult("charge", ok=True, detail="、".join(bits),
                          data={"eb": eb, "hp": hp, "sp": sp})

    def grant(self, owner_qq: str, item: str, *, kind: str = "gear",
              source: str = "story") -> StepResult:
        """给一件东西。重复给是 no-op。

        kind 区分普通装备 / 剧情道具 / 消耗品（见 save.Item）——
        剧本给的道具默认是 quest，用掉之后仍在身上。
        """
        name = str(item or "").strip()
        if not name:
            raise OpError("要给的东西没有名字", code="bad_item")
        card = self.save.card(owner_qq)
        if card is None:
            raise OpError("找不到角色卡：" + str(owner_qq), code="no_card")
        gear = list(card.gear or [])
        if card.has_item(name):
            return StepResult("grant", ok=True, detail="已经有 " + name)
        gear.append(Item(name=name, kind=kind, source=source))
        self._write_gear(owner_qq, card.kind, gear)
        return StepResult("grant", ok=True, detail="得到 " + name,
                          data={"item": name, "kind": kind})

    def take(self, owner_qq: str, item: str) -> StepResult:
        """收走一件东西。没有就失败（避免「凭空交出去」）。"""
        name = str(item or "").strip()
        card = self.save.card(owner_qq)
        if card is None:
            raise OpError("找不到角色卡：" + str(owner_qq), code="no_card")
        gear = [i for i in (card.gear or []) if i.name]
        hit = [i for i in gear if i.name == name]
        if not hit:
            raise OpError("身上没有 " + name, code="missing_item")
        gear = [i for i in gear if i.name != name]
        self._write_gear(owner_qq, card.kind, gear)
        return StepResult("take", ok=True, detail="交出 " + name,
                          data={"item": name})

    # -- 换场 ----------------------------------------------------------

    def move(self, to: str, *, ctx: Context | None = None) -> StepResult:
        """校验边 + 换场。**不带代价**（代价写在 Action 的步骤里）。

        只认本场声明过的出口，且条件必须满足
        ——DM 不能用讲故事绕过剧本。
        """
        target = str(to or "").strip()
        if not target:
            raise OpError("没有目标场景", code="bad_target")
        current = self.save.scene()
        if target == current:
            raise OpError("已经在 " + current, code="same_scene")
        context = ctx or Context(world=self.save.world())
        edges = [e for e in self.story.available(current, context)
                 if e.to == target]
        if not edges:
            known = [e.to for e in self.story.outgoing(current)]
            raise OpError(
                "「" + target + "」不是本场能走的路（本场出口："
                + ("、".join(known) or "无") + "）",
                code="no_edge")
        self.save.set_scene(target)
        return StepResult("move", ok=True, detail="前往 " + target,
                          data={"from": current, "to": target})

    # -- 记账 ----------------------------------------------------------

    def mark(self, kind: str, ident: str, *, topic: str = "") -> StepResult:
        """记线索 / 话题 / 旗标。三种，与条件谓词一一对应。"""
        what = str(kind or "").strip()
        key = str(ident or "").strip()
        if not key:
            raise OpError("要记的东西没有 id", code="bad_mark")
        if what == "clue":
            current = self.save.scene()
            scene = self.story.scene(current)
            known = {c.id for c in (scene.clues if scene else ())}
            if key not in known:
                raise OpError("本场没有这条线索：" + key, code="unknown_clue")
            changed = self.save.mark_clue(key)
            return StepResult("mark", ok=True,
                              detail=("新线索 " + key) if changed else "已查过",
                              data={"changed": changed})
        if what == "talked":
            changed = self.save.mark_talked(key, topic)
            return StepResult("mark", ok=True,
                              detail=("聊过 " + key) if changed else "已记过",
                              data={"changed": changed})
        if what == "flag":
            changed = self.save.mark_flag(key)
            return StepResult("mark", ok=True,
                              detail=("旗标 " + key) if changed else "已记过",
                              data={"changed": changed})
        raise OpError("不认识的 mark 类型：" + what, code="bad_mark")

    def reveal(self, ref: str) -> StepResult:
        """取一段剧情文本（只读）。ref 形状：scene:X / clue:X / ending:X。"""
        spec = str(ref or "").strip()
        if ":" not in spec:
            raise OpError("reveal 要写成 kind:id：" + spec, code="bad_ref")
        kind, ident = spec.split(":", 1)
        if kind == "scene":
            scene = self.story.scene(ident)
            if scene is None:
                raise OpError("没有这个场景：" + ident, code="unknown_scene")
            text = scene.name + chr(10) + scene.text
        elif kind == "clue":
            text = self._clue_text(ident)
        elif kind == "ending":
            text = self.story.ending_text(ident)
            if not text:
                raise OpError("没有这个结局：" + ident, code="unknown_ending")
        else:
            raise OpError("reveal 不认识的类型：" + kind, code="bad_ref")
        return StepResult("reveal", ok=True, detail=text,
                          data={"ref": spec, "text": text})

    def _clue_text(self, clue_id: str) -> str:
        for scene in self.story.scenes.values():
            for clue in scene.clues:
                if clue.id == clue_id:
                    return clue.text
        raise OpError("剧本里没有这条线索：" + clue_id, code="unknown_clue")

    def settle_rewards(self, scene_id: str) -> StepResult:
        """按场景的 rewards 结算（含 rewards.when 条件）。

        条件不满足 → 一条都不发。这样「只有钉了心脏才有青睐」就能表达，
        而不是无脑发放（原文 :105 是**有条件**的）。
        """
        scene = self.story.scene(scene_id)
        if scene is None or not scene.rewards:
            return StepResult("settle", ok=True, detail="")
        ctx = Context(world=self.save.world())
        if scene.rewards_when is not None and not scene.rewards_when.matches(ctx):
            return StepResult("settle", ok=True, detail="奖励条件未满足",
                              data={"skipped": True})
        rewards = dict(scene.rewards)
        # 人情单独一个开关：钱照付，青睐要看「有没有做戏剧性的事」
        if scene.favor_when is not None and not scene.favor_when.matches(ctx):
            rewards.pop("favor", None)
        done = self.save.settle_rewards(scene_id, rewards)
        return StepResult("settle", ok=True, detail="已结算",
                          data={"items": done, "rewards": rewards})

    def record(self, kind: str, text: str, data: dict | None = None,
               actor: str = "") -> StepResult:
        eid = self.save.record(kind, text, data=data, actor=actor)
        return StepResult("record", ok=True, detail=text, data={"id": eid})

    # -- 战斗交接 ------------------------------------------------------

    def battle(self, op: str, *, result: str = "") -> StepResult:
        """战斗进出。**只做交接**，不跑战斗。

        enter  -> 交出遭遇单（谁、什么修正、退出条件），战斗层照着开打
        leave  -> 回报结果（won/lost/withdraw），剧情层据此走边
        """
        action = str(op or "").strip()
        scene = self.story.scene(self.save.scene())
        if scene is None or scene.encounter is None:
            raise OpError("本场没有遭遇", code="no_encounter")
        enc = scene.encounter
        if action == "enter":
            order = {
                "scene": scene.id,
                "groups": list(enc.groups),
                "modifiers": list(enc.modifiers),
                "range_m": enc.range_m,
                "note": enc.note,
                "exit_conditions": {
                    "won": enc.on_win,
                    "lost": enc.on_lose,
                    "withdraw": enc.on_withdraw,
                },
            }
            return StepResult("battle", ok=True,
                              detail="交出一场遭遇：" + (enc.note or scene.id),
                              data=order)
        if action == "leave":
            value = str(result or "").strip()
            if value not in ("won", "lost", "withdraw"):
                raise OpError("战斗结果只能是 won/lost/withdraw：" + value,
                              code="bad_result")
            self.save.set_battle_result(value)
            return StepResult("battle", ok=True, detail="战斗结果：" + value,
                              data={"result": value})
        raise OpError("battle 只认 enter / leave：" + action, code="bad_op")

    # -- 玩家状态写入（窄口：只写加减结果 / gear） ---------------------

    def _write_state(self, owner_qq: str, kind: str, state: dict) -> None:
        if kind == "easy":
            data = self._easy_data(owner_qq)
            data["state"] = state
            if "hp" in state:
                data["hp"] = state["hp"]
            if "sp" in state:
                data["sp"] = state["sp"]
            self._write_easy(owner_qq, data)
            return
        self.save.conn.execute(
            "UPDATE characters SET state = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(state, ensure_ascii=False), _now(),
             self.save.group_id, str(owner_qq)),
        )

    def _write_gear(self, owner_qq: str, kind: str, gear: list) -> None:
        """落库：Item 转回字符串 / dict 形状（旧数据保持裸字符串）。"""
        payload = [i.to_json() if hasattr(i, "to_json") else i for i in gear]
        if kind == "easy":
            data = self._easy_data(owner_qq)
            data["gear"] = payload
            self._write_easy(owner_qq, data)
            return
        self.save.conn.execute(
            "UPDATE characters SET gear = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(payload, ensure_ascii=False), _now(),
             self.save.group_id, str(owner_qq)),
        )

    def _easy_data(self, owner_qq: str) -> dict:
        row = self.save.conn.execute(
            "SELECT data FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
            (self.save.group_id, str(owner_qq)),
        ).fetchone()
        if row is None:
            raise OpError("找不到简易卡：" + str(owner_qq), code="no_card")
        try:
            data = json.loads(row["data"] or "{}")
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _write_easy(self, owner_qq: str, data: dict) -> None:
        self.save.conn.execute(
            "UPDATE easy_cards SET data = ?, updated_at = ?"
            " WHERE group_id = ? AND owner_qq = ?",
            (json.dumps(data, ensure_ascii=False), _now(),
             self.save.group_id, str(owner_qq)),
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
