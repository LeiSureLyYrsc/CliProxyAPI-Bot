from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "cpaplugin"
_pkg = types.ModuleType("cpaplugin")
_pkg.__path__ = [str(_plugin_dir)]
sys.modules.setdefault("cpaplugin", _pkg)

from cpaplugin.client import allowed_api_call_url
from cpaplugin.format import (
    extract_oauth_callback_url,
    format_auth_list,
    looks_like_oauth_callback,
    visible_auth_files,
)
from cpaplugin.quota import (
    AccountQuota,
    QuotaWindow,
    _build_board,
    _plan_from_auth_file,
    _window_text,
    _wanted_files,
    format_quota_board,
    format_reset_zh,
    is_platform_query,
    normalize_platform,
    parse_antigravity_plan,
    parse_antigravity_summary,
    parse_claude_usage,
    parse_codex_usage,
    parse_xai_billing,
    platform_of,
    platform_total_chips,
    sort_windows,
)
from cpaplugin.render import build_platform_html


class PlatformAliasTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(normalize_platform("anthropic"), "claude")
        self.assertEqual(normalize_platform("x-ai"), "xai")
        self.assertEqual(normalize_platform("grok"), "xai")
        self.assertTrue(is_platform_query("antigravity"))
        self.assertFalse(is_platform_query("user@example.com"))

    def test_platform_of_normalizes_underscore(self) -> None:
        self.assertEqual(platform_of({"provider": "x_ai"}), "xai")
        self.assertEqual(platform_of({"type": "gemini"}), "gemini-cli")
        self.assertEqual(platform_of({"provider": "openai"}), "codex")


class ParserTests(unittest.TestCase):
    def test_antigravity_splits_gemini_and_claude_windows(self) -> None:
        windows = parse_antigravity_summary(
            {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {"displayName": "Five Hour Limit", "remainingFraction": 0.86},
                            {"displayName": "Weekly Limit", "remainingFraction": 0.41},
                        ],
                    },
                    {
                        "displayName": "Claude and GPT Models",
                        "buckets": [
                            {"displayName": "Five Hour Limit", "remainingFraction": 1.0},
                            {"displayName": "Weekly Limit", "remainingFraction": 0.0},
                        ],
                    },
                ]
            }
        )
        by_id = {item.id: item for item in windows}
        self.assertEqual(by_id["gemini-5h"].remaining_percent, 86.0)
        self.assertEqual(by_id["gemini-week"].remaining_percent, 41.0)
        self.assertEqual(by_id["claude-gpt-5h"].remaining_percent, 100.0)
        self.assertEqual(by_id["claude-gpt-week"].remaining_percent, 0.0)

    def test_claude_remaining_from_utilization(self) -> None:
        windows, plan = parse_claude_usage(
            {
                "plan_type": "pro",
                "five_hour": {"utilization": 20, "resets_at": "2026-08-22T12:00:00Z"},
                "seven_day": {"utilization": 55},
            }
        )
        self.assertEqual(plan, "Pro")
        self.assertEqual(windows[0].remaining_percent, 80.0)
        self.assertEqual(windows[1].remaining_percent, 45.0)

    def test_codex_weekly_window(self) -> None:
        windows, plan = parse_codex_usage(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {"used_percent": 10, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                },
            }
        )
        self.assertEqual(plan, "Plus")
        self.assertEqual(windows[0].id, "code-5h")
        self.assertEqual(windows[0].remaining_percent, 90.0)
        self.assertEqual(windows[1].id, "code-7d")
        self.assertEqual(windows[1].remaining_percent, 100.0)

    def test_antigravity_plan_from_paid_tier(self) -> None:
        self.assertEqual(
            parse_antigravity_plan({"paidTier": {"id": "g1-pro-tier", "name": "Google AI Pro"}}),
            "Pro",
        )
        self.assertEqual(
            parse_antigravity_plan({"currentTier": {"id": "g1-plus-tier", "name": "Google AI Plus"}}),
            "Plus",
        )
        self.assertEqual(parse_antigravity_plan({"paidTier": {"name": "Google AI Ultra"}}), "Ultra")
        self.assertEqual(_plan_from_auth_file({"account_type": "oauth", "account": "a@b.com"}), "")
        self.assertEqual(_plan_from_auth_file({"plan_type": "plus"}), "Plus")
        self.assertEqual(_plan_from_auth_file({"id_token": {"plan_type": "pro"}}), "Pro")
        self.assertEqual(_plan_from_auth_file({"id_token": {"planType": "team"}}), "Team")

    def test_windows_sort_rolling_week_month(self) -> None:
        windows = sort_windows(
            [
                QuotaWindow(id="gemini-week", label="Gemini 周"),
                QuotaWindow(id="gemini-month", label="Gemini 月"),
                QuotaWindow(id="gemini-5h", label="Gemini 5h"),
            ]
        )
        self.assertEqual([item.id for item in windows], ["gemini-5h", "gemini-week", "gemini-month"])

    def test_reset_zh(self) -> None:
        self.assertEqual(format_reset_zh("2h30m"), "在 2小时30分 后刷新额度")
        self.assertEqual(format_reset_zh("19m"), "在 19分 后刷新额度")
        self.assertEqual(format_reset_zh("1d2h"), "在 1天2小时 后刷新额度")
        self.assertEqual(format_reset_zh("-"), "")

    def test_xai_weekly_and_products(self) -> None:
        windows = parse_xai_billing(
            {
                "creditUsagePercent": 13,
                "billingPeriodEnd": "2026-08-24T05:33:00Z",
                "products": [
                    {"name": "GrokBuild", "usagePercent": 8},
                    {"name": "GrokChat", "usagePercent": 5},
                ],
            }
        )
        by_id = {item.id: item for item in windows}
        self.assertEqual(by_id["billing"].label, "周额度")
        self.assertEqual(by_id["billing"].remaining_percent, 87.0)
        self.assertIsNotNone(by_id["billing"].reset_at)
        self.assertEqual(by_id["grok-build"].used_percent, 8.0)
        self.assertIsNone(by_id["grok-build"].reset_at)
        self.assertEqual(by_id["grok-chat"].used_percent, 5.0)
        self.assertIsNone(by_id["grok-chat"].reset_at)

    def test_xai_real_cpa_product_usage_response(self) -> None:
        # 使用真实 CPA Management Center 响应
        real_response = {
            "config": {
                "creditUsagePercent": 45,
                "productUsage": [
                    {"product": "GrokImagine", "usagePercent": 30},
                    {"product": "GrokChat", "usagePercent": 50},
                    {"product": "GrokBuild", "usagePercent": 20},
                ],
                "currentPeriod": {"type": "weekly", "end": "2026-08-24T05:33:00Z"},
                "billingPeriodEnd": "2026-08-24T05:33:00Z",
            }
        }
        windows = parse_xai_billing(real_response)
        # 断言排序：billing -> GrokBuild -> GrokChat -> GrokImagine
        ids = [w.id for w in windows]
        self.assertEqual(ids, ["billing", "grok-build", "grok-chat", "grok-imagine"])
        by_id = {w.id: w for w in windows}
        self.assertEqual(by_id["billing"].label, "周额度")
        self.assertEqual(by_id["billing"].used_percent, 45.0)
        self.assertEqual(by_id["billing"].remaining_percent, 55.0)
        self.assertIsNotNone(by_id["billing"].reset_at)

        self.assertEqual(by_id["grok-build"].label, "GrokBuild")
        self.assertEqual(by_id["grok-build"].used_percent, 20.0)
        self.assertIsNone(by_id["grok-build"].reset_at)

        self.assertEqual(by_id["grok-chat"].label, "GrokChat")
        self.assertEqual(by_id["grok-chat"].used_percent, 50.0)
        self.assertIsNone(by_id["grok-chat"].reset_at)

        self.assertEqual(by_id["grok-imagine"].label, "GrokImagine")
        self.assertEqual(by_id["grok-imagine"].used_percent, 30.0)
        self.assertIsNone(by_id["grok-imagine"].reset_at)

    def test_xai_product_usage_snake_case_and_current_period_end_fallback(self) -> None:
        # 覆盖 product_usage 蛇形命名与 current_period.end 回退
        snake_response = {
            "credit_usage_percent": 60,
            "product_usage": [
                {"name": "GrokVoice", "used_percent": 15},
                {"name": "GrokImagine", "credit_usage_percent": 25},
            ],
            "current_period": {
                "end": "2026-09-01T12:00:00Z"
            }
        }
        windows = parse_xai_billing(snake_response)
        ids = [w.id for w in windows]
        self.assertEqual(ids, ["billing", "grok-imagine", "grok-voice"])
        by_id = {w.id: w for w in windows}
        self.assertEqual(by_id["billing"].used_percent, 60.0)
        self.assertIsNotNone(by_id["billing"].reset_at)
        self.assertEqual(by_id["grok-imagine"].used_percent, 25.0)
        self.assertIsNone(by_id["grok-imagine"].reset_at)
        self.assertEqual(by_id["grok-voice"].used_percent, 15.0)
        self.assertIsNone(by_id["grok-voice"].reset_at)

    def test_xai_dynamic_products_grok_imagine_and_voice(self) -> None:
        # 覆盖 GrokImagine, 未知 GrokVoice, config 嵌套, 重复列表去重, 列表优先于顶层固定字段
        windows = parse_xai_billing(
            {
                "config": {
                    "creditUsagePercent": 25,
                    "billingPeriodEnd": "2026-08-24T05:33:00Z",
                    "grokBuildUsagePercent": 99,  # 顶层固定字段应被列表中的覆盖
                    "products": [
                        {"name": "GrokBuild", "usagePercent": 10},
                        {"name": "GrokChat", "usagePercent": 12},
                        {"name": "GrokImagine", "usagePercent": 30},
                        {"name": "GrokVoice", "usedPercent": 5},
                    ],
                }
            }
        )
        ids = [w.id for w in windows]
        self.assertEqual(ids, ["billing", "grok-build", "grok-chat", "grok-imagine", "grok-voice"])
        by_id = {w.id: w for w in windows}
        self.assertEqual(by_id["billing"].label, "周额度")
        self.assertIsNotNone(by_id["billing"].reset_at)
        self.assertEqual(by_id["grok-build"].used_percent, 10.0)  # 列表优先，不是 99.0
        self.assertIsNone(by_id["grok-build"].reset_at)
        self.assertEqual(by_id["grok-imagine"].used_percent, 30.0)
        self.assertIsNone(by_id["grok-imagine"].reset_at)
        self.assertEqual(by_id["grok-voice"].used_percent, 5.0)
        self.assertIsNone(by_id["grok-voice"].reset_at)

    def test_xai_fallback_top_level_fields_when_no_list(self) -> None:
        windows = parse_xai_billing(
            {
                "creditUsagePercent": 40,
                "grokBuildUsagePercent": 15,
                "grokChatUsagePercent": 20,
            }
        )
        ids = [w.id for w in windows]
        self.assertEqual(ids, ["billing", "grok-build", "grok-chat"])
        by_id = {w.id: w for w in windows}
        self.assertEqual(by_id["grok-build"].used_percent, 15.0)
        self.assertEqual(by_id["grok-chat"].used_percent, 20.0)

    def test_xai_text_uses_consumed_percentage_for_products(self) -> None:
        weekly = QuotaWindow(
            id="billing",
            label="周额度",
            used_percent=20.0,
            remaining_percent=80.0,
        )
        product = QuotaWindow(
            id="grok-build",
            label="GrokBuild",
            used_percent=10.0,
            remaining_percent=90.0,
        )
        self.assertEqual(_window_text(weekly, compact=True), "周额度 剩 80%")
        self.assertEqual(_window_text(product, compact=True), "GrokBuild 已使用 10%")


class BoardFormatTests(unittest.TestCase):
    def test_platform_total_is_equivalent_not_percent_sum(self) -> None:
        accounts = []
        for remain in (86.0, 91.0, 86.0, 81.0):
            accounts.append(
                AccountQuota(
                    platform="antigravity",
                    name="acct",
                    auth_index="x",
                    plan="Pro",
                    windows=parse_antigravity_summary(
                        {
                            "groups": [
                                {
                                    "displayName": "Gemini Models",
                                    "buckets": [
                                        {"displayName": "Five Hour Limit", "remainingFraction": remain / 100.0},
                                        {"displayName": "Weekly Limit", "remainingFraction": 0.41},
                                    ],
                                }
                            ]
                        }
                    ),
                )
            )
        board = _build_board(accounts)
        text = "\n".join(format_quota_board(board))
        self.assertIn("【Antigravity】4 账号", text)
        self.assertIn("Gemini 5h 3.44/4 (86%)", text)
        self.assertNotIn("Gemini 5h 344", text)

    def test_skips_disabled(self) -> None:
        files = [
            {"provider": "antigravity", "disabled": True, "auth_index": "a"},
            {"provider": "antigravity", "disabled": False, "auth_index": "b"},
            {"provider": "codex", "disabled": False, "auth_index": "c"},
        ]
        wanted = _wanted_files(files, platform=None, skip_disabled=True)
        self.assertEqual(len(wanted), 2)
        self.assertEqual([item["auth_index"] for item in wanted], ["b", "c"])


    def test_html_merges_platform_cards_and_chips(self) -> None:
        accounts = []
        for index, remain in enumerate((86.0, 91.0, 86.0, 81.0), start=1):
            accounts.append(
                AccountQuota(
                    platform="antigravity",
                    name=f"acct-{index}",
                    auth_index=str(index),
                    plan="Pro",
                    windows=parse_antigravity_summary(
                        {
                            "groups": [
                                {
                                    "displayName": "Gemini Models",
                                    "buckets": [
                                        {"displayName": "Five Hour Limit", "remainingFraction": remain / 100.0},
                                        {"displayName": "Weekly Limit", "remainingFraction": 0.41},
                                    ],
                                },
                                {
                                    "displayName": "Claude and GPT Models",
                                    "buckets": [
                                        {"displayName": "Five Hour Limit", "remainingFraction": 1.0},
                                        {"displayName": "Weekly Limit", "remainingFraction": 0.0},
                                    ],
                                },
                            ]
                        }
                    ),
                )
            )
        board = _build_board(accounts)
        section = board.platforms[0]
        html_doc = build_platform_html(section)
        self.assertIn("platform-antigravity", html_doc)
        self.assertIn("Antigravity", html_doc)
        self.assertIn("344%", html_doc)
        self.assertIn("均 86%", html_doc)
        self.assertIn("4号", html_doc)
        self.assertIn("剩 86%", html_doc)
        self.assertNotIn("% remaining", html_doc)
        self.assertNotIn("Refreshes in", html_doc)
        self.assertIn("Gemini Models", html_doc)
        self.assertIn("Claude and GPT Models", html_doc)
        self.assertEqual(html_doc.count('<article class="card">'), 4)
        self.assertIn('class="card-header"', html_doc)
        self.assertIn('class="card-content"', html_doc)
        self.assertIn('class="progress-track"', html_doc)
        self.assertIn("progress-fill", html_doc)
        self.assertIn("width: 86.0%", html_doc)
        chips = platform_total_chips(section)
        self.assertTrue(any(item.startswith("Gemini 5h 3.44/4") for item in chips))

    def test_html_paginates_by_eight_cards(self) -> None:
        from cpaplugin.render import CARDS_PER_IMAGE, _chunks

        accounts = [
            AccountQuota(platform="codex", name=f"a{i}", auth_index=str(i))
            for i in range(9)
        ]
        pages = _chunks(accounts, CARDS_PER_IMAGE)
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[0]), 8)
        self.assertEqual(len(pages[1]), 1)


class AccountAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        from cpaplugin.aliases import use_memory_aliases

        use_memory_aliases({})

    def test_public_name_hides_email(self) -> None:
        from cpaplugin.format import display_name

        file = {
            "provider": "antigravity",
            "email": "hit@gmail.com",
            "name": "antigravity-hit@gmail.com.json",
            "auth_index": "abcd1234ffff",
        }
        self.assertEqual(display_name(file, public=True), "antigravity-abcd")
        self.assertNotIn("@", display_name(file, public=True))
        self.assertIn("hit@gmail.com", display_name(file, public=False))

    def test_alias_overrides_and_matches(self) -> None:
        from cpaplugin.aliases import format_alias_list, set_alias
        from cpaplugin.format import display_name, match_auth

        file = {
            "provider": "antigravity",
            "email": "hit@gmail.com",
            "name": "a.json",
            "auth_index": "abcd1234ffff",
        }
        set_alias(file, "家里号")
        self.assertEqual(display_name(file, public=True), "家里号")
        self.assertEqual(len(match_auth([file], "家里号")), 1)
        listing = format_alias_list()
        self.assertIn("家里号", listing)
        self.assertNotIn("hit@gmail.com", listing)

    def test_reject_email_alias(self) -> None:
        from cpaplugin.aliases import set_alias

        with self.assertRaises(ValueError):
            set_alias({"email": "a@b.com", "auth_index": "x"}, "a@b.com")

    def test_alias_list_hides_disabled(self) -> None:
        from cpaplugin.aliases import format_alias_list, set_alias, use_memory_aliases

        use_memory_aliases({})
        enabled = {
            "provider": "antigravity",
            "email": "on@example.com",
            "name": "on.json",
            "auth_index": "aaaa1111ffff",
            "disabled": False,
        }
        disabled = {
            "provider": "codex",
            "email": "off@example.com",
            "name": "off.json",
            "auth_index": "bbbb2222ffff",
            "disabled": True,
        }
        set_alias(enabled, "家里号")
        set_alias(disabled, "停用号")
        listing = format_alias_list([enabled, disabled], include_disabled=False)
        self.assertIn("家里号", listing)
        self.assertNotIn("停用号", listing)
        listing_all = format_alias_list([enabled, disabled], include_disabled=True)
        self.assertIn("停用号", listing_all)

    def test_same_email_alias_is_per_account(self) -> None:
        from cpaplugin.aliases import resolve_alias, set_alias

        antigravity = {
            "provider": "antigravity",
            "email": "same@example.com",
            "name": "antigravity-same@example.com.json",
            "auth_index": "agag1111ffff",
        }
        codex = {
            "provider": "codex",
            "email": "same@example.com",
            "name": "codex-same@example.com.json",
            "auth_index": "cxcx2222ffff",
        }
        set_alias(antigravity, "AG-1")
        self.assertEqual(resolve_alias(antigravity), "AG-1")
        self.assertEqual(resolve_alias(codex), "")


class AuthListFilterTests(unittest.TestCase):
    def test_hides_disabled_by_default(self) -> None:
        files = [
            {"provider": "antigravity", "disabled": False, "email": "on@example.com", "auth_index": "aaaa1111ffff", "status": "ready"},
            {"provider": "codex", "disabled": True, "email": "off@example.com", "auth_index": "bbbb2222ffff", "status": "ready"},
        ]
        visible = visible_auth_files(files, include_disabled=False)
        self.assertEqual(len(visible), 1)
        text = format_auth_list(files, include_disabled=False)
        self.assertNotIn("codex-bbbb", text)
        self.assertIn("antigravity-aaaa", text)
        all_text = format_auth_list(files, include_disabled=True)
        self.assertIn("disabled", all_text)


class OAuthCallbackParseTests(unittest.TestCase):
    def test_extracts_localhost_callback(self) -> None:
        url = "http://localhost:8317/v0/management/oauth-callback?provider=codex&state=codex-1&code=abc"
        self.assertTrue(looks_like_oauth_callback(url))
        self.assertEqual(extract_oauth_callback_url(f"完成了 {url}"), url)
        self.assertFalse(looks_like_oauth_callback("https://example.com/docs"))


class AllowlistTests(unittest.TestCase):
    def test_quota_urls_allowed(self) -> None:
        self.assertTrue(
            allowed_api_call_url(
                "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
            )
        )
        self.assertTrue(
            allowed_api_call_url("https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist")
        )
        self.assertTrue(allowed_api_call_url("https://cli-chat-proxy.grok.com/v1/billing?format=credits"))
        self.assertFalse(allowed_api_call_url("https://example.com/steal"))
        self.assertFalse(allowed_api_call_url("http://api.anthropic.com/api/oauth/usage"))


if __name__ == "__main__":
    unittest.main()
