import asyncio

from bot.func.encode import FFmpegProcess


class _FakeProcess:
    pid = 12345
    returncode = None

    class _Stderr:
        async def read(self, _size):
            return b""

    stderr = _Stderr()


def test_ffmpeg_process_start_returns_true_on_success(monkeypatch):
    async def fake_create(*_args, **_kwargs):
        return _FakeProcess()

    monkeypatch.setattr("bot.func.encode.asyncio.create_subprocess_exec", fake_create)
    process = FFmpegProcess(
        "ffmpeg -i input.mp4 output.mp4",
        "input.mp4",
        "output.mp4",
        1.0,
        100,
    )

    async def exercise():
        assert await process.start() is True
        await process._stop_stderr_task()

    asyncio.run(exercise())
