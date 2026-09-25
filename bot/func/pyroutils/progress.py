# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import time

from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.logger import LOGGER
from bot.utils.format import TimeFormatter, humanbytes
from bot.utils.ui import progress_bar

log = LOGGER(__name__)


# Global state for rate limiting
_progress_state = {}
# Cancel flags keyed by progress message id; progress callback raises to abort.
_cancel_flags = {}


def _transfer_key(message) -> str:
    return f"{message.chat.id}_{message.id}"


def flag_cancel(message) -> None:
    _cancel_flags[_transfer_key(message)] = True


def clear_cancel(message) -> None:
    key = _transfer_key(message)
    _cancel_flags.pop(key, None)
    _progress_state.pop(key, None)


def is_cancelled(message) -> bool:
    return _cancel_flags.get(_transfer_key(message), False)


async def progress_for_pyrogram(
    current, total, ud_type, message, start, last_update_time=None
):
    unique_id = _transfer_key(message)
    if _cancel_flags.get(unique_id):
        raise RuntimeError("Transfer cancelled by user")
    last_time = _progress_state.get(unique_id, 0)

    now = time.time()
    diff = now - start

    if now - last_time >= 3 or current == total:
        if total == 0:
            percentage = 0
        else:
            percentage = current * 100 / total

        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0
        estimated_total_time = elapsed_time + time_to_completion

        elapsed_time_str = TimeFormatter(elapsed_time)
        estimated_total_time_str = TimeFormatter(estimated_total_time) if estimated_total_time else "calculating…"

        progress_bar_text = progress_bar(percentage, width=20)

        if percentage == 100:
            status_emoji = "✅"
        elif percentage >= 75:
            status_emoji = "🔥"
        elif percentage >= 50:
            status_emoji = "⏳"
        elif percentage >= 25:
            status_emoji = "🚀"
        else:
            status_emoji = "▶️"

        progress_text = (
            f"{status_emoji} <b>{ud_type}</b>\n"
            f"<blockquote><code>{progress_bar_text}</code> <b>{percentage:.1f}%</b>\n"
            f"📦 {humanbytes(current)} / {humanbytes(total)}\n"
            f"⚡ {humanbytes(speed)}/s · ⏱ {elapsed_time_str} · ⏳ ETA {estimated_total_time_str}</blockquote>"
        )

        _progress_state[unique_id] = now
        if len(_progress_state) > 5000:
            _progress_state.pop(next(iter(_progress_state)))
        try:
            await message.edit(
                text=progress_text,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="cb_xfer_cancel")]]
                ),
            )
            if current == total:
                _progress_state.pop(unique_id, None)
        except FloodWait as exc:
            await asyncio.sleep(exc.value)
        except Exception as exc:
            if "MESSAGE_NOT_MODIFIED" in str(exc):
                return
            if "MESSAGE_ID_INVALID" in str(exc):
                _progress_state.pop(unique_id, None)
                return
            log.error(f"Error updating progress: {exc}")
