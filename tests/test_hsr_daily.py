import unittest
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from hsr_daily import (
    BindingStore,
    GAME_KEY_GENSHIN,
    GAME_KEY_HSR,
    GAME_KEY_NTE,
    GAME_KEY_ZZZ,
    HsrApiError,
    TAJIDUO_APP_VERSION,
    TAJIDUO_BASE_URL,
    _request_json,
    _tajiduo_request,
    fetch_nte_daily_note,
    format_game_menu,
    format_group_bind_guide,
    format_nte_bind_guide,
    format_note_status,
    is_daily_done,
    nte_reminder_reasons,
    parse_commission_command,
    parse_reminder_value,
    resolve_binding_path,
    select_nte_role,
)


class HsrDailyTest(unittest.TestCase):
    def test_tajiduo_matches_reference_android_app_version(self):
        # 必须与安卓 App 抓包协议（NTEUID 参考实现）一致；官网 Web 版本号不通用
        self.assertEqual(TAJIDUO_APP_VERSION, "1.2.4")

    def test_tajiduo_http_error_keeps_status_code(self):
        error = HTTPError("https://example.invalid", 402, "Payment Required", {}, None)

        with patch("hsr_daily.urlopen", side_effect=error):
            with self.assertRaises(HsrApiError) as raised:
                _request_json(
                    TAJIDUO_BASE_URL,
                    "/usercenter/api/v2/getGameRoles",
                    method="GET",
                    error_prefix="塔吉多",
                )

        self.assertEqual(raised.exception.status_code, 402)

    def test_tajiduo_402_refreshes_and_retries_once(self):
        account = {
            "access_token": "stale-access",
            "refresh_token": "valid-refresh",
            "device_id": "HT1",
            "access_token_updated_at": 1,
        }
        refreshed = {
            "code": 0,
            "data": {"accessToken": "fresh-access", "refreshToken": "fresh-refresh"},
        }
        roles = {"code": 0, "data": {"roles": [{"roleId": "116771663"}]}}

        with patch(
            "hsr_daily._request_json",
            side_effect=[HsrApiError("塔吉多接口返回 HTTP 402", status_code=402), refreshed, roles],
        ) as request_json:
            result = _tajiduo_request(
                account,
                "/usercenter/api/v2/getGameRoles",
                query={"gameId": "1289"},
            )

        self.assertEqual(result, roles["data"])
        self.assertEqual(account["access_token"], "fresh-access")
        self.assertEqual(account["refresh_token"], "fresh-refresh")
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual(request_json.call_args_list[0].kwargs["headers"]["authorization"], "stale-access")
        self.assertEqual(request_json.call_args_list[1].kwargs["headers"]["authorization"], "valid-refresh")
        self.assertEqual(request_json.call_args_list[2].kwargs["headers"]["authorization"], "fresh-access")

    def test_tajiduo_second_402_is_not_retried_again(self):
        account = {
            "access_token": "stale-access",
            "refresh_token": "valid-refresh",
            "device_id": "HT1",
        }
        refreshed = {
            "code": 0,
            "data": {"accessToken": "fresh-access", "refreshToken": "fresh-refresh"},
        }

        with patch(
            "hsr_daily._request_json",
            side_effect=[
                HsrApiError("塔吉多接口返回 HTTP 402", status_code=402),
                refreshed,
                HsrApiError("塔吉多接口返回 HTTP 402", status_code=402),
            ],
        ) as request_json:
            with self.assertRaises(HsrApiError) as raised:
                _tajiduo_request(
                    account,
                    "/usercenter/api/v2/getGameRoles",
                    query={"gameId": "1289"},
                )

        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(request_json.call_count, 3)

    def test_fetch_nte_daily_note_sends_only_role_id(self):
        account = {
            "access_token": "fresh",
            "refresh_token": "r",
            "device_id": "HT1",
            "access_token_updated_at": int(time.time()),
        }
        payload = {"code": 0, "data": {"roleid": "116771663", "staminaValue": 100}}

        with patch("hsr_daily._request_json", return_value=payload) as request_json:
            _, note = fetch_nte_daily_note(account, {"game_uid": "116771663"})

        self.assertEqual(request_json.call_args.kwargs["query"], {"roleId": "116771663"})
        self.assertEqual(note["staminaValue"], 100)

    def test_dead_refresh_token_falls_back_to_laohu_relogin(self):
        account = {
            "access_token": "stale",
            "refresh_token": "dead",
            "device_id": "HT1",
            "laohu_token": "laohu-token",
            "laohu_user_id": "42",
            "access_token_updated_at": 1,
        }
        login_ok = {"code": 0, "data": {"accessToken": "new-a", "refreshToken": "new-r", "uid": "9"}}
        role_home = {"code": 0, "data": {"staminaValue": 1}}
        saved = []

        with patch(
            "hsr_daily._request_json",
            side_effect=[
                HsrApiError("塔吉多接口返回 HTTP 402", status_code=402),
                login_ok,
                role_home,
            ],
        ):
            account, note = fetch_nte_daily_note(account, {"game_uid": "116771663"}, saved.append)

        self.assertEqual(account["access_token"], "new-a")
        self.assertEqual(account["refresh_token"], "new-r")
        self.assertEqual(account["laohu_token"], "laohu-token")
        self.assertEqual(saved[-1]["refresh_token"], "new-r")
        self.assertEqual(note, {"staminaValue": 1})

    def test_tokens_persist_even_when_note_fetch_fails_after_refresh(self):
        account = {
            "access_token": "stale",
            "refresh_token": "ok",
            "device_id": "HT1",
            "access_token_updated_at": 1,
        }
        refreshed = {"code": 0, "data": {"accessToken": "fresh-a", "refreshToken": "fresh-r"}}
        saved = []

        with patch(
            "hsr_daily._request_json",
            side_effect=[refreshed, HsrApiError("连接塔吉多失败：timeout")],
        ):
            with self.assertRaises(HsrApiError):
                fetch_nte_daily_note(account, {"game_uid": "116771663"}, saved.append)

        self.assertEqual(saved[-1]["access_token"], "fresh-a")
        self.assertEqual(saved[-1]["refresh_token"], "fresh-r")

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
        self.assertEqual(parse_commission_command("/委托绑定 异环 116771663"), ("bind_game", "nte:116771663"))
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
        self.assertIn("今天这关已经通过了，可以稍微休息一下。", text)

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
        self.assertIn("活跃度：100/100，已完成", text)
        self.assertNotIn("今日活跃", text)

    def test_format_nte_status_reads_fields_case_insensitively(self):
        role = {"nickname": "塔吉多", "game_uid": "116771663"}
        note = {
            "roleId": "116771663",
            "roleName": "塔吉多",
            "StaminaValue": 160,
            "staminamaxvalue": 240,
            "cityStaminaValue": 60,
            "cityStaminaMaxValue": 100,
            "dayValue": 80,
        }

        text = format_note_status(GAME_KEY_NTE, role, note)

        self.assertIn("本性像素：160/240", text)
        self.assertIn("都市活力：60/100", text)
        self.assertIn("活跃度：80/100，未完成", text)
        self.assertNotIn("和绑定 UID 不一致", text)

    def test_nte_reminder_reasons(self):
        note = {"dayvalue": 80, "citystaminaValue": 10, "citystaminaMaxValue": 100}

        self.assertEqual(nte_reminder_reasons(note), ["活跃度还没到 100（当前 80/100）"])
        self.assertEqual(
            nte_reminder_reasons(note, check_city_stamina=True),
            ["活跃度还没到 100（当前 80/100）", "都市活力还没清完（当前 10/100）"],
        )
        self.assertEqual(
            nte_reminder_reasons({"dayvalue": 100, "citystaminaValue": 10}, check_city_stamina=True),
            ["都市活力还没清完（当前 10/100）"],
        )

    def test_select_nte_role_requires_uid_when_multiple_roles(self):
        roles = [
            {"game_uid": "111", "nickname": "角色一"},
            {"game_uid": "222", "nickname": "角色二"},
        ]

        role = select_nte_role(roles, "222")

        self.assertEqual(role["game_uid"], "222")
        with self.assertRaises(HsrApiError):
            select_nte_role(roles)

    def test_resolve_binding_path_migrates_old_plugin_data(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_dir = root / "plugin"
            home_dir = root / "home"
            old_path = plugin_dir / "data" / "bindings.json"
            old_path.parent.mkdir(parents=True)
            old_data = {"users": {"123": {"games": {}}}}
            old_path.write_text(json.dumps(old_data), encoding="utf-8")

            new_path = resolve_binding_path(plugin_dir, home_dir)
            self.assertEqual(new_path, home_dir / ".astrbot_eryou_daily" / "bindings.json")
            self.assertEqual(json.loads(new_path.read_text(encoding="utf-8")), old_data)

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
