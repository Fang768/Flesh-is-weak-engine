"""Nexus Bot —— NoneBot2 入口。

运行方式：
    .venv\\Scripts\\python.exe bot.py
或：
    .venv\\Scripts\\nb.exe run
"""

import os as _os

# 本机可能开着系统代理（127.0.0.1:26561 之类），它会 MITM TLS，
# 导致 QQ 网关与 DeepSeek 的证书校验失败。机器人直连，不走代理。
for _k in ("NO_PROXY", "no_proxy"):
    _os.environ[_k] = "*"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    _os.environ.pop(_k, None)
import nonebot

from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
driver.register_adapter(QQAdapter)

# QQ 官方通道（plugins/qq_gateway）已经覆盖了闸门/建卡/回合，且命令名与下面三个
# 旧 OneBot 插件重复（NoneBot 会 warn "Duplicated prefix rule"）。NapCat 那条线
# 现在只是备用，所以默认不加载旧插件；需要回退时把 SKIP 清空即可。
SKIP = {"gate", "character", "play"}

import pathlib

for _dir in sorted(pathlib.Path("plugins").iterdir()):
    if _dir.is_dir() and (_dir / "__init__.py").exists() and _dir.name not in SKIP:
        nonebot.load_plugin(f"plugins.{_dir.name}")

# 一次性启动任务（幂等）：给存量卡补发 RED 起始资金 500 EB。
# 不补的话，2026-11-10 之前建的卡装不起任何义体（见 core/runtime.py）。
from core import runtime as _runtime

_runtime.run_startup_tasks()

if __name__ == "__main__":
    nonebot.run()
