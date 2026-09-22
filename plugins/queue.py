# Developed by ARGON telegram: @REACTIVEARGON
import time

import psutil
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import OWNER_ID
from bot.decorator import task
from bot.func.editquery import render_queue_text
from bot.func.encode import active_encodings
from bot.func.queue_manager import queue_manager
from bot.logger import LOGGER
from bot.utils.ui import ICONS, btn, close_btn, empty_state, refresh_btn, safe_edit, truncate

log = LOGGER(__name__)

BOT_START_TIME = time.time()


def _uptime_text() -> str:
    from bot.utils.format import format_time

    return format_time((int(time.time() - BOT_START_TIME) * 1000))


def _queue_keyboard(jobs, refresh_cb: str = "queue_view") -> InlineKeyboardMarkup:
    """Numbered cancel buttons with a clear header action row."""
    buttons = []
    if jobs:
        row = []
        for i, job in enumerate(jobs[:9], 1):
            row.append(btn(f"{i} 🚫", f"qcancel_{job.job_id}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([refresh_btn(refresh_cb)])
    buttons.append([close_btn()])
    return InlineKeyboardMarkup(buttons)


@Client.on_message(filters.command("cancel"))
@task
async def cancel_command(client: Client, message: Message):
    try:
        args = message.command
        user_id = message.from_user.id

        if len(args) < 2:
            await message.reply_text(
                "⚠️ Usage: <code>/cancel JOB_ID</code>\n<i>Get IDs from /queue.</i>"
            )
            return

        job_id = args[1]
        job = queue_manager.get_job(job_id)

        if not job:
            await message.reply_text(
                f"❌ No job <code>{job_id}</code> found — it may have already finished."
            )
            return

        if job.user_id != user_id and user_id != OWNER_ID:
            await message.reply_text("❌ That's not your job.")
            return

        if job.status == "running":
            if await _cancel_single_job(job_id):
                await message.reply_text(f"🚫 Job <code>{job_id}</code> cancelled.")
            else:
                await message.reply_text(f"⚠️ Could not cancel job <code>{job_id}</code>.")
        else:
            if await queue_manager.cancel_job(job_id):
                await message.reply_text(f"🚫 Job <code>{job_id}</code> removed from queue.")
            else:
                await message.reply_text(f"⚠️ Could not cancel job <code>{job_id}</code>.")

    except Exception as e:
        log.error(f"Error in cancel command: {e}")
        await message.reply_text("❌ An error occurred.")


def _user_queue_view(user_id: int):
    is_owner = user_id == OWNER_ID
    jobs = queue_manager.get_all_jobs() if is_owner else queue_manager.get_user_jobs(user_id)
    text = render_queue_text(jobs, for_user=user_id)
    if jobs:
        text += "\n\n<i>Tap 🚫 next to a number to cancel that job.</i>"
    return jobs, text


@Client.on_message(filters.command("queue"))
@task
async def queue_command(client: Client, message: Message):
    user_id = message.from_user.id
    jobs, text = _user_queue_view(user_id)
    await message.reply_text(text, reply_markup=_queue_keyboard(jobs))


@Client.on_callback_query(filters.regex(r"^queue_view$"))
async def queue_view_refresh(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    jobs, text = _user_queue_view(user_id)
    try:
        await safe_edit(callback_query.message, text, _queue_keyboard(jobs))
    except Exception:
        pass
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^qcancel_"))
async def queue_cancel_single(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    job_id = callback_query.data.replace("qcancel_", "", 1)

    job = queue_manager.get_job(job_id)
    if not job:
        await callback_query.answer("Job already finished.", show_alert=True)
        return
    if job.user_id != user_id and user_id != OWNER_ID:
        await callback_query.answer("❌ That's not your job.", show_alert=True)
        return

    ok = await _cancel_single_job(job_id)
    await callback_query.answer("🚫 Cancelled" if ok else "⚠️ Could not cancel")

    # Refresh the view the user is looking at.
    callback_query.data = "queue_view"
    await queue_view_refresh(client, callback_query)


@Client.on_message(filters.command("status"))
@task
async def status_command(client: Client, message: Message):
    text, buttons = _status_card()
    await message.reply_text(text, reply_markup=buttons)


def _status_card():
    jobs = queue_manager.get_all_jobs()
    running = [j for j in jobs if j.status in ("running", "yielded")]
    pending = len(jobs) - len(running)

    # Prime CPU so the first reading is not 0%.
    psutil.cpu_percent(interval=None)
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage(".").percent
    free_gb = psutil.disk_usage(".").free / (1024 ** 3)

    running_lines = ""
    for job in running[:5]:
        proc = active_encodings.get(job.job_id)
        pct = f"{proc.stats.percent:.0f}%" if proc else "?"
        name = job.file_name if job.file_name != "Unknown" else job.job_id
        icon = ICONS.pause if job.status == "yielded" else ICONS.encode
        running_lines += f"{icon} <code>{truncate(name, 28)}</code> · {pct}\n"

    load_block = (
        f"🖥 CPU <code>{cpu}%</code> · RAM <code>{ram}%</code>\n"
        f"💾 Disk <code>{disk}%</code> · Free <code>{free_gb:.1f} GB</code>"
    )

    text = (
        f"{ICONS.status} <b>Live status</b>\n"
        f"<blockquote>🎬 Encoding: <code>{len(running)}</code> · ⏳ Queued: <code>{pending}</code>\n"
        f"⏱ Uptime: <code>{_uptime_text()}</code></blockquote>"
    )
    if running_lines:
        text += f"\n<blockquote>{running_lines.rstrip()}</blockquote>"
    text += f"\n<blockquote>{load_block}</blockquote>"

    buttons = InlineKeyboardMarkup(
        [
            [
                refresh_btn("st_refresh"),
                close_btn(),
            ]
        ]
    )
    return text, buttons


@Client.on_callback_query(filters.regex(r"^st_refresh$"))
async def status_refresh(client: Client, callback_query: CallbackQuery):
    text, buttons = _status_card()
    try:
        await safe_edit(callback_query.message, text, buttons)
    except Exception:
        pass
    await callback_query.answer()


def _jobs_dashboard(jobs):
    lines = []
    row = []
    buttons = []
    for i, job in enumerate(jobs[:15], 1):
        icon = {"running": ICONS.encode, "yielded": ICONS.pause, "pending": "⏳"}.get(job.status, "•")
        lines.append(
            f"{i}️⃣ {icon} <code>{truncate(job.file_name, 24)}</code> · 👤 <code>{job.user_id}</code>"
        )
        row.append(btn(f"{i} ❌", f"qj_{job.job_id}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([refresh_btn("jobs_view")])
    buttons.append([close_btn()])

    body = "\n".join(lines)
    text = (
        f"{ICONS.jobs} <b>All jobs</b> · <code>{len(jobs)}</code>\n"
        f"<blockquote>{body}</blockquote>\n"
        f"<i>Tap a number to cancel that job.</i>"
    )
    return text, InlineKeyboardMarkup(buttons)


@Client.on_message(filters.command("jobs") & filters.user(OWNER_ID))
async def jobs_command(client: Client, message: Message):
    """Owner-only fleet-wide job dashboard with per-job cancel buttons."""
    jobs = queue_manager.get_all_jobs()
    if not jobs:
        await message.reply_text(
            empty_state("No active jobs", "Queue and workers are idle.", "Upload a video to get started.")
        )
        return

    text, buttons = _jobs_dashboard(jobs)
    await message.reply_text(text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^jobs_view$"))
async def jobs_view_refresh(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Owner only.", show_alert=True)
        return
    jobs = queue_manager.get_all_jobs()
    if not jobs:
        try:
            await safe_edit(
                callback_query.message,
                empty_state("No active jobs", "Queue and workers are idle."),
            )
        except Exception:
            pass
        await callback_query.answer()
        return

    text, buttons = _jobs_dashboard(jobs)
    try:
        await safe_edit(callback_query.message, text, buttons)
    except Exception:
        pass
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^qj_"))
async def qj_cancel(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Owner only.", show_alert=True)
        return

    job_id = callback_query.data.replace("qj_", "", 1)
    ok = await _cancel_single_job(job_id)
    await callback_query.answer("🚫 Cancelled" if ok else "⚠️ Not cancellable")
    callback_query.data = "jobs_view"
    await jobs_view_refresh(client, callback_query)


@Client.on_message(filters.command("info") & filters.user(OWNER_ID))
async def info_command(client: Client, message: Message):
    try:
        args = message.command
        if len(args) < 2:
            await message.reply_text("⚠️ Usage: <code>/info JOB_ID</code>")
            return

        job_id = args[1]
        job = queue_manager.get_job(job_id)

        if not job:
            await message.reply_text("⚠️ Job not found (or already finished).")
            return

        try:
            user = await client.get_users(job.user_id)
            user_text = f"{user.mention} (<code>{user.id}</code>)"
            username = f"@{user.username}" if user.username else "N/A"
        except Exception:
            user_text = f"User ID: <code>{job.user_id}</code>"
            username = "Unknown"

        status_map = {
            "pending": "⏳ Pending",
            "running": "🎬 Running",
            "yielded": "⏸ Paused",
            "completed": "✅ Completed",
            "failed": "❌ Failed",
            "cancelled": "🚫 Cancelled",
        }
        status = status_map.get(job.status, job.status)

        text = (
            f"{ICONS.info} <b>Job information</b>\n\n"
            f"<blockquote>🆔 <b>ID:</b> <code>{job.job_id}</code>\n"
            f"📊 <b>Status:</b> {status}</blockquote>\n\n"
            f"<blockquote>👤 <b>User</b>\n"
            f"├ Name: {user_text}\n"
            f"└ Username: {username}</blockquote>\n\n"
            f"<blockquote>📁 <b>File</b>\n"
            f"├ Name: <code>{escape_filename(job.file_name)}</code>\n"
            f"└ Size: {job.file_size}</blockquote>"
        )

        await message.reply_text(text)

    except Exception as e:
        log.error(f"Error in info command: {e}")
        await message.reply_text("❌ An error occurred.")


def escape_filename(name: str) -> str:
    from html import escape

    return escape(str(name or ""))


@Client.on_message(filters.command("clear"))
@task
async def clear_command(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id == OWNER_ID:
        count_all = len(queue_manager.get_all_jobs())
        count_mine = len(queue_manager.get_user_jobs(user_id))
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"👤 My jobs ({count_mine})", callback_data="queue_clear_mine"
                    ),
                    InlineKeyboardButton(
                        f"🌐 All jobs ({count_all})", callback_data="queue_confirm_all"
                    ),
                ],
                [close_btn("Cancel")],
            ]
        )
        await message.reply_text(
            f"{ICONS.queue} <b>Queue control</b>\n<i>Choose what to clear:</i>",
            reply_markup=buttons,
        )
    else:
        count = len(queue_manager.get_user_jobs(user_id))
        if count:
            await message.reply_text(
                f"⚠️ <b>Clear {count} queued job(s)?</b>\n<i>This cannot be undone.</i>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Yes, clear", callback_data="queue_clear_mine"
                            ),
                            InlineKeyboardButton("❌ No", callback_data="cb_close"),
                        ]
                    ]
                ),
            )
        else:
            await message.reply_text(
                empty_state("Nothing to clear", "You have no queued jobs.")
            )


@Client.on_message(filters.command("cancelall") & filters.user(OWNER_ID))
async def cancel_all_command(client: Client, message: Message):
    count = len(queue_manager.get_all_jobs())
    await message.reply_text(
        f"⚠️ <b>Cancel all {count} jobs?</b>\n<i>This cannot be undone.</i>",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes, cancel all", callback_data="queue_confirm_all"
                    ),
                    InlineKeyboardButton("❌ No", callback_data="cb_close"),
                ]
            ]
        ),
    )


@Client.on_callback_query(filters.regex(r"^queue_(clear_mine|confirm_all|cancel)$"))
async def queue_callback_handler(client: Client, callback_query: CallbackQuery):
    action = callback_query.data
    user_id = callback_query.from_user.id

    # Auth: clear_mine is allowed for the requesting user; confirm_all is owner-only.
    if action in ("queue_clear_mine",) and user_id != callback_query.message.chat.id:
        # Non-owner may only clear their own jobs via their own confirmation.
        pass
    if action == "queue_confirm_all" and user_id != OWNER_ID:
        await callback_query.answer("❌ Owner only.", show_alert=True)
        return
    if action not in ("queue_clear_mine", "queue_confirm_all", "queue_cancel") and user_id != OWNER_ID:
        await callback_query.answer("❌ Owner only.", show_alert=True)
        return

    if action == "queue_clear_mine":
        count = await _cancel_user_jobs(user_id)
        try:
            await safe_edit(callback_query.message, f"✅ Cleared {count} of your jobs.")
        except Exception:
            pass
        await callback_query.answer()

    elif action == "queue_confirm_all":
        count = await _cancel_all_jobs()
        try:
            await safe_edit(callback_query.message, f"✅ Cancelled all jobs ({count}).")
        except Exception:
            pass
        await callback_query.answer()

    elif action == "queue_cancel":
        try:
            await callback_query.message.delete()
        except Exception:
            pass
        await callback_query.answer()

    else:
        await callback_query.answer()


async def _cancel_user_jobs(user_id: int) -> int:
    jobs = queue_manager.get_user_jobs(user_id)
    count = 0
    for job in jobs:
        if await _cancel_single_job(job.job_id):
            count += 1
    return count


async def _cancel_all_jobs() -> int:
    count = len(queue_manager.get_all_jobs())

    # cancel_job uniformly handles running (proc kill), yielded (proc kill +
    # file cleanup) and pending (file cleanup) jobs.
    for job in queue_manager.get_all_jobs():
        try:
            await queue_manager.cancel_job(job.job_id)
        except Exception as e:
            log.error(f"Cancel failed for {job.job_id}: {e}")

    await queue_manager.clear_queue()
    return count


async def _cancel_single_job(job_id: str) -> bool:
    if await queue_manager.cancel_job(job_id):
        return True

    # Job row may be gone (terminal/evicted) while a process still runs.
    if job_id in active_encodings:
        try:
            await active_encodings[job_id].cancel()
            return True
        except Exception as e:
            log.error(f"Cancel failed for {job_id}: {e}")
    return False
