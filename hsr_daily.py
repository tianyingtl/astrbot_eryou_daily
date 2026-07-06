from __future__ import annotations

import hashlib
import json
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GAME_KEY_HSR = "hkrpg"
GAMES = {
    GAME_KEY_HSR: {
        "name": "崩坏：星穹铁道",
        "short_name": "星铁",
        "game_biz": "hkrpg_cn",
    }
}
GAME_ALIASES = {
    "星铁": GAME_KEY_HSR,
    "崩铁": GAME_KEY_HSR,
    "星穹铁道": GAME_KEY_HSR,
    "崩坏星穹铁道": GAME_KEY_HSR,
    "崩坏：星穹铁道": GAME_KEY_HSR,
    "hkrpg": GAME_KEY_HSR,
    "starrail": GAME_KEY_HSR,
}

BINDING_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
NOTE_URL = "https://api-takumi-record.mihoyo.com/game_record/app/hkrpg/api/note"
QR_CREATE_URL = "https://passport-api.miyoushe.com/account/ma-cn-passport/web/createQRLogin"
QR_QUERY_URL = "https://passport-api.miyoushe.com/account/ma-cn-passport/web/queryQRLoginStatus"

APP_VERSION = "2.71.1"
QR_APP_ID = "bll8iq97cem8"
DS_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
DEVICE_ID = "AFA5DBD7-D027-402B-9522-1D9A4A5EFB85"
DEVICE_FP = "38d7f349e93d8"
TIMEOUT_SECONDS = 8


class HsrApiError(RuntimeError):
    pass


class BindingStore:
    def __init__(self, path: Path):
        self.path = path

    def get_account_cookie(self, sender_key: str) -> str | None:
        account = self._get_user(sender_key).get("account") or {}
        cookie = account.get("cookie")
        return str(cookie) if cookie else None

    def set_account_cookie(self, sender_key: str, cookie: str) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        user["account"] = {"provider": "mihoyo", "cookie": cookie}
        self._save(data)

    def get_game_binding(self, sender_key: str, game_key: str) -> dict[str, Any] | None:
        return self._get_user(sender_key).get("games", {}).get(game_key)

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
    ) -> None:
        data = self._load()
        user = data.setdefault("users", {}).setdefault(sender_key, {})
        reminders = user.setdefault("reminders", [])
        new_reminder = {
            "group_id": str(group_id),
            "group_umo": group_umo,
            "game": game_key,
            "time": reminder_time,
            "last_reminded_date": "",
        }
        for index, reminder in enumerate(reminders):
            if reminder.get("group_id") == str(group_id) and reminder.get("game") == game_key:
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


def parse_commission_command(message: str) -> tuple[str, str] | None:
    text = (message or "").strip()
    for prefix in ("/委托", "／委托"):
        if text == prefix:
            return ("check", GAME_KEY_HSR)
        if not text.startswith(prefix):
            continue

        rest = text[len(prefix):].strip()
        if not rest:
            return ("check", GAME_KEY_HSR)
        if rest.lower() == "help" or rest == "帮助":
            return ("help", "")
        if rest.startswith("设置"):
            return ("reminder_set", rest[len("设置"):].strip())
        if rest == "绑定":
            return ("bind_game_menu", "")
        if rest.startswith("绑定"):
            game_text = rest[len("绑定"):].strip()
            game_key = resolve_game_key(game_text)
            return ("bind_game", game_key or game_text)
        if rest.startswith("扫码"):
            game_text = rest[len("扫码"):].strip()
            return ("qr", resolve_game_key(game_text) or "")
        if rest.startswith("手机号"):
            return ("phone", rest[len("手机号"):].strip())
        if rest == "确认":
            return ("confirm", "")
        if rest == "解绑":
            return ("unbind", "")
        return ("unknown", rest)
    return None


def parse_reminder_value(value: str) -> tuple[str | None, str | None, str | None]:
    parts = (value or "").split()
    if len(parts) != 2:
        return None, None, format_reminder_usage()

    game_key = resolve_game_key(parts[0])
    if not game_key:
        return None, None, "暂时只支持星铁。正确格式：/委托设置 星铁 20:00"

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


def format_help() -> str:
    return "\n".join(
        [
            "二游每日检查：",
            "/委托：检查崩坏：星穹铁道今日每日实训",
            "/委托绑定：选择要绑定的游戏",
            "/委托绑定 星铁：直接选择崩坏：星穹铁道",
            "/委托扫码：使用米游社 App 扫码登录",
            "/委托确认：扫码确认后完成绑定",
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
            "",
            "说明：绑定的是米游社账号。以后同一米游社账号支持更多米家游戏时，不需要重复登录。",
        ]
    )


def format_login_menu(game_key: str) -> str:
    return "\n".join(
        [
            f"已选择：{game_name(game_key)}",
            "请选择登录方式：",
            "/委托扫码：推荐，用米游社 App 扫码确认",
            "/委托手机号：查看手机号登录说明",
        ]
    )


def format_phone_login_notice() -> str:
    return "\n".join(
        [
            "手机号登录暂不在公开版里直接接收验证码。",
            "原因：米游社手机号登录通常需要极验人机验证，机器人直接收手机号和验证码不安全也不稳定。",
            "请使用推荐方式：/委托扫码",
        ]
    )


def format_not_bound() -> str:
    return "\n".join(
        [
            "你还没有绑定星铁。",
            "请私聊机器人发送 /委托绑定，然后按提示选择 星铁 -> 扫码 -> 确认。",
        ]
    )


def format_reminder_usage() -> str:
    return "\n".join(
        [
            "格式不对。正确格式：/委托设置 游戏名 时间",
            "示例：/委托设置 星铁 20:00",
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


def get_hsr_roles(cookie: str) -> list[dict[str, Any]]:
    payload = _api_get(BINDING_URL, cookie, {"game_biz": "hkrpg_cn"}, with_ds=False)
    data = _unwrap_payload(payload)
    roles = data.get("list") or []
    return [role for role in roles if role.get("game_biz") in {None, "hkrpg_cn"}]


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


def fetch_hsr_daily_note(cookie: str, role: dict[str, Any]) -> dict[str, Any]:
    role_id = role.get("game_uid") or role.get("uid")
    server = role.get("region")
    if not role_id or not server:
        raise HsrApiError("绑定的角色信息不完整，请重新绑定。")

    payload = _api_get(NOTE_URL, cookie, {"server": server, "role_id": role_id}, with_ds=True)
    return _unwrap_payload(payload)


def format_note_status(role: dict[str, Any], note: dict[str, Any]) -> str:
    nickname = role.get("nickname") or "未知角色"
    uid = role.get("game_uid") or role.get("uid") or "未知 UID"

    current_train = _first_int(note, "current_train_score")
    max_train = _first_int(note, "max_train_score") or 500
    train_done = is_train_done(note)

    lines = [
        "星铁今日委托检查",
        f"账号：{nickname}（UID {uid}）",
        f"每日实训：{_score_text(current_train)}/{max_train}，{'已完成' if train_done else '未完成'}",
    ]

    if train_done:
        lines.append("今天的每日已经 Clear。")
    else:
        lines.append("还没 Clear：每日实训还没满。")

    return "\n".join(lines)


def is_train_done(note: dict[str, Any]) -> bool:
    current_train = _first_int(note, "current_train_score")
    max_train = _first_int(note, "max_train_score") or 500
    return current_train is not None and current_train >= max_train


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


def _score_text(value: int | None) -> str:
    return "未知" if value is None else str(value)
