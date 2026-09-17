from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

_plugin_dir = Path(__file__).resolve().parents[1] / "plugins" / "cpaplugin"
_pkg = types.ModuleType("cpaplugin")
_pkg.__path__ = [str(_plugin_dir)]
sys.modules.setdefault("cpaplugin", _pkg)

from cpaplugin.config import Config
from cpaplugin.hub import ClientSession, Hub, HubError, authenticate_headers
from cpaplugin.protocol import ALLOWED_ACTIONS, valid_client_name
from cpaplugin.query import parse_quota_command
from cpaplugin.quota import accounts_from_result, board_from_accounts


class ClientNameTests(unittest.TestCase):
    def test_valid_names(self) -> None:
        self.assertTrue(valid_client_name("Server"))
        self.assertTrue(valid_client_name("Home"))
        self.assertTrue(valid_client_name("家里号"))
        self.assertFalse(valid_client_name("-bad"))
        self.assertFalse(valid_client_name("a/b"))
        self.assertFalse(valid_client_name("has space"))
        self.assertFalse(valid_client_name(""))


class QuotaCommandParseTests(unittest.TestCase):
    def _parse(self, text: str, known: set[str] | None = None):
        return parse_quota_command(
            text,
            known_clients=known or {"Server", "Home"},
            default_client="Server",
        )

    def test_default_local(self) -> None:
        sel = self._parse("/cpa quota")
        self.assertFalse(sel.all_clients)
        self.assertEqual(sel.client_name, "Server")
        self.assertIsNone(sel.platform)

    def test_platform_on_default_client(self) -> None:
        sel = self._parse("cpa quota antigravity")
        self.assertEqual(sel.platform, "antigravity")
        self.assertEqual(sel.client_name, "Server")

    def test_client_then_platform(self) -> None:
        sel = self._parse("/cpa quota Home antigravity")
        self.assertEqual(sel.client_name, "Home")
        self.assertEqual(sel.platform, "antigravity")

    def test_platform_then_client(self) -> None:
        sel = self._parse("/cpa quota antigravity Home")
        self.assertEqual(sel.client_name, "Home")
        self.assertEqual(sel.platform, "antigravity")

    def test_all_variants(self) -> None:
        for text in ("/cpa quota --all", "/cpa quota -all", "/cpa quota -a", "/cpa quota codex -all"):
            sel = self._parse(text)
            self.assertTrue(sel.all_clients, text)
            self.assertIsNone(sel.client_name)
        sel = self._parse("/cpa quota codex --all")
        self.assertEqual(sel.platform, "codex")
        self.assertTrue(sel.all_clients)

    def test_explicit_client_flag(self) -> None:
        sel = self._parse("/cpa quota antigravity --client Home")
        self.assertEqual(sel.client_name, "Home")
        self.assertEqual(sel.platform, "antigravity")

    def test_ambiguous_platform_and_client(self) -> None:
        sel = self._parse("/cpa quota codex", known={"Server", "codex"})
        self.assertIsNotNone(sel.error)
        self.assertIn("--client", sel.error or "")

    def test_unknown_token_is_account(self) -> None:
        sel = self._parse("/cpa quota AG-1")
        self.assertEqual(sel.account, "AG-1")
        self.assertEqual(sel.client_name, "Server")

    def test_reject_client_plus_all(self) -> None:
        sel = self._parse("/cpa quota Home --all")
        self.assertIsNotNone(sel.error)


class HubAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config(
            client_name="Server",
            cpa_server_client_keys={"Home": "secret-home", "HK": "secret-hk"},
        )

    def test_accepts_matching_key(self) -> None:
        name, error, _code = authenticate_headers(
            {"authorization": "Bearer secret-home", "x-cpa-client-name": "Home"},
            self.cfg,
        )
        self.assertEqual(name, "Home")
        self.assertEqual(error, "")

    def test_rejects_wrong_key(self) -> None:
        name, error, _code = authenticate_headers(
            {"authorization": "Bearer secret-hk", "x-cpa-client-name": "Home"},
            self.cfg,
        )
        self.assertEqual(name, "")
        self.assertIn("鉴权", error)

    def test_rejects_reserved_local_name(self) -> None:
        name, error, _code = authenticate_headers(
            {"authorization": "Bearer secret-home", "x-cpa-client-name": "Server"},
            self.cfg,
        )
        self.assertEqual(name, "")
        self.assertIn("本机", error)

    def test_query_action_only(self) -> None:
        self.assertEqual(ALLOWED_ACTIONS, frozenset({"quota.query"}))
        self.assertNotIn("quota.reset", ALLOWED_ACTIONS)
        self.assertNotIn("codex.refresh", ALLOWED_ACTIONS)


class RemoteBoardTests(unittest.TestCase):
    def test_accounts_from_result_keep_client(self) -> None:
        accounts = accounts_from_result(
            {
                "client_name": "Home",
                "cached": True,
                "accounts": [
                    {
                        "platform": "codex",
                        "name": "CX-1",
                        "auth_index": "abcd",
                        "windows": [
                            {
                                "id": "code-5h",
                                "label": "5h",
                                "remaining_percent": 80.0,
                                "reset_label": "2h",
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(accounts[0].client_name, "Home")
        board = board_from_accounts(accounts, cached=True)
        self.assertTrue(board.cached)
        self.assertEqual(board.platforms[0].platform, "codex")
        self.assertEqual(len(board.platforms[0].accounts), 1)


class HubResponseIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_response_with_different_client_name(self) -> None:
        class DummySocket:
            pass

        hub = Hub()
        session = ClientSession(name="Home", websocket=DummySocket(), session_id="s")  # type: ignore[arg-type]
        future = asyncio.get_running_loop().create_future()
        session.pending["r1"] = future
        cfg = Config(client_name="Server", cpa_server_client_keys={"Home": "secret-home"})
        await hub._on_message(
            session,
            {"version": 1, "type": "response", "id": "r1", "ok": True, "result": {"client_name": "Other", "accounts": []}},
            cfg,
        )
        with self.assertRaises(HubError):
            await future


if __name__ == "__main__":
    unittest.main()
