"""/quotanoa 根命令：额度查询 + 别名 / 主题 / 卡片 / 配置 / 火山实例 子命令。

根前缀固定为 `/`（用户要求“quota 必须使用指令头”），裸 `quota` 不匹配。
查询主体（平台 / 实例 / 账号 / --fresh 等）由 ``query.parse_quota_command``
从 plaintext 解析，因此根上不再声明 --fresh/--text/--instance 等 Option，
避免 `$main` 因 components 非空而不触发。

多实例：默认查询全部 CPA 实例并按实例名加前缀展示；``--instance <名>``
或位置参数里的实例名可限定到单个实例。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from arclet.alconna import Alconna, Args, CommandMeta, MultiVar, Option, Subcommand, store_true
from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import Arparma, Image, Query, UniMessage, on_alconna

from .. import state
from ..config import CpaConfig, ConfigError, normalize_name, valid_name, VolcengineAccount
from ..cpa.client import CPAError, get_client
from ..cpa.format import format_ambiguous, format_quota_list, is_cooling, match_auth
from ..cpa.quota import (
    PLATFORM_TITLES,
    QuotaBoard,
    clear_quota_cache,
    collect_quotas,
    format_quota_board,
    peek_quota_cache,
    platform_of,
)
from ..model import LOCAL_CHANNELS
from ..query import QuotaSelection, parse_quota_command, strip_quota_head, tokenize
from ..render.html import RenderError, render_board_images
from ..volcengine.provider import collect_board as collect_volcengine_board
from ..wb.provider import collect_board as collect_workbuddy_board
from ..qoder.provider import collect_board as collect_qoder_board

from .common import CPA_ADMIN, _require_one_across, _text, _without

#: /quotanoa all 与默认查询使用的本地渠道顺序。
LOCAL_CHANNEL_LABELS = {"volcengine": "火山", "workbuddy": "WorkBuddy", "qoder": "Qoder"}

#: 触发帮助的查询词（裸命令不再显示帮助）。
HELP_TOKENS = {"help", "--help", "-h"}


def _is_help_request(parts: Sequence[str]) -> bool:
    """判断请求是否为查看帮助。"""
    return len(parts) == 1 and parts[0].lower() in HELP_TOKENS


def _resolve_default_channels(configured: Sequence[str], entry: str) -> tuple[str, ...] | None:
    """无参数查询默认渠道集；返回 None 表示走全部 CPA 实例（现状）。

    - 配置 cpa.quota_default_channels 非空：两个入口都只查列表内本地渠道。
    - 配置为空：非对称——/quotanoa 默认查全部本地渠道，/cpa quota 默认查全部 CPA 实例。
    """
    if configured:
        return tuple(configured)
    if entry != "cpa":
        return tuple(LOCAL_CHANNELS)
    return None


# --------------------------------------------------------------------------- #
# 子命令 Alconna 结构（别名/主题/卡片/配置在各自模块里挂 handler）
# --------------------------------------------------------------------------- #

quota = on_alconna(
    Alconna(
        ["/"],
        "quotanoa",
        Subcommand("help", help_text="查看帮助"),
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
                help_text="设置别名：/quotanoa alias set <渠道> <查询词> <别名>",
            ),
            Subcommand("del|rm|delete", Args["query", str], dest="delete", help_text="删除别名"),
            help_text="分渠道账号别名",
        ),
        Subcommand(
            "theme",
            Subcommand("set", Args["name", str], help_text="设置额度图主题：/quotanoa theme set <主题>"),
            help_text="查看或设置额度图主题",
        ),
        Subcommand(
            "card",
            Subcommand("row", Args["count", str], help_text="设置每行卡片数：/quotanoa card row 1..6"),
            help_text="查看或设置卡片布局排版",
        ),
        Subcommand(
            "config",
            Subcommand("show", help_text="查看当前生效配置（密钥脱敏）"),
            Subcommand("reload", help_text="强制重载配置"),
            help_text="配置查看与重载",
        ),
        Subcommand(
            "volc",
            Subcommand("list", help_text="列出火山方舟账号"),
            Subcommand(
                "add",
                Args["name", str]["ak", str]["sk", str]["region?", str],
                help_text="新增火山方舟账号：/quotanoa volc add <名称> <AK> <SK> [region]",
            ),
            Subcommand(
                "remove|rm|delete",
                Args["name", str],
                Option("--yes|-y", action=store_true, dest="yes", help_text="确认删除"),
                dest="remove",
                help_text="删除火山方舟账号",
            ),
            help_text="火山方舟账号管理",
        ),
        Subcommand(
            "wb|workbuddy",
            Subcommand("list", help_text="列出 WorkBuddy 网关"),
            Subcommand(
                "add",
                Args["name", str]["base_url", str],
                Option("--user", Args["user", str], dest="user", help_text="控制台账号"),
                Option("--pass", Args["password", str], dest="password", help_text="控制台密码"),
                Option("--key", Args["key", str], dest="key", help_text="网关 api_key（跳过登录）"),
                Option("--timeout", Args["timeout", str], dest="timeout", help_text="请求超时秒"),
                help_text="新增 WorkBuddy 网关：/quotanoa wb add <名称> <base_url> --user U --pass P",
            ),
            Subcommand(
                "login",
                Args["name", str],
                help_text="校验账号密码并刷新会话：/quotanoa wb login <名称>",
            ),
            Subcommand(
                "remove|rm|delete",
                Args["name", str],
                Option("--yes|-y", action=store_true, dest="yes", help_text="确认删除"),
                dest="remove",
                help_text="删除 WorkBuddy 网关",
            ),
            help_text="WorkBuddy 网关额度查询与管理",
        ),
        Subcommand(
            "qoder|qd",
            Subcommand("list", help_text="列出 Qoder 代理"),
            Subcommand(
                "add",
                Args["name", str]["base_url", str],
                Option("--key", Args["key", str], dest="key", help_text="代理 API Key（Bearer）"),
                Option("--timeout", Args["timeout", str], dest="timeout", help_text="请求超时秒"),
                help_text="新增 Qoder 代理：/quotanoa qoder add <名称> <base_url> --key <API_KEY>",
            ),
            Subcommand(
                "remove|rm|delete",
                Args["name", str],
                Option("--yes|-y", action=store_true, dest="yes", help_text="确认删除"),
                dest="remove",
                help_text="删除 Qoder 代理",
            ),
            help_text="Qoder2OAPI 代理额度查询与管理",
        ),
        Args["a?", str]["b?", str]["tail", MultiVar(str, "*")],
        meta=CommandMeta(
            description="额度查询（仅管理员）",
            usage="发送 /quotanoa 查看帮助；/quotanoa 火山 查火山方舟",
            example="/quotanoa\n/quotanoa 火山\n/quotanoa claude\n/quotanoa claude Home\n/quotanoa --fresh\n/quotanoa cooling\n/quotanoa reset user@example.com\n/quotanoa alias set antigravity user@example.com AG-1\n/quotanoa volc add 火山主号 AK SK\n/quotanoa config show",
        ),
    ),
    permission=CPA_ADMIN,
    auto_send_output=True,
    skip_for_unmatch=False,
    use_cmd_start=True,
    block=True,
)


@quota.assign("$main")
async def quota_main(event: Event) -> None:
    await quota_entry(event, entry="quota")


@quota.assign("help")
async def quota_help() -> None:
    await UniMessage(_quota_help_text()).finish()


async def quota_entry(event: Event, *, entry: str = "quota") -> None:
    """共享入口：/quotanoa 与 /cpa quota 复用。

    ``entry`` 区分入口（"quota" / "cpa"），决定无参数时的默认渠道集：
    配置 cpa.quota_default_channels 非空时两个入口都只查列表内本地渠道；
    为空时非对称：/quotanoa 默认查全部本地渠道，/cpa quota 默认查全部 CPA 实例。
    查询：/quotanoa help（或 --help / -h）显示帮助。
    """
    if _is_help_request(strip_quota_head(tokenize(event.get_plaintext()))):
        await UniMessage(_quota_help_text()).finish()
    await quota_view(event, entry=entry)


# --------------------------------------------------------------------------- #
# 帮助
# --------------------------------------------------------------------------- #


def _quota_help_text() -> str:
    return "\n".join(
        [
            "QuotaNoa 额度查询（仅超级用户 / admins）",
            "命令固定带 / 前缀（指令头）。",
            "",
            "【查询】默认查询本地渠道（火山 / WorkBuddy / Qoder）；多实例时 CPA 结果按 [实例名] 前缀区分。",
            "  /quotanoa",
            "    无参数：查询全部本地渠道（火山 / WorkBuddy / Qoder）。",
            "    若配置了 cpa.quota_default_channels，则只查列表内渠道。",
            "  /quotanoa all",
            "    查询全部渠道：本地渠道 + 全部 CPA 实例（同义 --all / -a）。",
            "  /quotanoa help",
            "    查看本帮助（同义 --help / -h）。",
            "  /quotanoa <平台>",
            "    claude / codex(gpt, openai) / antigravity(反重力, agy) / kimi / xai / 火山(volcengine, ark) / workbuddy(wb) / qoder(qd)",
            "  /quotanoa <实例>",
            "    只查指定 CPA 实例。例：/quotanoa Home",
            "  /quotanoa <平台> <实例>",
            "    例：/quotanoa antigravity Home  或  /quotanoa Home antigravity",
            "  /quotanoa <查询词>",
            "    单个账号的额度卡（跨全部实例搜索）。",
            "  /quotanoa --instance <实例>   显式指定实例，避免与渠道名冲突",
            "  /quotanoa --fresh     忽略缓存，强制重查上游",
            "  /quotanoa --text      只发文字总览（排障 / 无浏览器）",
            "  /quotanoa cooling     只看冷却中的凭证（全部实例）",
            "  /quotanoa reset <查询词>   清除配额/冷却并恢复路由（跨实例搜索）",
            "",
            "【别名】分渠道存储（data/quotanoa_aliases.json）。",
            "  /quotanoa alias list [--disabled]",
            "  /quotanoa alias set <渠道> <查询词> <别名>",
            "    例：/quotanoa alias set antigravity user@example.com AG-1",
            "    渠道名可用文件里的 channel_keywords 自定义（如 agy → antigravity）。",
            "  /quotanoa alias del <查询词>    删除（跨渠道全部删除）",
            "",
            "【火山方舟】本地渠道，凭据存 data/quotanoa_config.json 的 volcengine.accounts。",
            "  支持 Coding Plan 与 Agent Plan，双套餐额度合并为一张卡片展示（含 Coding/Agent 档位徽章与到期时间）。",
            "  /quotanoa volc list",
            "  /quotanoa volc add <名称> <AK> <SK> [region]",
            "  /quotanoa volc remove <名称> --yes",
            "",
            "【WorkBuddy】本地渠道，网关存 data/quotanoa_config.json 的 workbuddy.servers。",
            "  /quotanoa wb              查询全部网关额度（同 workbuddy）",
            "  /quotanoa wb list",
            "  /quotanoa wb add <名称> <base_url> --user U --pass P [--timeout N]",
            "  /quotanoa wb login <名称>  校验账号密码并刷新会话",
            "  /quotanoa wb remove <名称> --yes",
            "",
            "【Qoder】本地渠道，代理存 data/quotanoa_config.json 的 qoder.servers。",
            "  /quotanoa qoder           查询全部代理号池额度（同 qd）",
            "  /quotanoa qoder list",
            "  /quotanoa qoder add <名称> <base_url> --key <API_KEY> [--timeout N]",
            "  /quotanoa qoder remove <名称> --yes",
            "",
            "【主题与排版】修改后立刻生效并持久化。",
            "  /quotanoa theme           查看当前主题与可选主题",
            "  /quotanoa theme set <主题>",
            "  /quotanoa card            查看每行卡片数",
            "  /quotanoa card row N      设置每行卡片数（1..6）",
            "",
            "【配置】",
            "  /quotanoa config show     查看生效配置（密钥脱敏）与最近解析错误",
            "  /quotanoa config reload   强制从磁盘重载配置",
            "",
            "【管理】CPA 实例 / 凭证 / 登录 / Codex 重置请用 /cpa。",
        ]
    )


# --------------------------------------------------------------------------- #
# 子命令：cooling / reset
# --------------------------------------------------------------------------- #


@quota.assign("cooling")
async def quota_cooling() -> None:
    names = state.get_snapshot().cpa.names()
    if not names:
        await UniMessage("没有配置 CPA 实例。新增：/cpa instance add <名称> <base_url>").finish()
        return
    lines: list[str] = []
    for name in names:
        try:
            files = await get_client(name).list_auth_files()
        except CPAError as exc:
            lines.append(f"[{name}] {exc}")
            continue
        cooling = [item for item in files if is_cooling(item)]
        if cooling:
            lines.append(format_quota_list(cooling, instance=name))
    await UniMessage("\n".join(lines) if lines else "当前没有冷却中的凭证。").finish()


@quota.assign("reset")
async def quota_reset(query: Query[str] = Query("reset.query")) -> None:
    instance, file = await _require_one_across(_text(query))
    auth_index = str(file.get("auth_index") or "")
    if not auth_index:
        await UniMessage("该凭证没有 auth_index，无法 reset-quota。").finish()
        return
    try:
        result = await get_client(instance).reset_quota(auth_index)
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(f"[{instance}] " + _format_reset_result(result, file)).finish()


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
# 火山方舟账号管理
# --------------------------------------------------------------------------- #


def _volcengine_raw() -> list[dict[str, Any]]:
    raw = state.get_snapshot().raw
    volc = raw.get("volcengine") if isinstance(raw, Mapping) else None
    accounts = volc.get("accounts") if isinstance(volc, Mapping) else None
    if not isinstance(accounts, list):
        return []
    return [dict(item) for item in accounts if isinstance(item, Mapping)]


def _write_volcengine(accounts: list[dict[str, Any]]) -> None:
    state.update_config({"volcengine": {"accounts": accounts}})


@quota.assign("volc.list")
async def volc_list() -> None:
    from ..cpa.format import mask_secret

    accounts = state.get_snapshot().volcengine.accounts
    if not accounts:
        await UniMessage("还没有配置火山方舟账号。新增：/quotanoa volc add <名称> <AK> <SK> [region]").finish()
        return
    lines = ["【火山方舟账号】"]
    for account in accounts:
        lines.append(f"  {account.name}  AK={mask_secret(account.access_key_id)}  region={account.region}")
    await UniMessage("\n".join(lines)).finish()


@quota.assign("volc.add")
async def volc_add(
    name: Query[str] = Query("volc.add.name"),
    ak: Query[str] = Query("volc.add.ak"),
    sk: Query[str] = Query("volc.add.sk"),
    region: Query[str] = Query("volc.add.region"),
) -> None:
    account_name = normalize_name(_text(name))
    if not valid_name(account_name):
        await UniMessage(f"账号名称非法：{_text(name)}（1–32 字符，不能含空白或 / \\）").finish()
        return
    ak_text = _text(ak)
    sk_text = _text(sk)
    if not ak_text or not sk_text:
        await UniMessage("AK / SK 不能为空。").finish()
        return
    accounts = _volcengine_raw()
    if any(normalize_name(str(item.get("name") or "")) == account_name for item in accounts):
        await UniMessage(f"火山账号「{account_name}」已存在。查看：/quotanoa volc list").finish()
        return
    entry: dict[str, Any] = {
        "name": account_name,
        "access_key_id": ak_text,
        "secret_access_key": sk_text,
    }
    if region.available and _text(region):
        entry["region"] = _text(region)
    accounts.append(entry)
    try:
        _write_volcengine(accounts)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已新增火山账号「{account_name}」。查看：/quotanoa volc list").finish()


@quota.assign("volc.remove")
async def volc_remove(
    arp: Arparma,
    name: Query[str] = Query("volc.remove.name"),
) -> None:
    account_name = normalize_name(_text(name))
    accounts = _volcengine_raw()
    remaining = [item for item in accounts if normalize_name(str(item.get("name") or "")) != account_name]
    if len(remaining) == len(accounts):
        await UniMessage(f"没有名为「{account_name}」的火山账号。查看：/quotanoa volc list").finish()
        return
    if not arp.find("volc.remove.yes"):
        await UniMessage(            f"即将删除火山账号「{account_name}」。确认请发送：\n/quotanoa volc remove {account_name} --yes").finish()
        return
    try:
        _write_volcengine(remaining)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已删除火山账号「{account_name}」。").finish()


# --------------------------------------------------------------------------- #
# 查询主体
# --------------------------------------------------------------------------- #


async def quota_view(event: Event, *, entry: str = "quota") -> None:
    snapshot = state.get_snapshot()
    selection = parse_quota_command(
        event.get_plaintext(),
        known_instances=set(snapshot.cpa.names()),
        extra_channels=_custom_channel_keywords(),
    )
    if selection.error:
        await UniMessage(selection.error).finish()
    # 火山方舟：本地渠道，凭据来自 volcengine.accounts。
    if selection.platform == "volcengine":
        await _send_volcengine_results(snapshot.cpa, selection)
        return
    # WorkBuddy：本地渠道，凭据来自 workbuddy.servers。
    if selection.platform == "workbuddy":
        await _send_workbuddy_results(snapshot.cpa, selection)
        return
    # Qoder：本地渠道，凭据来自 qoder.servers。
    if selection.platform == "qoder":
        await _send_qoder_results(snapshot.cpa, selection)
        return
    if selection.all_channels and not selection.platform:
        await _send_channels(snapshot, LOCAL_CHANNELS, include_cpa=True, selection=selection)
        return
    if not (selection.platform or selection.instance or selection.account):
        channels = _resolve_default_channels(snapshot.cpa.quota_default_channels, entry)
        if channels is not None:
            await _send_channels(snapshot, channels, include_cpa=False, selection=selection)
            return
    targets = _quota_targets(selection)
    if targets is None:
        return
    if not targets:
        await UniMessage("没有可查询的 CPA 实例。新增：/cpa instance add <名称> <base_url>").finish()
        return
    await UniMessage("正在按平台查询上游额度，可能需要几秒…").send()
    results: list[tuple[str, QuotaBoard | str]] = []
    for name in targets:
        try:
            board = await _instance_quota_board(name, selection)
            results.append((name, board))
        except CPAError as exc:
            results.append((name, str(exc)))
    await _send_quota_results(snapshot.cpa, results, want_text=selection.text, multi=len(targets) > 1)


async def _send_volcengine_results(cpa: CpaConfig, selection: QuotaSelection) -> None:
    accounts = list(state.get_snapshot().volcengine.accounts)
    if selection.account:
        accounts = _filter_volcengine_accounts(accounts, selection.account)
        if not accounts:
            await UniMessage(f"没有找到火山账号：{selection.account}").finish()
            return
    if not accounts:
        await UniMessage(
            "未配置火山方舟账号。用 /quotanoa volc add <名称> <AK> <SK> [region] 添加，"
            "或编辑 data/quotanoa_config.json 的 volcengine.accounts。"
        ).finish()
        return
    await UniMessage("正在查询火山方舟 Coding/Agent Plan 额度…").send()
    try:
        board = await collect_volcengine_board(accounts, force=selection.fresh)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免单渠道异常打断消息处理
        await UniMessage(f"火山额度查询失败：{exc}").finish()
        return
    await _send_quota_results(cpa, [("火山", board)], want_text=selection.text, multi=False)


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


# --------------------------------------------------------------------------- #
# WorkBuddy 网关额度查询（本地渠道，多网关聚合）
# --------------------------------------------------------------------------- #


async def _send_workbuddy_results(cpa: CpaConfig, selection: QuotaSelection) -> None:
    servers = list(state.get_snapshot().workbuddy.servers)
    if not servers:
        await UniMessage(
            "未配置 WorkBuddy 网关。用 /quotanoa wb add <名称> <base_url> --user U --pass P 添加，"
            "或编辑 data/quotanoa_config.json 的 workbuddy.servers。"
        ).finish()
        return
    await UniMessage("正在查询 WorkBuddy 额度…").send()
    try:
        board = await collect_workbuddy_board(servers, force=selection.fresh)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免单渠道异常打断消息处理
        await UniMessage(f"WorkBuddy 额度查询失败：{exc}").finish()
        return
    await _send_quota_results(cpa, [("WorkBuddy", board)], want_text=selection.text, multi=False)


async def _send_qoder_results(cpa: CpaConfig, selection: QuotaSelection) -> None:
    servers = list(state.get_snapshot().qoder.servers)
    if not servers:
        await UniMessage(
            "未配置 Qoder 代理。新增：/quotanoa qoder add <名称> <base_url> --key <API_KEY>"
        ).finish()
        return
    await UniMessage("正在查询 Qoder 额度…").send()
    try:
        board = await collect_qoder_board(servers, force=selection.fresh)
    except Exception as exc:  # noqa: BLE001 - 兜底，避免单渠道异常打断消息处理
        await UniMessage(f"Qoder 额度查询失败：{exc}").finish()
        return
    await _send_quota_results(cpa, [("Qoder", board)], want_text=selection.text, multi=False)


async def _local_channel_board(snapshot, channel: str, *, force: bool) -> QuotaBoard | None:
    """收集某个本地渠道的额度板；未配置返回 None。"""
    if channel == "volcengine":
        accounts = list(snapshot.volcengine.accounts)
        if not accounts:
            return None
        return await collect_volcengine_board(accounts, force=force)
    if channel == "workbuddy":
        servers = list(snapshot.workbuddy.servers)
        if not servers:
            return None
        return await collect_workbuddy_board(servers, force=force)
    if channel == "qoder":
        servers = list(snapshot.qoder.servers)
        if not servers:
            return None
        return await collect_qoder_board(servers, force=force)
    return None


def _no_channel_configured_text() -> str:
    return (
        "没有可查询的渠道。\n"
        "本地渠道：/quotanoa volc add <名称> <AK> <SK>、/quotanoa wb add …、/quotanoa qoder add …\n"
        "CPA 实例：/cpa instance add <名称> <base_url>\n"
        "查看全部渠道：/quotanoa all"
    )


async def _send_channels(
    snapshot,
    channels: Sequence[str],
    *,
    include_cpa: bool,
    selection: QuotaSelection,
) -> None:
    """按渠道集合发送额度：本地渠道 + 可选全部 CPA 实例。"""
    cpa = snapshot.cpa
    wanted_local = [ch for ch in channels if ch in LOCAL_CHANNELS]
    cpa_targets = list(cpa.names()) if include_cpa else []
    if not wanted_local and not cpa_targets:
        await UniMessage(_no_channel_configured_text()).finish()
        return
    results: list[tuple[str, QuotaBoard | str]] = []
    await UniMessage("正在查询额度，可能需要几秒…").send()
    for channel in wanted_local:
        label = LOCAL_CHANNEL_LABELS.get(channel, channel)
        try:
            board = await _local_channel_board(snapshot, channel, force=selection.fresh)
        except Exception as exc:  # noqa: BLE001 - 单渠道异常不阻断其它渠道
            results.append((label, f"额度查询失败：{exc}"))
            continue
        if board is None or not board.platforms:
            continue
        results.append((label, board))
    for name in cpa_targets:
        try:
            board = await _instance_quota_board(name, selection)
            results.append((name, board))
        except CPAError as exc:
            results.append((name, str(exc)))
    if not results:
        await UniMessage(_no_channel_configured_text()).finish()
        return
    await _send_quota_results(cpa, results, want_text=selection.text, multi=len(results) > 1)


def _custom_channel_keywords() -> dict[str, str]:
    """用户自定义渠道关键字（quotanoa_aliases.json 的 channel_keywords）。"""
    try:
        from ..aliases import custom_channel_keywords

        return custom_channel_keywords()
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# /quotanoa wb 子命令（查询 + 网关管理）
# --------------------------------------------------------------------------- #


@quota.assign(
    "workbuddy",
    additional=_without("workbuddy.list", "workbuddy.add", "workbuddy.remove", "workbuddy.login"),
)
async def quota_workbuddy(event: Event) -> None:
    """`/quotanoa wb`：查询 WorkBuddy 全部网关额度（渠道查询，不查单个账号）。

    子命令只在无 `list`/`add`/`remove` 时触发；`--text` / `--fresh` 由
    ``parse_quota_command`` 从 plaintext 统一解析（不在子命令上声明 Option，
    否则会劫持根级 ``/quotanoa --text``）。
    """
    selection = parse_quota_command(
        event.get_plaintext(),
        known_instances=set(state.get_snapshot().cpa.names()),
        extra_channels=_custom_channel_keywords(),
    )
    await _send_workbuddy_results(state.get_snapshot().cpa, selection)


@quota.assign(
    "qoder",
    additional=_without("qoder.list", "qoder.add", "qoder.remove"),
)
async def quota_qoder(event: Event) -> None:
    """`/quotanoa qoder`：查询 Qoder 全部代理号池额度（渠道查询，不查单个账号）。"""
    selection = parse_quota_command(
        event.get_plaintext(),
        known_instances=set(state.get_snapshot().cpa.names()),
        extra_channels=_custom_channel_keywords(),
    )
    await _send_qoder_results(state.get_snapshot().cpa, selection)


def _quota_targets(selection: QuotaSelection) -> list[str] | None:
    """返回要查询的实例名列表；``None`` 表示已发送错误消息、调用方应直接返回。"""
    if selection.instance:
        if selection.instance not in set(state.get_snapshot().cpa.names()):
            # 已在解析层校验过格式，这里只剩“不存在”一种情况。
            return [selection.instance]
        return [selection.instance]
    return list(state.get_snapshot().cpa.names())


async def _instance_quota_board(instance: str, selection: QuotaSelection) -> QuotaBoard:
    files = await get_client(instance).list_auth_files()
    platform = selection.platform
    target = files
    single = False
    if selection.account:
        matched = match_auth(files, selection.account)
        if not matched:
            raise CPAError(f"[{instance}] 没有找到凭证：{selection.account}")
        if len(matched) > 1:
            raise CPAError(format_ambiguous(selection.account, matched, instance=instance))
        target = matched
        single = True
    elif platform:
        target = [item for item in files if platform_of(item) == platform]
        if not target:
            raise CPAError(f"[{instance}] 没有 {platform} 平台的凭证。")
    force = selection.fresh
    skip_disabled = not single
    board = None if force else peek_quota_cache(target, instance=instance, platform=platform, skip_disabled=skip_disabled)
    if board is None:
        board = await collect_quotas(
            target, instance=instance, platform=platform, force=force, skip_disabled=skip_disabled
        )
    return board


async def _send_quota_results(
    cpa: CpaConfig,
    results: list[tuple[str, QuotaBoard | str]],
    *,
    want_text: bool,
    multi: bool,
) -> None:
    prefix = multi
    outgoing: list[tuple[str, bytes | None]] = []
    for name, item in results:
        if isinstance(item, str):
            outgoing.append((f"[{name}] {item}" if prefix else item, None))
            continue
        label = f"[{name}] " if prefix else ""
        if want_text or not cpa_uses_image(cpa, name):
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


def cpa_uses_image(cpa: CpaConfig, instance: str) -> bool:
    """该实例是否启用图片渲染（未找到时回退全局默认 True）。"""
    found = cpa.get(instance)
    return found.quota_image if found is not None else True
