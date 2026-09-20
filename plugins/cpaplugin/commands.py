from __future__ import annotations

from typing import Any

from arclet.alconna import Alconna, Args, CommandMeta, Option, Subcommand, store_true
from nonebot import get_plugin_config
from nonebot.adapters import Bot, Event
from nonebot.message import event_preprocessor
from nonebot.permission import SUPERUSER, Permission
from nonebot.exception import IgnoredException
from nonebot_plugin_alconna import Arparma, Image, Query, UniMessage, on_alconna

from .client import CPAError, get_client
from .config import Config
from .aliases import delete_alias, format_alias_list, set_alias
from .format import (
    display_name,
    format_ambiguous,
    format_auth_detail,
    format_auth_list,
    format_models,
    format_probe,
    format_quota_list,
    format_reset_result,
    is_cooling,
    looks_like_oauth_callback,
    match_auth,
)
from .oauth import (
    cancel_login,
    discover_auth_urls,
    has_pending,
    resolve_auth_path,
    start_login,
    submit_callback,
)
from .hub import HubError, get_hub
from .protocol import normalize_client_name, valid_client_name
from .query import QuotaSelection, parse_quota_command
from .render_settings import (
    get_render_settings,
    set_cards_per_row,
    set_theme,
)
from .theme_loader import get_theme_registry
from .quota import (
    PLATFORM_TITLES,
    QuotaBoard,
    accounts_from_result,
    board_from_accounts,
    collect_quotas,
    format_quota_board,
    normalize_platform,
    peek_quota_cache,
    platform_of,
    clear_quota_cache,
    refresh_codex_quota,
    stamp_client,
)
from .render import RenderError, render_board_images


async def _extra_admin(event: Event) -> bool:
    try:
        user_id = event.get_user_id()
    except Exception:
        return False
    return user_id in get_plugin_config(Config).cpa_admins


CPA_ADMIN = SUPERUSER | Permission(_extra_admin)


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
    if not text or text.lower().startswith(("cpa ", "/cpa ")):
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


def _can_refresh_codex(event: Event) -> bool:
    try:
        user_id = event.get_user_id()
    except Exception:
        return False
    admins = get_plugin_config(Config).codex_refresh_admin
    return bool(admins) and user_id in admins


def _text(query: Query[Any]) -> str:
    raw = query.result
    if isinstance(raw, (list, tuple)):
        return " ".join(str(item).strip() for item in raw if str(item).strip()).strip()
    return str(raw).strip()


def _without(*paths: str):
    async def _check(_event: Event, _bot: Bot, _state: Any, result: Arparma) -> bool:
        return all(result.query(path, "\0") == "\0" for path in paths)

    return _check


cpa = on_alconna(
    Alconna(
        ["/", ""],
        "cpa",
        Option(
            "--all|-all|-a",
            action=store_true,
            dest="quota_all_passthrough",
            help_text="兼容 cpa quota <平台> --all 的后置写法",
        ),
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
            "alias",
            Subcommand(
                "list",
                Option("--disabled", action=store_true, dest="disabled", help_text="包含已禁用账号"),
                help_text="列出账号别名",
            ),
            Subcommand(
                "set",
                Args["a", str]["b", str]["c?", str],
                help_text="设置别名：cpa alias set <渠道> <邮箱> <别名>",
            ),
            Subcommand("del|rm|delete", Args["query", str], dest="delete", help_text="删除别名"),
            help_text="账号显示别名，避免聊天里出现邮箱",
        ),
        Subcommand(
            "theme",
            Subcommand("set", Args["name", str], help_text="设置额度图主题：cpa theme set <主题>"),
            help_text="查看或设置额度图主题",
        ),
        Subcommand(
            "card",
            Subcommand("row", Args["count", str], help_text="设置每行卡片数：cpa card row 1..6"),
            help_text="查看或设置卡片布局排版",
        ),
        Subcommand(
            "quota",
            Option("--fresh|--refresh|-f", action=store_true, dest="fresh", help_text="忽略缓存强制刷新"),
            Option("--text|-t", action=store_true, dest="text", help_text="只发文字总览"),
            Option("--all|-all|-a", action=store_true, dest="all_clients", help_text="查询所有在线客户端"),
            Option("--client|-c", Args["client", str], dest="client", help_text="指定客户端名称"),
            Subcommand("cooling", help_text="仅看冷却中的凭证"),
            Subcommand("reset", Args["query", str], help_text="清除配额/冷却并恢复路由"),
            Args["a?", str]["b?", str],
            help_text="按平台查询上游额度并汇总",
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
            description="CliProxyAPI 管理（仅管理员）",
            usage="发送 cpa 或 /cpa 查看完整帮助",
            example="cpa status\ncpa auth list claude\ncpa alias set antigravity user@example.com AG-1\ncpa quota\ncpa quota antigravity\ncpa quota Home\ncpa quota antigravity Home\ncpa quota --all\ncpa quota --fresh\ncpa quota --text\ncpa codex refresh user@example.com\ncpa codex refresh user@example.com --client Home\ncpa login claude",
        ),
    ),
    permission=CPA_ADMIN,
    auto_send_output=True,
    skip_for_unmatch=False,
    use_cmd_start=True,
    block=True,
)


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
            "CliProxyAPI 管理（仅超级用户 / CPA_ADMINS）",
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
            "【别名】聊天和额度图默认不显示邮箱；未设别名时为「渠道-短索引」。",
            "  cpa alias list [--disabled]",
            "    默认隐藏已禁用账号的别名。",
            "  cpa alias set <渠道> <邮箱> <别名>",
            "    例：cpa alias set antigravity user@example.com AG-1",
            "    同邮箱跨渠道必须带渠道，避免串号。",
            "  cpa alias del <查询词>",
            "",
            "【主题与排版】修改后立刻生效并持久化保存。",
            "  cpa theme",
            "    查看当前额度图主题与可选主题列表。",
            "  cpa theme set <主题>",
            "    设置额度图主题（如 default, mac, md3, winxp, win7 等）。",
            "  cpa card",
            "    查看当前卡片排版设置。",
            "  cpa card row N",
            "    设置每行展示卡片数（1..6）。",
            "",
            "【额度】按平台出合并卡片图；一个平台一张。危险操作不会发到远程客户端。",
            "  cpa quota",
            "    本机客户端（默认名 Server）全平台。",
            "  cpa quota <平台>",
            "    只看一个平台：claude / codex(gpt, openai) / antigravity(反重力) / kimi / xai",
            "  cpa quota <客户端>",
            "    查询指定在线客户端。例：cpa quota Home",
            "  cpa quota <平台> <客户端>",
            "    例：cpa quota antigravity Home  或  cpa quota Home antigravity",
            "  cpa quota --client|-c <客户端>",
            "    显式指定客户端，避免与平台名冲突。",
            "  cpa quota --all|-all|-a",
            "    分别查询本机与所有在线客户端，按客户端分组展示。",
            "  cpa quota <平台> --all",
            "    每个客户端只查该平台。",
            "  cpa quota <查询词>",
            "    单个账号的额度卡（本机或指定客户端）。",
            "  cpa quota --fresh    忽略 60 秒缓存，强制重查上游",
            "  cpa quota --text     只发文字总览（排障 / 无浏览器）",
            "  cpa quota cooling    只看本机冷却中的凭证",
            "  cpa quota reset <查询词>",
            "    清除本机该号配额/冷却并恢复路由。远程客户端不可用。",
            "",
            "【Codex 重置】消耗官方重置次数，立刻刷新 5h/周窗口。",
            "  仅 CODEX_REFRESH_ADMIN 可执行；SUPERUSERS / CPA_ADMINS 不能代替该权限。",
            "  cpa codex refresh <查询词> [--client|-c <客户端>]",
            "    查询词：邮箱、别名、文件名、auth_index（不传 auth_index 给远程，只传查询词）。只匹配 Codex 账号。",
            "    远程客户端需开启 CODEX_REFRESH_ENABLED=true。",
            "",
            "【登录】授权链接优先私聊。不要加 is_webui。",
            f"  可用渠道：{providers}",
            "  cpa login <渠道>     例：cpa login claude",
            "    浏览器会跳到 localhost。完成后把地址栏完整回调链接发到当前聊天。",
            "  cpa login callback <回调链接>",
            "  cpa login cancel     取消进行中的登录",
            "",
            "【查询词】邮箱、文件名、label、别名、auth_index（可只写前缀）。",
            "匹配到多条时会列出候选项，请写得更精确。",
            "",
            "【注意】",
            "  · 额度合计是剩余当量，不是把百分比加在一起。",
            "  · 未装 Chromium 时额度图会回退文字：playwright install chromium",
            "  · 不要把 usage-queue 当查用量（会弹出队列）。",
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


@cpa.assign("alias.list")
async def alias_list(arp: Arparma) -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(
        format_alias_list(files, include_disabled=bool(arp.find("alias.list.disabled")))
    ).finish()


@cpa.assign("alias.set")
async def alias_set(
    a: Query[str] = Query("alias.set.a"),
    b: Query[str] = Query("alias.set.b"),
    c: Query[str] = Query("alias.set.c"),
) -> None:
    first = _text(a)
    second = _text(b)
    third = _text(c) if c.available else ""
    if third:
        file = await _require_platform_account(first, second)
        alias = third
    else:
        file = await _require_one(first)
        alias = second
    try:
        name = set_alias(file, alias)
    except ValueError as exc:
        await UniMessage(str(exc)).finish()
        return
    clear_quota_cache()
    await UniMessage(
        f"已设置别名「{name}」。\n"
        f"聊天/额度图将显示该名称，不再使用邮箱。查看：cpa alias list"
    ).finish()


@cpa.assign("alias.delete")
async def alias_delete(query: Query[str] = Query("alias.delete.query")) -> None:
    file = await _require_one(_text(query))
    shown = display_name(file, public=True)
    if not delete_alias(file):
        await UniMessage(f"{shown} 没有别名。").finish()
    clear_quota_cache()
    await UniMessage(f"已删除 {shown} 的别名。额度图将改用渠道短索引。").finish()


@cpa.assign("theme.set")
async def cpa_theme_set(name: Query[str] = Query("theme.set.name")) -> None:
    theme_name = _text(name)
    try:
        settings = set_theme(theme_name)
    except ValueError as exc:
        allowed = " / ".join(get_theme_registry().list_canonical_names())
        await UniMessage(f"{exc}\n可用主题：{allowed}").finish()
        return
    await UniMessage(f"已将额度图主题设置为「{settings.theme}」。").finish()


@cpa.assign("theme", additional=_without("theme.set"))
async def cpa_theme_get() -> None:
    settings = get_render_settings()
    registry = get_theme_registry()
    allowed = " / ".join(registry.list_canonical_names())
    aliases = registry.get_alias_map()
    alias_notes = [
        f"{alias} → {canonical}"
        for alias, canonical in sorted(aliases.items())
        if alias != canonical
    ]
    alias_text = f"\n别名：{' / '.join(alias_notes)}" if alias_notes else ""
    await UniMessage(
        f"当前额度图主题：{settings.theme}\n"
        f"可选主题：{allowed}{alias_text}\n"
        f"修改主题：cpa theme set <主题>"
    ).finish()


@cpa.assign("card.row")
async def cpa_card_row(count: Query[str] = Query("card.row.count")) -> None:
    val = _text(count)
    try:
        settings = set_cards_per_row(val)
    except ValueError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(f"已设置每行展示 {settings.cards_per_row} 张卡片。").finish()


@cpa.assign("card", additional=_without("card.row"))
async def cpa_card_get() -> None:
    settings = get_render_settings()
    await UniMessage(f"当前每行卡片数：{settings.cards_per_row} (1..6)\n修改排版：cpa card row <数量>").finish()


@cpa.assign("quota.cooling")
async def quota_cooling() -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_quota_list([item for item in files if is_cooling(item)])).finish()


def resolve_codex_refresh_target(
    raw_query: str,
    raw_client: str | None,
    cfg: Config,
    known_clients: set[str],
) -> tuple[str, str, str]:
    """解析 codex refresh 参数，返回 (target_client, query, error_message)。"""
    account_query = raw_query.strip()
    if not account_query:
        return "", "", "查询词不能为空。"
    client_param = (raw_client or "").strip()
    if not client_param:
        return cfg.client_name, account_query, ""
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
        await UniMessage("未配置 CODEX_REFRESH_ADMIN，或你不在名单中，无法刷新。").finish()
        return
    cfg = get_plugin_config(Config)
    hub = get_hub()
    known = hub.known_names(cfg)
    target_client, account_query, err = resolve_codex_refresh_target(
        _text(query),
        _text(client) if client.available else None,
        cfg,
        known,
    )
    if err:
        await UniMessage(err).finish()
        return
    if target_client == cfg.client_name:
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


@cpa.assign("quota.reset")
async def quota_reset(query: Query[str] = Query("quota.reset.query")) -> None:
    file = await _require_one(_text(query))
    auth_index = str(file.get("auth_index") or "")
    if not auth_index:
        await UniMessage("该凭证没有 auth_index，无法 reset-quota。").finish()
    try:
        result = await get_client().reset_quota(auth_index)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_reset_result(result, file)).finish()


@cpa.assign("quota", additional=_without("quota.cooling", "quota.reset"))
async def quota_view(event: Event) -> None:
    cfg = get_plugin_config(Config)
    hub = get_hub()
    known = hub.known_names(cfg)
    selection = parse_quota_command(
        event.get_plaintext(),
        known_clients=known,
        default_client=cfg.client_name,
    )
    if selection.error:
        await UniMessage(selection.error).finish()
    names = _quota_targets(cfg, selection)
    if not names:
        await UniMessage("没有可查询的客户端。").finish()
    await UniMessage("正在按平台查询上游额度，可能需要几秒…").send()
    results: list[tuple[str, QuotaBoard | str]] = []
    for name in names:
        try:
            if name == cfg.client_name:
                board = await _local_quota_board(selection)
            else:
                board = await _remote_quota_board(name, selection)
            results.append((name, board))
        except (CPAError, HubError) as exc:
            results.append((name, str(exc)))
    await _send_quota_results(cfg, results, want_text=selection.text, multi=len(names) > 1)


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


def _quota_targets(cfg: Config, selection: QuotaSelection) -> list[str]:
    local = cfg.client_name
    if selection.all_clients:
        names = [local]
        for name in get_hub().online_names():
            if name != local:
                names.append(name)
        return names
    return [selection.client_name or local]


async def _local_quota_board(selection: QuotaSelection) -> QuotaBoard:
    files = await get_client().list_auth_files()
    platform = selection.platform
    target = files
    single = False
    if selection.account:
        matched = match_auth(files, selection.account)
        if not matched:
            raise CPAError(f"没有找到凭证：{selection.account}")
        if len(matched) > 1:
            raise CPAError(format_ambiguous(selection.account, matched))
        target = matched
        single = True
    elif platform:
        target = [item for item in files if platform_of(item) == platform]
        if not target:
            raise CPAError(f"没有 {platform} 平台的凭证。")
    force = selection.fresh
    skip_disabled = not single
    board = None if force else peek_quota_cache(target, platform=platform, skip_disabled=skip_disabled)
    if board is None:
        board = await collect_quotas(target, platform=platform, force=force, skip_disabled=skip_disabled)
    return stamp_client(board, get_plugin_config(Config).client_name)


async def _remote_quota_board(name: str, selection: QuotaSelection) -> QuotaBoard:
    result = await get_hub().query_quota(
        name,
        platform=selection.platform,
        account=selection.account,
        fresh=selection.fresh,
    )
    return board_from_accounts(accounts_from_result(result), cached=result.cached)


async def _send_quota_results(
    cfg: Config,
    results: list[tuple[str, QuotaBoard | str]],
    *,
    want_text: bool,
    multi: bool,
) -> None:
    prefix = multi or cfg.server_mode
    outgoing: list[tuple[str, bytes | None]] = []
    for name, item in results:
        if isinstance(item, str):
            outgoing.append((f"[{name}] {item}" if prefix else item, None))
            continue
        label = f"[{name}] " if prefix else ""
        if want_text or not cfg.cpa_quota_image:
            chunks = format_quota_board(item)
            outgoing.extend((f"{label}{chunk}" if label else chunk, None) for chunk in chunks)
            continue
        try:
            packed = await render_board_images(item)
        except RenderError as exc:
            outgoing.append((f"{label}{exc}\n已回退为文字总览。" if label else f"{exc}\n已回退为文字总览。", None))
            chunks = format_quota_board(item)
            outgoing.extend((f"{label}{chunk}" if label else chunk, None) for chunk in chunks)
            continue
        if not packed:
            outgoing.append((f"{label}没有可展示的额度账号。" if label else "没有可展示的额度账号。", None))
            continue
        cache_note = " · 缓存" if item.cached else ""
        for key, images in packed:
            title = PLATFORM_TITLES.get(key, key)
            total = len(images)
            for index, png in enumerate(images, start=1):
                extra = f" {index}/{total}" if total > 1 else ""
                outgoing.append((f"{label}{title} 额度{extra}{cache_note}", png))
    if not outgoing:
        await UniMessage("没有可展示的额度账号。").finish()
        return
    for caption, png in outgoing[:-1]:
        await UniMessage(caption).send()
        if png is not None:
            await UniMessage(Image(raw=png, mimetype="image/png")).send()
    caption, png = outgoing[-1]
    if png is None:
        await UniMessage(caption).finish()
        return
    await UniMessage(caption).send()
    await UniMessage(Image(raw=png, mimetype="image/png")).finish()


async def _finish_quota_text(board: QuotaBoard) -> None:
    chunks = format_quota_board(board)
    for chunk in chunks[:-1]:
        await UniMessage(chunk).send()
    await UniMessage(chunks[-1]).finish()


async def _set_disabled(query: str, *, disabled: bool) -> None:
    file = await _require_one(query)
    name = str(file.get("name") or "")
    if not name:
        await UniMessage("该凭证没有文件名，无法改状态（配置型 API-key / 插件虚拟项请用其它方式）。").finish()
    try:
        await get_client().patch_auth_status(
            name,
            disabled,
            auth_index=str(file.get("auth_index") or "") or None,
        )
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    action = "已禁用" if disabled else "已启用"
    await UniMessage(f"{action} {name}。").finish()


async def _require_one(query: str) -> dict[str, Any]:
    try:
        files = await _lookup(query)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        raise
    if not files:
        await UniMessage(f"没有找到凭证：{query}").finish()
        raise CPAError(f"没有找到凭证：{query}")
    if len(files) > 1:
        await UniMessage(format_ambiguous(query, files)).finish()
        raise CPAError("匹配到多个凭证")
    return files[0]


async def _require_platform_account(provider: str, query: str) -> dict[str, Any]:
    platform = normalize_platform(provider)
    if not platform:
        await UniMessage(
            f"未知渠道「{provider}」。用法：cpa alias set <渠道> <邮箱> <别名>\n"
            "渠道如：claude / codex / antigravity / kimi / xai"
        ).finish()
        raise CPAError(f"未知渠道：{provider}")
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        raise
    scoped = [item for item in files if platform_of(item) == platform]
    matched = match_auth(scoped, query)
    if not matched:
        await UniMessage(f"没有找到 [{platform}] 凭证：{query}").finish()
        raise CPAError(f"没有找到凭证：{query}")
    if len(matched) > 1:
        await UniMessage(format_ambiguous(f"{platform} {query}", matched)).finish()
        raise CPAError("匹配到多个凭证")
    return matched[0]


async def _lookup(query: str) -> list[dict[str, Any]]:
    client = get_client()
    needle = query.strip()
    attempts: list[dict[str, str]] = [{"auth_index": needle}, {"name": needle}]
    if not needle.endswith(".json"):
        attempts.append({"name": f"{needle}.json"})
    for kwargs in attempts:
        try:
            files = await client.list_auth_files(**kwargs)
        except CPAError as exc:
            if "404" not in str(exc):
                raise
            files = []
        if files:
            return files
    files = await client.list_auth_files()
    return match_auth(files, needle)
