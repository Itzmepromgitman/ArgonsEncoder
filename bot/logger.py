# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import logging
import os
import time
from html import escape
from logging.handlers import RotatingFileHandler

from bot.config import (
    ERROR_LOGS_TO_TELEGRAM,
    LOG_CHANNEL,
    LOG_DIR,
    LOG_FILE_NAME,
)

os.makedirs(LOG_DIR, exist_ok=True)

formatter = logging.Formatter(
    fmt="%(asctime)s - %(name)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(LOG_FILE_NAME, maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(),
    ],
)

# Track in-flight Telegram log tasks so they cannot be garbage-collected.
_pending_tasks = set()
# Cooldown map: message-key -> last sent timestamp (flood protection).
_send_cooldown = {}
COOLDOWN_SECONDS = 120


class TelegramLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.client = None

    def emit(self, record):
        if (
            self.client
            and ERROR_LOGS_TO_TELEGRAM
            and record.levelno >= logging.ERROR
        ):
            try:
                msg = self.format(record)
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return  # No running loop; skip Telegram delivery.

                key = f"{record.name}:{record.getMessage()}"[:200]
                now = time.time()
                if len(_send_cooldown) > 1000:
                    cutoff = now - COOLDOWN_SECONDS * 2
                    retained_cooldowns = {
                        item_key: stamp
                        for item_key, stamp in _send_cooldown.items()
                        if stamp >= cutoff
                    }
                    _send_cooldown.clear()
                    _send_cooldown.update(retained_cooldowns)
                if now - _send_cooldown.get(key, 0) < COOLDOWN_SECONDS:
                    return
                _send_cooldown[key] = now

                task = loop.create_task(self.send_log(msg))
                _pending_tasks.add(task)
                task.add_done_callback(_pending_tasks.discard)
            except Exception:
                self.handleError(record)

    async def send_log(self, msg):
        try:
            if len(msg) > 4000:
                msg = msg[:4000] + "..."

            text = f"❌ <b>Error Log</b>\n\n<code>{escape(msg)}</code>"
            await self.client.send_message(LOG_CHANNEL, text)
        except Exception:
            pass


tg_handler = TelegramLogHandler()
tg_handler.setFormatter(formatter)
logging.getLogger().addHandler(tg_handler)

for handler in logging.getLogger().handlers:
    handler.setFormatter(formatter)

logging.getLogger("pyrogram").setLevel(logging.ERROR)


def LOGGER(name: str = "App"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    for handler in logger.handlers:
        handler.setFormatter(formatter)

    return logger


async def send_logs(client, message):
    if os.path.exists(LOG_FILE_NAME):
        await message.reply_document(LOG_FILE_NAME, caption="📄 Here are the latest logs.")
    else:
        await message.reply_text("⚠️ No logs found.")
