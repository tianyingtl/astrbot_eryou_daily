from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import time
import uuid
from base64 import b64encode
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GAME_KEY_HSR = "hkrpg"
GAME_KEY_GENSHIN = "genshin"
GAME_KEY_ZZZ = "zzz"
GAME_KEY_NTE = "nte"
GAMES = {
    GAME_KEY_HSR: {
        "name": "崩坏：星穹铁道",
        "short_name": "星铁",
        "game_biz": "hkrpg_cn",
    },
    GAME_KEY_GENSHIN: {
        "name": "原神",
        "short_name": "原神",
        "game_biz": "hk4e_cn",
    },
    GAME_KEY_ZZZ: {
        "name": "绝区零",
        "short_name": "绝区零",
        "game_biz": "nap_cn",
    },
    GAME_KEY_NTE: {
        "name": "异环",
        "short_name": "异环",
        "provider": "tajiduo",
        "game_id": "1289",
    },
}
GAME_ALIASES = {
    "星铁": GAME_KEY_HSR,
    "崩铁": GAME_KEY_HSR,
    "星穹铁道": GAME_KEY_HSR,
    "崩坏星穹铁道": GAME_KEY_HSR,
    "崩坏：星穹铁道": GAME_KEY_HSR,
    "hkrpg": GAME_KEY_HSR,
    "starrail": GAME_KEY_HSR,
    "原神": GAME_KEY_GENSHIN,
    "genshin": GAME_KEY_GENSHIN,
    "yuanshen": GAME_KEY_GENSHIN,
    "ys": GAME_KEY_GENSHIN,
    "绝区零": GAME_KEY_ZZZ,
    "绝区": GAME_KEY_ZZZ,
    "zzz": GAME_KEY_ZZZ,
    "zenless": GAME_KEY_ZZZ,
    "zenlesszonezero": GAME_KEY_ZZZ,
    "nap": GAME_KEY_ZZZ,
    "异环": GAME_KEY_NTE,
    "yihuan": GAME_KEY_NTE,
    "yh": GAME_KEY_NTE,
    "nte": GAME_KEY_NTE,
    "nevernesstoeverness": GAME_KEY_NTE,
}

BINDING_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
HSR_NOTE_URL = "https://api-takumi-record.mihoyo.com/game_record/app/hkrpg/api/note"
GENSHIN_NOTE_URL = "https://api-takumi-record.mihoyo.com/game_record/app/genshin/api/dailyNote"
ZZZ_NOTE_URL = "https://api-takumi-record.mihoyo.com/event/game_record_zzz/api/zzz/note"
NOTE_URLS = {
    GAME_KEY_HSR: HSR_NOTE_URL,
    GAME_KEY_GENSHIN: GENSHIN_NOTE_URL,
    GAME_KEY_ZZZ: ZZZ_NOTE_URL,
}
QR_CREATE_URL = "https://passport-api.miyoushe.com/account/ma-cn-passport/web/createQRLogin"
QR_QUERY_URL = "https://passport-api.miyoushe.com/account/ma-cn-passport/web/queryQRLoginStatus"

LAOHU_BASE_URL = "https://user.laohu.com"
LAOHU_APP_ID = 10550
LAOHU_APP_KEY = "89155cc4e8634ec5b1b6364013b23e3e"
LAOHU_SDK_VERSION = "4.273.0"
LAOHU_PACKAGE = "com.pwrd.htassistant"
LAOHU_VERSION_CODE = 12
TAJIDUO_BASE_URL = "https://bbs-api.tajiduo.com"
TAJIDUO_USER_CENTER_APP_ID = "10551"
TAJIDUO_APP_VERSION = "1.2.5"
TAJIDUO_CLIENT_UID = "0"
TAJIDUO_DS_SALT = "pUds3dfMkl"
TAJIDUO_DS_NONCE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

APP_VERSION = "2.71.1"
QR_APP_ID = "bll8iq97cem8"
DS_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
DEVICE_ID = "AFA5DBD7-D027-402B-9522-1D9A4A5EFB85"
DEVICE_FP = "38d7f349e93d8"
TIMEOUT_SECONDS = 8


class HsrApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BindingStore:
    def __init__(self, path: Path):
        self.path = path

    def get_account_cookie(self, sender_key: str) -> str | None:
        user = self._get_user(sender_key)
        accounts = user.get("accounts") or {}
        account = accounts.get("mihoyo") or user.get("account") or {}
        cookie = account.get("cookie")
        return str(cookie) if cookie else None

    def set_account_cookie(self, sender_key: str, cookie: str) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        user.setdefault("accounts", {})["mihoyo"] = {"provider": "mihoyo", "cookie": cookie}
        user["account"] = {"provider": "mihoyo", "cookie": cookie}
        self._save(data)

    def get_tajiduo_account(self, sender_key: str) -> dict[str, Any] | None:
        user = self._get_user(sender_key)
        accounts = user.get("accounts") or {}
        account = accounts.get("tajiduo")
        if not account and (user.get("account") or {}).get("provider") == "tajiduo":
            account = user.get("account")
        return dict(account) if account else None

    def set_tajiduo_account(self, sender_key: str, account: dict[str, Any]) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        saved = dict(account)
        saved["provider"] = "tajiduo"
        user.setdefault("accounts", {})["tajiduo"] = saved
        self._save(data)

    def set_tajiduo_binding(self, sender_key: str, account: dict[str, Any], role: dict[str, Any]) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        saved = dict(account)
        saved["provider"] = "tajiduo"
        user.setdefault("accounts", {})["tajiduo"] = saved
        user.setdefault("games", {})[GAME_KEY_NTE] = {"role": role}
        self._save(data)

    def get_game_binding(self, sender_key: str, game_key: str) -> dict[str, Any] | None:
        return self._get_user(sender_key).get("games", {}).get(game_key)

    def get_game_bindings(self, sender_key: str) -> dict[str, dict[str, Any]]:
        return self._get_user(sender_key).get("games", {})

    def set_game_binding(self, sender_key: str, game_key: str, role: dict[str, Any]) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        user.setdefault("games", {})[game_key] = {"role": role}
        self._save(data)

    def get_reminders(self) -> list[tuple[str, dict[str, Any]]]:
        data = self._load()
        reminders = []
        for sender_key, user in data.get("users", {}).items():
            for reminder in user.get("reminders", []):
                reminders.append((sender_key, reminder))
        return reminders

    def set_reminder(
        self,
        sender_key: str,
        group_id: str,
        group_umo: str,
        game_key: str,
        reminder_time: str,
        last_reminded_date: str = "",
    ) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        reminders = user.setdefault("reminders", [])
        new_reminder = {
            "group_id": str(group_id),
            "group_umo": group_umo,
            "game": game_key,
            "time": reminder_time,
            "last_reminded_date": last_reminded_date,
        }
        for index, reminder in enumerate(reminders):
            if reminder.get("group_id") == str(group_id) and reminder.get("game") == game_key:
                if not last_reminded_date:
                    new_reminder["last_reminded_date"] = reminder.get("last_reminded_date", "")
                reminders[index] = new_reminder
                break
        else:
            reminders.append(new_reminder)
        self._save(data)

    def mark_reminded(
        self,
        sender_key: str,
        group_id: str,
        game_key: str,
        reminder_date: str,
    ) -> None:
        data = self._load()
        reminders = data.setdefault("users", {}).setdefault(sender_key, {}).setdefault("reminders", [])
        for reminder in reminders:
            if reminder.get("group_id") == str(group_id) and reminder.get("game") == game_key:
                reminder["last_reminded_date"] = reminder_date
                break
        self._save(data)

    def delete_user(self, sender_key: str) -> bool:
        data = self._load()
        existed = False
        if sender_key in data.get("users", {}):
            del data["users"][sender_key]
            existed = True
        if sender_key in data.get("bindings", {}):
            del data["bindings"][sender_key]
            existed = True
        if existed:
            self._save(data)
        return existed

    def get_pending(self, sender_key: str) -> dict[str, Any] | None:
        return self._get_user(sender_key).get("pending")

    def set_pending(self, sender_key: str, pending: dict[str, Any]) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        user["pending"] = pending
        self._save(data)

    def delete_pending(self, sender_key: str) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        user.pop("pending", None)
        self._save(data)

    def _get_user(self, sender_key: str) -> dict[str, Any]:
        data = self._load()
        user = data.get("users", {}).get(sender_key)
        if user:
            return user

        old_binding = data.get("bindings", {}).get(sender_key)
        if old_binding:
            return {
                "account": {"provider": "mihoyo", "cookie": old_binding.get("cookie", "")},
                "games": {GAME_KEY_HSR: {"role": old_binding.get("role", {})}},
            }
        return {}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}}
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)


def resolve_binding_path(plugin_dir: Path, home_dir: Path | None = None) -> Path:
    old_path = plugin_dir / "data" / "bindings.json"
    home = Path(home_dir) if home_dir is not None else Path.home()
    new_path = home / ".astrbot_eryou_daily" / "bindings.json"

    if old_path.exists() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, new_path)
    return new_path


def parse_commission_command(message: str) -> tuple[str, str] | None:
    text = (message or "").strip()
    for prefix in ("/委托", "／委托"):
        if text == prefix:
            return ("check", "")
        if not text.startswith(prefix):
            continue

        rest = text[len(prefix):].strip()
        if not rest:
            return ("check", "")
        if rest.lower() == "help" or rest == "帮助":
            return ("help", "")
        if rest.startswith("设置"):
            return ("reminder_set", rest[len("设置"):].strip())
        if rest == "绑定":
            return ("bind_game_menu", "")
        if rest.startswith("绑定"):
            game_text = rest[len("绑定"):].strip()
            game_parts = game_text.split()
            game_key = resolve_game_key(game_parts[0] if game_parts else game_text)
            if game_key == GAME_KEY_NTE and len(game_parts) >= 2 and game_parts[1].isdigit():
                return ("bind_game", f"{game_key}:{game_parts[1]}")
            return ("bind_game", game_key or game_text)
        if rest.startswith("扫码"):
            game_text = rest[len("扫码"):].strip()
            return ("qr", resolve_game_key(game_text) or "")
        if rest.startswith("发码"):
            return ("sms", rest[len("发码"):].strip())
        if rest.startswith("确认"):
            return ("confirm", rest[len("确认"):].strip())
        if rest == "解绑":
            return ("unbind", "")
        game_key = resolve_game_key(rest)
        if game_key:
            return ("check", game_key)
        return ("unknown", rest)
    return None


def parse_reminder_value(value: str) -> tuple[str | None, str | None, str | None]:
    parts = (value or "").split()
    if len(parts) != 2:
        return None, None, format_reminder_usage()

    game_key = resolve_game_key(parts[0])
    if not game_key:
        return None, None, f"当前支持设置提醒：{supported_game_text()}。正确格式：/委托设置 星铁 20:00"

    match = re.fullmatch(r"([01]?\d|2[0-3])[:：]([0-5]\d)", parts[1])
    if not match:
        return None, None, format_reminder_usage()

    hour = int(match.group(1))
    minute = int(match.group(2))
    return game_key, f"{hour:02d}:{minute:02d}", None


def resolve_game_key(text: str) -> str | None:
    normalized = re.sub(r"[\s:：_-]+", "", (text or "").lower())
    return GAME_ALIASES.get(normalized)


def game_name(game_key: str) -> str:
    return GAMES.get(game_key, {}).get("name", game_key)


def supported_game_text() -> str:
    return "、".join(game["short_name"] for game in GAMES.values())


def is_supported_game(game_key: str) -> bool:
    return game_key in GAMES


def game_provider(game_key: str) -> str:
    return str(GAMES.get(game_key, {}).get("provider", "mihoyo"))


def supports_mihoyo_login(game_key: str) -> bool:
    return game_provider(game_key) == "mihoyo"


def supports_tajiduo_login(game_key: str) -> bool:
    return game_provider(game_key) == "tajiduo"


def format_help() -> str:
    return "\n".join(
        [
            "二游每日检查：",
            "/委托：检查已绑定游戏的今日每日状态",
            "/委托 星铁/原神/绝区零/异环：检查指定游戏",
            "/委托绑定：选择要绑定的游戏",
            "/委托绑定 星铁/原神/绝区零：直接发送米游社登录二维码",
            "/委托绑定 异环：使用塔吉多手机号短信登录",
            "/委托绑定 异环 UID：绑定指定异环角色",
            "/委托发码 手机号：异环发送短信验证码",
            "/委托扫码：二维码过期时重新生成",
            "/委托确认：米游社扫码确认后完成绑定",
            "/委托确认 验证码：异环短信验证后完成绑定",
            "/委托设置 星铁 20:00：到点未完成时在本群提醒你",
            "/委托解绑：删除本地绑定",
        ]
    )


def format_group_bind_guide() -> str:
    return "\n".join(
        [
            "绑定说明已经私聊发送给你了。",
            "如果没有收到，请先私聊机器人发送 /委托绑定。",
        ]
    )


def format_game_menu() -> str:
    return "\n".join(
        [
            "请选择要绑定的游戏：",
            "/委托绑定 星铁",
            "/委托绑定 原神",
            "/委托绑定 绝区零",
            "/委托绑定 异环",
            "/委托绑定 异环 UID",
            "",
            "米家游戏会私聊米游社登录二维码。",
            "异环会走塔吉多手机号短信登录；有多个角色时请带 UID。",
        ]
    )


def format_nte_bind_guide() -> str:
    return "\n".join(
        [
            "异环绑定使用塔吉多账号手机号短信登录。",
            "请在私聊里发送：/委托发码 手机号",
            "收到短信验证码后发送：/委托确认 验证码",
            "示例：/委托发码 13800138000",
            "如果有多个角色，可以先在群里发送：/委托绑定 异环 UID",
            "说明：手机号和验证码只用于本次登录，插件会保存塔吉多 token 到用户目录下的 .astrbot_eryou_daily。",
        ]
    )


def format_not_bound() -> str:
    return "\n".join(
        [
            "你还没有绑定游戏。",
            "请私聊机器人发送 /委托绑定，然后按提示选择游戏并完成登录。",
        ]
    )


def format_reminder_usage() -> str:
    return "\n".join(
        [
            "格式不对。正确格式：/委托设置 游戏名 时间",
            "示例：/委托设置 星铁 20:00",
            f"支持游戏：{supported_game_text()}。",
            "时间格式：24小时制 HH:MM，例如 08:30、20:00。",
        ]
    )


def create_qr_login() -> dict[str, str]:
    device_id = str(uuid.uuid4()).upper()
    payload, _ = _api_post_json(
        QR_CREATE_URL,
        {},
        _qr_headers(device_id),
    )
    data = _unwrap_payload(payload)
    url = data.get("url")
    ticket = data.get("ticket")
    if not url or not ticket:
        raise HsrApiError("二维码接口返回格式异常。")
    return {"url": str(url), "ticket": str(ticket), "device_id": device_id}


def save_qr_image(url: str, image_path: Path) -> None:
    import qrcode

    image_path.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url)
    image.save(image_path)


def get_login_cookie_by_qr(ticket: str, device_id: str) -> str:
    payload, set_cookie_lines = _api_post_json(
        QR_QUERY_URL,
        {"ticket": ticket},
        _qr_headers(device_id),
    )
    data = _unwrap_payload(payload)
    status = data.get("status")
    if status == "Created":
        raise HsrApiError("二维码还未扫描，请扫码后再发送 /委托确认。")
    if status == "Scanned":
        raise HsrApiError("二维码已扫描，请在米游社 App 内确认登录后再发送 /委托确认。")
    if status != "Confirmed":
        raise HsrApiError(f"未知扫码状态：{status}")

    cookie = _cookie_from_set_cookie(set_cookie_lines)
    if not cookie:
        raise HsrApiError("扫码已确认，但没有拿到登录 Cookie。")
    return cookie


def make_nte_device_id() -> str:
    return f"HT{uuid.uuid4().hex[:14].upper()}"


def send_nte_sms_code(mobile: str, device_id: str) -> None:
    params = _laohu_common_fields(device_id, use_millis=False)
    params.update({"cellphone": mobile, "areaCodeId": "1", "type": "16"})
    _laohu_submit("/m/newApi/sendPhoneCaptchaWithOutLogin", params)


def login_nte_by_sms(mobile: str, code: str, device_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    laohu = _laohu_login_by_sms(mobile, code, device_id)
    account = _tajiduo_user_center_login(laohu["token"], laohu["user_id"], device_id)
    return get_nte_roles(account)


def bind_nte_by_sms(
    mobile: str,
    code: str,
    device_id: str,
    target_uid: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    account, roles = login_nte_by_sms(mobile, code, device_id)
    role = select_nte_role(roles, target_uid)
    if not role:
        raise HsrApiError("这个塔吉多账号没有找到异环角色。")
    return account, role


def get_nte_roles(account: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    account = ensure_tajiduo_account(account)
    game_id = GAMES[GAME_KEY_NTE]["game_id"]
    data = _tajiduo_request(
        account,
        "/usercenter/api/v2/getGameRoles",
        query={"gameId": game_id},
    )

    if isinstance(data, list):
        raw_roles = data
    elif isinstance(data, dict):
        raw_roles = data.get("roles") or []
    else:
        raw_roles = []

    roles = [_format_nte_role(role) for role in raw_roles if isinstance(role, dict)]
    if roles:
        return account, roles

    bind_role = _tajiduo_request(
        account,
        "/apihub/api/getGameBindRole",
        query={"uid": account.get("center_uid", ""), "gameId": game_id},
    )
    if isinstance(bind_role, dict):
        role = _format_nte_role(bind_role)
        if role.get("game_uid"):
            roles.append(role)
    return account, roles


def fetch_nte_daily_note(
    account: dict[str, Any],
    role: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    role_id = role.get("game_uid") or role.get("uid") or role.get("role_id")
    if not role_id:
        raise HsrApiError("绑定的异环角色信息不完整，请重新绑定。")
    account = ensure_tajiduo_account(account)
    note = _tajiduo_request(
        account,
        "/apihub/awapi/yh/roleHome",
        query={"roleId": str(role_id), "_t": int(time.time())},
    )
    if not isinstance(note, dict):
        raise HsrApiError("异环接口返回格式异常。")
    return account, note


def ensure_tajiduo_account(account: dict[str, Any]) -> dict[str, Any]:
    account = dict(account or {})
    access_token = str(account.get("access_token") or "")
    updated_at = int(account.get("access_token_updated_at") or 0)
    if access_token and time.time() - updated_at < 3300:
        return account

    return _refresh_tajiduo_account(account)


def _refresh_tajiduo_account(account: dict[str, Any]) -> dict[str, Any]:
    account = dict(account or {})

    refresh_token = str(account.get("refresh_token") or "")
    if not refresh_token:
        raise HsrApiError("塔吉多登录态已过期，请重新绑定异环。")

    headers = _tajiduo_headers(str(account.get("device_id") or make_nte_device_id()), refresh_token)
    data = _tajiduo_extract(
        _request_json(
            TAJIDUO_BASE_URL,
            "/usercenter/api/refreshToken",
            method="POST",
            headers=headers,
            error_prefix="塔吉多",
        ),
        "/usercenter/api/refreshToken",
    )
    access_token = str(data.get("accessToken") or "") if isinstance(data, dict) else ""
    new_refresh = str(data.get("refreshToken") or "") if isinstance(data, dict) else ""
    if not access_token or not new_refresh:
        raise HsrApiError("塔吉多刷新登录态失败，请重新绑定异环。")

    account["access_token"] = access_token
    account["refresh_token"] = new_refresh
    account["access_token_updated_at"] = int(time.time())
    return account


def get_game_roles(cookie: str, game_key: str) -> list[dict[str, Any]]:
    game = GAMES.get(game_key)
    if not game:
        raise HsrApiError("当前版本不支持这个游戏。")
    if not supports_mihoyo_login(game_key):
        raise HsrApiError(f"{game_name(game_key)}不使用米游社账号绑定。")

    game_biz = game["game_biz"]
    payload = _api_get(BINDING_URL, cookie, {"game_biz": game_biz}, with_ds=False)
    data = _unwrap_payload(payload)
    roles = data.get("list") or []
    return [role for role in roles if role.get("game_biz") in {None, game_biz}]


def get_hsr_roles(cookie: str) -> list[dict[str, Any]]:
    return get_game_roles(cookie, GAME_KEY_HSR)


def select_default_role(roles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not roles:
        return None
    role = roles[0]
    return {
        "game_uid": role.get("game_uid") or role.get("uid"),
        "region": role.get("region"),
        "nickname": role.get("nickname"),
        "level": role.get("level"),
    }


def select_nte_role(roles: list[dict[str, Any]], target_uid: str = "") -> dict[str, Any] | None:
    if not roles:
        return None

    target_uid = str(target_uid or "").strip()
    if target_uid:
        for role in roles:
            role_uid = str(role.get("game_uid") or role.get("uid") or "")
            if role_uid == target_uid:
                return select_default_role([role])
        raise HsrApiError(
            f"没有找到 UID {target_uid} 的异环角色。可用角色：{_role_list_text(roles)}"
        )

    if len(roles) == 1:
        return select_default_role(roles)

    raise HsrApiError(
        "这个塔吉多账号下有多个异环角色，请用 /委托绑定 异环 UID 指定。"
        f"可用角色：{_role_list_text(roles)}"
    )


def _role_list_text(roles: list[dict[str, Any]]) -> str:
    items = []
    for role in roles:
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        items.append(f"{nickname}（UID {uid}）")
    return "、".join(items) if items else "无"


def fetch_hsr_daily_note(cookie: str, role: dict[str, Any]) -> dict[str, Any]:
    return fetch_daily_note(cookie, role, GAME_KEY_HSR)


def fetch_daily_note(cookie: str, role: dict[str, Any], game_key: str) -> dict[str, Any]:
    role_id = role.get("game_uid") or role.get("uid")
    server = role.get("region")
    if not role_id or not server:
        raise HsrApiError("绑定的角色信息不完整，请重新绑定。")
    url = NOTE_URLS.get(game_key)
    if not url:
        raise HsrApiError("当前版本不支持这个游戏。")

    payload = _api_get(url, cookie, {"server": server, "role_id": role_id}, with_ds=True)
    return _unwrap_payload(payload)


def format_note_status(game_key: str, role: dict[str, Any], note: dict[str, Any]) -> str:
    if game_key == GAME_KEY_NTE:
        return _format_nte_status(role, note)
    if game_key == GAME_KEY_GENSHIN:
        return _format_genshin_status(role, note)
    if game_key == GAME_KEY_ZZZ:
        return _format_zzz_status(role, note)
    return _format_hsr_status(role, note)


def is_daily_done(game_key: str, note: dict[str, Any]) -> bool:
    if game_key == GAME_KEY_NTE:
        return is_nte_done(note)
    if game_key == GAME_KEY_GENSHIN:
        return is_genshin_done(note)
    if game_key == GAME_KEY_ZZZ:
        return is_zzz_done(note)
    return is_train_done(note)


def daily_missing_text(game_key: str) -> str:
    if game_key == GAME_KEY_NTE:
        return "活跃度还没到 100"
    if game_key == GAME_KEY_GENSHIN:
        return "每日委托还没完成或奖励未领取"
    if game_key == GAME_KEY_ZZZ:
        return "今日活跃还没完成"
    return "每日实训还没完成"


def _format_hsr_status(role: dict[str, Any], note: dict[str, Any]) -> str:
    nickname = role.get("nickname") or "未知角色"
    uid = role.get("game_uid") or role.get("uid") or "未知 UID"

    current_train = _first_int(note, "current_train_score")
    max_train = _first_int(note, "max_train_score") or 500
    current_stamina = _first_int(note, "current_stamina", "stamina")
    max_stamina = _first_int(note, "max_stamina", "stamina_max") or 240
    reserve_stamina = _first_int(note, "current_reserve_stamina", "reserve_stamina")
    train_done = is_train_done(note)

    lines = [
        "娜娜米提醒：星铁今日委托检查",
        f"账号：{nickname}（UID {uid}）",
        f"开拓力：{_score_text(current_stamina)}/{max_stamina}",
        f"每日实训：{_score_text(current_train)}/{max_train}，{'已完成' if train_done else '未完成'}",
    ]
    if reserve_stamina is not None:
        lines.append(f"后备开拓力：{reserve_stamina}")

    if train_done:
        lines.append("今天这关已经通过了，可以稍微休息一下。")
    else:
        lines.append("今天这关还没过：每日实训还没满，先补一下比较稳。")

    return "\n".join(lines)


def _format_genshin_status(role: dict[str, Any], note: dict[str, Any]) -> str:
    nickname = role.get("nickname") or "未知角色"
    uid = role.get("game_uid") or role.get("uid") or "未知 UID"

    current = _first_int(note, "current_commission_num")
    max_count = _first_int(note, "max_commission_num") or 4
    current_resin = _first_int(note, "current_resin")
    max_resin = _first_int(note, "max_resin") or 200
    reward_received = bool(note.get("is_extra_task_reward_received"))
    done = is_genshin_done(note)

    lines = [
        "娜娜米提醒：原神今日委托检查",
        f"账号：{nickname}（UID {uid}）",
        f"原粹树脂：{_score_text(current_resin)}/{max_resin}",
        f"每日委托：{_score_text(current)}/{max_count}，{'已完成' if current is not None and current >= max_count else '未完成'}",
        f"凯瑟琳奖励：{'已领取' if reward_received else '未领取'}",
    ]

    if done:
        lines.append("今天这关已经通过了，可以稍微休息一下。")
    else:
        misses = []
        if current is None or current < max_count:
            misses.append("每日委托还没满")
        if not reward_received:
            misses.append("凯瑟琳奖励未领取")
        lines.append("今天这关还没过：" + "；".join(misses) + "。先补一下比较稳。")

    return "\n".join(lines)


def _format_zzz_status(role: dict[str, Any], note: dict[str, Any]) -> str:
    nickname = role.get("nickname") or "未知角色"
    uid = role.get("game_uid") or role.get("uid") or "未知 UID"

    current, max_count = _zzz_vitality(note)
    energy_current, energy_max = _zzz_energy(note)
    card_sign = _zzz_card_sign_text(note)
    done = is_zzz_done(note)

    lines = [
        "娜娜米提醒：绝区零今日委托检查",
        f"账号：{nickname}（UID {uid}）",
        f"电量：{_score_text(energy_current)}/{energy_max}",
        f"今日活跃：{_score_text(current)}/{max_count}，{'已完成' if done else '未完成'}",
    ]
    if card_sign:
        lines.append(f"刮刮卡：{card_sign}")

    if done:
        lines.append("今天这关已经通过了，可以稍微休息一下。")
    else:
        lines.append("今天这关还没过：今日活跃还没满，先补一下比较稳。")

    return "\n".join(lines)


def _format_nte_status(role: dict[str, Any], note: dict[str, Any]) -> str:
    bound_name = role.get("nickname") or "未知角色"
    bound_uid = str(role.get("game_uid") or role.get("uid") or "未知 UID")
    nickname = note.get("rolename") or bound_name
    uid = str(note.get("roleid") or bound_uid)

    stamina = _first_int(note, "staminaValue")
    max_stamina = _first_int(note, "staminaMaxValue") or 240
    city_stamina = _first_int(note, "citystaminaValue")
    max_city_stamina = _first_int(note, "citystaminaMaxValue") or 100
    day_value = _first_int(note, "dayvalue")
    done = is_nte_done(note)

    lines = [
        "娜娜米提醒：异环今日委托检查",
        f"绑定角色：{bound_name}（UID {bound_uid}）",
    ]
    if uid != bound_uid:
        lines.append(f"接口返回角色：{nickname}（UID {uid}），和绑定 UID 不一致，请重新绑定指定 UID。")

    lines.extend([
        f"本性像素：{_score_text(stamina)}/{max_stamina}",
        f"都市活力：{_score_text(city_stamina)}/{max_city_stamina}",
        f"活跃度：{_score_text(day_value)}/100，{'已完成' if done else '未完成'}",
    ])

    if done:
        lines.append("今天这关已经通过了，可以稍微休息一下。")
    else:
        lines.append("今天这关还没过：活跃度还没到 100，先补一下比较稳。")

    return "\n".join(lines)


def is_train_done(note: dict[str, Any]) -> bool:
    current_train = _first_int(note, "current_train_score")
    max_train = _first_int(note, "max_train_score") or 500
    return current_train is not None and current_train >= max_train


def is_genshin_done(note: dict[str, Any]) -> bool:
    current = _first_int(note, "current_commission_num")
    max_count = _first_int(note, "max_commission_num") or 4
    return current is not None and current >= max_count and bool(note.get("is_extra_task_reward_received"))


def is_zzz_done(note: dict[str, Any]) -> bool:
    current, max_count = _zzz_vitality(note)
    return current is not None and current >= max_count


def is_nte_done(note: dict[str, Any]) -> bool:
    day_value = _first_int(note, "dayvalue")
    return day_value is not None and day_value >= 100


def nte_reminder_reasons(note: dict[str, Any], check_city_stamina: bool = False) -> list[str]:
    reasons = []
    day_value = _first_int(note, "dayvalue")
    if day_value is None or day_value < 100:
        reasons.append(f"活跃度还没到 100（当前 {_score_text(day_value)}/100）")

    if check_city_stamina:
        city_stamina = _first_int(note, "citystaminaValue")
        max_city_stamina = _first_int(note, "citystaminaMaxValue") or 100
        if city_stamina is not None and city_stamina > 0:
            reasons.append(f"都市活力还没清完（当前 {city_stamina}/{max_city_stamina}）")
    return reasons


def _api_get(url: str, cookie: str, params: dict[str, Any], with_ds: bool) -> dict[str, Any]:
    cookie = _normalize_cookie(cookie)
    query = urlencode(sorted(params.items()))
    request_url = f"{url}?{query}" if query else url
    headers = _headers(cookie, query if with_ds else "")
    request = Request(request_url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise HsrApiError(f"米游社接口返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise HsrApiError(f"连接米游社失败：{exc.reason}") from exc
    return json.loads(body)


def _api_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8")
            set_cookie = response.headers.get_all("Set-Cookie") or []
    except HTTPError as exc:
        raise HsrApiError(f"米游社接口返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise HsrApiError(f"连接米游社失败：{exc.reason}") from exc
    return json.loads(text), set_cookie


def _laohu_login_by_sms(mobile: str, code: str, device_id: str) -> dict[str, str]:
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
    except ImportError as exc:
        raise HsrApiError("缺少异环登录依赖 pycryptodome，请安装 requirements.txt 后重载插件。") from exc

    aes_key = LAOHU_APP_KEY[-16:].encode("utf-8")

    def encrypt(value: str) -> str:
        cipher = AES.new(aes_key, AES.MODE_ECB)
        return b64encode(cipher.encrypt(pad(value.encode("utf-8"), AES.block_size))).decode("ascii")

    _laohu_check_sms_code(mobile, code, device_id)

    params = _laohu_common_fields(device_id, use_millis=True)
    params.update(
        {
            "cellphone": encrypt(mobile),
            "captcha": encrypt(code),
            "areaCodeId": "1",
            "type": "16",
        }
    )
    data = _laohu_submit("/openApi/sms/new/login", params, keep_empty=True)
    user_id = data.get("userId") if isinstance(data, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if user_id is None or not token:
        raise HsrApiError("老虎账号登录返回格式异常。")
    return {"user_id": str(user_id), "token": str(token)}


def _laohu_check_sms_code(mobile: str, code: str, device_id: str) -> None:
    params = _laohu_common_fields(device_id, use_millis=False)
    params.update({"cellphone": mobile, "captcha": code})
    _laohu_submit("/m/newApi/checkPhoneCaptchaWithOutLogin", params)


def _laohu_common_fields(device_id: str, use_millis: bool) -> dict[str, str]:
    timestamp = int(time.time() * 1000) if use_millis else int(time.time())
    fields = {
        "appId": str(LAOHU_APP_ID),
        "channelId": "1",
        "deviceId": device_id,
        "deviceType": "Pixel 6",
        "deviceModel": "Pixel 6",
        "deviceName": "Pixel 6",
        "deviceSys": "Android 14",
        "adm": device_id,
        "idfa": "",
        "sdkVersion": LAOHU_SDK_VERSION,
        "bid": LAOHU_PACKAGE,
        "t": str(timestamp),
    }
    if use_millis:
        fields["version"] = str(LAOHU_VERSION_CODE)
        fields["mac"] = ""
    else:
        fields["versionCode"] = str(LAOHU_VERSION_CODE)
        fields["imei"] = ""
    return fields


def _laohu_submit(path: str, params: dict[str, str], keep_empty: bool = False) -> Any:
    signed = dict(params)
    raw = "".join(str(signed[key]) for key in sorted(signed)) + LAOHU_APP_KEY
    signed["sign"] = hashlib.md5(raw.encode("utf-8")).hexdigest()
    body = {key: value for key, value in signed.items() if keep_empty or value != ""}
    payload = _request_json(
        LAOHU_BASE_URL,
        path,
        method="POST",
        body=body,
        error_prefix="老虎账号",
    )
    if payload.get("code") not in {0, "0"}:
        raise HsrApiError(payload.get("message") or "老虎账号接口返回错误。")
    return payload.get("result") if payload.get("result") is not None else {}


def _tajiduo_user_center_login(laohu_token: str, laohu_user_id: str, device_id: str) -> dict[str, Any]:
    data = _tajiduo_extract(
        _request_json(
            TAJIDUO_BASE_URL,
            "/usercenter/api/login",
            method="POST",
            body={
                "token": laohu_token,
                "userIdentity": str(laohu_user_id),
                "appId": TAJIDUO_USER_CENTER_APP_ID,
            },
            headers=_tajiduo_headers(device_id, ""),
            error_prefix="塔吉多",
        ),
        "/usercenter/api/login",
    )
    if not isinstance(data, dict):
        raise HsrApiError("塔吉多登录返回格式异常。")
    access_token = str(data.get("accessToken") or "")
    refresh_token = str(data.get("refreshToken") or "")
    center_uid = str(data.get("uid") or "")
    if not access_token or not refresh_token or not center_uid:
        raise HsrApiError("塔吉多登录返回缺少 token。")
    return {
        "provider": "tajiduo",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "center_uid": center_uid,
        "device_id": device_id,
        "laohu_token": laohu_token,
        "laohu_user_id": str(laohu_user_id),
        "access_token_updated_at": int(time.time()),
    }


def _tajiduo_request(
    account: dict[str, Any],
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    method: str = "GET",
) -> Any:
    def request_once() -> Any:
        return _tajiduo_extract(
            _request_json(
                TAJIDUO_BASE_URL,
                path,
                method=method,
                query=query,
                body=body,
                headers=_tajiduo_headers(
                    str(account.get("device_id") or make_nte_device_id()),
                    str(account.get("access_token") or ""),
                ),
                error_prefix="塔吉多",
            ),
            path,
        )

    try:
        return request_once()
    except HsrApiError as exc:
        if exc.status_code not in {401, 402, 403}:
            raise

    refreshed = _refresh_tajiduo_account(account)
    account.clear()
    account.update(refreshed)
    return request_once()


def _tajiduo_headers(device_id: str, authorization: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = "".join(random.choice(TAJIDUO_DS_NONCE_ALPHABET) for _ in range(8))
    raw = f"{timestamp}{nonce}{TAJIDUO_APP_VERSION}{TAJIDUO_DS_SALT}"
    return {
        "User-Agent": "okhttp/4.12.0",
        "platform": "android",
        "deviceid": device_id,
        "appversion": TAJIDUO_APP_VERSION,
        "uid": TAJIDUO_CLIENT_UID,
        "authorization": authorization,
        "ds": f"{timestamp},{nonce},{hashlib.md5(raw.encode('utf-8')).hexdigest()}",
    }


def _tajiduo_extract(payload: dict[str, Any], path: str) -> Any:
    if payload.get("code") not in {0, "0"}:
        status = payload.get("code")
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            status_code = None
        message = payload.get("msg") or payload.get("message") or "塔吉多接口返回错误。"
        if status_code in {401, 402, 403}:
            message = f"{message}，塔吉多登录态可能已失效，请重新绑定异环。"
        raise HsrApiError(f"[{path}] {message}", status_code=status_code)
    return payload.get("data") if payload.get("data") is not None else {}


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    error_prefix: str,
) -> dict[str, Any]:
    request_url = f"{base_url}{path}"
    if query:
        request_url = f"{request_url}?{urlencode(sorted((key, str(value)) for key, value in query.items()))}"
    data = urlencode(body).encode("utf-8") if body is not None else None
    merged_headers = dict(headers or {})
    if body is not None:
        merged_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = Request(request_url, data=data, headers=merged_headers, method=method)
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        raise HsrApiError(
            f"{error_prefix}接口返回 HTTP {exc.code}",
            status_code=int(exc.code),
        ) from exc
    except URLError as exc:
        raise HsrApiError(f"连接{error_prefix}失败：{exc.reason}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HsrApiError(f"{error_prefix}接口返回非 JSON 内容。") from exc
    if not isinstance(payload, dict):
        raise HsrApiError(f"{error_prefix}接口返回格式异常。")
    return payload


def _headers(cookie: str, query: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Origin": "https://webstatic.mihoyo.com",
        "Referer": "https://webstatic.mihoyo.com/app/community-game-records/index.html?v=6",
        "User-Agent": f"Mozilla/5.0 miHoYoBBS/{APP_VERSION}",
        "X-Requested-With": "com.mihoyo.hyperion",
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": "5",
        "x-rpc-device_fp": DEVICE_FP,
        "x-rpc-device_id": DEVICE_ID,
        "x-rpc-device_model": "iPhone14,3",
        "x-rpc-device_name": "iPhone",
        "x-rpc-language": "zh-cn",
        "x-rpc-sys_version": "17.3.1",
    }
    if query:
        headers["DS"] = _make_ds(query)
    return headers


def _qr_headers(device_id: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "x-rpc-app_id": QR_APP_ID,
        "x-rpc-device_id": device_id,
    }


def _make_ds(query: str) -> str:
    timestamp = int(time.time())
    nonce = random.randint(100000, 200000)
    checksum = hashlib.md5(
        f"salt={DS_SALT}&t={timestamp}&r={nonce}&b=&q={query}".encode("utf-8")
    ).hexdigest()
    return f"{timestamp},{nonce},{checksum}"


def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retcode = payload.get("retcode", payload.get("code", 0))
    try:
        retcode_value = int(retcode)
    except (TypeError, ValueError):
        retcode_value = retcode
    if retcode_value not in {0, 200}:
        message = payload.get("message") or payload.get("msg") or "未知错误"
        if retcode_value in {-100, -101, 10001, 100}:
            message = f"{message}，Cookie 可能已失效，请重新绑定。"
        raise HsrApiError(message)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HsrApiError("米游社接口返回格式异常。")
    return data


def _normalize_cookie(cookie: str) -> str:
    cookie = (cookie or "").strip()
    if not cookie or "=" not in cookie:
        raise HsrApiError("Cookie 格式不对。")
    if "\r" in cookie or "\n" in cookie:
        raise HsrApiError("Cookie 不能包含换行。")
    return cookie


def _cookie_from_set_cookie(lines: list[str]) -> str:
    cookies = []
    for line in lines:
        first_part = line.split(";", 1)[0].strip()
        if first_part and "=" in first_part:
            cookies.append(first_part)
    return "; ".join(cookies)


def _first_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _zzz_vitality(note: dict[str, Any]) -> tuple[int | None, int]:
    vitality = note.get("vitality")
    if not isinstance(vitality, dict):
        vitality = note.get("engagement")
    if not isinstance(vitality, dict):
        vitality = {}

    current = _first_int(vitality, "current", "cur_point", "current_progress", "value")
    max_count = _first_int(vitality, "max", "max_point", "total", "target") or 400
    return current, max_count


def _zzz_energy(note: dict[str, Any]) -> tuple[int | None, int]:
    energy = note.get("energy")
    if not isinstance(energy, dict):
        energy = note.get("battery")
    if not isinstance(energy, dict):
        energy = note.get("stamina")
    if not isinstance(energy, dict):
        energy = {}

    current = _first_int(energy, "current", "cur", "value", "current_energy")
    max_count = _first_int(energy, "max", "total", "limit", "max_energy") or 240
    return current, max_count


def _format_nte_role(role: dict[str, Any]) -> dict[str, Any]:
    return {
        "game_uid": role.get("roleId") or role.get("role_id") or role.get("uid"),
        "nickname": role.get("roleName") or role.get("role_name") or role.get("nickname"),
        "level": role.get("lev") or role.get("level"),
        "game_id": GAMES[GAME_KEY_NTE]["game_id"],
    }


def _zzz_card_sign_text(note: dict[str, Any]) -> str:
    value = note.get("card_sign")
    if isinstance(value, dict):
        value = value.get("status") or value.get("state") or value.get("done")
    if value is True:
        return "已刮"
    if value is False:
        return "未刮"
    text = str(value or "")
    if not text:
        return ""
    if text in {"CardSignDone", "Done", "Finished", "1"}:
        return "已刮"
    if text in {"CardSignNotDone", "NotDone", "0"}:
        return "未刮"
    return text


def _score_text(value: int | None) -> str:
    return "未知" if value is None else str(value)
