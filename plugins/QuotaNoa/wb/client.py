"""WorkBuddy2API 网关客户端（HTTP / JSON）。

鉴权模型（实测）：

- **控制台**：``POST /api/login {username, password}`` → 会话 token，用作
  ``Authorization: Bearer <token>`` 访问 ``/api/*``（含 ``GET /api/config``）。
- **数据接口**：``GET /v1/quota`` 需要网关的静态 ``api_key``（``Authorization:
  Bearer <api_key>``）。该 key 可由登录后 ``GET /api/config`` 读出。

因此本模块对外只暴露一个 ``fetch_quota``：优先用配置里的 ``api_key`` 直连；
否则用账号密码登录 → 读 ``api_key`` → 调 ``/v1/quota``，并缓存会话与 key，
过期后自动重登。``api_key``/``username``/``password`` 三者至少需配置其一。

详见仓库根目录 ``wb-for-quotanoa.md`` 与 ``tests/workbuddy.md``。
"""

from __future__ import annotations

import time

import httpx

from ..config import WorkbuddyServer


class WorkbuddyError(Exception):
    """WorkBuddy 网关调用失败，消息可直接发给管理员。"""


#: 每个网关的会话缓存：name → (token, api_key, 过期时间 monotonic)。
#: 会话默认 12h，这里保守取 10h；拿到 401 时也会主动失效重登。
_SESSION_TTL = 10 * 3600.0
_sessions: dict[str, tuple[str, str, float]] = {}


def reset_sessions(name: str | None = None) -> None:
    """清空会话缓存。传 ``name`` 只清该网关，传 None 清全部。"""
    if name is None:
        _sessions.clear()
        return
    _sessions.pop(name, None)


async def fetch_quota(server: WorkbuddyServer, *, timeout: float | None = None) -> dict:
    """查询单个网关的**全账号**套餐配额，返回 ``/v1/quota`` 的原始 JSON。"""
    api_key = await _resolve_api_key(server, timeout=timeout)
    return await _request(server, "/v1/quota", api_key=api_key, timeout=timeout)


async def healthz(server: WorkbuddyServer, *, timeout: float | None = None) -> dict:
    """健康检查（无鉴权）。``200`` = 有可服务账号；``503`` = 无可用账号。"""
    data = await _request(server, "/healthz", api_key="", timeout=timeout, allow_503=True)
    return data


async def login(server: WorkbuddyServer, *, timeout: float | None = None) -> tuple[str, str]:
    """用账号密码登录并返回 ``(会话 token, api_key)``；结果写入会话缓存。"""
    if not server.username or not server.password:
        raise WorkbuddyError(
            f"WorkBuddy 网关「{server.name}」缺少 username / password，无法登录控制台。"
        )
    payload = await _request(
        server,
        "/api/login",
        api_key="",
        method="POST",
        json_body={"username": server.username, "password": server.password},
        timeout=timeout,
        auth_mode="none",
    )
    token = str(payload.get("token") or "").strip()
    if not token:
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」登录成功但未返回 token。")
    api_key = await _read_api_key(server, token, timeout=timeout)
    _sessions[server.name] = (token, api_key, time.monotonic() + _SESSION_TTL)
    return token, api_key


async def _resolve_api_key(server: WorkbuddyServer, *, timeout: float | None = None) -> str:
    """返回可用的 ``api_key``：优先显式配置，其次缓存的会话，最后登录获取。"""
    if server.api_key:
        return server.api_key
    cached = _sessions.get(server.name)
    if cached and time.monotonic() < cached[2] and cached[1]:
        return cached[1]
    if server.username and server.password:
        _, api_key = await login(server, timeout=timeout)
        return api_key
    raise WorkbuddyError(
        f"WorkBuddy 网关「{server.name}」未配置鉴权：请设置 username/password（控制台登录）"
        "或 api_key（直连）。"
    )


async def _read_api_key(server: WorkbuddyServer, token: str, *, timeout: float | None = None) -> str:
    payload = await _request(
        server,
        "/api/config",
        api_key=token,
        timeout=timeout,
        auth_mode="session",
    )
    config = payload.get("config") if isinstance(payload, dict) else None
    api_key = str((config or {}).get("api_key") or "").strip() if isinstance(config, dict) else ""
    if not api_key:
        raise WorkbuddyError(
            f"WorkBuddy 网关「{server.name}」控制台未返回 api_key（GET /api/config）。"
        )
    return api_key


async def _request(
    server: WorkbuddyServer,
    path: str,
    *,
    api_key: str,
    method: str = "GET",
    json_body: dict | None = None,
    timeout: float | None = None,
    allow_503: bool = False,
    auth_mode: str = "auto",
) -> dict:
    base = (server.base_url or "").rstrip("/")
    if not base:
        raise WorkbuddyError(f"WorkBuddy 网关「{server.name}」缺少 base_url。")
    url = f"{base}{path}"
    headers = {"Accept": "application/json"}
    if api_key and auth_mode != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    budget = timeout if timeout is not None else server.timeout
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(budget)) as client:
            if method == "POST":
                response = await client.post(url, headers=headers, json=json_body)
            else:
                response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise WorkbuddyError(f"无法连接 WorkBuddy 网关「{server.name}」（{url}）：{exc}") from exc

    if response.status_code == 401:
        detail = _error_detail(response)
        where = "控制台账号或密码错误" if path == "/api/login" else "鉴权失败：api_key/会话无效"
        # 会话或 key 失效：丢弃缓存，下次调用会重新登录。
        reset_sessions(server.name)
        raise WorkbuddyError(
            f"WorkBuddy 网关「{server.name}」{where}（401）"
            + (f"：{detail}" if detail else "。")
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
