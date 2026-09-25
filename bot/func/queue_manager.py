# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from bot.config import (
    DOWNLOAD_DIR,
    MAX_CONCURRENT_JOBS,
    MAX_JOBS_PER_USER,
    MAX_QUEUE_LENGTH,
    MIN_FREE_DISK_BYTES,
    OWNER_ID,
)
from bot.logger import LOGGER
from database import get_variable_strict, set_variable

log = LOGGER(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SAVE_DEBOUNCE_SECONDS = float(os.environ.get("SAVE_DEBOUNCE_SECONDS", "2.0"))


@dataclass
class Job:
    job_id: str
    user_id: int
    func: Callable[..., Awaitable[Any]]
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, running, yielded, completed, failed, cancelled
    file_size: str = "Unknown"
    file_name: str = "Unknown"
    chat_id: int = 0
    message_id: int = 0
    task_type: str = "generic"
    input_file: str = ""
    output_file: str = ""
    source_id: str = ""
    reserved_bytes: int = 0
    cancel_requested: bool = False
    cleanup_pending: bool = False

    def to_dict(self):
        # Only primitive, BSON-safe fields are persisted.
        # func/args/kwargs are rebuilt on restore via reconstruct_worker().
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "status": self.status,
            "file_size": self.file_size,
            "file_name": self.file_name,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "task_type": self.task_type,
            "input_file": self.input_file,
            "output_file": self.output_file,
            "source_id": self.source_id,
            "reserved_bytes": self.reserved_bytes,
            "cleanup_pending": self.cleanup_pending,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            job_id=data["job_id"],
            user_id=int(data["user_id"]),
            func=None,  # Must be re-attached
            status=data.get("status", "pending"),
            file_size=data.get("file_size", "Unknown"),
            file_name=data.get("file_name", "Unknown"),
            chat_id=int(data.get("chat_id", 0) or 0),
            message_id=int(data.get("message_id", 0) or 0),
            task_type=data.get("task_type", "generic"),
            input_file=data.get("input_file", ""),
            output_file=data.get("output_file", ""),
            source_id=str(data.get("source_id", "") or ""),
            reserved_bytes=int(data.get("reserved_bytes", 0) or 0),
            cleanup_pending=bool(data.get("cleanup_pending", False)),
        )


class QueueManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(QueueManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._queue: asyncio.Queue = asyncio.Queue()
        self._active_jobs: Dict[str, Job] = {}
        self._jobs: Dict[str, Job] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._process_tasks: Set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._reserved_bytes = 0
        self._deferred_jobs = []
        self._client = None
        self._disk_lock = asyncio.Lock()
        self._dirty = False
        self._stopping = False
        self._flusher_task: Optional[asyncio.Task] = None
        self._initialized = True
        log.info(f"QueueManager initialized with {MAX_CONCURRENT_JOBS} concurrent slots")

    # ------------------------------------------------------------------
    # Persistence (debounced)
    # ------------------------------------------------------------------

    def mark_dirty(self):
        self._dirty = True
        if self._flusher_task is None or self._flusher_task.done():
            self._flusher_task = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
            if self._dirty:
                await self.save_queue()

    async def save_queue(self, force: bool = False):
        try:
            jobs_data = []
            seen_ids = set()
            for job in self._jobs.values():
                if job.status in ("pending", "running", "yielded") or job.cleanup_pending:
                    job_dict = job.to_dict()
                    if job_dict["status"] in ("running", "yielded") or job.cleanup_pending:
                        # Restart as pending; cleanup-pending rows must remain
                        # visible so a later privacy/operator pass can finish.
                        job_dict["status"] = "pending"
                    jobs_data.append(job_dict)
                    seen_ids.add(job.job_id)
            for deferred in self._deferred_jobs:
                if deferred.get("job_id") in seen_ids:
                    continue
                deferred_copy = dict(deferred)
                if deferred_copy.get("status") in ("running", "yielded"):
                    deferred_copy["status"] = "pending"
                jobs_data.append(deferred_copy)
                seen_ids.add(deferred_copy.get("job_id"))

            saved = await set_variable("queue_state", jobs_data)
            if not saved:
                self._dirty = True
                if force:
                    raise RuntimeError("queue persistence failed")
                return
            self._dirty = False
        except Exception as e:
            log.error(f"Failed to save queue: {e}")
            if force:
                raise

    async def restore_queue(self, client):
        self._client = client
        try:
            jobs_data = await get_variable_strict("queue_state", [])
            if not isinstance(jobs_data, list):
                log.error("Persisted queue state has an invalid schema")
                return False
            if not jobs_data:
                log.info("No older queue found")
                return True

            log.info(f"Restoring {len(jobs_data)} jobs from database...")
            self._reserved_bytes = sum(
                int(job.reserved_bytes or 0) for job in self._jobs.values()
            )
            self._deferred_jobs = list(self._deferred_jobs)

            from bot.func.encode import reconstruct_worker

            restored = 0
            schema_invalid = False
            for index, data in enumerate(jobs_data):
                if not isinstance(data, dict):
                    schema_invalid = True
                    log.warning("Malformed queue row")
                    continue
                row_user_id = int(data.get("user_id", 0) or 0)
                user_job_count = sum(
                    item.user_id == row_user_id for item in self._jobs.values()
                )
                if len(self._jobs) >= MAX_QUEUE_LENGTH or user_job_count >= MAX_JOBS_PER_USER:
                    self._deferred_jobs.extend(jobs_data[index:])
                    log.warning(
                        "Queue limit deferred %d persisted job(s)",
                        len(jobs_data[index:]),
                    )
                    break
                try:
                    job = Job.from_dict(data)
                    if job.job_id in self._jobs:
                        continue
                except Exception as e:
                    schema_invalid = True
                    log.warning(f"Malformed queue row: {e}")
                    continue

                from database import is_user_tombstoned

                if await is_user_tombstoned(job.user_id):
                    cleanup_ok = True
                    for path in (job.input_file, job.output_file):
                        if path:
                            try:
                                if os.path.exists(path):
                                    os.remove(path)
                            except OSError:
                                cleanup_ok = False
                    if not cleanup_ok:
                        retained = dict(data)
                        retained["status"] = "pending"
                        retained["cleanup_pending"] = True
                        self._deferred_jobs.append(retained)
                        log.error(
                            "Retained tombstoned queue row with undeleted files: %s",
                            job.job_id,
                        )
                    continue
                if job.task_type == "resume":
                    # A yielded process cannot survive a process restart. Rebuild
                    # it as a fresh encode from the original Telegram message.
                    job.task_type = "encode"
                elif job.task_type != "encode":
                    continue

                try:
                    job.func = reconstruct_worker(job, client)
                except Exception as e:
                    log.warning(f"Could not reconstruct job {job.job_id}: {e}")
                    continue

                if not job.args or job.args == ("PLACEHOLDER",):
                    job.args = (job.job_id,)
                job.status = "pending"
                if job.reserved_bytes and not await self.reserve_disk(job.reserved_bytes):
                    self._deferred_jobs.append(data)
                    log.warning("Deferred job %s: disk reservation unavailable", job.job_id)
                    continue
                self._jobs[job.job_id] = job
                await self._queue.put(job)
                restored += 1
                log.info(f"Restored job {job.job_id}")

            if schema_invalid:
                log.error("Queue state contains malformed rows; refusing rewrite")
                return False
            if self._deferred_jobs:
                log.error(
                    "Keeping %d persisted job(s) deferred; do not rewrite state",
                    len(self._deferred_jobs),
                )
                try:
                    await client.send_message(
                        OWNER_ID,
                        "⚠️ <b>Some queue jobs remain deferred</b>\n"
                        f"<i>{len(self._deferred_jobs)} persisted job(s) are waiting "
                        "for a queue slot or disk reservation.</i>",
                    )
                except Exception:
                    pass
            else:
                # Rewrite only when no row was deferred; otherwise the original
                # persisted state remains the source of truth.
                await self.save_queue(force=True)

            if restored:
                await self.start()
            return True
        except Exception as e:
            log.error(f"Failed to restore queue: {e}")
            return False

    async def forget_deferred(self, user_id: int) -> bool:
        removed = 0
        ok = True
        for data in list(self._deferred_jobs):
            if int(data.get("user_id", 0) or 0) != int(user_id):
                continue
            for path in [data.get("input_file", ""), data.get("output_file", "")]:
                if path:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        ok = False
            if not ok:
                continue
            self._deferred_jobs.remove(data)
            removed += 1
        if removed:
            try:
                await self.save_queue(force=True)
            except Exception as exc:
                log.error(f"Could not persist deferred-job deletion: {exc}")
                return False
        return ok

    async def retry_deferred(self):
        from database import privacy_admission_lock

        async with privacy_admission_lock:
            if self._stopping:
                return 0
            return await self._retry_deferred_unlocked()

    async def _retry_deferred_unlocked(self):
        """Re-admit persisted jobs when a queue slot/disk reservation frees."""
        if not self._deferred_jobs or self._client is None:
            return 0
        from bot.func.encode import reconstruct_worker

        admitted = 0
        removed = 0
        for data in list(self._deferred_jobs):
            if len(self._jobs) >= MAX_QUEUE_LENGTH:
                break
            try:
                job = Job.from_dict(data)
                from database import is_user_tombstoned

                if await is_user_tombstoned(job.user_id):
                    deletion_ok = True
                    for path in (job.input_file, job.output_file):
                        if path:
                            try:
                                if os.path.exists(path):
                                    os.remove(path)
                            except OSError:
                                deletion_ok = False
                    if not deletion_ok:
                        log.error("Could not remove tombstoned deferred files: %s", job.job_id)
                        continue
                    self._deferred_jobs.remove(data)
                    removed += 1
                    continue
                if sum(item.user_id == job.user_id for item in self._jobs.values()) >= MAX_JOBS_PER_USER:
                    continue
                if job.cleanup_pending:
                    continue
                if job.job_id in self._jobs:
                    self._deferred_jobs.remove(data)
                    removed += 1
                    continue
                if job.task_type == "resume":
                    job.task_type = "encode"
                if job.task_type != "encode":
                    self._deferred_jobs.remove(data)
                    removed += 1
                    continue
                job.func = reconstruct_worker(job, self._client)
                if not job.args or job.args == ("PLACEHOLDER",):
                    job.args = (job.job_id,)
                job.status = "pending"
                if job.reserved_bytes and not await self.reserve_disk(job.reserved_bytes):
                    continue
                self._jobs[job.job_id] = job
                await self._queue.put(job)
                self._deferred_jobs.remove(data)
                admitted += 1
            except Exception as exc:
                log.warning(f"Could not retry deferred job: {exc}")
        if admitted or removed:
            try:
                await self.save_queue(force=True)
            except Exception as exc:
                self._stopping = True
                log.error(f"Could not persist deferred queue update: {exc}")
                return 0
            await self.start()
        return admitted

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def _new_job_id(self) -> str:
        while True:
            job_id = str(uuid.uuid4())[:8]
            if job_id not in self._jobs:
                return job_id

    def has_source(self, user_id: int, source_id: str) -> bool:
        if not source_id:
            return False
        return any(
            job.user_id == user_id
            and job.source_id == source_id
            and (job.status in ("pending", "running", "yielded") or job.cleanup_pending)
            for job in self._jobs.values()
        )

    async def reserve_disk(self, requested_bytes: int) -> bool:
        requested_bytes = max(0, int(requested_bytes))
        async with self._disk_lock:
            try:
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                free = shutil.disk_usage(DOWNLOAD_DIR).free - self._reserved_bytes
            except OSError:
                return False
            if free - requested_bytes < MIN_FREE_DISK_BYTES:
                return False
            self._reserved_bytes += requested_bytes
            return True

    async def release_disk(self, reserved_bytes: int) -> None:
        async with self._disk_lock:
            self._reserved_bytes = max(0, self._reserved_bytes - max(0, int(reserved_bytes)))

    def can_accept_job(self, user_id: int) -> bool:
        return (
            len(self.get_all_jobs()) < MAX_QUEUE_LENGTH
            and len(self.get_user_jobs(user_id)) < MAX_JOBS_PER_USER
        )

    async def add_job(self, *args, **kwargs):
        from database import privacy_admission_lock

        async with privacy_admission_lock:
            return await self._add_job_unlocked(*args, **kwargs)

    async def _add_job_unlocked(
        self,
        user_id: int,
        func: Callable[..., Awaitable[Any]],
        *args,
        job_id: Optional[str] = None,
        file_size: str = "Unknown",
        file_name: str = "Unknown",
        chat_id: int = 0,
        message_id: int = 0,
        task_type: str = "generic",
        input_file: str = "",
        output_file: str = "",
        source_id: str = "",
        reserved_bytes: int = 0,
        **kwargs,
    ) -> Optional[str]:
        if self._stopping:
            log.warning("Rejecting new queue work while shutdown is in progress")
            return None
        try:
            from database import is_user_tombstoned

            if await is_user_tombstoned(user_id):
                log.warning("Rejecting queue work for tombstoned user %s", user_id)
                return None
        except Exception as exc:
            log.error(f"Could not verify privacy state for queue intake: {exc}")
            return None
        # Telegram file IDs survive renames and are the strongest duplicate
        # signal. Fall back to the display name for legacy restored jobs.
        if source_id or file_name != "Unknown":
            for job in self._jobs.values():
                if job.user_id != user_id or job.status not in (
                    "pending",
                    "running",
                    "yielded",
                ):
                    continue
                duplicate = bool(source_id and job.source_id == source_id)
                if not source_id and not job.source_id and job.file_name == file_name:
                    duplicate = True
                if duplicate:
                    log.warning(
                        f"Duplicate job attempt by user {user_id} for file {file_name}"
                    )
                    return None

        if len(self.get_all_jobs()) >= MAX_QUEUE_LENGTH:
            log.warning("Global queue limit reached")
            return None

        # Per-user cap
        if len(self.get_user_jobs(user_id)) >= MAX_JOBS_PER_USER:
            log.warning(
                f"User {user_id} hit the job limit ({MAX_JOBS_PER_USER})"
            )
            return None

        if job_id is None or job_id in self._jobs or job_id == "PLACEHOLDER":
            job_id = self._new_job_id()

        job = Job(
            job_id=job_id,
            user_id=user_id,
            func=func,
            args=args,
            kwargs=kwargs,
            file_size=file_size,
            file_name=file_name,
            chat_id=chat_id,
            message_id=message_id,
            task_type=task_type,
            input_file=input_file,
            output_file=output_file,
            source_id=str(source_id or ""),
            reserved_bytes=max(0, int(reserved_bytes or 0)),
        )
        self._jobs[job_id] = job
        await self._queue.put(job)
        log.info(f"Job {job_id} added to queue for user {user_id}")

        self.mark_dirty()

        if self._worker_task is None or self._worker_task.done():
            await self.start()

        return job_id

    async def cancel_job(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False

        job = self._jobs[job_id]

        def clean_files(j: Job) -> bool:
            success = True
            if j.input_file and os.path.exists(j.input_file):
                try:
                    os.remove(j.input_file)
                    log.info(f"Removed input file for job {j.job_id}")
                except OSError as exc:
                    success = False
                    log.error(f"Failed to remove input file for job {j.job_id}: {exc}")
            if j.output_file and os.path.exists(j.output_file):
                try:
                    os.remove(j.output_file)
                    log.info(f"Removed output file for job {j.job_id}")
                except OSError as exc:
                    success = False
                    log.error(f"Failed to remove output file for job {j.job_id}: {exc}")
            return success

        if job.cleanup_pending:
            if clean_files(job):
                await self.release_disk(job.reserved_bytes)
                job.reserved_bytes = 0
                job.cleanup_pending = False
                self._jobs.pop(job_id, None)
                self.mark_dirty()
                return True
            return False

        if job.status == "running":
            job.status = "cancelled"
            job.cancel_requested = True
            # Kill the actual ffmpeg process so the slot frees up.
            try:
                from bot.func.encode import active_encodings

                proc = active_encodings.get(job_id)
                if proc is not None:
                    await proc.cancel()
            except Exception as e:
                log.error(f"Failed to kill process for job {job_id}: {e}")
            log.info(f"Job {job_id} marked for cancellation")
            self.mark_dirty()
            return True

        if job.status == "yielded":
            job.status = "cancelled"
            cleanup_ok = True
            proc = None
            try:
                from bot.func.encode import active_encodings

                proc = active_encodings.get(job_id)
                if proc is not None:
                    await proc.cancel()
                    try:
                        await proc.message.edit("🚫 <b>Encoding cancelled</b>")
                    except Exception:
                        pass
                    if proc.input_file and os.path.exists(proc.input_file):
                        try:
                            os.remove(proc.input_file)
                        except OSError:
                            cleanup_ok = False
                    output_paths = proc.output_files or [
                        {"file_path": proc.output_file}
                    ]
                    for output in output_paths:
                        if output.get("file_path") and os.path.exists(output["file_path"]):
                            try:
                                os.remove(output["file_path"])
                            except OSError:
                                cleanup_ok = False
            except Exception as e:
                cleanup_ok = False
                log.error(f"Failed to cancel yielded job {job_id}: {e}")
            if cleanup_ok:
                if proc is not None:
                    from bot.func.encode import active_encodings

                    active_encodings.pop(job_id, None)
                await self.release_disk(job.reserved_bytes)
                job.reserved_bytes = 0
            else:
                job.cleanup_pending = True
            self.mark_dirty()
            return cleanup_ok

        if job.status == "pending":
            job.status = "cancelled"
            if not clean_files(job):
                job.cleanup_pending = True
                self.mark_dirty()
                return False
            await self.release_disk(job.reserved_bytes)
            job.reserved_bytes = 0
            self.mark_dirty()
            return True

        return False

    async def clear_queue(self):
        log.info("Clearing queue...")

        for job in list(self._jobs.values()):
            if job.status == "pending":
                await self.cancel_job(job.job_id)

        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        await self.save_queue()
        log.info("Queue cleared")

    async def start(self):
        if self._stopping:
            return
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            log.info("QueueManager worker started")

    def resume(self):
        self._stopping = False

    async def shutdown(self) -> bool:
        """Persist a restart snapshot and stop child encoders cleanly."""
        from database import privacy_admission_lock

        async with privacy_admission_lock:
            if self._stopping:
                return True
            self._stopping = True
            persisted = True
            # Save before cancellation: running/yielded rows are serialized as
            # pending and can be reconstructed from their Telegram source message.
            try:
                await self.save_queue(force=True)
            except Exception as e:
                persisted = False
                log.error(f"Shutdown save failed: {e}")
            if not persisted:
                self._stopping = False
                return False

        try:
            from bot.func.encode import active_encodings

            for process in list(active_encodings.values()):
                try:
                    await process.cancel()
                except Exception as exc:
                    log.warning(f"Could not stop FFmpeg during shutdown: {exc}")
        except Exception as exc:
            log.warning(f"Could not access active encoders during shutdown: {exc}")

        current = asyncio.current_task()
        pending_tasks = [
            task
            for task in self._process_tasks
            if task is not current and not task.done()
        ]
        if pending_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_tasks, return_exceptions=True),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                log.warning("Timed out waiting for encoding tasks during shutdown")

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._flusher_task and not self._flusher_task.done():
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
        return persisted

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self):
        log.info("Queue worker loop started")
        while True:
            acquired = False
            try:
                await self._semaphore.acquire()
                acquired = True
                job = await self._queue.get()

                if job.status == "cancelled":
                    log.info(f"Skipping cancelled job {job.job_id}")
                    if not job.cleanup_pending:
                        if job.reserved_bytes:
                            await self.release_disk(job.reserved_bytes)
                            job.reserved_bytes = 0
                        self._jobs.pop(job.job_id, None)
                    self._queue.task_done()
                    self._semaphore.release()
                    self.mark_dirty()
                    continue

                task = asyncio.create_task(self._process_job(job))
                self._process_tasks.add(task)
                task.add_done_callback(self._process_tasks.discard)

            except asyncio.CancelledError:
                if acquired:
                    self._semaphore.release()
                break
            except Exception as e:
                log.error(f"Error in queue worker: {e}")
                if acquired:
                    self._semaphore.release()
                await asyncio.sleep(1)

    async def _process_job(self, job: Job):
        try:
            if job.status == "cancelled" or job.cancel_requested or job.cleanup_pending:
                if not job.cleanup_pending:
                    if job.reserved_bytes:
                        await self.release_disk(job.reserved_bytes)
                        job.reserved_bytes = 0
                    self._jobs.pop(job.job_id, None)
                self.mark_dirty()
                self._queue.task_done()
                return

            self._active_jobs[job.job_id] = job
            job.status = "running"
            self.mark_dirty()
            log.info(f"Starting job {job.job_id}")

            result = None
            try:
                result = await job.func(*job.args, **job.kwargs)
                job.status = {
                    "YIELDED": "yielded",
                    "CANCELLED": "cancelled",
                    "FAILED": "failed",
                }.get(result, "completed")
            except asyncio.CancelledError:
                job.status = "cancelled"
                log.info(f"Job {job.job_id} was cancelled during execution")
            except Exception as e:
                job.status = "failed"
                log.error(f"Job {job.job_id} failed: {e}")
            finally:
                self._active_jobs.pop(job.job_id, None)
                self._queue.task_done()
                # Evict terminal jobs; keep yielded jobs (still resumable).
                if job.status in TERMINAL_STATUSES:
                    if job.status != "completed":
                        await self.release_disk(job.reserved_bytes)
                        job.reserved_bytes = 0
                    self._jobs.pop(job.job_id, None)
                self.mark_dirty()
                if self._deferred_jobs:
                    await self.retry_deferred()
        finally:
            self._semaphore.release()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_user_jobs(self, user_id: int) -> list:
        return [
            job
            for job in self._jobs.values()
            if job.user_id == user_id
            and (job.status in ("pending", "running", "yielded") or job.cleanup_pending)
        ]

    def get_all_jobs(self) -> list:
        return [
            job
            for job in self._jobs.values()
            if job.status in ("pending", "running", "yielded")
        ]

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def queue_position(self, job_id: str) -> int:
        """1-based position among active jobs only (FIFO-safe)."""
        ordered = [
            job
            for job in self._jobs.values()
            if job.status in ("pending", "running", "yielded") or job.cleanup_pending
        ]
        for i, job in enumerate(ordered, 1):
            if job.job_id == job_id:
                return i
        return 0


queue_manager = QueueManager()
