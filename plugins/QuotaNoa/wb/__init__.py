"""WorkBuddy2API 额度查询子包。

- ``client``：``GET /v1/quota``（Bearer 鉴权）
- ``quota``：响应解析 → 统一额度窗口（聚合积分 + 套餐数）
- ``provider``：按配置网关并发查询并组装额度板

本包 import 安全：不依赖 NoneBot 运行时副作用。
"""

from __future__ import annotations

from .client import WorkbuddyError, fetch_quota, healthz, login, reset_sessions
from .provider import CHANNEL, collect_board
from .quota import CREDITS_WINDOW_ID, CREDITS_WINDOW_LABEL, parse_quota_accounts

__all__ = [
    "CHANNEL",
    "CREDITS_WINDOW_ID",
    "CREDITS_WINDOW_LABEL",
    "WorkbuddyError",
    "collect_board",
    "fetch_quota",
    "healthz",
    "login",
    "parse_quota_accounts",
    "reset_sessions",
]
