"""把火山 Coding Plan 的 ``Result.QuotaUsage[]`` 解析为统一的额度窗口。

返回结构（实测）：

```json
{"Result": {"Status": "Running", "UpdateTimestamp": 1790132662,
  "QuotaUsage": [
    {"Level": "session", "Percent": 0.0701325, "ResetTimestamp": 1790146415, "Cap": 100},
    {"Level": "weekly",  "Percent": 0.009351,  "ResetTimestamp": 1790524800, "Cap": 100},
    {"Level": "monthly", "Percent": 0.0046755, "ResetTimestamp": 1792771199, "Cap": 100}
  ], "HasReward": false}}
```

- ``Percent`` 是**已用**百分比（0–100），剩余 = 100 - Percent。
- 各窗口无独立降级逻辑；``Status != Running`` 或缺 ``QuotaUsage`` 时返回空窗口，由调用方降级为文字。
"""

from __future__ import annotations

import time
from typing import Any

from ..config import VolcengineAccount
from ..model import AccountQuota, QuotaWindow

#: Level → (窗口 id, 展示名)
_LEVEL_META = {
    "session": ("volc-session", "5h"),
    "weekly": ("volc-week", "周"),
    "monthly": ("volc-month", "月"),
}

_LEVEL_ORDER = ("session", "weekly", "monthly")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reset_label(reset_ts: float | None) -> str:
    if reset_ts is None:
        return "-"
    delta = reset_ts - time.time()
    if delta <= 0:
        return "-"
    days, rem = divmod(int(delta), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    return f"{max(minutes, 1)}m"


def parse_coding_plan_usage(payload: dict[str, Any]) -> tuple[list[QuotaWindow], str]:
    """解析额度响应，返回 (窗口列表, 状态)。窗口方向为"已用"。

    仅当套餐 ``Status == Running`` 时才返回窗口；否则返回空窗口，由调用方降级展示。
    """
    result = payload.get("Result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return [], ""
    status = str(result.get("Status") or "")
    if status and status.lower() != "running":
        # 套餐未生效 / 停机 / 欠费：不展示历史额度，避免误判为正常。
        return [], status
    raw_items = result.get("QuotaUsage")
    if not isinstance(raw_items, list):
        return [], status

    by_level: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        level = str(item.get("Level") or "").strip().lower()
        if level:
            by_level[level] = item

    windows: list[QuotaWindow] = []
    for level in _LEVEL_ORDER:
        item = by_level.get(level)
        if item is None:
            continue
        window_id, label = _LEVEL_META[level]
        used = _num(item.get("Percent"))
        if used is None:
            used = _num(item.get("UsedPercent"))
        reset_ts = _num(item.get("ResetTimestamp"))
        if used is None:
            remaining_percent = None
        else:
            remaining_percent = max(0.0, 100.0 - used)
        windows.append(
            QuotaWindow(
                id=window_id,
                label=label,
                used_percent=used,
                remaining_percent=remaining_percent,
                # 火山的 Cap 是"百分比上限"（固定 100），不是绝对额度；不要写入 limit，
                # 否则 build_board 会把 limit 累加，文字模式会误报"绝对剩余 0/300"。
                reset_label=_reset_label(reset_ts),
                reset_at=reset_ts,
                direction="used",
            )
        )
    return windows, status


def account_from_usage(account: VolcengineAccount, payload: dict[str, Any]) -> AccountQuota:
    """把单个火山账号的额度响应转为统一账号额度对象。"""
    windows, status = parse_coding_plan_usage(payload)
    report = AccountQuota(
        platform="volcengine",
        name=account.name,
        auth_index="",
        plan="",
        status=status or "unknown",
    )
    if not windows:
        report.status = status or "error"
        if status and status.lower() != "running":
            report.error = f"套餐状态：{status}"
        return report
    report.windows = windows
    report.status = "ok" if (status or "running").lower() == "running" else status
    return report
