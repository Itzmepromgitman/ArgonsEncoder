# Developed by ARGON telegram: @REACTIVEARGON
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from bot.func.editquery import handle_encoding_callback
from bot.logger import LOGGER

log = LOGGER(__name__)

# Rate-limit state with bounded size.
user_last_interaction = {}
_MAX_TRACKED_USERS = 5000


def _prune_if_needed():
    if len(user_last_interaction) > _MAX_TRACKED_USERS:
        # Drop the oldest half (dicts preserve insertion order).
        for key in list(user_last_interaction.keys())[: len(user_last_interaction) // 2]:
            user_last_interaction.pop(key, None)


@Client.on_callback_query(filters.regex(r"^enc_"))
async def encoding_callback_handler(client: Client, callback_query: CallbackQuery):
    """Handler for encoding callbacks with rate limiting."""
    user_id = callback_query.from_user.id
    current_time = time.time()

    last = user_last_interaction.get(user_id, 0)
    if current_time - last < 2.0:
        await callback_query.answer(
            "⚠️ Please wait 2 seconds between actions.", show_alert=True
        )
        return

    user_last_interaction[user_id] = current_time
    _prune_if_needed()

    await handle_encoding_callback(client, callback_query)
