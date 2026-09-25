# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import copy
import json
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import (
    DOWNLOAD_DIR,
    FFMPEG_BIN,
    FFMPEG_WALL_TIMEOUT,
    FFPROBE_BIN,
    LOG_CHANNEL,
    LOG_DELIVERIES,
    MAX_FILE_SIZE,
    MAX_MEDIA_DURATION,
    MAX_OUTPUT_SIZE,
    MAX_RECOVERY_DISK_BYTES,
    PAUSED_JOB_TTL,
    THUMB_DIR,
    UI_UPDATE_INTERVAL,
    UPLOAD_RETRY_CAP,
    UPLOAD_RETRY_PER_USER,
)
from bot.func.download_manager import download_manager
from bot.func.ffmpeg_utils import generate_ffmpeg_cmd
from bot.func.media import prepare_telegram_thumbnail
from bot.func.pyroutils.progress import (
    TimeFormatter,
    clear_cancel,
    humanbytes,
    progress_for_pyrogram,
)
from bot.func.queue_manager import queue_manager
from bot.func.upload_manager import upload_manager
from bot.logger import LOGGER
from bot.utils.settings import normalize_settings
from bot.utils.ui import ICONS, progress_bar
from database import get_user_settings, inc_stats

log = LOGGER(__name__)

# Global registry of active encoding processes for callback handling.
# Map: job_id -> FFmpegProcess instance
active_encodings = {}

# Job-id -> short technical error detail (for the "Details" button). Capped.
ERROR_DETAILS: Dict[str, str] = {}
ERROR_DETAILS_OWNER: Dict[str, int] = {}
ERROR_DETAILS_CAP = 50

# error_key -> kwargs for retrying a failed upload (file kept on disk).
UPLOAD_RETRY: Dict[str, Dict[str, Any]] = {}
UPLOAD_INFLIGHT: Dict[str, Dict[str, Any]] = {}
UPLOAD_MANIFEST_ONLY: Dict[str, Dict[str, Any]] = {}
UPLOAD_RECOVERY_HEALTHY = True
UPLOAD_RETRY_LOCK = asyncio.Lock()
UPLOAD_RETRY_TTL_SECONDS = 24 * 60 * 60
UPLOAD_STATE_FILE = os.path.join(DOWNLOAD_DIR, ".upload_retries.json")


def _json_safe(value):
    if isinstance(value, bytes):
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _context_output_size(context: dict) -> int:
    total = 0
    for item in context.get("output_files", []) or []:
        path = item.get("file_path")
        if not path:
            continue
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total


def _track_failed_cleanup(key: str, user_id: int, outputs: List[Dict[str, str]]) -> None:
    outputs = [item for item in (outputs or []) if item.get("file_path")]
    if not outputs:
        return
    context = {
        "user_id": int(user_id),
        "file_path": outputs[0]["file_path"],
        "output_files": [dict(item) for item in outputs],
        "reserved_bytes": 0,
        "created_at": time.time(),
    }
    UPLOAD_MANIFEST_ONLY[key] = context
    if not _persist_upload_retries():
        log.error(f"Could not persist failed-cleanup recovery context {key}")


def register_pending_upload_recovery(job) -> bool:
    """Persist a queued upload before a process restart drops its worker queue."""
    outputs = [
        dict(item)
        for item in (job.kwargs.get("output_files") or [])
        if item.get("file_path") and os.path.isfile(item.get("file_path"))
    ]
    if not outputs:
        log.error(f"Cannot recover pending upload {job.job_id}: no output metadata")
        return False
    output_sizes = []
    for item in outputs:
        try:
            output_sizes.append(
                int(item.get("file_size", 0) or os.path.getsize(item["file_path"]))
            )
        except OSError:
            output_sizes.append(0)
    user_retry_count = sum(
        int(context.get("user_id", 0) or 0) == int(job.user_id)
        for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
    )
    pending_reserved = int(job.kwargs.get("reserved_bytes", 0) or 0)
    pending_size = _context_output_size(job.kwargs)
    recovery_size = sum(
        _context_output_size(context)
        for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
    )
    if recovery_size + pending_size > MAX_RECOVERY_DISK_BYTES:
        log.error("Cannot persist pending upload: physical recovery budget exhausted")
        return False
    recovery_reserved = sum(
        int(context.get("reserved_bytes", 0) or 0)
        for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
    )
    if recovery_reserved + pending_reserved > MAX_RECOVERY_DISK_BYTES:
        log.error("Cannot persist pending upload: recovery disk budget exhausted")
        return False
    if user_retry_count >= UPLOAD_RETRY_PER_USER:
        log.error(
            "Cannot persist pending upload for user %s: per-user recovery cap reached",
            job.user_id,
        )
        return False
    key = f"pending_{job.job_id}"
    UPLOAD_RETRY[key] = {
        "user_id": job.user_id,
        "file_path": outputs[0]["file_path"],
        "original_size": sum(output_sizes),
        "codec": job.kwargs.get("codec", "Unknown"),
        "crf": job.kwargs.get("crf", "N/A"),
        "preset": job.kwargs.get("preset", "N/A"),
        "resolution": job.kwargs.get("resolution", "N/A"),
        "thumb": job.kwargs.get("thumb"),
        "job_id": job.job_id,
        "output_files": outputs,
        "display_name": job.kwargs.get("display_name") or job.job_id,
        "delivery_settings": job.kwargs.get("delivery_settings"),
        "created_at": time.time(),
        "reserved_bytes": int(job.kwargs.get("reserved_bytes", 0) or 0),
        "_reservation_acquired": True,
    }
    if not _persist_upload_retries(preferred_key=key):
        UPLOAD_RETRY.pop(key, None)
        return False
    return True


def _persist_upload_retries(preferred_key: str | None = None) -> bool:
    """Persist restart-safe upload metadata, retaining the newly claimed item."""
    global UPLOAD_RECOVERY_HEALTHY
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        entries = []
        all_contexts = {
            **UPLOAD_MANIFEST_ONLY,
            **UPLOAD_INFLIGHT,
            **UPLOAD_RETRY,
        }
        contexts = list(all_contexts.items())
        if preferred_key and preferred_key in all_contexts:
            contexts = [
                (preferred_key, all_contexts[preferred_key]),
                *[item for item in contexts if item[0] != preferred_key],
            ]
        # The cap is a warning/soft-retention threshold, never a silent
        # truncation point: dropping an entry would make its output
        # unrecoverable after restart.
        for key, context in contexts:
            entries.append(
                {
                    "key": key,
                    "user_id": int(context.get("user_id", 0)),
                    "file_path": _json_safe(context.get("file_path")),
                    "original_size": int(context.get("original_size", 0)),
                    "codec": _json_safe(context.get("codec", "Unknown")),
                    "crf": _json_safe(context.get("crf", "N/A")),
                    "preset": _json_safe(context.get("preset", "N/A")),
                    "resolution": _json_safe(context.get("resolution", "N/A")),
                    "thumb": _json_safe(context.get("thumb")),
                    "job_id": _json_safe(context.get("job_id", "")),
                    "output_files": _json_safe(context.get("output_files") or []),
                    "display_name": _json_safe(context.get("display_name")),
                    "delivery_settings": _json_safe(context.get("delivery_settings")),
                    "created_at": context.get("created_at", time.time()),
                    "reserved_bytes": int(context.get("reserved_bytes", 0) or 0),
                }
            )
        temporary = f"{UPLOAD_STATE_FILE}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "entries": entries}, handle, indent=2)
        os.replace(temporary, UPLOAD_STATE_FILE)
        UPLOAD_RECOVERY_HEALTHY = True
        try:
            os.chmod(UPLOAD_STATE_FILE, 0o600)
        except OSError:
            pass
        return True
    except Exception as exc:
        UPLOAD_RECOVERY_HEALTHY = False
        log.warning(f"Could not persist upload retries: {exc}")
        return False


async def claim_upload_retry(key: str, user_id: int):
    """Claim a retry context without removing it until requeue succeeds."""
    async with UPLOAD_RETRY_LOCK:
        context = UPLOAD_RETRY.get(key)
        if not context or int(context.get("user_id", 0)) != int(user_id):
            return None
        if context.get("_claimed"):
            try:
                if time.time() - float(context.get("_claimed_at", 0)) < 300:
                    return None
            except (TypeError, ValueError):
                pass
        context["_claimed"] = True
        context["_claimed_at"] = time.time()
        return dict(context)


async def complete_upload_retry_claim(key: str) -> None:
    async with UPLOAD_RETRY_LOCK:
        UPLOAD_RETRY.pop(key, None)


async def restore_upload_retry(key: str, context: dict) -> None:
    async with UPLOAD_RETRY_LOCK:
        context = dict(context)
        context.pop("_claimed", None)
        context.pop("_claimed_at", None)
        UPLOAD_RETRY[key] = context


async def cleanup_expired_paused_jobs() -> int:
    """Release yielded FFmpeg jobs that have exceeded the pause retention window."""
    removed = 0
    now = time.time()
    for job_id, process in list(active_encodings.items()):
        if not process.yield_queue or not process.paused_at:
            continue
        if now - process.paused_at <= PAUSED_JOB_TTL:
            continue
        try:
            await process.cancel()
            from bot.func.queue_manager import queue_manager

            await queue_manager.cancel_job(job_id)
            removed += 1
        except Exception as exc:
            log.warning(f"Could not clean expired paused job {job_id}: {exc}")
    return removed


async def cleanup_expired_upload_retries() -> int:
    """Remove stale recovery files and release their disk reservations."""
    now = time.time()
    expired = []
    async with UPLOAD_RETRY_LOCK:
        for key, context in list(UPLOAD_RETRY.items()):
            try:
                age = now - float(context.get("created_at", now))
            except (TypeError, ValueError):
                age = 0
            claim_age = 0
            if context.get("_claimed"):
                try:
                    claim_age = time.time() - float(context.get("_claimed_at", 0))
                except (TypeError, ValueError):
                    claim_age = 0
            if age > UPLOAD_RETRY_TTL_SECONDS and not (
                context.get("_claimed") and claim_age < 300
            ):
                expired.append((key, context))
        for key, context in list(UPLOAD_MANIFEST_ONLY.items()):
            try:
                age = now - float(context.get("created_at", now))
            except (TypeError, ValueError):
                age = 0
            claim_age = 0
            if context.get("_claimed"):
                try:
                    claim_age = time.time() - float(context.get("_claimed_at", 0))
                except (TypeError, ValueError):
                    claim_age = 0
            if age > UPLOAD_RETRY_TTL_SECONDS and not (
                context.get("_claimed") and claim_age < 300
            ):
                expired.append((key, context))
    removed_count = 0
    for key, context in expired:
        file_ok = True
        for item in context.get("output_files", []):
            path = item.get("file_path")
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    file_ok = False
        if not file_ok:
            log.warning("Expired recovery file could not be removed: %s", key)
            continue
        async with UPLOAD_RETRY_LOCK:
            if key in UPLOAD_RETRY and UPLOAD_RETRY[key] is context:
                UPLOAD_RETRY.pop(key, None)
            if key in UPLOAD_MANIFEST_ONLY and UPLOAD_MANIFEST_ONLY[key] is context:
                UPLOAD_MANIFEST_ONLY.pop(key, None)
        reserved = int(context.get("reserved_bytes", 0) or 0)
        if reserved:
            try:
                await queue_manager.release_disk(reserved)
            except Exception as exc:
                log.warning(f"Could not release expired upload reservation: {exc}")
        removed_count += 1
    if removed_count and not _persist_upload_retries():
        log.warning("Expired upload cleanup was not persisted")
    return removed_count


async def restore_upload_retries(client: Client) -> int:
    """Notify users about upload outputs left by a previous process."""
    if not os.path.isfile(UPLOAD_STATE_FILE):
        return 0
    try:
        with open(UPLOAD_STATE_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
        entries = payload.get("entries", [])
    except Exception as exc:
        log.error(f"Could not read upload retry state: {exc}")
        raise RuntimeError("upload recovery manifest is unreadable") from exc

    restored = 0
    for entry in entries:
        if not isinstance(entry, dict):
            log.warning("Skipping malformed upload recovery entry")
            continue
        key = str(entry.get("key") or uuid.uuid4().hex[:8])
        raw_outputs = entry.get("output_files", [])
        if not isinstance(raw_outputs, list):
            log.warning("Skipping malformed upload recovery outputs")
            continue
        try:
            created_at = float(entry.get("created_at", time.time()))
        except (TypeError, ValueError):
            created_at = 0
        if time.time() - created_at > UPLOAD_RETRY_TTL_SECONDS:
            deletion_ok = True
            for item in entry.get("output_files", []):
                path = item.get("file_path")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        deletion_ok = False
            if not deletion_ok:
                retained = dict(entry)
                retained["reserved_bytes"] = 0
                UPLOAD_MANIFEST_ONLY[key] = retained
                log.error("Expired recovery file could not be removed: %s", key)
            continue
        outputs = [
            item for item in raw_outputs
            if isinstance(item, dict)
            and item.get("file_path")
            and os.path.isfile(item["file_path"])
        ]
        if not outputs:
            continue
        from database import tombstone_state
        if key in UPLOAD_MANIFEST_ONLY:
            manifest_context = UPLOAD_MANIFEST_ONLY[key]
            try:
                manifest_user_id = int(manifest_context.get("user_id", 0) or 0)
            except (TypeError, ValueError):
                manifest_user_id = 0
            manifest_state = await tombstone_state(manifest_user_id) if manifest_user_id else "no"
            if manifest_state == "yes":
                deletion_ok = True
                for item in manifest_context.get("output_files", []):
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                if deletion_ok:
                    UPLOAD_MANIFEST_ONLY.pop(key, None)
                else:
                    log.error("Could not remove tombstoned manifest-only files: %s", key)
                continue
            if manifest_state == "unknown":
                continue
            UPLOAD_MANIFEST_ONLY.pop(key, None)
        try:
            entry_user_id = int(entry.get("user_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        from database import tombstone_state

        tombstone_status = await tombstone_state(entry_user_id)
        if tombstone_status == "yes":
            deletion_ok = True
            for item in outputs:
                path = item.get("file_path")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        deletion_ok = False
            existing = UPLOAD_RETRY.get(key)
            if existing and deletion_ok:
                from bot.func.queue_manager import queue_manager

                reserved_existing = int(existing.get("reserved_bytes", 0) or 0)
                if reserved_existing:
                    await queue_manager.release_disk(reserved_existing)
                UPLOAD_RETRY.pop(key, None)
            if deletion_ok:
                UPLOAD_MANIFEST_ONLY.pop(key, None)
            else:
                if not existing:
                    retained = dict(entry)
                    retained["reserved_bytes"] = 0
                    UPLOAD_MANIFEST_ONLY[key] = retained
                log.error("Could not remove tombstoned recovery files for %s", key)
            continue
        if tombstone_status == "unknown":
            existing_unknown = UPLOAD_RETRY.get(key)
            if existing_unknown:
                reserved_existing = int(existing_unknown.get("reserved_bytes", 0) or 0)
                if reserved_existing:
                    from bot.func.queue_manager import queue_manager

                    await queue_manager.release_disk(reserved_existing)
                UPLOAD_RETRY.pop(key, None)
            current_size = sum(
                _context_output_size(context)
                for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
            )
            if current_size + _context_output_size(entry) > MAX_RECOVERY_DISK_BYTES:
                deletion_ok = True
                for item in outputs:
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                if deletion_ok:
                    log.error("Removed recovery output %s at physical disk budget", key)
                else:
                    manifest_entry = dict(entry)
                    manifest_entry["reserved_bytes"] = 0
                    UPLOAD_MANIFEST_ONLY[key] = manifest_entry
                    log.error("Recovery file deletion failed for %s; retained manifest-only", key)
                continue
            manifest_entry = dict(entry)
            manifest_entry["reserved_bytes"] = 0
            UPLOAD_MANIFEST_ONLY[key] = manifest_entry
            log.warning("Retained recovery entry %s while tombstone lookup is unavailable", key)
            continue
        existing_user_retries = sum(
            int(context.get("user_id", 0) or 0) == entry_user_id
            for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
        )
        try:
            entry_reserved = int(entry.get("reserved_bytes", 0) or 0)
        except (TypeError, ValueError):
            continue
        current_recovery_reserved = sum(
            int(context.get("reserved_bytes", 0) or 0)
            for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
        )
        if key in UPLOAD_RETRY:
            current_recovery_reserved -= int(
                UPLOAD_RETRY[key].get("reserved_bytes", 0) or 0
            )
        if key in UPLOAD_INFLIGHT:
            current_recovery_reserved -= int(
                UPLOAD_INFLIGHT[key].get("reserved_bytes", 0) or 0
            )
        current_recovery_reserved = max(0, current_recovery_reserved)
        entry_size = _context_output_size(entry)
        recovery_size = sum(
            _context_output_size(context)
            for context in {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}.values()
        )
        if key in UPLOAD_RETRY:
            recovery_size -= _context_output_size(UPLOAD_RETRY[key])
        if key in UPLOAD_INFLIGHT:
            recovery_size -= _context_output_size(UPLOAD_INFLIGHT[key])
        if recovery_size + entry_size > MAX_RECOVERY_DISK_BYTES:
            existing_overcap = UPLOAD_RETRY.get(key)
            deletion_ok = True
            for item in outputs:
                path = item.get("file_path")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        deletion_ok = False
            if deletion_ok:
                if existing_overcap:
                    reserved_existing = int(existing_overcap.get("reserved_bytes", 0) or 0)
                    if reserved_existing:
                        from bot.func.queue_manager import queue_manager

                        await queue_manager.release_disk(reserved_existing)
                    UPLOAD_RETRY.pop(key, None)
                log.error("Removed recovery output %s at physical disk budget", key)
            else:
                if existing_overcap:
                    manifest_entry = dict(existing_overcap)
                    manifest_entry["reserved_bytes"] = 0
                    UPLOAD_MANIFEST_ONLY[key] = manifest_entry
                else:
                    manifest_entry = dict(entry)
                    manifest_entry["reserved_bytes"] = 0
                    UPLOAD_MANIFEST_ONLY[key] = manifest_entry
                log.error("Recovery file deletion failed for %s; retained manifest-only", key)
            continue
        if key not in UPLOAD_RETRY and (
            existing_user_retries >= UPLOAD_RETRY_PER_USER
            or current_recovery_reserved + entry_reserved > MAX_RECOVERY_DISK_BYTES
        ):
            manifest_entry = dict(entry)
            manifest_entry["reserved_bytes"] = 0
            UPLOAD_MANIFEST_ONLY[key] = manifest_entry
            log.warning(
                "Retained recovery entry %s without notification at a recovery budget cap",
                key,
            )
            continue
        if key in UPLOAD_RETRY:
            existing = UPLOAD_RETRY[key]
            from database import tombstone_state

            existing_state = await tombstone_state(
                int(existing.get("user_id", 0) or 0)
            )
            if existing_state == "yes":
                deletion_ok = True
                for item in existing.get("output_files", []):
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                if deletion_ok:
                    reserved_existing = int(existing.get("reserved_bytes", 0) or 0)
                    if reserved_existing:
                        await queue_manager.release_disk(reserved_existing)
                    UPLOAD_RETRY.pop(key, None)
                else:
                    log.error("Could not remove existing tombstoned recovery files: %s", key)
                continue
            if existing_state == "unknown":
                existing_reserved = int(existing.get("reserved_bytes", 0) or 0)
                if existing_reserved:
                    from bot.func.queue_manager import queue_manager

                    await queue_manager.release_disk(existing_reserved)
                existing["reserved_bytes"] = 0
                existing["_tombstone_unknown"] = True
                continue
            reserved = int(existing.get("reserved_bytes", 0) or 0)
            if reserved and not existing.get("_reservation_acquired"):
                try:
                    from bot.func.queue_manager import queue_manager

                    if await queue_manager.reserve_disk(reserved):
                        existing["_reservation_acquired"] = True
                    else:
                        existing["reserved_bytes"] = 0
                except Exception:
                    existing["reserved_bytes"] = 0
            continue
        recovered_user_id = entry_user_id
        try:
            recovered_size = int(entry.get("original_size", 0) or 0)
        except (TypeError, ValueError):
            recovered_size = 0
        context = {
            "client": client,
            "user_id": recovered_user_id,
            "file_path": outputs[0]["file_path"],
            "progress_msg": None,
            "stats": EncodingStats(),
            "original_size": recovered_size,
            "codec": entry.get("codec", "Unknown"),
            "crf": entry.get("crf", "N/A"),
            "preset": entry.get("preset", "N/A"),
            "resolution": entry.get("resolution", "N/A"),
            "thumb": entry.get("thumb"),
            "job_id": entry.get("job_id", key),
            "output_files": outputs,
            "display_name": entry.get("display_name"),
            "delivery_settings": entry.get("delivery_settings"),
            "created_at": created_at,
            "reserved_bytes": int(entry.get("reserved_bytes", 0) or 0),
        }
        UPLOAD_RETRY[key] = context
        reserved = int(context.get("reserved_bytes", 0) or 0)
        if reserved:
            try:
                from bot.func.queue_manager import queue_manager

                if not await queue_manager.reserve_disk(reserved):
                    # Never carry a reservation that was not acquired; a later
                    # expiry must not debit another job's disk budget.
                    context["reserved_bytes"] = 0
                    log.warning("Could not reserve disk for recovered upload %s", key)
                else:
                    context["_reservation_acquired"] = True
            except Exception as exc:
                log.warning("Could not restore upload reservation %s: %s", key, exc)
        try:
            await client.send_message(
                context["user_id"],
                "♻️ <b>Upload recovery</b>\n"
                "<blockquote>An encoded output survived the bot restart. "
                "Retry delivery when ready.</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "🔁 Retry delivery",
                            callback_data=f"cb_retry_upload_{key}",
                        )
                    ]]
                ),
            )
            restored += 1
        except Exception as exc:
            log.warning(f"Could not notify user about upload recovery: {exc}")
    if not _persist_upload_retries():
        log.warning("Restored upload manifest could not be persisted")
    return restored


def _remember_error(key: str, detail: str, user_id: int | None = None):
    if len(ERROR_DETAILS) >= ERROR_DETAILS_CAP:
        oldest = next(iter(ERROR_DETAILS))
        ERROR_DETAILS.pop(oldest, None)
        ERROR_DETAILS_OWNER.pop(oldest, None)
    safe_detail = re.sub(r"(?:/[^ \n]+|[A-Za-z]:\\[^\s]+)", "<path>", detail)
    ERROR_DETAILS[key] = safe_detail[:1500]
    if user_id is not None:
        ERROR_DETAILS_OWNER[key] = int(user_id)


@dataclass
class EncodingStats:
    percent: float = 0.0
    fps: float = 0.0
    bitrate: str = "N/A"
    speed: str = "N/A"
    frame: int = 0
    total_frames: int = 0
    eta: str = "N/A"
    elapsed: str = "0s"
    size: str = "0 B"
    estimated_size: str = "0 B"
    compression: str = "1.0x"


class FFmpegProcess:
    def __init__(
        self,
        cmd: str,
        input_file: str,
        output_file: str,
        total_duration: float,
        original_size: int,
        file_name: str = "Unknown",
        codec: str = "Unknown",
        crf: str = "N/A",
        preset: str = "N/A",
        resolution: str = "N/A",
        current_step: int = 1,
        total_steps: int = 1,
        thumbnail_path: Optional[str] = None,
        keep_output: bool = False,
        output_files: Optional[List[Dict[str, str]]] = None,
        commands: Optional[List[Dict[str, str]]] = None,
        delivery_settings: Optional[Dict[str, Any]] = None,
        chat_id: int = 0,
        message_id: int = 0,
        source_id: str = "",
    ):
        self.cmd = cmd
        self.input_file = input_file
        self.output_file = output_file
        self.total_duration = total_duration
        self.original_size = original_size
        self.file_name = file_name
        self.codec = codec
        self.crf = crf
        self.preset = preset
        self.resolution = resolution
        self.current_step = current_step
        self.total_steps = total_steps
        self.thumbnail_path = thumbnail_path
        self.keep_output = keep_output
        self.output_files = output_files or []
        self.commands = commands or []
        self.delivery_settings = copy.deepcopy(delivery_settings)
        self.duration_limit = 0.0
        self.source_duration = 0.0
        self.process: Optional[asyncio.subprocess.Process] = None
        self.start_time = 0
        self.paused_at = 0.0
        self.is_paused = False
        self.is_cancelled = False
        self.yield_queue = False
        self.stats = EncodingStats()
        self.job_id = ""
        self.chat_id = chat_id
        self.message_id = message_id
        self.source_id = source_id
        self.message: Optional[Message] = None
        self.client: Optional[Client] = None
        self.user_id: int = 0
        self.is_viewing_queue = False
        self.stderr_tail = b""
        self._stderr_task: Optional[asyncio.Task] = None

    async def start(self):
        if self.is_cancelled:
            return False
        self.start_time = time.time()

        args = shlex.split(self.cmd)

        # Legacy command strings without an input: prepend ffmpeg + -i.
        if "-i" not in args:
            args = [FFMPEG_BIN, "-i", self.input_file] + args

        # Keep stderr quiet and route progress to stdout so the OS pipe can
        # never fill up and deadlock the encode.
        if "-nostats" not in args:
            args.insert(1, "-nostats")
        if "-loglevel" not in args:
            args[1:1] = ["-loglevel", "error"]

        if "-progress" not in args:
            args.extend(["-progress", "pipe:1"])

        executable = args[0] if args else FFMPEG_BIN
        cmd_args = args[1:] if len(args) > 1 else []

        final_args = cmd_args + [self.output_file, "-y"]

        log.info(f"Starting FFmpeg: {executable} {' '.join(final_args)}")

        creation = asyncio.ensure_future(
            asyncio.create_subprocess_exec(
                executable,
                *final_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
            )
        )
        try:
            self.process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            try:
                process = await creation
                process.kill()
                await process.wait()
            except Exception:
                pass
            raise

        if self.is_cancelled:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                await self.process.wait()
            return False

        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return True

    async def _drain_stderr(self):
        """Continuously drain stderr into a bounded tail buffer."""
        try:
            while True:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    break
                self.stderr_tail = (self.stderr_tail + chunk)[-8192:]
        except Exception:
            pass

    async def _stop_stderr_task(self):
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None

    async def pause(self):
        if self.process and not self.is_paused:
            try:
                parent = psutil.Process(self.process.pid)
                child_errors = False
                for child in parent.children(recursive=True):
                    try:
                        child.suspend()
                    except Exception:
                        child_errors = True
                parent.suspend()
                if child_errors:
                    parent.resume()
                    raise RuntimeError("one or more FFmpeg children could not be paused")
                self.is_paused = True
                self.yield_queue = True
                log.info(f"Process {self.process.pid} and children suspended.")
                return True
            except Exception as e:
                log.error(f"Failed to pause process: {e}")
                return False
        return False

    async def resume(self):
        if self.process and self.is_paused:
            try:
                parent = psutil.Process(self.process.pid)
                parent.resume()
                child_errors = False
                for child in parent.children(recursive=True):
                    try:
                        child.resume()
                    except Exception:
                        child_errors = True
                if child_errors:
                    raise RuntimeError("one or more FFmpeg children could not be resumed")
                self.is_paused = False
                self.yield_queue = False
                self.start_time = time.time()
                self.paused_at = 0.0
                log.info(f"Process {self.process.pid} and children resumed.")
                return True
            except Exception as e:
                log.error(f"Failed to resume process: {e}")
                return False
        return False

    async def cancel(self):
        self.is_cancelled = True
        if self.process:
            try:
                # A SIGSTOP'd process never delivers SIGTERM; resume first.
                if self.is_paused:
                    await self.resume()
                self.process.terminate()
                try:
                    parent = psutil.Process(self.process.pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception as e:
                log.error(f"Failed to terminate process: {e}")
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                await self.process.wait()
            finally:
                await self._stop_stderr_task()

    def parse_progress(self, line: str):
        try:
            parts = line.split("=")
            if len(parts) != 2:
                return
            key, value = parts[0].strip(), parts[1].strip()

            if key == "frame":
                self.stats.frame = int(value)
            elif key == "fps":
                self.stats.fps = float(value)
            elif key == "bitrate":
                self.stats.bitrate = value
            elif key == "speed":
                self.stats.speed = value
            elif key == "out_time_us":
                us = int(value)
                current_seconds = us / 1000000
                if self.total_duration > 0:
                    self.stats.percent = min(
                        100.0, (current_seconds / self.total_duration) * 100
                    )

                elapsed = time.time() - self.start_time
                if self.stats.percent > 0:
                    total_estimated = elapsed / (self.stats.percent / 100)
                    eta_seconds = total_estimated - elapsed
                    self.stats.eta = TimeFormatter(eta_seconds * 1000)

                self.stats.elapsed = TimeFormatter(elapsed * 1000)
        except Exception:
            pass

    def get_progress_ui(self) -> str:
        bar = progress_bar(self.stats.percent, width=20)

        current_size = 0
        try:
            current_size = os.path.getsize(self.output_file)
        except OSError:
            pass

        comp_text = ""
        if self.stats.percent > 0 and current_size > 0:
            est_size = current_size / (self.stats.percent / 100)
            if est_size > 0 and self.original_size > 0:
                comp = self.original_size / est_size
                if comp >= 1:
                    comp_text = f" · 🗜 {comp:.1f}× smaller"
                else:
                    comp_text = f" · 🗜 {1 / comp:.1f}× larger"

        if len(self.output_files) > 1:
            step_info = f" · Variant {self.current_step}/{len(self.output_files)}"
        else:
            step_info = (
                f" · Step {self.current_step}/{self.total_steps}"
                if self.total_steps > 1
                else ""
            )

        if self.is_paused:
            status_icon, status_text = "⏸", "Paused"
        else:
            status_icon, status_text = "🎬", "Encoding"

        return (
            f"{status_icon} <b>{status_text}</b>{step_info}\n"
            f"📁 <code>{escape(self.file_name)}</code>\n"
            f"<blockquote><code>{bar}</code> <b>{self.stats.percent:.1f}%</b>\n"
            f"📦 {humanbytes(current_size)} / {humanbytes(self.original_size)}{comp_text}\n"
            f"⏳ ETA <b>{self.stats.eta}</b> · ⏱ {self.stats.elapsed}\n"
            f"⚡ {self.stats.speed} · 🎞 {self.stats.fps:.1f} fps · 📊 {self.stats.bitrate}</blockquote>\n"
            f"<blockquote>⚙️ <code>{escape(str(self.codec))}</code> · CRF <code>{escape(str(self.crf))}</code> · "
            f"{escape(str(self.preset))} · {escape(str(self.resolution))}\n"
            f"🆔 <code>{self.job_id}</code></blockquote>"
        )


async def probe_file(path: str) -> Dict[str, Any]:
    """
    Probe a media file with ffprobe. Raises RuntimeError on failure so jobs
    never run against fabricated durations.
    """
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffprobe timed out after 60 seconds") from exc
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed (rc={proc.returncode}): {err.decode()[:300]}"
        )

    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe returned invalid JSON: {e}")

    info: Dict[str, Any] = {"duration": 0.0, "height": 0, "audio_codec": None}
    fmt = data.get("format", {})
    if "duration" in fmt:
        info["duration"] = float(fmt["duration"])
    for stream in data.get("streams", []):
        if info["duration"] == 0 and "duration" in stream:
            info["duration"] = float(stream["duration"])
        if stream.get("codec_type") == "video" and stream.get("height"):
            info["height"] = int(stream["height"])
        if stream.get("codec_type") == "audio" and not info["audio_codec"]:
            info["audio_codec"] = stream.get("codec_name")

    if info["duration"] <= 0:
        raise RuntimeError("Could not determine media duration")

    return info


async def _monitor_process(process: FFmpegProcess) -> str:
    """
    Monitors the FFmpeg process.
    Returns: 'FINISHED', 'FAILED', 'CANCELLED', or 'YIELDED'
    """
    last_update = 0.0
    timed_out = False

    while True:
        if time.time() - process.start_time > FFMPEG_WALL_TIMEOUT:
            timed_out = True
            log.error(f"FFmpeg wall-time limit reached for {process.job_id}")
            await process.cancel()
            break
        if process.yield_queue:
            process.paused_at = time.time()
            return "YIELDED"

        if process.process.returncode is not None:
            break

        try:
            try:
                line = await asyncio.wait_for(
                    process.process.stdout.readline(), timeout=1.0
                )
                if not line:
                    break
                process.parse_progress(line.decode(errors="replace").strip())
            except asyncio.TimeoutError:
                pass
        except Exception:
            break

        now = time.time()
        if now - last_update >= UI_UPDATE_INTERVAL:
            last_update = now
            try:
                if not process.is_viewing_queue:
                    pause_text = "▶️ Resume" if process.is_paused else "⏸ Pause"
                    buttons = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    pause_text,
                                    callback_data=f"enc_pause_{process.job_id}",
                                ),
                                InlineKeyboardButton(
                                    "❌ Cancel",
                                    callback_data=f"enc_cancel_{process.job_id}",
                                ),
                            ],
                            [
                                InlineKeyboardButton(
                                    "📋 Queue",
                                    callback_data=f"enc_queue_{process.job_id}",
                                ),
                            ],
                        ]
                    )
                    await process.message.edit(
                        process.get_progress_ui(), reply_markup=buttons
                    )
            except FloodWait as e:
                log.warning(f"FloodWait {e.value}s on UI update, backing off")
                await asyncio.sleep(e.value)
            except Exception as e:
                log.error(f"Failed to update UI: {e}")

    await process.process.wait()
    await process._stop_stderr_task()

    if timed_out:
        return "FAILED"
    if process.is_cancelled:
        return "CANCELLED"

    if process.process.returncode == 0:
        return "FINISHED"
    return "FAILED"


def progress_buttons(process: FFmpegProcess) -> InlineKeyboardMarkup:
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


async def _handle_job_completion(
    process: FFmpegProcess, status: str, cleanup_input: bool = True
) -> str:
    if status == "YIELDED":
        try:
            try:
                await process.message.delete()
            except Exception:
                pass

            user_link = f"<a href='tg://user?id={process.user_id}'>User</a>"
            step_label = (
                f"Variant {process.current_step}/{len(process.output_files)}"
                if len(process.output_files) > 1
                else f"Step {process.current_step}/{process.total_steps}"
            )
            pause_msg = await process.client.send_message(
                process.user_id,
                f"⏸ <b>Job Paused</b>\n"
                f"<blockquote>🆔 <code>{process.job_id}</code>\n"
                f"📁 <code>{escape(process.file_name)}</code>\n"
                f"⚙️ {escape(str(process.codec))} · {escape(str(process.resolution))} · CRF {escape(str(process.crf))}\n"
                f"🎯 {step_label}</blockquote>\n"
                f"{user_link}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "▶️ Resume",
                                callback_data=f"enc_pause_{process.job_id}",
                            ),
                            InlineKeyboardButton(
                                "❌ Cancel",
                                callback_data=f"enc_cancel_{process.job_id}",
                            ),
                        ]
                    ]
                ),
            )
            process.message = pause_msg
        except Exception as e:
            log.error(f"Failed to update UI on pause: {e}")
        return "YIELDED"

    if status == "CANCELLED":
        try:
            await process.message.edit("🚫 <b>Encoding Cancelled</b>")
        except Exception:
            pass
        cleanup_ok = _cleanup_files(process, cleanup_input=True)
        if not cleanup_ok:
            _track_failed_cleanup(
                f"cancelled_{process.job_id}",
                process.user_id,
                process.output_files or [{"file_path": process.output_file}],
            )
        active_encodings.pop(process.job_id, None)
        return "CANCELLED"

    if status == "FINISHED":
        is_final_step = process.current_step >= process.total_steps

        if not is_final_step:
            # Single-output jobs discard intermediates. Multi-resolution jobs
            # retain each completed variant until the whole delivery succeeds.
            if not process.keep_output:
                try:
                    if os.path.exists(process.output_file):
                        os.remove(process.output_file)
                except Exception:
                    pass
        else:
            outputs = process.output_files or [
                {
                    "file_path": process.output_file,
                    "resolution": process.resolution,
                }
            ]
            oversized = [
                item["file_path"]
                for item in outputs
                if os.path.isfile(item["file_path"])
                and os.path.getsize(item["file_path"]) > MAX_OUTPUT_SIZE
            ]
            if oversized:
                error = f"encoded output exceeds {MAX_OUTPUT_SIZE} bytes"
                _remember_error(process.job_id, error, process.user_id)
                deletion_ok = True
                for item in outputs:
                    try:
                        if os.path.isfile(item["file_path"]):
                            os.remove(item["file_path"])
                    except OSError:
                        deletion_ok = False
                if not deletion_ok:
                    _track_failed_cleanup(
                        f"oversized_{process.job_id}",
                        process.user_id,
                        outputs,
                    )
                try:
                    await process.message.edit(
                        "⚠️ <b>Encoded output is too large</b>\n"
                        "<i>The output exceeded the server limit and was removed.</i>"
                    )
                except Exception:
                    pass
                try:
                    if os.path.exists(process.input_file):
                        os.remove(process.input_file)
                except OSError:
                    pass
                active_encodings.pop(process.job_id, None)
                return "FAILED"

            reservation = queue_manager.get_job(process.job_id)
            reserved_bytes = int(getattr(reservation, "reserved_bytes", 0) or 0)

            async def upload_worker(**_kwargs):
                await _upload_video(
                    process.client,
                    process.user_id,
                    process.output_file,
                    None,
                    process.stats,
                    process.original_size,
                    codec=process.codec,
                    crf=process.crf,
                    preset=process.preset,
                    resolution=process.resolution,
                    thumb=process.thumbnail_path,
                    job_id=process.job_id,
                    output_files=outputs,
                    display_name=process.file_name,
                    delivery_settings=process.delivery_settings,
                    reserved_bytes=reserved_bytes,
                )

            await upload_manager.add_upload_job(
                process.user_id,
                upload_worker,
                output_files=outputs,
                reserved_bytes=reserved_bytes,
                source_job_id=process.job_id,
                codec=process.codec,
                crf=process.crf,
                preset=process.preset,
                resolution=process.resolution,
                thumb=process.thumbnail_path,
                display_name=process.file_name,
                delivery_settings=process.delivery_settings,
            )

            try:
                await process.message.delete()
            except Exception:
                pass

        if cleanup_input:
            try:
                if os.path.exists(process.input_file):
                    os.remove(process.input_file)
            except Exception:
                pass

        active_encodings.pop(process.job_id, None)
        return "FINISHED"

    if status == "FAILED":
        stderr_text = process.stderr_tail.decode(errors="replace") if process.stderr_tail else "Unknown error"
        log.error(f"FFmpeg failed for job {process.job_id}: {stderr_text[:800]}")
        _remember_error(process.job_id, f"exit={process.process.returncode}\n{stderr_text}", process.user_id)
        try:
            await process.message.edit(
                f"❌ <b>Encoding failed</b>\n"
                f"<blockquote>📁 <code>{escape(process.file_name)}</code> · "
                f"Step {process.current_step}/{process.total_steps}</blockquote>\n"
                f"<i>Nothing was lost — send the file again to retry, "
                f"or open Details for the technical reason.</i>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{process.job_id}"
                            ),
                            InlineKeyboardButton("⚙️ Settings", callback_data="cb_open_settings"),
                        ],
                        [
                            InlineKeyboardButton("🗑 Dismiss", callback_data="cb_close"),
                        ],
                    ]
                ),
            )
        except Exception as e:
            log.error(f"Failed to edit failure message: {e}")
        cleanup_ok = _cleanup_files(process, cleanup_input=cleanup_input)
        if not cleanup_ok:
            _track_failed_cleanup(
                f"failed_{process.job_id}",
                process.user_id,
                process.output_files or [{"file_path": process.output_file}],
            )
        active_encodings.pop(process.job_id, None)
        return "FAILED"


def _cleanup_files(process: FFmpegProcess, cleanup_input: bool = True) -> bool:
    download_root = os.path.realpath(DOWNLOAD_DIR)
    def safe_path(path: str) -> bool:
        try:
            return os.path.commonpath(
                (os.path.realpath(path), download_root)
            ) == download_root
        except (OSError, ValueError):
            return False

    paths = set()
    if cleanup_input and process.input_file:
        paths.add(process.input_file)
    if process.output_file:
        paths.add(process.output_file)
    paths.update(
        item.get("file_path")
        for item in (process.output_files or [])
        if item.get("file_path")
    )
    success = True
    for path in paths:
        if not safe_path(path):
            log.warning("Refusing cleanup outside DOWNLOAD_DIR: %s", path)
            success = False
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            success = False
            log.error(f"Cleanup failed for {path}: {exc}")
    return success


async def _run_encoding_job(
    ffmpeg_cmd: str,
    input_file: str,
    output_file: str,
    client: Client,
    message: Message,
    job_id: str,
    user_id: int,
    cleanup_input: bool = True,
    codec: str = "Unknown",
    crf: str = "N/A",
    preset: str = "N/A",
    resolution: str = "N/A",
    current_step: int = 1,
    total_steps: int = 1,
    thumbnail_path: Optional[str] = None,
    duration_limit: float = 0.0,
    keep_output: bool = False,
    output_files: Optional[List[Dict[str, str]]] = None,
    display_name: Optional[str] = None,
    commands: Optional[List[Dict[str, str]]] = None,
    source_duration: float = 0.0,
    delivery_settings: Optional[Dict[str, Any]] = None,
    chat_id: int = 0,
    message_id: int = 0,
    source_id: str = "",
) -> str:
    """Runs one encoding step. Returns its terminal status string."""
    try:
        duration = (
            source_duration
            if source_duration > 0
            else (await probe_file(input_file))["duration"]
        )
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError("invalid source duration")
    except Exception as e:
        log.error(f"Probe failed for job {job_id}: {e}")
        _remember_error(job_id, f"probe: {e}", user_id)
        try:
            await message.edit(
                "❌ <b>Encoding failed</b>\n"
                "<blockquote>The file could not be read by ffmpeg. "
                "It may be corrupted or an unsupported format.</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            ),
                            InlineKeyboardButton(
                                "🗑 Dismiss", callback_data="cb_close"
                            ),
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        return "FAILED"

    # Progress math must reflect trim/sample limits, not the full source.
    if duration_limit and duration_limit > 0:
        duration = min(duration, duration_limit)
    elif duration > MAX_MEDIA_DURATION:
        error = (
            f"Videos longer than {MAX_MEDIA_DURATION // 3600}h are not accepted "
            "for unattended encoding. Trim the source or use a sample encode."
        )
        _remember_error(job_id, error, user_id)
        try:
            await message.edit(
                "⚠️ <b>Video is too long</b>\n"
                f"<blockquote>{error}</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑 Dismiss", callback_data="cb_close")]]
                ),
            )
        except Exception:
            pass
        return "FAILED"

    try:
        original_size = os.path.getsize(input_file)
    except OSError as e:
        log.error(f"Input vanished for job {job_id}: {e}")
        _remember_error(job_id, f"input missing: {e}", user_id)
        try:
            await message.edit("❌ <b>Encoding failed</b>\n<blockquote>Input file went missing.</blockquote>")
        except Exception:
            pass
        return "FAILED"

    if original_size > MAX_FILE_SIZE:
        error = f"Input exceeds the {MAX_FILE_SIZE}-byte limit"
        _remember_error(job_id, error, user_id)
        try:
            await message.edit(
                "⚠️ <b>Input is too large</b>\n"
                "<blockquote>The downloaded source exceeds the configured limit.</blockquote>"
            )
        except Exception:
            pass
        return "FAILED"

    job = queue_manager.get_job(job_id)
    if job is not None and getattr(job, "cancel_requested", False):
        return "CANCELLED"

    process = FFmpegProcess(
        ffmpeg_cmd,
        input_file,
        output_file,
        duration,
        original_size,
        display_name or Path(input_file).name,
        codec=codec,
        crf=crf,
        preset=preset,
        resolution=resolution,
        current_step=current_step,
        total_steps=total_steps,
        thumbnail_path=thumbnail_path,
        keep_output=keep_output,
        output_files=output_files,
        commands=commands,
        delivery_settings=delivery_settings,
        chat_id=chat_id,
        message_id=message_id,
        source_id=source_id,
    )
    process.job_id = job_id
    process.duration_limit = duration_limit
    process.source_duration = duration
    process.message = message
    process.client = client
    process.user_id = user_id

    active_encodings[job_id] = process

    try:
        started = await process.start()
        if not started:
            _cleanup_files(process, cleanup_input=cleanup_input)
            active_encodings.pop(job_id, None)
            return "CANCELLED"
        status = await _monitor_process(process)
        await _handle_job_completion(process, status, cleanup_input=cleanup_input)
        return status
    except Exception as e:
        log.error(f"Encoding job failed: {e}", exc_info=True)
        _remember_error(job_id, str(e), user_id)
        try:
            await message.edit(
                "❌ <b>Encoding failed</b>\n"
                "<blockquote>The encoder stopped unexpectedly. Your source file "
                "was not delivered; open Details for the technical reason.</blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            ),
                            InlineKeyboardButton(
                                "🗑 Dismiss", callback_data="cb_close"
                            ),
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        _cleanup_files(process, cleanup_input=cleanup_input)
        active_encodings.pop(job_id, None)
        return "FAILED"


async def resume_encoding_job(job_id: str) -> str:
    """Resumes a yielded job, then drives any remaining quality steps."""
    if job_id not in active_encodings:
        log.warning(f"Attempted to resume non-existent or finished job: {job_id}")
        return "FAILED"

    process = active_encodings[job_id]
    if not await process.resume():
        _remember_error(job_id, "could not resume FFmpeg process", process.user_id)
        await process.cancel()
        _cleanup_files(process, cleanup_input=True)
        active_encodings.pop(job_id, None)
        return "FAILED"

    try:
        await process.message.delete()
    except Exception:
        pass

    process.message = await process.client.send_message(
        process.user_id, "🔄 <b>Resuming Encoding...</b>"
    )

    try:
        status = await _monitor_process(process)
        cleanup_input = process.current_step >= process.total_steps
        await _handle_job_completion(process, status, cleanup_input=cleanup_input)
        if status in ("FAILED", "CANCELLED"):
            for output in process.output_files or [
                {"file_path": item["output_file"]} for item in process.commands
            ]:
                try:
                    if os.path.exists(output["file_path"]):
                        os.remove(output["file_path"])
                except OSError:
                    pass
            try:
                if os.path.exists(process.input_file):
                    os.remove(process.input_file)
            except OSError:
                pass
            return status

        # Continue the exact command plan captured when the job first started.
        # Re-reading mutable settings here could otherwise skip or duplicate a
        # resolution after a user changes preferences while paused.
        commands = process.commands
        if not commands:
            settings = normalize_settings(await get_user_settings(process.user_id))
            output_base = str(
                Path(process.input_file).parent
                / f"encoded_{Path(process.input_file).stem}"
            )
            commands = generate_ffmpeg_cmd(
                settings,
                process.input_file,
                output_base,
                process.thumbnail_path,
            )

        total = len(commands)
        next_index = process.current_step  # 0-based index of next command
        while status == "FINISHED" and next_index < total:
            cmd_info = commands[next_index]
            is_last = next_index == total - 1
            status = await _run_encoding_job(
                cmd_info["cmd"],
                process.input_file,
                cmd_info["output_file"],
                process.client,
                process.message,
                job_id,
                process.user_id,
                cleanup_input=is_last,
                codec=process.codec,
                crf=str(process.crf),
                preset=process.preset,
                resolution=cmd_info.get("suffix", "1080p"),
                current_step=next_index + 1,
                total_steps=total,
                thumbnail_path=process.thumbnail_path,
                duration_limit=process.duration_limit,
                keep_output=process.keep_output,
                output_files=process.output_files,
                display_name=process.file_name,
                commands=commands,
                source_duration=process.source_duration,
                delivery_settings=process.delivery_settings,
                chat_id=process.chat_id,
                message_id=process.message_id,
                source_id=process.source_id,
            )
            next_index += 1
            if status in ("FAILED", "CANCELLED"):
                for output in process.output_files or [
                    {"file_path": item["output_file"]} for item in commands
                ]:
                    try:
                        if os.path.exists(output["file_path"]):
                            os.remove(output["file_path"])
                    except OSError:
                        pass
        return status
    except Exception as e:
        log.error(f"Resumed job failed: {e}", exc_info=True)
        _remember_error(job_id, str(e), process.user_id)
        try:
            await process.message.edit(
                "❌ <b>Resumed job failed</b>",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔍 Details", callback_data=f"cb_err_{job_id}"
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        _cleanup_files(process, cleanup_input=True)
        active_encodings.pop(job_id, None)
        return "FAILED"


def _duration_limit(video_settings: dict) -> float:
    trim_end = float(video_settings.get("trim_end", 0) or 0)
    trim_start = float(video_settings.get("trim_start", 0) or 0)
    sample = int(video_settings.get("sample_seconds", 0) or 0)
    limit = 0.0
    if trim_end > trim_start > 0:
        limit = trim_end - trim_start
    if sample > 0:
        limit = sample if not limit else min(limit, sample)
    return limit


async def safe_download_media(
    client: Client, message: Message, file_path: str, progress_msg: Message
):
    acquired = False
    completed = False
    try:
        await download_manager.acquire()
        acquired = True
        clear_cancel(progress_msg)
        downloaded_path = await client.download_media(
            message,
            file_name=file_path,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading...", progress_msg, time.time()),
        )
        clear_cancel(progress_msg)

        if not downloaded_path or not os.path.exists(downloaded_path):
            log.error(f"Download reported success but file not found: {downloaded_path}")
            return None

        completed = True
        return downloaded_path
    except Exception as e:
        if "Transfer cancelled" in str(e):
            log.info("Download cancelled by user")
        else:
            log.error(f"Download failed: {e}")
        return None
    finally:
        clear_cancel(progress_msg)
        if not completed:
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError:
                pass
        if acquired:
            download_manager.release()


def sanitize_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_", ".")).strip()
    return safe or f"video_{int(time.time())}.mp4"


def apply_rename_pattern(pattern: str, original_name: str, res: str, codec: str) -> str:
    try:
        name = pattern.format(
            original=Path(original_name).stem,
            res=res,
            codec=codec,
            date=time.strftime("%Y-%m-%d"),
        )
    except (KeyError, IndexError, ValueError):
        name = Path(original_name).stem
    safe = sanitize_filename(name)
    if len(safe) > 120:
        suffix = Path(safe).suffix
        safe = safe[: 120 - len(suffix)] + suffix
    return safe


def reconstruct_worker(job, client: Client):
    """Reconstructs the worker function for a restored job."""

    async def worker(job_id_arg):
        from database import is_user_tombstoned

        if await is_user_tombstoned(job.user_id):
            return "CANCELLED"
        downloaded_path = None
        output_files = []
        worker_succeeded = False
        try:
            log.info(
                f"Restoring job {job.job_id}: fetching message {job.message_id} "
                f"from chat {job.chat_id}"
            )
            message = await client.get_messages(job.chat_id, job.message_id)

            if (
                not message
                or job.chat_id != job.user_id
                or getattr(getattr(message, "chat", None), "id", None) != job.chat_id
                or (not message.video and not message.document)
            ):
                log.error(f"Failed to fetch message for job {job.job_id}")
                try:
                    await client.send_message(
                        job.user_id,
                        "⚠️ <b>Restored job could not find its original file.</b>\n"
                        "<i>The source message was deleted. Please send the file again.</i>",
                    )
                except Exception:
                    pass
                return "FAILED"

            source_size = int(
                getattr(message.video or message.document, "file_size", 0) or 0
            )
            if source_size > MAX_FILE_SIZE:
                await client.send_message(
                    job.user_id,
                    "⚠️ <b>Restored source exceeds the current file limit.</b>\n"
                    "<i>Please send a smaller file.</i>",
                )
                return "FAILED"

            downloads_dir = Path(DOWNLOAD_DIR)
            downloads_dir.mkdir(parents=True, exist_ok=True)

            file_name = job.file_name
            if not file_name or file_name == "Unknown":
                file_name = f"restored_{job.job_id}.mp4"

            download_file_path = (
                downloads_dir
                / f"{job.user_id}_{job.job_id}_{sanitize_filename(file_name)}"
            )

            status_msg = await client.send_message(
                job.user_id, f"🔄 <b>Restoring Job</b> <code>{job.job_id}</code>..."
            )

            downloaded_path = await safe_download_media(
                client, message, str(download_file_path), status_msg
            )

            if not downloaded_path:
                try:
                    await status_msg.edit("❌ <b>Restoration failed:</b> could not download the file.")
                except Exception:
                    pass
                return "FAILED"

            try:
                source_duration = float((await probe_file(downloaded_path))["duration"])
            except Exception:
                source_duration = 0.0
            settings = normalize_settings(await get_user_settings(job.user_id))

            from bot.func.ffmpeg_utils import (
                prepare_thumbnail,
                prepare_watermark_assets,
            )

            prepare_watermark_assets(job.user_id, settings)
            thumbnail_path = prepare_thumbnail(job.user_id, settings)

            settings["user_id"] = job.user_id

            output_base = str(
                Path(downloaded_path).parent
                / f"encoded_{Path(downloaded_path).stem}"
            )

            commands = generate_ffmpeg_cmd(
                settings, downloaded_path, output_base, thumbnail_path
            )

            video_settings = settings["video"]
            codec = video_settings["codec"]
            crf = video_settings["crf"]
            preset = video_settings["preset"]
            duration_limit = _duration_limit(video_settings)
            output_files = [
                {
                    "file_path": command["output_file"],
                    "resolution": command.get("suffix", "1080p"),
                }
                for command in commands
            ]
            keep_outputs = len(output_files) > 1

            final_status = "FAILED"
            for i, cmd_info in enumerate(commands):
                is_last = i == len(commands) - 1
                resolution = cmd_info.get("suffix", "1080p")

                status = await _run_encoding_job(
                    cmd_info["cmd"],
                    downloaded_path,
                    cmd_info["output_file"],
                    client,
                    status_msg,
                    job_id_arg,
                    job.user_id,
                    cleanup_input=is_last,
                    codec=codec,
                    crf=str(crf),
                    preset=preset,
                    resolution=resolution,
                    current_step=i + 1,
                    total_steps=len(commands),
                    thumbnail_path=thumbnail_path,
                    duration_limit=duration_limit,
                    keep_output=keep_outputs,
                    output_files=output_files,
                    display_name=file_name,
                    commands=commands,
                    source_duration=source_duration,
                    delivery_settings=settings,
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                    source_id=job.source_id,
                )
                if status != "FINISHED":
                    final_status = status
                    if status in ("FAILED", "CANCELLED"):
                        for output in output_files:
                            try:
                                if os.path.exists(output["file_path"]):
                                    os.remove(output["file_path"])
                            except OSError:
                                pass
                        try:
                            if os.path.exists(downloaded_path):
                                os.remove(downloaded_path)
                        except OSError:
                            pass
                    break
            else:
                final_status = "FINISHED"

            worker_succeeded = final_status == "FINISHED"
            return final_status

        except Exception as e:
            log.error(f"Error in restored worker for job {job.job_id}: {e}", exc_info=True)
            try:
                await client.send_message(
                    job.user_id,
                    "❌ <b>Restored job failed</b>\n"
                    "<i>The source could not be prepared. Please send the video again.</i>",
                )
            except Exception:
                pass
            return "FAILED"
        finally:
            if downloaded_path and os.path.isfile(downloaded_path):
                try:
                    os.remove(downloaded_path)
                except OSError:
                    pass
            if not worker_succeeded:
                for output in output_files:
                    path = output.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass

    return worker


async def generate_auto_thumbnail(output_file: str, user_id: int) -> Optional[str]:
    """Grabs a frame from the encoded video for use as an upload thumbnail."""
    thumb_path = None
    try:
        probe = await probe_file(output_file)
        at = max(1.0, probe["duration"] * 0.35)
        os.makedirs(THUMB_DIR, exist_ok=True)
        thumb_path = os.path.join(
            THUMB_DIR, f"auto_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
        )
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN,
            "-y",
            "-ss",
            str(at),
            "-i",
            output_file,
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-2",
            "-q:v",
            "4",
            thumb_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            if thumb_path and os.path.isfile(thumb_path):
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass
            raise
        if proc.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
        if thumb_path and os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
    except Exception as e:
        log.warning(f"Auto-thumbnail generation failed: {e}")
        if thumb_path and os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
    return None


def render_caption(
    file_name: str,
    codec: str,
    resolution: str,
    crf: str,
    preset: str,
    stats: EncodingStats,
    original_size: int,
    file_size: int,
    bot_username: str,
    sample: bool = False,
    variant_count: int = 1,
    variant_index: int = 1,
) -> str:
    comp = 1.0
    if file_size > 0 and original_size > 0:
        comp = original_size / file_size
    saved_pct = 0.0
    if original_size > 0 and file_size < original_size:
        saved_pct = (1 - file_size / original_size) * 100

    sample_note = "\n⚠️ <i>SAMPLE encode — first seconds only</i>\n" if sample else ""
    variant_note = (
        f"\n🎛 Variant <code>{variant_index}/{variant_count}</code>"
        if variant_count > 1
        else ""
    )

    return (
        f"✅ <b>Encode completed</b>{variant_note}\n\n"
        f"<blockquote>📁 <code>{escape(file_name)}</code>\n"
        f"⚙️ {escape(str(codec))} · {escape(str(resolution))} · CRF {escape(str(crf))} · {escape(str(preset))}</blockquote>\n\n"
        f"<blockquote>📊 <b>Stats</b>\n"
        f"📥 Original: <code>{humanbytes(original_size)}</code>\n"
        f"📤 Encoded: <code>{humanbytes(file_size)}</code>\n"
        f"🗜 Compression: <code>{comp:.2f}×</code>"
        + (f" · saved <code>{saved_pct:.1f}%</code>" if saved_pct > 0 else "")
        + f"\n⏱ Time: <code>{stats.elapsed}</code></blockquote>\n"
        f"{sample_note}"
        f"🤖 Encoded by @{bot_username}"
    )


def _completion_buttons(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⚙️ Settings", callback_data="cb_open_settings"
                ),
                InlineKeyboardButton(
                    "📋 Queue", url=f"https://t.me/{bot_username}?start=queue"
                ),
            ],
            [
                InlineKeyboardButton(
                    "✨ Features", callback_data="cb_features"
                ),
            ],
        ]
    )


async def _upload_video(
    client: Client,
    user_id: int,
    file_path: str,
    progress_msg: Optional[Message],
    stats: EncodingStats,
    original_size: int,
    codec: str = "Unknown",
    crf: str = "N/A",
    preset: str = "N/A",
    resolution: str = "N/A",
    thumb: Optional[str] = None,
    job_id: str = "",
    output_files: Optional[List[Dict[str, str]]] = None,
    display_name: Optional[str] = None,
    delivery_settings: Optional[Dict[str, Any]] = None,
    created_at: Optional[float] = None,
    reserved_bytes: int = 0,
):
    """Upload one or every enabled resolution, retaining only failed outputs."""
    upload_msg = None
    output_deleted = False
    auto_thumb_paths = []
    thumb_cleanup_paths = []
    shared_generated_thumb = None
    telegram_thumb = None
    remaining = list(
        output_files
        or [{"file_path": file_path, "resolution": resolution}]
    )
    remaining = [item for item in remaining if os.path.isfile(item["file_path"])]
    total_outputs = len(remaining)
    reservation_retained = False
    delivered_bytes = 0
    delivered_count = 0
    inflight_key = job_id or f"inflight_{user_id}_{int(time.time())}"
    UPLOAD_INFLIGHT[inflight_key] = {
        "user_id": user_id,
        "file_path": remaining[0]["file_path"] if remaining else file_path,
        "original_size": original_size,
        "codec": codec,
        "crf": crf,
        "preset": preset,
        "resolution": resolution,
        "thumb": thumb,
        "job_id": job_id or inflight_key,
        "output_files": [dict(item) for item in remaining],
        "display_name": display_name,
        "delivery_settings": delivery_settings,
        "created_at": created_at or time.time(),
        "reserved_bytes": int(reserved_bytes or 0),
        "_reservation_acquired": bool(reserved_bytes),
    }
    if not _persist_upload_retries():
        log.warning("Active upload recovery manifest could not be persisted")

    try:
        if not remaining:
            raise FileNotFoundError("No encoded output remains to upload")

        settings = normalize_settings(
            delivery_settings
            if delivery_settings is not None
            else await get_user_settings(user_id)
        )
        video_settings = settings["video"]
        as_video = bool(settings["output_as_video"])
        rename_pattern = settings["rename"]["pattern"]
        sample_mode = int(video_settings["sample_seconds"]) > 0
        me = await client.get_me()
        bot_username = me.username or "ArgonsEncoderBot"

        if total_outputs > 1 and rename_pattern and "{res}" not in rename_pattern:
            rename_pattern = f"{rename_pattern} [{{res}}]"

        if thumb and os.path.isfile(thumb):
            telegram_thumb = await prepare_telegram_thumbnail(
                thumb,
                destination=os.path.join(
                    THUMB_DIR, f"delivery_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
                ),
            )
            if telegram_thumb:
                thumb_cleanup_paths.append(telegram_thumb)

        delivery_label = (
            f"Delivering 1/{total_outputs}…" if total_outputs > 1 else "Uploading…"
        )
        upload_msg = await client.send_message(
            user_id,
            f"{ICONS.upload} <b>{delivery_label}</b>\n"
            "<i>Your encoded media will appear here.</i>",
        )
        clear_cancel(upload_msg)

        while remaining:
            item = remaining[0]
            current_path = item["file_path"]
            current_resolution = item.get("resolution", resolution)
            file_size = os.path.getsize(current_path)
            source_name = display_name or Path(current_path).name
            file_name = f"{Path(source_name).stem}{Path(current_path).suffix}"

            if not rename_pattern and total_outputs > 1:
                file_name = (
                    f"{Path(source_name).stem} [{current_resolution}]"
                    f"{Path(current_path).suffix}"
                )
            elif rename_pattern:
                renamed = apply_rename_pattern(
                    rename_pattern, source_name, current_resolution, str(codec)
                )
                if renamed:
                    ext = Path(source_name).suffix or (".mp4" if as_video else ".mkv")
                    file_name = f"{Path(renamed).stem}{ext}"

            caption = render_caption(
                file_name,
                codec,
                current_resolution,
                crf,
                preset,
                stats,
                original_size,
                file_size,
                bot_username,
                sample=sample_mode,
                variant_count=total_outputs,
                variant_index=delivered_count + 1,
            )
            current_thumb = telegram_thumb
            if not current_thumb:
                if shared_generated_thumb and os.path.isfile(shared_generated_thumb):
                    current_thumb = shared_generated_thumb
                else:
                    generated_thumb = await generate_auto_thumbnail(
                        current_path, user_id
                    )
                    if generated_thumb:
                        normalized_generated = await prepare_telegram_thumbnail(
                            generated_thumb,
                            destination=os.path.join(
                                THUMB_DIR,
                                f"auto_delivery_{user_id}_{uuid.uuid4().hex[:8]}.jpg",
                            ),
                        )
                        try:
                            os.remove(generated_thumb)
                        except OSError:
                            pass
                        if normalized_generated:
                            shared_generated_thumb = normalized_generated
                            thumb_cleanup_paths.append(normalized_generated)
                            current_thumb = normalized_generated

            send_kwargs = {
                "chat_id": user_id,
                "caption": caption,
                "thumb": current_thumb,
                "file_name": file_name,
                "reply_markup": _completion_buttons(bot_username),
                "protect_content": True,
            }
            sent_message = None
            fallback_reason = None
            for attempt in range(2):
                try:
                    if as_video:
                        try:
                            sent_message = await client.send_video(
                                supports_streaming=True,
                                video=current_path,
                                progress=progress_for_pyrogram,
                                progress_args=(
                                    f"{ICONS.upload} Uploading {current_resolution}…",
                                    upload_msg,
                                    time.time(),
                                ),
                                **send_kwargs,
                            )
                        except Exception as exc:
                            if isinstance(exc, FloodWait) or "Transfer cancelled" in str(exc):
                                raise
                            fallback_reason = "Streamable video delivery was unavailable; sent as a document."
                            sent_message = await client.send_document(
                                document=current_path,
                                progress=progress_for_pyrogram,
                                progress_args=(
                                    f"{ICONS.upload} Uploading {current_resolution}…",
                                    upload_msg,
                                    time.time(),
                                ),
                                **send_kwargs,
                            )
                    else:
                        sent_message = await client.send_document(
                            document=current_path,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                f"{ICONS.upload} Uploading {current_resolution}…",
                                upload_msg,
                                time.time(),
                            ),
                            **send_kwargs,
                        )
                    break
                except FloodWait as exc:
                    if attempt == 0:
                        log.warning(f"Upload FloodWait {exc.value}s, retrying")
                        await asyncio.sleep(exc.value)
                    else:
                        raise

            if sent_message is None:
                raise RuntimeError("Telegram did not return a sent-media message")
            if fallback_reason:
                try:
                    await sent_message.edit_caption(
                        caption=caption + f"\n\n<i>{escape(fallback_reason)}</i>"
                    )
                except Exception:
                    pass
            clear_cancel(upload_msg)
            if LOG_DELIVERIES and LOG_CHANNEL:
                try:
                    await sent_message.copy(LOG_CHANNEL)
                except Exception as log_error:
                    log.error(f"Failed to copy upload to log channel: {log_error}")

            delivered_bytes += file_size
            delivered_count += 1
            remaining.pop(0)
            try:
                os.remove(current_path)
            except OSError:
                pass
            if remaining:
                next_item = remaining[0]
                try:
                    await upload_msg.edit(
                        f"{ICONS.upload} <b>Delivered {delivered_count}/{total_outputs}</b>\n"
                        f"<i>Next: {escape(next_item.get('resolution', 'video'))}</i>"
                    )
                except Exception:
                    pass

        output_deleted = True
        try:
            await upload_msg.delete()
        except Exception:
            pass

        try:
            await inc_stats(
                {
                    "total_encodes": 1,
                    "in_bytes": int(original_size),
                    "out_bytes": int(delivered_bytes),
                },
                user_id=user_id,
            )
        except Exception as stats_error:
            log.error(f"Failed to update upload stats: {stats_error}")

    except asyncio.CancelledError:
        deletion_ok = True
        if upload_msg:
            clear_cancel(upload_msg)
        for item in remaining:
            path = item.get("file_path")
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    deletion_ok = False
        if remaining and not deletion_ok:
            _track_failed_cleanup(
                f"cancelled_{job_id or uuid.uuid4().hex[:8]}",
                user_id,
                remaining,
            )
            output_deleted = False
        else:
            output_deleted = True
        if upload_msg:
            try:
                await upload_msg.edit(
                    f"{ICONS.cancel_job} <b>Delivery cancelled.</b>\n"
                    + (
                        "<i>Remaining output was removed.</i>"
                        if output_deleted
                        else "<i>Output remains tracked for safe cleanup.</i>"
                    )
                )
            except Exception:
                pass
        return

    except Exception as exc:
        if upload_msg:
            clear_cancel(upload_msg)
        if "Transfer cancelled" in str(exc):
            log.info("Upload cancelled by user")
            deletion_ok = True
            for item in remaining:
                path = item.get("file_path")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        deletion_ok = False
            if remaining and not deletion_ok:
                _track_failed_cleanup(
                    f"cancelled_{job_id or uuid.uuid4().hex[:8]}",
                    user_id,
                    remaining,
                )
                output_deleted = False
            else:
                output_deleted = True
            if upload_msg:
                try:
                    await upload_msg.edit(
                        f"{ICONS.cancel_job} <b>Upload cancelled.</b>\n"
                        + (
                            "<i>The undelivered output was removed.</i>"
                            if output_deleted
                            else "<i>Output remains tracked for safe cleanup.</i>"
                        )
                    )
                except Exception:
                    pass
            return

        log.error(f"Upload failed: {exc}", exc_info=True)
        error_key = job_id or f"upload_{int(time.time())}"
        _remember_error(error_key, str(exc), user_id)
        if remaining:
            recovery_contexts = {**UPLOAD_INFLIGHT, **UPLOAD_RETRY, **UPLOAD_MANIFEST_ONLY}
            recovery_contexts.pop(inflight_key, None)
            user_retry_count = sum(
                int(context.get("user_id", 0) or 0) == int(user_id)
                for context in recovery_contexts.values()
            )
            recovery_reserved = sum(
                int(context.get("reserved_bytes", 0) or 0)
                for context in recovery_contexts.values()
            )
            recovery_size = sum(
                _context_output_size(context) for context in recovery_contexts.values()
            )
            pending_size = _context_output_size({"output_files": remaining})
            recovery_limit_hit = (
                user_retry_count >= UPLOAD_RETRY_PER_USER
                or recovery_size + pending_size > MAX_RECOVERY_DISK_BYTES
                or (
                    reserved_bytes > 0
                    and recovery_reserved + reserved_bytes > MAX_RECOVERY_DISK_BYTES
                )
            )
            if recovery_limit_hit:
                deletion_ok = True
                for item in remaining:
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                if not deletion_ok:
                    UPLOAD_MANIFEST_ONLY[error_key] = {
                        "user_id": user_id,
                        "file_path": remaining[0]["file_path"],
                        "output_files": [dict(item) for item in remaining],
                        "reserved_bytes": 0,
                        "created_at": created_at or time.time(),
                    }
                    _persist_upload_retries()
                    output_deleted = False
                else:
                    output_deleted = True
                if upload_msg:
                    try:
                        limit_message = (
                            "⚠️ <b>Too many pending deliveries</b>\n"
                            "<i>The undelivered output was removed to protect server storage. "
                            "Please try again later.</i>"
                            if deletion_ok
                            else "⚠️ <b>Recovery storage limit reached</b>\n"
                            "<i>The output is retained for retry cleanup.</i>"
                        )
                        await upload_msg.edit(limit_message)
                    except Exception:
                        pass
                upload_msg = None
            else:
                if len(UPLOAD_RETRY) >= UPLOAD_RETRY_CAP:
                    log.warning(
                        "Upload recovery soft cap reached (%d); retaining all contexts",
                        len(UPLOAD_RETRY),
                    )
                UPLOAD_RETRY[error_key] = {
                    "client": client,
                    "user_id": user_id,
                    "file_path": remaining[0]["file_path"],
                    "progress_msg": progress_msg,
                    "stats": stats,
                    "original_size": original_size,
                    "codec": codec,
                    "crf": crf,
                    "preset": preset,
                    "resolution": remaining[0].get("resolution", resolution),
                    "thumb": thumb,
                    "job_id": job_id,
                    "output_files": [dict(item) for item in remaining],
                    "display_name": display_name,
                    "delivery_settings": delivery_settings,
                    "created_at": created_at or time.time(),
                    "reserved_bytes": int(reserved_bytes or 0),
                    "_reservation_acquired": bool(reserved_bytes),
                }
                reservation_retained = True
                if not _persist_upload_retries():
                    log.warning("Failed-upload recovery manifest could not be persisted")
        if upload_msg:
            try:
                failed_count = len(remaining)
                await upload_msg.edit(
                    f"❌ <b>Upload paused</b>\n"
                    f"<blockquote>{failed_count} output(s) remain safely on disk.</blockquote>",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔍 Details",
                                    callback_data=f"cb_err_{error_key}",
                                ),
                                InlineKeyboardButton(
                                    "🔁 Retry",
                                    callback_data=f"cb_retry_upload_{error_key}",
                                ),
                            ]
                        ]
                    ),
                )
            except Exception:
                pass
        else:
            try:
                if remaining:
                    await client.send_message(
                        user_id,
                        "❌ <b>Upload paused</b>\n"
                        "<blockquote>The encoded output is still on disk.</blockquote>",
                        reply_markup=InlineKeyboardMarkup(
                            [[
                                InlineKeyboardButton(
                                    "🔁 Retry",
                                    callback_data=f"cb_retry_upload_{error_key}",
                                )
                            ]]
                        ),
                    )
                else:
                    await client.send_message(
                        user_id,
                        "❌ <b>Encoded output expired before upload</b>\n"
                        "<i>Please send the source video again.</i>",
                    )
            except Exception:
                pass
        output_deleted = False
    finally:
        UPLOAD_INFLIGHT.pop(inflight_key, None)
        if not _persist_upload_retries():
            log.warning("Final upload recovery manifest could not be persisted")
        if output_deleted:
            for item in remaining:
                try:
                    if os.path.isfile(item["file_path"]):
                        os.remove(item["file_path"])
                except OSError:
                    pass
        for path in [*auto_thumb_paths, *thumb_cleanup_paths]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        if reserved_bytes and not reservation_retained:
            await queue_manager.release_disk(reserved_bytes)


async def encode(
    ffmpeg_cmd: str,
    input_file: str,
    client: Client,
    user_id: int,
    custom_output_name: Optional[str] = None,
    display_mode: str = "rich",
    update_interval: float = 3.0,
    message: Optional[Message] = None,
    chat_id: int = 0,
    message_id: int = 0,
    source_file_name: Optional[str] = None,
    source_id: str = "",
    reserved_bytes: int = 0,
) -> Dict[str, Any]:
    if not custom_output_name:
        custom_output_name = f"encoded_{Path(input_file).name}"
    safe_output_name = sanitize_filename(Path(str(custom_output_name)).name)
    if not safe_output_name:
        safe_output_name = f"encoded_{Path(input_file).name}"
    output_base = str(Path(input_file).parent / os.path.splitext(safe_output_name)[0])

    # Pre-assign the job id so the queued card and worker share one id.
    job_id = str(uuid.uuid4())[:8]
    file_name = sanitize_filename(source_file_name or Path(input_file).name)

    if message:
        try:
            await message.delete()
        except Exception:
            pass

    try:
        me = await client.get_me()
        queue_url = f"https://t.me/{me.username}?start=queue"
    except Exception:
        queue_url = None

    markup = None
    if queue_url:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📋 Open queue", url=queue_url)]]
        )

    message = await client.send_message(
        user_id,
        f"⏳ <b>Queued</b>\n"
        f"<blockquote>🆔 <code>{job_id}</code>\n"
        f"Waiting for a free worker slot…</blockquote>\n"
        f"<i>Track progress in /queue</i>",
        reply_markup=markup,
    )

    async def _worker_impl(jid_arg):
        try:
            source_duration = float((await probe_file(input_file))["duration"])
        except Exception:
            source_duration = 0.0
        settings = normalize_settings(await get_user_settings(user_id))

        from bot.func.ffmpeg_utils import prepare_thumbnail, prepare_watermark_assets

        prepare_watermark_assets(user_id, settings)
        thumbnail_path = prepare_thumbnail(user_id, settings)

        settings["user_id"] = user_id

        commands = generate_ffmpeg_cmd(
            settings, input_file, output_base, thumbnail_path
        )

        video_settings = settings["video"]
        codec = video_settings["codec"]
        crf = video_settings["crf"]
        preset = video_settings["preset"]
        duration_limit = _duration_limit(video_settings)
        output_files = [
            {
                "file_path": command["output_file"],
                "resolution": command.get("suffix", "1080p"),
            }
            for command in commands
        ]
        keep_outputs = len(output_files) > 1

        final_status = "FAILED"
        for i, cmd_info in enumerate(commands):
            is_last = i == len(commands) - 1
            resolution = cmd_info.get("suffix", "1080p")

            status = await _run_encoding_job(
                cmd_info["cmd"],
                input_file,
                cmd_info["output_file"],
                client,
                message,
                jid_arg,
                user_id,
                cleanup_input=is_last,
                codec=codec,
                crf=str(crf),
                preset=preset,
                resolution=resolution,
                current_step=i + 1,
                total_steps=len(commands),
                thumbnail_path=thumbnail_path,
                duration_limit=duration_limit,
                keep_output=keep_outputs,
                output_files=output_files,
                display_name=file_name,
                commands=commands,
                source_duration=source_duration,
                delivery_settings=settings,
                chat_id=chat_id,
                message_id=message_id,
                source_id=source_id,
            )
            if status != "FINISHED":
                final_status = status
                if status in ("FAILED", "CANCELLED"):
                    deletion_ok = True
                    for output in output_files:
                        try:
                            if os.path.exists(output["file_path"]):
                                os.remove(output["file_path"])
                        except OSError:
                            deletion_ok = False
                    if not deletion_ok:
                        _track_failed_cleanup(
                            f"failed_{jid_arg}",
                            user_id,
                            output_files,
                        )
                    try:
                        if os.path.exists(input_file):
                            os.remove(input_file)
                    except OSError:
                        pass
                break
        else:
            final_status = "FINISHED"

        return final_status

    async def worker(jid_arg):
        try:
            return await _worker_impl(jid_arg)
        except Exception as exc:
            log.error(f"Queued encode worker failed: {exc}", exc_info=True)
            _remember_error(jid_arg, str(exc))
            try:
                await message.edit(
                    "❌ <b>Encoding failed</b>\n"
                    "<blockquote>The job could not be prepared. No upload was started; "
                    "please send the source video again.</blockquote>",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🗑 Dismiss", callback_data="cb_close")]]
                    ),
                )
            except Exception:
                pass
            cleanup_ok = True
            cleanup_outputs = []
            try:
                if os.path.exists(input_file):
                    os.remove(input_file)
                output_prefix = Path(output_base).name
                for candidate in Path(input_file).parent.glob(f"{output_prefix}*"):
                    if candidate.is_file() and candidate.suffix.lower() in {".mkv", ".mp4"}:
                        cleanup_outputs.append({"file_path": str(candidate)})
                        candidate.unlink()
            except OSError:
                cleanup_ok = False
            if not cleanup_ok and cleanup_outputs:
                _track_failed_cleanup(
                    f"failed_{jid_arg}",
                    user_id,
                    cleanup_outputs,
                )
            return "FAILED"

    try:
        file_size_str = humanbytes(os.path.getsize(input_file))
    except OSError:
        file_size_str = "Unknown"

    queued_id = await queue_manager.add_job(
        user_id,
        worker,
        job_id,
        job_id=job_id,
        file_size=file_size_str,
        file_name=file_name,
        chat_id=chat_id,
        message_id=message_id,
        task_type="encode",
        input_file=input_file,
        output_file=output_base,
        source_id=str(source_id or ""),
        reserved_bytes=reserved_bytes,
    )

    if queued_id is None:
        # Duplicate or over-limit: do not orphan the downloaded file.
        try:
            if os.path.exists(input_file):
                os.remove(input_file)
        except Exception:
            pass
        await queue_manager.release_disk(reserved_bytes)
        await message.edit(
            "⚠️ <b>Job not queued</b>\n\n"
            "<i>Either this exact file is already queued, or the queue/concurrent "
            "job limit is full. Check /queue.</i>"
        )
        return {"success": False, "error": "Duplicate or limit reached"}

    try:
        await message.edit(
            f"⏳ <b>Job queued</b>\n"
            f"<blockquote>🆔 Job ID: <code>{queued_id}</code>\n"
            f"🔢 Position: <code>{queue_manager.queue_position(queued_id)}</code></blockquote>\n"
            f"<i>Updates appear here when encoding starts.</i>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📋 Open queue", callback_data="cb_queue_hint")]]
            ),
        )
    except asyncio.CancelledError:
        log.warning(f"Queued message update cancelled for {queued_id}; job remains queued")
    except Exception as exc:
        # The job is already owned by QueueManager; a failed status edit must
        # not make the intake handler delete its live input/reservation.
        log.warning(f"Could not update queued message {queued_id}: {exc}")

    return {"success": True, "job_id": queued_id, "output_file": output_base}
