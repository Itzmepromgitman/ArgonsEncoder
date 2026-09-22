# Developed by ARGON telegram: @REACTIVEARGON
import asyncio

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
try:
    from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout
except ImportError:
    from asyncio import TimeoutError as ListenerTimeout

from bot.config import OWNER_ID
from bot.decorator import invalidate_user_caches, is_admin
from bot.logger import LOGGER, send_logs
from bot.utils.restart import restart_bot
from bot.utils.shell import shell_command
from bot.utils.ui import ICONS, close_btn, safe_edit
from database import full_userbase, del_user, get_variable, set_variable

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
        await message.reply_text(f"❌ Restart failed: <code>{e}</code>")


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
    await set_variable("banned_users", banned)
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
    await set_variable("banned_users", banned)
    invalidate_user_caches()
    await message.reply_text(f"✅ Unbanned <code>{target}</code>.")


@Client.on_message(filters.command("maint") & filters.private)
async def maint_command(client, message):
    if not await is_admin(message.from_user.id):
        return

    current = await get_variable("maintenance", False)
    new_state = not current
    await set_variable("maintenance", new_state)
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
    return (
        f"{icon} <b>{title}</b>\n"
        f"<blockquote>👥 Total users: <code>{total}</code>\n"
        f"✅ Successful: <code>{successful}</code>\n"
        f"🚫 Blocked: <code>{blocked}</code>\n"
        f"👻 Deactivated: <code>{deleted}</code>\n"
        f"⚠️ Unsuccessful: <code>{unsuccessful}</code></blockquote>"
    )


@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message):
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

    pls_wait = await message.reply(
        "<b>📣 Broadcast setup</b>\n<blockquote>Choose how to deliver this message.</blockquote>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📢 Normal", callback_data="broadcast_normal"),
                    InlineKeyboardButton("📌 Pin", callback_data="broadcast_pin"),
                ],
                [close_btn("Cancel")],
            ]
        ),
    )

    try:
        callback = await client.wait_for_callback_query(
            chat_id=message.chat.id,
            filters=filters.user(message.from_user.id),
            timeout=30,
        )
    except asyncio.TimeoutError:
        await pls_wait.edit("⏰ Broadcast timed out — run /broadcast again.")
        return

    await callback.answer()

    pin = 1 if callback.data == "broadcast_pin" else 0
    await pls_wait.edit(
        _broadcast_status_card(len(query), 0, 0, 0, 0),
        reply_markup=InlineKeyboardMarkup([[close_btn("Hide")]]),
    )

    for chat_id in query:
        try:
            sent = await broadcast_msg.copy(chat_id)
            successful += 1
            if pin:
                try:
                    await sent.pin(both_sides=True)
                except Exception:
                    pass
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                sent = await broadcast_msg.copy(chat_id)
                successful += 1
            except Exception:
                unsuccessful += 1
        except UserIsBlocked:
            await del_user(chat_id)
            blocked += 1
        except InputUserDeactivated:
            await del_user(chat_id)
            deleted += 1
        except Exception:
            unsuccessful += 1
        total += 1
        edit += 1

        if edit >= 20:
            edit = 0
            try:
                await pls_wait.edit_text(
                    _broadcast_status_card(total, successful, blocked, deleted, unsuccessful)
                )
            except Exception:
                pass

    await pls_wait.edit_text(
        _broadcast_status_card(total, successful, blocked, deleted, unsuccessful, done=True),
        reply_markup=InlineKeyboardMarkup([[close_btn("Close")]]),
    )


@Client.on_message(filters.command("admin") & filters.private)
async def admin(client, message):
    if message.from_user.id != OWNER_ID:
        return

    a = await get_variable("admin", [])
    admin_list = (
        "\n".join(f"• <code>{x}</code>" for x in a) if a else "<i>No extra admins yet.</i>"
    )
    txt = (
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

    # Prefer editing an existing panel message when opened via Refresh.
    if message.photo or (message.caption and "Admin panel" in (message.caption or "")):
        try:
            await message.edit_caption(caption=txt, reply_markup=keyboard)
            return
        except Exception:
            pass
    if await safe_edit(message, txt, keyboard):
        return

    await message.reply_text(txt, reply_markup=keyboard)


async def _parse_target_input(a):
    """Returns (chat_id, error) from a listened message."""
    if a.forward_from:
        return a.forward_from.id, None
    if a.forward_from_chat:
        return a.forward_from_chat.id, None
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


@Client.on_callback_query(filters.regex("^admin_"))
async def admin2(client, query):
    uid = query.from_user.id

    if uid != OWNER_ID:
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    action = query.data.split("_")[1]

    if action == "refresh":
        await query.answer()
        await admin(client, query.message)
        return

    if action not in ("add", "rem"):
        await query.answer("Invalid action.", show_alert=True)
        return

    txt = ADD_ADMIN_PROMPT if action == "add" else REM_ADMIN_PROMPT

    while True:
        b = await client.send_message(
            uid,
            text=txt + "\n<i>Send a user ID, username, or forward a message. Send /cancel to abort.</i>",
        )
        try:
            a = await client.listen(chat_id=uid, timeout=30)
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

        await set_variable("admin", admin1)
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
        # Build a synthetic message-like call: re-send the panel.
        class _M:
            def __init__(self, chat_id):
                self.chat = type("C", (), {"id": chat_id})()
                self.photo = None
                self.caption = None
            async def reply_text(self, text, reply_markup=None):
                return await client.send_message(chat_id, text, reply_markup=reply_markup)
        await admin(client, _M(uid))
        break
