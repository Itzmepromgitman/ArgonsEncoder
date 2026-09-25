# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import glob
import json
import os
import sys

from pyrogram import Client
from pyrogram.types import BotCommand, BotCommandScopeChat

from bot.config import (
    API_HASH,
    APP_ID,
    DOWNLOAD_DIR,
    FFMPEG_BIN,
    FFPROBE_BIN,
    MAX_CONCURRENT_TRANSMISSIONS,
    OWNER_ID,
    PORT,
    TG_BOT_TOKEN,
    TG_BOT_WORKERS,
    THUMB_DIR,
    WATERMARK_DIR,
    validate_config,
)
from .logger import LOGGER, tg_handler

log = LOGGER(__name__)
_upload_cleanup_task: asyncio.Task | None = None


async def _validate_media_toolchain():
    checks = (
        (FFMPEG_BIN, "-filters", ("drawtext", "scale", "overlay")),
        (FFMPEG_BIN, "-encoders", ("libx264", "libx265", "libvpx-vp9", "libaom-av1")),
        (FFMPEG_BIN, ("-h", "full"), ("fps_mode",)),
        (FFPROBE_BIN, "-version", ()),
    )
    for executable, flag, required in checks:
        flag_args = (flag,) if isinstance(flag, str) else tuple(flag)
        process = await asyncio.create_subprocess_exec(
            executable, "-hide_banner", *flag_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"{executable} {flag} timed out")
        if process.returncode != 0:
            raise RuntimeError(f"{executable} {flag} failed: {stderr.decode(errors='replace')[-300:]}")
        text = stdout.decode(errors="replace")
        missing = [name for name in required if name not in text]
        if missing:
            raise RuntimeError(f"FFmpeg is missing required capabilities: {', '.join(missing)}")


async def _retry_cleanup_loop():
    from bot.func.encode import (
        cleanup_expired_paused_jobs,
        cleanup_expired_upload_retries,
    )

    while True:
        await asyncio.sleep(300)
        try:
            await cleanup_expired_paused_jobs()
            await cleanup_expired_upload_retries()
            from bot.func.queue_manager import queue_manager

            await queue_manager.retry_deferred()
        except Exception as exc:
            log.warning(f"Upload recovery cleanup failed: {exc}")


async def _start_health_server():
    """Serve a tiny keep-alive endpoint on 0.0.0.0:$PORT inside the bot loop."""
    try:
        from aiohttp import web

        from bot.server import web_server

        runner = web.AppRunner(await web_server())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(PORT))
        await site.start()
        log.info(f"Health server listening on 0.0.0.0:{PORT}")
    except Exception as e:
        log.error(f"Health server failed to start: {e}")


def _startup_cleanup():
    """Wipe transient working dirs to prevent disk bloat across restarts."""
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        preserved_outputs = set()
        manifest = os.path.join(DOWNLOAD_DIR, ".upload_retries.json")
        try:
            with open(manifest, encoding="utf-8") as handle:
                payload = json.load(handle)
            for entry in payload.get("entries", []):
                for item in entry.get("output_files", []):
                    if item.get("file_path"):
                        preserved_outputs.add(os.path.abspath(item["file_path"]))
        except (OSError, ValueError, TypeError):
            pass
        # Preserve the durable upload-recovery manifest and its referenced
        # outputs; remove only transient media/input files from the old process.
        for root, dirs, files in os.walk(DOWNLOAD_DIR, topdown=False):
            for name in files:
                path = os.path.abspath(os.path.join(root, name))
                if (
                    name == ".upload_retries.json"
                    or (name.startswith(".privacy_") and name.endswith(".marker"))
                    or path in preserved_outputs
                ):
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
    except Exception as e:
        log.error(f"Failed to cleanup {DOWNLOAD_DIR}: {e}")

    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
        for pattern in ("auto_*.jpg", "*.tmp"):
            for p in glob.glob(os.path.join(THUMB_DIR, pattern)):
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception as e:
        log.error(f"Thumb cleanup failed: {e}")

    try:
        for p in glob.glob(os.path.join(WATERMARK_DIR, "preview_*.jpg")):
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception as e:
        log.error(f"Watermark cleanup failed: {e}")

    log.info("Startup cleanup complete")


class Bot(Client):
    def __init__(self):
        validate_config()
        # Always construct the bot client on the same event loop that will run
        # it. Loading a persisted session in a temporary constructor loop can
        # bind Motor/Pyrofork resources to the wrong loop.
        super().__init__(
            name="bot_session",
            api_id=APP_ID,
            api_hash=API_HASH,
            bot_token=TG_BOT_TOKEN,
            plugins={"root": "plugins"},
            workers=TG_BOT_WORKERS,
            max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
        )

    async def start(self):
        global _upload_cleanup_task
        await super().start()
        tg_handler.client = self

        try:
            from database import ensure_database_indexes, ping_database

            if not await ping_database():
                raise RuntimeError("MongoDB is unavailable")
            if not await ensure_database_indexes():
                raise RuntimeError("MongoDB privacy index setup failed")
        except Exception:
            log.exception("Database readiness check failed")
            await self.stop()
            raise
        try:
            await _validate_media_toolchain()
        except Exception:
            log.exception("FFmpeg toolchain validation failed")
            await self.stop()
            raise
        _startup_cleanup()
        await _start_health_server()

        try:
            await self.send_message(
                OWNER_ID,
                text=(
                    "♻️ <b>Bot restarted</b>\n"
                    "<blockquote>Queue restored · ready for jobs.</blockquote>"
                ),
            )
        except BaseException:
            pass

        log.info(
            """
      ___      _____    _____   ____   _   _
     /   \\    |  __ \\  / ____| / __ \\ | \\ | |
    /  ^  \\   | |__) || |  __ | |  | ||  \\| |
   /  /_\\  \\  |  _  / | | |_ || |  | || . ` |
  /  _____  \\ | | \\ \\ | |__| || |__| || |\\  |
 /__/     \\__\\|_|  \\_\\ \\_____| \\_____| |_| \\_|
 |__|     |__|

    Developed by ARGON telegram: @REACTIVEARGON
                                                  """
        )
        log.info("Argons Encoder started successfully")

        # Restore queue immediately after the client is live so restored jobs
        # are known before user traffic flows in.
        try:
            from database import privacy_admission_lock
            from bot.func.queue_manager import queue_manager

            async with privacy_admission_lock:
                restored_ok = await queue_manager.restore_queue(self)
            if not restored_ok:
                raise RuntimeError("Queue restoration failed")
        except Exception as e:
            log.error(f"Failed to restore queue: {e}")
            await self.stop()
            raise

        try:
            from database import privacy_admission_lock
            from bot.func.encode import UPLOAD_RECOVERY_HEALTHY, restore_upload_retries

            async with privacy_admission_lock:
                recovered = await restore_upload_retries(self)
            if not UPLOAD_RECOVERY_HEALTHY:
                raise RuntimeError("Upload recovery manifest is not durable")
            if recovered:
                log.info(f"Restored {recovered} upload recovery item(s)")
        except Exception as e:
            log.error(f"Failed to restore upload retries: {e}")
            await self.stop()
            raise
        if _upload_cleanup_task is None or _upload_cleanup_task.done():
            _upload_cleanup_task = asyncio.create_task(_retry_cleanup_loop())

        # Set Bot Commands — everyone gets the core set; owner also gets admin ops.
        try:
            base_commands = [
                BotCommand("start", "🏠 Open the home menu"),
                BotCommand("settings", "⚙️ Configure encoding"),
                BotCommand("queue", "📋 Your job queue"),
                BotCommand("status", "📊 Live server status"),
                BotCommand("stats", "📈 Bot statistics"),
                BotCommand("ss", "📸 Screenshots from a video"),
                BotCommand("cancel", "🚫 Cancel a job by ID"),
                BotCommand("clear", "🧹 Clear your queued jobs"),
                BotCommand("forget", "🗑 Delete my bot data"),
                BotCommand("features", "✨ Feature overview"),
                BotCommand("help", "📚 Usage manual"),
            ]
            await self.set_bot_commands(base_commands)

            owner_commands = base_commands + [
                BotCommand("jobs", "🗂 All jobs (owner)"),
                BotCommand("info", "ℹ️ Job details (owner)"),
                BotCommand("cancelall", "💣 Cancel every job (owner)"),
                BotCommand("admin", "💠 Admin panel"),
                BotCommand("broadcast", "📣 Broadcast (reply to msg)"),
                BotCommand("ban", "🚫 Ban a user"),
                BotCommand("unban", "✅ Unban a user"),
                BotCommand("maint", "🛠 Toggle maintenance"),
                BotCommand("restart", "♻️ Restart the bot"),
                BotCommand("log", "📄 Fetch logs"),
                BotCommand("shell", "🐍 Run Python (reply)"),
            ]
            await self.set_bot_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
            log.info("Bot commands set successfully")
        except Exception as e:
            log.error(f"Failed to set bot commands: {e}")

    async def stop(self, *args, **kwargs):
        global _upload_cleanup_task
        if _upload_cleanup_task and not _upload_cleanup_task.done():
            _upload_cleanup_task.cancel()
            try:
                await _upload_cleanup_task
            except asyncio.CancelledError:
                pass
        # Persist final queue state before disconnecting.
        try:
            from bot.func.upload_manager import upload_manager

            if not await upload_manager.shutdown():
                if upload_manager.get_all_jobs():
                    log.error("Upload recovery failed; keeping the client alive")
                    upload_manager.resume()
                    await upload_manager.start()
                    return
                log.error("Upload recovery was not fully persisted during shutdown")
        except Exception as e:
            log.error(f"Upload shutdown failed: {e}")
        try:
            from bot.func.queue_manager import queue_manager

            queue_saved = await queue_manager.shutdown()
            if not queue_saved and (
                queue_manager.get_all_jobs() or queue_manager._deferred_jobs
            ):
                log.error("Queue snapshot failed; keeping the client alive")
                queue_manager.resume()
                await queue_manager.start()
                return
        except Exception as e:
            log.error(f"Shutdown save failed: {e}")
        try:
            from bot.func.encode import (
                UPLOAD_INFLIGHT,
                UPLOAD_MANIFEST_ONLY,
                UPLOAD_RETRY,
                _persist_upload_retries,
            )

            if not _persist_upload_retries():
                log.error("Final upload recovery manifest could not be persisted")
                if UPLOAD_RETRY or UPLOAD_INFLIGHT or UPLOAD_MANIFEST_ONLY:
                    from bot.func.upload_manager import upload_manager
                    from bot.func.queue_manager import queue_manager

                    upload_manager.resume()
                    queue_manager.resume()
                    await upload_manager.start()
                    await queue_manager.start()
                    return
        except Exception as e:
            log.error(f"Upload retry save failed: {e}")
            try:
                from bot.func.encode import UPLOAD_INFLIGHT, UPLOAD_MANIFEST_ONLY, UPLOAD_RETRY

                if UPLOAD_RETRY or UPLOAD_INFLIGHT or UPLOAD_MANIFEST_ONLY:
                    from bot.func.upload_manager import upload_manager
                    from bot.func.queue_manager import queue_manager

                    upload_manager.resume()
                    queue_manager.resume()
                    await upload_manager.start()
                    await queue_manager.start()
                    return
            except Exception:
                pass
        await super().stop(*args, **kwargs)


if __name__ == "__main__":
    if sys.platform != "win32":
        try:
            import uvloop

            uvloop.install()
        except ImportError:
            pass
    bot = Bot()
    bot.run()
