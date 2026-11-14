# engine/ — 引擎层（新结构）

2026-11-14 起按三层重写。旧的 `core/` **原地不动**，新代码只进这里，
直到能跑通一局完整剧本再切换。

## 三层

| 层 | 目录 | 职责 | 不做什么 |
|---|---|---|---|
| 1 数据层 | `engine/story.py` `engine/conditions.py` | 把剧本编译成**带条件的场景图**；定义世界状态形状 | 不碰数据库、不调 LLM |
| 1 数据层 | `engine/save.py` | 存档：进度/触发/玩家状态/NPC 记忆 | 不做业务判断、不写玩家状态 |
| 2 引擎层 | `engine/ops.py` | **原子动作**：roll / charge / grant / take / move / mark / reveal / record / battle | 每个只做一件事 |
| 2 引擎层 | `engine/action.py` | **复杂动作**：原子按剧本数据组合 + 事务 | 不认识具体动作 |
| 2 引擎层（待做） | `engine/battle.py` | 战斗状态机：进入/退出条件由剧情给 | 不推进剧情 |
| 3 LLM | （不在本包） | DM 的大脑 | 不改状态 |

## 第 1 层的产物

```python
from engine.story import compile_story

story = compile_story("opera")
story.scenes          # scene_id -> Scene（含 clues / npcs / encounter / rewards）
story.edges           # from_id  -> [Edge]（每条边带 when 条件）
story.acts            # 幕，带 goal（DM 需要它才知道这一节要干什么）
story.graph()         # {from: [to, ...]}，整条剧情线
story.available(scene, ctx)   # 当前状态下能走哪些边
story.goal_of(scene)  # 这一节所属幕的目标
```

## 第 1 层第二件：存档

```python
from engine.save import open_save

save = open_save(conn, "qq:123")
save.scene()                  # 现在在哪一节
save.world()                  # World 快照 → 直接喂给 story.available()
save.mark_clue("office_photo", by="u1")
save.mark_talked("student", "论文")
save.mark_flag("got:ticket")
save.set_battle_result("won")
save.cards()                  # RED 卡 / 简易卡归一化后的只读视图
save.memory("student")        # NPC 记忆
save.board()                  # DM 牌面（只放标记，不含原始流水）
```

**读写对称**：能写的 `mark_clue / mark_talked / mark_flag` 正好就是条件能读的
`Clue / Talked / Flag`；`set_battle_result` 对应 `Battle`。不开放"随便塞个 key"。

**玩家状态这一版只读**（`test_save_never_writes_player_state` 用源码扫描守着）：
战斗结算 / 义体安装 / 商店扣钱仍在写这些字段，此时再开一个写入口就是两个写者。
等 `engine/drama.py` 接管后再收归。

**两张新表/新列**（复用旧 schema，不挪用语义）：

| 名字 | 用途 |
|---|---|
| `campaign_flags`（新表） | 通用旗标，条件 `flag:X` 读它。**不**塞进 `scene_progress.gates_passed` |
| `scene_progress.battle_result`（新列） | 上一场战斗结果，条件 `battle:won` 读它 |

二者都有老库升级路径（`db._ensure_columns`），已验证旧库能原地升级。

## 原子动作 vs 复杂动作

**原子动作**（`engine/ops.py`，9 个，不可再分）：

| 原子 | 作用 | 改状态 |
|---|---|---|
| `roll(skill, dv, card)` | 掷一次骰 | ❌ 纯计算 |
| `charge(owner, eb/hp/sp)` | 加减资源，不够则失败 | ✅ |
| `grant(owner, item)` | 给东西 | ✅ |
| `take(owner, item)` | 收走东西 | ✅ |
| `move(to)` | 校验边 + 换场（**不带代价**） | ✅ |
| `mark(kind, id)` | 线索 / 话题 / 旗标 | ✅ |
| `reveal(ref)` | 取剧情文本 | ❌ 只读 |
| `record(kind, text)` | 写事件 | ✅ |
| `battle(enter/leave)` | 战斗交接 | ✅ |

**复杂动作**（`engine/action.py`）就是数据里的一串原子：

```json
{"id": "buy_ticket", "label": "一口价买票", "steps": [
   {"op": "charge", "eb": 50},
   {"op": "grant",  "item": "演出票"}]}
```

**买票和进场必须分开**。买票只改「我有票了」；进场是另一个 Action：

```json
{"id": "enter_opera", "when": {"item": "演出票"}, "steps": [
   {"op": "take", "item": "演出票"},
   {"op": "move", "to": "a1.opera"}]}
```

于是「先付钱、待会进场」是**数据表达**，不需要 DM 临场拼。

**when 只有六种**（决定这一步要不要执行，不是跳转）：

`always`（默认）/ `success` / `fail` / `first`（整队只有一人做）/
`battle_won` / `battle_lost`

**事务**：整个 `perform` 在一个事务里，任何一步失败全部回滚——
不会出现「钱扣了但没换成场」。编译期还会拦「`move` 出现在 `charge` 之前」
（玩家会先到位、再扣代价，失败时卡在新场景）。

**旧剧本自动编译**：`exits` / `gates` / `clues` / `encounter` 各编译成 Action，
所以 `opera.json` 一行不用改就有了可执行能力。

**临场动作**：剧本没写的（买罐汽水、踢翻桌子），DM 直接调原子并自己叙述后果，
不生成临时 Action。判据：剧本写了的走 Action，没写的走原子——买票属于前者。

## 物品：装备 / 剧情道具 / 消耗品

`gear` 不再是字符串列表，而是 `Item`——**剧本给的道具终于有地方放了**：

| kind | 含义 | 例子 |
|---|---|---|
| `gear` | 买来的普通装备 | 中型手枪、皮衣 |
| `quest` | 剧情道具，**用过仍在身上** | 演出票、木桩、人头 |
| `consumable` | 用一次就没 | 弹药、汽水 |

落库兼容旧数据：普通装备仍是裸字符串，只有带类型/来源的才存成 dict。

```python
card.has_item("木桩")
card.find_item("木桩").kind     # "quest"
```

Action 里的 `grant` **默认就是 `quest`**——剧本给的东西和商店买的东西不一样。

## 条件化奖励

原文写「如果…就…」的地方，现在能用数据表达：

```json
"rewards": {"eb": 1500, "ip": 4,
            "favor": {"holder": "org:爱乐吸血鬼",
                      "when": {"any": [{"flag": "done:staked"},
                                       {"flag": "done:beheaded"}]}}}
```

- `rewards.when` —— 整份奖励的条件（不满足一条都不发）
- `favor.when` —— **只**约束人情那份（钱照付，青睐看条件）

对应原文 `:105`：钱无条件付，但「如果记得把木桩钉进鲁思文的心脏…才赢得青睐」。

## 四个概念

**Edge** —— 一条出边。四种来源，**只有这四种，不允许新增动词**：

| kind | 来源 | 例子 |
|---|---|---|
| `exit` | 玩家说去哪 | `a1.students -> a1.opera` |
| `gate` | 技能闸门（可带检定） | `撬锁 DV9 -> a2.office_in` |
| `battle` | 战斗结果（退出条件归剧情层） | `battle:won -> a3.church` |
| `ending` | 收尾 | `-> ending:opera_dead` |

**Condition** —— 边的触发条件，只读世界状态。通用谓词就这几个：

```
always / never / skill:X / clue:X / flag:X / talked:npc[/topic]
battle:won|lost|withdraw
all([...]) / any([...]) / not(...)
```

**Gate 跟掷骰不绑定**：`check` 为空就是「直接过去」。
「一口价买票」和「花钱买票 + 交易检定」是同一条边的两种数据写法，不是两个方法。

**校验在编译期做**：悬空引用、死路、不可达、重复 id 一律抛 `StoryError`。
旧 `core.story` 不校验，玩家会静默卡死——这是重写的主要动机之一。

## 红线

**不许为具体动作写方法。** 没有 `buy_ticket()`、没有 `pick_lock()`。
「买票」是数据：`{"to": "a1.opera", "cost": {"eb": 50}}`。
`tests/test_engine_story.py::test_no_verb_specific_api` 守着这条红线。

## 怎么加一种新剧情写法

1. 先问：能不能用现成的 kind + condition 表达？
2. 不能 → 加**一个通用条件谓词**（`conditions.py`），不是加一个动作方法。
3. 加谓词要配：`parse_when` 的写法、`describe()`、单测、本文件的一句话说明。
