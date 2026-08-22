# Developed by ARGON telegram: @REACTIVEARGON
import asyncio

from bot.config import MAX_CONCURRENT_DOWNLOADS
from bot.logger import LOGGER

log = LOGGER(__name__)


class DownloadManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DownloadManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._initialized = True
        log.info(
            f"DownloadManager initialized with {MAX_CONCURRENT_DOWNLOADS} concurrent slots"
        )

    async def acquire(self):
        await self._semaphore.acquire()

    def release(self):
        try:
            self._semaphore.release()
        except ValueError:
            log.warning("Download semaphore over-release ignored")


download_manager = DownloadManager()
