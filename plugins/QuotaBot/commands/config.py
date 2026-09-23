"""/quota config：查看当前生效配置与强制重载。"""

from __future__ import annotations

from nonebot_plugin_alconna import UniMessage

from .. import state
from ..cpa.format import mask_secret

from .quota import quota


@quota.assign("config.show")
async def quota_config_show() -> None:
    snapshot = state.get_snapshot()
    cpa = snapshot.cpa
    server = snapshot.server
    lines = [
        "【配置】（密钥已脱敏）",
        f"配置文件：{state.snapshot_path() or '（内存模式）'}",
        f"generation：{state.generation()}",
        "",
        "cpa：",
        f"  base_url：{cpa.base_url}",
        f"  management_key：{mask_secret(cpa.management_key) if cpa.management_key else '（未设置）'}",
        f"  admins：{', '.join(cpa.admins) or '（未设置）'}",
        f"  quota_image：{cpa.quota_image}",
        f"  quota_timeout：{cpa.quota_timeout}s  concurrency：{cpa.quota_concurrency}  cache_ttl：{cpa.quota_cache_ttl}s",
        "",
        "volcengine：",
        f"  账号：{', '.join(a.name for a in snapshot.volcengine.accounts) or '（未配置）'}",
        "",
        "render：",
        f"  theme：{snapshot.render.theme}  cards_per_row：{snapshot.render.cards_per_row}",
        "",
        "server：",
        f"  enabled：{server.enabled}  client_name：{server.client_name}",
        f"  host：{server.host}:{server.port}  client_keys：{', '.join(server.client_keys) or '（无）'}",
        "",
        f"aliases_file：{snapshot.aliases_file}",
    ]
    error = state.last_error()
    if error:
        lines.append("")
        lines.append(f"⚠ 上次解析错误：{error}")
    await UniMessage("\n".join(lines)).finish()


@quota.assign("config.reload")
async def quota_config_reload() -> None:
    try:
        state.reload_config()
    except Exception as exc:  # noqa: BLE001
        await UniMessage(f"配置重载失败：{exc}").finish()
        return
    await UniMessage(f"配置已重新加载（generation={state.generation()}）。").finish()
