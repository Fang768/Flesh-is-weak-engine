# nexus-bot 工作规则

跑团机器人（Cyberpunk RED）的项目规则。改这里的代码前先读 `docs/设计约束.md`。

## 硬性要求

1. **小步交付 + 自测**：每完成一个小模块，先自己测一遍再报告。
   - `./.venv/Scripts/python.exe -m pytest tests -q` 必须全绿
   - `./.venv/Scripts/python.exe scripts/simulate.py` 必须全部场景通过
   - 新增玩法要在 `scripts/simulate.py` 里补对应场景
   - 多测不同情况与边界：正常路径、空输入、超长输入、权限不足、重复消息、
     并发、模型超时、没卡/没团/没战斗、过期状态
   - 碰到真实通道（QQ/模型）用 `--real` 真跑一遍
2. **报告三件事**：改了什么、测了什么、还剩什么没测。
3. 改代码时遵守 `docs/设计约束.md` 的六条（窗口不关、一人一卡、破坏性操作两段确认、
   heredoc 不写反斜杠、系统指令与戏内行动分开、小步自测）。
4. **实现剧本/规则前先索引原文**：加新玩法或规则前，先用关键词检索
   `story/*.json`、`rules/red/data/playbook_*.txt`、`rules/red_core/raw/full.txt`
   的相关段落，按原文口径确认方案，并在方案/代码注释里引用 `文件:行号`。
   规则书没写明的，标为「约定」并在 docs 里写明依据；不要凭印象自定义实现。

## 版本管理（用户 2026-11-08 定）

- **改完不要自动 `git commit`，更不要 `git push`**。改完只落盘，然后继续下一件事。
- **等主人当天收工时**（他说「记录一下」「今天到这」「push 吧」这类），再提醒他一次，
  得到确认后统一 `git commit` + `git push`。
- 中途需要回滚就 `git checkout -- <file>` / `git stash`，别为了「安全」建 `.bak`：
  git 本身就是版本管理（用户 2026-11-08 明确要求不要再建备份文件）。
- 一次收工提交里可以包含多个已完成的小模块；commit message 按模块分段列要点。

## 目录速查

- `core/`：平台无关业务（gate_flow 闸门、card_flow 建卡、turn_flow 回合、combat 战斗、clues 线索、npc_flow NPC、events 事件、growth 成长）
- `cyberpunk/`：规则引擎（dice/stats/checks/damage/characters/gear/cyber/perks/names）
- `plugins/`：`qq_gateway` 是唯一在线入口（官方 QQ Bot）；`gate`/`character`/`play` 是 NapCat 线的备用插件，不加载
- `rules/red/`：核心书抽出来的数据（skills/templates）
- `story/`：剧本 JSON，`demo.json` 是占位剧本
- `scripts/`：simulate.py 本地模拟器、各类一次性补丁脚本
- `docs/`：进度.md、待办缺口.md、设计约束.md
