# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import json
import os
import shlex
import time
import uuid
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import (
    LOG_CHANNEL,
    THUMB_DIR,
    UI_UPDATE_INTERVAL,
)
from bot.func.download_manager import download_manager
from bot.func.ffmpeg_utils import generate_ffmpeg_cmd
from bot.func.pyroutils.progress import TimeFormatter, humanbytes, progress_for_pyrogram
from bot.func.queue_manager import queue_manager
from bot.func.upload_manager import upload_manager
from bot.logger import LOGGER
from database import get_user_settings, inc_stats

log = LOGGER(__name__)

# Global registry of active encoding processes for callback handling.
# Map: job_id -> FFmpegProcess instance
active_encodings = {}

# Job-id -> short technical error detail (for the "Details" button). Capped.
ERROR_DETAILS: Dict[str, str] = {}
ERROR_DETAILS_CAP = 50


def _remember_error(key: str, detail: str):
    if len(ERROR_DETAILS) >= ERROR_DETAILS_CAP:
        ERROR_DETAILS.pop(next(iter(ERROR_DETAILS)))
    ERROR_DETAILS[key] = detail[:1500]


@dataclass
class EncodingStats:
    percent: float = 0.0
    fps: float = 0.0
    bitrate: str = "N/A"
    speed: str = "N/A"
    frame: int = 0
    total_frames: int = 0
    eta: str = "N/A"
    elapsed: str = "0s"
    size: str = "0 B"
    estimated_size: str = "0 B"
    compression: str = "1.0x"


class FFmpegProcess:
    def __init__(
        self,
        cmd: str,
        input_file: str,
        output_file: str,
        total_duration: float,
        original_size: int,
        file_name: str = "Unknown",
        codec: str = "Unknown",
        crf: str = "N/A",
        preset: str = "N/A",
        resolution: str = "N/A",
        current_step: int = 1,
        total_steps: int = 1,
        thumbnail_path: Optional[str] = None,
    ):
        self.cmd = cmd
        self.input_file = input_file
        self.output_file = output_file
        self.total_duration = total_duration
        self.original_size = original_size
        self.file_name = file_name
        self.codec = codec
        self.crf = crf
        self.preset = preset
        self.resolution = resolution
        self.current_step = current_step
        self.total_steps = total_steps
        self.thumbnail_path = thumbnail_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self.start_time = 0
        self.is_paused = False
        self.is_cancelled = False
        self.yield_queue = False
        self.stats = EncodingStats()
        self.job_id = ""
        self.message: Optional[Message] = None
        self.client: Optional[Client] = None
        self.user_id: int = 0
        self.is_viewing_queue = False
        self.stderr_tail = b""
        self._stderr_task: Optional[asyncio.Task] = None

    async def start(self):
        self.start_time = time.time()

        args = shlex.split(self.cmd)

        # Legacy command strings without an input: prepend ffmpeg + -i.
        if "-i" not in args:
            args = ["ffmpeg", "-i", self.input_file] + args

        # Keep stderr quiet and route progress to stdout so the OS pipe can
        # never fill up and deadlock the encode.
        if "-nostats" not in args:
            args.insert(1, "-nostats")
        if "-loglevel" not in args:
            args[1:1] = ["-loglevel", "error"]

        if "-progress" not in args:
            args.extend(["-progress", "pipe:1"])

        executable = args[0] if args else "ffmpeg"
        cmd_args = args[1:] if len(args) > 1 else []

        final_args = cmd_args + [self.output_file, "-y"]

        log.info(f"Starting FFmpeg: {executable} {' '.join(final_args)}")

        self.process = await asyncio.create_subprocess_exec(
            executable,
            *final_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
        )

        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self):
        """Continuously drain stderr into a bounded tail buffer."""
        try:
            while True:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    break
                self.stderr_tail = (self.stderr_tail + chunk)[-8192:]
        except Exception:
            pass

    async def _stop_stderr_task(self):
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None

    async def pause(self):
        if self.process and not self.is_paused:
            try:
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.suspend()
                    except Exception:
                        pass
                parent.suspend()
                self.is_paused = True
                self.yield_queue = True
                log.info(f"Process {self.process.pid} and children suspended.")
            except Exception as e:
                log.error(f"Failed to pause process: {e}")

    async def resume(self):
        if self.process and self.is_paused:
            try:
                parent = psutil.Process(self.process.pid)
                parent.resume()
                for child in parent.children(recursive=True):
                    try:
                        child.resume()
                    except Exception:
                        pass
                self.is_paused = False
                self.yield_queue = False
                log.info(f"Process {self.process.pid} and children resumed.")
            except Exception as e:
                log.error(f"Failed to resume process: {e}")

    async def cancel(self):
        self.is_cancelled = True
        if self.process:
            try:
                # A SIGSTOP'd process never delivers SIGTERM; resume first.
                if self.is_paused:
                    await self.resume()
                self.process.terminate()
                try:
                    parent = psutil.Process(self.process.pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception as e:
                log.error(f"Failed to terminate process: {e}")

    def parse_progress(self, line: str):
        try:
            parts = line.split("=")
            if len(parts) != 2:
                return
            key, value = parts[0].strip(), parts[1].strip()

            if key == "frame":
                self.stats.frame = int(value)
            elif key == "fps":
                self.stats.fps = float(value)
            elif key == "bitrate":
                self.stats.bitrate = value
            elif key == "speed":
                self.stats.speed = value
            elif key == "out_time_us":
                us = int(value)
                current_seconds = us / 1000000
                if self.total_duration > 0:
                    self.stats.percent = min(
                        100.0, (current_seconds / self.total_duration) * 100
                    )

                elapsed = time.time() - self.start_time
                if self.stats.percent > 0:
                    total_estimated = elapsed / (self.stats.percent / 100)
                    eta_seconds = total_estimated - elapsed
                    self.stats.eta = TimeFormatter(eta_seconds * 1000)

                self.stats.elapsed = TimeFormatter(elapsed * 1000)
        except Exception:
            pass

    def get_progress_ui(self) -> str:
        bar_length = 20
        filled = int(self.stats.percent / 100 * bar_length)
        bar = "▰" * filled + "▱" * (bar_length - filled)

        current_size = 0
        try:
            current_size = os.path.getsize(self.output_file)
        except OSError:
            pass

        comp_text = ""
        if self.stats.percent > 0 and current_size > 0:
            est_size = current_size / (self.stats.percent / 100)
            if est_size > 0 and self.original_size > 0:
                comp = self.original_size / est_size
                if comp >= 1:
                    comp_text = f" · 🗜 {comp:.1f}× smaller"
                else:
                    comp_text = f" · 🗜 {1 / comp:.1f}× larger"

        step_info = f" · Step {self.current_step}/{self.total_steps}" if self.total_steps > 1 else ""

        if self.is_paused:
            status_icon, status_text = "⏸", "Paused"
        else:
            status_icon, status_text = "🎬", "Encoding"

        return (
            f"{status_icon} <b>{status_text}</b>{step_info}\n"
            f"📁 <code>{escape(self.file_name)}</code>\n"
            f"<blockquote><code>{bar}</code> <b>{self.stats.percent:.1f}%</b>\n"
            f"📦 {humanbytes(current_size)} / {humanbytes(self.original_size)}{comp_text}\n"
            f"⏳ ETA <b>{self.stats.eta}</b> · ⏱ {self.stats.elapsed}\n"
            f"⚡ {self.stats.speed} · 🎞 {self.stats.fps:.1f} fps · 📊 {self.stats.bitrate}</blockquote>\n"
            f"<blockquote>⚙️ <code>{escape(str(self.codec))}</code> · CRF <code>{escape(str(self.crf))}</code> · "
            f"{escape(str(self.preset))} · {escape(str(self.resolution))}\n"
            f"🆔 <code>{self.job_id}</code></blockquote>"
        )


async def probe_file(path: str) -> Dict[str, Any]:
    """
    Probe a media file with ffprobe. Raises RuntimeError on failure so jobs
    never run against fabricated durations.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed (rc={proc.returncode}): {err.decode()[:300]}"
        )

    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe returned invalid JSON: {e}")

    info: Dict[str, Any] = {"duration": 0.0, "height": 0, "audio_codec": None}
    fmt = data.get("format", {})
    if "duration" in fmt:
        info["duration"] = float(fmt["duration"])
    for stream in data.get("streams", []):
        if info["duration"] == 0 and "duration" in stream:
            info["duration"] = float(stream["duration"])
        if stream.get("codec_type") == "video" and stream.get("height"):
            info["height"] = int(stream["height"])
        if stream.get("codec_type") == "audio" and not info["audio_codec"]:
            info["audio_codec"] = stream.get("codec_name")

    if info["duration"] <= 0:
        raise RuntimeError("Could not determine media duration")

    return info


async def _monitor_process(process: FFmpegProcess) -> str:
    """
    Monitors the FFmpeg process.
    Returns: 'FINISHED', 'FAILED', 'CANCELLED', or 'YIELDED'
    """
    last_update = 0.0

    while True:
        if process.yield_queue:
            return "YIELDED"

        if process.process.returncode is not None:
            break

        try:
            try:
                line = await asyncio.wait_for(
                    process.process.stdout.readline(), timeout=1.0
                )
                if not line:
                    break
                process.parse_progress(line.decode(errors="replace").strip())
            except asyncio.TimeoutError:
                pass
        except Exception:
            break

        now = time.time()
        if now - last_update >= UI_UPDATE_INTERVAL:
            last_update = now
            try:
                if not process.is_viewing_queue:
                    pause_text = "▶️ Resume" if process.is_paused else "⏸ Pause"
                    buttons = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    pause_text,
                                    callback_data=f"enc_pause_{process.job_id}",
                                ),
                                InlineKeyboardButton(
                                    "❌ Cancel",
                                    callback_data=f"enc_cancel_{process.job_id}",
                                ),
                            ],
                            [
                                InlineKeyboardButton(
                                    "📋 Queue",
                                    callback_data=f"enc_queue_{process.job_id}",
                                ),
                            ],
                        ]
                    )
                    await process.message.edit(
                        process.get_progress_ui(), reply_markup=buttons
                    )
            except FloodWait as e:
                log.warning(f"FloodWait {e.value}s on UI update, backing off")
                await asyncio.sleep(e.value)
            except Exception as e:
                log.error(f"Failed to update UI: {e}")

    await process.process.wait()
    await process._stop_stderr_task()

    if process.is_cancelled:
        return "CANCELLED"

    if process.process.returncode == 0:
        return "FINISHED"
    return "FAILED"


def progress_buttons(process: FFmpegProcess) -> InlineKeyboardMarkup:
    pause_text = "▶️ Resume" if process.is_paused else "⏸ Pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    pause_text, callback_data=f"enc_pause_{process.job_id}"
                ),
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"enc_cancel_{process.job_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Queue", callback_data=f"enc_queue_{process.job_id}"
                ),
            ],
        ]
    )


async def _handle_job_completion(
    process: FFmpegProcess, status: str, cleanup_input: bool = True
) -> str:
    if status == "YIELDED":
        try:
            try:
                await process.message.delete()
            except Exception:
                pass

            user_link = f"<a href='tg://user?id={process.user_id}'>User</a>"
            pause_msg = await process.client.send_message(
                process.user_id,
                f"⏸ <b>Job Paused</b>\n"
                f"<blockquote>🆔 <code>{process.job_id}</code>\n"
                f"📁 <code>{escape(process.file_name)}</code>\n"
                f"⚙️ {escape(str(process.codec))} · {escape(str(process.resolution))} · CRF {escape(str(process.crf))}\n"
                f"🎯 Step {process.current_step}/{process.total_steps}</blockquote>\n"
                f"{user_link}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "▶️ Resume",
                                callback_data=f"enc_pause_{process.job_id}",
                            ),
                            InlineKeyboardButton(
                                "❌ Cancel",
                                callback_data=f"enc_cancel_{process.job_id}",
                            ),
                        ]
                    ]
                ),
            )
            process.message = pause_msg
        except Exception as e:
            log.error(f"Failed to update UI on pause: {e}")
        return "YIELDED"

    if status == "CANCELLED":
        try:
            await process.message.edit("🚫 <b>Encoding Cancelled</b>")
        except Exception:
            pass
        _cleanup_files(process, cleanup_input=True)
        active_encodings.pop(process.job_id, None)
        return "CANCELLED"

    if status == "FINISHED":
        is_final_step = process.current_step >= process.total_steps

        if not is_final_step:
            # Intermediate resolution: discard its output, keep input.
            try:
                if os.path.exists(process.output_file):
                    os.remove(process.output_file)
            except Exception:
                pass
        else:
            async def upload_worker():
                await _upload_video(
                    process.client,
                    process.user_id,
                    process.output_file,
                    None,
                    process.stats,
                    process.original_size,
                    codec=process.codec,
                    crf=process.crf,
                    preset=process.preset,
                    resolution=process.resolution,
                    thumb=process.thumbnail_path,
                    job_id=process.job_id,
                )

            await upload_manager.add_upload_job(process.user_id, upload_worker)

            try:
                await process.message.delete()
            except Exception:
                pass

        if cleanup_input:
            try:
                if os.path.exists(process.input_file):
                    os.remove(process.input_file)
            except Exception:
                pass

        active_encodings.pop(process.job_id, None)
        return "FINISHED"

    if status == "FAILED":
        stderr_text = process.stderr_tail.decode(errors="replace") if process.stderr_tail else "Unknown error"
        log.error(f"FFmpeg failed for job {process.job_id}: {stderr_text[:800]}")
        _remember_error(process.job_id, f"exit={process.process.returncode}\n{stderr_text}")
        try:
            await process.message.edit(
                f"❌ <b>Encoding failed</b>\n"
                f"<blockquote>📁 <code>{escape(process.file_name)}</code> · "
                f"Step {process.current_step}/{process.total_steps}</blockquote>\n"
                f"<i>Nothing was lost — send the file again to retry, "
                f"or open Details for the technical reason.</i>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{process.job_id}"
                            ),
                            InlineKeyboardButton("⚙️ Settings", callback_data="cb_open_settings"),
                        ],
                        [
                            InlineKeyboardButton("🗑 Dismiss", callback_data="cb_close"),
                        ],
                    ]
                ),
            )
        except Exception as e:
            log.error(f"Failed to edit failure message: {e}")
        _cleanup_files(process, cleanup_input=cleanup_input)
        active_encodings.pop(process.job_id, None)
        return "FAILED"


def _cleanup_files(process: FFmpegProcess, cleanup_input: bool = True):
    try:
        if cleanup_input and process.input_file and os.path.exists(process.input_file):
            os.remove(process.input_file)
        if process.output_file and os.path.exists(process.output_file):
            os.remove(process.output_file)
    except Exception as e:
        log.error(f"Cleanup failed: {e}")


async def _run_encoding_job(
    ffmpeg_cmd: str,
    input_file: str,
    output_file: str,
    client: Client,
    message: Message,
    job_id: str,
    user_id: int,
    cleanup_input: bool = True,
    codec: str = "Unknown",
    crf: str = "N/A",
    preset: str = "N/A",
    resolution: str = "N/A",
    current_step: int = 1,
    total_steps: int = 1,
    thumbnail_path: Optional[str] = None,
    duration_limit: float = 0.0,
) -> str:
    """Runs one encoding step. Returns its terminal status string."""
    try:
        probe = await probe_file(input_file)
        duration = probe["duration"]
    except Exception as e:
        log.error(f"Probe failed for job {job_id}: {e}")
        _remember_error(job_id, f"probe: {e}")
        try:
            await message.edit(
                "❌ <b>Encoding failed</b>\n"
                "<blockquote>The file could not be read by ffmpeg. "
                "It may be corrupted or an unsupported format.</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            ),
                            InlineKeyboardButton(
                                "🗑 Dismiss", callback_data="cb_close"
                            ),
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        return "FAILED"

    # Progress math must reflect trim/sample limits, not the full source.
    if duration_limit and duration_limit > 0:
        duration = min(duration, duration_limit)

    try:
        original_size = os.path.getsize(input_file)
    except OSError as e:
        log.error(f"Input vanished for job {job_id}: {e}")
        _remember_error(job_id, f"input missing: {e}")
        try:
            await message.edit("❌ <b>Encoding failed</b>\n<blockquote>Input file went missing.</blockquote>")
        except Exception:
            pass
        return "FAILED"

    process = FFmpegProcess(
        ffmpeg_cmd,
        input_file,
        output_file,
        duration,
        original_size,
        Path(input_file).name,
        codec=codec,
        crf=crf,
        preset=preset,
        resolution=resolution,
        current_step=current_step,
        total_steps=total_steps,
        thumbnail_path=thumbnail_path,
    )
    process.job_id = job_id
    process.message = message
    process.client = client
    process.user_id = user_id

    active_encodings[job_id] = process

    try:
        await process.start()
        status = await _monitor_process(process)
        await _handle_job_completion(process, status, cleanup_input=cleanup_input)
        return status
    except Exception as e:
        log.error(f"Encoding job failed: {e}", exc_info=True)
        _remember_error(job_id, str(e))
        try:
            await message.edit(
                "❌ <b>Encoding failed</b>\n"
                f"<blockquote>{escape(str(e))[:300]}</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            ),
                            InlineKeyboardButton(
                                "🗑 Dismiss", callback_data="cb_close"
                            ),
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        _cleanup_files(process, cleanup_input=cleanup_input)
        active_encodings.pop(job_id, None)
        return "FAILED"


async def resume_encoding_job(job_id: str) -> str:
    """Resumes a yielded job, then drives any remaining quality steps."""
    if job_id not in active_encodings:
        log.warning(f"Attempted to resume non-existent or finished job: {job_id}")
        return "FAILED"

    process = active_encodings[job_id]
    await process.resume()

    try:
        await process.message.delete()
    except Exception:
        pass

    process.message = await process.client.send_message(
        process.user_id, "🔄 <b>Resuming Encoding...</b>"
    )

    try:
        status = await _monitor_process(process)
        cleanup_input = process.current_step >= process.total_steps
        await _handle_job_completion(process, status, cleanup_input=cleanup_input)

        # Continue any remaining quality steps that never ran because the
        # worker loop broke on YIELDED.
        total = process.total_steps
        input_file = process.input_file
        client = process.client
        user_id = process.user_id
        message = process.message
        next_index = process.current_step  # 0-based index of next command
        output_base = str(
            Path(input_file).parent / f"encoded_{Path(input_file).stem}"
        )

        while status == "FINISHED" and next_index < total:
            settings = await get_user_settings(user_id)
            if not settings:
                settings = {}
            from bot.func.ffmpeg_utils import prepare_thumbnail, prepare_watermark_assets

            prepare_watermark_assets(user_id, settings)
            thumbnail_path = prepare_thumbnail(user_id, settings)
            settings["user_id"] = user_id

            commands = generate_ffmpeg_cmd(
                settings, input_file, output_base, thumbnail_path
            )
            cmd_info = commands[next_index]
            is_last = next_index == len(commands) - 1
            video_settings = settings.get("video", {})

            status = await _run_encoding_job(
                cmd_info["cmd"],
                input_file,
                cmd_info["output_file"],
                client,
                message,
                job_id,
                user_id,
                cleanup_input=is_last,
                codec=video_settings.get("codec", "libx264"),
                crf=str(video_settings.get("crf", "23")),
                preset=video_settings.get("preset", "medium"),
                resolution=cmd_info.get("suffix", "1080p"),
                current_step=next_index + 1,
                total_steps=len(commands),
                thumbnail_path=thumbnail_path,
                duration_limit=_duration_limit(video_settings),
            )
            next_index += 1
        return status
    except Exception as e:
        log.error(f"Resumed job failed: {e}", exc_info=True)
        _remember_error(job_id, str(e))
        try:
            await process.message.edit(
                "❌ <b>Resumed job failed</b>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        _cleanup_files(process, cleanup_input=True)
        active_encodings.pop(job_id, None)
        return "FAILED"


def _duration_limit(video_settings: dict) -> float:
    trim_end = float(video_settings.get("trim_end", 0) or 0)
    trim_start = float(video_settings.get("trim_start", 0) or 0)
    sample = int(video_settings.get("sample_seconds", 0) or 0)
    limit = 0.0
    if trim_end > trim_start > 0:
        limit = trim_end - trim_start
    if sample > 0:
        limit = sample if not limit else min(limit, sample)
    return limit


async def safe_download_media(
    client: Client, message: Message, file_path: str, progress_msg: Message
):
    try:
        await download_manager.acquire()
        downloaded_path = await client.download_media(
            message,
            file_name=file_path,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading...", progress_msg, time.time()),
        )

        if not downloaded_path or not os.path.exists(downloaded_path):
            log.error(f"Download reported success but file not found: {downloaded_path}")
            return None

        return downloaded_path
    except Exception as e:
        log.error(f"Download failed: {e}")
        return None
    finally:
        download_manager.release()


def sanitize_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_", ".")).strip()
    return safe or f"video_{int(time.time())}.mp4"


def apply_rename_pattern(pattern: str, original_name: str, res: str, codec: str) -> str:
    try:
        name = pattern.format(
            original=Path(original_name).stem,
            res=res,
            codec=codec,
            date=time.strftime("%Y-%m-%d"),
        )
    except (KeyError, IndexError, ValueError):
        name = Path(original_name).stem
    return sanitize_filename(name)


def reconstruct_worker(job, client: Client):
    """Reconstructs the worker function for a restored job."""

    async def worker(job_id_arg):
        try:
            log.info(
                f"Restoring job {job.job_id}: fetching message {job.message_id} "
                f"from chat {job.chat_id}"
            )
            message = await client.get_messages(job.chat_id, job.message_id)

            if not message or (not message.video and not message.document):
                log.error(f"Failed to fetch message for job {job.job_id}")
                try:
                    await client.send_message(
                        job.user_id,
                        "⚠️ <b>Restored job could not find its original file.</b>\n"
                        "<i>The source message was deleted. Please send the file again.</i>",
                    )
                except Exception:
                    pass
                return "FAILED"

            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)

            file_name = job.file_name
            if not file_name or file_name == "Unknown":
                file_name = f"restored_{job.job_id}.mp4"

            download_file_path = (
                downloads_dir
                / f"{job.user_id}_{job.job_id}_{sanitize_filename(file_name)}"
            )

            status_msg = await client.send_message(
                job.user_id, f"🔄 <b>Restoring Job</b> <code>{job.job_id}</code>..."
            )

            downloaded_path = await safe_download_media(
                client, message, str(download_file_path), status_msg
            )

            if not downloaded_path:
                try:
                    await status_msg.edit("❌ <b>Restoration failed:</b> could not download the file.")
                except Exception:
                    pass
                return "FAILED"

            settings = await get_user_settings(job.user_id)
            if not settings:
                settings = {}

            from bot.func.ffmpeg_utils import prepare_thumbnail, prepare_watermark_assets

            prepare_watermark_assets(job.user_id, settings)
            thumbnail_path = prepare_thumbnail(job.user_id, settings)

            settings["user_id"] = job.user_id

            output_base = str(
                Path(downloaded_path).parent
                / f"encoded_{Path(downloaded_path).stem}"
            )

            commands = generate_ffmpeg_cmd(
                settings, downloaded_path, output_base, thumbnail_path
            )

            video_settings = settings.get("video", {})
            codec = video_settings.get("codec", "libx264")
            crf = video_settings.get("crf", "23")
            preset = video_settings.get("preset", "medium")

            duration_limit = 0.0
            trim_end = float(video_settings.get("trim_end", 0) or 0)
            trim_start = float(video_settings.get("trim_start", 0) or 0)
            sample = int(video_settings.get("sample_seconds", 0) or 0)
            if trim_end > trim_start > 0:
                duration_limit = trim_end - trim_start
            if sample > 0:
                duration_limit = (
                    sample if not duration_limit else min(duration_limit, sample)
                )

            final_status = "FAILED"
            for i, cmd_info in enumerate(commands):
                is_last = i == len(commands) - 1
                resolution = cmd_info.get("suffix", "1080p")

                status = await _run_encoding_job(
                    cmd_info["cmd"],
                    downloaded_path,
                    cmd_info["output_file"],
                    client,
                    status_msg,
                    job_id_arg,
                    job.user_id,
                    cleanup_input=is_last,
                    codec=codec,
                    crf=str(crf),
                    preset=preset,
                    resolution=resolution,
                    current_step=i + 1,
                    total_steps=len(commands),
                    thumbnail_path=thumbnail_path,
                    duration_limit=duration_limit,
                )
                if status != "FINISHED":
                    final_status = status
                    break
            else:
                final_status = "FINISHED"

            return final_status

        except Exception as e:
            log.error(f"Error in restored worker for job {job.job_id}: {e}", exc_info=True)
            return "FAILED"

    return worker


async def generate_auto_thumbnail(output_file: str, user_id: int) -> Optional[str]:
    """Grabs a frame from the encoded video for use as an upload thumbnail."""
    try:
        probe = await probe_file(output_file)
        at = max(1.0, probe["duration"] * 0.35)
        os.makedirs(THUMB_DIR, exist_ok=True)
        thumb_path = os.path.join(
            THUMB_DIR, f"auto_{user_id}_{int(time.time())}.jpg"
        )
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-ss",
            str(at),
            "-i",
            output_file,
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-2",
            "-q:v",
            "4",
            thumb_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=60)
        if proc.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        log.warning(f"Auto-thumbnail generation failed: {e}")
    return None


def render_caption(
    file_name: str,
    codec: str,
    resolution: str,
    crf: str,
    preset: str,
    stats: EncodingStats,
    original_size: int,
    file_size: int,
    bot_username: str,
    sample: bool = False,
) -> str:
    comp = 1.0
    if file_size > 0 and original_size > 0:
        comp = original_size / file_size
    saved_pct = 0.0
    if original_size > 0 and file_size < original_size:
        saved_pct = (1 - file_size / original_size) * 100

    sample_note = "\n⚠️ <i>SAMPLE encode — first seconds only</i>\n" if sample else ""

    return (
        f"✅ <b>Encode completed</b>\n\n"
        f"<blockquote>📁 <code>{escape(file_name)}</code>\n"
        f"⚙️ {escape(str(codec))} · {escape(str(resolution))} · CRF {escape(str(crf))} · {escape(str(preset))}</blockquote>\n\n"
        f"<blockquote>📊 <b>Stats</b>\n"
        f"📥 Original: <code>{humanbytes(original_size)}</code>\n"
        f"📤 Encoded: <code>{humanbytes(file_size)}</code>\n"
        f"🗜 Compression: <code>{comp:.2f}×</code>"
        + (f" · saved <code>{saved_pct:.1f}%</code>" if saved_pct > 0 else "")
        + f"\n⏱ Time: <code>{stats.elapsed}</code></blockquote>\n"
        f"{sample_note}"
        f"🤖 Encoded by @{bot_username}"
    )


def _completion_buttons(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⚙️ Settings", callback_data="cb_open_settings"
                ),
                InlineKeyboardButton(
                    "📋 Queue", url=f"https://t.me/{bot_username}?start=queue"
                ),
            ],
            [
                InlineKeyboardButton(
                    "✨ Features", callback_data="cb_features"
                ),
            ],
        ]
    )


async def _upload_video(
    client: Client,
    user_id: int,
    file_path: str,
    progress_msg: Optional[Message],
    stats: EncodingStats,
    original_size: int,
    codec: str = "Unknown",
    crf: str = "N/A",
    preset: str = "N/A",
    resolution: str = "N/A",
    thumb: Optional[str] = None,
    job_id: str = "",
):
    upload_msg = None
    sent_message = None
    auto_thumb_path = None
    output_deleted = False

    try:
        file_name = Path(file_path).name
        file_size = os.path.getsize(file_path)

        settings = await get_user_settings(user_id)
        video_settings = settings.get("video", {})
        as_video = bool(settings.get("output_as_video", False))
        rename_pattern = (settings.get("rename", {}) or {}).get("pattern", "")
        sample_mode = int(video_settings.get("sample_seconds", 0) or 0) > 0

        if rename_pattern:
            renamed = apply_rename_pattern(rename_pattern, file_name, resolution, str(codec))
            if renamed:
                stem = Path(renamed).stem if renamed else Path(file_name).stem
                ext = Path(file_name).suffix or (".mp4" if as_video else ".mkv")
                file_name = f"{stem}{ext}"

        bot_username = (await client.get_me()).username

        caption = render_caption(
            file_name, codec, resolution, crf, preset, stats, original_size,
            file_size, bot_username, sample=sample_mode,
        )

        upload_msg = await client.send_message(user_id, "📤 <b>Uploading…</b>\n<i>Sending your encoded file.</i>")

        if not thumb:
            thumb = await generate_auto_thumbnail(file_path, user_id)
            auto_thumb_path = thumb

        send_kwargs = dict(
            chat_id=user_id,
            caption=caption,
            thumb=thumb,
            file_name=file_name,
            progress=progress_for_pyrogram,
            progress_args=("📤 Uploading encoded video...", upload_msg, time.time()),
            reply_markup=_completion_buttons(bot_username),
        )

        sent_message = None
        for attempt in range(2):
            try:
                if as_video:
                    try:
                        sent_message = await client.send_video(
                            supports_streaming=True, video=file_path, **send_kwargs
                        )
                    except Exception:
                        # Fall back to document if streamable send is rejected.
                        send_kwargs.pop("supports_streaming", None)
                        sent_message = await client.send_document(
                            document=file_path, **send_kwargs
                        )
                else:
                    sent_message = await client.send_document(
                        document=file_path, **send_kwargs
                    )
                break
            except FloodWait as e:
                if attempt == 0:
                    log.warning(f"Upload FloodWait {e.value}s, retrying")
                    await asyncio.sleep(e.value)
                else:
                    raise

        output_deleted = True

        try:
            await upload_msg.delete()
        except Exception:
            pass

        # Mirror to the log channel via message copy (no re-upload).
        if sent_message is not None and LOG_CHANNEL:
            try:
                await sent_message.copy(LOG_CHANNEL)
            except Exception as log_error:
                log.error(f"Failed to copy upload to log channel: {log_error}")

        # Persist aggregate stats.
        try:
            await inc_stats(
                {
                    "total_encodes": 1,
                    "in_bytes": int(original_size),
                    "out_bytes": int(file_size),
                },
                user_id=user_id,
            )
        except Exception as stats_error:
            log.error(f"Failed to update stats: {stats_error}")

    except Exception as e:
        log.error(f"Upload failed: {e}", exc_info=True)
        error_key = job_id or f"upload_{int(time.time())}"
        _remember_error(error_key, str(e))
        if upload_msg:
            try:
                await upload_msg.edit(
                    "❌ <b>Upload failed</b> — the encoded file is kept on disk.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔍 Details",
                                    callback_data=f"cb_err_{error_key}",
                                )
                            ]
                        ]
                    ),
                )
            except Exception:
                pass
        output_deleted = False
    finally:
        if output_deleted:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                log.error(f"Cleanup failed: {e}")
        if auto_thumb_path and os.path.exists(auto_thumb_path):
            try:
                os.remove(auto_thumb_path)
            except Exception:
                pass


async def encode(
    ffmpeg_cmd: str,
    input_file: str,
    client: Client,
    user_id: int,
    custom_output_name: Optional[str] = None,
    display_mode: str = "rich",
    update_interval: float = 3.0,
    message: Optional[Message] = None,
    chat_id: int = 0,
    message_id: int = 0,
) -> Dict[str, Any]:
    if not custom_output_name:
        custom_output_name = f"encoded_{Path(input_file).name}"

    output_base = str(Path(input_file).parent / os.path.splitext(custom_output_name)[0])

    if message:
        try:
            await message.delete()
        except Exception:
            pass

    message = await client.send_message(
        user_id,
        f"⏳ <b>Queued</b>\n"
        f"<blockquote>🆔 <code>{job_id}</code>\n"
        f"Waiting for a free worker slot…</blockquote>\n"
        f"<i>Track progress in /queue</i>",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("📋 Open queue", url=f"https://t.me/{(await client.get_me()).username}?start=queue")]]
        ),
    )

    # Pre-assign the job id so the worker never runs with placeholder args.
    job_id = str(uuid.uuid4())[:8]

    async def worker(jid_arg):
        settings = await get_user_settings(user_id)
        if not settings:
            settings = {}

        from bot.func.ffmpeg_utils import prepare_thumbnail, prepare_watermark_assets

        prepare_watermark_assets(user_id, settings)
        thumbnail_path = prepare_thumbnail(user_id, settings)

        settings["user_id"] = user_id

        commands = generate_ffmpeg_cmd(
            settings, input_file, output_base, thumbnail_path
        )

        video_settings = settings.get("video", {})
        codec = video_settings.get("codec", "libx264")
        crf = video_settings.get("crf", "23")
        preset = video_settings.get("preset", "medium")

        duration_limit = 0.0
        trim_end = float(video_settings.get("trim_end", 0) or 0)
        trim_start = float(video_settings.get("trim_start", 0) or 0)
        sample = int(video_settings.get("sample_seconds", 0) or 0)
        if trim_end > trim_start > 0:
            duration_limit = trim_end - trim_start
        if sample > 0:
            duration_limit = (
                sample if not duration_limit else min(duration_limit, sample)
            )

        final_status = "FAILED"
        for i, cmd_info in enumerate(commands):
            is_last = i == len(commands) - 1
            resolution = cmd_info.get("suffix", "1080p")

            status = await _run_encoding_job(
                cmd_info["cmd"],
                input_file,
                cmd_info["output_file"],
                client,
                message,
                jid_arg,
                user_id,
                cleanup_input=is_last,
                codec=codec,
                crf=str(crf),
                preset=preset,
                resolution=resolution,
                current_step=i + 1,
                total_steps=len(commands),
                thumbnail_path=thumbnail_path,
                duration_limit=duration_limit,
            )
            if status != "FINISHED":
                final_status = status
                break
        else:
            final_status = "FINISHED"

        return final_status

    try:
        file_size_str = humanbytes(os.path.getsize(input_file))
    except OSError:
        file_size_str = "Unknown"
    file_name = Path(input_file).name

    queued_id = await queue_manager.add_job(
        user_id,
        worker,
        job_id,
        job_id=job_id,
        file_size=file_size_str,
        file_name=file_name,
        chat_id=chat_id,
        message_id=message_id,
        task_type="encode",
        input_file=input_file,
        output_file=output_base,
    )

    if queued_id is None:
        # Duplicate or over-limit: do not orphan the downloaded file.
        try:
            if os.path.exists(input_file):
                os.remove(input_file)
        except Exception:
            pass
        await message.edit(
            "⚠️ <b>Job not queued</b>\n\n"
            "<i>Either this exact file is already queued, or you reached your "
            "concurrent job limit. Check /queue.</i>"
        )
        return {"success": False, "error": "Duplicate or limit reached"}

    await message.edit(
        f"⏳ <b>Job queued</b>\n"
        f"<blockquote>🆔 Job ID: <code>{queued_id}</code>\n"
        f"🔢 Position: <code>{queue_manager.queue_position(queued_id)}</code></blockquote>\n"
        f"<i>Updates appear here when encoding starts.</i>",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("📋 Open queue", callback_data="cb_queue_hint")]]
        ),
    )

    return {"success": True, "job_id": queued_id, "output_file": output_base}
