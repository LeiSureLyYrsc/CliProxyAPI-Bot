"""/quota qoder：Qoder2OAPI 代理管理（data/quotanoa_config.json 的 qoder.servers）。

查询用 /quota qoder（见 commands/quota.py）；本模块只负责代理的增删查，
写入配置后自动热重载。

鉴权：Qoder 代理统一使用 API Key（``--key``，来自代理的 data/api_key.txt），
以 ``Authorization: Bearer <key>`` 调用 ``GET /v1/dashboard/billing/credits``。
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


def _qoder_raw() -> list[dict[str, Any]]:
    raw = state.get_snapshot().raw
    qoder_raw = raw.get("qoder") if isinstance(raw, Mapping) else None
    servers = qoder_raw.get("servers") if isinstance(qoder_raw, Mapping) else None
    if not isinstance(servers, list):
        return []
    return [dict(item) for item in servers if isinstance(item, Mapping)]


def _write_qoder(servers: list[dict[str, Any]]) -> None:
    state.update_config({"qoder": {"servers": servers}})


@quota.assign("qoder.list")
async def qoder_list() -> None:
    servers = state.get_snapshot().qoder.servers
    if not servers:
        await UniMessage(
            "还没有配置 Qoder 代理。"
            "新增：/quota qoder add <名称> <base_url> --key <API_KEY>"
        ).finish()
        return
    lines = ["【Qoder 代理】"]
    for server in servers:
        key = mask_secret(server.api_key) if server.api_key else "（未设置）"
        lines.append(
            f"  {server.name}  {server.base_url}  api_key={key}  timeout={server.timeout:g}s"
        )
    await UniMessage("\n".join(lines)).finish()


@quota.assign("qoder.add")
async def qoder_add(
    name: Query[str] = Query("qoder.add.name"),
    base_url: Query[str] = Query("qoder.add.base_url"),
    key: Query[str] = Query("qoder.add.key.key"),
    timeout: Query[str] = Query("qoder.add.timeout.timeout"),
) -> None:
    raw_name = normalize_name(_text(name))
    if not valid_name(raw_name):
        await UniMessage(f"代理名称非法：{_text(name)}（1–32 字符，不能含空白或 / \\ :）").finish()
        return
    if is_channel_name(raw_name):
        await UniMessage(
            f"代理名称不能与渠道名称同名：{raw_name}。请换一个名字（如 qoder-main）。"
        ).finish()
        return
    url = _text(base_url).rstrip("/")
    if not url:
        await UniMessage("base_url 不能为空。").finish()
        return
    api_key = _text(key) if key.available else ""
    if not api_key:
        await UniMessage(
            "需要 --key <代理 API Key>（见该代理的 data/api_key.txt）。"
        ).finish()
        return
    servers = _qoder_raw()
    if any(normalize_name(str(item.get("name") or "")) == raw_name for item in servers):
        await UniMessage(f"代理「{raw_name}」已存在。查看：/quota qoder list").finish()
        return
    entry: dict[str, Any] = {"name": raw_name, "base_url": url, "api_key": api_key}
    if timeout.available and _text(timeout):
        entry["timeout"] = _text(timeout)
    servers.append(entry)
    try:
        _write_qoder(servers)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已新增 Qoder 代理「{raw_name}」→ {url}。查看：/quota qoder list").finish()


@quota.assign("qoder.remove")
async def qoder_remove(
    arp: Arparma,
    name: Query[str] = Query("qoder.remove.name"),
) -> None:
    raw_name = normalize_name(_text(name))
    servers = _qoder_raw()
    remaining = [item for item in servers if normalize_name(str(item.get("name") or "")) != raw_name]
    if len(remaining) == len(servers):
        await UniMessage(f"没有名为「{raw_name}」的 Qoder 代理。查看：/quota qoder list").finish()
        return
    if not arp.find("qoder.remove.yes"):
        await UniMessage(
            f"即将删除 Qoder 代理「{raw_name}」。确认请发送：\n/quota qoder remove {raw_name} --yes"
        ).finish()
        return
    try:
        _write_qoder(remaining)
    except ConfigError as exc:
        await UniMessage(f"写入配置失败：{exc}").finish()
        return
    await UniMessage(f"已删除 Qoder 代理「{raw_name}」。").finish()
