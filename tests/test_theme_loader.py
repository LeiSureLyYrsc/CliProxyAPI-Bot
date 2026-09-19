from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "cpaplugin"
_pkg = types.ModuleType("cpaplugin")
_pkg.__path__ = [str(_plugin_dir)]
sys.modules.setdefault("cpaplugin", _pkg)

from cpaplugin.render import build_platform_html, get_render_settings_adapter
from cpaplugin.render_settings import (
    DEFAULT_CARDS_PER_ROW,
    DEFAULT_THEME,
    RenderSettings,
    get_render_settings,
    load_render_settings,
    reset_render_settings_cache,
    set_cards_per_row,
    set_theme,
    use_memory_render_settings,
)
from cpaplugin.theme_loader import ThemeRegistry, reset_theme_registry


class ThemeLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_render_settings_cache()
        reset_theme_registry()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _create_theme(
        self,
        themes_dir: Path,
        folder_name: str,
        *,
        name: str,
        display_name: str = "Test",
        css_class: str = "theme-test",
        aliases: list[str] | None = None,
        css: str = ".test { color: red; }",
        wrapper: str = '<div class="test">__TITLE__ __GRID__ __PAGE_NOTE__</div>',
    ) -> Path:
        theme_folder = themes_dir / folder_name
        theme_folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": name,
            "display_name": display_name,
            "css_class": css_class,
            "aliases": aliases or [],
        }
        (theme_folder / "theme.json").write_text(json.dumps(meta), encoding="utf-8")
        (theme_folder / "theme.css").write_text(css, encoding="utf-8")
        (theme_folder / "wrapper.html").write_text(wrapper, encoding="utf-8")
        return theme_folder

    def test_discovery_and_alias_resolution(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"
        base_css = base_dir / "base.css"
        base_css.write_text("/* base */", encoding="utf-8")

        self._create_theme(
            themes_dir,
            "cyberpunk",
            name="cyberpunk",
            display_name="Cyberpunk",
            aliases=["neon", "cp2077"],
        )

        registry = ThemeRegistry(themes_dir=themes_dir, base_css_path=base_css)
        registry.load()

        canonical_names = registry.list_canonical_names()
        self.assertIn("cyberpunk", canonical_names)
        self.assertIn("default", canonical_names)

        # Name / alias resolution
        self.assertEqual(registry.resolve_theme_name("cyberpunk"), "cyberpunk")
        self.assertEqual(registry.resolve_theme_name("neon"), "cyberpunk")
        self.assertEqual(registry.resolve_theme_name("cp2077"), "cyberpunk")
        self.assertEqual(registry.resolve_theme_name("default"), "default")
        self.assertEqual(registry.resolve_theme_name("shadcn"), "default")
        self.assertEqual(registry.resolve_theme_name("non_existent"), "default")

    def test_malformed_metadata_rejected_with_deterministic_behavior(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)

        # 1. Invalid json
        bad_json_dir = themes_dir / "badjson"
        bad_json_dir.mkdir()
        (bad_json_dir / "theme.json").write_text("{invalid json", encoding="utf-8")
        (bad_json_dir / "theme.css").write_text("body {}", encoding="utf-8")
        (bad_json_dir / "wrapper.html").write_text("<div>__GRID__</div>", encoding="utf-8")

        # 2. Missing files
        missing_dir = themes_dir / "missingfiles"
        missing_dir.mkdir()
        (missing_dir / "theme.json").write_text('{"name": "missingfiles"}', encoding="utf-8")

        # 3. Invalid name with special characters
        invalid_name_dir = themes_dir / "invalid_name"
        invalid_name_dir.mkdir()
        (invalid_name_dir / "theme.json").write_text('{"name": "invalid name with space"}', encoding="utf-8")
        (invalid_name_dir / "theme.css").write_text("body {}", encoding="utf-8")
        (invalid_name_dir / "wrapper.html").write_text("<div>__GRID__</div>", encoding="utf-8")

        registry = ThemeRegistry(themes_dir=themes_dir)
        registry.load()

        self.assertNotIn("badjson", registry.list_canonical_names())
        self.assertNotIn("missingfiles", registry.list_canonical_names())
        self.assertNotIn("invalid name with space", registry.list_canonical_names())
        # Default fallback still works
        self.assertEqual(registry.resolve_theme_name("anything"), "default")

    def test_folder_name_css_class_and_required_placeholders_are_validated(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"

        self._create_theme(
            themes_dir,
            "folder_mismatch",
            name="different_name",
        )
        self._create_theme(
            themes_dir,
            "badclass",
            name="badclass",
            css_class='theme-bad" onclick="alert(1)',
        )
        self._create_theme(
            themes_dir,
            "missinggrid",
            name="missinggrid",
            wrapper="<div>__PAGE_NOTE__</div>",
        )
        self._create_theme(
            themes_dir,
            "missingnote",
            name="missingnote",
            wrapper='<div class="grid">__GRID__</div>',
        )

        registry = ThemeRegistry(themes_dir=themes_dir)
        registry.load()

        names = registry.list_canonical_names()
        self.assertNotIn("different_name", names)
        self.assertNotIn("badclass", names)
        self.assertNotIn("missinggrid", names)
        self.assertNotIn("missingnote", names)

    def test_security_validation_in_wrapper_and_css(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)

        # XSS script in wrapper
        self._create_theme(
            themes_dir,
            "xss1",
            name="xss1",
            wrapper='<div>__GRID__<script>alert(1)</script></div>',
        )
        # Event handler in wrapper
        self._create_theme(
            themes_dir,
            "xss2",
            name="xss2",
            wrapper='<div onclick="alert(1)">__GRID__</div>',
        )
        # External http import in CSS
        self._create_theme(
            themes_dir,
            "xss3",
            name="xss3",
            css='@import url("http://evil.com/a.css");',
        )
        # Disallowed placeholder like __CSS__ or __SECRET__ in wrapper
        self._create_theme(
            themes_dir,
            "badplaceholder",
            name="badplaceholder",
            wrapper='<div>__CSS__ __GRID__</div>',
        )

        registry = ThemeRegistry(themes_dir=themes_dir)
        registry.load()

        self.assertNotIn("xss1", registry.list_canonical_names())
        self.assertNotIn("xss2", registry.list_canonical_names())
        self.assertNotIn("xss3", registry.list_canonical_names())
        self.assertNotIn("badplaceholder", registry.list_canonical_names())

    def test_duplicate_alias_handling(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"

        self._create_theme(
            themes_dir,
            "theme_a",
            name="theme_a",
            aliases=["shared_alias", "alias_a"],
        )
        self._create_theme(
            themes_dir,
            "theme_b",
            name="theme_b",
            aliases=["shared_alias", "alias_b"],
        )

        registry = ThemeRegistry(themes_dir=themes_dir)
        registry.load()

        # First registered wins for collision
        self.assertEqual(registry.resolve_theme_name("shared_alias"), "theme_a")
        self.assertEqual(registry.resolve_theme_name("alias_b"), "theme_b")

    def test_legacy_json_migration_shadcn_to_default(self) -> None:
        reset_render_settings_cache()
        target_file = Path(self.temp_dir.name) / "cpa_render_settings.json"
        target_file.write_text(json.dumps({"theme": "shadcn", "cards_per_row": 3}), encoding="utf-8")

        import cpaplugin.render_settings as rs

        orig_get_path = rs.get_render_settings_path
        rs.get_render_settings_path = lambda cfg=None: target_file
        try:
            settings = load_render_settings()
            # Stored 'shadcn' resolves canonical 'default'
            self.assertEqual(settings.theme, "default")
            self.assertEqual(settings.cards_per_row, 3)

            # Test adapter returns canonical theme
            adapter = get_render_settings_adapter()
            self.assertEqual(adapter["theme"], "default")
            self.assertEqual(adapter["cards_per_row"], 3)
        finally:
            rs.get_render_settings_path = orig_get_path

    def test_dynamic_drop_in_theme_refresh(self) -> None:
        base_dir = Path(self.temp_dir.name)
        themes_dir = base_dir / "themes"
        base_css = base_dir / "base.css"
        base_css.write_text("", encoding="utf-8")

        registry = ThemeRegistry(themes_dir=themes_dir, base_css_path=base_css)
        registry.load()
        self.assertEqual(registry.list_canonical_names(), ["default"])

        # Dynamically drop in new theme
        self._create_theme(
            themes_dir,
            "dracula",
            name="dracula",
            display_name="Dracula",
        )

        # Before refresh
        self.assertEqual(registry.resolve_theme_name("dracula"), "default")

        # After refresh
        registry.refresh()
        self.assertIn("dracula", registry.list_canonical_names())
        self.assertEqual(registry.resolve_theme_name("dracula"), "dracula")
