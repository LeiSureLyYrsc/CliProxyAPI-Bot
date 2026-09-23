"""配置快照与热重载。

- ``data/quotabot_config.json`` 是唯一配置源（除 ``.env`` 里的 ``QUOTABOT_CONFIG_FILE``）。
- ``ensure_fresh()`` 在每个命令入口做廉价的 mtime/size 检查，变化才重新解析。
- 解析失败**保留上一份好快照**（fail-soft），错误可通过 ``last_error()`` 查询。
- 重载后按固定顺序失效下游缓存：别名 → 主题注册表 → 额度缓存。

本模块属根模块，只允许依赖 ``config``；对子包（cpa/render）的调用
一律用函数内延迟 import，以避免模块级循环依赖。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config as config_module
from .config import (
    DEFAULT_CONFIG_FILE,
    ConfigError,
    ConfigSnapshot,
    Config,
    read_config_file,
    snapshot_from_raw,
)

try:
    from nonebot.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("QuotaBot.state")

_hooks: list[Callable[[ConfigSnapshot], None]] = []
_lock = threading.RLock()

_snapshot: ConfigSnapshot | None = None
_generation: int = 0
_signature: tuple[int, int] | None = None
_last_error: str = ""
_loaded_path: Path | None = None
_memory_only = False

_env_config: Config | None = None
_path_override: Path | None = None


def register_reload_hook(hook: Callable[[ConfigSnapshot], None]) -> None:
    """注册一个在配置重载后调用的同步回调。"""
    if hook not in _hooks:
        _hooks.append(hook)


def set_config_path(path: Path | str | None) -> None:
    """覆盖配置文件的解析路径（测试用）。传 None 恢复默认解析。"""
    global _path_override
    _path_override = Path(path).expanduser().resolve() if path else None


def config_file_path() -> Path:
    """解析配置文件的绝对路径。"""
    global _env_config
    if _path_override is not None:
        return _path_override
    raw = DEFAULT_CONFIG_FILE
    try:
        if _env_config is None:
            from nonebot import get_plugin_config

            _env_config = get_plugin_config(Config)
        env_config = _env_config
        if env_config is not None:
            raw = env_config.quotabot_config_file or DEFAULT_CONFIG_FILE
    except Exception:
        raw = DEFAULT_CONFIG_FILE
    return Path(raw).expanduser().resolve()


def _read_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _warn_legacy(path: Path) -> None:
    """首次生成配置时，提示旧的 env / 数据文件不再生效，避免“配置消失”困惑。

    用户明确要求“不做自动迁移”，因此这里只告警、不迁移。
    """
    legacy_env = False
    for name in ("CPA_BASE_URL", "CPA_MANAGEMENT_KEY", "CPA_ADMINS", "CPA_ALIAS_FILE"):
        if os.environ.get(name):
            legacy_env = True
            break
    legacy_files = []
    data_dir = path.parent
    for name in ("cpa_aliases.json", "cpa_render_settings.json"):
        if (data_dir / name).is_file():
            legacy_files.append(str(data_dir / name))
    if not legacy_env and not legacy_files:
        return
    logger.warning(
        "检测到旧版 QuotaBot 配置："
        + ("环境变量 CPA_* " if legacy_env else "")
        + ("文件 " + ", ".join(legacy_files) if legacy_files else "")
        + "。这些不再生效；请改用 %s（/quota config show 查看，/quota config reload 重载）。未做自动迁移。",
        path,
    )


def get_snapshot() -> ConfigSnapshot:
    """返回当前快照；首次调用时惰性加载（缺失则生成默认文件）。"""
    with _lock:
        if _snapshot is None:
            _load(force=True, generate=True)
        return _snapshot  # type: ignore[return-value]


def generation() -> int:
    return _generation


def last_error() -> str:
    return _last_error


def snapshot_path() -> Path | None:
    return _loaded_path


def _load(*, force: bool, generate: bool) -> bool:
    """内部加载。返回是否发生了替换。调用方需持有 _lock。"""
    global _snapshot, _generation, _signature, _last_error, _loaded_path

    if _memory_only:
        if _snapshot is None:
            _snapshot = config_module.snapshot_from_raw(config_module.default_config_dict())
            _generation += 1
        return False

    path = config_file_path()
    signature = _read_signature(path)
    if not force and signature is not None and signature == _signature and _snapshot is not None:
        return False

    try:
        if signature is None:
            if not generate:
                raise ConfigError(f"配置文件不存在：{path}")
            generated = not path.is_file()
            raw = config_module.ensure_config_file(path)
            if generated:
                _warn_legacy(path)
        else:
            raw = read_config_file(path)
        snapshot = snapshot_from_raw(raw)
    except ConfigError as exc:
        _last_error = str(exc)
        if _snapshot is None:
            # 没有任何可用配置时，至少保证有默认值可用。
            _snapshot = config_module.snapshot_from_raw(config_module.default_config_dict())
            _generation += 1
        return False
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都不能让消息处理崩溃
        _last_error = f"配置解析失败：{exc}"
        if _snapshot is None:
            _snapshot = config_module.snapshot_from_raw(config_module.default_config_dict())
            _generation += 1
        return False

    previous = _snapshot
    _snapshot = snapshot
    _generation += 1
    _signature = _read_signature(path)
    _last_error = ""
    _loaded_path = path
    _invalidate(snapshot, previous)
    return True


def _cpa_connection_slice(snapshot: ConfigSnapshot) -> tuple[tuple[str, str, str, float], ...]:
    """实例连接相关字段的指纹。实例增删或连接变化都需要重建 HTTP 客户端。"""
    return tuple(
        (instance.name, instance.base_url, instance.management_key, instance.timeout)
        for instance in snapshot.cpa.instances
    )


def _invalidate(snapshot: ConfigSnapshot, previous: ConfigSnapshot | None = None) -> None:
    """按固定顺序失效下游缓存。任何异常只记录，不阻断消息处理。"""
    try:
        from . import aliases

        aliases.reset_alias_cache()
    except Exception as exc:
        logger.warning(f"配置重载后刷新别名失败：{exc}")
    try:
        from .render.themes import get_theme_registry

        registry = get_theme_registry()
        registry.refresh()
        theme = (snapshot.render.theme or "").strip().lower()
        if theme and not registry.is_valid_theme(theme):
            allowed = ", ".join(registry.list_canonical_names())
            logger.warning(
                f"配置 render.theme={snapshot.render.theme!r} 不是有效主题，将回退到 default。可用主题：{allowed}"
            )
    except Exception as exc:
        logger.warning(f"配置重载后刷新主题失败：{exc}")
    try:
        from .cpa.quota import clear_quota_cache

        clear_quota_cache()
    except Exception as exc:
        logger.warning(f"配置重载后清除额度缓存失败：{exc}")
    # 仅当连接相关配置（base_url/management_key/timeout）变化时才重建 HTTP 客户端，
    # 避免主题等无关修改无谓地重建连接池。
    connection_changed = previous is None or _cpa_connection_slice(previous) != _cpa_connection_slice(snapshot)
    if connection_changed:
        try:
            from .cpa.client import reset_client

            reset_client()
        except Exception as exc:
            logger.warning(f"配置重载后重建 HTTP 客户端失败：{exc}")
    for hook in list(_hooks):
        try:
            hook(snapshot)
        except Exception as exc:
            logger.warning(f"配置重载钩子执行失败：{exc}")


def ensure_fresh() -> bool:
    """廉价检查配置文件是否变化，变化则重载。返回是否重载。"""
    with _lock:
        if _memory_only:
            return False
        return _load(force=False, generate=True)


def reload_config() -> ConfigSnapshot:
    """强制从磁盘重载配置。"""
    with _lock:
        _load(force=True, generate=True)
        return _snapshot  # type: ignore[return-value]


def update_config(patch: Mapping[str, Any]) -> ConfigSnapshot:
    """把 patch 深合并进配置文件并重载（用于 /quota config 与主题设置）。

    磁盘模式下以**磁盘上的当前内容**为合并底，避免覆盖操作者刚手改的字段；
    内存模式只合并内存快照，绝不读写磁盘。
    """
    with _lock:
        if _memory_only:
            global _snapshot, _generation
            merged = config_module.deep_merge(dict(get_snapshot().raw), patch)
            _snapshot = snapshot_from_raw(merged)
            _generation += 1
            _invalidate(_snapshot)
            return _snapshot
        path = config_file_path()
        if path.is_file():
            base_raw = config_module.read_config_file(path)
        else:
            base_raw = dict(get_snapshot().raw)
        merged = config_module.deep_merge(base_raw, patch)
        current_sig = _read_signature(path)
        if _signature is not None and current_sig is not None and current_sig != _signature:
            raise ConfigError("配置文件已被外部修改，本次写入已放弃；下一条消息会自动重载最新配置，请重试。")
        config_module.atomic_write_json(path, merged)
        _load(force=True, generate=False)
        return _snapshot  # type: ignore[return-value]


def use_memory_config(data: ConfigSnapshot | Mapping[str, Any] | None = None) -> None:
    """测试用：只走内存，不读写文件。"""
    global _snapshot, _generation, _signature, _last_error, _loaded_path, _memory_only
    with _lock:
        _memory_only = True
        if isinstance(data, ConfigSnapshot):
            _snapshot = data
        elif isinstance(data, Mapping):
            _snapshot = snapshot_from_raw(data)
        else:
            _snapshot = snapshot_from_raw(config_module.default_config_dict())
        _generation += 1
        _signature = None
        _last_error = ""
        _loaded_path = None
        _invalidate(_snapshot)


def reset_state() -> None:
    """清空快照缓存，回到未加载状态。"""
    global _snapshot, _generation, _signature, _last_error, _loaded_path, _memory_only, _env_config, _path_override
    with _lock:
        _snapshot = None
        _generation = 0
        _signature = None
        _last_error = ""
        _loaded_path = None
        _memory_only = False
        _env_config = None
        _path_override = None


def invalidate_downstream() -> None:
    """仅供别名等外部改动后手动触发下游失效。"""
    with _lock:
        if _snapshot is not None:
            _invalidate(_snapshot)
