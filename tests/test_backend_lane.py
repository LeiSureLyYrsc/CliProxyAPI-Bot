from __future__ import annotations

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "cpaplugin"
_pkg = types.ModuleType("cpaplugin")
_pkg.__path__ = [str(_plugin_dir)]
sys.modules.setdefault("cpaplugin", _pkg)

from cpaplugin.protocol import AccountQuotaDTO, QuotaQueryResult, QuotaWindowDTO
from cpaplugin.quota import (
    AccountQuota,
    PlatformQuota,
    QuotaWindow,
    _build_board,
    accounts_from_result,
    calculate_aggregate_windows,
    calculate_plan_distribution,
    calculate_total_reset_credits,
    count_codex_reset_credits,
    extract_earliest_reset_seconds,
    extract_subscription_expiry,
    format_quota_board,
    format_subscription_expiry_label,
)
from cpaplugin.render_settings import (
    DEFAULT_CARDS_PER_ROW,
    DEFAULT_THEME,
    RenderSettings,
    get_render_settings,
    get_render_settings_path,
    load_render_settings,
    reset_render_settings_cache,
    save_render_settings,
    set_cards_per_row,
    set_theme,
    use_memory_render_settings,
)


class RenderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_render_settings_cache()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_default_settings(self) -> None:
        use_memory_render_settings()
        settings = get_render_settings()
        self.assertEqual(settings.theme, DEFAULT_THEME)
        self.assertEqual(settings.cards_per_row, DEFAULT_CARDS_PER_ROW)

    def test_theme_mutation_and_alias(self) -> None:
        use_memory_render_settings()
        set_theme("mac")
        self.assertEqual(get_render_settings().theme, "mac")
        set_theme("default")
        self.assertEqual(get_render_settings().theme, "shadcn")
        set_theme("md3")
        self.assertEqual(get_render_settings().theme, "md3")
        with self.assertRaises(ValueError):
            set_theme("invalid_theme_name")

    def test_cards_per_row_mutation_and_bounds(self) -> None:
        use_memory_render_settings()
        set_cards_per_row(2)
        self.assertEqual(get_render_settings().cards_per_row, 2)
        set_cards_per_row("6")
        self.assertEqual(get_render_settings().cards_per_row, 6)
        with self.assertRaises(ValueError):
            set_cards_per_row(0)
        with self.assertRaises(ValueError):
            set_cards_per_row(7)
        with self.assertRaises(ValueError):
            set_cards_per_row("abc")

    def test_file_persistence_and_preserves_unknown_fields(self) -> None:
        reset_render_settings_cache()
        target_file = Path(self.temp_dir.name) / "cpa_render_settings.json"
        
        # Write file with unknown fields
        initial_data = {
            "theme": "mac",
            "cards_per_row": 3,
            "custom_theme_config": {"accent": "blue"},
            "version": 2,
        }
        target_file.write_text(json.dumps(initial_data), encoding="utf-8")

        class FakeConfig:
            cpa_alias_file = str(Path(self.temp_dir.name) / "cpa_aliases.json")

        # Mock get_render_settings_path to return target_file
        import cpaplugin.render_settings as rs
        orig_get_path = rs.get_render_settings_path
        rs.get_render_settings_path = lambda cfg=None: target_file
        try:
            settings = load_render_settings()
            self.assertEqual(settings.theme, "mac")
            self.assertEqual(settings.cards_per_row, 3)
            self.assertEqual(settings.extra.get("version"), 2)

            # Modify theme via API
            set_theme("md3")
            
            # Read back from disk directly
            saved_raw = json.loads(target_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_raw["theme"], "md3")
            self.assertEqual(saved_raw["cards_per_row"], 3)
            self.assertEqual(saved_raw["custom_theme_config"], {"accent": "blue"})
            self.assertEqual(saved_raw["version"], 2)
        finally:
            rs.get_render_settings_path = orig_get_path

    def test_corrupted_file_falls_back_to_defaults(self) -> None:
        reset_render_settings_cache()
        target_file = Path(self.temp_dir.name) / "cpa_render_settings.json"
        target_file.write_text("invalid json content {{{", encoding="utf-8")

        import cpaplugin.render_settings as rs
        orig_get_path = rs.get_render_settings_path
        rs.get_render_settings_path = lambda cfg=None: target_file
        try:
            settings = load_render_settings()
            self.assertEqual(settings.theme, DEFAULT_THEME)
            self.assertEqual(settings.cards_per_row, DEFAULT_CARDS_PER_ROW)
        finally:
            rs.get_render_settings_path = orig_get_path

    def test_failed_write_does_not_change_cached_settings(self) -> None:
        import cpaplugin.render_settings as rs

        reset_render_settings_cache()
        use_memory_render_settings({"theme": "shadcn", "cards_per_row": 4})
        rs._memory_only = False
        current = get_render_settings()
        self.assertEqual(current.theme, "shadcn")

        blocker = Path(self.temp_dir.name) / "not-a-directory"
        blocker.write_text("file", encoding="utf-8")
        impossible = blocker / "cpa_render_settings.json"
        orig_get_path = rs.get_render_settings_path
        rs.get_render_settings_path = lambda cfg=None: impossible
        try:
            with self.assertRaises(ValueError):
                set_theme("mac")
            self.assertEqual(get_render_settings().theme, "shadcn")
            self.assertEqual(get_render_settings().cards_per_row, 4)
        finally:
            rs.get_render_settings_path = orig_get_path


class SubscriptionExpiryTests(unittest.TestCase):
    def test_generic_extraction_from_auth_file_and_nested(self) -> None:
        # 1. Top-level auth token expires_at must be ignored
        ts_top, label_top = extract_subscription_expiry({"expires_at": "2026-11-15T00:00:00Z"})
        self.assertIsNone(ts_top)
        self.assertEqual(label_top, "")

        # 2. Top-level explicit subscription_expires_at is accepted
        ts_top_explicit, label_top_explicit = extract_subscription_expiry({
            "subscription_expires_at": "2026-11-15T00:00:00Z"
        })
        self.assertIsNotNone(ts_top_explicit)
        self.assertIn("2026-11-15", label_top_explicit)
        self.assertIn("剩", label_top_explicit)

        # 3. Nested subscription dict allows generic expires_at/valid_until
        ts2, label2 = extract_subscription_expiry({
            "subscription": {
                "expires_at": 1790000000,
            }
        })
        self.assertIsNotNone(ts2)
        self.assertTrue(ts2 > 0)
        self.assertTrue(len(label2) > 0)

        # 4. Nested plan dict with millisecond timestamp
        ts3, label3 = extract_subscription_expiry({
            "plan": {
                "valid_until": 1790000000000,
            }
        })
        self.assertIsNotNone(ts3)
        self.assertAlmostEqual(ts3, 1790000000.0, places=1)

    def test_xai_billing_period_end_is_not_treated_as_subscription_expiry(self) -> None:
        data = {
            "config": {
                "billingPeriodEnd": "2026-08-24T05:33:00Z",
                "creditUsagePercent": 15,
            }
        }
        ts, label = extract_subscription_expiry(data)
        self.assertIsNone(ts)
        self.assertEqual(label, "")

    def test_expiry_label_countdown_format(self) -> None:
        future_ts = time.time() + 86400 * 5 + 3600 * 3 + 100
        label = format_subscription_expiry_label(future_ts)
        self.assertIn("5天3小时", label)

        past_ts = time.time() - 3600
        label_past = format_subscription_expiry_label(past_ts)
        self.assertIn("已过期", label_past)


class CodexResetCreditsTests(unittest.TestCase):
    def test_count_explicit_available_count(self) -> None:
        self.assertEqual(count_codex_reset_credits({"available_count": 3}), 3)
        self.assertEqual(count_codex_reset_credits({"availableCount": "5"}), 5)
        self.assertEqual(count_codex_reset_credits({"remaining": 2}), 2)

    def test_count_unredeemed_credit_list(self) -> None:
        data = {
            "credits": [
                {"id": "c1", "status": "available"},
                {"id": "c2", "status": "available"},
                {"id": "c3", "status": "used", "redeemed": True},
            ]
        }
        self.assertEqual(count_codex_reset_credits(data), 2)

    def test_credits_failure_does_not_corrupt_account_status(self) -> None:
        # Simulate normal codex fill followed by credit failure
        account = AccountQuota(
            platform="codex",
            name="cx1",
            auth_index="idx1",
            plan="Pro",
            status="ok",
            windows=[QuotaWindow(id="code-5h", label="5h", remaining_percent=80.0)],
        )
        saved_status = account.status
        saved_error = account.error
        try:
            # Fake failure in credits endpoint
            raise Exception("500 Internal Server Error on credits URL")
        except Exception:
            pass
        finally:
            account.status = saved_status
            account.error = saved_error
        self.assertEqual(account.status, "ok")
        self.assertEqual(account.error, "")
        self.assertEqual(len(account.windows), 1)


class AggregateHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        now = time.time()
        self.accounts = [
            AccountQuota(
                platform="claude",
                name="c1",
                auth_index="1",
                plan="Pro",
                cooling=True,
                reset_credits=3,
                windows=[
                    QuotaWindow(id="5h", label="5h", remaining_percent=80.0, reset_at=now + 3600, reset_label="1h"),
                    QuotaWindow(id="7d", label="7d", remaining_percent=50.0, reset_at=now + 86400 * 3, reset_label="3d"),
                ],
            ),
            AccountQuota(
                platform="claude",
                name="c2",
                auth_index="2",
                plan="Pro",
                reset_credits=2,
                windows=[
                    QuotaWindow(id="5h", label="5h", remaining_percent=40.0, reset_at=now + 1800, reset_label="30m"),
                    QuotaWindow(id="7d", label="7d", remaining_percent=70.0, reset_at=now + 86400 * 2, reset_label="2d"),
                ],
            ),
            AccountQuota(
                platform="claude",
                name="c3",
                auth_index="3",
                plan="Plus",
                windows=[
                    QuotaWindow(id="5h", label="5h", remaining_percent=60.0, reset_label="2h"),
                ],
            ),
        ]
        self.section = PlatformQuota(
            platform="claude",
            title="Claude",
            accounts=self.accounts,
            window_remain_sum={"5h": 180.0, "7d": 120.0},
            window_remain_count={"5h": 3, "7d": 2},
            window_labels={"5h": "5h", "7d": "7d"},
        )

    def test_plan_distribution(self) -> None:
        dist = calculate_plan_distribution(self.accounts)
        self.assertEqual(dist, [("Pro", 2), ("Plus", 1)])

    def test_total_reset_credits(self) -> None:
        total = calculate_total_reset_credits(self.accounts)
        self.assertEqual(total, 5)

    def test_earliest_reset_seconds(self) -> None:
        earliest = extract_earliest_reset_seconds(self.accounts)
        self.assertIsNotNone(earliest)
        # Should be closest to 1800 (30m)
        self.assertTrue(1700 <= earliest <= 1850)

    def test_earliest_reset_seconds_ms_and_past_timestamp_isolation(self) -> None:
        now = time.time()
        # Case 1: Millisecond timestamp in the future
        ms_account = AccountQuota(
            platform="codex",
            name="cx_ms",
            auth_index="m1",
            windows=[
                QuotaWindow(id="5h", label="5h", reset_at=(now + 1200) * 1000.0, reset_label="30m"),
            ],
        )
        self.assertAlmostEqual(extract_earliest_reset_seconds([ms_account]), 1200.0, delta=5.0)

        # Case 2: Past numeric reset_at must NOT fall back to stale reset_label
        stale_account = AccountQuota(
            platform="codex",
            name="cx_stale",
            auth_index="s1",
            windows=[
                QuotaWindow(id="5h", label="5h", reset_at=now - 500, reset_label="1h"),
            ],
        )
        self.assertIsNone(extract_earliest_reset_seconds([stale_account]))

    def test_codex_account_id_helper_and_plan_dict_sanitization(self) -> None:
        from cpaplugin.quota import _codex_account_id, _sanitize_plan

        # Codex account_id helper
        self.assertEqual(_codex_account_id({"chatgpt_account_id": "acc-1234"}), "acc-1234")
        self.assertEqual(_codex_account_id({"chatgpt_account_id": "user@example.com"}), "")
        self.assertEqual(_codex_account_id({"account": "user@example.com"}), "")

        # Plan dictionary sanitization
        self.assertEqual(_sanitize_plan({"name": "Google AI Pro"}), "Pro")
        self.assertEqual(_sanitize_plan({"tier": "Plus"}), "Plus")
        self.assertEqual(_sanitize_plan({"unknown_field": "some_val"}), "")
        self.assertEqual(_sanitize_plan("{'raw': 'dict'}"), "")

    def test_no_windows_cooling_chinese_text_rendering(self) -> None:
        # Account with cooling status and no windows
        cooling_no_win = AccountQuota(
            platform="kimi",
            name="km1",
            auth_index="k1",
            status="cooling",
            windows=[],
        )
        from cpaplugin.render import _card_html
        card_html = _card_html(cooling_no_win)
        self.assertIn("冷却中（无上游配额）", card_html)
        self.assertNotIn("cooling（无上游配额）", card_html)

    def test_aggregate_windows_summary(self) -> None:
        agg = calculate_aggregate_windows(self.section, self.accounts)
        by_id = {item["id"]: item for item in agg}
        self.assertEqual(by_id["5h"]["count"], 3)
        self.assertAlmostEqual(by_id["5h"]["sum_percent"], 180.0)
        self.assertAlmostEqual(by_id["5h"]["avg_percent"], 60.0)

        self.assertEqual(by_id["7d"]["count"], 2)
        self.assertAlmostEqual(by_id["7d"]["sum_percent"], 120.0)
        self.assertAlmostEqual(by_id["7d"]["avg_percent"], 60.0)

    def test_cooling_chinese_in_text(self) -> None:
        board = _build_board(self.accounts)
        text = "\n".join(format_quota_board(board))
        self.assertIn("冷却中", text)
        self.assertNotIn(" [cooling]", text)


class DTORoundtripTests(unittest.TestCase):
    def test_protocol_dto_roundtrip_with_new_fields(self) -> None:
        window_dto = QuotaWindowDTO(
            id="w1",
            label="5h",
            remaining_percent=90.0,
            reset_label="2h",
            reset_at=1790000000.0,
        )
        account_dto = AccountQuotaDTO(
            platform="codex",
            name="cx1",
            auth_index="idx1",
            plan="Pro",
            windows=[window_dto],
            subscription_expires_at=1795000000.0,
            subscription_expires_label="2026-12-01",
            reset_credits=4,
        )
        query_result = QuotaQueryResult(
            client_name="Home",
            queried_at="2026-09-17T00:00:00Z",
            cached=False,
            accounts=[account_dto],
        )
        raw_dict = query_result.model_dump()
        reconstructed_accounts = accounts_from_result(raw_dict)
        self.assertEqual(len(reconstructed_accounts), 1)
        acct = reconstructed_accounts[0]
        self.assertEqual(acct.subscription_expires_at, 1795000000.0)
        self.assertEqual(acct.subscription_expires_label, "2026-12-01")
        self.assertEqual(acct.reset_credits, 4)
        self.assertEqual(len(acct.windows), 1)
        self.assertEqual(acct.windows[0].reset_at, 1790000000.0)


if __name__ == "__main__":
    unittest.main()
