"""火山方舟 Coding Plan 额度查询客户端（控制面 OpenAPI）。

- 端点：``POST https://open.volcengineapi.com/?Action=GetCodingPlanUsage&Region=cn-beijing&Version=2024-01-01``
- 鉴权：Volcengine Signature V4（见 ``signer.py``），使用控制面 AccessKey/SecretAccessKey。
- 返回：``Result.QuotaUsage[]``（Level = session / weekly / monthly，Percent 为已用百分比）。
"""

from __future__ import annotations

import httpx

from ..config import VolcengineAccount
from .signer import canonical_query_string, sign_request

OPENAPI_HOST = "open.volcengineapi.com"
OPENAPI_URL = f"https://{OPENAPI_HOST}/"
ACTION_CODING_PLAN_USAGE = "GetCodingPlanUsage"
API_VERSION = "2024-01-01"


class VolcengineError(Exception):
    """火山 OpenAPI 调用失败，消息可直接发给管理员。"""


def _build_query(region: str) -> dict[str, str]:
    return {
        "Action": ACTION_CODING_PLAN_USAGE,
        "Region": region,
        "Version": API_VERSION,
    }


async def query_coding_plan_usage(
    account: VolcengineAccount,
    *,
    timeout: float = 20.0,
) -> dict:
    """查询单个火山账号的 Coding Plan 额度，返回原始 JSON。"""
    if not account.access_key_id or not account.secret_access_key:
        raise VolcengineError(f"火山账号「{account.name}」缺少 access_key_id / secret_access_key。")

    query = _build_query(account.region)
    headers = sign_request(
        access_key_id=account.access_key_id,
        secret_access_key=account.secret_access_key,
        method="POST",
        host=OPENAPI_HOST,
        path="/",
        query=query,
        region=account.region,
    )
    # 规范化 query 与签名同源：按 key 字典序、RFC3986 编码（%20 而非 +）。
    query_string = canonical_query_string(query)
    url = f"{OPENAPI_URL}?{query_string}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(url, headers=headers)
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
