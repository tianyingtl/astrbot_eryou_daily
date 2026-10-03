import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from hsr_daily import BindingStore, GAME_KEY_HSR


def load_plugin():
    # Replace only AstrBot's transport types; run the real reminder loop and store.
    names = ["astrbot", "astrbot.api", "astrbot.api.event",
             "astrbot.api.star", "astrbot.api.message_components"]
    modules = {name: ModuleType(name) for name in names}
    event = modules["astrbot.api.event"]
    event.AstrMessageEvent = object
    event.MessageChain = list
    event.filter = SimpleNamespace(
        EventMessageType=SimpleNamespace(ALL="all"),
        event_message_type=lambda _: lambda handler: handler,
    )
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = object
    components = modules["astrbot.api.message_components"]
    components.At = lambda qq: SimpleNamespace(qq=qq)
    components.Plain = lambda text: SimpleNamespace(text=text)
    spec = importlib.util.spec_from_file_location(
        "eryou_reminder_test_main", Path(__file__).resolve().parents[1] / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class HsrReminderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_plugin()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin = self.module.EryouDailyPlugin.__new__(self.module.EryouDailyPlugin)
        self.plugin.config = {}
        self.plugin.bindings = BindingStore(Path(self.temp.name) / "bindings.json")
        self.plugin.context = SimpleNamespace(send_message=AsyncMock())
        self.plugin.bindings.set_account_cookie("123", "test-cookie")
        self.plugin.bindings.set_game_binding("123", GAME_KEY_HSR, {"game_uid": "test"})
        self.plugin.bindings.set_reminder("123", "456", "test-group", GAME_KEY_HSR, "20:00")

    async def run_at(self, now, score=12000, train=500):
        note = {"current_train_score": train, "max_train_score": 500,
                "current_rogue_score": score, "max_rogue_score": 18000}
        with patch.object(self.module, "datetime") as clock:
            clock.now.return_value = now
            with patch.object(self.module, "fetch_daily_note", return_value=note) as fetch:
                await self.plugin._run_due_reminders()
                return fetch.call_count

    async def test_sunday_weekly_only_mentions_user_once(self):
        now = datetime(2026, 9, 27, 20, 0)
        await self.run_at(now)
        self.plugin.context.send_message.assert_awaited_once()
        target, message = self.plugin.context.send_message.call_args.args
        self.assertEqual(target, "test-group")
        self.assertEqual(message[0].qq, 123)
        self.assertIn("12000/18000", message[1].text)
        self.assertNotIn("每日实训还没完成", message[1].text)
        self.assertEqual(await self.run_at(now), 0)
        self.plugin.context.send_message.assert_awaited_once()

    async def test_before_time_does_not_fetch_or_send(self):
        self.assertEqual(await self.run_at(datetime(2026, 9, 27, 19, 59)), 0)
        self.plugin.context.send_message.assert_not_awaited()

    async def test_saturday_does_not_remind_weekly(self):
        await self.run_at(datetime(2026, 9, 26, 20, 0))
        self.plugin.context.send_message.assert_not_awaited()

    async def test_both_complete_does_not_send(self):
        await self.run_at(datetime(2026, 9, 27, 20, 0), score=18000)
        self.plugin.context.send_message.assert_not_awaited()

    async def test_daily_and_weekly_combine_into_one_message(self):
        await self.run_at(datetime(2026, 9, 27, 20, 0), train=100)
        self.plugin.context.send_message.assert_awaited_once()
        text = self.plugin.context.send_message.call_args.args[1][1].text
        self.assertIn("每日实训还没完成", text)
        self.assertIn("每周积分还没满", text)

    async def test_next_sunday_can_remind_again(self):
        await self.run_at(datetime(2026, 9, 27, 20, 0))
        await self.run_at(datetime(2026, 10, 4, 20, 0))
        self.assertEqual(self.plugin.context.send_message.await_count, 2)

    async def test_blacklisted_group_does_not_fetch_or_send(self):
        self.plugin.config = {"group_filter_mode": "黑名单", "blacklist_groups": ["456"]}
        self.assertEqual(await self.run_at(datetime(2026, 9, 27, 20, 0)), 0)
        self.plugin.context.send_message.assert_not_awaited()

