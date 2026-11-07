"""群级异步锁。

同一个人手快连发两条、或者两个人几乎同时说话时，回合状态会被算两遍
（场景切两次、事件记两份、骰子掷两回）。这里按「一个群一把锁」串行化。

用法::

    async with group_lock(gid):
        ...处理回合...

注意：锁只在本进程内有效。多进程部署要换成 Redis 之类，但现在是单进程。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

_locks: dict[str, asyncio.Lock] = {}
_guard = asyncio.Lock()


async def _lock_for(key: str) -> asyncio.Lock:
    async with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


@asynccontextmanager
async def group_lock(key: str):
    lock = await _lock_for(key)
    async with lock:
        yield
