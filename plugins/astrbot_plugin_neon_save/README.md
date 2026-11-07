# 本候选新增：自然认领与轻度DIY

NEXUS0.3.0-rc1配套存档0.2.5-rc1。自动审核默认关闭，只限角色认领和纯DIY。当前仅隔离验收，HOME未开放。请先阅读 [ENROLLMENT_GUIDE.md](ENROLLMENT_GUIDE.md) 的安全范围、配套升级和回滚限制。以下保留旧功能说明，旧管理员审批仍适用于非认领/DIY变更。

# 霓虹余烬 V3.2 · v0.2.4-rc1

目标环境：AstrBot Desktop v4.29.4 / macOS Apple Silicon / QQ官方机器人。**已完成本地测试，尚未在真实 AstrBot、Mac Desktop 和 QQ 群中安装验收。** aiocqhttp 同样尚未实测。

## 已实现

- 10张原文预设模板；属性、22项常见技能（未训练为0）、HP/RAM、个人资金、装备和义体一次写入。只为认领角色建档，仍为 pending，不自动开场；团队现金 null 不变，不发委托预付款。
- 第37章追赶XP：活跃角色累计XP中位数减6、最低0；无其他活跃角色为0。若中位数产生半点XP，因原文未规定取整而拒绝初始化，交由团体裁定。
- AI只能读取或创建不可变待审核提案，不能同意、掷骰或提交。相关玩家用本人平台账号确认摘要后，AstrBot管理员提交；管理员不能替别人同意。管理员也是玩家时，可用其本人账号同意自己的角色。
- 提案绑定群、旧 revision、规则版本3.2、完整结果 SHA256 和30分钟期限。版本变化后必须重建提案并重新同意。
- 每次提交使用同一个 BEGIN IMMEDIATE 事务，包含角色/世界修改、revision+1、旧版完整前后审计、新版提交前镜像、证据消费和提案状态。任何一项失败全部回滚。批次最多10项，支持多人同意后一次提交。
- 插件使用系统安全随机源生成骰点；保存群、角色、用途、时间及发起人。伤害证据限一小时、只能消费一次；档案首次1d10结果固定，重复请求返回同一结果。
- 任务结算以第25章覆盖第09章：基础+3XP，个人重大目标额外+2XP，关键剧情改变阵营格局额外+1XP，单任务最多6XP；当前XP和累计XP同步增加，符合有效参与且未满3/3时档案+1，每任务不可重复。暂离角色禁止资源、伤害和任务结算。
- 档案达到3/3仍“尚未生成”；公开异象提案经本人同意和管理员提交后，才允许本人掷揭晓骰；最终事实、线索和奖励再次由本人确认后提交。允许不确认或拒绝，保持现状，无自动惩罚。
- 单角色历史恢复、JSON导出、SQLite在线完整备份、事件审计。角色恢复保留最新revision并追加审计，禁止跨群及更换归属。

## 安装升级：保护现有 revision=6

**首次升级请先阅读同包 UPGRADE_ROLLBACK.md。不要卸载插件，不要执行 /neoninit，不要删除或替换 plugin_data。**

ZIP内是单层 `astrbot_plugin_neon_save/`，其下直接有 main.py、store.py、safe.py、templates.json、metadata.yaml 等。已有用户推荐停机备份后仅替换插件代码；全新隔离测试环境可用插件管理页上传ZIP。

持久化位置沿用旧版 `get_astrbot_plugin_data_path()/astrbot_plugin_neon_save/neon_saves.sqlite3`。不会创建另一个默认世界或复制示例存档。首次加载只新增 `neon_v020_*` 辅助表，原 saves/audit 结构、所有快照和 revision 原样保留；没有 destructive migration。存档仍使用 schema_version=1 / rules_version=3.2。

为使旧版结构可读，REP、ARM、CAP、背景与专属能力等原schema没有的字段，保存在角色 equipment 列表内的 `{"neon_v020":{...}}` 元数据中；HP、RAM、现金、XP、技能等仍为原顶层字段。元数据中的hp/cash是**初始模板基线**，当前资源应读角色顶层值。义体容量数字是占用，不是护甲；01 ARM3、06 ARM2，其余ARM0。08车辆登记为团队资产，08为操作者，不给其他角色重复发车。

## 最短操作流程

在 QQ 中实际 @机器人。AstrBot管理员身份由 event.is_admin() 核验，不以QQ昵称或自称管理员判断。

1. `/neonstatus`：升级后应仍为 revision=6、0名角色、第一幕 not_started、团队现金未确认。
2. `/neontemplate 03`：查询模板，不创建角色。
3. 玩家本人 `/neonclaim 6 03 昵称`：生成完整角色卡待审核提案，游戏存档仍revision=6。
4. 核对机器人返回的ID和64位摘要。玩家本人 `/neonconsent 提案ID 摘要`。
5. 管理员 `/neonapprove 提案ID 摘要`：一次写入完整卡，revision=7，角色pending；不是直接进战斗。
6. 入场安全节点经审核提案将status改为active。测试群完成流程后，正式群再开展游戏。

拒绝：`/neonreject 提案ID 摘要`，仅相关玩家可拒绝。忽略提案30分钟后过期。
查询：`/neonview 提案ID 03` 或 `world`。批次务必逐个角色查看结果后再确认，消息被QQ截断时不要确认。

`/neonpropose 当前revision base64url编码的JSON` 生成其他操作提案。AI工具 `neon_propose_change(revision,proposal_json)` 接收普通JSON字符串，返回同样的待审核预览；它不会修改游戏快照。也可让AI按照以下格式提出提案。全程不需要AI调用批准命令。

### 第25章额外XP审核

mission提案可选 `personal_major_goal` 和 `faction_shift`，必须为布尔值，省略为false。为true时分别必须提供非空 `personal_goal_evidence` 或 `faction_shift_evidence`（公开证据说明，最多500字）。本人同意和管理员审批仍不可省略，证据真实性由双方核对。程序固定计算3/5/4/6XP，不接受xp或xp_award等自定义奖励字段。同一角色同一任务不能重复结算，也不能以补填奖励条件再次领取XP。

示例：`{"kind":"mission","cid":"03","task":"mission-001","meaningful_action":"公开有效行动记录","personal_major_goal":true,"personal_goal_evidence":"目标完成消息引用","faction_shift":true,"faction_shift_evidence":"阵营格局变化消息引用"}`，审核后共6XP。

本次升级不追补历史XP，不修改既有任务记录。v0.2.0遗留的+2XP待审核提案会因结果预览不一致而拒绝提交，须重新提出并重新确认。已结算任务若需纠正，另行核对历史审计后走明确的管理员资源更正提案，不能重复mission。

### 提案格式（JSON）

```json
{"kind":"initialize","cid":"03","user":"玩家平台ID","nickname":"扳手"}
{"kind":"status","cid":"03","value":"active"}
{"kind":"resource","cid":"03","field":"cash","delta":100,"reason":"管理员核对实际到账"}
{"kind":"world","field":"current_task_id","value":"mission-001","reason":"正式发布任务"}
{"kind":"mission","cid":"03","task":"mission-001","meaningful_action":"公开工程检定，消息引用xxx；已完成且幸存"}
{"kind":"omen","cid":"03","omen":"接口出现一帧旧地图，仅叙事，无数值效果；询问是否继续揭晓"}
{"kind":"reveal","cid":"03","roll":"插件揭晓骰ID","fact":"经本人接受的待调查记录","clue":"下一条公共线索","reward":"线索共鸣"}
{"kind":"restore_role","cid":"03","source_revision":7}
{"kind":"batch","operations":[{"kind":"resource","cid":"01","field":"cash","delta":100,"reason":"已到账"},{"kind":"resource","cid":"02","field":"cash","delta":100,"reason":"已到账"}]}
```

资源白名单：cash、ram、xp、stress；delta必须为整数，结果不得为负，RAM不得超过上限，压力0—6。XP正向增加同步累计XP，消耗XP不扣累计XP。此入口用于管理员已核定的结算，不自动解释购买或训练。
世界白名单：current_task_id、scene_status、scene、location、action_window、recap、alert_level。需当前所有active玩家同意。status支持pending/active/away/retired。回归使用active提案，管理员确认是安全节点。

伤害流程：先由本人或管理员 `/neonroll 03 damage 2 6`，公开2d6，取得ID；提交：

```json
{"kind":"damage","cid":"03","roll":"骰点ID","bonus":2,"arm":0,"penetrating":false,"reason":"命中证据xxx；武器2d6+2；护甲已核对"}
```

插件计算 `max(0,骰点和+bonus−ARM)`，穿透忽略ARM，HP最低0；不自动判死。bonus范围0—20、ARM0—8，**命中、武器骰型、护甲适用性、穿透属性由玩家及管理员对公开证据审核，插件不具备完整战斗裁判能力**。

档案：先完成omen提案的本人同意和管理员提交，再由本人 `/neonroll 03 reveal 1 10`；重复请求固定首次结果。最终reveal提案再确认背景事实、线索、奖励。奖励仅允许“线索共鸣”“行动直觉”“义体升级线索”，奖励次数初始0，不免费授予义体。骰点类别按第40章1—10映射，不能另填类别或改骰。`check`用途骰可公开展示，不用于当前自动结算。

### 备份与审计

- `/neonhistory`：管理员查看原版审计摘要；每次提交完整before_json/after_json保留在 audit 表。
- `/neonaudit`：管理员查看近期提案、玩家同意/拒绝、骰点、提交事件。提交前完整镜像在 neon_v020_events 的 preimage 事件中。
- `/neonexport`：沿用v1 JSON导出，保存本群快照与校验值，**不含辅助授权表及其他群**。
- `/neonbackup`：管理员生成整个数据库的一致性SQLite备份（含所有群、审计、提案和证据），位于数据目录 database_backups/，不将文件发到群。
- 恢复某角色：restore_role提案，当前角色本人同意、管理员提交；只恢复相同归属且原审计中存在的角色。误删到角色已不存在的情况及整库灾难恢复见离线恢复指南，不开放无人审核的整库覆盖指令。

旧命令 neonjoin/neonset/neonpatch/neondossier/neonreveal/neonrestore 已明确停用，避免绕过新授权链；旧底层NeonStore仍保留用于兼容与回归测试，不注册为LLM写工具。新群管理员neoninit仍可初始化，已有群重复初始化拒绝。

## 测试与明确限制

见 TEST_REPORT.md。本地测试仅使用临时数据库及模拟AstrBot API。真实Mac Desktop插件加载、QQ长消息发送、断网后发送重试、DeepSeek工具联动尚未验证。不能宣称已完成真实环境端到端测试。

本版不自动接收第三方D&D骰子插件结果、聊天自报骰点或实体骰；这些不能冒充可信数据库证据。本版骰子在存入库后发送群消息，**没有QQ服务端投递回执验证**；如消息发送失败，请先通过返回同一揭晓骰或审计核对并公开，再审核，不得声称消息已成功送达。

管理员仍须核对任务完成、幸存、有效行动、命中、装备条件、安全边界和安全入场节点，不能仅凭AI提案自动批准。提案不能自证现实事件真实；玩家不回复绝不是同意。全套战斗/死亡标记、治疗物品消耗、技能训练、义体安装、奖励按任务重置、团队资产投票和自建角色不在本版自动裁判范围内。当前维持旧schema最多10名非retired角色的保守限制（away也占席位）；不自动删除旧角色、不代操缺席者。恢复历史角色可能撤销该角色后续资源变化，须核对预览。

## 本地复现测试

解压后在ZIP根目录运行：

```sh
PYTHONPATH=. python3 -m unittest discover -s astrbot_plugin_neon_save/tests -v
```

不需要安装AstrBot即可运行；标准库依赖，无新增第三方依赖。不要指向正式数据目录执行测试。

API参考（核对v4.29.4源码）：
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/filter/permission.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_handler.py

规则来源：用户CP2077_V32.docx，第03/04/06/07/09/10/11章以及36—44章。优先顺序为玩家同意的边界→硬规则（36—44覆盖冲突旧设定）→剧本→临场叙事。

## v0.2.4-rc1 安全检查
状态变为active时必须附entry：{"safe_node":true,"in_combat":false,"check_locked":false,"scene":"公开安全节点","team":"A"}。本人同意、管理员批准后记录入场时间。
mission必须额外附completed:true、survived:true、actually_present:true、completion_evidence非空公开证据。未开场或暂停中拒绝结算；有效行动不得仅为空白。声明真实性需本人及管理员核对。旧待审核提案缺少字段将拒绝提交，须重建并重新同意。

## v0.2.4-rc1
修复入场提案预览与批准时间不一致。提交复用已绑定摘要的入场时间；支持单角色与批次。旧批次若多个新入场时间不一致则拒绝并要求重建。

## 0.2.4-rc1安全闸门
配套NEXUS0.2.8-rc1，新增暂停闸门和提案控制版本校验。暂停时禁止骰点、提案、授权和结算；恢复后旧提案须重建。原saves、audit、store.py、templates.json及规则计算不改。启动时继承已有NEXUS显式暂停。不要单独降级绕过闸门。安全状态见/neonstatus。


## 2026-11-02 认领路由修复

NEXUS0.2.9-rc2配套存档0.2.4-rc2：未开放群的认领/确认明确拦截，不再进入普通模型；扩展明确中文角色引用；模型提案工具禁止角色初始化。安全引擎不变，03模板CAP23、已占5、剩余18。不启用HOME、不自动开场。真实认领验收仍需未绑定账号及本人确认。
