# Developed by ARGON telegram: @REACTIVEARGON
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import BOT_NAME
from bot.decorator import task
from bot.logger import LOGGER
from database import full_userbase, add_user, get_stats

log = LOGGER(__name__)

BOT_START_TIME = time.time()

START_IMG = "https://i.ibb.co/RGJnsfC6/monkey-d-luffy-red-3840x2160-24473.png"

START_TEXT = f"""🎬 <b>{BOT_NAME}</b>

Hi! Send me any video and I'll encode it with your saved settings — automatically.

<blockquote>⚡ FFmpeg-powered (x264 · x265 · VP9 · AV1)
🧠 Smart queue with pause/resume &amp; auto-restore
💧 Watermarks, thumbnails &amp; metadata support</blockquote>

Just upload a file to begin 👇
"""

HELP_TEXT = """<blockquote><b>🛠️ Commands:</b>

<code>/start</code> - Initialize
<code>/settings</code> - Configure encoding
<code>/queue</code> - View your jobs
<code>/status</code> - Live server status
<code>/cancel</code> - Abort a job (ID from /queue)
<code>/clear</code> - Clear your jobs
<code>/stats</code> - Bot statistics
<code>/ss</code> - Screenshots (reply to video)
<code>/help</code> - This manual</blockquote>

<b>How it works:</b> send a video → it downloads → encodes with your settings → you get the file. That's it!
"""

ABOUT_TEXT = f"""
<b>🤖 System Information</b>

<b>Version:</b> <code>2.1.0 (Stable)</code>
<b>Engine:</b> <code>Pyrogram + FFmpeg</code>

<blockquote>🔗 <b>Links:</b>
• <a href="https://t.me/fair_bots">Main Channel</a>
• <a href="https://t.me/reactiveargon">Developer</a>
• <a href="https://t.me/fair_bot_support">Support Group</a></blockquote>
"""

TUTORIAL_TEXT = """
<b>🚀 Quick Start Guide</b>

<blockquote><b>1. Send a Video</b>
Simply forward or upload a video file to the bot.

<b>2. Choose Settings</b>
Use <code>/settings</code> to configure:
• <b>Resolution:</b> 1080p, 720p, etc.
• <b>Codec:</b> x264, x265 (HEVC)
• <b>Watermark:</b> Add your custom branding

<b>3. Relax</b>
The bot will process your video and send it back!</blockquote>

<i>Tip: Use /queue to check progress.</i>
"""

FEATURES_TEXT = f"""<b>🚀 {BOT_NAME}</b>

<blockquote>Professional-grade Telegram encoding! 🎬

<b>Key Features:</b>

• <b>Smart Queue:</b> Auto-resume &amp; persistence
• <b>Pro Quality:</b> FFmpeg with custom presets
• <b>Total Control:</b> Watermarks, metadata, trim &amp; sample encodes

<i>Fast, stable, and fully customizable.</i>

Developed by @REACTIVEARGON</blockquote>
"""


@Client.on_message(filters.command("start"))
@task
async def start(client, message, query=False):
    if not query:
        from database import present_user

        user_id = message.from_user.id
        if not await present_user(user_id):
            await add_user(user_id)

            try:
                from bot.config import LOG_CHANNEL

                user = message.from_user
                user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"
                username = f"@{user.username}" if user.username else "No Username"

                await client.send_message(
                    LOG_CHANNEL,
                    f"🆕 <b>New User Started Bot</b>\n\n"
                    f"👤 <b>User:</b> {user_link} (<code>{user_id}</code>)\n"
                    f"🏷️ <b>Username:</b> {username}",
                )
            except Exception as e:
                log.error(f"Failed to send new user log: {e}")

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 Encode", callback_data="cb_encode_hint"),
                InlineKeyboardButton("🚀 Quick Start", callback_data="cb_tutorial"),
                InlineKeyboardButton("📚 Help", callback_data="cb_help"),
            ],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="cb_open_settings"),
                InlineKeyboardButton("📊 Stats", callback_data="cb_stats"),
                InlineKeyboardButton("ℹ️ About", callback_data="cb_about"),
            ],
            [
                InlineKeyboardButton("❌ Close", callback_data="cb_close"),
            ],
        ]
    )

    if query:
        await message.edit_caption(caption=START_TEXT, reply_markup=buttons)
    else:
        await message.reply_photo(
            photo=START_IMG, caption=START_TEXT, reply_markup=buttons
        )


@Client.on_message(filters.command("help"))
async def help_command(client, message):
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
    )
    await message.reply_text(text=HELP_TEXT, reply_markup=buttons)


async def _stats_text() -> str:
    users = await full_userbase()
    stats = await get_stats()
    total_encodes = int(stats.get("total_encodes", 0) or 0)
    in_bytes = int(stats.get("in_bytes", 0) or 0)
    out_bytes = int(stats.get("out_bytes", 0) or 0)
    saved = 0
    if in_bytes > 0 and out_bytes > 0 and in_bytes > out_bytes:
        saved = (1 - out_bytes / in_bytes) * 100

    from bot.utils.format import humanbytes

    uptime = time.time() - BOT_START_TIME

    return (
        f"<b>📊 Bot Statistics</b>\n"
        f"<blockquote>👥 Users: <code>{len(users):,}</code>\n"
        f"🎬 Total encodes: <code>{total_encodes:,}</code>\n"
        f"📥 Processed: <code>{humanbytes(in_bytes)}</code>\n"
        f"📤 Produced: <code>{humanbytes(out_bytes)}</code>\n"
        f"🗜 Space saved: <code>{saved:.1f}%</code>\n"
        f"⏱ Uptime: <code>{uptime // 3600:.0f}h {(uptime % 3600) // 60:.0f}m</code></blockquote>"
    )


@Client.on_message(filters.command("stats"))
async def stats_command(client, message):
    stats_text = await _stats_text()
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data="cb_close")]]
    )
    await message.reply_text(text=stats_text, reply_markup=buttons)


@Client.on_message(filters.command("features"))
async def features_command(client, message):
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data="cb_close")]]
    )
    await message.reply_text(text=FEATURES_TEXT, reply_markup=buttons)


@Client.on_callback_query(filters.regex("^cb_"))
async def handle_callbacks(client, callback_query: CallbackQuery):
    data = callback_query.data
    message = callback_query.message
    is_photo = bool(message.photo)

    try:
        if data == "cb_start":
            if is_photo:
                await start(client, message, query=True)
            else:
                await start(client, message, query=False)

        elif data == "cb_help":
            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
            )
            if is_photo:
                await message.edit_caption(caption=HELP_TEXT, reply_markup=buttons)
            else:
                await message.edit_text(text=HELP_TEXT, reply_markup=buttons)

        elif data == "cb_about":
            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
            )
            if is_photo:
                await message.edit_caption(caption=ABOUT_TEXT, reply_markup=buttons)
            else:
                await message.edit_text(text=ABOUT_TEXT, reply_markup=buttons)

        elif data == "cb_tutorial":
            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
            )
            if is_photo:
                await message.edit_caption(caption=TUTORIAL_TEXT, reply_markup=buttons)
            else:
                await message.edit_text(text=TUTORIAL_TEXT, reply_markup=buttons)

        elif data == "cb_stats":
            stats_text = await _stats_text()
            buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
            )
            if is_photo:
                await message.edit_caption(caption=stats_text, reply_markup=buttons)
            else:
                await message.edit_text(text=stats_text, reply_markup=buttons)

        elif data == "cb_encode_hint":
            await callback_query.answer(
                "Just send or forward any video file to this chat — encoding starts automatically!",
                show_alert=True,
            )
            return

        elif data == "cb_open_settings":
            await callback_query.answer()
            await client.send_message(
                callback_query.from_user.id,
                "⚙️ Send <code>/settings</code> to open your encoding settings.",
            )
            return

        elif data == "cb_close":
            await message.delete()

        elif data.startswith("cb_err_"):
            from bot.func.encode import ERROR_DETAILS

            job_id = data.replace("cb_err_", "", 1)
            detail = ERROR_DETAILS.get(job_id, "No details available.")
            # Telegram callback answers are capped at 200 chars.
            await callback_query.answer(detail[:190], show_alert=True)
            return

    except Exception as e:
        log.error(f"Callback '{data}' failed: {e}")
        try:
            await callback_query.answer("⚠️ Action failed.", show_alert=True)
        except Exception:
            pass
        return

    try:
        await callback_query.answer()
    except Exception:
        pass
