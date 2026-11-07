# NEXUS《霓虹余烬》源码

本包从实际安装插件只读提取；不是完整机器人迁移包，不含真实群数据或服务凭据。

## 实际版本与目录
- plugins/astrbot_plugin_nexus_gm：0.4.1-dev3，主持、DeepSeek语义决策、现场对话、动态第一幕、人数适配、自然认领、DIY、暂停恢复。
- plugins/astrbot_plugin_neon_save：0.2.7-rc1，SQLite事务、唯一归属、角色模板、本人同意、按群自动角色审核、剧情自动记录、审计。建表及兼容升级代码在store.py、safe.py、enrollment.py、body_privacy.py；没有独立迁移文件的功能不伪造迁移文件。
- plugins/astrbot_plugin_dnd_dice：当前安装的第三方骰点插件，原许可证及元数据保留。动态引擎的可信骰点实际调用存档插件safe.py的roll，不依赖模型造数。
- resources：TRPG_GM实际人格提示词及工具选择。程序提示词另在controller.py、semantic.py、conversation.py、voice.py等源码内。
- 两个自定义插件的tests为现有开发回归测试，不含游戏数据。

## 必要资源与实际边界
角色模板templates.json、外观参考、公开规则public_rules.json、公开词条、地点资料及第一幕动态大纲dynamic/act1.json均按原路径保留。源码自带规则摘录不等于完整V3.2规则书；外部知识库和完整战役原稿不属于插件源码，本包未导出知识库数据库。
NPC口吻、动机及公开状态逻辑已包含；没有发现独立完整NPC恋爱系统或九人恋爱档案库的实际安装代码，不能据聊天中的设计要求声称已经实现。当前仅含调情/拒绝的叙事约束和现有NPC状态。完整战斗裁判尚未实现。第一幕仍有固定可信机械操作，不能视为任意自由行动裁判。0.4.1-dev3是开发版，未完成2—3人连续真实QQ自由试玩验收。

## 安装到另一个AstrBot
1. 使用Python 3.12及AstrBot 4.29.4兼容实例，将plugins内各插件目录复制到目标实例data/plugins，目录名保持不变；先加载存档插件，再加载GM（缺依赖时重启核对日志）。
2. 自行在目标AstrBot配置DeepSeek模型与目标平台账号。本包不提供任何账号、API密钥、真实群号或用户绑定。
3. 如需同样人格，在目标实例创建TRPG_GM，使用resources文本，按其可用工具重选工具；不要将工具选择当成权限证明。
4. 插件按群白名单与授权默认不开放；在目标实例自行配置新群授权。原群白名单、自动审核策略、成年确认和开场授权没有迁移。需要这些功能时，查main.py、simple_open.py及enrollment.py中的对应JSON格式，在新实例明确授权；绝不可照搬他人的确认。
5. SQLite会由程序创建。不要复制真实数据库，不会自动开第一幕。所有默认权限和暂停控制仍按源码执行。

## 自动测试
在本目录执行（PYTHONPATH指向plugins）：
PYTHONPATH=plugins python -m unittest discover -s plugins/astrbot_plugin_nexus_gm/tests
PYTHONPATH=plugins python -m unittest discover -s plugins/astrbot_plugin_neon_save/tests -t plugins
第三方骰点测试：python -m pytest plugins/astrbot_plugin_dnd_dice/tests
实际打包测试结果见TEST_RESULTS.md。测试结果不替代真实QQ试玩。

## 安全与完整性
无QQ配置、API密钥、存档、聊天、审计、缓存或运行时授权文件。MANIFEST.json记录每个交付文件的SHA256；SOURCE_INTEGRITY.json证明实际安装文件与导出文件逐文件相同（测试、README等补充文件另列）。不修改正在运行的插件或游戏。

## 文档入口（Agent）

后续 AI 接手先读 [Agent/AGENTS.md](Agent/AGENTS.md) 的读写规则，再按 [Agent/PROJECT_INDEX.md](Agent/PROJECT_INDEX.md) 定位文件与章节。当前状态、风险、操作步骤和已确认决策分别在 [NOW.md](Agent/docs/context/NOW.md)、[RISKS.md](Agent/docs/context/RISKS.md)、[RUNBOOK.md](Agent/docs/context/RUNBOOK.md)、[DECISIONS.md](Agent/docs/context/DECISIONS.md)。
