from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState
from pydantic import ValidationError

from .config import Config
from .protocol import (
    PROTOCOL_VERSION,
    CodexRefreshPayload,
    CodexRefreshResult,
    Envelope,
    QuotaQueryPayload,
    QuotaQueryResult,
    normalize_client_name,
    valid_client_name,
)

try:
    from nonebot.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("cpaplugin.hub")


class HubError(Exception):
    """远程客户端查询失败，消息可直接发给管理员。"""


@dataclass
class PendingRequest:
    action: str
    future: asyncio.Future[dict[str, Any]]


@dataclass
class ClientSession:
    name: str
    websocket: WebSocket
    session_id: str
    pending: dict[str, PendingRequest] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Hub:
    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self._cfg: Config | None = None

    def configure(self, cfg: Config) -> None:
        self._cfg = cfg

    def online_names(self) -> list[str]:
        return sorted(self._sessions)

    def known_names(self, cfg: Config) -> set[str]:
        names = {normalize_client_name(cfg.client_name)}
        names.update(normalize_client_name(name) for name in cfg.cpa_server_client_keys)
        names.update(self._sessions)
        return names

    def create_app(self, cfg: Config) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, title="CPA Client Hub")

        @app.get("/health")
        async def health() -> dict[str, bool]:
            return {"ok": True}

        @app.websocket("/v1/client/ws")
        async def client_ws(websocket: WebSocket) -> None:
            await self.handle_socket(websocket, cfg)

        return app

    async def start(self, cfg: Config) -> None:
        if not cfg.server_mode:
            return
        if self._task and not self._task.done():
            return
        import uvicorn

        self._cfg = cfg
        app = self.create_app(cfg)
        config = uvicorn.Config(
            app,
            host=cfg.cpa_server_host,
            port=cfg.cpa_server_port,
            log_level="info",
            ws_max_size=max(1024, cfg.cpa_server_ws_max_size),
            ws_max_queue=8,
            ws_ping_interval=20,
            ws_ping_timeout=20,
            timeout_graceful_shutdown=10,
            limit_concurrency=100,
            proxy_headers=False,
            server_header=False,
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve(), name="cpa-client-hub")
        logger.info(f"CPA Server_Mode 监听 {cfg.cpa_server_host}:{cfg.cpa_server_port}")

    async def stop(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await _fail_pending(session, "服务器已关闭")
            await _close_ws(session.websocket, code=status.WS_1001_GOING_AWAY)
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._server = None

    async def handle_socket(self, websocket: WebSocket, cfg: Config) -> None:
        name, error, code = _handshake(websocket, cfg)
        if error or not name:
            await websocket.close(code=code or status.WS_1008_POLICY_VIOLATION)
            logger.warning(f"拒绝客户端连接：{error}")
            return
        session = ClientSession(name=name, websocket=websocket, session_id=uuid.uuid4().hex)
        async with self._lock:
            current = self._sessions.get(name)
            if current is not None:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                logger.warning(f"拒绝同名客户端：{name}")
                return
            await websocket.accept()
            self._sessions[name] = session
        logger.info(f"客户端已连接：{name}")
        try:
            await websocket.send_json(
                Envelope(version=PROTOCOL_VERSION, type="hello", id="", payload={"client_name": name}).model_dump()
            )
            while True:
                raw = await websocket.receive_json()
                await self._on_message(session, raw, cfg)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning(f"客户端 {name} 连接异常：{exc}")
        finally:
            await self._drop(session, "客户端已断开")

    async def _on_message(self, session: ClientSession, raw: Any, cfg: Config) -> None:
        try:
            envelope = Envelope.model_validate(raw)
        except ValidationError:
            return
        if envelope.version != PROTOCOL_VERSION:
            return
        if envelope.type != "response" or not envelope.id:
            return
        pending_item = session.pending.get(envelope.id)
        if pending_item is None or pending_item.future.done():
            return
        future = pending_item.future
        if envelope.ok is False:
            future.set_exception(HubError(envelope.error or "客户端返回失败"))
            return

        if pending_item.action == "codex.refresh":
            try:
                refresh_res = CodexRefreshResult.model_validate(envelope.result or {})
            except ValidationError as exc:
                future.set_exception(HubError(f"客户端返回无法解析：{exc}"))
                return
            future.set_result(refresh_res.model_dump())
            return

        try:
            result = QuotaQueryResult.model_validate(envelope.result or {})
        except ValidationError as exc:
            future.set_exception(HubError(f"客户端返回无法解析：{exc}"))
            return
        if result.client_name != session.name:
            future.set_exception(HubError("客户端返回的名称与连接身份不一致。"))
            return
        for account in result.accounts:
            account.client_name = session.name
            account.auth_index = ""
        if len(result.accounts) > cfg.cpa_server_max_accounts:
            result.accounts = result.accounts[: cfg.cpa_server_max_accounts]
        future.set_result(result.model_dump())

    async def _drop(self, session: ClientSession, reason: str) -> None:
        async with self._lock:
            current = self._sessions.get(session.name)
            if current is session:
                self._sessions.pop(session.name, None)
        await _fail_pending(session, reason)
        logger.info(f"客户端已断开：{session.name}")

    async def query_quota(
        self,
        name: str,
        *,
        platform: str | None = None,
        account: str | None = None,
        fresh: bool = False,
        timeout: float | None = None,
    ) -> QuotaQueryResult:
        cfg = self._cfg
        payload = QuotaQueryPayload(platform=platform, account=account, fresh=fresh)
        req_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        async with self._lock:
            session = self._sessions.get(name)
            if session is None:
                raise HubError(f"客户端 {name} 未连接。")
            session.pending[req_id] = PendingRequest(action="quota.query", future=future)
        message = Envelope(
            version=PROTOCOL_VERSION,
            type="request",
            id=req_id,
            action="quota.query",
            payload=payload.model_dump(),
        )
        try:
            async with session.lock:
                await session.websocket.send_json(message.model_dump())
                wait = timeout if timeout is not None else (cfg.cpa_server_request_timeout if cfg else 40.0)
                data = await asyncio.wait_for(future, timeout=max(1.0, wait))
            return QuotaQueryResult.model_validate(data)
        except asyncio.TimeoutError as exc:
            raise HubError(f"客户端 {name} 查询超时。") from exc
        except HubError:
            raise
        except Exception as exc:
            raise HubError(f"客户端 {name} 查询失败。") from exc
        finally:
            session.pending.pop(req_id, None)

    async def refresh_codex(
        self,
        name: str,
        account: str,
        *,
        timeout: float | None = None,
    ) -> CodexRefreshResult:
        cfg = self._cfg
        payload = CodexRefreshPayload(account=account)
        req_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        async with self._lock:
            session = self._sessions.get(name)
            if session is None:
                raise HubError(f"客户端 {name} 未连接。")
            session.pending[req_id] = PendingRequest(action="codex.refresh", future=future)
        message = Envelope(
            version=PROTOCOL_VERSION,
            type="request",
            id=req_id,
            action="codex.refresh",
            payload=payload.model_dump(),
        )
        try:
            async with session.lock:
                await session.websocket.send_json(message.model_dump())
                wait = timeout if timeout is not None else (cfg.cpa_server_request_timeout if cfg else 40.0)
                data = await asyncio.wait_for(future, timeout=max(1.0, wait))
            return CodexRefreshResult.model_validate(data)
        except asyncio.TimeoutError as exc:
            raise HubError(f"客户端 {name} 刷新超时。") from exc
        except HubError:
            raise
        except Exception as exc:
            raise HubError(f"客户端 {name} 刷新失败。") from exc
        finally:
            session.pending.pop(req_id, None)


_hub = Hub()


def get_hub() -> Hub:
    return _hub


def authenticate_headers(headers: Mapping[str, str], cfg: Config) -> tuple[str, str, int]:
    raw_name = headers.get("x-cpa-client-name") or headers.get("X-CPA-Client-Name") or ""
    name = normalize_client_name(raw_name)
    authorization = headers.get("authorization") or headers.get("Authorization") or ""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not valid_client_name(name):
        return "", "客户端名称非法", status.WS_1008_POLICY_VIOLATION
    local = normalize_client_name(cfg.client_name)
    if name == local:
        return "", "名称已被本机客户端占用", status.WS_1008_POLICY_VIOLATION
    expected = cfg.cpa_server_client_keys.get(name, "")
    if not expected or not token or not _tokens_match(token, expected):
        return "", "鉴权失败", status.WS_1008_POLICY_VIOLATION
    return name, "", 0


def _handshake(websocket: WebSocket, cfg: Config) -> tuple[str, str, int]:
    return authenticate_headers(websocket.headers, cfg)


def _tokens_match(provided: str, expected: str) -> bool:
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return secrets.compare_digest(left, right)


async def _fail_pending(session: ClientSession, reason: str) -> None:
    pending = list(session.pending.values())
    session.pending.clear()
    for item in pending:
        if not item.future.done():
            item.future.set_exception(HubError(reason))


async def _close_ws(websocket: WebSocket, *, code: int) -> None:
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=code)
    except Exception:
        pass
