"""/quota alias：分渠道账号别名管理（data/quota_aliases.json）。"""

from __future__ import annotations

from typing import Any

from nonebot_plugin_alconna import Arparma, Query, UniMessage

from ..aliases import delete_alias_for_keys, format_alias_list, set_alias_for_keys, unique_identity_keys
from ..cpa.client import CPAError, get_client
from ..cpa.format import display_name
from ..cpa.quota import clear_quota_cache, platform_of

from .common import _require_one, _require_platform_account, _text
from .quota import quota

ALIAS_SET_USAGE = "/quota alias set <渠道> <查询词> <别名>"


@quota.assign("alias.list")
async def quota_alias_list(arp: Arparma) -> None:
    try:
        files = await get_client().list_auth_files()
    except CPAError as exc:
        await UniMessage(str(exc)).finish()
        return
    await UniMessage(
        format_alias_list(files, include_disabled=bool(arp.find("alias.list.disabled")))
    ).finish()


@quota.assign("alias.set")
async def quota_alias_set(
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
    channel = platform_of(file)
    try:
        name = set_alias_for_keys(channel, unique_identity_keys(file), alias)
    except ValueError as exc:
        await UniMessage(str(exc)).finish()
        return
    clear_quota_cache()
    await UniMessage(
        f"已设置别名「{name}」（渠道 {channel}）。\n"
        f"聊天/额度图将显示该名称，不再使用邮箱。查看：/quota alias list"
    ).finish()


@quota.assign("alias.delete")
async def quota_alias_delete(query: Query[str] = Query("alias.delete.query")) -> None:
    file = await _require_one(_text(query))
    channel = platform_of(file)
    shown = display_name(file, public=True)
    # delete_alias_for_keys 已同时扫渠道桶与全局桶，无需再兜底
    removed = delete_alias_for_keys(channel, unique_identity_keys(file))
    if not removed:
        await UniMessage(f"{shown} 没有别名。").finish()
    clear_quota_cache()
    await UniMessage(f"已删除 {shown} 的别名。额度图将改用渠道短索引。").finish()
