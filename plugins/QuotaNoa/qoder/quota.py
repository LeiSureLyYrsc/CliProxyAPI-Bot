"""把 Qoder ``/v1/dashboard/billing/credits`` 响应解析为统一的账号额度对象。

注意（核心约束）：
- ``expires_at`` 是凭证/登录过期时间，不是额度重置时间。
- Qoder 额度响应无周期重置概念，因此所有配额窗口重置字段留空：``reset_label="-"``, ``reset_at=None``。
- 绝不展示或将 ``expires_at`` 写入窗口重置信息。
"""

from __future__ import annotations

import re
from typing import Any

from ..model import AccountQuota, QuotaWindow

GENERAL_WINDOW_ID = "qoder-general"
ADDON_WINDOW_ID = "qoder-addon"
GENERAL_WINDOW_LABEL = "通用"
ADDON_WINDOW_LABEL = "加量"

#: Qoder 订阅 TYPE → 展示档位名。
#:
#: 上游有两种形态：
#: - ``user_type``（``/v2/quota/usage`` 的 ``userType``）：如 ``personal_professional``；
#: - ``plan_tier`` / ``subscription_type`` 枚举：如 ``PLAN_TIER_PRO_PLUS`` /
#:   ``ORGANIZATION_PLAN_TIER_TEAM``（去前缀后查表）。
#:
#: 档位归属：个人订阅 = 体验版 / 专业版 / 高级版 / 旗舰版；企业订阅 = 团队版 / 企业标准版。
PLAN_LABELS: dict[str, str] = {
    # --- 个人订阅（user_type 形态）---
    "personal_standard": "体验版",
    "personal_free": "体验版",
    "personal_community": "体验版",
    "personal": "体验版",
    "personal_professional_trial": "专业版（试用）",
    "personal_professional": "专业版",
    "personal_pro_plus": "高级版",
    "personal_proplus": "高级版",
    "personal_ultra": "旗舰版",
    # --- 企业订阅 ---
    "teams": "团队版",
    "team": "团队版",
    "enterprise": "企业标准版",
    "enterprise_standard": "企业标准版",
    "enterprise_vpc": "企业专属版",
    # --- 代理层聚合态 ---
    "pool": "共享池",
    "none": "无订阅",
    # --- PLAN_TIER_* / ORGANIZATION_PLAN_TIER_*（去前缀后）---
    "free": "体验版",
    "community": "体验版",
    "community edition": "体验版",
    "protrial": "专业版（试用）",
    "pro trial": "专业版（试用）",
    "pro": "专业版",
    "proplus": "高级版",
    "pro plus": "高级版",
    "pro+": "高级版",
    "ultra": "旗舰版",
    "orgenterprise": "企业标准版",
}

_PLAN_TIER_PREFIX = re.compile(r"^(?:organization_plan_tier|plan_tier)_")


def plan_label(value: Any) -> str:
    """把 Qoder 上游订阅 TYPE 映射为展示档位名；未知值原样返回。

    兼容 ``user_type``（``personal_professional``）与 ``PLAN_TIER_*`` /
    ``ORGANIZATION_PLAN_TIER_*`` 枚举，忽略大小写与 ``-`` / ``_`` / 空格差异。
    """
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return ""
    lowered = raw.lower().replace("-", "_")
    if lowered in PLAN_LABELS:
        return PLAN_LABELS[lowered]
    token = _PLAN_TIER_PREFIX.sub("", lowered)
    token = re.sub(r"\s+", " ", token.replace("_", " ")).strip()
    if token in PLAN_LABELS:
        return PLAN_LABELS[token]
    compact = token.replace(" ", "")
    if compact in PLAN_LABELS:
        return PLAN_LABELS[compact]
    return raw


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _make_bucket_window(window_id: str, label: str, bucket: Any) -> QuotaWindow | None:
    if not isinstance(bucket, dict):
        return None
    total = _num(bucket.get("total"))
    if total is None or total <= 0:
        return None
    rem = _num(bucket.get("remaining"))
    remaining = float(rem) if rem is not None else 0.0
    remaining_percent = max(0.0, min(100.0, remaining / total * 100.0))
    return QuotaWindow(
        id=window_id,
        label=label,
        remaining=remaining,
        limit=float(total),
        remaining_percent=remaining_percent,
        reset_label="-",
        reset_at=None,
        direction="remaining",
    )


def _make_dedicated_windows(packages: Any) -> list[QuotaWindow]:
    if not isinstance(packages, list):
        return []
    windows: list[QuotaWindow] = []
    idx = 1
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        if entry.get("available") is False:
            continue
        label = str(entry.get("title") or entry.get("plan_name") or "专属").strip() or "专属"
        total = _num(entry.get("total"))
        rem = _num(entry.get("remaining"))
        limit = float(total) if total is not None else None
        remaining = float(rem) if rem is not None else None
        remaining_percent: float | None = None
        if total is not None and total > 0 and rem is not None:
            remaining_percent = max(0.0, min(100.0, rem / total * 100.0))
        windows.append(
            QuotaWindow(
                id=f"qoder-dedicated-{idx}",
                label=label,
                remaining=remaining,
                limit=limit,
                remaining_percent=remaining_percent,
                reset_label="-",
                reset_at=None,
                direction="remaining",
            )
        )
        idx += 1
    return windows


def parse_quota_accounts(payload: dict[str, Any], *, server_name: str) -> list[AccountQuota]:
    """把一个 Qoder 代理的 ``/v1/dashboard/billing/credits`` 响应转为统一账号额度列表。"""
    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    reports: list[AccountQuota] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw_id = str(row.get("id") or "").strip()
        raw_name = str(row.get("name") or "").strip()
        raw_email = str(row.get("email") or "").strip()
        raw_user_id = str(row.get("user_id") or "").strip()
        name = raw_name or raw_email or raw_user_id or raw_id or "(unknown)"

        plan = plan_label(row.get("user_type"))

        report = AccountQuota(
            platform="qoder",
            name=name,
            auth_index=raw_id,
            plan=plan,
            instance=server_name,
        )

        windows: list[QuotaWindow] = []
        gen_window = _make_bucket_window(GENERAL_WINDOW_ID, GENERAL_WINDOW_LABEL, row.get("general"))
        if gen_window is not None:
            windows.append(gen_window)

        addon_window = _make_bucket_window(ADDON_WINDOW_ID, ADDON_WINDOW_LABEL, row.get("addon"))
        if addon_window is not None:
            windows.append(addon_window)

        windows.extend(_make_dedicated_windows(row.get("dedicated")))
        report.windows = windows

        raw_flags = row.get("flags")
        flags: dict[str, Any] = raw_flags if isinstance(raw_flags, dict) else {}
        # 标记（disabled）与查询错误正交：即便本次查询失败，也保留账号的启用状态信息。
        if flags.get("skip_auth") or row.get("skip_auth"):
            report.disabled = True
        elif flags.get("enabled") is False or row.get("enabled") is False:
            report.disabled = True

        err = row.get("error")
        if err:
            report.status = "error"
            report.error = str(err).strip()
        elif report.disabled:
            report.status = "disabled"
        else:
            report.status = "ok"

        reports.append(report)

    return reports
