# 子代理做 DM / 敌人脚本：分层设计（2026-11-07 批准）

> 目标：让敌人和 NPC 的临场反应更像真人，同时不把规则裁决交给模型。
> 状态：设计已记录，未实现。实现时按「引擎保底 → 子代理提议 → 引擎校验」的顺序做。

## 1. 分层

| 层 | 归谁 | 内容 |
|---|---|---|
| 硬事实 | 引擎（`core/combat.py`、`core/turn_flow.py`） | 命中、伤害、HP/SP、轮次、动作槽、`on_win/on_lose`、脚本字段合法性 |
| 软决策 | 子代理（每敌一个短上下文） | 打谁、绕后/嘴炮/拖时间、临场撤退、台词 |
| 叙事 | 子代理 / `core/llm.py` | 旁白、NPC 语气、线索怎么描述 |
| 剧情骨架 | `story/*.json` | 场景图、gate、结局、奖励；子代理不能改 |

子代理**只输出受限 JSON**，引擎校验合法才执行；超时、非法、超预算一律回退现有确定性脚本。

## 2. 敌人决策 JSON（草案）

```json
{
  "action": "attack | move | flee | taunt | use | wait",
  "target": "pc | ally | 名字片段",
  "move_to": "behind_cover | nearest_exit | (x,y) | null",
  "line": "一句台词，可为空",
  "reason": "一句话理由，写进事件日志，便于复盘"
}
```

- `action` 决定调用哪个现有函数；`target` 必须解析到当前战斗里的活人，否则整条回退。
- 台词只进叙事，不参与判定；`reason` 只记日志。

## 3. 上下文与刷新时机

每个敌人一份短记忆（可存 `combatants.script["_brain"]` 或独立表）：

- 当前轮次、自己 HP/SP、看到的敌人（名字、距离/掩体）、同伴死活；
- 最近 2–3 条自己说过的话，避免复读；
- 刷新节点：自己的回合开始、自己受伤、同伴倒下、场景换场。其它回合不调用。

## 4. 接入点

把 `_enemy_turn()` 里的「选目标 + 选动作」抽成协议：

```python
class EnemyBrain(Protocol):
    def decide(self, view: dict) -> dict: ...
```

- `ScriptedBrain`：现有 `targets/flee_if` 逻辑，默认、离线测试与超时回退都用它。
- `LLMSubagentBrain`：拼一层提示词，调 `llm.complete_json`，可并发多敌；结果校验失败就退回 `ScriptedBrain`。
- 只有 `LLMSubagentBrain.decide` 是新的网络调用，其余出牌路径不变。

## 5. 成本与可复现

- 每轮每敌一次调用；4 敌 ≈ 每轮 4 次。用小模型/短提示、批量决策、只在关键节点刷新。
- 决策 JSON 与 seed 落 `events`，`scripts/simulate.py` 继续用假模型跑确定性回归；真模型只进 `--real`。
- 提示词里写清「不知道的事不要编」，并对 `target` 做白名单校验，防幻觉改规则。

## 6. 落地顺序（建议）

1. 抽 `EnemyBrain` 协议 + `ScriptedBrain`，行为与现状逐字节一致（纯重构，测试全绿）。
2. 加 `LLMSubagentBrain`，只接管「打谁/嘴炮」，动作仍限 attack/taunt；离线开一个实验场景。
3. 放开 move/flee，接 `flee_blocked` 的临场判断。
4. 剧情侧试点：让子代理只写 NPC 台词与线索描述，gate/结局仍走 JSON。

## 7. 非目标

- 不做多智能体互相辩论；不做子代理改卡/改剧情文件。
- 不把胜负、伤害、奖励结算交给模型。
