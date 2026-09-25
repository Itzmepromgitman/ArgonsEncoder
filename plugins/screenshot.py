# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import glob
import json
import os
import uuid
from html import escape
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto

from bot.config import (
    DOWNLOAD_DIR,
    FFMPEG_BIN,
    FFPROBE_BIN,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_FILE_SIZE,
    MAX_MEDIA_DURATION,
    MIN_FREE_DISK_BYTES,
    MIN_OUTPUT_RESERVATION_BYTES,
)
from bot.decorator import task
from database import is_user_tombstoned, privacy_admission_lock
from bot.func.queue_manager import queue_manager
from bot.func.encode import probe_file, safe_download_media, sanitize_filename
from bot.logger import LOGGER
from bot.utils.format import humanbytes

log = LOGGER(__name__)
_SCREENSHOT_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
_ACTIVE_SCREENSHOTS: set[int] = set()
_SCREENSHOT_TASKS: dict[int, asyncio.Task] = {}


async def _run_exec(*args, timeout: int = 120) -> bytes:
    """Run a trusted media utility and surface a concise failure."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{Path(args[0]).name} timed out") from exc
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace").strip()[-300:]
        raise RuntimeError(f"{Path(args[0]).name} failed ({proc.returncode}): {tail}")
    return stdout


async def _probe_frame_rate(path: str) -> float | None:
    """Read a cheap stream-level frame-rate hint without decoding the video."""
    try:
        process = await asyncio.create_subprocess_exec(
            FFPROBE_BIN,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            return None
        stream = json.loads(stdout.decode(errors="replace"))["streams"][0]
        for key in ("avg_frame_rate", "r_frame_rate"):
            value = str(stream.get(key, "0/0"))
            numerator, _, denominator = value.partition("/")
            if numerator.isdigit() and denominator.isdigit() and int(denominator) > 0:
                fps = float(numerator) / float(denominator)
                if 0 < fps <= 240:
                    return fps
    except (OSError, ValueError, KeyError, IndexError, asyncio.TimeoutError):
        return None
    return None


@Client.on_message(filters.command("ss") & filters.private)
@task
async def screenshot_command(client: Client, message, query=False):
    if not message.reply_to_message:
        await message.reply_text(
            "📸 <b>Reply to a video</b>\n<i>I’ll sample five frames across its timeline.</i>"
        )
        return

    target_msg = message.reply_to_message
    if not (target_msg.video or target_msg.document):
        await message.reply_text("❌ <b>That attachment is not a video.</b>")
        return

    doc = target_msg.video or target_msg.document
    doc_mime = str(getattr(doc, "mime_type", "") or "")
    file_name = str(getattr(doc, "file_name", "") or "video.mp4")
    extension = Path(file_name).suffix.lower()
    supported = extension in {
        ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
        ".3gp", ".ogv", ".ts", ".mts", ".m2ts", ".vob", ".asf", ".rm", ".rmvb",
    }
    if not target_msg.video and not (doc_mime.startswith("video/") or supported):
        await message.reply_text("❌ <b>That file is not a supported video.</b>")
        return

    file_size = int(getattr(doc, "file_size", 0) or 0)
    if file_size > MAX_FILE_SIZE:
        await message.reply_text(
            "⚠️ <b>Video is too large</b>\n"
            f"<blockquote>{humanbytes(file_size)} · limit {humanbytes(MAX_FILE_SIZE)}</blockquote>"
        )
        return
    if float(getattr(doc, "duration", 0) or 0) > MAX_MEDIA_DURATION:
        await message.reply_text(
            "⚠️ <b>Video is too long</b>\n"
            f"<i>The limit is {MAX_MEDIA_DURATION // 3600} hours.</i>"
        )
        return

    user_id = message.from_user.id
    reservation_bytes = max(
        MIN_OUTPUT_RESERVATION_BYTES,
        file_size + MIN_OUTPUT_RESERVATION_BYTES,
    ) if file_size > 0 else MIN_FREE_DISK_BYTES
    reservation_active = False
    async with privacy_admission_lock:
        if await is_user_tombstoned(user_id):
            await message.reply_text(
                "🗑 <b>Your bot data was deleted.</b>\n"
                "<i>Send /start to create a fresh profile.</i>"
            )
            return
        if user_id in _ACTIVE_SCREENSHOTS:
            await message.reply_text("⏳ <b>Screenshots are already running.</b>")
            return
        _ACTIVE_SCREENSHOTS.add(user_id)
        _SCREENSHOT_TASKS[user_id] = asyncio.current_task()
        try:
            reserved = await queue_manager.reserve_disk(reservation_bytes)
        except asyncio.CancelledError:
            _ACTIVE_SCREENSHOTS.discard(user_id)
            _SCREENSHOT_TASKS.pop(user_id, None)
            raise
        if not reserved:
            _ACTIVE_SCREENSHOTS.discard(user_id)
            _SCREENSHOT_TASKS.pop(user_id, None)
            await message.reply_text(
                "⚠️ <b>Server storage is reserved</b>\n"
                "<i>Please try again when a slot frees up.</i>"
            )
            return
        reservation_active = True
    try:
        await asyncio.wait_for(_SCREENSHOT_SEMAPHORE.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        await queue_manager.release_disk(reservation_bytes)
        reservation_active = False
        _ACTIVE_SCREENSHOTS.discard(user_id)
        _SCREENSHOT_TASKS.pop(user_id, None)
        await message.reply_text("⚠️ <b>Screenshot capacity is busy.</b>\n<i>Try again shortly.</i>")
        return
    except asyncio.CancelledError:
        await queue_manager.release_disk(reservation_bytes)
        reservation_active = False
        _ACTIVE_SCREENSHOTS.discard(user_id)
        _SCREENSHOT_TASKS.pop(user_id, None)
        raise

    status_msg = None
    downloads_dir = Path(DOWNLOAD_DIR)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    file_path = downloads_dir / (
        f"ss_{message.from_user.id}_{uuid.uuid4().hex[:8]}_{sanitize_filename(file_name)}"
    )
    downloaded_path = None
    frame_paths = []

    try:
        status_msg = await message.reply_text(
            "📥 <b>Preparing screenshots…</b>\n"
            "<i>Downloading once, then extracting five frames in a single pass.</i>"
        )
        downloaded_path = await safe_download_media(
            client, target_msg, str(file_path), status_msg
        )
        if not downloaded_path:
            await status_msg.edit(
                "❌ <b>Download failed</b>\n<i>Check your connection and try again.</i>"
            )
            return

        await status_msg.edit(
            "📸 <b>Extracting frames…</b>\n<i>One optimized FFmpeg pass · five timeline samples</i>"
        )
        duration = float(getattr(doc, "duration", 0) or 0)
        if duration <= 0:
            duration = float((await probe_file(downloaded_path))["duration"])
        if duration <= 0:
            raise RuntimeError("video duration is unavailable")
        if duration > MAX_MEDIA_DURATION:
            raise RuntimeError(
                f"video duration exceeds the {MAX_MEDIA_DURATION // 3600}h limit"
            )

        requested_times = [
            duration * ratio for ratio in (0.0, 0.2, 0.4, 0.6, 0.8)
        ]
        frame_pattern = str(
            downloads_dir / f"ss_{uuid.uuid4().hex[:8]}_frame_%02d.jpg"
        )
        fps = await _probe_frame_rate(downloaded_path)
        if fps:
            max_frame = max(0, int(duration * fps) - 1)
            frame_indices = sorted(
                {
                    max(0, min(int(round(timestamp * fps)), max_frame))
                    for timestamp in requested_times
                }
            )
            frame_times = [index / fps for index in frame_indices]
            terms = "+".join(f"eq(n\\,{index})" for index in frame_indices)
            video_filter = f"select='{terms}',scale='min(1280,iw)':-2"
        else:
            # Metadata-free VFR fallback: resample the decoded stream to five
            # evenly spaced output frames instead of relying on overlapping
            # timestamp windows that can duplicate or miss samples.
            frame_times = [duration * index / 5 for index in range(5)]
            video_filter = f"fps={5 / max(duration, 0.001):.12f},scale='min(1280,iw)':-2"
        await _run_exec(
            FFMPEG_BIN,
            "-nostats",
            "-loglevel",
            "error",
            "-y",
            "-i",
            downloaded_path,
            "-vf",
            video_filter,
            "-fps_mode",
            "vfr",
            "-q:v",
            "4",
            "-frames:v",
            str(len(frame_times)),
            frame_pattern,
        )
        frame_paths = sorted(glob.glob(frame_pattern))
        if not frame_paths:
            raise RuntimeError("FFmpeg produced no frames")

        await status_msg.edit(
            f"📤 <b>Sending {len(frame_paths)} screenshots…</b>\n"
            "<i>High-quality JPEG previews are grouped in the album.</i>"
        )
        media = [
            InputMediaPhoto(
                path,
                caption=(
                    f"⏱ <code>{frame_times[index]:.1f}s</code>"
                    if index < len(frame_times)
                    else "⏱ <code>sampled frame</code>"
                ),
            )
            for index, path in enumerate(frame_paths)
        ]
        media[0].caption = (
            "📸 <b>Video screenshots</b>\n"
            f"📁 <code>{escape(file_name)}</code>\n"
            f"⏱ Duration: <code>{duration:.1f}s</code>\n"
            f"<i>{len(frame_paths)} frames sampled across the timeline</i>"
        )
        if len(media) == 1:
            await message.reply_photo(
                photo=media[0].media,
                caption=media[0].caption,
            )
        else:
            await message.reply_media_group(media)
        await status_msg.delete()
    except Exception as exc:
        log.error(f"Screenshot extraction failed: {exc}", exc_info=True)
        try:
            if status_msg:
                await status_msg.edit(
                    "❌ <b>Screenshots failed</b>\n"
                    "<blockquote>The video may be damaged or use an unsupported stream.</blockquote>"
                )
        except Exception:
            pass
    finally:
        if not frame_paths and "frame_pattern" in locals():
            frame_paths = glob.glob(frame_pattern)
        cleanup_paths = ([downloaded_path] if downloaded_path else []) + frame_paths
        for path in cleanup_paths:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        _SCREENSHOT_SEMAPHORE.release()
        if reservation_active:
            await queue_manager.release_disk(reservation_bytes)
        _ACTIVE_SCREENSHOTS.discard(locals().get("user_id", 0))
        _SCREENSHOT_TASKS.pop(locals().get("user_id", 0), None)
