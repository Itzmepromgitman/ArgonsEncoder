# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import glob
import os
import shutil
import sys

from pyrogram import Client
from pyrogram.types import BotCommand, BotCommandScopeChat

from bot.config import (
    API_HASH,
    APP_ID,
    DOWNLOAD_DIR,
    MAX_CONCURRENT_TRANSMISSIONS,
    OWNER_ID,
    PORT,
    SESSION_DB_KEY,
    TG_BOT_TOKEN,
    TG_BOT_WORKERS,
    THUMB_DIR,
    WATERMARK_DIR,
)
from database import get_variable, set_variable

from .logger import LOGGER, tg_handler

log = LOGGER(__name__)


async def get_session():
    try:
        return await get_variable(SESSION_DB_KEY, None)
    except Exception as e:
        log.warning(f"Could not load stored session: {e}")
        return None


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
        if os.path.exists(DOWNLOAD_DIR):
            shutil.rmtree(DOWNLOAD_DIR)
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
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
        # Try to reuse a persisted user session; fall back to the bot account.
        session = None
        if sys.platform != "win32":
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                session = loop.run_until_complete(get_session())
            except Exception as e:
                log.warning(f"Session fetch failed, using bot login: {e}")

        common = dict(
            api_id=APP_ID,
            api_hash=API_HASH,
            plugins={"root": "plugins"},
            workers=TG_BOT_WORKERS,
            max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
        )

        if session:
            super().__init__(name="user_session", session_string=session, **common)
        else:
            super().__init__(
                name="bot_session", bot_token=TG_BOT_TOKEN, **common
            )

    async def start(self):
        await super().start()
        tg_handler.client = self

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
            from bot.func.queue_manager import queue_manager

            await queue_manager.restore_queue(self)
        except Exception as e:
            log.error(f"Failed to restore queue: {e}")

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

        # Persist a user session so restarts keep the same identity.
        try:
            session = await self.export_session_string()
            await set_variable(SESSION_DB_KEY, session)
        except Exception as e:
            log.debug(f"Session export skipped: {e}")

    async def stop(self, *args, **kwargs):
        # Persist final queue state before disconnecting.
        try:
            from bot.func.queue_manager import queue_manager

            await queue_manager.shutdown()
        except Exception as e:
            log.error(f"Shutdown save failed: {e}")
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
