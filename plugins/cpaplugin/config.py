import unicodedata

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    """CliProxyAPI Management API 连接与权限配置。"""

    cpa_base_url: str = "http://127.0.0.1:8317"
    cpa_management_key: str = ""
    cpa_admins: list[str] = Field(default_factory=list)
    codex_refresh_admin: list[str] = Field(default_factory=list)
    cpa_timeout: float = 15.0
    cpa_oauth_poll_interval: float = 3.0
    cpa_oauth_timeout: float = 1800.0
    cpa_quota_timeout: float = 25.0
    cpa_quota_concurrency: int = 4
    cpa_quota_cache_ttl: float = 60.0
    cpa_quota_image: bool = True
    cpa_quota_image_width: int = 520
    cpa_alias_file: str = "data/cpa_aliases.json"
    cpa_aliases: dict[str, str] = Field(default_factory=dict)
    server_mode: bool = False
    client_name: str = "Server"
    cpa_server_host: str = "127.0.0.1"
    cpa_server_port: int = 8320
    cpa_server_client_keys: dict[str, str] = Field(default_factory=dict)
    cpa_server_request_timeout: float = 40.0
    cpa_server_ws_max_size: int = 1_048_576
    cpa_server_max_accounts: int = 200

    @field_validator("cpa_aliases")
    @classmethod
    def normalize_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key).strip(): str(alias).strip() for key, alias in value.items() if str(key).strip() and str(alias).strip()}

    @field_validator("cpa_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("cpa_management_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()

    @field_validator("cpa_admins", "codex_refresh_admin")
    @classmethod
    def normalize_admins(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("client_name")
    @classmethod
    def normalize_client_name(cls, value: str) -> str:
        name = unicodedata.normalize("NFC", value.strip() or "Server")
        if not _valid_client_name(name):
            raise ValueError("CLIENT_NAME 非法：1–32 字符，不能含空白或 / \\，且不能以 - 开头。")
        return name

    @field_validator("cpa_server_client_keys")
    @classmethod
    def normalize_client_keys(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, secret in value.items():
            name = unicodedata.normalize("NFC", str(key).strip())
            token = str(secret).strip()
            if not name or not token:
                continue
            if not _valid_client_name(name):
                raise ValueError(f"客户端名称非法：{name}")
            cleaned[name] = token
        return cleaned


def _valid_client_name(name: str) -> bool:
    from .protocol import valid_client_name

    return valid_client_name(name)
