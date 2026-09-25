import asyncio

import pytest

from bot.utils.listener import ListenerBusy, listen_once


class _FakeClient:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = False

    async def listen(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return "ok"

    async def stop_listening(self, **kwargs):
        self.stopped = True
        self.release.set()


def test_listener_allows_only_one_prompt_per_user():
    async def scenario():
        client = _FakeClient()
        first = asyncio.create_task(
            listen_once(client, 7, 11, "test", 2)
        )
        await client.started.wait()
        with pytest.raises(ListenerBusy):
            await listen_once(client, 7, 12, "other", 2)
        client.release.set()
        assert await first == "ok"

    asyncio.run(scenario())
