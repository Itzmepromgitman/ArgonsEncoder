# Developed by ARGON telegram: @REACTIVEARGON
import asyncio
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
        self._worker_tasks: list = []
        self._max_concurrent = MAX_CONCURRENT_UPLOADS
        self._initialized = True
        log.info(
            f"UploadManager initialized with {MAX_CONCURRENT_UPLOADS} workers"
        )

    async def start(self):
        if not self._worker_tasks:
            for i in range(self._max_concurrent):
                task = asyncio.create_task(self._worker(i))
                self._worker_tasks.append(task)
            log.info(f"UploadManager started with {self._max_concurrent} workers")

    async def add_upload_job(
        self, user_id: int, func: Callable[..., Awaitable[Any]], *args, **kwargs
    ) -> str:
        job_id = str(uuid.uuid4())[:8]
        job = UploadJob(
            job_id=job_id, user_id=user_id, func=func, args=args, kwargs=kwargs
        )
        self._active_jobs[job_id] = job
        await self._queue.put(job)
        log.info(f"Upload job {job_id} added to queue for user {user_id}")

        if not self._worker_tasks or all(t.done() for t in self._worker_tasks):
            self._worker_tasks.clear()
            await self.start()

        return job_id

    async def _worker(self, worker_id: int):
        log.info(f"Upload worker {worker_id} started")
        while True:
            job = None
            try:
                job = await self._queue.get()
                job.status = "uploading"
                log.info(f"Worker {worker_id} starting upload job {job.job_id}")

                try:
                    await job.func(*job.args, **job.kwargs)
                    job.status = "completed"
                except asyncio.CancelledError:
                    job.status = "cancelled"
                    raise
                except Exception as e:
                    job.status = "failed"
                    log.error(f"Upload job {job.job_id} failed: {e}")
                finally:
                    if job.job_id in self._active_jobs:
                        del self._active_jobs[job.job_id]
                    self._queue.task_done()

            except asyncio.CancelledError:
                # Preserve worker liveness semantics for shutdown.
                raise
            except Exception as e:
                log.error(f"Error in upload worker {worker_id}: {e}")
                await asyncio.sleep(1)


upload_manager = UploadManager()
