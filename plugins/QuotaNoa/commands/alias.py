"""/quota alias：分渠道账号别名管理（data/quotanoa_aliases.json）。

别名按渠道存储、跨实例生效：查询词会在全部 CPA 实例与 WorkBuddy 网关账号中搜索，
命中唯一凭证后绑定。渠道名可用文件里的 ``channel_keywords`` 自定义。
"""

from __future__ import annotations

from typing import Any

from nonebot_plugin_alconna import Arparma, Query, UniMessage

from ..aliases import delete_alias_for_keys, format_alias_list, set_alias_for_keys, unique_identity_keys
from ..cpa.format import display_name
from ..cpa.quota import clear_quota_cache, platform_of

from .common import (
    _collect_alias_across,
    _require_one_across,
    _require_platform_account_across,
    _text,
)
from .quota import quota

ALIAS_SET_USAGE = "/quota alias set <渠道> <查询词> <别名>"


@quota.assign("alias.list")
async def quota_alias_list(arp: Arparma) -> None:
    files: list[dict[str, Any]] = []
    for _instance, file in await _collect_alias_across():
        files.append(file)
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
        _instance, file = await _require_platform_account_across(first, second)
        alias = third
    else:
        _instance, file = await _require_one_across(first)
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
    _instance, file = await _require_one_across(_text(query))
    channel = platform_of(file)
    shown = display_name(file, public=True)
    # delete_alias_for_keys 已同时扫渠道桶与全局桶，无需再兜底
    removed = delete_alias_for_keys(channel, unique_identity_keys(file))
    if not removed:
        await UniMessage(f"{shown} 没有别名。").finish()
        return
    clear_quota_cache()
    await UniMessage(f"已删除 {shown} 的别名。额度图将改用渠道短索引。").finish()
