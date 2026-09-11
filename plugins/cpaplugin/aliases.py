from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nonebot import get_plugin_config

from .config import Config

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_store: dict[str, str] = {}
_loaded = False
_memory_only = False


def identity_keys(file: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for field in ("auth_index", "email", "name", "id", "label"):
        raw = str(file.get(field) or "").strip()
        if not raw:
            continue
        candidates = [raw]
        if raw.lower().endswith(".json"):
            candidates.append(raw[:-5])
        for item in candidates:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            keys.append(item)
    scoped = _scoped_email_key(file)
    if scoped and scoped.lower() not in seen:
        keys.append(scoped)
    return keys


def unique_identity_keys(file: dict[str, Any]) -> list[str]:
    """跨平台同邮箱时只绑本条凭证，避免别名串号。"""
    keys: list[str] = []
    seen: set[str] = set()
    for field in ("auth_index", "name"):
        raw = str(file.get(field) or "").strip()
        if not raw:
            continue
        candidates = [raw]
        if raw.lower().endswith(".json"):
            candidates.append(raw[:-5])
        for item in candidates:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            keys.append(item)
    scoped = _scoped_email_key(file)
    if scoped and scoped.lower() not in seen:
        keys.append(scoped)
    if keys:
        return keys
    return identity_keys(file)


def _scoped_email_key(file: dict[str, Any]) -> str:
    email = str(file.get("email") or file.get("account") or "").strip()
    if "@" not in email:
        return ""
    provider = str(file.get("provider") or file.get("type") or "").strip().lower()
    if not provider:
        return ""
    return f"{provider}:{email}"


def resolve_alias(file: dict[str, Any]) -> str:
    mapping = load_aliases()
    if not mapping:
        return ""
    lookup = {key.lower(): alias for key, alias in mapping.items()}
    for key in identity_keys(file):
        alias = lookup.get(key.lower())
        if alias:
            return alias
    return ""


def public_fallback(file: dict[str, Any]) -> str:
    label = str(file.get("label") or "").strip()
    if label and "@" not in label and not _EMAIL.search(label):
        return label
    provider = str(file.get("provider") or file.get("type") or "acct").strip() or "acct"
    index = str(file.get("auth_index") or "")
    short = index[:4] if index else "????"
    return f"{provider}-{short}"


def load_aliases() -> dict[str, str]:
    global _store, _loaded
    if _loaded:
        return _store
    merged: dict[str, str] = {}
    try:
        cfg = get_plugin_config(Config)
        merged.update(_clean_map(cfg.cpa_aliases))
        if not _memory_only:
            path = Path(cfg.cpa_alias_file)
            if path.is_file():
                merged.update(_read_file(path))
    except Exception:
        pass
    _store = merged
    _loaded = True
    return _store


def set_alias(file: dict[str, Any], alias: str) -> str:
    name = alias.strip()
    if not name:
        raise ValueError("别名不能为空。")
    if "@" in name:
        raise ValueError("别名不要包含邮箱。")
    mapping = load_aliases()
    for key in unique_identity_keys(file):
        mapping[key] = name
    _commit(mapping)
    return name


def delete_alias(file: dict[str, Any]) -> bool:
    mapping = load_aliases()
    alias = resolve_alias(file)
    drop = {key.lower() for key in identity_keys(file)}
    removed = False
    for key, value in list(mapping.items()):
        if key.lower() in drop or (alias and value == alias):
            mapping.pop(key, None)
            removed = True
    _commit(mapping)
    return removed


def list_aliases() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for key, alias in load_aliases().items():
        grouped.setdefault(alias, []).append(key)
    return grouped


def format_alias_list(
    files: list[dict[str, Any]] | None = None,
    *,
    include_disabled: bool = False,
) -> str:
    grouped = list_aliases()
    if not grouped:
        return "还没有账号别名。设置：cpa alias set <渠道> <邮箱> <别名>"
    lines: list[str] = []
    hidden = 0
    for alias, keys in grouped.items():
        matched = _files_for_keys(files, keys) if files is not None else []
        if files is not None and matched and not include_disabled:
            if all(item.get("disabled") for item in matched):
                hidden += 1
                continue
        shown = ", ".join(_public_key(item) for item in keys)
        lines.append(f"{alias}  ←  {shown}")
    if not lines:
        if hidden:
            return "没有可显示的别名（已隐藏 disabled 账号）。查看：cpa alias list --disabled"
        return "还没有账号别名。设置：cpa alias set <渠道> <邮箱> <别名>"
    return "\n".join(lines)


def _files_for_keys(files: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    needles = {item.lower() for item in keys}
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file in files:
        markers = {item.lower() for item in identity_keys(file)}
        if not (needles & markers):
            continue
        stamp = str(file.get("auth_index") or file.get("name") or id(file))
        if stamp in seen:
            continue
        seen.add(stamp)
        matched.append(file)
    return matched


def _public_key(key: str) -> str:
    if "@" in key:
        local, _, domain = key.partition("@")
        prefix = local[:2] if local else "*"
        return f"{prefix}***@{domain}"
    if len(key) > 18:
        return key[:8] + "…"
    return key


def use_memory_aliases(data: dict[str, str] | None = None) -> None:
    """测试用：只走内存，不读写文件。"""
    global _store, _loaded, _memory_only
    _memory_only = True
    _store = _clean_map(data or {})
    _loaded = True


def _commit(mapping: dict[str, str]) -> None:
    global _store, _loaded
    _store = dict(mapping)
    _loaded = True
    if _memory_only:
        return
    try:
        cfg = get_plugin_config(Config)
        path = Path(cfg.cpa_alias_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _clean_map(data)


def _clean_map(data: dict[Any, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        name = str(key).strip()
        alias = str(value).strip()
        if name and alias:
            cleaned[name] = alias
    return cleaned
