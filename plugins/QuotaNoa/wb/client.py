"""WorkBuddy2API 网关客户端（HTTP / JSON）。

- 端点：``GET {base_url}/v1/quota``（全账号批量套餐配额，服务端 30s 缓存）。
- 鉴权：``Authorization: Bearer <api_key>``；``api_key`` 为空时网关放行。
- 返回：``{"provider": "workbuddy", "accounts": [...]}``，行级失败通过
  ``quotas: null`` + ``error`` 表达，整体仍为 200。

详见仓库根目录 ``wb-for-quotanoa.md``。
"""

from __future__ import annotations

import httpx

from ..config import WorkbuddyServer


class WorkbuddyError(Exception):
    """WorkBuddy 网关调用失败，消息可直接发给管理员。"""


async def fetch_quota(server: WorkbuddyServer, *, timeout: float | None = None) -> dict:
    """查询单个网关的**全账号**套餐配额，返回原始 JSON。"""
    return await _get_json(server, "/v1/quota", timeout=timeout)


async def healthz(server: WorkbuddyServer, *, timeout: float | None = None) -> dict:
    """健康检查（无鉴权）。``200`` = 有可服务账号；``503`` = 无可用账号。"""
    return await _get_json(server, "/healthz", timeout=timeout, allow_503=True)


async def _get_json(
    server: WorkbuddyServer,
    path: str,
    *,
    timeout: float | None = None,
    allow_503: bool = False,
) -> dict:
    base = (server.base_url or "").rstrip("/")
    if not base:
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」缺少 base_url。")
    url = f"{base}{path}"
    headers = {"Accept": "application/json"}
    if server.api_key:
        headers["Authorization"] = f"Bearer {server.api_key}"

    budget = timeout if timeout is not None else server.timeout
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(budget)) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise WorkbuddyError(f"无法连接 WorkBuddy 网关「{server.name}」（{url}）：{exc}") from exc

    if response.status_code == 401:
        raise WorkbuddyError(
            f"WorkBuddy 网关「{server.name}」鉴权失败（401）：api_key 缺失或错误。"
        )
    if response.status_code == 404:
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」返回 404：{url} 不存在，请检查 base_url。")
    if allow_503 and response.status_code == 503:
        return {"healthy": 0, "service": "workbuddy2api"}
    if response.status_code >= 400:
        detail = _error_detail(response)
        hint = "（服务内部错误，可稍后退避重试）" if response.status_code >= 500 else ""
        raise WorkbuddyError(
            f"WorkBuddy 网关「{server.name}」返回 HTTP {response.status_code}{hint}：{detail}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」返回了无法解析的 JSON。") from exc
    if not isinstance(data, dict):
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」返回结构异常（顶层不是对象）。")
    return data


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:200] if text else ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "")
        for key in ("message", "detail", "error"):
            if data.get(key):
                return str(data[key])
    return ""
