# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import sys
from html import escape

from bot.config import UPDATE_ON_START
from bot.decorator import task
from bot.logger import LOGGER

log = LOGGER(__name__)


@task
async def restart_bot(client, message):
    await message.reply_text("🔄 Updating and restarting bot...")

    # Persist queue state BEFORE anything destructive happens.
    try:
        from bot.func.upload_manager import upload_manager

        if not await upload_manager.shutdown():
            raise RuntimeError("Pending upload recovery could not be persisted")
    except Exception as e:
        log.error(f"Pre-restart upload shutdown failed: {e}")
        await message.reply_text(
            "⚠️ <b>Restart aborted safely.</b>\n"
            "<i>Pending delivery recovery could not be persisted.</i>"
        )
        try:
            from bot.func.upload_manager import upload_manager

            upload_manager.resume()
            await upload_manager.start()
        except Exception:
            pass
        try:
            from bot.func.encode import UPLOAD_RECOVERY_HEALTHY, restore_upload_retries
            from database import privacy_admission_lock

            async with privacy_admission_lock:
                await restore_upload_retries(client)
            if not UPLOAD_RECOVERY_HEALTHY:
                raise RuntimeError("Upload recovery manifest is not durable")
        except Exception:
            pass
        return
    try:
        from bot.func.queue_manager import queue_manager

        if not await queue_manager.shutdown():
            raise RuntimeError("Queue state could not be persisted; restart aborted")
    except Exception as e:
        log.error(f"Pre-restart save failed: {e}")
        await message.reply_text(
            "⚠️ <b>Restart aborted safely.</b>\n"
            "<i>The queue snapshot could not be persisted.</i>"
        )
        try:
            from bot.func.queue_manager import queue_manager

            queue_manager.resume()
            await queue_manager.start()
        except Exception:
            pass
        try:
            from bot.func.upload_manager import upload_manager

            upload_manager.resume()
            await upload_manager.start()
        except Exception:
            pass
        try:
            from bot.func.encode import UPLOAD_RECOVERY_HEALTHY, restore_upload_retries
            from database import privacy_admission_lock

            async with privacy_admission_lock:
                await restore_upload_retries(client)
            if not UPLOAD_RECOVERY_HEALTHY:
                raise RuntimeError("Upload recovery manifest is not durable")
        except Exception:
            pass
        return

    try:
        if UPDATE_ON_START:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "update.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode == 0:
                await message.reply_text("✅ Update successful! Restarting bot...")
            else:
                tail = err.decode(errors="replace")[-300:]
                await message.reply_text(
                    f"⚠️ Update failed (rc={proc.returncode}); restarting anyway...\n"
                    f"<code>{escape(tail)}</code>"
                )
        else:
            await message.reply_text("♻️ Restarting bot...")

        # Replace the current process with a fresh bot.
        os.execv(sys.executable, [sys.executable, "-m", "bot"])
    except Exception as e:
        recovery_ok = True
        try:
            from bot.func.upload_manager import upload_manager

            upload_manager.resume()
            await upload_manager.start()
        except Exception:
            pass
        try:
            from bot.func.queue_manager import queue_manager
            from database import privacy_admission_lock

            queue_manager.resume()
            await queue_manager.start()
            async with privacy_admission_lock:
                restored_ok = await queue_manager.restore_queue(client)
            if not restored_ok:
                recovery_ok = False
                log.error("Queue restoration failed after restart abort")
        except Exception as restore_error:
            recovery_ok = False
            log.error(f"Queue restoration failed after restart abort: {restore_error}")
        try:
            from bot.func.encode import UPLOAD_RECOVERY_HEALTHY, restore_upload_retries
            from database import privacy_admission_lock

            async with privacy_admission_lock:
                await restore_upload_retries(client)
            if not UPLOAD_RECOVERY_HEALTHY:
                raise RuntimeError("Upload recovery manifest is not durable")
        except Exception as restore_error:
            recovery_ok = False
            log.error(f"Upload recovery restoration failed: {restore_error}")
        log.error(f"Restart failed: {e}", exc_info=True)
        try:
            await message.reply_text(
                f"❌ <b>Restart failed:</b> <code>{escape(str(e))}</code>\n"
                f"<i>The bot is still running the old code.</i>"
                + ("" if recovery_ok else "\n⚠️ Recovery state could not be fully restored; inspect operator logs.")
            )
        except Exception:
            pass
