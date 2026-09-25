# Developed by ARGON telegram: @REACTIVEARGON
import time
from html import escape

from pyrogram import Client

from .config import ERROR_LOGS_TO_TELEGRAM, LOG_CHANNEL, OWNER_ID
from .logger import LOGGER

log = LOGGER("decorator")

# Simple TTL caches so we do not hit MongoDB on every update.
_ban_cache = {"ids": set(), "fetched_at": 0.0, "failed": False}
_admin_cache = {"ids": set(), "fetched_at": 0.0}
CACHE_TTL = 60.0


async def get_banned_users() -> set:
    now = time.time()
    if now - _ban_cache["fetched_at"] > CACHE_TTL:
        try:
            from database import get_variable_strict

            _ban_cache["ids"] = set(await get_variable_strict("banned_users", []))
            _ban_cache["failed"] = False
        except Exception as e:
            _ban_cache["failed"] = True
            log.error(f"Failed to load banned users: {e}")
        _ban_cache["fetched_at"] = now
    return _ban_cache["ids"]


async def get_admins() -> set:
    now = time.time()
    if now - _admin_cache["fetched_at"] > CACHE_TTL:
        try:
            from database import get_variable_strict

            _admin_cache["ids"] = set(await get_variable_strict("admin", []))
        except Exception as e:
            log.error(f"Failed to load admins: {e}")
        _admin_cache["fetched_at"] = now
    return _admin_cache["ids"]


def invalidate_user_caches():
    _ban_cache["fetched_at"] = 0.0
    _ban_cache["failed"] = False
    _admin_cache["fetched_at"] = 0.0


async def is_banned(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return False
    banned = await get_banned_users()
    if _ban_cache.get("failed"):
        return True
    return user_id in banned


async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    admins = await get_admins()
    return user_id in admins


def task(func):
    logger = LOGGER(func.__name__)

    async def wrapper(*args, **kwargs):
        try:
            # Ban enforcement: second positional arg is normally the Message.
            if len(args) >= 2 and hasattr(args[1], "from_user"):
                fuser = getattr(args[1], "from_user", None)
                if fuser is not None and await is_banned(fuser.id):
                    try:
                        await args[1].reply_text(
                            "🚫 <b>You are banned from using this bot.</b>"
                        )
                    except Exception:
                        pass
                    return None

            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in '{func.__name__}': {e}", exc_info=True)

            client = args[0] if args else None
            if isinstance(client, Client):
                # Prefer user-facing feedback on the triggering message.
                msg = args[1] if len(args) >= 2 and hasattr(args[1], "reply_text") else None
                if msg is not None:
                    try:
                        await msg.reply_text(
                            "❌ <b>Something went wrong.</b>\n"
                            "<i>The error was logged — please try again.</i>"
                        )
                    except Exception:
                        pass
                if ERROR_LOGS_TO_TELEGRAM:
                    try:
                        await client.send_message(
                            chat_id=LOG_CHANNEL,
                            text=(
                                f"Error in '{escape(func.__name__)}': "
                                f"<code>{escape(str(e))[:1000]}</code>"
                            ),
                        )
                    except Exception as ex:
                        logger.error(f"Failed to send error log to LOG_CHANNEL: {ex}")

    return wrapper
