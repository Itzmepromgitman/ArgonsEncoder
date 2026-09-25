# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict

from bot.config import MAX_CONCURRENT_UPLOADS
from bot.logger import LOGGER

log = LOGGER(__name__)


@dataclass
class UploadJob:
    job_id: str
    user_id: int
    func: Callable[..., Awaitable[Any]]
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    started: bool = False
    cleanup_pending: bool = False


class UploadManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UploadManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._queue: asyncio.Queue = asyncio.Queue()
        self._active_jobs: Dict[str, UploadJob] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._stopping = False
        self._worker_tasks: list = []
        self._max_concurrent = MAX_CONCURRENT_UPLOADS
        self._initialized = True
        log.info(
            f"UploadManager initialized with {MAX_CONCURRENT_UPLOADS} workers"
        )

    async def start(self):
        if self._stopping:
            return
        self._worker_tasks = [task for task in self._worker_tasks if not task.done()]
        missing = self._max_concurrent - len(self._worker_tasks)
        if missing > 0:
            for worker_id in range(len(self._worker_tasks), self._max_concurrent):
                self._worker_tasks.append(
                    asyncio.create_task(self._worker(worker_id))
                )
            log.info(f"UploadManager running with {self._max_concurrent} workers")

    async def add_upload_job(self, *args, **kwargs):
        from database import privacy_admission_lock

        async with privacy_admission_lock:
            return await self._add_upload_job_unlocked(*args, **kwargs)

    async def _add_upload_job_unlocked(
        self, user_id: int, func: Callable[..., Awaitable[Any]], *args, **kwargs
    ) -> str:
        if self._stopping:
            raise RuntimeError("Upload manager is shutting down")
        try:
            from database import is_user_tombstoned

            if await is_user_tombstoned(user_id):
                raise RuntimeError("User privacy state forbids new delivery")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Could not verify privacy state for delivery") from exc
        job_id = str(uuid.uuid4())[:8]
        job = UploadJob(
            job_id=job_id, user_id=user_id, func=func, args=args, kwargs=kwargs
        )
        self._active_jobs[job_id] = job
        await self._queue.put(job)
        log.info(f"Upload job {job_id} added to queue for user {user_id}")

        await self.start()

        return job_id

    def get_user_jobs(self, user_id: int) -> list[UploadJob]:
        return [job for job in self._active_jobs.values() if job.user_id == user_id]

    def get_all_jobs(self) -> list[UploadJob]:
        return list(self._active_jobs.values())

    async def cancel_user_jobs(
        self, user_id: int, preserve_recovery: bool = False
    ) -> int:
        """Cancel queued/active delivery work for one user and await cleanup."""
        jobs = self.get_user_jobs(user_id)

        try:
            from bot.func.encode import (
                UPLOAD_INFLIGHT,
                UPLOAD_RETRY,
                _persist_upload_retries,
            )
            from bot.func.queue_manager import queue_manager
        except Exception as exc:
            log.error(f"Could not load upload cancellation dependencies: {exc}")
            return 0

        runners = []
        for job in jobs:
            job.status = "cancelled"
            runner = self._running_tasks.get(job.job_id)
            if runner and not runner.done():
                runner.cancel()
                runners.append(runner)
            progress_msg = job.kwargs.get("progress_msg")
            if progress_msg is not None:
                try:
                    from bot.func.pyroutils.progress import flag_cancel

                    flag_cancel(progress_msg)
                except Exception:
                    pass

        if runners:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*runners, return_exceptions=True), timeout=10
                )
            except asyncio.TimeoutError:
                log.warning("Timed out waiting for cancelled upload workers")

        # If the wrapper task never began, its coroutine could not run its
        # finally block. Clean that startup-cancellation race explicitly.
        not_started = [job for job in jobs if not job.started]
        blocked_jobs = set()
        for job in not_started:
            if preserve_recovery:
                job.cleanup_pending = True
                try:
                    from bot.func.encode import _track_failed_cleanup
                    _track_failed_cleanup(
                        f"forget_{job.job_id}",
                        user_id,
                        job.kwargs.get("output_files", []) or [],
                    )
                except Exception as track_error:
                    log.error(f"Could not track preserved upload: {track_error}")
                continue
            deletion_ok = True
            for item in job.kwargs.get("output_files", []) or []:
                path = item.get("file_path")
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        deletion_ok = False
            if not deletion_ok:
                blocked_jobs.add(job.job_id)
                # Keep the delivery blocked; leave tracking for a later
                # operator retry rather than re-enabling the upload.
                job.status = "cancelled"
                job.cleanup_pending = True
                try:
                    from bot.func.encode import _track_failed_cleanup
                    _track_failed_cleanup(
                        f"cancelled_{job.job_id}",
                        user_id,
                        job.kwargs.get("output_files", []) or [],
                    )
                except Exception as track_error:
                    log.error(f"Could not track cancelled upload: {track_error}")
                continue
            source_id = job.kwargs.get("source_job_id", job.job_id)
            output_paths = {
                item.get("file_path")
                for item in job.kwargs.get("output_files", []) or []
            }
            matched_context = False
            for store in (UPLOAD_RETRY, UPLOAD_INFLIGHT):
                for key, context in list(store.items()):
                    if context.get("user_id") != user_id:
                        continue
                    context_paths = {
                        item.get("file_path")
                        for item in context.get("output_files", []) or []
                    }
                    if context.get("job_id") != source_id and not output_paths.intersection(context_paths):
                        continue
                    matched_context = True
                    reserved = int(context.get("reserved_bytes", 0) or 0)
                    if reserved:
                        await queue_manager.release_disk(reserved)
                    store.pop(key, None)
            reserved = int(job.kwargs.get("reserved_bytes", 0) or 0)
            if not matched_context and reserved:
                await queue_manager.release_disk(reserved)
            job.kwargs["reserved_bytes"] = 0

        # Remove recovery contexts that are not backed by a still-running
        # transfer. _upload_video owns the active context and releases it in
        # its finally block.
        active_job_ids = {
            job.kwargs.get("source_job_id", job.job_id)
            for job in jobs
            if self._running_tasks.get(job.job_id)
            and not self._running_tasks[job.job_id].done()
        }
        if not preserve_recovery:
            for store in (UPLOAD_RETRY, UPLOAD_INFLIGHT):
                for key, context in list(store.items()):
                    if context.get("user_id") != user_id:
                        continue
                    if context.get("job_id") in active_job_ids:
                        continue
                    if any(
                        job.kwargs.get("source_job_id", job.job_id) == context.get("job_id")
                        and job.job_id in blocked_jobs
                        for job in jobs
                    ):
                        continue
                    deletion_ok = True
                    for item in context.get("output_files", []) or []:
                        path = item.get("file_path")
                        if path and os.path.isfile(path):
                            try:
                                os.remove(path)
                            except OSError:
                                deletion_ok = False
                    if not deletion_ok:
                        log.error("Could not remove recovery output %s; retaining context", key)
                        continue
                    reserved = int(context.get("reserved_bytes", 0) or 0)
                    if reserved:
                        await queue_manager.release_disk(reserved)
                    store.pop(key, None)
        for job in jobs:
            runner = self._running_tasks.get(job.job_id)
            if job.job_id in blocked_jobs:
                continue
            if preserve_recovery and job.cleanup_pending and (not runner or runner.done()):
                self._active_jobs.pop(job.job_id, None)
            if (not preserve_recovery) and (not runner or runner.done()):
                self._active_jobs.pop(job.job_id, None)
        if not _persist_upload_retries():
            log.warning("Upload cancellation manifest could not be persisted")
        return len(jobs)

    async def shutdown(self) -> bool:
        """Stop accepting new deliveries and let active transfers finish briefly."""
        from database import privacy_admission_lock

        async with privacy_admission_lock:
            if self._stopping:
                return True
            self._stopping = True
            durable = True
            try:
                from bot.func.encode import _persist_upload_retries

                if not _persist_upload_retries():
                    self._stopping = False
                    return False
            except Exception as exc:
                log.error(f"Could not preflight upload recovery persistence: {exc}")
                self._stopping = False
                return False
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                from bot.func.encode import (
                    UPLOAD_RECOVERY_HEALTHY,
                    register_pending_upload_recovery,
                )

                persisted = register_pending_upload_recovery(job)
                if not UPLOAD_RECOVERY_HEALTHY:
                    persisted = False
            except Exception as exc:
                persisted = False
                log.warning(f"Could not persist pending upload {job.job_id}: {exc}")
            if not persisted:
                durable = False
                # Leave the job in the in-memory queue so an aborted restart
                # can resume it instead of silently losing the output.
                self._queue.task_done()
                await self._queue.put(job)
                break
            job.status = "cancelled"
            self._active_jobs.pop(job.job_id, None)
            self._queue.task_done()
        running = [task for task in self._running_tasks.values() if not task.done()]
        if running:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*running, return_exceptions=True), timeout=5
                )
            except asyncio.TimeoutError:
                durable = False
                log.warning("Upload transfers still active during shutdown")
        try:
            from bot.func.encode import UPLOAD_RECOVERY_HEALTHY

            if not UPLOAD_RECOVERY_HEALTHY:
                durable = False
        except Exception:
            pass
        if durable and not any(
            not task.done() for task in self._running_tasks.values()
        ):
            workers = [task for task in self._worker_tasks if not task.done()]
            for task in workers:
                task.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            self._worker_tasks = []
        return durable

    def resume(self):
        self._stopping = False

    async def _worker(self, worker_id: int):
        log.info(f"Upload worker {worker_id} started")
        while True:
            job = None
            try:
                job = await self._queue.get()
                if job.status == "cancelled":
                    if not job.cleanup_pending:
                        self._active_jobs.pop(job.job_id, None)
                    self._queue.task_done()
                    continue
                job.status = "uploading"
                log.info(f"Worker {worker_id} starting upload job {job.job_id}")

                try:
                    async def run_upload():
                        job.started = True
                        return await job.func(*job.args, **job.kwargs)

                    runner = asyncio.create_task(run_upload())
                    self._running_tasks[job.job_id] = runner
                    try:
                        await runner
                        if job.status != "cancelled":
                            job.status = "completed"
                    except asyncio.CancelledError:
                        job.status = "cancelled"
                        if self._stopping:
                            raise
                    except Exception as e:
                        job.status = "failed"
                        log.error(f"Upload job {job.job_id} failed: {e}")
                        try:
                            from bot.func.queue_manager import queue_manager

                            deletion_ok = True
                            for item in job.kwargs.get("output_files", []) or []:
                                path = item.get("file_path")
                                if path and os.path.isfile(path):
                                    try:
                                        os.remove(path)
                                    except OSError:
                                        deletion_ok = False
                            if deletion_ok:
                                reserved = int(job.kwargs.get("reserved_bytes", 0) or 0)
                                if reserved:
                                    await queue_manager.release_disk(reserved)
                                    job.kwargs["reserved_bytes"] = 0
                            else:
                                job.cleanup_pending = True
                                job.status = "cancelled"
                                try:
                                    from bot.func.encode import _track_failed_cleanup
                                    _track_failed_cleanup(
                                        f"failed_{job.job_id}",
                                        job.user_id,
                                        job.kwargs.get("output_files", []) or [],
                                    )
                                except Exception as track_error:
                                    log.error(f"Could not track failed upload: {track_error}")
                        except Exception as cleanup_error:
                            log.error(
                                f"Could not clean failed upload {job.job_id}: {cleanup_error}"
                            )
                    finally:
                        self._running_tasks.pop(job.job_id, None)
                except asyncio.CancelledError:
                    job.status = "cancelled"
                    raise
                finally:
                    if job.job_id in self._active_jobs and not job.cleanup_pending:
                        del self._active_jobs[job.job_id]
                    self._queue.task_done()

            except asyncio.CancelledError:
                # Preserve worker liveness semantics for shutdown.
                raise
            except Exception as e:
                log.error(f"Error in upload worker {worker_id}: {e}")
                await asyncio.sleep(1)


upload_manager = UploadManager()
