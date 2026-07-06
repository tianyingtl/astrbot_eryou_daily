from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from .hsr_daily import (
        BindingStore,
        HsrApiError,
        fetch_hsr_daily_note,
        format_help,
        format_note_status,
        format_not_bound,
        get_hsr_roles,
        parse_commission_command,
        select_default_role,
    )
except ImportError:
    from hsr_daily import (
        BindingStore,
        HsrApiError,
        fetch_hsr_daily_note,
        format_help,
        format_note_status,
        format_not_bound,
        get_hsr_roles,
        parse_commission_command,
        select_default_role,
    )


class EryouDailyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        data_path = Path(__file__).resolve().parent / "data" / "bindings.json"
        self.bindings = BindingStore(data_path)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        command = parse_commission_command(getattr(event, "message_str", ""))
        if command is None:
            return

        sender_key = _get_sender_key(event)
        if not sender_key:
            yield event.plain_result("无法识别发送者，不能查询或绑定账号。")
            event.stop_event()
            return

        action, value = command
        if action == "help":
            reply = format_help()
        elif action == "bind":
            reply = await self._bind(sender_key, value)
        elif action == "unbind":
            reply = self._unbind(sender_key)
        elif action == "check":
            reply = await self._check(sender_key)
        else:
            reply = "指令看不懂。发送 /委托帮助 查看用法。"

        yield event.plain_result(reply)
        event.stop_event()

    async def _bind(self, sender_key: str, cookie: str) -> str:
        if not cookie:
            return "请把米游社 Cookie 放在指令后面：/委托绑定 cookie_token=...; account_id=..."

        try:
            roles = await asyncio.to_thread(get_hsr_roles, cookie)
        except HsrApiError as exc:
            return f"绑定失败：{exc}"
        except Exception as exc:
            return f"绑定失败：{exc}"

        role = select_default_role(roles)
        if not role:
            return "这个 Cookie 没有找到崩坏：星穹铁道国服角色。"

        self.bindings.set(sender_key, {"cookie": cookie.strip(), "role": role})
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        return f"已绑定星铁账号：{nickname}（UID {uid}）。以后发送 /委托 就能检查今日状态。"

    def _unbind(self, sender_key: str) -> str:
        if self.bindings.delete(sender_key):
            return "已解绑星铁账号。"
        return "你还没有绑定星铁账号。"

    async def _check(self, sender_key: str) -> str:
        binding = self.bindings.get(sender_key)
        if not binding:
            return format_not_bound()

        try:
            note = await asyncio.to_thread(
                fetch_hsr_daily_note,
                binding["cookie"],
                binding["role"],
            )
        except HsrApiError as exc:
            return f"查询失败：{exc}"
        except Exception as exc:
            return f"查询失败：{exc}"

        return format_note_status(binding["role"], note)


def _get_sender_key(event: AstrMessageEvent) -> str:
    getter = getattr(event, "get_sender_id", None)
    if callable(getter):
        sender_id = getter()
        if sender_id:
            return str(sender_id)

    for attr in ("sender_id", "user_id"):
        value = getattr(event, attr, None)
        if value:
            return str(value)

    message_obj = getattr(event, "message_obj", None)
    sender = getattr(message_obj, "sender", None)
    for attr in ("user_id", "sender_id", "id"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)

    origin = getattr(event, "unified_msg_origin", None)
    return str(origin) if origin else ""
