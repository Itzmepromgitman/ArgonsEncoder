# Developed by ARGON telegram: @REACTIVEARGON
import os
import time
from html import escape

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import BOT_NAME, BOT_VERSION, LOG_DELIVERIES
from bot.decorator import is_banned, task
from bot.logger import LOGGER
from bot.utils.ui import ICONS, back_btn, btn, close_btn, safe_edit
from database import add_user, count_users, forget_user_data, get_stats

log = LOGGER(__name__)


async def _safe_callback_answer(callback_query, text=None, show_alert=False):
    try:
        await callback_query.answer(text, show_alert=show_alert)
    except Exception as exc:
        log.debug(f"Callback answer was no longer available: {exc}")


BOT_START_TIME = time.time()

START_IMG = "https://i.ibb.co/RGJnsfC6/monkey-d-luffy-red-3840x2160-24473.png"

START_TEXT = f"""{ICONS.encode} <b>{BOT_NAME}</b>

Send or forward a supported video. Your saved profile is applied automatically,
then the result is returned to this chat.

<blockquote>⚡ H.264 · HEVC · VP9 · AV1 · remux
📋 Persistent queue with pause, resume, and cancel
💧 Watermarks · thumbnails · trim · metadata</blockquote>

{ICONS.settings} Change the profile with <code>/settings</code>
{ICONS.queue} Track work with <code>/queue</code>
"""

HELP_TEXT = f"""{ICONS.help} <b>How to use {BOT_NAME}</b>

<blockquote expandable><b>🎬 Encoding</b>
Send or forward a video → it downloads → encodes → you get the file.

<b>⚡ Quick commands</b>
<code>/start</code> — Home
<code>/settings</code> — Codec, CRF, audio, trim, watermark…
<code>/queue</code> — Your jobs (tap 🚫 to cancel)
<code>/status</code> — Your jobs + server load
<code>/stats</code> — Global encode stats
<code>/ss</code> — Screenshots (reply to a video)
<code>/cancel &lt;id&gt;</code> — Cancel a job by ID
<code>/clear</code> — Clear your queued jobs
<code>/forget</code> — Delete your saved settings and personal assets
<code>/features</code> — Full feature list
<code>/help</code> — This manual</blockquote>

<b>Typical flow:</b> <code>/settings</code> → upload video → track in <code>/queue</code> → collect result.
"""

ABOUT_TEXT = f"""{ICONS.about} <b>About {BOT_NAME}</b>

<blockquote>Version: <code>{BOT_VERSION}</code>
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
• Failed deliveries survive restart with a 24-hour retry window

<b>🎨 UX</b>
• One-tap Fast / Balanced / Compact profiles
• Compact progress cards (bar, size, ETA, speed, FPS)
• Friendly errors with a Details button
• One-tap /forget privacy control
• Unified queue view, live /status, real /stats

<b>🛠 Admin</b>
• Maintenance mode, ban/unban, broadcast
• Owner dashboard, /shell, /restart, /log</blockquote>
"""


def _main_menu_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn(f"{ICONS.queue} Queue", "cb_queue_hint"), btn(f"{ICONS.settings} Settings", "cb_open_settings")],
            [btn(f"{ICONS.tutorial} Quick guide", "cb_tutorial"), btn(f"{ICONS.status} Status", "cb_status")],
            [btn(f"{ICONS.help} Help", "cb_help"), btn(f"{ICONS.stats} Stats", "cb_stats")],
            [btn("⚡ Fast", "cb_profile_fast"), btn("⚖️ Balanced", "cb_profile_balanced"), btn("💾 Compact", "cb_profile_compact")],
            [btn(f"{ICONS.about} About", "cb_about"), close_btn()],
        ]
    )


@Client.on_message(filters.command("start") & filters.private)
@task
async def start(client, message, query=False, payload: str = ""):
    is_new_user = False
    if not query:
        from database import present_user

        user_id = message.from_user.id
        presence = await present_user(user_id)
        if presence is None:
            await message.reply_text(
                "⚠️ <b>Profile state is temporarily unavailable.</b>\n"
                "<i>Please try again in a moment.</i>"
            )
            return
        is_new_user = not presence
        if is_new_user:
            if not await add_user(user_id, allow_tombstone=True):
                await message.reply_text(
                    "⚠️ <b>Profile state is temporarily unavailable.</b>\n"
                    "<i>Please try again in a moment.</i>"
                )
                return

            if LOG_DELIVERIES:
                try:
                    from bot.config import LOG_CHANNEL

                    user = message.from_user
                    user_link = (
                        f"<a href='tg://user?id={user_id}'>{escape(str(user.first_name))}</a>"
                    )
                    username = f"@{user.username}" if user.username else "No username"

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
        if any(
            getattr(message, attribute, None)
            for attribute in ("photo", "video", "document", "audio", "animation")
        ):
            try:
                await message.edit_caption(
                    caption=START_TEXT, reply_markup=buttons
                )
            except Exception:
                await message.reply_text(START_TEXT, reply_markup=buttons)
        else:
            edited = await safe_edit(message, START_TEXT, buttons)
            if not edited:
                await message.reply_text(START_TEXT, reply_markup=buttons)
    elif is_new_user:
        try:
            await message.reply_photo(
                photo=START_IMG, caption=START_TEXT, reply_markup=buttons
            )
        except Exception as exc:
            log.warning(f"Welcome image unavailable: {exc}")
            await message.reply_text(START_TEXT, reply_markup=buttons)
    else:
        await message.reply_text(START_TEXT, reply_markup=buttons)


@Client.on_message(filters.command("help") & filters.private)
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
    users = await count_users()
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
        f"<blockquote>👥 Users: <code>{users:,}</code>\n"
        f"🎬 Total encodes: <code>{total_encodes:,}</code>\n"
        f"📥 Processed: <code>{humanbytes(in_bytes)}</code>\n"
        f"📤 Produced: <code>{humanbytes(out_bytes)}</code>\n"
        f"🗜 Space saved: <code>{saved:.1f}%</code>\n"
        f"⏱ Uptime: <code>{uptime // 3600:.0f}h {(uptime % 3600) // 60:.0f}m</code></blockquote>"
    )


@Client.on_message(filters.command("forget") & filters.private)
async def forget_command(client, message):
    await message.reply_text(
        "🗑 <b>Delete your bot data?</b>\n"
        "<blockquote>This removes your settings, personal assets, statistics, "
        "queued jobs, and upload recovery entries. It cannot be undone.</blockquote>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Delete my data", callback_data="cb_forget_confirm"),
                    close_btn("Cancel"),
                ]
            ]
        ),
    )


@Client.on_message(filters.command("stats") & filters.private)
async def stats_command(client, message):
    stats_text = await _stats_text()
    await message.reply_text(
        text=stats_text,
        reply_markup=InlineKeyboardMarkup([[close_btn()]]),
    )


@Client.on_message(filters.command("features") & filters.private)
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


@Client.on_callback_query(filters.regex("^cb_") & filters.private)
async def handle_callbacks(client, callback_query: CallbackQuery):
    data = callback_query.data
    message = callback_query.message
    if (
        message is None
        or getattr(getattr(message, "chat", None), "id", None)
        != callback_query.from_user.id
    ):
        await _safe_callback_answer(callback_query, "This panel belongs to another chat.", show_alert=True)
        return
    if await is_banned(callback_query.from_user.id):
        await _safe_callback_answer(callback_query, "You are banned from using this bot.", show_alert=True)
        return

    try:
        if data == "cb_start":
            await _safe_callback_answer(callback_query)
            await start(client, message, query=True)
            return

        elif data == "cb_help":
            await _safe_callback_answer(callback_query)
            await _edit_or_reply(message, HELP_TEXT, InlineKeyboardMarkup(
                [
                    [btn(f"{ICONS.settings} Open Settings", "cb_open_settings")],
                    [back_btn()],
                ]
            ))

        elif data == "cb_about":
            await _safe_callback_answer(callback_query)
            await _edit_or_reply(message, ABOUT_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_tutorial":
            await _safe_callback_answer(callback_query)
            await _edit_or_reply(message, TUTORIAL_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_features":
            await _safe_callback_answer(callback_query)
            await _edit_or_reply(message, FEATURES_TEXT, InlineKeyboardMarkup([[back_btn()]]))

        elif data == "cb_stats":
            await _safe_callback_answer(callback_query)
            stats_text = await _stats_text()
            await _edit_or_reply(message, stats_text, InlineKeyboardMarkup([[back_btn()]]))

        elif data in {"cb_profile_fast", "cb_profile_balanced", "cb_profile_compact"}:
            from bot.utils.settings import apply_quality_preset, normalize_settings
            from database import get_user_settings, update_user_settings

            profile = data.removeprefix("cb_profile_")
            settings = normalize_settings(await get_user_settings(callback_query.from_user.id))
            settings = apply_quality_preset(settings, profile)
            saved = await update_user_settings(callback_query.from_user.id, settings)
            if saved:
                await _safe_callback_answer(callback_query,
                    f"✅ {profile.title()} profile applied", show_alert=False
                )
                await _edit_or_reply(message, START_TEXT, _main_menu_buttons())
            else:
                await _safe_callback_answer(callback_query, "⚠️ Could not save profile", show_alert=True)
            return

        elif data == "cb_encode_hint":
            await _safe_callback_answer(callback_query,
                "Just send or forward any video file here — encoding starts automatically!",
                show_alert=True,
            )
            return

        elif data == "cb_status":
            await _safe_callback_answer(callback_query)
            from plugins.queue import _status_card

            text, buttons = _status_card(
                callback_query.from_user.id, home_button=True
            )
            await _edit_or_reply(message, text, buttons)

        elif data == "cb_open_settings":
            # Open the real settings menu in-place instead of telling the user
            # to type a command (that was a dead-end UX).
            await _safe_callback_answer(callback_query)
            from plugins.settings import render_settings_menu

            await render_settings_menu(
                client, message, query=True, user_id=callback_query.from_user.id
            )
            return

        elif data == "cb_forget_confirm":
            await _safe_callback_answer(callback_query, "Deleting your data…")
            deleted = await forget_user_data(callback_query.from_user.id)
            if deleted:
                await _edit_or_reply(
                    message,
                    "🗑 <b>Your data was deleted.</b>\n"
                    "<i>Settings, personal assets, statistics, and queued work "
                    "were removed. Send /start for a fresh profile.</i>",
                    InlineKeyboardMarkup([[btn(f"{ICONS.home} Home", "cb_start")]]),
                )
            else:
                await _edit_or_reply(
                    message,
                    "⚠️ <b>Deletion failed.</b>\n<i>Please try again later.</i>",
                    InlineKeyboardMarkup([[close_btn()]]),
                )
            return

        elif data == "cb_close":
            await _safe_callback_answer(callback_query)
            try:
                await message.delete()
            except Exception:
                await safe_edit(message, f"{ICONS.close} <b>Closed.</b>\n<i>Send /start to open the menu again.</i>",
                                InlineKeyboardMarkup([[btn(f"{ICONS.home} Home", "cb_start")]]))
            return

        elif data == "cb_xfer_cancel":
            from bot.func.pyroutils.progress import flag_cancel

            flag_cancel(message)
            await _safe_callback_answer(callback_query, "❌ Cancelling transfer…")
            return

        elif data == "cb_queue_hint":
            await _safe_callback_answer(callback_query)
            from plugins.queue import _queue_keyboard, _user_queue_view

            jobs, text = _user_queue_view(callback_query.from_user.id)
            from bot.func.upload_manager import upload_manager

            keyboard = _queue_keyboard(
                jobs,
                upload_cancel=bool(
                    upload_manager.get_user_jobs(callback_query.from_user.id)
                ),
            )
            if any(
                getattr(message, attribute, None)
                for attribute in ("photo", "video", "document", "audio", "animation")
            ):
                try:
                    await message.edit_caption(caption=text, reply_markup=keyboard)
                except Exception:
                    await message.reply_text(text=text, reply_markup=keyboard)
            else:
                await safe_edit(message, text, keyboard)

        elif data.startswith("cb_err_"):
            from bot.func.encode import ERROR_DETAILS, ERROR_DETAILS_OWNER

            job_id = data.replace("cb_err_", "", 1)
            if ERROR_DETAILS_OWNER.get(job_id) != callback_query.from_user.id:
                await _safe_callback_answer(callback_query, "Details are unavailable.", show_alert=True)
                return
            detail = ERROR_DETAILS.get(job_id, "No details available.")
            # Telegram callback answers are capped at 200 chars.
            await _safe_callback_answer(callback_query, detail[:190], show_alert=True)
            return

        elif data.startswith("cb_retry_upload_"):
            from bot.func.encode import (
                UPLOAD_RETRY,
                _persist_upload_retries,
                _upload_video,
                claim_upload_retry,
                complete_upload_retry_claim,
                restore_upload_retry,
            )
            from bot.func.upload_manager import upload_manager
            from bot.func.queue_manager import queue_manager
            from database import is_user_tombstoned, privacy_admission_lock

            error_key = data.replace("cb_retry_upload_", "", 1)
            ctx = UPLOAD_RETRY.get(error_key)
            if not ctx:
                await _safe_callback_answer(callback_query,
                    "⚠️ Retry unavailable — this attempt already expired.",
                    show_alert=True,
                )
                return
            if callback_query.from_user.id != ctx.get("user_id"):
                await _safe_callback_answer(callback_query, "❌ This upload does not belong to you.", show_alert=True)
                return
            if await is_user_tombstoned(callback_query.from_user.id):
                await _safe_callback_answer(callback_query, "This recovery is no longer available.", show_alert=True)
                return
            claimed = await claim_upload_retry(error_key, callback_query.from_user.id)
            if not claimed:
                await _safe_callback_answer(
                    callback_query,
                    "⚠️ This retry was already claimed or expired.",
                    show_alert=True,
                )
                return
            ctx = claimed
            try:
                retry_age = time.time() - float(ctx.get("created_at", time.time()))
            except (TypeError, ValueError):
                retry_age = 0
            if retry_age > 24 * 60 * 60:
                deletion_ok = True
                for item in ctx.get("output_files", []):
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                if not deletion_ok:
                    await restore_upload_retry(error_key, ctx)
                    await _safe_callback_answer(
                        callback_query,
                        "⚠️ Expired output could not be removed; it remains tracked.",
                        show_alert=True,
                    )
                    return
                reserved = int(ctx.get("reserved_bytes", 0) or 0)
                if reserved:
                    await queue_manager.release_disk(reserved)
                await complete_upload_retry_claim(error_key)
                if not _persist_upload_retries():
                    log.warning("Expired upload manifest could not be persisted")
                await _safe_callback_answer(callback_query,
                    "⌛ This recovery file expired after 24 hours.", show_alert=True
                )
                return
            outputs = [
                item
                for item in ctx.get("output_files", [])
                if os.path.isfile(item.get("file_path", ""))
            ]
            if not outputs:
                reserved = int(ctx.get("reserved_bytes", 0) or 0)
                if reserved:
                    await queue_manager.release_disk(reserved)
                await complete_upload_retry_claim(error_key)
                if not _persist_upload_retries():
                    log.warning("Missing upload manifest could not be persisted")
                await _safe_callback_answer(callback_query,
                    "⚠️ Retry unavailable — the encoded file expired.",
                    show_alert=True,
                )
                return

            await _safe_callback_answer(callback_query, "🔁 Upload re-queued…")
            try:
                await message.edit(
                    f"{ICONS.upload} <b>Upload re-queued</b>\n"
                    "<i>Your file will be delivered when an upload slot is free.</i>"
                )
            except Exception:
                pass

            async def retry_worker(**_kwargs):
                await _upload_video(**ctx)

            try:
                await upload_manager.add_upload_job(
                    int(ctx.get("user_id", callback_query.from_user.id)),
                    retry_worker,
                    output_files=outputs,
                    reserved_bytes=int(ctx.get("reserved_bytes", 0) or 0),
                    source_job_id=str(ctx.get("job_id", error_key)),
                    codec=ctx.get("codec", "Unknown"),
                    crf=ctx.get("crf", "N/A"),
                    preset=ctx.get("preset", "N/A"),
                    resolution=ctx.get("resolution", "N/A"),
                    thumb=ctx.get("thumb"),
                    display_name=ctx.get("display_name"),
                    delivery_settings=ctx.get("delivery_settings"),
                )
                await complete_upload_retry_claim(error_key)
            except Exception as exc:
                cleanup_failed = False
                async with privacy_admission_lock:
                    state = await is_user_tombstoned(callback_query.from_user.id)
                    if not state:
                        await restore_upload_retry(error_key, ctx)
                        if not _persist_upload_retries():
                            log.warning("Restored retry manifest could not be persisted")
                    else:
                        deletion_ok = True
                        for item in ctx.get("output_files", []):
                            path = item.get("file_path")
                            if path and os.path.isfile(path):
                                try:
                                    os.remove(path)
                                except OSError:
                                    deletion_ok = False
                        if deletion_ok:
                            reserved = int(ctx.get("reserved_bytes", 0) or 0)
                            if reserved:
                                await queue_manager.release_disk(reserved)
                        else:
                            cleanup_failed = True
                            await restore_upload_retry(error_key, ctx)
                        if not _persist_upload_retries():
                            log.warning("Tombstoned retry cleanup was not persisted")
                        state = not deletion_ok
                await _safe_callback_answer(
                    callback_query,
                    "⚠️ Could not re-queue delivery; the recovery file is still available."
                    if not state or cleanup_failed
                    else "🗑 This recovery was removed with your bot data.",
                    show_alert=True,
                )
                log.error(f"Could not re-queue upload retry {error_key}: {exc}")
            return

        else:
            await _safe_callback_answer(callback_query)
            return

    except Exception as e:
        log.error(f"Callback '{data}' failed: {e}")
        try:
            await _safe_callback_answer(callback_query, "⚠️ Action failed — please try again.", show_alert=True)
        except Exception:
            pass
        return

    return
