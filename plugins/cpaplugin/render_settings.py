from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonebot import get_plugin_config

from .config import Config

DEFAULT_THEME = "shadcn"
DEFAULT_CARDS_PER_ROW = 4
THEME_ALIASES: dict[str, str] = {
    "default": "shadcn",
    "shadcn": "shadcn",
    "mac": "mac",
    "md3": "md3",
    "winxp": "winxp",
    "win7": "win7",
}
ALLOWED_THEMES: tuple[str, ...] = ("shadcn", "mac", "md3", "winxp", "win7")
MIN_CARDS_PER_ROW = 1
MAX_CARDS_PER_ROW = 6

_store: RenderSettings | None = None
_memory_only = False


@dataclass
class RenderSettings:
    theme: str = DEFAULT_THEME
    cards_per_row: int = DEFAULT_CARDS_PER_ROW
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.extra)
        data["theme"] = self.theme
        data["cards_per_row"] = self.cards_per_row
        return data


def get_render_settings_path(cfg: Config | None = None) -> Path:
    try:
        config = cfg or get_plugin_config(Config)
        alias_file = Path(config.cpa_alias_file)
    except Exception:
        alias_file = Path("data/cpa_aliases.json")
    return alias_file.parent / "cpa_render_settings.json"


def normalize_theme(value: str) -> str:
    text = (value or "").strip().lower()
    if text in THEME_ALIASES:
        return THEME_ALIASES[text]
    allowed = ", ".join(f"{k}" for k in THEME_ALIASES)
    raise ValueError(f"未知主题「{value}」。可选主题：{allowed}")


def normalize_theme_or_default(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_THEME
    text = value.strip().lower()
    return THEME_ALIASES.get(text, DEFAULT_THEME)


def normalize_cards_per_row(value: Any) -> int:
    try:
        num = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"每行卡片数必须为 {MIN_CARDS_PER_ROW} 到 {MAX_CARDS_PER_ROW} 的整数。")
    if not (MIN_CARDS_PER_ROW <= num <= MAX_CARDS_PER_ROW):
        raise ValueError(f"每行卡片数必须为 {MIN_CARDS_PER_ROW} 到 {MAX_CARDS_PER_ROW} 的整数。")
    return num


def normalize_cards_per_row_or_default(value: Any) -> int:
    try:
        num = int(value)
        if MIN_CARDS_PER_ROW <= num <= MAX_CARDS_PER_ROW:
            return num
    except (ValueError, TypeError):
        pass
    return DEFAULT_CARDS_PER_ROW


def load_render_settings() -> RenderSettings:
    global _store
    if _memory_only and _store is not None:
        return _store

    path = get_render_settings_path()
    if not path.is_file():
        settings = RenderSettings(theme=DEFAULT_THEME, cards_per_row=DEFAULT_CARDS_PER_ROW)
        _store = settings
        return settings

    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception:
        settings = RenderSettings(theme=DEFAULT_THEME, cards_per_row=DEFAULT_CARDS_PER_ROW)
        _store = settings
        return settings

    if not isinstance(data, dict):
        settings = RenderSettings(theme=DEFAULT_THEME, cards_per_row=DEFAULT_CARDS_PER_ROW)
        _store = settings
        return settings

    theme = normalize_theme_or_default(data.get("theme"))
    cards_per_row = normalize_cards_per_row_or_default(data.get("cards_per_row"))
    extra = {k: v for k, v in data.items() if k not in ("theme", "cards_per_row")}
    settings = RenderSettings(theme=theme, cards_per_row=cards_per_row, extra=extra)
    _store = settings
    return settings


def save_render_settings(settings: RenderSettings) -> None:
    global _store
    if _memory_only:
        _store = settings
        return

    path = get_render_settings_path()
    temp_path = path.with_name(f".cpa_render_settings_{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
        _store = settings
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"无法保存渲染设置：{exc}") from exc


def get_render_settings() -> RenderSettings:
    if _store is not None:
        return _store
    return load_render_settings()


def set_theme(theme_name: str) -> RenderSettings:
    target_theme = normalize_theme(theme_name)
    current = load_render_settings()
    updated = RenderSettings(
        theme=target_theme,
        cards_per_row=current.cards_per_row,
        extra=dict(current.extra),
    )
    save_render_settings(updated)
    return updated


def set_cards_per_row(count: int | str) -> RenderSettings:
    target_count = normalize_cards_per_row(count)
    current = load_render_settings()
    updated = RenderSettings(
        theme=current.theme,
        cards_per_row=target_count,
        extra=dict(current.extra),
    )
    save_render_settings(updated)
    return updated


def use_memory_render_settings(data: RenderSettings | dict[str, Any] | None = None) -> None:
    global _store, _memory_only
    _memory_only = True
    if isinstance(data, RenderSettings):
        _store = data
    elif isinstance(data, dict):
        theme = normalize_theme_or_default(data.get("theme"))
        cards_per_row = normalize_cards_per_row_or_default(data.get("cards_per_row"))
        extra = {k: v for k, v in data.items() if k not in ("theme", "cards_per_row")}
        _store = RenderSettings(theme=theme, cards_per_row=cards_per_row, extra=extra)
    else:
        _store = RenderSettings(theme=DEFAULT_THEME, cards_per_row=DEFAULT_CARDS_PER_ROW)


def reset_render_settings_cache() -> None:
    global _store, _memory_only
    _store = None
    _memory_only = False
