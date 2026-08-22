# Developed by ARGON telegram: @REACTIVEARGON
from html import escape

from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import OWNER_ID
from bot.func.encode import active_encodings, resume_encoding_job
from bot.func.queue_manager import queue_manager
from bot.logger import LOGGER

log = LOGGER(__name__)


def render_queue_text(jobs, for_user: int = 0) -> str:
    """Unified queue rendering used by /queue and the in-progress view."""
    total = 0
    lines = []
    for i, job in enumerate(jobs, 1):
        if job.status == "running":
            icon = "🎬"
            proc = active_encodings.get(job.job_id)
            extra = ""
            if proc is not None:
                pct = proc.stats.percent
                eta = proc.stats.eta
                extra = f"\n      {pct:.1f}% · ETA {eta}"
        elif job.status == "yielded":
            icon = "⏸"
            extra = ""
        else:
            icon = "⏳"
            size = job.file_size if job.file_size != "Unknown" else ""
            extra = f" · {size}" if size else ""

        name = escape(job.file_name or "Unknown")
        owner = ""
        if for_user == OWNER_ID:
            owner = f" · 👤 <code>{job.user_id}</code>"

        lines.append(f"{i}️⃣ {icon} <code>{name}</code>{extra}{owner}")

    header = f"📋 <b>Queue</b> · {len(jobs)} job(s)"
    body = "\n".join(lines) if lines else "📭 <b>Queue is empty.</b>"
    return f"{header}\n\n<blockquote>{body}</blockquote>"


async def handle_encoding_callback(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    parts = data.split("_")
    if len(parts) < 3:
        await callback_query.answer()
        return

    action = parts[1]
    job_id = parts[2]

    if job_id not in active_encodings:
        await callback_query.answer(
            "⚠️ This job already finished or was cancelled.", show_alert=True
        )
        return

    process = active_encodings[job_id]

    # Ownership check: only the job owner (or the owner of the bot).
    uid = callback_query.from_user.id
    if uid not in (process.user_id, OWNER_ID):
        await callback_query.answer("❌ That's not your job.", show_alert=True)
        return

    if action == "pause":
        if process.is_paused:
            if process.yield_queue:
                # Yielded: supersede the paused queue row, then re-queue a
                # resume task (the duplicate guard would otherwise block it).
                old = queue_manager.get_job(process.job_id)
                if old is not None and old.status == "yielded":
                    queue_manager._jobs.pop(process.job_id, None)
                    queue_manager.mark_dirty()

                async def resume_worker(jid):
                    return await resume_encoding_job(jid)

                queued_id = await queue_manager.add_job(
                    process.user_id,
                    resume_worker,
                    process.job_id,
                    file_name=process.file_name,
                    task_type="resume",
                )
                if queued_id is None:
                    await callback_query.answer(
                        "⚠️ Could not re-queue resume (duplicate/limit).",
                        show_alert=True,
                    )
                    return

                q_pos = queue_manager.queue_position(queued_id)
                await callback_query.answer(f"⏳ Resume queued at #{q_pos}")
                try:
                    await process.message.edit(
                        f"<blockquote>⏳ <b>Resume Queued</b>\n"
                        f"🆔 <code>{process.job_id}</code>\n"
                        f"🔢 Position: {q_pos}</blockquote>",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel",
                                        callback_data=f"enc_cancel_{job_id}",
                                    )
                                ]
                            ]
                        ),
                    )
                except Exception as e:
                    log.error(f"Failed to edit message in callback: {e}")
            else:
                await process.resume()
                await callback_query.answer("▶️ Resumed")
        else:
            await process.pause()
            await callback_query.answer("⏸ Paused")

    elif action == "cancel":
        was_paused = process.is_paused
        await process.cancel()

        if was_paused and process.yield_queue:
            # Nobody monitors a yielded process; clean up manually.
            try:
                await process.message.edit("🚫 <b>Encoding Cancelled</b>")
            except Exception:
                pass
            try:
                if process.input_file and os_path_exists(process.input_file):
                    os_remove(process.input_file)
                if process.output_file and os_path_exists(process.output_file):
                    os_remove(process.output_file)
            except Exception as e:
                log.error(f"Failed to clean yielded job files: {e}")
            active_encodings.pop(job_id, None)

        await callback_query.answer("🚫 Cancelled")

    elif action == "queue":
        process.is_viewing_queue = True
        jobs = queue_manager.get_all_jobs()
        text = render_queue_text(jobs, for_user=uid)
        buttons = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data=f"enc_back_{job_id}")]]
        )
        try:
            await process.message.edit(text, reply_markup=buttons)
        except Exception as e:
            log.error(f"Queue view edit failed: {e}")
        await callback_query.answer()

    elif action == "back":
        process.is_viewing_queue = False
        try:
            await process.message.edit(
                process.get_progress_ui(), reply_markup=progress_markup(process)
            )
        except Exception as e:
            log.error(f"Back-to-progress edit failed: {e}")
        await callback_query.answer()


def progress_markup(process) -> InlineKeyboardMarkup:
    pause_text = "▶️ Resume" if process.is_paused else "⏸ Pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    pause_text, callback_data=f"enc_pause_{process.job_id}"
                ),
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"enc_cancel_{process.job_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Queue", callback_data=f"enc_queue_{process.job_id}"
                ),
            ],
        ]
    )


def os_path_exists(path: str) -> bool:
    import os

    return os.path.exists(path)


def os_remove(path: str):
    import os

    os.remove(path)
