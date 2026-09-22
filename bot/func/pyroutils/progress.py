# Developed by ARGON telegram: @REACTIVEARGON
import math
import time

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.logger import LOGGER
from bot.utils.format import TimeFormatter, humanbytes

log = LOGGER(__name__)


# Global state for rate limiting
_progress_state = {}
# Cancel flags keyed by progress message id; progress callback raises to abort.
_cancel_flags = {}


def flag_cancel(message_id: int) -> None:
    _cancel_flags[message_id] = True


def clear_cancel(message_id: int) -> None:
    _cancel_flags.pop(message_id, None)


def is_cancelled(message_id: int) -> bool:
    return _cancel_flags.get(message_id, False)


async def progress_for_pyrogram(
    current, total, ud_type, message, start, last_update_time=None
):
    if _cancel_flags.get(message.id):
        raise RuntimeError("Transfer cancelled by user")
    # Use global state for rate limiting
    unique_id = f"{message.chat.id}_{message.id}"
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

        # Enhanced progress bar
        filled = math.floor(percentage / 5)  # 20 blocks
        progress_bar = "▰" * filled + "▱" * (20 - filled)

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
            f"<blockquote><code>{progress_bar}</code> <b>{percentage:.1f}%</b>\n"
            f"📦 {humanbytes(current)} / {humanbytes(total)}\n"
            f"⚡ {humanbytes(speed)}/s · ⏱ {elapsed_time_str} · ⏳ ETA {estimated_total_time_str}</blockquote>"
        )

        try:
            await message.edit(
                text=progress_text,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="cb_xfer_cancel")]]
                ),
            )
            if current == total:
                if unique_id in _progress_state:
                    del _progress_state[unique_id]
            else:
                _progress_state[unique_id] = now
        except Exception as e:
            if "MESSAGE_ID_INVALID" in str(e):
                # Message was deleted, stop updating
                if unique_id in _progress_state:
                    del _progress_state[unique_id]
            else:
                log.error(f"Error updating progress: {e}")
