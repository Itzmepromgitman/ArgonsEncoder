# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import shlex
import uuid

from bot.config import FFMPEG_BIN, MAX_CONCURRENT_JOBS, WATERMARK_DIR
from bot.func.ffmpeg_utils import generate_watermark_filter, prepare_watermark_assets
from bot.logger import LOGGER

log = LOGGER(__name__)
_PREVIEW_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


async def generate_preview(user_id: int, settings: dict) -> str:
    """
    Generates a preview image with the current watermark settings.
    Returns the path to the preview image.
    """
    try:
        # Ensure directory exists
        os.makedirs(WATERMARK_DIR, exist_ok=True)

        # Inject user_id for watermark font lookup
        settings["user_id"] = user_id

        # Restore watermark assets if needed
        prepare_watermark_assets(user_id, settings)

        wm_filter = generate_watermark_filter(settings, for_preview=True)
        if not wm_filter:
            log.warning(f"Preview Gen: No watermark filter generated for user {user_id}")
            return None

        import time

        output_path = os.path.join(
            WATERMARK_DIR,
            f"preview_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg",
        )
        cmd = [FFMPEG_BIN, "-y"]

        # Input: White background
        cmd.extend(["-f", "lavfi", "-i", "color=c=white:s=1920x1080:d=0.1"])

        if "movie=" in wm_filter:
            cmd.extend(["-filter_complex", wm_filter])
        else:
            cmd.extend(["-vf", wm_filter])

        cmd.extend(["-frames:v", "1", output_path])

        log.info(f"Preview CMD: {shlex.join(cmd)}")

        await _PREVIEW_SEMAPHORE.acquire()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                try:
                    if os.path.isfile(output_path):
                        os.remove(output_path)
                except OSError:
                    pass
                raise
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                try:
                    if os.path.isfile(output_path):
                        os.remove(output_path)
                except OSError:
                    pass
                raise

            if process.returncode != 0:
                log.error(f"Preview Gen Failed: {stderr.decode()}")
                try:
                    if os.path.isfile(output_path):
                        os.remove(output_path)
                except OSError:
                    pass
                return None

            return output_path
        finally:
            _PREVIEW_SEMAPHORE.release()

    except Exception as e:
        log.error(f"Preview Error: {e}", exc_info=True)
        return None
