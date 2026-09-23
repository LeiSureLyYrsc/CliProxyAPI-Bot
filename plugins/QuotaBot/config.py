"""QuotaBot 的 JSON 配置：定义、校验与原子写入。

NoneBot 的 `.env` 只保留 ``QUOTABOT_CONFIG_FILE``（指向本文件）；插件其余配置
（CPA 连接、火山账号、渲染设置、Server 模式、别名文件路径）都从该 JSON 读取。

本模块只做“纯数据”工作：解析、校验、默认值、原子写入。热重载与快照管理在
``state.py``。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

from .protocol import normalize_client_name, valid_client_name

DEFAULT_CONFIG_FILE = "data/quotabot_config.json"
DEFAULT_ALIASES_FILE = "data/quota_aliases.json"

DEFAULT_THEME = "default"
DEFAULT_CARDS_PER_ROW = 4
MIN_CARDS_PER_ROW = 1
MAX_CARDS_PER_ROW = 6

DEFAULT_CPA_BASE_URL = "http://127.0.0.1:8317"
DEFAULT_CLIENT_NAME = "Server"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8320


class ConfigError(ValueError):
    """配置解析 / 校验 / 写入失败，消息可直接发给管理员。

    继承 ``ValueError``，便于命令层沿用统一的非法取值处理。
    """


class Config(BaseModel):
    """NoneBot 环境配置。

    `.env` 里只保留这一项；其余配置都在 ``data/quotabot_config.json``。
    """

    quotabot_config_file: str = DEFAULT_CONFIG_FILE

    @field_validator("quotabot_config_file")
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


def _as_str_map(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        name = _as_str(key)
        token = _as_str(item)
        if name and token:
            cleaned[name] = token
    return cleaned


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


# --------------------------------------------------------------------------- #
# 各配置段
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CpaConfig:
    """CLIProxyAPI 管理接口连接与额度查询设置。"""

    base_url: str = DEFAULT_CPA_BASE_URL
    management_key: str = ""
    admins: tuple[str, ...] = ()
    codex_refresh_admin: tuple[str, ...] = ()
    timeout: float = 15.0
    oauth_poll_interval: float = 3.0
    oauth_timeout: float = 1800.0
    quota_timeout: float = 25.0
    quota_concurrency: int = 4
    quota_cache_ttl: float = 60.0
    quota_image: bool = True


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
class RenderConfig:
    """额度图渲染设置。theme 保持原始字符串，由渲染层解析为 canonical 名。"""

    theme: str = DEFAULT_THEME
    cards_per_row: int = DEFAULT_CARDS_PER_ROW


@dataclass(frozen=True)
class ServerConfig:
    """Server 模式（远程客户端额度聚合）。改动需重启生效。"""

    enabled: bool = False
    client_name: str = DEFAULT_CLIENT_NAME
    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_SERVER_PORT
    client_keys: Mapping[str, str] = field(default_factory=dict)
    request_timeout: float = 40.0
    ws_max_size: int = 1_048_576
    max_accounts: int = 200


@dataclass(frozen=True)
class ConfigSnapshot:
    """一次性完整配置快照。整体替换，不在原地修改。"""

    cpa: CpaConfig = field(default_factory=CpaConfig)
    volcengine: VolcengineConfig = field(default_factory=VolcengineConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    aliases_file: str = DEFAULT_ALIASES_FILE
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpa": {
                "base_url": self.cpa.base_url,
                "management_key": self.cpa.management_key,
                "admins": list(self.cpa.admins),
                "codex_refresh_admin": list(self.cpa.codex_refresh_admin),
                "timeout": self.cpa.timeout,
                "oauth_poll_interval": self.cpa.oauth_poll_interval,
                "oauth_timeout": self.cpa.oauth_timeout,
                "quota_timeout": self.cpa.quota_timeout,
                "quota_concurrency": self.cpa.quota_concurrency,
                "quota_cache_ttl": self.cpa.quota_cache_ttl,
                "quota_image": self.cpa.quota_image,
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
            "render": {
                "theme": self.render.theme,
                "cards_per_row": self.render.cards_per_row,
            },
            "server": {
                "enabled": self.server.enabled,
                "client_name": self.server.client_name,
                "host": self.server.host,
                "port": self.server.port,
                "client_keys": dict(self.server.client_keys),
                "request_timeout": self.server.request_timeout,
                "ws_max_size": self.server.ws_max_size,
                "max_accounts": self.server.max_accounts,
            },
            "aliases_file": self.aliases_file,
        }


def default_config_dict() -> dict[str, Any]:
    """返回默认配置的原始字典（用于首次生成文件）。"""
    return ConfigSnapshot().to_dict()


def _parse_cpa(raw: Any) -> CpaConfig:
    data = _as_mapping(raw)
    return CpaConfig(
        base_url=_strip_trailing_slash(_as_str(data.get("base_url"), DEFAULT_CPA_BASE_URL)) or DEFAULT_CPA_BASE_URL,
        management_key=_as_str(data.get("management_key")),
        admins=_as_str_list(data.get("admins")),
        codex_refresh_admin=_as_str_list(data.get("codex_refresh_admin")),
        timeout=max(1.0, _as_float(data.get("timeout"), 15.0)),
        oauth_poll_interval=max(1.0, _as_float(data.get("oauth_poll_interval"), 3.0)),
        oauth_timeout=max(1.0, _as_float(data.get("oauth_timeout"), 1800.0)),
        quota_timeout=max(1.0, _as_float(data.get("quota_timeout"), 25.0)),
        quota_concurrency=max(1, _as_int(data.get("quota_concurrency"), 4)),
        quota_cache_ttl=max(0.0, _as_float(data.get("quota_cache_ttl"), 60.0)),
        quota_image=_as_bool(data.get("quota_image"), True),
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


def _parse_render(raw: Any) -> RenderConfig:
    data = _as_mapping(raw)
    theme = _as_str(data.get("theme"), DEFAULT_THEME) or DEFAULT_THEME
    cards = _as_int(data.get("cards_per_row"), DEFAULT_CARDS_PER_ROW)
    cards = max(MIN_CARDS_PER_ROW, min(MAX_CARDS_PER_ROW, cards))
    return RenderConfig(theme=theme, cards_per_row=cards)


def _parse_server(raw: Any) -> ServerConfig:
    data = _as_mapping(raw)
    client_name = normalize_client_name(_as_str(data.get("client_name"), DEFAULT_CLIENT_NAME))
    if not valid_client_name(client_name):
        raise ConfigError("server.client_name 非法：1–32 字符，不能含空白或 / \\")
    client_keys = _as_str_map(data.get("client_keys"))
    for name in client_keys:
        if not valid_client_name(normalize_client_name(name)):
            raise ConfigError(f"server.client_keys 中客户端名称非法：{name}")
    return ServerConfig(
        enabled=_as_bool(data.get("enabled"), False),
        client_name=client_name,
        host=_as_str(data.get("host"), DEFAULT_SERVER_HOST) or DEFAULT_SERVER_HOST,
        port=max(1, min(65535, _as_int(data.get("port"), DEFAULT_SERVER_PORT))),
        client_keys=client_keys,
        request_timeout=max(1.0, _as_float(data.get("request_timeout"), 40.0)),
        ws_max_size=max(1024, _as_int(data.get("ws_max_size"), 1_048_576)),
        max_accounts=max(1, _as_int(data.get("max_accounts"), 200)),
    )


def snapshot_from_raw(raw: Mapping[str, Any]) -> ConfigSnapshot:
    """把原始 JSON 字典转换为强类型快照。"""
    if not isinstance(raw, Mapping):
        raise ConfigError("配置根节点必须是 JSON 对象。")
    aliases_file = _as_str(raw.get("aliases_file"), DEFAULT_ALIASES_FILE) or DEFAULT_ALIASES_FILE
    return ConfigSnapshot(
        cpa=_parse_cpa(raw.get("cpa")),
        volcengine=_parse_volcengine(raw.get("volcengine")),
        render=_parse_render(raw.get("render")),
        server=_parse_server(raw.get("server")),
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
