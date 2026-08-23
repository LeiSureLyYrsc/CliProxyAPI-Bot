from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx
from nonebot import get_plugin_config

from .config import Config

_AUTH_FAIL_COOLDOWN = 60.0
_FORBIDDEN_COOLDOWN = 300.0

# 只允许额度巡检用到的上游。不要把 /api-call 做成聊天里的任意代发。
_API_CALL_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("api.anthropic.com", "/api/oauth/usage"),
    ("chatgpt.com", "/backend-api/wham/usage"),
    ("chatgpt.com", "/backend-api/wham/rate-limit-reset-credits"),
    ("api.kimi.com", "/coding/v1/usages"),
    ("cli-chat-proxy.grok.com", "/v1/billing"),
    ("daily-cloudcode-pa.googleapis.com", "/v1internal:retrieveUserQuotaSummary"),
    ("daily-cloudcode-pa.sandbox.googleapis.com", "/v1internal:retrieveUserQuotaSummary"),
    ("cloudcode-pa.googleapis.com", "/v1internal:retrieveUserQuotaSummary"),
    ("cloudcode-pa.googleapis.com", "/v1internal:retrieveUserQuota"),
)


def allowed_api_call_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    path = parsed.path
    return any(host == allowed_host and path == allowed_path for allowed_host, allowed_path in _API_CALL_ALLOWLIST)


class CPAError(Exception):
    """Management API 调用失败，消息可直接发给管理员。"""


def management_root(base_url: str) -> str:
    url = base_url.rstrip("/")
    suffix = "/v0/management"
    if url.endswith(suffix):
        return url
    return f"{url}{suffix}"


class ManagementClient:
    def __init__(self, base_url: str, key: str, timeout: float) -> None:
        self._root = management_root(base_url)
        self._key = key
        self._client = httpx.AsyncClient(
            base_url=self._root,
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {key}",
                "X-Management-Key": key,
                "Accept": "application/json",
                "User-Agent": "CPA-Plugin/0.1.0",
            },
        )
        self._auth_blocked_until = 0.0
        self._auth_block_reason = ""

    @classmethod
    def from_config(cls, cfg: Config) -> ManagementClient:
        return cls(cfg.cpa_base_url, cfg.cpa_management_key, cfg.cpa_timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _ensure_ready(self) -> None:
        if not self._key:
            raise CPAError("未配置 CPA_MANAGEMENT_KEY，无法调用管理接口。")
        now = time.monotonic()
        if now < self._auth_blocked_until:
            raise CPAError(self._auth_block_reason)

    def _block_auth(self, seconds: float, reason: str) -> None:
        self._auth_blocked_until = time.monotonic() + seconds
        self._auth_block_reason = reason

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        self._ensure_ready()
        try:
            response = await self._client.request(
                method, path, params=params, json=json, timeout=timeout
            )
        except httpx.RequestError as exc:
            raise CPAError(f"无法连接 CLIProxyAPI：{exc}") from exc

        if response.status_code == 401:
            reason = (
                "管理密钥无效（401）。请检查 CPA_MANAGEMENT_KEY。"
                "为避免连续失败导致 IP 被封禁约 30 分钟，1 分钟内将暂停后续请求。"
            )
            self._block_auth(_AUTH_FAIL_COOLDOWN, reason)
            raise CPAError(reason)
        if response.status_code == 403:
            detail = _response_detail(response)
            reason = (
                f"管理接口拒绝访问（403）{detail}。"
                "若 Bot 与 CPA 不在同一台机器，需要 allow-remote 或 MANAGEMENT_PASSWORD。"
                "5 分钟内将暂停后续请求，以免触发 IP 封禁。"
            )
            self._block_auth(_FORBIDDEN_COOLDOWN, reason)
            raise CPAError(reason)
        if response.status_code >= 400:
            raise CPAError(
                f"CLIProxyAPI 返回 HTTP {response.status_code}{_response_detail(response)}"
            )
        return response

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any:
        response = await self.request(
            method, path, params=params, json=json, timeout=timeout
        )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise CPAError("CLIProxyAPI 返回了无法解析的 JSON。") from exc

    async def probe(self) -> dict[str, str]:
        """GET /config 仅用于探活与读取版本头，调用方不得把 body 发进聊天。"""
        response = await self.request("GET", "/config")
        return {
            "version": response.headers.get("X-CPA-VERSION", ""),
            "commit": response.headers.get("X-CPA-COMMIT", ""),
            "build_date": response.headers.get("X-CPA-BUILD-DATE", ""),
            "support_plugin": response.headers.get("X-CPA-SUPPORT-PLUGIN", ""),
        }

    async def latest_version(self) -> str | None:
        try:
            data = await self.request_json("GET", "/latest-version")
        except CPAError:
            return None
        if isinstance(data, dict):
            value = data.get("latest-version")
            return str(value) if value else None
        return None

    async def list_auth_files(
        self,
        *,
        name: str | None = None,
        auth_index: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if auth_index:
            params["auth_index"] = auth_index
        data = await self.request_json("GET", "/auth-files", params=params or None)
        files = data.get("files") if isinstance(data, dict) else None
        return files if isinstance(files, list) else []

    async def auth_models(self, name: str) -> list[Any]:
        data = await self.request_json("GET", "/auth-files/models", params={"name": name})
        models = data.get("models") if isinstance(data, dict) else None
        return models if isinstance(models, list) else []

    async def patch_auth_status(
        self,
        name: str,
        disabled: bool,
        auth_index: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"name": name, "disabled": disabled}
        if auth_index:
            body["auth_index"] = auth_index
        return await self.request_json("PATCH", "/auth-files/status", json=body)

    async def delete_auth_file(self, name: str) -> Any:
        return await self.request_json("DELETE", "/auth-files", params={"name": name})

    async def reset_quota(self, auth_index: str) -> Any:
        return await self.request_json("POST", "/reset-quota", json={"auth_index": auth_index})

    async def start_login(self, path: str) -> dict[str, Any]:
        data = await self.request_json("GET", path)
        return data if isinstance(data, dict) else {}

    async def auth_status(self, state: str) -> dict[str, Any]:
        data = await self.request_json("GET", "/get-auth-status", params={"state": state})
        return data if isinstance(data, dict) else {}

    async def cancel_oauth(self, state: str) -> Any:
        return await self.request_json("DELETE", "/oauth-session", params={"state": state})

    async def api_call(
        self,
        auth_index: str,
        method: str,
        url: str,
        *,
        header: dict[str, str] | None = None,
        data: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """内部代发上游请求。不要把原始 body 回传到聊天。"""
        if not allowed_api_call_url(url):
            raise CPAError("拒绝调用未列入额度白名单的上游地址。")
        payload: dict[str, Any] = {
            "auth_index": auth_index,
            "method": method,
            "url": url,
        }
        if header:
            payload["header"] = header
        if data is not None:
            payload["data"] = data
        result = await self.request_json("POST", "/api-call", json=payload, timeout=timeout)
        return result if isinstance(result, dict) else {}

    async def list_plugins(self) -> list[dict[str, Any]]:
        data = await self.request_json("GET", "/plugins")
        plugins = data.get("plugins") if isinstance(data, dict) else None
        return plugins if isinstance(plugins, list) else []


_client: ManagementClient | None = None


def get_client() -> ManagementClient:
    global _client
    if _client is None:
        _client = ManagementClient.from_config(get_plugin_config(Config))
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _response_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return f"：{text[:200]}" if text else ""
    if isinstance(data, dict):
        for key in ("error", "message", "status"):
            value = data.get(key)
            if value and key != "status":
                return f"：{value}"
            if key == "status" and value not in (None, "ok"):
                return f"：{value}"
    return ""
