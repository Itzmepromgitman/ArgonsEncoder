# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from bot.config import MAX_CONCURRENT_JOBS, MAX_JOBS_PER_USER
from bot.logger import LOGGER
from database import get_variable, set_variable

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
        self._dirty = False
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
            for job in self._jobs.values():
                if job.status in ("pending", "running", "yielded"):
                    job_dict = job.to_dict()
                    if job_dict["status"] in ("running", "yielded"):
                        # Restart as pending; yielded jobs keep their files.
                        job_dict["status"] = "pending"
                    jobs_data.append(job_dict)

            await set_variable("queue_state", jobs_data)
            self._dirty = False
        except Exception as e:
            log.error(f"Failed to save queue: {e}")
            if force:
                raise

    async def restore_queue(self, client):
        try:
            jobs_data = await get_variable("queue_state", [])
            if not jobs_data:
                log.info("No older queue found")
                return

            log.info(f"Restoring {len(jobs_data)} jobs from database...")

            from bot.func.encode import reconstruct_worker

            restored = 0
            for data in jobs_data:
                try:
                    job = Job.from_dict(data)
                except Exception as e:
                    log.warning(f"Skipping malformed queue row: {e}")
                    continue

                if job.task_type != "encode":
                    continue

                try:
                    job.func = reconstruct_worker(job, client)
                except Exception as e:
                    log.warning(f"Could not reconstruct job {job.job_id}: {e}")
                    continue

                # Legacy rows may carry placeholder args; rebuild them.
                if not job.args or job.args == ("PLACEHOLDER",):
                    job.args = (job.job_id,)

                job.status = "pending"
                self._jobs[job.job_id] = job
                await self._queue.put(job)
                restored += 1
                log.info(f"Restored job {job.job_id}")

            # Rewrite the persisted state so stale/legacy rows are pruned.
            await self.save_queue(force=True)

            if restored:
                await self.start()

        except Exception as e:
            log.error(f"Failed to restore queue: {e}")

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def _new_job_id(self) -> str:
        while True:
            job_id = str(uuid.uuid4())[:8]
            if job_id not in self._jobs:
                return job_id

    async def add_job(
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
        **kwargs,
    ) -> Optional[str]:
        # Duplicate guard: same user + same file already queued/running
        if file_name != "Unknown":
            for job in self._jobs.values():
                if (
                    job.user_id == user_id
                    and job.file_name == file_name
                    and job.status in ("pending", "running", "yielded")
                ):
                    log.warning(
                        f"Duplicate job attempt by user {user_id} for file {file_name}"
                    )
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

        def clean_files(j: Job):
            import os

            try:
                if j.input_file and os.path.exists(j.input_file):
                    os.remove(j.input_file)
                    log.info(f"Removed input file for job {j.job_id}")
                if j.output_file and os.path.exists(j.output_file):
                    os.remove(j.output_file)
                    log.info(f"Removed output file for job {j.job_id}")
            except Exception as e:
                log.error(f"Failed to clean files for job {j.job_id}: {e}")

        if job.status == "running":
            job.status = "cancelled"
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
            try:
                from bot.func.encode import active_encodings

                proc = active_encodings.pop(job_id, None)
                if proc is not None:
                    await proc.cancel()
                    if proc.input_file and os.path.exists(proc.input_file):
                        os.remove(proc.input_file)
                    if proc.output_file and os.path.exists(proc.output_file):
                        os.remove(proc.output_file)
            except Exception as e:
                log.error(f"Failed to cancel yielded job {job_id}: {e}")
            self.mark_dirty()
            return True

        if job.status == "pending":
            job.status = "cancelled"
            clean_files(job)
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
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            log.info("QueueManager worker started")

    async def shutdown(self):
        """Persist final state; call before process exit."""
        try:
            await self.save_queue(force=True)
        except Exception as e:
            log.error(f"Shutdown save failed: {e}")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self):
        log.info("Queue worker loop started")
        while True:
            try:
                await self._semaphore.acquire()
                job = await self._queue.get()

                if job.status == "cancelled":
                    log.info(f"Skipping cancelled job {job.job_id}")
                    self._queue.task_done()
                    self._semaphore.release()
                    self._jobs.pop(job.job_id, None)
                    self.mark_dirty()
                    continue

                task = asyncio.create_task(self._process_job(job))
                self._process_tasks.add(task)
                task.add_done_callback(self._process_tasks.discard)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in queue worker: {e}")
                self._semaphore.release()
                await asyncio.sleep(1)

    async def _process_job(self, job: Job):
        try:
            if job.status == "cancelled":
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
                    self._jobs.pop(job.job_id, None)
                self.mark_dirty()
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
            and job.status in ("pending", "running", "yielded")
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
        """1-based position of the job among all active/pending jobs."""
        ordered = list(self._jobs.values())
        for i, job in enumerate(ordered, 1):
            if job.job_id == job_id:
                return i
        return 0


queue_manager = QueueManager()
