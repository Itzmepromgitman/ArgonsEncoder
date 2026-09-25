"""Per-user listener coordination for Pyrofork callback workflows.

Pyrofork's ``listen``/``stop_listening`` API is scoped to a chat rather than a
single prompt. This adapter guarantees one active input prompt per bot user and
lets a cancel button target only the prompt message that owns the listener.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pyrogram import Client


class ListenerBusy(RuntimeError):
    """Raised when a user already has an input prompt in progress."""


@dataclass
class ListenerSession:
    token: str
    user_id: int
    message_id: int
    action: str
    started_at: float
    client: Any = None


_sessions: Dict[tuple[int, int], ListenerSession] = {}
_locks: Dict[tuple[int, int], asyncio.Lock] = {}
_admission_lock = asyncio.Lock()
_admitted: set[tuple[int, int]] = set()


def _key(client: Any, user_id: int) -> tuple[int, int]:
    return id(client), int(user_id)


def _lock_for(client: Any, user_id: int) -> asyncio.Lock:
    key = _key(client, user_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def listen_once(
    client: Any,
    user_id: int,
    message_id: int,
    action: str,
    timeout: float,
    filters: Any = None,
) -> Any:
    """Listen for one user-scoped message, rejecting concurrent prompts."""
    lock = _lock_for(client, user_id)
    key = _key(client, user_id)
    from database import is_user_tombstoned, privacy_admission_lock

    async with privacy_admission_lock:
        if isinstance(client, Client) and await is_user_tombstoned(user_id):
            raise ListenerBusy("This profile is pending deletion; no input prompt is available.")
        async with _admission_lock:
            if key in _admitted:
                raise ListenerBusy(
                    "Another input prompt is already active. Finish or cancel it first."
                )
            _admitted.add(key)
        try:
            await lock.acquire()
        except BaseException:
            async with _admission_lock:
                _admitted.discard(key)
            raise
        session = ListenerSession(
            token=uuid.uuid4().hex,
            user_id=int(user_id),
            message_id=int(message_id),
            action=action,
            started_at=asyncio.get_running_loop().time(),
            client=client,
        )
        _sessions[key] = session
    try:
        if filters is None:
            return await client.listen(chat_id=user_id, timeout=timeout)
        return await client.listen(
            chat_id=user_id, filters=filters, timeout=timeout
        )
    finally:
        _sessions.pop(key, None)
        lock.release()
        async with _admission_lock:
            _admitted.discard(key)
        if not lock.locked():
            _locks.pop(key, None)


def get_session(client: Any, user_id: int) -> Optional[ListenerSession]:
    return _sessions.get(_key(client, user_id))


async def cancel_user_sessions(user_id: int) -> int:
    cancelled = 0
    for key, session in list(_sessions.items()):
        if session.user_id != int(user_id):
            continue
        if session.client is not None:
            try:
                await session.client.stop_listening(chat_id=session.user_id)
            except Exception:
                # Keep admission occupied when Pyrogram could not stop the
                # listener; a replacement prompt must not race the old one.
                continue
        _sessions.pop(key, None)
        async with _admission_lock:
            _admitted.discard(key)
        cancelled += 1
    return cancelled


async def cancel_session(
    client: Any,
    user_id: int,
    message_id: Optional[int] = None,
) -> bool:
    """Stop only the listener owned by the supplied prompt message."""
    session = get_session(client, user_id)
    if session is None:
        return False
    if message_id is not None and session.message_id != int(message_id):
        return False
    try:
        await client.stop_listening(chat_id=user_id)
    except Exception:
        return False
    return True
