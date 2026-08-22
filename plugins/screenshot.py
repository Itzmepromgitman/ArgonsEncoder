# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import time
from html import escape
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto

from bot.decorator import task
from bot.func.encode import safe_download_media, sanitize_filename
from bot.logger import LOGGER

log = LOGGER(__name__)


async def _run_exec(*args):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return await proc.communicate()


@Client.on_message(filters.command("ss"))
@task
async def screenshot_command(client: Client, message, query=False):
    if not message.reply_to_message:
        await message.reply_text("❌ <b>Reply to a video to generate screenshots.</b>")
        return

    target_msg = message.reply_to_message
    if not (target_msg.video or target_msg.document):
        await message.reply_text("❌ <b>Reply to a valid video file.</b>")
        return

    doc_mime = getattr(target_msg.document, "mime_type", "") or ""
    if target_msg.document and "video" not in doc_mime:
        await message.reply_text("❌ <b>File is not a video.</b>")
        return

    status_msg = await message.reply_text("📥 <b>Downloading Video...</b>")

    downloads_dir = Path("downloads")
    downloads_dir.mkdir(exist_ok=True)

    file_name = "video.mp4"
    if target_msg.video:
        file_name = target_msg.video.file_name or file_name
    elif target_msg.document:
        file_name = target_msg.document.file_name or file_name

    safe_filename = sanitize_filename(file_name)
    file_path = downloads_dir / f"ss_{int(time.time())}_{safe_filename}"

    downloaded_path = None
    screenshots = []

    try:
        last_edit = {"t": time.time()}

        async def progress(current, total):
            now = time.time()
            if now - last_edit["t"] > 3 and total > 0:  # Update every 3s
                last_edit["t"] = now
                try:
                    pct = current * 100 / total
                    await status_msg.edit(f"📥 <b>Downloading...</b> {pct:.1f}%")
                except Exception:
                    pass

        # Route through the shared download limiter instead of raw download_media.
        downloaded_path = await safe_download_media(
            client, target_msg, str(file_path), status_msg
        )

        if not downloaded_path:
            await status_msg.edit("❌ <b>Download Failed.</b>")
            return

        await status_msg.edit("📸 <b>Generating Screenshots...</b>")

        duration = 0
        if target_msg.video:
            duration = target_msg.video.duration or 0

        if not duration:
            out, _ = await _run_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                downloaded_path,
            )
            try:
                duration = float(out.decode().strip())
            except ValueError:
                await status_msg.edit("❌ <b>Could not determine video duration.</b>")
                return

        timestamps = [
            duration * 0.2,
            duration * 0.35,
            duration * 0.5,
            duration * 0.65,
            duration * 0.8,
        ]

        for i, ts in enumerate(timestamps):
            ss_path = str(file_path.parent / f"ss_{i}_{file_path.stem}.jpg")
            try:
                await _run_exec(
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{ts:.2f}",
                    "-i",
                    downloaded_path,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    ss_path,
                )
            except Exception as e:
                log.warning(f"Screenshot {i} failed: {e}")
                continue

            if os.path.exists(ss_path):
                screenshots.append(ss_path)

        if not screenshots:
            await status_msg.edit("❌ <b>Failed to generate screenshots.</b>")
            return

        await status_msg.edit("📤 <b>Uploading Screenshots...</b>")

        media_group = [
            InputMediaPhoto(ss, caption=f"⏱ Timestamp: {timestamps[i]:.1f}s")
            for i, ss in enumerate(screenshots)
        ]

        media_group[0].caption = (
            f"📸 <b>Screenshots</b>\n"
            f"📁 <code>{escape(file_name)}</code>\n"
            f"⏱ Duration: {duration:.1f}s"
        )

        await message.reply_media_group(media_group)
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        log.error(f"Screenshot error: {e}")
        try:
            await status_msg.edit(f"❌ <b>Error:</b> <code>{escape(str(e))[:200]}</code>")
        except Exception:
            pass

    finally:
        for p in ([downloaded_path] if downloaded_path else []) + screenshots:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
