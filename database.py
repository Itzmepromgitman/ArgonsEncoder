# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import copy
import hashlib
import os
import time as _time
from collections import OrderedDict
from datetime import datetime, time, timedelta
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

from bot.config import DB_NAME, DB_URI, DOWNLOAD_DIR
from bot.logger import LOGGER

log = LOGGER(__name__)

# Short-TTL, bounded LRU cache so settings menus and encode workers avoid
# hammering Mongo without retaining every user forever.
_settings_cache: OrderedDict = OrderedDict()
_settings_cache_ttl = 30.0  # seconds
_settings_cache_max = max(100, int(os.environ.get("SETTINGS_CACHE_MAX", "2000")))
_deleted_user_ids: set[int] = set()
privacy_admission_lock = asyncio.Lock()


# --- Internal Helpers ---


def clean_value(value):
    if isinstance(value, time):
        return {"__time__": value.strftime("%H:%M")}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}
    return value


def restore_value(value):
    if isinstance(value, dict) and "__time__" in value:
        return datetime.strptime(value["__time__"], "%H:%M").time()
    if isinstance(value, dict) and "__datetime__" in value:
        try:
            return datetime.fromisoformat(value["__datetime__"])
        except ValueError:
            return value
    if isinstance(value, list):
        return [restore_value(v) for v in value]
    if isinstance(value, dict):
        return {k: restore_value(v) for k, v in value.items()}
    return value


# --- Client ---

dbclient = AsyncIOMotorClient(DB_URI)
database = dbclient[DB_NAME]
user_data = database["users"]
config_data = database["config"]
privacy_tombstones = database["privacy_tombstones"]


def _tombstone_key(user_id: int) -> str:
    return hashlib.sha256(f"argons:{int(user_id)}".encode()).hexdigest()


def _privacy_marker_paths(user_id: int) -> list[str]:
    name = f".privacy_{_tombstone_key(user_id)}.marker"
    return [
        os.path.join(DOWNLOAD_DIR, name),
        str(Path.home() / ".argons-privacy" / name),
        str(Path(__file__).resolve().parent / ".privacy-markers" / name),
    ]


def _privacy_marker_path(user_id: int) -> str:
    return _privacy_marker_paths(user_id)[0]


async def tombstone_state(user_id: int) -> str:
    if int(user_id) in _deleted_user_ids:
        return "yes"
    try:
        doc = await privacy_tombstones.find_one(
            {"_id": _tombstone_key(user_id), "expires_at": {"$gt": datetime.utcnow()}}
        )
        if doc:
            _deleted_user_ids.add(int(user_id))
            return "yes"
        return "no"
    except Exception as exc:
        log.warning(f"Could not check privacy tombstone for {user_id}: {exc}")
        return "unknown"


async def is_user_tombstoned(user_id: int) -> bool:
    # Privacy writes fail closed: an uncertain lookup must not allow a late
    # worker to recreate a deleted profile.
    return await tombstone_state(user_id) != "no"


async def ping_database() -> bool:
    try:
        await dbclient.admin.command("ping")
        return True
    except Exception as exc:
        log.error(f"MongoDB ping failed: {exc}")
        return False


async def ensure_database_indexes() -> bool:
    try:
        await privacy_tombstones.create_index("expires_at", expireAfterSeconds=0)
        return True
    except Exception as exc:
        log.error(f"Could not create privacy tombstone TTL index: {exc}")
        return False


async def add_user(user_id: int, allow_tombstone: bool = False) -> bool:
    async with privacy_admission_lock:
        try:
            for marker_path in _privacy_marker_paths(user_id):
                if os.path.isfile(marker_path):
                    with open(marker_path, encoding="utf-8") as marker:
                        if marker.read().strip() != "complete":
                            return False
            state = await tombstone_state(user_id)
            if state == "unknown":
                return False
            if state == "yes":
                marker = await privacy_tombstones.find_one(
                    {"_id": _tombstone_key(user_id)}
                )
                if not allow_tombstone or not marker or not marker.get("purge_complete"):
                    return False
            existing_user = await user_data.find_one({"_id": user_id})
            if existing_user and existing_user.get("privacy_delete_pending"):
                return False
            await user_data.update_one(
                {"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True
            )
            await privacy_tombstones.delete_one({"_id": _tombstone_key(user_id)})
            _deleted_user_ids.discard(int(user_id))
            return True
        except Exception as e:
            log.error(f"Error adding user {user_id}: {e}")
            return False


async def del_user(user_id: int):
    try:
        await user_data.delete_one({"_id": user_id})
        _settings_cache.pop(user_id, None)
    except Exception as e:
        log.error(f"Error deleting user {user_id}: {e}")


async def forget_user_data(user_id: int) -> bool:
    """Serialize privacy deletion against new queue/delivery admission."""
    async with privacy_admission_lock:
        return await _forget_user_data_impl(user_id)


async def _forget_user_data_impl(user_id: int) -> bool:
    """Delete a user's database record and locally stored personal assets."""
    durable_ok = True
    asset_errors = 0
    _deleted_user_ids.add(int(user_id))
    if len(_deleted_user_ids) > 10_000:
        _deleted_user_ids.pop()
    download_dir = DOWNLOAD_DIR
    marker_paths = _privacy_marker_paths(user_id)
    marker_written = False
    for marker_path in marker_paths:
        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w", encoding="utf-8") as marker:
                marker.write("pending")
            os.chmod(marker_path, 0o600)
            marker_written = True
        except Exception as exc:
            log.warning(f"Could not write privacy marker {marker_path}: {exc}")
    if not marker_written:
        durable_ok = False
        log.error(f"No durable local privacy marker could be written for {user_id}")
    try:
        await user_data.update_one(
            {"_id": user_id},
            {"$set": {"privacy_delete_pending": True}},
            upsert=True,
        )
    except Exception as exc:
        durable_ok = False
        log.error(f"Could not mark privacy deletion pending for {user_id}: {exc}")
    try:
        await privacy_tombstones.update_one(
            {"_id": _tombstone_key(user_id)},
            {
                "$set": {
                    "expires_at": datetime.utcnow() + timedelta(days=30),
                    "purge_complete": False,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        durable_ok = False
        log.error(f"Could not write privacy tombstone for {user_id}: {exc}")
    try:
        from bot.config import DOWNLOAD_DIR as download_dir
        from bot.config import THUMB_DIR, WATERMARK_DIR

        # Stop active and queued work before removing files and retry entries.
        try:
            from bot.utils.listener import cancel_user_sessions

            await cancel_user_sessions(int(user_id))
        except Exception as exc:
            durable_ok = False
            log.debug(f"Could not cancel listener sessions during deletion: {exc}")
        try:
            from plugins.screenshot import _SCREENSHOT_TASKS

            screenshot_task = _SCREENSHOT_TASKS.get(int(user_id))
            if screenshot_task and not screenshot_task.done():
                screenshot_task.cancel()
                try:
                    await screenshot_task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception as exc:
            durable_ok = False
            log.debug(f"Could not cancel screenshot work during deletion: {exc}")
        try:
            from bot.func.upload_manager import upload_manager

            await upload_manager.cancel_user_jobs(user_id, preserve_recovery=True)
            from bot.func.queue_manager import queue_manager as upload_qm

            for upload_job in upload_manager.get_user_jobs(user_id):
                if upload_job.started:
                    continue
                reserved = int(upload_job.kwargs.get("reserved_bytes", 0) or 0)
                deletion_ok = True
                for item in upload_job.kwargs.get("output_files", []) or []:
                    path = item.get("file_path")
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                        except OSError:
                            deletion_ok = False
                            asset_errors += 1
                if deletion_ok:
                    if reserved:
                        await upload_qm.release_disk(reserved)
                    upload_manager._active_jobs.pop(upload_job.job_id, None)
                else:
                    durable_ok = False
        except Exception as exc:
            durable_ok = False
            log.debug(f"Could not clear queued uploads during deletion: {exc}")
        try:
            from bot.func.queue_manager import queue_manager

            if not await queue_manager.forget_deferred(user_id):
                durable_ok = False
            for job in queue_manager.get_user_jobs(user_id):
                await queue_manager.cancel_job(job.job_id)
            await queue_manager.save_queue(force=True)
        except Exception as exc:
            durable_ok = False
            log.debug(f"Could not clear queued user jobs during deletion: {exc}")
        try:
            from bot.func.encode import (
                ERROR_DETAILS,
                ERROR_DETAILS_OWNER,
                UPLOAD_INFLIGHT,
                UPLOAD_MANIFEST_ONLY,
                UPLOAD_RETRY,
                _persist_upload_retries,
                active_encodings,
            )

            for job_id, process in list(active_encodings.items()):
                if process.user_id == user_id:
                    await process.cancel()
                    if process.process is not None:
                        try:
                            await asyncio.wait_for(process.process.wait(), timeout=10)
                        except asyncio.TimeoutError:
                            durable_ok = False
                        except ProcessLookupError:
                            pass
            for key, context in list(UPLOAD_RETRY.items()):
                if context.get("user_id") == user_id:
                    reserved = int(context.get("reserved_bytes", 0) or 0)
                    deletion_ok = True
                    for item in context.get("output_files", []) or []:
                        path = item.get("file_path")
                        if path and os.path.isfile(path):
                            try:
                                os.remove(path)
                            except OSError:
                                deletion_ok = False
                                asset_errors += 1
                    if deletion_ok:
                        if reserved:
                            from bot.func.queue_manager import queue_manager as qm

                            await qm.release_disk(reserved)
                        UPLOAD_RETRY.pop(key, None)
                    else:
                        durable_ok = False
            for key, context in list(UPLOAD_MANIFEST_ONLY.items()):
                if int(context.get("user_id", 0) or 0) == user_id:
                    deletion_ok = True
                    for item in context.get("output_files", []) or []:
                        path = item.get("file_path")
                        if path and os.path.isfile(path):
                            try:
                                os.remove(path)
                            except OSError:
                                deletion_ok = False
                                asset_errors += 1
                    if deletion_ok:
                        UPLOAD_MANIFEST_ONLY.pop(key, None)
                    else:
                        durable_ok = False
            from bot.func.upload_manager import upload_manager as um

            for key, context in list(UPLOAD_INFLIGHT.items()):
                if context.get("user_id") == user_id and not um.get_user_jobs(user_id):
                    reserved = int(context.get("reserved_bytes", 0) or 0)
                    deletion_ok = True
                    for item in context.get("output_files", []) or []:
                        path = item.get("file_path")
                        if path and os.path.isfile(path):
                            try:
                                os.remove(path)
                            except OSError:
                                deletion_ok = False
                                asset_errors += 1
                    if deletion_ok:
                        if reserved:
                            from bot.func.queue_manager import queue_manager as qm

                            await qm.release_disk(reserved)
                        UPLOAD_INFLIGHT.pop(key, None)
                    else:
                        durable_ok = False
            for key, owner in list(ERROR_DETAILS_OWNER.items()):
                if owner == user_id:
                    ERROR_DETAILS.pop(key, None)
                    ERROR_DETAILS_OWNER.pop(key, None)
            if not _persist_upload_retries():
                durable_ok = False
        except Exception as exc:
            durable_ok = False
            log.debug(f"Could not clear active user jobs during deletion: {exc}")

        if not durable_ok:
            # Keep the tombstone so late workers cannot recreate the profile;
            # do not claim a complete deletion after a durable-state failure.
            return False
        try:
            from bot.func.queue_manager import queue_manager as active_queue

            if active_queue.get_user_jobs(user_id):
                durable_ok = False
        except Exception as exc:
            log.debug(f"Could not verify queue cancellation: {exc}")
            durable_ok = False
        if not durable_ok:
            return False
        try:
            from bot.func.upload_manager import upload_manager as active_uploads

            if active_uploads.get_user_jobs(user_id):
                return False
        except Exception as exc:
            log.debug(f"Could not verify upload cancellation: {exc}")
            return False

        for root in (WATERMARK_DIR, THUMB_DIR):
            for name in (f"{user_id}.png", f"{user_id}.jpg", f"fonts/{user_id}.ttf"):
                path = os.path.join(root, name)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    asset_errors += 1
            try:
                prefixes = (
                    f"{user_id}_",
                    f"preview_{user_id}_",
                    f"auto_{user_id}_",
                    f"delivery_{user_id}_",
                    f"auto_delivery_{user_id}_",
                    f"view_{user_id}_",
                    f"legacy_{user_id}_",
                )
                for name in os.listdir(root):
                    if name.startswith(prefixes):
                        path = os.path.join(root, name)
                        if os.path.isfile(path):
                            os.remove(path)
            except OSError:
                asset_errors += 1
        try:
            font_dir = os.path.join(WATERMARK_DIR, "fonts")
            for name in os.listdir(font_dir):
                if name.startswith(f"{user_id}_") or name == f"{user_id}.ttf":
                    path = os.path.join(font_dir, name)
                    if os.path.isfile(path):
                        os.remove(path)
        except OSError:
            asset_errors += 1
        try:
            for name in os.listdir(download_dir):
                if (
                    name.startswith(f"{user_id}_")
                    or name.startswith(f"encoded_{user_id}_")
                    or name.startswith(f"restored_{user_id}_")
                ):
                    path = os.path.join(download_dir, name)
                    if os.path.isfile(path):
                        os.remove(path)
        except OSError:
            asset_errors += 1

        if asset_errors:
            durable_ok = False
        await user_data.delete_one({"_id": user_id})
        if durable_ok:
            finalized_marker = False
            for marker_path in marker_paths:
                try:
                    with open(marker_path, "w", encoding="utf-8") as marker:
                        marker.write("complete")
                    os.chmod(marker_path, 0o600)
                    finalized_marker = True
                except Exception as exc:
                    log.warning(f"Could not finalize privacy marker {marker_path}: {exc}")
            if not finalized_marker:
                durable_ok = False
        if durable_ok:
            try:
                await privacy_tombstones.update_one(
                    {"_id": _tombstone_key(user_id)},
                    {"$set": {"purge_complete": True}},
                )
            except Exception as exc:
                log.error(f"Could not finalize privacy tombstone for {user_id}: {exc}")
                durable_ok = False
        _settings_cache.pop(user_id, None)
        return durable_ok
    except Exception as e:
        _deleted_user_ids.discard(int(user_id))
        log.error(f"Error forgetting user {user_id}: {e}")
        return False


async def present_user(user_id: int):
    try:
        state = await tombstone_state(user_id)
        if state == "unknown":
            return None
        if state == "yes":
            return False
        found = await user_data.find_one({"_id": user_id})
        return bool(found)
    except Exception as e:
        log.error(f"Error finding user {user_id}: {e}")
        return None


async def full_userbase():
    try:
        cursor = user_data.find({}, {"_id": 1})
        return [doc["_id"] async for doc in cursor]
    except Exception as e:
        log.error(f"Error retrieving user base: {e}")
        return []


async def count_users() -> int:
    """Count registrations without loading every user ID into memory."""
    try:
        return await user_data.count_documents({})
    except Exception as e:
        log.error(f"Error counting users: {e}")
        return 0


async def get_user_settings(user_id: int):
    async with privacy_admission_lock:
        return await _get_user_settings_unlocked(user_id)


async def _get_user_settings_unlocked(user_id: int):
    """Retrieve settings from a bounded TTL cache or MongoDB."""
    if await is_user_tombstoned(user_id):
        return {}
    cached = _settings_cache.get(user_id)
    if cached is not None and (_time.time() - cached[0]) < _settings_cache_ttl:
        _settings_cache.move_to_end(user_id)
        return copy.deepcopy(cached[1])
    try:
        user = await user_data.find_one({"_id": user_id})
        settings = user.get("settings") if user and "settings" in user else {}
        settings = settings or {}
        _settings_cache[user_id] = (_time.time(), copy.deepcopy(settings))
        _settings_cache.move_to_end(user_id)
        while len(_settings_cache) > _settings_cache_max:
            _settings_cache.popitem(last=False)
        return settings
    except Exception as e:
        log.error(f"Error getting settings for {user_id}: {e}")
        return copy.deepcopy(cached[1]) if cached else {}


async def update_user_settings(user_id: int, settings: dict) -> bool:
    async with privacy_admission_lock:
        return await _update_user_settings_unlocked(user_id, settings)


async def _update_user_settings_unlocked(user_id: int, settings: dict) -> bool:
    """Update settings and atomically refresh this process's cache."""
    if await is_user_tombstoned(user_id):
        return False
    try:
        snapshot = copy.deepcopy(settings)
        await user_data.update_one(
            {"_id": user_id}, {"$set": {"settings": snapshot}}, upsert=True
        )
        _settings_cache[user_id] = (_time.time(), snapshot)
        _settings_cache.move_to_end(user_id)
        while len(_settings_cache) > _settings_cache_max:
            _settings_cache.popitem(last=False)
        return True
    except Exception as e:
        log.error(f"Error updating settings for {user_id}: {e}")
        return False


# --- Config variables ---


async def set_variable(key: str, value):
    """Set a configuration variable in the database and report success."""
    try:
        await config_data.update_one(
            {"_id": key},
            {"$set": {"value": clean_value(value)}},
            upsert=True,
        )
        return True
    except Exception as e:
        log.error(f"Error setting variable '{key}': {e}")
        return False


async def get_variable_strict(key: str, default=None):
    """Retrieve a variable without converting database errors into defaults."""
    entry = await config_data.find_one({"_id": key})
    if not entry:
        await config_data.insert_one({"_id": key, "value": clean_value(default)})
        return default
    value = entry.get("value", default)
    return default if value is None else restore_value(value)


async def get_variable(key: str, default=None):
    """Retrieve a configuration variable, falling back to default."""
    try:
        entry = await config_data.find_one({"_id": key})
    except Exception as e:
        log.error(f"Error getting variable '{key}': {e}")
        return default

    if not entry:
        try:
            await config_data.insert_one({"_id": key, "value": clean_value(default)})
        except Exception as e:
            log.debug(f"Could not persist default for '{key}': {e}")
        return default

    value = entry.get("value", default)
    return default if value is None else restore_value(value)


async def get_all_variables():
    """Retrieve all configuration variable keys and values."""
    variables = []
    try:
        cursor = config_data.find({})
        async for entry in cursor:
            variables.append((entry["_id"], restore_value(entry.get("value"))))
    except Exception as e:
        log.error(f"Error listing variables: {e}")
    return variables


# --- Aggregate stats ---


async def inc_stats(fields: dict, user_id: int = None):
    """Atomically increment global (and optional per-user) counters."""
    if user_id is not None and await is_user_tombstoned(user_id):
        return
    try:
        await config_data.update_one(
            {"_id": "stats"}, {"$inc": clean_value(fields)}, upsert=True
        )
        if user_id is not None and int(user_id) not in _deleted_user_ids:
            await user_data.update_one(
                {"_id": user_id},
                {"$inc": {f"stats.{k}": v for k, v in fields.items()}},
                upsert=False,
            )
    except Exception as e:
        log.error(f"Error incrementing stats: {e}")


async def get_stats() -> dict:
    try:
        doc = await config_data.find_one({"_id": "stats"})
        return doc or {}
    except Exception as e:
        log.error(f"Error reading stats: {e}")
        return {}
