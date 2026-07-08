from __future__ import annotations

import asyncio
import base64
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from time import time

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:
    from .hsr_daily import (
        GAME_KEY_HSR,
        GAME_KEY_NTE,
        BindingStore,
        HsrApiError,
        create_qr_login,
        daily_missing_text,
        fetch_daily_note,
        fetch_nte_daily_note,
        format_game_menu,
        format_help,
        format_nte_bind_guide,
        format_note_status,
        format_not_bound,
        format_reminder_usage,
        get_game_roles,
        get_nte_roles,
        game_name,
        get_login_cookie_by_qr,
        login_nte_by_sms,
        is_daily_done,
        is_supported_game,
        make_nte_device_id,
        nte_reminder_reasons,
        parse_commission_command,
        parse_reminder_value,
        resolve_binding_path,
        save_qr_image,
        select_default_role,
        select_nte_role,
        send_nte_sms_code,
        supports_mihoyo_login,
        supports_tajiduo_login,
        supported_game_text,
    )
except ImportError:
    from hsr_daily import (
        GAME_KEY_HSR,
        GAME_KEY_NTE,
        BindingStore,
        HsrApiError,
        create_qr_login,
        daily_missing_text,
        fetch_daily_note,
        fetch_nte_daily_note,
        format_game_menu,
        format_help,
        format_nte_bind_guide,
        format_note_status,
        format_not_bound,
        format_reminder_usage,
        get_game_roles,
        get_nte_roles,
        game_name,
        get_login_cookie_by_qr,
        login_nte_by_sms,
        is_daily_done,
        is_supported_game,
        make_nte_device_id,
        nte_reminder_reasons,
        parse_commission_command,
        parse_reminder_value,
        resolve_binding_path,
        save_qr_image,
        select_default_role,
        select_nte_role,
        send_nte_sms_code,
        supports_mihoyo_login,
        supports_tajiduo_login,
        supported_game_text,
    )


class EryouDailyPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        plugin_dir = Path(__file__).resolve().parent
        data_path = resolve_binding_path(plugin_dir)
        self.data_dir = data_path.parent
        self.bindings = BindingStore(data_path)
        self._reminder_task = None
        self._ensure_reminder_task()

    async def terminate(self) -> None:
        if self._reminder_task:
            self._reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reminder_task

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        command = parse_commission_command(getattr(event, "message_str", ""))
        if command is None:
            return
        self._ensure_reminder_task()

        sender_key = _get_sender_key(event)
        if not sender_key:
            yield event.plain_result("无法识别发送者，不能查询或绑定账号。")
            event.stop_event()
            return

        allowed, reason = self._group_allowed_event(event)
        if not allowed:
            yield event.plain_result(reason)
            event.stop_event()
            return

        action, value = command
        if action == "help":
            reply = format_help()
        elif action == "bind_game_menu":
            reply = await self._private_reply(event, sender_key, format_game_menu())
        elif action == "bind_game":
            game_key, target_uid = _split_game_value(value)
            if not is_supported_game(game_key):
                reply = f"当前支持绑定：{supported_game_text()}。示例：/委托绑定 星铁"
            elif supports_tajiduo_login(game_key):
                account = self.bindings.get_tajiduo_account(sender_key)
                if account:
                    reply = await self._bind_nte_from_account(sender_key, account, target_uid)
                else:
                    self.bindings.set_pending(
                        sender_key,
                        {
                            "type": "nte_target",
                            "game": GAME_KEY_NTE,
                            "target_uid": target_uid,
                            "created_at": int(time()),
                        },
                    )
                    guide = format_nte_bind_guide()
                    if target_uid:
                        guide = f"已记录要绑定的异环 UID：{target_uid}\n" + guide
                    reply = await self._private_reply(event, sender_key, guide)
            elif self.bindings.get_account_cookie(sender_key):
                cookie = self.bindings.get_account_cookie(sender_key)
                reply = await self._bind_game_from_cookie(sender_key, game_key, cookie)
            else:
                text, image_path = await self._start_qr(sender_key, game_key)
                if _is_private_event(event):
                    yield event.plain_result(text)
                    if image_path:
                        yield event.image_result(str(image_path))
                else:
                    yield event.plain_result(
                        await self._private_reply(event, sender_key, text, image_path)
                    )
                event.stop_event()
                return
        elif action == "qr":
            text, image_path = await self._start_qr(sender_key, value)
            if _is_private_event(event):
                yield event.plain_result(text)
                if image_path:
                    yield event.image_result(str(image_path))
            else:
                yield event.plain_result(
                    await self._private_reply(event, sender_key, text, image_path)
                )
            event.stop_event()
            return
        elif action == "sms":
            reply = await self._start_nte_sms(event, sender_key, value)
        elif action == "confirm":
            if value:
                reply = await self._confirm_nte_sms(event, sender_key, value)
            else:
                reply = await self._private_reply(
                    event,
                    sender_key,
                    await self._confirm_qr(sender_key),
                )
        elif action == "reminder_set":
            reply = await self._set_reminder(event, sender_key, value)
        elif action == "unbind":
            reply = self._unbind(sender_key)
        elif action == "check":
            reply = await self._check(sender_key, value)
        else:
            reply = "指令看不懂。发送 /委托帮助 查看用法。"

        yield event.plain_result(reply)
        event.stop_event()

    def _ensure_reminder_task(self) -> None:
        if self._reminder_task and not self._reminder_task.done():
            return
        try:
            self._reminder_task = asyncio.create_task(self._reminder_loop())
        except RuntimeError:
            self._reminder_task = None

    async def _private_reply(
        self,
        event: AstrMessageEvent,
        sender_key: str,
        text: str,
        image_path: Path | None = None,
    ) -> str:
        if _is_private_event(event):
            return text

        sent = await _send_private_onebot(event, sender_key, text, image_path)
        if sent and image_path:
            return "二维码已经私聊发送给你了，请去私聊窗口扫码确认。"
        if sent:
            return "绑定说明已经私聊发送给你了，请去私聊窗口继续操作。"
        return "我没法主动私聊你。请先私聊机器人发送 /委托绑定，再继续绑定。"

    async def _set_reminder(self, event: AstrMessageEvent, sender_key: str, value: str) -> str:
        group_id = _get_group_id(event)
        if not group_id:
            return "提醒需要在群里设置，这样到点才能在当前群里 at 你。\n" + format_reminder_usage()

        game_key, reminder_time, error = parse_reminder_value(value)
        if error:
            return error

        binding = self.bindings.get_game_binding(sender_key, game_key)
        if not binding:
            return f"请先绑定{game_name(game_key)}账号，再设置提醒：/委托绑定 {game_name(game_key)}"
        if supports_tajiduo_login(game_key):
            if not self.bindings.get_tajiduo_account(sender_key):
                return f"请先绑定{game_name(game_key)}账号，再设置提醒：/委托绑定 {game_name(game_key)}"
        elif not self.bindings.get_account_cookie(sender_key):
            return f"请先绑定{game_name(game_key)}账号，再设置提醒：/委托绑定 {game_name(game_key)}"

        now = datetime.now()
        last_reminded_date = ""
        note = ""
        reminder_minutes = _time_to_minutes(reminder_time)
        current_minutes = now.hour * 60 + now.minute
        if reminder_minutes is not None and current_minutes >= reminder_minutes:
            last_reminded_date = now.date().isoformat()
            note = "今天这个时间已经过了，将从明天开始提醒。"

        self.bindings.set_reminder(
            sender_key,
            group_id,
            getattr(event, "unified_msg_origin", ""),
            game_key,
            reminder_time,
            last_reminded_date,
        )
        reply = f"已设置提醒：每天 {reminder_time} 检查{game_name(game_key)}。如果还没完成，会在本群 at 你。"
        return f"{reply}\n{note}" if note else reply

    async def _start_qr(self, sender_key: str, game_key: str) -> tuple[str, Path | None]:
        pending = self.bindings.get_pending(sender_key) or {}
        game_key = game_key or pending.get("game") or GAME_KEY_HSR
        if not is_supported_game(game_key):
            return (f"当前支持绑定：{supported_game_text()}。示例：/委托绑定 星铁", None)
        if not supports_mihoyo_login(game_key):
            return (format_nte_bind_guide(), None)

        try:
            qr = await asyncio.to_thread(create_qr_login)
            image_path = self.data_dir / f"qr_{_safe_key(sender_key)}.png"
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
                    f"二维码过期后重新发送：/委托绑定 {game_name(game_key)}",
                ]
            ),
            image_path,
        )

    async def _confirm_qr(self, sender_key: str) -> str:
        pending = self.bindings.get_pending(sender_key)
        if not pending or not pending.get("ticket"):
            return "没有进行中的扫码登录。请先发送 /委托绑定 星铁。"

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

    async def _start_nte_sms(self, event: AstrMessageEvent, sender_key: str, mobile: str) -> str:
        if not _is_private_event(event):
            return await self._private_reply(event, sender_key, format_nte_bind_guide())
        mobile = re.sub(r"\D+", "", mobile or "")
        if not re.fullmatch(r"1\d{10}", mobile):
            return "手机号格式不对。请发送：/委托发码 13800138000"

        device_id = make_nte_device_id()
        old_pending = self.bindings.get_pending(sender_key) or {}
        target_uid = str(old_pending.get("target_uid") or "")
        try:
            await asyncio.to_thread(send_nte_sms_code, mobile, device_id)
        except HsrApiError as exc:
            return f"异环短信发送失败：{exc}"
        except Exception as exc:
            return f"异环短信发送失败：{exc}"

        self.bindings.set_pending(
            sender_key,
            {
                "type": "nte_sms",
                "game": GAME_KEY_NTE,
                "mobile": mobile,
                "device_id": device_id,
                "target_uid": target_uid,
                "created_at": int(time()),
            },
        )
        return "验证码已发送。收到后请在私聊发送：/委托确认 验证码"

    async def _confirm_nte_sms(self, event: AstrMessageEvent, sender_key: str, code: str) -> str:
        if not _is_private_event(event):
            return await self._private_reply(event, sender_key, "请在私聊窗口发送：/委托确认 验证码")
        code = re.sub(r"\D+", "", code or "")
        if not re.fullmatch(r"\d{4,8}", code):
            return "验证码格式不对。请发送：/委托确认 123456"

        pending = self.bindings.get_pending(sender_key)
        if not pending or pending.get("type") != "nte_sms":
            return "没有进行中的异环短信登录。请先发送 /委托发码 手机号。"

        try:
            account, roles = await asyncio.to_thread(
                login_nte_by_sms,
                pending["mobile"],
                code,
                pending["device_id"],
            )
        except HsrApiError as exc:
            return f"异环绑定失败：{exc}"
        except Exception as exc:
            return f"异环绑定失败：{exc}"

        self.bindings.set_tajiduo_account(sender_key, account)
        target_uid = str(pending.get("target_uid") or "")
        try:
            role = select_nte_role(roles, target_uid)
        except HsrApiError as exc:
            self.bindings.delete_pending(sender_key)
            return f"塔吉多登录成功，但还没选定异环角色。{exc}"
        if not role:
            self.bindings.delete_pending(sender_key)
            return "塔吉多登录成功，但这个账号没有找到异环角色。"

        self.bindings.set_tajiduo_binding(sender_key, account, role)
        self.bindings.delete_pending(sender_key)
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        return f"已绑定异环：{nickname}（UID {uid}）。以后发送 /委托 异环 就能检查今日状态。"

    async def _bind_nte_from_account(self, sender_key: str, account: dict, target_uid: str = "") -> str:
        try:
            account, roles = await asyncio.to_thread(get_nte_roles, account)
        except HsrApiError as exc:
            return f"读取塔吉多账号失败：{exc}"
        except Exception as exc:
            return f"读取塔吉多账号失败：{exc}"

        try:
            role = select_nte_role(roles, target_uid)
        except HsrApiError as exc:
            return str(exc)
        if not role:
            return "这个塔吉多账号没有找到异环角色。"

        self.bindings.set_tajiduo_binding(sender_key, account, role)
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        return f"已绑定异环：{nickname}（UID {uid}）。以后发送 /委托 异环 就能检查今日状态。"

    async def _bind_game_from_cookie(self, sender_key: str, game_key: str, cookie: str) -> str:
        if not is_supported_game(game_key):
            return f"当前支持绑定：{supported_game_text()}。示例：/委托绑定 星铁"
        if not supports_mihoyo_login(game_key):
            return format_nte_bind_guide()

        try:
            roles = await asyncio.to_thread(get_game_roles, cookie, game_key)
        except HsrApiError as exc:
            return f"读取米游社账号失败：{exc}"
        except Exception as exc:
            return f"读取米游社账号失败：{exc}"

        role = select_default_role(roles)
        if not role:
            return f"这个米游社账号没有找到{game_name(game_key)}国服角色。"

        self.bindings.set_game_binding(sender_key, game_key, role)
        nickname = role.get("nickname") or "未知角色"
        uid = role.get("game_uid") or role.get("uid") or "未知 UID"
        return f"已绑定{game_name(game_key)}：{nickname}（UID {uid}）。以后发送 /委托 就能检查今日状态。"

    def _unbind(self, sender_key: str) -> str:
        if self.bindings.delete_user(sender_key):
            return "已解绑本地账号、游戏绑定和提醒设置。"
        return "你还没有绑定账号。"

    async def _check(self, sender_key: str, game_key: str) -> str:
        if game_key:
            return await self._check_one(sender_key, game_key)

        cookie = self.bindings.get_account_cookie(sender_key)
        bindings = self.bindings.get_game_bindings(sender_key)
        if not bindings and cookie:
            bind_reply = await self._bind_game_from_cookie(sender_key, GAME_KEY_HSR, cookie)
            bindings = self.bindings.get_game_bindings(sender_key)
            if not bindings:
                return bind_reply

        if not bindings:
            return format_not_bound()

        replies = []
        for bound_game_key in bindings:
            if is_supported_game(bound_game_key):
                replies.append(await self._check_one(sender_key, bound_game_key))

        return "\n\n".join(replies) if replies else format_not_bound()

    async def _check_one(self, sender_key: str, game_key: str) -> str:
        if not is_supported_game(game_key):
            return f"当前支持：{supported_game_text()}。"

        binding = self.bindings.get_game_binding(sender_key, game_key)
        if supports_tajiduo_login(game_key):
            return await self._check_nte(sender_key, binding)

        cookie = self.bindings.get_account_cookie(sender_key)
        if not binding and cookie:
            bind_reply = await self._bind_game_from_cookie(sender_key, game_key, cookie)
            binding = self.bindings.get_game_binding(sender_key, game_key)
            if not binding:
                return bind_reply

        if not binding or not cookie:
            return f"你还没有绑定{game_name(game_key)}。请先发送 /委托绑定 {game_name(game_key)}"

        try:
            note = await asyncio.to_thread(
                fetch_daily_note,
                cookie,
                binding["role"],
                game_key,
            )
        except HsrApiError as exc:
            return f"查询失败：{exc}"
        except Exception as exc:
            return f"查询失败：{exc}"

        return format_note_status(game_key, binding["role"], note)

    async def _check_nte(self, sender_key: str, binding: dict | None) -> str:
        account = self.bindings.get_tajiduo_account(sender_key)
        if not binding and account:
            bind_reply = await self._bind_nte_from_account(sender_key, account)
            binding = self.bindings.get_game_binding(sender_key, GAME_KEY_NTE)
            if not binding:
                return bind_reply

        if not binding or not account:
            return "你还没有绑定异环。请先发送 /委托绑定 异环"

        try:
            account, note = await asyncio.to_thread(
                fetch_nte_daily_note,
                account,
                binding["role"],
            )
        except HsrApiError as exc:
            return f"查询失败：{exc}"
        except Exception as exc:
            return f"查询失败：{exc}"

        self.bindings.set_tajiduo_account(sender_key, account)
        return format_note_status(GAME_KEY_NTE, binding["role"], note)

    async def _reminder_loop(self) -> None:
        while True:
            try:
                await self._run_due_reminders()
            except Exception:
                pass
            await asyncio.sleep(60)

    async def _run_due_reminders(self) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        current_minutes = now.hour * 60 + now.minute

        for sender_key, reminder in self.bindings.get_reminders():
            game_key = reminder.get("game")
            group_id = str(reminder.get("group_id", ""))
            group_umo = reminder.get("group_umo", "")
            reminder_time = reminder.get("time", "")
            if not is_supported_game(game_key) or not group_id or not group_umo:
                continue
            if reminder.get("last_reminded_date") == today:
                continue
            if not self._group_allowed_by_id(group_id)[0]:
                continue

            due_minutes = _time_to_minutes(reminder_time)
            if due_minutes is None or current_minutes < due_minutes:
                continue

            binding = self.bindings.get_game_binding(sender_key, game_key)
            if not binding:
                self.bindings.mark_reminded(sender_key, group_id, game_key, today)
                continue

            try:
                if supports_tajiduo_login(game_key):
                    account = self.bindings.get_tajiduo_account(sender_key)
                    if not account:
                        self.bindings.mark_reminded(sender_key, group_id, game_key, today)
                        continue
                    account, note = await asyncio.to_thread(fetch_nte_daily_note, account, binding["role"])
                    self.bindings.set_tajiduo_account(sender_key, account)
                else:
                    cookie = self.bindings.get_account_cookie(sender_key)
                    if not cookie:
                        self.bindings.mark_reminded(sender_key, group_id, game_key, today)
                        continue
                    note = await asyncio.to_thread(fetch_daily_note, cookie, binding["role"], game_key)
            except Exception:
                continue

            if game_key == GAME_KEY_NTE:
                reasons = nte_reminder_reasons(note, check_city_stamina=now.weekday() == 6)
                if not reasons:
                    self.bindings.mark_reminded(sender_key, group_id, game_key, today)
                    continue
                reminder_text = f" 娜娜米提醒：{game_name(game_key)}" + "；".join(reasons) + "。今天这关还没过，先补一下比较稳。"
            else:
                if is_daily_done(game_key, note):
                    self.bindings.mark_reminded(sender_key, group_id, game_key, today)
                    continue
                reminder_text = f" 娜娜米提醒：{game_name(game_key)}{daily_missing_text(game_key)}。今天这关还没过，先补一下比较稳。"

            await self.context.send_message(
                group_umo,
                MessageChain(
                    [
                        Comp.At(qq=int(sender_key) if sender_key.isdigit() else sender_key),
                        Comp.Plain(reminder_text),
                    ]
                ),
            )
            self.bindings.mark_reminded(sender_key, group_id, game_key, today)

    def _group_allowed_event(self, event: AstrMessageEvent) -> tuple[bool, str]:
        group_id = _get_group_id(event)
        if not group_id:
            return True, ""
        return self._group_allowed_by_id(group_id)

    def _group_allowed_by_id(self, group_id: str) -> tuple[bool, str]:
        group_id = str(group_id)
        mode = str(_config_get(self.config, "group_filter_mode", "off")).lower()
        whitelist = _normalize_group_list(_config_get(self.config, "whitelist_groups", []))
        blacklist = _normalize_group_list(_config_get(self.config, "blacklist_groups", []))

        if mode in {"off", "关闭", "none", ""}:
            return True, ""
        if mode in {"whitelist", "white", "白名单"} and group_id not in whitelist:
            return False, "本群未加入 /委托 插件白名单。"
        if mode in {"blacklist", "black", "黑名单"} and group_id in blacklist:
            return False, "本群已被加入 /委托 插件黑名单。"
        return True, ""


async def _send_private_onebot(
    event: AstrMessageEvent,
    sender_key: str,
    text: str,
    image_path: Path | None = None,
) -> bool:
    if not sender_key.isdigit():
        return False

    bot = getattr(event, "bot", None)
    if not bot:
        return False

    messages = [{"type": "text", "data": {"text": text}}]
    if image_path:
        with image_path.open("rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        messages.append({"type": "image", "data": {"file": f"base64://{encoded}"}})

    payload = {"user_id": int(sender_key), "message": messages}
    self_id = _get_self_id(event)
    if self_id:
        payload["self_id"] = self_id

    try:
        send_private = getattr(bot, "send_private_msg", None)
        if callable(send_private):
            await send_private(**payload)
            return True

        call_action = getattr(bot, "call_action", None)
        if callable(call_action):
            await call_action("send_private_msg", **payload)
            return True

        api = getattr(bot, "api", None)
        api_call_action = getattr(api, "call_action", None)
        if callable(api_call_action):
            await api_call_action("send_private_msg", **payload)
            return True
    except Exception:
        return False

    return False


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


def _get_group_id(event: AstrMessageEvent) -> str:
    getter = getattr(event, "get_group_id", None)
    if callable(getter):
        group_id = getter()
        if group_id:
            return str(group_id)

    message_obj = getattr(event, "message_obj", None)
    group_id = getattr(message_obj, "group_id", "")
    return str(group_id) if group_id else ""


def _get_self_id(event: AstrMessageEvent) -> int | str | None:
    message_obj = getattr(event, "message_obj", None)
    value = getattr(message_obj, "self_id", None)
    if value:
        return value

    raw = getattr(message_obj, "raw_message", None)
    if isinstance(raw, dict):
        return raw.get("self_id")
    return None


def _is_private_event(event: AstrMessageEvent) -> bool:
    return not bool(_get_group_id(event))


def _safe_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)


def _split_game_value(value: str) -> tuple[str, str]:
    game_key, _, target_uid = str(value or "").partition(":")
    return game_key, target_uid.strip()


def _time_to_minutes(value: str) -> int | None:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(value or ""))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _config_get(config, key: str, default):
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return config[key]
    except Exception:
        return default


def _normalize_group_list(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        items = re.split(r"[\s,，;；]+", value)
    else:
        items = value
    return {str(item).strip() for item in items if str(item).strip()}
