# Developed by ARGON telegram: @REACTIVEARGON
import asyncio

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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
from database import full_userbase, del_user, get_variable, set_variable

log = LOGGER(__name__)


@Client.on_message(filters.command("restart") & filters.private)
async def handle_restart(client, message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        await message.reply_text("🔄 Restarting...")
        await restart_bot(client, message)
    except Exception as e:
        log.error(e)
        await message.reply_text(f"Error: <code>{e}</code>")


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
    state_txt = "🛠 <b>Maintenance mode ENABLED</b>\n<i>New jobs are rejected; running jobs continue.</i>" if new_state else "✅ <b>Maintenance mode DISABLED</b>\n<i>The bot accepts new jobs again.</i>"
    await message.reply_text(state_txt)


@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message):
    if not await is_admin(message.from_user.id):
        return

    if not message.reply_to_message:
        await message.reply_text("Reply to a message to broadcast it.")
        return

    query = await full_userbase()
    broadcast_msg = message.reply_to_message

    total = 0
    successful = 0
    blocked = 0
    deleted = 0
    unsuccessful = 0
    edit = 0

    pls_wait = await message.reply(
        "<i>Select broadcast type</i>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📢 Normal", callback_data="broadcast_normal"),
                    InlineKeyboardButton("📌 Pin", callback_data="broadcast_pin"),
                ]
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
        await pls_wait.edit("<i>⏰ Timed out. Please try again.</i>")
        return

    await callback.answer()

    pin = 1 if callback.data == "broadcast_pin" else 0
    await pls_wait.edit("<i>📤 Broadcast started...</i>")

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
            status = f"""<b><u>Broadcast in progress</u>

Total Users: <code>{total}</code>
Successful: <code>{successful}</code>
Blocked Users: <code>{blocked}</code>
Deleted Accounts: <code>{deleted}</code>
Unsuccessful: <code>{unsuccessful}</code></b>"""
            await pls_wait.edit_text(status)

    status = f"""<b><u>Broadcast Completed</u>

Total Users: <code>{total}</code>
Successful: <code>{successful}</code>
Blocked Users: <code>{blocked}</code>
Deleted Accounts: <code>{deleted}</code>
Unsuccessful: <code>{unsuccessful}</code></b>"""
    await pls_wait.edit_text(status)


@Client.on_message(filters.command("admin") & filters.private)
async def admin(client, message):
    if message.from_user.id != OWNER_ID:
        return

    a = await get_variable("admin", [])
    txt = (
        f"<blockquote expandable>💠 𝐀𝐃𝐌𝐈𝐍 𝐏𝐀𝐍𝐄𝐋  ♻️\n</blockquote>\n"
        f"<blockquote expandable>🚩 𝐀𝐃𝐌𝐈𝐍 :- {a}\n</blockquote>\n"
        f"<blockquote expandable>⚠️ 𝐍𝐎𝐓𝐄 - ADMINS CAN USE ALL BOT COMMANDS EXCEPT FSUB, ADMIN ‼️</blockquote>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("𝐀𝐃𝐃 𝐀𝐃𝐌𝐈𝐍", callback_data="admin_add"),
                InlineKeyboardButton("𝐑𝐄𝐌𝐎𝐕𝐄 𝐀𝐃𝐌𝐈𝐍", callback_data="admin_rem"),
            ],
            [
                InlineKeyboardButton("ϲℓοѕє", callback_data="cb_close"),
            ],
        ]
    )
    await message.reply_photo(
        photo="https://i.ibb.co/kVwykh4J/ce566244dba9.jpg",
        caption=txt,
        reply_markup=keyboard,
    )


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


@Client.on_callback_query(filters.regex("^admin_"))
async def admin2(client, query):
    uid = query.from_user.id
    user_id = uid

    if uid != OWNER_ID:
        await query.answer(
            "❌ ϐακκα!, γου αяє иοτ αℓℓοωє∂ το υѕє τнє ϐυττοи", show_alert=True
        )
        return

    action = query.data.split("_")[1]

    txt = (
        "<blockquote expandable>⚠️ <b>𝖣𝗈 𝖮𝗇𝖾 𝖡𝖾𝗅𝗈𝗐</b> ⚠️</blockquote>\n"
        "<blockquote expandable><i>🔱 𝖥𝗈𝗋𝗐𝖺𝗋𝖽 𝖠 𝖬𝖾𝗌𝗌𝖺𝗀𝖾 𝖥𝗋𝗈𝗆 𝖠𝖽𝗆𝗂𝗇</i></blockquote>\n"
        "<blockquote expandable><i>💠 𝖲𝖾𝗇𝖽 𝖬𝖾 𝖠𝖽𝗆𝗂𝗇 𝖨𝖣</i></blockquote>"
        "<blockquote>♨️ 𝗠𝗔𝗞𝗘 𝗦𝗨𝗥𝗘 𝗔𝗗𝗠𝗜𝗡 𝗜𝗗 𝗜𝗦 𝗩𝗔𝗟𝗜𝗗 ♨️</blockquote>"
    )

    if action == "add":
        while True:
            b = await client.send_message(
                uid,
                text=txt,
                reply_markup=ReplyKeyboardMarkup(
                    [["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True
                ),
            )
            try:
                a = await client.listen(chat_id=uid, timeout=30)
            except ListenerTimeout:
                await client.send_message(
                    chat_id=uid,
                    text="⏳ Timeout! Admin Setup cancelled.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await b.delete()
                break

            if a.text and a.text.lower() == "❌ cancel":
                await client.send_message(
                    chat_id=uid,
                    text="❌ Admin setup cancelled.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await b.delete()
                break

            chat_id, err = await _parse_target_input(a)
            if err:
                await client.send_message(user_id, err)
                await b.delete()
                continue

            admin1 = await get_variable("admin", [])

            if chat_id in admin1:
                await client.send_message(user_id, "User is already admin; resend a correct ID...")
                await b.delete()
                continue
            admin1.append(chat_id)

            await set_variable("admin", admin1)
            invalidate_user_caches()
            await b.delete()
            await client.send_message(
                user_id,
                f"✅ User {chat_id} added to admins.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await admin(client, query.message)
            await query.message.delete()
            break

    elif action == "rem":
        while True:
            b = await client.send_message(
                uid,
                text=txt,
                reply_markup=ReplyKeyboardMarkup(
                    [["❌ Cancel"]], one_time_keyboard=True, resize_keyboard=True
                ),
            )
            try:
                a = await client.listen(chat_id=uid, timeout=30)
            except ListenerTimeout:
                await client.send_message(
                    chat_id=uid,
                    text="⏳ Timeout! Admin Setup cancelled.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await b.delete()
                break

            if a.text and a.text.lower() == "❌ cancel":
                await client.send_message(
                    chat_id=uid,
                    text="❌ Admin setup cancelled.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await b.delete()
                break

            chat_id, err = await _parse_target_input(a)
            if err:
                await client.send_message(user_id, err)
                await b.delete()
                continue

            admin1 = await get_variable("admin", [])

            if chat_id not in admin1:
                await client.send_message(user_id, "User is not admin; resend a correct ID....")
                await b.delete()
                continue
            admin1.remove(chat_id)

            await set_variable("admin", admin1)
            invalidate_user_caches()
            await b.delete()
            await client.send_message(
                user_id,
                f"✅ User {chat_id} removed from admins.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await admin(client, query.message)
            await query.message.delete()
            break

    else:
        await query.answer("Invalid action.", show_alert=True)
