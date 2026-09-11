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
