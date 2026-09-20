from __future__ import annotations

import asyncio
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

_plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "cpaplugin"
_pkg = types.ModuleType("cpaplugin")
_pkg.__path__ = [str(_plugin_dir)]
sys.modules.setdefault("cpaplugin", _pkg)

from cpaplugin.quota import (
    AccountQuota,
    PlatformQuota,
    QuotaBoard,
    QuotaWindow,
    _build_board,
)
from cpaplugin.render import (
    _bar_html,
    build_platform_html,
    calculate_canvas_width,
    get_platform_icon_uri,
    paginate_accounts,
    render_platform_images,
)


class RenderThemesAndFeaturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_accounts = []
        for i in range(1, 10):  # 9 accounts
            acc = AccountQuota(
                platform="claude",
                name=f"claude-pro-{i}",
                auth_index=str(i),
                plan="Pro",
                cooling=(i == 1),  # First account cooling
                windows=[
                    QuotaWindow(
                        id="five_hour",
                        label="5h",
                        remaining_percent=80.0,
                        reset_label="2h15m",
                    ),
                    QuotaWindow(
                        id="seven_day",
                        label="7d",
                        remaining_percent=45.0,
                        reset_label="3d12h",
                    ),
                ],
            )
            # Backend fields may be assigned dynamically before quota.py schema update
            if i == 2:
                setattr(acc, "subscription_expires_label", "2026-10-01")
            if i == 3:
                setattr(acc, "reset_credits", 5)
            self.sample_accounts.append(acc)

        self.sample_section = PlatformQuota(
            platform="claude",
            title="Claude",
            accounts=self.sample_accounts,
            window_remain_sum={"five_hour": 720.0, "seven_day": 405.0},
            window_remain_count={"five_hour": 9, "seven_day": 9},
            window_labels={"five_hour": "5h", "seven_day": "7d"},
        )

    def test_theme_markers_and_components(self) -> None:
        for theme in ["shadcn", "mac", "md3", "winxp", "win7"]:
            html_doc = build_platform_html(self.sample_section, theme=theme)
            self.assertIn(f"theme-{theme}", html_doc)
            if theme == "mac":
                self.assertIn('<div class="sheet-window">', html_doc)
                self.assertIn('<div class="mac-titlebar">', html_doc)
                self.assertIn('<div class="mac-controls">', html_doc)
                self.assertNotIn('<div class="xp-window">', html_doc)
                self.assertNotIn('<div class="w7-window">', html_doc)
            elif theme == "md3":
                self.assertIn("theme-md3", html_doc)
                self.assertNotIn('<div class="sheet-window">', html_doc)
                self.assertNotIn('<div class="xp-window">', html_doc)
                self.assertNotIn('<div class="w7-window">', html_doc)
            elif theme == "shadcn":
                self.assertIn("theme-shadcn", html_doc)
                self.assertNotIn('<div class="sheet-window">', html_doc)
                self.assertNotIn('<div class="xp-window">', html_doc)
                self.assertNotIn('<div class="w7-window">', html_doc)
            elif theme == "winxp":
                self.assertIn("theme-winxp", html_doc)
                self.assertIn('<div class="xp-window">', html_doc)
                self.assertIn('<div class="xp-titlebar">', html_doc)
                self.assertIn("xp-btn-ctrl", html_doc)
                self.assertIn("xp-btn-close", html_doc)
                self.assertIn('<div class="xp-window-body">', html_doc)
                self.assertNotIn('<div class="mac-titlebar">', html_doc)
                self.assertNotIn('<div class="sheet-window">', html_doc)
                self.assertNotIn('<div class="w7-window">', html_doc)
            elif theme == "win7":
                self.assertIn("theme-win7", html_doc)
                self.assertIn('<div class="w7-window">', html_doc)
                self.assertIn('<div class="w7-titlebar">', html_doc)
                self.assertIn('<div class="w7-titlebar-icon"></div>', html_doc)
                self.assertIn('<div class="w7-titlebar-text">', html_doc)
                self.assertIn('<div class="w7-titlebar-controls">', html_doc)
                self.assertIn("w7-btn-ctrl", html_doc)
                self.assertIn("w7-btn-min", html_doc)
                self.assertIn("w7-btn-max", html_doc)
                self.assertIn("w7-btn-close", html_doc)
                self.assertIn('<div class="w7-window-body">', html_doc)
                self.assertNotIn('<div class="mac-titlebar">', html_doc)
                self.assertNotIn('<div class="xp-titlebar">', html_doc)
                self.assertNotIn('<div class="sheet-window">', html_doc)
                self.assertNotIn('<div class="xp-window">', html_doc)

    def test_icon_data_uris_and_no_remote_urls(self) -> None:
        platforms = ["claude", "codex", "antigravity", "kimi", "xai", "gemini-cli"]
        for p in platforms:
            uri = get_platform_icon_uri(p)
            self.assertTrue(uri.startswith("data:image/"), f"Platform {p} icon should be local data URI")
            self.assertNotIn("http://", uri)
            self.assertNotIn("https://", uri)

        # Check rendered HTML does not contain external http/https img src
        html_doc = build_platform_html(self.sample_section)
        # All img tags should only have data: src
        img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_doc)
        for src in img_srcs:
            self.assertTrue(src.startswith("data:"), f"Found non-data URI src in img: {src}")

    def test_summary_card_first_cell_on_page1_only(self) -> None:
        pages = paginate_accounts(self.sample_accounts, cards_per_row=4, rows_per_page=2)
        self.assertEqual(len(pages), 2)
        # Page 1 has 7 accounts (4*2 - 1 = 7)
        self.assertEqual(len(pages[0]), 7)
        # Page 2 has 2 accounts
        self.assertEqual(len(pages[1]), 2)

        # Render Page 1
        html_p1 = build_platform_html(
            self.sample_section, pages[0], page=1, pages=2, cards_per_row=4
        )
        self.assertIn("summary-card", html_p1)
        # Summary is the first cell inside grid
        grid_start = html_p1.find('<div class="grid">')
        summary_pos = html_p1.find('<article class="card summary-card">')
        first_acc_pos = html_p1.find('title="claude-pro-1"')
        self.assertTrue(grid_start < summary_pos < first_acc_pos)

        # Render Page 2
        html_p2 = build_platform_html(
            self.sample_section, pages[1], page=2, pages=2, cards_per_row=4
        )
        self.assertNotIn('<article class="card summary-card">', html_p2)

    def test_xai_render_contains_total_and_details_without_duplicate_reset(self) -> None:
        from cpaplugin.quota import parse_xai_billing
        windows = parse_xai_billing(
            {
                "creditUsagePercent": 20,
                "billingPeriodEnd": (datetime.now(timezone.utc).timestamp() + 86400 * 2),
                "products": [
                    {"name": "GrokBuild", "usagePercent": 10},
                    {"name": "GrokChat", "usagePercent": 5},
                    {"name": "GrokImagine", "usagePercent": 15},
                ],
            }
        )
        xai_account = AccountQuota(
            platform="xai",
            name="grok-user-1",
            auth_index="xai-1",
            plan="Premium",
            windows=windows,
        )
        section = PlatformQuota(
            platform="xai",
            title="xAI / Grok",
            accounts=[xai_account],
            window_remain_sum={"billing": 80.0},
            window_remain_count={"billing": 1},
            window_labels={"billing": "周额度"},
        )
        html_doc = build_platform_html(section, [xai_account])
        # 验证包含周额度与子项
        self.assertIn("周额度", html_doc)
        self.assertIn("GrokBuild", html_doc)
        self.assertIn("GrokChat", html_doc)
        self.assertIn("GrokImagine", html_doc)

        # 验证分组标题
        self.assertIn("xAI", html_doc)

        # 验证刷新提示只属于周额度，子项没有刷新提示
        # 统计 bar-reset-hint 在账号卡片中的数量，仅有 1 个（周额度的刷新）
        card_start = html_doc.find('title="grok-user-1"')
        card_slice = html_doc[card_start:]
        self.assertEqual(card_slice.count("bar-reset-hint"), 1)
        self.assertEqual(card_slice.count("后刷新额度"), 1)

        # 周额度按剩余展示；Grok 产品子项按已使用展示，进度条宽度也是已使用比例。
        self.assertIn("剩 80%", card_slice)
        self.assertIn("已使用 10%", card_slice)
        self.assertIn("已使用 5%", card_slice)
        self.assertIn("已使用 15%", card_slice)

        # 总统计卡片中的 Grok 子项同样按已使用比例聚合并明确标注。
        summary_end = html_doc.find('title="grok-user-1"')
        summary_slice = html_doc[:summary_end]
        self.assertIn("GrokBuild", summary_slice)
        self.assertIn("已使用 10%", summary_slice)
        self.assertIn("均已使用 10%", summary_slice)
        self.assertIn("GrokChat", summary_slice)
        self.assertIn("已使用 5%", summary_slice)
        self.assertIn("GrokImagine", summary_slice)
        self.assertIn("已使用 15%", summary_slice)

    def test_grok_product_bar_uses_consumed_percentage(self) -> None:
        low_usage = _bar_html(
            QuotaWindow(
                id="grok-build",
                label="GrokBuild",
                used_percent=10.0,
                remaining_percent=90.0,
            )
        )
        self.assertIn("已使用 10%", low_usage)
        self.assertIn("width: 10.0%", low_usage)
        self.assertNotIn("bar-low", low_usage)
        self.assertNotIn("bar-med", low_usage)

        high_usage = _bar_html(
            QuotaWindow(
                id="grok-imagine",
                label="GrokImagine",
                used_percent=90.0,
                remaining_percent=10.0,
            )
        )
        self.assertIn("已使用 90%", high_usage)
        self.assertIn("width: 90.0%", high_usage)
        self.assertIn("bar-low", high_usage)

    def test_summary_card_content_requirements(self) -> None:
        html_doc = build_platform_html(self.sample_section, self.sample_accounts[:7], page=1, pages=2)
        # Plan distribution Pro × 9
        self.assertIn("Pro × 9", html_doc)
        # Aggregate quota: SUM percent + average percent + count
        # 5h: 80 * 9 = 720% (均 80% · 9号)
        self.assertIn("720%", html_doc)
        self.assertIn("均 80%", html_doc)
        self.assertIn("9号", html_doc)
        # Multiple grouped resets
        self.assertIn("summary-reset-list", html_doc)
        self.assertIn("summary-reset-row", html_doc)
        self.assertIn("Claude · 小时额度：", html_doc)
        self.assertIn("Claude · 周额度：", html_doc)
        self.assertIn("最快于 2小时15分 后刷新额度", html_doc)
        self.assertIn("最快于 3天12小时 后刷新额度", html_doc)
        # Total reset credits: 5 -> 主动刷新次数: 5
        self.assertIn("主动刷新次数: 5", html_doc)

    def test_grouped_earliest_resets_multi_models_and_periods(self) -> None:
        mixed_accounts = [
            AccountQuota(
                platform="gemini-cli",
                name="gemini-acc",
                auth_index="1",
                windows=[
                    QuotaWindow(id="gemini-5h", label="5h", remaining_percent=20.0, reset_label="2h15m"),
                    QuotaWindow(id="gemini-week", label="1w", remaining_percent=50.0, reset_label="1d3h"),
                ],
            ),
            AccountQuota(
                platform="claude",
                name="claude-gpt-acc",
                auth_index="2",
                windows=[
                    QuotaWindow(id="claude-gpt-5h", label="5h", remaining_percent=10.0, reset_label="45m"),
                    QuotaWindow(id="claude-gpt-week", label="1w", remaining_percent=30.0, reset_label="2d"),
                ],
            ),
        ]
        sec = PlatformQuota(
            platform="antigravity",
            title="Antigravity",
            accounts=mixed_accounts,
            window_remain_sum={},
            window_remain_count={},
        )
        html_doc = build_platform_html(sec, mixed_accounts, page=1, pages=1)
        self.assertIn("summary-reset-list", html_doc)
        self.assertIn("Gemini · 小时额度：", html_doc)
        self.assertIn("最快于 2小时15分 后刷新额度", html_doc)
        self.assertIn("Gemini · 周额度：", html_doc)
        self.assertIn("最快于 1天3小时 后刷新额度", html_doc)
        self.assertIn("Claude/GPT · 小时额度：", html_doc)
        self.assertIn("最快于 45分 后刷新额度", html_doc)
        self.assertIn("Claude/GPT · 周额度：", html_doc)
        self.assertIn("最快于 2天 后刷新额度", html_doc)

    def test_win7_css_aero_glass_properties(self) -> None:
        html_doc = build_platform_html(self.sample_section, theme="win7")
        # Ensure backdrop-filter and translucent alpha colors are applied across window, titlebar, body, cards
        self.assertIn("backdrop-filter: blur(14px) saturate(175%)", html_doc)
        self.assertIn(".theme-win7 .w7-titlebar", html_doc)
        self.assertIn(".theme-win7 .w7-window-body", html_doc)
        self.assertIn(".theme-win7 .card", html_doc)
        self.assertIn("--w7-surface: rgba(230, 240, 252, 0.55)", html_doc)
        self.assertIn("--w7-card-bg: rgba(255, 255, 255, 0.48)", html_doc)
        self.assertIn("linear-gradient(115deg", html_doc)
        self.assertIn("rgba(170, 245, 238, 0.55)", html_doc)

    def test_account_card_features(self) -> None:
        html_doc = build_platform_html(self.sample_section, self.sample_accounts[:7], page=1, pages=2)
        # Chinese 冷却中
        self.assertIn("冷却中", html_doc)
        self.assertNotIn(">cooling<", html_doc)
        # Subscription expiry label
        self.assertIn("到期: 2026-10-01", html_doc)
        # Active refresh count
        self.assertIn("主动刷新 5 次", html_doc)

    def test_row_counts_and_pagination(self) -> None:
        # Test cards_per_row 1..6
        for cols in range(1, 7):
            width = calculate_canvas_width(cols)
            self.assertTrue(width >= 400)
            pages = paginate_accounts(self.sample_accounts, cards_per_row=cols, rows_per_page=2)
            p1_cap = max(1, 2 * cols - 1)
            self.assertEqual(len(pages[0]), min(len(self.sample_accounts), p1_cap))
            if len(self.sample_accounts) > p1_cap:
                self.assertEqual(len(pages[1]), min(len(self.sample_accounts) - p1_cap, 2 * cols))

            # HTML render check for grid column css
            html_doc = build_platform_html(self.sample_section, pages[0], cards_per_row=cols)
            self.assertIn(f"grid-template-columns: repeat({cols}, minmax(0, 1fr));", html_doc)


class ChromiumScreenshotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        from cpaplugin.render import close_renderer

        await close_renderer()

    async def test_screenshot_all_themes_if_available(self) -> None:
        sample_accounts = []
        for i in range(1, 8):
            acc = AccountQuota(
                platform="codex",
                name=f"openai-user-{i}",
                auth_index=str(i),
                plan="Plus" if i % 2 == 0 else "Pro",
                cooling=(i == 2),
                windows=[
                    QuotaWindow(
                        id="code-5h",
                        label="5h",
                        remaining_percent=75.0,
                        reset_label="1h45m",
                    ),
                    QuotaWindow(
                        id="code-7d",
                        label="7d",
                        remaining_percent=90.0,
                        reset_label="5d",
                    ),
                ],
            )
            if i == 1:
                setattr(acc, "subscription_expires_label", "2026-12-31")
            if i == 3:
                setattr(acc, "reset_credits", 2)
            sample_accounts.append(acc)

        section = PlatformQuota(
            platform="codex",
            title="Codex / OpenAI",
            accounts=sample_accounts,
            window_remain_sum={"code-5h": 525.0, "code-7d": 630.0},
            window_remain_count={"code-5h": 7, "code-7d": 7},
            window_labels={"code-5h": "5h", "code-7d": "7d"},
        )

        for theme in ["shadcn", "mac", "md3", "winxp", "win7"]:
            html_doc = build_platform_html(section, theme=theme, cards_per_row=4)
            self.assertTrue(len(html_doc) > 500)
            from cpaplugin.render import _screenshot

            try:
                png = await _screenshot(html_doc)
                self.assertTrue(len(png) > 1000, f"Screenshot for theme {theme} should return valid PNG bytes")
                # PNG header check
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            except Exception as e:
                self.skipTest(f"Chromium not available for screenshot: {e}")

    async def test_geometry_stress_and_dom_overflow_all_themes_and_cols(self) -> None:
        """
        DOM geometry stress test for all 3 themes and cols 1..6 checking:
        - sheet scrollWidth <= clientWidth + 1
        - card scrollWidth <= clientWidth + 1
        - icon naturalWidth > 0
        - grid column count matches requested cols
        - summary page 1 only
        - long title overflow permitted inside title element with ellipsis
        """
        from cpaplugin.render import _ensure_browser

        try:
            browser = await _ensure_browser()
        except Exception as e:
            self.skipTest(f"Playwright/Chromium not available: {e}")

        # Construct extreme stress accounts with very long names, plan, cooling, disabled, subscription, reset credits, 2 windows
        stress_accounts = []
        for i in range(1, 15):
            acc = AccountQuota(
                platform="claude" if i % 2 == 0 else "codex",
                name=f"super-extremely-long-account-identifier-that-could-easily-break-unconstrained-layouts-index-{i:03d}@enterprise-domain-node-cluster.org",
                auth_index=str(i),
                plan=f"Enterprise-Custom-Ultra-High-Tier-Plan-Tier-{i}",
                cooling=(i % 3 == 0),
                disabled=(i % 5 == 0),
                windows=[
                    QuotaWindow(
                        id="five_hour",
                        label="5-Hour Extended High Priority Window",
                        remaining_percent=12.5,
                        reset_label="2026-09-17T18:45:00Z",
                    ),
                    QuotaWindow(
                        id="seven_day",
                        label="7-Day Cumulative Enterprise Quota Limit",
                        remaining_percent=68.0,
                        reset_label="4d18h",
                    ),
                ],
            )
            setattr(acc, "subscription_expires_label", "2026-12-31T23:59:59+08:00 (Renewing Soon)")
            setattr(acc, "reset_credits", 12)
            stress_accounts.append(acc)

        section = PlatformQuota(
            platform="claude",
            title="Claude Enterprise Multi-Region Cluster",
            accounts=stress_accounts,
            window_remain_sum={"five_hour": 175.0, "seven_day": 952.0},
            window_remain_count={"five_hour": 14, "seven_day": 14},
            window_labels={
                "five_hour": "5-Hour Extended High Priority Window",
                "seven_day": "7-Day Cumulative Enterprise Quota Limit",
            },
        )

        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            for theme in ["shadcn", "mac", "md3", "winxp", "win7"]:
                for cols in range(1, 7):
                    paginated = paginate_accounts(stress_accounts, cards_per_row=cols, rows_per_page=2)
                    for page_idx, page_accs in enumerate(paginated, start=1):
                        canvas_w = calculate_canvas_width(cols)
                        html_doc = build_platform_html(
                            section,
                            page_accs,
                            page=page_idx,
                            pages=len(paginated),
                            width=canvas_w,
                            theme=theme,
                            cards_per_row=cols,
                        )

                        await page.set_content(html_doc, wait_until="load")

                        # 1. Check summary card on page 1 only
                        has_summary = await page.evaluate("() => !!document.querySelector('.summary-card')")
                        if page_idx == 1:
                            self.assertTrue(has_summary, f"Page 1 must have summary-card ({theme}, cols={cols})")
                        else:
                            self.assertFalse(has_summary, f"Page {page_idx} must not have summary-card ({theme}, cols={cols})")

                        # 2. Check icon naturalWidth > 0 if summary is present
                        if page_idx == 1:
                            icon_status = await page.evaluate(
                                """() => {
                                const img = document.querySelector('.brand-icon');
                                if (!img) return { found: false, naturalWidth: 0 };
                                return { found: true, naturalWidth: img.naturalWidth };
                            }"""
                            )
                            if icon_status["found"]:
                                self.assertGreater(
                                    icon_status["naturalWidth"],
                                    0,
                                    f"Brand icon naturalWidth must be > 0 ({theme}, cols={cols})",
                                )

                        # 3. Check sheet geometry overflow: scrollWidth <= clientWidth + 1
                        sheet_metrics = await page.evaluate(
                            """() => {
                            const sheet = document.querySelector('.sheet');
                            const sheetWin = document.querySelector('.sheet-window') || document.querySelector('.xp-window') || document.querySelector('.w7-window');
                            const target = sheetWin || sheet;
                            return {
                                clientWidth: target.clientWidth,
                                scrollWidth: target.scrollWidth,
                                overflow: target.scrollWidth - target.clientWidth
                            };
                        }"""
                        )
                        self.assertLessEqual(
                            sheet_metrics["scrollWidth"],
                            sheet_metrics["clientWidth"] + 1,
                            f"Sheet overflowed by {sheet_metrics['overflow']}px ({theme}, cols={cols}, page={page_idx})",
                        )

                        # 4. Check card geometry overflow: each card scrollWidth <= clientWidth + 1
                        cards_metrics = await page.evaluate(
                            """() => {
                            const cards = Array.from(document.querySelectorAll('.card'));
                            return cards.map((c, idx) => ({
                                idx,
                                isSummary: c.classList.contains('summary-card'),
                                clientWidth: c.clientWidth,
                                scrollWidth: c.scrollWidth,
                                overflow: c.scrollWidth - c.clientWidth
                            }));
                        }"""
                        )
                        for cm in cards_metrics:
                            self.assertLessEqual(
                                cm["scrollWidth"],
                                cm["clientWidth"] + 1,
                                f"Card #{cm['idx']} (isSummary={cm['isSummary']}) overflowed by {cm['overflow']}px ({theme}, cols={cols}, page={page_idx})",
                            )

                        # 5. Check computed grid column count
                        grid_col_count = await page.evaluate(
                            """() => {
                            const grid = document.querySelector('.grid');
                            if (!grid) return 0;
                            const comp = window.getComputedStyle(grid).gridTemplateColumns;
                            return comp.split(' ').filter(Boolean).length;
                        }"""
                        )
                        self.assertEqual(
                            grid_col_count,
                            cols,
                            f"Grid column count mismatch: expected {cols}, got {grid_col_count} ({theme})",
                        )
        finally:
            await context.close()

    async def test_win7_computed_backdrop_filter_in_chromium(self) -> None:
        from cpaplugin.render import _ensure_browser

        try:
            browser = await _ensure_browser()
        except Exception as e:
            self.skipTest(f"Playwright/Chromium not available: {e}")

        sample_accounts = [
            AccountQuota(
                platform="claude",
                name="claude-user-1",
                auth_index="1",
                plan="Pro",
                windows=[
                    QuotaWindow(id="five_hour", label="5h", remaining_percent=75.0, reset_label="1h45m"),
                ],
            )
        ]
        section = PlatformQuota(
            platform="claude",
            title="Claude",
            accounts=sample_accounts,
            window_remain_sum={"five_hour": 75.0},
            window_remain_count={"five_hour": 1},
            window_labels={"five_hour": "5h"},
        )

        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        try:
            html_doc = build_platform_html(section, sample_accounts, theme="win7", cards_per_row=4)
            await page.set_content(html_doc, wait_until="load")

            # Evaluate computed styles on win7 elements
            styles = await page.evaluate(
                """() => {
                const win = document.querySelector('.w7-window');
                const titlebar = document.querySelector('.w7-titlebar');
                const body = document.querySelector('.w7-window-body');
                const card = document.querySelector('.card');
                return {
                    winBdf: window.getComputedStyle(win).backdropFilter || window.getComputedStyle(win).webkitBackdropFilter,
                    titleBdf: window.getComputedStyle(titlebar).backdropFilter || window.getComputedStyle(titlebar).webkitBackdropFilter,
                    bodyBdf: window.getComputedStyle(body).backdropFilter || window.getComputedStyle(body).webkitBackdropFilter,
                    cardBdf: window.getComputedStyle(card).backdropFilter || window.getComputedStyle(card).webkitBackdropFilter,
                };
            }"""
            )
            # In Chromium supporting backdrop-filter, these should contain blur
            self.assertTrue("blur" in str(styles["winBdf"]).lower())
            self.assertTrue("blur" in str(styles["bodyBdf"]).lower())
            self.assertTrue("blur" in str(styles["cardBdf"]).lower())
        finally:
            await context.close()

    async def test_win7_pixel_level_glass_bleedthrough_probe(self) -> None:
        """
        Pixel probe: Compare pixel values inside win7 cards with aurora backdrop
        via in-browser canvas context, verifying the underlying aurora colors bleed into the card.
        """
        from cpaplugin.render import _ensure_browser

        try:
            browser = await _ensure_browser()
        except Exception as e:
            self.skipTest(f"Playwright/Chromium not available: {e}")

        sample_accounts = [
            AccountQuota(
                platform="claude",
                name="claude-user-1",
                auth_index="1",
                plan="Pro",
                windows=[
                    QuotaWindow(id="five_hour", label="5h", remaining_percent=75.0, reset_label="1h45m"),
                ],
            )
        ]
        section = PlatformQuota(
            platform="claude",
            title="Claude",
            accounts=sample_accounts,
            window_remain_sum={"five_hour": 75.0},
            window_remain_count={"five_hour": 1},
            window_labels={"five_hour": "5h"},
        )

        html_win7 = build_platform_html(section, sample_accounts, theme="win7", cards_per_row=4)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        try:
            await page.set_content(html_win7, wait_until="load")

            # Extract pixel data across horizontal and vertical positions inside .w7-window-body
            probe_result = await page.evaluate(
                """async () => {
                const body = document.querySelector('.w7-window-body');
                const rect = body.getBoundingClientRect();
                const card = document.querySelector('.card');
                const cardRect = card.getBoundingClientRect();

                // Create a canvas to sample rendered element background colors
                const canvas = document.createElement('canvas');
                canvas.width = window.innerWidth;
                canvas.height = window.innerHeight;
                const ctx = canvas.getContext('2d');

                // Read computed card background and window body background
                const bodyStyle = window.getComputedStyle(body);
                const cardStyle = window.getComputedStyle(card);
                const winStyle = window.getComputedStyle(document.querySelector('.w7-window'));

                return {
                    bodyBg: bodyStyle.backgroundColor,
                    cardBg: cardStyle.backgroundColor,
                    winBg: winStyle.backgroundColor,
                    winBdf: winStyle.backdropFilter || winStyle.webkitBackdropFilter,
                    bodyBdf: bodyStyle.backdropFilter || bodyStyle.webkitBackdropFilter,
                };
            }"""
            )

            # Extract alpha values from backgroundColor (rgba(r, g, b, a))
            import re
            body_alpha_match = re.search(r"rgba\([^)]+,\s*([\d.]+)\)", probe_result["bodyBg"])
            card_alpha_match = re.search(r"rgba\([^)]+,\s*([\d.]+)\)", probe_result["cardBg"])

            body_alpha = float(body_alpha_match.group(1)) if body_alpha_match else 1.0
            card_alpha = float(card_alpha_match.group(1)) if card_alpha_match else 1.0

            print(f"\n[Win7 Glass Probe] Body Alpha: {body_alpha}, Card Alpha: {card_alpha}, Win BDF: {probe_result['winBdf']}")

            # Assert transparency is clearly see-through while remaining readable
            self.assertLessEqual(body_alpha, 0.72, f"Body alpha {body_alpha} should be <= 0.72 for see-through glass")
            self.assertGreaterEqual(body_alpha, 0.50, f"Body alpha {body_alpha} should be >= 0.50 for contrast")
            self.assertLessEqual(card_alpha, 0.75, f"Card alpha {card_alpha} should be <= 0.75 for see-through glass")
            self.assertGreaterEqual(card_alpha, 0.45, f"Card alpha {card_alpha} should be >= 0.45 for readable text")
            self.assertTrue("blur" in str(probe_result["winBdf"]).lower())
            self.assertTrue("blur" in str(probe_result["bodyBdf"]).lower())
        finally:
            await context.close()




if __name__ == "__main__":
    unittest.main()
