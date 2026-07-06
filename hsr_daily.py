from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BINDING_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
NOTE_URL = "https://api-takumi-record.mihoyo.com/game_record/app/hkrpg/api/note"
APP_VERSION = "2.71.1"
DS_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
TIMEOUT_SECONDS = 8


class HsrApiError(RuntimeError):
    pass


class BindingStore:
    def __init__(self, path: Path):
        self.path = path

    def get(self, sender_key: str) -> dict[str, Any] | None:
        return self._load().get("bindings", {}).get(sender_key)

    def set(self, sender_key: str, binding: dict[str, Any]) -> None:
        data = self._load()
        data.setdefault("bindings", {})[sender_key] = binding
        self._save(data)

    def delete(self, sender_key: str) -> bool:
        data = self._load()
        bindings = data.setdefault("bindings", {})
        if sender_key not in bindings:
            return False
        del bindings[sender_key]
        self._save(data)
        return True

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"bindings": {}}
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
            return ("check", "")
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            if not rest:
                return ("check", "")
            if rest in {"帮助", "help", "Help", "HELP"}:
                return ("help", "")
            if rest.startswith("绑定"):
                return ("bind", rest[len("绑定"):].strip())
            if rest.startswith("解绑"):
                return ("unbind", "")
            return ("unknown", rest)
    return None


def format_help() -> str:
    return "\n".join(
        [
            "星铁每日检查用法：",
            "/委托：检查今日每日实训和派遣状态",
            "/委托绑定 <米游社Cookie>：绑定星铁账号",
            "/委托解绑：删除本地绑定",
            "建议私聊机器人绑定 Cookie，不要在群里发送。",
        ]
    )


def format_not_bound() -> str:
    return "\n".join(
        [
            "你还没有绑定星铁账号。",
            "请先私聊发送：/委托绑定 cookie_token=...; account_id=...",
        ]
    )


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
    train_done = current_train is not None and current_train >= max_train

    accepted = _first_int(note, "accepted_expedition_num", "accepted_epedition_num")
    total = _first_int(note, "total_expedition_num", "total_epedition_num")
    expeditions = note.get("expeditions") or []
    if total is None:
        total = len(expeditions)
    if accepted is None:
        accepted = _count_started_expeditions(expeditions)
    idle = max(total - accepted, 0)
    finished = _count_finished_expeditions(expeditions)

    lines = [
        "星铁今日委托检查",
        f"账号：{nickname}（UID {uid}）",
        f"每日实训：{_score_text(current_train)}/{max_train}，{'已完成' if train_done else '未完成'}",
        f"派遣：{accepted}/{total}，{_format_expedition_status(idle, finished)}",
    ]

    if train_done and idle == 0:
        lines.append("今天的每日已经 Clear。")
    else:
        misses = []
        if not train_done:
            misses.append("每日实训还没满")
        if idle:
            misses.append(f"还有 {idle} 个派遣空位")
        lines.append("还没 Clear：" + "；".join(misses))

    return "\n".join(lines)


def _api_get(url: str, cookie: str, params: dict[str, Any], with_ds: bool) -> dict[str, Any]:
    cookie = _normalize_cookie(cookie)
    query = urlencode(params)
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


def _headers(cookie: str, query: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "Origin": "https://webstatic.mihoyo.com",
        "Referer": "https://webstatic.mihoyo.com/",
        "User-Agent": f"Mozilla/5.0 miHoYoBBS/{APP_VERSION}",
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": "5",
        "x-rpc-language": "zh-cn",
    }
    if query:
        headers["DS"] = _make_ds(query)
    return headers


def _make_ds(query: str) -> str:
    timestamp = int(time.time())
    nonce = random.randint(100000, 200000)
    checksum = hashlib.md5(
        f"salt={DS_SALT}&t={timestamp}&r={nonce}&b=&q={query}".encode("utf-8")
    ).hexdigest()
    return f"{timestamp},{nonce},{checksum}"


def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retcode = payload.get("retcode", 0)
    try:
        retcode_value = int(retcode)
    except (TypeError, ValueError):
        retcode_value = retcode
    if retcode_value != 0:
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


def _count_started_expeditions(expeditions: list[dict[str, Any]]) -> int:
    idle_statuses = {"", "None", "Idle", "NotStart", "NotStarted"}
    return sum(1 for item in expeditions if str(item.get("status", "")) not in idle_statuses)


def _count_finished_expeditions(expeditions: list[dict[str, Any]]) -> int:
    return sum(1 for item in expeditions if int(item.get("remaining_time") or 0) <= 0)


def _format_expedition_status(idle: int, finished: int) -> str:
    if idle:
        return f"未全部派出，{idle} 个空位"
    if finished:
        return f"已全部派出，{finished} 个可领取"
    return "已全部派出"
