"""/quota 根命令：额度查询 + 别名 / 主题 / 卡片 / 配置子命令。

根前缀固定为 `/`（用户要求“quota 必须使用指令头”），裸 `quota` 不匹配。
查询主体（平台 / 客户端 / 账号 / --fresh 等）由 ``query.parse_quota_command``
从 plaintext 解析，因此根上不再声明 --fresh/--text/--all/--client 等 Option，
避免 `$main` 因 components 非空而不触发。
"""

from __future__ import annotations

from typing import Any, Sequence

from arclet.alconna import Alconna, Args, CommandMeta, MultiVar, Option, Subcommand, store_true
from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import Arparma, Image, Query, UniMessage, on_alconna

from .. import state
from ..config import CpaConfig, ServerConfig, VolcengineAccount
from ..cpa.client import CPAError, get_client
from ..cpa.format import format_ambiguous, format_quota_list, is_cooling, match_auth
from ..cpa.quota import (
    PLATFORM_TITLES,
    QuotaBoard,
    accounts_from_result,
    board_from_accounts,
    clear_quota_cache,
    collect_quotas,
    format_quota_board,
    peek_quota_cache,
    platform_of,
    stamp_client,
)
from ..hub import HubError, get_hub
from ..query import QuotaSelection, parse_quota_command
from ..render.html import RenderError, render_board_images
from ..volcengine.provider import collect_board as collect_volcengine_board

from .common import CPA_ADMIN, _require_one, _text

# --------------------------------------------------------------------------- #
# 子命令 Alconna 结构（别名/主题/卡片/配置在各自模块里挂 handler）
# --------------------------------------------------------------------------- #

quota = on_alconna(
    Alconna(
        ["/"],
        "quota",
        Subcommand("cooling", help_text="仅看冷却中的凭证"),
        Subcommand("reset", Args["query", str], help_text="清除配额/冷却并恢复路由"),
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
                help_text="设置别名：/quota alias set <渠道> <查询词> <别名>",
            ),
            Subcommand("del|rm|delete", Args["query", str], dest="delete", help_text="删除别名"),
            help_text="分渠道账号别名",
        ),
        Subcommand(
            "theme",
            Subcommand("set", Args["name", str], help_text="设置额度图主题：/quota theme set <主题>"),
            help_text="查看或设置额度图主题",
        ),
        Subcommand(
            "card",
            Subcommand("row", Args["count", str], help_text="设置每行卡片数：/quota card row 1..6"),
            help_text="查看或设置卡片布局排版",
        ),
        Subcommand(
            "config",
            Subcommand("show", help_text="查看当前生效配置（密钥脱敏）"),
            Subcommand("reload", help_text="强制重载配置"),
            help_text="配置查看与重载",
        ),
        Args["a?", str]["b?", str]["tail", MultiVar(str, "*")],
        meta=CommandMeta(
            description="额度查询（仅管理员）",
            usage="发送 /quota 查看帮助；/quota 火山 查火山方舟",
            example="/quota\n/quota 火山\n/quota claude\n/quota claude Home\n/quota --fresh\n/quota cooling\n/quota reset user@example.com\n/quota alias set antigravity user@example.com AG-1\n/quota theme\n/quota theme set md3\n/quota card row 4\n/quota config show",
        ),
    ),
    permission=CPA_ADMIN,
    auto_send_output=True,
    skip_for_unmatch=False,
    use_cmd_start=True,
    block=True,
)


@quota.assign("$main")
async def quota_main(arp: Arparma, event: Event) -> None:
    # MultiVar 在无剩余 token 时也会填充 tail=()，因此不能只看 bool(main_args)。
    if _should_show_help(arp.main_args):
        await UniMessage(_quota_help_text()).finish()
    await quota_view(event)


def _should_show_help(main_args: dict[str, Any]) -> bool:
    """判断 /quota 主命令是否没有任何查询参数（此时展示帮助）。"""
    a = main_args.get("a")
    b = main_args.get("b")
    tail = main_args.get("tail") or ()
    return not (a or b or tail)


# --------------------------------------------------------------------------- #
# 帮助
# --------------------------------------------------------------------------- #


def _quota_help_text() -> str:
    return "\n".join(
        [
            "QuotaBot 额度查询（仅超级用户 / admins）",
            "命令固定带 / 前缀（指令头）。",
            "",
            "【查询】",
            "  /quota",
            "    本机客户端全平台额度汇总。",
            "  /quota <平台>",
            "    claude / codex(gpt, openai) / antigravity(反重力) / kimi / xai / 火山(volcengine, ark)",
            "  /quota <客户端>",
            "    查询指定在线客户端。例：/quota Home",
            "  /quota <平台> <客户端>",
            "    例：/quota antigravity Home  或  /quota Home antigravity",
            "  /quota <查询词>",
            "    单个账号的额度卡。",
            "  /quota --fresh     忽略缓存，强制重查上游",
            "  /quota --text      只发文字总览（排障 / 无浏览器）",
            "  /quota --all       分别查询本机与所有在线客户端",
            "  /quota --client <客户端>   显式指定客户端，避免与平台名冲突",
            "  /quota cooling     只看本机冷却中的凭证",
            "  /quota reset <查询词>   清除配额/冷却并恢复路由（远程客户端不可用）",
            "",
            "【别名】分渠道存储（data/quota_aliases.json）。",
            "  /quota alias list [--disabled]",
            "  /quota alias set <渠道> <查询词> <别名>",
            "    例：/quota alias set antigravity user@example.com AG-1",
            "  /quota alias del <查询词>    删除（跨渠道全部删除）",
            "",
            "【主题与排版】修改后立刻生效并持久化。",
            "  /quota theme           查看当前主题与可选主题",
            "  /quota theme set <主题>",
            "  /quota card            查看每行卡片数",
            "  /quota card row N      设置每行卡片数（1..6）",
            "",
            "【配置】",
            "  /quota config show     查看生效配置（密钥脱敏）与最近解析错误",
            "  /quota config reload   强制从磁盘重载配置",
            "",
            "【管理】CPA 凭证 / 登录 / Codex 重置请用 /cpa。",
        ]
    )


# --------------------------------------------------------------------------- #
# 子命令：cooling / reset
# --------------------------------------------------------------------------- #


@quota.assign("cooling")
async def quota_cooling() -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(format_quota_list([item for item in files if is_cooling(item)])).finish()


@quota.assign("reset")
async def quota_reset(query: Query[str] = Query("reset.query")) -> None:
    file = await _require_one(_text(query))
    auth_index = str(file.get("auth_index") or "")
    if not auth_index:
        await UniMessage("该凭证没有 auth_index，无法 reset-quota。").finish()
    try:
        result = await get_client().reset_quota(auth_index)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
    await UniMessage(_format_reset_result(result, file)).finish()


def _format_reset_result(data: Any, file: dict[str, Any]) -> str:
    from ..cpa.format import display_name

    models = []
    if isinstance(data, dict):
        raw = data.get("models")
        if isinstance(raw, list):
            models = [str(item) for item in raw]
    extra = f"\n已恢复模型：{', '.join(models)}" if models else ""
    return f"已清除 {display_name(file, public=True)} 的配额/冷却。{extra}"


# --------------------------------------------------------------------------- #
# 查询主体
# --------------------------------------------------------------------------- #


async def quota_view(event: Event) -> None:
    snapshot = state.get_snapshot()
    server = snapshot.server
    hub = get_hub()
    known = hub.known_names(server)
    selection = parse_quota_command(
        event.get_plaintext(),
        known_clients=known,
        default_client=server.client_name,
    )
    if selection.error:
        await UniMessage(selection.error).finish()
    # 火山方舟：本地渠道，凭据来自 volcengine.accounts，不向远程客户端扩散。
    if selection.platform == "volcengine":
        await _send_volcengine_results(server, snapshot.cpa, selection)
        return
    names = _quota_targets(server, selection)
    if not names:
        await UniMessage("没有可查询的客户端。").finish()
    await UniMessage("正在按平台查询上游额度，可能需要几秒…").send()
    results: list[tuple[str, QuotaBoard | str]] = []
    for name in names:
        try:
            if name == server.client_name:
                board = await _local_quota_board(selection)
            else:
                board = await _remote_quota_board(name, selection)
            results.append((name, board))
        except (CPAError, HubError) as exc:
            results.append((name, str(exc)))
    await _send_quota_results(server, snapshot.cpa, results, want_text=selection.text, multi=len(names) > 1)


async def _send_volcengine_results(server: ServerConfig, cpa: CpaConfig, selection: QuotaSelection) -> None:
    accounts = list(state.get_snapshot().volcengine.accounts)
    if selection.account:
        accounts = _filter_volcengine_accounts(accounts, selection.account)
        if not accounts:
            await UniMessage(f"没有找到火山账号：{selection.account}").finish()
    if not accounts:
        await UniMessage(
            "未配置火山方舟账号。请在 data/quotabot_config.json 的 volcengine.accounts 里添加 "
            "{name, access_key_id, secret_access_key, region}。"
        ).finish()
    await UniMessage("正在查询火山方舟 Coding Plan 额度…").send()
    try:
        board = await collect_volcengine_board(accounts)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免单渠道异常打断消息处理
        await UniMessage(f"火山额度查询失败：{exc}").finish()
        return
    await _send_quota_results(server, cpa, [(server.client_name, board)], want_text=selection.text, multi=False)


def _filter_volcengine_accounts(accounts: Sequence[VolcengineAccount], query: str) -> list[VolcengineAccount]:
    needle = query.strip().lower()
    if not needle:
        return list(accounts)
    from ..aliases import resolve_alias_for_keys

    matched: list[VolcengineAccount] = []
    for account in accounts:
        alias = resolve_alias_for_keys("volcengine", [account.name]) or account.name
        fields = [account.name.lower(), alias.lower()]
        if any(needle in field or field.startswith(needle) for field in fields):
            matched.append(account)
    return matched


def _quota_targets(server: ServerConfig, selection: QuotaSelection) -> list[str]:
    local = server.client_name
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
    return stamp_client(board, state.get_snapshot().server.client_name)


async def _remote_quota_board(name: str, selection: QuotaSelection) -> QuotaBoard:
    result = await get_hub().query_quota(
        name,
        platform=selection.platform,
        account=selection.account,
        fresh=selection.fresh,
    )
    return board_from_accounts(accounts_from_result(result), cached=result.cached)


async def _send_quota_results(
    server: ServerConfig,
    cpa: CpaConfig,
    results: list[tuple[str, QuotaBoard | str]],
    *,
    want_text: bool,
    multi: bool,
) -> None:
    prefix = multi or server.enabled
    outgoing: list[tuple[str, bytes | None]] = []
    for name, item in results:
        if isinstance(item, str):
            outgoing.append((f"[{name}] {item}" if prefix else item, None))
            continue
        label = f"[{name}] " if prefix else ""
        if want_text or not cpa.quota_image:
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
