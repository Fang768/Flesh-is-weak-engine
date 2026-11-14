"""引擎层（新结构，2026-11-14 起）。

三层划分（与旧 core/ 并行，逐步替换）：

1. 数据层
   * engine.story    —— 把剧本 JSON 编译成带条件的场景图（编译期校验）
   * engine.save     —— 存档：进度 / 已触发内容 / 玩家状态 / NPC 记忆
2. 引擎层
   * engine.conditions —— 剧情条件的通用谓词（只读，判定与副作用分开）
   * engine.drama      —— 剧情状态机：给 DM 提示词 + 三个通用动词
   * engine.battle     —— 战斗状态机：从剧情进入，退出条件由剧情给
3. 第三层 LLM 即 DM 本身，不在本包里。

本包不 import core/，避免新旧互相拖拽。
"""
