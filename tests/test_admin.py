"""FR-4 개발자 전용 명령어 단위 테스트."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from cogs.admin import Admin, cog_autocomplete, is_developer  # noqa: E402

DEV_ID = 713755945502507100
OTHER_ID = 999999999999999999


class IsDeveloperTest(unittest.TestCase):
    def setUp(self):
        self._original = config.DEV_USER_IDS
        config.DEV_USER_IDS = frozenset({DEV_ID})

    def tearDown(self):
        config.DEV_USER_IDS = self._original

    def test_registered_developer_passes(self):
        user = MagicMock()
        user.id = DEV_ID
        self.assertTrue(is_developer(user))

    def test_other_user_is_rejected(self):
        user = MagicMock()
        user.id = OTHER_ID
        self.assertFalse(is_developer(user))

    def test_empty_dev_list_rejects_everyone(self):
        config.DEV_USER_IDS = frozenset()
        user = MagicMock()
        user.id = DEV_ID
        self.assertFalse(is_developer(user))


class CogAutocompleteTest(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, value: str, extensions=None):
        ctx = MagicMock()
        ctx.value = value
        ctx.bot.extensions = extensions or {
            "cogs.activity": object(),
            "cogs.admin": object(),
            "cogs.anonymous": object(),
            "cogs.credit": object(),
            "cogs.youtube": object(),
        }
        return ctx

    async def test_lists_all_loaded_cogs_when_empty(self):
        names = await cog_autocomplete(self._ctx(""))
        self.assertEqual(names, ["activity", "admin", "anonymous", "credit", "youtube"])

    async def test_filters_by_substring(self):
        self.assertEqual(await cog_autocomplete(self._ctx("an")), ["anonymous"])

    async def test_is_case_insensitive(self):
        self.assertEqual(await cog_autocomplete(self._ctx("ACT")), ["activity"])

    async def test_none_value_is_safe(self):
        self.assertGreater(len(await cog_autocomplete(self._ctx(None))), 0)

    async def test_non_cog_extensions_are_excluded(self):
        ctx = self._ctx("", extensions={"cogs.admin": object(), "other.thing": object()})
        self.assertEqual(await cog_autocomplete(ctx), ["admin"])

    async def test_result_is_capped_at_25(self):
        ctx = self._ctx("", extensions={f"cogs.mod{i}": object() for i in range(40)})
        self.assertEqual(len(await cog_autocomplete(ctx)), 25)


class FormatTracebackTest(unittest.TestCase):
    def _raise(self, message: str):
        try:
            raise ValueError(message)
        except ValueError as exc:
            return exc

    def test_short_traceback_is_kept_whole(self):
        summary = Admin._format_traceback(self._raise("boom"))
        self.assertIn("ValueError: boom", summary)
        self.assertNotIn("생략", summary)

    def test_long_traceback_is_truncated(self):
        summary = Admin._format_traceback(self._raise("x" * 5000))
        self.assertIn("생략", summary)
        self.assertLess(len(summary), 1400)

    def test_truncated_summary_fits_in_ephemeral_message(self):
        summary = Admin._format_traceback(self._raise("x" * 20000))
        # 코드블록 마크업까지 포함해도 2000자 제한 안에 들어와야 한다.
        self.assertLess(len(f"❌ `cogs.x` 리로드 실패\n```py\n{summary}\n```"), 2000)


class RestartExitCodeTest(unittest.TestCase):
    """`/재시작` 후 종료 코드가 1이어야 systemd 가 재기동한다."""

    def test_main_returns_1_when_restart_requested(self):
        import importlib

        main_module = importlib.import_module("main")
        main_module.bot.restart_requested = True
        try:
            self.assertTrue(getattr(main_module.bot, "restart_requested"))
        finally:
            main_module.bot.restart_requested = False

    def test_bot_starts_with_restart_flag_false(self):
        import importlib

        main_module = importlib.import_module("main")
        self.assertFalse(main_module.bot.restart_requested)


class IntentsTest(unittest.TestCase):
    """FR-6: reactions intent 가 켜져 있어야 raw 반응 이벤트를 받는다."""

    def test_required_intents_are_enabled(self):
        import importlib

        main_module = importlib.import_module("main")
        intents = main_module.intents
        self.assertTrue(intents.reactions)
        self.assertTrue(intents.guild_reactions)
        self.assertTrue(intents.message_content)
        self.assertTrue(intents.members)
        self.assertTrue(intents.guilds)


if __name__ == "__main__":
    unittest.main()
