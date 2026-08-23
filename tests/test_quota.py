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
from cpaplugin.quota import (
    AccountQuota,
    _build_board,
    _wanted_files,
    format_quota_board,
    is_platform_query,
    normalize_platform,
    parse_antigravity_summary,
    parse_claude_usage,
    parse_codex_usage,
    parse_xai_billing,
    platform_of,
    platform_total_chips,
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
        self.assertEqual(platform_of({"provider": "openai"}), "other")


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
        self.assertEqual(plan, "pro")
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
        self.assertEqual(plan, "plus")
        self.assertEqual(windows[0].id, "code-5h")
        self.assertEqual(windows[0].remaining_percent, 90.0)
        self.assertEqual(windows[1].id, "code-7d")
        self.assertEqual(windows[1].remaining_percent, 100.0)

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
        self.assertEqual(by_id["billing"].remaining_percent, 87.0)
        self.assertEqual(by_id["grok-build"].used_percent, 8.0)
        self.assertEqual(by_id["grok-chat"].used_percent, 5.0)


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
        self.assertIn("Gemini 5h 3.44/4 (86%)", html_doc)
        self.assertIn("86% remaining", html_doc)
        self.assertIn("Gemini Models", html_doc)
        self.assertIn("Claude and GPT Models", html_doc)
        self.assertEqual(html_doc.count('<article class="card">'), 4)
        self.assertIn('class="card-header"', html_doc)
        self.assertIn('class="card-content"', html_doc)
        self.assertIn('class="progress"', html_doc)
        self.assertIn("progress-indicator", html_doc)
        self.assertIn("translateX(-14.0%)", html_doc)
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


class AllowlistTests(unittest.TestCase):
    def test_quota_urls_allowed(self) -> None:
        self.assertTrue(
            allowed_api_call_url(
                "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
            )
        )
        self.assertTrue(allowed_api_call_url("https://cli-chat-proxy.grok.com/v1/billing?format=credits"))
        self.assertFalse(allowed_api_call_url("https://example.com/steal"))
        self.assertFalse(allowed_api_call_url("http://api.anthropic.com/api/oauth/usage"))


if __name__ == "__main__":
    unittest.main()
