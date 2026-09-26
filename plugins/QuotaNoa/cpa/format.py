from __future__ import annotations

import re
from typing import Any

from ..model import instance_tag

_CALLBACK_URL = re.compile(r"https?://[^\s<>\"']+", re.I)


def mask_secret(value: str, keep: int = 4) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "***"
    return f"{text[:keep]}...{text[-keep:]}"


def display_name(file: dict[str, Any], *, public: bool = False) -> str:
    from ..aliases import public_fallback, resolve_alias

    alias = resolve_alias(file)
    if alias:
        return alias
    if public:
        return public_fallback(file)
    for key in ("label", "email", "account", "id", "name"):
        value = file.get(key)
        if value:
            return str(value)
    return "(unknown)"


def short_index(file: dict[str, Any], size: int = 8) -> str:
    index = str(file.get("auth_index") or "")
    if not index:
        return "-"
    if len(index) <= size:
        return index
    return index[:size]


def is_cooling(file: dict[str, Any]) -> bool:
    if file.get("next_retry_after"):
        return True
    status = str(file.get("status") or "").lower()
    message = str(file.get("status_message") or "").lower()
    if "quota" in message or "429" in message or "cooldown" in message or "cooling" in message:
        return True
    return status in {"exhausted", "quota"}


def is_unhealthy(file: dict[str, Any]) -> bool:
    if is_cooling(file):
        return True
    if file.get("unavailable"):
        return True
    status = str(file.get("status") or "").lower()
    return status not in {"", "ready", "ok", "active"}


def match_auth(files: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if not needle:
        return []
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for file in files:
        fields = [
            str(file.get("auth_index") or ""),
            str(file.get("name") or ""),
            str(file.get("id") or ""),
            str(file.get("email") or ""),
            str(file.get("label") or ""),
            str(file.get("account") or ""),
        ]
        from ..aliases import resolve_alias

        alias = resolve_alias(file)
        if alias:
            fields.append(alias)
        lowered = [item.lower() for item in fields if item]
        if needle in lowered:
            exact.append(file)
        elif any(item.startswith(needle) or needle in item for item in lowered):
            partial.append(file)
    return exact or partial


def format_probe(
    headers: dict[str, str],
    files: list[dict[str, Any]],
    latest: str | None,
) -> str:
    ready = sum(1 for f in files if str(f.get("status") or "").lower() in {"ready", "ok", "active"} and not f.get("disabled"))
    disabled = sum(1 for f in files if f.get("disabled"))
    cooling = sum(1 for f in files if is_cooling(f))
    lines = ["CLIProxyAPI"]
    if headers.get("version"):
        lines.append(f"版本：{headers['version']}")
    if headers.get("commit"):
        lines.append(f"提交：{headers['commit']}")
    if headers.get("build_date"):
        lines.append(f"构建：{headers['build_date']}")
    if latest:
        lines.append(f"上游最新：{latest}")
    support = headers.get("support_plugin")
    if support:
        lines.append(f"动态插件：{'是' if support == '1' else support}")
    lines.append(
        f"凭证：{len(files)} 合计 | {ready} ready | {disabled} 禁用 | {cooling} 冷却"
    )
    return "\n".join(lines)


def format_auth_line(file: dict[str, Any], *, instance: str = "") -> str:
    provider = file.get("provider") or "?"
    status = file.get("status") or "?"
    flags: list[str] = []
    if file.get("disabled"):
        flags.append("disabled")
    if is_cooling(file):
        until = file.get("next_retry_after")
        flags.append(f"cooling {until}" if until else "cooling")
    elif file.get("unavailable"):
        flags.append("unavailable")
    flag_text = f"  {' '.join(flags)}" if flags else ""
    prefix = f"[{instance_tag(instance)}] " if instance_tag(instance) else ""
    return f"{prefix}[{provider}] {display_name(file, public=True)}  {status}  idx={short_index(file)}{flag_text}"


def visible_auth_files(files: list[dict[str, Any]], *, include_disabled: bool = False) -> list[dict[str, Any]]:
    if include_disabled:
        return list(files)
    return [item for item in files if not item.get("disabled")]


def format_auth_list(
    files: list[dict[str, Any]],
    *,
    limit: int = 30,
    include_disabled: bool = False,
    empty: str = "",
    instance: str = "",
) -> str:
    visible = visible_auth_files(files, include_disabled=include_disabled)
    if not visible:
        return empty or (
            "没有启用中的凭证。查看已禁用账号：cpa auth list --disabled"
            if files
            else "没有凭证。"
        )
    shown = visible[:limit]
    lines = [format_auth_line(file, instance=instance) for file in shown]
    if len(visible) > limit:
        lines.append(f"... 另有 {len(visible) - limit} 条未显示，请加 provider 过滤或用 show 精确查询")
    return "\n".join(lines)


def format_auth_detail(file: dict[str, Any]) -> str:
    from ..aliases import resolve_alias

    alias = resolve_alias(file)
    lines = [
        f"名称：{file.get('name') or '-'}",
        f"索引：{file.get('auth_index') or '-'}",
        f"渠道：{file.get('provider') or '-'}",
        f"别名：{alias or '（未设置，聊天里会显示为 ' + display_name(file, public=True) + '）'}",
        f"显示：{display_name(file, public=True)}",
        f"状态：{file.get('status') or '-'} / {_truncate(str(file.get('status_message') or 'ok'), 180)}",
        f"禁用：{bool(file.get('disabled'))}    不可用：{bool(file.get('unavailable'))}",
    ]
    if file.get("next_retry_after"):
        lines.append(f"冷却至：{file.get('next_retry_after')}")
    success = file.get("success")
    failed = file.get("failed")
    if success is not None or failed is not None:
        lines.append(f"计数：success={success or 0}  failed={failed or 0}")
    for key in ("email", "account", "account_type", "label", "last_refresh"):
        if file.get(key):
            lines.append(f"{key}：{file[key]}")
    buckets = file.get("recent_requests")
    if isinstance(buckets, list) and buckets:
        active = [b for b in buckets if isinstance(b, dict) and (b.get("success") or b.get("failed"))]
        show = (active or buckets)[-5:]
        lines.append("近期请求桶：")
        for bucket in show:
            if not isinstance(bucket, dict):
                continue
            lines.append(
                f"  {bucket.get('time', '?')}  ok={bucket.get('success', 0)}  fail={bucket.get('failed', 0)}"
            )
    return "\n".join(lines)


def format_quota_list(files: list[dict[str, Any]], *, instance: str = "") -> str:
    if not files:
        return "当前没有异常或冷却中的凭证。"
    return format_auth_list(files, instance=instance)


def format_models(models: list[Any]) -> str:
    if not models:
        return "该凭证没有返回模型列表。"
    lines: list[str] = []
    for item in models[:40]:
        if isinstance(item, str):
            lines.append(f"- {item}")
        elif isinstance(item, dict):
            name = item.get("name") or item.get("id") or item.get("alias") or "?"
            alias = item.get("alias")
            extra = f"  alias={alias}" if alias and alias != name else ""
            lines.append(f"- {name}{extra}")
        else:
            lines.append(f"- {item}")
    if len(models) > 40:
        lines.append(f"... 另有 {len(models) - 40} 个未显示")
    return "\n".join(lines)


def format_login_prompt(provider: str, payload: dict[str, Any], *, instance: str = "") -> str:
    url = payload.get("url") or ""
    target = instance or "<实例>"
    lines = [f"[{provider}] 请在浏览器完成授权。"]
    if url:
        lines.append(url)
    device = payload.get("flow") == "device" or payload.get("user_code")
    if device:
        if payload.get("user_code"):
            lines.append(f"设备码：{payload['user_code']}")
        if payload.get("expires_in"):
            lines.append(f"有效期约 {payload['expires_in']} 秒")
        lines.append("完成后我会自动通知。")
        lines.append(f"取消登录：cpa login {target} cancel")
        return "\n".join(lines)
    lines.append("授权完成后，把浏览器地址栏的完整回调链接直接发到当前聊天即可（localhost 也可以）。")
    lines.append(f"也可显式提交：cpa login {target} callback <回调链接>")
    lines.append(f"取消登录：cpa login {target} cancel")
    return "\n".join(lines)


def looks_like_oauth_callback(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if "oauth-callback" in lowered:
        return True
    if "code=" in lowered and ("state=" in lowered or "localhost" in lowered or "127.0.0.1" in lowered):
        return True
    return False


def extract_oauth_callback_url(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    match = _CALLBACK_URL.search(raw)
    if match:
        return match.group(0).rstrip(")>.,;\"'")
    if looks_like_oauth_callback(raw) and "://" in raw:
        return raw.split()[0].rstrip(")>.,;\"'")
    return ""


def format_ambiguous(query: str, files: list[dict[str, Any]], *, instance: str = "") -> str:
    return (
        f"「{query}」匹配到多个凭证，请用更精确的名称或 auth_index：\n"
        f"{format_auth_list(files, include_disabled=True, instance=instance)}"
    )


def format_reset_result(data: Any, file: dict[str, Any]) -> str:
    models = []
    if isinstance(data, dict):
        raw = data.get("models")
        if isinstance(raw, list):
            models = [str(item) for item in raw]
    extra = f"\n已恢复模型：{', '.join(models)}" if models else ""
    return f"已清除 {display_name(file, public=True)} 的配额/冷却。{extra}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
