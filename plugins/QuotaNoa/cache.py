"""渠道级查询结果 TTL 缓存（供火山 / WorkBuddy 等本地渠道共用）。

CPA 额度缓存在 ``cpa/quota.py``（按实例分桶）；本模块只服务不依赖 CPA 实例的
本地渠道。键形如 ``"<channel>:<scope>"``，TTL 由配置 ``refreshcache`` 决定。
``ttl <= 0`` 视为不缓存。
"""

from __future__ import annotations

import time
from typing import Any


class TtlCache:
    """极简 monotonic TTL 缓存。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            self._entries.pop(key, None)
            return
        self._entries[key] = (time.monotonic() + ttl, value)

    def clear(self, prefix: str | None = None) -> None:
        if prefix is None:
            self._entries.clear()
            return
        for key in [key for key in self._entries if key.startswith(prefix)]:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


def board_key(channel: str, scope: str = "") -> str:
    return f"{channel}:{scope}"


#: 本地渠道额度板共享缓存。
boards = TtlCache()


def clear_boards(prefix: str | None = None) -> None:
    boards.clear(prefix)
