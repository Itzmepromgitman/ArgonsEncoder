# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import uuid
from html import escape

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

try:
    from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout
except ImportError:
    from asyncio import TimeoutError as ListenerTimeout

from bot.config import LOG_CHANNEL, OWNER_ID
from bot.decorator import invalidate_user_caches, is_admin
from bot.logger import LOGGER, send_logs
from bot.utils.listener import ListenerBusy, listen_once
from bot.utils.restart import restart_bot
from bot.utils.shell import shell_command
from bot.utils.ui import ICONS, close_btn, progress_bar, safe_edit
from database import forget_user_data, full_userbase, get_variable, set_variable

log = LOGGER(__name__)


@Client.on_message(filters.command("restart") & filters.private)
async def handle_restart(client, message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        await message.reply_text("🔄 Restarting… I'll ping you when I'm back.")
        await restart_bot(client, message)
    except Exception as e:
        log.error(e)
        await message.reply_text(
            f"❌ Restart failed: <code>{escape(str(e))}</code>"
        )


@Client.on_message(filters.command("log") & filters.private)
async def handle_logs(client, message):
    if message.from_user.id != OWNER_ID:
        return
    await send_logs(client, message)


@Client.on_message(filters.command("shell") & filters.private)
async def handle_shell(client, message):
    if message.from_user.id != OWNER_ID:
        return
    await shell_command(client, message)


@Client.on_message(filters.command("ban") & filters.private)
async def ban_command(client, message):
    if not await is_admin(message.from_user.id):
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        try:
            target = int(message.command[1])
        except ValueError:
            await message.reply_text("⚠️ Usage: <code>/ban USER_ID</code> or reply to a user.")
            return

    if not target or target == OWNER_ID:
        await message.reply_text("❌ Invalid target (owner cannot be banned).")
        return

    banned = await get_variable("banned_users", [])
    if target in banned:
        await message.reply_text("⚠️ User is already banned.")
        return

    banned.append(target)
    if not await set_variable("banned_users", banned):
        await message.reply_text("⚠️ Could not save the ban — try again.")
        return
    invalidate_user_caches()
    await message.reply_text(f"🚫 Banned <code>{target}</code>.")


@Client.on_message(filters.command("unban") & filters.private)
async def unban_command(client, message):
    if not await is_admin(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.reply_text("⚠️ Usage: <code>/unban USER_ID</code>")
        return

    try:
        target = int(message.command[1])
    except ValueError:
        await message.reply_text("⚠️ Usage: <code>/unban USER_ID</code>")
        return

    banned = await get_variable("banned_users", [])
    if target not in banned:
        await message.reply_text("⚠️ User is not banned.")
        return

    banned.remove(target)
    if not await set_variable("banned_users", banned):
        await message.reply_text("⚠️ Could not save the unban — try again.")
        return
    invalidate_user_caches()
    await message.reply_text(f"✅ Unbanned <code>{target}</code>.")


@Client.on_message(filters.command("maint") & filters.private)
async def maint_command(client, message):
    if not await is_admin(message.from_user.id):
        return

    current = await get_variable("maintenance", False)
    new_state = not current
    if not await set_variable("maintenance", new_state):
        await message.reply_text("⚠️ Could not update maintenance mode.")
        return
    if new_state:
        state_txt = (
            f"{ICONS.warn} <b>Maintenance enabled</b>\n"
            "<blockquote>New jobs are rejected; running jobs finish normally.</blockquote>"
        )
    else:
        state_txt = (
            f"{ICONS.success} <b>Maintenance disabled</b>\n"
            "<blockquote>The bot accepts new jobs again.</blockquote>"
        )
    await message.reply_text(state_txt)


def _broadcast_status_card(total, successful, blocked, deleted, unsuccessful, done=False) -> str:
    title = "Broadcast completed" if done else "Broadcast in progress"
    icon = ICONS.success if done else ICONS.upload
    processed = successful + blocked + deleted + unsuccessful
    percent = (processed / total * 100) if total else (100 if done else 0)
    return (
        f"{icon} <b>{title}</b>\n"
        f"<blockquote><code>{progress_bar(percent, 16)}</code> <b>{percent:.1f}%</b>\n"
        f"👥 Reached: <code>{processed:,}</code> / <code>{total:,}</code>\n"
        f"✅ Sent: <code>{successful}</code> · 🚫 Blocked: <code>{blocked}</code>\n"
        f"👻 Deactivated: <code>{deleted}</code> · ⚠️ Failed: <code>{unsuccessful}</code></blockquote>"
    )


_broadcast_lock = asyncio.Lock()
_active_broadcast_token: str | None = None
_broadcast_stop_event: asyncio.Event | None = None


@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message):
    if _broadcast_lock.locked():
        await message.reply_text(
            "⏳ <b>Another broadcast is already running.</b>\n"
            "<i>Wait for it to finish before starting another.</i>"
        )
        return
    async with _broadcast_lock:
        return await _broadcast_command_impl(client, message)


async def _broadcast_command_impl(client, message):
    global _active_broadcast_token, _broadcast_stop_event
    if not await is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ Reply to the message you want to broadcast.",
            reply_markup=InlineKeyboardMarkup([[close_btn()]]),
        )
        return

    query = await full_userbase()
    broadcast_msg = message.reply_to_message

    successful = 0
    blocked = 0
    deleted = 0
    unsuccessful = 0
    total = 0
    edit = 0

    broadcast_token = uuid.uuid4().hex[:16]
    pls_wait = await message.reply(
        "<b>📣 Broadcast setup</b>\n<blockquote>Choose how to deliver this message.</blockquote>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📢 Normal",
                        callback_data=f"broadcast_normal:{broadcast_token}",
                    ),
                    InlineKeyboardButton(
                        "📌 Pin in log channel",
                        callback_data=f"broadcast_pin:{broadcast_token}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "Cancel",
                        callback_data=f"broadcast_cancel:{broadcast_token}",
                    )
                ],
            ]
        ),
    )

    try:
        callback = await client.wait_for_callback_query(
            chat_id=message.chat.id,
            filters=(
                filters.regex(
                    rf"^broadcast_(normal|pin|cancel):{broadcast_token}$"
                )
                & filters.user(message.from_user.id)
            ),
            timeout=30,
        )
    except asyncio.TimeoutError:
        await pls_wait.edit("⏰ Broadcast timed out — run /broadcast again.")
        return

    await callback.answer()

    if callback.data.startswith("broadcast_cancel:"):
        try:
            await pls_wait.delete()
        except Exception:
            pass
        return

    pin = 1 if callback.data.startswith("broadcast_pin:") else 0
    _active_broadcast_token = broadcast_token
    _broadcast_stop_event = asyncio.Event()
    stop_event = _broadcast_stop_event
    broadcast_markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "⏹ Stop broadcast",
                callback_data=f"broadcast_stop:{broadcast_token}",
            ),
            close_btn("Hide"),
        ]]
    )
    await pls_wait.edit(
        _broadcast_status_card(len(query), 0, 0, 0, 0),
        reply_markup=broadcast_markup,
    )

    stopped = False
    for chat_id in query:
        if stop_event.is_set():
            stopped = True
            break
        try:
            await broadcast_msg.copy(chat_id)
            successful += 1
        except FloodWait as e:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=e.value)
                stopped = True
                break
            except asyncio.TimeoutError:
                pass
            try:
                sent = await broadcast_msg.copy(chat_id)
                successful += 1
            except Exception:
                unsuccessful += 1
        except UserIsBlocked:
            await forget_user_data(chat_id)
            blocked += 1
        except InputUserDeactivated:
            await forget_user_data(chat_id)
            deleted += 1
        except Exception:
            unsuccessful += 1
        total += 1
        edit += 1

        if edit >= 20:
            edit = 0
            try:
                await pls_wait.edit_text(
                    _broadcast_status_card(total, successful, blocked, deleted, unsuccessful),
                    reply_markup=broadcast_markup,
                )
            except Exception:
                pass

    if pin and not stopped:
        try:
            pinned = await broadcast_msg.copy(LOG_CHANNEL)
            await pinned.pin(disable_notification=True)
        except Exception as exc:
            log.warning(f"Could not pin broadcast in log channel: {exc}")

    final_text = _broadcast_status_card(
        total, successful, blocked, deleted, unsuccessful, done=True
    )
    if stopped:
        final_text += "\n\n⏹ <b>Broadcast stopped by the operator.</b>"
    try:
        await pls_wait.edit_text(
            final_text,
            reply_markup=InlineKeyboardMarkup([[close_btn("Close")]]),
        )
    finally:
        _active_broadcast_token = None
        _broadcast_stop_event = None


@Client.on_callback_query(
    filters.regex(r"^broadcast_stop:") & filters.private
)
async def broadcast_stop_callback(client, callback_query):
    if not await is_admin(callback_query.from_user.id):
        await callback_query.answer("Admin only.", show_alert=True)
        return
    token = callback_query.data.split(":", 1)[1]
    if token != _active_broadcast_token or _broadcast_stop_event is None:
        await callback_query.answer("This broadcast is no longer active.", show_alert=True)
        return
    _broadcast_stop_event.set()
    await callback_query.answer("⏹ Stopping after the current recipient…")


async def _admin_panel_content():
    a = await get_variable("admin", [])
    admin_list = (
        "\n".join(f"• <code>{escape(str(x))}</code>" for x in a)
        if a
        else "<i>No extra admins yet.</i>"
    )
    text = (
        f"{ICONS.admin} <b>Admin panel</b>\n\n"
        f"<blockquote><b>Admins</b>\n{admin_list}</blockquote>\n"
        f"<i>Admins can use bot commands except owner-only ops "
        f"(/admin, /shell, /restart, /jobs).</i>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add admin", callback_data="admin_add"),
                InlineKeyboardButton("➖ Remove admin", callback_data="admin_rem"),
            ],
            [InlineKeyboardButton("♻️ Refresh", callback_data="admin_refresh")],
            [close_btn()],
        ]
    )
    return text, keyboard


@Client.on_message(filters.command("admin") & filters.private)
async def admin(client, message, owner_id: int | None = None):
    if owner_id is None:
        owner_id = getattr(getattr(message, "from_user", None), "id", None)
    if owner_id != OWNER_ID:
        return

    text, keyboard = await _admin_panel_content()
    if getattr(message, "photo", None) or (
        getattr(message, "caption", None)
        and "Admin panel" in (message.caption or "")
    ):
        try:
            await message.edit_caption(caption=text, reply_markup=keyboard)
            return
        except Exception:
            pass
    if await safe_edit(message, text, keyboard):
        return

    await message.reply_text(text, reply_markup=keyboard)


async def _parse_target_input(a):
    """Returns (chat_id, error) from a listened message."""
    if a.forward_from:
        return a.forward_from.id, None
    if a.forward_from_chat:
        return None, "Forwarded channel posts cannot identify a user. Forward a message from the user instead."
    if not a.text:
        return None, "Please send a valid user ID (text or forward)."
    try:
        return int(a.text.strip()), None
    except ValueError:
        return None, "Invalid input! Please send a valid user ID."


ADD_ADMIN_PROMPT = (
    f"{ICONS.admin} <b>Add an admin</b>\n\n"
    "<blockquote expandable><b>How</b>\n"
    "1. Forward a message from the target user, <b>or</b>\n"
    "2. Send their numeric user ID</blockquote>\n"
    "<i>Make sure the ID is valid · press Cancel to abort.</i>"
)

REM_ADMIN_PROMPT = (
    f"{ICONS.admin} <b>Remove an admin</b>\n\n"
    "<blockquote expandable><b>How</b>\n"
    "1. Forward a message from the target user, <b>or</b>\n"
    "2. Send their numeric user ID</blockquote>\n"
    "<i>Make sure the ID is valid · press Cancel to abort.</i>"
)


@Client.on_callback_query(filters.regex("^admin_") & filters.private)
async def admin2(client, query):
    uid = query.from_user.id

    if uid != OWNER_ID:
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    action = query.data.split("_")[1]

    if action == "refresh":
        await query.answer()
        await admin(client, query.message, owner_id=OWNER_ID)
        return

    if action not in ("add", "rem"):
        await query.answer("Invalid action.", show_alert=True)
        return

    txt = ADD_ADMIN_PROMPT if action == "add" else REM_ADMIN_PROMPT
    await query.answer("Waiting for the target user…")

    while True:
        b = await client.send_message(
            uid,
            text=txt + "\n<i>Send a numeric user ID or forward a message from the user. Send /cancel to abort.</i>",
        )
        try:
            a = await listen_once(
                client, uid, b.id, f"admin:{action}", 30
            )
        except ListenerBusy as exc:
            await client.send_message(uid, f"⚠️ {exc}")
            await b.delete()
            break
        except ListenerTimeout:
            await client.send_message(
                chat_id=uid,
                text="⏰ Timed out — admin setup cancelled.",
            )
            await b.delete()
            break

        if a.text and a.text.lower().strip() in ("/cancel", "cancel", "❌ cancel"):
            await client.send_message(
                chat_id=uid,
                text="🚫 Admin setup cancelled.",
            )
            await b.delete()
            break

        chat_id, err = await _parse_target_input(a)
        if err:
            await client.send_message(uid, f"❌ {err}")
            await b.delete()
            continue

        admin1 = await get_variable("admin", [])

        if action == "add":
            if chat_id in admin1:
                await client.send_message(uid, "⚠️ Already an admin — send a different ID…")
                await b.delete()
                continue
            admin1.append(chat_id)
        else:
            if chat_id not in admin1:
                await client.send_message(uid, "⚠️ Not an admin — send a different ID…")
                await b.delete()
                continue
            admin1.remove(chat_id)

        saved = await set_variable("admin", admin1)
        if not saved:
            await client.send_message(uid, "⚠️ Could not save the admin list — try again.")
            await b.delete()
            break
        invalidate_user_caches()
        await b.delete()

        verb = "added to" if action == "add" else "removed from"
        await client.send_message(
            uid,
            f"✅ User <code>{chat_id}</code> {verb} the admin list.",
        )

        # Drop the old panel (if any) and send a fresh one.
        try:
            await query.message.delete()
        except Exception:
            pass
        panel_text, panel_keyboard = await _admin_panel_content()
        await client.send_message(
            uid,
            panel_text,
            reply_markup=panel_keyboard,
        )
        break
