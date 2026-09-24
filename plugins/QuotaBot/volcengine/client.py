"""火山方舟 Coding Plan 额度查询客户端（控制面 OpenAPI）。

- 端点：``POST https://open.volcengineapi.com/?Action=GetCodingPlanUsage&Region=cn-beijing&Version=2024-01-01``
- 鉴权：Volcengine Signature V4（见 ``signer.py``），使用控制面 AccessKey/SecretAccessKey。
- 返回：``Result.QuotaUsage[]``（Level = session / weekly / monthly，Percent 为已用百分比）。
"""

from __future__ import annotations

import json

import httpx

from ..config import VolcengineAccount
from .signer import canonical_query_string, sign_request

OPENAPI_HOST = "open.volcengineapi.com"
OPENAPI_URL = f"https://{OPENAPI_HOST}/"
ACTION_CODING_PLAN_USAGE = "GetCodingPlanUsage"
ACTION_PERSONAL_PLAN = "GetPersonalPlan"
API_VERSION = "2024-01-01"


class VolcengineError(Exception):
    """火山 OpenAPI 调用失败，消息可直接发给管理员。"""


def _build_query(region: str, action: str) -> dict[str, str]:
    return {
        "Action": action,
        "Region": region,
        "Version": API_VERSION,
    }


async def _post_signed(
    account: VolcengineAccount,
    action: str,
    *,
    body: str = "",
    timeout: float = 20.0,
) -> dict:
    """对控制面 OpenAPI 发一次签名请求，返回原始 JSON。

    ``4xx`` 会抛 ``VolcengineError``；调用方按需捕获特定错误码。
    """
    if not account.access_key_id or not account.secret_access_key:
        raise VolcengineError(f"火山账号「{account.name}」缺少 access_key_id / secret_access_key。")

    query = _build_query(account.region, action)
    headers = sign_request(
        access_key_id=account.access_key_id,
        secret_access_key=account.secret_access_key,
        method="POST",
        host=OPENAPI_HOST,
        path="/",
        query=query,
        region=account.region,
        body=body,
    )
    # 规范化 query 与签名同源：按 key 字典序、RFC3986 编码（%20 而非 +）。
    query_string = canonical_query_string(query)
    url = f"{OPENAPI_URL}?{query_string}"
    content = body.encode("utf-8") if body else None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(url, headers=headers, content=content)
    except httpx.RequestError as exc:
        raise VolcengineError(f"无法连接火山 OpenAPI：{exc}") from exc

    if response.status_code >= 400:
        detail = _error_detail(response)
        if "SignatureDoesNotMatch" in detail:
            raise VolcengineError(
                f"火山签名失败（{response.status_code}）：{detail}\n"
                "请检查系统时间是否准确（SigV4 对时钟漂移敏感），以及 AK/SK 是否为控制台字面值（勿做 base64 解码）。"
            )
        raise VolcengineError(f"火山 OpenAPI 返回 HTTP {response.status_code}：{detail}")

    try:
        data = response.json()
    except ValueError as exc:
        raise VolcengineError("火山 OpenAPI 返回了无法解析的 JSON。") from exc
    if not isinstance(data, dict):
        raise VolcengineError("火山 OpenAPI 返回结构异常。")

    metadata = data.get("ResponseMetadata")
    if isinstance(metadata, dict) and metadata.get("Error"):
        err = metadata["Error"]
        code = err.get("Code") or err.get("Message") or "未知错误"
        message = err.get("Message") or ""
        raise VolcengineError(f"火山 OpenAPI 报错：{code}" + (f"（{message}）" if message else ""))
    return data


async def query_coding_plan_usage(
    account: VolcengineAccount,
    *,
    timeout: float = 20.0,
) -> dict:
    """查询单个火山账号的 Coding Plan 额度，返回原始 JSON。"""
    return await _post_signed(account, ACTION_CODING_PLAN_USAGE, timeout=timeout)


async def query_personal_plan(
    account: VolcengineAccount,
    *,
    timeout: float = 20.0,
) -> dict:
    """查询 Coding Plan 套餐档位（Lite / Pro）。

    未订阅该套餐时火山返回 ``404 ResourceNotFound.Plan``，此时返回空 dict
    （视为「无套餐」），不视为错误。
    """
    body = json.dumps({"Plan": "CodingPlan"})
    try:
        return await _post_signed(account, ACTION_PERSONAL_PLAN, body=body, timeout=timeout)
    except VolcengineError as exc:
        message = str(exc)
        if "ResourceNotFound.Plan" in message or "404" in message:
            return {}
        raise


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:200] if text else ""
    if isinstance(data, dict):
        metadata = data.get("ResponseMetadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("Error"), dict):
            err = metadata["Error"]
            return str(err.get("Code") or err.get("Message") or "")
        for key in ("Message", "message", "error"):
            if data.get(key):
                return str(data[key])
    return ""
