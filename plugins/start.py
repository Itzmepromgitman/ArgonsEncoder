# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.config import BOT_NAME
from bot.decorator import task
from bot.logger import LOGGER
from bot.utils.ui import ICONS, back_btn, btn, close_btn, safe_edit
from database import full_userbase, add_user, get_stats

log = LOGGER(__name__)

BOT_START_TIME = time.time()

START_IMG = "https://i.ibb.co/RGJnsfC6/monkey-d-luffy-red-3840x2160-24473.png"

START_TEXT = f"""{ICONS.encode} <b>{BOT_NAME}</b>

Send me any video — I'll encode it with your saved settings automatically.

<blockquote>⚡ FFmpeg engine · x264 · x265 · VP9 · AV1
🧠 Smart queue with pause / resume / auto-restore
💧 Watermarks, thumbnails &amp; metadata support</blockquote>

{ICONS.settings} Tweak quality any time with <code>/settings</code>
Just upload a file to begin 👇
"""

HELP_TEXT = f"""{ICONS.help} <b>How to use {BOT_NAME}</b>

<blockquote expandable><b>🎬 Encoding</b>
Send or forward a video → it downloads → encodes → you get the file.

<b>⚡ Quick commands</b>
<code>/start</code> — Home
<code>/settings</code> — Codec, CRF, audio, trim, watermark…
<code>/queue</code> — Your jobs (tap 🚫 to cancel)
<code>/status</code> — Live server load
<code>/stats</code> — Global encode stats
<code>/ss</code> — Screenshots (reply to a video)
<code>/cancel &lt;id&gt;</code> — Cancel a job by ID
<code>/clear</code> — Clear your queued jobs
<code>/features</code> — Full feature list
<code>/help</code> — This manual</blockquote>

<b>Typical flow:</b> <code>/settings</code> → upload video → track in <code>/queue</code> → collect result.
"""

ABOUT_TEXT = f"""{ICONS.about} <b>About {BOT_NAME}</b>

<blockquote>Version: <code>2.2.0</code>
Engine: <code>Pyrofork + FFmpeg</code>
License: <code>GPL-3.0</code></blockquote>

<blockquote expandable><b>🔗 Links</b>
• <a href="https://t.me/fair_bots">Main channel</a>
• <a href="https://t.me/reactiveargon">Developer</a>
• <a href="https://t.me/fair_bot_support">Support group</a></blockquote>

<i>Made by @REACTIVEARGON</i>
"""

TUTORIAL_TEXT = f"""{ICONS.tutorial} <b>Quick start</b>

<blockquote expandable><b>1. Send a video</b>
Forward or upload any video file to the bot.

<b>2. Tune settings</b>
Open <code>/settings</code> to pick:
• Resolution (1080p / 720p / 480p / 360p)
• Codec &amp; quality (CRF, preset)
• Audio, trim, watermark, thumbnail

<b>3. Relax</b>
Watch live progress, pause or cancel anytime,
then collect the finished file.</blockquote>

💡 Tip: <code>/queue</code> shows every active job.
"""

FEATURES_TEXT = f"""{ICONS.features} <b>{BOT_NAME} — Features</b>

<blockquote expandable><b>🎥 Encoding</b>
• Codecs: libx264, libx265, VP9, AV1, mpeg4
• Never upscales — small sources keep their size
• Audio codec / bitrate / track select or strip
• Subtitles: copy or drop
• Sample encodes, trim window, remux mode
• Output rename patterns · video or document delivery
• Auto thumbnails from real frames

<b>⚡ Queue</b>
• Concurrent workers, per-user limits
• Pause / resume / cancel from the progress card
• Queue persists and auto-restores after restart

<b>🎨 UX</b>
• Compact progress cards (bar, size, ETA, speed, FPS)
• Friendly errors with a Details button
• Unified queue view, live /status, real /stats

<b>🛠 Admin</b>
• Maintenance mode, ban/unban, broadcast
• Owner dashboard, /shell, /restart, /log</blockquote>
"""


def _main_menu_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn("🎬 Encode", "cb_encode_hint"),
                btn("🚀 Guide", "cb_tutorial"),
            ],
            [
                btn(f"{ICONS.settings} Settings", "cb_open_settings"),
                btn(f"{ICONS.help} Help", "cb_help"),
            ],
            [
                btn(f"{ICONS.stats} Stats", "cb_stats"),
                btn(f"{ICONS.about} About", "cb_about"),
            ],
            [
                btn(f"{ICONS.back} Home", "cb_start"),
                close_btn(),
            ],
        ]
    )


@Client.on_message(filters.command("start"))
@task
async def start(client, message, query=False, payload: str = ""):
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
                    f"🆕 <b>New user started the bot</b>\n\n"
                    f"👤 <b>User:</b> {user_link} (<code>{user_id}</code>)\n"
                    f"🏷️ <b>Username:</b> {username}",
                )
            except Exception as e:
                log.error(f"Failed to send new user log: {e}")

        # Deep-link payloads from inline buttons on finished encodes.
        if not payload and message.command and len(message.command) > 1:
            payload = str(message.command[1]).lower()

        if payload in ("settings", "config"):
            from plugins.settings import render_settings_menu

            await render_settings_menu(
                client, message, query=False, user_id=user_id
            )
            return
        if payload == "queue":
            from plugins.queue import queue_command

            await queue_command(client, message)
            return
        if payload in ("help", "start"):
            pass  # fall through to home menu

    buttons = _main_menu_buttons()

    if query:
        if message.photo:
            await message.edit_caption(caption=START_TEXT, reply_markup=buttons)
        else:
            await safe_edit(message, START_TEXT, buttons)
    else:
        await message.reply_photo(
            photo=START_IMG, caption=START_TEXT, reply_markup=buttons
        )


@Client.on_message(filters.command("help"))
async def help_command(client, message):
    await message.reply_text(
        text=HELP_TEXT,
        reply_markup=InlineKeyboardMarkup(
            [
                [btn(f"{ICONS.settings} Open Settings", "cb_open_settings")],
                [back_btn()],
            ]
        ),
    )


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
        f"{ICONS.stats} <b>Bot statistics</b>\n"
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
    await message.reply_text(
        text=stats_text,
        reply_markup=InlineKeyboardMarkup([[close_btn()]]),
    )


@Client.on_message(filters.command("features"))
async def features_command(client, message):
    await message.reply_text(
        text=FEATURES_TEXT,
        reply_markup=InlineKeyboardMarkup(
            [
                [btn(f"{ICONS.settings} Open Settings", "cb_open_settings")],
                [back_btn()],
            ]
        ),
    )


async def _edit_or_reply(message, text: str, buttons: InlineKeyboardMarkup):
    """Show a panel on the current message when possible; otherwise reply."""
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=buttons)
        else:
            ok = await safe_edit(message, text, buttons)
            if not ok:
                await message.reply_text(text=text, reply_markup=buttons)
    except Exception:
        try:
            await message.reply_text(text=text, reply_markup=buttons)
        except Exception as e:
            log.error(f"Panel render failed: {e}")


@Client.on_callback_query(filters.regex("^cb_"))
async def handle_callbacks(client, callback_query: CallbackQuery):
    data = callback_query.data
    message = callback_query.message

    try:
        if data == "cb_start":
            await start(client, message, query=True)

        elif data == "cb_help":
            await _edit_or_reply(message, HELP_TEXT, InlineKeyboardMarkup(
                [
                    [btn(f"{ICONS.settings} Open Settings", "cb_open_settings")],
                    [back_btn()],
                ]
            ))

        elif data == "cb_about":
            await _edit_or_reply(message, ABOUT_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_tutorial":
            await _edit_or_reply(message, TUTORIAL_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_features":
            await _edit_or_reply(message, FEATURES_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_stats":
            stats_text = await _stats_text()
            await _edit_or_reply(message, stats_text, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_encode_hint":
            await callback_query.answer(
                "Just send or forward any video file here — encoding starts automatically!",
                show_alert=True,
            )
            return

        elif data == "cb_open_settings":
            # Open the real settings menu in-place instead of telling the user
            # to type a command (that was a dead-end UX).
            await callback_query.answer()
            from plugins.settings import render_settings_menu

            await render_settings_menu(
                client, message, query=True, user_id=callback_query.from_user.id
            )
            return

        elif data == "cb_close":
            try:
                await message.delete()
            except Exception:
                await safe_edit(message, f"{ICONS.close} <b>Closed.</b>\n<i>Send /start to open the menu again.</i>",
                                InlineKeyboardMarkup([[btn(f"{ICONS.home} Home", "cb_start")]]))
            return

        elif data == "cb_xfer_cancel":
            from bot.func.pyroutils.progress import flag_cancel

            flag_cancel(message.id)
            await callback_query.answer("❌ Cancelling transfer…")
            return

        elif data == "cb_queue_hint":
            # Open the real queue view instead of a toast.
            from plugins.queue import queue_command

            await queue_command(client, message)
            return

        elif data.startswith("cb_err_"):
            from bot.func.encode import ERROR_DETAILS

            job_id = data.replace("cb_err_", "", 1)
            detail = ERROR_DETAILS.get(job_id, "No details available.")
            # Telegram callback answers are capped at 200 chars.
            await callback_query.answer(detail[:190], show_alert=True)
            return

        elif data.startswith("cb_retry_upload_"):
            from bot.func.encode import UPLOAD_RETRY, _upload_video

            error_key = data.replace("cb_retry_upload_", "", 1)
            ctx = UPLOAD_RETRY.pop(error_key, None)
            if not ctx or not os.path.isfile(ctx.get("file_path", "")):
                await callback_query.answer(
                    "⚠️ Retry unavailable — file or context expired.",
                    show_alert=True,
                )
                return
            await callback_query.answer("🔁 Retrying upload…")
            try:
                await message.edit("📤 <b>Retrying upload…</b>\n<i>Sending your encoded file.</i>")
            except Exception:
                pass
            asyncio.create_task(_upload_video(**ctx))
            return

        else:
            await callback_query.answer()
            return

    except Exception as e:
        log.error(f"Callback '{data}' failed: {e}")
        try:
            await callback_query.answer("⚠️ Action failed — please try again.", show_alert=True)
        except Exception:
            pass
        return

    try:
        await callback_query.answer()
    except Exception:
        pass
