"""分渠道账号别名。

存储文件默认 ``data/quota_aliases.json``，结构为“渠道 → 身份键 → 别名”：

```json
{
  "*":           { "user@example.com": "AG-1" },
  "antigravity": { "user@example.com": "AG-1" },
  "volcengine":  { "volc-1": "火山主号" }
}
```

- ``*`` 为全局桶，任何渠道在自身桶未命中时会回退到全局桶。
- 旧版扁平格式（``{"身份键": "别名"}``）读取时自动归一化进 ``*`` 桶。
- 本模块自带 mtime 检查（带 1 秒节流），外部手改文件也会生效。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .model import channel_of

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

GLOBAL_BUCKET = "*"
_CHECK_INTERVAL = 1.0

_store: dict[str, dict[str, str]] = {}
_loaded = False
_memory_only = False
_signature: tuple[int, int] | None = None
_checked_at = 0.0


# --------------------------------------------------------------------------- #
# 身份键
# --------------------------------------------------------------------------- #


def identity_keys(file: Mapping[str, Any]) -> list[str]:
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


def unique_identity_keys(file: Mapping[str, Any]) -> list[str]:
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


def _scoped_email_key(file: Mapping[str, Any]) -> str:
    email = str(file.get("email") or file.get("account") or "").strip()
    if "@" not in email:
        return ""
    provider = str(file.get("provider") or file.get("type") or "").strip().lower()
    if not provider:
        return ""
    return f"{provider}:{email}"


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #


def aliases_path() -> Path:
    from . import state

    try:
        raw = state.get_snapshot().aliases_file
    except Exception:
        from .config import DEFAULT_ALIASES_FILE

        raw = DEFAULT_ALIASES_FILE
    return Path(raw).expanduser().resolve()


def _read_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _read_file(path: Path) -> Any:
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clean_map(data: Any) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    if not isinstance(data, Mapping):
        return cleaned
    for key, value in data.items():
        name = str(key).strip()
        alias = str(value).strip()
        if name and alias:
            cleaned[name] = alias
    return cleaned


def _normalize_store(data: Any) -> dict[str, dict[str, str]]:
    if not isinstance(data, Mapping):
        return {}
    nested: dict[str, dict[str, str]] = {}
    flat: dict[str, str] = {}
    for key, value in data.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, Mapping):
            bucket = _clean_map(value)
            if bucket:
                nested.setdefault(name, {}).update(bucket)
        else:
            alias = str(value).strip()
            if alias:
                flat[name] = alias
    if flat:
        nested.setdefault(GLOBAL_BUCKET, {}).update(flat)
    return nested


def _ensure_loaded() -> None:
    global _store, _signature, _checked_at, _loaded
    if _memory_only:
        return
    now = time.monotonic()
    if _loaded and now - _checked_at < _CHECK_INTERVAL:
        return
    _checked_at = now
    path = aliases_path()
    signature = _read_signature(path)
    if _loaded and signature == _signature:
        return
    _store = _normalize_store(_read_file(path))
    _signature = signature
    _loaded = True


def load_aliases() -> dict[str, dict[str, str]]:
    _ensure_loaded()
    return _store


def _merged_lookup(channel: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, alias in _store.get(GLOBAL_BUCKET, {}).items():
        lookup[key.lower()] = alias
    for key, alias in _store.get(channel, {}).items():
        lookup[key.lower()] = alias
    return lookup


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def resolve_alias_for_keys(channel: str, keys: Iterable[str]) -> str:
    store = load_aliases()
    if not store:
        return ""
    lookup = _merged_lookup(channel or GLOBAL_BUCKET)
    for key in keys:
        text = str(key).strip()
        if not text:
            continue
        alias = lookup.get(text.lower())
        if alias:
            return alias
    return ""


def resolve_alias(file: Mapping[str, Any], *, channel: str | None = None) -> str:
    return resolve_alias_for_keys(channel or channel_of(file), identity_keys(file))


def public_fallback(file: Mapping[str, Any]) -> str:
    label = str(file.get("label") or "").strip()
    if label and "@" not in label and not _EMAIL.search(label):
        return label
    provider = str(file.get("provider") or file.get("type") or "acct").strip() or "acct"
    index = str(file.get("auth_index") or "")
    short = index[:4] if index else "????"
    return f"{provider}-{short}"


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #


def set_alias_for_keys(channel: str, keys: Iterable[str], alias: str) -> str:
    name = (alias or "").strip()
    if not name:
        raise ValueError("别名不能为空。")
    if "@" in name:
        raise ValueError("别名不要包含邮箱。")
    cleaned = [str(key).strip() for key in keys if str(key).strip()]
    if not cleaned:
        raise ValueError("该账号没有可用于绑定的身份标识。")
    target = channel or GLOBAL_BUCKET
    load_aliases()
    bucket = dict(_store.get(target, {}))
    for key in cleaned:
        bucket[key] = name
    _store[target] = bucket
    _commit()
    return name


def set_alias(file: Mapping[str, Any], alias: str, *, channel: str | None = None) -> str:
    return set_alias_for_keys(channel or channel_of(file), unique_identity_keys(file), alias)


def delete_alias_for_keys(channel: str, keys: Iterable[str]) -> bool:
    load_aliases()
    drop = {str(key).strip().lower() for key in keys if str(key).strip()}
    if not drop:
        return False
    removed = False
    for bucket_name in (channel or GLOBAL_BUCKET, GLOBAL_BUCKET):
        bucket = _store.get(bucket_name)
        if not bucket:
            continue
        for key in list(bucket):
            if key.lower() in drop:
                bucket.pop(key, None)
                removed = True
        if not bucket:
            _store.pop(bucket_name, None)
    if removed:
        _commit()
    return removed


def delete_alias(file: Mapping[str, Any], *, channel: str | None = None) -> bool:
    return delete_alias_for_keys(channel or channel_of(file), unique_identity_keys(file))


def _commit() -> None:
    global _signature
    global _checked_at
    if _memory_only:
        return
    from . import state
    from .config import atomic_write_json

    path = aliases_path()
    try:
        atomic_write_json(path, _store)
    except Exception:
        pass
    _signature = _read_signature(path)
    _checked_at = time.monotonic()
    # 别名变化 → 展示名与额度缓存里的 name 都需失效。
    state.invalidate_downstream()


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def list_aliases() -> dict[str, dict[str, str]]:
    """返回嵌套的别名表（渠道 → 身份键 → 别名）。"""
    return load_aliases()


def _hint() -> str:
    return "还没有账号别名。设置：/quota alias set <渠道> <查询词> <别名>"


def format_alias_list(
    files: Sequence[Mapping[str, Any]] | None = None,
    *,
    include_disabled: bool = False,
) -> str:
    store = load_aliases()
    if not store:
        return _hint()
    lines: list[str] = []
    hidden = 0
    for channel in sorted(store):
        bucket = store[channel]
        if not bucket:
            continue
        grouped: dict[str, list[str]] = {}
        for key, alias in bucket.items():
            grouped.setdefault(alias, []).append(key)
        for alias in sorted(grouped):
            keys = grouped[alias]
            if files is not None and not include_disabled:
                matched = _files_for_keys(files, keys)
                if matched and all(item.get("disabled") for item in matched):
                    hidden += 1
                    continue
            shown = ", ".join(_public_key(item) for item in keys)
            label = "全局" if channel == GLOBAL_BUCKET else channel
            lines.append(f"[{label}] {alias}  ←  {shown}")
    if not lines:
        if hidden:
            return "没有可显示的别名（已隐藏 disabled 账号）。查看：/quota alias list --disabled"
        return _hint()
    return "\n".join(lines)


def _files_for_keys(files: Sequence[Mapping[str, Any]], keys: list[str]) -> list[Mapping[str, Any]]:
    needles = {item.lower() for item in keys}
    matched: list[Mapping[str, Any]] = []
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


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #


def use_memory_aliases(data: Mapping[str, Any] | None = None) -> None:
    """测试用：只走内存，不读写文件。"""
    global _store, _loaded, _memory_only, _signature, _checked_at
    _memory_only = True
    _store = _normalize_store(data or {})
    _loaded = True
    _signature = None
    _checked_at = 0.0


def reset_alias_cache() -> None:
    """让下一次读取重新检查磁盘（内存模式下无操作）。"""
    global _store, _loaded, _signature, _checked_at
    if _memory_only:
        return
    _store = {}
    _loaded = False
    _signature = None
    _checked_at = 0.0
