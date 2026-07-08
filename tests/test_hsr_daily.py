import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hsr_daily import (
    BindingStore,
    GAME_KEY_GENSHIN,
    GAME_KEY_HSR,
    GAME_KEY_NTE,
    GAME_KEY_ZZZ,
    format_game_menu,
    format_group_bind_guide,
    format_nte_bind_guide,
    format_note_status,
    is_daily_done,
    parse_commission_command,
    parse_reminder_value,
)


class HsrDailyTest(unittest.TestCase):
    def test_parse_commission_command(self):
        self.assertEqual(parse_commission_command("/委托"), ("check", ""))
        self.assertEqual(parse_commission_command("/委托 原神"), ("check", GAME_KEY_GENSHIN))
        self.assertEqual(parse_commission_command("/委托 绝区零"), ("check", GAME_KEY_ZZZ))
        self.assertEqual(parse_commission_command("/委托 异环"), ("check", GAME_KEY_NTE))
        self.assertEqual(parse_commission_command("／委托帮助"), ("help", ""))
        self.assertEqual(parse_commission_command("/委托绑定"), ("bind_game_menu", ""))
        self.assertEqual(parse_commission_command("/委托绑定 星铁"), ("bind_game", GAME_KEY_HSR))
        self.assertEqual(parse_commission_command("/委托绑定 原神"), ("bind_game", GAME_KEY_GENSHIN))
        self.assertEqual(parse_commission_command("/委托绑定 绝区零"), ("bind_game", GAME_KEY_ZZZ))
        self.assertEqual(parse_commission_command("/委托绑定 异环"), ("bind_game", GAME_KEY_NTE))
        self.assertEqual(parse_commission_command("/委托发码 13800138000"), ("sms", "13800138000"))
        self.assertEqual(parse_commission_command("/委托扫码"), ("qr", ""))
        self.assertEqual(parse_commission_command("/委托确认"), ("confirm", ""))
        self.assertEqual(parse_commission_command("/委托确认 123456"), ("confirm", "123456"))
        self.assertEqual(parse_commission_command("/委托设置 星铁 20:00"), ("reminder_set", "星铁 20:00"))
        self.assertEqual(parse_commission_command("/委托解绑"), ("unbind", ""))
        self.assertIsNone(parse_commission_command("普通消息"))

    def test_bind_menus(self):
        self.assertIn("/委托绑定 星铁", format_game_menu())
        self.assertIn("/委托绑定 原神", format_game_menu())
        self.assertIn("/委托绑定 绝区零", format_game_menu())
        self.assertIn("/委托绑定 异环", format_game_menu())
        self.assertIn("塔吉多手机号短信登录", format_game_menu())
        self.assertIn("/委托发码 手机号", format_nte_bind_guide())
        self.assertNotIn("/委托扫码", format_group_bind_guide())

    def test_parse_reminder_value(self):
        self.assertEqual(parse_reminder_value("星铁 20:00"), (GAME_KEY_HSR, "20:00", None))
        self.assertEqual(parse_reminder_value("原神 20:00"), (GAME_KEY_GENSHIN, "20:00", None))
        self.assertEqual(parse_reminder_value("绝区零 20:00"), (GAME_KEY_ZZZ, "20:00", None))
        self.assertEqual(parse_reminder_value("异环 20:00"), (GAME_KEY_NTE, "20:00", None))
        self.assertEqual(parse_reminder_value("崩坏星穹铁道 8:30"), (GAME_KEY_HSR, "08:30", None))
        self.assertIsNotNone(parse_reminder_value("星铁 晚上八点")[2])

    def test_format_note_status_clear(self):
        role = {"nickname": "开拓者", "game_uid": "100000000"}
        note = {
            "current_train_score": 500,
            "max_train_score": 500,
            "current_stamina": 120,
            "max_stamina": 240,
            "current_reserve_stamina": 300,
            "accepted_epedition_num": 4,
            "total_epedition_num": 4,
            "expeditions": [
                {"status": "Ongoing", "remaining_time": 3600},
                {"status": "Finished", "remaining_time": 0},
            ],
        }

        text = format_note_status(GAME_KEY_HSR, role, note)

        self.assertIn("每日实训：500/500，已完成", text)
        self.assertIn("开拓力：120/240", text)
        self.assertIn("后备开拓力：300", text)
        self.assertNotIn("派" + "遣", text)
        self.assertIn("今天的每日已经 Clear。", text)

    def test_format_genshin_status_clear(self):
        role = {"nickname": "旅行者", "game_uid": "100000001"}
        note = {
            "current_commission_num": 4,
            "max_commission_num": 4,
            "current_resin": 80,
            "max_resin": 200,
            "is_extra_task_reward_received": True,
        }

        text = format_note_status(GAME_KEY_GENSHIN, role, note)

        self.assertTrue(is_daily_done(GAME_KEY_GENSHIN, note))
        self.assertIn("原粹树脂：80/200", text)
        self.assertIn("每日委托：4/4，已完成", text)
        self.assertIn("凯瑟琳奖励：已领取", text)

    def test_format_zzz_status_clear(self):
        role = {"nickname": "绳匠", "game_uid": "100000002"}
        note = {
            "vitality": {"current": 400, "max": 400},
            "energy": {"current": 120, "max": 240},
            "card_sign": "CardSignDone",
        }

        text = format_note_status(GAME_KEY_ZZZ, role, note)

        self.assertTrue(is_daily_done(GAME_KEY_ZZZ, note))
        self.assertIn("电量：120/240", text)
        self.assertIn("今日活跃：400/400，已完成", text)
        self.assertIn("刮刮卡：已刮", text)

    def test_format_nte_status_clear(self):
        role = {"nickname": "塔吉多", "game_uid": "116771663"}
        note = {
            "rolename": "塔吉多",
            "roleid": "116771663",
            "staminaValue": 160,
            "staminaMaxValue": 240,
            "citystaminaValue": 60,
            "citystaminaMaxValue": 100,
            "dayvalue": 100,
        }

        text = format_note_status(GAME_KEY_NTE, role, note)

        self.assertTrue(is_daily_done(GAME_KEY_NTE, note))
        self.assertIn("本性像素：160/240", text)
        self.assertIn("都市活力：60/100", text)
        self.assertIn("今日活跃：100/100，已完成", text)

    def test_store_keeps_mihoyo_and_tajiduo_accounts(self):
        with TemporaryDirectory() as temp_dir:
            store = BindingStore(Path(temp_dir) / "bindings.json")
            store.set_account_cookie("123", "ltoken=abc")
            store.set_tajiduo_binding(
                "123",
                {"access_token": "a", "refresh_token": "r", "center_uid": "9", "device_id": "HT1"},
                {"game_uid": "116771663", "nickname": "塔吉多"},
            )

            cookie = store.get_account_cookie("123")
            account = store.get_tajiduo_account("123")
            binding = store.get_game_binding("123", GAME_KEY_NTE)

        self.assertEqual(cookie, "ltoken=abc")
        self.assertEqual(account["center_uid"], "9")
        self.assertEqual(binding["role"]["game_uid"], "116771663")

    def test_set_reminder_can_skip_today(self):
        with TemporaryDirectory() as temp_dir:
            store = BindingStore(Path(temp_dir) / "bindings.json")
            store.set_reminder("123", "456", "umo", GAME_KEY_HSR, "00:00", "2026-07-06")

            reminders = store.get_reminders()

        self.assertEqual(reminders[0][1]["last_reminded_date"], "2026-07-06")


if __name__ == "__main__":
    unittest.main()
