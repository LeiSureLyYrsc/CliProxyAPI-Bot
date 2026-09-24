"""火山方舟额度 Provider：读取配置账号 → 查询 → 统一额度板。

本地渠道（``local_only``）：凭据来自本机 ``volcengine.accounts``，不属于任何 CPA 实例。
"""

from __future__ import annotations

from .. import state
from ..aliases import resolve_alias_for_keys
from ..config import VolcengineAccount
from ..model import AccountQuota, QuotaBoard, board_from_accounts
from .client import VolcengineError, query_coding_plan_usage, query_personal_plan
from .quota import account_from_usage, parse_personal_plan

CHANNEL = "volcengine"


async def _plan_of(account: VolcengineAccount) -> str:
    """拉取套餐档位（Lite/Pro）；失败或无套餐返回空串，不影响额度查询。"""
    try:
        payload = await query_personal_plan(account)
    except VolcengineError:
        return ""
    return parse_personal_plan(payload)


async def collect_board(accounts: list[VolcengineAccount] | None = None) -> QuotaBoard:
    """查询全部（或指定）火山账号，返回额度板。"""
    configs = list(accounts if accounts is not None else state.get_snapshot().volcengine.accounts)
    reports: list[AccountQuota] = []
    for account in configs:
        plan = await _plan_of(account)
        try:
            payload = await query_coding_plan_usage(account)
            report = account_from_usage(account, payload, plan=plan)
        except VolcengineError as exc:
            report = AccountQuota(
                platform=CHANNEL,
                name=account.name,
                auth_index="",
                plan=plan,
                status="error",
                error=str(exc),
            )
        alias = resolve_alias_for_keys(CHANNEL, [account.name])
        if alias:
            report.name = alias
        reports.append(report)
    return board_from_accounts(reports)
