# Nexus Bot（NoneBot2 + OneBot v11 跑团机器人）

基于 `plugins/astrbot_plugin_nexus_gm` / `neon_save` / `dnd_dice` 三个 AstrBot 插件的可复用部分，重建到 NoneBot2 + OneBot v11 栈上。

## 架构（当前实际运行）

```
QQ 9.9.37-54395（NCD 独立 QQ，NapCatQQ Desktop 管理）
   └─ SnowLuma 2.4.0 注入（协议端，带 QQ GUI）
        └─ OneBot v11 反向 WS  ws://127.0.0.1:8080/onebot/v11/ws
             └─ NoneBot2 2.5.0（本目录，插件在 plugins/）
```

- 协议端**不是** NapCat Shell，而是 **SnowLuma 2.4.0**：本机 QQ 为 `9.9.37-54395`，不在 NapCat 4.18.42 内置版本表（最高 `9.9.37-54276`）内，NapCat 注入后 QQ 主线程挂在 `CREATE_SUSPENDED`（1 线程 / 37MB），`loadNapCat.js` 从不执行。
- 协议端的安装、启停、日志、QQ 版本都在 **NapCatQQ Desktop** 里管理，不要手动跑 bat。

## 目录

```
nexus-bot/
├─ bot.py                  NoneBot2 入口
├─ .env                    运行配置（HOST/PORT/DRIVER/指令前缀）
├─ plugins/nexus_ping/     连通性自检：/ping -> pong
├─ plugins/cp_rules/       跑团规则：/r 骰点、/dv 难度表、/check 检定
├─ cyberpunk/              V3.0 规则引擎（dice/stats/checks/damage/characters/commands）
├─ tests/                  41 个单元测试（pytest）
├─ docs/复用分析.md        AstrBot 插件复用分析（任务交付物）
├─ scripts/
│  ├─ cdp.py               通过 CDP 驱动 NapCatQQ Desktop（js/text/click/tap/shot）
│  ├─ kill_stuck.py        TerminateProcess 强杀挂起 QQ
│  └─ screenshot.ps1       全屏截图
└─ start-bot.bat / start-napcat.bat
```

## 端口

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| NoneBot2 | http://127.0.0.1:8080 | 反向 WS 路径 `/onebot/v11/ws` |
| SnowLuma WebUI | http://127.0.0.1:5099 | 初始密码见 SnowLuma 启动日志，**尚未改密** |
| SnowLuma OneBot HTTP | 0.0.0.0:3000 | 无 token，仅测试期使用，**建议关闭** |
| SnowLuma OneBot WS-Server | 0.0.0.0:3001 | 无 token，仅测试期使用，**建议关闭** |
| Desktop 调试端口 | 127.0.0.1:9222 | 仅配合 `launch_desktop_debug.bat` 使用 |
| NapCatQQ Desktop | — | GUI 程序，无端口 |

## 启动顺序

1. `start-napcat.bat` —— 打开 NapCatQQ Desktop（或直接用桌面图标）。
2. 在 Desktop「Bot 实例」里启动实例 `nexus`（底座 SnowLuma，QQ 3916495355）。
3. `start-bot.bat` —— 起 NoneBot2（监听 8080）；SnowLuma 会自动反向连上来，日志出现 `OneBot V11 | Bot 3916495355 connected` 即成功。

顺序反过来也行：SnowLuma 的 ws 客户端每 5 秒重连一次。

## 验证

在 QQ 里发 `/ping`，回复 `pong | 会话 <session_id>`（私聊为 QQ 号，群聊为 `group_<群号>_<QQ号>`）。

- 私聊已实测通过（2026-11-06 00:28，QQ 1376009859）。
- 群 `1121562905 测试跑团`：机器人已被邀请入群，出站消息已验证（HTTP API `send_group_msg` 返回 `retcode:0`）。

## 规则引擎

`cyberpunk/` 是纯标准库的 Cyberpunk V3.0 规则实现，随机性全部通过 `Roller` 注入，
所以测试可以完全确定性。

| 模块 | 内容 |
| --- | --- |
| `dice.py` | `RandomRoller(seed)` / `ScriptedRoller([...])` |
| `stats.py` | 十属性校验 + 12 项派生属性（LUCK/HUM/HITS/STUN/SD…） |
| `checks.py` | `1D10+属性+技能 ≥ DV`、DV 阶梯 10/14/18/22/26/30、自然 10/1、修正表 |
| `damage.py` | 命中部位倍率（头×3/要害×2）、AP 与锐器半 SP、挡伤 SP−1、死亡豁免 |
| `characters.py` | 角色卡：有效属性/技能（吃到 Perk 加成）、伤害落池、休息恢复 |
| `commands.py` | 命令层纯逻辑：骰点解析、技能→属性表 |

跑测试：`.venv/Scripts/python.exe -m pytest tests -q`

## 已安装版本

- Python 3.12（`.venv`，用 uv + 阿里云镜像安装）
- nonebot2 2.5.0 / nonebot-adapter-onebot 2.4.6 / fastapi 驱动
- SnowLuma 2.4.0（`C:\ProgramData\NapCatQQ Desktop\components\SnowLuma`）
- 独立 QQ 9.9.37-54395（`C:\ProgramData\NapCatQQ Desktop\components\QQ`）

## 已知改动（Desktop 升级会覆盖）

`C:\ProgramData\NapCatQQ Desktop\components\SnowLuma\launcher.bat` 原来调用 PATH 里的系统 `node`（v26.3.0，不满足 `package.json` 要求的 `^24.22.1 || >=26.12.1`），已改为调用组件自带的 `node.exe`；原件备份为 `launcher.bat.bak`。

## 简易版（RED Easy Mode）入口

| 命令 | 作用 |
|---|---|
| `/模板` | 列出 6 张预置角色卡（肆拾/搬运工/火炬/赤尾/24-7/清道夫） |
| `/认领 火炬` | 领走一张，写入 `easy_cards`（同一人重复认领会覆盖） |
| `/check 手枪 dv=15` | 有简易版卡时走 `1d10+属性+技能 > DV`；没有则走 V3 卡或演示卡 |

战斗增补（命中部位/全自动/压制/AP/锐器/EV/简化 IP）见 `rules/cpr_easy/docs/11-战斗增补（V3移植）.md`。

### 专长与天赋（方案 B）

| 命令 | 作用 |
|---|---|
| `/专长` | 列出 10 项专长 + 24 项天赋的中文名 |
| `/专长 权威` | 查一条的说明、效果与花费（支持中英文与片段） |

建卡时可带 `perks=[...]`（英文键或中文名都行），会显示在角色卡末行；`Beautiful or Handsome`、`Perfect Pitch`、`Lightning Calculator` 会自动加到对应技能上。

### 存档、事件与成长

| 命令 | 权限 | 作用 |
|---|---|---|
| `/档案` | 所有人 | 看自己的卡 + 生命/IP/可用人情 |
| `/记录 [n]` | 所有人 | 最近 n 条事件（默认 5，最多 20） |
| `/奖励 [n]` | DM | 给本群所有 V3 卡各发 n 点 IP（默认 4） |
| `/提升 手枪` | 所有人 | 用 IP 升 1 级技能（花费 = 当前等级 × 2） |

- `events` 表记录 开团/暂停/继续/结束/场景/掷骰/受伤/奖励/笔记；`/继续` 时会带出「上次停在……」
- 角色状态（IP、已用掉的人情、当前生命）存在 `characters.state` JSON 里，跟卡面数值分开
- 旧库启动时自动补列（`core/db.py::_ensure_columns`），不需要手工迁移
