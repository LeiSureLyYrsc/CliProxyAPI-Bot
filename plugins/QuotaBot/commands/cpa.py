"""/cpa 管理命令：凭证巡检、OAuth 登录、Codex 重置（保留原 cpa 命名空间）。"""

from __future__ import annotations

from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, Option, Subcommand, store_true
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot_plugin_alconna import Arparma, Query, UniMessage, on_alconna

from .. import state
from ..config import ServerConfig
from ..cpa.client import CPAError, get_client
from ..cpa.format import (
    display_name,
    format_ambiguous,
    format_auth_detail,
    format_auth_list,
    format_models,
    format_probe,
    format_reset_result,
    looks_like_oauth_callback,
    match_auth,
)
from ..cpa.oauth import (
    cancel_login,
    discover_auth_urls,
    has_pending,
    resolve_auth_path,
    start_login,
    submit_callback,
)
from ..cpa.quota import refresh_codex_quota
from ..hub import HubError, get_hub
from ..model import normalize_channel
from ..protocol import normalize_client_name, valid_client_name

from .common import CPA_ADMIN, _can_refresh_codex, _require_one, _require_platform_account, _set_disabled, _text, _without

cpa = on_alconna(
    Alconna(
        ["/", ""],
        "cpa",
        Subcommand("status", help_text="探活与凭证概览"),
        Subcommand(
            "auth",
            Subcommand(
                "list",
                Args["provider?", str],
                Option("--disabled", action=store_true, dest="disabled", help_text="包含已禁用账号"),
                help_text="凭证摘要列表",
            ),
            Subcommand("show", Args["query", str], help_text="凭证详情"),
            Subcommand("on|enable", Args["query", str], dest="on", help_text="启用凭证"),
            Subcommand("off|disable", Args["query", str], dest="off", help_text="禁用凭证"),
            Subcommand("models", Args["query", str], help_text="凭证支持的模型"),
            Subcommand(
                "delete",
                Args["query", str],
                Option("--yes|-y", action=store_true, help_text="确认删除"),
                help_text="删除凭证文件",
            ),
            help_text="凭证管理",
        ),
        Subcommand(
            "codex",
            Subcommand(
                "refresh",
                Args["query", str],
                Option("--client|-c", Args["client", str], dest="client", help_text="指定客户端名称"),
                help_text="消耗一次 Codex 重置次数并刷新额度",
            ),
            help_text="Codex 上游额度操作",
        ),
        Subcommand(
            "login",
            Subcommand("cancel", help_text="取消进行中的登录"),
            Subcommand("callback", Args["url", str], help_text="提交浏览器回调链接"),
            Args["provider?", str],
            help_text="OAuth / 设备码登录",
        ),
        meta=CommandMeta(
            description="CLIProxyAPI 管理（仅管理员）",
            usage="发送 cpa 或 /cpa 查看完整帮助",
            example="cpa status\ncpa auth list claude\ncpa codex refresh user@example.com\ncpa codex refresh user@example.com --client Home\ncpa login claude",
        ),
    ),
    permission=CPA_ADMIN,
    auto_send_output=True,
    skip_for_unmatch=False,
    use_cmd_start=True,
    block=True,
)


@event_preprocessor
async def _capture_oauth_callback(bot: Bot, event: Event) -> None:
    try:
        if getattr(event, "post_type", "") == "message_sent":
            return
        if not has_pending(bot, event):
            return
        if not await CPA_ADMIN(bot, event):
            return
        text = event.get_plaintext().strip()
    except Exception:
        return
    if not text or text.lower().startswith(("cpa ", "/cpa ", "quota ", "/quota ")):
        return
    if not looks_like_oauth_callback(text):
        return
    try:
        message = await submit_callback(bot, event, text)
    except CPAError as exc:
        await UniMessage(str(exc)).send()
        raise IgnoredException("cpa oauth callback") from exc
    await UniMessage(message).send()
    raise IgnoredException("cpa oauth callback")


@cpa.assign("$main")
async def cpa_help() -> None:
    providers = "（暂时无法获取，CPA 未连通时仍可看本帮助）"
    try:
        mapping = await discover_auth_urls()
        known = ", ".join(sorted(set(mapping)))
        if known:
            providers = known
    except CPAError:
        pass
    await UniMessage(_cpa_help_text(providers)).finish()


def _cpa_help_text(providers: str) -> str:
    return "\n".join(
        [
            "CLIProxyAPI 管理（仅超级用户 / admins）",
            "命令前缀 / 可有可无：/cpa 与 cpa 相同。",
            "",
            "【探活】",
            "  cpa status",
            "    版本、凭证 ready / 禁用 / 冷却计数。不回传配置正文。",
            "",
            "【凭证】",
            "  cpa auth list [渠道] [--disabled]",
            "    摘要列表。默认隐藏已禁用账号；加 --disabled 才显示。",
            "    渠道如 claude / codex(gpt, openai) / antigravity(反重力) / kimi / xai。",
            "  cpa auth show <查询词>",
            "    单条详情（含原始邮箱，仅管理员对照用）。",
            "  cpa auth on|off <查询词>",
            "    启用 / 禁用。enable / disable 同义。",
            "  cpa auth models <查询词>",
            "    该凭证支持的模型。",
            "  cpa auth delete <查询词> --yes",
            "    删除磁盘凭证。没有 --yes 只预告，不会真删。",
            "",
            "【Codex 重置】消耗官方重置次数，立刻刷新 5h/周窗口。",
            "  仅 codex_refresh_admin 可执行；SUPERUSERS / admins 不能代替该权限。",
            "  cpa codex refresh <查询词> [--client|-c <客户端>]",
            "    查询词：邮箱、别名、文件名、auth_index。只匹配 Codex 账号。",
            "",
            "【登录】授权链接优先私聊。不要加 is_webui。",
            f"  可用渠道：{providers}",
            "  cpa login <渠道>     例：cpa login claude",
            "    浏览器会跳到 localhost。完成后把地址栏完整回调链接发到当前聊天。",
            "  cpa login callback <回调链接>",
            "  cpa login cancel     取消进行中的登录",
            "",
            "【额度】请用 /quota 命令。",
        ]
    )


@cpa.assign("status")
async def cpa_status() -> None:
    client = get_client()
    try:
        headers = await client.probe()
        files = await client.list_auth_files()
        latest = await client.latest_version()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_probe(headers, files, latest)).finish()


@cpa.assign("auth.list")
async def auth_list(
    arp: Arparma,
    provider: Query[str] = Query("auth.list.provider"),
) -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    if provider.available:
        needle = provider.result.strip().lower()
        files = [item for item in files if str(item.get("provider") or "").lower() == needle]
        if not files:
            await UniMessage(f"没有 provider={provider.result} 的凭证。").finish()
            return
    await UniMessage(format_auth_list(files, include_disabled=bool(arp.find("auth.list.disabled")))).finish()


@cpa.assign("auth.show")
async def auth_show(query: Query[str] = Query("auth.show.query")) -> None:
    file = await _require_one(_text(query))
    await UniMessage(format_auth_detail(file)).finish()


@cpa.assign("auth.on")
async def auth_on(query: Query[str] = Query("auth.on.query")) -> None:
    await _set_disabled(_text(query), disabled=False)


@cpa.assign("auth.off")
async def auth_off(query: Query[str] = Query("auth.off.query")) -> None:
    await _set_disabled(_text(query), disabled=True)


@cpa.assign("auth.models")
async def auth_models(query: Query[str] = Query("auth.models.query")) -> None:
    file = await _require_one(_text(query))
    name = str(file.get("name") or "")
    if not name:
        await UniMessage("该凭证没有文件名，无法查询模型列表。").finish()
    try:
        models = await get_client().auth_models(name)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_models(models)).finish()


@cpa.assign("auth.delete")
async def auth_delete(arp: Arparma, query: Query[str] = Query("auth.delete.query")) -> None:
    file = await _require_one(_text(query))
    confirmed = arp.find("auth.delete.yes")
    name = str(file.get("name") or "")
    if not name:
        await UniMessage("该凭证没有可删除的磁盘文件（可能是 runtime_only）。").finish()
    if not confirmed:
        await UniMessage(
            f"即将删除凭证 {name}。确认请发送：\ncpa auth delete {query.result} --yes"
        ).finish()
    try:
        await get_client().delete_auth_file(name)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(f"已删除 {name}。").finish()


def resolve_codex_refresh_target(
    raw_query: str,
    raw_client: str | None,
    server: ServerConfig,
    known_clients: set[str],
) -> tuple[str, str, str]:
    """解析 codex refresh 参数，返回 (target_client, query, error_message)。"""
    account_query = raw_query.strip()
    if not account_query:
        return "", "", "查询词不能为空。"
    client_param = (raw_client or "").strip()
    if not client_param:
        return server.client_name, account_query, ""
    normalized_client = normalize_client_name(client_param)
    if not valid_client_name(normalized_client):
        return "", "", f"客户端名称非法：{client_param}"
    if normalized_client not in known_clients:
        return "", "", f"未知客户端：{client_param}"
    return normalized_client, account_query, ""


@cpa.assign("codex.refresh")
async def codex_refresh(
    event: Event,
    query: Query[str] = Query("codex.refresh.query"),
    client: Query[str] = Query("codex.refresh.client"),
) -> None:
    if not _can_refresh_codex(event):
        await UniMessage("未配置 codex_refresh_admin，或你不在名单中，无法刷新。").finish()
        return
    snapshot = state.get_snapshot()
    server = snapshot.server
    hub = get_hub()
    known = hub.known_names(server)
    target_client, account_query, err = resolve_codex_refresh_target(
        _text(query),
        _text(client) if client.available else None,
        server,
        known,
    )
    if err:
        await UniMessage(err).finish()
        return
    if target_client == server.client_name:
        file = await _require_platform_account("codex", account_query)
        try:
            message = await refresh_codex_quota(file)
        except CPAError as exc:
            await UniMessage(str(exc)).finish()
            return
        await UniMessage(message).finish()
        return
    try:
        res = await hub.refresh_codex(target_client, account_query)
    except (CPAError, HubError) as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(res.message).finish()


@cpa.assign("login.cancel")
async def login_cancel(bot: Bot, event: Event) -> None:
    try:
        message = await cancel_login(bot, event)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(message).finish()


@cpa.assign("login.callback")
async def login_callback(bot: Bot, event: Event, url: Query[str] = Query("login.callback.url")) -> None:
    try:
        message = await submit_callback(bot, event, _text(url))
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(message).finish()


@cpa.assign("login", additional=_without("login.cancel", "login.callback"))
async def login_start(bot: Bot, event: Event, provider: Query[str] = Query("login.provider")) -> None:
    if not provider.available:
        mapping = await discover_auth_urls()
        known = ", ".join(sorted(set(mapping)))
        await UniMessage(f"用法：cpa login <渠道>\n可用渠道：{known or '（无法获取）'}").finish()
        return
    try:
        canonical, path = await resolve_auth_path(_text(provider))
        payload = await get_client().start_login(path)
        await start_login(bot, event, canonical, payload)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
