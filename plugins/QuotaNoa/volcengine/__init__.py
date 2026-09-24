"""火山方舟（Volcengine）额度查询子包。

- ``signer``：Volcengine Signature V4（纯标准库）
- ``client``：控制面 OpenAPI 调用（GetCodingPlanUsage / GetPersonalPlan）
- ``quota``：响应解析 → 统一额度窗口
- ``provider``：按配置账号查询并组装额度板

本包 import 安全：不依赖 NoneBot 运行时副作用。
"""

from __future__ import annotations

from .client import (
    VolcengineError,
    query_afp_usage,
    query_coding_plan_usage,
    query_personal_plan,
)
from .provider import CHANNEL, collect_board
from .quota import (
    accounts_from_usage,
    format_expiry_label,
    parse_agent_plan_usage,
    parse_coding_plan_usage,
    parse_personal_plan,
    parse_plan,
)
from .signer import sign_request

__all__ = [
    "CHANNEL",
    "VolcengineError",
    "accounts_from_usage",
    "collect_board",
    "format_expiry_label",
    "parse_agent_plan_usage",
    "parse_coding_plan_usage",
    "parse_personal_plan",
    "parse_plan",
    "query_afp_usage",
    "query_coding_plan_usage",
    "query_personal_plan",
    "sign_request",
]
