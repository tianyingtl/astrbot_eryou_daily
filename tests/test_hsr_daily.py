import unittest

from hsr_daily import (
    GAME_KEY_HSR,
    format_game_menu,
    format_group_bind_guide,
    format_login_menu,
    format_note_status,
    parse_commission_command,
    parse_reminder_value,
)


class HsrDailyTest(unittest.TestCase):
    def test_parse_commission_command(self):
        self.assertEqual(parse_commission_command("/委托"), ("check", GAME_KEY_HSR))
        self.assertEqual(parse_commission_command("／委托帮助"), ("help", ""))
        self.assertEqual(parse_commission_command("/委托绑定"), ("bind_game_menu", ""))
        self.assertEqual(parse_commission_command("/委托绑定 星铁"), ("bind_game", GAME_KEY_HSR))
        self.assertEqual(parse_commission_command("/委托扫码"), ("qr", ""))
        self.assertEqual(parse_commission_command("/委托手机号"), ("phone", ""))
        self.assertEqual(parse_commission_command("/委托确认"), ("confirm", ""))
        self.assertEqual(parse_commission_command("/委托设置 星铁 20:00"), ("reminder_set", "星铁 20:00"))
        self.assertEqual(parse_commission_command("/委托解绑"), ("unbind", ""))
        self.assertIsNone(parse_commission_command("普通消息"))

    def test_bind_menus(self):
        self.assertIn("/委托绑定 星铁", format_game_menu())
        self.assertIn("/委托扫码", format_login_menu(GAME_KEY_HSR))
        self.assertNotIn("/委托扫码", format_group_bind_guide())

    def test_parse_reminder_value(self):
        self.assertEqual(parse_reminder_value("星铁 20:00"), (GAME_KEY_HSR, "20:00", None))
        self.assertEqual(parse_reminder_value("崩坏星穹铁道 8:30"), (GAME_KEY_HSR, "08:30", None))
        self.assertIsNotNone(parse_reminder_value("星铁 晚上八点")[2])

    def test_format_note_status_clear(self):
        role = {"nickname": "开拓者", "game_uid": "100000000"}
        note = {
            "current_train_score": 500,
            "max_train_score": 500,
            "accepted_epedition_num": 4,
            "total_epedition_num": 4,
            "expeditions": [
                {"status": "Ongoing", "remaining_time": 3600},
                {"status": "Finished", "remaining_time": 0},
            ],
        }

        text = format_note_status(role, note)

        self.assertIn("每日实训：500/500，已完成", text)
        self.assertNotIn("派" + "遣", text)
        self.assertIn("今天的每日已经 Clear。", text)


if __name__ == "__main__":
    unittest.main()
