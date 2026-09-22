# Developed by ARGON telegram: @REACTIVEARGON
import os
import time
import uuid
from html import escape
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.config import DOWNLOAD_DIR, MAX_FILE_SIZE
from bot.decorator import is_admin, is_banned
from bot.func.encode import encode, safe_download_media, sanitize_filename
from bot.logger import LOGGER

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

    from database import get_variable

    if await get_variable("maintenance", False) and not await is_admin(user_id):
        await message.reply_text(
            "🛠 <b>Under maintenance</b>\n"
            "<blockquote>The bot is not accepting new jobs right now. "
            "Queued jobs are still being processed — try again soon!</blockquote>"
        )
        return

    log.info(f"Processing document/video from user {user_id}")

    # Log to Channel
    try:
        from bot.config import LOG_CHANNEL

        user = message.from_user
        doc = message.document or message.video
        user_link = f"<a href='tg://user?id={user_id}'>{escape(user.first_name)}</a>"
        await client.send_message(
            LOG_CHANNEL,
            f"📥 <b>File Received</b>\n\n"
            f"👤 <b>User:</b> {user_link} (<code>{user_id}</code>)\n"
            f"📄 <b>File:</b> <code>{escape(getattr(doc, 'file_name', 'Unknown') or 'Unknown')}</code>",
        )
    except Exception as e:
        log.error(f"Failed to send log to channel: {e}")

    download_file_path = None
    download_msg = None

    try:
        video_info = await check_and_process_video_document(message)

        if not video_info["is_video_document"]:
            await message.reply_text(
                "📄 That file isn't a video. Send MP4, MKV, AVI, MOV, WebM and similar."
            )
            return

        fi = video_info["file_info"]
        if not video_info["is_encodable"]:
            size_mb = (fi["file_size"] or 0) / (1024 * 1024)
            limit_gb = MAX_FILE_SIZE // (1024 * 1024 * 1024)
            if fi["file_size"] and fi["file_size"] > MAX_FILE_SIZE:
                await message.reply_text(
                    f"⚠️ <b>File too large</b>\n"
                    f"<blockquote>📁 <code>{escape(fi['file_name'])}</code>\n"
                    f"📦 {size_mb:.2f} MB · limit {limit_gb} GB</blockquote>\n"
                    f"<i>Split the file or lower the resolution source, then retry.</i>"
                )
            else:
                await message.reply_text(
                    f"⚠️ <b>Can't encode this file</b>\n"
                    f"<blockquote>📁 <code>{escape(fi['file_name'])}</code>\n"
                    f"📦 {size_mb:.2f} MB · "
                    f"🏷️ {escape(fi['mime_type']) or 'unknown type'}</blockquote>\n"
                    f"<i>Unsupported format. Supported: MP4, MKV, AVI, MOV, WebM and similar.</i>"
                )
            return

        if not video_info["encoding_ready"]:
            if fi["file_size"] and fi["file_size"] > MAX_FILE_SIZE:
                await message.reply_text(
                    f"⚠️ <b>File too large</b>\n"
                    f"<i>Over the {MAX_FILE_SIZE // (1024 * 1024 * 1024)} GB limit.</i>"
                )
            else:
                await message.reply_text(
                    "❌ <b>File looks corrupted</b>\n"
                    "<i>Try re-uploading it. If it keeps failing, the source file may be broken.</i>"
                )
            return

        downloads_dir = Path(DOWNLOAD_DIR)
        downloads_dir.mkdir(exist_ok=True)

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
        )

    except Exception as e:
        log.error(f"Unexpected error in document handler for user {user_id}: {e}")
        detail = escape(str(e))[:300]
        if download_msg:
            try:
                await download_msg.edit(
                    f"❌ <b>Something went wrong</b>\n<blockquote>{detail}</blockquote>"
                )
            except Exception:
                pass
        else:
            try:
                await message.reply_text(f"❌ <b>Something went wrong</b>\n<blockquote>{detail}</blockquote>")
            except Exception:
                pass

        if download_file_path and os.path.exists(download_file_path):
            try:
                os.remove(download_file_path)
                log.info(f"Cleaned up file after error: {download_file_path}")
            except Exception as cleanup_error:
                log.error(f"Failed to cleanup file {download_file_path}: {cleanup_error}")
