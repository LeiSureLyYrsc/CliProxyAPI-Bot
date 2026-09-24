"""把 WorkBuddy ``/v1/quota`` 响应解析为统一的账号额度对象。

响应结构（见仓库根 ``wb-for-quotanoa.md`` §3）：

```json
{"provider": "workbuddy",
 "accounts": [
   {"uid": "...", "nickname": "星际猫", "credits": 2013,
    "cooling": false, "disabled": false,
    "quotas": {"个人标准版": {"packageName": "个人标准版", "total": 2000,
                             "used": 800, "remaining": 1200,
                             "resetAt": "2026-10-01 00:00:00", "recurring": true}}}
 ]}
```

聚合口径（§8）：``remaining/total/used = Σ quotas[*].{remaining,total,used}``，
``packages = len(quotas)``。``quotas == null`` 表示本次未探测（冷却/超时/失败），
**不能当作 0**，应标为「未知」。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..model import AccountQuota, QuotaWindow

#: 聚合积分窗口的 id / 展示名。
CREDITS_WINDOW_ID = "wb-credits"
CREDITS_WINDOW_LABEL = "积分"

_NO_PROBE_HINT = "本次未探测（冷却/超时/失败）"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    num = _num(value)
    return int(num) if num is not None else 0


def _parse_reset_at(value: Any) -> float | None:
    """解析上游 ``resetAt``（``"%Y-%m-%d %H:%M:%S"``，服务器本地时区）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
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


def _earliest_recurring_reset(quotas: dict[str, Any]) -> float | None:
    """取周期套餐（``recurring=True``）中最早的**未来**重置时刻。"""
    now = time.time()
    best: float | None = None
    for entry in quotas.values():
        if not isinstance(entry, dict):
            continue
        if not entry.get("recurring"):
            continue
        ts = _parse_reset_at(entry.get("resetAt"))
        if ts is None or ts <= now:
            continue
        if best is None or ts < best:
            best = ts
    return best


def _aggregate(quotas: dict[str, Any]) -> tuple[int, int, int]:
    remaining = total = used = 0
    for entry in quotas.values():
        if not isinstance(entry, dict):
            continue
        remaining += _as_int(entry.get("remaining"))
        total += _as_int(entry.get("total"))
        used += _as_int(entry.get("used"))
    return remaining, total, used


def parse_quota_accounts(payload: dict[str, Any], *, server_name: str) -> list[AccountQuota]:
    """把一个网关的 ``/v1/quota`` 响应转为统一账号额度列表。

    ``server_name`` 写入 ``AccountQuota.instance``，用于多网关分组展示。
    """
    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    reports: list[AccountQuota] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("uid") or "").strip()
        nickname = str(row.get("nickname") or "").strip()
        name = nickname or uid or "(unknown)"
        cooling = bool(row.get("cooling"))
        disabled = bool(row.get("disabled"))
        quotas = row.get("quotas")

        report = AccountQuota(
            platform="workbuddy",
            name=name,
            auth_index=uid,
            instance=server_name,
            cooling=cooling,
            disabled=disabled,
        )

        if quotas is None:
            # 本次未探测：绝不当作 0，按行内 error 标注为「未知」。
            report.status = "cooling" if cooling else "unknown"
            report.error = str(row.get("error") or "").strip() or _NO_PROBE_HINT
            reports.append(report)
            continue

        if not isinstance(quotas, dict):
            report.status = "error"
            report.error = "quotas 字段结构异常。"
            reports.append(report)
            continue

        remaining, total, _used = _aggregate(quotas)
        packages = len(quotas)
        report.plan = f"{packages} 套餐"
        reset_ts = _earliest_recurring_reset(quotas)
        remaining_percent: float | None = None
        if total > 0:
            remaining_percent = max(0.0, min(100.0, remaining / total * 100.0))
        report.windows = [
            QuotaWindow(
                id=CREDITS_WINDOW_ID,
                label=CREDITS_WINDOW_LABEL,
                remaining=float(remaining),
                limit=float(total),
                remaining_percent=remaining_percent,
                reset_label=_reset_label(reset_ts),
                reset_at=reset_ts,
                direction="remaining",
            )
        ]
        report.status = "cooling" if cooling else ("disabled" if disabled else "ok")
        reports.append(report)
    return reports
