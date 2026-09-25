"""Media helpers for Telegram-specific delivery constraints."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional

from bot.config import FFMPEG_BIN
from bot.logger import LOGGER

log = LOGGER(__name__)

TELEGRAM_THUMB_MAX_BYTES = 200 * 1024
TELEGRAM_THUMB_SIZE = 320


async def prepare_telegram_thumbnail(
    source: str,
    destination: Optional[str] = None,
    *,
    max_bytes: int = TELEGRAM_THUMB_MAX_BYTES,
) -> Optional[str]:
    """Create a square JPEG thumbnail accepted by Telegram.

    Telegram rejects/poorly renders custom thumbnails when they are not square
    JPEG files or exceed 200 KiB. We always normalize through FFmpeg instead of
    trusting an arbitrary uploaded file. ``None`` means the source could not be
    converted safely; callers should then omit the thumbnail rather than send
    an invalid payload.
    """
    if not source or not os.path.isfile(source):
        return None

    output = destination or str(
        Path(source).with_name(f"{Path(source).stem}_{uuid.uuid4().hex[:8]}.jpg")
    )
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    temporary = f"{output}.{uuid.uuid4().hex[:8]}.tmp.jpg"
    filter_graph = (
        f"scale={TELEGRAM_THUMB_SIZE}:{TELEGRAM_THUMB_SIZE}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={TELEGRAM_THUMB_SIZE}:{TELEGRAM_THUMB_SIZE}:"
        "(ow-iw)/2:(oh-ih)/2:color=black"
    )

    try:
        for quality in (4, 7, 11, 16, 23, 31):
            command = [
                FFMPEG_BIN,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source,
                "-vf",
                filter_graph,
                "-frames:v",
                "1",
                "-q:v",
                str(quality),
                "-pix_fmt",
                "yuvj420p",
                temporary,
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=45
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                log.warning("Timed out while preparing Telegram thumbnail")
                return None
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise

            if process.returncode != 0 or not os.path.isfile(temporary):
                log.warning(
                    "Could not prepare Telegram thumbnail: %s",
                    stderr.decode(errors="replace")[-300:],
                )
                return None
            if os.path.getsize(temporary) < max_bytes:
                os.replace(temporary, output)
                return output
            try:
                os.remove(temporary)
            except OSError:
                pass

        log.warning("Could not reduce Telegram thumbnail below %d bytes", max_bytes)
        return None
    except FileNotFoundError:
        log.warning("FFmpeg is unavailable; cannot normalize Telegram thumbnail")
        return None
    except Exception as exc:
        log.warning("Telegram thumbnail normalization failed: %s", exc)
        return None
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
