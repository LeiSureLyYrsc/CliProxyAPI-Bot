"""/quota wb：WorkBuddy 网关管理（data/quotanoa_config.json 的 workbuddy.servers）。

查询用 ``/quota wb`` / ``/quota workbuddy``（见 commands/quota.py）；本模块只负责
网关的增删查，写入配置后自动热重载。
"""

from __future__ import annotations

from typing import Any, Mapping

from nonebot_plugin_alconna import Arparma, Query, UniMessage

from .. import state
from ..config import ConfigError, normalize_name, valid_name
from ..cpa.format import mask_secret
from ..model import is_channel_name

from .common import _text
from .quota import quota


def _workbuddy_raw() -> list[dict[str, Any]]:
    raw = state.get_snapshot().raw
    wb_raw = raw.get("workbuddy") if isinstance(raw, Mapping) else None
    servers = wb_raw.get("servers") if isinstance(wb_raw, Mapping) else None
    if not isinstance(servers, list):
        return []
    return [dict(item) for item in servers if isinstance(item, Mapping)]


def _write_workbuddy(servers: list[dict[str, Any]]) -> None:
    state.update_config({"workbuddy": {"servers": servers}})


@quota.assign("workbuddy.list")
async def wb_list() -> None:
    servers = state.get_snapshot().workbuddy.servers
    if not servers:
        await UniMessage(
            "还没有配置 WorkBuddy 网关。新增：/quota wb add <名称> <base_url> [--key K]"
        ).finish()
        return
    lines = ["【WorkBuddy 网关】"]
    for server in servers:
        key = mask_secret(server.api_key) if server.api_key else "（未设置）"
        lines.append(f"  {server.name}  {server.base_url}  key={key}  timeout={server.timeout:g}s")
    await UniMessage("\n".join(lines)).finish()


@quota.assign("workbuddy.add")
async def wb_add(
    name: Query[str] = Query("workbuddy.add.name"),
    base_url: Query[str] = Query("workbuddy.add.base_url"),
    key: Query[str] = Query("workbuddy.add.key.key"),
    timeout: Query[str] = Query("workbuddy.add.timeout.timeout"),
) -> None:
    raw_name = normalize_name(_text(name))
    if not valid_name(raw_name):
        await UniMessage(f"网关名称非法：{_text(name)}（1–32 字符，不能含空白或 / \\ :）").finish()
        return
    if is_channel_name(raw_name):
        await UniMessage(
            f"网关名称不能与渠道名称同名：{raw_name}。请换一个名字（如 wb-main、wb-backup）。"
        ).finish()
        return
    url = _text(base_url).rstrip("/")
    if not url:
        await UniMessage("base_url 不能为空。").finish()
        return
    servers = _workbuddy_raw()
    if any(normalize_name(str(item.get("name") or "")) == raw_name for item in servers):
        await UniMessage(f"网关「{raw_name}」已存在。查看：/quota wb list").finish()
        return
    entry: dict[str, Any] = {"name": raw_name, "base_url": url}
    if key.available and _text(key):
        entry["api_key"] = _text(key)
    if timeout.available and _text(timeout):
        entry["timeout"] = _text(timeout)
    servers.append(entry)
    try:
        _write_workbuddy(servers)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已新增 WorkBuddy 网关「{raw_name}」→ {url}。查看：/quota wb list").finish()


@quota.assign("workbuddy.remove")
async def wb_remove(
    arp: Arparma,
    name: Query[str] = Query("workbuddy.remove.name"),
) -> None:
    raw_name = normalize_name(_text(name))
    servers = _workbuddy_raw()
    remaining = [item for item in servers if normalize_name(str(item.get("name") or "")) != raw_name]
    if len(remaining) == len(servers):
        await UniMessage(f"没有名为「{raw_name}」的 WorkBuddy 网关。查看：/quota wb list").finish()
        return
    if not arp.find("workbuddy.remove.yes"):
        await UniMessage(
            f"即将删除 WorkBuddy 网关「{raw_name}」。确认请发送：\n/quota wb remove {raw_name} --yes"
        ).finish()
        return
    try:
        _write_workbuddy(remaining)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已删除 WorkBuddy 网关「{raw_name}」。").finish()
