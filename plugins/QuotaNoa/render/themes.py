from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")
CSS_CLASS_PATTERN = re.compile(r"^theme-[a-z0-9_-]+$")

FORBIDDEN_WRAPPER_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"</script>", re.IGNORECASE),
    re.compile(r"<style[\s>]", re.IGNORECASE),
    re.compile(r"</style>", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE),  # onclick, onload, onerror, etc.
    re.compile(r"javascript:", re.IGNORECASE),
]

FORBIDDEN_CSS_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"</script>", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
]

ALLOWED_PLACEHOLDERS = {"__TITLE__", "__GRID__", "__PAGE_NOTE__"}
PLACEHOLDER_REGEX = re.compile(r"__[A-Z0-9_]+__")


@dataclass(frozen=True)
class ThemeMeta:
    name: str
    display_name: str
    css_class: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


@dataclass(frozen=True)
class Theme:
    meta: ThemeMeta
    css: str
    wrapper: str

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def display_name(self) -> str:
        return self.meta.display_name

    @property
    def css_class(self) -> str:
        return self.meta.css_class

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.meta.aliases

    @property
    def description(self) -> str:
        return self.meta.description

    def render_wrapper(self, *, title: str, grid: str, page_note: str) -> str:
        """安全替换占位符，不使用 eval/format。"""
        res = self.wrapper.replace("__TITLE__", title)
        res = res.replace("__GRID__", grid)
        res = res.replace("__PAGE_NOTE__", page_note)
        return res


def validate_wrapper_security(content: str) -> None:
    for pattern in FORBIDDEN_WRAPPER_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"Wrapper contains forbidden pattern: {pattern.pattern}")
    # Validate placeholders
    placeholders = set(PLACEHOLDER_REGEX.findall(content))
    invalid = placeholders - ALLOWED_PLACEHOLDERS
    if invalid:
        raise ValueError(f"Wrapper contains invalid placeholders: {invalid}")
    required = {"__GRID__", "__PAGE_NOTE__"}
    missing = required - placeholders
    if missing:
        raise ValueError(f"Wrapper is missing required placeholders: {missing}")


def validate_css_security(content: str) -> None:
    for pattern in FORBIDDEN_CSS_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"CSS contains forbidden pattern: {pattern.pattern}")


class ThemeRegistry:
    def __init__(self, themes_dir: Path | None = None, base_css_path: Path | None = None) -> None:
        self._assets_dir = Path(__file__).resolve().parent / "assets"
        self._themes_dir = themes_dir or (self._assets_dir / "themes")
        self._base_css_path = base_css_path or (self._assets_dir / "base.css")
        self._themes: dict[str, Theme] = {}
        self._alias_map: dict[str, str] = {}
        self._base_css: str = ""
        self._loaded: bool = False

    def load(self, *, force: bool = False) -> None:
        if self._loaded and not force:
            return

        self._themes = {}
        self._alias_map = {}
        self._base_css = ""

        # Load base.css if exists, fallback to empty string
        if self._base_css_path.is_file():
            base_content = self._base_css_path.read_text(encoding="utf-8")
            validate_css_security(base_content)
            self._base_css = base_content

        if not self._themes_dir.is_dir():
            self._ensure_fallback_default()
            self._loaded = True
            return

        # Scan subdirectories
        for item in sorted(self._themes_dir.iterdir()):
            if not item.is_dir():
                continue
            folder_name = item.name.lower()
            if not NAME_PATTERN.match(folder_name):
                continue

            meta_file = item / "theme.json"
            css_file = item / "theme.css"
            wrapper_file = item / "wrapper.html"

            if not (meta_file.is_file() and css_file.is_file() and wrapper_file.is_file()):
                continue

            try:
                meta_raw = json.loads(meta_file.read_text(encoding="utf-8"))
                if not isinstance(meta_raw, dict):
                    continue

                canonical_name = str(meta_raw.get("name") or "").strip().lower()
                if not canonical_name or not NAME_PATTERN.match(canonical_name):
                    continue
                if canonical_name != folder_name:
                    continue

                display_name = str(meta_raw.get("display_name") or canonical_name)
                css_class = str(meta_raw.get("css_class") or f"theme-{canonical_name}")
                if not CSS_CLASS_PATTERN.match(css_class):
                    continue
                description = str(meta_raw.get("description") or "")
                raw_aliases = meta_raw.get("aliases") or []
                aliases_list: list[str] = []
                if isinstance(raw_aliases, list):
                    for a in raw_aliases:
                        if isinstance(a, str) and a.strip():
                            al = a.strip().lower()
                            if NAME_PATTERN.match(al):
                                aliases_list.append(al)

                css_content = css_file.read_text(encoding="utf-8")
                validate_css_security(css_content)

                wrapper_content = wrapper_file.read_text(encoding="utf-8")
                validate_wrapper_security(wrapper_content)

                # Check unique name / aliases collisions. A canonical name may not
                # take over an alias already claimed by an earlier theme.
                if canonical_name in self._themes or canonical_name in self._alias_map:
                    continue

                meta = ThemeMeta(
                    name=canonical_name,
                    display_name=display_name,
                    css_class=css_class,
                    aliases=tuple(aliases_list),
                    description=description,
                )

                theme = Theme(
                    meta=meta,
                    css=css_content,
                    wrapper=wrapper_content,
                )
                self._themes[canonical_name] = theme

                # Register aliases
                self._alias_map[canonical_name] = canonical_name
                self._alias_map[folder_name] = canonical_name
                for a in aliases_list:
                    if a not in self._alias_map:
                        self._alias_map[a] = canonical_name

            except Exception:
                # Malformed metadata / security validation failed - skip deterministically
                continue

        self._ensure_fallback_default()
        self._loaded = True

    def _ensure_fallback_default(self) -> None:
        if "default" not in self._themes:
            # Fallback synthetic default theme
            meta = ThemeMeta(
                name="default",
                display_name="默认",
                css_class="theme-shadcn",
                aliases=("shadcn",),
                description="默认主题",
            )
            default_wrapper = '<div class="grid">__GRID__</div>__PAGE_NOTE__'
            self._themes["default"] = Theme(
                meta=meta,
                css="",
                wrapper=default_wrapper,
            )
        # Ensure 'shadcn' and 'default' map to default
        self._alias_map["default"] = "default"
        self._alias_map["shadcn"] = "default"

    @property
    def base_css(self) -> str:
        self.load()
        return self._base_css

    def get_theme(self, name: str) -> Theme:
        self.load()
        canonical = self.resolve_theme_name(name)
        return self._themes.get(canonical, self._themes["default"])

    def resolve_theme_name(self, name: str | None) -> str:
        self.load()
        if not name:
            return "default"
        clean = name.strip().lower()
        return self._alias_map.get(clean, "default")

    def is_valid_theme(self, name: str) -> bool:
        self.load()
        clean = (name or "").strip().lower()
        return clean in self._alias_map

    def list_canonical_names(self) -> list[str]:
        self.load()
        return list(self._themes.keys())

    def get_alias_map(self) -> dict[str, str]:
        self.load()
        return dict(self._alias_map)

    def refresh(self) -> None:
        self.load(force=True)


_global_registry = ThemeRegistry()


def get_theme_registry() -> ThemeRegistry:
    return _global_registry


def reset_theme_registry() -> None:
    _global_registry.refresh()
