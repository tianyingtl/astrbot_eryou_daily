from __future__ import annotations

import asyncio
import re
from pathlib import Path
from time import time

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from .hsr_daily import (
        GAME_KEY_HSR,
        BindingStore,
        HsrApiError,
        create_qr_login,
        fetch_hsr_daily_note,
        format_game_menu,
        format_group_bind_guide,
        format_help,
        format_login_menu,
        format_note_status,
        format_not_bound,
        format_phone_login_notice,
        game_name,
        get_hsr_roles,
        get_login_cookie_by_qr,
        parse_commission_command,
        save_qr_image,
        select_default_role,
    )
except ImportError:
    from hsr_daily import (
        GAME_KEY_HSR,
        BindingStore,
        HsrApiError,
        create_qr_login,
        fetch_hsr_daily_note,
        format_game_menu,
        format_group_bind_guide,
        format_help,
        format_login_menu,
        format_note_status,
        format_not_bound,
        format_phone_login_notice,
        game_name,
        get_hsr_roles,
        get_login_cookie_by_qr,
        parse_commission_command,
        save_qr_image,
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
        elif action == "bind_game_menu":
            reply = self._private_only(event) or format_game_menu()
        elif action == "bind_game":
            reply = self._private_only(event) or await self._choose_game(sender_key, value)
        elif action == "qr":
            block = self._private_only(event)
            if block:
                yield event.plain_result(block)
                event.stop_event()
                return
            reply, image_path = await self._start_qr(sender_key, value)
            yield event.plain_result(reply)
            if image_path:
                yield event.image_result(str(image_path))
            event.stop_event()
            return
        elif action == "confirm":
            reply = self._private_only(event) or await self._confirm_qr(sender_key)
        elif action == "phone":
            reply = self._private_only(event) or format_phone_login_notice()
        elif action == "unbind":
            reply = self._unbind(sender_key)
        elif action == "check":
            reply = await self._check(sender_key)
        else:
            reply = "指令看不懂。发送 /委托帮助 查看用法。"

        yield event.plain_result(reply)
        event.stop_event()

    async def _choose_game(self, sender_key: str, game_key: str) -> str:
        if game_key != GAME_KEY_HSR:
            return "当前版本先支持：/委托绑定 星铁"

        cookie = self.bindings.get_account_cookie(sender_key)
        if not cookie:
            self.bindings.set_pending(sender_key, {"game": game_key, "created_at": int(time())})
            return format_login_menu(game_key)

        return await self._bind_game_from_cookie(sender_key, game_key, cookie)

    async def _start_qr(self, sender_key: str, game_key: str) -> tuple[str, Path | None]:
        pending = self.bindings.get_pending(sender_key) or {}
        game_key = game_key or pending.get("game") or GAME_KEY_HSR
        if game_key != GAME_KEY_HSR:
            return ("当前版本先支持：/委托绑定 星铁", None)

        try:
            qr = await asyncio.to_thread(create_qr_login)
            image_path = Path(__file__).resolve().parent / "data" / f"qr_{_safe_key(sender_key)}.png"
            await asyncio.to_thread(save_qr_image, qr["url"], image_path)
        except ImportError:
            return ("缺少二维码依赖。请安装 requirements.txt 后重载插件。", None)
        except Exception as exc:
            return (f"生成扫码登录失败：{exc}", None)

        self.bindings.set_pending(
            sender_key,
            {
                "game": game_key,
                "ticket": qr["ticket"],
                "device_id": qr["device_id"],
                "created_at": int(time()),
            },
        )
        return (
            "\n".join(
                [
                    f"已选择：{game_name(game_key)}",
                    "请用米游社 App 扫描二维码并确认登录。",
                    "确认后回到这里发送：/委托确认",
                    "二维码过期后重新发送：/委托扫码",
                ]
            ),
            image_path,
        )

    async def _confirm_qr(self, sender_key: str) -> str:
        pending = self.bindings.get_pending(sender_key)
        if not pending or not pending.get("ticket"):
            return "没有进行中的扫码登录。请先私聊发送 /委托绑定 星铁，然后选择 /委托扫码。"

        try:
            cookie = await asyncio.to_thread(
                get_login_cookie_by_qr,
                pending["ticket"],
                pending["device_id"],
            )
        except HsrApiError as exc:
            message = str(exc)
            if "未扫描" in message or "已扫描" in message:
                return message
            self.bindings.delete_pending(sender_key)
            return f"扫码登录失败：{message}"
        except Exception as exc:
            return f"扫码登录失败：{exc}"

        self.bindings.set_account_cookie(sender_key, cookie)
        game_key = pending.get("game") or GAME_KEY_HSR
        reply = await self._bind_game_from_cookie(sender_key, game_key, cookie)
        self.bindings.delete_pending(sender_key)
        return reply

    async def _bind_game_from_cookie(self, sender_key: str, game_key: str, cookie: str) -> str:
        if game_key != GAME_KEY_HSR:
            return "当前版本先支持星铁每日检查。"

        try:
            roles = await asyncio.to_thread(get_hsr_roles, cookie)
        except HsrApiError as exc:
            return f"读取米游社账号失败：{exc}"
        except Exception as exc:
            return f"读取米游社账号失败：{exc}"

        role = select_default_role(roles)
        if not role:
            return "这个米游社账号没有找到崩坏：星穹铁道国服角色。"

        self.bindings.set_game_binding(sender_key, game_key, role)
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        return f"已绑定{game_name(game_key)}：{nickname}（UID {uid}）。以后发送 /委托 就能检查今日状态。"

    def _unbind(self, sender_key: str) -> str:
        if self.bindings.delete_user(sender_key):
            return "已解绑本地米游社账号和游戏绑定。"
        return "你还没有绑定账号。"

    async def _check(self, sender_key: str) -> str:
        binding = self.bindings.get_game_binding(sender_key, GAME_KEY_HSR)
        cookie = self.bindings.get_account_cookie(sender_key)
        if not binding and cookie:
            bind_reply = await self._bind_game_from_cookie(sender_key, GAME_KEY_HSR, cookie)
            binding = self.bindings.get_game_binding(sender_key, GAME_KEY_HSR)
            if not binding:
                return bind_reply

        if not binding or not cookie:
            return format_not_bound()

        try:
            note = await asyncio.to_thread(
                fetch_hsr_daily_note,
                cookie,
                binding["role"],
            )
        except HsrApiError as exc:
            return f"查询失败：{exc}"
        except Exception as exc:
            return f"查询失败：{exc}"

        return format_note_status(binding["role"], note)

    def _private_only(self, event: AstrMessageEvent) -> str:
        if _is_private_event(event):
            return ""
        return format_group_bind_guide()


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


def _is_private_event(event: AstrMessageEvent) -> bool:
    message_obj = getattr(event, "message_obj", None)
    if message_obj is None:
        return False
    group_id = getattr(message_obj, "group_id", "")
    return not group_id


def _safe_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)
