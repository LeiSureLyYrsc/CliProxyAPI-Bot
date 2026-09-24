"""Qoder2OAPI 网关客户端（HTTP / JSON）。

接口：
- ``GET /v1/dashboard/billing/credits``：查询号池及各账号额度。
鉴权方式：
- ``Authorization: Bearer <api_key>``
"""

from __future__ import annotations

import httpx

from ..config import QoderServer


class QoderError(Exception):
    """Qoder 代理调用失败，消息可直接发给管理员。"""


async def fetch_credits(server: QoderServer, *, timeout: float | None = None) -> dict:
    """查询单个 Qoder 代理的号池及各账号额度，返回 ``/v1/dashboard/billing/credits`` 的原始 JSON。"""
    api_key = (server.api_key or "").strip()
    if not api_key:
        raise QoderError(
            f"Qoder 代理「{server.name}」未配置 api_key，请在配置中填入代理 API Key（见 data/api_key.txt）。"
        )
    base = (server.base_url or "").rstrip("/")
    if not base:
        raise QoderError(f"Qoder 代理「{server.name}」缺少 base_url。")

    url = f"{base}/v1/dashboard/billing/credits"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    budget = timeout if timeout is not None else server.timeout
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(budget)) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise QoderError(f"无法连接 Qoder 代理「{server.name}」（{url}）：{exc}") from exc

    if response.status_code == 401:
        detail = _error_detail(response)
        raise QoderError(
            f"Qoder 代理「{server.name}」鉴权失败：API Key 无效或缺失（401，见 data/api_key.txt）"
            + (f"：{detail}" if detail else "。")
        )
    if response.status_code == 404:
        raise QoderError(f"Qoder 代理「{server.name}」返回 404：{url} 不存在，请检查 base_url。")
    if response.status_code >= 400:
        detail = _error_detail(response)
        hint = "（服务内部错误，可稍后退避重试）" if response.status_code >= 500 else ""
        raise QoderError(
            f"Qoder 代理「{server.name}」返回 HTTP {response.status_code}{hint}：{detail}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise QoderError(f"Qoder 代理「{server.name}」返回了无法解析的 JSON。") from exc
    if not isinstance(data, dict):
        raise QoderError(f"Qoder 代理「{server.name}」返回结构异常（顶层不是对象）。")
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
