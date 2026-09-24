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
    lines = [
        "【配置】（密钥已脱敏）",
        f"配置文件：{state.snapshot_path() or '（内存模式）'}",
        f"generation：{state.generation()}",
        "",
        "cpa：",
        f"  admins：{', '.join(cpa.admins) or '（未设置）'}",
        f"  codex_refresh_admin：{', '.join(cpa.codex_refresh_admin) or '（未设置）'}",
        f"  实例数：{len(cpa.instances)}",
    ]
    for instance in cpa.instances:
        key = mask_secret(instance.management_key) if instance.management_key else "（未设置）"
        lines.append(
            f"    - {instance.name}  {instance.base_url}  key={key}  "
            f"quota_timeout={instance.quota_timeout}s concurrency={instance.quota_concurrency} "
            f"cache_ttl={instance.quota_cache_ttl}s image={instance.quota_image}"
        )
    lines.extend(
        [
            "",
            "volcengine：",
            f"  账号：{', '.join(a.name for a in snapshot.volcengine.accounts) or '（未配置）'}",
            "",
            "workbuddy：",
        ]
    )
    if snapshot.workbuddy.servers:
        for server in snapshot.workbuddy.servers:
            key = mask_secret(server.api_key) if server.api_key else "（未设置）"
            lines.append(f"  - {server.name}  {server.base_url}  key={key}  timeout={server.timeout:g}s")
    else:
        lines.append("  网关：（未配置）")
    lines.extend(
        [
            "",
            "render：",
            f"  theme：{snapshot.render.theme}  cards_per_row：{snapshot.render.cards_per_row}",
            "",
            f"aliases_file：{snapshot.aliases_file}",
        ]
    )
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
