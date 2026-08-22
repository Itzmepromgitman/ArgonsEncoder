# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import sys

from bot.config import UPDATE_ON_START
from bot.decorator import task
from bot.logger import LOGGER

log = LOGGER(__name__)


@task
async def restart_bot(client, message):
    await message.reply_text("🔄 Updating and restarting bot...")

    # Persist queue state BEFORE anything destructive happens.
    try:
        from bot.func.queue_manager import queue_manager

        await queue_manager.shutdown()
    except Exception as e:
        log.error(f"Pre-restart save failed: {e}")

    try:
        if UPDATE_ON_START:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "update.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode == 0:
                await message.reply_text("✅ Update successful! Restarting bot...")
            else:
                tail = err.decode(errors="replace")[-300:]
                await message.reply_text(
                    f"⚠️ Update failed (rc={proc.returncode}); restarting anyway...\n"
                    f"<code>{tail}</code>"
                )
        else:
            await message.reply_text("♻️ Restarting bot...")

        # Replace the current process with a fresh bot.
        os.execv(sys.executable, [sys.executable, "-m", "bot"])
    except Exception as e:
        log.error(f"Restart failed: {e}", exc_info=True)
        try:
            await message.reply_text(
                f"❌ <b>Restart failed:</b> <code>{e}</code>\n"
                f"<i>The bot is still running the old code.</i>"
            )
        except Exception:
            pass
