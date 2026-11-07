"""进程内共享的运行时对象。

为什么要单独一个模块：``plugins/gate`` 和 ``plugins/character`` 各自 import 会拿到
**两份不同的** ``AttentionBook`` / ``Gate``，窗口状态就对不上了（曾经真的这样错过）。
所有插件都从这里取同一份实例。
"""

from __future__ import annotations

from core import db
from core.attention import AttentionBook
from core.session import Gate

GATE = Gate()
ATTENTION = AttentionBook()
CONN = db.connect()
