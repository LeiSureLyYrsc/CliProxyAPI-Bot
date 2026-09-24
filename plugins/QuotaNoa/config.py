"""QuotaNoa 的 JSON 配置：定义、校验与原子写入。

NoneBot 的 `.env` 只保留 ``QUOTANOA_CONFIG_FILE``（指向本文件）；插件其余配置
（CPA 实例、火山账号、渲染设置、别名文件路径）都从该 JSON 读取。

CPA 支持多个实例：``cpa.instances[]`` 中每一项都是一个独立连接，自带连接与
额度查询设置。``cpa.admins`` / ``cpa.codex_refresh_admin`` 是全局权限名单，
不随实例区分。

本模块只做“纯数据”工作：解析、校验、默认值、原子写入。热重载与快照管理在
``state.py``。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

from .model import LOCAL_CHANNELS, is_channel_name, normalize_channel

DEFAULT_CONFIG_FILE = "data/quotanoa_config.json"
DEFAULT_ALIASES_FILE = "data/quotanoa_aliases.json"

DEFAULT_THEME = "default"
DEFAULT_CARDS_PER_ROW = 3
MIN_CARDS_PER_ROW = 1
MAX_CARDS_PER_ROW = 6

DEFAULT_CPA_BASE_URL = "http://127.0.0.1:8317"
DEFAULT_REFRESH_CACHE_TTL = 60.0

#: 实例名 / 渠道账号名的通用长度上限。
MAX_NAME_LEN = 32


class ConfigError(ValueError):
    """配置解析 / 校验 / 写入失败，消息可直接发给管理员。

    继承 ``ValueError``，便于命令层沿用统一的非法取值处理。
    """


# --------------------------------------------------------------------------- #
# 名称校验（实例名等）
# --------------------------------------------------------------------------- #


def normalize_name(value: str) -> str:
    """规范化名称：去首尾空白 + Unicode NFC。"""
    return unicodedata.normalize("NFC", (value or "").strip())


def valid_name(value: str) -> bool:
    """名称是否合法：1–32 字符，不含空白、``/`` ``\\`` ``:``、控制字符，不以 ``-`` 开头。

    ``:`` 被禁用是因为缓存键与跨实例展示都用它作分隔符。
    """
    name = normalize_name(value)
    if not name or len(name) > MAX_NAME_LEN:
        return False
    if name.startswith("-"):
        return False
    if any(ch in name for ch in "/\\:") or any(ch.isspace() for ch in name):
        return False
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        return False
    return True


class Config(BaseModel):
    """NoneBot 环境配置。

    `.env` 里只保留这一项；其余配置都在 ``data/quotanoa_config.json``。
    """

    quotanoa_config_file: str = DEFAULT_CONFIG_FILE

    @field_validator("quotanoa_config_file")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return str(value or "").strip() or DEFAULT_CONFIG_FILE


# --------------------------------------------------------------------------- #
# 强类型取值助手（容忍人为手写 JSON 的类型偏差）
# --------------------------------------------------------------------------- #


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return ()
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _as_str(item)
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return tuple(cleaned)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


# --------------------------------------------------------------------------- #
# 各配置段
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CpaInstance:
    """单个 CLIProxyAPI 实例：独立连接 + 独立额度查询设置。"""

    name: str
    base_url: str = DEFAULT_CPA_BASE_URL
    management_key: str = ""
    timeout: float = 15.0
    oauth_poll_interval: float = 3.0
    oauth_timeout: float = 1800.0
    quota_timeout: float = 25.0
    quota_concurrency: int = 4
    quota_cache_ttl: float = 60.0
    quota_image: bool = True


@dataclass(frozen=True)
class CpaConfig:
    """CPA 全局设置 + 多个实例。

    ``admins`` / ``codex_refresh_admin`` 是全局权限名单；``instances`` 是连接列表。
    """

    admins: tuple[str, ...] = ()
    codex_refresh_admin: tuple[str, ...] = ()
    instances: tuple[CpaInstance, ...] = ()
    quota_default_channels: tuple[str, ...] = ()

    def get(self, name: str) -> CpaInstance | None:
        """按名称取实例（名称已规范化）；不存在返回 None。"""
        wanted = normalize_name(name)
        for instance in self.instances:
            if instance.name == wanted:
                return instance
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(instance.name for instance in self.instances)


@dataclass(frozen=True)
class VolcengineAccount:
    """火山方舟 Coding Plan 查询凭据（控制面 AccessKey）。"""

    name: str
    access_key_id: str
    secret_access_key: str
    region: str = "cn-beijing"


@dataclass(frozen=True)
class VolcengineConfig:
    accounts: tuple[VolcengineAccount, ...] = ()


@dataclass(frozen=True)
class WorkbuddyServer:
    """单个 WorkBuddy2API 网关（单端口同时提供 API 与控制台）。

    - ``base_url`` 形如 ``http://127.0.0.1:7863``。
    - 鉴权为**控制台账号 + 密码**（``username`` / ``password``）：插件先
      ``POST /api/login`` 换取会话 token，再 ``GET /api/config`` 读取网关的
      ``api_key``，最后用它调 ``/v1/quota``。会话自动缓存与过期重登。
    - ``api_key`` 为**可选直连覆盖**：填了就跳过登录，直接用它与 ``/v1/quota``
      通信（适用于已知道网关密钥、或未开控制台鉴权的场景）。
    """

    name: str
    base_url: str
    username: str = ""
    password: str = ""
    api_key: str = ""
    timeout: float = 30.0


@dataclass(frozen=True)
class WorkbuddyConfig:
    servers: tuple[WorkbuddyServer, ...] = ()


@dataclass(frozen=True)
class QoderServer:
    """单个 Qoder2OAPI 代理。

    - ``base_url`` 形如 ``http://127.0.0.1:8000``。
    - ``api_key`` 为代理 API Key（Bearer），来自 data/api_key.txt 或环境变量。
    """

    name: str
    base_url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    timeout: float = 30.0


@dataclass(frozen=True)
class QoderConfig:
    servers: tuple[QoderServer, ...] = ()


@dataclass(frozen=True)
class RenderConfig:
    """额度图渲染设置。theme 保持原始字符串，由渲染层解析为 canonical 名。"""

    theme: str = DEFAULT_THEME
    cards_per_row: int = DEFAULT_CARDS_PER_ROW


@dataclass(frozen=True)
class RefreshCacheConfig:
    """各渠道查询结果缓存时长（秒）。

    ``channels`` 以 canonical 渠道名为键；未列出的渠道用 ``default``。
    ``0`` 表示该渠道不缓存。
    """

    default: float = DEFAULT_REFRESH_CACHE_TTL
    channels: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigSnapshot:
    """一次性完整配置快照。整体替换，不在原地修改。"""

    cpa: CpaConfig = field(default_factory=CpaConfig)
    volcengine: VolcengineConfig = field(default_factory=VolcengineConfig)
    workbuddy: WorkbuddyConfig = field(default_factory=WorkbuddyConfig)
    qoder: QoderConfig = field(default_factory=QoderConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    refreshcache: RefreshCacheConfig = field(default_factory=RefreshCacheConfig)
    #: 别名文件路径；默认由本模块的 ``DEFAULT_ALIASES_FILE`` 决定，
    #: JSON 里的 ``aliases_file`` 仅作可选覆盖（旧配置兼容），不再写入生成文件。
    aliases_file: str = DEFAULT_ALIASES_FILE
    raw: Mapping[str, Any] = field(default_factory=dict)

    def cache_ttl(self, channel: str = "", *, fallback: float | None = None) -> float:
        """渠道级缓存 TTL（秒）。

        优先级：``refreshcache.channels[渠道]`` → ``fallback``（如 CPA 实例级
        ``quota_cache_ttl``）→ ``refreshcache.default``。
        """
        canonical = normalize_channel(channel) if channel else ""
        if canonical:
            ttl = self.refreshcache.channels.get(canonical)
            if ttl is not None:
                return ttl
        if fallback is not None:
            return fallback
        return self.refreshcache.default

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpa": {
                "admins": list(self.cpa.admins),
                "codex_refresh_admin": list(self.cpa.codex_refresh_admin),
                "quota_default_channels": list(self.cpa.quota_default_channels),
                "instances": [
                    {
                        "name": instance.name,
                        "base_url": instance.base_url,
                        "management_key": instance.management_key,
                        "timeout": instance.timeout,
                        "oauth_poll_interval": instance.oauth_poll_interval,
                        "oauth_timeout": instance.oauth_timeout,
                        "quota_timeout": instance.quota_timeout,
                        "quota_concurrency": instance.quota_concurrency,
                        "quota_cache_ttl": instance.quota_cache_ttl,
                        "quota_image": instance.quota_image,
                    }
                    for instance in self.cpa.instances
                ],
            },
            "volcengine": {
                "accounts": [
                    {
                        "name": account.name,
                        "access_key_id": account.access_key_id,
                        "secret_access_key": account.secret_access_key,
                        "region": account.region,
                    }
                    for account in self.volcengine.accounts
                ]
            },
            "workbuddy": {
                "servers": [
                    {
                        "name": server.name,
                        "base_url": server.base_url,
                        "username": server.username,
                        "password": server.password,
                        "api_key": server.api_key,
                        "timeout": server.timeout,
                    }
                    for server in self.workbuddy.servers
                ]
            },
            "qoder": {
                "servers": [
                    {
                        "name": server.name,
                        "base_url": server.base_url,
                        "api_key": server.api_key,
                        "timeout": server.timeout,
                    }
                    for server in self.qoder.servers
                ]
            },
            "refreshcache": {
                "default": self.refreshcache.default,
                "channels": dict(self.refreshcache.channels),
            },
            "render": {
                "theme": self.render.theme,
                "cards_per_row": self.render.cards_per_row,
            },
        }


def default_config_dict() -> dict[str, Any]:
    """返回默认配置的原始字典（用于首次生成文件）。"""
    return ConfigSnapshot().to_dict()


def _parse_cpa_instance(entry: Any) -> CpaInstance | None:
    data = _as_mapping(entry)
    raw_name = _as_str(data.get("name"))
    name = normalize_name(raw_name)
    if not name:
        return None
    if not valid_name(name):
        raise ConfigError(f"cpa.instances 中实例名称非法：{raw_name}（1–32 字符，不能含空白或 / \\ :）")
    if is_channel_name(name):
        raise ConfigError(
            f"实例名称不能与渠道名称同名：{raw_name}。"
            "实例是代理多平台的网关，请换一个名字（如 Home、Office）。"
        )
    return CpaInstance(
        name=name,
        base_url=_strip_trailing_slash(_as_str(data.get("base_url"), DEFAULT_CPA_BASE_URL)) or DEFAULT_CPA_BASE_URL,
        management_key=_as_str(data.get("management_key")),
        timeout=max(1.0, _as_float(data.get("timeout"), 15.0)),
        oauth_poll_interval=max(1.0, _as_float(data.get("oauth_poll_interval"), 3.0)),
        oauth_timeout=max(1.0, _as_float(data.get("oauth_timeout"), 1800.0)),
        quota_timeout=max(1.0, _as_float(data.get("quota_timeout"), 25.0)),
        quota_concurrency=max(1, _as_int(data.get("quota_concurrency"), 4)),
        quota_cache_ttl=max(0.0, _as_float(data.get("quota_cache_ttl"), 60.0)),
        quota_image=_as_bool(data.get("quota_image"), True),
    )


def _parse_default_channels(value: Any) -> tuple[str, ...]:
    """解析 /quotanoa 默认渠道：只保留本地渠道（CPA 渠道靠显式参数或 all）。"""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in _as_str_list(value):
        canonical = normalize_channel(item)
        if not canonical or canonical not in LOCAL_CHANNELS or canonical in seen:
            continue
        seen.add(canonical)
        cleaned.append(canonical)
    return tuple(cleaned)


def _parse_cpa(raw: Any) -> CpaConfig:
    data = _as_mapping(raw)
    raw_instances = data.get("instances")
    instances: list[CpaInstance] = []
    seen: set[str] = set()
    if isinstance(raw_instances, (list, tuple)):
        for entry in raw_instances:
            instance = _parse_cpa_instance(entry)
            if instance is None:
                continue
            if instance.name in seen:
                raise ConfigError(f"cpa.instances 中实例名称重复：{instance.name}")
            seen.add(instance.name)
            instances.append(instance)
    return CpaConfig(
        admins=_as_str_list(data.get("admins")),
        codex_refresh_admin=_as_str_list(data.get("codex_refresh_admin")),
        instances=tuple(instances),
        quota_default_channels=_parse_default_channels(data.get("quota_default_channels")),
    )


def _parse_volcengine(raw: Any) -> VolcengineConfig:
    data = _as_mapping(raw)
    raw_accounts = data.get("accounts")
    accounts: list[VolcengineAccount] = []
    seen: set[str] = set()
    if isinstance(raw_accounts, (list, tuple)):
        for item in raw_accounts:
            entry = _as_mapping(item)
            name = _as_str(entry.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            accounts.append(
                VolcengineAccount(
                    name=name,
                    access_key_id=_as_str(entry.get("access_key_id")),
                    secret_access_key=_as_str(entry.get("secret_access_key")),
                    region=_as_str(entry.get("region"), "cn-beijing") or "cn-beijing",
                )
            )
    return VolcengineConfig(accounts=tuple(accounts))


def _parse_workbuddy(raw: Any) -> WorkbuddyConfig:
    data = _as_mapping(raw)
    raw_servers = data.get("servers")
    servers: list[WorkbuddyServer] = []
    seen: set[str] = set()
    if isinstance(raw_servers, (list, tuple)):
        for item in raw_servers:
            entry = _as_mapping(item)
            raw_name = _as_str(entry.get("name"))
            name = normalize_name(raw_name)
            base_url = _strip_trailing_slash(_as_str(entry.get("base_url")))
            if not name or not base_url or name in seen:
                continue
            if not valid_name(name):
                raise ConfigError(
                    f"workbuddy.servers 中网关名称非法：{raw_name}（1–{MAX_NAME_LEN} 字符，不能含空白或 / \\ :）"
                )
            if is_channel_name(name):
                raise ConfigError(
                    f"workbuddy.servers 网关名称不能与渠道名称同名：{raw_name}。"
                    "请换一个名字（如 wb-main、wb-backup）。"
                )
            seen.add(name)
            servers.append(
                WorkbuddyServer(
                    name=name,
                    base_url=base_url,
                    username=_as_str(entry.get("username")),
                    password=_as_str(entry.get("password")),
                    api_key=_as_str(entry.get("api_key")),
                    timeout=max(1.0, _as_float(entry.get("timeout"), 30.0)),
                )
            )
    return WorkbuddyConfig(servers=tuple(servers))


def _parse_qoder(raw: Any) -> QoderConfig:
    data = _as_mapping(raw)
    raw_servers = data.get("servers")
    servers: list[QoderServer] = []
    seen: set[str] = set()
    if isinstance(raw_servers, (list, tuple)):
        for item in raw_servers:
            entry = _as_mapping(item)
            raw_name = _as_str(entry.get("name"))
            name = normalize_name(raw_name)
            base_url = _strip_trailing_slash(
                _as_str(entry.get("base_url"), "http://127.0.0.1:8000")
            )
            if not name or not base_url or name in seen:
                continue
            if not valid_name(name):
                raise ConfigError(
                    f"qoder.servers 中代理名称非法：{raw_name}（1–{MAX_NAME_LEN} 字符，不能含空白或 / \\ :）"
                )
            if is_channel_name(name):
                raise ConfigError(
                    f"qoder.servers 代理名称不能与渠道名称同名：{raw_name}。"
                    "请换一个名字（如 qoder-main、qoder-backup）。"
                )
            seen.add(name)
            servers.append(
                QoderServer(
                    name=name,
                    base_url=base_url,
                    api_key=_as_str(entry.get("api_key")),
                    timeout=max(1.0, _as_float(entry.get("timeout"), 30.0)),
                )
            )
    return QoderConfig(servers=tuple(servers))


def _parse_render(raw: Any) -> RenderConfig:
    data = _as_mapping(raw)
    theme = _as_str(data.get("theme"), DEFAULT_THEME) or DEFAULT_THEME
    cards = _as_int(data.get("cards_per_row"), DEFAULT_CARDS_PER_ROW)
    cards = max(MIN_CARDS_PER_ROW, min(MAX_CARDS_PER_ROW, cards))
    return RenderConfig(theme=theme, cards_per_row=cards)


def _parse_refreshcache(raw: Any) -> RefreshCacheConfig:
    data = _as_mapping(raw)
    default = max(0.0, _as_float(data.get("default"), DEFAULT_REFRESH_CACHE_TTL))
    channels: dict[str, float] = {}
    for key, value in _as_mapping(data.get("channels")).items():
        canonical = normalize_channel(str(key))
        if not canonical:
            continue
        channels[canonical] = max(0.0, _as_float(value, default))
    return RefreshCacheConfig(default=default, channels=channels)


def snapshot_from_raw(raw: Mapping[str, Any]) -> ConfigSnapshot:
    """把原始 JSON 字典转换为强类型快照。"""
    if not isinstance(raw, Mapping):
        raise ConfigError("配置根节点必须是 JSON 对象。")
    aliases_file = _as_str(raw.get("aliases_file"), DEFAULT_ALIASES_FILE) or DEFAULT_ALIASES_FILE
    return ConfigSnapshot(
        cpa=_parse_cpa(raw.get("cpa")),
        volcengine=_parse_volcengine(raw.get("volcengine")),
        workbuddy=_parse_workbuddy(raw.get("workbuddy")),
        qoder=_parse_qoder(raw.get("qoder")),
        render=_parse_render(raw.get("render")),
        refreshcache=_parse_refreshcache(raw.get("refreshcache")),
        aliases_file=aliases_file,
        raw=dict(raw),
    )


# --------------------------------------------------------------------------- #
# 文件读写
# --------------------------------------------------------------------------- #


def read_config_file(path: Path) -> dict[str, Any]:
    """读取并解析 JSON 配置。文件不存在或非法时抛 ConfigError。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{path}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是 JSON 对象。")
    return data


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    """以 tmp + replace 方式原子写入 JSON。"""
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"无法写入配置文件：{exc}") from exc


def ensure_config_file(path: Path) -> dict[str, Any]:
    """确保配置文件存在并返回其内容；缺失时生成默认文件。"""
    if path.is_file():
        return read_config_file(path)
    data = default_config_dict()
    atomic_write_json(path, data)
    return data


def deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并 patch 到 base（返回新 dict，不修改入参）。"""
    merged: dict[str, Any] = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged
