from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.log import logger
from nonebot_plugin_alconna import Target, UniMessage, get_target

from .client import CPAError, get_client
from .. import state
from .format import extract_oauth_callback_url, format_login_prompt

BUILTIN_AUTH_URLS: dict[str, str] = {
    "claude": "/anthropic-auth-url",
    "anthropic": "/anthropic-auth-url",
    "codex": "/codex-auth-url",
    "antigravity": "/antigravity-auth-url",
    "kimi": "/kimi-auth-url",
    "xai": "/xai-auth-url",
}


def session_key(bot: Bot, event: Event) -> str:
    try:
        user_id = event.get_user_id()
    except Exception:
        return ""
    if not user_id:
        return ""
    return f"{bot.self_id}:{user_id}"


@dataclass
class PendingLogin:
    key: str
    state: str
    provider: str
    bot: Bot
    instance: str = ""
    target: Target = field(default_factory=lambda: Target("", ""))
    task: asyncio.Task[None] | None = field(default=None)


_pending: dict[str, PendingLogin] = {}


async def discover_auth_urls(instance: str) -> dict[str, str]:
    mapping = dict(BUILTIN_AUTH_URLS)
    try:
        plugins = await get_client(instance).list_plugins()
    except CPAError:
        return mapping
    for plugin in plugins:
        if not plugin.get("supports_oauth"):
            continue
        provider = str(plugin.get("oauth_provider") or "").strip().lower()
        if not provider:
            continue
        mapping.setdefault(provider, f"/{provider}-auth-url")
    return mapping


async def resolve_auth_path(instance: str, provider: str) -> tuple[str, str]:
    alias = provider.strip().lower()
    mapping = await discover_auth_urls(instance)
    path = mapping.get(alias)
    if not path:
        known = ", ".join(sorted(set(mapping)))
        raise CPAError(f"未知登录渠道「{provider}」。可用：{known or '（无）'}")
    canonical = alias
    for name, mapped in mapping.items():
        if mapped == path:
            canonical = name
            break
    return canonical, path


async def send_secret(bot: Bot, event: Event, text: str) -> Target:
    origin = get_target(event, bot)
    if origin.private:
        await UniMessage(text).send()
        return origin

    data = origin.dump()
    data["id"] = event.get_user_id()
    data["private"] = True
    data["channel"] = False
    private = Target.load(data)
    try:
        await UniMessage(text).send(target=private, bot=bot)
    except Exception as exc:
        logger.opt(exception=exc).warning("CPA oauth private send failed")
        await UniMessage(f"无法私聊发送，以下内容将在当前会话发出，请注意保密：\n{text}").send()
        return origin
    await UniMessage("授权信息已私聊发送。").send()
    return private


async def start_login(bot: Bot, event: Event, instance: str, provider: str, payload: dict[str, Any]) -> Target:
    status = str(payload.get("status") or "ok").lower()
    if status not in {"ok", "success"}:
        raise CPAError(str(payload.get("error") or f"登录启动失败：{status}"))
    state = str(payload.get("state") or "")
    if not state:
        raise CPAError("登录接口没有返回 state，无法跟踪授权进度。")

    key = session_key(bot, event)
    if not key:
        raise CPAError("当前事件没有用户上下文，无法开始登录。")
    await cancel_local(key, notify=False)

    target = await send_secret(bot, event, _login_text(provider, payload))
    pending = PendingLogin(key=key, state=state, provider=provider, bot=bot, instance=instance, target=target)
    pending.task = asyncio.create_task(_poll(pending), name=f"cpa-oauth-{key}")
    _pending[key] = pending
    return target


def has_pending(bot: Bot, event: Event) -> bool:
    key = session_key(bot, event)
    return bool(key) and key in _pending


async def cancel_login(bot: Bot, event: Event) -> str:
    key = session_key(bot, event)
    pending = _pending.get(key)
    if pending is None:
        return "当前没有进行中的登录。"
    provider = pending.provider
    await cancel_local(key, notify=False)
    return f"已取消 [{provider}] 登录。"


async def submit_callback(bot: Bot, event: Event, text: str) -> str:
    pending = _pending.get(session_key(bot, event))
    if pending is None:
        raise CPAError("当前没有进行中的登录。请先发送 cpa login <渠道>。")
    url = extract_oauth_callback_url(text)
    if not url:
        raise CPAError("没有从消息里解析到回调链接。请发送浏览器地址栏的完整 URL。")
    logger.info("CPA oauth callback received for [{}] (url redacted)", pending.provider)
    try:
        await get_client(pending.instance).oauth_callback(
            redirect_url=url, provider=pending.provider, state=pending.state
        )
    except CPAError as exc:
        raise CPAError(f"提交回调失败：{exc}") from exc
    try:
        status = await get_client(pending.instance).auth_status(pending.state)
    except CPAError as exc:
        return f"回调已提交，但查询登录状态失败：{exc}"
    state = str(status.get("status") or "")
    if state == "ok":
        await cancel_local(pending.key, notify=False)
        return f"[{pending.provider}] 登录成功，凭证已写入 CPA。"
    if state == "wait":
        return f"[{pending.provider}] 回调已提交，仍在等待 CPA 完成。可继续等待或 cpa login cancel。"
    error = status.get("error") or "未知错误"
    return f"[{pending.provider}] 登录失败：{error}"


async def cancel_all() -> None:
    keys = list(_pending)
    for key in keys:
        await cancel_local(key, notify=False)


async def cancel_local(key: str, *, notify: bool) -> None:
    pending = _pending.pop(key, None)
    if pending is None:
        return
    task = pending.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if notify:
        try:
            await UniMessage(f"[{pending.provider}] 登录已取消。").send(
                target=pending.target, bot=pending.bot
            )
        except Exception as exc:
            logger.opt(exception=exc).warning("CPA oauth cancel notify failed")


def _login_text(provider: str, payload: dict[str, Any]) -> str:
    return format_login_prompt(provider, payload)


async def _poll(pending: PendingLogin) -> None:
    instance_cfg = state.get_snapshot().cpa.get(pending.instance)
    interval = max(1.0, instance_cfg.oauth_poll_interval if instance_cfg else 3.0)
    timeout = max(interval, instance_cfg.oauth_timeout if instance_cfg else 1800.0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        client = get_client(pending.instance)
    except CPAError:
        await _notify(pending, f"[{pending.provider}] 实例「{pending.instance}」已不存在，登录已中止。")
        _pending.pop(pending.key, None)
        return
    try:
        while loop.time() < deadline:
            await asyncio.sleep(interval)
            try:
                status = await client.auth_status(pending.state)
            except CPAError as exc:
                await _notify(pending, f"[{pending.provider}] 轮询登录状态失败：{exc}")
                return
            status_text = str(status.get("status") or "")
            if status_text == "wait":
                continue
            if status_text == "ok":
                await _notify(pending, f"[{pending.provider}] 登录成功，凭证已写入 CPA。")
                return
            error = status.get("error") or "未知错误"
            await _notify(pending, f"[{pending.provider}] 登录失败：{error}")
            return
        try:
            await client.cancel_oauth(pending.state)
        except CPAError:
            pass
        await _notify(
            pending,
            f"[{pending.provider}] 登录超时（约 {int(timeout)} 秒），会话已取消，请重试。",
        )
    except asyncio.CancelledError:
        try:
            await client.cancel_oauth(pending.state)
        except CPAError:
            pass
        raise
    except Exception as exc:
        logger.opt(exception=exc).warning("CPA oauth poll crashed")
        await _notify(pending, f"[{pending.provider}] 登录跟踪异常结束，请重试。")
    finally:
        current = _pending.get(pending.key)
        if current is pending:
            _pending.pop(pending.key, None)


async def _notify(pending: PendingLogin, text: str) -> None:
    try:
        await UniMessage(text).send(target=pending.target, bot=pending.bot)
    except Exception as extra:
        logger.opt(exception=extra).warning("CPA oauth notify failed")
