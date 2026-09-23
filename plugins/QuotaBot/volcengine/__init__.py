"""火山方舟（Volcengine）额度查询子包。

- ``signer``：Volcengine Signature V4（纯标准库）
- ``client``：控制面 OpenAPI 调用（GetCodingPlanUsage）
- ``quota``：响应解析 → 统一额度窗口
- ``provider``：按配置账号查询并组装额度板

本包 import 安全：不依赖 NoneBot 运行时副作用。
"""

from __future__ import annotations

from .client import VolcengineError, query_coding_plan_usage
from .provider import CHANNEL, collect_board
from .quota import account_from_usage, parse_coding_plan_usage
from .signer import sign_request

__all__ = [
    "CHANNEL",
    "VolcengineError",
    "account_from_usage",
    "collect_board",
    "parse_coding_plan_usage",
    "query_coding_plan_usage",
    "sign_request",
]
