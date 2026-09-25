# Developed by ARGON telegram: @REACTIVEARGON
import os
import shutil
import time
import uuid
from html import escape
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import (
    DOWNLOAD_DIR,
    LOG_DELIVERIES,
    MAX_FILE_SIZE,
    MAX_MEDIA_DURATION,
    MAX_JOBS_PER_USER,
    MAX_OUTPUT_SIZE,
    MAX_OUTPUT_VARIANTS,
    MIN_FREE_DISK_BYTES,
    MIN_OUTPUT_RESERVATION_BYTES,
)
from bot.func.queue_manager import queue_manager
from bot.func.upload_manager import upload_manager
from bot.decorator import is_admin, is_banned
from bot.func.encode import encode, safe_download_media, sanitize_filename
from bot.logger import LOGGER
from bot.utils.format import humanbytes

log = LOGGER(__name__)


async def check_and_process_video_document(message: Message) -> dict:
    """Returns detailed information about the video document."""
    result = {
        "is_video_document": False,
        "is_encodable": False,
        "file_info": {},
        "encoding_ready": False,
    }

    if not message.document and not message.video:
        log.info("Message has no document or video attachment")
        return result

    doc = message.video if message.video else message.document
    result["is_video_document"] = True

    file_size = getattr(doc, "file_size", 0) or 0

    result["file_info"] = {
        "file_name": getattr(doc, "file_name", None) or f"video_{int(time.time())}.mp4",
        "file_size": file_size,
        "mime_type": getattr(doc, "mime_type", "") or "",
        "file_id": getattr(doc, "file_id", ""),
        "duration": getattr(doc, "duration", 0),
        "width": getattr(doc, "width", 0),
        "height": getattr(doc, "height", 0),
    }

    encodable_formats = {
        ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
        ".3gp", ".ogv", ".ts", ".mts", ".m2ts", ".vob", ".asf", ".rm",
        ".rmvb",
    }

    file_name = result["file_info"]["file_name"]
    if file_name:
        ext = os.path.splitext(file_name)[1].lower()
        if ext in encodable_formats:
            result["is_encodable"] = True

    mime_type = result["file_info"]["mime_type"]
    if mime_type and mime_type.startswith("video/"):
        result["is_encodable"] = True

    if result["is_encodable"] and 0 < file_size <= MAX_FILE_SIZE:
        result["encoding_ready"] = True

    return result


@Client.on_message(
    (filters.private) & (filters.document | filters.video)
)
async def enhanced_document_handler(client: Client, message: Message):
    user_id = message.from_user.id

    # Ban / maintenance gates (intake only; running jobs finish untouched).
    if await is_banned(user_id):
        await message.reply_text("🚫 <b>You are banned from using this bot.</b>")
        return

    from database import get_variable, is_user_tombstoned

    if await is_user_tombstoned(user_id):
        await message.reply_text(
            "🗑 <b>Your bot data was deleted.</b>\n"
            "<i>Send /start to create a fresh profile.</i>"
        )
        return

    if await get_variable("maintenance", False) and not await is_admin(user_id):
        await message.reply_text(
            "🛠 <b>Under maintenance</b>\n"
            "<blockquote>The bot is not accepting new jobs right now. "
            "Queued jobs are still being processed — try again soon!</blockquote>"
        )
        return

    active_jobs = len(queue_manager.get_user_jobs(user_id)) + len(
        upload_manager.get_user_jobs(user_id)
    )
    if active_jobs >= MAX_JOBS_PER_USER:
        await message.reply_text(
            f"⚠️ <b>Your queue is full</b>\n"
            f"<blockquote>{active_jobs} active job(s) · limit {MAX_JOBS_PER_USER}</blockquote>\n"
            "<i>Wait for a job to finish or use /queue to cancel one.</i>"
        )
        return

    log.info("Processing a private document/video intake")

    download_file_path = None
    download_msg = None
    reservation_bytes = 0
    reservation_active = False

    try:
        video_info = await check_and_process_video_document(message)

        if not video_info["is_video_document"]:
            await message.reply_text(
                "📄 That file isn't a video. Send MP4, MKV, AVI, MOV, WebM and similar."
            )
            return

        fi = video_info["file_info"]
        if not video_info["is_encodable"]:
            size_display = humanbytes(fi["file_size"])
            if fi["file_size"] and fi["file_size"] > MAX_FILE_SIZE:
                await message.reply_text(
                    f"⚠️ <b>File too large</b>\n"
                    f"<blockquote>📁 <code>{escape(fi['file_name'])}</code>\n"
                    f"📦 {humanbytes(fi['file_size'])} · limit {humanbytes(MAX_FILE_SIZE)}</blockquote>\n"
                    f"<i>Split the file or lower the resolution source, then retry.</i>"
                )
            else:
                await message.reply_text(
                    f"⚠️ <b>Can't encode this file</b>\n"
                    f"<blockquote>📁 <code>{escape(fi['file_name'])}</code>\n"
                    f"📦 {size_display} · "
                    f"🏷️ {escape(fi['mime_type']) or 'unknown type'}</blockquote>\n"
                    f"<i>Unsupported format. Supported: MP4, MKV, AVI, MOV, WebM and similar.</i>"
                )
            return

        if fi.get("duration", 0) and float(fi["duration"]) > MAX_MEDIA_DURATION:
            await message.reply_text(
                "⚠️ <b>Video is too long</b>\n"
                f"<i>The limit is {MAX_MEDIA_DURATION // 3600} hours.</i>"
            )
            return

        if not video_info["encoding_ready"]:
            if fi["file_size"] and fi["file_size"] > MAX_FILE_SIZE:
                await message.reply_text(
                    f"⚠️ <b>File too large</b>\n"
                    f"<i>Over the {humanbytes(MAX_FILE_SIZE)} limit.</i>"
                )
            else:
                await message.reply_text(
                    "❌ <b>File looks corrupted</b>\n"
                    "<i>Try re-uploading it. If it keeps failing, the source file may be broken.</i>"
                )
            return

        file_identity = str(fi.get("file_id", "") or "")
        source_id = file_identity or f"{user_id}:{message.chat.id}:{message.id}"
        if queue_manager.has_source(user_id, source_id):
            await message.reply_text(
                "ℹ️ <b>This file is already in your queue.</b>\n"
                "<i>Use /queue to track or cancel it.</i>"
            )
            return

        if not queue_manager.can_accept_job(user_id):
            await message.reply_text(
                "⏳ <b>The queue is full</b>\n"
                "<i>Wait for a slot or cancel an existing job from /queue.</i>"
            )
            return

        Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        free_disk = shutil.disk_usage(DOWNLOAD_DIR).free
        if free_disk < MIN_FREE_DISK_BYTES:
            await message.reply_text(
                "⚠️ <b>Server storage is temporarily low</b>\n"
                "<i>Please try again after the operator frees disk space.</i>"
            )
            return
        source_size = max(1, int(fi["file_size"]))
        duration = float(fi.get("duration", 0) or 0)
        if duration > 0:
            # 50 Mbps is the current custom-override ceiling; add container
            # overhead and cap each variant at the configured output limit.
            per_variant = int(duration * 50_000_000 / 8) + MIN_OUTPUT_RESERVATION_BYTES
        else:
            per_variant = max(MIN_OUTPUT_RESERVATION_BYTES, source_size * 3)
        per_variant = min(MAX_OUTPUT_SIZE, per_variant)
        reservation_variants = max(MAX_OUTPUT_VARIANTS, 4)
        reservation_bytes = source_size + per_variant * reservation_variants
        if not await queue_manager.reserve_disk(reservation_bytes):
            await message.reply_text(
                "⚠️ <b>Server storage is reserved for active jobs</b>\n"
                "<i>Please try again when a slot frees up.</i>"
            )
            return
        reservation_active = True

        # Log only accepted media when the operator explicitly opts in.
        if LOG_DELIVERIES:
            try:
                from bot.config import LOG_CHANNEL

                user = message.from_user
                user_link = f"<a href='tg://user?id={user_id}'>{escape(user.first_name)}</a>"
                await client.send_message(
                    LOG_CHANNEL,
                    f"📥 <b>File accepted</b>\n\n"
                    f"👤 <b>User:</b> {user_link} (<code>{user_id}</code>)\n"
                    f"📄 <b>File:</b> <code>{escape(fi['file_name'])}</code>\n"
                    f"📦 <b>Size:</b> <code>{humanbytes(fi['file_size'])}</code>",
                )
            except Exception as exc:
                log.error(f"Failed to send intake log: {exc}")

        downloads_dir = Path(DOWNLOAD_DIR)
        downloads_dir.mkdir(parents=True, exist_ok=True)

        safe_filename = sanitize_filename(fi["file_name"])
        download_file_path = (
            downloads_dir / f"{user_id}_{uuid.uuid4().hex[:6]}_{safe_filename}"
        )
        log.info(f"Download path: {download_file_path}")

        download_msg = await message.reply_text(
            "📥 <b>Downloading…</b>\n<i>Fetching your file — progress updates below.</i>"
        )
        downloaded_path = await safe_download_media(
            client, message, str(download_file_path), download_msg
        )

        if not downloaded_path:
            try:
                await download_msg.edit("❌ <b>Download failed.</b> Please try again.")
            except Exception:
                pass
            return

        if not os.path.exists(downloaded_path):
            try:
                await download_msg.edit("❌ <b>Error:</b> file not found after download.")
            except Exception:
                pass
            return

        await encode(
            ffmpeg_cmd="",
            input_file=downloaded_path,
            client=client,
            user_id=user_id,
            message=download_msg,
            chat_id=message.chat.id,
            message_id=message.id,
            source_file_name=fi["file_name"],
            source_id=source_id,
            reserved_bytes=reservation_bytes,
        )
        reservation_active = False

    except Exception as exc:
        log.error(
            f"Unexpected document-handler error for user {user_id}: {exc}",
            exc_info=True,
        )
        if download_msg:
            try:
                await download_msg.edit(
                    "❌ <b>Could not start this job</b>\n"
                    "<blockquote>No upload was started. Please wait a moment and try again.</blockquote>"
                )
            except Exception:
                pass
        else:
            try:
                await message.reply_text(
                    "❌ <b>Could not start this job</b>\n"
                    "<blockquote>Please wait a moment and try again.</blockquote>"
                )
            except Exception:
                pass

        if download_file_path and os.path.exists(download_file_path):
            try:
                os.remove(download_file_path)
                log.info(f"Cleaned up file after error: {download_file_path}")
            except Exception as cleanup_error:
                log.error(f"Failed to cleanup file {download_file_path}: {cleanup_error}")
    finally:
        if reservation_active:
            await queue_manager.release_disk(reservation_bytes)
