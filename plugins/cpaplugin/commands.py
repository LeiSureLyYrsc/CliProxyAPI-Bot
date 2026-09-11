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
from .quota import (
    PLATFORM_TITLES,
    QuotaBoard,
    collect_quotas,
    format_quota_board,
    is_platform_query,
    normalize_platform,
    peek_quota_cache,
    platform_of,
    clear_quota_cache,
    refresh_codex_quota,
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
    if not has_pending(bot, event):
        return
    try:
        if not await CPA_ADMIN(bot, event):
            return
    except Exception:
        return
    try:
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


def _text(query: Query[str]) -> str:
    return str(query.result).strip()


def _without(*paths: str):
    async def _check(_event: Event, _bot: Bot, _state: Any, result: Arparma) -> bool:
        return all(result.query(path, "\0") == "\0" for path in paths)

    return _check


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
            "quota",
            Args["query?", str],
            Option("--fresh|--refresh|-f", action=store_true, dest="fresh", help_text="忽略缓存强制刷新"),
            Option("--text|-t", action=store_true, dest="text", help_text="只发文字总览"),
            Subcommand("cooling", help_text="仅看冷却中的凭证"),
            Subcommand("reset", Args["query", str], help_text="清除配额/冷却并恢复路由"),
            help_text="按平台查询上游额度并汇总",
        ),
        Subcommand(
            "codex",
            Subcommand("refresh", Args["query", str], help_text="消耗一次 Codex 重置次数并刷新额度"),
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
            example="cpa status\ncpa auth list claude\ncpa alias set antigravity user@example.com AG-1\ncpa quota\ncpa quota antigravity\ncpa quota --fresh\ncpa quota --text\ncpa codex refresh user@example.com\ncpa login claude",
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
            "    渠道如 claude / codex / antigravity / kimi / xai。",
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
            "【额度】按平台出合并卡片图；一个平台一张。",
            "  cpa quota",
            "    全平台。Antigravity / Codex / xAI 等各发一张图。",
            "  cpa quota <平台>",
            "    只看一个平台：claude / codex / antigravity / kimi / xai",
            "  cpa quota <查询词>",
            "    单个账号的额度卡。",
            "  cpa quota --fresh    忽略 60 秒缓存，强制重查上游",
            "  cpa quota --text     只发文字总览（排障 / 无浏览器）",
            "  cpa quota cooling    只看本地冷却中的凭证",
            "  cpa quota reset <查询词>",
            "    清除该号配额/冷却并恢复路由。",
            "",
            "【Codex 重置】消耗官方重置次数，立刻刷新 5h/周窗口。",
            "  仅 CODEX_REFRESH_ADMIN 可执行；SUPERUSERS / CPA_ADMINS 不能代替该权限。",
            "  cpa codex refresh <查询词>",
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


@cpa.assign("quota.cooling")
async def quota_cooling() -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_quota_list([item for item in files if is_cooling(item)])).finish()


@cpa.assign("codex.refresh")
async def codex_refresh(event: Event, query: Query[str] = Query("codex.refresh.query")) -> None:
    if not _can_refresh_codex(event):
        await UniMessage("未配置 Codex_Refresh_Admin，或你不在名单中，无法刷新。").finish()
        return
    file = await _require_one(_text(query))
    try:
        message = await refresh_codex_quota(file)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(message).finish()


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
async def quota_view(arp: Arparma, query: Query[str] = Query("quota.query")) -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    platform = None
    target = files
    single = False
    if query.available:
        needle = _text(query)
        if is_platform_query(needle):
            platform = normalize_platform(needle)
            if not any(platform_of(item) == platform for item in files):
                await UniMessage(f"没有 {needle} 平台的凭证。").finish()
        else:
            matched = match_auth(files, needle)
            if not matched:
                await UniMessage(f"没有找到凭证：{needle}").finish()
            if len(matched) > 1:
                await UniMessage(format_ambiguous(needle, matched)).finish()
            target = matched
            single = True
    force = bool(arp.find("quota.fresh"))
    want_text = bool(arp.find("quota.text"))
    skip_disabled = not single
    board = None if force else peek_quota_cache(target, platform=platform, skip_disabled=skip_disabled)
    if board is None:
        await UniMessage("正在按平台查询上游额度，可能需要几秒…").send()
        try:
            board = await collect_quotas(
                target, platform=platform, force=force, skip_disabled=skip_disabled
            )
        except CPAError as exc:
            await UniMessage(str(exc)).finish()
    cfg = get_plugin_config(Config)
    if want_text or not cfg.cpa_quota_image:
        await _finish_quota_text(board)
    try:
        packed = await render_board_images(board)
    except RenderError as exc:
        await UniMessage(f"{exc}\n已回退为文字总览。").send()
        await _finish_quota_text(board)
    if not packed:
        await UniMessage("没有可展示的额度账号。").finish()
    cache_note = " · 缓存" if board.cached else ""
    outgoing: list[tuple[str, bytes]] = []
    for key, images in packed:
        title = PLATFORM_TITLES.get(key, key)
        total = len(images)
        for index, png in enumerate(images, start=1):
            extra = f" {index}/{total}" if total > 1 else ""
            outgoing.append((f"{title} 额度{extra}{cache_note}", png))
    for caption, png in outgoing[:-1]:
        await UniMessage(caption).send()
        await UniMessage(Image(raw=png, mimetype="image/png")).send()
    caption, png = outgoing[-1]
    await UniMessage(caption).send()
    await UniMessage(Image(raw=png, mimetype="image/png")).finish()


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
